from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from arena_optimizer import (
    AgentStatus,
    Candidate,
    OptimizerState,
    _candidate_from_mapping,
    _logs_after_tick,
    _next_candidate,
    apply_candidate,
    extract_window_metrics,
    optimize_once,
    read_agent_status,
    read_runtime_config,
    run_systemctl,
    save_state,
    write_runtime_config,
)


def healthy_log(
    start_tick: int,
    *,
    deposits: int = 1,
    extra_event: str | None = None,
    worker_target: int = 12,
    generation: int = 0,
) -> str:
    lines = []
    for index in range(21):
        events = [f"DEPOSIT_SUCCEEDED:{deposits}", "HARVEST_SUCCEEDED:1"]
        if extra_event and index == 20:
            events.append(f"{extra_event}:1")
        tick = start_tick + index * 25
        lines.append(
            f"tick={tick} accepted=True resources=20/80 workers=12 "
            f"vanguards=2 rangers=2 cargo=1 visible_resources=2 "
            f"actions=MOVE:10 events={','.join(events)} recovery=0 "
            f"danger_cells=0 core_hp=5 core_shield=5 "
            f"worker_target={worker_target} beacon_policy=pursue "
            f"tuning_generation={generation}"
        )
    return "\n".join(lines)


class SystemctlRunner:
    def __init__(self, *, core_state: str = "NORMAL", generation: int = 0):
        self.core_state = core_state
        self.generation = generation
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[1:3] == ["is-active", "--quiet"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1] == "show":
            stdout = (
                "ActiveState=active\n"
                "SubState=running\n"
                "StatusText=tick 5000; core 9:-179 "
                f"{self.core_state}; recovery 0; danger 0; core_hp 5; "
                f"core_shield 5; tuning_generation {self.generation}\n"
            )
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        if argv[1] == "restart":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "unexpected")


class OptimizerTests(unittest.TestCase):
    def test_extract_window_metrics_scores_healthy_collection(self) -> None:
        metrics = extract_window_metrics(healthy_log(1000, deposits=2))
        self.assertTrue(metrics.healthy)
        self.assertEqual(metrics.sampled_turns, 21)
        self.assertEqual(metrics.deposits, 42)
        self.assertEqual(metrics.harvests, 21)
        self.assertGreater(metrics.score, 0)

    def test_mixed_generation_window_is_not_eligible(self) -> None:
        mixed = "\n".join(
            [
                healthy_log(1000, generation=0),
                healthy_log(1600, generation=1),
            ]
        )
        metrics = extract_window_metrics(mixed)
        self.assertFalse(metrics.healthy)
        self.assertEqual(metrics.observed_generations, [0, 1])

    def test_sustained_return_blocking_rejects_candidate_window(self) -> None:
        congested = healthy_log(1000).replace(
            "tuning_generation=0",
            "tuning_generation=0 worker_state=abc123@1:0/c1/aWAIT/mRETURN_BLOCKED/t0:0",
        )
        metrics = extract_window_metrics(congested)
        self.assertEqual(metrics.return_blocked_workers, 21)
        self.assertFalse(metrics.healthy)

    def test_transient_resource_block_is_parsed_and_penalized(self) -> None:
        baseline = extract_window_metrics(healthy_log(1000))
        transient = healthy_log(1000).replace(
            "tuning_generation=0",
            (
                "tuning_generation=0 resource_blocked=1 "
                "scout_chunks=7 scout_oldest_age=31"
            ),
            1,
        )
        metrics = extract_window_metrics(transient)

        self.assertTrue(metrics.healthy)
        self.assertEqual(metrics.resource_blocked_workers, 1)
        self.assertEqual(metrics.latest_scout_chunks, 7)
        self.assertEqual(metrics.latest_scout_oldest_age, 31)
        self.assertLess(metrics.score, baseline.score)

    def test_sustained_resource_block_rejects_candidate_window(self) -> None:
        congested = healthy_log(1000).replace(
            "tuning_generation=0",
            "tuning_generation=0 resource_blocked=1",
        )
        metrics = extract_window_metrics(congested)

        self.assertEqual(metrics.resource_blocked_workers, 21)
        self.assertFalse(metrics.healthy)
        self.assertEqual(metrics.score, float("-inf"))

    def test_repeated_spawn_price_failures_reject_candidate_window(self) -> None:
        failed = healthy_log(1000).replace(
            "HARVEST_SUCCEEDED:1",
            "HARVEST_SUCCEEDED:1,CORE_SPAWN_FAILED/INSUFFICIENT_RESOURCES:1",
        )
        metrics = extract_window_metrics(failed)

        self.assertEqual(metrics.insufficient_spawn_failures, 21)
        self.assertFalse(metrics.healthy)

    def test_overlapping_journal_is_trimmed_to_new_ticks(self) -> None:
        overlapping = "\n".join([healthy_log(1000), healthy_log(1600)])
        new_only = _logs_after_tick(overlapping, 1599)
        metrics = extract_window_metrics(new_only)
        self.assertEqual(metrics.first_tick, 1600)
        self.assertEqual(metrics.sampled_turns, 21)

    def test_candidate_allowlist_locks_beacon_policy(self) -> None:
        self.assertEqual(_candidate_from_mapping(asdict(Candidate(12))), Candidate(12))
        with self.assertRaisesRegex(ValueError, "candidate_not_allowed"):
            _candidate_from_mapping({"worker_target": 14, "beacon_policy": "pursue"})
        with self.assertRaisesRegex(ValueError, "candidate_not_allowed"):
            _candidate_from_mapping({"worker_target": 18, "beacon_policy": "retreat"})

    def test_next_candidate_only_scales_up_and_honors_blacklist(self) -> None:
        self.assertEqual(_next_candidate(Candidate(12), 100, {}), Candidate(15))
        blocked = {Candidate(15).key: 101}
        self.assertEqual(_next_candidate(Candidate(12), 100, blocked), Candidate(18))
        self.assertEqual(_next_candidate(Candidate(15), 100, {}), Candidate(18))

    def test_runtime_config_bootstraps_and_rejects_unknown_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            self.assertEqual(read_runtime_config(path), Candidate(18))
            self.assertIn("ARENA_BEACON_POLICY=pursue", path.read_text())
            path.write_text(
                "ARENA_WORKER_TARGET=14\nARENA_BEACON_POLICY=pursue\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "candidate_not_allowed"):
                read_runtime_config(path)

    def test_run_systemctl_uses_fixed_argv_without_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        run_systemctl(["is-active", "--quiet", "arena-hero-agent.service"], runner=runner)
        self.assertEqual(calls[0][0][0], "systemctl")
        self.assertNotIn("shell", calls[0][1])

    def test_agent_status_requires_normal_healthy_core(self) -> None:
        runner = SystemctlRunner(core_state="NORMAL", generation=3)
        status = read_agent_status(runner=runner)
        self.assertTrue(status.safe_to_reconfigure)
        self.assertEqual(status.generation, 3)
        moving = AgentStatus("active", "running", 1, 0, "MOVING", 0, 0, 5, 5)
        self.assertFalse(moving.safe_to_reconfigure)

    def test_apply_candidate_rolls_back_when_health_check_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.env"
            write_runtime_config(path, Candidate(12), 0)
            checked: list[int] = []

            def health(generation: int) -> bool:
                checked.append(generation)
                return generation == 0

            applied, detail = apply_candidate(
                path,
                Candidate(15),
                1,
                systemctl_runner=SystemctlRunner(),
                health_checker=health,
            )
            self.assertFalse(applied)
            self.assertEqual(detail, "apply_failed_rolled_back")
            self.assertEqual(read_runtime_config(path), Candidate(12))
            self.assertEqual(checked, [1, 0])

    def test_four_baselines_start_safe_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.env"
            state = root / "state.json"
            report = root / "latest.json"
            write_runtime_config(runtime, Candidate(12), 0)
            save_state(
                state,
                OptimizerState(
                    active=asdict(Candidate(12)),
                    last_good=asdict(Candidate(12)),
                ),
            )
            runner = SystemctlRunner()
            decisions = []
            with patch("arena_optimizer.apply_candidate", return_value=(True, "applied")):
                for index in range(4):
                    result = optimize_once(
                        runtime_path=runtime,
                        state_path=state,
                        report_path=report,
                        journal_text=healthy_log(
                            1000 + index * 600,
                            worker_target=12,
                        ),
                        now=1000 + index,
                        systemctl_runner=runner,
                    )
                    decisions.append(result["decision"])
            self.assertEqual(decisions[:3], ["baseline_collect"] * 3)
            self.assertEqual(decisions[3], "candidate_started")
            self.assertEqual(result["active"]["worker_target"], 15)

    def test_moving_core_blocks_candidate_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.env"
            state_path = root / "state.json"
            report = root / "latest.json"
            write_runtime_config(runtime, Candidate(12), 0)
            state = OptimizerState(
                active=asdict(Candidate(12)),
                last_good=asdict(Candidate(12)),
                baseline_windows=[
                    {"score": 5.0, "healthy": True},
                    {"score": 5.0, "healthy": True},
                    {"score": 5.0, "healthy": True},
                ]
            )
            save_state(state_path, state)
            result = optimize_once(
                runtime_path=runtime,
                state_path=state_path,
                report_path=report,
                journal_text=healthy_log(2000, worker_target=12),
                now=2000,
                systemctl_runner=SystemctlRunner(core_state="MOVING"),
            )
            self.assertEqual(result["decision"], "hold")
            self.assertEqual(result["detail"], "unsafe_live_status")

    def test_candidate_core_destruction_triggers_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.env"
            state_path = root / "state.json"
            report = root / "latest.json"
            write_runtime_config(runtime, Candidate(15), 1)
            state = OptimizerState(
                active=asdict(Candidate(15)),
                last_good=asdict(Candidate(12)),
                generation=1,
                candidate_started_tick=1000,
            )
            save_state(state_path, state)
            with patch("arena_optimizer.apply_candidate", return_value=(True, "applied")):
                result = optimize_once(
                    runtime_path=runtime,
                    state_path=state_path,
                    report_path=report,
                    journal_text=healthy_log(
                        2000,
                        extra_event="CORE_DESTROYED",
                        worker_target=15,
                        generation=1,
                    ),
                    now=3000,
                    systemctl_runner=SystemctlRunner(generation=1),
                )
            self.assertEqual(result["decision"], "rollback")
            self.assertEqual(result["active"]["worker_target"], 12)

    def test_candidate_promotes_only_after_measured_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.env"
            state_path = root / "state.json"
            report = root / "latest.json"
            write_runtime_config(runtime, Candidate(15), 1)
            state = OptimizerState(
                active=asdict(Candidate(15)),
                last_good=asdict(Candidate(12)),
                generation=1,
                candidate_started_tick=1000,
                warmup_windows=1,
                baseline_score=4.0,
                candidate_windows=[
                    {"score": 5.0},
                    {"score": 5.1},
                    {"score": 5.2},
                ],
            )
            save_state(state_path, state)
            result = optimize_once(
                runtime_path=runtime,
                state_path=state_path,
                report_path=report,
                journal_text=healthy_log(
                    2000,
                    deposits=2,
                    worker_target=15,
                    generation=1,
                ),
                now=3000,
                systemctl_runner=SystemctlRunner(generation=1),
            )
            self.assertEqual(result["decision"], "promote")
            self.assertEqual(result["state"]["last_good"]["worker_target"], 15)

    def test_reports_and_state_are_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime.env"
            state = root / "state.json"
            report = root / "latest.json"
            write_runtime_config(runtime, Candidate(12), 0)
            optimize_once(
                runtime_path=runtime,
                state_path=state,
                report_path=report,
                journal_text=healthy_log(1000),
                now=1000,
                systemctl_runner=SystemctlRunner(),
            )
            self.assertIsInstance(json.loads(state.read_text()), dict)
            self.assertIsInstance(json.loads(report.read_text()), dict)

    def test_compatibility_hold_does_not_create_runtime_or_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "missing-runtime.env"
            marker = root / "compatibility-hold.json"
            marker.write_text("not-json\n", encoding="utf-8")
            runner = SystemctlRunner()
            result = optimize_once(
                runtime_path=runtime,
                state_path=root / "state.json",
                report_path=root / "latest.json",
                journal_text=healthy_log(1000),
                now=1000,
                systemctl_runner=runner,
                compatibility_marker_path=marker,
            )
            self.assertEqual(result["detail"], "compatibility_uncertain")
            self.assertTrue(result["compatibility_hold"])
            self.assertFalse(runtime.exists())
            self.assertEqual(runner.calls, [])


if __name__ == "__main__":
    unittest.main()
