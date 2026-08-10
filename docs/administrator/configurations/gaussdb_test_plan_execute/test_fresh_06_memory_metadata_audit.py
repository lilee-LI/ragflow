import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_06_memory_metadata_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_06_memory_metadata_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_pair_status_prioritizes_fail_then_blocked():
    module = load_module()

    assert module.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert module.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert module.expected_pair_status(["FAIL", "BLOCKED"]) == "FAIL"


def test_status_count_contract_accepts_fresh_outcomes_not_fixed_counts():
    module = load_module()

    assert module.status_count_contract_ok(
        {"PASS": 2, "FAIL": 1},
        {"PASS": 4, "FAIL": 2},
        {
            "control": {"PASS": 2, "FAIL": 1},
            "experiment": {"PASS": 2, "FAIL": 1},
        },
        3,
    )
    assert not module.status_count_contract_ok(
        {"PASS": 2, "UNKNOWN": 1},
        {"PASS": 6},
        {"control": {"PASS": 3}, "experiment": {"PASS": 3}},
        3,
    )


def test_validate_case_record_requires_control_then_experiment():
    module = load_module()
    record = {
        "case_id": "TC-MM-001",
        "group_order": ["control", "experiment"],
        "pair_status": "PASS",
        "groups": [
            {
                "group": "control",
                "status": "PASS",
                "recorded_at": "2026-07-14T10:00:00+08:00",
            },
            {
                "group": "experiment",
                "status": "PASS",
                "recorded_at": "2026-07-14T10:00:01+08:00",
            },
        ],
    }

    module.validate_case_record(record, "TC-MM-001")
    bad = json.loads(json.dumps(record))
    bad["groups"].reverse()
    try:
        module.validate_case_record(bad, "TC-MM-001")
    except ValueError as exc:
        assert "order" in str(exc)
    else:
        raise AssertionError("reversed group order must be rejected")


def test_scan_known_values_reports_counts_without_disclosing_values(tmp_path):
    module = load_module()
    secret = "private-runtime-value"
    (tmp_path / "one.json").write_text(json.dumps({"value": secret}), encoding="utf-8")

    result = module.scan_known_values(tmp_path, {secret})

    assert result == {"match_count": 1, "files_with_matches": 1}
    assert secret not in json.dumps(result)


def test_exact_sensitive_key_scan_accepts_redaction_and_rejects_plaintext(tmp_path):
    module = load_module()
    (tmp_path / "safe.json").write_text(
        json.dumps(
            {
                "authorization": {"redacted": True, "fingerprint": "abc123"},
                "token_fingerprint": "abc123",
                "password": "<redacted>",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "unsafe.json").write_text(
        json.dumps({"nested": {"password": "plaintext-value"}}),
        encoding="utf-8",
    )

    assert module.exact_sensitive_value_violation_count(tmp_path) == 1


def test_extract_created_memory_ids_uses_only_successful_post_memories(tmp_path):
    module = load_module()
    records = [
        (
            "TC-MM-001_control_create.json",
            {
                "request": {"method": "POST", "path": "/memories"},
                "response": {"body": {"code": 0, "data": {"id": "memory-a"}}},
            },
        ),
        (
            "TC-MM-001_control_list.json",
            {
                "request": {"method": "GET", "path": "/memories"},
                "response": {"body": {"code": 0, "data": [{"id": "not-created"}]}},
            },
        ),
        (
            "TC-MM-001_experiment_rejected.json",
            {
                "request": {"method": "POST", "path": "/memories"},
                "response": {"body": {"code": 101, "data": {"id": "rejected"}}},
            },
        ),
    ]
    for name, payload in records:
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    assert module.extract_created_memory_ids(tmp_path) == {
        "control": {"memory-a"},
        "experiment": set(),
    }


def test_extract_registered_emails_supports_registration_recorder_shape(tmp_path):
    module = load_module()
    (tmp_path / "TC-MM-018_control_register_secondary_user.json").write_text(
        json.dumps(
            {
                "request": {
                    "email": "memory-user@fresh.invalid",
                    "nickname": "MemoryUser",
                    "password": {"redacted": True},
                },
                "response": {"body": {"code": 0}},
            }
        ),
        encoding="utf-8",
    )

    assert module._extract_registered_emails(tmp_path) == {
        "control": {"memory-user@fresh.invalid"},
        "experiment": set(),
    }


def test_evidence_permissions_detects_bad_file_and_directory_modes(tmp_path):
    module = load_module()
    private = tmp_path / "private"
    raw = private / "raw"
    raw.mkdir(parents=True)
    evidence = raw / "one.json"
    evidence.write_text("{}\n", encoding="utf-8")
    private.chmod(0o700)
    raw.chmod(0o755)
    evidence.chmod(0o644)

    result = module.audit_permissions(private)

    assert result["file_mode_violation_count"] == 1
    assert result["directory_mode_violation_count"] == 1


def test_candidate_secret_values_exclude_numeric_configuration_limits():
    module = load_module()

    assert module.is_candidate_secret_value("private-runtime-value") is True
    assert module.is_candidate_secret_value(10_485_760) is False
    assert module.is_candidate_secret_value(True) is False
    assert module.is_candidate_secret_value("short") is False


def test_secondary_user_count_uses_dialect_safe_user_table_identifier():
    module = load_module()

    class Cursor:
        def __init__(self):
            self.sql = ""
            self.params = ()

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return (0,)

    cursor = Cursor()
    count = module.count_secondary_users(cursor, '"user"', {"memory-user@fresh.invalid"})

    assert count == 0
    assert 'FROM "user" WHERE email IN' in cursor.sql
    assert cursor.params == ("memory-user@fresh.invalid",)
