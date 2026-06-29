import argparse
from datetime import UTC, datetime

from botocore.exceptions import BotoCoreError, ClientError

from app.services.storage import StorageConfigurationError, get_storage_service


DEFAULT_KEY = "health/storage-check.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a small JSON object to Backblaze B2 and verify it exists."
    )
    parser.add_argument(
        "--key",
        default=DEFAULT_KEY,
        help=f"B2 object key to write. Defaults to {DEFAULT_KEY}.",
    )
    parser.add_argument(
        "--print-url",
        action="store_true",
        help="Print a temporary presigned download URL.",
    )
    parser.add_argument(
        "--url-expires",
        default=600,
        type=int,
        help="Presigned URL lifetime in seconds. Defaults to 600.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    checked_at = datetime.now(UTC).isoformat()
    storage = get_storage_service()

    payload = {
        "ok": True,
        "checked_at": checked_at,
        "purpose": "Backblaze B2 storage smoke test",
    }

    try:
        stored_object = storage.upload_json(
            key=args.key,
            data=payload,
            metadata={
                "purpose": "storage-smoke-test",
                "checked_at": checked_at,
            },
        )
        head = storage.client.head_object(
            Bucket=stored_object.bucket,
            Key=stored_object.key,
        )
    except StorageConfigurationError as exc:
        print(f"B2 storage is not configured: {exc}")
        return 2
    except (BotoCoreError, ClientError) as exc:
        print(f"B2 smoke test failed: {exc}")
        return 1

    print("B2 smoke test passed")
    print(f"bucket={stored_object.bucket}")
    print(f"key={stored_object.key}")
    print(f"content_type={stored_object.content_type}")
    print(f"uploaded_size={stored_object.size}")
    print(f"remote_size={head.get('ContentLength')}")

    if args.print_url:
        url = storage.generate_presigned_download_url(
            key=stored_object.key,
            expires_seconds=args.url_expires,
        )
        print(f"download_url_expires_seconds={args.url_expires}")
        print(f"download_url={url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
