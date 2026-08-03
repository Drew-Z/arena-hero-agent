from pathlib import Path
import tempfile
import unittest

from scripts.check_release_tag import read_project_version, validate_release_tag


class ReleaseTagTests(unittest.TestCase):
    def test_matching_version_is_accepted(self) -> None:
        validate_release_tag("v1.2.3", "1.2.3")

    def test_nonmatching_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must exactly match"):
            validate_release_tag("v1.2.4", "1.2.3")

    def test_project_version_is_read_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pyproject.toml"
            path.write_text('[project]\nname = "example"\nversion = "2.0.1"\n', encoding="utf-8")
            self.assertEqual(read_project_version(path), "2.0.1")


if __name__ == "__main__":
    unittest.main()
