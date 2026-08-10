#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from Cryptodome.Cipher import PKCS1_v1_5
from Cryptodome.PublicKey import RSA
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
DEFAULT_RUNTIME = RUNTIME_DIR
DEFAULT_EVIDENCE = evidence_dir("00_environment_setup") / "environment_validation.json"
EXPECTED_SERVICES = tuple(f"{group}:{service}" for group in GROUP_ORDER for service in ("api", "admin", "worker", "sync"))
KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")


def fingerprint(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _load_yaml(path: Path) -> dict[str, Any]:
    result = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"expected mapping in {path.name}")
    return result


def _split_host_port(value: str) -> tuple[str, int]:
    host, raw_port = value.rsplit(":", 1)
    return host, int(raw_port)


def summarize_health_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    type_fields = {
        "doc_engine": "type",
        "storage": "storage",
        "database": "database",
        "redis": None,
    }
    for component, type_field in type_fields.items():
        item = payload.get(component)
        if not isinstance(item, dict):
            item = {}
        result[component] = {
            "type": item.get(type_field) if type_field else None,
            "status": item.get("status"),
            "elapsed_ms": item.get("elapsed"),
        }
        if component == "doc_engine":
            result[component].update(
                {
                    "server_encoding": item.get("server_encoding"),
                    "client_encoding": item.get("client_encoding"),
                    "warning": item.get("warning"),
                }
            )
    heartbeats = payload.get("task_executor_heartbeats")
    result["task_executor_count"] = len(heartbeats) if isinstance(heartbeats, dict) else 0
    return result


def component_is_healthy(component: dict[str, Any]) -> bool:
    return component.get("status") in {"green", "healthy"}


def public_conflict_oracle_ok(metadata_schema: str, conflict_count: int, conflict_row_count: int) -> bool:
    if metadata_schema == "public":
        return True
    return conflict_count >= 0 and conflict_row_count == 0


def scan_log_text(text: str, known_secrets: list[str]) -> dict[str, int]:
    secrets = [item for item in known_secrets if len(item) >= 8]
    return {
        "known_secret_occurrences": sum(text.count(secret) for secret in secrets),
        "key_pattern_occurrences": len(KEY_PATTERN.findall(text)),
        "traceback_occurrences": text.count("Traceback (most recent call last):"),
        "error_occurrences": len(re.findall(r"\bERROR\b", text, flags=re.IGNORECASE)),
    }


def scan_log_path(path: Path, known_secrets: list[str], *, block_size: int = 4 * 1024 * 1024) -> dict[str, int]:
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    totals = {
        "known_secret_occurrences": 0,
        "key_pattern_occurrences": 0,
        "traceback_occurrences": 0,
        "error_occurrences": 0,
    }
    carry = ""
    with path.open("r", encoding="utf-8", errors="replace", buffering=block_size) as handle:
        while chunk := handle.read(block_size):
            text = carry + chunk
            boundary = text.rfind("\n")
            if boundary < 0:
                carry = text
                continue
            complete = text[: boundary + 1]
            carry = text[boundary + 1 :]
            for key, count in scan_log_text(complete, known_secrets).items():
                totals[key] += count
    if carry:
        for key, count in scan_log_text(carry, known_secrets).items():
            totals[key] += count
    return totals


def _process_start_ticks(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    closing = raw.rfind(")")
    fields = raw[closing + 2 :].split() if closing >= 0 else []
    return fields[19] if len(fields) > 19 else None


def _process_state(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None
    closing = raw.rfind(")")
    fields = raw[closing + 2 :].split() if closing >= 0 else []
    return fields[0] if fields else None


def validate_processes(runtime: Path) -> dict[str, Any]:
    state = json.loads((runtime / "processes.json").read_text(encoding="utf-8"))
    services = {}
    for label in EXPECTED_SERVICES:
        entry = state.get(label) or {}
        pid = int(entry.get("pid", 0))
        ticks = _process_start_ticks(pid) if pid > 1 else None
        alive = ticks is not None and _process_state(pid) != "Z"
        identity_matches = alive and ticks == str(entry.get("start_ticks"))
        argv = entry.get("command") if isinstance(entry.get("command"), list) else []
        services[label] = {
            "pid": pid,
            "alive": alive,
            "identity_matches": identity_matches,
            "argv": argv,
            "argv_fingerprint": fingerprint(json.dumps(argv, sort_keys=True)),
            "started_at_unix": entry.get("started_at_unix"),
        }
    return {
        "all_alive": all(item["alive"] and item["identity_matches"] for item in services.values()),
        "service_count": len(services),
        "services": services,
    }


def _encrypt_password(password: str, public_key_path: Path) -> str:
    key = RSA.import_key(public_key_path.read_text(encoding="utf-8"), "Welcome")
    encoded = base64.b64encode(password.encode("utf-8"))
    return base64.b64encode(PKCS1_v1_5.new(key).encrypt(encoded)).decode("ascii")


def _api_body(response) -> dict[str, Any]:
    payload = response.json()
    if response.status_code >= 400 or not isinstance(payload, dict) or payload.get("code") != 0:
        raise RuntimeError("API validation request failed")
    return payload


def validate_group_api(group: str, runtime: Path, environment: dict[str, str]) -> dict[str, Any]:
    api_port, admin_port = (9380, 9381) if group == "control" else (9480, 9481)
    api_base = f"http://127.0.0.1:{api_port}"
    session = requests.Session()
    ping = session.get(api_base + "/api/v1/system/ping", timeout=10)
    admin_ping = session.get(f"http://127.0.0.1:{admin_port}/api/v1/admin/ping", timeout=10)
    login = session.post(
        api_base + "/api/v1/auth/login",
        json={
            "email": environment["DEFAULT_SUPERUSER_EMAIL"],
            "password": _encrypt_password(
                environment["DEFAULT_SUPERUSER_PASSWORD"],
                runtime / group / "conf" / "public.pem",
            ),
        },
        timeout=30,
    )
    _api_body(login)
    token = login.headers.get("Authorization", "")
    if not token:
        raise RuntimeError("login returned no authorization token")
    session.headers.update({"Authorization": f"Bearer {token}"})
    status_body = _api_body(session.get(api_base + "/api/v1/system/status", timeout=30))
    defaults_body = _api_body(session.get(api_base + "/api/v1/models/default", timeout=30))
    health = summarize_health_payload(status_body.get("data") or {})
    defaults_data = defaults_body.get("data") or {}
    defaults = defaults_data.get("models") if isinstance(defaults_data, dict) else []
    safe_defaults = sorted(
        (
            {
                "model_provider": item.get("model_provider"),
                "model_instance": item.get("model_instance"),
                "model_name": item.get("model_name"),
                "model_type": item.get("model_type"),
                "enable": item.get("enable"),
            }
            for item in defaults
            if isinstance(item, dict)
        ),
        key=lambda item: str(item["model_type"]),
    )
    component_green = all(component_is_healthy(health[name]) for name in ("doc_engine", "storage", "database", "redis"))
    admin_body = admin_ping.json() if admin_ping.ok else {}
    ready = (
        ping.status_code == 200
        and ping.text == "pong"
        and admin_ping.status_code == 200
        and isinstance(admin_body, dict)
        and admin_body.get("code") == 0
        and admin_body.get("message") == "pong"
        and component_green
        and health["task_executor_count"] >= 1
    )
    return {
        "ready": ready,
        "api_ping": ping.status_code == 200 and ping.text == "pong",
        "admin_ping": admin_body.get("code") == 0 and admin_body.get("message") == "pong",
        "auth_token_fingerprint": fingerprint(token),
        "health": health,
        "defaults": safe_defaults,
    }


def _count_selected_tables(cursor, table_names: set[str]) -> dict[str, int]:
    counts = {}
    for name in ("tenant_model_provider", "tenant_model_instance", "tenant_model"):
        if name in table_names:
            cursor.execute(f"SELECT COUNT(*) FROM {name}")
            counts[name] = int(cursor.fetchone()[0])
    return counts


def validate_control_data(config: dict[str, Any]) -> dict[str, Any]:
    import infinity
    import pymysql
    import redis
    from infinity.common import NetworkAddress
    from minio import Minio

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
            cursor.execute("SELECT VERSION()")
            version = str(cursor.fetchone()[0])[:160]
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                (mysql["name"],),
            )
            tables = {row[0] for row in cursor.fetchall()}
            selected_counts = _count_selected_tables(cursor, tables)
    finally:
        connection.close()

    infinity_cfg = config["infinity"]
    infinity_host, infinity_port = _split_host_port(str(infinity_cfg["uri"]))
    infinity_conn = infinity.connect(NetworkAddress(infinity_host, infinity_port))
    try:
        infinity_present = infinity_cfg["db_name"] in set(infinity_conn.list_databases().db_names)
    finally:
        infinity_conn.disconnect()

    redis_cfg = config["redis"]
    redis_host, redis_port = _split_host_port(str(redis_cfg["host"]))
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        db=int(redis_cfg["db"]),
        username=redis_cfg.get("username") or None,
        password=redis_cfg.get("password") or None,
        socket_connect_timeout=5,
    )

    minio_cfg = config["minio"]
    minio_client = Minio(
        str(minio_cfg["host"]).removeprefix("http://").removeprefix("https://"),
        access_key=minio_cfg["user"],
        secret_key=minio_cfg["password"],
        secure=bool(minio_cfg.get("secure", False)),
    )
    object_count = sum(1 for _ in minio_client.list_objects(minio_cfg["bucket"], recursive=True))
    return {
        "metadata": {
            "database_matches": mysql["name"] == "rag_flow",
            "version": version,
            "table_count": len(tables),
            "selected_row_counts": selected_counts,
        },
        "docstore": {"database_present": infinity_present},
        "redis": {"db": int(redis_cfg["db"]), "key_count": int(redis_client.dbsize())},
        "minio": {"bucket_present": minio_client.bucket_exists(minio_cfg["bucket"]), "object_count": object_count},
    }


def _validate_gauss_connection(config: dict[str, Any]) -> dict[str, Any]:
    import psycopg2
    from psycopg2 import sql as psycopg2_sql

    schema = config["schema"]
    connection = psycopg2.connect(
        host=config["host"],
        port=int(config["port"]),
        dbname=config["database"],
        user=config["user"],
        password=config["password"],
        connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c search_path={schema}",
    )
    try:
        with connection.cursor() as cursor:
            # 分布式 GaussDB 不会把 libpq 启动参数中的 search_path 可靠地下发
            # 到 DN；直连校验器必须和产品 adapter 一样，在连接建立后显式 SET。
            cursor.execute(psycopg2_sql.SQL("SET search_path TO {}").format(psycopg2_sql.Identifier(schema)))
            cursor.execute("SELECT current_database(), current_schema(), version()")
            current_database, current_schema, version = cursor.fetchone()
            cursor.execute("SHOW sql_compatibility")
            compatibility = cursor.fetchone()[0]
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema=%s",
                (schema,),
            )
            tables = {row[0] for row in cursor.fetchall()}
            selected_counts = _count_selected_tables(cursor, tables)
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('user','system_settings')")
            public_conflict_tables = {str(row[0]) for row in cursor.fetchall()}
            public_conflicts = len(public_conflict_tables)
            public_conflict_rows = 0
            for table_name in sorted(public_conflict_tables):
                cursor.execute(f'SELECT COUNT(*) FROM public."{table_name}"')
                public_conflict_rows += int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {
        "database_matches": current_database == config["database"],
        "schema_matches": current_schema == schema,
        "version": str(version)[:160],
        "sql_compatibility": compatibility,
        "table_count": len(tables),
        "selected_row_counts": selected_counts,
        "public_conflicting_table_count": public_conflicts,
        "public_conflicting_row_count": public_conflict_rows,
    }


def _docstore_config(config: dict[str, Any]) -> dict[str, Any]:
    gaussdb = config.get("gaussdb") or {}
    nested = gaussdb.get("config") if isinstance(gaussdb, dict) else None
    return nested if isinstance(nested, dict) else gaussdb


def validate_experiment_data(config: dict[str, Any], environment: dict[str, str]) -> dict[str, Any]:
    import redis
    from minio import Minio

    metadata = {
        "host": environment["GAUSSDB_METADATA_HOST"],
        "port": environment["GAUSSDB_METADATA_PORT"],
        "database": environment["GAUSSDB_METADATA_DBNAME"],
        "user": environment["GAUSSDB_METADATA_USER"],
        "password": environment["GAUSSDB_METADATA_PASSWORD"],
        "schema": environment["GAUSSDB_METADATA_SCHEMA"],
    }
    docstore = _docstore_config(config)
    redis_cfg = config["redis"]
    redis_host, redis_port = _split_host_port(str(redis_cfg["host"]))
    redis_client = redis.Redis(
        host=redis_host,
        port=redis_port,
        db=int(redis_cfg["db"]),
        username=redis_cfg.get("username") or None,
        password=redis_cfg.get("password") or None,
        socket_connect_timeout=5,
    )
    minio_cfg = config["minio"]
    minio_client = Minio(
        str(minio_cfg["host"]).removeprefix("http://").removeprefix("https://"),
        access_key=minio_cfg["user"],
        secret_key=minio_cfg["password"],
        secure=bool(minio_cfg.get("secure", False)),
    )
    object_count = sum(1 for _ in minio_client.list_objects(minio_cfg["bucket"], recursive=True))
    return {
        "metadata": _validate_gauss_connection(metadata),
        "docstore": _validate_gauss_connection(docstore),
        "schema_isolation": metadata["schema"] != docstore["schema"],
        "redis": {"db": int(redis_cfg["db"]), "key_count": int(redis_client.dbsize())},
        "minio": {"bucket_present": minio_client.bucket_exists(minio_cfg["bucket"]), "object_count": object_count},
    }


def validate_isolation(
    runtime: Path,
    control: dict[str, Any],
    experiment: dict[str, Any],
    environments: dict[str, dict[str, str]],
) -> dict[str, Any]:
    control_env, experiment_env = environments["control"], environments["experiment"]
    checks = {
        "runtime_roots_distinct": control_env["RAG_PROJECT_BASE"] != experiment_env["RAG_PROJECT_BASE"],
        "api_ports_distinct": control["ragflow"]["http_port"] != experiment["ragflow"]["http_port"],
        "admin_ports_distinct": control["admin"]["http_port"] != experiment["admin"]["http_port"],
        "redis_dbs_distinct": control["redis"]["db"] != experiment["redis"]["db"],
        "minio_buckets_distinct": control["minio"]["bucket"] != experiment["minio"]["bucket"],
        "secrets_distinct": control_env["RAGFLOW_SECRET_KEY"] != experiment_env["RAGFLOW_SECRET_KEY"],
        "metadata_docstore_schemas_distinct": experiment_env["GAUSSDB_METADATA_SCHEMA"] != _docstore_config(experiment)["schema"],
    }
    return {
        "all_distinct": all(checks.values()),
        "checks": checks,
        "config_hashes": {group: fingerprint((runtime / group / "conf" / "service_conf.yaml").read_bytes()) for group in GROUP_ORDER},
        "environment_fingerprints": {group: fingerprint(json.dumps(environments[group], sort_keys=True)) for group in GROUP_ORDER},
    }


def collect_log_summary(
    runtime: Path,
    configs: dict[str, dict[str, Any]],
    environments: dict[str, dict[str, str]],
) -> dict[str, Any]:
    result = {}
    for group in GROUP_ORDER:
        config = configs[group]
        environment = environments[group]
        secrets = [
            environment.get("DEFAULT_SUPERUSER_PASSWORD", ""),
            environment.get("RAGFLOW_SECRET_KEY", ""),
            environment.get("GAUSSDB_METADATA_PASSWORD", ""),
            config.get("mysql", {}).get("password", ""),
            _docstore_config(config).get("password", ""),
            config.get("redis", {}).get("password", ""),
            config.get("minio", {}).get("password", ""),
        ]
        for path in sorted((runtime / group / "logs").glob("managed_*.log")):
            result[f"{group}:{path.stem.removeprefix('managed_')}"] = scan_log_path(path, secrets)
    return result


def _model_setup_summary() -> dict[str, Any]:
    path = DEFAULT_EVIDENCE.parent / "model_setup.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "group_order": payload.get("group_order"),
        "results": [
            {
                "group": item.get("group"),
                "ok": item.get("ok"),
                "defaults_verified": item.get("defaults_verified"),
                "models": item.get("models"),
                "probes": item.get("probes"),
                "qwen_key_fingerprint": item.get("qwen_key_fingerprint"),
            }
            for item in payload.get("results", [])
        ],
    }


def run_validation(runtime: Path) -> dict[str, Any]:
    environments = json.loads((runtime / "private_environments.json").read_text(encoding="utf-8"))
    configs = {group: _load_yaml(runtime / group / "conf" / "service_conf.yaml") for group in GROUP_ORDER}
    processes = validate_processes(runtime)
    api = {group: validate_group_api(group, runtime, environments[group]) for group in GROUP_ORDER}
    data = {
        "control": validate_control_data(configs["control"]),
        "experiment": validate_experiment_data(configs["experiment"], environments["experiment"]),
    }
    isolation = validate_isolation(runtime, configs["control"], configs["experiment"], environments)
    model_setup = _model_setup_summary()
    logs = collect_log_summary(runtime, configs, environments)
    operational_ready = (
        processes["all_alive"]
        and all(api[group]["ready"] for group in GROUP_ORDER)
        and isolation["all_distinct"]
        and data["control"]["metadata"]["table_count"] > 0
        and data["control"]["docstore"]["database_present"]
        and data["experiment"]["metadata"]["schema_matches"]
        and data["experiment"]["metadata"]["table_count"] > 0
        and public_conflict_oracle_ok(
            environments["experiment"]["GAUSSDB_METADATA_SCHEMA"],
            data["experiment"]["metadata"]["public_conflicting_table_count"],
            data["experiment"]["metadata"]["public_conflicting_row_count"],
        )
        and data["experiment"]["docstore"]["schema_matches"]
        and data["experiment"]["schema_isolation"]
        and api["experiment"]["health"]["doc_engine"]["client_encoding"] == "UTF8"
        and api["experiment"]["health"]["doc_engine"]["server_encoding"] in {"UTF8", "SQL_ASCII"}
        and all(item.get("ok") for item in model_setup["results"])
    )
    secret_occurrences = sum(item["known_secret_occurrences"] for item in logs.values())
    return {
        "batch_id": BATCH_ID,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "group_order": list(GROUP_ORDER),
        "operational_ready": operational_ready,
        "security_findings": {
            "known_secret_occurrences_in_managed_logs": secret_occurrences,
            "has_unredacted_secret": secret_occurrences > 0,
        },
        "processes": processes,
        "api": api,
        "data": data,
        "isolation": isolation,
        "model_setup": model_setup,
        "log_summary": logs,
    }


def write_evidence(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate fresh dual-instance environment")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    result = run_validation(args.runtime)
    write_evidence(args.evidence, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["operational_ready"] else 1)


if __name__ == "__main__":
    main()
