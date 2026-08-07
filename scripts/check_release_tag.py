from __future__ import annotations

import argparse
from pathlib import Path
import tomllib


def read_project_version(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as handle:
        data = tomllib.load(handle)
    return str(data["project"]["version"])


def validate_release_tag(tag: str, version: str) -> None:
    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"release tag {tag!r} must exactly match {expected!r}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate that a release tag matches pyproject.toml."
    )
    parser.add_argument("--tag", required=True, help="Git tag, for example v0.2.0.")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml.",
    )
    args = parser.parse_args()

    version = read_project_version(args.pyproject)
    try:
        validate_release_tag(args.tag, version)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"release tag {args.tag} matches project version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
