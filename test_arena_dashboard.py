from __future__ import annotations

import http.client
import json
import tempfile
import threading
import time
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
    read_control_config,
    save_expedition,
    save_alliance_config,
    save_production_config,
)


CORE_ID = "00000000-0000-4000-8000-000000000001"
WORKER_ID = "00000000-0000-4000-8000-000000000002"
ENEMY_CORE_ID = "10000000-0000-4000-8000-000000000001"
ENEMY_UNIT_ID = "10000000-0000-4000-8000-000000000002"


def make_turn(
    tick: int = 41,
    *,
    core_position: tuple[int, int] = (0, 0),
    enemy_position: tuple[int, int] | None = (4, 0),
    enemy_unit_position: tuple[int, int] | None = None,
    events: list[dict[str, object]] | None = None,
) -> Turn:
    core_x, core_y = core_position
    objects = [
        {
            "kind": "CORE",
            "id": CORE_ID,
            "controlled": True,
            "owner_username": "commander",
            "position": [core_x, core_y],
            "hp": 5,
            "shield": 5,
            "state": "NORMAL",
        },
        {
            "kind": "UNIT",
            "id": WORKER_ID,
            "controlled": True,
            "position": [core_x + 1, core_y],
            "hp": 2,
            "unit_type": "WORKER",
            "cargo": 0,
        },
        {"kind": "RESOURCE", "positions": [[core_x + 2, core_y]]},
        {"kind": "OBSTACLE", "positions": [[core_x, core_y + 2]]},
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
    if enemy_unit_position is not None:
        objects.append(
            {
                "kind": "UNIT",
                "id": ENEMY_UNIT_ID,
                "controlled": False,
                "position": list(enemy_unit_position),
                "hp": 2,
                "unit_type": "RANGER",
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

    def test_core_order_upgrades_existing_table_and_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            create_unit_order(
                path,
                unit_type="WORKER",
                unit_count=1,
                unit_ids=[WORKER_ID],
                target=(1, 0),
            )

            order = create_unit_order(
                path,
                unit_type="CORE",
                unit_count=1,
                unit_ids=[CORE_ID],
                target=(10, -5),
            )

            self.assertEqual(order["unit_type"], "CORE")
            self.assertEqual(order["unit_ids"], [CORE_ID])
            self.assertEqual(list_unit_orders(path)[0]["unit_type"], "CORE")
            with self.assertRaisesRegex(ValueError, "exactly one Core"):
                create_unit_order(
                    path,
                    unit_type="CORE",
                    unit_count=2,
                    unit_ids=[CORE_ID, WORKER_ID],
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

    def test_allies_are_excluded_from_enemy_and_combat_history(self) -> None:
        events = [
            {
                "event_id": "20000000-0000-4000-8000-000000000020",
                "tick": 41,
                "event_type": "DESTRUCTION_PARTICIPATION",
                "reason_code": "CORE",
                "target_id": ENEMY_CORE_ID,
                "position": [4, 0],
            },
            {
                "event_id": "20000000-0000-4000-8000-000000000021",
                "tick": 41,
                "event_type": "CORE_DESTROYED",
                "reason_code": "ATTACK",
                "target_id": CORE_ID,
                "position": [0, 0],
                "values": {"destroyed_by": ["ally"]},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(
                    make_turn(events=events),
                    allied_object_ids=[ENEMY_CORE_ID],
                    allied_usernames=["ally", "target"],
                )

            overview = read_overview(path)
            stats = read_kill_stats(path, excluded_usernames=["ally", "target"])
            self.assertEqual(overview["enemy_core_history"], [])
            self.assertEqual(stats["total_participations"], 0)
            self.assertEqual(stats["attacks_received"], 0)
            self.assertEqual(stats["revenge_targets"], [])

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

    def test_enemy_unit_history_keeps_last_seen_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(
                    make_turn(41, enemy_unit_position=(-390, 578))
                )
                recorder.record(make_turn(42))

            hidden = read_overview(path, tick=42)["enemy_unit_history"][0]

            self.assertFalse(hidden["currently_visible"])
            self.assertEqual(hidden["last_seen_tick"], 41)
            self.assertEqual(hidden["age_ticks"], 1)
            self.assertEqual(hidden["position"], [-390, 578])

    def test_destroyed_enemy_core_is_removed_from_later_history(self) -> None:
        destruction = {
            "event_id": "20000000-0000-4000-8000-000000000030",
            "tick": 42,
            "event_type": "DESTRUCTION_PARTICIPATION",
            "reason_code": "CORE",
            "target_id": ENEMY_CORE_ID,
            "position": [4, 0],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41, enemy_position=(4, 0)))
                recorder.record(
                    make_turn(42, enemy_position=None, events=[destruction])
                )

            self.assertEqual(
                read_overview(path, tick=41)["enemy_core_history"][0]["core_id"],
                ENEMY_CORE_ID,
            )
            self.assertEqual(read_overview(path, tick=42)["enemy_core_history"], [])

    def test_enemy_core_reappearing_after_destruction_is_shown_again(self) -> None:
        destruction = {
            "event_id": "20000000-0000-4000-8000-000000000031",
            "tick": 42,
            "event_type": "DESTRUCTION_PARTICIPATION",
            "reason_code": "CORE",
            "target_id": ENEMY_CORE_ID,
            "position": [4, 0],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41, enemy_position=(4, 0)))
                recorder.record(
                    make_turn(42, enemy_position=None, events=[destruction])
                )
                recorder.record(make_turn(43, enemy_position=(8, 0)))

            history = read_overview(path, tick=43)["enemy_core_history"]
            self.assertEqual(len(history), 1)
            self.assertEqual((history[0]["x"], history[0]["y"]), (8, 0))

    def test_overview_can_return_only_new_map_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            with HistoryRecorder(path) as recorder:
                recorder.record(make_turn(41))
                recorder.record(make_turn(42))

            delta = read_overview(path, since_tick=41)
            state_only = read_overview(path, include_history=False)

            self.assertTrue(delta["history_delta"])
            self.assertEqual(delta["explored"], [])
            self.assertEqual(delta["obstacles"], [])
            self.assertEqual(delta["resource_history"], [])
            self.assertEqual(state_only["state"]["resources"], 37)
            self.assertEqual(state_only["explored"], [])

    def test_control_config_persists_production_and_expedition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            save_production_config(
                path,
                worker_weight=4,
                vanguard_weight=1,
                ranger_weight=2,
            )
            save_expedition(
                path,
                expedition_id=None,
                name="strike-1",
                ranger_count=2,
                vanguard_count=2,
                target=(12, -8),
                enabled=True,
            )
            save_alliance_config(path, rally_enabled=True, rally_radius=24)

            config = read_control_config(path)

            self.assertEqual(config["production"]["ranger_weight"], 2)
            self.assertTrue(config["alliance"]["rally_enabled"])
            self.assertEqual(config["alliance"]["rally_radius"], 24)
            self.assertEqual(config["expeditions"][0]["name"], "strike-1")
            self.assertTrue(config["expeditions"][0]["enabled"])

    def test_alliance_config_defaults_to_twelve_and_validates_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            alliance = read_control_config(path)["alliance"]
            self.assertFalse(alliance["rally_enabled"])
            self.assertEqual(alliance["rally_radius"], 12)
            with self.assertRaisesRegex(ValueError, "between 1 and 256"):
                save_alliance_config(path, rally_enabled=False, rally_radius=0)
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                save_alliance_config(path, rally_enabled=1, rally_radius=12)

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
    def test_dual_account_overview_merges_vision_without_changing_primary_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary_db = root / "primary.sqlite3"
            secondary_db = root / "secondary.sqlite3"
            with HistoryRecorder(primary_db) as recorder:
                recorder.record(make_turn(enemy_position=None))
            with HistoryRecorder(secondary_db) as recorder:
                recorder.record(
                    make_turn(
                        core_position=(100, 100),
                        enemy_position=(104, 100),
                    )
                )
            app = DashboardApplication(
                history_db=primary_db,
                static_root=Path(__file__).with_name("dashboard"),
                allied_history_dbs=(secondary_db,),
            )

            overview = app.overview()
            explored = {(item[0], item[1]) for item in overview["explored"]}
            current_ids = {
                item.get("id")
                for item in overview["state"]["objects"]
                if isinstance(item, dict)
            }

            self.assertIn((0, 0), explored)
            self.assertIn((100, 100), explored)
            self.assertIn(ENEMY_CORE_ID, current_ids)
            self.assertEqual(overview["state"]["population"], 1)
            self.assertEqual(
                overview["accounts"],
                [
                    {
                        "role": "primary",
                        "username": "commander",
                        "tick": 41,
                        "resources": 37,
                        "population": 1,
                        "workers": 1,
                        "vanguards": 0,
                        "rangers": 0,
                        "core_position": [0, 0],
                    },
                    {
                        "role": "secondary",
                        "username": "commander",
                        "tick": 41,
                        "resources": 37,
                        "population": 1,
                        "workers": 1,
                        "vanguards": 0,
                        "rangers": 0,
                        "core_position": [100, 100],
                    },
                ],
            )

    def test_single_account_overview_has_one_account_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.sqlite3"
            with HistoryRecorder(history) as recorder:
                recorder.record(make_turn())
            app = DashboardApplication(
                history_db=history,
                static_root=Path(__file__).with_name("dashboard"),
            )

            accounts = app.overview()["accounts"]

            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["role"], "primary")
            self.assertEqual(accounts[0]["resources"], 37)
            self.assertEqual(accounts[0]["workers"], 1)

    def test_dashboard_renders_account_status_and_core_location_control(self) -> None:
        dashboard_root = Path(__file__).with_name("dashboard")
        html = (dashboard_root / "index.html").read_text(encoding="utf-8")
        script = (dashboard_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="account-status"', html)
        self.assertIn("function renderAccountStatus(accounts)", script)
        self.assertIn("centerMapAt(account.core_position)", script)

    def test_dashboard_exposes_alliance_rally_toggle(self) -> None:
        dashboard_root = Path(__file__).with_name("dashboard")
        html = (dashboard_root / "index.html").read_text(encoding="utf-8")
        script = (dashboard_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="alliance-rally-enabled"', html)
        self.assertIn('rally_enabled: document.querySelector', script)

    def test_windows_launcher_uses_lightweight_dashboard_healthcheck(self) -> None:
        launcher = Path(__file__).with_name("start_agent.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('api/overview?history=0', launcher)

    def test_map_target_can_be_hidden_without_reloading(self) -> None:
        script = (
            Path(__file__).with_name("dashboard") / "app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function clearMapTarget()", script)
        self.assertIn("state.orderTarget = null;", script)
        self.assertIn('"隐藏地图选点"', script)

    def test_dispatch_ui_supports_all_and_core_distance_selection(self) -> None:
        dashboard_root = Path(__file__).with_name("dashboard")
        html = (dashboard_root / "index.html").read_text(encoding="utf-8")
        script = (dashboard_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('value="DISTANT">远离 Core X 格', html)
        self.assertIn('value="ALL">全部', html)
        self.assertIn('id="order-min-distance"', html)
        self.assertIn('coreDistance >= minDistance', script)
        self.assertIn('selectionMode === "ALL"', script)

    def test_alliance_objects_exclude_local_account_and_reject_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alliance = root / "alliance"
            alliance.mkdir()
            peer = {
                "account_id": "account-2",
                "username": "ally",
                "updated_at": time.time(),
                "core_id": ENEMY_CORE_ID,
                "core_position": [7, 8],
                "units": [
                    {
                        "id": WORKER_ID,
                        "position": [6, 8],
                        "unit_type": "WORKER",
                        "hp": 2,
                        "cargo": 1,
                    }
                ],
            }
            (alliance / "account-2.json").write_text(json.dumps(peer), encoding="utf-8")
            (alliance / "account-1.json").write_text(
                json.dumps({**peer, "account_id": "account-1"}),
                encoding="utf-8",
            )
            (alliance / "account-3.json").write_text(
                json.dumps({**peer, "account_id": "account-3", "updated_at": 1}),
                encoding="utf-8",
            )
            static = root / "dashboard"
            static.mkdir()
            app = DashboardApplication(
                history_db=root / "history.sqlite3",
                static_root=static,
                alliance_directory=alliance,
                alliance_account_id="account-1",
                alliance_stale_seconds=60,
            )

            objects = app.alliance_objects()

            self.assertEqual([item["kind"] for item in objects], ["CORE", "UNIT"])
            self.assertTrue(all(item["alliance_account_id"] == "account-2" for item in objects))
            self.assertEqual(objects[0]["position"], [7, 8])

    def test_overview_excludes_allies_from_enemy_count_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = root / "history.sqlite3"
            with HistoryRecorder(history) as recorder:
                recorder.record(make_turn())
            alliance = root / "alliance"
            alliance.mkdir()
            (alliance / "account-2.json").write_text(
                json.dumps(
                    {
                        "account_id": "account-2",
                        "username": "target",
                        "updated_at": time.time(),
                        "core_id": ENEMY_CORE_ID,
                        "core_position": [4, 0],
                        "units": [],
                    }
                ),
                encoding="utf-8",
            )
            static = root / "dashboard"
            static.mkdir()
            app = DashboardApplication(
                history_db=history,
                static_root=static,
                alliance_directory=alliance,
                alliance_account_id="account-1",
            )

            overview = app.overview()

            allied_core = next(
                item
                for item in overview["state"]["objects"]
                if item.get("id") == ENEMY_CORE_ID
            )
            self.assertEqual(allied_core["relation"], "ALLY")
            self.assertEqual(overview["enemy_count"], 0)
            self.assertEqual(overview["enemy_core_history"], [])

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

    def test_alliance_config_endpoint_updates_all_account_databases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "dashboard"
            static.mkdir()
            app = DashboardApplication(
                history_db=root / "history.sqlite3",
                static_root=static,
                allied_history_dbs=(root / "secondary.sqlite3",),
            )
            server = DashboardServer(("127.0.0.1", 0), app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(
                    "POST",
                    "/api/alliance-config",
                    body=json.dumps({"rally_enabled": True, "rally_radius": 24}),
                    headers={"Content-Type": "application/json"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())

                self.assertEqual(response.status, 201)
                self.assertTrue(payload["rally_enabled"])
                self.assertEqual(payload["rally_radius"], 24)
                for name in ("history.sqlite3", "secondary.sqlite3"):
                    alliance = read_control_config(root / name)["alliance"]
                    self.assertTrue(alliance["rally_enabled"])
                    self.assertEqual(alliance["rally_radius"], 24)
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
