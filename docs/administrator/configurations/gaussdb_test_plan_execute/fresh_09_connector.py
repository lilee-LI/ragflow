#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


EXECUTE_DIR = Path(__file__).resolve().parent
PLAN_FILE = EXECUTE_DIR.parent / "gaussdb_test_plan" / "09_connector_system.md"
CASE_PATTERN = re.compile(r"^### (TC-CONN-\d{3}):\s*(.+)$", re.MULTILINE)
EXPECTED_CASE_IDS = (
    "TC-CONN-015",
    "TC-CONN-016",
    "TC-CONN-031",
    "TC-CONN-032",
    "TC-CONN-093",
    "TC-CONN-094",
)
CASE_DOMAINS = {
    "TC-CONN-015": "scheduling",
    "TC-CONN-016": "scheduling",
    "TC-CONN-031": "system_health",
    "TC-CONN-032": "system_health",
    "TC-CONN-093": "gaussdb_compatibility",
    "TC-CONN-094": "gaussdb_compatibility",
}
DOMAIN_MODULES = {
    "scheduling": "fresh_09_connector_scheduling.py",
    "system_health": "fresh_09_system_health.py",
    "gaussdb_compatibility": "fresh_09_gaussdb_compatibility.py",
}


def _load_module(path: Path, name: str):
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
            raise ValueError(f"duplicate current Connector case ID: {case_id}")
        result[case_id] = title.strip()
    if tuple(result) != EXPECTED_CASE_IDS:
        raise ValueError(f"expected current Connector cases {list(EXPECTED_CASE_IDS)}, found {list(result)}")
    return result


CASE_TITLES = _case_titles()


def _plan_case_ids() -> list[str]:
    return list(CASE_TITLES)


def case_family(case_id: str) -> str:
    try:
        return CASE_DOMAINS[case_id]
    except KeyError as exc:
        raise ValueError(f"unknown retained Connector case ID: {case_id}") from exc


def _install_domain_runners() -> dict[str, Callable[[], dict[str, Any]]]:
    runners: dict[str, Callable[[], dict[str, Any]]] = {}
    for domain, filename in DOMAIN_MODULES.items():
        module = _load_module(
            EXECUTE_DIR / filename,
            f"fresh_09_{domain}_runtime",
        )
        provided = module.get_runners(CASE_TITLES)
        for case_id, runner in provided.items():
            if case_id not in CASE_TITLES or case_family(case_id) != domain:
                raise ValueError(f"domain module registered an invalid case: {case_id}")
            if case_id in runners:
                raise ValueError(f"duplicate Connector runner: {case_id}")
            runners[case_id] = runner
    missing = [case_id for case_id in CASE_TITLES if case_id not in runners]
    extra = [case_id for case_id in runners if case_id not in CASE_TITLES]
    if missing or extra:
        raise ValueError(f"Connector runner mismatch: missing={missing}, extra={extra}")
    return {case_id: runners[case_id] for case_id in CASE_TITLES}


RUNNERS = _install_domain_runners()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a fresh GaussDB Connector/System adaptation case")
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


if __name__ == "__main__":
    main()
