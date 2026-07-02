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
import pytest

from common.doc_store.gaussdb_conn_base import GaussDBSearchBuilder


def test_fulltext_search_uses_simple_tsvector_and_threshold():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "content_with_weight"],
        condition={"available_int": 1},
        keywords=["contract", "risk"],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=10,
    )

    assert "to_tsvector('simple'" in sql
    assert "ts_rank" in sql
    assert "available_int = %s" in sql
    assert params[-2:] == [10, 0]


def test_vector_search_filters_invalid_placeholder_vectors():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"available_int": 1},
        keywords=[],
        vector=[0.1, 0.2, 0.3, 0.4],
        vector_dim=4,
        vector_weight=1.0,
        offset=0,
        limit=5,
    )

    assert "q_4_vec_valid = TRUE" in sql
    assert "q_4_vec <+> %s::floatvector(4)" in sql
    assert params[-2:] == [5, 0]


def test_selecting_vector_field_also_selects_valid_flag_for_decoding():
    builder = GaussDBSearchBuilder(schema="public")
    sql, _params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "q_4_vec"],
        condition={"kb_id": "kb1"},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=5,
    )

    assert "q_4_vec" in sql
    assert "q_4_vec_valid" in sql


def test_select_fields_defaults_and_deduplicates_vector_valid_column():
    builder = GaussDBSearchBuilder(schema="public")

    assert builder.normalize_select_fields(None) == ["id", "kb_id"]
    assert builder.normalize_select_fields(["*"]) == ["id", "kb_id"]
    assert builder.normalize_select_fields(["q_4_vec_valid", "q_4_vec"]) == ["id", "kb_id", "q_4_vec_valid", "q_4_vec"]


def test_hybrid_search_uses_configured_vector_weight():
    builder = GaussDBSearchBuilder(schema="public")
    sql, _params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"available_int": 1},
        keywords=["budget"],
        vector=[0.1, 0.2, 0.3, 0.4],
        vector_dim=4,
        vector_weight=0.7,
        offset=0,
        limit=5,
    )

    assert "%s * COALESCE(vec.vector_score, 0)" in sql
    assert "(1 - %s) * COALESCE(fts.fts_score, 0)" in sql
    assert "FULL OUTER JOIN" in sql


def test_hybrid_search_with_highlight_and_pagerank_uses_prefixed_score_column():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "chunk_order_int"],
        condition={"kb_id": "kb1"},
        keywords=["budget"],
        vector=[0.1, 0.2, 0.3, 0.4],
        vector_dim=4,
        vector_weight=0.7,
        offset=0,
        limit=5,
        highlight_fields=["content_with_weight"],
        pagerank_weight=10.0,
    )

    assert "ts_headline" in sql
    assert "c._order_id AS chunk_order_int" in sql
    assert "c.pagerank_fea" in sql
    assert 10.0 in params


def test_pagerank_feature_adds_bound_score_component():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"kb_id": "kb1"},
        keywords=["risk"],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=5,
        pagerank_weight=10.0,
    )

    assert "pagerank_fea" in sql
    assert "100.0 * %s" in sql
    assert 10.0 in params


def test_position_sort_uses_jsonb_numeric_paths_not_text_sort():
    builder = GaussDBSearchBuilder(schema="public")
    order_sql = builder.build_position_order_sql()

    assert "page_num_int #>> '{0}'" in order_sql
    assert "position_int #>> '{0,3}'" in order_sql
    assert "::int" in order_sql


def test_vector_only_query_omits_fulltext_predicates():
    builder = GaussDBSearchBuilder(schema="public")
    sql, _params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"available_int": 1},
        keywords=[],
        vector=[0.1, 0.2, 0.3, 0.4],
        vector_dim=4,
        vector_weight=1.0,
        offset=0,
        limit=5,
    )

    assert "to_tsvector" not in sql
    assert "q_4_vec_valid = TRUE" in sql


def test_deep_pagination_uses_stable_order_and_offset_limit_params():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"available_int": 1},
        keywords=["risk"],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=200,
        limit=50,
    )

    assert "ORDER BY" in sql
    assert params[-2:] == [50, 200]


def test_chunk_order_field_maps_to_gaussdb_storage_column():
    builder = GaussDBSearchBuilder(schema="public")
    sql, _params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "chunk_order_int"],
        condition={"kb_id": "kb1"},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=10,
    )

    assert "_order_id AS chunk_order_int" in sql
    assert "chunk_order_int AS" not in sql


def test_highlight_expr_uses_bound_keywords_not_inline_literals():
    builder = GaussDBSearchBuilder(schema="public")
    expr, params = builder.build_highlight_expr("content_with_weight", ["risk", "audit"])

    assert "ts_headline" in expr
    assert "plainto_tsquery" in expr
    assert "%s" in expr
    assert "risk" not in expr
    assert params == ["risk audit"]


def test_fulltext_search_can_project_highlight_column():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"kb_id": "kb1"},
        keywords=["risk"],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=5,
        highlight_fields=["content_with_weight"],
    )

    assert "ts_headline" in sql
    assert "AS _highlight" in sql
    assert "risk" in params


def test_aggregation_sql_groups_field_values():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_aggregation_sql(
        table="ragflow_tenant",
        field_name="docnm_kwd",
        condition={"kb_id": "kb1"},
    )

    assert "GROUP BY value" in sql
    assert "docnm_kwd" in sql
    assert params == ["kb1", 1000]


def test_aggregation_sql_rejects_jsonb_array_fields():
    builder = GaussDBSearchBuilder(schema="public")

    with pytest.raises(ValueError, match="JSONB array aggregation"):
        builder.build_aggregation_sql(
            table="ragflow_tenant",
            field_name="tag_kwd",
            condition={"kb_id": "kb1"},
        )


def test_filter_only_search_uses_default_order_and_collection_limit_when_limit_is_zero():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={"kb_id": "kb1"},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=0,
    )

    assert "ORDER BY kb_id ASC, id ASC LIMIT %s OFFSET %s" in sql
    assert params == ["kb1", 10000, 0]


def test_filter_only_search_applies_where_and_plain_field_ordering():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "docnm_kwd"],
        condition={"kb_id": "kb1"},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=5,
        limit=10,
        order_by=type("Order", (), {"fields": [("docnm_kwd", True)]})(),
    )

    assert "WHERE kb_id = %s" in sql
    assert "ORDER BY docnm_kwd DESC" in sql
    assert params == ["kb1", 10, 5]


def test_filter_only_search_without_condition_omits_where_clause():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=10,
    )

    assert " WHERE " not in sql
    assert params == [10, 0]


def test_fulltext_search_without_filters_has_match_predicate_only():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={},
        keywords=["risk"],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=5,
    )

    assert "WHERE to_tsvector" in sql
    assert " AND available_int" not in sql
    assert params[-2:] == [5, 0]


def test_fulltext_builder_without_keywords_or_filters_omits_where_clause():
    builder = GaussDBSearchBuilder(schema="public")
    builder._build_text_match_expr = lambda _keywords: ("", [])
    sql, params = builder._build_fulltext_search_sql(
        table="ragflow_tenant",
        select_fields=["id"],
        condition={},
        keywords=[],
        offset=0,
        limit=5,
        highlight_fields=None,
        pagerank_weight=0.0,
    )

    assert " WHERE " not in sql
    assert params[-2:] == [5, 0]


def test_condition_builder_supports_exists_must_not_lists_jsonb_and_null():
    builder = GaussDBSearchBuilder(schema="public")

    where_sql, params = builder.build_condition_where(
        {
            "exists": "doc_id",
            "must_not": {"exists": "img_id"},
            "doc_id": ["d1", "d2"],
            "tag_kwd": ["risk", "audit"],
            "mom_id": None,
        }
    )

    assert "doc_id IS NOT NULL" in where_sql
    assert "img_id IS NULL" in where_sql
    assert "doc_id IN (%s, %s)" in where_sql
    assert "(tag_kwd @> %s::jsonb OR tag_kwd @> %s::jsonb)" in where_sql
    assert "mom_id IS NULL" in where_sql
    assert params == ["d1", "d2", '["risk"]', '["audit"]']


def test_condition_builder_rejects_empty_list_conditions():
    builder = GaussDBSearchBuilder(schema="public")

    try:
        builder.build_condition_where({"doc_id": []})
    except ValueError as exc:
        assert "empty condition values" in str(exc)
    else:
        raise AssertionError("expected empty list condition to be rejected")


def test_vector_search_rejects_missing_or_mismatched_dimensions():
    builder = GaussDBSearchBuilder(schema="public")

    for vector_dim, vector in [(None, [0.1]), (2, [0.1])]:
        try:
            builder.build_search_sql(
                table="ragflow_tenant",
                select_fields=["id"],
                condition={"kb_id": "kb1"},
                keywords=[],
                vector=vector,
                vector_dim=vector_dim,
                vector_weight=1.0,
                offset=0,
                limit=5,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected invalid vector dimensions to be rejected")


def test_vector_score_and_hybrid_score_helpers_validate_dimensions_and_weight():
    builder = GaussDBSearchBuilder(schema="public")

    assert "q_4_vec <+>" in builder.build_vector_score_expr(4)
    assert "COALESCE(text_score, 0)" in builder.build_hybrid_score_expr("text_score", "vector_score", "0.25")


def test_normalize_select_fields_skips_score_deduplicates_and_rejects_unknown_field():
    builder = GaussDBSearchBuilder(schema="public")

    assert builder.normalize_select_fields(["_score", "doc_id", "doc_id", "q_4_vec"]) == [
        "id",
        "kb_id",
        "doc_id",
        "q_4_vec",
        "q_4_vec_valid",
    ]

    try:
        builder.normalize_select_fields(["not_a_column"])
    except Exception as exc:
        assert "not_a_column" in str(exc)
    else:
        raise AssertionError("expected unknown search field to be rejected")


def test_row_id_and_position_order_are_rendered_as_safe_select_expressions():
    builder = GaussDBSearchBuilder(schema="public")
    sql, _params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["row_id()", "chunk_order_int"],
        condition={"kb_id": "kb1"},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=5,
        order_by=type("Order", (), {"fields": [("position_int", False)]})(),
    )

    assert 'NULL AS "row_id()"' in sql
    assert "_order_id AS chunk_order_int" in sql
    assert "page_num_int #>> '{0}'" in sql
