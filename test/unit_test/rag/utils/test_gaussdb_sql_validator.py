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
import builtins

import pytest

from common.doc_store import gaussdb_conn_base as gaussdb_base
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


def test_jsonb_path_literal_rejects_empty_path_or_segment():
    with pytest.raises(UnsafeGaussDBSQL, match="empty JSONB path"):
        jsonb_path_literal([])

    with pytest.raises(UnsafeGaussDBSQL, match="segment"):
        jsonb_path_literal(["amount", ""])


def test_jsonb_path_literal_parser_handles_quotes_escapes_and_rejects_bad_shapes():
    assert gaussdb_base._parse_jsonb_path_literal("'{\"customer,name\",dept}'") == ("customer,name", "dept")
    assert gaussdb_base._parse_jsonb_path_literal("'{\"customer\\\\name\"}'") == ("customer\\name",)

    for literal in ["amount", "'{amount'", "'{amount,}'", "'{\"amount}'"]:
        with pytest.raises(UnsafeGaussDBSQL):
            gaussdb_base._parse_jsonb_path_literal(literal)


def test_exposed_table_ignores_empty_metadata_field_paths():
    table = ExposedGaussDBTable.from_field_map(
        physical_name="ragflow_tenant",
        kb_ids=["kb1"],
        field_map={"": "ignored", "dept": "string"},
    )

    assert "" not in table.json_fields
    assert table.json_fields["dept"] == ("dept",)


def test_validator_accepts_select_with_jsonb_path_and_kb_id():
    sql = _validator().validate_and_patch(
        "SELECT doc_id, docnm_kwd, chunk_data #>> '{amount}' AS amount "
        "FROM ragflow_tenant WHERE kb_id = 'kb1'"
    ).sql

    assert "SELECT" in sql
    assert "chunk_data #>> '{amount}'" in sql
    assert "LIMIT 128" in sql


def test_validator_rejects_jsonb_path_when_field_map_empty():
    table = ExposedGaussDBTable.from_field_map(
        physical_name="ragflow_tenant",
        kb_ids=["kb1"],
        field_map={},
    )
    validator = GaussDBSQLValidator({table.logical_name: table}, default_limit=128)

    with pytest.raises(UnsafeGaussDBSQL):
        validator.validate_and_patch(
            "SELECT doc_id, chunk_data #>> '{amount}' AS amount "
            "FROM ragflow_tenant WHERE kb_id = 'kb1'"
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT doc_id, chunk_data #>> path_col FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT doc_id, other_data #>> '{amount}' FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT doc_id, chunk_data #>> '{amount,}' FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT doc_id, chunk_data #>> '{customer,name}' FROM ragflow_tenant WHERE kb_id = 'kb1'",
    ],
)
def test_validator_rejects_dynamic_wrong_source_or_unexposed_jsonb_paths(sql):
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch(sql)


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
    sql = _validator().validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' AND chunk_data #>> '{dept}' = 'limit 5'"
    ).sql

    assert "'limit 5'" in sql
    assert sql.endswith("LIMIT 128")


def test_validator_adds_top_level_limit_when_subquery_contains_limit():
    sql = _validator().validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' AND EXISTS (SELECT 1 LIMIT 1)"
    ).sql

    assert "SELECT 1 LIMIT 1" in sql
    assert sql.endswith("LIMIT 128")


def test_validator_caps_top_level_fetch_first():
    sql = _validator().validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' FETCH FIRST 500 ROWS ONLY"
    ).sql

    assert "FETCH FIRST 128 ROWS ONLY" in sql


@pytest.mark.parametrize("literal", ["limit 5", "order by dept", "fetch next 5 rows"])
def test_validator_injects_kb_id_when_literal_contains_clause_keywords(literal):
    sql = _validator().validate_and_patch(
        f"SELECT doc_id FROM ragflow_tenant WHERE chunk_data #>> '{{dept}}' = '{literal}' ORDER BY doc_id"
    ).sql

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
        "SELECT x.value FROM ragflow_tenant CROSS JOIN LATERAL jsonb_array_elements(chunk_data #> '{tags}') AS x(value) WHERE kb_id = 'kb1'",
        "SELECT jsonb_path_query(chunk_data, '$.tags') FROM ragflow_tenant WHERE kb_id = 'kb1'",
        "SELECT x.amount FROM ragflow_tenant, jsonb_to_record(chunk_data) AS x(amount text) WHERE kb_id = 'kb1'",
        "SELECT x.amount FROM ragflow_tenant, jsonb_to_recordset(chunk_data) AS x(amount text) WHERE kb_id = 'kb1'",
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


@pytest.mark.parametrize("sql", ["", "```sql\nSELECT doc_id FROM ragflow_tenant\n```", "VALUES (1)", "SELECT FROM"])
def test_validator_normalizes_code_fences_and_rejects_empty_non_select_or_parse_errors(sql):
    validator = _validator()
    if sql.startswith("```"):
        assert "SELECT doc_id" in validator.validate_and_patch(sql).sql
    else:
        with pytest.raises(UnsafeGaussDBSQL):
            validator.validate_and_patch(sql)


def test_validator_select_columns_ignores_unaliased_expression():
    validated = _validator().validate_and_patch("SELECT 1, doc_id FROM ragflow_tenant WHERE kb_id = 'kb1'")

    assert validated.columns == ["1", "doc_id"]


def test_validator_select_columns_skips_expression_without_alias():
    class ExpressionWithoutAlias:
        alias_or_name = None

    ast = type("Ast", (), {"expressions": [ExpressionWithoutAlias()]})()

    assert _validator()._select_columns(ast) == []


def test_validator_rejects_cross_kb_predicate():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'other-kb'")


def test_validator_rejects_or_predicate_that_can_bypass_kb_scope():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' OR 1=1")


def test_validator_rejects_join_even_when_one_side_has_kb_scope():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch(
            "SELECT a.doc_id FROM ragflow_tenant a JOIN ragflow_tenant b ON a.doc_id = b.doc_id WHERE a.kb_id = 'kb1'"
        )


def test_validator_rejects_cte_that_filters_synthetic_kb_id_only():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch(
            "WITH rows AS (SELECT doc_id, 'kb1' AS kb_id FROM ragflow_tenant) SELECT doc_id FROM rows WHERE kb_id = 'kb1'"
        )


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


def test_validator_accepts_right_hand_kb_equality_and_allowed_in_boundary():
    equality_sql = _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE 'kb1' = kb_id").sql
    in_sql = _validator(kb_ids=["kb1", "kb2"]).validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id IN ('kb1', 'kb2')").sql

    assert "'kb1' = kb_id" in equality_sql
    assert "kb_id IN ('kb1', 'kb2')" in in_sql


def test_validator_rejects_dynamic_in_boundary_and_missing_required_kbs():
    with pytest.raises(UnsafeGaussDBSQL, match="static"):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id IN (doc_id)")

    table = ExposedGaussDBTable.from_field_map("ragflow_tenant", [], {})
    validator = GaussDBSQLValidator({"ragflow_tenant": table})
    with pytest.raises(UnsafeGaussDBSQL, match="boundary"):
        validator.validate_and_patch("SELECT doc_id FROM ragflow_tenant")


def test_validator_allows_non_kb_predicates_before_static_kb_boundary():
    sql = _validator(kb_ids=["kb1", "kb2"]).validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant "
        "WHERE doc_id IN ('doc1', 'doc2') AND kb_id IN ('kb1')"
    ).sql

    assert "doc_id IN ('doc1', 'doc2')" in sql
    assert "kb_id IN ('kb1')" in sql


def test_validator_rejects_unknown_table_before_execution():
    with pytest.raises(UnsafeGaussDBSQL, match="not allowed"):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_other WHERE kb_id = 'kb1'")


def test_validator_rejects_unscoped_join_when_kb_boundary_cannot_be_injected():
    with pytest.raises(UnsafeGaussDBSQL, match="complex|exactly one base table"):
        _validator().validate_and_patch(
            "SELECT a.doc_id FROM ragflow_tenant a JOIN ragflow_tenant b ON a.doc_id = b.doc_id"
        )


def test_validator_allows_safe_literal_that_contains_forbidden_keyword():
    sql = _validator().validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' AND chunk_data #>> '{dept}' = 'update'"
    ).sql

    assert "'update'" in sql


def test_validator_allows_readonly_cte_with_kb_boundary():
    sql = _validator().validate_and_patch(
        "WITH rows AS (SELECT doc_id, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1') "
        "SELECT doc_id FROM rows"
    ).sql

    assert sql.startswith("WITH rows AS")
    assert "LIMIT 128" in sql


def test_validator_allows_cte_output_alias_from_jsonb_path():
    sql = _validator().validate_and_patch(
        "WITH rows AS ("
        "SELECT chunk_data #>> '{amount}' AS amount, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1'"
        ") SELECT amount FROM rows"
    ).sql

    assert sql.startswith("WITH rows AS")
    assert "SELECT amount FROM rows" in sql


def test_validator_allows_qualified_cte_output_column():
    sql = _validator().validate_and_patch(
        "WITH rows AS ("
        "SELECT chunk_data #>> '{amount}' AS amount, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1'"
        ") SELECT rows.amount FROM rows"
    ).sql

    assert "SELECT rows.amount FROM rows" in sql


def test_validator_rejects_cte_join_output_columns_as_complex_boundary():
    with pytest.raises(UnsafeGaussDBSQL, match="complex"):
        _validator().validate_and_patch(
            "WITH a AS (SELECT doc_id, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1'), "
            "b AS (SELECT doc_id, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1') "
            "SELECT a.doc_id FROM a JOIN b ON a.doc_id = b.doc_id"
        )


def test_validator_accepts_to_date_on_exposed_jsonb_field():
    sql = _validator().validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant "
        "WHERE kb_id = 'kb1' AND to_date(chunk_data #>> '{amount}', 'YYYY-MM-DD') > to_date('2026-01-01', 'YYYY-MM-DD')"
    ).sql

    assert "TO_DATE" in sql.upper()
    assert "LIMIT 128" in sql


def test_validator_allows_select_alias_in_group_by_and_order_by():
    sql = _validator().validate_and_patch(
        "SELECT chunk_data #>> '{dept}' AS dept, COUNT(*) AS cnt "
        "FROM ragflow_tenant WHERE kb_id = 'kb1' GROUP BY dept ORDER BY dept"
    ).sql

    assert "GROUP BY dept" in sql
    assert "ORDER BY dept" in sql


def test_runtime_readonly_guard_allows_select_alias_in_group_by_and_order_by():
    sql = GaussDBSQLValidator.readonly_guard().validate_and_patch(
        "SELECT chunk_data #>> '{dept}' AS dept, COUNT(*) AS cnt "
        "FROM ragflow_tenant WHERE kb_id = 'kb1' GROUP BY dept ORDER BY dept"
    ).sql

    assert "GROUP BY dept" in sql
    assert "ORDER BY dept" in sql


def test_validator_can_disable_limit_for_internal_validation():
    validator = GaussDBSQLValidator({"ragflow_tenant"}, kb_ids=["kb1"], default_limit=0)
    validator.default_limit = 0

    sql = validator.validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1'").sql

    assert "LIMIT" not in sql


def test_validator_rejects_empty_or_unsafe_kb_literals():
    with pytest.raises(UnsafeGaussDBSQL):
        _validator().validate_and_patch("SELECT doc_id FROM ragflow_tenant WHERE kb_id IN ()")

    with pytest.raises(UnsafeGaussDBSQL, match="unsafe"):
        _validator(kb_ids=["kb1;drop"]).validate_and_patch("SELECT doc_id FROM ragflow_tenant")


def test_validator_extracts_kb_ids_from_sql_literals():
    validator = _validator(kb_ids=["kb1", "kb2"])

    assert validator._extract_kb_ids("WHERE kb_id = 'kb1' OR kb_id IN ('kb2')") == ["kb1", "kb2"]


def test_runtime_readonly_guard_rejects_queries_without_docengine_table():
    validator = GaussDBSQLValidator.readonly_guard()

    with pytest.raises(UnsafeGaussDBSQL, match="DocEngine table"):
        validator.validate_and_patch("SELECT 1")


def test_runtime_readonly_guard_allows_cte_output_aliases():
    sql = GaussDBSQLValidator.readonly_guard().validate_and_patch(
        "WITH rows AS ("
        "SELECT chunk_data #>> '{amount}' AS amount, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1'"
        ") SELECT amount FROM rows"
    ).sql

    assert "SELECT amount FROM rows" in sql


def test_runtime_readonly_guard_skips_non_kb_predicate_before_boundary():
    sql = GaussDBSQLValidator.readonly_guard().validate_and_patch(
        "SELECT doc_id FROM ragflow_tenant WHERE doc_id = 'doc1' AND kb_id = 'kb1'"
    ).sql

    assert "doc_id = 'doc1'" in sql


def test_static_boundary_helper_rejects_empty_or_missing_top_level_kb_boundary():
    validator = _validator()

    missing_ast = validator._parse_one("SELECT doc_id FROM ragflow_tenant WHERE doc_id = 'doc1'")
    assert validator._select_has_static_kb_boundary(missing_ast) is False

    non_comparison_ast = validator._parse_one("SELECT doc_id FROM ragflow_tenant WHERE doc_id IS NOT NULL")
    assert validator._select_has_static_kb_boundary(non_comparison_ast) is False

    empty_ast = validator._parse_one("SELECT doc_id FROM ragflow_tenant WHERE kb_id IN ()")
    with pytest.raises(UnsafeGaussDBSQL, match="empty"):
        validator._select_has_static_kb_boundary(empty_ast)


def test_sql_guard_internal_rejection_edges(monkeypatch):
    validator = _validator()

    with pytest.raises(UnsafeGaussDBSQL, match="exactly one"):
        validator._parse_one("SELECT 1; SELECT 2")

    original_import = builtins.__import__

    def block_sqlglot_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sqlglot" or name.startswith("sqlglot."):
            raise ImportError("blocked sqlglot")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", block_sqlglot_import)
    with pytest.raises(UnsafeGaussDBSQL, match="sqlglot is required"):
        validator._parse_one("SELECT 1")
    monkeypatch.setattr(builtins, "__import__", original_import)

    with pytest.raises(UnsafeGaussDBSQL, match="exactly one base table"):
        validator._enforce_kb_boundary("SELECT doc_id FROM ragflow_tenant, ragflow_other")

    wrong_source_ast = validator._parse_one("SELECT other_data #>> '{amount}' FROM ragflow_tenant WHERE kb_id = 'kb1'")
    with pytest.raises(UnsafeGaussDBSQL, match="chunk_data"):
        validator._validate_jsonb_paths(wrong_source_ast)

    dynamic_path_ast = validator._parse_one("SELECT chunk_data #>> ARRAY['amount'] FROM ragflow_tenant WHERE kb_id = 'kb1'")
    with pytest.raises(UnsafeGaussDBSQL, match="dynamic"):
        validator._validate_jsonb_paths(dynamic_path_ast)

    from sqlglot import exp

    cte_query = exp.Select()
    cte_query.set("expressions", [type("ExpressionWithoutAlias", (), {"alias_or_name": None})(), exp.column("doc_id")])
    fake_cte = type("Cte", (), {"alias_or_name": "rows", "this": cte_query})()
    fake_non_select_cte = type("Cte", (), {"alias_or_name": "ignored", "this": object()})()
    fake_ast = type("Ast", (), {"find_all": lambda self, _node_type: [fake_non_select_cte, fake_cte]})()
    assert validator._cte_output_columns(fake_ast) == {"rows": {"doc_id"}}

    fake_column = type("Column", (), {"name": "doc_id", "table": "", "parent": object()})()
    assert validator._is_cte_output_column(fake_column, {}) is False

    ast = validator._parse_one("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1'")
    monkeypatch.delattr(exp, "JSONBExtractScalar", raising=False)
    monkeypatch.delattr(exp, "JSONBExtract", raising=False)
    validator._validate_jsonb_paths(ast)

    cte_join_ast = validator._parse_one(
        "WITH rows AS (SELECT doc_id, kb_id FROM ragflow_tenant WHERE kb_id = 'kb1') "
        "SELECT t.doc_id FROM ragflow_tenant t JOIN rows r ON t.doc_id = r.doc_id WHERE t.kb_id = 'kb1'"
    )
    assert validator._direct_base_tables(cte_join_ast) == ["ragflow_tenant"]
    assert validator._direct_source_tables(cte_join_ast) == ["ragflow_tenant", "rows"]

    join_ast = validator._parse_one(
        "SELECT a.doc_id FROM ragflow_tenant a JOIN ragflow_other b ON a.doc_id = b.doc_id WHERE a.kb_id = 'kb1'"
    )
    assert validator._direct_source_tables(join_ast) == ["ragflow_tenant", "ragflow_other"]

    subquery_join_ast = validator._parse_one(
        "SELECT a.doc_id FROM ragflow_tenant a "
        "JOIN (SELECT doc_id FROM ragflow_other) b ON a.doc_id = b.doc_id WHERE a.kb_id = 'kb1'"
    )
    assert validator._direct_source_tables(subquery_join_ast) == ["ragflow_tenant"]

    original_parse_one = validator._parse_one
    invalid_limit_ast = original_parse_one("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' LIMIT 1")
    invalid_limit_ast.args["limit"].set("expression", exp.Literal(this="bad", is_string=False))
    monkeypatch.setattr(validator, "_parse_one", lambda _sql: invalid_limit_ast)
    assert "LIMIT 128" in validator._enforce_limit("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' LIMIT bad")

    dynamic_limit_ast = original_parse_one("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' LIMIT 1")
    dynamic_limit_ast.args["limit"].set("expression", exp.column("dynamic_limit"))
    monkeypatch.setattr(validator, "_parse_one", lambda _sql: dynamic_limit_ast)
    assert "LIMIT 128" in validator._enforce_limit("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1' LIMIT dynamic_limit")

    monkeypatch.setattr(validator, "_parse_one", original_parse_one)
    no_from_ast = validator._parse_one("SELECT 1")
    assert validator._direct_source_tables(no_from_ast) == []
