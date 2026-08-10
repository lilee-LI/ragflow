import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_10_memory_dialect.py")
EXPECTED_IDS = ["TC-FR-051", "TC-FR-052", "TC-FR-053"]


def load_module():
    name = "fresh_10_memory_dialect_contract"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_vector_literal_boundary_rejects_missing_empty_and_wrong_dimension():
    module = load_module()

    observed = module.vector_literal_boundary_observation()

    assert observed == {
        "none_error": "ValueError",
        "empty_error": "ValueError",
        "wrong_dimension_error": "ValueError",
        "valid_literal": "[1.0,2.0,3.0]",
        "zero_literal": "[0,0,0]",
    }


def test_memory_vector_contract_requires_atomic_rejection_and_cross_dim_reset():
    module = load_module()
    valid = {
        "invalid_failures": 3,
        "rows_after_invalid": 1,
        "old_row_preserved": True,
        "real_dimension": 3,
        "real_empty_flag": False,
        "unused_zero_vector": True,
        "unused_empty_flag": True,
        "cross_dimension": 2,
        "cleanup_succeeded": True,
    }

    assert module.memory_vector_contract_ok(valid, cross_dim_required=True)
    assert not module.memory_vector_contract_ok({**valid, "unused_empty_flag": False}, cross_dim_required=True)
    assert module.memory_vector_contract_ok(
        {
            **valid,
            "real_empty_flag": None,
            "unused_zero_vector": None,
            "unused_empty_flag": None,
            "cross_dimension": None,
        },
        cross_dim_required=False,
    )


def test_low_level_memory_fixture_uses_storage_integer_status():
    module = load_module()

    payload = module.memory_probe_payload("control")

    assert payload["status"] == 1
    assert type(payload["status"]) is int


def test_identifier_limit_contract_uses_stable_bounded_hash_name():
    module = load_module()

    observed = module.identifier_limit_observation()

    assert observed["accepted_length"] == 63
    assert observed["rejected_64"] == "InvalidGaussDBObjectName"
    assert observed["rejected_invalid"] == "InvalidGaussDBObjectName"
    assert observed["index_name_stable"] is True
    assert observed["index_name_length"] <= 63
    assert observed["index_name_hashed"] is True


def test_identifier_live_canary_uses_current_experiment_schema():
    module = load_module()
    table = module._identifier_table_name("experiment")

    builder, qualified = module.identifier_live_qualified_table("experiment", "fr_current_metadata", table)

    assert builder.schema == "fr_current_metadata"
    assert qualified == f"fr_current_metadata.{table}"
    assert not qualified.startswith("public.")


def test_domain_registers_only_memory_dialect_cases():
    module = load_module()
    titles = {case_id: case_id for case_id in EXPECTED_IDS}

    runners = module.get_runners(titles)

    assert list(runners) == EXPECTED_IDS
    assert all(callable(item) for item in runners.values())
