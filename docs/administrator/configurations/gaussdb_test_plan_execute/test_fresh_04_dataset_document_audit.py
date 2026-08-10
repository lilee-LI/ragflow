import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_04_dataset_document_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_04_dataset_document_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_pair_status_prioritizes_fail_then_blocked():
    module = load_module()

    assert module.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert module.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert module.expected_pair_status(["FAIL", "BLOCKED"]) == "FAIL"


def test_validate_case_record_requires_control_then_experiment_and_matching_pair():
    module = load_module()
    record = {
        "case_id": "TC-DD-001",
        "group_order": ["control", "experiment"],
        "pair_status": "PASS",
        "groups": [
            {
                "group": "control",
                "status": "PASS",
                "recorded_at": "2026-07-13T10:00:00+08:00",
            },
            {
                "group": "experiment",
                "status": "PASS",
                "recorded_at": "2026-07-13T10:00:01+08:00",
            },
        ],
    }

    module.validate_case_record(record, "TC-DD-001")
    bad = json.loads(json.dumps(record))
    bad["groups"].reverse()
    try:
        module.validate_case_record(bad, "TC-DD-001")
    except ValueError as exc:
        assert "order" in str(exc)
    else:
        raise AssertionError("reversed group order must be rejected")


def test_scan_secret_matches_returns_counts_without_secret_values(tmp_path):
    module = load_module()
    secret = "private-runtime-value"
    (tmp_path / "one.json").write_text(json.dumps({"value": secret}), encoding="utf-8")
    (tmp_path / "two.json").write_text("{}", encoding="utf-8")

    result = module.scan_secret_matches(tmp_path, {secret})

    assert result == {"known_secret_match_count": 1, "files_with_matches": 1}
    assert secret not in json.dumps(result)


def test_structured_secret_scan_excludes_sealed_raw_logs(tmp_path):
    module = load_module()
    secret = "private-runtime-value"
    (tmp_path / "sealed.log").write_text(secret, encoding="utf-8")
    (tmp_path / "safe.json").write_text("{}", encoding="utf-8")

    result = module.scan_secret_matches(tmp_path, {secret}, suffix=".json")

    assert result == {"known_secret_match_count": 0, "files_with_matches": 0}
