import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_08_file_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_08_file_audit", MODULE_PATH)
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
        "case_id": "TC-FM-001",
        "group_order": ["control", "experiment"],
        "pair_status": "PASS",
        "groups": [
            {
                "group": "control",
                "status": "PASS",
                "recorded_at": "2026-07-15T10:00:00+08:00",
                "steps": [{"name": "exercise"}],
                "oracle": {"expected": True},
                "findings": [],
            },
            {
                "group": "experiment",
                "status": "PASS",
                "recorded_at": "2026-07-15T10:00:01+08:00",
                "steps": [{"name": "exercise"}],
                "oracle": {"expected": True},
                "findings": [],
            },
        ],
    }

    module.validate_case_record(record, "TC-FM-001")
    bad = json.loads(json.dumps(record))
    bad["groups"][1]["steps"] = []
    try:
        module.validate_case_record(bad, "TC-FM-001")
    except ValueError as exc:
        assert "shape" in str(exc)
    else:
        raise AssertionError("empty execution steps must be rejected")


def test_extract_fixture_inventory_uses_successful_current_responses(tmp_path):
    module = load_module()
    records = {
        "TC-FM-001_control_create_folder.json": {
            "request": {"method": "POST", "path": "/files", "json": {}},
            "response": {
                "body": {
                    "code": 0,
                    "data": {
                        "id": "folder-a",
                        "parent_id": "root-a",
                        "location": "",
                        "type": "folder",
                    },
                }
            },
        },
        "TC-FM-008_control_upload.json": {
            "request": {"method": "POST", "path": "/files", "json": {}},
            "response": {
                "body": {
                    "code": 0,
                    "data": [
                        {
                            "id": "file-a",
                            "parent_id": "folder-a",
                            "location": "file-a.txt",
                            "type": "doc",
                        }
                    ],
                }
            },
        },
        "TC-FILE-LINK-001_control_dataset.json": {
            "request": {"method": "POST", "path": "/datasets", "json": {}},
            "response": {"body": {"code": 0, "data": {"id": "dataset-a"}}},
        },
        "TC-FILE-VER-001_control_commit.json": {
            "request": {
                "method": "POST",
                "path": "/folders/folder-a/commits",
                "json": {},
            },
            "response": {
                "body": {
                    "code": 0,
                    "data": {
                        "id": "commit-a",
                        "folder_id": "folder-a",
                        "tree_state": json.dumps(
                            {
                                "file-a": {
                                    "location": ".objects/hash-a",
                                    "parent_id": "folder-a",
                                }
                            }
                        ),
                    },
                }
            },
        },
        "TC-FILE-ACL-001_control_register.json": {
            "request": {
                "email": "file-user@fresh.invalid",
                "password": {"redacted": True},
            },
            "response": {"body": {"code": 0, "data": {"id": "user-a"}}},
        },
        "TC-FM-001_experiment_rejected.json": {
            "request": {"method": "POST", "path": "/files", "json": {}},
            "response": {
                "body": {
                    "code": 102,
                    "data": {"id": "rejected", "parent_id": "x"},
                }
            },
        },
    }
    for name, payload in records.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    inventory = module.extract_fixture_inventory(tmp_path)

    control = inventory["control"]
    assert control["file_ids"] == {"folder-a", "file-a"}
    assert control["dataset_ids"] == {"dataset-a"}
    assert control["commit_ids"] == {"commit-a"}
    assert control["commit_folder_ids"] == {"folder-a"}
    assert control["secondary_emails"] == {"file-user@fresh.invalid"}
    assert control["secondary_user_ids"] == {"user-a"}
    assert control["file_object_addresses"] == {("folder-a", "file-a.txt")}
    assert control["commit_object_addresses"] == {("folder-a", ".objects/hash-a")}
    assert "rejected" not in inventory["experiment"]["file_ids"]


def test_current_plan_and_runner_orders_are_exactly_89_cases():
    module = load_module()

    planned = module._plan_case_ids()
    runner = module._runner_case_ids()

    assert len(planned) == len(set(planned)) == 89
    assert planned == runner
    assert planned[:2] == ["TC-FM-001", "TC-FM-002"]
    assert planned[-2:] == ["TC-FILE-ACL-001", "TC-FILE-ACL-002"]


def test_case_category_preserves_plan_families():
    module = load_module()

    assert module.case_category("TC-FM-070") == "main"
    assert module.case_category("TC-FILE-DEL-004") == "delete"
    assert module.case_category("TC-FILE-LINK-004") == "link"
    assert module.case_category("TC-FILE-VER-006") == "version"
    assert module.case_category("TC-FILE-STOR-003") == "storage"
    assert module.case_category("TC-FILE-ACL-002") == "acl"


def test_evidence_permissions_detect_bad_modes_and_symlinks(tmp_path):
    module = load_module()
    private = tmp_path / "private"
    raw = private / "raw"
    raw.mkdir(parents=True)
    evidence = raw / "one.json"
    evidence.write_text("{}\n", encoding="utf-8")
    link = raw / "link.json"
    link.symlink_to(evidence)
    private.chmod(0o700)
    raw.chmod(0o755)
    evidence.chmod(0o644)

    result = module.audit_permissions(private)

    assert result["file_mode_violation_count"] == 1
    assert result["directory_mode_violation_count"] == 1
    assert result["symlink_count"] == 1


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


def test_business_cleanup_contract_separates_history_from_orphan_objects():
    module = load_module()
    clean = {
        "active_file_rows": 0,
        "active_dataset_rows": 0,
        "active_document_rows": 0,
        "active_relation_rows": 0,
        "active_task_rows": 0,
        "secondary_users": 0,
        "secondary_memberships": 0,
        "active_file_objects": 0,
        "known_invalid_attempt_objects": 0,
        "retained_commit_rows": 40,
        "retained_commit_item_rows": 40,
        "retained_commit_objects": 1,
    }

    assert module.business_cleanup_contract_ok(clean)
    assert not module.business_cleanup_contract_ok({**clean, "known_invalid_attempt_objects": 1})


def test_forbidden_markers_are_assembled_without_self_matching():
    module = load_module()

    assert all(marker not in Path(module.__file__).read_text() for marker in module.FORBIDDEN_MARKERS)
