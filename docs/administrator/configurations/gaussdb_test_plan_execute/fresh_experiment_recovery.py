#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

RESOURCE_PREFIX = f"fr_{BATCH_ID}"
RECOVERY_DATABASE = "zws_test2"
RECOVERY_METADATA_SCHEMA = f"{RESOURCE_PREFIX}_experiment_runtime_metadata"
RECOVERY_DOCSTORE_SCHEMA = f"{RESOURCE_PREFIX}_experiment_runtime_docstore"
STOP_ORDER = ("sync", "worker", "admin", "api")
START_ORDER = ("api", "admin", "worker", "sync")
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_PATH = evidence_dir("01_startup_migration") / "experiment_schema_recovery.json"
RAW_LOG_PATH = evidence_dir("01_startup_migration") / "raw" / "experiment_schema_recovery_api.log"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
RECOVERY_RESOURCE = re.compile(rf"^{re.escape(RESOURCE_PREFIX)}_experiment_runtime_(?:metadata|docstore)$")


def fingerprint(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = str(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def assert_recovery_resource(value: str) -> str:
    if not IDENTIFIER.fullmatch(value) or not RECOVERY_RESOURCE.fullmatch(value):
        raise ValueError(f"unsafe recovery resource: {value!r}")
    return value


def assert_authorized_database(value: str) -> str:
    if value != RECOVERY_DATABASE:
        raise ValueError(f"unsafe recovery database: {value!r}")
    return value


def build_recovery_payloads(config: dict[str, Any], environment: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    recovered_config = copy.deepcopy(config)
    recovered_environment = copy.deepcopy(environment)
    gaussdb = recovered_config["gaussdb"]
    docstore = gaussdb.get("config", gaussdb)
    docstore["database"] = RECOVERY_DATABASE
    docstore["schema"] = RECOVERY_DOCSTORE_SCHEMA
    recovered_environment["GAUSSDB_METADATA_DBNAME"] = RECOVERY_DATABASE
    recovered_environment["GAUSSDB_METADATA_SCHEMA"] = RECOVERY_METADATA_SCHEMA
    return recovered_config, recovered_environment


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected mapping in {path.name}")
    return payload


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    yaml = YAML()
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump(payload, stream)
    path.chmod(0o600)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


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


def _stop_experiment_services() -> dict[str, Any]:
    return {service: _service_manager("stop", "--group", "experiment", "--service", service) for service in STOP_ORDER}


def _reset_gauss_database() -> dict[str, Any]:
    import psycopg2

    database = assert_authorized_database(RECOVERY_DATABASE)
    metadata_schema = assert_recovery_resource(RECOVERY_METADATA_SCHEMA)
    docstore_schema = assert_recovery_resource(RECOVERY_DOCSTORE_SCHEMA)
    info = _parse_gauss_info(PROJECT_ROOT / "docs" / "administrator" / "configurations" / "gaussdb_info.md")
    base_kwargs = {
        "host": info["host"],
        "port": int(info["port"]),
        "user": info["user"],
        "password": info["password"],
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=off",
    }
    if info["dbname"] != database:
        raise ValueError("recovery is restricted to zws_test2")
    target = psycopg2.connect(dbname=database, **base_kwargs)
    target.autocommit = True
    with target.cursor() as cursor:
        for schema in (metadata_schema, docstore_schema):
            cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cursor.execute(f"CREATE SCHEMA {schema}")
        cursor.execute("SHOW sql_compatibility")
        compatibility = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema IN (%s, %s)",
            (metadata_schema, docstore_schema),
        )
        initial_table_count = int(cursor.fetchone()[0])
    target.close()
    return {
        "database_verified": database,
        "metadata_schema": metadata_schema,
        "metadata_schema_created": True,
        "docstore_schema_created": True,
        "sql_compatibility": compatibility,
        "initial_table_count": initial_table_count,
    }


def _reset_redis_and_minio(config: dict[str, Any]) -> dict[str, Any]:
    import redis
    from minio import Minio

    redis_cfg = config["redis"]
    redis_host, redis_port = str(redis_cfg["host"]).rsplit(":", 1)
    redis_client = redis.Redis(
        host=redis_host,
        port=int(redis_port),
        db=int(redis_cfg["db"]),
        username=redis_cfg.get("username") or None,
        password=redis_cfg.get("password") or None,
        socket_connect_timeout=5,
    )
    redis_before = int(redis_client.dbsize())
    redis_client.flushdb(asynchronous=False)

    minio_cfg = config["minio"]
    minio_client = Minio(
        str(minio_cfg["host"]).removeprefix("http://").removeprefix("https://"),
        access_key=minio_cfg["user"],
        secret_key=minio_cfg["password"],
        secure=bool(minio_cfg.get("secure", False)),
    )
    removed = 0
    for item in minio_client.list_objects(minio_cfg["bucket"], recursive=True):
        minio_client.remove_object(minio_cfg["bucket"], item.object_name)
        removed += 1
    return {
        "redis": {"before": redis_before, "after": int(redis_client.dbsize())},
        "minio": {"removed_objects": removed, "bucket_present": minio_client.bucket_exists(minio_cfg["bucket"])},
    }


def _wait_ping(url: str, timeout: float = 120) -> float:
    started = time.monotonic()
    deadline = started + timeout
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=2)
            if response.status_code == 200:
                return time.monotonic() - started
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"service did not become ready: {url}")


def _start_service(service: str) -> dict[str, Any]:
    return _service_manager("start", "--group", "experiment", "--service", service)


def _load_model_setup_module():
    path = EXECUTE_DIR / "fresh_model_setup.py"
    spec = importlib.util.spec_from_file_location("fresh_model_setup_recovery", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _catalog_snapshot(environment: dict[str, str]) -> dict[str, Any]:
    import psycopg2
    from psycopg2 import sql as psycopg2_sql

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
    try:
        with connection.cursor() as cursor:
            cursor.execute(psycopg2_sql.SQL("SET search_path TO {}").format(psycopg2_sql.Identifier(schema)))
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'",
                (schema,),
            )
            table_count = int(cursor.fetchone()[0])
            counts = {}
            for table in (
                "tenant_model_provider",
                "tenant_model_instance",
                "tenant_model",
            ):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                counts[table] = int(cursor.fetchone()[0])
    finally:
        connection.close()
    return {"table_count": table_count, "model_row_counts": counts}


def run_recovery(qwen_key: str) -> dict[str, Any]:
    config_path = RUNTIME_DIR / "experiment" / "conf" / "service_conf.yaml"
    environments_path = RUNTIME_DIR / "private_environments.json"
    current_config = _load_yaml(config_path)
    environments = json.loads(environments_path.read_text(encoding="utf-8"))
    recovered_config, recovered_environment = build_recovery_payloads(current_config, environments["experiment"])
    log_path = RUNTIME_DIR / "experiment" / "logs" / "managed_api.log"
    log_offset = log_path.stat().st_size

    stopped = _stop_experiment_services()
    gauss_reset = _reset_gauss_database()
    auxiliary_reset = _reset_redis_and_minio(recovered_config)
    _write_yaml(config_path, recovered_config)
    environments["experiment"] = recovered_environment
    _write_json(environments_path, environments)

    first_api_start = _start_service("api")
    first_ready_seconds = _wait_ping("http://127.0.0.1:9480/api/v1/system/ping")
    model_module = _load_model_setup_module()
    model_result = model_module.configure_group("experiment", RUNTIME_DIR, recovered_environment, qwen_key)

    _service_manager("stop", "--group", "experiment", "--service", "api")
    second_api_start = _start_service("api")
    second_ready_seconds = _wait_ping("http://127.0.0.1:9480/api/v1/system/ping")
    started = {"api": second_api_start}
    for service in START_ORDER[1:]:
        started[service] = _start_service(service)
    _wait_ping("http://127.0.0.1:9481/api/v1/admin/ping", timeout=60)
    time.sleep(8)
    status = _service_manager("status")
    experiment_status = {
        label: {
            "alive": item.get("alive"),
            "identity_matches": item.get("identity_matches"),
        }
        for label, item in status.items()
        if label.startswith("experiment:")
    }
    all_services_alive = len(experiment_status) == 4 and all(item["alive"] and item["identity_matches"] for item in experiment_status.values())
    catalog = _catalog_snapshot(recovered_environment)

    with log_path.open("rb") as stream:
        stream.seek(log_offset)
        raw_log = stream.read()
    RAW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_LOG_PATH.write_bytes(raw_log)
    RAW_LOG_PATH.chmod(0o600)

    result = {
        "batch_id": BATCH_ID,
        "reason": "recover experiment inside authorized zws_test2 schemas",
        "database": RECOVERY_DATABASE,
        "metadata_schema": RECOVERY_METADATA_SCHEMA,
        "docstore_schema": RECOVERY_DOCSTORE_SCHEMA,
        "stopped": {service: bool(item.get("stopped")) for service, item in stopped.items()},
        "gauss_reset": gauss_reset,
        "auxiliary_reset": auxiliary_reset,
        "first_api_start": {
            "alive": first_api_start.get("alive"),
            "ready_seconds": round(first_ready_seconds, 3),
        },
        "idempotent_api_restart": {
            "alive": second_api_start.get("alive"),
            "ready_seconds": round(second_ready_seconds, 3),
        },
        "model_setup": {
            "ok": model_result.get("ok"),
            "models": model_result.get("models"),
            "probes": model_result.get("probes"),
            "defaults_verified": model_result.get("defaults_verified"),
            "qwen_key_fingerprint": model_result.get("qwen_key_fingerprint"),
        },
        "services": experiment_status,
        "all_services_alive": all_services_alive,
        "catalog": catalog,
        "config_fingerprint": fingerprint(config_path.read_bytes()),
        "environment_fingerprint": fingerprint(json.dumps(recovered_environment, sort_keys=True)),
        "raw_api_log_sha256": hashlib.sha256(raw_log).hexdigest(),
    }
    result["operational_ready"] = all_services_alive and catalog["table_count"] == 40 and all(value == 2 for value in catalog["model_row_counts"].values()) and bool(model_result.get("ok"))
    _write_json(EVIDENCE_PATH, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover experiment on fresh public metadata schema")
    parser.add_argument("--qwen-key-stdin", action="store_true", required=True)
    parser.parse_args()
    qwen_key = sys.stdin.readline().rstrip("\r\n")
    if not qwen_key:
        parser.error("a non-empty key must be supplied on stdin")
    result = run_recovery(qwen_key)
    print(
        json.dumps(
            {
                "operational_ready": result["operational_ready"],
                "metadata_schema": result["metadata_schema"],
                "table_count": result["catalog"]["table_count"],
                "all_services_alive": result["all_services_alive"],
                "model_setup_ok": result["model_setup"]["ok"],
                "idempotent_restart_ready_seconds": result["idempotent_api_restart"]["ready_seconds"],
            },
            sort_keys=True,
        )
    )
    raise SystemExit(0 if result["operational_ready"] else 1)


if __name__ == "__main__":
    main()
