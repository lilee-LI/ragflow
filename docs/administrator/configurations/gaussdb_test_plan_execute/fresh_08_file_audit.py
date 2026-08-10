#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PLAN_DIR = EXECUTE_DIR.parent / "gaussdb_test_plan"
PLAN_FILES = (
    PLAN_DIR / "08_file_management.md",
    PLAN_DIR / "08_file_supplement.md",
)
RUNNER_PATH = EXECUTE_DIR / "fresh_08_file.py"
RUNNER_TEST_PATH = EXECUTE_DIR / "test_fresh_08_file.py"
AUDIT_TEST_PATH = EXECUTE_DIR / "test_fresh_08_file_audit.py"
EVIDENCE_DIR = evidence_dir("08_file")
RAW_DIR = EVIDENCE_DIR / "raw"
AUDIT_PATH = EVIDENCE_DIR / "coverage_audit.json"
CHECKPOINT_PATH = RAW_DIR / "checkpoint_089_environment.json"
RESIDUE_PATH = RAW_DIR / "residue_audit_089.json"
REPORT_PATH = EXECUTE_DIR / "08_file_report.md"
CASE_PATTERN = re.compile(r"^### (TC-(?:FM|FILE-[A-Z]+)-\d{3}):", re.MULTILINE)
BEARER_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}
GROUP_ORDER = ["control", "experiment"]
FORBIDDEN_MARKERS = (
    "gaussdb_test_plan_execute" + "_bak",
    "gaussdb" + "_test/",
    "mysql_control" + "_test/",
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMON = _load_module(
    EXECUTE_DIR / "fresh_06_memory_metadata_audit.py",
    "fresh_08_file_audit_common",
)
expected_pair_status = COMMON.expected_pair_status
is_candidate_secret_value = COMMON.is_candidate_secret_value


def _iter_regular_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path == AUDIT_PATH:
            continue
        yield path


def scan_known_values(root: Path, values: set[str]) -> dict[str, int]:
    match_count = 0
    files_with_matches = 0
    for path in _iter_regular_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        count = sum(text.count(value) for value in values if value)
        if count:
            match_count += count
            files_with_matches += 1
    return {"match_count": match_count, "files_with_matches": files_with_matches}


def _scan_pattern(root: Path, pattern: re.Pattern[str]) -> dict[str, int]:
    match_count = 0
    files_with_matches = 0
    for path in _iter_regular_files(root):
        count = len(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))
        if count:
            match_count += count
            files_with_matches += 1
    return {"match_count": match_count, "files_with_matches": files_with_matches}


def exact_sensitive_value_violation_count(root: Path) -> int:
    violations = 0

    def visit(value: Any) -> None:
        nonlocal violations
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in COMMON.EXACT_SENSITIVE_KEYS and not isinstance(item, (dict, list)) and str(item) not in COMMON.REDACTED_VALUES:
                    violations += 1
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for path in root.rglob("*.json"):
        if path.is_symlink() or path == AUDIT_PATH:
            continue
        try:
            visit(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError):
            continue
    return violations


def audit_permissions(root: Path) -> dict[str, int]:
    paths = list(root.rglob("*")) if root.exists() else []
    symlinks = [path for path in paths if path.is_symlink()]
    files = [path for path in paths if not path.is_symlink() and path.is_file()]
    directories = [root] + [path for path in paths if not path.is_symlink() and path.is_dir()]
    return {
        "private_file_count": len(files),
        "private_directory_count": len(directories),
        "file_mode_violation_count": sum((path.stat().st_mode & 0o777) != 0o600 for path in files),
        "directory_mode_violation_count": sum((path.stat().st_mode & 0o777) != 0o700 for path in directories),
        "symlink_count": len(symlinks),
    }


def validate_case_record(record: dict[str, Any], expected_case_id: str) -> None:
    if record.get("case_id") != expected_case_id:
        raise ValueError("case identity mismatch")
    if record.get("group_order") != GROUP_ORDER:
        raise ValueError("declared group order mismatch")
    groups = record.get("groups")
    if not isinstance(groups, list) or [item.get("group") for item in groups] != GROUP_ORDER:
        raise ValueError("group order mismatch")
    statuses = [str(item.get("status")) for item in groups]
    if any(status not in VALID_STATUSES for status in statuses):
        raise ValueError("invalid group status")
    if record.get("pair_status") != expected_pair_status(statuses):
        raise ValueError("pair status mismatch")
    for group in groups:
        steps = group.get("steps")
        if (
            not isinstance(steps, list)
            or not steps
            or not all(isinstance(step, dict) and step.get("name") for step in steps)
            or not isinstance(group.get("oracle"), dict)
            or not isinstance(group.get("findings", []), list)
        ):
            raise ValueError("group result shape mismatch")
    control_time = datetime.fromisoformat(str(groups[0].get("recorded_at")))
    experiment_time = datetime.fromisoformat(str(groups[1].get("recorded_at")))
    if control_time > experiment_time:
        raise ValueError("group timestamp order mismatch")


def _response_body(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("response", {})
    body = response.get("body", {}) if isinstance(response, dict) else {}
    return body if isinstance(body, dict) else {}


def _data_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _empty_inventory() -> dict[str, set[Any]]:
    return {
        "file_ids": set(),
        "dataset_ids": set(),
        "document_ids": set(),
        "commit_ids": set(),
        "commit_folder_ids": set(),
        "secondary_emails": set(),
        "secondary_user_ids": set(),
        "file_object_addresses": set(),
        "commit_object_addresses": set(),
    }


def _tree_state(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def extract_fixture_inventory(raw_dir: Path) -> dict[str, dict[str, set[Any]]]:
    result = {group: _empty_inventory() for group in GROUP_ORDER}
    for path in raw_dir.glob("*.json"):
        group = next((name for name in GROUP_ORDER if f"_{name}_" in path.name), None)
        if group is None:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        request = payload.get("request", {})
        request = request if isinstance(request, dict) else {}
        body = _response_body(payload)
        if body.get("code") != 0:
            continue
        method = str(request.get("method") or "").upper()
        request_path = str(request.get("path") or "")
        data = body.get("data")
        items = _data_items(data)

        if method == "POST" and request_path == "/files":
            for item in items:
                file_id = item.get("id")
                if isinstance(file_id, str) and file_id:
                    result[group]["file_ids"].add(file_id)
                parent_id = item.get("parent_id")
                location = item.get("location")
                if item.get("type") != "folder" and isinstance(parent_id, str) and parent_id and isinstance(location, str) and location:
                    result[group]["file_object_addresses"].add((parent_id, location))

        if method == "POST" and request_path == "/datasets":
            for item in items:
                dataset_id = item.get("id")
                if isinstance(dataset_id, str) and dataset_id:
                    result[group]["dataset_ids"].add(dataset_id)

        if "/documents" in request_path:
            for item in items:
                for key in ("id", "document_id"):
                    document_id = item.get(key)
                    if isinstance(document_id, str) and document_id:
                        result[group]["document_ids"].add(document_id)

        if method == "POST" and re.fullmatch(r"/folders/[^/]+/commits", request_path):
            for item in items:
                commit_id = item.get("id")
                folder_id = item.get("folder_id")
                if isinstance(commit_id, str) and commit_id:
                    result[group]["commit_ids"].add(commit_id)
                if isinstance(folder_id, str) and folder_id:
                    result[group]["commit_folder_ids"].add(folder_id)
                    for entry in _tree_state(item.get("tree_state")).values():
                        if not isinstance(entry, dict):
                            continue
                        location = entry.get("location")
                        if isinstance(location, str) and location.startswith(".objects/"):
                            result[group]["commit_object_addresses"].add((folder_id, location))

        request_json = request.get("json", request)
        request_json = request_json if isinstance(request_json, dict) else {}
        email = request_json.get("email")
        if isinstance(email, str) and email.endswith("@fresh.invalid") and ("register" in path.name.lower() or "register" in request_path.lower()):
            result[group]["secondary_emails"].add(email)
            for item in items:
                user_id = item.get("id")
                if isinstance(user_id, str) and user_id:
                    result[group]["secondary_user_ids"].add(user_id)
    return result


def _plan_case_ids() -> list[str]:
    case_ids: list[str] = []
    for path in PLAN_FILES:
        case_ids.extend(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate File case ID in current plans")
    return case_ids


def _load_runner_module():
    return _load_module(RUNNER_PATH, "fresh_08_file_audit_runner")


def _runner_case_ids() -> list[str]:
    runner = _load_runner_module()
    runner_ids = list(runner.RUNNERS)
    if runner_ids != list(runner.CASE_TITLES):
        raise ValueError("runner and title registry order mismatch")
    return runner_ids


def case_category(case_id: str) -> str:
    if case_id.startswith("TC-FM-"):
        return "main"
    mapping = {
        "TC-FILE-DEL-": "delete",
        "TC-FILE-LINK-": "link",
        "TC-FILE-VER-": "version",
        "TC-FILE-STOR-": "storage",
        "TC-FILE-ACL-": "acl",
    }
    for prefix, category in mapping.items():
        if case_id.startswith(prefix):
            return category
    raise ValueError(f"unknown File case family: {case_id}")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _count_for_values(cursor: Any, table: str, column: str, values: set[str], extra: str = "") -> int:
    if not values:
        return 0
    placeholders = ",".join(["%s"] * len(values))
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders}){extra}",
        tuple(sorted(values)),
    )
    return int(cursor.fetchone()[0])


def _count_for_either(
    cursor: Any,
    table: str,
    left_column: str,
    left_values: set[str],
    right_column: str,
    right_values: set[str],
) -> int:
    clauses: list[str] = []
    params: list[str] = []
    for column, values in (
        (left_column, left_values),
        (right_column, right_values),
    ):
        if values:
            clauses.append(column + " IN (" + ",".join(["%s"] * len(values)) + ")")
            params.extend(sorted(values))
    if not clauses:
        return 0
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE " + " OR ".join(clauses),
        tuple(params),
    )
    return int(cursor.fetchone()[0])


def _prefix_count(cursor: Any, table: str) -> int:
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE LOWER(name) LIKE %s OR LOWER(name) LIKE %s",
        ("fresh-fm-%", "fresh-file-%"),
    )
    return int(cursor.fetchone()[0])


def _relation_prefix_count(cursor: Any) -> int:
    cursor.execute(
        "SELECT COUNT(*) FROM file2document x "
        "LEFT JOIN file f ON f.id=x.file_id "
        "LEFT JOIN document d ON d.id=x.document_id "
        "LEFT JOIN knowledgebase k ON k.id=d.kb_id "
        "WHERE LOWER(f.name) LIKE %s OR LOWER(f.name) LIKE %s "
        "OR LOWER(d.name) LIKE %s OR LOWER(d.name) LIKE %s "
        "OR LOWER(k.name) LIKE %s OR LOWER(k.name) LIKE %s",
        tuple(["fresh-fm-%", "fresh-file-%"] * 3),
    )
    return int(cursor.fetchone()[0])


def _task_prefix_count(cursor: Any) -> int:
    cursor.execute(
        "SELECT COUNT(*) FROM task t JOIN document d ON d.id=t.doc_id WHERE LOWER(d.name) LIKE %s OR LOWER(d.name) LIKE %s",
        ("fresh-fm-%", "fresh-file-%"),
    )
    return int(cursor.fetchone()[0])


def _invalid_attempt_inventory() -> dict[str, dict[str, set[Any]]]:
    result = {group: {"object_addresses": set(), "commit_ids": set(), "folder_ids": set()} for group in GROUP_ORDER}
    fm034 = _load_json(RAW_DIR / "TC-FM-034_invalid_attempt_orphan_inventory.json")
    for group, item in fm034.get("groups", {}).items():
        if group not in result or not isinstance(item, dict):
            continue
        parent_id = item.get("logical_parent_id")
        location = item.get("location")
        if isinstance(parent_id, str) and isinstance(location, str):
            result[group]["object_addresses"].add((parent_id, location))
    fm066 = _load_json(RAW_DIR / "TC-FM-066_invalid_attempt_auxiliary_inventory.json")
    for item in fm066.get("entries", []):
        if not isinstance(item, dict):
            continue
        commit_id = item.get("commit_id")
        folder_id = item.get("folder_id")
        location = item.get("location")
        if isinstance(commit_id, str):
            result["control"]["commit_ids"].add(commit_id)
        if isinstance(folder_id, str):
            result["control"]["folder_ids"].add(folder_id)
        if isinstance(folder_id, str) and isinstance(location, str):
            result["control"]["object_addresses"].add((folder_id, location))
    return result


OBJECT_PROBE_SCRIPT = r"""
import json, sys
from common import settings
settings.init_settings()
addresses = json.loads(sys.argv[1])
exists_count = 0
error_count = 0
for parent_id, location in addresses:
    try:
        exists_count += bool(settings.STORAGE_IMPL.obj_exist(parent_id, location))
    except Exception:
        error_count += 1
print("__FRESH_RESULT__" + json.dumps({
    "address_count": len(addresses),
    "exists_count": exists_count,
    "error_count": error_count,
}, sort_keys=True))
"""


def _probe_objects(group: str, addresses: set[tuple[str, str]]) -> dict[str, int]:
    if not addresses:
        return {"address_count": 0, "exists_count": 0, "error_count": 0}
    manager = _load_module(
        EXECUTE_DIR / "fresh_service_manager.py",
        f"fresh_08_file_audit_service_manager_{group}",
    )
    payload = json.dumps([list(item) for item in sorted(addresses)])
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", OBJECT_PROBE_SCRIPT, payload],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    value = json.loads(marker)
    return {
        "address_count": int(value.get("address_count") or 0),
        "exists_count": int(value.get("exists_count") or 0),
        "error_count": int(value.get("error_count") or 0),
    }


def business_cleanup_contract_ok(item: dict[str, Any]) -> bool:
    required_zero = (
        "active_file_rows",
        "active_dataset_rows",
        "active_document_rows",
        "active_relation_rows",
        "active_task_rows",
        "secondary_users",
        "secondary_memberships",
        "active_file_objects",
        "known_invalid_attempt_objects",
        "object_probe_error_count",
    )
    return all(int(item.get(key) or 0) == 0 for key in required_zero)


def collect_residue() -> dict[str, Any]:
    runner = _load_runner_module()
    inventory = extract_fixture_inventory(RAW_DIR)
    invalid = _invalid_attempt_inventory()
    groups: dict[str, Any] = {}
    for group in GROUP_ORDER:
        current = inventory[group]
        file_ids = set(current["file_ids"])
        dataset_ids = set(current["dataset_ids"])
        document_ids = set(current["document_ids"])
        commit_ids = set(current["commit_ids"]) | set(invalid[group]["commit_ids"])
        folder_ids = set(current["commit_folder_ids"]) | set(invalid[group]["folder_ids"])
        secondary_emails = set(current["secondary_emails"])
        secondary_user_ids = set(current["secondary_user_ids"])
        connection, user_table, _namespace = runner.DB._open_database(group)
        try:
            with connection.cursor() as cursor:
                captured_files = _count_for_values(cursor, "file", "id", file_ids)
                prefix_files = _prefix_count(cursor, "file")
                captured_datasets = _count_for_values(cursor, "knowledgebase", "id", dataset_ids)
                prefix_datasets = _prefix_count(cursor, "knowledgebase")
                captured_documents = _count_for_values(cursor, "document", "id", document_ids)
                prefix_documents = _prefix_count(cursor, "document")
                captured_relations = _count_for_either(
                    cursor,
                    "file2document",
                    "file_id",
                    file_ids,
                    "document_id",
                    document_ids,
                )
                prefix_relations = _relation_prefix_count(cursor)
                captured_tasks = _count_for_values(cursor, "task", "doc_id", document_ids)
                prefix_tasks = _task_prefix_count(cursor)
                secondary_users = COMMON.count_secondary_users(cursor, user_table, secondary_emails)
                secondary_memberships = _count_for_values(cursor, "user_tenant", "user_id", secondary_user_ids)
                retained_commit_rows = _count_for_either(
                    cursor,
                    "file_commit",
                    "id",
                    commit_ids,
                    "folder_id",
                    folder_ids,
                )
                retained_commit_items = _count_for_values(cursor, "file_commit_item", "commit_id", commit_ids)
        finally:
            connection.close()

        active_probe = _probe_objects(group, set(current["file_object_addresses"]))
        commit_probe = _probe_objects(group, set(current["commit_object_addresses"]))
        invalid_probe = _probe_objects(group, set(invalid[group]["object_addresses"]))
        item = {
            "captured_file_id_count": len(file_ids),
            "captured_dataset_id_count": len(dataset_ids),
            "captured_document_id_count": len(document_ids),
            "captured_commit_id_count": len(commit_ids),
            "captured_commit_folder_id_count": len(folder_ids),
            "captured_secondary_email_count": len(secondary_emails),
            "captured_secondary_user_id_count": len(secondary_user_ids),
            "captured_file_object_address_count": active_probe["address_count"],
            "captured_commit_object_address_count": commit_probe["address_count"],
            "known_invalid_attempt_object_address_count": invalid_probe["address_count"],
            "active_file_rows_by_captured_id": captured_files,
            "active_file_rows_by_fresh_name_prefix": prefix_files,
            "active_dataset_rows_by_captured_id": captured_datasets,
            "active_dataset_rows_by_fresh_name_prefix": prefix_datasets,
            "active_document_rows_by_captured_id": captured_documents,
            "active_document_rows_by_fresh_name_prefix": prefix_documents,
            "active_relation_rows_by_captured_id": captured_relations,
            "active_relation_rows_by_fresh_name_prefix": prefix_relations,
            "active_task_rows_by_captured_id": captured_tasks,
            "active_task_rows_by_fresh_name_prefix": prefix_tasks,
            "active_file_rows": captured_files + prefix_files,
            "active_dataset_rows": captured_datasets + prefix_datasets,
            "active_document_rows": captured_documents + prefix_documents,
            "active_relation_rows": captured_relations + prefix_relations,
            "active_task_rows": captured_tasks + prefix_tasks,
            "secondary_users": secondary_users,
            "secondary_memberships": secondary_memberships,
            "active_file_objects": active_probe["exists_count"],
            "known_invalid_attempt_objects": invalid_probe["exists_count"],
            "retained_commit_rows": retained_commit_rows,
            "retained_commit_item_rows": retained_commit_items,
            "retained_commit_objects": commit_probe["exists_count"],
            "object_probe_error_count": (active_probe["error_count"] + commit_probe["error_count"] + invalid_probe["error_count"]),
        }
        item["business_fixture_cleanup_pass"] = business_cleanup_contract_ok(item)
        groups[group] = item
    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "group_order": GROUP_ORDER,
        "groups": groups,
        "business_fixture_cleanup_pass": all(item["business_fixture_cleanup_pass"] for item in groups.values()),
        "retained_commit_history_is_observation_only": True,
        "invalid_attempt_objects_are_cleanup_failures": True,
    }
    _write_json(RESIDUE_PATH, result)
    return result


def _formal_evidence_paths() -> list[Path]:
    return list(EVIDENCE_DIR.glob("TC-FM-*.json")) + list(EVIDENCE_DIR.glob("TC-FILE-*.json"))


def _forbidden_source_reference_count() -> int:
    source_paths = (
        RUNNER_PATH,
        RUNNER_TEST_PATH,
        Path(__file__),
        AUDIT_TEST_PATH,
        REPORT_PATH,
    )
    return sum(text.count(marker) for path in source_paths if path.exists() for text in (path.read_text(encoding="utf-8"),) for marker in FORBIDDEN_MARKERS)


def _category_status_counts(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        counts[case_category(str(record.get("case_id")))][str(record.get("pair_status"))] += 1
    return {category: dict(sorted(values.items())) for category, values in sorted(counts.items())}


def run_audit() -> dict[str, Any]:
    planned = _plan_case_ids()
    runner_ids = _runner_case_ids()
    planned_set = set(planned)
    runner_set = set(runner_ids)
    evidence_paths = _formal_evidence_paths()
    evidence_by_id = {path.stem: path for path in evidence_paths}
    evidence_ids = set(evidence_by_id)
    records: list[dict[str, Any]] = []
    validation_errors = 0
    for case_id in planned:
        record = _load_json(evidence_by_id[case_id]) if case_id in evidence_by_id else {}
        try:
            validate_case_record(record, case_id)
        except (ValueError, TypeError):
            validation_errors += 1
        records.append(record)

    chronological_ids: list[str] = []
    chronology_errors = 0
    sortable: list[tuple[datetime, str]] = []
    previous_experiment: datetime | None = None
    for record in records:
        groups = record.get("groups", [])
        try:
            control_time = datetime.fromisoformat(str(groups[0]["recorded_at"]))
            experiment_time = datetime.fromisoformat(str(groups[1]["recorded_at"]))
            sortable.append((control_time, str(record.get("case_id"))))
            if previous_experiment is not None and previous_experiment > control_time:
                chronology_errors += 1
            previous_experiment = experiment_time
        except (IndexError, KeyError, TypeError, ValueError):
            chronology_errors += 1
    chronological_ids = [case_id for _time, case_id in sorted(sortable)]

    pair_counts = Counter(str(record.get("pair_status")) for record in records)
    group_counts = Counter(str(group.get("status")) for record in records for group in record.get("groups", []))
    per_group_counts = {name: dict(sorted(Counter(str(group.get("status")) for record in records for group in record.get("groups", []) if group.get("group") == name).items())) for name in GROUP_ORDER}
    category_counts = _category_status_counts(records)
    expected_category_totals = Counter(case_category(case_id) for case_id in planned)
    observed_category_totals = {category: sum(values.values()) for category, values in category_counts.items()}
    finding_ids = sorted(
        {str(finding.get("id")) for record in records for group in record.get("groups", []) for finding in group.get("findings", []) if isinstance(finding, dict) and finding.get("id")}
    )

    permissions = audit_permissions(EVIDENCE_DIR)
    sensitive_values = COMMON._collect_known_sensitive_values()
    known_value_scan = scan_known_values(EVIDENCE_DIR, sensitive_values)
    sk_scan = _scan_pattern(EVIDENCE_DIR, COMMON.SK_PATTERN)
    ragflow_scan = _scan_pattern(EVIDENCE_DIR, COMMON.RAGFLOW_TOKEN_PATTERN)
    bearer_scan = _scan_pattern(EVIDENCE_DIR, BEARER_PATTERN)
    exact_key_violations = exact_sensitive_value_violation_count(EVIDENCE_DIR)
    public_paths = [EXECUTE_DIR / "PROGRESS.md"]
    if REPORT_PATH.exists():
        public_paths.append(REPORT_PATH)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_paths if path.exists())
    public_known_value_matches = sum(public_text.count(value) for value in sensitive_values if value)
    public_sk_matches = len(COMMON.SK_PATTERN.findall(public_text))
    public_ragflow_matches = len(COMMON.RAGFLOW_TOKEN_PATTERN.findall(public_text))
    public_bearer_matches = len(BEARER_PATTERN.findall(public_text))
    forbidden_references = _forbidden_source_reference_count()

    checkpoint = _load_json(CHECKPOINT_PATH)
    environment_ok = (
        checkpoint.get("operational_ready") is True
        and checkpoint.get("group_order") == GROUP_ORDER
        and checkpoint.get("processes", {}).get("all_alive") is True
        and checkpoint.get("processes", {}).get("service_count") == 8
    )
    residue = _load_json(RESIDUE_PATH)
    business_cleanup_ok = residue.get("business_fixture_cleanup_pass") is True

    coverage_ok = (
        len(planned) == 89
        and len(runner_ids) == 89
        and len(runner_set) == 89
        and planned == runner_ids
        and planned_set == evidence_ids
        and len(evidence_paths) == 89
        and len(evidence_by_id) == 89
        and planned == chronological_ids
        and validation_errors == 0
        and chronology_errors == 0
        and COMMON.status_count_contract_ok(pair_counts, group_counts, per_group_counts, 89)
        and observed_category_totals == dict(expected_category_totals)
    )
    hygiene_ok = (
        permissions["file_mode_violation_count"] == 0
        and permissions["directory_mode_violation_count"] == 0
        and permissions["symlink_count"] == 0
        and known_value_scan["match_count"] == 0
        and sk_scan["match_count"] == 0
        and ragflow_scan["match_count"] == 0
        and bearer_scan["match_count"] == 0
        and exact_key_violations == 0
        and public_known_value_matches == 0
        and public_sk_matches == 0
        and public_ragflow_matches == 0
        and public_bearer_matches == 0
        and forbidden_references == 0
    )
    return {
        "batch_id": BATCH_ID,
        "audit_pass": coverage_ok and hygiene_ok and environment_ok and business_cleanup_ok,
        "coverage_pass": coverage_ok,
        "evidence_hygiene_pass": hygiene_ok,
        "environment_pass": environment_ok,
        "business_fixture_cleanup_pass": business_cleanup_ok,
        "planned_case_count": len(planned),
        "runner_case_count": len(runner_ids),
        "evidence_case_count": len(evidence_ids),
        "planned_runner_order_exact": planned == runner_ids,
        "planned_evidence_order_exact": planned == chronological_ids,
        "missing_runner_ids": sorted(planned_set - runner_set),
        "extra_runner_ids": sorted(runner_set - planned_set),
        "missing_evidence_ids": sorted(planned_set - evidence_ids),
        "extra_evidence_ids": sorted(evidence_ids - planned_set),
        "case_validation_error_count": validation_errors,
        "cross_case_chronology_error_count": chronology_errors,
        "group_record_count": sum(group_counts.values()),
        "pair_status_counts": dict(sorted(pair_counts.items())),
        "group_status_counts": dict(sorted(group_counts.items())),
        "per_group_status_counts": per_group_counts,
        "category_status_counts": category_counts,
        "finding_ids": finding_ids,
        "finding_id_count": len(finding_ids),
        **permissions,
        "known_sensitive_value_count": len(sensitive_values),
        "private_evidence_known_value_scan": known_value_scan,
        "private_evidence_sk_pattern_scan": sk_scan,
        "private_evidence_ragflow_pattern_scan": ragflow_scan,
        "private_evidence_bearer_pattern_scan": bearer_scan,
        "exact_sensitive_value_violation_count": exact_key_violations,
        "public_document_known_value_match_count": public_known_value_matches,
        "public_document_sk_pattern_match_count": public_sk_matches,
        "public_document_ragflow_pattern_match_count": public_ragflow_matches,
        "public_document_bearer_pattern_match_count": public_bearer_matches,
        "forbidden_backup_source_reference_count": forbidden_references,
        "environment_operational_ready": checkpoint.get("operational_ready"),
        "environment_service_count": checkpoint.get("processes", {}).get("service_count"),
        "residue_summary": residue.get("groups", {}),
    }


def _write_json(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def main() -> None:
    collect_residue()
    result = run_audit()
    _write_json(AUDIT_PATH, result)
    result = run_audit()
    _write_json(AUDIT_PATH, result)
    print(
        json.dumps(
            {
                "audit_pass": result["audit_pass"],
                "coverage_pass": result["coverage_pass"],
                "evidence_hygiene_pass": result["evidence_hygiene_pass"],
                "environment_pass": result["environment_pass"],
                "business_fixture_cleanup_pass": result["business_fixture_cleanup_pass"],
                "pair_status_counts": result["pair_status_counts"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
