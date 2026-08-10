#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import evidence_dir
from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_runner_result import pair_exit_code

GROUP_ORDER = ("control", "experiment")
LONG_CONTENT_REQUEST_TIMEOUT_SECONDS = 900
EXECUTE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = evidence_dir("07_memory_store")
RAW_DIR = EVIDENCE_DIR / "raw"
PLAN_PATH = EXECUTE_DIR.parent / "gaussdb_test_plan" / "07_memory_store_e2e.md"


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MS = _load_module("fresh_06_memory_metadata.py", "fresh_07_memory_metadata_base")
BASE = MS.BASE
DD = MS.DD
AUTH = MS.AUTH
DB = MS.DB
for module in (MS, BASE, DD, AUTH, DB):
    module.EVIDENCE_DIR = EVIDENCE_DIR
    module.RAW_DIR = RAW_DIR


def _case_titles() -> dict[str, str]:
    pattern = re.compile(r"^### (TC-MS-\d{3}):\s*(.+)$", re.MULTILINE)
    result: dict[str, str] = {}
    for case_id, title in pattern.findall(PLAN_PATH.read_text(encoding="utf-8")):
        if case_id in result:
            raise ValueError(f"duplicate case id: {case_id}")
        result[case_id] = title.strip()
    if len(result) != 79:
        raise ValueError(f"expected 79 memory store cases, found {len(result)}")
    return result


CASE_TITLES = _case_titles()


def secondary_fixture_password(case_id: str) -> str:
    return f"Fresh-{case_id}-User-B@1234"


def document_id_matches(expected_id: str, returned_id: str, physical_ids: list[str]) -> bool:
    return returned_id == expected_id or physical_ids == [expected_id]


def group_result_shape_ok(result: dict[str, Any]) -> bool:
    return (
        result.get("status") in {"PASS", "FAIL", "BLOCKED"}
        and isinstance(result.get("steps"), list)
        and bool(result["steps"])
        and all(isinstance(step, dict) and step.get("name") for step in result["steps"])
        and isinstance(result.get("oracle"), dict)
    )


def successful_add_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("message") == "All add to task."
        and observed.get("exact_raw_count") == 1
        and observed.get("message_id_positive") is True
        and observed.get("task_delta") == 1
        and observed.get("size_cache_positive") is True
        and observed.get("cleanup_succeeded") is True
    )


def raw_document_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("id_matches") is True
        and observed.get("message_id_positive") is True
        and observed.get("message_type") == "raw"
        and observed.get("source_id") == 0
        and observed.get("memory_matches") is True
        and observed.get("agent_matches") is True
        and observed.get("session_matches") is True
        and observed.get("user_matches") is True
        and observed.get("zone_id") == 0
        and observed.get("status") is True
        and observed.get("content_matches") is True
        and observed.get("tokenized_nonempty") is True
        and observed.get("vector_dimension_positive") is True
        and observed.get("vector_is_real") is True
        and observed.get("forget_at_is_null") is True
    )


def fanout_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("message") == "All add to task."
        and observed.get("memory_counts") == [1, 1]
        and observed.get("memory_ids_match") is True
        and observed.get("contents_match") is True
        and observed.get("message_ids_distinct") is True
        and observed.get("task_delta") == 2
        and observed.get("cleanup_succeeded") is True
    )


def rejected_add_contract_ok(observed: dict[str, Any], *, expected_code: int, expected_message: str) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == expected_code
        and observed.get("message") == expected_message
        and observed.get("raw_delta") == 0
        and observed.get("task_delta") == 0
        and observed.get("cache_delta") == 0
        and observed.get("cleanup_succeeded") is True
    )


def long_content_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("stored_character_count") == observed.get("expected_character_count")
        and observed.get("prefix_matches") is True
        and observed.get("suffix_matches") is True
        and observed.get("tokenized_nonempty") is True
        and observed.get("cleanup_succeeded") is True
    )


def upsert_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("before_count") == 1
        and observed.get("adapter_error_count") == 0
        and observed.get("after_count") == 1
        and observed.get("id_unchanged") is True
        and observed.get("message_id_unchanged") is True
        and observed.get("updated_content_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def tokenizer_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("semantic_search_hit") is True and observed.get("cleanup_succeeded") is True
    if group == "experiment":
        return common and observed.get("physical_tokenized_matches") is True
    if group == "control":
        return common and observed.get("physical_tokenized_matches") is None
    raise ValueError("unknown group")


def list_messages_contract_ok(observed: dict[str, Any]) -> bool:
    expected_count = observed.get("expected_count")
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("storage_type") == "table"
        and observed.get("total_count") == expected_count
        and observed.get("returned_count") == expected_count
        and observed.get("raw_only") is True
        and observed.get("extract_lists_present") is True
        and observed.get("memory_ids_exact") is True
        and observed.get("message_ids_exact") is True
        and observed.get("valid_at_nonincreasing") is True
        and observed.get("valid_at_distinct") is True
        and observed.get("cleanup_succeeded") is True
    )


def pagination_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("responses") == [[200, 0], [200, 0]]
        and observed.get("page_lengths") == [3, 3]
        and observed.get("total_counts") == [10, 10]
        and observed.get("pages_disjoint") is True
        and observed.get("page_ids_from_fixture") is True
        and observed.get("valid_at_distinct_across_pages") is True
        and observed.get("cleanup_succeeded") is True
    )


def recent_messages_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("returned_count") == observed.get("limit")
        and observed.get("agent_ids_exact") is True
        and observed.get("session_ids_exact") is True
        and observed.get("memory_ids_exact") is True
        and observed.get("message_ids_from_fixture") is True
        and observed.get("valid_at_nonincreasing") is True
        and observed.get("valid_at_distinct") is True
        and observed.get("forgotten_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def recent_visibility_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("responses") == [[200, 0], [200, 0], [200, 0]]
        and observed.get("physical_raw_count") == 3
        and observed.get("physical_forgotten") is True
        and observed.get("physical_disabled") is True
        and observed.get("recent_count") == 2
        and observed.get("forgotten_absent") is True
        and observed.get("disabled_present") is True
        and observed.get("cleanup_succeeded") is True
    )


def recent_limit_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("baseline") == [200, 0]
        and observed.get("baseline_count") == 10
        and observed.get("huge") == [200, 101]
        and observed.get("invalid") == [200, 101]
        and observed.get("raw_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def list_failure_finding(
    group: str,
    observed: dict[str, Any],
    filter_name: str,
    fallback_id: str,
) -> dict[str, str]:
    if observed.get("valid_at_nonincreasing") is False:
        return {
            "id": "MS-LIST-ORDER-001",
            "summary": f"{group} message list was not ordered by valid_at DESC",
            "code_location": "memory/services/messages.py:list_message",
        }
    return {
        "id": fallback_id,
        "summary": f"{group} {filter_name} list filter leaked or omitted raw messages",
        "code_location": "memory/services/messages.py:list_message",
    }


def recent_failure_finding(group: str, observed: dict[str, Any]) -> dict[str, str]:
    if observed.get("valid_at_nonincreasing") is False:
        return {
            "id": "MS-RECENT-ORDER-001",
            "summary": f"{group} recent messages were not ordered by valid_at DESC",
            "code_location": "memory/services/messages.py:get_recent_messages",
        }
    return {
        "id": "MS-RECENT-FILTER-001",
        "summary": f"{group} recent-message filtering or limit differed",
        "code_location": "memory/services/messages.py:get_recent_messages",
    }


def semantic_search_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("write") == [200, 0]
        and observed.get("search") == [200, 0]
        and observed.get("physical_raw_count") == 1
        and observed.get("target_hit") is True
        and observed.get("target_content_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def pure_vector_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("public_search") == [200, 0]
        and observed.get("public_target_hit") is True
        and int(observed.get("adapter_result_count") or 0) >= 1
        and observed.get("query_dimension_positive") is True
        and observed.get("scores_finite") is True
        and observed.get("scores_nonincreasing") is True
        and observed.get("score_recomputation_matches") is True
        and observed.get("target_ids_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def vector_score_matches(backend: str, score: float, recomputed: float) -> bool:
    tolerance = 1e-3 if "Infinity" in backend else 1e-4
    return abs(float(score) - float(recomputed)) <= tolerance


def adapter_probe_select_fields(mode: str) -> list[str]:
    fields = [
        "message_id",
        "memory_id",
        "agent_id",
        "session_id",
        "user_id",
        "content",
        "valid_at",
        "status",
        "forget_at",
    ]
    if mode == "dense":
        fields.append("content_embed")
    elif mode != "fusion":
        raise ValueError("unsupported probe mode")
    return fields


def empty_vector_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("fixture_action_succeeded") is True
        and observed.get("target_absent_from_dense_results") is True
        and observed.get("other_message_present") is True
        and observed.get("fixture_residue_count") == 0
        and observed.get("cleanup_succeeded") is True
    )
    if group == "experiment":
        return common and observed.get("target_empty_flag") is True
    if group == "control":
        return common and observed.get("target_empty_flag") is None
    raise ValueError("unknown group")


def empty_vector_update_payload(dimension: int) -> dict[str, str]:
    if int(dimension) <= 0:
        raise ValueError("vector dimension must be positive")
    return {"remove": f"q_{int(dimension)}_vec"}


def search_parameter_contract_ok(observed: dict[str, Any], *, invalid_count: int) -> bool:
    invalid = observed.get("invalid_responses")
    return (
        isinstance(observed.get("legal_responses"), list)
        and bool(observed["legal_responses"])
        and all(response == [200, 0] for response in observed["legal_responses"])
        and observed.get("legal_limits_ok") is True
        and isinstance(invalid, list)
        and len(invalid) == invalid_count
        and all(response == [200, 101] for response in invalid)
        and observed.get("raw_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def search_visibility_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("mutation_responses") == [[200, 0], [200, 0]]
        and observed.get("search_response") == [200, 0]
        and observed.get("physical_raw_count") == 3
        and observed.get("physical_forgotten") is True
        and observed.get("physical_disabled") is True
        and observed.get("active_present") is True
        and observed.get("forgotten_absent") is True
        and observed.get("disabled_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def query_validation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("legal") == [200, 0]
        and observed.get("empty") == [200, 101]
        and observed.get("missing") == [200, 101]
        and observed.get("raw_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def multi_memory_search_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("writes") == [[200, 0], [200, 0]]
        and observed.get("search") == [200, 0]
        and observed.get("physical_raw_count") == 2
        and observed.get("returned_memory_ids_exact") is True
        and observed.get("returned_message_ids_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def search_filter_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("writes") == [[200, 0], [200, 0], [200, 0]]
        and observed.get("searches") == [[200, 0], [200, 0], [200, 0]]
        and observed.get("physical_raw_count") == 3
        and observed.get("physical_user_ids_exact") is True
        and observed.get("agent_filter_exact") is True
        and observed.get("agent_session_filter_exact") is True
        and observed.get("user_filter_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def status_transition_contract_ok(observed: dict[str, Any], *, expected_status: bool) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("response_message") is True
        and observed.get("physical_raw_count") == 1
        and observed.get("status") is expected_status
        and observed.get("physical_status_int") == (1 if expected_status else 0)
        and observed.get("identity_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def status_roundtrip_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("responses") == [[200, 0], [200, 0]]
        and observed.get("response_messages") == [True, True]
        and observed.get("statuses") == [False, True]
        and observed.get("physical_status_ints") == [0, 1]
        and observed.get("identity_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def status_visibility_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("disable") == [200, 0]
        and observed.get("search") == [200, 0]
        and observed.get("recent") == [200, 0]
        and observed.get("physical_raw_count") == 2
        and observed.get("physical_disabled") is True
        and observed.get("search_active_only") is True
        and observed.get("recent_includes_both") is True
        and observed.get("cleanup_succeeded") is True
    )


def invalid_status_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("responses") == [[200, 101], [200, 101]] and observed.get("messages_exact") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def status_access_rejection_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("response") == [200, 404] and observed.get("message_exact") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def missing_message_status_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("response") == [200, 404] and observed.get("message_identifies_missing_message") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def forget_transition_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("response_message") is True
        and observed.get("physical_raw_count") == 1
        and observed.get("forget_at_set") is True
        and observed.get("timestamp_in_window") is True
        and observed.get("identity_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def forget_visibility_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("forget") == [200, 0]
        and observed.get("search") == [200, 0]
        and observed.get("recent") == [200, 0]
        and observed.get("physical_raw_count") == 2
        and observed.get("physical_forgotten") is True
        and observed.get("search_active_only") is True
        and observed.get("recent_active_only") is True
        and observed.get("cleanup_succeeded") is True
    )


def forgotten_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("forget") == [200, 0]
        and observed.get("list") == [200, 0]
        and observed.get("physical_raw_count") == 2
        and observed.get("physical_forgotten") is True
        and observed.get("list_includes_both") is True
        and observed.get("cleanup_succeeded") is True
    )


def repeat_forget_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("responses") == [[200, 0], [200, 0]]
        and observed.get("response_messages") == [True, True]
        and observed.get("first_timestamp_set") is True
        and observed.get("second_timestamp_later") is True
        and observed.get("physical_raw_count") == 1
        and observed.get("identity_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def forget_access_rejection_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("response") == [200, 404] and observed.get("message_exact") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def missing_message_forget_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("response") == [200, 404] and observed.get("message_identifies_missing_message") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def message_content_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("response") == [200, 0]
        and observed.get("response_message") is True
        and observed.get("physical_raw_count") == 1
        and observed.get("id_exact") is True
        and observed.get("message_id_exact") is True
        and observed.get("memory_id_exact") is True
        and observed.get("content_exact") is True
        and observed.get("vector_nonempty") is True
        and observed.get("raw_response_consistent") is True
        and observed.get("cleanup_succeeded") is True
    )


def content_access_rejection_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("response") == [200, 404] and observed.get("message_exact") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def missing_message_content_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("response") == [200, 404] and observed.get("message_identifies_missing_message") is True and observed.get("raw_unchanged") is True and observed.get("cleanup_succeeded") is True


def hidden_message_content_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("mutations") == [[200, 0], [200, 0]]
        and observed.get("reads") == [[200, 0], [200, 0]]
        and observed.get("response_messages") == [True, True]
        and observed.get("physical_raw_count") == 2
        and observed.get("physical_forgotten") is True
        and observed.get("physical_inactive") is True
        and observed.get("ids_exact") is True
        and observed.get("contents_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def same_tenant_memory_isolation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("writes") == [[200, 0], [200, 0]]
        and observed.get("reads") == [[200, 0], [200, 0]]
        and observed.get("physical_raw_count") == 2
        and observed.get("api_a_only") is True
        and observed.get("api_b_only") is True
        and observed.get("physical_memory_ids_exact") is True
        and observed.get("backend_layout_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def cross_tenant_memory_isolation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("writes") == [[200, 0], [200, 0]]
        and observed.get("cross_reads") == [[200, 0], [200, 0]]
        and observed.get("cross_results_empty") is True
        and observed.get("physical_raw_counts") == [1, 1]
        and observed.get("tenant_indexes_distinct") is True
        and observed.get("physical_relations_distinct") is True
        and observed.get("cleanup_succeeded") is True
    )


def delete_memory_isolation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("writes") == [[200, 0], [200, 0]]
        and observed.get("delete") == [200, 0]
        and observed.get("physical_before") == 2
        and observed.get("physical_after") == 1
        and observed.get("target_removed") is True
        and observed.get("survivor_exact") is True
        and observed.get("survivor_api_exact") is True
        and observed.get("metadata_boundary_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def memory_target_rows_removed(snapshot: dict[str, Any], memory_id: str) -> bool:
    return all(row.get("memory_id") != memory_id for row in snapshot.get("rows", []))


def collision_isolation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("source_writes") == [[200, 0], [200, 0]]
        and observed.get("fixture_inserted") is True
        and observed.get("physical_pair_count") == 2
        and observed.get("physical_ids_exact") is True
        and observed.get("reads") == [[200, 0], [200, 0]]
        and observed.get("api_a_exact") is True
        and observed.get("api_b_exact") is True
        and observed.get("both_rows_survive_reads") is True
        and observed.get("fixture_cleanup_count") == 0
        and observed.get("memory_cleanup_succeeded") is True
    )


def initial_vector_schema_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("write") == [200, 0]
        and observed.get("physical_raw_count") == 1
        and observed.get("dimension_positive") is True
        and observed.get("vector_column_count") == 1
        and observed.get("vector_type_ok") is True
        and observed.get("vector_index_present") is True
        and observed.get("row_vector_real") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("empty_column_count") == 0 and observed.get("row_empty_flag") is None
    if group == "experiment":
        return common and observed.get("empty_column_count") == 1 and observed.get("row_empty_flag") is False
    raise ValueError("unknown group")


def new_vector_dimension_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("source_dimension_positive") is True
        and observed.get("dimensions_distinct") is True
        and observed.get("source_preserved") is True
        and observed.get("vector_dimensions_exact") is True
        and observed.get("empty_columns_exact") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("fixture_rejected") is True and observed.get("candidate_exists") is False
    if group == "experiment":
        return common and observed.get("fixture_rejected") is False and observed.get("candidate_exists") is True and observed.get("candidate_dimension_exact") is True
    raise ValueError("unknown group")


def cross_dimension_upsert_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    if observed.get("source_row_preserved") is not True or observed.get("cleanup_succeeded") is not True:
        return False
    if group == "control":
        return observed.get("fixture_rejected") is True and observed.get("source_content_preserved") is True and observed.get("source_dimension_preserved") is True
    if group == "experiment":
        return (
            observed.get("fixture_rejected") is False
            and observed.get("new_dimension_real") is True
            and observed.get("new_empty_false") is True
            and observed.get("old_dimension_zero") is True
            and observed.get("old_empty_true") is True
        )
    raise ValueError("unknown group")


def empty_vector_dense_filter_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("fixture_action_succeeded") is True
        and observed.get("dense_result_source_only") is True
        and observed.get("source_vector_real") is True
        and observed.get("fixture_cleanup_succeeded") is True
        and observed.get("memory_cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("empty_flag") is None and observed.get("rejected_fixture_absent") is True
    if group == "experiment":
        return common and observed.get("empty_flag") is True and observed.get("placeholder_excluded") is True
    raise ValueError("unknown group")


def content_embed_dimension_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("read") == [200, 0]
        and observed.get("returned_dimension_is_original") is True
        and observed.get("returned_vector_real") is True
        and observed.get("content_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("cross_dimension_rejected") is True and observed.get("row_restored") is True
    if group == "experiment":
        return (
            common
            and observed.get("cross_dimension_rejected") is False
            and observed.get("original_empty_false") is True
            and observed.get("other_empty_true") is True
            and observed.get("other_dimension_zero") is True
        )
    raise ValueError("unknown group")


def vector_ddl_idempotency_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("writes") == [[200, 0], [200, 0], [200, 0]]
        and observed.get("ensure_error_count") == 0
        and observed.get("vector_column_count") == 1
        and observed.get("vector_index_present") is True
        and observed.get("physical_raw_count") == 3
        and observed.get("all_rows_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("empty_column_count") == 0
    if group == "experiment":
        return common and observed.get("empty_column_count") == 1
    raise ValueError("unknown group")


def sequence_seed_from_max_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("exclusive_window") is True
        and observed.get("key_absent_before_init") is True
        and observed.get("service_max_positive") is True
        and observed.get("service_max_equals_physical") is True
        and observed.get("initialized_seed_equals_max") is True
        and observed.get("next_write") == [200, 0]
        and observed.get("new_id_equals_max_plus_one") is True
        and observed.get("no_collision") is True
        and observed.get("api_restored") is True
        and observed.get("cleanup_succeeded") is True
    )


def existing_sequence_seed_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("exclusive_window") is True
        and observed.get("db_max_positive") is True
        and observed.get("existing_seed_gt_max") is True
        and observed.get("seed_before_equals_after") is True
        and observed.get("initializer_skipped") is True
        and observed.get("api_restored") is True
        and observed.get("cleanup_succeeded") is True
    )


def memory_size_cache_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("exclusive_window") is True
        and observed.get("cache_absent_before_init") is True
        and observed.get("raw_count_positive") is True
        and observed.get("calculated_size_positive") is True
        and observed.get("service_size_equals_manual") is True
        and observed.get("cache_equals_manual") is True
        and observed.get("api_restored") is True
        and observed.get("memory_cleanup_succeeded") is True
        and observed.get("cache_fixture_cleanup_succeeded") is True
    )


def forgotten_scan_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("write_count") == 2
        and observed.get("forget") == [200, 0]
        and observed.get("adapter_exception") is None
        and observed.get("returned_count") == 1
        and observed.get("returned_target_only") is True
        and observed.get("selected_forget_at_nonempty") is True
        and observed.get("unselected_forget_at_absent") is True
        and observed.get("physical_forgotten_count") == 1
        and observed.get("cleanup_succeeded") is True
    )


def missing_field_scan_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("raw_before_after_unchanged") is True and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("native_column_absent") is True and observed.get("unsupported_exception") == "AssertionError" and observed.get("returned_count") == 0
    if group == "experiment":
        return (
            common
            and observed.get("fixture_set_null") is True
            and observed.get("physical_field_is_null") is True
            and observed.get("returned_count") == 1
            and observed.get("returned_target_only") is True
            and observed.get("selected_fields_exact") is True
            and observed.get("restore_succeeded") is True
            and observed.get("physical_field_restored") is True
            and observed.get("missing_after_restore_count") == 0
        )
    raise ValueError("unknown group")


def empty_store_seed_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("exclusive_window") is True
        and observed.get("metadata_memory_count") == 0
        and observed.get("key_absent_before_init") is True
        and observed.get("initialized_seed") == 1
        and observed.get("api_restored") is True
    )


def physical_table_name_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("writes") == [[200, 0], [200, 0]] and observed.get("all_tables_exist") is True and observed.get("physical_raw_count") == 2 and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("native_names_exact") is True and observed.get("unique_physical_table_count") == 2
    if group == "experiment":
        return common and observed.get("sha1_name_exact") is True and observed.get("unique_physical_table_count") == 1
    raise ValueError("unknown group")


def base_schema_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("write") == [200, 0]
        and observed.get("table_exists") is True
        and observed.get("mapping_or_base_columns_complete") is True
        and observed.get("vector_column_exact") is True
        and observed.get("physical_raw_count") == 1
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("empty_column_count") == 0 and observed.get("native_types_complete") is True
    if group == "experiment":
        return (
            common
            and observed.get("empty_column_count") == 1
            and observed.get("base_types_exact") is True
            and observed.get("required_not_null_exact") is True
            and observed.get("defaults_exact") is True
            and observed.get("ustore") is True
        )
    raise ValueError("unknown group")


def base_index_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("write") == [200, 0] and observed.get("table_exists") is True and observed.get("duplicate_index_count") == 0 and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("native_index_names_exact") is True and observed.get("native_index_types_exact") is True
    if group == "experiment":
        return common and observed.get("regular_index_count") == 7 and observed.get("regular_index_columns_exact") is True and observed.get("primary_key_present") is True
    raise ValueError("unknown group")


def fulltext_index_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("write") == [200, 0] and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("fulltext_index_names_exact") is True and observed.get("fulltext_types_exact") is True and observed.get("fulltext_columns_exact") is True
    if group == "experiment":
        return common and observed.get("ugin_index_count") == 1 and observed.get("ugin_name_exact") is True and observed.get("ugin_expression_exact") is True
    raise ValueError("unknown group")


def vector_index_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("write") == [200, 0]
        and observed.get("dimension_positive") is True
        and observed.get("vector_index_count") == 1
        and observed.get("vector_index_dimension_exact") is True
        and observed.get("cosine_metric") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("hnsw") is True and observed.get("gsdiskann") is False
    if group == "experiment":
        return common and observed.get("hnsw") is False and observed.get("gsdiskann") is True
    raise ValueError("unknown group")


def catalog_ddl_idempotency_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("write") == [200, 0]
        and observed.get("create_error_count") == 0
        and observed.get("catalog_signature_unchanged") is True
        and observed.get("duplicate_index_count") == 0
        and observed.get("row_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )


def delete_index_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("dedicated_write") == [200, 0]
        and observed.get("guard_write") == [200, 0]
        and observed.get("dedicated_only_before") is True
        and observed.get("delete_succeeded") is True
        and observed.get("dedicated_table_absent") is True
        and observed.get("guard_table_present") is True
        and observed.get("guard_row_preserved") is True
        and observed.get("business_cleanup_succeeded") is True
    )


def index_exist_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("write") == [200, 0]
        and observed.get("before") is True
        and observed.get("dropped_index_absent") is True
        and observed.get("restored") is True
        and observed.get("restored_index_present") is True
        and observed.get("row_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("after_drop") is True
    if group == "experiment":
        return common and observed.get("after_drop") is False
    raise ValueError("unknown group")


def concurrent_ddl_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("responses") == [[200, 0], [200, 0]]
        and observed.get("barrier_released") is True
        and observed.get("start_spread_bounded") is True
        and observed.get("physical_table_count") == 1
        and observed.get("physical_raw_count") == 2
        and observed.get("message_ids_unique") is True
        and observed.get("required_indexes_complete") is True
        and observed.get("duplicate_index_count") == 0
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("advisory_lock_sql_exact") is None
    if group == "experiment":
        return common and observed.get("advisory_lock_sql_exact") is True
    raise ValueError("unknown group")


def keyword_weight_mapping_ok(mapping: dict[float, list[float]]) -> bool:
    if set(mapping) != {0.0, 0.5, 1.0}:
        return False
    return all(len(mapping[weight]) == 2 and abs(float(mapping[weight][0]) - weight) <= 1e-9 and abs(float(mapping[weight][1]) - (1.0 - weight)) <= 1e-9 for weight in mapping)


def fusion_weight_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("api_responses") == [[200, 0], [200, 0], [200, 0]]
        and observed.get("all_target_hits") is True
        and observed.get("adapter_results_nonempty") is True
        and observed.get("scores_finite_and_ordered") is True
        and observed.get("keyword_weight_mapping_correct") is True
        and observed.get("cleanup_succeeded") is True
    )


def _evidence_module():
    return _load_module("fresh_case_evidence.py", "fresh_07_evidence")


def _request(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: Any = None,
    timeout: float = 180,
) -> dict[str, Any]:
    return MS._request(
        case_id,
        group,
        label,
        auth,
        method,
        path,
        payload=payload,
        params=params,
        timeout=timeout,
    )


def _owner(case_id: str, group: str) -> dict[str, str]:
    return MS._owner(case_id, group)


def _preclean_and_create(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    label: str = "create_raw_memory",
) -> tuple[bool, dict[str, Any]]:
    preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
    fixture = MS._create_memory_fixture(case_id, group, owner, label, f"{prefix}-{group}", memory_type=["raw"])
    return preclean, fixture


def _add_message(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    memory_ids: list[str],
    *,
    agent_id: str,
    session_id: str,
    user_input: str,
    agent_response: str,
    user_id: str | None = None,
    timeout: float = 240,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "memory_id": memory_ids,
        "agent_id": agent_id,
        "session_id": session_id,
        "user_input": user_input,
        "agent_response": agent_response,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/messages",
        payload=payload,
        timeout=timeout,
    )


def _task_count(group: str, memory_id: str) -> int:
    return MS._memory_task_count(group, memory_id)


def _cache_value(group: str, memory_id: str) -> int | None:
    snapshot = MS._memory_size_cache(group, memory_id)
    return snapshot.get("value") if snapshot.get("exists") else None


def _store_snapshot(
    case_id: str,
    group: str,
    label: str,
    tenant_id: str,
    memory_ids: list[str],
    *,
    agent_ids: list[str] | None = None,
    expected_contents: dict[str, str] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    if not tenant_id or not all(memory_ids):
        return {"backend": None, "index_exists": False, "raw_count": 0, "rows": []}
    payload = {
        "tenant_id": tenant_id,
        "memory_ids": memory_ids,
        "agent_ids": agent_ids or [],
        "expected_contents": expected_contents or {},
    }
    script = r"""
import hashlib, json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService, index_name
from rag.nlp.rag_tokenizer import fine_grained_tokenize, tokenize
p = json.loads(sys.argv[1])
tenant_id = p["tenant_id"]
agent_ids = p["agent_ids"]
expected_contents = p["expected_contents"]
conn = settings.msgStoreConn
backend = type(conn).__name__
rows = []
index_flags = []
for memory_id in p["memory_ids"]:
    exists = bool(MessageService.has_index(tenant_id, memory_id))
    index_flags.append(exists)
    if not exists:
        continue
    listed = MessageService.list_message(
        tenant_id,
        memory_id,
        agent_ids=agent_ids or None,
        page=1,
        page_size=100,
    )
    for item in listed.get("message_list", []):
        message_id = int(item["message_id"])
        doc = MessageService.get_by_message_id(memory_id, message_id, tenant_id) or {}
        content = str(doc.get("content") or "")
        embed = list(doc.get("content_embed") or [])
        segmented_content_nonempty = bool(content)
        physical_segmentation_matches = None
        physical_empty_false = None
        physical_status_int = None
        physical_forget_at_ms = None
        physical_id = str(doc.get("id") or "")
        if "Infinity" in backend and doc:
            raw_conn = conn.connPool.get_conn()
            try:
                raw_db = raw_conn.get_database(conn.dbName)
                raw_table = raw_db.get_table(f"{index_name(tenant_id)}_{memory_id}")
                frame, _ = raw_table.output(["id", "status_int", "forget_at_flt"]).filter(
                    f"message_id = {message_id}"
                ).to_df()
                physical_ids = [str(value) for value in frame["id"].tolist()]
                if len(physical_ids) == 1:
                    physical_id = physical_ids[0]
                    physical_status_int = int(frame["status_int"].tolist()[0])
                    forget_at_flt = float(frame["forget_at_flt"].tolist()[0] or 0)
                    physical_forget_at_ms = int(forget_at_flt) if forget_at_flt > 0 else None
            finally:
                conn.connPool.release_conn(raw_conn)
        if "GaussDB" in backend and doc:
            table = conn.physical_table(index_name(tenant_id))
            dim = len(embed)
            selected, description = conn._fetch_one_with_description(
                f"SELECT tokenized_content_ltks,q_{dim}_vec_empty,status_int,forget_at FROM {conn.ddl.qualified_name(table)} WHERE id=%s",
                [str(doc.get("id") or f"{memory_id}_{message_id}")],
            )
            if selected is not None:
                physical = conn._row_to_dict(selected, description)
                actual_tokenized = str(physical.get("tokenized_content_ltks") or "")
                expected_tokenized = fine_grained_tokenize(tokenize(content))
                segmented_content_nonempty = bool(actual_tokenized)
                physical_segmentation_matches = actual_tokenized == expected_tokenized
                physical_empty_false = physical.get(f"q_{dim}_vec_empty") is False
                physical_status_int = int(physical.get("status_int"))
                physical_forget_at = physical.get("forget_at")
                if physical_forget_at is not None:
                    physical_forget_at_ms = int(physical_forget_at.timestamp() * 1000)
        expected = expected_contents.get(str(doc.get("agent_id") or ""))
        rows.append({
            "id": physical_id,
            "message_id": message_id,
            "message_type": str(doc.get("message_type") or ""),
            "source_id": int(doc.get("source_id") or 0),
            "memory_id": str(doc.get("memory_id") or ""),
            "user_id": str(doc.get("user_id") or ""),
            "agent_id": str(doc.get("agent_id") or ""),
            "session_id": str(doc.get("session_id") or ""),
            "zone_id": int(doc.get("zone_id") or 0),
            "status": bool(doc.get("status")),
            "physical_status_int": physical_status_int,
            "physical_forget_at_ms": physical_forget_at_ms,
            "forget_at_is_null": doc.get("forget_at") in (None, "", "-"),
            "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "content_character_count": len(content),
            "content_matches": expected is not None and content == expected,
            "prefix_matches": expected is not None and content[:64] == expected[:64],
            "suffix_matches": expected is not None and content[-64:] == expected[-64:],
            "segmented_content_nonempty": segmented_content_nonempty,
            "physical_segmentation_matches": physical_segmentation_matches,
            "vector_dimension": len(embed),
            "vector_is_real": bool(embed) and any(float(value) != 0.0 for value in embed),
            "physical_vector_empty_false": physical_empty_false,
        })
rows.sort(key=lambda row: (row["memory_id"], row["message_id"]))
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": backend,
    "index_exists": any(index_flags),
    "index_flags": index_flags,
    "raw_count": len(rows),
    "rows": rows,
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, ensure_ascii=False, sort_keys=True)],
        timeout=timeout,
        max_attempts=2,
    )


def _row_contract(
    row: dict[str, Any],
    *,
    memory_id: str,
    agent_id: str,
    session_id: str,
    user_id: str,
) -> dict[str, Any]:
    message_id = int(row.get("message_id") or 0)
    return {
        "id_matches": row.get("id") == f"{memory_id}_{message_id}",
        "message_id_positive": message_id > 0,
        "message_type": row.get("message_type"),
        "source_id": row.get("source_id"),
        "memory_matches": row.get("memory_id") == memory_id,
        "agent_matches": row.get("agent_id") == agent_id,
        "session_matches": row.get("session_id") == session_id,
        "user_matches": row.get("user_id") == user_id,
        "zone_id": row.get("zone_id"),
        "status": row.get("status"),
        "content_matches": row.get("content_matches"),
        "tokenized_nonempty": row.get("segmented_content_nonempty"),
        "vector_dimension_positive": int(row.get("vector_dimension") or 0) > 0,
        "vector_is_real": row.get("vector_is_real"),
        "forget_at_is_null": row.get("forget_at_is_null"),
    }


def _cleanup_memories(
    case_id: str,
    group: str,
    auth: str,
    memory_ids: list[str],
) -> bool:
    return MS._cleanup_memory_ids(case_id, group, auth, memory_ids)


def _list_payload(response: dict[str, Any]) -> tuple[list[dict[str, Any]], int, str | None]:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    messages = data.get("messages") if isinstance(data.get("messages"), dict) else {}
    rows = messages.get("message_list") if isinstance(messages.get("message_list"), list) else []
    return (
        [row for row in rows if isinstance(row, dict)],
        int(messages.get("total_count") or 0),
        str(data.get("storage_type")) if data.get("storage_type") is not None else None,
    )


def _recent_payload(response: dict[str, Any]) -> list[dict[str, Any]]:
    rows = response.get("data") if isinstance(response.get("data"), list) else []
    return [row for row in rows if isinstance(row, dict)]


def _message_id_set(rows: list[dict[str, Any]]) -> set[int]:
    return {int(row.get("message_id") or 0) for row in rows if int(row.get("message_id") or 0) > 0}


def _valid_at_nonincreasing(rows: list[dict[str, Any]]) -> bool:
    values = [str(row.get("valid_at") or "") for row in rows]
    return all(left >= right for left, right in zip(values, values[1:]))


def _valid_at_distinct(rows: list[dict[str, Any]]) -> bool:
    values = [str(row.get("valid_at") or "") for row in rows]
    return len(values) == len(set(values))


def _write_raw_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    specs: list[dict[str, str]],
    *,
    inter_write_delay: float = 0,
) -> dict[str, Any]:
    preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
    memory_id = str(fixture.get("id") or "")
    before_tasks = _task_count(group, memory_id) if memory_id else 0
    responses = []
    for index, spec in enumerate(specs, 1):
        responses.append(
            _add_message(
                case_id,
                group,
                owner["auth"],
                f"write_raw_fixture_{index:03d}",
                [memory_id],
                agent_id=spec["agent_id"],
                session_id=spec["session_id"],
                user_input=spec["user_input"],
                agent_response=spec["agent_response"],
            )
        )
        if inter_write_delay > 0 and index < len(specs):
            time.sleep(inter_write_delay)
    snapshot = _store_snapshot(
        case_id,
        group,
        "read_only_raw_fixture_snapshot",
        owner["tenant_id"],
        [memory_id],
    )
    after_tasks = _task_count(group, memory_id) if memory_id else 0
    return {
        "preclean": preclean,
        "fixture": fixture,
        "memory_id": memory_id,
        "responses": responses,
        "writes_succeeded": len(responses) == len(specs) and all(response.get("http_status") == 200 and response.get("code") == 0 for response in responses),
        "task_delta": after_tasks - before_tasks,
        "snapshot": snapshot,
        "rows": snapshot.get("rows", []),
    }


def _write_two_memory_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    spec_a: dict[str, str],
    spec_b: dict[str, str],
) -> dict[str, Any]:
    preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
    memory_a = MS._create_memory_fixture(case_id, group, owner, "create_memory_a", f"{prefix}-{group}-a")
    memory_b = MS._create_memory_fixture(case_id, group, owner, "create_memory_b", f"{prefix}-{group}-b")
    response_a = _add_message(
        case_id,
        group,
        owner["auth"],
        "write_memory_a_message",
        [memory_a["id"]],
        agent_id=spec_a["agent_id"],
        session_id=spec_a["session_id"],
        user_input=spec_a["user_input"],
        agent_response=spec_a["agent_response"],
    )
    response_b = _add_message(
        case_id,
        group,
        owner["auth"],
        "write_memory_b_message",
        [memory_b["id"]],
        agent_id=spec_b["agent_id"],
        session_id=spec_b["session_id"],
        user_input=spec_b["user_input"],
        agent_response=spec_b["agent_response"],
    )
    snapshot = _store_snapshot(
        case_id,
        group,
        "read_only_two_memory_fixture_snapshot",
        owner["tenant_id"],
        [memory_a["id"], memory_b["id"]],
    )
    return {
        "preclean": preclean,
        "memory_a": memory_a,
        "memory_b": memory_b,
        "memory_ids": [memory_a["id"], memory_b["id"]],
        "responses": [response_a, response_b],
        "writes_succeeded": all(response.get("http_status") == 200 and response.get("code") == 0 for response in (response_a, response_b)),
        "snapshot": snapshot,
        "rows": snapshot.get("rows", []),
    }


def _physical_layout_probe(
    case_id: str,
    group: str,
    label: str,
    pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    payload = {"pairs": [[tenant_id, memory_id] for tenant_id, memory_id in pairs]}
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService, index_name
p = json.loads(sys.argv[1])
conn = settings.msgStoreConn
backend = type(conn).__name__
logical_indexes = []
physical_relations = []
index_flags = []
raw_counts = []
for tenant_id, memory_id in p["pairs"]:
    logical_index = index_name(tenant_id)
    logical_indexes.append(logical_index)
    exists = bool(MessageService.has_index(tenant_id, memory_id))
    index_flags.append(exists)
    if "Infinity" in backend:
        physical_relations.append(f"{logical_index}_{memory_id}")
    elif "GaussDB" in backend:
        physical_relations.append(str(conn.physical_table(logical_index)))
    else:
        physical_relations.append(f"{logical_index}:{memory_id}")
    if exists:
        listed = MessageService.list_message(
            tenant_id, memory_id, page=1, page_size=100
        )
        raw_counts.append(len(listed.get("message_list", [])))
    else:
        raw_counts.append(0)
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": backend,
    "index_flags": index_flags,
    "per_pair_raw_counts": raw_counts,
    "tenant_indexes_distinct": len(set(logical_indexes)) == len(logical_indexes),
    "physical_relations_distinct": len(set(physical_relations)) == len(physical_relations),
    "physical_relation_count": len(set(physical_relations)),
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=120,
        max_attempts=2,
    )


def _tenant_model_values(group: str, tenant_id: str) -> dict[str, str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT embd_id,llm_id FROM tenant WHERE id=%s", (tenant_id,))
            row = cursor.fetchone()
    finally:
        connection.close()
    return {
        "embd_id": "" if row is None or row[0] is None else str(row[0]),
        "llm_id": "" if row is None or row[1] is None else str(row[1]),
    }


def _create_memory_with_model_ids(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    name: str,
    *,
    embd_id: str,
    llm_id: str,
) -> dict[str, Any]:
    response = _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/memories",
        payload={
            "name": name,
            "memory_type": ["raw"],
            "embd_id": embd_id,
            "llm_id": llm_id,
        },
    )
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    memory_id = str(data.get("id") or "")
    return {
        "response": response,
        "data": data,
        "id": memory_id,
        "snapshot": MS._memory_snapshot(group, memory_id) if memory_id else {"count": 0},
    }


def _configure_secondary_embedding(
    case_id: str,
    group: str,
    secondary: dict[str, Any],
) -> dict[str, Any]:
    provider_name = "Ollama"
    case_slug = case_id.lower().removeprefix("tc-")
    instance_name = f"fresh-{case_slug}-{group}-embedding"
    model_name = "qwen3-embedding:0.6b"
    provider = BASE._ensure_provider(case_id, group, secondary["auth"], provider_name)
    precleaned = provider["ready"] and BASE._drop_provider_instance(case_id, group, secondary["auth"], instance_name, provider_name)
    created = _request(
        case_id,
        group,
        "create_secondary_ollama_embedding_instance",
        secondary["auth"],
        "POST",
        f"/providers/{provider_name}/instances",
        payload={
            "instance_name": instance_name,
            "api_key": "x",
            "base_url": "http://127.0.0.1:11434",
            "region": "",
            "model_info": [
                {
                    "model_name": model_name,
                    "model_type": ["embedding"],
                    "max_tokens": 8192,
                }
            ],
        },
        timeout=180,
    )
    entry = {
        "model_provider": provider_name,
        "model_instance": instance_name,
        "model_name": model_name,
        "model_type": "embedding",
    }
    default = BASE._set_default_model(
        case_id,
        group,
        secondary["auth"],
        "embedding",
        entry,
        "set_secondary_default_embedding",
    )
    actual_entry, default_sha = BASE._default_model_entry(case_id, group, secondary["auth"], "embedding")
    models = _tenant_model_values(group, secondary["tenant_id"])
    return {
        "ready": provider["ready"] and precleaned and created.get("code") == 0 and default.get("code") == 0 and BASE._default_entry_matches(actual_entry, entry) and bool(models["embd_id"]),
        "provider_added": provider["added"],
        "instance_name": instance_name,
        "model_values": models,
        "raw_sha256": [
            provider.get("before_raw_sha256"),
            provider.get("add_raw_sha256"),
            provider.get("after_raw_sha256"),
            created.get("raw_sha256"),
            default.get("raw_sha256"),
            default_sha,
        ],
    }


def _cleanup_secondary_embedding(
    case_id: str,
    group: str,
    secondary: dict[str, Any],
    configured: dict[str, Any],
) -> bool:
    cleared = BASE._set_default_model(
        case_id,
        group,
        secondary["auth"],
        "embedding",
        None,
        "clear_secondary_default_embedding",
    )
    dropped = BASE._drop_provider_instance(
        case_id,
        group,
        secondary["auth"],
        configured["instance_name"],
        "Ollama",
    )
    provider_removed = BASE._remove_provider_if_added(
        case_id,
        group,
        secondary["auth"],
        "Ollama",
        configured["provider_added"],
    )
    return cleared.get("code") == 0 and dropped and provider_removed


def _prepare_vector_tenant_fixture(
    case_id: str,
    group: str,
    *,
    message_count: int = 1,
) -> dict[str, Any]:
    case_number = case_id.rsplit("-", 1)[-1]
    email = f"ms-{case_number}-vector-user@fresh.invalid"
    password = secondary_fixture_password(case_id)
    secondary = BASE._prepare_secondary_user(case_id, group, email, password)
    configured = _configure_secondary_embedding(case_id, group, secondary)
    models = configured["model_values"]
    prefix = f"fresh-ms-{case_number}"
    memory = _create_memory_with_model_ids(
        case_id,
        group,
        secondary["auth"],
        "create_dedicated_vector_memory",
        f"{prefix}-{group}",
        embd_id=models["embd_id"],
        llm_id=models["llm_id"],
    )
    before_snapshot = _store_snapshot(
        case_id,
        group,
        "read_only_before_first_vector_write",
        secondary["tenant_id"],
        [memory["id"]],
    )
    responses = []
    for index in range(1, message_count + 1):
        responses.append(
            _add_message(
                case_id,
                group,
                secondary["auth"],
                f"write_real_vector_message_{index:03d}",
                [memory["id"]],
                agent_id=f"{prefix}-{group}-agent-{index}",
                session_id=f"{prefix}-{group}-session",
                user_input=f"real vector input {index}",
                agent_response=f"real vector response {index}",
            )
        )
    snapshot = _store_snapshot(
        case_id,
        group,
        "read_only_dedicated_vector_fixture_snapshot",
        secondary["tenant_id"],
        [memory["id"]],
    )
    return {
        "email": email,
        "secondary": secondary,
        "configured": configured,
        "memory": memory,
        "memory_id": memory["id"],
        "responses": responses,
        "before_snapshot": before_snapshot,
        "snapshot": snapshot,
        "rows": snapshot.get("rows", []),
        "ready": secondary["preclean"]
        and secondary["registration"].get("code") == 0
        and secondary["login"].get("code") == 0
        and configured["ready"]
        and memory["response"].get("code") == 0
        and before_snapshot.get("index_exists") is False
        and before_snapshot.get("raw_count") == 0
        and len(responses) == message_count
        and all(response.get("http_status") == 200 and response.get("code") == 0 for response in responses)
        and snapshot.get("raw_count") == message_count,
    }


def _cleanup_vector_tenant_fixture(
    case_id: str,
    group: str,
    fixture: dict[str, Any],
) -> dict[str, bool]:
    secondary = fixture["secondary"]
    memory_cleanup = _cleanup_memories(case_id, group, secondary["auth"], [fixture["memory_id"]])
    model_cleanup = _cleanup_secondary_embedding(case_id, group, secondary, fixture["configured"])
    user_cleanup = BASE._cleanup_secondary_user(case_id, group, fixture["email"])
    return {
        "memory": memory_cleanup,
        "model": model_cleanup,
        "user": user_cleanup["succeeded"],
        "succeeded": memory_cleanup and model_cleanup and user_cleanup["succeeded"],
    }


def _vector_dimension_action(
    case_id: str,
    group: str,
    label: str,
    *,
    action: str,
    tenant_id: str,
    memory_id: str,
    source_message_id: int,
    expected_raw_count: int,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "tenant_id": tenant_id,
        "memory_id": memory_id,
        "source_message_id": source_message_id,
        "expected_raw_count": expected_raw_count,
        "dim_b": 8,
    }
    script = r"""
import hashlib, json, re, sys
from common import settings
settings.init_settings()
from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
from memory.services.messages import MessageService, index_name
from memory.utils.gaussdb_conn import parse_vector_value
p = json.loads(sys.argv[1])
tenant_id = p["tenant_id"]
memory_id = p["memory_id"]
source_id = int(p["source_message_id"])
candidate_id = source_id + 1000000
conn = settings.msgStoreConn
backend = type(conn).__name__
source_before = MessageService.get_by_message_id(memory_id, source_id, tenant_id) or {}
vector_a = [float(value) for value in list(source_before.get("content_embed") or [])]
dim_a = len(vector_a)
dim_b = int(p["dim_b"])
if dim_b == dim_a:
    dim_b = 7
vector_b = [float(index + 1) / float(dim_b) for index in range(dim_b)]
source_content = str(source_before.get("content") or "")
source_hash = hashlib.sha256(source_content.encode("utf-8")).hexdigest()

def make_message(message_id, vector, content=None):
    return {
        "message_id": int(message_id),
        "message_type": str(source_before.get("message_type") or "raw"),
        "source_id": int(source_before.get("source_id") or 0),
        "memory_id": memory_id,
        "user_id": str(source_before.get("user_id") or ""),
        "agent_id": str(source_before.get("agent_id") or ""),
        "session_id": str(source_before.get("session_id") or ""),
        "content": source_content if content is None else content,
        "content_embed": list(vector),
        "valid_at": source_before.get("valid_at"),
        "invalid_at": source_before.get("invalid_at"),
        "forget_at": source_before.get("forget_at"),
        "status": bool(source_before.get("status")),
    }

def try_insert(message):
    try:
        errors = MessageService.insert_message([message], tenant_id, memory_id) or []
        return {"rejected": bool(errors), "error_count": len(errors), "exception_type": None}
    except Exception as exc:
        return {"rejected": True, "error_count": 1, "exception_type": type(exc).__name__}

def doc_summary(message_id):
    doc = MessageService.get_by_message_id(memory_id, int(message_id), tenant_id) or {}
    content = str(doc.get("content") or "")
    vector = [float(value) for value in list(doc.get("content_embed") or [])]
    return {
        "exists": bool(doc),
        "message_id": int(doc.get("message_id") or 0) if doc else None,
        "memory_id": str(doc.get("memory_id") or ""),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest() if doc else None,
        "vector_dimension": len(vector),
        "vector_real": bool(vector) and any(value != 0.0 for value in vector),
    }

def schema_summary(row_id=None):
    vector_dims = []
    vector_count_a = 0
    vector_count_b = 0
    empty_count_a = 0
    empty_count_b = 0
    vector_type_ok = False
    vector_index_present = False
    row_empty_a = None
    row_empty_b = None
    row_vector_a_zero = None
    row_vector_b_zero = None
    if "Infinity" in backend:
        raw_conn = conn.connPool.get_conn()
        try:
            raw_db = raw_conn.get_database(conn.dbName)
            table = raw_db.get_table(f"{index_name(tenant_id)}_{memory_id}")
            columns = table.show_columns().rows()
            names = [str(row[0]) for row in columns]
            for name, type_name, _default, _comment in columns:
                match = re.fullmatch(r"q_([0-9]+)_vec", str(name))
                if match:
                    vector_dims.append(int(match.group(1)))
                if str(name) == f"q_{dim_a}_vec":
                    vector_type_ok = "embedding" in str(type_name).lower() and str(dim_a) in str(type_name)
            vector_count_a = names.count(f"q_{dim_a}_vec")
            vector_count_b = names.count(f"q_{dim_b}_vec")
            empty_count_a = names.count(f"q_{dim_a}_vec_empty")
            empty_count_b = names.count(f"q_{dim_b}_vec_empty")
            vector_index_present = "q_vec_idx" in list(table.list_indexes().index_names)
        finally:
            conn.connPool.release_conn(raw_conn)
    else:
        table = conn.physical_table(index_name(tenant_id))
        names = list(conn._column_names(table))
        vector_dims = list(conn._vector_dimensions(table))
        vector_count_a = names.count(f"q_{dim_a}_vec")
        vector_count_b = names.count(f"q_{dim_b}_vec")
        empty_count_a = names.count(f"q_{dim_a}_vec_empty")
        empty_count_b = names.count(f"q_{dim_b}_vec_empty")
        selected_type, description_type = conn._fetch_one_with_description(
            "SELECT data_type,udt_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name=%s",
            [conn.schema, table, f"q_{dim_a}_vec"],
        )
        if selected_type is not None:
            type_row = conn._row_to_dict(selected_type, description_type)
            vector_type_ok = "floatvector" in (str(type_row.get("data_type") or "") + str(type_row.get("udt_name") or "")).lower()
        vector_index_present = bool(conn._diskann_index_exists(table, dim_a))
        if row_id is not None:
            select_columns = []
            if vector_count_a and empty_count_a:
                select_columns.extend([f"q_{dim_a}_vec", f"q_{dim_a}_vec_empty"])
            if vector_count_b and empty_count_b:
                select_columns.extend([f"q_{dim_b}_vec", f"q_{dim_b}_vec_empty"])
            if select_columns:
                selected, description = conn._fetch_one_with_description(
                    f"SELECT {','.join(select_columns)} FROM {conn.ddl.qualified_name(table)} WHERE id=%s",
                    [f"{memory_id}_{int(row_id)}"],
                )
                if selected is not None:
                    row = conn._row_to_dict(selected, description)
                    if vector_count_a and empty_count_a:
                        values = parse_vector_value(row.get(f"q_{dim_a}_vec"))
                        row_empty_a = bool(row.get(f"q_{dim_a}_vec_empty"))
                        row_vector_a_zero = len(values) == dim_a and all(value == 0.0 for value in values)
                    if vector_count_b and empty_count_b:
                        values = parse_vector_value(row.get(f"q_{dim_b}_vec"))
                        row_empty_b = bool(row.get(f"q_{dim_b}_vec_empty"))
                        row_vector_b_zero = len(values) == dim_b and all(value == 0.0 for value in values)
    return {
        "vector_dimensions": sorted(set(vector_dims)),
        "vector_column_count_a": vector_count_a,
        "vector_column_count_b": vector_count_b,
        "empty_column_count_a": empty_count_a,
        "empty_column_count_b": empty_count_b,
        "vector_type_ok": vector_type_ok,
        "vector_index_present": vector_index_present,
        "row_empty_a": row_empty_a,
        "row_empty_b": row_empty_b,
        "row_vector_a_zero": row_vector_a_zero,
        "row_vector_b_zero": row_vector_b_zero,
    }

result = {"action": p["action"], "backend": backend, "dim_a": dim_a, "dim_b": dim_b}
if p["action"] == "inspect":
    result["schema"] = schema_summary(source_id)
elif p["action"] == "new_dim":
    attempt = try_insert(make_message(candidate_id, vector_b, "new dimension candidate"))
    candidate = doc_summary(candidate_id)
    source_after = doc_summary(source_id)
    schema = schema_summary(candidate_id if candidate["exists"] else source_id)
    cleanup_succeeded = True
    if candidate["exists"]:
        MessageService.delete_message({"message_id": candidate_id}, tenant_id, memory_id)
        cleanup_succeeded = not doc_summary(candidate_id)["exists"]
    result.update({"attempt": attempt, "candidate": candidate, "source_after": source_after, "schema": schema, "fixture_cleanup_succeeded": cleanup_succeeded})
elif p["action"] == "same_id_cross_dim":
    attempt = try_insert(make_message(source_id, vector_b, "cross dimension replacement"))
    source_after = doc_summary(source_id)
    result.update({"attempt": attempt, "source_after": source_after, "schema": schema_summary(source_id)})
elif p["action"] == "dense_empty_filter":
    if "GaussDB" in backend:
        insert_attempt = try_insert(make_message(candidate_id, vector_a, "empty vector placeholder"))
        updated = MessageService.update_message(
            {"memory_id": memory_id, "message_id": candidate_id},
            {"remove": f"q_{dim_a}_vec"},
            tenant_id,
            memory_id,
        )
        empty_schema = schema_summary(candidate_id)
        fixture_action_succeeded = not insert_attempt["rejected"] and bool(updated) and empty_schema["row_empty_a"] is True
        rejected_fixture_absent = None
    else:
        candidate = make_message(candidate_id, vector_a, "missing vector candidate")
        candidate.pop("content_embed", None)
        insert_attempt = try_insert(candidate)
        empty_schema = schema_summary(source_id)
        fixture_action_succeeded = insert_attempt["rejected"] and not doc_summary(candidate_id)["exists"]
        rejected_fixture_absent = not doc_summary(candidate_id)["exists"]
    dense = MatchDenseExpr(f"q_{dim_a}_vec", vector_a, "float", "cosine", 10, {"similarity": 0.0})
    raw, _total = conn.search(
        select_fields=["message_id", "memory_id"],
        highlight_fields=[],
        condition={},
        match_expressions=[dense],
        order_by=OrderByExpr(),
        offset=0,
        limit=10,
        index_names=[index_name(tenant_id)],
        memory_ids=[memory_id],
        agg_fields=[],
    )
    if hasattr(raw, "messages"):
        rows = [dict(row) for row in raw.messages]
    elif hasattr(raw, "to_dict"):
        rows = [dict(row) for row in raw.to_dict(orient="records")]
    else:
        rows = []
    result_ids = sorted({int(row.get("message_id") or 0) for row in rows if int(row.get("message_id") or 0) > 0})
    cleanup_succeeded = True
    if doc_summary(candidate_id)["exists"]:
        MessageService.delete_message({"message_id": candidate_id}, tenant_id, memory_id)
        cleanup_succeeded = not doc_summary(candidate_id)["exists"]
    result.update({
        "insert_attempt": insert_attempt,
        "fixture_action_succeeded": fixture_action_succeeded,
        "rejected_fixture_absent": rejected_fixture_absent,
        "empty_schema": empty_schema,
        "dense_result_ids": result_ids,
        "fixture_cleanup_succeeded": cleanup_succeeded,
    })
elif p["action"] == "roundtrip_dims":
    cross_attempt = try_insert(make_message(source_id, vector_b, "temporary cross dimension content"))
    intermediate = doc_summary(source_id)
    restore_attempt = try_insert(make_message(source_id, vector_a, source_content))
    final_doc = doc_summary(source_id)
    result.update({
        "cross_attempt": cross_attempt,
        "intermediate": intermediate,
        "restore_attempt": restore_attempt,
        "final_doc": final_doc,
        "schema": schema_summary(source_id),
    })
elif p["action"] == "ddl_idempotent":
    ensure_errors = []
    for _index in range(2):
        try:
            conn.create_idx(index_name(tenant_id), memory_id, dim_a)
        except Exception as exc:
            ensure_errors.append(type(exc).__name__)
    listed = MessageService.list_message(tenant_id, memory_id, page=1, page_size=100)
    listed_ids = sorted(int(row.get("message_id") or 0) for row in listed.get("message_list", []))
    result.update({
        "ensure_error_count": len(ensure_errors),
        "listed_ids": listed_ids,
        "schema": schema_summary(source_id),
    })
else:
    raise ValueError("unsupported vector fixture action")

source_final = doc_summary(source_id)
result.update({
    "source_before_exists": bool(source_before),
    "source_content_sha256": source_hash,
    "source_final": source_final,
    "expected_raw_count": int(p["expected_raw_count"]),
})
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps(result, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=240,
        max_attempts=2,
    )


def _raw_snapshot_identity(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            row.get("id"),
            int(row.get("message_id") or 0),
            row.get("memory_id"),
            row.get("agent_id"),
            row.get("session_id"),
            row.get("status"),
            row.get("forget_at_is_null"),
            row.get("content_sha256"),
        )
        for row in rows
    )


def _raw_snapshot_identity_without_status(
    rows: list[dict[str, Any]],
) -> list[tuple[Any, ...]]:
    return sorted(
        (
            row.get("id"),
            int(row.get("message_id") or 0),
            row.get("memory_id"),
            row.get("agent_id"),
            row.get("session_id"),
            row.get("forget_at_is_null"),
            row.get("content_sha256"),
            int(row.get("vector_dimension") or 0),
        )
        for row in rows
    )


def _raw_snapshot_identity_without_forget(
    rows: list[dict[str, Any]],
) -> list[tuple[Any, ...]]:
    return sorted(
        (
            row.get("id"),
            int(row.get("message_id") or 0),
            row.get("memory_id"),
            row.get("agent_id"),
            row.get("session_id"),
            row.get("status"),
            row.get("physical_status_int"),
            row.get("content_sha256"),
            int(row.get("vector_dimension") or 0),
        )
        for row in rows
    )


def _list_observation(
    response: dict[str, Any],
    expected_rows: list[dict[str, Any]],
    memory_ids: set[str],
    cleanup_succeeded: bool,
) -> dict[str, Any]:
    rows, total_count, storage_type = _list_payload(response)
    return {
        "http_status": response.get("http_status"),
        "code": response.get("code"),
        "storage_type": storage_type,
        "total_count": total_count,
        "returned_count": len(rows),
        "expected_count": len(expected_rows),
        "raw_only": all(row.get("message_type") == "raw" for row in rows),
        "extract_lists_present": all(row.get("extract") == [] for row in rows),
        "memory_ids_exact": all(str(row.get("memory_id") or "") in memory_ids for row in rows),
        "message_ids_exact": _message_id_set(rows) == _message_id_set(expected_rows),
        "valid_at_nonincreasing": _valid_at_nonincreasing(rows),
        "valid_at_distinct": _valid_at_distinct(rows),
        "cleanup_succeeded": cleanup_succeeded,
    }


def _adapter_upsert(
    case_id: str,
    group: str,
    label: str,
    tenant_id: str,
    memory_id: str,
    message_id: int,
    updated_content: str,
) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "memory_id": memory_id,
        "message_id": message_id,
        "updated_content": updated_content,
    }
    script = r"""
import hashlib, json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService
p = json.loads(sys.argv[1])
tenant_id, memory_id = p["tenant_id"], p["memory_id"]
message_id = int(p["message_id"])
before = MessageService.get_by_message_id(memory_id, message_id, tenant_id) or {}
message = {
    "message_id": message_id,
    "message_type": str(before.get("message_type") or "raw"),
    "source_id": int(before.get("source_id") or 0),
    "memory_id": memory_id,
    "user_id": str(before.get("user_id") or ""),
    "agent_id": str(before.get("agent_id") or ""),
    "session_id": str(before.get("session_id") or ""),
    "content": p["updated_content"],
    "content_embed": list(before.get("content_embed") or []),
    "valid_at": before.get("valid_at"),
    "invalid_at": None,
    "forget_at": None,
    "status": True,
}
errors = MessageService.insert_message([message], tenant_id, memory_id) if before else ["missing-source"]
after = MessageService.get_by_message_id(memory_id, message_id, tenant_id) or {}
listed = MessageService.list_message(tenant_id, memory_id, page=1, page_size=100)
matches = [row for row in listed.get("message_list", []) if int(row.get("message_id") or 0) == message_id]
content = str(after.get("content") or "")
conn = settings.msgStoreConn
physical_ids = []
if "Infinity" in type(conn).__name__:
    raw_conn = conn.connPool.get_conn()
    try:
        from memory.services.messages import index_name
        raw_db = raw_conn.get_database(conn.dbName)
        raw_table = raw_db.get_table(f"{index_name(tenant_id)}_{memory_id}")
        frame, _ = raw_table.output(["id"]).filter(
            f"message_id = {message_id}"
        ).to_df()
        physical_ids = [str(value) for value in frame["id"].tolist()]
    finally:
        conn.connPool.release_conn(raw_conn)
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": type(conn).__name__,
    "before_count": int(bool(before)),
    "adapter_error_count": len(errors or []),
    "after_count": len(matches),
    "returned_id": str(after.get("id") or ""),
    "physical_ids": physical_ids,
    "message_id_unchanged": int(after.get("message_id") or 0) == message_id,
    "updated_content_matches": content == p["updated_content"],
    "updated_content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
}, sort_keys=True))
"""
    result = MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, ensure_ascii=False, sort_keys=True)],
        timeout=120,
        max_attempts=2,
    )
    result["id_unchanged"] = document_id_matches(
        f"{memory_id}_{message_id}",
        str(result.get("returned_id") or ""),
        [str(value) for value in result.get("physical_ids", [])],
    )
    return result


def _adapter_search_probe(
    case_id: str,
    group: str,
    label: str,
    tenant_id: str,
    memory_ids: list[str],
    query: str,
    *,
    mode: str,
    similarity_threshold: float = 0.0,
    keywords_weight: float = 0.7,
    top_n: int = 10,
    condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "memory_ids": memory_ids,
        "query": query,
        "mode": mode,
        "similarity_threshold": similarity_threshold,
        "keywords_weight": keywords_weight,
        "top_n": top_n,
        "condition": condition or {},
        "select_fields": adapter_probe_select_fields(mode),
    }
    script = r"""
import json, math, sys
from common import settings
settings.init_settings()
from api.db.joint_services.tenant_model_service import get_model_config_from_provider_instance
from api.db.services.llm_service import LLMBundle
from api.db.services.memory_service import MemoryService
from common.constants import LLMType
from common.doc_store.doc_store_base import FusionExpr, OrderByExpr
from memory.services.messages import MessageService, index_name
from memory.services.query import MsgTextQuery, get_vector
p = json.loads(sys.argv[1])
memory = MemoryService.get_by_memory_id(p["memory_ids"][0])
if not memory:
    raise RuntimeError("memory fixture missing")
config = get_model_config_from_provider_instance(memory.tenant_id, LLMType.EMBEDDING, memory.embd_id)
with LLMBundle(memory.tenant_id, config) as model:
    dense = get_vector(p["query"], model, topk=p["top_n"], similarity=p["similarity_threshold"])
expressions = [dense]
if p["mode"] == "fusion":
    text, _ = MsgTextQuery().question(p["query"], min_match=p["similarity_threshold"])
    expressions = [
        text,
        dense,
        FusionExpr(
            "weighted_sum",
            p["top_n"],
            {"weights": f'{1-float(p["keywords_weight"])},{float(p["keywords_weight"])}'},
        ),
    ]
elif p["mode"] != "dense":
    raise ValueError("unsupported probe mode")
conn = settings.msgStoreConn
order = OrderByExpr().desc("valid_at")
raw, total = conn.search(
    select_fields=p["select_fields"],
    highlight_fields=[],
    condition=p["condition"],
    match_expressions=expressions,
    order_by=order,
    offset=0,
    limit=p["top_n"],
    index_names=[index_name(p["tenant_id"])],
    memory_ids=p["memory_ids"],
    agg_fields=[],
)
if hasattr(raw, "messages"):
    rows = [dict(row) for row in raw.messages]
elif hasattr(raw, "to_dict"):
    rows = [dict(row) for row in raw.to_dict(orient="records")]
else:
    rows = []
query_vector = [float(value) for value in dense.embedding_data]
query_norm = math.sqrt(sum(value * value for value in query_vector))
backend = type(conn).__name__
score_tolerance = 1e-3 if "Infinity" in backend else 1e-4
result_rows = []
for row in rows:
    memory_id = str(row.get("memory_id") or "")
    message_id = int(row.get("message_id") or 0)
    doc = (
        MessageService.get_by_message_id(memory_id, message_id, p["tenant_id"]) or {}
        if p["mode"] == "dense"
        else {}
    )
    vector = [float(value) for value in list(doc.get("content_embed") or [])]
    vector_norm = math.sqrt(sum(value * value for value in vector))
    recomputed = None
    if vector and len(vector) == len(query_vector) and query_norm and vector_norm:
        recomputed = sum(a*b for a,b in zip(vector, query_vector)) / (vector_norm * query_norm)
    score = float(row.get("_score")) if row.get("_score") is not None else None
    if "GaussDB" in backend and message_id and vector:
        from memory.utils.gaussdb_conn import vector_literal
        table = conn.physical_table(index_name(p["tenant_id"]))
        dim = len(query_vector)
        selected, description = conn._fetch_one_with_description(
            f"SELECT 1 - ({conn.ddl.vector_column_name(dim)} <+> %s::floatvector({dim})) AS score "
            f"FROM {conn.ddl.qualified_name(table)} WHERE id=%s",
            [vector_literal(query_vector, dim), f"{memory_id}_{message_id}"],
        )
        if selected is not None:
            recomputed = float(conn._row_to_dict(selected, description)["score"])
    result_rows.append({
        "message_id": message_id,
        "memory_id": memory_id,
        "score": score,
        "recomputed_score": recomputed,
        "score_matches": score is not None and recomputed is not None and abs(score-recomputed) <= score_tolerance,
    })
scores = [row["score"] for row in result_rows if row["score"] is not None]
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": backend,
    "mode": p["mode"],
    "configured_weights": [1-float(p["keywords_weight"]), float(p["keywords_weight"])] if p["mode"] == "fusion" else None,
    "query_dimension": len(query_vector),
    "score_tolerance": score_tolerance,
    "result_count": len(result_rows),
    "reported_total": int(total or 0),
    "rows": result_rows,
    "scores_finite": bool(scores) and all(math.isfinite(value) for value in scores),
    "scores_nonincreasing": all(left >= right for left, right in zip(scores, scores[1:])),
    "score_recomputation_matches": bool(result_rows) and all(row["score_matches"] for row in result_rows),
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, ensure_ascii=False, sort_keys=True)],
        timeout=180,
        max_attempts=2,
    )


def _empty_vector_fixture_probe(
    case_id: str,
    group: str,
    label: str,
    tenant_id: str,
    memory_id: str,
    target_message_id: int,
) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "memory_id": memory_id,
        "target_message_id": target_message_id,
    }
    script = r"""
import json, time, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService, index_name
p = json.loads(sys.argv[1])
tenant_id = p["tenant_id"]
memory_id = p["memory_id"]
target_id = int(p["target_message_id"])
conn = settings.msgStoreConn
backend = type(conn).__name__
target = MessageService.get_by_message_id(memory_id, target_id, tenant_id) or {}
before = MessageService.list_message(tenant_id, memory_id, page=1, page_size=100)
before_ids = {int(row.get("message_id") or 0) for row in before.get("message_list", [])}
fixture_action_succeeded = False
target_empty_flag = None
excluded_id = target_id
fixture_residue_count = 0
fixture_rejected = None
adapter_cleanup_succeeded = True
if "GaussDB" in backend:
    dim = len(list(target.get("content_embed") or []))
    updated = MessageService.update_message(
        {"memory_id": memory_id, "message_id": target_id},
        {"remove": f"q_{dim}_vec"},
        tenant_id,
        memory_id,
    )
    table = conn.physical_table(index_name(tenant_id))
    selected, description = conn._fetch_one_with_description(
        f"SELECT {conn.ddl.vector_empty_column_name(dim)} AS empty_flag "
        f"FROM {conn.ddl.qualified_name(table)} WHERE id=%s",
        [f"{memory_id}_{target_id}"],
    )
    target_empty_flag = (
        bool(conn._row_to_dict(selected, description).get("empty_flag"))
        if selected is not None
        else False
    )
    fixture_action_succeeded = bool(updated) and target_empty_flag
else:
    excluded_id = int(time.time_ns() % 4000000000) + 5000000000
    candidate = {
        "message_id": excluded_id,
        "message_type": "raw",
        "source_id": 0,
        "memory_id": memory_id,
        "user_id": str(target.get("user_id") or ""),
        "agent_id": "fresh-ms-204-missing-vector-fixture",
        "session_id": "fresh-ms-204-missing-vector-fixture",
        "content": "missing vector fixture must be rejected",
        "valid_at": target.get("valid_at"),
        "invalid_at": None,
        "forget_at": None,
        "status": True,
    }
    try:
        errors = MessageService.insert_message([candidate], tenant_id, memory_id) or []
        fixture_rejected = bool(errors)
    except Exception:
        fixture_rejected = True
    inserted = MessageService.get_by_message_id(memory_id, excluded_id, tenant_id) or {}
    if inserted:
        deleted = MessageService.delete_message(
            {"memory_id": memory_id, "message_id": excluded_id}, tenant_id, memory_id
        )
        adapter_cleanup_succeeded = bool(deleted)
    residue = MessageService.get_by_message_id(memory_id, excluded_id, tenant_id) or {}
    fixture_residue_count = int(bool(residue))
    fixture_action_succeeded = (
        fixture_rejected is True
        and fixture_residue_count == 0
        and adapter_cleanup_succeeded
    )
after = MessageService.list_message(tenant_id, memory_id, page=1, page_size=100)
after_ids = {int(row.get("message_id") or 0) for row in after.get("message_list", [])}
original_rows_preserved = before_ids == after_ids and target_id in after_ids
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": backend,
    "excluded_message_id": excluded_id,
    "fixture_action_succeeded": fixture_action_succeeded,
    "fixture_rejected": fixture_rejected,
    "target_empty_flag": target_empty_flag,
    "fixture_residue_count": fixture_residue_count,
    "adapter_cleanup_succeeded": adapter_cleanup_succeeded,
    "original_rows_preserved": original_rows_preserved,
    "before_raw_count": len(before_ids),
    "after_raw_count": len(after_ids),
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, ensure_ascii=False, sort_keys=True)],
        timeout=180,
        max_attempts=2,
    )


def _finalize(recorder: Any, case_id: str) -> dict[str, Any]:
    result = recorder.finalize()
    _evidence_module().write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def _run_case(case_id: str, execute: Callable[[str, dict[str, str]], dict[str, Any]]) -> dict[str, Any]:
    recorder = _evidence_module().CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        result = execute(group, _owner(case_id, group))
        if not group_result_shape_ok(result):
            raise ValueError(f"invalid group result for {case_id}/{group}")
        recorder.add_group(
            group,
            result["status"],
            result["steps"],
            oracle=result["oracle"],
            findings=result.get("findings", []),
        )
    return _finalize(recorder, case_id)


def _single_write(
    case_id: str,
    group: str,
    owner: dict[str, str],
    *,
    prefix: str,
    agent_id: str,
    session_id: str,
    user_input: str,
    agent_response: str,
    cleanup_after_snapshot: bool = True,
    request_timeout: float = 240,
    snapshot_timeout: float = 120,
) -> dict[str, Any]:
    preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
    memory_id = fixture["id"]
    expected_content = f"User Input: {user_input}\nAgent Response: {agent_response}"
    before_tasks = _task_count(group, memory_id) if memory_id else 0
    response = _add_message(
        case_id,
        group,
        owner["auth"],
        "add_raw_message",
        [memory_id],
        agent_id=agent_id,
        session_id=session_id,
        user_input=user_input,
        agent_response=agent_response,
        timeout=request_timeout,
    )
    snapshot = _store_snapshot(
        case_id,
        group,
        "read_only_raw_message_snapshot",
        owner["tenant_id"],
        [memory_id],
        agent_ids=[agent_id],
        expected_contents={agent_id: expected_content},
        timeout=snapshot_timeout,
    )
    after_tasks = _task_count(group, memory_id) if memory_id else 0
    cache = _cache_value(group, memory_id) if memory_id else None
    rows = snapshot.get("rows", [])
    row = rows[0] if len(rows) == 1 else {}
    cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id]) if cleanup_after_snapshot else None
    return {
        "preclean": preclean,
        "fixture": fixture,
        "response": response,
        "snapshot": snapshot,
        "row": row,
        "expected_content": expected_content,
        "task_delta": after_tasks - before_tasks,
        "cache": cache,
        "cleanup": cleanup,
    }


def run_ms001() -> dict[str, Any]:
    case_id = "TC-MS-001"
    prefix = "fresh-ms-001"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
        memory_id = fixture["id"]
        credential = BASE._create_beta_credential(case_id, group, owner["auth"], "create_ordinary_api_token")
        api_token = credential.get("_token", "")
        api_agent = f"{prefix}-{group}-api-agent"
        api_session = f"{prefix}-{group}-api-session"
        jwt_agent = f"{prefix}-{group}-jwt-agent"
        jwt_session = f"{prefix}-{group}-jwt-session"
        api_content = "User Input: I like coriander in my soup\nAgent Response: Noted your preference for coriander"
        jwt_content = "User Input: JWT anti spoof input\nAgent Response: JWT anti spoof response"
        before_tasks = _task_count(group, memory_id) if memory_id else 0
        api_add = _add_message(
            case_id,
            group,
            api_token,
            "add_with_api_token_external_subject",
            [memory_id],
            agent_id=api_agent,
            session_id=api_session,
            user_input="I like coriander in my soup",
            agent_response="Noted your preference for coriander",
            user_id="user-001",
        )
        jwt_add = _add_message(
            case_id,
            group,
            owner["auth"],
            "add_with_jwt_spoof_attempt",
            [memory_id],
            agent_id=jwt_agent,
            session_id=jwt_session,
            user_input="JWT anti spoof input",
            agent_response="JWT anti spoof response",
            user_id="spoofed-user",
        )
        snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_two_attribution_rows",
            owner["tenant_id"],
            [memory_id],
            agent_ids=[api_agent, jwt_agent],
            expected_contents={api_agent: api_content, jwt_agent: jwt_content},
        )
        rows = {row.get("agent_id"): row for row in snapshot.get("rows", [])}
        api_contract = _row_contract(
            rows.get(api_agent, {}),
            memory_id=memory_id,
            agent_id=api_agent,
            session_id=api_session,
            user_id="user-001",
        )
        jwt_contract = _row_contract(
            rows.get(jwt_agent, {}),
            memory_id=memory_id,
            agent_id=jwt_agent,
            session_id=jwt_session,
            user_id=owner["tenant_id"],
        )
        task_delta = _task_count(group, memory_id) - before_tasks if memory_id else 0
        cache = _cache_value(group, memory_id) if memory_id else None
        token_cleanup = BASE._cleanup_beta_credential(
            case_id,
            group,
            owner["auth"],
            api_token,
            label="cleanup_ordinary_api_token",
        )
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id])
        response_ok = all(item.get("http_status") == 200 and item.get("code") == 0 and item.get("message") == "All add to task." for item in (api_add, jwt_add))
        passed = (
            preclean
            and fixture["response"]["code"] == 0
            and credential.get("code") == 0
            and response_ok
            and len(snapshot.get("rows", [])) == 2
            and raw_document_contract_ok(api_contract)
            and raw_document_contract_ok(jwt_contract)
            and task_delta == 2
            and isinstance(cache, int)
            and cache > 0
            and token_cleanup
            and memory_cleanup
        )
        findings = []
        if not api_contract.get("user_matches"):
            findings.append(
                {
                    "id": "MS-API-KEY-SUBJECT-ATTRIBUTION-001",
                    "summary": f"{group} API token caller could not preserve documented external user_id",
                    "code_location": "api/apps/restful_apis/memory_api.py:add_message",
                }
            )
        if not passed and not findings:
            findings.append(
                {
                    "id": "MS-RAW-WRITE-FIELD-CONTRACT-001",
                    "summary": f"{group} raw write fields/task/cache contract was incomplete",
                    "code_location": "api/db/joint_services/memory_message_service.py:queue_save_to_memory_task",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_raw_memory_and_ordinary_api_token",
                    "memory_code": fixture["response"]["code"],
                    "credential_code": credential.get("code"),
                    "credential_fingerprint": DB._fingerprint(api_token),
                },
                {
                    "name": "write_api_token_and_jwt_messages",
                    "responses": [
                        [api_add.get("http_status"), api_add.get("code")],
                        [jwt_add.get("http_status"), jwt_add.get("code")],
                    ],
                    "raw_sha256": [api_add.get("raw_sha256"), jwt_add.get("raw_sha256")],
                },
                {
                    "name": "read_only_full_field_vector_tokenization_and_attribution_verification",
                    "backend": snapshot.get("backend"),
                    "raw_count": len(snapshot.get("rows", [])),
                    "api_subject_preserved": api_contract.get("user_matches"),
                    "jwt_spoof_rejected": jwt_contract.get("user_matches"),
                    "task_delta": task_delta,
                    "size_cache_positive": isinstance(cache, int) and cache > 0,
                    "raw_sha256": snapshot.get("raw_sha256"),
                },
                {
                    "name": "cleanup_credential_and_memory_through_apis",
                    "credential_cleanup_succeeded": token_cleanup,
                    "memory_cleanup_succeeded": memory_cleanup,
                },
            ],
            "oracle": {
                "responses": [[200, 0], [200, 0]],
                "api_token_user_id": "user-001",
                "jwt_user_id": "current_authenticated_user",
                "raw_count": 2,
                "task_delta": 2,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_ms002() -> dict[str, Any]:
    case_id = "TC-MS-002"
    prefix = "fresh-ms-002"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        first = MS._create_memory_fixture(case_id, group, owner, "create_fanout_memory_a", f"{prefix}-{group}-a", memory_type=["raw"])
        second = MS._create_memory_fixture(case_id, group, owner, "create_fanout_memory_b", f"{prefix}-{group}-b", memory_type=["raw"])
        memory_ids = [first["id"], second["id"]]
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        expected = "User Input: remember guava preference\nAgent Response: stored in both memories"
        before_tasks = sum(_task_count(group, item) for item in memory_ids if item)
        response = _add_message(
            case_id,
            group,
            owner["auth"],
            "fanout_add_to_two_memories",
            memory_ids,
            agent_id=agent,
            session_id=session,
            user_input="remember guava preference",
            agent_response="stored in both memories",
        )
        snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_fanout_rows",
            owner["tenant_id"],
            memory_ids,
            agent_ids=[agent],
            expected_contents={agent: expected},
        )
        rows = snapshot.get("rows", [])
        counts = [sum(row.get("memory_id") == item for row in rows) for item in memory_ids]
        after_tasks = sum(_task_count(group, item) for item in memory_ids if item)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], memory_ids)
        observed = {
            "http_status": response.get("http_status"),
            "code": response.get("code"),
            "message": response.get("message"),
            "memory_counts": counts,
            "memory_ids_match": {row.get("memory_id") for row in rows} == set(memory_ids),
            "contents_match": len(rows) == 2 and all(row.get("content_matches") for row in rows),
            "message_ids_distinct": len({row.get("message_id") for row in rows}) == 2,
            "task_delta": after_tasks - before_tasks,
            "cleanup_succeeded": cleanup,
        }
        passed = preclean and fanout_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_two_raw_memories", "codes": [first["response"]["code"], second["response"]["code"]]},
                {"name": "fanout_write_one_payload", "response": [response.get("http_status"), response.get("code")], "raw_sha256": response.get("raw_sha256")},
                {
                    "name": "read_only_two_backend_rows_and_task_verification",
                    **{k: observed[k] for k in ("memory_counts", "memory_ids_match", "contents_match", "message_ids_distinct", "task_delta")},
                    "backend": snapshot.get("backend"),
                    "raw_sha256": snapshot.get("raw_sha256"),
                },
                {"name": "cleanup_two_memories_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"memory_counts": [1, 1], "message_ids": "distinct", "task_delta": 2},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-FANOUT-WRITE-001",
                    "summary": f"{group} fanout write did not create two isolated raw rows",
                    "code_location": "api/db/joint_services/memory_message_service.py:queue_save_to_memory_task",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms003() -> dict[str, Any]:
    case_id = "TC-MS-003"
    prefix = "fresh-ms-003"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
        memory_id = fixture["id"]
        pairs = [
            (f"{prefix}-{group}-alpha", f"{prefix}-{group}-session-alpha", "alpha input", "alpha response"),
            (f"{prefix}-{group}-beta", f"{prefix}-{group}-session-beta", "beta input", "beta response"),
        ]
        before_tasks = _task_count(group, memory_id) if memory_id else 0
        responses = [
            _add_message(case_id, group, owner["auth"], f"add_distinct_{index}", [memory_id], agent_id=agent, session_id=session, user_input=user_input, agent_response=agent_response)
            for index, (agent, session, user_input, agent_response) in enumerate(pairs, 1)
        ]
        expected = {agent: f"User Input: {user_input}\nAgent Response: {agent_response}" for agent, _session, user_input, agent_response in pairs}
        snapshot = _store_snapshot(case_id, group, "read_only_distinct_agent_session_rows", owner["tenant_id"], [memory_id], agent_ids=[item[0] for item in pairs], expected_contents=expected)
        rows = snapshot.get("rows", [])
        pairs_match = {(row.get("agent_id"), row.get("session_id")) for row in rows} == {(agent, session) for agent, session, _input, _response in pairs}
        task_delta = _task_count(group, memory_id) - before_tasks if memory_id else 0
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id])
        passed = preclean and all(item.get("code") == 0 for item in responses) and len(rows) == 2 and pairs_match and all(row.get("content_matches") for row in rows) and task_delta == 2 and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_raw_memory_and_write_two_agent_session_pairs", "codes": [item.get("code") for item in responses], "raw_sha256": [item.get("raw_sha256") for item in responses]},
                {"name": "read_only_exact_agent_session_storage_verification", "raw_count": len(rows), "pairs_match": pairs_match, "task_delta": task_delta, "raw_sha256": snapshot.get("raw_sha256")},
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"raw_count": 2, "agent_session_pairs": "exact", "task_delta": 2},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-AGENT-SESSION-WRITE-001",
                    "summary": f"{group} did not preserve distinct agent/session fields",
                    "code_location": "api/db/joint_services/memory_message_service.py:queue_save_to_memory_task",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_single_content_case(
    case_id: str,
    prefix: str,
    user_input: str,
    agent_response: str,
    finding_id: str,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        result = _single_write(
            case_id,
            group,
            owner,
            prefix=prefix,
            agent_id=agent,
            session_id=session,
            user_input=user_input,
            agent_response=agent_response,
        )
        row = result["row"]
        contract = _row_contract(row, memory_id=result["fixture"]["id"], agent_id=agent, session_id=session, user_id=owner["tenant_id"])
        passed = (
            result["preclean"]
            and result["response"].get("code") == 0
            and len(result["snapshot"].get("rows", [])) == 1
            and raw_document_contract_ok(contract)
            and result["task_delta"] == 1
            and isinstance(result["cache"], int)
            and result["cache"] > 0
            and result["cleanup"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_raw_memory_and_write_content_variant",
                    "response": [result["response"].get("http_status"), result["response"].get("code")],
                    "raw_sha256": result["response"].get("raw_sha256"),
                },
                {
                    "name": "read_only_exact_content_segmentation_and_vector_verification",
                    "backend": result["snapshot"].get("backend"),
                    "raw_count": len(result["snapshot"].get("rows", [])),
                    "content_matches": contract.get("content_matches"),
                    "segmented_content_nonempty": contract.get("tokenized_nonempty"),
                    "vector_dimension": row.get("vector_dimension"),
                    "task_delta": result["task_delta"],
                    "raw_sha256": result["snapshot"].get("raw_sha256"),
                },
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": result["cleanup"]},
            ],
            "oracle": {"response": [200, 0], "raw_count": 1, "content": "exact", "vector": "real"},
            "findings": []
            if passed
            else [{"id": finding_id, "summary": f"{group} content variant did not round-trip through Memory Store", "code_location": "api/db/joint_services/memory_message_service.py:embed_and_save"}],
        }

    return _run_case(case_id, execute)


def run_ms004() -> dict[str, Any]:
    return _run_single_content_case("TC-MS-004", "fresh-ms-004", "我喜欢在汤里放香菜", "已记录您对香菜的偏好", "MS-CHINESE-WRITE-001")


def run_ms005() -> dict[str, Any]:
    return _run_single_content_case(
        "TC-MS-005", "fresh-ms-005", 'test \' OR 1=1 -- and "quotes" and 100% discount', "value with ; DROP TABLE users; -- and backslash \\ end", "MS-SPECIAL-CHAR-WRITE-001"
    )


def run_ms006() -> dict[str, Any]:
    return _run_single_content_case("TC-MS-006", "fresh-ms-006", "I love this food 🍜👍🔥", "Noted your love for ramen 🍜", "MS-EMOJI-WRITE-001")


def run_ms007() -> dict[str, Any]:
    case_id = "TC-MS-007"
    prefix = "fresh-ms-007"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        head = f"{prefix}-{group}-HEAD-"
        tail = f"-{prefix}-{group}-TAIL"
        user_input = head + ("lorem ipsum " * 415) + "U" + tail
        agent_response = head + ("dolor sit amet " * 356) + "A" + tail
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        result = _single_write(
            case_id,
            group,
            owner,
            prefix=prefix,
            agent_id=agent,
            session_id=session,
            user_input=user_input,
            agent_response=agent_response,
            request_timeout=LONG_CONTENT_REQUEST_TIMEOUT_SECONDS,
            snapshot_timeout=LONG_CONTENT_REQUEST_TIMEOUT_SECONDS,
        )
        row = result["row"]
        observed = {
            "http_status": result["response"].get("http_status"),
            "code": result["response"].get("code"),
            "stored_character_count": row.get("content_character_count"),
            "expected_character_count": len(result["expected_content"]),
            "prefix_matches": row.get("prefix_matches"),
            "suffix_matches": row.get("suffix_matches"),
            "tokenized_nonempty": row.get("segmented_content_nonempty"),
            "cleanup_succeeded": result["cleanup"],
        }
        passed = result["preclean"] and result["task_delta"] == 1 and long_content_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "generate_unique_long_inputs_and_write_via_api",
                    "input_lengths": [len(user_input), len(agent_response)],
                    "response": [observed["http_status"], observed["code"]],
                    "raw_sha256": result["response"].get("raw_sha256"),
                },
                {
                    "name": "read_only_exact_character_length_and_boundary_marker_verification",
                    "stored_character_count": observed["stored_character_count"],
                    "expected_character_count": observed["expected_character_count"],
                    "prefix_matches": observed["prefix_matches"],
                    "suffix_matches": observed["suffix_matches"],
                    "raw_sha256": result["snapshot"].get("raw_sha256"),
                },
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": result["cleanup"]},
            ],
            "oracle": {"content_length": "exact Python character count", "prefix_suffix": "exact"},
            "findings": []
            if passed
            else [{"id": "MS-LONG-CONTENT-WRITE-001", "summary": f"{group} long content was rejected or truncated", "code_location": "memory/utils/gaussdb_conn.py:_message_to_row"}],
        }

    return _run_case(case_id, execute)


def run_ms008() -> dict[str, Any]:
    case_id = "TC-MS-008"
    prefix = "fresh-ms-008"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
        memory_id = fixture["id"]
        agent = f"{prefix}-{group}-agent"
        before_tasks = _task_count(group, memory_id) if memory_id else 0
        before_cache = _cache_value(group, memory_id) if memory_id else None
        response = _request(
            case_id,
            group,
            "reject_missing_user_input",
            owner["auth"],
            "POST",
            "/messages",
            payload={"memory_id": [memory_id], "agent_id": agent, "session_id": f"{prefix}-{group}-session", "agent_response": "some response"},
        )
        snapshot = _store_snapshot(case_id, group, "read_only_missing_field_zero_rows", owner["tenant_id"], [memory_id], agent_ids=[agent])
        after_tasks = _task_count(group, memory_id) if memory_id else 0
        after_cache = _cache_value(group, memory_id) if memory_id else None
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id])
        passed = (
            preclean
            and response.get("http_status") == 200
            and response.get("code") == 101
            and snapshot.get("raw_count") == 0
            and after_tasks - before_tasks == 0
            and before_cache == after_cache
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "submit_message_without_user_input", "response": [response.get("http_status"), response.get("code")], "raw_sha256": response.get("raw_sha256")},
                {
                    "name": "read_only_zero_store_task_cache_side_effect_verification",
                    "raw_delta": snapshot.get("raw_count"),
                    "task_delta": after_tasks - before_tasks,
                    "cache_unchanged": before_cache == after_cache,
                    "raw_sha256": snapshot.get("raw_sha256"),
                },
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 101], "raw_delta": 0, "task_delta": 0, "cache_delta": 0},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-MISSING-USER-INPUT-VALIDATION-001",
                    "summary": f"{group} missing user_input was not rejected without side effects",
                    "code_location": "api/apps/restful_apis/memory_api.py:add_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms009() -> dict[str, Any]:
    case_id = "TC-MS-009"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        missing_id = uuid.uuid4().hex
        proof = MS._memory_snapshot(group, missing_id)
        agent = f"fresh-ms-009-{group}-agent"
        before_tasks = _task_count(group, missing_id)
        before_cache = _cache_value(group, missing_id)
        before = _store_snapshot(case_id, group, "read_only_nonexistent_before", owner["tenant_id"], [missing_id], agent_ids=[agent])
        response = _add_message(
            case_id,
            group,
            owner["auth"],
            "write_to_proven_nonexistent_memory",
            [missing_id],
            agent_id=agent,
            session_id=f"fresh-ms-009-{group}-session",
            user_input="test input",
            agent_response="test response",
        )
        after = _store_snapshot(case_id, group, "read_only_nonexistent_after", owner["tenant_id"], [missing_id], agent_ids=[agent])
        after_tasks = _task_count(group, missing_id)
        after_cache = _cache_value(group, missing_id)
        observed = {
            "http_status": response.get("http_status"),
            "code": response.get("code"),
            "message": response.get("message"),
            "raw_delta": after.get("raw_count", 0) - before.get("raw_count", 0),
            "task_delta": after_tasks - before_tasks,
            "cache_delta": int(after_cache is not None) - int(before_cache is not None),
            "cleanup_succeeded": proof.get("count") == 0 and MS._memory_snapshot(group, missing_id).get("count") == 0,
        }
        passed = rejected_add_contract_ok(observed, expected_code=500, expected_message="Some messages failed to add. Detail:Memory not found.")
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "prove_random_memory_id_absent_before_request", "metadata_count": proof.get("count"), "store_raw_count": before.get("raw_count")},
                {
                    "name": "reject_write_to_nonexistent_memory",
                    "response": [response.get("http_status"), response.get("code")],
                    "message_matches": response.get("message") == "Some messages failed to add. Detail:Memory not found.",
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "read_only_zero_store_task_cache_side_effect_verification",
                    "raw_delta": observed["raw_delta"],
                    "task_delta": observed["task_delta"],
                    "cache_delta": observed["cache_delta"],
                    "raw_sha256": after.get("raw_sha256"),
                },
            ],
            "oracle": {"response": [200, 500], "message": "Some messages failed to add. Detail:Memory not found.", "deltas": {"store": 0, "task": 0, "cache": 0}},
            "findings": []
            if passed
            else [{"id": "MS-NONEXISTENT-MEMORY-WRITE-001", "summary": f"{group} nonexistent memory write contract differed", "code_location": "api/apps/services/memory_api_service.py:add_message"}],
        }

    return _run_case(case_id, execute)


def run_ms010() -> dict[str, Any]:
    case_id = "TC-MS-010"
    prefix = "fresh-ms-010"
    email = "ms-010-user-b@fresh.invalid"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
        memory_id = fixture["id"]
        user_b = MS._prepare_tagged_secondary_user(
            case_id,
            group,
            "cross_tenant_user_b",
            email,
            secondary_fixture_password(case_id),
        )
        agent = f"{prefix}-{group}-cross-agent"
        before = _store_snapshot(case_id, group, "read_only_cross_tenant_before", owner["tenant_id"], [memory_id], agent_ids=[agent])
        before_tasks = _task_count(group, memory_id) if memory_id else 0
        before_cache = _cache_value(group, memory_id) if memory_id else None
        response = _add_message(
            case_id,
            group,
            user_b["auth"],
            "reject_cross_tenant_write",
            [memory_id],
            agent_id=agent,
            session_id=f"{prefix}-{group}-cross-session",
            user_input="cross tenant write",
            agent_response="should be rejected",
        )
        after = _store_snapshot(case_id, group, "read_only_cross_tenant_after", owner["tenant_id"], [memory_id], agent_ids=[agent])
        after_tasks = _task_count(group, memory_id) if memory_id else 0
        after_cache = _cache_value(group, memory_id) if memory_id else None
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id])
        user_cleanup = MS._cleanup_tagged_secondary_user(case_id, group, "cross_tenant_user_b", email)
        observed = {
            "http_status": response.get("http_status"),
            "code": response.get("code"),
            "message": response.get("message"),
            "raw_delta": after.get("raw_count", 0) - before.get("raw_count", 0),
            "task_delta": after_tasks - before_tasks,
            "cache_delta": int(after_cache is not None) - int(before_cache is not None),
            "cleanup_succeeded": memory_cleanup and user_cleanup["succeeded"],
        }
        passed = preclean and user_b["login"].get("code") == 0 and rejected_add_contract_ok(observed, expected_code=500, expected_message="Some messages failed to add. Detail:Memory not found.")
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_owner_memory_and_independent_user_b", "memory_code": fixture["response"]["code"], "user_login_code": user_b["login"].get("code")},
                {
                    "name": "reject_cross_tenant_message_write",
                    "response": [response.get("http_status"), response.get("code")],
                    "message_matches": response.get("message") == "Some messages failed to add. Detail:Memory not found.",
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "read_only_zero_store_task_cache_side_effect_verification",
                    "raw_delta": observed["raw_delta"],
                    "task_delta": observed["task_delta"],
                    "cache_delta": observed["cache_delta"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {"name": "cleanup_memory_and_secondary_user_through_apis", "memory_cleanup_succeeded": memory_cleanup, "user_cleanup_succeeded": user_cleanup["succeeded"]},
            ],
            "oracle": {"response": [200, 500], "message": "Some messages failed to add. Detail:Memory not found.", "deltas": {"store": 0, "task": 0, "cache": 0}},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-CROSS-TENANT-WRITE-ACL-001",
                    "summary": f"{group} cross-tenant write was not safely rejected",
                    "code_location": "api/apps/services/memory_api_service.py:_filter_accessible_memories",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms011() -> dict[str, Any]:
    case_id = "TC-MS-011"
    prefix = "fresh-ms-011"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        result = _single_write(
            case_id, group, owner, prefix=prefix, agent_id=agent, session_id=session, user_input="upsert original input", agent_response="upsert original response", cleanup_after_snapshot=False
        )
        rows = result["snapshot"].get("rows", [])
        message_id = int(rows[0]["message_id"]) if len(rows) == 1 else 0
        updated_content = f"User Input: {prefix}-{group}-updated\nAgent Response: adapter upsert latest value"
        upsert = (
            _adapter_upsert(case_id, group, "adapter_same_id_upsert", owner["tenant_id"], result["fixture"]["id"], message_id, updated_content)
            if message_id
            else {"before_count": 0, "adapter_error_count": 1, "after_count": 0}
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [result["fixture"]["id"]])
        upsert["cleanup_succeeded"] = cleanup
        passed = result["preclean"] and result["response"].get("code") == 0 and upsert_contract_ok(upsert)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_original_api_message_and_capture_actual_id",
                    "response": [result["response"].get("http_status"), result["response"].get("code")],
                    "captured_id_positive": message_id > 0,
                    "raw_sha256": result["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "plan_authorized_adapter_upsert_same_composite_id",
                    "backend": upsert.get("backend"),
                    "adapter_error_count": upsert.get("adapter_error_count"),
                    "before_count": upsert.get("before_count"),
                    "after_count": upsert.get("after_count"),
                    "id_unchanged": upsert.get("id_unchanged"),
                    "updated_content_matches": upsert.get("updated_content_matches"),
                    "raw_sha256": upsert.get("raw_sha256"),
                },
                {"name": "cleanup_dedicated_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"row_count": 1, "id": "unchanged", "content": "latest adapter value"},
            "findings": [] if passed else [{"id": "MS-ADAPTER-UPSERT-001", "summary": f"{group} same-id adapter upsert was not atomic", "code_location": "memory/utils/gaussdb_conn.py:insert"}],
        }

    return _run_case(case_id, execute)


def run_ms012() -> dict[str, Any]:
    case_id = "TC-MS-012"
    prefix = "fresh-ms-012"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        result = _single_write(
            case_id,
            group,
            owner,
            prefix=prefix,
            agent_id=agent,
            session_id=session,
            user_input="coriander is great for health",
            agent_response="Yes, coriander has many benefits",
            cleanup_after_snapshot=False,
        )
        rows = result["snapshot"].get("rows", [])
        message_id = int(rows[0]["message_id"]) if len(rows) == 1 else 0
        search = _request(
            case_id,
            group,
            "semantic_search_for_tokenized_term",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[("memory_id", result["fixture"]["id"]), ("query", "coriander"), ("similarity_threshold", "0.0"), ("keywords_similarity_weight", "1.0"), ("top_n", "5")],
            timeout=240,
        )
        search_rows = search.get("data") if isinstance(search.get("data"), list) else []
        semantic_hit = any(int(item.get("message_id") or 0) == message_id for item in search_rows if isinstance(item, dict))
        physical_match = rows[0].get("physical_segmentation_matches") if len(rows) == 1 else (None if group == "control" else False)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [result["fixture"]["id"]])
        observed = {
            "http_status": search.get("http_status"),
            "code": search.get("code"),
            "physical_tokenized_matches": physical_match,
            "semantic_search_hit": semantic_hit,
            "cleanup_succeeded": cleanup,
        }
        passed = result["preclean"] and result["response"].get("code") == 0 and tokenizer_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_coriander_message_and_capture_raw_row",
                    "response": [result["response"].get("http_status"), result["response"].get("code")],
                    "message_id_positive": message_id > 0,
                    "raw_sha256": result["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "compare_current_write_segmentation_and_backend_semantic_hit",
                    "physical_segmentation_matches": physical_match,
                    "semantic_search_hit": semantic_hit,
                    "search_response": [search.get("http_status"), search.get("code")],
                    "raw_sha256": search.get("raw_sha256"),
                },
                {"name": "cleanup_memory_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"experiment_physical_tokenized": "fine_grained_tokenize(tokenize(content))", "control_physical_tokenized": "not applicable", "semantic_search_hit": True},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-TOKENIZER-CONSISTENCY-001",
                    "summary": f"{group} write/query tokenizer chain did not produce a semantic hit",
                    "code_location": "memory/utils/gaussdb_conn.py:normalize_fulltext_query",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms100() -> dict[str, Any]:
    case_id = "TC-MS-100"
    prefix = "fresh-ms-100"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent-{index}",
                "session_id": f"{prefix}-{group}-session",
                "user_input": f"list input {index}",
                "agent_response": f"list response {index}",
            }
            for index in range(1, 4)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs, inter_write_delay=1.05)
        response = _request(
            case_id,
            group,
            "list_messages_with_default_parameters",
            owner["auth"],
            "GET",
            f"/memories/{batch['memory_id']}",
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = _list_observation(response, batch["rows"], {batch["memory_id"]}, cleanup)
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 3 and batch["task_delta"] == 3 and list_messages_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_three_distinct_raw_messages_via_api",
                    "successful_writes": sum(response.get("code") == 0 for response in batch["responses"]),
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "task_delta": batch["task_delta"],
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "list_default_raw_messages_and_validate_shape_order",
                    "response": [observed["http_status"], observed["code"]],
                    "returned_count": observed["returned_count"],
                    "total_count": observed["total_count"],
                    "extract_lists_present": observed["extract_lists_present"],
                    "valid_at_nonincreasing": observed["valid_at_nonincreasing"],
                    "valid_at_distinct": observed["valid_at_distinct"],
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "raw_count": 3,
                "storage_type": "table",
                "extract": [],
                "order": "valid_at DESC",
            },
            "findings": [] if passed else [list_failure_finding(group, observed, "default", "MS-LIST-DEFAULT-001")],
        }

    return _run_case(case_id, execute)


def run_ms101() -> dict[str, Any]:
    case_id = "TC-MS-101"
    prefix = "fresh-ms-101"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        agent_a = f"{prefix}-{group}-agent-a"
        agent_b = f"{prefix}-{group}-agent-b"
        specs = [
            {
                "agent_id": agent,
                "session_id": f"{prefix}-{group}-session-{index}",
                "user_input": f"agent filter input {index}",
                "agent_response": f"agent filter response {index}",
            }
            for index, agent in enumerate([agent_a, agent_b, agent_a, agent_b], 1)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs, inter_write_delay=1.05)
        expected = [row for row in batch["rows"] if row.get("agent_id") == agent_a]
        response = _request(
            case_id,
            group,
            "list_messages_filtered_by_agent_id",
            owner["auth"],
            "GET",
            f"/memories/{batch['memory_id']}",
            params={"agent_id": agent_a},
        )
        returned, _total, _storage = _list_payload(response)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = _list_observation(response, expected, {batch["memory_id"]}, cleanup)
        agent_exact = all(row.get("agent_id") == agent_a for row in returned)
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 4 and len(expected) == 2 and agent_exact and list_messages_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_messages_for_each_of_two_agents",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "target_agent_count": len(expected),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "list_with_exact_agent_id_filter",
                    "response": [observed["http_status"], observed["code"]],
                    "returned_count": observed["returned_count"],
                    "total_count": observed["total_count"],
                    "agent_filter_exact": agent_exact,
                    "valid_at_nonincreasing": observed["valid_at_nonincreasing"],
                    "valid_at_distinct": observed["valid_at_distinct"],
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {"agent_filter": "exact", "returned_count": 2},
            "findings": [] if passed else [list_failure_finding(group, observed, "agent_id", "MS-LIST-AGENT-FILTER-001")],
        }

    return _run_case(case_id, execute)


def run_ms102() -> dict[str, Any]:
    case_id = "TC-MS-102"
    prefix = "fresh-ms-102"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        session_x = f"{prefix}-{group}-session-x"
        session_y = f"{prefix}-{group}-session-y"
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent",
                "session_id": session,
                "user_input": f"session filter input {index}",
                "agent_response": f"session filter response {index}",
            }
            for index, session in enumerate([session_x, session_y], 1)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        expected = [row for row in batch["rows"] if row.get("session_id") == session_x]
        response = _request(
            case_id,
            group,
            "list_messages_keywords_mapped_to_session_id",
            owner["auth"],
            "GET",
            f"/memories/{batch['memory_id']}",
            params={"keywords": session_x},
        )
        returned, _total, _storage = _list_payload(response)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = _list_observation(response, expected, {batch["memory_id"]}, cleanup)
        session_exact = all(row.get("session_id") == session_x for row in returned)
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 2 and len(expected) == 1 and session_exact and list_messages_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_messages_with_two_exact_session_ids",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "map_keywords_to_exact_session_filter",
                    "response": [observed["http_status"], observed["code"]],
                    "returned_count": observed["returned_count"],
                    "session_filter_exact": session_exact,
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {"keywords_semantics": "exact session_id", "returned_count": 1},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-LIST-SESSION-FILTER-001",
                    "summary": f"{group} keywords did not act as an exact session_id filter",
                    "code_location": "memory/services/messages.py:list_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms103() -> dict[str, Any]:
    case_id = "TC-MS-103"
    prefix = "fresh-ms-103"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent-{index:02d}",
                "session_id": f"{prefix}-{group}-session",
                "user_input": f"pagination input {index}",
                "agent_response": f"pagination response {index}",
            }
            for index in range(1, 11)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs, inter_write_delay=1.05)
        page_1 = _request(
            case_id,
            group,
            "list_page_1_size_3",
            owner["auth"],
            "GET",
            f"/memories/{batch['memory_id']}",
            params={"page": 1, "page_size": 3},
        )
        page_2 = _request(
            case_id,
            group,
            "list_page_2_size_3",
            owner["auth"],
            "GET",
            f"/memories/{batch['memory_id']}",
            params={"page": 2, "page_size": 3},
        )
        rows_1, total_1, _storage_1 = _list_payload(page_1)
        rows_2, total_2, _storage_2 = _list_payload(page_2)
        ids_1 = _message_id_set(rows_1)
        ids_2 = _message_id_set(rows_2)
        fixture_ids = _message_id_set(batch["rows"])
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "responses": [
                [page_1.get("http_status"), page_1.get("code")],
                [page_2.get("http_status"), page_2.get("code")],
            ],
            "page_lengths": [len(rows_1), len(rows_2)],
            "total_counts": [total_1, total_2],
            "pages_disjoint": not bool(ids_1 & ids_2),
            "page_ids_from_fixture": bool(ids_1 | ids_2) and ids_1 | ids_2 <= fixture_ids,
            "valid_at_distinct_across_pages": _valid_at_distinct(rows_1 + rows_2),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            batch["preclean"]
            and batch["writes_succeeded"]
            and batch["snapshot"].get("raw_count") == 10
            and batch["task_delta"] == 10
            and _valid_at_nonincreasing(rows_1)
            and _valid_at_nonincreasing(rows_2)
            and pagination_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_ten_raw_messages_via_api",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "task_delta": batch["task_delta"],
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "read_two_three_item_pages",
                    "responses": observed["responses"],
                    "page_lengths": observed["page_lengths"],
                    "total_counts": observed["total_counts"],
                    "pages_disjoint": observed["pages_disjoint"],
                    "page_ids_from_fixture": observed["page_ids_from_fixture"],
                    "valid_at_distinct_across_pages": observed["valid_at_distinct_across_pages"],
                    "raw_sha256": [
                        page_1.get("raw_sha256"),
                        page_2.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "page_lengths": [3, 3],
                "total_counts": [10, 10],
                "offset": "(page-1)*page_size",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-LIST-PAGINATION-001",
                    "summary": f"{group} raw message pagination returned overlapping or incomplete pages",
                    "code_location": "memory/services/messages.py:list_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms104() -> dict[str, Any]:
    case_id = "TC-MS-104"
    prefix = "fresh-ms-104"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        target_agent = f"{prefix}-{group}-agent-a"
        target_session = f"{prefix}-{group}-session-a"
        specs = [
            {
                "agent_id": target_agent,
                "session_id": target_session,
                "user_input": f"recent target input {index}",
                "agent_response": f"recent target response {index}",
            }
            for index in range(1, 7)
        ]
        specs.append(
            {
                "agent_id": f"{prefix}-{group}-agent-b",
                "session_id": f"{prefix}-{group}-session-b",
                "user_input": "recent noise input",
                "agent_response": "recent noise response",
            }
        )
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs, inter_write_delay=1.05)
        matching_fixture_ids = {int(row.get("message_id") or 0) for row in batch["rows"] if row.get("agent_id") == target_agent and row.get("session_id") == target_session}
        response = _request(
            case_id,
            group,
            "get_five_recent_messages_for_agent_and_session",
            owner["auth"],
            "GET",
            "/messages",
            params=[
                ("memory_id", batch["memory_id"]),
                ("agent_id", target_agent),
                ("session_id", target_session),
                ("limit", "5"),
            ],
        )
        rows = _recent_payload(response)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "http_status": response.get("http_status"),
            "code": response.get("code"),
            "returned_count": len(rows),
            "limit": 5,
            "agent_ids_exact": all(row.get("agent_id") == target_agent for row in rows),
            "session_ids_exact": all(row.get("session_id") == target_session for row in rows),
            "memory_ids_exact": all(row.get("memory_id") == batch["memory_id"] for row in rows),
            "message_ids_from_fixture": _message_id_set(rows) <= matching_fixture_ids,
            "valid_at_nonincreasing": _valid_at_nonincreasing(rows),
            "valid_at_distinct": _valid_at_distinct(rows),
            "forgotten_absent": all(row.get("forget_at") in (None, "", "-") for row in rows),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 7 and len(matching_fixture_ids) == 6 and recent_messages_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_six_matching_and_one_nonmatching_raw_messages",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "matching_fixture_count": len(matching_fixture_ids),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "get_filtered_recent_messages_with_limit_five",
                    "response": [observed["http_status"], observed["code"]],
                    "returned_count": observed["returned_count"],
                    "filters_exact": observed["agent_ids_exact"] and observed["session_ids_exact"] and observed["memory_ids_exact"],
                    "valid_at_nonincreasing": observed["valid_at_nonincreasing"],
                    "valid_at_distinct": observed["valid_at_distinct"],
                    "forgotten_absent": observed["forgotten_absent"],
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "maximum_count": 5,
                "filters": ["memory_id", "agent_id", "session_id"],
                "order": "valid_at DESC",
                "hide_forgotten": True,
            },
            "findings": [] if passed else [recent_failure_finding(group, observed)],
        }

    return _run_case(case_id, execute)


def run_ms105() -> dict[str, Any]:
    case_id = "TC-MS-105"
    prefix = "fresh-ms-105"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        active_agent = f"{prefix}-{group}-active"
        forgotten_agent = f"{prefix}-{group}-forgotten"
        disabled_agent = f"{prefix}-{group}-disabled"
        specs = [
            {
                "agent_id": agent,
                "session_id": f"{prefix}-{group}-session",
                "user_input": f"visibility input {index}",
                "agent_response": f"visibility response {index}",
            }
            for index, agent in enumerate([active_agent, forgotten_agent, disabled_agent], 1)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        forgotten_id = ids_by_agent.get(forgotten_agent, 0)
        disabled_id = ids_by_agent.get(disabled_agent, 0)
        forget = _request(
            case_id,
            group,
            "forget_one_raw_message",
            owner["auth"],
            "DELETE",
            f"/messages/{batch['memory_id']}:{forgotten_id}",
        )
        disable = _request(
            case_id,
            group,
            "disable_one_raw_message",
            owner["auth"],
            "PUT",
            f"/messages/{batch['memory_id']}:{disabled_id}",
            payload={"status": False},
        )
        recent = _request(
            case_id,
            group,
            "get_recent_after_forget_and_disable",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", batch["memory_id"]), ("limit", "10")],
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_forgotten_disabled_physical_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        physical_by_id = {int(row.get("message_id") or 0): row for row in physical.get("rows", [])}
        recent_rows = _recent_payload(recent)
        recent_ids = _message_id_set(recent_rows)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "responses": [
                [forget.get("http_status"), forget.get("code")],
                [disable.get("http_status"), disable.get("code")],
                [recent.get("http_status"), recent.get("code")],
            ],
            "physical_raw_count": physical.get("raw_count"),
            "physical_forgotten": forgotten_id > 0 and physical_by_id.get(forgotten_id, {}).get("forget_at_is_null") is False,
            "physical_disabled": disabled_id > 0 and physical_by_id.get(disabled_id, {}).get("status") is False,
            "recent_count": len(recent_rows),
            "forgotten_absent": forgotten_id > 0 and forgotten_id not in recent_ids,
            "disabled_present": disabled_id > 0 and disabled_id in recent_ids,
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and forgotten_id > 0 and disabled_id > 0 and recent_visibility_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_three_raw_messages_and_capture_ids",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": forgotten_id > 0 and disabled_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "forget_one_and_disable_one_through_message_apis",
                    "responses": observed["responses"][:2],
                    "physical_forgotten": observed["physical_forgotten"],
                    "physical_disabled": observed["physical_disabled"],
                    "raw_sha256": [
                        forget.get("raw_sha256"),
                        disable.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "recent_hides_forgotten_but_keeps_disabled",
                    "response": observed["responses"][2],
                    "recent_count": observed["recent_count"],
                    "forgotten_absent": observed["forgotten_absent"],
                    "disabled_present": observed["disabled_present"],
                    "raw_sha256": recent.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "physical_raw_count": 3,
                "forget_at": "hidden by recent",
                "status_false": "still returned by current implementation",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-RECENT-VISIBILITY-001",
                    "summary": f"{group} recent visibility did not hide forgotten while retaining disabled rows",
                    "code_location": "memory/services/messages.py:get_recent_messages",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms106() -> dict[str, Any]:
    case_id = "TC-MS-106"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        response = _request(
            case_id,
            group,
            "get_recent_without_memory_id",
            owner["auth"],
            "GET",
            "/messages",
        )
        passed = response.get("http_status") == 200 and response.get("code") == 101 and response.get("message") == "memory_ids is required."
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "request_recent_messages_without_memory_id",
                    "response": [response.get("http_status"), response.get("code")],
                    "message_exact": response.get("message") == "memory_ids is required.",
                    "raw_sha256": response.get("raw_sha256"),
                }
            ],
            "oracle": {
                "response": [200, 101],
                "message": "memory_ids is required.",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-RECENT-MEMORY-REQUIRED-001",
                    "summary": f"{group} recent endpoint did not enforce memory_id",
                    "code_location": "api/apps/restful_apis/memory_api.py:get_messages",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms107() -> dict[str, Any]:
    case_id = "TC-MS-107"
    prefix = "fresh-ms-107"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture_a = MS._create_memory_fixture(
            case_id,
            group,
            owner,
            "create_recent_fanout_memory_a",
            f"{prefix}-{group}-a",
            memory_type=["raw"],
        )
        fixture_b = MS._create_memory_fixture(
            case_id,
            group,
            owner,
            "create_recent_fanout_memory_b",
            f"{prefix}-{group}-b",
            memory_type=["raw"],
        )
        memory_ids = [str(fixture_a.get("id") or ""), str(fixture_b.get("id") or "")]
        before_tasks = sum(_task_count(group, memory_id) for memory_id in memory_ids)
        write = _add_message(
            case_id,
            group,
            owner["auth"],
            "fanout_one_message_to_two_memories",
            memory_ids,
            agent_id=f"{prefix}-{group}-agent",
            session_id=f"{prefix}-{group}-session",
            user_input="multi memory recent input",
            agent_response="multi memory recent response",
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_two_memory_raw_snapshot",
            owner["tenant_id"],
            memory_ids,
        )
        after_tasks = sum(_task_count(group, memory_id) for memory_id in memory_ids)
        recent = _request(
            case_id,
            group,
            "get_recent_from_two_memory_ids",
            owner["auth"],
            "GET",
            "/messages",
            params=[
                ("memory_id", memory_ids[0]),
                ("memory_id", memory_ids[1]),
                ("limit", "10"),
            ],
        )
        recent_rows = _recent_payload(recent)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], memory_ids)
        observed = {
            "response": [recent.get("http_status"), recent.get("code")],
            "returned_count": len(recent_rows),
            "memory_ids_exact": {str(row.get("memory_id") or "") for row in recent_rows} == set(memory_ids),
            "message_ids_exact": _message_id_set(recent_rows) == _message_id_set(physical.get("rows", [])),
            "valid_at_nonincreasing": _valid_at_nonincreasing(recent_rows),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            preclean
            and all(memory_ids)
            and write.get("http_status") == 200
            and write.get("code") == 0
            and physical.get("raw_count") == 2
            and after_tasks - before_tasks == 2
            and observed["response"] == [200, 0]
            and observed["returned_count"] == 2
            and observed["memory_ids_exact"]
            and observed["message_ids_exact"]
            and observed["valid_at_nonincreasing"]
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_memories_and_fanout_one_api_message",
                    "write_response": [write.get("http_status"), write.get("code")],
                    "physical_raw_count": physical.get("raw_count"),
                    "task_delta": after_tasks - before_tasks,
                    "raw_sha256": [
                        write.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "get_recent_with_repeated_memory_id_parameters",
                    "response": observed["response"],
                    "returned_count": observed["returned_count"],
                    "memory_ids_exact": observed["memory_ids_exact"],
                    "message_ids_exact": observed["message_ids_exact"],
                    "valid_at_nonincreasing": observed["valid_at_nonincreasing"],
                    "raw_sha256": recent.get("raw_sha256"),
                },
                {
                    "name": "cleanup_two_memories_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "memory_id_set": "both requested memories",
                "raw_count": 2,
                "order": "valid_at DESC",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-RECENT-MULTI-MEMORY-001",
                    "summary": f"{group} recent endpoint did not merge both accessible memories",
                    "code_location": "memory/services/messages.py:get_recent_messages",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms108() -> dict[str, Any]:
    case_id = "TC-MS-108"
    prefix = "fresh-ms-108"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean, fixture = _preclean_and_create(case_id, group, owner, prefix)
        memory_id = str(fixture.get("id") or "")
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_new_memory_has_zero_raw_rows",
            owner["tenant_id"],
            [memory_id],
        )
        response = _request(
            case_id,
            group,
            "list_new_empty_memory",
            owner["auth"],
            "GET",
            f"/memories/{memory_id}",
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id])
        observed = _list_observation(response, [], {memory_id}, cleanup)
        passed = preclean and fixture["response"].get("code") == 0 and physical.get("raw_count") == 0 and list_messages_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_new_raw_memory_and_prove_zero_store_rows",
                    "create_code": fixture["response"].get("code"),
                    "physical_raw_count": physical.get("raw_count"),
                    "raw_sha256": physical.get("raw_sha256"),
                },
                {
                    "name": "list_empty_memory",
                    "response": [observed["http_status"], observed["code"]],
                    "returned_count": observed["returned_count"],
                    "total_count": observed["total_count"],
                    "storage_type": observed["storage_type"],
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "message_list": [],
                "total_count": 0,
                "storage_type": "table",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-LIST-EMPTY-001",
                    "summary": f"{group} empty Memory list response differed from the stable shape",
                    "code_location": "memory/services/messages.py:list_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms109() -> dict[str, Any]:
    case_id = "TC-MS-109"
    prefix = "fresh-ms-109"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent-{index:02d}",
                "session_id": f"{prefix}-{group}-session",
                "user_input": f"limit input {index}",
                "agent_response": f"limit response {index}",
            }
            for index in range(1, 12)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        baseline = _request(
            case_id,
            group,
            "get_recent_with_limit_10",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", batch["memory_id"]), ("limit", "10")],
        )
        huge = _request(
            case_id,
            group,
            "get_recent_with_huge_limit",
            owner["auth"],
            "GET",
            "/messages",
            params=[
                ("memory_id", batch["memory_id"]),
                ("limit", "100000"),
            ],
        )
        invalid = _request(
            case_id,
            group,
            "get_recent_with_non_integer_limit",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", batch["memory_id"]), ("limit", "abc")],
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_raw_snapshot_after_limit_requests",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "baseline": [baseline.get("http_status"), baseline.get("code")],
            "baseline_count": len(_recent_payload(baseline)),
            "huge": [huge.get("http_status"), huge.get("code")],
            "huge_count": len(_recent_payload(huge)),
            "invalid": [invalid.get("http_status"), invalid.get("code")],
            "invalid_exposes_conversion_detail": "invalid literal" in str(invalid.get("message") or "").lower() or "valueerror" in str(invalid.get("message") or "").lower(),
            "raw_unchanged": _raw_snapshot_identity(batch["rows"]) == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 11 and after.get("raw_count") == 11 and recent_limit_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_eleven_raw_messages_via_api",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "compare_normal_huge_and_non_integer_limits",
                    "baseline_response": observed["baseline"],
                    "baseline_count": observed["baseline_count"],
                    "huge_response": observed["huge"],
                    "huge_count": observed["huge_count"],
                    "invalid_response": observed["invalid"],
                    "invalid_exposes_conversion_detail": observed["invalid_exposes_conversion_detail"],
                    "raw_sha256": [
                        baseline.get("raw_sha256"),
                        huge.get("raw_sha256"),
                        invalid.get("raw_sha256"),
                    ],
                },
                {
                    "name": "prove_read_only_limit_requests_left_store_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "baseline": [200, 0, 10],
                "huge_limit": [200, 101],
                "non_integer_limit": [200, 101],
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-RECENT-LIMIT-VALIDATION-001",
                    "summary": f"{group} recent limit lacked an upper bound or standardized argument error",
                    "code_location": "api/apps/restful_apis/memory_api.py:get_messages",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_semantic_search_case(
    case_id: str,
    prefix: str,
    *,
    user_input: str,
    agent_response: str,
    query: str,
    keywords_weight: float,
    finding_id: str,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        result = _single_write(
            case_id,
            group,
            owner,
            prefix=prefix,
            agent_id=agent,
            session_id=session,
            user_input=user_input,
            agent_response=agent_response,
            cleanup_after_snapshot=False,
        )
        physical_rows = result["snapshot"].get("rows", [])
        target_id = int(physical_rows[0].get("message_id") or 0) if len(physical_rows) == 1 else 0
        search = _request(
            case_id,
            group,
            "search_public_fusion_for_target_term",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", result["fixture"]["id"]),
                ("query", query),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", str(keywords_weight)),
                ("top_n", "5"),
            ],
            timeout=240,
        )
        search_rows = _recent_payload(search)
        target_rows = [row for row in search_rows if int(row.get("message_id") or 0) == target_id and target_id > 0]
        cleanup = _cleanup_memories(
            case_id,
            group,
            owner["auth"],
            [result["fixture"]["id"]],
        )
        observed = {
            "write": [
                result["response"].get("http_status"),
                result["response"].get("code"),
            ],
            "search": [search.get("http_status"), search.get("code")],
            "physical_raw_count": result["snapshot"].get("raw_count"),
            "target_hit": len(target_rows) == 1,
            "target_content_matches": len(target_rows) == 1 and str(target_rows[0].get("content") or "") == result["expected_content"],
            "cleanup_succeeded": cleanup,
        }
        passed = result["preclean"] and semantic_search_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_unique_raw_search_fixture_via_api",
                    "response": observed["write"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "target_id_positive": target_id > 0,
                    "raw_sha256": result["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "search_public_text_dense_fusion_path",
                    "response": observed["search"],
                    "returned_count": len(search_rows),
                    "target_hit": observed["target_hit"],
                    "target_content_matches": observed["target_content_matches"],
                    "raw_sha256": search.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "target_message": "present with exact content",
                "path": "text+dense+weighted_sum",
            },
            "findings": []
            if passed
            else [
                {
                    "id": finding_id,
                    "summary": f"{group} public fusion search did not return the exact target message",
                    "code_location": "api/db/joint_services/memory_message_service.py:query_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms200() -> dict[str, Any]:
    return _run_semantic_search_case(
        "TC-MS-200",
        "fresh-ms-200",
        user_input="coriander is great for health",
        agent_response="coriander has many useful nutrients",
        query="coriander",
        keywords_weight=0.7,
        finding_id="MS-ENGLISH-FUSION-SEARCH-001",
    )


def run_ms201() -> dict[str, Any]:
    return _run_semantic_search_case(
        "TC-MS-201",
        "fresh-ms-201",
        user_input="我喜欢在汤里放香菜",
        agent_response="已记录您对香菜的偏好",
        query="香菜",
        keywords_weight=0.7,
        finding_id="MS-CHINESE-FUSION-SEARCH-001",
    )


def run_ms202() -> dict[str, Any]:
    case_id = "TC-MS-202"
    prefix = "fresh-ms-202"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        agent = f"{prefix}-{group}-agent"
        session = f"{prefix}-{group}-session"
        result = _single_write(
            case_id,
            group,
            owner,
            prefix=prefix,
            agent_id=agent,
            session_id=session,
            user_input="coriander soup recipe",
            agent_response="a coriander soup recipe was recorded",
            cleanup_after_snapshot=False,
        )
        rows = result["snapshot"].get("rows", [])
        target_id = int(rows[0].get("message_id") or 0) if len(rows) == 1 else 0
        search = _request(
            case_id,
            group,
            "search_with_keyword_weight_one",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", result["fixture"]["id"]),
                ("query", "coriander"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "1.0"),
                ("top_n", "5"),
            ],
            timeout=240,
        )
        search_rows = _recent_payload(search)
        cleanup = _cleanup_memories(
            case_id,
            group,
            owner["auth"],
            [result["fixture"]["id"]],
        )
        physical_match = rows[0].get("physical_segmentation_matches") if len(rows) == 1 else (None if group == "control" else False)
        observed = {
            "http_status": search.get("http_status"),
            "code": search.get("code"),
            "physical_tokenized_matches": physical_match,
            "semantic_search_hit": target_id > 0 and target_id in _message_id_set(search_rows),
            "cleanup_succeeded": cleanup,
        }
        passed = result["preclean"] and result["response"].get("code") == 0 and result["snapshot"].get("raw_count") == 1 and tokenizer_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_coriander_recipe_and_capture_current_segmentation",
                    "response": [
                        result["response"].get("http_status"),
                        result["response"].get("code"),
                    ],
                    "physical_raw_count": result["snapshot"].get("raw_count"),
                    "raw_sha256": result["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "compare_write_query_tokenizer_chain_by_semantic_hit",
                    "physical_segmentation_matches": physical_match,
                    "semantic_search_hit": observed["semantic_search_hit"],
                    "search_response": [search.get("http_status"), search.get("code")],
                    "raw_sha256": search.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "experiment_physical_tokenized": "current write/query tokenizer functions match",
                "control_analyzer": "equivalent semantic hit",
                "semantic_search_hit": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-TOKENIZER-CONSISTENCY-001",
                    "summary": f"{group} write/query tokenizer paths did not produce an exact target hit",
                    "code_location": "memory/utils/gaussdb_conn.py:normalize_fulltext_query",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms203() -> dict[str, Any]:
    case_id = "TC-MS-203"
    prefix = "fresh-ms-203"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        target_agent = f"{prefix}-{group}-target"
        specs = [
            {
                "agent_id": target_agent,
                "session_id": f"{prefix}-{group}-session",
                "user_input": "coriander is a healthy herb used in soup",
                "agent_response": "the coriander message is relevant",
            },
            {
                "agent_id": f"{prefix}-{group}-other",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "satellite telemetry and orbital mechanics",
                "agent_response": "the unrelated message is distinct",
            },
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        fixture_ids = _message_id_set(batch["rows"])
        target_ids = {int(row.get("message_id") or 0) for row in batch["rows"] if row.get("agent_id") == target_agent}
        public = _request(
            case_id,
            group,
            "public_fusion_search_before_dense_probe",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", batch["memory_id"]),
                ("query", "coriander"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "0.0"),
                ("top_n", "5"),
            ],
            timeout=240,
        )
        public_ids = _message_id_set(_recent_payload(public))
        adapter = _adapter_search_probe(
            case_id,
            group,
            "read_only_pure_dense_score_probe",
            owner["tenant_id"],
            [batch["memory_id"]],
            "coriander",
            mode="dense",
            similarity_threshold=0.0,
            top_n=5,
        )
        adapter_ids = {int(row.get("message_id") or 0) for row in adapter.get("rows", []) if int(row.get("message_id") or 0) > 0}
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "public_search": [public.get("http_status"), public.get("code")],
            "public_target_hit": bool(target_ids) and target_ids <= public_ids,
            "adapter_result_count": adapter.get("result_count"),
            "query_dimension_positive": int(adapter.get("query_dimension") or 0) > 0,
            "scores_finite": adapter.get("scores_finite"),
            "scores_nonincreasing": adapter.get("scores_nonincreasing"),
            "score_recomputation_matches": adapter.get("score_recomputation_matches"),
            "target_ids_exact": bool(target_ids) and target_ids <= adapter_ids and adapter_ids <= fixture_ids,
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 2 and pure_vector_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_distinguishable_real_embedding_messages",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "fixture_ids_exact": len(fixture_ids) == 2,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "prove_relevant_message_on_public_fusion_path",
                    "response": observed["public_search"],
                    "target_hit": observed["public_target_hit"],
                    "raw_sha256": public.get("raw_sha256"),
                },
                {
                    "name": "read_only_pure_dense_score_and_order_verification",
                    "backend": adapter.get("backend"),
                    "query_dimension": adapter.get("query_dimension"),
                    "score_tolerance": adapter.get("score_tolerance"),
                    "adapter_result_count": adapter.get("result_count"),
                    "scores": [row.get("score") for row in adapter.get("rows", [])],
                    "scores_finite": observed["scores_finite"],
                    "scores_nonincreasing": observed["scores_nonincreasing"],
                    "score_recomputation_matches": observed["score_recomputation_matches"],
                    "raw_sha256": adapter.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "public_score_field": "not exposed",
                "adapter_score": "cosine similarity",
                "experiment_sql": "1 - cosine_distance",
                "query_dimension": "actual Ollama embedding dimension",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PURE-DENSE-COSINE-001",
                    "summary": f"{group} pure dense score or ordering did not match cosine recomputation",
                    "code_location": "memory/utils/gaussdb_conn.py:_build_vector_search_sql",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms204() -> dict[str, Any]:
    case_id = "TC-MS-204"
    prefix = "fresh-ms-204"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        target_agent = f"{prefix}-{group}-target"
        other_agent = f"{prefix}-{group}-other"
        specs = [
            {
                "agent_id": target_agent,
                "session_id": f"{prefix}-{group}-session",
                "user_input": "coriander empty vector target",
                "agent_response": "target vector fixture",
            },
            {
                "agent_id": other_agent,
                "session_id": f"{prefix}-{group}-session",
                "user_input": "coriander retained vector other",
                "agent_response": "other vector remains real",
            },
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        target_id = ids_by_agent.get(target_agent, 0)
        other_id = ids_by_agent.get(other_agent, 0)
        fixture = _empty_vector_fixture_probe(
            case_id,
            group,
            "plan_authorized_empty_vector_fixture",
            owner["tenant_id"],
            batch["memory_id"],
            target_id,
        )
        dense = _adapter_search_probe(
            case_id,
            group,
            "read_only_dense_search_after_empty_fixture",
            owner["tenant_id"],
            [batch["memory_id"]],
            "coriander",
            mode="dense",
            similarity_threshold=0.0,
            top_n=10,
        )
        dense_ids = {int(row.get("message_id") or 0) for row in dense.get("rows", []) if int(row.get("message_id") or 0) > 0}
        excluded_id = int(fixture.get("excluded_message_id") or 0)
        expected_present = other_id if group == "experiment" else target_id
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "fixture_action_succeeded": fixture.get("fixture_action_succeeded"),
            "target_empty_flag": fixture.get("target_empty_flag"),
            "target_absent_from_dense_results": excluded_id > 0 and excluded_id not in dense_ids,
            "other_message_present": expected_present > 0 and expected_present in dense_ids,
            "fixture_residue_count": fixture.get("fixture_residue_count"),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            batch["preclean"]
            and batch["writes_succeeded"]
            and batch["snapshot"].get("raw_count") == 2
            and target_id > 0
            and other_id > 0
            and fixture.get("original_rows_preserved") is True
            and empty_vector_contract_ok(group, observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_real_vector_messages_and_capture_ids",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": target_id > 0 and other_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "apply_plan_authorized_backend_specific_empty_vector_fixture",
                    "backend": fixture.get("backend"),
                    "fixture_action_succeeded": observed["fixture_action_succeeded"],
                    "fixture_rejected": fixture.get("fixture_rejected"),
                    "target_empty_flag": observed["target_empty_flag"],
                    "original_rows_preserved": fixture.get("original_rows_preserved"),
                    "fixture_residue_count": observed["fixture_residue_count"],
                    "raw_sha256": fixture.get("raw_sha256"),
                },
                {
                    "name": "read_only_pure_dense_empty_vector_exclusion",
                    "adapter_result_count": dense.get("result_count"),
                    "excluded_id_absent": observed["target_absent_from_dense_results"],
                    "real_message_present": observed["other_message_present"],
                    "raw_sha256": dense.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "experiment": "q_<dim>_vec_empty=true row excluded",
                "control": "native insert without content_embed rejected with no residue",
                "existing_rows": "preserved",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-EMPTY-VECTOR-EXCLUSION-001",
                    "summary": f"{group} empty-vector fixture was accepted into dense matching or damaged existing rows",
                    "code_location": "memory/utils/gaussdb_conn.py:_build_vector_search_sql",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms205() -> dict[str, Any]:
    case_id = "TC-MS-205"
    prefix = "fresh-ms-205"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-repetition",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "coriander coriander coriander unrelated sequence",
                "agent_response": "repeated keyword candidate",
            },
            {
                "agent_id": f"{prefix}-{group}-semantic",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "coriander is a healthy herb used in soup recipes",
                "agent_response": "semantic herb candidate",
            },
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        fixture_ids = _message_id_set(batch["rows"])
        weights = [0.0, 0.5, 1.0]
        api_results: dict[float, dict[str, Any]] = {}
        adapter_results: dict[float, dict[str, Any]] = {}
        for weight in weights:
            label = str(weight).replace(".", "_")
            api_results[weight] = _request(
                case_id,
                group,
                f"public_fusion_weight_{label}",
                owner["auth"],
                "GET",
                "/messages/search",
                params=[
                    ("memory_id", batch["memory_id"]),
                    ("query", "coriander"),
                    ("similarity_threshold", "0.0"),
                    ("keywords_similarity_weight", str(weight)),
                    ("top_n", "5"),
                ],
                timeout=240,
            )
            adapter_results[weight] = _adapter_search_probe(
                case_id,
                group,
                f"read_only_fusion_score_weight_{label}",
                owner["tenant_id"],
                [batch["memory_id"]],
                "coriander",
                mode="fusion",
                similarity_threshold=0.0,
                keywords_weight=weight,
                top_n=5,
            )
        api_responses = [[api_results[weight].get("http_status"), api_results[weight].get("code")] for weight in weights]
        api_id_sets = {weight: _message_id_set(_recent_payload(api_results[weight])) for weight in weights}
        configured = {weight: [float(value) for value in adapter_results[weight].get("configured_weights", [])] for weight in weights}
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "api_responses": api_responses,
            "all_target_hits": all(bool(api_id_sets[weight]) and api_id_sets[weight] <= fixture_ids for weight in weights),
            "adapter_results_nonempty": all(int(adapter_results[weight].get("result_count") or 0) > 0 for weight in weights),
            "scores_finite_and_ordered": all(adapter_results[weight].get("scores_finite") is True and adapter_results[weight].get("scores_nonincreasing") is True for weight in weights),
            "keyword_weight_mapping_correct": keyword_weight_mapping_ok(configured),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 2 and fusion_weight_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_text_and_vector_distinguishable_candidates",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "fixture_ids_exact": len(fixture_ids) == 2,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "run_public_fusion_at_zero_half_and_one",
                    "responses": api_responses,
                    "returned_counts": [len(api_id_sets[weight]) for weight in weights],
                    "all_results_from_fixture": observed["all_target_hits"],
                    "raw_sha256": [api_results[weight].get("raw_sha256") for weight in weights],
                },
                {
                    "name": "read_only_adapter_fusion_scores_and_weight_mapping",
                    "backend": adapter_results[0.5].get("backend"),
                    "configured_text_vector_weights": {str(weight): configured[weight] for weight in weights},
                    "result_counts": [adapter_results[weight].get("result_count") for weight in weights],
                    "scores_finite_and_ordered": observed["scores_finite_and_ordered"],
                    "keyword_weight_mapping_correct": observed["keyword_weight_mapping_correct"],
                    "raw_sha256": [adapter_results[weight].get("raw_sha256") for weight in weights],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "public_parameter": "keywords_similarity_weight",
                "expected_weights": "text=parameter,vector=1-parameter",
                "adapter_score": "weighted_sum",
                "api_score_field": "not exposed",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-KEYWORD-WEIGHT-DIRECTION-001",
                    "summary": f"{group} public keyword weight was wired as vector weight",
                    "code_location": "api/db/joint_services/memory_message_service.py:query_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms206() -> dict[str, Any]:
    case_id = "TC-MS-206"
    prefix = "fresh-ms-206"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent-{index:02d}",
                "session_id": f"{prefix}-{group}-session",
                "user_input": f"test top n candidate {index:02d}",
                "agent_response": f"test response {index:02d}",
            }
            for index in range(1, 11)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        fixture_ids = _message_id_set(batch["rows"])
        before_identity = _raw_snapshot_identity(batch["rows"])
        legal = _request(
            case_id,
            group,
            "public_search_top_n_3",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", batch["memory_id"]),
                ("query", "test"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "0.7"),
                ("top_n", "3"),
            ],
            timeout=240,
        )
        invalid_values = [("zero", "0"), ("negative", "-1"), ("text", "abc")]
        invalid_results: list[dict[str, Any]] = []
        for label, value in invalid_values:
            invalid_results.append(
                _request(
                    case_id,
                    group,
                    f"public_search_invalid_top_n_{label}",
                    owner["auth"],
                    "GET",
                    "/messages/search",
                    params=[
                        ("memory_id", batch["memory_id"]),
                        ("query", "test"),
                        ("similarity_threshold", "0.0"),
                        ("keywords_similarity_weight", "0.7"),
                        ("top_n", value),
                    ],
                    timeout=240,
                )
            )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_top_n_requests",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        legal_rows = _recent_payload(legal)
        legal_ids = _message_id_set(legal_rows)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "legal_responses": [[legal.get("http_status"), legal.get("code")]],
            "legal_limits_ok": len(legal_rows) <= 3 and legal_ids <= fixture_ids,
            "invalid_responses": [[result.get("http_status"), result.get("code")] for result in invalid_results],
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 10 and len(fixture_ids) == 10 and search_parameter_contract_ok(observed, invalid_count=3)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_ten_search_candidates_via_api",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_id_count": len(fixture_ids),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "search_with_legal_top_n_three",
                    "response": observed["legal_responses"][0],
                    "returned_count": len(legal_rows),
                    "at_most_three": observed["legal_limits_ok"],
                    "raw_sha256": legal.get("raw_sha256"),
                },
                {
                    "name": "reject_zero_negative_and_non_integer_top_n",
                    "responses": observed["invalid_responses"],
                    "returned_counts": [len(_recent_payload(result)) for result in invalid_results],
                    "raw_sha256": [result.get("raw_sha256") for result in invalid_results],
                },
                {
                    "name": "prove_search_requests_left_store_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "legal_top_n_3": "HTTP 200 / code=0 and at most three rows",
                "invalid_top_n": "HTTP 200 / code=101 for 0, -1, and abc",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-TOP-N-VALIDATION-001",
                    "summary": f"{group} search did not reject every non-positive or non-integer top_n with code 101",
                    "code_location": "api/apps/restful_apis/memory_api.py:search_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms207() -> dict[str, Any]:
    case_id = "TC-MS-207"
    prefix = "fresh-ms-207"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "test similarity threshold boundary",
                "agent_response": "test threshold response",
            }
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        fixture_ids = _message_id_set(batch["rows"])
        before_identity = _raw_snapshot_identity(batch["rows"])
        legal_results: list[dict[str, Any]] = []
        for label, value in (("zero", "0"), ("one", "1.0")):
            legal_results.append(
                _request(
                    case_id,
                    group,
                    f"public_search_threshold_{label}",
                    owner["auth"],
                    "GET",
                    "/messages/search",
                    params=[
                        ("memory_id", batch["memory_id"]),
                        ("query", "test"),
                        ("similarity_threshold", value),
                        ("keywords_similarity_weight", "0.0"),
                        ("top_n", "100"),
                    ],
                    timeout=240,
                )
            )
        invalid_results: list[dict[str, Any]] = []
        for label, value in (("negative", "-1.0"), ("above_one", "2.0"), ("text", "abc")):
            invalid_results.append(
                _request(
                    case_id,
                    group,
                    f"public_search_invalid_threshold_{label}",
                    owner["auth"],
                    "GET",
                    "/messages/search",
                    params=[
                        ("memory_id", batch["memory_id"]),
                        ("query", "test"),
                        ("similarity_threshold", value),
                        ("keywords_similarity_weight", "0.0"),
                        ("top_n", "100"),
                    ],
                    timeout=240,
                )
            )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_threshold_requests",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        legal_rows = [_recent_payload(result) for result in legal_results]
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "legal_responses": [[result.get("http_status"), result.get("code")] for result in legal_results],
            "legal_limits_ok": all(len(rows) <= 100 and _message_id_set(rows) <= fixture_ids for rows in legal_rows),
            "invalid_responses": [[result.get("http_status"), result.get("code")] for result in invalid_results],
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 1 and len(fixture_ids) == 1 and search_parameter_contract_ok(observed, invalid_count=3)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_single_threshold_candidate_via_api",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "search_at_legal_zero_and_one_boundaries",
                    "responses": observed["legal_responses"],
                    "returned_counts": [len(rows) for rows in legal_rows],
                    "legal_limits_ok": observed["legal_limits_ok"],
                    "raw_sha256": [result.get("raw_sha256") for result in legal_results],
                },
                {
                    "name": "reject_thresholds_below_zero_above_one_and_non_numeric",
                    "responses": observed["invalid_responses"],
                    "raw_sha256": [result.get("raw_sha256") for result in invalid_results],
                },
                {
                    "name": "prove_threshold_requests_left_store_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "legal_thresholds": "0 and 1 return HTTP 200 / code=0",
                "invalid_thresholds": "-1, 2, and abc return HTTP 200 / code=101",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-THRESHOLD-VALIDATION-001",
                    "summary": f"{group} search did not enforce the public similarity_threshold range with code 101",
                    "code_location": "api/apps/restful_apis/memory_api.py:search_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms208() -> dict[str, Any]:
    case_id = "TC-MS-208"
    prefix = "fresh-ms-208"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-keyword",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "test test test literal keyword candidate",
                "agent_response": "literal search candidate",
            },
            {
                "agent_id": f"{prefix}-{group}-semantic",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "evaluation checks and software quality",
                "agent_response": "semantic test candidate",
            },
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        fixture_ids = _message_id_set(batch["rows"])
        before_identity = _raw_snapshot_identity(batch["rows"])
        weights = [0.0, 0.5, 1.0]
        legal_results: dict[float, dict[str, Any]] = {}
        adapter_results: dict[float, dict[str, Any]] = {}
        for weight in weights:
            label = str(weight).replace(".", "_")
            legal_results[weight] = _request(
                case_id,
                group,
                f"public_search_keyword_weight_{label}",
                owner["auth"],
                "GET",
                "/messages/search",
                params=[
                    ("memory_id", batch["memory_id"]),
                    ("query", "test"),
                    ("similarity_threshold", "0.0"),
                    ("keywords_similarity_weight", str(weight)),
                    ("top_n", "5"),
                ],
                timeout=240,
            )
            adapter_results[weight] = _adapter_search_probe(
                case_id,
                group,
                f"read_only_keyword_weight_score_{label}",
                owner["tenant_id"],
                [batch["memory_id"]],
                "test",
                mode="fusion",
                similarity_threshold=0.0,
                keywords_weight=weight,
                top_n=5,
            )
        invalid_results: list[dict[str, Any]] = []
        for label, value in (("negative", "-0.1"), ("above_one", "1.1"), ("text", "abc")):
            invalid_results.append(
                _request(
                    case_id,
                    group,
                    f"public_search_invalid_keyword_weight_{label}",
                    owner["auth"],
                    "GET",
                    "/messages/search",
                    params=[
                        ("memory_id", batch["memory_id"]),
                        ("query", "test"),
                        ("similarity_threshold", "0.0"),
                        ("keywords_similarity_weight", value),
                        ("top_n", "5"),
                    ],
                    timeout=240,
                )
            )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_keyword_weight_requests",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        legal_rows = {weight: _recent_payload(legal_results[weight]) for weight in weights}
        configured = {weight: [float(value) for value in adapter_results[weight].get("configured_weights", [])] for weight in weights}
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        parameter_observed = {
            "legal_responses": [
                [
                    legal_results[weight].get("http_status"),
                    legal_results[weight].get("code"),
                ]
                for weight in weights
            ],
            "legal_limits_ok": all(len(legal_rows[weight]) <= 5 and _message_id_set(legal_rows[weight]) <= fixture_ids for weight in weights),
            "invalid_responses": [[result.get("http_status"), result.get("code")] for result in invalid_results],
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        fusion_observed = {
            "api_responses": parameter_observed["legal_responses"],
            "all_target_hits": all(bool(_message_id_set(legal_rows[weight])) and _message_id_set(legal_rows[weight]) <= fixture_ids for weight in weights),
            "adapter_results_nonempty": all(int(adapter_results[weight].get("result_count") or 0) > 0 for weight in weights),
            "scores_finite_and_ordered": all(adapter_results[weight].get("scores_finite") is True and adapter_results[weight].get("scores_nonincreasing") is True for weight in weights),
            "keyword_weight_mapping_correct": keyword_weight_mapping_ok(configured),
            "cleanup_succeeded": cleanup,
        }
        parameter_ok = search_parameter_contract_ok(parameter_observed, invalid_count=3)
        fusion_ok = fusion_weight_contract_ok(fusion_observed)
        passed = batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 2 and len(fixture_ids) == 2 and parameter_ok and fusion_ok
        findings: list[dict[str, str]] = []
        if not parameter_ok:
            findings.append(
                {
                    "id": "MS-SEARCH-WEIGHT-VALIDATION-001",
                    "summary": f"{group} search did not enforce the public keywords_similarity_weight range with code 101",
                    "code_location": "api/apps/restful_apis/memory_api.py:search_message",
                }
            )
        if not fusion_observed["keyword_weight_mapping_correct"]:
            findings.append(
                {
                    "id": "MS-KEYWORD-WEIGHT-DIRECTION-001",
                    "summary": f"{group} public keyword weight was wired as vector weight",
                    "code_location": "api/db/joint_services/memory_message_service.py:query_message",
                }
            )
        if not passed and not findings:
            findings.append(
                {
                    "id": "MS-SEARCH-WEIGHT-EXECUTION-001",
                    "summary": f"{group} legal keyword-weight search or evidence invariant failed",
                    "code_location": "api/db/joint_services/memory_message_service.py:query_message",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_weight_distinguishable_candidates_via_api",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_id_count": len(fixture_ids),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "search_at_legal_zero_half_and_one_weights",
                    "responses": parameter_observed["legal_responses"],
                    "returned_message_id_orders": [[int(row.get("message_id") or 0) for row in legal_rows[weight]] for weight in weights],
                    "legal_limits_ok": parameter_observed["legal_limits_ok"],
                    "raw_sha256": [legal_results[weight].get("raw_sha256") for weight in weights],
                },
                {
                    "name": "capture_raw_adapter_scores_and_weight_mapping",
                    "backend": adapter_results[0.5].get("backend"),
                    "configured_text_vector_weights": {str(weight): configured[weight] for weight in weights},
                    "raw_score_orders": [[row.get("score") for row in adapter_results[weight].get("rows", [])] for weight in weights],
                    "scores_finite_and_ordered": fusion_observed["scores_finite_and_ordered"],
                    "keyword_weight_mapping_correct": fusion_observed["keyword_weight_mapping_correct"],
                    "raw_sha256": [adapter_results[weight].get("raw_sha256") for weight in weights],
                },
                {
                    "name": "reject_out_of_range_and_non_numeric_keyword_weights",
                    "responses": parameter_observed["invalid_responses"],
                    "raw_sha256": [result.get("raw_sha256") for result in invalid_results],
                },
                {
                    "name": "prove_keyword_weight_requests_left_store_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": parameter_observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "legal_weights": "0, 0.5, and 1 return HTTP 200 / code=0",
                "invalid_weights": "-0.1, 1.1, and abc return HTTP 200 / code=101",
                "weight_order": "text_weight=parameter, vector_weight=1-parameter",
                "store_mutation": 0,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_ms209() -> dict[str, Any]:
    case_id = "TC-MS-209"
    prefix = "fresh-ms-209"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture_a = MS._create_memory_fixture(
            case_id,
            group,
            owner,
            "create_search_memory_a",
            f"{prefix}-{group}-a",
            memory_type=["raw"],
        )
        fixture_b = MS._create_memory_fixture(
            case_id,
            group,
            owner,
            "create_search_memory_b",
            f"{prefix}-{group}-b",
            memory_type=["raw"],
        )
        memory_ids = [
            str(fixture_a.get("id") or ""),
            str(fixture_b.get("id") or ""),
        ]
        writes = [
            _add_message(
                case_id,
                group,
                owner["auth"],
                f"write_guava_memory_{label}",
                [memory_id],
                agent_id=f"{prefix}-{group}-agent-{label}",
                session_id=f"{prefix}-{group}-session",
                user_input=f"guava preference from memory {label}",
                agent_response=f"guava response from memory {label}",
            )
            for label, memory_id in zip(("a", "b"), memory_ids)
        ]
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_two_memory_search_fixture",
            owner["tenant_id"],
            memory_ids,
        )
        search = _request(
            case_id,
            group,
            "search_guava_across_two_memories",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", memory_ids[0]),
                ("memory_id", memory_ids[1]),
                ("query", "guava"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "0.7"),
                ("top_n", "10"),
            ],
            timeout=240,
        )
        search_rows = _recent_payload(search)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], memory_ids)
        observed = {
            "writes": [[response.get("http_status"), response.get("code")] for response in writes],
            "search": [search.get("http_status"), search.get("code")],
            "physical_raw_count": physical.get("raw_count"),
            "returned_memory_ids_exact": {str(row.get("memory_id") or "") for row in search_rows} == set(memory_ids),
            "returned_message_ids_exact": _message_id_set(search_rows) == _message_id_set(physical.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = preclean and all(memory_ids) and fixture_a["response"].get("code") == 0 and fixture_b["response"].get("code") == 0 and multi_memory_search_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_memories_and_write_one_guava_message_each",
                    "create_codes": [
                        fixture_a["response"].get("code"),
                        fixture_b["response"].get("code"),
                    ],
                    "write_responses": observed["writes"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "raw_sha256": [
                        *(response.get("raw_sha256") for response in writes),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "search_with_two_repeated_memory_id_parameters",
                    "response": observed["search"],
                    "returned_count": len(search_rows),
                    "returned_memory_ids_exact": observed["returned_memory_ids_exact"],
                    "returned_message_ids_exact": observed["returned_message_ids_exact"],
                    "raw_sha256": search.get("raw_sha256"),
                },
                {
                    "name": "cleanup_two_memories_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "memory_id_set": "both requested memories",
                "message_id_set": "both newly written guava rows",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-MULTI-MEMORY-001",
                    "summary": f"{group} search did not return the exact guava rows from both requested memories",
                    "code_location": "api/db/joint_services/memory_message_service.py:query_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms210() -> dict[str, Any]:
    case_id = "TC-MS-210"
    prefix = "fresh-ms-210"
    email = "ms-210-user-b@fresh.invalid"
    password = secondary_fixture_password(case_id)

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        # A pre-existing secondary fixture is removed through the admin API.
        # Admin login uses the same superuser as the dataset owner and rotates
        # that user's access token, so always refresh the owner session before
        # continuing with business API requests.
        owner = _owner(case_id, group)
        membership = BASE._invite_and_accept_member(case_id, group, owner, secondary, email)
        fixture = MS._create_memory_fixture(
            case_id,
            group,
            owner,
            "create_team_filter_memory",
            f"{prefix}-{group}",
            memory_type=["raw"],
        )
        memory_id = str(fixture.get("id") or "")
        permission = MS._update_memory(
            case_id,
            group,
            owner["auth"],
            "set_filter_memory_team_permission",
            memory_id,
            {"permissions": "team"},
        )
        permission_snapshot = MS._memory_snapshot(group, memory_id)
        agent_a = f"{prefix}-{group}-agent-a"
        agent_b = f"{prefix}-{group}-agent-b"
        session_a = f"{prefix}-{group}-session-a"
        session_b = f"{prefix}-{group}-session-b"
        writes = [
            _add_message(
                case_id,
                group,
                owner["auth"],
                "owner_write_agent_a_session_a",
                [memory_id],
                agent_id=agent_a,
                session_id=session_a,
                user_input="test owner agent a session a",
                agent_response="owner filter candidate one",
            ),
            _add_message(
                case_id,
                group,
                owner["auth"],
                "owner_write_agent_a_session_b",
                [memory_id],
                agent_id=agent_a,
                session_id=session_b,
                user_input="test owner agent a session b",
                agent_response="owner filter candidate two",
            ),
            _add_message(
                case_id,
                group,
                secondary["auth"],
                "member_write_agent_b_session_a",
                [memory_id],
                agent_id=agent_b,
                session_id=session_a,
                user_input="test member agent b session a",
                agent_response="member filter candidate",
            ),
        ]
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_team_filter_fixture",
            owner["tenant_id"],
            [memory_id],
        )
        search_specs = [
            (
                "search_filter_agent_a",
                [("agent_id", agent_a)],
            ),
            (
                "search_filter_agent_a_session_a",
                [("agent_id", agent_a), ("session_id", session_a)],
            ),
            (
                "search_filter_secondary_user",
                [("user_id", secondary["tenant_id"])],
            ),
        ]
        searches: list[dict[str, Any]] = []
        for label, filters in search_specs:
            searches.append(
                _request(
                    case_id,
                    group,
                    label,
                    owner["auth"],
                    "GET",
                    "/messages/search",
                    params=[
                        ("memory_id", memory_id),
                        ("query", "test"),
                        ("similarity_threshold", "0.0"),
                        ("keywords_similarity_weight", "0.7"),
                        ("top_n", "10"),
                        *filters,
                    ],
                    timeout=240,
                )
            )
        physical_rows = physical.get("rows", [])
        expected_agent_ids = {int(row.get("message_id") or 0) for row in physical_rows if row.get("agent_id") == agent_a}
        expected_agent_session_ids = {int(row.get("message_id") or 0) for row in physical_rows if row.get("agent_id") == agent_a and row.get("session_id") == session_a}
        expected_user_ids = {int(row.get("message_id") or 0) for row in physical_rows if row.get("user_id") == secondary["tenant_id"]}
        returned_rows = [_recent_payload(search) for search in searches]
        membership_snapshot = BASE._membership_snapshot(group, secondary["tenant_id"], owner["tenant_id"])
        membership_cleanup = BASE._remove_secondary_membership(case_id, group, owner["tenant_id"], secondary)
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_id])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        cleanup = membership_cleanup["succeeded"] and memory_cleanup and user_cleanup["succeeded"]
        observed = {
            "writes": [[response.get("http_status"), response.get("code")] for response in writes],
            "searches": [[response.get("http_status"), response.get("code")] for response in searches],
            "physical_raw_count": physical.get("raw_count"),
            "physical_user_ids_exact": len(physical_rows) == 3 and {str(row.get("user_id") or "") for row in physical_rows} == {owner["tenant_id"], secondary["tenant_id"]},
            "agent_filter_exact": len(expected_agent_ids) == 2 and _message_id_set(returned_rows[0]) == expected_agent_ids and all(row.get("agent_id") == agent_a for row in returned_rows[0]),
            "agent_session_filter_exact": len(expected_agent_session_ids) == 1
            and _message_id_set(returned_rows[1]) == expected_agent_session_ids
            and all(row.get("agent_id") == agent_a and row.get("session_id") == session_a for row in returned_rows[1]),
            "user_filter_exact": len(expected_user_ids) == 1
            and _message_id_set(returned_rows[2]) == expected_user_ids
            and all(row.get("user_id") == secondary["tenant_id"] for row in returned_rows[2]),
            "cleanup_succeeded": cleanup,
        }
        setup_ready = (
            preclean
            and secondary["preclean"]
            and secondary["registration"].get("code") == 0
            and secondary["login"].get("code") == 0
            and membership["invite"].get("code") == 0
            and membership["accept"].get("code") == 0
            and membership_snapshot.get("role") == "normal"
            and fixture["response"].get("code") == 0
            and permission.get("code") == 0
            and permission_snapshot.get("permissions") == "team"
        )
        passed = setup_ready and search_filter_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "register_invite_and_accept_legitimate_team_member",
                    "registration_code": secondary["registration"].get("code"),
                    "login_code": secondary["login"].get("code"),
                    "invite_code": membership["invite"].get("code"),
                    "accept_code": membership["accept"].get("code"),
                    "membership_role": membership_snapshot.get("role"),
                },
                {
                    "name": "create_team_memory_and_write_as_two_authenticated_users",
                    "permission_response": [
                        permission.get("http_status"),
                        permission.get("code"),
                    ],
                    "permission_persisted": permission_snapshot.get("permissions") == "team",
                    "write_responses": observed["writes"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "physical_user_ids_exact": observed["physical_user_ids_exact"],
                    "raw_sha256": [
                        permission.get("raw_sha256"),
                        *(response.get("raw_sha256") for response in writes),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "search_by_agent_then_agent_session_then_real_user_id",
                    "responses": observed["searches"],
                    "returned_counts": [len(rows) for rows in returned_rows],
                    "agent_filter_exact": observed["agent_filter_exact"],
                    "agent_session_filter_exact": observed["agent_session_filter_exact"],
                    "user_filter_exact": observed["user_filter_exact"],
                    "raw_sha256": [response.get("raw_sha256") for response in searches],
                },
                {
                    "name": "remove_membership_memory_and_secondary_user_through_apis",
                    "membership_cleanup_succeeded": membership_cleanup["succeeded"],
                    "memory_cleanup_succeeded": memory_cleanup,
                    "user_cleanup_succeeded": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "agent_filter": "exactly two agent_a rows",
                "agent_session_filter": "exactly one agent_a/session_a row",
                "user_filter": "exactly the authenticated secondary user's row",
                "body_user_spoofing": "not used",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-FILTERS-001",
                    "summary": f"{group} agent/session/user search filters did not return the exact authenticated fixtures",
                    "code_location": "api/db/joint_services/memory_message_service.py:query_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms211() -> dict[str, Any]:
    case_id = "TC-MS-211"
    prefix = "fresh-ms-211"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        active_agent = f"{prefix}-{group}-active"
        forgotten_agent = f"{prefix}-{group}-forgotten"
        disabled_agent = f"{prefix}-{group}-disabled"
        specs = [
            {
                "agent_id": agent,
                "session_id": f"{prefix}-{group}-session",
                "user_input": f"test visibility candidate {index}",
                "agent_response": f"test visibility response {index}",
            }
            for index, agent in enumerate((active_agent, forgotten_agent, disabled_agent), 1)
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        active_id = ids_by_agent.get(active_agent, 0)
        forgotten_id = ids_by_agent.get(forgotten_agent, 0)
        disabled_id = ids_by_agent.get(disabled_agent, 0)
        forget = _request(
            case_id,
            group,
            "forget_search_fixture_message",
            owner["auth"],
            "DELETE",
            f"/messages/{batch['memory_id']}:{forgotten_id}",
        )
        disable = _request(
            case_id,
            group,
            "disable_search_fixture_message",
            owner["auth"],
            "PUT",
            f"/messages/{batch['memory_id']}:{disabled_id}",
            payload={"status": False},
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_search_visibility_physical_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        search = _request(
            case_id,
            group,
            "search_default_visibility",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", batch["memory_id"]),
                ("query", "test"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "0.7"),
                ("top_n", "10"),
            ],
            timeout=240,
        )
        physical_by_id = {int(row.get("message_id") or 0): row for row in physical.get("rows", [])}
        search_ids = _message_id_set(_recent_payload(search))
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "mutation_responses": [
                [forget.get("http_status"), forget.get("code")],
                [disable.get("http_status"), disable.get("code")],
            ],
            "search_response": [search.get("http_status"), search.get("code")],
            "physical_raw_count": physical.get("raw_count"),
            "physical_forgotten": forgotten_id > 0 and physical_by_id.get(forgotten_id, {}).get("forget_at_is_null") is False,
            "physical_disabled": disabled_id > 0 and physical_by_id.get(disabled_id, {}).get("status") is False,
            "active_present": active_id > 0 and active_id in search_ids,
            "forgotten_absent": forgotten_id > 0 and forgotten_id not in search_ids,
            "disabled_absent": disabled_id > 0 and disabled_id not in search_ids,
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and all((active_id, forgotten_id, disabled_id)) and search_visibility_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_active_forgotten_and_disabled_search_candidates",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": all((active_id, forgotten_id, disabled_id)),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "forget_one_and_disable_one_through_message_apis",
                    "responses": observed["mutation_responses"],
                    "physical_forgotten": observed["physical_forgotten"],
                    "physical_disabled": observed["physical_disabled"],
                    "raw_sha256": [
                        forget.get("raw_sha256"),
                        disable.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "search_hides_forgotten_and_disabled_by_default",
                    "response": observed["search_response"],
                    "returned_count": len(search_ids),
                    "active_present": observed["active_present"],
                    "forgotten_absent": observed["forgotten_absent"],
                    "disabled_absent": observed["disabled_absent"],
                    "raw_sha256": search.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "physical_raw_count": 3,
                "active": "returned",
                "forget_at_set": "hidden",
                "status_false": "hidden",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-VISIBILITY-001",
                    "summary": f"{group} default search visibility did not return only the active row",
                    "code_location": "memory/services/messages.py:search_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms212() -> dict[str, Any]:
    case_id = "TC-MS-212"
    prefix = "fresh-ms-212"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        specs = [
            {
                "agent_id": f"{prefix}-{group}-agent",
                "session_id": f"{prefix}-{group}-session",
                "user_input": "test required query candidate",
                "agent_response": "test nonempty query response",
            }
        ]
        batch = _write_raw_fixture(case_id, group, owner, prefix, specs)
        fixture_ids = _message_id_set(batch["rows"])
        before_identity = _raw_snapshot_identity(batch["rows"])
        base_params = [
            ("memory_id", batch["memory_id"]),
            ("similarity_threshold", "0.2"),
            ("keywords_similarity_weight", "0.7"),
            ("top_n", "5"),
        ]
        legal = _request(
            case_id,
            group,
            "search_with_nonempty_query_control",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[*base_params, ("query", "test")],
            timeout=240,
        )
        empty = _request(
            case_id,
            group,
            "search_with_empty_query",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[*base_params, ("query", "")],
            timeout=240,
        )
        missing = _request(
            case_id,
            group,
            "search_without_query_parameter",
            owner["auth"],
            "GET",
            "/messages/search",
            params=base_params,
            timeout=240,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_empty_and_missing_query",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        legal_ids = _message_id_set(_recent_payload(legal))
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "legal": [legal.get("http_status"), legal.get("code")],
            "empty": [empty.get("http_status"), empty.get("code")],
            "missing": [missing.get("http_status"), missing.get("code")],
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            batch["preclean"] and batch["writes_succeeded"] and batch["snapshot"].get("raw_count") == 1 and bool(fixture_ids) and fixture_ids <= legal_ids and query_validation_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_single_query_validation_candidate",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "search_with_nonempty_query_as_positive_control",
                    "response": observed["legal"],
                    "target_hit": bool(fixture_ids) and fixture_ids <= legal_ids,
                    "raw_sha256": legal.get("raw_sha256"),
                },
                {
                    "name": "reject_empty_and_missing_query_parameters",
                    "empty_response": observed["empty"],
                    "missing_response": observed["missing"],
                    "raw_sha256": [
                        empty.get("raw_sha256"),
                        missing.get("raw_sha256"),
                    ],
                },
                {
                    "name": "prove_query_requests_left_store_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "nonempty_query": [200, 0],
                "empty_query": [200, 101],
                "missing_query": [200, 101],
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEARCH-QUERY-VALIDATION-001",
                    "summary": f"{group} search did not reject empty and missing query with code 101",
                    "code_location": "api/apps/restful_apis/memory_api.py:search_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms213() -> dict[str, Any]:
    return _run_semantic_search_case(
        "TC-MS-213",
        "fresh-ms-213",
        user_input="100% discount on SELECT * FROM items",
        agent_response="special characters were stored as ordinary message text",
        query="discount SELECT",
        keywords_weight=1.0,
        finding_id="MS-SPECIAL-CHARACTER-SEARCH-001",
    )


def _snapshot_message_row(snapshot: dict[str, Any], message_id: int) -> dict[str, Any]:
    return next(
        (row for row in snapshot.get("rows", []) if int(row.get("message_id") or 0) == int(message_id)),
        {},
    )


def _set_message_status(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    memory_id: str,
    message_id: int,
    status: Any,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PUT",
        f"/messages/{memory_id}:{message_id}",
        payload={"status": status},
    )


def _forget_message(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/messages/{memory_id}:{message_id}",
    )


def _get_message_content(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/messages/{memory_id}:{message_id}/content",
    )


def run_ms300() -> dict[str, Any]:
    case_id = "TC-MS-300"
    prefix = "fresh-ms-300"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "status disable input",
                    "agent_response": "status disable response",
                }
            ],
        )
        fixture_ids = _message_id_set(batch["rows"])
        message_id = next(iter(fixture_ids), 0)
        before_row = _snapshot_message_row(batch["snapshot"], message_id)
        updated = _set_message_status(
            case_id,
            group,
            "set_message_status_false",
            owner["auth"],
            batch["memory_id"],
            message_id,
            False,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_status_false",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        after_row = _snapshot_message_row(after, message_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "response": [updated.get("http_status"), updated.get("code")],
            "response_message": updated.get("message"),
            "physical_raw_count": after.get("raw_count"),
            "status": after_row.get("status"),
            "physical_status_int": after_row.get("physical_status_int"),
            "identity_unchanged": _raw_snapshot_identity_without_status(batch["rows"]) == _raw_snapshot_identity_without_status(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            batch["preclean"]
            and batch["writes_succeeded"]
            and message_id > 0
            and before_row.get("status") is True
            and before_row.get("physical_status_int") == 1
            and status_transition_contract_ok(observed, expected_status=False)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_active_message_and_capture_physical_status_one",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "message_id_positive": message_id > 0,
                    "status": before_row.get("status"),
                    "physical_status_int": before_row.get("physical_status_int"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "set_status_false_through_api",
                    "response": observed["response"],
                    "message_is_true": observed["response_message"] is True,
                    "raw_sha256": updated.get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_physical_status_zero",
                    "status": observed["status"],
                    "physical_status_int": observed["physical_status_int"],
                    "identity_unchanged": observed["identity_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "message": True,
                "status": False,
                "physical_status_int": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-STATUS-DISABLE-001",
                    "summary": f"{group} status=false did not persist as physical zero",
                    "code_location": "api/apps/services/memory_api_service.py:update_message_status",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_status_roundtrip_case(
    case_id: str,
    prefix: str,
    *,
    finding_id: str,
    focus: str,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": f"{focus} input",
                    "agent_response": f"{focus} response",
                }
            ],
        )
        fixture_ids = _message_id_set(batch["rows"])
        message_id = next(iter(fixture_ids), 0)
        disabled = _set_message_status(
            case_id,
            group,
            "establish_status_false_precondition",
            owner["auth"],
            batch["memory_id"],
            message_id,
            False,
        )
        disabled_snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_status_zero_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        disabled_row = _snapshot_message_row(disabled_snapshot, message_id)
        enabled = _set_message_status(
            case_id,
            group,
            "set_message_status_true",
            owner["auth"],
            batch["memory_id"],
            message_id,
            True,
        )
        enabled_snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_status_one_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        enabled_row = _snapshot_message_row(enabled_snapshot, message_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "responses": [
                [disabled.get("http_status"), disabled.get("code")],
                [enabled.get("http_status"), enabled.get("code")],
            ],
            "response_messages": [
                disabled.get("message"),
                enabled.get("message"),
            ],
            "statuses": [disabled_row.get("status"), enabled_row.get("status")],
            "physical_status_ints": [
                disabled_row.get("physical_status_int"),
                enabled_row.get("physical_status_int"),
            ],
            "identity_unchanged": _raw_snapshot_identity_without_status(batch["rows"])
            == _raw_snapshot_identity_without_status(disabled_snapshot.get("rows", []))
            == _raw_snapshot_identity_without_status(enabled_snapshot.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            batch["preclean"]
            and batch["writes_succeeded"]
            and message_id > 0
            and _snapshot_message_row(batch["snapshot"], message_id).get("physical_status_int") == 1
            and status_roundtrip_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_active_message_and_capture_id",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "message_id_positive": message_id > 0,
                    "initial_physical_status_int": _snapshot_message_row(batch["snapshot"], message_id).get("physical_status_int"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "set_status_false_and_verify_physical_zero",
                    "response": observed["responses"][0],
                    "status": observed["statuses"][0],
                    "physical_status_int": observed["physical_status_ints"][0],
                    "raw_sha256": [
                        disabled.get("raw_sha256"),
                        disabled_snapshot.get("raw_sha256"),
                    ],
                },
                {
                    "name": "set_status_true_and_verify_physical_one",
                    "response": observed["responses"][1],
                    "status": observed["statuses"][1],
                    "physical_status_int": observed["physical_status_ints"][1],
                    "identity_unchanged": observed["identity_unchanged"],
                    "raw_sha256": [
                        enabled.get("raw_sha256"),
                        enabled_snapshot.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "focus": focus,
                "responses": [[200, 0], [200, 0]],
                "status_sequence": [False, True],
                "physical_status_int_sequence": [0, 1],
            },
            "findings": []
            if passed
            else [
                {
                    "id": finding_id,
                    "summary": f"{group} status false/true roundtrip did not map to physical 0/1",
                    "code_location": "memory/services/messages.py:update_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms301() -> dict[str, Any]:
    return _run_status_roundtrip_case(
        "TC-MS-301",
        "fresh-ms-301",
        finding_id="MS-STATUS-REACTIVATE-001",
        focus="reactivate disabled message",
    )


def run_ms302() -> dict[str, Any]:
    return _run_status_roundtrip_case(
        "TC-MS-302",
        "fresh-ms-302",
        finding_id="MS-STATUS-NUMERIC-MAPPING-001",
        focus="boolean to numeric status mapping",
    )


def run_ms303() -> dict[str, Any]:
    case_id = "TC-MS-303"
    prefix = "fresh-ms-303"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        active_agent = f"{prefix}-{group}-active"
        disabled_agent = f"{prefix}-{group}-disabled"
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": active_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "test active status candidate",
                    "agent_response": "test active status response",
                },
                {
                    "agent_id": disabled_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "test disabled status candidate",
                    "agent_response": "test disabled status response",
                },
            ],
        )
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        active_id = ids_by_agent.get(active_agent, 0)
        disabled_id = ids_by_agent.get(disabled_agent, 0)
        disabled = _set_message_status(
            case_id,
            group,
            "disable_second_search_candidate",
            owner["auth"],
            batch["memory_id"],
            disabled_id,
            False,
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_status_visibility_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        search = _request(
            case_id,
            group,
            "search_hides_disabled_message",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", batch["memory_id"]),
                ("query", "test"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "0.7"),
                ("top_n", "10"),
            ],
            timeout=240,
        )
        recent = _request(
            case_id,
            group,
            "recent_keeps_disabled_message",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", batch["memory_id"]), ("limit", "10")],
        )
        search_ids = _message_id_set(_recent_payload(search))
        recent_ids = _message_id_set(_recent_payload(recent))
        disabled_row = _snapshot_message_row(physical, disabled_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "disable": [disabled.get("http_status"), disabled.get("code")],
            "search": [search.get("http_status"), search.get("code")],
            "recent": [recent.get("http_status"), recent.get("code")],
            "physical_raw_count": physical.get("raw_count"),
            "physical_disabled": disabled_row.get("status") is False and disabled_row.get("physical_status_int") == 0,
            "search_active_only": active_id > 0 and search_ids == {active_id},
            "recent_includes_both": active_id > 0 and disabled_id > 0 and recent_ids == {active_id, disabled_id},
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and active_id > 0 and disabled_id > 0 and status_visibility_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_query_matchable_status_candidates",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": active_id > 0 and disabled_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "disable_second_candidate_and_verify_physical_zero",
                    "response": observed["disable"],
                    "physical_disabled": observed["physical_disabled"],
                    "raw_sha256": [
                        disabled.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "compare_default_search_and_recent_visibility",
                    "search_response": observed["search"],
                    "recent_response": observed["recent"],
                    "search_active_only": observed["search_active_only"],
                    "recent_includes_both": observed["recent_includes_both"],
                    "raw_sha256": [
                        search.get("raw_sha256"),
                        recent.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "search": "only status_int=1 message",
                "recent": "both active and disabled messages",
                "disabled_physical_status_int": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-STATUS-VISIBILITY-001",
                    "summary": f"{group} disabled-message visibility differed between search and recent",
                    "code_location": "memory/services/messages.py:search_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms304() -> dict[str, Any]:
    case_id = "TC-MS-304"
    prefix = "fresh-ms-304"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "invalid status input",
                    "agent_response": "invalid status response",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        before_identity = _raw_snapshot_identity(batch["rows"])
        invalid = [
            _set_message_status(
                case_id,
                group,
                "reject_string_status",
                owner["auth"],
                batch["memory_id"],
                message_id,
                "false",
            ),
            _set_message_status(
                case_id,
                group,
                "reject_numeric_status",
                owner["auth"],
                batch["memory_id"],
                message_id,
                1,
            ),
        ]
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_invalid_status_requests",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        expected_message = "Status must be a boolean."
        observed = {
            "responses": [[response.get("http_status"), response.get("code")] for response in invalid],
            "messages_exact": all(response.get("message") == expected_message for response in invalid),
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and message_id > 0 and invalid_status_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_active_message_and_capture_store_identity",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "message_id_positive": message_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "reject_string_and_numeric_status_values",
                    "responses": observed["responses"],
                    "messages_exact": observed["messages_exact"],
                    "raw_sha256": [response.get("raw_sha256") for response in invalid],
                },
                {
                    "name": "prove_invalid_requests_left_store_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "responses": [[200, 101], [200, 101]],
                "message": expected_message,
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-STATUS-TYPE-VALIDATION-001",
                    "summary": f"{group} non-boolean status values were not rejected without mutation",
                    "code_location": "api/apps/restful_apis/memory_api.py:update_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms305() -> dict[str, Any]:
    case_id = "TC-MS-305"
    prefix = "fresh-ms-305"
    email = "ms-305-user-b@fresh.invalid"
    password = secondary_fixture_password(case_id)

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        owner = _owner(case_id, group)
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "cross tenant status input",
                    "agent_response": "cross tenant status response",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        before_identity = _raw_snapshot_identity(batch["rows"])
        rejected = _set_message_status(
            case_id,
            group,
            "reject_cross_tenant_status_update",
            secondary["auth"],
            batch["memory_id"],
            message_id,
            False,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_cross_tenant_status_attempt",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        cleanup = memory_cleanup and user_cleanup["succeeded"]
        expected_message = f"Memory '{batch['memory_id']}' not found."
        observed = {
            "response": [rejected.get("http_status"), rejected.get("code")],
            "message_exact": rejected.get("message") == expected_message,
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            secondary["preclean"]
            and secondary["registration"].get("code") == 0
            and secondary["login"].get("code") == 0
            and batch["preclean"]
            and batch["writes_succeeded"]
            and message_id > 0
            and status_access_rejection_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_tenant_and_owner_message",
                    "secondary_registration_code": secondary["registration"].get("code"),
                    "secondary_login_code": secondary["login"].get("code"),
                    "owner_write_response": [
                        batch["responses"][0].get("http_status"),
                        batch["responses"][0].get("code"),
                    ],
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "reject_cross_tenant_status_update",
                    "response": observed["response"],
                    "message_exact": observed["message_exact"],
                    "raw_sha256": rejected.get("raw_sha256"),
                },
                {
                    "name": "prove_owner_message_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_and_secondary_user_through_apis",
                    "memory_cleanup_succeeded": memory_cleanup,
                    "user_cleanup_succeeded": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "response": [200, 404],
                "message": "Memory '<id>' not found.",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-STATUS-CROSS-TENANT-001",
                    "summary": f"{group} cross-tenant status update was not rejected without mutation",
                    "code_location": "api/apps/services/memory_api_service.py:_require_memory_access",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms306() -> dict[str, Any]:
    case_id = "TC-MS-306"
    prefix = "fresh-ms-306"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "missing message status input",
                    "agent_response": "missing message status response",
                }
            ],
        )
        fixture_ids = _message_id_set(batch["rows"])
        nonexistent_id = (max(fixture_ids) if fixture_ids else 0) + 1_000_000
        before_identity = _raw_snapshot_identity(batch["rows"])
        updated = _set_message_status(
            case_id,
            group,
            "update_nonexistent_message_status",
            owner["auth"],
            batch["memory_id"],
            nonexistent_id,
            False,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_nonexistent_status_update",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        message = str(updated.get("message") or "")
        observed = {
            "response": [updated.get("http_status"), updated.get("code")],
            "message_identifies_missing_message": str(nonexistent_id) in message and batch["memory_id"] in message and "not found" in message.lower(),
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and bool(fixture_ids) and nonexistent_id not in fixture_ids and missing_message_status_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_one_message_and_prove_dedicated_id_is_absent",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_id_count": len(fixture_ids),
                    "nonexistent_id_absent": nonexistent_id not in fixture_ids,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "reject_nonexistent_message_status_update",
                    "response": observed["response"],
                    "message_identifies_missing_message": observed["message_identifies_missing_message"],
                    "raw_sha256": updated.get("raw_sha256"),
                },
                {
                    "name": "prove_existing_store_row_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 404],
                "message": "identifies missing message and memory",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-STATUS-NONEXISTENT-SUCCESS-001",
                    "summary": f"{group} nonexistent message status update reported success",
                    "code_location": "api/apps/services/memory_api_service.py:update_message_status",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_single_forget_case(
    case_id: str,
    prefix: str,
    *,
    finding_id: str,
    focus: str,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": f"{focus} input",
                    "agent_response": f"{focus} response",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        before_row = _snapshot_message_row(batch["snapshot"], message_id)
        before_ms = int(time.time() * 1000)
        forgotten = _forget_message(
            case_id,
            group,
            "forget_existing_message",
            owner["auth"],
            batch["memory_id"],
            message_id,
        )
        after_ms = int(time.time() * 1000)
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_forget_timestamp",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        after_row = _snapshot_message_row(after, message_id)
        physical_ms = after_row.get("physical_forget_at_ms")
        lower_bound_ms = (before_ms // 1000) * 1000
        upper_bound_ms = ((after_ms // 1000) + 1) * 1000
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "response": [forgotten.get("http_status"), forgotten.get("code")],
            "response_message": forgotten.get("message"),
            "physical_raw_count": after.get("raw_count"),
            "forget_at_set": after_row.get("forget_at_is_null") is False and isinstance(physical_ms, int),
            "timestamp_in_window": isinstance(physical_ms, int) and lower_bound_ms <= physical_ms <= upper_bound_ms,
            "identity_unchanged": _raw_snapshot_identity_without_forget(batch["rows"]) == _raw_snapshot_identity_without_forget(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            batch["preclean"]
            and batch["writes_succeeded"]
            and message_id > 0
            and before_row.get("forget_at_is_null") is True
            and before_row.get("physical_forget_at_ms") is None
            and forget_transition_contract_ok(observed)
        )
        findings = []
        if not passed:
            if group == "control" and observed["forget_at_set"] and not observed["timestamp_in_window"]:
                findings.append(
                    {
                        "id": "MS-INFINITY-FORGET-TIMESTAMP-PRECISION-001",
                        "summary": "control Infinity float forget timestamp fell outside the API request window",
                        "code_location": "conf/message_infinity_mapping.json:forget_at_flt",
                    }
                )
            else:
                findings.append(
                    {
                        "id": finding_id,
                        "summary": f"{group} forget operation did not persist a current physical timestamp",
                        "code_location": "api/apps/services/memory_api_service.py:forget_message",
                    }
                )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_active_message_and_capture_null_forget_at",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "message_id_positive": message_id > 0,
                    "forget_at_is_null": before_row.get("forget_at_is_null"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "record_time_window_and_forget_message_through_api",
                    "response": observed["response"],
                    "message_is_true": observed["response_message"] is True,
                    "request_window_ms": after_ms - before_ms,
                    "raw_sha256": forgotten.get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_physical_forget_timestamp",
                    "forget_at_set": observed["forget_at_set"],
                    "timestamp_in_window": observed["timestamp_in_window"],
                    "identity_unchanged": observed["identity_unchanged"],
                    "storage_precision": "seconds",
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "focus": focus,
                "response": [200, 0],
                "message": True,
                "forget_at": "physical timestamp within request window at second precision",
                "row_count": 1,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_ms400() -> dict[str, Any]:
    return _run_single_forget_case(
        "TC-MS-400",
        "fresh-ms-400",
        finding_id="MS-FORGET-ONE-001",
        focus="forget one message",
    )


def run_ms401() -> dict[str, Any]:
    return _run_single_forget_case(
        "TC-MS-401",
        "fresh-ms-401",
        finding_id="MS-FORGET-TIMESTAMP-WINDOW-001",
        focus="verify forget timestamp window",
    )


def run_ms402() -> dict[str, Any]:
    case_id = "TC-MS-402"
    prefix = "fresh-ms-402"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        active_agent = f"{prefix}-{group}-active"
        forgotten_agent = f"{prefix}-{group}-forgotten"
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": active_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "test active forget visibility",
                    "agent_response": "test active response",
                },
                {
                    "agent_id": forgotten_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "test forgotten visibility",
                    "agent_response": "test forgotten response",
                },
            ],
        )
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        active_id = ids_by_agent.get(active_agent, 0)
        forgotten_id = ids_by_agent.get(forgotten_agent, 0)
        forgotten = _forget_message(
            case_id,
            group,
            "forget_second_search_candidate",
            owner["auth"],
            batch["memory_id"],
            forgotten_id,
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_forgotten_visibility_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        search = _request(
            case_id,
            group,
            "search_hides_forgotten_message",
            owner["auth"],
            "GET",
            "/messages/search",
            params=[
                ("memory_id", batch["memory_id"]),
                ("query", "test"),
                ("similarity_threshold", "0.0"),
                ("keywords_similarity_weight", "0.7"),
                ("top_n", "10"),
            ],
            timeout=240,
        )
        recent = _request(
            case_id,
            group,
            "recent_hides_forgotten_message",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", batch["memory_id"]), ("limit", "10")],
        )
        search_ids = _message_id_set(_recent_payload(search))
        recent_ids = _message_id_set(_recent_payload(recent))
        forgotten_row = _snapshot_message_row(physical, forgotten_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "forget": [forgotten.get("http_status"), forgotten.get("code")],
            "search": [search.get("http_status"), search.get("code")],
            "recent": [recent.get("http_status"), recent.get("code")],
            "physical_raw_count": physical.get("raw_count"),
            "physical_forgotten": forgotten_row.get("forget_at_is_null") is False and isinstance(forgotten_row.get("physical_forget_at_ms"), int),
            "search_active_only": active_id > 0 and search_ids == {active_id},
            "recent_active_only": active_id > 0 and recent_ids == {active_id},
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and active_id > 0 and forgotten_id > 0 and forget_visibility_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_query_matchable_messages",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": active_id > 0 and forgotten_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "forget_second_candidate_and_verify_physical_timestamp",
                    "response": observed["forget"],
                    "physical_forgotten": observed["physical_forgotten"],
                    "raw_sha256": [
                        forgotten.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "verify_default_search_and_recent_hide_forgotten_message",
                    "search_response": observed["search"],
                    "recent_response": observed["recent"],
                    "search_active_only": observed["search_active_only"],
                    "recent_active_only": observed["recent_active_only"],
                    "raw_sha256": [
                        search.get("raw_sha256"),
                        recent.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "physical_rows": 2,
                "forgotten_row_persists": True,
                "search_ids": "active only",
                "recent_ids": "active only",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-FORGET-DEFAULT-VISIBILITY-001",
                    "summary": f"{group} default search or recent endpoint exposed a forgotten message",
                    "code_location": "memory/services/messages.py:search_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms403() -> dict[str, Any]:
    case_id = "TC-MS-403"
    prefix = "fresh-ms-403"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        active_agent = f"{prefix}-{group}-active"
        forgotten_agent = f"{prefix}-{group}-forgotten"
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": active_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "management active message",
                    "agent_response": "management active response",
                },
                {
                    "agent_id": forgotten_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "management forgotten message",
                    "agent_response": "management forgotten response",
                },
            ],
        )
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        active_id = ids_by_agent.get(active_agent, 0)
        forgotten_id = ids_by_agent.get(forgotten_agent, 0)
        forgotten = _forget_message(
            case_id,
            group,
            "forget_management_list_candidate",
            owner["auth"],
            batch["memory_id"],
            forgotten_id,
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_management_list_physical_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        listed = _request(
            case_id,
            group,
            "management_list_includes_forgotten_message",
            owner["auth"],
            "GET",
            f"/memories/{batch['memory_id']}",
        )
        listed_rows, _, _ = _list_payload(listed)
        listed_ids = _message_id_set(listed_rows)
        forgotten_row = _snapshot_message_row(physical, forgotten_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "forget": [forgotten.get("http_status"), forgotten.get("code")],
            "list": [listed.get("http_status"), listed.get("code")],
            "physical_raw_count": physical.get("raw_count"),
            "physical_forgotten": forgotten_row.get("forget_at_is_null") is False and isinstance(forgotten_row.get("physical_forget_at_ms"), int),
            "list_includes_both": active_id > 0 and forgotten_id > 0 and listed_ids == {active_id, forgotten_id},
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and forgotten_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_active_and_to_be_forgotten_messages",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": active_id > 0 and forgotten_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "forget_second_message_and_prove_row_still_exists",
                    "response": observed["forget"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "physical_forgotten": observed["physical_forgotten"],
                    "raw_sha256": [
                        forgotten.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "list_management_view_with_hide_forgotten_false",
                    "response": observed["list"],
                    "list_includes_both": observed["list_includes_both"],
                    "raw_sha256": listed.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "physical_rows": 2,
                "management_list_ids": "active and forgotten",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-FORGET-MANAGEMENT-VISIBILITY-001",
                    "summary": f"{group} management list did not include both active and forgotten messages",
                    "code_location": "memory/services/messages.py:list_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms404() -> dict[str, Any]:
    case_id = "TC-MS-404"
    prefix = "fresh-ms-404"
    email = "ms-404-user-b@fresh.invalid"
    password = secondary_fixture_password(case_id)

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        owner = _owner(case_id, group)
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "cross tenant forget input",
                    "agent_response": "cross tenant forget response",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        before_identity = _raw_snapshot_identity(batch["rows"])
        rejected = _forget_message(
            case_id,
            group,
            "reject_cross_tenant_forget",
            secondary["auth"],
            batch["memory_id"],
            message_id,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_cross_tenant_forget_attempt",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        cleanup = memory_cleanup and user_cleanup["succeeded"]
        expected_message = f"Memory '{batch['memory_id']}' not found."
        observed = {
            "response": [rejected.get("http_status"), rejected.get("code")],
            "message_exact": rejected.get("message") == expected_message,
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            secondary["preclean"]
            and secondary["registration"].get("code") == 0
            and secondary["login"].get("code") == 0
            and batch["preclean"]
            and batch["writes_succeeded"]
            and message_id > 0
            and forget_access_rejection_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_tenant_and_owner_message",
                    "secondary_registration_code": secondary["registration"].get("code"),
                    "secondary_login_code": secondary["login"].get("code"),
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "reject_cross_tenant_forget",
                    "response": observed["response"],
                    "message_exact": observed["message_exact"],
                    "raw_sha256": rejected.get("raw_sha256"),
                },
                {
                    "name": "prove_owner_message_and_null_forget_at_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_and_secondary_user_through_apis",
                    "memory_cleanup_succeeded": memory_cleanup,
                    "user_cleanup_succeeded": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "response": [200, 404],
                "message": "Memory '<id>' not found.",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-FORGET-CROSS-TENANT-001",
                    "summary": f"{group} cross-tenant forget was not rejected without mutation",
                    "code_location": "api/apps/services/memory_api_service.py:_require_memory_access",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms405() -> dict[str, Any]:
    case_id = "TC-MS-405"
    prefix = "fresh-ms-405"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "repeat forget input",
                    "agent_response": "repeat forget response",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        first = _forget_message(
            case_id,
            group,
            "first_forget",
            owner["auth"],
            batch["memory_id"],
            message_id,
        )
        first_snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_first_forget_timestamp",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        first_row = _snapshot_message_row(first_snapshot, message_id)
        time.sleep(1.1)
        second = _forget_message(
            case_id,
            group,
            "second_forget_after_precision_boundary",
            owner["auth"],
            batch["memory_id"],
            message_id,
        )
        second_snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_second_forget_timestamp",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        second_row = _snapshot_message_row(second_snapshot, message_id)
        first_ms = first_row.get("physical_forget_at_ms")
        second_ms = second_row.get("physical_forget_at_ms")
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "responses": [
                [first.get("http_status"), first.get("code")],
                [second.get("http_status"), second.get("code")],
            ],
            "response_messages": [first.get("message"), second.get("message")],
            "first_timestamp_set": isinstance(first_ms, int) and first_row.get("forget_at_is_null") is False,
            "second_timestamp_later": isinstance(first_ms, int) and isinstance(second_ms, int) and second_ms > first_ms,
            "physical_raw_count": second_snapshot.get("raw_count"),
            "identity_unchanged": _raw_snapshot_identity_without_forget(batch["rows"])
            == _raw_snapshot_identity_without_forget(first_snapshot.get("rows", []))
            == _raw_snapshot_identity_without_forget(second_snapshot.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and message_id > 0 and repeat_forget_contract_ok(observed)
        findings = []
        if not passed:
            if group == "control" and observed["first_timestamp_set"] and not observed["second_timestamp_later"]:
                findings.append(
                    {
                        "id": "MS-INFINITY-FORGET-TIMESTAMP-PRECISION-001",
                        "summary": "control Infinity float forget timestamp did not advance after a repeated forget",
                        "code_location": "conf/message_infinity_mapping.json:forget_at_flt",
                    }
                )
            else:
                findings.append(
                    {
                        "id": "MS-FORGET-REPEAT-001",
                        "summary": f"{group} repeated forget did not update one existing row to a later timestamp",
                        "code_location": "api/apps/services/memory_api_service.py:forget_message",
                    }
                )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_one_active_message",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "message_id_positive": message_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "forget_first_time_and_capture_physical_timestamp",
                    "response": observed["responses"][0],
                    "message_is_true": observed["response_messages"][0] is True,
                    "first_timestamp_set": observed["first_timestamp_set"],
                    "raw_sha256": [
                        first.get("raw_sha256"),
                        first_snapshot.get("raw_sha256"),
                    ],
                },
                {
                    "name": "wait_across_second_and_forget_again",
                    "response": observed["responses"][1],
                    "message_is_true": observed["response_messages"][1] is True,
                    "second_timestamp_later": observed["second_timestamp_later"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "identity_unchanged": observed["identity_unchanged"],
                    "raw_sha256": [
                        second.get("raw_sha256"),
                        second_snapshot.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "responses": [[200, 0], [200, 0]],
                "second_forget_at": "later than first forget_at",
                "physical_rows": 1,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_ms406() -> dict[str, Any]:
    case_id = "TC-MS-406"
    prefix = "fresh-ms-406"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "missing message forget input",
                    "agent_response": "missing message forget response",
                }
            ],
        )
        fixture_ids = _message_id_set(batch["rows"])
        nonexistent_id = (max(fixture_ids) if fixture_ids else 0) + 1_000_000
        before_identity = _raw_snapshot_identity(batch["rows"])
        forgotten = _forget_message(
            case_id,
            group,
            "forget_proven_nonexistent_message",
            owner["auth"],
            batch["memory_id"],
            nonexistent_id,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_nonexistent_forget",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        message = str(forgotten.get("message") or "")
        observed = {
            "response": [forgotten.get("http_status"), forgotten.get("code")],
            "message_identifies_missing_message": str(nonexistent_id) in message and batch["memory_id"] in message and "not found" in message.lower(),
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and bool(fixture_ids) and nonexistent_id not in fixture_ids and missing_message_forget_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_one_message_and_prove_dedicated_id_is_absent",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_id_count": len(fixture_ids),
                    "nonexistent_id_absent": nonexistent_id not in fixture_ids,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "reject_forget_of_nonexistent_message",
                    "response": observed["response"],
                    "message_identifies_missing_message": observed["message_identifies_missing_message"],
                    "raw_sha256": forgotten.get("raw_sha256"),
                },
                {
                    "name": "prove_existing_store_row_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 404],
                "message": "identifies missing message and memory",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-FORGET-NONEXISTENT-SUCCESS-001",
                    "summary": f"{group} nonexistent message forget reported success",
                    "code_location": "api/apps/services/memory_api_service.py:forget_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms500() -> dict[str, Any]:
    case_id = "TC-MS-500"
    prefix = "fresh-ms-500"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        expected_content = "User Input: hello\nAgent Response: hi there"
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "hello",
                    "agent_response": "hi there",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        raw_row = _snapshot_message_row(batch["snapshot"], message_id)
        response = _get_message_content(
            case_id,
            group,
            "get_existing_message_content",
            owner["auth"],
            batch["memory_id"],
            message_id,
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        vector = data.get("content_embed")
        content = str(data.get("content") or "")
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "response": [response.get("http_status"), response.get("code")],
            "response_message": response.get("message"),
            "physical_raw_count": batch["snapshot"].get("raw_count"),
            "id_exact": data.get("id") == f"{batch['memory_id']}_{message_id}",
            "message_id_exact": int(data.get("message_id") or 0) == message_id,
            "memory_id_exact": data.get("memory_id") == batch["memory_id"],
            "content_exact": content == expected_content,
            "vector_nonempty": isinstance(vector, list) and len(vector) > 0 and any(float(value) != 0.0 for value in vector),
            "raw_response_consistent": hashlib.sha256(content.encode("utf-8")).hexdigest() == raw_row.get("content_sha256")
            and isinstance(vector, list)
            and len(vector) == int(raw_row.get("vector_dimension") or 0),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and message_id > 0 and message_content_contract_ok(observed)
        findings = []
        if not passed:
            if group == "control" and observed["response"] == [200, 0] and observed["content_exact"] and observed["raw_response_consistent"] and not observed["id_exact"]:
                findings.append(
                    {
                        "id": "MS-INFINITY-CONTENT-ID-OMITTED-001",
                        "summary": "control Infinity content response omitted the composite id field",
                        "code_location": "memory/utils/infinity_conn.py:get",
                    }
                )
            else:
                findings.append(
                    {
                        "id": "MS-CONTENT-GET-001",
                        "summary": f"{group} exact message content response differed from the physical raw row",
                        "code_location": "api/apps/services/memory_api_service.py:get_message_content",
                    }
                )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_known_message_and_capture_physical_identity",
                    "physical_raw_count": observed["physical_raw_count"],
                    "message_id_positive": message_id > 0,
                    "content_hash_matches_fixture": raw_row.get("content_sha256") == hashlib.sha256(expected_content.encode("utf-8")).hexdigest(),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "get_message_content_by_composite_id",
                    "response": observed["response"],
                    "message_is_true": observed["response_message"] is True,
                    "id_exact": observed["id_exact"],
                    "message_id_exact": observed["message_id_exact"],
                    "memory_id_exact": observed["memory_id_exact"],
                    "content_exact": observed["content_exact"],
                    "vector_nonempty": observed["vector_nonempty"],
                    "raw_response_consistent": observed["raw_response_consistent"],
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "id": "<actual_memory_id>_<captured_message_id>",
                "message_id": "captured numeric ID",
                "content": expected_content,
                "content_embed": "nonempty real vector matching physical dimension",
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_ms501() -> dict[str, Any]:
    case_id = "TC-MS-501"
    prefix = "fresh-ms-501"
    email = "ms-501-user-b@fresh.invalid"
    password = secondary_fixture_password(case_id)

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        owner = _owner(case_id, group)
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "cross tenant content input",
                    "agent_response": "cross tenant content response",
                }
            ],
        )
        message_id = next(iter(_message_id_set(batch["rows"])), 0)
        before_identity = _raw_snapshot_identity(batch["rows"])
        rejected = _get_message_content(
            case_id,
            group,
            "reject_cross_tenant_content_read",
            secondary["auth"],
            batch["memory_id"],
            message_id,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_cross_tenant_content_attempt",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        cleanup = memory_cleanup and user_cleanup["succeeded"]
        expected_message = f"Memory '{batch['memory_id']}' not found."
        observed = {
            "response": [rejected.get("http_status"), rejected.get("code")],
            "message_exact": rejected.get("message") == expected_message,
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            secondary["preclean"]
            and secondary["registration"].get("code") == 0
            and secondary["login"].get("code") == 0
            and batch["preclean"]
            and batch["writes_succeeded"]
            and message_id > 0
            and content_access_rejection_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_tenant_and_owner_message",
                    "secondary_registration_code": secondary["registration"].get("code"),
                    "secondary_login_code": secondary["login"].get("code"),
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "reject_cross_tenant_content_read",
                    "response": observed["response"],
                    "message_exact": observed["message_exact"],
                    "raw_sha256": rejected.get("raw_sha256"),
                },
                {
                    "name": "prove_owner_message_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_and_secondary_user_through_apis",
                    "memory_cleanup_succeeded": memory_cleanup,
                    "user_cleanup_succeeded": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "response": [200, 404],
                "message": "Memory '<id>' not found.",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-CONTENT-CROSS-TENANT-001",
                    "summary": f"{group} cross-tenant message content read was not rejected without mutation",
                    "code_location": "api/apps/services/memory_api_service.py:_require_memory_access",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms502() -> dict[str, Any]:
    case_id = "TC-MS-502"
    prefix = "fresh-ms-502"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "missing content input",
                    "agent_response": "missing content response",
                }
            ],
        )
        fixture_ids = _message_id_set(batch["rows"])
        nonexistent_id = (max(fixture_ids) if fixture_ids else 0) + 1_000_000
        before_identity = _raw_snapshot_identity(batch["rows"])
        missing = _get_message_content(
            case_id,
            group,
            "get_proven_nonexistent_message_content",
            owner["auth"],
            batch["memory_id"],
            nonexistent_id,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_nonexistent_content_read",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        expected_message = f"Message '{nonexistent_id}' in memory '{batch['memory_id']}' not found."
        observed = {
            "response": [missing.get("http_status"), missing.get("code")],
            "message_identifies_missing_message": missing.get("message") == expected_message,
            "raw_unchanged": before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and bool(fixture_ids) and nonexistent_id not in fixture_ids and missing_message_content_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_one_message_and_prove_dedicated_id_is_absent",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_id_count": len(fixture_ids),
                    "nonexistent_id_absent": nonexistent_id not in fixture_ids,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "get_nonexistent_message_content",
                    "response": observed["response"],
                    "message_exact": observed["message_identifies_missing_message"],
                    "raw_sha256": missing.get("raw_sha256"),
                },
                {
                    "name": "prove_read_left_existing_store_row_unchanged",
                    "raw_count_after": after.get("raw_count"),
                    "raw_unchanged": observed["raw_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 404],
                "message": "Message '<id>' in memory '<id>' not found.",
                "store_mutation": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-CONTENT-NONEXISTENT-001",
                    "summary": f"{group} nonexistent message content response did not identify the exact IDs",
                    "code_location": "api/apps/services/memory_api_service.py:get_message_content",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms503() -> dict[str, Any]:
    case_id = "TC-MS-503"
    prefix = "fresh-ms-503"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        forgotten_agent = f"{prefix}-{group}-forgotten"
        inactive_agent = f"{prefix}-{group}-inactive"
        expected_contents = {
            forgotten_agent: "User Input: forgotten content input\nAgent Response: forgotten content response",
            inactive_agent: "User Input: inactive content input\nAgent Response: inactive content response",
        }
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": forgotten_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "forgotten content input",
                    "agent_response": "forgotten content response",
                },
                {
                    "agent_id": inactive_agent,
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "inactive content input",
                    "agent_response": "inactive content response",
                },
            ],
        )
        ids_by_agent = {str(row.get("agent_id") or ""): int(row.get("message_id") or 0) for row in batch["rows"]}
        forgotten_id = ids_by_agent.get(forgotten_agent, 0)
        inactive_id = ids_by_agent.get(inactive_agent, 0)
        forgotten = _forget_message(
            case_id,
            group,
            "forget_first_content_candidate",
            owner["auth"],
            batch["memory_id"],
            forgotten_id,
        )
        inactive = _set_message_status(
            case_id,
            group,
            "disable_second_content_candidate",
            owner["auth"],
            batch["memory_id"],
            inactive_id,
            False,
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_hidden_content_physical_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        forgotten_read = _get_message_content(
            case_id,
            group,
            "get_forgotten_message_content",
            owner["auth"],
            batch["memory_id"],
            forgotten_id,
        )
        inactive_read = _get_message_content(
            case_id,
            group,
            "get_inactive_message_content",
            owner["auth"],
            batch["memory_id"],
            inactive_id,
        )
        forgotten_data = forgotten_read.get("data") if isinstance(forgotten_read.get("data"), dict) else {}
        inactive_data = inactive_read.get("data") if isinstance(inactive_read.get("data"), dict) else {}
        forgotten_row = _snapshot_message_row(physical, forgotten_id)
        inactive_row = _snapshot_message_row(physical, inactive_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "mutations": [
                [forgotten.get("http_status"), forgotten.get("code")],
                [inactive.get("http_status"), inactive.get("code")],
            ],
            "reads": [
                [forgotten_read.get("http_status"), forgotten_read.get("code")],
                [inactive_read.get("http_status"), inactive_read.get("code")],
            ],
            "response_messages": [
                forgotten_read.get("message"),
                inactive_read.get("message"),
            ],
            "physical_raw_count": physical.get("raw_count"),
            "physical_forgotten": forgotten_row.get("forget_at_is_null") is False and isinstance(forgotten_row.get("physical_forget_at_ms"), int),
            "physical_inactive": inactive_row.get("status") is False and inactive_row.get("physical_status_int") == 0,
            "ids_exact": int(forgotten_data.get("message_id") or 0) == forgotten_id
            and forgotten_data.get("memory_id") == batch["memory_id"]
            and int(inactive_data.get("message_id") or 0) == inactive_id
            and inactive_data.get("memory_id") == batch["memory_id"],
            "contents_exact": forgotten_data.get("content") == expected_contents[forgotten_agent] and inactive_data.get("content") == expected_contents[inactive_agent],
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and forgotten_id > 0 and inactive_id > 0 and hidden_message_content_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_messages_and_capture_ids",
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "captured_ids_positive": forgotten_id > 0 and inactive_id > 0,
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "forget_first_and_disable_second_message",
                    "mutation_responses": observed["mutations"],
                    "physical_forgotten": observed["physical_forgotten"],
                    "physical_inactive": observed["physical_inactive"],
                    "raw_sha256": [
                        forgotten.get("raw_sha256"),
                        inactive.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "get_hidden_message_contents_by_exact_ids",
                    "read_responses": observed["reads"],
                    "response_messages_true": observed["response_messages"] == [True, True],
                    "ids_exact": observed["ids_exact"],
                    "contents_exact": observed["contents_exact"],
                    "raw_sha256": [
                        forgotten_read.get("raw_sha256"),
                        inactive_read.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "forgotten_read": [200, 0],
                "inactive_read": [200, 0],
                "ids": "exact requested message_id and memory_id pairs",
                "contents": "exact original contents",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-CONTENT-HIDDEN-ROW-001",
                    "summary": f"{group} exact content read filtered a forgotten or inactive message",
                    "code_location": "memory/services/messages.py:get_by_message_id",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms600() -> dict[str, Any]:
    case_id = "TC-MS-600"
    prefix = "fresh-ms-600"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        spec_a = {
            "agent_id": f"{prefix}-{group}-agent-a",
            "session_id": f"{prefix}-{group}-session-a",
            "user_input": "M1 content",
            "agent_response": "M1 response",
        }
        spec_b = {
            "agent_id": f"{prefix}-{group}-agent-b",
            "session_id": f"{prefix}-{group}-session-b",
            "user_input": "M2 content",
            "agent_response": "M2 response",
        }
        batch = _write_two_memory_fixture(case_id, group, owner, prefix, spec_a, spec_b)
        memory_a_id, memory_b_id = batch["memory_ids"]
        rows_a = [row for row in batch["rows"] if row.get("memory_id") == memory_a_id]
        rows_b = [row for row in batch["rows"] if row.get("memory_id") == memory_b_id]
        ids_a = _message_id_set(rows_a)
        ids_b = _message_id_set(rows_b)
        read_a = _request(
            case_id,
            group,
            "get_recent_memory_a_only",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", memory_a_id), ("limit", "10")],
        )
        read_b = _request(
            case_id,
            group,
            "get_recent_memory_b_only",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", memory_b_id), ("limit", "10")],
        )
        returned_a = _message_id_set(_recent_payload(read_a))
        returned_b = _message_id_set(_recent_payload(read_b))
        layout = _physical_layout_probe(
            case_id,
            group,
            "read_only_same_tenant_physical_layout",
            [
                (owner["tenant_id"], memory_a_id),
                (owner["tenant_id"], memory_b_id),
            ],
        )
        backend = str(layout.get("backend") or "")
        backend_layout_matches = ("Infinity" in backend and layout.get("physical_relation_count") == 2 and layout.get("physical_relations_distinct") is True) or (
            "GaussDB" in backend and layout.get("physical_relation_count") == 1 and layout.get("physical_relations_distinct") is False
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], batch["memory_ids"])
        observed = {
            "writes": [[response.get("http_status"), response.get("code")] for response in batch["responses"]],
            "reads": [
                [read_a.get("http_status"), read_a.get("code")],
                [read_b.get("http_status"), read_b.get("code")],
            ],
            "physical_raw_count": batch["snapshot"].get("raw_count"),
            "api_a_only": len(ids_a) == 1 and returned_a == ids_a,
            "api_b_only": len(ids_b) == 1 and returned_b == ids_b,
            "physical_memory_ids_exact": len(rows_a) == len(rows_b) == 1 and layout.get("per_pair_raw_counts") == [1, 1],
            "backend_layout_matches": backend_layout_matches,
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["memory_a"]["response"].get("code") == 0 and batch["memory_b"]["response"].get("code") == 0 and same_tenant_memory_isolation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_same_tenant_memories_and_write_each_via_api",
                    "create_codes": [
                        batch["memory_a"]["response"].get("code"),
                        batch["memory_b"]["response"].get("code"),
                    ],
                    "write_responses": observed["writes"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "query_each_memory_and_verify_no_cross_read",
                    "read_responses": observed["reads"],
                    "api_a_only": observed["api_a_only"],
                    "api_b_only": observed["api_b_only"],
                    "raw_sha256": [
                        read_a.get("raw_sha256"),
                        read_b.get("raw_sha256"),
                    ],
                },
                {
                    "name": "read_only_verify_backend_specific_physical_layout",
                    "backend": backend,
                    "physical_relation_count": layout.get("physical_relation_count"),
                    "physical_relations_distinct": layout.get("physical_relations_distinct"),
                    "per_memory_raw_counts": layout.get("per_pair_raw_counts"),
                    "layout_matches": backend_layout_matches,
                    "raw_sha256": layout.get("raw_sha256"),
                },
                {
                    "name": "cleanup_both_memories_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "api": "each Memory returns only its own message",
                "control_layout": "two Infinity per-memory relations",
                "experiment_layout": "one GaussDB tenant-shared relation",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SAME-TENANT-MEMORY-ISOLATION-001",
                    "summary": f"{group} same-tenant Memory API or physical layout isolation failed",
                    "code_location": "memory/services/messages.py:list_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms601() -> dict[str, Any]:
    case_id = "TC-MS-601"
    prefix = "fresh-ms-601"
    email = "ms-601-user-b@fresh.invalid"
    password = secondary_fixture_password(case_id)

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        secondary = BASE._prepare_secondary_user(case_id, group, email, password)
        owner = _owner(case_id, group)
        owner_preclean = MS._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        configured = _configure_secondary_embedding(case_id, group, secondary)
        owner_models = _tenant_model_values(group, owner["tenant_id"])
        secondary_models = configured["model_values"]
        memory_a = MS._create_memory_fixture(
            case_id,
            group,
            owner,
            "create_tenant_a_memory",
            f"{prefix}-{group}-a",
        )
        memory_b = _create_memory_with_model_ids(
            case_id,
            group,
            secondary["auth"],
            "create_tenant_b_memory",
            f"{prefix}-{group}-b",
            embd_id=secondary_models["embd_id"],
            llm_id=secondary_models["llm_id"] or owner_models["llm_id"],
        )
        write_a = _add_message(
            case_id,
            group,
            owner["auth"],
            "write_tenant_a_message",
            [memory_a["id"]],
            agent_id=f"{prefix}-{group}-agent-a",
            session_id=f"{prefix}-{group}-session-a",
            user_input="tenant A isolated content",
            agent_response="tenant A isolated response",
        )
        write_b = _add_message(
            case_id,
            group,
            secondary["auth"],
            "write_tenant_b_message",
            [memory_b["id"]],
            agent_id=f"{prefix}-{group}-agent-b",
            session_id=f"{prefix}-{group}-session-b",
            user_input="tenant B isolated content",
            agent_response="tenant B isolated response",
        )
        physical_a = _store_snapshot(
            case_id,
            group,
            "read_only_tenant_a_physical_snapshot",
            owner["tenant_id"],
            [memory_a["id"]],
        )
        physical_b = _store_snapshot(
            case_id,
            group,
            "read_only_tenant_b_physical_snapshot",
            secondary["tenant_id"],
            [memory_b["id"]],
        )
        cross_a_to_b = _request(
            case_id,
            group,
            "tenant_a_queries_tenant_b_memory",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", memory_b["id"]), ("limit", "10")],
        )
        cross_b_to_a = _request(
            case_id,
            group,
            "tenant_b_queries_tenant_a_memory",
            secondary["auth"],
            "GET",
            "/messages",
            params=[("memory_id", memory_a["id"]), ("limit", "10")],
        )
        layout = _physical_layout_probe(
            case_id,
            group,
            "read_only_cross_tenant_physical_layout",
            [
                (owner["tenant_id"], memory_a["id"]),
                (secondary["tenant_id"], memory_b["id"]),
            ],
        )
        memory_a_cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_a["id"]])
        memory_b_cleanup = _cleanup_memories(case_id, group, secondary["auth"], [memory_b["id"]])
        model_cleanup = _cleanup_secondary_embedding(case_id, group, secondary, configured)
        user_cleanup = BASE._cleanup_secondary_user(case_id, group, email)
        cleanup = memory_a_cleanup and memory_b_cleanup and model_cleanup and user_cleanup["succeeded"]
        observed = {
            "writes": [
                [write_a.get("http_status"), write_a.get("code")],
                [write_b.get("http_status"), write_b.get("code")],
            ],
            "cross_reads": [
                [cross_a_to_b.get("http_status"), cross_a_to_b.get("code")],
                [cross_b_to_a.get("http_status"), cross_b_to_a.get("code")],
            ],
            "cross_results_empty": _recent_payload(cross_a_to_b) == [] and _recent_payload(cross_b_to_a) == [],
            "physical_raw_counts": [
                physical_a.get("raw_count"),
                physical_b.get("raw_count"),
            ],
            "tenant_indexes_distinct": layout.get("tenant_indexes_distinct"),
            "physical_relations_distinct": layout.get("physical_relations_distinct"),
            "cleanup_succeeded": cleanup,
        }
        passed = (
            secondary["preclean"]
            and secondary["registration"].get("code") == 0
            and secondary["login"].get("code") == 0
            and owner_preclean
            and configured["ready"]
            and memory_a["response"].get("code") == 0
            and memory_b["response"].get("code") == 0
            and owner["tenant_id"] != secondary["tenant_id"]
            and cross_tenant_memory_isolation_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_tenant_and_configure_local_embedding_via_api",
                    "registration_code": secondary["registration"].get("code"),
                    "login_code": secondary["login"].get("code"),
                    "tenant_ids_distinct": owner["tenant_id"] != secondary["tenant_id"],
                    "embedding_ready": configured["ready"],
                    "raw_sha256": configured["raw_sha256"],
                },
                {
                    "name": "create_one_memory_and_message_per_tenant_via_api",
                    "create_codes": [
                        memory_a["response"].get("code"),
                        memory_b["response"].get("code"),
                    ],
                    "write_responses": observed["writes"],
                    "physical_raw_counts": observed["physical_raw_counts"],
                    "raw_sha256": [
                        physical_a.get("raw_sha256"),
                        physical_b.get("raw_sha256"),
                    ],
                },
                {
                    "name": "verify_bidirectional_cross_tenant_queries_are_empty",
                    "cross_read_responses": observed["cross_reads"],
                    "cross_results_empty": observed["cross_results_empty"],
                    "raw_sha256": [
                        cross_a_to_b.get("raw_sha256"),
                        cross_b_to_a.get("raw_sha256"),
                    ],
                },
                {
                    "name": "read_only_verify_distinct_tenant_indexes_and_relations",
                    "backend": layout.get("backend"),
                    "tenant_indexes_distinct": observed["tenant_indexes_distinct"],
                    "physical_relations_distinct": observed["physical_relations_distinct"],
                    "physical_relation_count": layout.get("physical_relation_count"),
                    "raw_sha256": layout.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memories_model_instance_and_secondary_user_through_apis",
                    "memory_cleanup": [memory_a_cleanup, memory_b_cleanup],
                    "model_cleanup": model_cleanup,
                    "user_cleanup": user_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "cross_reads": "empty lists in both directions",
                "tenant_indexes": "distinct",
                "physical_relations": "distinct",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-CROSS-TENANT-DATA-ISOLATION-001",
                    "summary": f"{group} cross-tenant API or physical relation isolation failed",
                    "code_location": "api/apps/services/memory_api_service.py:_filter_accessible_memories",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms602() -> dict[str, Any]:
    case_id = "TC-MS-602"
    prefix = "fresh-ms-602"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        spec_a = {
            "agent_id": f"{prefix}-{group}-agent-a",
            "session_id": f"{prefix}-{group}-session-a",
            "user_input": "delete target content",
            "agent_response": "delete target response",
        }
        spec_b = {
            "agent_id": f"{prefix}-{group}-agent-b",
            "session_id": f"{prefix}-{group}-session-b",
            "user_input": "survivor content",
            "agent_response": "survivor response",
        }
        batch = _write_two_memory_fixture(case_id, group, owner, prefix, spec_a, spec_b)
        memory_a_id, memory_b_id = batch["memory_ids"]
        survivor_before = [row for row in batch["rows"] if row.get("memory_id") == memory_b_id]
        survivor_ids = _message_id_set(survivor_before)
        deleted = MS._delete_memory(
            case_id,
            group,
            owner["auth"],
            "delete_memory_a_only",
            memory_a_id,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_memory_a_delete",
            owner["tenant_id"],
            [memory_a_id, memory_b_id],
        )
        survivor_after = [row for row in after.get("rows", []) if row.get("memory_id") == memory_b_id]
        recent = _request(
            case_id,
            group,
            "get_surviving_memory_b_messages",
            owner["auth"],
            "GET",
            "/messages",
            params=[("memory_id", memory_b_id), ("limit", "10")],
        )
        metadata_a = MS._memory_snapshot(group, memory_a_id)
        metadata_b = MS._memory_snapshot(group, memory_b_id)
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [memory_b_id])
        observed = {
            "writes": [[response.get("http_status"), response.get("code")] for response in batch["responses"]],
            "delete": [deleted.get("http_status"), deleted.get("code")],
            "physical_before": batch["snapshot"].get("raw_count"),
            "physical_after": after.get("raw_count"),
            "target_removed": memory_target_rows_removed(after, memory_a_id),
            "survivor_exact": len(survivor_after) == 1 and _raw_snapshot_identity(survivor_before) == _raw_snapshot_identity(survivor_after),
            "survivor_api_exact": len(survivor_ids) == 1 and _message_id_set(_recent_payload(recent)) == survivor_ids,
            "metadata_boundary_exact": metadata_a.get("count") == 0 and metadata_b.get("count") == 1,
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and delete_memory_isolation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_memories_and_write_one_message_each",
                    "write_responses": observed["writes"],
                    "physical_raw_count": observed["physical_before"],
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "delete_memory_a_through_api",
                    "response": observed["delete"],
                    "raw_sha256": deleted.get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_only_memory_a_rows_and_metadata_removed",
                    "physical_after": observed["physical_after"],
                    "target_removed": observed["target_removed"],
                    "index_flags_observed": after.get("index_flags"),
                    "survivor_exact": observed["survivor_exact"],
                    "metadata_boundary_exact": observed["metadata_boundary_exact"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "query_surviving_memory_b_through_api",
                    "response": [recent.get("http_status"), recent.get("code")],
                    "survivor_api_exact": observed["survivor_api_exact"],
                    "raw_sha256": recent.get("raw_sha256"),
                },
                {
                    "name": "cleanup_surviving_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "memory_a_rows": 0,
                "memory_b_rows": 1,
                "memory_b_api": "unchanged exact message",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-DELETE-MEMORY-BOUNDARY-001",
                    "summary": f"{group} deleting Memory A affected Memory B or left A rows",
                    "code_location": "memory/services/messages.py:delete_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms603() -> dict[str, Any]:
    case_id = "TC-MS-603"
    prefix = "fresh-ms-603"
    collision_id = 123
    content_a = "fresh-ms-603 collision content from memory A"
    content_b = "fresh-ms-603 collision content from memory B"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        spec_a = {
            "agent_id": f"{prefix}-{group}-source-a",
            "session_id": f"{prefix}-{group}-session-a",
            "user_input": "collision source A",
            "agent_response": "collision source A response",
        }
        spec_b = {
            "agent_id": f"{prefix}-{group}-source-b",
            "session_id": f"{prefix}-{group}-session-b",
            "user_input": "collision source B",
            "agent_response": "collision source B response",
        }
        batch = _write_two_memory_fixture(case_id, group, owner, prefix, spec_a, spec_b)
        memory_a_id, memory_b_id = batch["memory_ids"]
        ids_a = _message_id_set([row for row in batch["rows"] if row.get("memory_id") == memory_a_id])
        ids_b = _message_id_set([row for row in batch["rows"] if row.get("memory_id") == memory_b_id])
        source_a_id = next(iter(ids_a), 0)
        source_b_id = next(iter(ids_b), 0)
        inserted = MS._collision_adapter_action(
            case_id,
            group,
            "insert_authorized_composite_id_collision_fixture",
            action="insert",
            tenant_id=owner["tenant_id"],
            memory_a_id=memory_a_id,
            memory_b_id=memory_b_id,
            source_a_id=source_a_id,
            source_b_id=source_b_id,
            content_a=content_a,
            content_b=content_b,
        )
        read_a = _get_message_content(
            case_id,
            group,
            "get_memory_a_collision_content",
            owner["auth"],
            memory_a_id,
            collision_id,
        )
        read_b = _get_message_content(
            case_id,
            group,
            "get_memory_b_collision_content",
            owner["auth"],
            memory_b_id,
            collision_id,
        )
        data_a = read_a.get("data") if isinstance(read_a.get("data"), dict) else {}
        data_b = read_b.get("data") if isinstance(read_b.get("data"), dict) else {}
        after_reads = MS._collision_adapter_action(
            case_id,
            group,
            "probe_collision_pair_after_both_api_reads",
            action="probe",
            tenant_id=owner["tenant_id"],
            memory_a_id=memory_a_id,
            memory_b_id=memory_b_id,
            source_a_id=source_a_id,
            source_b_id=source_b_id,
            content_a=content_a,
            content_b=content_b,
        )
        fixture_cleanup = MS._collision_adapter_action(
            case_id,
            group,
            "cleanup_authorized_composite_id_collision_fixture",
            action="cleanup",
            tenant_id=owner["tenant_id"],
            memory_a_id=memory_a_id,
            memory_b_id=memory_b_id,
            source_a_id=source_a_id,
            source_b_id=source_b_id,
            content_a=content_a,
            content_b=content_b,
        )
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], batch["memory_ids"])
        backend = str(inserted.get("backend") or "")
        backend_matches = "Infinity" in backend if group == "control" else "GaussDB" in backend
        fixture_inserted = (
            inserted.get("source_vectors_present") is True
            and inserted.get("insert_error_count") == 0
            and inserted.get("physical_pair_count") == 2
            and inserted.get("physical_ids_exact") is True
            and inserted.get("contents_exact") is True
            and backend_matches
        )
        observed = {
            "source_writes": [[response.get("http_status"), response.get("code")] for response in batch["responses"]],
            "fixture_inserted": fixture_inserted,
            "physical_pair_count": inserted.get("physical_pair_count"),
            "physical_ids_exact": inserted.get("physical_ids_exact"),
            "reads": [
                [read_a.get("http_status"), read_a.get("code")],
                [read_b.get("http_status"), read_b.get("code")],
            ],
            "api_a_exact": int(data_a.get("message_id") or 0) == collision_id and data_a.get("memory_id") == memory_a_id and data_a.get("content") == content_a,
            "api_b_exact": int(data_b.get("message_id") or 0) == collision_id and data_b.get("memory_id") == memory_b_id and data_b.get("content") == content_b,
            "both_rows_survive_reads": after_reads.get("physical_pair_count") == 2 and after_reads.get("physical_ids_exact") is True and after_reads.get("contents_exact") is True,
            "fixture_cleanup_count": fixture_cleanup.get("physical_pair_count"),
            "memory_cleanup_succeeded": memory_cleanup,
        }
        passed = (
            batch["preclean"]
            and batch["writes_succeeded"]
            and source_a_id > 0
            and source_b_id > 0
            and source_a_id != source_b_id
            and collision_id not in {source_a_id, source_b_id}
            and collision_isolation_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_memories_and_legal_api_source_messages",
                    "source_write_responses": observed["source_writes"],
                    "source_ids_distinct_and_not_collision_id": source_a_id != source_b_id and collision_id not in {source_a_id, source_b_id},
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "insert_plan_authorized_collision_rows_through_adapter",
                    "authorized_fixture": True,
                    "backend": backend,
                    "backend_matches_group": backend_matches,
                    "source_vectors_present": inserted.get("source_vectors_present"),
                    "insert_error_count": inserted.get("insert_error_count"),
                    "physical_pair_count": observed["physical_pair_count"],
                    "physical_ids_exact": observed["physical_ids_exact"],
                    "contents_exact": inserted.get("contents_exact"),
                    "raw_sha256": inserted.get("raw_sha256"),
                },
                {
                    "name": "read_both_collision_ids_without_cross_memory_confusion",
                    "read_responses": observed["reads"],
                    "api_a_exact": observed["api_a_exact"],
                    "api_b_exact": observed["api_b_exact"],
                    "both_rows_survive_reads": observed["both_rows_survive_reads"],
                    "raw_sha256": [
                        read_a.get("raw_sha256"),
                        read_b.get("raw_sha256"),
                        after_reads.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_adapter_fixture_then_memories_through_api",
                    "adapter_pair_count_after_cleanup": observed["fixture_cleanup_count"],
                    "adapter_cleanup_raw_sha256": fixture_cleanup.get("raw_sha256"),
                    "memory_cleanup_succeeded": memory_cleanup,
                },
            ],
            "oracle": {
                "physical_ids": ["<memory_a>_123", "<memory_b>_123"],
                "api_contents": "each Memory returns only its own collision row",
                "fixture_cleanup_count": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-COMPOSITE-ID-COLLISION-ISOLATION-001",
                    "summary": f"{group} composite message ID collision crossed a Memory boundary",
                    "code_location": "memory/services/messages.py:get_by_message_id",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms700() -> dict[str, Any]:
    case_id = "TC-MS-700"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        source_id = next(iter(_message_id_set(fixture["rows"])), 0)
        probe = _vector_dimension_action(
            case_id,
            group,
            "read_only_initial_vector_schema_probe",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_id=fixture["memory_id"],
            source_message_id=source_id,
            expected_raw_count=1,
        )
        schema = probe.get("schema") if isinstance(probe.get("schema"), dict) else {}
        cleanup = _cleanup_vector_tenant_fixture(case_id, group, fixture)
        observed = {
            "write": [
                fixture["responses"][0].get("http_status"),
                fixture["responses"][0].get("code"),
            ],
            "physical_raw_count": fixture["snapshot"].get("raw_count"),
            "dimension_positive": int(probe.get("dim_a") or 0) > 0,
            "vector_column_count": schema.get("vector_column_count_a"),
            "empty_column_count": schema.get("empty_column_count_a"),
            "vector_type_ok": schema.get("vector_type_ok"),
            "vector_index_present": schema.get("vector_index_present"),
            "row_vector_real": probe.get("source_final", {}).get("vector_real"),
            "row_empty_flag": schema.get("row_empty_a"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and source_id > 0 and initial_vector_schema_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dedicated_tenant_memory_and_prove_no_preexisting_index",
                    "tenant_registration_code": fixture["secondary"]["registration"].get("code"),
                    "embedding_ready": fixture["configured"]["ready"],
                    "memory_create_code": fixture["memory"]["response"].get("code"),
                    "index_absent_before_write": fixture["before_snapshot"].get("index_exists") is False,
                    "raw_sha256": fixture["before_snapshot"].get("raw_sha256"),
                },
                {
                    "name": "write_first_real_vector_message_via_api",
                    "response": observed["write"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "dimension_positive": observed["dimension_positive"],
                    "row_vector_real": observed["row_vector_real"],
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_vector_columns_types_and_index",
                    "backend": probe.get("backend"),
                    "dimension": probe.get("dim_a"),
                    "vector_column_count": observed["vector_column_count"],
                    "empty_column_count": observed["empty_column_count"],
                    "vector_type_ok": observed["vector_type_ok"],
                    "vector_index_present": observed["vector_index_present"],
                    "row_empty_flag": observed["row_empty_flag"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "q_<dim>_vec plus native HNSW, no empty column",
                "experiment": "q_<dim>_vec and q_<dim>_vec_empty=false plus vector index",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-VECTOR-FIRST-SCHEMA-001",
                    "summary": f"{group} first API vector write did not create the expected backend schema",
                    "code_location": "memory/utils/gaussdb_conn.py:create_idx",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms701() -> dict[str, Any]:
    case_id = "TC-MS-701"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        source_id = next(iter(_message_id_set(fixture["rows"])), 0)
        probe = _vector_dimension_action(
            case_id,
            group,
            "insert_new_dimension_adapter_fixture",
            action="new_dim",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_id=fixture["memory_id"],
            source_message_id=source_id,
            expected_raw_count=1,
        )
        schema = probe.get("schema") if isinstance(probe.get("schema"), dict) else {}
        source_after = probe.get("source_after") if isinstance(probe.get("source_after"), dict) else {}
        candidate = probe.get("candidate") if isinstance(probe.get("candidate"), dict) else {}
        dim_a, dim_b = int(probe.get("dim_a") or 0), int(probe.get("dim_b") or 0)
        cleanup = _cleanup_vector_tenant_fixture(case_id, group, fixture)
        expected_dims = [dim_a] if group == "control" else sorted([dim_a, dim_b])
        observed = {
            "source_dimension_positive": dim_a > 0,
            "dimensions_distinct": dim_a > 0 and dim_b > 0 and dim_a != dim_b,
            "fixture_rejected": probe.get("attempt", {}).get("rejected"),
            "candidate_exists": candidate.get("exists"),
            "candidate_dimension_exact": candidate.get("vector_dimension") == dim_b and candidate.get("vector_real") is True,
            "source_preserved": source_after.get("exists") is True and source_after.get("content_sha256") == probe.get("source_content_sha256") and source_after.get("vector_dimension") == dim_a,
            "vector_dimensions_exact": schema.get("vector_dimensions") == expected_dims,
            "empty_columns_exact": (
                schema.get("empty_column_count_a") == 0 and schema.get("empty_column_count_b") == 0
                if group == "control"
                else schema.get("empty_column_count_a") == 1 and schema.get("empty_column_count_b") == 1
            ),
            "cleanup_succeeded": probe.get("fixture_cleanup_succeeded") is True and cleanup["succeeded"],
        }
        passed = fixture["ready"] and source_id > 0 and new_vector_dimension_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dedicated_real_dimension_a_source_via_api",
                    "write_response": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
                    "source_dimension": dim_a,
                    "source_id_positive": source_id > 0,
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "attempt_plan_authorized_new_dimension_b_adapter_write",
                    "backend": probe.get("backend"),
                    "dimension_b": dim_b,
                    "fixture_rejected": observed["fixture_rejected"],
                    "candidate_exists": observed["candidate_exists"],
                    "candidate_dimension_exact": observed["candidate_dimension_exact"],
                    "source_preserved": observed["source_preserved"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "verify_backend_specific_vector_dimension_schema",
                    "vector_dimensions": schema.get("vector_dimensions"),
                    "vector_dimensions_exact": observed["vector_dimensions_exact"],
                    "empty_columns_exact": observed["empty_columns_exact"],
                },
                {
                    "name": "cleanup_adapter_row_memory_model_and_user",
                    "adapter_cleanup": probe.get("fixture_cleanup_succeeded"),
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "different dimension rejected, candidate absent, source preserved",
                "experiment": "new dimension columns added and candidate stored",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-VECTOR-NEW-DIMENSION-001",
                    "summary": f"{group} new-dimension adapter behavior or schema differed",
                    "code_location": "memory/utils/gaussdb_conn.py:_ensure_vector_column_exists",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms702() -> dict[str, Any]:
    case_id = "TC-MS-702"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        source_id = next(iter(_message_id_set(fixture["rows"])), 0)
        probe = _vector_dimension_action(
            case_id,
            group,
            "same_id_cross_dimension_adapter_upsert",
            action="same_id_cross_dim",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_id=fixture["memory_id"],
            source_message_id=source_id,
            expected_raw_count=1,
        )
        schema = probe.get("schema") if isinstance(probe.get("schema"), dict) else {}
        source_after = probe.get("source_after") if isinstance(probe.get("source_after"), dict) else {}
        cleanup = _cleanup_vector_tenant_fixture(case_id, group, fixture)
        observed = {
            "fixture_rejected": probe.get("attempt", {}).get("rejected"),
            "source_row_preserved": source_after.get("exists"),
            "source_content_preserved": source_after.get("content_sha256") == probe.get("source_content_sha256"),
            "source_dimension_preserved": source_after.get("vector_dimension") == probe.get("dim_a"),
            "new_dimension_real": source_after.get("vector_dimension") == probe.get("dim_b") and source_after.get("vector_real") is True,
            "new_empty_false": schema.get("row_empty_b") is False,
            "old_dimension_zero": schema.get("row_vector_a_zero") is True,
            "old_empty_true": schema.get("row_empty_a") is True,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and source_id > 0 and cross_dimension_upsert_contract_ok(group, observed)
        findings = []
        if not passed:
            if group == "control" and observed["fixture_rejected"] is True and observed["source_row_preserved"] is False:
                findings.append(
                    {
                        "id": "MS-INFINITY-CROSS-DIMENSION-DESTRUCTIVE-UPSERT-001",
                        "summary": "control Infinity rejected a cross-dimension upsert only after deleting the original row",
                        "code_location": "memory/utils/infinity_conn.py:insert",
                    }
                )
            else:
                findings.append(
                    {
                        "id": "MS-VECTOR-CROSS-DIMENSION-UPSERT-001",
                        "summary": f"{group} same-id cross-dimension upsert did not meet its safety contract",
                        "code_location": "memory/utils/gaussdb_conn.py:_build_merge_sql",
                    }
                )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_complete_dimension_a_source_via_api",
                    "write_response": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
                    "dimension_a": probe.get("dim_a"),
                    "source_id_positive": source_id > 0,
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "perform_same_id_dimension_b_adapter_upsert",
                    "backend": probe.get("backend"),
                    "dimension_b": probe.get("dim_b"),
                    "fixture_rejected": observed["fixture_rejected"],
                    "source_row_preserved": observed["source_row_preserved"],
                    "source_content_preserved": observed["source_content_preserved"],
                    "source_dimension_preserved": observed["source_dimension_preserved"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "verify_cross_dimension_physical_empty_markers",
                    "new_dimension_real": observed["new_dimension_real"],
                    "new_empty_false": observed["new_empty_false"],
                    "old_dimension_zero": observed["old_dimension_zero"],
                    "old_empty_true": observed["old_empty_true"],
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "reject before delete and preserve original row",
                "experiment": "dimension B real; dimension A zero and empty=true",
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_ms703() -> dict[str, Any]:
    case_id = "TC-MS-703"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        source_id = next(iter(_message_id_set(fixture["rows"])), 0)
        probe = _vector_dimension_action(
            case_id,
            group,
            "dense_search_empty_vector_filter_fixture",
            action="dense_empty_filter",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_id=fixture["memory_id"],
            source_message_id=source_id,
            expected_raw_count=1,
        )
        empty_schema = probe.get("empty_schema") if isinstance(probe.get("empty_schema"), dict) else {}
        candidate_id = source_id + 1_000_000
        cleanup = _cleanup_vector_tenant_fixture(case_id, group, fixture)
        observed = {
            "fixture_action_succeeded": probe.get("fixture_action_succeeded"),
            "dense_result_source_only": probe.get("dense_result_ids") == [source_id],
            "source_vector_real": probe.get("source_final", {}).get("vector_real"),
            "empty_flag": empty_schema.get("row_empty_a"),
            "rejected_fixture_absent": probe.get("rejected_fixture_absent"),
            "placeholder_excluded": candidate_id not in probe.get("dense_result_ids", []),
            "fixture_cleanup_succeeded": probe.get("fixture_cleanup_succeeded"),
            "memory_cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and source_id > 0 and empty_vector_dense_filter_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_real_dense_source_via_api",
                    "write_response": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
                    "source_id_positive": source_id > 0,
                    "source_vector_real": observed["source_vector_real"],
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "create_backend_specific_empty_or_rejected_vector_fixture",
                    "backend": probe.get("backend"),
                    "fixture_action_succeeded": observed["fixture_action_succeeded"],
                    "empty_flag": observed["empty_flag"],
                    "rejected_fixture_absent": observed["rejected_fixture_absent"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "run_pure_dense_adapter_search_and_verify_source_only",
                    "dense_result_count": len(probe.get("dense_result_ids", [])),
                    "dense_result_source_only": observed["dense_result_source_only"],
                    "placeholder_excluded": observed["placeholder_excluded"],
                },
                {
                    "name": "cleanup_adapter_fixture_memory_model_and_user",
                    "adapter_cleanup": observed["fixture_cleanup_succeeded"],
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "missing-vector fixture rejected and absent",
                "experiment": "empty=true placeholder excluded from dense search",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-VECTOR-EMPTY-DENSE-FILTER-001",
                    "summary": f"{group} dense search did not exclude the empty-vector fixture",
                    "code_location": "memory/utils/gaussdb_conn.py:_build_vector_search_sql",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms704() -> dict[str, Any]:
    case_id = "TC-MS-704"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        source_id = next(iter(_message_id_set(fixture["rows"])), 0)
        probe = _vector_dimension_action(
            case_id,
            group,
            "cross_dimension_then_restore_original_fixture",
            action="roundtrip_dims",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_id=fixture["memory_id"],
            source_message_id=source_id,
            expected_raw_count=1,
        )
        response = _get_message_content(
            case_id,
            group,
            "get_restored_real_dimension_content_embed",
            fixture["secondary"]["auth"],
            fixture["memory_id"],
            source_id,
        )
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        vector = data.get("content_embed") if isinstance(data.get("content_embed"), list) else []
        schema = probe.get("schema") if isinstance(probe.get("schema"), dict) else {}
        final_doc = probe.get("final_doc") if isinstance(probe.get("final_doc"), dict) else {}
        cleanup = _cleanup_vector_tenant_fixture(case_id, group, fixture)
        observed = {
            "read": [response.get("http_status"), response.get("code")],
            "returned_dimension_is_original": len(vector) == int(probe.get("dim_a") or 0),
            "returned_vector_real": bool(vector) and any(float(value) != 0.0 for value in vector),
            "content_preserved": hashlib.sha256(str(data.get("content") or "").encode("utf-8")).hexdigest() == probe.get("source_content_sha256"),
            "cross_dimension_rejected": probe.get("cross_attempt", {}).get("rejected"),
            "row_restored": final_doc.get("exists") is True
            and final_doc.get("content_sha256") == probe.get("source_content_sha256")
            and final_doc.get("vector_dimension") == probe.get("dim_a")
            and probe.get("restore_attempt", {}).get("rejected") is False,
            "original_empty_false": schema.get("row_empty_a") is False,
            "other_empty_true": schema.get("row_empty_b") is True,
            "other_dimension_zero": schema.get("row_vector_b_zero") is True,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and source_id > 0 and content_embed_dimension_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_original_real_dimension_source_via_api",
                    "write_response": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
                    "original_dimension": probe.get("dim_a"),
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "adapter_cross_dimension_then_restore_original_dimension",
                    "backend": probe.get("backend"),
                    "other_dimension": probe.get("dim_b"),
                    "cross_dimension_rejected": observed["cross_dimension_rejected"],
                    "row_restored": observed["row_restored"],
                    "original_empty_false": observed["original_empty_false"],
                    "other_empty_true": observed["other_empty_true"],
                    "other_dimension_zero": observed["other_dimension_zero"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "get_content_and_verify_only_real_original_vector_returned",
                    "response": observed["read"],
                    "returned_dimension_is_original": observed["returned_dimension_is_original"],
                    "returned_vector_real": observed["returned_vector_real"],
                    "content_preserved": observed["content_preserved"],
                    "raw_sha256": response.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "returned_content_embed": "original real dimension only",
                "experiment_other_dimension": "zero vector with empty=true",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-CONTENT-EMBED-REAL-DIMENSION-001",
                    "summary": f"{group} get_fields did not return only the restored real vector dimension",
                    "code_location": "memory/utils/gaussdb_conn.py:_content_embed_from_row",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms705() -> dict[str, Any]:
    case_id = "TC-MS-705"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group, message_count=3)
        message_ids = _message_id_set(fixture["rows"])
        source_id = min(message_ids) if message_ids else 0
        probe = _vector_dimension_action(
            case_id,
            group,
            "repeat_same_dimension_create_and_ensure",
            action="ddl_idempotent",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_id=fixture["memory_id"],
            source_message_id=source_id,
            expected_raw_count=3,
        )
        schema = probe.get("schema") if isinstance(probe.get("schema"), dict) else {}
        cleanup = _cleanup_vector_tenant_fixture(case_id, group, fixture)
        observed = {
            "writes": [[response.get("http_status"), response.get("code")] for response in fixture["responses"]],
            "ensure_error_count": probe.get("ensure_error_count"),
            "vector_column_count": schema.get("vector_column_count_a"),
            "empty_column_count": schema.get("empty_column_count_a"),
            "vector_index_present": schema.get("vector_index_present"),
            "physical_raw_count": fixture["snapshot"].get("raw_count"),
            "all_rows_preserved": probe.get("listed_ids") == sorted(message_ids),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and len(message_ids) == 3 and vector_ddl_idempotency_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_three_same_real_dimension_messages_via_api",
                    "write_responses": observed["writes"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "captured_id_count": len(message_ids),
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "repeat_same_dimension_adapter_create_and_ensure",
                    "backend": probe.get("backend"),
                    "dimension": probe.get("dim_a"),
                    "ensure_error_count": observed["ensure_error_count"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_single_vector_column_index_and_all_rows",
                    "vector_column_count": observed["vector_column_count"],
                    "empty_column_count": observed["empty_column_count"],
                    "vector_index_present": observed["vector_index_present"],
                    "all_rows_preserved": observed["all_rows_preserved"],
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "vector_column_count": 1,
                "vector_index": "one usable backend-native index",
                "physical_rows": 3,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-VECTOR-DDL-IDEMPOTENCY-001",
                    "summary": f"{group} repeated same-dimension create/ensure was not idempotent",
                    "code_location": "memory/utils/gaussdb_conn.py:create_idx",
                }
            ],
        }

    return _run_case(case_id, execute)


def _wait_managed_api(group: str, manager: Any, timeout: float = 180) -> float:
    started = time.monotonic()
    deadline = started + timeout
    label = f"{group}:api"
    while time.monotonic() < deadline:
        status = manager.ProcessRegistry(manager.STATE_PATH).status().get(label, {})
        if not status.get("alive") or not status.get("identity_matches"):
            raise RuntimeError(f"managed API exited before readiness: {group}")
        if AUTH._main_api_healthy(group):
            return time.monotonic() - started
        time.sleep(0.5)
    raise TimeoutError(f"managed API did not become ready: {group}")


def _exclusive_maintenance(
    case_id: str,
    group: str,
    label: str,
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", f"fresh_07_maintenance_manager_{case_id}_{group}_{label}")
    registry = manager.ProcessRegistry(manager.STATE_PATH)
    before = registry.status()
    api_label = f"{group}:api"
    worker_label = f"{group}:worker"
    api_was_running = bool(before.get(api_label, {}).get("alive") and before.get(api_label, {}).get("identity_matches"))
    worker_was_running = bool(before.get(worker_label, {}).get("alive") and before.get(worker_label, {}).get("identity_matches"))
    if not api_was_running:
        raise RuntimeError(f"managed API must be running before maintenance: {group}")

    worker_stop = registry.stop(worker_label)
    api_stop = registry.stop(api_label)
    stopped = registry.status()
    exclusive_window = not bool(stopped.get(api_label, {}).get("alive")) and not bool(stopped.get(worker_label, {}).get("alive"))
    action_result: dict[str, Any] = {}
    action_error: Exception | None = None
    api_ready_seconds: float | None = None
    try:
        if not exclusive_window:
            raise RuntimeError(f"failed to establish maintenance window: {group}")
        action_result = action()
    except Exception as exc:
        action_error = exc
    finally:
        manager.start_service(group, "api")
        api_ready_seconds = _wait_managed_api(group, manager)
        if worker_was_running:
            manager.start_service(group, "worker")

    after = registry.status()
    api_restored = bool(after.get(api_label, {}).get("alive") and after.get(api_label, {}).get("identity_matches"))
    worker_restored = bool(after.get(worker_label, {}).get("alive") and after.get(worker_label, {}).get("identity_matches")) == worker_was_running
    lifecycle = {
        "api_was_running": api_was_running,
        "worker_was_running": worker_was_running,
        "worker_stop_confirmed": bool(worker_stop.get("stopped")),
        "api_stop_confirmed": bool(api_stop.get("stopped")),
        "exclusive_window": exclusive_window,
        "api_restored": api_restored,
        "worker_state_restored": worker_restored,
        "api_ready_seconds": api_ready_seconds,
    }
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_lifecycle.json"
    _evidence_module().write_evidence(
        raw_path,
        {
            "maintenance_lifecycle": lifecycle,
            "action_raw_sha256": action_result.get("raw_sha256"),
        },
    )
    if action_error is not None:
        raise action_error
    return {
        "action": action_result,
        "lifecycle": lifecycle,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _sequence_maintenance_probe(
    case_id: str,
    group: str,
    label: str,
    *,
    action: str,
    seed_offset: int = 0,
) -> dict[str, Any]:
    payload = {"action": action, "seed_offset": seed_offset}
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from api.db.services.memory_service import MemoryService
from api.db.joint_services.memory_message_service import init_message_id_sequence
from memory.services.messages import MessageService
from rag.utils.redis_conn import REDIS_CONN

p = json.loads(sys.argv[1])
conn = settings.msgStoreConn
memories = list(MemoryService.get_all_memory())
memory_ids = [str(memory.id) for memory in memories]
tenant_ids = [str(memory.tenant_id) for memory in memories]
service_max = int(MessageService.get_max_message_id(tenant_ids, memory_ids)) if memories else 1

physical_values = []
backend = type(conn).__name__
if "Infinity" in backend:
    raw_conn = conn.connPool.get_conn()
    try:
        raw_db = raw_conn.get_database(conn.dbName)
        for table_name in raw_db.list_tables().table_names:
            if not str(table_name).startswith(conn.table_name_prefix):
                continue
            table = raw_db.get_table(table_name)
            columns = {str(row[0]) for row in table.show_columns().rows()}
            if "message_id" not in columns:
                continue
            frame, _ = table.output(["message_id"]).to_df()
            physical_values.extend(int(value) for value in frame["message_id"].tolist())
    finally:
        conn.connPool.release_conn(raw_conn)
else:
    table_rows, _ = conn._fetch_all_with_description(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_name LIKE %s ORDER BY table_name",
        [conn.schema, "ragflow_mem_%"],
    )
    for row in table_rows:
        table_name = str(row[0])
        selected, _ = conn._fetch_one_with_description(
            f"SELECT MAX(message_id) FROM {conn.ddl.qualified_name(table_name)}",
            [],
        )
        if selected is not None and selected[0] is not None:
            physical_values.append(int(selected[0]))
physical_max = max(physical_values, default=1)

key = "id_generator:memory"
requested_seed = None
if p["action"] in {"initialize_absent", "initialize_empty"}:
    REDIS_CONN.delete(key)
elif p["action"] == "preserve_existing":
    requested_seed = service_max + int(p["seed_offset"])
    REDIS_CONN.set(key, requested_seed)
else:
    raise ValueError("unknown sequence maintenance action")
key_exists_before = bool(REDIS_CONN.exist(key))
seed_before = int(REDIS_CONN.get(key)) if key_exists_before else None
init_message_id_sequence()
seed_after = int(REDIS_CONN.get(key)) if REDIS_CONN.exist(key) else None

pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": backend,
    "memory_count": len(memories),
    "service_max": service_max,
    "physical_max": physical_max,
    "physical_table_count": len(set(physical_values)) if physical_values else 0,
    "key_exists_before": key_exists_before,
    "seed_before": seed_before,
    "requested_seed": requested_seed,
    "seed_after": seed_after,
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=180,
        max_attempts=1,
    )


def _memory_size_maintenance_probe(
    case_id: str,
    group: str,
    label: str,
    *,
    tenant_id: str,
    memory_id: str,
) -> dict[str, Any]:
    payload = {"tenant_id": tenant_id, "memory_id": memory_id}
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from api.db.joint_services.memory_message_service import init_memory_size_cache
from common.doc_store.doc_store_base import OrderByExpr
from memory.services.messages import MessageService, index_name
from rag.utils.redis_conn import REDIS_CONN

p = json.loads(sys.argv[1])
tenant_id = p["tenant_id"]
memory_id = p["memory_id"]
conn = settings.msgStoreConn
key = f"memory_{memory_id}"
REDIS_CONN.delete(key)
cache_absent_before = not bool(REDIS_CONN.exist(key))
service_map = MessageService.calculate_memory_size([memory_id], [tenant_id])
service_size = int(service_map.get(memory_id, 0))

order_by = OrderByExpr()
order_by.desc("valid_at")
result, total = conn.search(
    select_fields=["memory_id", "content", "content_embed"],
    highlight_fields=[],
    condition={},
    match_expressions=[],
    order_by=order_by,
    offset=0,
    limit=2048,
    index_names=[index_name(tenant_id)],
    memory_ids=[memory_id],
    agg_fields=[],
    hide_forgotten=False,
)
docs = conn.get_fields(result, ["memory_id", "content", "content_embed"])
manual_size = 0
for doc in docs.values():
    content = doc.get("content", "")
    embed = doc.get("content_embed")
    embed_size = sys.getsizeof(embed[0]) * len(embed) if embed else 0
    manual_size += sys.getsizeof(content) + embed_size

init_memory_size_cache()
cache_value = int(REDIS_CONN.get(key)) if REDIS_CONN.exist(key) else None
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": type(conn).__name__,
    "cache_absent_before": cache_absent_before,
    "raw_count": int(total or 0),
    "normalized_row_count": len(docs),
    "service_size": service_size,
    "manual_size": manual_size,
    "cache_value": cache_value,
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=180,
        max_attempts=1,
    )


def _forgotten_scan_probe(
    case_id: str,
    group: str,
    label: str,
    *,
    tenant_id: str,
    memory_id: str,
    target_message_id: int,
) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "memory_id": memory_id,
        "target_message_id": target_message_id,
    }
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from memory.services.messages import index_name

p = json.loads(sys.argv[1])
conn = settings.msgStoreConn
fields = ["message_id", "content", "content_embed", "forget_at"]
adapter_exception = None
adapter_error_code = None
docs = {}
docs_without = {}
try:
    result = conn.get_forgotten_messages(
        select_fields=fields,
        index_name=index_name(p["tenant_id"]),
        memory_id=p["memory_id"],
        limit=512,
    )
    docs = conn.get_fields(result, fields) if result is not None else {}
    without_field = ["message_id", "content", "content_embed"]
    result_without = conn.get_forgotten_messages(
        select_fields=without_field,
        index_name=index_name(p["tenant_id"]),
        memory_id=p["memory_id"],
        limit=512,
    )
    docs_without = conn.get_fields(result_without, without_field) if result_without is not None else {}
except Exception as exc:
    adapter_exception = type(exc).__name__
    adapter_error_code = getattr(exc, "error_code", None)
rows = list(docs.values())
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": type(conn).__name__,
    "adapter_exception": adapter_exception,
    "adapter_error_code": adapter_error_code,
    "returned_count": len(rows),
    "message_ids": sorted(int(row.get("message_id") or 0) for row in rows),
    "selected_forget_at_nonempty": bool(rows) and all(row.get("forget_at") not in (None, "", "-") for row in rows),
    "selected_vector_dimensions": [len(row.get("content_embed") or []) for row in rows],
    "unselected_forget_at_absent": all("forget_at" not in row for row in docs_without.values()),
    "target_message_id": int(p["target_message_id"]),
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=120,
        max_attempts=2,
    )


def _missing_field_scan_probe(
    case_id: str,
    group: str,
    label: str,
    *,
    tenant_id: str,
    memory_id: str,
    message_id: int,
) -> dict[str, Any]:
    payload = {
        "tenant_id": tenant_id,
        "memory_id": memory_id,
        "message_id": message_id,
    }
    script = r"""
import hashlib, json, sys
from common import settings
settings.init_settings()
from memory.services.messages import MessageService, index_name

p = json.loads(sys.argv[1])
tenant_id = p["tenant_id"]
memory_id = p["memory_id"]
message_id = int(p["message_id"])
conn = settings.msgStoreConn
backend = type(conn).__name__
logical_index = index_name(tenant_id)
before = MessageService.get_by_message_id(memory_id, message_id, tenant_id) or {}
content = str(before.get("content") or "")
before_identity = {
    "message_id": int(before.get("message_id") or 0),
    "memory_id": str(before.get("memory_id") or ""),
    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    "vector_dimension": len(before.get("content_embed") or []),
}

native_column_absent = False
unsupported_exception = None
fixture_set_null = False
physical_field_is_null = False
returned = {}
selected_fields_exact = False
restore_succeeded = False
physical_field_restored = False
missing_after_restore_count = 0

if "Infinity" in backend:
    raw_conn = conn.connPool.get_conn()
    try:
        raw_db = raw_conn.get_database(conn.dbName)
        table = raw_db.get_table(f"{logical_index}_{memory_id}")
        columns = {str(row[0]) for row in table.show_columns().rows()}
        native_column_absent = "tokenized_content_ltks" not in columns
    finally:
        conn.connPool.release_conn(raw_conn)
    try:
        result = conn.get_missing_field_message(
            select_fields=["message_id", "content"],
            index_name=logical_index,
            memory_id=memory_id,
            field_name="tokenized_content_ltks",
            limit=512,
        )
        returned = conn.get_fields(result, ["message_id", "content"]) if result is not None else {}
    except Exception as exc:
        unsupported_exception = type(exc).__name__
else:
    fixture_set_null = bool(conn.update(
        {"message_id": message_id},
        {"remove": "tokenized_content_ltks"},
        logical_index,
        memory_id,
    ))
    table = conn.physical_table(logical_index)
    selected, _ = conn._fetch_one_with_description(
        f"SELECT tokenized_content_ltks FROM {conn.ddl.qualified_name(table)} WHERE memory_id=%s AND message_id=%s",
        [memory_id, message_id],
    )
    physical_field_is_null = selected is not None and selected[0] is None
    result = conn.get_missing_field_message(
        select_fields=["message_id", "content"],
        index_name=logical_index,
        memory_id=memory_id,
        field_name="tokenized_content_ltks",
        limit=512,
    )
    returned = conn.get_fields(result, ["message_id", "content"]) if result is not None else {}
    selected_fields_exact = bool(returned) and all(
        set(row) == {"message_id", "content"} for row in returned.values()
    )
    restore_succeeded = bool(conn.update(
        {"message_id": message_id},
        {"content": content},
        logical_index,
        memory_id,
    ))
    selected_after, _ = conn._fetch_one_with_description(
        f"SELECT tokenized_content_ltks FROM {conn.ddl.qualified_name(table)} WHERE memory_id=%s AND message_id=%s",
        [memory_id, message_id],
    )
    physical_field_restored = (
        selected_after is not None
        and selected_after[0] is not None
        and bool(str(selected_after[0]))
    )
    result_after = conn.get_missing_field_message(
        select_fields=["message_id", "content"],
        index_name=logical_index,
        memory_id=memory_id,
        field_name="tokenized_content_ltks",
        limit=512,
    )
    docs_after = conn.get_fields(result_after, ["message_id", "content"]) if result_after is not None else {}
    missing_after_restore_count = len(docs_after)

after = MessageService.get_by_message_id(memory_id, message_id, tenant_id) or {}
after_content = str(after.get("content") or "")
after_identity = {
    "message_id": int(after.get("message_id") or 0),
    "memory_id": str(after.get("memory_id") or ""),
    "content_sha256": hashlib.sha256(after_content.encode("utf-8")).hexdigest(),
    "vector_dimension": len(after.get("content_embed") or []),
}
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "backend": backend,
    "native_column_absent": native_column_absent,
    "unsupported_exception": unsupported_exception,
    "fixture_set_null": fixture_set_null,
    "physical_field_is_null": physical_field_is_null,
    "returned_count": len(returned),
    "message_ids": sorted(int(row.get("message_id") or 0) for row in returned.values()),
    "selected_fields_exact": selected_fields_exact,
    "restore_succeeded": restore_succeeded,
    "physical_field_restored": physical_field_restored,
    "missing_after_restore_count": missing_after_restore_count,
    "raw_before_after_unchanged": before_identity == after_identity,
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=120,
        max_attempts=2,
    )


def run_ms800() -> dict[str, Any]:
    case_id = "TC-MS-800"
    prefix = "fresh-ms-800"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent-{index}",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": f"sequence input {index}",
                    "agent_response": f"sequence response {index}",
                }
                for index in range(1, 4)
            ],
        )
        before_ids = _message_id_set(batch["rows"])
        maintenance = _exclusive_maintenance(
            case_id,
            group,
            "initialize_sequence_from_store_max",
            lambda: _sequence_maintenance_probe(
                case_id,
                group,
                "initialize_sequence_from_store_max",
                action="initialize_absent",
            ),
        )
        action_result = maintenance["action"]
        next_response = _add_message(
            case_id,
            group,
            owner["auth"],
            "write_next_message_after_sequence_init",
            [batch["memory_id"]],
            agent_id=f"{prefix}-{group}-agent-next",
            session_id=f"{prefix}-{group}-session",
            user_input="next sequence input",
            agent_response="next sequence response",
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_next_sequence_write",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        after_ids = _message_id_set(after.get("rows", []))
        new_ids = after_ids - before_ids
        expected_next = int(action_result.get("service_max") or 0) + 1
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "exclusive_window": maintenance["lifecycle"]["exclusive_window"],
            "key_absent_before_init": action_result.get("key_exists_before") is False,
            "service_max_positive": int(action_result.get("service_max") or 0) > 0,
            "service_max_equals_physical": action_result.get("service_max") == action_result.get("physical_max"),
            "initialized_seed_equals_max": action_result.get("seed_after") == action_result.get("service_max"),
            "next_write": [next_response.get("http_status"), next_response.get("code")],
            "new_id_equals_max_plus_one": new_ids == {expected_next},
            "no_collision": len(after_ids) == len(before_ids) + 1,
            "api_restored": maintenance["lifecycle"]["api_restored"],
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and len(before_ids) == 3 and sequence_seed_from_max_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_three_messages_and_capture_store_max",
                    "write_responses": [[response.get("http_status"), response.get("code")] for response in batch["responses"]],
                    "captured_id_count": len(before_ids),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "stop_id_producers_delete_group_key_and_initialize_once",
                    "backend": action_result.get("backend"),
                    "memory_count": action_result.get("memory_count"),
                    "service_max": action_result.get("service_max"),
                    "physical_max": action_result.get("physical_max"),
                    "key_absent_before_init": observed["key_absent_before_init"],
                    "seed_after": action_result.get("seed_after"),
                    "exclusive_window": observed["exclusive_window"],
                    "raw_sha256": [
                        action_result.get("raw_sha256"),
                        maintenance.get("raw_sha256"),
                    ],
                },
                {
                    "name": "restore_api_and_allocate_exact_next_id",
                    "api_restored": observed["api_restored"],
                    "response": observed["next_write"],
                    "new_id_equals_max_plus_one": observed["new_id_equals_max_plus_one"],
                    "no_collision": observed["no_collision"],
                    "raw_sha256": [
                        next_response.get("raw_sha256"),
                        after.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "seed": "exact MessageService and physical global max",
                "next_message_id": "max + 1 without collision",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEQUENCE-INITIALIZE-MAX-001",
                    "summary": f"{group} sequence initialization did not seed from the exact store maximum",
                    "code_location": "api/db/joint_services/memory_message_service.py:init_message_id_sequence",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms801() -> dict[str, Any]:
    case_id = "TC-MS-801"
    prefix = "fresh-ms-801"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "existing sequence input",
                    "agent_response": "existing sequence response",
                }
            ],
        )
        maintenance = _exclusive_maintenance(
            case_id,
            group,
            "preserve_existing_sequence_seed",
            lambda: _sequence_maintenance_probe(
                case_id,
                group,
                "preserve_existing_sequence_seed",
                action="preserve_existing",
                seed_offset=100,
            ),
        )
        action_result = maintenance["action"]
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "exclusive_window": maintenance["lifecycle"]["exclusive_window"],
            "db_max_positive": int(action_result.get("service_max") or 0) > 0,
            "existing_seed_gt_max": int(action_result.get("requested_seed") or 0) > int(action_result.get("service_max") or 0),
            "seed_before_equals_after": action_result.get("seed_before") == action_result.get("seed_after"),
            "initializer_skipped": action_result.get("key_exists_before") is True,
            "api_restored": maintenance["lifecycle"]["api_restored"],
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and len(_message_id_set(batch["rows"])) == 1 and existing_sequence_seed_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_message_and_capture_safe_seed_baseline",
                    "write": [
                        batch["responses"][0].get("http_status"),
                        batch["responses"][0].get("code"),
                    ],
                    "service_max": action_result.get("service_max"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "stop_id_producers_set_safe_existing_seed_and_initialize_once",
                    "requested_seed": action_result.get("requested_seed"),
                    "seed_before": action_result.get("seed_before"),
                    "seed_after": action_result.get("seed_after"),
                    "initializer_skipped": observed["initializer_skipped"],
                    "exclusive_window": observed["exclusive_window"],
                    "raw_sha256": [
                        action_result.get("raw_sha256"),
                        maintenance.get("raw_sha256"),
                    ],
                },
                {
                    "name": "restore_api_and_cleanup_memory_through_api",
                    "api_restored": observed["api_restored"],
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "existing_seed": "max + 100",
                "initializer": "skip and preserve exact seed",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-SEQUENCE-PRESERVE-EXISTING-001",
                    "summary": f"{group} sequence initialization did not preserve an existing safe seed",
                    "code_location": "api/db/joint_services/memory_message_service.py:init_message_id_sequence",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms802() -> dict[str, Any]:
    case_id = "TC-MS-802"
    prefix = "fresh-ms-802"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent-{index}",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": f"memory size input {index}",
                    "agent_response": f"memory size response {index}",
                }
                for index in range(1, 3)
            ],
        )
        maintenance = _exclusive_maintenance(
            case_id,
            group,
            "initialize_memory_size_cache",
            lambda: _memory_size_maintenance_probe(
                case_id,
                group,
                "initialize_memory_size_cache",
                tenant_id=owner["tenant_id"],
                memory_id=batch["memory_id"],
            ),
        )
        action_result = maintenance["action"]
        memory_cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        cache_key = f"memory_{batch['memory_id']}"
        redis_client = BASE._redis_client(group)
        redis_client.delete(cache_key)
        cache_fixture_cleanup = redis_client.get(cache_key) is None
        observed = {
            "exclusive_window": maintenance["lifecycle"]["exclusive_window"],
            "cache_absent_before_init": action_result.get("cache_absent_before") is True,
            "raw_count_positive": int(action_result.get("raw_count") or 0) > 0,
            "calculated_size_positive": int(action_result.get("manual_size") or 0) > 0,
            "service_size_equals_manual": action_result.get("service_size") == action_result.get("manual_size"),
            "cache_equals_manual": action_result.get("cache_value") == action_result.get("manual_size"),
            "api_restored": maintenance["lifecycle"]["api_restored"],
            "memory_cleanup_succeeded": memory_cleanup,
            "cache_fixture_cleanup_succeeded": cache_fixture_cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and len(_message_id_set(batch["rows"])) == 2 and memory_size_cache_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_messages_and_remove_only_target_cache_key",
                    "write_responses": [[response.get("http_status"), response.get("code")] for response in batch["responses"]],
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "cache_absent_before_init": observed["cache_absent_before_init"],
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "stop_id_producers_and_initialize_memory_size_cache_once",
                    "backend": action_result.get("backend"),
                    "raw_count": action_result.get("raw_count"),
                    "normalized_row_count": action_result.get("normalized_row_count"),
                    "service_size": action_result.get("service_size"),
                    "manual_size": action_result.get("manual_size"),
                    "cache_value": action_result.get("cache_value"),
                    "exclusive_window": observed["exclusive_window"],
                    "raw_sha256": [
                        action_result.get("raw_sha256"),
                        maintenance.get("raw_sha256"),
                    ],
                },
                {
                    "name": "restore_api_and_cleanup_memory_and_authorized_cache_fixture",
                    "api_restored": observed["api_restored"],
                    "memory_cleanup_succeeded": memory_cleanup,
                    "cache_fixture_cleanup_succeeded": cache_fixture_cleanup,
                    "cache_key_fingerprint": DB._fingerprint(cache_key),
                },
            ],
            "oracle": {
                "size_formula": "sys.getsizeof(content) + sys.getsizeof(vector[0]) * len(vector)",
                "cache": "exact manual and MessageService size",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-MEMORY-SIZE-CACHE-INITIALIZE-001",
                    "summary": f"{group} startup memory size cache differed from exact object-size calculation",
                    "code_location": "api/db/joint_services/memory_message_service.py:init_memory_size_cache",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms803() -> dict[str, Any]:
    case_id = "TC-MS-803"
    prefix = "fresh-ms-803"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent-{index}",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": f"forgotten scan input {index}",
                    "agent_response": f"forgotten scan response {index}",
                }
                for index in range(1, 3)
            ],
        )
        message_ids = sorted(_message_id_set(batch["rows"]))
        target_id = message_ids[0] if message_ids else 0
        forgotten = _forget_message(
            case_id,
            group,
            "forget_exact_scan_target",
            owner["auth"],
            batch["memory_id"],
            target_id,
        )
        physical = _store_snapshot(
            case_id,
            group,
            "read_only_forgotten_physical_snapshot",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        probe = _forgotten_scan_probe(
            case_id,
            group,
            "scan_forgotten_messages_via_adapter",
            tenant_id=owner["tenant_id"],
            memory_id=batch["memory_id"],
            target_message_id=target_id,
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        forgotten_rows = [row for row in physical.get("rows", []) if row.get("physical_forget_at_ms") is not None]
        observed = {
            "write_count": len(message_ids),
            "forget": [forgotten.get("http_status"), forgotten.get("code")],
            "adapter_exception": probe.get("adapter_exception"),
            "returned_count": probe.get("returned_count"),
            "returned_target_only": probe.get("message_ids") == [target_id],
            "selected_forget_at_nonempty": probe.get("selected_forget_at_nonempty"),
            "unselected_forget_at_absent": probe.get("unselected_forget_at_absent"),
            "physical_forgotten_count": len(forgotten_rows),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and target_id > 0 and forgotten_scan_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_two_messages_and_forget_one_through_api",
                    "write_count": len(message_ids),
                    "forget_response": observed["forget"],
                    "physical_forgotten_count": len(forgotten_rows),
                    "raw_sha256": [
                        batch["snapshot"].get("raw_sha256"),
                        forgotten.get("raw_sha256"),
                        physical.get("raw_sha256"),
                    ],
                },
                {
                    "name": "scan_forgotten_message_and_normalize_selected_fields",
                    "backend": probe.get("backend"),
                    "adapter_exception": probe.get("adapter_exception"),
                    "adapter_error_code": probe.get("adapter_error_code"),
                    "returned_count": probe.get("returned_count"),
                    "returned_target_only": observed["returned_target_only"],
                    "selected_forget_at_nonempty": observed["selected_forget_at_nonempty"],
                    "selected_vector_dimensions": probe.get("selected_vector_dimensions"),
                    "unselected_forget_at_absent": observed["unselected_forget_at_absent"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "returned_rows": 1,
                "message_id": "captured forgotten target",
                "forget_at": "selected and non-null only when requested",
            },
            "findings": []
            if passed
            else [
                {
                    "id": ("MS-INFINITY-FORGOTTEN-EXISTS-FILTER-001" if group == "control" and probe.get("adapter_exception") == "InfinityException" else "MS-FORGOTTEN-MAINTENANCE-SCAN-001"),
                    "summary": (
                        "control Infinity bound the exists filter as a nonexistent column and rejected the forgotten scan"
                        if group == "control" and probe.get("adapter_exception") == "InfinityException"
                        else f"{group} forgotten-message adapter scan did not return the exact forgotten row"
                    ),
                    "code_location": ("memory/utils/infinity_conn.py:get_forgotten_messages" if group == "control" else "memory/utils/gaussdb_conn.py:get_forgotten_messages"),
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms804() -> dict[str, Any]:
    case_id = "TC-MS-804"
    prefix = "fresh-ms-804"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        batch = _write_raw_fixture(
            case_id,
            group,
            owner,
            prefix,
            [
                {
                    "agent_id": f"{prefix}-{group}-agent",
                    "session_id": f"{prefix}-{group}-session",
                    "user_input": "missing field input",
                    "agent_response": "missing field response",
                }
            ],
        )
        message_ids = sorted(_message_id_set(batch["rows"]))
        target_id = message_ids[0] if message_ids else 0
        before_identity = _raw_snapshot_identity(batch["rows"])
        probe = _missing_field_scan_probe(
            case_id,
            group,
            "scan_and_restore_missing_tokenized_field",
            tenant_id=owner["tenant_id"],
            memory_id=batch["memory_id"],
            message_id=target_id,
        )
        after = _store_snapshot(
            case_id,
            group,
            "read_only_after_missing_field_probe",
            owner["tenant_id"],
            [batch["memory_id"]],
        )
        cleanup = _cleanup_memories(case_id, group, owner["auth"], [batch["memory_id"]])
        observed = {
            "native_column_absent": probe.get("native_column_absent"),
            "unsupported_exception": probe.get("unsupported_exception"),
            "fixture_set_null": probe.get("fixture_set_null"),
            "physical_field_is_null": probe.get("physical_field_is_null"),
            "returned_count": probe.get("returned_count"),
            "returned_target_only": probe.get("message_ids") == [target_id],
            "selected_fields_exact": probe.get("selected_fields_exact"),
            "restore_succeeded": probe.get("restore_succeeded"),
            "physical_field_restored": probe.get("physical_field_restored"),
            "missing_after_restore_count": probe.get("missing_after_restore_count"),
            "raw_before_after_unchanged": probe.get("raw_before_after_unchanged") is True and before_identity == _raw_snapshot_identity(after.get("rows", [])),
            "cleanup_succeeded": cleanup,
        }
        passed = batch["preclean"] and batch["writes_succeeded"] and target_id > 0 and missing_field_scan_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_dedicated_message_and_capture_native_row",
                    "write": [
                        batch["responses"][0].get("http_status"),
                        batch["responses"][0].get("code"),
                    ],
                    "physical_raw_count": batch["snapshot"].get("raw_count"),
                    "raw_sha256": batch["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "exercise_backend_specific_missing_field_scan",
                    "backend": probe.get("backend"),
                    "native_column_absent": observed["native_column_absent"],
                    "unsupported_exception": observed["unsupported_exception"],
                    "fixture_set_null": observed["fixture_set_null"],
                    "physical_field_is_null": observed["physical_field_is_null"],
                    "returned_count": observed["returned_count"],
                    "returned_target_only": observed["returned_target_only"],
                    "selected_fields_exact": observed["selected_fields_exact"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "restore_tokenized_field_and_verify_original_row",
                    "restore_succeeded": observed["restore_succeeded"],
                    "physical_field_restored": observed["physical_field_restored"],
                    "missing_after_restore_count": observed["missing_after_restore_count"],
                    "raw_before_after_unchanged": observed["raw_before_after_unchanged"],
                    "raw_sha256": after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "control": "safe AssertionError because native schema has no tokenized_content_ltks",
                "experiment": "exact NULL row returned, then content update restores tokenization",
                "startup_repair_claim": "none for non-Elasticsearch backends",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-MISSING-FIELD-MAINTENANCE-SCAN-001",
                    "summary": f"{group} missing-field adapter behavior or restoration differed from its backend contract",
                    "code_location": "memory/utils/gaussdb_conn.py:get_missing_field_message",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms805() -> dict[str, Any]:
    case_id = "TC-MS-805"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        metadata_memory_count = MS._memory_table_count(group)
        if metadata_memory_count != 0:
            return {
                "status": "FAIL",
                "steps": [
                    {
                        "name": "read_only_confirm_empty_metadata_precondition",
                        "metadata_memory_count": metadata_memory_count,
                    }
                ],
                "oracle": {"metadata_memory_count": 0, "seed": 1},
                "findings": [
                    {
                        "id": "MS-EMPTY-SEQUENCE-PRECONDITION-001",
                        "summary": f"{group} metadata was not empty before the empty-store sequence test",
                        "code_location": "api/db/joint_services/memory_message_service.py:init_message_id_sequence",
                    }
                ],
            }
        maintenance = _exclusive_maintenance(
            case_id,
            group,
            "initialize_empty_store_sequence",
            lambda: _sequence_maintenance_probe(
                case_id,
                group,
                "initialize_empty_store_sequence",
                action="initialize_empty",
            ),
        )
        action_result = maintenance["action"]
        observed = {
            "exclusive_window": maintenance["lifecycle"]["exclusive_window"],
            "metadata_memory_count": metadata_memory_count,
            "key_absent_before_init": action_result.get("key_exists_before") is False,
            "initialized_seed": action_result.get("seed_after"),
            "api_restored": maintenance["lifecycle"]["api_restored"],
        }
        passed = action_result.get("memory_count") == 0 and empty_store_seed_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "read_only_confirm_no_memory_metadata",
                    "metadata_memory_count": metadata_memory_count,
                    "message_store_memory_count": action_result.get("memory_count"),
                },
                {
                    "name": "stop_id_producers_delete_group_key_and_initialize_empty_store",
                    "key_absent_before_init": observed["key_absent_before_init"],
                    "initialized_seed": observed["initialized_seed"],
                    "exclusive_window": observed["exclusive_window"],
                    "raw_sha256": [
                        action_result.get("raw_sha256"),
                        maintenance.get("raw_sha256"),
                    ],
                },
                {
                    "name": "restore_group_api",
                    "api_restored": observed["api_restored"],
                },
            ],
            "oracle": {"metadata_memory_count": 0, "redis_seed": 1},
            "findings": []
            if passed
            else [
                {
                    "id": "MS-EMPTY-SEQUENCE-INITIALIZE-001",
                    "summary": f"{group} empty-store sequence initialization did not seed exactly 1",
                    "code_location": "api/db/joint_services/memory_message_service.py:init_message_id_sequence",
                }
            ],
        }

    return _run_case(case_id, execute)


def _cleanup_catalog_fixture(
    case_id: str,
    group: str,
    fixture: dict[str, Any],
    *,
    extra_memory_ids: list[str] | None = None,
) -> dict[str, bool]:
    secondary = fixture["secondary"]
    memory_ids = [fixture["memory_id"], *(extra_memory_ids or [])]
    memory_cleanup = _cleanup_memories(case_id, group, secondary["auth"], memory_ids)
    model_cleanup = _cleanup_secondary_embedding(case_id, group, secondary, fixture["configured"])
    user_cleanup = BASE._cleanup_secondary_user(case_id, group, fixture["email"])
    return {
        "memory": memory_cleanup,
        "model": model_cleanup,
        "user": user_cleanup["succeeded"],
        "succeeded": memory_cleanup and model_cleanup and user_cleanup["succeeded"],
    }


def _catalog_probe(
    case_id: str,
    group: str,
    label: str,
    *,
    action: str,
    tenant_id: str,
    memory_ids: list[str],
    dimension: int,
) -> dict[str, Any]:
    payload = {
        "action": action,
        "tenant_id": tenant_id,
        "memory_ids": memory_ids,
        "dimension": dimension,
    }
    script = r"""
import hashlib, json, os, re, sys
from common import settings
settings.init_settings()
from common.file_utils import get_project_base_directory
from memory.services.messages import MessageService, index_name

p = json.loads(sys.argv[1])
conn = settings.msgStoreConn
backend = type(conn).__name__
logical_index = index_name(p["tenant_id"])
memory_ids = p["memory_ids"]
dimension = int(p["dimension"])

def normalized_text(value):
    return " ".join(str(value or "").lower().replace('"', '').split())

def snapshot():
    if "Infinity" in backend:
        raw_conn = conn.connPool.get_conn()
        try:
            raw_db = raw_conn.get_database(conn.dbName)
            physical_names = [f"{logical_index}_{memory_id}" for memory_id in memory_ids]
            table_flags = []
            row_count = 0
            primary_columns = []
            index_names = []
            index_details = []
            for position, table_name in enumerate(physical_names):
                try:
                    table = raw_db.get_table(table_name)
                    table_flags.append(True)
                except Exception:
                    table_flags.append(False)
                    continue
                column_rows = list(table.show_columns().rows())
                if position == 0:
                    primary_columns = [
                        {
                            "name": str(row[0]),
                            "type": str(row[1]),
                            "default": None if row[2] is None else str(row[2]),
                        }
                        for row in column_rows
                    ]
                    index_names = [str(name) for name in table.list_indexes().index_names]
                    for name in index_names:
                        detail = table.show_index(name)
                        index_details.append({
                            "name": name,
                            "type": str(getattr(detail, "index_type", "")),
                            "columns": str(getattr(detail, "index_column_names", "")),
                            "parameters": str(getattr(detail, "other_parameters", "")),
                        })
                frame, _ = table.output(["message_id"]).to_df()
                row_count += len(frame.index)
        finally:
            conn.connPool.release_conn(raw_conn)

        mapping_path = os.path.join(
            get_project_base_directory(), "conf", conn.mapping_file_name
        )
        with open(mapping_path, encoding="utf-8") as stream:
            mapping = json.load(stream)
        column_map = {item["name"]: item for item in primary_columns}
        mapping_columns_complete = set(mapping).issubset(column_map)
        native_types_complete = all(
            str(column_map[name]["type"]).lower().startswith(
                str(config["type"]).split(",", 1)[0].lower()
            )
            for name, config in mapping.items()
            if name in column_map
        )
        vector_name = f"q_{dimension}_vec"
        expected_indexes = {
            "q_vec_idx",
            "ft_content_rag_coarse",
            "ft_content_rag_fine",
        }
        details = {item["name"]: item for item in index_details}
        fulltext_names = sorted(
            name for name in index_names if name.startswith("ft_content_")
        )
        fulltext_details = [details[name] for name in fulltext_names]
        vector_detail = details.get("q_vec_idx", {})
        vector_parameters = normalized_text(vector_detail.get("parameters"))
        return {
            "backend": backend,
            "logical_index": logical_index,
            "physical_names": physical_names,
            "physical_table_count": sum(table_flags),
            "unique_physical_table_count": len(
                {name for name, exists in zip(physical_names, table_flags) if exists}
            ),
            "all_tables_exist": bool(table_flags) and all(table_flags),
            "native_names_exact": all(
                name == f"{logical_index}_{memory_id}"
                for name, memory_id in zip(physical_names, memory_ids)
            ),
            "sha1_name_exact": None,
            "table_exists": bool(table_flags and table_flags[0]),
            "columns": primary_columns,
            "column_count": len(primary_columns),
            "mapping_or_base_columns_complete": mapping_columns_complete,
            "native_types_complete": native_types_complete,
            "base_types_exact": None,
            "required_not_null_exact": None,
            "defaults_exact": None,
            "ustore": None,
            "vector_column_exact": vector_name in column_map,
            "empty_column_count": sum(
                item["name"].endswith("_vec_empty") for item in primary_columns
            ),
            "index_names": sorted(index_names),
            "index_details": sorted(index_details, key=lambda item: item["name"]),
            "duplicate_index_count": len(index_names) - len(set(index_names)),
            "native_index_names_exact": set(index_names) == expected_indexes,
            "native_index_types_exact": (
                "hnsw" in normalized_text(vector_detail.get("type"))
                and all(
                    "fulltext" in normalized_text(item.get("type"))
                    for item in fulltext_details
                )
            ),
            "regular_index_count": None,
            "regular_index_columns_exact": None,
            "primary_key_present": None,
            "fulltext_index_names_exact": fulltext_names
            == ["ft_content_rag_coarse", "ft_content_rag_fine"],
            "fulltext_types_exact": all(
                "fulltext" in normalized_text(item.get("type"))
                for item in fulltext_details
            ),
            "fulltext_columns_exact": all(
                "content" in normalized_text(item.get("columns"))
                for item in fulltext_details
            ),
            "ugin_index_count": None,
            "ugin_name_exact": None,
            "ugin_expression_exact": None,
            "vector_index_count": int("q_vec_idx" in index_names),
            "vector_index_dimension_exact": vector_name
            in normalized_text(vector_detail.get("columns")),
            "cosine_metric": "cosine" in vector_parameters,
            "hnsw": "hnsw" in normalized_text(vector_detail.get("type")),
            "gsdiskann": False,
            "required_indexes_complete": set(index_names) == expected_indexes,
            "physical_raw_count": row_count,
            "index_exist": bool(conn.index_exist(logical_index, memory_ids[0])),
            "advisory_lock_sql_exact": None,
            "catalog_signature": {
                "physical_names": sorted(physical_names),
                "columns": sorted((item["name"], item["type"]) for item in primary_columns),
                "indexes": sorted(
                    (
                        item["name"],
                        item["type"],
                        item["columns"],
                        item["parameters"],
                    )
                    for item in index_details
                ),
            },
        }

    table_name = conn.physical_table(logical_index)
    expected_table = "ragflow_mem_" + hashlib.sha1(
        logical_index.encode("utf-8")
    ).hexdigest()[:32]
    table_exists = bool(conn._table_exists(table_name))
    columns = []
    index_details = []
    reloptions = ""
    row_count = 0
    if table_exists:
        column_rows, _ = conn._fetch_all_with_description(
            "SELECT column_name,data_type,udt_name,is_nullable,column_default,ordinal_position "
            "FROM information_schema.columns WHERE table_schema=%s AND table_name=%s "
            "ORDER BY ordinal_position",
            [conn.schema, table_name],
        )
        columns = [
            {
                "name": str(row[0]),
                "data_type": str(row[1]),
                "udt_name": str(row[2]),
                "is_nullable": str(row[3]),
                "default": None if row[4] is None else str(row[4]),
                "ordinal": int(row[5]),
            }
            for row in column_rows
        ]
        index_rows, _ = conn._fetch_all_with_description(
            "SELECT indexname,indexdef FROM pg_indexes "
            "WHERE schemaname=%s AND tablename=%s ORDER BY indexname",
            [conn.schema, table_name],
        )
        index_details = [
            {"name": str(row[0]), "definition": str(row[1])}
            for row in index_rows
        ]
        build_ustore_check_sql = getattr(
            conn.ddl, "build_ustore_check_sql", None
        )
        if callable(build_ustore_check_sql):
            ustore_sql, ustore_params = build_ustore_check_sql(table_name)
        else:
            ustore_sql = (
                "SELECT c.reloptions FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = %s AND c.relname = %s"
            )
            ustore_params = [conn.schema, table_name]
        ustore_row, _ = conn._fetch_one_with_description(ustore_sql, ustore_params)
        reloptions = str(ustore_row[0] if ustore_row is not None else "")
        placeholders = ",".join(["%s"] * len(memory_ids))
        count_row, _ = conn._fetch_one_with_description(
            f"SELECT COUNT(*) FROM {conn.ddl.qualified_name(table_name)} "
            f"WHERE memory_id IN ({placeholders})",
            memory_ids,
        )
        row_count = int(count_row[0]) if count_row is not None else 0

    from memory.utils.gaussdb_conn import BASE_COLUMNS
    column_map = {item["name"]: item for item in columns}
    index_map = {item["name"]: item["definition"] for item in index_details}
    regular_expected = {
        conn.ddl.index_name(table_name, suffix): tuple(index_columns)
        for suffix, index_columns in conn.ddl.REGULAR_INDEXES
    }
    pk_name = conn.ddl.index_name(table_name, "pk")
    ugin_name = conn.ddl.index_name(table_name, "tokenized_ugin")
    diskann_name = conn.ddl.index_name(table_name, f"q_{dimension}_vec_diskann")

    def type_ok(name):
        item = column_map.get(name, {})
        data_type = normalized_text(item.get("data_type"))
        udt_name = normalized_text(item.get("udt_name"))
        if name in {"id", "message_type_kwd", "memory_id", "user_id", "agent_id", "session_id"}:
            return "char" in data_type or "varchar" in udt_name
        if name in {"message_id", "source_id", "zone_id", "status_int"}:
            return "numeric" in data_type or "int" in data_type or "number" in udt_name
        if name in {"valid_at", "invalid_at", "forget_at"}:
            return "timestamp" in data_type
        if name in {"content_ltks", "tokenized_content_ltks"}:
            return data_type == "text" or udt_name == "text"
        return False

    regular_columns_exact = all(
        name in index_map
        and all(column in normalized_text(index_map[name]) for column in index_columns)
        for name, index_columns in regular_expected.items()
    )
    ugin_definition = normalized_text(index_map.get(ugin_name))
    diskann_definition = normalized_text(index_map.get(diskann_name))
    advisory_sql, advisory_params = conn.ddl.build_advisory_lock_sql(
        f"gaussdb_memory_create_table:{table_name}"
    )
    index_names = list(index_map)
    return {
        "backend": backend,
        "logical_index": logical_index,
        "physical_names": [table_name for _ in memory_ids],
        "physical_table_count": int(table_exists),
        "unique_physical_table_count": int(table_exists),
        "all_tables_exist": table_exists,
        "native_names_exact": None,
        "sha1_name_exact": table_name == expected_table,
        "table_exists": table_exists,
        "columns": columns,
        "column_count": len(columns),
        "mapping_or_base_columns_complete": set(BASE_COLUMNS).issubset(column_map),
        "native_types_complete": None,
        "base_types_exact": all(type_ok(name) for name in BASE_COLUMNS),
        "required_not_null_exact": all(
            column_map.get(name, {}).get("is_nullable") == "NO"
            for name in ("id", "message_id", "memory_id", "status_int")
        ),
        "defaults_exact": (
            "0" in str(column_map.get("zone_id", {}).get("default") or "")
            and "1" in str(column_map.get("status_int", {}).get("default") or "")
        ),
        "ustore": "ustore" in reloptions.lower(),
        "vector_column_exact": f"q_{dimension}_vec" in column_map,
        "empty_column_count": sum(
            name.endswith("_vec_empty") for name in column_map
        ),
        "index_names": sorted(index_names),
        "index_details": index_details,
        "duplicate_index_count": len(index_names) - len(set(index_names)),
        "native_index_names_exact": None,
        "native_index_types_exact": None,
        "regular_index_count": sum(name in index_map for name in regular_expected),
        "regular_index_columns_exact": regular_columns_exact,
        "primary_key_present": pk_name in index_map,
        "fulltext_index_names_exact": None,
        "fulltext_types_exact": None,
        "fulltext_columns_exact": None,
        "ugin_index_count": int(ugin_name in index_map),
        "ugin_name_exact": ugin_name in index_map,
        "ugin_expression_exact": (
            "using ugin" in ugin_definition
            and "to_tsvector('simple'" in ugin_definition
            and "tokenized_content_ltks" in ugin_definition
        ),
        "vector_index_count": int(diskann_name in index_map),
        "vector_index_dimension_exact": (
            f"q_{dimension}_vec" in diskann_definition
        ),
        "cosine_metric": "cosine" in diskann_definition,
        "hnsw": False,
        "gsdiskann": "using gsdiskann" in diskann_definition,
        "required_indexes_complete": (
            set(regular_expected).issubset(index_map)
            and pk_name in index_map
            and ugin_name in index_map
            and diskann_name in index_map
        ),
        "physical_raw_count": row_count,
        "index_exist": bool(conn.index_exist(logical_index, memory_ids[0])),
        "advisory_lock_sql_exact": (
            advisory_sql == "SELECT pg_advisory_xact_lock(hashtext(%s))"
            and advisory_params
            == [f"gaussdb_memory_create_table:{table_name}"]
        ),
        "catalog_signature": {
            "physical_names": [table_name],
            "columns": sorted(
                (item["name"], item["data_type"], item["udt_name"])
                for item in columns
            ),
            "indexes": sorted(
                (item["name"], normalized_text(item["definition"]))
                for item in index_details
            ),
            "reloptions": reloptions,
        },
    }

before = snapshot()
create_error_count = 0
delete_succeeded = None
dropped_index_name = None
after_drop = None
restored = None
if p["action"] == "repeat_create":
    try:
        conn.create_idx(logical_index, memory_ids[0], dimension)
    except Exception:
        create_error_count += 1
elif p["action"] == "delete_idx":
    try:
        delete_succeeded = conn.delete_idx(logical_index, memory_ids[0]) is not False
    except Exception:
        delete_succeeded = False
elif p["action"] == "drop_restore_index":
    if "Infinity" in backend:
        from infinity.common import ConflictType
        raw_conn = conn.connPool.get_conn()
        try:
            raw_db = raw_conn.get_database(conn.dbName)
            table = raw_db.get_table(f"{logical_index}_{memory_ids[0]}")
            dropped_index_name = "ft_content_rag_coarse"
            table.drop_index(dropped_index_name, ConflictType.Ignore)
        finally:
            conn.connPool.release_conn(raw_conn)
    else:
        table_name = conn.physical_table(logical_index)
        dropped_index_name = conn.ddl.index_name(table_name, "message_id")
        conn._execute_write(
            f"DROP INDEX {conn.ddl.qualified_name(dropped_index_name)}", []
        )
    after_drop = snapshot()
    conn.create_idx(logical_index, memory_ids[0], dimension)
    restored = snapshot()
elif p["action"] != "inspect":
    raise ValueError("unknown catalog action")

after = snapshot()
pool = getattr(conn, "connPool", None)
if callable(getattr(pool, "destroy", None)):
    pool.destroy()
print("__FRESH_RESULT__" + json.dumps({
    "action": p["action"],
    "before": before,
    "after": after,
    "after_drop": after_drop,
    "restored": restored,
    "create_error_count": create_error_count,
    "delete_succeeded": delete_succeeded,
    "dropped_index_name": dropped_index_name,
}, sort_keys=True))
"""
    return MS._run_store_probe(
        case_id,
        group,
        label,
        script,
        [json.dumps(payload, sort_keys=True)],
        timeout=300,
        max_attempts=1,
    )


def _concurrent_first_writes(
    case_id: str,
    group: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    import concurrent.futures
    import threading

    manager = _load_module("fresh_service_manager.py", f"fresh_07_concurrent_log_{group}")
    status = manager.ProcessRegistry(manager.STATE_PATH).status()[f"{group}:api"]
    log_path = Path(status["log_path"])
    log_offset = log_path.stat().st_size if log_path.exists() else 0
    barrier = threading.Barrier(3)

    def write(index: int) -> dict[str, Any]:
        ready_ns = time.time_ns()
        barrier.wait(timeout=30)
        started_ns = time.time_ns()
        response = _add_message(
            case_id,
            group,
            fixture["secondary"]["auth"],
            f"concurrent_first_write_{index}",
            [fixture["memory_id"]],
            agent_id=f"fresh-ms-908-{group}-agent-{index}",
            session_id=f"fresh-ms-908-{group}-session",
            user_input=f"concurrent first input {index}",
            agent_response=f"concurrent first response {index}",
        )
        return {
            "index": index,
            "ready_ns": ready_ns,
            "started_ns": started_ns,
            "finished_ns": time.time_ns(),
            "response": response,
        }

    barrier_released = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write, index) for index in (1, 2)]
        barrier.wait(timeout=30)
        barrier_released = True
        results = [future.result(timeout=300) for future in futures]
    results.sort(key=lambda item: item["index"])
    log_slice = b""
    if log_path.exists():
        with log_path.open("rb") as stream:
            stream.seek(log_offset)
            log_slice = stream.read()
    raw_log_path = RAW_DIR / f"{case_id}_{group}_concurrent_first_write_api.log"
    raw_log_path.write_bytes(log_slice)
    raw_log_path.chmod(0o600)
    timeline_path = RAW_DIR / f"{case_id}_{group}_concurrent_timeline.json"
    timeline = [
        {
            "index": item["index"],
            "ready_ns": item["ready_ns"],
            "started_ns": item["started_ns"],
            "finished_ns": item["finished_ns"],
            "http_status": item["response"].get("http_status"),
            "code": item["response"].get("code"),
            "raw_sha256": item["response"].get("raw_sha256"),
        }
        for item in results
    ]
    _evidence_module().write_evidence(
        timeline_path,
        {
            "barrier_released": barrier_released,
            "timeline": timeline,
            "api_log_line_count": len(log_slice.splitlines()),
            "api_log_sha256": hashlib.sha256(log_slice).hexdigest(),
        },
    )
    started_values = [item["started_ns"] for item in results]
    return {
        "responses": [[item["response"].get("http_status"), item["response"].get("code")] for item in results],
        "barrier_released": barrier_released,
        "start_spread_ms": (max(started_values) - min(started_values)) / 1_000_000,
        "timeline_raw_sha256": hashlib.sha256(timeline_path.read_bytes()).hexdigest(),
        "api_log_raw_sha256": hashlib.sha256(raw_log_path.read_bytes()).hexdigest(),
    }


def run_ms900() -> dict[str, Any]:
    case_id = "TC-MS-900"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        models = fixture["configured"]["model_values"]
        second_memory = _create_memory_with_model_ids(
            case_id,
            group,
            fixture["secondary"]["auth"],
            "create_second_catalog_memory_same_tenant",
            f"fresh-ms-900-{group}-second",
            embd_id=models["embd_id"],
            llm_id=models["llm_id"],
        )
        second_write = _add_message(
            case_id,
            group,
            fixture["secondary"]["auth"],
            "write_second_same_tenant_catalog_message",
            [second_memory["id"]],
            agent_id=f"fresh-ms-900-{group}-agent-2",
            session_id=f"fresh-ms-900-{group}-session",
            user_input="second table naming input",
            agent_response="second table naming response",
        )
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "inspect_backend_physical_table_names",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"], second_memory["id"]],
            dimension=dimension,
        )
        catalog = probe["before"]
        cleanup = _cleanup_catalog_fixture(
            case_id,
            group,
            fixture,
            extra_memory_ids=[second_memory["id"]],
        )
        observed = {
            "writes": [
                [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
                [second_write.get("http_status"), second_write.get("code")],
            ],
            "all_tables_exist": catalog.get("all_tables_exist"),
            "physical_raw_count": catalog.get("physical_raw_count"),
            "native_names_exact": catalog.get("native_names_exact"),
            "sha1_name_exact": catalog.get("sha1_name_exact"),
            "unique_physical_table_count": catalog.get("unique_physical_table_count"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and second_memory["response"].get("code") == 0 and dimension > 0 and physical_table_name_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_one_message_to_each_same_tenant_memory",
                    "responses": observed["writes"],
                    "dimension": dimension,
                    "raw_sha256": [
                        fixture["snapshot"].get("raw_sha256"),
                        second_write.get("raw_sha256"),
                    ],
                },
                {
                    "name": "read_only_verify_backend_native_physical_names",
                    "backend": catalog.get("backend"),
                    "physical_table_count": catalog.get("physical_table_count"),
                    "unique_physical_table_count": observed["unique_physical_table_count"],
                    "all_tables_exist": observed["all_tables_exist"],
                    "native_names_exact": observed["native_names_exact"],
                    "sha1_name_exact": observed["sha1_name_exact"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memories_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "two existing per-memory Infinity tables",
                "experiment": "one shared ragflow_mem_<sha1(logical_index)> table",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-TABLE-NAME-001",
                    "summary": f"{group} physical Memory table naming or tenant sharing differed from its backend contract",
                    "code_location": "memory/utils/gaussdb_conn.py:GaussDBMemoryDDLBuilder.physical_table_name",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms901() -> dict[str, Any]:
    case_id = "TC-MS-901"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "inspect_backend_native_columns",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        catalog = probe["before"]
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "table_exists": catalog.get("table_exists"),
            "mapping_or_base_columns_complete": catalog.get("mapping_or_base_columns_complete"),
            "vector_column_exact": catalog.get("vector_column_exact"),
            "physical_raw_count": catalog.get("physical_raw_count"),
            "empty_column_count": catalog.get("empty_column_count"),
            "native_types_complete": catalog.get("native_types_complete"),
            "base_types_exact": catalog.get("base_types_exact"),
            "required_not_null_exact": catalog.get("required_not_null_exact"),
            "defaults_exact": catalog.get("defaults_exact"),
            "ustore": catalog.get("ustore"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and dimension > 0 and base_schema_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_real_vector_message_to_materialize_schema",
                    "response": observed["write"],
                    "dimension": dimension,
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "read_only_inspect_columns_types_nullability_and_storage",
                    "backend": catalog.get("backend"),
                    "table_exists": observed["table_exists"],
                    "column_count": catalog.get("column_count"),
                    "mapping_or_base_columns_complete": observed["mapping_or_base_columns_complete"],
                    "vector_column_exact": observed["vector_column_exact"],
                    "empty_column_count": observed["empty_column_count"],
                    "native_types_complete": observed["native_types_complete"],
                    "base_types_exact": observed["base_types_exact"],
                    "required_not_null_exact": observed["required_not_null_exact"],
                    "defaults_exact": observed["defaults_exact"],
                    "ustore": observed["ustore"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "current Infinity mapping plus real vector column",
                "experiment": "15 BASE_COLUMNS, vector/empty columns and USTORE",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-BASE-SCHEMA-001",
                    "summary": f"{group} physical Memory columns or types were incomplete",
                    "code_location": "memory/utils/gaussdb_conn.py:GaussDBMemoryDDLBuilder.build_memory_table_ddl",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms902() -> dict[str, Any]:
    case_id = "TC-MS-902"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "inspect_backend_native_base_indexes",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        catalog = probe["before"]
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "table_exists": catalog.get("table_exists"),
            "duplicate_index_count": catalog.get("duplicate_index_count"),
            "native_index_names_exact": catalog.get("native_index_names_exact"),
            "native_index_types_exact": catalog.get("native_index_types_exact"),
            "regular_index_count": catalog.get("regular_index_count"),
            "regular_index_columns_exact": catalog.get("regular_index_columns_exact"),
            "primary_key_present": catalog.get("primary_key_present"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and dimension > 0 and base_index_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_message_and_materialize_backend_indexes",
                    "response": observed["write"],
                    "dimension": dimension,
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "read_only_inspect_native_index_catalog",
                    "backend": catalog.get("backend"),
                    "index_count": len(catalog.get("index_names", [])),
                    "duplicate_index_count": observed["duplicate_index_count"],
                    "native_index_names_exact": observed["native_index_names_exact"],
                    "native_index_types_exact": observed["native_index_types_exact"],
                    "regular_index_count": observed["regular_index_count"],
                    "regular_index_columns_exact": observed["regular_index_columns_exact"],
                    "primary_key_present": observed["primary_key_present"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "q_vec_idx plus two current mapping fulltext indexes",
                "experiment": "seven regular indexes plus primary key",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-BASE-INDEXES-001",
                    "summary": f"{group} physical Memory base indexes were incomplete or duplicated",
                    "code_location": "memory/utils/gaussdb_conn.py:GaussDBMemoryDDLBuilder.build_regular_index_ddls",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms903() -> dict[str, Any]:
    case_id = "TC-MS-903"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "inspect_backend_fulltext_index",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        catalog = probe["before"]
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "fulltext_index_names_exact": catalog.get("fulltext_index_names_exact"),
            "fulltext_types_exact": catalog.get("fulltext_types_exact"),
            "fulltext_columns_exact": catalog.get("fulltext_columns_exact"),
            "ugin_index_count": catalog.get("ugin_index_count"),
            "ugin_name_exact": catalog.get("ugin_name_exact"),
            "ugin_expression_exact": catalog.get("ugin_expression_exact"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and dimension > 0 and fulltext_index_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_message_and_materialize_fulltext_index",
                    "response": observed["write"],
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_fulltext_index_definition",
                    "backend": catalog.get("backend"),
                    "fulltext_index_names_exact": observed["fulltext_index_names_exact"],
                    "fulltext_types_exact": observed["fulltext_types_exact"],
                    "fulltext_columns_exact": observed["fulltext_columns_exact"],
                    "ugin_index_count": observed["ugin_index_count"],
                    "ugin_name_exact": observed["ugin_name_exact"],
                    "ugin_expression_exact": observed["ugin_expression_exact"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "rag-coarse and rag-fine FullText indexes on content",
                "experiment": "one tokenized_ugin simple tsvector expression index",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-FULLTEXT-INDEX-001",
                    "summary": f"{group} physical Memory fulltext index differed from its native contract",
                    "code_location": "memory/utils/gaussdb_conn.py:GaussDBMemoryDDLBuilder.build_fulltext_ugin_ddl",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms904() -> dict[str, Any]:
    case_id = "TC-MS-904"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "inspect_backend_vector_index",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        catalog = probe["before"]
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "dimension_positive": dimension > 0,
            "vector_index_count": catalog.get("vector_index_count"),
            "vector_index_dimension_exact": catalog.get("vector_index_dimension_exact"),
            "cosine_metric": catalog.get("cosine_metric"),
            "hnsw": catalog.get("hnsw"),
            "gsdiskann": catalog.get("gsdiskann"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and vector_index_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_real_vector_message_and_capture_dimension",
                    "response": observed["write"],
                    "dimension": dimension,
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "read_only_verify_native_vector_index_definition",
                    "backend": catalog.get("backend"),
                    "vector_index_count": observed["vector_index_count"],
                    "vector_index_dimension_exact": observed["vector_index_dimension_exact"],
                    "cosine_metric": observed["cosine_metric"],
                    "hnsw": observed["hnsw"],
                    "gsdiskann": observed["gsdiskann"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "one q_vec_idx HNSW/cosine on the real dimension",
                "experiment": "one q_<dim>_vec_diskann gsdiskann/cosine index",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-VECTOR-INDEX-001",
                    "summary": f"{group} physical Memory vector index differed from its native dimension/metric contract",
                    "code_location": "memory/utils/gaussdb_conn.py:GaussDBMemoryDDLBuilder.build_diskann_index_ddl",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms905() -> dict[str, Any]:
    case_id = "TC-MS-905"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "repeat_create_idx_and_compare_catalog",
            action="repeat_create",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        before = probe["before"]
        after = probe["after"]
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "create_error_count": probe.get("create_error_count"),
            "catalog_signature_unchanged": before.get("catalog_signature") == after.get("catalog_signature"),
            "duplicate_index_count": after.get("duplicate_index_count"),
            "row_preserved": before.get("physical_raw_count") == 1 and after.get("physical_raw_count") == 1,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and dimension > 0 and catalog_ddl_idempotency_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_message_and_capture_catalog_before_repeat",
                    "response": observed["write"],
                    "dimension": dimension,
                    "index_count": len(before.get("index_names", [])),
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "repeat_create_idx_through_backend_adapter",
                    "backend": before.get("backend"),
                    "create_error_count": observed["create_error_count"],
                    "catalog_signature_unchanged": observed["catalog_signature_unchanged"],
                    "duplicate_index_count": observed["duplicate_index_count"],
                    "row_preserved": observed["row_preserved"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control": "ConflictType.Ignore with unchanged native catalog",
                "experiment": "IF NOT EXISTS/advisory lock with unchanged catalog",
                "physical_rows": 1,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-DDL-IDEMPOTENCY-001",
                    "summary": f"{group} repeated create_idx changed catalog structure, duplicated indexes, or lost the row",
                    "code_location": "memory/utils/gaussdb_conn.py:create_idx",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms906() -> dict[str, Any]:
    case_id = "TC-MS-906"
    guard_prefix = "fresh-ms-906-guard"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        guard = _write_raw_fixture(
            case_id,
            group,
            owner,
            guard_prefix,
            [
                {
                    "agent_id": f"{guard_prefix}-{group}-agent",
                    "session_id": f"{guard_prefix}-{group}-session",
                    "user_input": "delete index guard input",
                    "agent_response": "delete index guard response",
                }
            ],
        )
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        dedicated_only_before = MS._tenant_memory_count(group, fixture["secondary"]["tenant_id"]) == 1
        probe = _catalog_probe(
            case_id,
            group,
            "delete_only_dedicated_physical_table",
            action="delete_idx",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        guard_after = _store_snapshot(
            case_id,
            group,
            "read_only_guard_after_dedicated_delete",
            owner["tenant_id"],
            [guard["memory_id"]],
        )
        dedicated_cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        refreshed_owner = _owner(case_id, group)
        guard_cleanup = _cleanup_memories(case_id, group, refreshed_owner["auth"], [guard["memory_id"]])
        business_cleanup = dedicated_cleanup["succeeded"] and guard_cleanup
        observed = {
            "dedicated_write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "guard_write": [guard["responses"][0].get("http_status"), guard["responses"][0].get("code")],
            "dedicated_only_before": dedicated_only_before,
            "delete_succeeded": probe.get("delete_succeeded") is True,
            "dedicated_table_absent": probe["after"].get("physical_table_count") == 0,
            "guard_table_present": guard_after.get("index_exists") is True,
            "guard_row_preserved": guard_after.get("raw_count") == 1,
            "business_cleanup_succeeded": business_cleanup,
        }
        passed = guard["preclean"] and guard["writes_succeeded"] and fixture["ready"] and dimension > 0 and delete_index_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dedicated_tenant_table_and_independent_guard_table",
                    "dedicated_write": observed["dedicated_write"],
                    "guard_write": observed["guard_write"],
                    "dedicated_only_before": observed["dedicated_only_before"],
                    "raw_sha256": [
                        fixture["snapshot"].get("raw_sha256"),
                        guard["snapshot"].get("raw_sha256"),
                    ],
                },
                {
                    "name": "delete_dedicated_index_and_verify_physical_table_absent",
                    "backend": probe["before"].get("backend"),
                    "delete_succeeded": observed["delete_succeeded"],
                    "dedicated_table_absent": observed["dedicated_table_absent"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "prove_other_tenant_guard_table_and_row_survive",
                    "guard_table_present": observed["guard_table_present"],
                    "guard_row_preserved": observed["guard_row_preserved"],
                    "raw_sha256": guard_after.get("raw_sha256"),
                },
                {
                    "name": "cleanup_metadata_models_users_and_guard_through_apis",
                    "dedicated_cleanup": dedicated_cleanup,
                    "guard_cleanup": guard_cleanup,
                    "business_cleanup_succeeded": business_cleanup,
                },
            ],
            "oracle": {
                "dedicated_physical_table": "absent after delete_idx",
                "independent_guard": "table and one row preserved",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-DELETE-INDEX-001",
                    "summary": f"{group} delete_idx did not remove only the dedicated physical table while preserving the guard",
                    "code_location": "memory/utils/gaussdb_conn.py:delete_idx",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms907() -> dict[str, Any]:
    case_id = "TC-MS-907"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group)
        dimension = int(fixture["rows"][0].get("vector_dimension") or 0) if fixture["rows"] else 0
        probe = _catalog_probe(
            case_id,
            group,
            "drop_index_check_index_exist_and_restore",
            action="drop_restore_index",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        before = probe["before"]
        after_drop = probe.get("after_drop") or {}
        restored = probe.get("restored") or {}
        dropped_name = probe.get("dropped_index_name")
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "write": [fixture["responses"][0].get("http_status"), fixture["responses"][0].get("code")],
            "before": before.get("index_exist"),
            "after_drop": after_drop.get("index_exist"),
            "dropped_index_absent": dropped_name not in set(after_drop.get("index_names", [])),
            "restored": restored.get("index_exist"),
            "restored_index_present": dropped_name in set(restored.get("index_names", [])),
            "row_preserved": before.get("physical_raw_count") == 1 and after_drop.get("physical_raw_count") == 1 and restored.get("physical_raw_count") == 1,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and dimension > 0 and index_exist_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "write_message_and_confirm_complete_index",
                    "response": observed["write"],
                    "index_exist_before": observed["before"],
                    "raw_sha256": fixture["snapshot"].get("raw_sha256"),
                },
                {
                    "name": "drop_one_dedicated_index_and_recheck_index_exist",
                    "backend": before.get("backend"),
                    "dropped_index_fingerprint": DB._fingerprint(str(dropped_name)),
                    "dropped_index_absent": observed["dropped_index_absent"],
                    "index_exist_after_drop": observed["after_drop"],
                    "raw_sha256": probe.get("raw_sha256"),
                },
                {
                    "name": "recreate_indexes_and_verify_row_and_catalog_restored",
                    "index_exist_restored": observed["restored"],
                    "restored_index_present": observed["restored_index_present"],
                    "row_preserved": observed["row_preserved"],
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "control_after_drop": True,
                "experiment_after_drop": False,
                "after_restore": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-INDEX-EXIST-001",
                    "summary": f"{group} index_exist drop/restore behavior differed from its documented backend semantics",
                    "code_location": "memory/utils/gaussdb_conn.py:index_exist",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ms908() -> dict[str, Any]:
    case_id = "TC-MS-908"

    def execute(group: str, _owner_data: dict[str, str]) -> dict[str, Any]:
        fixture = _prepare_vector_tenant_fixture(case_id, group, message_count=0)
        concurrent = _concurrent_first_writes(case_id, group, fixture)
        snapshot = _store_snapshot(
            case_id,
            group,
            "read_only_after_concurrent_first_writes",
            fixture["secondary"]["tenant_id"],
            [fixture["memory_id"]],
        )
        message_ids = _message_id_set(snapshot.get("rows", []))
        dimension = int(snapshot["rows"][0].get("vector_dimension") or 0) if snapshot.get("rows") else 0
        probe = _catalog_probe(
            case_id,
            group,
            "inspect_catalog_after_concurrent_first_writes",
            action="inspect",
            tenant_id=fixture["secondary"]["tenant_id"],
            memory_ids=[fixture["memory_id"]],
            dimension=dimension,
        )
        catalog = probe["before"]
        cleanup = _cleanup_catalog_fixture(case_id, group, fixture)
        observed = {
            "responses": concurrent.get("responses"),
            "barrier_released": concurrent.get("barrier_released"),
            "start_spread_bounded": float(concurrent.get("start_spread_ms") or 0) <= 1000,
            "physical_table_count": catalog.get("physical_table_count"),
            "physical_raw_count": catalog.get("physical_raw_count"),
            "message_ids_unique": len(message_ids) == 2,
            "required_indexes_complete": catalog.get("required_indexes_complete"),
            "duplicate_index_count": catalog.get("duplicate_index_count"),
            "advisory_lock_sql_exact": catalog.get("advisory_lock_sql_exact"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = fixture["ready"] and fixture["before_snapshot"].get("index_exists") is False and dimension > 0 and concurrent_ddl_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "prove_no_physical_table_before_concurrent_first_writes",
                    "index_exists_before": fixture["before_snapshot"].get("index_exists"),
                    "raw_count_before": fixture["before_snapshot"].get("raw_count"),
                    "raw_sha256": fixture["before_snapshot"].get("raw_sha256"),
                },
                {
                    "name": "release_two_api_writes_through_thread_barrier",
                    "responses": observed["responses"],
                    "barrier_released": observed["barrier_released"],
                    "start_spread_ms": concurrent.get("start_spread_ms"),
                    "start_spread_bounded": observed["start_spread_bounded"],
                    "raw_sha256": [
                        concurrent.get("timeline_raw_sha256"),
                        concurrent.get("api_log_raw_sha256"),
                    ],
                },
                {
                    "name": "read_only_verify_single_catalog_and_two_unique_rows",
                    "backend": catalog.get("backend"),
                    "dimension": dimension,
                    "physical_table_count": observed["physical_table_count"],
                    "physical_raw_count": observed["physical_raw_count"],
                    "message_ids_unique": observed["message_ids_unique"],
                    "required_indexes_complete": observed["required_indexes_complete"],
                    "duplicate_index_count": observed["duplicate_index_count"],
                    "advisory_lock_sql_exact": observed["advisory_lock_sql_exact"],
                    "raw_sha256": [
                        snapshot.get("raw_sha256"),
                        probe.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_memory_model_and_dedicated_user_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "responses": [[200, 0], [200, 0]],
                "physical_tables": 1,
                "physical_rows": 2,
                "indexes": "one complete native set without duplicates",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "MS-PHYSICAL-CONCURRENT-DDL-001",
                    "summary": f"{group} concurrent first writes did not converge to one complete catalog with two rows",
                    "code_location": "memory/utils/gaussdb_conn.py:create_idx",
                }
            ],
        }

    return _run_case(case_id, execute)


RUNNERS: dict[str, Callable[[], dict[str, Any]]] = {
    "TC-MS-001": run_ms001,
    "TC-MS-002": run_ms002,
    "TC-MS-003": run_ms003,
    "TC-MS-004": run_ms004,
    "TC-MS-005": run_ms005,
    "TC-MS-006": run_ms006,
    "TC-MS-007": run_ms007,
    "TC-MS-008": run_ms008,
    "TC-MS-009": run_ms009,
    "TC-MS-010": run_ms010,
    "TC-MS-011": run_ms011,
    "TC-MS-012": run_ms012,
    "TC-MS-100": run_ms100,
    "TC-MS-101": run_ms101,
    "TC-MS-102": run_ms102,
    "TC-MS-103": run_ms103,
    "TC-MS-104": run_ms104,
    "TC-MS-105": run_ms105,
    "TC-MS-106": run_ms106,
    "TC-MS-107": run_ms107,
    "TC-MS-108": run_ms108,
    "TC-MS-109": run_ms109,
    "TC-MS-200": run_ms200,
    "TC-MS-201": run_ms201,
    "TC-MS-202": run_ms202,
    "TC-MS-203": run_ms203,
    "TC-MS-204": run_ms204,
    "TC-MS-205": run_ms205,
    "TC-MS-206": run_ms206,
    "TC-MS-207": run_ms207,
    "TC-MS-208": run_ms208,
    "TC-MS-209": run_ms209,
    "TC-MS-210": run_ms210,
    "TC-MS-211": run_ms211,
    "TC-MS-212": run_ms212,
    "TC-MS-213": run_ms213,
    "TC-MS-300": run_ms300,
    "TC-MS-301": run_ms301,
    "TC-MS-302": run_ms302,
    "TC-MS-303": run_ms303,
    "TC-MS-304": run_ms304,
    "TC-MS-305": run_ms305,
    "TC-MS-306": run_ms306,
    "TC-MS-400": run_ms400,
    "TC-MS-401": run_ms401,
    "TC-MS-402": run_ms402,
    "TC-MS-403": run_ms403,
    "TC-MS-404": run_ms404,
    "TC-MS-405": run_ms405,
    "TC-MS-406": run_ms406,
    "TC-MS-500": run_ms500,
    "TC-MS-501": run_ms501,
    "TC-MS-502": run_ms502,
    "TC-MS-503": run_ms503,
    "TC-MS-600": run_ms600,
    "TC-MS-601": run_ms601,
    "TC-MS-602": run_ms602,
    "TC-MS-603": run_ms603,
    "TC-MS-700": run_ms700,
    "TC-MS-701": run_ms701,
    "TC-MS-702": run_ms702,
    "TC-MS-703": run_ms703,
    "TC-MS-704": run_ms704,
    "TC-MS-705": run_ms705,
    "TC-MS-800": run_ms800,
    "TC-MS-801": run_ms801,
    "TC-MS-802": run_ms802,
    "TC-MS-803": run_ms803,
    "TC-MS-804": run_ms804,
    "TC-MS-805": run_ms805,
    "TC-MS-900": run_ms900,
    "TC-MS-901": run_ms901,
    "TC-MS-902": run_ms902,
    "TC-MS-903": run_ms903,
    "TC-MS-904": run_ms904,
    "TC-MS-905": run_ms905,
    "TC-MS-906": run_ms906,
    "TC-MS-907": run_ms907,
    "TC-MS-908": run_ms908,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh Memory Store cases")
    parser.add_argument("--case", choices=sorted(CASE_TITLES), required=True)
    args = parser.parse_args()
    if args.case not in RUNNERS:
        raise SystemExit(f"runner not implemented yet: {args.case}")
    result = RUNNERS[args.case]()
    print(
        json.dumps(
            {
                "case_id": result["case_id"],
                "pair_status": result["pair_status"],
                "group_statuses": {item["group"]: item["status"] for item in result["groups"]},
            },
            sort_keys=True,
        )
    )
    return pair_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
