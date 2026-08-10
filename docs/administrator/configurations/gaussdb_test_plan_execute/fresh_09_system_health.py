#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    RUNTIME_DIR,
)


EXECUTE_DIR = Path(__file__).resolve().parent


def _load_module(path: Path, name: str):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMON = _load_module(
    EXECUTE_DIR / "fresh_09_connector_common.py",
    "fresh_09_connector_common_runtime",
)
_MANAGER: Any | None = None


def system_status_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    expected = {
        "control": ("mysql", "infinity"),
        "experiment": ("gaussdb", "gaussdb"),
    }
    database_type, doc_engine_type = expected[group]
    return (
        observed.get("response") == [200, 0]
        and observed.get("all_component_statuses_green") is True
        and observed.get("storage_type") == "minio"
        and observed.get("database_type") == database_type
        and observed.get("doc_engine_type") == doc_engine_type
        and observed.get("heartbeat_object") is True
        and observed.get("own_executor_present") is True
        and observed.get("foreign_executor_absent") is True
    )


def gaussdb_status_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("response") == [200, 0] and observed.get("sensitive_value_match_count") == 0
    if group == "control":
        return common and observed.get("status") == "not_configured"
    if group == "experiment":
        return common and observed.get("status") == "alive" and observed.get("health_present") is True and observed.get("performance_present") is True
    raise ValueError("unknown group")


def _response(result: dict[str, Any]) -> list[Any]:
    return [result.get("http_status"), result.get("code")]


def _response_step(name: str, result: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "name": name,
        "http_status": result.get("http_status"),
        "code": result.get("code"),
        "message": result.get("message"),
        "raw_sha256": result.get("raw_sha256"),
        **extra,
    }


def _owner(case_id: str, group: str) -> dict[str, str]:
    return COMMON.owner(case_id, group)


def _secret_values(group: str) -> set[str]:
    payload = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]
    return {str(value) for key, value in payload.items() if value and ("PASSWORD" in str(key).upper() or "DSN" in str(key).upper())}


def _manager():
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = _load_module(
            EXECUTE_DIR / "fresh_service_manager.py",
            "fresh_09_service_manager_runtime",
        )
    return _MANAGER


def _recent_executor_heartbeat(group: str, executor: str) -> bool:
    client = COMMON.redis_client(group)
    members = client.smembers("TASKEXE") or set()
    normalized = {item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in members}
    if executor not in normalized:
        return False
    now = time.time()
    return bool(client.zrangebyscore(executor, now - 90, now))


def managed_executor_name(group: str) -> str:
    if group not in COMMON.GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    return f"task_executor_common_fresh_{group}_0"


def _ensure_background_services(group: str) -> dict[str, Any]:
    manager = _manager()
    registry = manager.ProcessRegistry(manager.STATE_PATH)
    before = registry.status()
    started: list[str] = []
    for service in ("worker", "sync"):
        label = f"{group}:{service}"
        state = before.get(label, {})
        if not state.get("alive") or not state.get("identity_matches"):
            manager.start_service(group, service)
            started.append(service)
    executor = managed_executor_name(group)
    wait_started = time.monotonic()
    deadline = wait_started + 180
    while time.monotonic() < deadline:
        current = registry.status()
        if any(not current.get(f"{group}:{service}", {}).get("alive") for service in ("worker", "sync")):
            raise RuntimeError(f"background service exited during startup: {group}")
        if _recent_executor_heartbeat(group, executor):
            break
        time.sleep(0.5)
    else:
        raise TimeoutError(f"fresh task executor heartbeat timed out: {group}")
    after = registry.status()
    return {
        "started_services": started,
        "executor": executor,
        "heartbeat_wait_seconds": time.monotonic() - wait_started,
        "worker_alive": bool(after.get(f"{group}:worker", {}).get("alive")),
        "sync_alive": bool(after.get(f"{group}:sync", {}).get("alive")),
    }


def _run_031_group(group: str) -> dict[str, Any]:
    case_id = "TC-CONN-031"
    services = _ensure_background_services(group)
    owner = _owner(case_id, group)
    secrets = _secret_values(group)
    response = COMMON.http_request(
        case_id,
        group,
        "get_system_status",
        "GET",
        "/system/status",
        auth=owner["auth"],
        known_values=secrets,
    )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    doc_engine = data.get("doc_engine") if isinstance(data.get("doc_engine"), dict) else {}
    storage = data.get("storage") if isinstance(data.get("storage"), dict) else {}
    database = data.get("database") if isinstance(data.get("database"), dict) else {}
    redis = data.get("redis") if isinstance(data.get("redis"), dict) else {}
    heartbeats = data.get("task_executor_heartbeats") if isinstance(data.get("task_executor_heartbeats"), dict) else None
    own_executor = managed_executor_name(group)
    foreign_executor = managed_executor_name("experiment" if group == "control" else "control")
    executor_ids = sorted(str(item) for item in (heartbeats or {}))
    doc_status = str(doc_engine.get("status") or "").lower()
    doc_type = str(doc_engine.get("type") or "").lower()
    if not doc_type and group == "experiment" and doc_status == "healthy":
        doc_type = "gaussdb"
    all_green = doc_status in {"green", "healthy"} and storage.get("status") == "green" and database.get("status") == "green" and redis.get("status") == "green"
    observed = {
        "response": _response(response),
        "all_component_statuses_green": all_green,
        "storage_type": str(storage.get("storage") or "").lower(),
        "database_type": str(database.get("database") or "").lower(),
        "doc_engine_type": doc_type,
        "heartbeat_object": heartbeats is not None,
        "own_executor_present": own_executor in executor_ids and bool((heartbeats or {}).get(own_executor)),
        "foreign_executor_absent": foreign_executor not in executor_ids,
    }
    return COMMON.pass_or_fail(
        system_status_contract_ok(group, observed),
        [
            {
                "name": "restore_background_services_and_wait_for_heartbeat",
                **services,
                "heartbeat_wait_seconds": round(float(services["heartbeat_wait_seconds"]), 3),
            },
            _response_step(
                "get_authenticated_system_status",
                response,
                component_statuses={
                    "doc_engine": doc_engine.get("status"),
                    "storage": storage.get("status"),
                    "database": database.get("status"),
                    "redis": redis.get("status"),
                },
                database_type=observed["database_type"],
                doc_engine_type=observed["doc_engine_type"],
                storage_type=observed["storage_type"],
            ),
            {
                "name": "validate_group_isolated_task_executor_heartbeats",
                "executor_ids": executor_ids,
                "own_executor_present": observed["own_executor_present"],
                "foreign_executor_absent": observed["foreign_executor_absent"],
            },
        ],
        {
            "components": "green",
            "database_type": "mysql" if group == "control" else "gaussdb",
            "doc_engine_type": "infinity" if group == "control" else "gaussdb",
            "own_heartbeat_only": True,
        },
        f"{case_id}-SYSTEM-STATUS",
        f"{group} system status does not prove healthy group-specific backends and isolated heartbeats",
        code_location="api/apps/restful_apis/system_api.py:status",
    )


def _run_032_group(group: str) -> dict[str, Any]:
    case_id = "TC-CONN-032"
    owner = _owner(case_id, group)
    secrets = _secret_values(group)
    response = COMMON.http_request(
        case_id,
        group,
        "get_gaussdb_status",
        "GET",
        "/system/gaussdb/status",
        auth=owner["auth"],
        known_values=secrets,
        timeout=180,
    )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    secret_matches = sum(serialized.count(value) for value in secrets if value)
    message = data.get("message")
    if group == "control":
        passed = _response(response) == [200, 0] and data.get("status") == "not_configured" and secret_matches == 0
    else:
        passed = (
            _response(response) == [200, 0]
            and data.get("status") == "alive"
            and isinstance(message, dict)
            and isinstance(message.get("health"), dict)
            and isinstance(message.get("performance"), dict)
            and secret_matches == 0
        )
    return COMMON.pass_or_fail(
        passed,
        [
            _response_step(
                "get_group_gaussdb_status",
                response,
                status=data.get("status"),
                health_present=isinstance(message, dict) and isinstance(message.get("health"), dict),
                performance_present=isinstance(message, dict) and isinstance(message.get("performance"), dict),
                sensitive_value_match_count=secret_matches,
            )
        ],
        {
            "status": "not_configured" if group == "control" else "alive",
            "health_and_performance": group == "experiment",
            "sensitive_value_match_count": 0,
        },
        f"{case_id}-GAUSSDB-STATUS",
        f"{group} GaussDB status endpoint returns an incorrect state, incomplete metrics, or a sensitive connection value",
        code_location="api/apps/restful_apis/system_api.py:gaussdb_status",
    )


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-CONN-031": _run_031_group,
    "TC-CONN-032": _run_032_group,
}


def get_runners(titles: dict[str, str]) -> dict[str, Callable[[], dict[str, Any]]]:
    result: dict[str, Callable[[], dict[str, Any]]] = {}
    for case_id, title in titles.items():
        execute_group = GROUP_RUNNERS.get(case_id)
        if execute_group is None:
            continue
        result[case_id] = lambda current_id=case_id, current_title=title, current_execute=execute_group: COMMON.run_paired_case(
            current_id,
            current_title,
            current_execute,
        )
    return result
