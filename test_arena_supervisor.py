from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from arena_supervisor import (
    AnalysisError,
    Attempt,
    JournalSnapshot,
    Metrics,
    ModelResult,
    _request_analysis,
    analyze_with_fallback,
    atomic_write_json,
    build_report,
    assess_deterministic,
    extract_metrics,
    llm_trigger_reasons,
    output_text,
    read_compatibility_marker,
    read_journal,
    run_supervisor,
    truncate_utf8_tail,
    validate_analysis,
    write_reports,
)


VALID_ANALYSIS = {
    "status": "watch",
    "summary": "Collection slowed in the recent sample.",
    "signals": ["Cargo remained unchanged."],
    "recommendations": ["Inspect resource target selection."],
    "requires_human": False,
}


class FakeResponse:
    def __init__(self, status_code: int, data: object):
        self.status_code = status_code
        self._data = data

    def json(self) -> object:
        if isinstance(self._data, Exception):
            raise self._data
        return self._data


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def post(self, url: str, *, json: dict[str, object]) -> FakeResponse:
        self.calls.append((url, json))
        return self.response


class SequenceClient:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = iter(responses)
        self.models: list[str] = []

    def __enter__(self) -> "SequenceClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, object]) -> FakeResponse:
        self.models.append(str(json["model"]))
        return next(self.responses)


class SupervisorTests(unittest.TestCase):
    @staticmethod
    def combat_pressure_log(samples: int = 4) -> str:
        return "\n".join(
            (
                f"tick={tick} accepted=True resources=23/95 workers=12 cargo=0 "
                "visible_resources=0 known_resources=0 recovery=0 "
                "danger_cells=4 combat_pressure=1 core_hp=5 core_shield=5 "
                "actions=MOVE:1 events=UNIT_MOVE_SUCCEEDED:1"
            )
            for tick in range(100, 100 + samples)
        )

    def test_truncate_utf8_tail_preserves_complete_lines(self) -> None:
        raw = "old\n中间\nlatest\n".encode()
        text, truncated = truncate_utf8_tail(raw, 12)
        self.assertTrue(truncated)
        self.assertEqual(text, "latest\n")

    def test_truncate_utf8_tail_zero(self) -> None:
        self.assertEqual(truncate_utf8_tail(b"abc", 0), ("", True))

    def test_read_journal_uses_fixed_unit_without_shell(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        invocation_id = "c5d4a4097fe546a589e4ab9bdf259d72"

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            calls.append((argv, kwargs))
            if argv[0] == "systemctl":
                return subprocess.CompletedProcess(
                    argv, 0, f"{invocation_id}\n".encode(), b""
                )
            return subprocess.CompletedProcess(argv, 0, b"tick=1 accepted=True\n", b"")

        snapshot = read_journal(runner=runner)
        self.assertEqual(
            calls[0][0],
            [
                "systemctl",
                "show",
                "arena-hero-agent.service",
                "--property=InvocationID",
                "--value",
            ],
        )
        self.assertIn("--unit=arena-hero-agent.service", calls[1][0])
        self.assertIn(f"_SYSTEMD_INVOCATION_ID={invocation_id}", calls[1][0])
        self.assertTrue(all("shell" not in kwargs for _, kwargs in calls))
        self.assertIsNone(snapshot.error)
        self.assertEqual(snapshot.invocation_id, invocation_id)

    def test_read_journal_nonzero_is_reportable(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if argv[0] == "systemctl":
                return subprocess.CompletedProcess(
                    argv, 0, b"c5d4a4097fe546a589e4ab9bdf259d72\n", b""
                )
            return subprocess.CompletedProcess(argv, 1, b"", b"denied")

        self.assertEqual(read_journal(runner=runner).error, "journal_exit_1")

    def test_read_journal_invalid_invocation_fails_closed(self) -> None:
        calls: list[list[str]] = []

        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, b"not-an-id\n", b"")

        snapshot = read_journal(runner=runner)
        self.assertEqual(snapshot.error, "invocation_invalid")
        self.assertEqual(snapshot.text, "")
        self.assertEqual(len(calls), 1)

    def test_read_journal_invocation_lookup_failure_is_reportable(self) -> None:
        def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            return subprocess.CompletedProcess(argv, 4, b"", b"not found")

        self.assertEqual(read_journal(runner=runner).error, "invocation_exit_4")

    def test_extract_metrics_handles_old_and_new_logs(self) -> None:
        log = "\n".join(
            [
                "tick=33374 accepted=True resources=6 workers=3",
                (
                    "2026-08-02T01:00:00+00:00 host arena-hero-agent[1]: "
                    "tick=33446 accepted=True resources=12/40 workers=8 cargo=2 "
                    "vanguards=2 rangers=2 visible_resources=3 "
                    "actions=HARVEST:2,MOVE:6 "
                    "events=HARVEST_SUCCEEDED:2 recovery=0 core=9:-179 "
                    "beacon_distance=200 known_resources=4 danger_cells=1 "
                    "combat_pressure=1 resource_blocked=12 scout_chunks=7 "
                    "scout_oldest_age=31 projected_core_damage=2 "
                    "core_survival_margin=3 upkeep_due=1 upkeep_paid=1 "
                    "upkeep_deficit=0 upkeep_damage=0 "
                    "phase=STOCKPILE core_hp=5 core_shield=4"
                ),
                "WARNING tick=33447 manual_override unit_actions=1 core_actions=0",
            ]
        )
        metrics = extract_metrics(log)
        self.assertEqual(metrics.sampled_turns, 2)
        self.assertEqual(metrics.first_tick, 33374)
        self.assertEqual(metrics.latest_tick, 33446)
        self.assertEqual(metrics.first_resources, 6)
        self.assertEqual(metrics.latest_resources, 12)
        self.assertEqual(metrics.resource_delta, 6)
        self.assertEqual(metrics.latest_capacity, 40)
        self.assertEqual(metrics.latest_workers, 8)
        self.assertEqual(metrics.latest_vanguards, 2)
        self.assertEqual(metrics.latest_rangers, 2)
        self.assertEqual(metrics.latest_phase, "STOCKPILE")
        self.assertEqual(metrics.latest_core_hp, 5)
        self.assertEqual(metrics.latest_core_shield, 4)
        self.assertEqual(metrics.latest_core_position, [9, -179])
        self.assertEqual(metrics.max_danger_cells, 1)
        self.assertEqual(metrics.combat_pressure_samples, 1)
        self.assertEqual(metrics.latest_resource_blocked, 12)
        self.assertEqual(metrics.resource_blocked_workers, 12)
        self.assertEqual(metrics.latest_scout_chunks, 7)
        self.assertEqual(metrics.latest_scout_oldest_age, 31)
        self.assertEqual(metrics.latest_projected_core_damage, 2)
        self.assertEqual(metrics.latest_core_survival_margin, 3)
        self.assertEqual(metrics.min_core_survival_margin, 3)
        self.assertEqual(metrics.critical_core_margin_samples, 0)
        self.assertEqual(metrics.latest_upkeep_due, 1)
        self.assertEqual(metrics.latest_upkeep_paid, 1)
        self.assertEqual(metrics.latest_upkeep_deficit, 0)
        self.assertEqual(metrics.latest_upkeep_damage, 0)
        self.assertEqual(metrics.upkeep_deficit_samples, 0)
        self.assertEqual(metrics.upkeep_damage_samples, 0)
        self.assertEqual(metrics.last_harvest_tick, 33446)
        self.assertEqual(metrics.ticks_since_harvest, 0)
        self.assertEqual(metrics.action_counts, {"HARVEST": 2, "MOVE": 6})
        self.assertEqual(metrics.warning_counts, {"manual_override": 1})

    def test_unexplained_resource_loss_is_parsed_outside_turn_lines(self) -> None:
        metrics = extract_metrics(
            "\n".join(
                [
                    "tick=100 accepted=True resources=31/95 workers=12 core_hp=5 core_shield=5",
                    "WARNING unexplained_resource_loss tick=101 resources=31->1 unexplained_loss=30",
                    "tick=101 accepted=True resources=1/95 workers=12 core_hp=5 core_shield=5",
                ]
            )
        )

        self.assertEqual(metrics.unexplained_resource_loss, 30)
        self.assertEqual(metrics.warning_counts["unexplained_resource_loss"], 1)

    def test_negative_projected_core_margin_is_counted(self) -> None:
        metrics = extract_metrics(
            "tick=101 accepted=True resources=5/10 workers=1 "
            "projected_core_damage=6 core_survival_margin=-1 "
            "core_hp=5 core_shield=0"
        )

        self.assertEqual(metrics.latest_projected_core_damage, 6)
        self.assertEqual(metrics.latest_core_survival_margin, -1)
        self.assertEqual(metrics.min_core_survival_margin, -1)
        self.assertEqual(metrics.critical_core_margin_samples, 1)

    def test_output_text_supports_both_response_shapes(self) -> None:
        self.assertEqual(output_text({"output_text": "direct"}), "direct")
        nested = {
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "one"},
                        {"type": "refusal", "refusal": "no"},
                    ]
                },
                {"content": [{"type": "output_text", "text": "two"}]},
            ]
        }
        self.assertEqual(output_text(nested), "one\ntwo")

    def test_validate_analysis_rejects_extra_execution_fields(self) -> None:
        candidate = {**VALID_ANALYSIS, "command": "systemctl restart arena-hero-agent"}
        with self.assertRaisesRegex(AnalysisError, "analysis_fields_invalid"):
            validate_analysis(candidate)

    def test_validate_analysis_rejects_bad_status_and_item_types(self) -> None:
        with self.assertRaisesRegex(AnalysisError, "status_invalid"):
            validate_analysis({**VALID_ANALYSIS, "status": "degraded"})
        with self.assertRaisesRegex(AnalysisError, "signals_item_invalid"):
            validate_analysis({**VALID_ANALYSIS, "signals": [1]})

    def test_request_analysis_uses_verified_responses_payload(self) -> None:
        response_data = {
            "model": "resolved-model",
            "output_text": json.dumps(VALID_ANALYSIS),
        }
        client = FakeClient(FakeResponse(200, response_data))
        analysis, resolved = _request_analysis(
            client=client, base_url="https://example.test/v1", model="requested", prompt="log"
        )
        self.assertEqual(analysis, VALID_ANALYSIS)
        self.assertEqual(resolved, "resolved-model")
        payload = client.calls[0][1]
        self.assertEqual(payload["model"], "requested")
        self.assertIs(payload["store"], False)
        self.assertIs(payload["stream"], False)
        self.assertNotIn("tools", payload)

    def test_request_analysis_rejects_markdown_fence(self) -> None:
        client = FakeClient(
            FakeResponse(200, {"output_text": "```json\n{}\n```"})
        )
        with self.assertRaisesRegex(AnalysisError, "output_json_invalid"):
            _request_analysis(
                client=client, base_url="https://example.test/v1", model="m", prompt="log"
            )

    def test_analyze_with_fallback_stops_after_first_valid_model(self) -> None:
        client = SequenceClient(
            [
                FakeResponse(503, {}),
                FakeResponse(200, {"output_text": "not-json"}),
                FakeResponse(
                    200,
                    {
                        "model": "resolved-third",
                        "output_text": json.dumps(VALID_ANALYSIS),
                    },
                ),
            ]
        )
        result = analyze_with_fallback(
            base_url="https://example.test/v1",
            api_key="secret",
            models=("first", "second", "third", "unused"),
            prompt="log",
            client_factory=lambda **kwargs: client,
        )
        self.assertEqual(client.models, ["first", "second", "third"])
        self.assertEqual(result.requested_model, "third")
        self.assertEqual(result.resolved_model, "resolved-third")
        self.assertEqual([attempt.outcome for attempt in result.attempts], ["failed", "failed", "succeeded"])

    def test_build_report_is_deterministic_when_models_fail(self) -> None:
        report = build_report(
            snapshot=JournalSnapshot("", 0, False, "journal_exit_1"),
            metrics=Metrics(latest_tick=7),
            result=ModelResult(None, None, None, [Attempt("m", "failed", 12, "http_503")]),
            generated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        self.assertEqual(report["status"], "critical")
        self.assertTrue(report["requires_human"])
        self.assertEqual(report["metrics"]["latest_tick"], 7)
        self.assertNotIn("api_key", json.dumps(report).lower())

    def test_deterministic_core_destruction_overrides_healthy_model(self) -> None:
        report = build_report(
            snapshot=JournalSnapshot("", 0, False),
            metrics=Metrics(
                sampled_turns=10,
                event_counts={"CORE_DESTROYED": 1},
            ),
            result=ModelResult(VALID_ANALYSIS | {"status": "healthy"}, "m", "m", []),
            generated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        self.assertEqual(report["status"], "critical")
        self.assertTrue(report["requires_human"])

    def test_recent_combat_pressure_forces_watch_after_danger_clears(self) -> None:
        report = build_report(
            snapshot=JournalSnapshot("", 1, False),
            metrics=Metrics(
                sampled_turns=10,
                latest_danger_cells=0,
                max_danger_cells=4,
                latest_combat_pressure=0,
                combat_pressure_samples=3,
            ),
            result=ModelResult(VALID_ANALYSIS | {"status": "healthy"}, "m", "m", []),
            generated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        self.assertEqual(report["status"], "watch")
        self.assertTrue(any("combat pressure" in signal for signal in report["signals"]))

    def test_long_harvest_stall_forces_watch(self) -> None:
        report = build_report(
            snapshot=JournalSnapshot("", 1, False),
            metrics=Metrics(
                sampled_turns=8,
                first_tick=100,
                latest_tick=200,
                latest_cargo=0,
                latest_known_resources=0,
                latest_visible_resources=0,
            ),
            result=ModelResult(VALID_ANALYSIS | {"status": "healthy"}, "m", "m", []),
            generated_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        self.assertEqual(report["status"], "watch")
        self.assertTrue(any("No harvesting" in signal for signal in report["signals"]))

    def test_llm_triggers_only_for_sustained_combat_pressure(self) -> None:
        snapshot = JournalSnapshot(self.combat_pressure_log(), 500, False)
        metrics = extract_metrics(snapshot.text)
        compatibility = {"hold": False, "status": "compatible"}

        self.assertEqual(
            llm_trigger_reasons(snapshot, metrics, compatibility),
            ("sustained_combat_pressure",),
        )
        assessment = assess_deterministic(snapshot, metrics, compatibility)
        self.assertEqual(assessment.status, "watch")

    def test_short_danger_window_does_not_trigger_llm(self) -> None:
        snapshot = JournalSnapshot(self.combat_pressure_log(samples=1), 200, False)
        metrics = extract_metrics(snapshot.text)
        compatibility = {"hold": False, "status": "compatible"}

        self.assertEqual(llm_trigger_reasons(snapshot, metrics, compatibility), ())
        self.assertEqual(
            assess_deterministic(snapshot, metrics, compatibility).status,
            "watch",
        )

    def test_unexplained_resource_loss_immediately_triggers_model_review(self) -> None:
        metrics = Metrics(
            sampled_turns=1,
            first_tick=101,
            latest_tick=101,
            latest_core_hp=5,
            unexplained_resource_loss=30,
            warning_counts={"unexplained_resource_loss": 1},
        )
        snapshot = JournalSnapshot("warning", 7, False)

        assessment = assess_deterministic(snapshot, metrics, {"hold": False})
        reasons = llm_trigger_reasons(snapshot, metrics, {"hold": False})

        self.assertEqual(assessment.status, "watch")
        self.assertTrue(assessment.requires_human)
        self.assertIn("unexplained_resource_loss", reasons)

    def test_upkeep_deficit_triggers_watch_and_model_review(self) -> None:
        snapshot = JournalSnapshot(
            "tick=101 accepted=True resources=0/100 workers=12 "
            "vanguards=4 rangers=4 events=UNIT_DAMAGED/UPKEEP_DEFICIT:1,UPKEEP_PAID:1 "
            "upkeep_due=1 upkeep_paid=0 upkeep_deficit=1 upkeep_damage=1",
            180,
            False,
        )
        metrics = extract_metrics(snapshot.text)

        assessment = assess_deterministic(snapshot, metrics, {"hold": False})
        reasons = llm_trigger_reasons(snapshot, metrics, {"hold": False})

        self.assertEqual(metrics.upkeep_deficit_samples, 1)
        self.assertEqual(metrics.upkeep_deficit_total, 1)
        self.assertEqual(metrics.upkeep_damage_samples, 1)
        self.assertEqual(metrics.upkeep_damage_total, 1)
        self.assertEqual(assessment.status, "watch")
        self.assertTrue(assessment.requires_human)
        self.assertIn("upkeep_deficit", reasons)
        self.assertIn("upkeep_unit_damage", reasons)

    def test_fully_paid_upkeep_does_not_trigger_review(self) -> None:
        snapshot = JournalSnapshot(
            "tick=101 accepted=True resources=9/100 workers=12 "
            "vanguards=4 rangers=4 events=UPKEEP_PAID:1 "
            "upkeep_due=1 upkeep_paid=1 upkeep_deficit=0 upkeep_damage=0",
            150,
            False,
        )
        metrics = extract_metrics(snapshot.text)

        assessment = assess_deterministic(snapshot, metrics, {"hold": False})
        reasons = llm_trigger_reasons(snapshot, metrics, {"hold": False})

        self.assertEqual(metrics.upkeep_deficit_samples, 0)
        self.assertEqual(metrics.upkeep_damage_samples, 0)
        self.assertEqual(assessment.status, "healthy")
        self.assertFalse(assessment.requires_human)
        self.assertEqual(reasons, ())

    def test_critical_projected_core_margin_triggers_model_review(self) -> None:
        metrics = Metrics(
            sampled_turns=1,
            first_tick=101,
            latest_tick=101,
            latest_core_hp=5,
            latest_projected_core_damage=4,
            latest_core_survival_margin=1,
            min_core_survival_margin=1,
            critical_core_margin_samples=1,
        )
        snapshot = JournalSnapshot("turn", 4, False)

        assessment = assess_deterministic(snapshot, metrics, {"hold": False})
        reasons = llm_trigger_reasons(snapshot, metrics, {"hold": False})

        self.assertEqual(assessment.status, "watch")
        self.assertIn("critical_projected_core_margin", reasons)

    def test_persistent_resource_block_triggers_model_review(self) -> None:
        metrics = Metrics(
            sampled_turns=1,
            first_tick=101,
            latest_tick=101,
            latest_core_hp=5,
            resource_blocked_workers=12,
            event_counts={},
        )
        snapshot = JournalSnapshot("turn", 4, False)

        assessment = assess_deterministic(snapshot, metrics, {"hold": False})
        reasons = llm_trigger_reasons(snapshot, metrics, {"hold": False})

        self.assertEqual(assessment.status, "watch")
        self.assertIn("persistent_resource_block", reasons)

    def test_economic_stall_triggers_llm_review(self) -> None:
        snapshot = JournalSnapshot("", 1, False)
        metrics = Metrics(
            sampled_turns=8,
            first_tick=100,
            latest_tick=240,
            resource_delta=0,
            latest_cargo=0,
            event_counts={},
        )
        reasons = llm_trigger_reasons(
            snapshot,
            metrics,
            {"hold": False, "status": "compatible"},
        )
        self.assertEqual(reasons, ("prolonged_economic_stall",))

    def test_core_migration_deposit_oscillation_triggers_llm_review(self) -> None:
        snapshot = JournalSnapshot("", 1, False)
        metrics = Metrics(
            sampled_turns=20,
            first_tick=100,
            latest_tick=140,
            event_counts={
                "CORE_MOVE_CANCELLED": 6,
                "DEPOSIT_FAILED/CORE_MOVING": 4,
            },
        )
        reasons = llm_trigger_reasons(
            snapshot,
            metrics,
            {"hold": False, "status": "compatible"},
        )
        self.assertEqual(reasons, ("core_migration_deposit_oscillation",))

    def test_compatibility_hold_forces_watch_and_human_review(self) -> None:
        report = build_report(
            snapshot=JournalSnapshot("", 1, False),
            metrics=Metrics(sampled_turns=10),
            result=ModelResult(VALID_ANALYSIS | {"status": "healthy"}, "m", "m", []),
            generated_at=datetime(2026, 8, 2, tzinfo=UTC),
            compatibility={
                "hold": True,
                "status": "incompatible",
                "reasons": ["gameplay_changed"],
            },
        )
        self.assertEqual(report["status"], "watch")
        self.assertTrue(report["requires_human"])
        self.assertTrue(report["compatibility"]["hold"])

    def test_invalid_compatibility_marker_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "hold.json"
            marker.write_text("not-json\n", encoding="utf-8")
            status = read_compatibility_marker(marker)
        self.assertTrue(status["hold"])
        self.assertEqual(status["status"], "marker_invalid")

    def test_supervisor_writes_metrics_without_ai_credentials(self) -> None:
        snapshot = JournalSnapshot(
            "tick=9 accepted=True resources=5/10 workers=1 recovery=0 danger_cells=0",
            70,
            False,
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("arena_supervisor.read_journal", return_value=snapshot),
            ):
                report, latest = run_supervisor(Path(directory))
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["analysis_source"], "deterministic")
        self.assertFalse(report["model_review"]["required"])
        self.assertEqual(report["model_review"]["outcome"], "not_required")
        self.assertEqual(report["model_attempts"][0]["detail"], "no_model_trigger")
        self.assertEqual(latest.name, "latest.json")

    def test_supervisor_skips_model_for_normal_window_with_credentials(self) -> None:
        snapshot = JournalSnapshot(
            "tick=9 accepted=True resources=5/10 workers=1 recovery=0 danger_cells=0",
            70,
            False,
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {
                        "ARENA_SUPERVISOR_AI_ENABLED": "true",
                        "ARENA_SUPERVISOR_AI_BASE_URL": "https://example.test/v1",
                        "ARENA_SUPERVISOR_AI_API_KEY": "secret",
                        "ARENA_SUPERVISOR_MODELS": "model-a",
                    },
                    clear=True,
                ),
                patch("arena_supervisor.read_journal", return_value=snapshot),
                patch("arena_supervisor.analyze_with_fallback") as analyze,
            ):
                report, _ = run_supervisor(Path(directory))

        analyze.assert_not_called()
        self.assertEqual(report["analysis_source"], "deterministic")
        self.assertFalse(report["model_review"]["triggered"])

    def test_supervisor_calls_model_for_sustained_combat_pressure(self) -> None:
        snapshot = JournalSnapshot(self.combat_pressure_log(), 500, False)
        model_result = ModelResult(
            VALID_ANALYSIS,
            "model-a",
            "model-a",
            [Attempt("model-a", "succeeded", 10)],
        )
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {
                        "ARENA_SUPERVISOR_AI_ENABLED": "true",
                        "ARENA_SUPERVISOR_AI_BASE_URL": "https://example.test/v1",
                        "ARENA_SUPERVISOR_AI_API_KEY": "secret",
                        "ARENA_SUPERVISOR_MODELS": "model-a",
                    },
                    clear=True,
                ),
                patch("arena_supervisor.read_journal", return_value=snapshot),
                patch(
                    "arena_supervisor.analyze_with_fallback",
                    return_value=model_result,
                ) as analyze,
            ):
                report, _ = run_supervisor(Path(directory))

        analyze.assert_called_once()
        self.assertEqual(report["analysis_source"], "model")
        self.assertTrue(report["model_review"]["required"])
        self.assertTrue(report["model_review"]["triggered"])
        self.assertEqual(
            report["model_review"]["reasons"],
            ["sustained_combat_pressure"],
        )

    def test_anomaly_without_credentials_keeps_deterministic_watch(self) -> None:
        snapshot = JournalSnapshot(self.combat_pressure_log(), 500, False)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("arena_supervisor.read_journal", return_value=snapshot),
            ):
                report, _ = run_supervisor(Path(directory))

        self.assertEqual(report["status"], "watch")
        self.assertEqual(report["analysis_source"], "deterministic")
        self.assertTrue(report["requires_human"])
        self.assertEqual(report["model_review"]["outcome"], "disabled")
        self.assertEqual(report["model_attempts"][0]["detail"], "ai_disabled")

    def test_anomaly_with_credentials_stays_disabled_without_opt_in(self) -> None:
        snapshot = JournalSnapshot(self.combat_pressure_log(), 500, False)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {
                        "ARENA_SUPERVISOR_AI_BASE_URL": "https://example.test/v1",
                        "ARENA_SUPERVISOR_AI_API_KEY": "secret",
                        "ARENA_SUPERVISOR_MODELS": "model-a",
                    },
                    clear=True,
                ),
                patch("arena_supervisor.read_journal", return_value=snapshot),
                patch("arena_supervisor.analyze_with_fallback") as analyze,
            ):
                report, _ = run_supervisor(Path(directory))

        analyze.assert_not_called()
        self.assertEqual(report["analysis_source"], "deterministic")
        self.assertEqual(report["model_review"]["outcome"], "disabled")
        self.assertEqual(report["model_attempts"][0]["detail"], "ai_disabled")

    def test_enabled_anomaly_without_complete_model_config_is_not_configured(self) -> None:
        snapshot = JournalSnapshot(self.combat_pressure_log(), 500, False)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {"ARENA_SUPERVISOR_AI_ENABLED": "true"},
                    clear=True,
                ),
                patch("arena_supervisor.read_journal", return_value=snapshot),
            ):
                report, _ = run_supervisor(Path(directory))

        self.assertEqual(report["analysis_source"], "deterministic")
        self.assertEqual(report["model_review"]["outcome"], "not_configured")
        self.assertEqual(report["model_attempts"][0]["detail"], "ai_not_configured")

    def test_invalid_ai_enabled_value_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"ARENA_SUPERVISOR_AI_ENABLED": "sometimes"},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "must be true or false"):
                    run_supervisor(Path(directory))

    def test_atomic_write_preserves_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "latest.json"
            target.write_text('{"old":true}\n', encoding="utf-8")
            atomic_write_json(target, {"new": "中文"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"new": "中文"})
            self.assertEqual(list(target.parent.glob(".latest.json.*")), [])

    def test_write_reports_prunes_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            for second in range(3):
                report = {"generated_at": f"2026-08-02T00:00:0{second}Z", "value": second}
                write_reports(output_dir, report, history_limit=2)
            files = sorted((output_dir / "history").glob("*.json"))
            self.assertEqual(len(files), 2)
            self.assertEqual(json.loads((output_dir / "latest.json").read_text())["value"], 2)


if __name__ == "__main__":
    unittest.main()
