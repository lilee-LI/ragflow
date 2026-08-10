#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_runner_result import pair_exit_code


EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PLAN_FILE = EXECUTE_DIR.parent / "gaussdb_test_plan" / "10_fault_recovery.md"
CASE_PATTERN = re.compile(r"^### (TC-FR-\d{3}):\s*(.+)$", re.MULTILINE)
EXPECTED_CASE_IDS = (
    *[f"TC-FR-{number:03d}" for number in range(1, 13)],
    *[f"TC-FR-{number:03d}" for number in range(18, 25)],
    "TC-FR-027",
    "TC-FR-037",
    "TC-FR-038",
    "TC-FR-044",
    "TC-FR-045",
    "TC-FR-051",
    "TC-FR-052",
    "TC-FR-053",
)
CASE_DOMAINS = {
    **{f"TC-FR-{number:03d}": "error_retry" for number in range(1, 13)},
    **{f"TC-FR-{number:03d}": "connection_recovery" for number in range(18, 25)},
    **{
        case_id: "boundary_transaction"
        for case_id in (
            "TC-FR-027",
            "TC-FR-037",
            "TC-FR-038",
            "TC-FR-044",
            "TC-FR-045",
        )
    },
    **{case_id: "memory_dialect" for case_id in ("TC-FR-051", "TC-FR-052", "TC-FR-053")},
}
DOMAIN_MODULES = {
    "error_retry": "fresh_10_error_retry.py",
    "connection_recovery": "fresh_10_connection_recovery.py",
    "boundary_transaction": "fresh_10_boundary_transaction.py",
    "memory_dialect": "fresh_10_memory_dialect.py",
}


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


def _case_titles() -> dict[str, str]:
    result: dict[str, str] = {}
    for case_id, title in CASE_PATTERN.findall(PLAN_FILE.read_text(encoding="utf-8")):
        if case_id in result:
            raise ValueError(f"duplicate current fault-recovery case ID: {case_id}")
        result[case_id] = title.strip()
    if tuple(result) != EXPECTED_CASE_IDS:
        raise ValueError(f"expected current fault-recovery cases {list(EXPECTED_CASE_IDS)}, found {list(result)}")
    return result


CASE_TITLES = _case_titles()
_DOMAIN_RUNNERS: dict[str, dict[str, Callable[[], dict[str, Any]]]] = {}


def case_family(case_id: str) -> str:
    try:
        return CASE_DOMAINS[case_id]
    except KeyError as exc:
        raise ValueError(f"unknown retained fault-recovery case: {case_id}") from exc


def _runners_for_domain(domain: str) -> dict[str, Callable[[], dict[str, Any]]]:
    if domain in _DOMAIN_RUNNERS:
        return _DOMAIN_RUNNERS[domain]
    filename = DOMAIN_MODULES[domain]
    module = _load_module(
        EXECUTE_DIR / filename,
        f"fresh_10_{domain}_runtime",
    )
    provided = module.get_runners(CASE_TITLES)
    expected = [case_id for case_id in EXPECTED_CASE_IDS if CASE_DOMAINS[case_id] == domain]
    if list(provided) != expected or not all(callable(item) for item in provided.values()):
        raise ValueError(f"fault-recovery domain runner mismatch for {domain}: expected={expected}, found={list(provided)}")
    _DOMAIN_RUNNERS[domain] = provided
    return provided


def _run_case(case_id: str) -> dict[str, Any]:
    domain = case_family(case_id)
    return _runners_for_domain(domain)[case_id]()


RUNNERS: dict[str, Callable[[], dict[str, Any]]] = {case_id: (lambda current_id=case_id: _run_case(current_id)) for case_id in EXPECTED_CASE_IDS}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a fresh GaussDB fault-recovery adaptation case")
    parser.add_argument("--case", choices=list(CASE_TITLES), required=True)
    args = parser.parse_args()
    result = RUNNERS[args.case]()
    print(
        json.dumps(
            {
                "case_id": args.case,
                "group_statuses": {item["group"]: item["status"] for item in result["groups"]},
                "pair_status": result["pair_status"],
            },
            sort_keys=True,
        )
    )
    return pair_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
