from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from arena_version_monitor import (
    REVIEWED_API_VERSION,
    REVIEWED_GAMEPLAY_VERSION,
    REVIEWED_SDK_COMMIT,
    REVIEWED_SDK_VERSION,
    REVIEWED_SERVER_COMMIT,
    ContractVersion,
    evaluate_versions,
    run_check,
)


MATCHING_CONTRACT = ContractVersion(
    api=REVIEWED_API_VERSION,
    gameplay=REVIEWED_GAMEPLAY_VERSION,
    server_commit=REVIEWED_SERVER_COMMIT,
    sdk=REVIEWED_SDK_VERSION,
    sdk_commit=REVIEWED_SDK_COMMIT,
)


def contract_html(contract: ContractVersion = MATCHING_CONTRACT) -> str:
    return f"""
    <html><body><table>
      <tr><td>HTTP and WebSocket API</td><td>{contract.api}</td></tr>
      <tr><td>Gameplay rules</td><td>{contract.gameplay}</td></tr>
      <tr><td>Reviewed server commit</td><td><code>{contract.server_commit}</code></td></tr>
      <tr><td>Python SDK</td><td>arena-hero-python, v{contract.sdk}</td></tr>
      <tr><td>Reviewed SDK commit</td><td><code>{contract.sdk_commit}</code></td></tr>
    </table></body></html>
    """


class FakeClient:
    def __init__(self, *, pypi_version: str = REVIEWED_SDK_VERSION, page: str | None = None):
        self.pypi_version = pypi_version
        self.page = page if page is not None else contract_html()

    def get(self, url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "pypi.org" in url:
            return httpx.Response(
                200,
                json={"info": {"version": self.pypi_version}},
                request=request,
            )
        return httpx.Response(200, text=self.page, request=request)


class VersionMonitorTests(unittest.TestCase):
    def test_matching_versions_remove_old_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "compatibility-hold.json"
            report = root / "latest.json"
            marker.write_text('{"hold":true}\n', encoding="utf-8")
            result = evaluate_versions(
                installed_sdk=REVIEWED_SDK_VERSION,
                pypi_sdk=REVIEWED_SDK_VERSION,
                contract=MATCHING_CONTRACT,
                marker_path=marker,
                report_path=report,
                now=datetime(2026, 8, 2, tzinfo=UTC),
            )
            self.assertEqual(result["status"], "compatible")
            self.assertFalse(marker.exists())
            self.assertFalse(json.loads(report.read_text())["hold"])

    def test_each_reviewed_contract_change_creates_hold(self) -> None:
        changes = (
            replace(MATCHING_CONTRACT, gameplay="v0.13"),
            replace(MATCHING_CONTRACT, server_commit="a" * 40),
            replace(MATCHING_CONTRACT, sdk_commit="b" * 40),
        )
        for changed in changes:
            with self.subTest(changed=changed):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    marker = root / "hold.json"
                    result = evaluate_versions(
                        installed_sdk=REVIEWED_SDK_VERSION,
                        pypi_sdk=REVIEWED_SDK_VERSION,
                        contract=changed,
                        marker_path=marker,
                        report_path=root / "latest.json",
                    )
                    self.assertEqual(result["status"], "incompatible")
                    self.assertTrue(marker.exists())

    def test_pypi_or_installed_sdk_drift_creates_hold(self) -> None:
        for installed, latest in (("0.2.7", "0.2.7"), ("0.2.6", "0.2.7")):
            with self.subTest(installed=installed, latest=latest):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    result = evaluate_versions(
                        installed_sdk=installed,
                        pypi_sdk=latest,
                        contract=MATCHING_CONTRACT,
                        marker_path=root / "hold.json",
                        report_path=root / "latest.json",
                    )
                    self.assertTrue(result["hold"])

    def test_failed_check_preserves_existing_marker_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "hold.json"
            report = root / "latest.json"
            original = b'{"status":"incompatible","reason":"old"}\n'
            marker.write_bytes(original)
            result = run_check(
                marker_path=marker,
                report_path=report,
                client=FakeClient(page="<html>missing fields</html>"),
                installed_sdk=REVIEWED_SDK_VERSION,
            )
            self.assertEqual(result["status"], "check_failed")
            self.assertEqual(marker.read_bytes(), original)

    def test_first_failed_check_creates_fail_closed_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "hold.json"
            result = run_check(
                marker_path=marker,
                report_path=root / "latest.json",
                client=FakeClient(page="<html>missing fields</html>"),
                installed_sdk=REVIEWED_SDK_VERSION,
            )
            self.assertTrue(result["hold"])
            self.assertTrue(marker.exists())
            self.assertEqual(list(root.glob(".latest.json.*")), [])


if __name__ == "__main__":
    unittest.main()
