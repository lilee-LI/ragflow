import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_06_memory_metadata.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_06_memory_metadata", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_plan_case_titles_are_all_47_in_plan_order():
    module = load_module()

    assert len(module.CASE_TITLES) == 47
    assert list(module.CASE_TITLES)[0] == "TC-MM-001"
    assert list(module.CASE_TITLES)[23] == "TC-MM-024"
    assert list(module.CASE_TITLES)[24] == "TC-MM-SUP-001"
    assert list(module.CASE_TITLES)[-1] == "TC-MM-SUP-023"


def test_normal_create_contract_checks_defaults_and_no_message_store():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "id_present": True,
        "database_count": 1,
        "name_matches": True,
        "tenant_matches": True,
        "memory_type": 1,
        "embd_matches": True,
        "llm_matches": True,
        "permissions": "me",
        "memory_size": 5242880,
        "forgetting_policy": "FIFO",
        "temperature": 0.5,
        "storage_type": "table",
        "message_store_absent": True,
        "cleanup_succeeded": True,
    }

    assert module.normal_create_contract_ok(observed)
    observed["message_store_absent"] = False
    assert not module.normal_create_contract_ok(observed)


def test_rejection_contract_requires_database_zero_delta():
    module = load_module()
    observed = {
        "responses": [
            {"http_status": 200, "code": 101},
            {"http_status": 200, "code": 101},
        ],
        "database_delta": 0,
        "cleanup_succeeded": True,
    }

    assert module.rejection_contract_ok(observed, request_count=2)
    observed["database_delta"] = 1
    assert not module.rejection_contract_ok(observed, request_count=2)


def test_group_result_shape_requires_steps_and_oracle():
    module = load_module()

    assert module.group_result_shape_ok({"status": "PASS", "steps": [{"name": "probe"}], "oracle": {}})
    assert not module.group_result_shape_ok({"status": "PASS", "steps": [], "oracle": {}})


def test_empty_model_physical_contract_differs_by_group():
    module = load_module()
    common = {
        "http_status": 200,
        "code": 0,
        "api_embd_id": "",
        "api_llm_id": "",
        "orm_embd_id": "",
        "orm_llm_id": "",
        "cleanup_succeeded": True,
    }
    control = {
        **common,
        "physical_embd_is_empty": True,
        "physical_llm_is_empty": True,
        "physical_embd_is_null": False,
        "physical_llm_is_null": False,
    }
    experiment = {
        **common,
        "physical_embd_is_empty": False,
        "physical_llm_is_empty": False,
        "physical_embd_is_null": True,
        "physical_llm_is_null": True,
    }

    assert module.empty_model_contract_ok("control", control)
    assert module.empty_model_contract_ok("experiment", experiment)


def test_memory_size_update_contract_matches_current_zero_semantics():
    module = load_module()
    observed = {
        "one": {"code": 0, "stored": 1},
        "zero": {"code": 0, "stored": 1},
        "negative": {"code": 101, "stored": 1},
        "numeric_string": {"code": 0, "stored": 100},
        "cleanup_succeeded": True,
    }

    assert module.memory_size_update_contract_ok(observed)


def test_temperature_update_contract_checks_boundaries_and_rejections():
    module = load_module()
    observed = {
        "accepted": [
            {"code": 0, "stored": 0.5},
            {"code": 0, "stored": 0.0},
            {"code": 0, "stored": 1.0},
        ],
        "rejected": [
            {"code": 101, "stored": 1.0},
            {"code": 101, "stored": 1.0},
        ],
        "cleanup_succeeded": True,
    }

    assert module.temperature_update_contract_ok(observed)


def test_nonempty_memory_update_contract_requires_message_and_unchanged_fields():
    module = load_module()
    observed = {
        "message_readable": True,
        "size_cache_positive": True,
        "embd_update_code": 101,
        "type_update_code": 101,
        "embd_unchanged": True,
        "type_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.nonempty_update_contract_ok(observed)


def test_public_internal_model_update_rejects_silent_success_noop():
    module = load_module()
    silent_noop = {
        "http_status": 200,
        "code": 0,
        "fields_changed": False,
        "update_time_changed": False,
        "cleanup_succeeded": True,
    }
    explicit_rejection = {
        **silent_noop,
        "code": 101,
    }

    assert not module.internal_model_update_contract_ok(silent_noop)
    assert module.internal_model_update_contract_ok(explicit_rejection)


def test_ordinary_empty_text_contract_uses_group_physical_semantics():
    module = load_module()
    common = {"http_status": 200, "code": 0, "cleanup_succeeded": True}
    control = {
        **common,
        "database_values": ["", "", "", ""],
        "api_values": ["", "", "", ""],
    }
    experiment = {
        **common,
        "database_values": [None, None, None, None],
        "api_values": [None, None, None, None],
    }

    assert module.ordinary_empty_text_contract_ok("control", control)
    assert module.ordinary_empty_text_contract_ok("experiment", experiment)


def test_message_forget_contract_requires_scoped_timestamp_update():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "before_exists": True,
        "after_exists": True,
        "forget_at_changed": True,
        "identity_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.message_forget_contract_ok(observed)
    observed["identity_unchanged"] = False
    assert not module.message_forget_contract_ok(observed)


def test_message_denial_contract_requires_not_found_and_zero_mutation():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 404,
        "message_exists_before": True,
        "message_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.message_denial_contract_ok(observed)
    observed["message_unchanged"] = False
    assert not module.message_denial_contract_ok(observed)


def test_collision_contract_requires_exact_composite_ids_and_b_survival():
    module = load_module()
    observed = {
        "fixture_inserted": True,
        "physical_pair_count": 2,
        "physical_ids_exact": True,
        "http_status": 200,
        "code": 0,
        "api_returns_a_only": True,
        "b_still_exact": True,
        "fixture_cleanup_succeeded": True,
        "memory_cleanup_succeeded": True,
    }

    assert module.message_collision_contract_ok(observed)
    observed["b_still_exact"] = False
    assert not module.message_collision_contract_ok(observed)


def test_denied_message_add_contract_requires_no_store_cache_or_task_delta():
    module = load_module()
    observed = {
        "denied_http_status": 200,
        "denied_code": 500,
        "denied_message_exact": True,
        "denied_store_delta": 0,
        "denied_cache_delta": 0,
        "denied_task_delta": 0,
        "owner_code": 0,
        "owner_message_readable": True,
        "owner_exact_raw_count": 1,
        "cleanup_succeeded": True,
    }

    assert module.denied_message_add_contract_ok(observed)
    observed["denied_task_delta"] = 1
    assert not module.denied_message_add_contract_ok(observed)


def test_pagination_protocol_contracts_require_parameter_error_code():
    module = load_module()
    illegal_type = {
        "legal": [200, 0],
        "invalid": [200, 101],
        "metadata_delta": 0,
    }
    bounds = {
        "boundary": [200, 0],
        "invalid": [[200, 101], [200, 101], [200, 101]],
        "metadata_delta": 0,
    }

    assert module.pagination_type_contract_ok(illegal_type)
    assert module.pagination_bounds_contract_ok(bounds)
    illegal_type["invalid"] = [200, 100]
    bounds["invalid"][0] = [200, 100]
    assert not module.pagination_type_contract_ok(illegal_type)
    assert not module.pagination_bounds_contract_ok(bounds)


def test_store_probe_retries_a_nonzero_exit_before_succeeding(tmp_path, monkeypatch):
    import os
    import sys
    from types import SimpleNamespace

    module = load_module()
    raw_dir = tmp_path / "raw"
    marker = tmp_path / "first-attempt-failed"
    manager = SimpleNamespace(
        PYTHON=Path(sys.executable),
        PROJECT_ROOT=tmp_path,
        load_group_environment=lambda _group: dict(os.environ),
    )
    original_load_module = module._load_module
    monkeypatch.setattr(module, "RAW_DIR", raw_dir)
    monkeypatch.setattr(
        module,
        "_load_module",
        lambda name, key: manager if name == "fresh_service_manager.py" else original_load_module(name, key),
    )
    script = """
import json
import pathlib
import sys

marker = pathlib.Path(sys.argv[1])
if not marker.exists():
    marker.write_text("failed once")
    raise SystemExit(17)
print("__FRESH_RESULT__" + json.dumps({"recovered": True}))
"""

    result = module._run_store_probe(
        "TC-PROBE-RETRY",
        "experiment",
        "transient_nonzero",
        script,
        [str(marker)],
        timeout=10,
        max_attempts=2,
    )

    assert result["recovered"] is True
    assert (raw_dir / "TC-PROBE-RETRY_experiment_transient_nonzero_error_1.json").is_file()
