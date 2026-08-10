#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import requests
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    RUNTIME_DIR,
    evidence_dir,
)
from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_runner_result import pair_exit_code

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = evidence_dir("02_user_management")
RAW_DIR = EVIDENCE_DIR / "raw"
BASELINE_EMAIL = "um001@fresh.invalid"
BASELINE_PASSWORD = "UM001-Test@1234"
FLOW_EMAIL = "um026@fresh.invalid"
FLOW_ORIGINAL_PASSWORD = "UM026-Test@1234"
FLOW_NEW_PASSWORD = "UM026-New@5678"
CASCADE_EMAIL = "um044fresh@fresh.invalid"
CASE_TITLES = {
    "TC-UM-001": "正常用户注册 - 最小参数集",
    "TC-UM-002": "重复邮箱注册",
    "TC-UM-003": "缺少 email 字段注册",
    "TC-UM-004": "缺少 password 字段注册",
    "TC-UM-005": "缺少 nickname 字段注册",
    "TC-UM-006": "无效邮箱格式注册",
    "TC-UM-007": "空字符串密码注册",
    "TC-UM-008": "空字符串邮箱注册",
    "TC-UM-009": "空字符串 nickname 注册",
    "TC-UM-010": "纯空格 nickname 注册",
    "TC-UM-011": "nickname 包含非法字符注册",
    "TC-UM-012": "SQL 注入尝试注册",
    "TC-UM-013": "超长 nickname 注册",
    "TC-UM-014": "边界值 nickname 注册",
    "TC-UM-015": "中文 nickname 注册",
    "TC-UM-016": "正确邮箱密码登录",
    "TC-UM-017": "错误密码登录",
    "TC-UM-018": "未注册邮箱登录",
    "TC-UM-019": "空邮箱登录",
    "TC-UM-020": "空密码登录",
    "TC-UM-021": "邮箱大小写敏感性验证",
    "TC-UM-022": "Authorization Header 验证",
    "TC-UM-023": "多次登录会话管理",
    "TC-UM-024": "空请求体登录",
    "TC-UM-025": "缺失 password 字段登录",
    "TC-UM-026": "注册到登录完整流程",
    "TC-UM-027": "登录后获取个人资料",
    "TC-UM-028": "更新用户个人资料",
    "TC-UM-029": "受保护字段不可修改",
    "TC-UM-030": "修改密码流程",
    "TC-UM-031": "管理员停用用户",
    "TC-UM-032": "被停用用户登录失败",
    "TC-UM-033": "重新激活用户",
    "TC-UM-034": "设置 status=0",
    "TC-UM-035": "status 与 is_active 区别",
    "TC-UM-036": "恢复状态后完整验证",
    "TC-UM-037": "设置用户为 superuser",
    "TC-UM-038": "superuser 访问管理员功能",
    "TC-UM-039": "取消 superuser 权限",
    "TC-UM-040": "PATCH 尝试设置 is_superuser",
    "TC-UM-041": "PATCH 尝试修改 status",
    "TC-UM-042": "数据库软删除用户",
    "TC-UM-043": "软删除后登录与旧认证失败",
    "TC-UM-044": "完整删除用户数据",
    "TC-UM-045": "物理删除后邮箱可重新注册",
    "TC-UM-046": "级联删除数据一致性",
    "TC-UM-047": "创建删除再创建同邮箱",
    "TC-UM-048": "软删除后同邮箱不可注册",
    "TC-UM-049": "管理员获取所有用户列表",
    "TC-UM-050": "租户内用户列表",
    "TC-UM-051": "非 owner 访问租户用户列表",
    "TC-UM-052": "邀请用户加入租户",
    "TC-UM-053": "更新 nickname 字段",
    "TC-UM-054": "更新 language 字段",
    "TC-UM-055": "更新 color_schema 字段",
    "TC-UM-056": "avatar 空字符串字段契约",
    "TC-UM-057": "拒绝空 nickname",
    "TC-UM-058": "特殊字符 nickname 更新",
    "TC-UM-059": "非空 nickname 存储长度",
    "TC-UM-060": "五用户并发注册",
    "TC-UM-061": "错误旧密码修改密码",
    "TC-UM-062": "登出后旧 Token 失效",
}


def successful_registration_contract_ok(observed: dict[str, Any]) -> bool:
    database = observed.get("database", {})
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("auth_header_present") is True
        and observed.get("response_email_matches") is True
        and observed.get("response_nickname_matches") is True
        and database.get("user_count") == 1
        and database.get("status") == "1"
        and database.get("is_active") == "1"
        and database.get("is_superuser") is False
        and database.get("login_channel") == "password"
        and database.get("credential_hash_scheme") in {"scrypt", "pbkdf2"}
        and database.get("plaintext_credential_absent") is True
        and database.get("tenant_count") == 1
        and database.get("owner_relation_count") == 1
        and database.get("root_file_count") == 1
    )


def rejected_registration_contract_ok(observed: dict[str, Any], expected_codes: list[int]) -> bool:
    responses = observed.get("responses", [])
    return (
        len(responses) == len(expected_codes)
        and all(response.get("http_status") == 200 and response.get("code") == expected for response, expected in zip(responses, expected_codes, strict=True))
        and observed.get("database_row_count") == 0
    )


def registration_value_contract_ok(observed: dict[str, Any], expected_value: str, expected_length: int) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("auth_header_present") is True
        and observed.get("database_row_count") == 1
        and observed.get("stored_value") == expected_value
        and observed.get("stored_length") == expected_length
    )


def duplicate_registration_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 103 and observed.get("user_count") == 1 and observed.get("tenant_count") == 1 and observed.get("owner_relation_count") == 1


def gauss_readonly_options(schema: str) -> str:
    return f"-c client_encoding=UTF8 -c default_transaction_read_only=on -c search_path={schema}"


def set_gauss_search_path(connection, schema: str) -> None:
    from psycopg2 import sql as psycopg2_sql

    with connection.cursor() as cursor:
        cursor.execute(psycopg2_sql.SQL("SET search_path TO {}").format(psycopg2_sql.Identifier(schema)))


def successful_login_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("auth_header_present") is True
        and observed.get("response_email_matches") is True
        and observed.get("sensitive_fields_absent") is True
        and observed.get("last_login_changed") is True
        and observed.get("auth_state_changed") is True
    )


def rejected_login_contract_ok(observed: dict[str, Any], expected_code: int) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == expected_code and observed.get("last_login_unchanged") is True and observed.get("auth_state_unchanged") is True


def token_access_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("valid_http_status") == 200
        and observed.get("valid_code") == 0
        and observed.get("valid_email_matches") is True
        and observed.get("invalid_http_status") == 401
        and observed.get("invalid_code") == 401
    )


def full_user_flow_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("registration_code") == 0
        and observed.get("registration_profile_code") == 0
        and observed.get("logout_code") == 0
        and observed.get("invalid_prefix_after_logout") is True
        and observed.get("old_auth_http_status") == 401
        and observed.get("old_auth_code") == 401
        and observed.get("new_login_code") == 0
        and observed.get("new_profile_code") == 0
        and observed.get("new_auth_not_invalid") is True
        and observed.get("tenant_count") == 1
        and observed.get("owner_relation_count") == 1
    )


def profile_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("patch_code") == 0
        and observed.get("patch_data") is True
        and observed.get("profile_code") == 0
        and observed.get("profile_matches") is True
        and observed.get("database_matches") is True
    )


def protected_fields_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("patch_code") == 0
        and observed.get("email_unchanged") is True
        and observed.get("status_unchanged") is True
        and observed.get("superuser_unchanged") is True
        and observed.get("active_unchanged") is True
        and observed.get("nickname_changed") is True
    )


def password_change_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("patch_code") == 0 and observed.get("new_login_code") == 0 and observed.get("old_login_code") == 109 and observed.get("credential_hash_changed") is True


def status_transition_contract_ok(
    observed: dict[str, Any],
    *,
    expected_status: str,
    expected_active: str,
    expected_login_code: int,
) -> bool:
    return observed.get("admin_code") == 0 and observed.get("status") == expected_status and observed.get("is_active") == expected_active and observed.get("login_code") == expected_login_code


def role_transition_contract_ok(observed: dict[str, Any], *, expected: bool) -> bool:
    return observed.get("admin_code") == 0 and observed.get("database_superuser") is expected and observed.get("login_superuser") is expected


def idempotent_status_update_ok(rowcount: int, stored_status: str | None, expected_status: str) -> bool:
    return rowcount in {0, 1} and stored_status == expected_status


def cascade_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("admin_delete_code") == 0 and all(
        observed.get(key) == 0
        for key in (
            "user_count",
            "tenant_count",
            "relation_count",
            "file_count",
            "dataset_count",
        )
    )


def recreation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("first_create_code") == 0
        and observed.get("delete_code") == 0
        and observed.get("count_after_delete") == 0
        and observed.get("old_graph_absent") is True
        and observed.get("second_create_code") == 0
        and observed.get("ids_distinct") is True
        and observed.get("final_count") == 1
        and observed.get("final_nickname_matches") is True
    )


def physical_delete_precondition_ok(delete_code: int, remaining_count: int) -> bool:
    return delete_code == 0 and remaining_count == 0


def avatar_empty_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("patch_http_status") == 200
        and observed.get("patch_code") == 0
        and observed.get("patch_data") is True
        and observed.get("profile_code") == 0
        and observed.get("profile_avatar_present") is True
    )
    if group == "control":
        return common and observed.get("database_avatar") == "" and observed.get("database_is_null") is False and observed.get("profile_avatar") == ""
    if group == "experiment":
        return common and observed.get("database_avatar") is None and observed.get("database_is_null") is True and observed.get("profile_avatar") is None
    return False


def nickname_rejection_contract_ok(observed: dict[str, Any]) -> bool:
    responses = observed.get("responses")
    return (
        isinstance(responses, list)
        and len(responses) == 2
        and all(item.get("http_status") == 200 and item.get("code") == 101 and item.get("data") is False for item in responses)
        and observed.get("nickname_unchanged") is True
    )


def special_nickname_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("valid_patch_code") == 0 and observed.get("invalid_patch_code") == 101 and observed.get("database_value_matches") is True and observed.get("profile_value_matches") is True


def nonempty_nickname_storage_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("patch_code") == 0
        and observed.get("profile_code") == 0
        and observed.get("database_value") == "Space User"
        and observed.get("database_length") == 10
        and observed.get("database_octet_length") == 10
        and observed.get("profile_value") == "Space User"
    )


def concurrent_registration_contract_ok(observed: dict[str, Any]) -> bool:
    rows = observed.get("database_rows")
    return (
        observed.get("cleanup_ok") is True
        and observed.get("response_count") == 5
        and observed.get("all_response_codes_zero") is True
        and observed.get("all_auth_headers_present") is True
        and observed.get("database_email_set_matches") is True
        and isinstance(rows, list)
        and len(rows) == 5
        and all(row.get("status") == "1" and row.get("tenant_count") == 1 and row.get("owner_relation_count") == 1 for row in rows)
    )


def wrong_password_change_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("patch_http_status") == 200
        and observed.get("patch_code") == 109
        and observed.get("patch_data") is False
        and observed.get("credential_hash_unchanged") is True
        and observed.get("current_password_login_code") == 0
        and observed.get("proposed_password_login_code") == 109
    )


def logout_invalidation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("logout_http_status") == 200
        and observed.get("logout_code") == 0
        and observed.get("logout_data") is True
        and observed.get("auth_state_changed") is True
        and observed.get("invalid_prefix") is True
        and observed.get("old_auth_http_status") == 401
        and observed.get("old_auth_code") == 401
    )


def tenant_membership_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("invite_code") == 0
        and observed.get("list_code") == 0
        and observed.get("target_in_list") is True
        and observed.get("owner_in_list") is False
        and observed.get("database_role") == "invite"
        and observed.get("target_relation_count") == 2
    )


def runtime_signing_secret(environment: dict[str, Any], redis_secret: str | None) -> str | None:
    explicit = environment.get("RAGFLOW_SECRET_KEY")
    if explicit:
        return str(explicit)
    return redis_secret


def connect_with_retry(connect_fn, *, attempts: int = 3, delay_seconds: float = 0.25):
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(1, attempts + 1):
        try:
            return connect_fn()
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected mapping in {path}")
    return payload


def _load_evidence_module():
    path = EXECUTE_DIR / "fresh_case_evidence.py"
    spec = importlib.util.spec_from_file_location("fresh_case_evidence_um", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _encrypt_password(group: str, password: str) -> str:
    from Cryptodome.Cipher import PKCS1_v1_5
    from Cryptodome.PublicKey import RSA

    public_key = RUNTIME_DIR / group / "conf" / "public.pem"
    key = RSA.import_key(public_key.read_text(encoding="utf-8"), "Welcome")
    encoded = base64.b64encode(password.encode("utf-8"))
    return base64.b64encode(PKCS1_v1_5.new(key).encrypt(encoded)).decode("ascii")


def _api_base(group: str) -> str:
    config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")
    return f"http://127.0.0.1:{int(config['ragflow']['http_port'])}/api/v1"


def _record_http(
    case_id: str,
    group: str,
    label: str,
    request_payload: dict[str, Any],
    response: requests.Response,
) -> str:
    evidence = _load_evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    try:
        body = response.json()
    except ValueError:
        body = {"non_json_response_length": len(response.content)}
    transcript = evidence.sanitize(
        {
            "request": request_payload,
            "response": {
                "http_status": response.status_code,
                "Authorization": response.headers.get("Authorization"),
                "body": body,
            },
        }
    )
    evidence.write_evidence(path, transcript)
    return _sha256(path)


def _register(
    case_id: str,
    group: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    wire_payload = dict(payload)
    if "password" in wire_payload:
        wire_payload["password"] = _encrypt_password(group, str(wire_payload["password"]))
    response = requests.post(f"{_api_base(group)}/users", json=wire_payload, timeout=45)
    raw_sha = _record_http(case_id, group, label, payload, response)
    body = response.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    auth_value = response.headers.get("Authorization")
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "auth_header_present": bool(auth_value),
        "auth_fingerprint": _fingerprint(auth_value) if auth_value else None,
        "response_data": data,
        "raw_sha256": raw_sha,
        "_auth_value": auth_value,
    }


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def _login(
    case_id: str,
    group: str,
    label: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if payload is None:
        request_summary = {"body_present": False}
        response = requests.post(
            f"{_api_base(group)}/auth/login",
            data=b"",
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    else:
        request_summary = payload
        wire_payload = dict(payload)
        if "password" in wire_payload:
            wire_payload["password"] = _encrypt_password(group, str(wire_payload["password"]))
        response = requests.post(f"{_api_base(group)}/auth/login", json=wire_payload, timeout=30)
    raw_sha = _record_http(case_id, group, label, request_summary, response)
    body = response.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    auth_value = response.headers.get("Authorization")
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "auth_header_present": bool(auth_value),
        "auth_fingerprint": _fingerprint(auth_value) if auth_value else None,
        "response_data": data,
        "sensitive_fields_absent": not {"password", "access_token"}.intersection(data),
        "raw_sha256": raw_sha,
        "_auth_value": auth_value,
    }


def _auth_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    auth_value: str,
    *,
    payload: dict[str, Any] | None = None,
    encrypt_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {auth_value}"}
    wire_payload = dict(payload) if payload is not None else None
    if wire_payload is not None:
        for field in encrypt_fields:
            if field in wire_payload:
                wire_payload[field] = _encrypt_password(group, str(wire_payload[field]))
    response = requests.request(
        method,
        f"{_api_base(group)}{path}",
        headers=headers,
        json=wire_payload,
        timeout=45,
    )
    raw_sha = _record_http(
        case_id,
        group,
        label,
        {"method": method, "path": path, "Authorization": auth_value, "json": payload},
        response,
    )
    body = response.json()
    data = body.get("data")
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": data,
        "raw_sha256": raw_sha,
    }


def _open_database(group: str):
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            read_timeout=10,
        )
        return connection, "`user`", config["name"]

    import psycopg2

    environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]
    schema = environment["GAUSSDB_METADATA_SCHEMA"]
    connection = connect_with_retry(
        lambda: psycopg2.connect(
            host=environment["GAUSSDB_METADATA_HOST"],
            port=int(environment["GAUSSDB_METADATA_PORT"]),
            dbname=environment["GAUSSDB_METADATA_DBNAME"],
            user=environment["GAUSSDB_METADATA_USER"],
            password=environment["GAUSSDB_METADATA_PASSWORD"],
            connect_timeout=5,
            options=gauss_readonly_options(schema),
        )
    )
    set_gauss_search_path(connection, schema)
    return connection, '"user"', schema


def _registration_snapshot(group: str, email: str, *, plaintext_credential: str | None = None) -> dict[str, Any]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,email,nickname,status,is_active,is_superuser,login_channel,password FROM {user_table} WHERE email=%s",
                (email,),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                return {"user_count": len(rows)}
            user_id, stored_email, nickname, status, is_active, is_superuser, login_channel, credential_hash = rows[0]
            cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (user_id,))
            tenant_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s AND tenant_id=%s AND role='owner'",
                (user_id, user_id),
            )
            owner_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM file WHERE tenant_id=%s AND created_by=%s AND name='/' AND type='folder'",
                (user_id, user_id),
            )
            root_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    scheme = str(credential_hash).split(":", 1)[0].split("$", 1)[0]
    return {
        "user_count": 1,
        "stored_email": stored_email,
        "stored_nickname": nickname,
        "stored_nickname_length": len(nickname),
        "status": status,
        "is_active": is_active,
        "is_superuser": bool(is_superuser),
        "login_channel": login_channel,
        "credential_hash_scheme": scheme,
        "plaintext_credential_absent": (plaintext_credential is None or plaintext_credential not in str(credential_hash)),
        "tenant_count": tenant_count,
        "owner_relation_count": owner_count,
        "root_file_count": root_count,
    }


def _auth_snapshot(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,last_login_time,access_token FROM {user_table} WHERE email=%s",
                (email,),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                return {"user_count": len(rows)}
            user_id, last_login_time, auth_state = rows[0]
            cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (user_id,))
            tenant_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s AND tenant_id=%s AND role='owner'",
                (user_id, user_id),
            )
            owner_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    auth_text = str(auth_state or "")
    return {
        "user_count": 1,
        "last_login_time": str(last_login_time) if last_login_time is not None else None,
        "auth_state_fingerprint": _fingerprint(auth_text),
        "auth_state_length": len(auth_text),
        "invalid_prefix": auth_text.startswith("INVALID_"),
        "tenant_count": tenant_count,
        "owner_relation_count": owner_count,
    }


def _user_state_snapshot(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT nickname,email,language,color_schema,status,is_active,is_superuser,password FROM {user_table} WHERE email=%s",
                (email,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        return {"user_count": len(rows)}
    nickname, stored_email, language, color_schema, status, is_active, is_superuser, credential_hash = rows[0]
    credential_text = str(credential_hash or "")
    return {
        "user_count": 1,
        "nickname": nickname,
        "email": stored_email,
        "language": language,
        "color_schema": color_schema,
        "status": status,
        "is_active": is_active,
        "is_superuser": bool(is_superuser),
        "credential_hash_scheme": credential_text.split(":", 1)[0].split("$", 1)[0],
        "credential_hash_fingerprint": _fingerprint(credential_text),
    }


def _ensure_flow_user(group: str) -> dict[str, Any]:
    count = _email_count(group, [FLOW_EMAIL])
    if count == 1:
        return {"created": False, "existing_count": 1}
    if count != 0:
        raise RuntimeError("flow user email is not unique")
    response = _register(
        "TC-UM-027",
        group,
        "flow_precondition",
        {
            "email": FLOW_EMAIL,
            "nickname": "UM026Flow",
            "password": FLOW_ORIGINAL_PASSWORD,
        },
    )
    if response["code"] != 0:
        raise RuntimeError("failed to create flow user")
    return {"created": True, "existing_count": 0}


def _admin_action(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session, base = _open_admin_session(group)
    response = session.request(method, f"{base}{path}", json=payload, timeout=90)
    raw_sha = _record_http(
        case_id,
        group,
        label,
        {"method": method, "path": path, "json": payload},
        response,
    )
    body = response.json()
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "raw_sha256": raw_sha,
    }


def _set_user_status_direct(group: str, email: str, status: str) -> bool:
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            autocommit=True,
        )
        user_table = "`user`"
    else:
        import psycopg2

        environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]
        schema = environment["GAUSSDB_METADATA_SCHEMA"]
        connection = psycopg2.connect(
            host=environment["GAUSSDB_METADATA_HOST"],
            port=int(environment["GAUSSDB_METADATA_PORT"]),
            dbname=environment["GAUSSDB_METADATA_DBNAME"],
            user=environment["GAUSSDB_METADATA_USER"],
            password=environment["GAUSSDB_METADATA_PASSWORD"],
            connect_timeout=5,
            options=(f"-c client_encoding=UTF8 -c default_transaction_read_only=off -c search_path={schema}"),
        )
        connection.autocommit = True
        set_gauss_search_path(connection, schema)
        user_table = '"user"'
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {user_table} SET status=%s WHERE email=%s",
                (status, email),
            )
            changed = cursor.rowcount
            cursor.execute(f"SELECT status FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
    finally:
        connection.close()
    return idempotent_status_update_ok(int(changed), str(row[0]) if row is not None else None, status)


def _raw_auth_state(group: str, email: str) -> str:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT access_token FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None or not row[0]:
        raise RuntimeError("user auth state is unavailable")
    return str(row[0])


def _signed_current_auth(group: str, email: str) -> str:
    import redis
    from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer

    config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")["redis"]
    host, port = str(config["host"]).rsplit(":", 1)
    client = redis.Redis(
        host=host,
        port=int(port),
        db=int(config["db"]),
        username=config.get("username") or None,
        password=config.get("password") or None,
        socket_connect_timeout=5,
    )
    redis_value = client.get("ragflow:system:secret_key")
    redis_secret = redis_value.decode("utf-8") if redis_value else None
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    secret = runtime_signing_secret(environments[group], redis_secret)
    if not secret:
        raise RuntimeError("runtime signing key is unavailable")
    return Serializer(secret_key=secret).dumps(_raw_auth_state(group, email))


def _cascade_snapshot(
    group: str,
    email: str,
    user_id: str,
    tenant_id: str,
    dataset_id: str | None,
) -> dict[str, int]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {user_table} WHERE email=%s", (email,))
            user_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (tenant_id,))
            tenant_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM user_tenant WHERE user_id=%s OR tenant_id=%s",
                (user_id, tenant_id),
            )
            relation_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT COUNT(*) FROM file WHERE tenant_id=%s OR created_by=%s",
                (tenant_id, user_id),
            )
            file_count = int(cursor.fetchone()[0])
            if dataset_id:
                cursor.execute(
                    "SELECT COUNT(*) FROM knowledgebase WHERE id=%s OR tenant_id=%s",
                    (dataset_id, tenant_id),
                )
                dataset_count = int(cursor.fetchone()[0])
            else:
                dataset_count = 0
    finally:
        connection.close()
    return {
        "user_count": user_count,
        "tenant_count": tenant_count,
        "relation_count": relation_count,
        "file_count": file_count,
        "dataset_count": dataset_count,
    }


def _orphan_snapshot(group: str) -> dict[str, int]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM user_tenant ut LEFT JOIN {user_table} u ON ut.user_id=u.id WHERE u.id IS NULL")
            missing_user = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM user_tenant ut LEFT JOIN tenant t ON ut.tenant_id=t.id WHERE t.id IS NULL")
            missing_tenant = int(cursor.fetchone()[0])
            cursor.execute(f"SELECT COUNT(*) FROM file f WHERE NOT EXISTS (SELECT 1 FROM tenant t WHERE t.id=f.tenant_id) OR NOT EXISTS (SELECT 1 FROM {user_table} u WHERE u.id=f.created_by)")
            orphan_file = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "user_tenant_missing_user": missing_user,
        "user_tenant_missing_tenant": missing_tenant,
        "file_missing_owner": orphan_file,
    }


def _user_list_snapshot(group: str) -> list[dict[str, Any]]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT email,nickname,create_date,is_active,is_superuser FROM {user_table} ORDER BY email")
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "email": row[0],
            "nickname": row[1],
            "create_date": str(row[2]),
            "is_active": row[3],
            "is_superuser": bool(row[4]),
        }
        for row in rows
    ]


def _membership_snapshot(group: str, email: str, tenant_id: str) -> dict[str, Any]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT id FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
            if row is None:
                return {"user_count": 0}
            user_id = str(row[0])
            cursor.execute(
                "SELECT role FROM user_tenant WHERE user_id=%s AND tenant_id=%s",
                (user_id, tenant_id),
            )
            membership = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM user_tenant WHERE user_id=%s", (user_id,))
            relation_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "user_count": 1,
        "user_id": user_id,
        "role": membership[0] if membership else None,
        "relation_count": relation_count,
    }


def _profile_storage_snapshot(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT nickname,language,color_schema,avatar,avatar IS NULL,LENGTH(nickname),OCTET_LENGTH(nickname) FROM {user_table} WHERE email=%s",
                (email,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        return {"user_count": len(rows)}
    nickname, language, color_schema, avatar, avatar_is_null, nickname_length, nickname_octet_length = rows[0]
    return {
        "user_count": 1,
        "nickname": nickname,
        "language": language,
        "color_schema": color_schema,
        "avatar": avatar,
        "avatar_is_null": bool(avatar_is_null),
        "nickname_length": int(nickname_length),
        "nickname_octet_length": int(nickname_octet_length),
    }


def _concurrent_registration_snapshot(group: str, emails: list[str]) -> list[dict[str, Any]]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(emails))
            cursor.execute(
                f"SELECT u.email,u.nickname,u.status,"
                f"(SELECT COUNT(*) FROM tenant t WHERE t.id=u.id),"
                f"(SELECT COUNT(*) FROM user_tenant ut WHERE ut.user_id=u.id "
                f"AND ut.tenant_id=u.id AND ut.role='owner') "
                f"FROM {user_table} u WHERE u.email IN ({placeholders}) "
                "ORDER BY u.email",
                tuple(emails),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "email": row[0],
            "nickname": row[1],
            "status": row[2],
            "tenant_count": int(row[3]),
            "owner_relation_count": int(row[4]),
        }
        for row in rows
    ]


def _email_collation_snapshot(group: str, email: str) -> dict[str, Any]:
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute("SELECT @@collation_database")
            else:
                cursor.execute("SELECT datcollate FROM pg_database WHERE datname=current_database()")
            collation = str(cursor.fetchone()[0])
            cursor.execute(
                f"SELECT COUNT(*) FROM {user_table} WHERE LOWER(email)=LOWER(%s)",
                (email,),
            )
            lower_match_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {"database_collation": collation, "lower_match_count": lower_match_count}


def _email_count(group: str, emails: list[str]) -> int:
    if not emails:
        return 0
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(emails))
            cursor.execute(
                f"SELECT COUNT(*) FROM {user_table} WHERE email IN ({placeholders})",
                tuple(emails),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _nickname_count(group: str, nicknames: list[str]) -> int:
    if not nicknames:
        return 0
    connection, user_table, _namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            placeholders = ",".join(["%s"] * len(nicknames))
            cursor.execute(
                f"SELECT COUNT(*) FROM {user_table} WHERE nickname IN ({placeholders})",
                tuple(nicknames),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _user_table_exists(group: str) -> bool:
    connection, _user_table, namespace = _open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name='user'",
                (namespace,),
            )
            return int(cursor.fetchone()[0]) == 1
    finally:
        connection.close()


def _open_admin_session(group: str) -> tuple[requests.Session, str]:
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = environments[group]
    session = requests.Session()
    base = f"http://127.0.0.1:{int(environment['ADMIN_PORT'])}/api/v1/admin"
    response = session.post(
        f"{base}/login",
        json={
            "email": environment["DEFAULT_SUPERUSER_EMAIL"],
            "password": _encrypt_password(group, environment["DEFAULT_SUPERUSER_PASSWORD"]),
        },
        timeout=30,
    )
    body = response.json()
    auth = response.headers.get("Authorization")
    if response.status_code != 200 or body.get("code") != 0 or not auth:
        raise RuntimeError("Admin login failed")
    session.headers["Authorization"] = f"Bearer {auth}"
    return session, base


def _delete_user_via_admin(group: str, email: str) -> bool:
    if _email_count(group, [email]) == 0:
        return True
    session, base = _open_admin_session(group)
    encoded = requests.utils.quote(email, safe="")
    disabled = session.put(
        f"{base}/users/{encoded}/activate",
        json={"activate_status": "off"},
        timeout=30,
    )
    deleted = session.delete(f"{base}/users/{encoded}", timeout=90)
    return disabled.json().get("code") == 0 and deleted.json().get("code") == 0 and _email_count(group, [email]) == 0


def _ensure_baseline_user(group: str) -> dict[str, Any]:
    count = _email_count(group, [BASELINE_EMAIL])
    if count == 1:
        return {"created": False, "existing_count": 1}
    if count != 0:
        raise RuntimeError("baseline email is not unique")
    response = _register(
        "TC-UM-002",
        group,
        "baseline_precondition",
        {
            "email": BASELINE_EMAIL,
            "nickname": "UM001User",
            "password": BASELINE_PASSWORD,
        },
    )
    if response["code"] != 0:
        raise RuntimeError("failed to create baseline user")
    return {"created": True, "existing_count": 0}


def _finalize(recorder, case_id: str) -> dict[str, Any]:
    evidence = _load_evidence_module()
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def run_um001() -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder("TC-UM-001", CASE_TITLES["TC-UM-001"])
    for group in GROUP_ORDER:
        preexisting = _email_count(group, [BASELINE_EMAIL])
        cleanup = True
        if preexisting:
            cleanup = _delete_user_via_admin(group, BASELINE_EMAIL)
        response = _register(
            "TC-UM-001",
            group,
            "register",
            {
                "email": BASELINE_EMAIL,
                "nickname": "UM001User",
                "password": BASELINE_PASSWORD,
            },
        )
        snapshot = _registration_snapshot(group, BASELINE_EMAIL, plaintext_credential=BASELINE_PASSWORD)
        observed = {
            **{k: response[k] for k in ("http_status", "code", "auth_header_present")},
            "response_email_matches": response["response_data"].get("email") == BASELINE_EMAIL,
            "response_nickname_matches": response["response_data"].get("nickname") == "UM001User",
            "database": snapshot,
        }
        ok = cleanup and successful_registration_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "ensure_fixture_absent_via_admin_api_if_needed",
                    "preexisting_count": preexisting,
                    "complete": cleanup,
                },
                {
                    "name": "register_via_main_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "auth_header_present": response["auth_header_present"],
                    "response_email_matches": observed["response_email_matches"],
                    "response_nickname_matches": observed["response_nickname_matches"],
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "read_only_database_validation", "observed": snapshot},
            ],
            oracle={
                "http_status": 200,
                "code": 0,
                "user_tenant_owner_root_file_counts": [1, 1, 1, 1],
                "credential_hash_scheme": ["scrypt", "pbkdf2"],
            },
        )
    return _finalize(recorder, "TC-UM-001")


def run_um002() -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder("TC-UM-002", CASE_TITLES["TC-UM-002"])
    for group in GROUP_ORDER:
        precondition = _ensure_baseline_user(group)
        response = _register(
            "TC-UM-002",
            group,
            "duplicate",
            {
                "email": BASELINE_EMAIL,
                "nickname": "UM002Duplicate",
                "password": BASELINE_PASSWORD,
            },
        )
        snapshot = _registration_snapshot(group, BASELINE_EMAIL)
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "user_count": snapshot.get("user_count"),
            "tenant_count": snapshot.get("tenant_count"),
            "owner_relation_count": snapshot.get("owner_relation_count"),
        }
        ok = duplicate_registration_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "confirm_baseline_user", "observed": precondition},
                {
                    "name": "submit_duplicate_registration",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_duplicate_count",
                    "user_count": snapshot.get("user_count"),
                    "tenant_count": snapshot.get("tenant_count"),
                    "owner_relation_count": snapshot.get("owner_relation_count"),
                },
            ],
            oracle={"code": 103, "user_count": 1},
        )
    return _finalize(recorder, "TC-UM-002")


REJECTION_CASES: dict[str, dict[str, Any]] = {
    "TC-UM-003": {
        "payloads": [{"nickname": "UM003MissingEmail", "password": "UM-Test@1234"}],
        "codes": [101],
        "count_by": "nickname",
    },
    "TC-UM-004": {
        "payloads": [{"email": "um004@fresh.invalid", "nickname": "UM004MissingPassword"}],
        "codes": [101],
        "count_by": "email",
    },
    "TC-UM-005": {
        "payloads": [{"email": "um005@fresh.invalid", "password": "UM-Test@1234"}],
        "codes": [101],
        "count_by": "email",
    },
    "TC-UM-006": {
        "payloads": [
            {
                "email": "invalid-email",
                "nickname": "UM006a",
                "password": "UM-Test@1234",
            },
            {
                "email": "user@",
                "nickname": "UM006b",
                "password": "UM-Test@1234",
            },
            {
                "email": "@example.com",
                "nickname": "UM006c",
                "password": "UM-Test@1234",
            },
            {
                "email": "user@example",
                "nickname": "UM006d",
                "password": "UM-Test@1234",
            },
        ],
        "codes": [103, 103, 103, 103],
        "count_by": "email",
    },
    "TC-UM-008": {
        "payloads": [{"email": "", "nickname": "UM008", "password": "UM-Test@1234"}],
        "codes": [103],
        "count_by": "email",
    },
    "TC-UM-009": {
        "payloads": [
            {
                "email": "um009@fresh.invalid",
                "nickname": "",
                "password": "UM-Test@1234",
            }
        ],
        "codes": [101],
        "count_by": "email",
    },
    "TC-UM-010": {
        "payloads": [
            {
                "email": "um010@fresh.invalid",
                "nickname": "   ",
                "password": "UM-Test@1234",
            }
        ],
        "codes": [101],
        "count_by": "email",
    },
    "TC-UM-011": {
        "payloads": [
            {
                "email": "um011a@fresh.invalid",
                "nickname": "User@#$%",
                "password": "UM-Test@1234",
            },
            {
                "email": "um011b@fresh.invalid",
                "nickname": "<script>alert(1)</script>",
                "password": "UM-Test@1234",
            },
            {
                "email": "um011c@fresh.invalid",
                "nickname": "User\nName",
                "password": "UM-Test@1234",
            },
        ],
        "codes": [101, 101, 101],
        "count_by": "email",
    },
    "TC-UM-013": {
        "payloads": [
            {
                "email": "um013@fresh.invalid",
                "nickname": "A" * 101,
                "password": "UM-Test@1234",
            }
        ],
        "codes": [101],
        "count_by": "email",
    },
}


def run_rejection_case(case_id: str) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    definition = REJECTION_CASES[case_id]
    for group in GROUP_ORDER:
        responses = [_register(case_id, group, f"request_{index}", payload) for index, payload in enumerate(definition["payloads"], start=1)]
        if definition["count_by"] == "email":
            values = [payload.get("email") for payload in definition["payloads"]]
            row_count = _email_count(group, [str(value) for value in values])
        else:
            values = [payload.get("nickname") for payload in definition["payloads"]]
            row_count = _nickname_count(group, [str(value) for value in values])
        observed = {"responses": responses, "database_row_count": row_count}
        ok = rejected_registration_contract_ok(observed, definition["codes"])
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "submit_registration_variants",
                    "responses": [
                        {
                            "http_status": response["http_status"],
                            "code": response["code"],
                            "raw_sha256": response["raw_sha256"],
                        }
                        for response in responses
                    ],
                },
                {
                    "name": "read_only_database_validation",
                    "count_by": definition["count_by"],
                    "database_row_count": row_count,
                },
            ],
            oracle={
                "expected_codes": definition["codes"],
                "database_row_count": 0,
            },
        )
    return _finalize(recorder, case_id)


def run_um007() -> dict[str, Any]:
    case_id = "TC-UM-007"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    email = "um007@fresh.invalid"
    for group in GROUP_ORDER:
        cleanup = _delete_user_via_admin(group, email)
        response = _register(
            case_id,
            group,
            "empty_password",
            {"email": email, "nickname": "UM007", "password": ""},
        )
        snapshot = _registration_snapshot(group, email)
        observed = {
            **{k: response[k] for k in ("http_status", "code", "auth_header_present")},
            "response_email_matches": response["response_data"].get("email") == email,
            "response_nickname_matches": response["response_data"].get("nickname") == "UM007",
            "database": snapshot,
        }
        ok = cleanup and successful_registration_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "register_rsa_encrypted_empty_password",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "auth_header_present": response["auth_header_present"],
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "read_only_database_validation", "observed": snapshot},
            ],
            oracle={"current_backend_minimum_password_length": None, "code": 0},
        )
    return _finalize(recorder, case_id)


def run_um012() -> dict[str, Any]:
    case_id = "TC-UM-012"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    payloads = [
        {
            "email": "test'; DROP TABLE user; --@example.com",
            "nickname": "SQLInjectEmail",
            "password": "UM-Test@1234",
        },
        {
            "email": "um012@fresh.invalid",
            "nickname": "Robert'; DROP TABLE students;--",
            "password": "UM-Test@1234",
        },
    ]
    for group in GROUP_ORDER:
        responses = [_register(case_id, group, f"injection_{index}", payload) for index, payload in enumerate(payloads, start=1)]
        row_count = _email_count(group, [payload["email"] for payload in payloads])
        table_exists = _user_table_exists(group)
        observed = {"responses": responses, "database_row_count": row_count}
        ok = rejected_registration_contract_ok(observed, [103, 101]) and table_exists
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "submit_email_and_nickname_injection_payloads",
                    "responses": [
                        {
                            "http_status": response["http_status"],
                            "code": response["code"],
                            "raw_sha256": response["raw_sha256"],
                        }
                        for response in responses
                    ],
                },
                {
                    "name": "read_only_integrity_validation",
                    "injection_row_count": row_count,
                    "user_table_exists": table_exists,
                },
            ],
            oracle={"codes": [103, 101], "row_count": 0, "user_table_exists": True},
        )
    return _finalize(recorder, case_id)


def run_value_success_case(case_id: str, email: str, nickname: str) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        cleanup = _delete_user_via_admin(group, email)
        response = _register(
            case_id,
            group,
            "register",
            {
                "email": email,
                "nickname": nickname,
                "password": "UM-Test@1234",
            },
        )
        snapshot = _registration_snapshot(group, email)
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "auth_header_present": response["auth_header_present"],
            "database_row_count": snapshot.get("user_count"),
            "stored_value": snapshot.get("stored_nickname"),
            "stored_length": snapshot.get("stored_nickname_length"),
        }
        ok = cleanup and registration_value_contract_ok(observed, nickname, len(nickname))
        findings = []
        if case_id == "TC-UM-014" and group == "experiment" and not ok:
            findings.append(
                {
                    "id": "UM-TENANT-NAME-LENGTH-001",
                    "summary": ("a valid 100-character nickname makes the derived tenant name exceed tenant.name VARCHAR(100), so GaussDB rejects registration"),
                    "api_code": response["code"],
                    "api_message": response["message"],
                }
            )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "register_boundary_value_via_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "auth_header_present": response["auth_header_present"],
                    "raw_sha256": response["raw_sha256"],
                    "message": response["message"],
                },
                {
                    "name": "read_only_value_validation",
                    "database_row_count": snapshot.get("user_count"),
                    "stored_value": snapshot.get("stored_nickname"),
                    "stored_length": snapshot.get("stored_nickname_length"),
                },
            ],
            oracle={"stored_value": nickname, "stored_length": len(nickname)},
            findings=findings,
        )
    return _finalize(recorder, case_id)


def run_um016() -> dict[str, Any]:
    case_id = "TC-UM-016"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        precondition = _ensure_baseline_user(group)
        before = _auth_snapshot(group, BASELINE_EMAIL)
        time.sleep(1.05)
        response = _login(
            case_id,
            group,
            "correct_login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        after = _auth_snapshot(group, BASELINE_EMAIL)
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "auth_header_present": response["auth_header_present"],
            "response_email_matches": response["response_data"].get("email") == BASELINE_EMAIL,
            "sensitive_fields_absent": response["sensitive_fields_absent"],
            "last_login_changed": before.get("last_login_time") != after.get("last_login_time"),
            "auth_state_changed": before.get("auth_state_fingerprint") != after.get("auth_state_fingerprint"),
        }
        ok = successful_login_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "confirm_baseline_user", "observed": precondition},
                {
                    "name": "login_with_correct_rsa_encrypted_password",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "auth_header_present": response["auth_header_present"],
                    "response_email_matches": observed["response_email_matches"],
                    "sensitive_fields_absent": observed["sensitive_fields_absent"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_auth_state_comparison",
                    "before": before,
                    "after": after,
                    "last_login_changed": observed["last_login_changed"],
                    "auth_state_changed": observed["auth_state_changed"],
                },
            ],
            oracle={"code": 0, "last_login_rotated": True, "auth_state_rotated": True},
        )
    return _finalize(recorder, case_id)


LOGIN_REJECTION_CASES = {
    "TC-UM-017": {
        "payload": {"email": BASELINE_EMAIL, "password": "WrongPassword"},
        "code": 109,
        "tracked_email": BASELINE_EMAIL,
        "needs_baseline": True,
    },
    "TC-UM-018": {
        "payload": {"email": "nonexistent@fresh.invalid", "password": "UM-Test@1234"},
        "code": 109,
        "tracked_email": "nonexistent@fresh.invalid",
        "needs_baseline": False,
    },
    "TC-UM-019": {
        "payload": {"email": "", "password": "UM-Test@1234"},
        "code": 109,
        "tracked_email": "",
        "needs_baseline": False,
    },
    "TC-UM-020": {
        "payload": {"email": BASELINE_EMAIL, "password": ""},
        "code": 109,
        "tracked_email": BASELINE_EMAIL,
        "needs_baseline": True,
    },
    "TC-UM-024": {
        "payload": None,
        "code": 109,
        "tracked_email": BASELINE_EMAIL,
        "needs_baseline": True,
    },
    "TC-UM-025": {
        "payload": {"email": BASELINE_EMAIL},
        "code": 500,
        "tracked_email": BASELINE_EMAIL,
        "needs_baseline": True,
    },
}


def run_login_rejection_case(case_id: str) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    definition = LOGIN_REJECTION_CASES[case_id]
    for group in GROUP_ORDER:
        precondition = _ensure_baseline_user(group) if definition["needs_baseline"] else {"target_count": _email_count(group, [definition["tracked_email"]])}
        before = _auth_snapshot(group, definition["tracked_email"])
        response = _login(case_id, group, "rejected_login", definition["payload"])
        after = _auth_snapshot(group, definition["tracked_email"])
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "last_login_unchanged": before.get("last_login_time") == after.get("last_login_time"),
            "auth_state_unchanged": before.get("auth_state_fingerprint") == after.get("auth_state_fingerprint"),
        }
        ok = rejected_login_contract_ok(observed, definition["code"])
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "confirm_login_precondition", "observed": precondition},
                {
                    "name": "submit_rejected_login",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_auth_state_comparison",
                    "before": before,
                    "after": after,
                    "last_login_unchanged": observed["last_login_unchanged"],
                    "auth_state_unchanged": observed["auth_state_unchanged"],
                },
            ],
            oracle={
                "http_status": 200,
                "code": definition["code"],
                "auth_state_unchanged": True,
            },
        )
    return _finalize(recorder, case_id)


def run_um021() -> dict[str, Any]:
    case_id = "TC-UM-021"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    upper_email = BASELINE_EMAIL.upper()
    for group in GROUP_ORDER:
        precondition = _ensure_baseline_user(group)
        before = _auth_snapshot(group, BASELINE_EMAIL)
        response = _login(
            case_id,
            group,
            "uppercase_email_login",
            {"email": upper_email, "password": BASELINE_PASSWORD},
        )
        after = _auth_snapshot(group, BASELINE_EMAIL)
        collation = _email_collation_snapshot(group, BASELINE_EMAIL)
        expected_code = 0 if group == "control" else 109
        state_changed = before.get("auth_state_fingerprint") != after.get("auth_state_fingerprint")
        ok = response["http_status"] == 200 and response["code"] == expected_code and collation["lower_match_count"] == 1 and state_changed == (group == "control")
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "confirm_lowercase_fixture", "observed": precondition},
                {
                    "name": "login_with_uppercase_email",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "auth_header_present": response["auth_header_present"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_collation_and_state_validation",
                    "observed": collation,
                    "auth_state_changed": state_changed,
                },
            ],
            oracle={
                "control_code": 0,
                "experiment_code": 109,
                "lowercase_match_count": 1,
            },
        )
    return _finalize(recorder, case_id)


def run_um022() -> dict[str, Any]:
    case_id = "TC-UM-022"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        precondition = _ensure_baseline_user(group)
        login = _login(
            case_id,
            group,
            "login_for_auth_header",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        valid = _auth_request(
            case_id,
            group,
            "valid_auth_header",
            "GET",
            "/users/me",
            login["_auth_value"],
        )
        invalid = _auth_request(
            case_id,
            group,
            "invalid_auth_header",
            "GET",
            "/users/me",
            "invalid_token_here",
        )
        valid_data = valid["data"] if isinstance(valid["data"], dict) else {}
        observed = {
            "valid_http_status": valid["http_status"],
            "valid_code": valid["code"],
            "valid_email_matches": valid_data.get("email") == BASELINE_EMAIL,
            "invalid_http_status": invalid["http_status"],
            "invalid_code": invalid["code"],
        }
        ok = login["code"] == 0 and token_access_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "login_without_reusing_cookie",
                    "precondition": precondition,
                    "code": login["code"],
                    "auth_header_present": login["auth_header_present"],
                    "raw_sha256": login["raw_sha256"],
                },
                {
                    "name": "request_profile_with_valid_header",
                    "http_status": valid["http_status"],
                    "code": valid["code"],
                    "email_matches": observed["valid_email_matches"],
                    "raw_sha256": valid["raw_sha256"],
                },
                {
                    "name": "request_profile_with_invalid_header",
                    "http_status": invalid["http_status"],
                    "code": invalid["code"],
                    "raw_sha256": invalid["raw_sha256"],
                },
            ],
            oracle={"valid": [200, 0], "invalid": [401, 401]},
        )
    return _finalize(recorder, case_id)


def run_um023() -> dict[str, Any]:
    case_id = "TC-UM-023"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        precondition = _ensure_baseline_user(group)
        first = _login(
            case_id,
            group,
            "login_1",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        second = _login(
            case_id,
            group,
            "login_2",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        old_access = _auth_request(
            case_id,
            group,
            "old_auth",
            "GET",
            "/users/me",
            first["_auth_value"],
        )
        new_access = _auth_request(
            case_id,
            group,
            "new_auth",
            "GET",
            "/users/me",
            second["_auth_value"],
        )
        snapshot = _auth_snapshot(group, BASELINE_EMAIL)
        ok = (
            first["code"] == 0
            and second["code"] == 0
            and first["auth_fingerprint"] != second["auth_fingerprint"]
            and old_access["http_status"] == 401
            and old_access["code"] == 401
            and new_access["http_status"] == 200
            and new_access["code"] == 0
            and not snapshot["invalid_prefix"]
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "confirm_baseline_user", "observed": precondition},
                {
                    "name": "login_twice",
                    "first_code": first["code"],
                    "second_code": second["code"],
                    "auth_values_distinct": first["auth_fingerprint"] != second["auth_fingerprint"],
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {
                    "name": "validate_old_and_new_auth_headers_without_cookies",
                    "old": {"http_status": old_access["http_status"], "code": old_access["code"]},
                    "new": {"http_status": new_access["http_status"], "code": new_access["code"]},
                    "raw_sha256": [old_access["raw_sha256"], new_access["raw_sha256"]],
                },
                {"name": "read_only_latest_auth_state", "observed": snapshot},
            ],
            oracle={"old_auth": [401, 401], "new_auth": [200, 0]},
        )
    return _finalize(recorder, case_id)


def run_um026() -> dict[str, Any]:
    case_id = "TC-UM-026"
    email = FLOW_EMAIL
    password = FLOW_ORIGINAL_PASSWORD
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        cleanup = _delete_user_via_admin(group, email)
        registration = _register(
            case_id,
            group,
            "register",
            {"email": email, "nickname": "UM026Flow", "password": password},
        )
        registration_profile = _auth_request(
            case_id,
            group,
            "registration_profile",
            "GET",
            "/users/me",
            registration["_auth_value"],
        )
        logout = _auth_request(
            case_id,
            group,
            "logout",
            "POST",
            "/auth/logout",
            registration["_auth_value"],
        )
        after_logout = _auth_snapshot(group, email)
        old_access = _auth_request(
            case_id,
            group,
            "old_auth_after_logout",
            "GET",
            "/users/me",
            registration["_auth_value"],
        )
        new_login = _login(
            case_id,
            group,
            "new_login",
            {"email": email, "password": password},
        )
        after_login = _auth_snapshot(group, email)
        new_profile = _auth_request(
            case_id,
            group,
            "new_profile",
            "GET",
            "/users/me",
            new_login["_auth_value"],
        )
        observed = {
            "registration_code": registration["code"],
            "registration_profile_code": registration_profile["code"],
            "logout_code": logout["code"],
            "invalid_prefix_after_logout": after_logout.get("invalid_prefix"),
            "old_auth_http_status": old_access["http_status"],
            "old_auth_code": old_access["code"],
            "new_login_code": new_login["code"],
            "new_profile_code": new_profile["code"],
            "new_auth_not_invalid": after_login.get("invalid_prefix") is False,
            "tenant_count": after_login.get("tenant_count"),
            "owner_relation_count": after_login.get("owner_relation_count"),
        }
        ok = cleanup and full_user_flow_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "register_and_use_registration_auth",
                    "registration_code": registration["code"],
                    "profile_code": registration_profile["code"],
                    "raw_sha256": [registration["raw_sha256"], registration_profile["raw_sha256"]],
                },
                {
                    "name": "logout_and_reject_old_auth",
                    "logout_code": logout["code"],
                    "database_after_logout": after_logout,
                    "old_auth_http_status": old_access["http_status"],
                    "old_auth_code": old_access["code"],
                    "raw_sha256": [logout["raw_sha256"], old_access["raw_sha256"]],
                },
                {
                    "name": "login_again_and_use_new_auth",
                    "login_code": new_login["code"],
                    "profile_code": new_profile["code"],
                    "database_after_login": after_login,
                    "raw_sha256": [new_login["raw_sha256"], new_profile["raw_sha256"]],
                },
            ],
            oracle={
                "registration_profile": 0,
                "logout_invalid_prefix": True,
                "old_auth": [401, 401],
                "new_login_profile": [0, 0],
            },
        )
    return _finalize(recorder, case_id)


def _login_flow_user(case_id: str, group: str, label: str, password: str) -> dict[str, Any]:
    _ensure_flow_user(group)
    return _login(
        case_id,
        group,
        label,
        {"email": FLOW_EMAIL, "password": password},
    )


def run_um027() -> dict[str, Any]:
    case_id = "TC-UM-027"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login_flow_user(case_id, group, "login", FLOW_ORIGINAL_PASSWORD)
        profile = _auth_request(case_id, group, "profile", "GET", "/users/me", login["_auth_value"])
        database = _user_state_snapshot(group, FLOW_EMAIL)
        data = profile["data"] if isinstance(profile["data"], dict) else {}
        matches = all(data.get(key) == database.get(key) for key in ("nickname", "email", "language", "color_schema", "status", "is_active", "is_superuser"))
        sensitive_absent = not {"password", "access_token"}.intersection(data)
        ok = login["code"] == 0 and profile["http_status"] == 200 and profile["code"] == 0 and matches and sensitive_absent
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "login_and_get_profile",
                    "login_code": login["code"],
                    "profile_http_status": profile["http_status"],
                    "profile_code": profile["code"],
                    "sensitive_fields_absent": sensitive_absent,
                    "raw_sha256": [login["raw_sha256"], profile["raw_sha256"]],
                },
                {
                    "name": "compare_profile_with_read_only_database",
                    "profile_matches": matches,
                    "database": database,
                },
            ],
            oracle={"profile_code": 0, "database_matches": True, "sensitive_fields_absent": True},
        )
    return _finalize(recorder, case_id)


def run_um028() -> dict[str, Any]:
    case_id = "TC-UM-028"
    update = {"nickname": "UpdatedUser026", "language": "English", "color_schema": "Dark"}
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login_flow_user(case_id, group, "login", FLOW_ORIGINAL_PASSWORD)
        patch = _auth_request(
            case_id,
            group,
            "patch_profile",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload=update,
        )
        profile = _auth_request(case_id, group, "get_profile", "GET", "/users/me", login["_auth_value"])
        database = _user_state_snapshot(group, FLOW_EMAIL)
        data = profile["data"] if isinstance(profile["data"], dict) else {}
        observed = {
            "patch_code": patch["code"],
            "patch_data": patch["data"],
            "profile_code": profile["code"],
            "profile_matches": all(data.get(k) == v for k, v in update.items()),
            "database_matches": all(database.get(k) == v for k, v in update.items()),
        }
        ok = profile_update_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "patch_profile_via_api",
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "data": patch["data"],
                    "raw_sha256": patch["raw_sha256"],
                },
                {
                    "name": "api_and_database_validation",
                    "profile_code": profile["code"],
                    "profile_matches": observed["profile_matches"],
                    "database_matches": observed["database_matches"],
                    "database": database,
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle=update,
        )
    return _finalize(recorder, case_id)


def run_um029() -> dict[str, Any]:
    case_id = "TC-UM-029"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login_flow_user(case_id, group, "login", FLOW_ORIGINAL_PASSWORD)
        before = _user_state_snapshot(group, FLOW_EMAIL)
        patch = _auth_request(
            case_id,
            group,
            "patch_protected_fields",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={
                "email": "hacked@fresh.invalid",
                "status": "0",
                "is_superuser": True,
                "is_active": "0",
                "nickname": "ProtectedTest",
            },
        )
        after = _user_state_snapshot(group, FLOW_EMAIL)
        observed = {
            "patch_code": patch["code"],
            "email_unchanged": after.get("email") == before.get("email"),
            "status_unchanged": after.get("status") == before.get("status"),
            "superuser_unchanged": after.get("is_superuser") == before.get("is_superuser"),
            "active_unchanged": after.get("is_active") == before.get("is_active"),
            "nickname_changed": after.get("nickname") == "ProtectedTest",
        }
        ok = protected_fields_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "patch_protected_and_mutable_fields",
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "raw_sha256": patch["raw_sha256"],
                },
                {
                    "name": "read_only_before_after_validation",
                    "before": before,
                    "after": after,
                    "contract": observed,
                },
            ],
            oracle={"protected_fields_unchanged": True, "nickname": "ProtectedTest"},
        )
    return _finalize(recorder, case_id)


def run_um030() -> dict[str, Any]:
    case_id = "TC-UM-030"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login_flow_user(case_id, group, "login", FLOW_ORIGINAL_PASSWORD)
        before = _user_state_snapshot(group, FLOW_EMAIL)
        patch = _auth_request(
            case_id,
            group,
            "change_password",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"password": FLOW_ORIGINAL_PASSWORD, "new_password": FLOW_NEW_PASSWORD},
            encrypt_fields=("password", "new_password"),
        )
        after = _user_state_snapshot(group, FLOW_EMAIL)
        new_login = _login(
            case_id,
            group,
            "login_new_password",
            {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD},
        )
        old_login = _login(
            case_id,
            group,
            "login_old_password",
            {"email": FLOW_EMAIL, "password": FLOW_ORIGINAL_PASSWORD},
        )
        observed = {
            "patch_code": patch["code"],
            "new_login_code": new_login["code"],
            "old_login_code": old_login["code"],
            "credential_hash_changed": before.get("credential_hash_fingerprint") != after.get("credential_hash_fingerprint"),
        }
        ok = password_change_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "change_password_via_profile_api",
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "raw_sha256": patch["raw_sha256"],
                },
                {
                    "name": "login_with_new_and_old_passwords",
                    "new_login_code": new_login["code"],
                    "old_login_code": old_login["code"],
                    "raw_sha256": [new_login["raw_sha256"], old_login["raw_sha256"]],
                },
                {
                    "name": "read_only_credential_hash_comparison",
                    "before_fingerprint": before.get("credential_hash_fingerprint"),
                    "after_fingerprint": after.get("credential_hash_fingerprint"),
                    "changed": observed["credential_hash_changed"],
                    "scheme": after.get("credential_hash_scheme"),
                },
            ],
            oracle={"patch_code": 0, "new_login_code": 0, "old_login_code": 109},
        )
    return _finalize(recorder, case_id)


def _activate_flow_user(case_id: str, group: str, state: str) -> dict[str, Any]:
    encoded = requests.utils.quote(FLOW_EMAIL, safe="")
    return _admin_action(
        case_id,
        group,
        f"activate_{state}",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": state},
    )


def run_um031() -> dict[str, Any]:
    case_id = "TC-UM-031"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _ensure_flow_user(group)
        admin = _activate_flow_user(case_id, group, "off")
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = admin["code"] == 0 and state.get("status") == "1" and state.get("is_active") == "0"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "disable_user_via_admin_api",
                    "http_status": admin["http_status"],
                    "code": admin["code"],
                    "raw_sha256": admin["raw_sha256"],
                },
                {"name": "read_only_status_validation", "observed": state},
            ],
            oracle={"status": "1", "is_active": "0"},
        )
    return _finalize(recorder, case_id)


def run_um032() -> dict[str, Any]:
    case_id = "TC-UM-032"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        before = _auth_snapshot(group, FLOW_EMAIL)
        login = _login(case_id, group, "disabled_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        after = _auth_snapshot(group, FLOW_EMAIL)
        state = _user_state_snapshot(group, FLOW_EMAIL)
        observed = {
            "admin_code": 0,
            "status": state.get("status"),
            "is_active": state.get("is_active"),
            "login_code": login["code"],
        }
        ok = (
            status_transition_contract_ok(observed, expected_status="1", expected_active="0", expected_login_code=403)
            and before.get("last_login_time") == after.get("last_login_time")
            and before.get("auth_state_fingerprint") == after.get("auth_state_fingerprint")
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "login_disabled_user",
                    "http_status": login["http_status"],
                    "code": login["code"],
                    "raw_sha256": login["raw_sha256"],
                },
                {"name": "read_only_state_unchanged", "before": before, "after": after, "user": state},
            ],
            oracle={"code": 403, "last_login_and_auth_state_unchanged": True},
        )
    return _finalize(recorder, case_id)


def run_um033() -> dict[str, Any]:
    case_id = "TC-UM-033"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        admin = _activate_flow_user(case_id, group, "on")
        login = _login(case_id, group, "reactivated_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        state = _user_state_snapshot(group, FLOW_EMAIL)
        observed = {
            "admin_code": admin["code"],
            "status": state.get("status"),
            "is_active": state.get("is_active"),
            "login_code": login["code"],
        }
        ok = status_transition_contract_ok(observed, expected_status="1", expected_active="1", expected_login_code=0)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "reactivate_via_admin_api", "code": admin["code"], "raw_sha256": admin["raw_sha256"]},
                {"name": "login_and_read_only_validate", "login_code": login["code"], "state": state, "raw_sha256": login["raw_sha256"]},
            ],
            oracle={"status": "1", "is_active": "1", "login_code": 0},
        )
    return _finalize(recorder, case_id)


def run_um034() -> dict[str, Any]:
    case_id = "TC-UM-034"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        changed = _set_user_status_direct(group, FLOW_EMAIL, "0")
        login = _login(case_id, group, "status_zero_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = changed and state.get("status") == "0" and state.get("is_active") == "1" and login["code"] == 109
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "plan_authorized_direct_status_fixture", "changed_exactly_one": changed},
                {"name": "login_status_zero_user", "http_status": login["http_status"], "code": login["code"], "raw_sha256": login["raw_sha256"]},
                {"name": "read_only_status_validation", "observed": state},
            ],
            oracle={"status": "0", "is_active": "1", "login_code": 109},
        )
    return _finalize(recorder, case_id)


def run_um035() -> dict[str, Any]:
    case_id = "TC-UM-035"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        status_login = _login(case_id, group, "status_zero_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        restored = _set_user_status_direct(group, FLOW_EMAIL, "1")
        admin = _activate_flow_user(case_id, group, "off")
        active_login = _login(case_id, group, "inactive_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = status_login["code"] == 109 and restored and admin["code"] == 0 and active_login["code"] == 403 and state.get("status") == "1" and state.get("is_active") == "0"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "verify_status_zero_behavior", "code": status_login["code"], "raw_sha256": status_login["raw_sha256"]},
                {"name": "restore_status_and_disable_via_admin", "status_restored": restored, "admin_code": admin["code"], "raw_sha256": admin["raw_sha256"]},
                {"name": "verify_inactive_behavior", "code": active_login["code"], "state": state, "raw_sha256": active_login["raw_sha256"]},
            ],
            oracle={"status_zero_code": 109, "inactive_code": 403},
        )
    return _finalize(recorder, case_id)


def run_um036() -> dict[str, Any]:
    case_id = "TC-UM-036"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        status_restored = _set_user_status_direct(group, FLOW_EMAIL, "1")
        admin = _activate_flow_user(case_id, group, "on")
        login = _login(case_id, group, "restored_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        profile = _auth_request(case_id, group, "restored_profile", "GET", "/users/me", login["_auth_value"])
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = status_restored and admin["code"] == 0 and login["code"] == 0 and profile["code"] == 0 and state.get("status") == "1" and state.get("is_active") == "1"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "restore_status_and_active_state", "status_restored": status_restored, "admin_code": admin["code"], "raw_sha256": admin["raw_sha256"]},
                {"name": "login_and_profile_after_restore", "login_code": login["code"], "profile_code": profile["code"], "raw_sha256": [login["raw_sha256"], profile["raw_sha256"]]},
                {"name": "read_only_state_validation", "observed": state},
            ],
            oracle={"status": "1", "is_active": "1", "login_profile_codes": [0, 0]},
        )
    return _finalize(recorder, case_id)


def _set_flow_admin(case_id: str, group: str, enabled: bool) -> dict[str, Any]:
    encoded = requests.utils.quote(FLOW_EMAIL, safe="")
    return _admin_action(
        case_id,
        group,
        "grant_admin" if enabled else "revoke_admin",
        "PUT" if enabled else "DELETE",
        f"/users/{encoded}/admin",
    )


def run_role_transition(case_id: str, expected: bool) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        admin = _set_flow_admin(case_id, group, expected)
        state = _user_state_snapshot(group, FLOW_EMAIL)
        login = _login(case_id, group, "login_after_role_change", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        observed = {
            "admin_code": admin["code"],
            "database_superuser": state.get("is_superuser"),
            "login_superuser": login["response_data"].get("is_superuser"),
        }
        ok = role_transition_contract_ok(observed, expected=expected)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "change_role_via_admin_api", "http_status": admin["http_status"], "code": admin["code"], "raw_sha256": admin["raw_sha256"]},
                {
                    "name": "login_and_read_only_role_validation",
                    "login_code": login["code"],
                    "login_superuser": observed["login_superuser"],
                    "database_superuser": observed["database_superuser"],
                    "raw_sha256": login["raw_sha256"],
                },
            ],
            oracle={"is_superuser": expected},
        )
    return _finalize(recorder, case_id)


def run_um038() -> dict[str, Any]:
    case_id = "TC-UM-038"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    for group in GROUP_ORDER:
        login = _login(case_id, group, "superuser_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        response = requests.get(
            f"http://127.0.0.1:{int(environments[group]['ADMIN_PORT'])}/api/v1/admin/users",
            headers={"Authorization": f"Bearer {login['_auth_value']}"},
            timeout=45,
        )
        raw_sha = _record_http(case_id, group, "admin_users", {"Authorization": login["_auth_value"]}, response)
        body = response.json()
        users = body.get("data") if isinstance(body.get("data"), list) else []
        target_present = any(item.get("email") == FLOW_EMAIL for item in users if isinstance(item, dict))
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = state.get("is_superuser") is True and response.status_code == 200 and body.get("code") == 0 and target_present
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "login_as_promoted_user", "code": login["code"], "is_superuser": login["response_data"].get("is_superuser"), "raw_sha256": login["raw_sha256"]},
                {
                    "name": "access_real_admin_users_endpoint",
                    "http_status": response.status_code,
                    "code": body.get("code"),
                    "target_present": target_present,
                    "returned_user_count": len(users),
                    "raw_sha256": raw_sha,
                },
                {"name": "read_only_role_validation", "observed": state},
            ],
            oracle={"admin_http_status": 200, "code": 0, "target_present": True},
        )
    return _finalize(recorder, case_id)


def run_protected_self_patch(case_id: str, field: str, attempted: Any) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(case_id, group, "login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        before = _user_state_snapshot(group, FLOW_EMAIL)
        patch = _auth_request(
            case_id,
            group,
            "protected_patch",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={field: attempted},
        )
        after = _user_state_snapshot(group, FLOW_EMAIL)
        profile = _auth_request(case_id, group, "profile", "GET", "/users/me", login["_auth_value"])
        key = "is_superuser" if field == "is_superuser" else "status"
        profile_data = profile["data"] if isinstance(profile["data"], dict) else {}
        ok = patch["code"] == 0 and after.get(key) == before.get(key) and profile_data.get(key) == before.get(key)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "attempt_self_patch_of_protected_field", "field": field, "http_status": patch["http_status"], "code": patch["code"], "raw_sha256": patch["raw_sha256"]},
                {
                    "name": "database_and_profile_unchanged",
                    "before": before.get(key),
                    "after": after.get(key),
                    "profile": profile_data.get(key),
                    "profile_code": profile["code"],
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={"field": field, "unchanged": True},
        )
    return _finalize(recorder, case_id)


def run_um042() -> dict[str, Any]:
    case_id = "TC-UM-042"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        changed = _set_user_status_direct(group, FLOW_EMAIL, "0")
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = changed and state.get("user_count") == 1 and state.get("status") == "0"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "plan_authorized_soft_delete_fixture", "final_value_confirmed": changed},
                {"name": "read_only_soft_delete_validation", "observed": state},
            ],
            oracle={"user_count": 1, "status": "0"},
        )
    return _finalize(recorder, case_id)


def run_um043() -> dict[str, Any]:
    case_id = "TC-UM-043"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        old_auth = _signed_current_auth(group, FLOW_EMAIL)
        login = _login(case_id, group, "soft_deleted_login", {"email": FLOW_EMAIL, "password": FLOW_NEW_PASSWORD})
        profile = _auth_request(case_id, group, "old_auth_profile", "GET", "/users/me", old_auth)
        state = _user_state_snapshot(group, FLOW_EMAIL)
        ok = state.get("status") == "0" and login["http_status"] == 200 and login["code"] == 109 and profile["http_status"] == 401 and profile["code"] == 401
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "login_soft_deleted_user", "http_status": login["http_status"], "code": login["code"], "raw_sha256": login["raw_sha256"]},
                {"name": "use_preexisting_signed_auth_without_cookie", "http_status": profile["http_status"], "code": profile["code"], "raw_sha256": profile["raw_sha256"]},
                {"name": "read_only_status_validation", "observed": state},
            ],
            oracle={"login": [200, 109], "old_auth": [401, 401]},
        )
    return _finalize(recorder, case_id)


def _disable_and_delete_user(case_id: str, group: str, email: str) -> dict[str, Any]:
    encoded = requests.utils.quote(email, safe="")
    disabled = _admin_action(
        case_id,
        group,
        "disable_before_delete",
        "PUT",
        f"/users/{encoded}/activate",
        {"activate_status": "off"},
    )
    deleted = _admin_action(case_id, group, "delete_user", "DELETE", f"/users/{encoded}")
    return {"disabled": disabled, "deleted": deleted}


def run_um044() -> dict[str, Any]:
    case_id = "TC-UM-044"
    email = CASCADE_EMAIL
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        cleanup = _delete_user_via_admin(group, email)
        registration = _register(
            case_id,
            group,
            "register_delete_fixture",
            {"email": email, "nickname": "DeleteTest044", "password": "UM044-Test@1234"},
        )
        user_id = str(registration["response_data"].get("id"))
        tenant_id = user_id
        dataset = _auth_request(
            case_id,
            group,
            "create_dataset_fixture",
            "POST",
            "/datasets",
            registration["_auth_value"],
            payload={"name": f"um044-{group}-dataset", "chunk_method": "naive"},
        )
        dataset_data = dataset["data"] if isinstance(dataset["data"], dict) else {}
        dataset_id = str(dataset_data.get("id")) if dataset_data.get("id") else None
        before = _cascade_snapshot(group, email, user_id, tenant_id, dataset_id)
        deletion = _disable_and_delete_user(case_id, group, email)
        after = _cascade_snapshot(group, email, user_id, tenant_id, dataset_id)
        observed = {"admin_delete_code": deletion["deleted"]["code"], **after}
        ok = cleanup and registration["code"] == 0 and dataset["code"] == 0 and deletion["disabled"]["code"] == 0 and cascade_delete_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "register_and_create_dataset_via_api",
                    "registration_code": registration["code"],
                    "dataset_code": dataset["code"],
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "dataset_id": dataset_id,
                    "raw_sha256": [registration["raw_sha256"], dataset["raw_sha256"]],
                },
                {"name": "read_only_fixture_snapshot", "observed": before},
                {
                    "name": "disable_and_delete_via_admin_api",
                    "disable_code": deletion["disabled"]["code"],
                    "delete_code": deletion["deleted"]["code"],
                    "raw_sha256": [deletion["disabled"]["raw_sha256"], deletion["deleted"]["raw_sha256"]],
                },
                {"name": "read_only_cascade_validation", "observed": after},
            ],
            oracle={"all_saved_identifier_counts": 0},
        )
    return _finalize(recorder, case_id)


def _um044_old_id(group: str) -> str:
    data = json.loads((EVIDENCE_DIR / "TC-UM-044.json").read_text(encoding="utf-8"))
    group_data = next(item for item in data["groups"] if item["group"] == group)
    return str(group_data["steps"][0]["user_id"])


def run_um045() -> dict[str, Any]:
    case_id = "TC-UM-045"
    email = CASCADE_EMAIL
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        old_id = _um044_old_id(group)
        pre_count = _email_count(group, [email])
        precondition_steps: list[dict[str, Any]] = [
            {
                "name": "check_physical_delete_precondition",
                "old_user_id": old_id,
                "preexisting_count": pre_count,
            }
        ]
        if pre_count:
            deletion = _disable_and_delete_user(case_id, group, email)
            remaining = _email_count(group, [email])
            precondition_steps.append(
                {
                    "name": "retry_plan_required_physical_delete_via_admin_api",
                    "disable_code": deletion["disabled"]["code"],
                    "delete_code": deletion["deleted"]["code"],
                    "remaining_count": remaining,
                    "raw_sha256": [
                        deletion["disabled"]["raw_sha256"],
                        deletion["deleted"]["raw_sha256"],
                    ],
                }
            )
            if not physical_delete_precondition_ok(int(deletion["deleted"]["code"]), remaining):
                recorder.add_group(
                    group,
                    "BLOCKED",
                    precondition_steps,
                    oracle={"physical_delete_code": 0, "remaining_count": 0},
                    findings=[
                        {
                            "id": "UM-CASCADE-DELETE-001",
                            "summary": "TC-UM-044 physical deletion precondition is not satisfied",
                        }
                    ],
                )
                continue
        registration = _register(
            case_id,
            group,
            "re_register",
            {"email": email, "nickname": "Reborn044", "password": "UM044-New@9999"},
        )
        new_id = str(registration["response_data"].get("id"))
        login = _login(case_id, group, "login_reborn", {"email": email, "password": "UM044-New@9999"})
        snapshot = _registration_snapshot(group, email)
        ok = registration["code"] == 0 and login["code"] == 0 and new_id != old_id and snapshot.get("user_count") == 1 and snapshot.get("stored_nickname") == "Reborn044"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            precondition_steps
            + [
                {
                    "name": "register_and_login_same_email",
                    "registration_code": registration["code"],
                    "login_code": login["code"],
                    "new_user_id": new_id,
                    "ids_distinct": new_id != old_id,
                    "raw_sha256": [registration["raw_sha256"], login["raw_sha256"]],
                },
                {"name": "read_only_new_user_validation", "observed": snapshot},
            ],
            oracle={"code": 0, "new_id_distinct": True, "nickname": "Reborn044"},
        )
    return _finalize(recorder, case_id)


def run_um046() -> dict[str, Any]:
    case_id = "TC-UM-046"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        snapshot = _orphan_snapshot(group)
        ok = all(value == 0 for value in snapshot.values())
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [{"name": "read_only_global_orphan_queries", "observed": snapshot}],
            oracle={"all_orphan_counts": 0},
        )
    return _finalize(recorder, case_id)


def run_um047() -> dict[str, Any]:
    case_id = "TC-UM-047"
    email = "um047@fresh.invalid"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _delete_user_via_admin(group, email)
        first = _register(case_id, group, "create_1", {"email": email, "nickname": "RecreateUser047", "password": "UM047-Test@1234"})
        first_id = str(first["response_data"].get("id"))
        deletion = _disable_and_delete_user(case_id, group, email)
        count_after_delete = _email_count(group, [email])
        old_graph = _cascade_snapshot(group, email, first_id, first_id, dataset_id=None)
        second = _register(case_id, group, "create_2", {"email": email, "nickname": "NewRecreate047", "password": "UM047-New@5678"})
        second_id = str(second["response_data"].get("id"))
        snapshot = _registration_snapshot(group, email)
        observed = {
            "first_create_code": first["code"],
            "delete_code": deletion["deleted"]["code"],
            "count_after_delete": count_after_delete,
            "old_graph_absent": all(value == 0 for value in old_graph.values()),
            "second_create_code": second["code"],
            "ids_distinct": first_id != second_id,
            "final_count": snapshot.get("user_count"),
            "final_nickname_matches": snapshot.get("stored_nickname") == "NewRecreate047",
        }
        ok = recreation_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "create_first_user", "code": first["code"], "user_id": first_id, "raw_sha256": first["raw_sha256"]},
                {
                    "name": "delete_via_admin_and_verify_old_graph_absent",
                    "disable_code": deletion["disabled"]["code"],
                    "delete_code": deletion["deleted"]["code"],
                    "count": count_after_delete,
                    "old_graph": old_graph,
                    "raw_sha256": [deletion["disabled"]["raw_sha256"], deletion["deleted"]["raw_sha256"]],
                },
                {
                    "name": "create_second_user_and_compare",
                    "code": second["code"],
                    "user_id": second_id,
                    "ids_distinct": first_id != second_id,
                    "snapshot": snapshot,
                    "raw_sha256": second["raw_sha256"],
                },
            ],
            oracle={"old_graph_absent": True, "ids_distinct": True, "final_count": 1, "nickname": "NewRecreate047"},
        )
    return _finalize(recorder, case_id)


def run_um048() -> dict[str, Any]:
    case_id = "TC-UM-048"
    email = "um048@fresh.invalid"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _delete_user_via_admin(group, email)
        first = _register(case_id, group, "create", {"email": email, "nickname": "SoftDelUser048", "password": "UM048-Test@1234"})
        soft_deleted = _set_user_status_direct(group, email, "0")
        duplicate = _register(case_id, group, "duplicate_after_soft_delete", {"email": email, "nickname": "RetryUser048", "password": "UM048-Test@1234"})
        snapshot = _user_state_snapshot(group, email)
        ok = first["code"] == 0 and soft_deleted and duplicate["code"] == 103 and snapshot.get("user_count") == 1 and snapshot.get("status") == "0"
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "create_and_soft_delete_fixture", "create_code": first["code"], "soft_delete_final_value": soft_deleted, "raw_sha256": first["raw_sha256"]},
                {"name": "retry_same_email_registration", "http_status": duplicate["http_status"], "code": duplicate["code"], "raw_sha256": duplicate["raw_sha256"]},
                {"name": "read_only_single_soft_deleted_row", "observed": snapshot},
            ],
            oracle={"duplicate_code": 103, "user_count": 1, "status": "0"},
        )
    return _finalize(recorder, case_id)


def run_um049() -> dict[str, Any]:
    case_id = "TC-UM-049"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        admin = _admin_action(case_id, group, "list_users", "GET", "/users")
        api_users = admin["data"] if isinstance(admin["data"], list) else []
        database = _user_list_snapshot(group)
        api_emails = [item.get("email") for item in api_users if isinstance(item, dict)]
        db_emails = [item["email"] for item in database]
        fields_ok = all(set(item) == {"email", "nickname", "create_date", "is_active", "is_superuser"} for item in api_users if isinstance(item, dict))
        ok = admin["http_status"] == 200 and admin["code"] == 0 and api_emails == db_emails and api_emails == sorted(api_emails) and fields_ok
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "get_real_admin_user_list", "http_status": admin["http_status"], "code": admin["code"], "returned_count": len(api_users), "raw_sha256": admin["raw_sha256"]},
                {
                    "name": "read_only_set_and_order_comparison",
                    "database_count": len(database),
                    "email_sets_and_order_match": api_emails == db_emails,
                    "ascending": api_emails == sorted(api_emails),
                    "public_fields_exact": fields_ok,
                },
            ],
            oracle={"database_set_and_order_match": True, "public_fields_exact": True},
        )
    return _finalize(recorder, case_id)


def _owner_tenant_id(case_id: str, group: str, auth_value: str) -> tuple[str, dict[str, Any]]:
    tenants = _auth_request(case_id, group, "list_owner_tenants", "GET", "/tenants", auth_value)
    data = tenants["data"] if isinstance(tenants["data"], list) else []
    owner = [item for item in data if isinstance(item, dict) and item.get("role") == "owner"]
    if len(owner) != 1:
        raise RuntimeError("owner tenant was not found exactly once")
    return str(owner[0]["tenant_id"]), tenants


def _run_membership_case(case_id: str, target_email: str, target_nickname: str, target_password: str) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _delete_user_via_admin(group, target_email)
        owner_login = _login(
            case_id,
            group,
            "owner_login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        target = _register(case_id, group, "target_registration", {"email": target_email, "nickname": target_nickname, "password": target_password})
        tenant_id, tenant_list = _owner_tenant_id(case_id, group, owner_login["_auth_value"])
        invite = _auth_request(
            case_id,
            group,
            "invite_target",
            "POST",
            f"/tenants/{tenant_id}/users",
            owner_login["_auth_value"],
            payload={"email": target_email},
        )
        member_list = _auth_request(
            case_id,
            group,
            "list_members",
            "GET",
            f"/tenants/{tenant_id}/users",
            owner_login["_auth_value"],
        )
        members = member_list["data"] if isinstance(member_list["data"], list) else []
        target_id = str(target["response_data"].get("id"))
        snapshot = _membership_snapshot(group, target_email, tenant_id)
        observed = {
            "invite_code": invite["code"],
            "list_code": member_list["code"],
            "target_in_list": any(str(item.get("user_id")) == target_id for item in members if isinstance(item, dict)),
            "owner_in_list": any(str(item.get("user_id")) == tenant_id for item in members if isinstance(item, dict)),
            "database_role": snapshot.get("role"),
            "target_relation_count": snapshot.get("relation_count"),
        }
        ok = target["code"] == 0 and tenant_membership_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "register_target_and_get_owner_tenant", "registration_code": target["code"], "tenant_id": tenant_id, "raw_sha256": [target["raw_sha256"], tenant_list["raw_sha256"]]},
                {"name": "invite_via_owner_api", "http_status": invite["http_status"], "code": invite["code"], "raw_sha256": invite["raw_sha256"]},
                {
                    "name": "api_and_database_membership_validation",
                    "list_code": member_list["code"],
                    "returned_member_count": len(members),
                    "contract": observed,
                    "database": snapshot,
                    "raw_sha256": member_list["raw_sha256"],
                },
            ],
            oracle={"role": "invite", "target_relations": 2, "owner_excluded": True},
        )
    return _finalize(recorder, case_id)


def run_um050() -> dict[str, Any]:
    return _run_membership_case(
        "TC-UM-050",
        "um050member@fresh.invalid",
        "Member050",
        "UM050-Test@1234",
    )


def run_um051() -> dict[str, Any]:
    case_id = "TC-UM-051"
    target_email = "um050member@fresh.invalid"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        member_login = _login(case_id, group, "member_login", {"email": target_email, "password": "UM050-Test@1234"})
        owner_login = _login(
            case_id,
            group,
            "owner_login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        tenant_id, _tenants = _owner_tenant_id(case_id, group, owner_login["_auth_value"])
        response = _auth_request(case_id, group, "member_list_attempt", "GET", f"/tenants/{tenant_id}/users", member_login["_auth_value"])
        snapshot = _membership_snapshot(group, target_email, tenant_id)
        ok = member_login["code"] == 0 and snapshot.get("role") == "invite" and response["http_status"] == 200 and response["code"] == 109
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "login_non_owner_member", "code": member_login["code"], "database_role": snapshot.get("role"), "raw_sha256": member_login["raw_sha256"]},
                {"name": "attempt_owner_only_member_list", "http_status": response["http_status"], "code": response["code"], "raw_sha256": response["raw_sha256"]},
            ],
            oracle={"role": "invite", "http_status": 200, "code": 109},
        )
    return _finalize(recorder, case_id)


def run_um052() -> dict[str, Any]:
    return _run_membership_case(
        "TC-UM-052",
        "um052@fresh.invalid",
        "InviteTest052",
        "UM052-Test@1234",
    )


def _run_baseline_profile_field(case_id: str, field: str, value: Any) -> dict[str, Any]:
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        _ensure_baseline_user(group)
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        patch = _auth_request(
            case_id,
            group,
            f"patch_{field}",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={field: value},
        )
        profile = _auth_request(
            case_id,
            group,
            "profile",
            "GET",
            "/users/me",
            login["_auth_value"],
        )
        profile_data = profile["data"] if isinstance(profile["data"], dict) else {}
        database = _profile_storage_snapshot(group, BASELINE_EMAIL)
        observed = {
            "patch_code": patch["code"],
            "patch_data": patch["data"],
            "profile_code": profile["code"],
            "profile_matches": profile_data.get(field) == value,
            "database_matches": database.get(field) == value,
        }
        ok = login["code"] == 0 and profile_update_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "patch_baseline_profile_field",
                    "field": field,
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "data": patch["data"],
                    "raw_sha256": [login["raw_sha256"], patch["raw_sha256"]],
                },
                {
                    "name": "api_and_read_only_database_validation",
                    "field": field,
                    "profile_value": profile_data.get(field),
                    "database_value": database.get(field),
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={field: value},
        )
    return _finalize(recorder, case_id)


def run_um053() -> dict[str, Any]:
    return _run_baseline_profile_field("TC-UM-053", "nickname", "New Nickname_053")


def run_um054() -> dict[str, Any]:
    return _run_baseline_profile_field("TC-UM-054", "language", "English")


def run_um055() -> dict[str, Any]:
    return _run_baseline_profile_field("TC-UM-055", "color_schema", "Dark")


def run_um056() -> dict[str, Any]:
    case_id = "TC-UM-056"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        patch = _auth_request(
            case_id,
            group,
            "patch_empty_avatar",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"avatar": ""},
        )
        profile = _auth_request(
            case_id,
            group,
            "profile",
            "GET",
            "/users/me",
            login["_auth_value"],
        )
        profile_data = profile["data"] if isinstance(profile["data"], dict) else {}
        database = _profile_storage_snapshot(group, BASELINE_EMAIL)
        observed = {
            "patch_http_status": patch["http_status"],
            "patch_code": patch["code"],
            "patch_data": patch["data"],
            "profile_code": profile["code"],
            "profile_avatar_present": "avatar" in profile_data,
            "profile_avatar": profile_data.get("avatar"),
            "database_avatar": database.get("avatar"),
            "database_is_null": database.get("avatar_is_null"),
        }
        ok = login["code"] == 0 and avatar_empty_contract_ok(group, observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "patch_avatar_to_empty_string",
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "raw_sha256": [login["raw_sha256"], patch["raw_sha256"]],
                },
                {
                    "name": "field_contract_api_and_database_validation",
                    "observed": observed,
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={
                "control": {"database": "empty_string", "api": "empty_string"},
                "experiment": {"database": "null", "api": "null"},
            },
        )
    return _finalize(recorder, case_id)


def run_um057() -> dict[str, Any]:
    case_id = "TC-UM-057"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        before = _profile_storage_snapshot(group, BASELINE_EMAIL)
        empty = _auth_request(
            case_id,
            group,
            "patch_empty_nickname",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"nickname": ""},
        )
        spaces = _auth_request(
            case_id,
            group,
            "patch_spaces_nickname",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"nickname": "   "},
        )
        after = _profile_storage_snapshot(group, BASELINE_EMAIL)
        observed = {
            "responses": [
                {
                    "http_status": empty["http_status"],
                    "code": empty["code"],
                    "data": empty["data"],
                },
                {
                    "http_status": spaces["http_status"],
                    "code": spaces["code"],
                    "data": spaces["data"],
                },
            ],
            "nickname_unchanged": before.get("nickname") == after.get("nickname"),
        }
        messages_ok = empty["message"] == spaces["message"] == "Nickname cannot be empty."
        ok = nickname_rejection_contract_ok(observed) and messages_ok
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "reject_empty_and_space_only_nicknames",
                    "empty": {"http_status": empty["http_status"], "code": empty["code"], "message": empty["message"]},
                    "spaces": {"http_status": spaces["http_status"], "code": spaces["code"], "message": spaces["message"]},
                    "raw_sha256": [login["raw_sha256"], empty["raw_sha256"], spaces["raw_sha256"]],
                },
                {
                    "name": "read_only_nickname_unchanged",
                    "before": before.get("nickname"),
                    "after": after.get("nickname"),
                },
            ],
            oracle={"codes": [101, 101], "nickname_unchanged": True},
        )
    return _finalize(recorder, case_id)


def run_um058() -> dict[str, Any]:
    case_id = "TC-UM-058"
    valid_value = "User's Name_Test-01"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        valid = _auth_request(
            case_id,
            group,
            "patch_valid_special_nickname",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"nickname": valid_value},
        )
        invalid = _auth_request(
            case_id,
            group,
            "patch_invalid_special_nickname",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"nickname": "User@Name"},
        )
        profile = _auth_request(
            case_id,
            group,
            "profile",
            "GET",
            "/users/me",
            login["_auth_value"],
        )
        profile_data = profile["data"] if isinstance(profile["data"], dict) else {}
        database = _profile_storage_snapshot(group, BASELINE_EMAIL)
        observed = {
            "valid_patch_code": valid["code"],
            "invalid_patch_code": invalid["code"],
            "database_value_matches": database.get("nickname") == valid_value,
            "profile_value_matches": profile_data.get("nickname") == valid_value,
        }
        ok = special_nickname_contract_ok(observed) and valid["data"] is True and invalid["data"] is False and invalid["message"] == "Nickname contains invalid characters."
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "accept_valid_then_reject_invalid_special_nickname",
                    "valid_code": valid["code"],
                    "invalid_code": invalid["code"],
                    "invalid_message": invalid["message"],
                    "raw_sha256": [login["raw_sha256"], valid["raw_sha256"], invalid["raw_sha256"]],
                },
                {
                    "name": "api_and_database_keep_last_valid_value",
                    "observed": observed,
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={"stored_nickname": valid_value, "invalid_code": 101},
        )
    return _finalize(recorder, case_id)


def run_um059() -> dict[str, Any]:
    case_id = "TC-UM-059"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        patch = _auth_request(
            case_id,
            group,
            "patch_space_nickname",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={"nickname": "Space User"},
        )
        profile = _auth_request(
            case_id,
            group,
            "profile",
            "GET",
            "/users/me",
            login["_auth_value"],
        )
        profile_data = profile["data"] if isinstance(profile["data"], dict) else {}
        database = _profile_storage_snapshot(group, BASELINE_EMAIL)
        observed = {
            "patch_code": patch["code"],
            "profile_code": profile["code"],
            "database_value": database.get("nickname"),
            "database_length": database.get("nickname_length"),
            "database_octet_length": database.get("nickname_octet_length"),
            "profile_value": profile_data.get("nickname"),
        }
        ok = nonempty_nickname_storage_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "patch_nonempty_nickname_with_space",
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "raw_sha256": [login["raw_sha256"], patch["raw_sha256"]],
                },
                {
                    "name": "read_only_length_and_api_validation",
                    "observed": observed,
                    "raw_sha256": profile["raw_sha256"],
                },
            ],
            oracle={"nickname": "Space User", "length": 10, "octet_length": 10},
        )
    return _finalize(recorder, case_id)


def _run_concurrent_registrations(case_id: str, group: str, emails: list[str]) -> list[dict[str, Any]]:
    barrier = threading.Barrier(len(emails))

    def submit_one(index: int, email: str) -> dict[str, Any]:
        barrier.wait(timeout=10)
        return _register(
            case_id,
            group,
            f"register_{index}",
            {
                "email": email,
                "nickname": f"Concurrent{index}",
                "password": "UM060-Test@1234",
            },
        )

    with ThreadPoolExecutor(max_workers=len(emails)) as executor:
        futures = [executor.submit(submit_one, index, email) for index, email in enumerate(emails, start=1)]
        return [future.result(timeout=60) for future in futures]


def run_um060() -> dict[str, Any]:
    case_id = "TC-UM-060"
    emails = [f"um060-concurrent-{index}@fresh.invalid" for index in range(1, 6)]
    expected_nicknames = {email: f"Concurrent{index}" for index, email in enumerate(emails, start=1)}
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        cleanup_results = [_delete_user_via_admin(group, email) for email in emails]
        responses = _run_concurrent_registrations(case_id, group, emails)
        database = _concurrent_registration_snapshot(group, emails)
        response_summaries = [
            {
                "email": email,
                "http_status": response["http_status"],
                "code": response["code"],
                "auth_header_present": response["auth_header_present"],
                "raw_sha256": response["raw_sha256"],
            }
            for email, response in zip(emails, responses, strict=True)
        ]
        observed = {
            "cleanup_ok": all(cleanup_results),
            "response_count": len(responses),
            "all_response_codes_zero": all(item["http_status"] == 200 and item["code"] == 0 for item in responses),
            "all_auth_headers_present": all(item["auth_header_present"] for item in responses),
            "database_email_set_matches": [row["email"] for row in database] == sorted(emails),
            "database_rows": database,
        }
        nickname_values_match = all(row["nickname"] == expected_nicknames.get(row["email"]) for row in database)
        ok = concurrent_registration_contract_ok(observed) and nickname_values_match
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "five_registration_requests_released_concurrently",
                    "cleanup_results": cleanup_results,
                    "responses": response_summaries,
                },
                {
                    "name": "read_only_complete_user_graph_validation",
                    "database_rows": database,
                    "nickname_values_match": nickname_values_match,
                },
            ],
            oracle={
                "successful_responses": 5,
                "users": 5,
                "tenant_count_each": 1,
                "owner_relation_count_each": 1,
            },
        )
    return _finalize(recorder, case_id)


def run_um061() -> dict[str, Any]:
    case_id = "TC-UM-061"
    proposed_password = "UM061-New@5678"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        before = _user_state_snapshot(group, BASELINE_EMAIL)
        patch = _auth_request(
            case_id,
            group,
            "change_password_with_wrong_old_password",
            "PATCH",
            "/users/me",
            login["_auth_value"],
            payload={
                "password": "WrongOldPass",
                "new_password": proposed_password,
            },
            encrypt_fields=("password", "new_password"),
        )
        after = _user_state_snapshot(group, BASELINE_EMAIL)
        current_login = _login(
            case_id,
            group,
            "login_current_password",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        proposed_login = _login(
            case_id,
            group,
            "login_proposed_password",
            {"email": BASELINE_EMAIL, "password": proposed_password},
        )
        observed = {
            "patch_http_status": patch["http_status"],
            "patch_code": patch["code"],
            "patch_data": patch["data"],
            "credential_hash_unchanged": before.get("credential_hash_fingerprint") == after.get("credential_hash_fingerprint"),
            "current_password_login_code": current_login["code"],
            "proposed_password_login_code": proposed_login["code"],
        }
        ok = login["code"] == 0 and patch["message"] == "Password error!" and wrong_password_change_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "reject_wrong_old_password",
                    "http_status": patch["http_status"],
                    "code": patch["code"],
                    "message": patch["message"],
                    "raw_sha256": [login["raw_sha256"], patch["raw_sha256"]],
                },
                {
                    "name": "read_only_hash_and_login_validation",
                    "contract": observed,
                    "before_hash_fingerprint": before.get("credential_hash_fingerprint"),
                    "after_hash_fingerprint": after.get("credential_hash_fingerprint"),
                    "raw_sha256": [current_login["raw_sha256"], proposed_login["raw_sha256"]],
                },
            ],
            oracle={"patch_code": 109, "hash_unchanged": True, "new_password_inactive": True},
        )
    return _finalize(recorder, case_id)


def run_um062() -> dict[str, Any]:
    case_id = "TC-UM-062"
    evidence = _load_evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        login = _login(
            case_id,
            group,
            "login",
            {"email": BASELINE_EMAIL, "password": BASELINE_PASSWORD},
        )
        before = _auth_snapshot(group, BASELINE_EMAIL)
        logout = _auth_request(
            case_id,
            group,
            "logout",
            "POST",
            "/auth/logout",
            login["_auth_value"],
        )
        after = _auth_snapshot(group, BASELINE_EMAIL)
        old_profile = _auth_request(
            case_id,
            group,
            "old_auth_profile",
            "GET",
            "/users/me",
            login["_auth_value"],
        )
        observed = {
            "logout_http_status": logout["http_status"],
            "logout_code": logout["code"],
            "logout_data": logout["data"],
            "auth_state_changed": before.get("auth_state_fingerprint") != after.get("auth_state_fingerprint"),
            "invalid_prefix": after.get("invalid_prefix"),
            "old_auth_http_status": old_profile["http_status"],
            "old_auth_code": old_profile["code"],
        }
        ok = login["code"] == 0 and logout_invalidation_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "logout_via_api",
                    "http_status": logout["http_status"],
                    "code": logout["code"],
                    "data": logout["data"],
                    "raw_sha256": [login["raw_sha256"], logout["raw_sha256"]],
                },
                {
                    "name": "read_only_invalid_prefix_and_old_auth_rejection",
                    "contract": observed,
                    "before_auth_fingerprint": before.get("auth_state_fingerprint"),
                    "after_auth_fingerprint": after.get("auth_state_fingerprint"),
                    "auth_state_length": after.get("auth_state_length"),
                    "raw_sha256": old_profile["raw_sha256"],
                },
            ],
            oracle={"invalid_prefix": True, "old_auth": [401, 401]},
        )
    return _finalize(recorder, case_id)


def get_runners() -> dict[str, Callable[[], dict[str, Any]]]:
    return {
        "TC-UM-001": run_um001,
        "TC-UM-002": run_um002,
        "TC-UM-003": lambda: run_rejection_case("TC-UM-003"),
        "TC-UM-004": lambda: run_rejection_case("TC-UM-004"),
        "TC-UM-005": lambda: run_rejection_case("TC-UM-005"),
        "TC-UM-006": lambda: run_rejection_case("TC-UM-006"),
        "TC-UM-007": run_um007,
        "TC-UM-008": lambda: run_rejection_case("TC-UM-008"),
        "TC-UM-009": lambda: run_rejection_case("TC-UM-009"),
        "TC-UM-010": lambda: run_rejection_case("TC-UM-010"),
        "TC-UM-011": lambda: run_rejection_case("TC-UM-011"),
        "TC-UM-012": run_um012,
        "TC-UM-013": lambda: run_rejection_case("TC-UM-013"),
        "TC-UM-014": lambda: run_value_success_case("TC-UM-014", "um014@fresh.invalid", "A" * 100),
        "TC-UM-015": lambda: run_value_success_case("TC-UM-015", "um015@fresh.invalid", "测试用户"),
        "TC-UM-016": run_um016,
        "TC-UM-017": lambda: run_login_rejection_case("TC-UM-017"),
        "TC-UM-018": lambda: run_login_rejection_case("TC-UM-018"),
        "TC-UM-019": lambda: run_login_rejection_case("TC-UM-019"),
        "TC-UM-020": lambda: run_login_rejection_case("TC-UM-020"),
        "TC-UM-021": run_um021,
        "TC-UM-022": run_um022,
        "TC-UM-023": run_um023,
        "TC-UM-024": lambda: run_login_rejection_case("TC-UM-024"),
        "TC-UM-025": lambda: run_login_rejection_case("TC-UM-025"),
        "TC-UM-026": run_um026,
        "TC-UM-027": run_um027,
        "TC-UM-028": run_um028,
        "TC-UM-029": run_um029,
        "TC-UM-030": run_um030,
        "TC-UM-031": run_um031,
        "TC-UM-032": run_um032,
        "TC-UM-033": run_um033,
        "TC-UM-034": run_um034,
        "TC-UM-035": run_um035,
        "TC-UM-036": run_um036,
        "TC-UM-037": lambda: run_role_transition("TC-UM-037", True),
        "TC-UM-038": run_um038,
        "TC-UM-039": lambda: run_role_transition("TC-UM-039", False),
        "TC-UM-040": lambda: run_protected_self_patch("TC-UM-040", "is_superuser", True),
        "TC-UM-041": lambda: run_protected_self_patch("TC-UM-041", "status", "0"),
        "TC-UM-042": run_um042,
        "TC-UM-043": run_um043,
        "TC-UM-044": run_um044,
        "TC-UM-045": run_um045,
        "TC-UM-046": run_um046,
        "TC-UM-047": run_um047,
        "TC-UM-048": run_um048,
        "TC-UM-049": run_um049,
        "TC-UM-050": run_um050,
        "TC-UM-051": run_um051,
        "TC-UM-052": run_um052,
        "TC-UM-053": run_um053,
        "TC-UM-054": run_um054,
        "TC-UM-055": run_um055,
        "TC-UM-056": run_um056,
        "TC-UM-057": run_um057,
        "TC-UM-058": run_um058,
        "TC-UM-059": run_um059,
        "TC-UM-060": run_um060,
        "TC-UM-061": run_um061,
        "TC-UM-062": run_um062,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh user management cases")
    parser.add_argument("--case", choices=sorted(CASE_TITLES), required=True)
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
    return pair_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
