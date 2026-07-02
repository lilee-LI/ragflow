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
"""Translate RAGFlow document-metadata filters into GaussDB JSONB SQL predicates."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

OP_NE = "\u2260"
OP_GE = "\u2265"
OP_LE = "\u2264"

SUPPORTED_OPERATORS: frozenset[str] = frozenset(
    {
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
)

_CANONICAL_TO_INTERNAL = {
    "=": "eq",
    OP_NE: "ne",
    ">": "gt",
    OP_GE: "gte",
    "<": "lt",
    OP_LE: "lte",
    "in": "in",
    "not in": "not_in",
    "contains": "contains",
    "not contains": "not_contains",
    "start with": "start_with",
    "end with": "end_with",
    "empty": "empty",
    "not empty": "not_empty",
}

_OP_ALIASES = {
    "is": "=",
    "is not": OP_NE,
    "not is": OP_NE,
    "!=": OP_NE,
    "<>": OP_NE,
    ">=": OP_GE,
    "<=": OP_LE,
}

_INTERNAL_RANGE_SQL = {
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER_RE = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_KEY_SEGMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsupportedGaussDBMetaFilter(ValueError):
    pass


@dataclass(frozen=True)
class TranslatedGaussDBMetaFilter:
    sql: str
    params: list[Any]


@dataclass(frozen=True)
class GaussDBFilterPlan:
    logic: str
    translated: list[TranslatedGaussDBMetaFilter]

    def to_predicate(self) -> TranslatedGaussDBMetaFilter:
        if not self.translated:
            return TranslatedGaussDBMetaFilter("1=1", [])
        joiner = " AND " if self.logic == "and" else " OR "
        sql = joiner.join(f"({item.sql})" for item in self.translated)
        params: list[Any] = []
        for item in self.translated:
            params.extend(item.params)
        return TranslatedGaussDBMetaFilter(sql, params)


class GaussDBMetaFilterTranslator:
    def __init__(self, jsonb_column: str = "meta_fields") -> None:
        self.jsonb_column = validate_jsonb_column(jsonb_column)

    def translate(self, flt: dict) -> TranslatedGaussDBMetaFilter:
        key = validate_meta_key(flt.get("key"))
        op = normalize_metadata_filter_op(flt)
        value = flt.get("value")

        if op == "empty":
            return TranslatedGaussDBMetaFilter(self._empty_predicate(key), [])
        if op == "not_empty":
            return TranslatedGaussDBMetaFilter(self._not_empty_predicate(key), [])
        if op == "eq":
            return self._translate_equal(key, value, flt)
        if op == "ne":
            return self._translate_not_equal(key, value, flt)
        if op in _INTERNAL_RANGE_SQL:
            return self._translate_range(key, op, value, flt)
        if op == "in":
            return self._translate_in(key, value, flt)
        if op == "not_in":
            return self._translate_not_in(key, value, flt)
        if op == "contains":
            return self._translate_contains(key, value, flt)
        if op == "not_contains":
            return self._translate_not_contains(key, value, flt)
        if op == "start_with":
            return self._translate_start_with(key, value, flt)
        if op == "end_with":
            return self._translate_end_with(key, value, flt)
        raise UnsupportedGaussDBMetaFilter(f"no handler for operator {op!r}")

    def _translate_equal(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        sql, params = self._equal_predicate(key, value, flt)
        return TranslatedGaussDBMetaFilter(sql, params)

    def _translate_not_equal(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        sql, params = self._equal_predicate(key, value, flt)
        return TranslatedGaussDBMetaFilter(f"{self._key_exists_expr(key)} AND ({sql}) IS NOT TRUE", params)

    def _translate_range(self, key: str, op: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        value_type, coerced = coerce_range_value(value, flt)
        text_expr = self._text_expr(key)
        sql_op = _INTERNAL_RANGE_SQL[op]
        if value_type == "date":
            return TranslatedGaussDBMetaFilter(
                (
                    f"{text_expr} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$' "
                    f"AND to_date({text_expr}, 'YYYY-MM-DD') {sql_op} to_date(%s, 'YYYY-MM-DD')"
                ),
                [coerced],
            )
        return TranslatedGaussDBMetaFilter(
            f"{text_expr} ~ '^-?[0-9]+(\\.[0-9]+)?$' AND ({text_expr})::DOUBLE PRECISION {sql_op} %s",
            [coerced],
        )

    def _translate_in(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        fragments, params = self._membership_predicates(key, value, flt)
        return TranslatedGaussDBMetaFilter(" OR ".join(f"({sql})" for sql in fragments), params)

    def _translate_not_in(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        fragments, params = self._membership_predicates(key, value, flt)
        sql = " AND ".join(f"({fragment}) IS NOT TRUE" for fragment in fragments)
        return TranslatedGaussDBMetaFilter(f"{self._key_exists_expr(key)} AND {sql}", params)

    def _translate_contains(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        text = coerce_string_value(value, flt)
        array_expr = f"jsonb_exists({self._value_expr(key)}, %s)"
        like_expr = f"lower({self._text_expr(key)}) LIKE %s ESCAPE '\\'"
        return TranslatedGaussDBMetaFilter(
            f"{array_expr} OR {like_expr}",
            [text.lower(), f"%{escape_like_pattern(text.lower())}%"],
        )

    def _translate_not_contains(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        positive = self._translate_contains(key, value, flt)
        return TranslatedGaussDBMetaFilter(
            f"{self._key_exists_expr(key)} AND ({positive.sql}) IS NOT TRUE",
            positive.params,
        )

    def _translate_start_with(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        text = coerce_string_value(value, flt).lower()
        return TranslatedGaussDBMetaFilter(
            f"lower({self._text_expr(key)}) LIKE %s ESCAPE '\\'",
            [f"{escape_like_pattern(text)}%"],
        )

    def _translate_end_with(self, key: str, value: Any, flt: dict) -> TranslatedGaussDBMetaFilter:
        text = coerce_string_value(value, flt).lower()
        return TranslatedGaussDBMetaFilter(
            f"lower({self._text_expr(key)}) LIKE %s ESCAPE '\\'",
            [f"%{escape_like_pattern(text)}"],
        )

    def _equal_predicate(self, key: str, value: Any, flt: dict) -> tuple[str, list[Any]]:
        value = coerce_scalar_value(value, flt)
        value_expr = self._value_expr(key)
        if value is None:
            return f"{self._key_exists_expr(key)} AND {value_expr} = 'null'::jsonb", []
        if isinstance(value, str):
            if value == "":
                return f"{value_expr} = '\"\"'::jsonb", []
            value = value.lower()
            return f"lower({self._text_expr(key)}) = %s OR jsonb_exists({value_expr}, %s)", [value, value]
        return f"{value_expr} @> %s::jsonb", [jsonb_param(value)]

    def _membership_predicates(self, key: str, value: Any, flt: dict) -> tuple[list[str], list[Any]]:
        fragments: list[str] = []
        params: list[Any] = []
        for member in coerce_membership_values(value, flt):
            sql, member_params = self._equal_predicate(key, member, flt)
            fragments.append(sql)
            params.extend(member_params)
        return fragments, params

    def _empty_predicate(self, key: str) -> str:
        value_expr = self._value_expr(key)
        return (
            f"{self._key_missing_expr(key)} OR "
            f"{value_expr} = 'null'::jsonb OR "
            f"{value_expr} = '\"\"'::jsonb OR "
            f"{value_expr} = '[]'::jsonb OR "
            f"{value_expr} = '{{}}'::jsonb"
        )

    def _not_empty_predicate(self, key: str) -> str:
        value_expr = self._value_expr(key)
        return (
            f"{self._key_exists_expr(key)} AND "
            f"({value_expr} = 'null'::jsonb) IS NOT TRUE AND "
            f"({value_expr} = '\"\"'::jsonb) IS NOT TRUE AND "
            f"({value_expr} = '[]'::jsonb) IS NOT TRUE AND "
            f"({value_expr} = '{{}}'::jsonb) IS NOT TRUE"
        )

    def _value_expr(self, key: str) -> str:
        return f"{self.jsonb_column} #> {jsonb_path_literal(key)}"

    def _text_expr(self, key: str) -> str:
        return f"{self.jsonb_column} #>> {jsonb_path_literal(key)}"

    def _key_exists_expr(self, key: str) -> str:
        segments = split_meta_key(key)
        if len(segments) == 1:
            return f"{self.jsonb_column} ? '{segments[0]}'"
        parent_path = jsonb_path_literal(".".join(segments[:-1]))
        return f"({self.jsonb_column} #> {parent_path}) ? '{segments[-1]}'"

    def _key_missing_expr(self, key: str) -> str:
        key_exists = self._key_exists_expr(key)
        if len(split_meta_key(key)) == 1:
            return f"NOT ({key_exists})"
        return f"({key_exists}) IS NOT TRUE"


def build_gaussdb_meta_filter_where(filters: Sequence[dict], logic: str = "and") -> TranslatedGaussDBMetaFilter:
    return plan_pushdown(filters, logic).to_predicate()


def build_gaussdb_filter(
    filters: Sequence[dict],
    logic: str,
    jsonb_column: str = "meta_fields",
) -> tuple[str, list[Any]]:
    translated = plan_pushdown(filters, logic, GaussDBMetaFilterTranslator(jsonb_column)).to_predicate()
    return translated.sql, translated.params


def plan_pushdown(
    filters: Sequence[dict],
    logic: str,
    translator: GaussDBMetaFilterTranslator | None = None,
) -> GaussDBFilterPlan:
    if logic not in {"and", "or"}:
        raise UnsupportedGaussDBMetaFilter(f"unknown logic {logic!r}")
    translator = translator or GaussDBMetaFilterTranslator()
    return GaussDBFilterPlan(logic=logic, translated=[translator.translate(flt) for flt in filters])


def is_pushdown_supported(filters: Sequence[dict]) -> bool:
    try:
        plan_pushdown(filters, "and")
        return True
    except UnsupportedGaussDBMetaFilter:
        return False


def extract_doc_ids(rows: Iterable[Any]) -> list[str]:
    ids: list[str] = []
    for row in rows or []:
        value = None
        if isinstance(row, dict):
            value = row.get("id", row.get("doc_id"))
        elif isinstance(row, (list, tuple)) and row:
            value = row[0]
        if value is not None:
            ids.append(str(value))
    return ids


def normalize_gaussdb_meta_operator(op: Any) -> str:
    if op is None:
        raise UnsupportedGaussDBMetaFilter("metadata filter operator is missing")
    normalized = " ".join(str(op).strip().lower().split())
    canonical = _OP_ALIASES.get(normalized, normalized)
    if canonical not in SUPPORTED_OPERATORS:
        raise UnsupportedGaussDBMetaFilter(f"unsupported metadata filter operator {op!r}")
    return canonical


def normalize_metadata_filter_op(flt: dict | Any) -> str:
    op = flt.get("op", flt.get("operator", flt.get("comparison_operator"))) if isinstance(flt, dict) else flt
    return _CANONICAL_TO_INTERNAL[normalize_gaussdb_meta_operator(op)]


def validate_jsonb_column(column: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(column or ""):
        raise UnsupportedGaussDBMetaFilter(f"invalid JSONB column {column!r}")
    return column


def validate_meta_key(key: Any) -> str:
    if not isinstance(key, str) or not key:
        raise UnsupportedGaussDBMetaFilter(f"invalid metadata key {key!r}")
    split_meta_key(key)
    return key


def split_meta_key(key: str) -> list[str]:
    segments = key.split(".")
    if not segments or any(not segment for segment in segments):
        raise UnsupportedGaussDBMetaFilter(f"invalid metadata key {key!r}")
    for segment in segments:
        if not _KEY_SEGMENT_RE.fullmatch(segment):
            raise UnsupportedGaussDBMetaFilter(f"invalid metadata key segment {segment!r}")
    return segments


def jsonb_path_literal(key: str) -> str:
    return "'{" + ",".join(split_meta_key(validate_meta_key(key))) + "}'"


def coerce_scalar_value(value: Any, flt: dict) -> Any:
    if isinstance(value, (list, dict, tuple)):
        raise UnsupportedGaussDBMetaFilter(f"scalar comparison value is non-scalar: {flt}")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return value
        try:
            parsed = ast.literal_eval(stripped)
        except Exception:
            return value
        if isinstance(parsed, (int, float, bool)) or parsed is None:
            return parsed
    return value


def coerce_range_value(value: Any, flt: dict) -> tuple[str, int | float | str]:
    if value is None:
        raise UnsupportedGaussDBMetaFilter(f"range comparison value is None: {flt}")
    if isinstance(value, bool):
        raise UnsupportedGaussDBMetaFilter(f"range comparison value is boolean: {flt}")
    if isinstance(value, (int, float)):
        return "number", value

    text = str(value).strip()
    if _DATE_RE.fullmatch(text):
        return "date", text

    try:
        parsed = ast.literal_eval(text)
    except Exception:
        parsed = None
    if isinstance(parsed, (int, float)) and not isinstance(parsed, bool):
        return "number", parsed
    if _NUMBER_RE.fullmatch(text):
        return ("number", float(text) if "." in text else int(text))
    raise UnsupportedGaussDBMetaFilter(f"unsupported range comparison value: {flt}")


def coerce_string_value(value: Any, flt: dict) -> str:
    if value is None or isinstance(value, (list, dict, tuple)):
        raise UnsupportedGaussDBMetaFilter(f"string operator value must be a scalar: {flt}")
    text = str(value)
    if not text:
        raise UnsupportedGaussDBMetaFilter(f"string operator value is empty: {flt}")
    return text


def coerce_membership_values(value: Any, flt: dict) -> list[Any]:
    if value is None:
        raise UnsupportedGaussDBMetaFilter(f"membership value is None: {flt}")
    if isinstance(value, (list, tuple)):
        members = list(value)
    elif isinstance(value, str):
        stripped = value.strip()
        try:
            parsed = ast.literal_eval(stripped)
        except Exception:
            parsed = value
        if isinstance(parsed, (list, tuple)):
            members = list(parsed)
        else:
            members = [item.strip() for item in value.split(",") if item.strip()]
    else:
        members = [value]
    if not members:
        raise UnsupportedGaussDBMetaFilter(f"membership value resolved to empty list: {flt}")
    return [coerce_scalar_value(member, flt) for member in members]


def jsonb_param(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def escape_like_pattern(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
