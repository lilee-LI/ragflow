import importlib.util
import json
import stat
import sys
from pathlib import Path

import pytest


EXECUTE_DIR = Path(__file__).resolve().parent
RUNNER_PATH = EXECUTE_DIR / "fresh_10_fault_recovery.py"
COMMON_PATH = EXECUTE_DIR / "fresh_10_fault_recovery_common.py"
EXPECTED_IDS = [
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
]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_runner_registry_matches_cleaned_plan_exactly():
    runner = load_module(RUNNER_PATH, "fresh_10_fault_recovery_contract")

    assert list(runner.CASE_TITLES) == EXPECTED_IDS
    assert list(runner.RUNNERS) == EXPECTED_IDS
    assert list(runner.EXPECTED_CASE_IDS) == EXPECTED_IDS
    assert set(runner.CASE_DOMAINS) == set(EXPECTED_IDS)
    assert all(callable(item) for item in runner.RUNNERS.values())
    assert set(runner.DOMAIN_MODULES) == {
        "error_retry",
        "connection_recovery",
        "boundary_transaction",
        "memory_dialect",
    }


def test_runner_rejects_unknown_or_removed_case():
    runner = load_module(RUNNER_PATH, "fresh_10_fault_recovery_unknown_case")

    with pytest.raises(ValueError, match="unknown retained fault-recovery case"):
        runner.case_family("TC-FR-013")
    with pytest.raises(ValueError, match="unknown retained fault-recovery case"):
        runner.case_family("TC-FR-055")


def test_runner_bootstraps_project_root_for_direct_cli_execution():
    runner = load_module(RUNNER_PATH, "fresh_10_fault_recovery_cli_path")

    assert runner.PROJECT_ROOT == EXECUTE_DIR.parents[3]
    assert str(runner.PROJECT_ROOT) in sys.path


def test_paired_evidence_is_ordered_private_and_redacted(tmp_path):
    common = load_module(COMMON_PATH, "fresh_10_fault_recovery_common_contract")
    calls = []

    def execute_group(group):
        calls.append(group)
        return common.case_group_result(
            "PASS",
            [{"name": "probe", "password": "must-not-survive"}],
            {"connection": "recovered", "token": "must-not-survive"},
        )

    target = tmp_path / "private" / "TC-FR-001.json"
    record = common.run_paired_case("TC-FR-001", "contract", execute_group, evidence_path=target)

    assert calls == ["control", "experiment"]
    assert record["group_order"] == ["control", "experiment"]
    assert record["pair_status"] == "PASS"
    assert [item["group"] for item in record["groups"]] == calls
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    serialized = target.read_text(encoding="utf-8")
    assert "must-not-survive" not in serialized
    payload = json.loads(serialized)
    assert payload["groups"][0]["steps"][0]["password"]["redacted"] is True
    assert payload["groups"][0]["oracle"]["token"]["redacted"] is True


def test_pair_status_prefers_fail_then_blocked():
    common = load_module(COMMON_PATH, "fresh_10_fault_recovery_status_contract")

    assert common.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert common.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert common.expected_pair_status(["FAIL", "BLOCKED"]) == "FAIL"
