#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    RUNTIME_DIR,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
STATE_PATH = RUNTIME_DIR / "processes.json"
PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
GROUPS = {"control", "experiment"}
SERVICES = {"api", "admin", "worker", "sync"}
PROXY_LABEL = "fault_proxy"
AUTH_PROTOCOL_LABEL = "auth_protocol"
_CHILDREN: dict[int, subprocess.Popen] = {}


def service_command(group: str, service: str) -> list[str]:
    if group not in GROUPS:
        raise ValueError("unknown group")
    if service not in SERVICES:
        raise ValueError("unknown service")
    commands = {
        "api": [str(PYTHON), "api/ragflow_server.py", "--init-superuser"],
        "admin": [str(PYTHON), str(EXECUTE_DIR / "fresh_admin_server.py")],
        "worker": [
            str(PYTHON),
            "rag/svr/task_executor.py",
            "-i",
            f"fresh_{group}_0",
        ],
        "sync": [str(PYTHON), "rag/svr/sync_data_source.py"],
    }
    return commands[service]


def proxy_command() -> list[str]:
    return [
        str(PYTHON),
        str(EXECUTE_DIR / "fresh_fault_proxy.py"),
        "--config",
        str(RUNTIME_DIR / "private_proxy_config.json"),
    ]


def auth_protocol_command() -> list[str]:
    return [
        str(PYTHON),
        str(EXECUTE_DIR / "fresh_auth_protocol_stub.py"),
        "--config",
        str(RUNTIME_DIR / "auth_protocol" / "private_config.json"),
    ]


def process_start_ticks(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    closing = raw.rfind(")")
    if closing < 0:
        return None
    fields_after_command = raw[closing + 2 :].split()
    return fields_after_command[19] if len(fields_after_command) > 19 else None


def process_state(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    closing = raw.rfind(")")
    if closing < 0:
        return None
    fields_after_command = raw[closing + 2 :].split()
    return fields_after_command[0] if fields_after_command else None


def process_alive(pid: int) -> bool:
    if pid <= 1 or process_start_ticks(pid) is None or process_state(pid) == "Z":
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class ProcessRegistry:
    def __init__(self, state_path: Path):
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.state_path.exists():
            return {}
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("invalid process state")
        return payload

    def _save(self, state: dict[str, dict[str, Any]]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.state_path)
        self.state_path.chmod(0o600)

    @staticmethod
    def _identity_matches(entry: dict[str, Any]) -> bool:
        current = process_start_ticks(int(entry["pid"]))
        return current is not None and current == str(entry["start_ticks"])

    def start(
        self,
        label: str,
        command: list[str],
        environment: dict[str, str],
        log_path: Path,
        cwd: Path,
    ) -> dict[str, Any]:
        state = self._load()
        existing = state.get(label)
        if existing and self._identity_matches(existing) and process_alive(int(existing["pid"])):
            raise RuntimeError(f"process already running: {label}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab", buffering=0) as log_stream:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        _CHILDREN[process.pid] = process
        ticks = process_start_ticks(process.pid)
        if ticks is None:
            raise RuntimeError(f"process exited before registration: {label}")
        entry = {
            "pid": process.pid,
            "start_ticks": ticks,
            "command": command,
            "log_path": str(log_path),
            "started_at_unix": time.time(),
        }
        state[label] = entry
        self._save(state)
        return entry

    def stop(self, label: str, timeout: float = 15) -> dict[str, Any]:
        state = self._load()
        entry = state.get(label)
        if entry is None:
            return {"label": label, "stopped": False, "reason": "not registered"}
        pid = int(entry["pid"])
        current_ticks = process_start_ticks(pid)
        if current_ticks is None:
            return {"label": label, "stopped": True, "reason": "already exited"}
        if current_ticks != str(entry["start_ticks"]):
            raise RuntimeError(f"process identity mismatch: {label}")
        if not process_alive(pid):
            handle = _CHILDREN.pop(pid, None)
            if handle is not None:
                handle.wait(timeout=1)
            return {"label": label, "stopped": True, "reason": "already exited"}
        os.killpg(pid, signal.SIGTERM)
        handle = _CHILDREN.get(pid)
        if handle is not None:
            try:
                handle.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                pass
        else:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and process_alive(pid):
                time.sleep(0.05)
        if process_alive(pid):
            if not self._identity_matches(entry):
                raise RuntimeError(f"process identity changed while stopping: {label}")
            os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and process_alive(pid):
                time.sleep(0.05)
        handle = _CHILDREN.pop(pid, None)
        if handle is not None and handle.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                handle.wait(timeout=1)
        return {"label": label, "stopped": not process_alive(pid)}

    def status(self) -> dict[str, dict[str, Any]]:
        result = {}
        for label, entry in sorted(self._load().items()):
            pid = int(entry["pid"])
            alive = process_alive(pid)
            identity_matches = alive and self._identity_matches(entry)
            result[label] = {
                "pid": pid,
                "alive": alive,
                "identity_matches": identity_matches,
                "log_path": entry["log_path"],
                "started_at_unix": entry.get("started_at_unix"),
            }
        return result


def load_group_environment(group: str) -> dict[str, str]:
    if group not in GROUPS:
        raise ValueError("unknown group")
    private_path = RUNTIME_DIR / "private_environments.json"
    payload = json.loads(private_path.read_text(encoding="utf-8"))
    private = payload[group]
    environment = os.environ.copy()
    for key in (
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ):
        environment.pop(key, None)
    environment.update({str(key): str(value) for key, value in private.items()})
    if group == "control":
        environment["INFINITY_POOL_MAX_SIZE"] = "1"
    environment["NLTK_DATA"] = str(PROJECT_ROOT / "nltk_data")
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def managed_log_path(group: str, service: str) -> Path:
    return RUNTIME_DIR / group / "logs" / f"managed_{service}.log"


def start_service(group: str, service: str) -> dict[str, Any]:
    registry = ProcessRegistry(STATE_PATH)
    label = f"{group}:{service}"
    entry = registry.start(
        label,
        service_command(group, service),
        load_group_environment(group),
        managed_log_path(group, service),
        PROJECT_ROOT,
    )
    time.sleep(0.25)
    status = registry.status()[label]
    if not status["alive"] or not status["identity_matches"]:
        raise RuntimeError(f"service exited during startup: {label}")
    return {
        "label": label,
        "pid": entry["pid"],
        "alive": True,
        "log_path": str(managed_log_path(group, service)),
    }


def start_proxy() -> dict[str, Any]:
    registry = ProcessRegistry(STATE_PATH)
    entry = registry.start(
        PROXY_LABEL,
        proxy_command(),
        os.environ.copy(),
        RUNTIME_DIR / "proxy.log",
        PROJECT_ROOT,
    )
    time.sleep(0.25)
    status = registry.status()[PROXY_LABEL]
    if not status["alive"] or not status["identity_matches"]:
        raise RuntimeError("fault proxy exited during startup")
    return {
        "label": PROXY_LABEL,
        "pid": entry["pid"],
        "alive": True,
        "log_path": str(RUNTIME_DIR / "proxy.log"),
    }


def start_auth_protocol() -> dict[str, Any]:
    registry = ProcessRegistry(STATE_PATH)
    log_path = RUNTIME_DIR / "auth_protocol" / "managed_stub.log"
    entry = registry.start(
        AUTH_PROTOCOL_LABEL,
        auth_protocol_command(),
        os.environ.copy(),
        log_path,
        PROJECT_ROOT,
    )
    time.sleep(0.25)
    status = registry.status()[AUTH_PROTOCOL_LABEL]
    if not status["alive"] or not status["identity_matches"]:
        raise RuntimeError("auth protocol stub exited during startup")
    return {
        "label": AUTH_PROTOCOL_LABEL,
        "pid": entry["pid"],
        "alive": True,
        "log_path": str(log_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage fresh RAGFlow test services")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--group", choices=sorted(GROUPS), required=True)
    start_parser.add_argument("--service", choices=sorted(SERVICES), required=True)
    stop_parser = subparsers.add_parser("stop")
    stop_parser.add_argument("--group", choices=sorted(GROUPS), required=True)
    stop_parser.add_argument("--service", choices=sorted(SERVICES), required=True)
    subparsers.add_parser("proxy-start")
    subparsers.add_parser("proxy-stop")
    subparsers.add_parser("auth-protocol-start")
    subparsers.add_parser("auth-protocol-stop")
    subparsers.add_parser("status")
    args = parser.parse_args()
    registry = ProcessRegistry(STATE_PATH)
    if args.command == "start":
        result = start_service(args.group, args.service)
    elif args.command == "stop":
        result = registry.stop(f"{args.group}:{args.service}")
    elif args.command == "proxy-start":
        result = start_proxy()
    elif args.command == "proxy-stop":
        result = registry.stop(PROXY_LABEL)
    elif args.command == "auth-protocol-start":
        result = start_auth_protocol()
    elif args.command == "auth-protocol-stop":
        result = registry.stop(AUTH_PROTOCOL_LABEL)
    else:
        result = registry.status()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
