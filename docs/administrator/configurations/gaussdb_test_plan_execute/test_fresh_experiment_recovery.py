import copy
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_experiment_recovery.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_experiment_recovery", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_recovery_switches_only_database_and_schema_fields():
    module = load_module()
    config = {
        "gaussdb": {
            "config": {
                "database": "old_database",
                "schema": "old_docstore",
                "password": "private-value",
            }
        },
        "redis": {"db": 12},
    }
    environment = {
        "GAUSSDB_METADATA_DBNAME": "old_database",
        "GAUSSDB_METADATA_SCHEMA": "old_metadata",
        "GAUSSDB_METADATA_PASSWORD": "private-value",
        "RAGFLOW_SECRET_KEY": "unchanged-secret",
    }
    config_before = copy.deepcopy(config)
    environment_before = copy.deepcopy(environment)

    recovered_config, recovered_environment = module.build_recovery_payloads(config, environment)

    assert config == config_before
    assert environment == environment_before
    assert recovered_config["gaussdb"]["config"]["database"] == module.RECOVERY_DATABASE
    assert recovered_config["gaussdb"]["config"]["schema"] == module.RECOVERY_DOCSTORE_SCHEMA
    assert recovered_environment["GAUSSDB_METADATA_DBNAME"] == module.RECOVERY_DATABASE
    assert recovered_environment["GAUSSDB_METADATA_SCHEMA"] == module.RECOVERY_METADATA_SCHEMA
    assert recovered_environment["GAUSSDB_METADATA_PASSWORD"] == "private-value"
    assert recovered_environment["RAGFLOW_SECRET_KEY"] == "unchanged-secret"


def test_recovery_resources_are_exact_batch_identifiers():
    module = load_module()

    assert module.assert_authorized_database(module.RECOVERY_DATABASE) == "zws_test2"
    assert module.assert_recovery_resource(module.RECOVERY_METADATA_SCHEMA) == module.RECOVERY_METADATA_SCHEMA
    assert module.assert_recovery_resource(module.RECOVERY_DOCSTORE_SCHEMA) == module.RECOVERY_DOCSTORE_SCHEMA


def test_recovery_never_creates_or_drops_a_gauss_database():
    source = MODULE_PATH.read_text(encoding="utf-8")

    assert "DROP DATABASE" not in source
    assert "CREATE DATABASE" not in source
    assert 'RECOVERY_DATABASE = "zws_test2"' in source


def test_experiment_services_stop_and_start_in_safe_order():
    module = load_module()

    assert module.STOP_ORDER == ("sync", "worker", "admin", "api")
    assert module.START_ORDER == ("api", "admin", "worker", "sync")
