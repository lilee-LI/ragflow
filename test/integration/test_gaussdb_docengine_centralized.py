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
from concurrent.futures import ThreadPoolExecutor

import pytest
import psycopg2
from psycopg2 import sql


def _search_ids(conn, table, kb_id, condition):
    from common.doc_store.doc_store_base import OrderByExpr

    result = conn.search(["id", "doc_id", "content_with_weight"], [], condition, [], OrderByExpr(), 0, 20, table, [kb_id])
    return conn.get_doc_ids(result)


def _explain(admin_conn, query, params=None):
    with admin_conn.cursor() as cur:
        cur.execute(sql.SQL("EXPLAIN ") + query, params or [])
        return "\n".join(row[0] for row in cur.fetchall())


def _index_definitions(admin_conn, schema, table):
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = %s
               AND tablename = %s
            """,
            [schema, table],
        )
        return "\n".join(row[0] for row in cur.fetchall()).lower()


def test_centralized_create_idx_creates_ustore_fts_and_vector_objects(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env)

    conn.create_idx(table, "kb-it", 4)

    assert conn.index_exist(table, "kb-it") is True
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


def test_centralized_insert_get_update_delete_chunk_roundtrip(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env)
    conn.create_idx(table, "kb-it", 4)

    assert conn.insert(
        [
            {
                "id": "c1",
                "kb_id": "kb-it",
                "doc_id": "d1",
                "content_with_weight": "hello",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        table,
        "kb-it",
    ) == []

    row = conn.get("c1", table, ["kb-it"])
    assert row["id"] == "c1"
    assert row["kb_id"] == "kb-it"
    assert row["q_4_vec"] == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert row["q_4_vec_valid"] is True
    assert conn.update({"id": "c1"}, {"pagerank_fea": 9}, table, "kb-it") is True
    assert conn.delete({"id": "c1"}, table, "kb-it") == 1


def test_centralized_insert_batch_rolls_back_on_db_error(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "rollback")
    kb_id = "kb-rollback"
    conn.create_idx(table, kb_id, 4)

    errors = conn.insert(
        [
            {
                "id": "before-error",
                "kb_id": kb_id,
                "doc_id": "doc-rollback",
                "content_with_weight": "must not commit",
                "metadata": {"ok": True},
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
            },
            {
                "id": "bad-json",
                "kb_id": kb_id,
                "doc_id": "doc-rollback",
                "content_with_weight": "invalid jsonb",
                "metadata": "not-json",
                "q_4_vec": [0.2, 0.3, 0.4, 0.5],
            },
        ],
        table,
        kb_id,
    )

    assert set(errors) == {"before-error", "bad-json"}
    assert conn.get("before-error", table, [kb_id]) is None
    assert _search_ids(conn, table, kb_id, {"doc_id": "doc-rollback"}) == []


def test_centralized_reparse_replaces_old_chunks_and_cancel_cleanup_removes_partial_chunks(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "reparse")
    kb_id = "kb-reparse"
    conn.create_idx(table, kb_id, 4)

    assert conn.insert(
        [
            {"id": "old-1", "kb_id": kb_id, "doc_id": "doc-reparse", "content_with_weight": "old chunk one", "q_4_vec": [0.1, 0.1, 0.1, 0.1]},
            {"id": "old-2", "kb_id": kb_id, "doc_id": "doc-reparse", "content_with_weight": "old chunk two", "q_4_vec": [0.2, 0.2, 0.2, 0.2]},
        ],
        table,
        kb_id,
    ) == []
    assert set(_search_ids(conn, table, kb_id, {"doc_id": "doc-reparse"})) == {"old-1", "old-2"}

    assert conn.delete({"doc_id": "doc-reparse"}, table, kb_id) == 2
    assert conn.insert(
        [
            {"id": "new-1", "kb_id": kb_id, "doc_id": "doc-reparse", "content_with_weight": "new chunk", "q_4_vec": [0.3, 0.3, 0.3, 0.3]},
        ],
        table,
        kb_id,
    ) == []
    assert _search_ids(conn, table, kb_id, {"doc_id": "doc-reparse"}) == ["new-1"]

    assert conn.insert(
        [
            {"id": "partial-1", "kb_id": kb_id, "doc_id": "doc-cancel", "content_with_weight": "partial chunk", "q_4_vec": [0.4, 0.4, 0.4, 0.4]},
        ],
        table,
        kb_id,
    ) == []
    assert _search_ids(conn, table, kb_id, {"doc_id": "doc-cancel"}) == ["partial-1"]
    assert conn.delete({"doc_id": "doc-cancel"}, table, kb_id) == 1
    assert _search_ids(conn, table, kb_id, {"doc_id": "doc-cancel"}) == []


def test_centralized_concurrent_create_idx_is_idempotent(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _i: conn.create_idx(table, "kb-it", 4), range(4)))

    assert all(result is True or result is None for result in results)
    assert conn.index_exist(table, "kb-it") is True


def test_centralized_vector_dimensions_create_expected_columns(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    for dim in (1024, 1536, 3072):
        table = table_name(gaussdb_env, f"vec_{dim}")
        conn.create_idx(table, f"kb-{dim}", dim)
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


def test_centralized_explain_records_fulltext_and_vector_plans(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "explain")
    conn.create_idx(table, "kb-explain", 4)
    assert conn.insert(
        [
            {
                "id": "c1",
                "kb_id": "kb-explain",
                "doc_id": "d1",
                "title_tks": "risk",
                "content_with_weight": "risk audit",
                "content_ltks": "risk audit",
                "content_sm_ltks": "risk audit",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        table,
        "kb-explain",
    ) == []

    fulltext_query = sql.SQL(
        "SELECT id FROM {} "
        "WHERE to_tsvector('simple', coalesce(title_tks, ' ') || ' ' || coalesce(content_sm_ltks, ' ')) "
        "@@ plainto_tsquery('simple', %s)"
    ).format(sql.Identifier(gaussdb_env["schema"], table))
    try:
        fulltext_plan = _explain(gaussdb_admin_conn, fulltext_query, ["risk"])
    except psycopg2.errors.StatementTooComplex:
        gaussdb_admin_conn.rollback()
        fulltext_plan = ""
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(fulltext_query, ["risk"])
        assert cur.fetchall() == [("c1",)]
    vector_plan = _explain(
        gaussdb_admin_conn,
        sql.SQL(
            "SELECT id FROM {} "
            "WHERE q_4_vec_valid = TRUE "
            "ORDER BY q_4_vec <+> %s::floatvector(4) LIMIT 1"
        ).format(sql.Identifier(gaussdb_env["schema"], table)),
        ["[0.1,0.2,0.3,0.4]"],
    )

    index_defs = _index_definitions(gaussdb_admin_conn, gaussdb_env["schema"], table)
    assert "using ugin" in index_defs
    assert "using gsdiskann" in index_defs

    fulltext_plan_lower = fulltext_plan.lower()
    vector_plan_lower = vector_plan.lower()
    if fulltext_plan_lower:
        assert (
            "ugin" in fulltext_plan_lower
            or "bitmap" in fulltext_plan_lower
            or "to_tsvector" in fulltext_plan_lower
        )
    assert "gsdiskann" in vector_plan_lower or "q_4_vec" in vector_plan_lower
