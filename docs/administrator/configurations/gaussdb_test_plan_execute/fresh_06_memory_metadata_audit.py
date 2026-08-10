#!/usr/bin/env python3
from __future__ import annotations

import ast
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PLAN_DIR = EXECUTE_DIR.parent / "gaussdb_test_plan"
PLAN_FILES = (
    PLAN_DIR / "06_memory_metadata.md",
    PLAN_DIR / "06_memory_supplement.md",
)
RUNNER_PATH = EXECUTE_DIR / "fresh_06_memory_metadata.py"
RUNNER_TEST_PATH = EXECUTE_DIR / "test_fresh_06_memory_metadata.py"
AUDIT_TEST_PATH = EXECUTE_DIR / "test_fresh_06_memory_metadata_audit.py"
EVIDENCE_DIR = evidence_dir("06_memory_metadata")
RAW_DIR = EVIDENCE_DIR / "raw"
AUDIT_PATH = EVIDENCE_DIR / "coverage_audit.json"
CHECKPOINT_PATH = RAW_DIR / "checkpoint_047_environment.json"
RESIDUE_PATH = RAW_DIR / "residue_audit_047.json"
REPORT_PATH = EXECUTE_DIR / "06_memory_metadata_report.md"
CASE_PATTERN = re.compile(r"^### (TC-MM(?:-SUP)?-\d{3}):", re.MULTILINE)
MAIN_CASE_PATTERN = re.compile(r"^TC-MM-\d{3}$")
SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")
RAGFLOW_TOKEN_PATTERN = re.compile(r"\bragflow-[A-Za-z0-9._-]{8,}\b")
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "api_key",
    "api-key",
    "access_key",
    "authorization",
    "cookie",
    "token",
)
EXACT_SENSITIVE_KEYS = {
    "password",
    "secret",
    "api_key",
    "api-key",
    "access_key",
    "secret_key",
    "authorization",
    "cookie",
    "token",
    "access_token",
    "token_value",
    "jwt_secret",
}
REDACTED_VALUES = {"", "<redacted>", "<omitted>", "None", "null"}
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}


def expected_pair_status(statuses: list[str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PASS"


def status_count_contract_ok(
    pair_counts: dict[str, int],
    group_counts: dict[str, int],
    per_group_counts: dict[str, dict[str, int]],
    expected_case_count: int,
) -> bool:
    return (
        set(pair_counts).issubset(VALID_STATUSES)
        and set(group_counts).issubset(VALID_STATUSES)
        and sum(pair_counts.values()) == expected_case_count
        and sum(group_counts.values()) == expected_case_count * 2
        and set(per_group_counts) == {"control", "experiment"}
        and all(set(counts).issubset(VALID_STATUSES) and sum(counts.values()) == expected_case_count for counts in per_group_counts.values())
    )


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
    control_time = datetime.fromisoformat(str(groups[0].get("recorded_at")))
    experiment_time = datetime.fromisoformat(str(groups[1].get("recorded_at")))
    if control_time > experiment_time:
        raise ValueError("group timestamp order mismatch")


def scan_known_values(root: Path, values: set[str]) -> dict[str, int]:
    match_count = 0
    files_with_matches = 0
    for path in root.rglob("*"):
        if not path.is_file() or path == AUDIT_PATH:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        count = sum(text.count(value) for value in values if value)
        if count:
            match_count += count
            files_with_matches += 1
    return {"match_count": match_count, "files_with_matches": files_with_matches}


def _scan_pattern(root: Path, pattern: re.Pattern[str]) -> dict[str, int]:
    match_count = 0
    files_with_matches = 0
    for path in root.rglob("*"):
        if not path.is_file() or path == AUDIT_PATH:
            continue
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
                if str(key).lower() in EXACT_SENSITIVE_KEYS and not isinstance(item, (dict, list)) and str(item) not in REDACTED_VALUES:
                    violations += 1
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for path in root.rglob("*.json"):
        if path == AUDIT_PATH:
            continue
        try:
            visit(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
    return violations


def audit_permissions(root: Path) -> dict[str, int]:
    files = [path for path in root.rglob("*") if path.is_file()]
    directories = [root] + [path for path in root.rglob("*") if path.is_dir()]
    return {
        "private_file_count": len(files),
        "private_directory_count": len(directories),
        "file_mode_violation_count": sum((path.stat().st_mode & 0o777) != 0o600 for path in files),
        "directory_mode_violation_count": sum((path.stat().st_mode & 0o777) != 0o700 for path in directories),
    }


def extract_created_memory_ids(raw_dir: Path) -> dict[str, set[str]]:
    result = {"control": set(), "experiment": set()}
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
        body = payload.get("response", {}).get("body", {})
        data = body.get("data", {}) if isinstance(body, dict) else {}
        if request.get("method") == "POST" and request.get("path") == "/memories" and body.get("code") == 0 and isinstance(data, dict) and isinstance(data.get("id"), str) and data["id"]:
            result[group].add(data["id"])
    return result


def _extract_registered_emails(raw_dir: Path) -> dict[str, set[str]]:
    result = {"control": set(), "experiment": set()}
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
        request_json = request.get("json", request) if isinstance(request, dict) else {}
        email = request_json.get("email") if isinstance(request_json, dict) else None
        if ("register" in path.name.lower() or (request.get("method") == "POST" and "register" in str(request.get("path", "")))) and isinstance(email, str) and email.endswith("@fresh.invalid"):
            result[group].add(email)
    return result


def _plan_case_ids() -> list[str]:
    result: list[str] = []
    for path in PLAN_FILES:
        result.extend(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))
    if len(result) != len(set(result)):
        raise ValueError("duplicate memory metadata case ID in plans")
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


def is_candidate_secret_value(value: Any) -> bool:
    if not isinstance(value, (str, bytes)):
        return False
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    text = text.strip()
    return len(text) >= 8 and text not in REDACTED_VALUES


def _collect_known_sensitive_values() -> set[str]:
    values: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                visit(item_value, str(item_key))
            return
        if isinstance(value, list):
            for item in value:
                visit(item, key)
            return
        if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
            text = str(value).strip()
            if is_candidate_secret_value(value):
                values.add(text)

    runtime_root = RUNTIME_DIR
    yaml = YAML(typ="safe", pure=True)
    for path in runtime_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            if path.suffix == ".json":
                visit(json.loads(path.read_text(encoding="utf-8")))
            elif path.name in {"service_conf.yaml", "service_conf.yml"}:
                visit(yaml.load(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            continue
    return values


def _forbidden_source_reference_count() -> int:
    source_paths = (RUNNER_PATH, RUNNER_TEST_PATH, Path(__file__), AUDIT_TEST_PATH)
    forbidden = (
        "gaussdb_test_plan_execute" + "_bak",
        "gaussdb" + "_test/",
        "mysql_control" + "_test/",
    )
    return sum(text.count(marker) for path in source_paths if path.exists() for text in (path.read_text(encoding="utf-8"),) for marker in forbidden)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _load_runner_module():
    spec = importlib.util.spec_from_file_location("fresh_06_audit_runner", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _count_for_values(cursor: Any, table: str, column: str, values: set[str], extra: str = "") -> int:
    if not values:
        return 0
    placeholders = ",".join(["%s"] * len(values))
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders}){extra}",
        tuple(sorted(values)),
    )
    return int(cursor.fetchone()[0])


def count_secondary_users(cursor: Any, user_table: str, emails: set[str]) -> int:
    return _count_for_values(cursor, user_table, "email", emails)


def collect_residue() -> dict[str, Any]:
    runner = _load_runner_module()
    memory_ids = extract_created_memory_ids(RAW_DIR)
    emails = _extract_registered_emails(RAW_DIR)
    groups: dict[str, Any] = {}
    for group in ("control", "experiment"):
        connection, user_table, _namespace = runner.DB._open_database(group)
        try:
            with connection.cursor() as cursor:
                metadata_id_count = _count_for_values(cursor, "memory", "id", memory_ids[group])
                cursor.execute(
                    "SELECT COUNT(*) FROM memory WHERE LOWER(name) LIKE %s",
                    ("fresh-mm-%",),
                )
                fresh_name_count = int(cursor.fetchone()[0])
                task_count = _count_for_values(
                    cursor,
                    "task",
                    "doc_id",
                    memory_ids[group],
                    " AND task_type='memory'",
                )
                secondary_user_count = count_secondary_users(cursor, user_table, emails[group])
        finally:
            connection.close()
        redis_client = runner.BASE._redis_client(group)
        cache_key_count = sum(bool(redis_client.exists(f"memory_{memory_id}")) for memory_id in memory_ids[group])
        groups[group] = {
            "created_memory_id_count": len(memory_ids[group]),
            "registered_secondary_email_count": len(emails[group]),
            "active_memory_by_captured_id": metadata_id_count,
            "active_memory_by_fresh_name_prefix": fresh_name_count,
            "secondary_users": secondary_user_count,
            "memory_cache_keys": cache_key_count,
            "memory_task_rows": task_count,
        }
    sup020 = _load_json(EVIDENCE_DIR / "TC-MM-SUP-020.json")
    fixture_cleanup_ok = all(group.get("status") == "PASS" and group.get("oracle", {}).get("fixture_cleanup") == 0 for group in sup020.get("groups", [])) and len(sup020.get("groups", [])) == 2
    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "group_order": ["control", "experiment"],
        "groups": groups,
        "authorized_adapter_fixture_cleanup_pass": fixture_cleanup_ok,
        "business_fixture_cleanup_pass": fixture_cleanup_ok
        and all(item["active_memory_by_captured_id"] == 0 and item["active_memory_by_fresh_name_prefix"] == 0 and item["secondary_users"] == 0 for item in groups.values()),
        "auxiliary_residue_is_observation_only": True,
    }
    _write_json(RESIDUE_PATH, result)
    return result


def run_audit() -> dict[str, Any]:
    planned = _plan_case_ids()
    runner = _runner_case_ids()
    planned_set = set(planned)
    runner_set = set(runner)
    evidence_paths = sorted(EVIDENCE_DIR.glob("TC-*.json"))
    evidence_ids = {path.stem for path in evidence_paths}
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
    main_records = [record for record in records if MAIN_CASE_PATTERN.fullmatch(str(record.get("case_id")))]
    supplement_records = [record for record in records if record not in main_records]
    finding_ids = sorted({str(finding.get("id")) for record in records for group in record.get("groups", []) for finding in group.get("findings", []) if finding.get("id")})

    permissions = audit_permissions(EVIDENCE_DIR)
    sensitive_values = _collect_known_sensitive_values()
    known_value_scan = scan_known_values(EVIDENCE_DIR, sensitive_values)
    sk_scan = _scan_pattern(EVIDENCE_DIR, SK_PATTERN)
    ragflow_scan = _scan_pattern(EVIDENCE_DIR, RAGFLOW_TOKEN_PATTERN)
    exact_key_violations = exact_sensitive_value_violation_count(EVIDENCE_DIR)
    public_paths = [EXECUTE_DIR / "PROGRESS.md"]
    if REPORT_PATH.exists():
        public_paths.append(REPORT_PATH)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_paths if path.exists())
    public_known_value_matches = sum(public_text.count(value) for value in sensitive_values if value)
    public_sk_matches = len(SK_PATTERN.findall(public_text))
    public_ragflow_matches = len(RAGFLOW_TOKEN_PATTERN.findall(public_text))
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
        len(planned) == 47
        and len(runner) == 47
        and len(runner_set) == 47
        and planned == runner
        and planned_set == evidence_ids
        and validation_errors == 0
        and len(records) == 47
        and status_count_contract_ok(pair_counts, group_counts, per_group_counts, 47)
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
        "runner_case_count": len(runner),
        "evidence_case_count": len(evidence_ids),
        "planned_runner_order_exact": planned == runner,
        "missing_runner_ids": sorted(planned_set - runner_set),
        "extra_runner_ids": sorted(runner_set - planned_set),
        "missing_evidence_ids": sorted(planned_set - evidence_ids),
        "extra_evidence_ids": sorted(evidence_ids - planned_set),
        "case_validation_error_count": validation_errors,
        "group_record_count": sum(group_counts.values()),
        "pair_status_counts": dict(sorted(pair_counts.items())),
        "group_status_counts": dict(sorted(group_counts.items())),
        "per_group_status_counts": per_group_counts,
        "main_plan_case_count": len(main_records),
        "main_plan_status_counts": dict(sorted(Counter(str(record.get("pair_status")) for record in main_records).items())),
        "supplement_case_count": len(supplement_records),
        "supplement_status_counts": dict(sorted(Counter(str(record.get("pair_status")) for record in supplement_records).items())),
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
