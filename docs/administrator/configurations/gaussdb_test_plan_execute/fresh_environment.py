#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

RESOURCE_PREFIX = f"fr_{BATCH_ID}"
RESOURCE_TOKEN = BATCH_ID.replace("_", "-")
CONTROL_MYSQL_DATABASE = "rag_flow"
CONTROL_INFINITY_DATABASE = "default_db"
EXPERIMENT_DATABASE = "zws_test2"
GAUSS_CLEAN_MAINTENANCE_WORK_MEM = "1GB"
EXPERIMENT_METADATA_SCHEMA = f"{RESOURCE_PREFIX}_experiment_metadata"
EXPERIMENT_DOCSTORE_SCHEMA = f"{RESOURCE_PREFIX}_experiment_docstore"
CONTROL_BUCKET = f"fr-{RESOURCE_TOKEN}-control"
EXPERIMENT_BUCKET = f"fr-{RESOURCE_TOKEN}-experiment"
CONTROL_REDIS_DB = 11
EXPERIMENT_REDIS_DB = 12

EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[4]
ENV_EVIDENCE_DIR = evidence_dir("00_environment_setup")

PROXY_PORTS = {
    "control_metadata": 13306,
    "control_docstore": 33817,
    "control_redis": 16381,
    "control_minio": 19000,
    "experiment_metadata": 18001,
    "experiment_docstore": 18002,
    "experiment_redis": 16382,
    "experiment_minio": 19002,
}
PROXY_CONTROL_PORT = 19990

GAUSS_INFO_KEYS = {"host", "port", "dbname", "user", "password"}
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
BATCH_RESOURCE = re.compile(rf"^{re.escape(RESOURCE_PREFIX)}_(?:control|experiment)(?:_[a-z0-9_]+)?$")
SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "access_key")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def parse_gauss_info(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise ValueError(f"invalid gauss info line {line_number}")
        key, value = (part.strip() for part in stripped.split(":", 1))
        if key in values:
            raise ValueError(f"duplicate key in gauss info: {key}")
        values[key] = value
    missing = GAUSS_INFO_KEYS - values.keys()
    extra = values.keys() - GAUSS_INFO_KEYS
    if missing or extra:
        raise ValueError(f"invalid gauss info keys; missing={sorted(missing)}, extra={sorted(extra)}")
    try:
        port = int(values["port"])
    except ValueError as exc:
        raise ValueError("invalid gauss info port") from exc
    if not 1 <= port <= 65535:
        raise ValueError("invalid gauss info port")
    if any(not values[key] for key in GAUSS_INFO_KEYS):
        raise ValueError("gauss info contains empty value")
    return values


def load_yaml(path: Path) -> dict[str, Any]:
    payload = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected mapping in {path.name}")
    return payload


def split_host_port(value: str) -> tuple[str, int]:
    host, raw_port = str(value).rsplit(":", 1)
    port = int(raw_port)
    if not host or not 1 <= port <= 65535:
        raise ValueError("invalid host:port")
    return host, port


def assert_batch_resource_name(name: str) -> str:
    if not BATCH_RESOURCE.fullmatch(name or ""):
        raise ValueError(f"unsafe resource target: {name!r}")
    if not IDENTIFIER.fullmatch(name):
        raise ValueError(f"invalid resource identifier: {name!r}")
    return name


def _assert_exact_database(value: str, expected: str, label: str) -> str:
    if value != expected:
        raise ValueError(f"unsafe {label} database target: {value!r}")
    return value


def assert_control_mysql_database(value: str) -> str:
    return _assert_exact_database(value, CONTROL_MYSQL_DATABASE, "control MySQL")


def assert_control_infinity_database(value: str) -> str:
    return _assert_exact_database(value, CONTROL_INFINITY_DATABASE, "control Infinity")


def assert_authorized_gauss_database(value: str) -> str:
    return _assert_exact_database(value, EXPERIMENT_DATABASE, "GaussDB")


def quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def gauss_connect_kwargs(gauss: dict[str, str], *, dbname: str | None = None) -> dict[str, Any]:
    return {
        "host": gauss["host"],
        "port": int(gauss["port"]),
        "dbname": dbname or gauss["dbname"],
        "user": gauss["user"],
        "password": gauss["password"],
        "connect_timeout": 5,
        "options": "-c default_transaction_read_only=off",
    }


def _base_service(base_config: dict[str, Any], name: str) -> dict[str, Any]:
    service = base_config.get(name)
    if not isinstance(service, dict):
        raise ValueError(f"base config missing {name}")
    return copy.deepcopy(service)


def build_group_configs(base_config: dict[str, Any], gauss_info: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
    control_mysql = _base_service(base_config, "mysql")
    control_infinity = _base_service(base_config, "infinity")
    assert_control_mysql_database(str(control_mysql.get("name", "")))
    assert_control_infinity_database(str(control_infinity.get("db_name", "")))
    assert_authorized_gauss_database(gauss_info["dbname"])
    control_redis = _base_service(base_config, "redis")
    experiment_redis = _base_service(base_config, "redis")
    control_minio = _base_service(base_config, "minio")
    experiment_minio = _base_service(base_config, "minio")

    control_mysql.update(
        {
            "name": CONTROL_MYSQL_DATABASE,
            "host": "127.0.0.1",
            "port": PROXY_PORTS["control_metadata"],
        }
    )
    control_infinity.update(
        {
            "uri": f"127.0.0.1:{PROXY_PORTS['control_docstore']}",
            "db_name": CONTROL_INFINITY_DATABASE,
        }
    )
    control_redis.update(
        {
            "db": CONTROL_REDIS_DB,
            "host": f"127.0.0.1:{PROXY_PORTS['control_redis']}",
        }
    )
    experiment_redis.update(
        {
            "db": EXPERIMENT_REDIS_DB,
            "host": f"127.0.0.1:{PROXY_PORTS['experiment_redis']}",
        }
    )
    control_minio.update(
        {
            "host": f"127.0.0.1:{PROXY_PORTS['control_minio']}",
            "bucket": CONTROL_BUCKET,
            "prefix_path": f"{RESOURCE_PREFIX}/control",
        }
    )
    experiment_minio.update(
        {
            "host": f"127.0.0.1:{PROXY_PORTS['experiment_minio']}",
            "bucket": EXPERIMENT_BUCKET,
            "prefix_path": f"{RESOURCE_PREFIX}/experiment",
        }
    )

    control = {
        "ragflow": {"host": "127.0.0.1", "http_port": 9380},
        "admin": {"host": "127.0.0.1", "http_port": 9381},
        "mysql": control_mysql,
        "infinity": control_infinity,
        "redis": control_redis,
        "minio": control_minio,
    }
    experiment = {
        "ragflow": {"host": "127.0.0.1", "http_port": 9480},
        "admin": {"host": "127.0.0.1", "http_port": 9481},
        "gaussdb": {
            "host": "127.0.0.1",
            "port": PROXY_PORTS["experiment_docstore"],
            "database": EXPERIMENT_DATABASE,
            "user": gauss_info["user"],
            "password": gauss_info["password"],
            "schema": EXPERIMENT_DOCSTORE_SCHEMA,
        },
        "redis": experiment_redis,
        "minio": experiment_minio,
    }
    return control, experiment


def build_private_environments(
    gauss_info: dict[str, str],
) -> dict[str, dict[str, str]]:
    common = {
        "PYTHONPATH": str(PROJECT_ROOT),
        "REGISTER_ENABLED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "LLM_BINDING_HOST": "127.0.0.1",
    }
    control = {
        **common,
        "RAG_PROJECT_BASE": str(RUNTIME_DIR / "control"),
        "DB_TYPE": "mysql",
        "DOC_ENGINE": "infinity",
        "ADMIN_PORT": "9381",
        "RAGFLOW_SECRET_KEY": secrets.token_urlsafe(48),
        "DEFAULT_SUPERUSER_EMAIL": f"fr-{RESOURCE_TOKEN}-control-admin@example.com",
        "DEFAULT_SUPERUSER_PASSWORD": secrets.token_urlsafe(24),
    }
    experiment = {
        **common,
        "RAG_PROJECT_BASE": str(RUNTIME_DIR / "experiment"),
        "DB_TYPE": "gaussdb",
        "DOC_ENGINE": "gaussdb",
        "ADMIN_PORT": "9481",
        "RAGFLOW_SECRET_KEY": secrets.token_urlsafe(48),
        "DEFAULT_SUPERUSER_EMAIL": f"fr-{RESOURCE_TOKEN}-experiment-admin@example.com",
        "DEFAULT_SUPERUSER_PASSWORD": secrets.token_urlsafe(24),
        "GAUSSDB_METADATA_HOST": "127.0.0.1",
        "GAUSSDB_METADATA_PORT": str(PROXY_PORTS["experiment_metadata"]),
        "GAUSSDB_METADATA_DBNAME": EXPERIMENT_DATABASE,
        "GAUSSDB_METADATA_USER": gauss_info["user"],
        "GAUSSDB_METADATA_PASSWORD": gauss_info["password"],
        "GAUSSDB_METADATA_SCHEMA": EXPERIMENT_METADATA_SCHEMA,
        "GAUSSDB_METADATA_MAX_CONNECTIONS": "50",
        "GAUSSDB_METADATA_STALE_TIMEOUT": "30",
    }
    return {"control": control, "experiment": experiment}


def _redact(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return {"redacted": True, "fingerprint": fingerprint(value)}
    identity_parts = ("host", "uri", "user", "username", "database", "dbname", "email")
    if lowered == "name" or any(part in lowered for part in identity_parts):
        return {"redacted": True, "fingerprint": fingerprint(value)}
    return value


def redacted_manifest(
    configs: tuple[dict[str, Any], dict[str, Any]],
    environments: dict[str, dict[str, str]],
) -> dict[str, Any]:
    return {
        "batch_id": BATCH_ID,
        "groups": {
            "control": {
                "config": _redact(configs[0]),
                "environment": _redact(environments["control"]),
            },
            "experiment": {
                "config": _redact(configs[1]),
                "environment": _redact(environments["experiment"]),
            },
        },
        "proxy_ports": PROXY_PORTS,
        "resources": {
            "control_database": CONTROL_MYSQL_DATABASE,
            "control_docstore_database": CONTROL_INFINITY_DATABASE,
            "experiment_database": EXPERIMENT_DATABASE,
            "experiment_metadata_schema": EXPERIMENT_METADATA_SCHEMA,
            "experiment_docstore_schema": EXPERIMENT_DOCSTORE_SCHEMA,
            "control_bucket": CONTROL_BUCKET,
            "experiment_bucket": EXPERIMENT_BUCKET,
            "redis_dbs": [CONTROL_REDIS_DB, EXPERIMENT_REDIS_DB],
        },
    }


def build_proxy_config(base_config: dict[str, Any], gauss_info: dict[str, str]) -> dict[str, Any]:
    mysql = _base_service(base_config, "mysql")
    infinity_host, infinity_port = split_host_port(_base_service(base_config, "infinity")["uri"])
    redis_host, redis_port = split_host_port(_base_service(base_config, "redis")["host"])
    minio_host, minio_port = split_host_port(_base_service(base_config, "minio")["host"])
    upstreams = {
        "control_metadata": (mysql["host"], int(mysql.get("port", 3306))),
        "control_docstore": (infinity_host, infinity_port),
        "control_redis": (redis_host, redis_port),
        "control_minio": (minio_host, minio_port),
        "experiment_metadata": (gauss_info["host"], int(gauss_info["port"])),
        "experiment_docstore": (gauss_info["host"], int(gauss_info["port"])),
        "experiment_redis": (redis_host, redis_port),
        "experiment_minio": (minio_host, minio_port),
    }
    return {
        "control_host": "127.0.0.1",
        "control_port": PROXY_CONTROL_PORT,
        "control_token": secrets.token_urlsafe(32),
        "proxies": {
            name: {
                "listen_host": "127.0.0.1",
                "listen_port": PROXY_PORTS[name],
                "upstream_host": upstream[0],
                "upstream_port": upstream[1],
            }
            for name, upstream in upstreams.items()
        },
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    yaml = YAML()
    yaml.default_flow_style = False
    with path.open("w", encoding="utf-8") as stream:
        yaml.dump(payload, stream)
    path.chmod(0o600)


def _write_private_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def write_environment_result(command: str, result: dict[str, Any]) -> Path:
    if command not in {"clean", "render"}:
        raise ValueError(f"unsupported environment result: {command}")
    ENV_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    ENV_EVIDENCE_DIR.chmod(0o700)
    path = ENV_EVIDENCE_DIR / f"{command}_result.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    path.chmod(0o600)
    return path


def snapshot_initial_api_logs() -> dict[str, Any]:
    groups = ("control", "experiment")
    sources = {group: RUNTIME_DIR / group / "logs" / "managed_api.log" for group in groups}
    destinations = {group: ENV_EVIDENCE_DIR / f"startup_attempt1_{group}.log" for group in groups}
    for source in sources.values():
        if not source.is_file():
            raise FileNotFoundError(f"managed API log is missing: {source}")
    for destination in destinations.values():
        if destination.exists():
            raise FileExistsError(f"startup log evidence already exists: {destination}")

    ENV_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    ENV_EVIDENCE_DIR.chmod(0o700)
    logs: dict[str, dict[str, Any]] = {}
    for group in groups:
        payload = sources[group].read_bytes()
        with destinations[group].open("xb") as stream:
            stream.write(payload)
        destinations[group].chmod(0o600)
        logs[group] = {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    return {"batch_id": BATCH_ID, "group_order": list(groups), "logs": logs}


def _copy_runtime_assets(source_conf: Path, root: Path) -> None:
    conf_dir = root / "conf"
    conf_dir.mkdir(parents=True, exist_ok=True)
    excluded = {
        "service_conf.yaml",
        "service_conf_control.yaml",
        "service_conf_gaussdb.yaml",
        "service_conf_gaussdb_backup.yaml",
        "local.service_conf.yaml.backup",
    }
    for source in source_conf.iterdir():
        if source.name in excluded or "backup" in source.name.lower():
            continue
        target = conf_dir / source.name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    for private_name in ("private.pem", "public.pem"):
        private_path = conf_dir / private_name
        if private_path.exists():
            private_path.chmod(0o600)
    for asset_name in ("agent", "rag", "ragflow_deps"):
        target = root / asset_name
        target.symlink_to(PROJECT_ROOT / asset_name, target_is_directory=True)
    (root / "logs").mkdir(mode=0o700)


def render_runtime(reset: bool = False) -> dict[str, Any]:
    if RUNTIME_DIR.exists():
        if not reset:
            raise FileExistsError(f"runtime already exists: {RUNTIME_DIR}")
        if RUNTIME_DIR.parent != EXECUTE_DIR / "runtime" or RUNTIME_DIR.name != BATCH_ID:
            raise ValueError("refusing to remove unexpected runtime path")
        shutil.rmtree(RUNTIME_DIR)
    RUNTIME_DIR.mkdir(parents=True, mode=0o700)

    base = load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")
    gauss = parse_gauss_info(PROJECT_ROOT / "docs" / "administrator" / "configurations" / "gaussdb_info.md")
    configs = build_group_configs(base, gauss)
    environments = build_private_environments(gauss)
    proxy_config = build_proxy_config(base, gauss)

    for group, config in zip(("control", "experiment"), configs, strict=True):
        root = RUNTIME_DIR / group
        root.mkdir(mode=0o700)
        _copy_runtime_assets(PROJECT_ROOT / "conf", root)
        _write_yaml(root / "conf" / "service_conf.yaml", config)

    _write_private_json(RUNTIME_DIR / "private_environments.json", environments)
    _write_private_json(RUNTIME_DIR / "private_proxy_config.json", proxy_config)
    manifest = redacted_manifest(configs, environments)
    (RUNTIME_DIR / "manifest.redacted.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _clean_mysql(base: dict[str, Any]) -> dict[str, Any]:
    import pymysql

    cfg = _base_service(base, "mysql")
    name = assert_control_mysql_database(str(cfg.get("name", "")))
    conn = pymysql.connect(
        host=cfg["host"],
        port=int(cfg.get("port", 3306)),
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=5,
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
            (name,),
        )
        count = int(cur.fetchone()[0])
    conn.close()
    return {"created": True, "table_count": count}


def _clean_infinity(base: dict[str, Any]) -> dict[str, Any]:
    import infinity
    from infinity.common import ConflictType, NetworkAddress

    cfg = _base_service(base, "infinity")
    name = assert_control_infinity_database(str(cfg.get("db_name", "")))
    host, port = split_host_port(cfg["uri"])
    conn = infinity.connect(NetworkAddress(host, port))
    try:
        names = set(conn.list_databases().db_names)
        if name not in names:
            raise ValueError("authorized Infinity database does not exist")
        database = conn.get_database(name)
        tables_before = list(database.list_tables().table_names)
        for table_name in sorted(tables_before):
            database.drop_table(table_name, ConflictType.Ignore)
        tables_after = list(database.list_tables().table_names)
    finally:
        conn.disconnect()
    return {
        "database_present": True,
        "table_count_before": len(tables_before),
        "table_count_after": len(tables_after),
    }


def _clean_redis(base: dict[str, Any]) -> dict[str, Any]:
    import redis

    cfg = _base_service(base, "redis")
    host, port = split_host_port(cfg["host"])
    results = {}
    for db in (CONTROL_REDIS_DB, EXPERIMENT_REDIS_DB):
        if db not in {11, 12}:
            raise ValueError("unsafe redis DB")
        client = redis.Redis(
            host=host,
            port=port,
            db=db,
            username=cfg.get("username") or None,
            password=cfg.get("password") or None,
            socket_connect_timeout=5,
        )
        before = int(client.dbsize())
        client.flushdb(asynchronous=False)
        results[str(db)] = {"before": before, "after": int(client.dbsize())}
    return results


def _empty_minio_bucket(client, bucket: str) -> int:
    removed = 0
    if client.bucket_exists(bucket):
        for obj in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, obj.object_name)
            removed += 1
        client.remove_bucket(bucket)
    client.make_bucket(bucket)
    return removed


def _clean_minio(base: dict[str, Any]) -> dict[str, Any]:
    from minio import Minio

    cfg = _base_service(base, "minio")
    endpoint = str(cfg["host"]).removeprefix("http://").removeprefix("https://")
    client = Minio(
        endpoint,
        access_key=cfg["user"],
        secret_key=cfg["password"],
        secure=bool(cfg.get("secure", False)),
    )
    results = {}
    for bucket in (CONTROL_BUCKET, EXPERIMENT_BUCKET):
        if not bucket.startswith(f"fr-{RESOURCE_TOKEN}-"):
            raise ValueError("unsafe bucket")
        results[bucket.rsplit("-", 1)[-1]] = {
            "removed_objects": _empty_minio_bucket(client, bucket),
            "exists": client.bucket_exists(bucket),
        }
    return results


def _clean_gauss(gauss: dict[str, str]) -> dict[str, Any]:
    import psycopg2

    database = assert_authorized_gauss_database(gauss["dbname"])
    schemas = (
        assert_batch_resource_name(EXPERIMENT_METADATA_SCHEMA),
        assert_batch_resource_name(EXPERIMENT_DOCSTORE_SCHEMA),
    )
    target_conn = psycopg2.connect(**gauss_connect_kwargs(gauss, dbname=database))
    target_conn.autocommit = True
    with target_conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        if cur.fetchone() != (database,):
            raise ValueError("GaussDB connection resolved outside zws_test2")
        cur.execute("SHOW sql_compatibility")
        compatibility = str(cur.fetchone()[0])
        cur.execute("SHOW maintenance_work_mem")
        maintenance_work_mem_before = str(cur.fetchone()[0])
        cur.execute("SET maintenance_work_mem = %s", (GAUSS_CLEAN_MAINTENANCE_WORK_MEM,))
        cur.execute("SHOW maintenance_work_mem")
        maintenance_work_mem_used = str(cur.fetchone()[0])
        cur.execute(
            "SELECT table_schema, table_type, COUNT(*) "
            "FROM information_schema.tables "
            "WHERE table_schema <> 'public' "
            "AND table_schema NOT IN (%s,%s) "
            "GROUP BY table_schema, table_type ORDER BY table_schema, table_type",
            schemas,
        )
        system_before = cur.fetchall()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
        public_tables = [str(row[0]) for row in cur.fetchall()]
        cur.execute("SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema='public' ORDER BY sequence_name")
        public_sequences = [str(row[0]) for row in cur.fetchall()]

        def public_row_count() -> int:
            count = 0
            for table in public_tables:
                cur.execute(f"SELECT COUNT(*) FROM {quote_identifier('public')}.{quote_identifier(table)}")
                count += int(cur.fetchone()[0])
            return count

        rows_before = public_row_count()
        if public_tables:
            targets = ", ".join(f"{quote_identifier('public')}.{quote_identifier(table)}" for table in public_tables)
            cur.execute(f"TRUNCATE TABLE {targets}")
        for sequence in public_sequences:
            qualified_sequence = f"{quote_identifier('public')}.{quote_identifier(sequence)}"
            cur.execute("SELECT setval(%s, %s, %s)", (qualified_sequence, 1, False))
            if cur.fetchone() != (1,):
                raise ValueError("failed to reset public sequence")
        rows_after = public_row_count()
        for schema in schemas:
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name IN (%s,%s)",
            schemas,
        )
        created = {row[0] for row in cur.fetchall()}
        cur.execute(
            "SELECT table_schema, table_type, COUNT(*) "
            "FROM information_schema.tables "
            "WHERE table_schema <> 'public' "
            "AND table_schema NOT IN (%s,%s) "
            "GROUP BY table_schema, table_type ORDER BY table_schema, table_type",
            schemas,
        )
        system_after = cur.fetchall()
    target_conn.close()
    return {
        "database": database,
        "sql_compatibility": compatibility,
        "maintenance_work_mem_before": maintenance_work_mem_before,
        "maintenance_work_mem_used": maintenance_work_mem_used,
        "public_table_count": len(public_tables),
        "public_rows_before": rows_before,
        "public_rows_after": rows_after,
        "public_sequences_reset": len(public_sequences),
        "metadata_schema": EXPERIMENT_METADATA_SCHEMA in created,
        "docstore_schema": EXPERIMENT_DOCSTORE_SCHEMA in created,
        "system_catalog_unchanged": system_before == system_after,
    }


def clean_external_resources() -> dict[str, Any]:
    base = load_yaml(PROJECT_ROOT / "conf" / "service_conf.yaml")
    gauss = parse_gauss_info(PROJECT_ROOT / "docs" / "administrator" / "configurations" / "gaussdb_info.md")
    return {
        "batch_id": BATCH_ID,
        "mysql": _clean_mysql(base),
        "infinity": _clean_infinity(base),
        "redis": _clean_redis(base),
        "minio": _clean_minio(base),
        "gauss": _clean_gauss(gauss),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare fresh isolated GaussDB test environments")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render")
    render_parser.add_argument("--reset", action="store_true")
    render_parser.add_argument("--confirm", required=True)
    clean_parser = subparsers.add_parser("clean")
    clean_parser.add_argument("--confirm", required=True)
    snapshot_parser = subparsers.add_parser("snapshot-startup")
    snapshot_parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.confirm != BATCH_ID:
        parser.error("--confirm must equal the fresh batch id")
    if args.command == "render":
        result = render_runtime(reset=args.reset)
        write_environment_result("render", result)
    elif args.command == "clean":
        result = clean_external_resources()
        write_environment_result("clean", result)
    else:
        result = snapshot_initial_api_logs()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
