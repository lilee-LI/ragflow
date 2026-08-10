import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_09_gaussdb_compatibility.py")


def load_module():
    assert MODULE_PATH.exists()
    spec = importlib.util.spec_from_file_location("fresh_09_compatibility_tested", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_registry_contains_only_connector_gaussdb_compatibility_cases():
    module = load_module()

    assert list(module.GROUP_RUNNERS) == ["TC-CONN-093", "TC-CONN-094"]


def test_empty_string_contract_accepts_physical_difference_and_shared_semantics():
    module = load_module()
    common = {
        "orm_before": ["", ""],
        "api_error_msg_before": "",
        "empty_suffix_unchanged": True,
        "error_msg_after": "append-sentinel",
        "full_trace_after": "trace-sentinel",
        "literal_null_absent": True,
        "cleanup_succeeded": True,
    }

    assert module.empty_string_contract_ok("control", {**common, "physical_before": ["", ""]})
    assert module.empty_string_contract_ok("experiment", {**common, "physical_before": [None, None]})
    assert not module.empty_string_contract_ok("experiment", {**common, "physical_before": ["NULL", None]})


def test_distinct_order_contract_requires_selected_sort_column_and_exact_order():
    module = load_module()
    observed = {
        "response": [200, 0],
        "service_ids": ["new", "middle", "old"],
        "api_ids": ["new", "middle", "old"],
        "database_ids": ["new", "middle", "old"],
        "update_time_selected": True,
        "strictly_descending": True,
        "cleanup_succeeded": True,
    }

    assert module.distinct_order_contract_ok(observed)
    assert not module.distinct_order_contract_ok({**observed, "update_time_selected": False})
