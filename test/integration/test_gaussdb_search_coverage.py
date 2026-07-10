# Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
import re
import time
import uuid

import pytest
import psycopg2
from psycopg2 import sql
from psycopg2.extras import execute_values

from gaussdb_test_markers import mark_gaussdb_both_by_default


pytestmark = pytest.mark.gaussdb_integration


_JSONB_COLUMNS = {
    "chunk_data",
    "entities_kwd",
    "extra",
    "important_kwd",
    "meta_fields",
    "metadata",
    "page_num_int",
    "position_int",
    "question_kwd",
    "source_id",
    "tag_feas",
    "tag_kwd",
    "top_int",
}
_VECTOR_COLUMN_RE = re.compile(r"^q_(\d+)_vec$")
_FTS_COLUMNS = (
    "title_tks",
    "title_sm_tks",
    "important_tks",
    "question_tks",
    "content_ltks",
    "content_sm_ltks",
)


@contextmanager
def _independent_connection(connect_attempts=1):
    conn = None
    for attempt in range(connect_attempts):
        try:
            conn = psycopg2.connect(
                host=os.environ["GAUSSDB_HOST"],
                port=int(os.environ.get("GAUSSDB_PORT", "19995")),
                dbname=os.environ["GAUSSDB_DATABASE"],
                user=os.environ["GAUSSDB_USER"],
                password=os.environ["GAUSSDB_PASSWORD"],
                connect_timeout=10,
                options="-c default_transaction_read_only=off",
            )
            break
        except psycopg2.OperationalError:
            if attempt + 1 == connect_attempts:
                raise
            time.sleep(1)
    try:
        yield conn
    finally:
        conn.close()


def _insert_rows_independently(schema: str, table: str, rows: list[dict]) -> None:
    expected_keys = sorted((str(row["id"]), str(row["kb_id"])) for row in rows)
    vector_probes = []
    with _independent_connection() as admin_conn:
        with admin_conn.cursor() as cur:
            for row in rows:
                columns = []
                placeholders = []
                params = []
                for column, value in row.items():
                    columns.append(sql.Identifier(column))
                    vector_match = _VECTOR_COLUMN_RE.fullmatch(column)
                    if vector_match:
                        dim = int(vector_match.group(1))
                        placeholders.append(sql.SQL("%s::floatvector({})").format(sql.SQL(str(dim))))
                        params.append(json.dumps(value, separators=(",", ":")))
                        valid_column = f"{column}_valid"
                        columns.append(sql.Identifier(valid_column))
                        placeholders.append(sql.SQL("%s"))
                        params.append(True)
                        vector_probes.append((str(row["id"]), valid_column))
                    elif column in _JSONB_COLUMNS:
                        placeholders.append(sql.SQL("%s::jsonb"))
                        params.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                    else:
                        placeholders.append(sql.SQL("%s"))
                        params.append(value)
                cur.execute(
                    sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(schema, table),
                        sql.SQL(", ").join(columns),
                        sql.SQL(", ").join(placeholders),
                    ),
                    params,
                )
        admin_conn.commit()
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT id, kb_id FROM {} ORDER BY id, kb_id").format(sql.Identifier(schema, table)))
            assert sorted((str(row[0]), str(row[1])) for row in cur.fetchall()) == expected_keys
            for chunk_id, valid_column in vector_probes:
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE id = %s").format(
                        sql.Identifier(valid_column),
                        sql.Identifier(schema, table),
                    ),
                    [chunk_id],
                )
                assert cur.fetchone() == (True,)


def _meta_table(env: dict, suffix: str) -> str:
    table = f"ragflow_doc_meta_{env['table_prefix']}_{suffix}"
    env["created_tables"].add(table)
    return table


def _new_kb() -> str:
    return uuid.uuid4().hex


def _create_chunks(conn, table: str, kb_id: str, rows: list[dict], dim: int = 4) -> None:
    assert conn.create_idx(table, kb_id, dim) is True
    _insert_rows_independently(conn.schema, table, rows)


def _search(conn, table, kb_id, *, fields=None, condition=None, matches=None, offset=0, limit=10, agg=None, rank=None):
    from common.doc_store.doc_store_base import OrderByExpr

    return conn.search(
        fields or ["id", "kb_id", "content_with_weight"],
        [],
        condition or {},
        matches or [],
        OrderByExpr(),
        offset,
        limit,
        table,
        [kb_id],
        agg_fields=agg,
        rank_feature=rank,
    )


def _delete_table(conn, table: str) -> None:
    with _independent_connection(connect_attempts=3) as admin_conn:
        with admin_conn.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(conn.schema, table)))
        admin_conn.commit()
    with _independent_connection(connect_attempts=3) as admin_conn:
        with admin_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                [conn.schema, table],
            )
            assert cur.fetchone() == (0,)


def _base_row(chunk_id: str, kb_id: str, content: str, **extra) -> dict:
    row = {
        "id": chunk_id,
        "kb_id": kb_id,
        "doc_id": f"doc-{chunk_id}",
        "docnm_kwd": f"{chunk_id}.txt",
        "content_with_weight": content,
        "content_ltks": content,
        "content_sm_ltks": content,
    }
    row.update(extra)
    return row


def _index_defs(admin_conn, schema: str, table: str) -> list[tuple[str, str]]:
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            [schema, table],
        )
        return cur.fetchall()


def _ugin_indexes(index_defs: list[tuple[str, str]], config: str | None = None) -> list[tuple[str, str]]:
    indexes = [(name, definition) for name, definition in index_defs if "using ugin" in definition.lower()]
    if config is None:
        return indexes
    marker = f"to_tsvector('{config}'"
    return [(name, definition) for name, definition in indexes if marker in definition.lower()]


def _table_oid(admin_conn, schema: str, table: str) -> int:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.oid
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND c.relname = %s
            """,
            [schema, table],
        )
        return int(cur.fetchone()[0])


def _table_rows(admin_conn, schema: str, table: str) -> list[tuple[str, str]]:
    with admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id, content_with_weight FROM {} ORDER BY id").format(sql.Identifier(schema, table))
        )
        return [(str(row[0]), str(row[1])) for row in cur.fetchall()]


def _strip_em_tags(text: str) -> str:
    return re.sub(r"</?em>", "", text)


def _explain_ugin_probe(admin_conn, schema: str, table: str, *, config: str, keyword: str) -> str:
    expression = " || ' ' || ".join(f"coalesce({column}, ' ')" for column in _FTS_COLUMNS)
    distributed = os.getenv("GAUSSDB_VARIANT") == "distributed"
    try:
        if distributed:
            admin_conn.rollback()
            previous_autocommit = admin_conn.autocommit
            admin_conn.autocommit = True
            try:
                with admin_conn.cursor() as cur:
                    cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(schema, table)))
            finally:
                admin_conn.autocommit = previous_autocommit

        with admin_conn.cursor() as cur:
            if not distributed:
                cur.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(schema, table)))
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute("SET LOCAL enable_indexscan = off")
            cur.execute("SET LOCAL enable_sort = off")
            if distributed:
                cur.execute("SET LOCAL enable_fast_query_shipping = off")
            cur.execute("SET LOCAL explain_perf_mode = 'normal'")
            cur.execute(
                sql.SQL(
                    f"EXPLAIN SELECT id FROM {{}} "
                    f"WHERE to_tsvector('{config}', {expression}) @@ plainto_tsquery('{config}', %s)"
                ).format(sql.Identifier(schema, table)),
                [keyword],
            )
            return "\n".join(str(row[0]) for row in cur.fetchall())
    finally:
        admin_conn.rollback()


def _explain_search(conn, admin_conn, table: str, kb_id: str, *, vector, vector_dim: int, vector_weight: float, keywords=None) -> str:
    from common.doc_store.doc_store_base import OrderByExpr
    from psycopg2 import sql

    search_sql, params = conn._search_builder().build_search_sql(
        table=table,
        select_fields=["id"],
        condition={"kb_id": kb_id},
        keywords=keywords or [],
        vector=vector,
        vector_dim=vector_dim,
        vector_weight=vector_weight,
        offset=0,
        limit=10,
        highlight_fields=[],
        order_by=OrderByExpr(),
    )
    try:
        distributed = os.getenv("GAUSSDB_VARIANT") == "distributed"
        if distributed:
            admin_conn.rollback()
            previous_autocommit = admin_conn.autocommit
            admin_conn.autocommit = True
            try:
                with admin_conn.cursor() as cur:
                    cur.execute(sql.SQL("ANALYZE {} ").format(sql.Identifier(conn.schema, table)))
            finally:
                admin_conn.autocommit = previous_autocommit

        with admin_conn.cursor() as cur:
            if not distributed:
                cur.execute(sql.SQL("ANALYZE {} ").format(sql.Identifier(conn.schema, table)))
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute("SET LOCAL enable_sort = off")
            if distributed:
                cur.execute("SET LOCAL enable_fast_query_shipping = off")
            cur.execute("SET LOCAL explain_perf_mode = 'normal'")
            cur.execute("SET LOCAL plan_cache_mode = 'force_custom_plan'")
            if "), vec AS (" in search_sql:
                explain_sql, replacements = re.subn(
                    r"(\), vec AS \(\s*)SELECT\b",
                    r"\1SELECT /*+ no_gpc use_cplan */",
                    search_sql,
                    count=1,
                    flags=re.IGNORECASE,
                )
                assert replacements == 1
            else:
                explain_sql = re.sub(r"\bSELECT\b", "SELECT /*+ no_gpc use_cplan */", search_sql, count=1, flags=re.IGNORECASE)
            cur.execute("EXPLAIN " + explain_sql, params)
            plan = "\n".join(str(row[0]) for row in cur.fetchall())
        return plan
    finally:
        admin_conn.rollback()


def test_tc_ret_001_single_chunk_search(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_001")
    try:
        _create_chunks(
            conn,
            table,
            kb_id,
            [_base_row("c1", kb_id, "hello world"), _base_row("c2", kb_id, "test content")],
        )
        result = _search(conn, table, kb_id)
        assert type(result).__name__ == "SearchResult"
        assert result.total == 2
        assert result.chunks == [
            {"id": "c1", "kb_id": kb_id, "content_with_weight": "hello world", "_score": 0.0},
            {"id": "c2", "kb_id": kb_id, "content_with_weight": "test content", "_score": 0.0},
        ]
    finally:
        _delete_table(conn, table)


def test_tc_ret_002_multi_table_search_merges_and_sorts(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    first = table_name(gaussdb_env, "ret_002_a")
    second = table_name(gaussdb_env, "ret_002_b")
    try:
        _create_chunks(conn, first, kb_id, [_base_row("b", kb_id, "beta")])
        _create_chunks(conn, second, kb_id, [_base_row("a", kb_id, "alpha")])
        result = conn.search(["id", "kb_id"], [], {"kb_id": kb_id}, [], OrderByExpr(), 0, 10, [first, second], [kb_id])
        assert result.total == 2
        assert [row["id"] for row in result.chunks] == ["a", "b"]
        assert conn.get_doc_ids(result) == ["a", "b"]
    finally:
        _delete_table(conn, first)
        _delete_table(conn, second)


def test_tc_ret_003_doc_meta_fast_path(gaussdb_env, register_table):
    from common.doc_store.doc_store_base import OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    other_kb_id = "f" * 32
    table = _meta_table(gaussdb_env, "ret_003")
    register_table(table)
    try:
        assert conn.create_doc_meta_idx(table) is True
        _insert_rows_independently(
            conn.schema,
            table,
            [
                {"id": "d1", "kb_id": kb_id, "meta_fields": {"status": "active"}},
                {"id": "d2", "kb_id": kb_id, "meta_fields": {"status": "inactive"}},
            ],
        )
        result = conn.search(
            ["id", "kb_id", "meta_fields", "id"],
            [],
            {"kb_id": kb_id},
            [],
            OrderByExpr().desc("id"),
            0,
            10,
            table,
            [kb_id, other_kb_id, kb_id],
        )
        assert result.total == 2
        assert result.chunks == [
            {"id": "d2", "kb_id": kb_id, "meta_fields": {"status": "inactive"}},
            {"id": "d1", "kb_id": kb_id, "meta_fields": {"status": "active"}},
        ]
    finally:
        _delete_table(conn, table)


def test_tc_ret_005_single_field_aggregation(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_005")
    try:
        _create_chunks(
            conn,
            table,
            kb_id,
            [
                _base_row("c1", kb_id, "one", doc_type_kwd="active"),
                _base_row("c2", kb_id, "two", doc_type_kwd="active"),
                _base_row("c3", kb_id, "three", doc_type_kwd="inactive"),
            ],
        )
        result = _search(conn, table, kb_id, fields=["id"], agg=["doc_type_kwd"])
        assert result.total == 2
        assert result.chunks == [{"value": "active", "count": 2}, {"value": "inactive", "count": 1}]
        assert all(set(row) == {"value", "count"} for row in result.chunks)
        assert all("id" not in row and "kb_id" not in row for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_009_deep_page_returns_real_total(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_009")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("c1", kb_id, "hello")])
        result = _search(conn, table, kb_id, fields=["id"], offset=1000, limit=10)
        assert result.total == 1
        assert result.chunks == []
    finally:
        _delete_table(conn, table)


def test_tc_ret_010_multi_table_zero_limit_uses_collection_limit(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    first = table_name(gaussdb_env, "ret_010_a")
    second = table_name(gaussdb_env, "ret_010_b")
    try:
        assert conn.create_idx(first, kb_id, 4) is True
        assert conn.create_idx(second, kb_id, 4) is True
        seeds = ((first, "a", 5001, "alpha"), (second, "b", 5000, "beta"))
        with _independent_connection() as admin_conn:
            with admin_conn.cursor() as cur:
                for table, prefix, count, content in seeds:
                    statement = sql.SQL("INSERT INTO {} (id, kb_id, content_with_weight) VALUES %s").format(
                        sql.Identifier(conn.schema, table)
                    )
                    execute_values(
                        cur,
                        statement.as_string(cur),
                        [(f"{prefix}-{index:05d}", kb_id, content) for index in range(count)],
                        page_size=1000,
                    )
            admin_conn.commit()
            with admin_conn.cursor() as cur:
                for table, prefix, count, _ in seeds:
                    cur.execute(
                        sql.SQL("SELECT COUNT(*), MIN(id), MAX(id) FROM {} WHERE kb_id = %s").format(
                            sql.Identifier(conn.schema, table)
                        ),
                        [kb_id],
                    )
                    assert cur.fetchone() == (count, f"{prefix}-00000", f"{prefix}-{count - 1:05d}")

        result = conn.search(
            ["id"],
            [],
            {},
            [],
            OrderByExpr().asc("id"),
            0,
            0,
            [first, second],
            [kb_id],
        )
        result_ids = [row["id"] for row in result.chunks]
        assert result.total == 10001
        assert len(result_ids) == 10000
        assert result_ids[0] == "a-00000"
        assert result_ids[-1] == "b-04998"
        assert "b-04999" not in result_ids
    finally:
        _delete_table(conn, first)
        _delete_table(conn, second)


def test_tc_ret_102_ugin_index_and_fulltext_hit(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_102")
    try:
        rows = [_base_row("hit", kb_id, "audit contract", title_tks="contract")]
        rows.extend(_base_row(f"miss-{i}", kb_id, "budget", title_tks="budget") for i in range(199))
        _create_chunks(conn, table, kb_id, rows)
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["content_with_weight"], "contract", 10)])
        scores = conn.get_scores(result)
        assert conn.get_doc_ids(result) == ["hit"]
        assert scores["hit"] > 0
        index_defs = _index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table)
        simple_ugin_indexes = _ugin_indexes(index_defs, "simple")
        ngram_ugin_indexes = _ugin_indexes(index_defs, "ngram")
        assert len(simple_ugin_indexes) == 1
        assert len(ngram_ugin_indexes) == 1
        plan = _explain_search(conn, gaussdb_admin_conn, table, kb_id, vector=None, vector_dim=None, vector_weight=0.0, keywords=["contract"])
        assert simple_ugin_indexes[0][0].lower() in plan.lower()
    finally:
        _delete_table(conn, table)


def test_tc_ret_109_dual_ugin_supports_chinese_english_mixed_and_inplace_upgrade(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from common.doc_store.doc_store_base import MatchDenseExpr, MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_109")
    target_vector = [1.0, *([0.0] * 1023)]
    orthogonal_vector = [0.0, 1.0, *([0.0] * 1022)]
    try:
        _create_chunks(
            conn,
            table,
            kb_id,
            [
                _base_row("en-hit", kb_id, "audit contract report"),
                _base_row("mixed-en-only", kb_id, "audit compliance only"),
                _base_row("mixed-hit", kb_id, "深圳 audit 联合检查"),
                _base_row("mixed-zh-only", kb_id, "深圳 联合检查"),
                _base_row("zh-hit", kb_id, "深圳数据库审计"),
                _base_row("zh-miss", kb_id, "北京财务报表"),
                _base_row("single-char-vector-hit", kb_id, "单字向量目标", q_1024_vec=target_vector),
                _base_row("single-char-vector-miss", kb_id, "单字向量负样本", q_1024_vec=orthogonal_vector),
            ]
            + [_base_row(f"zh-noise-{i}", kb_id, f"budget filler noise {i}") for i in range(240)],
            dim=1024,
        )
        initial_oid = _table_oid(gaussdb_admin_conn, gaussdb_env["schema"], table)
        initial_rows = _table_rows(gaussdb_admin_conn, gaussdb_env["schema"], table)
        initial_index_defs = _index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table)
        initial_simple_ugin = _ugin_indexes(initial_index_defs, "simple")
        initial_ngram_ugin = _ugin_indexes(initial_index_defs, "ngram")
        assert len(initial_simple_ugin) == 1
        assert len(initial_ngram_ugin) == 1
        for _name, definition in [initial_simple_ugin[0], initial_ngram_ugin[0]]:
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

        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP INDEX {}.{}").format(
                    sql.Identifier(gaussdb_env["schema"]),
                    sql.Identifier(initial_ngram_ugin[0][0]),
                )
            )
        gaussdb_admin_conn.commit()

        dropped_index_defs = _index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table)
        assert len(_ugin_indexes(dropped_index_defs, "simple")) == 1
        assert len(_ugin_indexes(dropped_index_defs, "ngram")) == 0
        assert _table_oid(gaussdb_admin_conn, gaussdb_env["schema"], table) == initial_oid
        assert _table_rows(gaussdb_admin_conn, gaussdb_env["schema"], table) == initial_rows

        gaussdb_admin_conn.rollback()
        assert conn.create_idx(table, kb_id, 1024) is True
        rebuilt_index_defs = _index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table)
        rebuilt_simple_ugin = _ugin_indexes(rebuilt_index_defs, "simple")
        rebuilt_ngram_ugin = _ugin_indexes(rebuilt_index_defs, "ngram")
        assert len(rebuilt_simple_ugin) == 1
        assert len(rebuilt_ngram_ugin) == 1
        assert _table_oid(gaussdb_admin_conn, gaussdb_env["schema"], table) == initial_oid
        assert _table_rows(gaussdb_admin_conn, gaussdb_env["schema"], table) == initial_rows

        zh_result = conn.search(
            ["id", "content_with_weight", "_score"],
            ["content_with_weight"],
            {"kb_id": kb_id},
            [MatchTextExpr(["content_with_weight"], "数据库审计", 10)],
            None,
            0,
            10,
            table,
            [kb_id],
        )
        assert conn.get_doc_ids(zh_result) == ["zh-hit"]
        assert zh_result.total == 1
        zh_scores = conn.get_scores(zh_result)
        assert zh_scores["zh-hit"] > 0
        zh_highlight = conn.get_highlight(zh_result, ["数据库审计"], "content_with_weight")["zh-hit"]
        assert zh_result.chunks[0]["_highlight_source"] == _strip_em_tags(zh_highlight)
        assert zh_highlight.count("<em>") == 1
        assert _strip_em_tags(zh_highlight) == "深圳数据库审计"

        en_result = _search(
            conn,
            table,
            kb_id,
            fields=["id", "_score"],
            matches=[MatchTextExpr(["content_with_weight"], "contract", 10)],
        )
        assert conn.get_doc_ids(en_result) == ["en-hit"]
        assert en_result.total == 1
        en_scores = conn.get_scores(en_result)
        assert en_scores["en-hit"] > 0

        mixed_sql, mixed_params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "_score"],
            condition={"kb_id": kb_id},
            keywords=["深圳 audit"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
            highlight_fields=["content_with_weight"],
        )
        assert "plainto_tsquery('simple', %s)" in mixed_sql
        assert "plainto_tsquery('ngram', %s)" in mixed_sql
        assert "audit" in mixed_params
        assert "深圳" in mixed_params

        mixed_result = conn.search(
            ["id", "content_with_weight", "_score"],
            ["content_with_weight"],
            {"kb_id": kb_id},
            [MatchTextExpr(["content_with_weight"], "深圳 audit", 10)],
            None,
            0,
            10,
            table,
            [kb_id],
        )
        assert conn.get_doc_ids(mixed_result) == ["mixed-hit"]
        assert mixed_result.total == 1
        mixed_scores = conn.get_scores(mixed_result)
        assert mixed_scores["mixed-hit"] > 0
        assert mixed_result.chunks[0]["_highlight_source"] == "深圳 audit 联合检查"
        mixed_highlight = conn.get_highlight(mixed_result, ["深圳 audit"], "content_with_weight")["mixed-hit"]
        assert mixed_result.chunks[0]["_highlight_source"] == _strip_em_tags(mixed_highlight)
        assert mixed_highlight.count("<em>") == 2
        assert _strip_em_tags(mixed_highlight) == "深圳 audit 联合检查"
        assert "<em>深圳</em>" in mixed_highlight
        assert "<em>audit</em>" in mixed_highlight.lower()

        single_character_text_result = conn.search(
            ["id", "_score"],
            [],
            {"kb_id": kb_id},
            [MatchTextExpr(["content_with_weight"], "中", 10)],
            None,
            0,
            10,
            table,
            [kb_id],
        )
        assert single_character_text_result.total == 0
        assert single_character_text_result.chunks == []

        single_character_vector_sql, _single_character_vector_params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "q_1024_vec", "_score"],
            condition={"kb_id": kb_id},
            keywords=["中"],
            vector=target_vector,
            vector_dim=1024,
            vector_weight=0.5,
            similarity_threshold=0.0,
            topn=1,
            offset=0,
            limit=1,
        )
        assert "WITH vec AS" in single_character_vector_sql
        assert "fts_raw" not in single_character_vector_sql
        assert "plainto_tsquery('simple'" not in single_character_vector_sql
        assert "plainto_tsquery('ngram'" not in single_character_vector_sql

        single_character_vector_result = conn.search(
            ["id", "q_1024_vec", "_score"],
            [],
            {"kb_id": kb_id},
            [
                MatchTextExpr(["content_with_weight"], "中", 10),
                MatchDenseExpr(
                    "q_1024_vec",
                    target_vector,
                    "float",
                    "cosine",
                    1,
                    {"similarity": 0.0},
                ),
            ],
            None,
            0,
            1,
            table,
            [kb_id],
        )
        assert conn.get_doc_ids(single_character_vector_result) == ["single-char-vector-hit"]
        assert single_character_vector_result.total == 1
        assert single_character_vector_result.chunks[0]["q_1024_vec_valid"] is True
        assert conn.get_scores(single_character_vector_result)["single-char-vector-hit"] == pytest.approx(
            1.0,
            abs=1e-6,
        )
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "SELECT id, q_1024_vec_valid, q_1024_vec <+> %s::floatvector(1024) AS distance "
                    "FROM {} WHERE kb_id = %s AND id IN (%s, %s) ORDER BY distance, id"
                ).format(sql.Identifier(gaussdb_env["schema"], table)),
                [
                    json.dumps(target_vector, separators=(",", ":")),
                    kb_id,
                    "single-char-vector-hit",
                    "single-char-vector-miss",
                ],
            )
            single_character_vector_rows = cur.fetchall()
        assert [row[0] for row in single_character_vector_rows] == [
            "single-char-vector-hit",
            "single-char-vector-miss",
        ]
        assert all(row[1] is True for row in single_character_vector_rows)
        assert [float(row[2]) for row in single_character_vector_rows] == pytest.approx(
            [0.0, 1.0],
            abs=1e-6,
        )

        zh_plan = _explain_ugin_probe(
            gaussdb_admin_conn,
            gaussdb_env["schema"],
            table,
            config="ngram",
            keyword="数据库审计",
        )
        assert rebuilt_ngram_ugin[0][0].lower() in zh_plan.lower()
        assert "seq scan" not in zh_plan.lower()
        assert "bitmap" in zh_plan.lower() or "index scan" in zh_plan.lower()

        en_plan = _explain_ugin_probe(
            gaussdb_admin_conn,
            gaussdb_env["schema"],
            table,
            config="simple",
            keyword="contract",
        )
        assert rebuilt_simple_ugin[0][0].lower() in en_plan.lower()
        assert "seq scan" not in en_plan.lower()
        assert "bitmap" in en_plan.lower() or "index scan" in en_plan.lower()
    finally:
        _delete_table(conn, table)


def test_tc_ret_103_fulltext_null_tokens_do_not_propagate(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_103")
    try:
        _create_chunks(
            conn,
            table,
            kb_id,
            [
                _base_row("hit", kb_id, "risk review", title_tks=None, important_tks=None, question_tks=None),
                _base_row("miss", kb_id, "unrelated", title_tks=None, important_tks=None, question_tks=None),
            ],
        )
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["content_ltks"], "risk", 10)])
        fts_expr = conn._search_builder().build_fts_vector_expr()
        for field in ("title_tks", "title_sm_tks", "important_tks", "question_tks", "content_ltks", "content_sm_ltks"):
            assert f"coalesce({field}, ' ')" in fts_expr
        assert conn.get_doc_ids(result) == ["hit"]
        assert conn.get_scores(result)["hit"] > 0
        assert all(row.get("_score", 0) > 0 for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_105_fulltext_field_weights_affect_score(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_105")
    try:
        _create_chunks(
            conn,
            table,
            kb_id,
            [
                _base_row("important", kb_id, "irrelevant", important_tks="signal"),
                _base_row("question", kb_id, "irrelevant", question_tks="signal"),
                _base_row("title", kb_id, "irrelevant", title_tks="signal"),
                _base_row("title-sm", kb_id, "irrelevant", title_sm_tks="signal"),
                _base_row("content", kb_id, "irrelevant", content_ltks="signal"),
                _base_row("content-sm", kb_id, "irrelevant", content_sm_ltks="signal"),
            ],
        )
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "_score"],
            condition={"kb_id": kb_id},
            keywords=["signal"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
        )
        for field, weight in (
            ("title_tks", 10.0),
            ("title_sm_tks", 5.0),
            ("important_tks", 20.0),
            ("question_tks", 20.0),
            ("content_ltks", 2.0),
            ("content_sm_ltks", 1.0),
        ):
            assert (
                f"{weight} * COALESCE(ts_rank(to_tsvector('simple', coalesce({field}, ' ')), "
                "plainto_tsquery('simple', %s)), 0)"
            ) in search_sql
        assert params == ["signal"] * 6 + [kb_id, "signal", 10, 0]
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["content_ltks"], "signal", 10)])
        scores = conn.get_scores(result)
        assert set(scores) == {"important", "question", "title", "title-sm", "content", "content-sm"}
        assert scores["important"] == scores["question"]
        assert scores["important"] > scores["title"] > scores["title-sm"] > scores["content"] > scores["content-sm"] > 0
    finally:
        _delete_table(conn, table)


def test_tc_ret_106_fulltext_tie_breaker_is_stable(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchTextExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    first_kb_id = "a" * 32
    second_kb_id = "b" * 32
    table = table_name(gaussdb_env, "ret_106")
    try:
        _create_chunks(
            conn,
            table,
            first_kb_id,
            [
                _base_row("z", first_kb_id, "same", title_tks="same"),
                _base_row("b", second_kb_id, "same", title_tks="same"),
                _base_row("a", second_kb_id, "same", title_tks="same"),
            ],
        )
        result = conn.search(
            ["id", "kb_id", "_score"],
            [],
            {},
            [MatchTextExpr(["title_tks"], "same", 10)],
            OrderByExpr(),
            0,
            10,
            table,
            [second_kb_id, first_kb_id],
        )
        assert [(row["kb_id"], row["id"]) for row in result.chunks] == [
            (first_kb_id, "z"),
            (second_kb_id, "a"),
            (second_kb_id, "b"),
        ]
        assert len(set(conn.get_scores(result).values())) == 1
    finally:
        _delete_table(conn, table)


def test_tc_ret_107_multi_field_ugin_search(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_107")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("title", kb_id, "alpha", title_tks="alpha"), _base_row("body", kb_id, "beta", content_ltks="alpha")])
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["title_tks", "content_ltks"], "alpha", 10)])
        assert set(conn.get_doc_ids(result)) == {"title", "body"}
        index_defs = _index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table)
        ugin_defs = _ugin_indexes(index_defs)
        assert len(_ugin_indexes(index_defs, "simple")) == 1
        assert len(_ugin_indexes(index_defs, "ngram")) == 1
        assert len(ugin_defs) == 2
        for _name, definition in ugin_defs:
            lowered = definition.lower()
            assert all(
                field in lowered
                for field in (
                    "title_tks",
                    "title_sm_tks",
                    "important_tks",
                    "question_tks",
                    "content_ltks",
                    "content_sm_ltks",
                )
            )
    finally:
        _delete_table(conn, table)


def test_tc_ret_201_vector_topk_and_positive_negative_recall(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_201")
    try:
        target = [1.0, *([0.0] * 1023)]
        far = [0.0, 1.0, *([0.0] * 1022)]
        _create_chunks(conn, table, kb_id, [_base_row("near", kb_id, "near", q_1024_vec=target), _base_row("far", kb_id, "far", q_1024_vec=far)], dim=1024)
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "content_with_weight"],
            condition={"kb_id": kb_id},
            keywords=None,
            vector=target,
            vector_dim=1024,
            vector_weight=1.0,
            offset=0,
            limit=1,
        )
        assert "WITH vec AS (" in search_sql
        assert "q_1024_vec <+> %s::floatvector(1024) AS distance" in search_sql
        assert "ORDER BY q_1024_vec <+> %s::floatvector(1024) ASC LIMIT %s" in search_sql
        assert params[-4:] == [1, 0.0, 1, 0]
        result = _search(
            conn,
            table,
            kb_id,
            fields=["id", "q_1024_vec", "_score"],
            matches=[MatchDenseExpr("q_1024_vec", target, "float", "cosine", 1, {"similarity": 0.0})],
            limit=1,
        )
        assert conn.get_doc_ids(result) == ["near"]
        assert result.chunks[0]["q_1024_vec_valid"] is True
        assert conn.get_scores(result)["near"] > 0.99
    finally:
        _delete_table(conn, table)


def test_tc_ret_202_gsdiskann_index_is_used_by_vector_path(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_202")
    try:
        target = [1.0, *([0.0] * 1023)]
        rows = [_base_row("hit", kb_id, "hit", q_1024_vec=target)]
        for i in range(199):
            noise = [0.0] * 1024
            noise[i + 1] = 1.0
            rows.append(_base_row(f"noise-{i}", kb_id, "noise", q_1024_vec=noise))
        _create_chunks(conn, table, kb_id, rows, dim=1024)
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchDenseExpr("q_1024_vec", target, "float", "cosine", 10, {"similarity": 0.0})])
        assert "hit" in conn.get_doc_ids(result)
        assert any("using gsdiskann" in indexdef.lower() for _, indexdef in _index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table))
        plan = _explain_search(conn, gaussdb_admin_conn, table, kb_id, vector=target, vector_dim=1024, vector_weight=1.0)
        diskann_index = conn.ddl.index_name(table, "q_1024_vec_diskann")
        assert f"index scan using {diskann_index}" in plan.lower(), plan
    finally:
        _delete_table(conn, table)


def test_tc_ret_203_invalid_vector_valid_flag_is_not_recalled(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_203")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("valid", kb_id, "valid", q_4_vec=[1.0, 0.0, 0.0, 0.0]), _base_row("placeholder", kb_id, "placeholder")])
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT q_4_vec_valid FROM {} WHERE kb_id = %s AND id = %s").format(
                    sql.Identifier(gaussdb_env["schema"], table)
                ),
                [kb_id, "placeholder"],
            )
            assert cur.fetchone() == (False,)
        gaussdb_admin_conn.rollback()
        result = _search(conn, table, kb_id, fields=["id", "q_4_vec", "_score"], matches=[MatchDenseExpr("q_4_vec", [1.0, 0.0, 0.0, 0.0], "float", "cosine", 10, {"similarity": 0.0})])
        assert conn.get_doc_ids(result) == ["valid"]
        assert result.chunks[0]["q_4_vec_valid"] is True
        assert all(row["id"] != "placeholder" for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_204_vector_threshold_is_applied_to_final_score(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_204")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("near", kb_id, "near", q_4_vec=[1.0, 0.0, 0.0, 0.0]), _base_row("below", kb_id, "below", q_4_vec=[0.7, 0.7, 0.0, 0.0])])
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "_score"],
            condition={"kb_id": kb_id},
            keywords=None,
            vector=[1.0, 0.0, 0.0, 0.0],
            vector_dim=4,
            vector_weight=1.0,
            similarity_threshold=0.95,
            offset=0,
            limit=10,
        )
        inner_sql, outer_sql = search_sql.split(") SELECT vec.*, COUNT(*) OVER() AS __total FROM vec ", 1)
        assert "WHERE _score" not in inner_sql
        assert outer_sql.startswith("WHERE _score >= %s ")
        assert params[-3:] == [0.95, 10, 0]
        unfiltered = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchDenseExpr("q_4_vec", [1.0, 0.0, 0.0, 0.0], "float", "cosine", 10, {"similarity": 0.0})])
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchDenseExpr("q_4_vec", [1.0, 0.0, 0.0, 0.0], "float", "cosine", 10, {"similarity": 0.95})])
        assert set(conn.get_doc_ids(unfiltered)) == {"near", "below"}
        assert conn.get_doc_ids(result) == ["near"]
        assert all(row["_score"] >= 0.95 for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_208_missing_vector_column_is_rejected_by_database(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from psycopg2 import errors
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_208")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("c1", kb_id, "one", q_1024_vec=[1.0] * 1024)], dim=1024)
        with pytest.raises(errors.UndefinedColumn, match="q_512_vec"):
            _search(
                conn,
                table,
                kb_id,
                fields=["id"],
                matches=[MatchDenseExpr("q_512_vec", [1.0] * 512, "float", "cosine", 10)],
            )
        recovery = _search(conn, table, kb_id, fields=["id"])
        assert conn.get_doc_ids(recovery) == ["c1"]
    finally:
        _delete_table(conn, table)


def test_tc_ret_210_pagerank_changes_vector_score(gaussdb_env, table_name):
    from common import constants
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_210")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("near", kb_id, "near", q_4_vec=[1.0, 0.0, 0.0, 0.0], pagerank_fea=0), _base_row("ranked", kb_id, "ranked", q_4_vec=[0.9, 0.1, 0.0, 0.0], pagerank_fea=100)])
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id"],
            condition={"kb_id": kb_id},
            keywords=None,
            vector=[1.0, 0.0, 0.0, 0.0],
            vector_dim=4,
            vector_weight=1.0,
            offset=0,
            limit=10,
            pagerank_weight=1.0,
        )
        assert "COALESCE(pagerank_fea, 0)::DOUBLE PRECISION / 100.0 * %s" in search_sql
        assert 1.0 in params
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchDenseExpr("q_4_vec", [1.0, 0.0, 0.0, 0.0], "float", "cosine", 10, {"similarity": 0.0})], rank={constants.PAGERANK_FLD: 1})
        scores = conn.get_scores(result)
        assert scores["ranked"] > scores["near"]
    finally:
        _delete_table(conn, table)


def _hybrid_matches(weight: float, threshold: float = 0.0, vector_field: str = "q_4_vec", vector=None):
    from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchTextExpr

    vector = [1.0, 0.0, 0.0, 0.0] if vector is None else vector
    return [
        MatchTextExpr(["content_ltks"], "alpha", 10),
        MatchDenseExpr(vector_field, vector, "float", "cosine", 10, {"similarity": threshold}),
        FusionExpr("weighted_sum", 10, {"weights": f"{1 - weight},{weight}"}),
    ]


def _hybrid_sql(conn, table: str, kb_id: str, weight: float, threshold: float = 0.0, *, vector=None, dim: int = 4):
    vector = [1.0, 0.0, 0.0, 0.0] if vector is None else vector
    return conn._search_builder().build_search_sql(
        table=table,
        select_fields=["id", "content_with_weight"],
        condition={"kb_id": kb_id},
        keywords=["alpha"],
        vector=vector,
        vector_dim=dim,
        vector_weight=weight,
        similarity_threshold=threshold,
        offset=0,
        limit=10,
    )


def test_tc_ret_302_hybrid_scores_are_normalized_and_positive(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_302")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("text", kb_id, "alpha", title_tks="alpha"), _base_row("vector", kb_id, "beta", q_4_vec=[1.0, 0.0, 0.0, 0.0]), _base_row("both", kb_id, "alpha", title_tks="alpha", q_4_vec=[0.9, 0.1, 0.0, 0.0])])
        search_sql, _params = _hybrid_sql(conn, table, kb_id, 0.5)
        assert "COALESCE(raw_fts_score / NULLIF(MAX(raw_fts_score) OVER (), 0), 0) AS fts_score" in search_sql
        assert search_sql.index("WITH fts_raw AS (") < search_sql.index("), fts AS (") < search_sql.index("), vec AS (") < search_sql.index("), merged AS (")
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.5))
        scores = conn.get_scores(result)
        assert set(scores) == {"text", "vector", "both"}
        assert all(0.0 <= score <= 1.0 for score in scores.values())
        both_vector_similarity = 0.9 / math.sqrt(0.9**2 + 0.1**2)
        assert scores == pytest.approx(
            {
                "both": 0.5 + 0.5 * both_vector_similarity,
                "text": 0.5,
                "vector": 0.5,
            },
            abs=1e-6,
        )
        assert conn.get_doc_ids(result) == ["both", "text", "vector"]
    finally:
        _delete_table(conn, table)


def test_tc_ret_304_hybrid_returns_fulltext_when_vector_leg_is_empty(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_304")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("text", kb_id, "alpha", title_tks="alpha")])
        result = _search(conn, table, kb_id, fields=["id", "content_with_weight", "_score"], matches=_hybrid_matches(0.5))
        assert conn.get_doc_ids(result) == ["text"]
        assert result.total == 1
        assert result.chunks[0]["content_with_weight"] == "alpha"
        assert result.chunks[0]["_score"] > 0
    finally:
        _delete_table(conn, table)


def test_tc_ret_305_hybrid_zero_text_score_does_not_fail(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_305")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("vector", kb_id, "beta", q_4_vec=[1.0, 0.0, 0.0, 0.0])])
        search_sql, _params = _hybrid_sql(conn, table, kb_id, 0.7)
        assert "COALESCE(raw_fts_score / NULLIF(MAX(raw_fts_score) OVER (), 0), 0) AS fts_score" in search_sql
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.7))
        assert result.total == 1
        assert conn.get_scores(result)["vector"] == pytest.approx(0.7, abs=1e-6)
    finally:
        _delete_table(conn, table)


def test_tc_ret_306_hybrid_threshold_filters_final_score(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_306")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("hit", kb_id, "alpha", title_tks="alpha", q_4_vec=[1.0, 0.0, 0.0, 0.0]), _base_row("below", kb_id, "alpha", content_ltks="alpha", q_4_vec=[0.0, 1.0, 0.0, 0.0])])
        search_sql, params = _hybrid_sql(conn, table, kb_id, 0.5, 0.8)
        assert "WHERE merged.score >= %s" in search_sql
        assert search_sql.rindex("WHERE merged.score >= %s") > search_sql.index("), merged AS (")
        assert params[-3:] == [0.8, 10, 0]
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.5, 0.8))
        assert conn.get_doc_ids(result) == ["hit"]
        assert all(row["_score"] >= 0.8 for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_307_hybrid_returns_only_fused_candidates(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_307")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("candidate", kb_id, "alpha", title_tks="alpha", q_4_vec=[1.0, 0.0, 0.0, 0.0]), _base_row("negative", kb_id, "omega", title_tks="omega")])
        search_sql, _params = _hybrid_sql(conn, table, kb_id, 0.5)
        assert f'FROM merged JOIN "{conn.schema}"."{table}" c ON c.kb_id = merged.kb_id AND c.id = merged.id' in search_sql
        final_select = search_sql[search_sql.rindex("SELECT ") :]
        assert "c.id" in final_select and "c.kb_id" in final_select and "c.content_with_weight" in final_select
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.5))
        assert conn.get_doc_ids(result) == ["candidate"]
        assert "negative" not in conn.get_scores(result)
    finally:
        _delete_table(conn, table)


def test_tc_ret_308_hybrid_vector_leg_has_gsdiskann_index(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_308")
    try:
        target = [1.0, *([0.0] * 1023)]
        rows = [_base_row("hit", kb_id, "alpha", title_tks="alpha", q_1024_vec=target)]
        for i in range(199):
            noise = [0.0] * 1024
            noise[i + 1] = 1.0
            rows.append(_base_row(f"noise-{i}", kb_id, "alpha", title_tks="alpha", q_1024_vec=noise))
        _create_chunks(conn, table, kb_id, rows, dim=1024)
        matches = _hybrid_matches(0.5, vector_field="q_1024_vec", vector=target)
        search_sql, _params = _hybrid_sql(conn, table, kb_id, 0.5, vector=target, dim=1024)
        assert search_sql.index("WITH fts_raw AS (") < search_sql.index("), fts AS (") < search_sql.index("), vec AS (") < search_sql.index("), merged AS (")
        vec_cte = search_sql.split("), vec AS (", 1)[1].split("), merged AS (", 1)[0]
        assert f'FROM "{conn.schema}"."{table}" WHERE' in vec_cte
        assert "q_1024_vec_valid = TRUE" in vec_cte
        assert "ORDER BY q_1024_vec <+> %s::floatvector(1024) ASC LIMIT %s" in vec_cte
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=matches)
        assert "hit" in conn.get_doc_ids(result)
        diskann_index = conn.ddl.index_name(table, "q_1024_vec_diskann")
        index_defs = dict(_index_defs(gaussdb_admin_conn, gaussdb_env["schema"], table))
        assert diskann_index in index_defs
        assert "using gsdiskann" in index_defs[diskann_index].lower()
        plan = _explain_search(conn, gaussdb_admin_conn, table, kb_id, vector=target, vector_dim=1024, vector_weight=0.5, keywords=["alpha"])
        assert f"index scan using {diskann_index}" in plan.lower(), plan
    finally:
        _delete_table(conn, table)


def test_tc_ret_309_hybrid_empty_both_legs_returns_empty(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_309")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("invalid", kb_id, "omega")])
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.5))
        assert result.total == 0
        assert result.chunks == []
    finally:
        _delete_table(conn, table)


def test_tc_ret_311_zero_vector_weight_keeps_term_scores(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_311")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("term", kb_id, "alpha", title_tks="alpha", q_4_vec=[1.0, 0.0, 0.0, 0.0]), _base_row("negative", kb_id, "beta", title_tks="beta")])
        search_sql, params = _hybrid_sql(conn, table, kb_id, 0.0)
        assert "(1 - %s) * COALESCE(fts.fts_score, 0) + %s * COALESCE(vec.vector_score, 0) AS score" in search_sql
        assert params[-5:] == [0.0, 0.0, 0.0, 10, 0]
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.0))
        assert conn.get_doc_ids(result) == ["term"]
        assert conn.get_scores(result)["term"] > 0
    finally:
        _delete_table(conn, table)


def _metadata_seed(conn, env, suffix: str):
    kb_id = _new_kb()
    meta = _meta_table(env, suffix)
    table = f"ragflow_it_{env['table_prefix']}_{suffix}_chunks"
    env["created_tables"].add(table)
    assert conn.create_doc_meta_idx(meta) is True
    _insert_rows_independently(
        conn.schema,
        meta,
        [
            {"id": "d1", "kb_id": kb_id, "meta_fields": {"status": "active", "author": "Alice"}},
            {"id": "d2", "kb_id": kb_id, "meta_fields": {"status": "inactive", "author": "Bob"}},
        ],
    )
    _create_chunks(conn, table, kb_id, [_base_row("c1", kb_id, "hello", doc_id="d1"), _base_row("c2", kb_id, "hello", doc_id="d2")])
    return kb_id, meta, table


def _metadata_retrieve(conn, meta, table, kb_id, *, status="active"):
    doc_ids = conn.fetch_metadata_doc_ids(meta, [kb_id], "meta_fields @> %s::jsonb", [f'{{"status":"{status}"}}'], 10)
    result = _search(conn, table, kb_id, fields=["id", "doc_id", "content_with_weight"], condition={"kb_id": kb_id, "doc_id": doc_ids}, matches=[])
    return doc_ids, result


def test_tc_ret_401_metadata_condition_pushdown_combines_with_retrieval(gaussdb_env):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id, meta, table = _metadata_seed(conn, gaussdb_env, "ret_401")
    try:
        doc_ids, result = _metadata_retrieve(conn, meta, table, kb_id)
        where_sql, where_params = conn._search_builder().build_condition_where({"kb_id": kb_id, "doc_id": doc_ids})
        assert where_sql == "kb_id = %s AND doc_id IN (%s)"
        assert where_params == [kb_id, "d1"]
        assert doc_ids == ["d1"]
        assert conn.get_doc_ids(result) == ["c1"]
        assert result.chunks[0]["doc_id"] == "d1"
    finally:
        _delete_table(conn, meta)
        _delete_table(conn, table)


def test_tc_ret_402_metadata_doc_id_list_is_an_in_filter(gaussdb_env):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id, meta, table = _metadata_seed(conn, gaussdb_env, "ret_402")
    try:
        doc_ids = conn.fetch_metadata_doc_ids(meta, [kb_id], "meta_fields @> %s::jsonb", ['{"status":"active"}'], 10)
        result = _search(
            conn,
            table,
            kb_id,
            fields=["id", "doc_id", "content_with_weight"],
            condition={"kb_id": kb_id, "doc_id": doc_ids},
            matches=[MatchTextExpr(["content_with_weight"], "hello", 10)],
        )
        where_sql, where_params = conn._search_builder().build_condition_where({"kb_id": kb_id, "doc_id": doc_ids})
        assert where_sql == "kb_id = %s AND doc_id IN (%s)"
        assert where_params == [kb_id, "d1"]
        assert doc_ids == ["d1"]
        assert result.total == 1 and conn.get_doc_ids(result) == ["c1"]
    finally:
        _delete_table(conn, meta)
        _delete_table(conn, table)


def test_tc_ret_403_metadata_fast_filter_returns_scoped_chunk(gaussdb_env):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id, meta, table = _metadata_seed(conn, gaussdb_env, "ret_403")
    try:
        doc_ids, result = _metadata_retrieve(conn, meta, table, kb_id)
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "doc_id", "content_with_weight"],
            condition={"kb_id": kb_id, "doc_id": doc_ids},
            keywords=["hello"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
        )
        assert "doc_id IN (%s)" in search_sql
        assert "WITH meta_cte" not in search_sql
        assert kb_id in params and "d1" in params and "hello" in params and params[-2:] == [10, 0]
        text_result = _search(conn, table, kb_id, fields=["id", "doc_id"], condition={"kb_id": kb_id, "doc_id": doc_ids}, matches=[MatchTextExpr(["content_ltks"], "hello", 10)])
        assert doc_ids == ["d1"]
        assert {row["doc_id"] for row in result.chunks} == {"d1"}
        assert conn.get_doc_ids(text_result) == ["c1"]
        assert all(row["kb_id"] == kb_id for row in result.chunks)
    finally:
        _delete_table(conn, meta)
        _delete_table(conn, table)


def test_tc_ret_404_metadata_filter_does_not_require_fulltext_hit(gaussdb_env):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id, meta, table = _metadata_seed(conn, gaussdb_env, "ret_404")
    try:
        doc_ids, result = _metadata_retrieve(conn, meta, table, kb_id)
        assert doc_ids == ["d1"]
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id", "doc_id"],
            condition={"kb_id": kb_id, "doc_id": doc_ids},
            keywords=["hello"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
        )
        assert "doc_id IN (%s)" in search_sql and "WITH meta_cte" not in search_sql
        assert kb_id in params and "d1" in params
        text_result = _search(conn, table, kb_id, fields=["id", "doc_id"], condition={"kb_id": kb_id, "doc_id": doc_ids}, matches=[MatchTextExpr(["content_ltks"], "hello", 10)])
        assert conn.get_doc_ids(text_result) == ["c1"]
        assert text_result.chunks[0]["doc_id"] == result.chunks[0]["doc_id"]
    finally:
        _delete_table(conn, meta)
        _delete_table(conn, table)


def test_tc_ret_405_metadata_conditions_have_real_positive_negative_results(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_405")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("active", kb_id, "hello", doc_type_kwd="active", tag_kwd=["t1", "t2"]), _base_row("empty", kb_id, "world", doc_type_kwd=None, tag_kwd=None)])
        exists = _search(conn, table, kb_id, fields=["id"], condition={"kb_id": kb_id, "exists": "doc_type_kwd"})
        missing = _search(conn, table, kb_id, fields=["id"], condition={"kb_id": kb_id, "must_not": {"exists": "doc_type_kwd"}})
        contains = _search(conn, table, kb_id, fields=["id"], condition={"kb_id": kb_id, "tag_kwd": ["t1"]})
        exact = _search(conn, table, kb_id, fields=["id"], condition={"kb_id": kb_id, "doc_type_kwd": "active"})
        builder = conn._search_builder()
        assert builder.build_condition_where({"exists": "doc_type_kwd"}) == ("doc_type_kwd IS NOT NULL", [])
        assert builder.build_condition_where({"must_not": {"exists": "doc_type_kwd"}}) == ("doc_type_kwd IS NULL", [])
        assert builder.build_condition_where({"tag_kwd": ["t1"]}) == ("(tag_kwd @> %s::jsonb)", ['["t1"]'])
        assert builder.build_condition_where({"doc_type_kwd": "active"}) == ("doc_type_kwd = %s", ["active"])
        assert conn.get_doc_ids(exists) == ["active"]
        assert conn.get_doc_ids(missing) == ["empty"]
        assert conn.get_doc_ids(contains) == ["active"]
        assert conn.get_doc_ids(exact) == ["active"]
    finally:
        _delete_table(conn, table)


def test_tc_ret_603_local_jsonb_array_aggregation(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_603")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("c1", kb_id, "one", tag_kwd=["tag1", "tag2"]), _base_row("c2", kb_id, "two", tag_kwd=["tag1", "tag3"])])
        result = _search(conn, table, kb_id, fields=["id", "tag_kwd"])
        assert dict(conn.get_aggregation(result, "tag_kwd")) == {"tag1": 2, "tag2": 1, "tag3": 1}
    finally:
        _delete_table(conn, table)


def test_tc_ret_604_aggregation_value_count_rows_are_used(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_604")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("a", kb_id, "one", doc_type_kwd="active"), _base_row("b", kb_id, "two", doc_type_kwd="active"), _base_row("c", kb_id, "three", doc_type_kwd="inactive")])
        result = _search(conn, table, kb_id, fields=["id"], agg=["doc_type_kwd"])
        assert conn.get_aggregation(result, "doc_type_kwd") == [("active", 2), ("inactive", 1)]
        assert all(set(row) == {"value", "count"} for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_605_highlight_is_returned_for_fulltext_hit(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_605")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("hit", kb_id, "hello world", title_tks="hello")])
        result = conn.search(["id", "content_with_weight"], ["content_with_weight"], {"kb_id": kb_id}, [MatchTextExpr(["content_with_weight"], "hello", 10)], None, 0, 10, table, [kb_id])
        highlights = conn.get_highlight(result, ["hello"], "content_with_weight")
        assert conn.get_doc_ids(result) == ["hit"]
        assert highlights["hit"]
        assert "hello" in highlights["hit"].lower()
        filter_result = _search(conn, table, kb_id, fields=["id"])
        assert conn.get_highlight(filter_result, ["hello"], "content_with_weight") == {}
    finally:
        _delete_table(conn, table)


def test_tc_ret_601_single_field_aggregation_returns_value_count_rows(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_601")
    try:
        _create_chunks(
            conn,
            table,
            kb_id,
            [
                _base_row("active-1", kb_id, "one", doc_type_kwd="active"),
                _base_row("active-2", kb_id, "two", doc_type_kwd="active"),
                _base_row("inactive", kb_id, "three", doc_type_kwd="inactive"),
            ],
        )
        result = _search(conn, table, kb_id, fields=["id"], agg=["doc_type_kwd"])
        assert result.total == 2
        assert conn.get_aggregation(result, "doc_type_kwd") == [("active", 2), ("inactive", 1)]
        assert all(set(row) == {"value", "count"} for row in result.chunks)
    finally:
        _delete_table(conn, table)


def test_tc_ret_701_pagerank_is_added_to_fulltext_score(gaussdb_env, table_name):
    from common import constants
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_701")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("plain", kb_id, "alpha", title_tks="alpha", pagerank_fea=0), _base_row("ranked", kb_id, "alpha", title_tks="alpha", pagerank_fea=100)])
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id"],
            condition={"kb_id": kb_id},
            keywords=["alpha"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
            pagerank_weight=1.0,
        )
        assert "COALESCE(pagerank_fea, 0)::DOUBLE PRECISION / 100.0 * %s" in search_sql
        assert 1.0 in params
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["title_tks"], "alpha", 10)], rank={constants.PAGERANK_FLD: 1})
        scores = conn.get_scores(result)
        assert conn.get_doc_ids(result) == ["ranked", "plain"]
        assert scores["ranked"] > scores["plain"]
    finally:
        _delete_table(conn, table)


def test_tc_ret_703_null_pagerank_is_zero(gaussdb_env, table_name):
    from common import constants
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_703")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("null", kb_id, "alpha", title_tks="alpha", pagerank_fea=None), _base_row("zero", kb_id, "alpha", title_tks="alpha", pagerank_fea=0)])
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["title_tks"], "alpha", 10)], rank={constants.PAGERANK_FLD: 1})
        scores = conn.get_scores(result)
        assert scores["null"] == scores["zero"]
    finally:
        _delete_table(conn, table)


def test_tc_ret_704_tag_features_are_not_a_gaussdb_rank_input(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_704")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("a", kb_id, "alpha", title_tks="alpha", tag_feas={"tag1": 100}), _base_row("b", kb_id, "alpha", title_tks="alpha", tag_feas={"tag1": 1})])
        search_sql, params = conn._search_builder().build_search_sql(
            table=table,
            select_fields=["id"],
            condition={"kb_id": kb_id},
            keywords=["alpha"],
            vector=None,
            vector_dim=None,
            vector_weight=0.0,
            offset=0,
            limit=10,
            pagerank_weight=0.0,
        )
        assert "tag_feas" not in search_sql
        assert {"tag1": 100} not in params
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["title_tks"], "alpha", 10)], rank={"tag_feas": {"tag1": 100}})
        scores = conn.get_scores(result)
        assert scores["a"] == scores["b"]
        assert conn.get_doc_ids(result) == ["a", "b"]
    finally:
        _delete_table(conn, table)


def test_tc_ret_901_fulltext_positive_negative_and_score(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_901")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("positive", kb_id, "audit contract", title_tks="contract"), _base_row("negative", kb_id, "budget", title_tks="budget")])
        result = _search(conn, table, kb_id, fields=["id", "_score"], matches=[MatchTextExpr(["title_tks"], "contract", 10)])
        assert conn.get_doc_ids(result) == ["positive"]
        assert conn.get_scores(result)["positive"] > 0
        assert result.total == 1
    finally:
        _delete_table(conn, table)


def test_tc_ret_902_vector_topk_positive_negative_and_valid(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_902")
    try:
        target = [1.0, *([0.0] * 1023)]
        if gaussdb_env["variant_evidence"].get("variant") == "distributed":
            mid = [0.8, 0.6, *([0.0] * 1022)]
            far = [0.6, 0.8, *([0.0] * 1022)]
            rows = [
                _base_row("near", kb_id, "near", q_1024_vec=target),
                _base_row("mid", kb_id, "mid", q_1024_vec=mid),
                _base_row("far", kb_id, "far", q_1024_vec=far),
                _base_row("placeholder", kb_id, "placeholder"),
            ]
            for i in range(3, 200):
                noise = [0.0] * 1024
                noise[i + 1] = 1.0
                rows.append(_base_row(f"noise-{i}", kb_id, "noise", q_1024_vec=noise))
            _create_chunks(conn, table, kb_id, rows, dim=1024)
            result = _search(
                conn,
                table,
                kb_id,
                fields=["id", "q_1024_vec", "_score"],
                matches=[MatchDenseExpr("q_1024_vec", target, "float", "cosine", 3, {"similarity": 0.5})],
                limit=3,
            )
            assert conn.get_doc_ids(result) == ["near", "mid", "far"]
            assert all(chunk["q_1024_vec_valid"] is True for chunk in result.chunks)
            assert [chunk["_score"] for chunk in result.chunks] == pytest.approx([1.0, 0.8, 0.6], abs=1e-6)
            with gaussdb_admin_conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT id, q_1024_vec <+> %s::floatvector(1024) AS distance FROM {} "
                        "WHERE kb_id = %s AND id IN ('near', 'mid', 'far') ORDER BY distance, id"
                    ).format(sql.Identifier(gaussdb_env["schema"], table)),
                    [json.dumps(target, separators=(",", ":")), kb_id],
                )
                distance_rows = cur.fetchall()
            assert [row[0] for row in distance_rows] == ["near", "mid", "far"]
            assert [float(row[1]) for row in distance_rows] == pytest.approx([0.0, 0.2, 0.4], abs=1e-6)
            plan = _explain_search(conn, gaussdb_admin_conn, table, kb_id, vector=target, vector_dim=1024, vector_weight=1.0)
            assert conn.ddl.index_name(table, "q_1024_vec_diskann").lower() in plan.lower()
            return

        rows = [_base_row("positive", kb_id, "near", q_1024_vec=target)]
        for i in range(199):
            noise = [0.0] * 1024
            noise[i + 1] = 1.0
            rows.append(_base_row(f"negative-{i}", kb_id, "far", q_1024_vec=noise))
        _create_chunks(conn, table, kb_id, rows, dim=1024)
        result = _search(conn, table, kb_id, fields=["id", "q_1024_vec", "_score"], matches=[MatchDenseExpr("q_1024_vec", target, "float", "cosine", 1, {"similarity": 0.9})], limit=1)
        assert conn.get_doc_ids(result) == ["positive"]
        assert result.chunks[0]["q_1024_vec_valid"] is True
        assert result.chunks[0]["_score"] >= 0.9
        plan = _explain_search(conn, gaussdb_admin_conn, table, kb_id, vector=target, vector_dim=1024, vector_weight=1.0)
        assert conn.ddl.index_name(table, "q_1024_vec_diskann").lower() in plan.lower()
    finally:
        _delete_table(conn, table)


def test_tc_ret_903_hybrid_weight_changes_final_score_ordering(gaussdb_env, table_name):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = _new_kb()
    table = table_name(gaussdb_env, "ret_903")
    try:
        _create_chunks(conn, table, kb_id, [_base_row("text", kb_id, "alpha", title_tks="alpha", q_4_vec=[0.0, 1.0, 0.0, 0.0]), _base_row("vector", kb_id, "beta", q_4_vec=[1.0, 0.0, 0.0, 0.0])])
        text_heavy = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.3))
        balanced = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.5))
        vector_heavy = _search(conn, table, kb_id, fields=["id", "_score"], matches=_hybrid_matches(0.9))
        assert conn.get_doc_ids(text_heavy)[0] == "text"
        assert conn.get_scores(balanced)["text"] == conn.get_scores(balanced)["vector"]
        assert conn.get_doc_ids(balanced) == ["text", "vector"]
        assert conn.get_doc_ids(vector_heavy)[0] == "vector"
        assert conn.get_scores(text_heavy) != conn.get_scores(vector_heavy)
    finally:
        _delete_table(conn, table)


def test_tc_ret_904_metadata_combination_returns_only_matching_document(gaussdb_env):
    from common.doc_store.doc_store_base import MatchTextExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id, meta, table = _metadata_seed(conn, gaussdb_env, "ret_904")
    try:
        doc_ids, result = _metadata_retrieve(conn, meta, table, kb_id)
        text_result = _search(conn, table, kb_id, fields=["id", "doc_id", "_score"], condition={"kb_id": kb_id, "doc_id": doc_ids}, matches=[MatchTextExpr(["content_ltks"], "hello", 10)])
        assert doc_ids == ["d1"]
        assert conn.get_doc_ids(result) == ["c1"]
        assert conn.get_doc_ids(text_result) == ["c1"]
        assert text_result.chunks[0]["doc_id"] == "d1"
        assert text_result.chunks[0]["_score"] > 0
    finally:
        _delete_table(conn, meta)
        _delete_table(conn, table)


@pytest.mark.gaussdb_variant_specific
def test_tc_ret_905_declared_vector_dimensions_have_real_search_support(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from common.doc_store.doc_store_base import MatchDenseExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    variant = os.getenv("GAUSSDB_VARIANT")
    expected = os.getenv("GAUSSDB_EXPECTED_VECTOR_DIMS")
    if variant not in {"centralized", "distributed"}:
        pytest.fail("GAUSSDB_VARIANT must be centralized or distributed")
    if not expected:
        pytest.fail("GAUSSDB_EXPECTED_VECTOR_DIMS is required for TC-RET-905")
    dims = [int(item.strip()) for item in expected.split(",") if item.strip()]
    assert dims
    conn = GaussDBConnection()
    for dim in dims:
        kb_id = _new_kb()
        table = table_name(gaussdb_env, f"ret_905_{dim}")
        try:
            target = [1.0, *([0.0] * (dim - 1))]
            mid = [0.8, 0.6, *([0.0] * (dim - 2))]
            far = [0.6, 0.8, *([0.0] * (dim - 2))]
            rows = [
                _base_row("near", kb_id, f"near-{dim}", **{f"q_{dim}_vec": target}),
                _base_row("mid", kb_id, f"mid-{dim}", **{f"q_{dim}_vec": mid}),
                _base_row("far", kb_id, f"far-{dim}", **{f"q_{dim}_vec": far}),
            ]
            for i in range(197):
                noise = [0.0] * dim
                noise[(i % (dim - 1)) + 1] = 1.0
                rows.append(_base_row(f"noise-{i}", kb_id, f"noise-{dim}", **{f"q_{dim}_vec": noise}))
            _create_chunks(conn, table, kb_id, rows, dim=dim)
            result = _search(
                conn,
                table,
                kb_id,
                fields=["id", f"q_{dim}_vec", "_score"],
                matches=[MatchDenseExpr(f"q_{dim}_vec", target, "float", "cosine", 3, {"similarity": 0.5})],
                limit=3,
            )
            assert result.total == 3
            assert conn.get_doc_ids(result) == ["near", "mid", "far"]
            assert [chunk["_score"] for chunk in result.chunks] == pytest.approx([1.0, 0.8, 0.6], abs=1e-6)
            assert all(chunk[f"q_{dim}_vec_valid"] is True for chunk in result.chunks)
            with gaussdb_admin_conn.cursor() as cur:
                cur.execute(
                    sql.SQL(
                        "SELECT id, {} <+> %s::floatvector({}) AS distance FROM {} "
                        "WHERE kb_id = %s AND id IN ('near', 'mid', 'far') ORDER BY distance, id"
                    ).format(
                        sql.Identifier(f"q_{dim}_vec"),
                        sql.SQL(str(dim)),
                        sql.Identifier(gaussdb_env["schema"], table),
                    ),
                    [json.dumps(target, separators=(",", ":")), kb_id],
                )
                distance_rows = cur.fetchall()
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
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                    [gaussdb_env["schema"], table],
                )
                index_defs = [str(row[0]).lower() for row in cur.fetchall()]
                cur.execute(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                      FROM pg_attribute a
                      JOIN pg_class c ON c.oid = a.attrelid
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = %s AND c.relname = %s
                       AND a.attname = %s AND a.attnum > 0 AND NOT a.attisdropped
                    """,
                    [gaussdb_env["schema"], table, f"q_{dim}_vec"],
                )
                vector_type = cur.fetchone()[0]
            assert [row[0] for row in distance_rows] == ["near", "mid", "far"]
            assert [float(row[1]) for row in distance_rows] == pytest.approx([0.0, 0.2, 0.4], abs=1e-6)
            assert [chunk["_score"] for chunk in result.chunks] == pytest.approx(
                [1.0 - float(row[1]) for row in distance_rows],
                abs=1e-6,
            )
            assert "storage_type=ustore" in str(reloptions).lower()
            assert vector_type.lower() == f"floatvector({dim})"
            assert sum(" using ugin " in definition for definition in index_defs) == 2
            assert sum(" using gsdiskann " in definition for definition in index_defs) == 1
            search_sql, params = conn._search_builder().build_search_sql(
                table=table,
                select_fields=["id"],
                condition={"kb_id": kb_id},
                keywords=None,
                vector=target,
                vector_dim=dim,
                vector_weight=1.0,
                offset=0,
                limit=3,
            )
            assert f"q_{dim}_vec <+> %s::floatvector({dim})" in search_sql
            expected_vector_param = json.dumps(target, separators=(",", ":"))
            assert params[0] == expected_vector_param
            assert params[1] == expected_vector_param
            assert params[-5] == expected_vector_param
            assert sum(value == expected_vector_param for value in params) == 3
            assert params[-4:] == [3, 0.0, 3, 0]
            plan = _explain_search(conn, gaussdb_admin_conn, table, kb_id, vector=target, vector_dim=dim, vector_weight=1.0)
            diskann_index = conn.ddl.index_name(table, f"q_{dim}_vec_diskann")
            assert diskann_index.lower() in plan.lower(), f"variant={variant} dim={dim} expected_supported=True"
        finally:
            _delete_table(conn, table)


mark_gaussdb_both_by_default(globals())
