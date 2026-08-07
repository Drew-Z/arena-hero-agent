from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Callable, Mapping, Sequence


JOURNAL_UNIT = "arena-hero-agent.service"
AGENT_UNIT = "arena-hero-agent.service"
DEFAULT_RUNTIME_ENV = Path("/etc/arena-hero-agent/runtime.env")
DEFAULT_STATE_PATH = Path("/var/lib/arena-hero-optimizer/state.json")
DEFAULT_REPORT_PATH = Path("/var/lib/arena-hero-optimizer/latest.json")
DEFAULT_COMPATIBILITY_MARKER = Path(
    "/var/lib/arena-hero-version/compatibility-hold.json"
)
WINDOW_TICKS = 400
MIN_SAMPLES = 20
BASELINE_WINDOWS = 4
CANDIDATE_WARMUP_WINDOWS = 1
CANDIDATE_EVAL_WINDOWS = 4
COOLDOWN_SECONDS = 48 * 60 * 60
FAILED_CANDIDATE_COOLDOWN_SECONDS = 7 * 24 * 60 * 60
PROMOTION_THRESHOLD = 1.10
MAX_REJECT_RATIO = 0.02
ALLOWED_WORKER_TARGETS = (10, 12, 17, 22, 23)
LOCKED_BEACON_POLICY = "retreat"

TICK_RE = re.compile(r"\btick=(\d+)\s+accepted=(\w+)")
ANY_TICK_RE = re.compile(r"\btick(?:=|\s)(\d+)\b")
RESOURCE_RE = re.compile(r"\bresources=(\d+)(?:/(\d+))?")
COUNTS_RE = re.compile(r"\b(actions|events)=(\S+)")


@dataclass(frozen=True, slots=True)
class Candidate:
    worker_target: int
    beacon_policy: str = LOCKED_BEACON_POLICY

    @property
    def key(self) -> str:
        return f"workers={self.worker_target};beacon_policy={self.beacon_policy}"


@dataclass(slots=True)
class WindowMetrics:
    sampled_turns: int = 0
    accepted_turns: int = 0
    rejected_turns: int = 0
    first_tick: int | None = None
    latest_tick: int | None = None
    deposits: int = 0
    harvests: int = 0
    core_destroyed: int = 0
    respawns: int = 0
    recovery_samples: int = 0
    danger_samples: int = 0
    manual_overrides: int = 0
    plan_skipped: int = 0
    min_core_hp: int | None = None
    min_core_shield: int | None = None
    latest_resources: int | None = None
    attributed_turns: int = 0
    observed_worker_targets: list[int] = field(default_factory=list)
    observed_beacon_policies: list[str] = field(default_factory=list)
    observed_generations: list[int] = field(default_factory=list)
    return_blocked_workers: int = 0
    clear_core_blocked_workers: int = 0
    scout_blocked_workers: int = 0
    defender_on_core_samples: int = 0
    delivery_blocked_workers: int = 0
    resource_blocked_workers: int = 0
    unexplained_resource_loss: int = 0
    latest_scout_chunks: int | None = None
    latest_scout_oldest_age: int | None = None
    captured_resources: int = 0
    core_healed: int = 0
    unit_healed: int = 0
    insufficient_spawn_failures: int = 0

    @property
    def healthy(self) -> bool:
        if self.sampled_turns < MIN_SAMPLES or self.latest_tick is None:
            return False
        if self.attributed_turns != self.sampled_turns:
            return False
        if (
            len(self.observed_worker_targets) != 1
            or self.observed_beacon_policies != [LOCKED_BEACON_POLICY]
            or len(self.observed_generations) != 1
        ):
            return False
        if self.latest_tick - (self.first_tick or self.latest_tick) < WINDOW_TICKS:
            return False
        if self.rejected_turns / max(1, self.sampled_turns) > MAX_REJECT_RATIO:
            return False
        # A candidate that strands cargo is not an economic improvement, even
        # when harvest counts look healthy. Allow a few transient blocks, but
        # reject sustained return congestion before it can be promoted.
        if self.return_blocked_workers > max(3, self.sampled_turns // 10):
            return False
        if self.delivery_blocked_workers > max(3, self.sampled_turns // 10):
            return False
        if self.defender_on_core_samples > max(3, self.sampled_turns // 10):
            return False
        if self.resource_blocked_workers > max(6, self.sampled_turns // 5):
            return False
        if self.insufficient_spawn_failures >= 3:
            return False
        return not (
            self.core_destroyed
            or self.respawns
            or self.plan_skipped
            or self.manual_overrides
            or self.unexplained_resource_loss
        )

    @property
    def score(self) -> float:
        if not self.healthy:
            return float("-inf")
        tick_span = max(1, (self.latest_tick or 0) - (self.first_tick or 0))
        deposits = self.deposits * 100 / tick_span
        harvests = self.harvests * 100 / tick_span
        recovery_penalty = self.recovery_samples * 2 / max(1, self.sampled_turns)
        danger_penalty = self.danger_samples * 3 / max(1, self.sampled_turns)
        delivery_penalty = self.return_blocked_workers * 0.5 / max(1, self.sampled_turns)
        delivery_penalty += self.delivery_blocked_workers * 0.5 / max(
            1, self.sampled_turns
        )
        delivery_penalty += self.resource_blocked_workers * 0.5 / max(
            1, self.sampled_turns
        )
        delivery_penalty += self.insufficient_spawn_failures / max(
            1, self.sampled_turns
        )
        return (
            deposits
            + harvests * 0.25
            - recovery_penalty
            - danger_penalty
            - delivery_penalty
        )

    def matches(self, candidate: Candidate) -> bool:
        return (
            self.observed_worker_targets == [candidate.worker_target]
            and self.observed_beacon_policies == [candidate.beacon_policy]
        )


@dataclass(slots=True)
class OptimizerState:
    active: dict[str, object] = field(
        default_factory=lambda: asdict(Candidate(23))
    )
    last_good: dict[str, object] = field(
        default_factory=lambda: asdict(Candidate(23))
    )
    generation: int = 0
    last_observed_tick: int | None = None
    cooldown_until: float = 0.0
    candidate_started_tick: int | None = None
    warmup_windows: int = 0
    baseline_windows: list[dict[str, object]] = field(default_factory=list)
    candidate_windows: list[dict[str, object]] = field(default_factory=list)
    baseline_score: float | None = None
    failed_candidates: dict[str, float] = field(default_factory=dict)
    last_decision: str = "initialized"
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentStatus:
    active_state: str
    sub_state: str
    tick: int | None
    generation: int | None
    core_state: str | None
    recovery: int | None
    danger: int | None
    core_hp: int | None
    core_shield: int | None

    @property
    def safe_to_reconfigure(self) -> bool:
        return (
            self.active_state == "active"
            and self.sub_state == "running"
            and self.tick is not None
            and self.core_state == "NORMAL"
            and self.recovery == 0
            and self.danger == 0
            and (self.core_hp or 0) >= 4
            and (self.core_shield or 0) >= 4
        )


def atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_json(path: Path, default: Mapping[str, object]) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _candidate_from_mapping(value: Mapping[str, object]) -> Candidate:
    worker_target = value.get("worker_target")
    beacon_policy = value.get("beacon_policy")
    if worker_target not in ALLOWED_WORKER_TARGETS or beacon_policy != LOCKED_BEACON_POLICY:
        raise ValueError("candidate_not_allowed")
    return Candidate(int(worker_target), str(beacon_policy))


def load_state(path: Path) -> OptimizerState:
    raw = load_json(path, {})
    state = OptimizerState()
    for name in (
        "active",
        "last_good",
        "generation",
        "last_observed_tick",
        "cooldown_until",
        "candidate_started_tick",
        "warmup_windows",
        "baseline_windows",
        "candidate_windows",
        "baseline_score",
        "failed_candidates",
        "last_decision",
        "last_error",
    ):
        if name in raw:
            setattr(state, name, raw[name])
    state.active = asdict(_candidate_from_mapping(state.active))
    state.last_good = asdict(_candidate_from_mapping(state.last_good))
    return state


def save_state(path: Path, state: OptimizerState) -> None:
    atomic_write_json(path, asdict(state))


def _parse_count_list(value: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    if value == "none":
        return counts
    for item in value.split(","):
        name, separator, raw_count = item.rpartition(":")
        if not separator:
            continue
        try:
            count = int(raw_count)
        except ValueError:
            continue
        if name and count >= 0:
            counts[name] = counts.get(name, 0) + count
    return counts


def extract_window_metrics(log_text: str) -> WindowMetrics:
    metrics = WindowMetrics()
    worker_targets: set[int] = set()
    beacon_policies: set[str] = set()
    generations: set[int] = set()
    for line in log_text.splitlines():
        if "manual_override" in line:
            metrics.manual_overrides += 1
        if "plan_skipped" in line:
            metrics.plan_skipped += 1
        unexplained_loss = re.search(r"\bunexplained_loss=(\d+)", line)
        if unexplained_loss and "unexplained_resource_loss" in line:
            metrics.unexplained_resource_loss += int(unexplained_loss.group(1))
        match = TICK_RE.search(line)
        if not match:
            continue
        tick = int(match.group(1))
        metrics.sampled_turns += 1
        metrics.accepted_turns += match.group(2).lower() == "true"
        metrics.rejected_turns += match.group(2).lower() != "true"
        metrics.first_tick = tick if metrics.first_tick is None else min(metrics.first_tick, tick)
        metrics.latest_tick = max(metrics.latest_tick or tick, tick)
        resource_match = RESOURCE_RE.search(line)
        if resource_match:
            metrics.latest_resources = int(resource_match.group(1))
        for counts_match in COUNTS_RE.finditer(line):
            counts = _parse_count_list(counts_match.group(2))
            metrics.deposits += counts.get("DEPOSIT_SUCCEEDED", 0)
            metrics.harvests += counts.get("HARVEST_SUCCEEDED", 0)
            metrics.core_destroyed += counts.get("CORE_DESTROYED", 0)
            metrics.respawns += counts.get("CORE_RESPAWNED", 0)
            metrics.insufficient_spawn_failures += counts.get(
                "CORE_SPAWN_FAILED/INSUFFICIENT_RESOURCES",
                0,
            )
        recovery = re.search(r"\brecovery=(\d+)", line)
        if recovery and recovery.group(1) != "0":
            metrics.recovery_samples += 1
        danger = re.search(r"\bdanger_cells=(\d+)", line)
        if danger and int(danger.group(1)) > 0:
            metrics.danger_samples += 1
        defender_on_core = re.search(r"\bdefender_on_core=(\d+)", line)
        delivery_blocked = re.search(r"\bdelivery_blocked=(\d+)", line)
        resource_blocked = re.search(r"\bresource_blocked=(\d+)", line)
        scout_chunks = re.search(r"\bscout_chunks=(\d+)", line)
        scout_oldest_age = re.search(r"\bscout_oldest_age=(\d+)", line)
        captured_resources = re.search(r"\bcaptured_resources=(\d+)", line)
        core_healed = re.search(r"\bcore_healed=(\d+)", line)
        unit_healed = re.search(r"\bunit_healed=(\d+)", line)
        if defender_on_core and int(defender_on_core.group(1)) > 0:
            metrics.defender_on_core_samples += 1
        if delivery_blocked:
            metrics.delivery_blocked_workers += int(delivery_blocked.group(1))
        if resource_blocked:
            metrics.resource_blocked_workers += int(resource_blocked.group(1))
        if scout_chunks:
            metrics.latest_scout_chunks = int(scout_chunks.group(1))
        if scout_oldest_age:
            metrics.latest_scout_oldest_age = int(scout_oldest_age.group(1))
        if captured_resources:
            metrics.captured_resources += int(captured_resources.group(1))
        if core_healed:
            metrics.core_healed += int(core_healed.group(1))
        if unit_healed:
            metrics.unit_healed += int(unit_healed.group(1))
        hp = re.search(r"\bcore_hp=(\d+)", line)
        shield = re.search(r"\bcore_shield=(\d+)", line)
        if hp:
            value = int(hp.group(1))
            metrics.min_core_hp = value if metrics.min_core_hp is None else min(metrics.min_core_hp, value)
        if shield:
            value = int(shield.group(1))
            metrics.min_core_shield = value if metrics.min_core_shield is None else min(metrics.min_core_shield, value)
        worker_state = re.search(r"\bworker_state=(.+)$", line)
        if worker_state:
            states = worker_state.group(1).split(";")
            metrics.return_blocked_workers += sum(
                "/mRETURN_BLOCKED" in state for state in states
            )
            metrics.clear_core_blocked_workers += sum(
                "/mCLEAR_CORE_BLOCKED" in state for state in states
            )
            metrics.scout_blocked_workers += sum(
                "/mSCOUT_BLOCKED" in state for state in states
            )
        worker_target = re.search(r"\bworker_target=(\d+)\b", line)
        beacon_policy = re.search(r"\bbeacon_policy=([a-z]+)\b", line)
        generation = re.search(r"\btuning_generation=(\d+)\b", line)
        if worker_target and beacon_policy and generation:
            metrics.attributed_turns += 1
            worker_targets.add(int(worker_target.group(1)))
            beacon_policies.add(beacon_policy.group(1))
            generations.add(int(generation.group(1)))
    metrics.observed_worker_targets = sorted(worker_targets)
    metrics.observed_beacon_policies = sorted(beacon_policies)
    metrics.observed_generations = sorted(generations)
    return metrics


def _logs_after_tick(log_text: str, last_tick: int | None) -> str:
    if last_tick is None:
        return log_text
    lines = []
    for line in log_text.splitlines():
        tick = ANY_TICK_RE.search(line)
        if tick and int(tick.group(1)) > last_tick:
            lines.append(line)
    return "\n".join(lines)


def read_journal(
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> str:
    result = runner(
        [
            "journalctl",
            f"--unit={JOURNAL_UNIT}",
            "--since=-6h",
            "--lines=4000",
            "--no-pager",
            "--output=short-iso-precise",
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError("journal_unavailable")
    return (result.stdout or b"").decode("utf-8", errors="replace")


def read_runtime_config(path: Path) -> Candidate:
    if not path.exists():
        write_runtime_config(path, Candidate(23), 0)
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name in {"ARENA_WORKER_TARGET", "ARENA_BEACON_POLICY"}:
            values[name] = value.strip()
    return _candidate_from_mapping(
        {
            "worker_target": int(values.get("ARENA_WORKER_TARGET", "23")),
            "beacon_policy": values.get("ARENA_BEACON_POLICY", LOCKED_BEACON_POLICY),
        }
    )


def write_runtime_config(path: Path, candidate: Candidate, generation: int) -> None:
    if candidate.worker_target not in ALLOWED_WORKER_TARGETS or candidate.beacon_policy != LOCKED_BEACON_POLICY:
        raise ValueError("candidate_not_allowed")
    payload = (
        f"ARENA_WORKER_TARGET={candidate.worker_target}\n"
        f"ARENA_BEACON_POLICY={candidate.beacon_policy}\n"
        f"ARENA_TUNING_GENERATION={generation}\n"
    ).encode("ascii")
    _atomic_write_bytes(path, payload, mode=0o644)


def run_systemctl(
    args: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    return runner(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def service_is_active(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    result = run_systemctl(["is-active", "--quiet", AGENT_UNIT], runner=runner)
    return result.returncode == 0


def read_agent_status(
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> AgentStatus:
    result = run_systemctl(
        [
            "show",
            AGENT_UNIT,
            "--property=ActiveState",
            "--property=SubState",
            "--property=StatusText",
        ],
        runner=runner,
    )
    if result.returncode != 0:
        return AgentStatus("unknown", "unknown", None, None, None, None, None, None, None)
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            values[name] = value
    status_text = values.get("StatusText", "")

    def integer(pattern: str) -> int | None:
        match = re.search(pattern, status_text)
        return int(match.group(1)) if match else None

    core_state_match = re.search(r"\bcore\s+-?\d+:-?\d+\s+(NORMAL|MOVING)\b", status_text)
    return AgentStatus(
        values.get("ActiveState", "unknown"),
        values.get("SubState", "unknown"),
        integer(r"\btick\s+(\d+)\b"),
        integer(r"\btuning_generation\s+(\d+)\b"),
        core_state_match.group(1) if core_state_match else None,
        integer(r"\brecovery\s+(\d+)\b"),
        integer(r"\bdanger\s+(\d+)\b"),
        integer(r"\bcore_hp\s+(\d+)\b"),
        integer(r"\bcore_shield\s+(\d+)\b"),
    )


def wait_for_generation(
    generation: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleeper: Callable[[float], None] = time.sleep,
    attempts: int = 18,
) -> bool:
    for attempt in range(attempts):
        status = read_agent_status(runner=runner)
        if (
            status.active_state == "active"
            and status.sub_state == "running"
            and status.tick is not None
            and status.generation == generation
        ):
            return True
        if attempt + 1 < attempts:
            sleeper(5)
    return False


def _generation_from_env(raw: bytes | None) -> int:
    if not raw:
        return 0
    match = re.search(rb"(?m)^ARENA_TUNING_GENERATION=(\d+)\s*$", raw)
    return int(match.group(1)) if match else 0


def apply_candidate(
    path: Path,
    candidate: Candidate,
    generation: int,
    *,
    systemctl_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    health_checker: Callable[[int], bool] | None = None,
) -> tuple[bool, str]:
    previous = path.read_bytes() if path.exists() else None
    checker = health_checker or (
        lambda expected_generation: wait_for_generation(
            expected_generation,
            runner=systemctl_runner,
        )
    )
    write_runtime_config(path, candidate, generation)
    restart = run_systemctl(["restart", AGENT_UNIT], runner=systemctl_runner)
    if restart.returncode == 0 and checker(generation):
        return True, "applied"
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write_bytes(path, previous, mode=0o644)
    rollback = run_systemctl(["restart", AGENT_UNIT], runner=systemctl_runner)
    if rollback.returncode != 0 or not checker(_generation_from_env(previous)):
        return False, "rollback_failed"
    return False, "apply_failed_rolled_back"


def _next_candidate(active: Candidate, now: float, failed: Mapping[str, float]) -> Candidate | None:
    for worker_target in ALLOWED_WORKER_TARGETS:
        if worker_target <= active.worker_target:
            continue
        candidate = Candidate(worker_target)
        if float(failed.get(candidate.key, 0)) > now:
            continue
        return candidate
    return None


def _compatibility_marker_present(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return path.exists()
    except OSError:
        return True


def _window_record(metrics: WindowMetrics) -> dict[str, object]:
    return {"score": metrics.score, "healthy": metrics.healthy, **asdict(metrics)}


def optimize_once(
    *,
    runtime_path: Path,
    state_path: Path,
    report_path: Path,
    journal_text: str,
    now: float | None = None,
    systemctl_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    compatibility_marker_path: Path | None = None,
) -> dict[str, object]:
    now = time.time() if now is None else now
    state = load_state(state_path)
    if _compatibility_marker_present(compatibility_marker_path):
        metrics = extract_window_metrics(
            _logs_after_tick(journal_text, state.last_observed_tick)
        )
        state.last_observed_tick = metrics.latest_tick
        state.last_decision = "hold"
        state.last_error = None
        report: dict[str, object] = {
            "schema_version": 1,
            "generated_at": datetime.fromtimestamp(now, UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "decision": "hold",
            "detail": "compatibility_uncertain",
            "compatibility_hold": True,
            "active": state.active,
            "generation": state.generation,
            "metrics": asdict(metrics),
            "state": asdict(state),
        }
        save_state(state_path, state)
        atomic_write_json(report_path, report)
        return report
    active = read_runtime_config(runtime_path)
    metrics = extract_window_metrics(
        _logs_after_tick(journal_text, state.last_observed_tick)
    )
    decision = "hold"
    detail = ""

    if not service_is_active(runner=systemctl_runner):
        decision, detail = "hold", "agent_inactive"
    elif state.last_observed_tick is not None and metrics.latest_tick is None:
        decision, detail = "hold", "tick_not_advancing"
    elif not metrics.healthy or not metrics.matches(active):
        hard_failure = bool(
            metrics.core_destroyed
            or metrics.respawns
            or metrics.plan_skipped
            or (
                metrics.sampled_turns
                and metrics.rejected_turns / metrics.sampled_turns > MAX_REJECT_RATIO
            )
        )
        if state.candidate_started_tick is not None and hard_failure:
            previous = _candidate_from_mapping(state.last_good)
            failed_candidate = active
            rolled_back, rollback_detail = apply_candidate(
                runtime_path,
                previous,
                state.generation + 1,
                systemctl_runner=systemctl_runner,
            )
            state.generation += 1
            state.failed_candidates[failed_candidate.key] = (
                now + FAILED_CANDIDATE_COOLDOWN_SECONDS
            )
            if rolled_back:
                state.active = asdict(previous)
                state.candidate_started_tick = None
                state.candidate_windows.clear()
                state.warmup_windows = 0
                state.cooldown_until = now + COOLDOWN_SECONDS
                decision, detail = "rollback", "candidate_hard_failure"
            else:
                decision, detail = "rollback_failed", rollback_detail
        else:
            detail = (
                "window_config_mismatch"
                if metrics.healthy and not metrics.matches(active)
                else "unsafe_or_insufficient_window"
            )
            decision = "hold"
            if state.candidate_started_tick is None:
                state.baseline_windows.clear()
    elif state.last_observed_tick is not None and metrics.latest_tick <= state.last_observed_tick:
        decision, detail = "hold", "tick_not_advancing"
    elif active.key != Candidate(**state.active).key:
        state.active = asdict(active)
        state.last_good = asdict(active)
        state.baseline_windows.clear()
        state.candidate_windows.clear()
        state.warmup_windows = 0
        decision, detail = "adopt_runtime_config", "runtime_config_changed"
    elif state.candidate_started_tick is not None:
        if state.warmup_windows < CANDIDATE_WARMUP_WINDOWS:
            state.warmup_windows += 1
            decision, detail = "candidate_warmup", str(state.warmup_windows)
        else:
            state.candidate_windows.append(_window_record(metrics))
            if len(state.candidate_windows) < CANDIDATE_EVAL_WINDOWS:
                decision, detail = "candidate_collect", str(len(state.candidate_windows))
            else:
                candidate_score = median(
                    float(window["score"]) for window in state.candidate_windows
                )
                baseline_score = max(state.baseline_score or 0.0, 0.1)
                if candidate_score >= baseline_score * PROMOTION_THRESHOLD:
                    state.last_good = dict(state.active)
                    state.baseline_windows = list(state.candidate_windows)
                    state.baseline_score = candidate_score
                    state.candidate_started_tick = None
                    state.candidate_windows.clear()
                    state.warmup_windows = 0
                    state.cooldown_until = now + COOLDOWN_SECONDS
                    decision, detail = "promote", f"{candidate_score:.3f}>{baseline_score:.3f}"
                else:
                    previous = _candidate_from_mapping(state.last_good)
                    state.failed_candidates[active.key] = now + FAILED_CANDIDATE_COOLDOWN_SECONDS
                    applied, detail = apply_candidate(
                        runtime_path,
                        previous,
                        state.generation + 1,
                        systemctl_runner=systemctl_runner,
                    )
                    state.generation += 1
                    if applied:
                        state.active = asdict(previous)
                        state.candidate_started_tick = None
                        state.candidate_windows.clear()
                        state.warmup_windows = 0
                        state.cooldown_until = now + COOLDOWN_SECONDS
                        decision = "rollback"
                    else:
                        decision = "rollback_failed"
                    detail = f"{detail};candidate_score={candidate_score:.3f};baseline={baseline_score:.3f}"
    else:
        state.baseline_windows.append(_window_record(metrics))
        state.baseline_windows = state.baseline_windows[-BASELINE_WINDOWS:]
        if len(state.baseline_windows) < BASELINE_WINDOWS:
            decision, detail = "baseline_collect", str(len(state.baseline_windows))
        else:
            state.baseline_score = max(
                median(
                float(window["score"]) for window in state.baseline_windows
                ),
                0.1,
            )
            if now < state.cooldown_until:
                decision, detail = "cooldown", str(round(state.cooldown_until - now))
            else:
                candidate = _next_candidate(active, now, state.failed_candidates)
                if candidate is None:
                    decision, detail = "hold", "no_allowed_candidate"
                else:
                    status = read_agent_status(runner=systemctl_runner)
                    if not status.safe_to_reconfigure:
                        decision, detail = "hold", "unsafe_live_status"
                        candidate = None
                if candidate is not None:
                    state.generation += 1
                    applied, apply_detail = apply_candidate(
                        runtime_path,
                        candidate,
                        state.generation,
                        systemctl_runner=systemctl_runner,
                    )
                    if applied:
                        state.active = asdict(candidate)
                        state.candidate_started_tick = metrics.latest_tick
                        state.warmup_windows = 0
                        state.candidate_windows.clear()
                        decision, detail = "candidate_started", candidate.key
                    else:
                        state.failed_candidates[candidate.key] = now + FAILED_CANDIDATE_COOLDOWN_SECONDS
                        decision, detail = "apply_failed", apply_detail

    state.last_observed_tick = metrics.latest_tick
    state.last_decision = decision
    state.last_error = None
    report: dict[str, object] = {
        "schema_version": 1,
        "generated_at": datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z"),
        "decision": decision,
        "detail": detail,
        "compatibility_hold": False,
        "active": state.active,
        "generation": state.generation,
        "metrics": asdict(metrics),
        "state": asdict(state),
    }
    save_state(state_path, state)
    atomic_write_json(report_path, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic Arena Hero candidate optimizer.")
    parser.add_argument("--runtime-env", type=Path, default=DEFAULT_RUNTIME_ENV)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--compatibility-marker",
        type=Path,
        default=DEFAULT_COMPATIBILITY_MARKER,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        journal_text = read_journal()
        report = optimize_once(
            runtime_path=args.runtime_env,
            state_path=args.state,
            report_path=args.report,
            journal_text=journal_text,
            compatibility_marker_path=args.compatibility_marker,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"optimizer_failed error={type(exc).__name__}", flush=True)
        return 1
    print(
        f"optimizer_complete decision={report['decision']} "
        f"detail={report['detail']} generation={report['generation']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
