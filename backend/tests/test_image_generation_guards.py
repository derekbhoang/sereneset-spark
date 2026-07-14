import unittest

from fastapi import HTTPException, status

from app.api.v1.routes.assets import ensure_image_generation_format
from app.models.asset import AssetFormat


class ImageGenerationFormatGuardTests(unittest.TestCase):
    def test_accepts_image_format(self) -> None:
        ensure_image_generation_format(AssetFormat.image)

    def test_rejects_non_image_formats(self) -> None:
        for asset_format in (AssetFormat.copy, AssetFormat.video_concept):
            with self.subTest(asset_format=asset_format):
                with self.assertRaises(HTTPException) as raised:
                    ensure_image_generation_format(asset_format)

                self.assertEqual(
                    raised.exception.status_code,
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                )
                self.assertEqual(
                    raised.exception.detail,
                    "Image generation routes only support assets with format 'image'",
                )


if __name__ == "__main__":
    unittest.main()
