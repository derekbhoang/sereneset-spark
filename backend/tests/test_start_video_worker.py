import os
import unittest
from unittest.mock import MagicMock, patch

from scripts.start_video_worker import wait_for_api


class StartVideoWorkerTests(unittest.TestCase):
    def test_skips_wait_without_api_host(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            wait_for_api()

    def test_waits_for_api_liveness(self) -> None:
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response

        with (
            patch.dict(
                os.environ,
                {"API_HOSTPORT": "sereneset-spark-api:8000"},
                clear=True,
            ),
            patch(
                "scripts.start_video_worker.urllib.request.urlopen",
                return_value=response,
            ) as urlopen,
        ):
            wait_for_api()

        urlopen.assert_called_once_with(
            "http://sereneset-spark-api:8000/api/v1/health",
            timeout=5,
        )


if __name__ == "__main__":
    unittest.main()
