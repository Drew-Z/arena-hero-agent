from __future__ import annotations

import argparse
import json
import mimetypes
import math
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from arena_history import (
    cancel_unit_order,
    create_unit_order,
    delete_expedition,
    list_ticks,
    list_unit_orders,
    read_control_config,
    read_kill_stats,
    read_overview,
    save_expedition,
    save_alliance_config,
    save_production_config,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_BASE_URL = "https://api.arenahero.io"
DEFAULT_ALLIANCE_STALE_SECONDS = 60.0
LEADERBOARD_KEYS = (
    "beacon_ticks_held",
    "damage_dealt",
    "core_destruction_participations",
)


def _validated_leaderboard(value: object) -> dict[str, list[dict[str, object]]]:
    if not isinstance(value, dict):
        raise ValueError("leaderboard response must be an object")
    result: dict[str, list[dict[str, object]]] = {}
    for key in LEADERBOARD_KEYS:
        entries = value.get(key)
        if not isinstance(entries, list):
            raise ValueError(f"leaderboard field is invalid: {key}")
        validated = []
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("rank"), int)
                or isinstance(entry.get("rank"), bool)
                or not isinstance(entry.get("username"), str)
                or not entry["username"].strip()
                or not isinstance(entry.get("score"), int)
                or isinstance(entry.get("score"), bool)
                or entry["rank"] < 1
                or entry["score"] < 0
            ):
                raise ValueError(f"leaderboard entry is invalid: {key}")
            validated.append(
                {
                    "rank": entry["rank"],
                    "username": entry["username"],
                    "score": entry["score"],
                }
            )
        result[key] = validated
    return result


class DashboardApplication:
    def __init__(
        self,
        *,
        history_db: Path,
        static_root: Path,
        base_url: str = DEFAULT_BASE_URL,
        alliance_directory: Path | None = None,
        alliance_account_id: str | None = None,
        alliance_stale_seconds: float = DEFAULT_ALLIANCE_STALE_SECONDS,
    ) -> None:
        self.history_db = history_db
        self.static_root = static_root.resolve()
        self.base_url = base_url.rstrip("/")
        self.alliance_directory = alliance_directory
        self.alliance_account_id = alliance_account_id
        self.alliance_stale_seconds = alliance_stale_seconds
        self._leaderboard_lock = threading.Lock()
        self._leaderboard_at = 0.0
        self._leaderboard: dict[str, list[dict[str, object]]] | None = None

    def alliance_objects(self) -> list[dict[str, object]]:
        if self.alliance_directory is None or self.alliance_account_id is None:
            return []
        now = time.time()
        objects: list[dict[str, object]] = []
        try:
            paths = tuple(self.alliance_directory.glob("*.json"))
        except OSError:
            return []
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                account_id = str(value["account_id"])
                updated_at = float(value["updated_at"])
                if (
                    account_id == self.alliance_account_id
                    or not math.isfinite(updated_at)
                    or abs(now - updated_at) > self.alliance_stale_seconds
                ):
                    continue
                username = str(value.get("username", ""))
                core_id = value.get("core_id")
                core_position = value.get("core_position")
                if (
                    isinstance(core_id, str)
                    and isinstance(core_position, list)
                    and len(core_position) == 2
                    and all(isinstance(item, int) for item in core_position)
                ):
                    objects.append(
                        {
                            "kind": "CORE",
                            "id": core_id,
                            "position": core_position,
                            "owner_username": username,
                            "alliance_account_id": account_id,
                        }
                    )
                units = value.get("units", [])
                if isinstance(units, list):
                    for unit in units:
                        if not isinstance(unit, dict):
                            continue
                        unit_id = unit.get("id")
                        position = unit.get("position")
                        unit_type = unit.get("unit_type")
                        if (
                            not isinstance(unit_id, str)
                            or not isinstance(position, list)
                            or len(position) != 2
                            or not all(isinstance(item, int) for item in position)
                            or unit_type not in {"WORKER", "VANGUARD", "RANGER"}
                        ):
                            continue
                        objects.append(
                            {
                                "kind": "UNIT",
                                "id": unit_id,
                                "position": position,
                                "unit_type": unit_type,
                                "hp": unit.get("hp"),
                                "cargo": unit.get("cargo", 0),
                                "owner_username": username,
                                "alliance_account_id": account_id,
                            }
                        )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
        return sorted(
            objects,
            key=lambda item: (
                str(item["alliance_account_id"]),
                0 if item["kind"] == "CORE" else 1,
                str(item["id"]),
            ),
        )

    def alliance_identity(self) -> tuple[frozenset[str], frozenset[str]]:
        objects = self.alliance_objects()
        return (
            frozenset(str(item["id"]) for item in objects),
            frozenset(
                str(item.get("owner_username", "")).casefold()
                for item in objects
                if str(item.get("owner_username", "")).strip()
            ),
        )

    def overview(self, **kwargs: object) -> dict[str, object]:
        overview = read_overview(self.history_db, **kwargs)
        alliance_objects = self.alliance_objects()
        allied_ids = {str(item["id"]) for item in alliance_objects}
        allied_usernames = {
            str(item.get("owner_username", "")).casefold()
            for item in alliance_objects
            if str(item.get("owner_username", "")).strip()
        }

        def is_ally(item: object) -> bool:
            if not isinstance(item, dict):
                return False
            return str(item.get("id", item.get("core_id", ""))) in allied_ids or (
                item.get("kind", "CORE") == "CORE"
                and str(item.get("owner_username", "")).casefold()
                in allied_usernames
            )

        state = overview.get("state")
        objects = state.get("objects", []) if isinstance(state, dict) else []
        if isinstance(objects, list):
            for item in objects:
                if is_ally(item):
                    item["relation"] = "ALLY"
        history = overview.get("enemy_core_history")
        if isinstance(history, list):
            overview["enemy_core_history"] = [
                item for item in history if not is_ally(item)
            ]
        overview["enemy_count"] = sum(
            isinstance(item, dict)
            and item.get("kind") in {"CORE", "UNIT"}
            and item.get("controlled") is False
            and not is_ally(item)
            for item in objects
        )
        overview["alliance_objects"] = alliance_objects
        return overview

    def leaderboard(self) -> dict[str, object]:
        now = time.monotonic()
        with self._leaderboard_lock:
            if self._leaderboard is not None and now - self._leaderboard_at < 15:
                return {"available": True, "stale": False, **self._leaderboard}
            try:
                response = httpx.get(
                    f"{self.base_url}/api/v1/leaderboard",
                    headers={"Accept": "application/json"},
                    timeout=5.0,
                    follow_redirects=False,
                )
                response.raise_for_status()
                self._leaderboard = _validated_leaderboard(response.json())
                self._leaderboard_at = now
                return {"available": True, "stale": False, **self._leaderboard}
            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                if self._leaderboard is not None:
                    return {
                        "available": True,
                        "stale": True,
                        "error": type(exc).__name__,
                        **self._leaderboard,
                    }
                return {
                    "available": False,
                    "stale": False,
                    "error": type(exc).__name__,
                }


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ticks":
            values = parse_qs(parsed.query)
            try:
                limit = int(values.get("limit", ["512"])[0])
            except ValueError:
                self._send_json(
                    {"error": "limit_must_be_an_integer"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"ticks": list_ticks(self.server.app.history_db, limit=limit)})
            return
        if parsed.path == "/api/overview":
            values = parse_qs(parsed.query)
            try:
                tick = int(values["tick"][0]) if "tick" in values else None
                since_tick = (
                    int(values["since_tick"][0]) if "since_tick" in values else None
                )
            except ValueError:
                self._send_json(
                    {"error": "tick_parameters_must_be_integers"},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            include_history = values.get("history", ["1"])[0] != "0"
            overview = self.server.app.overview(
                    tick=tick,
                    since_tick=since_tick,
                    include_history=include_history,
                )
            self._send_json(overview)
            return
        if parsed.path == "/api/leaderboard":
            self._send_json(self.server.app.leaderboard())
            return
        if parsed.path == "/api/orders":
            self._send_json(list_unit_orders(self.server.app.history_db))
            return
        if parsed.path == "/api/kills":
            _, allied_usernames = self.server.app.alliance_identity()
            self._send_json(
                read_kill_stats(
                    self.server.app.history_db,
                    excluded_usernames=tuple(allied_usernames),
                )
            )
            return
        if parsed.path == "/api/control-config":
            self._send_json(read_control_config(self.server.app.history_db))
            return
        self._send_static(parsed.path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in {
            "/api/orders",
            "/api/control-config",
            "/api/alliance-config",
            "/api/expeditions",
        }:
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError("request body is too large or empty")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("request body must be an object")
            if path == "/api/orders":
                result = create_unit_order(
                    self.server.app.history_db,
                    unit_type=payload.get("unit_type", ""),
                    unit_count=payload.get("unit_count", 0),
                    unit_ids=payload.get("unit_ids", []),
                    target=(payload.get("target_x"), payload.get("target_y")),
                )
            elif path == "/api/control-config":
                result = save_production_config(
                    self.server.app.history_db,
                    worker_weight=payload.get("worker_weight"),
                    vanguard_weight=payload.get("vanguard_weight"),
                    ranger_weight=payload.get("ranger_weight"),
                )
            elif path == "/api/alliance-config":
                result = save_alliance_config(
                    self.server.app.history_db,
                    rally_radius=payload.get("rally_radius"),
                )
            else:
                result = save_expedition(
                    self.server.app.history_db,
                    expedition_id=payload.get("id"),
                    name=payload.get("name", ""),
                    ranger_count=payload.get("ranger_count"),
                    vanguard_count=payload.get("vanguard_count"),
                    target=(payload.get("target_x"), payload.get("target_y")),
                    enabled=payload.get("enabled", True),
                )
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._send_json(
                {"error": "invalid_control_request", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(result, status=HTTPStatus.CREATED)

    def do_DELETE(self) -> None:
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 3 or parts[:2] not in (["api", "orders"], ["api", "expeditions"]):
            self._send_json({"error": "not_found"}, status=HTTPStatus.NOT_FOUND)
            return
        try:
            item_id = int(parts[2])
            if parts[1] == "orders":
                result = cancel_unit_order(self.server.app.history_db, item_id)
            else:
                delete_expedition(self.server.app.history_db, item_id)
                result = {"id": item_id, "deleted": True}
        except (ValueError, TypeError) as exc:
            self._send_json(
                {"error": "invalid_control_request", "message": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(result)

    def _send_json(
        self,
        payload: object,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (self.server.app.static_root / relative).resolve()
        if (
            not candidate.is_relative_to(self.server.app.static_root)
            or not candidate.is_file()
        ):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = candidate.read_bytes()
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{media_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"dashboard client={self.client_address[0]} {format % args}")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        app: DashboardApplication,
    ) -> None:
        self.app = app
        super().__init__(server_address, DashboardHandler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Arena Hero tactical history dashboard.")
    parser.add_argument("--history-db", type=Path, default=Path("arena_history.sqlite3"))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--alliance-directory", type=Path)
    parser.add_argument("--alliance-account-id")
    parser.add_argument(
        "--alliance-stale-seconds",
        type=float,
        default=DEFAULT_ALLIANCE_STALE_SECONDS,
    )
    parser.add_argument(
        "--static-root",
        type=Path,
        default=Path(__file__).with_name("dashboard"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("dashboard port must be between 1 and 65535")
    if not math.isfinite(args.alliance_stale_seconds) or args.alliance_stale_seconds <= 0:
        raise SystemExit("alliance stale seconds must be finite and positive")
    if (args.alliance_directory is None) != (args.alliance_account_id is None):
        raise SystemExit("alliance directory and account ID must be configured together")
    app = DashboardApplication(
        history_db=args.history_db,
        static_root=args.static_root,
        base_url=args.base_url,
        alliance_directory=args.alliance_directory,
        alliance_account_id=args.alliance_account_id,
        alliance_stale_seconds=args.alliance_stale_seconds,
    )
    if not app.static_root.is_dir():
        raise SystemExit(f"dashboard static directory is missing: {app.static_root}")
    server = DashboardServer((args.host, args.port), app)
    print(f"Arena Hero dashboard: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
