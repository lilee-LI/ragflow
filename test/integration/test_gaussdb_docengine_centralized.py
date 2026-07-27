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
import json

import pytest
from psycopg2 import sql

from gaussdb_test_markers import mark_gaussdb_both_by_default


pytestmark = pytest.mark.gaussdb_integration


def _search_chunks(conn, table, kb_id, condition):
    from common.doc_store.doc_store_base import OrderByExpr

    result = conn.search(["id", "doc_id", "content_with_weight"], [], condition, [], OrderByExpr(), 0, 20, table, [kb_id])
    return result.chunks


def _decode_vector(value):
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def _insert_chunk(admin_conn, schema, table, *, chunk_id, kb_id, doc_id, content, vector):
    with admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("INSERT INTO {} (id, kb_id, doc_id, content_with_weight, q_1024_vec, q_1024_vec_valid) VALUES (%s, %s, %s, %s, %s::floatvector(1024), TRUE)").format(sql.Identifier(schema, table)),
            [chunk_id, kb_id, doc_id, content, json.dumps(vector, separators=(",", ":"))],
        )
    admin_conn.commit()


def _insert_maintenance_probe_rows(conn, table, kb_id, count=5000):
    for start in range(0, count, 500):
        rows = []
        for position in range(start, min(start + 500, count)):
            vector = [0.0] * 1024
            vector[position % 1024] = 1.0
            rows.append(
                {
                    "id": f"maintenance-{position}",
                    "kb_id": kb_id,
                    "content_with_weight": f"maintenance probe {position}",
                    "q_1024_vec": vector,
                }
            )
        assert conn.insert(rows, table, kb_id) == []


def test_tc_wrt_001_centralized_create_idx_creates_ustore_fts_and_vector_objects(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_001")
    kb_id = "kb-wrt-001"

    assert conn.create_idx(table, kb_id, 1024) is True

    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, column_default, data_type, is_nullable
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
               AND column_name IN ('id', 'kb_id', 'available_int', 'removed_kwd', 'chunk_data', 'metadata')
            """,
            [gaussdb_env["schema"], table],
        )
        columns = {row[0]: row[1:] for row in cur.fetchall()}
        cur.execute(
            """
            SELECT tc.constraint_type, kcu.column_name, kcu.ordinal_position
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu
                ON kcu.constraint_schema = tc.constraint_schema
               AND kcu.constraint_name = tc.constraint_name
             WHERE tc.table_schema = %s
               AND tc.table_name = %s
               AND tc.constraint_type = 'PRIMARY KEY'
             ORDER BY kcu.ordinal_position
            """,
            [gaussdb_env["schema"], table],
        )
        primary_key_columns = cur.fetchall()

    assert set(columns) == {"id", "kb_id", "available_int", "removed_kwd", "chunk_data", "metadata"}
    assert columns["id"][1:] == ("character varying", "NO")
    assert columns["kb_id"][1:] == ("character varying", "NO")
    assert columns["available_int"] == ("1", "integer", "NO")
    assert columns["removed_kwd"] == ("'N'::character varying", "character varying", "YES")
    assert columns["chunk_data"] == (None, "jsonb", "YES")
    assert columns["metadata"] == (None, "jsonb", "YES")
    assert primary_key_columns == [("PRIMARY KEY", "kb_id", 1), ("PRIMARY KEY", "id", 2)]
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT reloptions
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND c.relname = %s
            """,
            [gaussdb_env["schema"], table],
        )
        table_options = cur.fetchone()[0]
    assert "storage_type=ustore" in str(table_options).lower()

    diskann_index = conn.ddl.index_name(table, "q_1024_vec_diskann")
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql.SQL("DROP INDEX {}.{}").format(sql.Identifier(gaussdb_env["schema"]), sql.Identifier(diskann_index)))
    gaussdb_admin_conn.commit()
    _insert_maintenance_probe_rows(conn, table, kb_id)

    before = str(conn.pool.fetch_one("SHOW maintenance_work_mem")[0]).upper()
    assert before
    assert conn.create_idx(table, kb_id, 1024) is True
    after = str(conn.pool.fetch_one("SHOW maintenance_work_mem")[0]).upper()
    assert after == before

    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(gaussdb_env["schema"], table)))
        assert cur.fetchone()[0] == 5000
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
               AND column_name IN ('q_1024_vec', 'q_1024_vec_valid')
            """,
            [gaussdb_env["schema"], table],
        )
        vector_columns = {row[0] for row in cur.fetchall()}
        cur.execute(
            """
            SELECT c.reloptions
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relname = %s
            """,
            [gaussdb_env["schema"], table],
        )
        rebuilt_table_options = cur.fetchone()[0]
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            [gaussdb_env["schema"], table],
        )
        index_defs = [str(row[0]).lower() for row in cur.fetchall()]
    assert vector_columns == {"q_1024_vec", "q_1024_vec_valid"}
    assert "storage_type=ustore" in str(rebuilt_table_options).lower()
    assert sum(" using gsdiskann " in definition for definition in index_defs) == 1
    assert sum(" using ugin " in definition for definition in index_defs) == 2
    assert sum("to_tsvector('simple'" in definition for definition in index_defs) == 1
    assert sum("to_tsvector('ngram'" in definition for definition in index_defs) == 1


def test_tc_wrt_1001_centralized_create_insert_get_roundtrip(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1001")
    kb_id = "kb-wrt-1001"
    assert conn.create_idx(table, kb_id, 1024) is True

    assert (
        conn.insert(
            [
                {
                    "id": "c1",
                    "kb_id": kb_id,
                    "doc_id": "d1",
                    "content_with_weight": "hello",
                    "q_1024_vec": [0.1] * 1024,
                }
            ],
            table,
            kb_id,
        )
        == []
    )

    row = conn.get("c1", table, [kb_id])
    assert row["id"] == "c1"
    assert row["kb_id"] == kb_id
    assert row["doc_id"] == "d1"
    assert row["content_with_weight"] == "hello"
    assert row["q_1024_vec"] == pytest.approx([0.1] * 1024)
    assert row["q_1024_vec_valid"] is True

    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id, kb_id, doc_id, content_with_weight, q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(sql.Identifier(gaussdb_env["schema"], table)),
            ["c1", kb_id],
        )
        stored = cur.fetchone()
    assert stored[:4] == ("c1", kb_id, "d1", "hello")
    assert _decode_vector(stored[4]) == pytest.approx([0.1] * 1024)
    assert stored[5] is True


def test_tc_wrt_1010_integration_batch_insert_rolls_back_without_residue(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1010")
    kb_id = "kb-wrt-1010"
    assert conn.create_idx(table, kb_id, 1024) is True
    constraint = f"ck_{gaussdb_env['table_prefix'][:20]}_wrt1010"
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK (id <> 'force-db-error')").format(
                sql.Identifier(gaussdb_env["schema"], table),
                sql.Identifier(constraint),
            )
        )
        cur.execute(
            sql.SQL("INSERT INTO {} (id, kb_id, doc_id, content_with_weight) VALUES (%s, %s, %s, %s)").format(sql.Identifier(gaussdb_env["schema"], table)),
            ["stable", kb_id, "doc-stable", "preexisting"],
        )
    gaussdb_admin_conn.commit()

    errors = conn.insert(
        [
            {
                "id": "before-error",
                "kb_id": kb_id,
                "doc_id": "doc-rollback",
                "content_with_weight": "must not commit",
                "metadata": {"status": "valid"},
                "q_1024_vec": [0.1] * 1024,
            },
            {
                "id": "force-db-error",
                "kb_id": kb_id,
                "doc_id": "doc-rollback",
                "content_with_weight": "must fail check",
                "metadata": {"status": "also-valid-json"},
                "q_1024_vec": [0.2] * 1024,
            },
        ],
        table,
        kb_id,
    )

    assert errors == ["before-error", "force-db-error"]
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT id, kb_id, doc_id, content_with_weight FROM {} ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], table)))
        rows = cur.fetchall()
    assert rows == [("stable", kb_id, "doc-stable", "preexisting")]


def test_tc_wrt_1007_centralized_reparse_replaces_old_chunks(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1007")
    kb_id = "kb-wrt-1007"
    assert conn.create_idx(table, kb_id, 1024) is True

    _insert_chunk(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        chunk_id="c1",
        kb_id=kb_id,
        doc_id="d1",
        content="old chunk",
        vector=[0.1] * 1024,
    )
    assert [(row["id"], row["doc_id"], row["content_with_weight"]) for row in _search_chunks(conn, table, kb_id, {"doc_id": "d1"})] == [("c1", "d1", "old chunk")]

    assert conn.delete({"doc_id": "d1"}, table, kb_id) == 1
    assert (
        conn.insert(
            [
                {"id": "c1_new", "kb_id": kb_id, "doc_id": "d1", "content_with_weight": "new chunk", "q_1024_vec": [0.2] * 1024},
            ],
            table,
            kb_id,
        )
        == []
    )
    assert [(row["id"], row["doc_id"], row["content_with_weight"]) for row in _search_chunks(conn, table, kb_id, {"doc_id": "d1"})] == [("c1_new", "d1", "new chunk")]
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [gaussdb_env["schema"], table],
        )
        assert cur.fetchone()[0] == 1
        cur.execute(
            sql.SQL("SELECT id, doc_id, content_with_weight, q_1024_vec, q_1024_vec_valid FROM {} WHERE doc_id = %s").format(sql.Identifier(gaussdb_env["schema"], table)),
            ["d1"],
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][:3] == ("c1_new", "d1", "new chunk")
    assert _decode_vector(rows[0][3]) == pytest.approx([0.2] * 1024)
    assert rows[0][4] is True


def test_tc_wrt_002_centralized_sequential_create_idx_is_idempotent(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_002")
    kb_id = "kb-wrt-002"
    result1 = conn.create_idx(table, kb_id, 1024)
    result2 = conn.create_idx(table, kb_id, 1024)

    assert result1 is True
    assert result2 is True
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            [gaussdb_env["schema"], table],
        )
        table_count = cur.fetchone()[0]
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
               AND column_name IN ('q_1024_vec', 'q_1024_vec_valid')
            """,
            [gaussdb_env["schema"], table],
        )
        vector_columns = {row[0] for row in cur.fetchall()}
        cur.execute(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = %s
               AND tablename = %s
            """,
            [gaussdb_env["schema"], table],
        )
        index_defs = "\n".join(row[0] for row in cur.fetchall()).lower()

    assert table_count == 1
    assert vector_columns == {"q_1024_vec", "q_1024_vec_valid"}
    assert "using gsdiskann" in index_defs
    assert "using ugin" in index_defs


@pytest.mark.gaussdb_centralized
def test_tc_wrt_004_integration_vector_dimensions_create_expected_columns(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    for dim in (1, 4096):
        table = table_name(gaussdb_env, f"vec_{dim}")
        assert conn.create_idx(table, f"kb-{dim}", dim) is True
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = %s
                   AND table_name = %s
                   AND column_name IN (%s, %s)
                """,
                [gaussdb_env["schema"], table, f"q_{dim}_vec", f"q_{dim}_vec_valid"],
            )
            columns = {row[0] for row in cur.fetchall()}
        assert columns == {f"q_{dim}_vec", f"q_{dim}_vec_valid"}


def test_tc_ret_101_centralized_simple_parser_recalls_only_matching_chunk(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from common.doc_store.doc_store_base import MatchTextExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = "kb-ret-101"
    table = table_name(gaussdb_env, "ret_101")
    assert conn.create_idx(table, kb_id, 4) is True
    try:
        with gaussdb_admin_conn.cursor() as cur:
            cur.executemany(
                sql.SQL("INSERT INTO {} (id, kb_id, doc_id, content_with_weight, content_ltks, content_sm_ltks) VALUES (%s, %s, %s, %s, %s, %s)").format(sql.Identifier(gaussdb_env["schema"], table)),
                [
                    ("c1", kb_id, "d1", "hello world", "hello", "hello"),
                    ("c2", kb_id, "d2", "no match", "test", "test"),
                ],
            )
        gaussdb_admin_conn.commit()
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT id, content_ltks FROM {} ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], table)))
            assert cur.fetchall() == [("c1", "hello"), ("c2", "test")]

        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "content_with_weight"],
            condition={"kb_id": kb_id},
            keywords=["hello"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
            order_by=OrderByExpr(),
        )
        assert "to_tsvector('simple'" in search_sql
        assert "@@ plainto_tsquery('simple', %s)" in search_sql
        assert "'hello'" not in search_sql
        assert params.count("hello") >= 1

        result = conn.search(
            ["id", "content_with_weight", "_score"],
            [],
            {"kb_id": kb_id},
            [MatchTextExpr(["content_ltks"], "hello", 10)],
            OrderByExpr(),
            0,
            10,
            table,
            [kb_id],
        )
        assert result.total == 1
        assert [(row["id"], row["content_with_weight"]) for row in result.chunks] == [("c1", "hello world")]
        assert result.chunks[0]["_score"] > 0
    finally:
        gaussdb_admin_conn.rollback()
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(gaussdb_env["schema"], table)))
        gaussdb_admin_conn.commit()
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                [gaussdb_env["schema"], table],
            )
            assert cur.fetchone() == (0,)


mark_gaussdb_both_by_default(globals())
