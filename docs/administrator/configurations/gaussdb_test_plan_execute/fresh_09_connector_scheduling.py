#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
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
MANAGER = _load_module(
    EXECUTE_DIR / "fresh_service_manager.py",
    "fresh_09_scheduler_service_manager_runtime",
)


@contextmanager
def quiesced_sync_service(group: str):
    if group not in COMMON.GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    label = f"{group}:sync"
    registry = MANAGER.ProcessRegistry(MANAGER.STATE_PATH)
    before = registry.status().get(label, {})
    state = {
        "was_running": bool(before.get("alive") and before.get("identity_matches")),
        "stopped": False,
        "restored": False,
    }
    if state["was_running"]:
        stopped = registry.stop(label)
        state["stopped"] = bool(stopped.get("stopped"))
        if not state["stopped"]:
            raise COMMON.CaseBlocked(f"failed to quiesce managed sync service: {label}")
    try:
        yield state
    finally:
        if state["was_running"]:
            MANAGER.start_service(group, "sync")
            after = registry.status().get(label, {})
            state["restored"] = bool(after.get("alive") and after.get("identity_matches"))
            if not state["restored"]:
                raise RuntimeError(f"failed to restore managed sync service: {label}")
        else:
            state["restored"] = True


def due_scheduler_contract_ok(
    group: str,
    observed: dict[str, Any],
    *,
    frequency_field: str,
) -> bool:
    if group == "control":
        expected_expression = f"NOW() - INTERVAL `t2`.`{frequency_field}` MINUTE"
    elif group == "experiment":
        expected_expression = f"NOW() AT TIME ZONE 'Asia/Shanghai' - (t2.{frequency_field} * INTERVAL '1 minute')"
    else:
        raise ValueError("unknown group")
    return (
        observed.get("expression") == expected_expression
        and observed.get("returned_ids") == [observed.get("target_id")]
        and observed.get("excluded_ids_absent") is True
        and observed.get("fixture_restored") is True
        and observed.get("cleanup_succeeded") is True
    )


def _response(result: dict[str, Any]) -> list[Any]:
    return [result.get("http_status"), result.get("code")]


def _rows(group: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    connection, _user_table, _namespace = COMMON.open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return list(cursor.fetchall())
    finally:
        connection.close()


def _fixture_execute(
    group: str,
    operations: list[tuple[str, tuple[Any, ...]]],
) -> None:
    connection, _user_table, _namespace = COMMON.execution_base()._open_writable_fixture_database(group)
    try:
        with connection.cursor() as cursor:
            for statement, params in operations:
                cursor.execute(statement, params)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _sync_rows(
    group: str,
    connector_id: str,
    *,
    kb_id: str | None = None,
) -> list[dict[str, Any]]:
    statement = "SELECT id,kb_id,task_type,status,from_beginning,update_time,update_date,new_docs_indexed,total_docs_indexed,error_msg FROM sync_logs WHERE connector_id=%s"
    params: tuple[Any, ...] = (connector_id,)
    if kb_id is not None:
        statement += " AND kb_id=%s"
        params = (connector_id, kb_id)
    statement += " ORDER BY update_time DESC,id DESC"
    rows = _rows(group, statement, params)
    return [
        {
            "id": str(row[0]),
            "kb_id": str(row[1]),
            "task_type": str(row[2]),
            "status": str(row[3]),
            "from_beginning": None if row[4] is None else str(row[4]),
            "update_time": None if row[5] is None else int(row[5]),
            "update_date": None if row[6] is None else str(row[6]),
            "new_docs_indexed": int(row[7] or 0),
            "total_docs_indexed": int(row[8] or 0),
            "error_msg_is_empty": row[9] in (None, ""),
        }
        for row in rows
    ]


def _entity_count(group: str, table: str, entity_id: str) -> int:
    if table not in {"connector", "knowledgebase"}:
        raise ValueError("unsupported entity table")
    return int(_rows(group, f"SELECT COUNT(*) FROM {table} WHERE id=%s", (entity_id,))[0][0])


def _connector_link_count(group: str, connector_id: str, dataset_id: str) -> int:
    return int(
        _rows(
            group,
            "SELECT COUNT(*) FROM connector2kb WHERE connector_id=%s AND kb_id=%s",
            (connector_id, dataset_id),
        )[0][0]
    )


def _connector_ids_by_name(group: str, tenant_id: str, name: str) -> list[str]:
    return [
        str(row[0])
        for row in _rows(
            group,
            "SELECT id FROM connector WHERE tenant_id=%s AND name=%s ORDER BY id",
            (tenant_id, name),
        )
    ]


def _dataset_ids_by_name(group: str, tenant_id: str, name: str) -> list[str]:
    return [
        str(row[0])
        for row in _rows(
            group,
            "SELECT id FROM knowledgebase WHERE tenant_id=%s AND name=%s ORDER BY id",
            (tenant_id, name),
        )
    ]


def _preclean_connector(case_id: str, group: str, owner: dict[str, str], name: str) -> bool:
    succeeded = True
    for index, connector_id in enumerate(_connector_ids_by_name(group, owner["tenant_id"], name)):
        response = COMMON.http_request(
            case_id,
            group,
            f"preclean_connector_{name}_{index}",
            "DELETE",
            f"/connectors/{connector_id}",
            auth=owner["auth"],
        )
        succeeded = succeeded and response.get("code") == 0
    return succeeded and not _connector_ids_by_name(group, owner["tenant_id"], name)


def _preclean_dataset(
    case_id: str,
    group: str,
    auth: str,
    tenant_id: str,
    name: str,
) -> bool:
    ids = _dataset_ids_by_name(group, tenant_id, name)
    if ids:
        response = COMMON.http_request(
            case_id,
            group,
            f"preclean_dataset_{name}",
            "DELETE",
            "/datasets",
            auth=auth,
            payload={"ids": ids},
            timeout=120,
        )
        if response.get("code") != 0:
            return False
    return not _dataset_ids_by_name(group, tenant_id, name)


def _linked_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    *,
    suffix: str,
    source: str = "google_drive",
    config: dict[str, Any] | None = None,
    refresh_freq: int = 5,
    prune_freq: int = 5,
    auto_parse: str = "1",
) -> dict[str, Any]:
    connector_name = f"fresh09-{case_id[-3:]}-{suffix}-{group}"
    dataset_name = f"fresh09-{case_id[-3:]}-{suffix}-kb-{group}"
    preclean = _preclean_connector(case_id, group, owner, connector_name)
    preclean = (
        _preclean_dataset(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            dataset_name,
        )
        and preclean
    )
    create = COMMON.http_request(
        case_id,
        group,
        f"create_connector_{suffix}",
        "POST",
        "/connectors",
        auth=owner["auth"],
        payload={
            "name": connector_name,
            "source": source,
            "config": config if config is not None else {"credentials": {}},
            "refresh_freq": refresh_freq,
            "prune_freq": prune_freq,
        },
    )
    create_data = create.get("data") if isinstance(create.get("data"), dict) else {}
    connector_id = str(create_data.get("id") or "")
    dataset = COMMON.http_request(
        case_id,
        group,
        f"create_dataset_{suffix}",
        "POST",
        "/datasets",
        auth=owner["auth"],
        payload={"name": dataset_name, "chunk_method": "naive"},
        timeout=120,
    )
    dataset_data = dataset.get("data") if isinstance(dataset.get("data"), dict) else {}
    dataset_id = str(dataset_data.get("id") or "")
    link = (
        COMMON.http_request(
            case_id,
            group,
            f"link_connector_{suffix}",
            "PUT",
            f"/datasets/{dataset_id}",
            auth=owner["auth"],
            payload={"connectors": [{"id": connector_id, "auto_parse": auto_parse}]},
            timeout=120,
        )
        if connector_id and dataset_id
        else {}
    )
    return {
        "preclean": preclean,
        "connector_name": connector_name,
        "dataset_name": dataset_name,
        "connector_id": connector_id,
        "dataset_id": dataset_id,
        "create": create,
        "dataset": dataset,
        "link": link,
    }


def _cleanup_linked_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    fixture: dict[str, Any],
    *,
    suffix: str = "",
) -> bool:
    connector_id = str(fixture.get("connector_id") or "")
    dataset_id = str(fixture.get("dataset_id") or "")
    unlink_ok = True
    if connector_id and dataset_id and _connector_link_count(group, connector_id, dataset_id):
        response = COMMON.http_request(
            case_id,
            group,
            f"unlink_connector{suffix}",
            "PUT",
            f"/datasets/{dataset_id}",
            auth=owner["auth"],
            payload={"connectors": []},
            timeout=120,
        )
        unlink_ok = response.get("code") == 0 and _connector_link_count(group, connector_id, dataset_id) == 0
    if not unlink_ok:
        return False
    connector_ok = True
    if connector_id and _entity_count(group, "connector", connector_id):
        response = COMMON.http_request(
            case_id,
            group,
            f"cleanup_connector{suffix}",
            "DELETE",
            f"/connectors/{connector_id}",
            auth=owner["auth"],
        )
        connector_ok = response.get("code") == 0 and _entity_count(group, "connector", connector_id) == 0
    dataset_ok = True
    if dataset_id and _entity_count(group, "knowledgebase", dataset_id):
        response = COMMON.http_request(
            case_id,
            group,
            f"cleanup_dataset{suffix}",
            "DELETE",
            "/datasets",
            auth=owner["auth"],
            payload={"ids": [dataset_id]},
            timeout=120,
        )
        dataset_ok = response.get("code") == 0 and _entity_count(group, "knowledgebase", dataset_id) == 0
    return bool(fixture.get("preclean")) and unlink_ok and connector_ok and dataset_ok


def _insert_sync_log(
    group: str,
    *,
    log_id: str,
    connector_id: str,
    kb_id: str,
    task_type: str,
    status: str,
    update_date: str,
) -> None:
    timestamp = int(time.time() * 1000)
    statement = (
        "INSERT INTO sync_logs "
        "(id,connector_id,task_type,status,from_beginning,new_docs_indexed,"
        "total_docs_indexed,docs_removed_from_index,error_msg,error_count,"
        "full_exception_trace,time_started,kb_id,create_time,create_date,"
        "update_time,update_date) VALUES "
        "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
    )
    _fixture_execute(
        group,
        [
            (
                statement,
                (
                    log_id,
                    connector_id,
                    task_type,
                    status,
                    "0",
                    0,
                    0,
                    0,
                    "",
                    0,
                    None,
                    None,
                    kb_id,
                    timestamp,
                    update_date,
                    timestamp,
                    update_date,
                ),
            )
        ],
    )


def _set_log_fields(
    group: str,
    log_id: str,
    *,
    status: str | None = None,
    update_date: str | None = None,
) -> None:
    assignments: list[str] = []
    values: list[Any] = []
    if status is not None:
        assignments.append("status=%s")
        values.append(status)
    if update_date is not None:
        assignments.append("update_date=%s")
        values.append(update_date)
    if not assignments:
        return
    values.append(log_id)
    _fixture_execute(
        group,
        [
            (
                "UPDATE sync_logs SET " + ",".join(assignments) + " WHERE id=%s",
                tuple(values),
            )
        ],
    )


def _fixture_delete_logs(group: str, connector_ids: list[str]) -> bool:
    ids = [item for item in connector_ids if item]
    if not ids:
        return True
    placeholders = ",".join(["%s"] * len(ids))
    _fixture_execute(
        group,
        [
            (
                f"DELETE FROM sync_logs WHERE connector_id IN ({placeholders})",
                tuple(ids),
            )
        ],
    )
    return (
        int(
            _rows(
                group,
                f"SELECT COUNT(*) FROM sync_logs WHERE connector_id IN ({placeholders})",
                tuple(ids),
            )[0][0]
        )
        == 0
    )


def _scheduler_probe(
    group: str,
    *,
    method: str,
    frequency_field: str,
) -> dict[str, Any]:
    if method not in {"list_due_sync_tasks", "list_due_prune_tasks"}:
        raise ValueError("unsupported scheduler probe")
    script = r"""
import json, sys
from api.db.services.connector_service import SyncLogsService, _poll_interval_expr
method, frequency_field = sys.argv[1:3]
tasks = getattr(SyncLogsService, method)()
payload = {
    "expression": _poll_interval_expr(frequency_field).sql,
    "tasks": [
        {
            "id": str(task.get("id")),
            "connector_id": str(task.get("connector_id")),
            "kb_id": str(task.get("kb_id")),
            "task_type": str(task.get("task_type")),
            "status": str(task.get("status")),
        }
        for task in tasks
    ],
}
print("__FRESH_RESULT__" + json.dumps(payload, sort_keys=True))
"""
    completed = subprocess.run(
        [
            str(MANAGER.PYTHON),
            "-c",
            script,
            method,
            frequency_field,
        ],
        cwd=MANAGER.PROJECT_ROOT,
        env=MANAGER.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    marker = next(
        (line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__")),
        None,
    )
    if completed.returncode != 0 or marker is None:
        raise COMMON.CaseBlocked(f"{group} isolated scheduler probe exited {completed.returncode} without a result marker")
    return json.loads(marker)


def _run_015_group_with_sync_paused(group: str) -> dict[str, Any]:
    case_id = "TC-CONN-015"
    owner = COMMON.owner(case_id, group)
    fixture = _linked_fixture(
        case_id,
        group,
        owner,
        suffix="due-sync",
        refresh_freq=1,
    )
    connector_id = fixture["connector_id"]
    kb_id = fixture["dataset_id"]
    initial = [item for item in _sync_rows(group, connector_id, kb_id=kb_id) if item["task_type"] == "sync"]
    target_id = initial[0]["id"] if initial else ""
    excluded = {
        "cancelled": uuid.uuid4().hex,
        "done": uuid.uuid4().hex,
        "different_task_type": uuid.uuid4().hex,
        "not_due": uuid.uuid4().hex,
    }
    if target_id:
        _set_log_fields(group, target_id, update_date="2000-01-01 00:00:00")
        _insert_sync_log(group, log_id=excluded["cancelled"], connector_id=connector_id, kb_id=kb_id, task_type="sync", status="2", update_date="2000-01-01 00:00:00")
        _insert_sync_log(group, log_id=excluded["done"], connector_id=connector_id, kb_id=kb_id, task_type="sync", status="3", update_date="2000-01-01 00:00:00")
        _insert_sync_log(group, log_id=excluded["different_task_type"], connector_id=connector_id, kb_id=kb_id, task_type="prune", status="5", update_date="2000-01-01 00:00:00")
        _insert_sync_log(group, log_id=excluded["not_due"], connector_id=connector_id, kb_id=kb_id, task_type="sync", status="5", update_date="2999-01-01 00:00:00")
    probe = _scheduler_probe(group, method="list_due_sync_tasks", frequency_field="refresh_freq")
    returned_ids = [str(item.get("id")) for item in probe.get("tasks", [])]
    api_cleanup = _cleanup_linked_fixture(case_id, group, owner, fixture)
    fixture_restored = _fixture_delete_logs(group, [connector_id])
    observed = {
        "expression": probe.get("expression"),
        "returned_ids": returned_ids,
        "target_id": target_id,
        "excluded_ids_absent": set(excluded.values()).isdisjoint(returned_ids),
        "fixture_restored": fixture_restored,
        "cleanup_succeeded": api_cleanup,
    }
    return COMMON.pass_or_fail(
        due_scheduler_contract_ok(group, observed, frequency_field="refresh_freq"),
        [
            {"name": "create_linked_scheduled_sync_fixture", "responses": [_response(fixture["create"]), _response(fixture["dataset"]), _response(fixture["link"])]},
            {"name": "inject_authorized_due_time_and_exclusion_variants", "target_present": bool(target_id), "excluded_variant_count": len(excluded)},
            {"name": "invoke_real_due_sync_scheduler_in_group_environment", "expression": probe.get("expression"), "returned_ids": returned_ids},
            {"name": "cleanup_api_entities_and_restore_fixture_rows", "api_cleanup_succeeded": api_cleanup, "fixture_restored": fixture_restored},
        ],
        {"dialect": "group-specific", "returned_ids": [target_id], "excluded_variants": list(excluded)},
        f"{case_id}-DUE-SYNC",
        f"{group} due-sync scheduler uses the wrong SQL dialect or returns the wrong task set",
        code_location="api/db/services/connector_service.py:SyncLogsService._list_due_tasks_for_freq",
    )


def _run_016_group_with_sync_paused(group: str) -> dict[str, Any]:
    case_id = "TC-CONN-016"
    owner = COMMON.owner(case_id, group)
    fixtures = [
        _linked_fixture(case_id, group, owner, suffix="prune-enabled", config={"credentials": {}, "sync_deleted_files": True}, prune_freq=1),
        _linked_fixture(case_id, group, owner, suffix="prune-disabled", config={"credentials": {}, "sync_deleted_files": False}, prune_freq=1),
        _linked_fixture(case_id, group, owner, suffix="prune-zero", config={"credentials": {}, "sync_deleted_files": True}, prune_freq=0),
    ]
    connector_ids = [str(item["connector_id"]) for item in fixtures]
    prune_rows = [[row for row in _sync_rows(group, item["connector_id"], kb_id=item["dataset_id"]) if row["task_type"] == "prune"] for item in fixtures]
    target_id = prune_rows[0][0]["id"] if prune_rows[0] else ""
    disabled_id = uuid.uuid4().hex
    if not prune_rows[1]:
        _insert_sync_log(group, log_id=disabled_id, connector_id=fixtures[1]["connector_id"], kb_id=fixtures[1]["dataset_id"], task_type="prune", status="5", update_date="2000-01-01 00:00:00")
    else:
        disabled_id = prune_rows[1][0]["id"]
    zero_id = prune_rows[2][0]["id"] if prune_rows[2] else ""
    for log_id in (target_id, disabled_id, zero_id):
        if log_id:
            _set_log_fields(group, log_id, update_date="2000-01-01 00:00:00")
    probe = _scheduler_probe(group, method="list_due_prune_tasks", frequency_field="prune_freq")
    returned_ids = [str(item.get("id")) for item in probe.get("tasks", [])]
    api_cleanup = all(_cleanup_linked_fixture(case_id, group, owner, item, suffix=f"_{index}") for index, item in enumerate(fixtures))
    fixture_restored = _fixture_delete_logs(group, connector_ids)
    excluded = [item for item in (disabled_id, zero_id) if item]
    observed = {
        "expression": probe.get("expression"),
        "returned_ids": returned_ids,
        "target_id": target_id,
        "excluded_ids_absent": set(excluded).isdisjoint(returned_ids),
        "fixture_restored": fixture_restored,
        "cleanup_succeeded": api_cleanup,
    }
    return COMMON.pass_or_fail(
        due_scheduler_contract_ok(group, observed, frequency_field="prune_freq"),
        [
            {
                "name": "create_enabled_disabled_and_zero_frequency_prune_fixtures",
                "fixture_responses": [[_response(item["create"]), _response(item["dataset"]), _response(item["link"])] for item in fixtures],
            },
            {"name": "inject_authorized_prune_due_times", "target_present": bool(target_id), "excluded_variant_count": len(excluded)},
            {"name": "invoke_real_due_prune_scheduler_in_group_environment", "expression": probe.get("expression"), "returned_ids": returned_ids},
            {"name": "cleanup_api_entities_and_restore_fixture_rows", "api_cleanup_succeeded": api_cleanup, "fixture_restored": fixture_restored},
        ],
        {"dialect": "group-specific", "returned_ids": [target_id], "flag_and_positive_frequency_required": True},
        f"{case_id}-DUE-PRUNE",
        f"{group} due-prune scheduler uses the wrong dialect or fails flag/frequency filtering",
        code_location="api/db/services/connector_service.py:SyncLogsService.list_due_prune_tasks",
    )


def _run_015_group(group: str) -> dict[str, Any]:
    with quiesced_sync_service(group) as sync_state:
        result = _run_015_group_with_sync_paused(group)
    result["steps"].insert(
        0,
        {
            "name": "quiesce_and_restore_background_sync_service",
            **sync_state,
        },
    )
    return result


def _run_016_group(group: str) -> dict[str, Any]:
    with quiesced_sync_service(group) as sync_state:
        result = _run_016_group_with_sync_paused(group)
    result["steps"].insert(
        0,
        {
            "name": "quiesce_and_restore_background_sync_service",
            **sync_state,
        },
    )
    return result


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-CONN-015": _run_015_group,
    "TC-CONN-016": _run_016_group,
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
