#!/usr/bin/env python3
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
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
    PLAN_DIR / "05_chat_session_agent.md",
    PLAN_DIR / "05_chat_agent_supplement.md",
)
RUNNER_PATH = EXECUTE_DIR / "fresh_05_chat_session_agent.py"
RUNNER_TEST_PATH = EXECUTE_DIR / "test_fresh_05_chat_session_agent.py"
EVIDENCE_DIR = evidence_dir("05_chat_session_agent")
RAW_DIR = EVIDENCE_DIR / "raw"
AUDIT_PATH = EVIDENCE_DIR / "coverage_audit.json"
CHECKPOINT_PATH = RAW_DIR / "checkpoint_122_environment.json"
RESIDUE_PATH = RAW_DIR / "residue_audit_122.json"
REPORT_PATH = EXECUTE_DIR / "05_chat_session_agent_report.md"
CASE_PATTERN = re.compile(
    r"^### (TC-(?:CS|CHAT-(?:DEL|PATCH)|AGENT-(?:WH|COMP))-\d{3}):",
    re.MULTILINE,
)
MAIN_CASE_PATTERN = re.compile(r"^TC-CS-\d{3}$")
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


def _plan_case_ids() -> list[str]:
    result: list[str] = []
    for path in PLAN_FILES:
        result.extend(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))
    if len(result) != len(set(result)):
        raise ValueError("duplicate chat/session/agent case ID in plans")
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
            if len(text) >= 8 and text not in REDACTED_VALUES:
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

    gauss_info = EXECUTE_DIR.parent / "gaussdb_info.md"
    if gauss_info.exists():
        line_pattern = re.compile(r"\s*(?:[-*]\s*)?([A-Za-z0-9_.-]+)\s*[:=]\s*(.*?)\s*$")
        for line in gauss_info.read_text(encoding="utf-8", errors="replace").splitlines():
            match = line_pattern.match(line)
            if not match or not any(part in match.group(1).lower() for part in SENSITIVE_KEY_PARTS):
                continue
            value = match.group(2).strip().strip("`\"'")
            if len(value) >= 8 and value not in REDACTED_VALUES:
                values.add(value)
    return values


def _forbidden_source_reference_count() -> int:
    source_paths = (
        RUNNER_PATH,
        RUNNER_TEST_PATH,
        Path(__file__),
        Path(__file__).with_name("test_fresh_05_chat_session_agent_audit.py"),
        EXECUTE_DIR / "fresh_05_091_100_implementation_plan.md",
    )
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
    name = "fresh_05_chat_session_agent_audit_runner"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def extract_fixture_inventory(raw_dir: Path) -> dict[str, dict[str, set[str]]]:
    keys = (
        "dialog_ids",
        "conversation_ids",
        "canvas_ids",
        "api_conversation_ids",
        "search_ids",
        "secondary_emails",
    )
    result = {group: {key: set() for key in keys} for group in ("control", "experiment")}
    for path in raw_dir.glob("*.json"):
        group = next((name for name in result if f"_{name}_" in path.name), None)
        if group is None:
            continue
        payload = _load_json(path)
        request = payload.get("request", {})
        response = payload.get("response", {})
        request = request if isinstance(request, dict) else {}
        response = response if isinstance(response, dict) else {}
        body = response.get("body", {})
        body = body if isinstance(body, dict) else {}
        if body.get("code") != 0:
            continue
        request_json = request.get("json", request)
        request_json = request_json if isinstance(request_json, dict) else {}
        email = request_json.get("email")
        if isinstance(email, str) and email.endswith("@fresh.invalid") and "register" in path.name.lower():
            result[group]["secondary_emails"].add(email)
        if str(request.get("method") or "").upper() != "POST":
            continue
        data = body.get("data", {})
        data = data if isinstance(data, dict) else {}
        resource_id = data.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            continue
        request_path = str(request.get("path") or "")
        key = None
        if request_path == "/chats":
            key = "dialog_ids"
        elif re.fullmatch(r"/chats/[^/]+/sessions", request_path):
            key = "conversation_ids"
        elif request_path == "/agents":
            key = "canvas_ids"
        elif re.fullmatch(r"/agents/[^/]+/sessions", request_path):
            key = "api_conversation_ids"
        elif request_path == "/searches":
            key = "search_ids"
        if key:
            result[group][key].add(resource_id)
    return result


def _count_values(
    cursor: Any,
    table: str,
    column: str,
    values: set[str],
    extra: str = "",
) -> int:
    if not values:
        return 0
    placeholders = ",".join(["%s"] * len(values))
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders}){extra}",
        tuple(sorted(values)),
    )
    return int(cursor.fetchone()[0])


def _prefix_count(cursor: Any, table: str, column: str, extra: str = "") -> int:
    cursor.execute(
        f"SELECT COUNT(*) FROM {table} WHERE LOWER(COALESCE({column},'')) LIKE %s{extra}",
        ("fresh-%",),
    )
    return int(cursor.fetchone()[0])


def _count_parented_values(
    cursor: Any,
    child_table: str,
    parent_table: str,
    values: set[str],
    parent_extra: str = "",
) -> int:
    if not values:
        return 0
    placeholders = ",".join(["%s"] * len(values))
    cursor.execute(
        f"SELECT COUNT(*) FROM {child_table} c JOIN {parent_table} p ON p.id=c.dialog_id WHERE c.id IN ({placeholders}){parent_extra}",
        tuple(sorted(values)),
    )
    return int(cursor.fetchone()[0])


def _count_parented_prefix(
    cursor: Any,
    child_table: str,
    parent_table: str,
    name_column: str,
    parent_extra: str = "",
) -> int:
    cursor.execute(
        f"SELECT COUNT(*) FROM {child_table} c JOIN {parent_table} p ON p.id=c.dialog_id WHERE LOWER(COALESCE(c.{name_column},'')) LIKE %s{parent_extra}",
        ("fresh-%",),
    )
    return int(cursor.fetchone()[0])


def _secondary_counts(cursor: Any, user_table: str, emails: set[str]) -> tuple[int, int]:
    if not emails:
        return 0, 0
    placeholders = ",".join(["%s"] * len(emails))
    params = tuple(sorted(emails))
    cursor.execute(
        f"SELECT COUNT(*) FROM {user_table} WHERE email IN ({placeholders})",
        params,
    )
    users = int(cursor.fetchone()[0])
    cursor.execute(
        f"SELECT COUNT(*) FROM user_tenant WHERE user_id IN (SELECT id FROM {user_table} WHERE email IN ({placeholders}))",
        params,
    )
    return users, int(cursor.fetchone()[0])


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def collect_residue() -> dict[str, Any]:
    runner = _load_runner_module()
    inventory = extract_fixture_inventory(RAW_DIR)
    groups: dict[str, Any] = {}
    for group in ("control", "experiment"):
        connection, user_table, _namespace = runner.DB._open_database(group)
        try:
            with connection.cursor() as cursor:
                active = {
                    "dialogs_by_id": _count_values(
                        cursor,
                        "dialog",
                        "id",
                        inventory[group]["dialog_ids"],
                        " AND status='1'",
                    ),
                    "dialogs_by_fresh_prefix": _prefix_count(cursor, "dialog", "name", " AND status='1'"),
                    "conversations_by_id": _count_parented_values(
                        cursor,
                        "conversation",
                        "dialog",
                        inventory[group]["conversation_ids"],
                        " AND p.status='1'",
                    ),
                    "conversations_by_fresh_prefix": _count_parented_prefix(
                        cursor,
                        "conversation",
                        "dialog",
                        "name",
                        " AND p.status='1'",
                    ),
                    "agents_by_id": _count_values(
                        cursor,
                        "user_canvas",
                        "id",
                        inventory[group]["canvas_ids"],
                    ),
                    "agents_by_fresh_prefix": _prefix_count(cursor, "user_canvas", "title"),
                    "agent_sessions_by_id": _count_parented_values(
                        cursor,
                        "api_4_conversation",
                        "user_canvas",
                        inventory[group]["api_conversation_ids"],
                    ),
                    "agent_sessions_by_fresh_prefix": _count_parented_prefix(
                        cursor,
                        "api_4_conversation",
                        "user_canvas",
                        "name",
                    ),
                    "searches_by_id": _count_values(
                        cursor,
                        "search",
                        "id",
                        inventory[group]["search_ids"],
                    ),
                    "searches_by_fresh_prefix": _prefix_count(cursor, "search", "name"),
                }
                secondary_users, secondary_memberships = _secondary_counts(cursor, user_table, inventory[group]["secondary_emails"])
        finally:
            connection.close()
        groups[group] = {
            "active_fresh_resources": active,
            "captured_secondary_email_count": len(inventory[group]["secondary_emails"]),
            "secondary_users": secondary_users,
            "secondary_memberships": secondary_memberships,
        }
    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "group_order": ["control", "experiment"],
        "groups": groups,
        "active_fixture_cleanup_pass": all(
            all(value == 0 for value in item["active_fresh_resources"].values()) and item["secondary_users"] == 0 and item["secondary_memberships"] == 0 for item in groups.values()
        ),
        "soft_deleted_or_parentless_history_is_observation_only": True,
    }
    _write_private_json(RESIDUE_PATH, result)
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

    private_files = [path for path in EVIDENCE_DIR.rglob("*") if path.is_file()]
    private_dirs = [EVIDENCE_DIR] + [path for path in EVIDENCE_DIR.rglob("*") if path.is_dir()]
    file_mode_violations = sum((path.stat().st_mode & 0o777) != 0o600 for path in private_files)
    directory_mode_violations = sum((path.stat().st_mode & 0o777) != 0o700 for path in private_dirs)
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
    active_resources_clean = all(
        all(int(value) == 0 for value in group.get("active_fresh_resources", {}).values()) and int(group.get("secondary_users", -1)) == 0 and int(group.get("secondary_memberships", -1)) == 0
        for group in residue.get("groups", {}).values()
    ) and set(residue.get("groups", {})) == {"control", "experiment"}

    coverage_ok = (
        len(planned) == 122
        and len(runner) == 122
        and len(runner_set) == 122
        and planned == runner
        and planned_set == evidence_ids
        and validation_errors == 0
        and len(records) == 122
        and sum(pair_counts.values()) == 122
        and sum(group_counts.values()) == 244
    )
    hygiene_ok = (
        file_mode_violations == 0
        and directory_mode_violations == 0
        and known_value_scan["match_count"] == 0
        and sk_scan["match_count"] == 0
        and ragflow_scan["match_count"] == 0
        and exact_key_violations == 0
        and public_known_value_matches == 0
        and public_sk_matches == 0
        and public_ragflow_matches == 0
        and forbidden_references == 0
    )
    supplement_counts = Counter(str(record.get("pair_status")) for record in supplement_records)
    return {
        "batch_id": BATCH_ID,
        "audit_pass": coverage_ok and hygiene_ok and environment_ok and active_resources_clean,
        "coverage_pass": coverage_ok,
        "evidence_hygiene_pass": hygiene_ok,
        "environment_pass": environment_ok,
        "active_fixture_cleanup_pass": active_resources_clean,
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
        "supplement_status_counts": dict(sorted(supplement_counts.items())),
        "supplement_all_pass": supplement_counts == {"PASS": 22},
        "finding_ids": finding_ids,
        "finding_id_count": len(finding_ids),
        "private_file_count": len(private_files),
        "private_directory_count": len(private_dirs),
        "file_mode_violation_count": file_mode_violations,
        "directory_mode_violation_count": directory_mode_violations,
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
        "managed_log_known_secret_occurrence_count": checkpoint.get("security_findings", {}).get("known_secret_occurrences_in_managed_logs"),
    }


def _write_result(result: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    AUDIT_PATH.parent.chmod(0o700)
    temporary = AUDIT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(AUDIT_PATH)
    AUDIT_PATH.chmod(0o600)


def main() -> None:
    collect_residue()
    _write_result(run_audit())
    result = run_audit()
    _write_result(result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
