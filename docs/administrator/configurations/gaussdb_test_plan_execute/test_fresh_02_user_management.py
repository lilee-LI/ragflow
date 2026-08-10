import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_02_user_management.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_02_user_management", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_successful_registration_contract_requires_api_and_four_database_objects():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "auth_header_present": True,
        "response_email_matches": True,
        "response_nickname_matches": True,
        "database": {
            "user_count": 1,
            "status": "1",
            "is_active": "1",
            "is_superuser": False,
            "login_channel": "password",
            "credential_hash_scheme": "scrypt",
            "plaintext_credential_absent": True,
            "tenant_count": 1,
            "owner_relation_count": 1,
            "root_file_count": 1,
        },
    }

    assert module.successful_registration_contract_ok(observed) is True
    assert module.successful_registration_contract_ok({**observed, "auth_header_present": False}) is False


def test_rejected_registration_contract_requires_all_codes_and_no_rows():
    module = load_module()
    observed = {
        "responses": [
            {"http_status": 200, "code": 101},
            {"http_status": 200, "code": 101},
        ],
        "database_row_count": 0,
    }

    assert module.rejected_registration_contract_ok(observed, [101, 101]) is True
    assert module.rejected_registration_contract_ok(observed, [103, 103]) is False


def test_registration_value_contract_checks_database_value_and_length():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "auth_header_present": True,
        "database_row_count": 1,
        "stored_value": "测试用户",
        "stored_length": 4,
    }

    assert module.registration_value_contract_ok(observed, "测试用户", 4) is True
    assert module.registration_value_contract_ok(observed, "测试用户", 5) is False


def test_duplicate_registration_contract_keeps_exactly_one_user_graph():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 103,
        "user_count": 1,
        "tenant_count": 1,
        "owner_relation_count": 1,
    }

    assert module.duplicate_registration_contract_ok(observed) is True
    assert module.duplicate_registration_contract_ok({**observed, "user_count": 0}) is False


def test_gauss_readonly_options_include_utf8_and_schema():
    module = load_module()

    options = module.gauss_readonly_options("public")
    assert "client_encoding=UTF8" in options
    assert "default_transaction_read_only=on" in options
    assert "search_path=public" in options


def test_gauss_direct_connection_explicitly_sets_search_path():
    module = load_module()
    calls = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def execute(self, query):
            calls.append(query)

    class Connection:
        def cursor(self):
            return Cursor()

    module.set_gauss_search_path(Connection(), "isolated_metadata")

    assert "SET search_path TO" in str(calls[0])
    assert "Identifier('isolated_metadata')" in str(calls[0])


def test_successful_login_contract_requires_rotated_state_and_safe_response():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "auth_header_present": True,
        "response_email_matches": True,
        "sensitive_fields_absent": True,
        "last_login_changed": True,
        "auth_state_changed": True,
    }

    assert module.successful_login_contract_ok(observed) is True
    assert module.successful_login_contract_ok({**observed, "sensitive_fields_absent": False}) is False


def test_rejected_login_contract_requires_unchanged_auth_state():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 109,
        "last_login_unchanged": True,
        "auth_state_unchanged": True,
    }

    assert module.rejected_login_contract_ok(observed, 109) is True
    assert module.rejected_login_contract_ok({**observed, "auth_state_unchanged": False}, 109) is False


def test_token_access_contract_requires_valid_and_invalid_paths():
    module = load_module()
    observed = {
        "valid_http_status": 200,
        "valid_code": 0,
        "valid_email_matches": True,
        "invalid_http_status": 401,
        "invalid_code": 401,
    }

    assert module.token_access_contract_ok(observed) is True
    assert module.token_access_contract_ok({**observed, "invalid_http_status": 200}) is False


def test_full_user_flow_contract_requires_logout_invalidation_and_new_login():
    module = load_module()
    observed = {
        "registration_code": 0,
        "registration_profile_code": 0,
        "logout_code": 0,
        "invalid_prefix_after_logout": True,
        "old_auth_http_status": 401,
        "old_auth_code": 401,
        "new_login_code": 0,
        "new_profile_code": 0,
        "new_auth_not_invalid": True,
        "tenant_count": 1,
        "owner_relation_count": 1,
    }

    assert module.full_user_flow_contract_ok(observed) is True


def test_profile_update_contract_requires_api_profile_and_database_agreement():
    module = load_module()
    observed = {
        "patch_code": 0,
        "patch_data": True,
        "profile_code": 0,
        "profile_matches": True,
        "database_matches": True,
    }

    assert module.profile_update_contract_ok(observed) is True


def test_protected_fields_contract_allows_only_nickname_change():
    module = load_module()
    observed = {
        "patch_code": 0,
        "email_unchanged": True,
        "status_unchanged": True,
        "superuser_unchanged": True,
        "active_unchanged": True,
        "nickname_changed": True,
    }

    assert module.protected_fields_contract_ok(observed) is True
    assert module.protected_fields_contract_ok({**observed, "superuser_unchanged": False}) is False


def test_password_change_contract_requires_new_success_old_rejection_and_new_hash():
    module = load_module()
    observed = {
        "patch_code": 0,
        "new_login_code": 0,
        "old_login_code": 109,
        "credential_hash_changed": True,
    }

    assert module.password_change_contract_ok(observed) is True


def test_status_and_role_contracts_require_exact_database_and_api_values():
    module = load_module()

    assert (
        module.status_transition_contract_ok(
            {"admin_code": 0, "status": "1", "is_active": "0", "login_code": 403},
            expected_status="1",
            expected_active="0",
            expected_login_code=403,
        )
        is True
    )
    assert (
        module.role_transition_contract_ok(
            {"admin_code": 0, "database_superuser": True, "login_superuser": True},
            expected=True,
        )
        is True
    )


def test_idempotent_status_update_uses_final_value_not_driver_rowcount():
    module = load_module()

    assert module.idempotent_status_update_ok(0, "1", "1") is True
    assert module.idempotent_status_update_ok(1, "1", "1") is True
    assert module.idempotent_status_update_ok(0, None, "1") is False


def test_cascade_delete_contract_requires_all_saved_identifiers_absent():
    module = load_module()
    observed = {
        "admin_delete_code": 0,
        "user_count": 0,
        "tenant_count": 0,
        "relation_count": 0,
        "file_count": 0,
        "dataset_count": 0,
    }

    assert module.cascade_delete_contract_ok(observed) is True
    assert module.cascade_delete_contract_ok({**observed, "file_count": 1}) is False


def test_recreation_contract_requires_new_id_and_one_new_record():
    module = load_module()
    observed = {
        "first_create_code": 0,
        "delete_code": 0,
        "count_after_delete": 0,
        "old_graph_absent": True,
        "second_create_code": 0,
        "ids_distinct": True,
        "final_count": 1,
        "final_nickname_matches": True,
    }

    assert module.recreation_contract_ok(observed) is True
    assert module.recreation_contract_ok({**observed, "old_graph_absent": False}) is False


def test_tenant_membership_contract_requires_invite_role_and_owner_exclusion():
    module = load_module()
    observed = {
        "invite_code": 0,
        "list_code": 0,
        "target_in_list": True,
        "owner_in_list": False,
        "database_role": "invite",
        "target_relation_count": 2,
    }

    assert module.tenant_membership_contract_ok(observed) is True


def test_runtime_signing_secret_prefers_explicit_environment_then_redis():
    module = load_module()

    assert module.runtime_signing_secret({"RAGFLOW_SECRET_KEY": "e" * 64}, "r" * 64) == "e" * 64
    assert module.runtime_signing_secret({}, "r" * 64) == "r" * 64
    assert module.runtime_signing_secret({}, None) is None


def test_connect_with_retry_recovers_transient_error_and_preserves_final_error():
    module = load_module()
    attempts = []

    def transient():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("transient")
        return "connected"

    assert module.connect_with_retry(transient, attempts=2, delay_seconds=0) == "connected"
    assert len(attempts) == 2

    with pytest.raises(OSError, match="persistent"):
        module.connect_with_retry(
            lambda: (_ for _ in ()).throw(OSError("persistent")),
            attempts=2,
            delay_seconds=0,
        )


def test_physical_delete_precondition_requires_success_and_no_remaining_row():
    module = load_module()

    assert module.physical_delete_precondition_ok(0, 0) is True
    assert module.physical_delete_precondition_ok(-1, 1) is False
    assert module.physical_delete_precondition_ok(0, 1) is False


def test_avatar_empty_contract_distinguishes_mysql_empty_from_gauss_null():
    module = load_module()
    common = {
        "patch_http_status": 200,
        "patch_code": 0,
        "patch_data": True,
        "profile_code": 0,
        "profile_avatar_present": True,
    }

    assert (
        module.avatar_empty_contract_ok(
            "control",
            {
                **common,
                "database_avatar": "",
                "database_is_null": False,
                "profile_avatar": "",
            },
        )
        is True
    )
    assert (
        module.avatar_empty_contract_ok(
            "experiment",
            {
                **common,
                "database_avatar": None,
                "database_is_null": True,
                "profile_avatar": None,
            },
        )
        is True
    )


def test_nickname_rejection_contract_requires_both_rejections_and_no_change():
    module = load_module()
    observed = {
        "responses": [
            {"http_status": 200, "code": 101, "data": False},
            {"http_status": 200, "code": 101, "data": False},
        ],
        "nickname_unchanged": True,
    }

    assert module.nickname_rejection_contract_ok(observed) is True
    assert module.nickname_rejection_contract_ok({**observed, "nickname_unchanged": False}) is False


def test_special_nickname_contract_keeps_valid_value_after_invalid_attempt():
    module = load_module()
    observed = {
        "valid_patch_code": 0,
        "invalid_patch_code": 101,
        "database_value_matches": True,
        "profile_value_matches": True,
    }

    assert module.special_nickname_contract_ok(observed) is True


def test_nonempty_nickname_storage_contract_requires_character_and_octet_lengths():
    module = load_module()
    observed = {
        "patch_code": 0,
        "profile_code": 0,
        "database_value": "Space User",
        "database_length": 10,
        "database_octet_length": 10,
        "profile_value": "Space User",
    }

    assert module.nonempty_nickname_storage_contract_ok(observed) is True
    assert module.nonempty_nickname_storage_contract_ok({**observed, "database_length": 9}) is False


def test_concurrent_registration_contract_requires_five_complete_user_graphs():
    module = load_module()
    observed = {
        "cleanup_ok": True,
        "response_count": 5,
        "all_response_codes_zero": True,
        "all_auth_headers_present": True,
        "database_email_set_matches": True,
        "database_rows": [
            {
                "status": "1",
                "tenant_count": 1,
                "owner_relation_count": 1,
            }
            for _ in range(5)
        ],
    }

    assert module.concurrent_registration_contract_ok(observed) is True
    assert module.concurrent_registration_contract_ok({**observed, "all_response_codes_zero": False}) is False


def test_wrong_password_change_contract_requires_unchanged_hash_and_passwords():
    module = load_module()
    observed = {
        "patch_http_status": 200,
        "patch_code": 109,
        "patch_data": False,
        "credential_hash_unchanged": True,
        "current_password_login_code": 0,
        "proposed_password_login_code": 109,
    }

    assert module.wrong_password_change_contract_ok(observed) is True


def test_logout_invalidation_contract_requires_invalid_prefix_and_old_token_rejection():
    module = load_module()
    observed = {
        "logout_http_status": 200,
        "logout_code": 0,
        "logout_data": True,
        "auth_state_changed": True,
        "invalid_prefix": True,
        "old_auth_http_status": 401,
        "old_auth_code": 401,
    }

    assert module.logout_invalidation_contract_ok(observed) is True
