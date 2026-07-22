import hashlib
import unittest
from io import BytesIO
from unittest.mock import MagicMock, call, patch

from botocore.config import Config

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
    def test_uses_an_explicit_client_configuration(self) -> None:
        settings = Settings(
            _env_file=None,
            B2_BUCKET_NAME="test-bucket",
            B2_APPLICATION_KEY_ID="test-key-id",
            B2_APPLICATION_KEY="test-application-key",
        )
        client_config = Config(
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=5,
        )
        expected_client = MagicMock()
        storage = B2StorageService(
            settings,
            client_config=client_config,
        )

        with patch(
            "app.services.storage.boto3.client",
            return_value=expected_client,
        ) as create_client:
            client = storage.client

        self.assertIs(client, expected_client)
        self.assertIs(create_client.call_args.kwargs["config"], client_config)

    def test_checks_bucket_access_without_reading_an_object(self) -> None:
        storage, client = make_storage()

        storage.check_bucket_access()

        client.head_bucket.assert_called_once_with(Bucket="test-bucket")
        client.get_object.assert_not_called()

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


class B2StreamedUploadTests(unittest.TestCase):
    def test_inspects_and_uploads_without_reading_the_whole_file_at_once(
        self,
    ) -> None:
        storage, client = make_storage()

        class TrackingFile(BytesIO):
            def __init__(self, body: bytes) -> None:
                super().__init__(body)
                self.read_sizes: list[int] = []

            def read(self, size: int = -1) -> bytes:
                self.read_sizes.append(size)
                return super().read(size)

        body = b"video-data" * 300_000
        fileobj = TrackingFile(body)

        stored_object = storage.upload_fileobj(
            key="campaigns/video/input.mp4",
            fileobj=fileobj,
            content_type="video/mp4",
            max_size_bytes=len(body),
            metadata={"sha256": hashlib.sha256(body).hexdigest()},
        )

        self.assertEqual(stored_object.size, len(body))
        self.assertEqual(stored_object.content_type, "video/mp4")
        self.assertEqual(fileobj.tell(), 0)
        self.assertTrue(fileobj.read_sizes)
        self.assertNotIn(-1, fileobj.read_sizes)
        self.assertLessEqual(max(fileobj.read_sizes), 1024 * 1024)
        upload_call = client.upload_fileobj.call_args
        self.assertIs(upload_call.kwargs["Fileobj"], fileobj)
        self.assertEqual(upload_call.kwargs["Bucket"], "test-bucket")
        self.assertEqual(
            upload_call.kwargs["Key"],
            "campaigns/video/input.mp4",
        )
        self.assertEqual(
            upload_call.kwargs["ExtraArgs"]["ContentType"],
            "video/mp4",
        )
        self.assertFalse(upload_call.kwargs["Config"].use_threads)

    def test_returns_a_sha256_and_rewinds_after_inspection(self) -> None:
        storage, _client = make_storage()
        body = b"bounded-video-input"
        fileobj = BytesIO(body)

        inspection = storage.inspect_fileobj(
            fileobj=fileobj,
            max_size_bytes=len(body),
            chunk_size_bytes=4,
        )

        self.assertEqual(inspection.size, len(body))
        self.assertEqual(
            inspection.sha256,
            hashlib.sha256(body).hexdigest(),
        )
        self.assertEqual(fileobj.tell(), 0)

    def test_rejects_oversized_upload_before_contacting_b2(self) -> None:
        storage, client = make_storage()
        fileobj = BytesIO(b"12345")

        with self.assertRaisesRegex(
            StorageObjectTooLargeError,
            "configured size limit",
        ):
            storage.upload_fileobj(
                key="campaigns/video/input.mp4",
                fileobj=fileobj,
                content_type="video/mp4",
                max_size_bytes=4,
            )

        self.assertEqual(fileobj.tell(), 0)
        client.upload_fileobj.assert_not_called()


if __name__ == "__main__":
    unittest.main()
