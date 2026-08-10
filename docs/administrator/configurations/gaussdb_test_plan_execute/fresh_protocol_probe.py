#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Callable

from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

EXECUTE_DIR = Path(__file__).resolve().parent
DEFAULT_RUNTIME = RUNTIME_DIR
DEFAULT_EVIDENCE = evidence_dir("00_environment_setup") / "protocol_probe.json"
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _load_yaml(path: Path) -> dict[str, Any]:
    payload = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected mapping in {path.name}")
    return payload


def _split_host_port(value: str) -> tuple[str, int]:
    host, raw_port = value.rsplit(":", 1)
    port = int(raw_port)
    if not host or not 1 <= port <= 65535:
        raise ValueError("invalid host and port")
    return host, port


def _probe_mysql(config: dict[str, Any]) -> bool:
    import pymysql

    cfg = config["mysql"]
    conn = pymysql.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["name"],
        connect_timeout=5,
        read_timeout=5,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            return cur.fetchone() == (1,)
    finally:
        conn.close()


def _probe_infinity(config: dict[str, Any]) -> bool:
    import infinity
    from infinity.common import NetworkAddress

    cfg = config["infinity"]
    host, port = _split_host_port(str(cfg["uri"]))
    conn = infinity.connect(NetworkAddress(host, port))
    try:
        return cfg["db_name"] in set(conn.list_databases().db_names)
    finally:
        conn.disconnect()


def _probe_redis(config: dict[str, Any]) -> bool:
    import redis

    cfg = config["redis"]
    host, port = _split_host_port(str(cfg["host"]))
    client = redis.Redis(
        host=host,
        port=port,
        db=int(cfg["db"]),
        username=cfg.get("username") or None,
        password=cfg.get("password") or None,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    return bool(client.ping())


def _probe_minio(config: dict[str, Any]) -> bool:
    from minio import Minio

    cfg = config["minio"]
    endpoint = str(cfg["host"]).removeprefix("http://").removeprefix("https://")
    client = Minio(
        endpoint,
        access_key=cfg["user"],
        secret_key=cfg["password"],
        secure=bool(cfg.get("secure", False)),
    )
    return bool(client.bucket_exists(cfg["bucket"]))


def _probe_gauss(config: dict[str, Any]) -> bool:
    import psycopg2

    schema = str(config["schema"])
    if not SAFE_IDENTIFIER.fullmatch(schema):
        raise ValueError("unsafe schema")
    conn = psycopg2.connect(
        host=config["host"],
        port=int(config["port"]),
        dbname=config["database"],
        user=config["user"],
        password=config["password"],
        connect_timeout=5,
        options=(f"-c default_transaction_read_only=on -c search_path={schema}"),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_schema()")
            row = cur.fetchone()
            return row == (config["database"], schema)
    finally:
        conn.close()


def _probe_proxy_controller(proxy_config: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://{proxy_config['control_host']}:{proxy_config['control_port']}/status",
        headers={"Authorization": f"Bearer {proxy_config['control_token']}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200:
            raise RuntimeError("proxy controller returned non-200 status")
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise ValueError("invalid proxy status")
    return payload


def summarize_proxy_status(status: dict[str, Any], *, expected_count: int) -> dict[str, Any]:
    proxies = status.get("proxies")
    if not isinstance(proxies, dict):
        proxies = {}
    modes = [item.get("mode") for item in proxies.values() if isinstance(item, dict)]
    proxy_count = len(proxies)
    complete = proxy_count == expected_count
    return {
        "running": status.get("status") == "running" and complete,
        "proxy_count": proxy_count,
        "all_normal": complete and all(mode == "normal" for mode in modes),
        "accepted_total": sum(int(item.get("accepted", 0)) for item in proxies.values() if isinstance(item, dict)),
        "active_total": sum(int(item.get("active", 0)) for item in proxies.values() if isinstance(item, dict)),
        "upstream_errors_total": sum(int(item.get("upstream_errors", 0)) for item in proxies.values() if isinstance(item, dict)),
        "fault_hits_total": sum(int(item.get("fault_hits", 0)) for item in proxies.values() if isinstance(item, dict)),
    }


def summarize_proxy_details(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    proxies = status.get("proxies")
    if not isinstance(proxies, dict):
        return {}
    safe_fields = (
        "mode",
        "accepted",
        "completed",
        "active",
        "upstream_errors",
        "fault_hits",
        "resets",
    )
    return {str(name): {field: item.get(field, 0) for field in safe_fields} for name, item in sorted(proxies.items()) if isinstance(item, dict)}


def _safe_probe(probe: Callable[[], bool]) -> dict[str, Any]:
    try:
        return {"ok": bool(probe())}
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__}


def run_probe(runtime: Path) -> dict[str, Any]:
    control = _load_yaml(runtime / "control" / "conf" / "service_conf.yaml")
    experiment = _load_yaml(runtime / "experiment" / "conf" / "service_conf.yaml")
    environments = json.loads((runtime / "private_environments.json").read_text(encoding="utf-8"))
    proxy_config = json.loads((runtime / "private_proxy_config.json").read_text(encoding="utf-8"))
    metadata_env = environments["experiment"]
    metadata_gauss = {
        "host": metadata_env["GAUSSDB_METADATA_HOST"],
        "port": metadata_env["GAUSSDB_METADATA_PORT"],
        "database": metadata_env["GAUSSDB_METADATA_DBNAME"],
        "user": metadata_env["GAUSSDB_METADATA_USER"],
        "password": metadata_env["GAUSSDB_METADATA_PASSWORD"],
        "schema": metadata_env["GAUSSDB_METADATA_SCHEMA"],
    }
    gaussdb = experiment["gaussdb"]
    docstore_gauss = gaussdb.get("config", gaussdb)

    protocols = {
        "control_metadata": _safe_probe(lambda: _probe_mysql(control)),
        "control_docstore": _safe_probe(lambda: _probe_infinity(control)),
        "control_redis": _safe_probe(lambda: _probe_redis(control)),
        "control_minio": _safe_probe(lambda: _probe_minio(control)),
        "experiment_metadata": _safe_probe(lambda: _probe_gauss(metadata_gauss)),
        "experiment_docstore": _safe_probe(lambda: _probe_gauss(docstore_gauss)),
        "experiment_redis": _safe_probe(lambda: _probe_redis(experiment)),
        "experiment_minio": _safe_probe(lambda: _probe_minio(experiment)),
    }
    status = _probe_proxy_controller(proxy_config)
    return {
        "batch_id": BATCH_ID,
        "protocols": protocols,
        "proxy": summarize_proxy_status(status, expected_count=8),
        "proxy_details": summarize_proxy_details(status),
    }


def write_evidence(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe all fresh-batch proxy protocols")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    result = run_probe(args.runtime)
    write_evidence(args.evidence, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    protocols_ok = all(item["ok"] for item in result["protocols"].values())
    proxy_ok = result["proxy"]["running"] and result["proxy"]["all_normal"]
    raise SystemExit(0 if protocols_ok and proxy_ok else 1)


if __name__ == "__main__":
    main()
