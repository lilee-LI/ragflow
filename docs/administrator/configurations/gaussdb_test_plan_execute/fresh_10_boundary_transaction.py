#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

try:
    from api.db import db_error_utils as ERROR_UTILS
except ImportError:
    from api.db import gaussdb_error_utils as ERROR_UTILS
from common.doc_store.gaussdb_conn_base import GaussDBDDLBuilder
from common.doc_store.gaussdb_conn_pool import (
    GaussDBConfig,
    GaussDBConnectionPool,
    load_gaussdb_config,
)


EXECUTE_DIR = Path(__file__).resolve().parent
CASE_IDS = (
    "TC-FR-027",
    "TC-FR-037",
    "TC-FR-038",
    "TC-FR-044",
    "TC-FR-045",
)
CONCURRENT_MESSAGE_COUNT = 6


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
    EXECUTE_DIR / "fresh_10_fault_recovery_common.py",
    "fresh_10_fault_recovery_common_runtime",
)
CONNECTION = _load_module(
    EXECUTE_DIR / "fresh_10_connection_recovery.py",
    "fresh_10_connection_recovery_runtime",
)


def deterministic_schema_injection_observation() -> dict[str, Any]:
    from common import settings
    from common.doc_store import gaussdb_conn_pool

    malicious = "public;search_path=attacker"
    connection_attempts = 0
    original_pool = gaussdb_conn_pool.psycopg2_pool.ThreadedConnectionPool

    def forbidden_pool(*args, **kwargs):
        nonlocal connection_attempts
        connection_attempts += 1
        raise AssertionError("connection must not be attempted")

    gaussdb_conn_pool.psycopg2_pool.ThreadedConnectionPool = forbidden_pool
    metadata_error = None
    docstore_error = None
    try:
        try:
            settings._normalize_gaussdb_metadata_schema(malicious)
        except Exception as error:
            metadata_error = type(error).__name__
        try:
            load_gaussdb_config(
                {
                    "config": {
                        "host": "127.0.0.1",
                        "port": 1,
                        "database": "db",
                        "user": "user",
                        "password": "schema-probe-value",
                        "schema": malicious,
                    }
                }
            )
        except Exception as error:
            docstore_error = type(error).__name__
    finally:
        gaussdb_conn_pool.psycopg2_pool.ThreadedConnectionPool = original_pool
    return {
        "metadata_error": metadata_error,
        "docstore_error": docstore_error,
        "connection_attempts": connection_attempts,
    }


def identifier_injection_observation() -> dict[str, Any]:
    builder = GaussDBDDLBuilder("public")
    invalid = (
        "bad-name",
        "bad;drop_table",
        'bad"quoted',
        "a" * 64,
    )
    classes: list[str] = []
    database_sends = 0
    for value in invalid:
        try:
            builder.validate_identifier(value)
            database_sends += 1
        except Exception as error:
            classes.append(type(error).__name__)
    return {
        "invalid_count": len(invalid),
        "invalid_error_classes": classes,
        "database_sends": database_sends,
    }


class _PermissionCursor:
    def __init__(self, flags: tuple[bool, bool]):
        self.flags = flags
        self.closed = False
        self.execute_calls = 0

    def execute(self, _statement, _params):
        self.execute_calls += 1

    def fetchone(self):
        return self.flags

    def close(self):
        self.closed = True


class _PermissionConnection:
    def __init__(self, flags: tuple[bool, bool]):
        self.cursor_object = _PermissionCursor(flags)

    def cursor(self):
        return self.cursor_object


def permission_matrix_observation(has_usage: bool, has_create: bool) -> dict[str, Any]:
    password = "permission-password-value"
    connection = _PermissionConnection((has_usage, has_create))
    pool = object.__new__(GaussDBConnectionPool)
    pool.config = GaussDBConfig("127.0.0.1", 1, "db", "permission_user", password, "public")
    pool.resolved_schema = "public"
    put_calls = 0

    def get_conn():
        return connection

    def put_conn(_connection):
        nonlocal put_calls
        put_calls += 1

    pool.get_conn = get_conn
    pool.put_conn = put_conn
    error_class = None
    message = ""
    try:
        GaussDBConnectionPool.check_schema_access(pool)
    except Exception as error:
        error_class = type(error).__name__
        message = str(error)
    missing = [privilege for privilege in ("USAGE", "CREATE") if privilege in message]
    return {
        "missing": missing,
        "error_class": error_class,
        "put_calls": put_calls,
        "cursor_closed": connection.cursor_object.closed,
        "password_present": password in message,
    }


def storage_row_identity(document: dict[str, Any], memory_id: str, message_id: int) -> dict[str, Any]:
    if not document:
        return {"id": "", "retrieved": False}
    return {
        "id": str(document.get("id") or f"{memory_id}_{message_id}"),
        "retrieved": True,
    }


def concurrent_memory_contract_ok(observed: dict[str, Any]) -> bool:
    count = int(observed.get("request_count") or 0)
    dimensions = observed.get("vector_dimensions")
    return (
        count > 0
        and observed.get("accepted_count") == count
        and observed.get("raw_count") == count
        and observed.get("unique_message_ids") == count
        and observed.get("unique_row_ids") == count
        and observed.get("retrieved_count") == count
        and isinstance(dimensions, list)
        and len(dimensions) == count
        and bool(dimensions)
        and len(set(int(item) for item in dimensions)) == 1
        and int(dimensions[0]) > 0
        and observed.get("vector_real_count") == count
        and observed.get("cache_value") == observed.get("calculated_size")
        and observed.get("cleanup_succeeded") is True
    )


def transaction_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("conflict_code") == observed.get("expected_code")
        and observed.get("rows_after_rollback") == 0
        and observed.get("recovery_value") == 1
        and observed.get("cleanup_succeeded") is True
    )


def _catalog_snapshot(group: str) -> dict[str, int]:
    connection = COMMON.open_metadata_database(group)
    try:
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()")
            else:
                cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema()")
            table_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM tenant")
            tenant_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {"table_count": table_count, "tenant_count": tenant_count}


def _control_gauss_config_isolation() -> dict[str, Any]:
    manager = COMMON.service_manager()
    environment = COMMON.group_environment("control")
    environment["DB_TYPE"] = "mysql"
    environment["GAUSSDB_METADATA_SCHEMA"] = "public;search_path=attacker"
    script = r"""
import json, os
from common import settings
config = settings.load_database_config("mysql")
print("__FRESH_RESULT__" + json.dumps({
    "db_type": os.environ.get("DB_TYPE", "").lower(),
    "mysql_config_loaded": isinstance(config, dict) and bool(config),
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script],
        cwd=manager.PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    marker = next(
        (line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__")),
        None,
    )
    return {
        "exit_code": completed.returncode,
        "result": json.loads(marker) if marker is not None else {},
    }


def _memory_ids_by_prefix(group: str, tenant_id: str, prefix: str) -> list[str]:
    connection = COMMON.open_metadata_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM memory WHERE tenant_id=%s AND LOWER(name) LIKE %s ORDER BY id",
                (tenant_id, prefix.lower() + "%"),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _memory_count(group: str, memory_id: str) -> int:
    connection = COMMON.open_metadata_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM memory WHERE id=%s", (memory_id,))
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _model_ids(group: str, tenant_id: str) -> dict[str, str]:
    connection = COMMON.open_metadata_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT embd_id,llm_id FROM tenant WHERE id=%s", (tenant_id,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None or not row[0] or not row[1]:
        raise COMMON.CaseBlocked(f"{group} configured Memory models are missing")
    return {"embd_id": str(row[0]), "llm_id": str(row[1])}


def _delete_memory(case_id: str, group: str, owner: dict[str, str], memory_id: str, label: str) -> bool:
    if not memory_id or _memory_count(group, memory_id) == 0:
        return True
    response = COMMON.http_request(
        case_id,
        group,
        label,
        "DELETE",
        f"/memories/{memory_id}",
        auth=owner["auth"],
    )
    return response.get("code") in {0, 404} and _memory_count(group, memory_id) == 0


def _preclean_memory_prefix(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
) -> bool:
    succeeded = True
    for index, memory_id in enumerate(_memory_ids_by_prefix(group, owner["tenant_id"], prefix), 1):
        succeeded = _delete_memory(case_id, group, owner, memory_id, f"preclean_memory_{index}") and succeeded
    return succeeded and not _memory_ids_by_prefix(group, owner["tenant_id"], prefix)


def _store_snapshot(group: str, tenant_id: str, memory_id: str) -> dict[str, Any]:
    manager = COMMON.service_manager()
    payload = json.dumps({"tenant_id": tenant_id, "memory_id": memory_id}, sort_keys=True)
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService
p = json.loads(sys.argv[1])
listed = MessageService.list_message(
    p["tenant_id"], p["memory_id"], page=1, page_size=100
)
rows = []
calculated_size = 0
for item in listed.get("message_list", []):
    message_id = int(item.get("message_id") or 0)
    doc = MessageService.get_by_message_id(
        p["memory_id"], message_id, p["tenant_id"]
    ) or {}
    embed = list(doc.get("content_embed") or [])
    calculated_size += MessageService.calculate_message_size(doc)
    rows.append({
        "reported_id": str(doc.get("id") or ""),
        "retrieved": bool(doc),
        "message_id": message_id,
        "vector_dimension": len(embed),
        "vector_real": bool(embed) and any(float(value) != 0.0 for value in embed),
    })
conn = settings.msgStoreConn
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
shared_pool = getattr(conn, "pool", None)
if callable(getattr(shared_pool, "close_all", None)):
    shared_pool.close_all()
print("__FRESH_RESULT__" + json.dumps({
    "backend": type(conn).__name__,
    "raw_count": len(rows),
    "rows": sorted(rows, key=lambda row: row["message_id"]),
    "calculated_size": calculated_size,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, payload],
        cwd=manager.PROJECT_ROOT,
        env=COMMON.group_environment(group),
        capture_output=True,
        text=True,
        timeout=150,
        check=False,
    )
    marker = next(
        (line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__")),
        None,
    )
    if completed.returncode != 0 or marker is None:
        raise COMMON.CaseBlocked(f"{group} current Memory Store probe did not return a result")
    result = json.loads(marker)
    rows = result.get("rows") if isinstance(result.get("rows"), list) else []
    for row in rows:
        reported_id = str(row.pop("reported_id", "") or "")
        retrieved = row.pop("retrieved", False) is True
        identity = storage_row_identity(
            {"id": reported_id} if retrieved else {},
            memory_id,
            int(row.get("message_id") or 0),
        )
        row.update(identity)
    return result


def _wait_for_raw_messages(
    case_id: str,
    group: str,
    owner: dict[str, str],
    memory_id: str,
    expected: int,
    *,
    timeout: float = 240,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempts = 0
    last_total = 0
    while time.monotonic() < deadline:
        attempts += 1
        response = COMMON.http_request(
            case_id,
            group,
            f"wait_messages_{attempts:03d}",
            "GET",
            f"/memories/{memory_id}",
            auth=owner["auth"],
            params={"page": 1, "page_size": 100},
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        messages = data.get("messages") if isinstance(data.get("messages"), dict) else {}
        last_total = int(messages.get("total_count") or 0)
        if response.get("code") == 0 and last_total >= expected:
            return {"attempts": attempts, "total": last_total}
        time.sleep(0.5)
    return {"attempts": attempts, "total": last_total}


def _run_concurrent_memory(group: str) -> dict[str, Any]:
    case_id = "TC-FR-027"
    owner = COMMON.owner(case_id, group)
    prefix = f"fresh10-fr027-{group}"
    preclean = _preclean_memory_prefix(case_id, group, owner, prefix)
    model_ids = _model_ids(group, owner["tenant_id"])
    create = COMMON.http_request(
        case_id,
        group,
        "create_concurrent_memory",
        "POST",
        "/memories",
        auth=owner["auth"],
        payload={
            "name": prefix,
            "memory_type": ["raw"],
            **model_ids,
        },
    )
    data = create.get("data") if isinstance(create.get("data"), dict) else {}
    memory_id = str(data.get("id") or "")
    accepted: list[dict[str, Any]] = []
    snapshot: dict[str, Any] = {"raw_count": 0, "rows": [], "calculated_size": 0}
    cache_value = None
    cleanup_succeeded = False
    observed: dict[str, Any] = {
        "preclean_succeeded": preclean,
        "create_response": [create.get("http_status"), create.get("code")],
        "request_count": CONCURRENT_MESSAGE_COUNT,
        "accepted_count": 0,
        "raw_count": 0,
        "unique_message_ids": 0,
        "unique_row_ids": 0,
        "retrieved_count": 0,
        "vector_dimensions": [],
        "vector_real_count": 0,
        "cache_value": None,
        "calculated_size": 0,
        "backend": None,
    }
    try:
        if create.get("code") != 0 or not memory_id:
            raise COMMON.CaseBlocked(f"{group} could not create the concurrent Memory fixture")

        def submit(index: int):
            return COMMON.http_request(
                case_id,
                group,
                f"concurrent_message_{index:02d}",
                "POST",
                "/messages",
                auth=owner["auth"],
                payload={
                    "memory_id": [memory_id],
                    "agent_id": f"fr027-{group}-agent-{index:02d}",
                    "session_id": f"fr027-{group}-session-{index:02d}",
                    "user_input": f"concurrent input {index:02d}",
                    "agent_response": f"concurrent response {index:02d}",
                },
                timeout=240,
            )

        with ThreadPoolExecutor(max_workers=CONCURRENT_MESSAGE_COUNT) as executor:
            futures = [executor.submit(submit, index) for index in range(CONCURRENT_MESSAGE_COUNT)]
            for future in as_completed(futures):
                accepted.append(future.result())

        wait = _wait_for_raw_messages(
            case_id,
            group,
            owner,
            memory_id,
            CONCURRENT_MESSAGE_COUNT,
        )
        snapshot = _store_snapshot(group, owner["tenant_id"], memory_id)
        redis = COMMON.redis_client(group)
        cache_key = f"memory_{memory_id}"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            raw_value = redis.get(cache_key)
            cache_value = int(raw_value) if raw_value is not None else None
            if cache_value == int(snapshot.get("calculated_size") or 0):
                break
            time.sleep(0.25)
        rows = snapshot.get("rows") if isinstance(snapshot.get("rows"), list) else []
        observed = {
            "preclean_succeeded": preclean,
            "create_response": [create.get("http_status"), create.get("code")],
            "request_count": CONCURRENT_MESSAGE_COUNT,
            "accepted_count": sum(item.get("http_status") == 200 and item.get("code") == 0 and item.get("message") == "All add to task." for item in accepted),
            "wait_attempts": wait["attempts"],
            "raw_count": int(snapshot.get("raw_count") or 0),
            "unique_message_ids": len({int(row.get("message_id") or 0) for row in rows}),
            "unique_row_ids": len({str(row.get("id") or "") for row in rows}),
            "retrieved_count": sum(row.get("retrieved") is True for row in rows),
            "vector_dimensions": [int(row.get("vector_dimension") or 0) for row in rows],
            "vector_real_count": sum(row.get("vector_real") is True for row in rows),
            "cache_value": cache_value,
            "calculated_size": int(snapshot.get("calculated_size") or 0),
            "backend": snapshot.get("backend"),
        }
    finally:
        cleanup_succeeded = _delete_memory(case_id, group, owner, memory_id, "cleanup_concurrent_memory")
        if memory_id:
            redis = COMMON.redis_client(group)
            redis.delete(f"memory_{memory_id}")
            try:
                remaining = _store_snapshot(group, owner["tenant_id"], memory_id)
                cleanup_succeeded = cleanup_succeeded and int(remaining.get("raw_count") or 0) == 0 and redis.get(f"memory_{memory_id}") is None
            except COMMON.CaseBlocked:
                cleanup_succeeded = False
    observed["cleanup_succeeded"] = cleanup_succeeded
    return observed


def _live_schema_access(group: str) -> bool:
    if group == "control":
        connection = COMMON.open_metadata_database(group)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return int(cursor.fetchone()[0]) == 1
        finally:
            connection.close()
    host, port = CONNECTION._private_proxy_target("experiment", "docstore")
    pool = GaussDBConnectionPool(
        config=CONNECTION._docstore_gauss_config(host, port),
        minconn=1,
        maxconn=2,
    )
    try:
        pool.check_schema_access()
        return True
    finally:
        pool.close_all()


def _transaction_observation(group: str) -> dict[str, Any]:
    table = f"{COMMON.RESOURCE_PREFIX}_045_{group}"
    connection = COMMON.open_metadata_database(group, writable=True)
    caught: Exception | None = None
    rows_after = -1
    recovery_value = None
    cleanup = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
            cursor.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, unique_value INTEGER UNIQUE)")
        connection.commit()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"INSERT INTO {table} (id,unique_value) VALUES (1,100)")
                cursor.execute(f"INSERT INTO {table} (id,unique_value) VALUES (2,200)")
                cursor.execute(f"INSERT INTO {table} (id,unique_value) VALUES (3,100)")
            connection.commit()
        except Exception as error:
            caught = error
            connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            rows_after = int(cursor.fetchone()[0])
            cursor.execute("SELECT 1")
            recovery_value = int(cursor.fetchone()[0])
        connection.rollback()
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            connection.commit()
            cleanup = True
        finally:
            connection.close()
    code = ERROR_UTILS.sqlstate_from_exception(caught) if group == "experiment" and caught is not None else ERROR_UTILS.mysql_errno_from_exception(caught) if caught is not None else None
    return {
        "conflict_code": str(code) if code is not None else None,
        "expected_code": "23505" if group == "experiment" else "1062",
        "error_class": type(caught).__name__ if caught else None,
        "rows_after_rollback": rows_after,
        "recovery_value": recovery_value,
        "cleanup_succeeded": cleanup,
    }


def _run_027_group(group: str) -> dict[str, Any]:
    observed = _run_concurrent_memory(group)
    cache_exact = observed.get("cache_value") == observed.get("calculated_size")
    if not cache_exact:
        finding_id = "TC-FR-027-MEMORY-SIZE-CACHE"
        summary = f"{group} Memory size cache differs from the persisted-message product formula"
        code_location = "memory/services/messages.py:MessageService.calculate_message_size"
    else:
        finding_id = "TC-FR-027-CONCURRENT-MEMORY"
        summary = f"{group} concurrent Memory writes lost or duplicated messages"
        code_location = "memory/utils/gaussdb_conn.py:GaussDBMemoryConnection.insert"
    return COMMON.pass_or_fail(
        concurrent_memory_contract_ok(observed) and observed.get("preclean_succeeded") is True and observed.get("create_response") == [200, 0],
        [{"name": "submit_unique_messages_concurrently_and_verify_store", **observed}],
        {
            "message_count": CONCURRENT_MESSAGE_COUNT,
            "ids_unique": True,
            "vector_dimension_consistent": True,
            "cache_formula_exact": True,
        },
        finding_id,
        summary,
        code_location=code_location,
    )


def _run_037_group(group: str) -> dict[str, Any]:
    before = _catalog_snapshot(group)
    if group == "control":
        isolation = _control_gauss_config_isolation()
        probe: dict[str, Any] = {"not_applicable": True}
        passed_probe = isolation["exit_code"] == 0 and isolation["result"].get("db_type") == "mysql" and isolation["result"].get("mysql_config_loaded") is True
    else:
        isolation = {"not_applicable": True}
        probe = deterministic_schema_injection_observation()
        passed_probe = probe["metadata_error"] == "ValueError" and probe["docstore_error"] == "InvalidGaussDBConfig" and probe["connection_attempts"] == 0
    after = _catalog_snapshot(group)
    passed = passed_probe and before == after
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "capture_catalog_canary_before", **before},
            {"name": "exercise_group_specific_schema_config", "isolation": isolation, "probe": probe},
            {"name": "prove_catalog_canary_unchanged", **after},
        ],
        {"reject_before_connection": group == "experiment", "catalog_unchanged": True},
        "TC-FR-037-SCHEMA-INJECTION",
        f"{group} schema injection was not isolated or changed the catalog",
        code_location="common/settings.py:_normalize_gaussdb_metadata_schema",
    )


def _run_038_group(group: str) -> dict[str, Any]:
    before = _catalog_snapshot(group)
    probe = identifier_injection_observation()
    after = _catalog_snapshot(group)
    passed = probe["invalid_count"] == 4 and probe["invalid_error_classes"] == ["InvalidGaussDBObjectName"] * 4 and probe["database_sends"] == 0 and before == after
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "capture_catalog_canary_before", **before},
            {"name": "reject_invalid_index_identifiers", **probe},
            {"name": "prove_catalog_canary_unchanged", **after},
        ],
        {"invalid_inputs": 4, "database_sends": 0, "catalog_unchanged": True},
        "TC-FR-038-INDEX-INJECTION",
        f"{group} invalid index identifier reached the database or changed catalog state",
        code_location="common/doc_store/gaussdb_conn_base.py:GaussDBDDLBuilder.validate_identifier",
    )


def _run_044_group(group: str) -> dict[str, Any]:
    live = _live_schema_access(group)
    usage = permission_matrix_observation(False, True)
    create = permission_matrix_observation(True, False)
    both = permission_matrix_observation(False, False)
    matrix = [usage, create, both]
    passed = (
        live is True
        and [item["missing"] for item in matrix] == [["USAGE"], ["CREATE"], ["USAGE", "CREATE"]]
        and all(item["error_class"] == "GaussDBPermissionError" for item in matrix)
        and all(item["put_calls"] == 1 for item in matrix)
        and all(item["cursor_closed"] is True for item in matrix)
        and all(item["password_present"] is False for item in matrix)
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "live_positive_schema_access", "succeeded": live},
            {"name": "simulate_missing_usage_create_permissions", "matrix": matrix},
        ],
        {"positive": True, "missing_matrix": [["USAGE"], ["CREATE"], ["USAGE", "CREATE"]]},
        "TC-FR-044-SCHEMA-PERMISSION",
        f"{group} schema permission check did not report missing grants or return its connection",
        code_location="common/doc_store/gaussdb_conn_pool.py:GaussDBConnectionPool.check_schema_access",
    )


def _run_045_group(group: str) -> dict[str, Any]:
    observed = _transaction_observation(group)
    return COMMON.pass_or_fail(
        transaction_contract_ok(observed),
        [{"name": "trigger_unique_conflict_and_rollback_whole_transaction", **observed}],
        {"rows_after_rollback": 0, "connection_recovered": True},
        "TC-FR-045-TRANSACTION-ROLLBACK",
        f"{group} failed transaction left rows behind or the connection aborted",
        code_location="common/doc_store/gaussdb_conn_pool.py:GaussDBConnectionPool.put_conn",
    )


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-FR-027": _run_027_group,
    "TC-FR-037": _run_037_group,
    "TC-FR-038": _run_038_group,
    "TC-FR-044": _run_044_group,
    "TC-FR-045": _run_045_group,
}


def get_runners(titles: dict[str, str]) -> dict[str, Callable[[], dict[str, Any]]]:
    result: dict[str, Callable[[], dict[str, Any]]] = {}
    for case_id in CASE_IDS:
        if case_id not in titles:
            continue
        execute_group = GROUP_RUNNERS[case_id]
        title = titles[case_id]
        result[case_id] = lambda current_id=case_id, current_title=title, current_execute=execute_group: COMMON.run_paired_case(current_id, current_title, current_execute)
    return result
