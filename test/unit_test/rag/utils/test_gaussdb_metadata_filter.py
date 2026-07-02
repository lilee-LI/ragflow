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

from common import metadata_gaussdb_filter as gaussdb_filter
from common.metadata_gaussdb_filter import (
    GaussDBMetaFilterTranslator,
    SUPPORTED_OPERATORS,
    UnsupportedGaussDBMetaFilter,
    build_gaussdb_filter,
    build_gaussdb_meta_filter_where,
    extract_doc_ids,
    is_pushdown_supported,
    jsonb_path_literal,
    normalize_gaussdb_meta_operator,
)

OP_NE = "\u2260"
OP_GE = "\u2265"
OP_LE = "\u2264"


@pytest.mark.parametrize(
    ("flt", "sql_part"),
    [
        ({"key": "author", "op": "=", "value": "Alice"}, "lower(meta_fields #>> '{author}') ="),
        ({"key": "author", "op": OP_NE, "value": "Alice"}, "IS NOT TRUE"),
        ({"key": "amount", "op": ">", "value": 100}, "::DOUBLE PRECISION >"),
        ({"key": "amount", "op": OP_GE, "value": 100}, "::DOUBLE PRECISION >="),
        ({"key": "amount", "op": "<", "value": 200}, "::DOUBLE PRECISION <"),
        ({"key": "date", "op": OP_LE, "value": "2026-06-26"}, "to_date"),
        ({"key": "tags", "op": "contains", "value": "audit"}, "jsonb_exists"),
        ({"key": "tags", "op": "not contains", "value": "audit"}, "IS NOT TRUE"),
        ({"key": "title", "op": "start with", "value": "RAG"}, "LIKE"),
        ({"key": "title", "op": "end with", "value": "Flow"}, "LIKE"),
        ({"key": "status", "op": "empty", "value": None}, "NOT (meta_fields ? 'status')"),
        ({"key": "status", "op": "not empty", "value": None}, "meta_fields ? 'status'"),
        ({"key": "dept", "op": "in", "value": ["finance", "risk"]}, " OR "),
        ({"key": "dept", "op": "not in", "value": ["finance", "risk"]}, " AND "),
    ],
)
def test_supported_operators_translate_to_jsonb_predicates(flt, sql_part):
    translated = build_gaussdb_meta_filter_where([flt], "and")

    assert sql_part in translated.sql
    assert "%s" in translated.sql or flt["op"] in ("empty", "not empty", "=")
    assert isinstance(translated.params, list)


def test_empty_string_uses_jsonb_empty_string_not_sql_empty_string():
    translated = build_gaussdb_meta_filter_where([{"key": "note", "op": "=", "value": ""}], "and")

    assert "'\"\"'::jsonb" in translated.sql
    assert "= ''" not in translated.sql
    assert translated.params == []


def test_json_null_is_distinct_from_key_missing_for_equality():
    translated = build_gaussdb_meta_filter_where([{"key": "reviewer", "op": "=", "value": None}], "and")

    assert "meta_fields ? 'reviewer'" in translated.sql
    assert "'null'::jsonb" in translated.sql
    assert translated.params == []


def test_empty_and_not_empty_cover_missing_null_empty_string_array_and_object():
    empty_sql = build_gaussdb_meta_filter_where([{"key": "status", "op": "empty", "value": None}], "and").sql
    not_empty_sql = build_gaussdb_meta_filter_where([{"key": "status", "op": "not empty", "value": None}], "and").sql

    assert "NOT (meta_fields ? 'status')" in empty_sql
    assert "'null'::jsonb" in empty_sql
    assert "'\"\"'::jsonb" in empty_sql
    assert "'[]'::jsonb" in empty_sql
    assert "'{}'::jsonb" in empty_sql
    assert "meta_fields ? 'status'" in not_empty_sql
    assert "'null'::jsonb" in not_empty_sql
    assert "'\"\"'::jsonb" in not_empty_sql
    assert "'[]'::jsonb" in not_empty_sql
    assert "'{}'::jsonb" in not_empty_sql


def test_or_logic_preserves_parentheses():
    translated = build_gaussdb_meta_filter_where(
        [
            {"key": "author", "op": "=", "value": "Alice"},
            {"key": "department", "op": "=", "value": "finance"},
        ],
        "or",
    )

    assert ") OR (" in translated.sql
    assert translated.params == ["alice", "alice", "finance", "finance"]


def test_rejects_unsupported_before_after_operators():
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([{"key": "date", "op": "before", "value": "2026-01-01"}], "and")

    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([{"key": "date", "op": "after", "value": "2026-01-01"}], "and")


def test_supported_operator_set_matches_frontend_contract():
    assert SUPPORTED_OPERATORS == {
        "=",
        OP_NE,
        ">",
        OP_GE,
        "<",
        OP_LE,
        "in",
        "not in",
        "contains",
        "not contains",
        "start with",
        "end with",
        "empty",
        "not empty",
    }


@pytest.mark.parametrize(
    ("input_op", "canonical"),
    [
        ("is", "="),
        ("is not", OP_NE),
        ("not is", OP_NE),
        ("!=", OP_NE),
        ("<>", OP_NE),
        (">=", OP_GE),
        ("<=", OP_LE),
        (OP_NE, OP_NE),
        (OP_GE, OP_GE),
        (OP_LE, OP_LE),
    ],
)
def test_operator_aliases_normalize_to_canonical_frontend_operators(input_op, canonical):
    assert normalize_gaussdb_meta_operator(input_op) == canonical


def test_empty_filter_list_returns_tautology():
    translated = build_gaussdb_meta_filter_where([], "and")

    assert translated.sql == "1=1"
    assert translated.params == []


def test_unknown_logic_is_rejected():
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([{"key": "author", "op": "=", "value": "Alice"}], "xor")


def test_missing_or_unknown_operator_is_rejected():
    with pytest.raises(UnsupportedGaussDBMetaFilter, match="missing"):
        normalize_gaussdb_meta_operator(None)

    with pytest.raises(UnsupportedGaussDBMetaFilter, match="unsupported"):
        build_gaussdb_meta_filter_where([{"key": "author", "op": "around", "value": "Alice"}], "and")


def test_invalid_key_format_is_rejected():
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([{"key": "author;drop table x", "op": "=", "value": "Alice"}], "and")


@pytest.mark.parametrize("key", ["", None, "vendor..name"])
def test_missing_or_empty_key_is_rejected(key):
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([{"key": key, "op": "=", "value": "Alice"}], "and")


def test_membership_accepts_csv_python_list_and_numeric_members():
    csv_result = build_gaussdb_meta_filter_where([{"key": "status", "op": "in", "value": "Open,Closed"}], "and")
    list_result = build_gaussdb_meta_filter_where([{"key": "status", "op": "in", "value": "['Open', 'Closed']"}], "and")
    numeric_result = build_gaussdb_meta_filter_where([{"key": "year", "op": "in", "value": "[2024, 2025]"}], "and")

    assert "jsonb_exists" in csv_result.sql
    assert "jsonb_exists" in list_result.sql
    assert "@> %s::jsonb" in numeric_result.sql
    assert len(csv_result.params) == 4
    assert len(list_result.params) == 4
    assert len(numeric_result.params) == 2


def test_string_equality_matches_scalar_text_or_jsonb_array_member():
    translated = build_gaussdb_meta_filter_where([{"key": "tags", "op": "=", "value": "audit"}], "and")

    assert "lower(meta_fields #>> '{tags}') =" in translated.sql
    assert "jsonb_exists(meta_fields #> '{tags}', %s)" in translated.sql
    assert translated.params == ["audit", "audit"]


def test_not_contains_requires_existing_key():
    translated = build_gaussdb_meta_filter_where([{"key": "tags", "op": "not contains", "value": "audit"}], "and")

    assert "meta_fields ? 'tags'" in translated.sql
    assert "NOT (meta_fields ? 'tags')" not in translated.sql
    assert translated.sql.startswith("(meta_fields ? 'tags' AND")


def test_nested_empty_treats_missing_parent_as_missing_key():
    translated = build_gaussdb_meta_filter_where([{"key": "vendor.name", "op": "empty", "value": None}], "and")

    assert "((meta_fields #> '{vendor}') ? 'name') IS NOT TRUE" in translated.sql


def test_unsafe_range_string_is_not_pushdown_supported():
    assert not is_pushdown_supported([{"key": "amount", "op": ">", "value": "not-a-number"}])


@pytest.mark.parametrize(
    "flt",
    [
        {"key": "author", "op": "=", "value": ["Alice"]},
        {"key": "amount", "op": ">", "value": None},
        {"key": "amount", "op": ">", "value": True},
        {"key": "amount", "op": ">", "value": "not-a-number"},
        {"key": "title", "op": "contains", "value": None},
        {"key": "title", "op": "contains", "value": []},
        {"key": "dept", "op": "in", "value": None},
        {"key": "dept", "op": "in", "value": []},
    ],
)
def test_invalid_metadata_filter_values_are_rejected(flt):
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([flt], "and")


def test_bool_and_numeric_string_pairwise_values_have_stable_jsonb_semantics():
    bool_result = build_gaussdb_meta_filter_where([{"key": "active", "op": "=", "value": "True"}], "and")
    number_result = build_gaussdb_meta_filter_where([{"key": "amount", "op": ">", "value": "120"}], "and")
    decimal_result = build_gaussdb_meta_filter_where([{"key": "amount", "op": ">", "value": "120.5"}], "and")
    regex_number_result = build_gaussdb_meta_filter_where([{"key": "amount", "op": ">", "value": "01"}], "and")
    list_like_scalar = build_gaussdb_meta_filter_where([{"key": "amounts", "op": "=", "value": "[1]"}], "and")
    scalar_membership = build_gaussdb_meta_filter_where([{"key": "amount", "op": "in", "value": 7}], "and")

    assert "@> %s::jsonb" in bool_result.sql
    assert bool_result.params == ["true"]
    assert "::DOUBLE PRECISION > %s" in number_result.sql
    assert number_result.params == [120]
    assert decimal_result.params == [120.5]
    assert regex_number_result.params == [1]
    assert list_like_scalar.params == ["[1]", "[1]"]
    assert scalar_membership.params == ["7"]


def test_unknown_internal_metadata_operator_is_rejected(monkeypatch):
    monkeypatch.setitem(gaussdb_filter._CANONICAL_TO_INTERNAL, "=", "unknown_internal")

    with pytest.raises(UnsupportedGaussDBMetaFilter, match="no handler"):
        GaussDBMetaFilterTranslator().translate({"key": "author", "op": "=", "value": "Alice"})


def test_custom_jsonb_column_must_be_plain_identifier():
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_filter([{"key": "author", "op": "=", "value": "Alice"}], "and", jsonb_column="meta-fields")


def test_string_operator_empty_value_is_rejected():
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        build_gaussdb_meta_filter_where([{"key": "title", "op": "contains", "value": ""}], "and")


def test_like_pattern_escapes_percent_underscore_and_backslash():
    translated = build_gaussdb_meta_filter_where([{"key": "title", "op": "contains", "value": r"a%b_c\\d"}], "and")

    assert "LIKE" in translated.sql
    assert "ESCAPE '\\'" in translated.sql
    assert translated.params == [r"a%b_c\\d", r"%a\%b\_c\\\\d%"]


def test_jsonb_path_encodes_nested_keys():
    translated = build_gaussdb_meta_filter_where([{"key": "vendor.name", "op": "=", "value": "Acme"}], "and")

    assert "#>> '{vendor,name}'" in translated.sql
    assert translated.params == ["acme", "acme"]


def test_jsonb_path_rejects_unsafe_segments():
    with pytest.raises(UnsupportedGaussDBMetaFilter):
        jsonb_path_literal("vendor.bad,key")

    with pytest.raises(UnsupportedGaussDBMetaFilter):
        jsonb_path_literal("vendor..name")


def test_build_gaussdb_filter_returns_sql_and_params_tuple():
    sql, params = build_gaussdb_filter([{"key": "author", "op": "=", "value": "Alice"}], "and")

    assert "lower(meta_fields #>> '{author}') =" in sql
    assert params == ["alice", "alice"]


def test_is_pushdown_supported_checks_full_translation_not_just_operator():
    assert is_pushdown_supported([{"key": "author", "op": "=", "value": "Alice"}])
    assert not is_pushdown_supported([{"key": "author;drop", "op": "=", "value": "Alice"}])
    assert not is_pushdown_supported([{"key": "date", "op": "before", "value": "2026-01-01"}])


def test_extract_doc_ids_accepts_tuple_list_and_dict_rows():
    rows = [
        ("doc_1",),
        ["doc_2"],
        {"id": "doc_3"},
        {"doc_id": "doc_4"},
        {"id": None},
        [],
    ]

    assert extract_doc_ids(rows) == ["doc_1", "doc_2", "doc_3", "doc_4"]
