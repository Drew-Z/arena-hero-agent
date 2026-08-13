from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import UUID


DEFAULT_HISTORY_LIMIT = 4096
VISION_RADII = {
    "CORE": 5,
    "WORKER": 3,
    "VANGUARD": 4,
    "RANGER": 5,
}

Position = tuple[int, int]
UNIT_ORDER_TYPES = {"CORE", "WORKER", "VANGUARD", "RANGER"}
UNIT_ORDER_STATUSES = {"PENDING", "COMPLETED", "CANCELLED"}
PRODUCTION_UNIT_TYPES = ("WORKER", "VANGUARD", "RANGER")
DEFAULT_ALLIANCE_RALLY_RADIUS = 12
MIN_ALLIANCE_RALLY_RADIUS = 1
MAX_ALLIANCE_RALLY_RADIUS = 256
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1


def _position(value: object) -> Position | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return value[0], value[1]
    return None


def _supercover_cells(start: Position, end: Position) -> tuple[Position, ...]:
    x, y = start
    dx = end[0] - x
    dy = end[1] - y
    nx = abs(dx)
    ny = abs(dy)
    sx = 1 if dx > 0 else -1 if dx < 0 else 0
    sy = 1 if dy > 0 else -1 if dy < 0 else 0
    ix = iy = 0
    cells: list[Position] = [start]
    while ix < nx or iy < ny:
        decision = (1 + 2 * ix) * ny - (1 + 2 * iy) * nx
        if decision == 0:
            if sx:
                cells.append((x + sx, y))
            if sy:
                cells.append((x, y + sy))
            x += sx
            y += sy
            ix += int(bool(sx))
            iy += int(bool(sy))
        elif decision < 0:
            x += sx
            ix += 1
        else:
            y += sy
            iy += 1
        cells.append((x, y))
    return tuple(dict.fromkeys(cells))


def position_visible_from(
    origin: Position,
    target: Position,
    radius: int,
    obstacles: set[Position],
) -> bool:
    if abs(target[0] - origin[0]) + abs(target[1] - origin[1]) > radius:
        return False
    line = _supercover_cells(origin, target)
    return target in obstacles or not any(
        cell in obstacles for cell in line[1:-1]
    )


def visible_cells(state: Mapping[str, Any]) -> set[Position]:
    objects = state.get("objects")
    if not isinstance(objects, list):
        return set()
    obstacles = {
        position
        for item in objects
        if isinstance(item, dict) and item.get("kind") == "OBSTACLE"
        for raw_position in item.get("positions", [])
        if (position := _position(raw_position)) is not None
    }
    visible: set[Position] = set()
    for item in objects:
        if not isinstance(item, dict) or not item.get("controlled"):
            continue
        origin = _position(item.get("position"))
        if origin is None:
            continue
        kind = str(item.get("kind", ""))
        unit_type = str(item.get("unit_type", ""))
        radius = VISION_RADII.get(kind if kind == "CORE" else unit_type)
        if radius is None:
            continue
        for dx in range(-radius, radius + 1):
            for dy in range(-radius + abs(dx), radius - abs(dx) + 1):
                target = origin[0] + dx, origin[1] + dy
                if position_visible_from(origin, target, radius, obstacles):
                    visible.add(target)
    return visible


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _ensure_unit_orders_table(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'unit_orders'"
    ).fetchone()
    if existing is not None:
        existing_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(unit_orders)")
        }
        if "unit_ids_json" not in existing_columns:
            connection.execute(
                "ALTER TABLE unit_orders ADD COLUMN unit_ids_json TEXT NOT NULL DEFAULT '[]'"
            )
            connection.execute(
                "UPDATE unit_orders SET status = 'CANCELLED' WHERE status = 'PENDING'"
            )
    if existing is not None and "'CORE'" not in str(existing["sql"]):
        connection.executescript(
            """
            DROP INDEX IF EXISTS unit_orders_status_idx;
            ALTER TABLE unit_orders RENAME TO unit_orders_legacy;
            CREATE TABLE unit_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                unit_type TEXT NOT NULL,
                unit_count INTEGER NOT NULL,
                unit_ids_json TEXT NOT NULL DEFAULT '[]',
                target_x INTEGER NOT NULL,
                target_y INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                completed_tick INTEGER,
                CHECK (unit_type IN ('CORE', 'WORKER', 'VANGUARD', 'RANGER')),
                CHECK (unit_count BETWEEN 1 AND 64),
                CHECK (status IN ('PENDING', 'COMPLETED', 'CANCELLED'))
            );
            INSERT INTO unit_orders
                (id, created_at, unit_type, unit_count, unit_ids_json,
                 target_x, target_y, status, completed_tick)
            SELECT id, created_at, unit_type, unit_count, unit_ids_json,
                   target_x, target_y, status, completed_tick
            FROM unit_orders_legacy;
            DROP TABLE unit_orders_legacy;
            """
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS unit_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            unit_type TEXT NOT NULL,
            unit_count INTEGER NOT NULL,
            unit_ids_json TEXT NOT NULL DEFAULT '[]',
            target_x INTEGER NOT NULL,
            target_y INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            completed_tick INTEGER,
            CHECK (unit_type IN ('CORE', 'WORKER', 'VANGUARD', 'RANGER')),
            CHECK (unit_count BETWEEN 1 AND 64),
            CHECK (status IN ('PENDING', 'COMPLETED', 'CANCELLED'))
        );
        CREATE INDEX IF NOT EXISTS unit_orders_status_idx
            ON unit_orders (status, id);
        """
    )
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(unit_orders)")
    }
    if "unit_ids_json" not in columns:
        connection.execute(
            "ALTER TABLE unit_orders ADD COLUMN unit_ids_json TEXT NOT NULL DEFAULT '[]'"
        )
        connection.execute(
            "UPDATE unit_orders SET status = 'CANCELLED' WHERE status = 'PENDING'"
        )


def _ensure_combat_records_table(connection: sqlite3.Connection) -> bool:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS combat_records (
            event_id TEXT NOT NULL,
            tick INTEGER NOT NULL,
            direction TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'DESTROYED',
            username TEXT NOT NULL DEFAULT '',
            x INTEGER,
            y INTEGER,
            PRIMARY KEY (event_id, direction, username),
            CHECK (direction IN ('DEALT', 'SUFFERED')),
            CHECK (target_kind IN ('UNIT', 'CORE')),
            CHECK (outcome IN ('DAMAGED', 'DESTROYED'))
        );
        CREATE INDEX IF NOT EXISTS combat_records_tick_idx
            ON combat_records (tick);
        CREATE INDEX IF NOT EXISTS combat_records_username_idx
            ON combat_records (username, direction, target_kind);
        """
    )
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(combat_records)")
    }
    if "outcome" not in columns:
        connection.execute(
            "ALTER TABLE combat_records ADD COLUMN outcome TEXT NOT NULL DEFAULT 'DESTROYED'"
        )
        return True
    return False


def _ensure_control_config_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS production_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            worker_weight INTEGER NOT NULL,
            vanguard_weight INTEGER NOT NULL,
            ranger_weight INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            CHECK (worker_weight BETWEEN 0 AND 100),
            CHECK (vanguard_weight BETWEEN 0 AND 100),
            CHECK (ranger_weight BETWEEN 0 AND 100)
        );
        CREATE TABLE IF NOT EXISTS expeditions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ranger_count INTEGER NOT NULL,
            vanguard_count INTEGER NOT NULL,
            target_x INTEGER NOT NULL,
            target_y INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL,
            CHECK (ranger_count BETWEEN 0 AND 32),
            CHECK (vanguard_count BETWEEN 0 AND 32),
            CHECK (enabled IN (0, 1))
        );
        CREATE TABLE IF NOT EXISTS alliance_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            rally_radius INTEGER NOT NULL,
            updated_at REAL NOT NULL,
            CHECK (rally_radius BETWEEN 1 AND 256)
        );
        """
    )


def _record_enemy_core_destructions(
    connection: sqlite3.Connection,
    state: Mapping[str, object],
    snapshot_tick: int,
    *,
    allied_object_ids: frozenset[str] = frozenset(),
) -> None:
    events = state.get("events", [])
    if not isinstance(events, list):
        return
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("event_type") != "DESTRUCTION_PARTICIPATION"
            or event.get("reason_code") != "CORE"
        ):
            continue
        core_id = str(event.get("target_id", ""))
        if not core_id or core_id in allied_object_ids:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO enemy_core_destructions (core_id, tick, event_id)
            VALUES (?, ?, ?)
            """,
            (
                core_id,
                int(event.get("tick", snapshot_tick)),
                str(event.get("event_id", "")),
            ),
        )


def _backfill_enemy_core_destructions(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT tick, state_json FROM snapshots"):
        try:
            state = json.loads(row["state_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(state, dict):
            _record_enemy_core_destructions(connection, state, int(row["tick"]))


def _enemy_core_username(
    connection: sqlite3.Connection,
    core_id: object,
    tick: int,
) -> str:
    if not core_id:
        return ""
    row = connection.execute(
        """
        SELECT owner_username FROM enemy_core_sightings
        WHERE core_id = ? AND tick <= ? ORDER BY tick DESC LIMIT 1
        """,
        (str(core_id), tick),
    ).fetchone()
    return str(row["owner_username"]) if row is not None else ""


def _record_combat_events(
    connection: sqlite3.Connection,
    state: Mapping[str, Any],
    snapshot_tick: int,
    *,
    allied_object_ids: frozenset[str] = frozenset(),
    allied_usernames: frozenset[str] = frozenset(),
) -> None:
    for event in state.get("events", []):
        if not isinstance(event, dict) or not event.get("event_id"):
            continue
        if str(event.get("actor_id", "")) in allied_object_ids:
            continue
        event_id = str(event["event_id"])
        event_tick = int(event.get("tick", snapshot_tick))
        event_type = str(event.get("event_type", ""))
        values = event.get("values")
        values = values if isinstance(values, dict) else {}
        position = _position(event.get("position"))
        coordinates = position if position is not None else (None, None)
        records: list[tuple[str, str, str, str]] = []
        if event_type == "DESTRUCTION_PARTICIPATION":
            target_kind = str(event.get("reason_code", ""))
            if (
                target_kind in {"UNIT", "CORE"}
                and str(event.get("target_id", "")) not in allied_object_ids
            ):
                username = (
                    _enemy_core_username(
                        connection,
                        event.get("target_id"),
                        snapshot_tick,
                    )
                    if target_kind == "CORE"
                    else ""
                )
                records.append(("DEALT", target_kind, "DESTROYED", username))
        elif event_type == "UNIT_DAMAGED":
            outcome = "DESTROYED" if values.get("hp") == 0 else "DAMAGED"
            records.append(("SUFFERED", "UNIT", outcome, ""))
        elif event_type == "CORE_DAMAGED":
            records.append(("SUFFERED", "CORE", "DAMAGED", ""))
        elif event_type == "CORE_DESTROYED" and event.get("reason_code") == "ATTACK":
            attackers = values.get("destroyed_by")
            usernames = (
                [
                    str(username)
                    for username in attackers
                    if str(username).strip()
                    and str(username).casefold() not in allied_usernames
                ]
                if isinstance(attackers, list)
                else []
            )
            if not isinstance(attackers, list) or usernames:
                records.extend(
                    ("SUFFERED", "CORE", "DESTROYED", username)
                    for username in (usernames or [""])
                )
        for direction, target_kind, outcome, username in records:
            connection.execute(
                """
                INSERT OR IGNORE INTO combat_records
                    (event_id, tick, direction, target_kind, outcome, username, x, y)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    event_tick,
                    direction,
                    target_kind,
                    outcome,
                    username,
                    coordinates[0],
                    coordinates[1],
                ),
            )


def _backfill_combat_records(connection: sqlite3.Connection) -> None:
    for row in connection.execute(
        "SELECT tick, state_json FROM snapshots ORDER BY tick"
    ):
        _record_combat_events(connection, json.loads(row["state_json"]), int(row["tick"]))


def _revenge_scores(connection: sqlite3.Connection) -> dict[str, int]:
    scores: dict[str, int] = {}
    for row in connection.execute(
        """
        SELECT username, direction, target_kind, COUNT(*) AS total
        FROM combat_records WHERE username != ''
        GROUP BY username, direction, target_kind
        """
    ):
        username = str(row["username"])
        delta = int(row["total"])
        if row["direction"] == "DEALT" and row["target_kind"] == "CORE":
            delta = -delta
        elif row["direction"] != "SUFFERED":
            continue
        scores[username] = scores.get(username, 0) + delta
    return {username: score for username, score in scores.items() if score > 0}


def _unit_order_dict(row: sqlite3.Row) -> dict[str, object]:
    order = dict(row)
    order["unit_ids"] = json.loads(str(order.pop("unit_ids_json")))
    return order


class HistoryRecorder:
    def __init__(self, path: Path, *, limit: int = DEFAULT_HISTORY_LIMIT) -> None:
        if limit < 1:
            raise ValueError("history limit must be positive")
        self.path = path
        self.limit = limit
        self.connection = _connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                tick INTEGER PRIMARY KEY,
                captured_at REAL NOT NULL,
                status TEXT NOT NULL,
                resources INTEGER NOT NULL,
                population INTEGER NOT NULL,
                workers INTEGER NOT NULL,
                vanguards INTEGER NOT NULL,
                rangers INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                strategy_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS explored_cells (
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                first_seen_tick INTEGER NOT NULL,
                last_seen_tick INTEGER NOT NULL,
                PRIMARY KEY (x, y)
            );
            CREATE TABLE IF NOT EXISTS obstacle_cells (
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                first_seen_tick INTEGER NOT NULL,
                last_seen_tick INTEGER NOT NULL,
                PRIMARY KEY (x, y)
            );
            CREATE TABLE IF NOT EXISTS resource_cells (
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                first_seen_tick INTEGER NOT NULL,
                last_seen_tick INTEGER NOT NULL,
                PRIMARY KEY (x, y)
            );
            CREATE TABLE IF NOT EXISTS enemy_core_sightings (
                core_id TEXT NOT NULL,
                tick INTEGER NOT NULL,
                owner_username TEXT NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                hp INTEGER NOT NULL,
                shield INTEGER NOT NULL,
                state TEXT NOT NULL,
                PRIMARY KEY (core_id, tick)
            );
            CREATE INDEX IF NOT EXISTS enemy_core_tick_idx
                ON enemy_core_sightings (tick);
            CREATE TABLE IF NOT EXISTS enemy_core_destructions (
                core_id TEXT NOT NULL,
                tick INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                PRIMARY KEY (core_id, tick, event_id)
            );
            CREATE INDEX IF NOT EXISTS enemy_core_destruction_tick_idx
                ON enemy_core_destructions (tick);
            """
        )
        with self.connection:
            _backfill_enemy_core_destructions(self.connection)
        _ensure_unit_orders_table(self.connection)
        _ensure_control_config_tables(self.connection)
        combat_schema_upgraded = _ensure_combat_records_table(self.connection)
        combat_records_empty = (
            self.connection.execute("SELECT 1 FROM combat_records LIMIT 1").fetchone()
            is None
        )
        if combat_schema_upgraded or combat_records_empty:
            with self.connection:
                _backfill_combat_records(self.connection)

    def record(
        self,
        turn: object,
        *,
        strategy: Mapping[str, object] | None = None,
        allied_object_ids: Sequence[object] = (),
        allied_usernames: Sequence[str] = (),
    ) -> None:
        tick = int(getattr(turn, "tick"))
        state = getattr(turn, "state").model_dump(mode="json", exclude_none=True)
        plan = getattr(turn, "plan").model_dump(mode="json", exclude_none=True)
        objects = state.get("objects", [])
        unit_types = [
            item.get("unit_type")
            for item in objects
            if isinstance(item, dict)
            and item.get("kind") == "UNIT"
            and item.get("controlled") is True
        ]
        obstacles = {
            position
            for item in objects
            if isinstance(item, dict) and item.get("kind") == "OBSTACLE"
            for raw_position in item.get("positions", [])
            if (position := _position(raw_position)) is not None
        }
        resources = {
            position
            for item in objects
            if isinstance(item, dict) and item.get("kind") == "RESOURCE"
            for raw_position in item.get("positions", [])
            if (position := _position(raw_position)) is not None
        }
        allied_ids = frozenset(str(identifier) for identifier in allied_object_ids)
        allied_names = frozenset(
            str(username).casefold()
            for username in allied_usernames
            if str(username).strip()
        )
        enemy_cores = [
            item
            for item in objects
            if isinstance(item, dict)
            and item.get("kind") == "CORE"
            and item.get("controlled") is False
            and str(item.get("id", "")) not in allied_ids
            and str(item.get("owner_username", "")).casefold() not in allied_names
            and _position(item.get("position")) is not None
        ]
        with self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tick,
                    time.time(),
                    str(state.get("status", "UNKNOWN")),
                    int(state.get("resources", 0)),
                    int(state.get("population", 0)),
                    unit_types.count("WORKER"),
                    unit_types.count("VANGUARD"),
                    unit_types.count("RANGER"),
                    json.dumps(state, separators=(",", ":"), ensure_ascii=False),
                    json.dumps(plan, separators=(",", ":"), ensure_ascii=False),
                    json.dumps(
                        dict(strategy or {}),
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                ),
            )
            self._upsert_cells("explored_cells", visible_cells(state), tick)
            self._upsert_cells("obstacle_cells", obstacles, tick)
            self._upsert_cells("resource_cells", resources, tick)
            for item in enemy_cores:
                position = _position(item["position"])
                assert position is not None
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO enemy_core_sightings
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item["id"]),
                        tick,
                        str(item.get("owner_username", "unknown")),
                        position[0],
                        position[1],
                        int(item.get("hp", 0)),
                        int(item.get("shield", 0)),
                        str(item.get("state", "UNKNOWN")),
                    ),
                )
            _record_enemy_core_destructions(
                self.connection,
                state,
                tick,
                allied_object_ids=allied_ids,
            )
            _record_combat_events(
                self.connection,
                state,
                tick,
                allied_object_ids=allied_ids,
                allied_usernames=allied_names,
            )
            cutoff = self.connection.execute(
                "SELECT tick FROM snapshots ORDER BY tick DESC LIMIT 1 OFFSET ?",
                (self.limit - 1,),
            ).fetchone()
            if cutoff is not None:
                self.connection.execute(
                    "DELETE FROM snapshots WHERE tick < ?",
                    (cutoff["tick"],),
                )
                self.connection.execute(
                    "DELETE FROM enemy_core_sightings WHERE tick < ?",
                    (cutoff["tick"],),
                )
                self.connection.execute(
                    "DELETE FROM enemy_core_destructions WHERE tick < ?",
                    (cutoff["tick"],),
                )

    def _upsert_cells(
        self,
        table: str,
        positions: set[Position],
        tick: int,
    ) -> None:
        if table not in {"explored_cells", "obstacle_cells", "resource_cells"}:
            raise ValueError("unsupported history table")
        self.connection.executemany(
            f"""
            INSERT INTO {table} VALUES (?, ?, ?, ?)
            ON CONFLICT(x, y) DO UPDATE SET last_seen_tick=excluded.last_seen_tick
            """,
            ((x, y, tick, tick) for x, y in positions),
        )

    def close(self) -> None:
        self.connection.close()

    def active_orders(self, *, limit: int = 64) -> list[dict[str, object]]:
        safe_limit = max(1, min(limit, 64))
        rows = self.connection.execute(
            """
            SELECT id, created_at, unit_type, unit_count, unit_ids_json,
                   target_x, target_y, status
            FROM unit_orders WHERE status = 'PENDING' ORDER BY id LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        return [_unit_order_dict(row) for row in rows]

    def revenge_usernames(self) -> frozenset[str]:
        return frozenset(username.casefold() for username in _revenge_scores(self.connection))

    def control_config(self) -> dict[str, object]:
        return _read_control_config(self.connection, ensure_tables=False)

    def complete_order(self, order_id: int, *, tick: int) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE unit_orders SET status = 'COMPLETED', completed_tick = ?
                WHERE id = ? AND status = 'PENDING'
                """,
                (tick, order_id),
            )

    def __enter__(self) -> HistoryRecorder:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def list_ticks(path: Path, *, limit: int = 512) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    safe_limit = max(1, min(limit, 4096))
    with closing(_connect(path, read_only=True)) as connection:
        rows = connection.execute(
            """
            SELECT tick, captured_at, status, resources, population,
                   workers, vanguards, rangers
            FROM snapshots ORDER BY tick DESC LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]


def create_unit_order(
    path: Path,
    *,
    unit_type: str,
    unit_count: int,
    unit_ids: Sequence[str],
    target: Position,
) -> dict[str, object]:
    normalized_type = str(unit_type).upper()
    if normalized_type not in UNIT_ORDER_TYPES:
        raise ValueError("unit_type must be CORE, WORKER, VANGUARD, or RANGER")
    if (
        isinstance(unit_count, bool)
        or not isinstance(unit_count, int)
        or not 1 <= unit_count <= 64
    ):
        raise ValueError("unit_count must be between 1 and 64")
    if normalized_type == "CORE" and unit_count != 1:
        raise ValueError("CORE orders must select exactly one Core")
    if isinstance(unit_ids, (str, bytes)) or not isinstance(unit_ids, Sequence):
        raise ValueError("unit_ids must be a list of Unit UUIDs")
    try:
        normalized_ids = [str(UUID(value)) for value in unit_ids]
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("unit_ids must contain valid Unit UUIDs") from exc
    if len(normalized_ids) != unit_count:
        raise ValueError("unit_count must match the selected Unit IDs")
    if len(set(normalized_ids)) != len(normalized_ids):
        raise ValueError("unit_ids must not contain duplicates")
    if (
        not isinstance(target, (tuple, list))
        or len(target) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not INT64_MIN <= value <= INT64_MAX
            for value in target
        )
    ):
        raise ValueError("target coordinates must be signed int64 values")
    with closing(_connect(path)) as connection:
        _ensure_unit_orders_table(connection)
        cursor = connection.execute(
            """
            INSERT INTO unit_orders
                (created_at, unit_type, unit_count, unit_ids_json, target_x, target_y)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                normalized_type,
                unit_count,
                json.dumps(normalized_ids, separators=(",", ":")),
                target[0],
                target[1],
            ),
        )
        order_id = int(cursor.lastrowid)
        connection.commit()
    return {
        "id": order_id,
        "unit_type": normalized_type,
        "unit_count": unit_count,
        "unit_ids": normalized_ids,
        "target_x": target[0],
        "target_y": target[1],
        "status": "PENDING",
    }


def list_unit_orders(path: Path, *, limit: int = 64) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    safe_limit = max(1, min(limit, 64))
    with closing(_connect(path, read_only=True)) as connection:
        try:
            rows = connection.execute(
                """
                SELECT id, created_at, unit_type, unit_count, target_x, target_y,
                       unit_ids_json, status, completed_tick
                FROM unit_orders ORDER BY id DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    return [_unit_order_dict(row) for row in rows]


def cancel_unit_order(path: Path, order_id: int) -> dict[str, object]:
    if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id < 1:
        raise ValueError("order_id must be a positive integer")
    with closing(_connect(path)) as connection:
        _ensure_unit_orders_table(connection)
        row = connection.execute(
            "SELECT status FROM unit_orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            raise ValueError("unit order was not found")
        if row["status"] == "PENDING":
            connection.execute(
                "UPDATE unit_orders SET status = 'CANCELLED' WHERE id = ?",
                (order_id,),
            )
            connection.commit()
        result = connection.execute(
            """
            SELECT id, created_at, unit_type, unit_count, unit_ids_json,
                   target_x, target_y, status, completed_tick
            FROM unit_orders WHERE id = ?
            """,
            (order_id,),
        ).fetchone()
        assert result is not None
        return _unit_order_dict(result)


def _read_control_config(
    connection: sqlite3.Connection,
    *,
    ensure_tables: bool = True,
) -> dict[str, object]:
    if ensure_tables:
        _ensure_control_config_tables(connection)
    production = connection.execute(
        "SELECT worker_weight, vanguard_weight, ranger_weight, updated_at "
        "FROM production_config WHERE id = 1"
    ).fetchone()
    expeditions = connection.execute(
        """
        SELECT id, name, ranger_count, vanguard_count, target_x, target_y,
               enabled, updated_at
        FROM expeditions ORDER BY id
        """
    ).fetchall()
    alliance = connection.execute(
        "SELECT rally_radius, updated_at FROM alliance_config WHERE id = 1"
    ).fetchone()
    return {
        "production": dict(production) if production is not None else None,
        "alliance": (
            dict(alliance)
            if alliance is not None
            else {"rally_radius": DEFAULT_ALLIANCE_RALLY_RADIUS, "updated_at": None}
        ),
        "expeditions": [
            {**dict(row), "enabled": bool(row["enabled"])} for row in expeditions
        ],
    }


def save_alliance_config(
    path: Path,
    *,
    rally_radius: int,
) -> dict[str, object]:
    if isinstance(rally_radius, bool) or not isinstance(rally_radius, int):
        raise ValueError("alliance rally radius must be an integer")
    if not MIN_ALLIANCE_RALLY_RADIUS <= rally_radius <= MAX_ALLIANCE_RALLY_RADIUS:
        raise ValueError(
            "alliance rally radius must be between "
            f"{MIN_ALLIANCE_RALLY_RADIUS} and {MAX_ALLIANCE_RALLY_RADIUS}"
        )
    updated_at = time.time()
    with closing(_connect(path)) as connection:
        _ensure_control_config_tables(connection)
        connection.execute(
            """
            INSERT INTO alliance_config VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                rally_radius=excluded.rally_radius,
                updated_at=excluded.updated_at
            """,
            (rally_radius, updated_at),
        )
        connection.commit()
    return {"rally_radius": rally_radius, "updated_at": updated_at}


def read_control_config(path: Path) -> dict[str, object]:
    with closing(_connect(path)) as connection:
        return _read_control_config(connection)


def save_production_config(
    path: Path,
    *,
    worker_weight: int,
    vanguard_weight: int,
    ranger_weight: int,
) -> dict[str, object]:
    weights = (worker_weight, vanguard_weight, ranger_weight)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in weights):
        raise ValueError("production weights must be integers")
    if any(not 0 <= value <= 100 for value in weights) or sum(weights) < 1:
        raise ValueError("production weights must be 0-100 and not all zero")
    updated_at = time.time()
    with closing(_connect(path)) as connection:
        _ensure_control_config_tables(connection)
        connection.execute(
            """
            INSERT INTO production_config VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                worker_weight=excluded.worker_weight,
                vanguard_weight=excluded.vanguard_weight,
                ranger_weight=excluded.ranger_weight,
                updated_at=excluded.updated_at
            """,
            (*weights, updated_at),
        )
        connection.commit()
    return {
        "worker_weight": worker_weight,
        "vanguard_weight": vanguard_weight,
        "ranger_weight": ranger_weight,
        "updated_at": updated_at,
    }


def save_expedition(
    path: Path,
    *,
    expedition_id: int | None,
    name: str,
    ranger_count: int,
    vanguard_count: int,
    target: Position,
    enabled: bool,
) -> dict[str, object]:
    normalized_name = str(name).strip()
    if not normalized_name or len(normalized_name) > 40:
        raise ValueError("expedition name must contain 1-40 characters")
    counts = (ranger_count, vanguard_count)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
        raise ValueError("expedition counts must be integers")
    if any(not 0 <= value <= 32 for value in counts) or not 1 <= sum(counts) <= 32:
        raise ValueError("expedition must contain 1-32 combat units")
    if (
        not isinstance(target, (tuple, list))
        or len(target) != 2
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not INT64_MIN <= value <= INT64_MAX
            for value in target
        )
    ):
        raise ValueError("target coordinates must be signed int64 values")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    updated_at = time.time()
    with closing(_connect(path)) as connection:
        _ensure_control_config_tables(connection)
        if expedition_id is None:
            cursor = connection.execute(
                """
                INSERT INTO expeditions
                    (name, ranger_count, vanguard_count, target_x, target_y,
                     enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (*((normalized_name,) + counts + tuple(target)), int(enabled), updated_at),
            )
            expedition_id = int(cursor.lastrowid)
        else:
            if isinstance(expedition_id, bool) or expedition_id < 1:
                raise ValueError("expedition id must be positive")
            cursor = connection.execute(
                """
                UPDATE expeditions SET name=?, ranger_count=?, vanguard_count=?,
                    target_x=?, target_y=?, enabled=?, updated_at=? WHERE id=?
                """,
                (*((normalized_name,) + counts + tuple(target)), int(enabled), updated_at, expedition_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("expedition was not found")
        connection.commit()
    return {
        "id": expedition_id,
        "name": normalized_name,
        "ranger_count": ranger_count,
        "vanguard_count": vanguard_count,
        "target_x": target[0],
        "target_y": target[1],
        "enabled": enabled,
        "updated_at": updated_at,
    }


def delete_expedition(path: Path, expedition_id: int) -> None:
    if isinstance(expedition_id, bool) or expedition_id < 1:
        raise ValueError("expedition id must be positive")
    with closing(_connect(path)) as connection:
        _ensure_control_config_tables(connection)
        cursor = connection.execute("DELETE FROM expeditions WHERE id = ?", (expedition_id,))
        if cursor.rowcount != 1:
            raise ValueError("expedition was not found")
        connection.commit()


def read_kill_stats(
    path: Path,
    *,
    recent_limit: int = 32,
    excluded_usernames: Sequence[str] = (),
) -> dict[str, object]:
    if not path.is_file():
        return {
            "available": False,
            "unit_participations": 0,
            "core_participations": 0,
            "recent": [],
            "losses": [],
            "revenge_targets": [],
        }
    with closing(_connect(path, read_only=True)) as connection:
        try:
            rows = connection.execute(
                """
                SELECT event_id, tick, direction, target_kind, outcome, username, x, y
                FROM combat_records ORDER BY tick DESC, event_id DESC
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return {
                "available": True,
                "unit_participations": 0,
                "core_participations": 0,
                "total_participations": 0,
                "recent": [],
                "losses": [],
                "revenge_targets": [],
            }
    excluded_names = {
        str(username).casefold()
        for username in excluded_usernames
        if str(username).strip()
    }
    rows = [
        row
        for row in rows
        if not row["username"]
        or str(row["username"]).casefold() not in excluded_names
    ]
    dealt = [row for row in rows if row["direction"] == "DEALT"]
    suffered = [row for row in rows if row["direction"] == "SUFFERED"]
    unit_participations = sum(row["target_kind"] == "UNIT" for row in dealt)
    core_participations = sum(row["target_kind"] == "CORE" for row in dealt)

    def record_dict(row: sqlite3.Row) -> dict[str, object]:
        return {
            "tick": int(row["tick"]),
            "kind": str(row["target_kind"]),
            "outcome": str(row["outcome"]),
            "username": str(row["username"]) or None,
            "position": (
                [int(row["x"]), int(row["y"])]
                if row["x"] is not None and row["y"] is not None
                else None
            ),
        }

    revenge_counts: dict[str, int] = {}
    for row in suffered:
        username = str(row["username"])
        if username:
            revenge_counts[username] = revenge_counts.get(username, 0) + 1
    for row in dealt:
        username = str(row["username"])
        if username and row["target_kind"] == "CORE":
            revenge_counts[username] = revenge_counts.get(username, 0) - 1
    return {
        "available": True,
        "unit_participations": unit_participations,
        "core_participations": core_participations,
        "total_participations": unit_participations + core_participations,
        "attacks_received": len({str(row["event_id"]) for row in suffered}),
        "units_lost": len(
            {
                str(row["event_id"])
                for row in suffered
                if row["target_kind"] == "UNIT" and row["outcome"] == "DESTROYED"
            }
        ),
        "cores_lost": len(
            {
                str(row["event_id"])
                for row in suffered
                if row["target_kind"] == "CORE" and row["outcome"] == "DESTROYED"
            }
        ),
        "recent": [record_dict(row) for row in dealt[: max(1, min(recent_limit, 128))]],
        "losses": [
            record_dict(row)
            for row in suffered
            if row["outcome"] == "DESTROYED"
        ][: max(1, min(recent_limit, 128))],
        "attacks": [
            record_dict(row) for row in suffered[: max(1, min(recent_limit, 128))]
        ],
        "revenge_targets": [
            {"username": username, "score": score}
            for username, score in sorted(
                revenge_counts.items(), key=lambda item: (-item[1], item[0])
            )
            if score > 0
        ],
    }


def revenge_usernames(path: Path) -> frozenset[str]:
    if not path.is_file():
        return frozenset()
    with closing(_connect(path, read_only=True)) as connection:
        try:
            return frozenset(
                username.casefold() for username in _revenge_scores(connection)
            )
        except sqlite3.OperationalError:
            return frozenset()


def read_overview(
    path: Path,
    *,
    tick: int | None = None,
    since_tick: int | None = None,
    include_history: bool = True,
) -> dict[str, object]:
    if not path.is_file():
        return {"available": False, "ticks": []}
    with closing(_connect(path, read_only=True)) as connection:
        if tick is None:
            row = connection.execute(
                "SELECT * FROM snapshots ORDER BY tick DESC LIMIT 1"
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM snapshots WHERE tick <= ? ORDER BY tick DESC LIMIT 1",
                (tick,),
            ).fetchone()
        if row is None:
            return {"available": False, "ticks": []}
        selected_tick = int(row["tick"])
        history_start = max(0, since_tick or 0)
        explored = (
            connection.execute(
                """
                SELECT x, y, first_seen_tick, last_seen_tick FROM explored_cells
                WHERE first_seen_tick <= ? AND first_seen_tick > ?
                """,
                (selected_tick, history_start),
            ).fetchall()
            if include_history
            else []
        )
        obstacles = (
            connection.execute(
                """
                SELECT x, y FROM obstacle_cells
                WHERE first_seen_tick <= ? AND first_seen_tick > ?
                """,
                (selected_tick, history_start),
            ).fetchall()
            if include_history
            else []
        )
        resources = (
            connection.execute(
                """
                SELECT x, y, first_seen_tick, last_seen_tick FROM resource_cells
                WHERE first_seen_tick <= ? AND first_seen_tick > ?
                """,
                (selected_tick, history_start),
            ).fetchall()
            if include_history
            else []
        )
        enemy_cores = connection.execute(
            """
            SELECT sighting.* FROM enemy_core_sightings AS sighting
            JOIN (
                SELECT core_id, MAX(tick) AS tick
                FROM enemy_core_sightings WHERE tick <= ? GROUP BY core_id
            ) AS latest
            ON latest.core_id = sighting.core_id AND latest.tick = sighting.tick
            WHERE NOT EXISTS (
                SELECT 1 FROM enemy_core_destructions AS destruction
                WHERE destruction.core_id = sighting.core_id
                  AND destruction.tick >= sighting.tick
                  AND destruction.tick <= ?
            )
            """,
            (selected_tick, selected_tick),
        ).fetchall()
        trail_rows = connection.execute(
            """
            SELECT tick, state_json FROM snapshots
            WHERE tick <= ? ORDER BY tick DESC LIMIT 128
            """,
            (selected_tick,),
        ).fetchall()
    state = json.loads(row["state_json"])
    visible_enemy_core_ids = {
        str(item["id"])
        for item in state.get("objects", [])
        if isinstance(item, dict)
        and item.get("kind") == "CORE"
        and item.get("controlled") is False
    }
    enemy_core_history = []
    for row_item in enemy_cores:
        item = dict(row_item)
        last_seen_tick = int(item["tick"])
        item.update(
            currently_visible=item["core_id"] in visible_enemy_core_ids,
            last_seen_tick=last_seen_tick,
            age_ticks=selected_tick - last_seen_tick,
        )
        enemy_core_history.append(item)

    trails: dict[str, list[list[int]]] = {}
    for trail_row in reversed(trail_rows):
        trail_state = json.loads(trail_row["state_json"])
        for item in trail_state.get("objects", []):
            if (
                isinstance(item, dict)
                and item.get("controlled") is True
                and (position := _position(item.get("position"))) is not None
            ):
                trails.setdefault(str(item["id"]), []).append(
                    [position[0], position[1]]
                )
    return {
        "available": True,
        "tick": selected_tick,
        "history_delta": since_tick is not None,
        "captured_at": row["captured_at"],
        "state": state,
        "plan": json.loads(row["plan_json"]),
        "strategy": json.loads(row["strategy_json"]),
        "explored": [list(item) for item in explored],
        "obstacles": [list(item) for item in obstacles],
        "resource_history": [list(item) for item in resources],
        "enemy_core_history": enemy_core_history,
        "trails": trails,
    }
