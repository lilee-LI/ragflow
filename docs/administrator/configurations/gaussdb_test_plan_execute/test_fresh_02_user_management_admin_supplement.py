import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).with_name("fresh_02_user_management_admin_supplement.py")
EXPECTED_IDS = [
    *(f"TC-UM-ADMIN-{number:03d}" for number in range(1, 21)),
    *(f"TC-UM-SET-{number:03d}" for number in range(1, 6)),
]


def load_module():
    name = "fresh_02_user_management_admin_supplement_contract"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_registry_is_exact_and_ordered():
    module = load_module()

    assert list(module.CASE_TITLES) == EXPECTED_IDS
    assert list(module.get_runners()) == EXPECTED_IDS
    assert all(callable(runner) for runner in module.get_runners().values())


def test_admin_wire_payload_encrypts_only_password_fields_without_mutation():
    module = load_module()
    source = {
        "username": "fresh@example.invalid",
        "password": "old-plain",
        "new_password": "new-plain",
        "activate_status": "off",
    }

    wire = module.prepare_admin_wire_payload(source, lambda value: f"cipher({value})")

    assert wire == {
        "username": "fresh@example.invalid",
        "password": "cipher(old-plain)",
        "new_password": "cipher(new-plain)",
        "activate_status": "off",
    }
    assert source["password"] == "old-plain"
    assert source["new_password"] == "new-plain"
    assert "old-plain" not in module.admin_request_summary("POST", "/users", source)
    assert "new-plain" not in module.admin_request_summary("POST", "/users", source)


def test_cleanup_order_is_public_api_only_and_role_aware():
    module = load_module()

    assert module.cleanup_action_order(False) == ("deactivate", "delete")
    assert module.cleanup_action_order(True) == (
        "revoke_admin",
        "deactivate",
        "delete",
    )


def test_authenticated_admin_request_reauthenticates_after_transient_401_and_login_failure():
    module = load_module()
    contexts = iter([RuntimeError("temporary connection exhaustion"), "fresh-context"])
    sent = []
    pauses = []

    def open_context():
        value = next(contexts)
        if isinstance(value, Exception):
            raise value
        return value

    def send(context):
        sent.append(context)
        status = 401 if context == "stale-context" else 200
        return SimpleNamespace(status_code=status)

    response, attempts = module.authenticated_admin_request(
        open_context,
        send,
        initial_context="stale-context",
        max_attempts=4,
        delay_seconds=0.25,
        pause=pauses.append,
    )

    assert response.status_code == 200
    assert attempts == 3
    assert sent == ["stale-context", "fresh-context"]
    assert pauses == [0.25, 0.25]


def test_create_and_rejection_contracts_require_http_business_and_database_oracles():
    module = load_module()
    created = {
        "http_status": 200,
        "code": 0,
        "response_email_matches": True,
        "response_superuser": False,
        "database": {
            "user_count": 1,
            "nickname": "",
            "is_superuser": False,
            "tenant_count": 1,
            "owner_relation_count": 1,
            "root_file_count": 1,
        },
    }

    assert module.admin_create_contract_ok(created, expected_superuser=False)
    assert not module.admin_create_contract_ok(
        {**created, "database": {**created["database"], "tenant_count": 0}},
        expected_superuser=False,
    )
    assert module.admin_rejection_contract_ok(
        {"http_status": 400, "code": 409, "message": "already exists"},
        expected_code=409,
        message_fragment="already exists",
    )


def test_delete_transition_and_query_contracts_are_strict():
    module = load_module()

    assert module.cascade_delete_contract_ok(
        {
            "http_status": 200,
            "code": 0,
            "user_count": 0,
            "tenant_count": 0,
            "relation_count": 0,
            "dataset_count": 0,
            "dialog_count": 0,
        }
    )
    assert module.state_transition_contract_ok({"http_status": 200, "code": 0, "database_value": "0"}, "0")
    assert module.role_transition_contract_ok({"http_status": 200, "code": 0, "database_value": True}, True)
    assert module.details_contract_ok(
        {
            "http_status": 200,
            "code": 0,
            "row_count": 1,
            "fields": sorted(module.ADMIN_DETAIL_FIELDS),
        }
    )
    assert module.empty_collection_contract_ok({"http_status": 200, "code": 0, "api_count": 0, "database_count": 0})
    assert module.forbidden_contract_ok({"http_status": 403})
    assert module.forbidden_failure_findings({"http_status": 403}) == []
    assert module.forbidden_failure_findings({"http_status": 500}) == [
        {
            "type": "admin_exception_mapping_defect",
            "source": "admin/server/auth.py::check_admin_auth; admin/server/admin_server.py",
            "detail": "AdminException(code=403) 未注册全局异常映射，最终返回 HTTP 500。",
        }
    ]


def test_settings_contracts_preserve_fields_and_validate_credentials_and_nickname():
    module = load_module()
    protected = {
        "http_status": 200,
        "code": 0,
        "nickname": "NewName",
        "email_unchanged": True,
        "status_unchanged": True,
        "superuser_unchanged": True,
    }

    assert module.protected_settings_contract_ok(protected)
    assert module.password_settings_contract_ok(
        {
            "http_status": 200,
            "code": 0,
            "hash_changed": True,
            "new_login_code": 0,
            "old_login_code": 109,
        }
    )
    assert module.settings_rejection_contract_ok(
        {"http_status": 200, "code": 109, "message": "Password error!"},
        expected_code=109,
        message_fragment="Password error!",
    )
    assert module.nickname_settings_contract_ok(
        {"http_status": 200, "code": 0, "stored_nickname": "Spaced Name"},
        "Spaced Name",
    )
    assert module.settings_rejection_contract_ok(
        {
            "http_status": 200,
            "code": 101,
            "message": "Nickname contains invalid characters.",
        },
        expected_code=101,
        message_fragment="Nickname",
    )
