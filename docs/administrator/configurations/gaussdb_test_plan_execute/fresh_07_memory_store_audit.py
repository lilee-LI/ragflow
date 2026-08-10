#!/usr/bin/env python3
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PLAN_PATH = EXECUTE_DIR.parent / "gaussdb_test_plan" / "07_memory_store_e2e.md"
RUNNER_PATH = EXECUTE_DIR / "fresh_07_memory_store.py"
RUNNER_TEST_PATH = EXECUTE_DIR / "test_fresh_07_memory_store.py"
AUDIT_TEST_PATH = EXECUTE_DIR / "test_fresh_07_memory_store_audit.py"
EVIDENCE_DIR = evidence_dir("07_memory_store")
RAW_DIR = EVIDENCE_DIR / "raw"
AUDIT_PATH = EVIDENCE_DIR / "coverage_audit.json"
CHECKPOINT_PATH = RAW_DIR / "checkpoint_079_environment.json"
RESIDUE_PATH = RAW_DIR / "residue_audit_079.json"
REPORT_PATH = EXECUTE_DIR / "07_memory_store_report.md"
CASE_PATTERN = re.compile(r"^### (TC-MS-\d{3}):", re.MULTILINE)
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMON = _load_module(EXECUTE_DIR / "fresh_06_memory_metadata_audit.py", "fresh_07_audit_common")
expected_pair_status = COMMON.expected_pair_status
scan_known_values = COMMON.scan_known_values
exact_sensitive_value_violation_count = COMMON.exact_sensitive_value_violation_count
audit_permissions = COMMON.audit_permissions
is_candidate_secret_value = COMMON.is_candidate_secret_value


def validate_case_record(record: dict[str, Any], expected_case_id: str) -> None:
    if record.get("case_id") != expected_case_id:
        raise ValueError("case identity mismatch")
    expected_order = ["control", "experiment"]
    if record.get("group_order") != expected_order:
        raise ValueError("declared group order mismatch")
    groups = record.get("groups")
    if not isinstance(groups, list) or [item.get("group") for item in groups] != expected_order:
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


def extract_fixture_inventory(raw_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {group: {"memories": {}, "emails": set(), "model_instances": set()} for group in ("control", "experiment")}
    for path in raw_dir.glob("*.json"):
        group = next(
            (name for name in ("control", "experiment") if f"_{name}_" in path.name),
            None,
        )
        if group is None:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        request = payload.get("request", {})
        response_body = payload.get("response", {}).get("body", {})
        if not isinstance(request, dict) or not isinstance(response_body, dict):
            continue
        request_json = request.get("json", request)
        request_json = request_json if isinstance(request_json, dict) else {}
        data = response_body.get("data", {})
        data = data if isinstance(data, dict) else {}
        if request.get("method") == "POST" and request.get("path") == "/memories" and response_body.get("code") == 0 and isinstance(data.get("id"), str) and data.get("id"):
            result[group]["memories"][data["id"]] = str(data.get("tenant_id") or "")
        email = request_json.get("email")
        if response_body.get("code") == 0 and isinstance(email, str) and email.endswith("@fresh.invalid") and ("register" in path.name.lower() or "register" in str(request.get("path", "")).lower()):
            result[group]["emails"].add(email)
        instance_name = request_json.get("instance_name")
        if (
            response_body.get("code") == 0
            and request.get("method") == "POST"
            and str(request.get("path", "")).endswith("/instances")
            and isinstance(instance_name, str)
            and instance_name.startswith("fresh-ms-")
        ):
            result[group]["model_instances"].add(instance_name)
    return result


def _plan_case_ids() -> list[str]:
    result = CASE_PATTERN.findall(PLAN_PATH.read_text(encoding="utf-8"))
    if len(result) != len(set(result)):
        raise ValueError("duplicate Memory Store case ID in current plan")
    return result


def _runner_case_ids() -> list[str]:
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"), filename=str(RUNNER_PATH))
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RUNNERS" for target in node.targets):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "RUNNERS":
            value = node.value
        if isinstance(value, ast.Dict):
            return [str(key.value) for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)]
    raise ValueError("RUNNERS assignment was not found")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _load_runner_module():
    return _load_module(RUNNER_PATH, "fresh_07_memory_store_audit_runner")


def _count_for_values(cursor: Any, table: str, column: str, values: set[str], extra: str = "") -> int:
    if not values:
        return 0
    placeholders = ",".join(["%s"] * len(values))
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders}){extra}",
        tuple(sorted(values)),
    )
    return int(cursor.fetchone()[0])


def count_model_instances(cursor: Any, names: set[str]) -> int:
    if not names:
        return 0
    placeholders = ",".join(["%s"] * len(names))
    ordered = tuple(sorted(names))
    cursor.execute(
        f"SELECT COUNT(*) FROM tenant_model_instance WHERE instance_name IN ({placeholders})",
        ordered,
    )
    return int(cursor.fetchone()[0])


def count_model_fixture_rows(cursor: Any, names: set[str]) -> dict[str, int]:
    if not names:
        return {"instances": 0, "models": 0, "providers": 0}
    placeholders = ",".join(["%s"] * len(names))
    params = tuple(sorted(names))
    queries = {
        "instances": (f"SELECT COUNT(*) FROM tenant_model_instance WHERE instance_name IN ({placeholders})"),
        "models": (f"SELECT COUNT(*) FROM tenant_model m JOIN tenant_model_instance i ON i.id=m.instance_id WHERE i.instance_name IN ({placeholders})"),
        "providers": (f"SELECT COUNT(DISTINCT p.id) FROM tenant_model_provider p JOIN tenant_model_instance i ON i.provider_id=p.id WHERE i.instance_name IN ({placeholders})"),
    }
    result: dict[str, int] = {}
    for name, sql in queries.items():
        cursor.execute(sql, params)
        result[name] = int(cursor.fetchone()[0])
    return result


MESSAGE_RESIDUE_SCRIPT = r"""
import json, sys
from common import settings
settings.init_settings()

p = json.loads(sys.argv[1])
targets = set(p["memory_ids"])
conn = settings.msgStoreConn
backend = type(conn).__name__
row_count = 0
if "Infinity" in backend:
    raw_conn = conn.connPool.get_conn()
    try:
        raw_db = raw_conn.get_database(conn.dbName)
        for table_name in raw_db.list_tables().table_names:
            if not str(table_name).startswith(conn.table_name_prefix):
                continue
            table = raw_db.get_table(table_name)
            columns = {str(row[0]) for row in table.show_columns().rows()}
            if "memory_id" not in columns:
                continue
            frame, _ = table.output(["memory_id"]).to_df()
            row_count += sum(str(value) in targets for value in frame["memory_id"].tolist())
    finally:
        conn.connPool.release_conn(raw_conn)
else:
    table_rows, _ = conn._fetch_all_with_description(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name LIKE %s ORDER BY table_name",
        [conn.schema, "ragflow_mem_%"],
    )
    if targets:
        placeholders = ",".join(["%s"] * len(targets))
        params = sorted(targets)
        for table_row in table_rows:
            table_name = str(table_row[0])
            selected, _ = conn._fetch_one_with_description(
                f"SELECT COUNT(*) FROM {conn.ddl.qualified_name(table_name)} "
                f"WHERE memory_id IN ({placeholders})",
                params,
            )
            row_count += int(selected[0]) if selected is not None else 0
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({"backend": backend, "row_count": row_count}, sort_keys=True))
"""


def _count_message_rows(runner: Any, group: str, memory_ids: set[str]) -> dict[str, Any]:
    if not memory_ids:
        return {"backend": None, "row_count": 0}
    return runner.MS._run_store_probe(
        "AUDIT-MS-079",
        group,
        "read_only_captured_message_residue",
        MESSAGE_RESIDUE_SCRIPT,
        [json.dumps({"memory_ids": sorted(memory_ids)}, sort_keys=True)],
        timeout=300,
        max_attempts=2,
    )


def collect_residue() -> dict[str, Any]:
    runner = _load_runner_module()
    inventory = extract_fixture_inventory(RAW_DIR)
    groups: dict[str, Any] = {}
    for group in ("control", "experiment"):
        memories = set(inventory[group]["memories"])
        emails = set(inventory[group]["emails"])
        model_instances = set(inventory[group]["model_instances"])
        connection, user_table, _namespace = runner.DB._open_database(group)
        try:
            with connection.cursor() as cursor:
                active_memory_count = _count_for_values(cursor, "memory", "id", memories)
                cursor.execute(
                    "SELECT COUNT(*) FROM memory WHERE LOWER(name) LIKE %s",
                    ("fresh-ms-%",),
                )
                fresh_name_count = int(cursor.fetchone()[0])
                task_count = _count_for_values(
                    cursor,
                    "task",
                    "doc_id",
                    memories,
                    " AND task_type='memory'",
                )
                secondary_user_count = COMMON.count_secondary_users(cursor, user_table, emails)
                model_fixture_rows = count_model_fixture_rows(cursor, model_instances)
        finally:
            connection.close()
        redis_client = runner.BASE._redis_client(group)
        cache_key_count = sum(bool(redis_client.exists(f"memory_{memory_id}")) for memory_id in memories)
        sequence_key_present = bool(redis_client.exists("id_generator:memory"))
        message_probe = _count_message_rows(runner, group, memories)
        groups[group] = {
            "captured_memory_id_count": len(memories),
            "captured_secondary_email_count": len(emails),
            "captured_model_instance_count": len(model_instances),
            "active_memory_by_captured_id": active_memory_count,
            "active_memory_by_fresh_name_prefix": fresh_name_count,
            "secondary_users": secondary_user_count,
            "model_instances": model_fixture_rows["instances"],
            "model_rows": model_fixture_rows["models"],
            "model_provider_rows": model_fixture_rows["providers"],
            "captured_message_rows": int(message_probe.get("row_count") or 0),
            "memory_cache_keys": cache_key_count,
            "memory_task_rows": task_count,
            "sequence_key_present": sequence_key_present,
            "message_store_backend": message_probe.get("backend"),
            "message_probe_raw_sha256": message_probe.get("raw_sha256"),
        }
    cleanup_ok = all(
        item["active_memory_by_captured_id"] == 0
        and item["active_memory_by_fresh_name_prefix"] == 0
        and item["secondary_users"] == 0
        and item["model_instances"] == 0
        and item["model_rows"] == 0
        and item["model_provider_rows"] == 0
        and item["captured_message_rows"] == 0
        for item in groups.values()
    )
    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "group_order": ["control", "experiment"],
        "groups": groups,
        "business_fixture_cleanup_pass": cleanup_ok,
        "task_cache_and_sequence_residue_is_observation_only": True,
    }
    _write_json(RESIDUE_PATH, result)
    return result


def _forbidden_source_reference_count() -> int:
    source_paths = (
        RUNNER_PATH,
        RUNNER_TEST_PATH,
        Path(__file__),
        AUDIT_TEST_PATH,
    )
    forbidden = (
        "gaussdb_test_plan_execute" + "_bak",
        "gaussdb" + "_test/",
        "mysql_control" + "_test/",
    )
    return sum(text.count(marker) for path in source_paths if path.exists() for text in (path.read_text(encoding="utf-8"),) for marker in forbidden)


def _stage_status_counts(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    stages: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        case_number = int(str(record.get("case_id", "0")).rsplit("-", 1)[-1])
        stages[f"{case_number // 100}xx"][str(record.get("pair_status"))] += 1
    return {stage: dict(sorted(counts.items())) for stage, counts in sorted(stages.items())}


def run_audit() -> dict[str, Any]:
    planned = _plan_case_ids()
    runner_ids = _runner_case_ids()
    planned_set = set(planned)
    runner_set = set(runner_ids)
    evidence_paths = sorted(EVIDENCE_DIR.glob("TC-MS-*.json"))
    evidence_order = [path.stem for path in evidence_paths]
    evidence_ids = set(evidence_order)
    records: list[dict[str, Any]] = []
    validation_errors = 0
    for path in evidence_paths:
        record = _load_json(path)
        try:
            validate_case_record(record, path.stem)
        except (ValueError, TypeError):
            validation_errors += 1
        records.append(record)

    pair_counts = Counter(str(record.get("pair_status")) for record in records)
    group_counts = Counter(str(group.get("status")) for record in records for group in record.get("groups", []))
    per_group_counts = {
        name: dict(sorted(Counter(str(group.get("status")) for record in records for group in record.get("groups", []) if group.get("group") == name).items())) for name in ("control", "experiment")
    }
    finding_ids = sorted({str(finding.get("id")) for record in records for group in record.get("groups", []) for finding in group.get("findings", []) if finding.get("id")})

    permissions = audit_permissions(EVIDENCE_DIR)
    sensitive_values = COMMON._collect_known_sensitive_values()
    known_value_scan = scan_known_values(EVIDENCE_DIR, sensitive_values)
    sk_scan = COMMON._scan_pattern(EVIDENCE_DIR, COMMON.SK_PATTERN)
    ragflow_scan = COMMON._scan_pattern(EVIDENCE_DIR, COMMON.RAGFLOW_TOKEN_PATTERN)
    exact_key_violations = exact_sensitive_value_violation_count(EVIDENCE_DIR)
    public_paths = [EXECUTE_DIR / "PROGRESS.md"]
    if REPORT_PATH.exists():
        public_paths.append(REPORT_PATH)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_paths if path.exists())
    public_known_value_matches = sum(public_text.count(value) for value in sensitive_values if value)
    public_sk_matches = len(COMMON.SK_PATTERN.findall(public_text))
    public_ragflow_matches = len(COMMON.RAGFLOW_TOKEN_PATTERN.findall(public_text))
    forbidden_references = _forbidden_source_reference_count()

    checkpoint = _load_json(CHECKPOINT_PATH)
    environment_ok = (
        checkpoint.get("operational_ready") is True
        and checkpoint.get("group_order") == ["control", "experiment"]
        and checkpoint.get("processes", {}).get("all_alive") is True
        and checkpoint.get("processes", {}).get("service_count") == 8
    )
    residue = _load_json(RESIDUE_PATH)
    business_cleanup_ok = residue.get("business_fixture_cleanup_pass") is True

    coverage_ok = (
        len(planned) == 79
        and len(runner_ids) == 79
        and len(runner_set) == 79
        and planned == runner_ids
        and planned == evidence_order
        and planned_set == evidence_ids
        and validation_errors == 0
        and len(records) == 79
        and COMMON.status_count_contract_ok(pair_counts, group_counts, per_group_counts, 79)
    )
    hygiene_ok = (
        permissions["file_mode_violation_count"] == 0
        and permissions["directory_mode_violation_count"] == 0
        and known_value_scan["match_count"] == 0
        and sk_scan["match_count"] == 0
        and ragflow_scan["match_count"] == 0
        and exact_key_violations == 0
        and public_known_value_matches == 0
        and public_sk_matches == 0
        and public_ragflow_matches == 0
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
        "planned_evidence_order_exact": planned == evidence_order,
        "missing_runner_ids": sorted(planned_set - runner_set),
        "extra_runner_ids": sorted(runner_set - planned_set),
        "missing_evidence_ids": sorted(planned_set - evidence_ids),
        "extra_evidence_ids": sorted(evidence_ids - planned_set),
        "case_validation_error_count": validation_errors,
        "group_record_count": sum(group_counts.values()),
        "pair_status_counts": dict(sorted(pair_counts.items())),
        "group_status_counts": dict(sorted(group_counts.items())),
        "per_group_status_counts": per_group_counts,
        "stage_status_counts": _stage_status_counts(records),
        "finding_ids": finding_ids,
        "finding_id_count": len(finding_ids),
        **permissions,
        "known_sensitive_value_count": len(sensitive_values),
        "private_evidence_known_value_scan": known_value_scan,
        "private_evidence_sk_pattern_scan": sk_scan,
        "private_evidence_ragflow_pattern_scan": ragflow_scan,
        "exact_sensitive_value_violation_count": exact_key_violations,
        "public_document_known_value_match_count": public_known_value_matches,
        "public_document_sk_pattern_match_count": public_sk_matches,
        "public_document_ragflow_pattern_match_count": public_ragflow_matches,
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
