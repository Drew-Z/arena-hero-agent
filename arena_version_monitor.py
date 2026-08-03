from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Mapping

import httpx


SOURCE_VERSION_URL = "https://doc.arenahero.io/reference/source-and-version"
PYPI_URL = "https://pypi.org/pypi/arena-hero/json"
DEFAULT_MARKER_PATH = Path(
    "/var/lib/arena-hero-version/compatibility-hold.json"
)
DEFAULT_REPORT_PATH = Path("/var/lib/arena-hero-version/latest.json")

REVIEWED_API_VERSION = "v0.1"
REVIEWED_GAMEPLAY_VERSION = "v0.13"
REVIEWED_SDK_VERSION = "0.2.8"
REVIEWED_SERVER_COMMIT = "57c2a5b2c070c808092ddcf5425d0c87773fc6e2"
REVIEWED_SDK_COMMIT = "e32ff948b7ee05fa932a1305eef164bc45fc2986"

VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}(?:[A-Za-z0-9.+-]*)$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class VersionCheckError(RuntimeError):
    pass


class _PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)

    @property
    def text(self) -> str:
        return "\n".join(self.parts)


@dataclass(frozen=True, slots=True)
class ContractVersion:
    api: str
    gameplay: str
    server_commit: str
    sdk: str
    sdk_commit: str


def _required_match(pattern: str, text: str, field: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    if match is None:
        raise VersionCheckError(f"contract_{field}_missing")
    return match.group(1)


def parse_contract_page(page: str) -> ContractVersion:
    parser = _PageText()
    parser.feed(page)
    text = parser.text
    api = _required_match(
        r"HTTP and WebSocket API\s+(v[0-9]+\.[0-9]+)",
        text,
        "api",
    )
    gameplay = _required_match(
        r"Gameplay rules\s+(v[0-9]+\.[0-9]+)",
        text,
        "gameplay",
    )
    server_commit = _required_match(
        r"Reviewed server commit\s+([0-9a-f]{40})",
        text,
        "server_commit",
    ).lower()
    sdk_section = _required_match(
        r"Python SDK\s+(.*?)\s+Reviewed SDK commit",
        text,
        "sdk_section",
    )
    sdk = _required_match(
        r"(?:^|[^0-9])v?([0-9]+\.[0-9]+\.[0-9]+)(?:[^0-9]|$)",
        sdk_section,
        "sdk",
    )
    sdk_commit = _required_match(
        r"Reviewed SDK commit\s+([0-9a-f]{40})",
        text,
        "sdk_commit",
    ).lower()
    if not VERSION_RE.fullmatch(api.removeprefix("v")):
        raise VersionCheckError("contract_api_invalid")
    if not VERSION_RE.fullmatch(gameplay.removeprefix("v")):
        raise VersionCheckError("contract_gameplay_invalid")
    if not VERSION_RE.fullmatch(sdk):
        raise VersionCheckError("contract_sdk_invalid")
    if not COMMIT_RE.fullmatch(server_commit) or not COMMIT_RE.fullmatch(sdk_commit):
        raise VersionCheckError("contract_commit_invalid")
    return ContractVersion(api, gameplay, server_commit, sdk, sdk_commit)


def parse_pypi_version(payload: Mapping[str, Any]) -> str:
    info = payload.get("info")
    if not isinstance(info, Mapping):
        raise VersionCheckError("pypi_info_missing")
    latest = info.get("version")
    if not isinstance(latest, str) or not VERSION_RE.fullmatch(latest):
        raise VersionCheckError("pypi_version_invalid")
    return latest


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def evaluate_versions(
    *,
    installed_sdk: str,
    pypi_sdk: str,
    contract: ContractVersion,
    marker_path: Path,
    report_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not VERSION_RE.fullmatch(installed_sdk):
        raise VersionCheckError("installed_sdk_invalid")
    observed = {
        "installed_sdk": installed_sdk,
        "pypi_sdk": pypi_sdk,
        **asdict(contract),
    }
    reviewed = {
        "api": REVIEWED_API_VERSION,
        "gameplay": REVIEWED_GAMEPLAY_VERSION,
        "server_commit": REVIEWED_SERVER_COMMIT,
        "sdk": REVIEWED_SDK_VERSION,
        "sdk_commit": REVIEWED_SDK_COMMIT,
    }
    reasons = []
    if installed_sdk != REVIEWED_SDK_VERSION:
        reasons.append("installed_sdk_changed")
    if pypi_sdk != REVIEWED_SDK_VERSION:
        reasons.append("pypi_sdk_changed")
    if installed_sdk != pypi_sdk:
        reasons.append("installed_sdk_not_latest")
    for field in ("api", "gameplay", "server_commit", "sdk", "sdk_commit"):
        if observed[field] != reviewed[field]:
            reasons.append(f"contract_{field}_changed")

    checked_at = _timestamp(now)
    status = "incompatible" if reasons else "compatible"
    report: dict[str, Any] = {
        "schema_version": 1,
        "checked_at": checked_at,
        "status": status,
        "hold": bool(reasons),
        "reasons": reasons,
        "observed": observed,
        "reviewed": reviewed,
    }
    atomic_write_json(report_path, report)
    if reasons:
        atomic_write_json(marker_path, report)
    else:
        marker_path.unlink(missing_ok=True)
    return report


def record_check_failure(
    *,
    marker_path: Path,
    report_path: Path,
    error: Exception,
    now: datetime | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "checked_at": _timestamp(now),
        "status": "check_failed",
        "hold": True,
        "reasons": [f"check_failed:{type(error).__name__}"],
    }
    atomic_write_json(report_path, report)
    if not marker_path.exists():
        atomic_write_json(marker_path, report)
    return report


def run_check(
    *,
    marker_path: Path,
    report_path: Path,
    client: httpx.Client,
    installed_sdk: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        installed = installed_sdk or version("arena-hero")
        pypi_response = client.get(PYPI_URL)
        pypi_response.raise_for_status()
        pypi_payload = pypi_response.json()
        if not isinstance(pypi_payload, Mapping):
            raise VersionCheckError("pypi_response_invalid")
        source_response = client.get(SOURCE_VERSION_URL)
        source_response.raise_for_status()
        return evaluate_versions(
            installed_sdk=installed,
            pypi_sdk=parse_pypi_version(pypi_payload),
            contract=parse_contract_page(source_response.text),
            marker_path=marker_path,
            report_path=report_path,
            now=now,
        )
    except (
        PackageNotFoundError,
        ValueError,
        OSError,
        httpx.HTTPError,
        VersionCheckError,
    ) as exc:
        return record_check_failure(
            marker_path=marker_path,
            report_path=report_path,
            error=exc,
            now=now,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check Arena Hero versions without applying upgrades."
    )
    parser.add_argument("--marker", type=Path, default=DEFAULT_MARKER_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    timeout = httpx.Timeout(20.0, connect=8.0)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "arena-hero-version-monitor/1.0"},
    ) as client:
        report = run_check(
            marker_path=args.marker,
            report_path=args.report,
            client=client,
        )
    print(
        f"version_check status={report['status']} hold={int(report['hold'])}",
        flush=True,
    )
    return 0 if report["status"] == "compatible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
