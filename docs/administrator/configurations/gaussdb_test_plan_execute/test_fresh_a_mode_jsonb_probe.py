import importlib.util
import inspect
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_a_mode_jsonb_probe.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_a_mode_jsonb_probe", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_probe_contract_requires_execution_results_and_no_residue():
    module = load_module()
    result = {
        "sql_compatibility": "A",
        "aggregation_executed": True,
        "aggregation_values_match": True,
        "rename_executed": True,
        "renamed_values_match": True,
        "forbidden_sql_absent": True,
        "rollback_no_residue": True,
    }

    assert module.probe_contract_ok(result)
    assert not module.probe_contract_ok({**result, "rollback_no_residue": False})


def test_probe_uses_a_mode_table_catalog_instead_of_to_regclass():
    module = load_module()
    source = inspect.getsource(module._table_is_absent)

    assert "information_schema.tables" in source
    assert "to_regclass" not in source
