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
from common.doc_store.gaussdb_conn_base import GaussDBDDLBuilder, UnsafeGaussDBSQL
from rag.utils.gaussdb_conn import GaussDBConnection, SearchResult


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


class UnsupportedDiskannError(Exception):
    pgcode = "0A000"


class UndefinedTableError(Exception):
    pgcode = "42P01"


class MissingTableOnceCursor(RecordingCursor):
    def __init__(self):
        super().__init__()
        self.insert_attempts = 0

    def executemany(self, sql, params):
        materialized = [list(row) for row in params]
        self.executed.append((sql, materialized))
        self.insert_attempts += 1
        if self.insert_attempts == 1:
            raise UndefinedTableError("chunk table does not exist")
        self.rowcount = len(materialized)


class UnsupportedDiskannCursor(RecordingCursor):
    def execute(self, sql, params=None):
        super().execute(sql, params)
        if "USING gsdiskann" in sql:
            raise UnsupportedDiskannError("The vectordb indexes are supported only by high-order features")


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


def test_scoped_search_keeps_kb_boundary_when_optional_doc_ids_are_empty():
    conn = object.__new__(GaussDBConnection)

    condition = conn._scoped_search_condition({"doc_ids": []}, ["kb1"])

    assert condition == {"doc_id": [], "kb_id": ["kb1"]}


def test_scoped_search_rejects_empty_kb_boundary():
    conn = object.__new__(GaussDBConnection)

    with pytest.raises(ValueError, match="kb_id boundary"):
        conn._scoped_search_condition({"kb_id": []}, None)


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
    assert "FROM public.ragflow_tenant" in sql
    assert "kb_id = 'kb1'" in sql
    assert "LIMIT 20" in sql
    assert params == []
    assert result == {
        "columns": [{"name": "doc_id", "type": "text"}, {"name": "amount", "type": "text"}],
        "rows": [["doc1", "120"]],
    }


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


def test_parse_match_expressions_prefers_gaussdb_tokenized_keywords():
    conn = make_conn(RecordingCursor())

    parsed = conn._parse_match_expressions(
        [MatchTextExpr(["content_ltks"], "测试检索问题", 20, {"original_query": "测试检索问题"})],
        None,
        {"keywords": ["测试", "检索", "问题"]},
    )

    assert parsed["keywords"] == ["测试", "检索", "问题"]


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
    assert "INSERT INTO public.ragflow_tenant" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "%s::floatvector(4)" in sql
    assert params[0][0] == "c1"
    assert "[0.1,0.2,0.3,0.4]" in params[0]
    assert any(value is True for value in params[0])
    assert conn.pool.conn.commits == 1


def test_insert_chunk_creates_missing_gaussdb_table_and_retries_once():
    cursor = MissingTableOnceCursor()
    conn = make_conn(cursor, vector_dimensions=[4])
    create_calls = []
    conn.create_idx = lambda index_name, dataset_id, vector_size: create_calls.append(
        (index_name, dataset_id, vector_size)
    )

    errors = conn.insert(
        [
            {
                "id": "c1",
                "kb_id": "kb1",
                "doc_id": "d1",
                "content_with_weight": "hello",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        "ragflow_tenant",
        "kb1",
    )

    assert errors == []
    assert create_calls == [("ragflow_tenant", "kb1", 4)]
    assert cursor.insert_attempts == 2
    assert conn.pool.conn.rollbacks == 1
    assert conn.pool.conn.commits == 1


def test_insert_chunk_does_not_create_table_for_unrelated_write_failure():
    class InvalidWriteCursor(RecordingCursor):
        def executemany(self, sql, params):
            super().executemany(sql, params)
            raise ValueError("invalid chunk value")

    cursor = InvalidWriteCursor()
    conn = make_conn(cursor, vector_dimensions=[4])
    create_calls = []
    conn.create_idx = lambda *args: create_calls.append(args)

    errors = conn.insert(
        [
            {
                "id": "c1",
                "kb_id": "kb1",
                "doc_id": "d1",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        "ragflow_tenant",
        "kb1",
    )

    assert errors == ["c1"]
    assert create_calls == []
    assert conn.pool.conn.rollbacks == 1


def test_insert_chunk_does_not_create_table_when_target_still_exists():
    cursor = MissingTableOnceCursor()
    conn = make_conn(cursor, vector_dimensions=[4])
    conn.index_exist = lambda *_args: True
    create_calls = []
    conn.create_idx = lambda *args: create_calls.append(args)

    errors = conn.insert(
        [
            {
                "id": "c1",
                "kb_id": "kb1",
                "doc_id": "d1",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
            }
        ],
        "ragflow_tenant",
        "kb1",
    )

    assert errors == ["c1"]
    assert create_calls == []
    assert cursor.insert_attempts == 1


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


def test_get_promotes_dynamic_fields_from_extra_jsonb():
    cursor = RecordingCursor()
    cursor.description = [("id",), ("kb_id",), ("extra",)]
    cursor.rows = [("c1", "kb1", '{"compile_kwd": "artifact_page"}')]
    conn = make_conn(cursor)

    row = conn.get("c1", "ragflow_tenant", ["kb1"])

    assert row["compile_kwd"] == "artifact_page"
    assert row["extra"] == {"compile_kwd": "artifact_page"}


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
    assert "INSERT INTO public.ragflow_doc_meta_tenant" in sql
    assert "(id, kb_id, meta_fields)" in sql
    assert "%s::jsonb" in sql
    assert params == [["doc1", "kb1", '{"author": "Alice"}']]


def test_delete_chunk_scopes_by_id_and_kb_id():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    deleted = conn.delete({"id": "c1"}, "ragflow_tenant", "kb1")

    sql, params = cursor.executed[-1]
    assert deleted == 1
    assert "DELETE FROM public.ragflow_tenant" in sql
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
    assert "DELETE FROM public.ragflow_doc_meta_tenant" in sql
    assert params == ["doc1", "kb1"]


def test_delete_rejects_unscoped_condition():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    deleted = conn.delete({}, "ragflow_tenant", "kb1")

    assert deleted == 0
    assert cursor.executed == []


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


def test_update_serializes_dynamic_vector_and_marks_it_valid():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    updated = conn.update(
        {"id": "c1"},
        {"content_with_weight": "更新后的内容", "q_4_vec": [0.1, 0.2, 0.3, 0.4]},
        "ragflow_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert updated is True
    assert "q_4_vec = %s::floatvector(4)" in sql
    assert "q_4_vec_valid = %s" in sql
    assert params == ["更新后的内容", "[0.1,0.2,0.3,0.4]", True, "c1", "kb1"]


def test_update_merges_dynamic_fields_into_extra_jsonb():
    cursor = RecordingCursor()
    cursor.rows = [("c1", "kb1", '{"existing": 1}')]
    cursor.rowcount = 1
    conn = make_conn(cursor)

    updated = conn.update(
        {"id": "c1"},
        {"compile_kwd": "artifact_page", "source_chunk_ids": ["s1", "s2"]},
        "ragflow_tenant",
        "kb1",
    )

    select_sql, select_params = cursor.executed[-2]
    sql, params = cursor.executed[-1]
    assert updated is True
    assert "SELECT id, kb_id, extra" in select_sql
    assert "FOR UPDATE" in select_sql
    assert select_params == ["c1", "kb1"]
    assert "extra = %s::jsonb" in sql
    assert "jsonb_set" not in sql
    assert json.loads(params[0]) == {
        "existing": 1,
        "compile_kwd": "artifact_page",
        "source_chunk_ids": ["s1", "s2"],
    }
    assert params[1:] == ["kb1", "c1"]


def test_delete_filters_dynamic_fields_through_extra_jsonb():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    deleted = conn.delete(
        {"compile_kwd": ["artifact_page"]},
        "ragflow_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert deleted == 1
    assert "(extra -> 'compile_kwd') = %s::jsonb" in sql
    assert params == ['"artifact_page"', '["artifact_page"]', "kb1"]


def test_update_builds_one_a_mode_compatible_assignment_for_tag_rename():
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    updated = conn.update(
        {"id": "c1"},
        {"remove": {"tag_kwd": "old"}, "add": {"tag_kwd": "new"}},
        "ragflow_tenant",
        "kb1",
    )

    sql, params = cursor.executed[-1]
    assert updated is True
    assert sql.count("tag_kwd =") == 1
    assert "jsonb_array_elements_text" in sql
    assert "array_agg" in sql
    assert "array_to_json" in sql
    assert "UNION SELECT %s AS value" in sql
    assert "LATERAL" not in sql
    assert "jsonb_insert" not in sql
    assert "tag_kwd - %s" not in sql
    assert params[:2] == ["old", "new"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"remove": {"tag_kwd": "risk"}}, ["risk", "c1", "kb1"]),
        ({"add": {"tag_kwd": "risk"}}, ["risk", "c1", "kb1"]),
    ],
)
def test_update_builds_a_mode_compatible_single_tag_operation(payload, expected):
    cursor = RecordingCursor()
    cursor.rowcount = 1
    conn = make_conn(cursor)

    assert conn.update({"id": "c1"}, payload, "ragflow_tenant", "kb1") is True

    sql, params = cursor.executed[-1]
    assert sql.count("tag_kwd =") == 1
    assert "jsonb_array_elements_text" in sql
    assert "jsonb_insert" not in sql
    assert "tag_kwd - %s" not in sql
    assert params == expected


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
    assert "SELECT id, kb_id, meta_fields, COUNT(*) OVER() AS __total FROM public.ragflow_doc_meta_tenant" in sql
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
    assert "SELECT COUNT(*) FROM public.ragflow_doc_meta_tenant" in sql
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
    assert "SELECT id FROM public.ragflow_doc_meta_tenant" in sql
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
    assert "DELETE FROM public.ragflow_tenant WHERE kb_id = %s" in sql
    assert params == ["kb1"]


def test_delete_idx_without_dataset_id_drops_table():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.delete_idx("ragflow_tenant", None)

    sql, params = cursor.executed[-1]
    assert sql == "DROP TABLE IF EXISTS public.ragflow_tenant"
    assert params == []


def test_create_idx_sets_maintenance_work_mem_before_diskann():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.create_idx("ragflow_tenant", "kb1", 4)

    statements = [sql for sql, _params in cursor.executed]
    assert statements[0] == "SELECT pg_advisory_xact_lock(hashtext(%s))"
    assert cursor.executed[0][1] == ["create_idx:public:ragflow_tenant"]
    work_mem_index = statements.index("SET LOCAL maintenance_work_mem = '1GB'")
    diskann_index = next(i for i, sql in enumerate(statements) if "USING gsdiskann" in sql)
    assert work_mem_index < diskann_index


def test_create_idx_keeps_functional_table_and_propagates_missing_diskann_prerequisite():
    cursor = UnsupportedDiskannCursor()
    conn = make_conn(cursor)

    with pytest.raises(UnsupportedDiskannError, match="supported only by high-order features"):
        conn.create_idx("ragflow_tenant", "kb1", 4)

    statements = [sql for sql, _params in cursor.executed]
    assert any(sql.startswith("CREATE TABLE IF NOT EXISTS") for sql in statements)
    assert any("ADD COLUMN IF NOT EXISTS q_4_vec" in sql for sql in statements)
    assert any("USING gsdiskann" in sql for sql in statements)
    assert conn.pool.conn.commits == 1
    assert conn.pool.conn.rollbacks == 1


def test_create_idx_commits_functional_ddl_before_diskann():
    cursor = RecordingCursor()
    conn = make_conn(cursor)

    conn.create_idx("ragflow_tenant", "kb1", 4)

    assert conn.pool.conn.commits == 2
    assert conn.pool.conn.rollbacks == 0


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
    result = SearchResult(total=2, chunks=[{"tag_kwd": ["a", "b"]}, {"tag_kwd": ["a"]}])
    aggregate_result = SearchResult(total=2, chunks=[{"value": "a", "count": 2}])

    assert sorted(conn.get_aggregation(result, "tag_kwd")) == [("a", 2), ("b", 1)]
    assert conn.get_aggregation(aggregate_result, "tag_kwd") == [("a", 2)]


def extract_insert_columns(sql):
    segment = sql.split("(", 1)[1].split(")", 1)[0]
    return [column.strip() for column in segment.split(",")]
