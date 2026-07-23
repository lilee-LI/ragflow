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

from common.doc_store.gaussdb_conn_base import GaussDBSearchBuilder, InvalidGaussDBObjectName


def test_empty_optional_collection_is_omitted_from_where_clause():
    builder = GaussDBSearchBuilder(schema="public")

    sql, params = builder.build_condition_where({"doc_id": [], "available_int": 1})

    assert sql == "available_int = %s"
    assert params == [1]


def test_dynamic_chunk_field_exists_filter_reads_extra_jsonb():
    builder = GaussDBSearchBuilder(schema="public")

    sql, params = builder.build_condition_where({"kb_id": "kb1", "must_not": {"exists": "compile_kwd"}})

    assert "(extra -> 'compile_kwd') IS NULL" in sql
    assert "(extra -> 'compile_kwd') = 'null'::jsonb" in sql
    assert params == ["kb1"]


def test_dynamic_chunk_field_select_and_filter_are_backed_by_extra_jsonb():
    builder = GaussDBSearchBuilder(schema="public")

    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "compile_kwd"],
        condition={"kb_id": "kb1", "compile_kwd": ["artifact_page"]},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        offset=0,
        limit=10,
    )

    assert "(extra -> 'compile_kwd') AS compile_kwd" in sql
    assert "(extra -> 'compile_kwd') = %s::jsonb" in sql
    assert params[:3] == ["kb1", '"artifact_page"', '["artifact_page"]']


def test_dynamic_chunk_field_rejects_unsafe_json_key():
    builder = GaussDBSearchBuilder(schema="public")

    with pytest.raises(InvalidGaussDBObjectName):
        builder.build_condition_where({"must_not": {"exists": "compile_kwd'; DROP TABLE user; --"}})


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


def test_cjk_hybrid_search_uses_bound_substring_scoring_instead_of_empty_fts():
    builder = GaussDBSearchBuilder(schema="public")

    sql, params = builder.build_search_sql(
        table="ragflow_tenant",
        select_fields=["id", "content_with_weight"],
        condition={"kb_id": "kb1", "available_int": 1},
        keywords=["测试", "检索", "问题"],
        vector=[0.1, 0.2, 0.3, 0.4],
        vector_dim=4,
        vector_weight=0.3,
        similarity_threshold=0.5,
        offset=0,
        limit=5,
    )

    assert "strpos(" in sql
    assert "plainto_tsquery" not in sql
    assert all(keyword in params for keyword in ["测试", "检索", "问题"])
    assert sql.count("%s") == len(params)


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


def test_aggregation_sql_expands_jsonb_arrays():
    builder = GaussDBSearchBuilder(schema="public")
    sql, params = builder.build_aggregation_sql(
        table="ragflow_tenant",
        field_name="tag_kwd",
        condition={"kb_id": "kb1"},
    )

    assert "jsonb_array_elements_text" in sql
    assert "tag_kwd" in sql
    assert "FROM (SELECT jsonb_array_elements_text" in sql
    assert ") AS expanded" in sql
    assert "LATERAL" not in sql
    assert "FROM public.ragflow_tenant," not in sql
    assert params == ["kb1", 1000]
