#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import evidence_dir

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = evidence_dir("06_memory_metadata")
RAW_DIR = EVIDENCE_DIR / "raw"
PLAN_FILES = (
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "06_memory_metadata.md",
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "06_memory_supplement.md",
)


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BASE = _load_module("fresh_05_chat_session_agent.py", "fresh_06_chat_base")
DD = BASE.DD
AUTH = BASE.AUTH
DB = BASE.DB
for module in (BASE, DD, AUTH, DB):
    module.EVIDENCE_DIR = EVIDENCE_DIR
    module.RAW_DIR = RAW_DIR


def _case_titles() -> dict[str, str]:
    pattern = re.compile(r"^### (TC-MM(?:-SUP)?-\d{3}):\s*(.+)$", re.MULTILINE)
    result: dict[str, str] = {}
    for path in PLAN_FILES:
        for case_id, title in pattern.findall(path.read_text(encoding="utf-8")):
            if case_id in result:
                raise ValueError(f"duplicate case id: {case_id}")
            result[case_id] = title.strip()
    if len(result) != 47:
        raise ValueError(f"expected 47 memory metadata cases, found {len(result)}")
    return result


CASE_TITLES = _case_titles()


def normal_create_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_present") is True
        and observed.get("database_count") == 1
        and observed.get("name_matches") is True
        and observed.get("tenant_matches") is True
        and observed.get("memory_type") == 1
        and observed.get("embd_matches") is True
        and observed.get("llm_matches") is True
        and observed.get("permissions") == "me"
        and observed.get("memory_size") == 5 * 1024 * 1024
        and observed.get("forgetting_policy") == "FIFO"
        and observed.get("temperature") == 0.5
        and observed.get("storage_type") == "table"
        and observed.get("message_store_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def rejection_contract_ok(observed: dict[str, Any], *, request_count: int) -> bool:
    responses = observed.get("responses", [])
    return (
        len(responses) == request_count
        and all(item.get("http_status") == 200 and isinstance(item.get("code"), int) and item.get("code") != 0 for item in responses)
        and observed.get("database_delta") == 0
        and observed.get("cleanup_succeeded") is True
    )


def group_result_shape_ok(result: dict[str, Any]) -> bool:
    return (
        result.get("status") in {"PASS", "FAIL", "BLOCKED"}
        and isinstance(result.get("steps"), list)
        and bool(result.get("steps"))
        and all(isinstance(step, dict) and step.get("name") for step in result["steps"])
        and isinstance(result.get("oracle"), dict)
    )


def empty_model_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("api_embd_id") == ""
        and observed.get("api_llm_id") == ""
        and observed.get("orm_embd_id") == ""
        and observed.get("orm_llm_id") == ""
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return (
            common
            and observed.get("physical_embd_is_empty") is True
            and observed.get("physical_llm_is_empty") is True
            and observed.get("physical_embd_is_null") is False
            and observed.get("physical_llm_is_null") is False
        )
    if group == "experiment":
        return (
            common
            and observed.get("physical_embd_is_empty") is False
            and observed.get("physical_llm_is_empty") is False
            and observed.get("physical_embd_is_null") is True
            and observed.get("physical_llm_is_null") is True
        )
    raise ValueError("unknown group")


def memory_size_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("one") == {"code": 0, "stored": 1}
        and observed.get("zero") == {"code": 0, "stored": 1}
        and observed.get("negative", {}).get("code") != 0
        and observed.get("negative", {}).get("stored") == 1
        and observed.get("numeric_string") == {"code": 0, "stored": 100}
        and observed.get("cleanup_succeeded") is True
    )


def temperature_update_contract_ok(observed: dict[str, Any]) -> bool:
    accepted = observed.get("accepted", [])
    rejected = observed.get("rejected", [])
    return (
        accepted
        == [
            {"code": 0, "stored": 0.5},
            {"code": 0, "stored": 0.0},
            {"code": 0, "stored": 1.0},
        ]
        and len(rejected) == 2
        and all(item.get("code") != 0 and item.get("stored") == 1.0 for item in rejected)
        and observed.get("cleanup_succeeded") is True
    )


def nonempty_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("message_readable") is True
        and observed.get("size_cache_positive") is True
        and observed.get("embd_update_code") not in {None, 0}
        and observed.get("type_update_code") not in {None, 0}
        and observed.get("embd_unchanged") is True
        and observed.get("type_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def internal_model_update_contract_ok(observed: dict[str, Any]) -> bool:
    explicit_rejection = observed.get("http_status") == 200 and observed.get("code") not in {None, 0} and observed.get("fields_changed") is False and observed.get("update_time_changed") is False
    real_update = observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("fields_changed") is True and observed.get("update_time_changed") is True
    return (explicit_rejection or real_update) and observed.get("cleanup_succeeded") is True


def ordinary_empty_text_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("database_values") == ["", "", "", ""] and observed.get("api_values") == ["", "", "", ""]
    if group == "experiment":
        return common and observed.get("database_values") == [None, None, None, None] and observed.get("api_values") == [None, None, None, None]
    raise ValueError("unknown group")


def message_forget_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("before_exists") is True
        and observed.get("after_exists") is True
        and observed.get("forget_at_changed") is True
        and observed.get("identity_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def message_denial_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 404
        and observed.get("message_exists_before") is True
        and observed.get("message_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def message_collision_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_inserted") is True
        and observed.get("physical_pair_count") == 2
        and observed.get("physical_ids_exact") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("api_returns_a_only") is True
        and observed.get("b_still_exact") is True
        and observed.get("fixture_cleanup_succeeded") is True
        and observed.get("memory_cleanup_succeeded") is True
    )


def denied_message_add_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("denied_http_status") == 200
        and observed.get("denied_code") == 500
        and observed.get("denied_message_exact") is True
        and observed.get("denied_store_delta") == 0
        and observed.get("denied_cache_delta") == 0
        and observed.get("denied_task_delta") == 0
        and observed.get("owner_code") == 0
        and observed.get("owner_message_readable") is True
        and observed.get("owner_exact_raw_count") == 1
        and observed.get("cleanup_succeeded") is True
    )


def pagination_type_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("legal") == [200, 0] and observed.get("invalid") == [200, 101] and observed.get("metadata_delta") == 0


def pagination_bounds_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("boundary") == [200, 0] and observed.get("invalid") == [[200, 101], [200, 101], [200, 101]] and observed.get("metadata_delta") == 0


def _evidence_module():
    return _load_module("fresh_case_evidence.py", "fresh_06_evidence")


def _request(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    return BASE._request(
        case_id,
        group,
        label,
        auth,
        method,
        path,
        payload=payload,
        params=params,
        timeout=timeout,
    )


def _unauthenticated_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = DD.requests.request(
        method,
        f"{DB._api_base(group)}{path}",
        json=payload,
        timeout=45,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    _evidence_module().write_evidence(
        raw_path,
        _evidence_module().sanitize(
            {
                "request": {
                    "method": method,
                    "path": path,
                    "authorization_present": False,
                    "json": payload,
                },
                "response": {"http_status": response.status_code, "body": body},
            }
        ),
    )
    return {
        "http_status": response.status_code,
        "code": body.get("code") if isinstance(body, dict) else None,
        "message": body.get("message") if isinstance(body, dict) else None,
        "data": body.get("data") if isinstance(body, dict) else None,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _owner(case_id: str, group: str) -> dict[str, str]:
    return DD._ensure_owner(case_id, group)


def _model_ids(group: str, tenant_id: str) -> dict[str, str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT embd_id,llm_id FROM tenant WHERE id=%s", (tenant_id,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None or not row[0] or not row[1]:
        raise RuntimeError(f"configured memory models missing for {group}")
    return {"embd_id": str(row[0]), "llm_id": str(row[1])}


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _memory_snapshot(group: str, memory_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,name,avatar,tenant_id,memory_type,storage_type,embd_id,"
                "tenant_embd_id,llm_id,tenant_llm_id,permissions,description,"
                "memory_size,forgetting_policy,temperature,system_prompt,user_prompt,"
                "create_time,update_time,LENGTH(embd_id),LENGTH(llm_id) "
                "FROM memory WHERE id=%s",
                (memory_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    return {
        "count": 1,
        "id": str(row[0]),
        "name": str(row[1]),
        "avatar": row[2],
        "tenant_id": str(row[3]),
        "memory_type": int(row[4]),
        "storage_type": str(row[5]),
        "embd_id": "" if row[6] is None else str(row[6]),
        "tenant_embd_id": None if row[7] is None else str(row[7]),
        "llm_id": "" if row[8] is None else str(row[8]),
        "tenant_llm_id": None if row[9] is None else str(row[9]),
        "permissions": str(row[10]),
        "description": row[11],
        "memory_size": int(row[12]),
        "forgetting_policy": str(row[13]),
        "temperature": float(row[14]),
        "system_prompt": row[15],
        "user_prompt": row[16],
        "create_time": int(row[17]),
        "update_time": int(row[18]),
        "physical_embd_is_null": row[6] is None,
        "physical_llm_is_null": row[8] is None,
        "physical_embd_is_empty": row[6] == "" and row[19] == 0,
        "physical_llm_is_empty": row[8] == "" and row[20] == 0,
    }


def _memory_ids_by_prefix(group: str, tenant_id: str, prefix: str) -> list[str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM memory WHERE tenant_id=%s AND LOWER(name) LIKE %s ORDER BY create_time,id",
                (tenant_id, prefix.lower() + "%"),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _tenant_memory_count(group: str, tenant_id: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM memory WHERE tenant_id=%s", (tenant_id,))
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _accessible_memory_rows(group: str, user_id: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tenant_id FROM user_tenant WHERE user_id=%s AND status=%s",
                (user_id, "1"),
            )
            joined = {user_id, *[str(row[0]) for row in cursor.fetchall()]}
            placeholders = ",".join(["%s"] * len(joined))
            cursor.execute(
                f"SELECT id,name,tenant_id,memory_type,permissions,update_time FROM memory WHERE tenant_id IN ({placeholders}) AND (tenant_id=%s OR permissions=%s) ORDER BY update_time DESC,id",
                (*sorted(joined), user_id, "team"),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "name": str(row[1]),
            "tenant_id": str(row[2]),
            "memory_type": int(row[3]),
            "permissions": str(row[4]),
            "update_time": int(row[5]),
        }
        for row in rows
    ]


def _memory_table_count(group: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM memory")
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _message_store_snapshot(group: str, tenant_id: str, memory_id: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", "fresh_06_store_manager")
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService, index_name
tenant_id, memory_id = sys.argv[1:3]
exists = bool(MessageService.has_index(tenant_id, memory_id))
total = 0
if exists:
    result = MessageService.list_message(tenant_id, memory_id, page=1, page_size=100)
    total = int(result.get("total_count") or 0)
pool = getattr(settings.msgStoreConn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({"index_exists": exists, "raw_message_count": total}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, tenant_id, memory_id],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        return {
            "index_exists": False,
            "raw_message_count": -1,
            "probe_error": "message_store_probe_failed",
            "probe_stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
        }
    marker = next(
        (line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__")),
        None,
    )
    if marker is None:
        return {
            "index_exists": False,
            "raw_message_count": -1,
            "probe_error": "message_store_probe_marker_missing",
            "probe_stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        }
    result = json.loads(marker)
    return {
        "index_exists": bool(result.get("index_exists")),
        "raw_message_count": int(result.get("raw_message_count") or 0),
    }


def _create_memory(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "POST", "/memories", payload=payload)


def _update_memory(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    memory_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "PUT", f"/memories/{memory_id}", payload=payload)


def _get_memory_config(case_id: str, group: str, auth: str, label: str, memory_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", f"/memories/{memory_id}/config")


def _delete_memory(case_id: str, group: str, auth: str, label: str, memory_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "DELETE", f"/memories/{memory_id}")


def _list_memories(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", "/memories", params=params)


def _add_message(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    memory_id: str,
    *,
    marker: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/messages",
        payload={
            "memory_id": [memory_id],
            "agent_id": f"{marker}-agent",
            "session_id": f"{marker}-session",
            "user_input": f"{marker} user input",
            "agent_response": f"{marker} agent response",
        },
        timeout=180,
    )


def _wait_memory_messages(
    case_id: str,
    group: str,
    auth: str,
    memory_id: str,
    *,
    label_prefix: str,
    timeout: float = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempts = 0
    last: dict[str, Any] = {"http_status": None, "code": None, "data": None}
    while time.monotonic() < deadline:
        attempts += 1
        last = _request(
            case_id,
            group,
            f"{label_prefix}_{attempts:03d}",
            auth,
            "GET",
            f"/memories/{memory_id}",
        )
        data = last["data"] if isinstance(last.get("data"), dict) else {}
        messages = data.get("messages") if isinstance(data.get("messages"), dict) else {}
        rows = messages.get("message_list") if isinstance(messages.get("message_list"), list) else []
        if last.get("code") == 0 and rows:
            return {"response": last, "messages": rows, "attempts": attempts}
        time.sleep(0.25)
    return {"response": last, "messages": [], "attempts": attempts}


def _api_message_snapshot(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    response = _request(case_id, group, label, auth, "GET", f"/memories/{memory_id}")
    data = response["data"] if isinstance(response.get("data"), dict) else {}
    messages = data.get("messages") if isinstance(data.get("messages"), dict) else {}
    rows = messages.get("message_list") if isinstance(messages.get("message_list"), list) else []
    row = next(
        (item for item in rows if isinstance(item, dict) and int(item.get("message_id") or 0) == message_id),
        {},
    )
    fields = (
        "message_id",
        "message_type",
        "source_id",
        "memory_id",
        "user_id",
        "agent_id",
        "session_id",
        "valid_at",
        "invalid_at",
        "forget_at",
        "status",
    )
    return {
        "http_status": response["http_status"],
        "code": response["code"],
        "exists": bool(row),
        **{field: row.get(field) for field in fields},
        "raw_sha256": response["raw_sha256"],
    }


def _delete_message(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/messages/{memory_id}:{message_id}",
    )


def _get_message_content(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/messages/{memory_id}:{message_id}/content",
    )


def _run_store_probe(
    case_id: str,
    group: str,
    label: str,
    script: str,
    arguments: list[str],
    *,
    timeout: float = 60,
    max_attempts: int = 2,
) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", f"fresh_06_probe_manager_{label}")
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    for stale in RAW_DIR.glob(f"{case_id}_{group}_{label}_timeout_*.json"):
        stale.unlink()
    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, max_attempts + 1):
        command = [str(manager.PYTHON), "-c", script, *arguments]
        process = subprocess.Popen(
            command,
            cwd=manager.PROJECT_ROOT,
            env=manager.load_group_environment(group),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                tail_stdout, tail_stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                tail_stdout, tail_stderr = process.communicate()

            def output_text(value: Any) -> str:
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value or "")

            partial_stdout = output_text(error.stdout) + output_text(tail_stdout)
            partial_stderr = output_text(error.stderr) + output_text(tail_stderr)
            timeout_path = RAW_DIR / f"{case_id}_{group}_{label}_timeout_{attempt}.json"
            _evidence_module().write_evidence(
                timeout_path,
                {
                    "probe": {
                        "group": group,
                        "label": label,
                        "attempt": attempt,
                        "timed_out": True,
                        "timeout_seconds": timeout,
                        "partial_stdout_line_count": len(partial_stdout.splitlines()),
                        "partial_stderr_line_count": len(partial_stderr.splitlines()),
                    }
                },
            )
            if attempt == max_attempts:
                raise RuntimeError(f"store probe timed out: {case_id}/{group}/{label}")
            time.sleep(0.5)
            continue
        if process.returncode != 0:
            error_path = RAW_DIR / f"{case_id}_{group}_{label}_error_{attempt}.json"
            _evidence_module().write_evidence(
                error_path,
                {
                    "probe": {
                        "group": group,
                        "label": label,
                        "attempt": attempt,
                        "subprocess_exit_code": process.returncode,
                        "stdout_line_count": len(stdout.splitlines()),
                        "stderr_line_count": len(stderr.splitlines()),
                    }
                },
            )
            if attempt == max_attempts:
                raise RuntimeError(f"store probe exited nonzero: {case_id}/{group}/{label}")
            time.sleep(0.5)
            continue
        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        break
    assert completed is not None
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    result = json.loads(marker)
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    _evidence_module().write_evidence(
        raw_path,
        _evidence_module().sanitize(
            {
                "probe": {
                    "group": group,
                    "label": label,
                    "subprocess_exit_code": completed.returncode,
                },
                "result": result,
            }
        ),
    )
    return {**result, "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest()}


def _message_document_snapshot(
    case_id: str,
    group: str,
    label: str,
    tenant_id: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    script = r"""
import hashlib, json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService
tenant_id, memory_id, message_id = sys.argv[1], sys.argv[2], int(sys.argv[3])
if MessageService.has_index(tenant_id, memory_id):
    MessageService.list_message(tenant_id, memory_id, page=1, page_size=100)
doc = MessageService.get_by_message_id(memory_id, message_id, tenant_id) or {}
content = str(doc.get("content") or "")
result = {
    "backend": type(settings.msgStoreConn).__name__,
    "exists": bool(doc),
    "id": str(doc.get("id") or ""),
    "message_id": int(doc.get("message_id")) if doc.get("message_id") is not None else None,
    "memory_id": str(doc.get("memory_id") or ""),
    "agent_id": str(doc.get("agent_id") or ""),
    "session_id": str(doc.get("session_id") or ""),
    "forget_at": None if doc.get("forget_at") is None else str(doc.get("forget_at")),
    "status": bool(doc.get("status")) if doc else None,
    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if doc else None,
}
pool = getattr(settings.msgStoreConn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps(result, sort_keys=True))
"""
    return _run_store_probe(
        case_id,
        group,
        label,
        script,
        [tenant_id, memory_id, str(message_id)],
    )


def _message_filter_snapshot(
    case_id: str,
    group: str,
    label: str,
    tenant_id: str,
    memory_id: str,
    agent_id: str,
    session_id: str,
) -> dict[str, Any]:
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService
tenant_id, memory_id, agent_id, session_id = sys.argv[1:5]
index_exists = bool(MessageService.has_index(tenant_id, memory_id))
listed = (
    MessageService.list_message(
        tenant_id, memory_id, agent_ids=[agent_id], keywords=session_id, page=1, page_size=100
    )
    if index_exists
    else {"message_list": [], "total_count": 0}
)
rows = [
    row for row in listed.get("message_list", [])
    if str(row.get("agent_id") or "") == agent_id
    and str(row.get("session_id") or "") == session_id
]
result = {
    "backend": type(settings.msgStoreConn).__name__,
    "index_exists": index_exists,
    "exact_raw_count": len(rows),
    "message_ids": sorted(int(row["message_id"]) for row in rows),
}
pool = getattr(settings.msgStoreConn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps(result, sort_keys=True))
"""
    return _run_store_probe(
        case_id,
        group,
        label,
        script,
        [tenant_id, memory_id, agent_id, session_id],
    )


def _collision_adapter_action(
    case_id: str,
    group: str,
    label: str,
    *,
    action: str,
    tenant_id: str,
    memory_a_id: str,
    memory_b_id: str,
    source_a_id: int,
    source_b_id: int,
    content_a: str,
    content_b: str,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "tenant_id": tenant_id,
        "memory_a_id": memory_a_id,
        "memory_b_id": memory_b_id,
        "source_a_id": source_a_id,
        "source_b_id": source_b_id,
        "content_a": content_a,
        "content_b": content_b,
    }
    script = r"""
import hashlib, json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService, index_name
p = json.loads(sys.argv[1])
tenant_id = p["tenant_id"]
memory_a_id = p["memory_a_id"]
memory_b_id = p["memory_b_id"]
fixture_message_id = 123

for target_memory_id in (memory_a_id, memory_b_id):
    if MessageService.has_index(tenant_id, target_memory_id):
        MessageService.list_message(
            tenant_id, target_memory_id, page=1, page_size=100
        )

def get_doc(memory_id, message_id):
    return MessageService.get_by_message_id(memory_id, int(message_id), tenant_id) or {}

def summary(doc):
    content = str(doc.get("content") or "")
    return {
        "exists": bool(doc),
        "id": str(doc.get("id") or ""),
        "message_id": int(doc.get("message_id")) if doc.get("message_id") is not None else None,
        "memory_id": str(doc.get("memory_id") or ""),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if doc else None,
    }

insert_errors = []
source_vectors_present = None
if p["action"] == "insert":
    source_a = get_doc(memory_a_id, p["source_a_id"])
    source_b = get_doc(memory_b_id, p["source_b_id"])
    source_vectors_present = bool(source_a.get("content_embed")) and bool(source_b.get("content_embed"))
    for memory_id, source, content, suffix in (
        (memory_a_id, source_a, p["content_a"], "a"),
        (memory_b_id, source_b, p["content_b"], "b"),
    ):
        message = {
            "message_id": fixture_message_id,
            "message_type": "raw",
            "source_id": 0,
            "memory_id": memory_id,
            "user_id": str(source.get("user_id") or ""),
            "agent_id": f"fresh-mm-sup-020-collision-{suffix}",
            "session_id": f"fresh-mm-sup-020-collision-{suffix}",
            "content": content,
            "content_embed": list(source.get("content_embed") or []),
            "valid_at": source.get("valid_at"),
            "invalid_at": None,
            "forget_at": None,
            "status": True,
        }
        insert_errors.extend(MessageService.insert_message([message], tenant_id, memory_id) or [])
elif p["action"] == "cleanup":
    MessageService.delete_message({"message_id": fixture_message_id}, tenant_id, memory_a_id)
    MessageService.delete_message({"message_id": fixture_message_id}, tenant_id, memory_b_id)
elif p["action"] != "probe":
    raise ValueError("unsupported collision adapter action")

a = summary(get_doc(memory_a_id, fixture_message_id))
b = summary(get_doc(memory_b_id, fixture_message_id))
backend = type(settings.msgStoreConn).__name__
if "Infinity" in backend:
    raw_ids = []
    raw_conn = settings.msgStoreConn.connPool.get_conn()
    try:
        raw_db = raw_conn.get_database(settings.msgStoreConn.dbName)
        for target_memory_id in (memory_a_id, memory_b_id):
            table = raw_db.get_table(f"{index_name(tenant_id)}_{target_memory_id}")
            frame, _ = table.output(["id"]).filter(
                f"message_id = {fixture_message_id}"
            ).to_df()
            raw_ids.extend(str(value) for value in frame["id"].tolist())
    finally:
        settings.msgStoreConn.connPool.release_conn(raw_conn)
    physical_ids_exact = set(raw_ids) == {
        f"{memory_a_id}_{fixture_message_id}",
        f"{memory_b_id}_{fixture_message_id}",
    }
else:
    physical_ids_exact = (
        a["id"] == f"{memory_a_id}_{fixture_message_id}"
        and b["id"] == f"{memory_b_id}_{fixture_message_id}"
    )
result = {
    "action": p["action"],
    "backend": backend,
    "source_vectors_present": source_vectors_present,
    "insert_error_count": len(insert_errors),
    "physical_pair_count": int(a["exists"]) + int(b["exists"]),
    "physical_ids_exact": physical_ids_exact,
    "contents_exact": a["content_sha256"] == hashlib.sha256(p["content_a"].encode("utf-8")).hexdigest() and b["content_sha256"] == hashlib.sha256(p["content_b"].encode("utf-8")).hexdigest(),
    "a": a,
    "b": b,
}
pool = getattr(settings.msgStoreConn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps(result, sort_keys=True))
"""
    return _run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, ensure_ascii=False, sort_keys=True)],
        timeout=90,
        max_attempts=2,
    )


def _memory_task_count(group: str, memory_id: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM task WHERE doc_id=%s AND task_type=%s",
                (memory_id, "memory"),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _memory_size_cache(group: str, memory_id: str) -> dict[str, Any]:
    client = BASE._redis_client(group)
    key = f"memory_{memory_id}"
    value = client.get(key)
    return {
        "exists": value is not None,
        "value": int(value) if value is not None else None,
        "key_fingerprint": DB._fingerprint(key),
    }


def _cleanup_memory_ids(case_id: str, group: str, auth: str, memory_ids: list[str]) -> bool:
    ok = True
    for index, memory_id in enumerate(dict.fromkeys(item for item in memory_ids if item), 1):
        if _memory_snapshot(group, memory_id).get("count") == 1:
            response = _delete_memory(case_id, group, auth, f"cleanup_memory_{index}", memory_id)
            ok = response.get("code") in {0, 404} and ok
    return ok and all(_memory_snapshot(group, memory_id).get("count") == 0 for memory_id in memory_ids if memory_id)


def _cleanup_prefix(case_id: str, group: str, auth: str, tenant_id: str, prefix: str) -> bool:
    ids = _memory_ids_by_prefix(group, tenant_id, prefix)
    return _cleanup_memory_ids(case_id, group, auth, ids) and not _memory_ids_by_prefix(group, tenant_id, prefix)


def _prepare_tagged_secondary_user(
    case_id: str,
    group: str,
    tag: str,
    email: str,
    password: str,
) -> dict[str, Any]:
    preclean = True
    if DB._email_count(group, [email]):
        preclean = _cleanup_tagged_secondary_user(case_id, group, f"preclean_{tag}", email)["succeeded"]
    registration = DB._register(
        case_id,
        group,
        f"register_secondary_{tag}",
        {"email": email, "nickname": f"FreshMM{tag}", "password": password},
    )
    login = AUTH._login(case_id, group, f"login_secondary_{tag}", email, password)
    return {
        "preclean": preclean,
        "registration": registration,
        "login": login,
        "auth": str(login.get("_auth") or ""),
        "tenant_id": DD._owner_id(group, email) if DB._email_count(group, [email]) else "",
    }


def _cleanup_tagged_secondary_user(case_id: str, group: str, tag: str, email: str) -> dict[str, Any]:
    if not DB._email_count(group, [email]):
        return {"succeeded": True, "disabled_code": None, "deleted_code": None}
    encoded = DD.requests.utils.quote(email, safe="")
    disabled = _tagged_admin_action(
        case_id,
        group,
        f"disable_secondary_{tag}",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "off"},
    )
    deleted = _tagged_admin_action(
        case_id,
        group,
        f"delete_secondary_{tag}",
        "DELETE",
        f"/users/{encoded}",
    )
    return {
        "succeeded": disabled["code"] == 0 and deleted["code"] == 0 and DB._email_count(group, [email]) == 0,
        "disabled_code": disabled["code"],
        "deleted_code": deleted["code"],
        "raw_sha256": [disabled["raw_sha256"], deleted["raw_sha256"]],
    }


def _tagged_admin_action(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    max_attempts: int = 3,
) -> dict[str, Any]:
    for stale in RAW_DIR.glob(f"{case_id}_{group}_{label}_retry_*.json"):
        stale.unlink()
    last: dict[str, Any] = {
        "http_status": None,
        "code": None,
        "message": None,
        "data": None,
        "raw_sha256": None,
        "attempts": 0,
    }
    for attempt in range(1, max_attempts + 1):
        session = None
        try:
            session, base = DB._open_admin_session(group)
            response = session.request(method, f"{base}{path}", json=payload, timeout=90)
            attempt_label = label if attempt == 1 else f"{label}_retry_{attempt}"
            raw_sha = DB._record_http(
                case_id,
                group,
                attempt_label,
                {"method": method, "path": path, "json": payload},
                response,
            )
            try:
                body = response.json()
            except ValueError:
                body = {}
            last = {
                "http_status": response.status_code,
                "code": body.get("code") if isinstance(body, dict) else None,
                "message": body.get("message") if isinstance(body, dict) else None,
                "data": body.get("data") if isinstance(body, dict) else None,
                "raw_sha256": raw_sha,
                "attempts": attempt,
            }
            if response.status_code == 200 and last["code"] == 0:
                return last
        except RuntimeError as error:
            last = {
                **last,
                "message": type(error).__name__,
                "attempts": attempt,
            }
        finally:
            if session is not None:
                session.close()
        if attempt < max_attempts:
            time.sleep(0.5 * attempt)
    return last


def _models_payload(name: str, models: dict[str, str], memory_type: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "memory_type": ["raw"] if memory_type is None else memory_type,
        "embd_id": models["embd_id"],
        "llm_id": models["llm_id"],
    }


def _create_memory_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    label: str,
    name: str,
    *,
    memory_type: Any = None,
) -> dict[str, Any]:
    models = _model_ids(group, owner["tenant_id"])
    response = _create_memory(
        case_id,
        group,
        owner["auth"],
        label,
        _models_payload(name, models, memory_type),
    )
    data = response["data"] if isinstance(response["data"], dict) else {}
    memory_id = str(data.get("id") or "")
    return {
        "response": response,
        "data": data,
        "id": memory_id,
        "snapshot": _memory_snapshot(group, memory_id) if memory_id else {"count": 0},
        "models": models,
    }


def _finalize(recorder, case_id: str) -> dict[str, Any]:
    result = recorder.finalize()
    _evidence_module().write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def _run_case(case_id: str, execute: Callable[[str, dict[str, str]], dict[str, Any]]) -> dict[str, Any]:
    recorder = _evidence_module().CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        result = execute(group, _owner(case_id, group))
        if not group_result_shape_ok(result):
            raise ValueError(f"invalid group result for {case_id}/{group}")
        recorder.add_group(
            group,
            result["status"],
            result["steps"],
            oracle=result["oracle"],
            findings=result.get("findings", []),
        )
    return _finalize(recorder, case_id)


def run_mm001() -> dict[str, Any]:
    case_id = "TC-MM-001"
    prefix = "fresh-mm-001"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        created = _create_memory(
            case_id,
            group,
            owner["auth"],
            "create_normal_memory",
            _models_payload(prefix, models),
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        memory_id = str(data.get("id") or "")
        snapshot = _memory_snapshot(group, memory_id) if memory_id else {"count": 0}
        store = _message_store_snapshot(group, owner["tenant_id"], memory_id) if memory_id else {"index_exists": False, "raw_message_count": 0}
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [memory_id])
        observed = {
            "http_status": created["http_status"],
            "code": created["code"],
            "id_present": bool(memory_id),
            "database_count": snapshot.get("count"),
            "name_matches": snapshot.get("name") == prefix,
            "tenant_matches": snapshot.get("tenant_id") == owner["tenant_id"],
            "memory_type": snapshot.get("memory_type"),
            "embd_matches": snapshot.get("embd_id") == models["embd_id"],
            "llm_matches": snapshot.get("llm_id") == models["llm_id"],
            "permissions": snapshot.get("permissions"),
            "memory_size": snapshot.get("memory_size"),
            "forgetting_policy": snapshot.get("forgetting_policy"),
            "temperature": snapshot.get("temperature"),
            "storage_type": snapshot.get("storage_type"),
            "message_store_absent": not store["index_exists"] and store["raw_message_count"] == 0,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = normal_create_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_memory_with_current_configured_models",
                    "http_status": created["http_status"],
                    "code": created["code"],
                    "memory_id_fingerprint": DB._fingerprint(memory_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "read_only_metadata_defaults_verification",
                    "database_count": snapshot.get("count"),
                    "memory_type": snapshot.get("memory_type"),
                    "defaults_match": all(
                        [
                            observed["permissions"] == "me",
                            observed["memory_size"] == 5 * 1024 * 1024,
                            observed["forgetting_policy"] == "FIFO",
                            observed["temperature"] == 0.5,
                            observed["storage_type"] == "table",
                        ]
                    ),
                },
                {
                    "name": "read_only_message_store_absence_verification",
                    **store,
                },
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "response": [200, 0],
                "memory_type": 1,
                "defaults": {
                    "permissions": "me",
                    "memory_size": 5 * 1024 * 1024,
                    "forgetting_policy": "FIFO",
                    "temperature": 0.5,
                    "storage_type": "table",
                },
                "message_store_created": False,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-NORMAL-CREATE-001",
                    "summary": f"{group} normal Memory create/default contract did not pass",
                    "code_location": "api/apps/services/memory_api_service.py:create_memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm002() -> dict[str, Any]:
    case_id = "TC-MM-002"
    prefix = "fresh-mm-002"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        created = _create_memory(
            case_id,
            group,
            owner["auth"],
            "create_multi_type_memory",
            _models_payload(prefix, models, ["raw", "semantic", "episodic"]),
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        memory_id = str(data.get("id") or "")
        snapshot = _memory_snapshot(group, memory_id) if memory_id else {"count": 0}
        store = _message_store_snapshot(group, owner["tenant_id"], memory_id) if memory_id else {"index_exists": False, "raw_message_count": 0}
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [memory_id])
        passed = (
            preclean
            and created["http_status"] == 200
            and created["code"] == 0
            and snapshot.get("memory_type") == 7
            and data.get("memory_type") == ["raw", "semantic", "episodic"]
            and not store["index_exists"]
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_raw_semantic_episodic_memory",
                    "http_status": created["http_status"],
                    "code": created["code"],
                    "api_type_order_matches": data.get("memory_type") == ["raw", "semantic", "episodic"],
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "read_only_bit_flag_and_store_verification",
                    "physical_memory_type": snapshot.get("memory_type"),
                    "message_store_index_exists": store["index_exists"],
                },
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"memory_type_bit_value": 7, "api_order": ["raw", "semantic", "episodic"]},
        }

    return _run_case(case_id, execute)


def run_mm003() -> dict[str, Any]:
    case_id = "TC-MM-003"
    prefix = "fresh-mm-003"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        payload = _models_payload(prefix, models)
        first = _create_memory(case_id, group, owner["auth"], "create_original_memory", payload)
        second = _create_memory(case_id, group, owner["auth"], "create_duplicate_name_memory", payload)
        first_data = first["data"] if isinstance(first["data"], dict) else {}
        second_data = second["data"] if isinstance(second["data"], dict) else {}
        ids = [str(first_data.get("id") or ""), str(second_data.get("id") or "")]
        snapshots = [_memory_snapshot(group, item) for item in ids if item]
        names = [item.get("name") for item in snapshots]
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], ids)
        passed = preclean and first["code"] == 0 and second["code"] == 0 and len(set(ids)) == 2 and names == [prefix, f"{prefix}(1)"] and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_same_memory_name_twice",
                    "codes": [first["code"], second["code"]],
                    "ids_distinct": len(set(ids)) == 2,
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {
                    "name": "read_only_duplicate_name_verification",
                    "database_count": len(snapshots),
                    "names_match": names == [prefix, f"{prefix}(1)"],
                },
                {"name": "cleanup_both_memories_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"names": [prefix, f"{prefix}(1)"], "overwrite": False},
        }

    return _run_case(case_id, execute)


def run_mm004() -> dict[str, Any]:
    case_id = "TC-MM-004"
    prefix = "fresh-mm-004"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        before = _tenant_memory_count(group, owner["tenant_id"])
        payloads = [
            {"memory_type": ["raw"], **models},
            {"name": f"{prefix}-no-type", **models},
            {"name": f"{prefix}-no-embd", "memory_type": ["raw"], "llm_id": models["llm_id"]},
            {"name": f"{prefix}-no-llm", "memory_type": ["raw"], "embd_id": models["embd_id"]},
        ]
        responses = [_create_memory(case_id, group, owner["auth"], f"missing_required_field_{index}", payload) for index, payload in enumerate(payloads, 1)]
        after = _tenant_memory_count(group, owner["tenant_id"])
        cleanup = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        observed = {
            "responses": [{"http_status": item["http_status"], "code": item["code"]} for item in responses],
            "database_delta": after - before,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = rejection_contract_ok(observed, request_count=4)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "submit_four_missing_required_field_requests",
                    "responses": observed["responses"],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_zero_metadata_write_verification",
                    "database_delta": observed["database_delta"],
                },
                {"name": "cleanup_unexpected_rows_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"all_code_nonzero": True, "metadata_delta": 0},
        }

    return _run_case(case_id, execute)


def run_mm005() -> dict[str, Any]:
    case_id = "TC-MM-005"
    prefix = "fresh-mm-005"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        variants = [
            ("invalid", ["invalid_type"]),
            ("empty", []),
            ("string", "raw"),
            ("duplicate", ["raw", "raw"]),
        ]
        responses: list[dict[str, Any]] = []
        snapshots: dict[str, dict[str, Any]] = {}
        ids: list[str] = []
        for label, memory_type in variants:
            response = _create_memory(
                case_id,
                group,
                owner["auth"],
                f"create_{label}_memory_type",
                _models_payload(f"{prefix}-{label}", models, memory_type),
            )
            data = response["data"] if isinstance(response["data"], dict) else {}
            memory_id = str(data.get("id") or "")
            ids.append(memory_id)
            snapshots[label] = _memory_snapshot(group, memory_id) if memory_id else {"count": 0}
            responses.append(response)
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], ids)
        invalid_ok = responses[0]["code"] != 0 and snapshots["invalid"].get("count") == 0
        empty_ok = responses[1]["code"] != 0 and snapshots["empty"].get("count") == 0
        string_ok = responses[2]["code"] != 0 and snapshots["string"].get("count") == 0
        duplicate_ok = responses[3]["code"] == 0 and snapshots["duplicate"].get("memory_type") == 1
        passed = preclean and invalid_ok and empty_ok and string_ok and duplicate_ok and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "submit_invalid_empty_string_and_duplicate_type_variants",
                    "http_statuses": [item["http_status"] for item in responses],
                    "codes": [item["code"] for item in responses],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_per_variant_storage_verification",
                    "invalid_rejected": invalid_ok,
                    "empty_rejected": empty_ok,
                    "string_rejected": string_ok,
                    "duplicate_normalized_to_one": duplicate_ok,
                },
                {"name": "cleanup_created_variant_memories_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "invalid": "reject",
                "empty": "reject",
                "non_list": "reject",
                "duplicate": "accept as bit value 1",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-EMPTY-TYPE-VALIDATION-001",
                    "summary": f"{group} Memory create accepted an empty memory_type or another invalid variant",
                    "code_location": "api/apps/services/memory_api_service.py:create_memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm006() -> dict[str, Any]:
    case_id = "TC-MM-006"
    prefix = "fresh-mm-006"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        payloads = [
            {
                "name": f"{prefix}-bad-embd",
                "memory_type": ["raw"],
                "embd_id": "nonexistent-embedding@fresh.invalid",
                "llm_id": models["llm_id"],
            },
            {
                "name": f"{prefix}-bad-llm",
                "memory_type": ["raw"],
                "embd_id": models["embd_id"],
                "llm_id": "nonexistent-chat@fresh.invalid",
            },
        ]
        before = _tenant_memory_count(group, owner["tenant_id"])
        responses = [_create_memory(case_id, group, owner["auth"], f"create_nonexistent_model_{index}", payload) for index, payload in enumerate(payloads, 1)]
        ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in responses]
        after = _tenant_memory_count(group, owner["tenant_id"])
        snapshots = [_memory_snapshot(group, item) for item in ids if item]
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], ids)
        observed = {
            "responses": [{"http_status": item["http_status"], "code": item["code"]} for item in responses],
            "database_delta": after - before,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = rejection_contract_ok(observed, request_count=2)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "submit_nonexistent_embedding_and_chat_model_ids",
                    "responses": observed["responses"],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_model_id_write_verification",
                    "database_delta": observed["database_delta"],
                    "stored_row_count": len(snapshots),
                },
                {"name": "cleanup_unexpected_memories_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"both_rejected": True, "metadata_delta": 0},
            "findings": []
            if passed
            else [
                {
                    "id": "MM-MODEL-VALIDATION-001",
                    "summary": f"{group} Memory create accepted nonexistent model identifiers",
                    "code_location": "api/apps/services/memory_api_service.py:create_memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm007() -> dict[str, Any]:
    case_id = "TC-MM-007"
    prefix = "fresh-mm-007"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        special_name = f"{prefix}-test'; DROP TABLE memory;--"
        chinese_name = f"{prefix}-测试记忆"
        names = ["", "   ", prefix + "A" * 200, special_name, chinese_name]
        responses = [
            _create_memory(
                case_id,
                group,
                owner["auth"],
                f"create_name_boundary_{index}",
                _models_payload(name, models),
            )
            for index, name in enumerate(names, 1)
        ]
        ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in responses]
        snapshots = [_memory_snapshot(group, item) if item else {"count": 0} for item in ids]
        table_count = _memory_table_count(group)
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], ids)
        passed = (
            preclean
            and all(item["http_status"] == 200 and item["code"] != 0 for item in responses[:3])
            and responses[3]["code"] == 0
            and responses[4]["code"] == 0
            and snapshots[3].get("name") == special_name
            and snapshots[4].get("name") == chinese_name
            and table_count > 0
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "submit_empty_whitespace_overlong_special_and_chinese_names",
                    "codes": [item["code"] for item in responses],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_exact_name_and_table_integrity_verification",
                    "special_name_exact": snapshots[3].get("name") == special_name,
                    "chinese_name_exact": snapshots[4].get("name") == chinese_name,
                    "memory_table_nonempty": table_count > 0,
                },
                {"name": "cleanup_successful_boundary_memories_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "reject_first_three": True,
                "accept_special_and_chinese_exactly": True,
                "parameterized_sql_intact": True,
            },
        }

    return _run_case(case_id, execute)


def run_mm008() -> dict[str, Any]:
    case_id = "TC-MM-008"
    prefix = "fresh-mm-008"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        requested_permissions = ["me", "team", "public"]
        responses = []
        ids = []
        snapshots = []
        for index, permission in enumerate(requested_permissions, 1):
            payload = _models_payload(f"{prefix}-{index}", models)
            payload["permissions"] = permission
            response = _create_memory(case_id, group, owner["auth"], f"create_with_permission_{permission}", payload)
            data = response["data"] if isinstance(response["data"], dict) else {}
            memory_id = str(data.get("id") or "")
            responses.append(response)
            ids.append(memory_id)
            snapshots.append(_memory_snapshot(group, memory_id) if memory_id else {"count": 0})
        target_id = ids[0]
        team_update = _update_memory(
            case_id,
            group,
            owner["auth"],
            "update_permission_to_team",
            target_id,
            {"permissions": "team"},
        )
        after_team = _memory_snapshot(group, target_id)
        invalid_update = _update_memory(
            case_id,
            group,
            owner["auth"],
            "reject_invalid_permission_update",
            target_id,
            {"permissions": "public"},
        )
        after_invalid = _memory_snapshot(group, target_id)
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], ids)
        creation_ignored = all(item["code"] == 0 for item in responses) and all(item.get("permissions") == "me" for item in snapshots)
        passed = (
            preclean
            and creation_ignored
            and team_update["http_status"] == 200
            and team_update["code"] == 0
            and after_team.get("permissions") == "team"
            and invalid_update["http_status"] == 200
            and invalid_update["code"] != 0
            and after_invalid.get("permissions") == "team"
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_memories_with_me_team_and_invalid_permissions",
                    "codes": [item["code"] for item in responses],
                    "creation_permissions_all_default_me": creation_ignored,
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "update_permission_to_team_then_reject_public",
                    "team_response": [team_update["http_status"], team_update["code"]],
                    "invalid_response": [invalid_update["http_status"], invalid_update["code"]],
                    "team_persisted_after_rejection": after_invalid.get("permissions") == "team",
                    "raw_sha256": [team_update["raw_sha256"], invalid_update["raw_sha256"]],
                },
                {"name": "cleanup_permission_memories_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "create_permissions_ignored_to": "me",
                "valid_update": "team",
                "invalid_update": "reject without mutation",
            },
        }

    return _run_case(case_id, execute)


def run_mm009() -> dict[str, Any]:
    case_id = "TC-MM-009"
    prefix = "fresh-mm-009"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixtures = [
            _create_memory_fixture(
                case_id,
                group,
                owner,
                f"create_list_fixture_{index}",
                f"{prefix}-{index}",
                memory_type=memory_type,
            )
            for index, memory_type in enumerate((["raw"], ["raw", "semantic"], ["episodic"]), 1)
        ]
        listed = _list_memories(case_id, group, owner["auth"], "list_all_accessible_memories")
        data = listed["data"] if isinstance(listed["data"], dict) else {}
        api_rows = data.get("memory_list") if isinstance(data.get("memory_list"), list) else []
        db_rows = _accessible_memory_rows(group, owner["tenant_id"])
        api_ids = [str(item.get("id")) for item in api_rows if isinstance(item, dict)]
        db_ids = [item["id"] for item in db_rows]
        fixture_ids = [item["id"] for item in fixtures]
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], fixture_ids)
        passed = (
            preclean
            and all(item["response"]["code"] == 0 for item in fixtures)
            and listed["http_status"] == 200
            and listed["code"] == 0
            and set(fixture_ids).issubset(api_ids)
            and set(api_ids) == set(db_ids)
            and data.get("total_count") == len(db_ids)
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_memory_list_fixtures",
                    "codes": [item["response"]["code"] for item in fixtures],
                    "fixture_id_count": len(set(fixture_ids)),
                },
                {
                    "name": "list_all_accessible_memories_and_compare_database",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "api_count": len(api_ids),
                    "database_count": len(db_ids),
                    "id_sets_equal": set(api_ids) == set(db_ids),
                    "fixtures_present": set(fixture_ids).issubset(api_ids),
                    "raw_sha256": listed["raw_sha256"],
                },
                {"name": "cleanup_list_fixtures_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"api_matches_accessible_database_rows": True},
        }

    return _run_case(case_id, execute)


def run_mm010() -> dict[str, Any]:
    case_id = "TC-MM-010"
    prefix = "fresh-mm-010"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixtures = [
            _create_memory_fixture(
                case_id,
                group,
                owner,
                f"create_filter_fixture_{index}",
                f"{prefix}-{index}",
                memory_type=memory_type,
            )
            for index, memory_type in enumerate((["raw"], ["raw", "semantic"], ["episodic"]), 1)
        ]
        raw = _list_memories(
            case_id,
            group,
            owner["auth"],
            "filter_by_raw",
            {"memory_type": "raw", "keywords": prefix},
        )
        union = _list_memories(
            case_id,
            group,
            owner["auth"],
            "filter_by_raw_or_semantic",
            {"memory_type": "raw,semantic", "keywords": prefix},
        )
        invalid = _list_memories(
            case_id,
            group,
            owner["auth"],
            "filter_by_invalid_type",
            {"memory_type": "invalid", "keywords": prefix},
        )

        def ids(response: dict[str, Any]) -> set[str]:
            data = response["data"] if isinstance(response["data"], dict) else {}
            rows = data.get("memory_list") if isinstance(data.get("memory_list"), list) else []
            return {str(item.get("id")) for item in rows if isinstance(item, dict)}

        fixture_ids = [item["id"] for item in fixtures]
        expected = set(fixture_ids[:2])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], fixture_ids)
        passed = (
            preclean
            and all(item["response"]["code"] == 0 for item in fixtures)
            and raw["code"] == union["code"] == invalid["code"] == 0
            and ids(raw) == expected
            and ids(union) == expected
            and ids(invalid) == set()
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_raw_combined_and_episodic_filter_fixtures",
                    "codes": [item["response"]["code"] for item in fixtures],
                    "physical_types": [item["snapshot"].get("memory_type") for item in fixtures],
                },
                {
                    "name": "filter_single_multi_and_invalid_memory_types",
                    "codes": [raw["code"], union["code"], invalid["code"]],
                    "raw_ids_exact": ids(raw) == expected,
                    "union_ids_exact": ids(union) == expected,
                    "invalid_empty": ids(invalid) == set(),
                    "raw_sha256": [raw["raw_sha256"], union["raw_sha256"], invalid["raw_sha256"]],
                },
                {"name": "cleanup_filter_fixtures_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"raw_bit": 1, "raw_or_semantic_bit": 3, "invalid_filter": []},
        }

    return _run_case(case_id, execute)


def run_mm011() -> dict[str, Any]:
    case_id = "TC-MM-011"
    prefix = "fresh-mm-011"
    keyword = "needle"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        names = [f"{prefix}-{keyword}-{index}" if index <= 3 else f"{prefix}-noise-{index}" for index in range(1, 6)]
        fixtures = [_create_memory_fixture(case_id, group, owner, f"create_pagination_fixture_{index}", name) for index, name in enumerate(names, 1)]
        page1 = _list_memories(
            case_id,
            group,
            owner["auth"],
            "keyword_page_one",
            {"keywords": keyword, "page": 1, "page_size": 2},
        )
        page2 = _list_memories(
            case_id,
            group,
            owner["auth"],
            "keyword_page_two",
            {"keywords": keyword, "page": 2, "page_size": 2},
        )
        count_before_invalid = _tenant_memory_count(group, owner["tenant_id"])
        lower = _list_memories(
            case_id,
            group,
            owner["auth"],
            "invalid_page_and_negative_page_size",
            {"keywords": prefix, "page": 0, "page_size": -1},
        )
        upper = _list_memories(
            case_id,
            group,
            owner["auth"],
            "oversized_page_size",
            {"keywords": prefix, "page": 1, "page_size": 9999},
        )
        count_after_invalid = _tenant_memory_count(group, owner["tenant_id"])

        def rows(response: dict[str, Any]) -> list[dict[str, Any]]:
            data = response["data"] if isinstance(response["data"], dict) else {}
            value = data.get("memory_list")
            return value if isinstance(value, list) else []

        page1_data = page1["data"] if isinstance(page1["data"], dict) else {}
        page2_data = page2["data"] if isinstance(page2["data"], dict) else {}
        legal_ok = (
            page1["code"] == page2["code"] == 0
            and len(rows(page1)) == 2
            and len(rows(page2)) == 1
            and page1_data.get("total_count") == page2_data.get("total_count") == 3
            and not ({str(item.get("id")) for item in rows(page1)} & {str(item.get("id")) for item in rows(page2)})
            and all(keyword in str(item.get("name")) for item in [*rows(page1), *rows(page2)])
        )
        invalid_ok = lower["code"] == 101 and upper["code"] == 101
        fixture_ids = [item["id"] for item in fixtures]
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], fixture_ids)
        passed = preclean and all(item["response"]["code"] == 0 for item in fixtures) and legal_ok and invalid_ok and count_before_invalid == count_after_invalid and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_five_keyword_and_noise_memories",
                    "create_codes": [item["response"]["code"] for item in fixtures],
                },
                {
                    "name": "verify_legal_keyword_pagination",
                    "page_counts": [len(rows(page1)), len(rows(page2))],
                    "total_counts": [page1_data.get("total_count"), page2_data.get("total_count")],
                    "legal_contract_passed": legal_ok,
                    "raw_sha256": [page1["raw_sha256"], page2["raw_sha256"]],
                },
                {
                    "name": "exercise_invalid_lower_and_upper_pagination",
                    "lower_response": [lower["http_status"], lower["code"]],
                    "upper_response": [upper["http_status"], upper["code"]],
                    "metadata_count_unchanged": count_before_invalid == count_after_invalid,
                    "raw_sha256": [lower["raw_sha256"], upper["raw_sha256"]],
                },
                {"name": "cleanup_pagination_fixtures_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "legal_page_sizes": [2, 1],
                "keyword_total": 3,
                "invalid_page_code": 101,
                "oversized_page_code": 101,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-PAGINATION-VALIDATION-001",
                    "summary": f"{group} Memory list pagination validation did not use the parameter-error contract",
                    "code_location": "api/apps/restful_apis/memory_api.py:list_memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm012() -> dict[str, Any]:
    case_id = "TC-MM-012"
    prefix = "fresh-mm-012"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_update_fixture", prefix)
        updated_name = f"{prefix}-updated"
        updated_description = "Updated description"
        updated = _update_memory(
            case_id,
            group,
            owner["auth"],
            "update_name_and_description",
            fixture["id"],
            {"name": updated_name, "description": updated_description},
        )
        data = updated["data"] if isinstance(updated["data"], dict) else {}
        snapshot = _memory_snapshot(group, fixture["id"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        passed = (
            preclean
            and fixture["response"]["code"] == 0
            and updated["http_status"] == 200
            and updated["code"] == 0
            and data.get("name") == snapshot.get("name") == updated_name
            and data.get("description") == snapshot.get("description") == updated_description
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_memory_update_fixture", "code": fixture["response"]["code"]},
                {
                    "name": "update_name_and_description_through_api",
                    "http_status": updated["http_status"],
                    "code": updated["code"],
                    "raw_sha256": updated["raw_sha256"],
                },
                {
                    "name": "read_only_updated_fields_verification",
                    "name_matches": snapshot.get("name") == updated_name,
                    "description_matches": snapshot.get("description") == updated_description,
                },
                {"name": "cleanup_updated_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"name": updated_name, "description": updated_description},
        }

    return _run_case(case_id, execute)


def run_mm013() -> dict[str, Any]:
    case_id = "TC-MM-013"
    prefix = "fresh-mm-013"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_size_fixture", prefix)
        observations: dict[str, dict[str, Any]] = {}
        variants = [("one", 1), ("zero", 0), ("negative", -1), ("numeric_string", "100")]
        raw_hashes = []
        for label, value in variants:
            response = _update_memory(
                case_id,
                group,
                owner["auth"],
                f"update_memory_size_{label}",
                fixture["id"],
                {"memory_size": value},
            )
            snapshot = _memory_snapshot(group, fixture["id"])
            observations[label] = {"code": response["code"], "stored": snapshot.get("memory_size")}
            raw_hashes.append(response["raw_sha256"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        observed = {**observations, "cleanup_succeeded": preclean and cleanup}
        passed = fixture["response"]["code"] == 0 and memory_size_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_memory_size_fixture", "code": fixture["response"]["code"]},
                {
                    "name": "update_memory_size_one_zero_negative_and_numeric_string",
                    "observations": observations,
                    "raw_sha256": raw_hashes,
                },
                {"name": "cleanup_memory_size_fixture_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "one": "accept 1",
                "zero": "success no-op",
                "negative": "reject",
                "numeric_string": "accept 100",
            },
        }

    return _run_case(case_id, execute)


def run_mm014() -> dict[str, Any]:
    case_id = "TC-MM-014"
    prefix = "fresh-mm-014"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_temperature_fixture", prefix)
        accepted = []
        rejected = []
        raw_hashes = []
        for value in (0.5, 0, 1):
            response = _update_memory(
                case_id,
                group,
                owner["auth"],
                f"accept_temperature_{str(value).replace('.', '_')}",
                fixture["id"],
                {"temperature": value},
            )
            snapshot = _memory_snapshot(group, fixture["id"])
            accepted.append({"code": response["code"], "stored": snapshot.get("temperature")})
            raw_hashes.append(response["raw_sha256"])
        for value in (-0.5, 2.0):
            response = _update_memory(
                case_id,
                group,
                owner["auth"],
                f"reject_temperature_{str(value).replace('.', '_').replace('-', 'negative_')}",
                fixture["id"],
                {"temperature": value},
            )
            snapshot = _memory_snapshot(group, fixture["id"])
            rejected.append({"code": response["code"], "stored": snapshot.get("temperature")})
            raw_hashes.append(response["raw_sha256"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        observed = {"accepted": accepted, "rejected": rejected, "cleanup_succeeded": preclean and cleanup}
        passed = fixture["response"]["code"] == 0 and temperature_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_temperature_fixture", "code": fixture["response"]["code"]},
                {
                    "name": "accept_boundaries_and_reject_out_of_range_temperatures",
                    "accepted": accepted,
                    "rejected": rejected,
                    "raw_sha256": raw_hashes,
                },
                {"name": "cleanup_temperature_fixture_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"accepted": [0.0, 0.5, 1.0], "rejected": [-0.5, 2.0]},
        }

    return _run_case(case_id, execute)


def run_mm015() -> dict[str, Any]:
    case_id = "TC-MM-015"
    prefix = "fresh-mm-015"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_nonempty_update_fixture", prefix)
        before = fixture["snapshot"]
        added = _add_message(
            case_id,
            group,
            owner["auth"],
            "add_raw_message_before_restricted_updates",
            fixture["id"],
            marker=prefix,
        )
        readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            fixture["id"],
            label_prefix="poll_raw_message_before_restricted_updates",
        )
        cache = _memory_size_cache(group, fixture["id"])
        embd_update = _update_memory(
            case_id,
            group,
            owner["auth"],
            "reject_embedding_update_on_nonempty_memory",
            fixture["id"],
            {"embd_id": "fresh-different-embedding@invalid"},
        )
        after_embd = _memory_snapshot(group, fixture["id"])
        type_update = _update_memory(
            case_id,
            group,
            owner["auth"],
            "reject_type_update_on_nonempty_memory",
            fixture["id"],
            {"memory_type": ["raw", "semantic"]},
        )
        after_type = _memory_snapshot(group, fixture["id"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        observed = {
            "message_readable": bool(readable["messages"]),
            "size_cache_positive": cache["exists"] and int(cache["value"] or 0) > 0,
            "embd_update_code": embd_update["code"],
            "type_update_code": type_update["code"],
            "embd_unchanged": after_embd.get("embd_id") == before.get("embd_id"),
            "type_unchanged": after_type.get("memory_type") == before.get("memory_type"),
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = fixture["response"]["code"] == 0 and added["code"] == 0 and nonempty_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_memory_and_synchronously_store_raw_message",
                    "create_code": fixture["response"]["code"],
                    "add_code": added["code"],
                    "message_readable": observed["message_readable"],
                    "poll_attempts": readable["attempts"],
                    "raw_sha256": added["raw_sha256"],
                },
                {
                    "name": "read_only_positive_memory_size_cache_verification",
                    "cache_exists": cache["exists"],
                    "cache_positive": observed["size_cache_positive"],
                    "cache_key_fingerprint": cache["key_fingerprint"],
                },
                {
                    "name": "reject_embedding_and_type_updates_on_nonempty_memory",
                    "embedding_response": [embd_update["http_status"], embd_update["code"]],
                    "type_response": [type_update["http_status"], type_update["code"]],
                    "embedding_unchanged": observed["embd_unchanged"],
                    "type_unchanged": observed["type_unchanged"],
                    "raw_sha256": [embd_update["raw_sha256"], type_update["raw_sha256"]],
                },
                {"name": "cleanup_nonempty_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "raw_message_readable": True,
                "size_cache_positive": True,
                "restricted_fields": ["embd_id", "memory_type"],
                "mutation": 0,
            },
        }

    return _run_case(case_id, execute)


def run_mm016() -> dict[str, Any]:
    case_id = "TC-MM-016"
    prefix = "fresh-mm-016"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_config_fixture", prefix)
        config = _get_memory_config(case_id, group, owner["auth"], "get_memory_config", fixture["id"])
        data = config["data"] if isinstance(config["data"], dict) else {}
        snapshot = _memory_snapshot(group, fixture["id"])
        comparable = {
            "id": snapshot.get("id"),
            "name": snapshot.get("name"),
            "avatar": snapshot.get("avatar"),
            "tenant_id": snapshot.get("tenant_id"),
            "memory_type": ["raw"],
            "storage_type": snapshot.get("storage_type"),
            "embd_id": snapshot.get("embd_id"),
            "llm_id": snapshot.get("llm_id"),
            "permissions": snapshot.get("permissions"),
            "description": snapshot.get("description"),
            "memory_size": snapshot.get("memory_size"),
            "forgetting_policy": snapshot.get("forgetting_policy"),
            "temperature": snapshot.get("temperature"),
            "system_prompt": snapshot.get("system_prompt"),
            "user_prompt": snapshot.get("user_prompt"),
        }
        fields_match = all(data.get(key) == value for key, value in comparable.items())
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        passed = preclean and fixture["response"]["code"] == 0 and config["http_status"] == 200 and config["code"] == 0 and fields_match and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_memory_config_fixture", "code": fixture["response"]["code"]},
                {
                    "name": "get_config_and_compare_read_only_metadata",
                    "http_status": config["http_status"],
                    "code": config["code"],
                    "field_count_compared": len(comparable),
                    "all_fields_match": fields_match,
                    "raw_sha256": config["raw_sha256"],
                },
                {"name": "cleanup_config_fixture_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"api_config_matches_database": True},
        }

    return _run_case(case_id, execute)


def run_mm017() -> dict[str, Any]:
    case_id = "TC-MM-017"
    missing_id = "fresh-mm-017-nonexistent"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        response = _get_memory_config(case_id, group, owner["auth"], "get_nonexistent_memory_config", missing_id)
        snapshot = _memory_snapshot(group, missing_id)
        passed = response["http_status"] == 200 and response["code"] == 404 and snapshot.get("count") == 0
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "get_nonexistent_memory_config",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "read_only_nonexistent_metadata_verification", "database_count": snapshot.get("count")},
            ],
            "oracle": {"response": [200, 404]},
        }

    return _run_case(case_id, execute)


def run_mm018() -> dict[str, Any]:
    case_id = "TC-MM-018"
    prefix = "fresh-mm-018"
    email = "mm-018-user-b@fresh.invalid"
    password = "Fresh-MM-018-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        fixture = _create_memory_fixture(case_id, group, owner, "create_user_a_memory", prefix)
        before = _memory_snapshot(group, fixture["id"])
        denied = _get_memory_config(
            case_id,
            group,
            secondary["auth"],
            "user_b_get_user_a_memory_config",
            fixture["id"],
        )
        after = _memory_snapshot(group, fixture["id"])
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        passed = (
            preclean
            and secondary["preclean"]
            and secondary["registration"]["code"] == 0
            and secondary["login"]["code"] == 0
            and secondary["tenant_id"] != owner["tenant_id"]
            and fixture["response"]["code"] == 0
            and denied["http_status"] == 200
            and denied["code"] == 404
            and before == after
            and memory_cleanup
            and user_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_user_b_and_user_a_memory",
                    "registration_code": secondary["registration"]["code"],
                    "login_code": secondary["login"]["code"],
                    "tenant_ids_distinct": secondary["tenant_id"] != owner["tenant_id"],
                    "memory_code": fixture["response"]["code"],
                },
                {
                    "name": "deny_cross_tenant_memory_config_access",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "memory_unchanged": before == after,
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "cleanup_memory_and_secondary_user_through_apis",
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {"response": [200, 404], "data_disclosed": False, "mutation": 0},
        }

    return _run_case(case_id, execute)


def run_mm019() -> dict[str, Any]:
    case_id = "TC-MM-019"
    prefix = "fresh-mm-019"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_delete_fixture", prefix)
        before = _memory_snapshot(group, fixture["id"])
        deleted = _delete_memory(case_id, group, owner["auth"], "delete_normal_memory", fixture["id"])
        after = _memory_snapshot(group, fixture["id"])
        config = _get_memory_config(case_id, group, owner["auth"], "get_deleted_memory_config", fixture["id"])
        passed = (
            preclean
            and fixture["response"]["code"] == 0
            and before.get("count") == 1
            and deleted["http_status"] == 200
            and deleted["code"] == 0
            and after.get("count") == 0
            and config["http_status"] == 200
            and config["code"] == 404
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_normal_delete_fixture", "database_count": before.get("count")},
                {
                    "name": "delete_memory_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "verify_metadata_removed_and_config_not_found",
                    "database_count": after.get("count"),
                    "config_response": [config["http_status"], config["code"]],
                    "raw_sha256": config["raw_sha256"],
                },
            ],
            "oracle": {"delete_response": [200, 0], "metadata_count": 0, "config": [200, 404]},
        }

    return _run_case(case_id, execute)


def run_mm020() -> dict[str, Any]:
    case_id = "TC-MM-020"
    prefix = "fresh-mm-020"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        target = _create_memory_fixture(case_id, group, owner, "create_delete_with_message_target", f"{prefix}-target")
        survivor = _create_memory_fixture(case_id, group, owner, "create_delete_with_message_survivor", f"{prefix}-survivor")
        add_target = _add_message(case_id, group, owner["auth"], "add_target_message", target["id"], marker=f"{prefix}-target")
        add_survivor = _add_message(case_id, group, owner["auth"], "add_survivor_message", survivor["id"], marker=f"{prefix}-survivor")
        target_readable = _wait_memory_messages(case_id, group, owner["auth"], target["id"], label_prefix="poll_target_message")
        survivor_readable = _wait_memory_messages(case_id, group, owner["auth"], survivor["id"], label_prefix="poll_survivor_message")
        before_target_store = _message_store_snapshot(group, owner["tenant_id"], target["id"])
        before_survivor_store = _message_store_snapshot(group, owner["tenant_id"], survivor["id"])
        deleted = _delete_memory(case_id, group, owner["auth"], "delete_memory_with_messages", target["id"])
        target_metadata_after = _memory_snapshot(group, target["id"])
        survivor_metadata_after = _memory_snapshot(group, survivor["id"])
        target_store_after = _message_store_snapshot(group, owner["tenant_id"], target["id"])
        survivor_store_after = _message_store_snapshot(group, owner["tenant_id"], survivor["id"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [survivor["id"]])
        passed = (
            preclean
            and target["response"]["code"] == survivor["response"]["code"] == 0
            and add_target["code"] == add_survivor["code"] == 0
            and bool(target_readable["messages"])
            and bool(survivor_readable["messages"])
            and before_target_store["raw_message_count"] == 1
            and before_survivor_store["raw_message_count"] == 1
            and deleted["http_status"] == 200
            and deleted["code"] == 0
            and target_metadata_after.get("count") == 0
            and target_store_after["raw_message_count"] == 0
            and survivor_metadata_after.get("count") == 1
            and survivor_store_after["raw_message_count"] == 1
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_target_and_survivor_memories_with_raw_messages",
                    "create_codes": [target["response"]["code"], survivor["response"]["code"]],
                    "add_codes": [add_target["code"], add_survivor["code"]],
                    "messages_readable": [bool(target_readable["messages"]), bool(survivor_readable["messages"])],
                },
                {
                    "name": "read_only_predelete_message_store_verification",
                    "target_raw_count": before_target_store["raw_message_count"],
                    "survivor_raw_count": before_survivor_store["raw_message_count"],
                },
                {
                    "name": "delete_target_memory_and_verify_store_scope",
                    "delete_response": [deleted["http_status"], deleted["code"]],
                    "target_metadata_count": target_metadata_after.get("count"),
                    "target_raw_count": target_store_after["raw_message_count"],
                    "survivor_metadata_count": survivor_metadata_after.get("count"),
                    "survivor_raw_count": survivor_store_after["raw_message_count"],
                    "raw_sha256": deleted["raw_sha256"],
                },
                {"name": "cleanup_survivor_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "target_metadata": 0,
                "target_messages": 0,
                "survivor_metadata": 1,
                "survivor_messages": 1,
            },
        }

    return _run_case(case_id, execute)


def run_mm021() -> dict[str, Any]:
    case_id = "TC-MM-021"
    prefix = "fresh-mm-021"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_repeat_delete_fixture", prefix)
        first = _delete_memory(case_id, group, owner["auth"], "first_delete", fixture["id"])
        second = _delete_memory(case_id, group, owner["auth"], "second_delete", fixture["id"])
        snapshot = _memory_snapshot(group, fixture["id"])
        passed = (
            preclean
            and fixture["response"]["code"] == 0
            and first["http_status"] == 200
            and first["code"] == 0
            and second["http_status"] == 200
            and second["code"] == 404
            and snapshot.get("count") == 0
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_repeat_delete_fixture", "code": fixture["response"]["code"]},
                {
                    "name": "delete_same_memory_twice",
                    "first_response": [first["http_status"], first["code"]],
                    "second_response": [second["http_status"], second["code"]],
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {"name": "read_only_deleted_metadata_verification", "database_count": snapshot.get("count")},
            ],
            "oracle": {"first": [200, 0], "second": [200, 404]},
        }

    return _run_case(case_id, execute)


def run_mm022() -> dict[str, Any]:
    case_id = "TC-MM-022"
    prefix = "fresh-mm-022"
    email = "mm-022-user-b@fresh.invalid"
    password = "Fresh-MM-022-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        fixture = _create_memory_fixture(case_id, group, owner, "create_cross_tenant_delete_target", prefix)
        before = _memory_snapshot(group, fixture["id"])
        denied = _delete_memory(
            case_id,
            group,
            secondary["auth"],
            "user_b_delete_user_a_memory",
            fixture["id"],
        )
        after = _memory_snapshot(group, fixture["id"])
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        passed = (
            preclean
            and secondary["preclean"]
            and secondary["registration"]["code"] == 0
            and secondary["login"]["code"] == 0
            and fixture["response"]["code"] == 0
            and denied["http_status"] == 200
            and denied["code"] == 404
            and before == after
            and memory_cleanup
            and user_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_user_b_and_user_a_delete_target",
                    "registration_code": secondary["registration"]["code"],
                    "login_code": secondary["login"]["code"],
                    "memory_code": fixture["response"]["code"],
                },
                {
                    "name": "deny_cross_tenant_memory_delete",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "memory_unchanged": before == after,
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "cleanup_memory_and_secondary_user_through_apis",
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {"response": [200, 404], "mutation": 0},
        }

    return _run_case(case_id, execute)


def run_mm023() -> dict[str, Any]:
    case_id = "TC-MM-023"
    prefix = "fresh-mm-023"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_memory(
            case_id,
            group,
            owner["auth"],
            "create_memory_with_empty_model_ids",
            {"name": prefix, "memory_type": ["raw"], "embd_id": "", "llm_id": ""},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        memory_id = str(data.get("id") or "")
        snapshot = _memory_snapshot(group, memory_id) if memory_id else {"count": 0}
        config = _get_memory_config(case_id, group, owner["auth"], "get_empty_model_memory_config", memory_id)
        config_data = config["data"] if isinstance(config["data"], dict) else {}
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [memory_id])
        observed = {
            "http_status": created["http_status"],
            "code": created["code"],
            "api_embd_id": config_data.get("embd_id"),
            "api_llm_id": config_data.get("llm_id"),
            "orm_embd_id": snapshot.get("embd_id"),
            "orm_llm_id": snapshot.get("llm_id"),
            "physical_embd_is_empty": snapshot.get("physical_embd_is_empty"),
            "physical_llm_is_empty": snapshot.get("physical_llm_is_empty"),
            "physical_embd_is_null": snapshot.get("physical_embd_is_null"),
            "physical_llm_is_null": snapshot.get("physical_llm_is_null"),
            "cleanup_succeeded": preclean and config["code"] == 0 and cleanup,
        }
        passed = empty_model_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_memory_with_empty_embedding_and_chat_ids",
                    "http_status": created["http_status"],
                    "code": created["code"],
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "verify_api_orm_and_physical_empty_model_semantics",
                    "api_values_empty": config_data.get("embd_id") == config_data.get("llm_id") == "",
                    "orm_values_empty": snapshot.get("embd_id") == snapshot.get("llm_id") == "",
                    "physical_embd_is_empty": snapshot.get("physical_embd_is_empty"),
                    "physical_llm_is_empty": snapshot.get("physical_llm_is_empty"),
                    "physical_embd_is_null": snapshot.get("physical_embd_is_null"),
                    "physical_llm_is_null": snapshot.get("physical_llm_is_null"),
                    "raw_sha256": config["raw_sha256"],
                },
                {"name": "cleanup_empty_model_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "control_physical": "empty string",
                "experiment_physical": "NULL",
                "api_and_orm": "empty string",
            },
        }

    return _run_case(case_id, execute)


def run_mm024() -> dict[str, Any]:
    case_id = "TC-MM-024"
    prefix = "fresh-mm-024-no-auth"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        models = _model_ids(group, owner["tenant_id"])
        before = _tenant_memory_count(group, owner["tenant_id"])
        listed = _unauthenticated_request(case_id, group, "list_memories_without_authorization", "GET", "/memories")
        created = _unauthenticated_request(
            case_id,
            group,
            "create_memory_without_authorization",
            "POST",
            "/memories",
            payload=_models_payload(prefix, models),
        )
        after = _tenant_memory_count(group, owner["tenant_id"])
        cleanup = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        passed = listed["http_status"] == listed["code"] == 401 and created["http_status"] == created["code"] == 401 and before == after and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "get_memory_list_without_authorization_header",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "post_memory_without_authorization_header",
                    "http_status": created["http_status"],
                    "code": created["code"],
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "read_only_no_unauthenticated_write_verification",
                    "metadata_count_unchanged": before == after,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {"responses": [[401, 401], [401, 401]], "metadata_delta": 0},
        }

    return _run_case(case_id, execute)


def run_mm_sup001() -> dict[str, Any]:
    case_id = "TC-MM-SUP-001"
    prefix = "fresh-mm-sup-001"
    requested_name = f"  {prefix} Spaced Memory Name  "
    expected_name = f"{prefix} Spaced Memory Name"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_whitespace_wrapped_name", requested_name)
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        passed = (
            preclean
            and fixture["response"]["http_status"] == 200
            and fixture["response"]["code"] == 0
            and fixture["data"].get("name") == expected_name
            and fixture["snapshot"].get("name") == expected_name
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_memory_with_leading_and_trailing_spaces",
                    "http_status": fixture["response"]["http_status"],
                    "code": fixture["response"]["code"],
                    "raw_sha256": fixture["response"]["raw_sha256"],
                },
                {
                    "name": "read_only_stripped_name_verification",
                    "api_name_matches": fixture["data"].get("name") == expected_name,
                    "database_name_matches": fixture["snapshot"].get("name") == expected_name,
                },
                {"name": "cleanup_stripped_name_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"stored_name": expected_name},
        }

    return _run_case(case_id, execute)


def run_mm_sup002() -> dict[str, Any]:
    case_id = "TC-MM-SUP-002"
    prefix = "fresh-mm-sup-002"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        before = _tenant_memory_count(group, owner["tenant_id"])
        response = _create_memory(
            case_id,
            group,
            owner["auth"],
            "reject_name_longer_than_limit",
            _models_payload(prefix + "X" * 129, models),
        )
        after = _tenant_memory_count(group, owner["tenant_id"])
        cleanup = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        passed = preclean and response["http_status"] == 200 and response["code"] != 0 and "exceeds limit" in str(response["message"] or "") and before == after and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "submit_over_limit_memory_name",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_mentions_limit": "exceeds limit" in str(response["message"] or ""),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_zero_write_and_cleanup_verification",
                    "metadata_count_unchanged": before == after,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {"code_nonzero": True, "message_fragment": "exceeds limit", "metadata_delta": 0},
        }

    return _run_case(case_id, execute)


def run_mm_sup003() -> dict[str, Any]:
    case_id = "TC-MM-SUP-003"
    prefix = "fresh-mm-sup-003"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(
            case_id,
            group,
            owner,
            "create_duplicate_unordered_memory_types",
            prefix,
            memory_type=["semantic", "raw", "semantic", "episodic"],
        )
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        expected = ["raw", "semantic", "episodic"]
        passed = preclean and fixture["response"]["code"] == 0 and fixture["snapshot"].get("memory_type") == 7 and fixture["data"].get("memory_type") == expected and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_memory_with_duplicate_unordered_types",
                    "http_status": fixture["response"]["http_status"],
                    "code": fixture["response"]["code"],
                    "api_types": fixture["data"].get("memory_type"),
                    "raw_sha256": fixture["response"]["raw_sha256"],
                },
                {
                    "name": "read_only_bit_value_and_enum_order_verification",
                    "physical_bit_value": fixture["snapshot"].get("memory_type"),
                    "api_order_matches": fixture["data"].get("memory_type") == expected,
                },
                {"name": "cleanup_normalized_type_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"bit_value": 7, "api_order": expected},
        }

    return _run_case(case_id, execute)


def run_mm_sup004() -> dict[str, Any]:
    case_id = "TC-MM-SUP-004"
    prefix = "fresh-mm-sup-004"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        models = _model_ids(group, owner["tenant_id"])
        upper = _create_memory(
            case_id,
            group,
            owner["auth"],
            "reject_uppercase_memory_types",
            _models_payload(f"{prefix}-upper", models, ["RAW", "Semantic"]),
        )
        lower = _create_memory(
            case_id,
            group,
            owner["auth"],
            "accept_lowercase_memory_types",
            _models_payload(f"{prefix}-lower", models, ["raw", "semantic"]),
        )
        lower_data = lower["data"] if isinstance(lower["data"], dict) else {}
        lower_id = str(lower_data.get("id") or "")
        upper_data = upper["data"] if isinstance(upper["data"], dict) else {}
        upper_id = str(upper_data.get("id") or "")
        upper_snapshot = _memory_snapshot(group, upper_id) if upper_id else {"count": 0}
        lower_snapshot = _memory_snapshot(group, lower_id) if lower_id else {"count": 0}
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [upper_id, lower_id])
        passed = (
            preclean
            and upper["http_status"] == 200
            and upper["code"] != 0
            and "not supported" in str(upper["message"] or "")
            and upper_snapshot.get("count") == 0
            and lower["code"] == 0
            and lower_snapshot.get("memory_type") == 3
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "reject_uppercase_and_accept_lowercase_type_variants",
                    "uppercase_response": [upper["http_status"], upper["code"]],
                    "uppercase_message_matches": "not supported" in str(upper["message"] or ""),
                    "lowercase_response": [lower["http_status"], lower["code"]],
                    "raw_sha256": [upper["raw_sha256"], lower["raw_sha256"]],
                },
                {
                    "name": "read_only_case_sensitive_type_storage_verification",
                    "uppercase_row_count": upper_snapshot.get("count"),
                    "lowercase_bit_value": lower_snapshot.get("memory_type"),
                },
                {"name": "cleanup_lowercase_control_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"uppercase": "reject", "lowercase_bit_value": 3},
        }

    return _run_case(case_id, execute)


def _run_invalid_update_supplement(
    case_id: str,
    field: str,
    value: Any,
    message_fragment: str,
) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_invalid_update_fixture", prefix)
        before = _memory_snapshot(group, fixture["id"])
        response = _update_memory(
            case_id,
            group,
            owner["auth"],
            f"reject_invalid_{field}",
            fixture["id"],
            {field: value},
        )
        after = _memory_snapshot(group, fixture["id"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        unchanged = before.get(field) == after.get(field)
        passed = (
            preclean and fixture["response"]["code"] == 0 and response["http_status"] == 200 and response["code"] != 0 and message_fragment in str(response["message"] or "") and unchanged and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_invalid_update_fixture", "code": fixture["response"]["code"]},
                {
                    "name": f"reject_invalid_{field}_through_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_fragment_present": message_fragment in str(response["message"] or ""),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_no_invalid_field_mutation_and_cleanup",
                    "field_unchanged": unchanged,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {"code_nonzero": True, "message_fragment": message_fragment, "mutation": 0},
        }

    return _run_case(case_id, execute)


def run_mm_sup005() -> dict[str, Any]:
    return _run_invalid_update_supplement("TC-MM-SUP-005", "temperature", 1.5, "Temperature should be in range [0, 1]")


def run_mm_sup006() -> dict[str, Any]:
    return _run_invalid_update_supplement("TC-MM-SUP-006", "memory_size", -1, "Memory size should be in range")


def run_mm_sup007() -> dict[str, Any]:
    return _run_invalid_update_supplement("TC-MM-SUP-007", "forgetting_policy", "invalid_policy", "not supported")


def run_mm_sup008() -> dict[str, Any]:
    return _run_invalid_update_supplement("TC-MM-SUP-008", "permissions", "invalid_permission", "Unknown permission")


def run_mm_sup009() -> dict[str, Any]:
    case_id = "TC-MM-SUP-009"
    prefix = "fresh-mm-sup-009"
    email = "mm-sup-009-user-b@fresh.invalid"
    password = "Fresh-MM-SUP-009-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        membership = BASE._invite_and_accept_member(case_id, group, owner, secondary, email)
        fixture = _create_memory_fixture(case_id, group, owner, "create_team_memory", prefix)
        team = _update_memory(case_id, group, owner["auth"], "set_memory_permission_team", fixture["id"], {"permissions": "team"})
        permission_snapshot = _memory_snapshot(group, fixture["id"])
        accessed = _request(case_id, group, "team_member_get_memory", secondary["auth"], "GET", f"/memories/{fixture['id']}")
        membership_cleanup = BASE._remove_secondary_membership(case_id, group, owner["tenant_id"], secondary)
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        passed = (
            preclean
            and secondary["preclean"]
            and secondary["registration"]["code"] == 0
            and secondary["login"]["code"] == 0
            and membership["invite"]["code"] == 0
            and membership["accept"]["code"] == 0
            and membership["snapshot"].get("role") == "normal"
            and fixture["response"]["code"] == 0
            and team["code"] == 0
            and permission_snapshot.get("permissions") == "team"
            and accessed["http_status"] == 200
            and accessed["code"] == 0
            and membership_cleanup["succeeded"]
            and memory_cleanup
            and user_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "register_invite_and_accept_normal_team_member",
                    "registration_code": secondary["registration"]["code"],
                    "invite_code": membership["invite"]["code"],
                    "accept_code": membership["accept"]["code"],
                    "membership_role": membership["snapshot"].get("role"),
                },
                {
                    "name": "create_team_memory_and_access_as_joined_member",
                    "create_code": fixture["response"]["code"],
                    "permission_update_code": team["code"],
                    "member_response": [accessed["http_status"], accessed["code"]],
                    "raw_sha256": [team["raw_sha256"], accessed["raw_sha256"]],
                },
                {
                    "name": "cleanup_membership_memory_and_user_through_apis",
                    "membership_cleanup": membership_cleanup["succeeded"],
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {"joined_role": "normal", "member_response": [200, 0]},
        }

    return _run_case(case_id, execute)


def _run_unjoined_access_supplement(case_id: str, *, permission: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    email = f"{case_id.lower()}-user-b@fresh.invalid"
    password = f"Fresh-{case_id}-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        fixture = _create_memory_fixture(case_id, group, owner, "create_acl_memory", prefix)
        if permission == "team":
            permission_response = _update_memory(case_id, group, owner["auth"], "set_team_permission_without_membership", fixture["id"], {"permissions": "team"})
        else:
            permission_response = {"http_status": 200, "code": 0, "raw_sha256": None}
        before = _memory_snapshot(group, fixture["id"])
        denied = _request(
            case_id,
            group,
            "unjoined_user_get_memory",
            secondary["auth"],
            "GET",
            f"/memories/{fixture['id']}",
        )
        after = _memory_snapshot(group, fixture["id"])
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        passed = (
            preclean
            and secondary["preclean"]
            and secondary["registration"]["code"] == 0
            and secondary["login"]["code"] == 0
            and permission_response["code"] == 0
            and before.get("permissions") == permission
            and denied["http_status"] == 200
            and denied["code"] == 404
            and before == after
            and memory_cleanup
            and user_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_user_and_acl_memory",
                    "registration_code": secondary["registration"]["code"],
                    "login_code": secondary["login"]["code"],
                    "permission": before.get("permissions"),
                },
                {
                    "name": "deny_unjoined_user_memory_access",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "memory_unchanged": before == after,
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "cleanup_acl_memory_and_user_through_apis",
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {"permission": permission, "response": [200, 404], "mutation": 0},
        }

    return _run_case(case_id, execute)


def run_mm_sup010() -> dict[str, Any]:
    return _run_unjoined_access_supplement("TC-MM-SUP-010", permission="team")


def run_mm_sup011() -> dict[str, Any]:
    return _run_unjoined_access_supplement("TC-MM-SUP-011", permission="me")


def run_mm_sup012() -> dict[str, Any]:
    case_id = "TC-MM-SUP-012"
    prefix = "fresh-mm-sup-012"
    user_b_email = "mm-sup-012-user-b@fresh.invalid"
    user_b_password = "Fresh-MM-SUP-012-User-B@1234"
    user_c_email = "mm-sup-012-user-c@fresh.invalid"
    user_c_password = "Fresh-MM-SUP-012-User-C@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        owner_preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        user_b = _prepare_tagged_secondary_user(case_id, group, "user_b", user_b_email, user_b_password)
        user_c = _prepare_tagged_secondary_user(case_id, group, "user_c", user_c_email, user_c_password)
        membership = BASE._invite_and_accept_member(case_id, group, owner, user_b, user_b_email)
        user_c_preclean = _cleanup_prefix(case_id, group, user_c["auth"], user_c["tenant_id"], prefix)

        memory_a = _create_memory_fixture(case_id, group, owner, "create_user_a_team_memory", f"{prefix}-a")
        permission = _update_memory(
            case_id,
            group,
            owner["auth"],
            "set_user_a_memory_team_permission",
            memory_a["id"],
            {"permissions": "team"},
        )
        models = _model_ids(group, owner["tenant_id"])
        create_c = _create_memory(
            case_id,
            group,
            user_c["auth"],
            "create_user_c_private_memory",
            _models_payload(f"{prefix}-c", models),
        )
        create_c_data = create_c["data"] if isinstance(create_c["data"], dict) else {}
        memory_c_id = str(create_c_data.get("id") or "")

        list_b = _list_memories(
            case_id,
            group,
            user_b["auth"],
            "user_b_filter_joined_and_unjoined_owners",
            {
                "owner_ids": f"{owner['tenant_id']},{user_c['tenant_id']}",
                "keywords": prefix,
            },
        )
        list_c = _list_memories(
            case_id,
            group,
            user_c["auth"],
            "user_c_filter_own_owner_id",
            {"owner_ids": user_c["tenant_id"], "keywords": prefix},
        )

        def listed_ids(response: dict[str, Any]) -> set[str]:
            data = response["data"] if isinstance(response.get("data"), dict) else {}
            rows = data.get("memory_list") if isinstance(data.get("memory_list"), list) else []
            return {str(row.get("id")) for row in rows if isinstance(row, dict)}

        b_ids = listed_ids(list_b)
        c_ids = listed_ids(list_c)
        snapshot_a = _memory_snapshot(group, memory_a["id"])
        snapshot_c = _memory_snapshot(group, memory_c_id) if memory_c_id else {"count": 0}
        membership_snapshot = BASE._membership_snapshot(group, user_b["tenant_id"], owner["tenant_id"])

        membership_cleanup = BASE._remove_secondary_membership(case_id, group, owner["tenant_id"], user_b)
        memory_a_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [memory_a["id"]])
        memory_c_cleanup = _cleanup_memory_ids(case_id, group, user_c["auth"], [memory_c_id])
        user_b_cleanup = _cleanup_tagged_secondary_user(case_id, group, "user_b", user_b_email)
        user_c_cleanup = _cleanup_tagged_secondary_user(case_id, group, "user_c", user_c_email)

        passed = (
            owner_preclean
            and user_c_preclean
            and user_b["preclean"]
            and user_c["preclean"]
            and user_b["registration"]["code"] == user_b["login"]["code"] == 0
            and user_c["registration"]["code"] == user_c["login"]["code"] == 0
            and membership["invite"]["code"] == membership["accept"]["code"] == 0
            and membership_snapshot.get("count") == 1
            and membership_snapshot.get("role") == "normal"
            and memory_a["response"]["code"] == permission["code"] == 0
            and create_c["code"] == 0
            and snapshot_a.get("tenant_id") == owner["tenant_id"]
            and snapshot_a.get("permissions") == "team"
            and snapshot_c.get("tenant_id") == user_c["tenant_id"]
            and list_b["http_status"] == 200
            and list_b["code"] == 0
            and b_ids == {memory_a["id"]}
            and list_c["http_status"] == 200
            and list_c["code"] == 0
            and c_ids == {memory_c_id}
            and membership_cleanup["succeeded"]
            and memory_a_cleanup
            and memory_c_cleanup
            and user_b_cleanup["succeeded"]
            and user_c_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_joined_user_b_and_independent_user_c",
                    "user_b_registration_code": user_b["registration"]["code"],
                    "user_c_registration_code": user_c["registration"]["code"],
                    "membership_role": membership_snapshot.get("role"),
                    "membership_status": membership_snapshot.get("status"),
                },
                {
                    "name": "create_team_memory_a_and_private_memory_c",
                    "memory_a_response": [memory_a["response"]["http_status"], memory_a["response"]["code"]],
                    "permission_update_code": permission["code"],
                    "memory_c_response": [create_c["http_status"], create_c["code"]],
                    "database_tenants_match": snapshot_a.get("tenant_id") == owner["tenant_id"] and snapshot_c.get("tenant_id") == user_c["tenant_id"],
                    "raw_sha256": [
                        memory_a["response"]["raw_sha256"],
                        permission["raw_sha256"],
                        create_c["raw_sha256"],
                    ],
                },
                {
                    "name": "filter_owner_ids_to_accessible_tenants_only",
                    "user_b_response": [list_b["http_status"], list_b["code"]],
                    "user_b_only_memory_a": b_ids == {memory_a["id"]},
                    "user_c_response": [list_c["http_status"], list_c["code"]],
                    "user_c_only_memory_c": c_ids == {memory_c_id},
                    "raw_sha256": [list_b["raw_sha256"], list_c["raw_sha256"]],
                },
                {
                    "name": "cleanup_membership_memories_and_users_through_apis",
                    "membership_cleanup": membership_cleanup["succeeded"],
                    "memory_cleanups": [memory_a_cleanup, memory_c_cleanup],
                    "user_cleanups": [user_b_cleanup["succeeded"], user_c_cleanup["succeeded"]],
                },
            ],
            "oracle": {
                "user_b_visible_ids": "memory_a_only",
                "user_c_visible_ids": "memory_c_only",
                "unjoined_tenant_filtered": True,
            },
        }

    return _run_case(case_id, execute)


def run_mm_sup013() -> dict[str, Any]:
    case_id = "TC-MM-SUP-013"
    prefix = "fresh-mm-sup-013"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_internal_model_update_fixture", prefix)
        before = _memory_snapshot(group, fixture["id"])
        candidates = {
            "tenant_llm_id": 700000000000000001,
            "tenant_embd_id": 700000000000000002,
        }
        response = _update_memory(
            case_id,
            group,
            owner["auth"],
            "update_public_internal_model_fields",
            fixture["id"],
            candidates,
        )
        after = _memory_snapshot(group, fixture["id"])
        fields_changed = any(after.get(key) != before.get(key) for key in candidates)
        update_time_changed = after.get("update_time") != before.get("update_time")
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fields_changed": fields_changed,
            "update_time_changed": update_time_changed,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = fixture["response"]["code"] == 0 and internal_model_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_internal_model_field_update_fixture",
                    "code": fixture["response"]["code"],
                },
                {
                    "name": "submit_tenant_llm_and_embedding_fields_through_public_route",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_internal_fields_and_update_time_verification",
                    "fields_changed": fields_changed,
                    "update_time_changed": update_time_changed,
                    "silent_success_noop": response["code"] == 0 and not fields_changed and not update_time_changed,
                },
                {
                    "name": "cleanup_internal_model_update_fixture_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "allowed_outcomes": ["explicit_rejection", "real_update"],
                "silent_success_noop_allowed": False,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-INTERNAL-MODEL-FIELD-NOOP-001",
                    "summary": f"{group} public Memory update silently accepted tenant model fields without mutation",
                    "code_location": "api/apps/services/memory_api_service.py:update_memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm_sup014() -> dict[str, Any]:
    case_id = "TC-MM-SUP-014"
    prefix = "fresh-mm-sup-014"
    new_embd_id = "fresh-mm-sup-014-new-embedding"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        nonempty = _create_memory_fixture(case_id, group, owner, "create_nonempty_embedding_fixture", f"{prefix}-nonempty")
        empty = _create_memory_fixture(case_id, group, owner, "create_empty_embedding_control", f"{prefix}-empty")
        added = _add_message(
            case_id,
            group,
            owner["auth"],
            "add_nonempty_embedding_message",
            nonempty["id"],
            marker=f"{prefix}-message",
        )
        readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            nonempty["id"],
            label_prefix="poll_nonempty_embedding_message",
        )
        cache = _memory_size_cache(group, nonempty["id"])
        store = _message_store_snapshot(group, owner["tenant_id"], nonempty["id"])
        before_nonempty = _memory_snapshot(group, nonempty["id"])
        before_empty = _memory_snapshot(group, empty["id"])
        rejected = _update_memory(
            case_id,
            group,
            owner["auth"],
            "reject_nonempty_embedding_update",
            nonempty["id"],
            {"embd_id": new_embd_id},
        )
        accepted = _update_memory(
            case_id,
            group,
            owner["auth"],
            "accept_empty_embedding_update",
            empty["id"],
            {"embd_id": new_embd_id},
        )
        after_nonempty = _memory_snapshot(group, nonempty["id"])
        after_empty = _memory_snapshot(group, empty["id"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [nonempty["id"], empty["id"]])
        message_fragment = "Can't update ['embd_id'] when memory isn't empty."
        passed = (
            preclean
            and nonempty["response"]["code"] == empty["response"]["code"] == 0
            and added["code"] == 0
            and bool(readable["messages"])
            and cache["exists"]
            and (cache["value"] or 0) > 0
            and store["raw_message_count"] >= 1
            and rejected["http_status"] == 200
            and rejected["code"] != 0
            and message_fragment in str(rejected["message"] or "")
            and after_nonempty.get("embd_id") == before_nonempty.get("embd_id")
            and accepted["http_status"] == 200
            and accepted["code"] == 0
            and before_empty.get("embd_id") != new_embd_id
            and after_empty.get("embd_id") == new_embd_id
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_nonempty_and_empty_embedding_update_fixtures",
                    "create_codes": [nonempty["response"]["code"], empty["response"]["code"]],
                    "add_message_code": added["code"],
                    "raw_sha256": added["raw_sha256"],
                },
                {
                    "name": "confirm_nonempty_precondition_with_api_cache_and_message_store",
                    "message_readable": bool(readable["messages"]),
                    "poll_attempts": readable["attempts"],
                    "size_cache_positive": cache["exists"] and (cache["value"] or 0) > 0,
                    "raw_message_count": store["raw_message_count"],
                },
                {
                    "name": "reject_nonempty_and_accept_empty_embedding_updates",
                    "nonempty_response": [rejected["http_status"], rejected["code"]],
                    "message_matches": message_fragment in str(rejected["message"] or ""),
                    "nonempty_value_unchanged": after_nonempty.get("embd_id") == before_nonempty.get("embd_id"),
                    "empty_response": [accepted["http_status"], accepted["code"]],
                    "empty_value_changed": after_empty.get("embd_id") == new_embd_id,
                    "raw_sha256": [rejected["raw_sha256"], accepted["raw_sha256"]],
                },
                {"name": "cleanup_embedding_update_fixtures_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "nonempty_update": "reject_without_mutation",
                "empty_update": "accept_and_persist",
            },
        }

    return _run_case(case_id, execute)


def run_mm_sup015() -> dict[str, Any]:
    case_id = "TC-MM-SUP-015"
    prefix = "fresh-mm-sup-015"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        nonempty = _create_memory_fixture(case_id, group, owner, "create_nonempty_type_fixture", f"{prefix}-nonempty")
        empty = _create_memory_fixture(case_id, group, owner, "create_empty_type_control", f"{prefix}-empty")
        added = _add_message(
            case_id,
            group,
            owner["auth"],
            "add_nonempty_type_message",
            nonempty["id"],
            marker=f"{prefix}-message",
        )
        readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            nonempty["id"],
            label_prefix="poll_nonempty_type_message",
        )
        cache = _memory_size_cache(group, nonempty["id"])
        store = _message_store_snapshot(group, owner["tenant_id"], nonempty["id"])
        before_nonempty = _memory_snapshot(group, nonempty["id"])
        before_empty = _memory_snapshot(group, empty["id"])
        payload = {"memory_type": ["raw", "semantic"]}
        rejected = _update_memory(
            case_id,
            group,
            owner["auth"],
            "reject_nonempty_memory_type_update",
            nonempty["id"],
            payload,
        )
        accepted = _update_memory(
            case_id,
            group,
            owner["auth"],
            "accept_empty_memory_type_update",
            empty["id"],
            payload,
        )
        after_nonempty = _memory_snapshot(group, nonempty["id"])
        after_empty = _memory_snapshot(group, empty["id"])
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [nonempty["id"], empty["id"]])
        message_fragment = "Can't update ['memory_type'] when memory isn't empty."
        passed = (
            preclean
            and nonempty["response"]["code"] == empty["response"]["code"] == 0
            and added["code"] == 0
            and bool(readable["messages"])
            and cache["exists"]
            and (cache["value"] or 0) > 0
            and store["raw_message_count"] >= 1
            and rejected["http_status"] == 200
            and rejected["code"] != 0
            and message_fragment in str(rejected["message"] or "")
            and after_nonempty.get("memory_type") == before_nonempty.get("memory_type")
            and after_nonempty.get("system_prompt") == before_nonempty.get("system_prompt")
            and accepted["http_status"] == 200
            and accepted["code"] == 0
            and before_empty.get("memory_type") == 1
            and after_empty.get("memory_type") == 3
            and after_empty.get("system_prompt") != before_empty.get("system_prompt")
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_nonempty_and_empty_type_update_fixtures",
                    "create_codes": [nonempty["response"]["code"], empty["response"]["code"]],
                    "add_message_code": added["code"],
                    "raw_sha256": added["raw_sha256"],
                },
                {
                    "name": "confirm_type_update_nonempty_precondition",
                    "message_readable": bool(readable["messages"]),
                    "poll_attempts": readable["attempts"],
                    "size_cache_positive": cache["exists"] and (cache["value"] or 0) > 0,
                    "raw_message_count": store["raw_message_count"],
                },
                {
                    "name": "reject_nonempty_and_accept_empty_memory_type_updates",
                    "nonempty_response": [rejected["http_status"], rejected["code"]],
                    "message_matches": message_fragment in str(rejected["message"] or ""),
                    "nonempty_type_and_prompt_unchanged": after_nonempty.get("memory_type") == before_nonempty.get("memory_type")
                    and after_nonempty.get("system_prompt") == before_nonempty.get("system_prompt"),
                    "empty_response": [accepted["http_status"], accepted["code"]],
                    "empty_bit_value": after_empty.get("memory_type"),
                    "empty_default_prompt_changed": after_empty.get("system_prompt") != before_empty.get("system_prompt"),
                    "raw_sha256": [rejected["raw_sha256"], accepted["raw_sha256"]],
                },
                {"name": "cleanup_memory_type_update_fixtures_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "nonempty_update": "reject_without_type_or_prompt_mutation",
                "empty_update": "bit_3_and_new_default_prompt",
            },
        }

    return _run_case(case_id, execute)


def run_mm_sup016() -> dict[str, Any]:
    case_id = "TC-MM-SUP-016"
    prefix = "fresh-mm-sup-016"
    fields = ("avatar", "description", "system_prompt", "user_prompt")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_empty_text_update_fixture", prefix)
        response = _update_memory(
            case_id,
            group,
            owner["auth"],
            "update_ordinary_text_fields_to_empty",
            fixture["id"],
            {field: "" for field in fields},
        )
        snapshot = _memory_snapshot(group, fixture["id"])
        config = _get_memory_config(case_id, group, owner["auth"], "get_empty_text_memory_config", fixture["id"])
        config_data = config["data"] if isinstance(config["data"], dict) else {}
        database_values = [snapshot.get(field) for field in fields]
        api_values = [config_data.get(field) for field in fields]
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "database_values": database_values,
            "api_values": api_values,
            "cleanup_succeeded": preclean and config["code"] == 0 and cleanup,
        }
        passed = fixture["response"]["code"] == 0 and ordinary_empty_text_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_and_update_four_ordinary_text_fields_to_empty",
                    "create_code": fixture["response"]["code"],
                    "update_response": [response["http_status"], response["code"]],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "verify_group_specific_physical_and_api_empty_semantics",
                    "database_all_empty_strings": database_values == ["", "", "", ""],
                    "database_all_null": database_values == [None, None, None, None],
                    "api_all_empty_strings": api_values == ["", "", "", ""],
                    "api_all_null": api_values == [None, None, None, None],
                    "raw_sha256": config["raw_sha256"],
                },
                {"name": "cleanup_empty_text_update_fixture_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "control_database_and_api": ["empty", "empty", "empty", "empty"],
                "experiment_database_and_api": ["null", "null", "null", "null"],
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-ORDINARY-TEXT-EMPTY-SEMANTICS-001",
                    "summary": f"{group} ordinary TextField empty-value semantics diverged from its backend contract",
                    "code_location": "api/db/db_models.py:Memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm_sup017() -> dict[str, Any]:
    case_id = "TC-MM-SUP-017"
    prefix = "fresh-mm-sup-017"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_memory_fixture(case_id, group, owner, "create_owner_message_fixture", prefix)
        added = _add_message(
            case_id,
            group,
            owner["auth"],
            "owner_add_message_for_forget",
            fixture["id"],
            marker=prefix,
        )
        readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            fixture["id"],
            label_prefix="poll_owner_message_for_forget",
        )
        message_id = int(readable["messages"][0]["message_id"]) if readable["messages"] else 0
        before = _message_document_snapshot(
            case_id,
            group,
            "owner_message_before_forget",
            owner["tenant_id"],
            fixture["id"],
            message_id,
        )
        forgotten = _delete_message(
            case_id,
            group,
            owner["auth"],
            "owner_forget_message",
            fixture["id"],
            message_id,
        )
        after = _message_document_snapshot(
            case_id,
            group,
            "owner_message_after_forget",
            owner["tenant_id"],
            fixture["id"],
            message_id,
        )
        identity_keys = (
            "id",
            "message_id",
            "memory_id",
            "agent_id",
            "session_id",
            "status",
            "content_sha256",
        )
        identity_unchanged = all(before.get(key) == after.get(key) for key in identity_keys)
        forget_at_changed = before.get("forget_at") != after.get("forget_at") and after.get("forget_at") not in {None, "", "-"}
        cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        observed = {
            "http_status": forgotten["http_status"],
            "code": forgotten["code"],
            "before_exists": before["exists"],
            "after_exists": after["exists"],
            "forget_at_changed": forget_at_changed,
            "identity_unchanged": identity_unchanged,
            "cleanup_succeeded": preclean and cleanup,
        }
        setup_ready = fixture["response"]["code"] == 0 and added["code"] == 0 and bool(readable["messages"]) and message_id > 0
        passed = setup_ready and message_forget_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "owner_create_memory_add_message_and_poll_readable",
                    "create_code": fixture["response"]["code"],
                    "add_code": added["code"],
                    "message_readable": bool(readable["messages"]),
                    "poll_attempts": readable["attempts"],
                    "message_id": message_id,
                    "raw_sha256": added["raw_sha256"],
                },
                {
                    "name": "owner_forget_message_through_api",
                    "http_status": forgotten["http_status"],
                    "code": forgotten["code"],
                    "raw_sha256": forgotten["raw_sha256"],
                },
                {
                    "name": "read_only_scoped_forget_timestamp_verification",
                    "message_exists_before_and_after": before["exists"] and after["exists"],
                    "forget_at_changed": forget_at_changed,
                    "identity_unchanged": identity_unchanged,
                    "raw_sha256": [before["raw_sha256"], after["raw_sha256"]],
                },
                {"name": "cleanup_owner_message_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "response": [200, 0],
                "forget_at": "set",
                "other_message_fields": "unchanged",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-OWNER-MESSAGE-FORGET-001",
                    "summary": f"{group} owner Message forget contract did not pass",
                    "code_location": "api/apps/services/memory_api_service.py:forget_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm_sup018() -> dict[str, Any]:
    case_id = "TC-MM-SUP-018"
    prefix = "fresh-mm-sup-018"
    email = "mm-sup-018-user-b@fresh.invalid"
    password = "Fresh-MM-SUP-018-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        user_b = _prepare_tagged_secondary_user(case_id, group, "user_b", email, password)
        membership = BASE._invite_and_accept_member(case_id, group, owner, user_b, email)
        fixture = _create_memory_fixture(case_id, group, owner, "create_team_message_fixture", prefix)
        permission = _update_memory(
            case_id,
            group,
            owner["auth"],
            "set_team_message_permission",
            fixture["id"],
            {"permissions": "team"},
        )
        permission_snapshot = _memory_snapshot(group, fixture["id"])
        added = _add_message(
            case_id,
            group,
            owner["auth"],
            "owner_add_team_message",
            fixture["id"],
            marker=prefix,
        )
        readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            fixture["id"],
            label_prefix="poll_team_message",
        )
        message_id = int(readable["messages"][0]["message_id"]) if readable["messages"] else 0
        before = _api_message_snapshot(
            case_id,
            group,
            owner["auth"],
            "team_message_before_forget",
            fixture["id"],
            message_id,
        )
        forgotten = _delete_message(
            case_id,
            group,
            user_b["auth"],
            "joined_user_forget_team_message",
            fixture["id"],
            message_id,
        )
        after = _api_message_snapshot(
            case_id,
            group,
            owner["auth"],
            "team_message_after_forget",
            fixture["id"],
            message_id,
        )
        identity_keys = (
            "message_id",
            "message_type",
            "source_id",
            "memory_id",
            "user_id",
            "agent_id",
            "session_id",
            "valid_at",
            "invalid_at",
            "status",
        )
        identity_unchanged = all(before.get(key) == after.get(key) for key in identity_keys)
        forget_at_changed = before.get("forget_at") != after.get("forget_at") and after.get("forget_at") not in {None, "", "-"}
        membership_snapshot = BASE._membership_snapshot(group, user_b["tenant_id"], owner["tenant_id"])
        membership_cleanup = BASE._remove_secondary_membership(case_id, group, owner["tenant_id"], user_b)
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = _cleanup_tagged_secondary_user(case_id, group, "user_b", email)
        observed = {
            "http_status": forgotten["http_status"],
            "code": forgotten["code"],
            "before_exists": before["exists"],
            "after_exists": after["exists"],
            "forget_at_changed": forget_at_changed,
            "identity_unchanged": identity_unchanged,
            "cleanup_succeeded": preclean and membership_cleanup["succeeded"] and memory_cleanup and user_cleanup["succeeded"],
        }
        setup_ready = (
            user_b["preclean"]
            and user_b["registration"]["code"] == user_b["login"]["code"] == 0
            and membership["invite"]["code"] == membership["accept"]["code"] == 0
            and membership_snapshot.get("role") == "normal"
            and fixture["response"]["code"] == 0
            and permission["code"] == 0
            and permission_snapshot.get("permissions") == "team"
            and added["code"] == 0
            and bool(readable["messages"])
        )
        passed = setup_ready and message_forget_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "join_user_b_and_create_team_memory_message",
                    "registration_code": user_b["registration"]["code"],
                    "invite_code": membership["invite"]["code"],
                    "accept_code": membership["accept"]["code"],
                    "membership_role": membership_snapshot.get("role"),
                    "permission": permission_snapshot.get("permissions"),
                    "add_code": added["code"],
                    "message_readable": bool(readable["messages"]),
                },
                {
                    "name": "joined_user_forget_team_message_through_api",
                    "http_status": forgotten["http_status"],
                    "code": forgotten["code"],
                    "message_id": message_id,
                    "raw_sha256": forgotten["raw_sha256"],
                },
                {
                    "name": "read_only_team_scoped_forget_verification",
                    "forget_at_changed": forget_at_changed,
                    "identity_unchanged": identity_unchanged,
                    "raw_sha256": [before["raw_sha256"], after["raw_sha256"]],
                },
                {
                    "name": "cleanup_team_membership_memory_and_user_through_apis",
                    "membership_cleanup": membership_cleanup["succeeded"],
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "joined_team_user_response": [200, 0],
                "forget_at": "set_for_target_only",
            },
        }

    return _run_case(case_id, execute)


def run_mm_sup019() -> dict[str, Any]:
    case_id = "TC-MM-SUP-019"
    prefix = "fresh-mm-sup-019"
    email = "mm-sup-019-user-b@fresh.invalid"
    password = "Fresh-MM-SUP-019-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        user_b = _prepare_tagged_secondary_user(case_id, group, "user_b", email, password)
        fixture = _create_memory_fixture(case_id, group, owner, "create_private_message_fixture", prefix)
        added = _add_message(
            case_id,
            group,
            owner["auth"],
            "owner_add_private_message",
            fixture["id"],
            marker=prefix,
        )
        readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            fixture["id"],
            label_prefix="poll_private_message",
        )
        message_id = int(readable["messages"][0]["message_id"]) if readable["messages"] else 0
        before = _api_message_snapshot(
            case_id,
            group,
            owner["auth"],
            "private_message_before_unauthorized_forget",
            fixture["id"],
            message_id,
        )
        denied = _delete_message(
            case_id,
            group,
            user_b["auth"],
            "unauthorized_user_forget_private_message",
            fixture["id"],
            message_id,
        )
        after = _api_message_snapshot(
            case_id,
            group,
            owner["auth"],
            "private_message_after_unauthorized_forget",
            fixture["id"],
            message_id,
        )
        identity_keys = (
            "exists",
            "message_id",
            "message_type",
            "source_id",
            "memory_id",
            "user_id",
            "agent_id",
            "session_id",
            "valid_at",
            "invalid_at",
            "forget_at",
            "status",
        )
        message_unchanged = all(before.get(key) == after.get(key) for key in identity_keys)
        membership_snapshot = BASE._membership_snapshot(group, user_b["tenant_id"], owner["tenant_id"])
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = _cleanup_tagged_secondary_user(case_id, group, "user_b", email)
        observed = {
            "http_status": denied["http_status"],
            "code": denied["code"],
            "message_exists_before": before["exists"],
            "message_unchanged": message_unchanged,
            "cleanup_succeeded": preclean and memory_cleanup and user_cleanup["succeeded"],
        }
        setup_ready = (
            user_b["preclean"]
            and user_b["registration"]["code"] == user_b["login"]["code"] == 0
            and membership_snapshot.get("count") == 0
            and fixture["response"]["code"] == 0
            and added["code"] == 0
            and bool(readable["messages"])
        )
        passed = setup_ready and message_denial_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_unjoined_user_and_private_owner_message",
                    "registration_code": user_b["registration"]["code"],
                    "login_code": user_b["login"]["code"],
                    "owner_membership_count": membership_snapshot.get("count"),
                    "add_code": added["code"],
                    "message_readable": bool(readable["messages"]),
                },
                {
                    "name": "deny_unauthorized_message_forget_as_not_found",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "message_id": message_id,
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "read_only_zero_message_mutation_verification",
                    "message_unchanged": message_unchanged,
                    "raw_sha256": [before["raw_sha256"], after["raw_sha256"]],
                },
                {
                    "name": "cleanup_private_memory_and_unauthorized_user_through_apis",
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {"response": [200, 404], "message_mutation": 0},
        }

    return _run_case(case_id, execute)


def run_mm_sup020() -> dict[str, Any]:
    case_id = "TC-MM-SUP-020"
    prefix = "fresh-mm-sup-020"
    collision_id = 123
    content_a = "fresh-mm-sup-020 collision content from memory a"
    content_b = "fresh-mm-sup-020 collision content from memory b"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        memory_a = _create_memory_fixture(case_id, group, owner, "create_collision_memory_a", f"{prefix}-a")
        memory_b = _create_memory_fixture(case_id, group, owner, "create_collision_memory_b", f"{prefix}-b")
        add_a = _add_message(
            case_id,
            group,
            owner["auth"],
            "add_legal_source_message_a",
            memory_a["id"],
            marker=f"{prefix}-source-a",
        )
        add_b = _add_message(
            case_id,
            group,
            owner["auth"],
            "add_legal_source_message_b",
            memory_b["id"],
            marker=f"{prefix}-source-b",
        )
        readable_a = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            memory_a["id"],
            label_prefix="poll_legal_source_message_a",
        )
        readable_b = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            memory_b["id"],
            label_prefix="poll_legal_source_message_b",
        )
        source_a_id = int(readable_a["messages"][0]["message_id"]) if readable_a["messages"] else 0
        source_b_id = int(readable_b["messages"][0]["message_id"]) if readable_b["messages"] else 0

        inserted = _collision_adapter_action(
            case_id,
            group,
            "insert_authorized_composite_id_collision_fixture",
            action="insert",
            tenant_id=owner["tenant_id"],
            memory_a_id=memory_a["id"],
            memory_b_id=memory_b["id"],
            source_a_id=source_a_id,
            source_b_id=source_b_id,
            content_a=content_a,
            content_b=content_b,
        )
        content_response = _get_message_content(
            case_id,
            group,
            owner["auth"],
            "get_memory_a_collision_message_content",
            memory_a["id"],
            collision_id,
        )
        api_data = content_response["data"] if isinstance(content_response["data"], dict) else {}
        after_api_probe = _collision_adapter_action(
            case_id,
            group,
            "probe_collision_pair_after_memory_a_api_read",
            action="probe",
            tenant_id=owner["tenant_id"],
            memory_a_id=memory_a["id"],
            memory_b_id=memory_b["id"],
            source_a_id=source_a_id,
            source_b_id=source_b_id,
            content_a=content_a,
            content_b=content_b,
        )
        fixture_cleanup = _collision_adapter_action(
            case_id,
            group,
            "cleanup_authorized_composite_id_collision_fixture",
            action="cleanup",
            tenant_id=owner["tenant_id"],
            memory_a_id=memory_a["id"],
            memory_b_id=memory_b["id"],
            source_a_id=source_a_id,
            source_b_id=source_b_id,
            content_a=content_a,
            content_b=content_b,
        )
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [memory_a["id"], memory_b["id"]])

        backend = str(inserted.get("backend") or "")
        backend_matches_group = "Infinity" in backend if group == "control" else "Gauss" in backend
        fixture_inserted = (
            inserted.get("source_vectors_present") is True
            and inserted.get("insert_error_count") == 0
            and inserted.get("physical_pair_count") == 2
            and inserted.get("physical_ids_exact") is True
            and inserted.get("contents_exact") is True
            and backend_matches_group
        )
        api_returns_a_only = (
            int(api_data.get("message_id") or 0) == collision_id and api_data.get("memory_id") == memory_a["id"] and api_data.get("content") == content_a and api_data.get("content") != content_b
        )
        b_after = after_api_probe.get("b") if isinstance(after_api_probe.get("b"), dict) else {}
        b_still_exact = (
            after_api_probe.get("physical_pair_count") == 2
            and after_api_probe.get("physical_ids_exact") is True
            and after_api_probe.get("contents_exact") is True
            and b_after.get("exists") is True
            and int(b_after.get("message_id") or 0) == collision_id
            and b_after.get("memory_id") == memory_b["id"]
        )
        setup_ready = (
            preclean
            and memory_a["response"]["code"] == memory_b["response"]["code"] == 0
            and add_a["code"] == add_b["code"] == 0
            and bool(readable_a["messages"])
            and bool(readable_b["messages"])
            and source_a_id > 0
            and source_b_id > 0
            and source_a_id != source_b_id
            and collision_id not in {source_a_id, source_b_id}
        )
        observed = {
            "fixture_inserted": fixture_inserted,
            "physical_pair_count": inserted.get("physical_pair_count"),
            "physical_ids_exact": inserted.get("physical_ids_exact"),
            "http_status": content_response["http_status"],
            "code": content_response["code"],
            "api_returns_a_only": api_returns_a_only,
            "b_still_exact": b_still_exact,
            "fixture_cleanup_succeeded": fixture_cleanup.get("physical_pair_count") == 0,
            "memory_cleanup_succeeded": memory_cleanup,
        }
        passed = setup_ready and message_collision_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_memories_and_legal_api_source_messages",
                    "create_codes": [memory_a["response"]["code"], memory_b["response"]["code"]],
                    "add_codes": [add_a["code"], add_b["code"]],
                    "messages_readable": [bool(readable_a["messages"]), bool(readable_b["messages"])],
                    "source_ids_distinct_and_not_fixture_id": source_a_id != source_b_id and collision_id not in {source_a_id, source_b_id},
                },
                {
                    "name": "insert_plan_authorized_collision_rows_through_message_adapter",
                    "authorized_fixture": True,
                    "backend": backend,
                    "backend_matches_group": backend_matches_group,
                    "source_vectors_present": inserted.get("source_vectors_present"),
                    "insert_error_count": inserted.get("insert_error_count"),
                    "physical_pair_count": inserted.get("physical_pair_count"),
                    "physical_ids_exact": inserted.get("physical_ids_exact"),
                    "contents_exact_by_hash": inserted.get("contents_exact"),
                    "raw_sha256": inserted["raw_sha256"],
                },
                {
                    "name": "get_memory_a_composite_id_content_without_memory_b_confusion",
                    "http_status": content_response["http_status"],
                    "code": content_response["code"],
                    "api_returns_memory_a_only": api_returns_a_only,
                    "memory_b_row_still_exact": b_still_exact,
                    "raw_sha256": [content_response["raw_sha256"], after_api_probe["raw_sha256"]],
                },
                {
                    "name": "cleanup_adapter_fixture_then_memories_through_api",
                    "adapter_pair_count_after_cleanup": fixture_cleanup.get("physical_pair_count"),
                    "adapter_cleanup_evidence_sha256": fixture_cleanup["raw_sha256"],
                    "memory_cleanup": memory_cleanup,
                },
            ],
            "oracle": {
                "physical_ids": ["<memory_a>_123", "<memory_b>_123"],
                "api_memory_a_content": "memory_a_only",
                "memory_b_survives_read": True,
                "fixture_cleanup": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-COMPOSITE-MESSAGE-ID-ISOLATION-001",
                    "summary": f"{group} Message composite ID isolation fixture did not pass",
                    "code_location": "memory/services/messages.py:get_by_message_id",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm_sup021() -> dict[str, Any]:
    case_id = "TC-MM-SUP-021"
    prefix = "fresh-mm-sup-021"
    email = "mm-sup-021-user-b@fresh.invalid"
    password = "Fresh-MM-SUP-021-User-B@1234"
    denied_agent = "agent-deny"
    denied_session = "session-deny"
    owner_agent = "fresh-mm-sup-021-owner-agent"
    owner_session = "fresh-mm-sup-021-owner-session"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        user_b = _prepare_tagged_secondary_user(case_id, group, "user_b", email, password)
        membership_snapshot = BASE._membership_snapshot(group, user_b["tenant_id"], owner["tenant_id"])
        fixture = _create_memory_fixture(case_id, group, owner, "create_denied_add_target_memory", prefix)
        store_before = _message_store_snapshot(group, owner["tenant_id"], fixture["id"])
        denied_rows_before = _message_filter_snapshot(
            case_id,
            group,
            "denied_message_rows_before_request",
            owner["tenant_id"],
            fixture["id"],
            denied_agent,
            denied_session,
        )
        cache_before = _memory_size_cache(group, fixture["id"])
        task_before = _memory_task_count(group, fixture["id"])
        denied = _request(
            case_id,
            group,
            "unjoined_user_add_message_to_private_memory",
            user_b["auth"],
            "POST",
            "/messages",
            payload={
                "memory_id": [fixture["id"]],
                "agent_id": denied_agent,
                "session_id": denied_session,
                "user_input": "should not be stored",
                "agent_response": "should not be stored",
            },
            timeout=180,
        )
        store_after_denied = _message_store_snapshot(group, owner["tenant_id"], fixture["id"])
        denied_rows_after = _message_filter_snapshot(
            case_id,
            group,
            "denied_message_rows_after_request",
            owner["tenant_id"],
            fixture["id"],
            denied_agent,
            denied_session,
        )
        cache_after_denied = _memory_size_cache(group, fixture["id"])
        task_after_denied = _memory_task_count(group, fixture["id"])

        owner_add = _request(
            case_id,
            group,
            "owner_add_control_message",
            owner["auth"],
            "POST",
            "/messages",
            payload={
                "memory_id": [fixture["id"]],
                "agent_id": owner_agent,
                "session_id": owner_session,
                "user_input": "owner control input",
                "agent_response": "owner control response",
            },
            timeout=180,
        )
        owner_readable = _wait_memory_messages(
            case_id,
            group,
            owner["auth"],
            fixture["id"],
            label_prefix="poll_owner_control_message",
        )
        owner_rows = _message_filter_snapshot(
            case_id,
            group,
            "owner_control_message_rows",
            owner["tenant_id"],
            fixture["id"],
            owner_agent,
            owner_session,
        )
        memory_cleanup = _cleanup_memory_ids(case_id, group, owner["auth"], [fixture["id"]])
        user_cleanup = _cleanup_tagged_secondary_user(case_id, group, "user_b", email)

        denied_store_delta = store_after_denied["raw_message_count"] - store_before["raw_message_count"]
        denied_cache_delta = 0 if cache_before == cache_after_denied else 1
        denied_task_delta = task_after_denied - task_before
        denied_message_exact = denied["message"] == "Some messages failed to add. Detail:Memory not found."
        observed = {
            "denied_http_status": denied["http_status"],
            "denied_code": denied["code"],
            "denied_message_exact": denied_message_exact,
            "denied_store_delta": denied_store_delta,
            "denied_cache_delta": denied_cache_delta,
            "denied_task_delta": denied_task_delta,
            "owner_code": owner_add["code"],
            "owner_message_readable": bool(owner_readable["messages"]),
            "owner_exact_raw_count": owner_rows["exact_raw_count"],
            "cleanup_succeeded": preclean and memory_cleanup and user_cleanup["succeeded"],
        }
        setup_ready = (
            user_b["preclean"]
            and user_b["registration"]["code"] == user_b["login"]["code"] == 0
            and membership_snapshot.get("count") == 0
            and fixture["response"]["code"] == 0
            and denied_rows_before["exact_raw_count"] == 0
            and denied_rows_after["exact_raw_count"] == 0
        )
        passed = setup_ready and denied_message_add_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_unjoined_user_and_private_message_target",
                    "registration_code": user_b["registration"]["code"],
                    "login_code": user_b["login"]["code"],
                    "owner_membership_count": membership_snapshot.get("count"),
                    "memory_create_code": fixture["response"]["code"],
                },
                {
                    "name": "reject_unjoined_user_message_add",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "message_exact": denied_message_exact,
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "verify_denied_store_cache_and_task_zero_delta",
                    "raw_message_delta": denied_store_delta,
                    "exact_denied_rows_before_after": [
                        denied_rows_before["exact_raw_count"],
                        denied_rows_after["exact_raw_count"],
                    ],
                    "cache_delta": denied_cache_delta,
                    "task_delta": denied_task_delta,
                    "raw_sha256": [
                        denied_rows_before["raw_sha256"],
                        denied_rows_after["raw_sha256"],
                    ],
                },
                {
                    "name": "owner_add_control_message_and_verify_exact_raw_row",
                    "owner_response": [owner_add["http_status"], owner_add["code"]],
                    "owner_message_readable": bool(owner_readable["messages"]),
                    "owner_exact_raw_count": owner_rows["exact_raw_count"],
                    "raw_sha256": [owner_add["raw_sha256"], owner_rows["raw_sha256"]],
                },
                {
                    "name": "cleanup_message_target_and_unjoined_user_through_apis",
                    "memory_cleanup": memory_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "denied_response": [200, 500],
                "denied_message": "Memory not found",
                "denied_deltas": {"store": 0, "cache": 0, "task": 0},
                "owner_control_raw_count": 1,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-DENIED-MESSAGE-ADD-WRITE-001",
                    "summary": f"{group} inaccessible Memory message-add zero-write contract did not pass",
                    "code_location": "api/apps/services/memory_api_service.py:add_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm_sup022() -> dict[str, Any]:
    case_id = "TC-MM-SUP-022"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        before = _tenant_memory_count(group, owner["tenant_id"])
        legal = _list_memories(
            case_id,
            group,
            owner["auth"],
            "legal_page_type_control",
            {"page": 1, "page_size": 50},
        )
        invalid = _list_memories(
            case_id,
            group,
            owner["auth"],
            "invalid_string_page_type",
            {"page": "abc", "page_size": 50},
        )
        after = _tenant_memory_count(group, owner["tenant_id"])
        observed = {
            "legal": [legal["http_status"], legal["code"]],
            "invalid": [invalid["http_status"], invalid["code"]],
            "metadata_delta": after - before,
        }
        passed = pagination_type_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "list_memories_with_legal_integer_pagination",
                    "http_status": legal["http_status"],
                    "code": legal["code"],
                    "raw_sha256": legal["raw_sha256"],
                },
                {
                    "name": "list_memories_with_invalid_string_page",
                    "http_status": invalid["http_status"],
                    "code": invalid["code"],
                    "value_error_disclosed": "invalid literal" in str(invalid["message"] or ""),
                    "raw_sha256": invalid["raw_sha256"],
                },
                {
                    "name": "read_only_invalid_pagination_zero_write_verification",
                    "metadata_delta": after - before,
                },
            ],
            "oracle": {
                "legal": [200, 0],
                "invalid_protocol": [200, 101],
                "metadata_delta": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-PAGINATION-FRAMEWORK-ERROR-001",
                    "summary": f"{group} invalid page type escaped route validation and did not return code 101",
                    "code_location": "api/apps/restful_apis/memory_api.py:list_memory",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_mm_sup023() -> dict[str, Any]:
    case_id = "TC-MM-SUP-023"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        before = _tenant_memory_count(group, owner["tenant_id"])
        boundary = _list_memories(
            case_id,
            group,
            owner["auth"],
            "legal_page_size_boundary_100",
            {"page": 1, "page_size": 100},
        )
        oversized = _list_memories(
            case_id,
            group,
            owner["auth"],
            "reject_page_size_101",
            {"page": 1, "page_size": 101},
        )
        negative = _list_memories(
            case_id,
            group,
            owner["auth"],
            "reject_negative_page_size",
            {"page": 1, "page_size": -1},
        )
        zero = _list_memories(
            case_id,
            group,
            owner["auth"],
            "reject_zero_page_size",
            {"page": 1, "page_size": 0},
        )
        after = _tenant_memory_count(group, owner["tenant_id"])
        observed = {
            "boundary": [boundary["http_status"], boundary["code"]],
            "invalid": [
                [oversized["http_status"], oversized["code"]],
                [negative["http_status"], negative["code"]],
                [zero["http_status"], zero["code"]],
            ],
            "metadata_delta": after - before,
        }
        passed = pagination_bounds_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "list_memories_at_legal_page_size_boundary",
                    "http_status": boundary["http_status"],
                    "code": boundary["code"],
                    "raw_sha256": boundary["raw_sha256"],
                },
                {
                    "name": "exercise_oversized_negative_and_zero_page_sizes",
                    "responses": observed["invalid"],
                    "raw_sha256": [
                        oversized["raw_sha256"],
                        negative["raw_sha256"],
                        zero["raw_sha256"],
                    ],
                },
                {
                    "name": "read_only_page_size_boundary_zero_write_verification",
                    "metadata_delta": after - before,
                },
            ],
            "oracle": {
                "boundary_100": [200, 0],
                "invalid_101_negative_zero": [[200, 101], [200, 101], [200, 101]],
                "metadata_delta": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MM-PAGE-SIZE-BOUNDARY-VALIDATION-001",
                    "summary": f"{group} page_size upper/lower bounds did not use code 101 validation",
                    "code_location": "api/utils/pagination_utils.py:validate_rest_api_page_size",
                }
            ],
        }

    return _run_case(case_id, execute)


RUNNERS: dict[str, Callable[[], dict[str, Any]]] = {
    "TC-MM-001": run_mm001,
    "TC-MM-002": run_mm002,
    "TC-MM-003": run_mm003,
    "TC-MM-004": run_mm004,
    "TC-MM-005": run_mm005,
    "TC-MM-006": run_mm006,
    "TC-MM-007": run_mm007,
    "TC-MM-008": run_mm008,
    "TC-MM-009": run_mm009,
    "TC-MM-010": run_mm010,
    "TC-MM-011": run_mm011,
    "TC-MM-012": run_mm012,
    "TC-MM-013": run_mm013,
    "TC-MM-014": run_mm014,
    "TC-MM-015": run_mm015,
    "TC-MM-016": run_mm016,
    "TC-MM-017": run_mm017,
    "TC-MM-018": run_mm018,
    "TC-MM-019": run_mm019,
    "TC-MM-020": run_mm020,
    "TC-MM-021": run_mm021,
    "TC-MM-022": run_mm022,
    "TC-MM-023": run_mm023,
    "TC-MM-024": run_mm024,
    "TC-MM-SUP-001": run_mm_sup001,
    "TC-MM-SUP-002": run_mm_sup002,
    "TC-MM-SUP-003": run_mm_sup003,
    "TC-MM-SUP-004": run_mm_sup004,
    "TC-MM-SUP-005": run_mm_sup005,
    "TC-MM-SUP-006": run_mm_sup006,
    "TC-MM-SUP-007": run_mm_sup007,
    "TC-MM-SUP-008": run_mm_sup008,
    "TC-MM-SUP-009": run_mm_sup009,
    "TC-MM-SUP-010": run_mm_sup010,
    "TC-MM-SUP-011": run_mm_sup011,
    "TC-MM-SUP-012": run_mm_sup012,
    "TC-MM-SUP-013": run_mm_sup013,
    "TC-MM-SUP-014": run_mm_sup014,
    "TC-MM-SUP-015": run_mm_sup015,
    "TC-MM-SUP-016": run_mm_sup016,
    "TC-MM-SUP-017": run_mm_sup017,
    "TC-MM-SUP-018": run_mm_sup018,
    "TC-MM-SUP-019": run_mm_sup019,
    "TC-MM-SUP-020": run_mm_sup020,
    "TC-MM-SUP-021": run_mm_sup021,
    "TC-MM-SUP-022": run_mm_sup022,
    "TC-MM-SUP-023": run_mm_sup023,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fresh Memory metadata cases")
    parser.add_argument("--case", choices=sorted(CASE_TITLES), required=True)
    args = parser.parse_args()
    if args.case not in RUNNERS:
        raise SystemExit(f"runner not implemented yet: {args.case}")
    result = RUNNERS[args.case]()
    print(
        json.dumps(
            {
                "case_id": result["case_id"],
                "pair_status": result["pair_status"],
                "group_statuses": {item["group"]: item["status"] for item in result["groups"]},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
