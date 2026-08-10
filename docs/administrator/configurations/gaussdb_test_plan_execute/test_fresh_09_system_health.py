import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_09_system_health.py")


def load_module():
    assert MODULE_PATH.exists()
    spec = importlib.util.spec_from_file_location("fresh_09_system_health_tested", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_registry_contains_only_system_health_adaptation_cases():
    module = load_module()

    assert list(module.GROUP_RUNNERS) == ["TC-CONN-031", "TC-CONN-032"]


def test_common_exposes_namespaced_runtime_directory():
    module = load_module()

    assert module.RUNTIME_DIR.name == module.COMMON.BATCH_ID


def test_system_status_contract_requires_group_specific_backends():
    module = load_module()
    common = {
        "response": [200, 0],
        "all_component_statuses_green": True,
        "storage_type": "minio",
        "heartbeat_object": True,
        "own_executor_present": True,
        "foreign_executor_absent": True,
    }

    assert module.system_status_contract_ok(
        "control",
        {**common, "database_type": "mysql", "doc_engine_type": "infinity"},
    )
    assert module.system_status_contract_ok(
        "experiment",
        {**common, "database_type": "gaussdb", "doc_engine_type": "gaussdb"},
    )
    assert not module.system_status_contract_ok(
        "experiment",
        {**common, "database_type": "mysql", "doc_engine_type": "gaussdb"},
    )


def test_gaussdb_status_contract_requires_metrics_only_for_experiment():
    module = load_module()

    assert module.gaussdb_status_contract_ok(
        "control",
        {
            "response": [200, 0],
            "status": "not_configured",
            "health_present": False,
            "performance_present": False,
            "sensitive_value_match_count": 0,
        },
    )
    assert module.gaussdb_status_contract_ok(
        "experiment",
        {
            "response": [200, 0],
            "status": "alive",
            "health_present": True,
            "performance_present": True,
            "sensitive_value_match_count": 0,
        },
    )
    assert not module.gaussdb_status_contract_ok(
        "experiment",
        {
            "response": [200, 0],
            "status": "alive",
            "health_present": True,
            "performance_present": True,
            "sensitive_value_match_count": 1,
        },
    )
