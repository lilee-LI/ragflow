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
from psycopg2 import sql


pytestmark = [pytest.mark.gaussdb_integration, pytest.mark.gaussdb_both]


OP_NE = "\u2260"
OP_GE = "\u2265"
OP_LE = "\u2264"
EMPTY_SQL = (
    "((meta_fields #> '{status}') IS NULL OR meta_fields #> '{status}' = 'null'::jsonb "
    "OR meta_fields #> '{status}' = '\"\"'::jsonb OR meta_fields #> '{status}' = '[]'::jsonb "
    "OR meta_fields #> '{status}' = '{}'::jsonb)"
)
NOT_EMPTY_SQL = (
    "((meta_fields #> '{status}') IS NOT NULL AND "
    "(meta_fields #> '{status}' = 'null'::jsonb) IS NOT TRUE AND "
    "(meta_fields #> '{status}' = '\"\"'::jsonb) IS NOT TRUE AND "
    "(meta_fields #> '{status}' = '[]'::jsonb) IS NOT TRUE AND "
    "(meta_fields #> '{status}' = '{}'::jsonb) IS NOT TRUE)"
)


def _assert_filter_contract(filters, expected_sql, expected_params, logic="and"):
    from common.metadata_gaussdb_filter import build_gaussdb_filter

    actual_sql, actual_params = build_gaussdb_filter(filters, logic)
    assert actual_sql == expected_sql
    assert actual_params == expected_params


@pytest.fixture
def metadata_scope(gaussdb_env, gaussdb_admin_conn, ragflow_kb_context, register_table, monkeypatch):
    from api.db.services.doc_metadata_service import DocMetadataService
    from common import settings

    monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", True, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_INFINITY", False, raising=False)
    tenant_id = ragflow_kb_context["tenant_id"]
    kb_id = ragflow_kb_context["kb_id"]
    table = DocMetadataService._get_doc_meta_index_name(tenant_id)
    register_table(table)
    assert settings.docStoreConn.create_doc_meta_idx(table) is True
    with gaussdb_admin_conn.cursor() as cur:
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
    assert "storage_type=ustore" in str(reloptions).lower()
    assert sum(" using ubtree " in definition and "(kb_id)" in definition for definition in index_defs) == 1
    try:
        yield {
            "admin_conn": gaussdb_admin_conn,
            "conn": settings.docStoreConn,
            "kb_id": kb_id,
            "kb_ids": [kb_id],
            "schema": gaussdb_env["schema"],
            "table": table,
            "tenant_id": tenant_id,
        }
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


def _insert(scope, rows, kb_id=None):
    target_kb_id = kb_id or scope["kb_id"]
    values = [(row["id"], target_kb_id, json.dumps(row["meta_fields"], ensure_ascii=False, separators=(",", ":"))) for row in rows]
    with scope["admin_conn"].cursor() as cur:
        cur.executemany(
            sql.SQL("INSERT INTO {} (id, kb_id, meta_fields) VALUES (%s, %s, %s::jsonb)").format(sql.Identifier(scope["schema"], scope["table"])),
            values,
        )
    scope["admin_conn"].commit()
    with scope["admin_conn"].cursor() as cur:
        cur.execute(sql.SQL("SELECT id, kb_id, meta_fields FROM {} ORDER BY id").format(sql.Identifier(scope["schema"], scope["table"])))
        stored = cur.fetchall()
    assert stored == sorted(
        [(row["id"], target_kb_id, row["meta_fields"]) for row in rows],
        key=lambda item: item[0],
    )


def _match(scope, filters, kb_ids=None, limit=10000, logic="and"):
    from api.db.services.doc_metadata_service import DocMetadataService

    result = DocMetadataService.filter_doc_ids_by_meta_pushdown(kb_ids or scope["kb_ids"], filters, logic, limit=limit)
    assert result is not None
    return result


def test_tc_mgf_203_empty_matches_missing_and_excludes_nonempty(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "missing", "meta_fields": {"other": 1}},
            {"id": "ready", "meta_fields": {"status": "ready"}},
        ],
    )

    empty_filter = [{"key": "status", "op": "empty", "value": None}]
    _assert_filter_contract(empty_filter, EMPTY_SQL, [])
    assert _match(metadata_scope, empty_filter) == ["missing"]


def test_tc_mgf_204_empty_matches_json_null_and_excludes_nonempty(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "json-null", "meta_fields": {"status": None}},
            {"id": "active", "meta_fields": {"status": "active"}},
        ],
    )

    empty_filter = [{"key": "status", "op": "empty", "value": None}]
    not_empty_filter = [{"key": "status", "op": "not empty", "value": None}]
    _assert_filter_contract(empty_filter, EMPTY_SQL, [])
    _assert_filter_contract(not_empty_filter, NOT_EMPTY_SQL, [])
    assert _match(metadata_scope, empty_filter) == ["json-null"]
    assert _match(metadata_scope, not_empty_filter) == ["active"]


def test_tc_mgf_205_empty_matches_empty_string_and_not_equal(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "empty-string", "meta_fields": {"status": ""}},
            {"id": "active", "meta_fields": {"status": "active"}},
        ],
    )

    empty_filter = [{"key": "status", "op": "empty", "value": None}]
    equal_empty_filter = [{"key": "status", "op": "=", "value": ""}]
    not_equal_filter = [{"key": "status", "op": OP_NE, "value": "active"}]
    not_empty_filter = [{"key": "status", "op": "not empty", "value": None}]
    not_equal_sql = "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"
    _assert_filter_contract(empty_filter, EMPTY_SQL, [])
    _assert_filter_contract(equal_empty_filter, "(meta_fields #> '{status}' = '\"\"'::jsonb)", [])
    _assert_filter_contract(not_equal_filter, not_equal_sql, ["active", "active"])
    _assert_filter_contract(not_empty_filter, NOT_EMPTY_SQL, [])
    assert _match(metadata_scope, empty_filter) == ["empty-string"]
    assert _match(metadata_scope, equal_empty_filter) == ["empty-string"]
    assert _match(metadata_scope, not_equal_filter) == ["empty-string"]
    assert _match(metadata_scope, not_empty_filter) == ["active"]


def test_tc_mgf_206_empty_matches_empty_array_and_excludes_nonempty_array(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "empty-array", "meta_fields": {"tags": []}},
            {"id": "tagged", "meta_fields": {"tags": ["audit"]}},
        ],
    )

    empty_filter = [{"key": "tags", "op": "empty", "value": None}]
    not_empty_filter = [{"key": "tags", "op": "not empty", "value": None}]
    _assert_filter_contract(
        empty_filter,
        EMPTY_SQL.replace("{status}", "{tags}"),
        [],
    )
    _assert_filter_contract(
        not_empty_filter,
        NOT_EMPTY_SQL.replace("{status}", "{tags}"),
        [],
    )
    assert _match(metadata_scope, empty_filter) == ["empty-array"]
    assert _match(metadata_scope, not_empty_filter) == ["tagged"]


def test_tc_mgf_207_empty_matches_empty_object_and_excludes_nonempty_object(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "empty-object", "meta_fields": {"profile": {}}},
            {"id": "profiled", "meta_fields": {"profile": {"name": "Alice"}}},
        ],
    )

    empty_filter = [{"key": "profile", "op": "empty", "value": None}]
    not_empty_filter = [{"key": "profile", "op": "not empty", "value": None}]
    _assert_filter_contract(empty_filter, EMPTY_SQL.replace("{status}", "{profile}"), [])
    _assert_filter_contract(not_empty_filter, NOT_EMPTY_SQL.replace("{status}", "{profile}"), [])
    assert _match(metadata_scope, empty_filter) == ["empty-object"]
    assert _match(metadata_scope, not_empty_filter) == ["profiled"]


def test_tc_mgf_208_not_equal_matches_null_and_empty_string_but_not_missing(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "json-null", "meta_fields": {"status": None}},
            {"id": "empty-string", "meta_fields": {"status": ""}},
            {"id": "active", "meta_fields": {"status": "active"}},
            {"id": "missing", "meta_fields": {"other": 1}},
        ],
    )

    not_equal_filter = [{"key": "status", "op": OP_NE, "value": "active"}]
    not_equal_sql = "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"
    _assert_filter_contract(not_equal_filter, not_equal_sql, ["active", "active"])
    assert _match(metadata_scope, not_equal_filter) == [
        "empty-string",
        "json-null",
    ]


def test_tc_mgf_209_not_empty_excludes_all_five_empty_states(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "missing", "meta_fields": {"other": 1}},
            {"id": "json-null", "meta_fields": {"status": None}},
            {"id": "empty-string", "meta_fields": {"status": ""}},
            {"id": "empty-array", "meta_fields": {"status": []}},
            {"id": "empty-object", "meta_fields": {"status": {}}},
            {"id": "active", "meta_fields": {"status": "active"}},
        ],
    )

    not_empty_filter = [{"key": "status", "op": "not empty", "value": None}]
    _assert_filter_contract(not_empty_filter, NOT_EMPTY_SQL, [])
    assert _match(metadata_scope, not_empty_filter) == ["active"]


def test_tc_mgf_801_equal_is_case_insensitive(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "upper", "meta_fields": {"status": "Active"}},
            {"id": "lower", "meta_fields": {"status": "active"}},
            {"id": "other", "meta_fields": {"status": "inactive"}},
        ],
    )

    equal_filter = [{"key": "status", "op": "=", "value": "ACTIVE"}]
    equal_sql = "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))"
    _assert_filter_contract(equal_filter, equal_sql, ["active", "active"])
    assert _match(metadata_scope, equal_filter) == ["lower", "upper"]
    assert _match(
        metadata_scope,
        [{"key": "status", "op": "in", "value": ["inactive"]}],
    ) == ["other"]
    assert _match(
        metadata_scope,
        [
            {"key": "status", "op": "=", "value": "active"},
            {"key": "status", "op": OP_NE, "value": "inactive"},
        ],
        logic="and",
    ) == ["lower", "upper"]
    assert _match(
        metadata_scope,
        [
            {"key": "status", "op": "=", "value": "active"},
            {"key": "status", "op": "=", "value": "inactive"},
        ],
        logic="or",
    ) == ["lower", "other", "upper"]


def test_tc_mgf_802_contains_matches_array_element_and_scalar_substring(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "array", "meta_fields": {"tags": ["Acme", "Beta"], "code": "ACME-001"}},
            {"id": "scalar", "meta_fields": {"tags": "Acme Corp", "code": "ACME-002"}},
            {"id": "other", "meta_fields": {"tags": "Contoso", "code": "ZZ-ACME"}},
        ],
    )

    contains_filter = [{"key": "tags", "op": "contains", "value": "acme"}]
    contains_sql = "(jsonb_exists(meta_fields #> '{tags}', %s) OR lower(meta_fields #>> '{tags}') LIKE %s ESCAPE '\\')"
    _assert_filter_contract(contains_filter, contains_sql, ["acme", "%acme%"])
    assert _match(metadata_scope, contains_filter) == ["array", "scalar"]
    assert _match(
        metadata_scope,
        [{"key": "tags", "op": "not contains", "value": "acme"}],
    ) == ["other"]
    assert _match(
        metadata_scope,
        [{"key": "code", "op": "start with", "value": "acme-"}],
    ) == ["array", "scalar"]
    assert _match(
        metadata_scope,
        [{"key": "code", "op": "end with", "value": "-001"}],
    ) == ["array"]


def test_tc_mgf_803_greater_than_filters_numeric_values_and_rejects_non_numeric(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "string-number", "meta_fields": {"amount": "200"}},
            {"id": "small-number", "meta_fields": {"amount": 50}},
            {"id": "non-number", "meta_fields": {"amount": "abc"}},
            {"id": "missing", "meta_fields": {"other": 1}},
        ],
    )

    greater_filter = [{"key": "amount", "op": ">", "value": 100}]
    greater_sql = "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"
    _assert_filter_contract(greater_filter, greater_sql, [100])
    assert _match(metadata_scope, greater_filter) == ["string-number"]
    assert _match(metadata_scope, [{"key": "amount", "op": OP_GE, "value": 200}]) == ["string-number"]
    assert _match(metadata_scope, [{"key": "amount", "op": "<", "value": 100}]) == ["small-number"]
    assert _match(metadata_scope, [{"key": "amount", "op": OP_LE, "value": 50}]) == ["small-number"]
    assert _match(
        metadata_scope,
        [
            {"key": "amount", "op": ">", "value": 40},
            {"key": "amount", "op": "<", "value": 100},
        ],
        logic="and",
    ) == ["small-number"]
    assert _match(
        metadata_scope,
        [
            {"key": "amount", "op": "<", "value": 60},
            {"key": "amount", "op": ">", "value": 150},
        ],
        logic="or",
    ) == ["small-number", "string-number"]


def test_tc_mgf_804_greater_than_normalizes_date_prefix_across_precisions(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "with-time", "meta_fields": {"dt": "2026-07-08 10:30:00"}},
            {"id": "date-only", "meta_fields": {"dt": "2026-07-09"}},
            {"id": "boundary", "meta_fields": {"dt": "2026-07-01"}},
            {"id": "old", "meta_fields": {"dt": "2026-06-01"}},
        ],
    )

    greater_filter = [{"key": "dt", "op": ">", "value": "2026-07"}]
    from common.metadata_gaussdb_filter import build_gaussdb_filter

    actual_sql, actual_params = build_gaussdb_filter(greater_filter, "and")
    assert actual_sql.startswith("(CASE WHEN meta_fields #>> '{dt}' ~")
    assert actual_sql.endswith("END > to_timestamp(%s, 'YYYY-MM-DD HH24:MI:SS'))")
    assert actual_params == ["2026-07-01 00:00:00"]
    assert _match(metadata_scope, greater_filter) == ["date-only", "with-time"]


def test_tc_mgf_805_empty_and_not_empty_distinguish_all_five_empty_states(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "missing", "meta_fields": {"other": 1}},
            {"id": "json-null", "meta_fields": {"status": None}},
            {"id": "empty-string", "meta_fields": {"status": ""}},
            {"id": "empty-array", "meta_fields": {"status": []}},
            {"id": "empty-object", "meta_fields": {"status": {}}},
            {"id": "active", "meta_fields": {"status": "active"}},
        ],
    )

    empty_filter = [{"key": "status", "op": "empty", "value": None}]
    not_empty_filter = [{"key": "status", "op": "not empty", "value": None}]
    _assert_filter_contract(empty_filter, EMPTY_SQL, [])
    _assert_filter_contract(not_empty_filter, NOT_EMPTY_SQL, [])
    empty_ids = _match(metadata_scope, empty_filter)
    not_empty_ids = _match(metadata_scope, not_empty_filter)
    assert empty_ids == ["empty-array", "empty-object", "empty-string", "json-null", "missing"]
    assert not_empty_ids == ["active"]
    assert len(empty_ids) == 5
    assert set(empty_ids).isdisjoint(not_empty_ids)
    assert set(empty_ids) | set(not_empty_ids) == {
        "active",
        "empty-array",
        "empty-object",
        "empty-string",
        "json-null",
        "missing",
    }


def test_tc_mgf_806_not_in_matches_null_and_empty_string_but_not_missing_or_members(metadata_scope):
    _insert(
        metadata_scope,
        [
            {"id": "json-null", "meta_fields": {"status": None}},
            {"id": "empty-string", "meta_fields": {"status": ""}},
            {"id": "member-a", "meta_fields": {"status": "a"}},
            {"id": "member-b", "meta_fields": {"status": "b"}},
            {"id": "missing", "meta_fields": {"other": 1}},
        ],
    )

    not_in_filter = [{"key": "status", "op": "not in", "value": ["a", "b"]}]
    not_in_sql = (
        "((meta_fields #> '{status}') IS NOT NULL AND "
        "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE AND "
        "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"
    )
    _assert_filter_contract(not_in_filter, not_in_sql, ["a", "a", "b", "b"])
    assert _match(metadata_scope, not_in_filter) == [
        "empty-string",
        "json-null",
    ]


def test_tc_mgf_809_limit_plus_one_probe_returns_none_or_complete_result(metadata_scope, monkeypatch):
    from api.db.services.doc_metadata_service import DocMetadataService

    matching_ids = [f"active-{index}" for index in range(1, 6)]
    _insert(
        metadata_scope,
        [{"id": doc_id, "meta_fields": {"status": "active"}} for doc_id in matching_ids] + [{"id": "inactive", "meta_fields": {"status": "inactive"}}],
    )

    fetch_calls = []
    original_fetch = metadata_scope["conn"].fetch_metadata_doc_ids

    def tracked_fetch(*args, **kwargs):
        fetch_calls.append((args, kwargs))
        return original_fetch(*args, **kwargs)

    monkeypatch.setattr(metadata_scope["conn"], "fetch_metadata_doc_ids", tracked_fetch)

    over_limit = DocMetadataService.filter_doc_ids_by_meta_pushdown(metadata_scope["kb_ids"], [{"key": "status", "op": "=", "value": "active"}], "and", limit=3)
    assert over_limit is None
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0][-1] == 4

    fetch_calls.clear()
    exact_limit = DocMetadataService.filter_doc_ids_by_meta_pushdown(metadata_scope["kb_ids"], [{"key": "status", "op": "=", "value": "active"}], "and", limit=5)
    assert exact_limit == matching_ids
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0][-1] == 6

    fetch_calls.clear()
    under_limit = DocMetadataService.filter_doc_ids_by_meta_pushdown(metadata_scope["kb_ids"], [{"key": "status", "op": "=", "value": "active"}], "and", limit=6)
    assert under_limit == matching_ids
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0][-1] == 7
