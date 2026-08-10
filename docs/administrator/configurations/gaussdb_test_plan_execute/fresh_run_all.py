#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import re
import subprocess
import sys
import traceback
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    EVIDENCE_ROOT,
    EXPLICIT_RUN,
    RUNTIME_DIR,
    RUN_ID_ENV,
)


EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
PLAN_DIR = EXECUTE_DIR.parent / "gaussdb_test_plan"
CASE_PATTERN = re.compile(r"^###\s+(TC-[A-Z0-9-]+):", re.MULTILINE)
GROUP_ORDER = ("control", "experiment")
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}
STATUS_PATH = EVIDENCE_ROOT / "formal_execution_status.json"
DIAGNOSTIC_DIR = EVIDENCE_ROOT / "formal_execution_diagnostics"
GROUP_LOG_DIR = RUNTIME_DIR / "formal_group_logs"
TOKEN_PATTERN = re.compile(r"(?i)(?:Bearer\s+|ragflow-|sk-)[A-Za-z0-9._~+/=-]{8,}")


@dataclass(frozen=True)
class RunnerSpec:
    script: str
    section: str
    plan_files: tuple[str, ...]


RUNNER_SPECS = (
    RunnerSpec("fresh_01_startup_migration.py", "01_startup_migration", ("01_startup_migration.md",)),
    RunnerSpec("fresh_02_user_management.py", "02_user_management", ("02_user_management.md",)),
    RunnerSpec(
        "fresh_02_user_management_admin_supplement.py",
        "02_user_management_admin_supplement",
        ("02_user_management_admin_supplement.md",),
    ),
    RunnerSpec(
        "fresh_03_auth.py",
        "03_authentication",
        ("03_auth_token_session.md", "03_auth_supplement.md"),
    ),
    RunnerSpec(
        "fresh_04_dataset_document.py",
        "04_dataset_document",
        ("04_dataset_document.md", "04_dataset_document_supplement.md"),
    ),
    RunnerSpec(
        "fresh_05_chat_session_agent.py",
        "05_chat_session_agent",
        ("05_chat_session_agent.md", "05_chat_agent_supplement.md"),
    ),
    RunnerSpec(
        "fresh_06_memory_metadata.py",
        "06_memory_metadata",
        ("06_memory_metadata.md", "06_memory_supplement.md"),
    ),
    RunnerSpec("fresh_07_memory_store.py", "07_memory_store", ("07_memory_store_e2e.md",)),
    RunnerSpec(
        "fresh_08_file.py",
        "08_file",
        ("08_file_management.md", "08_file_supplement.md"),
    ),
    RunnerSpec("fresh_09_connector.py", "09_connector", ("09_connector_system.md",)),
    RunnerSpec("fresh_10_fault_recovery.py", "10_fault_recovery", ("10_fault_recovery.md",)),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def case_ids_for_spec(spec: RunnerSpec) -> list[str]:
    result: list[str] = []
    for filename in spec.plan_files:
        result.extend(CASE_PATTERN.findall((PLAN_DIR / filename).read_text(encoding="utf-8")))
    return result


def execution_manifest() -> list[tuple[RunnerSpec, str]]:
    manifest = [(spec, case_id) for spec in RUNNER_SPECS for case_id in case_ids_for_spec(spec)]
    case_ids = [case_id for _spec, case_id in manifest]
    if len(case_ids) != 643 or len(set(case_ids)) != 643:
        raise ValueError(f"expected 643 unique current cases, got total={len(case_ids)} unique={len(set(case_ids))}")
    return manifest


def validate_case_evidence(payload: dict[str, Any], case_id: str) -> tuple[str, str]:
    if payload.get("case_id") != case_id:
        raise ValueError("case identity mismatch")
    if payload.get("group_order") != list(GROUP_ORDER):
        raise ValueError("declared group order mismatch")
    groups = payload.get("groups")
    if not isinstance(groups, list) or [item.get("group") for item in groups] != list(GROUP_ORDER):
        raise ValueError("recorded group order mismatch")
    statuses = tuple(str(item.get("status")) for item in groups)
    if any(status not in VALID_STATUSES for status in statuses):
        raise ValueError("invalid group status")
    expected_pair = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    if payload.get("pair_status") != expected_pair:
        raise ValueError("pair status mismatch")
    return statuses


def case_decision(statuses: tuple[str, str], returncode: int) -> tuple[bool, str]:
    if statuses == ("PASS", "PASS"):
        return (returncode == 0, "pass" if returncode == 0 else "runner_nonzero_after_pass")
    if statuses == ("FAIL", "FAIL"):
        return True, "common_double_failure"
    if statuses == ("BLOCKED", "BLOCKED"):
        return True, "common_double_blocked"
    if statuses == ("PASS", "FAIL"):
        return False, "experiment_only_failure"
    if statuses == ("FAIL", "PASS"):
        return True, "control_baseline_failure"
    return False, "asymmetric_or_single_group_blocked"


def case_decision_with_allowlist(
    case_id: str,
    statuses: tuple[str, str],
    returncode: int,
    allowed_experiment_failures: set[str],
    allowed_asymmetric_blocked: set[str] | None = None,
) -> tuple[bool, str]:
    should_continue, reason = case_decision(statuses, returncode)
    if not should_continue and reason == "experiment_only_failure" and case_id in allowed_experiment_failures:
        return True, "accepted_experiment_only_failure"
    if not should_continue and reason == "asymmetric_or_single_group_blocked" and case_id in (allowed_asymmetric_blocked or set()):
        return True, "accepted_asymmetric_blocked"
    return should_continue, reason


def safe_tail(value: str, limit: int = 4000) -> str:
    return TOKEN_PATTERN.sub("<redacted>", value[-limit:])


def atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def evidence_path(spec: RunnerSpec, case_id: str) -> Path:
    return EVIDENCE_ROOT / spec.section / f"{case_id}.json"


def load_group_runners(spec: RunnerSpec, expected_case_ids: list[str]) -> tuple[Any, dict[str, Any]]:
    module_name = f"fresh_group_{Path(spec.script).stem}_{BATCH_ID}"
    module_spec = importlib.util.spec_from_file_location(module_name, EXECUTE_DIR / spec.script)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load grouped runner: {spec.script}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    declared = list(module.CASE_TITLES)
    if declared != expected_case_ids:
        raise ValueError(f"grouped runner case order mismatch: {spec.script}")
    getter = getattr(module, "get_runners", None)
    runners = getter() if callable(getter) else getattr(module, "RUNNERS", None)
    if not isinstance(runners, dict) or set(runners) != set(expected_case_ids):
        raise ValueError(f"grouped runner map mismatch: {spec.script}")
    if not all(callable(item) for item in runners.values()):
        raise ValueError(f"grouped runner contains non-callable entries: {spec.script}")
    return module, runners


def group_modules_to_remove(before_modules: set[str], current_modules: set[str]) -> set[str]:
    return {name for name in current_modules - before_modules if name.startswith("fresh_")}


def completed_prefix(
    manifest: list[tuple[RunnerSpec, str]],
    allowed_experiment_failures: set[str],
    allowed_asymmetric_blocked: set[str] | None = None,
) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    found_gap = False
    for spec, case_id in manifest:
        path = evidence_path(spec, case_id)
        if not path.exists():
            found_gap = True
            continue
        if found_gap:
            raise ValueError(f"non-prefix evidence exists for {case_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        statuses = validate_case_evidence(payload, case_id)
        should_continue, reason = case_decision_with_allowlist(
            case_id,
            statuses,
            0,
            allowed_experiment_failures,
            allowed_asymmetric_blocked,
        )
        if not should_continue:
            raise ValueError(f"existing evidence is a stop condition: {case_id}: {reason}")
        completed.append({"case_id": case_id, "statuses": list(statuses), "reason": reason})
    return completed


def status_payload(
    *,
    completed: list[dict[str, Any]],
    total: int,
    state: str,
    current_case: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    signatures = Counter("/".join(item["statuses"]) for item in completed)
    return {
        "batch_id": BATCH_ID,
        "state": state,
        "updated_at": utc_now(),
        "completed_count": len(completed),
        "total_count": total,
        "current_case": current_case,
        "stop_reason": reason,
        "group_signature_counts": dict(sorted(signatures.items())),
        "last_completed_case": completed[-1]["case_id"] if completed else None,
    }


def run_all(
    *,
    resume: bool,
    allowed_experiment_failures: set[str],
    allowed_asymmetric_blocked: set[str] | None = None,
) -> int:
    if not EXPLICIT_RUN or os.environ.get(RUN_ID_ENV) != BATCH_ID:
        raise ValueError(f"{RUN_ID_ENV} must be explicitly set to the current batch")
    manifest = execution_manifest()
    completed = completed_prefix(
        manifest,
        allowed_experiment_failures,
        allowed_asymmetric_blocked,
    )
    if completed and not resume:
        raise FileExistsError("current run already contains formal case evidence; use --resume")
    start_index = len(completed)
    atomic_private_json(
        STATUS_PATH,
        status_payload(
            completed=completed,
            total=len(manifest),
            state="running",
            current_case=manifest[start_index][1] if start_index < len(manifest) else None,
        ),
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    for index, (spec, case_id) in enumerate(manifest[start_index:], start_index + 1):
        path = evidence_path(spec, case_id)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite formal evidence: {path}")
        print(
            f"START {index:03d}/643 {case_id} runner={spec.script}",
            flush=True,
        )
        process = subprocess.run(
            [sys.executable, str(EXECUTE_DIR / spec.script), "--case", case_id],
            cwd=PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if not path.is_file():
            reason = "missing_case_evidence"
            atomic_private_json(
                DIAGNOSTIC_DIR / f"{case_id}.json",
                {
                    "batch_id": BATCH_ID,
                    "case_id": case_id,
                    "returncode": process.returncode,
                    "stdout_tail": safe_tail(process.stdout),
                    "stderr_tail": safe_tail(process.stderr),
                },
            )
            atomic_private_json(
                STATUS_PATH,
                status_payload(
                    completed=completed,
                    total=len(manifest),
                    state="stopped",
                    current_case=case_id,
                    reason=reason,
                ),
            )
            print(f"STOP {case_id} reason={reason}", flush=True)
            return 3
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            statuses = validate_case_evidence(payload, case_id)
        except Exception as exc:
            reason = f"invalid_case_evidence:{type(exc).__name__}"
            atomic_private_json(
                STATUS_PATH,
                status_payload(
                    completed=completed,
                    total=len(manifest),
                    state="stopped",
                    current_case=case_id,
                    reason=reason,
                ),
            )
            print(f"STOP {case_id} reason={reason}", flush=True)
            return 3
        should_continue, reason = case_decision_with_allowlist(
            case_id,
            statuses,
            process.returncode,
            allowed_experiment_failures,
            allowed_asymmetric_blocked,
        )
        completed.append({"case_id": case_id, "statuses": list(statuses), "reason": reason})
        state = "running" if should_continue else "stopped"
        atomic_private_json(
            STATUS_PATH,
            status_payload(
                completed=completed,
                total=len(manifest),
                state=state,
                current_case=case_id,
                reason=None if should_continue else reason,
            ),
        )
        print(
            f"DONE {index:03d}/643 {case_id} statuses={statuses[0]}/{statuses[1]} decision={reason}",
            flush=True,
        )
        if not should_continue:
            return 2
    atomic_private_json(
        STATUS_PATH,
        status_payload(
            completed=completed,
            total=len(manifest),
            state="complete",
            current_case=None,
        ),
    )
    return 0


def run_all_grouped(
    *,
    resume: bool,
    allowed_experiment_failures: set[str],
    allowed_asymmetric_blocked: set[str] | None = None,
) -> int:
    if not EXPLICIT_RUN or os.environ.get(RUN_ID_ENV) != BATCH_ID:
        raise ValueError(f"{RUN_ID_ENV} must be explicitly set to the current batch")
    manifest = execution_manifest()
    completed = completed_prefix(
        manifest,
        allowed_experiment_failures,
        allowed_asymmetric_blocked,
    )
    if completed and not resume:
        raise FileExistsError("current run already contains formal case evidence; use --resume")
    start_index = len(completed)
    atomic_private_json(
        STATUS_PATH,
        status_payload(
            completed=completed,
            total=len(manifest),
            state="running",
            current_case=manifest[start_index][1] if start_index < len(manifest) else None,
        ),
    )
    GROUP_LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    GROUP_LOG_DIR.chmod(0o700)
    offset = 0
    for spec in RUNNER_SPECS:
        group_case_ids = case_ids_for_spec(spec)
        indexed_cases = [(offset + local_index, case_id) for local_index, case_id in enumerate(group_case_ids, 1) if offset + local_index > start_index]
        offset += len(group_case_ids)
        if not indexed_cases:
            continue
        log_path = GROUP_LOG_DIR / f"{spec.section}.log"
        with log_path.open("a", encoding="utf-8") as group_log:
            log_path.chmod(0o600)
            before_modules = set(sys.modules)
            try:
                with redirect_stdout(group_log), redirect_stderr(group_log):
                    module, runners = load_group_runners(spec, group_case_ids)
            except Exception as exc:
                traceback.print_exc(file=group_log)
                reason = f"group_load_failure:{type(exc).__name__}"
                atomic_private_json(
                    STATUS_PATH,
                    status_payload(
                        completed=completed,
                        total=len(manifest),
                        state="stopped",
                        current_case=indexed_cases[0][1],
                        reason=reason,
                    ),
                )
                print(f"STOP {indexed_cases[0][1]} reason={reason}", flush=True)
                return 3
            print(
                f"GROUP_START section={spec.section} remaining={len(indexed_cases)}",
                flush=True,
            )
            try:
                for index, case_id in indexed_cases:
                    path = evidence_path(spec, case_id)
                    if path.exists():
                        raise FileExistsError(f"refusing to overwrite formal evidence: {path}")
                    print(f"START {index:03d}/643 {case_id}", flush=True)
                    runner_error: Exception | None = None
                    try:
                        with redirect_stdout(group_log), redirect_stderr(group_log):
                            runners[case_id]()
                    except Exception as exc:
                        runner_error = exc
                        traceback.print_exc(file=group_log)
                    if not path.is_file():
                        reason = f"runner_exception:{type(runner_error).__name__}" if runner_error is not None else "missing_case_evidence"
                        atomic_private_json(
                            DIAGNOSTIC_DIR / f"{case_id}.json",
                            {
                                "batch_id": BATCH_ID,
                                "case_id": case_id,
                                "error_type": type(runner_error).__name__ if runner_error is not None else None,
                            },
                        )
                        atomic_private_json(
                            STATUS_PATH,
                            status_payload(
                                completed=completed,
                                total=len(manifest),
                                state="stopped",
                                current_case=case_id,
                                reason=reason,
                            ),
                        )
                        print(f"STOP {case_id} reason={reason}", flush=True)
                        return 3
                    try:
                        payload = json.loads(path.read_text(encoding="utf-8"))
                        statuses = validate_case_evidence(payload, case_id)
                    except Exception as exc:
                        reason = f"invalid_case_evidence:{type(exc).__name__}"
                        atomic_private_json(
                            STATUS_PATH,
                            status_payload(
                                completed=completed,
                                total=len(manifest),
                                state="stopped",
                                current_case=case_id,
                                reason=reason,
                            ),
                        )
                        print(f"STOP {case_id} reason={reason}", flush=True)
                        return 3
                    if runner_error is not None:
                        should_continue = False
                        reason = f"runner_exception:{type(runner_error).__name__}"
                    else:
                        should_continue, reason = case_decision_with_allowlist(
                            case_id,
                            statuses,
                            0,
                            allowed_experiment_failures,
                            allowed_asymmetric_blocked,
                        )
                    completed.append(
                        {
                            "case_id": case_id,
                            "statuses": list(statuses),
                            "reason": reason,
                        }
                    )
                    atomic_private_json(
                        STATUS_PATH,
                        status_payload(
                            completed=completed,
                            total=len(manifest),
                            state="running" if should_continue else "stopped",
                            current_case=case_id,
                            reason=None if should_continue else reason,
                        ),
                    )
                    print(
                        f"DONE {index:03d}/643 {case_id} statuses={statuses[0]}/{statuses[1]} decision={reason}",
                        flush=True,
                    )
                    if not should_continue:
                        return 2
            finally:
                del runners
                del module
                for module_name in group_modules_to_remove(before_modules, set(sys.modules)):
                    sys.modules.pop(module_name, None)
                gc.collect()
            print(f"GROUP_DONE section={spec.section}", flush=True)
    atomic_private_json(
        STATUS_PATH,
        status_payload(
            completed=completed,
            total=len(manifest),
            state="complete",
            current_case=None,
        ),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all 643 current GaussDB cases in order")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--grouped", action="store_true")
    parser.add_argument(
        "--allow-experiment-failure",
        action="append",
        default=[],
        metavar="CASE_ID",
        help="continue past an explicitly reviewed PASS/FAIL case",
    )
    parser.add_argument(
        "--allow-asymmetric-blocked",
        action="append",
        default=[],
        metavar="CASE_ID",
        help="continue past an explicitly reviewed asymmetric BLOCKED case",
    )
    args = parser.parse_args()
    allowed_experiment_failures = set(args.allow_experiment_failure)
    allowed_asymmetric_blocked = set(args.allow_asymmetric_blocked)
    if args.grouped:
        return run_all_grouped(
            resume=args.resume,
            allowed_experiment_failures=allowed_experiment_failures,
            allowed_asymmetric_blocked=allowed_asymmetric_blocked,
        )
    return run_all(
        resume=args.resume,
        allowed_experiment_failures=allowed_experiment_failures,
        allowed_asymmetric_blocked=allowed_asymmetric_blocked,
    )


if __name__ == "__main__":
    raise SystemExit(main())
