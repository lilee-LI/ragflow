#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import signal
from contextlib import suppress
from pathlib import Path
from typing import Any


ALLOWED_MODES = {"normal", "down", "blackhole"}
MAX_CONTROL_BODY = 64 * 1024


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    if config.get("control_host") not in {"127.0.0.1", "::1"}:
        raise ValueError("control server must use loopback")
    control_port = int(config.get("control_port", 0))
    if not 1 <= control_port <= 65535:
        raise ValueError("invalid control port")
    token = str(config.get("control_token", ""))
    if len(token) < 16:
        raise ValueError("control token is too short")
    proxies = config.get("proxies")
    if not isinstance(proxies, dict) or not proxies:
        raise ValueError("at least one proxy is required")
    listeners: set[tuple[str, int]] = set()
    for name, item in proxies.items():
        if not isinstance(name, str) or not name.replace("_", "").isalnum():
            raise ValueError("invalid proxy name")
        if not isinstance(item, dict):
            raise ValueError("invalid proxy config")
        listen_host = str(item.get("listen_host", ""))
        listen_port = int(item.get("listen_port", 0))
        upstream_host = str(item.get("upstream_host", ""))
        upstream_port = int(item.get("upstream_port", 0))
        if listen_host not in {"127.0.0.1", "::1"}:
            raise ValueError("proxy listener must use loopback")
        if not upstream_host or not 1 <= listen_port <= 65535 or not 1 <= upstream_port <= 65535:
            raise ValueError("invalid proxy endpoint")
        listener = (listen_host, listen_port)
        if listen_port != 0 and listener in listeners:
            raise ValueError("duplicate listener")
        listeners.add(listener)
    return config


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    writer.close()
    with suppress(Exception):
        await writer.wait_closed()


class TcpFaultProxy:
    def __init__(self, name: str, config: dict[str, Any]):
        self.name = name
        self.listen_host = str(config["listen_host"])
        self.listen_port = int(config["listen_port"])
        self._upstream_host = str(config["upstream_host"])
        self._upstream_port = int(config["upstream_port"])
        self.mode = "normal"
        self.latency_ms = 0
        self._condition = asyncio.Condition()
        self._server: asyncio.Server | None = None
        self._sessions: dict[int, tuple[asyncio.StreamWriter, asyncio.StreamWriter | None]] = {}
        self._next_session = 1
        self.accepted = 0
        self.completed = 0
        self.fault_hits = 0
        self.resets = 0
        self.bytes_up = 0
        self.bytes_down = 0
        self.upstream_errors = 0

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("proxy already started")
        self._server = await asyncio.start_server(
            self._handle_client,
            self.listen_host,
            self.listen_port,
        )
        self.listen_port = int(self._server.sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        await self.reset_connections()

    async def configure(self, mode: str | None = None, latency_ms: int | None = None) -> None:
        if mode is not None and mode not in ALLOWED_MODES:
            raise ValueError("invalid fault mode")
        if latency_ms is not None and not 0 <= int(latency_ms) <= 60_000:
            raise ValueError("invalid latency")
        async with self._condition:
            if mode is not None:
                self.mode = mode
            if latency_ms is not None:
                self.latency_ms = int(latency_ms)
            self._condition.notify_all()

    async def reset_connections(self) -> int:
        sessions = list(self._sessions.items())
        for _, (client, upstream) in sessions:
            client.close()
            if upstream is not None:
                upstream.close()
        for _, (client, upstream) in sessions:
            with suppress(Exception):
                await client.wait_closed()
            if upstream is not None:
                with suppress(Exception):
                    await upstream.wait_closed()
        closed = len(sessions)
        self.resets += closed
        async with self._condition:
            self._condition.notify_all()
        return closed

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "listen_host": self.listen_host,
            "listen_port": self.listen_port,
            "mode": self.mode,
            "latency_ms": self.latency_ms,
            "active": len(self._sessions),
            "accepted": self.accepted,
            "completed": self.completed,
            "fault_hits": self.fault_hits,
            "resets": self.resets,
            "bytes_up": self.bytes_up,
            "bytes_down": self.bytes_down,
            "upstream_errors": self.upstream_errors,
        }

    async def _wait_while_blackholed(self) -> None:
        async with self._condition:
            while self.mode == "blackhole":
                await self._condition.wait()

    async def _pipe(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        direction: str,
    ) -> None:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            if self.latency_ms:
                await asyncio.sleep(self.latency_ms / 1000)
            writer.write(data)
            await writer.drain()
            if direction == "up":
                self.bytes_up += len(data)
            else:
                self.bytes_down += len(data)
        if writer.can_write_eof():
            with suppress(Exception):
                writer.write_eof()
                await writer.drain()

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        self.accepted += 1
        session_id = self._next_session
        self._next_session += 1
        self._sessions[session_id] = (client_writer, None)
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            if self.mode == "down":
                self.fault_hits += 1
                return
            if self.mode == "blackhole":
                self.fault_hits += 1
                await self._wait_while_blackholed()
                if self.mode == "down":
                    return
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(self._upstream_host, self._upstream_port),
                    timeout=10,
                )
            except Exception:
                self.upstream_errors += 1
                return
            self._sessions[session_id] = (client_writer, upstream_writer)
            await asyncio.gather(
                self._pipe(client_reader, upstream_writer, "up"),
                self._pipe(upstream_reader, client_writer, "down"),
                return_exceptions=True,
            )
        finally:
            await _close_writer(client_writer)
            await _close_writer(upstream_writer)
            self._sessions.pop(session_id, None)
            self.completed += 1


class ProxyController:
    def __init__(self, config: dict[str, Any]):
        validate_config(config)
        self.control_host = str(config["control_host"])
        self.control_port = int(config["control_port"])
        self._token = str(config["control_token"])
        self.proxies = {name: TcpFaultProxy(name, proxy_config) for name, proxy_config in config["proxies"].items()}
        self._control_server: asyncio.Server | None = None

    async def start(self) -> None:
        for proxy in self.proxies.values():
            await proxy.start()
        self._control_server = await asyncio.start_server(
            self._handle_control,
            self.control_host,
            self.control_port,
        )

    async def close(self) -> None:
        if self._control_server is not None:
            self._control_server.close()
            await self._control_server.wait_closed()
            self._control_server = None
        for proxy in self.proxies.values():
            await proxy.close()

    def status(self) -> dict[str, Any]:
        return {
            "status": "running",
            "proxies": {name: proxy.status() for name, proxy in sorted(self.proxies.items())},
        }

    async def _dispatch(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        if method == "GET" and path == "/status":
            return 200, self.status()
        if method == "POST" and path == "/reset-all":
            return 200, {"closed": {name: await proxy.reset_connections() for name, proxy in self.proxies.items()}}
        if method == "POST" and path.startswith("/proxies/"):
            name = path.removeprefix("/proxies/")
            proxy = self.proxies.get(name)
            if proxy is None:
                return 404, {"error": "unknown proxy"}
            allowed = {"mode", "latency_ms", "reset"}
            if set(body) - allowed:
                return 400, {"error": "unknown field"}
            try:
                await proxy.configure(
                    mode=body.get("mode"),
                    latency_ms=body.get("latency_ms"),
                )
                closed = await proxy.reset_connections() if body.get("reset") else 0
            except (TypeError, ValueError):
                return 400, {"error": "invalid configuration"}
            return 200, {"proxy": proxy.status(), "closed": closed}
        return 404, {"error": "not found"}

    async def _handle_control(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        status = 500
        payload: Any = {"error": "internal"}
        try:
            raw_headers = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            if len(raw_headers) > MAX_CONTROL_BODY:
                raise ValueError("headers too large")
            lines = raw_headers.decode("ascii", errors="strict").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if not line:
                    continue
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
            supplied = headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, self._token):
                status, payload = 401, {"error": "unauthorized"}
            else:
                content_length = int(headers.get("content-length", "0"))
                if not 0 <= content_length <= MAX_CONTROL_BODY:
                    raise ValueError("body too large")
                raw_body = await reader.readexactly(content_length) if content_length else b""
                body = json.loads(raw_body) if raw_body else {}
                if not isinstance(body, dict):
                    raise ValueError("body must be object")
                status, payload = await self._dispatch(method, path, body)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, UnicodeError, ValueError, json.JSONDecodeError):
            status, payload = 400, {"error": "bad request"}
        response_body = json.dumps(payload, sort_keys=True).encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found"}.get(status, "Error")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(response_body)}\r\nConnection: close\r\n\r\n".encode("ascii") + response_body)
        with suppress(Exception):
            await writer.drain()
        await _close_writer(writer)


async def run(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    controller = ProxyController(config)
    await controller.start()
    listeners = {name: {"host": proxy.listen_host, "port": proxy.listen_port} for name, proxy in sorted(controller.proxies.items())}
    print(json.dumps({"status": "ready", "listeners": listeners}, sort_keys=True), flush=True)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopped.set)
    await stopped.wait()
    await controller.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fresh batch TCP fault proxy")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
