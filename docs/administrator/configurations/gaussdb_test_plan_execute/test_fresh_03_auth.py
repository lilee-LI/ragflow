import hashlib
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_03_auth.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_03_auth", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_seed_tiktoken_cache_copies_only_validated_dependency(tmp_path, monkeypatch):
    module = load_module()
    source = tmp_path / "source.tiktoken"
    source.write_bytes(b"fresh validated tokenizer fixture")
    monkeypatch.setattr(
        module,
        "TIKTOKEN_ENCODING_SHA256",
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    root = tmp_path / "runtime"
    root.mkdir()

    target = module._seed_tiktoken_cache(root, candidates=(source,))

    assert target == root / module.TIKTOKEN_CACHE_KEY
    assert target.read_bytes() == source.read_bytes()


def test_seed_tiktoken_cache_rejects_unvalidated_dependency(tmp_path):
    module = load_module()
    source = tmp_path / "source.tiktoken"
    source.write_bytes(b"invalid")
    root = tmp_path / "runtime"
    root.mkdir()

    with pytest.raises(RuntimeError, match="validated tiktoken dependency"):
        module._seed_tiktoken_cache(root, candidates=(source,))


def test_successful_login_contract_requires_rotation_safe_data_and_session():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "authorization_present": True,
        "decoded_token_matches_database": True,
        "access_state_rotated": True,
        "last_login_changed": True,
        "update_time_changed": True,
        "safe_response": True,
        "session_cookie_present": True,
    }

    assert module.successful_login_contract_ok(observed) is True
    assert module.successful_login_contract_ok({**observed, "decoded_token_matches_database": False}) is False


def test_rejected_login_contract_requires_exact_code_and_unchanged_state():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 109,
        "authorization_absent": True,
        "auth_state_unchanged": True,
        "last_login_unchanged": True,
    }

    assert module.rejected_login_contract_ok(observed, 109) is True


def test_logout_contract_requires_invalid_database_state_and_old_token_401():
    module = load_module()
    observed = {
        "logout_http_status": 200,
        "logout_code": 0,
        "logout_data": True,
        "invalid_prefix": True,
        "old_token_http_status": 401,
        "old_token_code": 401,
    }

    assert module.logout_contract_ok(observed) is True


def test_dynamic_case_inventory_contains_main_and_supplement_ids():
    module = load_module()

    assert len(module.CASE_TITLES) == 67
    assert "TC-AT-001" in module.CASE_TITLES
    assert "TC-AT-SOFTDEL-001" in module.CASE_TITLES


def test_gauss_writable_search_path_is_set_before_transaction_mode(monkeypatch):
    module = load_module()
    events = []

    class Connection:
        @property
        def autocommit(self):
            return None

        @autocommit.setter
        def autocommit(self, value):
            events.append(("autocommit", value))

    monkeypatch.setattr(
        module.BASE,
        "set_gauss_search_path",
        lambda _connection, schema: events.append(("search_path", schema)),
    )

    module._initialize_gauss_writable_connection(Connection(), "isolated_metadata", False)

    assert events == [
        ("autocommit", True),
        ("search_path", "isolated_metadata"),
        ("autocommit", False),
    ]


def test_oauth_new_user_contract_requires_redirect_token_and_complete_graph():
    module = load_module()
    observed = {
        "callback_http_status": 302,
        "auth_query_present": True,
        "decoded_token_matches_database": True,
        "user_count": 1,
        "tenant_count": 1,
        "owner_relation_count": 1,
        "root_file_count": 1,
        "login_channel": "testoauth",
    }

    assert module.oauth_new_user_contract_ok(observed) is True


def test_oauth_existing_user_contract_requires_rotation_without_duplicate():
    module = load_module()
    observed = {
        "callback_http_status": 302,
        "auth_query_present": True,
        "decoded_token_matches_database": True,
        "user_count": 1,
        "access_state_rotated": True,
        "last_login_unchanged": True,
    }

    assert module.oauth_existing_user_contract_ok(observed) is True


def test_captcha_and_otp_contracts_require_real_cache_and_mail_state():
    module = load_module()

    assert (
        module.captcha_contract_ok(
            {
                "http_status": 200,
                "jpeg_content_type": True,
                "captcha_length": 4,
                "captcha_ttl_in_range": True,
            }
        )
        is True
    )
    assert (
        module.otp_send_contract_ok(
            {
                "http_status": 200,
                "code": 0,
                "data": True,
                "stored_hash_format": True,
                "otp_ttl_in_range": True,
                "last_sent_present": True,
                "mail_count": 1,
                "mail_otp_length": 4,
            }
        )
        is True
    )


def test_password_reset_contract_requires_hash_change_verified_consumption_and_logins():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "authorization_present": True,
        "authorization_usable": True,
        "password_hash_changed": True,
        "verified_key_absent": True,
        "new_password_login_code": 0,
        "old_password_login_code": 109,
    }

    assert module.password_reset_contract_ok(observed) is True


def test_api_token_contract_requires_prefix_beta_length_and_database_match():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "token_prefix": True,
        "beta_length": 32,
        "tenant_matches": True,
        "database_count": 1,
        "database_values_match": True,
    }

    assert module.api_token_contract_ok(observed) is True


def test_session_contract_requires_cookie_only_profile_success():
    module = load_module()
    observed = {
        "login_code": 0,
        "session_cookie_present": True,
        "profile_http_status": 200,
        "profile_code": 0,
        "profile_email_matches": True,
    }

    assert module.session_auth_contract_ok(observed) is True


def test_encryption_chain_contract_requires_base64_hash_and_signed_access_state():
    module = load_module()
    observed = {
        "rsa_roundtrip_to_base64": True,
        "decrypted_is_not_plaintext": True,
        "hash_accepts_base64": True,
        "hash_rejects_plaintext": True,
        "signed_access_roundtrip": True,
        "tampered_signature_rejected": True,
    }

    assert module.encryption_chain_contract_ok(observed) is True


def test_user_query_filter_contract_requires_all_invalid_inputs_empty():
    module = load_module()
    observed = {
        "empty_count": 0,
        "none_count": 0,
        "space_count": 0,
        "short_count": 0,
        "invalid_prefix_count": 0,
        "normal_query_executed": True,
        "invalid_row_exists": True,
    }

    assert module.user_query_filter_contract_ok(observed) is True


def test_registration_disabled_contract_requires_password_and_oauth_rejection():
    module = load_module()
    observed = {
        "config_http_status": 200,
        "config_code": 0,
        "register_enabled": 0,
        "password_http_status": 200,
        "password_code": 103,
        "password_graph_count": 0,
        "oauth_http_status": 302,
        "oauth_error_present": True,
        "oauth_auth_absent": True,
        "oauth_graph_count": 0,
        "isolated_process_stopped": True,
        "main_api_healthy": True,
    }

    assert module.registration_disabled_contract_ok(observed) is True
    assert (
        module.registration_disabled_contract_ok(
            {
                **observed,
                "oauth_error_present": False,
                "oauth_auth_absent": False,
                "oauth_graph_count": 4,
            }
        )
        is False
    )


def test_active_required_contract_requires_exact_forbidden_and_reactivation():
    module = load_module()
    observed = {
        "login_code": 0,
        "disable_code": 0,
        "route_http_status": 200,
        "route_code": 403,
        "reactivate_code": 0,
        "isolated_process_stopped": True,
        "main_api_healthy": True,
    }

    assert module.active_required_contract_ok(observed) is True
    assert module.active_required_contract_ok({**observed, "route_code": 0}) is False


def test_metadata_fault_contract_requires_explicit_500_and_recovery():
    module = load_module()
    observed = {
        "proxy_down_confirmed": True,
        "login_http_status": 500,
        "login_failed_finitely": True,
        "profile_http_status": 500,
        "profile_failed_finitely": True,
        "proxy_restored": True,
        "recovery_login_code": 0,
        "main_api_healthy": True,
    }

    assert module.metadata_fault_contract_ok(observed) is True
    assert module.metadata_fault_contract_ok({**observed, "login_http_status": 200}) is False


def test_empty_string_contract_uses_group_specific_physical_storage():
    module = load_module()
    common = {
        "user_service_empty_count": 0,
        "orm_nickname": "",
        "login_restored": True,
        "nickname_restored": True,
    }

    assert (
        module.empty_string_contract_ok(
            "control",
            {**common, "physical_access_is_empty": True, "physical_nickname_is_empty": True},
        )
        is True
    )
    assert (
        module.empty_string_contract_ok(
            "experiment",
            {**common, "physical_access_is_null": True, "physical_nickname_is_null": True},
        )
        is True
    )


def test_registration_compensation_contract_requires_complete_normal_graph_and_no_residue():
    module = load_module()
    observed = {
        "normal_register_code": 0,
        "normal_graph_count": 4,
        "normal_cleanup_succeeded": True,
        "fault_request_failed": True,
        "table_restored": True,
        "failed_graph_count": 0,
        "recovery_login_code": 0,
    }

    assert module.registration_compensation_contract_ok(observed) is True
    assert module.registration_compensation_contract_ok({**observed, "failed_graph_count": 1}) is False


def test_composite_token_contract_requires_unique_rows_rejected_duplicate_and_cleanup():
    module = load_module()
    observed = {
        "create_codes": [0, 0, 0],
        "created_distinct_count": 3,
        "duplicate_groups": 0,
        "duplicate_insert_rejected": True,
        "count_unchanged_after_duplicate": True,
        "delete_codes": [0, 0, 0],
        "remaining_fixture_count": 0,
    }

    assert module.composite_token_contract_ok(observed) is True
    assert module.composite_token_contract_ok({**observed, "duplicate_insert_rejected": False}) is False


def test_soft_deleted_registration_contract_requires_single_original_row():
    module = load_module()
    observed = {
        "status_before": "0",
        "http_status": 200,
        "code": 103,
        "duplicate_message": True,
        "user_count": 1,
        "status_after": "0",
    }

    assert module.soft_deleted_registration_contract_ok(observed) is True


def test_soft_deleted_reset_security_contract_requires_rejection_and_unchanged_hash():
    module = load_module()
    observed = {
        "status": "0",
        "security_rejection_http_400": True,
        "reset_succeeded": False,
        "password_hash_unchanged": True,
    }

    assert module.soft_deleted_reset_security_contract_ok(observed) is True
    assert (
        module.soft_deleted_reset_security_contract_ok(
            {
                **observed,
                "security_rejection_http_400": False,
                "reset_succeeded": True,
                "password_hash_unchanged": False,
            }
        )
        is False
    )


def test_soft_deleted_oauth_security_contract_requires_no_auth_or_state_change():
    module = load_module()
    observed = {
        "status": "0",
        "callback_http_status": 302,
        "error_present": True,
        "auth_absent": True,
        "access_state_unchanged": True,
        "last_login_unchanged": True,
    }

    assert module.soft_deleted_oauth_security_contract_ok(observed) is True
    assert (
        module.soft_deleted_oauth_security_contract_ok(
            {
                **observed,
                "error_present": False,
                "auth_absent": False,
                "access_state_unchanged": False,
            }
        )
        is False
    )


def test_keypair_match_contract_requires_frontend_backend_fingerprint_and_login():
    module = load_module()
    observed = {
        "public_fingerprints_match": True,
        "key_bits": 2048,
        "login_http_status": 200,
        "login_code": 0,
        "signed_header_present": True,
        "issued_value_matches_database": True,
    }

    assert module.keypair_match_contract_ok(observed) is True
    assert module.keypair_match_contract_ok({**observed, "public_fingerprints_match": False}) is False


def test_keypair_mismatch_contract_requires_isolation_crypt_error_and_cleanup():
    module = load_module()
    observed = {
        "isolated_public_differs": True,
        "http_status": 200,
        "code": 500,
        "message": "Fail to crypt password",
        "decryption_error_marker_count": 1,
        "main_private_unchanged": True,
        "isolated_process_stopped": True,
        "main_api_healthy": True,
    }

    assert module.keypair_mismatch_contract_ok(observed) is True


def test_frontend_one_line_public_key_is_normalized_and_parsed_without_exposure():
    module = load_module()
    info = module._frontend_public_key_info()

    assert info["bits"] == 2048
    assert len(info["fingerprint"]) == 64
    assert info["_pem"].startswith("-----BEGIN PUBLIC KEY-----\n")
    assert info["_pem"].endswith("\n-----END PUBLIC KEY-----")


def test_token_401_contracts_require_three_key_cleanup_and_single_redirect():
    module = load_module()
    cleanup = {
        "authorization_absent": True,
        "token_key_absent": True,
        "user_info_absent": True,
        "redirect_calls": 1,
        "frontend_probe_passed": True,
    }

    assert module.token_401_cleanup_contract_ok(cleanup) is True
    assert module.token_401_cleanup_contract_ok({**cleanup, "user_info_absent": False}) is False
    assert module.token_401_single_redirect_contract_ok(cleanup) is True
    assert module.token_401_single_redirect_contract_ok({**cleanup, "redirect_calls": 3}) is False


def test_oauth_callback_contracts_require_raw_storage_url_cleanup_and_noop():
    module = load_module()
    callback = {
        "authorization_equals_callback": True,
        "bearer_prefix_absent": True,
        "user_info_absent": True,
        "query_auth_removed": True,
        "navigate_root_calls": 1,
        "frontend_probe_passed": True,
    }
    noop = {
        "authorization_absent": True,
        "set_search_calls": 0,
        "navigate_calls": 0,
        "frontend_probe_passed": True,
    }

    assert module.oauth_callback_contract_ok(callback) is True
    assert module.oauth_callback_contract_ok({**callback, "bearer_prefix_absent": False}) is False
    assert module.oauth_callback_noop_contract_ok(noop) is True


def test_short_password_security_contracts_require_registration_rejection():
    module = load_module()
    registration = {
        "http_status": 400,
        "account_count": 0,
        "signed_header_absent": True,
        "cleanup_succeeded": True,
    }
    login = {
        "registration_rejected": True,
        "account_count_before_login": 0,
        "login_succeeded": False,
        "signed_header_absent": True,
        "cleanup_succeeded": True,
    }

    assert module.short_password_registration_security_contract_ok(registration) is True
    assert module.short_password_registration_security_contract_ok({**registration, "http_status": 200, "account_count": 1}) is False
    assert module.short_password_login_security_contract_ok(login) is True
    assert (
        module.short_password_login_security_contract_ok(
            {
                **login,
                "registration_rejected": False,
                "account_count_before_login": 1,
                "login_succeeded": True,
                "signed_header_absent": False,
            }
        )
        is False
    )


def test_exception_contracts_require_protocol_400_characterized_missing_field_and_413():
    module = load_module()

    assert module.invalid_json_contract_ok({"http_status": 400}) is True
    assert module.invalid_json_contract_ok({"http_status": 200, "code": 100}) is False
    assert (
        module.missing_password_contract_ok(
            {
                "http_status": 200,
                "code": 500,
                "message": "Fail to crypt password",
            }
        )
        is True
    )


def test_frontend_probe_parser_accepts_one_exact_sanitized_observation():
    module = load_module()
    output = 'FRESH_OBSERVED={"case_id":"TC-AT-TOKEN-401-001","group":"control","redirect_calls":1}\nPASS suite\n'

    assert module._parse_frontend_probe_output(output, "TC-AT-TOKEN-401-001", "control") == {
        "case_id": "TC-AT-TOKEN-401-001",
        "group": "control",
        "redirect_calls": 1,
    }
    assert (
        module.oversize_request_contract_ok(
            {
                "http_status": 413,
                "configured_limit": 1048576,
                "request_length_exceeds_limit": True,
                "isolated_process_stopped": True,
                "main_api_healthy": True,
            }
        )
        is True
    )
