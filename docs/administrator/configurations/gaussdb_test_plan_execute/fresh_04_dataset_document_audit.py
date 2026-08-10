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
PLAN_DIR = EXECUTE_DIR.parent / "gaussdb_test_plan"
PLAN_FILES = (
    PLAN_DIR / "04_dataset_document.md",
    PLAN_DIR / "04_dataset_document_supplement.md",
)
RUNNER_PATH = EXECUTE_DIR / "fresh_04_dataset_document.py"
EVIDENCE_DIR = evidence_dir("04_dataset_document")
AUDIT_PATH = EVIDENCE_DIR / "coverage_audit.json"
REPORT_PATH = EXECUTE_DIR / "04_dataset_document_report.md"
CASE_PATTERN = re.compile(r"^### (TC-DD(?:-[A-Z0-9]+)*-\d{3}):", re.MULTILINE)
MAIN_CASE_PATTERN = re.compile(r"^TC-DD-\d{3}$")
SK_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "api_key",
    "access_key",
    "authorization",
    "cookie",
    "token",
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


def scan_secret_matches(root: Path, secrets: set[str], *, suffix: str | None = None) -> dict[str, int]:
    match_count = 0
    files_with_matches = 0
    for path in root.rglob("*"):
        if not path.is_file() or path == AUDIT_PATH or (suffix is not None and path.suffix != suffix):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        count = sum(text.count(secret) for secret in secrets if secret)
        if count:
            match_count += count
            files_with_matches += 1
    return {
        "known_secret_match_count": match_count,
        "files_with_matches": files_with_matches,
    }


def _plan_case_ids() -> list[str]:
    result: list[str] = []
    for path in PLAN_FILES:
        result.extend(CASE_PATTERN.findall(path.read_text(encoding="utf-8")))
    if len(result) != len(set(result)):
        raise ValueError("duplicate dataset/document case ID in plans")
    return result


def _runner_case_ids() -> set[str]:
    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"), filename=str(RUNNER_PATH))
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "RUNNERS" for target in node.targets):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "RUNNERS":
            value = node.value
        if not isinstance(value, ast.Dict):
            continue
        return {str(key.value) for key in value.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
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


def _forbidden_source_reference_count() -> int:
    source_paths = (
        RUNNER_PATH,
        EXECUTE_DIR / "test_fresh_04_dataset_document.py",
        Path(__file__),
        EXECUTE_DIR / "test_fresh_04_dataset_document_audit.py",
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
    evidence_paths = sorted(EVIDENCE_DIR.glob("TC-DD*.json"))
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
    per_group_counts = {
        group_name: dict(sorted(Counter(str(group.get("status")) for record in records for group in record.get("groups", []) if group.get("group") == group_name).items()))
        for group_name in ("control", "experiment")
    }
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
    secret_scan = scan_secret_matches(EVIDENCE_DIR, secrets)
    structured_secret_scan = scan_secret_matches(EVIDENCE_DIR, secrets, suffix=".json")
    sk_scan = _scan_pattern(EVIDENCE_DIR, SK_PATTERN)
    public_paths = [EXECUTE_DIR / "PROGRESS.md"]
    if REPORT_PATH.exists():
        public_paths.append(REPORT_PATH)
    public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_paths)
    public_secret_matches = sum(public_text.count(secret) for secret in secrets if secret)
    public_sk_matches = len(SK_PATTERN.findall(public_text))
    forbidden_references = _forbidden_source_reference_count()

    coverage_ok = (
        len(planned) == 98 and planned_set == runner_set == evidence_ids and validation_errors == 0 and len(records) == 98 and sum(pair_counts.values()) == 98 and sum(group_counts.values()) == 196
    )
    hygiene_ok = (
        file_mode_violations == 0
        and dir_mode_violations == 0
        and structured_secret_scan["known_secret_match_count"] == 0
        and sk_scan["match_count"] == 0
        and public_secret_matches == 0
        and public_sk_matches == 0
        and forbidden_references == 0
    )
    return {
        "batch_id": BATCH_ID,
        "audit_pass": coverage_ok and hygiene_ok,
        "coverage_pass": coverage_ok,
        "evidence_hygiene_pass": hygiene_ok,
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
        "per_group_status_counts": per_group_counts,
        "main_plan_case_count": len(main_records),
        "main_plan_status_counts": dict(sorted(main_counts.items())),
        "supplement_case_count": len(supplement_records),
        "supplement_status_counts": dict(sorted(supplement_counts.items())),
        "finding_ids": finding_ids,
        "finding_id_count": len(finding_ids),
        "private_file_count": len(private_files),
        "private_directory_count": len(private_dirs),
        "file_mode_violation_count": file_mode_violations,
        "directory_mode_violation_count": dir_mode_violations,
        "runtime_secret_value_count": len(secrets),
        "private_evidence_known_secret_scan": secret_scan,
        "structured_json_known_secret_scan": structured_secret_scan,
        "private_evidence_sk_pattern_scan": sk_scan,
        "public_document_known_secret_match_count": public_secret_matches,
        "public_document_sk_pattern_match_count": public_sk_matches,
        "forbidden_backup_source_reference_count": forbidden_references,
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
    _write_result(run_audit())
    result = run_audit()
    _write_result(result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
