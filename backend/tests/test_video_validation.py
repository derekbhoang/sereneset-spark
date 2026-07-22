import unittest
from io import BytesIO
from struct import pack

from app.services.video_validation import (
    MAX_MP4_BOX_COUNT,
    VideoContentValidationError,
    validate_mp4_contents,
)


def mp4_box(box_type: bytes, payload: bytes) -> bytes:
    return pack(">I4s", len(payload) + 8, box_type) + payload


def file_type_box(
    *,
    major_brand: bytes = b"isom",
    compatible_brands: tuple[bytes, ...] = (b"isom", b"mp42"),
) -> bytes:
    return mp4_box(
        b"ftyp",
        major_brand + bytes(4) + b"".join(compatible_brands),
    )


def movie_box(*, handler_type: bytes = b"vide") -> bytes:
    handler = mp4_box(
        b"hdlr",
        bytes(8) + handler_type,
    )
    media = mp4_box(b"mdia", handler)
    return mp4_box(b"moov", mp4_box(b"trak", media))


def valid_mp4(*, handler_type: bytes = b"vide") -> bytes:
    return (
        file_type_box()
        + movie_box(handler_type=handler_type)
        + mp4_box(b"mdat", b"encoded-video-data")
    )


class Mp4ContentValidationTests(unittest.TestCase):
    def test_accepts_structural_mp4_with_video_track_and_media_data(self) -> None:
        body = valid_mp4()
        fileobj = BytesIO(body)
        fileobj.seek(len(body))

        result = validate_mp4_contents(
            fileobj=fileobj,
            size_bytes=len(body),
        )

        self.assertEqual(result.container, "mp4")
        self.assertEqual(result.major_brand, "isom")
        self.assertEqual(result.compatible_brands, ("isom", "mp42"))
        self.assertEqual(result.video_track_count, 1)
        self.assertEqual(result.media_data_box_count, 1)
        self.assertEqual(fileobj.tell(), 0)

    def test_rejects_non_mp4_bytes_and_rewinds(self) -> None:
        body = b"not-an-mp4-file"
        fileobj = BytesIO(body)

        with self.assertRaisesRegex(
            VideoContentValidationError,
            "beyond the file",
        ):
            validate_mp4_contents(
                fileobj=fileobj,
                size_bytes=len(body),
            )

        self.assertEqual(fileobj.tell(), 0)

    def test_rejects_mp4_without_video_track(self) -> None:
        body = valid_mp4(handler_type=b"soun")

        with self.assertRaisesRegex(
            VideoContentValidationError,
            "does not contain a video track",
        ):
            validate_mp4_contents(
                fileobj=BytesIO(body),
                size_bytes=len(body),
            )

    def test_rejects_mp4_without_media_data(self) -> None:
        body = file_type_box() + movie_box()

        with self.assertRaisesRegex(
            VideoContentValidationError,
            "does not contain media data",
        ):
            validate_mp4_contents(
                fileobj=BytesIO(body),
                size_bytes=len(body),
            )

    def test_rejects_quicktime_container_labeled_as_mp4(self) -> None:
        body = (
            file_type_box(
                major_brand=b"qt  ",
                compatible_brands=(b"qt  ",),
            )
            + movie_box()
            + mp4_box(b"mdat", b"video-data")
        )

        with self.assertRaisesRegex(
            VideoContentValidationError,
            "QuickTime content is not supported",
        ):
            validate_mp4_contents(
                fileobj=BytesIO(body),
                size_bytes=len(body),
            )

    def test_limits_box_traversal(self) -> None:
        body = file_type_box() + (mp4_box(b"free", b"") * MAX_MP4_BOX_COUNT)

        with self.assertRaisesRegex(
            VideoContentValidationError,
            "too many container boxes",
        ):
            validate_mp4_contents(
                fileobj=BytesIO(body),
                size_bytes=len(body),
            )


if __name__ == "__main__":
    unittest.main()
