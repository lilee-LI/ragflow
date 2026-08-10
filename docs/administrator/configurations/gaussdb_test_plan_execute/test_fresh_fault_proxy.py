import asyncio
import importlib.util
import json
from contextlib import suppress
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_fault_proxy.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_fault_proxy", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def start_echo_server():
    connections = 0

    async def echo(reader, writer):
        nonlocal connections
        connections += 1
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port, lambda: connections


@pytest.mark.asyncio
async def test_proxy_forwards_bytes_and_reports_redacted_status():
    module = load_module()
    upstream, upstream_port, connection_count = await start_echo_server()
    proxy = module.TcpFaultProxy(
        "case_proxy",
        {
            "listen_host": "127.0.0.1",
            "listen_port": 0,
            "upstream_host": "127.0.0.1",
            "upstream_port": upstream_port,
        },
    )
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.listen_port)
        writer.write(b"fresh-evidence")
        await writer.drain()
        assert await reader.readexactly(14) == b"fresh-evidence"
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0)

        status = proxy.status()
        assert status["name"] == "case_proxy"
        assert status["accepted"] == 1
        assert status["bytes_up"] == 14
        assert status["bytes_down"] == 14
        assert "upstream_host" not in json.dumps(status)
        assert connection_count() == 1
    finally:
        await proxy.close()
        upstream.close()
        await upstream.wait_closed()


@pytest.mark.asyncio
async def test_down_mode_closes_without_contacting_upstream_then_recovers():
    module = load_module()
    upstream, upstream_port, connection_count = await start_echo_server()
    proxy = module.TcpFaultProxy(
        "case_proxy",
        {
            "listen_host": "127.0.0.1",
            "listen_port": 0,
            "upstream_host": "127.0.0.1",
            "upstream_port": upstream_port,
        },
    )
    await proxy.start()
    try:
        await proxy.configure(mode="down")
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.listen_port)
        writer.write(b"must-not-arrive")
        await writer.drain()
        try:
            closed_payload = await asyncio.wait_for(reader.read(), 1)
        except ConnectionResetError:
            closed_payload = b""
        assert closed_payload == b""
        writer.close()
        with suppress(ConnectionResetError):
            await writer.wait_closed()
        assert connection_count() == 0
        assert proxy.status()["fault_hits"] == 1

        await proxy.configure(mode="normal")
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.listen_port)
        writer.write(b"recovered")
        await writer.drain()
        assert await reader.readexactly(9) == b"recovered"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        upstream.close()
        await upstream.wait_closed()


@pytest.mark.asyncio
async def test_blackhole_waits_until_cleared_and_reset_closes_active_connection():
    module = load_module()
    upstream, upstream_port, _ = await start_echo_server()
    proxy = module.TcpFaultProxy(
        "case_proxy",
        {
            "listen_host": "127.0.0.1",
            "listen_port": 0,
            "upstream_host": "127.0.0.1",
            "upstream_port": upstream_port,
        },
    )
    await proxy.start()
    try:
        await proxy.configure(mode="blackhole")
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.listen_port)
        writer.write(b"held")
        await writer.drain()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(reader.read(1), 0.05)

        await proxy.configure(mode="normal")
        assert await asyncio.wait_for(reader.readexactly(4), 1) == b"held"
        assert proxy.status()["active"] == 1
        closed = await proxy.reset_connections()
        assert closed == 1
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
        assert proxy.status()["resets"] == 1
    finally:
        await proxy.close()
        upstream.close()
        await upstream.wait_closed()


def test_validate_config_rejects_duplicate_listeners_and_non_loopback_control():
    module = load_module()
    base = {
        "control_host": "0.0.0.0",
        "control_port": 19990,
        "control_token": "x" * 32,
        "proxies": {
            "a": {
                "listen_host": "127.0.0.1",
                "listen_port": 12345,
                "upstream_host": "one",
                "upstream_port": 1,
            },
            "b": {
                "listen_host": "127.0.0.1",
                "listen_port": 12345,
                "upstream_host": "two",
                "upstream_port": 2,
            },
        },
    }
    with pytest.raises(ValueError, match="loopback"):
        module.validate_config(base)
    base["control_host"] = "127.0.0.1"
    with pytest.raises(ValueError, match="duplicate listener"):
        module.validate_config(base)
