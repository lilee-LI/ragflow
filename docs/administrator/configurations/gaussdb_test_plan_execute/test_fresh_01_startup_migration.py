import hashlib
import importlib.util
import inspect
import sys
import types
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_01_startup_migration.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_01_startup_migration", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_summarize_startup_log_records_signals_without_copying_log_text():
    module = load_module()
    summary = module.summarize_startup_log(
        'RAGFlow server is ready after 1s\nrelation "system_settings" does not exist\nAUTO_INCREMENT ON DUPLICATE KEY UPDATE SHOW PROCESSLIST\npassword=must-not-leak\n'
    )

    assert summary == {
        "ready": True,
        "undefined_system_settings": True,
        "relation_missing_occurrences": 1,
        "traceback_occurrences": 0,
        "mysql_only_keyword_counts": {
            "AUTO_INCREMENT": 1,
            "ON DUPLICATE KEY UPDATE": 1,
            "SHOW PROCESSLIST": 1,
        },
    }
    assert "must-not-leak" not in str(summary)


def test_case_resource_names_are_batch_scoped_and_group_distinct():
    module = load_module()

    control = module.case_database_name("TC-SM-004", "control")
    experiment = module.case_database_name("TC-SM-004", "experiment")

    assert control == f"fr_{module.BATCH_ID}_control_sm004"
    assert experiment == f"fr_{module.BATCH_ID}_experiment_sm004"
    assert control != experiment
    assert module.assert_case_resource(control) == control
    assert module.assert_case_resource(experiment) == experiment


def test_experiment_case_connection_stays_in_zws_test2_and_uses_case_schema(
    monkeypatch,
):
    module = load_module()
    schema = module.case_database_name("TC-SM-004", "experiment")
    captured = {}
    executed = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, sql):
            executed.append(sql)

    class FakeConnection:
        autocommit = False

        def cursor(self):
            return FakeCursor()

    def connect(**kwargs):
        captured.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr(
        module,
        "_parse_gauss_info",
        lambda _path: {
            "host": "gauss.invalid",
            "port": "8000",
            "dbname": "zws_test2",
            "user": "user",
            "password": "secret",
        },
    )
    monkeypatch.setitem(sys.modules, "psycopg2", types.SimpleNamespace(connect=connect))

    connection = module._case_gauss_connection(schema, read_only=True)

    assert connection.autocommit is False
    assert captured["dbname"] == "zws_test2"
    assert f"search_path={schema}" in captured["options"]
    assert "default_transaction_read_only=on" in captured["options"]
    assert executed == [f'SET search_path TO "{schema}"']

    monkeypatch.setattr(
        module,
        "_parse_gauss_info",
        lambda _path: {
            "host": "gauss.invalid",
            "port": "8000",
            "dbname": "other_database",
            "user": "user",
            "password": "secret",
        },
    )
    with pytest.raises(ValueError, match="zws_test2"):
        module._case_gauss_connection(schema, read_only=False)


def test_pair_oracle_requires_exact_40_table_contract():
    module = load_module()

    assert module.table_contract_ok(40, {"user", "tenant", "system_settings"}) is True
    assert module.table_contract_ok(39, {"user", "tenant", "system_settings"}) is False
    assert module.table_contract_ok(40, {"user", "tenant"}) is False


def test_initializer_cache_seed_copies_only_a_validated_encoding(tmp_path, monkeypatch):
    module = load_module()
    source = tmp_path / "cl100k_base.tiktoken"
    source.write_bytes(b"fresh validated encoding fixture")
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(module, "TIKTOKEN_ENCODING_SHA256", expected_sha256)
    root = tmp_path / "case-runtime"
    root.mkdir()

    target = module.seed_tiktoken_cache(root, candidates=(source,))

    assert target == root / module.TIKTOKEN_CACHE_KEY
    assert target.read_bytes() == source.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o600

    source.write_bytes(b"invalid replacement")
    target.unlink()
    with pytest.raises(RuntimeError, match="validated cl100k_base.tiktoken"):
        module.seed_tiktoken_cache(root, candidates=(source,))


def test_gauss_schema_snapshot_uses_current_runtime_metadata_environment():
    module = load_module()

    source = inspect.getsource(module._experiment_gauss_schema_snapshot)

    assert "private_environments.json" in source
    assert "GAUSSDB_METADATA_DBNAME" in source
    assert "GAUSSDB_METADATA_SCHEMA" in source
    assert "_parse_gauss_info" not in source
    assert "gaussdb_info.md" not in source


def test_sm001_experiment_findings_only_report_a_current_runtime_failure():
    module = load_module()

    assert module.sm001_experiment_findings(True) == []
    assert module.sm001_experiment_findings(False) == [
        {
            "id": "ENV-DEFECT-001",
            "summary": ("current experiment metadata schema did not satisfy the cold-start migration contract"),
            "code_location": "api/db/db_models.py:init_database_tables",
        }
    ]


def test_catalog_counts_must_match_exactly_for_idempotent_restart():
    module = load_module()

    before = {"table_count": 40, "index_count": 81}
    assert module.catalog_counts_equal(before, {"table_count": 40, "index_count": 81}) is True
    assert module.catalog_counts_equal(before, {"table_count": 40, "index_count": 82}) is False


def test_summarize_restart_log_reports_ddl_failures_without_copying_config():
    module = load_module()
    summary = module.summarize_restart_log("password=must-not-leak\nRAGFlow server is ready after 1s\nCRITICAL create tables failed\n")

    assert summary == {
        "ready": True,
        "create_table_failure_occurrences": 1,
        "critical_occurrences": 1,
        "traceback_occurrences": 0,
    }
    assert "must-not-leak" not in str(summary)


def test_restart_contract_requires_live_ready_process_and_unchanged_catalog():
    module = load_module()
    before = {"table_count": 40, "index_count": 81}
    good_restart = {
        "stop_confirmed": True,
        "start_confirmed": True,
        "currently_alive": True,
        "ready": True,
        "log_summary": {
            "ready": True,
            "create_table_failure_occurrences": 0,
            "critical_occurrences": 0,
            "traceback_occurrences": 0,
        },
    }

    assert module.restart_contract_ok(before, before.copy(), good_restart) is True
    failed_restart = {**good_restart, "currently_alive": False, "ready": False}
    assert module.restart_contract_ok(before, before.copy(), failed_restart) is False


def test_concurrent_init_contract_requires_both_processes_and_complete_catalog():
    module = load_module()

    assert module.concurrent_init_contract_ok([0, 0], 40, 0) is True
    assert module.concurrent_init_contract_ok([0, 1], 40, 1) is False
    assert module.concurrent_init_contract_ok([0, 0], 39, 0) is False


def test_summarize_init_log_counts_lock_and_ddl_failures_only():
    module = load_module()
    summary = module.summarize_init_log("password=must-not-leak\nacquire gaussdb lock init_database_tables timeout\ncreate tables failed\nTraceback (most recent call last):\n")

    assert summary == {
        "lock_failure_occurrences": 1,
        "create_table_failure_occurrences": 1,
        "traceback_occurrences": 1,
        "transient_docstore_connection_failure": False,
    }
    assert "must-not-leak" not in str(summary)


def test_content_hash_column_contract_requires_nullable_varchar32_and_index():
    module = load_module()

    assert (
        module.content_hash_contract_ok(
            {
                "exists": True,
                "data_type": "character varying",
                "max_length": 32,
                "is_nullable": "YES",
                "index_count": 1,
            }
        )
        is True
    )
    assert (
        module.content_hash_contract_ok(
            {
                "exists": True,
                "data_type": "varchar",
                "max_length": 32,
                "is_nullable": "NO",
                "index_count": 1,
            }
        )
        is False
    )


def test_migration_row_counts_must_be_preserved_exactly():
    module = load_module()
    before = {"user": 1, "tenant": 1, "document": 0}

    assert module.row_counts_preserved(before, before.copy()) is True
    assert module.row_counts_preserved(before, {**before, "tenant": 0}) is False


def test_tenant_llm_migration_contract_checks_group_specific_identity():
    module = load_module()
    common = {
        "id_exists": True,
        "temp_id_exists": False,
        "primary_columns": ["id"],
        "composite_unique": True,
        "fixture_count": 1,
        "fixture_id_present": True,
    }

    assert (
        module.tenant_llm_migration_contract_ok(
            {**common, "auto_increment": True, "sequence_exists": False, "default_has_nextval": False},
            "control",
        )
        is True
    )
    assert (
        module.tenant_llm_migration_contract_ok(
            {**common, "auto_increment": False, "sequence_exists": True, "default_has_nextval": True},
            "experiment",
        )
        is True
    )
    assert (
        module.tenant_llm_migration_contract_ok(
            {**common, "auto_increment": False, "sequence_exists": False, "default_has_nextval": False},
            "experiment",
        )
        is False
    )


def test_tenant_llm_fixture_supplies_all_database_required_values():
    module = load_module()

    fixture = module.tenant_llm_fixture_values()

    assert set(fixture) == {
        "tenant_id",
        "llm_factory",
        "model_type",
        "llm_name",
        "api_key",
        "max_tokens",
        "used_tokens",
        "status",
    }
    assert fixture["max_tokens"] == 8192
    assert fixture["used_tokens"] == 0
    assert fixture["status"] == "1"


def test_initializer_retry_is_limited_to_pre_metadata_docstore_disconnect():
    module = load_module()
    retryable = {
        "exit_code": 1,
        "log_summary": {
            "lock_failure_occurrences": 0,
            "create_table_failure_occurrences": 0,
            "traceback_occurrences": 2,
            "transient_docstore_connection_failure": True,
        },
    }
    ddl_failure = {
        "exit_code": 1,
        "log_summary": {
            **retryable["log_summary"],
            "create_table_failure_occurrences": 1,
        },
    }

    assert module.should_retry_initializer(retryable) is True
    assert module.should_retry_initializer(ddl_failure) is False


def test_email_unique_contract_requires_index_and_duplicate_api_rejection():
    module = load_module()

    assert (
        module.email_unique_contract_ok(
            unique_index_count=1,
            first_api_code=0,
            duplicate_api_code=102,
            database_row_count=1,
        )
        is True
    )
    assert (
        module.email_unique_contract_ok(
            unique_index_count=0,
            first_api_code=0,
            duplicate_api_code=102,
            database_row_count=1,
        )
        is False
    )
    assert (
        module.email_unique_contract_ok(
            unique_index_count=1,
            first_api_code=0,
            duplicate_api_code=0,
            database_row_count=2,
        )
        is False
    )


def test_legacy_index_cleanup_contract_requires_all_three_absent():
    module = load_module()

    assert (
        module.legacy_indexes_removed(
            {
                "idx_api_key_provider_id": False,
                "tenantmodelinstance_api_key_provider_id": False,
                "idx_provider_model_instance": False,
            }
        )
        is True
    )
    assert (
        module.legacy_indexes_removed(
            {
                "idx_api_key_provider_id": False,
                "tenantmodelinstance_api_key_provider_id": True,
                "idx_provider_model_instance": False,
            }
        )
        is False
    )


def test_empty_string_contract_distinguishes_mysql_and_gauss_a_mode():
    module = load_module()

    assert (
        module.empty_string_contract_ok(
            "control",
            {
                "insert_succeeded": True,
                "stored_is_null": False,
                "stored_length": 0,
                "sqlstate": None,
                "sql_compatibility": None,
            },
        )
        is True
    )
    assert (
        module.empty_string_contract_ok(
            "experiment",
            {
                "insert_succeeded": False,
                "stored_is_null": None,
                "stored_length": None,
                "sqlstate": "23502",
                "sql_compatibility": "A",
            },
        )
        is True
    )


def test_nullable_field_contract_requires_complete_list_and_gauss_all_yes():
    module = load_module()
    control = {field: "NO" for field in module.EMPTY_STRING_FIELDS}
    experiment = {field: "YES" for field in module.EMPTY_STRING_FIELDS}

    assert module.nullable_field_contract_ok("control", control) is True
    assert module.nullable_field_contract_ok("experiment", experiment) is True
    incomplete = dict(experiment)
    incomplete.pop(next(iter(incomplete)))
    assert module.nullable_field_contract_ok("experiment", incomplete) is False
    wrong = dict(experiment)
    wrong[next(iter(wrong))] = "NO"
    assert module.nullable_field_contract_ok("experiment", wrong) is False


def test_orm_empty_string_roundtrip_contract_distinguishes_storage_and_api():
    module = load_module()
    common = {
        "registration_code": 0,
        "tenant_api_value": "",
        "dataset_create_code": 0,
        "dataset_get_code": 0,
        "dataset_api_value": "",
        "dataset_delete_code": 0,
        "user_cleanup_complete": True,
    }

    assert (
        module.orm_empty_string_roundtrip_contract_ok(
            "control",
            {
                **common,
                "tenant_storage": {
                    "python_value": "",
                    "is_null": False,
                    "length": 0,
                },
                "dataset_storage": {
                    "python_value": "",
                    "is_null": False,
                    "length": 0,
                },
            },
        )
        is True
    )
    assert (
        module.orm_empty_string_roundtrip_contract_ok(
            "experiment",
            {
                **common,
                "tenant_storage": {
                    "python_value": None,
                    "is_null": True,
                    "length": None,
                },
                "dataset_storage": {
                    "python_value": None,
                    "is_null": True,
                    "length": None,
                },
            },
        )
        is True
    )

    wrong_api = {
        **common,
        "tenant_storage": {"python_value": None, "is_null": True, "length": None},
        "dataset_storage": {"python_value": None, "is_null": True, "length": None},
        "dataset_api_value": None,
    }
    assert module.orm_empty_string_roundtrip_contract_ok("experiment", wrong_api) is False


def test_runtime_http_port_is_read_from_service_configuration():
    module = load_module()

    config = {
        "ragflow": {"http_port": "9380"},
        "admin": {"http_port": 9381},
    }
    assert module.runtime_http_port(config, "ragflow") == 9380
    assert module.runtime_http_port(config, "admin") == 9381


def test_empty_string_query_contract_requires_group_specific_sql():
    module = load_module()

    assert (
        module.empty_string_query_contract_ok(
            "control",
            {
                "sql": "SELECT * FROM `memory` WHERE (`memory`.`embd_id` = %s)",
                "params": [""],
            },
        )
        is True
    )
    assert (
        module.empty_string_query_contract_ok(
            "experiment",
            {
                "sql": ('SELECT * FROM "memory" WHERE (("memory"."embd_id" IS NULL) OR (LENGTH("memory"."embd_id") = %s))'),
                "params": [0],
            },
        )
        is True
    )
    assert (
        module.empty_string_query_contract_ok(
            "experiment",
            {"sql": 'SELECT * FROM "memory" WHERE "embd_id" = %s', "params": [""]},
        )
        is False
    )


def test_mysql_migration_guard_contract_requires_control_only_invocation():
    module = load_module()
    empty_log = {
        "mysql_migration": 0,
        "AUTO_INCREMENT": 0,
        "ON DUPLICATE": 0,
        "SHOW PROCESSLIST": 0,
    }

    assert (
        module.mysql_migration_guard_contract_ok(
            "control",
            {"exit_code": 0, "migration_invocation_count": 1, "skip_message": False},
            empty_log,
        )
        is True
    )
    assert (
        module.mysql_migration_guard_contract_ok(
            "experiment",
            {"exit_code": 0, "migration_invocation_count": 0, "skip_message": True},
            empty_log,
        )
        is True
    )
    assert (
        module.mysql_migration_guard_contract_ok(
            "experiment",
            {"exit_code": 0, "migration_invocation_count": 1, "skip_message": False},
            empty_log,
        )
        is False
    )


def test_ddl_dialect_contract_requires_native_catalog_types():
    module = load_module()

    assert (
        module.ddl_dialect_contract_ok(
            "control",
            {
                "catalog": "mysql_information_schema",
                "auto_increment_column_count": 1,
                "datetime_column_count": 5,
                "longtext_column_count": 3,
            },
        )
        is True
    )
    assert (
        module.ddl_dialect_contract_ok(
            "experiment",
            {
                "catalog": "gaussdb_information_schema",
                "sql_compatibility": "A",
                "nextval_column_count": 1,
                "timestamp_column_count": 5,
                "text_column_count": 3,
                "mysql_only_type_column_count": 0,
            },
        )
        is True
    )


def test_index_dialect_contract_rejects_mysql_syntax_in_gauss_catalog():
    module = load_module()

    assert (
        module.index_dialect_contract_ok(
            "control",
            {
                "catalog": "mysql_information_schema.statistics",
                "index_count": 10,
                "show_create_backtick_count": 4,
            },
        )
        is True
    )
    assert (
        module.index_dialect_contract_ok(
            "experiment",
            {
                "catalog": "pg_indexes",
                "index_count": 10,
                "backtick_occurrences": 0,
                "mysql_only_syntax_occurrences": 0,
                "invalid_index_definition_count": 0,
            },
        )
        is True
    )
    assert (
        module.index_dialect_contract_ok(
            "experiment",
            {
                "catalog": "pg_indexes",
                "index_count": 10,
                "backtick_occurrences": 1,
                "mysql_only_syntax_occurrences": 0,
                "invalid_index_definition_count": 0,
            },
        )
        is False
    )


def test_database_alias_contract_requires_exact_normalization():
    module = load_module()
    observed = {
        "GaussDB": "gaussdb",
        "gauss": "gaussdb",
        "GAUSSDB": "gaussdb",
        "gaussdb": "gaussdb",
        "postgresql": "postgres",
    }

    assert module.database_alias_contract_ok(observed) is True
    assert module.database_alias_contract_ok({**observed, "postgresql": "gaussdb"}) is False


def test_schema_validation_contract_accepts_identifiers_and_rejects_injection():
    module = load_module()
    observed = {
        "ragflow_meta": {"accepted": True, "normalized": "ragflow_meta"},
        "public": {"accepted": True, "normalized": "public"},
        "_test_schema": {"accepted": True, "normalized": "_test_schema"},
        "": {"accepted": True, "normalized": "public"},
        "test; DROP TABLE": {"accepted": False, "error_type": "ValueError"},
        "test-schema": {"accepted": False, "error_type": "ValueError"},
        "123schema": {"accepted": False, "error_type": "ValueError"},
    }

    assert module.schema_validation_contract_ok(observed) is True
    wrong = dict(observed)
    wrong["test-schema"] = {"accepted": True, "normalized": "test-schema"}
    assert module.schema_validation_contract_ok(wrong) is False


def test_config_isolation_contract_keeps_metadata_and_docstore_namespaces_distinct():
    module = load_module()
    fake = {
        "fake_metadata": {"host": "meta-host", "name": "meta_db"},
        "fake_docstore": {"host": "doc-host", "database": "doc_db"},
    }

    assert (
        module.config_isolation_contract_ok(
            "control",
            {
                **fake,
                "runtime_database_type": "mysql",
                "runtime_doc_engine": "infinity",
                "gauss_metadata_env_did_not_override_mysql": True,
                "actual_selection": {"metadata": "mysql", "docstore": "infinity"},
            },
        )
        is True
    )
    assert (
        module.config_isolation_contract_ok(
            "experiment",
            {
                **fake,
                "runtime_database_type": "gaussdb",
                "runtime_doc_engine": "gaussdb",
                "actual_selection": {"metadata": "gaussdb", "docstore": "gaussdb"},
                "actual_metadata_matches_environment": True,
                "actual_docstore_matches_service_config": True,
                "actual_targets_distinct": True,
            },
        )
        is True
    )


def test_admin_service_display_contract_requires_types_config_and_no_secrets():
    module = load_module()
    common = {
        "http_status": 200,
        "code": 0,
        "metadata_service_count": 1,
        "retrieval_service_count": 1,
        "metadata_matches_expected": True,
        "retrieval_matches_expected": True,
        "credential_exposure_count": 0,
    }

    assert (
        module.admin_service_display_contract_ok(
            "control",
            {**common, "metadata_type": "mysql", "retrieval_type": "infinity"},
        )
        is True
    )
    assert (
        module.admin_service_display_contract_ok(
            "experiment",
            {**common, "metadata_type": "gaussdb", "retrieval_type": "gaussdb"},
        )
        is True
    )
    assert (
        module.admin_service_display_contract_ok(
            "experiment",
            {
                **common,
                "metadata_type": "gaussdb",
                "retrieval_type": None,
                "retrieval_service_count": 0,
            },
        )
        is False
    )


def test_admin_metadata_health_contract_requires_group_specific_sql():
    module = load_module()
    common = {
        "detail_http_status": 200,
        "detail_code": 0,
        "detail_status": "alive",
    }

    assert module.admin_metadata_health_contract_ok("control", {**common, "executed_sql": ["SHOW PROCESSLIST;"]}) is True
    assert module.admin_metadata_health_contract_ok("experiment", {**common, "executed_sql": ["SELECT 1;"]}) is True
    assert module.admin_metadata_health_contract_ok("experiment", {**common, "executed_sql": ["SHOW PROCESSLIST;"]}) is False


def test_compose_profile_contract_isolates_mysql_from_external_gaussdb():
    module = load_module()
    common = {
        "services_exit_code": 0,
        "config_exit_code": 0,
        "ragflow_cpu_present": True,
        "mysql_dependency_required": False,
        "application_environment_matches": True,
    }

    assert (
        module.compose_profile_contract_ok(
            "control",
            {
                **common,
                "mysql_present": True,
                "infinity_present": True,
                "gauss_metadata_environment_present": True,
            },
        )
        is True
    )
    assert (
        module.compose_profile_contract_ok(
            "experiment",
            {
                **common,
                "mysql_present": False,
                "infinity_present": False,
                "gauss_metadata_environment_present": True,
            },
        )
        is True
    )


def test_structured_raw_evidence_redacts_nested_and_environment_secrets():
    module = load_module()
    payload = {
        "password": "outer-credential-value",
        "authorization_url": "https://oauth.invalid/authorize",
        "gaussdb": {
            "config": {
                "password": "nested-credential-value",
                "host": "gauss.invalid",
            }
        },
        "environment": [
            "API_KEY=api-key-credential-value",
            "NORMAL=value",
        ],
        "command": ["connect", "nested-credential-value"],
    }

    redacted, collected = module.redact_structured_secrets(payload)

    assert redacted["password"] == "<redacted>"
    assert redacted["authorization_url"] == "https://oauth.invalid/authorize"
    assert redacted["gaussdb"]["config"]["password"] == "<redacted>"
    assert redacted["gaussdb"]["config"]["host"] == "gauss.invalid"
    assert redacted["environment"] == ["API_KEY=<redacted>", "NORMAL=value"]
    assert collected == {
        "outer-credential-value",
        "nested-credential-value",
        "api-key-credential-value",
    }
    encoded = module.redacted_json_bytes(payload)
    assert b"outer-credential-value" not in encoded
    assert b"nested-credential-value" not in encoded
    assert b"api-key-credential-value" not in encoded
    assert b"https://oauth.invalid/authorize" in encoded


def test_yaml_raw_evidence_redaction_preserves_non_secret_contract_fields():
    module = load_module()
    documents = [
        {
            "kind": "Secret",
            "stringData": {
                "DB_TYPE": "gaussdb",
                "GAUSSDB_METADATA_PASSWORD": "helm-credential-value",
            },
        }
    ]

    encoded = module.redacted_yaml_bytes(documents)

    assert b"helm-credential-value" not in encoded
    assert b"<redacted>" in encoded
    assert b"DB_TYPE: gaussdb" in encoded


def test_dotenv_selected_values_follow_literal_env_file_entries():
    module = load_module()
    text = "A=one\nB=${B:-two}\n# C=ignored\nD=value=with=equals\n"

    assert module.dotenv_selected_values(text, {"A", "B", "D"}) == {
        "A": "one",
        "B": "${B:-two}",
        "D": "value=with=equals",
    }


def test_helm_metadata_contract_removes_mysql_for_gaussdb_values():
    module = load_module()
    common = {
        "template_exit_code": 0,
        "environment_config_resource_count": 1,
        "ragflow_workload_count": 1,
    }

    assert (
        module.helm_metadata_contract_ok(
            "control",
            {
                **common,
                "db_type": "mysql",
                "mysql_resource_count": 3,
                "mysql_credential_keys_present": True,
            },
        )
        is True
    )
    assert (
        module.helm_metadata_contract_ok(
            "experiment",
            {
                **common,
                "db_type": "gaussdb",
                "mysql_resource_count": 0,
                "mysql_credential_keys_present": False,
                "gauss_metadata_keys_present": True,
                "gauss_metadata_values_match": True,
            },
        )
        is True
    )
