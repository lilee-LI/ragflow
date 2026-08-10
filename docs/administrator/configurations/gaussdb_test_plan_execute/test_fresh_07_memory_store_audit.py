import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_07_memory_store_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_07_memory_store_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_pair_status_prioritizes_fail_then_blocked():
    module = load_module()

    assert module.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert module.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert module.expected_pair_status(["FAIL", "BLOCKED"]) == "FAIL"


def test_validate_case_record_requires_order_shape_and_timestamps():
    module = load_module()
    record = {
        "case_id": "TC-MS-001",
        "group_order": ["control", "experiment"],
        "pair_status": "PASS",
        "groups": [
            {
                "group": "control",
                "status": "PASS",
                "recorded_at": "2026-07-14T10:00:00+08:00",
                "steps": [{"name": "exercise"}],
                "oracle": {"expected": True},
                "findings": [],
            },
            {
                "group": "experiment",
                "status": "PASS",
                "recorded_at": "2026-07-14T10:00:01+08:00",
                "steps": [{"name": "exercise"}],
                "oracle": {"expected": True},
                "findings": [],
            },
        ],
    }

    module.validate_case_record(record, "TC-MS-001")
    bad = json.loads(json.dumps(record))
    bad["groups"][1]["steps"] = []
    try:
        module.validate_case_record(bad, "TC-MS-001")
    except ValueError as exc:
        assert "shape" in str(exc)
    else:
        raise AssertionError("empty execution steps must be rejected")


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


def test_extract_fixture_inventory_uses_only_successful_business_creates(tmp_path):
    module = load_module()
    records = [
        (
            "TC-MS-001_control_create.json",
            {
                "request": {"method": "POST", "path": "/memories"},
                "response": {
                    "body": {
                        "code": 0,
                        "data": {"id": "memory-a", "tenant_id": "tenant-a"},
                    }
                },
            },
        ),
        (
            "TC-MS-900_control_create_secondary_ollama_embedding_instance.json",
            {
                "request": {
                    "method": "POST",
                    "path": "/providers/Ollama/instances",
                    "json": {"instance_name": "fresh-ms-900-control-embedding"},
                },
                "response": {"body": {"code": 0}},
            },
        ),
        (
            "TC-MS-010_experiment_register_secondary_user.json",
            {
                "request": {
                    "method": "POST",
                    "path": "/register",
                    "json": {"email": "ms-010-user@fresh.invalid"},
                },
                "response": {"body": {"code": 0}},
            },
        ),
        (
            "TC-MS-009_experiment_rejected.json",
            {
                "request": {"method": "POST", "path": "/memories"},
                "response": {
                    "body": {
                        "code": 500,
                        "data": {"id": "rejected", "tenant_id": "tenant-x"},
                    }
                },
            },
        ),
    ]
    for name, payload in records:
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    inventory = module.extract_fixture_inventory(tmp_path)

    assert inventory["control"]["memories"] == {"memory-a": "tenant-a"}
    assert inventory["control"]["model_instances"] == {"fresh-ms-900-control-embedding"}
    assert inventory["experiment"]["emails"] == {"ms-010-user@fresh.invalid"}
    assert "rejected" not in inventory["experiment"]["memories"]


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


def test_count_model_instances_uses_string_instance_name_column():
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
    count = module.count_model_instances(cursor, {"fresh-ms-900-control-embedding"})

    assert count == 0
    assert "tenant_model_instance" in cursor.sql
    assert "instance_name IN" in cursor.sql
    assert "tenant_llm" not in cursor.sql
    assert cursor.params == ("fresh-ms-900-control-embedding",)


def test_count_model_fixture_rows_scopes_all_relations_by_instance_name():
    module = load_module()

    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

        def fetchone(self):
            return (1,)

    cursor = Cursor()
    result = module.count_model_fixture_rows(cursor, {"fresh-ms-907-experiment-embedding"})

    assert result == {"instances": 1, "models": 1, "providers": 1}
    assert len(cursor.calls) == 3
    assert all("instance_name IN" in sql for sql, _params in cursor.calls)
    assert all(params == ("fresh-ms-907-experiment-embedding",) for _sql, params in cursor.calls)
