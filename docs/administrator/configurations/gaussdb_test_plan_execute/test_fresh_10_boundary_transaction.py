import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_10_boundary_transaction.py")
EXPECTED_IDS = [
    "TC-FR-027",
    "TC-FR-037",
    "TC-FR-038",
    "TC-FR-044",
    "TC-FR-045",
]


def load_module():
    name = "fresh_10_boundary_transaction_contract"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_schema_and_identifier_injection_fail_before_database_send():
    module = load_module()

    schema = module.deterministic_schema_injection_observation()
    identifier = module.identifier_injection_observation()

    assert schema["metadata_error"] == "ValueError"
    assert schema["docstore_error"] == "InvalidGaussDBConfig"
    assert schema["connection_attempts"] == 0
    assert identifier["invalid_count"] == 4
    assert identifier["invalid_error_classes"] == ["InvalidGaussDBObjectName"] * 4
    assert identifier["database_sends"] == 0


def test_permission_matrix_returns_connection_and_redacts_password():
    module = load_module()

    usage = module.permission_matrix_observation(False, True)
    create = module.permission_matrix_observation(True, False)
    both = module.permission_matrix_observation(False, False)

    assert usage["missing"] == ["USAGE"]
    assert create["missing"] == ["CREATE"]
    assert both["missing"] == ["USAGE", "CREATE"]
    for observed in (usage, create, both):
        assert observed["error_class"] == "GaussDBPermissionError"
        assert observed["put_calls"] == 1
        assert observed["cursor_closed"] is True
        assert observed["password_present"] is False


def test_concurrent_memory_contract_requires_unique_vectors_and_exact_cache():
    module = load_module()
    valid = {
        "request_count": 6,
        "accepted_count": 6,
        "raw_count": 6,
        "unique_message_ids": 6,
        "unique_row_ids": 6,
        "retrieved_count": 6,
        "vector_dimensions": [1024] * 6,
        "vector_real_count": 6,
        "cache_value": 17400,
        "calculated_size": 17400,
        "cleanup_succeeded": True,
    }

    assert module.concurrent_memory_contract_ok(valid)
    assert not module.concurrent_memory_contract_ok({**valid, "unique_message_ids": 5})


def test_storage_row_identity_uses_requested_key_when_backend_omits_id_field():
    module = load_module()

    infinity = module.storage_row_identity({"message_id": 17, "content": "stored"}, "memory-a", 17)
    gaussdb = module.storage_row_identity({"id": "memory-a_18", "message_id": 18}, "memory-a", 18)
    missing = module.storage_row_identity({}, "memory-a", 19)

    assert infinity == {"id": "memory-a_17", "retrieved": True}
    assert gaussdb == {"id": "memory-a_18", "retrieved": True}
    assert missing == {"id": "", "retrieved": False}


def test_transaction_contract_requires_full_rollback_and_recovery():
    module = load_module()

    assert module.transaction_contract_ok(
        {
            "conflict_code": "23505",
            "expected_code": "23505",
            "rows_after_rollback": 0,
            "recovery_value": 1,
            "cleanup_succeeded": True,
        }
    )
    assert not module.transaction_contract_ok(
        {
            "conflict_code": "23505",
            "expected_code": "23505",
            "rows_after_rollback": 2,
            "recovery_value": 1,
            "cleanup_succeeded": True,
        }
    )


def test_domain_registers_only_boundary_transaction_cases():
    module = load_module()
    titles = {case_id: case_id for case_id in EXPECTED_IDS}

    runners = module.get_runners(titles)

    assert list(runners) == EXPECTED_IDS
    assert all(callable(item) for item in runners.values())
