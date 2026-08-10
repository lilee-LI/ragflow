import importlib.util
import json
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_03_auth_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_03_auth_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pair_status_precedence_matches_case_recorder():
    module = load_module()

    assert module.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert module.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert module.expected_pair_status(["BLOCKED", "FAIL"]) == "FAIL"


def test_validate_case_requires_control_then_experiment_and_consistent_pair():
    module = load_module()
    record = {
        "case_id": "TC-AT-001",
        "group_order": ["control", "experiment"],
        "pair_status": "FAIL",
        "groups": [
            {
                "group": "control",
                "status": "PASS",
                "recorded_at": "2026-07-13T00:00:00+00:00",
            },
            {
                "group": "experiment",
                "status": "FAIL",
                "recorded_at": "2026-07-13T00:00:01+00:00",
            },
        ],
    }

    module.validate_case_record(record, "TC-AT-001")
    with pytest.raises(ValueError, match="group order"):
        module.validate_case_record(
            {
                **record,
                "groups": list(reversed(record["groups"])),
            },
            "TC-AT-001",
        )


def test_secret_scan_returns_counts_without_returning_secret_values(tmp_path):
    module = load_module()
    secret = "fresh-private-value-123"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "one.log").write_text(f"prefix {secret} suffix", encoding="utf-8")
    (evidence / "two.json").write_text('{"safe": true}', encoding="utf-8")

    result = module.scan_secret_matches(evidence, {secret})

    assert result == {"known_secret_match_count": 1, "files_with_matches": 1}
    assert secret not in json.dumps(result)
