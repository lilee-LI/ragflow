#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import http.server
import importlib.util
import json
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import requests

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    RUNTIME_DIR,
    evidence_dir,
)

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = evidence_dir("08_file")
RAW_DIR = EVIDENCE_DIR / "raw"
PLAN_FILES = (
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "08_file_management.md",
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "08_file_supplement.md",
)


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DD = _load_module("fresh_04_dataset_document.py", "fresh_08_dataset_base")
AUTH = DD.AUTH
DB = DD.DB
for module in (DD, AUTH, DB):
    module.EVIDENCE_DIR = EVIDENCE_DIR
    module.RAW_DIR = RAW_DIR


def _case_titles() -> dict[str, str]:
    import re

    pattern = re.compile(r"^### (TC-[A-Z0-9-]+-\d{3}):\s*(.+)$", re.MULTILINE)
    result: dict[str, str] = {}
    for path in PLAN_FILES:
        for case_id, title in pattern.findall(path.read_text(encoding="utf-8")):
            if case_id in result:
                raise ValueError(f"duplicate case id: {case_id}")
            result[case_id] = title.strip()
    if len(result) != 89:
        raise ValueError(f"expected 89 File cases, found {len(result)}")
    return result


CASE_TITLES = _case_titles()


def group_result_shape_ok(result: dict[str, Any]) -> bool:
    return (
        result.get("status") in {"PASS", "FAIL", "BLOCKED"}
        and isinstance(result.get("steps"), list)
        and bool(result["steps"])
        and all(isinstance(step, dict) and step.get("name") for step in result["steps"])
        and isinstance(result.get("oracle"), dict)
    )


def folder_create_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("response") == [200, 0]
        and observed.get("id_present") is True
        and observed.get("name_matches") is True
        and observed.get("parent_matches") is True
        and observed.get("type") == "folder"
        and observed.get("tenant_matches") is True
        and observed.get("creator_matches") is True
        and observed.get("orm_source_type") == ""
        and observed.get("api_source_type") == ""
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("physical_source_is_empty") is True and observed.get("physical_source_is_null") is False
    if group == "experiment":
        return common and observed.get("physical_source_is_empty") is False and observed.get("physical_source_is_null") is True
    raise ValueError("unknown group")


def rejection_contract_ok(observed: dict[str, Any], *, expected_code: int, expected_message: str) -> bool:
    return (
        observed.get("response") == [200, expected_code]
        and observed.get("message") == expected_message
        and observed.get("file_delta") == 0
        and observed.get("object_delta") == 0
        and observed.get("fixture_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def upload_contract_ok(observed: dict[str, Any], *, expected_count: int) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("returned_count") == expected_count
        and observed.get("database_count") == expected_count
        and observed.get("parent_matches") is True
        and observed.get("size_matches") is True
        and observed.get("type_matches") is True
        and observed.get("location_nonempty") is True
        and observed.get("object_exists") is True
        and observed.get("object_size_matches") is True
        and observed.get("object_sha_matches") is True
        and observed.get("source_contract_ok") is True
        and observed.get("cleanup_succeeded") is True
    )


def expected_upload_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix == "pdf":
        return "pdf"
    if suffix in {
        "msg",
        "eml",
        "doc",
        "docx",
        "ppt",
        "pptx",
        "yml",
        "xml",
        "htm",
        "json",
        "jsonl",
        "ldjson",
        "csv",
        "txt",
        "ini",
        "xls",
        "xlsx",
        "wps",
        "rtf",
        "hlp",
        "pages",
        "numbers",
        "key",
        "md",
        "mdx",
        "py",
        "js",
        "java",
        "c",
        "cpp",
        "h",
        "php",
        "go",
        "ts",
        "sh",
        "cs",
        "kt",
        "html",
        "sql",
        "epub",
    }:
        return "doc"
    if suffix in {"wav", "flac", "ape", "alac", "wavpack", "wv", "mp3", "aac", "ogg", "vorbis", "opus"}:
        return "audio"
    if suffix in {"jpg", "jpeg", "png", "tif", "gif", "svg", "webp", "mp4", "avi", "mkv"}:
        return "visual"
    return "other"


def list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("fixture_ids_expected") == observed.get("fixture_ids_returned")
        and observed.get("total_matches_database") is True
        and observed.get("all_parent_matches") is True
        and observed.get("parent_folder_matches") is True
        and observed.get("folder_sizes_match") is True
        and observed.get("has_child_folder_boolean") is True
        and observed.get("cleanup_succeeded") is True
    )


def response_parent_matches_query_folder(response_parent: dict[str, Any], query_folder: dict[str, Any]) -> bool:
    return bool(query_folder) and str(response_parent.get("id") or "") == str(query_folder.get("parent_id") or "")


def filter_rows_by_keywords(rows: list[dict[str, Any]], keywords: str | None) -> list[dict[str, Any]]:
    normalized = (keywords or "").casefold()
    if not normalized:
        return rows
    return [row for row in rows if normalized in str(row.get("name") or "").casefold()]


def pagination_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("legal_responses") == [[200, 0], [200, 0]]
        and observed.get("page_lengths") == [5, 5]
        and observed.get("pages_disjoint") is True
        and observed.get("pages_complete") is True
        and observed.get("descending") is True
        and observed.get("total_matches") is True
        and all(item == [200, 101] for item in observed.get("invalid_responses", []))
        and observed.get("database_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def move_contract_ok(observed: dict[str, Any], *, require_location_change: bool) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("row_count") == 1
        and observed.get("parent_matches") is True
        and observed.get("name_matches") is True
        and (observed.get("location_changed") is True if require_location_change else observed.get("location_changed") is False)
        and observed.get("old_object_absent") is True
        and observed.get("new_object_exists") is True
        and observed.get("new_object_length_matches") is True
        and observed.get("new_object_sha_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def unchanged_file_contract_ok(observed: dict[str, Any], *, expected_code: int, expected_message: str) -> bool:
    return (
        observed.get("response") == [200, expected_code]
        and observed.get("message") == expected_message
        and observed.get("row_unchanged") is True
        and observed.get("object_still_exists") is True
        and observed.get("object_sha_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def delete_contract_ok(observed: dict[str, Any], *, expected_success_count: int) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("success_count") == expected_success_count
        and observed.get("file_rows_remaining") == 0
        and observed.get("file_links_remaining") == 0
        and observed.get("documents_remaining") == 0
        and observed.get("tasks_remaining") == 0
        and observed.get("objects_remaining") == 0
        and observed.get("cleanup_succeeded") is True
    )


def knowledgebase_cleanup_contract_ok(observed: dict[str, Any]) -> bool:
    counts = observed.get("artifact_counts")
    return (
        observed.get("document_delete_response") == [200, 0]
        and observed.get("dataset_delete_response") == [200, 0]
        and isinstance(counts, dict)
        and all(counts.get(name) == 0 for name in ("files", "file_links", "documents", "tasks"))
        and observed.get("object_absent") is True
        and observed.get("dataset_absent") is True
    )


def link_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("data_true") is True
        and observed.get("linked_file_ids") == observed.get("expected_file_ids")
        and observed.get("document_count") == len(observed.get("expected_file_ids", []))
        and observed.get("all_dataset_ids_match") is True
        and observed.get("all_document_metadata_match") is True
        and observed.get("all_dataset_configuration_match") is True
        and observed.get("cleanup_succeeded") is True
    )


def run_probe_with_retry(
    run_attempt: Callable[[], subprocess.CompletedProcess[str]],
    *,
    max_attempts: int = 2,
    on_failure: Callable[[int, str, Any], None] | None = None,
    pause: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    pause = pause or (lambda: time.sleep(0.5))
    for attempt in range(1, max_attempts + 1):
        try:
            completed = run_attempt()
        except subprocess.TimeoutExpired as error:
            if on_failure:
                on_failure(attempt, "timeout", error)
        else:
            if completed.returncode == 0:
                return completed
            if on_failure:
                on_failure(attempt, "nonzero", completed)
        if attempt < max_attempts:
            pause()
    raise RuntimeError("probe failed after retries")


def hierarchy_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("returned_ids") == observed.get("expected_ids")
        and observed.get("all_required_fields_present") is True
        and observed.get("database_path_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def commit_add_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("response_id_present") is True
        and observed.get("commit_row_count") == 1
        and observed.get("item_row_count") == 1
        and observed.get("parent_is_null") is True
        and observed.get("operation") == "add"
        and observed.get("tree_entry_matches") is True
        and observed.get("object_exists") is True
        and observed.get("object_sha_matches") is True
    )


def commit_access_denied_contract_ok(observed: dict[str, Any]) -> bool:
    response = observed.get("response")
    return (
        isinstance(response, list)
        and len(response) == 2
        and response[0] == 200
        and response[1] in {102, 108, 403, 404}
        and observed.get("data_absent") is True
        and observed.get("commit_delta") == 0
        and observed.get("file_unchanged") is True
        and observed.get("object_unchanged") is True
    )


def download_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, None]
        and observed.get("bytes_match") is True
        and observed.get("content_type_matches") is True
        and observed.get("database_size_matches") is True
        and observed.get("storage_size_matches") is True
        and observed.get("storage_sha_matches") is True
        and observed.get("cleanup_succeeded") is True
        and observed.get("object_absent_after_cleanup") is True
    )


def path_filename_attempt_ok(observed: dict[str, Any]) -> bool:
    response = observed.get("response")
    if isinstance(response, list) and len(response) == 2 and response[0] in {200, 400, 422} and response[1] != 0:
        return observed.get("new_row_count") == 0
    return (
        response == [200, 0]
        and observed.get("new_row_count") == 1
        and observed.get("returned_name") == observed.get("expected_basename")
        and observed.get("returned_parent_is_requested") is True
        and observed.get("unsafe_intermediate_count") == 0
    )


def concurrent_folder_contract_ok(observed: dict[str, Any], *, request_count: int) -> bool:
    pairs = observed.get("response_pairs") or []
    messages = observed.get("messages") or []
    success_indexes = [index for index, pair in enumerate(pairs) if pair == [200, 0]]
    duplicate_indexes = [index for index, pair in enumerate(pairs) if pair == [200, 102] and index < len(messages) and messages[index] == "Duplicated folder name in the same folder."]
    return (
        observed.get("all_requests_completed") is True
        and len(pairs) == request_count
        and len(success_indexes) == 1
        and len(duplicate_indexes) == request_count - 1
        and observed.get("database_row_count") == 1
        and observed.get("cleanup_succeeded") is True
    )


def unauthenticated_contract_ok(observed: dict[str, Any]) -> bool:
    return (observed.get("http_status") in {401, 403} or observed.get("code") in {401, 403, 1001, 1002}) and observed.get("data_absent") is True and observed.get("file_delta") == 0


def cross_tenant_matrix_contract_ok(observed: dict[str, Any]) -> bool:
    denials = observed.get("denial_results")
    return (
        isinstance(denials, list)
        and len(denials) == 8
        and all(item is True for item in denials)
        and observed.get("victim_delete_response") == [200, 0]
        and observed.get("attacker_rows_preserved") is True
        and observed.get("attacker_objects_preserved") is True
        and observed.get("active_cleanup_succeeded") is True
    )


def source_type_empty_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("api_source_type") == "" and observed.get("orm_source_type") == "" and observed.get("orm_query_hit") is True and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("physical_is_empty") is True and observed.get("physical_is_null") is False
    if group == "experiment":
        return common and observed.get("physical_is_empty") is False and observed.get("physical_is_null") is True
    raise ValueError("unknown group")


def storage_folder_cleanup_contract_ok(observed: dict[str, Any]) -> bool:
    mode = observed.get("mode")
    expected_bucket_state = True if mode == "fixed_bucket" else False
    return (
        mode in {"fixed_bucket", "per_bucket"}
        and observed.get("delete_response") == [200, 0]
        and observed.get("file_rows_remaining") == 0
        and observed.get("object_exists_after") is False
        and observed.get("physical_bucket_exists_after") is expected_bucket_state
        and (observed.get("logical_object_count_after") == 0 if mode == "fixed_bucket" else True)
        and observed.get("cleanup_succeeded") is True
    )


def repeated_link_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("responses") == [[200, 0], [200, 0]]
        and observed.get("first_relation_count") == 1
        and observed.get("final_relation_count") == 1
        and observed.get("final_dataset_matches") is True
        and observed.get("old_document_absent_if_replaced") is True
        and observed.get("cleanup_succeeded") is True
    )


def skill_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("success_response") == [200, 0]
        and observed.get("success_go_called") is True
        and observed.get("success_rows_absent") is True
        and observed.get("success_object_absent") is True
        and observed.get("failure_response_nonzero") is True
        and observed.get("failure_go_called") is True
        and observed.get("failure_rows_preserved") is True
        and observed.get("failure_object_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )


def team_file_download_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("membership_active") is True
        and observed.get("dataset_permission") == "team"
        and observed.get("relation_count") == 1
        and observed.get("download_response") == [200, None]
        and observed.get("bytes_match") is True
        and observed.get("cleanup_succeeded") is True
    )


def _evidence_module():
    return _load_module("fresh_case_evidence.py", "fresh_08_evidence")


def _record_http(
    case_id: str,
    group: str,
    label: str,
    request_summary: dict[str, Any],
    response: requests.Response,
) -> str:
    evidence = _evidence_module()
    try:
        body: Any = response.json()
    except ValueError:
        body = {
            "binary_length": len(response.content),
            "binary_sha256": hashlib.sha256(response.content).hexdigest(),
            "content_type": response.headers.get("Content-Type"),
        }
    path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    evidence.write_evidence(
        path,
        evidence.sanitize(
            {
                "request": request_summary,
                "response": {"http_status": response.status_code, "body": body},
            }
        ),
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _http(
    case_id: str,
    group: str,
    label: str,
    auth: str | None,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    files: list[tuple[str, bytes, str]] | None = None,
    multipart_fields: dict[str, str] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    request_files: list[tuple[str, Any]] | None = None
    if files is not None or multipart_fields is not None:
        request_files = []
        for name, value in (multipart_fields or {}).items():
            request_files.append((name, (None, value)))
        for name, content, content_type in files or []:
            request_files.append(("file", (name, content, content_type)))
    started = time.monotonic()
    response = requests.request(
        method,
        f"{DB._api_base(group)}{path}",
        headers=headers,
        json=payload if request_files is None else None,
        params=params,
        files=request_files,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    raw_sha = _record_http(
        case_id,
        group,
        label,
        {
            "method": method,
            "path": path,
            "params": params,
            "json": payload,
            "multipart_fields": sorted((multipart_fields or {}).keys()),
            "files": [
                {
                    "name": name,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_type": content_type,
                }
                for name, content, content_type in files or []
            ],
            "Authorization": auth,
        },
        response,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "content_type": response.headers.get("Content-Type"),
        "response_length": len(response.content),
        "response_sha256": hashlib.sha256(response.content).hexdigest(),
        "elapsed_seconds": elapsed,
        "raw_sha256": raw_sha,
        "_content": response.content,
    }


def _owner(case_id: str, group: str) -> dict[str, str]:
    return DD._ensure_owner(case_id, group)


def _file_rows(
    group: str,
    *,
    tenant_id: str | None = None,
    ids: list[str] | None = None,
    prefix: str | None = None,
    parent_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if tenant_id is not None:
        clauses.append("tenant_id=%s")
        params.append(tenant_id)
    if ids is not None:
        if not ids:
            return []
        clauses.append("id IN (" + ",".join(["%s"] * len(ids)) + ")")
        params.extend(ids)
    if prefix is not None:
        clauses.append("LOWER(name) LIKE %s")
        params.append(prefix.lower() + "%")
    if parent_id is not None:
        clauses.append("parent_id=%s AND id<>%s")
        params.extend([parent_id, parent_id])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,parent_id,tenant_id,created_by,name,location,size,type,source_type,create_time FROM file{where} ORDER BY create_time,id",
                tuple(params),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "parent_id": str(row[1]),
            "tenant_id": str(row[2]),
            "created_by": str(row[3]),
            "name": str(row[4]),
            "location": None if row[5] is None else str(row[5]),
            "size": int(row[6] or 0),
            "type": str(row[7]),
            "source_type": None if row[8] is None else str(row[8]),
            "create_time": int(row[9] or 0),
        }
        for row in rows
    ]


def _root(case_id: str, group: str, owner: dict[str, str]) -> dict[str, Any]:
    response = _http(case_id, group, "initialize_and_list_root", owner["auth"], "GET", "/files", params={"page": 1, "page_size": 100})
    rows = _file_rows(group, tenant_id=owner["tenant_id"])
    roots = [row for row in rows if row["id"] == row["parent_id"]]
    if len(roots) != 1:
        raise RuntimeError(f"expected one root folder for {group}")
    return {"response": response, "row": roots[0], "children": _file_rows(group, parent_id=roots[0]["id"])}


def _missing_file_id(group: str) -> str:
    for _ in range(10):
        candidate = uuid.uuid4().hex
        if not _file_rows(group, ids=[candidate]):
            return candidate
    raise RuntimeError("failed to allocate missing file id")


def _orm_source_type(group: str, file_id: str) -> str | None:
    manager = _load_module("fresh_service_manager.py", f"fresh_08_orm_manager_{group}")
    script = r"""
import json, sys
from api.db.services.file_service import FileService
ok, row = FileService.get_by_id(sys.argv[1])
print("__FRESH_RESULT__" + json.dumps({"ok": ok, "source_type": row.source_type if ok else None}))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, file_id],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return json.loads(marker).get("source_type")


def _orm_empty_source_query(group: str, file_id: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", f"fresh_08_orm_empty_query_{group}")
    script = r"""
import json, sys
from api.db.services.file_service import FileService
rows = FileService.query(id=sys.argv[1], source_type="")
print("__FRESH_RESULT__" + json.dumps({
    "ids": [row.id for row in rows],
    "source_types": [row.source_type for row in rows],
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, file_id],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return json.loads(marker)


def _orm_update_source_type(group: str, file_id: str, source_type: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", f"fresh_08_orm_source_update_{group}")
    script = r"""
import json, sys
from api.db.services.file_service import FileService
file_id, source_type = sys.argv[1], sys.argv[2]
updated = int(FileService.update_by_id(file_id, {"source_type": source_type}) or 0)
ok, row = FileService.get_by_id(file_id)
empty_rows = FileService.query(id=file_id, source_type="")
print("__FRESH_RESULT__" + json.dumps({
    "updated": updated,
    "ok": bool(ok),
    "source_type": row.source_type if ok else None,
    "empty_query_ids": [item.id for item in empty_rows],
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, file_id, source_type],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return json.loads(marker)


def _storage_snapshot(case_id: str, group: str, label: str, parent_id: str, location: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", f"fresh_08_storage_{group}_{label}")
    script = r"""
import hashlib, json, sys
from common import settings
settings.init_settings()
parent_id, location = sys.argv[1], sys.argv[2]
exists = bool(settings.STORAGE_IMPL.obj_exist(parent_id, location))
blob = settings.STORAGE_IMPL.get(parent_id, location) if exists else None
print("__FRESH_RESULT__" + json.dumps({
    "exists": exists,
    "length": len(blob) if blob is not None else None,
    "sha256": hashlib.sha256(blob).hexdigest() if blob is not None else None,
}, sort_keys=True))
"""
    command = [str(manager.PYTHON), "-c", script, parent_id, location]
    for stale in RAW_DIR.glob(f"{case_id}_{group}_{label}_probe_error_*.json"):
        stale.unlink()

    def record_failure(attempt: int, kind: str, failure: Any) -> None:
        stdout = getattr(failure, "stdout", "") or ""
        stderr = getattr(failure, "stderr", "") or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        _evidence_module().write_evidence(
            RAW_DIR / f"{case_id}_{group}_{label}_probe_error_{attempt}.json",
            {
                "probe": {
                    "attempt": attempt,
                    "failure_kind": kind,
                    "exit_code": getattr(failure, "returncode", None),
                    "stdout_line_count": len(str(stdout).splitlines()),
                    "stderr_line_count": len(str(stderr).splitlines()),
                }
            },
        )

    completed = run_probe_with_retry(
        lambda: subprocess.run(
            command,
            cwd=manager.PROJECT_ROOT,
            env=manager.load_group_environment(group),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ),
        max_attempts=2,
        on_failure=record_failure,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    result = json.loads(marker)
    path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    _evidence_module().write_evidence(path, {"storage": result})
    return {**result, "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _create_folder(
    case_id: str,
    group: str,
    owner: dict[str, str],
    label: str,
    name: str,
    *,
    parent_id: str | None = None,
    file_type: str | None = "folder",
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name}
    if parent_id is not None:
        payload["parent_id"] = parent_id
    if file_type is not None:
        payload["type"] = file_type
    return _http(case_id, group, label, owner["auth"], "POST", "/files", payload=payload)


def _upload(
    case_id: str,
    group: str,
    owner: dict[str, str],
    label: str,
    files: list[tuple[str, bytes, str]],
    *,
    parent_id: str | None = None,
) -> dict[str, Any]:
    fields = {"parent_id": parent_id} if parent_id else None
    return _http(case_id, group, label, owner["auth"], "POST", "/files", files=files, multipart_fields=fields)


def _delete_files(
    case_id: str,
    group: str,
    owner: dict[str, str],
    label: str,
    ids: list[str],
) -> dict[str, Any]:
    if not ids:
        return {"http_status": 200, "code": 0, "data": {"success_count": 0}, "raw_sha256": None}
    return _http(case_id, group, label, owner["auth"], "DELETE", "/files", payload={"ids": ids}, timeout=180)


def _delete_dataset_documents(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    return DD._delete_documents(
        case_id,
        group,
        auth,
        label,
        dataset_id,
        document_ids,
    )


def _cleanup_prefix(case_id: str, group: str, owner: dict[str, str], prefix: str) -> bool:
    rows = _file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix)
    ids = {row["id"] for row in rows}
    top = [row["id"] for row in rows if row["parent_id"] not in ids]
    if top:
        _delete_files(case_id, group, owner, "cleanup_prefix", top)
    return not _file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix)


def _cleanup_file_prefix(case_id: str, group: str, owner: dict[str, str], prefix: str) -> bool:
    for attempt in range(3):
        rows = _file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix)
        if not rows:
            return True
        non_folder_ids = [row["id"] for row in rows if row["type"] != "folder"]
        if non_folder_ids:
            _delete_files(
                case_id,
                group,
                owner,
                f"cleanup_fixture_files_{attempt + 1}",
                non_folder_ids,
            )
        rows = _file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix)
        ids = {row["id"] for row in rows}
        top_folder_ids = [row["id"] for row in rows if row["type"] == "folder" and row["parent_id"] not in ids]
        if top_folder_ids:
            _delete_files(
                case_id,
                group,
                owner,
                f"cleanup_fixture_folders_{attempt + 1}",
                top_folder_ids,
            )
    return not _file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix)


def _file_row(group: str, file_id: str) -> dict[str, Any]:
    rows = _file_rows(group, ids=[file_id]) if file_id else []
    return rows[0] if len(rows) == 1 else {}


def _move_files(
    case_id: str,
    group: str,
    owner: dict[str, str],
    label: str,
    src_file_ids: list[str],
    *,
    dest_file_id: str | None = None,
    new_name: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"src_file_ids": src_file_ids}
    if dest_file_id is not None:
        payload["dest_file_id"] = dest_file_id
    if new_name is not None:
        payload["new_name"] = new_name
    return _http(
        case_id,
        group,
        label,
        owner["auth"],
        "POST",
        "/files/move",
        payload=payload,
        timeout=180,
    )


def _file_document_rows(
    group: str,
    *,
    file_ids: list[str] | None = None,
    dataset_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if file_ids is not None:
        if not file_ids:
            return []
        clauses.append("f2d.file_id IN (" + ",".join(["%s"] * len(file_ids)) + ")")
        params.extend(file_ids)
    if dataset_id is not None:
        clauses.append("d.kb_id=%s")
        params.append(dataset_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT f2d.file_id,f2d.document_id,d.kb_id,d.name,d.type,d.location,d.size,d.parser_id,"
                "d.parser_config,d.pipeline_id,k.parser_config,k.pipeline_id,d.source_type "
                "FROM file2document f2d JOIN document d ON d.id=f2d.document_id "
                f"JOIN knowledgebase k ON k.id=d.kb_id{where} "
                "ORDER BY f2d.file_id,d.kb_id,d.id",
                tuple(params),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()

    def normalized_json(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    return [
        {
            "file_id": str(row[0]),
            "document_id": str(row[1]),
            "dataset_id": str(row[2]),
            "name": str(row[3]),
            "type": str(row[4]),
            "location": str(row[5] or ""),
            "size": int(row[6] or 0),
            "parser_id": str(row[7] or ""),
            "parser_config": normalized_json(row[8]),
            "pipeline_id": str(row[9] or ""),
            "dataset_parser_config": normalized_json(row[10]),
            "dataset_pipeline_id": str(row[11] or ""),
            "source_type": str(row[12] or ""),
        }
        for row in rows
    ]


def _wait_file_documents(
    group: str,
    file_ids: list[str],
    dataset_id: str,
    *,
    expected_count: int,
    timeout: float = 20,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        rows = _file_document_rows(group, file_ids=file_ids, dataset_id=dataset_id)
        if len(rows) == expected_count:
            return rows
        time.sleep(0.1)
    return rows


def _saved_artifact_counts(group: str, file_ids: list[str], document_ids: list[str]) -> dict[str, int]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:

            def count(table: str, column: str, ids: list[str]) -> int:
                if not ids:
                    return 0
                placeholders = ",".join(["%s"] * len(ids))
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders})",
                    tuple(ids),
                )
                return int(cursor.fetchone()[0])

            return {
                "files": count("file", "id", file_ids),
                "file_links": count("file2document", "file_id", file_ids),
                "documents": count("document", "id", document_ids),
                "tasks": count("task", "doc_id", document_ids),
            }
    finally:
        connection.close()


def _commit_rows(
    group: str,
    *,
    folder_id: str | None = None,
    commit_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if folder_id is not None:
        clauses.append("folder_id=%s")
        params.append(folder_id)
    if commit_ids is not None:
        if not commit_ids:
            return []
        clauses.append("id IN (" + ",".join(["%s"] * len(commit_ids)) + ")")
        params.extend(commit_ids)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,folder_id,parent_id,message,author_id,file_count,tree_state,create_time FROM file_commit{where} ORDER BY create_time,id",
                tuple(params),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()

    def parse_tree(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    return [
        {
            "id": str(row[0]),
            "folder_id": str(row[1]),
            "parent_id": None if row[2] is None else str(row[2]),
            "message": str(row[3] or ""),
            "author_id": str(row[4]),
            "file_count": int(row[5] or 0),
            "tree_state": parse_tree(row[6]),
            "create_time": int(row[7] or 0),
        }
        for row in rows
    ]


def _commit_item_rows(
    group: str,
    *,
    commit_ids: list[str] | None = None,
    file_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for column, ids in (("commit_id", commit_ids), ("file_id", file_ids)):
        if ids is None:
            continue
        if not ids:
            return []
        clauses.append(column + " IN (" + ",".join(["%s"] * len(ids)) + ")")
        params.extend(ids)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,commit_id,file_id,operation,old_hash,new_hash,old_location,new_location,old_name,new_name,create_time FROM file_commit_item{where} ORDER BY create_time,id",
                tuple(params),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "commit_id": str(row[1]),
            "file_id": str(row[2]),
            "operation": str(row[3]),
            "old_hash": "" if row[4] is None else str(row[4]),
            "new_hash": "" if row[5] is None else str(row[5]),
            "old_location": "" if row[6] is None else str(row[6]),
            "new_location": "" if row[7] is None else str(row[7]),
            "old_name": "" if row[8] is None else str(row[8]),
            "new_name": "" if row[9] is None else str(row[9]),
            "create_time": int(row[10] or 0),
        }
        for row in rows
    ]


def _commit_request(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _http(
        case_id,
        group,
        label,
        auth,
        method,
        path,
        payload=payload,
        params=params,
        timeout=180,
    )


def _create_commit(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    folder_id: str,
    message: str,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    return _commit_request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/folders/{folder_id}/commits",
        payload={"message": message, "files": changes},
    )


def _prepare_secondary_user(
    case_id: str,
    group: str,
    owner: dict[str, str],
    number: int,
) -> dict[str, Any]:
    email = f"fm-{number:03d}-commit-user-b@fresh.invalid"
    password = f"Fresh-FM-{number:03d}-Commit-B@1234"
    preclean = True
    if DB._email_count(group, [email]):
        preclean = DB._delete_user_via_admin(group, email)
        owner.update(_owner(case_id, group))
    registration = DB._register(
        case_id,
        group,
        "register_commit_attacker",
        {
            "email": email,
            "nickname": f"FreshFM{number:03d}CommitB",
            "password": password,
        },
    )
    login = AUTH._login(
        case_id,
        group,
        "login_commit_attacker",
        email,
        password,
    )
    tenant_id = DD._owner_id(group, email) if DB._email_count(group, [email]) else ""
    return {
        "email": email,
        "auth": str(login.get("_auth") or ""),
        "tenant_id": tenant_id,
        "preclean": preclean,
        "registration_code": registration.get("code"),
        "login_code": login.get("code"),
        "raw_sha256": [registration.get("raw_sha256"), login.get("raw_sha256")],
    }


def _prepare_named_secondary_user(
    case_id: str,
    group: str,
    owner: dict[str, str],
    email: str,
    password: str,
    label_prefix: str,
) -> dict[str, Any]:
    preclean = True
    if DB._email_count(group, [email]):
        preclean = DB._delete_user_via_admin(group, email)
        owner.update(_owner(case_id, group))
    registration = DB._register(
        case_id,
        group,
        f"register_{label_prefix}",
        {
            "email": email,
            "nickname": "FreshFileUserB",
            "password": password,
        },
    )
    login = AUTH._login(
        case_id,
        group,
        f"login_{label_prefix}",
        email,
        password,
    )
    tenant_id = DD._owner_id(group, email) if DB._email_count(group, [email]) else ""
    return {
        "email": email,
        "auth": str(login.get("_auth") or ""),
        "tenant_id": tenant_id,
        "preclean": preclean,
        "registration_code": registration.get("code"),
        "login_code": login.get("code"),
        "raw_sha256": [registration.get("raw_sha256"), login.get("raw_sha256")],
    }


def _membership_snapshot(group: str, user_id: str, tenant_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT role,status FROM user_tenant WHERE user_id=%s AND tenant_id=%s ORDER BY id",
                (user_id, tenant_id),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return {
        "count": len(rows),
        "role": str(rows[0][0]) if len(rows) == 1 else None,
        "status": str(rows[0][1]) if len(rows) == 1 else None,
    }


def _invite_and_accept_member(
    case_id: str,
    group: str,
    owner: dict[str, str],
    secondary: dict[str, Any],
) -> dict[str, Any]:
    invited = _http(
        case_id,
        group,
        "invite_secondary_user_to_owner_tenant",
        owner["auth"],
        "POST",
        f"/tenants/{owner['tenant_id']}/users",
        payload={"email": secondary["email"]},
    )
    accepted = _http(
        case_id,
        group,
        "secondary_accept_owner_tenant_invitation",
        secondary["auth"],
        "PATCH",
        f"/tenants/{owner['tenant_id']}",
    )
    return {
        "invite": invited,
        "accept": accepted,
        "snapshot": _membership_snapshot(group, secondary["tenant_id"], owner["tenant_id"]),
    }


def _remove_membership(
    case_id: str,
    group: str,
    owner_tenant_id: str,
    secondary: dict[str, Any],
) -> dict[str, Any]:
    before = _membership_snapshot(group, secondary["tenant_id"], owner_tenant_id)
    response = (
        _http(
            case_id,
            group,
            "secondary_leave_owner_tenant",
            secondary["auth"],
            "DELETE",
            f"/tenants/{owner_tenant_id}/users",
            payload={"user_id": secondary["tenant_id"]},
        )
        if before["count"]
        else {"http_status": 200, "code": 0, "raw_sha256": None}
    )
    after = _membership_snapshot(group, secondary["tenant_id"], owner_tenant_id)
    return {
        "response": response,
        "before": before,
        "after": after,
        "succeeded": response.get("code") == 0 and after["count"] == 0,
    }


def _document_rows(group: str, document_ids: list[str]) -> list[dict[str, Any]]:
    if not document_ids:
        return []
    placeholders = ",".join(["%s"] * len(document_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,kb_id,source_type,location,name,size FROM document WHERE id IN ({placeholders}) ORDER BY id",
                tuple(document_ids),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "dataset_id": str(row[1]),
            "source_type": str(row[2]),
            "location": str(row[3] or ""),
            "name": str(row[4]),
            "size": int(row[5] or 0),
        }
        for row in rows
    ]


def _storage_namespace_snapshot(case_id: str, group: str, label: str, logical_bucket: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", f"fresh_08_namespace_{group}_{label}")
    script = r"""
import json, sys
from common import settings
settings.init_settings()
logical_bucket = sys.argv[1]
impl = settings.STORAGE_IMPL
inner = getattr(impl, "storage_impl", impl)
fixed_bucket = bool(getattr(inner, "bucket", None))
conn = getattr(inner, "conn", None)
physical_bucket = getattr(inner, "bucket", None) if fixed_bucket else logical_bucket
if conn is not None and hasattr(conn, "bucket_exists"):
    exists = bool(conn.bucket_exists(physical_bucket))
else:
    exists = bool(impl.bucket_exists(logical_bucket)) if hasattr(impl, "bucket_exists") else None
logical_object_count = None
if conn is not None and hasattr(conn, "list_objects"):
    if not exists:
        logical_object_count = 0
    elif fixed_bucket:
        prefix_path = getattr(inner, "prefix_path", None)
        prefix = f"{prefix_path}/{logical_bucket}/" if prefix_path else f"{logical_bucket}/"
        logical_object_count = sum(1 for _ in conn.list_objects(physical_bucket, prefix=prefix, recursive=True))
    else:
        logical_object_count = sum(1 for _ in conn.list_objects(physical_bucket, recursive=True))
print("__FRESH_RESULT__" + json.dumps({
    "mode": "fixed_bucket" if fixed_bucket else "per_bucket",
    "physical_bucket_exists": exists,
    "logical_object_count": logical_object_count,
    "implementation": type(inner).__name__,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, logical_bucket],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    result = json.loads(marker)
    path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    _evidence_module().write_evidence(path, {"storage_namespace": result})
    return {**result, "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _start_skill_backend_stub(group: str, space_name: str) -> tuple[http.server.ThreadingHTTPServer, threading.Thread, dict[str, Any]]:
    api_port = int(urlparse(DB._api_base(group)).port or 0)
    state: dict[str, Any] = {
        "mode": "success",
        "space_name": space_name,
        "events": [],
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            state["events"].append({"method": "GET", "path": parsed.path, "query": parse_qs(parsed.query), "mode": state["mode"]})
            if parsed.path == "/api/v1/skills/spaces":
                self._reply(
                    200,
                    {
                        "code": 0,
                        "data": {"spaces": [{"id": "fresh-controlled-space-id", "name": state["space_name"]}]},
                    },
                )
                return
            self._reply(404, {"code": 404})

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            state["events"].append({"method": "DELETE", "path": parsed.path, "query": parse_qs(parsed.query), "mode": state["mode"]})
            if parsed.path != "/api/v1/skills/index":
                self._reply(404, {"code": 404})
            elif state["mode"] == "success":
                self._reply(200, {"code": 0, "data": True})
            else:
                self._reply(500, {"code": 500, "message": "controlled failure"})

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", api_port + 4), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, state


def _record_skill_stub_events(case_id: str, group: str, label: str, events: list[dict[str, Any]]) -> str:
    path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    _evidence_module().write_evidence(path, {"stub_events": events})
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _api_log_checkpoint(group: str) -> tuple[Path, int]:
    processes = json.loads((RUNTIME_DIR / "processes.json").read_text())
    path = Path(processes[f"{group}:api"]["log_path"])
    return path, path.stat().st_size


def _api_log_pattern_counts(checkpoint: tuple[Path, int], patterns: list[str]) -> dict[str, int]:
    path, offset = checkpoint
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        text = handle.read()
    return {pattern: text.count(pattern) for pattern in patterns}


def _cleanup_secondary_user(case_id: str, group: str, email: str) -> bool:
    if not DB._email_count(group, [email]):
        return False
    try:
        result = DB._disable_and_delete_user(case_id, group, email)
    except requests.exceptions.JSONDecodeError:
        return DB._email_count(group, [email]) == 0
    return result["disabled"]["code"] == 0 and result["deleted"]["code"] == 0 and DB._email_count(group, [email]) == 0


def _response_has_no_data(response: dict[str, Any]) -> bool:
    return response.get("data") in (None, {}, [])


def _commit_access_observed(
    response: dict[str, Any],
    *,
    before_commit_count: int,
    after_commit_count: int,
    before_file: dict[str, Any],
    after_file: dict[str, Any],
    before_object: dict[str, Any],
    after_object: dict[str, Any],
) -> dict[str, Any]:
    return {
        "response": _response_pair(response),
        "data_absent": _response_has_no_data(response),
        "commit_delta": after_commit_count - before_commit_count,
        "file_unchanged": before_file == after_file,
        "object_unchanged": (
            before_object.get("exists"),
            before_object.get("length"),
            before_object.get("sha256"),
        )
        == (
            after_object.get("exists"),
            after_object.get("length"),
            after_object.get("sha256"),
        ),
    }


def _cleanup_commit_workspace(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    folder_ids: list[str],
    object_addresses: list[tuple[str, str]],
    secondary_email: str,
) -> dict[str, Any]:
    file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
    object_states = [
        _storage_snapshot(
            case_id,
            group,
            f"prove_commit_object_cleanup_{index + 1}",
            parent_id,
            location,
        )
        for index, (parent_id, location) in enumerate(sorted(set(object_addresses)))
    ]
    user_cleanup = _cleanup_secondary_user(case_id, group, secondary_email)
    return {
        "active_file_cleanup": file_cleanup,
        "object_count_checked": len(object_states),
        "objects_remaining": sum(item.get("exists") is True for item in object_states),
        "secondary_user_cleanup": user_cleanup,
        "retained_commit_rows_without_delete_api": sum(len(_commit_rows(group, folder_id=folder_id)) for folder_id in folder_ids),
        "cleanup_succeeded": file_cleanup and all(item.get("exists") is False for item in object_states) and user_cleanup,
    }


def _source_contract(group: str, row: dict[str, Any], api_value: Any) -> bool:
    if group == "control":
        return row.get("source_type") == "" and api_value == ""
    return row.get("source_type") is None and api_value == ""


def _response_pair(response: dict[str, Any]) -> list[Any]:
    return [response.get("http_status"), response.get("code")]


def _result(
    passed: bool,
    steps: list[dict[str, Any]],
    oracle: dict[str, Any],
    finding_id: str,
    summary: str,
) -> dict[str, Any]:
    code_location = "api/apps/restful_apis/file_commit_api.py" if finding_id.startswith(("FM-COMMIT-", "FM-FILE-VERSIONS-", "FM-DATASET-COMMIT-")) else "api/apps/restful_apis/file_api.py"
    return {
        "status": "PASS" if passed else "FAIL",
        "steps": steps,
        "oracle": oracle,
        "findings": []
        if passed
        else [
            {
                "id": finding_id,
                "summary": summary,
                "code_location": code_location,
            }
        ],
    }


def _finalize(recorder: Any, case_id: str) -> dict[str, Any]:
    result = recorder.finalize()
    _evidence_module().write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def _run_case(case_id: str, execute: Callable[[str, dict[str, str]], dict[str, Any]]) -> dict[str, Any]:
    recorder = _evidence_module().CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        result = execute(group, _owner(case_id, group))
        if not group_result_shape_ok(result):
            raise ValueError(f"invalid group result for {case_id}/{group}")
        recorder.add_group(group, result["status"], result["steps"], oracle=result["oracle"], findings=result.get("findings", []))
    return _finalize(recorder, case_id)


def _run_fm_001_to_018(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_prefix(case_id, group, owner, prefix)
        root = _root(case_id, group, owner)
        root_id = root["row"]["id"]

        if number == 1:
            name = f"{prefix}-{group}-folder"
            response = _create_folder(case_id, group, owner, "create_root_folder", name)
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            rows = _file_rows(group, ids=[str(data.get("id") or "")]) if data.get("id") else []
            row = rows[0] if len(rows) == 1 else {}
            orm_source = _orm_source_type(group, row["id"]) if row else None
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(response),
                "id_present": bool(data.get("id")),
                "name_matches": row.get("name") == name,
                "parent_matches": row.get("parent_id") == root_id,
                "type": row.get("type"),
                "tenant_matches": row.get("tenant_id") == owner["tenant_id"],
                "creator_matches": row.get("created_by") == owner["tenant_id"],
                "orm_source_type": orm_source,
                "api_source_type": data.get("source_type"),
                "physical_source_is_empty": row.get("source_type") == "",
                "physical_source_is_null": row.get("source_type") is None,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and folder_create_contract_ok(group, observed)
            return _result(
                passed,
                [
                    {"name": "initialize_root_then_create_folder", "response": observed["response"], "raw_sha256": response.get("raw_sha256")},
                    {"name": "read_only_verify_file_row_and_source_dialect", **{k: v for k, v in observed.items() if k not in {"response"}}},
                ],
                {"parent": "current tenant root", "source_type": "MySQL empty string; GaussDB NULL; ORM/API empty string"},
                "FM-FOLDER-CREATE-001",
                f"{group} root folder creation or source_type dialect contract differed",
            )

        if number == 2:
            parent = _create_folder(case_id, group, owner, "create_parent_folder", f"{prefix}-{group}-parent")
            parent_id = str((parent.get("data") or {}).get("id") or "")
            child = _create_folder(case_id, group, owner, "create_nested_folder", f"{prefix}-{group}-child", parent_id=parent_id)
            child_id = str((child.get("data") or {}).get("id") or "")
            rows = _file_rows(group, ids=[child_id]) if child_id else []
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            passed = preclean and _response_pair(parent) == [200, 0] and _response_pair(child) == [200, 0] and len(rows) == 1 and rows[0]["parent_id"] == parent_id and cleanup
            return _result(
                passed,
                [
                    {"name": "create_parent_then_nested_child", "responses": [_response_pair(parent), _response_pair(child)], "raw_sha256": [parent.get("raw_sha256"), child.get("raw_sha256")]},
                    {"name": "verify_parent_id_and_recursive_api_cleanup", "child_parent_matches": bool(rows and rows[0]["parent_id"] == parent_id), "cleanup_succeeded": cleanup},
                ],
                {"child_parent_id": "exact API-created parent"},
                "FM-NESTED-CREATE-001",
                f"{group} nested folder relationship differed",
            )

        if number == 3:
            name = f"{prefix}-{group}-duplicate"
            first = _create_folder(case_id, group, owner, "create_original_folder", name)
            before = len(_file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix))
            second = _create_folder(case_id, group, owner, "reject_duplicate_folder", name)
            after = len(_file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix))
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            passed = (
                preclean
                and _response_pair(first) == [200, 0]
                and _response_pair(second) == [200, 102]
                and second.get("message") == "Duplicated folder name in the same folder."
                and before == after == 1
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_then_repeat_same_parent_name",
                        "responses": [_response_pair(first), _response_pair(second)],
                        "duplicate_message": second.get("message"),
                        "raw_sha256": [first.get("raw_sha256"), second.get("raw_sha256")],
                    },
                    {"name": "verify_zero_duplicate_delta_and_cleanup", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"duplicate": [200, 102, "Duplicated folder name in the same folder."]},
                "FM-DUPLICATE-FOLDER-001",
                f"{group} duplicate folder handling differed",
            )

        if number == 4:
            virtual = _create_folder(case_id, group, owner, "create_implicit_virtual", f"{prefix}-{group}-virtual", file_type=None)
            virtual_data = virtual.get("data") if isinstance(virtual.get("data"), dict) else {}
            row_list = _file_rows(group, ids=[str(virtual_data.get("id") or "")]) if virtual_data.get("id") else []
            row = row_list[0] if row_list else {}
            listed = _http(case_id, group, "list_virtual_from_database", owner["auth"], "GET", "/files", params={"parent_id": root_id, "page": 1, "page_size": 100})
            bogus = _create_folder(case_id, group, owner, "reject_unknown_folder_type", f"{prefix}-{group}-bogus", file_type="bogus")
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            list_items = (listed.get("data") or {}).get("files", []) if isinstance(listed.get("data"), dict) else []
            listed_virtual = next((item for item in list_items if item.get("id") == virtual_data.get("id")), {})
            location_ok = (group == "control" and row.get("location") == "") or (group == "experiment" and row.get("location") is None)
            passed = (
                preclean
                and _response_pair(virtual) == [200, 0]
                and row.get("type") == "virtual"
                and row.get("size") == 0
                and location_ok
                and listed_virtual.get("id") == virtual_data.get("id")
                and _response_pair(bogus) == [200, 101]
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_implicit_virtual_and_read_database_backed_list",
                        "response": _response_pair(virtual),
                        "physical_location": row.get("location"),
                        "listed_location": listed_virtual.get("location"),
                        "raw_sha256": [virtual.get("raw_sha256"), listed.get("raw_sha256")],
                    },
                    {"name": "reject_unknown_type_and_cleanup", "response": _response_pair(bogus), "cleanup_succeeded": cleanup, "raw_sha256": bogus.get("raw_sha256")},
                ],
                {"implicit_type": "virtual", "unknown_type": [200, 101]},
                "FM-FOLDER-TYPE-VALIDATION-001",
                f"{group} accepted an unknown File type or virtual storage semantics differed",
            )

        if number in {5, 6, 7}:
            before = len(_file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix))
            if number == 5:
                missing = _missing_file_id(group)
                response = _create_folder(case_id, group, owner, "reject_missing_parent", f"{prefix}-{group}-orphan", parent_id=missing)
                expected_code, expected_message = 102, "Parent Folder Doesn't Exist!"
                finding = "FM-MISSING-PARENT-ERROR-001"
            elif number == 6:
                response = _create_folder(case_id, group, owner, "reject_empty_name", "")
                expected_code, expected_message = 101, "Invalid request parameters"
                finding = "FM-EMPTY-NAME-VALIDATION-001"
            else:
                response = _create_folder(case_id, group, owner, "reject_long_name", "A" * 300)
                expected_code, expected_message = 101, "Invalid request parameters"
                finding = "FM-LONG-NAME-VALIDATION-001"
            after = len(_file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix))
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            message_ok = response.get("message") == expected_message if number == 5 else isinstance(response.get("message"), str) and bool(response.get("message"))
            passed = preclean and _response_pair(response) == [200, expected_code] and message_ok and before == after == 0 and cleanup
            return _result(
                passed,
                [
                    {"name": "submit_invalid_folder_request", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {"name": "verify_zero_file_delta", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"response_code": expected_code, "message": expected_message},
                finding,
                f"{group} invalid folder request response or zero-delta contract differed",
            )

        if number in {8, 9, 10}:
            target_id = root_id
            folder_response = None
            if number == 10:
                folder_response = _create_folder(case_id, group, owner, "create_upload_target", f"{prefix}-{group}-target")
                target_id = str((folder_response.get("data") or {}).get("id") or "")
            file_specs = [(f"{prefix}-{group}-one.pdf", b"%PDF-fresh-file-one", "application/pdf")]
            if number == 9:
                file_specs = [
                    (f"{prefix}-{group}-one.pdf", b"pdf-fresh", "application/pdf"),
                    (f"{prefix}-{group}-two.docx", b"docx-fresh", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                    (f"{prefix}-{group}-three.txt", b"txt-fresh", "text/plain"),
                ]
            response = _upload(case_id, group, owner, "upload_files", file_specs, parent_id=target_id if number == 10 else None)
            data = response.get("data") if isinstance(response.get("data"), list) else []
            ids = [str(item.get("id")) for item in data if item.get("id")]
            rows = _file_rows(group, ids=ids)
            row_by_id = {row["id"]: row for row in rows}
            snapshots = []
            all_object_ok = True
            for item, spec in zip(data, file_specs):
                row = row_by_id.get(str(item.get("id")), {})
                snapshot = _storage_snapshot(case_id, group, f"read_only_uploaded_object_{len(snapshots) + 1}", str(row.get("parent_id") or ""), str(row.get("location") or "")) if row else {}
                snapshots.append(snapshot)
                all_object_ok = all_object_ok and snapshot.get("exists") is True and snapshot.get("length") == len(spec[1]) and snapshot.get("sha256") == hashlib.sha256(spec[1]).hexdigest()
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            expected_types = [expected_upload_type(spec[0]) for spec in file_specs]
            observed = {
                "response": _response_pair(response),
                "returned_count": len(data),
                "database_count": len(rows),
                "parent_matches": len(rows) == len(file_specs) and all(row["parent_id"] == target_id for row in rows),
                "size_matches": len(rows) == len(file_specs) and sorted(row["size"] for row in rows) == sorted(len(spec[1]) for spec in file_specs),
                "type_matches": sorted(row["type"] for row in rows) == sorted(expected_types),
                "location_nonempty": len(rows) == len(file_specs) and all(bool(row["location"]) for row in rows),
                "object_exists": all_object_ok,
                "object_size_matches": all_object_ok,
                "object_sha_matches": all_object_ok,
                "source_contract_ok": len(rows) == len(file_specs)
                and all(_source_contract(group, row, next((item.get("source_type") for item in data if item.get("id") == row["id"]), None)) for row in rows),
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and (folder_response is None or _response_pair(folder_response) == [200, 0]) and upload_contract_ok(observed, expected_count=len(file_specs))
            return _result(
                passed,
                [
                    {"name": "upload_api_files", "response": observed["response"], "returned_count": len(data), "raw_sha256": response.get("raw_sha256")},
                    {
                        "name": "verify_database_and_object_bytes",
                        "database_count": len(rows),
                        "object_checks": [{k: value for k, value in snapshot.items() if k != "sha256"} for snapshot in snapshots],
                    },
                    {"name": "cleanup_through_file_api", "cleanup_succeeded": cleanup},
                ],
                {"database": "exact metadata", "storage": "exact bytes at parent_id/location"},
                "FM-UPLOAD-STORAGE-001",
                f"{group} upload database or object-store contract differed",
            )

        if number in {11, 12, 13}:
            before = len(_file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix))
            if number == 11:
                missing = _missing_file_id(group)
                response = _upload(case_id, group, owner, "reject_missing_upload_folder", [(f"{prefix}-{group}.txt", b"missing-parent", "text/plain")], parent_id=missing)
                expected_code, expected_message = 102, "Can't find this folder!"
            elif number == 12:
                response = _http(case_id, group, "reject_no_file_part", owner["auth"], "POST", "/files", files=[], multipart_fields={"parent_id": root_id})
                expected_code, expected_message = 101, "No file part!"
            else:
                response = _upload(case_id, group, owner, "reject_empty_filename", [("", b"", "application/octet-stream")], parent_id=root_id)
                expected_code, expected_message = 101, "No file selected!"
            after = len(_file_rows(group, tenant_id=owner["tenant_id"], prefix=prefix))
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            passed = preclean and _response_pair(response) == [200, expected_code] and response.get("message") == expected_message and before == after == 0 and cleanup
            return _result(
                passed,
                [
                    {"name": "submit_invalid_multipart_request", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {"name": "verify_zero_metadata_delta", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, expected_code, expected_message]},
                "FM-UPLOAD-VALIDATION-001",
                f"{group} invalid multipart response or zero-delta contract differed",
            )

        if number in {14, 15, 16}:
            folder = _create_folder(case_id, group, owner, "create_list_folder", f"{prefix}-{group}-folder")
            folder_id = str((folder.get("data") or {}).get("id") or "")
            fixture_ids: list[str] = [folder_id]
            query_parent = root_id
            query_params: dict[str, Any] = {"page": 1, "page_size": 100}
            expected_ids: list[str]
            if number == 14:
                _create_folder(case_id, group, owner, "create_child_folder", f"{prefix}-{group}-child-folder", parent_id=folder_id)
                _upload(case_id, group, owner, "upload_child_file", [(f"{prefix}-{group}-child.txt", b"child-bytes", "text/plain")], parent_id=folder_id)
                root_upload = _upload(case_id, group, owner, "upload_root_file", [(f"{prefix}-{group}-root.txt", b"root-bytes", "text/plain")])
                root_file_id = str(((root_upload.get("data") or [{}])[0]).get("id") or "")
                fixture_ids.append(root_file_id)
                expected_ids = sorted(fixture_ids)
            elif number == 15:
                upload = _upload(case_id, group, owner, "upload_two_children", [(f"{prefix}-{group}-a.txt", b"a", "text/plain"), (f"{prefix}-{group}-b.txt", b"bb", "text/plain")], parent_id=folder_id)
                expected_ids = sorted(str(item.get("id")) for item in (upload.get("data") or []) if item.get("id"))
                query_parent = folder_id
                query_params["parent_id"] = folder_id
            else:
                upload = _upload(
                    case_id,
                    group,
                    owner,
                    "upload_keyword_files",
                    [(f"{prefix}-{group}-ReportAlpha.txt", b"a", "text/plain"), (f"{prefix}-{group}-reportBeta.txt", b"b", "text/plain"), (f"{prefix}-{group}-noise.txt", b"c", "text/plain")],
                    parent_id=folder_id,
                )
                items = upload.get("data") or []
                expected_ids = sorted(str(item.get("id")) for item in items if "report" in str(item.get("name", "")).lower())
                query_parent = folder_id
                query_params.update({"parent_id": folder_id, "keywords": "report"})
            listed = _http(case_id, group, "list_fixture_files", owner["auth"], "GET", "/files", params=query_params)
            list_data = listed.get("data") if isinstance(listed.get("data"), dict) else {}
            returned = list_data.get("files", []) if isinstance(list_data.get("files"), list) else []
            returned_fixture_ids = sorted(str(item.get("id")) for item in returned if str(item.get("id")) in set(expected_ids))
            db_children = _file_rows(group, parent_id=query_parent)
            db_scope = filter_rows_by_keywords(db_children, query_params.get("keywords"))
            query_rows = _file_rows(group, ids=[query_parent])
            query_folder = query_rows[0] if len(query_rows) == 1 else {}
            folder_item = next((item for item in returned if item.get("id") == folder_id), {})
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(listed),
                "fixture_ids_expected": expected_ids,
                "fixture_ids_returned": returned_fixture_ids,
                "total_matches_database": list_data.get("total") == len(db_scope),
                "all_parent_matches": all(str(item.get("parent_id")) == query_parent for item in returned),
                "parent_folder_matches": isinstance(list_data.get("parent_folder"), dict) and response_parent_matches_query_folder(list_data["parent_folder"], query_folder),
                "folder_sizes_match": number != 14 or folder_item.get("size") == len(b"child-bytes"),
                "has_child_folder_boolean": number != 14 or folder_item.get("has_child_folder") is True,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(folder) == [200, 0] and list_contract_ok(observed)
            return _result(
                passed,
                [
                    {"name": "create_unique_list_fixture", "expected_ids": expected_ids},
                    {
                        "name": "list_and_compare_database_parent_set",
                        "response": observed["response"],
                        "returned_fixture_ids": returned_fixture_ids,
                        "total": list_data.get("total"),
                        "database_scope_total": len(db_scope),
                        "database_unfiltered_total": len(db_children),
                        "raw_sha256": listed.get("raw_sha256"),
                    },
                    {"name": "cleanup_fixture_tree", "cleanup_succeeded": cleanup},
                ],
                {"scope": "exact parent and fixture IDs", "root_baseline": "system folders initialized before fixture"},
                "FM-LIST-CONTRACT-001",
                f"{group} File list/filter/parent contract differed",
            )

        if number == 17:
            folder = _create_folder(case_id, group, owner, "create_pagination_folder", f"{prefix}-{group}-folder")
            folder_id = str((folder.get("data") or {}).get("id") or "")
            ids = []
            for index in range(10):
                response = _upload(case_id, group, owner, f"upload_page_file_{index + 1:02d}", [(f"{prefix}-{group}-{index + 1:02d}.txt", f"page-{index}".encode(), "text/plain")], parent_id=folder_id)
                ids.extend(str(item.get("id")) for item in (response.get("data") or []) if item.get("id"))
                time.sleep(0.01)
            page1 = _http(case_id, group, "list_page_1", owner["auth"], "GET", "/files", params={"parent_id": folder_id, "orderby": "create_time", "desc": "true", "page": 1, "page_size": 5})
            page2 = _http(case_id, group, "list_page_2", owner["auth"], "GET", "/files", params={"parent_id": folder_id, "orderby": "create_time", "desc": "true", "page": 2, "page_size": 5})
            p1 = (page1.get("data") or {}).get("files", []) if isinstance(page1.get("data"), dict) else []
            p2 = (page2.get("data") or {}).get("files", []) if isinstance(page2.get("data"), dict) else []
            invalid_params = [
                {"parent_id": folder_id, "page": 0},
                {"parent_id": folder_id, "page_size": 0},
                {"parent_id": folder_id, "page_size": 101},
                {"parent_id": folder_id, "desc": "not_bool"},
                {"parent_id": folder_id, "orderby": "__bad__"},
            ]
            invalid = [_http(case_id, group, f"reject_invalid_pagination_{index + 1}", owner["auth"], "GET", "/files", params=params) for index, params in enumerate(invalid_params)]
            rows = _file_rows(group, ids=ids)
            ordered_times = [int(item.get("create_time") or 0) for item in p1 + p2]
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            observed = {
                "legal_responses": [_response_pair(page1), _response_pair(page2)],
                "page_lengths": [len(p1), len(p2)],
                "pages_disjoint": not ({str(item.get("id")) for item in p1} & {str(item.get("id")) for item in p2}),
                "pages_complete": {str(item.get("id")) for item in p1 + p2} == set(ids),
                "descending": ordered_times == sorted(ordered_times, reverse=True),
                "total_matches": (page1.get("data") or {}).get("total") == 10 and (page2.get("data") or {}).get("total") == 10,
                "invalid_responses": [_response_pair(item) for item in invalid],
                "database_unchanged": len(rows) == 10,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and pagination_contract_ok(observed)
            return _result(
                passed,
                [
                    {"name": "create_ten_distinct_pagination_files", "count": len(ids)},
                    {
                        "name": "verify_two_disjoint_descending_pages",
                        **{k: v for k, v in observed.items() if k not in {"invalid_responses", "cleanup_succeeded"}},
                        "raw_sha256": [page1.get("raw_sha256"), page2.get("raw_sha256")],
                    },
                    {"name": "exercise_invalid_pagination_and_order_fields", "responses": observed["invalid_responses"], "raw_sha256": [item.get("raw_sha256") for item in invalid]},
                    {"name": "cleanup_fixture", "cleanup_succeeded": cleanup},
                ],
                {"legal": "two complete disjoint descending pages", "invalid": "all code 101"},
                "FM-LIST-PARAMETER-VALIDATION-001",
                f"{group} File pagination/order validation contract differed",
            )

        if number == 18:
            missing = _missing_file_id(group)
            response = _http(case_id, group, "list_missing_folder", owner["auth"], "GET", "/files", params={"parent_id": missing})
            cleanup = _cleanup_prefix(case_id, group, owner, prefix)
            passed = preclean and _response_pair(response) == [200, 102] and response.get("message") == "Folder not found!" and not _file_rows(group, ids=[missing]) and cleanup
            return _result(
                passed,
                [
                    {"name": "prove_parent_absent_then_list", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {"name": "verify_no_created_missing_parent", "still_absent": not _file_rows(group, ids=[missing]), "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "Folder not found!"]},
                "FM-LIST-MISSING-FOLDER-001",
                f"{group} missing folder list response differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_fm_019_to_028(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        _root(case_id, group, owner)

        if number == 19:
            destination = _create_folder(case_id, group, owner, "create_valid_destination", f"{prefix}-{group}-destination")
            destination_id = str((destination.get("data") or {}).get("id") or "")
            uploads = _upload(
                case_id,
                group,
                owner,
                "upload_move_and_nonfolder_destination_fixtures",
                [
                    (f"{prefix}-{group}-valid.txt", b"valid-move-bytes", "text/plain"),
                    (f"{prefix}-{group}-unsafe.txt", b"unsafe-move-bytes", "text/plain"),
                    (f"{prefix}-{group}-ordinary.txt", b"ordinary-file-bytes", "text/plain"),
                ],
            )
            upload_items = uploads.get("data") if isinstance(uploads.get("data"), list) else []
            ids = [str(item.get("id") or "") for item in upload_items]
            valid_id, unsafe_id, ordinary_id = (ids + ["", "", ""])[:3]
            valid_before = _file_row(group, valid_id)
            unsafe_before = _file_row(group, unsafe_id)
            valid_before_object = _storage_snapshot(case_id, group, "read_valid_object_before_move", valid_before.get("parent_id", ""), valid_before.get("location", "")) if valid_before else {}
            unsafe_before_object = _storage_snapshot(case_id, group, "read_unsafe_object_before_move", unsafe_before.get("parent_id", ""), unsafe_before.get("location", "")) if unsafe_before else {}

            moved = _move_files(case_id, group, owner, "move_to_folder", [valid_id], dest_file_id=destination_id)
            valid_after = _file_row(group, valid_id)
            valid_old_after = _storage_snapshot(case_id, group, "prove_old_valid_object_absent", valid_before.get("parent_id", ""), valid_before.get("location", "")) if valid_before else {}
            valid_new_after = _storage_snapshot(case_id, group, "read_new_valid_object", valid_after.get("parent_id", ""), valid_after.get("location", "")) if valid_after else {}

            rejected = _move_files(case_id, group, owner, "reject_ordinary_file_destination", [unsafe_id], dest_file_id=ordinary_id)
            unsafe_after = _file_row(group, unsafe_id)
            unsafe_old_after = (
                _storage_snapshot(case_id, group, "read_unsafe_old_address_after_request", unsafe_before.get("parent_id", ""), unsafe_before.get("location", "")) if unsafe_before else {}
            )
            unsafe_actual_after = (
                _storage_snapshot(case_id, group, "read_unsafe_actual_address_after_request", unsafe_after.get("parent_id", ""), unsafe_after.get("location", "")) if unsafe_after else {}
            )
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            valid_observed = {
                "response": _response_pair(moved),
                "row_count": int(bool(valid_after)),
                "parent_matches": valid_after.get("parent_id") == destination_id,
                "name_matches": valid_after.get("name") == valid_before.get("name"),
                "location_changed": (valid_before.get("parent_id"), valid_before.get("location")) != (valid_after.get("parent_id"), valid_after.get("location")),
                "old_object_absent": valid_old_after.get("exists") is False,
                "new_object_exists": valid_new_after.get("exists") is True,
                "new_object_length_matches": valid_new_after.get("length") == len(b"valid-move-bytes"),
                "new_object_sha_matches": valid_new_after.get("sha256") == hashlib.sha256(b"valid-move-bytes").hexdigest(),
                "cleanup_succeeded": cleanup,
            }
            rejection_ok = (
                _response_pair(rejected) == [200, 102]
                and isinstance(rejected.get("message"), str)
                and bool(rejected.get("message"))
                and unsafe_after == unsafe_before
                and unsafe_old_after.get("exists") is True
                and unsafe_old_after.get("sha256") == unsafe_before_object.get("sha256")
                and unsafe_actual_after.get("exists") is True
            )
            passed = (
                preclean
                and _response_pair(destination) == [200, 0]
                and _response_pair(uploads) == [200, 0]
                and valid_before_object.get("exists") is True
                and move_contract_ok(valid_observed, require_location_change=True)
                and rejection_ok
            )
            return _result(
                passed,
                [
                    {"name": "move_real_file_and_verify_storage_transition", **valid_observed, "raw_sha256": moved.get("raw_sha256")},
                    {
                        "name": "reject_nonfolder_destination_without_mutation",
                        "response": _response_pair(rejected),
                        "message": rejected.get("message"),
                        "row_unchanged": unsafe_after == unsafe_before,
                        "old_object_exists": unsafe_old_after.get("exists"),
                        "actual_parent_id_is_ordinary_file": unsafe_after.get("parent_id") == ordinary_id,
                        "raw_sha256": rejected.get("raw_sha256"),
                    },
                ],
                {"valid_move": "database parent plus exact object bytes move", "ordinary_file_destination": "HTTP 200/code 102 and no mutation"},
                "FM-MOVE-NONFOLDER-DEST-001",
                f"{group} valid move or non-folder destination safety contract differed",
            )

        if number == 20:
            destination = _create_folder(case_id, group, owner, "create_batch_destination", f"{prefix}-{group}-destination")
            destination_id = str((destination.get("data") or {}).get("id") or "")
            contents = [b"batch-one", b"batch-two", b"batch-three"]
            upload = _upload(
                case_id,
                group,
                owner,
                "upload_three_batch_files",
                [(f"{prefix}-{group}-{index + 1}.txt", content, "text/plain") for index, content in enumerate(contents)],
            )
            ids = [str(item.get("id") or "") for item in (upload.get("data") or [])]
            before = {file_id: _file_row(group, file_id) for file_id in ids}
            move = _move_files(case_id, group, owner, "move_three_files", ids, dest_file_id=destination_id)
            after = {file_id: _file_row(group, file_id) for file_id in ids}
            storage_checks = []
            for index, file_id in enumerate(ids):
                old = before[file_id]
                new = after[file_id]
                old_state = _storage_snapshot(case_id, group, f"prove_batch_old_absent_{index + 1}", old.get("parent_id", ""), old.get("location", ""))
                new_state = _storage_snapshot(case_id, group, f"read_batch_new_object_{index + 1}", new.get("parent_id", ""), new.get("location", "")) if new else {}
                storage_checks.append(old_state.get("exists") is False and new_state.get("exists") is True and new_state.get("sha256") == hashlib.sha256(contents[index]).hexdigest())
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = (
                preclean
                and _response_pair(destination) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and len(ids) == 3
                and _response_pair(move) == [200, 0]
                and all(row.get("parent_id") == destination_id for row in after.values())
                and all(storage_checks)
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "upload_and_batch_move_three_files",
                        "responses": [_response_pair(upload), _response_pair(move)],
                        "file_count": len(ids),
                        "raw_sha256": [upload.get("raw_sha256"), move.get("raw_sha256")],
                    },
                    {
                        "name": "verify_all_database_parents_and_object_bytes",
                        "all_parents_match": all(row.get("parent_id") == destination_id for row in after.values()),
                        "storage_checks": storage_checks,
                        "cleanup_succeeded": cleanup,
                    },
                ],
                {"batch": "all three rows and exact objects move atomically enough for successful response"},
                "FM-BATCH-MOVE-001",
                f"{group} batch file move contract differed",
            )

        if number == 21:
            dataset_prefix = f"{prefix}-{group}-dataset"
            dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], dataset_prefix)
            dataset = DD._create_dataset(case_id, group, owner["auth"], "create_rename_dataset", {"name": dataset_prefix})
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            upload = _upload(case_id, group, owner, "upload_rename_file", [(f"{prefix}-{group}-old.txt", b"rename-object-bytes", "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            before = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_rename_object_before", before.get("parent_id", ""), before.get("location", "")) if before else {}
            link = _http(case_id, group, "link_rename_file_to_dataset", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [file_id], "kb_ids": [dataset_id]})
            linked_rows = _wait_file_documents(group, [file_id], dataset_id, expected_count=1)
            new_name = f"{prefix}-{group}-new.txt"
            rename = _move_files(case_id, group, owner, "rename_file_in_place", [file_id], new_name=new_name)
            after = _file_row(group, file_id)
            document_rows = _file_document_rows(group, file_ids=[file_id], dataset_id=dataset_id)
            after_object = _storage_snapshot(case_id, group, "read_rename_object_after", after.get("parent_id", ""), after.get("location", "")) if after else {}
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            dataset_cleanup_response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_rename_dataset", [dataset_id]) if dataset_id else {"code": 0}
            dataset_cleanup = not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            cleanup = file_cleanup and dataset_cleanup_response.get("code") == 0 and dataset_cleanup
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and _response_pair(link) == [200, 0]
                and link.get("data") is True
                and len(linked_rows) == 1
                and _response_pair(rename) == [200, 0]
                and after.get("name") == new_name
                and after.get("parent_id") == before.get("parent_id")
                and after.get("location") == before.get("location")
                and len(document_rows) == 1
                and document_rows[0].get("name") == new_name
                and before_object.get("exists") is True
                and after_object.get("exists") is True
                and before_object.get("sha256") == after_object.get("sha256") == hashlib.sha256(b"rename-object-bytes").hexdigest()
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "link_file_then_rename_in_place",
                        "responses": [_response_pair(link), _response_pair(rename)],
                        "linked_before_rename": len(linked_rows) == 1,
                        "raw_sha256": [link.get("raw_sha256"), rename.get("raw_sha256")],
                    },
                    {
                        "name": "verify_file_document_and_object_hash",
                        "file_name": after.get("name"),
                        "parent_unchanged": after.get("parent_id") == before.get("parent_id"),
                        "location_unchanged": after.get("location") == before.get("location"),
                        "document_name_matches": len(document_rows) == 1 and document_rows[0].get("name") == new_name,
                        "object_hash_unchanged": before_object.get("sha256") == after_object.get("sha256"),
                        "cleanup_succeeded": cleanup,
                    },
                ],
                {"rename": "file and linked document names change; object address and bytes remain"},
                "FM-RENAME-LINKED-DOCUMENT-001",
                f"{group} in-place rename, linked document, or object contract differed",
            )

        if number == 22:
            destination = _create_folder(case_id, group, owner, "create_move_rename_destination", f"{prefix}-{group}-destination")
            destination_id = str((destination.get("data") or {}).get("id") or "")
            upload = _upload(case_id, group, owner, "upload_move_rename_file", [(f"{prefix}-{group}-old.txt", b"move-rename-bytes", "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            before = _file_row(group, file_id)
            new_name = f"{prefix}-{group}-renamed.txt"
            move = _move_files(case_id, group, owner, "move_and_rename", [file_id], dest_file_id=destination_id, new_name=new_name)
            after = _file_row(group, file_id)
            old_object = _storage_snapshot(case_id, group, "prove_move_rename_old_absent", before.get("parent_id", ""), before.get("location", "")) if before else {}
            new_object = _storage_snapshot(case_id, group, "read_move_rename_new_object", after.get("parent_id", ""), after.get("location", "")) if after else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(move),
                "row_count": int(bool(after)),
                "parent_matches": after.get("parent_id") == destination_id,
                "name_matches": after.get("name") == new_name,
                "location_changed": (before.get("parent_id"), before.get("location")) != (after.get("parent_id"), after.get("location")),
                "old_object_absent": old_object.get("exists") is False,
                "new_object_exists": new_object.get("exists") is True,
                "new_object_length_matches": new_object.get("length") == len(b"move-rename-bytes"),
                "new_object_sha_matches": new_object.get("sha256") == hashlib.sha256(b"move-rename-bytes").hexdigest(),
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(destination) == [200, 0] and _response_pair(upload) == [200, 0] and move_contract_ok(observed, require_location_change=True)
            return _result(
                passed,
                [{"name": "move_and_rename_single_file", **observed, "raw_sha256": move.get("raw_sha256")}],
                {"row": "new parent and name", "storage": "old address absent and exact bytes at new address"},
                "FM-MOVE-RENAME-001",
                f"{group} move-and-rename contract differed",
            )

        if number in {23, 25, 26}:
            if number == 26:
                upload = _upload(
                    case_id,
                    group,
                    owner,
                    "upload_duplicate_name_fixtures",
                    [(f"{prefix}-{group}-report.pdf", b"report", "application/pdf"), (f"{prefix}-{group}-other.pdf", b"other", "application/pdf")],
                )
                items = upload.get("data") or []
                target_name = str((items[0] if items else {}).get("name") or "")
                file_id = str((items[1] if len(items) > 1 else {}).get("id") or "")
                expected_message = "Duplicated file name in the same folder."
                request_label = "reject_duplicate_rename"
                request_options = {"new_name": target_name}
            else:
                extension = "pdf" if number == 25 else "txt"
                content_type = "application/pdf" if number == 25 else "text/plain"
                upload = _upload(case_id, group, owner, "upload_rejection_fixture", [(f"{prefix}-{group}-original.{extension}", b"unchanged-object", content_type)])
                file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
                if number == 23:
                    expected_message = "Parent folder not found!"
                    request_label = "reject_missing_destination"
                    request_options = {"dest_file_id": _missing_file_id(group)}
                else:
                    expected_message = "The extension of file can't be changed"
                    request_label = "reject_extension_change"
                    request_options = {"new_name": f"{prefix}-{group}-changed.docx"}
            before = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_rejected_object_before", before.get("parent_id", ""), before.get("location", "")) if before else {}
            response = _move_files(
                case_id,
                group,
                owner,
                request_label,
                [file_id],
                **request_options,
            )
            after = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_rejected_object_after", after.get("parent_id", ""), after.get("location", "")) if after else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(response),
                "message": response.get("message"),
                "row_unchanged": before == after,
                "object_still_exists": after_object.get("exists") is True,
                "object_sha_unchanged": before_object.get("sha256") == after_object.get("sha256"),
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(upload) == [200, 0] and unchanged_file_contract_ok(observed, expected_code=102, expected_message=expected_message)
            finding = {23: "FM-MOVE-MISSING-DESTINATION-001", 25: "FM-RENAME-EXTENSION-001", 26: "FM-RENAME-DUPLICATE-001"}[number]
            return _result(
                passed,
                [{"name": "submit_rejected_move_or_rename", **observed, "raw_sha256": response.get("raw_sha256")}],
                {"response": [200, 102, expected_message], "mutation": "none"},
                finding,
                f"{group} rejected move/rename response or no-mutation contract differed",
            )

        if number == 24:
            folder_a = _create_folder(case_id, group, owner, "create_cycle_parent", f"{prefix}-{group}-a")
            folder_a_id = str((folder_a.get("data") or {}).get("id") or "")
            folder_b = _create_folder(case_id, group, owner, "create_cycle_child", f"{prefix}-{group}-b", parent_id=folder_a_id)
            folder_b_id = str((folder_b.get("data") or {}).get("id") or "")
            before = {file_id: _file_row(group, file_id) for file_id in (folder_a_id, folder_b_id)}
            response = _move_files(case_id, group, owner, "reject_folder_cycle", [folder_a_id], dest_file_id=folder_b_id)
            after = {file_id: _file_row(group, file_id) for file_id in (folder_a_id, folder_b_id)}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = (
                preclean
                and _response_pair(folder_a) == [200, 0]
                and _response_pair(folder_b) == [200, 0]
                and _response_pair(response) == [200, 102]
                and response.get("message") == "Cannot move a folder into its own subfolder."
                and before == after
                and cleanup
            )
            return _result(
                passed,
                [
                    {"name": "attempt_parent_into_descendant", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {"name": "verify_hierarchy_unchanged_and_cleanup", "rows_unchanged": before == after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "Cannot move a folder into its own subfolder."], "hierarchy": "unchanged"},
                "FM-MOVE-CYCLE-001",
                f"{group} folder cycle prevention contract differed",
            )

        if number in {27, 28}:
            upload_count = 2 if number == 28 else 1
            upload = _upload(
                case_id,
                group,
                owner,
                "upload_parameter_validation_fixtures",
                [(f"{prefix}-{group}-{index + 1}.pdf", f"parameter-{index}".encode(), "application/pdf") for index in range(upload_count)],
            )
            ids = [str(item.get("id") or "") for item in (upload.get("data") or [])]
            before = {file_id: _file_row(group, file_id) for file_id in ids}
            response = _move_files(case_id, group, owner, "reject_move_parameter_shape", ids, **({"new_name": f"{prefix}-{group}-single.pdf"} if number == 28 else {}))
            after = {file_id: _file_row(group, file_id) for file_id in ids}
            expected_fragment = "new_name can only be used with a single file" if number == 28 else "At least one of dest_file_id or new_name must be provided"
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = (
                preclean and _response_pair(upload) == [200, 0] and _response_pair(response) == [200, 101] and expected_fragment in str(response.get("message") or "") and before == after and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "submit_schema_invalid_move",
                        "response": _response_pair(response),
                        "message_contains_expected": expected_fragment in str(response.get("message") or ""),
                        "raw_sha256": response.get("raw_sha256"),
                    },
                    {"name": "verify_all_rows_unchanged_and_cleanup", "rows_unchanged": before == after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 101], "message_contains": expected_fragment, "mutation": "none"},
                "FM-MOVE-SCHEMA-VALIDATION-001",
                f"{group} move schema validation contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_fm_029_to_035(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        _root(case_id, group, owner)

        if number == 29:
            dataset_prefix = f"{prefix}-{group}-dataset"
            dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], dataset_prefix)
            dataset = DD._create_dataset(case_id, group, owner["auth"], "create_delete_dataset", {"name": dataset_prefix})
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            content = b"delete-linked-file-bytes"
            upload = _upload(case_id, group, owner, "upload_delete_linked_file", [(f"{prefix}-{group}.txt", content, "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            file_before = _file_row(group, file_id)
            link = _http(case_id, group, "link_delete_file_to_dataset", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [file_id], "kb_ids": [dataset_id]})
            relations = _wait_file_documents(group, [file_id], dataset_id, expected_count=1)
            document_ids = [row["document_id"] for row in relations]
            artifacts_before = _saved_artifact_counts(group, [file_id], document_ids)
            task_counts_before = DD._document_task_counts(group, document_ids)
            index_before = DD._docstore_index_artifact_snapshot(group, owner["tenant_id"], dataset_id)
            object_before = _storage_snapshot(case_id, group, "read_delete_object_before", file_before.get("parent_id", ""), file_before.get("location", "")) if file_before else {}
            deleted = _delete_files(case_id, group, owner, "delete_single_linked_file", [file_id])
            artifacts_after = _saved_artifact_counts(group, [file_id], document_ids)
            index_after = DD._docstore_index_artifact_snapshot(group, owner["tenant_id"], dataset_id)
            object_after = _storage_snapshot(case_id, group, "prove_delete_object_absent", file_before.get("parent_id", ""), file_before.get("location", "")) if file_before else {}
            dataset_cleanup_response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_delete_dataset", [dataset_id]) if dataset_id else {"code": 0}
            cleanup = not _file_rows(group, ids=[file_id]) and dataset_cleanup_response.get("code") == 0 and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            observed = {
                "response": _response_pair(deleted),
                "success_count": (deleted.get("data") or {}).get("success_count"),
                "file_rows_remaining": artifacts_after["files"],
                "file_links_remaining": artifacts_after["file_links"],
                "documents_remaining": artifacts_after["documents"],
                "tasks_remaining": artifacts_after["tasks"],
                "objects_remaining": int(object_after.get("exists") is True),
                "cleanup_succeeded": cleanup,
            }
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and _response_pair(link) == [200, 0]
                and link.get("data") is True
                and len(relations) == 1
                and artifacts_before == {"files": 1, "file_links": 1, "documents": 1, "tasks": sum(task_counts_before.values())}
                and object_before.get("exists") is True
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and int(index_before.get("total") or 0) == 0
                and int(index_after.get("total") or 0) == 0
                and delete_contract_ok(observed, expected_success_count=1)
            )
            return _result(
                passed,
                [
                    {
                        "name": "capture_saved_file_document_task_index_and_object_ids",
                        "file_id_present": bool(file_id),
                        "document_count": len(document_ids),
                        "task_count": sum(task_counts_before.values()),
                        "docengine_artifact_count": int(index_before.get("total") or 0),
                        "object_exists": object_before.get("exists"),
                    },
                    {"name": "delete_single_file_and_verify_saved_artifacts", **observed, "docengine_artifact_count": int(index_after.get("total") or 0), "raw_sha256": deleted.get("raw_sha256")},
                ],
                {"response": [200, 0], "success_count": 1, "saved_artifacts": "file/link/document/task/object/DocEngine absent"},
                "FM-DELETE-SINGLE-CASCADE-001",
                f"{group} single file cascade deletion contract differed",
            )

        if number == 30:
            contents = [b"delete-batch-one", b"delete-batch-two"]
            upload = _upload(case_id, group, owner, "upload_batch_delete_files", [(f"{prefix}-{group}-{index + 1}.txt", content, "text/plain") for index, content in enumerate(contents)])
            ids = [str(item.get("id") or "") for item in (upload.get("data") or [])]
            before = [_file_row(group, file_id) for file_id in ids]
            deleted = _delete_files(case_id, group, owner, "delete_two_files", ids)
            objects_after = [_storage_snapshot(case_id, group, f"prove_batch_delete_object_absent_{index + 1}", row.get("parent_id", ""), row.get("location", "")) for index, row in enumerate(before)]
            counts = _saved_artifact_counts(group, ids, [])
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(deleted),
                "success_count": (deleted.get("data") or {}).get("success_count"),
                "file_rows_remaining": counts["files"],
                "file_links_remaining": counts["file_links"],
                "documents_remaining": 0,
                "tasks_remaining": 0,
                "objects_remaining": sum(item.get("exists") is True for item in objects_after),
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(upload) == [200, 0] and len(ids) == 2 and delete_contract_ok(observed, expected_success_count=2)
            return _result(
                passed,
                [{"name": "delete_two_files_and_verify_rows_objects", **observed, "raw_sha256": deleted.get("raw_sha256")}],
                {"response": [200, 0], "success_count": 2, "objects": "both absent"},
                "FM-DELETE-BATCH-001",
                f"{group} multi-file deletion contract differed",
            )

        if number == 31:
            folder = _create_folder(case_id, group, owner, "create_recursive_delete_folder", f"{prefix}-{group}-folder")
            folder_id = str((folder.get("data") or {}).get("id") or "")
            child = _create_folder(case_id, group, owner, "create_recursive_delete_child", f"{prefix}-{group}-child", parent_id=folder_id)
            child_id = str((child.get("data") or {}).get("id") or "")
            root_file = _upload(case_id, group, owner, "upload_recursive_root_file", [(f"{prefix}-{group}-root.txt", b"recursive-root", "text/plain")], parent_id=folder_id)
            child_file = _upload(case_id, group, owner, "upload_recursive_child_file", [(f"{prefix}-{group}-child.txt", b"recursive-child", "text/plain")], parent_id=child_id)
            file_ids = [str(((response.get("data") or [{}])[0]).get("id") or "") for response in (root_file, child_file)]
            saved_ids = [folder_id, child_id, *file_ids]
            file_rows = [_file_row(group, file_id) for file_id in file_ids]
            deleted = _delete_files(case_id, group, owner, "delete_folder_recursively", [folder_id])
            objects_after = [_storage_snapshot(case_id, group, f"prove_recursive_object_absent_{index + 1}", row.get("parent_id", ""), row.get("location", "")) for index, row in enumerate(file_rows)]
            counts = _saved_artifact_counts(group, saved_ids, [])
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(deleted),
                "success_count": (deleted.get("data") or {}).get("success_count"),
                "file_rows_remaining": counts["files"],
                "file_links_remaining": counts["file_links"],
                "documents_remaining": 0,
                "tasks_remaining": 0,
                "objects_remaining": sum(item.get("exists") is True for item in objects_after),
                "cleanup_succeeded": cleanup,
            }
            passed = (
                preclean and all(_response_pair(item) == [200, 0] for item in (folder, child, root_file, child_file)) and len(saved_ids) == 4 and delete_contract_ok(observed, expected_success_count=4)
            )
            return _result(
                passed,
                [
                    {"name": "create_nested_folder_tree", "saved_row_count": len(saved_ids), "saved_object_count": len(file_rows)},
                    {"name": "delete_parent_and_verify_recursive_cleanup", **observed, "raw_sha256": deleted.get("raw_sha256")},
                ],
                {"response": [200, 0], "success_count": 4, "tree": "all rows and exact objects absent"},
                "FM-DELETE-RECURSIVE-001",
                f"{group} recursive folder deletion contract differed",
            )

        if number == 32:
            missing = _missing_file_id(group)
            before = len(_file_rows(group, tenant_id=owner["tenant_id"]))
            deleted = _delete_files(case_id, group, owner, "reject_missing_delete", [missing])
            after = len(_file_rows(group, tenant_id=owner["tenant_id"]))
            data = deleted.get("data") if isinstance(deleted.get("data"), dict) else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            expected_error = f"File or Folder not found: {missing}"
            passed = (
                preclean
                and _response_pair(deleted) == [200, 102]
                and deleted.get("message") == "Deleted files failed with 1 errors"
                and data.get("success_count") == 0
                and expected_error in (data.get("errors") or [])
                and before == after
                and not _file_rows(group, ids=[missing])
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "delete_missing_id",
                        "response": _response_pair(deleted),
                        "message": deleted.get("message"),
                        "success_count": data.get("success_count"),
                        "error_matches": expected_error in (data.get("errors") or []),
                        "raw_sha256": deleted.get("raw_sha256"),
                    },
                    {"name": "verify_zero_metadata_delta", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "Deleted files failed with 1 errors"], "success_count": 0, "error": expected_error},
                "FM-DELETE-MISSING-001",
                f"{group} missing file deletion response or zero-delta contract differed",
            )

        if number == 33:
            email = "fm-033-user-b@fresh.invalid"
            password = "Fresh-FM-033-User-B@1234"
            secondary_preclean = True
            if DB._email_count(group, [email]):
                secondary_preclean = DB._delete_user_via_admin(group, email)
            registration = DB._register(case_id, group, "register_delete_attacker", {"email": email, "nickname": "FreshFM033UserB", "password": password})
            secondary_login = AUTH._login(case_id, group, "login_delete_attacker", email, password)
            secondary_auth = str(secondary_login.get("_auth") or "")
            secondary_tenant_id = DD._owner_id(group, email) if DB._email_count(group, [email]) else ""
            content = b"cross-tenant-delete-guard"
            upload = _upload(case_id, group, owner, "upload_owner_delete_guard_file", [(f"{prefix}-{group}.txt", content, "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            before = _file_row(group, file_id)
            object_before = _storage_snapshot(case_id, group, "read_owner_object_before_denied_delete", before.get("parent_id", ""), before.get("location", "")) if before else {}
            denied = _http(case_id, group, "secondary_delete_owner_file", secondary_auth, "DELETE", "/files", payload={"ids": [file_id]}, timeout=180)
            after = _file_row(group, file_id)
            object_after = _storage_snapshot(case_id, group, "read_owner_object_after_denied_delete", after.get("parent_id", ""), after.get("location", "")) if after else {}
            data = denied.get("data") if isinstance(denied.get("data"), dict) else {}
            expected_error = f"No authorization for file {file_id}"
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            if DB._email_count(group, [email]):
                user_cleanup_result = DB._disable_and_delete_user(case_id, group, email)
                user_cleanup = user_cleanup_result["disabled"]["code"] == 0 and user_cleanup_result["deleted"]["code"] == 0
            else:
                user_cleanup = False
            cleanup = file_cleanup and user_cleanup and DB._email_count(group, [email]) == 0
            passed = (
                preclean
                and secondary_preclean
                and registration.get("code") == 0
                and secondary_login.get("code") == 0
                and bool(secondary_auth)
                and owner["tenant_id"] != secondary_tenant_id
                and _response_pair(upload) == [200, 0]
                and _response_pair(denied) == [200, 102]
                and data.get("success_count") == 0
                and expected_error in (data.get("errors") or [])
                and before == after
                and object_before.get("exists") is True
                and object_after.get("exists") is True
                and object_before.get("sha256") == object_after.get("sha256") == hashlib.sha256(content).hexdigest()
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_independent_tenant_and_owner_file",
                        "registration_code": registration.get("code"),
                        "login_code": secondary_login.get("code"),
                        "tenant_ids_distinct": owner["tenant_id"] != secondary_tenant_id,
                    },
                    {
                        "name": "deny_cross_tenant_delete_and_preserve_object",
                        "response": _response_pair(denied),
                        "success_count": data.get("success_count"),
                        "error_matches": expected_error in (data.get("errors") or []),
                        "row_unchanged": before == after,
                        "object_hash_unchanged": object_before.get("sha256") == object_after.get("sha256"),
                        "raw_sha256": denied.get("raw_sha256"),
                    },
                    {"name": "cleanup_file_and_secondary_user_through_apis", "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102], "success_count": 0, "error": expected_error, "mutation": "none"},
                "FM-DELETE-CROSS-TENANT-001",
                f"{group} cross-tenant file deletion authorization contract differed",
            )

        if number == 34:
            dataset_prefix = f"{prefix}-{group}-dataset"
            dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], dataset_prefix)
            dataset = DD._create_dataset(case_id, group, owner["auth"], "create_protected_dataset", {"name": dataset_prefix})
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            content = b"protected-knowledgebase-source"
            upload = DD._upload_local_documents(case_id, group, owner["auth"], "upload_protected_dataset_document", dataset_id, [(f"{prefix}-{group}.txt", content, "text/plain")])
            document_items = DD._uploaded_items([upload])
            document_ids = [str(item.get("id") or "") for item in document_items if item.get("id")]
            relations = _file_document_rows(group, dataset_id=dataset_id)
            file_id = relations[0]["file_id"] if len(relations) == 1 else ""
            before = _file_row(group, file_id)
            document_location = relations[0]["location"] if relations else ""
            object_before = _storage_snapshot(case_id, group, "read_protected_dataset_object_before", dataset_id, document_location) if dataset_id and document_location else {}
            deleted = _delete_files(case_id, group, owner, "delete_protected_source_file", [file_id])
            after = _file_row(group, file_id)
            artifacts_after_request = _saved_artifact_counts(group, [file_id], document_ids)
            object_after_request = _storage_snapshot(case_id, group, "read_protected_dataset_object_after_skip", dataset_id, document_location) if dataset_id and document_location else {}
            data = deleted.get("data") if isinstance(deleted.get("data"), dict) else {}
            document_cleanup_response = (
                DD._delete_documents(
                    case_id,
                    group,
                    owner["auth"],
                    "cleanup_protected_document",
                    dataset_id,
                    document_ids,
                )
                if dataset_id and document_ids
                else {"http_status": None, "code": None}
            )
            artifacts_after_cleanup = _saved_artifact_counts(group, [file_id], document_ids)
            object_after_cleanup = _storage_snapshot(case_id, group, "prove_protected_dataset_object_cleanup", dataset_id, document_location) if dataset_id and document_location else {}
            dataset_cleanup_response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_protected_dataset", [dataset_id]) if dataset_id else {"http_status": None, "code": None}
            cleanup_observed = {
                "document_delete_response": _response_pair(document_cleanup_response),
                "dataset_delete_response": _response_pair(dataset_cleanup_response),
                "artifact_counts": artifacts_after_cleanup,
                "object_absent": object_after_cleanup.get("exists") is False,
                "dataset_absent": not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix),
            }
            cleanup = knowledgebase_cleanup_contract_ok(cleanup_observed)
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and len(document_ids) == 1
                and len(relations) == 1
                and before.get("source_type") == "knowledgebase"
                and object_before.get("exists") is True
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and _response_pair(deleted) == [200, 0]
                and data.get("success_count") == 0
                and after == before
                and artifacts_after_request == {"files": 1, "file_links": 1, "documents": 1, "tasks": 0}
                and object_after_request.get("exists") is True
                and object_after_request.get("sha256") == object_before.get("sha256")
                and cleanup
            )
            return _result(
                passed,
                [
                    {"name": "create_real_knowledgebase_source_file", "source_type": before.get("source_type"), "document_count": len(document_ids), "object_exists": object_before.get("exists")},
                    {
                        "name": "delete_request_skips_protected_source",
                        "response": _response_pair(deleted),
                        "success_count": data.get("success_count"),
                        "row_unchanged": after == before,
                        "relations_preserved": artifacts_after_request,
                        "object_hash_unchanged": object_after_request.get("sha256") == object_before.get("sha256"),
                        "raw_sha256": deleted.get("raw_sha256"),
                    },
                    {"name": "cleanup_document_then_dataset_through_apis", **cleanup_observed, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 0], "success_count": 0, "protected_graph": "unchanged until document and dataset API cleanup"},
                "FM-DELETE-KB-PROTECTION-001",
                f"{group} knowledgebase source protection contract differed",
            )

        if number == 35:
            before = len(_file_rows(group, tenant_id=owner["tenant_id"]))
            deleted = _http(case_id, group, "reject_empty_delete_ids", owner["auth"], "DELETE", "/files", payload={"ids": []})
            after = len(_file_rows(group, tenant_id=owner["tenant_id"]))
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = preclean and _response_pair(deleted) == [200, 101] and before == after and cleanup
            return _result(
                passed,
                [
                    {"name": "submit_empty_delete_ids", "response": _response_pair(deleted), "message": deleted.get("message"), "raw_sha256": deleted.get("raw_sha256")},
                    {"name": "verify_zero_metadata_delta", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 101], "schema": "ids min_length=1", "mutation": "none"},
                "FM-DELETE-EMPTY-IDS-001",
                f"{group} empty delete ID schema contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_fm_036_to_039(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        _root(case_id, group, owner)

        if number in {36, 37, 38}:
            dataset_prefix = f"{prefix}-{group}-dataset"
            dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], dataset_prefix)
            dataset = DD._create_dataset(case_id, group, owner["auth"], "create_link_dataset", {"name": dataset_prefix})
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
        else:
            dataset_prefix = ""
            dataset_preclean = True
            dataset = {"http_status": 200, "code": 0}
            dataset_id = ""

        if number == 36:
            content = b"link-single-file-bytes"
            upload = _upload(case_id, group, owner, "upload_single_link_file", [(f"{prefix}-{group}.txt", content, "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            file_row = _file_row(group, file_id)
            link = _http(case_id, group, "link_single_file_to_dataset", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [file_id], "kb_ids": [dataset_id]})
            relations = _wait_file_documents(group, [file_id], dataset_id, expected_count=1)
            relation = relations[0] if len(relations) == 1 else {}
            metadata_match = (
                relation.get("name") == file_row.get("name")
                and relation.get("type") == file_row.get("type")
                and relation.get("location") == file_row.get("location")
                and relation.get("size") == file_row.get("size")
                and bool(relation.get("parser_id"))
            )
            configuration_match = relation.get("parser_config") == relation.get("dataset_parser_config") and relation.get("pipeline_id") == relation.get("dataset_pipeline_id")
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = _storage_snapshot(case_id, group, "prove_link_file_object_cleanup", file_row.get("parent_id", ""), file_row.get("location", "")) if file_row else {}
            dataset_cleanup_response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_link_dataset", [dataset_id]) if dataset_id else {"code": None}
            cleanup = file_cleanup and object_after.get("exists") is False and dataset_cleanup_response.get("code") == 0 and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            observed = {
                "response": _response_pair(link),
                "data_true": link.get("data") is True,
                "expected_file_ids": [file_id],
                "linked_file_ids": sorted(row["file_id"] for row in relations),
                "document_count": len(relations),
                "all_dataset_ids_match": all(row["dataset_id"] == dataset_id for row in relations),
                "all_document_metadata_match": metadata_match,
                "all_dataset_configuration_match": configuration_match,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and dataset_preclean and _response_pair(dataset) == [200, 0] and _response_pair(upload) == [200, 0] and link_contract_ok(observed)
            return _result(
                passed,
                [
                    {"name": "schedule_single_file_link", "response": observed["response"], "data_true": observed["data_true"], "raw_sha256": link.get("raw_sha256")},
                    {
                        "name": "poll_and_verify_document_relation_configuration",
                        "linked_file_ids": observed["linked_file_ids"],
                        "document_count": len(relations),
                        "dataset_ids_match": observed["all_dataset_ids_match"],
                        "metadata_match": metadata_match,
                        "configuration_match": configuration_match,
                        "cleanup_succeeded": cleanup,
                    },
                ],
                {"response": [200, 0], "data": True, "relation": "one file/document in target dataset with inherited configuration"},
                "FM-LINK-SINGLE-001",
                f"{group} single file-to-dataset link contract differed",
            )

        if number == 37:
            folder = _create_folder(case_id, group, owner, "create_recursive_link_folder", f"{prefix}-{group}-folder")
            folder_id = str((folder.get("data") or {}).get("id") or "")
            nested = _create_folder(case_id, group, owner, "create_recursive_link_nested", f"{prefix}-{group}-nested", parent_id=folder_id)
            nested_id = str((nested.get("data") or {}).get("id") or "")
            empty = _create_folder(case_id, group, owner, "create_empty_link_folder", f"{prefix}-{group}-empty")
            empty_id = str((empty.get("data") or {}).get("id") or "")
            virtual = _create_folder(case_id, group, owner, "create_virtual_link_boundary", f"{prefix}-{group}-virtual", parent_id=folder_id, file_type=None)
            virtual_id = str((virtual.get("data") or {}).get("id") or "")
            contents = [b"recursive-link-top", b"recursive-link-nested"]
            first = _upload(case_id, group, owner, "upload_recursive_link_top", [(f"{prefix}-{group}-top.txt", contents[0], "text/plain")], parent_id=folder_id)
            second = _upload(case_id, group, owner, "upload_recursive_link_nested", [(f"{prefix}-{group}-nested.txt", contents[1], "text/plain")], parent_id=nested_id)
            real_ids = [str(((response.get("data") or [{}])[0]).get("id") or "") for response in (first, second)]
            real_rows = [_file_row(group, file_id) for file_id in real_ids]
            empty_link = _http(case_id, group, "link_empty_folder", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [empty_id], "kb_ids": [dataset_id]})
            time.sleep(0.5)
            empty_relations = _file_document_rows(group, file_ids=[empty_id], dataset_id=dataset_id)
            link = _http(case_id, group, "link_recursive_folder", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [folder_id], "kb_ids": [dataset_id]})
            deadline = time.monotonic() + 20
            relations: list[dict[str, Any]] = []
            while time.monotonic() < deadline:
                relations = _file_document_rows(group, file_ids=[*real_ids, virtual_id], dataset_id=dataset_id)
                if set(real_ids).issubset({row["file_id"] for row in relations}):
                    time.sleep(0.5)
                    relations = _file_document_rows(group, file_ids=[*real_ids, virtual_id], dataset_id=dataset_id)
                    break
                time.sleep(0.1)
            expected_ids = sorted(real_ids)
            linked_ids = sorted(row["file_id"] for row in relations)
            file_rows_by_id = {row["id"]: row for row in [*real_rows, _file_row(group, virtual_id)] if row}
            metadata_match = all(
                row.get("name") == file_rows_by_id.get(row["file_id"], {}).get("name")
                and row.get("type") == file_rows_by_id.get(row["file_id"], {}).get("type")
                and row.get("location") == file_rows_by_id.get(row["file_id"], {}).get("location")
                and row.get("size") == file_rows_by_id.get(row["file_id"], {}).get("size")
                for row in relations
            )
            configuration_match = all(row.get("parser_config") == row.get("dataset_parser_config") and row.get("pipeline_id") == row.get("dataset_pipeline_id") for row in relations)
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            objects_after = [
                _storage_snapshot(case_id, group, f"prove_recursive_link_object_cleanup_{index + 1}", row.get("parent_id", ""), row.get("location", "")) for index, row in enumerate(real_rows)
            ]
            dataset_cleanup_response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_recursive_link_dataset", [dataset_id]) if dataset_id else {"code": None}
            cleanup = (
                file_cleanup
                and all(item.get("exists") is False for item in objects_after)
                and dataset_cleanup_response.get("code") == 0
                and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            )
            observed = {
                "response": _response_pair(link),
                "data_true": link.get("data") is True,
                "expected_file_ids": expected_ids,
                "linked_file_ids": linked_ids,
                "document_count": len(relations),
                "all_dataset_ids_match": all(row["dataset_id"] == dataset_id for row in relations),
                "all_document_metadata_match": metadata_match,
                "all_dataset_configuration_match": configuration_match,
                "cleanup_succeeded": cleanup,
            }
            empty_ok = _response_pair(empty_link) == [200, 0] and empty_link.get("data") is True and not empty_relations
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and all(_response_pair(item) == [200, 0] for item in (folder, nested, empty, virtual, first, second))
                and empty_ok
                and link_contract_ok(observed)
            )
            return _result(
                passed,
                [
                    {"name": "link_empty_folder_as_successful_noop", "response": _response_pair(empty_link), "relation_count": len(empty_relations), "raw_sha256": empty_link.get("raw_sha256")},
                    {"name": "schedule_recursive_folder_link", "response": _response_pair(link), "real_file_ids": expected_ids, "virtual_file_id": virtual_id, "raw_sha256": link.get("raw_sha256")},
                    {
                        "name": "poll_exact_real_file_relation_set",
                        "linked_file_ids": linked_ids,
                        "virtual_linked": virtual_id in linked_ids,
                        "document_count": len(relations),
                        "metadata_match": metadata_match,
                        "configuration_match": configuration_match,
                        "cleanup_succeeded": cleanup,
                    },
                ],
                {"empty_folder": "scheduled true with zero relations", "recursive": "all real nested files only; virtual excluded"},
                "FM-LINK-RECURSIVE-TYPE-FILTER-001",
                f"{group} recursive folder link or virtual type-filter contract differed",
            )

        if number == 38:
            missing = _missing_file_id(group)
            before_documents = DD._document_count_for_datasets(group, [dataset_id])
            response = _http(case_id, group, "reject_missing_link_file", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [missing], "kb_ids": [dataset_id]})
            after_documents = DD._document_count_for_datasets(group, [dataset_id])
            dataset_cleanup_response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_missing_file_link_dataset", [dataset_id]) if dataset_id else {"code": None}
            cleanup = dataset_cleanup_response.get("code") == 0 and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and _response_pair(response) == [200, 102]
                and response.get("message") == "File not found!"
                and before_documents == after_documents == 0
                and not _file_rows(group, ids=[missing])
                and cleanup
            )
            return _result(
                passed,
                [
                    {"name": "link_missing_file_id", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {"name": "verify_zero_document_delta_and_cleanup", "before": before_documents, "after": after_documents, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "File not found!"], "document_delta": 0},
                "FM-LINK-MISSING-FILE-001",
                f"{group} missing file link response or zero-delta contract differed",
            )

        if number == 39:
            content = b"missing-dataset-link-guard"
            upload = _upload(case_id, group, owner, "upload_missing_dataset_link_file", [(f"{prefix}-{group}.txt", content, "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            before = _file_row(group, file_id)
            object_before = _storage_snapshot(case_id, group, "read_missing_dataset_object_before", before.get("parent_id", ""), before.get("location", "")) if before else {}
            missing_dataset = uuid.uuid4().hex
            response = _http(case_id, group, "reject_missing_link_dataset", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [file_id], "kb_ids": [missing_dataset]})
            after = _file_row(group, file_id)
            relations = _file_document_rows(group, file_ids=[file_id])
            object_after = _storage_snapshot(case_id, group, "read_missing_dataset_object_after", after.get("parent_id", ""), after.get("location", "")) if after else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = (
                preclean
                and _response_pair(upload) == [200, 0]
                and _response_pair(response) == [200, 102]
                and response.get("message") == "Can't find this dataset!"
                and before == after
                and not relations
                and object_before.get("exists") is True
                and object_after.get("exists") is True
                and object_before.get("sha256") == object_after.get("sha256") == hashlib.sha256(content).hexdigest()
                and cleanup
            )
            return _result(
                passed,
                [
                    {"name": "link_to_missing_dataset", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {
                        "name": "verify_file_relation_and_object_unchanged",
                        "row_unchanged": before == after,
                        "relation_count": len(relations),
                        "object_hash_unchanged": object_before.get("sha256") == object_after.get("sha256"),
                        "cleanup_succeeded": cleanup,
                    },
                ],
                {"response": [200, 102, "Can't find this dataset!"], "mutation": "none"},
                "FM-LINK-MISSING-DATASET-001",
                f"{group} missing dataset link response or no-mutation contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_fm_040_to_044(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        root = _root(case_id, group, owner)
        root_id = root["row"]["id"]

        if number == 40:
            folder = _create_folder(case_id, group, owner, "create_parent_query_folder", f"{prefix}-{group}-folder")
            folder_id = str((folder.get("data") or {}).get("id") or "")
            upload = _upload(case_id, group, owner, "upload_parent_query_file", [(f"{prefix}-{group}.txt", b"parent-query", "text/plain")], parent_id=folder_id)
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            file_row = _file_row(group, file_id)
            folder_row = _file_row(group, folder_id)
            response = _http(case_id, group, "get_file_parent", owner["auth"], "GET", f"/files/{file_id}/parent")
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            parent = data.get("parent_folder") if isinstance(data.get("parent_folder"), dict) else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(response),
                "expected_ids": [folder_id],
                "returned_ids": [str(parent.get("id") or "")],
                "all_required_fields_present": all(key in parent for key in ("id", "name", "type")) and parent.get("type") == "folder",
                "database_path_matches": file_row.get("parent_id") == folder_id and parent.get("id") == folder_row.get("id") and parent.get("name") == folder_row.get("name"),
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(folder) == [200, 0] and _response_pair(upload) == [200, 0] and hierarchy_contract_ok(observed)
            return _result(
                passed,
                [{"name": "get_direct_parent_folder", **observed, "raw_sha256": response.get("raw_sha256")}],
                {"response": [200, 0], "parent": "exact database parent with id/name/type"},
                "FM-HIERARCHY-PARENT-001",
                f"{group} direct parent folder query contract differed",
            )

        if number == 41:
            response = _http(case_id, group, "get_root_parent", owner["auth"], "GET", f"/files/{root_id}/parent")
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            parent = data.get("parent_folder") if isinstance(data.get("parent_folder"), dict) else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(response),
                "expected_ids": [root_id],
                "returned_ids": [str(parent.get("id") or "")],
                "all_required_fields_present": all(key in parent for key in ("id", "name", "type")) and parent.get("type") == "folder",
                "database_path_matches": root["row"].get("parent_id") == root_id and parent.get("id") == root_id and parent.get("name") == root["row"].get("name"),
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and hierarchy_contract_ok(observed)
            return _result(
                passed,
                [{"name": "get_root_parent_as_self", **observed, "raw_sha256": response.get("raw_sha256")}],
                {"response": [200, 0], "parent": "root itself"},
                "FM-HIERARCHY-ROOT-PARENT-001",
                f"{group} root self-parent contract differed",
            )

        if number == 42:
            folder_ids: list[str] = []
            parent_id = root_id
            for label in ("a", "b", "c"):
                response = _create_folder(case_id, group, owner, f"create_ancestor_{label}", f"{prefix}-{group}-{label}", parent_id=parent_id)
                parent_id = str((response.get("data") or {}).get("id") or "")
                folder_ids.append(parent_id)
            upload = _upload(case_id, group, owner, "upload_deep_ancestor_file", [(f"{prefix}-{group}.txt", b"ancestor-path", "text/plain")], parent_id=folder_ids[-1])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            response = _http(case_id, group, "get_all_ancestors", owner["auth"], "GET", f"/files/{file_id}/ancestors")
            data = response.get("data") if isinstance(response.get("data"), dict) else {}
            parents = data.get("parent_folders") if isinstance(data.get("parent_folders"), list) else []
            expected_ids = [file_id, *reversed(folder_ids), root_id]
            returned_ids = [str(item.get("id") or "") for item in parents if isinstance(item, dict)]
            rows = {row["id"]: row for row in _file_rows(group, ids=expected_ids)}
            database_path_matches = (
                len(rows) == len(expected_ids)
                and all(rows[expected_ids[index]].get("parent_id") == expected_ids[index + 1] for index in range(len(expected_ids) - 1))
                and rows[root_id].get("parent_id") == root_id
            )
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response": _response_pair(response),
                "expected_ids": expected_ids,
                "returned_ids": returned_ids,
                "all_required_fields_present": len(parents) == len(expected_ids) and all(isinstance(item, dict) and all(key in item for key in ("id", "name", "type")) for item in parents),
                "database_path_matches": database_path_matches,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(upload) == [200, 0] and len(folder_ids) == 3 and hierarchy_contract_ok(observed)
            return _result(
                passed,
                [{"name": "create_root_a_b_c_file_path", "folder_count": len(folder_ids)}, {"name": "get_ancestors_in_current_service_order", **observed, "raw_sha256": response.get("raw_sha256")}],
                {"response": [200, 0], "order": "file,C,B,A,root"},
                "FM-HIERARCHY-ANCESTORS-001",
                f"{group} ancestor path order or database contract differed",
            )

        if number == 43:
            missing = _missing_file_id(group)
            before = len(_file_rows(group, tenant_id=owner["tenant_id"]))
            response = _http(case_id, group, "get_missing_ancestors", owner["auth"], "GET", f"/files/{missing}/ancestors")
            after = len(_file_rows(group, tenant_id=owner["tenant_id"]))
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = preclean and _response_pair(response) == [200, 102] and response.get("message") == "Folder not found!" and before == after and not _file_rows(group, ids=[missing]) and cleanup
            return _result(
                passed,
                [
                    {"name": "get_missing_file_ancestors", "response": _response_pair(response), "message": response.get("message"), "raw_sha256": response.get("raw_sha256")},
                    {"name": "verify_zero_metadata_delta", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "Folder not found!"], "mutation": "none"},
                "FM-HIERARCHY-MISSING-001",
                f"{group} missing ancestor response or zero-delta contract differed",
            )

        if number == 44:
            email = "fm-044-user-b@fresh.invalid"
            password = "Fresh-FM-044-User-B@1234"
            secondary_preclean = True
            if DB._email_count(group, [email]):
                secondary_preclean = DB._delete_user_via_admin(group, email)
            registration = DB._register(case_id, group, "register_parent_query_attacker", {"email": email, "nickname": "FreshFM044UserB", "password": password})
            secondary_login = AUTH._login(case_id, group, "login_parent_query_attacker", email, password)
            secondary_auth = str(secondary_login.get("_auth") or "")
            secondary_tenant_id = DD._owner_id(group, email) if DB._email_count(group, [email]) else ""
            content = b"cross-tenant-parent-guard"
            upload = _upload(case_id, group, owner, "upload_owner_parent_guard_file", [(f"{prefix}-{group}.txt", content, "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            before = _file_row(group, file_id)
            object_before = _storage_snapshot(case_id, group, "read_parent_guard_object_before", before.get("parent_id", ""), before.get("location", "")) if before else {}
            denied = _http(case_id, group, "secondary_get_owner_file_parent", secondary_auth, "GET", f"/files/{file_id}/parent")
            after = _file_row(group, file_id)
            object_after = _storage_snapshot(case_id, group, "read_parent_guard_object_after", after.get("parent_id", ""), after.get("location", "")) if after else {}
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            if DB._email_count(group, [email]):
                user_cleanup_result = DB._disable_and_delete_user(case_id, group, email)
                user_cleanup = user_cleanup_result["disabled"]["code"] == 0 and user_cleanup_result["deleted"]["code"] == 0
            else:
                user_cleanup = False
            cleanup = file_cleanup and user_cleanup and DB._email_count(group, [email]) == 0
            passed = (
                preclean
                and secondary_preclean
                and registration.get("code") == 0
                and secondary_login.get("code") == 0
                and bool(secondary_auth)
                and owner["tenant_id"] != secondary_tenant_id
                and _response_pair(upload) == [200, 0]
                and _response_pair(denied) == [200, 102]
                and denied.get("message") == "No authorization."
                and before == after
                and object_before.get("exists") is True
                and object_after.get("exists") is True
                and object_before.get("sha256") == object_after.get("sha256") == hashlib.sha256(content).hexdigest()
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_independent_tenant_and_owner_file",
                        "registration_code": registration.get("code"),
                        "login_code": secondary_login.get("code"),
                        "tenant_ids_distinct": owner["tenant_id"] != secondary_tenant_id,
                    },
                    {
                        "name": "deny_cross_tenant_parent_query",
                        "response": _response_pair(denied),
                        "message": denied.get("message"),
                        "row_unchanged": before == after,
                        "object_hash_unchanged": object_before.get("sha256") == object_after.get("sha256"),
                        "raw_sha256": denied.get("raw_sha256"),
                    },
                    {"name": "cleanup_file_and_secondary_user_through_apis", "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "No authorization."], "mutation": "none"},
                "FM-HIERARCHY-CROSS-TENANT-001",
                f"{group} cross-tenant parent query authorization contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_fm_045_to_057(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        secondary = _prepare_secondary_user(case_id, group, owner, number)
        secondary_ready = secondary["preclean"] and secondary["registration_code"] == 0 and secondary["login_code"] == 0 and bool(secondary["auth"]) and owner["tenant_id"] != secondary["tenant_id"]
        _root(case_id, group, owner)

        if number == 57:
            dataset_prefix = f"{prefix}-{group}-dataset"
            dataset_preclean = DD._cleanup_prefix(
                case_id,
                group,
                owner["auth"],
                owner["tenant_id"],
                dataset_prefix,
            )
            dataset = DD._create_dataset(
                case_id,
                group,
                owner["auth"],
                "create_commit_dataset",
                {"name": dataset_prefix},
            )
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            source_content = b"dataset-commit-source"
            document_upload = DD._upload_local_documents(
                case_id,
                group,
                owner["auth"],
                "upload_commit_dataset_document",
                dataset_id,
                [(f"{prefix}-{group}.txt", source_content, "text/plain")],
            )
            document_items = DD._uploaded_items([document_upload])
            document_ids = [str(item.get("id") or "") for item in document_items if item.get("id")]
            relations = _file_document_rows(group, dataset_id=dataset_id)
            file_id = relations[0]["file_id"] if len(relations) == 1 else ""
            file_before = _file_row(group, file_id)
            folder_id = str(file_before.get("parent_id") or "")
            folder_row = _file_row(group, folder_id)
            document_location = relations[0]["location"] if relations else ""
            source_object_before = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_dataset_source_object_before_commit",
                    dataset_id,
                    document_location,
                )
                if dataset_id and document_location
                else {}
            )
            commit_content = f"{prefix}-{group}-dataset-commit-content"
            commit_hash = hashlib.sha256(commit_content.encode()).hexdigest()
            commit_location = f".objects/{commit_hash}"
            created = _create_commit(
                case_id,
                group,
                "create_dataset_folder_commit",
                owner["auth"],
                folder_id,
                f"{prefix} dataset commit",
                [
                    {
                        "file_id": file_id,
                        "file_name": file_before.get("name", ""),
                        "operation": "add",
                        "content": commit_content,
                    }
                ],
            )
            created_data = created.get("data") if isinstance(created.get("data"), dict) else {}
            commit_id = str(created_data.get("id") or "")
            commit_rows = _commit_rows(group, commit_ids=[commit_id]) if commit_id else []
            commit_items = _commit_item_rows(group, commit_ids=[commit_id]) if commit_id else []
            tree_entry = commit_rows[0]["tree_state"].get(file_id, {}) if len(commit_rows) == 1 else {}
            commit_object = _storage_snapshot(case_id, group, "read_dataset_commit_object", folder_id, commit_location) if folder_id else {}
            add_observed = {
                "response": _response_pair(created),
                "response_id_present": bool(commit_id),
                "commit_row_count": len(commit_rows),
                "item_row_count": len(commit_items),
                "parent_is_null": len(commit_rows) == 1 and commit_rows[0]["parent_id"] is None,
                "operation": commit_items[0]["operation"] if len(commit_items) == 1 else None,
                "tree_entry_matches": tree_entry.get("hash") == commit_hash
                and tree_entry.get("location") == commit_location
                and tree_entry.get("name") == file_before.get("name")
                and tree_entry.get("parent_id") == folder_id,
                "object_exists": commit_object.get("exists") is True,
                "object_sha_matches": commit_object.get("sha256") == commit_hash,
            }
            direct = _commit_request(case_id, group, "list_direct_dataset_folder_commits", owner["auth"], "GET", f"/folders/{folder_id}/commits", params={"page": 1, "page_size": 10})
            resolved = _commit_request(case_id, group, "list_dataset_resolved_commits", owner["auth"], "GET", f"/datasets/{dataset_id}/commits", params={"page": 1, "page_size": 10})
            direct_data = direct.get("data") if isinstance(direct.get("data"), dict) else {}
            resolved_data = resolved.get("data") if isinstance(resolved.get("data"), dict) else {}
            direct_ids = [str(item.get("id") or "") for item in direct_data.get("commits", []) if isinstance(item, dict)]
            resolved_ids = [str(item.get("id") or "") for item in resolved_data.get("commits", []) if isinstance(item, dict)]
            resolver_ok = (
                _response_pair(direct) == [200, 0]
                and _response_pair(resolved) == [200, 0]
                and direct_data.get("total") == resolved_data.get("total") == 1
                and direct_ids == resolved_ids == [commit_id]
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_dataset_idor_object_before", folder_id, commit_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_list_dataset_commits", secondary["auth"], "GET", f"/datasets/{dataset_id}/commits", params={"page": 1, "page_size": 10})
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_dataset_idor_object_after", folder_id, commit_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            document_cleanup = (
                DD._delete_documents(case_id, group, owner["auth"], "cleanup_commit_dataset_document", dataset_id, document_ids) if dataset_id and document_ids else {"http_status": None, "code": None}
            )
            dataset_cleanup = DD._delete_ids(case_id, group, owner["auth"], "cleanup_commit_dataset", [dataset_id]) if dataset_id else {"http_status": None, "code": None}
            artifact_counts = _saved_artifact_counts(group, [file_id], document_ids)
            source_object_after = _storage_snapshot(case_id, group, "prove_dataset_source_object_cleanup", dataset_id, document_location) if dataset_id and document_location else {}
            commit_object_after = _storage_snapshot(case_id, group, "observe_retained_dataset_commit_object", folder_id, commit_location) if folder_id else {}
            user_cleanup = _cleanup_secondary_user(case_id, group, secondary["email"])
            active_cleanup = (
                _response_pair(document_cleanup) == [200, 0]
                and _response_pair(dataset_cleanup) == [200, 0]
                and all(value == 0 for value in artifact_counts.values())
                and not _file_rows(group, ids=[folder_id])
                and source_object_after.get("exists") is False
                and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
                and user_cleanup
            )
            passed = (
                preclean
                and secondary_ready
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and _response_pair(document_upload) == [200, 0]
                and len(document_ids) == len(relations) == 1
                and folder_row.get("source_type") == "knowledgebase"
                and folder_row.get("name") == dataset_prefix
                and source_object_before.get("exists") is True
                and commit_add_contract_ok(add_observed)
                and resolver_ok
                and commit_access_denied_contract_ok(idor)
                and active_cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "materialize_dataset_folder_and_create_commit",
                        "dataset_response": _response_pair(dataset),
                        "document_response": _response_pair(document_upload),
                        "folder_source_type": folder_row.get("source_type"),
                        **add_observed,
                        "raw_sha256": [dataset.get("raw_sha256"), document_upload.get("raw_sha256"), created.get("raw_sha256")],
                    },
                    {
                        "name": "compare_direct_and_dataset_resolved_commit_lists",
                        "direct_response": _response_pair(direct),
                        "dataset_response": _response_pair(resolved),
                        "direct_ids": direct_ids,
                        "resolved_ids": resolved_ids,
                        "totals": [direct_data.get("total"), resolved_data.get("total")],
                        "raw_sha256": [direct.get("raw_sha256"), resolved.get("raw_sha256")],
                    },
                    {"name": "reject_independent_tenant_dataset_commit_list", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {
                        "name": "cleanup_dataset_document_and_secondary_user_through_apis",
                        "active_cleanup_succeeded": active_cleanup,
                        "artifact_counts": artifact_counts,
                        "source_object_absent": source_object_after.get("exists") is False,
                        "retained_commit_rows_without_delete_api": len(_commit_rows(group, folder_id=folder_id)),
                        "retained_commit_object_without_delete_api": commit_object_after.get("exists") is True,
                    },
                ],
                {
                    "resolver": "dataset route equals direct folder route",
                    "attacker": "authorization rejection without commit metadata",
                    "cleanup": "active dataset graph removed; commit metadata/object observation retained because no delete API",
                },
                "FM-DATASET-COMMIT-RESOLVER-IDOR-001",
                f"{group} dataset commit resolver or authorization contract differed",
            )

        folder = _create_folder(
            case_id,
            group,
            owner,
            "create_commit_workspace",
            f"{prefix}-{group}-workspace",
        )
        folder_id = str((folder.get("data") or {}).get("id") or "")
        upload_parent = folder_id
        nested_response: dict[str, Any] | None = None
        nested_id = ""
        if number == 54:
            nested_response = _create_folder(
                case_id,
                group,
                owner,
                "create_commit_nested_folder",
                f"{prefix}-{group}-old-subfolder",
                parent_id=folder_id,
            )
            nested_id = str((nested_response.get("data") or {}).get("id") or "")
            upload_parent = nested_id
        original_name = f"{prefix}-{group}-old.txt"
        upload = _upload(
            case_id,
            group,
            owner,
            "upload_commit_file",
            [(original_name, b"pre-commit-upload-bytes", "text/plain")],
            parent_id=upload_parent,
        )
        file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
        original_file = _file_row(group, file_id)
        object_addresses: list[tuple[str, str]] = []
        if original_file:
            object_addresses.append((original_file["parent_id"], str(original_file.get("location") or "")))
        initial_content = f"{prefix}-{group}-initial-content"
        initial_bytes = initial_content.encode()
        initial_hash = hashlib.sha256(initial_bytes).hexdigest()
        initial_location = f".objects/{initial_hash}"
        initial_commit = _create_commit(
            case_id,
            group,
            "create_initial_add_commit",
            owner["auth"],
            folder_id,
            f"{prefix} initial",
            [
                {
                    "file_id": file_id,
                    "file_name": original_name,
                    "operation": "add",
                    "content": initial_content,
                }
            ],
        )
        initial_data = initial_commit.get("data") if isinstance(initial_commit.get("data"), dict) else {}
        initial_commit_id = str(initial_data.get("id") or "")
        initial_rows = _commit_rows(group, commit_ids=[initial_commit_id]) if initial_commit_id else []
        initial_items = _commit_item_rows(group, commit_ids=[initial_commit_id]) if initial_commit_id else []
        initial_tree_entry = initial_rows[0]["tree_state"].get(file_id, {}) if len(initial_rows) == 1 else {}
        initial_object = _storage_snapshot(
            case_id,
            group,
            "read_initial_commit_object",
            folder_id,
            initial_location,
        )
        object_addresses.append((folder_id, initial_location))
        initial_observed = {
            "response": _response_pair(initial_commit),
            "response_id_present": bool(initial_commit_id),
            "commit_row_count": len(initial_rows),
            "item_row_count": len(initial_items),
            "parent_is_null": len(initial_rows) == 1 and initial_rows[0]["parent_id"] is None,
            "operation": initial_items[0]["operation"] if len(initial_items) == 1 else None,
            "tree_entry_matches": (
                initial_tree_entry.get("hash") == initial_hash
                and initial_tree_entry.get("location") == initial_location
                and initial_tree_entry.get("name") == original_name
                and initial_tree_entry.get("parent_id") == upload_parent
                and initial_tree_entry.get("status") == "1"
            ),
            "object_exists": initial_object.get("exists") is True,
            "object_sha_matches": initial_object.get("sha256") == initial_hash,
        }
        base_ready = (
            preclean
            and secondary_ready
            and _response_pair(folder) == [200, 0]
            and (nested_response is None or _response_pair(nested_response) == [200, 0])
            and _response_pair(upload) == [200, 0]
            and commit_add_contract_ok(initial_observed)
        )

        if number == 45:
            outside_folder = _create_folder(
                case_id,
                group,
                owner,
                "create_cross_folder_workspace",
                f"{prefix}-{group}-outside",
            )
            outside_folder_id = str((outside_folder.get("data") or {}).get("id") or "")
            outside_upload = _upload(
                case_id,
                group,
                owner,
                "upload_cross_folder_file",
                [(f"{prefix}-{group}-outside.txt", b"outside-upload", "text/plain")],
                parent_id=outside_folder_id,
            )
            outside_id = str(((outside_upload.get("data") or [{}])[0]).get("id") or "")
            outside_before = _file_row(group, outside_id)
            outside_object_before = (
                _storage_snapshot(case_id, group, "read_cross_folder_object_before", outside_before.get("parent_id", ""), outside_before.get("location", "")) if outside_before else {}
            )
            if outside_before:
                object_addresses.append((outside_before["parent_id"], str(outside_before.get("location") or "")))
            before_cross_count = len(_commit_rows(group, folder_id=folder_id))
            cross_content = f"{prefix}-{group}-cross-folder-content"
            cross_hash = hashlib.sha256(cross_content.encode()).hexdigest()
            cross_response = _create_commit(
                case_id,
                group,
                "reject_cross_folder_file_commit",
                owner["auth"],
                folder_id,
                f"{prefix} cross folder reject",
                [{"file_id": outside_id, "file_name": outside_before.get("name", ""), "operation": "add", "content": cross_content}],
            )
            outside_after = _file_row(group, outside_id)
            outside_object_after = (
                _storage_snapshot(case_id, group, "read_cross_folder_object_after", outside_before.get("parent_id", ""), outside_before.get("location", "")) if outside_before else {}
            )
            after_cross_count = len(_commit_rows(group, folder_id=folder_id))
            object_addresses.append((folder_id, f".objects/{cross_hash}"))
            cross_observed = _commit_access_observed(
                cross_response,
                before_commit_count=before_cross_count,
                after_commit_count=after_cross_count,
                before_file=outside_before,
                after_file=outside_after,
                before_object=outside_object_before,
                after_object=outside_object_after,
            )

            owner_file_before = _file_row(group, file_id)
            owner_object_before = _storage_snapshot(case_id, group, "read_attacker_write_object_before", folder_id, str(owner_file_before.get("location") or "")) if owner_file_before else {}
            before_attacker_count = len(_commit_rows(group, folder_id=folder_id))
            attacker_content = f"{prefix}-{group}-attacker-content"
            attacker_hash = hashlib.sha256(attacker_content.encode()).hexdigest()
            attacker_response = _create_commit(
                case_id,
                group,
                "attacker_create_commit",
                secondary["auth"],
                folder_id,
                f"{prefix} attacker secret message",
                [{"file_id": file_id, "file_name": original_name, "operation": "add", "content": attacker_content}],
            )
            owner_file_after = _file_row(group, file_id)
            owner_object_after = _storage_snapshot(case_id, group, "read_attacker_write_object_after", folder_id, str(owner_file_before.get("location") or "")) if owner_file_before else {}
            after_attacker_count = len(_commit_rows(group, folder_id=folder_id))
            object_addresses.append((folder_id, f".objects/{attacker_hash}"))
            attacker_observed = _commit_access_observed(
                attacker_response,
                before_commit_count=before_attacker_count,
                after_commit_count=after_attacker_count,
                before_file=owner_file_before,
                after_file=owner_file_after,
                before_object=owner_object_before,
                after_object=owner_object_after,
            )
            cleanup = _cleanup_commit_workspace(
                case_id,
                group,
                owner,
                prefix,
                [folder_id, outside_folder_id],
                object_addresses,
                secondary["email"],
            )
            passed = (
                base_ready
                and _response_pair(outside_folder) == [200, 0]
                and _response_pair(outside_upload) == [200, 0]
                and commit_access_denied_contract_ok(cross_observed)
                and commit_access_denied_contract_ok(attacker_observed)
                and cleanup["cleanup_succeeded"]
            )
            return _result(
                passed,
                [
                    {"name": "create_owner_add_commit_and_verify_row_item_tree_object", **initial_observed, "raw_sha256": initial_commit.get("raw_sha256")},
                    {
                        "name": "reject_file_outside_workspace_tree",
                        **cross_observed,
                        "actual_parent_points_outside_workspace": outside_after.get("parent_id") == outside_folder_id,
                        "raw_sha256": cross_response.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_commit_write", **attacker_observed, "raw_sha256": attacker_response.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"normal": "first add commit has one row/item/tree entry and exact object", "cross_folder_and_tenant": "authorization rejection with zero mutation"},
                "FM-COMMIT-ADD-SCOPE-IDOR-001",
                f"{group} add commit, workspace scope, or cross-tenant write contract differed",
            )

        if number == 46:
            modified_content = f"{prefix}-{group}-modified-content"
            modified_hash = hashlib.sha256(modified_content.encode()).hexdigest()
            modified_location = f".objects/{modified_hash}"
            modified = _create_commit(
                case_id,
                group,
                "create_modify_commit",
                owner["auth"],
                folder_id,
                f"{prefix} modified",
                [{"file_id": file_id, "file_name": original_name, "operation": "modify", "content": modified_content}],
            )
            modified_id = str(((modified.get("data") or {}) if isinstance(modified.get("data"), dict) else {}).get("id") or "")
            modified_rows = _commit_rows(group, commit_ids=[modified_id]) if modified_id else []
            modified_items = _commit_item_rows(group, commit_ids=[modified_id]) if modified_id else []
            modified_object = _storage_snapshot(case_id, group, "read_modify_commit_object", folder_id, modified_location)
            object_addresses.append((folder_id, modified_location))
            modified_ok = (
                _response_pair(modified) == [200, 0]
                and len(modified_rows) == len(modified_items) == 1
                and modified_rows[0]["parent_id"] == initial_commit_id
                and modified_items[0]["operation"] == "modify"
                and modified_items[0]["old_hash"] == initial_hash
                and modified_items[0]["new_hash"] == modified_hash
                and modified_rows[0]["tree_state"].get(file_id, {}).get("hash") == modified_hash
                and modified_object.get("exists") is True
                and modified_object.get("sha256") == modified_hash
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_modify_idor_object_before", folder_id, str(before_file.get("location") or "")) if before_file else {}
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker_content = f"{prefix}-{group}-attacker-modified"
            attacker_hash = hashlib.sha256(attacker_content.encode()).hexdigest()
            attacker = _create_commit(
                case_id,
                group,
                "attacker_modify_commit",
                secondary["auth"],
                folder_id,
                f"{prefix} attacker modify",
                [{"file_id": file_id, "file_name": original_name, "operation": "modify", "content": attacker_content}],
            )
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_modify_idor_object_after", folder_id, str(before_file.get("location") or "")) if before_file else {}
            after_count = len(_commit_rows(group, folder_id=folder_id))
            object_addresses.append((folder_id, f".objects/{attacker_hash}"))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and modified_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "create_add_then_modify_commit_chain",
                        "initial_commit_id": initial_commit_id,
                        "modify_response": _response_pair(modified),
                        "parent_matches": bool(modified_rows and modified_rows[0]["parent_id"] == initial_commit_id),
                        "old_new_hashes_match": bool(modified_items and modified_items[0]["old_hash"] == initial_hash and modified_items[0]["new_hash"] == modified_hash),
                        "tree_and_object_match": modified_ok,
                        "raw_sha256": modified.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_modify_commit", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"normal": "second commit parent=first with exact old/new hashes", "attacker": "authorization rejection and zero mutation"},
                "FM-COMMIT-MODIFY-IDOR-001",
                f"{group} modify commit chain or cross-tenant write contract differed",
            )

        if number == 47:
            before_commit_count = len(_commit_rows(group, folder_id=folder_id))
            before_item_count = len(_commit_item_rows(group, file_ids=[file_id]))
            before_file = _file_row(group, file_id)
            deleted = _create_commit(case_id, group, "create_delete_commit", owner["auth"], folder_id, f"{prefix} delete", [{"file_id": file_id, "operation": "delete"}])
            after_commit_count = len(_commit_rows(group, folder_id=folder_id))
            after_item_count = len(_commit_item_rows(group, file_ids=[file_id]))
            after_file = _file_row(group, file_id)
            delete_data = deleted.get("data") if isinstance(deleted.get("data"), dict) else {}
            delete_id = str(delete_data.get("id") or "")
            delete_rows = _commit_rows(group, commit_ids=[delete_id]) if delete_id else []
            delete_items = _commit_item_rows(group, commit_ids=[delete_id]) if delete_id else []
            historical = _commit_request(case_id, group, "read_history_after_delete_attempt", owner["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/files/{file_id}/content")
            functional_ok = (
                _response_pair(deleted) == [200, 0]
                and len(delete_rows) == len(delete_items) == 1
                and delete_items[0]["operation"] == "delete"
                and delete_rows[0]["tree_state"].get(file_id, {}).get("status") == "0"
                and _response_pair(historical) == [200, 0]
                and (historical.get("data") or {}).get("content") == initial_content
            )
            rollback_ok = _response_pair(deleted) != [200, 0] and after_commit_count == before_commit_count and after_item_count == before_item_count and after_file == before_file
            before_object = _storage_snapshot(case_id, group, "read_delete_idor_object_before", folder_id, initial_location)
            attacker_before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _create_commit(case_id, group, "attacker_delete_commit", secondary["auth"], folder_id, f"{prefix} attacker delete", [{"file_id": file_id, "operation": "delete"}])
            attacker_after_count = len(_commit_rows(group, folder_id=folder_id))
            attacker_after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_delete_idor_object_after", folder_id, initial_location)
            idor = _commit_access_observed(
                attacker,
                before_commit_count=attacker_before_count,
                after_commit_count=attacker_after_count,
                before_file=after_file,
                after_file=attacker_after_file,
                before_object=before_object,
                after_object=after_object,
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and functional_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "attempt_delete_commit_and_check_atomicity",
                        "response": _response_pair(deleted),
                        "functional_contract_satisfied": functional_ok,
                        "failure_path_rolled_back": rollback_ok,
                        "commit_count_before_after": [before_commit_count, after_commit_count],
                        "item_count_before_after": [before_item_count, after_item_count],
                        "file_unchanged_on_failure": after_file == before_file,
                        "raw_sha256": deleted.get("raw_sha256"),
                    },
                    {
                        "name": "verify_historical_content_if_delete_succeeded",
                        "response": _response_pair(historical),
                        "content_matches": (historical.get("data") or {}).get("content") == initial_content,
                        "raw_sha256": historical.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_delete_commit", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"functional": "delete item, status=0 tree, historical bytes readable", "failure": "atomic rollback with no half commit", "attacker": "authorization rejection"},
                "FM-COMMIT-DELETE-ATOMIC-IDOR-001",
                f"{group} delete commit functionality, atomicity, or authorization contract differed",
            )

        if number == 48:
            new_name = f"{prefix}-{group}-new.txt"
            renamed = _create_commit(
                case_id, group, "create_rename_commit", owner["auth"], folder_id, f"{prefix} rename", [{"file_id": file_id, "operation": "rename", "old_name": original_name, "new_name": new_name}]
            )
            rename_id = str(((renamed.get("data") or {}) if isinstance(renamed.get("data"), dict) else {}).get("id") or "")
            rename_rows = _commit_rows(group, commit_ids=[rename_id]) if rename_id else []
            rename_items = _commit_item_rows(group, commit_ids=[rename_id]) if rename_id else []
            renamed_file = _file_row(group, file_id)
            rename_ok = (
                _response_pair(renamed) == [200, 0]
                and len(rename_rows) == len(rename_items) == 1
                and rename_rows[0]["parent_id"] == initial_commit_id
                and rename_items[0]["operation"] == "rename"
                and rename_items[0]["old_name"] == original_name
                and rename_items[0]["new_name"] == new_name
                and renamed_file.get("name") == new_name
                and rename_rows[0]["tree_state"].get(file_id, {}).get("name") == new_name
            )
            before_object = _storage_snapshot(case_id, group, "read_rename_idor_object_before", folder_id, initial_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker_new_name = f"{prefix}-{group}-attacker.txt"
            attacker = _create_commit(
                case_id,
                group,
                "attacker_rename_commit",
                secondary["auth"],
                folder_id,
                f"{prefix} attacker rename",
                [{"file_id": file_id, "operation": "rename", "old_name": new_name, "new_name": attacker_new_name}],
            )
            after_count = len(_commit_rows(group, folder_id=folder_id))
            attacker_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_rename_idor_object_after", folder_id, initial_location)
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=renamed_file, after_file=attacker_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and rename_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "create_rename_commit_and_verify_item_file_tree",
                        "response": _response_pair(renamed),
                        "item_names_match": bool(rename_items and rename_items[0]["old_name"] == original_name and rename_items[0]["new_name"] == new_name),
                        "file_name": renamed_file.get("name"),
                        "tree_name": rename_rows[0]["tree_state"].get(file_id, {}).get("name") if rename_rows else None,
                        "raw_sha256": renamed.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_rename_commit", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"normal": "old/new names in item and live/tree updated", "attacker": "authorization rejection with zero mutation"},
                "FM-COMMIT-RENAME-IDOR-001",
                f"{group} rename commit or cross-tenant write contract differed",
            )

        if number == 49:
            commit_ids = [initial_commit_id]
            created_responses = [initial_commit]
            for index in range(2):
                time.sleep(0.01)
                content = f"{prefix}-{group}-list-version-{index + 2}"
                content_hash = hashlib.sha256(content.encode()).hexdigest()
                response = _create_commit(
                    case_id,
                    group,
                    f"create_list_commit_{index + 2}",
                    owner["auth"],
                    folder_id,
                    f"{prefix} commit {index + 2}",
                    [{"file_id": file_id, "file_name": original_name, "operation": "modify", "content": content}],
                )
                response_data = response.get("data") if isinstance(response.get("data"), dict) else {}
                commit_ids.append(str(response_data.get("id") or ""))
                created_responses.append(response)
                object_addresses.append((folder_id, f".objects/{content_hash}"))
            listed = _commit_request(case_id, group, "list_commits_page", owner["auth"], "GET", f"/folders/{folder_id}/commits", params={"page": 1, "page_size": 10})
            list_data = listed.get("data") if isinstance(listed.get("data"), dict) else {}
            commits = list_data.get("commits") if isinstance(list_data.get("commits"), list) else []
            returned_ids = [str(item.get("id") or "") for item in commits]
            returned_times = [int(item.get("create_time") or 0) for item in commits]
            list_ok = (
                _response_pair(listed) == [200, 0]
                and list_data.get("total") == 3
                and set(returned_ids) == set(commit_ids)
                and returned_times == sorted(returned_times, reverse=True)
                and all(all(key in item for key in ("id", "message", "author_id", "create_time")) for item in commits)
            )
            invalid_params = [
                {"page": "abc"},
                {"page": 0},
                {"page_size": 0},
                {"order_by": "__bad__"},
            ]
            invalid = [
                _commit_request(case_id, group, f"reject_invalid_commit_list_{index + 1}", owner["auth"], "GET", f"/folders/{folder_id}/commits", params=params)
                for index, params in enumerate(invalid_params)
            ]
            invalid_ok = all(_response_pair(item) == [200, 101] for item in invalid)
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_list_idor_object_before", folder_id, str(before_file.get("location") or "")) if before_file else {}
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_list_commits", secondary["auth"], "GET", f"/folders/{folder_id}/commits", params={"page": 1, "page_size": 10})
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_list_idor_object_after", folder_id, str(before_file.get("location") or "")) if before_file else {}
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = (
                base_ready
                and all(_response_pair(item) == [200, 0] for item in created_responses)
                and list_ok
                and invalid_ok
                and commit_access_denied_contract_ok(idor)
                and cleanup["cleanup_succeeded"]
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_three_commits_and_list_page",
                        "response": _response_pair(listed),
                        "total": list_data.get("total"),
                        "returned_ids": returned_ids,
                        "descending": returned_times == sorted(returned_times, reverse=True),
                        "required_fields_present": all(all(key in item for key in ("id", "message", "author_id", "create_time")) for item in commits),
                        "raw_sha256": listed.get("raw_sha256"),
                    },
                    {
                        "name": "validate_commit_list_parameters",
                        "responses": [_response_pair(item) for item in invalid],
                        "all_argument_errors": invalid_ok,
                        "raw_sha256": [item.get("raw_sha256") for item in invalid],
                    },
                    {"name": "reject_independent_tenant_commit_list", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"list": "three commits newest-first with exact total", "invalid": "all code 101", "attacker": "authorization rejection"},
                "FM-COMMIT-LIST-PARAM-IDOR-001",
                f"{group} commit listing, parameter validation, or authorization contract differed",
            )

        if number == 50:
            detail = _commit_request(case_id, group, "get_commit_detail", owner["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}")
            data = detail.get("data") if isinstance(detail.get("data"), dict) else {}
            files = data.get("files") if isinstance(data.get("files"), list) else []
            detail_ok = (
                _response_pair(detail) == [200, 0]
                and data.get("id") == initial_commit_id
                and data.get("folder_id") == folder_id
                and data.get("file_count") == 1
                and len(files) == 1
                and files[0].get("file_id") == file_id
                and files[0].get("operation") == "add"
                and all(key in files[0] for key in ("old_hash", "new_hash", "old_name", "new_name"))
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_detail_idor_object_before", folder_id, initial_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_get_commit_detail", secondary["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}")
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_detail_idor_object_after", folder_id, initial_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and detail_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "get_complete_commit_detail",
                        "response": _response_pair(detail),
                        "commit_matches": data.get("id") == initial_commit_id and data.get("folder_id") == folder_id,
                        "file_count": len(files),
                        "file_fields_present": bool(files and all(key in files[0] for key in ("file_id", "operation", "old_hash", "new_hash", "old_name", "new_name"))),
                        "raw_sha256": detail.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_commit_detail", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"detail": "commit and one complete file item", "attacker": "authorization rejection without data"},
                "FM-COMMIT-DETAIL-IDOR-001",
                f"{group} commit detail or authorization contract differed",
            )

        if number == 51:
            listed = _commit_request(case_id, group, "list_commit_files", owner["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/files")
            items = listed.get("data") if isinstance(listed.get("data"), list) else []
            required = ("id", "file_id", "operation", "old_hash", "new_hash", "old_location", "new_location", "old_name", "new_name")
            list_ok = _response_pair(listed) == [200, 0] and len(items) == 1 and items[0].get("file_id") == file_id and items[0].get("operation") == "add" and all(key in items[0] for key in required)
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_items_idor_object_before", folder_id, initial_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_list_commit_files", secondary["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/files")
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_items_idor_object_after", folder_id, initial_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and list_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "list_complete_commit_items",
                        "response": _response_pair(listed),
                        "item_count": len(items),
                        "required_fields_present": bool(items and all(key in items[0] for key in required)),
                        "raw_sha256": listed.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_commit_items", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"items": "one add item with all persisted fields", "attacker": "authorization rejection"},
                "FM-COMMIT-ITEMS-IDOR-001",
                f"{group} commit item listing or authorization contract differed",
            )

        if number == 52:
            modified_content = f"{prefix}-{group}-diff-modified"
            modified_hash = hashlib.sha256(modified_content.encode()).hexdigest()
            modified_location = f".objects/{modified_hash}"
            modified = _create_commit(
                case_id,
                group,
                "create_diff_target_commit",
                owner["auth"],
                folder_id,
                f"{prefix} diff target",
                [{"file_id": file_id, "file_name": original_name, "operation": "modify", "content": modified_content}],
            )
            modified_id = str(((modified.get("data") or {}) if isinstance(modified.get("data"), dict) else {}).get("id") or "")
            object_addresses.append((folder_id, modified_location))
            diff = _commit_request(case_id, group, "diff_two_commits", owner["auth"], "GET", f"/folders/{folder_id}/commits/diff", params={"from": initial_commit_id, "to": modified_id})
            entries = diff.get("data") if isinstance(diff.get("data"), list) else []
            diff_ok = (
                _response_pair(modified) == [200, 0]
                and _response_pair(diff) == [200, 0]
                and len(entries) == 1
                and entries[0].get("file_id") == file_id
                and entries[0].get("operation") == "modify"
                and entries[0].get("old_hash") == initial_hash
                and entries[0].get("new_hash") == modified_hash
                and entries[0].get("old_location") == initial_location
                and entries[0].get("new_location") == modified_location
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_diff_idor_object_before", folder_id, modified_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_diff_commits", secondary["auth"], "GET", f"/folders/{folder_id}/commits/diff", params={"from": initial_commit_id, "to": modified_id})
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_diff_idor_object_after", folder_id, modified_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and diff_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "diff_tree_state_snapshots",
                        "response": _response_pair(diff),
                        "entry_count": len(entries),
                        "exact_modify_hash_location_contract": diff_ok,
                        "raw_sha256": diff.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_commit_diff", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"diff": "one modify with exact old/new hash and location", "attacker": "authorization rejection"},
                "FM-COMMIT-DIFF-IDOR-001",
                f"{group} commit diff or authorization contract differed",
            )

        if number == 53:
            new_content = b"fresh-uncommitted-file"
            new_upload = _upload(case_id, group, owner, "upload_uncommitted_file", [(f"{prefix}-{group}-uncommitted.txt", new_content, "text/plain")], parent_id=folder_id)
            new_file_id = str(((new_upload.get("data") or [{}])[0]).get("id") or "")
            new_file_row = _file_row(group, new_file_id)
            if new_file_row:
                object_addresses.append((new_file_row["parent_id"], str(new_file_row.get("location") or "")))
            changes = _commit_request(case_id, group, "get_uncommitted_changes", owner["auth"], "GET", f"/folders/{folder_id}/changes")
            entries = changes.get("data") if isinstance(changes.get("data"), list) else []
            matching = [item for item in entries if item.get("file_id") == new_file_id]
            changes_ok = (
                _response_pair(new_upload) == [200, 0]
                and _response_pair(changes) == [200, 0]
                and len(matching) == 1
                and matching[0].get("operation") == "add"
                and matching[0].get("file_name") == new_file_row.get("name")
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_changes_idor_object_before", folder_id, initial_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_get_uncommitted_changes", secondary["auth"], "GET", f"/folders/{folder_id}/changes")
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_changes_idor_object_after", folder_id, initial_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and changes_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "create_and_detect_uncommitted_file",
                        "response": _response_pair(changes),
                        "entry_count": len(entries),
                        "new_file_add_detected": changes_ok,
                        "raw_sha256": changes.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_uncommitted_changes", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"changes": "new live file appears exactly as add", "attacker": "authorization rejection"},
                "FM-COMMIT-CHANGES-IDOR-001",
                f"{group} uncommitted changes or authorization contract differed",
            )

        if number == 54:
            old_nested_name = f"{prefix}-{group}-old-subfolder"
            new_nested_name = f"{prefix}-{group}-new-subfolder"
            rename_live = _move_files(case_id, group, owner, "rename_live_subfolder_after_commit", [nested_id], new_name=new_nested_name)
            tree = _commit_request(case_id, group, "get_historical_commit_tree", owner["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/tree")
            tree_data = tree.get("data") if isinstance(tree.get("data"), dict) else {}

            def flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
                result = [node]
                for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
                    if isinstance(child, dict):
                        result.extend(flatten(child))
                return result

            nodes = flatten(tree_data) if tree_data else []
            nested_nodes = [item for item in nodes if item.get("id") == nested_id]
            file_nodes = [item for item in nodes if item.get("id") == file_id]
            tree_ok = (
                _response_pair(rename_live) == [200, 0]
                and _response_pair(tree) == [200, 0]
                and tree_data.get("id") == folder_id
                and tree_data.get("type") == "folder"
                and len(nested_nodes) == 1
                and nested_nodes[0].get("name") == old_nested_name
                and nested_nodes[0].get("type") == "folder"
                and len(file_nodes) == 1
                and file_nodes[0].get("name") == original_name
                and file_nodes[0].get("type") == "file"
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_tree_idor_object_before", folder_id, initial_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_get_commit_tree", secondary["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/tree")
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_tree_idor_object_after", folder_id, initial_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and tree_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "rename_live_subfolder_after_commit",
                        "response": _response_pair(rename_live),
                        "old_name": old_nested_name,
                        "new_name": new_nested_name,
                        "raw_sha256": rename_live.get("raw_sha256"),
                    },
                    {
                        "name": "read_historical_hierarchical_tree",
                        "response": _response_pair(tree),
                        "root_matches": tree_data.get("id") == folder_id,
                        "nested_name": nested_nodes[0].get("name") if nested_nodes else None,
                        "historical_name_stable": bool(nested_nodes and nested_nodes[0].get("name") == old_nested_name),
                        "file_nested": len(file_nodes) == 1,
                        "raw_sha256": tree.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_commit_tree", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"tree": "commit-time nested name and hierarchy remain after live rename", "attacker": "authorization rejection"},
                "FM-COMMIT-TREE-SNAPSHOT-IDOR-001",
                f"{group} historical tree stability or authorization contract differed",
            )

        if number == 55:
            modified_content = f"{prefix}-{group}-later-content"
            modified_hash = hashlib.sha256(modified_content.encode()).hexdigest()
            modified = _create_commit(
                case_id,
                group,
                "create_later_content_commit",
                owner["auth"],
                folder_id,
                f"{prefix} later",
                [{"file_id": file_id, "file_name": original_name, "operation": "modify", "content": modified_content}],
            )
            object_addresses.append((folder_id, f".objects/{modified_hash}"))
            content = _commit_request(case_id, group, "get_historical_file_content", owner["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/files/{file_id}/content")
            content_ok = _response_pair(modified) == [200, 0] and _response_pair(content) == [200, 0] and (content.get("data") or {}).get("content") == initial_content
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_content_idor_object_before", folder_id, initial_location)
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_get_historical_content", secondary["auth"], "GET", f"/folders/{folder_id}/commits/{initial_commit_id}/files/{file_id}/content")
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_content_idor_object_after", folder_id, initial_location)
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and content_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "read_initial_content_after_later_modify",
                        "response": _response_pair(content),
                        "content_length": len(str((content.get("data") or {}).get("content") or "")),
                        "content_matches_initial": (content.get("data") or {}).get("content") == initial_content,
                        "raw_sha256": content.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_historical_content", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"content": "exact initial bytes from content-addressable snapshot", "attacker": "authorization rejection without content"},
                "FM-COMMIT-CONTENT-IDOR-001",
                f"{group} historical content or authorization contract differed",
            )

        if number == 56:
            time.sleep(0.01)
            modified_content = f"{prefix}-{group}-version-two"
            modified_hash = hashlib.sha256(modified_content.encode()).hexdigest()
            modified = _create_commit(
                case_id,
                group,
                "create_second_file_version",
                owner["auth"],
                folder_id,
                f"{prefix} version two",
                [{"file_id": file_id, "file_name": original_name, "operation": "modify", "content": modified_content}],
            )
            modified_id = str(((modified.get("data") or {}) if isinstance(modified.get("data"), dict) else {}).get("id") or "")
            object_addresses.append((folder_id, f".objects/{modified_hash}"))
            versions = _commit_request(case_id, group, "get_file_versions", owner["auth"], "GET", f"/files/{file_id}/versions")
            rows = versions.get("data") if isinstance(versions.get("data"), list) else []
            times = [int(item.get("create_time") or 0) for item in rows]
            versions_ok = (
                _response_pair(modified) == [200, 0]
                and _response_pair(versions) == [200, 0]
                and len(rows) == 2
                and {str(item.get("commit_id") or "") for item in rows} == {initial_commit_id, modified_id}
                and times == sorted(times, reverse=True)
                and {str(item.get("operation") or "") for item in rows} == {"add", "modify"}
                and {str(item.get("hash") or "") for item in rows} == {initial_hash, modified_hash}
                and all(all(key in item for key in ("commit_id", "operation", "hash", "create_time", "message")) for item in rows)
            )
            before_file = _file_row(group, file_id)
            before_object = _storage_snapshot(case_id, group, "read_versions_idor_object_before", folder_id, f".objects/{modified_hash}")
            before_count = len(_commit_rows(group, folder_id=folder_id))
            attacker = _commit_request(case_id, group, "attacker_get_file_versions", secondary["auth"], "GET", f"/files/{file_id}/versions")
            after_file = _file_row(group, file_id)
            after_object = _storage_snapshot(case_id, group, "read_versions_idor_object_after", folder_id, f".objects/{modified_hash}")
            after_count = len(_commit_rows(group, folder_id=folder_id))
            idor = _commit_access_observed(
                attacker, before_commit_count=before_count, after_commit_count=after_count, before_file=before_file, after_file=after_file, before_object=before_object, after_object=after_object
            )
            cleanup = _cleanup_commit_workspace(case_id, group, owner, prefix, [folder_id], object_addresses, secondary["email"])
            passed = base_ready and versions_ok and commit_access_denied_contract_ok(idor) and cleanup["cleanup_succeeded"]
            return _result(
                passed,
                [
                    {
                        "name": "create_two_versions_and_list_history",
                        "response": _response_pair(versions),
                        "version_count": len(rows),
                        "commit_ids_match": {str(item.get("commit_id") or "") for item in rows} == {initial_commit_id, modified_id},
                        "descending": times == sorted(times, reverse=True),
                        "hashes_match": {str(item.get("hash") or "") for item in rows} == {initial_hash, modified_hash},
                        "raw_sha256": versions.get("raw_sha256"),
                    },
                    {"name": "reject_independent_tenant_file_versions", **idor, "raw_sha256": attacker.get("raw_sha256")},
                    {"name": "cleanup_active_files_objects_and_secondary_user", **cleanup},
                ],
                {"versions": "two complete records newest-first with exact hashes", "attacker": "authorization rejection without messages/hashes"},
                "FM-FILE-VERSIONS-IDOR-001",
                f"{group} file version history or authorization contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_fm_058_to_070(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-fm-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        root = _root(case_id, group, owner)
        root_id = str(root["row"]["id"])

        if number == 58:
            content = b"%PDF-1.4\nfresh download contract\n%%EOF\n"
            filename = f"{prefix}-{group}.pdf"
            uploaded = _upload(
                case_id,
                group,
                owner,
                "upload_download_fixture",
                [(filename, content, "application/pdf")],
            )
            file_id = str(((uploaded.get("data") or [{}])[0]).get("id") or "")
            row = _file_row(group, file_id)
            stored = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_download_object_before",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                if row
                else {}
            )
            downloaded = _http(
                case_id,
                group,
                "download_file",
                owner["auth"],
                "GET",
                f"/files/{file_id}",
            )
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_download_object_after_cleanup",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                if row
                else {}
            )
            observed = {
                "response": _response_pair(downloaded),
                "bytes_match": downloaded.get("_content") == content,
                "content_type_matches": str(downloaded.get("content_type") or "").split(";", 1)[0] == "application/pdf",
                "database_size_matches": row.get("size") == len(content),
                "storage_size_matches": stored.get("length") == len(content),
                "storage_sha_matches": stored.get("sha256") == hashlib.sha256(content).hexdigest(),
                "cleanup_succeeded": cleanup and not _file_row(group, file_id),
                "object_absent_after_cleanup": after.get("exists") is False,
            }
            passed = preclean and _response_pair(uploaded) == [200, 0] and download_contract_ok(observed)
            return _result(
                passed,
                [
                    {
                        "name": "upload_file_and_verify_database_storage",
                        "upload_response": _response_pair(uploaded),
                        "file_id_present": bool(file_id),
                        "database_size": row.get("size"),
                        "storage_length": stored.get("length"),
                        "storage_sha_matches": observed["storage_sha_matches"],
                        "raw_sha256": [uploaded.get("raw_sha256"), stored.get("raw_sha256")],
                    },
                    {
                        "name": "download_exact_binary_and_content_type",
                        **observed,
                        "download_length": downloaded.get("response_length"),
                        "content_type": downloaded.get("content_type"),
                        "raw_sha256": [downloaded.get("raw_sha256"), after.get("raw_sha256")],
                    },
                ],
                {"response": [200, "binary"], "content_type": "application/pdf", "bytes": "exact", "cleanup": "row and object absent"},
                "FM-DOWNLOAD-BYTES-001",
                f"{group} file download bytes, metadata, or cleanup contract differed",
            )

        if number == 59:
            missing = _missing_file_id(group)
            before = len(_file_rows(group))
            response = _http(
                case_id,
                group,
                "download_missing_file",
                owner["auth"],
                "GET",
                f"/files/{missing}",
            )
            after = len(_file_rows(group))
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = preclean and _response_pair(response) == [200, 102] and response.get("message") == "Document not found!" and before == after and not _file_row(group, missing) and cleanup
            return _result(
                passed,
                [
                    {
                        "name": "download_nonexistent_file",
                        "response": _response_pair(response),
                        "message": response.get("message"),
                        "raw_sha256": response.get("raw_sha256"),
                    },
                    {"name": "verify_zero_metadata_delta", "before": before, "after": after, "cleanup_succeeded": cleanup},
                ],
                {"response": [200, 102, "Document not found!"], "mutation": "none"},
                "FM-DOWNLOAD-MISSING-001",
                f"{group} missing file download response or zero-delta contract differed",
            )

        if number == 60:
            filename = f"{prefix}-{group}-empty.txt"
            uploaded = _upload(
                case_id,
                group,
                owner,
                "upload_empty_file",
                [(filename, b"", "text/plain")],
            )
            file_id = str(((uploaded.get("data") or [{}])[0]).get("id") or "")
            row = _file_row(group, file_id)
            before_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_empty_object_before",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                if row
                else {}
            )
            relations = _file_document_rows(group, file_ids=[file_id]) if file_id else []
            response = _http(
                case_id,
                group,
                "download_empty_file",
                owner["auth"],
                "GET",
                f"/files/{file_id}",
            )
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            after_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_empty_object_after_cleanup",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                if row
                else {}
            )
            passed = (
                preclean
                and _response_pair(uploaded) == [200, 0]
                and row.get("size") == 0
                and before_object.get("exists") is True
                and before_object.get("length") == 0
                and relations == []
                and _response_pair(response) == [200, 102]
                and response.get("message") == "This file is empty."
                and cleanup
                and not _file_row(group, file_id)
                and after_object.get("exists") is False
            )
            return _result(
                passed,
                [
                    {
                        "name": "upload_unlinked_zero_byte_file",
                        "response": _response_pair(uploaded),
                        "database_size": row.get("size"),
                        "primary_object_exists": before_object.get("exists"),
                        "primary_object_length": before_object.get("length"),
                        "file_document_relation_count": len(relations),
                        "raw_sha256": [uploaded.get("raw_sha256"), before_object.get("raw_sha256")],
                    },
                    {
                        "name": "download_empty_file_after_primary_and_fallback_lookup",
                        "response": _response_pair(response),
                        "message": response.get("message"),
                        "cleanup_succeeded": cleanup,
                        "object_absent_after_cleanup": after_object.get("exists") is False,
                        "raw_sha256": [response.get("raw_sha256"), after_object.get("raw_sha256")],
                    },
                ],
                {"response": [200, 102, "This file is empty."], "fixture": "unlinked zero-byte primary object", "cleanup": "row and object absent"},
                "FM-DOWNLOAD-EMPTY-001",
                f"{group} empty file download fallback or explicit error contract differed",
            )

        if number == 61:
            initial_ids = {row["id"] for row in _file_rows(group, tenant_id=owner["tenant_id"])}
            special_name = f"{prefix}-{group}-测试@#$%^&()_+-=[]{{}}|;:,.<>?.txt"
            special = _create_folder(case_id, group, owner, "create_special_character_folder", special_name)
            special_id = str((special.get("data") or {}).get("id") or "")
            special_row = _file_row(group, special_id)
            path_parent_name = f"{prefix}-{group}-path-parent"
            path_parent = _create_folder(case_id, group, owner, "create_path_test_parent", path_parent_name)
            path_parent_id = str((path_parent.get("data") or {}).get("id") or "")

            attempts: list[dict[str, Any]] = []
            object_addresses: list[tuple[str, str]] = []
            filenames = [
                f"../{prefix}-{group}-escape.txt",
                f"/{prefix}-{group}-absolute/{prefix}-{group}-absolute.txt",
            ]
            for index, filename in enumerate(filenames, start=1):
                before_rows = {row["id"]: row for row in _file_rows(group, tenant_id=owner["tenant_id"])}
                response = _upload(
                    case_id,
                    group,
                    owner,
                    f"upload_path_style_name_{index}",
                    [(filename, f"path-attempt-{index}".encode(), "text/plain")],
                    parent_id=path_parent_id,
                )
                after_rows = {row["id"]: row for row in _file_rows(group, tenant_id=owner["tenant_id"])}
                new_rows = [row for row_id, row in after_rows.items() if row_id not in before_rows]
                returned = (response.get("data") or [{}])[0] if isinstance(response.get("data"), list) else {}
                returned_id = str(returned.get("id") or "")
                returned_row = after_rows.get(returned_id, {})
                expected_basename = Path(filename).name
                unsafe_rows = [row for row in new_rows if row.get("id") != returned_id]
                if returned_row and returned_row.get("type") != "folder":
                    object_addresses.append((str(returned_row.get("parent_id") or ""), str(returned_row.get("location") or "")))
                observed = {
                    "response": _response_pair(response),
                    "new_row_count": len(new_rows),
                    "returned_name": returned_row.get("name") or returned.get("name"),
                    "expected_basename": expected_basename,
                    "returned_parent_is_requested": returned_row.get("parent_id") == path_parent_id,
                    "unsafe_intermediate_count": len(unsafe_rows),
                }
                attempts.append(
                    {
                        **observed,
                        "filename_style": "parent_traversal" if index == 1 else "absolute",
                        "safe_contract": path_filename_attempt_ok(observed),
                        "new_row_names": [str(row.get("name") or "") for row in new_rows],
                        "raw_sha256": response.get("raw_sha256"),
                    }
                )

            listed = _http(
                case_id,
                group,
                "list_special_character_parent",
                owner["auth"],
                "GET",
                "/files",
                params={"parent_id": root_id, "page": 1, "page_size": 100},
            )
            listed_files = ((listed.get("data") or {}).get("files") or []) if isinstance(listed.get("data"), dict) else []
            special_list_rows = [item for item in listed_files if str(item.get("id") or "") == special_id]
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_states = [_storage_snapshot(case_id, group, f"read_path_object_after_cleanup_{index + 1}", parent, location) for index, (parent, location) in enumerate(object_addresses)]
            created_ids = {row["id"] for row in _file_rows(group, tenant_id=owner["tenant_id"]) if row["id"] not in initial_ids}
            special_ok = (
                _response_pair(special) == [200, 0]
                and special_row.get("name") == special_name
                and special_row.get("parent_id") == root_id
                and len(special_list_rows) == 1
                and special_list_rows[0].get("name") == special_name
            )
            passed = (
                preclean
                and special_ok
                and _response_pair(path_parent) == [200, 0]
                and bool(path_parent_id)
                and all(item["safe_contract"] for item in attempts)
                and cleanup
                and not created_ids
                and all(item.get("exists") is False for item in object_states)
            )
            return _result(
                passed,
                [
                    {
                        "name": "round_trip_special_character_folder_name",
                        "response": _response_pair(special),
                        "database_name_exact": special_row.get("name") == special_name,
                        "list_name_exact": len(special_list_rows) == 1 and special_list_rows[0].get("name") == special_name,
                        "raw_sha256": [special.get("raw_sha256"), listed.get("raw_sha256")],
                    },
                    {"name": "exercise_parent_traversal_and_absolute_multipart_names", "attempts": attempts},
                    {
                        "name": "cleanup_all_created_hierarchy_and_objects",
                        "cleanup_succeeded": cleanup,
                        "created_rows_remaining": len(created_ids),
                        "objects_remaining": sum(item.get("exists") is True for item in object_states),
                        "raw_sha256": [item.get("raw_sha256") for item in object_states],
                    },
                ],
                {"special_name": "exact round trip", "path_names": "reject or safe basename directly under requested parent", "unsafe_intermediate_folders": 0},
                "FM-NAME-PATH-NORMALIZATION-001",
                f"{group} special filename round trip or path normalization contract differed",
            )

        if number == 62:
            name = f"{prefix}-{group}-日本語テスト文件_中文_한국어"
            created = _create_folder(case_id, group, owner, "create_unicode_folder", name)
            file_id = str((created.get("data") or {}).get("id") or "")
            row = _file_row(group, file_id)
            listed = _http(
                case_id,
                group,
                "list_unicode_folder",
                owner["auth"],
                "GET",
                "/files",
                params={"parent_id": root_id, "page": 1, "page_size": 100},
            )
            files = ((listed.get("data") or {}).get("files") or []) if isinstance(listed.get("data"), dict) else []
            matches = [item for item in files if str(item.get("id") or "") == file_id]
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = (
                preclean
                and _response_pair(created) == [200, 0]
                and _response_pair(listed) == [200, 0]
                and row.get("name") == name
                and row.get("parent_id") == root_id
                and len(matches) == 1
                and matches[0].get("name") == name
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_and_list_multiscript_unicode_folder",
                        "create_response": _response_pair(created),
                        "list_response": _response_pair(listed),
                        "database_name_exact": row.get("name") == name,
                        "api_name_exact": len(matches) == 1 and matches[0].get("name") == name,
                        "parent_matches": row.get("parent_id") == root_id,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [created.get("raw_sha256"), listed.get("raw_sha256")],
                    }
                ],
                {"response": [200, 0], "database_and_api_name": "exact Unicode", "cleanup": "absent"},
                "FM-NAME-UNICODE-001",
                f"{group} Unicode folder name storage or API round-trip contract differed",
            )

        if number == 63:
            name = f"{prefix}-{group}-并发文件夹"
            request_count = 5
            barrier = threading.Barrier(request_count)

            def create_one(index: int) -> dict[str, Any]:
                barrier.wait(timeout=10)
                return _create_folder(
                    case_id,
                    group,
                    owner,
                    f"concurrent_create_same_folder_{index + 1}",
                    name,
                    parent_id=root_id,
                )

            started = time.monotonic()
            responses: list[dict[str, Any]] = []
            errors: list[str] = []
            with ThreadPoolExecutor(max_workers=request_count) as executor:
                futures = [executor.submit(create_one, index) for index in range(request_count)]
                for future in futures:
                    try:
                        responses.append(future.result(timeout=180))
                    except Exception as error:
                        errors.append(type(error).__name__)
            elapsed = time.monotonic() - started
            rows = [row for row in _file_rows(group, tenant_id=owner["tenant_id"], parent_id=root_id) if row.get("name") == name]
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "response_pairs": [_response_pair(item) for item in responses],
                "messages": [item.get("message") for item in responses],
                "database_row_count": len(rows),
                "all_requests_completed": len(responses) == request_count and not errors,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and concurrent_folder_contract_ok(observed, request_count=request_count)
            return _result(
                passed,
                [
                    {
                        "name": "barrier_release_five_same_name_creates",
                        **observed,
                        "elapsed_seconds": elapsed,
                        "errors": errors,
                        "raw_sha256": [item.get("raw_sha256") for item in responses],
                    }
                ],
                {"requests": 5, "success": 1, "duplicates": 4, "database_rows": 1, "deadlock": "none"},
                "FM-FOLDER-CONCURRENT-DUPLICATE-001",
                f"{group} concurrent same-name folder uniqueness contract differed",
            )

        if number == 64:
            folder_ids: list[str] = []
            responses: list[dict[str, Any]] = []
            parent_id = root_id
            for level in range(1, 16):
                response = _create_folder(
                    case_id,
                    group,
                    owner,
                    f"create_deep_level_{level:02d}",
                    f"{prefix}-{group}-level-{level:02d}",
                    parent_id=parent_id,
                )
                responses.append(response)
                created_id = str((response.get("data") or {}).get("id") or "")
                if _response_pair(response) != [200, 0] or not created_id:
                    break
                folder_ids.append(created_id)
                parent_id = created_id
            content = b"deep hierarchy file"
            uploaded = (
                _upload(
                    case_id,
                    group,
                    owner,
                    "upload_file_at_level_15",
                    [(f"{prefix}-{group}-deep.txt", content, "text/plain")],
                    parent_id=folder_ids[-1],
                )
                if len(folder_ids) == 15
                else {}
            )
            file_id = str(((uploaded.get("data") or [{}])[0]).get("id") or "") if isinstance(uploaded.get("data"), list) else ""
            row = _file_row(group, file_id)
            ancestors = _http(case_id, group, "get_deep_file_ancestors", owner["auth"], "GET", f"/files/{file_id}/ancestors") if file_id else {}
            returned = ((ancestors.get("data") or {}).get("parent_folders") or []) if isinstance(ancestors.get("data"), dict) else []
            returned_ids = [str(item.get("id") or "") for item in returned]
            expected_ids = [file_id, *reversed(folder_ids), root_id] if file_id else []
            parent_chain_ok = all(_file_row(group, folder_id).get("parent_id") == (root_id if index == 0 else folder_ids[index - 1]) for index, folder_id in enumerate(folder_ids)) and row.get(
                "parent_id"
            ) == (folder_ids[-1] if folder_ids else None)
            object_before = _storage_snapshot(case_id, group, "read_deep_file_object_before", str(row.get("parent_id") or ""), str(row.get("location") or "")) if row else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = _storage_snapshot(case_id, group, "read_deep_file_object_after_cleanup", str(row.get("parent_id") or ""), str(row.get("location") or "")) if row else {}
            passed = (
                preclean
                and len(folder_ids) == 15
                and all(_response_pair(item) == [200, 0] for item in responses)
                and _response_pair(uploaded) == [200, 0]
                and _response_pair(ancestors) == [200, 0]
                and returned_ids == expected_ids
                and len(returned_ids) == 17
                and parent_chain_ok
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and cleanup
                and object_after.get("exists") is False
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_fifteen_level_folder_chain_and_file",
                        "folder_count": len(folder_ids),
                        "all_create_responses_success": all(_response_pair(item) == [200, 0] for item in responses),
                        "upload_response": _response_pair(uploaded),
                        "database_parent_chain_matches": parent_chain_ok,
                        "raw_sha256": [item.get("raw_sha256") for item in responses] + [uploaded.get("raw_sha256")],
                    },
                    {
                        "name": "get_seventeen_entry_deep_ancestor_path",
                        "response": _response_pair(ancestors),
                        "expected_ids": expected_ids,
                        "returned_ids": returned_ids,
                        "length": len(returned_ids),
                        "cleanup_succeeded": cleanup,
                        "object_absent_after_cleanup": object_after.get("exists") is False,
                        "raw_sha256": [ancestors.get("raw_sha256"), object_before.get("raw_sha256"), object_after.get("raw_sha256")],
                    },
                ],
                {"folders": 15, "ancestors_order": "file,level15..level1,root", "ancestor_count": 17},
                "FM-HIERARCHY-DEEP-001",
                f"{group} deep folder creation or complete ancestor path contract differed",
            )

        if number == 65:
            before = len(_file_rows(group))
            response = _http(case_id, group, "list_files_without_authorization", None, "GET", "/files")
            after = len(_file_rows(group))
            observed = {
                "http_status": response.get("http_status"),
                "code": response.get("code"),
                "data_absent": _response_has_no_data(response),
                "file_delta": after - before,
            }
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            passed = preclean and unauthenticated_contract_ok(observed) and cleanup
            return _result(
                passed,
                [
                    {
                        "name": "use_fresh_client_without_authorization_or_cookie",
                        **observed,
                        "message": response.get("message"),
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": response.get("raw_sha256"),
                    }
                ],
                {"authentication": "rejected", "data": "absent", "metadata_delta": 0},
                "FM-AUTH-MISSING-001",
                f"{group} missing Authorization rejection or zero-mutation contract differed",
            )

        if number == 66:
            secondary = _prepare_secondary_user(case_id, group, owner, number)
            secondary_owner = {
                "auth": secondary["auth"],
                "tenant_id": secondary["tenant_id"],
            }
            secondary_ready = (
                secondary["preclean"] and secondary["registration_code"] == 0 and secondary["login_code"] == 0 and bool(secondary["auth"]) and owner["tenant_id"] != secondary["tenant_id"]
            )
            secondary_root = _root(case_id, group, secondary_owner) if secondary_ready else {"row": {}}
            secondary_root_id = str((secondary_root.get("row") or {}).get("id") or "")
            secondary_file_preclean = _cleanup_file_prefix(case_id, group, secondary_owner, prefix) if secondary_ready else False
            dataset_name = f"{prefix}-{group}-tenant-b-dataset"
            dataset_preclean = (
                DD._cleanup_prefix(
                    case_id,
                    group,
                    secondary_owner["auth"],
                    secondary_owner["tenant_id"],
                    dataset_name,
                )
                if secondary_ready
                else False
            )

            victim_folder = (
                _create_folder(
                    case_id,
                    group,
                    secondary_owner,
                    "create_tenant_b_victim_folder",
                    f"{prefix}-{group}-tenant-b-folder",
                    parent_id=secondary_root_id,
                )
                if secondary_ready
                else {}
            )
            victim_folder_id = str((victim_folder.get("data") or {}).get("id") or "")
            victim_content = b"tenant-b-owner-only-file"
            victim_upload = (
                _upload(
                    case_id,
                    group,
                    secondary_owner,
                    "upload_tenant_b_victim_file",
                    [(f"{prefix}-{group}-tenant-b.txt", victim_content, "text/plain")],
                    parent_id=victim_folder_id,
                )
                if victim_folder_id
                else {}
            )
            victim_file_id = str(((victim_upload.get("data") or [{}])[0]).get("id") or "") if isinstance(victim_upload.get("data"), list) else ""
            victim_file = _file_row(group, victim_file_id)
            victim_source_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_victim_object_before",
                    str(victim_file.get("parent_id") or ""),
                    str(victim_file.get("location") or ""),
                )
                if victim_file
                else {}
            )
            victim_commit_content = f"{prefix}-{group}-tenant-b-commit"
            victim_commit_hash = hashlib.sha256(victim_commit_content.encode()).hexdigest()
            victim_commit = (
                _create_commit(
                    case_id,
                    group,
                    "create_tenant_b_file_commit",
                    secondary_owner["auth"],
                    victim_folder_id,
                    f"{prefix} tenant b commit",
                    [
                        {
                            "file_id": victim_file_id,
                            "file_name": victim_file.get("name", ""),
                            "operation": "add",
                            "content": victim_commit_content,
                        }
                    ],
                )
                if victim_file_id
                else {}
            )
            victim_commit_id = str(((victim_commit.get("data") or {}) if isinstance(victim_commit.get("data"), dict) else {}).get("id") or "")
            victim_commit_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_commit_object_before",
                    victim_folder_id,
                    f".objects/{victim_commit_hash}",
                )
                if victim_folder_id
                else {}
            )

            source_upload = _upload(
                case_id,
                group,
                owner,
                "upload_tenant_a_move_sources",
                [
                    (f"{prefix}-{group}-tenant-a-source-folder.txt", b"tenant-a-source-folder", "text/plain"),
                    (f"{prefix}-{group}-tenant-a-source-file.txt", b"tenant-a-source-file", "text/plain"),
                ],
                parent_id=root_id,
            )
            source_items = source_upload.get("data") if isinstance(source_upload.get("data"), list) else []
            source_ids = [str(item.get("id") or "") for item in source_items]
            source_before = {file_id: _file_row(group, file_id) for file_id in source_ids}
            source_object_addresses: set[tuple[str, str]] = {(str(row.get("parent_id") or ""), str(row.get("location") or "")) for row in source_before.values() if row}
            source_objects_before = {
                file_id: _storage_snapshot(
                    case_id,
                    group,
                    f"read_tenant_a_source_object_before_{index + 1}",
                    str(source_before[file_id].get("parent_id") or ""),
                    str(source_before[file_id].get("location") or ""),
                )
                for index, file_id in enumerate(source_ids)
                if source_before.get(file_id)
            }

            dataset = (
                DD._create_dataset(
                    case_id,
                    group,
                    secondary_owner["auth"],
                    "create_tenant_b_commit_dataset",
                    {"name": dataset_name},
                )
                if secondary_ready
                else {}
            )
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            dataset_upload = (
                DD._upload_local_documents(
                    case_id,
                    group,
                    secondary_owner["auth"],
                    "upload_tenant_b_commit_dataset_document",
                    dataset_id,
                    [(f"{prefix}-{group}-tenant-b-dataset.txt", b"tenant-b-dataset-source", "text/plain")],
                )
                if dataset_id
                else {}
            )
            dataset_items = DD._uploaded_items([dataset_upload]) if dataset_upload else []
            dataset_document_ids = [str(item.get("id") or "") for item in dataset_items if item.get("id")]
            dataset_relations = _file_document_rows(group, dataset_id=dataset_id) if dataset_id else []
            dataset_file_id = dataset_relations[0]["file_id"] if len(dataset_relations) == 1 else ""
            dataset_file = _file_row(group, dataset_file_id)
            dataset_folder_id = str(dataset_file.get("parent_id") or "")
            dataset_source_location = dataset_relations[0]["location"] if dataset_relations else ""
            dataset_source_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_dataset_source_before",
                    dataset_id,
                    dataset_source_location,
                )
                if dataset_id and dataset_source_location
                else {}
            )
            dataset_commit_content = f"{prefix}-{group}-tenant-b-dataset-commit"
            dataset_commit_hash = hashlib.sha256(dataset_commit_content.encode()).hexdigest()
            dataset_commit = (
                _create_commit(
                    case_id,
                    group,
                    "create_tenant_b_dataset_commit",
                    secondary_owner["auth"],
                    dataset_folder_id,
                    f"{prefix} tenant b dataset commit",
                    [
                        {
                            "file_id": dataset_file_id,
                            "file_name": dataset_file.get("name", ""),
                            "operation": "add",
                            "content": dataset_commit_content,
                        }
                    ],
                )
                if dataset_file_id
                else {}
            )
            dataset_commit_id = str(((dataset_commit.get("data") or {}) if isinstance(dataset_commit.get("data"), dict) else {}).get("id") or "")
            dataset_commit_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_dataset_commit_object_before",
                    dataset_folder_id,
                    f".objects/{dataset_commit_hash}",
                )
                if dataset_folder_id
                else {}
            )

            def denied_response(response: dict[str, Any]) -> bool:
                return response.get("http_status") in {401, 403} or (response.get("http_status") == 200 and response.get("code") not in {None, 0} and _response_has_no_data(response))

            parent_read = _http(case_id, group, "tenant_a_read_tenant_b_parent", owner["auth"], "GET", f"/files/{victim_file_id}/parent")
            folder_list = _http(
                case_id,
                group,
                "tenant_a_list_tenant_b_folder",
                owner["auth"],
                "GET",
                "/files",
                params={"parent_id": victim_folder_id, "page": 1, "page_size": 100},
            )

            before_create_ids = {row["id"] for row in _file_rows(group, tenant_id=owner["tenant_id"])}
            foreign_create = _create_folder(
                case_id,
                group,
                owner,
                "tenant_a_create_under_tenant_b_folder",
                f"{prefix}-{group}-injected-folder",
                parent_id=victim_folder_id,
            )
            after_create_rows = _file_rows(group, tenant_id=owner["tenant_id"])
            create_new_ids = {row["id"] for row in after_create_rows} - before_create_ids

            before_upload_ids = {row["id"] for row in after_create_rows}
            foreign_upload = _upload(
                case_id,
                group,
                owner,
                "tenant_a_upload_under_tenant_b_folder",
                [(f"{prefix}-{group}-injected-upload.txt", b"injected-upload", "text/plain")],
                parent_id=victim_folder_id,
            )
            after_upload_rows = _file_rows(group, tenant_id=owner["tenant_id"])
            upload_new_ids = {row["id"] for row in after_upload_rows} - before_upload_ids

            move_folder_before = _file_row(group, source_ids[0]) if len(source_ids) >= 1 else {}
            move_to_folder = (
                _move_files(
                    case_id,
                    group,
                    owner,
                    "tenant_a_move_file_to_tenant_b_folder",
                    [source_ids[0]],
                    dest_file_id=victim_folder_id,
                )
                if len(source_ids) >= 1
                else {}
            )
            move_folder_after = _file_row(group, source_ids[0]) if len(source_ids) >= 1 else {}
            move_file_before = _file_row(group, source_ids[1]) if len(source_ids) >= 2 else {}
            move_to_file = (
                _move_files(
                    case_id,
                    group,
                    owner,
                    "tenant_a_move_file_to_tenant_b_file",
                    [source_ids[1]],
                    dest_file_id=victim_file_id,
                )
                if len(source_ids) >= 2
                else {}
            )
            move_file_after = _file_row(group, source_ids[1]) if len(source_ids) >= 2 else {}
            for row in (move_folder_after, move_file_after):
                if row:
                    source_object_addresses.add((str(row.get("parent_id") or ""), str(row.get("location") or "")))

            versions_read = _commit_request(case_id, group, "tenant_a_read_tenant_b_versions", owner["auth"], "GET", f"/files/{victim_file_id}/versions")
            commit_detail_read = _commit_request(case_id, group, "tenant_a_read_tenant_b_commit_detail", owner["auth"], "GET", f"/folders/{victim_folder_id}/commits/{victim_commit_id}")
            folder_commits_read = _commit_request(
                case_id, group, "tenant_a_read_tenant_b_folder_commits", owner["auth"], "GET", f"/folders/{victim_folder_id}/commits", params={"page": 1, "page_size": 10}
            )
            dataset_commits_read = _commit_request(
                case_id, group, "tenant_a_read_tenant_b_dataset_commits", owner["auth"], "GET", f"/datasets/{dataset_id}/commits", params={"page": 1, "page_size": 10}
            )

            create_denied = denied_response(foreign_create) and not create_new_ids
            upload_denied = denied_response(foreign_upload) and not upload_new_ids
            move_folder_denied = denied_response(move_to_folder) and move_folder_before == move_folder_after
            move_file_denied = denied_response(move_to_file) and move_file_before == move_file_after
            denial_checks = [
                ("foreign_parent_query", denied_response(parent_read) and parent_read.get("message") == "No authorization.", "FM-CROSS-TENANT-PARENT-READ-001", "api/apps/restful_apis/file_api.py"),
                ("foreign_folder_list", denied_response(folder_list), "FM-CROSS-TENANT-FOLDER-LIST-001", "api/apps/restful_apis/file_api.py"),
                ("foreign_parent_create", create_denied, "FM-CROSS-TENANT-PARENT-CREATE-001", "api/apps/restful_apis/file_api.py"),
                ("foreign_parent_upload", upload_denied, "FM-CROSS-TENANT-PARENT-UPLOAD-001", "api/apps/restful_apis/file_api.py"),
                ("foreign_folder_move", move_folder_denied, "FM-CROSS-TENANT-DESTINATION-MOVE-001", "api/apps/restful_apis/file_api.py"),
                ("foreign_file_move", move_file_denied, "FM-CROSS-TENANT-FILE-AS-DESTINATION-001", "api/apps/restful_apis/file_api.py"),
                (
                    "foreign_commit_and_versions_read",
                    denied_response(versions_read) and denied_response(commit_detail_read) and denied_response(folder_commits_read),
                    "FM-CROSS-TENANT-COMMIT-VERSION-READ-001",
                    "api/apps/restful_apis/file_commit_api.py",
                ),
                ("foreign_dataset_commit_read", denied_response(dataset_commits_read), "FM-CROSS-TENANT-DATASET-COMMIT-READ-001", "api/apps/restful_apis/file_commit_api.py"),
            ]

            attacker_rows_before_delete = {
                row["id"]: row
                for row in _file_rows(group, tenant_id=owner["tenant_id"])
                if row.get("parent_id") in {victim_folder_id, victim_file_id} or row["id"] in create_new_ids or row["id"] in upload_new_ids
            }
            attacker_objects_before_delete = {
                file_id: _storage_snapshot(
                    case_id,
                    group,
                    f"read_injected_object_before_victim_delete_{index + 1}",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                for index, (file_id, row) in enumerate(attacker_rows_before_delete.items())
                if row.get("type") != "folder" and row.get("location")
            }
            victim_delete = _delete_files(
                case_id,
                group,
                secondary_owner,
                "tenant_b_delete_own_folder_with_injected_children",
                [victim_folder_id],
            )
            attacker_rows_after_delete = {file_id: _file_row(group, file_id) for file_id in attacker_rows_before_delete}
            attacker_objects_after_delete = {
                file_id: _storage_snapshot(
                    case_id,
                    group,
                    f"read_injected_object_after_victim_delete_{index + 1}",
                    str(attacker_rows_before_delete[file_id].get("parent_id") or ""),
                    str(attacker_rows_before_delete[file_id].get("location") or ""),
                )
                for index, file_id in enumerate(attacker_objects_before_delete)
            }
            attacker_rows_preserved = all(attacker_rows_after_delete[file_id] == before for file_id, before in attacker_rows_before_delete.items())
            attacker_objects_preserved = all(
                (
                    attacker_objects_after_delete[file_id].get("exists"),
                    attacker_objects_after_delete[file_id].get("length"),
                    attacker_objects_after_delete[file_id].get("sha256"),
                )
                == (
                    before.get("exists"),
                    before.get("length"),
                    before.get("sha256"),
                )
                for file_id, before in attacker_objects_before_delete.items()
            )

            document_cleanup = (
                _delete_dataset_documents(
                    case_id,
                    group,
                    secondary_owner["auth"],
                    "cleanup_tenant_b_dataset_documents",
                    dataset_id,
                    dataset_document_ids,
                )
                if dataset_document_ids
                else {"http_status": 200, "code": 0}
            )
            dataset_cleanup = (
                DD._delete_ids(
                    case_id,
                    group,
                    secondary_owner["auth"],
                    "cleanup_tenant_b_dataset",
                    [dataset_id],
                )
                if dataset_id
                else {"http_status": 200, "code": 0}
            )
            primary_file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            secondary_file_cleanup = _cleanup_file_prefix(case_id, group, secondary_owner, prefix)
            source_objects_after_cleanup = [
                _storage_snapshot(
                    case_id,
                    group,
                    f"read_tenant_a_source_object_after_cleanup_{index + 1}",
                    parent_id,
                    location,
                )
                for index, (parent_id, location) in enumerate(sorted(source_object_addresses))
            ]
            victim_source_after_cleanup = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_victim_object_after_cleanup",
                    str(victim_file.get("parent_id") or ""),
                    str(victim_file.get("location") or ""),
                )
                if victim_file
                else {}
            )
            victim_commit_after_cleanup = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_commit_object_after_cleanup",
                    victim_folder_id,
                    f".objects/{victim_commit_hash}",
                )
                if victim_folder_id
                else {}
            )
            dataset_source_after_cleanup = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_tenant_b_dataset_source_after_cleanup",
                    dataset_id,
                    dataset_source_location,
                )
                if dataset_id and dataset_source_location
                else {}
            )
            dataset_commit_after_cleanup = (
                _storage_snapshot(
                    case_id,
                    group,
                    "observe_retained_tenant_b_dataset_commit_object",
                    dataset_folder_id,
                    f".objects/{dataset_commit_hash}",
                )
                if dataset_folder_id
                else {}
            )
            artifact_counts = _saved_artifact_counts(group, [dataset_file_id], dataset_document_ids)
            user_cleanup = _cleanup_secondary_user(case_id, group, secondary["email"])
            active_cleanup = (
                primary_file_cleanup
                and secondary_file_cleanup
                and _response_pair(document_cleanup) == [200, 0]
                and _response_pair(dataset_cleanup) == [200, 0]
                and DD._dataset_count_by_name(group, secondary_owner["tenant_id"], dataset_name) == 0
                and all(value == 0 for value in artifact_counts.values())
                and all(item.get("exists") is False for item in source_objects_after_cleanup)
                and victim_source_after_cleanup.get("exists") is False
                and victim_commit_after_cleanup.get("exists") is False
                and dataset_source_after_cleanup.get("exists") is False
                and user_cleanup
            )
            observed = {
                "denial_results": [item[1] for item in denial_checks],
                "victim_delete_response": _response_pair(victim_delete),
                "attacker_rows_preserved": attacker_rows_preserved,
                "attacker_objects_preserved": attacker_objects_preserved,
                "active_cleanup_succeeded": active_cleanup,
            }
            setup_ready = (
                preclean
                and secondary_ready
                and secondary_file_preclean
                and dataset_preclean
                and _response_pair(victim_folder) == [200, 0]
                and _response_pair(victim_upload) == [200, 0]
                and victim_source_object.get("sha256") == hashlib.sha256(victim_content).hexdigest()
                and _response_pair(victim_commit) == [200, 0]
                and victim_commit_object.get("exists") is True
                and _response_pair(source_upload) == [200, 0]
                and len(source_ids) == 2
                and all(item.get("exists") is True for item in source_objects_before.values())
                and _response_pair(dataset) == [200, 0]
                and len(dataset_document_ids) == 1
                and len(dataset_relations) == 1
                and dataset_source_object.get("exists") is True
                and _response_pair(dataset_commit) == [200, 0]
                and bool(dataset_commit_id)
                and dataset_commit_object.get("exists") is True
            )
            passed = setup_ready and cross_tenant_matrix_contract_ok(observed)
            findings = [
                {
                    "id": finding_id,
                    "summary": f"{group} {name.replace('_', ' ')} was not rejected without mutation",
                    "code_location": location,
                }
                for name, ok, finding_id, location in denial_checks
                if not ok
            ]
            if not attacker_rows_preserved or not attacker_objects_preserved:
                findings.append(
                    {
                        "id": "FM-CROSS-TENANT-RECURSIVE-DELETE-001",
                        "summary": f"{group} victim recursive deletion removed attacker-owned injected rows or objects",
                        "code_location": "api/apps/services/file_api_service.py",
                    }
                )
            if not setup_ready or not active_cleanup:
                findings.append(
                    {
                        "id": "FM-CROSS-TENANT-FIXTURE-LIFECYCLE-001",
                        "summary": f"{group} dedicated cross-tenant fixture setup or API cleanup contract differed",
                        "code_location": "docs/administrator/configurations/gaussdb_test_plan_execute/fresh_08_file.py",
                    }
                )
            return {
                "status": "PASS" if passed else "FAIL",
                "steps": [
                    {
                        "name": "create_independent_tenant_a_b_file_commit_and_dataset_fixtures",
                        "tenant_ids_distinct": owner["tenant_id"] != secondary_owner["tenant_id"],
                        "victim_folder_response": _response_pair(victim_folder),
                        "victim_upload_response": _response_pair(victim_upload),
                        "victim_commit_response": _response_pair(victim_commit),
                        "source_upload_response": _response_pair(source_upload),
                        "dataset_response": _response_pair(dataset),
                        "dataset_document_count": len(dataset_document_ids),
                        "dataset_commit_response": _response_pair(dataset_commit),
                        "setup_ready": setup_ready,
                        "raw_sha256": [
                            victim_folder.get("raw_sha256"),
                            victim_upload.get("raw_sha256"),
                            victim_commit.get("raw_sha256"),
                            source_upload.get("raw_sha256"),
                            dataset.get("raw_sha256"),
                            dataset_upload.get("raw_sha256"),
                            dataset_commit.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "exercise_eight_cross_tenant_access_surfaces",
                        "checks": [{"surface": name, "denied_without_mutation": ok} for name, ok, _finding_id, _location in denial_checks],
                        "responses": {
                            "parent": _response_pair(parent_read),
                            "folder_list": _response_pair(folder_list),
                            "create": _response_pair(foreign_create),
                            "upload": _response_pair(foreign_upload),
                            "move_folder": _response_pair(move_to_folder),
                            "move_file": _response_pair(move_to_file),
                            "versions": _response_pair(versions_read),
                            "commit_detail": _response_pair(commit_detail_read),
                            "folder_commits": _response_pair(folder_commits_read),
                            "dataset_commits": _response_pair(dataset_commits_read),
                        },
                        "created_injected_row_count": len(create_new_ids | upload_new_ids),
                        "raw_sha256": [
                            parent_read.get("raw_sha256"),
                            folder_list.get("raw_sha256"),
                            foreign_create.get("raw_sha256"),
                            foreign_upload.get("raw_sha256"),
                            move_to_folder.get("raw_sha256"),
                            move_to_file.get("raw_sha256"),
                            versions_read.get("raw_sha256"),
                            commit_detail_read.get("raw_sha256"),
                            folder_commits_read.get("raw_sha256"),
                            dataset_commits_read.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "delete_victim_folder_and_preserve_attacker_owned_injected_graph",
                        "response": _response_pair(victim_delete),
                        "attacker_row_count": len(attacker_rows_before_delete),
                        "attacker_rows_preserved": attacker_rows_preserved,
                        "attacker_objects_preserved": attacker_objects_preserved,
                        "raw_sha256": [victim_delete.get("raw_sha256"), *[item.get("raw_sha256") for item in attacker_objects_after_delete.values()]],
                    },
                    {
                        "name": "cleanup_all_active_file_dataset_document_object_and_user_fixtures",
                        "document_delete_response": _response_pair(document_cleanup),
                        "dataset_delete_response": _response_pair(dataset_cleanup),
                        "artifact_counts": artifact_counts,
                        "active_cleanup_succeeded": active_cleanup,
                        "retained_dataset_commit_object_without_delete_api": dataset_commit_after_cleanup.get("exists") is True,
                        "retained_commit_rows_without_delete_api": len(_commit_rows(group, folder_id=victim_folder_id)) + len(_commit_rows(group, folder_id=dataset_folder_id)),
                        "raw_sha256": [
                            document_cleanup.get("raw_sha256"),
                            dataset_cleanup.get("raw_sha256"),
                            dataset_source_after_cleanup.get("raw_sha256"),
                            dataset_commit_after_cleanup.get("raw_sha256"),
                        ],
                    },
                ],
                "oracle": {
                    "all_eight_cross_tenant_surfaces": "authorization rejection with zero mutation/data",
                    "recursive_delete": "attacker-owned injected graph preserved",
                    "cleanup": "active fixtures absent; commit history/object without public delete API observed only",
                },
                "findings": findings,
            }

        if number in {67, 68}:
            name = f"{prefix}-{group}-source-empty"
            created = _create_folder(case_id, group, owner, "create_default_source_type_folder", name, parent_id=root_id)
            file_id = str((created.get("data") or {}).get("id") or "")
            physical = _file_row(group, file_id)
            orm_source = _orm_source_type(group, file_id) if file_id else None
            empty_query = _orm_empty_source_query(group, file_id) if file_id else {"ids": [], "source_types": []}
            listed = _http(
                case_id,
                group,
                "list_default_source_type_folder",
                owner["auth"],
                "GET",
                "/files",
                params={"parent_id": root_id, "page": 1, "page_size": 100},
            )
            files = ((listed.get("data") or {}).get("files") or []) if isinstance(listed.get("data"), dict) else []
            matches = [item for item in files if str(item.get("id") or "") == file_id]
            api_source = matches[0].get("source_type") if len(matches) == 1 else None
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "api_source_type": api_source,
                "orm_source_type": orm_source,
                "orm_query_hit": empty_query.get("ids") == [file_id] and empty_query.get("source_types") == [""],
                "physical_is_empty": physical.get("source_type") == "",
                "physical_is_null": physical.get("source_type") is None,
                "cleanup_succeeded": cleanup,
            }
            passed = preclean and _response_pair(created) == [200, 0] and _response_pair(listed) == [200, 0] and source_type_empty_contract_ok(group, observed)
            return _result(
                passed,
                [
                    {
                        "name": "create_file_without_source_type_and_read_physical_orm_api_values",
                        "create_response": _response_pair(created),
                        "list_response": _response_pair(listed),
                        **observed,
                        "raw_sha256": [created.get("raw_sha256"), listed.get("raw_sha256")],
                    },
                    {
                        "name": "query_file_by_application_empty_source_type",
                        "query_ids": empty_query.get("ids"),
                        "query_source_types": empty_query.get("source_types"),
                        "expected_physical_predicate": "source_type=''" if group == "control" else "source_type IS NULL",
                    },
                ],
                {"application": "source_type empty string", "control_physical": "empty string", "experiment_physical": "NULL", "empty_query": "one exact hit"},
                "FM-SOURCE-TYPE-DEFAULT-001" if number == 67 else "FM-SOURCE-TYPE-EMPTY-QUERY-001",
                f"{group} File.source_type default/query empty-string compatibility contract differed",
            )

        if number == 69:
            original_name = f"{prefix}-{group}-source-update"
            renamed_name = f"{prefix}-{group}-source-update-renamed"
            created = _create_folder(case_id, group, owner, "create_source_update_fixture", original_name, parent_id=root_id)
            file_id = str((created.get("data") or {}).get("id") or "")
            nonempty_update = _orm_update_source_type(group, file_id, "field-test") if file_id else {}
            nonempty_physical = _file_row(group, file_id)
            empty_update = _orm_update_source_type(group, file_id, "") if file_id else {}
            empty_physical = _file_row(group, file_id)
            renamed = _move_files(case_id, group, owner, "rename_after_empty_source_update", [file_id], new_name=renamed_name) if file_id else {}
            after_rename = _file_row(group, file_id)
            after_orm = _orm_source_type(group, file_id) if file_id else None
            empty_query = _orm_empty_source_query(group, file_id) if file_id else {"ids": []}
            listed = _http(
                case_id,
                group,
                "list_after_empty_source_update",
                owner["auth"],
                "GET",
                "/files",
                params={"parent_id": root_id, "page": 1, "page_size": 100},
            )
            files = ((listed.get("data") or {}).get("files") or []) if isinstance(listed.get("data"), dict) else []
            matches = [item for item in files if str(item.get("id") or "") == file_id]
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "api_source_type": matches[0].get("source_type") if len(matches) == 1 else None,
                "orm_source_type": after_orm,
                "orm_query_hit": empty_query.get("ids") == [file_id],
                "physical_is_empty": after_rename.get("source_type") == "",
                "physical_is_null": after_rename.get("source_type") is None,
                "cleanup_succeeded": cleanup,
            }
            passed = (
                preclean
                and _response_pair(created) == [200, 0]
                and nonempty_update.get("updated") == 1
                and nonempty_update.get("source_type") == "field-test"
                and nonempty_physical.get("source_type") == "field-test"
                and empty_update.get("updated") == 1
                and empty_update.get("source_type") == ""
                and empty_update.get("empty_query_ids") == [file_id]
                and _response_pair(renamed) == [200, 0]
                and after_rename.get("name") == renamed_name
                and source_type_empty_contract_ok(group, observed)
            )
            return _result(
                passed,
                [
                    {
                        "name": "set_field_test_then_update_source_type_to_application_empty",
                        "initial_update_count": nonempty_update.get("updated"),
                        "initial_physical_source_type": nonempty_physical.get("source_type"),
                        "empty_update_count": empty_update.get("updated"),
                        "empty_orm_source_type": empty_update.get("source_type"),
                        "empty_query_hit": empty_update.get("empty_query_ids") == [file_id],
                        "physical_is_empty": empty_physical.get("source_type") == "",
                        "physical_is_null": empty_physical.get("source_type") is None,
                    },
                    {
                        "name": "rename_other_field_and_preserve_application_empty_source_type",
                        "rename_response": _response_pair(renamed),
                        "name_matches": after_rename.get("name") == renamed_name,
                        **observed,
                        "raw_sha256": [created.get("raw_sha256"), renamed.get("raw_sha256"), listed.get("raw_sha256")],
                    },
                ],
                {
                    "authorized_fixture_exception": "FileService.update_by_id source_type only",
                    "control_physical": "empty string",
                    "experiment_physical": "NULL",
                    "rename": "source_type remains application empty",
                },
                "FM-SOURCE-TYPE-EMPTY-UPDATE-001",
                f"{group} File.source_type ORM empty update or later API preservation contract differed",
            )

        if number == 70:
            dataset_name = f"{prefix}-{group}-knowledgebase"
            dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], dataset_name)
            dataset = DD._create_dataset(case_id, group, owner["auth"], "create_source_type_dataset", {"name": dataset_name})
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            source_content = b"knowledgebase source type fixture"
            uploaded = (
                DD._upload_local_documents(
                    case_id,
                    group,
                    owner["auth"],
                    "upload_source_type_dataset_document",
                    dataset_id,
                    [(f"{prefix}-{group}-knowledgebase.txt", source_content, "text/plain")],
                )
                if dataset_id
                else {}
            )
            items = DD._uploaded_items([uploaded]) if uploaded else []
            document_ids = [str(item.get("id") or "") for item in items if item.get("id")]
            relations = _file_document_rows(group, dataset_id=dataset_id) if dataset_id else []
            file_id = relations[0]["file_id"] if len(relations) == 1 else ""
            file_before = _file_row(group, file_id)
            folder_id = str(file_before.get("parent_id") or "")
            folder_before = _file_row(group, folder_id)
            orm_source = _orm_source_type(group, file_id) if file_id else None
            location = relations[0]["location"] if relations else ""
            object_before = _storage_snapshot(case_id, group, "read_knowledgebase_source_object_before", dataset_id, location) if dataset_id and location else {}
            listed = (
                _http(
                    case_id,
                    group,
                    "list_knowledgebase_source_folder",
                    owner["auth"],
                    "GET",
                    "/files",
                    params={"parent_id": folder_id, "page": 1, "page_size": 100},
                )
                if folder_id
                else {}
            )
            files = ((listed.get("data") or {}).get("files") or []) if isinstance(listed.get("data"), dict) else []
            matches = [item for item in files if str(item.get("id") or "") == file_id]
            protected_delete = _delete_files(case_id, group, owner, "attempt_delete_knowledgebase_source_file", [file_id]) if file_id else {}
            file_after_delete = _file_row(group, file_id)
            relations_after_delete = _file_document_rows(group, file_ids=[file_id]) if file_id else []
            object_after_delete = _storage_snapshot(case_id, group, "read_knowledgebase_source_object_after_protected_delete", dataset_id, location) if dataset_id and location else {}
            document_cleanup = _delete_dataset_documents(case_id, group, owner["auth"], "cleanup_source_type_document", dataset_id, document_ids) if document_ids else {"http_status": 200, "code": 0}
            dataset_cleanup = DD._delete_ids(case_id, group, owner["auth"], "cleanup_source_type_dataset", [dataset_id]) if dataset_id else {"http_status": 200, "code": 0}
            artifacts = _saved_artifact_counts(group, [file_id], document_ids)
            object_after_cleanup = _storage_snapshot(case_id, group, "read_knowledgebase_source_object_after_cleanup", dataset_id, location) if dataset_id and location else {}
            prefix_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            cleanup = (
                _response_pair(document_cleanup) == [200, 0]
                and _response_pair(dataset_cleanup) == [200, 0]
                and DD._dataset_count_by_name(group, owner["tenant_id"], dataset_name) == 0
                and all(value == 0 for value in artifacts.values())
                and object_after_cleanup.get("exists") is False
                and prefix_cleanup
            )
            delete_data = protected_delete.get("data") if isinstance(protected_delete.get("data"), dict) else {}
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and len(document_ids) == 1
                and len(relations) == 1
                and file_before.get("source_type") == "knowledgebase"
                and folder_before.get("source_type") == "knowledgebase"
                and orm_source == "knowledgebase"
                and len(matches) == 1
                and matches[0].get("source_type") == "knowledgebase"
                and object_before.get("sha256") == hashlib.sha256(source_content).hexdigest()
                and _response_pair(protected_delete) == [200, 0]
                and delete_data.get("success_count") == 0
                and file_after_delete == file_before
                and relations_after_delete == relations
                and object_after_delete.get("sha256") == object_before.get("sha256")
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "upload_dataset_document_and_verify_knowledgebase_source_marker",
                        "dataset_response": _response_pair(dataset),
                        "document_count": len(document_ids),
                        "relation_count": len(relations),
                        "file_physical_source_type": file_before.get("source_type"),
                        "folder_physical_source_type": folder_before.get("source_type"),
                        "orm_source_type": orm_source,
                        "api_source_type": matches[0].get("source_type") if len(matches) == 1 else None,
                        "object_sha_matches": object_before.get("sha256") == hashlib.sha256(source_content).hexdigest(),
                        "raw_sha256": [dataset.get("raw_sha256"), uploaded.get("raw_sha256"), listed.get("raw_sha256"), object_before.get("raw_sha256")],
                    },
                    {
                        "name": "skip_direct_delete_for_knowledgebase_source",
                        "response": _response_pair(protected_delete),
                        "success_count": delete_data.get("success_count"),
                        "file_unchanged": file_after_delete == file_before,
                        "relation_unchanged": relations_after_delete == relations,
                        "object_hash_unchanged": object_after_delete.get("sha256") == object_before.get("sha256"),
                        "raw_sha256": [protected_delete.get("raw_sha256"), object_after_delete.get("raw_sha256")],
                    },
                    {
                        "name": "cleanup_document_then_dataset_through_public_apis",
                        "document_delete_response": _response_pair(document_cleanup),
                        "dataset_delete_response": _response_pair(dataset_cleanup),
                        "artifact_counts": artifacts,
                        "object_absent": object_after_cleanup.get("exists") is False,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [document_cleanup.get("raw_sha256"), dataset_cleanup.get("raw_sha256"), object_after_cleanup.get("raw_sha256")],
                    },
                ],
                {"source_type": "knowledgebase at physical/ORM/API layers", "direct_file_delete": "skip with success_count=0", "cleanup": "Document API before Dataset API"},
                "FM-SOURCE-TYPE-KNOWLEDGEBASE-001",
                f"{group} knowledgebase source marker, delete protection, or API cleanup contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_file_del_supplement(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-file-del-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        root = _root(case_id, group, owner)
        root_id = str(root["row"]["id"])

        if number == 1:
            folder_a = _create_folder(case_id, group, owner, "create_folder_a", f"{prefix}-{group}-a", parent_id=root_id)
            folder_a_id = str((folder_a.get("data") or {}).get("id") or "")
            folder_c = _create_folder(case_id, group, owner, "create_subfolder_c", f"{prefix}-{group}-c", parent_id=folder_a_id)
            folder_c_id = str((folder_c.get("data") or {}).get("id") or "")
            contents = [b"recursive-delete-b", b"recursive-delete-d"]
            upload_b = _upload(case_id, group, owner, "upload_file_b", [(f"{prefix}-{group}-b.txt", contents[0], "text/plain")], parent_id=folder_a_id)
            upload_d = _upload(case_id, group, owner, "upload_file_d", [(f"{prefix}-{group}-d.txt", contents[1], "text/plain")], parent_id=folder_c_id)
            file_ids = [str(((response.get("data") or [{}])[0]).get("id") or "") for response in (upload_b, upload_d)]
            all_ids = [folder_a_id, file_ids[0], folder_c_id, file_ids[1]]
            file_rows = [_file_row(group, file_id) for file_id in file_ids]
            objects_before = [
                _storage_snapshot(case_id, group, f"read_recursive_object_before_{index + 1}", str(row.get("parent_id") or ""), str(row.get("location") or "")) for index, row in enumerate(file_rows)
            ]
            deleted = _delete_files(case_id, group, owner, "delete_folder_a_recursively", [folder_a_id])
            artifacts = _saved_artifact_counts(group, file_ids, [])
            rows_remaining = len(_file_rows(group, ids=all_ids))
            objects_after = [
                _storage_snapshot(case_id, group, f"read_recursive_object_after_{index + 1}", str(row.get("parent_id") or ""), str(row.get("location") or "")) for index, row in enumerate(file_rows)
            ]
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            data = deleted.get("data") if isinstance(deleted.get("data"), dict) else {}
            passed = (
                preclean
                and all(_response_pair(response) == [200, 0] for response in (folder_a, folder_c, upload_b, upload_d))
                and len(set(all_ids)) == 4
                and all(item.get("exists") is True for item in objects_before)
                and _response_pair(deleted) == [200, 0]
                and data.get("success_count") == 4
                and rows_remaining == 0
                and artifacts["file_links"] == 0
                and all(item.get("exists") is False for item in objects_after)
                and cleanup
            )
            return _result(
                passed,
                [
                    {"name": "create_a_b_c_d_recursive_fixture", "unique_id_count": len(set(all_ids)), "objects_exist": all(item.get("exists") is True for item in objects_before)},
                    {
                        "name": "delete_folder_a_and_verify_saved_descendants",
                        "response": _response_pair(deleted),
                        "success_count": data.get("success_count"),
                        "file_rows_remaining": rows_remaining,
                        "file_links_remaining": artifacts["file_links"],
                        "objects_remaining": sum(item.get("exists") is True for item in objects_after),
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [deleted.get("raw_sha256"), *[item.get("raw_sha256") for item in objects_after]],
                    },
                ],
                {"response": [200, 0], "success_count": 4, "saved_file_rows_links_objects": "absent"},
                "FM-DELETE-SUP-RECURSIVE-001",
                f"{group} supplement recursive folder deletion contract differed",
            )

        if number == 2:
            dataset_prefix = f"{prefix}-{group}-dataset"
            dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], dataset_prefix)
            dataset = DD._create_dataset(case_id, group, owner["auth"], "create_delete_link_dataset", {"name": dataset_prefix})
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            content = b"supplement linked delete"
            upload = _upload(case_id, group, owner, "upload_linked_delete_file", [(f"{prefix}-{group}.txt", content, "text/plain")])
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            file_row = _file_row(group, file_id)
            link = _http(case_id, group, "link_file_before_delete", owner["auth"], "POST", "/files/link-to-datasets", payload={"file_ids": [file_id], "kb_ids": [dataset_id]})
            relations = _wait_file_documents(group, [file_id], dataset_id, expected_count=1)
            document_ids = [row["document_id"] for row in relations]
            task_counts = DD._document_task_counts(group, document_ids)
            index_before = DD._docstore_index_artifact_snapshot(group, owner["tenant_id"], dataset_id)
            artifacts_before = _saved_artifact_counts(group, [file_id], document_ids)
            object_before = _storage_snapshot(case_id, group, "read_linked_delete_object_before", str(file_row.get("parent_id") or ""), str(file_row.get("location") or "")) if file_row else {}
            deleted = _delete_files(case_id, group, owner, "delete_file_with_linked_document", [file_id])
            artifacts_after = _saved_artifact_counts(group, [file_id], document_ids)
            index_after = DD._docstore_index_artifact_snapshot(group, owner["tenant_id"], dataset_id)
            object_after = _storage_snapshot(case_id, group, "read_linked_delete_object_after", str(file_row.get("parent_id") or ""), str(file_row.get("location") or "")) if file_row else {}
            dataset_cleanup = DD._delete_ids(case_id, group, owner["auth"], "cleanup_delete_link_dataset", [dataset_id]) if dataset_id else {"http_status": 200, "code": 0}
            cleanup = _response_pair(dataset_cleanup) == [200, 0] and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix) and _cleanup_file_prefix(case_id, group, owner, prefix)
            data = deleted.get("data") if isinstance(deleted.get("data"), dict) else {}
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(dataset) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and _response_pair(link) == [200, 0]
                and link.get("data") is True
                and len(relations) == 1
                and artifacts_before == {"files": 1, "file_links": 1, "documents": 1, "tasks": sum(task_counts.values())}
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and _response_pair(deleted) == [200, 0]
                and data.get("success_count") == 1
                and all(value == 0 for value in artifacts_after.values())
                and int(index_before.get("total") or 0) == int(index_after.get("total") or 0) == 0
                and object_after.get("exists") is False
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "link_file_and_save_document_task_docengine_object_ids",
                        "link_response": _response_pair(link),
                        "document_count": len(document_ids),
                        "task_count": sum(task_counts.values()),
                        "docengine_artifact_count": int(index_before.get("total") or 0),
                        "artifact_counts": artifacts_before,
                        "object_exists": object_before.get("exists"),
                    },
                    {
                        "name": "delete_file_and_verify_saved_cascade_artifacts",
                        "response": _response_pair(deleted),
                        "success_count": data.get("success_count"),
                        "artifact_counts": artifacts_after,
                        "docengine_artifact_count": int(index_after.get("total") or 0),
                        "object_absent": object_after.get("exists") is False,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [deleted.get("raw_sha256"), object_after.get("raw_sha256"), dataset_cleanup.get("raw_sha256")],
                    },
                ],
                {"response": [200, 0], "saved_file_relation_document_task_docengine_object": "absent"},
                "FM-DELETE-SUP-LINKED-DOCUMENT-001",
                f"{group} supplement linked file cascade deletion contract differed",
            )

        if number == 3:
            folder = _create_folder(case_id, group, owner, "create_storage_folder", f"{prefix}-{group}-folder", parent_id=root_id)
            folder_id = str((folder.get("data") or {}).get("id") or "")
            content = b"folder storage namespace cleanup"
            upload = _upload(case_id, group, owner, "upload_folder_storage_object", [(f"{prefix}-{group}.bin", content, "application/octet-stream")], parent_id=folder_id)
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            row = _file_row(group, file_id)
            namespace_before = _storage_namespace_snapshot(case_id, group, "read_folder_namespace_before", folder_id) if folder_id else {}
            object_before = _storage_snapshot(case_id, group, "read_folder_object_before", folder_id, str(row.get("location") or "")) if row else {}
            deleted = _delete_files(case_id, group, owner, "delete_folder_and_storage_namespace", [folder_id])
            namespace_after = _storage_namespace_snapshot(case_id, group, "read_folder_namespace_after", folder_id) if folder_id else {}
            object_after = _storage_snapshot(case_id, group, "read_folder_object_after", folder_id, str(row.get("location") or "")) if row else {}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            observed = {
                "mode": namespace_before.get("mode"),
                "delete_response": _response_pair(deleted),
                "file_rows_remaining": len(_file_rows(group, ids=[folder_id, file_id])),
                "object_exists_after": object_after.get("exists"),
                "physical_bucket_exists_after": namespace_after.get("physical_bucket_exists"),
                "logical_object_count_after": namespace_after.get("logical_object_count"),
                "cleanup_succeeded": cleanup,
            }
            passed = (
                preclean
                and _response_pair(folder) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and namespace_before.get("mode") == namespace_after.get("mode")
                and storage_folder_cleanup_contract_ok(observed)
            )
            return _result(
                passed,
                [
                    {
                        "name": "detect_storage_mode_and_save_folder_object",
                        "mode": namespace_before.get("mode"),
                        "implementation": namespace_before.get("implementation"),
                        "physical_bucket_exists_before": namespace_before.get("physical_bucket_exists"),
                        "logical_object_count_before": namespace_before.get("logical_object_count"),
                        "object_sha_matches": object_before.get("sha256") == hashlib.sha256(content).hexdigest(),
                        "raw_sha256": [namespace_before.get("raw_sha256"), object_before.get("raw_sha256")],
                    },
                    {
                        "name": "delete_folder_and_validate_logical_or_physical_bucket_contract",
                        **observed,
                        "raw_sha256": [deleted.get("raw_sha256"), namespace_after.get("raw_sha256"), object_after.get("raw_sha256")],
                    },
                ],
                {"fixed_bucket": "logical prefix empty and physical bucket remains", "per_bucket": "folder bucket absent"},
                "FM-DELETE-SUP-BUCKET-001",
                f"{group} folder storage bucket/prefix cleanup contract differed",
            )

        if number == 4:
            space_name = f"{prefix}-{group}-space"
            space = _create_folder(case_id, group, owner, "create_skill_space_fixture", space_name, parent_id=root_id)
            space_id = str((space.get("data") or {}).get("id") or "")
            mark_space = _orm_update_source_type(group, space_id, "skill_space") if space_id else {}
            success_skill_name = f"{prefix}-{group}-success"
            failure_skill_name = f"{prefix}-{group}-failure"
            success_skill = _create_folder(case_id, group, owner, "create_success_skill_folder", success_skill_name, parent_id=space_id)
            failure_skill = _create_folder(case_id, group, owner, "create_failure_skill_folder", failure_skill_name, parent_id=space_id)
            success_skill_id = str((success_skill.get("data") or {}).get("id") or "")
            failure_skill_id = str((failure_skill.get("data") or {}).get("id") or "")
            contents = [b"skill success object", b"skill failure object"]
            success_upload = _upload(case_id, group, owner, "upload_success_skill_object", [(f"{prefix}-{group}-success.txt", contents[0], "text/plain")], parent_id=success_skill_id)
            failure_upload = _upload(case_id, group, owner, "upload_failure_skill_object", [(f"{prefix}-{group}-failure.txt", contents[1], "text/plain")], parent_id=failure_skill_id)
            success_file_id = str(((success_upload.get("data") or [{}])[0]).get("id") or "")
            failure_file_id = str(((failure_upload.get("data") or [{}])[0]).get("id") or "")
            success_row = _file_row(group, success_file_id)
            failure_row = _file_row(group, failure_file_id)
            success_object_before = (
                _storage_snapshot(case_id, group, "read_success_skill_object_before", str(success_row.get("parent_id") or ""), str(success_row.get("location") or "")) if success_row else {}
            )
            failure_object_before = (
                _storage_snapshot(case_id, group, "read_failure_skill_object_before", str(failure_row.get("parent_id") or ""), str(failure_row.get("location") or "")) if failure_row else {}
            )
            checkpoint = _api_log_checkpoint(group)
            server = None
            events: list[dict[str, Any]] = []
            try:
                server, thread, state = _start_skill_backend_stub(group, space_name)
                success_start = len(state["events"])
                success_delete = _delete_files(case_id, group, owner, "delete_skill_after_go_success", [success_skill_id])
                success_events = list(state["events"][success_start:])
                state["mode"] = "failure"
                failure_start = len(state["events"])
                failure_delete = _delete_files(case_id, group, owner, "preserve_skill_after_go_failure", [failure_skill_id])
                failure_events = list(state["events"][failure_start:])
                failure_row_after = _file_row(group, failure_file_id)
                failure_folder_after = _file_row(group, failure_skill_id)
                failure_object_after = (
                    _storage_snapshot(case_id, group, "read_failure_skill_object_after_rejection", str(failure_row.get("parent_id") or ""), str(failure_row.get("location") or ""))
                    if failure_row
                    else {}
                )
                state["mode"] = "success"
                cleanup_start = len(state["events"])
                failure_cleanup = _delete_files(case_id, group, owner, "cleanup_failure_skill_after_go_success", [failure_skill_id])
                cleanup_events = list(state["events"][cleanup_start:])
                events = list(state["events"])
            finally:
                if server is not None:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)
            stub_raw = _record_skill_stub_events(case_id, group, "skill_backend_stub_events", events)
            success_object_after = (
                _storage_snapshot(case_id, group, "read_success_skill_object_after", str(success_row.get("parent_id") or ""), str(success_row.get("location") or "")) if success_row else {}
            )
            failure_object_after_cleanup = (
                _storage_snapshot(case_id, group, "read_failure_skill_object_after_cleanup", str(failure_row.get("parent_id") or ""), str(failure_row.get("location") or "")) if failure_row else {}
            )
            restore_space = _orm_update_source_type(group, space_id, "") if space_id else {}
            space_cleanup = _delete_files(case_id, group, owner, "cleanup_restored_skill_space", [space_id]) if space_id else {"http_status": 200, "code": 0}
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix) and not _file_rows(group, ids=[space_id, success_skill_id, failure_skill_id, success_file_id, failure_file_id])
            log_counts = _api_log_pattern_counts(checkpoint, ["Successfully deleted skill index", "Aborting folder deletion due to index deletion failure"])

            def go_called(call_events: list[dict[str, Any]], skill_name: str, mode: str) -> bool:
                return any(
                    event.get("method") == "DELETE" and event.get("path") == "/api/v1/skills/index" and event.get("mode") == mode and event.get("query", {}).get("skill_id") == [skill_name]
                    for event in call_events
                )

            observed = {
                "success_response": _response_pair(success_delete),
                "success_go_called": go_called(success_events, success_skill_name, "success"),
                "success_rows_absent": not _file_rows(group, ids=[success_skill_id, success_file_id]),
                "success_object_absent": success_object_after.get("exists") is False,
                "failure_response_nonzero": failure_delete.get("code") not in {None, 0},
                "failure_go_called": go_called(failure_events, failure_skill_name, "failure"),
                "failure_rows_preserved": failure_row_after == failure_row and bool(failure_folder_after),
                "failure_object_preserved": failure_object_after.get("sha256") == failure_object_before.get("sha256"),
                "cleanup_succeeded": _response_pair(failure_cleanup) == [200, 0]
                and go_called(cleanup_events, failure_skill_name, "success")
                and failure_object_after_cleanup.get("exists") is False
                and restore_space.get("source_type") == ""
                and _response_pair(space_cleanup) == [200, 0]
                and cleanup,
            }
            passed = (
                preclean
                and _response_pair(space) == [200, 0]
                and mark_space.get("source_type") == "skill_space"
                and all(_response_pair(item) == [200, 0] for item in (success_skill, failure_skill, success_upload, failure_upload))
                and success_object_before.get("sha256") == hashlib.sha256(contents[0]).hexdigest()
                and failure_object_before.get("sha256") == hashlib.sha256(contents[1]).hexdigest()
                and skill_delete_contract_ok(observed)
                and log_counts["Successfully deleted skill index"] >= 2
                and log_counts["Aborting folder deletion due to index deletion failure"] >= 1
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_api_skill_space_and_mark_only_space_via_authorized_orm_fixture",
                        "space_response": _response_pair(space),
                        "orm_update_count": mark_space.get("updated"),
                        "source_type": mark_space.get("source_type"),
                        "success_object_exists": success_object_before.get("exists"),
                        "failure_object_exists": failure_object_before.get("exists"),
                    },
                    {
                        "name": "delete_skill_only_after_controlled_go_success",
                        "response": _response_pair(success_delete),
                        "go_called": observed["success_go_called"],
                        "rows_absent": observed["success_rows_absent"],
                        "object_absent": observed["success_object_absent"],
                        "success_log_count": log_counts["Successfully deleted skill index"],
                        "raw_sha256": [success_delete.get("raw_sha256"), success_object_after.get("raw_sha256"), stub_raw],
                    },
                    {
                        "name": "preserve_skill_when_controlled_go_delete_fails",
                        "response": _response_pair(failure_delete),
                        "go_called": observed["failure_go_called"],
                        "rows_preserved": observed["failure_rows_preserved"],
                        "object_preserved": observed["failure_object_preserved"],
                        "abort_log_count": log_counts["Aborting folder deletion due to index deletion failure"],
                        "raw_sha256": [failure_delete.get("raw_sha256"), failure_object_after.get("raw_sha256")],
                    },
                    {
                        "name": "switch_stub_to_success_restore_space_source_and_cleanup_through_api",
                        "failure_cleanup_response": _response_pair(failure_cleanup),
                        "restore_source_type": restore_space.get("source_type"),
                        "space_cleanup_response": _response_pair(space_cleanup),
                        "cleanup_succeeded": observed["cleanup_succeeded"],
                        "raw_sha256": [failure_cleanup.get("raw_sha256"), failure_object_after_cleanup.get("raw_sha256"), space_cleanup.get("raw_sha256")],
                    },
                ],
                {
                    "success_gate": "Go HTTP 200/code0 before recursive delete",
                    "failure_gate": "abort and preserve metadata/object",
                    "logs": "success and abort phrases",
                    "fixture_exception": "source_type ORM update then restore",
                },
                "FM-DELETE-SUP-SKILL-GO-GATE-001",
                f"{group} skill folder Go backend success/failure deletion gate contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_file_link_supplement(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-file-link-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        dataset_prefix = f"{prefix}-{group}-dataset"
        dataset_preclean = DD._cleanup_prefix(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            dataset_prefix,
        )
        _root(case_id, group, owner)

        dataset_count = 2 if number == 3 else 1
        datasets = [
            DD._create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_link_dataset_{index + 1}",
                {"name": f"{dataset_prefix}-{index + 1}"},
            )
            for index in range(dataset_count)
        ]
        dataset_ids = [str((response.get("data") or {}).get("id") or "") for response in datasets]

        if number in {1, 2}:
            folder: dict[str, Any] | None = None
            nested: dict[str, Any] | None = None
            parent_ids: list[str | None] = [None, None]
            if number == 2:
                folder = _create_folder(
                    case_id,
                    group,
                    owner,
                    "create_recursive_link_folder",
                    f"{prefix}-{group}-folder",
                )
                folder_id = str((folder.get("data") or {}).get("id") or "")
                nested = _create_folder(
                    case_id,
                    group,
                    owner,
                    "create_recursive_link_nested_folder",
                    f"{prefix}-{group}-nested",
                    parent_id=folder_id,
                )
                nested_id = str((nested.get("data") or {}).get("id") or "")
                parent_ids = [folder_id, nested_id]
            contents = [b"supplement link first", b"supplement link second"]
            uploads = [
                _upload(
                    case_id,
                    group,
                    owner,
                    f"upload_link_file_{index + 1}",
                    [
                        (
                            f"{prefix}-{group}-{index + 1}.txt",
                            content,
                            "text/plain",
                        )
                    ],
                    parent_id=parent_ids[index],
                )
                for index, content in enumerate(contents)
            ]
            file_ids = [str(((response.get("data") or [{}])[0]).get("id") or "") for response in uploads]
            file_rows = {file_id: _file_row(group, file_id) for file_id in file_ids}
            target_ids = [str((folder.get("data") or {}).get("id") or "")] if folder else file_ids
            link = _http(
                case_id,
                group,
                "link_files_or_recursive_folder_to_dataset",
                owner["auth"],
                "POST",
                "/files/link-to-datasets",
                payload={"file_ids": target_ids, "kb_ids": dataset_ids},
            )
            _wait_file_documents(
                group,
                file_ids,
                dataset_ids[0],
                expected_count=2,
            )
            relations = _file_document_rows(group, dataset_id=dataset_ids[0])
            linked_ids = sorted(row["file_id"] for row in relations)
            metadata_match = all(
                row.get("name") == file_rows.get(row["file_id"], {}).get("name")
                and row.get("type") == file_rows.get(row["file_id"], {}).get("type")
                and row.get("location") == file_rows.get(row["file_id"], {}).get("location")
                and row.get("size") == file_rows.get(row["file_id"], {}).get("size")
                and row.get("source_type") == "local"
                for row in relations
            )
            configuration_match = all(row.get("parser_config") == row.get("dataset_parser_config") and row.get("pipeline_id") == row.get("dataset_pipeline_id") for row in relations)
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            objects_after = [
                _storage_snapshot(
                    case_id,
                    group,
                    f"prove_link_object_cleanup_{index + 1}",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                for index, row in enumerate(file_rows.values())
                if row
            ]
            dataset_cleanup = DD._delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_link_dataset",
                dataset_ids,
            )
            cleanup = (
                file_cleanup
                and all(item.get("exists") is False for item in objects_after)
                and _response_pair(dataset_cleanup) == [200, 0]
                and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            )
            observed = {
                "response": _response_pair(link),
                "data_true": link.get("data") is True,
                "expected_file_ids": sorted(file_ids),
                "linked_file_ids": linked_ids,
                "document_count": len(relations),
                "all_dataset_ids_match": all(row["dataset_id"] == dataset_ids[0] for row in relations),
                "all_document_metadata_match": metadata_match,
                "all_dataset_configuration_match": configuration_match,
                "cleanup_succeeded": cleanup,
            }
            fixture_responses = [*datasets, *uploads]
            if folder is not None:
                fixture_responses.extend([folder, nested or {}])
            passed = preclean and dataset_preclean and all(_response_pair(item) == [200, 0] for item in fixture_responses) and len(set(file_ids)) == 2 and link_contract_ok(observed)
            finding = "FM-LINK-SUP-TWO-FILES-001" if number == 1 else "FM-LINK-SUP-RECURSIVE-FOLDER-001"
            return _result(
                passed,
                [
                    {
                        "name": "create_two_real_files_and_schedule_link",
                        "fixture_responses": [_response_pair(item) for item in fixture_responses],
                        "requested_file_ids_are_folder": number == 2,
                        "saved_innermost_file_count": len(file_ids),
                        "link_response": _response_pair(link),
                        "raw_sha256": [
                            *[item.get("raw_sha256") for item in fixture_responses],
                            link.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "poll_exact_local_document_and_relation_set",
                        "expected_file_ids": sorted(file_ids),
                        "linked_file_ids": linked_ids,
                        "document_count": len(relations),
                        "all_source_types_local": all(row.get("source_type") == "local" for row in relations),
                        "metadata_matches": metadata_match,
                        "configuration_matches": configuration_match,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": dataset_cleanup.get("raw_sha256"),
                    },
                ],
                {
                    "response": [200, 0],
                    "relations": "exactly two real files in the requested dataset",
                    "source_type": "local",
                },
                finding,
                f"{group} supplement file/folder link expansion contract differed",
            )

        if number == 3:
            content = b"supplement multi dataset link"
            upload = _upload(
                case_id,
                group,
                owner,
                "upload_multi_dataset_file",
                [(f"{prefix}-{group}.txt", content, "text/plain")],
            )
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            row = _file_row(group, file_id)
            relations_before = _file_document_rows(group, file_ids=[file_id])
            link = _http(
                case_id,
                group,
                "link_one_file_to_two_datasets",
                owner["auth"],
                "POST",
                "/files/link-to-datasets",
                payload={"file_ids": [file_id], "kb_ids": dataset_ids},
            )
            for dataset_id in dataset_ids:
                _wait_file_documents(group, [file_id], dataset_id, expected_count=1)
            relations = _file_document_rows(group, file_ids=[file_id])
            document_ids = [item["document_id"] for item in relations]
            documents = _document_rows(group, document_ids)
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_multi_dataset_object_cleanup",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                if row
                else {}
            )
            dataset_cleanup = DD._delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_multi_link_datasets",
                dataset_ids,
            )
            cleanup = file_cleanup and object_after.get("exists") is False and _response_pair(dataset_cleanup) == [200, 0] and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            exact_dataset_set = {item["dataset_id"] for item in relations} == set(dataset_ids)
            passed = (
                preclean
                and dataset_preclean
                and all(_response_pair(item) == [200, 0] for item in datasets)
                and len(set(dataset_ids)) == 2
                and _response_pair(upload) == [200, 0]
                and relations_before == []
                and _response_pair(link) == [200, 0]
                and link.get("data") is True
                and len(relations) == len(documents) == 2
                and exact_dataset_set
                and all(item.get("source_type") == "local" for item in documents)
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "prove_no_old_relation_then_schedule_two_dataset_link",
                        "prior_relation_count": len(relations_before),
                        "dataset_count": len(set(dataset_ids)),
                        "upload_response": _response_pair(upload),
                        "link_response": _response_pair(link),
                        "raw_sha256": [
                            upload.get("raw_sha256"),
                            link.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "poll_replacement_relation_set_in_both_datasets",
                        "relation_count": len(relations),
                        "document_count": len(documents),
                        "exact_dataset_set": exact_dataset_set,
                        "all_source_types_local": all(item.get("source_type") == "local" for item in documents),
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [
                            object_after.get("raw_sha256"),
                            dataset_cleanup.get("raw_sha256"),
                        ],
                    },
                ],
                {
                    "response": [200, 0],
                    "final_relation_count": 2,
                    "datasets": "exactly the two IDs from the current request",
                },
                "FM-LINK-SUP-MULTI-DATASET-001",
                f"{group} multi-dataset link replacement contract differed",
            )

        if number == 4:
            content = b"supplement repeated link"
            upload = _upload(
                case_id,
                group,
                owner,
                "upload_repeated_link_file",
                [(f"{prefix}-{group}.txt", content, "text/plain")],
            )
            file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
            row = _file_row(group, file_id)
            first = _http(
                case_id,
                group,
                "link_file_first_time",
                owner["auth"],
                "POST",
                "/files/link-to-datasets",
                payload={"file_ids": [file_id], "kb_ids": dataset_ids},
            )
            first_relations = _wait_file_documents(group, [file_id], dataset_ids[0], expected_count=1)
            first_document_id = first_relations[0]["document_id"] if len(first_relations) == 1 else ""
            second = _http(
                case_id,
                group,
                "link_file_second_time",
                owner["auth"],
                "POST",
                "/files/link-to-datasets",
                payload={"file_ids": [file_id], "kb_ids": dataset_ids},
            )
            deadline = time.monotonic() + 20
            final_relations: list[dict[str, Any]] = []
            same_id_grace_deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                final_relations = _file_document_rows(group, file_ids=[file_id], dataset_id=dataset_ids[0])
                if len(final_relations) == 1:
                    final_document_id = final_relations[0]["document_id"]
                    if final_document_id != first_document_id or time.monotonic() >= same_id_grace_deadline:
                        break
                time.sleep(0.1)
            final_document_id = final_relations[0]["document_id"] if len(final_relations) == 1 else ""
            old_document_absent_if_replaced = final_document_id == first_document_id or not _document_rows(group, [first_document_id])
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_repeated_link_object_cleanup",
                    str(row.get("parent_id") or ""),
                    str(row.get("location") or ""),
                )
                if row
                else {}
            )
            dataset_cleanup = DD._delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_repeated_link_dataset",
                dataset_ids,
            )
            cleanup = file_cleanup and object_after.get("exists") is False and _response_pair(dataset_cleanup) == [200, 0] and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
            observed = {
                "responses": [_response_pair(first), _response_pair(second)],
                "first_relation_count": len(first_relations),
                "final_relation_count": len(final_relations),
                "final_dataset_matches": len(final_relations) == 1 and final_relations[0]["dataset_id"] == dataset_ids[0],
                "old_document_absent_if_replaced": old_document_absent_if_replaced,
                "cleanup_succeeded": cleanup,
            }
            passed = (
                preclean
                and dataset_preclean
                and _response_pair(datasets[0]) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and first.get("data") is True
                and second.get("data") is True
                and len(final_relations) == 1
                and final_relations[0].get("source_type") == "local"
                and repeated_link_contract_ok(observed)
            )
            return _result(
                passed,
                [
                    {
                        "name": "schedule_same_file_dataset_link_twice",
                        "responses": observed["responses"],
                        "first_relation_count": len(first_relations),
                        "document_id_changed": bool(first_document_id and final_document_id and first_document_id != final_document_id),
                        "raw_sha256": [
                            first.get("raw_sha256"),
                            second.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "poll_one_rebuilt_relation_and_no_old_document",
                        "final_relation_count": len(final_relations),
                        "final_dataset_matches": observed["final_dataset_matches"],
                        "old_document_absent_if_replaced": old_document_absent_if_replaced,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [
                            object_after.get("raw_sha256"),
                            dataset_cleanup.get("raw_sha256"),
                        ],
                    },
                ],
                {
                    "responses": [[200, 0], [200, 0]],
                    "final_relation_count": 1,
                    "replacement": "old document absent when ID changes",
                },
                "FM-LINK-SUP-REPEAT-001",
                f"{group} repeated link replacement contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_file_version_supplement(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-file-ver-{number:03d}"
    finding_ids = {
        1: "FM-COMMIT-SUP-CREATE-IDOR-001",
        2: "FM-COMMIT-SUP-VERSIONS-IDOR-001",
        3: "FM-COMMIT-SUP-DETAIL-IDOR-001",
        4: "FM-COMMIT-SUP-DIFF-IDOR-001",
        5: "FM-COMMIT-SUP-TREE-IDOR-001",
        6: "FM-COMMIT-SUP-CONTENT-IDOR-001",
    }

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        secondary_email = f"file-ver-{number:03d}-user-b@fresh.invalid"
        secondary = _prepare_named_secondary_user(
            case_id,
            group,
            owner,
            secondary_email,
            f"Fresh-File-Ver-{number:03d}-B@1234",
            "version_independent_user",
        )
        secondary_ready = secondary["preclean"] and secondary["registration_code"] == 0 and secondary["login_code"] == 0 and bool(secondary["auth"]) and secondary["tenant_id"] != owner["tenant_id"]
        _root(case_id, group, owner)
        folder = _create_folder(
            case_id,
            group,
            owner,
            "create_version_workspace",
            f"{prefix}-{group}-workspace",
        )
        folder_id = str((folder.get("data") or {}).get("id") or "")
        file_name = f"{prefix}-{group}.txt"
        upload_content = b"fresh version live upload"
        upload = _upload(
            case_id,
            group,
            owner,
            "upload_version_file",
            [(file_name, upload_content, "text/plain")],
            parent_id=folder_id,
        )
        file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
        file_row = _file_row(group, file_id)
        object_addresses: list[tuple[str, str]] = []
        if file_row:
            object_addresses.append(
                (
                    str(file_row.get("parent_id") or ""),
                    str(file_row.get("location") or ""),
                )
            )

        initial_content = f"{prefix}-{group}-plain text content"
        initial_hash = hashlib.sha256(initial_content.encode()).hexdigest()
        initial_location = f".objects/{initial_hash}"
        initial = _create_commit(
            case_id,
            group,
            "create_owner_initial_commit",
            owner["auth"],
            folder_id,
            "Initial version",
            [
                {
                    "file_id": file_id,
                    "file_name": file_name,
                    "operation": "add",
                    "content": initial_content,
                }
            ],
        )
        initial_data = initial.get("data") if isinstance(initial.get("data"), dict) else {}
        initial_commit_id = str(initial_data.get("id") or "")
        initial_rows = _commit_rows(group, commit_ids=[initial_commit_id]) if initial_commit_id else []
        initial_items = _commit_item_rows(group, commit_ids=[initial_commit_id]) if initial_commit_id else []
        initial_tree = initial_rows[0]["tree_state"].get(file_id, {}) if len(initial_rows) == 1 else {}
        initial_object = (
            _storage_snapshot(
                case_id,
                group,
                "read_owner_initial_commit_object",
                folder_id,
                initial_location,
            )
            if folder_id
            else {}
        )
        object_addresses.append((folder_id, initial_location))
        initial_observed = {
            "response": _response_pair(initial),
            "response_id_present": bool(initial_commit_id),
            "commit_row_count": len(initial_rows),
            "item_row_count": len(initial_items),
            "parent_is_null": len(initial_rows) == 1 and initial_rows[0]["parent_id"] is None,
            "operation": initial_items[0]["operation"] if len(initial_items) == 1 else None,
            "tree_entry_matches": initial_tree.get("hash") == initial_hash
            and initial_tree.get("location") == initial_location
            and initial_tree.get("name") == file_name
            and initial_tree.get("parent_id") == folder_id,
            "object_exists": initial_object.get("exists") is True,
            "object_sha_matches": initial_object.get("sha256") == initial_hash,
        }
        base_ready = (
            preclean
            and secondary_ready
            and _response_pair(folder) == [200, 0]
            and _response_pair(upload) == [200, 0]
            and commit_add_contract_ok(initial_observed)
            and len(initial_rows) == 1
            and initial_rows[0]["message"] == "Initial version"
        )

        modified: dict[str, Any] | None = None
        modified_id = ""
        modified_hash = ""
        modified_location = ""
        modified_ok = True
        if number in {2, 4}:
            time.sleep(0.01)
            modified_content = f"{prefix}-{group}-modified content"
            modified_hash = hashlib.sha256(modified_content.encode()).hexdigest()
            modified_location = f".objects/{modified_hash}"
            modified = _create_commit(
                case_id,
                group,
                "create_owner_second_commit",
                owner["auth"],
                folder_id,
                "Second version",
                [
                    {
                        "file_id": file_id,
                        "file_name": file_name,
                        "operation": "modify",
                        "content": modified_content,
                    }
                ],
            )
            modified_data = modified.get("data") if isinstance(modified.get("data"), dict) else {}
            modified_id = str(modified_data.get("id") or "")
            modified_rows = _commit_rows(group, commit_ids=[modified_id]) if modified_id else []
            modified_items = _commit_item_rows(group, commit_ids=[modified_id]) if modified_id else []
            modified_object = _storage_snapshot(
                case_id,
                group,
                "read_owner_second_commit_object",
                folder_id,
                modified_location,
            )
            object_addresses.append((folder_id, modified_location))
            modified_ok = (
                _response_pair(modified) == [200, 0]
                and len(modified_rows) == len(modified_items) == 1
                and modified_rows[0]["parent_id"] == initial_commit_id
                and modified_items[0]["operation"] == "modify"
                and modified_items[0]["old_hash"] == initial_hash
                and modified_items[0]["new_hash"] == modified_hash
                and modified_object.get("sha256") == modified_hash
            )

        happy_response: dict[str, Any]
        happy_ok: bool
        happy_step: dict[str, Any]
        attacker_method = "GET"
        attacker_path = ""
        attacker_params: dict[str, Any] | None = None
        attacker_payload: dict[str, Any] | None = None
        security_location = modified_location or initial_location

        if number == 1:
            happy_response = initial
            happy_ok = commit_add_contract_ok(initial_observed)
            happy_step = {
                "name": "owner_create_initial_commit_and_verify_saved_graph",
                **initial_observed,
                "message_matches": bool(initial_rows and initial_rows[0]["message"] == "Initial version"),
                "raw_sha256": [
                    initial.get("raw_sha256"),
                    initial_object.get("raw_sha256"),
                ],
            }
            attacker_method = "POST"
            attacker_path = f"/folders/{folder_id}/commits"
            attacker_content = f"{prefix}-{group}-unauthorized content"
            attacker_hash = hashlib.sha256(attacker_content.encode()).hexdigest()
            security_location = f".objects/{attacker_hash}"
            object_addresses.append((folder_id, security_location))
            attacker_payload = {
                "message": "Unauthorized version",
                "files": [
                    {
                        "file_id": file_id,
                        "file_name": file_name,
                        "operation": "modify",
                        "content": attacker_content,
                    }
                ],
            }
        elif number == 2:
            happy_response = _commit_request(
                case_id,
                group,
                "owner_list_file_versions",
                owner["auth"],
                "GET",
                f"/files/{file_id}/versions",
            )
            versions = happy_response.get("data") if isinstance(happy_response.get("data"), list) else []
            version_times = [int(item.get("create_time") or 0) for item in versions]
            happy_ok = (
                modified_ok
                and _response_pair(happy_response) == [200, 0]
                and len(versions) == 2
                and {str(item.get("commit_id") or "") for item in versions} == {initial_commit_id, modified_id}
                and {str(item.get("operation") or "") for item in versions} == {"add", "modify"}
                and {str(item.get("hash") or "") for item in versions} == {initial_hash, modified_hash}
                and version_times == sorted(version_times, reverse=True)
            )
            happy_step = {
                "name": "owner_list_two_file_versions",
                "response": _response_pair(happy_response),
                "version_count": len(versions),
                "commit_ids_match": {str(item.get("commit_id") or "") for item in versions} == {initial_commit_id, modified_id},
                "operations_match": {str(item.get("operation") or "") for item in versions} == {"add", "modify"},
                "newest_first": version_times == sorted(version_times, reverse=True),
                "raw_sha256": [
                    modified.get("raw_sha256") if modified else None,
                    happy_response.get("raw_sha256"),
                ],
            }
            attacker_path = f"/files/{file_id}/versions"
        elif number == 3:
            happy_response = _commit_request(
                case_id,
                group,
                "owner_get_commit_detail",
                owner["auth"],
                "GET",
                f"/folders/{folder_id}/commits/{initial_commit_id}",
            )
            detail = happy_response.get("data") if isinstance(happy_response.get("data"), dict) else {}
            detail_files = detail.get("files") if isinstance(detail.get("files"), list) else []
            happy_ok = (
                _response_pair(happy_response) == [200, 0]
                and detail.get("id") == initial_commit_id
                and detail.get("folder_id") == folder_id
                and detail.get("message") == "Initial version"
                and detail.get("file_count") == 1
                and len(detail_files) == 1
                and detail_files[0].get("file_id") == file_id
                and detail_files[0].get("operation") == "add"
            )
            happy_step = {
                "name": "owner_get_specific_commit_detail",
                "response": _response_pair(happy_response),
                "commit_matches": detail.get("id") == initial_commit_id and detail.get("folder_id") == folder_id,
                "message_matches": detail.get("message") == "Initial version",
                "file_item_count": len(detail_files),
                "raw_sha256": happy_response.get("raw_sha256"),
            }
            attacker_path = f"/folders/{folder_id}/commits/{initial_commit_id}"
        elif number == 4:
            happy_response = _commit_request(
                case_id,
                group,
                "owner_diff_two_commits",
                owner["auth"],
                "GET",
                f"/folders/{folder_id}/commits/diff",
                params={"from": initial_commit_id, "to": modified_id},
            )
            diff = happy_response.get("data") if isinstance(happy_response.get("data"), list) else []
            happy_ok = (
                modified_ok
                and _response_pair(happy_response) == [200, 0]
                and len(diff) == 1
                and diff[0].get("file_id") == file_id
                and diff[0].get("operation") == "modify"
                and diff[0].get("old_hash") == initial_hash
                and diff[0].get("new_hash") == modified_hash
                and diff[0].get("old_location") == initial_location
                and diff[0].get("new_location") == modified_location
            )
            happy_step = {
                "name": "owner_compare_two_commits",
                "response": _response_pair(happy_response),
                "diff_count": len(diff),
                "exact_old_new_hashes": bool(diff and diff[0].get("old_hash") == initial_hash and diff[0].get("new_hash") == modified_hash),
                "raw_sha256": [
                    modified.get("raw_sha256") if modified else None,
                    happy_response.get("raw_sha256"),
                ],
            }
            attacker_path = f"/folders/{folder_id}/commits/diff"
            attacker_params = {"from": initial_commit_id, "to": modified_id}
        elif number == 5:
            happy_response = _commit_request(
                case_id,
                group,
                "owner_get_commit_tree",
                owner["auth"],
                "GET",
                f"/folders/{folder_id}/commits/{initial_commit_id}/tree",
            )
            tree = happy_response.get("data") if isinstance(happy_response.get("data"), dict) else {}

            def flatten(node: dict[str, Any]) -> list[dict[str, Any]]:
                result = [node]
                for child in node.get("children", []) if isinstance(node.get("children"), list) else []:
                    if isinstance(child, dict):
                        result.extend(flatten(child))
                return result

            nodes = flatten(tree) if tree else []
            file_nodes = [item for item in nodes if item.get("id") == file_id]
            happy_ok = (
                _response_pair(happy_response) == [200, 0]
                and tree.get("id") == folder_id
                and tree.get("type") == "folder"
                and len(file_nodes) == 1
                and file_nodes[0].get("name") == file_name
                and file_nodes[0].get("type") == "file"
            )
            happy_step = {
                "name": "owner_get_commit_file_tree",
                "response": _response_pair(happy_response),
                "root_matches": tree.get("id") == folder_id,
                "file_node_count": len(file_nodes),
                "file_node_matches": bool(file_nodes and file_nodes[0].get("name") == file_name and file_nodes[0].get("type") == "file"),
                "raw_sha256": happy_response.get("raw_sha256"),
            }
            attacker_path = f"/folders/{folder_id}/commits/{initial_commit_id}/tree"
        elif number == 6:
            happy_response = _commit_request(
                case_id,
                group,
                "owner_get_commit_file_content",
                owner["auth"],
                "GET",
                f"/folders/{folder_id}/commits/{initial_commit_id}/files/{file_id}/content",
            )
            content_data = happy_response.get("data") if isinstance(happy_response.get("data"), dict) else {}
            happy_ok = _response_pair(happy_response) == [200, 0] and content_data.get("content") == initial_content
            happy_step = {
                "name": "owner_get_exact_commit_file_content",
                "response": _response_pair(happy_response),
                "content_is_string": isinstance(content_data.get("content"), str),
                "content_matches": content_data.get("content") == initial_content,
                "content_length": len(str(content_data.get("content") or "")),
                "raw_sha256": happy_response.get("raw_sha256"),
            }
            attacker_path = f"/folders/{folder_id}/commits/{initial_commit_id}/files/{file_id}/content"
        else:
            raise AssertionError("unreachable")

        before_file = _file_row(group, file_id)
        before_count = len(_commit_rows(group, folder_id=folder_id))
        before_object = _storage_snapshot(
            case_id,
            group,
            "read_version_security_object_before",
            folder_id,
            security_location,
        )
        attacker_response = _commit_request(
            case_id,
            group,
            "independent_user_replay_version_endpoint",
            secondary["auth"],
            attacker_method,
            attacker_path,
            payload=attacker_payload,
            params=attacker_params,
        )
        after_file = _file_row(group, file_id)
        after_count = len(_commit_rows(group, folder_id=folder_id))
        after_object = _storage_snapshot(
            case_id,
            group,
            "read_version_security_object_after",
            folder_id,
            security_location,
        )
        security = _commit_access_observed(
            attacker_response,
            before_commit_count=before_count,
            after_commit_count=after_count,
            before_file=before_file,
            after_file=after_file,
            before_object=before_object,
            after_object=after_object,
        )
        cleanup = _cleanup_commit_workspace(
            case_id,
            group,
            owner,
            prefix,
            [folder_id],
            object_addresses,
            secondary["email"],
        )
        passed = base_ready and modified_ok and happy_ok and commit_access_denied_contract_ok(security) and cleanup["cleanup_succeeded"]
        return _result(
            passed,
            [
                happy_step,
                {
                    "name": "reject_independent_tenant_endpoint_replay",
                    **security,
                    "raw_sha256": [
                        attacker_response.get("raw_sha256"),
                        before_object.get("raw_sha256"),
                        after_object.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_active_workspace_objects_and_secondary_user",
                    **cleanup,
                },
            ],
            {
                "owner": "specified commit/version endpoint succeeds with exact saved data",
                "independent_tenant": "HTTP 200/code 108-compatible denial, no data or mutation",
                "retained_history": "commit rows may remain because no public delete API exists",
            },
            finding_ids[number],
            f"{group} supplement version endpoint or independent-tenant authorization contract differed",
        )

    return _run_case(case_id, execute)


def _run_file_storage_supplement(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-file-stor-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        _root(case_id, group, owner)
        content = b"fresh storage upload database and object\x00\x01" if number == 1 else (b"fresh storage exact download\x00\xff" if number == 2 else b"fresh storage move address and bytes")

        source_folder = _create_folder(
            case_id,
            group,
            owner,
            "create_storage_source_folder",
            f"{prefix}-{group}-source",
        )
        source_folder_id = str((source_folder.get("data") or {}).get("id") or "")
        destination: dict[str, Any] | None = None
        destination_id = ""
        if number == 3:
            destination = _create_folder(
                case_id,
                group,
                owner,
                "create_storage_destination_folder",
                f"{prefix}-{group}-destination",
            )
            destination_id = str((destination.get("data") or {}).get("id") or "")
        upload = _upload(
            case_id,
            group,
            owner,
            "upload_storage_fixture",
            [
                (
                    f"{prefix}-{group}.bin",
                    content,
                    "application/octet-stream",
                )
            ],
            parent_id=source_folder_id,
        )
        file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
        before = _file_row(group, file_id)
        object_before = (
            _storage_snapshot(
                case_id,
                group,
                "read_storage_object_before",
                str(before.get("parent_id") or ""),
                str(before.get("location") or ""),
            )
            if before
            else {}
        )

        if number == 1:
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_upload_object_cleanup",
                    str(before.get("parent_id") or ""),
                    str(before.get("location") or ""),
                )
                if before
                else {}
            )
            passed = (
                preclean
                and _response_pair(source_folder) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and bool(before.get("location"))
                and before.get("parent_id") == source_folder_id
                and before.get("size") == len(content)
                and object_before.get("exists") is True
                and object_before.get("length") == len(content)
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and cleanup
                and not _file_rows(group, ids=[source_folder_id, file_id])
                and object_after.get("exists") is False
            )
            return _result(
                passed,
                [
                    {
                        "name": "upload_binary_and_read_saved_file_location",
                        "folder_response": _response_pair(source_folder),
                        "upload_response": _response_pair(upload),
                        "database_row_count": int(bool(before)),
                        "parent_matches": before.get("parent_id") == source_folder_id,
                        "location_nonempty": bool(before.get("location")),
                        "database_size": before.get("size"),
                        "raw_sha256": [
                            source_folder.get("raw_sha256"),
                            upload.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "verify_exact_object_then_api_cleanup",
                        "object_exists": object_before.get("exists"),
                        "object_length": object_before.get("length"),
                        "object_sha_matches": object_before.get("sha256") == hashlib.sha256(content).hexdigest(),
                        "cleanup_succeeded": cleanup,
                        "object_absent_after_cleanup": object_after.get("exists") is False,
                        "raw_sha256": [
                            object_before.get("raw_sha256"),
                            object_after.get("raw_sha256"),
                        ],
                    },
                ],
                {
                    "database": "one row with exact parent, location, and size",
                    "storage": "exact object bytes at saved address",
                },
                "FM-STORAGE-SUP-UPLOAD-001",
                f"{group} upload database/object consistency contract differed",
            )

        if number == 2:
            download = _http(
                case_id,
                group,
                "download_storage_fixture",
                owner["auth"],
                "GET",
                f"/files/{file_id}",
            )
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_download_object_cleanup",
                    str(before.get("parent_id") or ""),
                    str(before.get("location") or ""),
                )
                if before
                else {}
            )
            passed = (
                preclean
                and _response_pair(source_folder) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and before.get("size") == len(content)
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and _response_pair(download) == [200, None]
                and download.get("_content") == content
                and download.get("response_length") == len(content)
                and cleanup
                and object_after.get("exists") is False
            )
            return _result(
                passed,
                [
                    {
                        "name": "upload_and_save_database_storage_address",
                        "responses": [
                            _response_pair(source_folder),
                            _response_pair(upload),
                        ],
                        "database_size": before.get("size"),
                        "storage_sha_matches": object_before.get("sha256") == hashlib.sha256(content).hexdigest(),
                        "raw_sha256": [
                            upload.get("raw_sha256"),
                            object_before.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "download_exact_bytes_from_saved_object",
                        "response": _response_pair(download),
                        "bytes_match": download.get("_content") == content,
                        "response_length": download.get("response_length"),
                        "cleanup_succeeded": cleanup,
                        "object_absent_after_cleanup": object_after.get("exists") is False,
                        "raw_sha256": [
                            download.get("raw_sha256"),
                            object_after.get("raw_sha256"),
                        ],
                    },
                ],
                {"response": [200, "binary"], "bytes": "exact upload payload"},
                "FM-STORAGE-SUP-DOWNLOAD-001",
                f"{group} storage-backed download bytes contract differed",
            )

        if number == 3:
            move = _move_files(
                case_id,
                group,
                owner,
                "move_storage_fixture",
                [file_id],
                dest_file_id=destination_id,
            )
            after = _file_row(group, file_id)
            old_object_after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_old_storage_address_absent",
                    str(before.get("parent_id") or ""),
                    str(before.get("location") or ""),
                )
                if before
                else {}
            )
            new_object = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_new_storage_address",
                    str(after.get("parent_id") or ""),
                    str(after.get("location") or ""),
                )
                if after
                else {}
            )
            cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            new_object_after_cleanup = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_new_storage_address_cleanup",
                    str(after.get("parent_id") or ""),
                    str(after.get("location") or ""),
                )
                if after
                else {}
            )
            observed = {
                "response": _response_pair(move),
                "row_count": int(bool(after)),
                "parent_matches": after.get("parent_id") == destination_id,
                "name_matches": after.get("name") == before.get("name"),
                "location_changed": (
                    before.get("parent_id"),
                    before.get("location"),
                )
                != (after.get("parent_id"), after.get("location")),
                "old_object_absent": old_object_after.get("exists") is False,
                "new_object_exists": new_object.get("exists") is True,
                "new_object_length_matches": new_object.get("length") == len(content),
                "new_object_sha_matches": new_object.get("sha256") == hashlib.sha256(content).hexdigest(),
                "cleanup_succeeded": cleanup and new_object_after_cleanup.get("exists") is False,
            }
            passed = (
                preclean
                and _response_pair(source_folder) == [200, 0]
                and destination is not None
                and _response_pair(destination) == [200, 0]
                and _response_pair(upload) == [200, 0]
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and move_contract_ok(observed, require_location_change=True)
            )
            return _result(
                passed,
                [
                    {
                        "name": "save_old_address_then_move_file",
                        "fixture_responses": [
                            _response_pair(source_folder),
                            _response_pair(destination or {}),
                            _response_pair(upload),
                        ],
                        "move_response": _response_pair(move),
                        "parent_updated": after.get("parent_id") == destination_id,
                        "object_address_changed": observed["location_changed"],
                        "raw_sha256": [
                            upload.get("raw_sha256"),
                            move.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "verify_old_absent_new_exact_and_cleanup",
                        "old_object_absent": observed["old_object_absent"],
                        "new_object_exists": observed["new_object_exists"],
                        "new_object_length_matches": observed["new_object_length_matches"],
                        "new_object_sha_matches": observed["new_object_sha_matches"],
                        "cleanup_succeeded": observed["cleanup_succeeded"],
                        "raw_sha256": [
                            old_object_after.get("raw_sha256"),
                            new_object.get("raw_sha256"),
                            new_object_after_cleanup.get("raw_sha256"),
                        ],
                    },
                ],
                {
                    "database": "parent updated; full object address changed",
                    "storage": "old absent and exact bytes at new address",
                },
                "FM-STORAGE-SUP-MOVE-001",
                f"{group} move database/object address consistency contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _run_file_acl_supplement(case_id: str) -> dict[str, Any]:
    number = int(case_id.rsplit("-", 1)[-1])
    prefix = f"fresh-file-acl-{number:03d}"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_file_prefix(case_id, group, owner, prefix)
        dataset_prefix = f"{prefix}-{group}-dataset"
        dataset_preclean = DD._cleanup_prefix(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            dataset_prefix,
        )
        secondary = _prepare_named_secondary_user(
            case_id,
            group,
            owner,
            f"file-acl-{number:03d}-user-b@fresh.invalid",
            f"Fresh-File-ACL-{number:03d}-B@1234",
            "file_acl_user_b",
        )
        secondary_ready = secondary["preclean"] and secondary["registration_code"] == 0 and secondary["login_code"] == 0 and bool(secondary["auth"]) and secondary["tenant_id"] != owner["tenant_id"]
        _root(case_id, group, owner)
        content = b"fresh team dataset file access" if number == 1 else b"fresh owner only unlinked file"
        upload = _upload(
            case_id,
            group,
            owner,
            "upload_acl_file",
            [(f"{prefix}-{group}.txt", content, "text/plain")],
        )
        file_id = str(((upload.get("data") or [{}])[0]).get("id") or "")
        before = _file_row(group, file_id)
        object_before = (
            _storage_snapshot(
                case_id,
                group,
                "read_acl_object_before",
                str(before.get("parent_id") or ""),
                str(before.get("location") or ""),
            )
            if before
            else {}
        )

        if number == 1:
            membership = _invite_and_accept_member(case_id, group, owner, secondary)
            membership_snapshot = membership["snapshot"]
            dataset = DD._create_dataset(
                case_id,
                group,
                owner["auth"],
                "create_team_file_dataset",
                {"name": dataset_prefix, "permission": "team"},
            )
            dataset_id = str((dataset.get("data") or {}).get("id") or "")
            dataset_snapshot = DD._dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
            link = _http(
                case_id,
                group,
                "link_owner_file_to_team_dataset",
                owner["auth"],
                "POST",
                "/files/link-to-datasets",
                payload={"file_ids": [file_id], "kb_ids": [dataset_id]},
            )
            relations = _wait_file_documents(group, [file_id], dataset_id, expected_count=1)
            downloaded = _http(
                case_id,
                group,
                "team_member_download_owner_file",
                secondary["auth"],
                "GET",
                f"/files/{file_id}",
            )
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_team_acl_object_cleanup",
                    str(before.get("parent_id") or ""),
                    str(before.get("location") or ""),
                )
                if before
                else {}
            )
            dataset_cleanup = (
                DD._delete_ids(
                    case_id,
                    group,
                    owner["auth"],
                    "cleanup_team_file_dataset",
                    [dataset_id],
                )
                if dataset_id
                else {"http_status": 200, "code": 0}
            )
            membership_cleanup = _remove_membership(
                case_id,
                group,
                owner["tenant_id"],
                secondary,
            )
            user_cleanup = _cleanup_secondary_user(case_id, group, secondary["email"])
            cleanup = (
                file_cleanup
                and object_after.get("exists") is False
                and _response_pair(dataset_cleanup) == [200, 0]
                and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], dataset_prefix)
                and membership_cleanup["succeeded"]
                and user_cleanup
            )
            membership_active = membership_snapshot.get("count") == 1 and membership_snapshot.get("role") == "normal"
            observed = {
                "membership_active": membership_active,
                "dataset_permission": dataset_snapshot.get("permission"),
                "relation_count": len(relations),
                "download_response": _response_pair(downloaded),
                "bytes_match": downloaded.get("_content") == content,
                "cleanup_succeeded": cleanup,
            }
            passed = (
                preclean
                and dataset_preclean
                and secondary_ready
                and _response_pair(upload) == [200, 0]
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and _response_pair(membership["invite"]) == [200, 0]
                and _response_pair(membership["accept"]) == [200, 0]
                and _response_pair(dataset) == [200, 0]
                and dataset_snapshot.get("tenant_id") == owner["tenant_id"]
                and _response_pair(link) == [200, 0]
                and link.get("data") is True
                and len(relations) == 1
                and relations[0].get("source_type") == "local"
                and team_file_download_contract_ok(observed)
            )
            return _result(
                passed,
                [
                    {
                        "name": "invite_accept_and_read_only_confirm_team_membership",
                        "secondary_ready": secondary_ready,
                        "invite_response": _response_pair(membership["invite"]),
                        "accept_response": _response_pair(membership["accept"]),
                        "membership_count": membership_snapshot.get("count"),
                        "membership_role": membership_snapshot.get("role"),
                        "membership_status": membership_snapshot.get("status"),
                        "raw_sha256": [
                            *secondary.get("raw_sha256", []),
                            membership["invite"].get("raw_sha256"),
                            membership["accept"].get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "create_team_dataset_link_file_and_poll_relation",
                        "upload_response": _response_pair(upload),
                        "dataset_response": _response_pair(dataset),
                        "dataset_permission": dataset_snapshot.get("permission"),
                        "link_response": _response_pair(link),
                        "relation_count": len(relations),
                        "source_type_local": len(relations) == 1 and relations[0].get("source_type") == "local",
                        "raw_sha256": [
                            upload.get("raw_sha256"),
                            dataset.get("raw_sha256"),
                            link.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "team_member_download_exact_owner_file_bytes",
                        "response": _response_pair(downloaded),
                        "bytes_match": downloaded.get("_content") == content,
                        "response_length": downloaded.get("response_length"),
                        "raw_sha256": downloaded.get("raw_sha256"),
                    },
                    {
                        "name": "cleanup_file_dataset_membership_and_secondary_user",
                        "file_cleanup_succeeded": file_cleanup,
                        "object_absent": object_after.get("exists") is False,
                        "dataset_cleanup_response": _response_pair(dataset_cleanup),
                        "membership_cleanup_succeeded": membership_cleanup["succeeded"],
                        "secondary_user_cleanup_succeeded": user_cleanup,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": [
                            object_after.get("raw_sha256"),
                            dataset_cleanup.get("raw_sha256"),
                            membership_cleanup["response"].get("raw_sha256"),
                        ],
                    },
                ],
                {
                    "membership": "accepted normal member of owner tenant",
                    "dataset_permission": "team",
                    "download": [200, "exact binary"],
                },
                "FM-ACL-SUP-TEAM-DOWNLOAD-001",
                f"{group} team dataset File download authorization contract differed",
            )

        if number == 2:
            membership_before = _membership_snapshot(group, secondary["tenant_id"], owner["tenant_id"])
            relations = _file_document_rows(group, file_ids=[file_id])
            denied = _http(
                case_id,
                group,
                "unrelated_user_download_owner_only_file",
                secondary["auth"],
                "GET",
                f"/files/{file_id}",
            )
            after = _file_row(group, file_id)
            object_after_attempt = (
                _storage_snapshot(
                    case_id,
                    group,
                    "read_owner_only_object_after_denial",
                    str(before.get("parent_id") or ""),
                    str(before.get("location") or ""),
                )
                if before
                else {}
            )
            file_cleanup = _cleanup_file_prefix(case_id, group, owner, prefix)
            object_after_cleanup = (
                _storage_snapshot(
                    case_id,
                    group,
                    "prove_owner_only_object_cleanup",
                    str(before.get("parent_id") or ""),
                    str(before.get("location") or ""),
                )
                if before
                else {}
            )
            user_cleanup = _cleanup_secondary_user(case_id, group, secondary["email"])
            cleanup = file_cleanup and object_after_cleanup.get("exists") is False and user_cleanup
            passed = (
                preclean
                and dataset_preclean
                and secondary_ready
                and membership_before.get("count") == 0
                and _response_pair(upload) == [200, 0]
                and relations == []
                and object_before.get("sha256") == hashlib.sha256(content).hexdigest()
                and denied.get("http_status") == 200
                and denied.get("code") not in {None, 0}
                and denied.get("message") == "No authorization."
                and _response_has_no_data(denied)
                and after == before
                and object_after_attempt.get("exists") is True
                and object_after_attempt.get("length") == object_before.get("length")
                and object_after_attempt.get("sha256") == object_before.get("sha256")
                and cleanup
            )
            return _result(
                passed,
                [
                    {
                        "name": "create_unrelated_user_and_unlinked_owner_file",
                        "secondary_ready": secondary_ready,
                        "tenant_membership_count": membership_before.get("count"),
                        "upload_response": _response_pair(upload),
                        "file_document_relation_count": len(relations),
                        "object_sha_matches": object_before.get("sha256") == hashlib.sha256(content).hexdigest(),
                        "raw_sha256": [
                            *secondary.get("raw_sha256", []),
                            upload.get("raw_sha256"),
                            object_before.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "deny_unrelated_user_owner_only_file_download",
                        "response": _response_pair(denied),
                        "message": denied.get("message"),
                        "data_absent": _response_has_no_data(denied),
                        "file_row_unchanged": after == before,
                        "object_unchanged": object_after_attempt.get("sha256") == object_before.get("sha256"),
                        "raw_sha256": [
                            denied.get("raw_sha256"),
                            object_after_attempt.get("raw_sha256"),
                        ],
                    },
                    {
                        "name": "cleanup_owner_file_and_secondary_user",
                        "file_cleanup_succeeded": file_cleanup,
                        "object_absent_after_cleanup": object_after_cleanup.get("exists") is False,
                        "secondary_user_cleanup_succeeded": user_cleanup,
                        "cleanup_succeeded": cleanup,
                        "raw_sha256": object_after_cleanup.get("raw_sha256"),
                    },
                ],
                {
                    "response": [200, "nonzero", "No authorization."],
                    "data": "absent",
                    "metadata_and_object": "unchanged",
                },
                "FM-ACL-SUP-OWNER-ONLY-001",
                f"{group} unrelated-user File download denial contract differed",
            )

        raise AssertionError("unreachable")

    return _run_case(case_id, execute)


def _unimplemented(case_id: str) -> dict[str, Any]:
    raise RuntimeError(f"case is not implemented yet: {case_id}")


IMPLEMENTED_CASE_IDS = set(CASE_TITLES)
RUNNERS: dict[str, Callable[[], dict[str, Any]]] = {case_id: (lambda current=case_id: _unimplemented(current)) for case_id in CASE_TITLES}
for _case_id in CASE_TITLES:
    if _case_id.startswith("TC-FM-"):
        _number = int(_case_id.rsplit("-", 1)[-1])
        RUNNERS[_case_id] = (
            (lambda current=_case_id: _run_fm_001_to_018(current))
            if _number <= 18
            else (
                (lambda current=_case_id: _run_fm_019_to_028(current))
                if _number <= 28
                else (
                    (lambda current=_case_id: _run_fm_029_to_035(current))
                    if _number <= 35
                    else (
                        (lambda current=_case_id: _run_fm_036_to_039(current))
                        if _number <= 39
                        else (
                            (lambda current=_case_id: _run_fm_040_to_044(current))
                            if _number <= 44
                            else ((lambda current=_case_id: _run_fm_045_to_057(current)) if _number <= 57 else (lambda current=_case_id: _run_fm_058_to_070(current)))
                        )
                    )
                )
            )
        )
    elif _case_id.startswith("TC-FILE-DEL-"):
        RUNNERS[_case_id] = lambda current=_case_id: _run_file_del_supplement(current)
    elif _case_id.startswith("TC-FILE-LINK-"):
        RUNNERS[_case_id] = lambda current=_case_id: _run_file_link_supplement(current)
    elif _case_id.startswith("TC-FILE-VER-"):
        RUNNERS[_case_id] = lambda current=_case_id: _run_file_version_supplement(current)
    elif _case_id.startswith("TC-FILE-STOR-"):
        RUNNERS[_case_id] = lambda current=_case_id: _run_file_storage_supplement(current)
    elif _case_id.startswith("TC-FILE-ACL-"):
        RUNNERS[_case_id] = lambda current=_case_id: _run_file_acl_supplement(current)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fresh File cases")
    parser.add_argument("--case", choices=list(CASE_TITLES), required=True)
    args = parser.parse_args()
    if args.case not in IMPLEMENTED_CASE_IDS:
        raise SystemExit(f"case is not implemented yet: {args.case}")
    result = RUNNERS[args.case]()
    print(json.dumps({"case_id": args.case, "group_statuses": {item["group"]: item["status"] for item in result["groups"]}, "pair_status": result["pair_status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
