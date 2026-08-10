#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import requests
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)
from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_runner_result import pair_exit_code

RESOURCE_PREFIX = f"fr_{BATCH_ID}"
GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_DIR = evidence_dir("01_startup_migration")
ENV_EVIDENCE_DIR = evidence_dir("00_environment_setup")
EXPERIMENT_METADATA_SCHEMA = f"{RESOURCE_PREFIX}_experiment_metadata"
CASE_RESOURCE = re.compile(rf"^{re.escape(RESOURCE_PREFIX)}_(?:control|experiment)_sm\d{{3}}(?:_[a-z0-9_]+)?$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
REQUIRED_CORE_TABLES = {"user", "tenant", "system_settings"}
AUTHORIZED_GAUSS_DATABASE = "zws_test2"
TIKTOKEN_ENCODING_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
TIKTOKEN_CACHE_KEY = hashlib.sha1(TIKTOKEN_ENCODING_URL.encode()).hexdigest()
TIKTOKEN_ENCODING_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
MYSQL_ONLY_KEYWORDS = (
    "AUTO_INCREMENT",
    "ON DUPLICATE KEY UPDATE",
    "SHOW PROCESSLIST",
)
LEGACY_INDEXES = {
    "idx_api_key_provider_id": (
        "tenant_model_instance",
        ("api_key", "provider_id"),
    ),
    "tenantmodelinstance_api_key_provider_id": (
        "tenant_model_instance",
        ("api_key", "provider_id"),
    ),
    "idx_provider_model_instance": (
        "tenant_model",
        ("provider_id", "model_name", "instance_id"),
    ),
}
EMPTY_STRING_FIELDS = (
    "tenant.llm_id",
    "tenant.embd_id",
    "tenant.asr_id",
    "tenant.img2txt_id",
    "tenant.rerank_id",
    "knowledgebase.embd_id",
    "dialog.llm_id",
    "dialog.rerank_id",
    "memory.embd_id",
    "memory.llm_id",
    "file.source_type",
    "system_settings.value",
    "task.task_type",
    "sync_logs.error_msg",
    "sync_logs.full_exception_trace",
)
CASE_TITLES = {
    "TC-SM-001": "全新 Schema 首次启动",
    "TC-SM-002": "重复启动幂等性",
    "TC-SM-003": "并发启动锁保护",
    "TC-SM-004": "缺列补齐",
    "TC-SM-005": "tenant_llm 主键升级",
    "TC-SM-006": "user.email 唯一索引恢复",
    "TC-SM-007": "历史索引清理",
    "TC-SM-008": "GaussDB 空字符串根因验证",
    "TC-SM-009": "清单字段 nullable 验证",
    "TC-SM-010": "ORM 空字符串写入和读取",
    "TC-SM-011": "空字符串查询改写验证",
    "TC-SM-012": "GaussDB 不执行 MySQL 迁移脚本",
    "TC-SM-013": "表结构方言正确性",
    "TC-SM-014": "索引方言正确性",
    "TC-SM-015": "DB_TYPE 别名归一化",
    "TC-SM-016": "GAUSSDB_METADATA_SCHEMA 安全校验",
    "TC-SM-017": "GAUSSDB_METADATA 与 DocEngine 配置隔离",
    "TC-SM-018": "Admin 展示 GaussDB 配置",
    "TC-SM-019": "健康检查使用 SELECT 1",
    "TC-SM-020": "Docker Compose profile 隔离",
    "TC-SM-021": "Helm values 不强制 MySQL",
}

SENSITIVE_CONFIG_KEYS = {
    "AUTHORIZATION",
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "ACCESS_KEY",
    "SECRET_KEY",
    "CLIENT_SECRET",
    "PRIVATE_KEY",
    "COOKIE",
}
SENSITIVE_CONFIG_SUFFIXES = (
    "_PASSWORD",
    "_SECRET",
    "_TOKEN",
    "_API_KEY",
    "_ACCESS_KEY",
    "_SECRET_KEY",
    "_CLIENT_SECRET",
    "_PRIVATE_KEY",
    "_COOKIE",
)


def summarize_startup_log(text: str) -> dict[str, Any]:
    return {
        "ready": "RAGFlow server is ready" in text,
        "undefined_system_settings": ("system_settings" in text and "does not exist" in text),
        "relation_missing_occurrences": len(re.findall(r"relation .* does not exist", text, flags=re.IGNORECASE)),
        "traceback_occurrences": text.count("Traceback (most recent call last):"),
        "mysql_only_keyword_counts": {keyword: text.count(keyword) for keyword in MYSQL_ONLY_KEYWORDS},
    }


def is_sensitive_config_key(value: object) -> bool:
    key = str(value).strip().upper()
    return key in SENSITIVE_CONFIG_KEYS or key.endswith(SENSITIVE_CONFIG_SUFFIXES)


def _string_leaves(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, dict):
        return {item for child in value.values() for item in _string_leaves(child)}
    if isinstance(value, (list, tuple)):
        return {item for child in value for item in _string_leaves(child)}
    return set()


def redact_structured_secrets(value: Any, sensitive_values: set[str] | None = None) -> tuple[Any, set[str]]:
    collected = sensitive_values if sensitive_values is not None else set()
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if is_sensitive_config_key(key):
                collected.update(_string_leaves(child))
                result[key] = "<redacted>" if child not in (None, "") else child
            else:
                result[key], _ = redact_structured_secrets(child, collected)
        return result, collected
    if isinstance(value, list):
        result = []
        for child in value:
            if isinstance(child, str) and "=" in child:
                key, raw = child.split("=", 1)
                if is_sensitive_config_key(key):
                    if raw:
                        collected.add(raw)
                    result.append(f"{key}=<redacted>" if raw else child)
                    continue
            redacted, _ = redact_structured_secrets(child, collected)
            result.append(redacted)
        return result, collected
    if isinstance(value, tuple):
        redacted, _ = redact_structured_secrets(list(value), collected)
        return tuple(redacted), collected
    return value, collected


def _replace_sensitive_values(payload: bytes, values: set[str]) -> bytes:
    result = payload
    for value in sorted(values, key=len, reverse=True):
        if len(value) >= 4:
            result = result.replace(value.encode("utf-8"), b"<redacted>")
    return result


def redacted_json_bytes(value: Any) -> bytes:
    redacted, sensitive_values = redact_structured_secrets(value)
    payload = (json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return _replace_sensitive_values(payload, sensitive_values)


def redacted_yaml_bytes(documents: list[dict[str, Any]]) -> bytes:
    redacted, sensitive_values = redact_structured_secrets(documents)
    yaml = YAML()
    yaml.default_flow_style = False
    stream = io.StringIO()
    yaml.dump_all(redacted, stream)
    return _replace_sensitive_values(stream.getvalue().encode("utf-8"), sensitive_values)


def summarize_restart_log(text: str) -> dict[str, Any]:
    return {
        "ready": "RAGFlow server is ready" in text,
        "create_table_failure_occurrences": text.lower().count("create tables failed"),
        "critical_occurrences": len(re.findall(r"\bCRITICAL\b", text)),
        "traceback_occurrences": text.count("Traceback (most recent call last):"),
    }


def summarize_init_log(text: str) -> dict[str, Any]:
    return {
        "lock_failure_occurrences": len(
            re.findall(
                r"acquire (?:gaussdb|mysql) lock .* timeout",
                text,
                flags=re.IGNORECASE,
            )
        ),
        "create_table_failure_occurrences": text.lower().count("create tables failed"),
        "traceback_occurrences": text.count("Traceback (most recent call last):"),
        "transient_docstore_connection_failure": ("GaussDBConnectionError" in text and "server closed the connection unexpectedly" in text),
    }


def should_retry_initializer(result: dict[str, Any]) -> bool:
    summary = result["log_summary"]
    return (
        result.get("exit_code") != 0
        and summary.get("transient_docstore_connection_failure") is True
        and summary.get("lock_failure_occurrences") == 0
        and summary.get("create_table_failure_occurrences") == 0
    )


def concurrent_init_contract_ok(exit_codes: list[int], table_count: int, failure_count: int) -> bool:
    return exit_codes == [0, 0] and table_count == 40 and failure_count == 0


def content_hash_contract_ok(snapshot: dict[str, Any]) -> bool:
    return (
        snapshot.get("exists") is True
        and snapshot.get("data_type") in {"varchar", "character varying"}
        and snapshot.get("max_length") == 32
        and snapshot.get("is_nullable") == "YES"
        and int(snapshot.get("index_count", 0)) >= 1
    )


def row_counts_preserved(before: dict[str, int], after: dict[str, int]) -> bool:
    return before == after


def tenant_llm_migration_contract_ok(snapshot: dict[str, Any], group: str) -> bool:
    common = (
        snapshot.get("id_exists") is True
        and snapshot.get("temp_id_exists") is False
        and snapshot.get("primary_columns") == ["id"]
        and snapshot.get("composite_unique") is True
        and snapshot.get("fixture_count") == 1
        and snapshot.get("fixture_id_present") is True
    )
    if group == "control":
        return common and snapshot.get("auto_increment") is True
    if group == "experiment":
        return common and snapshot.get("sequence_exists") is True and snapshot.get("default_has_nextval") is True
    raise ValueError("unknown group")


def tenant_llm_fixture_values() -> dict[str, Any]:
    return {
        "tenant_id": "fr_sm005_tenant",
        "llm_factory": "OpenAI",
        "model_type": "chat",
        "llm_name": "fr-sm005-model",
        "api_key": "test-only-key",
        "max_tokens": 8192,
        "used_tokens": 0,
        "status": "1",
    }


def email_unique_contract_ok(
    *,
    unique_index_count: int,
    first_api_code: int,
    duplicate_api_code: int,
    database_row_count: int,
) -> bool:
    return unique_index_count >= 1 and first_api_code == 0 and duplicate_api_code != 0 and database_row_count == 1


def legacy_indexes_removed(snapshot: dict[str, bool]) -> bool:
    return set(snapshot) == set(LEGACY_INDEXES) and not any(snapshot.values())


def empty_string_contract_ok(group: str, result: dict[str, Any]) -> bool:
    if group == "control":
        return result.get("insert_succeeded") is True and result.get("stored_is_null") is False and result.get("stored_length") == 0
    if group == "experiment":
        return result.get("sql_compatibility") in {"A", "ORA"} and result.get("insert_succeeded") is False and result.get("sqlstate") == "23502"
    raise ValueError("unknown group")


def nullable_field_contract_ok(group: str, snapshot: dict[str, str]) -> bool:
    if set(snapshot) != set(EMPTY_STRING_FIELDS):
        return False
    if group == "control":
        return all(value in {"YES", "NO"} for value in snapshot.values())
    if group == "experiment":
        return all(value == "YES" for value in snapshot.values())
    raise ValueError("unknown group")


def orm_empty_string_roundtrip_contract_ok(group: str, result: dict[str, Any]) -> bool:
    common = (
        result.get("registration_code") == 0
        and result.get("tenant_api_value") == ""
        and result.get("dataset_create_code") == 0
        and result.get("dataset_get_code") == 0
        and result.get("dataset_api_value") == ""
        and result.get("dataset_delete_code") == 0
        and result.get("user_cleanup_complete") is True
    )
    storages = (result.get("tenant_storage", {}), result.get("dataset_storage", {}))
    if group == "control":
        return common and all(item.get("python_value") == "" and item.get("is_null") is False and item.get("length") == 0 for item in storages)
    if group == "experiment":
        return common and all(item.get("python_value") is None and item.get("is_null") is True and item.get("length") is None for item in storages)
    raise ValueError("unknown group")


def empty_string_query_contract_ok(group: str, compiled: dict[str, Any]) -> bool:
    sql = " ".join(str(compiled.get("sql", "")).upper().split())
    params = list(compiled.get("params", []))
    if group == "control":
        return "EMBD_ID" in sql and " = " in sql and "IS NULL" not in sql and "LENGTH(" not in sql and params == [""]
    if group == "experiment":
        return "EMBD_ID" in sql and "IS NULL" in sql and "LENGTH(" in sql and "" not in params and 0 in params
    raise ValueError("unknown group")


def mysql_migration_guard_contract_ok(group: str, probe: dict[str, Any], log_counts: dict[str, int]) -> bool:
    common = probe.get("exit_code") == 0 and set(log_counts) == {
        "mysql_migration",
        "AUTO_INCREMENT",
        "ON DUPLICATE",
        "SHOW PROCESSLIST",
    }
    if group == "control":
        return common and probe.get("migration_invocation_count") == 1
    if group == "experiment":
        return common and probe.get("migration_invocation_count") == 0 and probe.get("skip_message") is True and not any(log_counts.values())
    raise ValueError("unknown group")


def ddl_dialect_contract_ok(group: str, snapshot: dict[str, Any]) -> bool:
    if group == "control":
        return (
            snapshot.get("catalog") == "mysql_information_schema"
            and int(snapshot.get("auto_increment_column_count", 0)) > 0
            and int(snapshot.get("datetime_column_count", 0)) > 0
            and int(snapshot.get("longtext_column_count", 0)) > 0
        )
    if group == "experiment":
        return (
            snapshot.get("catalog") == "gaussdb_information_schema"
            and snapshot.get("sql_compatibility") in {"A", "ORA"}
            and int(snapshot.get("nextval_column_count", 0)) > 0
            and int(snapshot.get("timestamp_column_count", 0)) > 0
            and int(snapshot.get("text_column_count", 0)) > 0
            and snapshot.get("mysql_only_type_column_count") == 0
        )
    raise ValueError("unknown group")


def index_dialect_contract_ok(group: str, snapshot: dict[str, Any]) -> bool:
    if group == "control":
        return snapshot.get("catalog") == "mysql_information_schema.statistics" and int(snapshot.get("index_count", 0)) > 0 and int(snapshot.get("show_create_backtick_count", 0)) > 0
    if group == "experiment":
        return (
            snapshot.get("catalog") == "pg_indexes"
            and int(snapshot.get("index_count", 0)) > 0
            and snapshot.get("backtick_occurrences") == 0
            and snapshot.get("mysql_only_syntax_occurrences") == 0
            and snapshot.get("invalid_index_definition_count") == 0
        )
    raise ValueError("unknown group")


def database_alias_contract_ok(observed: dict[str, str]) -> bool:
    return observed == {
        "GaussDB": "gaussdb",
        "gauss": "gaussdb",
        "GAUSSDB": "gaussdb",
        "gaussdb": "gaussdb",
        "postgresql": "postgres",
    }


def schema_validation_contract_ok(observed: dict[str, dict[str, Any]]) -> bool:
    expected_accepted = {
        "ragflow_meta": "ragflow_meta",
        "public": "public",
        "_test_schema": "_test_schema",
        "": "public",
    }
    expected_rejected = {"test; DROP TABLE", "test-schema", "123schema"}
    if set(observed) != set(expected_accepted) | expected_rejected:
        return False
    if any(observed[value].get("accepted") is not True or observed[value].get("normalized") != normalized for value, normalized in expected_accepted.items()):
        return False
    return all(observed[value].get("accepted") is False and observed[value].get("error_type") == "ValueError" for value in expected_rejected)


def config_isolation_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    fake_isolated = observed.get("fake_metadata") == {"host": "meta-host", "name": "meta_db"} and observed.get("fake_docstore") == {"host": "doc-host", "database": "doc_db"}
    if group == "control":
        return (
            fake_isolated
            and observed.get("runtime_database_type") == "mysql"
            and observed.get("runtime_doc_engine") == "infinity"
            and observed.get("gauss_metadata_env_did_not_override_mysql") is True
            and observed.get("actual_selection") == {"metadata": "mysql", "docstore": "infinity"}
        )
    if group == "experiment":
        return (
            fake_isolated
            and observed.get("runtime_database_type") == "gaussdb"
            and observed.get("runtime_doc_engine") == "gaussdb"
            and observed.get("actual_selection") == {"metadata": "gaussdb", "docstore": "gaussdb"}
            and observed.get("actual_metadata_matches_environment") is True
            and observed.get("actual_docstore_matches_service_config") is True
            and observed.get("actual_targets_distinct") is True
        )
    raise ValueError("unknown group")


def admin_service_display_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("metadata_service_count") == 1
        and observed.get("retrieval_service_count") == 1
        and observed.get("metadata_matches_expected") is True
        and observed.get("retrieval_matches_expected") is True
        and observed.get("credential_exposure_count") == 0
    )
    if group == "control":
        return common and observed.get("metadata_type") == "mysql" and observed.get("retrieval_type") == "infinity"
    if group == "experiment":
        return common and observed.get("metadata_type") == "gaussdb" and observed.get("retrieval_type") == "gaussdb"
    raise ValueError("unknown group")


def admin_metadata_health_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("detail_http_status") == 200 and observed.get("detail_code") == 0 and observed.get("detail_status") == "alive"
    sql = observed.get("executed_sql")
    if group == "control":
        return common and sql == ["SHOW PROCESSLIST;"]
    if group == "experiment":
        return common and sql == ["SELECT 1;"]
    raise ValueError("unknown group")


def compose_profile_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("services_exit_code") == 0
        and observed.get("config_exit_code") == 0
        and observed.get("ragflow_cpu_present") is True
        and observed.get("mysql_dependency_required") is False
        and observed.get("application_environment_matches") is True
    )
    if group == "control":
        return common and observed.get("mysql_present") is True and observed.get("infinity_present") is True
    if group == "experiment":
        return common and observed.get("mysql_present") is False and observed.get("infinity_present") is False and observed.get("gauss_metadata_environment_present") is True
    raise ValueError("unknown group")


def helm_metadata_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("template_exit_code") == 0 and observed.get("environment_config_resource_count") == 1 and int(observed.get("ragflow_workload_count", 0)) > 0
    if group == "control":
        return common and observed.get("db_type") == "mysql" and int(observed.get("mysql_resource_count", 0)) > 0 and observed.get("mysql_credential_keys_present") is True
    if group == "experiment":
        return (
            common
            and observed.get("db_type") == "gaussdb"
            and observed.get("mysql_resource_count") == 0
            and observed.get("mysql_credential_keys_present") is False
            and observed.get("gauss_metadata_keys_present") is True
            and observed.get("gauss_metadata_values_match") is True
        )
    raise ValueError("unknown group")


def catalog_counts_equal(before: dict[str, int], after: dict[str, int]) -> bool:
    return before == after and set(before) == {"table_count", "index_count"}


def restart_contract_ok(before: dict[str, int], after: dict[str, int], restart: dict[str, Any]) -> bool:
    log_summary = restart["log_summary"]
    return (
        before["table_count"] == 40
        and catalog_counts_equal(before, after)
        and restart["stop_confirmed"]
        and restart["start_confirmed"]
        and restart["currently_alive"]
        and restart["ready"]
        and log_summary["ready"]
        and log_summary["create_table_failure_occurrences"] == 0
        and log_summary["critical_occurrences"] == 0
        and log_summary["traceback_occurrences"] == 0
    )


def case_database_name(case_id: str, group: str) -> str:
    if group not in GROUP_ORDER:
        raise ValueError("unknown group")
    match = re.fullmatch(r"TC-SM-(\d{3})", case_id)
    if not match:
        raise ValueError("invalid startup migration case id")
    return f"{RESOURCE_PREFIX}_{group}_sm{match.group(1)}"


def assert_case_resource(name: str) -> str:
    if not CASE_RESOURCE.fullmatch(name) or not IDENTIFIER.fullmatch(name):
        raise ValueError(f"unsafe case resource: {name!r}")
    return name


def table_contract_ok(table_count: int, table_names: set[str]) -> bool:
    return table_count == 40 and REQUIRED_CORE_TABLES.issubset(table_names)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_tiktoken_cache(root: Path, *, candidates: tuple[Path, ...] | None = None) -> Path:
    target = root / TIKTOKEN_CACHE_KEY
    if target.is_file() and _sha256(target) == TIKTOKEN_ENCODING_SHA256:
        target.chmod(0o600)
        return target

    sources = candidates or (
        PROJECT_ROOT / "ragflow_deps" / "cl100k_base.tiktoken",
        Path(tempfile.gettempdir()) / "data-gym-cache" / TIKTOKEN_CACHE_KEY,
    )
    for source in sources:
        if not source.is_file() or _sha256(source) != TIKTOKEN_ENCODING_SHA256:
            continue
        shutil.copyfile(source, target)
        target.chmod(0o600)
        if _sha256(target) == TIKTOKEN_ENCODING_SHA256:
            return target
        target.unlink(missing_ok=True)

    raise RuntimeError("validated cl100k_base.tiktoken dependency is unavailable; run ragflow_deps/download_deps.py or prewarm the tiktoken cache")


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected mapping in {path.name}")
    return payload


def runtime_http_port(config: dict[str, Any], service: str) -> int:
    if service not in {"ragflow", "admin"}:
        raise ValueError("unknown HTTP service")
    port = int(config[service]["http_port"])
    if not 1024 <= port <= 65535:
        raise ValueError("invalid runtime HTTP port")
    return port


def dotenv_selected_values(text: str, keys: set[str]) -> dict[str, str]:
    selected = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in keys:
            selected[key.strip()] = value.strip()
    return selected


def _parse_gauss_info(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and ":" in stripped:
            key, value = stripped.split(":", 1)
            result[key.strip()] = value.strip()
    if set(result) != {"host", "port", "dbname", "user", "password"}:
        raise ValueError("invalid gauss info")
    return result


def _case_gauss_connection(schema: str, *, read_only: bool, autocommit: bool = False):
    import psycopg2

    schema = assert_case_resource(schema)
    info = _parse_gauss_info(PROJECT_ROOT / "docs" / "administrator" / "configurations" / "gaussdb_info.md")
    if info["dbname"] != AUTHORIZED_GAUSS_DATABASE:
        raise ValueError("startup cases are restricted to the authorized zws_test2 database")
    connection = psycopg2.connect(
        host=info["host"],
        port=int(info["port"]),
        dbname=AUTHORIZED_GAUSS_DATABASE,
        user=info["user"],
        password=info["password"],
        connect_timeout=5,
        options=(f"-c default_transaction_read_only={'on' if read_only else 'off'} -c search_path={schema}"),
    )
    connection.autocommit = autocommit
    _set_gauss_search_path(connection, schema)
    return connection


def _set_gauss_search_path(connection, schema: str) -> None:
    if not IDENTIFIER.fullmatch(schema):
        raise ValueError("invalid GaussDB schema identifier")
    with connection.cursor() as cursor:
        # libpq 启动 options 只在 CN 生效；分布式用例的未限定表名还需要连接后
        # 显式 SET，才能让同一 search_path 下发到 DN。
        cursor.execute(f'SET search_path TO "{schema}"')


def _control_catalog_snapshot() -> dict[str, Any]:
    import pymysql

    config = _load_yaml(RUNTIME_DIR / "control" / "conf" / "service_conf.yaml")
    mysql = config["mysql"]
    connection = pymysql.connect(
        host=mysql["host"],
        port=int(mysql["port"]),
        user=mysql["user"],
        password=mysql["password"],
        database=mysql["name"],
        connect_timeout=5,
        read_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                (mysql["name"],),
            )
            tables = {row[0] for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) FROM `user` WHERE is_superuser=1")
            superusers = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM tenant")
            tenants = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM llm_factories")
            factories = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM system_settings")
            settings_rows = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "table_count": len(tables),
        "required_tables_present": sorted(REQUIRED_CORE_TABLES.intersection(tables)),
        "table_contract_ok": table_contract_ok(len(tables), tables),
        "default_data": {
            "superuser_count": superusers,
            "tenant_count": tenants,
            "llm_factory_count": factories,
            "system_settings_count": settings_rows,
        },
    }


def _experiment_gauss_schema_snapshot() -> dict[str, Any]:
    import psycopg2

    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = environments["experiment"]
    schema = environment["GAUSSDB_METADATA_SCHEMA"]
    connection = psycopg2.connect(
        host=environment["GAUSSDB_METADATA_HOST"],
        port=int(environment["GAUSSDB_METADATA_PORT"]),
        dbname=environment["GAUSSDB_METADATA_DBNAME"],
        user=environment["GAUSSDB_METADATA_USER"],
        password=environment["GAUSSDB_METADATA_PASSWORD"],
        connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c search_path={schema}",
    )
    _set_gauss_search_path(connection, schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW sql_compatibility")
            compatibility = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                (schema,),
            )
            target_tables = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'")
            public_tables = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('user','tenant','system_settings')")
            public_core_conflicts = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "database": environment["GAUSSDB_METADATA_DBNAME"],
        "metadata_schema": schema,
        "sql_compatibility": compatibility,
        "target_schema_table_count": target_tables,
        "public_table_count": public_tables,
        "public_core_conflicting_table_count": public_core_conflicts,
    }


def sm001_experiment_findings(experiment_ok: bool) -> list[dict[str, str]]:
    if experiment_ok:
        return []
    return [
        {
            "id": "ENV-DEFECT-001",
            "summary": ("current experiment metadata schema did not satisfy the cold-start migration contract"),
            "code_location": "api/db/db_models.py:init_database_tables",
        }
    ]


def _catalog_counts(group: str) -> dict[str, int]:
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")
        mysql = config["mysql"]
        connection = pymysql.connect(
            host=mysql["host"],
            port=int(mysql["port"]),
            user=mysql["user"],
            password=mysql["password"],
            database=mysql["name"],
            connect_timeout=5,
            read_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                    (mysql["name"],),
                )
                table_count = int(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT COUNT(DISTINCT CONCAT(table_name, '/', index_name)) FROM information_schema.statistics WHERE table_schema=%s",
                    (mysql["name"],),
                )
                index_count = int(cursor.fetchone()[0])
        finally:
            connection.close()
        return {"table_count": table_count, "index_count": index_count}

    if group != "experiment":
        raise ValueError("unknown group")
    import psycopg2

    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = environments[group]
    schema = environment["GAUSSDB_METADATA_SCHEMA"]
    connection = psycopg2.connect(
        host=environment["GAUSSDB_METADATA_HOST"],
        port=int(environment["GAUSSDB_METADATA_PORT"]),
        dbname=environment["GAUSSDB_METADATA_DBNAME"],
        user=environment["GAUSSDB_METADATA_USER"],
        password=environment["GAUSSDB_METADATA_PASSWORD"],
        connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c search_path={schema}",
    )
    _set_gauss_search_path(connection, schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                (schema,),
            )
            table_count = int(cursor.fetchone()[0])
            cursor.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname=%s", (schema,))
            index_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {"table_count": table_count, "index_count": index_count}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    yaml = YAML()
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump(payload, stream)
    path.chmod(0o600)


def _prepare_case_database(group: str, database: str) -> dict[str, Any]:
    database = assert_case_resource(database)
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                (database,),
            )
            table_count = int(cursor.fetchone()[0])
        connection.close()
        return {"created": True, "initial_table_count": table_count}

    if group != "experiment":
        raise ValueError("unknown group")
    connection = _case_gauss_connection(database, read_only=False, autocommit=True)
    with connection.cursor() as cursor:
        cursor.execute(f"DROP SCHEMA IF EXISTS {database} CASCADE")
        cursor.execute(f"CREATE SCHEMA {database}")
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
            (database,),
        )
        table_count = int(cursor.fetchone()[0])
        cursor.execute("SHOW sql_compatibility")
        compatibility = cursor.fetchone()[0]
    connection.close()
    return {
        "created": True,
        "database": AUTHORIZED_GAUSS_DATABASE,
        "schema": database,
        "initial_table_count": table_count,
        "sql_compatibility": compatibility,
    }


def _drop_case_database(group: str, database: str) -> bool:
    database = assert_case_resource(database)
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{database}`")
        connection.close()
        return True

    connection = _case_gauss_connection(database, read_only=False, autocommit=True)
    with connection.cursor() as cursor:
        cursor.execute(f"DROP SCHEMA IF EXISTS {database} CASCADE")
    connection.close()
    return True


def _prepare_case_runtime(case_id: str, group: str, database: str, *, api_port: int | None = None) -> tuple[Path, dict[str, str]]:
    root = RUNTIME_DIR / "cases" / case_id / group
    expected_parent = RUNTIME_DIR / "cases" / case_id
    if root.exists():
        if root.parent != expected_parent:
            raise ValueError("unsafe case runtime path")
        shutil.rmtree(root)
    root.mkdir(parents=True, mode=0o700)
    shutil.copytree(RUNTIME_DIR / group / "conf", root / "conf")
    config_path = root / "conf" / "service_conf.yaml"
    config = _load_yaml(config_path)
    if api_port is not None:
        if not 1024 <= api_port <= 65535:
            raise ValueError("invalid case API port")
        config["ragflow"]["http_port"] = api_port
        config["redis"]["db"] = 13 if group == "control" else 14
    if group == "control":
        config["mysql"]["name"] = database
    elif api_port is None:
        config["ragflow"]["http_port"] = 0
    _write_yaml(config_path, config)
    for asset in ("agent", "rag", "ragflow_deps"):
        (root / asset).symlink_to(PROJECT_ROOT / asset, target_is_directory=True)
    (root / "logs").mkdir(mode=0o700)
    seed_tiktoken_cache(root)

    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = copy.deepcopy(environments[group])
    environment.update(
        {
            "RAG_PROJECT_BASE": str(root),
            "RAGFLOW_SECRET_KEY": secrets.token_urlsafe(48),
            "PYTHONPATH": str(PROJECT_ROOT),
            "PYTHONUNBUFFERED": "1",
            "NLTK_DATA": str(PROJECT_ROOT / "nltk_data"),
        }
    )
    if group == "experiment":
        if environment["GAUSSDB_METADATA_DBNAME"] != AUTHORIZED_GAUSS_DATABASE:
            raise ValueError("experiment runtime escaped zws_test2")
        environment["GAUSSDB_METADATA_SCHEMA"] = database
    for key in (
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ):
        environment.pop(key, None)
    return root, {str(key): str(value) for key, value in environment.items()}


def _case_catalog_snapshot(group: str, database: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                (database,),
            )
            tables = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT COUNT(DISTINCT CONCAT(table_name, '/', index_name)) FROM information_schema.statistics WHERE table_schema=%s",
                (database,),
            )
            index_count = int(cursor.fetchone()[0])
        connection.close()
    else:
        connection = _case_gauss_connection(database, read_only=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                (database,),
            )
            tables = {row[0] for row in cursor.fetchall()}
            cursor.execute(
                "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=%s",
                (database,),
            )
            index_count = int(cursor.fetchone()[0])
        connection.close()
    return {
        "table_count": len(tables),
        "index_count": index_count,
        "required_tables_present": sorted(REQUIRED_CORE_TABLES.intersection(tables)),
        "table_contract_ok": table_contract_ok(len(tables), tables),
    }


def _run_concurrent_initializers(case_id: str, group: str, root: Path, environment: dict[str, str]) -> dict[str, Any]:
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_paths = [raw_dir / f"{case_id}_{group}_initializer_{index}.log" for index in (1, 2)]
    command = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        "-c",
        ("from common import settings; settings.init_settings(); from api.db.db_models import init_database_tables; init_database_tables()"),
    ]
    streams = [path.open("wb", buffering=0) for path in log_paths]
    started = time.monotonic()
    processes = [
        subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        for stream in streams
    ]
    exit_codes = []
    try:
        for process in processes:
            try:
                exit_codes.append(process.wait(timeout=180))
            except subprocess.TimeoutExpired:
                process.kill()
                exit_codes.append(process.wait(timeout=10))
    finally:
        for stream in streams:
            stream.close()
    elapsed = round(time.monotonic() - started, 3)
    for path in log_paths:
        path.chmod(0o600)
    summaries = [summarize_init_log(path.read_text(encoding="utf-8", errors="replace")) for path in log_paths]
    return {
        "exit_codes": exit_codes,
        "elapsed_seconds": elapsed,
        "process_ids": [process.pid for process in processes],
        "log_summaries": summaries,
        "raw_log_sha256": [_sha256(path) for path in log_paths],
    }


def _run_single_initializer(
    case_id: str,
    group: str,
    environment: dict[str, str],
    label: str,
    *,
    include_default_data: bool = False,
) -> dict[str, Any]:
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = raw_dir / f"{case_id}_{group}_{label}.log"
    source = "from common import settings; settings.init_settings(); from api.db.db_models import init_database_tables; init_database_tables();"
    if include_default_data:
        source += " from api.db.init_data import init_web_data, init_superuser; init_web_data(); init_superuser()"
    started = time.monotonic()
    with log_path.open("wb", buffering=0) as stream:
        completed = subprocess.run(
            [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-c", source],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=180,
            check=False,
        )
    log_path.chmod(0o600)
    summary = summarize_init_log(log_path.read_text(encoding="utf-8", errors="replace"))
    return {
        "exit_code": completed.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "log_summary": summary,
        "raw_log_sha256": _sha256(log_path),
    }


def _run_initializer_with_transient_retry(
    case_id: str,
    group: str,
    environment: dict[str, str],
    label: str,
    *,
    include_default_data: bool = False,
) -> dict[str, Any]:
    first = _run_single_initializer(
        case_id,
        group,
        environment,
        f"{label}_attempt1",
        include_default_data=include_default_data,
    )
    attempts = [first]
    if should_retry_initializer(first):
        second = _run_single_initializer(
            case_id,
            group,
            environment,
            f"{label}_attempt2",
            include_default_data=include_default_data,
        )
        attempts.append(second)
    final = attempts[-1]
    return {
        **final,
        "retried_transient_docstore_failure": len(attempts) == 2,
        "attempts": attempts,
    }


def _migration_row_counts(group: str, database: str) -> dict[str, int]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
    else:
        connection = _case_gauss_connection(database, read_only=True)
    try:
        with connection.cursor() as cursor:
            result = {}
            for table in ("user", "tenant", "document"):
                quoted = f"`{table}`" if group == "control" else f'"{table}"'
                cursor.execute(f"SELECT COUNT(*) FROM {quoted}")
                result[table] = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return result


def _content_hash_snapshot(group: str, database: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type, character_maximum_length, is_nullable FROM information_schema.columns WHERE table_schema=%s AND table_name='document' AND column_name='content_hash'",
                (database,),
            )
            row = cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(DISTINCT index_name) FROM information_schema.statistics WHERE table_schema=%s AND table_name='document' AND column_name='content_hash'",
                (database,),
            )
            index_count = int(cursor.fetchone()[0])
        connection.close()
    else:
        connection = _case_gauss_connection(database, read_only=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type, character_maximum_length, is_nullable FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='document' AND column_name='content_hash'"
            )
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() AND tablename='document' AND indexdef ILIKE '%content_hash%'")
            index_count = int(cursor.fetchone()[0])
        connection.close()
    return {
        "exists": row is not None,
        "data_type": row[0] if row else None,
        "max_length": int(row[1]) if row and row[1] is not None else None,
        "is_nullable": row[2] if row else None,
        "index_count": index_count,
    }


def _drop_content_hash(group: str, database: str) -> bool:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE document DROP COLUMN content_hash")
        connection.close()
    else:
        connection = _case_gauss_connection(database, read_only=False, autocommit=True)
        with connection.cursor() as cursor:
            cursor.execute("ALTER TABLE document DROP COLUMN content_hash CASCADE")
        connection.close()
    return True


def _tenant_llm_snapshot(group: str, database: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, column_default, extra FROM information_schema.columns WHERE table_schema=%s AND table_name='tenant_llm' AND column_name IN ('id','temp_id')",
                (database,),
            )
            columns = {row[0]: {"default": row[1], "extra": row[2]} for row in cursor.fetchall()}
            cursor.execute(
                "SELECT column_name FROM information_schema.key_column_usage WHERE table_schema=%s AND table_name='tenant_llm' AND constraint_name='PRIMARY' ORDER BY ordinal_position",
                (database,),
            )
            primary_columns = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                "SELECT index_name, non_unique, column_name, seq_in_index FROM information_schema.statistics WHERE table_schema=%s AND table_name='tenant_llm' ORDER BY index_name, seq_in_index",
                (database,),
            )
            indexes: dict[str, dict[str, Any]] = {}
            for name, non_unique, column, _seq in cursor.fetchall():
                item = indexes.setdefault(name, {"non_unique": int(non_unique), "columns": []})
                item["columns"].append(column)
            cursor.execute("SELECT COUNT(*) FROM tenant_llm WHERE tenant_id='fr_sm005_tenant'")
            fixture_count = int(cursor.fetchone()[0])
            fixture_id_present = False
            if "id" in columns:
                cursor.execute("SELECT COUNT(*) FROM tenant_llm WHERE tenant_id='fr_sm005_tenant' AND id IS NOT NULL")
                fixture_id_present = int(cursor.fetchone()[0]) == 1
        connection.close()
        composite_unique = any(name != "PRIMARY" and item["non_unique"] == 0 and item["columns"] == ["tenant_id", "llm_factory", "llm_name"] for name, item in indexes.items())
        id_default = columns.get("id", {}).get("default")
        return {
            "id_exists": "id" in columns,
            "temp_id_exists": "temp_id" in columns,
            "primary_columns": primary_columns,
            "composite_unique": composite_unique,
            "fixture_count": fixture_count,
            "fixture_id_present": fixture_id_present,
            "auto_increment": "auto_increment" in str(columns.get("id", {}).get("extra", "")).lower(),
            "sequence_exists": False,
            "default_has_nextval": "nextval" in str(id_default).lower(),
        }

    connection = _case_gauss_connection(database, read_only=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name, column_default FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='tenant_llm' AND column_name IN ('id','temp_id')")
        columns = {row[0]: row[1] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT tc.constraint_name, tc.constraint_type, kcu.column_name, "
            "kcu.ordinal_position FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_schema=kcu.constraint_schema "
            "AND tc.constraint_name=kcu.constraint_name "
            "AND tc.table_name=kcu.table_name "
            "WHERE tc.table_schema=current_schema() AND tc.table_name='tenant_llm' "
            "AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE') "
            "ORDER BY tc.constraint_name, kcu.ordinal_position"
        )
        constraints: dict[str, dict[str, Any]] = {}
        for name, kind, column, _position in cursor.fetchall():
            item = constraints.setdefault(name, {"type": kind, "columns": []})
            item["columns"].append(column)
        cursor.execute("SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relkind='S' AND c.relname='tenant_llm_id_seq'")
        sequence_exists = int(cursor.fetchone()[0]) == 1
        cursor.execute("SELECT COUNT(*) FROM tenant_llm WHERE tenant_id='fr_sm005_tenant'")
        fixture_count = int(cursor.fetchone()[0])
        fixture_id_present = False
        if "id" in columns:
            cursor.execute("SELECT COUNT(*) FROM tenant_llm WHERE tenant_id='fr_sm005_tenant' AND id IS NOT NULL")
            fixture_id_present = int(cursor.fetchone()[0]) == 1
    connection.close()
    primary_columns = next(
        (item["columns"] for item in constraints.values() if item["type"] == "PRIMARY KEY"),
        [],
    )
    composite_unique = any(item["type"] == "UNIQUE" and item["columns"] == ["tenant_id", "llm_factory", "llm_name"] for item in constraints.values())
    return {
        "id_exists": "id" in columns,
        "temp_id_exists": "temp_id" in columns,
        "primary_columns": primary_columns,
        "composite_unique": composite_unique,
        "fixture_count": fixture_count,
        "fixture_id_present": fixture_id_present,
        "auto_increment": False,
        "sequence_exists": sequence_exists,
        "default_has_nextval": "nextval" in str(columns.get("id", "")).lower(),
    }


def _mutate_tenant_llm_to_legacy(group: str, database: str) -> dict[str, Any]:
    fixture = tenant_llm_fixture_values()
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO tenant_llm (tenant_id,llm_factory,model_type,llm_name,api_key,max_tokens,used_tokens,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(fixture.values()),
            )
            cursor.execute(
                "SELECT index_name, GROUP_CONCAT(column_name ORDER BY seq_in_index) "
                "FROM information_schema.statistics WHERE table_schema=%s "
                "AND table_name='tenant_llm' AND non_unique=0 "
                "GROUP BY index_name",
                (database,),
            )
            unique_indexes = [name for name, columns in cursor.fetchall() if name != "PRIMARY" and columns == "tenant_id,llm_factory,llm_name"]
            for index_name in unique_indexes:
                if not IDENTIFIER.fullmatch(index_name):
                    raise ValueError("unsafe generated index name")
                cursor.execute(f"ALTER TABLE tenant_llm DROP INDEX `{index_name}`")
            cursor.execute("ALTER TABLE tenant_llm MODIFY COLUMN id INT NOT NULL")
            cursor.execute("ALTER TABLE tenant_llm DROP PRIMARY KEY")
            cursor.execute("ALTER TABLE tenant_llm DROP COLUMN id")
            cursor.execute("ALTER TABLE tenant_llm ADD PRIMARY KEY (tenant_id,llm_factory,llm_name)")
        connection.close()
        return {"fixture_inserted": True, "dropped_unique_count": len(unique_indexes)}

    connection = _case_gauss_connection(database, read_only=False, autocommit=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO tenant_llm (tenant_id,llm_factory,model_type,llm_name,api_key,max_tokens,used_tokens,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            tuple(fixture.values()),
        )
        cursor.execute(
            "SELECT tc.constraint_name, tc.constraint_type, kcu.column_name, "
            "kcu.ordinal_position FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_schema=kcu.constraint_schema "
            "AND tc.constraint_name=kcu.constraint_name "
            "AND tc.table_name=kcu.table_name "
            "WHERE tc.table_schema=current_schema() AND tc.table_name='tenant_llm' "
            "AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE') "
            "ORDER BY tc.constraint_name,kcu.ordinal_position"
        )
        constraints: dict[str, dict[str, Any]] = {}
        for name, kind, column, _position in cursor.fetchall():
            item = constraints.setdefault(name, {"type": kind, "columns": []})
            item["columns"].append(column)
        dropped = []
        for name, item in constraints.items():
            if item["type"] == "PRIMARY KEY" or (item["type"] == "UNIQUE" and item["columns"] == ["tenant_id", "llm_factory", "llm_name"]):
                if not IDENTIFIER.fullmatch(name):
                    raise ValueError("unsafe generated constraint name")
                cursor.execute(f'ALTER TABLE tenant_llm DROP CONSTRAINT "{name}"')
                dropped.append(name)
        cursor.execute("ALTER TABLE tenant_llm DROP COLUMN id CASCADE")
        cursor.execute("DROP SEQUENCE IF EXISTS tenant_llm_id_seq")
        cursor.execute("ALTER TABLE tenant_llm ADD PRIMARY KEY (tenant_id,llm_factory,llm_name)")
    connection.close()
    return {"fixture_inserted": True, "dropped_constraint_count": len(dropped)}


def _email_unique_index_count(group: str, database: str) -> int:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(DISTINCT index_name) FROM information_schema.statistics WHERE table_schema=%s AND table_name='user' AND column_name='email' AND non_unique=0",
                (database,),
            )
            count = int(cursor.fetchone()[0])
        connection.close()
        return count

    connection = _case_gauss_connection(database, read_only=True)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM pg_indexes WHERE schemaname=current_schema() "
            "AND tablename='user' AND lower(indexdef) LIKE 'create unique index%' "
            "AND (lower(indexdef) LIKE '%(email)%' "
            "OR lower(indexdef) LIKE '%(\"email\")%')"
        )
        count = int(cursor.fetchone()[0])
    connection.close()
    return count


def _drop_email_unique_index(group: str, database: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT index_name FROM information_schema.statistics WHERE table_schema=%s AND table_name='user' AND column_name='email' AND non_unique=0 AND index_name<>'PRIMARY'",
                (database,),
            )
            names = [row[0] for row in cursor.fetchall()]
            for name in names:
                if not IDENTIFIER.fullmatch(name):
                    raise ValueError("unsafe generated email index name")
                cursor.execute(f"ALTER TABLE `user` DROP INDEX `{name}`")
        connection.close()
        return {"dropped_count": len(names)}

    connection = _case_gauss_connection(database, read_only=False, autocommit=True)
    dropped = []
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT tc.constraint_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_schema=kcu.constraint_schema "
            "AND tc.constraint_name=kcu.constraint_name "
            "AND tc.table_name=kcu.table_name "
            "WHERE tc.table_schema=current_schema() AND tc.table_name='user' "
            "AND tc.constraint_type='UNIQUE' AND kcu.column_name='email'"
        )
        for (name,) in cursor.fetchall():
            if not IDENTIFIER.fullmatch(name):
                raise ValueError("unsafe generated email constraint name")
            cursor.execute(f'ALTER TABLE "user" DROP CONSTRAINT "{name}"')
            dropped.append(name)
        cursor.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() "
            "AND tablename='user' AND lower(indexdef) LIKE 'create unique index%' "
            "AND (lower(indexdef) LIKE '%(email)%' "
            "OR lower(indexdef) LIKE '%(\"email\")%')"
        )
        for (name,) in cursor.fetchall():
            if not IDENTIFIER.fullmatch(name):
                raise ValueError("unsafe generated email index name")
            cursor.execute(f'DROP INDEX IF EXISTS "{name}"')
            dropped.append(name)
    connection.close()
    return {"dropped_count": len(dropped)}


def _encrypt_case_password(password: str, public_key_path: Path) -> str:
    import base64
    from Cryptodome.Cipher import PKCS1_v1_5
    from Cryptodome.PublicKey import RSA

    key = RSA.import_key(public_key_path.read_text(encoding="utf-8"), "Welcome")
    encoded = base64.b64encode(password.encode("utf-8"))
    return base64.b64encode(PKCS1_v1_5.new(key).encrypt(encoded)).decode("ascii")


def _flush_case_redis(root: Path) -> dict[str, int]:
    import redis

    config = _load_yaml(root / "conf" / "service_conf.yaml")["redis"]
    host, raw_port = str(config["host"]).rsplit(":", 1)
    client = redis.Redis(
        host=host,
        port=int(raw_port),
        db=int(config["db"]),
        username=config.get("username") or None,
        password=config.get("password") or None,
        socket_connect_timeout=5,
    )
    before = int(client.dbsize())
    client.flushdb(asynchronous=False)
    return {"before": before, "after": int(client.dbsize())}


def _launch_case_api(
    case_id: str,
    group: str,
    root: Path,
    environment: dict[str, str],
    port: int,
) -> tuple[subprocess.Popen, Path, float]:
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path = raw_dir / f"{case_id}_{group}_api.log"
    with log_path.open("wb", buffering=0) as stream:
        process = subprocess.Popen(
            [
                str(PROJECT_ROOT / ".venv" / "bin" / "python"),
                "api/ragflow_server.py",
                "--init-superuser",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    started = time.monotonic()
    deadline = started + 120
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("case API exited before readiness")
        try:
            response = requests.get(f"http://127.0.0.1:{port}/api/v1/system/ping", timeout=2)
            if response.status_code == 200 and response.text == "pong":
                log_path.chmod(0o600)
                return process, log_path, time.monotonic() - started
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise TimeoutError("case API readiness timeout")


def _stop_case_api(process: subprocess.Popen) -> bool:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    return process.poll() is not None


def _register_duplicate_email(group: str, root: Path, port: int) -> dict[str, Any]:
    email = f"sm006-{group}@fresh.invalid"
    encrypted = _encrypt_case_password("Sm006-case-password!", root / "conf" / "public.pem")
    payload = {
        "email": email,
        "nickname": f"SM006 {group}",
        "password": encrypted,
    }
    session = requests.Session()
    first = session.post(f"http://127.0.0.1:{port}/api/v1/users", json=payload, timeout=30)
    second_payload = {**payload, "nickname": f"SM006 duplicate {group}"}
    second = session.post(
        f"http://127.0.0.1:{port}/api/v1/users",
        json=second_payload,
        timeout=30,
    )
    first_body = first.json()
    second_body = second.json()
    return {
        "fixture_email": email,
        "first_http_status": first.status_code,
        "first_code": first_body.get("code"),
        "duplicate_http_status": second.status_code,
        "duplicate_code": second_body.get("code"),
    }


def _email_fixture_row_count(group: str, database: str, email: str) -> int:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
    else:
        connection = _case_gauss_connection(database, read_only=True)
    with connection.cursor() as cursor:
        quoted = "`user`" if group == "control" else '"user"'
        cursor.execute(f"SELECT COUNT(*) FROM {quoted} WHERE email=%s", (email,))
        count = int(cursor.fetchone()[0])
    connection.close()
    return count


def _legacy_index_presence(group: str, database: str) -> dict[str, bool]:
    names = tuple(LEGACY_INDEXES)
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT DISTINCT index_name FROM information_schema.statistics WHERE table_schema=%s AND index_name IN (%s,%s,%s)",
                (database, *names),
            )
            present = {row[0] for row in cursor.fetchall()}
        connection.close()
    else:
        connection = _case_gauss_connection(database, read_only=True)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname=current_schema() AND indexname IN (%s,%s,%s)",
                names,
            )
            present = {row[0] for row in cursor.fetchall()}
        connection.close()
    return {name: name in present for name in names}


def _create_legacy_indexes(group: str, database: str) -> dict[str, bool]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            for name, (table, columns) in LEGACY_INDEXES.items():
                column_sql = ",".join(f"`{column}`" for column in columns)
                cursor.execute(f"CREATE INDEX `{name}` ON `{table}` ({column_sql})")
        connection.close()
    else:
        connection = _case_gauss_connection(database, read_only=False, autocommit=True)
        with connection.cursor() as cursor:
            for name, (table, columns) in LEGACY_INDEXES.items():
                column_sql = ",".join(f'"{column}"' for column in columns)
                cursor.execute(f'CREATE INDEX "{name}" ON "{table}" ({column_sql})')
        connection.close()
    return _legacy_index_presence(group, database)


def _run_empty_string_probe(group: str, database: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        base = _load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=base["host"],
            port=int(base.get("port", 3306)),
            user=base["user"],
            password=base["password"],
            database=database,
            connect_timeout=5,
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE test_empty_string (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL)")
            cursor.execute("INSERT INTO test_empty_string (name) VALUES (%s)", ("",))
            cursor.execute("SELECT name IS NULL, LENGTH(name) FROM test_empty_string")
            stored_is_null, stored_length = cursor.fetchone()
            cursor.execute("DROP TABLE test_empty_string")
        connection.close()
        return {
            "insert_succeeded": True,
            "stored_is_null": bool(stored_is_null),
            "stored_length": int(stored_length),
            "sqlstate": None,
            "sql_compatibility": None,
            "table_cleaned": True,
        }

    import psycopg2

    connection = _case_gauss_connection(database, read_only=False)
    with connection.cursor() as cursor:
        cursor.execute("SHOW sql_compatibility")
        compatibility = cursor.fetchone()[0]
        cursor.execute("CREATE TABLE test_empty_string (id SERIAL PRIMARY KEY, name VARCHAR(255) NOT NULL)")
    connection.commit()
    insert_succeeded = False
    sqlstate = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO test_empty_string (name) VALUES (%s)", ("",))
        connection.commit()
        insert_succeeded = True
    except psycopg2.Error as exc:
        sqlstate = exc.pgcode
        connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM test_empty_string")
        row_count = int(cursor.fetchone()[0])
        cursor.execute("DROP TABLE test_empty_string")
    connection.commit()
    connection.close()
    return {
        "insert_succeeded": insert_succeeded,
        "stored_is_null": None,
        "stored_length": None,
        "sqlstate": sqlstate,
        "sql_compatibility": compatibility,
        "row_count_after_insert_attempt": row_count,
        "table_cleaned": True,
    }


def _nullable_field_snapshot(group: str) -> dict[str, str]:
    desired = set(EMPTY_STRING_FIELDS)
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / "control" / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name,column_name,is_nullable FROM information_schema.columns WHERE table_schema=%s",
                (config["name"],),
            )
            rows = cursor.fetchall()
        connection.close()
    else:
        import psycopg2

        environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
        environment = environments["experiment"]
        schema = environment["GAUSSDB_METADATA_SCHEMA"]
        connection = psycopg2.connect(
            host=environment["GAUSSDB_METADATA_HOST"],
            port=int(environment["GAUSSDB_METADATA_PORT"]),
            dbname=environment["GAUSSDB_METADATA_DBNAME"],
            user=environment["GAUSSDB_METADATA_USER"],
            password=environment["GAUSSDB_METADATA_PASSWORD"],
            connect_timeout=5,
            options=(f"-c default_transaction_read_only=on -c search_path={schema}"),
        )
        _set_gauss_search_path(connection, schema)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name,column_name,is_nullable FROM information_schema.columns WHERE table_schema=%s",
                (schema,),
            )
            rows = cursor.fetchall()
        connection.close()
    snapshot = {f"{table}.{column}": nullable for table, column, nullable in rows if f"{table}.{column}" in desired}
    return dict(sorted(snapshot.items()))


def _empty_embedding_storage_snapshot(group: str, user_id: str, dataset_id: str) -> dict[str, dict[str, Any]]:
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / "control" / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            read_timeout=10,
        )
        tenant_sql = "SELECT embd_id, embd_id IS NULL, CHAR_LENGTH(embd_id) FROM tenant WHERE id=%s"
        dataset_sql = "SELECT embd_id, embd_id IS NULL, CHAR_LENGTH(embd_id) FROM knowledgebase WHERE id=%s AND tenant_id=%s"
    elif group == "experiment":
        import psycopg2

        environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))["experiment"]
        schema = environment["GAUSSDB_METADATA_SCHEMA"]
        connection = psycopg2.connect(
            host=environment["GAUSSDB_METADATA_HOST"],
            port=int(environment["GAUSSDB_METADATA_PORT"]),
            dbname=environment["GAUSSDB_METADATA_DBNAME"],
            user=environment["GAUSSDB_METADATA_USER"],
            password=environment["GAUSSDB_METADATA_PASSWORD"],
            connect_timeout=5,
            options=(f"-c default_transaction_read_only=on -c search_path={schema}"),
        )
        _set_gauss_search_path(connection, schema)
        tenant_sql = "SELECT embd_id, embd_id IS NULL, LENGTH(embd_id) FROM tenant WHERE id=%s"
        dataset_sql = "SELECT embd_id, embd_id IS NULL, LENGTH(embd_id) FROM knowledgebase WHERE id=%s AND tenant_id=%s"
    else:
        raise ValueError("unknown group")

    try:
        with connection.cursor() as cursor:
            cursor.execute(tenant_sql, (user_id,))
            tenant_row = cursor.fetchone()
            cursor.execute(dataset_sql, (dataset_id, user_id))
            dataset_row = cursor.fetchone()
    finally:
        connection.close()
    if tenant_row is None or dataset_row is None:
        raise RuntimeError("empty embedding fixture row is missing")

    def convert(row: tuple[Any, Any, Any]) -> dict[str, Any]:
        return {
            "python_value": row[0],
            "is_null": bool(row[1]),
            "length": int(row[2]) if row[2] is not None else None,
        }

    return {
        "tenant_storage": convert(tenant_row),
        "dataset_storage": convert(dataset_row),
    }


def _empty_embedding_cleanup_counts(group: str, email: str, user_id: str | None, dataset_id: str | None) -> dict[str, int]:
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / "control" / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            read_timeout=10,
        )
        user_table = "`user`"
    elif group == "experiment":
        import psycopg2

        environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))["experiment"]
        schema = environment["GAUSSDB_METADATA_SCHEMA"]
        connection = psycopg2.connect(
            host=environment["GAUSSDB_METADATA_HOST"],
            port=int(environment["GAUSSDB_METADATA_PORT"]),
            dbname=environment["GAUSSDB_METADATA_DBNAME"],
            user=environment["GAUSSDB_METADATA_USER"],
            password=environment["GAUSSDB_METADATA_PASSWORD"],
            connect_timeout=5,
            options=(f"-c default_transaction_read_only=on -c search_path={schema}"),
        )
        _set_gauss_search_path(connection, schema)
        user_table = '"user"'
    else:
        raise ValueError("unknown group")

    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {user_table} WHERE email=%s", (email,))
            user_count = int(cursor.fetchone()[0])
            if user_id:
                cursor.execute("SELECT COUNT(*) FROM tenant WHERE id=%s", (user_id,))
                tenant_count = int(cursor.fetchone()[0])
            else:
                tenant_count = 0
            if dataset_id:
                cursor.execute("SELECT COUNT(*) FROM knowledgebase WHERE id=%s", (dataset_id,))
                dataset_count = int(cursor.fetchone()[0])
            else:
                dataset_count = 0
    finally:
        connection.close()
    return {
        "user_count": user_count,
        "tenant_count": tenant_count,
        "dataset_count": dataset_count,
    }


def _admin_cleanup_empty_embedding_user(group: str, email: str) -> dict[str, Any]:
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = environments[group]
    root = RUNTIME_DIR / group
    base_url = f"http://127.0.0.1:{int(environment['ADMIN_PORT'])}/api/v1/admin"
    encrypted_password = _encrypt_case_password(environment["DEFAULT_SUPERUSER_PASSWORD"], root / "conf" / "public.pem")
    session = requests.Session()
    login = session.post(
        f"{base_url}/login",
        json={
            "email": environment["DEFAULT_SUPERUSER_EMAIL"],
            "password": encrypted_password,
        },
        timeout=30,
    )
    login_body = login.json()
    auth = login.headers.get("Authorization")
    if auth:
        session.headers["Authorization"] = f"Bearer {auth}"
    encoded_email = requests.utils.quote(email, safe="")
    disabled = session.put(
        f"{base_url}/users/{encoded_email}/activate",
        json={"activate_status": "off"},
        timeout=30,
    )
    disabled_body = disabled.json()
    deleted = session.delete(f"{base_url}/users/{encoded_email}", timeout=90)
    deleted_body = deleted.json()
    return {
        "admin_login_http_status": login.status_code,
        "admin_login_code": login_body.get("code"),
        "admin_auth_present": bool(auth),
        "disable_http_status": disabled.status_code,
        "disable_code": disabled_body.get("code"),
        "delete_http_status": deleted.status_code,
        "delete_code": deleted_body.get("code"),
    }


def _open_admin_session(group: str) -> tuple[requests.Session, str, dict[str, Any]]:
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = environments[group]
    root = RUNTIME_DIR / group
    base_url = f"http://127.0.0.1:{int(environment['ADMIN_PORT'])}/api/v1/admin"
    session = requests.Session()
    response = session.post(
        f"{base_url}/login",
        json={
            "email": environment["DEFAULT_SUPERUSER_EMAIL"],
            "password": _encrypt_case_password(
                environment["DEFAULT_SUPERUSER_PASSWORD"],
                root / "conf" / "public.pem",
            ),
        },
        timeout=30,
    )
    body = response.json()
    auth = response.headers.get("Authorization")
    if response.status_code != 200 or body.get("code") != 0 or not auth:
        raise RuntimeError(f"{group} Admin login failed")
    session.headers["Authorization"] = f"Bearer {auth}"
    return (
        session,
        base_url,
        {
            "login_http_status": response.status_code,
            "login_code": body.get("code"),
            "admin_auth_present": True,
        },
    )


def _request_admin_services(group: str, case_id: str) -> tuple[requests.Session, str, dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    session, base_url, login_summary = _open_admin_session(group)
    response = session.get(f"{base_url}/services", timeout=90)
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_path = raw_dir / f"{case_id}_{group}_services.json"
    raw_path.write_bytes(response.content)
    raw_path.chmod(0o600)
    body = response.json()
    services = body.get("data") if isinstance(body.get("data"), list) else []
    request_summary = {
        **login_summary,
        "http_status": response.status_code,
        "code": body.get("code"),
        "raw_response_sha256": _sha256(raw_path),
    }
    return session, base_url, body, services, request_summary


def _known_admin_service_credentials(group: str) -> dict[str, list[str]]:
    config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")
    environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]
    gaussdb = config.get("gaussdb") or {}
    docstore = gaussdb.get("config", gaussdb)
    values: dict[str, list[Any]] = {
        "metadata": [environment.get("GAUSSDB_METADATA_PASSWORD") if group == "experiment" else (config.get("mysql") or {}).get("password")],
        "docstore": [docstore.get("password")],
        "redis": [(config.get("redis") or {}).get("password")],
        "object_store": [(config.get("minio") or {}).get("password")],
        "admin": [environment.get("DEFAULT_SUPERUSER_PASSWORD")],
    }
    return {category: [str(value) for value in candidates if value not in {None, ""}] for category, candidates in values.items()}


def _admin_service_display_probe(group: str) -> dict[str, Any]:
    _session, _base_url, body, services, request_summary = _request_admin_services(group, "TC-SM-018")
    metadata = [item for item in services if isinstance(item, dict) and isinstance(item.get("extra"), dict) and item["extra"].get("meta_type")]
    retrieval = [item for item in services if isinstance(item, dict) and isinstance(item.get("extra"), dict) and item["extra"].get("retrieval_type")]
    config = _load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")
    environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))[group]
    if group == "control":
        expected_meta = config["mysql"]
        expected_retrieval = config["infinity"]
        infinity_host, infinity_port = str(expected_retrieval["uri"]).rsplit(":", 1)
        metadata_matches = len(metadata) == 1 and (
            metadata[0].get("host") == expected_meta["host"] and int(metadata[0].get("port")) == int(expected_meta["port"]) and metadata[0]["extra"].get("username") == expected_meta["user"]
        )
        retrieval_matches = len(retrieval) == 1 and (
            retrieval[0].get("host") == infinity_host and int(retrieval[0].get("port")) == int(infinity_port) and retrieval[0]["extra"].get("db_name") == expected_retrieval["db_name"]
        )
    else:
        gaussdb = config["gaussdb"]
        expected_doc = gaussdb.get("config", gaussdb)
        metadata_matches = len(metadata) == 1 and (
            metadata[0].get("host") == environment["GAUSSDB_METADATA_HOST"]
            and int(metadata[0].get("port")) == int(environment["GAUSSDB_METADATA_PORT"])
            and metadata[0]["extra"].get("username") == environment["GAUSSDB_METADATA_USER"]
            and metadata[0]["extra"].get("schema") == environment["GAUSSDB_METADATA_SCHEMA"]
        )
        retrieval_matches = len(retrieval) == 1 and (
            retrieval[0].get("host") == expected_doc["host"] and int(retrieval[0].get("port")) == int(expected_doc["port"]) and retrieval[0]["extra"].get("retrieval_type") == "gaussdb"
        )
    raw_text = json.dumps(body, ensure_ascii=False, sort_keys=True)
    exposures = {category: any(value in raw_text for value in values) for category, values in _known_admin_service_credentials(group).items()}
    safe_services = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "host": item.get("host"),
            "port": item.get("port"),
            "service_type": item.get("service_type"),
            "status": item.get("status"),
            "meta_type": (item.get("extra") or {}).get("meta_type"),
            "retrieval_type": (item.get("extra") or {}).get("retrieval_type"),
            "schema": (item.get("extra") or {}).get("schema"),
        }
        for item in services
    ]
    return {
        **request_summary,
        "service_count": len(services),
        "metadata_service_count": len(metadata),
        "retrieval_service_count": len(retrieval),
        "metadata_type": metadata[0]["extra"].get("meta_type") if len(metadata) == 1 else None,
        "retrieval_type": retrieval[0]["extra"].get("retrieval_type") if len(retrieval) == 1 else None,
        "metadata_matches_expected": metadata_matches,
        "retrieval_matches_expected": retrieval_matches,
        "credential_exposure_categories": exposures,
        "credential_exposure_count": sum(exposures.values()),
        "safe_service_summaries": safe_services,
    }


def _admin_metadata_detail_probe(group: str) -> dict[str, Any]:
    session, base_url, _body, services, request_summary = _request_admin_services(group, "TC-SM-019")
    metadata = [item for item in services if isinstance(item, dict) and isinstance(item.get("extra"), dict) and item["extra"].get("meta_type")]
    if len(metadata) != 1:
        raise RuntimeError(f"{group} metadata service was not found exactly once")
    service_id = int(metadata[0]["id"])
    response = session.get(f"{base_url}/services/{service_id}", timeout=60)
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_path = raw_dir / f"TC-SM-019_{group}_metadata_detail.json"
    raw_path.write_bytes(response.content)
    raw_path.chmod(0o600)
    body = response.json()
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    message = data.get("message")
    return {
        **request_summary,
        "metadata_service_id": service_id,
        "metadata_type": metadata[0]["extra"].get("meta_type"),
        "detail_http_status": response.status_code,
        "detail_code": body.get("code"),
        "detail_status": data.get("status"),
        "detail_message_kind": type(message).__name__,
        "detail_database": message.get("database") if isinstance(message, dict) else None,
        "detail_result": message.get("result") if isinstance(message, dict) else None,
        "detail_raw_sha256": _sha256(raw_path),
    }


def _health_sql_trace_probe(group: str) -> dict[str, Any]:
    source = "\n".join(
        [
            "import json",
            "from common import settings",
            "from api.utils import health_utils",
            "executed = []",
            "class Cursor:",
            "    def fetchall(self): return [(1, 'user', 'host', 'db', 'Sleep', 0, '', '')]",
            "    def fetchone(self): return (1,)",
            "    def close(self): return None",
            "class FakeDB:",
            "    def execute_sql(self, sql): executed.append(sql); return Cursor()",
            "health_utils.DB = FakeDB()",
            ("settings.DATABASE_TYPE = 'mysql'; result = health_utils.get_mysql_status()" if group == "control" else "settings.DATABASE_TYPE = 'gaussdb'; result = health_utils.get_database_status()"),
            "print('FRESH_TC_SM_019=' + json.dumps({'executed_sql': executed, 'status': result.get('status')}, sort_keys=True))",
        ]
    )
    return _run_settings_probe(group, "TC-SM-019", source)


def _compose_profile_probe(group: str) -> dict[str, Any]:
    if group == "control":
        database_type = "mysql"
        doc_engine = "infinity"
        profiles = "infinity,cpu,metadata-mysql"
    elif group == "experiment":
        database_type = "gaussdb"
        doc_engine = "gaussdb"
        profiles = "gaussdb,cpu,metadata-gaussdb"
    else:
        raise ValueError("unknown group")
    process_metadata_overrides = {
        "GAUSSDB_METADATA_HOST": "sm020-meta-host",
        "GAUSSDB_METADATA_PORT": "15432",
        "GAUSSDB_METADATA_USER": "sm020-meta-user",
        "GAUSSDB_METADATA_PASSWORD": "sm020-meta-credential",
        "GAUSSDB_METADATA_DBNAME": "sm020_meta_db",
        "GAUSSDB_METADATA_SCHEMA": "sm020_meta_schema",
        "GAUSSDB_METADATA_MAX_CONNECTIONS": "17",
        "GAUSSDB_METADATA_STALE_TIMEOUT": "23",
    }
    environment = os.environ.copy()
    environment.update(
        {
            "DB_TYPE": database_type,
            "DOC_ENGINE": doc_engine,
            "DEVICE": "cpu",
            "COMPOSE_PROFILES": profiles,
            "MYSQL_PASSWORD": "sm020-mysql-credential",
            "MINIO_PASSWORD": "sm020-minio-credential",
            "REDIS_PASSWORD": "sm020-redis-credential",
            **process_metadata_overrides,
        }
    )
    base_command = [
        "docker",
        "compose",
        "-f",
        str(PROJECT_ROOT / "docker" / "docker-compose.yml"),
    ]
    services_result = subprocess.run(
        [*base_command, "config", "--services"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    config_result = subprocess.run(
        [*base_command, "config", "--format", "json"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    rendered = json.loads(config_result.stdout.decode("utf-8")) if config_result.returncode == 0 else {}
    environment_secrets = {str(value) for key, value in environment.items() if value and is_sensitive_config_key(key)}
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths = {
        "services_stdout": raw_dir / f"TC-SM-020_{group}_services.stdout",
        "services_stderr": raw_dir / f"TC-SM-020_{group}_services.stderr",
        "config_stdout": raw_dir / f"TC-SM-020_{group}_config.json",
        "config_stderr": raw_dir / f"TC-SM-020_{group}_config.stderr",
    }
    contents = {
        "services_stdout": services_result.stdout,
        "services_stderr": services_result.stderr,
        "config_stdout": (redacted_json_bytes(rendered) if config_result.returncode == 0 else config_result.stdout),
        "config_stderr": config_result.stderr,
    }
    for key, path in paths.items():
        path.write_bytes(_replace_sensitive_values(contents[key], environment_secrets))
        path.chmod(0o600)
    services = [line.strip() for line in services_result.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    rendered_services = rendered.get("services", {})
    app = rendered_services.get("ragflow-cpu", {})
    app_environment = app.get("environment", {})
    if isinstance(app_environment, list):
        app_environment = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in app_environment if isinstance(item, str) and "=" in item}
    mysql_dependency = (app.get("depends_on") or {}).get("mysql")
    dependency_required = bool(mysql_dependency.get("required", True)) if isinstance(mysql_dependency, dict) else False
    required_metadata_keys = set(process_metadata_overrides)
    expected_metadata = dotenv_selected_values(
        (PROJECT_ROOT / "docker" / ".env").read_text(encoding="utf-8"),
        required_metadata_keys,
    )
    metadata_environment_present = required_metadata_keys.issubset(app_environment)
    application_environment_matches = (
        app_environment.get("DB_TYPE") == database_type
        and app_environment.get("DOC_ENGINE") == doc_engine
        and expected_metadata.keys() == required_metadata_keys
        and all(str(app_environment.get(key)) == value for key, value in expected_metadata.items())
    )
    return {
        "services_exit_code": services_result.returncode,
        "config_exit_code": config_result.returncode,
        "profiles": profiles.split(","),
        "active_services": services,
        "mysql_present": "mysql" in services,
        "infinity_present": "infinity" in services,
        "ragflow_cpu_present": "ragflow-cpu" in services,
        "mysql_dependency_present": mysql_dependency is not None,
        "mysql_dependency_required": dependency_required,
        "gauss_metadata_environment_present": metadata_environment_present,
        "application_environment_matches": application_environment_matches,
        "safe_application_environment": {
            "DB_TYPE": app_environment.get("DB_TYPE"),
            "DOC_ENGINE": app_environment.get("DOC_ENGINE"),
            "GAUSSDB_METADATA_HOST": app_environment.get("GAUSSDB_METADATA_HOST"),
            "GAUSSDB_METADATA_PORT": app_environment.get("GAUSSDB_METADATA_PORT"),
            "GAUSSDB_METADATA_DBNAME": app_environment.get("GAUSSDB_METADATA_DBNAME"),
            "GAUSSDB_METADATA_SCHEMA": app_environment.get("GAUSSDB_METADATA_SCHEMA"),
            "metadata_credential_matches": app_environment.get("GAUSSDB_METADATA_PASSWORD") == expected_metadata.get("GAUSSDB_METADATA_PASSWORD"),
        },
        "raw_sha256": {key: _sha256(path) for key, path in paths.items()},
    }


def _helm_metadata_probe(group: str) -> dict[str, Any]:
    helm_path = shutil.which("helm") or "/home/zws/.local/bin/helm"
    if not Path(helm_path).is_file():
        raise RuntimeError("Helm is not installed")
    command = [helm_path, "template", "ragflow", str(PROJECT_ROOT / "helm")]
    safe_metadata = {
        "GAUSSDB_METADATA_HOST": "sm021-meta-host",
        "GAUSSDB_METADATA_PORT": "15432",
        "GAUSSDB_METADATA_USER": "sm021-meta-user",
        "GAUSSDB_METADATA_PASSWORD": "sm021-meta-credential",
        "GAUSSDB_METADATA_DBNAME": "sm021_meta_db",
        "GAUSSDB_METADATA_SCHEMA": "sm021_meta_schema",
        "GAUSSDB_METADATA_MAX_CONNECTIONS": "19",
        "GAUSSDB_METADATA_STALE_TIMEOUT": "29",
    }
    if group == "control":
        command.extend(
            [
                "--set-string",
                "env.DB_TYPE=mysql",
                "--set",
                "mysql.enabled=true",
                "--set-string",
                "env.DOC_ENGINE=infinity",
            ]
        )
    elif group == "experiment":
        command.extend(
            [
                "--set-string",
                "env.DB_TYPE=gaussdb",
                "--set",
                "mysql.enabled=false",
                "--set-string",
                "env.MYSQL_PASSWORD=",
                "--set-string",
                "env.DOC_ENGINE=infinity",
            ]
        )
        for key, value in safe_metadata.items():
            command.extend(["--set-string", f"env.{key}={value}"])
    else:
        raise ValueError("unknown group")
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        timeout=60,
        check=False,
    )
    version = subprocess.run(
        [helm_path, "version", "--short"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    documents = []
    if completed.returncode == 0:
        yaml = YAML(typ="safe", pure=True)
        documents = [item for item in yaml.load_all(completed.stdout.decode("utf-8")) if isinstance(item, dict)]
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = raw_dir / f"TC-SM-021_{group}_template.yaml"
    stderr_path = raw_dir / f"TC-SM-021_{group}_template.stderr"
    explicit_secrets = {safe_metadata["GAUSSDB_METADATA_PASSWORD"]}
    stdout_path.write_bytes(redacted_yaml_bytes(documents) if completed.returncode == 0 else _replace_sensitive_values(completed.stdout, explicit_secrets))
    stderr_path.write_bytes(_replace_sensitive_values(completed.stderr, explicit_secrets))
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)
    env_secrets = [item for item in documents if item.get("kind") == "Secret" and str((item.get("metadata") or {}).get("name", "")).endswith("-env-config")]
    string_data = env_secrets[0].get("stringData", {}) if len(env_secrets) == 1 else {}
    mysql_resources = []
    for item in documents:
        metadata = item.get("metadata") or {}
        labels = metadata.get("labels") or {}
        name = str(metadata.get("name", ""))
        if labels.get("app.kubernetes.io/component") == "mysql" or name in {
            "ragflow-mysql",
            "mysql-init-script",
        }:
            mysql_resources.append(item)
    ragflow_workloads = [
        item for item in documents if item.get("kind") in {"Deployment", "StatefulSet"} and (item.get("metadata") or {}).get("labels", {}).get("app.kubernetes.io/component") == "ragflow"
    ]
    mysql_credential_keys = {"MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD"}
    required_gauss_keys = set(safe_metadata)
    return {
        "helm_version": version.stdout.strip(),
        "template_exit_code": completed.returncode,
        "document_count": len(documents),
        "environment_config_resource_count": len(env_secrets),
        "ragflow_workload_count": len(ragflow_workloads),
        "db_type": string_data.get("DB_TYPE"),
        "mysql_resource_count": len(mysql_resources),
        "mysql_resource_kinds": sorted({str(item.get("kind")) for item in mysql_resources}),
        "mysql_credential_keys_present": bool(mysql_credential_keys.intersection(string_data)),
        "gauss_metadata_keys_present": required_gauss_keys.issubset(string_data),
        "gauss_metadata_values_match": all(str(string_data.get(key)) == value for key, value in safe_metadata.items()),
        "safe_gauss_metadata": {
            "host": string_data.get("GAUSSDB_METADATA_HOST"),
            "port": string_data.get("GAUSSDB_METADATA_PORT"),
            "database": string_data.get("GAUSSDB_METADATA_DBNAME"),
            "schema": string_data.get("GAUSSDB_METADATA_SCHEMA"),
            "credential_matches": string_data.get("GAUSSDB_METADATA_PASSWORD") == safe_metadata["GAUSSDB_METADATA_PASSWORD"],
        },
        "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
    }


def _run_empty_embedding_api_flow(group: str) -> dict[str, Any]:
    root = RUNTIME_DIR / group
    config = _load_yaml(root / "conf" / "service_conf.yaml")
    api_base = f"http://127.0.0.1:{runtime_http_port(config, 'ragflow')}/api/v1"
    email = f"sm010-{group}-{BATCH_ID.replace('_', '-')}@fresh.invalid"
    password = "Sm010-fresh-case-password!"
    session = requests.Session()
    user_id: str | None = None
    dataset_id: str | None = None
    result: dict[str, Any] = {
        "fixture_email": email,
        "registration_code": None,
        "tenant_api_value": None,
        "dataset_create_code": None,
        "dataset_get_code": None,
        "dataset_api_value": None,
        "dataset_delete_code": None,
        "user_cleanup_complete": False,
    }
    try:
        registration = session.post(
            f"{api_base}/users",
            json={
                "email": email,
                "nickname": f"SM010 {group}",
                "password": _encrypt_case_password(password, root / "conf" / "public.pem"),
            },
            timeout=30,
        )
        registration_body = registration.json()
        result.update(
            {
                "registration_http_status": registration.status_code,
                "registration_code": registration_body.get("code"),
            }
        )
        if registration_body.get("code") != 0:
            raise RuntimeError("test user registration failed")
        auth = registration.headers.get("Authorization")
        if not auth:
            raise RuntimeError("registration response omitted authorization")
        session.headers["Authorization"] = f"Bearer {auth}"
        user_id = str(registration_body["data"]["id"])

        tenant_response = session.get(f"{api_base}/users/me/models", timeout=30)
        tenant_body = tenant_response.json()
        result.update(
            {
                "tenant_api_http_status": tenant_response.status_code,
                "tenant_api_code": tenant_body.get("code"),
                "tenant_api_value": (tenant_body.get("data") or {}).get("embd_id"),
            }
        )

        dataset_response = session.post(
            f"{api_base}/datasets",
            json={
                "name": f"fr-sm010-{group}-empty-embedding",
                "chunk_method": "naive",
            },
            timeout=60,
        )
        dataset_body = dataset_response.json()
        result.update(
            {
                "dataset_create_http_status": dataset_response.status_code,
                "dataset_create_code": dataset_body.get("code"),
            }
        )
        if dataset_body.get("code") != 0:
            raise RuntimeError("empty embedding dataset creation failed")
        dataset_id = str(dataset_body["data"]["id"])
        result["dataset_create_api_value"] = dataset_body["data"].get("embedding_model")

        result.update(_empty_embedding_storage_snapshot(group, user_id, dataset_id))

        detail_response = session.get(f"{api_base}/datasets/{dataset_id}", timeout=30)
        detail_body = detail_response.json()
        result.update(
            {
                "dataset_get_http_status": detail_response.status_code,
                "dataset_get_code": detail_body.get("code"),
                "dataset_api_value": (detail_body.get("data") or {}).get("embedding_model"),
            }
        )
    except Exception as exc:
        result["execution_error_type"] = type(exc).__name__
    finally:
        if dataset_id and user_id:
            try:
                deletion = session.delete(
                    f"{api_base}/datasets",
                    json={"ids": [dataset_id]},
                    timeout=90,
                )
                deletion_body = deletion.json()
                result.update(
                    {
                        "dataset_delete_http_status": deletion.status_code,
                        "dataset_delete_code": deletion_body.get("code"),
                    }
                )
            except Exception as exc:
                result["dataset_cleanup_error_type"] = type(exc).__name__
        if user_id:
            try:
                result["admin_cleanup"] = _admin_cleanup_empty_embedding_user(group, email)
            except Exception as exc:
                result["user_cleanup_error_type"] = type(exc).__name__
        counts = _empty_embedding_cleanup_counts(group, email, user_id, dataset_id)
        result["cleanup_counts"] = counts
        admin_cleanup = result.get("admin_cleanup", {})
        result["user_cleanup_complete"] = (
            counts == {"user_count": 0, "tenant_count": 0, "dataset_count": 0}
            and admin_cleanup.get("admin_login_code") == 0
            and admin_cleanup.get("disable_code") == 0
            and admin_cleanup.get("delete_code") == 0
        )
    return result


def _compile_empty_string_query(group: str) -> dict[str, Any]:
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = os.environ.copy()
    environment.update({str(k): str(v) for k, v in environments[group].items()})
    for key in (
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ):
        environment.pop(key, None)
    source = "import json; from api.db.db_models import Memory; sql, params = Memory.select().where(Memory.embd_id == '').sql(); print('FRESH_SM011=' + json.dumps({'sql': sql, 'params': params}))"
    completed = subprocess.run(
        [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-c", source],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = raw_dir / f"TC-SM-011_{group}_compile.stdout"
    stderr_path = raw_dir / f"TC-SM-011_{group}_compile.stderr"
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)
    marker = b"FRESH_SM011="
    payload = None
    for line in completed.stdout.splitlines():
        if line.startswith(marker):
            payload = json.loads(line[len(marker) :].decode("utf-8"))
    if completed.returncode != 0 or not isinstance(payload, dict):
        raise RuntimeError(f"{group} ORM query compilation failed")
    return {
        "exit_code": completed.returncode,
        "sql": payload.get("sql"),
        "params": payload.get("params"),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
    }


def _memory_list_filter_scope() -> dict[str, Any]:
    path = PROJECT_ROOT / "api" / "apps" / "restful_apis" / "memory_api.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "list_memory"]
    if len(functions) != 1:
        raise RuntimeError("memory list endpoint function not found exactly once")
    referenced_strings = {node.value for node in ast.walk(functions[0]) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    return {
        "function_found": True,
        "embd_id_filter_referenced": "embd_id" in referenced_strings,
        "recognized_filters": sorted(referenced_strings.intersection({"memory_type", "tenant_id", "owner_ids", "storage_type"})),
        "source_sha256": _sha256(path),
    }


def _extract_shell_function(source: str, name: str) -> str:
    lines = source.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if re.fullmatch(rf"{re.escape(name)}\(\)\s*\{{", line.strip())),
        None,
    )
    if start is None:
        raise RuntimeError(f"shell function {name} was not found")
    depth = 0
    selected = []
    for line in lines[start:]:
        selected.append(line)
        depth += line.count("{") - line.count("}")
        if depth == 0:
            break
    if not selected or depth != 0:
        raise RuntimeError(f"shell function {name} was not balanced")
    return "\n".join(selected) + "\n"


def _run_mysql_migration_guard_probe(group: str) -> dict[str, Any]:
    path = PROJECT_ROOT / "docker" / "launch_backend_service.sh"
    source = path.read_text(encoding="utf-8")
    function_source = _extract_shell_function(source, "run_mysql_migrations")
    environment = os.environ.copy()
    environment.update(
        {
            "DB_TYPE": "mysql" if group == "control" else "gaussdb",
            "PY": "/bin/echo",
        }
    )
    completed = subprocess.run(
        ["bash", "-c", "set -e\n" + function_source + "run_mysql_migrations\n"],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        timeout=30,
        check=False,
    )
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = raw_dir / f"TC-SM-012_{group}_guard.stdout"
    stderr_path = raw_dir / f"TC-SM-012_{group}_guard.stderr"
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)
    stdout_text = completed.stdout.decode("utf-8", errors="replace")
    return {
        "exit_code": completed.returncode,
        "migration_invocation_count": stdout_text.count("tools/scripts/mysql_migration.py"),
        "skip_message": ("Skipping MySQL-specific model provider table migrations" in stdout_text),
        "launch_script_sha256": _sha256(path),
        "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
    }


def _startup_mysql_only_log_counts(group: str) -> dict[str, Any]:
    path = ENV_EVIDENCE_DIR / f"startup_attempt1_{group}.log"
    text = path.read_text(encoding="utf-8", errors="replace")
    counts = {
        keyword: text.count(keyword)
        for keyword in (
            "mysql_migration",
            "AUTO_INCREMENT",
            "ON DUPLICATE",
            "SHOW PROCESSLIST",
        )
    }
    return {"counts": counts, "raw_log_sha256": _sha256(path)}


def _ddl_dialect_snapshot(group: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / "control" / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            read_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name,column_name,data_type,column_default,extra FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position",
                    (config["name"],),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        auto_increment = [row for row in rows if "auto_increment" in str(row[4]).lower()]
        datetimes = [row for row in rows if str(row[2]).lower() == "datetime"]
        longtexts = [row for row in rows if str(row[2]).lower() in {"longtext", "mediumtext", "tinytext"}]
        return {
            "catalog": "mysql_information_schema",
            "column_count": len(rows),
            "auto_increment_column_count": len(auto_increment),
            "datetime_column_count": len(datetimes),
            "longtext_column_count": len(longtexts),
            "auto_increment_samples": [f"{row[0]}.{row[1]}" for row in auto_increment[:10]],
            "datetime_samples": [f"{row[0]}.{row[1]}" for row in datetimes[:10]],
            "longtext_samples": [f"{row[0]}.{row[1]}" for row in longtexts[:10]],
        }

    if group != "experiment":
        raise ValueError("unknown group")
    import psycopg2

    environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))["experiment"]
    schema = environment["GAUSSDB_METADATA_SCHEMA"]
    connection = psycopg2.connect(
        host=environment["GAUSSDB_METADATA_HOST"],
        port=int(environment["GAUSSDB_METADATA_PORT"]),
        dbname=environment["GAUSSDB_METADATA_DBNAME"],
        user=environment["GAUSSDB_METADATA_USER"],
        password=environment["GAUSSDB_METADATA_PASSWORD"],
        connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c search_path={schema}",
    )
    _set_gauss_search_path(connection, schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW sql_compatibility")
            compatibility = cursor.fetchone()[0]
            cursor.execute(
                "SELECT table_name,column_name,data_type,column_default FROM information_schema.columns WHERE table_schema=%s ORDER BY table_name,ordinal_position",
                (schema,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    nextvals = [row for row in rows if "nextval" in str(row[3]).lower()]
    timestamps = [row for row in rows if str(row[2]).lower() == "timestamp without time zone"]
    texts = [row for row in rows if str(row[2]).lower() == "text"]
    mysql_only = [row for row in rows if str(row[2]).lower() in {"longtext", "mediumtext", "tinytext", "datetime"}]
    return {
        "catalog": "gaussdb_information_schema",
        "sql_compatibility": compatibility,
        "column_count": len(rows),
        "nextval_column_count": len(nextvals),
        "timestamp_column_count": len(timestamps),
        "text_column_count": len(texts),
        "mysql_only_type_column_count": len(mysql_only),
        "nextval_samples": [f"{row[0]}.{row[1]}" for row in nextvals[:10]],
        "timestamp_samples": [f"{row[0]}.{row[1]}" for row in timestamps[:10]],
        "text_samples": [f"{row[0]}.{row[1]}" for row in texts[:10]],
        "mysql_only_samples": [f"{row[0]}.{row[1]}:{row[2]}" for row in mysql_only[:10]],
    }


def _index_dialect_snapshot(group: str) -> dict[str, Any]:
    if group == "control":
        import pymysql

        config = _load_yaml(RUNTIME_DIR / "control" / "conf" / "service_conf.yaml")["mysql"]
        connection = pymysql.connect(
            host=config["host"],
            port=int(config["port"]),
            user=config["user"],
            password=config["password"],
            database=config["name"],
            connect_timeout=5,
            read_timeout=10,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT DISTINCT table_name,index_name FROM information_schema.statistics WHERE table_schema=%s ORDER BY table_name,index_name",
                    (config["name"],),
                )
                indexes = cursor.fetchall()
                cursor.execute("SHOW CREATE TABLE tenant_llm")
                show_create = str(cursor.fetchone()[1])
        finally:
            connection.close()
        return {
            "catalog": "mysql_information_schema.statistics",
            "index_count": len(indexes),
            "show_create_backtick_count": show_create.count("`"),
            "index_samples": [f"{table}.{name}" for table, name in indexes[:10]],
        }

    if group != "experiment":
        raise ValueError("unknown group")
    import psycopg2

    environment = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))["experiment"]
    schema = environment["GAUSSDB_METADATA_SCHEMA"]
    connection = psycopg2.connect(
        host=environment["GAUSSDB_METADATA_HOST"],
        port=int(environment["GAUSSDB_METADATA_PORT"]),
        dbname=environment["GAUSSDB_METADATA_DBNAME"],
        user=environment["GAUSSDB_METADATA_USER"],
        password=environment["GAUSSDB_METADATA_PASSWORD"],
        connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c search_path={schema}",
    )
    _set_gauss_search_path(connection, schema)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=%s ORDER BY tablename,indexname",
                (schema,),
            )
            indexes = cursor.fetchall()
    finally:
        connection.close()
    definitions = "\n".join(str(row[2]) for row in indexes)
    mysql_only = re.findall(
        r"\b(?:FULLTEXT|SPATIAL)\b|(?:KEY|INDEX)\s+`",
        definitions,
        flags=re.IGNORECASE,
    )
    invalid = [
        row
        for row in indexes
        if not re.match(
            r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+.+\s+ON\s+",
            str(row[2]),
            flags=re.IGNORECASE,
        )
    ]
    return {
        "catalog": "pg_indexes",
        "index_count": len(indexes),
        "backtick_occurrences": definitions.count("`"),
        "mysql_only_syntax_occurrences": len(mysql_only),
        "invalid_index_definition_count": len(invalid),
        "index_definition_samples": [str(row[2]) for row in indexes[:5]],
    }


def _run_settings_probe(group: str, case_id: str, source: str) -> dict[str, Any]:
    environments = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    environment = os.environ.copy()
    environment.update({str(k): str(v) for k, v in environments[group].items()})
    for key in (
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ):
        environment.pop(key, None)
    marker = f"FRESH_{case_id.replace('-', '_')}=".encode("ascii")
    completed = subprocess.run(
        [str(PROJECT_ROOT / ".venv" / "bin" / "python"), "-c", source],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout_path = raw_dir / f"{case_id}_{group}_settings.stdout"
    stderr_path = raw_dir / f"{case_id}_{group}_settings.stderr"
    stdout_path.write_bytes(completed.stdout)
    stderr_path.write_bytes(completed.stderr)
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)
    payload = None
    for line in completed.stdout.splitlines():
        if line.startswith(marker):
            payload = json.loads(line[len(marker) :].decode("utf-8"))
    if completed.returncode != 0 or not isinstance(payload, dict):
        raise RuntimeError(f"{case_id} {group} settings probe failed")
    return {
        "exit_code": completed.returncode,
        "payload": payload,
        "stdout_sha256": _sha256(stdout_path),
        "stderr_sha256": _sha256(stderr_path),
    }


def _database_alias_probe(group: str) -> dict[str, Any]:
    source = "\n".join(
        [
            "import json",
            "from common import settings",
            "values = ['GaussDB', 'gauss', 'GAUSSDB', 'gaussdb', 'postgresql']",
            "observed = {value: settings.normalize_database_type(value) for value in values}",
            "print('FRESH_TC_SM_015=' + json.dumps(observed, sort_keys=True))",
        ]
    )
    return _run_settings_probe(group, "TC-SM-015", source)


def _schema_validation_probe(group: str) -> dict[str, Any]:
    source = "\n".join(
        [
            "import json, os",
            "from common import settings",
            "values = ['ragflow_meta', 'public', '_test_schema', '', 'test; DROP TABLE', 'test-schema', '123schema']",
            "observed = {}",
            "for value in values:",
            "    os.environ['GAUSSDB_METADATA_SCHEMA'] = value",
            "    try:",
            "        normalized = settings._normalize_gaussdb_metadata_schema(value)",
            "        config = settings._gaussdb_env_config()",
            "        observed[value] = {'accepted': True, 'normalized': normalized, 'search_path_present': ('search_path=' + normalized) in config['options']}",
            "    except Exception as exc:",
            "        observed[value] = {'accepted': False, 'error_type': type(exc).__name__}",
            "print('FRESH_TC_SM_016=' + json.dumps(observed, sort_keys=True))",
        ]
    )
    return _run_settings_probe(group, "TC-SM-016", source)


def _config_isolation_probe(group: str) -> dict[str, Any]:
    source = "\n".join(
        [
            "import json, os",
            "from common import settings",
            "from common.doc_store.gaussdb_conn_pool import load_gaussdb_config",
            f"group = {group!r}",
            "observed = {'runtime_database_type': settings.DATABASE_TYPE, 'runtime_doc_engine': settings.DOC_ENGINE.lower()}",
            "observed['actual_selection'] = {'metadata': settings.DATABASE_TYPE, 'docstore': settings.DOC_ENGINE.lower()}",
            "if group == 'control':",
            "    before = settings.load_database_config('mysql')",
            "    before_safe = (before.get('host'), before.get('port'), before.get('name'), before.get('user'))",
            "else:",
            "    metadata = settings.DATABASE",
            "    expected_meta = (os.environ['GAUSSDB_METADATA_HOST'], int(os.environ['GAUSSDB_METADATA_PORT']), os.environ['GAUSSDB_METADATA_DBNAME'], os.environ['GAUSSDB_METADATA_USER'])",
            "    actual_meta = (metadata.get('host'), int(metadata.get('port')), metadata.get('name'), metadata.get('user'))",
            "    observed['actual_metadata_matches_environment'] = actual_meta == expected_meta",
            "    raw_doc = settings.get_base_config('gaussdb', {})",
            "    doc = load_gaussdb_config(raw_doc)",
            "    raw_cfg = raw_doc.get('config', raw_doc)",
            "    observed['actual_docstore_matches_service_config'] = (doc.host, doc.port, doc.database, doc.user, doc.schema) == (str(raw_cfg.get('host')).strip(), int(raw_cfg.get('port')), str(raw_cfg.get('database')).strip(), str(raw_cfg.get('user')).strip(), str(raw_cfg.get('schema') or 'public').strip())",
            "    observed['actual_targets_distinct'] = (metadata.get('name'), os.environ.get('GAUSSDB_METADATA_SCHEMA', 'public')) != (doc.database, doc.schema)",
            "os.environ.update({'GAUSSDB_METADATA_HOST': 'meta-host', 'GAUSSDB_METADATA_PORT': '5432', 'GAUSSDB_METADATA_DBNAME': 'meta_db', 'GAUSSDB_METADATA_USER': 'meta-user', 'GAUSSDB_METADATA_PASSWORD': 'meta-password', 'GAUSSDB_METADATA_SCHEMA': 'meta_schema'})",
            "fake_meta = settings.load_database_config('gaussdb')",
            "observed['fake_metadata'] = {'host': fake_meta['host'], 'name': fake_meta['name']}",
            "fake_doc = load_gaussdb_config({'host': 'doc-host', 'port': 15432, 'database': 'doc_db', 'user': 'doc-user', 'password': 'doc-password', 'schema': 'doc_schema'})",
            "observed['fake_docstore'] = {'host': fake_doc.host, 'database': fake_doc.database}",
            "if group == 'control':",
            "    after = settings.load_database_config('mysql')",
            "    after_safe = (after.get('host'), after.get('port'), after.get('name'), after.get('user'))",
            "    observed['gauss_metadata_env_did_not_override_mysql'] = before_safe == after_safe",
            "print('FRESH_TC_SM_017=' + json.dumps(observed, sort_keys=True))",
        ]
    )
    return _run_settings_probe(group, "TC-SM-017", source)


def _service_manager(*args: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(PROJECT_ROOT / ".venv" / "bin" / "python"),
            str(EXECUTE_DIR / "fresh_service_manager.py"),
            *args,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("service manager command failed")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("invalid service manager response")
    return payload


def _wait_api_ping(group: str, timeout: float = 120) -> float:
    port = 9380 if group == "control" else 9480
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(f"http://127.0.0.1:{port}/api/v1/system/ping", timeout=2)
            if response.status_code == 200 and response.text == "pong":
                return time.monotonic() - started
        except requests.RequestException:
            pass
        if not _registered_api_alive(group):
            raise RuntimeError(f"{group} API exited before becoming ready")
        time.sleep(0.5)
    raise TimeoutError(f"{group} API did not become ready")


def _registered_api_alive(group: str) -> bool:
    state = json.loads((RUNTIME_DIR / "processes.json").read_text(encoding="utf-8"))
    entry = state.get(f"{group}:api") or {}
    pid = int(entry.get("pid", 0))
    if pid <= 1:
        return False
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return False
    closing = raw.rfind(")")
    fields = raw[closing + 2 :].split() if closing >= 0 else []
    return len(fields) > 19 and fields[0] != "Z" and fields[19] == str(entry.get("start_ticks"))


def _restart_group_api(group: str, case_id: str) -> dict[str, Any]:
    log_path = RUNTIME_DIR / group / "logs" / "managed_api.log"
    offset = log_path.stat().st_size
    stop_result = _service_manager("stop", "--group", group, "--service", "api")
    start_result = _service_manager("start", "--group", group, "--service", "api")
    ready = False
    ready_seconds = None
    readiness_error_type = None
    try:
        ready_seconds = _wait_api_ping(group)
        ready = True
    except (RuntimeError, TimeoutError) as exc:
        readiness_error_type = type(exc).__name__
    with log_path.open("rb") as stream:
        stream.seek(offset)
        raw_slice = stream.read()
    raw_dir = EVIDENCE_DIR / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_path = raw_dir / f"{case_id}_{group}_restart.log"
    raw_path.write_bytes(raw_slice)
    raw_path.chmod(0o600)
    summary = summarize_restart_log(raw_slice.decode("utf-8", errors="replace"))
    status = _service_manager("status").get(f"{group}:api", {})
    return {
        "stop_confirmed": bool(stop_result.get("stopped")),
        "start_confirmed": bool(start_result.get("alive")),
        "currently_alive": bool(status.get("alive") and status.get("identity_matches")),
        "ready": ready,
        "ready_seconds": round(ready_seconds, 3) if ready_seconds is not None else None,
        "readiness_error_type": readiness_error_type,
        "log_summary": summary,
        "raw_log_sha256": _sha256(raw_path),
    }


def _load_case_evidence_module():
    path = EXECUTE_DIR / "fresh_case_evidence.py"
    spec = importlib.util.spec_from_file_location("fresh_case_evidence_runtime", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_sm001() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-001", CASE_TITLES["TC-SM-001"])
    clean_result = json.loads((ENV_EVIDENCE_DIR / "clean_result.json").read_text(encoding="utf-8"))
    control_log_path = ENV_EVIDENCE_DIR / "startup_attempt1_control.log"
    experiment_log_path = ENV_EVIDENCE_DIR / "startup_attempt1_experiment.log"

    control_log = summarize_startup_log(control_log_path.read_text(encoding="utf-8", errors="replace"))
    control_catalog = _control_catalog_snapshot()
    control_ok = (
        clean_result["mysql"]["table_count"] == 0
        and control_log["ready"]
        and control_catalog["table_contract_ok"]
        and control_catalog["default_data"]["superuser_count"] >= 1
        and control_catalog["default_data"]["tenant_count"] >= 1
        and control_catalog["default_data"]["system_settings_count"] > 0
    )
    recorder.add_group(
        "control",
        "PASS" if control_ok else "FAIL",
        [
            {
                "name": "confirm_clean_baseline",
                "observed_table_count": clean_result["mysql"]["table_count"],
                "expected_table_count": 0,
            },
            {
                "name": "cold_start",
                "startup": control_log,
                "raw_log_sha256": _sha256(control_log_path),
            },
            {"name": "read_only_catalog_and_defaults", "observed": control_catalog},
        ],
        oracle={
            "expected_tables": 40,
            "expected_ready": True,
            "expected_default_user_tenant_system_settings": True,
            "legacy_llm_factories_nonempty_required": False,
        },
    )

    experiment_log = summarize_startup_log(experiment_log_path.read_text(encoding="utf-8", errors="replace"))
    experiment_schema = _experiment_gauss_schema_snapshot()
    experiment_ok = experiment_schema["target_schema_table_count"] == 40 and experiment_log["ready"] and not experiment_log["undefined_system_settings"]
    recorder.add_group(
        "experiment",
        "PASS" if experiment_ok else "FAIL",
        [
            {
                "name": "confirm_clean_baseline",
                "metadata_schema_created": clean_result["gauss"]["metadata_schema"],
            },
            {
                "name": "cold_start",
                "startup": experiment_log,
                "raw_log_sha256": _sha256(experiment_log_path),
            },
            {
                "name": "read_only_catalog_after_cold_start",
                "observed": experiment_schema,
            },
        ],
        oracle={
            "expected_tables": 40,
            "expected_ready": True,
            "expected_no_mysql_only_execution": True,
        },
        findings=sm001_experiment_findings(experiment_ok),
    )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-001.json", result)
    return result


def run_sm002() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-002", CASE_TITLES["TC-SM-002"])
    for group in GROUP_ORDER:
        before = _catalog_counts(group)
        restart = _restart_group_api(group, "TC-SM-002")
        after = _catalog_counts(group)
        ok = restart_contract_ok(before, after, restart)
        findings = []
        if group == "experiment" and not ok:
            findings.append(
                {
                    "id": "SM-DEFECT-002",
                    "summary": ("restart in a populated non-public metadata schema treated all tables as absent; index creation then failed because the existing indexes already existed"),
                    "code_location": "api/db/db_models.py:init_database_tables",
                }
            )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {"name": "catalog_before_restart", "observed": before},
                {"name": "restart_api", "observed": restart},
                {"name": "catalog_after_restart", "observed": after},
            ],
            oracle={
                "table_and_index_counts_unchanged": True,
                "expected_table_count": 40,
                "ddl_error_count": 0,
            },
            findings=findings,
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-002.json", result)
    return result


def run_sm003() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-003", CASE_TITLES["TC-SM-003"])
    for group in GROUP_ORDER:
        database = case_database_name("TC-SM-003", group)
        prepared = _prepare_case_database(group, database)
        cleanup_ok = False
        try:
            root, environment = _prepare_case_runtime("TC-SM-003", group, database)
            concurrent = _run_concurrent_initializers("TC-SM-003", group, root, environment)
            catalog = _case_catalog_snapshot(group, database)
        finally:
            cleanup_ok = _drop_case_database(group, database)
        failure_count = sum(summary["lock_failure_occurrences"] + summary["create_table_failure_occurrences"] + summary["traceback_occurrences"] for summary in concurrent["log_summaries"])
        ok = prepared["initial_table_count"] == 0 and concurrent_init_contract_ok(concurrent["exit_codes"], catalog["table_count"], failure_count) and catalog["table_contract_ok"] and cleanup_ok
        findings = []
        if not ok:
            findings.append(
                {
                    "id": "SM-CONCURRENCY-001",
                    "summary": ("one or both concurrent initialization processes did not complete cleanly under the database initialization lock"),
                    "lock_failure_count": sum(item["lock_failure_occurrences"] for item in concurrent["log_summaries"]),
                }
            )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "create_empty_case_database",
                    "resource": database,
                    "observed": prepared,
                },
                {
                    "name": "start_two_initializers_concurrently",
                    "observed": concurrent,
                },
                {"name": "read_only_catalog_validation", "observed": catalog},
                {"name": "exact_case_cleanup", "completed": cleanup_ok},
            ],
            oracle={
                "initializer_exit_codes": [0, 0],
                "expected_table_count": 40,
                "lock_or_ddl_failure_count": 0,
            },
            findings=findings,
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-003.json", result)
    return result


def run_sm004() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-004", CASE_TITLES["TC-SM-004"])
    for group in GROUP_ORDER:
        database = case_database_name("TC-SM-004", group)
        prepared = _prepare_case_database(group, database)
        cleanup_ok = False
        try:
            _root, environment = _prepare_case_runtime("TC-SM-004", group, database)
            initial = _run_single_initializer(
                "TC-SM-004",
                group,
                environment,
                "initial_full",
                include_default_data=True,
            )
            rows_before = _migration_row_counts(group, database)
            column_before = _content_hash_snapshot(group, database)
            dropped = _drop_content_hash(group, database)
            missing_snapshot = _content_hash_snapshot(group, database)
            migration = _run_single_initializer("TC-SM-004", group, environment, "migration")
            column_after = _content_hash_snapshot(group, database)
            rows_after = _migration_row_counts(group, database)
        finally:
            cleanup_ok = _drop_case_database(group, database)
        failure_count = sum(item["lock_failure_occurrences"] + item["create_table_failure_occurrences"] + item["traceback_occurrences"] for item in (initial["log_summary"], migration["log_summary"]))
        ok = (
            prepared["initial_table_count"] == 0
            and initial["exit_code"] == 0
            and content_hash_contract_ok(column_before)
            and dropped
            and missing_snapshot["exists"] is False
            and migration["exit_code"] == 0
            and content_hash_contract_ok(column_after)
            and row_counts_preserved(rows_before, rows_after)
            and failure_count == 0
            and cleanup_ok
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "create_and_initialize_case_database",
                    "resource": database,
                    "baseline": prepared,
                    "initializer": initial,
                },
                {
                    "name": "snapshot_rows_and_column_before_mutation",
                    "row_counts": rows_before,
                    "column": column_before,
                },
                {
                    "name": "drop_document_content_hash",
                    "completed": dropped,
                    "missing_snapshot": missing_snapshot,
                },
                {
                    "name": "run_migration",
                    "initializer": migration,
                },
                {
                    "name": "validate_restored_column_and_rows",
                    "column": column_after,
                    "row_counts": rows_after,
                    "rows_preserved": row_counts_preserved(rows_before, rows_after),
                },
                {"name": "exact_case_cleanup", "completed": cleanup_ok},
            ],
            oracle={
                "column_type": "VARCHAR(32)",
                "nullable": True,
                "indexed": True,
                "existing_rows_preserved": True,
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-004.json", result)
    return result


def run_sm005() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-005", CASE_TITLES["TC-SM-005"])
    for group in GROUP_ORDER:
        database = case_database_name("TC-SM-005", group)
        prepared = _prepare_case_database(group, database)
        cleanup_ok = False
        initialization_failed = False
        try:
            _root, environment = _prepare_case_runtime("TC-SM-005", group, database)
            initial = _run_initializer_with_transient_retry("TC-SM-005", group, environment, "initial")
            if initial["exit_code"] != 0:
                initialization_failed = True
            else:
                mutation = _mutate_tenant_llm_to_legacy(group, database)
                legacy_snapshot = _tenant_llm_snapshot(group, database)
                migration = _run_initializer_with_transient_retry("TC-SM-005", group, environment, "migration")
                migrated_snapshot = _tenant_llm_snapshot(group, database)
        finally:
            cleanup_ok = _drop_case_database(group, database)
        if initialization_failed:
            recorder.add_group(
                group,
                "FAIL",
                [
                    {
                        "name": "initialize_current_schema",
                        "resource": database,
                        "baseline": prepared,
                        "initializer": initial,
                    },
                    {"name": "exact_case_cleanup", "completed": cleanup_ok},
                ],
                oracle={"initializer_exit_code": 0},
                findings=[
                    {
                        "id": "SM005-ENV-INITIALIZER",
                        "summary": "case initializer failed before legacy schema construction",
                    }
                ],
            )
            continue
        legacy_ok = legacy_snapshot["id_exists"] is False and legacy_snapshot["primary_columns"] == ["tenant_id", "llm_factory", "llm_name"] and legacy_snapshot["fixture_count"] == 1
        log_failures = sum(item["lock_failure_occurrences"] + item["create_table_failure_occurrences"] + item["traceback_occurrences"] for item in (initial["log_summary"], migration["log_summary"]))
        ok = (
            prepared["initial_table_count"] == 0
            and initial["exit_code"] == 0
            and mutation["fixture_inserted"]
            and legacy_ok
            and migration["exit_code"] == 0
            and tenant_llm_migration_contract_ok(migrated_snapshot, group)
            and log_failures == 0
            and cleanup_ok
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "initialize_current_schema",
                    "resource": database,
                    "baseline": prepared,
                    "initializer": initial,
                },
                {
                    "name": "construct_legacy_composite_primary_key",
                    "mutation": mutation,
                    "catalog": legacy_snapshot,
                },
                {"name": "run_primary_key_migration", "initializer": migration},
                {
                    "name": "validate_id_primary_key_and_fixture",
                    "catalog": migrated_snapshot,
                    "contract_ok": tenant_llm_migration_contract_ok(migrated_snapshot, group),
                },
                {"name": "exact_case_cleanup", "completed": cleanup_ok},
            ],
            oracle={
                "id_primary_key": True,
                "composite_unique": True,
                "fixture_preserved_with_id": True,
                "identity_mechanism": ("AUTO_INCREMENT" if group == "control" else "tenant_llm_id_seq"),
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-005.json", result)
    return result


def run_sm006() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-006", CASE_TITLES["TC-SM-006"])
    ports = {"control": 19386, "experiment": 19486}
    for group in GROUP_ORDER:
        database = case_database_name("TC-SM-006", group)
        prepared = _prepare_case_database(group, database)
        process = None
        cleanup_ok = False
        api_stopped = False
        try:
            root, environment = _prepare_case_runtime("TC-SM-006", group, database, api_port=ports[group])
            initial = _run_initializer_with_transient_retry("TC-SM-006", group, environment, "initial")
            unique_before = _email_unique_index_count(group, database)
            dropped = _drop_email_unique_index(group, database)
            unique_after_drop = _email_unique_index_count(group, database)
            redis_before = _flush_case_redis(root)
            process, api_log_path, ready_seconds = _launch_case_api("TC-SM-006", group, root, environment, ports[group])
            unique_after_migration = _email_unique_index_count(group, database)
            registrations = _register_duplicate_email(group, root, ports[group])
            fixture_rows = _email_fixture_row_count(group, database, registrations["fixture_email"])
            api_stopped = _stop_case_api(process)
            process = None
            redis_after = _flush_case_redis(root)
            api_log_summary = summarize_init_log(api_log_path.read_text(encoding="utf-8", errors="replace"))
            api_log_sha256 = _sha256(api_log_path)
        finally:
            if process is not None:
                api_stopped = _stop_case_api(process)
            cleanup_ok = _drop_case_database(group, database)
        ok = (
            prepared["initial_table_count"] == 0
            and initial["exit_code"] == 0
            and unique_before >= 1
            and dropped["dropped_count"] >= 1
            and unique_after_drop == 0
            and email_unique_contract_ok(
                unique_index_count=unique_after_migration,
                first_api_code=int(registrations["first_code"]),
                duplicate_api_code=int(registrations["duplicate_code"]),
                database_row_count=fixture_rows,
            )
            and api_stopped
            and redis_after["after"] == 0
            and cleanup_ok
        )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "initialize_case_table_structure",
                    "resource": database,
                    "baseline": prepared,
                    "initializer": initial,
                },
                {
                    "name": "drop_user_email_unique_index",
                    "unique_before": unique_before,
                    "mutation": dropped,
                    "unique_after_drop": unique_after_drop,
                },
                {
                    "name": "start_case_api_and_run_migration",
                    "ready_seconds": round(ready_seconds, 3),
                    "unique_after_migration": unique_after_migration,
                    "log_summary": api_log_summary,
                    "raw_log_sha256": api_log_sha256,
                },
                {
                    "name": "register_duplicate_email_via_api",
                    "observed": registrations,
                    "database_row_count": fixture_rows,
                },
                {
                    "name": "stop_api_and_clean_case_resources",
                    "api_stopped": api_stopped,
                    "redis_before": redis_before,
                    "redis_after": redis_after,
                    "database_cleaned": cleanup_ok,
                },
            ],
            oracle={
                "unique_index_restored": True,
                "first_registration_code": 0,
                "duplicate_registration_rejected": True,
                "single_fixture_row": True,
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-006.json", result)
    return result


def run_sm007() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-007", CASE_TITLES["TC-SM-007"])
    for group in GROUP_ORDER:
        database = case_database_name("TC-SM-007", group)
        prepared = _prepare_case_database(group, database)
        cleanup_ok = False
        try:
            _root, environment = _prepare_case_runtime("TC-SM-007", group, database)
            initial = _run_initializer_with_transient_retry("TC-SM-007", group, environment, "initial")
            created = _create_legacy_indexes(group, database)
            migration = _run_initializer_with_transient_retry("TC-SM-007", group, environment, "migration")
            after = _legacy_index_presence(group, database)
        finally:
            cleanup_ok = _drop_case_database(group, database)
        ok = prepared["initial_table_count"] == 0 and initial["exit_code"] == 0 and all(created.values()) and migration["exit_code"] == 0 and legacy_indexes_removed(after) and cleanup_ok
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "initialize_case_database",
                    "resource": database,
                    "baseline": prepared,
                    "initializer": initial,
                },
                {
                    "name": "create_three_declared_legacy_indexes",
                    "presence": created,
                },
                {"name": "run_migration", "initializer": migration},
                {
                    "name": "verify_legacy_indexes_absent",
                    "presence": after,
                    "all_removed": legacy_indexes_removed(after),
                },
                {"name": "exact_case_cleanup", "completed": cleanup_ok},
            ],
            oracle={"all_three_legacy_indexes_removed": True},
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-007.json", result)
    return result


def run_sm008() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-008", CASE_TITLES["TC-SM-008"])
    for group in GROUP_ORDER:
        database = case_database_name("TC-SM-008", group)
        prepared = _prepare_case_database(group, database)
        cleanup_ok = False
        try:
            probe = _run_empty_string_probe(group, database)
        finally:
            cleanup_ok = _drop_case_database(group, database)
        ok = prepared["initial_table_count"] == 0 and empty_string_contract_ok(group, probe) and probe["table_cleaned"] and cleanup_ok
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "create_empty_case_database",
                    "resource": database,
                    "observed": prepared,
                },
                {
                    "name": "create_not_null_varchar_and_insert_empty_string",
                    "observed": probe,
                },
                {"name": "exact_case_cleanup", "completed": cleanup_ok},
            ],
            oracle={
                "control": "empty string stored as non-null length zero",
                "experiment": "A/ORA mode raises SQLSTATE 23502",
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-008.json", result)
    return result


def run_sm009() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-009", CASE_TITLES["TC-SM-009"])
    for group in GROUP_ORDER:
        snapshot = _nullable_field_snapshot(group)
        ok = nullable_field_contract_ok(group, snapshot)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "read_only_information_schema_query",
                    "field_count": len(snapshot),
                    "nullable_by_field": snapshot,
                }
            ],
            oracle={
                "expected_field_count": len(EMPTY_STRING_FIELDS),
                "all_nullable_required": group == "experiment",
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-009.json", result)
    return result


def run_sm010() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-010", CASE_TITLES["TC-SM-010"])
    for group in GROUP_ORDER:
        observed = _run_empty_embedding_api_flow(group)
        ok = orm_empty_string_roundtrip_contract_ok(group, observed)
        findings = []
        if not ok:
            findings.append(
                {
                    "id": "SM-EMPTY-ROUNDTRIP-001",
                    "summary": ("empty embedding storage or REST round-trip did not match the group-specific compatibility contract"),
                }
            )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "register_dedicated_user_and_read_tenant_default",
                    "registration_http_status": observed.get("registration_http_status"),
                    "registration_code": observed.get("registration_code"),
                    "tenant_api_http_status": observed.get("tenant_api_http_status"),
                    "tenant_api_code": observed.get("tenant_api_code"),
                    "tenant_api_value": observed.get("tenant_api_value"),
                    "tenant_storage": observed.get("tenant_storage"),
                },
                {
                    "name": "create_dataset_without_embedding_model_via_api",
                    "http_status": observed.get("dataset_create_http_status"),
                    "code": observed.get("dataset_create_code"),
                    "api_value": observed.get("dataset_create_api_value"),
                    "dataset_storage": observed.get("dataset_storage"),
                },
                {
                    "name": "read_dataset_via_api",
                    "http_status": observed.get("dataset_get_http_status"),
                    "code": observed.get("dataset_get_code"),
                    "embedding_model": observed.get("dataset_api_value"),
                },
                {
                    "name": "cleanup_via_dataset_and_admin_apis",
                    "dataset_delete_http_status": observed.get("dataset_delete_http_status"),
                    "dataset_delete_code": observed.get("dataset_delete_code"),
                    "admin_cleanup": observed.get("admin_cleanup"),
                    "cleanup_counts": observed.get("cleanup_counts"),
                    "complete": observed.get("user_cleanup_complete"),
                },
                {
                    "name": "unexpected_execution_errors",
                    "execution_error_type": observed.get("execution_error_type"),
                    "dataset_cleanup_error_type": observed.get("dataset_cleanup_error_type"),
                    "user_cleanup_error_type": observed.get("user_cleanup_error_type"),
                },
            ],
            oracle={
                "control_storage": "non-null empty string of length zero",
                "experiment_storage": "NULL under A-compatible GaussDB",
                "tenant_api_embd_id": "",
                "dataset_api_embedding_model": "",
                "all_business_mutations_use_api": True,
                "fixture_cleanup_required": True,
            },
            findings=findings,
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-010.json", result)
    return result


def run_sm011() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-011", CASE_TITLES["TC-SM-011"])
    api_scope = _memory_list_filter_scope()
    for group in GROUP_ORDER:
        compiled = _compile_empty_string_query(group)
        ok = compiled["exit_code"] == 0 and empty_string_query_contract_ok(group, compiled) and api_scope["function_found"] and not api_scope["embd_id_filter_referenced"]
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "compile_memory_empty_embedding_query",
                    "observed": compiled,
                },
                {
                    "name": "confirm_memory_list_api_filter_scope",
                    "observed": api_scope,
                },
            ],
            oracle={
                "control": "ordinary equality with one empty-string parameter",
                "experiment": "IS NULL OR LENGTH(field)=0 without empty-string parameter",
                "list_api_embd_id_filter_expected": False,
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-011.json", result)
    return result


def run_sm012() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-012", CASE_TITLES["TC-SM-012"])
    for group in GROUP_ORDER:
        probe = _run_mysql_migration_guard_probe(group)
        startup_log = _startup_mysql_only_log_counts(group)
        ok = mysql_migration_guard_contract_ok(group, probe, startup_log["counts"])
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "execute_current_launch_script_migration_guard",
                    "observed": probe,
                    "probe_python": "/bin/echo",
                },
                {
                    "name": "scan_fresh_initial_startup_log",
                    "observed": startup_log,
                },
            ],
            oracle={
                "control_mysql_migration_invocation_count": 1,
                "experiment_mysql_migration_invocation_count": 0,
                "experiment_mysql_only_startup_log_occurrences": 0,
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-012.json", result)
    return result


def run_sm013() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-013", CASE_TITLES["TC-SM-013"])
    for group in GROUP_ORDER:
        snapshot = _ddl_dialect_snapshot(group)
        ok = ddl_dialect_contract_ok(group, snapshot)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [{"name": "read_only_column_catalog", "observed": snapshot}],
            oracle={
                "control": "MySQL AUTO_INCREMENT, DATETIME and LONGTEXT baseline",
                "experiment": ("nextval, timestamp and text present; zero MySQL-only column types"),
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-013.json", result)
    return result


def run_sm014() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-014", CASE_TITLES["TC-SM-014"])
    for group in GROUP_ORDER:
        snapshot = _index_dialect_snapshot(group)
        ok = index_dialect_contract_ok(group, snapshot)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [{"name": "read_only_index_catalog", "observed": snapshot}],
            oracle={
                "control": "MySQL statistics catalog and backtick SHOW CREATE baseline",
                "experiment": ("pg_indexes definitions with zero backticks and zero MySQL-only syntax"),
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-014.json", result)
    return result


def run_sm015() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-015", CASE_TITLES["TC-SM-015"])
    for group in GROUP_ORDER:
        probe = _database_alias_probe(group)
        observed = probe["payload"]
        ok = probe["exit_code"] == 0 and database_alias_contract_ok(observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "isolated_current_settings_alias_probe",
                    "normalized_by_input": observed,
                    "stdout_sha256": probe["stdout_sha256"],
                    "stderr_sha256": probe["stderr_sha256"],
                }
            ],
            oracle={
                "GaussDB": "gaussdb",
                "gauss": "gaussdb",
                "GAUSSDB": "gaussdb",
                "gaussdb": "gaussdb",
                "postgresql": "postgres",
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-015.json", result)
    return result


def run_sm016() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-016", CASE_TITLES["TC-SM-016"])
    for group in GROUP_ORDER:
        probe = _schema_validation_probe(group)
        observed = probe["payload"]
        accepted_have_search_path = all(not item.get("accepted") or item.get("search_path_present") is True for item in observed.values())
        ok = probe["exit_code"] == 0 and schema_validation_contract_ok(observed) and accepted_have_search_path
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "configuration_load_schema_validation_probe",
                    "observed_by_input": observed,
                    "stdout_sha256": probe["stdout_sha256"],
                    "stderr_sha256": probe["stderr_sha256"],
                }
            ],
            oracle={
                "accepted": ["ragflow_meta", "public", "_test_schema", ""],
                "empty_default": "public",
                "rejected": [
                    "test; DROP TABLE",
                    "test-schema",
                    "123schema",
                ],
                "rejection_error_type": "ValueError",
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-016.json", result)
    return result


def run_sm017() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-017", CASE_TITLES["TC-SM-017"])
    for group in GROUP_ORDER:
        probe = _config_isolation_probe(group)
        observed = probe["payload"]
        ok = probe["exit_code"] == 0 and config_isolation_contract_ok(group, observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "load_metadata_and_docstore_namespaces_independently",
                    "observed": observed,
                    "stdout_sha256": probe["stdout_sha256"],
                    "stderr_sha256": probe["stderr_sha256"],
                }
            ],
            oracle={
                "fake_metadata_target": "meta-host/meta_db",
                "fake_docstore_target": "doc-host/doc_db",
                "control_selection": "mysql metadata plus infinity DocEngine",
                "experiment_selection": "distinct GaussDB metadata and DocEngine targets",
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-017.json", result)
    return result


def run_sm018() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-018", CASE_TITLES["TC-SM-018"])
    for group in GROUP_ORDER:
        observed = _admin_service_display_probe(group)
        ok = admin_service_display_contract_ok(group, observed)
        findings = []
        if observed["credential_exposure_count"]:
            findings.append(
                {
                    "id": "SM-ADMIN-SECRET-001",
                    "summary": ("authenticated Admin services response exposes one or more configured credentials in plaintext"),
                    "exposed_categories": [category for category, exposed in observed["credential_exposure_categories"].items() if exposed],
                }
            )
        if group == "experiment" and observed["retrieval_service_count"] != 1:
            findings.append(
                {
                    "id": "SM-ADMIN-GAUSS-RETRIEVAL-001",
                    "summary": ("Admin services response omits the active GaussDB retrieval service"),
                    "observed_count": observed["retrieval_service_count"],
                }
            )
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "login_and_get_admin_services",
                    "http_status": observed["http_status"],
                    "code": observed["code"],
                    "service_count": observed["service_count"],
                    "raw_response_sha256": observed["raw_response_sha256"],
                },
                {
                    "name": "validate_active_metadata_and_retrieval_configuration",
                    "metadata_service_count": observed["metadata_service_count"],
                    "retrieval_service_count": observed["retrieval_service_count"],
                    "metadata_type": observed["metadata_type"],
                    "retrieval_type": observed["retrieval_type"],
                    "metadata_matches_expected": observed["metadata_matches_expected"],
                    "retrieval_matches_expected": observed["retrieval_matches_expected"],
                    "services": observed["safe_service_summaries"],
                },
                {
                    "name": "scan_response_for_known_credentials",
                    "exposure_by_category": observed["credential_exposure_categories"],
                    "exposure_count": observed["credential_exposure_count"],
                },
            ],
            oracle={
                "control_types": {"metadata": "mysql", "retrieval": "infinity"},
                "experiment_types": {
                    "metadata": "gaussdb",
                    "retrieval": "gaussdb",
                },
                "host_port_user_schema_match": True,
                "plaintext_credential_exposure_count": 0,
            },
            findings=findings,
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-018.json", result)
    return result


def run_sm019() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-019", CASE_TITLES["TC-SM-019"])
    for group in GROUP_ORDER:
        api_observed = _admin_metadata_detail_probe(group)
        trace = _health_sql_trace_probe(group)
        observed = {
            **api_observed,
            "executed_sql": trace["payload"].get("executed_sql"),
            "trace_status": trace["payload"].get("status"),
        }
        ok = trace["exit_code"] == 0 and trace["payload"].get("status") == "alive" and admin_metadata_health_contract_ok(group, observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "get_real_admin_metadata_service_detail",
                    "metadata_service_id": observed["metadata_service_id"],
                    "metadata_type": observed["metadata_type"],
                    "http_status": observed["detail_http_status"],
                    "code": observed["detail_code"],
                    "status": observed["detail_status"],
                    "message_kind": observed["detail_message_kind"],
                    "database": observed["detail_database"],
                    "result": observed["detail_result"],
                    "raw_response_sha256": observed["detail_raw_sha256"],
                },
                {
                    "name": "capture_health_function_sql_with_fake_cursor",
                    "executed_sql": observed["executed_sql"],
                    "function_status": observed["trace_status"],
                    "stdout_sha256": trace["stdout_sha256"],
                    "stderr_sha256": trace["stderr_sha256"],
                },
            ],
            oracle={
                "control_sql": ["SHOW PROCESSLIST;"],
                "experiment_sql": ["SELECT 1;"],
                "real_detail_status": "alive",
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-019.json", result)
    return result


def run_sm020() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-020", CASE_TITLES["TC-SM-020"])
    for group in GROUP_ORDER:
        observed = _compose_profile_probe(group)
        ok = compose_profile_contract_ok(group, observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "render_active_compose_services",
                    "profiles": observed["profiles"],
                    "exit_code": observed["services_exit_code"],
                    "active_services": observed["active_services"],
                },
                {
                    "name": "render_and_parse_full_compose_config",
                    "exit_code": observed["config_exit_code"],
                    "mysql_present": observed["mysql_present"],
                    "infinity_present": observed["infinity_present"],
                    "ragflow_cpu_present": observed["ragflow_cpu_present"],
                    "mysql_dependency_present": observed["mysql_dependency_present"],
                    "mysql_dependency_required": observed["mysql_dependency_required"],
                    "gauss_metadata_environment_present": observed["gauss_metadata_environment_present"],
                    "application_environment_matches": observed["application_environment_matches"],
                    "safe_application_environment": observed["safe_application_environment"],
                    "raw_sha256": observed["raw_sha256"],
                },
            ],
            oracle={
                "control_services_include": ["mysql", "infinity", "ragflow-cpu"],
                "experiment_services_exclude": ["mysql", "infinity"],
                "experiment_services_include": ["ragflow-cpu"],
                "mysql_dependency_required": False,
                "gauss_metadata_environment_propagated": True,
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-020.json", result)
    return result


def run_sm021() -> dict[str, Any]:
    evidence = _load_case_evidence_module()
    recorder = evidence.CaseRecorder("TC-SM-021", CASE_TITLES["TC-SM-021"])
    for group in GROUP_ORDER:
        observed = _helm_metadata_probe(group)
        ok = helm_metadata_contract_ok(group, observed)
        recorder.add_group(
            group,
            "PASS" if ok else "FAIL",
            [
                {
                    "name": "render_helm_chart_with_group_values",
                    "helm_version": observed["helm_version"],
                    "exit_code": observed["template_exit_code"],
                    "document_count": observed["document_count"],
                    "environment_config_resource_count": observed["environment_config_resource_count"],
                    "ragflow_workload_count": observed["ragflow_workload_count"],
                    "db_type": observed["db_type"],
                    "stdout_sha256": observed["stdout_sha256"],
                    "stderr_sha256": observed["stderr_sha256"],
                },
                {
                    "name": "validate_mysql_and_gauss_metadata_resources",
                    "mysql_resource_count": observed["mysql_resource_count"],
                    "mysql_resource_kinds": observed["mysql_resource_kinds"],
                    "mysql_credential_keys_present": observed["mysql_credential_keys_present"],
                    "gauss_metadata_keys_present": observed["gauss_metadata_keys_present"],
                    "gauss_metadata_values_match": observed["gauss_metadata_values_match"],
                    "safe_gauss_metadata": observed["safe_gauss_metadata"],
                },
            ],
            oracle={
                "control": "MySQL workload and credential keys rendered",
                "experiment": ("zero MySQL resources/credential keys and complete GaussDB metadata env"),
            },
        )
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / "TC-SM-021.json", result)
    return result


def get_runners() -> dict[str, Callable[[], dict[str, Any]]]:
    return {
        "TC-SM-001": run_sm001,
        "TC-SM-002": run_sm002,
        "TC-SM-003": run_sm003,
        "TC-SM-004": run_sm004,
        "TC-SM-005": run_sm005,
        "TC-SM-006": run_sm006,
        "TC-SM-007": run_sm007,
        "TC-SM-008": run_sm008,
        "TC-SM-009": run_sm009,
        "TC-SM-010": run_sm010,
        "TC-SM-011": run_sm011,
        "TC-SM-012": run_sm012,
        "TC-SM-013": run_sm013,
        "TC-SM-014": run_sm014,
        "TC-SM-015": run_sm015,
        "TC-SM-016": run_sm016,
        "TC-SM-017": run_sm017,
        "TC-SM-018": run_sm018,
        "TC-SM-019": run_sm019,
        "TC-SM-020": run_sm020,
        "TC-SM-021": run_sm021,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh startup and migration cases")
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
