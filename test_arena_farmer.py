from __future__ import annotations

import os
import io
import tempfile
import threading
import unittest
from collections import deque
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from arena_hero import (
    Accepted,
    CommandPlan,
    Direction,
    PlayerState,
    Received,
    Turn,
    UnitType,
    unit_cost,
)

from arena_farmer import (
    AllianceCoordinator,
    CoreRaidTarget,
    CoreFarmer,
    GlobalPosture,
    LifecycleMode,
    ResourceLedgerSnapshot,
    ThreatLevel,
    _emit_resource_ledger,
    _core_guard_ids,
    _core_reserve_ids,
    _enemy_threat_cells,
    _is_turn_scoped_api_error,
    _manual_override_summary,
    _next_force_unit_type,
    _notify_systemd,
    _path_directions,
    _position_diagnostics,
    _ranger_can_shoot,
    _reconcile_resource_turn,
    _should_log_turn,
    _systemd_status,
    build_parser,
    load_api_key,
    play,
)


CORE_ID = "00000000-0000-4000-8000-000000000001"
WORKER_1 = "00000000-0000-4000-8000-000000000002"
WORKER_2 = "00000000-0000-4000-8000-000000000003"
WORKER_3 = "00000000-0000-4000-8000-000000000006"
WORKER_4 = "00000000-0000-4000-8000-000000000007"
WORKER_5 = "00000000-0000-4000-8000-000000000008"
WORKER_6 = "00000000-0000-4000-8000-000000000009"
WORKER_7 = "00000000-0000-4000-8000-00000000000a"
WORKER_8 = "00000000-0000-4000-8000-00000000000b"
WORKER_9 = "00000000-0000-4000-8000-00000000000c"
WORKER_10 = "00000000-0000-4000-8000-00000000000d"
WORKER_11 = "00000000-0000-4000-8000-00000000000e"
WORKER_12 = "00000000-0000-4000-8000-00000000000f"
VANGUARD_1 = "00000000-0000-4000-8000-000000000004"
RANGER_1 = "00000000-0000-4000-8000-000000000005"
VANGUARD_2 = "00000000-0000-4000-8000-000000000010"
RANGER_2 = "00000000-0000-4000-8000-000000000011"
VANGUARD_3 = "00000000-0000-4000-8000-000000000012"
VANGUARD_4 = "00000000-0000-4000-8000-000000000015"
VANGUARD_5 = "00000000-0000-4000-8000-000000000016"
RANGER_3 = "00000000-0000-4000-8000-000000000013"
RANGER_4 = "00000000-0000-4000-8000-000000000014"
RANGER_5 = "00000000-0000-4000-8000-000000000017"
ENEMY_1 = "10000000-0000-4000-8000-000000000001"
ENEMY_2 = "10000000-0000-4000-8000-000000000002"
ALLY_CORE_ID = "20000000-0000-4000-8000-000000000001"
ALLY_UNIT_ID = "20000000-0000-4000-8000-000000000002"


def unit(
    identifier: str,
    unit_type: str,
    position: tuple[int, int],
    *,
    cargo: int | None = None,
    controlled: bool = True,
    hp: int | None = None,
) -> dict[str, object]:
    return {
        "kind": "UNIT",
        "id": identifier,
        "controlled": controlled,
        "position": list(position),
        "hp": hp if hp is not None else (2 if unit_type != "VANGUARD" else 4),
        "unit_type": unit_type,
        "cargo": cargo,
    }


def enemy_core(
    identifier: str,
    position: tuple[int, int],
) -> dict[str, object]:
    return {
        "kind": "CORE",
        "id": identifier,
        "controlled": False,
        "owner_username": "enemy",
        "position": list(position),
        "hp": 5,
        "shield": 5,
        "state": "NORMAL",
    }


def make_turn(
    *,
    tick: int = 9,
    resources: int = 0,
    core_hp: int = 5,
    shield: int = 5,
    core: bool = True,
    core_position: tuple[int, int] = (0, 0),
    core_identifier: str = CORE_ID,
    owner_username: str = "farmer",
    core_state: str = "NORMAL",
    move_direction: str = "RIGHT",
    move_progress: int = 1,
    move_destination: tuple[int, int] | None = None,
    beacon_position: tuple[int, int] = (0, 0),
    beacon_status: str | None = None,
    units: list[dict[str, object]] | None = None,
    enemies: list[dict[str, object]] | None = None,
    resource_cells: list[tuple[int, int]] | None = None,
    obstacles: list[tuple[int, int]] | None = None,
    events: list[dict[str, object]] | None = None,
) -> Turn:
    objects: list[dict[str, object]] = []
    if core:
        core_object: dict[str, object] = {
            "kind": "CORE",
            "id": core_identifier,
            "controlled": True,
            "owner_username": owner_username,
            "position": list(core_position),
            "hp": core_hp,
            "shield": shield,
            "state": core_state,
        }
        if core_state == "MOVING":
            direction = Direction(move_direction)
            destination = move_destination or (
                core_position[0] + direction.delta[0],
                core_position[1] + direction.delta[1],
            )
            core_object.update(
                {
                    "move_direction": move_direction,
                    "move_progress": move_progress,
                    "move_required_ticks": 4,
                    "destination": list(destination),
                }
            )
        objects.append(core_object)
    objects.extend(units or [])
    objects.extend(enemies or [])
    if resource_cells:
        objects.append({"kind": "RESOURCE", "positions": resource_cells})
    if obstacles:
        objects.append({"kind": "OBSTACLE", "positions": obstacles})

    beacon: dict[str, object] = {"position": list(beacon_position)}
    if beacon_status is not None:
        beacon["status"] = beacon_status

    state = PlayerState.model_validate(
        {
            "status": "ACTIVE" if core else "RESPAWNING",
            "respawn_at_tick": None if core else tick + 10,
            "resources": resources,
            "population": len(units or []),
            "champion_beacon": beacon,
            "objects": objects,
            "events": events or [],
        }
    )

    def submitter(plan: CommandPlan, _key: str | None) -> Accepted:
        return Accepted(
            accepted=True,
            tick=plan.tick,
            source="AGENT",
            received_at="2026-08-01T00:00:00Z",
        )

    return Turn(tick=tick, state=state, submitter=submitter)


def plan(
    turn: Turn,
    *,
    beacon_policy: str = "pursue",
) -> dict[str, object]:
    CoreFarmer(beacon_policy=beacon_policy).choose_actions(turn)
    return turn.plan.model_dump(mode="json", exclude_none=True)


class AllianceCoordinatorTests(unittest.TestCase):
    def test_population_leader_is_selected_and_follower_core_moves_toward_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            leader = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            )
            follower = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-1",
                expected_members=2,
                barrier_timeout_seconds=0,
            )
            leader.publish(
                make_turn(
                    tick=100,
                    core_identifier=ALLY_CORE_ID,
                    owner_username="ally",
                    core_position=(30, 0),
                    units=[
                        unit(ALLY_UNIT_ID, "WORKER", (29, 0), cargo=0),
                        unit(ENEMY_2, "VANGUARD", (30, 1)),
                    ],
                )
            )
            turn = make_turn(
                tick=100,
                core_position=(0, 0),
                units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            )
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="hold",
                alliance_coordinator=follower,
            )

            tactic.choose_actions(turn)

            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            self.assertTrue(tactic.alliance_ready)
            self.assertEqual(tactic.alliance_leader.account_id, "account-2")
            self.assertEqual(queued["core_action"]["type"], "START_MOVE")
            self.assertEqual(queued["core_action"]["direction"], "RIGHT")
            self.assertEqual(tactic.active_core_move_reason, "ALLY_RALLY")

    def test_configured_alliance_rally_radius_controls_stop_distance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            leader = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            )
            follower = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-1",
                expected_members=2,
                barrier_timeout_seconds=0,
            )
            leader.publish(
                make_turn(
                    tick=100,
                    core_identifier=ALLY_CORE_ID,
                    owner_username="ally",
                    core_position=(20, 0),
                    units=[unit(ALLY_UNIT_ID, "WORKER", (20, 1), cargo=0)],
                )
            )
            turn = make_turn(
                tick=100,
                core_position=(0, 0),
                units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            )
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="hold",
                alliance_coordinator=follower,
            )
            tactic.alliance_rally_radius = 24

            tactic.choose_actions(turn)

            self.assertIsNone(tactic._alliance_rally_target(turn))
            core_action = turn.plan.model_dump(mode="json", exclude_none=True).get(
                "core_action"
            )
            self.assertTrue(
                core_action is None or core_action["type"] != "START_MOVE"
            )

    def test_follower_core_routes_around_obstacles_toward_leader(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            ).publish(
                make_turn(
                    tick=100,
                    core_identifier=ALLY_CORE_ID,
                    owner_username="ally",
                    core_position=(30, 30),
                    units=[
                        unit(ALLY_UNIT_ID, "WORKER", (29, 30), cargo=0),
                        unit(ENEMY_2, "WORKER", (30, 29), cargo=0),
                    ],
                )
            )
            turn = make_turn(
                tick=100,
                core_position=(0, 0),
                units=[unit(WORKER_1, "WORKER", (-2, -2), cargo=0)],
                obstacles=[(1, 0), (0, 1)],
            )
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="hold",
                alliance_coordinator=AllianceCoordinator(
                    shared,
                    alliance_id="duo",
                    account_id="account-1",
                    expected_members=2,
                    barrier_timeout_seconds=0,
                ),
            )

            tactic.choose_actions(turn)

            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            self.assertEqual(queued["core_action"]["type"], "START_MOVE")
            self.assertIn(queued["core_action"]["direction"], {"UP", "LEFT"})
            self.assertEqual(tactic.active_core_move_reason, "ALLY_RALLY")

            leader_turn = make_turn(
                tick=101,
                core_identifier=ALLY_CORE_ID,
                owner_username="ally",
                core_position=(30, 30),
                units=[
                    unit(ALLY_UNIT_ID, "WORKER", (29, 30), cargo=0),
                    unit(ENEMY_2, "WORKER", (30, 29), cargo=0),
                ],
            )
            AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            ).publish(leader_turn)
            moving = make_turn(
                tick=101,
                core_position=(0, 0),
                core_state="MOVING",
                move_direction=queued["core_action"]["direction"],
                move_destination=(0, -1) if queued["core_action"]["direction"] == "UP" else (-1, 0),
                units=[unit(WORKER_1, "WORKER", (-2, -2), cargo=0)],
                obstacles=[(1, 0), (0, 1)],
            )

            tactic.choose_actions(moving)

            continued = moving.plan.model_dump(mode="json", exclude_none=True)
            self.assertNotEqual(
                continued.get("core_action", {}).get("type"),
                "CANCEL_MOVE",
            )

    def test_allied_core_and_units_are_not_targets_or_threats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            peer = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            )
            local = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-1",
                expected_members=2,
                barrier_timeout_seconds=0,
            )
            peer.publish(
                make_turn(
                    tick=100,
                    core_identifier=ALLY_CORE_ID,
                    owner_username="ally",
                    core_position=(3, 0),
                    units=[unit(ALLY_UNIT_ID, "RANGER", (2, 0))],
                )
            )
            turn = make_turn(
                tick=100,
                resources=20,
                enemies=[
                    {
                        **enemy_core(ALLY_CORE_ID, (3, 0)),
                        "owner_username": "ally",
                    },
                    unit(ALLY_UNIT_ID, "RANGER", (2, 0), controlled=False),
                ],
            )
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="hold",
                alliance_coordinator=local,
            )
            tactic.revenge_usernames = {"ally", "rival"}

            tactic.choose_actions(turn)

            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            self.assertEqual(tactic._hostile_enemies(turn), ())
            self.assertEqual(tactic.allied_occupied_cells, {(2, 0), (3, 0)})
            self.assertFalse(tactic.combat_pressure_active)
            self.assertEqual(tactic.revenge_usernames, {"rival"})
            self.assertIsNone(tactic.isolated_core_target_id)
            self.assertNotIn("START_MOVE", str(queued.get("core_action", {})))

    def test_missing_expected_member_pauses_all_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="hold",
                alliance_coordinator=AllianceCoordinator(
                    Path(directory),
                    alliance_id="duo",
                    account_id="account-1",
                    expected_members=2,
                    barrier_timeout_seconds=0,
                ),
            )
            turn = make_turn(
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=0)],
            )

            tactic.choose_actions(turn)

            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            self.assertFalse(tactic.alliance_ready)
            self.assertEqual(queued["core_action"]["type"], "WAIT")
            self.assertEqual(
                queued["unit_actions"][WORKER_1]["type"],
                "WAIT",
            )

    def test_previous_tick_peer_state_is_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            ).publish(make_turn(tick=99, core_identifier=ALLY_CORE_ID))
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="hold",
                alliance_coordinator=AllianceCoordinator(
                    shared,
                    alliance_id="duo",
                    account_id="account-1",
                    expected_members=2,
                    barrier_timeout_seconds=0,
                ),
            )

            tactic.choose_actions(make_turn(tick=100))

            self.assertFalse(tactic.alliance_ready)

    def test_stale_and_foreign_alliance_state_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            coordinator = AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-1",
                stale_seconds=10,
            )
            (shared / "stale.json").write_text(
                '{"version":1,"alliance_id":"duo","account_id":"stale",'
                '"username":"ally","tick":1,"population":99,"core_id":null,'
                '"core_position":null,"unit_ids":[],"updated_at":1}',
                encoding="utf-8",
            )
            (shared / "foreign.json").write_text(
                '{"version":1,"alliance_id":"other","account_id":"foreign",'
                '"username":"ally","tick":1,"population":99,"core_id":null,'
                '"core_position":null,"unit_ids":[],"updated_at":100}',
                encoding="utf-8",
            )

            self.assertEqual(coordinator.peers(now=100), ())


class ResourceLedgerTests(unittest.TestCase):
    @staticmethod
    def _snapshot(*, tick: int = 100, resources: int = 31) -> ResourceLedgerSnapshot:
        return ResourceLedgerSnapshot(
            tick=tick,
            resources=resources,
            population=18,
            workers=12,
            vanguards=3,
            rangers=3,
            actions="SPAWN:1",
            core_action="SPAWN",
        )

    def test_spawn_cost_reconciles_negative_resource_delta(self) -> None:
        turn = make_turn(
            tick=101,
            resources=15,
            units=[unit(RANGER_4, "RANGER", (0, 0))],
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-000000000020",
                    "tick": 100,
                    "event_type": "CORE_SPAWN_SUCCEEDED",
                    "actor_id": CORE_ID,
                    "target_id": RANGER_4,
                    "position": [0, 0],
                    "values": {"unit_type": "RANGER", "cost": 16},
                }
            ],
        )

        result = _reconcile_resource_turn(self._snapshot(), turn)

        self.assertEqual(result.actual_delta, -16)
        self.assertEqual(result.expected_delta, -16)
        self.assertEqual(result.unexplained_loss, 0)

    def test_unexplained_negative_delta_emits_structured_warning(self) -> None:
        turn = make_turn(tick=101, resources=1)
        result = _reconcile_resource_turn(self._snapshot(), turn)
        output = io.StringIO()

        with redirect_stderr(output):
            _emit_resource_ledger(result)

        self.assertEqual(result.unexplained_loss, 30)
        warning = output.getvalue()
        self.assertIn("WARNING unexplained_resource_loss", warning)
        self.assertIn("resources=31->1", warning)
        self.assertIn("previous_fleet=12W:3V:3R", warning)
        self.assertIn("events=none", warning)

    def test_tick_gap_records_loss_without_false_alarm(self) -> None:
        turn = make_turn(tick=103, resources=20)

        result = _reconcile_resource_turn(self._snapshot(), turn)

        self.assertEqual(result.skipped_reason, "tick_gap")
        self.assertEqual(result.unexplained_loss, 0)


class DynamicPricingTests(unittest.TestCase):
    def test_v014_unit_cost_boundaries(self) -> None:
        expected = {
            19: (5, 10, 12),
            20: (7, 13, 16),
            24: (7, 13, 16),
            25: (8, 17, 20),
            29: (8, 17, 20),
            30: (11, 22, 26),
        }
        for population, prices in expected.items():
            with self.subTest(population=population):
                self.assertEqual(
                    tuple(
                        unit_cost(unit_type, population)
                        for unit_type in (
                            UnitType.WORKER,
                            UnitType.VANGUARD,
                            UnitType.RANGER,
                        )
                    ),
                    prices,
                )


class CoreFarmerTests(unittest.TestCase):
    def test_production_weights_choose_largest_ratio_deficit(self) -> None:
        tactic = CoreFarmer(worker_target=18, beacon_policy="hold")
        tactic.production_weights = {
            UnitType.WORKER: 1,
            UnitType.VANGUARD: 1,
            UnitType.RANGER: 4,
        }
        turn = make_turn(
            resources=200,
            units=self._workers(8) + [
                unit(VANGUARD_1, "VANGUARD", (3, 0)),
                unit(RANGER_1, "RANGER", (-3, 0)),
            ],
        )

        tactic.choose_actions(turn)

        self.assertEqual(
            turn.plan.model_dump(mode="json", exclude_none=True)["core_action"]["unit_type"],
            "RANGER",
        )

    def test_production_weights_do_not_override_initial_force_stage(self) -> None:
        tactic = CoreFarmer(worker_target=18, beacon_policy="hold")
        tactic.production_weights = {
            UnitType.WORKER: 0,
            UnitType.VANGUARD: 0,
            UnitType.RANGER: 1,
        }
        turn = make_turn(resources=200, units=[])

        tactic.choose_actions(turn)

        self.assertEqual(
            turn.plan.model_dump(mode="json", exclude_none=True)["core_action"]["unit_type"],
            "WORKER",
        )

    def test_expedition_keeps_core_guards_unclaimed(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            units=[
                unit(VANGUARD_1, "VANGUARD", (0, 1)),
                unit(VANGUARD_2, "VANGUARD", (8, 0)),
                unit(RANGER_1, "RANGER", (1, 0)),
                unit(RANGER_2, "RANGER", (9, 0)),
            ]
        )

        orders = tactic.expedition_orders(
            turn,
            [{
                "id": 3,
                "enabled": True,
                "ranger_count": 1,
                "vanguard_count": 1,
                "target_x": 20,
                "target_y": 0,
            }],
            claimed_ids=set(),
        )

        selected = {unit_id for order in orders for unit_id in order["unit_ids"]}
        self.assertEqual(selected, {VANGUARD_2, RANGER_2})

    def test_expedition_preserves_existing_combat_action(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            units=[unit(RANGER_1, "RANGER", (0, 0))],
            enemies=[unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False)],
        )
        tactic.choose_actions(turn)
        before = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"][RANGER_1]

        tactic.apply_unit_orders(
            turn,
            [{
                "id": -10,
                "preserve_combat": True,
                "unit_type": "RANGER",
                "unit_count": 1,
                "unit_ids": [RANGER_1],
                "target_x": 20,
                "target_y": 0,
            }],
        )

        self.assertEqual(before["type"], "SHOOT")
        self.assertEqual(
            turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"][RANGER_1],
            before,
        )

    def test_dashboard_order_overrides_worker_action(self) -> None:
        turn = make_turn(
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
        )
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(turn)

        completed = tactic.apply_unit_orders(
            turn,
            [
                {
                    "id": 7,
                    "unit_type": "WORKER",
                    "unit_count": 1,
                    "unit_ids": [WORKER_1],
                    "target_x": 3,
                    "target_y": 0,
                }
            ],
        )

        self.assertEqual(completed, ())
        self.assertEqual(
            turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "RIGHT"},
        )

    def test_dashboard_order_controls_only_selected_unit(self) -> None:
        turn = make_turn(
            units=[
                unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                unit(WORKER_2, "WORKER", (2, 0), cargo=0),
            ],
        )
        tactic = CoreFarmer(worker_target=2, beacon_policy="hold")
        tactic.choose_actions(turn)
        before = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]

        tactic.apply_unit_orders(
            turn,
            [
                {
                    "id": 8,
                    "unit_type": "WORKER",
                    "unit_count": 1,
                    "unit_ids": [WORKER_1],
                    "target_x": 5,
                    "target_y": 0,
                }
            ],
        )

        actions = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        self.assertEqual(actions[WORKER_1], {"type": "MOVE", "direction": "RIGHT"})
        self.assertEqual(actions[WORKER_2], before[WORKER_2])

    def test_dashboard_core_order_starts_safe_core_move(self) -> None:
        turn = make_turn(units=[unit(WORKER_1, "WORKER", (-2, 0), cargo=0)])
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(turn)

        completed = tactic.apply_unit_orders(
            turn,
            [
                {
                    "id": 9,
                    "unit_type": "CORE",
                    "unit_count": 1,
                    "unit_ids": [CORE_ID],
                    "target_x": 8,
                    "target_y": 0,
                }
            ],
        )

        self.assertEqual(completed, ())
        self.assertEqual(
            turn.plan.model_dump(mode="json", exclude_none=True)["core_action"],
            {"type": "START_MOVE", "direction": "RIGHT"},
        )
        self.assertEqual(tactic.active_core_move_reason, "MANUAL_ORDER")

    def test_dashboard_core_order_clears_congested_departure_lane(self) -> None:
        blockers = [
            unit(WORKER_1, "WORKER", (0, -1), cargo=1),
            unit(WORKER_2, "WORKER", (0, -1), cargo=1),
            unit(WORKER_3, "WORKER", (0, 1), cargo=1),
            unit(WORKER_4, "WORKER", (0, 1), cargo=1),
            unit(WORKER_5, "WORKER", (-1, 0), cargo=1),
            unit(WORKER_6, "WORKER", (-1, 0), cargo=1),
        ]
        turn = make_turn(units=blockers, obstacles=[(1, 0)])
        tactic = CoreFarmer(worker_target=6, beacon_policy="hold")
        tactic.choose_actions(turn)

        tactic.apply_unit_orders(
            turn,
            [
                {
                    "id": 12,
                    "unit_type": "CORE",
                    "unit_count": 1,
                    "unit_ids": [CORE_ID],
                    "target_x": 0,
                    "target_y": 20,
                }
            ],
        )

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(
            queued["core_action"],
            {"type": "START_MOVE", "direction": "DOWN"},
        )
        down_blockers = {
            WORKER_3,
            WORKER_4,
        }
        self.assertTrue(
            any(
                queued["unit_actions"][identifier]["type"] == "MOVE"
                for identifier in down_blockers
            )
        )
        self.assertEqual(tactic.active_core_move_reason, "MANUAL_ORDER")

    def test_dashboard_core_order_pauses_for_bounded_delivery_window(self) -> None:
        cargo_workers = [
            unit(identifier, "WORKER", (-10 - index, index), cargo=1)
            for index, identifier in enumerate(
                (WORKER_1, WORKER_2, WORKER_3, WORKER_4, WORKER_5, WORKER_6)
            )
        ]
        tactic = CoreFarmer(worker_target=6, beacon_policy="hold")
        tactic.last_core_move_tick = 100
        order = {
            "id": 13,
            "unit_type": "CORE",
            "unit_count": 1,
            "unit_ids": [CORE_ID],
            "target_x": 20,
            "target_y": 0,
        }

        paused = make_turn(tick=103, units=cargo_workers)
        tactic.choose_actions(paused)
        tactic.apply_unit_orders(paused, [order])
        paused_plan = paused.plan.model_dump(mode="json", exclude_none=True)

        resumed = make_turn(tick=107, units=cargo_workers)
        tactic.choose_actions(resumed)
        tactic.apply_unit_orders(resumed, [order])
        resumed_plan = resumed.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(paused_plan["core_action"], {"type": "WAIT"})
        self.assertEqual(
            resumed_plan["core_action"],
            {"type": "START_MOVE", "direction": "RIGHT"},
        )

    def test_migration_delivery_window_does_not_reserve_core_for_spawn(self) -> None:
        cargo_workers = [
            unit(WORKER_1, "WORKER", (1, 0), cargo=1),
            unit(WORKER_2, "WORKER", (4, 0), cargo=1),
            unit(WORKER_3, "WORKER", (5, 0), cargo=1),
            unit(WORKER_4, "WORKER", (6, 0), cargo=1),
            unit(WORKER_5, "WORKER", (7, 0), cargo=1),
            unit(WORKER_6, "WORKER", (8, 0), cargo=1),
        ]
        tactic = CoreFarmer(worker_target=18, beacon_policy="hold")
        tactic.manual_core_order_active = True
        tactic.last_core_move_tick = 100
        turn = make_turn(tick=103, resources=20, units=cargo_workers)

        tactic.choose_actions(turn)
        tactic.apply_unit_orders(
            turn,
            [
                {
                    "id": 14,
                    "unit_type": "CORE",
                    "unit_count": 1,
                    "unit_ids": [CORE_ID],
                    "target_x": 20,
                    "target_y": 0,
                }
            ],
        )

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(queued["core_action"], {"type": "WAIT"})
        self.assertEqual(
            queued["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "LEFT"},
        )

    def test_alliance_rally_pauses_for_bounded_delivery_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            peer_units = [
                unit(
                    f"30000000-0000-4000-8000-{index:012d}",
                    "WORKER",
                    (20 + index, 20),
                    cargo=0,
                )
                for index in range(7)
            ]
            AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            ).publish(
                make_turn(
                    tick=103,
                    core_identifier=ALLY_CORE_ID,
                    owner_username="ally",
                    core_position=(0, 30),
                    units=peer_units,
                )
            )
            cargo_workers = [
                unit(identifier, "WORKER", (-10 - index, index), cargo=1)
                for index, identifier in enumerate(
                    (WORKER_1, WORKER_2, WORKER_3, WORKER_4, WORKER_5, WORKER_6)
                )
            ]
            tactic = CoreFarmer(
                worker_target=6,
                beacon_policy="hold",
                alliance_coordinator=AllianceCoordinator(
                    shared,
                    alliance_id="duo",
                    account_id="account-1",
                    expected_members=2,
                    barrier_timeout_seconds=0,
                ),
            )
            tactic.last_core_move_tick = 100

            paused = make_turn(tick=103, units=cargo_workers)
            tactic.choose_actions(paused)

            self.assertEqual(
                paused.plan.model_dump(mode="json", exclude_none=True)["core_action"],
                {"type": "WAIT"},
            )

    def test_alliance_rally_clears_congestion_despite_waiting_cargo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shared = Path(directory)
            peer_units = [
                unit(
                    f"30000000-0000-4000-8000-{index:012d}",
                    "WORKER",
                    (20 + index, 20),
                    cargo=0,
                )
                for index in range(7)
            ]
            AllianceCoordinator(
                shared,
                alliance_id="duo",
                account_id="account-2",
                expected_members=2,
                barrier_timeout_seconds=0,
            ).publish(
                make_turn(
                    tick=100,
                    core_identifier=ALLY_CORE_ID,
                    owner_username="ally",
                    core_position=(0, 30),
                    units=peer_units,
                )
            )
            blockers = [
                unit(WORKER_1, "WORKER", (0, -1), cargo=1),
                unit(WORKER_2, "WORKER", (0, -1), cargo=1),
                unit(WORKER_3, "WORKER", (0, 1), cargo=1),
                unit(WORKER_4, "WORKER", (0, 1), cargo=1),
                unit(WORKER_5, "WORKER", (-1, 0), cargo=1),
                unit(WORKER_6, "WORKER", (-1, 0), cargo=1),
            ]
            turn = make_turn(
                tick=100,
                units=blockers,
                obstacles=[(1, 0)],
            )
            tactic = CoreFarmer(
                worker_target=6,
                beacon_policy="hold",
                alliance_coordinator=AllianceCoordinator(
                    shared,
                    alliance_id="duo",
                    account_id="account-1",
                    expected_members=2,
                    barrier_timeout_seconds=0,
                ),
            )

            tactic.choose_actions(turn)

            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            self.assertEqual(queued["core_action"]["type"], "START_MOVE")
            self.assertIn(
                queued["core_action"]["direction"],
                {"UP", "DOWN", "LEFT"},
            )
            self.assertEqual(tactic.active_core_move_reason, "ALLY_RALLY")

    def test_allied_cells_block_manual_unit_and_core_orders(self) -> None:
        turn = make_turn(units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)])
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(turn)
        tactic.allied_occupied_cells = {(1, 0)}

        tactic.apply_unit_orders(
            turn,
            [
                {
                    "id": 10,
                    "unit_type": "WORKER",
                    "unit_count": 1,
                    "unit_ids": [WORKER_1],
                    "target_x": 3,
                    "target_y": 0,
                },
                {
                    "id": 11,
                    "unit_type": "CORE",
                    "unit_count": 1,
                    "unit_ids": [CORE_ID],
                    "target_x": 3,
                    "target_y": 0,
                },
            ],
        )

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotEqual(
            queued["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "RIGHT"},
        )
        self.assertNotEqual(
            queued.get("core_action"),
            {"type": "START_MOVE", "direction": "RIGHT"},
        )

    def test_mature_core_guards_are_the_nearest_units(self) -> None:
        turn = make_turn(
            units=[
                unit(VANGUARD_1, "VANGUARD", (40, 0)),
                unit(VANGUARD_2, "VANGUARD", (41, 0)),
                unit(VANGUARD_3, "VANGUARD", (0, 3)),
                unit(VANGUARD_4, "VANGUARD", (0, -3)),
                unit(VANGUARD_5, "VANGUARD", (42, 0)),
                unit(RANGER_1, "RANGER", (-40, 0)),
                unit(RANGER_2, "RANGER", (-41, 0)),
                unit(RANGER_3, "RANGER", (2, 0)),
                unit(RANGER_4, "RANGER", (-2, 0)),
                unit(RANGER_5, "RANGER", (-42, 0)),
            ]
        )

        vanguards, rangers = _core_guard_ids(turn)

        self.assertEqual(vanguards, {UUID(VANGUARD_3), UUID(VANGUARD_4)})
        self.assertEqual(rangers, {UUID(RANGER_3), UUID(RANGER_4)})

    def test_core_threat_recalls_active_raid_units(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (20, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (21, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (30, 0))],
                )
            )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

        threatened = make_turn(
            tick=103,
            units=defenders,
            enemies=[
                enemy_core(ENEMY_1, (30, 0)),
                unit(ENEMY_2, "RANGER", (3, 0), controlled=False),
            ],
        )
        tactic.choose_actions(threatened)
        actions = threatened.plan.model_dump(mode="json", exclude_none=True)[
            "unit_actions"
        ]

        self.assertEqual(actions[VANGUARD_2], {"type": "MOVE", "direction": "LEFT"})
        self.assertEqual(actions[RANGER_2], {"type": "MOVE", "direction": "LEFT"})
        self.assertIn(UUID(VANGUARD_2), tactic.squad_return_ids)
        self.assertIn(UUID(RANGER_2), tactic.squad_return_ids)

    def test_near_core_threat_spawns_combat_unit_before_retreat(self) -> None:
        turn = make_turn(
            tick=100,
            resources=10,
            units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
        )

        CoreFarmer(worker_target=6, beacon_policy="hold").choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(
            queued["core_action"],
            {"type": "SPAWN", "unit_type": "VANGUARD"},
        )

    @staticmethod
    def _workers(count: int, *, cargo: int = 0) -> list[dict[str, object]]:
        identifiers = [
            WORKER_1,
            WORKER_2,
            WORKER_3,
            WORKER_4,
            WORKER_5,
            WORKER_6,
            WORKER_7,
            WORKER_8,
            WORKER_9,
            WORKER_10,
            WORKER_11,
            WORKER_12,
        ] + [
            f"20000000-0000-4000-8000-{index:012x}"
            for index in range(16, 27)
        ]
        positions = [
            (1, 0),
            (0, 1),
            (-1, 0),
            (0, -1),
            (2, 0),
            (0, 2),
            (-2, 0),
            (0, -2),
            (2, 1),
            (1, 2),
            (-2, -1),
            (-1, -2),
        ]
        positions.extend((index, 3) for index in range(7, 18))
        return [
            unit(identifier, "WORKER", position, cargo=cargo)
            for identifier, position in zip(
                identifiers[:count], positions[:count], strict=True
            )
        ]

    def test_respawning_queues_no_actions(self) -> None:
        turn = make_turn(core=False)
        tactic = CoreFarmer()
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(queued["unit_actions"], {})
        self.assertNotIn("core_action", queued)
        self.assertEqual(
            tactic.threat_assessment.lifecycle,
            LifecycleMode.RESPAWNING,
        )
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.RESPAWNING,
        )

    def test_worker_harvests_and_deposits(self) -> None:
        harvesting = plan(
            make_turn(
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=0)],
                resource_cells=[(1, 0)],
            )
        )
        self.assertEqual(harvesting["unit_actions"][WORKER_1]["type"], "HARVEST")

        depositing = plan(
            make_turn(
                resources=9,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=1)],
            )
        )
        self.assertEqual(depositing["unit_actions"][WORKER_1]["type"], "DEPOSIT")

    def test_same_cell_contention_uses_lowest_uuid(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_2, "WORKER", (1, 0), cargo=0),
                    unit(WORKER_1, "WORKER", (1, 0), cargo=0),
                ],
                resource_cells=[(1, 0)],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "HARVEST")
        self.assertNotEqual(queued["unit_actions"][WORKER_2]["type"], "HARVEST")

    def test_depleted_event_retargets_current_resource(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                resource_cells=[(2, 0)],
                events=[
                    {
                        "event_id": "20000000-0000-4000-8000-000000000001",
                        "tick": 8,
                        "event_type": "HARVEST_FAILED",
                        "reason_code": "RESOURCE_DEPLETED",
                        "actor_id": WORKER_1,
                        "position": [1, 0],
                    }
                ],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["direction"], "RIGHT")

    def test_new_refill_replaces_exploration_with_resource_move(self) -> None:
        first = plan(
            make_turn(
                tick=12,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            )
        )
        second = plan(
            make_turn(
                tick=13,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                resource_cells=[(0, -2)],
            )
        )
        self.assertEqual(first["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(second["unit_actions"][WORKER_1]["direction"], "UP")

    def test_nearest_worker_claims_resource_regardless_of_uuid_order(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_1, "WORKER", (10, 0), cargo=0),
                    unit(WORKER_2, "WORKER", (1, 0), cargo=0),
                ],
                resource_cells=[(2, 0)],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_2]["direction"], "RIGHT")

    def test_much_nearer_worker_takes_over_sticky_resource_intent(self) -> None:
        tactic = CoreFarmer()
        tactic.resource_intents[UUID(WORKER_1)] = (2, 0)
        turn = make_turn(
            tick=20,
            core_position=(-5, -5),
            beacon_position=(-5, -5),
            units=[
                unit(WORKER_1, "WORKER", (10, 0), cargo=0),
                unit(WORKER_2, "WORKER", (1, 0), cargo=0),
            ],
            resource_cells=[(2, 0)],
        )

        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.resource_intents, {UUID(WORKER_2): (2, 0)})
        self.assertEqual(queued["unit_actions"][WORKER_2]["direction"], "RIGHT")

    def test_resource_intent_stays_with_worker_when_advantage_is_small(self) -> None:
        tactic = CoreFarmer()
        tactic.resource_intents[UUID(WORKER_1)] = (4, 0)
        turn = make_turn(
            tick=20,
            core_position=(-5, -5),
            beacon_position=(-5, -5),
            units=[
                unit(WORKER_1, "WORKER", (1, 0), cargo=0),
                unit(WORKER_2, "WORKER", (2, 0), cargo=0),
            ],
            resource_cells=[(4, 0)],
        )

        tactic.choose_actions(turn)

        self.assertEqual(tactic.resource_intents, {UUID(WORKER_1): (4, 0)})

    def test_resource_assignment_minimizes_total_path_cost(self) -> None:
        tactic = CoreFarmer()
        turn = make_turn(
            tick=20,
            core_position=(-20, -20),
            beacon_position=(-20, -20),
            units=[
                unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                unit(WORKER_2, "WORKER", (10, 0), cargo=0),
            ],
            resource_cells=[(4, 0), (-5, 0)],
        )

        tactic.choose_actions(turn)

        self.assertEqual(tactic.resource_intents[UUID(WORKER_1)], (-5, 0))
        self.assertEqual(tactic.resource_intents[UUID(WORKER_2)], (4, 0))

    def test_worker_enters_resource_cell_occupied_by_one_friendly_defender(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            tick=100,
            resources=10,
            units=[
                unit(WORKER_1, "WORKER", (-3, 0), cargo=0),
                unit(RANGER_1, "RANGER", (-2, 0)),
            ],
            resource_cells=[(-2, 0)],
        )

        tactic.choose_actions(turn)
        actions = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]

        self.assertEqual(actions[WORKER_1], {"type": "MOVE", "direction": "RIGHT"})
        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "RESOURCE")
        self.assertEqual(actions[RANGER_1]["type"], "MOVE")

    def test_resource_assignment_uses_obstacle_path_cost(self) -> None:
        tactic = CoreFarmer()
        turn = make_turn(
            core_position=(-5, -5),
            beacon_position=(-5, -5),
            units=[
                unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                unit(WORKER_2, "WORKER", (0, 4), cargo=0),
            ],
            resource_cells=[(2, 0)],
            obstacles=[(1, -2), (1, -1), (1, 0), (1, 1), (1, 2)],
        )
        tactic.choose_actions(turn)
        self.assertEqual(tactic.worker_targets[UUID(WORKER_2)], (2, 0))
        self.assertNotEqual(tactic.worker_targets[UUID(WORKER_1)], (2, 0))

    def test_stalled_resource_target_is_released_temporarily(self) -> None:
        tactic = CoreFarmer()
        latest: Turn | None = None
        for tick in range(50, 58):
            latest = make_turn(
                tick=tick,
                core_position=(-5, -5),
                beacon_position=(-5, -5),
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                resource_cells=[(3, 0)],
            )
            tactic.choose_actions(latest)

        self.assertIsNotNone(latest)
        self.assertEqual(tactic.last_released_targets[UUID(WORKER_1)], (3, 0))
        self.assertNotIn(UUID(WORKER_1), tactic.resource_intents)
        self.assertTrue(tactic.worker_modes[UUID(WORKER_1)].startswith("SCOUT"))

    def test_worker_keeps_resource_intent_through_temporary_fog(self) -> None:
        tactic = CoreFarmer()
        visible = make_turn(
            tick=20,
            core_position=(0, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (3, 0), cargo=0)],
            resource_cells=[(-5, 0)],
        )
        tactic.choose_actions(visible)

        fogged = make_turn(
            tick=21,
            core_position=(1, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (2, 0), cargo=0)],
        )
        tactic.choose_actions(fogged)
        queued = fogged.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "RESOURCE")
        self.assertEqual(tactic.worker_targets[UUID(WORKER_1)], (-5, 0))

    def test_exploration_does_not_cycle_back_every_four_ticks(self) -> None:
        tactic = CoreFarmer()
        position = (0, 0)
        deltas = {
            "UP": (0, -1),
            "RIGHT": (1, 0),
            "DOWN": (0, 1),
            "LEFT": (-1, 0),
        }
        for tick in range(20, 24):
            turn = make_turn(
                tick=tick,
                units=[unit(WORKER_1, "WORKER", position, cargo=0)],
            )
            tactic.choose_actions(turn)
            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            direction = queued["unit_actions"][WORKER_1]["direction"]
            dx, dy = deltas[direction]
            position = position[0] + dx, position[1] + dy

        self.assertNotEqual(position, (0, 0))
        self.assertGreater(abs(position[0]) + abs(position[1]), 1)

    def test_stalled_scout_switches_direction_after_three_ticks(self) -> None:
        tactic = CoreFarmer(beacon_policy="hold")
        targets = []
        for tick in range(20, 24):
            turn = make_turn(
                tick=tick,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            )
            tactic.choose_actions(turn)
            targets.append(tactic.worker_targets[UUID(WORKER_1)])

        self.assertEqual(targets[:3], [targets[0]] * 3)
        self.assertNotEqual(targets[3], targets[0])

    def test_interrupted_scout_returns_before_resuming_exploration(self) -> None:
        tactic = CoreFarmer(beacon_policy="hold")
        first_target = None
        for tick in range(20, 23):
            turn = make_turn(
                tick=tick,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            )
            tactic.choose_actions(turn)
            first_target = tactic.worker_targets[UUID(WORKER_1)]

        interrupted = make_turn(
            tick=23,
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            enemies=[unit(ENEMY_1, "VANGUARD", (1, 1), controlled=False)],
        )
        tactic.choose_actions(interrupted)
        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "SCOUT_EVADE")
        self.assertNotIn(UUID(WORKER_1), tactic.scout_progress)

        resumed = make_turn(
            tick=24,
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
        )
        tactic.choose_actions(resumed)

        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "SCOUT_COOLDOWN")
        self.assertEqual(tactic.worker_targets[UUID(WORKER_1)], (0, 0))
        self.assertNotEqual(tactic.worker_targets[UUID(WORKER_1)], first_target)

    def test_worker_avoids_visible_obstacle(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                resource_cells=[(2, 0)],
                obstacles=[(1, 0)],
            )
        )
        self.assertNotEqual(queued["unit_actions"][WORKER_1]["direction"], "RIGHT")

    def test_worker_avoids_vanguard_attack_cell(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (1, 1), controlled=False),
                ],
                resource_cells=[(2, 0)],
            )
        )
        self.assertNotEqual(queued["unit_actions"][WORKER_1]["direction"], "RIGHT")

    def test_worker_avoids_clear_ranger_fire_lane(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                enemies=[
                    unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
                ],
                resource_cells=[(2, 0)],
            )
        )
        self.assertNotEqual(queued["unit_actions"][WORKER_1]["direction"], "RIGHT")

    def test_worker_evades_visible_combat_units(self) -> None:
        enemy_objects = (
            unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False),
            unit(ENEMY_1, "RANGER", (2, 0), controlled=False),
        )
        for enemy in enemy_objects:
            with self.subTest(kind=enemy["kind"], unit_type=enemy.get("unit_type")):
                queued = plan(
                    make_turn(
                        core_position=(10, 10),
                        units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                        enemies=[enemy],
                        resource_cells=[(1, 0)],
                    ),
                    beacon_policy="retreat",
                )
                action = queued["unit_actions"][WORKER_1]
                self.assertEqual(action["type"], "MOVE")
                direction = Direction(action["direction"])
                destination = direction.delta
                self.assertGreater(
                    abs(destination[0] - 2) + abs(destination[1]),
                    2,
                )

    def test_enemy_worker_does_not_interrupt_resource_collection(self) -> None:
        queued = plan(
            make_turn(
                core_position=(10, 10),
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                enemies=[unit(ENEMY_1, "WORKER", (2, 0), controlled=False)],
                resource_cells=[(1, 0)],
            ),
            beacon_policy="hold",
        )
        self.assertEqual(
            queued["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "RIGHT"},
        )

    def test_worker_on_resource_harvests_despite_visible_core(self) -> None:
        queued = plan(
            make_turn(
                core_position=(10, 10),
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                enemies=[enemy_core(ENEMY_1, (2, 0))],
                resource_cells=[(0, 0)],
            ),
            beacon_policy="hold",
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "HARVEST")

    def test_worker_remembers_permanent_obstacle(self) -> None:
        tactic = CoreFarmer()
        first = make_turn(
            tick=10,
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            obstacles=[(1, 0)],
        )
        tactic.choose_actions(first)

        second = make_turn(
            tick=11,
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            resource_cells=[(2, 0)],
        )
        tactic.choose_actions(second)
        queued = second.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotEqual(queued["unit_actions"][WORKER_1]["direction"], "RIGHT")

    def test_worker_routes_around_obstacle_without_revisiting(self) -> None:
        tactic = CoreFarmer()
        position = (0, 0)
        visited = [position]
        deltas = {
            "UP": (0, -1),
            "RIGHT": (1, 0),
            "DOWN": (0, 1),
            "LEFT": (-1, 0),
        }

        for tick in range(20, 25):
            turn = make_turn(
                tick=tick,
                units=[unit(WORKER_1, "WORKER", position, cargo=0)],
                resource_cells=[(3, 0)],
                obstacles=[(1, 0)],
            )
            tactic.choose_actions(turn)
            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            direction = queued["unit_actions"][WORKER_1]["direction"]
            dx, dy = deltas[direction]
            position = position[0] + dx, position[1] + dy
            visited.append(position)

        self.assertEqual(position, (3, 0))
        self.assertEqual(len(visited), len(set(visited)))

    def test_pathfinder_penalizes_recent_backtracking(self) -> None:
        directions = _path_directions(
            (0, -1),
            (10, 0),
            {(1, -2), (1, -1), (1, 0)},
            discouraged={(0, 0)},
        )
        self.assertEqual(len(directions), 1)
        self.assertNotEqual(directions, (Direction.DOWN,))

    def test_resource_route_penalizes_recent_backtracking(self) -> None:
        tactic = CoreFarmer()
        tactic.worker_history[UUID(WORKER_1)] = deque([(0, 0)], maxlen=6)
        turn = make_turn(
            core_position=(0, 5),
            beacon_position=(0, 5),
            units=[unit(WORKER_1, "WORKER", (0, -1), cargo=0)],
            resource_cells=[(10, 0)],
            obstacles=[(1, -2), (1, -1), (1, 0)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotEqual(queued["unit_actions"][WORKER_1]["direction"], "DOWN")

    def test_pathfinder_handles_far_diagonal_and_budget_fallback(self) -> None:
        diagonal = _path_directions((0, 0), (100, 100), set())
        self.assertIn(diagonal, ((Direction.RIGHT,), (Direction.DOWN,)))

        fallback = _path_directions(
            (0, 0),
            (100, 100),
            set(),
            max_expansions=1,
        )
        self.assertIn(fallback, ((Direction.RIGHT,), (Direction.DOWN,)))

    def test_pathfinder_approaches_open_rally_radius_when_center_is_blocked(
        self,
    ) -> None:
        blocked = {(10, 0), (9, 0), (11, 0), (10, -1), (10, 1)}

        directions = _path_directions(
            (0, 0),
            (10, 0),
            blocked,
            target_radius=5,
        )

        self.assertEqual(directions, (Direction.RIGHT,))

    def test_cargo_worker_routes_around_obstacle_and_deposits(self) -> None:
        tactic = CoreFarmer()
        position = (2, 0)
        visited = [position]
        deltas = {
            "UP": (0, -1),
            "RIGHT": (1, 0),
            "DOWN": (0, 1),
            "LEFT": (-1, 0),
        }

        for tick in range(30, 34):
            turn = make_turn(
                tick=tick,
                units=[unit(WORKER_1, "WORKER", position, cargo=1)],
                obstacles=[(1, 0)],
            )
            tactic.choose_actions(turn)
            queued = turn.plan.model_dump(mode="json", exclude_none=True)
            direction = queued["unit_actions"][WORKER_1]["direction"]
            dx, dy = deltas[direction]
            position = position[0] + dx, position[1] + dy
            visited.append(position)

        depositing = make_turn(
            tick=34,
            units=[unit(WORKER_1, "WORKER", position, cargo=1)],
            obstacles=[(1, 0)],
        )
        tactic.choose_actions(depositing)
        queued = depositing.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(position, (0, 0))
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "DEPOSIT")
        self.assertEqual(len(visited), len(set(visited)))

    def test_cargo_worker_uses_second_slot_in_single_friendly_corridor(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_1, "WORKER", (2, 0), cargo=1),
                    unit(VANGUARD_1, "VANGUARD", (1, 0)),
                ],
                obstacles=[(2, -1), (2, 1), (3, 0)],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(
            queued["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "LEFT"},
        )

    def test_defender_and_cargo_worker_swap_through_legal_second_slots(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_1, "WORKER", (1, 0), cargo=1),
                    unit(VANGUARD_1, "VANGUARD", (0, 0)),
                ],
                obstacles=[(-1, 0), (0, -1), (0, 1)],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(
            queued["unit_actions"][VANGUARD_1],
            {"type": "MOVE", "direction": "RIGHT"},
        )
        self.assertEqual(
            queued["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "LEFT"},
        )

    def test_cargo_worker_cancels_moving_core_for_delivery(self) -> None:
        queued = plan(
            make_turn(
                core_state="MOVING",
                beacon_position=(5, 0),
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=1)],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "WAIT")
        self.assertEqual(queued["core_action"]["type"], "CANCEL_MOVE")

    def test_defender_vacates_core_for_cargo_despite_visible_far_worker(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_1, "WORKER", (1, 0), cargo=1),
                    unit(VANGUARD_1, "VANGUARD", (0, 0)),
                ],
                enemies=[
                    unit(ENEMY_1, "WORKER", (20, 0), controlled=False),
                ],
            )
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][WORKER_1]["direction"], "LEFT")

    def test_cargo_worker_avoids_ranger_fire_lane_on_return(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(WORKER_1, "WORKER", (2, 0), cargo=1)],
                enemies=[
                    unit(ENEMY_1, "RANGER", (1, 3), controlled=False),
                ],
            )
        )
        self.assertNotEqual(queued["unit_actions"][WORKER_1]["direction"], "LEFT")

    def test_ranger_geometry_supports_exact_diagonals(self) -> None:
        self.assertTrue(_ranger_can_shoot((0, 0), (3, 3), set()))
        self.assertFalse(_ranger_can_shoot((0, 0), (2, 1), set()))
        self.assertFalse(_ranger_can_shoot((0, 0), (3, 3), {(2, 2)}))

    def test_enemy_ranger_diagonal_threat_stops_at_obstacle(self) -> None:
        enemy = make_turn(
            enemies=[
                unit(ENEMY_1, "RANGER", (0, 0), controlled=False),
            ],
        ).visible_enemies[0]
        danger = _enemy_threat_cells((enemy,), {(2, 2)})

        self.assertIn((1, 1), danger)
        self.assertNotIn((2, 2), danger)
        self.assertNotIn((3, 3), danger)

    def test_core_stays_stationary_during_beacon_campaign(self) -> None:
        direct = plan(make_turn(beacon_position=(3, 0)))
        self.assertNotEqual(direct.get("core_action", {}).get("type"), "START_MOVE")

        detour = plan(
            make_turn(
                beacon_position=(3, 0),
                resource_cells=[(1, 0)],
            )
        )
        self.assertNotEqual(detour.get("core_action", {}).get("type"), "START_MOVE")

    def test_retreat_policy_does_not_move_core_without_a_threat(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.startup_tick = 0
        turn = make_turn(
            tick=100,
            beacon_position=(3, 0),
            units=[
                unit(WORKER_1, "WORKER", (5, 5), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, 5)),
                unit(RANGER_1, "RANGER", (7, 5)),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_retreat_policy_ignores_beacon_geometry_without_a_threat(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.startup_tick = 0
        turn = make_turn(
            tick=100,
            beacon_position=(3, 0),
            units=[
                unit(WORKER_1, "WORKER", (5, 5), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, 5)),
                unit(RANGER_1, "RANGER", (7, 5)),
            ],
            resource_cells=[(-1, 0)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_retreat_policy_holds_at_beacon_distance_floor(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.startup_tick = 0
        turn = make_turn(
            tick=100,
            core_position=(0, -224),
            beacon_position=(0, 0),
            units=[
                unit(WORKER_1, "WORKER", (5, -224), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, -224)),
                unit(RANGER_1, "RANGER", (7, -224)),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_retreat_policy_keeps_service_window_after_core_move(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.startup_tick = 0
        tactic.last_core_move_tick = 100
        turn = make_turn(
            tick=107,
            beacon_position=(30, 0),
            units=[
                unit(WORKER_1, "WORKER", (5, 5), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, 5)),
                unit(RANGER_1, "RANGER", (7, 5)),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_visible_enemy_makes_core_evade_before_noncritical_repair(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            shield=4,
            beacon_position=(10, 0),
            units=[
                unit(WORKER_1, "WORKER", (5, 5), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, 5)),
                unit(VANGUARD_2, "VANGUARD", (7, 5)),
                unit(RANGER_1, "RANGER", (8, 5)),
                unit(RANGER_2, "RANGER", (9, 5)),
            ],
            enemies=[
                unit(ENEMY_1, "RANGER", (0, 3), controlled=False),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        direction = Direction(queued["core_action"]["direction"])
        destination = direction.delta

        self.assertEqual(queued["core_action"]["type"], "START_MOVE")
        self.assertGreater(
            abs(destination[0]) + abs(destination[1] - 3),
            3,
        )

    def test_core_evades_visible_combat_units(self) -> None:
        enemy_objects = (
            unit(ENEMY_1, "VANGUARD", (3, 0), controlled=False),
            unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
        )
        for enemy in enemy_objects:
            with self.subTest(kind=enemy["kind"], unit_type=enemy.get("unit_type")):
                queued = plan(
                    make_turn(
                        beacon_position=(10, 0),
                        enemies=[enemy],
                    ),
                    beacon_policy="retreat",
                )
                self.assertEqual(queued["core_action"]["type"], "START_MOVE")
                direction = Direction(queued["core_action"]["direction"])
                destination = direction.delta
                self.assertGreater(
                    abs(destination[0] - 3) + abs(destination[1]),
                    3,
                )

    def test_enemy_worker_alone_does_not_make_core_retreat(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(10, 0),
                enemies=[unit(ENEMY_1, "WORKER", (3, 0), controlled=False)],
            ),
            beacon_policy="hold",
        )
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_compatibility_hold_keeps_harvest_and_deposit_running(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "compatibility-hold.json"
            marker.write_text("not-json\n", encoding="utf-8")
            tactic = CoreFarmer(
                worker_target=2,
                beacon_policy="retreat",
                compatibility_marker=marker,
            )
            turn = make_turn(
                resources=5,
                units=[
                    unit(WORKER_1, "WORKER", (1, 0), cargo=0),
                    unit(WORKER_2, "WORKER", (0, 0), cargo=1),
                ],
                resource_cells=[(1, 0)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertTrue(tactic.compatibility_hold)
        self.assertEqual(
            tactic.threat_assessment.lifecycle,
            LifecycleMode.COMPATIBILITY_HOLD,
        )
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.COMPATIBILITY_HOLD,
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "HARVEST")
        self.assertEqual(queued["unit_actions"][WORKER_2]["type"], "DEPOSIT")
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_compatibility_hold_stops_spawning_but_allows_legal_attacks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "compatibility-hold.json"
            marker.write_text("{}\n", encoding="utf-8")
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="retreat",
                compatibility_marker=marker,
            )
            defenders = [
                unit(VANGUARD_1, "VANGUARD", (0, 3)),
                unit(VANGUARD_2, "VANGUARD", (3, 0)),
                unit(RANGER_1, "RANGER", (-2, 0)),
                unit(RANGER_2, "RANGER", (2, 0)),
            ]
            for tick in (100, 101, 102):
                turn = make_turn(
                    tick=tick,
                    resources=30,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (4, 0))],
                )
                tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertIsNone(tactic.isolated_core_target_id)
        self.assertIsNone(tactic.stationary_unit_target_id)
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "SPAWN")
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_compatibility_hold_preserves_healing_and_emergency_retreat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "compatibility-hold.json"
            marker.write_text("{}\n", encoding="utf-8")
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="retreat",
                compatibility_marker=marker,
            )
            damaged = make_turn(resources=2, core_hp=3)
            tactic.choose_actions(damaged)
            self.assertEqual(
                damaged.plan.model_dump(mode="json", exclude_none=True)[
                    "core_action"
                ]["type"],
                "HEAL",
            )

            threatened = make_turn(
                tick=10,
                enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
                beacon_position=(10, 0),
            )
            tactic.choose_actions(threatened)

        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.ENGAGED)
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.COMPATIBILITY_HOLD,
        )
        self.assertEqual(
            threatened.plan.model_dump(mode="json", exclude_none=True)[
                "core_action"
            ]["type"],
            "START_MOVE",
        )

    def test_compatibility_hold_cancels_nonurgent_move_but_keeps_evasion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "compatibility-hold.json"
            marker.write_text("{}\n", encoding="utf-8")
            tactic = CoreFarmer(
                worker_target=1,
                beacon_policy="retreat",
                compatibility_marker=marker,
            )
            tactic.active_core_move_reason = "RETREAT"
            planned = make_turn(
                tick=100,
                core_state="MOVING",
                move_direction="LEFT",
                move_destination=(-1, 0),
            )
            tactic.choose_actions(planned)
            self.assertEqual(
                planned.plan.model_dump(mode="json", exclude_none=True)[
                    "core_action"
                ]["type"],
                "CANCEL_MOVE",
            )

            tactic.active_core_move_reason = "EVADE"
            evading = make_turn(
                tick=101,
                core_state="MOVING",
                move_direction="LEFT",
                move_destination=(-1, 0),
                enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
            )
            tactic.choose_actions(evading)

        self.assertNotIn(
            "core_action",
            evading.plan.model_dump(mode="json", exclude_none=True),
        )

    def test_isolated_core_does_not_make_own_core_evade(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(10, 0),
                enemies=[enemy_core(ENEMY_1, (3, 0))],
            ),
            beacon_policy="hold",
        )
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_evade_toward_beacon_continues_when_enemy_distance_improves(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        normal = make_turn(
            tick=100,
            core_position=(10, 0),
            beacon_position=(0, 0),
            enemies=[unit(ENEMY_1, "RANGER", (13, 0), controlled=False)],
            obstacles=[(10, -1), (10, 1)],
        )
        tactic.choose_actions(normal)
        started = normal.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(started["core_action"], {"type": "START_MOVE", "direction": "LEFT"})

        moving = make_turn(
            tick=101,
            core_position=(10, 0),
            core_state="MOVING",
            move_direction="LEFT",
            move_destination=(9, 0),
            beacon_position=(0, 0),
            enemies=[unit(ENEMY_1, "RANGER", (13, 0), controlled=False)],
            obstacles=[(10, -1), (10, 1)],
        )
        tactic.choose_actions(moving)
        continued = moving.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("core_action", continued)

    def test_evade_toward_beacon_continues_after_enemy_leaves_view(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        normal = make_turn(
            tick=100,
            core_position=(10, 0),
            beacon_position=(0, 0),
            enemies=[unit(ENEMY_1, "RANGER", (13, 0), controlled=False)],
            obstacles=[(10, -1), (10, 1)],
        )
        tactic.choose_actions(normal)
        started = normal.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(started["core_action"], {"type": "START_MOVE", "direction": "LEFT"})

        moving = make_turn(
            tick=101,
            core_position=(10, 0),
            core_state="MOVING",
            move_direction="LEFT",
            move_destination=(9, 0),
            beacon_position=(0, 0),
            obstacles=[(10, -1), (10, 1)],
        )
        tactic.choose_actions(moving)
        continued = moving.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("core_action", continued)
        self.assertEqual(tactic.last_core_cancel_reason, "NONE")

    def test_critical_core_finishes_immediately_safe_evasion(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            resources=1,
            core_hp=2,
            shield=0,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=3,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("core_action", queued)

    def test_critical_core_keeps_improving_evasion_instead_of_heal_loop(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            resources=1,
            core_hp=2,
            shield=0,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=2,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("core_action", queued)
        self.assertEqual(tactic.last_core_cancel_reason, "NONE")

    def test_moving_core_does_not_cancel_without_projected_damage(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            resources=1,
            core_hp=2,
            shield=0,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=2,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("core_action", queued)
        self.assertEqual(tactic.last_core_cancel_reason, "NONE")

    def test_moving_core_cancels_direction_toward_visible_enemy(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="RIGHT",
            move_destination=(1, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            enemies=[
                unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(queued["core_action"]["type"], "CANCEL_MOVE")

    def test_moving_core_keeps_safe_direction_away_from_enemy(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="LEFT",
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            enemies=[
                unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotIn("core_action", queued)

    def test_moving_core_ignores_far_enemy_distance_noise(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="RIGHT",
            move_destination=(1, 0),
            beacon_position=(-10, 0),
            units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            enemies=[unit(ENEMY_1, "RANGER", (20, 0), controlled=False)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotIn("core_action", queued)
        self.assertEqual(tactic.last_core_cancel_reason, "NONE")

    def test_stationary_core_does_not_evade_far_enemy_without_cargo(self) -> None:
        for beacon_policy in ("hold", "retreat"):
            with self.subTest(beacon_policy=beacon_policy):
                tactic = CoreFarmer(worker_target=1, beacon_policy=beacon_policy)
                turn = make_turn(
                    tick=100,
                    beacon_position=(20, 0),
                    units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
                    enemies=[
                        unit(ENEMY_1, "RANGER", (20, 0), controlled=False)
                    ],
                )
                tactic.choose_actions(turn)
                queued = turn.plan.model_dump(mode="json", exclude_none=True)

                self.assertNotEqual(
                    queued.get("core_action", {}).get("type"), "START_MOVE"
                )
                self.assertNotEqual(tactic.active_core_move_reason, "EVADE")

    def test_moving_core_cancels_when_destination_enters_threat_radius(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="RIGHT",
            move_destination=(1, 0),
            beacon_position=(-10, 0),
            units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            enemies=[unit(ENEMY_1, "RANGER", (13, 0), controlled=False)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(queued["core_action"]["type"], "CANCEL_MOVE")
        self.assertEqual(tactic.last_core_cancel_reason, "ENEMY_RISK_WORSE")

    def test_committed_retreat_ignores_nearby_cargo_until_arrival(self) -> None:
        queued = plan(
            make_turn(
                core_state="MOVING",
                move_direction="LEFT",
                move_progress=2,
                move_destination=(-1, 0),
                beacon_position=(5, 0),
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=1)],
            )
        )
        self.assertNotIn("core_action", queued)

    def test_committed_retreat_still_cancels_cargo_on_core(self) -> None:
        queued = plan(
            make_turn(
                core_state="MOVING",
                move_direction="LEFT",
                move_progress=3,
                move_destination=(-1, 0),
                beacon_position=(5, 0),
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=1)],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "CANCEL_MOVE")

    def test_improving_evade_keeps_moving_despite_cargo_on_core(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.active_core_move_reason = "EVADE"
        turn = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=2,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=1)],
            enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotIn("core_action", queued)

    def test_recent_evasion_survives_visibility_loss_and_cargo_arrival(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        visible = make_turn(
            tick=100,
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (2, 2), cargo=0)],
            enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
        )
        tactic.choose_actions(visible)
        started = visible.plan.model_dump(mode="json", exclude_none=True)[
            "core_action"
        ]
        self.assertEqual(started["type"], "START_MOVE")
        direction = Direction(started["direction"])
        destination = direction.delta

        hidden = make_turn(
            tick=101,
            core_state="MOVING",
            move_direction=direction.value,
            move_progress=1,
            move_destination=destination,
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=1)],
        )
        tactic.choose_actions(hidden)
        queued = hidden.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotIn("core_action", queued)
        self.assertEqual(tactic.last_core_cancel_reason, "NONE")

    def test_core_move_allows_one_friendly_unit_at_destination(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        moving = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=2,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (-1, 0), cargo=0)],
        )
        tactic.choose_actions(moving)
        queued = moving.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotIn("core_action", queued)

    def test_core_move_rejects_two_friendly_units_at_destination(self) -> None:
        tactic = CoreFarmer(worker_target=2, beacon_policy="retreat")
        moving = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=2,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            units=[
                unit(WORKER_1, "WORKER", (-1, 0), cargo=0),
                unit(WORKER_2, "WORKER", (-1, 0), cargo=0),
            ],
            obstacles=[(-2, 0), (-1, -1), (-1, 1)],
        )
        tactic.choose_actions(moving)
        queued = moving.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(queued["core_action"]["type"], "CANCEL_MOVE")
        self.assertEqual(tactic.last_core_cancel_reason, "DESTINATION_BLOCKED")

    def test_moving_core_clears_units_from_destination_without_cancelling(self) -> None:
        tactic = CoreFarmer(worker_target=2, beacon_policy="retreat")
        moving = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="LEFT",
            move_progress=2,
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            units=[
                unit(WORKER_1, "WORKER", (-1, 0), cargo=1),
                unit(WORKER_2, "WORKER", (-1, 0), cargo=1),
            ],
        )

        tactic.choose_actions(moving)

        queued = moving.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotIn("core_action", queued)
        self.assertEqual(tactic.last_core_cancel_reason, "NONE")
        self.assertTrue(
            all(
                queued["unit_actions"][identifier]["type"] == "MOVE"
                for identifier in (WORKER_1, WORKER_2)
            )
        )

    def test_committed_retreat_does_not_cancel_for_beacon_geometry(self) -> None:
        queued = plan(
            make_turn(
                core_state="MOVING",
                move_direction="RIGHT",
                move_progress=2,
                move_destination=(1, 0),
                beacon_position=(5, 0),
                units=[unit(WORKER_1, "WORKER", (8, 8), cargo=0)],
            )
        )
        self.assertNotIn("core_action", queued)

    def test_moving_core_cancels_new_resource_destination(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=100,
            core_state="MOVING",
            move_direction="LEFT",
            move_destination=(-1, 0),
            beacon_position=(10, 0),
            units=[unit(WORKER_1, "WORKER", (5, 5), cargo=0)],
            resource_cells=[(-1, 0)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(queued["core_action"]["type"], "CANCEL_MOVE")

    def test_respawn_enters_recovery_and_spawns_second_worker(self) -> None:
        tactic = CoreFarmer()
        turn = make_turn(
            tick=100,
            resources=5,
            core_position=(-100, -100),
            beacon_position=(0, 0),
            units=[unit(WORKER_1, "WORKER", (-100, -100), cargo=0)],
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-000000000003",
                    "tick": 99,
                    "event_type": "CORE_RESPAWNED",
                    "actor_id": CORE_ID,
                    "position": [-100, -100],
                }
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertTrue(tactic.recovery_mode)
        self.assertEqual(tactic.recovery_reason, "CORE_RESPAWNED")
        self.assertEqual(tactic.threat_assessment.lifecycle, LifecycleMode.RECOVERY)
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.RECOVERY,
        )
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "WORKER")

    def test_remote_low_fleet_recovery_survives_process_restart(self) -> None:
        tactic = CoreFarmer(worker_target=8, beacon_policy="hold")
        turn = make_turn(
            tick=500,
            resources=10,
            core_position=(9, -179),
            beacon_position=(47, -17),
            units=self._workers(5),
        )

        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertTrue(tactic.recovery_mode)
        self.assertEqual(tactic.recovery_reason, "REMOTE_LOW_FLEET")
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "WORKER")

    def test_recovery_worker_expansion_still_stops_for_nearby_enemy(self) -> None:
        tactic = CoreFarmer(worker_target=8, beacon_policy="hold")
        turn = make_turn(
            tick=500,
            resources=5,
            core_position=(9, -179),
            beacon_position=(47, -17),
            units=self._workers(3),
            enemies=[
                unit(ENEMY_1, "RANGER", (15, -179), controlled=False),
            ],
        )

        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertTrue(tactic.recovery_mode)
        self.assertEqual(tactic.threat_assessment.lifecycle, LifecycleMode.RECOVERY)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.PRE_EVADE)
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.RECOVERY,
        )
        self.assertNotEqual(
            queued.get("core_action", {}).get("unit_type"),
            "WORKER",
        )

    def test_scouting_revisits_least_recently_covered_rings(self) -> None:
        tactic = CoreFarmer(beacon_policy="hold")
        worker_id = UUID(WORKER_1)
        tactic.scout_slots[worker_id] = 0
        tactic.scout_stages[worker_id] = 0
        first_target = tactic._scout_target(worker_id, (9, -179), None)

        targets = []
        for tick in range(32):
            target = tactic._scout_target(worker_id, (9, -179), None)
            targets.append(target)
            tactic._advance_scout(
                worker_id,
                visited_target=target,
                tick=tick,
            )

        self.assertEqual(targets[0], first_target)
        self.assertGreaterEqual(
            max(abs(x - 9) + abs(y + 179) for x, y in targets),
            30,
        )
        self.assertGreaterEqual(len(set(targets)), 24)

    def test_scouting_prefers_a_less_recently_covered_chunk(self) -> None:
        tactic = CoreFarmer(beacon_policy="hold")
        worker_id = UUID(WORKER_1)
        tactic.scout_slots[worker_id] = 0
        tactic.scout_stages[worker_id] = 0
        tactic.scout_chunk_last_seen[(0, 0)] = 100

        target = tactic._scout_target(worker_id, (0, 0), None)

        self.assertEqual(target, (40, 0))

    def test_hold_policy_never_routes_core_or_scouts_toward_beacon(self) -> None:
        tactic = CoreFarmer(beacon_policy="hold")
        turn = make_turn(
            tick=200,
            resources=10,
            core_position=(-100, -100),
            beacon_position=(0, 0),
            units=[unit(WORKER_1, "WORKER", (-99, -100), cargo=0)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertNotEqual(queued["core_action"]["type"], "START_MOVE")
        self.assertNotEqual(tactic.worker_targets[UUID(WORKER_1)], (0, 0))

    def test_retreat_policy_does_not_pick_up_ground_beacon(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.startup_tick = 0
        turn = make_turn(
            tick=100,
            beacon_position=(0, 0),
            beacon_status="GROUND",
            units=[
                unit(WORKER_1, "WORKER", (5, 5), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, 5)),
                unit(RANGER_1, "RANGER", (7, 5)),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(queued.get("core_action", {}).get("type"), "PICKUP_BEACON")
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_retreat_policy_never_starts_routine_beacon_migration(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        units = [
            unit(WORKER_1, "WORKER", (5, 5), cargo=0),
            unit(VANGUARD_1, "VANGUARD", (6, 5)),
            unit(RANGER_1, "RANGER", (7, 5)),
        ]
        first = make_turn(tick=100, beacon_position=(30, 0), units=units)
        tactic.choose_actions(first)
        self.assertNotEqual(
            first.plan.model_dump(mode="json", exclude_none=True)
            .get("core_action", {})
            .get("type"),
            "START_MOVE",
        )

        ready = make_turn(tick=108, beacon_position=(30, 0), units=units)
        tactic.choose_actions(ready)
        self.assertNotEqual(
            ready.plan.model_dump(mode="json", exclude_none=True)
            .get("core_action", {})
            .get("type"),
            "START_MOVE",
        )

    def test_recovery_blocks_planned_retreat_without_visible_enemy(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        tactic.recovery_until_tick = 200
        turn = make_turn(
            tick=100,
            beacon_position=(30, 0),
            units=[
                unit(WORKER_1, "WORKER", (5, 5), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (6, 5)),
                unit(RANGER_1, "RANGER", (7, 5)),
            ],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_recovery_ends_after_time_fleet_and_stock_recover(self) -> None:
        tactic = CoreFarmer(worker_target=6, beacon_policy="pursue")
        tactic.recovery_until_tick = 100
        turn = make_turn(
            tick=100,
            resources=20,
            beacon_position=(10, 0),
            units=self._workers(6),
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertFalse(tactic.recovery_mode)
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "VANGUARD")

    def test_core_avoids_visible_vanguard_attack_cell(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(3, 0),
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (1, 1), controlled=False),
                ],
            )
        )
        self.assertNotEqual(queued["core_action"]["direction"], "RIGHT")

    def test_core_uses_immediate_defender_only_when_escape_is_blocked(self) -> None:
        queued = plan(
            make_turn(
                resources=10,
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=0)],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False),
                ],
                obstacles=[(-1, 0), (0, -1), (0, 1)],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "VANGUARD")

    def test_blocked_core_repairs_before_building_defender(self) -> None:
        queued = plan(
            make_turn(
                resources=10,
                shield=4,
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=0)],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False),
                ],
                obstacles=[(-1, 0), (0, -1), (0, 1)],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "REPAIR_SHIELD")

    def test_critical_core_heals_before_building_defender(self) -> None:
        queued = plan(
            make_turn(
                resources=10,
                core_hp=2,
                shield=0,
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=0)],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False),
                ],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "HEAL")

    def test_damaged_core_heals_before_nonurgent_actions(self) -> None:
        queued = plan(
            make_turn(
                resources=3,
                core_hp=3,
                beacon_position=(10, 0),
            )
        )
        self.assertEqual(queued["core_action"]["type"], "HEAL")

    def test_core_prequeues_heal_for_nonfatal_projected_hp_damage(self) -> None:
        queued = plan(
            make_turn(
                resources=1,
                shield=0,
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (1, 0), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )
        self.assertEqual(queued["core_action"]["type"], "HEAL")

    def test_core_does_not_prequeue_heal_when_shield_absorbs_damage(self) -> None:
        queued = plan(
            make_turn(
                resources=1,
                shield=1,
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (1, 0), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )
        self.assertNotEqual(queued["core_action"]["type"], "HEAL")

    def test_core_does_not_prequeue_heal_for_fatal_projected_damage(self) -> None:
        queued = plan(
            make_turn(
                resources=5,
                core_hp=2,
                shield=0,
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (1, 0), controlled=False),
                    unit(ENEMY_2, "VANGUARD", (0, 1), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )
        self.assertNotEqual(queued["core_action"]["type"], "HEAL")

    def test_projected_ranger_damage_respects_obstacle_line_of_fire(self) -> None:
        blocked = plan(
            make_turn(
                resources=1,
                shield=0,
                enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
                obstacles=[(2, 0)],
            ),
            beacon_policy="hold",
        )
        clear = plan(
            make_turn(
                resources=1,
                shield=0,
                enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
            ),
            beacon_policy="hold",
        )
        self.assertNotEqual(blocked["core_action"]["type"], "HEAL")
        self.assertEqual(clear["core_action"]["type"], "HEAL")

    def test_damaged_defender_heals_only_with_core_reserve(self) -> None:
        queued = plan(
            make_turn(
                resources=11,
                units=[unit(RANGER_1, "RANGER", (0, 0), hp=1)],
            ),
            beacon_policy="hold",
        )
        self.assertEqual(queued["unit_actions"][RANGER_1]["type"], "HEAL")

    def test_damaged_defender_returns_to_core_when_guard_is_preserved(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            resources=12,
            units=[
                unit(VANGUARD_1, "VANGUARD", (2, 0), hp=2),
                unit(VANGUARD_2, "VANGUARD", (0, 3)),
                unit(RANGER_1, "RANGER", (0, -2)),
            ],
        )

        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.healing_defender_ids, {UUID(VANGUARD_1)})
        self.assertEqual(
            queued["unit_actions"][VANGUARD_1],
            {"type": "MOVE", "direction": "LEFT"},
        )

    def test_healing_return_keeps_one_same_type_guard(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            resources=20,
            units=[unit(VANGUARD_1, "VANGUARD", (2, 0), hp=2)],
        )

        tactic.choose_actions(turn)

        self.assertEqual(tactic.healing_defender_ids, set())

    def test_healing_return_is_cancelled_if_same_type_guard_is_lost(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        first = make_turn(
            tick=100,
            resources=20,
            units=[
                unit(VANGUARD_1, "VANGUARD", (2, 0), hp=2),
                unit(VANGUARD_2, "VANGUARD", (0, 3)),
            ],
        )
        tactic.choose_actions(first)
        self.assertEqual(tactic.healing_defender_ids, {UUID(VANGUARD_1)})

        after_guard_loss = make_turn(
            tick=101,
            resources=20,
            units=[unit(VANGUARD_1, "VANGUARD", (1, 0), hp=2)],
        )
        tactic.choose_actions(after_guard_loss)

        self.assertEqual(tactic.healing_defender_ids, set())

    def test_healing_return_pauses_for_delivery_congestion(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            resources=20,
            units=[
                unit(WORKER_1, "WORKER", (1, 0), cargo=1),
                unit(VANGUARD_1, "VANGUARD", (2, 0), hp=2),
                unit(VANGUARD_2, "VANGUARD", (0, 3)),
            ],
        )

        tactic.choose_actions(turn)

        self.assertEqual(tactic.healing_defender_ids, set())

    def test_only_one_wounded_defender_returns_at_a_time(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            resources=30,
            units=[
                unit(VANGUARD_1, "VANGUARD", (2, 0), hp=2),
                unit(VANGUARD_2, "VANGUARD", (0, 3)),
                unit(RANGER_1, "RANGER", (-2, 0), hp=1),
                unit(RANGER_2, "RANGER", (0, -2)),
            ],
        )

        tactic.choose_actions(turn)

        self.assertEqual(len(tactic.healing_defender_ids), 1)

    def test_core_stays_after_arrival_without_cargo(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(3, 0),
                events=[
                    {
                        "event_id": "20000000-0000-4000-8000-000000000002",
                        "tick": 8,
                        "event_type": "CORE_MOVE_SUCCEEDED",
                        "actor_id": CORE_ID,
                        "position": [0, 0],
                    }
                ],
            )
        )
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_core_waits_for_nearby_cargo_worker(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(3, 0),
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=1)],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["direction"], "LEFT")
        self.assertEqual(queued["core_action"]["type"], "WAIT")

    def test_core_waits_for_single_cargo_worker_two_steps_away(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(5, 0),
                units=[unit(WORKER_1, "WORKER", (2, 0), cargo=1)],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "WAIT")

    def test_core_does_not_move_for_one_distant_cargo_worker(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(5, 0),
                units=[unit(WORKER_1, "WORKER", (4, 0), cargo=1)],
            )
        )
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_core_waits_for_bulk_cargo_within_four_steps(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(8, 0),
                units=[
                    unit(WORKER_1, "WORKER", (4, 0), cargo=1),
                    unit(WORKER_2, "WORKER", (5, 0), cargo=1),
                    unit(WORKER_3, "WORKER", (6, 0), cargo=1),
                ],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "WAIT")

    def test_core_waits_for_cargo_backlog_even_when_distant(self) -> None:
        queued = plan(
            make_turn(
                beacon_position=(8, 0),
                units=[
                    unit(WORKER_1, "WORKER", (10, 0), cargo=1),
                    unit(WORKER_2, "WORKER", (11, 0), cargo=1),
                    unit(WORKER_3, "WORKER", (12, 0), cargo=1),
                ],
            )
        )
        self.assertEqual(queued["core_action"]["type"], "WAIT")

    def test_departing_worker_and_arriving_cargo_handoff_same_tick(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                    unit(WORKER_2, "WORKER", (1, 0), cargo=1),
                ],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][WORKER_2]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][WORKER_2]["direction"], "LEFT")

    def test_delivery_handoff_breaks_guarded_core_deadlock(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        blocked = make_turn(
            tick=100,
            core_position=(0, 0),
            beacon_position=(10, 10),
            units=[
                unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                unit(WORKER_2, "WORKER", (1, 0), cargo=1),
                unit(VANGUARD_1, "VANGUARD", (0, -1)),
                unit(RANGER_1, "RANGER", (0, 1)),
            ],
            obstacles=[(-1, 0)],
        )

        tactic.choose_actions(blocked)
        first_plan = blocked.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(first_plan["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(first_plan["unit_actions"][WORKER_2]["direction"], "LEFT")
        self.assertTrue(
            any(
                first_plan["unit_actions"].get(identifier, {}).get("type") == "MOVE"
                for identifier in (VANGUARD_1, RANGER_1)
            )
        )

        depositing = make_turn(
            tick=101,
            core_position=(0, 0),
            beacon_position=(10, 10),
            units=[
                unit(WORKER_1, "WORKER", (0, -1), cargo=0),
                unit(WORKER_2, "WORKER", (0, 0), cargo=1),
                unit(VANGUARD_1, "VANGUARD", (0, -2)),
                unit(RANGER_1, "RANGER", (-1, 1)),
            ],
            obstacles=[(-1, 0)],
        )
        tactic.choose_actions(depositing)
        second_plan = depositing.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(second_plan["unit_actions"][WORKER_2]["type"], "DEPOSIT")

    def test_full_core_cargo_handoff_clears_spawn_lane(self) -> None:
        queued = plan(
            make_turn(
                resources=25,
                units=[
                    unit(WORKER_1, "WORKER", (0, 0), cargo=1),
                    unit(WORKER_2, "WORKER", (1, 0), cargo=1),
                    unit(WORKER_3, "WORKER", (1, 0), cargo=1),
                    unit(WORKER_4, "WORKER", (-1, 0), cargo=1),
                    unit(WORKER_5, "WORKER", (-1, 0), cargo=1),
                ],
                obstacles=[(0, -1), (0, 1)],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "WORKER")

    def test_visible_enemy_worker_does_not_disable_safe_delivery_handoff(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                    unit(WORKER_2, "WORKER", (1, 0), cargo=1),
                    unit(VANGUARD_1, "VANGUARD", (0, -1)),
                    unit(RANGER_1, "RANGER", (0, 1)),
                ],
                enemies=[unit(ENEMY_1, "WORKER", (20, 0), controlled=False)],
                obstacles=[(-1, 0)],
            )
        )

        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][WORKER_2]["direction"], "LEFT")

    def test_delivery_handoff_shifts_multi_unit_corridor(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        turn = make_turn(
            tick=200,
            units=[
                unit(WORKER_1, "WORKER", (0, 0), cargo=0),
                unit(WORKER_2, "WORKER", (0, -1), cargo=0),
                unit(WORKER_3, "WORKER", (1, 0), cargo=1),
                unit(WORKER_4, "WORKER", (0, 1), cargo=1),
                unit(RANGER_1, "RANGER", (0, -2)),
                unit(VANGUARD_1, "VANGUARD", (0, 2)),
            ],
            obstacles=[(-1, 0), (-1, -1), (1, -1)],
        )
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(queued["unit_actions"][RANGER_1]["direction"], "UP")
        self.assertEqual(queued["unit_actions"][WORKER_2]["direction"], "UP")
        self.assertEqual(queued["unit_actions"][WORKER_1]["direction"], "UP")
        self.assertEqual(queued["unit_actions"][WORKER_3]["direction"], "LEFT")
        self.assertEqual(
            tactic.worker_modes[UUID(WORKER_2)],
            "DELIVERY_CHAIN_CLEAR",
        )

    def test_idle_guard_moves_to_outer_post_and_leaves_core_neighbor(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(VANGUARD_1, "VANGUARD", (0, 1))],
            )
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][VANGUARD_1]["direction"], "DOWN")

    def test_full_defense_fleet_spreads_outside_core_neighbors(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (0, 1)),
                    unit(VANGUARD_2, "VANGUARD", (0, -1)),
                    unit(RANGER_1, "RANGER", (1, 0)),
                    unit(RANGER_2, "RANGER", (-1, 0)),
                ],
            ),
            beacon_policy="hold",
        )
        destinations = set()
        positions = {
            VANGUARD_1: (0, 1),
            VANGUARD_2: (0, -1),
            RANGER_1: (1, 0),
            RANGER_2: (-1, 0),
        }
        for identifier, position in positions.items():
            action = queued["unit_actions"][identifier]
            self.assertEqual(action["type"], "MOVE")
            dx, dy = Direction(action["direction"]).delta
            destination = position[0] + dx, position[1] + dy
            self.assertGreaterEqual(abs(destination[0]) + abs(destination[1]), 2)
            destinations.add(destination)
        self.assertEqual(len(destinations), 4)

    def test_non_guard_combat_units_leave_static_posts_to_patrol(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (0, 3)),
                    unit(VANGUARD_2, "VANGUARD", (0, -3)),
                    unit(RANGER_1, "RANGER", (-2, 0)),
                    unit(RANGER_2, "RANGER", (2, 0)),
                ],
            ),
            beacon_policy="hold",
        )
        self.assertIn(
            "MOVE",
            {action["type"] for action in queued["unit_actions"].values()},
        )

    def test_confirmed_isolated_core_uses_strike_group_and_keeps_guards(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(VANGUARD_3, "VANGUARD", (4, 1)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
            unit(RANGER_3, "RANGER", (2, 2)),
            unit(RANGER_4, "RANGER", (4, 3)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=20,
                units=units,
                enemies=[enemy_core(ENEMY_1, (4, 0))],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertNotEqual(queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"), "SWEEP")
        self.assertNotEqual(queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"), "SHOOT")
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][VANGUARD_3]["type"], "SWEEP")
        for ranger_id in (RANGER_2, RANGER_3, RANGER_4):
            self.assertEqual(queued["unit_actions"][ranger_id]["type"], "SHOOT")
        self.assertEqual(queued["core_action"]["type"], "SPAWN")

    def test_main_assault_rallies_once_before_launching(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        target = enemy_core(ENEMY_1, (40, 0))
        scattered = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (5, 0)),
            unit(VANGUARD_3, "VANGUARD", (30, 10)),
            unit(VANGUARD_4, "VANGUARD", (-20, -5)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (6, 0)),
            unit(RANGER_3, "RANGER", (31, 10)),
            unit(RANGER_4, "RANGER", (-19, -5)),
        ]
        first = make_turn(tick=100, units=scattered, enemies=[target])

        tactic.choose_actions(first)

        self.assertFalse(tactic.core_raid_launched)
        rally = tactic.core_raid_rally_position
        self.assertIsNotNone(rally)
        assert rally is not None

        gathered = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", rally),
            unit(VANGUARD_3, "VANGUARD", (rally[0] + 1, rally[1])),
            unit(VANGUARD_4, "VANGUARD", (rally[0] - 1, rally[1])),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (rally[0], rally[1] + 1)),
            unit(RANGER_3, "RANGER", (rally[0] + 1, rally[1] + 1)),
            unit(RANGER_4, "RANGER", (rally[0] - 1, rally[1] + 1)),
        ]
        second = make_turn(tick=101, units=gathered, enemies=[target])
        tactic.choose_actions(second)

        self.assertTrue(tactic.core_raid_launched)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_core_raid_keeps_the_same_strike_members_while_rallying(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        target = enemy_core(ENEMY_1, (40, 0))
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (5, 0)),
            unit(VANGUARD_3, "VANGUARD", (30, 10)),
            unit(VANGUARD_4, "VANGUARD", (-20, -5)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (6, 0)),
            unit(RANGER_3, "RANGER", (31, 10)),
            unit(RANGER_4, "RANGER", (-19, -5)),
        ]
        first = make_turn(tick=100, units=units, enemies=[target])
        tactic.choose_actions(first)
        locked = (
            set(tactic.core_raid_vanguard_ids),
            set(tactic.core_raid_ranger_ids),
        )

        moved = [dict(item) for item in units]
        for item in moved:
            if item["id"] in {str(value) for value in locked[1]}:
                item["position"] = [item["position"][0] - 20, item["position"][1]]
        second = make_turn(tick=101, units=moved, enemies=[target])
        tactic.choose_actions(second)

        self.assertEqual(tactic._strike_group_ids(second, tactic._select_isolated_core_target(second)), locked)

    def test_core_raid_launches_after_rally_timeout(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        target = enemy_core(ENEMY_1, (40, 0))
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (5, 0)),
            unit(VANGUARD_3, "VANGUARD", (30, 10)),
            unit(VANGUARD_4, "VANGUARD", (-20, -5)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (6, 0)),
            unit(RANGER_3, "RANGER", (31, 10)),
            unit(RANGER_4, "RANGER", (-19, -5)),
        ]
        tactic.choose_actions(make_turn(tick=100, units=units, enemies=[target]))

        timed_out = make_turn(tick=112, units=units, enemies=[target])
        tactic.choose_actions(timed_out)

        self.assertTrue(tactic.core_raid_launched)
        actions = timed_out.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        for unit_id in tactic.core_raid_vanguard_ids | tactic.core_raid_ranger_ids:
            self.assertNotEqual(actions[str(unit_id)]["type"], "WAIT")

    def test_core_target_score_avoids_protected_core_and_keeps_lock(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        near_core_id = "10000000-0000-4000-8000-000000000100"
        far_core_id = "10000000-0000-4000-8000-000000000101"
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        protected = make_turn(
            tick=100,
            units=defenders,
            enemies=[
                enemy_core(near_core_id, (10, 0)),
                enemy_core(far_core_id, (20, 0)),
                unit(
                    "10000000-0000-4000-8000-000000000102",
                    "VANGUARD",
                    (9, 0),
                    controlled=False,
                ),
                unit(
                    "10000000-0000-4000-8000-000000000103",
                    "RANGER",
                    (10, 1),
                    controlled=False,
                ),
            ],
        )

        tactic.choose_actions(protected)
        self.assertEqual(tactic.isolated_core_target_id, UUID(far_core_id))

        unlocked_competitor = make_turn(
            tick=101,
            units=defenders,
            enemies=[
                enemy_core(near_core_id, (10, 0)),
                enemy_core(far_core_id, (20, 0)),
            ],
        )
        tactic.choose_actions(unlocked_competitor)
        self.assertEqual(tactic.isolated_core_target_id, UUID(far_core_id))

    def test_revenge_core_is_preferred_when_both_targets_are_attackable(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.revenge_usernames = {"rival"}
        near_core = enemy_core(ENEMY_1, (10, 0))
        far_core = enemy_core(ENEMY_2, (20, 0))
        near_core["owner_username"] = "stranger"
        far_core["owner_username"] = "rival"
        tactic.choose_actions(
            make_turn(
                tick=100,
                units=[
                    unit(VANGUARD_1, "VANGUARD", (0, 3)),
                    unit(VANGUARD_2, "VANGUARD", (1, 0)),
                    unit(RANGER_1, "RANGER", (-2, 0)),
                    unit(RANGER_2, "RANGER", (2, 0)),
                ],
                enemies=[near_core, far_core],
            )
        )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_2))

    def test_minimum_defense_fleet_raids_exposed_core_and_keeps_guards(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[enemy_core(ENEMY_1, (4, 0))],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"),
            "SWEEP",
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"),
            "SHOOT",
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_core_confirmation_bridges_intermittent_visibility(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(WORKER_1, "WORKER", (2, 0), cargo=0),
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 2)),
        ]
        for tick in (100, 101, 102, 103, 104):
            enemies = [enemy_core(ENEMY_1, (4, 0))] if tick in {100, 102, 104} else []
            turn = make_turn(
                tick=tick,
                resources=5,
                units=units,
                enemies=enemies,
            )
            tactic.choose_actions(turn)

        sighting = tactic.enemy_core_sightings[UUID(ENEMY_1)]
        self.assertEqual(sighting.observations, 3)
        self.assertGreaterEqual(
            tactic.stationary_core_memory[UUID(ENEMY_1)].observations,
            3,
        )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(tactic.core_raid_spotter_id, UUID(WORKER_1))

    def test_visible_core_is_reacquired_after_visibility_gap(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 2)),
        ]
        for tick in (100, 101, 102, 103, 104):
            enemies = [enemy_core(ENEMY_1, (4, 0))] if tick in {100, 104} else []
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=enemies,
            )
            tactic.choose_actions(turn)

        self.assertEqual(tactic.enemy_core_sightings[UUID(ENEMY_1)].observations, 1)
        self.assertIn(UUID(ENEMY_1), tactic.stationary_core_memory)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_visible_core_target_updates_when_position_changes(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick, position in ((100, (4, 0)), (101, (5, 0))):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[] if position is None else [enemy_core(ENEMY_1, position)],
            )
            tactic.choose_actions(turn)

        sighting = tactic.enemy_core_sightings[UUID(ENEMY_1)]
        self.assertEqual(sighting.position, (5, 0))
        self.assertEqual(sighting.observations, 1)
        self.assertEqual(
            tactic.stationary_core_memory[UUID(ENEMY_1)].position,
            (5, 0),
        )
        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(
            queued["unit_actions"][RANGER_2],
            {
                "type": "SHOOT",
                "target_id": ENEMY_1,
                "expected_cell": [5, 0],
            },
        )

    def test_newly_exposing_worker_becomes_stationary_core_observer(self) -> None:
        tactic = CoreFarmer(worker_target=2, beacon_policy="hold")
        tactic.worker_history[UUID(WORKER_1)] = deque([(20, 0)])
        tactic.worker_history[UUID(WORKER_2)] = deque([(28, 0)])
        units = [
            unit(WORKER_1, "WORKER", (27, 0), cargo=0),
            unit(WORKER_2, "WORKER", (28, 0), cargo=0),
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        turn = make_turn(
            tick=100,
            resources=5,
            units=units,
            enemies=[enemy_core(ENEMY_1, (30, 0))],
        )

        tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.core_raid_spotter_id, UUID(WORKER_1))
        self.assertEqual(tactic.core_observer_target_id, UUID(ENEMY_1))
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "WAIT")
        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "CORE_OBSERVER")
        self.assertNotEqual(
            queued["unit_actions"][WORKER_2]["type"],
            "WAIT",
        )

    def test_distant_core_uses_nearby_strike_pair_and_keeps_observer(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(WORKER_1, "WORKER", (27, 0), cargo=0),
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (29, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (28, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=units,
                enemies=[enemy_core(ENEMY_1, (30, 0))],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(tactic.core_raid_spotter_id, UUID(WORKER_1))
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "WAIT")
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(
            queued["unit_actions"][RANGER_2],
            {
                "type": "SHOOT",
                "target_id": ENEMY_1,
                "expected_cell": [30, 0],
            },
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"),
            "SWEEP",
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"),
            "SHOOT",
        )

    def test_combat_pressure_keeps_core_raid_target(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (49, 0))],
                )
            )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

        pressured = make_turn(
            tick=103,
            resources=5,
            units=defenders,
            enemies=[
                enemy_core(ENEMY_1, (49, 0)),
                unit(ENEMY_2, "VANGUARD", (5, 0), controlled=False),
            ],
        )
        tactic.choose_actions(pressured)
        queued = pressured.plan.model_dump(mode="json", exclude_none=True)

        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertIsNone(tactic.stationary_unit_target_id)
        self.assertNotEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(
            queued["unit_actions"][RANGER_2],
            {
                "type": "SHOOT",
                "target_id": ENEMY_2,
                "expected_cell": [5, 0],
            },
        )

    def test_long_range_core_raid_accepts_operational_boundary(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[enemy_core(ENEMY_1, (49, 0))],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "MOVE")
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"),
            "SWEEP",
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"),
            "SHOOT",
        )

    def test_long_range_core_raid_accepts_target_beyond_old_boundary(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (50, 0))],
                )
            )

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_active_core_raid_persists_beyond_old_release_boundary(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (49, 0))],
                )
            )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

        at_release_boundary = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (-7, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (-6, 0)),
        ]
        tactic.choose_actions(
            make_turn(
                tick=103,
                resources=5,
                units=at_release_boundary,
                enemies=[enemy_core(ENEMY_1, (49, 0))],
            )
        )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

        beyond_release_boundary = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (-8, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (-7, 0)),
        ]
        tactic.choose_actions(
            make_turn(
                tick=104,
                resources=5,
                units=beyond_release_boundary,
                enemies=[enemy_core(ENEMY_1, (49, 0))],
            )
        )

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_long_range_core_raid_accepts_visible_protector(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        enemies = [
            enemy_core(ENEMY_1, (49, 0)),
            unit(ENEMY_2, "RANGER", (50, 0), controlled=False),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=enemies,
                )
            )

        self.assertFalse(tactic.combat_pressure_active)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_spotted_core_dispatches_strike_pair_within_operational_range(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(WORKER_1, "WORKER", (17, 0), cargo=0),
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (0, -3)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=units,
                enemies=[enemy_core(ENEMY_1, (20, 0))],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(tactic.core_raid_spotter_id, UUID(WORKER_1))
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "WAIT")
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "MOVE")

    def test_core_raid_continues_toward_recent_memory_without_visibility(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (20, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (21, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (30, 0))],
                )
            )

        unseen = make_turn(tick=103, resources=5, units=defenders)
        tactic.choose_actions(unseen)
        queued = unseen.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "MOVE")
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"),
            "SWEEP",
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"),
            "SHOOT",
        )

    def test_core_raid_clears_target_when_last_seen_cell_is_visible_and_empty(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (20, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (27, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (30, 0))],
                )
            )

        unseen = make_turn(tick=103, resources=5, units=defenders)
        tactic.choose_actions(unseen)
        queued = unseen.plan.model_dump(mode="json", exclude_none=True)

        self.assertIsNone(tactic.isolated_core_target_id)
        self.assertNotIn(UUID(ENEMY_1), tactic.stationary_core_memory)
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_2, {}).get("type"),
            "SHOOT",
        )

    def test_core_raid_releases_position_when_strike_group_observes_it_empty(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        distant_defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (20, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (21, 0)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=distant_defenders,
                    enemies=[enemy_core(ENEMY_1, (30, 0))],
                )
            )

        arrived = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (29, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (28, 0)),
        ]
        tactic.choose_actions(make_turn(tick=103, resources=5, units=arrived))

        self.assertIsNone(tactic.isolated_core_target_id)
        self.assertNotIn(UUID(ENEMY_1), tactic.stationary_core_memory)

    def test_exposed_core_remains_priority_near_combat_unit(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[
                    enemy_core(ENEMY_1, (4, 0)),
                    unit(ENEMY_2, "RANGER", (10, 0), controlled=False),
                ],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertIsNone(tactic.stationary_unit_target_id)
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")
        self.assertEqual(queued["core_action"]["type"], "START_MOVE")

    def test_core_raid_continues_while_own_core_is_moving_away(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (4, 0))],
                )
            )
        tactic.active_core_move_reason = "EVADE"
        moving = make_turn(
            tick=102,
            resources=5,
            core_state="MOVING",
            move_direction="LEFT",
            move_destination=(-1, 0),
            units=defenders,
            enemies=[enemy_core(ENEMY_1, (4, 0))],
        )
        tactic.choose_actions(moving)

        queued = moving.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertNotIn("core_action", queued)
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_static_worker_is_cleared_by_strike_pair_after_confirmation(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[unit(ENEMY_1, "WORKER", (4, 0), controlled=False)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.stationary_unit_target_id, UUID(ENEMY_1))
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"),
            "SWEEP",
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"),
            "SHOOT",
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_static_worker_uses_nearest_mixed_pair_with_full_defense_fleet(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (3, 1)),
            unit(VANGUARD_3, "VANGUARD", (4, 1)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 2)),
            unit(RANGER_3, "RANGER", (2, 0)),
            unit(RANGER_4, "RANGER", (4, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[unit(ENEMY_1, "WORKER", (5, 0), controlled=False)],
            )
            tactic.choose_actions(turn)

        actions = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        self.assertEqual(tactic.stationary_unit_target_id, UUID(ENEMY_1))
        self.assertEqual(actions[RANGER_4]["type"], "SHOOT")
        self.assertEqual(actions[VANGUARD_3]["type"], "MOVE")
        self.assertEqual(actions[RANGER_3]["type"], "SHOOT")
        for ranger_id in (RANGER_1, RANGER_2):
            self.assertNotEqual(actions.get(ranger_id, {}).get("type"), "SHOOT")
        for vanguard_id in (VANGUARD_1, VANGUARD_2):
            self.assertNotEqual(actions.get(vanguard_id, {}).get("type"), "SWEEP")

    def test_high_hp_static_vanguard_adds_bounded_vanguard_support(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (3, 1)),
            unit(VANGUARD_3, "VANGUARD", (4, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 2)),
            unit(RANGER_3, "RANGER", (2, 0)),
            unit(RANGER_4, "RANGER", (4, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[
                    unit(
                        ENEMY_1,
                        "VANGUARD",
                        (5, 0),
                        controlled=False,
                        hp=4,
                    )
                ],
            )
            tactic.choose_actions(turn)

        actions = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        self.assertEqual(actions[RANGER_4]["type"], "SHOOT")
        self.assertEqual(actions[VANGUARD_3]["type"], "SWEEP")
        for vanguard_id in (VANGUARD_1, VANGUARD_2):
            self.assertNotEqual(actions.get(vanguard_id, {}).get("type"), "SWEEP")
        self.assertEqual(actions[RANGER_3]["type"], "SHOOT")
        for ranger_id in (RANGER_1, RANGER_2):
            self.assertNotEqual(actions.get(ranger_id, {}).get("type"), "SHOOT")

    def test_moving_enemy_worker_remains_an_attack_target(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick, position in ((100, (4, 0)), (101, (4, 0)), (102, (5, 0))):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[unit(ENEMY_1, "WORKER", position, controlled=False)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.stationary_unit_target_id, UUID(ENEMY_1))
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_hunt_keeps_target_and_pair_when_nearer_units_appear(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        first_force = [
            unit(VANGUARD_1, "VANGUARD", (0, 1)),
            unit(VANGUARD_2, "VANGUARD", (0, -1)),
            unit(VANGUARD_3, "VANGUARD", (8, 1)),
            unit(VANGUARD_4, "VANGUARD", (20, 1)),
            unit(RANGER_1, "RANGER", (1, 0)),
            unit(RANGER_2, "RANGER", (-1, 0)),
            unit(RANGER_3, "RANGER", (8, -1)),
            unit(RANGER_4, "RANGER", (20, -1)),
        ]
        first = make_turn(
            tick=100,
            units=first_force,
            enemies=[unit(ENEMY_1, "WORKER", (10, 0), controlled=False)],
        )
        tactic.choose_actions(first)
        locked_pair = (
            set(tactic.unit_hunt_vanguard_ids),
            set(tactic.unit_hunt_ranger_ids),
        )

        second_force = [
            unit(VANGUARD_1, "VANGUARD", (0, 1)),
            unit(VANGUARD_2, "VANGUARD", (0, -1)),
            unit(VANGUARD_3, "VANGUARD", (20, 1)),
            unit(VANGUARD_4, "VANGUARD", (8, 1)),
            unit(RANGER_1, "RANGER", (1, 0)),
            unit(RANGER_2, "RANGER", (-1, 0)),
            unit(RANGER_3, "RANGER", (20, -1)),
            unit(RANGER_4, "RANGER", (8, -1)),
        ]
        second = make_turn(
            tick=101,
            units=second_force,
            enemies=[
                unit(ENEMY_1, "WORKER", (11, 0), controlled=False),
                unit(ENEMY_2, "WORKER", (9, 0), controlled=False),
            ],
        )
        tactic.choose_actions(second)

        self.assertEqual(tactic.stationary_unit_target_id, UUID(ENEMY_1))
        self.assertEqual(
            (
                tactic.unit_hunt_vanguard_ids,
                tactic.unit_hunt_ranger_ids,
            ),
            locked_pair,
        )

    def test_mixed_hunt_predicts_worker_escape_instead_of_sweeping_old_cell(
        self,
    ) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 1)),
            unit(VANGUARD_2, "VANGUARD", (4, 0)),
            unit(RANGER_1, "RANGER", (0, -1)),
            unit(RANGER_2, "RANGER", (3, 0)),
        ]
        first = make_turn(
            tick=100,
            units=defenders,
            enemies=[unit(ENEMY_1, "WORKER", (4, 0), controlled=False)],
        )
        tactic.choose_actions(first)
        moving = make_turn(
            tick=101,
            units=defenders,
            enemies=[unit(ENEMY_1, "WORKER", (5, 0), controlled=False)],
        )
        tactic.choose_actions(moving)

        actions = moving.plan.model_dump(mode="json", exclude_none=True)[
            "unit_actions"
        ]
        self.assertEqual(
            actions[VANGUARD_2],
            {"type": "MOVE", "direction": "RIGHT"},
        )
        self.assertEqual(
            actions[RANGER_2],
            {"type": "SHOOT", "expected_cell": [6, 0]},
        )

    def test_visible_combat_units_do_not_protect_each_other_from_attack(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, -2)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, -2)),
        ]
        enemies = [
            unit(ENEMY_1, "RANGER", (4, 0), controlled=False),
            unit(ENEMY_2, "VANGUARD", (6, 0), controlled=False),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=enemies,
            )
            tactic.choose_actions(turn)

        self.assertIsNotNone(tactic.stationary_unit_target_id)

    def test_unprotected_static_combat_unit_can_be_cleared(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=5,
                units=defenders,
                enemies=[unit(ENEMY_1, "RANGER", (4, 0), controlled=False)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.stationary_unit_target_id, UUID(ENEMY_1))
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_assault_pair_enters_enemy_ranger_threat_range(self) -> None:
        turn = make_turn(
            units=[
                unit(VANGUARD_1, "VANGUARD", (0, 3)),
                unit(VANGUARD_2, "VANGUARD", (0, 2)),
                unit(RANGER_1, "RANGER", (-2, 0)),
                unit(RANGER_2, "RANGER", (0, 1)),
            ],
            enemies=[unit(ENEMY_1, "RANGER", (5, 1), controlled=False)],
            obstacles=[(-1, 1)],
        )

        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.stationary_unit_target_id, UUID(ENEMY_1))
        self.assertEqual(
            queued["unit_actions"][RANGER_2],
            {"type": "MOVE", "direction": "RIGHT"},
        )

    def test_unit_assaults_use_pairs_reinforcements_and_core_guards(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        force = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (4, 1)),
            unit(VANGUARD_3, "VANGUARD", (5, 1)),
            unit(VANGUARD_4, "VANGUARD", (6, 1)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (4, -1)),
            unit(RANGER_3, "RANGER", (5, -1)),
            unit(RANGER_4, "RANGER", (6, -1)),
        ]

        single = make_turn(
            units=force,
            enemies=[unit(ENEMY_1, "WORKER", (10, 0), controlled=False)],
        )
        single_target = single.visible_enemies[0]
        vanguards, rangers = tactic._strike_group_ids(single, single_target)
        self.assertEqual((len(vanguards), len(rangers)), (1, 1))
        self.assertNotIn(UUID(VANGUARD_1), vanguards)
        self.assertNotIn(UUID(RANGER_1), rangers)

        reinforced = make_turn(
            units=force,
            enemies=[
                unit(ENEMY_1, "RANGER", (10, 0), controlled=False),
                unit(ENEMY_2, "VANGUARD", (11, 0), controlled=False),
            ],
        )
        reinforced_target = reinforced.visible_enemies[0]
        vanguards, rangers = tactic._strike_group_ids(
            reinforced,
            reinforced_target,
        )
        self.assertEqual((len(vanguards), len(rangers)), (2, 2))

        full_assault_enemies = [
            unit(
                f"10000000-0000-4000-8000-{index:012x}",
                "RANGER" if index % 2 else "VANGUARD",
                (10 + index, 0),
                controlled=False,
            )
            for index in range(1, 5)
        ]
        full_assault = make_turn(units=force, enemies=full_assault_enemies)
        full_target = full_assault.visible_enemies[0]
        vanguards, rangers = tactic._strike_group_ids(full_assault, full_target)
        self.assertEqual((len(vanguards), len(rangers)), (3, 3))
        self.assertNotIn(UUID(VANGUARD_1), vanguards)
        self.assertNotIn(UUID(RANGER_1), rangers)

    def test_core_raid_uses_four_vanguards_two_rangers_and_layers_defense(self) -> None:
        vanguards = [
            unit(
                f"00000000-0000-4000-8000-{index:012x}",
                "VANGUARD",
                (index, 0),
            )
            for index in range(1, 8)
        ]
        rangers = [
            unit(
                f"00000000-0000-4000-8000-{100 + index:012x}",
                "RANGER",
                (index, 1),
            )
            for index in range(1, 7)
        ]
        turn = make_turn(units=vanguards + rangers)
        target = CoreRaidTarget(
            id=UUID(ENEMY_1),
            position=(30, 0),
            visible_enemy=None,
        )

        guards = _core_guard_ids(turn)
        reserves = _core_reserve_ids(turn)
        selected = CoreFarmer._select_strike_group_ids(turn, target)

        self.assertEqual(tuple(map(len, selected)), (4, 2))
        self.assertTrue(guards[0].isdisjoint(selected[0]))
        self.assertTrue(guards[1].isdisjoint(selected[1]))
        self.assertTrue(reserves[0].isdisjoint(selected[0]))
        self.assertTrue(reserves[1].isdisjoint(selected[1]))

    def test_core_raid_requires_force_after_home_defense(self) -> None:
        units = [
            unit(
                f"00000000-0000-4000-8000-{index:012x}",
                "VANGUARD" if index <= 6 else "RANGER",
                (index, 0),
            )
            for index in range(1, 13)
        ]
        turn = make_turn(
            units=units,
            enemies=[enemy_core(ENEMY_1, (11, 0))],
        )
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")

        self.assertIsNone(tactic._select_isolated_core_target(turn))
        self.assertIsNone(tactic.isolated_core_target_id)

    def test_stalled_core_uses_patrol_force_after_three_ticks(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        vanguards = [
            unit(
                f"00000000-0000-4000-8000-{200 + index:012x}",
                "VANGUARD",
                (index, 0) if index <= 3 else (6 + index, 0),
            )
            for index in range(1, 6)
        ]
        rangers = [
            unit(
                f"00000000-0000-4000-8000-{300 + index:012x}",
                "RANGER",
                (index, 1) if index <= 4 else (5 + index, 1),
            )
            for index in range(1, 8)
        ]
        enemies = [
            enemy_core(ENEMY_1, (40, 0)),
            unit(ENEMY_2, "WORKER", (41, 0), controlled=False),
        ]

        for tick in (100, 101):
            turn = make_turn(tick=tick, units=vanguards + rangers, enemies=enemies)
            tactic.choose_actions(turn)
            self.assertIsNone(tactic.isolated_core_target_id)

        turn = make_turn(tick=102, units=vanguards + rangers, enemies=enemies)
        tactic.choose_actions(turn)

        guards = _core_guard_ids(turn)
        reserves = _core_reserve_ids(turn)
        strike_vanguards = {
            unit.id for unit in turn.vanguards
        } - guards[0] - reserves[0]
        strike_rangers = {
            unit.id for unit in turn.rangers
        } - guards[1] - reserves[1]
        queued = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertTrue(tactic.core_raid_stalled)
        self.assertTrue(tactic.core_raid_launched)
        self.assertEqual(tactic.core_raid_vanguard_ids, strike_vanguards)
        self.assertEqual(tactic.core_raid_ranger_ids, strike_rangers)
        for unit_id in strike_vanguards | strike_rangers:
            self.assertEqual(queued[str(unit_id)]["type"], "MOVE")
        for unit_id in guards[0] | guards[1] | reserves[0] | reserves[1]:
            self.assertNotIn(unit_id, strike_vanguards | strike_rangers)

    def test_moving_unit_near_stalled_core_blocks_patrol_attack(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(
                f"00000000-0000-4000-8000-{400 + index:012x}",
                "VANGUARD" if index <= 5 else "RANGER",
                (index, 0),
            )
            for index in range(1, 13)
        ]
        for tick, worker_position in (
            (100, (41, 0)),
            (101, (41, 0)),
            (102, (42, 0)),
        ):
            turn = make_turn(
                tick=tick,
                units=units,
                enemies=[
                    enemy_core(ENEMY_1, (40, 0)),
                    unit(ENEMY_2, "WORKER", worker_position, controlled=False),
                ],
            )
            tactic.choose_actions(turn)

        self.assertEqual(
            tactic.enemy_unit_sightings[UUID(ENEMY_2)].observations,
            1,
        )
        self.assertIsNone(tactic.isolated_core_target_id)

    def test_empty_stalled_core_is_attackable_after_three_ticks(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(
                f"00000000-0000-4000-8000-{500 + index:012x}",
                "VANGUARD" if index <= 5 else "RANGER",
                (index, 0),
            )
            for index in range(1, 13)
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                units=units,
                enemies=[enemy_core(ENEMY_1, (40, 0))],
            )
            tactic.choose_actions(turn)

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertTrue(tactic.core_raid_stalled)

    def test_stalled_core_can_be_attacked_by_one_available_unit_type(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, 1)),
            unit(RANGER_1, "RANGER", (0, 2)),
            unit(RANGER_2, "RANGER", (8, 0)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                units=units,
                enemies=[enemy_core(ENEMY_1, (40, 0))],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(tactic.core_raid_vanguard_ids, set())
        self.assertEqual(tactic.core_raid_ranger_ids, {UUID(RANGER_2)})
        self.assertEqual(queued[RANGER_2]["type"], "MOVE")

    def test_stalled_core_confirmation_survives_short_visibility_gaps(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, 1)),
            unit(RANGER_1, "RANGER", (0, 2)),
            unit(RANGER_2, "RANGER", (38, 4)),
        ]
        enemies = [
            enemy_core(ENEMY_1, (40, 0)),
            unit(ENEMY_2, "WORKER", (40, 0), controlled=False),
        ]
        for tick in (100, 101, 102, 103, 104):
            turn = make_turn(
                tick=tick,
                units=units,
                enemies=enemies if tick in {100, 102, 104} else [],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertTrue(tactic.core_raid_stalled)
        self.assertEqual(
            tactic.enemy_unit_sightings[UUID(ENEMY_2)].observations,
            3,
        )
        self.assertEqual(queued[RANGER_2]["type"], "MOVE")

    def test_patrol_ranger_holds_enemy_core_in_view_while_confirming(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, 1)),
            unit(RANGER_1, "RANGER", (0, 2)),
            unit(RANGER_2, "RANGER", (39, 4)),
        ]
        enemies = [
            enemy_core(ENEMY_1, (40, 0)),
            unit(ENEMY_2, "WORKER", (40, 0), controlled=False),
        ]

        first = make_turn(tick=100, units=units, enemies=enemies)
        tactic.choose_actions(first)
        first_actions = first.plan.model_dump(
            mode="json",
            exclude_none=True,
        )["unit_actions"]

        self.assertEqual(tactic.core_raid_spotter_id, UUID(RANGER_2))
        self.assertEqual(tactic.core_observer_target_id, UUID(ENEMY_1))
        self.assertEqual(first_actions[RANGER_2]["type"], "WAIT")

        second = make_turn(tick=101, units=units, enemies=enemies)
        tactic.choose_actions(second)
        second_actions = second.plan.model_dump(
            mode="json",
            exclude_none=True,
        )["unit_actions"]
        self.assertEqual(second_actions[RANGER_2]["type"], "WAIT")

        third = make_turn(tick=102, units=units, enemies=enemies)
        tactic.choose_actions(third)
        third_actions = third.plan.model_dump(
            mode="json",
            exclude_none=True,
        )["unit_actions"]
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(third_actions[RANGER_2]["type"], "MOVE")

    def test_nearby_ranger_launches_stalled_raid_with_distant_vanguard(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        vanguards = [
            unit(
                f"00000000-0000-4000-8000-{600 + index:012x}",
                "VANGUARD",
                (index, 0),
            )
            for index in range(1, 6)
        ]
        rangers = [
            unit(
                f"00000000-0000-4000-8000-{700 + index:012x}",
                "RANGER",
                (index, 1) if index < 7 else (39, 4),
            )
            for index in range(1, 8)
        ]
        enemies = [
            enemy_core(ENEMY_1, (40, 0)),
            unit(ENEMY_2, "WORKER", (40, 0), controlled=False),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                units=vanguards + rangers,
                enemies=enemies,
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
        nearby_ranger_id = str(rangers[-1]["id"])
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertTrue(tactic.core_raid_stalled)
        self.assertEqual(queued[nearby_ranger_id]["type"], "MOVE")

    def test_unit_assault_falls_back_to_same_type_pair(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            units=[
                unit(VANGUARD_1, "VANGUARD", (0, 3)),
                unit(RANGER_1, "RANGER", (-2, 0)),
                unit(RANGER_2, "RANGER", (4, -1)),
                unit(RANGER_3, "RANGER", (5, -1)),
            ],
            enemies=[unit(ENEMY_1, "WORKER", (10, 0), controlled=False)],
        )

        vanguards, rangers = tactic._strike_group_ids(
            turn,
            turn.visible_enemies[0],
        )

        self.assertEqual(vanguards, set())
        self.assertEqual(rangers, {UUID(RANGER_2), UUID(RANGER_3)})

    def test_core_hunt_does_not_require_free_capture_capacity(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(VANGUARD_3, "VANGUARD", (4, 1)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
            unit(RANGER_3, "RANGER", (2, 2)),
            unit(RANGER_4, "RANGER", (4, 3)),
        ]
        for tick in (100, 101, 102):
            turn = make_turn(
                tick=tick,
                resources=30,
                units=defenders,
                enemies=[enemy_core(ENEMY_1, (4, 0))],
            )
            tactic.choose_actions(turn)

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_capture_healing_and_spawn_prices_are_logged_structurally(self) -> None:
        turn = make_turn(
            resources=15,
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-000000000020",
                    "tick": 8,
                    "event_type": "CORE_RESOURCES_CAPTURED",
                    "values": {
                        "amount": 7,
                        "available": 9,
                        "destroyed": 2,
                        "capacity": 20,
                    },
                },
                {
                    "event_id": "20000000-0000-4000-8000-000000000021",
                    "tick": 8,
                    "event_type": "CORE_HEAL_SUCCEEDED",
                    "values": {"amount": 2, "hp": 5, "cost": 2},
                },
                {
                    "event_id": "20000000-0000-4000-8000-000000000022",
                    "tick": 8,
                    "event_type": "CORE_SPAWN_SUCCEEDED",
                    "values": {"unit_type": "RANGER", "cost": 16},
                },
                {
                    "event_id": "20000000-0000-4000-8000-000000000023",
                    "tick": 8,
                    "event_type": "CORE_SPAWN_FAILED",
                    "reason_code": "INSUFFICIENT_RESOURCES",
                    "values": {"required": 16},
                },
            ],
        )
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(turn)
        diagnostics = _position_diagnostics(turn, tactic)

        self.assertIn("captured_resources=7", diagnostics)
        self.assertIn("capture_destroyed=2", diagnostics)
        self.assertIn("core_healed=2", diagnostics)
        self.assertIn("spawn_cost=16", diagnostics)
        self.assertIn("spawn_required=16", diagnostics)
        self.assertIn("next_worker_cost=5", diagnostics)
        self.assertIn("next_vanguard_cost=10", diagnostics)
        self.assertIn("next_ranger_cost=12", diagnostics)
        self.assertIn("projected_core_damage=0", diagnostics)
        self.assertIn("core_survival_margin=5", diagnostics)
        self.assertIn("global_posture=NORMAL", diagnostics)
        self.assertIn("threat_level=NORMAL", diagnostics)
        self.assertIn("threat_reason=NONE", diagnostics)

    def test_nearby_combat_unit_does_not_cancel_core_raid(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        units = [
            unit(VANGUARD_1, "VANGUARD", (0, -3)),
            unit(VANGUARD_2, "VANGUARD", (3, 0)),
            unit(VANGUARD_3, "VANGUARD", (4, 1)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
            unit(RANGER_3, "RANGER", (2, 2)),
            unit(RANGER_4, "RANGER", (4, 3)),
        ]
        for tick in (100, 101, 102):
            enemies = [enemy_core(ENEMY_1, (4, 0))]
            if tick == 102:
                enemies.append(
                    unit(ENEMY_2, "RANGER", (5, 1), controlled=False)
                )
            turn = make_turn(
                tick=tick,
                resources=20,
                units=units,
                enemies=enemies,
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(VANGUARD_1, {}).get("type"),
            "SWEEP",
        )
        self.assertNotEqual(
            queued.get("unit_actions", {}).get(RANGER_1, {}).get("type"),
            "SHOOT",
        )

    def test_stationary_core_memory_discourages_repeated_route(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (0, -3)),
            unit(VANGUARD_3, "VANGUARD", (1, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 3)),
            unit(RANGER_3, "RANGER", (0, 2)),
            unit(RANGER_4, "RANGER", (2, -2)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=20,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (2, 0))],
                )
            )

        unseen = make_turn(
            tick=103,
            core_position=(10, 10),
            units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
            resource_cells=[(4, 0)],
        )
        tactic.choose_actions(unseen)
        queued = unseen.plan.model_dump(mode="json", exclude_none=True)

        self.assertIn(UUID(ENEMY_1), tactic.stationary_core_memory)
        self.assertNotEqual(
            queued["unit_actions"][WORKER_1]["direction"],
            "RIGHT",
        )

        reacquired = make_turn(
            tick=104,
            resources=20,
            units=defenders,
            enemies=[enemy_core(ENEMY_1, (2, 0))],
        )
        tactic.choose_actions(reacquired)
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_worker_limit_matches_aggressive_force_plan(self) -> None:
        CoreFarmer(worker_target=18)
        with self.assertRaisesRegex(ValueError, "between 1 and 18"):
            CoreFarmer(worker_target=19)

    def test_population_hard_stops_at_48_without_self_destruct(self) -> None:
        units = [
            unit(
                f"20000000-0000-4000-8000-{index:012x}",
                (
                    "WORKER"
                    if index < 18
                    else "VANGUARD"
                    if index < 32
                    else "RANGER"
                ),
                (20 + index, 20),
                cargo=0 if index < 18 else None,
            )
            for index in range(48)
        ]
        turn = make_turn(resources=200, units=units)
        tactic = CoreFarmer(worker_target=18, beacon_policy="hold")
        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertNotEqual(
            queued.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertTrue(
            all(
                action.get("type") != "SELF_DESTRUCT"
                for action in queued.get("unit_actions", {}).values()
            )
        )

    def test_force_stage_order(self) -> None:
        cases = (
            ((0, 0, 0), UnitType.WORKER),
            ((8, 0, 0), UnitType.VANGUARD),
            ((8, 1, 0), UnitType.RANGER),
            ((8, 1, 1), UnitType.WORKER),
            ((12, 1, 1), UnitType.RANGER),
            ((12, 3, 4), UnitType.WORKER),
            ((18, 6, 8), UnitType.VANGUARD),
            ((18, 14, 16), None),
        )
        for counts, expected in cases:
            with self.subTest(counts=counts):
                self.assertIs(
                    _next_force_unit_type(18, *counts),
                    expected,
                )

    def test_emergency_defenders_use_dynamic_price_preview(self) -> None:
        vanguard_turn = dict(
            units=self._workers(6),
            enemies=[unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False)],
            obstacles=[(-1, 0), (0, -1), (0, 1)],
        )
        ranger_turn = dict(
            units=self._workers(6),
            enemies=[unit(ENEMY_1, "RANGER", (5, 0), controlled=False)],
            obstacles=[(-1, 0), (1, 0), (0, -1), (0, 1)],
        )
        with patch("arena_farmer.unit_cost", return_value=30):
            vanguard_wait = plan(make_turn(resources=29, **vanguard_turn))
            vanguard_spawn = plan(make_turn(resources=30, **vanguard_turn))
            ranger_wait = plan(make_turn(resources=29, **ranger_turn))
            ranger_spawn = plan(make_turn(resources=30, **ranger_turn))

        self.assertNotEqual(
            vanguard_wait.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(vanguard_spawn["core_action"]["unit_type"], "VANGUARD")
        self.assertNotEqual(
            ranger_wait.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(ranger_spawn["core_action"]["unit_type"], "RANGER")

    def test_four_workers_accumulate_before_expanding_to_six(self) -> None:
        workers = [
            unit(WORKER_1, "WORKER", (1, 0), cargo=0),
            unit(WORKER_2, "WORKER", (0, 1), cargo=0),
            unit(WORKER_3, "WORKER", (-1, 0), cargo=0),
            unit(WORKER_4, "WORKER", (0, -1), cargo=0),
        ]
        accumulating = plan(make_turn(resources=14, units=workers))
        self.assertNotIn("core_action", accumulating)

        expanding = plan(make_turn(resources=15, units=workers))
        self.assertEqual(expanding["core_action"]["type"], "SPAWN")
        self.assertEqual(expanding["core_action"]["unit_type"], "WORKER")

    def test_six_workers_continue_worker_growth(self) -> None:
        workers = self._workers(6)
        accumulating = plan(make_turn(resources=14, units=workers))
        self.assertNotEqual(
            accumulating.get("core_action", {}).get("unit_type"),
            "WORKER",
        )

        expanding = plan(make_turn(resources=15, units=workers))
        self.assertEqual(expanding["core_action"]["type"], "SPAWN")
        self.assertEqual(expanding["core_action"]["unit_type"], "WORKER")

    def test_seven_workers_finish_initial_worker_growth(self) -> None:
        workers = self._workers(7)
        accumulating = plan(make_turn(resources=14, units=workers))
        self.assertNotEqual(
            accumulating.get("core_action", {}).get("unit_type"),
            "WORKER",
        )

        expanding = plan(make_turn(resources=15, units=workers))
        self.assertEqual(expanding["core_action"]["type"], "SPAWN")
        self.assertEqual(expanding["core_action"]["unit_type"], "WORKER")

    def test_eight_workers_balance_the_first_combat_wave(self) -> None:
        workers = self._workers(8)
        accumulating = plan(make_turn(resources=24, units=workers))
        self.assertEqual(accumulating["core_action"]["unit_type"], "VANGUARD")

        first_vanguard = plan(make_turn(resources=25, units=workers))
        self.assertEqual(first_vanguard["core_action"]["unit_type"], "VANGUARD")

        vanguard = unit(VANGUARD_1, "VANGUARD", (3, 0))
        first_ranger = plan(make_turn(resources=27, units=workers + [vanguard]))
        self.assertEqual(first_ranger["core_action"]["unit_type"], "RANGER")

        ranger = unit(RANGER_1, "RANGER", (4, 0))
        worker_expansion = plan(
            make_turn(resources=25, units=workers + [vanguard, ranger])
        )
        self.assertEqual(worker_expansion["core_action"]["unit_type"], "WORKER")

    def test_early_defense_uses_dynamic_price_preview(self) -> None:
        workers = self._workers(8)
        vanguard = unit(VANGUARD_1, "VANGUARD", (3, 0))
        prices = {
            UnitType.WORKER: 30,
            UnitType.VANGUARD: 40,
            UnitType.RANGER: 50,
        }
        with patch(
            "arena_farmer.unit_cost",
            side_effect=lambda kind, _population: prices[kind],
        ):
            vanguard_wait = plan(make_turn(resources=39, units=workers))
            vanguard_spawn = plan(make_turn(resources=40, units=workers))
            ranger_wait = plan(
                make_turn(resources=44, units=workers + [vanguard])
            )
            ranger_spawn = plan(
                make_turn(resources=45, units=workers + [vanguard])
            )

        self.assertNotEqual(
            vanguard_wait.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(vanguard_spawn["core_action"]["unit_type"], "VANGUARD")
        self.assertNotEqual(
            ranger_wait.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(ranger_spawn["core_action"]["unit_type"], "RANGER")

    def test_second_force_stage_keeps_10_resource_core_reserve(self) -> None:
        workers = self._workers(12)
        early_fleet = [
            unit(VANGUARD_1, "VANGUARD", (3, 0)),
            unit(RANGER_1, "RANGER", (4, 0)),
        ]
        with patch("arena_farmer.unit_cost", return_value=30):
            accumulating = plan(make_turn(resources=39, units=workers + early_fleet))
            expanding = plan(make_turn(resources=40, units=workers + early_fleet))
        self.assertNotEqual(
            accumulating.get("core_action", {}).get("unit_type"),
            "RANGER",
        )
        self.assertEqual(expanding["core_action"]["type"], "SPAWN")
        self.assertEqual(expanding["core_action"]["unit_type"], "RANGER")

    def test_mature_fleet_completes_first_wave_before_late_wave(self) -> None:
        workers = self._workers(12)
        first_vanguard = unit(VANGUARD_1, "VANGUARD", (3, 0))
        second_vanguard = unit(VANGUARD_2, "VANGUARD", (4, 0))
        third_vanguard = unit(VANGUARD_3, "VANGUARD", (4, 1))
        first_ranger = unit(RANGER_1, "RANGER", (5, 0))

        second_vanguard_plan = plan(
            make_turn(
                resources=25,
                units=workers + [first_vanguard, first_ranger],
            )
        )
        self.assertEqual(
            second_vanguard_plan["core_action"]["unit_type"],
            "RANGER",
        )

        third_vanguard_plan = plan(
            make_turn(
                resources=25,
                units=workers
                + [first_vanguard, second_vanguard, first_ranger],
            )
        )
        self.assertEqual(third_vanguard_plan["core_action"]["unit_type"], "RANGER")

        second_ranger_plan = plan(
            make_turn(
                resources=27,
                units=workers
                + [
                    first_vanguard,
                    second_vanguard,
                    third_vanguard,
                    first_ranger,
                ],
            )
        )
        self.assertEqual(second_ranger_plan["core_action"]["unit_type"], "RANGER")

    def test_mature_defense_uses_dynamic_price_preview(self) -> None:
        workers = self._workers(12)
        vanguards = [
            unit(VANGUARD_1, "VANGUARD", (3, 0)),
            unit(VANGUARD_2, "VANGUARD", (4, 0)),
            unit(VANGUARD_3, "VANGUARD", (5, 0)),
        ]
        rangers = [
            unit(RANGER_1, "RANGER", (6, 0)),
            unit(RANGER_2, "RANGER", (7, 0)),
            unit(RANGER_3, "RANGER", (8, 0)),
            unit(RANGER_4, "RANGER", (9, 0)),
        ]
        with patch("arena_farmer.unit_cost", return_value=30):
            accumulating = plan(
                make_turn(resources=39, units=workers + vanguards[:2] + rangers),
                beacon_policy="hold",
            )
            spawning = plan(
                make_turn(resources=40, units=workers + vanguards[:2] + rangers),
                beacon_policy="hold",
            )

        self.assertNotEqual(
            accumulating.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(spawning["core_action"]["unit_type"], "VANGUARD")

        with patch("arena_farmer.unit_cost", return_value=30):
            ranger_wait = plan(
                make_turn(
                    resources=39,
                    units=workers + vanguards + rangers[:3],
                ),
                beacon_policy="hold",
            )
            ranger_spawn = plan(
                make_turn(
                    resources=40,
                    units=workers + vanguards + rangers[:3],
                ),
                beacon_policy="hold",
            )

        self.assertNotEqual(
            ranger_wait.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(ranger_spawn["core_action"]["unit_type"], "RANGER")

    def test_defense_fleet_leaves_core_spawn_cell(self) -> None:
        workers = [
            unit(WORKER_1, "WORKER", (3, 3), cargo=0),
            unit(WORKER_2, "WORKER", (4, 3), cargo=0),
            unit(WORKER_3, "WORKER", (5, 3), cargo=0),
            unit(WORKER_4, "WORKER", (3, 4), cargo=0),
            unit(WORKER_5, "WORKER", (4, 4), cargo=0),
            unit(WORKER_6, "WORKER", (5, 4), cargo=0),
            unit(WORKER_7, "WORKER", (3, 5), cargo=0),
            unit(WORKER_8, "WORKER", (4, 5), cargo=0),
            unit(WORKER_9, "WORKER", (5, 5), cargo=0),
            unit(WORKER_10, "WORKER", (6, 3), cargo=0),
            unit(WORKER_11, "WORKER", (6, 4), cargo=0),
            unit(WORKER_12, "WORKER", (6, 5), cargo=0),
        ]
        queued = plan(
            make_turn(
                resources=25,
                units=workers
                + [
                    unit(VANGUARD_1, "VANGUARD", (0, 0)),
                    unit(RANGER_1, "RANGER", (0, 0)),
                ],
            )
        )

        self.assertEqual(queued["unit_actions"][VANGUARD_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][RANGER_1]["type"], "MOVE")
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "RANGER")

    def test_nearby_enemy_blocks_worker_expansion(self) -> None:
        queued = plan(
            make_turn(
                resources=30,
                units=self._workers(6),
                enemies=[
                    unit(ENEMY_1, "RANGER", (6, 0), controlled=False),
                ],
            )
        )
        self.assertNotEqual(
            queued.get("core_action", {}).get("unit_type"),
            "WORKER",
        )

    def test_full_core_worker_moves_out_to_clear_spawn_lane(self) -> None:
        queued = plan(
            make_turn(
                resources=10,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=1)],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")

    def test_mature_fleet_reserves_core_cell_for_affordable_spawn(self) -> None:
        workers = self._workers(20)
        workers[0] = unit(WORKER_1, "WORKER", (0, 0), cargo=1)
        queued = plan(
            make_turn(
                resources=75,
                units=workers
                + [
                    unit(VANGUARD_1, "VANGUARD", (3, 0)),
                    unit(VANGUARD_2, "VANGUARD", (4, 0)),
                    unit(VANGUARD_3, "VANGUARD", (5, 0)),
                    unit(RANGER_1, "RANGER", (0, 3)),
                    unit(RANGER_2, "RANGER", (0, 4)),
                    unit(RANGER_3, "RANGER", (0, 5)),
                    unit(RANGER_4, "RANGER", (0, 6)),
                ],
            )
        )

        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "VANGUARD")

    def test_spawn_reservation_blocks_new_cargo_delivery(self) -> None:
        workers = self._workers(20)
        workers[0] = unit(WORKER_1, "WORKER", (1, 0), cargo=1)
        queued = plan(
            make_turn(
                resources=75,
                units=workers
                + [
                    unit(VANGUARD_1, "VANGUARD", (3, 0)),
                    unit(VANGUARD_2, "VANGUARD", (4, 0)),
                    unit(VANGUARD_3, "VANGUARD", (5, 0)),
                    unit(RANGER_1, "RANGER", (0, 3)),
                    unit(RANGER_2, "RANGER", (0, 4)),
                    unit(RANGER_3, "RANGER", (0, 5)),
                    unit(RANGER_4, "RANGER", (0, 6)),
                ],
            )
        )

        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertNotEqual(
            queued["unit_actions"][WORKER_1],
            {"type": "MOVE", "direction": "LEFT"},
        )

    def test_departing_worker_frees_core_spawn_slot(self) -> None:
        queued = plan(
            make_turn(
                resources=10,
                units=[unit(WORKER_1, "WORKER", (0, 0), cargo=0)],
                resource_cells=[(2, 0)],
            )
        )
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["core_action"]["type"], "SPAWN")

    def test_farmer_keeps_five_resource_reserve_before_spawning(self) -> None:
        queued = plan(
            make_turn(
                resources=5,
                units=[unit(WORKER_1, "WORKER", (1, 0), cargo=0)],
            )
        )
        self.assertNotIn("core_action", queued)

    def test_defenders_counterattack_during_core_pressure_with_open_routes(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (1, 0)),
                    unit(RANGER_1, "RANGER", (0, 2)),
                ],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False),
                    unit(ENEMY_2, "RANGER", (0, 4), controlled=False),
                ],
            )
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_1]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_1]["type"], "SHOOT")

    def test_vanguard_counterattacks_immediate_core_threat_before_retreat(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(VANGUARD_1, "VANGUARD", (0, 0))],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (1, 0), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(
            queued["unit_actions"][VANGUARD_1],
            {"type": "SWEEP", "direction": "RIGHT"},
        )

    def test_ranger_counterattacks_clear_core_threat_before_retreat(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(RANGER_1, "RANGER", (0, 0))],
                enemies=[
                    unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(queued["unit_actions"][RANGER_1]["type"], "SHOOT")
        self.assertEqual(queued["unit_actions"][RANGER_1]["target_id"], ENEMY_1)
        self.assertEqual(queued["unit_actions"][RANGER_1]["expected_cell"], [3, 0])

    def test_vanguard_immediately_attacks_adjacent_enemy_worker(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(VANGUARD_1, "VANGUARD", (1, 0))],
                enemies=[unit(ENEMY_1, "WORKER", (2, 0), controlled=False)],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(
            queued["unit_actions"][VANGUARD_1],
            {"type": "SWEEP", "direction": "RIGHT"},
        )

    def test_ranger_attacks_enemy_worker_during_recovery(self) -> None:
        event = {
            "event_id": "20000000-0000-4000-8000-000000000031",
            "tick": 99,
            "event_type": "CORE_RESPAWNED",
            "actor_id": CORE_ID,
            "position": [0, 0],
        }
        queued = plan(
            make_turn(
                tick=100,
                units=[unit(RANGER_1, "RANGER", (0, 0))],
                enemies=[unit(ENEMY_1, "WORKER", (3, 0), controlled=False)],
                events=[event],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(queued["unit_actions"][RANGER_1]["type"], "SHOOT")
        self.assertEqual(queued["unit_actions"][RANGER_1]["target_id"], ENEMY_1)

    def test_obstacle_blocks_core_threat_counterattack(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(RANGER_1, "RANGER", (0, 0))],
                enemies=[
                    unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
                ],
                obstacles=[(1, 0)],
            ),
            beacon_policy="hold",
        )

        self.assertNotEqual(queued["unit_actions"][RANGER_1]["type"], "SHOOT")

    def test_defenders_attack_only_when_escape_is_blocked(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (1, 0)),
                    unit(RANGER_1, "RANGER", (0, -1)),
                ],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (2, 0), controlled=False),
                    unit(ENEMY_2, "RANGER", (0, 2), controlled=False),
                ],
                obstacles=[(1, -1), (1, 1), (0, -2), (-1, -1)],
            )
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_1]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_1]["type"], "SHOOT")
        self.assertEqual(queued["unit_actions"][RANGER_1]["target_id"], ENEMY_2)
        self.assertEqual(queued["unit_actions"][RANGER_1]["expected_cell"], [0, 2])

    def test_pursuing_enemy_is_counterattacked_while_core_retreats(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="retreat")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (4, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        first = make_turn(
            tick=100,
            beacon_position=(10, 0),
            units=defenders,
            enemies=[unit(ENEMY_1, "RANGER", (6, 0), controlled=False)],
        )
        tactic.choose_actions(first)

        chasing = make_turn(
            tick=101,
            beacon_position=(10, 0),
            units=defenders,
            enemies=[unit(ENEMY_1, "RANGER", (5, 0), controlled=False)],
        )
        tactic.choose_actions(chasing)
        queued = chasing.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")
        self.assertEqual(queued["core_action"]["type"], "START_MOVE")

    def test_pursuit_survives_one_tick_visibility_gap(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        for tick, enemies in (
            (100, [unit(ENEMY_1, "RANGER", (6, 0), controlled=False)]),
            (101, [unit(ENEMY_1, "RANGER", (5, 0), controlled=False)]),
            (102, []),
        ):
            tactic.choose_actions(make_turn(tick=tick, enemies=enemies))

        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})
        self.assertTrue(tactic.combat_pressure_active)

        reacquired = make_turn(
            tick=103,
            enemies=[unit(ENEMY_1, "RANGER", (4, 0), controlled=False)],
        )
        tactic.choose_actions(reacquired)
        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})

    def test_activity_alert_outlives_pursuit_for_two_hidden_ticks(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        for tick, enemies in (
            (100, [unit(ENEMY_1, "RANGER", (6, 0), controlled=False)]),
            (101, [unit(ENEMY_1, "RANGER", (5, 0), controlled=False)]),
            (102, []),
            (103, []),
        ):
            tactic.choose_actions(make_turn(tick=tick, enemies=enemies))

        self.assertEqual(tactic.pursuing_enemy_ids, set())
        self.assertTrue(tactic.combat_pressure_active)

        tactic.choose_actions(make_turn(tick=104, enemies=[]))
        self.assertEqual(tactic.active_enemy_ids, set())
        self.assertFalse(tactic.combat_pressure_active)

    def test_distant_pursuit_requires_two_approach_observations(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                enemies=[unit(ENEMY_1, "RANGER", (20, 0), controlled=False)],
            )
        )
        tactic.choose_actions(
            make_turn(
                tick=101,
                enemies=[unit(ENEMY_1, "RANGER", (19, 0), controlled=False)],
            )
        )
        self.assertEqual(tactic.pursuing_enemy_ids, set())
        self.assertEqual(tactic.active_enemy_ids, {UUID(ENEMY_1)})
        self.assertEqual(tactic.preemptive_evade_enemy_ids, {UUID(ENEMY_1)})
        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.PRE_EVADE)
        self.assertEqual(tactic.threat_assessment.primary_reason, "TIME_TO_RANGE")
        self.assertEqual(
            tactic.last_retreat_direction is not None,
            True,
        )

        tactic.choose_actions(
            make_turn(
                tick=102,
                enemies=[unit(ENEMY_1, "RANGER", (18, 0), controlled=False)],
            )
        )
        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})
        self.assertTrue(tactic.combat_pressure_active)

    def test_distant_lateral_activity_alerts_without_moving_core(self) -> None:
        tactic = CoreFarmer(worker_target=2, beacon_policy="hold")
        workers = [unit(WORKER_1, "WORKER", (5, 5), cargo=0)]
        tactic.choose_actions(
            make_turn(
                tick=100,
                resources=50,
                units=workers,
                enemies=[unit(ENEMY_1, "RANGER", (20, 0), controlled=False)],
            )
        )
        alerted = make_turn(
            tick=101,
            resources=50,
            units=workers,
            enemies=[unit(ENEMY_1, "RANGER", (20, 1), controlled=False)],
        )

        tactic.choose_actions(alerted)
        queued = alerted.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.active_enemy_ids, {UUID(ENEMY_1)})
        self.assertEqual(tactic.preemptive_evade_enemy_ids, set())
        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.ALERT)
        self.assertEqual(
            tactic.threat_assessment.primary_reason,
            "HOSTILE_ACTIVITY",
        )
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.ALERT,
        )
        self.assertEqual(queued["core_action"]["type"], "SPAWN")

    def test_distant_stationary_enemy_does_not_pause_production(self) -> None:
        tactic = CoreFarmer(worker_target=2, beacon_policy="hold")
        workers = [unit(WORKER_1, "WORKER", (5, 5), cargo=0)]
        for tick in (100, 101):
            turn = make_turn(
                tick=tick,
                resources=50,
                units=workers,
                enemies=[unit(ENEMY_1, "RANGER", (20, 0), controlled=False)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.active_enemy_ids, set())
        self.assertFalse(tactic.combat_pressure_active)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.NORMAL)
        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "WORKER")

    def test_recovery_worker_uses_dynamic_price_preview(self) -> None:
        event = {
            "event_id": "20000000-0000-4000-8000-000000000030",
            "tick": 99,
            "event_type": "CORE_RESPAWNED",
            "actor_id": CORE_ID,
            "position": [-100, -100],
        }
        turn_fields = dict(
            tick=100,
            core_position=(-100, -100),
            beacon_position=(0, 0),
            units=[unit(WORKER_1, "WORKER", (-100, -100), cargo=0)],
            events=[event],
        )
        with patch("arena_farmer.unit_cost", return_value=9):
            waiting = plan(make_turn(resources=8, **turn_fields))
            spawning = plan(make_turn(resources=9, **turn_fields))

        self.assertNotEqual(
            waiting.get("core_action", {}).get("type"),
            "SPAWN",
        )
        self.assertEqual(spawning["core_action"]["unit_type"], "WORKER")

    def test_recovery_rebuilds_to_eight_workers_before_combat_units(self) -> None:
        event = {
            "event_id": "20000000-0000-4000-8000-000000000032",
            "tick": 99,
            "event_type": "CORE_RESPAWNED",
            "actor_id": CORE_ID,
            "position": [0, 0],
        }
        turn = make_turn(
            tick=100,
            resources=5,
            units=self._workers(6)
            + [
                unit(VANGUARD_1, "VANGUARD", (20, 0)),
                unit(VANGUARD_2, "VANGUARD", (21, 0)),
                unit(RANGER_1, "RANGER", (22, 0)),
            ],
            events=[event],
        )

        CoreFarmer(worker_target=18, beacon_policy="hold").choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(queued["core_action"]["type"], "SPAWN")
        self.assertEqual(queued["core_action"]["unit_type"], "WORKER")

    def test_activity_alert_survives_two_complete_hidden_ticks(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        for tick, enemies in (
            (100, [unit(ENEMY_1, "RANGER", (20, 0), controlled=False)]),
            (101, [unit(ENEMY_1, "RANGER", (20, 1), controlled=False)]),
            (102, []),
            (103, []),
        ):
            tactic.choose_actions(make_turn(tick=tick, enemies=enemies))
            if tick >= 101:
                self.assertEqual(tactic.active_enemy_ids, {UUID(ENEMY_1)})

        tactic.choose_actions(make_turn(tick=104, enemies=[]))
        self.assertEqual(tactic.active_enemy_ids, set())
        self.assertFalse(tactic.combat_pressure_active)

    def test_multi_axis_crossfire_uses_lower_damage_breakout(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            tick=100,
            enemies=[
                unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
                unit(ENEMY_2, "RANGER", (-3, 0), controlled=False),
                unit("10000000-0000-4000-8000-000000000003", "RANGER", (0, 3), controlled=False),
                unit("10000000-0000-4000-8000-000000000004", "RANGER", (0, -3), controlled=False),
            ],
        )

        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.last_projected_core_damage, 4)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.BREAKOUT)
        self.assertEqual(
            tactic.threat_assessment.primary_reason,
            "MULTI_AXIS_BREAKOUT",
        )
        self.assertEqual(
            tactic.threat_assessment.global_posture,
            GlobalPosture.BREAKOUT,
        )
        self.assertEqual(queued["core_action"]["type"], "START_MOVE")

    def test_multi_axis_guards_split_across_threat_sides(self) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (3, 0)),
                    unit(VANGUARD_2, "VANGUARD", (-3, 0)),
                ],
                enemies=[
                    unit(ENEMY_1, "RANGER", (8, 0), controlled=False),
                    unit(ENEMY_2, "RANGER", (-8, 0), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )

        self.assertEqual(queued["unit_actions"][VANGUARD_1]["type"], "WAIT")
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "WAIT")

    def test_enemy_matching_moving_core_speed_counts_as_pursuit(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                core_position=(0, 0),
                enemies=[unit(ENEMY_1, "RANGER", (6, 0), controlled=False)],
            )
        )
        tactic.choose_actions(
            make_turn(
                tick=101,
                core_position=(1, 0),
                enemies=[unit(ENEMY_1, "RANGER", (7, 0), controlled=False)],
            )
        )

        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})
        self.assertTrue(tactic.combat_pressure_active)

    def test_defenders_keep_engaging_visible_enemy_after_pursuit_score_resets(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (4, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        for tick, position in ((100, (6, 0)), (101, (5, 0)), (102, (5, 0))):
            turn = make_turn(
                tick=tick,
                units=defenders,
                enemies=[unit(ENEMY_1, "RANGER", position, controlled=False)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.pursuing_enemy_ids, set())
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")

    def test_stationary_enemy_is_not_misclassified_when_core_moves(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        first = make_turn(
            tick=100,
            core_position=(0, 0),
            enemies=[unit(ENEMY_1, "RANGER", (6, 0), controlled=False)],
        )
        tactic.choose_actions(first)
        second = make_turn(
            tick=101,
            core_position=(1, 0),
            enemies=[unit(ENEMY_1, "RANGER", (6, 0), controlled=False)],
        )
        tactic.choose_actions(second)

        self.assertEqual(tactic.pursuing_enemy_ids, set())

    def test_ranger_keeps_firing_during_continuous_chase(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (0, -3)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (2, 0)),
        ]
        first = make_turn(
            tick=100,
            units=defenders,
            enemies=[unit(ENEMY_1, "RANGER", (6, 0), controlled=False)],
        )
        tactic.choose_actions(first)
        firing = make_turn(
            tick=101,
            units=defenders,
            enemies=[unit(ENEMY_1, "RANGER", (5, 0), controlled=False)],
        )
        tactic.choose_actions(firing)
        self.assertEqual(
            firing.plan.model_dump(mode="json", exclude_none=True)["unit_actions"]
            [RANGER_2]["type"],
            "SHOOT",
        )

        falling_back = make_turn(
            tick=102,
            units=defenders,
            enemies=[unit(ENEMY_1, "RANGER", (4, 0), controlled=False)],
        )
        tactic.choose_actions(falling_back)
        action = falling_back.plan.model_dump(mode="json", exclude_none=True)[
            "unit_actions"
        ][RANGER_2]
        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})
        self.assertEqual(action["type"], "SHOOT")
        self.assertEqual(action["expected_cell"], [4, 0])

    def test_distant_confirmed_pursuit_starts_core_evasion_early(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        for tick, position in ((100, (20, 0)), (101, (19, 0)), (102, (18, 0))):
            turn = make_turn(
                tick=tick,
                enemies=[unit(ENEMY_1, "RANGER", position, controlled=False)],
            )
            tactic.choose_actions(turn)

        queued = turn.plan.model_dump(mode="json", exclude_none=True)
        self.assertEqual(tactic.pursuing_enemy_ids, {UUID(ENEMY_1)})
        self.assertEqual(queued["core_action"]["type"], "START_MOVE")

    def test_recent_attack_keeps_pressure_and_retreat_after_visibility_loss(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
            )
        )
        attacked = make_turn(
            tick=101,
            shield=4,
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-000000000099",
                    "tick": 100,
                    "event_type": "CORE_DAMAGED",
                    "reason_code": "ATTACK",
                    "target_id": CORE_ID,
                    "position": [0, 0],
                    "values": {
                        "damage": 1,
                        "shield_damage": 1,
                        "hp_damage": 0,
                    },
                }
            ],
        )
        tactic.choose_actions(attacked)
        queued = attacked.plan.model_dump(mode="json", exclude_none=True)

        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(len(tactic.recent_attack_threats), 1)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.ENGAGED)
        self.assertEqual(
            tactic.threat_assessment.primary_reason,
            "RECENT_CORE_ATTACK",
        )
        self.assertEqual(queued["core_action"]["type"], "START_MOVE")

    def test_remote_worker_attack_recalls_defense_without_moving_core(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                units=[unit(WORKER_1, "WORKER", (20, 1), cargo=0)],
                enemies=[unit(ENEMY_1, "RANGER", (20, 0), controlled=False)],
            )
        )
        attacked = make_turn(
            tick=101,
            units=[unit(WORKER_1, "WORKER", (20, 1), cargo=0, hp=1)],
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-00000000009a",
                    "tick": 100,
                    "event_type": "UNIT_DAMAGED",
                    "reason_code": "ATTACK",
                    "target_id": WORKER_1,
                    "position": [20, 1],
                    "values": {"damage": 1, "hp": 1},
                }
            ],
        )
        tactic.choose_actions(attacked)
        queued = attacked.plan.model_dump(mode="json", exclude_none=True)

        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.ENGAGED)
        self.assertEqual(
            tactic.threat_assessment.primary_reason,
            "RECENT_FLEET_ATTACK",
        )
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_attack_memory_keeps_only_geometrically_possible_attackers(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                enemies=[
                    unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
                    unit(ENEMY_2, "RANGER", (-10, 0), controlled=False),
                ],
            )
        )
        attacked = make_turn(
            tick=101,
            shield=4,
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-00000000009d",
                    "tick": 100,
                    "event_type": "CORE_DAMAGED",
                    "reason_code": "ATTACK",
                    "target_id": CORE_ID,
                    "position": [0, 0],
                    "values": {
                        "damage": 1,
                        "shield_damage": 1,
                        "hp_damage": 0,
                    },
                }
            ],
        )

        tactic.choose_actions(attacked)

        self.assertEqual(set(tactic.recent_attack_threats), {UUID(ENEMY_1)})

    def test_explicit_attack_actor_excludes_opposite_visible_threat(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                enemies=[
                    unit(ENEMY_1, "RANGER", (3, 0), controlled=False),
                    unit(ENEMY_2, "RANGER", (-3, 0), controlled=False),
                ],
            )
        )
        attacked = make_turn(
            tick=101,
            shield=4,
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-00000000009e",
                    "tick": 100,
                    "event_type": "CORE_DAMAGED",
                    "reason_code": "ATTACK",
                    "actor_id": ENEMY_1,
                    "target_id": CORE_ID,
                    "position": [0, 0],
                    "values": {
                        "damage": 1,
                        "shield_damage": 1,
                        "hp_damage": 0,
                    },
                }
            ],
        )

        tactic.choose_actions(attacked)

        self.assertEqual(set(tactic.recent_attack_threats), {UUID(ENEMY_1)})

    def test_recent_attack_memory_is_exactly_six_planning_ticks(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                enemies=[unit(ENEMY_1, "RANGER", (3, 0), controlled=False)],
            )
        )
        tactic.choose_actions(
            make_turn(
                tick=101,
                shield=4,
                events=[
                    {
                        "event_id": "20000000-0000-4000-8000-00000000009f",
                        "tick": 100,
                        "event_type": "CORE_DAMAGED",
                        "reason_code": "ATTACK",
                        "target_id": CORE_ID,
                        "position": [0, 0],
                        "values": {
                            "damage": 1,
                            "shield_damage": 1,
                            "hp_damage": 0,
                        },
                    }
                ],
            )
        )

        tactic.choose_actions(make_turn(tick=106))
        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(len(tactic.recent_attack_threats), 1)

        tactic.choose_actions(make_turn(tick=107))
        self.assertFalse(tactic.combat_pressure_active)
        self.assertEqual(tactic.recent_attack_threats, {})

    def test_remote_interceptor_does_not_cancel_core_raid(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        defenders = [
            unit(VANGUARD_1, "VANGUARD", (0, 3)),
            unit(VANGUARD_2, "VANGUARD", (15, 0)),
            unit(RANGER_1, "RANGER", (-2, 0)),
            unit(RANGER_2, "RANGER", (15, 1)),
        ]
        for tick in (100, 101, 102):
            tactic.choose_actions(
                make_turn(
                    tick=tick,
                    resources=5,
                    units=defenders,
                    enemies=[enemy_core(ENEMY_1, (30, 0))],
                )
            )
        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

        intercepted = make_turn(
            tick=103,
            resources=5,
            units=defenders,
            enemies=[
                enemy_core(ENEMY_1, (30, 0)),
                unit(ENEMY_2, "VANGUARD", (16, 0), controlled=False),
            ],
        )
        tactic.choose_actions(intercepted)
        queued = intercepted.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))
        self.assertEqual(tactic.squad_return_ids, set())
        self.assertTrue(tactic.combat_pressure_active)
        self.assertEqual(tactic.threat_assessment.level, ThreatLevel.ENGAGED)
        self.assertEqual(
            tactic.threat_assessment.primary_reason,
            "LOCAL_SQUAD_CONTACT",
        )
        self.assertEqual(queued["unit_actions"][VANGUARD_2]["type"], "SWEEP")
        self.assertEqual(queued["unit_actions"][RANGER_2]["type"], "SHOOT")
        self.assertNotEqual(queued.get("core_action", {}).get("type"), "START_MOVE")

    def test_evading_remote_scout_keeps_returning_after_contact_lost(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        first = make_turn(
            tick=100,
            units=[unit(WORKER_1, "WORKER", (20, 0), cargo=0)],
            enemies=[unit(ENEMY_1, "VANGUARD", (21, 0), controlled=False)],
        )
        tactic.choose_actions(first)
        self.assertIn(UUID(WORKER_1), tactic.scout_return_ids)

        returning = make_turn(
            tick=101,
            units=[unit(WORKER_1, "WORKER", (19, 0), cargo=0)],
        )
        tactic.choose_actions(returning)
        queued = returning.plan.model_dump(mode="json", exclude_none=True)

        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "SCOUT_RETURN")
        self.assertEqual(queued["unit_actions"][WORKER_1]["type"], "MOVE")
        self.assertEqual(queued["unit_actions"][WORKER_1]["direction"], "LEFT")

    def test_scout_does_not_step_back_into_recent_enemy_vision(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        contact = make_turn(
            tick=100,
            core_position=(-100, 0),
            units=[unit(WORKER_1, "WORKER", (4, 0), cargo=0)],
            enemies=[unit(ENEMY_1, "VANGUARD", (1, 0), controlled=False)],
        )
        tactic.choose_actions(contact)
        first_action = contact.plan.model_dump(mode="json", exclude_none=True)[
            "unit_actions"
        ][WORKER_1]
        first_direction = Direction(first_action["direction"])
        hidden_position = (
            4 + first_direction.delta[0],
            first_direction.delta[1],
        )
        self.assertGreater(
            abs(hidden_position[0] - 1) + abs(hidden_position[1]),
            3,
        )

        hidden = make_turn(
            tick=101,
            core_position=(-100, 0),
            units=[unit(WORKER_1, "WORKER", hidden_position, cargo=0)],
        )
        tactic.choose_actions(hidden)
        action = hidden.plan.model_dump(mode="json", exclude_none=True)[
            "unit_actions"
        ][WORKER_1]
        second_direction = Direction(action["direction"])

        self.assertEqual(
            tactic.worker_modes[UUID(WORKER_1)],
            "SCOUT_BREAK_CONTACT",
        )
        self.assertNotEqual(
            second_direction.delta,
            tuple(-value for value in first_direction.delta),
        )

    def test_unsupported_scout_breaks_contact_from_pursuer(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        for tick, worker_position, enemy_position in (
            (100, (4, 0), (1, 0)),
            (101, (5, 0), (2, 0)),
        ):
            turn = make_turn(
                tick=tick,
                core_position=(0, 0),
                units=[unit(WORKER_1, "WORKER", worker_position, cargo=0)],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", enemy_position, controlled=False)
                ],
            )
            tactic.choose_actions(turn)
            action = turn.plan.model_dump(mode="json", exclude_none=True)[
                "unit_actions"
            ][WORKER_1]
            direction = Direction(action["direction"])
            destination = (
                worker_position[0] + direction.delta[0],
                worker_position[1] + direction.delta[1],
            )
            self.assertGreater(
                abs(destination[0] - enemy_position[0])
                + abs(destination[1] - enemy_position[1]),
                abs(worker_position[0] - enemy_position[0])
                + abs(worker_position[1] - enemy_position[1]),
            )

    def test_threatened_scout_moves_toward_nearby_support(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        turn = make_turn(
            tick=100,
            core_position=(-100, 0),
            units=[
                unit(WORKER_1, "WORKER", (4, 0), cargo=0),
                unit(VANGUARD_1, "VANGUARD", (4, 3)),
            ],
            enemies=[unit(ENEMY_1, "VANGUARD", (1, 0), controlled=False)],
        )

        tactic.choose_actions(turn)
        action = turn.plan.model_dump(mode="json", exclude_none=True)[
            "unit_actions"
        ][WORKER_1]

        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "SCOUT_SUPPORT")
        self.assertEqual(action["direction"], "DOWN")

    def test_new_contact_interrupts_scout_cooldown(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.scout_cooldown_until[UUID(WORKER_1)] = 105
        turn = make_turn(
            tick=103,
            units=[unit(WORKER_1, "WORKER", (3, 0), cargo=0)],
            enemies=[unit(ENEMY_1, "VANGUARD", (4, 0), controlled=False)],
        )

        tactic.choose_actions(turn)

        self.assertNotIn(UUID(WORKER_1), tactic.scout_cooldown_until)
        self.assertIn(UUID(WORKER_1), tactic.scout_return_ids)
        self.assertEqual(tactic.worker_modes[UUID(WORKER_1)], "SCOUT_EVADE")

    def test_respawn_drops_old_battle_threat_memory(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="hold")
        tactic.choose_actions(
            make_turn(
                tick=100,
                enemies=[unit(ENEMY_1, "RANGER", (6, 0), controlled=False)],
            )
        )
        respawned = make_turn(
            tick=101,
            core_position=(100, 100),
            events=[
                {
                    "event_id": "20000000-0000-4000-8000-00000000009b",
                    "tick": 100,
                    "event_type": "CORE_DAMAGED",
                    "reason_code": "ATTACK",
                    "target_id": CORE_ID,
                    "position": [0, 0],
                    "values": {
                        "damage": 5,
                        "shield_damage": 5,
                        "hp_damage": 0,
                    },
                },
                {
                    "event_id": "20000000-0000-4000-8000-00000000009c",
                    "tick": 100,
                    "event_type": "CORE_RESPAWNED",
                    "target_id": CORE_ID,
                    "position": [100, 100],
                    "values": {"resources": 5, "workers": 1},
                },
            ],
        )
        tactic.choose_actions(respawned)

        self.assertFalse(tactic.combat_pressure_active)
        self.assertEqual(tactic.recent_attack_threats, {})
        self.assertEqual(tactic.recent_core_attack_until_tick, 0)

    def test_ranger_focuses_enemy_ranger_before_vanguard(self) -> None:
        queued = plan(
            make_turn(
                units=[unit(RANGER_1, "RANGER", (0, 0))],
                enemies=[
                    unit(ENEMY_1, "VANGUARD", (0, 1), controlled=False),
                    unit(ENEMY_2, "RANGER", (3, 0), controlled=False),
                ],
            ),
            beacon_policy="hold",
        )
        self.assertEqual(
            queued["unit_actions"][RANGER_1],
            {
                "type": "SHOOT",
                "target_id": ENEMY_2,
                "expected_cell": [3, 0],
            },
        )


    def test_visible_enemy_core_is_locked_immediately(self) -> None:
        tactic = CoreFarmer(worker_target=1, beacon_policy="pursue")
        turn = make_turn(
            tick=100,
            units=[
                unit(VANGUARD_1, "VANGUARD", (0, 3)),
                unit(VANGUARD_2, "VANGUARD", (3, 0)),
                unit(RANGER_1, "RANGER", (-2, 0)),
                unit(RANGER_2, "RANGER", (2, 0)),
            ],
            enemies=[enemy_core(ENEMY_1, (4, 0))],
        )

        tactic.choose_actions(turn)

        self.assertEqual(tactic.isolated_core_target_id, UUID(ENEMY_1))

    def test_core_focus_continues_when_core_dies_before_reinforcement(self) -> None:
        target = enemy_core(ENEMY_1, (4, 0))
        target["hp"] = 1
        target["shield"] = 1
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (0, 3)),
                    unit(VANGUARD_2, "VANGUARD", (3, 0)),
                    unit(RANGER_1, "RANGER", (-2, 0)),
                    unit(RANGER_2, "RANGER", (2, 0)),
                ],
                enemies=[
                    target,
                    unit(ENEMY_2, "VANGUARD", (3, 1), controlled=False),
                ],
            ),
            beacon_policy="pursue",
        )

        self.assertEqual(
            queued["unit_actions"][VANGUARD_2],
            {"type": "SWEEP", "direction": "RIGHT"},
        )
        self.assertEqual(
            queued["unit_actions"][RANGER_2]["expected_cell"], [4, 0]
        )

    def test_core_focus_switches_to_returning_attacker_when_survival_is_unsafe(
        self,
    ) -> None:
        queued = plan(
            make_turn(
                units=[
                    unit(VANGUARD_1, "VANGUARD", (0, 3)),
                    unit(VANGUARD_2, "VANGUARD", (3, 0)),
                    unit(RANGER_1, "RANGER", (-2, 0)),
                    unit(RANGER_2, "RANGER", (2, 0)),
                ],
                enemies=[
                    enemy_core(ENEMY_1, (4, 0)),
                    unit(ENEMY_2, "VANGUARD", (3, 1), controlled=False),
                ],
            ),
            beacon_policy="pursue",
        )

        self.assertEqual(
            queued["unit_actions"][VANGUARD_2],
            {"type": "SWEEP", "direction": "DOWN"},
        )
        self.assertEqual(
            queued["unit_actions"][RANGER_2]["expected_cell"], [3, 1]
        )

    def test_strong_force_dispatches_a_vanguard_to_ground_beacon(self) -> None:
        workers = [
            unit(
                f"30000000-0000-4000-8000-{index:012x}",
                "WORKER",
                (index + 20, 20),
                cargo=0,
            )
            for index in range(18)
        ]
        vanguards = [
            unit(
                f"31000000-0000-4000-8000-{index:012x}",
                "VANGUARD",
                (index + 1, 8),
            )
            for index in range(14)
        ]
        rangers = [
            unit(
                f"32000000-0000-4000-8000-{index:012x}",
                "RANGER",
                (index + 1, -8),
            )
            for index in range(8)
        ]
        tactic = CoreFarmer(worker_target=18, beacon_policy="pursue")
        turn = make_turn(
            tick=100,
            resources=30,
            beacon_position=(12, 0),
            beacon_status="GROUND",
            units=workers + vanguards + rangers,
        )

        tactic.choose_actions(turn)
        queued = turn.plan.model_dump(mode="json", exclude_none=True)

        self.assertIsNotNone(tactic.beacon_runner_id)
        self.assertIn(str(tactic.beacon_runner_id), queued["unit_actions"])
        self.assertEqual(
            queued["unit_actions"][str(tactic.beacon_runner_id)]["type"],
            "MOVE",
        )


class ApiKeyLoadingTests(unittest.TestCase):
    def test_loads_ignored_env_file_without_logging_value(self) -> None:
        previous = os.environ.pop("ARENA_HERO_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / ".env"
                path.write_text('ARENA_HERO_API_KEY="test-only-key"\n', encoding="utf-8")
                self.assertEqual(
                    load_api_key(env_file=path, can_prompt=False), "test-only-key"
                )
        finally:
            if previous is not None:
                os.environ["ARENA_HERO_API_KEY"] = previous


class EventLoopTests(unittest.TestCase):
    def test_compatibility_marker_can_be_disabled_or_overridden(self) -> None:
        parser = build_parser()
        disabled = parser.parse_args(["--no-compatibility-marker"])
        custom = parser.parse_args(["--compatibility-marker", "custom-hold.json"])
        watchdog = parser.parse_args(["--stale-turn-timeout-seconds", "45"])

        self.assertIsNone(disabled.compatibility_marker)
        self.assertEqual(custom.compatibility_marker, Path("custom-hold.json"))
        self.assertEqual(watchdog.stale_turn_timeout_seconds, 45)

    def test_stale_turn_watchdog_closes_stream_for_supervisor_restart(self) -> None:
        instances: list[FakeGame] = []

        class FakeGame:
            def __init__(self, **_kwargs: object) -> None:
                self.closed = threading.Event()
                instances.append(self)

            def __enter__(self) -> FakeGame:
                return self

            def __exit__(self, *_args: object) -> None:
                self.close()

            def close(self) -> None:
                self.closed.set()

            def events(self):
                self.closed.wait(timeout=1)
                if False:
                    yield None

        errors = io.StringIO()
        with (
            patch("arena_farmer.ArenaHeroClient", FakeGame),
            redirect_stderr(errors),
            self.assertRaisesRegex(OSError, "unattended recovery timeout"),
        ):
            play(
                "test-only-key",
                base_url="https://example.test",
                worker_target=12,
                beacon_policy="retreat",
                stale_turn_timeout_seconds=0.05,
            )

        self.assertTrue(instances[0].closed.is_set())
        self.assertIn("restarting the Agent", errors.getvalue())

    def test_stale_turn_watchdog_rejects_nonfinite_timeouts(self) -> None:
        for timeout in (float("nan"), float("inf")):
            with self.subTest(timeout=timeout), self.assertRaisesRegex(
                ValueError,
                "must be finite",
            ):
                play(
                    "test-only-key",
                    base_url="https://example.test",
                    worker_target=12,
                    beacon_policy="retreat",
                    stale_turn_timeout_seconds=timeout,
                )

    def test_systemd_notify_is_optional_outside_service(self) -> None:
        previous = os.environ.pop("NOTIFY_SOCKET", None)
        try:
            self.assertFalse(_notify_systemd("WATCHDOG=1"))
        finally:
            if previous is not None:
                os.environ["NOTIFY_SOCKET"] = previous

    def test_respawning_systemd_status_does_not_dereference_core(self) -> None:
        turn = make_turn(core=False)
        tactic = CoreFarmer()
        tactic.choose_actions(turn)
        status = _systemd_status(turn, tactic, turn.tick)
        self.assertIn("core respawning", status)
        self.assertIn("core_hp none", status)
        self.assertIn("posture RESPAWNING", status)
        self.assertIn("threat NORMAL", status)

    def test_play_submits_respawning_turn_without_status_crash(self) -> None:
        class FakeGame:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def __enter__(self) -> "FakeGame":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def events(self):
                yield make_turn(core=False)

        notifications: list[tuple[str, ...]] = []
        with (
            patch("arena_farmer.ArenaHeroClient", FakeGame),
            patch(
                "arena_farmer._notify_systemd",
                side_effect=lambda *lines: notifications.append(lines) or True,
            ),
            self.assertRaisesRegex(OSError, "event stream ended unexpectedly"),
        ):
            play(
                "test-only-key",
                base_url="https://example.test",
                worker_target=12,
                beacon_policy="retreat",
            )
        self.assertIn("core respawning", notifications[0][1])

    def test_periodic_and_significant_turns_are_logged(self) -> None:
        self.assertTrue(_should_log_turn(make_turn(tick=20)))
        self.assertFalse(_should_log_turn(make_turn(tick=21)))
        self.assertTrue(
            _should_log_turn(
                make_turn(
                    tick=21,
                    events=[
                        {
                            "event_id": "20000000-0000-4000-8000-000000000004",
                            "tick": 20,
                            "event_type": "CORE_DAMAGED",
                            "reason_code": "ATTACK",
                            "actor_id": CORE_ID,
                        }
                    ],
                )
            )
        )
        self.assertTrue(
            _should_log_turn(
                make_turn(
                    tick=21,
                    enemies=[enemy_core(ENEMY_1, (3, 0))],
                )
            )
        )

    def test_manual_receipt_logs_counts_without_plan_contents(self) -> None:
        receipt = Received(
            tick=9,
            source="MANUAL",
            received_at="2026-08-01T00:00:00Z",
            plan=CommandPlan(tick=9),
        )
        self.assertEqual(
            _manual_override_summary(receipt),
            "WARNING tick=9 manual_override unit_actions=0 core_actions=0",
        )

    def test_agent_receipt_does_not_warn(self) -> None:
        receipt = Received(
            tick=9,
            source="AGENT",
            received_at="2026-08-01T00:00:00Z",
            plan=CommandPlan(tick=9),
        )
        self.assertIsNone(_manual_override_summary(receipt))

    def test_stale_turn_errors_do_not_stop_agent(self) -> None:
        self.assertTrue(_is_turn_scoped_api_error("TICK_MISMATCH"))
        self.assertTrue(_is_turn_scoped_api_error("COMMAND_WINDOW_CLOSED"))
        self.assertFalse(_is_turn_scoped_api_error("INVALID_COMMAND"))


if __name__ == "__main__":
    unittest.main()
