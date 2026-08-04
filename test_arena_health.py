from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arena_health import build_health_report, check_heartbeat, check_report, write_heartbeat


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


class ArenaHealthTests(unittest.TestCase):
    def test_heartbeat_round_trip_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.json"
            write_heartbeat(
                path,
                tick=42,
                resources=18,
                population=7,
                core_alive=True,
                generated_at=NOW,
            )

            result = check_heartbeat(path, max_age_seconds=180, now=NOW)

            self.assertTrue(result["ok"])
            self.assertEqual(result["tick"], 42)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["resources"], 18)

    def test_stale_heartbeat_is_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heartbeat.json"
            write_heartbeat(
                path,
                tick=42,
                resources=18,
                population=7,
                core_alive=True,
                generated_at=NOW - timedelta(seconds=181),
            )

            result = check_heartbeat(path, max_age_seconds=180, now=NOW)

            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "stale_or_invalid_heartbeat")

    def test_systemd_report_combines_runtime_and_monitor_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / "heartbeat.json"
            version = root / "version.json"
            supervisor = root / "supervisor.json"
            write_heartbeat(
                heartbeat,
                tick=99,
                resources=25,
                population=19,
                core_alive=True,
                generated_at=NOW,
            )
            version.write_text(
                json.dumps(
                    {
                        "checked_at": "2026-08-03T11:00:00Z",
                        "status": "compatible",
                        "hold": False,
                    }
                ),
                encoding="utf-8",
            )
            supervisor.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-08-03T11:30:00Z",
                        "status": "watch",
                        "requires_human": False,
                    }
                ),
                encoding="utf-8",
            )

            def runner(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "ActiveState=active\nSubState=running\nNRestarts=0\nStatusText=tick 99\n",
                    "",
                )

            report = build_health_report(
                heartbeat_path=heartbeat,
                max_heartbeat_age_seconds=180,
                heartbeat_only=False,
                service="arena-hero-agent.service",
                version_report=version,
                supervisor_report=supervisor,
                require_supervisor=True,
                max_report_age_seconds=7 * 60 * 60,
                now=NOW,
                runner=runner,
            )

            self.assertEqual(report["status"], "healthy")
            self.assertTrue(report["checks"]["service"]["ok"])
            self.assertEqual(report["checks"]["supervisor"]["status"], "watch")

    def test_compatibility_hold_fails_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / "heartbeat.json"
            version = root / "version.json"
            write_heartbeat(
                heartbeat,
                tick=99,
                resources=25,
                population=19,
                core_alive=True,
                generated_at=NOW,
            )
            version.write_text(
                json.dumps(
                    {
                        "checked_at": "2026-08-03T11:00:00Z",
                        "status": "incompatible",
                        "hold": True,
                    }
                ),
                encoding="utf-8",
            )

            def runner(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "ActiveState=active\nSubState=running\nNRestarts=0\n",
                    "",
                )

            report = build_health_report(
                heartbeat_path=heartbeat,
                max_heartbeat_age_seconds=180,
                heartbeat_only=False,
                service="arena-hero-agent.service",
                version_report=version,
                supervisor_report=root / "missing.json",
                require_supervisor=False,
                max_report_age_seconds=7 * 60 * 60,
                now=NOW,
                runner=runner,
            )

            self.assertEqual(report["status"], "unhealthy")
            self.assertFalse(report["checks"]["version"]["ok"])

    def test_stale_optional_supervisor_report_does_not_fail_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            heartbeat = root / "heartbeat.json"
            version = root / "version.json"
            supervisor = root / "supervisor.json"
            write_heartbeat(
                heartbeat,
                tick=99,
                resources=25,
                population=19,
                core_alive=True,
                generated_at=NOW,
            )
            version.write_text(
                json.dumps(
                    {
                        "checked_at": "2026-08-03T11:00:00Z",
                        "status": "compatible",
                        "hold": False,
                    }
                ),
                encoding="utf-8",
            )
            supervisor.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-08-02T11:00:00Z",
                        "status": "watch",
                        "requires_human": True,
                    }
                ),
                encoding="utf-8",
            )

            def runner(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    "ActiveState=active\nSubState=running\nNRestarts=0\n",
                    "",
                )

            report = build_health_report(
                heartbeat_path=heartbeat,
                max_heartbeat_age_seconds=180,
                heartbeat_only=False,
                service="arena-hero-agent.service",
                version_report=version,
                supervisor_report=supervisor,
                require_supervisor=False,
                max_report_age_seconds=7 * 60 * 60,
                now=NOW,
                runner=runner,
            )

            self.assertEqual(report["status"], "healthy")
            self.assertTrue(report["checks"]["supervisor"]["ok"])
            self.assertEqual(
                report["checks"]["supervisor"]["reason"],
                "optional_report_ignored",
            )

    def test_unreadable_report_only_fails_when_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / "invalid.json"
            report_path.write_text("not-json", encoding="utf-8")

            optional = check_report(
                report_path,
                timestamp_field="generated_at",
                max_age_seconds=7 * 60 * 60,
                allowed_statuses={"healthy", "watch"},
                now=NOW,
                required=False,
            )
            required = check_report(
                report_path,
                timestamp_field="generated_at",
                max_age_seconds=7 * 60 * 60,
                allowed_statuses={"healthy", "watch"},
                now=NOW,
                required=True,
            )

            self.assertTrue(optional["ok"])
            self.assertEqual(optional["reason"], "optional_report_ignored")
            self.assertFalse(required["ok"])
            self.assertEqual(required["reason"], "report_unavailable:JSONDecodeError")


if __name__ == "__main__":
    unittest.main()
