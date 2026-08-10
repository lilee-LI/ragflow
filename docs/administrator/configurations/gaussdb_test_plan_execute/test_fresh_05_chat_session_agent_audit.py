import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_05_chat_session_agent_audit.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_05_chat_session_agent_audit", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_pair_status_prioritizes_fail_then_blocked():
    module = load_module()

    assert module.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert module.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert module.expected_pair_status(["FAIL", "BLOCKED"]) == "FAIL"


def test_validate_case_record_requires_control_then_experiment():
    module = load_module()
    record = {
        "case_id": "TC-CS-001",
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

    module.validate_case_record(record, "TC-CS-001")
    bad = json.loads(json.dumps(record))
    bad["groups"].reverse()
    try:
        module.validate_case_record(bad, "TC-CS-001")
    except ValueError as exc:
        assert "order" in str(exc)
    else:
        raise AssertionError("reversed group order must be rejected")


def test_scan_known_values_returns_counts_without_values(tmp_path):
    module = load_module()
    secret = "private-runtime-value"
    (tmp_path / "one.json").write_text(json.dumps({"value": secret}), encoding="utf-8")

    result = module.scan_known_values(tmp_path, {secret})

    assert result == {"match_count": 1, "files_with_matches": 1}
    assert secret not in json.dumps(result)


def test_exact_sensitive_key_scan_ignores_fingerprints_but_rejects_plaintext(
    tmp_path,
):
    module = load_module()
    (tmp_path / "safe.json").write_text(
        json.dumps(
            {
                "token_fingerprint": "abc123",
                "known_secret_occurrences": 0,
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


def test_extract_fixture_inventory_tracks_successful_current_run_resources(tmp_path):
    module = load_module()
    records = {
        "TC-CS-001_control_create_chat.json": {
            "request": {"method": "POST", "path": "/chats"},
            "response": {"body": {"code": 0, "data": {"id": "chat-a"}}},
        },
        "TC-CS-002_control_create_session.json": {
            "request": {"method": "POST", "path": "/chats/chat-a/sessions"},
            "response": {"body": {"code": 0, "data": {"id": "session-a"}}},
        },
        "TC-CS-003_experiment_register_secondary_user.json": {
            "request": {"email": "chat-user@fresh.invalid"},
            "response": {"body": {"code": 0}},
        },
        "TC-CS-004_experiment_rejected_agent.json": {
            "request": {"method": "POST", "path": "/agents"},
            "response": {"body": {"code": 101, "data": {"id": "rejected"}}},
        },
    }
    for name, payload in records.items():
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    inventory = module.extract_fixture_inventory(tmp_path)

    assert inventory["control"]["dialog_ids"] == {"chat-a"}
    assert inventory["control"]["conversation_ids"] == {"session-a"}
    assert inventory["experiment"]["secondary_emails"] == {"chat-user@fresh.invalid"}
    assert "rejected" not in inventory["experiment"]["canvas_ids"]


def test_active_resource_queries_require_live_parent_state():
    module = load_module()

    class Cursor:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params):
            self.calls.append((sql, params))

        def fetchone(self):
            return (0,)

    cursor = Cursor()
    module._count_values(cursor, "dialog", "id", {"chat-a"}, " AND status='1'")
    module._count_parented_values(
        cursor,
        "conversation",
        "dialog",
        {"session-a"},
        " AND p.status='1'",
    )

    assert "status='1'" in cursor.calls[0][0]
    assert "JOIN dialog p ON p.id=c.dialog_id" in cursor.calls[1][0]
    assert "p.status='1'" in cursor.calls[1][0]
