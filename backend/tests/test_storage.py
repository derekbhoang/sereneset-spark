import unittest
from unittest.mock import MagicMock, call

from app.core.config import Settings
from app.services.storage import (
    B2StorageService,
    StorageObjectTooLargeError,
)


def make_storage() -> tuple[B2StorageService, MagicMock]:
    settings = Settings(
        _env_file=None,
        B2_BUCKET_NAME="test-bucket",
        B2_APPLICATION_KEY_ID="test-key-id",
        B2_APPLICATION_KEY="test-application-key",
    )
    storage = B2StorageService(settings)
    client = MagicMock()
    storage._client = client
    return storage, client


class B2ServerSideCopyTests(unittest.TestCase):
    def test_copies_large_object_without_downloading_body(self) -> None:
        storage, client = make_storage()
        large_size = 500 * 1024 * 1024
        client.head_object.return_value = {
            "ContentLength": large_size,
            "ContentType": "video/mp4",
            "ETag": '"source-etag"',
        }
        client.copy_object.return_value = {
            "CopyObjectResult": {"ETag": '"destination-etag"'}
        }

        stored_object = storage.copy_object(
            source_key="genblaze/run/video.mp4",
            destination_key="campaigns/one/assets/two/versions/v1/video.mp4",
            content_type="video/mp4",
            metadata={"version_number": 1, "source_sha256": None},
            max_size_bytes=large_size,
        )

        self.assertEqual(stored_object.size, large_size)
        self.assertEqual(stored_object.etag, '"destination-etag"')
        client.head_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="genblaze/run/video.mp4",
        )
        client.copy_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="campaigns/one/assets/two/versions/v1/video.mp4",
            CopySource={
                "Bucket": "test-bucket",
                "Key": "genblaze/run/video.mp4",
            },
            ContentType="video/mp4",
            Metadata={"version_number": "1"},
            MetadataDirective="REPLACE",
        )
        client.get_object.assert_not_called()

    def test_rejects_oversized_source_before_copy(self) -> None:
        storage, client = make_storage()
        client.head_object.return_value = {
            "ContentLength": 501 * 1024 * 1024,
            "ContentType": "video/mp4",
        }

        with self.assertRaisesRegex(
            StorageObjectTooLargeError,
            "configured size limit",
        ):
            storage.copy_object(
                source_key="genblaze/run/video.mp4",
                destination_key="campaigns/video.mp4",
                max_size_bytes=500 * 1024 * 1024,
            )

        client.copy_object.assert_not_called()
        client.get_object.assert_not_called()


class B2ChunkedDownloadTests(unittest.TestCase):
    def test_streams_bounded_chunks_and_closes_the_response(self) -> None:
        storage, client = make_storage()
        body = MagicMock()
        body.read.side_effect = [b"ab", b"cd", b""]
        client.get_object.return_value = {
            "Body": body,
            "ContentLength": 4,
        }

        chunks = list(
            storage.iter_download_chunks(
                key="campaigns/video.mp4",
                chunk_size_bytes=2,
                max_size_bytes=4,
            )
        )

        self.assertEqual(chunks, [b"ab", b"cd"])
        self.assertEqual(body.read.call_args_list, [call(2), call(2), call(2)])
        body.close.assert_called_once_with()

    def test_rejects_oversized_object_before_reading_the_body(self) -> None:
        storage, client = make_storage()
        body = MagicMock()
        client.get_object.return_value = {
            "Body": body,
            "ContentLength": 5,
        }

        with self.assertRaisesRegex(
            StorageObjectTooLargeError,
            "configured size limit",
        ):
            list(
                storage.iter_download_chunks(
                    key="campaigns/video.mp4",
                    chunk_size_bytes=2,
                    max_size_bytes=4,
                )
            )

        body.read.assert_not_called()
        body.close.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
