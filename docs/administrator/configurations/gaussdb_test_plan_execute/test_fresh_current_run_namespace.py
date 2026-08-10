from pathlib import Path


EXECUTE_DIR = Path(__file__).resolve().parent

CURRENT_RUN_FILES = (
    "fresh_02_cleanup.py",
    "fresh_02_user_management_admin_supplement.py",
    "fresh_03_auth_audit.py",
    "fresh_04_dataset_document_audit.py",
    "fresh_05_chat_session_agent_audit.py",
    "fresh_06_memory_metadata_audit.py",
    "fresh_07_memory_store_audit.py",
    "fresh_08_file.py",
    "fresh_08_file_audit.py",
    "fresh_09_connector_common.py",
    "fresh_auth_protocol_renew.py",
    "fresh_auth_protocol_setup.py",
    "fresh_runtime_ca_repair.py",
)


def test_fresh_execution_files_never_hardcode_legacy_run_paths():
    forbidden = (
        'BATCH_ID = "20260710_fresh_001"',
        'EXECUTE_DIR / "evidence_private"',
        'EXECUTE_DIR / "runtime" / BATCH_ID',
    )

    for filename in CURRENT_RUN_FILES:
        source = (EXECUTE_DIR / filename).read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in source, f"{filename} contains {marker}"


def test_formal_evidence_writers_use_current_run_context():
    expected = {
        "fresh_02_user_management_admin_supplement.py",
        "fresh_08_file.py",
        "fresh_09_connector_common.py",
    }

    for filename in expected:
        source = (EXECUTE_DIR / filename).read_text(encoding="utf-8")
        assert "fresh_run_context import" in source
        assert "evidence_dir(" in source
