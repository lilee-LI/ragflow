import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_run_all.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_run_all_tested", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_execution_manifest_maps_all_current_cases_once():
    module = load_module()

    manifest = module.execution_manifest()
    case_ids = [case_id for _spec, case_id in manifest]

    assert len(case_ids) == len(set(case_ids)) == 643
    assert case_ids[0] == "TC-SM-001"
    assert case_ids[-1] == "TC-FR-053"
    assert sum(spec.script == "fresh_09_connector.py" for spec, _ in manifest) == 6
    assert sum(spec.script == "fresh_10_fault_recovery.py" for spec, _ in manifest) == 27


@pytest.mark.parametrize(
    ("statuses", "returncode", "expected"),
    [
        (("PASS", "PASS"), 0, (True, "pass")),
        (("PASS", "PASS"), 1, (False, "runner_nonzero_after_pass")),
        (("FAIL", "FAIL"), 0, (True, "common_double_failure")),
        (("FAIL", "FAIL"), 1, (True, "common_double_failure")),
        (("PASS", "FAIL"), 0, (False, "experiment_only_failure")),
        (("FAIL", "PASS"), 0, (True, "control_baseline_failure")),
        (("BLOCKED", "BLOCKED"), 0, (True, "common_double_blocked")),
        (("PASS", "BLOCKED"), 0, (False, "asymmetric_or_single_group_blocked")),
    ],
)
def test_case_decision_enforces_adaptation_stop_rule(statuses, returncode, expected):
    module = load_module()

    assert module.case_decision(statuses, returncode) == expected


def test_validate_case_evidence_requires_exact_control_experiment_shape():
    module = load_module()
    payload = {
        "case_id": "TC-X-001",
        "group_order": ["control", "experiment"],
        "pair_status": "FAIL",
        "groups": [
            {"group": "control", "status": "FAIL"},
            {"group": "experiment", "status": "FAIL"},
        ],
    }

    assert module.validate_case_evidence(payload, "TC-X-001") == ("FAIL", "FAIL")
    payload["groups"].reverse()
    with pytest.raises(ValueError, match="group order"):
        module.validate_case_evidence(payload, "TC-X-001")


def test_runner_source_has_no_backup_source_reference():
    source = MODULE_PATH.read_text(encoding="utf-8").lower()

    assert "execute_bak" not in source
    assert "mysql_control_test" not in source


def test_group_cleanup_never_evicts_shared_third_party_modules():
    module = load_module()

    result = module.group_modules_to_remove(
        {"sys", "ruamel.yaml"},
        {
            "sys",
            "ruamel.yaml",
            "ruamel.yaml.parser",
            "requests.sessions",
            "fresh_group_runner",
            "fresh_case_evidence_group",
        },
    )

    assert result == {"fresh_group_runner", "fresh_case_evidence_group"}
