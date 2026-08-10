from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any


DEFAULT_HISTORY_LIMIT = 4096
VISION_RADII = {
    "CORE": 5,
    "WORKER": 3,
    "VANGUARD": 4,
    "RANGER": 5,
}

Position = tuple[int, int]


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
                line = _supercover_cells(origin, target)
                if target in obstacles or not any(
                    cell in obstacles for cell in line[1:-1]
                ):
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
            """
        )

    def record(
        self,
        turn: object,
        *,
        strategy: Mapping[str, object] | None = None,
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
        enemy_cores = [
            item
            for item in objects
            if isinstance(item, dict)
            and item.get("kind") == "CORE"
            and item.get("controlled") is False
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


def read_overview(path: Path, *, tick: int | None = None) -> dict[str, object]:
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
        explored = connection.execute(
            """
            SELECT x, y, first_seen_tick, last_seen_tick FROM explored_cells
            WHERE first_seen_tick <= ?
            """,
            (selected_tick,),
        ).fetchall()
        obstacles = connection.execute(
            "SELECT x, y FROM obstacle_cells WHERE first_seen_tick <= ?",
            (selected_tick,),
        ).fetchall()
        resources = connection.execute(
            """
            SELECT x, y, first_seen_tick, last_seen_tick FROM resource_cells
            WHERE first_seen_tick <= ?
            """,
            (selected_tick,),
        ).fetchall()
        enemy_cores = connection.execute(
            """
            SELECT sighting.* FROM enemy_core_sightings AS sighting
            JOIN (
                SELECT core_id, MAX(tick) AS tick
                FROM enemy_core_sightings WHERE tick <= ? GROUP BY core_id
            ) AS latest
            ON latest.core_id = sighting.core_id AND latest.tick = sighting.tick
            """,
            (selected_tick,),
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
