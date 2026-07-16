import logging
import os
import time
import urllib.error
import urllib.request

from app.workers.video_generation import main as run_video_worker


logger = logging.getLogger(__name__)


def wait_for_api() -> None:
    api_hostport = os.getenv("API_HOSTPORT", "").strip()
    if not api_hostport:
        return

    health_url = f"http://{api_hostport}/api/v1/health"
    timeout_seconds = int(os.getenv("API_STARTUP_TIMEOUT_SECONDS", "900"))
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=5) as response:
                if response.status == 200:
                    logger.info("API is live; starting the video worker")
                    return
        except (OSError, urllib.error.URLError):
            pass

        time.sleep(2)

    raise TimeoutError(f"API did not become live within {timeout_seconds} seconds")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    wait_for_api()
    run_video_worker()


if __name__ == "__main__":
    main()
