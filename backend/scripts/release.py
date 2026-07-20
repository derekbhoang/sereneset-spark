from __future__ import annotations

import subprocess
import sys


def run_release() -> None:
    commands = (
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        [sys.executable, "scripts/seed.py", "--showcase-only"],
    )

    for command in commands:
        print(f"Running release step: {' '.join(command)}", flush=True)
        subprocess.run(command, check=True)


if __name__ == "__main__":
    run_release()
