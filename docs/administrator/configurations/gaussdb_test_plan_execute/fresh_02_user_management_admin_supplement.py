#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable

import requests

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = evidence_dir("02_user_management_admin_supplement")
RAW_DIR = EVIDENCE_DIR / "raw"
GROUP_ORDER = ("control", "experiment")

CASE_TITLES = {
    "TC-UM-ADMIN-001": "Admin 创建用户 - 正常流程",
    "TC-UM-ADMIN-002": "Admin 创建用户 - 重复邮箱",
    "TC-UM-ADMIN-003": "Admin 创建用户 - 无效邮箱",
    "TC-UM-ADMIN-004": "Admin 创建 superuser",
    "TC-UM-ADMIN-005": "Admin 删除用户 - 需要先停用",
    "TC-UM-ADMIN-006": "Admin 删除用户 - 完整流程",
    "TC-UM-ADMIN-007": "Admin 删除 superuser - 被拒绝",
    "TC-UM-ADMIN-008": "Admin 修改密码 - 不需要旧密码",
    "TC-UM-ADMIN-009": "Admin 修改密码 - 相同密码",
    "TC-UM-ADMIN-010": "Admin 激活用户",
    "TC-UM-ADMIN-011": "Admin 停用用户",
    "TC-UM-ADMIN-012": "Admin 激活状态 - 无效值",
    "TC-UM-ADMIN-013": "Admin grant admin - 正常流程",
    "TC-UM-ADMIN-014": "Admin grant admin - 自我操作被拒绝",
    "TC-UM-ADMIN-015": "Admin revoke admin - 正常流程",
    "TC-UM-ADMIN-016": "Admin revoke admin - 自我操作被拒绝",
    "TC-UM-ADMIN-017": "Admin 查看用户详情",
    "TC-UM-ADMIN-018": "Admin 查看用户数据集",
    "TC-UM-ADMIN-019": "Admin 查看用户 agents",
    "TC-UM-ADMIN-020": "非 superuser 访问 Admin API - 被拒绝",
    "TC-UM-SET-001": "修改个人资料 - 受保护字段被忽略",
    "TC-UM-SET-002": "修改密码 - 需要旧密码",
    "TC-UM-SET-003": "修改密码 - 旧密码错误",
    "TC-UM-SET-004": "修改 nickname - 自动 strip",
    "TC-UM-SET-005": "修改 nickname - 验证正则",
}

ADMIN_DETAIL_FIELDS = (
    "avatar",
    "email",
    "language",
    "last_login_time",
    "is_active",
    "is_anonymous",
    "login_channel",
    "status",
    "is_superuser",
    "create_date",
    "update_date",
)


def _load_local_module(name: str, filename: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_local_module("fresh_02_user_management_current", "fresh_02_user_management.py")
EVIDENCE = _load_local_module("fresh_case_evidence_admin_supplement", "fresh_case_evidence.py")
BASE.EVIDENCE_DIR = EVIDENCE_DIR
BASE.RAW_DIR = RAW_DIR


def prepare_admin_wire_payload(
    payload: dict[str, Any] | None,
    encrypt: Callable[[str], str],
) -> dict[str, Any] | None:
    if payload is None:
        return None
    wire = dict(payload)
    for field in ("password", "new_password"):
        if field in wire:
            wire[field] = encrypt(str(wire[field]))
    return wire


def admin_request_summary(method: str, path: str, payload: dict[str, Any] | None) -> str:
    fields = sorted(payload) if payload else []
    sensitive = sorted(field for field in fields if field in {"password", "new_password"})
    return json.dumps(
        {
            "method": method.upper(),
            "path": path,
            "json_fields": fields,
            "encrypted_fields": sensitive,
        },
        sort_keys=True,
    )


def cleanup_action_order(is_superuser: bool) -> tuple[str, ...]:
    if is_superuser:
        return ("revoke_admin", "deactivate", "delete")
    return ("deactivate", "delete")


def admin_create_contract_ok(observed: dict[str, Any], *, expected_superuser: bool) -> bool:
    database = observed.get("database", {})
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("response_email_matches") is True
        and observed.get("response_superuser") is expected_superuser
        and database.get("user_count") == 1
        and database.get("nickname") == ""
        and database.get("is_superuser") is expected_superuser
        and database.get("tenant_count") == 1
        and database.get("owner_relation_count") == 1
        and database.get("root_file_count") == 1
    )


def admin_rejection_contract_ok(
    observed: dict[str, Any],
    *,
    expected_code: int | None = None,
    message_fragment: str,
) -> bool:
    code_ok = expected_code is None or observed.get("code") == expected_code
    return observed.get("http_status") == 400 and code_ok and message_fragment.lower() in str(observed.get("message", "")).lower()


def cascade_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("user_count") == 0
        and observed.get("tenant_count") == 0
        and observed.get("relation_count") == 0
        and observed.get("dataset_count") == 0
        and observed.get("dialog_count") == 0
        and observed.get("file_count", 0) == 0
        and observed.get("agent_count", 0) == 0
    )


def state_transition_contract_ok(observed: dict[str, Any], expected_value: str) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and str(observed.get("database_value")) == expected_value


def role_transition_contract_ok(observed: dict[str, Any], expected_value: bool) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("database_value") is expected_value


def details_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("row_count") == 1 and set(ADMIN_DETAIL_FIELDS).issubset(set(observed.get("fields", [])))


def empty_collection_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("api_count") == 0 and observed.get("database_count") == 0


def forbidden_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 403


def forbidden_failure_findings(observed: dict[str, Any]) -> list[dict[str, Any]]:
    if observed.get("http_status") != 500:
        return []
    return [
        {
            "type": "admin_exception_mapping_defect",
            "source": "admin/server/auth.py::check_admin_auth; admin/server/admin_server.py",
            "detail": "AdminException(code=403) 未注册全局异常映射，最终返回 HTTP 500。",
        }
    ]


def protected_settings_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("nickname") == "NewName"
        and observed.get("email_unchanged") is True
        and observed.get("status_unchanged") is True
        and observed.get("superuser_unchanged") is True
    )


def password_settings_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("hash_changed") is True and observed.get("new_login_code") == 0 and observed.get("old_login_code") == 109


def settings_rejection_contract_ok(observed: dict[str, Any], *, expected_code: int, message_fragment: str) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == expected_code and message_fragment.lower() in str(observed.get("message", "")).lower()


def nickname_settings_contract_ok(observed: dict[str, Any], expected_nickname: str) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("stored_nickname") == expected_nickname


def _fixture_email(case_id: str, group: str) -> str:
    stem = case_id.removeprefix("TC-UM-").lower()
    return f"{stem}-{group}@fresh.invalid"


def _fixture_password(case_id: str, group: str, suffix: str = "Current") -> str:
    number = case_id.rsplit("-", 1)[-1]
    family = "Admin" if "-ADMIN-" in case_id else "Settings"
    return f"Fresh-{family}-{number}-{group}-{suffix}@1234"


def _environment(group: str) -> dict[str, Any]:
    return json.loads((BASE.RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]


def _public_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if not key.startswith("_")}


def authenticated_admin_request(
    open_context: Callable[[], Any],
    send: Callable[[Any], Any],
    *,
    initial_context: Any = None,
    max_attempts: int = 10,
    delay_seconds: float = 2.0,
    pause: Callable[[float], Any] = time.sleep,
) -> tuple[Any, int]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    context = initial_context
    last_response = None
    for attempt in range(1, max_attempts + 1):
        try:
            if context is None:
                context = open_context()
            response = send(context)
        except RuntimeError:
            context = None
            if attempt == max_attempts:
                raise
            pause(delay_seconds)
            continue
        last_response = response
        if response.status_code != 401:
            return response, attempt
        context = None
        if attempt < max_attempts:
            pause(delay_seconds)
    assert last_response is not None
    return last_response, max_attempts


def _database_snapshot(group: str, email: str, *, known_user_id: str | None = None) -> dict[str, Any]:
    connection, user_table, _namespace = BASE._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,email,nickname,status,is_active,is_superuser,password,login_channel FROM {user_table} WHERE email=%s",
                (email,),
            )
            rows = cursor.fetchall()
            user_id = rows[0][0] if len(rows) == 1 else known_user_id
            if user_id is None:
                return {
                    "user_count": len(rows),
                    "tenant_count": 0,
                    "owner_relation_count": 0,
                    "relation_count": 0,
                    "root_file_count": 0,
                    "file_count": 0,
                    "dataset_count": 0,
                    "dialog_count": 0,
                    "agent_count": 0,
                }
            cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (user_id,))
            tenant_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s OR tenant_id=%s",
                (user_id, user_id),
            )
            relation_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s AND tenant_id=%s AND role='owner'",
                (user_id, user_id),
            )
            owner_relation_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM file WHERE tenant_id=%s OR created_by=%s",
                (user_id, user_id),
            )
            file_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM file WHERE tenant_id=%s AND created_by=%s AND name='/' AND type='folder'",
                (user_id, user_id),
            )
            root_file_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM knowledgebase WHERE tenant_id=%s OR created_by=%s",
                (user_id, user_id),
            )
            dataset_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM dialog WHERE tenant_id=%s", (user_id,))
            dialog_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM user_canvas WHERE user_id=%s", (user_id,))
            agent_count = int(cursor.fetchone()[0])
    finally:
        connection.close()

    result: dict[str, Any] = {
        "user_count": len(rows),
        "user_id_fingerprint": BASE._fingerprint(user_id),
        "tenant_count": tenant_count,
        "owner_relation_count": owner_relation_count,
        "relation_count": relation_count,
        "root_file_count": root_file_count,
        "file_count": file_count,
        "dataset_count": dataset_count,
        "dialog_count": dialog_count,
        "agent_count": agent_count,
        "_user_id": str(user_id),
    }
    if len(rows) == 1:
        (
            _row_id,
            stored_email,
            nickname,
            status,
            is_active,
            is_superuser,
            credential_hash,
            login_channel,
        ) = rows[0]
        result.update(
            {
                "email": stored_email,
                "nickname": "" if nickname is None else str(nickname),
                "status": None if status is None else str(status),
                "is_active": None if is_active is None else str(is_active),
                "is_superuser": bool(is_superuser),
                "login_channel": login_channel,
                "credential_hash_fingerprint": BASE._fingerprint(str(credential_hash or "")),
            }
        )
    return result


def _admin_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    context: tuple[requests.Session, str] | None = None,
) -> dict[str, Any]:
    wire = prepare_admin_wire_payload(payload, lambda value: BASE._encrypt_password(group, value))

    def send(active_context):
        session, base = active_context
        return session.request(method.upper(), f"{base}{path}", json=wire, timeout=90)

    response, auth_attempts = authenticated_admin_request(
        lambda: BASE._open_admin_session(group),
        send,
        initial_context=context,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        label,
        {
            "summary": admin_request_summary(method, path, payload),
            "json": payload,
        },
        response,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message") if body else f"non-json response ({len(response.content)} bytes)",
        "data": body.get("data"),
        "raw_sha256": raw_sha,
        "auth_attempts": auth_attempts,
    }


def _response_step(name: str, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "http_status": response.get("http_status"),
        "code": response.get("code"),
        "message": response.get("message"),
        "raw_sha256": response.get("raw_sha256"),
        "auth_attempts": response.get("auth_attempts"),
    }


def _cleanup_user(case_id: str, group: str, email: str, label_prefix: str) -> dict[str, Any]:
    before = _database_snapshot(group, email)
    if before.get("user_count") == 0:
        return {
            "complete": True,
            "preexisting": False,
            "actions": [],
            "residual": _public_snapshot(before),
        }

    user_id = before.get("_user_id")
    context = BASE._open_admin_session(group)
    encoded = requests.utils.quote(email, safe="")
    responses: list[tuple[str, dict[str, Any]]] = []
    for action in cleanup_action_order(bool(before.get("is_superuser"))):
        if action == "revoke_admin":
            response = _admin_request(
                case_id,
                group,
                f"{label_prefix}_revoke_admin",
                "DELETE",
                f"/users/{encoded}/admin",
                context=context,
            )
        elif action == "deactivate":
            response = _admin_request(
                case_id,
                group,
                f"{label_prefix}_deactivate",
                "PUT",
                f"/users/{encoded}/activate",
                {"activate_status": "off"},
                context=context,
            )
        else:
            response = _admin_request(
                case_id,
                group,
                f"{label_prefix}_delete",
                "DELETE",
                f"/users/{encoded}",
                context=context,
            )
        responses.append((action, response))

    residual = _database_snapshot(group, email, known_user_id=user_id)
    delete_responses = [item for action, item in responses if action == "delete"]
    return {
        "complete": bool(delete_responses) and delete_responses[-1].get("code") == 0 and residual.get("user_count") == 0,
        "preexisting": True,
        "actions": [
            {
                "action": action,
                "http_status": response.get("http_status"),
                "code": response.get("code"),
                "raw_sha256": response.get("raw_sha256"),
            }
            for action, response in responses
        ],
        "residual": _public_snapshot(residual),
    }


def _create_fixture(
    case_id: str,
    group: str,
    *,
    role: str = "user",
    email: str | None = None,
    password: str | None = None,
    label: str = "setup_create",
) -> dict[str, Any]:
    target_email = email or _fixture_email(case_id, group)
    target_password = password or _fixture_password(case_id, group)
    preclean = _cleanup_user(case_id, group, target_email, "preclean")
    response = _admin_request(
        case_id,
        group,
        label,
        "POST",
        "/users",
        {
            "username": target_email,
            "password": target_password,
            "role": role,
        },
    )
    snapshot = _database_snapshot(group, target_email)
    return {
        "email": target_email,
        "password": target_password,
        "preclean": preclean,
        "response": response,
        "snapshot": snapshot,
    }


def _fixture_steps(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    response = fixture["response"]
    return [
        {
            "name": "ensure_fresh_fixture_via_public_admin_api",
            "complete": fixture["preclean"]["complete"],
            "preexisting": fixture["preclean"]["preexisting"],
            "actions": fixture["preclean"]["actions"],
        },
        _response_step("create_fixture_via_admin_api", response),
        {
            "name": "read_only_fixture_database_snapshot",
            "observed": _public_snapshot(fixture["snapshot"]),
        },
    ]


def _post_cleanup_step(cleanup: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "cleanup_fixture_via_public_admin_api",
        "complete": cleanup["complete"],
        "actions": cleanup["actions"],
        "residual": cleanup["residual"],
    }


def _main_login(case_id: str, group: str, label: str, email: str, password: str) -> dict[str, Any]:
    return BASE._login(
        case_id,
        group,
        label,
        {"email": email, "password": password},
    )


def _normal_token_admin_request(case_id: str, group: str, auth_value: str) -> dict[str, Any]:
    environment = _environment(group)
    path = "/users"
    url = f"http://127.0.0.1:{int(environment['ADMIN_PORT'])}/api/v1/admin{path}"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {auth_value}"},
        timeout=45,
    )
    raw_sha = BASE._record_http(
        case_id,
        group,
        "normal_user_admin_list",
        {"method": "GET", "path": path, "Authorization": auth_value},
        response,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "raw_sha256": raw_sha,
    }


def _outcome(
    ok: bool,
    steps: list[dict[str, Any]],
    oracle: dict[str, Any],
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "steps": steps,
        "oracle": oracle,
        "findings": findings or [],
    }


def _admin_001(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    response = fixture["response"]
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    observed = {
        "http_status": response.get("http_status"),
        "code": response.get("code"),
        "response_email_matches": data.get("email") == fixture["email"],
        "response_superuser": data.get("is_superuser"),
        "database": _public_snapshot(fixture["snapshot"]),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and admin_create_contract_ok(observed, expected_superuser=False) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "validate_admin_create_contract",
                "observed": observed,
            },
            _post_cleanup_step(cleanup),
        ],
        {
            "http_status": 200,
            "code": 0,
            "user_tenant_owner_root_counts": [1, 1, 1, 1],
            "is_superuser": False,
        },
    )


def _admin_002(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    duplicate = _admin_request(
        case_id,
        group,
        "duplicate_create",
        "POST",
        "/users",
        {
            "username": fixture["email"],
            "password": fixture["password"],
            "role": "user",
        },
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": duplicate["http_status"],
        "code": duplicate["code"],
        "message": duplicate["message"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and admin_rejection_contract_ok(observed, expected_code=409, message_fragment="already exists")
        and after.get("user_count") == 1
        and after.get("tenant_count") == 1
        and after.get("owner_relation_count") == 1
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("submit_duplicate_email", duplicate),
            {
                "name": "read_only_uniqueness_validation",
                "observed": _public_snapshot(after),
            },
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 400, "code": 409, "message_contains": "already exists"},
    )


def _admin_003(case_id: str, group: str) -> dict[str, Any]:
    invalid_email = f"not-an-email-{group}"
    response = _admin_request(
        case_id,
        group,
        "invalid_email_create",
        "POST",
        "/users",
        {
            "username": invalid_email,
            "password": _fixture_password(case_id, group),
        },
    )
    snapshot = _database_snapshot(group, invalid_email)
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "message": response["message"],
    }
    ok = admin_rejection_contract_ok(observed, expected_code=400, message_fragment="Invalid email") and snapshot.get("user_count") == 0
    return _outcome(
        ok,
        [
            _response_step("submit_invalid_email", response),
            {
                "name": "read_only_absence_validation",
                "observed": _public_snapshot(snapshot),
            },
        ],
        {"http_status": 400, "code": 400, "message_contains": "Invalid email"},
    )


def _admin_004(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group, role="admin")
    response = fixture["response"]
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    observed = {
        "http_status": response.get("http_status"),
        "code": response.get("code"),
        "response_email_matches": data.get("email") == fixture["email"],
        "response_superuser": data.get("is_superuser"),
        "database": _public_snapshot(fixture["snapshot"]),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and admin_create_contract_ok(observed, expected_superuser=True) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {"name": "validate_superuser_flag", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "is_superuser": True},
    )


def _admin_005(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "delete_active_user",
        "DELETE",
        f"/users/{encoded}",
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "message": response["message"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and admin_rejection_contract_ok(observed, message_fragment="is active and can't be deleted")
        and after.get("user_count") == 1
        and after.get("is_active") == "1"
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("reject_active_user_delete", response),
            {
                "name": "read_only_user_remains_active",
                "observed": _public_snapshot(after),
            },
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 400, "message_contains": "is active and can't be deleted"},
    )


def _admin_006(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    user_id = fixture["snapshot"].get("_user_id")
    context = BASE._open_admin_session(group)
    deactivate = _admin_request(
        case_id,
        group,
        "deactivate_before_delete",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "off"},
        context=context,
    )
    delete = _admin_request(
        case_id,
        group,
        "delete_inactive_user",
        "DELETE",
        f"/users/{encoded}",
        context=context,
    )
    after = _database_snapshot(group, fixture["email"], known_user_id=user_id)
    observed = {
        "http_status": delete["http_status"],
        "code": delete["code"],
        **_public_snapshot(after),
    }
    final_cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    findings = []
    if after.get("file_count", 0):
        findings.append(
            {
                "type": "product_cleanup_defect",
                "source": "api/db/joint_services/user_account_service.py::delete_user_data",
                "detail": "用户无 knowledgebase 时根目录文件记录未随用户删除。",
                "residual_file_count": after.get("file_count"),
            }
        )
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and deactivate["code"] == 0 and cascade_delete_contract_ok(observed) and final_cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("deactivate_user", deactivate),
            _response_step("delete_inactive_user", delete),
            {
                "name": "read_only_cascade_validation",
                "observed": _public_snapshot(after),
            },
            _post_cleanup_step(final_cleanup),
        ],
        {
            "http_status": 200,
            "code": 0,
            "all_user_owned_metadata_counts": 0,
        },
        findings,
    )


def _admin_007(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group, role="admin")
    encoded = requests.utils.quote(fixture["email"], safe="")
    context = BASE._open_admin_session(group)
    deactivate = _admin_request(
        case_id,
        group,
        "deactivate_superuser",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "off"},
        context=context,
    )
    rejection = _admin_request(
        case_id,
        group,
        "delete_superuser",
        "DELETE",
        f"/users/{encoded}",
        context=context,
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": rejection["http_status"],
        "code": rejection["code"],
        "message": rejection["message"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and deactivate["code"] == 0
        and admin_rejection_contract_ok(observed, message_fragment="Can't delete the super user")
        and after.get("user_count") == 1
        and after.get("is_active") == "0"
        and after.get("is_superuser") is True
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("deactivate_secondary_superuser", deactivate),
            _response_step("reject_superuser_delete", rejection),
            {
                "name": "read_only_superuser_remains",
                "observed": _public_snapshot(after),
            },
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 400, "message_contains": "Can't delete the super user"},
    )


def _admin_008(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    new_password = _fixture_password(case_id, group, "New")
    encoded = requests.utils.quote(fixture["email"], safe="")
    before = fixture["snapshot"]
    response = _admin_request(
        case_id,
        group,
        "admin_change_password",
        "PUT",
        f"/users/{encoded}/password",
        {"new_password": new_password},
    )
    after = _database_snapshot(group, fixture["email"])
    new_login = _main_login(case_id, group, "new_password_login", fixture["email"], new_password)
    old_login = _main_login(
        case_id,
        group,
        "old_password_login",
        fixture["email"],
        fixture["password"],
    )
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and response["http_status"] == 200
        and response["code"] == 0
        and before.get("credential_hash_fingerprint") != after.get("credential_hash_fingerprint")
        and new_login["code"] == 0
        and old_login["code"] == 109
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("change_password_without_old_password", response),
            {
                "name": "read_only_hash_and_login_validation",
                "hash_changed": before.get("credential_hash_fingerprint") != after.get("credential_hash_fingerprint"),
                "new_login_code": new_login["code"],
                "old_login_code": old_login["code"],
                "raw_sha256": [
                    new_login["raw_sha256"],
                    old_login["raw_sha256"],
                ],
            },
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "credential_hash_changed": True},
    )


def _admin_009(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    before = fixture["snapshot"]
    response = _admin_request(
        case_id,
        group,
        "same_password_update",
        "PUT",
        f"/users/{encoded}/password",
        {"new_password": fixture["password"]},
    )
    after = _database_snapshot(group, fixture["email"])
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and response["http_status"] == 200
        and response["code"] == 0
        and response["message"] == "Same password, no need to update!"
        and before.get("credential_hash_fingerprint") == after.get("credential_hash_fingerprint")
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("submit_same_password", response),
            {
                "name": "read_only_hash_unchanged",
                "unchanged": before.get("credential_hash_fingerprint") == after.get("credential_hash_fingerprint"),
            },
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "message": "Same password, no need to update!"},
    )


def _admin_010(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    context = BASE._open_admin_session(group)
    setup = _admin_request(
        case_id,
        group,
        "setup_inactive",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "off"},
        context=context,
    )
    response = _admin_request(
        case_id,
        group,
        "activate_user",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "on"},
        context=context,
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "database_value": after.get("is_active"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and setup["code"] == 0 and state_transition_contract_ok(observed, "1") and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("prepare_inactive_user", setup),
            _response_step("activate_user", response),
            {"name": "read_only_active_state", "observed": _public_snapshot(after)},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "is_active": "1"},
    )


def _admin_011(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "deactivate_user",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "off"},
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "database_value": after.get("is_active"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and state_transition_contract_ok(observed, "0") and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("deactivate_active_user", response),
            {"name": "read_only_active_state", "observed": _public_snapshot(after)},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "is_active": "0"},
    )


def _admin_012(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "invalid_activate_status",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "invalid"},
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "message": response["message"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and admin_rejection_contract_ok(observed, expected_code=400, message_fragment="Invalid activate_status")
        and after.get("is_active") == "1"
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("reject_invalid_activate_status", response),
            {
                "name": "read_only_state_unchanged",
                "observed": _public_snapshot(after),
            },
            _post_cleanup_step(cleanup),
        ],
        {
            "http_status": 400,
            "code": 400,
            "message_contains": "Invalid activate_status",
        },
    )


def _admin_013(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "grant_admin",
        "PUT",
        f"/users/{encoded}/admin",
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "database_value": after.get("is_superuser"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and role_transition_contract_ok(observed, True) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("grant_admin_role", response),
            {"name": "read_only_role_state", "observed": _public_snapshot(after)},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "is_superuser": True},
    )


def _admin_014(case_id: str, group: str) -> dict[str, Any]:
    current_email = str(_environment(group)["DEFAULT_SUPERUSER_EMAIL"])
    before = _database_snapshot(group, current_email)
    encoded = requests.utils.quote(current_email, safe="")
    response = _admin_request(
        case_id,
        group,
        "grant_current_admin",
        "PUT",
        f"/users/{encoded}/admin",
    )
    after = _database_snapshot(group, current_email)
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "message": response["message"],
    }
    ok = (
        before.get("user_count") == 1
        and before.get("is_superuser") is True
        and admin_rejection_contract_ok(observed, expected_code=409, message_fragment="can't grant current user")
        and after.get("is_superuser") is True
    )
    return _outcome(
        ok,
        [
            {
                "name": "read_only_current_admin_precondition",
                "observed": _public_snapshot(before),
            },
            _response_step("reject_self_grant", response),
            {
                "name": "read_only_current_admin_unchanged",
                "observed": _public_snapshot(after),
            },
        ],
        {"http_status": 400, "code": 409, "message_contains": "can't grant"},
    )


def _admin_015(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group, role="admin")
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "revoke_admin",
        "DELETE",
        f"/users/{encoded}/admin",
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "database_value": after.get("is_superuser"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and role_transition_contract_ok(observed, False) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("revoke_admin_role", response),
            {"name": "read_only_role_state", "observed": _public_snapshot(after)},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "is_superuser": False},
    )


def _admin_016(case_id: str, group: str) -> dict[str, Any]:
    current_email = str(_environment(group)["DEFAULT_SUPERUSER_EMAIL"])
    before = _database_snapshot(group, current_email)
    encoded = requests.utils.quote(current_email, safe="")
    response = _admin_request(
        case_id,
        group,
        "revoke_current_admin",
        "DELETE",
        f"/users/{encoded}/admin",
    )
    after = _database_snapshot(group, current_email)
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "message": response["message"],
    }
    wording_ok = admin_rejection_contract_ok(observed, expected_code=409, message_fragment="can't revoke current user")
    findings = []
    if response["http_status"] == 400 and response["code"] == 409 and "can't grant current user" in str(response["message"]).lower():
        findings.append(
            {
                "type": "admin_api_message_defect",
                "source": "admin/server/routes.py::revoke_admin",
                "detail": "revoke 自我操作分支错误复用了 grant 文案。",
            }
        )
    ok = before.get("user_count") == 1 and before.get("is_superuser") is True and wording_ok and after.get("is_superuser") is True
    return _outcome(
        ok,
        [
            {
                "name": "read_only_current_admin_precondition",
                "observed": _public_snapshot(before),
            },
            _response_step("reject_self_revoke", response),
            {
                "name": "read_only_current_admin_unchanged",
                "observed": _public_snapshot(after),
            },
        ],
        {"http_status": 400, "code": 409, "message_contains": "can't revoke"},
        findings,
    )


def _admin_017(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(case_id, group, "get_user_details", "GET", f"/users/{encoded}")
    rows = response.get("data") if isinstance(response.get("data"), list) else []
    fields = sorted(rows[0]) if len(rows) == 1 and isinstance(rows[0], dict) else []
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "row_count": len(rows),
        "fields": fields,
        "email_matches": len(rows) == 1 and rows[0].get("email") == fixture["email"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and details_contract_ok(observed) and observed["email_matches"] and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("get_user_details", response),
            {"name": "validate_detail_fields", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "required_fields": list(ADMIN_DETAIL_FIELDS)},
    )


def _admin_018(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "get_user_datasets",
        "GET",
        f"/users/{encoded}/datasets",
    )
    data = response.get("data") if isinstance(response.get("data"), list) else []
    snapshot = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "api_count": len(data),
        "database_count": snapshot.get("dataset_count"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and empty_collection_contract_ok(observed) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("get_user_datasets", response),
            {"name": "compare_api_and_database_dataset_counts", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "api_and_database_count": 0},
    )


def _admin_019(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    encoded = requests.utils.quote(fixture["email"], safe="")
    response = _admin_request(
        case_id,
        group,
        "get_user_agents",
        "GET",
        f"/users/{encoded}/agents",
    )
    data = response.get("data") if isinstance(response.get("data"), list) else []
    snapshot = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": response["http_status"],
        "code": response["code"],
        "api_count": len(data),
        "database_count": snapshot.get("agent_count"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and empty_collection_contract_ok(observed) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            _response_step("get_user_agents", response),
            {"name": "compare_api_and_database_agent_counts", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "api_and_database_count": 0},
    )


def _admin_020(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    login = _main_login(
        case_id,
        group,
        "normal_user_login",
        fixture["email"],
        fixture["password"],
    )
    response = _normal_token_admin_request(case_id, group, login.get("_auth_value"))
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    observed = {"http_status": response["http_status"]}
    findings = forbidden_failure_findings(observed)
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and login["code"] == 0 and forbidden_contract_ok(observed) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "login_normal_user",
                "http_status": login["http_status"],
                "code": login["code"],
                "auth_header_present": login["auth_header_present"],
                "raw_sha256": login["raw_sha256"],
            },
            _response_step("reject_normal_user_admin_access", response),
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 403},
        findings,
    )


def _set_001(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    before = fixture["snapshot"]
    login = _main_login(case_id, group, "settings_login", fixture["email"], fixture["password"])
    attempted_email = f"protected-change-{group}@fresh.invalid"
    patch = BASE._auth_request(
        case_id,
        group,
        "protected_fields_patch",
        "PATCH",
        "/users/me",
        login.get("_auth_value"),
        payload={
            "nickname": "NewName",
            "email": attempted_email,
            "status": "0",
            "is_superuser": True,
        },
    )
    after = _database_snapshot(group, fixture["email"])
    attempted = _database_snapshot(group, attempted_email)
    observed = {
        "http_status": patch["http_status"],
        "code": patch["code"],
        "nickname": after.get("nickname"),
        "email_unchanged": after.get("email") == before.get("email"),
        "status_unchanged": after.get("status") == before.get("status"),
        "superuser_unchanged": after.get("is_superuser") == before.get("is_superuser"),
        "active_unchanged": after.get("is_active") == before.get("is_active"),
        "attempted_email_absent": attempted.get("user_count") == 0,
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and login["code"] == 0
        and protected_settings_contract_ok(observed)
        and observed["active_unchanged"]
        and observed["attempted_email_absent"]
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "login_fixture",
                "http_status": login["http_status"],
                "code": login["code"],
                "raw_sha256": login["raw_sha256"],
            },
            _response_step("patch_profile_with_protected_fields", patch),
            {"name": "read_only_protected_field_validation", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"code": 0, "only_nickname_changes": True},
    )


def _set_002(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    new_password = _fixture_password(case_id, group, "New")
    before = fixture["snapshot"]
    login = _main_login(case_id, group, "settings_login", fixture["email"], fixture["password"])
    patch = BASE._auth_request(
        case_id,
        group,
        "change_password_with_old",
        "PATCH",
        "/users/me",
        login.get("_auth_value"),
        payload={
            "password": fixture["password"],
            "new_password": new_password,
        },
        encrypt_fields=("password", "new_password"),
    )
    after = _database_snapshot(group, fixture["email"])
    new_login = _main_login(case_id, group, "new_password_login", fixture["email"], new_password)
    old_login = _main_login(
        case_id,
        group,
        "old_password_login",
        fixture["email"],
        fixture["password"],
    )
    observed = {
        "http_status": patch["http_status"],
        "code": patch["code"],
        "hash_changed": before.get("credential_hash_fingerprint") != after.get("credential_hash_fingerprint"),
        "new_login_code": new_login["code"],
        "old_login_code": old_login["code"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and login["code"] == 0 and password_settings_contract_ok(observed) and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "login_fixture",
                "code": login["code"],
                "raw_sha256": login["raw_sha256"],
            },
            _response_step("change_password_with_correct_old_password", patch),
            {
                "name": "read_only_hash_and_login_validation",
                "observed": observed,
                "raw_sha256": [
                    new_login["raw_sha256"],
                    old_login["raw_sha256"],
                ],
            },
            _post_cleanup_step(cleanup),
        ],
        {"code": 0, "hash_changed": True, "new_login": 0, "old_login": 109},
    )


def _set_003(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    wrong_password = _fixture_password(case_id, group, "Wrong")
    proposed_password = _fixture_password(case_id, group, "Proposed")
    before = fixture["snapshot"]
    login = _main_login(case_id, group, "settings_login", fixture["email"], fixture["password"])
    patch = BASE._auth_request(
        case_id,
        group,
        "change_password_with_wrong_old",
        "PATCH",
        "/users/me",
        login.get("_auth_value"),
        payload={"password": wrong_password, "new_password": proposed_password},
        encrypt_fields=("password", "new_password"),
    )
    after = _database_snapshot(group, fixture["email"])
    current_login = _main_login(
        case_id,
        group,
        "current_password_login",
        fixture["email"],
        fixture["password"],
    )
    proposed_login = _main_login(
        case_id,
        group,
        "proposed_password_login",
        fixture["email"],
        proposed_password,
    )
    observed = {
        "http_status": patch["http_status"],
        "code": patch["code"],
        "message": patch["message"],
        "hash_unchanged": before.get("credential_hash_fingerprint") == after.get("credential_hash_fingerprint"),
        "current_login_code": current_login["code"],
        "proposed_login_code": proposed_login["code"],
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and login["code"] == 0
        and settings_rejection_contract_ok(observed, expected_code=109, message_fragment="Password error!")
        and observed["hash_unchanged"]
        and observed["current_login_code"] == 0
        and observed["proposed_login_code"] == 109
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "login_fixture",
                "code": login["code"],
                "raw_sha256": login["raw_sha256"],
            },
            _response_step("reject_wrong_old_password", patch),
            {
                "name": "read_only_hash_and_login_validation",
                "observed": observed,
                "raw_sha256": [
                    current_login["raw_sha256"],
                    proposed_login["raw_sha256"],
                ],
            },
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 109, "message": "Password error!"},
    )


def _set_004(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    login = _main_login(case_id, group, "settings_login", fixture["email"], fixture["password"])
    patch = BASE._auth_request(
        case_id,
        group,
        "spaced_nickname_patch",
        "PATCH",
        "/users/me",
        login.get("_auth_value"),
        payload={"nickname": "  Spaced Name  "},
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": patch["http_status"],
        "code": patch["code"],
        "stored_nickname": after.get("nickname"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = fixture["preclean"]["complete"] and fixture["response"]["code"] == 0 and login["code"] == 0 and nickname_settings_contract_ok(observed, "Spaced Name") and cleanup["complete"]
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "login_fixture",
                "code": login["code"],
                "raw_sha256": login["raw_sha256"],
            },
            _response_step("patch_spaced_nickname", patch),
            {"name": "read_only_trimmed_nickname", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 0, "stored_nickname": "Spaced Name"},
    )


def _set_005(case_id: str, group: str) -> dict[str, Any]:
    fixture = _create_fixture(case_id, group)
    login = _main_login(case_id, group, "settings_login", fixture["email"], fixture["password"])
    patch = BASE._auth_request(
        case_id,
        group,
        "invalid_nickname_patch",
        "PATCH",
        "/users/me",
        login.get("_auth_value"),
        payload={"nickname": "Invalid@Name#"},
    )
    after = _database_snapshot(group, fixture["email"])
    observed = {
        "http_status": patch["http_status"],
        "code": patch["code"],
        "message": patch["message"],
        "nickname_unchanged": after.get("nickname") == fixture["snapshot"].get("nickname"),
    }
    cleanup = _cleanup_user(case_id, group, fixture["email"], "post_cleanup")
    ok = (
        fixture["preclean"]["complete"]
        and fixture["response"]["code"] == 0
        and login["code"] == 0
        and settings_rejection_contract_ok(observed, expected_code=101, message_fragment="Nickname")
        and observed["nickname_unchanged"]
        and cleanup["complete"]
    )
    return _outcome(
        ok,
        [
            *_fixture_steps(fixture),
            {
                "name": "login_fixture",
                "code": login["code"],
                "raw_sha256": login["raw_sha256"],
            },
            _response_step("reject_invalid_nickname", patch),
            {"name": "read_only_nickname_unchanged", "observed": observed},
            _post_cleanup_step(cleanup),
        ],
        {"http_status": 200, "code": 101, "message_contains": "Nickname"},
    )


CASE_HANDLERS: dict[str, Callable[[str, str], dict[str, Any]]] = {
    "TC-UM-ADMIN-001": _admin_001,
    "TC-UM-ADMIN-002": _admin_002,
    "TC-UM-ADMIN-003": _admin_003,
    "TC-UM-ADMIN-004": _admin_004,
    "TC-UM-ADMIN-005": _admin_005,
    "TC-UM-ADMIN-006": _admin_006,
    "TC-UM-ADMIN-007": _admin_007,
    "TC-UM-ADMIN-008": _admin_008,
    "TC-UM-ADMIN-009": _admin_009,
    "TC-UM-ADMIN-010": _admin_010,
    "TC-UM-ADMIN-011": _admin_011,
    "TC-UM-ADMIN-012": _admin_012,
    "TC-UM-ADMIN-013": _admin_013,
    "TC-UM-ADMIN-014": _admin_014,
    "TC-UM-ADMIN-015": _admin_015,
    "TC-UM-ADMIN-016": _admin_016,
    "TC-UM-ADMIN-017": _admin_017,
    "TC-UM-ADMIN-018": _admin_018,
    "TC-UM-ADMIN-019": _admin_019,
    "TC-UM-ADMIN-020": _admin_020,
    "TC-UM-SET-001": _set_001,
    "TC-UM-SET-002": _set_002,
    "TC-UM-SET-003": _set_003,
    "TC-UM-SET-004": _set_004,
    "TC-UM-SET-005": _set_005,
}


def run_case(case_id: str) -> dict[str, Any]:
    recorder = EVIDENCE.CaseRecorder(case_id, CASE_TITLES[case_id])
    handler = CASE_HANDLERS[case_id]
    for group in GROUP_ORDER:
        result = handler(case_id, group)
        recorder.add_group(
            group,
            "PASS" if result["ok"] else "FAIL",
            result["steps"],
            oracle=result["oracle"],
            findings=result["findings"],
        )
    final = recorder.finalize()
    EVIDENCE.write_evidence(EVIDENCE_DIR / f"{case_id}.json", final)
    return final


def get_runners() -> dict[str, Callable[[], dict[str, Any]]]:
    return {case_id: partial(run_case, case_id) for case_id in CASE_TITLES}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run fresh Admin and Settings user management supplement cases")
    parser.add_argument("--case", choices=list(CASE_TITLES), required=True)
    args = parser.parse_args()
    result = get_runners()[args.case]()
    print(
        json.dumps(
            {
                "case_id": result["case_id"],
                "pair_status": result["pair_status"],
                "group_statuses": {item["group"]: item["status"] for item in result["groups"]},
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
