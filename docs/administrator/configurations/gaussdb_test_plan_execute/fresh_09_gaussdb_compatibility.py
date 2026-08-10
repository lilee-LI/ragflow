#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable


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
SCHEDULING = _load_module(
    EXECUTE_DIR / "fresh_09_connector_scheduling.py",
    "fresh_09_connector_scheduling_runtime",
)
MANAGER = _load_module(
    EXECUTE_DIR / "fresh_service_manager.py",
    "fresh_09_compatibility_service_manager_runtime",
)


def empty_string_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    if group == "control":
        physical_expected = ["", ""]
    elif group == "experiment":
        physical_expected = [None, None]
    else:
        raise ValueError("unknown group")
    return (
        observed.get("physical_before") == physical_expected
        and observed.get("orm_before") == ["", ""]
        and observed.get("api_error_msg_before") == ""
        and observed.get("empty_suffix_unchanged") is True
        and observed.get("error_msg_after") == "append-sentinel"
        and observed.get("full_trace_after") == "trace-sentinel"
        and observed.get("literal_null_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def distinct_order_contract_ok(observed: dict[str, Any]) -> bool:
    service_ids = observed.get("service_ids")
    return (
        observed.get("response") == [200, 0]
        and isinstance(service_ids, list)
        and service_ids == observed.get("api_ids")
        and service_ids == observed.get("database_ids")
        and observed.get("update_time_selected") is True
        and observed.get("strictly_descending") is True
        and observed.get("cleanup_succeeded") is True
    )


def _response(result: dict[str, Any]) -> list[Any]:
    return [result.get("http_status"), result.get("code")]


def _raw_text_values(group: str, log_id: str) -> list[Any]:
    rows = SCHEDULING._rows(
        group,
        "SELECT error_msg,full_exception_trace FROM sync_logs WHERE id=%s",
        (log_id,),
    )
    return list(rows[0]) if rows else ["missing", "missing"]


def _run_service_probe(
    group: str,
    *,
    mode: str,
    arguments: list[str],
) -> dict[str, Any]:
    scripts = {
        "empty": r"""
import json, sys
from api.db.db_models import SyncLogs
from api.db.services.connector_service import SyncLogsService, _append_text_expr

log_id = sys.argv[1]
found, row = SyncLogsService.get_by_id(log_id)
if not found:
    raise RuntimeError("sync log is missing")
before = [row.error_msg, row.full_exception_trace]
empty_error_expr = _append_text_expr(SyncLogs.error_msg, "")
empty_trace_expr = _append_text_expr(SyncLogs.full_exception_trace, "")
SyncLogsService.increase_removed_docs(log_id, 0, "", 0)
found, after = SyncLogsService.get_by_id(log_id)
print("__FRESH_RESULT__" + json.dumps({
    "orm_before": before,
    "orm_after": [after.error_msg, after.full_exception_trace],
    "empty_expressions_are_none": empty_error_expr is None and empty_trace_expr is None,
}, sort_keys=True))
""",
        "append": r"""
import json, sys
from api.db.db_models import SyncLogs
from api.db.services.connector_service import SyncLogsService, _append_text_expr

log_id, error_sentinel, trace_sentinel = sys.argv[1:4]
SyncLogsService.increase_removed_docs(log_id, 0, error_sentinel, 0)
trace_expr = _append_text_expr(SyncLogs.full_exception_trace, trace_sentinel)
SyncLogs.update(full_exception_trace=trace_expr).where(SyncLogs.id == log_id).execute()
found, row = SyncLogsService.get_by_id(log_id)
if not found:
    raise RuntimeError("sync log is missing after append")
print("__FRESH_RESULT__" + json.dumps({
    "error_msg_after": row.error_msg,
    "full_trace_after": row.full_exception_trace,
}, sort_keys=True))
""",
        "list": r"""
import json, sys
from api.db.services.connector_service import SyncLogsService

connector_id = sys.argv[1]
rows, total = SyncLogsService.list_sync_tasks(connector_id, 1, 15)
print("__FRESH_RESULT__" + json.dumps({
    "ids": [str(row.get("id")) for row in rows],
    "update_times": [int(row.get("update_time")) for row in rows],
    "update_time_selected": all("update_time" in row for row in rows),
    "total": int(total),
}, sort_keys=True))
""",
    }
    script = scripts.get(mode)
    if script is None:
        raise ValueError("unknown compatibility probe mode")
    completed = subprocess.run(
        [str(MANAGER.PYTHON), "-c", script, *arguments],
        cwd=MANAGER.PROJECT_ROOT,
        env=MANAGER.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    marker = next(
        (line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__")),
        None,
    )
    if completed.returncode != 0 or marker is None:
        raise COMMON.CaseBlocked(f"{group} compatibility probe {mode} exited {completed.returncode} without a result marker")
    return json.loads(marker)


def _api_logs(
    case_id: str,
    group: str,
    owner: dict[str, str],
    connector_id: str,
    label: str,
) -> dict[str, Any]:
    return COMMON.http_request(
        case_id,
        group,
        label,
        "GET",
        f"/connectors/{connector_id}/logs?page=1&page_size=15",
        auth=owner["auth"],
    )


def _run_093_group(group: str) -> dict[str, Any]:
    case_id = "TC-CONN-093"
    owner = COMMON.owner(case_id, group)
    fixture = SCHEDULING._linked_fixture(
        case_id,
        group,
        owner,
        suffix="empty-string",
    )
    connector_id = str(fixture.get("connector_id") or "")
    kb_id = str(fixture.get("dataset_id") or "")
    logs = SCHEDULING._sync_rows(group, connector_id, kb_id=kb_id)
    log_id = logs[0]["id"] if logs else ""
    if not log_id:
        raise COMMON.CaseBlocked(f"{group} did not create the expected sync log")

    physical_before = _raw_text_values(group, log_id)
    api_before = _api_logs(case_id, group, owner, connector_id, "list_empty_log")
    api_data = api_before.get("data") if isinstance(api_before.get("data"), dict) else {}
    api_rows = api_data.get("logs") if isinstance(api_data.get("logs"), list) else []
    api_row = next((row for row in api_rows if str(row.get("id")) == log_id), {})

    empty_probe = _run_service_probe(
        group,
        mode="empty",
        arguments=[log_id],
    )
    physical_after_empty = _raw_text_values(group, log_id)
    append_probe = _run_service_probe(
        group,
        mode="append",
        arguments=[log_id, "append-sentinel", "trace-sentinel"],
    )
    physical_after_append = _raw_text_values(group, log_id)

    api_cleanup = SCHEDULING._cleanup_linked_fixture(
        case_id,
        group,
        owner,
        fixture,
    )
    fixture_cleanup = SCHEDULING._fixture_delete_logs(group, [connector_id])
    serialized_after = json.dumps(
        [append_probe, physical_after_append],
        ensure_ascii=False,
        default=str,
    )
    observed = {
        "physical_before": physical_before,
        "orm_before": empty_probe.get("orm_before"),
        "api_error_msg_before": api_row.get("error_msg"),
        "empty_suffix_unchanged": (empty_probe.get("empty_expressions_are_none") is True and empty_probe.get("orm_after") == empty_probe.get("orm_before") and physical_after_empty == physical_before),
        "error_msg_after": append_probe.get("error_msg_after"),
        "full_trace_after": append_probe.get("full_trace_after"),
        "literal_null_absent": "NULLappend-sentinel" not in serialized_after and "NULLtrace-sentinel" not in serialized_after,
        "cleanup_succeeded": api_cleanup and fixture_cleanup,
    }
    return COMMON.pass_or_fail(
        empty_string_contract_ok(group, observed),
        [
            {
                "name": "create_connector_and_sync_log_fixture",
                "responses": [
                    _response(fixture["create"]),
                    _response(fixture["dataset"]),
                    _response(fixture["link"]),
                ],
                "log_id": log_id,
            },
            {
                "name": "compare_physical_orm_and_api_empty_semantics",
                "physical_before": physical_before,
                "orm_before": empty_probe.get("orm_before"),
                "api_response": _response(api_before),
                "api_error_msg_before": api_row.get("error_msg"),
            },
            {
                "name": "exercise_empty_and_nonempty_append_paths",
                "empty_suffix_unchanged": observed["empty_suffix_unchanged"],
                "error_msg_after": observed["error_msg_after"],
                "full_trace_after": observed["full_trace_after"],
                "literal_null_absent": observed["literal_null_absent"],
            },
            {
                "name": "cleanup_connector_dataset_and_sync_logs",
                "api_cleanup_succeeded": api_cleanup,
                "fixture_cleanup_succeeded": fixture_cleanup,
            },
        ],
        {
            "physical_empty": ["", ""] if group == "control" else [None, None],
            "application_empty": ["", ""],
            "append_results": ["append-sentinel", "trace-sentinel"],
        },
        f"{case_id}-EMPTY-STRING",
        f"{group} sync-log empty-string or append semantics do not match the database adapter contract",
        code_location="api/db/services/connector_service.py:_append_text_expr",
    )


def _run_094_group(group: str) -> dict[str, Any]:
    case_id = "TC-CONN-094"
    owner = COMMON.owner(case_id, group)
    fixture = SCHEDULING._linked_fixture(
        case_id,
        group,
        owner,
        suffix="distinct-order",
    )
    connector_id = str(fixture.get("connector_id") or "")
    kb_id = str(fixture.get("dataset_id") or "")
    initial = SCHEDULING._sync_rows(group, connector_id, kb_id=kb_id)
    target_id = initial[0]["id"] if initial else ""
    extra_ids = [uuid.uuid4().hex, uuid.uuid4().hex]
    if not target_id:
        raise COMMON.CaseBlocked(f"{group} did not create the expected sync log")

    for log_id, date in zip(extra_ids, ("2001-01-01 00:00:00", "2002-01-01 00:00:00"), strict=True):
        SCHEDULING._insert_sync_log(
            group,
            log_id=log_id,
            connector_id=connector_id,
            kb_id=kb_id,
            task_type="sync",
            status="5",
            update_date=date,
        )
    ordered = [target_id, extra_ids[1], extra_ids[0]]
    for log_id, update_time, update_date in zip(
        ordered,
        (3000, 2000, 1000),
        ("2003-01-01 00:00:00", "2002-01-01 00:00:00", "2001-01-01 00:00:00"),
        strict=True,
    ):
        SCHEDULING._fixture_execute(
            group,
            [
                (
                    "UPDATE sync_logs SET update_time=%s,update_date=%s WHERE id=%s",
                    (update_time, update_date, log_id),
                )
            ],
        )

    probe = _run_service_probe(
        group,
        mode="list",
        arguments=[connector_id],
    )
    api = _api_logs(case_id, group, owner, connector_id, "list_distinct_logs")
    api_data = api.get("data") if isinstance(api.get("data"), dict) else {}
    api_rows = api_data.get("logs") if isinstance(api_data.get("logs"), list) else []
    api_ids = [str(row.get("id")) for row in api_rows]
    database_rows = SCHEDULING._rows(
        group,
        "SELECT id,update_time FROM sync_logs WHERE connector_id=%s ORDER BY update_time DESC",
        (connector_id,),
    )
    database_ids = [str(row[0]) for row in database_rows]
    service_times = [int(value) for value in probe.get("update_times", [])]

    api_cleanup = SCHEDULING._cleanup_linked_fixture(
        case_id,
        group,
        owner,
        fixture,
    )
    fixture_cleanup = SCHEDULING._fixture_delete_logs(group, [connector_id])
    observed = {
        "response": _response(api),
        "service_ids": probe.get("ids"),
        "api_ids": api_ids,
        "database_ids": database_ids,
        "update_time_selected": probe.get("update_time_selected"),
        "strictly_descending": service_times == sorted(service_times, reverse=True) and len(service_times) == len(set(service_times)),
        "cleanup_succeeded": api_cleanup and fixture_cleanup,
    }
    return COMMON.pass_or_fail(
        distinct_order_contract_ok(observed),
        [
            {
                "name": "create_three_distinct_sync_log_rows",
                "responses": [
                    _response(fixture["create"]),
                    _response(fixture["dataset"]),
                    _response(fixture["link"]),
                ],
                "expected_ids": ordered,
            },
            {
                "name": "invoke_real_distinct_order_service_query",
                "service_ids": probe.get("ids"),
                "update_times": service_times,
                "update_time_selected": probe.get("update_time_selected"),
            },
            {
                "name": "compare_api_service_and_database_order",
                "api_response": _response(api),
                "api_ids": api_ids,
                "database_ids": database_ids,
            },
            {
                "name": "cleanup_connector_dataset_and_sync_logs",
                "api_cleanup_succeeded": api_cleanup,
                "fixture_cleanup_succeeded": fixture_cleanup,
            },
        ],
        {
            "ids": ordered,
            "order": "update_time DESC",
            "update_time_selected": True,
        },
        f"{case_id}-DISTINCT-ORDER",
        f"{group} Connector log query does not preserve DISTINCT/ORDER BY compatibility and order",
        code_location="api/db/services/connector_service.py:SyncLogsService.list_sync_tasks",
    )


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-CONN-093": _run_093_group,
    "TC-CONN-094": _run_094_group,
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
