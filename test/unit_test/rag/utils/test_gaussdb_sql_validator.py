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
import pytest

from common.doc_store.gaussdb_conn_base import (
    ExposedGaussDBTable,
    GaussDBSQLValidator,
    UnsafeGaussDBSQL,
    jsonb_path_literal,
)


def _validator(kb_ids=None, field_map=None, fetch_size=128):
    table = ExposedGaussDBTable.from_field_map(
        physical_name="ragflow_tenant",
        kb_ids=kb_ids or ["kb1"],
        field_map=field_map or {"amount": "number", "dept": "string", "customer,name": "string"},
    )
    return GaussDBSQLValidator({table.logical_name: table}, default_limit=fetch_size)


def test_jsonb_path_literal_encodes_special_keys():
    assert jsonb_path_literal(["amount"]) == "'{amount}'"
    assert jsonb_path_literal(["customer", "name"]) == "'{customer,name}'"
    assert jsonb_path_literal(["customer,name"]) == "'{\"customer,name\"}'"


def test_validator_accepts_select_with_jsonb_path_and_kb_id():
    sql = _validator().validate_and_patch("SELECT doc_id, docnm_kwd, chunk_data #>> '{amount}' AS amount FROM ragflow_tenant WHERE kb_id = 'kb1'").sql

    assert "SELECT" in sql
    assert "chunk_data #>> '{amount}'" in sql
    assert "LIMIT 128" in sql


def test_runtime_table_qualification_qualifies_base_table_but_not_cte():
    validator = GaussDBSQLValidator.readonly_guard()
    sql = validator.validate_and_patch("WITH rows AS (SELECT doc_id, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1') SELECT doc_id FROM rows").sql

    qualified = validator.qualify_runtime_tables(sql, "ragflow_schema")

    assert "FROM ragflow_schema.ragflow_tenant" in qualified
    assert "SELECT doc_id FROM rows" in qualified


def test_validator_rejects_jsonb_path_when_field_map_empty():
    table = ExposedGaussDBTable.from_field_map(
        physical_name="ragflow_tenant",
        kb_ids=["kb1"],
        field_map={},
    )
    validator = GaussDBSQLValidator({table.logical_name: table}, default_limit=128)

    with pytest.raises(UnsafeGaussDBSQL):
        validator.validate_and_patch("SELECT doc_id, chunk_data #>> '{amount}' AS amount FROM ragflow_tenant WHERE kb_id = 'kb1'")


def test_validator_injects_kb_id_for_simple_single_table_query():
    sql = _validator().validate_and_patch("SELECT doc_id, docnm_kwd FROM ragflow_tenant").sql

    assert "kb_id = 'kb1'" in sql
    assert "LIMIT 128" in sql


def test_validator_injects_multi_kb_boundary():
    sql = _validator(kb_ids=["kb1", "kb2"]).validate_and_patch("SELECT doc_id FROM ragflow_tenant").sql

    assert "kb_id IN ('kb1', 'kb2')" in sql


@pytest.mark.parametrize(
    "raw_sql,expected",
    [
        ("SELECT doc_id FROM ragflow_tenant LIMIT 500", "LIMIT 128"),
        ("SELECT doc_id FROM ragflow_tenant LIMIT 20", "LIMIT 20"),
        ("SELECT doc_id FROM ragflow_tenant ORDER BY doc_id", "ORDER BY doc_id LIMIT 128"),
    ],
)
def test_validator_enforces_limit(raw_sql, expected):
    sql = _validator().validate_and_patch(raw_sql).sql

    assert expected in sql


def test_validator_adds_top_level_limit_when_literal_contains_limit_text():
    sql = _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' AND chunk_data #>> '{dept}' = 'limit 5'").sql

    assert "'limit 5'" in sql
    assert sql.endswith("LIMIT 128")


def test_validator_adds_top_level_limit_when_subquery_contains_limit():
    sql = _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' AND EXISTS (SELECT 1 LIMIT 1)").sql

    assert "SELECT 1 LIMIT 1" in sql
    assert sql.endswith("LIMIT 128")


def test_validator_caps_top_level_fetch_first():
    sql = _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' FETCH FIRST 500 ROWS ONLY").sql

    assert "FETCH FIRST 128 ROWS ONLY" in sql


@pytest.mark.parametrize("literal", ["limit 5", "order by dept", "fetch next 5 rows"])
def test_validator_injects_kb_id_when_literal_contains_clause_keywords(literal):
    sql = _validator().validate_and_patch(f"SELECT doc_id FROM ragflow_tenant WHERE chunk_data #>> '{{dept}}' = '{literal}' ORDER BY doc_id").sql

    assert f"'{literal}'" in sql
    assert "AND kb_id = 'kb1'" in sql
    assert "ORDER BY doc_id" in sql
    assert sql.endswith("LIMIT 128")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "UPDATE ragflow_tenant SET kb_id = 'kb2'",
        "DROP TABLE ragflow_tenant",
        "SELECT * FROM users",
        "SELECT * FROM ragflow_tenant",
        "SELECT pg_sleep(10) FROM ragflow_tenant",
        "SELECT now() FROM ragflow_tenant",
        "SELECT CURRENT_USER FROM ragflow_tenant",
        "SELECT CURRENT_DATE FROM ragflow_tenant",
        "SELECT CURRENT_DATABASE() FROM ragflow_tenant",
        "SELECT CURRENT_CATALOG FROM ragflow_tenant",
        "SELECT md5(doc_id) FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT random() FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT row_number() OVER (ORDER BY doc_id) FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT json_extract_string(chunk_data, '$.amount') FROM ragflow_tenant",
        "SELECT chunk_data ->> 'amount' FROM ragflow_tenant",
        "SELECT chunk_data['amount'] FROM ragflow_tenant",
        "SELECT chunk_data @> '{\"amount\":1}'::jsonb FROM ragflow_tenant",
        "SELECT chunk_data ? 'amount' FROM ragflow_tenant",
        "SELECT chunk_data #- '{amount}' FROM ragflow_tenant",
        "SELECT chunk_data #>> '{unknown}' FROM ragflow_tenant",
        "SELECT amount FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT ragflow_tenant.amount FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT content_with_weight FROM ragflow_tenant",
        "SELECT doc_id FROM ragflow_tenant; SELECT * FROM users",
        "WITH x AS (DELETE FROM ragflow_tenant RETURNING *) SELECT * FROM x",
        "SELECT 1",
        "SELECT COUNT(*) AS rows",
    ],
)
def test_validator_rejects_unsafe_sql(sql):
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch(sql)


def test_validator_rejects_cross_kb_predicate():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'other-kb'")


def test_validator_rejects_or_predicate_that_can_bypass_kb_scope():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' OR 1=1")


def test_validator_rejects_join_even_when_one_side_has_kb_scope():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("SELECT a.doc_id FROM ragflow_tenant a JOIN ragflow_tenant b ON a.doc_id = b.doc_id WHERE a.kb_id = 'kb1'")


def test_validator_rejects_cte_that_filters_synthetic_kb_id_only():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("WITH rows AS (SELECT doc_id, 'kb1' AS kb_id FROM ragflow_tenant) SELECT doc_id FROM rows WHERE kb_id = 'kb1'")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT doc_id FROM ragflow_tenant WHERE NOT kb_id = 'kb1'",
        "SELECT doc_id FROM ragflow_tenant WHERE (kb_id = 'kb1') IS FALSE",
        "SELECT doc_id FROM ragflow_tenant WHERE CASE WHEN kb_id = 'kb1' THEN TRUE ELSE TRUE END",
    ],
)
def test_validator_rejects_non_positive_kb_predicates(sql):
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch(sql)


def test_validator_allows_count_star_with_kb_injection():
    sql = _validator().validate_and_patch("SELECT COUNT(*) AS rows FROM ragflow_tenant").sql

    assert "COUNT(*)" in sql
    assert "kb_id = 'kb1'" in sql
    assert "LIMIT 128" in sql


def test_validator_allows_safe_literal_that_contains_forbidden_keyword():
    sql = _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' AND chunk_data #>> '{dept}' = 'update'").sql

    assert "'update'" in sql


def test_validator_allows_readonly_cte_with_kb_boundary():
    sql = _validator().validate_and_patch("WITH rows AS (SELECT doc_id, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1') SELECT doc_id FROM rows").sql

    assert sql.startswith("WITH rows AS")
    assert "LIMIT 128" in sql


def test_validator_allows_cte_output_alias_from_jsonb_path():
    sql = _validator().validate_and_patch("WITH rows AS (SELECT chunk_data #>> '{amount}' AS amount, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1') SELECT amount FROM rows").sql

    assert sql.startswith("WITH rows AS")
    assert "SELECT amount FROM rows" in sql
