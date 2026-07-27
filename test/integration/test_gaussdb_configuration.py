#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import math
import os
import uuid
from dataclasses import replace

import pytest

from gaussdb_test_markers import mark_gaussdb_both_by_default


pytestmark = pytest.mark.gaussdb_integration


def _assert_no_sensitive_material(payload):
    text = repr(payload).lower()
    for marker in ("password", "postgresql://", "dbname=", "api_key", "api-key", "access_token", "authorization", "bearer "):
        assert marker not in text
    for env_name in ("GAUSSDB_PASSWORD", "API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(env_name)
        if secret and len(secret) >= 4 and secret.lower() in text:
            pytest.fail(f"{env_name} value leaked in integration payload")


def test_tc_cfg_302_pool_get_conn_executes_real_select(gaussdb_env):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config

    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    connection = None
    cursor = None
    try:
        connection = pool.get_conn()
        cursor = connection.cursor()
        cursor.execute("SELECT 1")

        assert connection is not None
        assert not bool(connection.closed)
        assert cursor.fetchone() == (1,)
        assert pool.resolved_schema == gaussdb_env["schema"]
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            pool.put_conn(connection)
        pool.close_all()


def test_tc_cfg_306_pool_accepts_real_schema_usage_and_create_privileges(gaussdb_env, gaussdb_admin_conn):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config

    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    try:
        pool.check_schema_access()
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT has_schema_privilege(%s, %s, 'USAGE'),
                       has_schema_privilege(%s, %s, 'CREATE')
                """,
                [pool.config.user, pool.resolved_schema, pool.config.user, pool.resolved_schema],
            )
            assert cur.fetchone() == (True, True)
        assert pool.resolved_schema == gaussdb_env["schema"]
    finally:
        pool.close_all()
def test_tc_cfg_309_pool_rejects_real_missing_schema(gaussdb_env, gaussdb_admin_conn):
    from psycopg2 import ProgrammingError

    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionError, GaussDBConnectionPool, load_gaussdb_config

    missing_schema = f"ragflow_missing_{uuid.uuid4().hex}"
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = %s)",
            [missing_schema],
        )
        assert cur.fetchone() == (False,)

    config = replace(load_gaussdb_config(), schema=missing_schema)
    pool = GaussDBConnectionPool(config=config)
    try:
        with pytest.raises(GaussDBConnectionError, match=missing_schema) as exc_info:
            pool.check_schema_access()
        assert type(exc_info.value) is GaussDBConnectionError
        assert isinstance(exc_info.value.__cause__, ProgrammingError)
        error = str(exc_info.value)
        assert "does not exist" in error.lower()
        _assert_no_sensitive_material(error)
    finally:
        pool.close_all()

    assert gaussdb_env["schema"] != missing_schema


@pytest.mark.gaussdb_centralized
def test_tc_cfg_601_health_reports_real_a_compatibility(gaussdb_env):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config
    from rag.utils.gaussdb_conn import GaussDBConnection

    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    try:
        conn = GaussDBConnection(pool=pool)
        compatibility = conn.pool.fetch_one("SHOW sql_compatibility")[0]
        assert str(compatibility).upper() == "A"

        version = conn.pool.fetch_one("SELECT version()")[0]
        result = conn.health()

        assert version
        assert result["status"] == "healthy"
        assert result["uri"] == conn.pool.masked_uri
        assert "password=" not in result["uri"].lower()
        assert "dbname=" not in result["uri"].lower()
        assert "postgresql://" not in result["uri"].lower()
        assert result["version_comment"] == version
        assert result["schema"] == gaussdb_env["schema"]
        assert result["sql_compatibility"] == "A"
        _assert_no_sensitive_material(result)
    finally:
        pool.close_all()


def test_tc_cfg_607_performance_metrics_reports_real_connection(gaussdb_env):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config
    from rag.utils.gaussdb_conn import GaussDBConnection

    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    try:
        conn = GaussDBConnection(pool=pool)
        result = conn.get_performance_metrics()
        assert result["connection"] == "connected"
        assert isinstance(result["latency_ms"], (int, float))
        assert math.isfinite(result["latency_ms"])
        assert result["latency_ms"] >= 0
        assert result["schema"] == gaussdb_env["schema"]
        _assert_no_sensitive_material(result)
    finally:
        pool.close_all()


@pytest.mark.gaussdb_variant_specific
def test_tc_cfg_612_status_reports_real_alive_payload(gaussdb_env, ragflow_http_auth):
    import requests

    response = requests.get(
        f"{ragflow_http_auth['base_url']}/api/v1/system/gaussdb/status",
        headers=ragflow_http_auth["headers"],
        timeout=ragflow_http_auth["request_timeout"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    result = payload["data"]
    health = result["message"]["health"]
    performance = result["message"]["performance"]
    assert result["status"] == "alive"
    assert health["status"] == "healthy"
    assert health["schema"] == gaussdb_env["schema"]
    assert performance["connection"] == "connected"
    assert isinstance(performance["latency_ms"], (int, float))
    assert math.isfinite(performance["latency_ms"])
    assert performance["latency_ms"] >= 0
    assert performance["schema"] == gaussdb_env["schema"]
    evidence = gaussdb_env["variant_evidence"]
    expected_uri = (
        f"{os.environ['GAUSSDB_USER']}@{os.environ['GAUSSDB_HOST']}:{os.environ.get('GAUSSDB_PORT', '19995')}"
        f"/{evidence['database']}?schema={gaussdb_env['schema']}"
    )
    assert health["uri"] == expected_uri
    assert health["sql_compatibility"] == evidence["sql_compatibility"]
    assert health["sql_compatibility"] == ("ORA" if evidence["variant"] == "distributed" else "A")
    assert health["version_comment"] == evidence["version"]
    _assert_no_sensitive_material(payload)


@pytest.mark.gaussdb_variant_specific
def test_tc_cfg_901_real_pool_connection_smoke(gaussdb_env):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config
    from rag.utils.gaussdb_conn import GaussDBConnection

    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    connection = None
    cursor = None
    try:
        conn = GaussDBConnection(pool=pool)
        assert conn.db_type() == "gaussdb"
        assert conn.schema == conn.resolved_schema == pool.resolved_schema == gaussdb_env["schema"]
        connection = conn.pool.get_conn()
        cursor = connection.cursor()
        cursor.execute("SELECT current_schema()")
        assert cursor.fetchone() == (gaussdb_env["schema"],)
        cursor.execute("SHOW search_path")
        assert cursor.fetchone() == (gaussdb_env["schema"],)
        evidence = gaussdb_env["variant_evidence"]
        assert evidence["database"] == os.environ["GAUSSDB_DATABASE"]
        assert evidence["user"] == os.environ["GAUSSDB_USER"]
        assert evidence["sql_compatibility"] == ("ORA" if evidence["variant"] == "distributed" else "A")
        assert evidence["enable_ustore"] == "on"
        assert evidence["enable_default_ustore_table"] == "on"
        assert evidence["enable_vectordb"] == "on"
        assert evidence["schema_privileges"] == (True, True)
        assert evidence["topology"] == ({"C": 3, "D": 3, "S": 6} if evidence["variant"] == "distributed" else {})
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            pool.put_conn(connection)
        pool.close_all()


@pytest.mark.gaussdb_variant_specific
def test_tc_cfg_902_real_status_probe_smoke(gaussdb_env):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config
    from rag.utils.gaussdb_conn import GaussDBConnection

    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    try:
        conn = GaussDBConnection(pool=pool)
        health = conn.health()
        performance = conn.get_performance_metrics()
        assert health["status"] == "healthy"
        assert health["version_comment"]
        expected_compatibility = "ORA" if gaussdb_env["variant_evidence"].get("variant") == "distributed" else "A"
        assert health["sql_compatibility"] == expected_compatibility
        assert health["schema"] == gaussdb_env["schema"]
        assert performance["connection"] == "connected"
        assert math.isfinite(performance["latency_ms"])
        assert performance["latency_ms"] >= 0
        assert performance["schema"] == gaussdb_env["schema"]
        _assert_no_sensitive_material({"health": health, "performance": performance})
    finally:
        pool.close_all()


def test_tc_cfg_903_create_idx_smoke_uses_truncated_real_index_names(gaussdb_env, table_name, register_table, gaussdb_admin_conn):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "cfg_903")
    meta_table = register_table(f"ragflow_doc_meta_{gaussdb_env['table_prefix']}_cfg_903")
    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    try:
        conn = GaussDBConnection(pool=pool)
        assert conn.create_idx(table, "kb-cfg-903", 1024, "naive") is True
        assert conn.create_doc_meta_idx(meta_table) is True

        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = %s
                   AND table_name IN (%s, %s)
                """,
                [gaussdb_env["schema"], table, meta_table],
            )
            assert {row[0] for row in cur.fetchall()} == {table, meta_table}

            cur.execute(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = %s
                   AND tablename = %s
                """,
                [gaussdb_env["schema"], table],
            )
            chunk_indexes = [(row[0], row[1].lower()) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT indexname, indexdef
                  FROM pg_indexes
                 WHERE schemaname = %s
                   AND tablename = %s
                """,
                [gaussdb_env["schema"], meta_table],
            )
            meta_indexes = [(row[0], row[1].lower()) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT c.reloptions
                  FROM pg_class c
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = %s AND c.relname = %s
                """,
                [gaussdb_env["schema"], table],
            )
            reloptions = cur.fetchone()[0]
            cur.execute(
                """
                SELECT column_name, udt_name, is_nullable, column_default
                  FROM information_schema.columns
                 WHERE table_schema = %s
                   AND table_name = %s
                   AND column_name IN ('q_1024_vec', 'q_1024_vec_valid')
                """,
                [gaussdb_env["schema"], table],
            )
            object_evidence = {row[0]: row[1:] for row in cur.fetchall()}
            cur.execute(
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                  FROM pg_attribute a
                  JOIN pg_class c ON c.oid = a.attrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = %s
                   AND c.relname = %s
                   AND a.attname = 'q_1024_vec'
                   AND a.attnum > 0
                   AND NOT a.attisdropped
                """,
                [gaussdb_env["schema"], table],
            )
            vector_type_declaration = cur.fetchone()[0]

        fixed_chunk_table = "ragflow_it_0123456789abcdef0123456789abcdef_cfg_903"
        fixed_meta_table = "ragflow_doc_meta_0123456789abcdef0123456789abcdef_cfg_903"
        assert conn.ddl.index_name(fixed_chunk_table, "fts_all") == (
            "idx_gdb_ragflow_it_0123456789abcdef0123456789abcdef__d839bbe2f5"
        )
        assert conn.ddl.index_name(fixed_chunk_table, "fts_all_ngram") == (
            "idx_gdb_ragflow_it_0123456789abcdef0123456789abcdef__39df30fec5"
        )
        assert conn.ddl.index_name(fixed_chunk_table, "q_1024_vec_diskann") == (
            "idx_gdb_ragflow_it_0123456789abcdef0123456789abcdef__945c83acf7"
        )
        assert conn.ddl.index_name(fixed_meta_table, "kb_id") == (
            "idx_gdb_ragflow_doc_meta_0123456789abcdef0123456789a_b39d0ef468"
        )

        simple_ugin_indexes = [
            (name, definition)
            for name, definition in chunk_indexes
            if " using ugin " in definition and "to_tsvector('simple'" in definition
        ]
        ngram_ugin_indexes = [
            (name, definition)
            for name, definition in chunk_indexes
            if " using ugin " in definition and "to_tsvector('ngram'" in definition
        ]
        diskann_indexes = [(name, definition) for name, definition in chunk_indexes if " using gsdiskann " in definition]
        meta_kb_indexes = [
            (name, definition)
            for name, definition in meta_indexes
            if " using ubtree " in definition and "(kb_id)" in definition
        ]
        assert len(simple_ugin_indexes) == 1
        assert len(ngram_ugin_indexes) == 1
        for _name, definition in [simple_ugin_indexes[0], ngram_ugin_indexes[0]]:
            assert all(
                column in definition
                for column in (
                    "title_tks",
                    "title_sm_tks",
                    "important_tks",
                    "question_tks",
                    "content_ltks",
                    "content_sm_ltks",
                )
            )
        assert len(diskann_indexes) == 1
        assert "(q_1024_vec cosine)" in diskann_indexes[0][1]
        assert len(meta_kb_indexes) == 1
        assert all(
            len(name) <= 63
            for name, _definition in [
                simple_ugin_indexes[0],
                ngram_ugin_indexes[0],
                diskann_indexes[0],
                meta_kb_indexes[0],
            ]
        )
        assert "storage_type=ustore" in str(reloptions).lower()
        vector_type, vector_nullable, _vector_default = object_evidence["q_1024_vec"]
        valid_type, valid_nullable, valid_default = object_evidence["q_1024_vec_valid"]
        assert "floatvector" in vector_type.lower()
        assert vector_type_declaration.lower() == "floatvector(1024)"
        assert vector_nullable == "YES"
        assert valid_type.lower() in {"bool", "boolean"}
        assert valid_nullable == "NO"
        assert str(valid_default).lower() in {"false", "false::boolean"}
    finally:
        pool.close_all()


def test_tc_cfg_904_health_does_not_create_objects_before_real_create_idx(
    gaussdb_env, table_name, register_table, gaussdb_admin_conn
):
    from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, load_gaussdb_config
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "cfg_904")
    meta_table = register_table(f"ragflow_doc_meta_{gaussdb_env['table_prefix']}_cfg_904")
    pool = GaussDBConnectionPool(config=load_gaussdb_config())
    try:
        conn = GaussDBConnection(pool=pool)
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM information_schema.tables
                 WHERE table_schema = %s
                   AND table_name IN (%s, %s)
                """,
                [gaussdb_env["schema"], table, meta_table],
            )
            before = cur.fetchone()[0]

        health_result = conn.health()
        _assert_no_sensitive_material(health_result)

        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM information_schema.tables
                 WHERE table_schema = %s
                   AND table_name IN (%s, %s)
                """,
                [gaussdb_env["schema"], table, meta_table],
            )
            after_health = cur.fetchone()[0]

        assert health_result["status"] == "healthy"
        assert before == after_health == 0
        assert conn.create_idx(table, "kb-cfg-904", 1024, "naive") is True
        assert conn.create_doc_meta_idx(meta_table) is True

        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                  FROM information_schema.tables
                 WHERE table_schema = %s
                   AND table_name IN (%s, %s)
                """,
                [gaussdb_env["schema"], table, meta_table],
            )
            assert cur.fetchone()[0] == 2
    finally:
        pool.close_all()


mark_gaussdb_both_by_default(globals())
