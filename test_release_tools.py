from pathlib import Path
import tempfile
import tomllib
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

    def test_build_dependency_declarations_stay_in_sync(self) -> None:
        root = Path(__file__).resolve().parent
        with (root / "pyproject.toml").open("rb") as project_file:
            project_requires = set(tomllib.load(project_file)["build-system"]["requires"])

        input_requires = self._read_exact_pins(root / "requirements-build.in")
        lock_requires = self._read_exact_pins(root / "requirements-build.lock")

        self.assertEqual(project_requires, input_requires)
        self.assertLessEqual(input_requires, lock_requires)

    @staticmethod
    def _read_exact_pins(path: Path) -> set[str]:
        pins = set()
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip().removesuffix("\\").strip()
            if "==" in line and not line.startswith("--"):
                pins.add(line)
        return pins


if __name__ == "__main__":
    unittest.main()
