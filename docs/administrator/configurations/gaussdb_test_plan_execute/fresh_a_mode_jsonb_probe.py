#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Any

import psycopg2
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    EVIDENCE_ROOT,
    RUNTIME_DIR,
    evidence_dir,
)
from common.doc_store.gaussdb_conn_base import GaussDBSearchBuilder
from rag.utils.gaussdb_conn import GaussDBConnection


DEFAULT_RUNTIME = RUNTIME_DIR
DEFAULT_EVIDENCE = evidence_dir("00_environment_setup") / "a_mode_jsonb_probe.json"
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
EXPECTED_INITIAL_TAGS = ("旧标签", "风险")
EXPECTED_RENAMED_TAGS = ("新标签", "风险")


def probe_contract_ok(result: dict[str, object]) -> bool:
    return (
        result.get("sql_compatibility") in {"A", "ORA"}
        and result.get("aggregation_executed") is True
        and result.get("aggregation_values_match") is True
        and result.get("rename_executed") is True
        and result.get("renamed_values_match") is True
        and result.get("forbidden_sql_absent") is True
        and result.get("rollback_no_residue") is True
    )


def _empty_result() -> dict[str, object]:
    return {
        "sql_compatibility": "unknown",
        "aggregation_executed": False,
        "aggregation_values_match": False,
        "rename_executed": False,
        "renamed_values_match": False,
        "forbidden_sql_absent": False,
        "rollback_no_residue": False,
        "expected_initial_values": list(EXPECTED_INITIAL_TAGS),
        "expected_renamed_values": list(EXPECTED_RENAMED_TAGS),
        "aggregation_sql_sha256": None,
        "rename_sql_sha256": None,
    }


def _validate_runtime(runtime: Path) -> Path:
    resolved = runtime.resolve()
    if resolved != RUNTIME_DIR.resolve():
        raise ValueError("probe runtime does not match the active run context")
    return resolved


def _validate_evidence_path(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(EVIDENCE_ROOT.resolve()):
        raise ValueError("probe evidence path is outside the active run context")
    return resolved


def _load_experiment_config(runtime: Path) -> dict[str, Any]:
    config_path = runtime / "experiment" / "conf" / "service_conf.yaml"
    payload = YAML(typ="safe", pure=True).load(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("experiment service configuration must be a mapping")
    gaussdb = payload.get("gaussdb")
    config = gaussdb.get("config", gaussdb) if isinstance(gaussdb, dict) else None
    if not isinstance(config, dict):
        raise ValueError("experiment gaussdb configuration is missing")
    required = ("host", "port", "database", "user", "password", "schema")
    if any(config.get(key) in (None, "") for key in required):
        raise ValueError("experiment gaussdb configuration is incomplete")
    schema = str(config["schema"])
    if not IDENTIFIER.fullmatch(schema):
        raise ValueError("experiment DocEngine schema is unsafe")
    return config


def _connect(config: dict[str, Any], *, read_only: bool):
    schema = str(config["schema"])
    read_only_value = "on" if read_only else "off"
    return psycopg2.connect(
        host=str(config["host"]),
        port=int(config["port"]),
        dbname=str(config["database"]),
        user=str(config["user"]),
        password=str(config["password"]),
        options=(f"-c search_path={schema},public -c client_encoding=UTF8 -c default_transaction_read_only={read_only_value}"),
    )


def _probe_table_name() -> str:
    digest = hashlib.sha256(BATCH_ID.encode("utf-8")).hexdigest()[:12]
    return f"ragflow_jsonb_probe_{digest}"


def _sql_fingerprint(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _forbidden_sql_absent(*statements: str) -> bool:
    combined = "\n".join(statements)
    return "LATERAL" not in combined.upper() and "jsonb_insert" not in combined.lower() and re.search(r"=\s*[A-Za-z_][A-Za-z0-9_]*\s*-\s*%s", combined) is None


def _coerce_tag_values(value: Any) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("probe tag value is not a JSON array")
    return [str(item) for item in value]


def _table_is_absent(config: dict[str, Any], schema: str, table_name: str) -> bool:
    connection = _connect(config, read_only=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                (schema, table_name),
            )
            row = cursor.fetchone()
            return bool(row) and int(row[0]) == 0
    finally:
        connection.rollback()
        connection.close()


def _cleanup_table(config: dict[str, Any], qualified_table: str) -> None:
    connection = _connect(config, read_only=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {qualified_table}")
        connection.commit()
    finally:
        connection.close()


def _write_evidence(path: Path, result: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    EVIDENCE_ROOT.chmod(0o700)
    path.parent.chmod(0o700)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def run_probe(runtime: Path, evidence: Path) -> dict[str, object]:
    result = _empty_result()
    evidence_path = _validate_evidence_path(evidence)
    config: dict[str, Any] | None = None
    qualified_table: str | None = None
    table_created = False
    connection = None

    try:
        runtime_path = _validate_runtime(runtime)
        config = _load_experiment_config(runtime_path)
        schema = str(config["schema"])
        table_name = _probe_table_name()
        builder = GaussDBSearchBuilder(schema=schema)
        qualified_table = builder.ddl.qualified_name(table_name)
        aggregation_sql, aggregation_params = builder.build_aggregation_sql(
            table_name,
            "tag_kwd",
            {"kb_id": "probe-kb"},
        )
        adapter = object.__new__(GaussDBConnection)
        set_sql, set_params = adapter._build_set_clause(
            {"remove": {"tag_kwd": "旧标签"}, "add": {"tag_kwd": "新标签"}},
            is_meta=False,
            condition={"kb_id": "probe-kb"},
        )
        rename_sql = f"UPDATE {qualified_table} SET {set_sql} WHERE id=%s AND kb_id=%s"
        result["aggregation_sql_sha256"] = _sql_fingerprint(aggregation_sql)
        result["rename_sql_sha256"] = _sql_fingerprint(rename_sql)
        result["forbidden_sql_absent"] = _forbidden_sql_absent(aggregation_sql, rename_sql)
        if not result["forbidden_sql_absent"]:
            raise ValueError("production-generated JSONB SQL contains a forbidden construct")

        connection = _connect(config, read_only=False)
        with connection.cursor() as cursor:
            cursor.execute("SHOW sql_compatibility")
            compatibility_row = cursor.fetchone()
            compatibility = str(compatibility_row[0]).strip().upper() if compatibility_row else "unknown"
            result["sql_compatibility"] = compatibility
            if compatibility not in {"A", "ORA"}:
                raise ValueError("probe requires A-compatible GaussDB")

            cursor.execute(f"CREATE TABLE {qualified_table} (id VARCHAR2(64) NOT NULL, kb_id VARCHAR2(64) NOT NULL, tag_kwd JSONB)")
            table_created = True
            cursor.execute(
                f"INSERT INTO {qualified_table} (id, kb_id, tag_kwd) VALUES (%s, %s, %s::jsonb)",
                ("probe-row", "probe-kb", json.dumps(EXPECTED_INITIAL_TAGS, ensure_ascii=False)),
            )

            cursor.execute(aggregation_sql, aggregation_params)
            aggregation_rows = cursor.fetchall()
            result["aggregation_executed"] = True
            aggregation = {str(value): int(count) for value, count in aggregation_rows}
            result["aggregation_values_match"] = aggregation == {"旧标签": 1, "风险": 1}

            cursor.execute(
                rename_sql,
                [*set_params, "probe-row", "probe-kb"],
            )
            result["rename_executed"] = True
            cursor.execute(
                f"SELECT tag_kwd FROM {qualified_table} WHERE id=%s AND kb_id=%s",
                ("probe-row", "probe-kb"),
            )
            tag_row = cursor.fetchone()
            renamed_values = _coerce_tag_values(tag_row[0]) if tag_row else []
            result["renamed_values_match"] = sorted(renamed_values) == sorted(EXPECTED_RENAMED_TAGS)
    except Exception:
        pass
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.rollback()
            with suppress(Exception):
                connection.close()

    if config is not None and qualified_table is not None and table_created:
        with suppress(Exception):
            result["rollback_no_residue"] = _table_is_absent(config, schema, table_name)
        if result["rollback_no_residue"] is not True:
            with suppress(Exception):
                _cleanup_table(config, qualified_table)

    _write_evidence(evidence_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a rollback-only GaussDB A-mode JSONB probe")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args()
    result = run_probe(args.runtime, args.evidence)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if probe_contract_ok(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
