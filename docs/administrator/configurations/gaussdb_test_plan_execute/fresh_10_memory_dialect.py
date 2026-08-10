#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from common.doc_store.gaussdb_conn_base import GaussDBDDLBuilder
from memory.utils.gaussdb_conn import vector_literal, zero_vector_literal


EXECUTE_DIR = Path(__file__).resolve().parent
CASE_IDS = ("TC-FR-051", "TC-FR-052", "TC-FR-053")


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


def _error_class(call: Callable[[], Any]) -> str | None:
    try:
        call()
    except Exception as error:
        return type(error).__name__
    return None


def vector_literal_boundary_observation() -> dict[str, Any]:
    return {
        "none_error": _error_class(lambda: vector_literal(None, 3)),
        "empty_error": _error_class(lambda: vector_literal([], 3)),
        "wrong_dimension_error": _error_class(lambda: vector_literal([1, 2], 3)),
        "valid_literal": vector_literal("[1,2,3]", 3),
        "zero_literal": zero_vector_literal(3),
    }


def memory_vector_contract_ok(observed: dict[str, Any], *, cross_dim_required: bool) -> bool:
    base = (
        observed.get("invalid_failures") == 3
        and observed.get("rows_after_invalid") == 1
        and observed.get("old_row_preserved") is True
        and observed.get("real_dimension") == 3
        and observed.get("cleanup_succeeded") is True
    )
    if not base or not cross_dim_required:
        return base
    return observed.get("real_empty_flag") is False and observed.get("unused_zero_vector") is True and observed.get("unused_empty_flag") is True and observed.get("cross_dimension") == 2


def identifier_limit_observation() -> dict[str, Any]:
    builder = GaussDBDDLBuilder("public")
    accepted = builder.validate_identifier("a" * 63)
    rejected_64 = _error_class(lambda: builder.validate_identifier("a" * 64))
    rejected_invalid = _error_class(lambda: builder.validate_identifier("bad-name"))
    table = "t" * 63
    suffix = "s" * 63
    first = builder.index_name(table, suffix)
    second = builder.index_name(table, suffix)
    unhashed = f"idx_gdb_{table}_{suffix}"
    return {
        "accepted_length": len(accepted),
        "rejected_64": rejected_64,
        "rejected_invalid": rejected_invalid,
        "index_name": first,
        "index_name_length": len(first),
        "index_name_stable": first == second,
        "index_name_hashed": first != unhashed and first.count("_") >= 3,
    }


def memory_probe_payload(group: str) -> dict[str, Any]:
    return {
        "group": group,
        "index_name": f"{COMMON.RESOURCE_PREFIX}_051_{group}",
        "memory_id": f"fr051{group}",
        "status": 1,
    }


def _run_memory_subprocess(group: str) -> dict[str, Any]:
    manager = COMMON.service_manager()
    payload = json.dumps(memory_probe_payload(group), sort_keys=True)
    script = r"""
import json, sys
from common import settings

p = json.loads(sys.argv[1])
settings.init_settings()
store = settings.msgStoreConn
index_name = p["index_name"]
memory_id = p["memory_id"]
base_id = 9105100 if p["group"] == "control" else 9205100

def message(offset, vector, content):
    message_id = base_id + offset
    return {
        "id": f"{memory_id}_{message_id}",
        "message_id": message_id,
        "message_type": "raw",
        "source_id": 0,
        "memory_id": memory_id,
        "user_id": "",
        "agent_id": f"fr051-{p['group']}",
        "session_id": f"fr051-{offset}",
        "content": content,
        "valid_at": "2026-07-16 00:00:00",
        "invalid_at": None,
        "forget_at": None,
        "status": p["status"],
        "content_embed": vector,
    }

def attempt(documents):
    try:
        failures = store.insert(documents, index_name, memory_id)
        return bool(failures)
    except Exception:
        return True

try:
    store.delete_idx(index_name, memory_id)
except Exception:
    pass

cleanup_succeeded = False
result = {
    "backend": type(store).__name__,
    "invalid_failures": 0,
    "rows_after_invalid": -1,
    "old_row_preserved": False,
    "real_dimension": 0,
    "real_empty_flag": None,
    "unused_zero_vector": None,
    "unused_empty_flag": None,
    "cross_dimension": None,
}
try:
    valid = message(1, [0.1, 0.2, 0.3], "stable old row")
    valid_failures = store.insert([valid], index_name, memory_id)
    if valid_failures:
        raise RuntimeError("valid vector fixture was rejected")
    before = store.get(valid["id"], index_name, [memory_id]) or {}
    before_vector = before.get("content_embed")
    result["real_dimension"] = len(before_vector) if before_vector is not None else 0

    invalid_none = message(2, None, "invalid none")
    invalid_empty = message(3, [], "invalid empty")
    mixed_a = message(4, [0.4, 0.5, 0.6], "mixed first")
    mixed_b = message(5, [0.7, 0.8], "mixed second")
    result["invalid_failures"] = sum([
        attempt([invalid_none]),
        attempt([invalid_empty]),
        attempt([mixed_a, mixed_b]),
    ])
    ids = [valid["id"], invalid_none["id"], invalid_empty["id"], mixed_a["id"], mixed_b["id"]]
    stored = [store.get(doc_id, index_name, [memory_id]) for doc_id in ids]
    result["rows_after_invalid"] = sum(bool(item) for item in stored)
    old = stored[0] or {}
    old_vector = old.get("content_embed")
    result["old_row_preserved"] = (
        old.get("content") == "stable old row"
        and old_vector is not None
        and len(old_vector) == 3
    )

    if p["group"] == "experiment":
        table = store.physical_table(index_name)
        qualified = store.ddl.qualified_name(table)
        row, description = store._fetch_one_with_description(
            f"SELECT q_3_vec_empty FROM {qualified} WHERE id = %s",
            [valid["id"]],
        )
        flags = store._row_to_dict(row, description)
        result["real_empty_flag"] = bool(flags.get("q_3_vec_empty"))

        replacement = message(1, [0.9, 0.8], "cross dimension row")
        replacement_failures = store.insert([replacement], index_name, memory_id)
        if replacement_failures:
            raise RuntimeError("cross-dimension replacement was rejected")
        row, description = store._fetch_one_with_description(
            f"SELECT q_3_vec,q_3_vec_empty,q_2_vec_empty FROM {qualified} WHERE id = %s",
            [valid["id"]],
        )
        physical = store._row_to_dict(row, description)
        old_values = physical.get("q_3_vec")
        if isinstance(old_values, str):
            old_values = json.loads(old_values)
        result["unused_zero_vector"] = (
            old_values is not None
            and len(old_values) == 3
            and all(float(item) == 0.0 for item in old_values)
        )
        result["unused_empty_flag"] = bool(physical.get("q_3_vec_empty"))
        after = store.get(valid["id"], index_name, [memory_id]) or {}
        after_vector = after.get("content_embed")
        result["cross_dimension"] = len(after_vector) if after_vector is not None else 0
finally:
    try:
        store.delete_idx(index_name, memory_id)
        cleanup_succeeded = not store.index_exist(index_name, memory_id)
    except Exception:
        cleanup_succeeded = False
    result["cleanup_succeeded"] = cleanup_succeeded
    pool = getattr(store, "connPool", None)
    if callable(getattr(pool, "destroy", None)):
        pool.destroy()
    shared_pool = getattr(store, "pool", None)
    if callable(getattr(shared_pool, "close_all", None)):
        shared_pool.close_all()

print("__FRESH_RESULT__" + json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, payload],
        cwd=manager.PROJECT_ROOT,
        env=COMMON.group_environment(group),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    marker = next(
        (line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__")),
        None,
    )
    if completed.returncode != 0 or marker is None:
        raise COMMON.CaseBlocked(f"{group} current Memory vector probe did not return a result")
    return json.loads(marker)


def _dialect_canary(group: str) -> dict[str, Any]:
    table = f"{COMMON.RESOURCE_PREFIX}_052_{group}"
    connection = COMMON.open_metadata_database(group, writable=True)
    observed: dict[str, Any] = {
        "compatibility": "mysql-equivalent" if group == "control" else None,
        "varchar_supported": False,
        "json_supported": False,
        "ustore": False if group == "control" else None,
        "transaction_value": None,
        "rows_after_rollback": -1,
        "gauss_dialect_parsed": group == "experiment",
        "cleanup_succeeded": False,
        "error_class": None,
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")
        connection.commit()
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute(f"CREATE TABLE {table} (id VARCHAR(96) PRIMARY KEY, payload JSON NOT NULL, note VARCHAR(64)) ENGINE=InnoDB")
            else:
                cursor.execute("SHOW sql_compatibility")
                observed["compatibility"] = str(cursor.fetchone()[0]).upper()
                cursor.execute(f"CREATE TABLE {table} (id VARCHAR2(96) PRIMARY KEY, payload JSONB NOT NULL, note VARCHAR2(64)) WITH (storage_type=USTORE)")
        connection.commit()
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute(
                    f"INSERT INTO {table} (id,payload,note) VALUES (%s,%s,%s)",
                    ("row-1", '{"kind":"fresh"}', "dialect"),
                )
                cursor.execute(
                    f"SELECT JSON_UNQUOTE(JSON_EXTRACT(payload, '$.kind')) FROM {table} WHERE id=%s",
                    ("row-1",),
                )
            else:
                cursor.execute(
                    f"INSERT INTO {table} (id,payload,note) VALUES (%s,%s::jsonb,%s)",
                    ("row-1", '{"kind":"fresh"}', "dialect"),
                )
                cursor.execute(
                    f"SELECT payload #>> '{{kind}}' FROM {table} WHERE id=%s",
                    ("row-1",),
                )
            observed["transaction_value"] = str(cursor.fetchone()[0])
        connection.rollback()
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            observed["rows_after_rollback"] = int(cursor.fetchone()[0])
            if group == "experiment":
                cursor.execute(
                    "SELECT c.reloptions FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname=%s",
                    (table,),
                )
                row = cursor.fetchone()
                options = row[0] if row and row[0] else []
                observed["ustore"] = any("storage_type=ustore" in str(item).lower() for item in options)
        observed["varchar_supported"] = True
        observed["json_supported"] = observed["transaction_value"] == "fresh"
    except Exception as error:
        observed["error_class"] = type(error).__name__
        connection.rollback()
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            connection.commit()
            with connection.cursor() as cursor:
                if group == "control":
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
                        (table,),
                    )
                else:
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=current_schema() AND table_name=%s",
                        (table,),
                    )
                observed["cleanup_succeeded"] = int(cursor.fetchone()[0]) == 0
        except Exception:
            connection.rollback()
            observed["cleanup_succeeded"] = False
        finally:
            connection.close()
    return observed


def dialect_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    base = (
        observed.get("varchar_supported") is True
        and observed.get("json_supported") is True
        and observed.get("transaction_value") == "fresh"
        and observed.get("rows_after_rollback") == 0
        and observed.get("cleanup_succeeded") is True
        and observed.get("error_class") is None
    )
    if group == "control":
        return base and observed.get("compatibility") == "mysql-equivalent" and observed.get("gauss_dialect_parsed") is False
    return base and observed.get("compatibility") in {"A", "ORA"} and observed.get("ustore") is True and observed.get("gauss_dialect_parsed") is True


def _identifier_table_name(group: str) -> str:
    prefix = f"{COMMON.RESOURCE_PREFIX}_053_{group}_"
    return (prefix + "x" * 63)[:63]


def identifier_live_qualified_table(group: str, current_schema: str, table: str) -> tuple[GaussDBDDLBuilder, str]:
    schema = "public" if group == "control" else current_schema
    builder = GaussDBDDLBuilder(schema)
    qualified_table = table if group == "control" else builder.qualified_name(table)
    return builder, qualified_table


def _identifier_live_canary(group: str) -> dict[str, Any]:
    connection = COMMON.open_metadata_database(group, writable=True)
    current_schema = "public"
    if group == "experiment":
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            current_schema = str(cursor.fetchone()[0])
    builder, qualified_table = identifier_live_qualified_table(group, current_schema, _identifier_table_name(group))
    table = builder.validate_identifier(_identifier_table_name(group))
    index = builder.index_name(table, "s" * 63)
    observed: dict[str, Any] = {
        "table_length": len(table),
        "index_length": len(index),
        "table_exists": False,
        "index_exists": False,
        "cleanup_succeeded": False,
        "error_class": None,
    }
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {qualified_table}")
            if group == "control":
                cursor.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value INTEGER)")
                cursor.execute(f"CREATE INDEX {index} ON {table} (value)")
            else:
                cursor.execute(f"CREATE TABLE {qualified_table} (id NUMBER(19) PRIMARY KEY, value NUMBER(19)) WITH (storage_type=USTORE)")
                cursor.execute(f"CREATE INDEX {index} ON {qualified_table} (value)")
        connection.commit()
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
                    (table,),
                )
                observed["table_exists"] = int(cursor.fetchone()[0]) == 1
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s",
                    (table, index),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                    (current_schema, table),
                )
                observed["table_exists"] = int(cursor.fetchone()[0]) == 1
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=%s AND tablename=%s AND indexname=%s",
                    (current_schema, table, index),
                )
            observed["index_exists"] = int(cursor.fetchone()[0]) == 1
    except Exception as error:
        observed["error_class"] = type(error).__name__
        connection.rollback()
    finally:
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {qualified_table}")
            connection.commit()
            with connection.cursor() as cursor:
                if group == "control":
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
                        (table,),
                    )
                else:
                    cursor.execute(
                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                        (current_schema, table),
                    )
                observed["cleanup_succeeded"] = int(cursor.fetchone()[0]) == 0
        except Exception:
            connection.rollback()
            observed["cleanup_succeeded"] = False
        finally:
            connection.close()
    return observed


def _run_051_group(group: str) -> dict[str, Any]:
    boundary = vector_literal_boundary_observation()
    observed = _run_memory_subprocess(group)
    boundary_ok = boundary == {
        "none_error": "ValueError",
        "empty_error": "ValueError",
        "wrong_dimension_error": "ValueError",
        "valid_literal": "[1.0,2.0,3.0]",
        "zero_literal": "[0,0,0]",
    }
    passed = boundary_ok and memory_vector_contract_ok(observed, cross_dim_required=group == "experiment")
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "verify_vector_literal_input_boundaries", **boundary},
            {"name": "reject_invalid_vectors_and_verify_live_row_state", **observed},
        ],
        {
            "invalid_inputs_atomic": True,
            "real_vector_dimension": 3,
            "gauss_cross_dimension_reset": group == "experiment",
            "cleanup": True,
        },
        "TC-FR-051-VECTOR-EMPTY",
        f"{group} Memory vector invalid-input or cross-dimension contract failed",
        code_location="memory/utils/gaussdb_conn.py:GaussDBMemoryConnection.insert",
    )


def _run_052_group(group: str) -> dict[str, Any]:
    observed = _dialect_canary(group)
    return COMMON.pass_or_fail(
        dialect_contract_ok(group, observed),
        [{"name": "execute_group_specific_live_dialect_transaction_canary", **observed}],
        {
            "compatibility": "mysql-equivalent" if group == "control" else "A_or_ORA",
            "json_and_varchar": True,
            "ustore": group == "experiment",
            "rollback_rows": 0,
            "cleanup": True,
        },
        "TC-FR-052-A-ORA-DIALECT",
        f"{group} group-specific VARCHAR/JSON/USTORE transaction canary failed",
        code_location="memory/utils/gaussdb_conn.py:GaussDBMemoryDDLBuilder.build_memory_table_ddl",
    )


def _run_053_group(group: str) -> dict[str, Any]:
    limits = identifier_limit_observation()
    live = _identifier_live_canary(group)
    passed = (
        limits["accepted_length"] == 63
        and limits["rejected_64"] == "InvalidGaussDBObjectName"
        and limits["rejected_invalid"] == "InvalidGaussDBObjectName"
        and limits["index_name_stable"] is True
        and limits["index_name_length"] <= 63
        and limits["index_name_hashed"] is True
        and live["table_length"] == 63
        and live["index_length"] <= 63
        and live["table_exists"] is True
        and live["index_exists"] is True
        and live["cleanup_succeeded"] is True
        and live["error_class"] is None
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "verify_identifier_length_and_hash_boundaries", **limits},
            {"name": "create_only_builder_approved_live_objects_and_cleanup", **live},
        ],
        {
            "accepted_length": 63,
            "rejected_length": 64,
            "stable_index_length_at_most": 63,
            "cleanup": True,
        },
        "TC-FR-053-IDENTIFIER-LENGTH",
        f"{group} identifier length, stable hash, or live object cleanup failed",
        code_location="common/doc_store/gaussdb_conn_base.py:GaussDBDDLBuilder.index_name",
    )


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-FR-051": _run_051_group,
    "TC-FR-052": _run_052_group,
    "TC-FR-053": _run_053_group,
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
