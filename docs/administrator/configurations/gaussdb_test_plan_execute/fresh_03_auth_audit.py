#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
PLAN_DIR = EXECUTE_DIR.parent / "gaussdb_test_plan"
PLAN_FILES = (PLAN_DIR / "03_auth_token_session.md", PLAN_DIR / "03_auth_supplement.md")
RUNNER_PATH = EXECUTE_DIR / "fresh_03_auth.py"
EVIDENCE_DIR = evidence_dir("03_authentication")
AUDIT_PATH = EVIDENCE_DIR / "coverage_audit.json"
CASE_PATTERN = re.compile(r"^### (TC-[A-Z0-9-]+-\d{3}):", re.MULTILINE)
MAIN_CASE_PATTERN = re.compile(r"^TC-AT-\d{3}$")
SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "beta",
    "api_key",
    "access_key",
    "authorization",
    "cookie",
)
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


def scan_secret_matches(root: Path, secrets: set[str]) -> dict[str, int]:
    match_count = 0
    files_with_matches = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        file_count = sum(text.count(secret) for secret in secrets if secret)
        if file_count:
            files_with_matches += 1
            match_count += file_count
    return {
        "known_secret_match_count": match_count,
        "files_with_matches": files_with_matches,
    }


def _plan_case_ids() -> list[str]:
    result: list[str] = []
    for path in PLAN_FILES:
        result.extend(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))
    if len(result) != len(set(result)):
        raise ValueError("duplicate authentication case ID in plans")
    return result


def _runner_case_ids() -> set[str]:
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"), filename=str(RUNNER_PATH))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RUNNERS" for target in node.targets):
            if not isinstance(node.value, ast.Dict):
                raise ValueError("RUNNERS is not a dict literal")
            keys = {str(key.value) for key in node.value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
            return keys
    raise ValueError("RUNNERS assignment was not found")


def _collect_runtime_secrets() -> set[str]:
    secrets: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        lowered = key.lower()
        if isinstance(value, dict):
            for item_key, item_value in value.items():
                visit(item_value, str(item_key))
            return
        if isinstance(value, list):
            for item in value:
                visit(item, key)
            return
        if any(part in lowered for part in SENSITIVE_KEY_PARTS):
            text = str(value)
            if len(text) >= 8 and text not in {"<redacted>", "None"}:
                secrets.add(text)

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
        except (ValueError, OSError):
            continue
    return secrets


def _scan_pattern(root: Path, pattern: re.Pattern[str], suffix: str | None = None) -> dict[str, int]:
    matches = 0
    files = 0
    for path in root.rglob("*"):
        if not path.is_file() or (suffix is not None and path.suffix != suffix):
            continue
        count = len(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))
        if count:
            matches += count
            files += 1
    return {"match_count": matches, "files_with_matches": files}


def _plaintext_sensitive_json_fields() -> dict[str, int]:
    field_count = 0
    files_with_fields = 0

    def count_value(value: Any, key: str = "") -> int:
        lowered = key.lower()
        if any(part in lowered for part in SENSITIVE_KEY_PARTS):
            if isinstance(value, dict) and value.get("redacted") is True:
                return 0
            return 1
        if isinstance(value, dict):
            return sum(count_value(item, str(item_key)) for item_key, item in value.items())
        if isinstance(value, list):
            return sum(count_value(item, key) for item in value)
        return 0

    for path in EVIDENCE_DIR.rglob("*.json"):
        if path == AUDIT_PATH:
            continue
        try:
            count = count_value(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            count = 1
        if count:
            field_count += count
            files_with_fields += 1
    return {
        "plaintext_sensitive_field_count": field_count,
        "files_with_plaintext_sensitive_fields": files_with_fields,
    }


def _forbidden_source_reference_count() -> int:
    source_paths = (
        EXECUTE_DIR / "fresh_03_auth.py",
        EXECUTE_DIR / "fresh_03_auth_frontend.test.tsx",
        EXECUTE_DIR / "fresh_03_auth_frontend.jest.config.cjs",
        EXECUTE_DIR / "fresh_active_required_server.py",
        EXECUTE_DIR / "fresh_auth_protocol_setup.py",
        EXECUTE_DIR / "fresh_auth_protocol_renew.py",
        EXECUTE_DIR / "fresh_auth_protocol_stub.py",
    )
    forbidden = (
        "gaussdb_test_plan_execute" + "_bak",
        "gaussdb" + "_test/",
        "mysql_control" + "_test/",
    )
    return sum(text.count(marker) for path in source_paths for text in (path.read_text(encoding="utf-8"),) for marker in forbidden)


def run_audit() -> dict[str, Any]:
    planned = _plan_case_ids()
    planned_set = set(planned)
    runner_set = _runner_case_ids()
    evidence_paths = sorted(EVIDENCE_DIR.glob("TC-AT*.json"))
    evidence_ids = {path.stem for path in evidence_paths}
    records: list[dict[str, Any]] = []
    validation_errors = 0
    for path in evidence_paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        try:
            validate_case_record(record, path.stem)
        except (ValueError, TypeError):
            validation_errors += 1
        records.append(record)

    pair_counts = Counter(str(record.get("pair_status")) for record in records)
    group_counts = Counter(str(group.get("status")) for record in records for group in record.get("groups", []))
    main_records = [record for record in records if MAIN_CASE_PATTERN.fullmatch(str(record.get("case_id")))]
    supplement_records = [record for record in records if record not in main_records]
    main_counts = Counter(str(record.get("pair_status")) for record in main_records)
    supplement_counts = Counter(str(record.get("pair_status")) for record in supplement_records)
    finding_ids = sorted({str(finding.get("id")) for record in records for group in record.get("groups", []) for finding in group.get("findings", []) if finding.get("id")})

    private_files = [path for path in EVIDENCE_DIR.rglob("*") if path.is_file()]
    private_dirs = [EVIDENCE_DIR] + [path for path in EVIDENCE_DIR.rglob("*") if path.is_dir()]
    file_mode_violations = sum((path.stat().st_mode & 0o777) != 0o600 for path in private_files)
    dir_mode_violations = sum((path.stat().st_mode & 0o777) != 0o700 for path in private_dirs)
    secrets = _collect_runtime_secrets()
    all_secret_scan = scan_secret_matches(EVIDENCE_DIR, secrets)
    structured_secret_scan = {
        "known_secret_match_count": 0,
        "files_with_matches": 0,
    }
    for path in EVIDENCE_DIR.rglob("*.json"):
        text = path.read_text(encoding="utf-8", errors="replace")
        count = sum(text.count(secret) for secret in secrets if secret)
        if count:
            structured_secret_scan["known_secret_match_count"] += count
            structured_secret_scan["files_with_matches"] += 1
    sk_all = _scan_pattern(EVIDENCE_DIR, SK_PATTERN)
    sk_structured = _scan_pattern(EVIDENCE_DIR, SK_PATTERN, ".json")
    public_document_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            EXECUTE_DIR / "03_auth_token_session_report.md",
            EXECUTE_DIR / "PROGRESS.md",
        )
    )
    public_document_secret_matches = sum(public_document_text.count(secret) for secret in secrets if secret)
    public_document_sk_matches = len(SK_PATTERN.findall(public_document_text))
    plaintext_fields = _plaintext_sensitive_json_fields()
    forbidden_references = _forbidden_source_reference_count()

    coverage_ok = (
        len(planned) == 67 and planned_set == runner_set == evidence_ids and validation_errors == 0 and len(records) == 67 and sum(pair_counts.values()) == 67 and sum(group_counts.values()) == 134
    )
    evidence_hygiene_ok = (
        file_mode_violations == 0
        and dir_mode_violations == 0
        and structured_secret_scan["known_secret_match_count"] == 0
        and sk_structured["match_count"] == 0
        and public_document_secret_matches == 0
        and public_document_sk_matches == 0
        and plaintext_fields["plaintext_sensitive_field_count"] == 0
        and forbidden_references == 0
    )
    return {
        "batch_id": BATCH_ID,
        "audit_pass": coverage_ok and evidence_hygiene_ok,
        "coverage_pass": coverage_ok,
        "evidence_hygiene_pass": evidence_hygiene_ok,
        "planned_case_count": len(planned),
        "runner_case_count": len(runner_set),
        "evidence_case_count": len(evidence_ids),
        "missing_runner_ids": sorted(planned_set - runner_set),
        "extra_runner_ids": sorted(runner_set - planned_set),
        "missing_evidence_ids": sorted(planned_set - evidence_ids),
        "extra_evidence_ids": sorted(evidence_ids - planned_set),
        "case_validation_error_count": validation_errors,
        "group_record_count": sum(group_counts.values()),
        "pair_status_counts": dict(sorted(pair_counts.items())),
        "group_status_counts": dict(sorted(group_counts.items())),
        "main_plan_status_counts": dict(sorted(main_counts.items())),
        "supplement_status_counts": dict(sorted(supplement_counts.items())),
        "finding_ids": finding_ids,
        "finding_id_count": len(finding_ids),
        "private_file_count": len(private_files),
        "private_directory_count": len(private_dirs),
        "file_mode_violation_count": file_mode_violations,
        "directory_mode_violation_count": dir_mode_violations,
        "runtime_secret_value_count": len(secrets),
        "all_private_evidence_known_secret_scan": all_secret_scan,
        "structured_json_known_secret_scan": structured_secret_scan,
        "all_private_evidence_sk_pattern_scan": sk_all,
        "structured_json_sk_pattern_scan": sk_structured,
        "public_document_known_secret_match_count": public_document_secret_matches,
        "public_document_sk_pattern_match_count": public_document_sk_matches,
        **plaintext_fields,
        "forbidden_backup_source_reference_count": forbidden_references,
    }


def _write_result(result: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = AUDIT_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(AUDIT_PATH)
    AUDIT_PATH.chmod(0o600)


def main() -> None:
    _write_result(run_audit())
    result = run_audit()
    _write_result(result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
