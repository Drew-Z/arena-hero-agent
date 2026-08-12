from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from arena_hero import Accepted, CommandPlan, PlayerState, Turn

from arena_dashboard import (
    DashboardApplication,
    DashboardServer,
    LEADERBOARD_KEYS,
    _validated_leaderboard,
)
from arena_history import (
    HistoryRecorder,
    cancel_unit_order,
    create_unit_order,
    list_ticks,
    list_unit_orders,
    read_kill_stats,
    read_overview,
)


CORE_ID = "00000000-0000-4000-8000-000000000001"
WORKER_ID = "00000000-0000-4000-8000-000000000002"
ENEMY_CORE_ID = "10000000-0000-4000-8000-000000000001"


def make_turn(
    tick: int = 41,
    *,
    enemy_position: tuple[int, int] | None = (4, 0),
    events: list[dict[str, object]] | None = None,
) -> Turn:
    objects = [
        {
            "kind": "CORE",
            "id": CORE_ID,
            "controlled": True,
            "owner_username": "commander",
            "position": [0, 0],
            "hp": 5,
            "shield": 5,
            "state": "NORMAL",
        },
        {
            "kind": "UNIT",
            "id": WORKER_ID,
            "controlled": True,
            "position": [1, 0],
            "hp": 2,
            "unit_type": "WORKER",
            "cargo": 0,
        },
        {"kind": "RESOURCE", "positions": [[2, 0]]},
        {"kind": "OBSTACLE", "positions": [[0, 2]]},
    ]
    if enemy_position is not None:
        objects.append(
            {
                "kind": "CORE",
                "id": ENEMY_CORE_ID,
                "controlled": False,
                "owner_username": "target",
                "position": list(enemy_position),
                "hp": 4,
                "shield": 1,
                "state": "NORMAL",
            }
        )
    state = PlayerState.model_validate(
        {
            "status": "ACTIVE",
            "respawn_at_tick": None,
            "resources": 37,
            "population": 1,
            "champion_beacon": {"position": [8, 3]},
            "objects": objects,
            "events": events or [],
        }
    )

    def submitter(plan: CommandPlan, _key: str | None) -> Accepted:
        return Accepted(
            accepted=True,
            tick=plan.tick,
            source="AGENT",
            received_at="2026-08-07T00:00:00Z",
        )

    return Turn(tick=tick, state=state, submitter=submitter)


class HistoryTests(unittest.TestCase):
    def test_unit_orders_are_validated_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            order = create_unit_order(
                path,
                unit_type="worker",
                unit_count=1,
                unit_ids=[WORKER_ID],
                target=(-445, 547),
            )
            self.assertEqual(order["unit_type"], "WORKER")
            self.assertEqual(order["unit_ids"], [WORKER_ID])
            self.assertEqual(list_unit_orders(path)[0]["target_x"], -445)
            with self.assertRaises(ValueError):
                create_unit_order(
                    path,
                    unit_type="WORKER",
                    unit_count=0,
                    unit_ids=[],
                    target=(0, 0),
                )
            with self.assertRaisesRegex(ValueError, "must match"):
                create_unit_order(
                    path,
                    unit_type="WORKER",
                    unit_count=1,
                    unit_ids=[],
                    target=(0, 0),
                )

    def test_pending_order_can_be_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            order = create_unit_order(
                path,
                unit_type="WORKER",
                unit_count=1,
                unit_ids=[WORKER_ID],
                target=(3, 0),
            )
            cancelled = cancel_unit_order(path, int(order["id"]))
            self.assertEqual(cancelled["status"], "CANCELLED")
            with HistoryRecorder(path) as recorder:
                self.assertEqual(recorder.active_orders(), [])

    def test_kill_stats_deduplicate_participation_events(self) -> None:
        events = [
            {
                "event_id": "20000000-0000-4000-8000-000000000001",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "UNIT",
                "position": [3, 0],
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000002",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "CORE",
                "position": [4, 0],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(events=events))
                recorder.record(make_turn(42, enemy_position=None, events=events))
            stats = read_kill_stats(path)
            self.assertEqual(stats["unit_participations"], 1)
            self.assertEqual(stats["core_participations"], 1)
            self.assertEqual(len(stats["recent"]), 2)

    def test_combat_history_records_usernames_losses_and_revenge(self) -> None:
        events = [
            {
                "event_id": "20000000-0000-4000-8000-000000000010",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "CORE",
                "target_id": ENEMY_CORE_ID,
                "position": [4, 0],
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000011",
                "tick": 41,
                "event_type": "UNIT_DAMAGED",
                "reason_code": "ATTACK",
                "target_id": WORKER_ID,
                "position": [1, 0],
                "values": {"damage": 2, "hp": 0},
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000012",
                "tick": 41,
                "event_type": "CORE_DESTROYED",
                "reason_code": "ATTACK",
                "target_id": CORE_ID,
                "position": [0, 0],
                "values": {"destroyed_by": ["rival", "other_rival"]},
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000013",
                "tick": 41,
                "event_type": "UNIT_DAMAGED",
                "reason_code": "ATTACK",
                "target_id": WORKER_ID,
                "position": [1, 0],
                "values": {"damage": 1, "hp": 1},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(events=events))
                self.assertEqual(
                    recorder.revenge_usernames(),
                    frozenset({"rival", "other_rival"}),
                )
            stats = read_kill_stats(path)
            self.assertEqual(stats["recent"][0]["username"], "target")
            self.assertEqual(stats["units_lost"], 1)
            self.assertEqual(stats["cores_lost"], 1)
            self.assertEqual(stats["attacks_received"], 3)
            self.assertEqual(stats["attacks"][0]["outcome"], "DAMAGED")
            self.assertTrue(any(loss["username"] is None for loss in stats["losses"]))
            self.assertEqual(
                stats["revenge_targets"],
                [
                    {"username": "other_rival", "score": 1},
                    {"username": "rival", "score": 1},
                ],
            )

    def test_records_and_reads_tactical_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            turn = make_turn()
            with HistoryRecorder(path) as recorder:
                recorder.record(turn, strategy={"phase": "EXPANSION"})

            ticks = list_ticks(path)
            overview = read_overview(path, tick=41)

            self.assertEqual([item["tick"] for item in ticks], [41])
            self.assertTrue(overview["available"])
            self.assertEqual(overview["strategy"]["phase"], "EXPANSION")
            self.assertIn([2, 0, 41, 41], overview["resource_history"])
            self.assertEqual(
                overview["enemy_core_history"][0]["core_id"],
                ENEMY_CORE_ID,
            )
            self.assertTrue(
                overview["enemy_core_history"][0]["currently_visible"]
            )
            self.assertEqual(overview["enemy_core_history"][0]["age_ticks"], 0)
            self.assertIn(WORKER_ID, overview["trails"])

    def test_enemy_core_history_distinguishes_live_and_last_seen_positions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41, enemy_position=(4, 0)))
                recorder.record(make_turn(42, enemy_position=None))
                recorder.record(make_turn(43, enemy_position=(5, 0)))

            hidden = read_overview(path, tick=42)["enemy_core_history"][0]
            visible = read_overview(path, tick=43)["enemy_core_history"][0]

            self.assertFalse(hidden["currently_visible"])
            self.assertEqual(hidden["last_seen_tick"], 41)
            self.assertEqual(hidden["age_ticks"], 1)
            self.assertEqual((hidden["x"], hidden["y"]), (4, 0))
            self.assertTrue(visible["currently_visible"])
            self.assertEqual(visible["age_ticks"], 0)
            self.assertEqual((visible["x"], visible["y"]), (5, 0))

    def test_history_limit_removes_old_snapshots_and_core_sightings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path, limit=2) as recorder:
                for tick in (40, 41, 42):
                    recorder.record(make_turn(tick))

            self.assertEqual([item["tick"] for item in list_ticks(path)], [41, 42])
            overview = read_overview(path, tick=40)
            self.assertFalse(overview["available"])


class DashboardTests(unittest.TestCase):
    def test_order_endpoint_accepts_coordinate_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            (static / "index.html").write_text("ok", encoding="utf-8")
            app = DashboardApplication(history_db=root / "history.sqlite3", static_root=static)
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                body = json.dumps(
                    {
                        "unit_type": "WORKER",
                        "unit_count": 1,
                        "unit_ids": [WORKER_ID],
                        "target_x": -445,
                        "target_y": 547,
                    }
                )
                connection.request(
                    "POST",
                    "/api/orders",
                    body=body,
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, 201)
                self.assertEqual(payload["target_y"], 547)

                connection.request("DELETE", f"/api/orders/{payload['id']}")
                response = connection.getresponse()
                cancelled = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertEqual(cancelled["status"], "CANCELLED")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_validates_all_leaderboard_categories(self) -> None:
        payload = {
            key: [{"rank": 1, "username": "commander", "score": 0}]
            for key in LEADERBOARD_KEYS
        }

        self.assertEqual(_validated_leaderboard(payload), payload)
        payload["damage_dealt"][0]["score"] = True
        with self.assertRaisesRegex(ValueError, "damage_dealt"):
            _validated_leaderboard(payload)

    def test_static_handler_rejects_parent_directory_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            (static / "index.html").write_text("ok", encoding="utf-8")
            (root / "secret.txt").write_text("secret", encoding="utf-8")
            app = DashboardApplication(
                history_db=root / "history.sqlite3",
                static_root=static,
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request("GET", "/../secret.txt")
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
