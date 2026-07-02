#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
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

from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchTextExpr, OrderByExpr
from common.doc_store.gaussdb_conn_base import GaussDBConnectionBase, GaussDBDDLBuilder, UnsafeGaussDBSQL
from common.doc_store.gaussdb_conn_pool import GaussDBError
from rag.utils.gaussdb_conn import (
    GaussDBConnection,
    SearchResult,
    build_doc_meta_order_by,
    close_cursor,
    decode_column_value,
    nested_numeric_value,
    normalize_column_value,
    normalize_kb_id,
    normalize_kb_ids,
    normalize_table_names,
    parse_fusion_vector_weight,
    parse_json_dict,
    parse_vector_literal,
    select_doc_meta_columns,
    sortable_search_value,
    validate_filter_column,
    vector_literal,
    zero_vector_literal,
)


class RecordingCursor:
    def __init__(self):
        self.executed = []
        self.rowcount = 0
        self.description = None
        self.rows = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params or []))

    def executemany(self, sql, params):
        materialized = [list(row) for row in params]
        self.executed.append((sql, materialized))
        self.rowcount = len(materialized)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed = True


class SequencedCursor(RecordingCursor):
    def __init__(self, results):
        super().__init__()
        self.results = list(results)

    def execute(self, sql, params=None):
        super().execute(sql, params)
        rows, description = self.results.pop(0)
        self.rows = rows
        self.description = description


class RecordingConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class RecordingPool:
    def __init__(self, cursor):
        self.cursor = cursor
        self.conn = RecordingConnection(cursor)
        self.put_back = []

    def get_conn(self):
        return self.conn

    def put_conn(self, conn):
        self.put_back.append(conn)


def make_conn(cursor, vector_dimensions=None):
    conn = GaussDBConnection.__new__(GaussDBConnection)
    conn.schema = "public"
    conn.resolved_schema = "public"
    conn.pool = RecordingPool(cursor)
    conn.ddl = GaussDBDDLBuilder(schema="public")
    conn.logger = type("Logger", (), {"debug": lambda *_args, **_kwargs: None, "error": lambda *_args, **_kwargs: None})()
    if vector_dimensions is not None:
        conn.get_vector_dimensions = lambda _table: vector_dimensions
    return conn


def test_sql_executes_scoped_docengine_select_with_runtime_guard():
    cursor = RecordingCursor()
    cursor.description = [("doc_id",), ("amount",)]
    cursor.rows = [("doc1", "120")]
    conn = make_conn(cursor)

    result = conn.sql(
        "SELECT doc_id, chunk_data #>> '{amount}' AS amount FROM ragflow_tenant WHERE kb_id = 'kb1'",
        fetch_size=20,
    )

    sql, params = cursor.executed[-1]
    assert cursor.executed[0][0] == "SET LOCAL statement_timeout = 30000"
    assert "FROM ragflow_tenant" in sql
    assert "kb_id = 'kb1'" in sql
    assert "LIMIT 20" in sql
    assert params == []
    assert result == {
        "columns": [{"name": "doc_id", "type": "text"}, {"name": "amount", "type": "text"}],
        "rows": [["doc1", "120"]],
    }


def test_db_type_reports_gaussdb():
    assert make_conn(RecordingCursor()).db_type() == "gaussdb"


def test_sql_returns_connection_after_query_failure():
    class FailingCursor(RecordingCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if not str(sql).startswith("SET LOCAL"):
                raise RuntimeError("query timeout")

    cursor = FailingCursor()
    conn = make_conn(cursor)

    with pytest.raises(RuntimeError, match="query timeout"):
        conn.sql("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1'")

    assert cursor.executed[0][0] == "SET LOCAL statement_timeout = 30000"
    assert cursor.closed is True
    assert conn.pool.put_back


def test_parse_match_expressions_keeps_gaussdb_search_params_out_of_rank_feature():
    conn = make_conn(RecordingCursor())

    parsed = conn._parse_match_expressions(
        [
            MatchTextExpr(["content_with_weight"], "risk audit", 20),
            MatchDenseExpr("q_4_vec", [0.1, 0.2, 0.3, 0.4], "float", "cosine", 20, {"similarity": 0.1}),
            FusionExpr("weighted_sum", 20, {"weights": "0.5,0.5"}),
        ],
        {"pagerank_fea": 7, "vector_similarity_weight": 0.99, "similarity_threshold": 0.99},
        {"vector_similarity_weight": 0.7, "similarity_threshold": 0.2},
    )

    assert parsed["pagerank_weight"] == 7
    assert parsed["vector_weight"] == 0.7
    assert parsed["similarity_threshold"] == 0.2


def test_parse_match_expressions_covers_text_only_vector_only_and_unknown_expr():
    conn = make_conn(RecordingCursor())

    text_only = conn._parse_match_expressions([MatchTextExpr(["content_with_weight"], "risk audit", 8)], None, None)
    vector_only = conn._parse_match_expressions(
        [MatchDenseExpr("q_4_vec", [0.1, 0.2, 0.3, 0.4], "float", "cosine", 6, {})],
        None,
        {},
    )
    unrelated_params = conn._parse_match_expressions(
        [MatchDenseExpr("q_4_vec", [0.1, 0.2, 0.3, 0.4], "float", "cosine", 6, {})],
        None,
        {"unused": "kept"},
    )
    unknown_only = conn._parse_match_expressions([object()], None, None)

    assert text_only["keywords"] == ["risk", "audit"]
    assert text_only["vector_weight"] == 0.0
    assert vector_only["vector_weight"] == 1.0
    assert vector_only["similarity_threshold"] == 0.0
    assert unrelated_params["vector_weight"] == 1.0
    assert unknown_only["topn"] is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT doc_id FROM pg_class WHERE kb_id = 'kb1'",
        "SELECT doc_id FROM public.ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT doc_id FROM ragflow_tenant",
        "SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' OR 1 = 1",
        "SELECT content_with_weight FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT chunk_data ->> 'amount' FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT current_database() FROM ragflow_tenant WHERE kb_id = 'kb1'",
    ],
)
def test_sql_runtime_guard_rejects_unscoped_or_unsafe_sql(sql):
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    with pytest.raises(UnsafeGaussDBSQL):
        conn.sql(sql)

    assert cursor.executed == []


def test_insert_chunk_uses_parameterized_upsert_and_valid_vector_flag():
    cursor = RecordingCursor()
    conn = make_conn(cursor, vector_dimensions=[4])

    errors = conn.insert(
        [{"id": "c1", "kb_id": "kb1", "doc_id": "d1", "content_with_weight": "hello", "q_4_vec": [0.1, 0.2, 0.3, 0.4]}],
        "ragflow_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert errors == []
    assert 'INSERT INTO "public"."ragflow_tenant"' in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "%s::floatvector(4)" in sql
    assert params[0][0] == "c1"
    assert "[0.1,0.2,0.3,0.4]" in params[0]
    assert any(value is True for value in params[0])
    assert conn.pool.conn.commits == 1


def test_insert_missing_vector_writes_invalid_placeholder_vector():
    cursor = RecordingCursor()
    conn = make_conn(cursor, vector_dimensions=[4])

    errors = conn.insert(
        [{"id": "c1", "kb_id": "kb1", "doc_id": "d1", "content_with_weight": "hello"}],
        "ragflow_tenant",
        "kb1",
    )

    _sql, params = cursor.executed[-1]
    assert errors == []
    assert "[0,0,0,0]" in params[0]
    assert any(value is False for value in params[0])


def test_insert_missing_vector_rejects_when_existing_dimension_is_unknown():
    cursor = RecordingCursor()
    conn = make_conn(cursor, vector_dimensions=[])

    errors = conn.insert(
        [{"id": "c1", "kb_id": "kb1", "doc_id": "d1", "content_with_weight": "hello"}],
        "ragflow_tenant",
        "kb1",
    )

    assert errors == ["c1"]
    assert cursor.executed == []


def test_insert_missing_vector_infers_dimension_from_same_batch_real_vector():
    cursor = RecordingCursor()
    conn = make_conn(cursor, vector_dimensions=[])

    errors = conn.insert(
        [
            {"id": "c1", "kb_id": "kb1", "doc_id": "d1", "q_4_vec": [0.1, 0.2, 0.3, 0.4]},
            {"id": "c2", "kb_id": "kb1", "doc_id": "d2"},
        ],
        "ragflow_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert errors == []
    assert "q_4_vec" in sql
    assert "[0,0,0,0]" in params[1]
    assert any(value is False for value in params[1])


def test_insert_serializes_jsonb_unknown_fields_to_extra_and_derives_metadata_fields():
    cursor = RecordingCursor()
    conn = make_conn(cursor, vector_dimensions=[4])

    errors = conn.insert(
        [
            {
                "id": "c1",
                "kb_id": "kb1",
                "doc_id": "d1",
                "content_with_weight": "hello",
                "metadata": {"_group_id": "g1", "_title": "Doc One"},
                "unknown_key": "kept",
            }
        ],
        "ragflow_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert errors == []
    assert "%s::jsonb" in sql
    row = dict(zip(extract_insert_columns(sql), params[0]))
    assert row["group_id"] == "g1"
    assert row["docnm_kwd"] == "Doc One"
    assert json.loads(row["extra"]) == {"unknown_key": "kept"}
    assert json.loads(row["metadata"]) == {"_group_id": "g1", "_title": "Doc One"}


def test_normalize_chunk_row_uses_group_id_metadata_without_title():
    conn = make_conn(RecordingCursor(), vector_dimensions=[4])

    row = conn._normalize_chunk_row(
        "ragflow_tenant",
        {"id": "c1", "kb_id": "kb1", "doc_id": "d1", "metadata": {"_group_id": "g1"}},
        "kb1",
        [],
    )

    assert row["group_id"] == "g1"
    assert "docnm_kwd" not in row


def test_insert_doc_meta_uses_parameterized_upsert():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    errors = conn.insert(
        [{"id": "doc1", "kb_id": "kb1", "meta_fields": {"author": "Alice"}}],
        "ragflow_doc_meta_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert errors == []
    assert 'INSERT INTO "public"."ragflow_doc_meta_tenant"' in sql
    assert "(id, kb_id, meta_fields)" in sql
    assert "%s::jsonb" in sql
    assert "kb_id = VALUES(kb_id)" not in sql
    assert "meta_fields = VALUES(meta_fields)" in sql
    assert params == [["doc1", "kb1", '{"author": "Alice"}']]


def test_insert_empty_input_returns_empty_without_database_call():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    assert conn.insert([], "ragflow_tenant", "kb1") == []
    assert cursor.executed == []


def test_insert_returns_ids_when_write_fails_after_normalization():
    class FailingManyCursor(RecordingCursor):
        def executemany(self, sql, params):
            super().executemany(sql, params)
            raise RuntimeError("bulk write failed")

    cursor = FailingManyCursor()
    conn = make_conn(cursor, vector_dimensions=[4])

    errors = conn.insert(
        [{"id": "c1", "kb_id": "kb1", "doc_id": "d1", "q_4_vec": [0.1, 0.2, 0.3, 0.4]}],
        "ragflow_tenant",
        "kb1",
    )

    assert errors == ["c1"]
    assert conn.pool.conn.rollbacks == 1


def test_insert_doc_meta_rejects_missing_or_mismatched_scope():
    conn = make_conn(RecordingCursor())

    assert conn.insert([{"id": "doc1", "meta_fields": {}}], "ragflow_doc_meta_tenant", None) == ["doc1"]
    assert conn.insert([{"id": "doc1", "kb_id": "kb2", "meta_fields": {}}], "ragflow_doc_meta_tenant", "kb1") == ["doc1"]


def test_delete_chunk_scopes_by_id_and_kb_id():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    deleted = conn.delete({"id": "c1"}, "ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert deleted == 1
    assert 'DELETE FROM "public"."ragflow_tenant"' in sql
    assert "WHERE id = %s" in sql
    assert "AND kb_id = %s" in sql
    assert params == ["c1", "kb1"]


def test_delete_doc_meta_scopes_by_id_and_kb_id():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    deleted = conn.delete({"id": "doc1"}, "ragflow_doc_meta_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert deleted == 1
    assert 'DELETE FROM "public"."ragflow_doc_meta_tenant"' in sql
    assert params == ["doc1", "kb1"]


def test_delete_rejects_unscoped_condition():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    deleted = conn.delete({}, "ragflow_tenant", "kb1")

    assert deleted == 0
    assert cursor.executed == []


def test_delete_returns_zero_when_builder_produces_empty_where():
    cursor = RecordingCursor()
    conn = make_conn(cursor)
    conn._build_where_clause = lambda *_args, **_kwargs: ("", [])

    deleted = conn.delete({"id": "c1"}, "ragflow_tenant", "kb1")

    assert deleted == 0
    assert cursor.executed == []


def test_delete_without_kb_scope_returns_zero_without_database_call():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    assert conn.delete({"id": "c1"}, "ragflow_tenant", None) == 0
    assert cursor.executed == []


def test_delete_allows_kb_scope_from_condition_without_dataset_id():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    deleted = conn.delete({"id": "c1", "kb_id": "kb1"}, "ragflow_tenant", None)

    sql, params = cursor.executed[-1]
    assert deleted == 1
    assert "kb_id = %s" in sql
    assert params == ["c1", "kb1"]


def test_repeated_delete_of_missing_chunk_returns_zero():
    cursor = RecordingCursor()
    cursor.rowcount = 0
    conn = make_conn(cursor)

    deleted = conn.delete({"id": "c1"}, "ragflow_tenant", "kb1")

    assert deleted == 0


def test_update_builds_parameterized_set_clause_and_scopes_kb_id():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    updated = conn.update({"id": "c1"}, {"pagerank_fea": 9, "important_kwd": ["risk"]}, "ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert updated is True
    assert "SET pagerank_fea = %s" in sql
    assert "%s::jsonb" in sql
    assert "WHERE id = %s" in sql
    assert "AND kb_id = %s" in sql
    assert params[-2:] == ["c1", "kb1"]


def test_update_without_kb_scope_returns_false_without_database_call():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    updated = conn.update({"id": "c1"}, {"pagerank_fea": 9}, "ragflow_tenant", None)

    assert updated is False
    assert cursor.executed == []


@pytest.mark.parametrize("key_column", ["id", "kb_id"])
def test_update_rejects_key_columns_without_database_call(key_column):
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    updated = conn.update({"id": "c1"}, {key_column: "new-value"}, "ragflow_tenant", "kb1")

    assert updated is False
    assert cursor.executed == []


@pytest.mark.parametrize("key_column", ["id", "kb_id"])
def test_update_rejects_removing_key_columns_without_database_call(key_column):
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    updated = conn.update({"id": "c1"}, {"remove": key_column}, "ragflow_tenant", "kb1")

    assert updated is False
    assert cursor.executed == []


def test_update_supports_jsonb_add_and_remove_operations():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    updated = conn.update({"id": "c1"}, {"add": {"tag_kwd": "risk"}, "remove": {"source_id": "old"}}, "ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert updated is True
    assert "tag_kwd = jsonb_insert" in sql
    assert "source_id = source_id - %s" in sql
    assert params[:2] == ['"risk"', "old"]


def test_delete_jsonb_multi_value_condition_uses_contains_predicate():
    cursor = RecordingCursor()
    cursor.rowcount = 2
    conn = make_conn(cursor)

    deleted = conn.delete({"tag_kwd": "risk"}, "ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert deleted == 2
    assert "tag_kwd @> %s::jsonb" in sql
    assert params == ['["risk"]', "kb1"]


def test_delete_jsonb_multi_value_list_condition_uses_contains_or_predicates():
    cursor = RecordingCursor()
    cursor.rowcount = 2
    conn = make_conn(cursor)

    deleted = conn.delete({"source_id": ["doc1", "doc2"]}, "ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert deleted == 2
    assert "(source_id @> %s::jsonb OR source_id @> %s::jsonb)" in sql
    assert params == ['["doc1"]', '["doc2"]', "kb1"]


def test_get_scopes_by_id_and_kb_ids_and_decodes_jsonb():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("position_int",), ("q_4_vec",), ("q_4_vec_valid",)]
    cursor.rows = [("c1", "kb1", "[[1, 2, 3, 4]]", "[0,0,0,0]", False)]
    conn = make_conn(cursor)

    row = conn.get("c1", "ragflow_tenant", ["kb1", "kb2"])

    sql, params = cursor.executed[-1]
    assert "WHERE id = %s" in sql
    assert "kb_id IN (%s, %s)" in sql
    assert params == ["c1", "kb1", "kb2"]
    assert row == {"id": "c1", "kb_id": "kb1", "position_int": [[1, 2, 3, 4]], "q_4_vec_valid": False}


def test_get_doc_meta_allows_empty_kb_scope_and_decodes_jsonb():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("meta_fields",)]
    cursor.rows = [("doc1", "kb1", '{"author": "Alice"}')]
    conn = make_conn(cursor)

    row = conn.get("doc1", "ragflow_doc_meta_tenant", [""])

    sql, params = cursor.executed[-1]
    assert "WHERE id = %s" in sql
    assert "kb_id IN" not in sql
    assert params == ["doc1"]
    assert row == {"id": "doc1", "kb_id": "kb1", "meta_fields": {"author": "Alice"}}


def test_get_returns_none_for_empty_id_missing_kb_or_missing_row():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    assert conn.get("", "ragflow_tenant", ["kb1"]) is None
    assert conn.get("c1", "ragflow_tenant", []) is None
    assert cursor.executed == []

    cursor = SequencedCursor([
        ([(1,)], [("?column?",)]),
        ([], [("id",), ("kb_id",)]),
    ])
    conn = make_conn(cursor)
    assert conn.get("c1", "ragflow_tenant", ["kb1"]) is None
    assert len(cursor.executed) == 2


def test_get_returns_none_when_table_does_not_exist():
    cursor = SequencedCursor([
        ([], None),
    ])
    conn = make_conn(cursor)

    assert conn.get("c1", "ragflow_tenant", ["kb1"]) is None
    assert len(cursor.executed) == 1
    assert "information_schema.tables" in cursor.executed[0][0]


def test_search_doc_meta_table_returns_search_result():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("meta_fields",), ("__total",)]
    cursor.rows = [("doc1", "kb1", '{"author": "Alice"}', 1001)]
    conn = make_conn(cursor)

    result = conn.search(
        select_fields=["*"],
        highlight_fields=[],
        condition={"id": "doc1"},
        match_expressions=[],
        order_by=OrderByExpr(),
        offset=5,
        limit=10,
        index_names="ragflow_doc_meta_tenant",
        knowledgebase_ids=["kb1", "kb2"],
    )

    sql, params = cursor.executed[-1]
    assert result == SearchResult(total=1001, chunks=[{"id": "doc1", "kb_id": "kb1", "meta_fields": {"author": "Alice"}}])
    assert 'SELECT id, kb_id, meta_fields, COUNT(*) OVER() AS __total FROM "public"."ragflow_doc_meta_tenant"' in sql
    assert "id = %s" in sql
    assert "kb_id IN (%s, %s)" in sql
    assert "LIMIT %s OFFSET %s" in sql
    assert params == ["doc1", "kb1", "kb2", 10, 5]


def test_search_doc_meta_empty_page_falls_back_to_count():
    cursor = RecordingCursor()
    conn = make_conn(cursor)
    conn._fetch_all_with_description = lambda _sql, _params: ([], [("id",), ("kb_id",), ("meta_fields",), ("__total",)])
    count_queries = []

    def fake_fetch_one(sql, params):
        count_queries.append((sql, params))
        return (1001,)

    conn._fetch_one = fake_fetch_one

    result = conn.search(
        select_fields=["*"],
        highlight_fields=[],
        condition={"id": "doc-missing"},
        match_expressions=[],
        order_by=OrderByExpr(),
        offset=2000,
        limit=1000,
        index_names="ragflow_doc_meta_tenant",
        knowledgebase_ids=["kb1"],
    )

    sql, params = count_queries[0]
    assert result == SearchResult(total=1001, chunks=[])
    assert 'SELECT COUNT(*) FROM "public"."ragflow_doc_meta_tenant"' in sql
    assert "id = %s" in sql
    assert "kb_id IN (%s)" in sql
    assert params == ["doc-missing", "kb1"]


def test_fetch_metadata_doc_ids_builds_scoped_jsonb_query():
    cursor = RecordingCursor()
    cursor.rows = [("doc2",), ("doc1",)]
    conn = make_conn(cursor)

    doc_ids = conn.fetch_metadata_doc_ids(
        "ragflow_doc_meta_tenant",
        ["kb1", "kb2"],
        "lower(meta_fields #>> '{author}') = %s",
        ["alice"],
        25,
    )

    sql, params = cursor.executed[-1]
    assert doc_ids == ["doc2", "doc1"]
    assert 'SELECT id FROM "public"."ragflow_doc_meta_tenant"' in sql
    assert "kb_id IN (%s, %s)" in sql
    assert "(lower(meta_fields #>> '{author}') = %s)" in sql
    assert "LIMIT %s" in sql
    assert params == ["kb1", "kb2", "alice", 25]


def test_get_vector_dimensions_reads_q_vector_columns():
    cursor = RecordingCursor()
    cursor.rows = [("q_768_vec",), ("q_4_vec",), ("q_768_vec_valid",)]
    conn = make_conn(cursor)

    assert conn.get_vector_dimensions("ragflow_tenant") == [4, 768]

    sql, params = cursor.executed[-1]
    assert "information_schema.columns" in sql
    assert params == ["public", "ragflow_tenant"]


def test_index_exist_checks_information_schema_tables():
    cursor = RecordingCursor()
    cursor.rows = [(1,)]
    conn = make_conn(cursor)

    assert conn.index_exist("ragflow_tenant") is True

    sql, params = cursor.executed[-1]
    assert "information_schema.tables" in sql
    assert params == ["public", "ragflow_tenant"]


def test_delete_idx_with_dataset_id_deletes_only_that_kb():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.delete_idx("ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert 'DELETE FROM "public"."ragflow_tenant" WHERE kb_id = %s' in sql
    assert params == ["kb1"]


def test_delete_idx_without_dataset_id_drops_table():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.delete_idx("ragflow_tenant", None)

    sql, params = cursor.executed[-1]
    assert sql == 'DROP TABLE IF EXISTS "public"."ragflow_tenant"'
    assert params == []


def test_create_doc_meta_idx_rejects_non_metadata_table():
    conn = make_conn(RecordingCursor())

    with pytest.raises(ValueError, match="document metadata"):
        conn.create_doc_meta_idx("ragflow_tenant")


def test_create_doc_meta_idx_executes_table_and_kb_index_ddls():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    assert conn.create_doc_meta_idx("ragflow_doc_meta_tenant") is True

    statements = [sql for sql, _params in cursor.executed]
    assert any('CREATE TABLE IF NOT EXISTS "public"."ragflow_doc_meta_tenant"' in sql for sql in statements)
    assert any("CREATE INDEX IF NOT EXISTS" in sql and "kb_id" in sql for sql in statements)


def test_create_idx_uses_schema_lock_before_ddl():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.create_idx("ragflow_tenant", "kb1", 4)

    statements = [sql for sql, _params in cursor.executed]
    assert statements[0] == "SELECT pg_advisory_xact_lock(hashtext(%s))"
    assert cursor.executed[0][1] == ["create_idx:public:ragflow_tenant"]
    work_mem_index = statements.index("SET LOCAL maintenance_work_mem = '1GB'")
    diskann_index = next(i for i, sql in enumerate(statements) if "USING gsdiskann" in sql)
    assert work_mem_index < diskann_index


def test_create_idx_sets_maintenance_work_mem_before_diskann():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.create_idx("ragflow_tenant", "kb1", 4)

    statements = [sql for sql, _params in cursor.executed]
    work_mem_index = statements.index("SET LOCAL maintenance_work_mem = '1GB'")
    diskann_index = next(i for i, sql in enumerate(statements) if "USING gsdiskann" in sql)
    assert work_mem_index < diskann_index


def test_get_total_and_doc_ids_read_search_result_chunks():
    result = SearchResult(total=2, chunks=[{"id": "c1"}, {"id": "c2"}])
    conn = GaussDBConnection.__new__(GaussDBConnection)

    assert conn.get_total(result) == 2
    assert conn.get_doc_ids(result) == ["c1", "c2"]


def test_get_fields_converts_search_result_chunks():
    result = SearchResult(total=1, chunks=[{"id": "c1", "position_int": [[1, 2, 3, 4]], "_score": 0.7}])
    conn = GaussDBConnection.__new__(GaussDBConnection)

    fields = conn.get_fields(result, ["position_int", "_score"])

    assert fields == {"c1": {"position_int": [[1, 2, 3, 4]], "_score": 0.7}}


def test_get_fields_rejects_cross_kb_duplicate_chunk_ids():
    result = SearchResult(
        total=2,
        chunks=[
            {"id": "c1", "kb_id": "kb1", "doc_id": "d1"},
            {"id": "c1", "kb_id": "kb2", "doc_id": "d2"},
        ],
    )
    conn = GaussDBConnection.__new__(GaussDBConnection)

    with pytest.raises(GaussDBError, match="cross-KB duplicate chunk id: c1"):
        conn.get_fields(result, ["doc_id"])


def test_get_fields_allows_same_kb_duplicate_chunk_ids():
    result = SearchResult(
        total=2,
        chunks=[
            {"id": "c1", "kb_id": "kb1", "doc_id": "old"},
            {"id": "c1", "kb_id": "kb1", "doc_id": "new"},
        ],
    )
    conn = GaussDBConnection.__new__(GaussDBConnection)

    assert conn.get_fields(result, ["doc_id"]) == {"c1": {"doc_id": "new"}}


def test_get_highlight_rejects_cross_kb_duplicate_chunk_ids():
    result = SearchResult(
        total=2,
        chunks=[
            {"id": "c1", "kb_id": "kb1", "_highlight": "old"},
            {"id": "c1", "kb_id": "kb2", "_highlight": "new"},
        ],
    )
    conn = GaussDBConnection.__new__(GaussDBConnection)

    with pytest.raises(GaussDBError, match="cross-KB duplicate chunk id: c1"):
        conn.get_highlight(result, [], "content_with_weight")


def test_get_scores_rejects_cross_kb_duplicate_chunk_ids():
    result = SearchResult(
        total=2,
        chunks=[
            {"id": "c1", "kb_id": "kb1", "_score": 0.1},
            {"id": "c1", "kb_id": "kb2", "_score": 0.9},
        ],
    )
    conn = GaussDBConnection.__new__(GaussDBConnection)

    with pytest.raises(GaussDBError, match="cross-KB duplicate chunk id: c1"):
        conn.get_scores(result)


def test_get_rejects_cross_kb_duplicate_chunk_ids():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("doc_id",)]
    cursor.rows = [("c1", "kb1", "d1"), ("c1", "kb2", "d2")]
    conn = make_conn(cursor)

    with pytest.raises(GaussDBError, match="cross-KB duplicate chunk id: c1"):
        conn.get("c1", "ragflow_tenant", ["kb1", "kb2"])


def test_search_hides_invalid_placeholder_vector_when_vector_field_is_requested():
    cursor = RecordingCursor()
    cursor.description = [
        ("id",),
        ("kb_id",),
        ("q_4_vec",),
        ("q_4_vec_valid",),
        ("_score",),
        ("__total",),
    ]
    cursor.rows = [("c1", "kb1", "[0,0,0,0]", False, 0.0, 1)]
    conn = make_conn(cursor)

    result = conn.search(["q_4_vec"], [], {}, [], OrderByExpr(), 0, 10, "ragflow_tenant", ["kb1"])

    sql, _params = cursor.executed[-1]
    assert "q_4_vec_valid" in sql
    assert conn.get_fields(result, ["q_4_vec"]) == {"c1": {}}


def test_search_empty_deep_page_falls_back_to_total_count():
    description = [("id",), ("kb_id",), ("_score",), ("__total",)]
    cursor = SequencedCursor(
        [
            ([], description),
            ([("c1", "kb1", 0.0, 15)], description),
        ]
    )
    conn = make_conn(cursor)

    result = conn.search(["id"], [], {}, [], OrderByExpr(), 20, 10, "ragflow_tenant", ["kb1"])

    assert result.total == 15
    assert result.chunks == []
    assert len(cursor.executed) == 2


def test_multi_table_search_applies_global_pagination_and_ordering():
    description = [("id",), ("kb_id",), ("_score",), ("__total",)]
    cursor = SequencedCursor(
        [
            ([("c1", "kb1", 0.9, 2), ("c3", "kb1", 0.3, 2)], description),
            ([("c2", "kb1", 0.8, 1)], description),
        ]
    )
    conn = make_conn(cursor)
    match = MatchTextExpr(["content_ltks"], "risk", 10, {"original_query": "risk"})

    result = conn.search(["id"], [], {}, [match], OrderByExpr(), 1, 1, ["ragflow_tenant_a", "ragflow_tenant_b"], ["kb1"])

    assert result.total == 3
    assert [chunk["id"] for chunk in result.chunks] == ["c2"]


def test_multi_table_aggregation_merges_duplicate_buckets():
    description = [("value",), ("count",)]
    cursor = SequencedCursor(
        [
            ([("doc-a", 2), ("doc-b", 1)], description),
            ([("doc-a", 3)], description),
        ]
    )
    conn = make_conn(cursor)

    result = conn.search([], [], {}, [], OrderByExpr(), 0, 0, ["ragflow_tenant_a", "ragflow_tenant_b"], ["kb1"], ["docnm_kwd"])

    assert result.chunks == [{"value": "doc-a", "count": 5}, {"value": "doc-b", "count": 1}]
    assert result.total == 2


def test_get_aggregation_counts_values_or_uses_aggregate_rows():
    conn = GaussDBConnection.__new__(GaussDBConnection)
    result = SearchResult(total=3, chunks=[{"tag_kwd": ["a", "b"]}, {"tag_kwd": ["a"]}, {"tag_kwd": "c"}])
    aggregate_result = SearchResult(total=2, chunks=[{"value": "a", "count": 2}])

    assert sorted(conn.get_aggregation(result, "tag_kwd")) == [("a", 2), ("b", 1), ("c", 1)]
    assert conn.get_aggregation(aggregate_result, "tag_kwd") == [("a", 2)]


def test_base_connection_health_metrics_and_contract_methods():
    class BasePool:
        masked_uri = "user@host:19995/postgres?schema=public"
        resolved_schema = "public"

        def __init__(self):
            self.checked = False
            self.rows = [("GaussDB 8",), ("A",), (1,), RuntimeError("timeout")]

        def check_schema_access(self):
            self.checked = True

        def fetch_one(self, _sql, _params=None):
            row = self.rows.pop(0)
            if isinstance(row, Exception):
                raise row
            return row

    pool = BasePool()
    base = GaussDBConnectionBase(pool=pool)

    assert pool.checked is True
    assert base.db_type() == "gaussdb"
    health = base.health()
    assert health["status"] == "healthy"
    assert health["version_comment"] == "GaussDB 8"
    assert health["sql_compatibility"] == "A"
    assert base.get_performance_metrics()["connection"] == "connected"
    assert base.get_performance_metrics()["connection"] == "disconnected"

    contract_calls = [
        ("create_idx", ("idx", "kb1", 4)),
        ("delete_idx", ("idx", "kb1")),
        ("index_exist", ("idx", "kb1")),
        ("search", ([], [], {}, [], OrderByExpr(), 0, 10, "idx", ["kb1"])),
        ("get", ("id", "idx", ["kb1"])),
        ("insert", ([], "idx")),
        ("update", ({}, {}, "idx", "kb1")),
        ("delete", ({}, "idx", "kb1")),
        ("get_total", (SearchResult(total=0, chunks=[]),)),
        ("get_doc_ids", (SearchResult(total=0, chunks=[]),)),
        ("get_fields", (SearchResult(total=0, chunks=[]), [])),
        ("get_highlight", (SearchResult(total=0, chunks=[]), [], "content")),
        ("get_aggregation", (SearchResult(total=0, chunks=[]), "tag_kwd")),
        ("sql", ("SELECT 1", 1, "json")),
    ]
    for method, args in contract_calls:
        with pytest.raises(NotImplementedError):
            getattr(base, method)(*args)


def test_base_connection_health_reports_healthy_for_a_compatible_gaussdb():
    class BasePool:
        masked_uri = "user@host:19995/postgres?schema=public"
        resolved_schema = "public"

        def __init__(self):
            self.rows = [("GaussDB 8",), ("A",)]

        def check_schema_access(self):
            pass

        def fetch_one(self, _sql, _params=None):
            return self.rows.pop(0)

    health = GaussDBConnectionBase(pool=BasePool()).health()

    assert health["status"] == "healthy"
    assert health["version_comment"] == "GaussDB 8"
    assert health["sql_compatibility"] == "A"


def test_base_connection_health_reports_healthy_for_ora_compatible_gaussdb():
    class BasePool:
        masked_uri = "user@host:19995/postgres?schema=public"
        resolved_schema = "public"

        def __init__(self):
            self.rows = [("GaussDB 8",), ("ORA",)]

        def check_schema_access(self):
            pass

        def fetch_one(self, _sql, _params=None):
            return self.rows.pop(0)

    health = GaussDBConnectionBase(pool=BasePool()).health()

    assert health["status"] == "healthy"
    assert health["sql_compatibility"] == "ORA"


def test_base_connection_health_reports_unhealthy_for_unsupported_compatibility():
    class BasePool:
        masked_uri = "user@host:19995/postgres?schema=public"
        resolved_schema = "public"

        def __init__(self):
            self.rows = [("GaussDB 8",), ("PG",)]

        def check_schema_access(self):
            pass

        def fetch_one(self, _sql, _params=None):
            return self.rows.pop(0)

    health = GaussDBConnectionBase(pool=BasePool()).health()

    assert health["status"] == "unhealthy"
    assert health["sql_compatibility"] == "PG"
    assert "A/ORA" in health["error"]


def test_base_connection_health_reports_unhealthy_when_version_is_missing():
    class BasePool:
        masked_uri = "user@host:19995/postgres?schema=public"
        resolved_schema = "public"

        def check_schema_access(self):
            pass

        def fetch_one(self, _sql, _params=None):
            return None

    health = GaussDBConnectionBase(pool=BasePool()).health()

    assert health["status"] == "unhealthy"
    assert health["version_comment"] == "unknown"
    assert "version" in health["error"]


def test_search_empty_index_names_returns_empty_without_database_call():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    result = conn.search(["id"], [], {}, [], OrderByExpr(), 0, 10, [], ["kb1"])

    assert result == SearchResult(total=0, chunks=[])
    assert cursor.executed == []


def test_search_rejects_mixed_doc_meta_and_chunk_tables_without_database_call():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    with pytest.raises(ValueError, match="document metadata"):
        conn.search(
            ["id"],
            [],
            {"kb_id": "kb1"},
            [],
            OrderByExpr(),
            0,
            10,
            ["ragflow_doc_meta_tenant", "ragflow_tenant"],
            ["kb1"],
        )

    assert cursor.executed == []


def test_search_requires_chunk_kb_boundary_without_database_call():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    with pytest.raises(ValueError, match="kb_id boundary"):
        conn.search(["id"], [], {}, [], OrderByExpr(), 0, 10, "ragflow_tenant", [])

    assert cursor.executed == []


def test_search_rejects_condition_kb_id_outside_authorized_kbs():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    with pytest.raises(ValueError, match="knowledgebase_ids"):
        conn.search(
            ["id"],
            [],
            {"kb_id": "kb-b"},
            [],
            OrderByExpr(),
            0,
            10,
            "ragflow_tenant",
            ["kb-a"],
        )

    assert cursor.executed == []


def test_search_uses_dataset_ids_fallback_and_doc_ids_alias():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("doc_id",), ("__total",)]
    cursor.rows = [("c1", "kb1", "d1", 1)]
    conn = make_conn(cursor)

    result = conn.search(
        ["id", "doc_id"],
        [],
        {"doc_ids": ["d1", "d2"]},
        [],
        OrderByExpr(),
        0,
        10,
        "ragflow_tenant",
        None,
        dataset_ids=["kb1"],
    )

    sql, params = cursor.executed[-1]
    assert result.total == 1
    assert result.chunks[0]["doc_id"] == "d1"
    assert "kb_id IN (%s)" in sql
    assert "doc_id IN (%s, %s)" in sql
    assert params[-5:] == ["kb1", "d1", "d2", 10, 0]


def test_search_rejects_multiple_aggregation_fields_without_partial_query():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    with pytest.raises(ValueError, match="one aggregation field"):
        conn.search(
            [],
            [],
            {},
            [],
            OrderByExpr(),
            0,
            10,
            "ragflow_tenant",
            ["kb1"],
            ["docnm_kwd", "tag_kwd"],
        )

    assert cursor.executed == []


def test_parse_match_expressions_rejects_non_float_vector():
    conn = make_conn(RecordingCursor())

    with pytest.raises(ValueError, match="vector data type"):
        conn._parse_match_expressions(
            [MatchDenseExpr("q_4_vec", [1, 2, 3, 4], "int8", "cosine", 10, {})],
            {},
            {},
        )


def test_multi_table_search_limit_zero_uses_default_collection_limit():
    conn = make_conn(RecordingCursor())
    calls = []

    def fake_search_chunk_table(**kwargs):
        calls.append((kwargs["table"], kwargs["limit"]))
        return SearchResult(total=0, chunks=[])

    conn._search_chunk_table = fake_search_chunk_table

    result = conn.search(["id"], [], {"kb_id": "kb1"}, [], OrderByExpr(), 0, 0, ["ragflow_a", "ragflow_b"], ["kb1"])

    assert result == SearchResult(total=0, chunks=[])
    assert calls == [("ragflow_a", 10000), ("ragflow_b", 10000)]


def test_update_and_delete_swallow_write_errors_after_rollback():
    class FailingCursor(RecordingCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            raise RuntimeError("write failed")

    update_cursor = FailingCursor()
    update_conn = make_conn(update_cursor)
    delete_cursor = FailingCursor()
    delete_conn = make_conn(delete_cursor)

    assert update_conn.update({"id": "c1"}, {"pagerank_fea": 1}, "ragflow_tenant", "kb1") is False
    assert delete_conn.delete({"id": "c1"}, "ragflow_tenant", "kb1") == 0
    assert update_conn.pool.conn.rollbacks == 1
    assert delete_conn.pool.conn.rollbacks == 1


def test_update_returns_false_for_empty_inputs_or_empty_set_clause():
    conn = make_conn(RecordingCursor())

    assert conn.update({}, {"pagerank_fea": 1}, "ragflow_tenant", "kb1") is False
    assert conn.update({"id": "c1"}, {}, "ragflow_tenant", "kb1") is False

    conn._build_set_clause = lambda *_args, **_kwargs: ("", [])
    assert conn.update({"id": "c1"}, {"pagerank_fea": 1}, "ragflow_tenant", "kb1") is False


def test_sql_markdown_serializes_bytes_and_json_values():
    cursor = RecordingCursor()
    cursor.description = [("doc_id",), ("payload",), ("raw",)]
    cursor.rows = [("doc1", {"amount": 120}, b"ok")]
    conn = make_conn(cursor)

    result = conn.sql("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1'", format="markdown")

    assert result["rows"] == [["doc1", '{"amount": 120}', "ok"]]
    assert "|doc_id|payload|raw|" in result["markdown"]
    assert "|doc1|{\"amount\": 120}|ok|" in result["markdown"]


def test_highlight_and_scores_helpers_ignore_empty_rows():
    conn = GaussDBConnection.__new__(GaussDBConnection)
    result = SearchResult(
        total=3,
        chunks=[
            {"id": "c1", "_highlight": "<em>risk</em>", "_score": 0.9},
            {"id": "c2", "highlight": "audit", "_score": None},
            {"highlight": "missing id", "_score": 1.0},
        ],
    )

    assert conn.get_highlight(result, ["risk"], "content_with_weight") == {"c1": "<em>risk</em>", "c2": "audit"}
    assert conn.get_scores(result) == {"c1": 0.9, "c2": 0.0}


def test_get_fields_skips_rows_without_id_and_empty_aggregation_values():
    conn = GaussDBConnection.__new__(GaussDBConnection)
    result = SearchResult(total=2, chunks=[{"position_int": [[1]]}, {"id": "c1", "tag_kwd": ["", "risk"], "empty": None}])

    assert conn.get_fields(result, ["position_int", "empty"]) == {"c1": {}}
    assert conn.get_aggregation(result, "tag_kwd") == [("risk", 1)]


def test_fetch_metadata_doc_ids_returns_empty_for_empty_scope_or_filter():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    assert conn.fetch_metadata_doc_ids("ragflow_doc_meta_tenant", [], "meta_fields ? 'a'", [], 10) == []
    assert conn.fetch_metadata_doc_ids("ragflow_doc_meta_tenant", ["kb1"], "", [], 10) == []
    assert cursor.executed == []


def test_fetch_metadata_doc_ids_accepts_dict_and_empty_limit_defaults():
    conn = make_conn(RecordingCursor())
    conn._fetch_all = lambda _sql, _params: [{"id": "doc1"}, {"id": None}, ("doc2",), []]

    assert conn.fetch_metadata_doc_ids("ragflow_doc_meta_tenant", ["kb1"], "meta_fields ? 'a'", [], 0) == ["doc1", "doc2"]


def test_search_doc_meta_without_kb_scope_uses_default_limit_and_order():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("__total",)]
    cursor.rows = [("doc1", "kb1", 1)]
    conn = make_conn(cursor)

    result = conn.search(
        ["id", "kb_id"],
        [],
        {},
        [],
        type("Order", (), {"fields": [("id", True)]})(),
        0,
        0,
        "ragflow_doc_meta_tenant",
        [],
    )

    sql, params = cursor.executed[-1]
    assert result.total == 1
    assert "ORDER BY id DESC" in sql
    assert params == [10000, 0]


def test_search_doc_meta_empty_page_count_without_where_clause():
    conn = make_conn(RecordingCursor())
    conn._fetch_all_with_description = lambda _sql, _params: ([], [("id",), ("kb_id",), ("__total",)])
    count_queries = []
    conn._fetch_one = lambda sql, params: count_queries.append((sql, params)) or (3,)

    result = conn.search(["id"], [], {}, [], OrderByExpr(), 5, 10, "ragflow_doc_meta_tenant", [])

    assert result == SearchResult(total=3, chunks=[])
    assert count_queries == [('SELECT COUNT(*) FROM "public"."ragflow_doc_meta_tenant"', [])]


def test_search_chunk_aggregation_ignores_null_values_and_missing_counts():
    cursor = SequencedCursor([([(None, 2), ("risk", None)], [("value",), ("count",)])])
    conn = make_conn(cursor)

    result = conn.search([], [], {}, [], OrderByExpr(), 0, 10, "ragflow_tenant", ["kb1"], ["tag_kwd"])

    assert result == SearchResult(total=1, chunks=[{"value": "risk", "count": 0}])


def test_fetch_metadata_doc_ids_rejects_chunk_table():
    conn = make_conn(RecordingCursor())

    with pytest.raises(ValueError, match="document metadata"):
        conn.fetch_metadata_doc_ids("ragflow_tenant", ["kb1"], "TRUE", [], 10)


def test_adjust_chunk_pagerank_requires_scope_and_updates_by_id_and_kb():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    assert conn.adjust_chunk_pagerank_fea("", "ragflow_tenant", "kb1", 1) is False
    assert conn.adjust_chunk_pagerank_fea("c1", "ragflow_tenant", "", 1) is False
    assert conn.adjust_chunk_pagerank_fea("c1", "ragflow_tenant", "kb1", 3, min_w=1, max_w=9) is True

    sql, params = cursor.executed[-1]
    assert "WHERE kb_id = %s AND id = %s" in sql
    assert params == [1, 9, 3, "kb1", "c1"]


def test_sort_search_chunks_supports_default_and_explicit_ordering():
    conn = GaussDBConnection.__new__(GaussDBConnection)
    chunks = [
        {"id": "b", "kb_id": "kb1", "_score": 0.1, "page_num_int": [2], "position_int": [[0, 0, 0, 3]]},
        {"id": "a", "kb_id": "kb1", "_score": 0.9, "page_num_int": [1], "position_int": [[0, 0, 0, 9]]},
    ]

    assert [row["id"] for row in conn._sort_search_chunks(chunks, OrderByExpr(), has_match=True)] == ["a", "b"]
    assert [row["id"] for row in conn._sort_search_chunks(chunks, OrderByExpr(), has_match=False)] == ["a", "b"]
    ordered = conn._sort_search_chunks(chunks, type("Order", (), {"fields": [("page_num_int", False)]})(), has_match=False)
    assert [row["id"] for row in ordered] == ["a", "b"]


def test_build_set_clause_covers_remove_add_metadata_and_rejects_bad_targets():
    conn = make_conn(RecordingCursor())

    set_sql, params = conn._build_set_clause(
        {"remove": "img_id", "metadata": {"_group_id": "g1", "_title": "Doc"}, "tag_kwd": ["risk"]},
        is_meta=False,
        condition={"id": "c1"},
    )
    assert "img_id = NULL" in set_sql
    assert "metadata = %s::jsonb" in set_sql
    assert "group_id = %s" in set_sql
    assert "docnm_kwd = %s" in set_sql
    assert "tag_kwd = %s::jsonb" in set_sql
    assert params[-3:] == ["g1", "Doc", '["risk"]']

    for value in [
        {"remove": 1},
        {"remove": "bad_column"},
        {"remove": {"doc_id": "d1"}},
        {"add": "bad"},
        {"add": {"doc_id": "d1"}},
        {"unknown": 1},
    ]:
        with pytest.raises(ValueError):
            conn._build_set_clause(value, is_meta=False, condition={})


def test_build_set_clause_metadata_without_title_or_group_and_meta_table_jsonb():
    conn = make_conn(RecordingCursor())

    chunk_sql, chunk_params = conn._build_set_clause({"metadata": {"status": "open"}}, is_meta=False, condition={})
    meta_sql, meta_params = conn._build_set_clause({"meta_fields": {"status": "open"}}, is_meta=True, condition={})

    assert chunk_sql == "metadata = %s::jsonb"
    assert chunk_params == ['{"status": "open"}']
    assert meta_sql == "meta_fields = %s::jsonb"
    assert meta_params == ['{"status": "open"}']


def test_build_where_clause_covers_exists_must_not_lists_and_rejections():
    conn = make_conn(RecordingCursor())

    where_sql, params = conn._build_where_clause(
        {"exists": "doc_id", "must_not": {"exists": "img_id"}, "doc_id": ["d1", "d2"], "tag_kwd": "risk"},
        "kb1",
        is_meta=False,
    )

    assert "doc_id IS NOT NULL" in where_sql
    assert "img_id IS NULL" in where_sql
    assert "doc_id IN (%s, %s)" in where_sql
    assert "tag_kwd @> %s::jsonb" in where_sql
    assert params == ["d1", "d2", '["risk"]', "kb1"]

    with pytest.raises(ValueError, match="does not match"):
        conn._build_where_clause({"kb_id": "kb2"}, "kb1", is_meta=False)
    with pytest.raises(ValueError, match="empty list"):
        conn._build_where_clause({"doc_id": []}, "kb1", is_meta=False)
    with pytest.raises(ValueError, match="unsupported"):
        conn._build_where_clause({"bad_column": "x"}, "kb1", is_meta=False)


def test_build_where_clause_meta_exists_and_jsonb_empty_list_rejection():
    conn = make_conn(RecordingCursor())

    where_sql, params = conn._build_where_clause({"exists": "meta_fields", "kb_id": "kb1"}, None, is_meta=True)
    assert where_sql == "meta_fields IS NOT NULL AND kb_id = %s"
    assert params == ["kb1"]

    with pytest.raises(ValueError, match="empty list"):
        conn._build_where_clause({"tag_kwd": []}, "kb1", is_meta=False)


def test_normalize_row_helpers_and_decoders_cover_edge_types():
    conn = make_conn(RecordingCursor(), vector_dimensions=[4])

    row = conn._normalize_chunk_row(
        "ragflow_tenant",
        {
            "id": "c1",
            "kb_id": "kb1",
            "doc_id": "d1",
            "metadata": {"_title": "Doc"},
            "extra": "not-json",
            "unknown": "kept",
        },
        "kb1",
        [],
    )

    assert row["group_id"] == "d1"
    assert row["docnm_kwd"] == "Doc"
    assert json.loads(row["extra"]) == {"unknown": "kept"}
    assert row["q_4_vec"] == "[0,0,0,0]"
    assert row["q_4_vec_valid"] is False

    metadata_without_scope = conn._normalize_chunk_row(
        "ragflow_tenant",
        {"id": "c2", "kb_id": "kb1", "metadata": {"status": "open"}},
        "kb1",
        [4],
    )
    assert "group_id" not in metadata_without_scope
    assert "docnm_kwd" not in metadata_without_scope

    no_metadata_or_doc_id = conn._normalize_chunk_row("ragflow_tenant", {"id": "c3", "kb_id": "kb1"}, "kb1", [4])
    assert "group_id" not in no_metadata_or_doc_id

    for document in [{}, {"id": "c1", "kb_id": "other"}]:
        with pytest.raises(ValueError):
            conn._normalize_chunk_row("ragflow_tenant", document, "kb1", [])
    with pytest.raises(ValueError):
        conn._normalize_chunk_row("ragflow_tenant", {"id": "c1"}, None, [])


def test_module_level_helpers_cover_nulls_duplicates_and_invalid_values():
    assert normalize_kb_id(["kb1", "kb2"]) == "kb1"
    assert normalize_kb_id(None) is None
    assert normalize_kb_ids(["kb1", "", None, "kb1", "kb2"]) == ["kb1", "kb2"]
    assert normalize_table_names(" ragflow_a, ,ragflow_b ") == ["ragflow_a", "ragflow_b"]
    assert parse_fusion_vector_weight(FusionExpr("weighted_sum", 10, {"weights": "0.1,not-a-number"})) is None
    assert parse_fusion_vector_weight(FusionExpr("weighted_sum", 10, {})) is None
    assert sortable_search_value(None, "doc_id") == ""
    assert sortable_search_value(3, "doc_id") == 3
    assert sortable_search_value("doc-a", "doc_id") == "doc-a"
    assert sortable_search_value([[0, 0, 0, 7]], "position_int") == 7.0
    assert sortable_search_value([8], "top_int") == 8.0
    assert nested_numeric_value([[1]], [0, 3]) == 0.0
    assert nested_numeric_value([["2"]], [0, 0]) == 2.0
    assert parse_json_dict({"a": 1}) == {"a": 1}
    assert parse_json_dict("not-json") == {}
    assert parse_json_dict("[1]") == {}
    assert parse_vector_literal("not-json") is None
    assert parse_vector_literal('{"not":"list"}') is None
    assert vector_literal("[1,2,3,4]", 4) == "[1,2,3,4]"
    assert vector_literal("not-json", 4) == "not-json"
    with pytest.raises(ValueError):
        vector_literal([1, 2], 4)
    assert zero_vector_literal(3) == "[0,0,0]"
    assert normalize_column_value("doc_id", None) is None
    assert normalize_column_value("kb_id", ["kb1"]) == "kb1"
    assert normalize_column_value("content_with_weight", {"text": "x"}) == '{"text": "x"}'
    assert normalize_column_value("tag_kwd", ["risk"]) == '["risk"]'
    assert decode_column_value("metadata", '{"a":1}') == {"a": 1}
    assert decode_column_value("metadata", "not-json") == "not-json"
    assert decode_column_value("metadata", {"a": 1}) == {"a": 1}
    assert decode_column_value("q_4_vec", "[1,2,3,4]") == [1, 2, 3, 4]
    assert decode_column_value("q_4_vec", "not-json") == "not-json"
    assert decode_column_value("q_4_vec", [1, 2, 3, 4]) == [1, 2, 3, 4]
    assert select_doc_meta_columns(["id", "kb_id", "id"]) == ["id", "kb_id"]
    assert build_doc_meta_order_by(type("Order", (), {"fields": [("id", True), ("kb_id", False)]})()) == "id DESC, kb_id ASC"
    with pytest.raises(ValueError):
        select_doc_meta_columns(["doc_id"])
    with pytest.raises(ValueError):
        build_doc_meta_order_by(type("Order", (), {"fields": [("doc_id", False)]})())
    with pytest.raises(ValueError):
        validate_filter_column("bad", is_meta=False)
    close_cursor(object())


def test_build_chunk_upsert_requires_rows():
    conn = make_conn(RecordingCursor())

    with pytest.raises(ValueError, match="rows are required"):
        conn._build_chunk_upsert("ragflow_tenant", [])


def test_execute_statements_rolls_back_and_returns_connection_on_failure():
    class FailingStatementCursor(RecordingCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if sql == "bad":
                raise RuntimeError("statement failed")

    cursor = FailingStatementCursor()
    conn = make_conn(cursor)

    with pytest.raises(RuntimeError, match="statement failed"):
        conn._execute_statements(["ok", ("bad", ["p"])])

    assert conn.pool.conn.rollbacks == 1
    assert cursor.closed is True
    assert conn.pool.put_back == [conn.pool.conn]


def extract_insert_columns(sql):
    segment = sql.split("(", 1)[1].split(")", 1)[0]
    return [column.strip() for column in segment.split(",")]
