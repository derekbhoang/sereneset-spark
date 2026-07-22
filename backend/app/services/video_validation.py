from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO


MAX_MP4_BOX_COUNT = 10_000
MAX_FTYP_PAYLOAD_SIZE_BYTES = 4096


class VideoContentValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Mp4ValidationResult:
    major_brand: str
    compatible_brands: tuple[str, ...]
    video_track_count: int
    media_data_box_count: int
    container: str = "mp4"

    def as_metadata(self) -> dict[str, object]:
        return {
            "container": self.container,
            "major_brand": self.major_brand,
            "compatible_brands": list(self.compatible_brands),
            "video_track_count": self.video_track_count,
            "media_data_box_count": self.media_data_box_count,
        }


@dataclass(frozen=True)
class _Mp4Box:
    box_type: bytes
    start: int
    end: int
    header_size: int

    @property
    def payload_start(self) -> int:
        return self.start + self.header_size

    @property
    def payload_size(self) -> int:
        return self.end - self.payload_start


@dataclass
class _BoxCounter:
    count: int = 0

    def increment(self) -> None:
        self.count += 1
        if self.count > MAX_MP4_BOX_COUNT:
            raise VideoContentValidationError("MP4 contains too many container boxes")


def _seek(fileobj: BinaryIO, offset: int) -> None:
    try:
        fileobj.seek(offset)
    except (AttributeError, OSError, ValueError) as exc:
        raise VideoContentValidationError(
            "MP4 could not be inspected as a seekable file"
        ) from exc


def _read_exact(fileobj: BinaryIO, size: int) -> bytes:
    try:
        body = fileobj.read(size)
    except (OSError, ValueError) as exc:
        raise VideoContentValidationError("MP4 could not be read") from exc

    if not isinstance(body, bytes) or len(body) != size:
        raise VideoContentValidationError("MP4 is truncated")

    return body


def _iter_boxes(
    fileobj: BinaryIO,
    *,
    start: int,
    end: int,
    counter: _BoxCounter,
) -> Iterator[_Mp4Box]:
    offset = start
    while offset < end:
        if end - offset < 8:
            raise VideoContentValidationError("MP4 contains an incomplete box header")

        counter.increment()
        _seek(fileobj, offset)
        header = _read_exact(fileobj, 8)
        size_32 = struct.unpack(">I", header[:4])[0]
        box_type = header[4:8]
        header_size = 8

        if size_32 == 1:
            extended_size = struct.unpack(">Q", _read_exact(fileobj, 8))[0]
            header_size = 16
            box_size = extended_size
        elif size_32 == 0:
            box_size = end - offset
        else:
            box_size = size_32

        if box_size < header_size:
            raise VideoContentValidationError("MP4 contains a box with an invalid size")

        box_end = offset + box_size
        if box_end > end:
            raise VideoContentValidationError(
                "MP4 contains a box that extends beyond the file"
            )

        yield _Mp4Box(
            box_type=box_type,
            start=offset,
            end=box_end,
            header_size=header_size,
        )
        offset = box_end


def _brand_name(value: bytes) -> str:
    if len(value) != 4 or any(byte < 0x20 or byte > 0x7E for byte in value):
        raise VideoContentValidationError("MP4 file-type box contains an invalid brand")

    return value.decode("ascii")


def _parse_file_type_box(
    fileobj: BinaryIO,
    box: _Mp4Box,
) -> tuple[str, tuple[str, ...]]:
    if (
        box.payload_size < 8
        or box.payload_size > MAX_FTYP_PAYLOAD_SIZE_BYTES
        or (box.payload_size - 8) % 4 != 0
    ):
        raise VideoContentValidationError("MP4 file-type box has an invalid size")

    _seek(fileobj, box.payload_start)
    payload = _read_exact(fileobj, box.payload_size)
    major_brand_bytes = payload[:4]
    compatible_brand_bytes = [
        payload[offset : offset + 4] for offset in range(8, len(payload), 4)
    ]
    all_brands = [major_brand_bytes, *compatible_brand_bytes]
    if all(brand == b"qt  " for brand in all_brands):
        raise VideoContentValidationError(
            "QuickTime content is not supported; upload an MP4 file"
        )

    return (
        _brand_name(major_brand_bytes),
        tuple(_brand_name(brand) for brand in compatible_brand_bytes),
    )


def _track_has_video_handler(
    fileobj: BinaryIO,
    *,
    track: _Mp4Box,
    counter: _BoxCounter,
) -> bool:
    for track_child in _iter_boxes(
        fileobj,
        start=track.payload_start,
        end=track.end,
        counter=counter,
    ):
        if track_child.box_type != b"mdia":
            continue

        for media_child in _iter_boxes(
            fileobj,
            start=track_child.payload_start,
            end=track_child.end,
            counter=counter,
        ):
            if media_child.box_type != b"hdlr":
                continue
            if media_child.payload_size < 12:
                raise VideoContentValidationError(
                    "MP4 contains an invalid media handler"
                )

            _seek(fileobj, media_child.payload_start)
            handler_header = _read_exact(fileobj, 12)
            return handler_header[8:12] == b"vide"

    return False


def _count_video_tracks(
    fileobj: BinaryIO,
    *,
    movie: _Mp4Box,
    counter: _BoxCounter,
) -> int:
    video_track_count = 0
    for movie_child in _iter_boxes(
        fileobj,
        start=movie.payload_start,
        end=movie.end,
        counter=counter,
    ):
        if movie_child.box_type == b"trak" and _track_has_video_handler(
            fileobj,
            track=movie_child,
            counter=counter,
        ):
            video_track_count += 1

    return video_track_count


def validate_mp4_contents(
    *,
    fileobj: BinaryIO,
    size_bytes: int,
) -> Mp4ValidationResult:
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes < 1
    ):
        raise VideoContentValidationError("MP4 must not be empty")

    _seek(fileobj, 0)
    try:
        counter = _BoxCounter()
        major_brand: str | None = None
        compatible_brands: tuple[str, ...] = ()
        movie_box_count = 0
        media_data_box_count = 0
        video_track_count = 0

        for index, box in enumerate(
            _iter_boxes(
                fileobj,
                start=0,
                end=size_bytes,
                counter=counter,
            )
        ):
            if index == 0 and box.box_type != b"ftyp":
                raise VideoContentValidationError("MP4 must begin with a file-type box")

            if box.box_type == b"ftyp":
                if major_brand is not None:
                    raise VideoContentValidationError(
                        "MP4 contains multiple file-type boxes"
                    )
                major_brand, compatible_brands = _parse_file_type_box(
                    fileobj,
                    box,
                )
            elif box.box_type == b"moov":
                movie_box_count += 1
                video_track_count += _count_video_tracks(
                    fileobj,
                    movie=box,
                    counter=counter,
                )
            elif box.box_type == b"mdat" and box.payload_size > 0:
                media_data_box_count += 1

        if major_brand is None:
            raise VideoContentValidationError("MP4 does not contain a file-type box")
        if movie_box_count == 0:
            raise VideoContentValidationError("MP4 does not contain movie metadata")
        if video_track_count == 0:
            raise VideoContentValidationError("MP4 does not contain a video track")
        if media_data_box_count == 0:
            raise VideoContentValidationError("MP4 does not contain media data")

        result = Mp4ValidationResult(
            major_brand=major_brand,
            compatible_brands=compatible_brands,
            video_track_count=video_track_count,
            media_data_box_count=media_data_box_count,
        )
    except Exception:
        try:
            fileobj.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
        raise

    _seek(fileobj, 0)
    return result
