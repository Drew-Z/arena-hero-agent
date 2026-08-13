from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import sqlite3
import socket
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from getpass import getpass
from itertools import combinations, count, product
from pathlib import Path
from typing import Protocol
from uuid import UUID

from arena_health import write_heartbeat
from arena_history import HistoryRecorder, visible_cells
from arena_hero import (
    APIError,
    ArenaHeroClient,
    ArenaHeroError,
    AuthenticationError,
    BeaconStatus,
    CommandSource,
    CoreState,
    Direction,
    PolicyViolationError,
    ProtocolError,
    Received,
    TransportError,
    Turn,
    UnitType,
    unit_cost,
)

API_KEY_ENV = "ARENA_HERO_API_KEY"
DEFAULT_BASE_URL = "https://api.arenahero.io"
DEFAULT_COMPATIBILITY_MARKER = Path(
    "/var/lib/arena-hero-version/compatibility-hold.json"
)
FORCE_STAGES = (
    (8, 1, 1),
    (12, 3, 4),
    (18, 6, 8),
    (18, 14, 16),
)
DEFAULT_WORKER_TARGET = FORCE_STAGES[-1][0]
DEFAULT_BEACON_POLICY = "pursue"
BASE_WORKER_TARGET = FORCE_STAGES[0][0]
CORE_RESOURCE_RESERVE = 10
LATE_EXPANSION_RESERVE = 10
EARLY_DEFENSE_WORKER_GOAL = FORCE_STAGES[0][0]
EARLY_DEFENSE_RESERVE = 10
LONG_TERM_DEFENSE_RESERVE = 10
EARLY_DEFENSE_VANGUARD_TARGET = FORCE_STAGES[0][1]
EARLY_DEFENSE_RANGER_TARGET = FORCE_STAGES[0][2]
MATURE_DEFENSE_WORKER_GOAL = FORCE_STAGES[1][0]
MID_FORCE_VANGUARD_TARGET = FORCE_STAGES[1][1]
MID_FORCE_RANGER_TARGET = FORCE_STAGES[1][2]
DEFENSE_VANGUARD_TARGET = FORCE_STAGES[-1][1]
DEFENSE_RANGER_TARGET = FORCE_STAGES[-1][2]
TARGET_POPULATION = sum(FORCE_STAGES[-1])
MAX_WORKER_TARGET = FORCE_STAGES[-1][0]
VANGUARD_GUARD_RADIUS = 3
RANGER_GUARD_RADIUS = 2
VANGUARD_CORE_GUARDS = 1
RANGER_CORE_GUARDS = 1
MATURE_VANGUARD_CORE_GUARDS = 2
MATURE_RANGER_CORE_GUARDS = 2
MATURE_GUARD_FLEET_MIN = 5
CORE_RAID_VANGUARDS = 4
CORE_RAID_RANGERS = 2
CORE_RESERVE_VANGUARDS = 1
CORE_RESERVE_RANGERS = 2
CORE_RESERVE_RADIUS = 5
EARLY_ASSAULT_MIN_VANGUARDS = VANGUARD_CORE_GUARDS + 1
EARLY_ASSAULT_MIN_RANGERS = RANGER_CORE_GUARDS + 1
ASSAULT_MIN_VANGUARDS = (
    MATURE_VANGUARD_CORE_GUARDS + CORE_RESERVE_VANGUARDS + CORE_RAID_VANGUARDS
)
ASSAULT_MIN_RANGERS = (
    MATURE_RANGER_CORE_GUARDS + CORE_RESERVE_RANGERS + CORE_RAID_RANGERS
)
MAIN_ASSAULT_MIN_VANGUARDS = ASSAULT_MIN_VANGUARDS
MAIN_ASSAULT_MIN_RANGERS = ASSAULT_MIN_RANGERS
MAIN_ASSAULT_RALLY_RADIUS = 5
CORE_RAID_RALLY_TIMEOUT_TICKS = 12
CORE_TARGET_DURABILITY_WEIGHT = 2
CORE_TARGET_PROTECTOR_HP_WEIGHT = 3
ISOLATED_CORE_CONFIRM_TICKS = 2
CORE_VISIBILITY_GAP_TICKS = 2
CORE_RAID_STRIKE_MAX_DISTANCE = 256
CORE_RAID_STRIKE_RELEASE_DISTANCE = 320
CORE_RAID_MEMORY_TTL = 256
CORE_OBSERVER_MIN_DISTANCE = 2
CORE_OBSERVER_MAX_DISTANCE = 3
CORE_PROTECTOR_RADIUS = 5
UNIT_HEAL_RESOURCE_RESERVE = 10
POST_THREAT_CAUTION_TICKS = 8
RECENT_ATTACK_MEMORY_TICKS = 6
PURSUIT_MEMORY_TTL = 2
PURSUIT_SCORE_MAX = 4
DISTANT_PURSUIT_SCORE_THRESHOLD = 3
ACTIVE_ENEMY_ALERT_TICKS = 2
CORE_PREEMPTIVE_EVADE_HORIZON_TICKS = 16
SQUAD_DISENGAGE_TICKS = 8
SCOUT_SAFE_RETURN_RADIUS = 3
SCOUT_COOLDOWN_TICKS = 3
STATIONARY_CORE_MEMORY_TTL = 256
RESOURCE_MEMORY_TTL = 64
MANUAL_ORDER_ARRIVAL_RADIUS = 2
RESOURCE_STALL_TICKS = 6
RESOURCE_COOLDOWN_TICKS = 8
RESOURCE_ASSIGNMENT_STICKY_BONUS = 2
SCOUT_STALL_TICKS = 3
RECOVERY_TICKS = 160
RECOVERY_MIN_WORKERS = 8
RECOVERY_MIN_RESOURCES = 20
RECOVERY_THREAT_DISTANCE = 12
RECOVERY_INFERENCE_RESOURCE_LIMIT = CORE_RESOURCE_RESERVE + unit_cost(
    UnitType.WORKER,
    0,
)
LOG_SNAPSHOT_INTERVAL = 20
PATH_COST_MAX_EXPANSIONS = 512
PATH_COST_UNREACHABLE = 1_000_000
CORE_SHORT_CARGO_ETA = 2
CORE_BULK_CARGO_ETA = 4
CORE_BULK_CARGO = 3
CORE_CONGESTED_CARGO = 3
CORE_DELIVERY_CHAIN_MAX = 8
CORE_EVADE_TRIGGER_DISTANCE = 12
CORE_EVADE_RELEASE_DISTANCE = CORE_EVADE_TRIGGER_DISTANCE + 2
CORE_MOVE_COMMIT_PROGRESS = 2
UNIT_EVADE_TRIGGER_DISTANCE = 5
ASSAULT_REINFORCEMENT_RADIUS = UNIT_EVADE_TRIGGER_DISTANCE
FULL_ASSAULT_HOSTILE_COUNT = 4
BEACON_CAMPAIGN_POPULATION = 40
BEACON_CAMPAIGN_RESOURCES = 30
BEACON_RETURN_RADIUS = 3
RETREAT_MIN_BEACON_DISTANCE = 224
COMBAT_PATROL_INITIAL_RADIUS = 24
COMBAT_PATROL_GROWTH_TICKS = 64
COMBAT_PATROL_RING_STEP = 8
RETREAT_SERVICE_TICKS = 8
SIGNED_INT64_MIN = -(2**63)
SIGNED_INT64_MAX = 2**63 - 1
TRANSIENT_EXIT_CODE = 75
CONFIGURATION_EXIT_CODE = 2
AUTHENTICATION_EXIT_CODE = 10
POLICY_EXIT_CODE = 11
PROTOCOL_EXIT_CODE = 12
API_EXIT_CODE = 13
AGENT_EXIT_CODE = 14
DEFAULT_STALE_TURN_TIMEOUT_SECONDS = 0.0
DEFAULT_ALLIANCE_STALE_SECONDS = 60.0
DEFAULT_ALLIANCE_BARRIER_TIMEOUT_SECONDS = 1.0
ALLY_CORE_RALLY_RADIUS = 12
TURN_SKIP_API_ERRORS = frozenset(
    {
        "COMMAND_RATE_LIMITED",
        "COMMAND_WINDOW_CLOSED",
        "TICK_MISMATCH",
        "TICK_NOT_READY",
    }
)
CARDINAL_DIRECTIONS = (
    Direction.UP,
    Direction.RIGHT,
    Direction.DOWN,
    Direction.LEFT,
)
RANGER_LINE_VECTORS = (
    (0, -1),
    (1, -1),
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
)
SCOUT_VECTORS = (
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
    (0, -1),
    (1, -1),
)
SCOUT_STAGE_CYCLE = len(SCOUT_VECTORS)
SCOUT_RING_STEP = 10
SCOUT_RING_COUNT = 4
SCOUT_COVERAGE_MEMORY_TTL = 4096

Position = tuple[int, int]


class LifecycleMode(str, Enum):
    ACTIVE = "ACTIVE"
    RESPAWNING = "RESPAWNING"
    COMPATIBILITY_HOLD = "COMPATIBILITY_HOLD"
    RECOVERY = "RECOVERY"


class ThreatLevel(str, Enum):
    NORMAL = "NORMAL"
    ALERT = "ALERT"
    PRE_EVADE = "PRE_EVADE"
    ENGAGED = "ENGAGED"
    BREAKOUT = "BREAKOUT"


class GlobalPosture(str, Enum):
    NORMAL = "NORMAL"
    ALERT = "ALERT"
    PRE_EVADE = "PRE_EVADE"
    ENGAGED = "ENGAGED"
    BREAKOUT = "BREAKOUT"
    RECOVERY = "RECOVERY"
    COMPATIBILITY_HOLD = "COMPATIBILITY_HOLD"
    RESPAWNING = "RESPAWNING"


def _chunk_coordinates(position: Position) -> Position:
    return position[0] // 32, position[1] // 32


def _chunk_axis(value: int) -> int:
    return value if value >= 0 else -value - 1


def _chunk_resource_quota(position: Position) -> int:
    chunk_x, chunk_y = _chunk_coordinates(position)
    ring = _chunk_axis(chunk_x) + _chunk_axis(chunk_y)
    return max(2, (16 * 8) // (8 + ring))


class Movable(Protocol):
    id: object
    position: Position

    def move(self, direction: Direction) -> None: ...


@dataclass(slots=True)
class MovementContext:
    obstacles: set[Position]
    resource_cells: set[Position]
    enemy_cells: set[Position]
    danger_cells: set[Position]
    allied_cells: set[Position]
    discouraged_cells: set[Position]
    friendly_counts: Counter[Position]
    reserved_destinations: set[Position]
    core_position: Position | None
    delivery_lane: Position | None = None
    preplanned_units: set[UUID] | None = None


@dataclass(slots=True)
class ResourceProgress:
    target: Position
    best_cost: int
    stalled_turns: int = 0


@dataclass(slots=True)
class ScoutProgress:
    target: Position
    best_cost: int
    stalled_turns: int = 0


@dataclass(slots=True)
class EnemyCoreSighting:
    position: Position
    first_tick: int
    last_tick: int
    observations: int = 1


@dataclass(slots=True, frozen=True)
class CoreRaidTarget:
    id: UUID
    position: Position
    visible_enemy: object | None


@dataclass(slots=True)
class EnemyUnitMotion:
    position: Position
    last_tick: int
    core_distance: int
    unit_type: UnitType
    pursuit_score: int = 0
    pursuit_ticks: int = 0
    activity_until_tick: int = 0
    preemptive_evade_until_tick: int = 0
    ticks_to_attack_range: int | None = None


@dataclass(slots=True, frozen=True)
class RememberedThreat:
    id: UUID
    position: Position
    unit_type: UnitType
    expires_tick: int


@dataclass(slots=True, frozen=True)
class AlliancePeer:
    account_id: str
    alliance_id: str
    username: str
    tick: int
    population: int
    core_id: UUID | None
    core_position: Position | None
    unit_ids: frozenset[UUID]
    unit_positions: frozenset[Position]
    updated_at: float


def _coordination_name(value: str, label: str) -> str:
    selected = value.strip()
    if not selected or len(selected) > 64 or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for character in selected
    ):
        raise ValueError(
            f"{label} must contain only letters, numbers, underscores, or hyphens"
        )
    return selected


class AllianceCoordinator:
    def __init__(
        self,
        directory: Path,
        *,
        alliance_id: str,
        account_id: str,
        expected_members: int = 1,
        stale_seconds: float = DEFAULT_ALLIANCE_STALE_SECONDS,
        barrier_timeout_seconds: float = DEFAULT_ALLIANCE_BARRIER_TIMEOUT_SECONDS,
    ) -> None:
        if not math.isfinite(stale_seconds) or stale_seconds <= 0:
            raise ValueError("alliance stale seconds must be finite and positive")
        if expected_members < 1:
            raise ValueError("alliance expected members must be positive")
        if not math.isfinite(barrier_timeout_seconds) or barrier_timeout_seconds < 0:
            raise ValueError("alliance barrier timeout must be finite and non-negative")
        self.directory = directory
        self.alliance_id = _coordination_name(alliance_id, "alliance_id")
        self.account_id = _coordination_name(account_id, "account_id")
        self.expected_members = expected_members
        self.stale_seconds = stale_seconds
        self.barrier_timeout_seconds = barrier_timeout_seconds

    @property
    def state_path(self) -> Path:
        return self.directory / f"{self.account_id}.json"

    def publish(self, turn: Turn) -> None:
        core = turn.core
        state = {
            "version": 1,
            "alliance_id": self.alliance_id,
            "account_id": self.account_id,
            "username": core.owner_username if core is not None else "",
            "tick": turn.tick,
            "population": len(turn.units),
            "core_id": str(core.id) if core is not None else None,
            "core_position": list(core.position) if core is not None else None,
            "unit_ids": [str(unit.id) for unit in turn.units],
            "unit_positions": [list(unit.position) for unit in turn.units],
            "units": [
                {
                    "id": str(unit.id),
                    "position": list(unit.position),
                    "unit_type": unit.unit_type.value,
                    "hp": unit.hp,
                    "cargo": getattr(unit, "cargo", 0),
                }
                for unit in turn.units
            ],
            "updated_at": time.time(),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / (
            f".{self.account_id}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(state, separators=(",", ":")),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.state_path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def peers(self, *, now: float | None = None) -> tuple[AlliancePeer, ...]:
        selected_now = time.time() if now is None else now
        peers: list[AlliancePeer] = []
        try:
            paths = tuple(self.directory.glob("*.json"))
        except OSError:
            return ()
        for path in paths:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                updated_at = float(raw["updated_at"])
                if (
                    raw.get("version") != 1
                    or raw.get("alliance_id") != self.alliance_id
                    or selected_now - updated_at > self.stale_seconds
                    or updated_at - selected_now > self.stale_seconds
                ):
                    continue
                account_id = _coordination_name(str(raw["account_id"]), "account_id")
                position_raw = raw.get("core_position")
                core_position = (
                    (int(position_raw[0]), int(position_raw[1]))
                    if isinstance(position_raw, list) and len(position_raw) == 2
                    else None
                )
                core_id_raw = raw.get("core_id")
                peers.append(
                    AlliancePeer(
                        account_id=account_id,
                        alliance_id=self.alliance_id,
                        username=str(raw.get("username", "")),
                        tick=int(raw["tick"]),
                        population=max(0, int(raw["population"])),
                        core_id=UUID(core_id_raw) if core_id_raw else None,
                        core_position=core_position,
                        unit_ids=frozenset(UUID(value) for value in raw.get("unit_ids", ())),
                        unit_positions=frozenset(
                            (int(value[0]), int(value[1]))
                            for value in raw.get("unit_positions", ())
                            if isinstance(value, list) and len(value) == 2
                        ),
                        updated_at=updated_at,
                    )
                )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return tuple(sorted(peers, key=lambda peer: peer.account_id))


@dataclass(slots=True, frozen=True)
class ThreatAssessment:
    lifecycle: LifecycleMode = LifecycleMode.ACTIVE
    level: ThreatLevel = ThreatLevel.NORMAL
    primary_reason: str = "NONE"
    recent_attack: bool = False
    recent_core_attack: bool = False
    activity_enemy_ids: frozenset[UUID] = frozenset()
    preemptive_enemy_ids: frozenset[UUID] = frozenset()
    pursuing_enemy_ids: frozenset[UUID] = frozenset()
    near_core_enemy_ids: frozenset[UUID] = frozenset()
    threatening_core_enemy_ids: frozenset[UUID] = frozenset()
    disengaging: bool = False
    local_squad_contact: bool = False
    caution: bool = False
    breakout: bool = False

    @property
    def combat_pressure(self) -> bool:
        return bool(
            self.recent_attack
            or self.disengaging
            or self.activity_enemy_ids
            or self.pursuing_enemy_ids
            or self.near_core_enemy_ids
            or self.local_squad_contact
        )

    @property
    def global_posture(self) -> GlobalPosture:
        if self.lifecycle is LifecycleMode.RESPAWNING:
            return GlobalPosture.RESPAWNING
        if self.lifecycle is LifecycleMode.COMPATIBILITY_HOLD:
            return GlobalPosture.COMPATIBILITY_HOLD
        if self.lifecycle is LifecycleMode.RECOVERY:
            return GlobalPosture.RECOVERY
        return GlobalPosture(self.level.value)


@dataclass(slots=True, frozen=True)
class ResourceLedgerSnapshot:
    tick: int
    resources: int
    population: int
    workers: int
    vanguards: int
    rangers: int
    actions: str
    core_action: str


@dataclass(slots=True, frozen=True)
class ResourceLedgerResult:
    previous: ResourceLedgerSnapshot
    tick: int
    resources: int
    population: int
    workers: int
    vanguards: int
    rangers: int
    actual_delta: int
    expected_delta: int
    unexplained_delta: int
    events: str
    skipped_reason: str | None = None

    @property
    def unexplained_loss(self) -> int:
        return max(0, -self.unexplained_delta)


def _api_key_from_env_file(path: Path) -> str | None:
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip() == API_KEY_ENV:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value or None
    return None


def load_api_key(
    *,
    env_file: Path | None = None,
    can_prompt: bool | None = None,
    prompt: Callable[[str], str] | None = None,
) -> str:
    if key := os.environ.get(API_KEY_ENV, "").strip():
        return key

    selected_env_file = env_file or Path.cwd() / ".env"
    if selected_env_file.is_file():
        if key := _api_key_from_env_file(selected_env_file):
            return key
        if env_file is not None:
            raise ValueError(f"{API_KEY_ENV} is missing from {selected_env_file}")
    elif env_file is not None:
        raise ValueError(f"Environment file does not exist: {selected_env_file}")

    if can_prompt is None:
        can_prompt = sys.stdin.isatty()
    if not can_prompt:
        raise ValueError(f"Set {API_KEY_ENV} or add it to .env")

    key = (prompt or getpass)("Arena Hero API key: ").strip()
    if not key:
        raise ValueError("API key cannot be empty")
    return key


def _distance(left: Position, right: Position) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _core_guard_ids(turn: Turn) -> tuple[set[UUID], set[UUID]]:
    core = turn.core
    if core is None:
        return set(), set()

    def nearest(units: Sequence[object], mature_count: int) -> set[UUID]:
        count = mature_count if len(units) >= MATURE_GUARD_FLEET_MIN else 1
        return {
            unit.id
            for unit in sorted(
                units,
                key=lambda unit: (
                    _distance(unit.position, core.position),
                    _uuid_sort_key(unit),
                ),
            )[:count]
        }

    return (
        nearest(turn.vanguards, MATURE_VANGUARD_CORE_GUARDS),
        nearest(turn.rangers, MATURE_RANGER_CORE_GUARDS),
    )


def _core_reserve_ids(turn: Turn) -> tuple[set[UUID], set[UUID]]:
    """Select the next-nearest combat units for the middle defense layer."""
    if (
        len(turn.vanguards) < MATURE_GUARD_FLEET_MIN
        or len(turn.rangers) < MATURE_GUARD_FLEET_MIN
    ):
        return set(), set()
    guard_vanguards, guard_rangers = _core_guard_ids(turn)

    def nearest(
        units: Sequence[object],
        excluded: set[UUID],
        count: int,
    ) -> set[UUID]:
        return {
            unit.id
            for unit in sorted(
                (unit for unit in units if unit.id not in excluded),
                key=lambda unit: (
                    _distance(unit.position, turn.core.position),
                    _uuid_sort_key(unit),
                ),
            )[:count]
        }

    if turn.core is None:
        return set(), set()
    return (
        nearest(turn.vanguards, guard_vanguards, CORE_RESERVE_VANGUARDS),
        nearest(turn.rangers, guard_rangers, CORE_RESERVE_RANGERS),
    )


def _core_raid_strike_distance(
    position: Position,
    vanguards: Sequence[object],
    rangers: Sequence[object],
) -> int:
    return max(
        min(_distance(defender.position, position) for defender in vanguards),
        min(_distance(defender.position, position) for defender in rangers),
    )


def _minimum_cost_assignment(costs: Sequence[Sequence[int]]) -> tuple[int, ...]:
    """Return one deterministic minimum-cost column for each matrix row."""
    if not costs:
        return ()
    row_count = len(costs)
    column_count = len(costs[0])
    if column_count < row_count or any(
        len(row) != column_count for row in costs
    ):
        raise ValueError("assignment matrix must be rectangular with rows <= columns")

    row_potential = [0] * (row_count + 1)
    column_potential = [0] * (column_count + 1)
    matched_row = [0] * (column_count + 1)
    previous_column = [0] * (column_count + 1)

    for row_index in range(1, row_count + 1):
        matched_row[0] = row_index
        current_column = 0
        minimum_slack = [sys.maxsize] * (column_count + 1)
        visited = [False] * (column_count + 1)
        while True:
            visited[current_column] = True
            current_row = matched_row[current_column]
            delta = sys.maxsize
            next_column = 0
            for column_index in range(1, column_count + 1):
                if visited[column_index]:
                    continue
                reduced_cost = (
                    costs[current_row - 1][column_index - 1]
                    - row_potential[current_row]
                    - column_potential[column_index]
                )
                if reduced_cost < minimum_slack[column_index]:
                    minimum_slack[column_index] = reduced_cost
                    previous_column[column_index] = current_column
                if minimum_slack[column_index] < delta:
                    delta = minimum_slack[column_index]
                    next_column = column_index
            for column_index in range(column_count + 1):
                if visited[column_index]:
                    row_potential[matched_row[column_index]] += delta
                    column_potential[column_index] -= delta
                else:
                    minimum_slack[column_index] -= delta
            current_column = next_column
            if matched_row[current_column] == 0:
                break
        while True:
            next_column = previous_column[current_column]
            matched_row[current_column] = matched_row[next_column]
            current_column = next_column
            if current_column == 0:
                break

    assignment = [-1] * row_count
    for column_index in range(1, column_count + 1):
        row_index = matched_row[column_index]
        if row_index:
            assignment[row_index - 1] = column_index - 1
    return tuple(assignment)


def _destination(position: Position, direction: Direction) -> Position:
    dx, dy = direction.delta
    return position[0] + dx, position[1] + dy


def _is_signed_int64_position(position: Position) -> bool:
    return all(
        SIGNED_INT64_MIN <= coordinate <= SIGNED_INT64_MAX
        for coordinate in position
    )


def _minimum_enemy_distance(
    position: Position,
    enemies: Sequence[object],
) -> int:
    return min(
        (_distance(position, enemy.position) for enemy in enemies),
        default=SIGNED_INT64_MAX,
    )


def _enemy_distance_vector(
    position: Position,
    enemies: Sequence[object],
) -> tuple[int, ...]:
    return tuple(sorted(_distance(position, enemy.position) for enemy in enemies))


def _position_threat_key(
    position: Position,
    enemies: Sequence[object],
    obstacles: set[Position],
) -> tuple[int, tuple[int, ...]]:
    distances = _enemy_distance_vector(position, enemies)
    return (
        _projected_core_damage(position, enemies, obstacles),
        tuple(-distance for distance in distances),
    )


def _retreat_direction(
    position: Position,
    beacon_position: Position,
    enemies: Sequence[object],
    obstacles: set[Position],
    blocked: set[Position],
    previous_direction: Direction | None,
    *,
    allow_beacon_approach: bool,
) -> Direction | None:
    current_beacon_distance = _distance(position, beacon_position)
    away_vector = (
        position[0] - beacon_position[0],
        position[1] - beacon_position[1],
    )
    candidates: list[tuple[tuple[object, ...], Direction]] = []
    for index, direction in enumerate(CARDINAL_DIRECTIONS):
        destination = _destination(position, direction)
        if destination in blocked or not _is_signed_int64_position(destination):
            continue
        beacon_distance = _distance(destination, beacon_position)
        if beacon_distance < current_beacon_distance and not allow_beacon_approach:
            continue
        dx, dy = direction.delta
        alignment = dx * away_vector[0] + dy * away_vector[1]
        continuity = int(direction is previous_direction)
        if enemies:
            score = (
                *_position_threat_key(destination, enemies, obstacles),
                -beacon_distance,
                -alignment,
                -continuity,
                index,
            )
        else:
            score = (
                -beacon_distance,
                -alignment,
                -continuity,
                index,
            )
        candidates.append((score, direction))
    if not candidates:
        return None
    best_score, best_direction = min(candidates, key=lambda candidate: candidate[0])
    if (
        enemies
        and best_score[0]
        > _projected_core_damage(position, enemies, obstacles)
    ):
        return None
    return best_direction


def _threat_axis(origin: Position, target: Position) -> Direction:
    dx = target[0] - origin[0]
    dy = target[1] - origin[1]
    if abs(dx) >= abs(dy):
        return Direction.RIGHT if dx >= 0 else Direction.LEFT
    return Direction.DOWN if dy >= 0 else Direction.UP


def _is_multi_axis_breakout(
    position: Position,
    enemies: Sequence[object],
    obstacles: set[Position],
    blocked: set[Position],
) -> bool:
    if len(enemies) < 2 or _projected_core_damage(position, enemies, obstacles) == 0:
        return False
    if len({_threat_axis(position, enemy.position) for enemy in enemies}) < 2:
        return False

    current_distances = {
        enemy.id: _distance(position, enemy.position) for enemy in enemies
    }
    for direction in CARDINAL_DIRECTIONS:
        destination = _destination(position, direction)
        if destination in blocked or not _is_signed_int64_position(destination):
            continue
        if all(
            _distance(destination, enemy.position) > current_distances[enemy.id]
            for enemy in enemies
        ):
            return False
    return True


def _directions_toward(start: Position, target: Position) -> tuple[Direction, ...]:
    ranked = sorted(
        CARDINAL_DIRECTIONS,
        key=lambda direction: (
            _distance(_destination(start, direction), target),
            CARDINAL_DIRECTIONS.index(direction),
        ),
    )
    return tuple(ranked)


def _path_directions(
    start: Position,
    target: Position,
    blocked: set[Position],
    *,
    discouraged: set[Position] | None = None,
    target_radius: int = 0,
    max_expansions: int = 4096,
) -> tuple[Direction, ...]:
    if start == target:
        return ()

    blocked = set(blocked)
    blocked.discard(start)
    discouraged = set(discouraged or ())
    discouraged.discard(start)
    if target_radius:
        goals = {
            (target[0] + dx, target[1] + dy)
            for dx in range(-target_radius, target_radius + 1)
            for dy in range(-target_radius, target_radius + 1)
            if abs(dx) + abs(dy) <= target_radius
            and (target[0] + dx, target[1] + dy) not in blocked
        }
    elif target in blocked:
        goals = {
            _destination(target, direction)
            for direction in CARDINAL_DIRECTIONS
            if _destination(target, direction) not in blocked
        }
    else:
        goals = {target}
    if not goals or start in goals:
        return ()

    def distance_to_goal(position: Position) -> int:
        return min(_distance(position, goal) for goal in goals)

    sequence = count()
    start_distance = distance_to_goal(start)
    frontier: list[tuple[int, int, int, int, Position]] = [
        (start_distance, start_distance, 0, next(sequence), start)
    ]
    costs = {start: 0}
    came_from: dict[Position, tuple[Position, Direction]] = {}
    expansions = 0
    reached: Position | None = None

    while frontier and expansions < max_expansions:
        _, _, current_cost, _, current = heapq.heappop(frontier)
        if current_cost != costs.get(current):
            continue
        if current in goals:
            reached = current
            break

        expansions += 1
        for direction in CARDINAL_DIRECTIONS:
            destination = _destination(current, direction)
            if destination in blocked:
                continue
            new_cost = current_cost + 1
            if destination in discouraged:
                new_cost += 4
            if new_cost >= costs.get(destination, sys.maxsize):
                continue
            costs[destination] = new_cost
            came_from[destination] = (current, direction)
            remaining_distance = distance_to_goal(destination)
            heapq.heappush(
                frontier,
                (
                    new_cost + remaining_distance,
                    remaining_distance,
                    new_cost,
                    next(sequence),
                    destination,
                ),
            )

    if reached is None:
        reached = min(
            came_from,
            key=lambda position: (
                distance_to_goal(position),
                costs[position],
            ),
            default=None,
        )
        if reached is None:
            return ()

    current = reached
    while True:
        previous, direction = came_from[current]
        if previous == start:
            return (direction,)
        current = previous


def _estimated_path_cost(
    start: Position,
    target: Position,
    blocked: set[Position],
    *,
    max_expansions: int = PATH_COST_MAX_EXPANSIONS,
) -> int:
    if start == target:
        return 0

    blocked = set(blocked)
    blocked.discard(start)
    if target in blocked:
        return PATH_COST_UNREACHABLE

    sequence = count()
    start_distance = _distance(start, target)
    frontier: list[tuple[int, int, int, int, Position]] = [
        (start_distance, start_distance, 0, next(sequence), start)
    ]
    costs = {start: 0}
    expansions = 0

    while frontier and expansions < max_expansions:
        _, _, current_cost, _, current = heapq.heappop(frontier)
        if current_cost != costs.get(current):
            continue
        if current == target:
            return current_cost

        expansions += 1
        for direction in CARDINAL_DIRECTIONS:
            destination = _destination(current, direction)
            if destination in blocked:
                continue
            new_cost = current_cost + 1
            if new_cost >= costs.get(destination, sys.maxsize):
                continue
            costs[destination] = new_cost
            remaining_distance = _distance(destination, target)
            heapq.heappush(
                frontier,
                (
                    new_cost + remaining_distance,
                    remaining_distance,
                    new_cost,
                    next(sequence),
                    destination,
                ),
            )

    if not frontier:
        return PATH_COST_UNREACHABLE
    return min(estimated_cost for estimated_cost, *_ in frontier)


def _exploration_directions(unit: Movable) -> tuple[Direction, ...]:
    unit_number = getattr(unit.id, "int", 0)
    offset = unit_number % len(CARDINAL_DIRECTIONS)
    return CARDINAL_DIRECTIONS[offset:] + CARDINAL_DIRECTIONS[:offset]


def _rotate_directions(
    directions: tuple[Direction, ...],
    offset: int,
) -> tuple[Direction, ...]:
    offset %= len(directions)
    return directions[offset:] + directions[:offset]


def _queue_move(
    unit: Movable,
    directions: Iterable[Direction],
    context: MovementContext,
    *,
    allow_core_entry: bool = False,
    allow_friendly_entry: Position | None = None,
    allow_single_friendly_transit: bool = False,
    avoid_danger: bool = True,
) -> bool:
    for direction in directions:
        destination = _destination(unit.position, direction)
        if (
            destination in context.obstacles
            or destination in context.enemy_cells
            or destination in context.allied_cells
        ):
            continue
        if avoid_danger and destination in context.danger_cells:
            continue
        if destination in context.reserved_destinations:
            continue

        occupants = context.friendly_counts[destination]
        if occupants:
            entering_core = allow_core_entry and destination == context.core_position
            entering_allowed_friendly = destination == allow_friendly_entry
            entering_single_friendly = (
                allow_single_friendly_transit and occupants < 2
            )
            if not (
                entering_core
                or entering_allowed_friendly
                or entering_single_friendly
            ) or occupants >= 2:
                continue

        unit.move(direction)
        context.friendly_counts[unit.position] -= 1
        context.friendly_counts[destination] += 1
        context.reserved_destinations.add(destination)
        return True
    return False


def _queue_away_from_enemies(
    unit: Movable,
    enemies: Sequence[object],
    context: MovementContext,
    beacon_position: Position,
    *,
    trigger_distance: int = UNIT_EVADE_TRIGGER_DISTANCE,
    keep_core_neighbors_clear: bool = False,
) -> bool:
    """Prefer escape over work or combat whenever a visible enemy is nearby."""
    current_enemy_distance = _minimum_enemy_distance(unit.position, enemies)
    if not enemies or current_enemy_distance > trigger_distance:
        return False

    core_neighbors = set()
    if keep_core_neighbors_clear and context.core_position is not None:
        core_neighbors = {
            _destination(context.core_position, direction)
            for direction in CARDINAL_DIRECTIONS
        }

    candidates: list[tuple[tuple[object, ...], Direction]] = []
    for index, direction in enumerate(CARDINAL_DIRECTIONS):
        destination = _destination(unit.position, direction)
        if (
            destination in core_neighbors
            and unit.position != context.core_position
        ):
            continue
        candidates.append(
            (
                (
                    *_position_threat_key(
                        destination,
                        enemies,
                        context.obstacles,
                    ),
                    -_distance(destination, beacon_position),
                    index,
                ),
                direction,
            )
        )

    directions = tuple(
        direction for _, direction in sorted(candidates)
    )
    return _queue_move(
        unit,
        directions,
        context,
        avoid_danger=False,
    )


def _select_delivery_lane(context: MovementContext) -> Position | None:
    """Keep one passable Core neighbor available for cargo handoff."""
    if context.core_position is None:
        return None
    candidates: list[tuple[int, int, Position]] = []
    for index, direction in enumerate(CARDINAL_DIRECTIONS):
        destination = _destination(context.core_position, direction)
        if not _is_signed_int64_position(destination):
            continue
        if destination in context.obstacles:
            continue
        if destination in context.enemy_cells or destination in context.danger_cells:
            continue
        candidates.append(
            (int(context.friendly_counts[destination] > 0), index, destination)
        )
    return min(candidates, default=(0, 0, None))[2]


def _queue_core_defender_egress(
    turn: Turn,
    context: MovementContext,
    enemies: Sequence[object],
    healing_holds: set[UUID] | None = None,
    *,
    force_departure: bool = False,
) -> set[UUID]:
    """Move a defender off the Core before Workers plan their deliveries."""
    core = turn.core
    if core is None or context.core_position is None:
        return set()

    defenders = sorted(
        (
            defender
            for defender in (*turn.vanguards, *turn.rangers)
            if defender.position == core.position
        ),
        key=_uuid_sort_key,
    )
    if not defenders:
        return set()

    defender = defenders[0]
    if _legal_attack_targets(defender, enemies, context.obstacles):
        return set()

    imminent_cargo = any(
        worker.cargo > 0
        and _distance(worker.position, core.position) <= CORE_SHORT_CARGO_ETA
        for worker in turn.workers
    )
    missing_hp = _unit_max_hp(defender.unit_type) - defender.hp
    if (
        not force_departure
        and healing_holds is not None
        and defender.id in healing_holds
        and missing_hp > 0
        and not imminent_cargo
        and core.view.state is CoreState.NORMAL
        and core.hp == 5
        and turn.resources >= UNIT_HEAL_RESOURCE_RESERVE + missing_hp
    ):
        return set()

    current_enemy_distance = _minimum_enemy_distance(defender.position, enemies)
    candidates: list[tuple[tuple[int, int, int, int], Direction]] = []
    for index, direction in enumerate(CARDINAL_DIRECTIONS):
        destination = _destination(defender.position, direction)
        if not _is_signed_int64_position(destination):
            continue
        if destination in context.obstacles:
            continue
        if destination in context.enemy_cells or destination in context.danger_cells:
            continue
        if destination in context.reserved_destinations:
            continue
        if context.friendly_counts[destination] >= 2:
            continue

        enemy_distance = _minimum_enemy_distance(destination, enemies)
        if (
            enemies
            and current_enemy_distance <= UNIT_EVADE_TRIGGER_DISTANCE
            and enemy_distance < current_enemy_distance
        ):
            continue
        candidates.append(
            (
                (
                    int(destination != context.delivery_lane),
                    enemy_distance,
                    _distance(destination, turn.beacon.position),
                    -index,
                ),
                direction,
            )
        )

    directions = tuple(
        direction for _, direction in sorted(candidates, reverse=True)
    )
    if not _queue_move(
        defender,
        directions,
        context,
        allow_single_friendly_transit=True,
    ):
        return set()
    return {defender.id}


def _queue_core_delivery_handoff(
    turn: Turn,
    context: MovementContext,
    enemies: Sequence[object],
    *,
    force_departure: bool = False,
) -> set[UUID]:
    """Break a full Core cell by shifting a friendly corridor from outside in."""
    core = turn.core
    if core is None or context.core_position is None:
        return set()
    departing_workers = sorted(
        (
            worker
            for worker in turn.workers
            if worker.position == core.position
            and (
                force_departure
                or worker.cargo == 0
                or turn.resource_space == 0
            )
        ),
        key=_uuid_sort_key,
    )
    if not departing_workers:
        return set()

    departing_worker = departing_workers[0]
    passable_neighbors = []
    for direction in CARDINAL_DIRECTIONS:
        destination = _destination(core.position, direction)
        if not _is_signed_int64_position(destination):
            continue
        if destination in context.obstacles:
            continue
        if destination in context.enemy_cells or destination in context.danger_cells:
            continue
        passable_neighbors.append((direction, destination))
    if not passable_neighbors:
        return set()
    if force_departure:
        directions = tuple(
            direction
            for direction, position in sorted(
                passable_neighbors,
                key=lambda item: (
                    context.friendly_counts[item[1]],
                    int(item[1] == context.delivery_lane),
                    CARDINAL_DIRECTIONS.index(item[0]),
                ),
            )
            if context.friendly_counts[position] < 2
        )
        if _queue_move(
            departing_worker,
            directions,
            context,
            allow_single_friendly_transit=True,
        ):
            return {departing_worker.id}
    if any(
        context.friendly_counts[position] == 0
        for _, position in passable_neighbors
    ):
        # Normal resource/scout routing clears a free lane without overriding
        # useful work. Coordination is needed only when all exits are occupied.
        return set()

    units_by_position: dict[Position, list[object]] = {}
    for unit in turn.units:
        if _legal_attack_targets(unit, enemies, context.obstacles):
            continue
        units_by_position.setdefault(unit.position, []).append(unit)
    for units in units_by_position.values():
        units.sort(
            key=lambda unit: (
                int(getattr(unit, "cargo", 0) > 0),
                _uuid_sort_key(unit),
            )
        )
    starts = sorted(
        (
            (position, units_by_position[position][0], index)
            for index, (_, position) in enumerate(passable_neighbors)
            if 0 < context.friendly_counts[position] <= 2
            and position in units_by_position
        ),
        key=lambda item: (
            int(getattr(item[1], "cargo", 0) > 0),
            item[2],
        ),
    )
    chain: tuple[Position, ...] | None = None
    for start, _, _ in starts:
        frontier: deque[tuple[Position, tuple[Position, ...]]] = deque(
            [(start, (start,))]
        )
        visited = {core.position, start}
        while frontier:
            current, path = frontier.popleft()
            if len(path) >= CORE_DELIVERY_CHAIN_MAX:
                continue
            for direction in CARDINAL_DIRECTIONS:
                destination = _destination(current, direction)
                if destination in visited or not _is_signed_int64_position(destination):
                    continue
                if destination in context.obstacles:
                    continue
                if destination in context.enemy_cells or destination in context.danger_cells:
                    continue
                if destination in context.reserved_destinations:
                    continue
                occupants = context.friendly_counts[destination]
                if occupants == 0:
                    chain = (*path, destination)
                    frontier.clear()
                    break
                if occupants != 1 or destination not in units_by_position:
                    continue
                visited.add(destination)
                frontier.append((destination, (*path, destination)))
            if chain is not None:
                break
        if chain is not None:
            break
    if chain is None:
        return set()

    handoff: set[UUID] = set()
    for source, destination in reversed(tuple(zip(chain[:-1], chain[1:]))):
        unit = units_by_position[source][0]
        direction = _direction_to_adjacent(source, destination)
        if direction is None or not _queue_move(unit, (direction,), context):
            return handoff
        handoff.add(unit.id)

    first_position = chain[0]
    first_direction = _direction_to_adjacent(core.position, first_position)
    if first_direction is None or not _queue_move(
        departing_worker,
        (first_direction,),
        context,
        allow_single_friendly_transit=True,
    ):
        return handoff
    handoff.add(departing_worker.id)

    for worker in sorted(
        (
            worker
            for worker in turn.workers
            if not force_departure
            if turn.resource_space > 0
            and worker.cargo > 0
            and worker.id not in handoff
            and _distance(worker.position, core.position) == 1
        ),
        key=_uuid_sort_key,
    ):
        direction = _direction_to_adjacent(worker.position, core.position)
        if direction is not None and _queue_move(
            worker,
            (direction,),
            context,
            allow_core_entry=True,
        ):
            handoff.add(worker.id)
            break
    return handoff


def _queue_toward(
    unit: Movable,
    target: Position,
    context: MovementContext,
    *,
    allow_core_entry: bool = False,
    allow_target_entry: bool = False,
    allow_single_friendly_transit: bool = False,
    discouraged: set[Position] | None = None,
    avoid_danger: bool = True,
    target_radius: int = 0,
) -> bool:
    blocked = (
        set(context.obstacles)
        | set(context.enemy_cells)
        | set(context.allied_cells)
        | set(context.reserved_destinations)
    )
    if avoid_danger:
        blocked.update(context.danger_cells)
    for cell, occupants in context.friendly_counts.items():
        if occupants <= 0 or cell == unit.position:
            continue
        entering_core = (
            allow_core_entry
            and cell == context.core_position
            and occupants < 2
        )
        entering_target = allow_target_entry and cell == target and occupants < 2
        entering_single_friendly = (
            allow_single_friendly_transit and occupants < 2
        )
        if not entering_core and not entering_target and not entering_single_friendly:
            blocked.add(cell)

    combined_discouraged = set(context.discouraged_cells)
    combined_discouraged.update(discouraged or ())
    directions = _path_directions(
        unit.position,
        target,
        blocked,
        discouraged=combined_discouraged,
        target_radius=target_radius,
    )
    if not directions:
        return False
    return _queue_move(
        unit,
        directions,
        context,
        allow_core_entry=allow_core_entry,
        allow_friendly_entry=target if allow_target_entry else None,
        allow_single_friendly_transit=allow_single_friendly_transit,
        avoid_danger=avoid_danger,
    )


def _direction_to_adjacent(start: Position, target: Position) -> Direction | None:
    delta = target[0] - start[0], target[1] - start[1]
    for direction in CARDINAL_DIRECTIONS:
        if direction.delta == delta:
            return direction
    return None


def _intermediate_cells(start: Position, target: Position) -> tuple[Position, ...]:
    dx = target[0] - start[0]
    dy = target[1] - start[1]
    distance = max(abs(dx), abs(dy))
    if distance == 0:
        return ()
    if dx != 0 and dy != 0 and abs(dx) != abs(dy):
        return ()
    step_x = 0 if dx == 0 else (1 if dx > 0 else -1)
    step_y = 0 if dy == 0 else (1 if dy > 0 else -1)
    return tuple(
        (start[0] + step_x * step, start[1] + step_y * step)
        for step in range(1, distance)
    )


def _ranger_line_range(start: Position, target: Position) -> int | None:
    dx = abs(target[0] - start[0])
    dy = abs(target[1] - start[1])
    if dx == 0 and dy == 0:
        return None
    if dx == 0 or dy == 0 or dx == dy:
        return max(dx, dy)
    return None


def _ranger_can_shoot(
    start: Position,
    target: Position,
    obstacles: set[Position],
) -> bool:
    line_range = _ranger_line_range(start, target)
    return (
        line_range is not None
        and 1 <= line_range <= 3
        and not any(
            cell in obstacles for cell in _intermediate_cells(start, target)
        )
    )


def _enemy_threat_cells(
    enemies: Sequence[object],
    obstacles: set[Position],
) -> set[Position]:
    danger_cells: set[Position] = set()
    for enemy in enemies:
        unit_type = getattr(enemy, "unit_type", None)
        if unit_type is UnitType.VANGUARD:
            danger_cells.update(
                _destination(enemy.position, direction)
                for direction in CARDINAL_DIRECTIONS
            )
        elif unit_type is UnitType.RANGER:
            for dx, dy in RANGER_LINE_VECTORS:
                for distance in range(1, 4):
                    cell = (
                        enemy.position[0] + dx * distance,
                        enemy.position[1] + dy * distance,
                    )
                    if cell in obstacles:
                        break
                    danger_cells.add(cell)
    return danger_cells


def _projected_core_damage(
    core_position: Position,
    enemies: Sequence[object],
    obstacles: set[Position],
) -> int:
    damage = 0
    for enemy in enemies:
        if getattr(enemy, "kind", None) == "CORE":
            continue
        if enemy.unit_type is UnitType.VANGUARD:
            if _distance(core_position, enemy.position) == 1:
                damage += 1
        elif enemy.unit_type is UnitType.RANGER and _ranger_can_shoot(
            enemy.position,
            core_position,
            obstacles,
        ):
            damage += 1
    return damage


def _core_threatening_enemies(
    core_position: Position,
    enemies: Sequence[object],
    obstacles: set[Position],
) -> tuple[object, ...]:
    threats = []
    for enemy in enemies:
        if getattr(enemy, "kind", None) == "CORE":
            continue
        if enemy.unit_type is UnitType.VANGUARD:
            if _distance(core_position, enemy.position) == 1:
                threats.append(enemy)
        elif enemy.unit_type is UnitType.RANGER and _ranger_can_shoot(
            enemy.position,
            core_position,
            obstacles,
        ):
            threats.append(enemy)
    return tuple(threats)


def _guard_post(
    unit: Movable,
    core_position: Position,
    context: MovementContext,
    preferred_directions: Sequence[Direction],
    radius: int,
) -> Position:
    """Pick a stable outer post while keeping Core neighbors clear for cargo."""
    for direction in preferred_directions:
        dx, dy = direction.delta
        destination = (
            core_position[0] + dx * radius,
            core_position[1] + dy * radius,
        )
        if destination in context.obstacles:
            continue
        if destination in context.resource_cells:
            continue
        if destination in context.enemy_cells or destination in context.danger_cells:
            continue
        if destination in context.reserved_destinations:
            continue
        occupants = context.friendly_counts[destination]
        if destination == unit.position:
            return destination
        if occupants:
            continue
        return destination
    return unit.position


def _combat_target_key(origin: Position, enemy: object) -> tuple[object, ...]:
    unit_type = getattr(enemy, "unit_type", None)
    type_priority = (
        0
        if unit_type is UnitType.RANGER
        else 1
        if unit_type is UnitType.VANGUARD
        else 2
        if unit_type is UnitType.WORKER
        else 3
    )
    return (
        type_priority,
        getattr(enemy, "hp", 5),
        _distance(origin, enemy.position),
        _uuid_sort_key(enemy),
    )


def _legal_attack_targets(
    unit: object,
    enemies: Sequence[object],
    obstacles: set[Position],
) -> tuple[object, ...]:
    if unit.unit_type is UnitType.VANGUARD:
        return tuple(
            enemy
            for enemy in enemies
            if _distance(unit.position, enemy.position) == 1
        )
    if unit.unit_type is UnitType.RANGER:
        return tuple(
            enemy
            for enemy in enemies
            if _ranger_can_shoot(unit.position, enemy.position, obstacles)
        )
    return ()


def _core_attack_priority_ids(
    turn: Turn,
    target: object | None,
    obstacles: set[Position],
    enemies: Sequence[object] | None = None,
) -> set[UUID]:
    visible_target = (
        target.visible_enemy
        if isinstance(target, CoreRaidTarget)
        else target
    )
    if visible_target is None:
        return set()
    target_id = getattr(visible_target, "id", None)
    if getattr(visible_target, "kind", None) != "CORE":
        return {target_id}

    core_attackers = tuple(
        unit
        for unit in (*turn.vanguards, *turn.rangers)
        if visible_target in _legal_attack_targets(
            unit,
            (visible_target,),
            obstacles,
        )
    )
    if not core_attackers:
        return {target_id}

    threats = tuple(
        enemy
        for enemy in (turn.visible_enemies if enemies is None else enemies)
        if getattr(enemy, "kind", None) != "CORE"
        and getattr(enemy, "unit_type", None)
        in {UnitType.VANGUARD, UnitType.RANGER}
        and any(
            _legal_attack_targets(enemy, (attacker,), obstacles)
            for attacker in core_attackers
        )
    )
    if not threats:
        return {target_id}

    remaining_core = visible_target.hp + visible_target.shield
    remaining_hp = [attacker.hp for attacker in core_attackers]
    while remaining_hp:
        remaining_core -= len(remaining_hp)
        if remaining_core <= 0:
            return {target_id}
        incoming = len(threats)
        while incoming and remaining_hp:
            victim = min(range(len(remaining_hp)), key=remaining_hp.__getitem__)
            remaining_hp[victim] -= 1
            incoming -= 1
            if remaining_hp[victim] <= 0:
                remaining_hp.pop(victim)
    return {enemy.id for enemy in threats}


def _defense_post_directions(
    core_position: Position,
    enemies: Sequence[object],
    fallback: Sequence[Direction],
    *,
    defender_index: int = 0,
    priority_ids: set[UUID] | None = None,
) -> tuple[Direction, ...]:
    combat_enemies = tuple(
        enemy
        for enemy in enemies
        if getattr(enemy, "kind", None) != "CORE"
        and getattr(enemy, "unit_type", None)
        in {UnitType.VANGUARD, UnitType.RANGER}
    )
    if not combat_enemies:
        return tuple(fallback)
    priority_ids = priority_ids or set()
    axis_enemies: dict[Direction, object] = {}
    for enemy in combat_enemies:
        axis = _directions_toward(core_position, enemy.position)[0]
        current = axis_enemies.get(axis)
        if current is None or (
            int(enemy.id not in priority_ids),
            _distance(core_position, enemy.position),
            _combat_target_key(core_position, enemy),
        ) < (
            int(current.id not in priority_ids),
            _distance(core_position, current.position),
            _combat_target_key(core_position, current),
        ):
            axis_enemies[axis] = enemy
    ordered_axes = tuple(
        axis
        for axis, _ in sorted(
            axis_enemies.items(),
            key=lambda item: (
                int(item[1].id not in priority_ids),
                _distance(core_position, item[1].position),
                _combat_target_key(core_position, item[1]),
                CARDINAL_DIRECTIONS.index(item[0]),
            ),
        )
    )
    primary = ordered_axes[defender_index % len(ordered_axes)]
    return (primary,) + tuple(
        direction
        for direction in _directions_toward(
            core_position,
            axis_enemies[primary].position,
        )
        if direction is not primary
    )


def _core_target_score(
    turn: Turn,
    enemy_core: object,
    strike_distance: int,
    enemies: Sequence[object] | None = None,
) -> tuple[object, ...]:
    protector_hp = sum(
        enemy.hp
        for enemy in (turn.visible_enemies if enemies is None else enemies)
        if getattr(enemy, "kind") != "CORE"
        and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
        and _distance(enemy.position, enemy_core.position) <= CORE_PROTECTOR_RADIUS
    )
    durability = enemy_core.hp + enemy_core.shield
    score = (
        strike_distance
        + durability * CORE_TARGET_DURABILITY_WEIGHT
        + protector_hp * CORE_TARGET_PROTECTOR_HP_WEIGHT
    )
    return score, strike_distance, _uuid_sort_key(enemy_core)


def _strike_rally_position(
    units: Sequence[object],
    target: Position,
) -> Position | None:
    if not units:
        return None
    positions = tuple(unit.position for unit in units)
    return min(
        positions,
        key=lambda position: (
            sum(_distance(position, other) for other in positions),
            _distance(position, target),
            position,
        ),
    )


def _force_stage_targets(
    worker_target: int,
    workers: int,
    vanguards: int,
    rangers: int,
) -> tuple[int, int, int]:
    for stage_workers, stage_vanguards, stage_rangers in FORCE_STAGES:
        targets = (
            min(worker_target, stage_workers),
            stage_vanguards,
            stage_rangers,
        )
        if any(
            current < target
            for current, target in zip(
                (workers, vanguards, rangers),
                targets,
            )
        ):
            return targets
    return worker_target, DEFENSE_VANGUARD_TARGET, DEFENSE_RANGER_TARGET


def _next_force_unit_type(
    worker_target: int,
    workers: int,
    vanguards: int,
    rangers: int,
) -> UnitType | None:
    target_workers, target_vanguards, target_rangers = _force_stage_targets(
        worker_target,
        workers,
        vanguards,
        rangers,
    )
    if workers < target_workers:
        return UnitType.WORKER
    if vanguards >= target_vanguards and rangers >= target_rangers:
        return None
    if vanguards >= target_vanguards:
        return UnitType.RANGER
    if rangers >= target_rangers:
        return UnitType.VANGUARD
    return (
        UnitType.VANGUARD
        if vanguards * target_rangers <= rangers * target_vanguards
        else UnitType.RANGER
    )


def _uuid_sort_key(obj: object) -> bytes:
    identifier = getattr(obj, "id")
    return getattr(identifier, "bytes")


def _unit_max_hp(unit_type: UnitType) -> int:
    return 4 if unit_type is UnitType.VANGUARD else 2


class CoreFarmer:
    def __init__(
        self,
        *,
        worker_target: int = DEFAULT_WORKER_TARGET,
        beacon_policy: str = DEFAULT_BEACON_POLICY,
        compatibility_marker: Path | None = DEFAULT_COMPATIBILITY_MARKER,
        alliance_coordinator: AllianceCoordinator | None = None,
    ) -> None:
        if not 1 <= worker_target <= MAX_WORKER_TARGET:
            raise ValueError(
                f"worker_target must be between 1 and {MAX_WORKER_TARGET}"
            )
        if beacon_policy not in {"hold", "pursue", "retreat"}:
            raise ValueError("beacon_policy must be 'hold', 'pursue', or 'retreat'")
        self.worker_target = worker_target
        self.beacon_policy = beacon_policy
        self.compatibility_marker = compatibility_marker
        self.alliance_coordinator = alliance_coordinator
        self.alliance_peers: tuple[AlliancePeer, ...] = ()
        self.allied_object_ids: set[UUID] = set()
        self.allied_usernames: set[str] = set()
        self.allied_occupied_cells: set[Position] = set()
        self.alliance_leader: AlliancePeer | None = None
        self.alliance_turn_tick: int | None = None
        self.compatibility_hold = False
        self.known_obstacles: set[Position] = set()
        self.scout_slots: dict[UUID, int] = {}
        self.scout_stages: dict[UUID, int] = {}
        self.scout_progress: dict[UUID, ScoutProgress] = {}
        self.scout_target_last_visited: dict[Position, int] = {}
        self.scout_claims: set[Position] = set()
        self.scout_chunk_last_seen: dict[Position, int] = {}
        self.worker_history: dict[UUID, deque[Position]] = {}
        self.resource_last_seen: dict[Position, int] = {}
        self.resource_intents: dict[UUID, Position] = {}
        self.resource_progress: dict[UUID, ResourceProgress] = {}
        self.resource_cooldowns: dict[tuple[UUID, Position], int] = {}
        self.worker_modes: dict[UUID, str] = {}
        self.worker_targets: dict[UUID, Position] = {}
        self.last_danger_cells: set[Position] = set()
        self.last_released_targets: dict[UUID, Position] = {}
        self.recovery_until_tick = 0
        self.recovery_reason = "NONE"
        self.last_core_move_tick = -RETREAT_SERVICE_TICKS
        self.last_retreat_direction: Direction | None = None
        self.active_core_move_reason: str | None = None
        self.last_core_cancel_reason = "NONE"
        self.last_projected_core_damage = 0
        self.last_core_survival_margin = 0
        self.enemy_core_sightings: dict[UUID, EnemyCoreSighting] = {}
        self.enemy_unit_sightings: dict[UUID, EnemyCoreSighting] = {}
        self.enemy_unit_motion: dict[UUID, EnemyUnitMotion] = {}
        self.active_enemy_ids: set[UUID] = set()
        self.preemptive_evade_enemy_ids: set[UUID] = set()
        self.pursuing_enemy_ids: set[UUID] = set()
        self.recent_attack_until_tick = 0
        self.recent_core_attack_until_tick = 0
        self.recent_attack_threats: dict[UUID, RememberedThreat] = {}
        self.threat_assessment = ThreatAssessment()
        self.combat_pressure_active = False
        self.squad_return_ids: set[UUID] = set()
        self.scout_return_ids: set[UUID] = set()
        self.scout_cooldown_until: dict[UUID, int] = {}
        self.squad_disengage_until_tick = 0
        self.healing_defender_ids: set[UUID] = set()
        self.stationary_core_memory: dict[UUID, EnemyCoreSighting] = {}
        self.isolated_core_target_id: UUID | None = None
        self.core_raid_rally_position: Position | None = None
        self.core_raid_launched = False
        self.core_raid_started_tick: int | None = None
        self.core_raid_vanguard_ids: set[UUID] = set()
        self.core_raid_ranger_ids: set[UUID] = set()
        self.core_observer_candidates: dict[UUID, UUID] = {}
        self.core_observer_target_id: UUID | None = None
        self.core_raid_spotter_id: UUID | None = None
        self.stationary_unit_target_id: UUID | None = None
        self.beacon_runner_id: UUID | None = None
        self.threat_caution_until_tick = 0
        self.startup_tick: int | None = None
        self.manual_order_ids: tuple[int, ...] = ()
        self.production_weights: dict[UnitType, int] | None = None
        self.expedition_members: dict[int, set[UUID]] = {}
        self.revenge_usernames: set[str] = set()

    def _refresh_alliance(self, turn: Turn) -> None:
        coordinator = self.alliance_coordinator
        if coordinator is None:
            self.alliance_peers = ()
            self.allied_object_ids.clear()
            self.allied_usernames.clear()
            self.allied_occupied_cells.clear()
            self.alliance_leader = None
            return
        try:
            coordinator.publish(turn)
            deadline = time.monotonic() + coordinator.barrier_timeout_seconds
            while True:
                peers = coordinator.peers()
                fresh_accounts = {
                    peer.account_id
                    for peer in peers
                    if peer.tick >= turn.tick
                }
                if len(fresh_accounts) >= coordinator.expected_members:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.05, remaining))
        except OSError as exc:
            print(
                f"WARNING tick={turn.tick} alliance_coordination_error="
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            peers = ()
        self.alliance_peers = peers
        self.alliance_turn_tick = turn.tick
        self.allied_object_ids = {
            identifier
            for peer in peers
            if peer.account_id != coordinator.account_id
            for identifier in (
                *((peer.core_id,) if peer.core_id is not None else ()),
                *peer.unit_ids,
            )
        }
        self.allied_usernames = {
            peer.username
            for peer in peers
            if peer.account_id != coordinator.account_id and peer.username
        }
        self.revenge_usernames.difference_update(
            username.casefold() for username in self.allied_usernames
        )
        self.allied_occupied_cells = {
            position
            for peer in peers
            if peer.account_id != coordinator.account_id
            for position in (
                *((peer.core_position,) if peer.core_position is not None else ()),
                *peer.unit_positions,
            )
        }
        self.allied_occupied_cells.update(
            enemy.position
            for enemy in turn.visible_enemies
            if enemy.id in self.allied_object_ids
            or (
                getattr(enemy, "kind", None) == "CORE"
                and getattr(enemy, "owner_username", "") in self.allied_usernames
            )
        )
        self.enemy_core_sightings = {
            identifier: sighting
            for identifier, sighting in self.enemy_core_sightings.items()
            if identifier not in self.allied_object_ids
        }
        self.enemy_unit_sightings = {
            identifier: sighting
            for identifier, sighting in self.enemy_unit_sightings.items()
            if identifier not in self.allied_object_ids
        }
        self.enemy_unit_motion = {
            identifier: motion
            for identifier, motion in self.enemy_unit_motion.items()
            if identifier not in self.allied_object_ids
        }
        self.stationary_core_memory = {
            identifier: sighting
            for identifier, sighting in self.stationary_core_memory.items()
            if identifier not in self.allied_object_ids
        }
        self.active_enemy_ids.difference_update(self.allied_object_ids)
        self.preemptive_evade_enemy_ids.difference_update(self.allied_object_ids)
        self.pursuing_enemy_ids.difference_update(self.allied_object_ids)
        for identifier in self.allied_object_ids:
            self.recent_attack_threats.pop(identifier, None)
        if self.isolated_core_target_id in self.allied_object_ids:
            self._release_core_raid(forget_position=True)
        if self.stationary_unit_target_id in self.allied_object_ids:
            self.stationary_unit_target_id = None
        viable = tuple(peer for peer in peers if peer.core_position is not None)
        self.alliance_leader = (
            min(viable, key=lambda peer: (-peer.population, peer.account_id))
            if viable
            else None
        )

    def _hostile_enemies(self, turn: Turn) -> tuple[object, ...]:
        return tuple(
            enemy
            for enemy in turn.visible_enemies
            if enemy.id not in self.allied_object_ids
            and (
                getattr(enemy, "kind", None) != "CORE"
                or getattr(enemy, "owner_username", "") not in self.allied_usernames
            )
        )

    def _alliance_rally_target(self, turn: Turn) -> Position | None:
        coordinator = self.alliance_coordinator
        leader = self.alliance_leader
        core = turn.core
        if (
            coordinator is None
            or leader is None
            or core is None
            or leader.account_id == coordinator.account_id
            or leader.core_position is None
            or _distance(core.position, leader.core_position) <= ALLY_CORE_RALLY_RADIUS
        ):
            return None
        return leader.core_position

    @property
    def alliance_ready(self) -> bool:
        coordinator = self.alliance_coordinator
        if coordinator is None:
            return True
        if self.alliance_turn_tick is None:
            return False
        fresh_accounts = {
            peer.account_id
            for peer in self.alliance_peers
            if peer.tick >= self.alliance_turn_tick
        }
        return len(fresh_accounts) >= coordinator.expected_members

    @property
    def recovery_mode(self) -> bool:
        return self.recovery_until_tick > 0

    def strategy_phase(self, turn: Turn) -> str:
        if turn.core is None:
            return "RESPAWNING"
        if self.compatibility_hold:
            return "COMPATIBILITY_HOLD"
        if self.recovery_mode:
            return "RECOVERY"
        if (
            self.isolated_core_target_id is not None
            or self.stationary_unit_target_id is not None
        ):
            return "ASSAULT"
        next_unit = _next_force_unit_type(
            self.worker_target,
            len(turn.workers),
            len(turn.vanguards),
            len(turn.rangers),
        )
        if next_unit is UnitType.WORKER:
            return "MOBILIZE_ECONOMY"
        if next_unit is not None:
            return "MOBILIZE_ARMY"
        if self.beacon_runner_id is not None:
            return "BEACON_CAMPAIGN"
        return "EXPAND_CONTROL"

    def _refresh_compatibility_hold(self) -> None:
        if self.compatibility_marker is None:
            self.compatibility_hold = False
            return
        try:
            self.compatibility_hold = self.compatibility_marker.exists()
        except OSError:
            self.compatibility_hold = True

    def _release_core_observer(self) -> None:
        self.core_observer_target_id = None
        self.core_raid_spotter_id = None

    def _release_core_raid(self, *, forget_position: bool = False) -> None:
        target_id = self.isolated_core_target_id
        self.isolated_core_target_id = None
        self.core_raid_rally_position = None
        self.core_raid_launched = False
        self.core_raid_started_tick = None
        self.core_raid_vanguard_ids.clear()
        self.core_raid_ranger_ids.clear()
        if target_id is not None and forget_position:
            self.stationary_core_memory.pop(target_id, None)
            self.core_observer_candidates.pop(target_id, None)
        if self.core_observer_target_id == target_id:
            self._release_core_observer()

    def _infer_core_observer(self, turn: Turn, enemy_core: object) -> UUID | None:
        candidates = [
            worker
            for worker in turn.workers
            if worker.cargo == 0
            and worker.position not in turn.resource_cells
            and _distance(worker.position, enemy_core.position)
            <= CORE_OBSERVER_MAX_DISTANCE
        ]
        if not candidates:
            return None
        newly_exposing = [
            worker
            for worker in candidates
            if not self.worker_history.get(worker.id)
            or _distance(
                self.worker_history[worker.id][-1],
                enemy_core.position,
            )
            > CORE_OBSERVER_MAX_DISTANCE
        ]
        pool = newly_exposing or candidates
        return min(
            pool,
            key=lambda worker: (
                abs(
                    _distance(worker.position, enemy_core.position)
                    - CORE_OBSERVER_MAX_DISTANCE
                ),
                _uuid_sort_key(worker),
            ),
        ).id

    def _assess_threat(
        self,
        turn: Turn,
        *,
        breakout: bool = False,
        local_squad_contact: bool = False,
    ) -> ThreatAssessment:
        core = turn.core
        if core is None:
            return ThreatAssessment(
                lifecycle=LifecycleMode.RESPAWNING,
                primary_reason="CORE_RESPAWNING",
            )

        visible_combat_enemies = tuple(
            enemy
            for enemy in self._hostile_enemies(turn)
            if getattr(enemy, "kind") != "CORE"
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
        )
        near_core_enemy_ids = frozenset(
            enemy.id
            for enemy in visible_combat_enemies
            if _distance(core.position, enemy.position)
            <= CORE_EVADE_TRIGGER_DISTANCE
        )
        threatening_core_enemy_ids = frozenset(
            enemy.id
            for enemy in _core_threatening_enemies(
                core.position,
                visible_combat_enemies,
                self.known_obstacles,
            )
        )
        recent_attack = turn.tick <= self.recent_attack_until_tick
        recent_core_attack = turn.tick <= self.recent_core_attack_until_tick
        disengaging = turn.tick <= self.squad_disengage_until_tick
        caution = turn.tick <= self.threat_caution_until_tick

        if self.compatibility_hold:
            lifecycle = LifecycleMode.COMPATIBILITY_HOLD
        elif self.recovery_mode:
            lifecycle = LifecycleMode.RECOVERY
        else:
            lifecycle = LifecycleMode.ACTIVE

        if breakout:
            level = ThreatLevel.BREAKOUT
            primary_reason = "MULTI_AXIS_BREAKOUT"
        elif recent_core_attack:
            level = ThreatLevel.ENGAGED
            primary_reason = "RECENT_CORE_ATTACK"
        elif local_squad_contact:
            level = ThreatLevel.ENGAGED
            primary_reason = "LOCAL_SQUAD_CONTACT"
        elif recent_attack:
            level = ThreatLevel.ENGAGED
            primary_reason = "RECENT_FLEET_ATTACK"
        elif threatening_core_enemy_ids:
            level = ThreatLevel.ENGAGED
            primary_reason = "CURRENT_CORE_ATTACK"
        elif self.pursuing_enemy_ids:
            level = ThreatLevel.PRE_EVADE
            primary_reason = "CONFIRMED_PURSUIT"
        elif self.preemptive_evade_enemy_ids:
            level = ThreatLevel.PRE_EVADE
            primary_reason = "TIME_TO_RANGE"
        elif near_core_enemy_ids:
            level = ThreatLevel.PRE_EVADE
            primary_reason = "CORE_DISTANCE_FALLBACK"
        elif self.active_enemy_ids:
            level = ThreatLevel.ALERT
            primary_reason = "HOSTILE_ACTIVITY"
        elif disengaging:
            level = ThreatLevel.ALERT
            primary_reason = "SQUAD_DISENGAGING"
        else:
            level = ThreatLevel.NORMAL
            primary_reason = "NONE"

        return ThreatAssessment(
            lifecycle=lifecycle,
            level=level,
            primary_reason=primary_reason,
            recent_attack=recent_attack,
            recent_core_attack=recent_core_attack,
            activity_enemy_ids=frozenset(self.active_enemy_ids),
            preemptive_enemy_ids=frozenset(self.preemptive_evade_enemy_ids),
            pursuing_enemy_ids=frozenset(self.pursuing_enemy_ids),
            near_core_enemy_ids=near_core_enemy_ids,
            threatening_core_enemy_ids=threatening_core_enemy_ids,
            disengaging=disengaging,
            local_squad_contact=local_squad_contact,
            caution=caution,
            breakout=breakout,
        )

    def _refresh_threat_assessment(
        self,
        turn: Turn,
        *,
        breakout: bool = False,
        local_squad_contact: bool = False,
    ) -> None:
        self.threat_assessment = self._assess_threat(
            turn,
            breakout=breakout,
            local_squad_contact=local_squad_contact,
        )
        self.combat_pressure_active = self.threat_assessment.combat_pressure

    def _remembered_retreat_threats(
        self,
        turn: Turn,
        visible_enemies: Sequence[object],
    ) -> tuple[RememberedThreat, ...]:
        visible_ids = {enemy.id for enemy in visible_enemies}
        remembered = {
            threat.id: threat
            for threat in self.recent_attack_threats.values()
            if threat.id not in visible_ids and threat.expires_tick >= turn.tick
        }
        tracked_ids = (
            self.active_enemy_ids
            | self.preemptive_evade_enemy_ids
            | self.pursuing_enemy_ids
        )
        for unit_id in tracked_ids - visible_ids - set(remembered):
            motion = self.enemy_unit_motion.get(unit_id)
            if motion is None:
                continue
            remembered[unit_id] = RememberedThreat(
                id=unit_id,
                position=motion.position,
                unit_type=motion.unit_type,
                expires_tick=max(
                    motion.activity_until_tick,
                    motion.preemptive_evade_until_tick,
                    turn.tick,
                ),
            )
        return tuple(
            threat
            for threat in remembered.values()
        )

    @staticmethod
    def _has_imminent_cargo(turn: Turn) -> bool:
        if turn.core is None:
            return False
        return any(
            worker.cargo > 0
            and _distance(worker.position, turn.core.position) <= CORE_SHORT_CARGO_ETA
            for worker in turn.workers
        )

    def _refresh_healing_defenders(
        self,
        turn: Turn,
        combat_target: object | None,
    ) -> None:
        core = turn.core
        defenders = (*turn.vanguards, *turn.rangers)
        defender_ids = {defender.id for defender in defenders}
        self.healing_defender_ids.intersection_update(defender_ids)
        for defender in defenders:
            if defender.hp >= _unit_max_hp(defender.unit_type):
                self.healing_defender_ids.discard(defender.id)
        if core is None:
            self.healing_defender_ids.clear()
            return
        defenders_by_id = {defender.id: defender for defender in defenders}
        for defender_id in tuple(self.healing_defender_ids):
            defender = defenders_by_id[defender_id]
            same_type_guard_remains = any(
                other.id != defender_id
                and other.unit_type is defender.unit_type
                for other in defenders
            )
            if defender.position != core.position and not same_type_guard_remains:
                self.healing_defender_ids.discard(defender_id)

        if self.healing_defender_ids:
            return
        if (
            combat_target is not None
            or self.combat_pressure_active
            or core.view.state is not CoreState.NORMAL
            or core.hp < 5
            or self._has_imminent_cargo(turn)
        ):
            return

        candidates = []
        for defender in defenders:
            max_hp = _unit_max_hp(defender.unit_type)
            missing_hp = max_hp - defender.hp
            same_type = [
                unit for unit in defenders if unit.unit_type is defender.unit_type
            ]
            if missing_hp <= 0:
                continue
            if defender.position != core.position and len(same_type) <= 1:
                continue
            if turn.resources < UNIT_HEAL_RESOURCE_RESERVE + missing_hp:
                continue
            candidates.append(
                (
                    int(defender.position != core.position),
                    defender.hp / max_hp,
                    _distance(defender.position, core.position),
                    _uuid_sort_key(defender),
                    defender,
                )
            )
        if candidates:
            self.healing_defender_ids.add(min(candidates)[4].id)

    def _healing_return_ready(self, turn: Turn, defender: object) -> bool:
        core = turn.core
        if core is None or defender.id not in self.healing_defender_ids:
            return False
        missing_hp = _unit_max_hp(defender.unit_type) - defender.hp
        return (
            missing_hp > 0
            and not self.combat_pressure_active
            and core.view.state is CoreState.NORMAL
            and core.hp == 5
            and turn.resources >= UNIT_HEAL_RESOURCE_RESERVE + missing_hp
            and not self._has_imminent_cargo(turn)
        )

    @staticmethod
    def _pursuit_is_confirmed(motion: EnemyUnitMotion) -> bool:
        return motion.pursuit_score > 0 and (
            motion.core_distance <= CORE_EVADE_TRIGGER_DISTANCE
            or motion.pursuit_score >= DISTANT_PURSUIT_SCORE_THRESHOLD
        )

    @staticmethod
    def _attack_range(unit_type: UnitType) -> int:
        return 3 if unit_type is UnitType.RANGER else 1

    def _attack_event_threats(
        self,
        event: object,
        visible_units: Mapping[UUID, object],
        prior_motion: Mapping[UUID, EnemyUnitMotion],
    ) -> tuple[tuple[UUID, Position, UnitType], ...]:
        actor_id = getattr(event, "actor_id", None)
        if actor_id is not None:
            visible_actor = visible_units.get(actor_id)
            if (
                visible_actor is not None
                and visible_actor.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                return ((actor_id, visible_actor.position, visible_actor.unit_type),)
            remembered_actor = prior_motion.get(actor_id)
            if (
                remembered_actor is not None
                and remembered_actor.unit_type
                in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                return (
                    (
                        actor_id,
                        remembered_actor.position,
                        remembered_actor.unit_type,
                    ),
                )

        target_position = getattr(event, "position", None)
        candidates: dict[UUID, tuple[UUID, Position, UnitType]] = {}
        for unit_id, motion in prior_motion.items():
            if motion.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            can_attack = target_position is None or (
                motion.unit_type is UnitType.VANGUARD
                and _distance(motion.position, target_position) == 1
            ) or (
                motion.unit_type is UnitType.RANGER
                and _ranger_can_shoot(
                    motion.position,
                    target_position,
                    self.known_obstacles,
                )
            )
            if can_attack:
                candidates[unit_id] = (
                    unit_id,
                    motion.position,
                    motion.unit_type,
                )
        if candidates:
            return tuple(candidates.values())

        for unit_id, enemy_unit in visible_units.items():
            if enemy_unit.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            can_attack = target_position is None or (
                enemy_unit.unit_type is UnitType.VANGUARD
                and _distance(enemy_unit.position, target_position) == 1
            ) or (
                enemy_unit.unit_type is UnitType.RANGER
                and _ranger_can_shoot(
                    enemy_unit.position,
                    target_position,
                    self.known_obstacles,
                )
            )
            if can_attack:
                candidates[unit_id] = (
                    unit_id,
                    enemy_unit.position,
                    enemy_unit.unit_type,
                )
        return tuple(candidates.values())

    def _update_enemy_awareness(self, turn: Turn) -> None:
        visible_cores = {
            enemy.id: enemy
            for enemy in self._hostile_enemies(turn)
            if getattr(enemy, "kind") == "CORE"
        }
        visible_units = {
            enemy.id: enemy
            for enemy in self._hostile_enemies(turn)
            if getattr(enemy, "kind") != "CORE"
        }
        prior_motion = dict(self.enemy_unit_motion)
        if (
            self.core_observer_target_id is not None
            and self.core_observer_target_id not in visible_cores
            and self.core_observer_target_id != self.isolated_core_target_id
        ):
            sighting = self.enemy_core_sightings.get(self.core_observer_target_id)
            if (
                sighting is None
                or turn.tick - sighting.last_tick > CORE_VISIBILITY_GAP_TICKS
            ):
                self._release_core_observer()
        if any(
            enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            for enemy in visible_units.values()
        ):
            self.threat_caution_until_tick = max(
                self.threat_caution_until_tick,
                turn.tick + POST_THREAT_CAUTION_TICKS,
            )

        hidden_unit_ids = set(self.enemy_unit_sightings) - set(visible_units)
        for unit_id in hidden_unit_ids:
            self.enemy_unit_sightings.pop(unit_id, None)
        self.active_enemy_ids.clear()
        self.preemptive_evade_enemy_ids.clear()
        self.pursuing_enemy_ids.clear()
        for unit_id in set(self.enemy_unit_motion) - set(visible_units):
            motion = self.enemy_unit_motion[unit_id]
            hidden_ticks = turn.tick - motion.last_tick
            if (
                hidden_ticks >= PURSUIT_MEMORY_TTL
                and turn.tick > motion.activity_until_tick
                and turn.tick > motion.preemptive_evade_until_tick
            ):
                self.enemy_unit_motion.pop(unit_id, None)
                continue
            if (
                turn.tick <= motion.activity_until_tick
                and motion.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                self.active_enemy_ids.add(unit_id)
            if (
                turn.tick <= motion.preemptive_evade_until_tick
                and motion.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                self.preemptive_evade_enemy_ids.add(unit_id)
            if (
                hidden_ticks < PURSUIT_MEMORY_TTL
                and motion.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                and self._pursuit_is_confirmed(motion)
            ):
                self.pursuing_enemy_ids.add(unit_id)
        for unit_id, enemy_unit in visible_units.items():
            sighting = self.enemy_unit_sightings.get(unit_id)
            if (
                sighting is not None
                and sighting.last_tick == turn.tick - 1
                and sighting.position == enemy_unit.position
            ):
                sighting.last_tick = turn.tick
            else:
                self.enemy_unit_sightings[unit_id] = EnemyCoreSighting(
                    position=enemy_unit.position,
                    first_tick=turn.tick,
                    last_tick=turn.tick,
                )
            core_distance = (
                _distance(turn.core.position, enemy_unit.position)
                if turn.core is not None
                else SIGNED_INT64_MAX
            )
            previous_motion = self.enemy_unit_motion.get(unit_id)
            pursuit_score = 0
            pursuit_ticks = 0
            activity_until_tick = 0
            preemptive_evade_until_tick = 0
            ticks_to_attack_range = None
            if (
                previous_motion is not None
                and turn.tick - previous_motion.last_tick
                <= PURSUIT_MEMORY_TTL
            ):
                observation_gap = turn.tick - previous_motion.last_tick
                missed_ticks = max(
                    0,
                    observation_gap - 1,
                )
                pursuit_score = max(
                    0,
                    previous_motion.pursuit_score - missed_ticks,
                )
                activity_until_tick = previous_motion.activity_until_tick
                preemptive_evade_until_tick = (
                    previous_motion.preemptive_evade_until_tick
                )
                if previous_motion.position == enemy_unit.position:
                    pursuit_score = 0
                else:
                    activity_until_tick = turn.tick + ACTIVE_ENEMY_ALERT_TICKS
                    closed_distance = previous_motion.core_distance - core_distance
                    if closed_distance > 0:
                        pursuit_score = min(
                            PURSUIT_SCORE_MAX,
                            pursuit_score + 2,
                        )
                        remaining_distance = max(
                            0,
                            core_distance - self._attack_range(enemy_unit.unit_type),
                        )
                        ticks_to_attack_range = math.ceil(
                            remaining_distance * observation_gap / closed_distance
                        )
                        if (
                            ticks_to_attack_range
                            <= CORE_PREEMPTIVE_EVADE_HORIZON_TICKS
                        ):
                            preemptive_evade_until_tick = (
                                turn.tick + ACTIVE_ENEMY_ALERT_TICKS
                            )
                    elif core_distance == previous_motion.core_distance:
                        pursuit_score = min(
                            PURSUIT_SCORE_MAX,
                            pursuit_score + 1,
                        )
                    else:
                        pursuit_score = max(0, pursuit_score - 1)
                if pursuit_score > 0:
                    pursuit_ticks = previous_motion.pursuit_ticks + 1
            motion = EnemyUnitMotion(
                position=enemy_unit.position,
                last_tick=turn.tick,
                core_distance=core_distance,
                unit_type=enemy_unit.unit_type,
                pursuit_score=pursuit_score,
                pursuit_ticks=pursuit_ticks,
                activity_until_tick=activity_until_tick,
                preemptive_evade_until_tick=preemptive_evade_until_tick,
                ticks_to_attack_range=ticks_to_attack_range,
            )
            self.enemy_unit_motion[unit_id] = motion
            if (
                enemy_unit.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                and turn.tick <= motion.activity_until_tick
            ):
                self.active_enemy_ids.add(unit_id)
            if (
                enemy_unit.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                and turn.tick <= motion.preemptive_evade_until_tick
            ):
                self.preemptive_evade_enemy_ids.add(unit_id)
            if (
                enemy_unit.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                and self._pursuit_is_confirmed(motion)
            ):
                self.pursuing_enemy_ids.add(unit_id)

        core_respawned = any(
            event.event_type == "CORE_RESPAWNED" for event in turn.events
        )
        attack_events = tuple(
            event
            for event in turn.events
            if not core_respawned
            and getattr(event, "actor_id", None) not in self.allied_object_ids
            and event.reason_code == "ATTACK"
            and event.event_type in {"CORE_DAMAGED", "UNIT_DAMAGED"}
        )
        if attack_events:
            attack_expires_tick = turn.tick + RECENT_ATTACK_MEMORY_TICKS - 1
            self.recent_attack_until_tick = max(
                self.recent_attack_until_tick,
                attack_expires_tick,
            )
            if any(event.event_type == "CORE_DAMAGED" for event in attack_events):
                self.recent_core_attack_until_tick = max(
                    self.recent_core_attack_until_tick,
                    attack_expires_tick,
                )
            self.threat_caution_until_tick = max(
                self.threat_caution_until_tick,
                self.recent_attack_until_tick,
            )
            for event in attack_events:
                for unit_id, position, unit_type in self._attack_event_threats(
                    event,
                    visible_units,
                    prior_motion,
                ):
                    self.recent_attack_threats[unit_id] = RememberedThreat(
                        id=unit_id,
                        position=position,
                        unit_type=unit_type,
                        expires_tick=attack_expires_tick,
                    )
        else:
            for unit_id, remembered in tuple(self.recent_attack_threats.items()):
                visible = visible_units.get(unit_id)
                if visible is not None:
                    self.recent_attack_threats[unit_id] = RememberedThreat(
                        id=unit_id,
                        position=visible.position,
                        unit_type=visible.unit_type,
                        expires_tick=remembered.expires_tick,
                    )
        for unit_id, remembered in tuple(self.recent_attack_threats.items()):
            if remembered.expires_tick < turn.tick:
                self.recent_attack_threats.pop(unit_id, None)

        for core_id, sighting in tuple(self.stationary_core_memory.items()):
            if turn.tick - sighting.last_tick > STATIONARY_CORE_MEMORY_TTL:
                self.stationary_core_memory.pop(core_id, None)
                self.core_observer_candidates.pop(core_id, None)
                if self.isolated_core_target_id == core_id:
                    self._release_core_raid()

        for core_id, sighting in tuple(self.enemy_core_sightings.items()):
            if (
                core_id not in visible_cores
                and turn.tick - sighting.last_tick > CORE_VISIBILITY_GAP_TICKS
            ):
                self.enemy_core_sightings.pop(core_id, None)

        for core_id, enemy_core in visible_cores.items():
            sighting = self.enemy_core_sightings.get(core_id)
            continuously_visible = (
                sighting is not None
                and sighting.position == enemy_core.position
                and enemy_core.state is CoreState.NORMAL
                and turn.tick - sighting.last_tick - 1 <= CORE_VISIBILITY_GAP_TICKS
            )
            if continuously_visible:
                sighting.last_tick = turn.tick
                sighting.observations += 1
            else:
                self.enemy_core_sightings[core_id] = EnemyCoreSighting(
                    position=enemy_core.position,
                    first_tick=turn.tick,
                    last_tick=turn.tick,
                    observations=1,
                )
                if (
                    enemy_core.state is not CoreState.NORMAL
                    or (
                        sighting is not None
                        and sighting.position != enemy_core.position
                    )
                    or (
                        core_id in self.stationary_core_memory
                        and self.stationary_core_memory[core_id].position
                        != enemy_core.position
                    )
                ):
                    self.stationary_core_memory.pop(core_id, None)
                    self.core_observer_candidates.pop(core_id, None)
                    if self.isolated_core_target_id == core_id:
                        self._release_core_raid()

            if (
                enemy_core.state is CoreState.NORMAL
                and core_id not in self.core_observer_candidates
            ):
                observer_id = self._infer_core_observer(turn, enemy_core)
                if observer_id is not None:
                    self.core_observer_candidates[core_id] = observer_id
                    if self.core_observer_target_id is None:
                        self.core_observer_target_id = core_id
                        self.core_raid_spotter_id = observer_id

            current_sighting = self.enemy_core_sightings[core_id]
            if (
                enemy_core.state is CoreState.NORMAL
                and current_sighting.observations >= ISOLATED_CORE_CONFIRM_TICKS + 1
            ):
                self.stationary_core_memory[core_id] = EnemyCoreSighting(
                    position=enemy_core.position,
                    first_tick=current_sighting.first_tick,
                    last_tick=turn.tick,
                    observations=current_sighting.observations,
                )

    def _select_isolated_core_target(self, turn: Turn) -> CoreRaidTarget | None:
        core = turn.core
        mature_fleet = (
            len(turn.vanguards) >= MATURE_GUARD_FLEET_MIN
            and len(turn.rangers) >= MATURE_GUARD_FLEET_MIN
        )
        if core is None or self.recovery_mode:
            self._release_core_raid()
            return None
        minimum_vanguards, minimum_rangers = (
            (ASSAULT_MIN_VANGUARDS, ASSAULT_MIN_RANGERS)
            if mature_fleet
            else (
                EARLY_ASSAULT_MIN_VANGUARDS,
                EARLY_ASSAULT_MIN_RANGERS,
            )
        )
        if (
            len(turn.vanguards) < minimum_vanguards
            or len(turn.rangers) < minimum_rangers
        ):
            self._release_core_raid()
            return None

        guard_vanguards, guard_rangers = _core_guard_ids(turn)
        reserve_vanguards, reserve_rangers = _core_reserve_ids(turn)

        def strike_groups(position: Position) -> tuple[tuple[object, ...], tuple[object, ...]]:
            vanguards = sorted(
                (
                    unit
                    for unit in turn.vanguards
                    if unit.id not in guard_vanguards | reserve_vanguards
                ),
                key=lambda unit: (_distance(unit.position, position), _uuid_sort_key(unit)),
            )
            rangers = sorted(
                (
                    unit
                    for unit in turn.rangers
                    if unit.id not in guard_rangers | reserve_rangers
                ),
                key=lambda unit: (_distance(unit.position, position), _uuid_sort_key(unit)),
            )
            if not mature_fleet:
                vanguards = sorted(
                    (unit for unit in turn.vanguards if unit.id not in guard_vanguards),
                    key=lambda unit: (_distance(unit.position, position), _uuid_sort_key(unit)),
                )
                rangers = sorted(
                    (unit for unit in turn.rangers if unit.id not in guard_rangers),
                    key=lambda unit: (_distance(unit.position, position), _uuid_sort_key(unit)),
                )
                return tuple(vanguards), tuple(rangers)
            return (
                tuple(vanguards[:CORE_RAID_VANGUARDS]),
                tuple(rangers[:CORE_RAID_RANGERS]),
            )
        visible_cores = {
            enemy.id: enemy
            for enemy in self._hostile_enemies(turn)
            if getattr(enemy, "kind") == "CORE"
        }

        if self.isolated_core_target_id is not None:
            target_id = self.isolated_core_target_id
            remembered = self.stationary_core_memory.get(target_id)
            visible_target = visible_cores.get(target_id)
            if visible_target is not None:
                sighting = self.enemy_core_sightings.get(target_id)
                remembered = EnemyCoreSighting(
                    position=visible_target.position,
                    first_tick=(
                        sighting.first_tick
                        if sighting is not None
                        else turn.tick
                    ),
                    last_tick=turn.tick,
                    observations=(
                        sighting.observations
                        if sighting is not None
                        else 1
                    ),
                )
                self.stationary_core_memory[target_id] = remembered
            vanguard_strike_group, ranger_strike_group = strike_groups(
                remembered.position if remembered is not None else core.position
            )
            release_target = (
                remembered is None
                or turn.tick - remembered.last_tick > CORE_RAID_MEMORY_TTL
                or _core_raid_strike_distance(
                    remembered.position,
                    vanguard_strike_group,
                    ranger_strike_group,
                )
                > CORE_RAID_STRIKE_RELEASE_DISTANCE
            )
            forget_position = False
            if visible_target is None and remembered is not None:
                current_visible_cells = visible_cells(
                    turn.state.model_dump(mode="json", exclude_none=True)
                )
                forget_position = remembered.position in current_visible_cells
                release_target = release_target or forget_position or bool(
                    visible_cores
                )
            if release_target:
                self._release_core_raid(forget_position=forget_position)
            else:
                assert remembered is not None
                return CoreRaidTarget(
                    id=target_id,
                    position=remembered.position,
                    visible_enemy=visible_target,
                )

        candidates = []
        for enemy_core in visible_cores.values():
            vanguard_strike_group, ranger_strike_group = strike_groups(
                enemy_core.position
            )
            strike_distance = _core_raid_strike_distance(
                enemy_core.position,
                vanguard_strike_group,
                ranger_strike_group,
            )
            if strike_distance > CORE_RAID_STRIKE_MAX_DISTANCE:
                continue
            candidates.append(
                (
                    str(getattr(enemy_core, "owner_username", "")).casefold()
                    not in self.revenge_usernames,
                    _core_target_score(
                        turn,
                        enemy_core,
                        strike_distance,
                        self._hostile_enemies(turn),
                    ),
                    enemy_core,
                )
            )

        if not candidates:
            return None
        target = min(candidates, key=lambda candidate: candidate[:2])[2]
        sighting = self.enemy_core_sightings.get(target.id)
        self.stationary_core_memory[target.id] = EnemyCoreSighting(
            position=target.position,
            first_tick=sighting.first_tick if sighting is not None else turn.tick,
            last_tick=turn.tick,
            observations=sighting.observations if sighting is not None else 1,
        )
        self.isolated_core_target_id = target.id
        vanguard_strike_group, ranger_strike_group = strike_groups(target.position)
        self.core_raid_vanguard_ids = {
            unit.id for unit in vanguard_strike_group
        }
        self.core_raid_ranger_ids = {
            unit.id for unit in ranger_strike_group
        }
        self.core_raid_started_tick = turn.tick
        self.core_raid_rally_position = _strike_rally_position(
            (*vanguard_strike_group, *ranger_strike_group),
            target.position,
        )
        self.core_raid_launched = False
        observer_id = self.core_observer_candidates.get(target.id)
        living_empty_workers = {
            worker.id
            for worker in turn.workers
            if worker.cargo == 0
        }
        self.core_observer_target_id = target.id
        self.core_raid_spotter_id = (
            observer_id if observer_id in living_empty_workers else None
        )
        return CoreRaidTarget(
            id=target.id,
            position=target.position,
            visible_enemy=target,
        )

    def _stationary_enemy_units(self, turn: Turn) -> tuple[object, ...]:
        return tuple(
            enemy
            for enemy in self._hostile_enemies(turn)
            if getattr(enemy, "kind") != "CORE"
        )

    def _select_stationary_unit_target(
        self,
        turn: Turn,
        stationary_units: Sequence[object],
    ) -> object | None:
        self.stationary_unit_target_id = None
        core = turn.core
        if (
            core is None
            or self.recovery_mode
            or (
                len(turn.vanguards) <= VANGUARD_CORE_GUARDS
                and len(turn.rangers) <= RANGER_CORE_GUARDS
            )
        ):
            return None
        candidates = list(stationary_units)
        if not candidates:
            return None
        target = min(
            candidates,
            key=lambda unit: (
                0
                if unit.unit_type is UnitType.RANGER
                else 1
                if unit.unit_type is UnitType.WORKER
                else 2,
                _distance(core.position, unit.position),
                min(
                    _distance(defender.position, unit.position)
                    for defender in (*turn.vanguards, *turn.rangers)
                ),
                _uuid_sort_key(unit),
            ),
        )
        self.stationary_unit_target_id = target.id
        return target

    def _enter_recovery(self, tick: int, reason: str) -> None:
        self.recovery_until_tick = max(
            self.recovery_until_tick,
            tick + RECOVERY_TICKS,
        )
        self.recovery_reason = reason
        # Coordinates remembered before destruction are not useful at the new spawn.
        self.resource_last_seen.clear()
        self.resource_intents.clear()
        self.resource_progress.clear()
        self.resource_cooldowns.clear()
        self.scout_target_last_visited.clear()
        self.scout_claims.clear()
        self.scout_chunk_last_seen.clear()
        self.enemy_unit_sightings.clear()
        self.enemy_unit_motion.clear()
        self.active_enemy_ids.clear()
        self.preemptive_evade_enemy_ids.clear()
        self.pursuing_enemy_ids.clear()
        self.recent_attack_until_tick = 0
        self.recent_core_attack_until_tick = 0
        self.recent_attack_threats.clear()
        self.squad_return_ids.clear()
        self.scout_return_ids.clear()
        self.scout_cooldown_until.clear()
        self.squad_disengage_until_tick = 0

    def _update_recovery_mode(self, turn: Turn) -> None:
        if turn.core is None:
            return
        respawned = any(
            event.event_type == "CORE_RESPAWNED"
            for event in turn.events
        )
        recovery_worker_goal = min(RECOVERY_MIN_WORKERS, self.worker_target)
        distant_low_stock = (
            len(turn.workers) < recovery_worker_goal
            and turn.resources < RECOVERY_INFERENCE_RESOURCE_LIMIT
            and _distance(turn.core.position, turn.beacon.position) >= 80
        )
        if respawned or (distant_low_stock and not self.recovery_mode):
            self._enter_recovery(
                turn.tick,
                "CORE_RESPAWNED" if respawned else "REMOTE_LOW_FLEET",
            )
            return
        if not self.recovery_mode:
            return
        nearest_threat = min(
            (
                _distance(turn.core.position, enemy.position)
                for enemy in self._hostile_enemies(turn)
            ),
            default=None,
        )
        if (
            turn.tick >= self.recovery_until_tick
            and len(turn.workers) >= recovery_worker_goal
            and turn.resources
            >= min(RECOVERY_MIN_RESOURCES, turn.resource_capacity)
            and (
                nearest_threat is None
                or nearest_threat > RECOVERY_THREAT_DISTANCE
            )
        ):
            self.recovery_until_tick = 0
            self.recovery_reason = "NONE"

    def _update_core_movement_history(self, turn: Turn) -> None:
        for event in turn.events:
            if event.event_type == "CORE_MOVE_SUCCEEDED":
                self.last_core_move_tick = turn.tick
                self.active_core_move_reason = None
            elif event.event_type in {
                "CORE_MOVE_FAILED",
                "CORE_MOVE_CANCELLED",
            }:
                self.last_core_move_tick = turn.tick
                self.last_retreat_direction = None
                self.active_core_move_reason = None

    def _refresh_resource_memory(self, turn: Turn) -> None:
        current_resources = set(turn.resource_cells)
        for cell in current_resources:
            self.resource_last_seen[cell] = turn.tick

        friendly_positions = [unit.position for unit in turn.units]
        if turn.core is not None:
            friendly_positions.append(turn.core.position)
        for cell, last_seen in tuple(self.resource_last_seen.items()):
            expired = turn.tick - last_seen > RESOURCE_MEMORY_TTL
            definitely_visible = any(
                _distance(position, cell) <= 1 for position in friendly_positions
            )
            if expired or (definitely_visible and cell not in current_resources):
                self.resource_last_seen.pop(cell, None)

        living_worker_ids = {worker.id for worker in turn.workers}
        for worker_id, target in tuple(self.resource_intents.items()):
            if (
                worker_id not in living_worker_ids
                or target not in self.resource_last_seen
            ):
                self.resource_intents.pop(worker_id, None)

    def _refresh_resource_progress(
        self,
        workers: Sequence[object],
        *,
        tick: int,
        blocked: set[Position],
    ) -> None:
        self.last_released_targets.clear()
        worker_by_id = {worker.id: worker for worker in workers}

        for key, retry_at_tick in tuple(self.resource_cooldowns.items()):
            worker_id, target = key
            if (
                retry_at_tick <= tick
                or worker_id not in worker_by_id
                or target not in self.resource_last_seen
            ):
                self.resource_cooldowns.pop(key, None)

        for worker_id in tuple(self.resource_progress):
            target = self.resource_intents.get(worker_id)
            if worker_id not in worker_by_id or target not in self.resource_last_seen:
                self.resource_progress.pop(worker_id, None)

        for worker_id, worker in worker_by_id.items():
            target = self.resource_intents.get(worker_id)
            if target is None or target not in self.resource_last_seen:
                continue
            cost = _estimated_path_cost(worker.position, target, blocked)
            progress = self.resource_progress.get(worker_id)
            if progress is None or progress.target != target:
                self.resource_progress[worker_id] = ResourceProgress(target, cost)
                continue
            if cost < progress.best_cost:
                progress.best_cost = cost
                progress.stalled_turns = 0
                continue

            progress.stalled_turns += 1
            if progress.stalled_turns < RESOURCE_STALL_TICKS:
                continue
            self.resource_cooldowns[(worker_id, target)] = (
                tick + RESOURCE_COOLDOWN_TICKS
            )
            self.resource_intents.pop(worker_id, None)
            self.resource_progress.pop(worker_id, None)
            self.last_released_targets[worker_id] = target

    def _assign_resource_targets(
        self,
        workers: Sequence[object],
        *,
        tick: int,
        blocked: set[Position],
    ) -> dict[UUID, Position]:
        available_resources = {
            cell for cell in self.resource_last_seen if cell not in blocked
        }
        if not workers or not available_resources:
            self.resource_intents = {}
            return {}

        ordered_workers = sorted(workers, key=_uuid_sort_key)
        ordered_resources = sorted(available_resources)
        unassigned_cost = PATH_COST_UNREACHABLE * (len(ordered_workers) + 1)
        forbidden_cost = unassigned_cost * 2
        cost_matrix: list[list[int]] = []
        for worker in ordered_workers:
            row = []
            for cell in ordered_resources:
                if self.resource_cooldowns.get((worker.id, cell), 0) > tick:
                    row.append(forbidden_cost)
                    continue
                path_cost = _estimated_path_cost(worker.position, cell, blocked)
                if path_cost >= PATH_COST_UNREACHABLE:
                    row.append(forbidden_cost)
                    continue
                age = tick - self.resource_last_seen[cell]
                stale_penalty = 0 if age == 0 else min(6, 2 + age // 8)
                sticky_bonus = (
                    RESOURCE_ASSIGNMENT_STICKY_BONUS
                    if self.resource_intents.get(worker.id) == cell
                    else 0
                )
                row.append(
                    max(0, path_cost + stale_penalty - sticky_bonus)
                )
            row.extend([unassigned_cost] * len(ordered_workers))
            cost_matrix.append(row)

        assignments: dict[UUID, Position] = {}
        for row_index, (worker, column_index) in enumerate(
            zip(
                ordered_workers,
                _minimum_cost_assignment(cost_matrix),
                strict=True,
            )
        ):
            if column_index >= len(ordered_resources):
                continue
            if cost_matrix[row_index][column_index] >= forbidden_cost:
                continue
            assignments[worker.id] = ordered_resources[column_index]

        self.resource_intents = assignments
        return assignments

    def _set_worker_mode(
        self,
        worker: object,
        mode: str,
        target: Position | None = None,
    ) -> None:
        self.worker_modes[worker.id] = mode
        if target is None:
            self.worker_targets.pop(worker.id, None)
        else:
            self.worker_targets[worker.id] = target

    def _refresh_scout_assignments(self, workers: Sequence[object]) -> None:
        living_ids = {getattr(worker, "id") for worker in workers}
        for worker_id in set(self.scout_slots) - living_ids:
            self.scout_slots.pop(worker_id, None)
            self.scout_stages.pop(worker_id, None)
            self.scout_progress.pop(worker_id, None)
            self.worker_history.pop(worker_id, None)

        used_slots = set(self.scout_slots.values())
        for worker in workers:
            worker_id = getattr(worker, "id")
            if worker_id in self.scout_slots:
                continue
            slot = 0
            while slot in used_slots:
                slot += 1
            self.scout_slots[worker_id] = slot
            self.scout_stages[worker_id] = 0
            used_slots.add(slot)

    def _scout_target(
        self,
        worker_id: UUID,
        core_position: Position,
        beacon_position: Position | None,
        *,
        claim: bool = False,
    ) -> Position:
        slot = self.scout_slots[worker_id]
        stage = self.scout_stages[worker_id]
        heading = (0, 0)
        if beacon_position is not None and self.beacon_policy == "pursue":
            heading = (
                (beacon_position[0] > core_position[0])
                - (beacon_position[0] < core_position[0]),
                (beacon_position[1] > core_position[1])
                - (beacon_position[1] < core_position[1]),
            )
        vectors = SCOUT_VECTORS
        if heading != (0, 0):
            vectors = tuple(
                vector
                for _, vector in sorted(
                    enumerate(SCOUT_VECTORS),
                    key=lambda item: (
                        -(item[1][0] * heading[0] + item[1][1] * heading[1]),
                        abs(item[1][0] * heading[1] - item[1][1] * heading[0]),
                        item[0],
                    ),
                )
            )
        vector = vectors[(slot + stage) % len(vectors)]
        base_ring = 1 + slot // len(SCOUT_VECTORS)
        candidates = []
        for ring_offset in range(SCOUT_RING_COUNT):
            radius = SCOUT_RING_STEP * (base_ring + ring_offset)
            vector_scale = radius // (abs(vector[0]) + abs(vector[1]))
            candidate = (
                core_position[0] + vector[0] * vector_scale,
                core_position[1] + vector[1] * vector_scale,
            )
            if candidate in self.scout_claims:
                continue
            if (
                beacon_position is not None
                and self.beacon_policy != "pursue"
                and _distance(candidate, beacon_position)
                < min(
                    RETREAT_MIN_BEACON_DISTANCE,
                    _distance(core_position, beacon_position),
                )
            ):
                continue
            candidates.append(candidate)
        if not candidates:
            candidates.append(core_position)
        target = min(
            candidates,
            key=lambda candidate: (
                self.scout_chunk_last_seen.get(
                    _chunk_coordinates(candidate),
                    -1,
                ),
                self.scout_target_last_visited.get(candidate, -1),
                -_chunk_resource_quota(candidate),
                _distance(core_position, candidate),
                candidate[0],
                candidate[1],
            ),
        )
        if claim:
            self.scout_claims.add(target)
        return target

    def _advance_scout(
        self,
        worker_id: UUID,
        *,
        visited_target: Position | None = None,
        tick: int | None = None,
    ) -> None:
        if visited_target is not None and tick is not None:
            self.scout_target_last_visited[visited_target] = tick
        self.scout_stages[worker_id] = (
            self.scout_stages[worker_id] + 1
        ) % SCOUT_STAGE_CYCLE

    def _scout_route_stalled(
        self,
        worker: object,
        target: Position,
        context: MovementContext,
    ) -> bool:
        blocked = (
            set(context.obstacles)
            | set(context.enemy_cells)
            | set(context.danger_cells)
        )
        cost = _estimated_path_cost(worker.position, target, blocked)
        progress = self.scout_progress.get(worker.id)
        if progress is None or progress.target != target:
            self.scout_progress[worker.id] = ScoutProgress(target, cost)
            return False
        if cost < progress.best_cost:
            progress.best_cost = cost
            progress.stalled_turns = 0
            return False
        progress.stalled_turns += 1
        return progress.stalled_turns >= SCOUT_STALL_TICKS

    def _control_empty_worker(
        self,
        worker: object,
        *,
        tick: int,
        core_position: Position,
        beacon_position: Position | None,
        current_resources: set[Position],
        assigned_target: Position | None,
        context: MovementContext,
    ) -> None:
        if (
            assigned_target == worker.position
            and worker.position in current_resources
        ):
            self.scout_progress.pop(worker.id, None)
            worker.harvest()
            self._set_worker_mode(worker, "HARVEST", worker.position)
            return

        if assigned_target is not None:
            self.scout_progress.pop(worker.id, None)
            if _queue_toward(
                worker,
                assigned_target,
                context,
                allow_target_entry=True,
                discouraged=set(self.worker_history[worker.id]),
            ):
                self._set_worker_mode(worker, "RESOURCE", assigned_target)
                return
            worker.wait()
            self._set_worker_mode(worker, "RESOURCE_BLOCKED", assigned_target)
            return

        target = self._scout_target(
            worker.id,
            core_position,
            beacon_position,
            claim=True,
        )
        if worker.position == target:
            self.scout_progress.pop(worker.id, None)
            self._advance_scout(
                worker.id,
                visited_target=target,
                tick=tick,
            )
            target = self._scout_target(
                worker.id,
                core_position,
                beacon_position,
                claim=True,
            )
        elif self._scout_route_stalled(worker, target, context):
            self.scout_progress.pop(worker.id, None)
            self._advance_scout(worker.id)
            target = self._scout_target(
                worker.id,
                core_position,
                beacon_position,
                claim=True,
            )
        if not _queue_toward(
            worker,
            target,
            context,
            discouraged=set(self.worker_history[worker.id]),
        ):
            self._advance_scout(worker.id)
            target = self._scout_target(
                worker.id,
                core_position,
                beacon_position,
                claim=True,
            )
            moved = _queue_toward(
                worker,
                target,
                context,
                discouraged=set(self.worker_history[worker.id]),
            )
            if not moved:
                worker.wait()
                self._set_worker_mode(worker, "SCOUT_BLOCKED", target)
                return
        self._set_worker_mode(worker, "SCOUT", target)

    def _core_observer_position(
        self,
        turn: Turn,
        raid_target: CoreRaidTarget | None,
    ) -> Position | None:
        if self.core_raid_spotter_id is None:
            return None
        worker = next(
            (
                candidate
                for candidate in turn.workers
                if candidate.id == self.core_raid_spotter_id
            ),
            None,
        )
        if worker is None or worker.cargo > 0:
            self._release_core_observer()
            return None
        if raid_target is not None:
            return raid_target.position
        target_id = self.core_observer_target_id
        sighting = (
            self.enemy_core_sightings.get(target_id)
            if target_id is not None
            else None
        )
        if (
            sighting is None
            or sighting.observations >= ISOLATED_CORE_CONFIRM_TICKS + 1
        ):
            self._release_core_observer()
            return None
        return sighting.position

    def _control_core_observer(
        self,
        worker: object,
        target: Position,
        context: MovementContext,
    ) -> None:
        current_distance = _distance(worker.position, target)
        if (
            CORE_OBSERVER_MIN_DISTANCE
            <= current_distance
            <= CORE_OBSERVER_MAX_DISTANCE
            and worker.position not in context.danger_cells
        ):
            worker.wait()
            self._set_worker_mode(worker, "CORE_OBSERVER", target)
            return

        candidates = []
        for dx in range(-CORE_OBSERVER_MAX_DISTANCE, CORE_OBSERVER_MAX_DISTANCE + 1):
            for dy in range(
                -CORE_OBSERVER_MAX_DISTANCE,
                CORE_OBSERVER_MAX_DISTANCE + 1,
            ):
                watch_position = (target[0] + dx, target[1] + dy)
                watch_distance = abs(dx) + abs(dy)
                if (
                    not CORE_OBSERVER_MIN_DISTANCE
                    <= watch_distance
                    <= CORE_OBSERVER_MAX_DISTANCE
                    or not _is_signed_int64_position(watch_position)
                    or watch_position in context.obstacles
                    or watch_position in context.enemy_cells
                    or watch_position in context.danger_cells
                    or watch_position == context.core_position
                ):
                    continue
                candidates.append(
                    (
                        _distance(worker.position, watch_position),
                        CORE_OBSERVER_MAX_DISTANCE - watch_distance,
                        watch_position[0],
                        watch_position[1],
                        watch_position,
                    )
                )
        if candidates:
            watch_position = min(candidates)[4]
            if _queue_toward(worker, watch_position, context):
                self._set_worker_mode(
                    worker,
                    "CORE_OBSERVER_REPOSITION",
                    target,
                )
                return
        worker.wait()
        self._set_worker_mode(worker, "CORE_OBSERVER_BLOCKED", target)

    def choose_actions(self, turn: Turn) -> None:
        turn.clear()
        self._refresh_alliance(turn)
        if not self.alliance_ready:
            if turn.core is not None:
                turn.core.wait()
            for unit in turn.units:
                unit.wait()
            self.threat_assessment = ThreatAssessment()
            self.combat_pressure_active = False
            return
        if turn.core is None:
            self._refresh_threat_assessment(turn)
            return

        core = turn.core
        if self.startup_tick is None:
            self.startup_tick = turn.tick
        self._refresh_return_states(turn)
        self._update_recovery_mode(turn)
        self._update_core_movement_history(turn)
        self._update_enemy_awareness(turn)
        self._refresh_compatibility_hold()
        self.known_obstacles.update(turn.obstacle_cells)
        self._refresh_threat_assessment(turn)
        self.stationary_unit_target_id = None
        if self.compatibility_hold:
            self._release_core_raid()
            self._release_core_observer()
            isolated_core_target = None
        else:
            isolated_core_target = self._select_isolated_core_target(turn)
        stationary_unit_target = None
        if (
            not self.compatibility_hold
            and isolated_core_target is None
        ):
            stationary_candidates = self._stationary_enemy_units(turn)
            stationary_unit_target = self._select_stationary_unit_target(
                turn,
                stationary_candidates,
            )
        combat_target = isolated_core_target or stationary_unit_target
        observer_position = self._core_observer_position(
            turn,
            isolated_core_target,
        )
        enemies = self._hostile_enemies(turn)
        mobile_enemies = tuple(
            enemy
            for enemy in enemies
            if getattr(enemy, "kind") != "CORE"
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
        )
        if self._strike_group_locally_threatened(
            turn,
            combat_target,
            mobile_enemies,
        ):
            self._refresh_threat_assessment(
                turn,
                local_squad_contact=True,
            )
        self._select_beacon_runner(turn, combat_target)
        enemy_cells = {enemy.position for enemy in enemies}
        danger_cells = _enemy_threat_cells(enemies, self.known_obstacles)
        self.last_danger_cells = danger_cells
        discouraged_core_cells = {
            (
                sighting.position[0] + dx,
                sighting.position[1] + dy,
            )
            for sighting in self.stationary_core_memory.values()
            for dx in range(-1, 2)
            for dy in range(-1, 2)
            if abs(dx) + abs(dy) <= 1
        }
        friendly_counts = Counter(unit.position for unit in turn.units)
        friendly_counts[core.position] += 1
        friendly_counts.update(
            enemy.position
            for enemy in turn.visible_enemies
            if enemy.id in self.allied_object_ids
            or (
                getattr(enemy, "kind", None) == "CORE"
                and getattr(enemy, "owner_username", "") in self.allied_usernames
            )
        )
        context = MovementContext(
            obstacles=set(self.known_obstacles),
            resource_cells=set(turn.resource_cells),
            enemy_cells=enemy_cells,
            danger_cells=danger_cells,
            allied_cells=set(self.allied_occupied_cells),
            discouraged_cells=discouraged_core_cells,
            friendly_counts=friendly_counts,
            reserved_destinations=set(),
            core_position=core.position,
            preplanned_units=set(),
        )
        context.delivery_lane = _select_delivery_lane(context)
        breakout = _is_multi_axis_breakout(
            core.position,
            mobile_enemies,
            context.obstacles,
            self._core_blocked_cells(turn, context),
        )
        if breakout:
            self._refresh_threat_assessment(turn, breakout=True)
        nearest_visible_threat = min(
            (_distance(core.position, enemy.position) for enemy in mobile_enemies),
            default=None,
        )
        if self._core_defense_active(nearest_visible_threat):
            threat_count = max(
                1,
                len(self.threat_assessment.near_core_enemy_ids),
                len(self.recent_attack_threats),
            )
            self._recall_core_defenders(turn, threat_count)

        workers = sorted(turn.workers, key=_uuid_sort_key)
        self.worker_modes.clear()
        self.worker_targets.clear()
        self.scout_claims.clear()
        for worker in workers:
            self.scout_chunk_last_seen[_chunk_coordinates(worker.position)] = turn.tick
        for chunk, last_seen in tuple(self.scout_chunk_last_seen.items()):
            if turn.tick - last_seen > SCOUT_COVERAGE_MEMORY_TTL:
                self.scout_chunk_last_seen.pop(chunk, None)
        self._refresh_scout_assignments(workers)
        for worker in workers:
            history = self.worker_history.setdefault(
                worker.id,
                deque(maxlen=6),
            )
            if not history or history[-1] != worker.position:
                history.append(worker.position)
        cargo_workers = [worker for worker in workers if worker.cargo > 0]
        cargo_workers.sort(
            key=lambda worker: (
                _distance(worker.position, core.position),
                _uuid_sort_key(worker),
            )
        )
        empty_workers = [worker for worker in workers if worker.cargo == 0]
        self._refresh_healing_defenders(turn, combat_target)
        healing_holds = {
            defender.id
            for defender in (*turn.vanguards, *turn.rangers)
            if self._healing_return_ready(turn, defender)
        }
        retreat_enemies = mobile_enemies + self._remembered_retreat_threats(
            turn,
            mobile_enemies,
        )
        spawn_reservation = self._spawn_reservation(
            turn,
            combat_target,
            retreat_enemies,
        )
        reserve_core_for_spawn = spawn_reservation is not None
        raid_launched = self._refresh_core_raid_launch(
            turn,
            isolated_core_target,
        )
        observer_id = (
            self.core_raid_spotter_id
            if observer_position is not None
            else None
        )
        economic_empty_workers = [
            worker for worker in empty_workers if worker.id != observer_id
        ]
        preplanned_units = _queue_core_defender_egress(
            turn,
            context,
            mobile_enemies,
            healing_holds,
            force_departure=reserve_core_for_spawn,
        )
        preplanned_units.update(
            _queue_core_delivery_handoff(
                turn,
                context,
                enemies,
                force_departure=reserve_core_for_spawn,
            )
        )
        context.preplanned_units = preplanned_units
        for worker in workers:
            if worker.id not in preplanned_units:
                continue
            if worker.position == core.position:
                mode = "CLEAR_CORE_HANDOFF"
            elif worker.cargo > 0:
                mode = "DELIVERY_CHAIN_CARGO"
            else:
                mode = "DELIVERY_CHAIN_CLEAR"
            self._set_worker_mode(worker, mode, core.position)
        self._refresh_resource_memory(turn)
        resource_route_blocked = (
            set(context.obstacles)
            | set(context.enemy_cells)
            | set(context.danger_cells)
        )
        self._refresh_resource_progress(
            economic_empty_workers,
            tick=turn.tick,
            blocked=resource_route_blocked,
        )
        resource_assignments = self._assign_resource_targets(
            economic_empty_workers,
            tick=turn.tick,
            blocked=resource_route_blocked,
        )
        current_resources = set(turn.resource_cells)
        allow_core_delivery = turn.resource_space > 0 and not reserve_core_for_spawn
        departing_core_workers = [
            worker for worker in empty_workers if worker.position == core.position
        ]
        departing_ids = {worker.id for worker in departing_core_workers}

        navigation_beacon = turn.beacon.position
        for worker in departing_core_workers:
            if worker.id in preplanned_units:
                continue
            if self._control_returning_scout(
                turn,
                worker,
                mobile_enemies,
                context,
            ):
                continue
            if _queue_away_from_enemies(
                worker,
                mobile_enemies,
                context,
                turn.beacon.position,
            ):
                if _distance(worker.position, core.position) > SCOUT_SAFE_RETURN_RADIUS:
                    self.scout_return_ids.add(worker.id)
                self._set_worker_mode(worker, "EVADE", core.position)
                continue
            if (
                self.recovery_mode
                and len(workers) < min(RECOVERY_MIN_WORKERS, self.worker_target)
            ):
                recovery_egress = tuple(
                    sorted(
                        _exploration_directions(worker),
                        key=lambda direction: _distance(
                            _destination(worker.position, direction),
                            navigation_beacon,
                        ),
                        reverse=True,
                    )
                )
                if _queue_move(worker, recovery_egress, context):
                    self._set_worker_mode(worker, "RECOVERY_EGRESS", core.position)
                    continue
            if worker.id == observer_id and observer_position is not None:
                self._control_core_observer(
                    worker,
                    observer_position,
                    context,
                )
                continue
            self._control_empty_worker(
                worker,
                tick=turn.tick,
                core_position=core.position,
                beacon_position=navigation_beacon,
                current_resources=current_resources,
                assigned_target=resource_assignments.get(worker.id),
                context=context,
            )

        for worker in cargo_workers:
            if worker.id in preplanned_units:
                continue
            if _queue_away_from_enemies(
                worker,
                mobile_enemies,
                context,
                turn.beacon.position,
            ):
                self._set_worker_mode(worker, "EVADE_CARGO", core.position)
                continue
            if worker.position == core.position:
                if core.view.state is CoreState.NORMAL and turn.resource_space > 0:
                    worker.deposit()
                    self._set_worker_mode(worker, "DEPOSIT", core.position)
                elif core.view.state is not CoreState.NORMAL:
                    worker.wait()
                    self._set_worker_mode(worker, "WAIT_CORE", core.position)
                else:
                    moved = _queue_move(
                        worker,
                        _exploration_directions(worker),
                        context,
                    )
                    if not moved:
                        worker.wait()
                    self._set_worker_mode(
                        worker,
                        "CLEAR_CORE" if moved else "CLEAR_CORE_BLOCKED",
                    )
                continue
            moved = _queue_toward(
                worker,
                core.position,
                context,
                allow_core_entry=allow_core_delivery,
                allow_single_friendly_transit=allow_core_delivery,
            )
            if not moved:
                worker.wait()
            self._set_worker_mode(
                worker,
                "RETURN" if moved else "RETURN_BLOCKED",
                core.position,
            )

        for worker in empty_workers:
            if worker.id in departing_ids or worker.id in preplanned_units:
                continue
            if self._control_returning_scout(
                turn,
                worker,
                mobile_enemies,
                context,
            ):
                continue
            if _queue_away_from_enemies(
                worker,
                mobile_enemies,
                context,
                turn.beacon.position,
            ):
                if _distance(worker.position, core.position) > SCOUT_SAFE_RETURN_RADIUS:
                    self.scout_return_ids.add(worker.id)
                self._set_worker_mode(worker, "EVADE", core.position)
                continue
            if worker.id == observer_id and observer_position is not None:
                self._control_core_observer(
                    worker,
                    observer_position,
                    context,
                )
                continue
            self._control_empty_worker(
                worker,
                tick=turn.tick,
                core_position=core.position,
                beacon_position=navigation_beacon,
                current_resources=current_resources,
                assigned_target=resource_assignments.get(worker.id),
                context=context,
            )

        for worker_id in tuple(self.scout_progress):
            if self.worker_modes.get(worker_id) not in {"SCOUT", "SCOUT_BLOCKED"}:
                self.scout_progress.pop(worker_id, None)

        self._control_vanguards(
            turn,
            enemies,
            context,
            combat_target,
            raid_launched=raid_launched,
            reserve_core_for_spawn=reserve_core_for_spawn,
        )
        self._control_rangers(
            turn,
            enemies,
            context,
            combat_target,
            raid_launched=raid_launched,
            reserve_core_for_spawn=reserve_core_for_spawn,
        )
        self._control_core(turn, context, combat_target)

    def apply_unit_orders(
        self,
        turn: Turn,
        orders: Sequence[Mapping[str, object]],
    ) -> tuple[int, ...]:
        """Apply dashboard orders after the autonomous plan is complete."""
        if turn.core is None or not orders or not self.alliance_ready:
            self.manual_order_ids = ()
            return ()
        enemies = self._hostile_enemies(turn)
        context = MovementContext(
            obstacles=set(self.known_obstacles) | set(turn.obstacle_cells),
            resource_cells=set(turn.resource_cells),
            enemy_cells={enemy.position for enemy in enemies},
            danger_cells=_enemy_threat_cells(
                enemies,
                self.known_obstacles | set(turn.obstacle_cells),
            ),
            allied_cells=set(self.allied_occupied_cells),
            discouraged_cells=set(),
            friendly_counts=Counter(unit.position for unit in turn.units)
            + Counter(
                enemy.position
                for enemy in turn.visible_enemies
                if enemy.id in self.allied_object_ids
                or (
                    getattr(enemy, "kind", None) == "CORE"
                    and getattr(enemy, "owner_username", "")
                    in self.allied_usernames
                )
            ),
            reserved_destinations=set(),
            core_position=turn.core.position,
        )
        units_by_type = {
            "WORKER": list(turn.workers),
            "VANGUARD": list(turn.vanguards),
            "RANGER": list(turn.rangers),
        }
        claimed: set[UUID] = set()
        core_claimed = False
        completed: list[int] = []
        active_ids: list[int] = []
        for order in orders:
            order_id = int(order["id"])
            unit_type = str(order["unit_type"])
            target = (int(order["target_x"]), int(order["target_y"]))
            count = int(order["unit_count"])
            requested_ids = tuple(UUID(str(value)) for value in order.get("unit_ids", ()))
            if unit_type == "CORE":
                core = turn.core
                if (
                    core_claimed
                    or count != 1
                    or requested_ids != (core.id,)
                ):
                    active_ids.append(order_id)
                    continue
                core_claimed = True
                arrived = _distance(core.position, target) <= MANUAL_ORDER_ARRIVAL_RADIUS
                if arrived:
                    if core.view.state is CoreState.MOVING:
                        core.cancel_move()
                    else:
                        core.wait()
                    self.active_core_move_reason = None
                    completed.append(order_id)
                    continue
                blocked = self._core_blocked_cells(turn, context) | set(
                    context.danger_cells
                )
                directions = _path_directions(
                    core.position,
                    target,
                    blocked,
                    target_radius=MANUAL_ORDER_ARRIVAL_RADIUS,
                )
                if (
                    core.view.state is CoreState.NORMAL
                    and not directions
                    and self._clear_core_departure_lane(
                        turn,
                        context,
                        target,
                        target_radius=MANUAL_ORDER_ARRIVAL_RADIUS,
                    )
                ):
                    blocked = self._core_blocked_cells(turn, context) | set(
                        context.danger_cells
                    )
                    directions = _path_directions(
                        core.position,
                        target,
                        blocked,
                        target_radius=MANUAL_ORDER_ARRIVAL_RADIUS,
                    )
                if core.view.state is CoreState.MOVING:
                    expected_destination = (
                        _destination(core.position, directions[0])
                        if directions
                        else None
                    )
                    if core.view.destination == expected_destination:
                        core.clear_action()
                    else:
                        core.cancel_move()
                    self.active_core_move_reason = "MANUAL_ORDER"
                elif core.view.state is CoreState.NORMAL and directions:
                    core.start_move(directions[0])
                    self.active_core_move_reason = "MANUAL_ORDER"
                else:
                    core.wait()
                active_ids.append(order_id)
                continue
            candidates = {
                unit.id: unit
                for unit in units_by_type.get(unit_type, [])
                if unit.id not in claimed
                and not (unit_type == "WORKER" and unit.cargo > 0)
            }
            selected = [candidates[unit_id] for unit_id in requested_ids if unit_id in candidates]
            if len(selected) < count:
                active_ids.append(order_id)
                continue
            claimed.update(unit.id for unit in selected)
            arrived = all(
                _distance(unit.position, target) <= MANUAL_ORDER_ARRIVAL_RADIUS
                for unit in selected
            )
            for unit in selected:
                if order.get("preserve_combat") and _legal_attack_targets(
                    unit,
                    enemies,
                    context.obstacles,
                ):
                    continue
                if arrived:
                    unit.wait()
                elif not _queue_toward(
                    unit,
                    target,
                    context,
                    avoid_danger=True,
                    target_radius=MANUAL_ORDER_ARRIVAL_RADIUS,
                ):
                    unit.wait()
                self._set_worker_mode(unit, "MANUAL_ORDER", target)
            if arrived:
                completed.append(order_id)
            else:
                active_ids.append(order_id)
        self.manual_order_ids = tuple(active_ids)
        return tuple(completed)

    def expedition_orders(
        self,
        turn: Turn,
        expeditions: Sequence[Mapping[str, object]],
        *,
        claimed_ids: set[UUID],
    ) -> tuple[dict[str, object], ...]:
        if turn.core is None:
            self.expedition_members.clear()
            return ()
        guard_vanguards, guard_rangers = _core_guard_ids(turn)
        guards = guard_vanguards | guard_rangers
        units_by_id = {unit.id: unit for unit in (*turn.vanguards, *turn.rangers)}
        active_ids = {int(item["id"]) for item in expeditions if item.get("enabled")}
        for expedition_id in set(self.expedition_members) - active_ids:
            self.expedition_members.pop(expedition_id, None)

        orders: list[dict[str, object]] = []
        for expedition in expeditions:
            if not expedition.get("enabled"):
                continue
            expedition_id = int(expedition["id"])
            target = (int(expedition["target_x"]), int(expedition["target_y"]))
            members = {
                unit_id
                for unit_id in self.expedition_members.get(expedition_id, set())
                if unit_id in units_by_id and unit_id not in claimed_ids and unit_id not in guards
            }
            for unit_type, count_key in (
                (UnitType.RANGER, "ranger_count"),
                (UnitType.VANGUARD, "vanguard_count"),
            ):
                desired = int(expedition[count_key])
                typed = sorted(
                    (
                        unit_id
                        for unit_id in members
                        if units_by_id[unit_id].unit_type is unit_type
                    ),
                    key=lambda unit_id: unit_id.bytes,
                )[:desired]
                members = {
                    unit_id
                    for unit_id in members
                    if units_by_id[unit_id].unit_type is not unit_type
                } | set(typed)
                if len(typed) < desired:
                    candidates = sorted(
                        (
                            unit
                            for unit in units_by_id.values()
                            if unit.unit_type is unit_type
                            and unit.id not in members
                            and unit.id not in claimed_ids
                            and unit.id not in guards
                        ),
                        key=lambda unit: (_distance(unit.position, target), _uuid_sort_key(unit)),
                    )
                    typed.extend(unit.id for unit in candidates[: desired - len(typed)])
                members.update(typed)
                claimed_ids.update(typed)
                if typed:
                    orders.append(
                        {
                            "id": -expedition_id * 10 - int(unit_type is UnitType.VANGUARD),
                            "preserve_combat": True,
                            "unit_type": unit_type.value,
                            "unit_count": len(typed),
                            "unit_ids": [str(unit_id) for unit_id in typed],
                            "target_x": target[0],
                            "target_y": target[1],
                        }
                    )
            self.expedition_members[expedition_id] = members
        return tuple(orders)

    def _beacon_campaign_ready(self, turn: Turn, target: object | None) -> bool:
        core = turn.core
        guard_vanguards, _ = _core_guard_ids(turn)
        return bool(
            self.beacon_policy == "pursue"
            and target is None
            and core is not None
            and core.view.state is CoreState.NORMAL
            and core.hp == 5
            and core.shield >= 5
            and len(turn.units) >= BEACON_CAMPAIGN_POPULATION
            and turn.resources >= BEACON_CAMPAIGN_RESOURCES
            and len(turn.vanguards) > len(guard_vanguards)
            and not self.threat_assessment.recent_core_attack
            and not self.threat_assessment.threatening_core_enemy_ids
        )

    def _select_beacon_runner(self, turn: Turn, target: object | None) -> None:
        if not self._beacon_campaign_ready(turn, target):
            self.beacon_runner_id = None
            return
        guard_vanguards, _ = _core_guard_ids(turn)
        candidates = [
            unit
            for unit in sorted(turn.vanguards, key=_uuid_sort_key)
            if unit.id not in guard_vanguards
        ]
        living_ids = {unit.id for unit in candidates}
        if self.beacon_runner_id in living_ids:
            return
        self.beacon_runner_id = min(
            candidates,
            key=lambda unit: (
                _distance(unit.position, turn.beacon.position),
                _uuid_sort_key(unit),
            ),
        ).id

    def _combat_patrol_target(
        self,
        turn: Turn,
        unit: object,
        index: int,
    ) -> Position:
        core = turn.core
        if core is None:
            return unit.position
        elapsed = max(0, turn.tick - (self.startup_tick or turn.tick))
        radius = (
            COMBAT_PATROL_INITIAL_RADIUS
            + elapsed // COMBAT_PATROL_GROWTH_TICKS * COMBAT_PATROL_RING_STEP
        )
        vector = SCOUT_VECTORS[(unit.id.int + index) % len(SCOUT_VECTORS)]
        return (
            core.position[0] + vector[0] * radius,
            core.position[1] + vector[1] * radius,
        )

    @staticmethod
    def _select_strike_group_ids(
        turn: Turn,
        target: object | None,
        enemies: Sequence[object] | None = None,
    ) -> tuple[set[UUID], set[UUID]]:
        if target is None:
            return set(), set()
        guard_vanguards, guard_rangers = _core_guard_ids(turn)
        reserve_vanguards, reserve_rangers = _core_reserve_ids(turn)
        available_vanguards = sorted(
            (
                unit
                for unit in turn.vanguards
                if unit.id not in guard_vanguards
            ),
            key=lambda unit: (
                _distance(unit.position, target.position),
                _uuid_sort_key(unit),
            ),
        )
        available_rangers = sorted(
            (
                unit
                for unit in turn.rangers
                if unit.id not in guard_rangers
            ),
            key=lambda unit: (
                _distance(unit.position, target.position),
                _uuid_sort_key(unit),
            ),
        )
        if isinstance(target, CoreRaidTarget):
            mature_fleet = (
                len(turn.vanguards) >= MATURE_GUARD_FLEET_MIN
                and len(turn.rangers) >= MATURE_GUARD_FLEET_MIN
            )
            if not mature_fleet:
                return (
                    {unit.id for unit in available_vanguards},
                    {unit.id for unit in available_rangers},
                )
            raid_vanguards = tuple(
                unit
                for unit in available_vanguards
                if unit.id not in reserve_vanguards
            )[:CORE_RAID_VANGUARDS]
            raid_rangers = tuple(
                unit
                for unit in available_rangers
                if unit.id not in reserve_rangers
            )[:CORE_RAID_RANGERS]
            return (
                {unit.id for unit in raid_vanguards},
                {unit.id for unit in raid_rangers},
            )

        local_hostiles = sum(
            getattr(enemy, "kind", None) != "CORE"
            and getattr(enemy, "unit_type", None)
            in {UnitType.VANGUARD, UnitType.RANGER}
            and _distance(enemy.position, target.position)
            <= ASSAULT_REINFORCEMENT_RADIUS
            for enemy in (turn.visible_enemies if enemies is None else enemies)
        )
        if local_hostiles >= FULL_ASSAULT_HOSTILE_COUNT:
            return (
                {unit.id for unit in available_vanguards},
                {unit.id for unit in available_rangers},
            )

        pair_count = max(1, local_hostiles)
        selected_vanguards: set[UUID] = set()
        selected_rangers: set[UUID] = set()
        for _ in range(pair_count):
            if available_vanguards and available_rangers:
                vanguard, ranger = min(
                    product(available_vanguards, available_rangers),
                    key=lambda pair: (
                        max(
                            _distance(pair[0].position, target.position),
                            _distance(pair[1].position, target.position),
                        ),
                        _distance(pair[0].position, pair[1].position),
                        _uuid_sort_key(pair[0]),
                        _uuid_sort_key(pair[1]),
                    ),
                )
                available_vanguards.remove(vanguard)
                available_rangers.remove(ranger)
                selected_vanguards.add(vanguard.id)
                selected_rangers.add(ranger.id)
                continue
            if len(available_rangers) >= 2:
                pair = min(
                    combinations(available_rangers, 2),
                    key=lambda units: (
                        max(
                            _distance(units[0].position, target.position),
                            _distance(units[1].position, target.position),
                        ),
                        _distance(units[0].position, units[1].position),
                        _uuid_sort_key(units[0]),
                        _uuid_sort_key(units[1]),
                    ),
                )
                for ranger in pair:
                    available_rangers.remove(ranger)
                    selected_rangers.add(ranger.id)
                continue
            if len(available_vanguards) >= 2:
                pair = min(
                    combinations(available_vanguards, 2),
                    key=lambda units: (
                        max(
                            _distance(units[0].position, target.position),
                            _distance(units[1].position, target.position),
                        ),
                        _distance(units[0].position, units[1].position),
                        _uuid_sort_key(units[0]),
                        _uuid_sort_key(units[1]),
                    ),
                )
                for vanguard in pair:
                    available_vanguards.remove(vanguard)
                    selected_vanguards.add(vanguard.id)
                continue
            break
        return selected_vanguards, selected_rangers

    def _refresh_core_raid_group(
        self,
        turn: Turn,
        target: CoreRaidTarget,
    ) -> None:
        living_vanguards = {unit.id for unit in turn.vanguards}
        living_rangers = {unit.id for unit in turn.rangers}
        self.core_raid_vanguard_ids.intersection_update(living_vanguards)
        self.core_raid_ranger_ids.intersection_update(living_rangers)
        selected_vanguards, selected_rangers = self._select_strike_group_ids(
            turn,
            target,
            self._hostile_enemies(turn),
        )
        needed_vanguards = max(
            0,
            len(selected_vanguards) - len(self.core_raid_vanguard_ids),
        )
        needed_rangers = max(
            0,
            len(selected_rangers) - len(self.core_raid_ranger_ids),
        )
        self.core_raid_vanguard_ids.update(
            sorted(
                (
                    unit_id
                    for unit_id in selected_vanguards
                    if unit_id not in self.core_raid_vanguard_ids
                ),
                key=lambda unit_id: unit_id.bytes,
            )[:needed_vanguards]
        )
        self.core_raid_ranger_ids.update(
            sorted(
                (
                    unit_id
                    for unit_id in selected_rangers
                    if unit_id not in self.core_raid_ranger_ids
                ),
                key=lambda unit_id: unit_id.bytes,
            )[:needed_rangers]
        )

    def _strike_group_ids(
        self,
        turn: Turn,
        target: object | None,
    ) -> tuple[set[UUID], set[UUID]]:
        if (
            isinstance(target, CoreRaidTarget)
            and target.id == self.isolated_core_target_id
            and (self.core_raid_vanguard_ids or self.core_raid_ranger_ids)
        ):
            return (
                set(self.core_raid_vanguard_ids),
                set(self.core_raid_ranger_ids),
            )
        return self._select_strike_group_ids(
            turn,
            target,
            self._hostile_enemies(turn),
        )

    def _refresh_core_raid_launch(
        self,
        turn: Turn,
        target: object | None,
    ) -> bool:
        if not isinstance(target, CoreRaidTarget) or self.core_raid_launched:
            return True
        self._refresh_core_raid_group(turn, target)
        if (
            self.core_raid_started_tick is not None
            and turn.tick - self.core_raid_started_tick
            >= CORE_RAID_RALLY_TIMEOUT_TICKS
        ):
            self.core_raid_launched = True
            return True
        mature_fleet = (
            len(turn.vanguards) >= MATURE_GUARD_FLEET_MIN
            and len(turn.rangers) >= MATURE_GUARD_FLEET_MIN
        )
        if (
            (
                mature_fleet
                and (
                    len(turn.vanguards) < MAIN_ASSAULT_MIN_VANGUARDS
                    or len(turn.rangers) < MAIN_ASSAULT_MIN_RANGERS
                )
            )
            or (
                not mature_fleet
                and (
                    len(turn.vanguards) < EARLY_ASSAULT_MIN_VANGUARDS
                    or len(turn.rangers) < EARLY_ASSAULT_MIN_RANGERS
                )
            )
            or self.core_raid_rally_position is None
        ):
            self.core_raid_launched = True
            return True

        strike_vanguards, strike_rangers = self._strike_group_ids(turn, target)
        rallied_vanguards = sum(
            vanguard.id in strike_vanguards
            and _distance(vanguard.position, self.core_raid_rally_position)
            <= MAIN_ASSAULT_RALLY_RADIUS
            for vanguard in turn.vanguards
        )
        rallied_rangers = sum(
            ranger.id in strike_rangers
            and _distance(ranger.position, self.core_raid_rally_position)
            <= MAIN_ASSAULT_RALLY_RADIUS
            for ranger in turn.rangers
        )
        if mature_fleet:
            required_vanguards = CORE_RAID_VANGUARDS
            required_rangers = CORE_RAID_RANGERS
        else:
            required_vanguards = len(strike_vanguards)
            required_rangers = len(strike_rangers)
        self.core_raid_launched = (
            rallied_vanguards >= required_vanguards
            and rallied_rangers >= required_rangers
        )
        return self.core_raid_launched

    def _refresh_return_states(self, turn: Turn) -> None:
        core = turn.core
        if core is None:
            self.squad_return_ids.clear()
            self.scout_return_ids.clear()
            return
        defenders = {unit.id: unit for unit in (*turn.vanguards, *turn.rangers)}
        self.squad_return_ids.intersection_update(defenders)
        for unit_id in tuple(self.squad_return_ids):
            unit = defenders[unit_id]
            guard_radius = (
                VANGUARD_GUARD_RADIUS
                if unit.unit_type is UnitType.VANGUARD
                else RANGER_GUARD_RADIUS
            )
            local_threat = any(
                getattr(enemy, "kind") != "CORE"
                and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                and _distance(unit.position, enemy.position)
                <= UNIT_EVADE_TRIGGER_DISTANCE
                for enemy in self._hostile_enemies(turn)
            )
            if (
                not local_threat
                and _distance(unit.position, core.position) <= guard_radius
            ):
                self.squad_return_ids.discard(unit_id)

        worker_ids = {worker.id for worker in turn.workers}
        self.scout_return_ids.intersection_update(worker_ids)
        for worker_id in tuple(self.scout_cooldown_until):
            if (
                worker_id not in worker_ids
                or self.scout_cooldown_until[worker_id] < turn.tick
            ):
                self.scout_cooldown_until.pop(worker_id, None)

    def _core_defense_active(self, nearest_threat: int | None) -> bool:
        return bool(
            self.threat_assessment.breakout
            or self.threat_assessment.recent_core_attack
            or (
                nearest_threat is not None
                and nearest_threat <= CORE_EVADE_TRIGGER_DISTANCE
            )
        )

    def _recall_core_defenders(self, turn: Turn, threat_count: int) -> None:
        core = turn.core
        if core is None:
            return
        guard_vanguards, guard_rangers = _core_guard_ids(turn)
        for units, guard_ids in (
            (turn.vanguards, guard_vanguards),
            (turn.rangers, guard_rangers),
        ):
            recall_count = min(len(units), len(guard_ids) + threat_count)
            self.squad_return_ids.update(
                unit.id
                for unit in sorted(
                    units,
                    key=lambda unit: (
                        _distance(unit.position, core.position),
                        _uuid_sort_key(unit),
                    ),
                )[:recall_count]
            )
        if self.core_raid_spotter_id is not None:
            self.scout_return_ids.add(self.core_raid_spotter_id)

    def _active_raid_target_for_recall(self) -> CoreRaidTarget | None:
        target_id = self.isolated_core_target_id
        if target_id is None:
            return None
        remembered = self.stationary_core_memory.get(target_id)
        if remembered is None:
            return None
        return CoreRaidTarget(
            id=target_id,
            position=remembered.position,
            visible_enemy=None,
        )

    def _strike_group_locally_threatened(
        self,
        turn: Turn,
        target: object | None,
        enemies: Sequence[object],
    ) -> bool:
        if target is None:
            return False
        strike_vanguards, strike_rangers = self._strike_group_ids(turn, target)
        strike_ids = strike_vanguards | strike_rangers
        if self.core_raid_spotter_id is not None:
            strike_ids.add(self.core_raid_spotter_id)
        members = [unit for unit in turn.units if unit.id in strike_ids]
        target_id = getattr(target, "id", None)
        return any(
            enemy.id != target_id
            and any(
                _distance(member.position, enemy.position)
                <= UNIT_EVADE_TRIGGER_DISTANCE
                for member in members
            )
            for enemy in enemies
        )

    def _control_returning_scout(
        self,
        turn: Turn,
        worker: object,
        enemies: Sequence[object],
        context: MovementContext,
    ) -> bool:
        core = turn.core
        if core is None:
            return False
        nearby_enemies = tuple(
            enemy
            for enemy in enemies
            if _distance(worker.position, enemy.position)
            <= UNIT_EVADE_TRIGGER_DISTANCE
        )
        if nearby_enemies:
            self.scout_cooldown_until.pop(worker.id, None)
            self.scout_return_ids.add(worker.id)
        cooldown_until = self.scout_cooldown_until.get(worker.id, 0)
        if cooldown_until >= turn.tick:
            worker.wait()
            self._set_worker_mode(worker, "SCOUT_COOLDOWN", core.position)
            return True
        if worker.id not in self.scout_return_ids:
            return False
        if nearby_enemies and _queue_away_from_enemies(
            worker,
            nearby_enemies,
            context,
            turn.beacon.position,
        ):
            self._set_worker_mode(worker, "SCOUT_EVADE", core.position)
            return True
        if (
            not nearby_enemies
            and _distance(worker.position, core.position)
            <= SCOUT_SAFE_RETURN_RADIUS
        ):
            self.scout_return_ids.discard(worker.id)
            self.scout_cooldown_until[worker.id] = (
                turn.tick + SCOUT_COOLDOWN_TICKS
            )
            worker.wait()
            self._set_worker_mode(worker, "SCOUT_COOLDOWN", core.position)
            return True
        if _queue_toward(
            worker,
            core.position,
            context,
            allow_core_entry=True,
            allow_single_friendly_transit=True,
        ):
            self._set_worker_mode(worker, "SCOUT_RETURN", core.position)
        else:
            worker.wait()
            self._set_worker_mode(worker, "SCOUT_RETURN_BLOCKED", core.position)
        return True

    def _control_vanguards(
        self,
        turn: Turn,
        enemies: Sequence[object],
        context: MovementContext,
        isolated_core_target: object | None,
        *,
        raid_launched: bool,
        reserve_core_for_spawn: bool,
    ) -> None:
        core = turn.core
        if core is None:
            return
        target_id = getattr(isolated_core_target, "id", None)
        visible_target = (
            isolated_core_target.visible_enemy
            if isinstance(isolated_core_target, CoreRaidTarget)
            else isolated_core_target
        )
        priority_target_ids = _core_attack_priority_ids(
            turn,
            isolated_core_target,
            context.obstacles,
            enemies,
        )
        avoidance_enemies = tuple(
            enemy for enemy in enemies if enemy.id != target_id
        )
        core_threats = _core_threatening_enemies(
            core.position,
            enemies,
            context.obstacles,
        )
        guard_vanguards, _ = _core_guard_ids(turn)
        reserve_vanguards, _ = _core_reserve_ids(turn)
        strike_vanguards, _ = self._strike_group_ids(turn, isolated_core_target)
        for index, vanguard in enumerate(
            sorted(turn.vanguards, key=_uuid_sort_key)
        ):
            attack_targets = _legal_attack_targets(
                vanguard,
                enemies,
                context.obstacles,
            )
            if attack_targets:
                target = min(
                    attack_targets,
                    key=lambda enemy: (
                        int(enemy.id not in priority_target_ids),
                        _combat_target_key(vanguard.position, enemy),
                    ),
                )
                direction = _direction_to_adjacent(
                    vanguard.position,
                    target.position,
                )
                if direction is not None:
                    vanguard.sweep(direction)
                    continue
            if context.preplanned_units and vanguard.id in context.preplanned_units:
                continue
            immediate_core_threats = [
                enemy
                for enemy in core_threats
                if _distance(vanguard.position, core.position)
                <= CORE_PROTECTOR_RADIUS
                and _distance(vanguard.position, enemy.position) == 1
            ]
            if immediate_core_threats:
                target = min(
                    immediate_core_threats,
                    key=lambda enemy: _combat_target_key(vanguard.position, enemy),
                )
                direction = _direction_to_adjacent(vanguard.position, target.position)
                if direction is not None:
                    vanguard.sweep(direction)
                    continue
            if self._healing_return_ready(turn, vanguard) and (
                not reserve_core_for_spawn or vanguard.position == core.position
            ):
                if vanguard.position == core.position:
                    vanguard.heal()
                elif not _queue_toward(
                    vanguard,
                    core.position,
                    context,
                    allow_core_entry=True,
                    allow_single_friendly_transit=True,
                ):
                    vanguard.wait()
                continue
            pursuing_adjacent = [
                enemy
                for enemy in enemies
                if enemy.id in self.pursuing_enemy_ids
                and _distance(vanguard.position, enemy.position) == 1
            ]
            if pursuing_adjacent:
                pursuer = min(pursuing_adjacent, key=_uuid_sort_key)
                direction = _direction_to_adjacent(
                    vanguard.position,
                    pursuer.position,
                )
                if direction is not None:
                    vanguard.sweep(direction)
                    continue
            adjacent_enemies = [
                enemy
                for enemy in enemies
                if _distance(vanguard.position, enemy.position) == 1
            ]
            return_adjacent = list(adjacent_enemies)
            if (
                visible_target is not None
                and getattr(visible_target, "kind", None) == "CORE"
                and _distance(vanguard.position, visible_target.position) == 1
            ):
                return_adjacent.append(visible_target)
            if (
                vanguard.id in self.squad_return_ids
                and vanguard.id in strike_vanguards
                and return_adjacent
            ):
                target = min(
                    return_adjacent,
                    key=lambda enemy: _combat_target_key(
                        vanguard.position,
                        enemy,
                    ),
                )
                direction = _direction_to_adjacent(
                    vanguard.position,
                    target.position,
                )
                if direction is not None:
                    vanguard.sweep(direction)
                    continue
            if (
                vanguard.id in self.squad_return_ids
                and _distance(vanguard.position, core.position)
                > VANGUARD_GUARD_RADIUS
            ):
                if not _queue_toward(
                    vanguard,
                    core.position,
                    context,
                    avoid_danger=False,
                    target_radius=VANGUARD_GUARD_RADIUS,
                ):
                    vanguard.wait()
                continue
            strike_member = (
                vanguard.id in strike_vanguards
                and vanguard.id not in self.squad_return_ids
            )
            if strike_member:
                assault_position = (
                    isolated_core_target.position
                    if raid_launched or self.core_raid_rally_position is None
                    else self.core_raid_rally_position
                )
                direction = _direction_to_adjacent(
                    vanguard.position,
                    isolated_core_target.position,
                )
                if direction is not None and visible_target is not None:
                    vanguard.sweep(direction)
                    continue
                elif adjacent_enemies:
                    target = min(
                        adjacent_enemies,
                        key=lambda enemy: _combat_target_key(
                            vanguard.position,
                            enemy,
                        ),
                    )
                    direction = _direction_to_adjacent(
                        vanguard.position,
                        target.position,
                    )
                    if direction is not None:
                        vanguard.sweep(direction)
                        continue
                if not _queue_toward(
                    vanguard,
                    assault_position,
                    context,
                    avoid_danger=(
                        isinstance(isolated_core_target, CoreRaidTarget)
                        and not raid_launched
                    ),
                    target_radius=(
                        MAIN_ASSAULT_RALLY_RADIUS if not raid_launched else 0
                    ),
                ):
                    vanguard.wait()
                continue
            if vanguard.id in reserve_vanguards and not self.combat_pressure_active:
                target_position = _guard_post(
                    vanguard,
                    core.position,
                    context,
                    _rotate_directions(
                        (
                            Direction.DOWN,
                            Direction.UP,
                            Direction.LEFT,
                            Direction.RIGHT,
                        ),
                        index,
                    ),
                    CORE_RESERVE_RADIUS,
                )
                if target_position != vanguard.position and _queue_toward(
                    vanguard,
                    target_position,
                    context,
                ):
                    continue
                vanguard.wait()
                continue
            if vanguard.id == self.beacon_runner_id:
                if turn.beacon.carrier_id == vanguard.id:
                    if (
                        _distance(vanguard.position, core.position)
                        > BEACON_RETURN_RADIUS
                        and _queue_toward(vanguard, core.position, context)
                    ):
                        continue
                    vanguard.wait()
                    continue
                if (
                    vanguard.position == turn.beacon.position
                    and turn.beacon.status is BeaconStatus.GROUND
                ):
                    vanguard.pickup_beacon()
                    continue
                if _queue_toward(vanguard, turn.beacon.position, context):
                    continue
                vanguard.wait()
                continue
            nearby_enemies = [
                enemy
                for enemy in avoidance_enemies
                if _distance(vanguard.position, enemy.position)
                <= UNIT_EVADE_TRIGGER_DISTANCE
            ]
            adjacent = [
                enemy
                for enemy in nearby_enemies
                if _distance(vanguard.position, enemy.position) == 1
            ]
            if self.combat_pressure_active and adjacent:
                target = min(
                    adjacent,
                    key=lambda enemy: _combat_target_key(vanguard.position, enemy),
                )
                direction = _direction_to_adjacent(vanguard.position, target.position)
                if direction is not None:
                    vanguard.sweep(direction)
                    continue
            if self.combat_pressure_active:
                target_position = _guard_post(
                    vanguard,
                    core.position,
                    context,
                    _defense_post_directions(
                        core.position,
                        enemies,
                        CARDINAL_DIRECTIONS,
                        defender_index=index,
                        priority_ids=(
                            self.active_enemy_ids | self.pursuing_enemy_ids
                        ),
                    ),
                    VANGUARD_GUARD_RADIUS,
                )
                if target_position != vanguard.position and _queue_toward(
                    vanguard,
                    target_position,
                    context,
                ):
                    continue
                vanguard.wait()
                continue
            if _queue_away_from_enemies(
                vanguard,
                nearby_enemies,
                context,
                turn.beacon.position,
                keep_core_neighbors_clear=True,
            ):
                continue
            if adjacent:
                target = min(
                    adjacent,
                    key=lambda enemy: _combat_target_key(vanguard.position, enemy),
                )
                direction = _direction_to_adjacent(vanguard.position, target.position)
                if direction is not None:
                    vanguard.sweep(direction)
                    continue

            if nearby_enemies:
                vanguard.wait()
                continue

            if (
                vanguard.id not in guard_vanguards
                and _queue_toward(
                    vanguard,
                    self._combat_patrol_target(turn, vanguard, index),
                    context,
                )
            ):
                continue

            target_position = _guard_post(
                vanguard,
                core.position,
                context,
                _rotate_directions(
                    (
                        Direction.DOWN,
                        Direction.UP,
                        Direction.LEFT,
                        Direction.RIGHT,
                    ),
                    index,
                ),
                VANGUARD_GUARD_RADIUS,
            )
            if target_position != vanguard.position:
                moved = _queue_toward(
                    vanguard,
                    target_position,
                    context,
                )
                if moved:
                    continue
            vanguard.wait()

    def _control_rangers(
        self,
        turn: Turn,
        enemies: Sequence[object],
        context: MovementContext,
        isolated_core_target: object | None,
        *,
        raid_launched: bool,
        reserve_core_for_spawn: bool,
    ) -> None:
        core = turn.core
        if core is None:
            return

        target_id = getattr(isolated_core_target, "id", None)
        visible_target = (
            isolated_core_target.visible_enemy
            if isinstance(isolated_core_target, CoreRaidTarget)
            else isolated_core_target
        )
        priority_target_ids = _core_attack_priority_ids(
            turn,
            isolated_core_target,
            context.obstacles,
            enemies,
        )
        avoidance_enemies = tuple(
            enemy for enemy in enemies if enemy.id != target_id
        )
        core_threats = _core_threatening_enemies(
            core.position,
            enemies,
            context.obstacles,
        )
        _, guard_rangers = _core_guard_ids(turn)
        _, reserve_rangers = _core_reserve_ids(turn)
        _, strike_rangers = self._strike_group_ids(turn, isolated_core_target)
        for index, ranger in enumerate(
            sorted(turn.rangers, key=_uuid_sort_key)
        ):
            attack_targets = _legal_attack_targets(
                ranger,
                enemies,
                context.obstacles,
            )
            if attack_targets:
                target = min(
                    attack_targets,
                    key=lambda enemy: (
                        int(enemy.id not in priority_target_ids),
                        _combat_target_key(ranger.position, enemy),
                    ),
                )
                ranger.shoot(target)
                continue
            if context.preplanned_units and ranger.id in context.preplanned_units:
                continue
            immediate_core_threats = [
                enemy
                for enemy in core_threats
                if _distance(ranger.position, core.position)
                <= CORE_PROTECTOR_RADIUS
                and _ranger_can_shoot(
                    ranger.position,
                    enemy.position,
                    context.obstacles,
                )
            ]
            if immediate_core_threats:
                target = min(
                    immediate_core_threats,
                    key=lambda enemy: _combat_target_key(ranger.position, enemy),
                )
                ranger.shoot(target)
                continue
            if self._healing_return_ready(turn, ranger) and (
                not reserve_core_for_spawn or ranger.position == core.position
            ):
                if ranger.position == core.position:
                    ranger.heal()
                elif not _queue_toward(
                    ranger,
                    core.position,
                    context,
                    allow_core_entry=True,
                    allow_single_friendly_transit=True,
                ):
                    ranger.wait()
                continue
            pursuing_targets = [
                enemy
                for enemy in enemies
                if enemy.id in self.pursuing_enemy_ids
                and _ranger_can_shoot(
                    ranger.position,
                    enemy.position,
                    context.obstacles,
                )
            ]
            if pursuing_targets:
                pursuer = min(
                    pursuing_targets,
                    key=lambda enemy: _combat_target_key(ranger.position, enemy),
                )
                ranger.shoot(pursuer)
                continue
            return_shootable = [
                enemy
                for enemy in enemies
                if _ranger_can_shoot(
                    ranger.position,
                    enemy.position,
                    context.obstacles,
                )
            ]
            if (
                visible_target is not None
                and getattr(visible_target, "kind", None) == "CORE"
                and _ranger_can_shoot(
                    ranger.position,
                    visible_target.position,
                    context.obstacles,
                )
            ):
                return_shootable.append(visible_target)
            if (
                ranger.id in self.squad_return_ids
                and ranger.id in strike_rangers
                and return_shootable
            ):
                target = min(
                    return_shootable,
                    key=lambda enemy: _combat_target_key(
                        ranger.position,
                        enemy,
                    ),
                )
                ranger.shoot(target)
                continue
            if (
                ranger.id in self.squad_return_ids
                and _distance(ranger.position, core.position)
                > RANGER_GUARD_RADIUS
            ):
                if not _queue_toward(
                    ranger,
                    core.position,
                    context,
                    avoid_danger=False,
                    target_radius=RANGER_GUARD_RADIUS,
                ):
                    ranger.wait()
                continue
            strike_member = (
                ranger.id in strike_rangers
                and ranger.id not in self.squad_return_ids
            )
            if strike_member:
                can_shoot_target_cell = _ranger_can_shoot(
                    ranger.position,
                    isolated_core_target.position,
                    context.obstacles,
                )
                if visible_target is not None and can_shoot_target_cell:
                    ranger.shoot(visible_target)
                elif (escort_targets := [
                    enemy
                    for enemy in avoidance_enemies
                    if _ranger_can_shoot(
                        ranger.position,
                        enemy.position,
                        context.obstacles,
                    )
                ]):
                    escort = min(
                        escort_targets,
                        key=lambda enemy: _combat_target_key(
                            ranger.position,
                            enemy,
                        ),
                    )
                    ranger.shoot(escort)
                elif not _queue_toward(
                    ranger,
                    (
                        isolated_core_target.position
                        if raid_launched or self.core_raid_rally_position is None
                        else self.core_raid_rally_position
                    ),
                    context,
                    avoid_danger=(
                        isinstance(isolated_core_target, CoreRaidTarget)
                        and not raid_launched
                    ),
                    target_radius=(
                        MAIN_ASSAULT_RALLY_RADIUS if not raid_launched else 0
                    ),
                ):
                    ranger.wait()
                continue
            if ranger.id in reserve_rangers and not self.combat_pressure_active:
                target_position = _guard_post(
                    ranger,
                    core.position,
                    context,
                    _rotate_directions(
                        (
                            Direction.LEFT,
                            Direction.RIGHT,
                            Direction.UP,
                            Direction.DOWN,
                        ),
                        index,
                    ),
                    CORE_RESERVE_RADIUS,
                )
                if target_position != ranger.position and _queue_toward(
                    ranger,
                    target_position,
                    context,
                ):
                    continue
                ranger.wait()
                continue
            nearby_enemies = [
                enemy
                for enemy in avoidance_enemies
                if _distance(ranger.position, enemy.position)
                <= UNIT_EVADE_TRIGGER_DISTANCE
            ]
            shootable = [
                enemy
                for enemy in nearby_enemies
                if _ranger_can_shoot(
                    ranger.position,
                    enemy.position,
                    context.obstacles,
                )
            ]
            if self.combat_pressure_active and shootable:
                target = min(
                    shootable,
                    key=lambda enemy: _combat_target_key(ranger.position, enemy),
                )
                ranger.shoot(target)
                continue
            if self.combat_pressure_active:
                target_position = _guard_post(
                    ranger,
                    core.position,
                    context,
                    _defense_post_directions(
                        core.position,
                        enemies,
                        CARDINAL_DIRECTIONS,
                        defender_index=index,
                        priority_ids=(
                            self.active_enemy_ids | self.pursuing_enemy_ids
                        ),
                    ),
                    RANGER_GUARD_RADIUS,
                )
                if target_position != ranger.position and _queue_toward(
                    ranger,
                    target_position,
                    context,
                ):
                    continue
                ranger.wait()
                continue
            if _queue_away_from_enemies(
                ranger,
                nearby_enemies,
                context,
                turn.beacon.position,
                keep_core_neighbors_clear=True,
            ):
                continue
            if shootable:
                target = min(
                    shootable,
                    key=lambda enemy: _combat_target_key(ranger.position, enemy),
                )
                ranger.shoot(target)
                continue

            if nearby_enemies:
                ranger.wait()
                continue

            if (
                ranger.id not in guard_rangers
                and _queue_toward(
                    ranger,
                    self._combat_patrol_target(turn, ranger, index),
                    context,
                )
            ):
                continue

            target_position = _guard_post(
                ranger,
                core.position,
                context,
                _rotate_directions(
                    (
                        Direction.LEFT,
                        Direction.RIGHT,
                        Direction.UP,
                        Direction.DOWN,
                    ),
                    index,
                ),
                RANGER_GUARD_RADIUS,
            )
            if target_position != ranger.position:
                moved = _queue_toward(
                    ranger,
                    target_position,
                    context,
                )
                if moved:
                    continue
            ranger.wait()

    def _should_wait_for_cargo(
        self,
        turn: Turn,
        context: MovementContext,
    ) -> bool:
        core = turn.core
        if core is None or turn.resource_space <= 0:
            return False
        cargo_workers = [worker for worker in turn.workers if worker.cargo > 0]
        if not cargo_workers:
            return False

        blocked = (
            set(context.obstacles)
            | set(context.enemy_cells)
            | set(context.danger_cells)
        )
        return_etas = [
            _estimated_path_cost(worker.position, core.position, blocked)
            for worker in cargo_workers
        ]
        nearest_eta = min(return_etas)
        total_cargo = sum(worker.cargo for worker in cargo_workers)
        return (
            total_cargo >= CORE_CONGESTED_CARGO
            or nearest_eta <= CORE_SHORT_CARGO_ETA
            or (
                total_cargo >= CORE_BULK_CARGO
                and nearest_eta <= CORE_BULK_CARGO_ETA
            )
        )

    def _core_blocked_cells(
        self,
        turn: Turn,
        context: MovementContext,
    ) -> set[Position]:
        blocked = (
            set(context.obstacles)
            | set(context.enemy_cells)
            | set(context.allied_cells)
            | set(turn.resource_cells)
        )
        blocked.update(
            cell
            for cell, occupants in context.friendly_counts.items()
            if occupants >= 2 and cell != context.core_position
        )
        return blocked

    def _start_core_retreat(
        self,
        turn: Turn,
        context: MovementContext,
        enemies: Sequence[object],
        *,
        reason: str,
    ) -> bool:
        core = turn.core
        if core is None:
            return False
        direction = _retreat_direction(
            core.position,
            turn.beacon.position,
            enemies,
            context.obstacles,
            self._core_blocked_cells(turn, context),
            self.last_retreat_direction,
            allow_beacon_approach=reason == "EVADE",
        )
        if direction is None:
            return False
        core.start_move(direction)
        self.last_retreat_direction = direction
        self.active_core_move_reason = reason
        return True

    def _start_alliance_rally(
        self,
        turn: Turn,
        context: MovementContext,
        target: Position,
    ) -> bool:
        core = turn.core
        if core is None:
            return False
        blocked = self._core_blocked_cells(turn, context) | set(
            context.danger_cells
        )
        directions = _path_directions(
            core.position,
            target,
            blocked,
            target_radius=ALLY_CORE_RALLY_RADIUS,
        )
        if not directions and self._clear_core_departure_lane(
            turn,
            context,
            target,
            target_radius=ALLY_CORE_RALLY_RADIUS,
        ):
            blocked = self._core_blocked_cells(turn, context) | set(
                context.danger_cells
            )
            directions = _path_directions(
                core.position,
                target,
                blocked,
                target_radius=ALLY_CORE_RALLY_RADIUS,
            )
        if not directions:
            return False
        direction = directions[0]
        destination = _destination(core.position, direction)
        if not _is_signed_int64_position(destination):
            return False
        core.start_move(direction)
        self.last_retreat_direction = direction
        self.active_core_move_reason = "ALLY_RALLY"
        return True

    def _clear_core_departure_lane(
        self,
        turn: Turn,
        context: MovementContext,
        target: Position,
        *,
        target_radius: int,
    ) -> bool:
        """Move friendly units out of the first viable Core route cell."""
        core = turn.core
        if core is None or core.view.state is not CoreState.NORMAL:
            return False
        fixed_blocked = (
            set(context.obstacles)
            | set(context.enemy_cells)
            | set(context.danger_cells)
            | set(context.allied_cells)
            | set(turn.resource_cells)
        )
        directions = _path_directions(
            core.position,
            target,
            fixed_blocked,
            target_radius=target_radius,
        )
        if not directions:
            return False
        lane = _destination(core.position, directions[0])
        excess = max(0, context.friendly_counts[lane] - 1)
        if excess == 0:
            return False
        core_neighbors = {
            _destination(core.position, direction)
            for direction in CARDINAL_DIRECTIONS
        }
        occupants = sorted(
            (unit for unit in turn.units if unit.position == lane),
            key=lambda unit: (
                0 if getattr(unit, "unit_type", None) is UnitType.WORKER else 1,
                int(getattr(unit, "cargo", 0) > 0),
                _uuid_sort_key(unit),
            ),
        )
        for unit in occupants:
            escape_directions = tuple(
                sorted(
                    CARDINAL_DIRECTIONS,
                    key=lambda direction: (
                        _destination(unit.position, direction) in core_neighbors,
                        context.friendly_counts[
                            _destination(unit.position, direction)
                        ],
                        -_distance(
                            _destination(unit.position, direction),
                            core.position,
                        ),
                        CARDINAL_DIRECTIONS.index(direction),
                    ),
                )
            )
            if not _queue_move(unit, escape_directions, context):
                continue
            if getattr(unit, "unit_type", None) is UnitType.WORKER:
                self._set_worker_mode(unit, "CORE_LANE_CLEAR", target)
            excess -= 1
            if excess == 0:
                return True
        return False

    def _moving_core_should_cancel(
        self,
        turn: Turn,
        context: MovementContext,
        enemies: Sequence[object],
        *,
        projected_core_damage: int,
        core_survival_margin: int,
    ) -> bool:
        core = turn.core
        if core is None or core.view.destination is None:
            return False

        self.last_core_cancel_reason = "NONE"
        destination = core.view.destination
        if destination in self._core_blocked_cells(turn, context):
            self.last_core_cancel_reason = "DESTINATION_BLOCKED"
            return True

        if self.active_core_move_reason == "ALLY_RALLY":
            rally_target = self._alliance_rally_target(turn)
            if rally_target is None:
                self.last_core_cancel_reason = "ALLY_RALLY_CHANGED"
                return True

        if enemies:
            current_threat_key = _position_threat_key(
                core.position,
                enemies,
                context.obstacles,
            )
            destination_threat_key = _position_threat_key(
                destination,
                enemies,
                context.obstacles,
            )
            current_enemy_distance = _minimum_enemy_distance(core.position, enemies)
            destination_enemy_distance = _minimum_enemy_distance(destination, enemies)
            enemy_cancel_radius = (
                CORE_EVADE_RELEASE_DISTANCE
                if self.active_core_move_reason == "EVADE"
                else CORE_EVADE_TRIGGER_DISTANCE
            )
            enemy_threat_relevant = (
                min(current_enemy_distance, destination_enemy_distance)
                <= enemy_cancel_radius
            )
            if (
                enemy_threat_relevant
                and destination_threat_key[0] > current_threat_key[0]
            ):
                self.last_core_cancel_reason = "PROJECTED_DAMAGE_WORSE"
                return True
            if (
                self.active_core_move_reason == "EVADE"
                and self.combat_pressure_active
                and destination_threat_key[0] <= current_threat_key[0]
            ):
                return False
            if enemy_threat_relevant and destination_threat_key > current_threat_key:
                self.last_core_cancel_reason = "ENEMY_RISK_WORSE"
                return True
            if destination_threat_key <= current_threat_key:
                return False

        projected_hp_damage = max(0, projected_core_damage - core.shield)
        if (
            projected_hp_damage > 0
            and core_survival_margin > 0
            and turn.resources >= 1
        ):
            self.last_core_cancel_reason = "PROJECTED_HEAL"
            return True

        if (
            self.active_core_move_reason == "EVADE"
            and turn.tick <= self.threat_caution_until_tick
        ):
            return False

        move_progress = core.view.move_progress or 0
        move_committed = move_progress >= CORE_MOVE_COMMIT_PROGRESS
        cargo_on_core = any(
            worker.cargo > 0 and worker.position == core.position
            for worker in turn.workers
        )
        if cargo_on_core or (
            not move_committed and self._should_wait_for_cargo(turn, context)
        ):
            self.last_core_cancel_reason = "CARGO_DELIVERY"
            return True

        cancel_for_beacon = (
            not move_committed
            and self.active_core_move_reason != "EVADE"
            and self.beacon_policy == "retreat"
            and _distance(destination, turn.beacon.position)
            < _distance(core.position, turn.beacon.position)
        )
        if cancel_for_beacon:
            self.last_core_cancel_reason = "BEACON_APPROACH"
        return cancel_for_beacon

    def _spawn_unit_type(
        self,
        turn: Turn,
        nearest_threat: int | None,
    ) -> UnitType | None:
        core = turn.core
        if (
            core is None
            or core.view.state is not CoreState.NORMAL
            or self.compatibility_hold
        ):
            return None
        population = len(turn.units)
        target_population = (
            self.worker_target
            + DEFENSE_VANGUARD_TARGET
            + DEFENSE_RANGER_TARGET
        )
        if population >= target_population:
            return None

        next_unit = _next_force_unit_type(
            self.worker_target,
            len(turn.workers),
            len(turn.vanguards),
            len(turn.rangers),
        )
        if next_unit is None:
            return None

        emergency_spawn = self._core_defense_active(nearest_threat)
        if emergency_spawn:
            guard_vanguards, guard_rangers = _core_guard_ids(turn)
            local_vanguards = sum(
                _distance(unit.position, core.position) <= VANGUARD_GUARD_RADIUS
                for unit in turn.vanguards
            )
            local_rangers = sum(
                _distance(unit.position, core.position) <= RANGER_GUARD_RADIUS
                for unit in turn.rangers
            )
            vanguard_deficit = max(0, len(guard_vanguards) - local_vanguards)
            ranger_deficit = max(0, len(guard_rangers) - local_rangers)
            next_unit = (
                UnitType.VANGUARD
                if nearest_threat is not None and nearest_threat <= 3
                else UnitType.RANGER
                if nearest_threat is not None and nearest_threat <= 6
                else UnitType.RANGER
                if ranger_deficit > vanguard_deficit
                else UnitType.VANGUARD
            )
        elif (
            nearest_threat is not None
            and nearest_threat <= 3
            and len(turn.vanguards) < EARLY_DEFENSE_VANGUARD_TARGET
        ):
            next_unit = UnitType.VANGUARD
        elif (
            nearest_threat is not None
            and nearest_threat <= 6
            and len(turn.rangers) < EARLY_DEFENSE_RANGER_TARGET
        ):
            next_unit = UnitType.RANGER
        elif (
            self.production_weights
            and not self.recovery_mode
            and len(turn.workers) >= BASE_WORKER_TARGET
            and len(turn.vanguards) >= EARLY_DEFENSE_VANGUARD_TARGET
            and len(turn.rangers) >= EARLY_DEFENSE_RANGER_TARGET
        ):
            counts = {
                UnitType.WORKER: len(turn.workers),
                UnitType.VANGUARD: len(turn.vanguards),
                UnitType.RANGER: len(turn.rangers),
            }
            eligible = [
                unit_type
                for unit_type in (UnitType.WORKER, UnitType.VANGUARD, UnitType.RANGER)
                if self.production_weights.get(unit_type, 0) > 0
                and (
                    unit_type is not UnitType.WORKER
                    or counts[unit_type] < self.worker_target
                )
            ]
            if eligible:
                total_weight = sum(self.production_weights[unit_type] for unit_type in eligible)
                next_unit = max(
                    eligible,
                    key=lambda unit_type: (
                        (population + 1) * self.production_weights[unit_type]
                        - counts[unit_type] * total_weight,
                        -(
                            UnitType.WORKER,
                            UnitType.VANGUARD,
                            UnitType.RANGER,
                        ).index(unit_type),
                    ),
                )
            else:
                return None

        reserve = (
            0
            if emergency_spawn
            or (
                self.recovery_mode
                and next_unit is UnitType.WORKER
                and len(turn.workers)
                < min(RECOVERY_MIN_WORKERS, self.worker_target)
            )
            else CORE_RESOURCE_RESERVE
        )
        threshold = min(
            turn.resource_capacity,
            reserve + unit_cost(next_unit, population),
        )
        return next_unit if turn.resources >= threshold else None

    def _spawn_reservation(
        self,
        turn: Turn,
        target: object | None,
        retreat_enemies: Sequence[object],
    ) -> UnitType | None:
        core = turn.core
        if core is None:
            return None
        nearest_threat = min(
            (_distance(core.position, enemy.position) for enemy in retreat_enemies),
            default=None,
        )
        next_unit = self._spawn_unit_type(turn, nearest_threat)
        emergency_spawn = self._core_defense_active(nearest_threat)
        if (
            next_unit is None
            or core.hp < 5
            or core.shield < 5
            or self.healing_defender_ids
            or (
                self.beacon_policy == "pursue"
                and self._beacon_campaign_ready(turn, target)
                and core.position == turn.beacon.position
                and turn.beacon.status is BeaconStatus.GROUND
            )
            or (
                retreat_enemies
                and nearest_threat is not None
                and not emergency_spawn
                and (
                    nearest_threat <= CORE_EVADE_TRIGGER_DISTANCE
                    or self.preemptive_evade_enemy_ids
                    or self.pursuing_enemy_ids
                    or turn.tick <= self.recent_core_attack_until_tick
                )
            )
        ):
            return None
        return next_unit

    def _control_core(
        self,
        turn: Turn,
        context: MovementContext,
        isolated_core_target: object | None,
    ) -> None:
        core = turn.core
        if core is None:
            return
        enemies = tuple(
            enemy
            for enemy in self._hostile_enemies(turn)
            if getattr(enemy, "kind") != "CORE"
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
        )
        retreat_enemies = enemies + self._remembered_retreat_threats(turn, enemies)
        projected_core_damage = _projected_core_damage(
            core.position,
            enemies,
            context.obstacles,
        )
        projected_hp_damage = max(0, projected_core_damage - core.shield)
        core_survival_margin = core.hp - projected_hp_damage
        self.last_projected_core_damage = projected_core_damage
        self.last_core_survival_margin = core_survival_margin
        if _is_multi_axis_breakout(
            core.position,
            enemies,
            context.obstacles,
            self._core_blocked_cells(turn, context),
        ):
            self._refresh_threat_assessment(turn, breakout=True)
        if core.view.state is CoreState.MOVING:
            if (
                self.compatibility_hold
                and self.active_core_move_reason != "EVADE"
            ):
                core.cancel_move()
                self.last_core_cancel_reason = "COMPATIBILITY_HOLD"
                self.last_core_move_tick = turn.tick
                self.last_retreat_direction = None
                self.active_core_move_reason = None
                return
            if self._moving_core_should_cancel(
                turn,
                context,
                retreat_enemies,
                projected_core_damage=projected_core_damage,
                core_survival_margin=core_survival_margin,
            ):
                core.cancel_move()
                self.last_core_move_tick = turn.tick
                self.last_retreat_direction = None
                self.active_core_move_reason = None
            return
        if core.view.state is not CoreState.NORMAL:
            return

        if (
            not self.compatibility_hold
            and
            self.beacon_policy == "pursue"
            and self._beacon_campaign_ready(turn, isolated_core_target)
            and core.position == turn.beacon.position
            and turn.beacon.status is BeaconStatus.GROUND
        ):
            core.pickup_beacon()
            return

        can_spawn = context.friendly_counts[core.position] < 2
        nearest_threat = min(
            (
                _distance(core.position, enemy.position)
                for enemy in retreat_enemies
            ),
            default=None,
        )
        critical_core = core.shield == 0 and core.hp <= 2
        projected_nonfatal_hp_damage = (
            projected_hp_damage > 0 and core_survival_margin > 0
        )
        cargo_waiting = self._should_wait_for_cargo(turn, context)
        cargo_on_core = any(
            worker.cargo > 0 and worker.position == core.position
            for worker in turn.workers
        )
        if (
            turn.resources >= 1
            and core_survival_margin > 0
            and (
                (critical_core and core.hp < 5)
                or projected_nonfatal_hp_damage
            )
        ):
            core.heal()
            return

        next_unit = self._spawn_unit_type(turn, nearest_threat)
        if (
            can_spawn
            and core.hp == 5
            and core.shield >= 5
            and self._core_defense_active(nearest_threat)
            and next_unit in {UnitType.VANGUARD, UnitType.RANGER}
        ):
            core.spawn(next_unit)
            return

        if (
            retreat_enemies
            and nearest_threat is not None
            and (
                nearest_threat <= CORE_EVADE_TRIGGER_DISTANCE
                or self.preemptive_evade_enemy_ids
                or self.pursuing_enemy_ids
                or turn.tick <= self.recent_core_attack_until_tick
            )
            and self._start_core_retreat(
                turn,
                context,
                retreat_enemies,
                reason="EVADE",
            )
        ):
            return

        if core.hp < 5 and turn.resources >= 1 and core_survival_margin > 0:
            core.heal()
            return

        if core.shield < 5 and turn.resources >= 1:
            core.repair_shield()
            return

        if self.compatibility_hold:
            core.wait()
            return


        alliance_rally_target = self._alliance_rally_target(turn)
        if (
            alliance_rally_target is not None
            and core.hp == 5
            and core.shield >= 5
            and not retreat_enemies
            and not self.combat_pressure_active
            and not cargo_on_core
            and self._start_alliance_rally(
                turn,
                context,
                alliance_rally_target,
            )
        ):
            return

        if can_spawn and next_unit is not None:
            core.spawn(next_unit)
            return

        if cargo_waiting:
            core.wait()
            return

        # Routine expansion remains a Unit responsibility. The Core migrates
        # only for survival or toward the largest fresh alliance member.
        return


def _event_int(event: object, name: str) -> int:
    values = getattr(event, "values", None)
    if not isinstance(values, Mapping):
        return 0
    value = values.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _resource_event_effect(event: object) -> int:
    event_type = getattr(event, "event_type", "")
    if event_type in {"DEPOSIT_SUCCEEDED", "CORE_RESOURCES_CAPTURED"}:
        return _event_int(event, "amount")
    if event_type == "CORE_RESOURCE_OVERFLOW_DESTROYED":
        return -_event_int(event, "amount")
    if event_type in {
        "UNIT_HEAL_SUCCEEDED",
        "CORE_HEAL_SUCCEEDED",
        "CORE_REPAIR_SUCCEEDED",
        "CORE_SPAWN_SUCCEEDED",
    }:
        return -_event_int(event, "cost")
    return 0


def _resource_ledger_snapshot(turn: Turn) -> ResourceLedgerSnapshot:
    plan = turn.plan.model_dump(mode="json", exclude_none=True)
    core_action = plan.get("core_action", {})
    core_action_name = core_action.get("type", "NONE")
    if direction := core_action.get("direction"):
        core_action_name = f"{core_action_name}:{direction}"
    actions, _ = _turn_diagnostics(turn)
    return ResourceLedgerSnapshot(
        tick=turn.tick,
        resources=turn.resources,
        population=len(turn.units),
        workers=len(turn.workers),
        vanguards=len(turn.vanguards),
        rangers=len(turn.rangers),
        actions=actions,
        core_action=core_action_name,
    )


def _reconcile_resource_turn(
    previous: ResourceLedgerSnapshot,
    turn: Turn,
) -> ResourceLedgerResult:
    actual_delta = turn.resources - previous.resources
    _, events = _turn_diagnostics(turn)
    skipped_reason = None
    if turn.tick != previous.tick + 1:
        skipped_reason = "tick_gap"
    elif any(
        event.event_type in {"CORE_DESTROYED", "CORE_RESPAWNED"}
        for event in turn.events
    ):
        skipped_reason = "core_lifecycle"

    expected_delta = (
        actual_delta
        if skipped_reason is not None
        else sum(_resource_event_effect(event) for event in turn.events)
    )
    return ResourceLedgerResult(
        previous=previous,
        tick=turn.tick,
        resources=turn.resources,
        population=len(turn.units),
        workers=len(turn.workers),
        vanguards=len(turn.vanguards),
        rangers=len(turn.rangers),
        actual_delta=actual_delta,
        expected_delta=expected_delta,
        unexplained_delta=actual_delta - expected_delta,
        events=events,
        skipped_reason=skipped_reason,
    )


def _emit_resource_ledger(result: ResourceLedgerResult) -> None:
    if result.actual_delta >= 0:
        return
    previous = result.previous
    prefix = (
        "WARNING unexplained_resource_loss"
        if result.unexplained_loss
        else "RESOURCE_LEDGER"
    )
    print(
        f"{prefix} tick={result.tick} previous_tick={previous.tick} "
        f"resources={previous.resources}->{result.resources} "
        f"actual_delta={result.actual_delta} expected_delta={result.expected_delta} "
        f"unexplained_delta={result.unexplained_delta} "
        f"unexplained_loss={result.unexplained_loss} "
        f"previous_population={previous.population} current_population={result.population} "
        f"previous_fleet={previous.workers}W:{previous.vanguards}V:{previous.rangers}R "
        f"current_fleet={result.workers}W:{result.vanguards}V:{result.rangers}R "
        f"previous_actions={previous.actions} previous_core_action={previous.core_action} "
        f"events={result.events} skipped_reason={result.skipped_reason or 'none'}",
        file=sys.stderr if result.unexplained_loss else sys.stdout,
        flush=True,
    )


def _format_counts(counts: Counter[str]) -> str:
    if not counts:
        return "none"
    return ",".join(f"{name}:{counts[name]}" for name in sorted(counts))


def _visible_enemy_counts(turn: Turn) -> Counter[str]:
    counts: Counter[str] = Counter()
    for enemy in turn.visible_enemies:
        if getattr(enemy, "kind") == "CORE":
            counts["CORE"] += 1
        else:
            counts[getattr(enemy, "unit_type").value] += 1
    return counts


def _turn_diagnostics(turn: Turn) -> tuple[str, str]:
    plan = turn.plan.model_dump(mode="json", exclude_none=True)
    action_counts = Counter(
        action["type"] for action in plan.get("unit_actions", {}).values()
    )
    core_action = plan.get("core_action")
    if core_action:
        action_counts[core_action["type"]] += 1
    event_counts = Counter(
        (
            f"{event.event_type}/{event.reason_code}"
            if event.reason_code
            else event.event_type
        )
        for event in turn.events
    )
    return _format_counts(action_counts), _format_counts(event_counts)


def _position_diagnostics(turn: Turn, tactic: CoreFarmer) -> str:
    core = turn.core
    if core is None:
        return "core=respawning"
    plan = turn.plan.model_dump(mode="json", exclude_none=True)
    unit_actions = plan.get("unit_actions", {})
    worker_parts = []
    for worker in sorted(turn.workers, key=_uuid_sort_key):
        action = unit_actions.get(str(worker.id), {})
        action_name = action.get("type", "NONE")
        if direction := action.get("direction"):
            action_name = f"{action_name}:{direction}"
        mode = tactic.worker_modes.get(worker.id, "UNKNOWN")
        target = tactic.worker_targets.get(worker.id)
        target_text = (
            f"/t{target[0]}:{target[1]}" if target is not None else ""
        )
        worker_parts.append(
            f"{str(worker.id)[:6]}@{worker.position[0]}:{worker.position[1]}"
            f"/c{worker.cargo}/a{action_name}/m{mode}{target_text}"
        )
    workers = ";".join(worker_parts)
    defender_parts = []
    for defender in sorted((*turn.vanguards, *turn.rangers), key=_uuid_sort_key):
        action = unit_actions.get(str(defender.id), {})
        action_name = action.get("type", "NONE")
        if direction := action.get("direction"):
            action_name = f"{action_name}:{direction}"
        defender_parts.append(
            f"{defender.unit_type.value[0]}{str(defender.id)[:6]}@"
            f"{defender.position[0]}:{defender.position[1]}/a{action_name}"
        )
    defenders = ";".join(defender_parts)
    defender_on_core = sum(
        defender.position == core.position
        for defender in (*turn.vanguards, *turn.rangers)
    )
    delivery_blocked = sum(
        mode in {"RETURN_BLOCKED", "CLEAR_CORE_BLOCKED"}
        for mode in tactic.worker_modes.values()
    )
    resource_blocked = sum(
        mode == "RESOURCE_BLOCKED" for mode in tactic.worker_modes.values()
    )
    scout_oldest_age = max(
        (
            turn.tick - last_seen
            for last_seen in tactic.scout_chunk_last_seen.values()
        ),
        default=0,
    )
    captured_resources = sum(
        capture.amount
        for event in turn.events
        if (capture := event.core_resource_capture) is not None
    )
    capture_destroyed = sum(
        capture.destroyed
        for event in turn.events
        if (capture := event.core_resource_capture) is not None
    )
    core_healed = sum(
        healing.amount
        for event in turn.events
        if event.event_type == "CORE_HEAL_SUCCEEDED"
        and (healing := event.healing) is not None
    )
    unit_healed = sum(
        healing.amount
        for event in turn.events
        if event.event_type == "UNIT_HEAL_SUCCEEDED"
        and (healing := event.healing) is not None
    )
    spawn_cost = sum(
        _event_int(event, "cost")
        for event in turn.events
        if event.event_type == "CORE_SPAWN_SUCCEEDED"
    )
    spawn_required = max(
        (
            _event_int(event, "required")
            for event in turn.events
            if event.event_type == "CORE_SPAWN_FAILED"
            and event.reason_code == "INSUFFICIENT_RESOURCES"
        ),
        default=0,
    )
    population = len(turn.units)
    enemy_counts = _visible_enemy_counts(turn)
    core_action = plan.get("core_action", {})
    core_action_name = core_action.get("type", "NONE")
    if direction := core_action.get("direction"):
        core_action_name = f"{core_action_name}:{direction}"
    beacon_distance = _distance(core.position, turn.beacon.position)
    movement = ""
    if core.view.state is CoreState.MOVING:
        movement = (
            f"/p{core.view.move_progress}:{core.view.move_required_ticks}"
            f"->{core.view.destination[0]}:{core.view.destination[1]}"
        )
    return (
        f"core={core.position[0]}:{core.position[1]} "
        f"core_state={core.view.state.value}{movement} "
        f"core_action={core_action_name} "
        f"beacon={turn.beacon.position[0]}:{turn.beacon.position[1]} "
        f"beacon_distance={beacon_distance} "
        f"known_resources={len(tactic.resource_last_seen)} "
        f"released_targets={len(tactic.last_released_targets)} "
        f"danger_cells={len(tactic.last_danger_cells)} "
        f"projected_core_damage={tactic.last_projected_core_damage} "
        f"core_survival_margin={tactic.last_core_survival_margin} "
        f"defender_on_core={defender_on_core} "
        f"delivery_blocked={delivery_blocked} "
        f"resource_blocked={resource_blocked} "
        f"scout_chunks={len(tactic.scout_chunk_last_seen)} "
        f"scout_oldest_age={scout_oldest_age} "
        f"captured_resources={captured_resources} "
        f"capture_destroyed={capture_destroyed} "
        f"core_healed={core_healed} "
        f"unit_healed={unit_healed} "
        f"spawn_cost={spawn_cost} "
        f"spawn_required={spawn_required} "
        f"next_worker_cost={unit_cost(UnitType.WORKER, population)} "
        f"next_vanguard_cost={unit_cost(UnitType.VANGUARD, population)} "
        f"next_ranger_cost={unit_cost(UnitType.RANGER, population)} "
        f"visible_enemies={len(turn.visible_enemies)} "
        f"alliance_ready={int(tactic.alliance_ready)} "
        f"alliance_peers={len(tactic.alliance_peers)} "
        f"alliance_leader={tactic.alliance_leader.account_id if tactic.alliance_leader else 'none'} "
        f"allied_objects={len(tactic.allied_object_ids)} "
        f"enemy_types={_format_counts(enemy_counts)} "
        f"global_posture={tactic.threat_assessment.global_posture.value} "
        f"threat_level={tactic.threat_assessment.level.value} "
        f"threat_reason={tactic.threat_assessment.primary_reason} "
        f"stationary_core_memory={len(tactic.stationary_core_memory)} "
        f"clear_core_target={str(tactic.isolated_core_target_id)[:8] if tactic.isolated_core_target_id else 'none'} "
        f"core_spotter={str(tactic.core_raid_spotter_id)[:8] if tactic.core_raid_spotter_id else 'none'} "
        f"clear_unit_target={str(tactic.stationary_unit_target_id)[:8] if tactic.stationary_unit_target_id else 'none'} "
        f"active_enemies={len(tactic.active_enemy_ids)} "
        f"preemptive_evade={len(tactic.preemptive_evade_enemy_ids)} "
        f"pursuing_enemies={len(tactic.pursuing_enemy_ids)} "
        f"recent_attack_threats={len(tactic.recent_attack_threats)} "
        f"recent_attack_until={tactic.recent_attack_until_tick} "
        f"recent_core_attack_until={tactic.recent_core_attack_until_tick} "
        f"combat_pressure={int(tactic.combat_pressure_active)} "
        f"squad_return={len(tactic.squad_return_ids)} "
        f"scout_return={len(tactic.scout_return_ids)} "
        f"squad_disengage_until={tactic.squad_disengage_until_tick} "
        f"healing_defenders={len(tactic.healing_defender_ids)} "
        f"compatibility_hold={int(tactic.compatibility_hold)} "
        f"threat_caution_until={tactic.threat_caution_until_tick} "
        f"core_cancel_reason={tactic.last_core_cancel_reason} "
        f"recovery_reason={tactic.recovery_reason} "
        f"recovery_until={tactic.recovery_until_tick} "
        f"defender_state={defenders or 'none'} "
        f"worker_state={workers or 'none'}"
    )


def _manual_override_summary(receipt: Received) -> str | None:
    if receipt.source is not CommandSource.MANUAL:
        return None
    unit_actions = len(receipt.plan.unit_actions)
    core_actions = int(receipt.plan.core_action is not None)
    return (
        f"WARNING tick={receipt.tick} manual_override "
        f"unit_actions={unit_actions} core_actions={core_actions}"
    )


def _is_turn_scoped_api_error(error_code: str) -> bool:
    return error_code in TURN_SKIP_API_ERRORS


def _notify_systemd(*lines: str) -> bool:
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    payload = "\n".join(lines).encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.connect(address)
            notifier.sendall(payload)
    except OSError:
        return False
    return True


def _systemd_status(turn: Turn, tactic: CoreFarmer, accepted_tick: int) -> str:
    core = turn.core
    if core is None:
        core_status = "core respawning"
        core_health = "core_hp none; core_shield none"
    else:
        core_status = (
            f"core {core.position[0]}:{core.position[1]} "
            f"{core.view.state.value}; beacon_distance "
            f"{_distance(core.position, turn.beacon.position)}"
        )
        core_health = f"core_hp {core.hp}; core_shield {core.shield}"
    tuning_generation = os.environ.get("ARENA_TUNING_GENERATION", "0").strip() or "0"
    return (
        f"STATUS=tick {accepted_tick}; resources {turn.resources}/"
        f"{turn.resource_capacity}; workers {len(turn.workers)}; "
        f"fleet {len(turn.vanguards)}v/{len(turn.rangers)}r; "
        f"phase {tactic.strategy_phase(turn)}; {core_status}; "
        f"posture {tactic.threat_assessment.global_posture.value}; "
        f"threat {tactic.threat_assessment.level.value}; "
        f"threat_reason {tactic.threat_assessment.primary_reason}; "
        f"recovery {int(tactic.recovery_mode)}; "
        f"danger {len(tactic.last_danger_cells)}; "
        f"enemies {len(turn.visible_enemies)}; {core_health}; "
        f"worker_target {tactic.worker_target}; "
        f"beacon_policy {tactic.beacon_policy}; "
        f"compatibility_hold {int(tactic.compatibility_hold)}; "
        f"tuning_generation {tuning_generation}"
    )


def _has_significant_events(turn: Turn) -> bool:
    routine_events = {
        "CORE_MOVE_PROGRESS",
        "CORE_MOVE_STARTED",
        "CORE_MOVE_SUCCEEDED",
        "UNIT_MOVE_SUCCEEDED",
    }
    return any(event.event_type not in routine_events for event in turn.events)


def _should_log_turn(turn: Turn) -> bool:
    return (
        bool(turn.visible_enemies)
        or turn.tick % LOG_SNAPSHOT_INTERVAL == 0
        or _has_significant_events(turn)
    )


class _AcceptedTurnWatchdog:
    def __init__(self, game: ArenaHeroClient, timeout_seconds: float) -> None:
        self.game = game
        self.timeout_seconds = timeout_seconds
        self.stop_event = threading.Event()
        self.timed_out = threading.Event()
        self.lock = threading.Lock()
        self.last_accepted_at = time.monotonic()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> _AcceptedTurnWatchdog:
        if self.timeout_seconds > 0:
            self.thread = threading.Thread(
                target=self._run,
                name="arena-accepted-turn-watchdog",
                daemon=True,
            )
            self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=1.0)

    def mark_accepted(self) -> None:
        with self.lock:
            self.last_accepted_at = time.monotonic()

    def _run(self) -> None:
        poll_interval = min(1.0, max(0.05, self.timeout_seconds / 4))
        while not self.stop_event.wait(poll_interval):
            with self.lock:
                elapsed = time.monotonic() - self.last_accepted_at
            if elapsed < self.timeout_seconds:
                continue
            self.timed_out.set()
            print(
                "WARNING no accepted Turn received within "
                f"{self.timeout_seconds:g}s; restarting the Agent",
                file=sys.stderr,
                flush=True,
            )
            self.game.close()
            return


def play(
    api_key: str,
    *,
    base_url: str,
    worker_target: int,
    beacon_policy: str,
    compatibility_marker: Path | None = DEFAULT_COMPATIBILITY_MARKER,
    heartbeat_file: Path | None = None,
    history_db: Path | None = None,
    stale_turn_timeout_seconds: float = DEFAULT_STALE_TURN_TIMEOUT_SECONDS,
    alliance_directory: Path | None = None,
    alliance_id: str | None = None,
    alliance_account_id: str | None = None,
    alliance_expected_members: int = 1,
    alliance_stale_seconds: float = DEFAULT_ALLIANCE_STALE_SECONDS,
    alliance_barrier_timeout_seconds: float = DEFAULT_ALLIANCE_BARRIER_TIMEOUT_SECONDS,
) -> None:
    if (
        not math.isfinite(stale_turn_timeout_seconds)
        or stale_turn_timeout_seconds < 0
    ):
        raise ValueError("stale Turn timeout must be finite and zero or positive")
    if alliance_directory is not None and (
        alliance_id is None or alliance_account_id is None
    ):
        raise ValueError(
            "alliance_id and alliance_account_id are required with alliance_directory"
        )
    if alliance_directory is None and (
        alliance_id is not None or alliance_account_id is not None
    ):
        raise ValueError(
            "alliance_directory is required when alliance coordination is configured"
        )
    alliance_coordinator = (
        AllianceCoordinator(
            alliance_directory,
            alliance_id=alliance_id,
            account_id=alliance_account_id,
            expected_members=alliance_expected_members,
            stale_seconds=alliance_stale_seconds,
            barrier_timeout_seconds=alliance_barrier_timeout_seconds,
        )
        if alliance_directory is not None
        and alliance_id is not None
        and alliance_account_id is not None
        else None
    )
    tactic = CoreFarmer(
        worker_target=worker_target,
        beacon_policy=beacon_policy,
        compatibility_marker=compatibility_marker,
        alliance_coordinator=alliance_coordinator,
    )
    last_accepted_tick: int | None = None
    resource_ledger_snapshot: ResourceLedgerSnapshot | None = None
    with ExitStack() as stack:
        history = (
            stack.enter_context(HistoryRecorder(history_db))
            if history_db is not None
            else None
        )
        game = stack.enter_context(
            ArenaHeroClient(api_key=api_key, base_url=base_url)
        )
        watchdog = _AcceptedTurnWatchdog(game, stale_turn_timeout_seconds)
        with watchdog:
            for event in game.events():
                if isinstance(event, Received):
                    if warning := _manual_override_summary(event):
                        print(warning, file=sys.stderr, flush=True)
                    continue
                if not isinstance(event, Turn):
                    continue
                turn = event
                if last_accepted_tick is not None and turn.tick <= last_accepted_tick:
                    print(
                        f"WARNING tick={turn.tick} duplicate_turn_ignored",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                if resource_ledger_snapshot is not None:
                    _emit_resource_ledger(
                        _reconcile_resource_turn(resource_ledger_snapshot, turn)
                    )
                active_orders = history.active_orders() if history is not None else ()
                control_config = history.control_config() if history is not None else {}
                production = control_config.get("production")
                tactic.production_weights = (
                    {
                        UnitType.WORKER: int(production["worker_weight"]),
                        UnitType.VANGUARD: int(production["vanguard_weight"]),
                        UnitType.RANGER: int(production["ranger_weight"]),
                    }
                    if isinstance(production, Mapping)
                    else None
                )
                tactic.revenge_usernames = (
                    set(history.revenge_usernames()) if history is not None else set()
                )
                tactic.choose_actions(turn)
                claimed_ids = {
                    UUID(str(unit_id))
                    for order in active_orders
                    for unit_id in order.get("unit_ids", ())
                }
                expedition_orders = tactic.expedition_orders(
                    turn,
                    control_config.get("expeditions", ()),
                    claimed_ids=claimed_ids,
                )
                completed_orders = tactic.apply_unit_orders(
                    turn,
                    (*active_orders, *expedition_orders),
                )
                try:
                    accepted = turn.submit()
                except APIError as exc:
                    if _is_turn_scoped_api_error(exc.error):
                        print(
                            f"WARNING tick={turn.tick} plan_skipped error={exc.error}",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    raise
                last_accepted_tick = accepted.tick
                watchdog.mark_accepted()
                if history is not None:
                    for order_id in completed_orders:
                        if order_id < 1:
                            continue
                        history.complete_order(order_id, tick=turn.tick)
                resource_ledger_snapshot = _resource_ledger_snapshot(turn)
                if history is not None:
                    try:
                        history.record(
                            turn,
                            allied_object_ids=tuple(tactic.allied_object_ids),
                            allied_usernames=tuple(tactic.allied_usernames),
                            strategy={
                                "phase": tactic.strategy_phase(turn),
                                "posture": tactic.threat_assessment.global_posture.value,
                                "threat": tactic.threat_assessment.level.value,
                                "threat_reason": tactic.threat_assessment.primary_reason,
                                "core_target": (
                                    str(tactic.isolated_core_target_id)
                                    if tactic.isolated_core_target_id
                                    else None
                                ),
                                "unit_target": (
                                    str(tactic.stationary_unit_target_id)
                                    if tactic.stationary_unit_target_id
                                    else None
                                ),
                                "beacon_runner": (
                                    str(tactic.beacon_runner_id)
                                    if tactic.beacon_runner_id
                                    else None
                                ),
                                "manual_orders": list(tactic.manual_order_ids),
                                "expedition_members": {
                                    str(expedition_id): [
                                        str(unit_id)
                                        for unit_id in sorted(
                                            member_ids,
                                            key=lambda item: item.bytes,
                                        )
                                    ]
                                    for expedition_id, member_ids in tactic.expedition_members.items()
                                },
                            },
                        )
                    except (OSError, sqlite3.Error) as exc:
                        print(
                            f"WARNING history_disabled error={type(exc).__name__}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        history = None
                _notify_systemd(
                    "WATCHDOG=1",
                    _systemd_status(turn, tactic, accepted.tick),
                )
                if heartbeat_file is not None:
                    write_heartbeat(
                        heartbeat_file,
                        tick=accepted.tick,
                        resources=turn.resources,
                        population=len(turn.units),
                        core_alive=turn.core is not None,
                    )
                if _should_log_turn(turn):
                    actions, events = _turn_diagnostics(turn)
                    print(
                        f"tick={accepted.tick} accepted={accepted.accepted} "
                        f"resources={turn.resources}/{turn.resource_capacity} "
                        f"workers={len(turn.workers)} vanguards={len(turn.vanguards)} "
                        f"rangers={len(turn.rangers)} cargo={sum(worker.cargo for worker in turn.workers)} "
                        f"visible_resources={len(turn.resource_cells)} actions={actions} events={events} "
                        f"recovery={int(tactic.recovery_mode)} phase={tactic.strategy_phase(turn)} "
                        f"worker_target={tactic.worker_target} "
                        f"beacon_policy={tactic.beacon_policy} "
                        f"tuning_generation={os.environ.get('ARENA_TUNING_GENERATION', '0').strip() or '0'} "
                        f"core_hp={turn.core.hp if turn.core else 'none'} "
                        f"core_shield={turn.core.shield if turn.core else 'none'} "
                        f"{_position_diagnostics(turn, tactic)}",
                        flush=True,
                    )
        if watchdog.timed_out.is_set():
            raise OSError(
                "no accepted Turn received before the unattended recovery timeout"
            )
    raise OSError("Arena Hero event stream ended unexpectedly")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggressive Arena Hero expansion tactic.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--worker-target", type=int, default=DEFAULT_WORKER_TARGET)
    parser.add_argument(
        "--beacon-policy",
        choices=("hold", "pursue", "retreat"),
        default=DEFAULT_BEACON_POLICY,
    )
    marker_group = parser.add_mutually_exclusive_group()
    marker_group.add_argument(
        "--compatibility-marker",
        type=Path,
        default=DEFAULT_COMPATIBILITY_MARKER,
        help="Path created by arena-hero-version-monitor when compatibility is unsafe.",
    )
    marker_group.add_argument(
        "--no-compatibility-marker",
        action="store_const",
        const=None,
        dest="compatibility_marker",
        help="Disable compatibility-marker checks (useful outside systemd).",
    )
    parser.add_argument(
        "--heartbeat-file",
        type=Path,
        help="Atomically write liveness metadata after every accepted Turn.",
    )
    parser.add_argument(
        "--history-db",
        type=Path,
        default=Path("arena_history.sqlite3"),
        help="Record bounded Turn history for arena-hero-dashboard.",
    )
    parser.add_argument(
        "--stale-turn-timeout-seconds",
        type=float,
        default=DEFAULT_STALE_TURN_TIMEOUT_SECONDS,
        help="Exit transiently after this many seconds without an accepted Turn (0 disables).",
    )
    parser.add_argument(
        "--alliance-directory",
        type=Path,
        help="Shared directory used to coordinate trusted local accounts.",
    )
    parser.add_argument(
        "--alliance-id",
        help="Alliance name shared by accounts in the coordination directory.",
    )
    parser.add_argument(
        "--alliance-account-id",
        help="Stable non-secret identifier for this account.",
    )
    parser.add_argument(
        "--alliance-expected-members",
        type=int,
        default=1,
        help="Pause autonomous actions until this many fresh members are present.",
    )
    parser.add_argument(
        "--alliance-stale-seconds",
        type=float,
        default=DEFAULT_ALLIANCE_STALE_SECONDS,
        help="Ignore member state older than this many seconds.",
    )
    parser.add_argument(
        "--alliance-barrier-timeout-seconds",
        type=float,
        default=DEFAULT_ALLIANCE_BARRIER_TIMEOUT_SECONDS,
        help="Wait this long for same-Turn member identity before choosing WAIT.",
    )
    return parser


def _is_transient_api_error(exc: APIError) -> bool:
    return exc.status_code in {408, 429} or exc.status_code >= 500


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        api_key = load_api_key(env_file=args.env_file)
        play(
            api_key,
            base_url=args.base_url,
            worker_target=args.worker_target,
            beacon_policy=args.beacon_policy,
            compatibility_marker=args.compatibility_marker,
            heartbeat_file=args.heartbeat_file,
            history_db=args.history_db,
            stale_turn_timeout_seconds=args.stale_turn_timeout_seconds,
            alliance_directory=args.alliance_directory,
            alliance_id=args.alliance_id,
            alliance_account_id=args.alliance_account_id,
            alliance_expected_members=args.alliance_expected_members,
            alliance_stale_seconds=args.alliance_stale_seconds,
            alliance_barrier_timeout_seconds=args.alliance_barrier_timeout_seconds,
        )

    except KeyboardInterrupt:
        print("Arena Hero agent stopped.", file=sys.stderr)
        return 130
    except AuthenticationError:
        print("Arena Hero authentication failed. Rotate and replace the local API key.", file=sys.stderr)
        return AUTHENTICATION_EXIT_CODE
    except PolicyViolationError as exc:
        print(f"Arena Hero connection rejected by policy: {exc}", file=sys.stderr)
        return POLICY_EXIT_CODE
    except ProtocolError as exc:
        print(f"Arena Hero protocol mismatch; upgrade the official SDK: {exc}", file=sys.stderr)
        return PROTOCOL_EXIT_CODE
    except APIError as exc:
        print(f"Arena Hero API rejected the request: {exc.error}: {exc.message}", file=sys.stderr)
        return TRANSIENT_EXIT_CODE if _is_transient_api_error(exc) else API_EXIT_CODE
    except (TransportError, OSError) as exc:
        print(f"Arena Hero transient failure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return TRANSIENT_EXIT_CODE
    except ValueError as exc:
        print(f"Arena Hero configuration error: {exc}", file=sys.stderr)
        return CONFIGURATION_EXIT_CODE
    except ArenaHeroError as exc:
        print(f"Arena Hero agent stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
        return AGENT_EXIT_CODE
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
