import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_service_manager.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_service_manager", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_service_command_uses_current_source_and_group_specific_worker_id():
    module = load_module()

    api = module.service_command("control", "api")
    worker = module.service_command("experiment", "worker")

    assert api[-2:] == ["api/ragflow_server.py", "--init-superuser"]
    assert worker[-3:] == ["rag/svr/task_executor.py", "-i", "fresh_experiment_0"]
    assert api[0] == str(module.PROJECT_ROOT / ".venv" / "bin" / "python")


def test_proxy_command_uses_current_runtime_config():
    module = load_module()

    command = module.proxy_command()

    assert command == [
        str(module.PYTHON),
        str(module.EXECUTE_DIR / "fresh_fault_proxy.py"),
        "--config",
        str(module.RUNTIME_DIR / "private_proxy_config.json"),
    ]


def test_auth_protocol_command_uses_current_runtime_config():
    module = load_module()

    command = module.auth_protocol_command()

    assert command == [
        str(module.PYTHON),
        str(module.EXECUTE_DIR / "fresh_auth_protocol_stub.py"),
        "--config",
        str(module.RUNTIME_DIR / "auth_protocol" / "private_config.json"),
    ]


def test_process_registry_starts_tracks_and_stops_only_owned_process(tmp_path):
    module = load_module()
    registry = module.ProcessRegistry(tmp_path / "processes.json")
    log_path = tmp_path / "dummy.log"
    command = [
        sys.executable,
        "-c",
        "import time; print('dummy-ready', flush=True); time.sleep(30)",
    ]

    entry = registry.start("control:dummy", command, {}, log_path, tmp_path)
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and "dummy-ready" not in log_path.read_text():
            time.sleep(0.02)
        status = registry.status()["control:dummy"]
        assert status["alive"] is True
        assert status["identity_matches"] is True
        assert entry["pid"] > 1
        persisted = json.loads((tmp_path / "processes.json").read_text())
        assert "control:dummy" in persisted
    finally:
        stopped = registry.stop("control:dummy", timeout=3)
    assert stopped["stopped"] is True
    assert registry.status()["control:dummy"]["alive"] is False


def test_process_registry_refuses_to_signal_pid_when_start_identity_changed(tmp_path, monkeypatch):
    module = load_module()
    state_path = tmp_path / "processes.json"
    state_path.write_text(
        json.dumps(
            {
                "control:dummy": {
                    "pid": 1234,
                    "start_ticks": "old",
                    "command": ["dummy"],
                    "log_path": "dummy.log",
                }
            }
        )
    )
    registry = module.ProcessRegistry(state_path)
    monkeypatch.setattr(module, "process_start_ticks", lambda pid: "new")

    with pytest.raises(RuntimeError, match="identity mismatch"):
        registry.stop("control:dummy")
