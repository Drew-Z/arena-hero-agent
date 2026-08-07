from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_HEARTBEAT_PATH = Path("/run/arena-hero-agent/heartbeat.json")
DEFAULT_VERSION_REPORT = Path("/var/lib/arena-hero-version/latest.json")
DEFAULT_SUPERVISOR_REPORT = Path("/var/lib/arena-hero-supervisor/latest.json")
DEFAULT_MAX_HEARTBEAT_AGE_SECONDS = 180
DEFAULT_MAX_REPORT_AGE_SECONDS = 7 * 60 * 60
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 5

CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _timestamp(value: datetime | None = None) -> str:
    return (value or datetime.now(UTC)).astimezone(UTC).isoformat().replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp_missing")
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("timestamp_timezone_missing")
    return parsed.astimezone(UTC)


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_heartbeat(
    path: Path,
    *,
    tick: int,
    resources: int,
    population: int,
    core_alive: bool,
    generated_at: datetime | None = None,
) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "generated_at": _timestamp(generated_at),
            "tick": tick,
            "resources": resources,
            "population": population,
            "core_alive": core_alive,
        },
    )


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("json_root_not_object")
    return value


def check_heartbeat(
    path: Path,
    *,
    max_age_seconds: int,
    max_future_skew_seconds: int = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        payload = _load_json(path)
        generated_at = _parse_timestamp(payload.get("generated_at"))
        signed_age_seconds = (now - generated_at).total_seconds()
        timestamp_valid = signed_age_seconds >= -max_future_skew_seconds
        age_seconds = max(0.0, signed_age_seconds)
        tick = payload.get("tick")
        core_alive = payload.get("core_alive")
        ok = (
            isinstance(tick, int)
            and tick > 0
            and core_alive is True
            and timestamp_valid
            and age_seconds <= max_age_seconds
        )
        if ok:
            reason = "ok"
        elif not timestamp_valid:
            reason = "heartbeat_timestamp_in_future"
        else:
            reason = "stale_or_invalid_heartbeat"
        return {
            "ok": ok,
            "reason": reason,
            "path": str(path),
            "age_seconds": round(age_seconds, 3),
            "future_skew_seconds": round(max(0.0, -signed_age_seconds), 3),
            "tick": tick,
            "core_alive": core_alive,
            "resources": payload.get("resources"),
            "population": payload.get("population"),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "reason": f"heartbeat_unavailable:{type(exc).__name__}",
            "path": str(path),
        }


def _default_runner(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def check_systemd_service(
    service: str,
    *,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    try:
        completed = runner(
            (
                "systemctl",
                "show",
                service,
                "--property=ActiveState,SubState,NRestarts,StatusText",
                "--no-pager",
            )
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "reason": f"systemctl_failed:{type(exc).__name__}"}
    values = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    ok = (
        completed.returncode == 0
        and values.get("ActiveState") == "active"
        and values.get("SubState") == "running"
    )
    return {
        "ok": ok,
        "reason": "ok" if ok else "service_not_running",
        "service": service,
        "active_state": values.get("ActiveState"),
        "sub_state": values.get("SubState"),
        "restarts": values.get("NRestarts"),
        "status_text": values.get("StatusText"),
    }


def check_report(
    path: Path,
    *,
    timestamp_field: str,
    max_age_seconds: int,
    allowed_statuses: set[str],
    max_future_skew_seconds: int = DEFAULT_MAX_FUTURE_SKEW_SECONDS,
    now: datetime | None = None,
    required: bool = True,
) -> dict[str, Any]:
    if not path.exists() and not required:
        return {"ok": True, "reason": "optional_report_not_present", "path": str(path)}
    now = (now or datetime.now(UTC)).astimezone(UTC)
    try:
        payload = _load_json(path)
        generated_at = _parse_timestamp(payload.get(timestamp_field))
        signed_age_seconds = (now - generated_at).total_seconds()
        timestamp_valid = signed_age_seconds >= -max_future_skew_seconds
        age_seconds = max(0.0, signed_age_seconds)
        status = payload.get("status")
        hold = bool(payload.get("hold", False))
        requires_human = bool(payload.get("requires_human", False))
        report_ok = (
            status in allowed_statuses
            and not hold
            and not requires_human
            and timestamp_valid
            and age_seconds <= max_age_seconds
        )
        ok = report_ok or not required
        if report_ok:
            reason = "ok"
        elif not timestamp_valid and required:
            reason = "report_timestamp_in_future"
        elif required:
            reason = "report_stale_or_unhealthy"
        else:
            reason = "optional_report_ignored"
        return {
            "ok": ok,
            "reason": reason,
            "path": str(path),
            "age_seconds": round(age_seconds, 3),
            "future_skew_seconds": round(max(0.0, -signed_age_seconds), 3),
            "status": status,
            "hold": hold,
            "requires_human": requires_human,
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": not required,
            "reason": (
                f"report_unavailable:{type(exc).__name__}"
                if required
                else "optional_report_ignored"
            ),
            "path": str(path),
        }


def build_health_report(
    *,
    heartbeat_path: Path,
    max_heartbeat_age_seconds: int,
    heartbeat_only: bool,
    service: str,
    version_report: Path,
    supervisor_report: Path,
    require_supervisor: bool,
    max_report_age_seconds: int,
    now: datetime | None = None,
    runner: CommandRunner = _default_runner,
) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    checks: dict[str, dict[str, Any]] = {
        "heartbeat": check_heartbeat(
            heartbeat_path,
            max_age_seconds=max_heartbeat_age_seconds,
            now=now,
        )
    }
    if not heartbeat_only:
        checks["service"] = check_systemd_service(service, runner=runner)
        checks["version"] = check_report(
            version_report,
            timestamp_field="checked_at",
            max_age_seconds=max_report_age_seconds,
            allowed_statuses={"compatible"},
            now=now,
        )
        checks["supervisor"] = check_report(
            supervisor_report,
            timestamp_field="generated_at",
            max_age_seconds=max_report_age_seconds,
            allowed_statuses={"healthy", "watch"},
            now=now,
            required=require_supervisor,
        )
    ok = all(check["ok"] for check in checks.values())
    return {
        "schema_version": 1,
        "generated_at": _timestamp(now),
        "status": "healthy" if ok else "unhealthy",
        "checks": checks,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Arena Hero unattended runtime health."
    )
    parser.add_argument(
        "--heartbeat-file",
        type=Path,
        default=Path(os.environ.get("ARENA_HEARTBEAT_PATH", DEFAULT_HEARTBEAT_PATH)),
    )
    parser.add_argument(
        "--max-heartbeat-age-seconds",
        type=int,
        default=DEFAULT_MAX_HEARTBEAT_AGE_SECONDS,
    )
    parser.add_argument("--heartbeat-only", action="store_true")
    parser.add_argument("--service", default="arena-hero-agent.service")
    parser.add_argument("--version-report", type=Path, default=DEFAULT_VERSION_REPORT)
    parser.add_argument(
        "--supervisor-report", type=Path, default=DEFAULT_SUPERVISOR_REPORT
    )
    parser.add_argument("--require-supervisor", action="store_true")
    parser.add_argument(
        "--max-report-age-seconds",
        type=int,
        default=DEFAULT_MAX_REPORT_AGE_SECONDS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_heartbeat_age_seconds <= 0 or args.max_report_age_seconds <= 0:
        raise SystemExit("health age limits must be positive")
    report = build_health_report(
        heartbeat_path=args.heartbeat_file,
        max_heartbeat_age_seconds=args.max_heartbeat_age_seconds,
        heartbeat_only=args.heartbeat_only,
        service=args.service,
        version_report=args.version_report,
        supervisor_report=args.supervisor_report,
        require_supervisor=args.require_supervisor,
        max_report_age_seconds=args.max_report_age_seconds,
    )
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
