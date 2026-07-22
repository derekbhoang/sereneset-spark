import unittest
from pathlib import Path


REQUIREMENTS_PATH = Path(__file__).resolve().parents[1] / "requirements.txt"
GENBLAZE_PINS = (
    "genblaze-core==0.3.4",
    "genblaze-gmicloud==0.3.2",
    "genblaze-s3==0.3.4",
)


class GenblazeDependencyPinTests(unittest.TestCase):
    def test_known_working_genblaze_set_is_exactly_pinned(self) -> None:
        requirements = [
            line.strip().lower()
            for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

        self.assertEqual(
            [item for item in requirements if item.startswith("genblaze")],
            list(GENBLAZE_PINS),
        )
