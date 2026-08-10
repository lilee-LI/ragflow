#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import ipaddress
import io
import json
import re
import socket
import subprocess
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterator

import jwt

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID as BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)
from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_runner_result import pair_exit_code

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = evidence_dir("05_chat_session_agent")
RAW_DIR = EVIDENCE_DIR / "raw"
PLAN_FILES = (
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "05_chat_session_agent.md",
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "05_chat_agent_supplement.md",
)


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DD = _load_module("fresh_04_dataset_document.py", "fresh_05_dataset_base")
AUTH = DD.AUTH
DB = DD.DB
DD.EVIDENCE_DIR = EVIDENCE_DIR
DD.RAW_DIR = RAW_DIR
AUTH.EVIDENCE_DIR = EVIDENCE_DIR
AUTH.RAW_DIR = RAW_DIR
DB.EVIDENCE_DIR = EVIDENCE_DIR
DB.RAW_DIR = RAW_DIR


def _evidence_module():
    return _load_module("fresh_case_evidence.py", "fresh_05_evidence")


def _case_titles() -> dict[str, str]:
    pattern = re.compile(
        r"^### (TC-(?:CS|CHAT-(?:DEL|PATCH)|AGENT-(?:WH|COMP))-\d{3}):\s*(.+)$",
        re.MULTILINE,
    )
    result: dict[str, str] = {}
    for path in PLAN_FILES:
        for case_id, title in pattern.findall(path.read_text(encoding="utf-8")):
            if case_id in result:
                raise ValueError(f"duplicate case id: {case_id}")
            result[case_id] = title.strip()
    if len(result) != 122:
        raise ValueError(f"expected 122 chat/session/agent cases, found {len(result)}")
    return result


CASE_TITLES = _case_titles()


def minimal_chat_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_present") is True
        and observed.get("name_matches") is True
        and observed.get("dataset_ids") == []
        and observed.get("llm_inherited") is True
        and observed.get("api_rerank_id") == ""
        and observed.get("top_n") == 6
        and observed.get("top_k") == 1024
        and observed.get("similarity_threshold") == 0.1
        and observed.get("vector_similarity_weight") == 0.3
        and observed.get("database_count") == 1
        and observed.get("tenant_matches") is True
        and observed.get("status") == "1"
        and observed.get("kb_ids") == []
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("physical_rerank_is_empty") is True and observed.get("physical_rerank_is_null") is False
    if group == "experiment":
        return common and observed.get("physical_rerank_is_empty") is False and observed.get("physical_rerank_is_null") is True
    raise ValueError("unknown group")


def full_chat_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("database_count") == 1
        and observed.get("dataset_ids_match") is True
        and observed.get("kb_names_match") is True
        and observed.get("llm_matches") is True
        and observed.get("rerank_matches") is True
        and observed.get("llm_setting_matches") is True
        and observed.get("prompt_config_matches") is True
        and observed.get("scalar_fields_match") is True
        and observed.get("database_json_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def chat_rejection_contract_ok(observed: dict[str, Any], *, expected_code: int, message_fragment: str) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == expected_code
        and message_fragment in str(observed.get("message") or "")
        and observed.get("database_delta") == 0
        and observed.get("cleanup_succeeded") is True
    )


def group_result_shape_ok(result: dict[str, Any]) -> bool:
    return (
        result.get("status") in {"PASS", "FAIL", "BLOCKED"}
        and isinstance(result.get("steps"), list)
        and bool(result.get("steps"))
        and all(isinstance(step, dict) and step.get("name") for step in result["steps"])
        and isinstance(result.get("oracle"), dict)
    )


def chat_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("actual_count") == observed.get("expected_count")
        and observed.get("total_matches") is True
        and observed.get("ids_exact") is True
        and observed.get("required_fields_present") is True
        and observed.get("descending_order") is True
        and observed.get("pages_disjoint") is True
        and observed.get("filter_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def chat_detail_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_matches") is True
        and observed.get("dataset_mapping_matches") is True
        and observed.get("json_fields_match") is True
        and observed.get("database_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def chat_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("response_matches") is True
        and observed.get("database_matches") is True
        and observed.get("json_merge_matches") is True
        and observed.get("unsubmitted_fields_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )


def empty_chat_field_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("api_value") == "" and observed.get("orm_value") == "" and observed.get("cleanup_succeeded") is True
    if group == "control":
        return common and observed.get("physical_is_empty") is True and observed.get("physical_is_null") is False
    if group == "experiment":
        return common and observed.get("physical_is_empty") is False and observed.get("physical_is_null") is True
    raise ValueError("unknown group")


def chat_soft_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("dialog_count_after") == 1
        and observed.get("dialog_status_after") == "0"
        and observed.get("list_absent") is True
        and observed.get("detail_denied") is True
        and observed.get("session_count_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def session_create_contract_ok(observed: dict[str, Any], *, expected_name: str) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_present") is True
        and observed.get("chat_id_matches") is True
        and observed.get("name") == expected_name
        and observed.get("database_count") == 1
        and observed.get("database_matches") is True
        and observed.get("prologue_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def session_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("actual_count") == observed.get("expected_count")
        and observed.get("ids_exact") is True
        and observed.get("chat_id_mapping_matches") is True
        and observed.get("descending_order") is True
        and observed.get("database_count") == 3
        and observed.get("cleanup_succeeded") is True
    )


def session_detail_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_matches") is True
        and observed.get("message_fields_present") is True
        and observed.get("messages_match_database") is True
        and observed.get("reference_matches_database") is True
        and observed.get("chunks_format_present") is True
        and observed.get("avatar_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def session_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("name_http_status") == 200
        and observed.get("name_code") == 0
        and observed.get("name_response_matches") is True
        and observed.get("name_database_matches") is True
        and observed.get("messages_http_status") == 200
        and observed.get("messages_code") == 102
        and observed.get("reference_http_status") == 200
        and observed.get("reference_code") == 102
        and observed.get("messages_unchanged") is True
        and observed.get("reference_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def session_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("deleted_count") == observed.get("expected_deleted_count")
        and observed.get("remaining_count") == observed.get("expected_remaining_count")
        and observed.get("targets_physically_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def message_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("target_pair_absent") is True
        and observed.get("message_count_delta") == 2
        and observed.get("reference_count_delta") == 1
        and observed.get("untargeted_pair_present") is True
        and observed.get("cleanup_succeeded") is True
    )


def feedback_contract_ok(observed: dict[str, Any], *, expected_code: int) -> bool:
    common = observed.get("http_status") == 200 and observed.get("code") == expected_code and observed.get("cleanup_succeeded") is True
    if expected_code == 0:
        return common and observed.get("assistant_found") is True and observed.get("thumbup_matches") is True and observed.get("feedback_matches") is True
    if expected_code == 102:
        return common and "thumbup must be a boolean" in str(observed.get("message") or "") and observed.get("messages_unchanged") is True
    raise ValueError("unsupported feedback contract code")


def stream_completion_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("content_type_is_sse") is True
        and int(observed.get("event_count") or 0) >= 2
        and observed.get("all_events_parsed") is True
        and observed.get("final_event_matches") is True
        and observed.get("answer_event_present") is True
        and observed.get("session_id_matches") is True
        and observed.get("database_session_count") == 1
        and observed.get("history_preserved") is True
        and observed.get("message_pair_appended") is True
        and observed.get("cleanup_succeeded") is True
    )


def nonstream_completion_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("answer_nonempty") is True
        and observed.get("reference_present") is True
        and observed.get("session_id_present") is True
        and observed.get("user_content_matches") is True
        and observed.get("message_pair_present") is True
        and observed.get("cleanup_succeeded") is True
    )


def completion_validation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_statuses") == [200, 200, 200]
        and observed.get("codes") == [101, 101, 101]
        and observed.get("list_messages_match") is True
        and observed.get("assistant_only_message_mentions_user") is True
        and observed.get("database_delta") == 0
        and observed.get("cleanup_succeeded") is True
    )


def pass_all_history_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("answer_nonempty") is True
        and observed.get("session_id_matches") is True
        and observed.get("client_baseline_exact") is True
        and observed.get("assistant_appended") is True
        and observed.get("final_message_count") == 4
        and observed.get("cleanup_succeeded") is True
    )


def audio_speech_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("content_type_is_mpeg") is True
        and observed.get("body_matches_stub") is True
        and observed.get("stub_call_observed") is True
        and observed.get("default_model_set") is True
        and observed.get("default_model_restored") is True
        and observed.get("provider_instance_removed") is True
        and observed.get("chat_cleanup_succeeded") is True
    )


def audio_transcription_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("text_matches") is True
        and observed.get("multipart_call_observed") is True
        and observed.get("valid_wav_fixture") is True
        and observed.get("default_model_set") is True
        and observed.get("default_model_restored") is True
        and observed.get("provider_instance_removed") is True
    )


def mindmap_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("root_shape_matches") is True
        and int(observed.get("node_count") or 0) >= 2
        and observed.get("hierarchy_present") is True
        and observed.get("cleanup_succeeded") is True
    )


def recommendation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("list_nonempty") is True
        and observed.get("all_items_nonempty_strings") is True
        and observed.get("database_delta") == 0
    )


def agent_create_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    if group not in GROUP_ORDER:
        raise ValueError("unknown group")
    common = (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_present") is True
        and observed.get("title_matches") is True
        and observed.get("canvas_category") == "agent_canvas"
        and observed.get("database_count") == 1
        and observed.get("owner_matches") is True
        and observed.get("database_dsl_matches") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return common and observed.get("physical_tags_is_empty") is True and observed.get("physical_tags_is_null") is False
    return common and observed.get("physical_tags_is_empty") is False and observed.get("physical_tags_is_null") is True


def agent_duplicate_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 102
        and "already exists" in str(observed.get("message") or "")
        and observed.get("database_delta") == 0
        and observed.get("fixture_ready") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("canvas_is_list") is True
        and observed.get("fixture_ids_present") is True
        and observed.get("total_matches_database") is True
        and observed.get("required_fields_present") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_tag_filter_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("matched_id_present") is True
        and observed.get("unmatched_id_absent") is True
        and observed.get("all_returned_tags_match") is True
        and observed.get("database_tags_match") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_detail_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("id_matches") is True
        and observed.get("dsl_matches_request") is True
        and observed.get("dsl_matches_database") is True
        and observed.get("last_publish_time_present") is True
        and observed.get("versions_absent") is True
        and observed.get("datasets_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("title_matches") is True
        and observed.get("database_dsl_matches") is True
        and observed.get("version_count_delta") == 1
        and observed.get("latest_version_dsl_matches") is True
        and observed.get("replica_exists") is True
        and observed.get("replica_dsl_matches") is True
        and observed.get("replica_title_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("canvas_count_after") == 0
        and observed.get("version_count_after") == 0
        and observed.get("session_count_after") == 0
        and observed.get("replica_exists_after") is False
    )


def nonowner_agent_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 103
        and observed.get("message") == "Only the owner of the agent is authorized for this operation."
        and observed.get("fixture_ready") is True
        and observed.get("canvas_unchanged") is True
        and observed.get("secondary_ready") is True
        and observed.get("agent_cleanup_succeeded") is True
        and observed.get("secondary_cleanup_succeeded") is True
    )


def agent_reset_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("path_cleared") is True
        and observed.get("history_cleared") is True
        and observed.get("retrieval_cleared") is True
        and observed.get("memory_cleared") is True
        and observed.get("system_globals_cleared") is True
        and observed.get("custom_globals_preserved") is True
        and observed.get("component_topology_preserved") is True
        and observed.get("component_runtime_cleared") is True
        and observed.get("database_dsl_matches") is True
        and observed.get("replica_dsl_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_template_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("list_nonempty") is True
        and observed.get("required_fields_present") is True
        and observed.get("multilingual_json_types") is True
        and observed.get("database_count_matches") is True
        and observed.get("database_rows_match") is True
    )


def agent_prompt_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") == 0 and observed.get("keys_exact") is True and observed.get("all_nonempty_strings") is True


def agent_version_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("version_count") == 3
        and observed.get("required_fields_present") is True
        and observed.get("descending_order") is True
        and observed.get("database_ids_match") is True
        and observed.get("database_scalar_fields_match") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_version_detail_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("id_matches") is True
        and observed.get("agent_id_matches") is True
        and observed.get("dsl_present") is True
        and observed.get("dsl_matches_database") is True
        and observed.get("scalar_fields_match") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_session_create_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("id_present") is True
        and observed.get("name_matches") is True
        and observed.get("dsl_present") is True
        and observed.get("database_count") == 1
        and observed.get("database_mapping_matches") is True
        and observed.get("json_fields_match") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_session_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("session_count") == 3
        and observed.get("total_matches") is True
        and observed.get("ids_exact") is True
        and observed.get("required_fields_present") is True
        and observed.get("descending_order") is True
        and observed.get("database_mapping_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_session_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("target_count_after") == 0
        and observed.get("cleanup_succeeded") is True
    )


def agent_session_bulk_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("target_ids_absent") is True
        and observed.get("untargeted_id_present") is True
        and observed.get("remaining_count") == 1
        and observed.get("cleanup_succeeded") is True
    )


def agent_log_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("fixture_ready") is True
        and observed.get("execution_succeeded") is True
        and observed.get("message_id_present") is True
        and observed.get("redis_log_present") is True
        and observed.get("log_nonempty") is True
        and observed.get("endpoint_matches_redis") is True
        and observed.get("cleanup_succeeded") is True
    )


def webhook_immediate_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 202
        and observed.get("response_body_matches") is True
        and observed.get("fixture_ready") is True
        and observed.get("trace_webhook_id_present") is True
        and observed.get("trace_events_present") is True
        and observed.get("trace_finished") is True
        and observed.get("trace_payload_matches") is True
        and observed.get("session_database_delta") == 0
        and observed.get("cleanup_succeeded") is True
    )


def webhook_token_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("valid_http_status") == 200
        and observed.get("valid_body_matches") is True
        and observed.get("invalid_http_status") == 400
        and observed.get("invalid_code") == 400
        and observed.get("invalid_message_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def webhook_unconfigured_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 400
        and observed.get("code") == 400
        and observed.get("message") == "Webhook not configured for this agent."
        and observed.get("cleanup_succeeded") is True
    )


def webhook_owner_test_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("owner_http_status") == 200
        and observed.get("owner_body_matches") is True
        and observed.get("nonowner_http_status") == 200
        and observed.get("nonowner_code") == 103
        and observed.get("nonowner_message") == "Only the owner of the agent is authorized for this operation."
        and observed.get("agent_cleanup_succeeded") is True
        and observed.get("secondary_cleanup_succeeded") is True
    )


def webhook_trace_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("initial_http_status") == 200
        and observed.get("initial_code") == 0
        and observed.get("next_since_ts_present") is True
        and observed.get("trigger_http_status") == 200
        and observed.get("webhook_id_present") is True
        and observed.get("events_present") is True
        and observed.get("finished") is True
        and observed.get("payload_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def webhook_body_size_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 400
        and observed.get("code") == 400
        and observed.get("message_matches") is True
        and observed.get("request_over_limit") is True
        and observed.get("trace_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def chatbot_completion_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("first_http_status") == 200
        and observed.get("first_content_type_is_sse") is True
        and observed.get("first_parse_error_count") == 0
        and observed.get("prologue_matches") is True
        and observed.get("session_id_present") is True
        and observed.get("first_final_event_matches") is True
        and observed.get("first_database_mapping_matches") is True
        and observed.get("second_http_status") == 200
        and observed.get("second_content_type_is_sse") is True
        and observed.get("second_parse_error_count") == 0
        and observed.get("second_answer_nonempty") is True
        and observed.get("second_final_event_matches") is True
        and observed.get("second_database_history_matches") is True
        and observed.get("token_cleanup_succeeded") is True
        and observed.get("chat_cleanup_succeeded") is True
    )


def chatbot_info_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("required_fields_exact") is True
        and observed.get("title_matches") is True
        and observed.get("avatar_matches") is True
        and observed.get("prologue_matches") is True
        and observed.get("llm_id_matches") is True
        and observed.get("has_tavily_key_matches") is True
        and observed.get("token_cleanup_succeeded") is True
        and observed.get("chat_cleanup_succeeded") is True
    )


def agentbot_completion_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("content_type_is_sse") is True
        and observed.get("parse_error_count") == 0
        and observed.get("event_count_positive") is True
        and observed.get("message_event_nonempty") is True
        and observed.get("error_event_absent") is True
        and observed.get("session_id_present") is True
        and observed.get("database_message_pair_matches") is True
        and observed.get("session_cleanup_succeeded") is True
        and observed.get("agent_cleanup_succeeded") is True
        and observed.get("token_cleanup_succeeded") is True
    )


def agentbot_inputs_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("required_fields_present") is True
        and observed.get("input_form_matches") is True
        and observed.get("prologue_matches") is True
        and observed.get("mode_matches") is True
        and observed.get("title_matches") is True
        and observed.get("avatar_matches") is True
        and observed.get("agent_cleanup_succeeded") is True
        and observed.get("token_cleanup_succeeded") is True
    )


def searchbot_ask_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("content_type_is_sse") is True
        and observed.get("parse_error_count") == 0
        and observed.get("answer_nonempty") is True
        and observed.get("error_event_absent") is True
        and observed.get("expected_chunk_referenced") is True
        and observed.get("final_event_matches") is True
        and observed.get("search_cleanup_succeeded") is True
        and observed.get("dataset_cleanup_succeeded") is True
        and observed.get("token_cleanup_succeeded") is True
    )


def searchbot_retrieval_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("chunks_nonempty") is True
        and observed.get("expected_chunk_present") is True
        and observed.get("scores_present") is True
        and observed.get("dataset_cleanup_succeeded") is True
        and observed.get("token_cleanup_succeeded") is True
    )


def searchbot_mindmap_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("mindmap_nonempty") is True
        and observed.get("mindmap_structure_valid") is True
        and observed.get("search_cleanup_succeeded") is True
        and observed.get("dataset_cleanup_succeeded") is True
        and observed.get("token_cleanup_succeeded") is True
    )


def chat_model_roundtrip_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = (
        observed.get("fixture_ready") is True
        and observed.get("initial_api_empty") is True
        and observed.get("valid_update_response") == [200, 0]
        and observed.get("valid_api_matches") is True
        and observed.get("valid_database_matches") is True
        and observed.get("final_update_response") == [200, 0]
        and observed.get("final_api_empty") is True
        and observed.get("cleanup_succeeded") is True
    )
    if group == "control":
        return (
            common
            and observed.get("initial_physical_empty") is True
            and observed.get("initial_physical_null") is False
            and observed.get("final_physical_empty") is True
            and observed.get("final_physical_null") is False
        )
    if group == "experiment":
        return (
            common
            and observed.get("initial_physical_empty") is False
            and observed.get("initial_physical_null") is True
            and observed.get("final_physical_empty") is False
            and observed.get("final_physical_null") is True
        )
    raise ValueError("unknown group")


def nested_json_empty_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("api_values_are_empty_strings") is True
        and observed.get("database_values_are_empty_strings") is True
        and observed.get("other_prompt_fields_preserved") is True
        and observed.get("cleanup_succeeded") is True
    )


def unauthenticated_chat_agent_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("chat_authorization_header_absent") is True
        and observed.get("chat_http_status") == 401
        and observed.get("agent_authorization_header_absent") is True
        and observed.get("agent_http_status") == 401
        and observed.get("database_delta") == 0
    )


def special_chat_names_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("response_statuses") == [[200, 0], [200, 0]]
        and observed.get("api_names_exact") is True
        and observed.get("database_names_exact") is True
        and observed.get("utf8_byte_lengths_valid") is True
        and observed.get("cleanup_succeeded") is True
    )


def large_history_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("baseline_completion_count") == 50
        and observed.get("baseline_all_success") is True
        and observed.get("baseline_message_count") == 101
        and observed.get("final_http_status") == 200
        and observed.get("final_code") == 0
        and observed.get("final_message_delta") == 2
        and observed.get("final_message_count") == 103
        and observed.get("all_messages_valid") is True
        and observed.get("no_truncation") is True
        and observed.get("cleanup_succeeded") is True
    )


def history_messages_not_truncated(messages: list[Any], prologue: str, questions: list[str]) -> bool:
    if len(messages) != 1 + 2 * len(questions):
        return False
    first = messages[0]
    if not isinstance(first, dict) or first.get("role") != "assistant" or first.get("content") != prologue:
        return False
    for index, question in enumerate(questions):
        user = messages[1 + 2 * index]
        assistant = messages[2 + 2 * index]
        if not isinstance(user, dict) or not isinstance(assistant, dict):
            return False
        message_id = user.get("id")
        if (
            user.get("role") != "user"
            or user.get("content") != question
            or not message_id
            or assistant.get("role") != "assistant"
            or assistant.get("id") != message_id
            or not isinstance(assistant.get("content"), str)
            or not assistant.get("content")
        ):
            return False
    return True


def concurrent_session_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("response_statuses") == [[200, 0]] * 5
        and observed.get("unique_id_count") == 5
        and observed.get("database_count") == 5
        and observed.get("database_ids_exact") is True
        and observed.get("database_names_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def float_sanitization_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("isolated_exit_code") == 0
        and observed.get("isolated_nested_values_sanitized") is True
        and observed.get("isolated_other_values_unchanged") is True
        and observed.get("nonstream_http_status") == 200
        and observed.get("nonstream_code") == 0
        and observed.get("nonstream_strict_parse_ok") is True
        and observed.get("stream_http_status") == 200
        and observed.get("stream_strict_parse_error_count") == 0
        and observed.get("stream_final_event_matches") is True
        and observed.get("stream_error_event_absent") is True
        and observed.get("nonstandard_literal_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def large_agent_dsl_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("component_count") == 100
        and observed.get("request_json_bytes_over_100k") is True
        and observed.get("detail_http_status") == 200
        and observed.get("detail_code") == 0
        and observed.get("api_dsl_exact") is True
        and observed.get("database_dsl_exact") is True
        and observed.get("serialized_lengths_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_completion_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("stream_http_status") == 200
        and observed.get("stream_content_type_ok") is True
        and observed.get("stream_done_seen") is True
        and observed.get("stream_parse_error_count") == 0
        and observed.get("stream_error_absent") is True
        and observed.get("stream_answer_nonempty") is True
        and observed.get("nonstream_http_status") == 200
        and observed.get("nonstream_code") == 0
        and observed.get("nonstream_answer_nonempty") is True
        and observed.get("session_count") == 2
        and observed.get("session_ids_match") is True
        and observed.get("sessions_have_messages") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_debug_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("input_form_http_status") == 200
        and observed.get("input_form_code") == 0
        and observed.get("input_key_present") is True
        and observed.get("debug_http_status") == 200
        and observed.get("debug_code") == 0
        and observed.get("output_nonempty") is True
        and observed.get("cleanup_succeeded") is True
    )


def agent_file_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("upload_http_status") == 200
        and observed.get("upload_code") == 0
        and observed.get("file_id_present") is True
        and observed.get("download_http_status") == 200
        and observed.get("download_bytes_exact") is True
        and observed.get("no_file_delete_endpoint_recorded") is True
        and observed.get("agent_cleanup_succeeded") is True
    )


def agent_tag_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("list_http_status") == 200
        and observed.get("list_code") == 0
        and observed.get("initial_count_exact") is True
        and observed.get("update_http_status") == 200
        and observed.get("update_code") == 0
        and observed.get("database_tags_exact") is True
        and observed.get("cleanup_succeeded") is True
    )


def bot_credential_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("beta_status") == 200
        and observed.get("beta_code") == 0
        and observed.get("ordinary_status") == 401
        and observed.get("ordinary_code") == 401
        and observed.get("credential_cleanup_succeeded") is True
        and observed.get("chat_cleanup_succeeded") is True
    )


def cross_tenant_chat_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("get_status") == 200
        and observed.get("get_code") == 109
        and observed.get("get_message_exact") is True
        and observed.get("delete_status") == 200
        and observed.get("delete_code") == 109
        and observed.get("delete_message_exact") is True
        and observed.get("chat_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def cross_tenant_agent_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("get_status") == 200
        and observed.get("get_code") == 103
        and observed.get("agent_unchanged") is True
        and observed.get("cleanup_succeeded") is True
    )


def team_agent_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("membership_role") == "normal"
        and observed.get("get_status") == 200
        and observed.get("get_code") == 0
        and observed.get("update_status") == 200
        and observed.get("update_rejected") is True
        and observed.get("agent_unchanged") is True
        and observed.get("membership_cleanup_succeeded") is True
        and observed.get("agent_cleanup_succeeded") is True
        and observed.get("secondary_cleanup_succeeded") is True
    )


def db_connection_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("loopback_status") == 200
        and observed.get("loopback_failed") is True
        and observed.get("loopback_message_mentions_unsafe") is True
        and observed.get("allowed_target_is_global") is True
        and observed.get("allowed_status") == 200
        and observed.get("allowed_code") == 0
        and observed.get("allowed_data_exact") is True
    )


def dataflow_rerun_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("initial_terminal") is True
        and observed.get("initial_progress_success") is True
        and observed.get("log_id_present") is True
        and observed.get("dsl_present") is True
        and observed.get("component_present") is True
        and observed.get("rerun_status") == 200
        and observed.get("rerun_code") == 0
        and observed.get("rerun_task_observed") is True
        and observed.get("rerun_terminal") is True
        and observed.get("rerun_progress_success") is True
        and observed.get("log_count_increased") is True
        and observed.get("dataset_cleanup_succeeded") is True
        and observed.get("agent_cleanup_succeeded") is True
    )


def supplement_chat_delete_contract_ok(mode: str, observed: dict[str, Any]) -> bool:
    common = observed.get("fixture_ready") is True and observed.get("delete_status") == 200 and observed.get("delete_code") == 0 and observed.get("cleanup_succeeded") is True
    if mode == "single":
        return common and observed.get("dialog_count") == 1 and observed.get("dialog_status") == "0"
    if mode == "list":
        return common and observed.get("list_status") == 200 and observed.get("list_code") == 0 and observed.get("list_absent") is True
    if mode == "detail":
        return common and observed.get("detail_status") == 200 and observed.get("detail_code") == 109 and observed.get("detail_message_exact") is True and observed.get("detail_data_absent") is True
    if mode == "bulk":
        return common and observed.get("success_count") == 2 and observed.get("target_statuses_exact") is True and observed.get("untargeted_active") is True
    if mode == "all":
        return common and int(observed.get("success_count") or 0) > 0 and observed.get("remaining_active") == 0 and observed.get("all_statuses_zero") is True
    raise ValueError("unknown supplement chat delete mode")


def supplement_chat_patch_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("patch_status") == 200
        and observed.get("patch_code") == 0
        and observed.get("api_value_exact") is True
        and observed.get("database_value_exact") is True
        and observed.get("unsubmitted_value_preserved") is True
        and observed.get("name_updated") is True
        and observed.get("cleanup_succeeded") is True
    )


def supplement_webhook_security_contract_ok(
    observed: dict[str, Any],
    *,
    expect_success: bool,
    message_fragment: str = "",
) -> bool:
    common = observed.get("fixture_ready") is True and observed.get("cleanup_succeeded") is True
    if expect_success:
        return common and observed.get("http_status") == 200 and observed.get("body_matches") is True
    return common and observed.get("http_status") == 400 and observed.get("code") == 400 and message_fragment in str(observed.get("message") or "")


def supplement_webhook_rate_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("request_count") == 15
        and observed.get("first_ten_statuses") == [200] * 10
        and observed.get("last_five_statuses") == [400] * 5
        and observed.get("last_five_codes") == [400] * 5
        and observed.get("last_five_messages_match") is True
        and observed.get("rate_key_precleaned") is True
        and observed.get("rate_key_cleanup_succeeded") is True
        and observed.get("agent_cleanup_succeeded") is True
    )


def supplement_agent_new_session_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("session_id_present") is True
        and observed.get("message_id_present") is True
        and observed.get("database_session_count") == 1
        and observed.get("messages_persisted") is True
        and observed.get("output_nonempty") is True
        and observed.get("cleanup_succeeded") is True
    )


def supplement_agent_continuation_contract_ok(observed: dict[str, Any]) -> bool:
    return supplement_agent_new_session_contract_ok(observed) and observed.get("session_id_unchanged") is True and observed.get("session_updated") is True


def supplement_agent_stream_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("content_type_is_sse") is True
        and observed.get("parse_error_count") == 0
        and observed.get("done_count") == 1
        and observed.get("message_event_present") is True
        and observed.get("message_end_present") is True
        and observed.get("answer_nonempty") is True
        and observed.get("error_absent") is True
        and observed.get("session_id_present") is True
        and observed.get("database_session_count") == 1
        and observed.get("messages_persisted") is True
        and observed.get("cleanup_succeeded") is True
    )


def supplement_dataflow_completion_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_ready") is True
        and observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("message_id_present") is True
        and observed.get("session_id_present") is True
        and observed.get("task_count") == 1
        and observed.get("task_id_matches") is True
        and observed.get("task_type") == "dataflow"
        and observed.get("doc_id") == "dataflow_x"
        and observed.get("session_count") == 1
        and observed.get("agent_cleanup_succeeded") is True
        and observed.get("session_cleanup_succeeded") is True
    )


def supplement_task_cancel_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        supplement_dataflow_completion_contract_ok(observed)
        and observed.get("cancel_status") == 200
        and observed.get("cancel_code") == 0
        and observed.get("cancel_data_true") is True
        and observed.get("redis_cancel_flag_present") is True
        and observed.get("task_progress") == -1.0
    )


def _json_value(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _strict_json_loads(value: str) -> Any:
    def reject_constant(constant: str) -> None:
        raise ValueError(f"nonstandard JSON constant: {constant}")

    return json.loads(value, parse_constant=reject_constant)


def _parse_agent_sse_frame(payload_text: str) -> tuple[str, Any]:
    if payload_text == "[DONE]":
        return "done", None
    return "event", _strict_json_loads(payload_text)


def _owner(case_id: str, group: str) -> dict[str, str]:
    return DD._ensure_owner(case_id, group)


def _transport_failure_result(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    params: dict[str, Any] | None,
    timeout: float,
    exception: Exception,
) -> dict[str, Any]:
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_transport_failure.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": method,
                    "path": path,
                    "params": params,
                    "json": payload,
                    "authorization_present": bool(auth),
                    "timeout_seconds": timeout,
                },
                "response": {
                    "http_status": 0,
                    "transport_exception_type": type(exception).__name__,
                },
            }
        ),
    )
    return {
        "http_status": 0,
        "code": None,
        "message": f"transport failure: {type(exception).__name__}",
        "data": None,
        "total_datasets": None,
        "total": None,
        "content_type": None,
        "response_length": 0,
        "elapsed_seconds": timeout,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _request(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    timeout: float = 120,
) -> dict[str, Any]:
    try:
        return DD._request(
            case_id,
            group,
            label,
            auth,
            method,
            path,
            payload=payload,
            params=params,
            files=files,
            timeout=timeout,
        )
    except DD.requests.RequestException as exception:
        failure_payload = payload
        if files is not None:
            failure_payload = {
                "multipart_files": [
                    {
                        "field": field,
                        "name": item[0],
                        "size": len(item[1]),
                        "sha256": hashlib.sha256(item[1]).hexdigest(),
                        "content_type": item[2],
                    }
                    for field, item in files
                ]
            }
        return _transport_failure_result(
            case_id,
            group,
            label,
            auth,
            method,
            path,
            failure_payload,
            params,
            timeout,
            exception,
        )


def _binary_request(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any],
    timeout: float = 120,
) -> dict[str, Any]:
    try:
        response = DD.requests.request(
            method,
            f"{DB._api_base(group)}{path}",
            headers={"Authorization": f"Bearer {auth}"},
            json=payload,
            timeout=timeout,
        )
    except DD.requests.RequestException as exception:
        result = _transport_failure_result(
            case_id,
            group,
            label,
            auth,
            method,
            path,
            payload,
            None,
            timeout,
            exception,
        )
        result.update({"body_sha256": None, "body_prefix_hex": ""})
        return result
    body = response.content
    content_type = response.headers.get("Content-Type")
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_binary.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": method,
                    "path": path,
                    "json": payload,
                    "authorization_present": bool(auth),
                },
                "response": {
                    "http_status": response.status_code,
                    "content_type": content_type,
                    "body_length": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "body_prefix_hex": body[:16].hex(),
                },
            }
        ),
    )
    return {
        "http_status": response.status_code,
        "code": None,
        "message": None,
        "data": None,
        "content_type": content_type,
        "response_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_prefix_hex": body[:16].hex(),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _binary_get(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    path: str,
    params: dict[str, Any],
    *,
    timeout: float = 120,
) -> dict[str, Any]:
    try:
        response = DD.requests.get(
            f"{DB._api_base(group)}{path}",
            headers={"Authorization": f"Bearer {auth}"},
            params=params,
            timeout=timeout,
        )
    except DD.requests.RequestException as exception:
        result = _transport_failure_result(
            case_id,
            group,
            label,
            auth,
            "GET",
            path,
            None,
            params,
            timeout,
            exception,
        )
        result.update(
            {
                "_body": b"",
                "body_sha256": None,
                "body_prefix_hex": "",
            }
        )
        return result
    body = response.content
    content_type = response.headers.get("Content-Type")
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_binary.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": "GET",
                    "path": path,
                    "params": params,
                    "authorization_present": bool(auth),
                },
                "response": {
                    "http_status": response.status_code,
                    "content_type": content_type,
                    "body_length": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "body_prefix_hex": body[:16].hex(),
                },
            }
        ),
    )
    return {
        "http_status": response.status_code,
        "code": None,
        "message": None,
        "data": None,
        "content_type": content_type,
        "response_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_prefix_hex": body[:16].hex(),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "_body": body,
    }


def _chat_snapshot(group: str, chat_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,tenant_id,name,kb_ids,llm_id,llm_setting,rerank_id,"
                "prompt_config,description,top_n,top_k,similarity_threshold,"
                "vector_similarity_weight,status,LENGTH(rerank_id),LENGTH(llm_id) "
                "FROM dialog WHERE id=%s",
                (chat_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    return {
        "count": 1,
        "id": str(row[0]),
        "tenant_id": str(row[1]),
        "name": str(row[2]),
        "kb_ids": _json_value(row[3], []),
        "llm_id": "" if row[4] is None else str(row[4]),
        "physical_llm_is_null": row[4] is None,
        "physical_llm_is_empty": row[4] == "" and row[15] == 0,
        "llm_setting": _json_value(row[5], {}),
        "rerank_id": "" if row[6] is None else str(row[6]),
        "physical_rerank_is_null": row[6] is None,
        "physical_rerank_is_empty": row[6] == "" and row[14] == 0,
        "prompt_config": _json_value(row[7], {}),
        "description": row[8],
        "top_n": int(row[9]),
        "top_k": int(row[10]),
        "similarity_threshold": float(row[11]),
        "vector_similarity_weight": float(row[12]),
        "status": str(row[13]),
    }


def _tenant_default_llm(group: str, tenant_id: str) -> str:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT llm_id FROM tenant WHERE id=%s", (tenant_id,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None or not row[0]:
        raise RuntimeError("tenant default chat model is not configured")
    return str(row[0])


def _tenant_active_chat_count(group: str, tenant_id: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM dialog WHERE tenant_id=%s AND status=%s",
                (tenant_id, "1"),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _tenant_conversation_count(group: str, tenant_id: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM conversation WHERE user_id=%s",
                (tenant_id,),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _active_chat_ids_by_prefix(group: str, tenant_id: str, prefix: str) -> list[str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM dialog WHERE tenant_id=%s AND status=%s AND name LIKE %s ORDER BY id",
                (tenant_id, "1", prefix + "%"),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _active_chat_ids_by_names(group: str, tenant_id: str, names: list[str]) -> list[str]:
    if not names:
        return []
    placeholders = ",".join(["%s"] * len(names))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM dialog WHERE tenant_id=%s AND status=%s AND name IN ({placeholders}) ORDER BY id",
                (tenant_id, "1", *names),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _cleanup_chat_prefix(case_id: str, group: str, auth: str, tenant_id: str, prefix: str) -> bool:
    ids = _active_chat_ids_by_prefix(group, tenant_id, prefix)
    if ids:
        response = _request(
            case_id,
            group,
            "cleanup_existing_chats",
            auth,
            "DELETE",
            "/chats",
            payload={"ids": ids},
        )
        if response["code"] != 0:
            return False
    return not _active_chat_ids_by_prefix(group, tenant_id, prefix)


def _create_chat(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "POST", "/chats", payload=payload)


def _list_chats(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", "/chats", params=params)


def _get_chat(case_id: str, group: str, auth: str, label: str, chat_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", f"/chats/{chat_id}")


def _put_chat(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "PUT", f"/chats/{chat_id}", payload=payload)


def _patch_chat(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PATCH",
        f"/chats/{chat_id}",
        payload=payload,
    )


def _bulk_delete_chats(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "DELETE", "/chats", payload=payload)


def _create_session(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/chats/{chat_id}/sessions",
        payload=payload,
    )


def _list_sessions(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/chats/{chat_id}/sessions",
        params=params,
    )


def _get_session(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    session_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/chats/{chat_id}/sessions/{session_id}",
    )


def _patch_session(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PATCH",
        f"/chats/{chat_id}/sessions/{session_id}",
        payload=payload,
    )


def _delete_sessions(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/chats/{chat_id}/sessions",
        payload=payload,
    )


def _delete_session_message(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    session_id: str,
    message_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/chats/{chat_id}/sessions/{session_id}/messages/{message_id}",
    )


def _feedback_message(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    session_id: str,
    message_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PUT",
        f"/chats/{chat_id}/sessions/{session_id}/messages/{message_id}/feedback",
        payload=payload,
    )


def _complete_nonstream(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    pass_all_history_messages: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "session_id": session_id,
        "messages": messages,
        "stream": False,
    }
    if pass_all_history_messages:
        payload["pass_all_history_messages"] = True
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/chat/completions",
        payload=payload,
        timeout=120,
    )


def _complete_stream(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    chat_id: str,
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
) -> dict[str, Any]:
    path = "/chat/completions"
    timeout = 120.0
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "messages": messages,
        "stream": True,
    }
    if session_id:
        payload["session_id"] = session_id
    try:
        with DD.requests.request(
            "POST",
            f"{DB._api_base(group)}{path}",
            headers={"Authorization": f"Bearer {auth}"},
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            response.encoding = "utf-8"
            data_lines: list[str] = []
            events: list[Any] = []
            parse_error_count = 0
            response_length = 0
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = str(line)
                response_length += len(line.encode("utf-8"))
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                data_lines.append(payload_text)
                try:
                    events.append(_strict_json_loads(payload_text))
                except (json.JSONDecodeError, ValueError):
                    parse_error_count += 1
            http_status = response.status_code
            content_type = response.headers.get("Content-Type")
    except DD.requests.RequestException as exception:
        result = _transport_failure_result(
            case_id,
            group,
            label,
            auth,
            "POST",
            path,
            payload,
            None,
            timeout,
            exception,
        )
        result.update(
            {
                "events": [],
                "data_line_count": 0,
                "parse_error_count": 0,
            }
        )
        return result

    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_sse.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": "POST",
                    "path": path,
                    "json": payload,
                    "authorization_present": bool(auth),
                    "timeout_seconds": timeout,
                },
                "response": {
                    "http_status": http_status,
                    "content_type": content_type,
                    "data_line_count": len(data_lines),
                    "parse_error_count": parse_error_count,
                    "events": events,
                },
            }
        ),
    )
    return {
        "http_status": http_status,
        "code": None,
        "message": None,
        "data": None,
        "content_type": content_type,
        "events": events,
        "data_line_count": len(data_lines),
        "parse_error_count": parse_error_count,
        "response_length": response_length,
        "elapsed_seconds": None,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _sse_post(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 180,
) -> dict[str, Any]:
    try:
        with DD.requests.post(
            f"{DB._api_base(group)}{path}",
            headers={"Authorization": f"Bearer {auth}"},
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            response.encoding = "utf-8"
            events: list[Any] = []
            data_lines: list[str] = []
            parse_error_count = 0
            response_length = 0
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = str(line)
                response_length += len(line.encode("utf-8"))
                if not line.startswith("data:"):
                    continue
                payload_text = line[5:].strip()
                data_lines.append(payload_text)
                try:
                    events.append(_strict_json_loads(payload_text))
                except (json.JSONDecodeError, ValueError):
                    parse_error_count += 1
            http_status = response.status_code
            content_type = response.headers.get("Content-Type")
    except DD.requests.RequestException as exception:
        result = _transport_failure_result(
            case_id,
            group,
            label,
            auth,
            "POST",
            path,
            payload,
            None,
            timeout,
            exception,
        )
        result.update({"events": [], "data_line_count": 0, "parse_error_count": 0})
        return result

    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_sse.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": "POST",
                    "path": path,
                    "json": payload,
                    "authorization_present": bool(auth),
                    "timeout_seconds": timeout,
                },
                "response": {
                    "http_status": http_status,
                    "content_type": content_type,
                    "data_line_count": len(data_lines),
                    "parse_error_count": parse_error_count,
                    "events": events,
                },
            }
        ),
    )
    return {
        "http_status": http_status,
        "code": None,
        "message": None,
        "data": None,
        "content_type": content_type,
        "events": events,
        "data_line_count": len(data_lines),
        "parse_error_count": parse_error_count,
        "response_length": response_length,
        "elapsed_seconds": None,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _agent_sse_post(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    payload: dict[str, Any],
    *,
    timeout: float = 180,
) -> dict[str, Any]:
    path = "/agents/chat/completions"
    try:
        with DD.requests.post(
            f"{DB._api_base(group)}{path}",
            headers={"Authorization": f"Bearer {auth}"},
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            response.encoding = "utf-8"
            events: list[Any] = []
            data_line_count = 0
            done_count = 0
            parse_error_count = 0
            response_length = 0
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = str(line)
                response_length += len(line.encode("utf-8"))
                if not line.startswith("data:"):
                    continue
                data_line_count += 1
                payload_text = line[5:].strip()
                try:
                    kind, event = _parse_agent_sse_frame(payload_text)
                except (json.JSONDecodeError, ValueError):
                    parse_error_count += 1
                    continue
                if kind == "done":
                    done_count += 1
                else:
                    events.append(event)
            http_status = response.status_code
            content_type = response.headers.get("Content-Type")
    except DD.requests.RequestException as exception:
        result = _transport_failure_result(
            case_id,
            group,
            label,
            auth,
            "POST",
            path,
            payload,
            None,
            timeout,
            exception,
        )
        result.update(
            {
                "events": [],
                "data_line_count": 0,
                "done_count": 0,
                "parse_error_count": 0,
            }
        )
        return result

    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_agent_sse.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": "POST",
                    "path": path,
                    "json": payload,
                    "authorization_present": bool(auth),
                    "timeout_seconds": timeout,
                },
                "response": {
                    "http_status": http_status,
                    "content_type": content_type,
                    "data_line_count": data_line_count,
                    "done_count": done_count,
                    "parse_error_count": parse_error_count,
                    "events": events,
                },
            }
        ),
    )
    return {
        "http_status": http_status,
        "code": None,
        "message": None,
        "data": None,
        "content_type": content_type,
        "events": events,
        "data_line_count": data_line_count,
        "done_count": done_count,
        "parse_error_count": parse_error_count,
        "response_length": response_length,
        "elapsed_seconds": None,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _strict_json_post(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout: float = 180,
) -> dict[str, Any]:
    response = DD.requests.post(
        f"{DB._api_base(group)}{path}",
        headers={"Authorization": f"Bearer {auth}"},
        json=payload,
        timeout=timeout,
    )
    text_body = response.text
    try:
        body = _strict_json_loads(text_body)
        strict_parse_ok = True
    except (json.JSONDecodeError, ValueError):
        body = {}
        strict_parse_ok = False
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}_strict_json.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": "POST",
                    "path": path,
                    "json": payload,
                    "authorization_present": bool(auth),
                },
                "response": {
                    "http_status": response.status_code,
                    "content_type": response.headers.get("Content-Type"),
                    "strict_parse_ok": strict_parse_ok,
                    "body": body if strict_parse_ok else None,
                    "body_length": len(response.content),
                    "body_sha256": hashlib.sha256(response.content).hexdigest(),
                },
            }
        ),
    )
    body_dict = body if isinstance(body, dict) else {}
    return {
        "http_status": response.status_code,
        "code": body_dict.get("code"),
        "message": body_dict.get("message"),
        "data": body_dict.get("data"),
        "strict_parse_ok": strict_parse_ok,
        "content_type": response.headers.get("Content-Type"),
        "body_length": len(response.content),
        "body_sha256": hashlib.sha256(response.content).hexdigest(),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _api_token_exists(group: str, token: str) -> bool:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM api_token WHERE token=%s", (token,))
            return int(cursor.fetchone()[0]) == 1
    finally:
        connection.close()


def _create_beta_credential(case_id: str, group: str, auth: str, label: str) -> dict[str, Any]:
    response = _request(case_id, group, label, auth, "POST", "/system/tokens")
    data = response["data"] if isinstance(response["data"], dict) else {}
    response["_token"] = str(data.get("token") or "")
    response["_beta"] = str(data.get("beta") or "")
    response["_tenant_id"] = str(data.get("tenant_id") or "")
    return response


def _delete_beta_credential(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    token: str,
) -> dict[str, Any]:
    if not token:
        return {"http_status": 0, "code": None, "raw_sha256": None}
    encoded = DD.requests.utils.quote(token, safe="")
    response = DD.requests.delete(
        f"{DB._api_base(group)}/system/tokens/{encoded}",
        headers={"Authorization": f"Bearer {auth}"},
        timeout=30,
    )
    try:
        body: Any = response.json()
    except ValueError:
        body = {}
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    evidence.write_evidence(
        raw_path,
        evidence.sanitize(
            {
                "request": {
                    "method": "DELETE",
                    "path": "/system/tokens/<redacted>",
                    "authorization_present": bool(auth),
                    "token_fingerprint": DB._fingerprint(token),
                },
                "response": {
                    "http_status": response.status_code,
                    "body": body,
                },
            }
        ),
    )
    body_dict = body if isinstance(body, dict) else {}
    return {
        "http_status": response.status_code,
        "code": body_dict.get("code"),
        "message": body_dict.get("message"),
        "data": body_dict.get("data"),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _cleanup_beta_credential(
    case_id: str,
    group: str,
    auth: str,
    token: str,
    *,
    label: str = "cleanup_beta_credential",
) -> bool:
    if not token:
        return False
    response = _delete_beta_credential(case_id, group, auth, label, token)
    return response.get("code") == 0 and not _api_token_exists(group, token)


def _session_rows(group: str, chat_id: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,dialog_id,name,message,reference,user_id,create_time,update_time FROM conversation WHERE dialog_id=%s ORDER BY create_time DESC,id DESC",
                (chat_id,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "chat_id": str(row[1]),
            "name": str(row[2]),
            "messages": _json_value(row[3], []),
            "reference": _json_value(row[4], []),
            "user_id": "" if row[5] is None else str(row[5]),
            "create_time": int(row[6]) if row[6] is not None else None,
            "update_time": int(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]


def _session_snapshot(group: str, session_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,dialog_id,name,message,reference,user_id,create_time,update_time FROM conversation WHERE id=%s",
                (session_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    return {
        "count": 1,
        "id": str(row[0]),
        "chat_id": str(row[1]),
        "name": str(row[2]),
        "messages": _json_value(row[3], []),
        "reference": _json_value(row[4], []),
        "user_id": "" if row[5] is None else str(row[5]),
        "create_time": int(row[6]) if row[6] is not None else None,
        "update_time": int(row[7]) if row[7] is not None else None,
    }


def _cleanup_session_chat_prefix(case_id: str, group: str, auth: str, tenant_id: str, prefix: str) -> bool:
    chat_ids = _active_chat_ids_by_prefix(group, tenant_id, prefix)
    for chat_id in chat_ids:
        response = _delete_sessions(
            case_id,
            group,
            auth,
            "cleanup_existing_sessions",
            chat_id,
            {"delete_all": True},
        )
        if response["code"] != 0 or _conversation_count(group, chat_id) != 0:
            return False
    if chat_ids:
        response = _bulk_delete_chats(
            case_id,
            group,
            auth,
            "cleanup_existing_session_chats",
            {"ids": chat_ids},
        )
        if response["code"] != 0:
            return False
    return not _active_chat_ids_by_prefix(group, tenant_id, prefix)


def _cleanup_created_session_chat(
    case_id: str,
    group: str,
    auth: str,
    tenant_id: str,
    prefix: str,
    chat_id: str,
) -> bool:
    if not chat_id:
        return not _active_chat_ids_by_prefix(group, tenant_id, prefix)
    sessions = _delete_sessions(
        case_id,
        group,
        auth,
        "cleanup_created_sessions",
        chat_id,
        {"delete_all": True},
    )
    sessions_clean = sessions["code"] == 0 and _conversation_count(group, chat_id) == 0
    chat_clean = _cleanup_created_chat(case_id, group, auth, tenant_id, prefix, chat_id)
    return sessions_clean and chat_clean


def _response_chats(response: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    chats = data.get("chats") if isinstance(data.get("chats"), list) else []
    total = int(data.get("total") or 0)
    return [item for item in chats if isinstance(item, dict)], total


def _dialog_rows(group: str, chat_ids: list[str]) -> list[dict[str, Any]]:
    if not chat_ids:
        return []
    placeholders = ",".join(["%s"] * len(chat_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,name,status,tenant_id,create_time FROM dialog WHERE id IN ({placeholders}) ORDER BY id",
                tuple(chat_ids),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "name": str(row[1]),
            "status": str(row[2]),
            "tenant_id": str(row[3]),
            "create_time": int(row[4]) if row[4] is not None else None,
        }
        for row in rows
    ]


def _conversation_count(group: str, chat_id: str, session_id: str | None = None) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            if session_id:
                cursor.execute(
                    "SELECT COUNT(*) FROM conversation WHERE dialog_id=%s AND id=%s",
                    (chat_id, session_id),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM conversation WHERE dialog_id=%s",
                    (chat_id,),
                )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _create_chat_batch(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    count: int,
) -> list[dict[str, Any]]:
    return [
        _create_chat(
            case_id,
            group,
            owner["auth"],
            f"create_chat_{index}",
            {"name": f"{prefix}-{index:02d}"},
        )
        for index in range(1, count + 1)
    ]


def _delete_chat(case_id: str, group: str, auth: str, label: str, chat_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "DELETE", f"/chats/{chat_id}")


def _cleanup_created_chat(case_id: str, group: str, auth: str, tenant_id: str, prefix: str, chat_id: str) -> bool:
    response = _delete_chat(case_id, group, auth, "cleanup_created_chat", chat_id) if chat_id and _chat_snapshot(group, chat_id).get("status") == "1" else {"code": 0}
    return response.get("code") == 0 and not _active_chat_ids_by_prefix(group, tenant_id, prefix)


def _agent_dsl(*, probe: str | None = None) -> dict[str, Any]:
    globals_value: dict[str, Any] = {
        "sys.query": "",
        "sys.user_id": "",
        "sys.conversation_turns": 0,
        "sys.files": [],
    }
    if probe is not None:
        globals_value["plan_probe"] = probe
    return {
        "components": {
            "begin": {
                "obj": {"component_name": "Begin", "params": {}},
                "downstream": ["message"],
                "upstream": [],
            },
            "message": {
                "obj": {
                    "component_name": "Message",
                    "params": {"content": ["{sys.query}"]},
                },
                "downstream": [],
                "upstream": ["begin"],
            },
        },
        "history": [],
        "messages": [],
        "reference": [],
        "retrieval": [],
        "path": [],
        "answer": [],
        "globals": globals_value,
        "variables": {},
    }


def _agent_llm_dsl(llm_id: str) -> dict[str, Any]:
    return {
        "components": {
            "begin": {
                "obj": {"component_name": "Begin", "params": {}},
                "downstream": ["LLM:Fresh"],
                "upstream": [],
            },
            "LLM:Fresh": {
                "obj": {
                    "component_name": "LLM",
                    "params": {
                        "llm_id": llm_id,
                        "sys_prompt": "Answer the controlled test query briefly.",
                        "prompts": [{"role": "user", "content": "{sys.query}"}],
                        "cite": False,
                        "max_tokens": 128,
                    },
                },
                "downstream": ["Message:Fresh"],
                "upstream": ["begin"],
            },
            "Message:Fresh": {
                "obj": {
                    "component_name": "Message",
                    "params": {"content": ["{LLM:Fresh@content}"]},
                },
                "downstream": [],
                "upstream": ["LLM:Fresh"],
            },
        },
        "history": [],
        "messages": [],
        "reference": [],
        "retrieval": [],
        "path": [],
        "answer": [],
        "globals": {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
        },
        "variables": {},
    }


def _agent_update_dsl() -> dict[str, Any]:
    return {
        "components": {
            "begin": {
                "obj": {"component_name": "Begin", "params": {}},
                "downstream": ["message"],
                "upstream": [],
            },
            "message": {
                "obj": {
                    "component_name": "Message",
                    "params": {"content": ["{sys.query}"]},
                },
                "downstream": [],
                "upstream": ["begin"],
            },
        },
        "history": [],
        "retrieval": [],
        "path": [],
        "globals": {"sys.query": "", "plan_probe": "updated"},
        "variables": {},
    }


def _agent_runtime_dsl() -> dict[str, Any]:
    dsl = _agent_dsl(probe="stale-system-runtime")
    dsl["history"] = [{"role": "user", "content": "stale history"}]
    dsl["retrieval"] = [{"chunk_id": "stale-chunk"}]
    dsl["memory"] = [{"id": "stale-memory"}]
    dsl["path"] = ["begin", "message"]
    dsl["globals"].update(
        {
            "sys.query": "stale query",
            "sys.user_id": "stale user",
            "sys.conversation_turns": 7,
            "sys.files": ["stale-file"],
            "sys.history": ["stale-history"],
            "sys.date": "stale-date",
            "env.keep": "stale-env",
            "custom.user": "preserve-custom-value",
        }
    )
    dsl["variables"] = {"keep": {"type": "string", "value": "environment-default"}}
    dsl["components"]["message"]["obj"]["params"].update(
        {
            "inputs": {"runtime": {"type": "str", "value": "stale-input"}},
            "outputs": {
                "content": {"type": "str", "value": "stale-output"},
                "downloads": {"type": "list", "value": ["stale-download"]},
            },
            "debug_inputs": {"runtime": "stale-debug"},
        }
    )
    return dsl


def _agent_tool_logging_dsl(llm_id: str) -> dict[str, Any]:
    agent_id = "Agent:FreshLogProbe"
    message_id = "Message:FreshLogProbe"
    return {
        "components": {
            "begin": {
                "obj": {"component_name": "Begin", "params": {}},
                "downstream": [agent_id],
                "upstream": [],
            },
            agent_id: {
                "obj": {
                    "component_name": "Agent",
                    "params": {
                        "llm_id": llm_id,
                        "sys_prompt": ("Use the available retrieval tool when useful, then answer briefly. This is a controlled execution-log probe."),
                        "prompts": [{"role": "user", "content": "{sys.query}"}],
                        "tools": [
                            {
                                "component_name": "Retrieval",
                                "name": "Fresh Retrieval Probe",
                                "params": {
                                    "dataset_ids": [],
                                    "kb_ids": [],
                                    "similarity_threshold": 0.2,
                                    "keywords_similarity_weight": 0.5,
                                    "top_n": 8,
                                    "top_k": 1024,
                                    "rerank_id": "",
                                    "empty_response": "",
                                    "use_kg": False,
                                    "cross_languages": [],
                                    "toc_enhance": False,
                                    "outputs": {
                                        "formalized_content": {
                                            "type": "string",
                                            "value": "",
                                        }
                                    },
                                },
                            }
                        ],
                        "max_rounds": 1,
                        "max_retries": 0,
                        "message_history_window_size": 12,
                        "cite": False,
                        "max_tokens": 256,
                        "outputs": {
                            "content": {"type": "string", "value": ""},
                            "structured": {},
                        },
                    },
                },
                "downstream": [message_id],
                "upstream": ["begin"],
            },
            message_id: {
                "obj": {
                    "component_name": "Message",
                    "params": {"content": [f"{{{agent_id}@content}}"]},
                },
                "downstream": [],
                "upstream": [agent_id],
            },
        },
        "history": [],
        "messages": [],
        "reference": [],
        "retrieval": [],
        "path": [],
        "answer": [],
        "globals": {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
            "sys.history": [],
        },
        "variables": {},
    }


def _large_agent_dsl() -> dict[str, Any]:
    components: dict[str, Any] = {
        "begin": {
            "obj": {"component_name": "Begin", "params": {}},
            "downstream": ["Message:001"],
            "upstream": [],
        }
    }
    for index in range(1, 100):
        component_id = f"Message:{index:03d}"
        previous_id = "begin" if index == 1 else f"Message:{index - 1:03d}"
        next_ids = [f"Message:{index + 1:03d}"] if index < 99 else []
        components[component_id] = {
            "obj": {
                "component_name": "Message",
                "params": {"content": [f"fresh-large-dsl-node-{index:03d}-" + ("x" * 1200)]},
            },
            "downstream": next_ids,
            "upstream": [previous_id],
        }
    return {
        "components": components,
        "history": [],
        "messages": [],
        "reference": [],
        "retrieval": {"chunks": [], "doc_aggs": []},
        "path": [],
        "answer": [],
        "globals": {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
        },
        "variables": {},
    }


def isolated_float_probe_source() -> str:
    return """
import ast
import json
import math
from pathlib import Path

import numpy as np

module_path = Path("api/apps/restful_apis/chat_api.py")
tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
target = next(
    node
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    and node.name == "_sanitize_json_floats"
)
namespace = {"math": math}
exec(
    compile(ast.Module(body=[target], type_ignores=[]), str(module_path), "exec"),
    namespace,
)
sanitize_json_floats = namespace["_sanitize_json_floats"]

original = {
    "python": [float("nan"), float("inf"), float("-inf")],
    "numpy": {"f16": np.float16("nan"), "f32": np.float32("inf"), "f64": np.float64("-inf")},
    "tuple": (np.float32("nan"), "keep"),
    "unchanged": {"integer": 7, "text": "fresh", "flag": True},
}
result = sanitize_json_floats(original)
payload = {
    "nested_values_sanitized": result["python"] == [None, None, None]
        and result["numpy"] == {"f16": None, "f32": None, "f64": None}
        and result["tuple"] == (None, "keep"),
    "other_values_unchanged": result["unchanged"] == original["unchanged"],
}
print(json.dumps(payload, allow_nan=False, sort_keys=True))
"""


def _run_isolated_float_sanitizer_probe(case_id: str, group: str) -> dict[str, Any]:
    source = isolated_float_probe_source()
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(EXECUTE_DIR.parents[3]),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        payload = _strict_json_loads(lines[-1]) if lines else {}
    except (json.JSONDecodeError, ValueError):
        payload = {}
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_isolated_float_sanitizer_probe.json"
    evidence.write_evidence(
        raw_path,
        {
            "process": {
                "executable_name": Path(sys.executable).name,
                "isolated": True,
                "exit_code": completed.returncode,
                "stdout_length": len(completed.stdout.encode("utf-8")),
                "stdout_sha256": hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
                "stderr_length": len(completed.stderr.encode("utf-8")),
                "stderr_sha256": hashlib.sha256(completed.stderr.encode("utf-8")).hexdigest(),
            },
            "result": payload,
        },
    )
    return {
        "exit_code": completed.returncode,
        "nested_values_sanitized": payload.get("nested_values_sanitized") is True,
        "other_values_unchanged": payload.get("other_values_unchanged") is True,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _webhook_agent_dsl(
    response_body: dict[str, Any],
    *,
    response_status: int = 202,
    security: dict[str, Any] | None = None,
) -> dict[str, Any]:
    begin_params = {
        "mode": "Webhook",
        "methods": ["POST", "GET"],
        "content_types": "application/json",
        "security": security
        or {
            "auth_type": "none",
            "allow_anonymous": True,
            "max_body_size": "1MB",
        },
        "schema": {
            "query": {
                "properties": {"param1": {"type": "string"}},
                "required": [],
            },
            "headers": {"properties": {}, "required": []},
            "body": {
                "properties": {"input": {"type": "string"}},
                "required": [],
            },
        },
        "execution_mode": "Immediately",
        "response": {
            "status": response_status,
            "body_template": json.dumps(response_body, ensure_ascii=False),
        },
    }
    return {
        "components": {
            "begin": {
                "obj": {"component_name": "Begin", "params": begin_params},
                "downstream": ["message"],
                "upstream": [],
            },
            "message": {
                "obj": {
                    "component_name": "Message",
                    "params": {"content": ["webhook execution finished"]},
                },
                "downstream": [],
                "upstream": ["begin"],
            },
        },
        "history": [],
        "messages": [],
        "reference": [],
        "retrieval": [],
        "path": [],
        "answer": [],
        "globals": {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
            "sys.history": [],
        },
        "variables": {},
    }


def _non_webhook_agent_dsl() -> dict[str, Any]:
    dsl = _agent_dsl(probe="non-webhook")
    dsl["components"]["begin"]["obj"]["params"]["mode"] = "conversational"
    return dsl


def _create_agent(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    *,
    title: str,
    dsl: dict[str, Any],
    tags: str | None = "fresh-agent",
    permission: str | None = None,
    canvas_category: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": title, "dsl": dsl}
    if tags is not None:
        payload["tags"] = tags
    if permission is not None:
        payload["permission"] = permission
    if canvas_category is not None:
        payload["canvas_category"] = canvas_category
    return _request(case_id, group, label, auth, "POST", "/agents", payload=payload)


def _list_agents(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", "/agents", params=params)


def _get_agent(case_id: str, group: str, auth: str, label: str, agent_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", f"/agents/{agent_id}")


def _delete_agent(case_id: str, group: str, auth: str, label: str, agent_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "DELETE", f"/agents/{agent_id}")


def _put_agent(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PUT",
        f"/agents/{agent_id}",
        payload=payload,
    )


def _reset_agent(case_id: str, group: str, auth: str, label: str, agent_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "POST", f"/agents/{agent_id}/reset")


def _create_agent_session(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/agents/{agent_id}/sessions",
        payload=payload,
    )


def _list_agent_sessions(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/agents/{agent_id}/sessions",
        params=params,
    )


def _delete_agent_session_item(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    session_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/agents/{agent_id}/sessions/{session_id}",
    )


def _bulk_delete_agent_sessions(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/agents/{agent_id}/sessions",
        payload=payload,
    )


def _complete_agent_nonstream(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    session_id: str,
    question: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/agents/chat/completions",
        payload={
            "agent_id": agent_id,
            "session_id": session_id,
            "question": question,
            "stream": False,
        },
        timeout=180,
    )


def _get_agent_logs(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    message_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/agents/{agent_id}/logs/{message_id}",
    )


def _webhook_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    *,
    auth: str | None = None,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    custom_headers: dict[str, str] | None = None,
    raw_body: bytes | None = None,
    content_type: str = "application/json",
    timeout: float = 120,
) -> dict[str, Any]:
    headers: dict[str, str] = {"Content-Type": content_type}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    if custom_headers:
        headers.update(custom_headers)
    response = DD.requests.request(
        method,
        f"{DB._api_base(group)}{path}",
        headers=headers,
        json=payload if raw_body is None else None,
        data=raw_body,
        params=params,
        timeout=timeout,
    )
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    request_body_metadata: Any = payload
    if raw_body is not None:
        request_body_metadata = {
            "length": len(raw_body),
            "sha256": hashlib.sha256(raw_body).hexdigest(),
        }
    evidence.write_evidence(
        raw_path,
        {
            "request": {
                "method": method,
                "path": path,
                "params": params,
                "body": request_body_metadata,
                "authorization_present": bool(auth),
                "custom_header_names": sorted((custom_headers or {}).keys()),
                "content_type": content_type,
            },
            "response": {
                "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "body": body,
                "body_length": len(response.content),
                "body_sha256": hashlib.sha256(response.content).hexdigest(),
            },
        },
    )
    body_dict = body if isinstance(body, dict) else {}
    return {
        "http_status": response.status_code,
        "code": body_dict.get("code"),
        "message": body_dict.get("message"),
        "data": body_dict.get("data"),
        "body": body,
        "content_type": response.headers.get("Content-Type"),
        "body_length": len(response.content),
        "body_sha256": hashlib.sha256(response.content).hexdigest(),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _poll_webhook_trace(
    case_id: str,
    group: str,
    auth: str,
    agent_id: str,
    since_ts: float,
    *,
    label_prefix: str,
    max_attempts: int = 20,
) -> dict[str, Any]:
    webhook_id = ""
    next_since_ts = float(since_ts)
    events: list[Any] = []
    responses: list[dict[str, Any]] = []
    finished = False
    for attempt in range(1, max_attempts + 1):
        params: dict[str, Any] = {"since_ts": next_since_ts}
        if webhook_id:
            params["webhook_id"] = webhook_id
        response = _request(
            case_id,
            group,
            f"{label_prefix}_{attempt:02d}",
            auth,
            "GET",
            f"/agents/{agent_id}/webhook/logs",
            params=params,
        )
        responses.append(response)
        data = response["data"] if isinstance(response["data"], dict) else {}
        if data.get("webhook_id"):
            webhook_id = str(data["webhook_id"])
        new_events = data.get("events") if isinstance(data.get("events"), list) else []
        events.extend(new_events)
        if isinstance(data.get("next_since_ts"), (int, float)):
            next_since_ts = float(data["next_since_ts"])
        finished = data.get("finished") is True
        if finished:
            break
        time.sleep(0.1)
    return {
        "webhook_id": webhook_id,
        "events": events,
        "finished": finished,
        "next_since_ts": next_since_ts,
        "responses": responses,
    }


def _trace_contains(events: list[Any], *values: str) -> bool:
    serialized = json.dumps(events, ensure_ascii=False, sort_keys=True)
    return all(value in serialized for value in values)


def _list_agent_templates(case_id: str, group: str, auth: str, label: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", "/agents/templates")


def _get_agent_prompts(case_id: str, group: str, auth: str, label: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", "/agents/prompts")


def _list_agent_versions(case_id: str, group: str, auth: str, label: str, agent_id: str) -> dict[str, Any]:
    return _request(case_id, group, label, auth, "GET", f"/agents/{agent_id}/versions")


def _get_agent_version(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    agent_id: str,
    version_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/agents/{agent_id}/versions/{version_id}",
    )


def _agent_snapshot(group: str, agent_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,user_id,title,canvas_category,permission,tags,dsl,LENGTH(tags) FROM user_canvas WHERE id=%s",
                (agent_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    return {
        "count": 1,
        "id": str(row[0]),
        "user_id": str(row[1]),
        "title": row[2],
        "canvas_category": row[3],
        "permission": row[4],
        "tags": "" if row[5] is None else str(row[5]),
        "physical_tags_is_null": row[5] is None,
        "physical_tags_is_empty": row[5] == "" and row[7] == 0,
        "dsl": _json_value(row[6], {}),
    }


def _agent_ids_by_prefix(group: str, tenant_id: str, prefix: str) -> list[str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM user_canvas WHERE user_id=%s AND LOWER(title) LIKE %s ORDER BY id",
                (tenant_id, prefix.lower() + "%"),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _visible_agent_count(group: str, tenant_id: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM user_canvas uc WHERE uc.user_id=%s OR "
                "(uc.permission=%s AND uc.user_id IN "
                "(SELECT ut.tenant_id FROM user_tenant ut JOIN tenant t "
                "ON t.id=ut.tenant_id WHERE ut.user_id=%s AND ut.status=%s "
                "AND ut.role=%s AND t.status=%s))",
                (tenant_id, "team", tenant_id, "1", "normal", "1"),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _agent_version_count(group: str, agent_id: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM user_canvas_version WHERE user_canvas_id=%s",
                (agent_id,),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _agent_version_rows(group: str, agent_id: str) -> list[dict[str, Any]]:
    release_column = "v.`release`" if group == "control" else 'v."release"'
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT v.id,v.user_canvas_id,v.title,v.description,"
                f"{release_column},v.dsl,v.create_time,v.update_time "
                "FROM user_canvas_version v WHERE v.user_canvas_id=%s "
                "ORDER BY v.create_time ASC,v.id ASC",
                (agent_id,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "user_canvas_id": str(row[1]),
            "title": row[2],
            "description": row[3],
            "release": bool(row[4]),
            "dsl": _json_value(row[5], {}),
            "create_time": int(row[6]) if row[6] is not None else None,
            "update_time": int(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]


def _agent_session_rows(group: str, agent_id: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,name,dialog_id,user_id,exp_user_id,message,reference,"
                "source,dsl,version_title,duration,round,thumb_up,create_time,"
                "update_time FROM api_4_conversation "
                "WHERE dialog_id=%s ORDER BY create_time ASC,id ASC",
                (agent_id,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "dialog_id": str(row[2]),
            "user_id": str(row[3]),
            "exp_user_id": None if row[4] is None else str(row[4]),
            "message": _json_value(row[5], []),
            "reference": _json_value(row[6], []),
            "source": row[7],
            "dsl": _json_value(row[8], {}),
            "version_title": row[9],
            "duration": float(row[10]),
            "round": int(row[11]),
            "thumb_up": int(row[12]),
            "create_time": int(row[13]) if row[13] is not None else None,
            "update_time": int(row[14]) if row[14] is not None else None,
        }
        for row in rows
    ]


def _canvas_template_rows(group: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id,title,description,canvas_type,canvas_types,canvas_category,dsl FROM canvas_template ORDER BY id ASC")
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "title": _json_value(row[1], {}),
            "description": _json_value(row[2], {}),
            "canvas_type": row[3],
            "canvas_types": _json_value(row[4], []),
            "canvas_category": row[5],
            "dsl": _json_value(row[6], {}),
        }
        for row in rows
    ]


def _redis_client(group: str):
    import redis
    import yaml

    config = yaml.safe_load((RUNTIME_DIR / group / "conf" / "service_conf.yaml").read_text(encoding="utf-8"))["redis"]
    host, port = str(config["host"]).rsplit(":", 1)
    return redis.Redis(
        host=host,
        port=int(port),
        db=int(config["db"]),
        username=config.get("username") or None,
        password=config.get("password") or None,
        decode_responses=True,
        socket_connect_timeout=5,
    )


def _agent_replica_snapshot(group: str, agent_id: str, tenant_id: str, runtime_user_id: str | None = None) -> dict[str, Any]:
    key = f"canvas:replica:{agent_id}:{tenant_id}:{runtime_user_id if runtime_user_id is not None else tenant_id}"
    client = _redis_client(group)
    value = client.get(key)
    return {
        "exists": value is not None,
        "payload": _json_value(value, {}) if value is not None else None,
        "ttl": int(client.ttl(key)),
        "key_fingerprint": DB._fingerprint(key),
    }


def _agent_log_redis_snapshot(group: str, agent_id: str, message_id: str) -> dict[str, Any]:
    key = f"{agent_id}-{message_id}-logs"
    client = _redis_client(group)
    value = client.get(key)
    return {
        "exists": value is not None,
        "payload": _json_value(value, []) if value is not None else None,
        "ttl": int(client.ttl(key)),
        "key_fingerprint": DB._fingerprint(key),
    }


def _cleanup_agent_sessions_and_agent(
    case_id: str,
    group: str,
    auth: str,
    tenant_id: str,
    prefix: str,
    agent_id: str,
) -> dict[str, Any]:
    session_ids = [row["id"] for row in _agent_session_rows(group, agent_id)]
    if session_ids:
        session_response = _bulk_delete_agent_sessions(
            case_id,
            group,
            auth,
            "cleanup_agent_sessions",
            agent_id,
            {"ids": session_ids},
        )
        session_code = session_response.get("code")
    else:
        session_code = 0
    sessions_removed = not _agent_session_rows(group, agent_id)
    agent_removed = _cleanup_created_agents(case_id, group, auth, tenant_id, prefix, [agent_id])
    return {
        "session_code": session_code,
        "sessions_removed": sessions_removed,
        "agent_removed": agent_removed,
        "succeeded": session_code == 0 and sessions_removed and agent_removed,
    }


def _component_topology(dsl: dict[str, Any]) -> dict[str, Any]:
    components = dsl.get("components") if isinstance(dsl, dict) else None
    if not isinstance(components, dict):
        return {}
    return {
        str(component_id): {
            "component_name": (component.get("obj", {}).get("component_name") if isinstance(component, dict) else None),
            "upstream": component.get("upstream") if isinstance(component, dict) else None,
            "downstream": component.get("downstream") if isinstance(component, dict) else None,
        }
        for component_id, component in components.items()
    }


def _component_runtime_cleared(dsl: dict[str, Any]) -> bool:
    components = dsl.get("components") if isinstance(dsl, dict) else None
    if not isinstance(components, dict):
        return False
    for component in components.values():
        if not isinstance(component, dict):
            return False
        params = component.get("obj", {}).get("params", {})
        if not isinstance(params, dict):
            return False
        if params.get("debug_inputs") not in (None, {}):
            return False
        for field in ("inputs", "outputs"):
            values = params.get(field, {})
            if not isinstance(values, dict):
                return False
            for value in values.values():
                if isinstance(value, dict) and value.get("value") is not None:
                    return False
    return True


def _cleanup_agent_prefix(case_id: str, group: str, auth: str, tenant_id: str, prefix: str) -> bool:
    ids = _agent_ids_by_prefix(group, tenant_id, prefix)
    for index, agent_id in enumerate(ids, start=1):
        response = _delete_agent(
            case_id,
            group,
            auth,
            f"cleanup_existing_agent_{index}",
            agent_id,
        )
        if response.get("code") != 0:
            return False
    return not _agent_ids_by_prefix(group, tenant_id, prefix)


def _cleanup_created_agents(
    case_id: str,
    group: str,
    auth: str,
    tenant_id: str,
    prefix: str,
    agent_ids: list[str],
) -> bool:
    ok = True
    for index, agent_id in enumerate(agent_ids, start=1):
        if agent_id and _agent_snapshot(group, agent_id).get("count") == 1:
            response = _delete_agent(case_id, group, auth, f"cleanup_created_agent_{index}", agent_id)
            ok = response.get("code") == 0 and ok
    return ok and not _agent_ids_by_prefix(group, tenant_id, prefix)


def _finalize(recorder, case_id: str) -> dict[str, Any]:
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


def _dataset_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    *,
    with_chunk: bool,
    embedding_model: str | None = None,
    chunk_content: str | None = None,
) -> dict[str, Any]:
    DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
    payload: dict[str, Any] = {"name": prefix}
    if embedding_model:
        payload["embedding_model"] = embedding_model
    created = DD._create_dataset(case_id, group, owner["auth"], f"create_dataset_{prefix}", payload)
    data = created["data"] if isinstance(created["data"], dict) else {}
    dataset_id = str(data.get("id") or "")
    document_id = ""
    chunk_id = ""
    api_codes = [created["code"]]
    if dataset_id and with_chunk:
        document = DD._upload_empty_document(
            case_id,
            group,
            owner["auth"],
            f"create_document_{prefix}",
            dataset_id,
            f"{prefix}.txt",
        )
        document_data = document["data"] if isinstance(document["data"], dict) else {}
        document_id = str(document_data.get("id") or "")
        api_codes.append(document["code"])
        chunk = DD._add_chunk(
            case_id,
            group,
            owner["auth"],
            f"add_chunk_{prefix}",
            dataset_id,
            document_id,
            {
                "content": chunk_content or f"fresh chat fixture {prefix}",
                "important_keywords": ["fresh", "chat"],
                "questions": ["what is this fixture"],
            },
        )
        chunk_data = chunk["data"] if isinstance(chunk["data"], dict) else {}
        chunk_id = str((chunk_data.get("chunk") or {}).get("id") or "")
        api_codes.append(chunk["code"])
    snapshot = DD._dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
    return {
        "id": dataset_id,
        "name": prefix,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "api_codes": api_codes,
        "ready": bool(dataset_id) and all(code == 0 for code in api_codes) and (not with_chunk or snapshot.get("chunk_num") == 1),
        "embedding_model": snapshot.get("embedding_model"),
        "snapshot": snapshot,
    }


def _cleanup_dataset_fixture(case_id: str, group: str, owner: dict[str, str], dataset_ids: list[str]) -> bool:
    ids = [item for item in dataset_ids if item]
    if not ids:
        return True
    response = DD._delete_ids(case_id, group, owner["auth"], "cleanup_chat_datasets", ids)
    return response["code"] == 0 and all(DD._dataset_snapshot(group, dataset_id).get("count") == 0 for dataset_id in ids)


def searchbot_fixture_config(dataset_id: str) -> dict[str, Any]:
    return {
        "kb_ids": [dataset_id],
        "doc_ids": [],
        "similarity_threshold": 0.0,
        "vector_similarity_weight": 1.0,
        "top_k": 1024,
    }


def searchbot_ask_fixture_content() -> str:
    return "什么是向量检索？向量检索把查询与文档转换为向量，再依据相似度从数据集中返回相关内容。"


def chat_mindmap_fixture_content() -> str:
    return "RAGFlow的架构包括API层、检索层、文档解析层和模型层。"


def searchbot_retrieval_fixture_content() -> str:
    return "文档解析流程包括上传、解析、切分、向量化、建立索引和检索。每一步都应能从数据集的 chunk 中回读。"


def searchbot_mindmap_fixture_content() -> str:
    return "系统架构包括 API 服务、元数据存储、文档解析、向量索引、检索服务和模型推理，各模块按数据流连接。"


def _search_app_count(group: str, search_id: str) -> int:
    table = "`search`" if group == "control" else '"search"'
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE id=%s", (search_id,))
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _cleanup_search_prefix(
    case_id: str,
    group: str,
    auth: str,
    prefix: str,
) -> bool:
    listed = _request(
        case_id,
        group,
        "list_existing_search_apps_for_cleanup",
        auth,
        "GET",
        "/searches",
        params={"keywords": prefix, "page": 1, "page_size": 100},
    )
    data = listed["data"] if isinstance(listed["data"], dict) else {}
    rows = data.get("search_apps") if isinstance(data.get("search_apps"), list) else []
    ok = listed["code"] == 0
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not str(row.get("name") or "").startswith(prefix):
            continue
        search_id = str(row.get("id") or row.get("search_id") or "")
        if not search_id:
            ok = False
            continue
        deleted = _request(
            case_id,
            group,
            f"cleanup_existing_search_app_{index}",
            auth,
            "DELETE",
            f"/searches/{search_id}",
        )
        ok = deleted["code"] == 0 and _search_app_count(group, search_id) == 0 and ok
    return ok


def _create_search_app(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    name: str,
    dataset_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/searches",
        payload={
            "name": name,
            "description": "fresh deterministic searchbot fixture",
            "search_config": searchbot_fixture_config(dataset_id),
        },
    )


def _cleanup_search_app(
    case_id: str,
    group: str,
    auth: str,
    search_id: str,
) -> bool:
    if not search_id:
        return False
    response = _request(
        case_id,
        group,
        "cleanup_created_search_app",
        auth,
        "DELETE",
        f"/searches/{search_id}",
    )
    return response["code"] == 0 and _search_app_count(group, search_id) == 0


class _EmbeddingStubHandler(BaseHTTPRequestHandler):
    alias = "fresh-cs-008-alt-embedding"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/tags":
            self._send(
                {
                    "models": [
                        {
                            "name": self.alias,
                            "model": self.alias,
                            "details": {"family": "fresh", "families": ["fresh"]},
                        }
                    ]
                }
            )
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            request_body = json.loads(raw)
        except json.JSONDecodeError:
            request_body = {}
        if self.path == "/api/show":
            self._send(
                {
                    "details": {"family": "fresh", "families": ["fresh"]},
                    "capabilities": ["embedding"],
                    "model_info": {"fresh.context_length": 8192},
                }
            )
            return
        if self.path == "/api/embed":
            inputs = request_body.get("input", [])
            if isinstance(inputs, str):
                inputs = [inputs]
            vector = [0.03125] * 1024
            self._send(
                {
                    "model": request_body.get("model") or self.alias,
                    "embeddings": [vector for _ in inputs],
                    "prompt_eval_count": max(1, len(inputs)),
                }
            )
            return
        self._send({"error": "not found"}, 404)


@contextmanager
def _embedding_stub() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EmbeddingStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _AudioModelStubHandler(BaseHTTPRequestHandler):
    tts_model = "fresh-controlled-tts"
    asr_model = "fresh-controlled-asr"
    audio_bytes = b"ID3\x04\x00\x00fresh-controlled-audio"
    transcription_text = "fresh controlled transcription"
    calls: list[dict[str, Any]] = []

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path == "/v1/audio/speech":
            try:
                request_body = json.loads(body or b"{}")
            except json.JSONDecodeError:
                request_body = {}
            type(self).calls.append(
                {
                    "kind": "tts",
                    "model": request_body.get("model"),
                    "input": request_body.get("input"),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(type(self).audio_bytes)))
            self.end_headers()
            self.wfile.write(type(self).audio_bytes)
            return
        if self.path == "/v1/audio/transcriptions":
            type(self).calls.append(
                {
                    "kind": "asr",
                    "content_type_is_multipart": str(self.headers.get("Content-Type") or "").startswith("multipart/form-data"),
                    "body_length": len(body),
                }
            )
            self._send_json({"text": type(self).transcription_text})
            return
        type(self).calls.append({"kind": "unknown", "path": self.path})
        self._send_json({"error": "not found"}, 404)


@contextmanager
def _audio_model_stub() -> Iterator[str]:
    _AudioModelStubHandler.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AudioModelStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _provider_names(case_id: str, group: str, auth: str, label: str) -> tuple[list[str], str]:
    response = _request(case_id, group, label, auth, "GET", "/providers")
    rows = response["data"] if isinstance(response["data"], list) else []
    return (
        [str(item.get("name") or "") for item in rows if isinstance(item, dict)],
        response["raw_sha256"],
    )


def _ensure_provider(case_id: str, group: str, auth: str, provider_name: str) -> dict[str, Any]:
    names_before, before_sha = _provider_names(case_id, group, auth, f"list_providers_before_{provider_name.lower()}")
    added = provider_name not in names_before
    add_response = {"code": 0, "raw_sha256": None}
    if added:
        add_response = _request(
            case_id,
            group,
            f"add_{provider_name.lower()}_provider",
            auth,
            "PUT",
            "/providers",
            payload={"provider_name": provider_name},
        )
    names_after, after_sha = _provider_names(case_id, group, auth, f"list_providers_after_{provider_name.lower()}")
    return {
        "ready": add_response["code"] == 0 and provider_name in names_after,
        "added": added,
        "add_code": add_response["code"],
        "before_raw_sha256": before_sha,
        "add_raw_sha256": add_response.get("raw_sha256"),
        "after_raw_sha256": after_sha,
    }


def _remove_provider_if_added(
    case_id: str,
    group: str,
    auth: str,
    provider_name: str,
    added: bool,
) -> bool:
    if not added:
        return True
    response = _request(
        case_id,
        group,
        f"remove_{provider_name.lower()}_provider",
        auth,
        "DELETE",
        f"/providers/{provider_name}",
    )
    names_after, _sha = _provider_names(case_id, group, auth, f"list_providers_after_remove_{provider_name.lower()}")
    return response["code"] == 0 and provider_name not in names_after


def _provider_instance_names(case_id: str, group: str, auth: str, provider_name: str = "Ollama") -> tuple[list[str], str]:
    label = "list_ollama_instances" if provider_name == "Ollama" else f"list_{provider_name.lower()}_instances"
    response = _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/providers/{provider_name}/instances",
    )
    rows = response["data"] if isinstance(response["data"], list) else []
    return (
        [str(item.get("instance_name")) for item in rows if isinstance(item, dict)],
        response["raw_sha256"],
    )


def _drop_provider_instance(
    case_id: str,
    group: str,
    auth: str,
    instance_name: str,
    provider_name: str = "Ollama",
) -> bool:
    names, _sha = _provider_instance_names(case_id, group, auth, provider_name)
    if instance_name not in names:
        return True
    label = "drop_alt_embedding_instance" if provider_name == "Ollama" else f"drop_{provider_name.lower()}_instance"
    response = _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/providers/{provider_name}/instances",
        payload={"instances": [instance_name]},
    )
    names_after, _sha_after = _provider_instance_names(case_id, group, auth, provider_name)
    return response["code"] == 0 and instance_name not in names_after


def _default_model_entry(case_id: str, group: str, auth: str, model_type: str) -> tuple[dict[str, Any] | None, str]:
    response = _request(
        case_id,
        group,
        f"list_default_models_for_{model_type}",
        auth,
        "GET",
        "/models/default",
    )
    data = response["data"] if isinstance(response["data"], dict) else {}
    rows = data.get("models") if isinstance(data.get("models"), list) else []
    internal_type = "speech2text" if model_type == "asr" else model_type
    entry = next(
        (item for item in rows if isinstance(item, dict) and item.get("model_type") == internal_type),
        None,
    )
    return entry, response["raw_sha256"]


def _set_default_model(
    case_id: str,
    group: str,
    auth: str,
    model_type: str,
    entry: dict[str, Any] | None,
    label: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model_type": model_type}
    if entry:
        payload.update(
            {
                "model_provider": entry["model_provider"],
                "model_instance": entry["model_instance"],
                "model_name": entry["model_name"],
            }
        )
    return _request(
        case_id,
        group,
        label,
        auth,
        "PATCH",
        "/models/default",
        payload=payload,
    )


def _default_entry_matches(entry: dict[str, Any] | None, expected: dict[str, Any] | None) -> bool:
    if entry is None or expected is None:
        return entry is expected
    return all(entry.get(key) == expected.get(key) for key in ("model_provider", "model_instance", "model_name", "model_type"))


def _audio_instance_name(case_id: str, group: str) -> str:
    return f"fresh-{case_id.lower()}-{group}-audio"


def _create_audio_provider_instance(
    case_id: str,
    group: str,
    auth: str,
    base_url: str,
) -> dict[str, Any]:
    instance_name = _audio_instance_name(case_id, group)
    provider = _ensure_provider(case_id, group, auth, "OpenAI")
    precleaned = provider["ready"] and _drop_provider_instance(case_id, group, auth, instance_name, "OpenAI")
    response = _request(
        case_id,
        group,
        "create_controlled_audio_provider_instance",
        auth,
        "POST",
        "/providers/OpenAI/instances",
        payload={
            "instance_name": instance_name,
            "api_key": "x",
            "base_url": base_url,
            "region": "",
            "model_info": [
                {
                    "model_name": _AudioModelStubHandler.tts_model,
                    "model_type": ["tts"],
                    "max_tokens": 4096,
                },
                {
                    "model_name": _AudioModelStubHandler.asr_model,
                    "model_type": ["speech2text"],
                    "max_tokens": 4096,
                },
            ],
        },
        timeout=180,
    )
    names, list_sha = _provider_instance_names(case_id, group, auth, "OpenAI")
    return {
        "instance_name": instance_name,
        "provider_ready": provider["ready"],
        "provider_added": provider["added"],
        "provider_add_code": provider["add_code"],
        "precleaned": precleaned,
        "create_code": response["code"],
        "visible": instance_name in names,
        "raw_sha256": response["raw_sha256"],
        "list_raw_sha256": list_sha,
    }


def _controlled_audio_default_entry(instance_name: str, model_type: str) -> dict[str, Any]:
    return {
        "model_provider": "OpenAI",
        "model_instance": instance_name,
        "model_name": (_AudioModelStubHandler.tts_model if model_type == "tts" else _AudioModelStubHandler.asr_model),
        "model_type": "tts" if model_type == "tts" else "speech2text",
    }


def _wav_fixture_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def _create_alt_embedding_instance(
    case_id: str,
    group: str,
    auth: str,
    base_url: str,
) -> dict[str, Any]:
    instance_name = f"fresh-cs-008-{group}-alt"
    alias = _EmbeddingStubHandler.alias
    precleaned = _drop_provider_instance(case_id, group, auth, instance_name)
    response = _request(
        case_id,
        group,
        "create_alt_embedding_instance",
        auth,
        "POST",
        "/providers/Ollama/instances",
        payload={
            "instance_name": instance_name,
            "api_key": "x",
            "base_url": base_url,
            "region": "",
            "model_info": [
                {
                    "model_name": alias,
                    "model_type": ["embedding"],
                    "max_tokens": 8192,
                }
            ],
        },
        timeout=180,
    )
    models = _request(
        case_id,
        group,
        "list_embedding_models_after_alt_create",
        auth,
        "GET",
        "/models",
        params={"type": "embedding"},
    )
    rows = models["data"] if isinstance(models["data"], list) else []
    visible = any(isinstance(item, dict) and item.get("name") == alias and item.get("instance_name") == instance_name and item.get("provider_name") == "Ollama" for item in rows)
    return {
        "model_id": f"{alias}@{instance_name}@Ollama",
        "instance_name": instance_name,
        "precleaned": precleaned,
        "create_code": response["code"],
        "visible": visible,
        "raw_sha256": response["raw_sha256"],
    }


def run_cs001() -> dict[str, Any]:
    case_id = "TC-CS-001"
    prefix = "fresh-cs-001"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        default_llm = _tenant_default_llm(group, owner["tenant_id"])
        name = f"{prefix}-GaussDB最小Chat"
        response = _create_chat(case_id, group, owner["auth"], "create_minimal_chat", {"name": name})
        data = response["data"] if isinstance(response["data"], dict) else {}
        chat_id = str(data.get("id") or "")
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "id_present": bool(chat_id),
            "name_matches": data.get("name") == name and snapshot.get("name") == name,
            "dataset_ids": data.get("dataset_ids"),
            "llm_inherited": data.get("llm_id") == default_llm and snapshot.get("llm_id") == default_llm,
            "api_rerank_id": data.get("rerank_id"),
            "top_n": data.get("top_n"),
            "top_k": data.get("top_k"),
            "similarity_threshold": data.get("similarity_threshold"),
            "vector_similarity_weight": data.get("vector_similarity_weight"),
            "database_count": snapshot.get("count"),
            "tenant_matches": snapshot.get("tenant_id") == owner["tenant_id"],
            "status": snapshot.get("status"),
            "kb_ids": snapshot.get("kb_ids"),
            "physical_rerank_is_empty": snapshot.get("physical_rerank_is_empty"),
            "physical_rerank_is_null": snapshot.get("physical_rerank_is_null"),
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = minimal_chat_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_minimal_chat_through_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "defaults_match": all(observed[key] for key in ("id_present", "name_matches", "llm_inherited")),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_dialog_and_empty_field_verification",
                    "database_count": snapshot.get("count"),
                    "tenant_matches": observed["tenant_matches"],
                    "status": snapshot.get("status"),
                    "physical_rerank_is_empty": snapshot.get("physical_rerank_is_empty"),
                    "physical_rerank_is_null": snapshot.get("physical_rerank_is_null"),
                },
                {
                    "name": "soft_delete_cleanup_through_api",
                    "cleanup_succeeded": cleanup,
                    "final_status": _chat_snapshot(group, chat_id).get("status"),
                },
            ],
            "oracle": {
                "response": [200, 0],
                "defaults": {
                    "dataset_ids": [],
                    "top_n": 6,
                    "top_k": 1024,
                    "similarity_threshold": 0.1,
                    "vector_similarity_weight": 0.3,
                },
                "control_physical_rerank": "empty string",
                "experiment_physical_rerank": "NULL",
            },
        }

    return _run_case(case_id, execute)


def run_cs002() -> dict[str, Any]:
    case_id = "TC-CS-002"
    prefix = "fresh-cs-002"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dataset = _dataset_fixture(case_id, group, owner, f"{prefix}-dataset", with_chunk=True)
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        rerank_id = "BAAI/bge-reranker-v2-m3"
        prompt_config = {
            "system": "You are a fresh RAG assistant with {knowledge}.",
            "prologue": "Fresh chat ready.",
            "parameters": [{"key": "knowledge", "optional": False}],
            "empty_response": "No fresh evidence found.",
            "quote": True,
            "tts": False,
            "refine_multiturn": True,
        }
        llm_setting = {"temperature": 0.5, "top_p": 0.8, "max_tokens": 1024}
        payload = {
            "name": f"{prefix}-full",
            "dataset_ids": [dataset["id"]],
            "llm_id": llm_id,
            "llm_setting": llm_setting,
            "rerank_id": rerank_id,
            "prompt_config": prompt_config,
            "description": "fresh full chat",
            "top_n": 10,
            "top_k": 2048,
            "similarity_threshold": 0.3,
            "vector_similarity_weight": 0.5,
        }
        response = _create_chat(case_id, group, owner["auth"], "create_full_chat", payload)
        data = response["data"] if isinstance(response["data"], dict) else {}
        chat_id = str(data.get("id") or "")
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        chat_cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "database_count": snapshot.get("count"),
            "dataset_ids_match": data.get("dataset_ids") == [dataset["id"]] and snapshot.get("kb_ids") == [dataset["id"]],
            "kb_names_match": data.get("kb_names") == [dataset["name"]],
            "llm_matches": data.get("llm_id") == llm_id and snapshot.get("llm_id") == llm_id,
            "rerank_matches": data.get("rerank_id") == rerank_id and snapshot.get("rerank_id") == rerank_id,
            "llm_setting_matches": data.get("llm_setting") == llm_setting,
            "prompt_config_matches": data.get("prompt_config") == prompt_config,
            "scalar_fields_match": data.get("description") == "fresh full chat"
            and data.get("top_n") == 10
            and data.get("top_k") == 2048
            and data.get("similarity_threshold") == 0.3
            and data.get("vector_similarity_weight") == 0.5,
            "database_json_matches": snapshot.get("llm_setting") == llm_setting and snapshot.get("prompt_config") == prompt_config,
            "cleanup_succeeded": chat_cleanup and dataset_cleanup,
        }
        passed = dataset["ready"] and full_chat_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset_document_and_chunk_through_api",
                    "api_codes": dataset["api_codes"],
                    "fixture_ready": dataset["ready"],
                    "dataset_id_fingerprint": DB._fingerprint(dataset["id"]),
                    "document_id_fingerprint": DB._fingerprint(dataset["document_id"]),
                    "chunk_id_fingerprint": DB._fingerprint(dataset["chunk_id"]),
                },
                {
                    "name": "create_full_chat_through_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "response_fields_match": all(
                        observed[key]
                        for key in (
                            "dataset_ids_match",
                            "kb_names_match",
                            "llm_matches",
                            "rerank_matches",
                            "llm_setting_matches",
                            "prompt_config_matches",
                            "scalar_fields_match",
                        )
                    ),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_dialog_json_verification",
                    "database_count": snapshot.get("count"),
                    "database_json_matches": observed["database_json_matches"],
                },
                {
                    "name": "cleanup_chat_and_dataset_through_api",
                    "chat_cleanup": chat_cleanup,
                    "dataset_cleanup": dataset_cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "dataset_mapping": True,
                "json_roundtrip": True,
                "nonempty_llm_and_rerank": True,
            },
        }

    return _run_case(case_id, execute)


def _simple_rejection_case(
    case_id: str,
    prefix: str,
    payload_factory: Callable[[str, dict[str, str]], dict[str, Any]],
    message_fragment: str,
    *,
    setup: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
    teardown: Callable[[str, dict[str, str], dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        context = setup(group, owner) if setup else {}
        before = _tenant_active_chat_count(group, owner["tenant_id"])
        response = _create_chat(
            case_id,
            group,
            owner["auth"],
            "submit_rejected_chat_create",
            payload_factory(group, owner),
        )
        after = _tenant_active_chat_count(group, owner["tenant_id"])
        context_cleanup = teardown(group, owner, context) if teardown else True
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix) and context_cleanup
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "message": response["message"],
            "database_delta": after - before,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = chat_rejection_contract_ok(observed, expected_code=102, message_fragment=message_fragment) and context.get("ready", True)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "prepare_case_fixture",
                    "preclean": preclean,
                    "fixture_ready": context.get("ready", True),
                    "fixture_summary": context.get("summary", {}),
                },
                {
                    "name": "submit_rejected_chat_create",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_fragment_present": message_fragment in str(response["message"] or ""),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "verify_no_active_dialog_delta_and_cleanup",
                    "active_count_before": before,
                    "active_count_after": after,
                    "database_delta": after - before,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 102],
                "message_contains": message_fragment,
                "database_delta": 0,
            },
        }

    return _run_case(case_id, execute)


def run_cs003() -> dict[str, Any]:
    case_id = "TC-CS-003"
    prefix = "fresh-cs-003"

    def setup(group: str, owner: dict[str, str]) -> dict[str, Any]:
        first = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_duplicate_name_precondition",
            {"name": f"{prefix}-duplicate"},
        )
        data = first["data"] if isinstance(first["data"], dict) else {}
        return {
            "ready": first["code"] == 0 and bool(data.get("id")),
            "summary": {"create_code": first["code"]},
        }

    return _simple_rejection_case(
        case_id,
        prefix,
        lambda _group, _owner: {"name": f"{prefix}-duplicate"},
        "Duplicated chat name in creating chat",
        setup=setup,
    )


def run_cs004() -> dict[str, Any]:
    return _simple_rejection_case(
        "TC-CS-004",
        "fresh-cs-004",
        lambda _group, _owner: {"name": "x" * 256},
        "larger than 255",
    )


def run_cs005() -> dict[str, Any]:
    prefix = "fresh-cs-005"
    return _simple_rejection_case(
        "TC-CS-005",
        prefix,
        lambda _group, _owner: {
            "name": f"{prefix}-tenant-field",
            "tenant_id": "forbidden-tenant-id",
        },
        "must not be provided",
    )


def run_cs006() -> dict[str, Any]:
    prefix = "fresh-cs-006"
    return _simple_rejection_case(
        "TC-CS-006",
        prefix,
        lambda _group, _owner: {
            "name": f"{prefix}-missing-dataset",
            "dataset_ids": ["fresh-nonexistent-dataset-id"],
        },
        "don't own the dataset",
    )


def run_cs007() -> dict[str, Any]:
    case_id = "TC-CS-007"
    prefix = "fresh-cs-007"

    def setup(group: str, owner: dict[str, str]) -> dict[str, Any]:
        dataset = _dataset_fixture(case_id, group, owner, f"{prefix}-empty-dataset", with_chunk=False)
        return {
            "ready": dataset["ready"] and dataset["snapshot"].get("chunk_num") == 0,
            "dataset": dataset,
            "summary": {
                "dataset_code": dataset["api_codes"][0],
                "chunk_num": dataset["snapshot"].get("chunk_num"),
            },
        }

    def payload(_group: str, owner: dict[str, str]) -> dict[str, Any]:
        ids = DD._dataset_ids_by_prefix(_group, owner["tenant_id"], f"{prefix}-empty-dataset")
        return {"name": f"{prefix}-empty", "dataset_ids": ids[:1]}

    def teardown(group: str, owner: dict[str, str], context: dict[str, Any]) -> bool:
        return _cleanup_dataset_fixture(case_id, group, owner, [context.get("dataset", {}).get("id", "")])

    return _simple_rejection_case(
        case_id,
        prefix,
        payload,
        "doesn't own parsed file",
        setup=setup,
        teardown=teardown,
    )


def run_cs008() -> dict[str, Any]:
    case_id = "TC-CS-008"
    prefix = "fresh-cs-008"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        default_embedding = DD._tenant_default_embedding(group, owner["tenant_id"])
        with _embedding_stub() as base_url:
            alt = _create_alt_embedding_instance(case_id, group, owner["auth"], base_url)
            dataset_default = _dataset_fixture(
                case_id,
                group,
                owner,
                f"{prefix}-default",
                with_chunk=True,
                embedding_model=default_embedding,
            )
            dataset_alt = _dataset_fixture(
                case_id,
                group,
                owner,
                f"{prefix}-alt",
                with_chunk=True,
                embedding_model=alt["model_id"],
            )
            before = _tenant_active_chat_count(group, owner["tenant_id"])
            response = _create_chat(
                case_id,
                group,
                owner["auth"],
                "create_chat_with_mixed_embeddings",
                {
                    "name": f"{prefix}-mixed",
                    "dataset_ids": [dataset_default["id"], dataset_alt["id"]],
                },
            )
            after = _tenant_active_chat_count(group, owner["tenant_id"])
            dataset_cleanup = _cleanup_dataset_fixture(
                case_id,
                group,
                owner,
                [dataset_default["id"], dataset_alt["id"]],
            )
            instance_cleanup = _drop_provider_instance(case_id, group, owner["auth"], alt["instance_name"])
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix) and dataset_cleanup and instance_cleanup
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "message": response["message"],
            "database_delta": after - before,
            "cleanup_succeeded": preclean and cleanup,
        }
        fixtures_ready = (
            alt["precleaned"]
            and alt["create_code"] == 0
            and alt["visible"]
            and dataset_default["ready"]
            and dataset_alt["ready"]
            and dataset_default["embedding_model"] != dataset_alt["embedding_model"]
        )
        passed = fixtures_ready and chat_rejection_contract_ok(
            observed,
            expected_code=102,
            message_fragment="different embedding models",
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "register_controlled_alt_embedding_through_provider_api",
                    "create_code": alt["create_code"],
                    "model_visible": alt["visible"],
                    "model_id_fingerprint": DB._fingerprint(alt["model_id"]),
                    "raw_sha256": alt["raw_sha256"],
                },
                {
                    "name": "create_two_chunked_datasets_with_distinct_embeddings",
                    "default_ready": dataset_default["ready"],
                    "alt_ready": dataset_alt["ready"],
                    "embedding_ids_distinct": dataset_default["embedding_model"] != dataset_alt["embedding_model"],
                    "default_api_codes": dataset_default["api_codes"],
                    "alt_api_codes": dataset_alt["api_codes"],
                },
                {
                    "name": "verify_mixed_embedding_chat_rejected",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_fragment_present": "different embedding models" in str(response["message"] or ""),
                    "database_delta": after - before,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "cleanup_datasets_and_model_instance_through_api",
                    "dataset_cleanup": dataset_cleanup,
                    "instance_cleanup": instance_cleanup,
                },
            ],
            "oracle": {
                "response": [200, 102],
                "message_contains": "different embedding models",
                "database_delta": 0,
                "controlled_model_cleanup": True,
            },
        }

    return _run_case(case_id, execute)


def run_cs009() -> dict[str, Any]:
    prefix = "fresh-cs-009"
    return _simple_rejection_case(
        "TC-CS-009",
        prefix,
        lambda _group, _owner: {
            "name": f"{prefix}-invalid-llm",
            "llm_id": "fresh-nonexistent-chat-model",
        },
        "doesn't exist",
    )


def run_cs010() -> dict[str, Any]:
    prefix = "fresh-cs-010"
    return _simple_rejection_case(
        "TC-CS-010",
        prefix,
        lambda _group, _owner: {
            "name": f"{prefix}-invalid-rerank",
            "rerank_id": "fresh-nonexistent-rerank-model",
        },
        "doesn't exist",
    )


def run_cs011() -> dict[str, Any]:
    case_id = "TC-CS-011"
    prefix = "fresh-cs-011"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat_batch(case_id, group, owner, prefix, 3)
        expected_ids = {str(item["data"].get("id")) for item in created if item["code"] == 0 and isinstance(item["data"], dict)}
        listed = _list_chats(case_id, group, owner["auth"], "list_chats_default")
        chats, total = _response_chats(listed)
        target = [item for item in chats if str(item.get("name", "")).startswith(prefix)]
        actual_ids = {str(item.get("id")) for item in target}
        active_total = _tenant_active_chat_count(group, owner["tenant_id"])
        create_times = [int(item.get("create_time") or 0) for item in target]
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        observed = {
            "http_status": listed["http_status"],
            "code": listed["code"],
            "actual_count": len(target),
            "expected_count": 3,
            "total_matches": total == active_total and total >= 3,
            "ids_exact": actual_ids == expected_ids,
            "required_fields_present": all({"id", "name", "dataset_ids", "llm_id", "kb_names"}.issubset(item) for item in target),
            "descending_order": all(left >= right for left, right in zip(create_times, create_times[1:])),
            "pages_disjoint": True,
            "filter_exact": all(str(item.get("name", "")).startswith(prefix) for item in target),
            "cleanup_succeeded": preclean and all(item["code"] == 0 for item in created) and cleanup,
        }
        passed = chat_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_chat_fixtures_through_api",
                    "create_codes": [item["code"] for item in created],
                    "unique_id_count": len(expected_ids),
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "list_chats_with_default_pagination",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "target_count": len(target),
                    "total": total,
                    "database_active_total": active_total,
                    "ids_exact": observed["ids_exact"],
                    "required_fields_present": observed["required_fields_present"],
                    "descending_order": observed["descending_order"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {"name": "soft_delete_cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"minimum_fixture_count": 3, "response_shape": "data.chats+data.total"},
        }

    return _run_case(case_id, execute)


def run_cs012() -> dict[str, Any]:
    case_id = "TC-CS-012"
    prefix = "fresh-cs-012"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat_batch(case_id, group, owner, prefix, 5)
        expected_ids = {str(item["data"].get("id")) for item in created if item["code"] == 0 and isinstance(item["data"], dict)}
        common_params = {
            "page_size": 2,
            "orderby": "create_time",
            "desc": "true",
            "keywords": prefix,
        }
        first = _list_chats(
            case_id,
            group,
            owner["auth"],
            "list_chat_page_1",
            {**common_params, "page": 1},
        )
        second = _list_chats(
            case_id,
            group,
            owner["auth"],
            "list_chat_page_2",
            {**common_params, "page": 2},
        )
        page1, total1 = _response_chats(first)
        page2, total2 = _response_chats(second)
        ids1 = [str(item.get("id")) for item in page1]
        ids2 = [str(item.get("id")) for item in page2]
        times1 = [int(item.get("create_time") or 0) for item in page1]
        times2 = [int(item.get("create_time") or 0) for item in page2]
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        observed = {
            "http_status": first["http_status"] if second["http_status"] == 200 else second["http_status"],
            "code": first["code"] if second["code"] == 0 else second["code"],
            "actual_count": len(page1) + len(page2),
            "expected_count": 4,
            "total_matches": total1 == total2 == 5,
            "ids_exact": set(ids1 + ids2).issubset(expected_ids) and len(set(ids1 + ids2)) == 4,
            "required_fields_present": all({"id", "name", "dataset_ids", "llm_id", "kb_names"}.issubset(item) for item in page1 + page2),
            "descending_order": all(left >= right for values in (times1, times2) for left, right in zip(values, values[1:])),
            "pages_disjoint": set(ids1).isdisjoint(ids2),
            "filter_exact": all(prefix in str(item.get("name", "")) for item in page1 + page2),
            "cleanup_succeeded": preclean and all(item["code"] == 0 for item in created) and cleanup,
        }
        passed = chat_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_five_chat_fixtures_through_api",
                    "create_codes": [item["code"] for item in created],
                    "unique_id_count": len(expected_ids),
                },
                {
                    "name": "query_two_explicit_pages",
                    "page_sizes": [len(page1), len(page2)],
                    "totals": [total1, total2],
                    "pages_disjoint": observed["pages_disjoint"],
                    "descending_order": observed["descending_order"],
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {"name": "soft_delete_cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"page_sizes": [2, 2], "stable_total": 5, "order": "create_time desc"},
        }

    return _run_case(case_id, execute)


def run_cs013() -> dict[str, Any]:
    case_id = "TC-CS-013"
    prefix = "fresh-cs-013"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        names = [f"{prefix}-target-a", f"{prefix}-target-b", f"{prefix}-noise"]
        created = [
            _create_chat(
                case_id,
                group,
                owner["auth"],
                f"create_filter_chat_{index}",
                {"name": name},
            )
            for index, name in enumerate(names, 1)
        ]
        expected_ids = {str(item["data"].get("id")) for item in created[:2] if item["code"] == 0 and isinstance(item["data"], dict)}
        listed = _list_chats(
            case_id,
            group,
            owner["auth"],
            "filter_chats_by_keywords",
            {"keywords": f"{prefix}-target"},
        )
        chats, total = _response_chats(listed)
        actual_ids = {str(item.get("id")) for item in chats}
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        observed = {
            "http_status": listed["http_status"],
            "code": listed["code"],
            "actual_count": len(chats),
            "expected_count": 2,
            "total_matches": total == 2,
            "ids_exact": actual_ids == expected_ids,
            "required_fields_present": all("id" in item and "name" in item for item in chats),
            "descending_order": True,
            "pages_disjoint": True,
            "filter_exact": all(f"{prefix}-target" in str(item.get("name")) for item in chats),
            "cleanup_succeeded": preclean and all(item["code"] == 0 for item in created) and cleanup,
        }
        passed = chat_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_target_and_noise_chats",
                    "create_codes": [item["code"] for item in created],
                },
                {
                    "name": "filter_chat_list_by_keywords",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "total": total,
                    "ids_exact": observed["ids_exact"],
                    "filter_exact": observed["filter_exact"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {"name": "soft_delete_cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"matching_count": 2, "noise_absent": True},
        }

    return _run_case(case_id, execute)


def run_cs014() -> dict[str, Any]:
    case_id = "TC-CS-014"
    prefix = "fresh-cs-014"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        llm_setting = {"temperature": 0.23, "top_p": 0.67}
        prompt_config = {"system": "fresh detail system", "prologue": "fresh detail prologue"}
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_detail",
            {"name": prefix, "llm_setting": llm_setting, "prompt_config": prompt_config},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(created_data.get("id") or "")
        detail = _get_chat(case_id, group, owner["auth"], "get_chat_detail", chat_id)
        data = detail["data"] if isinstance(detail["data"], dict) else {}
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        cleanup = _cleanup_created_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": detail["http_status"],
            "code": detail["code"],
            "id_matches": data.get("id") == chat_id,
            "dataset_mapping_matches": data.get("dataset_ids") == snapshot.get("kb_ids") == [],
            "json_fields_match": data.get("llm_setting") == snapshot.get("llm_setting") and data.get("prompt_config") == snapshot.get("prompt_config"),
            "database_matches": snapshot.get("count") == 1 and snapshot.get("name") == prefix and data.get("name") == prefix,
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = chat_detail_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_with_json_fields",
                    "code": created["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "get_detail_and_compare_read_only_database",
                    "http_status": detail["http_status"],
                    "code": detail["code"],
                    "id_matches": observed["id_matches"],
                    "dataset_mapping_matches": observed["dataset_mapping_matches"],
                    "json_fields_match": observed["json_fields_match"],
                    "database_matches": observed["database_matches"],
                    "raw_sha256": detail["raw_sha256"],
                },
                {"name": "soft_delete_cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "api_database_roundtrip": True},
        }

    return _run_case(case_id, execute)


def run_cs015() -> dict[str, Any]:
    case_id = "TC-CS-015"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        before = _tenant_active_chat_count(group, owner["tenant_id"])
        response = _get_chat(
            case_id,
            group,
            owner["auth"],
            "get_nonexistent_chat",
            "fresh-nonexistent-chat-id",
        )
        after = _tenant_active_chat_count(group, owner["tenant_id"])
        passed = response["http_status"] == 200 and response["code"] == 109 and response["message"] == "No authorization." and response["data"] is False and before == after
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "get_nonexistent_chat",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_matches": response["message"] == "No authorization.",
                    "data_is_false": response["data"] is False,
                    "database_unchanged": before == after,
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            "oracle": {"response": [200, 109], "message": "No authorization.", "side_effects": 0},
        }

    return _run_case(case_id, execute)


def run_cs016() -> dict[str, Any]:
    case_id = "TC-CS-016"
    prefix = "fresh-cs-016"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat_for_soft_delete_query", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(data.get("id") or "")
        sessions_before = _conversation_count(group, chat_id) if chat_id else 0
        deleted = _delete_chat(case_id, group, owner["auth"], "soft_delete_chat", chat_id)
        snapshot = _chat_snapshot(group, chat_id)
        listed = _list_chats(case_id, group, owner["auth"], "list_after_soft_delete", {"keywords": prefix})
        chats, _total = _response_chats(listed)
        detail = _get_chat(case_id, group, owner["auth"], "get_after_soft_delete", chat_id)
        sessions_after = _conversation_count(group, chat_id) if chat_id else 0
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "dialog_count_after": snapshot.get("count"),
            "dialog_status_after": snapshot.get("status"),
            "list_absent": all(str(item.get("id")) != chat_id for item in chats),
            "detail_denied": detail["http_status"] == 200 and detail["code"] == 109,
            "session_count_unchanged": sessions_before == sessions_after,
            "cleanup_succeeded": preclean and created["code"] == 0 and not _active_chat_ids_by_prefix(group, owner["tenant_id"], prefix),
        }
        passed = chat_soft_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_and_soft_delete_chat", "create_code": created["code"], "delete_code": deleted["code"], "raw_sha256": [created["raw_sha256"], deleted["raw_sha256"]]},
                {"name": "read_only_soft_delete_verification", "dialog_count": snapshot.get("count"), "status": snapshot.get("status"), "session_count_unchanged": observed["session_count_unchanged"]},
                {"name": "verify_deleted_chat_absent_and_denied", "list_absent": observed["list_absent"], "detail_code": detail["code"], "raw_sha256": [listed["raw_sha256"], detail["raw_sha256"]]},
            ],
            "oracle": {"dialog_row": 1, "status": "0", "list_absent": True, "detail_code": 109},
        }

    return _run_case(case_id, execute)


def run_cs017() -> dict[str, Any]:
    case_id = "TC-CS-017"
    prefix = "fresh-cs-017"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dataset = _dataset_fixture(case_id, group, owner, f"{prefix}-dataset", with_chunk=True)
        initial_llm_setting = {"temperature": 0.2, "top_p": 0.4}
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_put_update",
            {
                "name": f"{prefix}-before",
                "llm_setting": initial_llm_setting,
                "icon": "fresh-icon",
            },
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(created_data.get("id") or "")
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        payload = {
            "name": f"{prefix}-after",
            "description": "fresh updated description",
            "llm_id": llm_id,
            "rerank_id": "BAAI/bge-reranker-v2-m3",
            "dataset_ids": [dataset["id"]],
            "prompt_config": {
                "system": "fresh updated system {knowledge}",
                "prologue": "fresh updated prologue",
            },
            "similarity_threshold": 0.5,
            "vector_similarity_weight": 0.6,
            "top_n": 8,
            "top_k": 512,
        }
        updated = _put_chat(case_id, group, owner["auth"], "put_update_chat", chat_id, payload)
        data = updated["data"] if isinstance(updated["data"], dict) else {}
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        response_matches = (
            data.get("name") == payload["name"]
            and data.get("description") == payload["description"]
            and data.get("llm_id") == llm_id
            and data.get("rerank_id") == payload["rerank_id"]
            and data.get("dataset_ids") == [dataset["id"]]
            and data.get("prompt_config") == payload["prompt_config"]
            and data.get("top_n") == 8
            and data.get("top_k") == 512
            and data.get("similarity_threshold") == 0.5
            and data.get("vector_similarity_weight") == 0.6
        )
        database_matches = (
            snapshot.get("name") == payload["name"]
            and snapshot.get("description") == payload["description"]
            and snapshot.get("llm_id") == llm_id
            and snapshot.get("rerank_id") == payload["rerank_id"]
            and snapshot.get("kb_ids") == [dataset["id"]]
            and snapshot.get("prompt_config") == payload["prompt_config"]
        )
        unsubmitted_preserved = snapshot.get("llm_setting") == initial_llm_setting
        chat_cleanup = _cleanup_created_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        observed = {
            "http_status": updated["http_status"],
            "code": updated["code"],
            "response_matches": response_matches,
            "database_matches": database_matches,
            "json_merge_matches": data.get("prompt_config") == payload["prompt_config"],
            "unsubmitted_fields_preserved": unsubmitted_preserved,
            "cleanup_succeeded": preclean and created["code"] == 0 and dataset["ready"] and chat_cleanup and dataset_cleanup,
        }
        passed = chat_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_and_chunked_dataset_fixture", "chat_code": created["code"], "dataset_ready": dataset["ready"], "raw_sha256": created["raw_sha256"]},
                {"name": "put_update_requested_chat_fields", "http_status": updated["http_status"], "code": updated["code"], "response_matches": response_matches, "raw_sha256": updated["raw_sha256"]},
                {"name": "read_only_update_and_preservation_verification", "database_matches": database_matches, "unsubmitted_llm_setting_preserved": unsubmitted_preserved},
                {"name": "cleanup_chat_and_dataset_through_api", "chat_cleanup": chat_cleanup, "dataset_cleanup": dataset_cleanup},
            ],
            "oracle": {"specified_fields_updated": True, "unsubmitted_fields_preserved": True},
        }

    return _run_case(case_id, execute)


def _run_patch_merge_case(case_id: str, field: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        if field == "prompt_config":
            initial = {"system": "old system", "prologue": "old prologue", "quote": True}
            patch_value = {"system": "new system"}
        else:
            initial = {"temperature": 0.1, "top_p": 0.3, "max_tokens": 512}
            patch_value = {"temperature": 0.9}
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_patch_merge",
            {"name": prefix, field: initial, "description": "preserve-me"},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(created_data.get("id") or "")
        baseline = created_data.get(field) if isinstance(created_data.get(field), dict) else {}
        expected = dict(baseline)
        expected.update(patch_value)
        patched = _patch_chat(
            case_id,
            group,
            owner["auth"],
            "patch_nested_chat_json",
            chat_id,
            {field: patch_value},
        )
        data = patched["data"] if isinstance(patched["data"], dict) else {}
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        cleanup = _cleanup_created_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        response_matches = data.get(field) == expected
        database_matches = snapshot.get(field) == expected
        observed = {
            "http_status": patched["http_status"],
            "code": patched["code"],
            "response_matches": response_matches,
            "database_matches": database_matches,
            "json_merge_matches": response_matches and database_matches,
            "unsubmitted_fields_preserved": data.get("description") == "preserve-me" and snapshot.get("description") == "preserve-me",
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = chat_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_with_baseline_nested_json", "code": created["code"], "baseline_keys": sorted(baseline), "raw_sha256": created["raw_sha256"]},
                {
                    "name": "patch_single_nested_json_key",
                    "http_status": patched["http_status"],
                    "code": patched["code"],
                    "response_merge_matches": response_matches,
                    "raw_sha256": patched["raw_sha256"],
                },
                {"name": "read_only_json_merge_verification", "database_merge_matches": database_matches, "unsubmitted_description_preserved": observed["unsubmitted_fields_preserved"]},
                {"name": "soft_delete_cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"field": field, "merge_not_replace": True},
        }

    return _run_case(case_id, execute)


def run_cs018() -> dict[str, Any]:
    return _run_patch_merge_case("TC-CS-018", "prompt_config")


def run_cs019() -> dict[str, Any]:
    return _run_patch_merge_case("TC-CS-019", "llm_setting")


def _run_empty_chat_field_case(case_id: str, field: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        create_payload: dict[str, Any] = {"name": prefix}
        if field == "rerank_id":
            create_payload[field] = "BAAI/bge-reranker-v2-m3"
        created = _create_chat(case_id, group, owner["auth"], "create_chat_with_nonempty_field", create_payload)
        data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(data.get("id") or "")
        updated = _put_chat(
            case_id,
            group,
            owner["auth"],
            "clear_chat_model_field",
            chat_id,
            {field: ""},
        )
        detail = _get_chat(case_id, group, owner["auth"], "get_cleared_chat_field", chat_id)
        detail_data = detail["data"] if isinstance(detail["data"], dict) else {}
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        cleanup = _cleanup_created_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": updated["http_status"],
            "code": updated["code"],
            "api_value": detail_data.get(field),
            "orm_value": snapshot.get(field),
            "physical_is_empty": snapshot.get(f"physical_{'llm' if field == 'llm_id' else 'rerank'}_is_empty"),
            "physical_is_null": snapshot.get(f"physical_{'llm' if field == 'llm_id' else 'rerank'}_is_null"),
            "cleanup_succeeded": preclean and created["code"] == 0 and detail["code"] == 0 and cleanup,
        }
        passed = empty_chat_field_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_with_nonempty_model_field", "field": field, "create_code": created["code"], "initial_nonempty": bool(data.get(field)), "raw_sha256": created["raw_sha256"]},
                {"name": "clear_model_field_through_put", "http_status": updated["http_status"], "code": updated["code"], "raw_sha256": updated["raw_sha256"]},
                {
                    "name": "verify_api_orm_and_physical_storage",
                    "api_value_is_empty": detail_data.get(field) == "",
                    "orm_value_is_empty": snapshot.get(field) == "",
                    "physical_is_empty": observed["physical_is_empty"],
                    "physical_is_null": observed["physical_is_null"],
                    "raw_sha256": detail["raw_sha256"],
                },
                {"name": "soft_delete_cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"control_physical": "empty string", "experiment_physical": "NULL", "api": "empty string"},
        }

    return _run_case(case_id, execute)


def run_cs020() -> dict[str, Any]:
    return _run_empty_chat_field_case("TC-CS-020", "llm_id")


def run_cs021() -> dict[str, Any]:
    return _run_empty_chat_field_case("TC-CS-021", "rerank_id")


def run_cs022() -> dict[str, Any]:
    case_id = "TC-CS-022"
    prefix = "fresh-cs-022"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat_batch(case_id, group, owner, prefix, 2)
        data_a = created[0]["data"] if isinstance(created[0]["data"], dict) else {}
        data_b = created[1]["data"] if isinstance(created[1]["data"], dict) else {}
        id_a, id_b = str(data_a.get("id") or ""), str(data_b.get("id") or "")
        before = _tenant_active_chat_count(group, owner["tenant_id"])
        response = _put_chat(
            case_id,
            group,
            owner["auth"],
            "update_chat_to_duplicate_name",
            id_b,
            {"name": data_a.get("name")},
        )
        after = _tenant_active_chat_count(group, owner["tenant_id"])
        snapshot_b = _chat_snapshot(group, id_b)
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        passed = (
            preclean
            and all(item["code"] == 0 for item in created)
            and response["http_status"] == 200
            and response["code"] == 102
            and response["message"] == "Duplicated chat name."
            and before == after
            and snapshot_b.get("name") == data_b.get("name")
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_two_distinct_chats", "create_codes": [item["code"] for item in created], "ids_distinct": id_a != id_b},
                {
                    "name": "attempt_duplicate_name_update",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_matches": response["message"] == "Duplicated chat name.",
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "verify_no_database_mutation_and_cleanup",
                    "active_count_unchanged": before == after,
                    "original_name_preserved": snapshot_b.get("name") == data_b.get("name"),
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {"response": [200, 102], "message": "Duplicated chat name.", "mutation": 0},
        }

    return _run_case(case_id, execute)


def _prepare_secondary_user(case_id: str, group: str, email: str, password: str) -> dict[str, Any]:
    preclean = True
    if DB._email_count(group, [email]):
        preclean = DB._delete_user_via_admin(group, email)
    registration = DB._register(
        case_id,
        group,
        "register_secondary_chat_user",
        {"email": email, "nickname": "FreshCSSecondary", "password": password},
    )
    login = AUTH._login(case_id, group, "login_secondary_chat_user", email, password)
    return {
        "preclean": preclean,
        "registration": registration,
        "login": login,
        "auth": str(login.get("_auth") or ""),
        "tenant_id": DD._owner_id(group, email) if DB._email_count(group, [email]) else "",
    }


def _cleanup_secondary_user(case_id: str, group: str, email: str) -> dict[str, Any]:
    if not DB._email_count(group, [email]):
        return {"succeeded": True, "disabled_code": None, "deleted_code": None}
    result = DB._disable_and_delete_user(case_id, group, email)
    return {
        "succeeded": result["disabled"]["code"] == 0 and result["deleted"]["code"] == 0 and DB._email_count(group, [email]) == 0,
        "disabled_code": result["disabled"]["code"],
        "deleted_code": result["deleted"]["code"],
        "raw_sha256": [
            result["disabled"]["raw_sha256"],
            result["deleted"]["raw_sha256"],
        ],
    }


def run_cs023() -> dict[str, Any]:
    case_id = "TC-CS-023"
    prefix = "fresh-cs-023"
    email = "cs-023-user-b@fresh.invalid"
    password = "Fresh-CS-023-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        owner_clean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = _prepare_secondary_user(case_id, group, email, password)
        created = _create_chat(case_id, group, owner["auth"], "create_user_a_chat", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(data.get("id") or "")
        before = _chat_snapshot(group, chat_id)
        denied = _put_chat(
            case_id,
            group,
            secondary["auth"],
            "user_b_attempt_update_user_a_chat",
            chat_id,
            {"name": f"{prefix}-tampered"},
        )
        after = _chat_snapshot(group, chat_id)
        chat_cleanup = _cleanup_created_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        user_cleanup = _cleanup_secondary_user(case_id, group, email)
        passed = (
            owner_clean
            and secondary["preclean"]
            and secondary["registration"]["code"] == 0
            and secondary["login"]["code"] == 0
            and secondary["tenant_id"] != owner["tenant_id"]
            and created["code"] == 0
            and denied["http_status"] == 200
            and denied["code"] == 109
            and denied["message"] == "No authorization."
            and before.get("name") == after.get("name") == prefix
            and chat_cleanup
            and user_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_distinct_user_b_and_user_a_chat",
                    "registration_code": secondary["registration"]["code"],
                    "secondary_login_code": secondary["login"]["code"],
                    "tenant_ids_distinct": secondary["tenant_id"] != owner["tenant_id"],
                    "chat_create_code": created["code"],
                },
                {
                    "name": "deny_cross_tenant_chat_update",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "message_matches": denied["message"] == "No authorization.",
                    "chat_name_unchanged": before.get("name") == after.get("name"),
                    "raw_sha256": denied["raw_sha256"],
                },
                {"name": "cleanup_chat_and_secondary_user_through_apis", "chat_cleanup": chat_cleanup, "user_cleanup": user_cleanup["succeeded"]},
            ],
            "oracle": {"response": [200, 109], "message": "No authorization.", "mutation": 0},
        }

    return _run_case(case_id, execute)


def run_cs024() -> dict[str, Any]:
    case_id = "TC-CS-024"
    prefix = "fresh-cs-024"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_with_session_for_soft_delete",
            {"name": prefix, "prompt_config": {"prologue": "fresh retained session"}},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session_before_chat_delete", chat_id, {"name": "fresh retained session"})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        count_before = _conversation_count(group, chat_id, session_id) if session_id else 0
        deleted = _delete_chat(case_id, group, owner["auth"], "soft_delete_chat_with_session", chat_id)
        snapshot = _chat_snapshot(group, chat_id)
        count_after = _conversation_count(group, chat_id, session_id) if session_id else 0
        listed = _list_chats(case_id, group, owner["auth"], "list_after_chat_soft_delete", {"keywords": prefix})
        chats, _total = _response_chats(listed)
        detail = _get_chat(case_id, group, owner["auth"], "get_soft_deleted_chat", chat_id)
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "dialog_count_after": snapshot.get("count"),
            "dialog_status_after": snapshot.get("status"),
            "list_absent": all(str(item.get("id")) != chat_id for item in chats),
            "detail_denied": detail["http_status"] == 200 and detail["code"] == 109,
            "session_count_unchanged": count_before == count_after == 1,
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and not _active_chat_ids_by_prefix(group, owner["tenant_id"], prefix),
        }
        passed = chat_soft_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_and_session_through_api",
                    "chat_code": created["code"],
                    "session_code": session["code"],
                    "session_row_before": count_before,
                    "raw_sha256": [created["raw_sha256"], session["raw_sha256"]],
                },
                {"name": "soft_delete_chat_through_api", "http_status": deleted["http_status"], "code": deleted["code"], "raw_sha256": deleted["raw_sha256"]},
                {
                    "name": "verify_dialog_soft_deleted_and_session_retained",
                    "dialog_count": snapshot.get("count"),
                    "dialog_status": snapshot.get("status"),
                    "session_row_after": count_after,
                    "list_absent": observed["list_absent"],
                    "detail_code": detail["code"],
                },
                {
                    "name": "record_product_defined_retained_session_cleanup_state",
                    "active_chat_absent": observed["cleanup_succeeded"],
                    "session_intentionally_retained": count_after == 1,
                    "sql_cleanup_used": False,
                },
            ],
            "oracle": {"dialog_status": "0", "conversation_status_unchanged": True, "physical_session_retained": True},
        }

    return _run_case(case_id, execute)


def run_cs025() -> dict[str, Any]:
    case_id = "TC-CS-025"
    prefix = "fresh-cs-025"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat_batch(case_id, group, owner, prefix, 3)
        ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in created]
        deleted = _bulk_delete_chats(case_id, group, owner["auth"], "bulk_delete_two_chats", {"ids": ids[:2]})
        rows = {row["id"]: row for row in _dialog_rows(group, ids)}
        cleanup = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        passed = (
            preclean
            and all(item["code"] == 0 for item in created)
            and len(set(ids)) == 3
            and deleted["http_status"] == 200
            and deleted["code"] == 0
            and data.get("success_count") == 2
            and all(rows.get(item, {}).get("status") == "0" for item in ids[:2])
            and rows.get(ids[2], {}).get("status") == "1"
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_three_chat_fixtures", "create_codes": [item["code"] for item in created], "unique_id_count": len(set(ids))},
                {
                    "name": "bulk_soft_delete_two_chat_ids",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "success_count": data.get("success_count"),
                    "raw_sha256": deleted["raw_sha256"],
                },
                {"name": "read_only_status_scope_verification", "deleted_statuses": [rows.get(item, {}).get("status") for item in ids[:2]], "untargeted_status": rows.get(ids[2], {}).get("status")},
                {"name": "cleanup_remaining_active_chat_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"target_status": "0", "untargeted_status": "1", "success_count": 2},
        }

    return _run_case(case_id, execute)


def run_cs026() -> dict[str, Any]:
    case_id = "TC-CS-026"
    prefix = "fresh-cs-026"
    email = "cs-026-dedicated@fresh.invalid"
    password = "Fresh-CS-026-Dedicated@1234"

    def execute(group: str, _owner_a: dict[str, str]) -> dict[str, Any]:
        secondary = _prepare_secondary_user(case_id, group, email, password)
        owner = {"auth": secondary["auth"], "tenant_id": secondary["tenant_id"]}
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix) if owner["auth"] and owner["tenant_id"] else False
        created = _create_chat_batch(case_id, group, owner, prefix, 3)
        ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in created]
        deleted = _bulk_delete_chats(case_id, group, owner["auth"], "delete_all_dedicated_tenant_chats", {"delete_all": True})
        rows = _dialog_rows(group, ids)
        remaining = _tenant_active_chat_count(group, owner["tenant_id"])
        user_cleanup = _cleanup_secondary_user(case_id, group, email)
        data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        passed = (
            secondary["preclean"]
            and secondary["registration"]["code"] == 0
            and secondary["login"]["code"] == 0
            and preclean
            and all(item["code"] == 0 for item in created)
            and len(set(ids)) == 3
            and deleted["http_status"] == 200
            and deleted["code"] == 0
            and data.get("success_count") == 3
            and len(rows) == 3
            and all(row["status"] == "0" for row in rows)
            and remaining == 0
            and user_cleanup["succeeded"]
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dedicated_tenant_and_three_chats",
                    "registration_code": secondary["registration"]["code"],
                    "login_code": secondary["login"]["code"],
                    "create_codes": [item["code"] for item in created],
                    "unique_id_count": len(set(ids)),
                },
                {
                    "name": "delete_all_dedicated_tenant_chats",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "success_count": data.get("success_count"),
                    "raw_sha256": deleted["raw_sha256"],
                },
                {"name": "read_only_all_status_verification", "row_count": len(rows), "all_status_zero": all(row["status"] == "0" for row in rows), "remaining_active_count": remaining},
                {
                    "name": "cleanup_dedicated_user_through_admin_api",
                    "cleanup_succeeded": user_cleanup["succeeded"],
                    "disabled_code": user_cleanup.get("disabled_code"),
                    "deleted_code": user_cleanup.get("deleted_code"),
                },
            ],
            "oracle": {"dedicated_fixture_only": True, "success_count": 3, "remaining_active": 0},
        }

    return _run_case(case_id, execute)


def run_cs027() -> dict[str, Any]:
    case_id = "TC-CS-027"
    prefix = "fresh-cs-027"
    prologue = "你好，有什么可以帮助？"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_with_prologue",
            {"name": prefix, "prompt_config": {"prologue": prologue}},
        )
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_minimal_session", chat_id, {})
        data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(data.get("id") or "")
        snapshot = _session_snapshot(group, session_id)
        api_messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        expected_messages = [{"role": "assistant", "content": prologue}]
        cleanup = _cleanup_created_session_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        observed = {
            "http_status": session["http_status"],
            "code": session["code"],
            "id_present": bool(session_id),
            "chat_id_matches": str(data.get("chat_id") or "") == chat_id,
            "name": data.get("name"),
            "database_count": snapshot.get("count", 0),
            "database_matches": snapshot.get("chat_id") == chat_id and snapshot.get("name") == "New session" and snapshot.get("messages") == expected_messages,
            "prologue_matches": api_messages == expected_messages,
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = session_create_contract_ok(observed, expected_name="New session")
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_with_exact_prologue", "http_status": created["http_status"], "code": created["code"], "raw_sha256": created["raw_sha256"]},
                {"name": "create_session_with_empty_body", "http_status": session["http_status"], "code": session["code"], "id_present": bool(session_id), "raw_sha256": session["raw_sha256"]},
                {
                    "name": "read_only_session_json_verification",
                    "database_count": snapshot.get("count", 0),
                    "default_name_matches": snapshot.get("name") == "New session",
                    "prologue_matches": observed["prologue_matches"] and snapshot.get("messages") == expected_messages,
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "default_name": "New session", "prologue_persisted": True, "physical_session_cleanup": True},
        }

    return _run_case(case_id, execute)


def run_cs028() -> dict[str, Any]:
    case_id = "TC-CS-028"
    prefix = "fresh-cs-028"
    expected_name = "自定义会话名"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(
            case_id,
            group,
            owner["auth"],
            "create_named_session",
            chat_id,
            {"name": expected_name},
        )
        data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(data.get("id") or "")
        snapshot = _session_snapshot(group, session_id)
        api_messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        database_messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": session["http_status"],
            "code": session["code"],
            "id_present": bool(session_id),
            "chat_id_matches": str(data.get("chat_id") or "") == chat_id,
            "name": data.get("name"),
            "database_count": snapshot.get("count", 0),
            "database_matches": snapshot.get("chat_id") == chat_id and snapshot.get("name") == expected_name and database_messages == api_messages,
            "prologue_matches": bool(api_messages) and api_messages == database_messages and api_messages[0].get("role") == "assistant",
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = session_create_contract_ok(observed, expected_name=expected_name)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_and_unicode_named_session",
                    "chat_code": created["code"],
                    "session_http_status": session["http_status"],
                    "session_code": session["code"],
                    "raw_sha256": [created["raw_sha256"], session["raw_sha256"]],
                },
                {
                    "name": "read_only_custom_name_verification",
                    "database_count": snapshot.get("count", 0),
                    "api_name_matches": data.get("name") == expected_name,
                    "database_name_matches": snapshot.get("name") == expected_name,
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "unicode_name_roundtrip": True, "physical_session_cleanup": True},
        }

    return _run_case(case_id, execute)


def run_cs029() -> dict[str, Any]:
    case_id = "TC-CS-029"
    prefix = "fresh-cs-029"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        sessions = [
            _create_session(
                case_id,
                group,
                owner["auth"],
                f"create_session_{index}",
                chat_id,
                {"name": f"fresh session {index}"},
            )
            for index in range(1, 4)
        ]
        rows = _session_rows(group, chat_id)
        listed = _list_sessions(
            case_id,
            group,
            owner["auth"],
            "list_sessions_page_one",
            chat_id,
            {"page": 1, "page_size": 2, "orderby": "create_time", "desc": "true"},
        )
        data = listed["data"] if isinstance(listed["data"], list) else []
        actual_ids = [str(item.get("id") or "") for item in data if isinstance(item, dict)]
        expected_ids = [row["id"] for row in rows[:2]]
        create_times = [item.get("create_time") for item in data if isinstance(item, dict)]
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": listed["http_status"],
            "code": listed["code"],
            "actual_count": len(data),
            "expected_count": 2,
            "ids_exact": actual_ids == expected_ids,
            "chat_id_mapping_matches": len(data) == 2 and all(isinstance(item, dict) and item.get("chat_id") == chat_id and "dialog_id" not in item for item in data),
            "descending_order": len(create_times) == 2 and all(isinstance(value, int) for value in create_times) and create_times == sorted(create_times, reverse=True),
            "database_count": len(rows),
            "cleanup_succeeded": preclean and created["code"] == 0 and all(item["code"] == 0 for item in sessions) and cleanup,
        }
        passed = session_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_and_three_sessions", "chat_code": created["code"], "session_codes": [item["code"] for item in sessions], "database_count": len(rows)},
                {
                    "name": "list_first_page_ordered_by_create_time_desc",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "returned_count": len(data),
                    "ids_exact": observed["ids_exact"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {"name": "verify_chat_id_mapping_and_order", "chat_id_mapping_matches": observed["chat_id_mapping_matches"], "descending_order": observed["descending_order"]},
                {"name": "cleanup_sessions_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "page_size": 2, "database_session_count": 3, "ordering": "create_time desc"},
        }

    return _run_case(case_id, execute)


def run_cs030() -> dict[str, Any]:
    case_id = "TC-CS-030"
    prefix = "fresh-cs-030-chat"
    dataset_prefix = "fresh-cs-030-dataset"
    icon = "fresh-cs-030-icon"
    message_id = "fresh-cs-030-message"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dataset = _dataset_fixture(case_id, group, owner, dataset_prefix, with_chunk=True)
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_retrieval_chat",
            {
                "name": prefix,
                "dataset_ids": [dataset["id"]],
                "icon": icon,
                "similarity_threshold": 0.0,
                "vector_similarity_weight": 0.3,
                "prompt_config": {"prologue": "fresh session detail prologue"},
            },
        )
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "complete_with_retrieval_reference",
            chat_id,
            session_id,
            [{"role": "user", "content": "what is this fresh chat fixture", "id": message_id}],
        )
        detail = _get_session(case_id, group, owner["auth"], "get_session_detail", chat_id, session_id)
        data = detail["data"] if isinstance(detail["data"], dict) else {}
        snapshot = _session_snapshot(group, session_id)
        messages = data.get("messages") if isinstance(data.get("messages"), list) else []
        references = data.get("reference") if isinstance(data.get("reference"), list) else []
        database_references = snapshot.get("reference") if isinstance(snapshot.get("reference"), list) else []
        formatted_references = bool(references) and all(isinstance(reference, dict) and isinstance(reference.get("chunks"), list) for reference in references)
        referenced_chunk_count = sum(len(reference.get("chunks", [])) for reference in references if isinstance(reference, dict))
        chat_cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        observed = {
            "http_status": detail["http_status"],
            "code": detail["code"],
            "id_matches": str(data.get("id") or "") == session_id,
            "message_fields_present": bool(messages) and all(isinstance(message, dict) and {"role", "content", "id"}.issubset(message) for message in messages),
            "messages_match_database": messages == snapshot.get("messages"),
            "reference_matches_database": references == database_references,
            "chunks_format_present": formatted_references and referenced_chunk_count > 0,
            "avatar_matches": data.get("avatar") == icon,
            "cleanup_succeeded": preclean and dataset["ready"] and created["code"] == 0 and session["code"] == 0 and completion["code"] == 0 and chat_cleanup and dataset_cleanup,
        }
        passed = session_detail_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chunked_dataset_chat_and_session", "dataset_ready": dataset["ready"], "chat_code": created["code"], "session_code": session["code"]},
                {"name": "append_dialogue_and_reference_through_completion_api", "http_status": completion["http_status"], "code": completion["code"], "raw_sha256": completion["raw_sha256"]},
                {
                    "name": "get_session_detail",
                    "http_status": detail["http_status"],
                    "code": detail["code"],
                    "message_count": len(messages),
                    "reference_count": len(references),
                    "referenced_chunk_count": referenced_chunk_count,
                    "raw_sha256": detail["raw_sha256"],
                },
                {
                    "name": "read_only_json_and_avatar_verification",
                    "message_fields_present": observed["message_fields_present"],
                    "messages_match_database": observed["messages_match_database"],
                    "reference_matches_database": observed["reference_matches_database"],
                    "chunks_format_present": observed["chunks_format_present"],
                    "avatar_matches": observed["avatar_matches"],
                },
                {"name": "cleanup_session_chat_and_dataset_through_apis", "chat_cleanup": chat_cleanup, "dataset_cleanup": dataset_cleanup},
            ],
            "oracle": {"response": [200, 0], "message_fields": ["role", "content", "id"], "reference_format": "chunks_format", "avatar_source": "dialog.icon"},
        }

    return _run_case(case_id, execute)


def run_cs031() -> dict[str, Any]:
    case_id = "TC-CS-031"
    prefix = "fresh-cs-031"
    expected_name = "新会话名"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {"name": "before update"})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        renamed = _patch_session(case_id, group, owner["auth"], "update_session_name", chat_id, session_id, {"name": expected_name})
        after_name = _session_snapshot(group, session_id)
        reject_messages = _patch_session(
            case_id,
            group,
            owner["auth"],
            "reject_messages_update",
            chat_id,
            session_id,
            {"messages": [{"role": "user", "content": "注入"}]},
        )
        reject_reference = _patch_session(
            case_id,
            group,
            owner["auth"],
            "reject_reference_update",
            chat_id,
            session_id,
            {"reference": [{"id": "fake"}]},
        )
        final_snapshot = _session_snapshot(group, session_id)
        renamed_data = renamed["data"] if isinstance(renamed["data"], dict) else {}
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "name_http_status": renamed["http_status"],
            "name_code": renamed["code"],
            "name_response_matches": renamed_data.get("name") == expected_name,
            "name_database_matches": after_name.get("name") == expected_name,
            "messages_http_status": reject_messages["http_status"],
            "messages_code": reject_messages["code"],
            "reference_http_status": reject_reference["http_status"],
            "reference_code": reject_reference["code"],
            "messages_unchanged": final_snapshot.get("messages") == after_name.get("messages"),
            "reference_unchanged": final_snapshot.get("reference") == after_name.get("reference"),
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and cleanup,
        }
        passed = session_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_session_fixture", "chat_code": created["code"], "session_code": session["code"]},
                {
                    "name": "update_name_only",
                    "http_status": renamed["http_status"],
                    "code": renamed["code"],
                    "response_matches": observed["name_response_matches"],
                    "database_matches": observed["name_database_matches"],
                    "raw_sha256": renamed["raw_sha256"],
                },
                {
                    "name": "reject_messages_mutation",
                    "http_status": reject_messages["http_status"],
                    "code": reject_messages["code"],
                    "unchanged": observed["messages_unchanged"],
                    "raw_sha256": reject_messages["raw_sha256"],
                },
                {
                    "name": "reject_reference_mutation",
                    "http_status": reject_reference["http_status"],
                    "code": reject_reference["code"],
                    "unchanged": observed["reference_unchanged"],
                    "raw_sha256": reject_reference["raw_sha256"],
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"name_update": [200, 0], "messages_update": [200, 102], "reference_update": [200, 102], "json_mutation": 0},
        }

    return _run_case(case_id, execute)


def run_cs032() -> dict[str, Any]:
    case_id = "TC-CS-032"
    prefix = "fresh-cs-032"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        sessions = [_create_session(case_id, group, owner["auth"], f"create_session_{index}", chat_id, {"name": f"session {index}"}) for index in range(1, 4)]
        session_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in sessions]
        count_before = _conversation_count(group, chat_id)
        deleted = _delete_sessions(case_id, group, owner["auth"], "delete_two_sessions", chat_id, {"ids": session_ids[:2]})
        count_after = _conversation_count(group, chat_id)
        target_counts = [_session_snapshot(group, session_id).get("count", 0) for session_id in session_ids[:2]]
        remaining_count = _session_snapshot(group, session_ids[2]).get("count", 0) if len(session_ids) == 3 else 0
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "deleted_count": count_before - count_after,
            "expected_deleted_count": 2,
            "remaining_count": count_after,
            "expected_remaining_count": 1,
            "targets_physically_absent": target_counts == [0, 0] and remaining_count == 1,
            "cleanup_succeeded": preclean and created["code"] == 0 and all(item["code"] == 0 for item in sessions) and cleanup,
        }
        passed = session_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_session_fixtures",
                    "chat_code": created["code"],
                    "session_codes": [item["code"] for item in sessions],
                    "database_count_before": count_before,
                    "uploaded_blob_count": 0,
                },
                {"name": "delete_two_session_ids_through_api", "http_status": deleted["http_status"], "code": deleted["code"], "raw_sha256": deleted["raw_sha256"]},
                {
                    "name": "read_only_physical_removal_verification",
                    "deleted_count": count_before - count_after,
                    "remaining_count": count_after,
                    "target_counts": target_counts,
                    "untargeted_count": remaining_count,
                    "blob_cleanup_applicable": False,
                },
                {"name": "cleanup_remaining_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "delete_semantics": "physical", "target_count": 0, "untargeted_count": 1},
        }

    return _run_case(case_id, execute)


def run_cs033() -> dict[str, Any]:
    case_id = "TC-CS-033"
    prefix = "fresh-cs-033"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        sessions = [_create_session(case_id, group, owner["auth"], f"create_session_{index}", chat_id, {"name": f"session {index}"}) for index in range(1, 4)]
        session_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in sessions]
        count_before = _conversation_count(group, chat_id)
        deleted = _delete_sessions(case_id, group, owner["auth"], "delete_all_sessions", chat_id, {"delete_all": True})
        count_after = _conversation_count(group, chat_id)
        target_counts = [_session_snapshot(group, session_id).get("count", 0) for session_id in session_ids]
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "deleted_count": count_before - count_after,
            "expected_deleted_count": 3,
            "remaining_count": count_after,
            "expected_remaining_count": 0,
            "targets_physically_absent": target_counts == [0, 0, 0],
            "cleanup_succeeded": preclean and created["code"] == 0 and all(item["code"] == 0 for item in sessions) and cleanup,
        }
        passed = session_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_three_session_fixtures", "chat_code": created["code"], "session_codes": [item["code"] for item in sessions], "database_count_before": count_before},
                {"name": "delete_all_sessions_through_api", "http_status": deleted["http_status"], "code": deleted["code"], "raw_sha256": deleted["raw_sha256"]},
                {"name": "read_only_all_physical_removal_verification", "deleted_count": count_before - count_after, "remaining_count": count_after, "target_counts": target_counts},
                {"name": "cleanup_chat_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "delete_all": True, "remaining_session_count": 0},
        }

    return _run_case(case_id, execute)


def run_cs034() -> dict[str, Any]:
    case_id = "TC-CS-034"
    prefix = "fresh-cs-034"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        count_before = _conversation_count(group, chat_id)
        missing = _get_session(
            case_id,
            group,
            owner["auth"],
            "get_nonexistent_session",
            chat_id,
            "nonexistent-session-id",
        )
        count_after = _conversation_count(group, chat_id)
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        passed = (
            preclean
            and created["code"] == 0
            and missing["http_status"] == 200
            and missing["code"] == 102
            and "Session not found" in str(missing["message"] or "")
            and count_before == count_after == 0
            and cleanup
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_empty_chat_fixture", "http_status": created["http_status"], "code": created["code"]},
                {
                    "name": "get_nonexistent_session",
                    "http_status": missing["http_status"],
                    "code": missing["code"],
                    "message_matches": "Session not found" in str(missing["message"] or ""),
                    "raw_sha256": missing["raw_sha256"],
                },
                {"name": "read_only_no_mutation_verification", "database_count_before": count_before, "database_count_after": count_after},
                {"name": "cleanup_chat_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 102], "message_contains": "Session not found", "database_delta": 0},
        }

    return _run_case(case_id, execute)


def run_cs035() -> dict[str, Any]:
    case_id = "TC-CS-035"
    prefix = "fresh-cs-035"
    target_id = "fresh-cs-035-pair-one"
    remaining_id = "fresh-cs-035-pair-two"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "create_two_message_pairs",
            chat_id,
            session_id,
            [
                {"role": "user", "content": "first fresh question", "id": target_id},
                {"role": "assistant", "content": "first fresh baseline answer", "id": target_id},
                {"role": "user", "content": "second fresh question", "id": remaining_id},
            ],
            pass_all_history_messages=True,
        )
        before = _session_snapshot(group, session_id)
        before_messages = before.get("messages") if isinstance(before.get("messages"), list) else []
        before_references = before.get("reference") if isinstance(before.get("reference"), list) else []
        deleted = _delete_session_message(
            case_id,
            group,
            owner["auth"],
            "delete_first_message_pair",
            chat_id,
            session_id,
            target_id,
        )
        after = _session_snapshot(group, session_id)
        after_messages = after.get("messages") if isinstance(after.get("messages"), list) else []
        after_references = after.get("reference") if isinstance(after.get("reference"), list) else []
        remaining_roles = [message.get("role") for message in after_messages if isinstance(message, dict) and message.get("id") == remaining_id]
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "target_pair_absent": all(not isinstance(message, dict) or message.get("id") != target_id for message in after_messages),
            "message_count_delta": len(before_messages) - len(after_messages),
            "reference_count_delta": len(before_references) - len(after_references),
            "untargeted_pair_present": remaining_roles == ["user", "assistant"],
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and completion["code"] == 0 and cleanup,
        }
        passed = message_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_message_pairs_through_completion_api",
                    "http_status": completion["http_status"],
                    "code": completion["code"],
                    "message_count": len(before_messages),
                    "reference_count": len(before_references),
                    "raw_sha256": completion["raw_sha256"],
                },
                {"name": "delete_target_message_pair", "http_status": deleted["http_status"], "code": deleted["code"], "raw_sha256": deleted["raw_sha256"]},
                {
                    "name": "read_only_message_and_reference_index_verification",
                    "target_pair_absent": observed["target_pair_absent"],
                    "message_count_delta": observed["message_count_delta"],
                    "reference_count_delta": observed["reference_count_delta"],
                    "untargeted_pair_present": observed["untargeted_pair_present"],
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "removed_messages": 2, "removed_references": 1, "untargeted_pair_preserved": True},
        }

    return _run_case(case_id, execute)


def run_cs036() -> dict[str, Any]:
    case_id = "TC-CS-036"
    prefix = "fresh-cs-036"
    message_id = "fresh-cs-036-message"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "create_assistant_message",
            chat_id,
            session_id,
            [{"role": "user", "content": "give a short fresh answer", "id": message_id}],
        )
        feedback = _feedback_message(
            case_id,
            group,
            owner["auth"],
            "thumb_up_message",
            chat_id,
            session_id,
            message_id,
            {"thumbup": True, "feedback": "回答很有帮助"},
        )
        snapshot = _session_snapshot(group, session_id)
        messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        assistant = next((message for message in messages if isinstance(message, dict) and message.get("id") == message_id and message.get("role") == "assistant"), {})
        references = snapshot.get("reference") if isinstance(snapshot.get("reference"), list) else []
        referenced_chunk_count = sum(len(reference.get("chunks", [])) for reference in references if isinstance(reference, dict) and isinstance(reference.get("chunks"), list))
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": feedback["http_status"],
            "code": feedback["code"],
            "assistant_found": bool(assistant),
            "thumbup_matches": assistant.get("thumbup") is True,
            "feedback_matches": "feedback" not in assistant,
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and completion["code"] == 0 and cleanup,
        }
        passed = feedback_contract_ok(observed, expected_code=0)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_assistant_message_through_completion_api", "http_status": completion["http_status"], "code": completion["code"], "raw_sha256": completion["raw_sha256"]},
                {"name": "submit_positive_feedback", "http_status": feedback["http_status"], "code": feedback["code"], "raw_sha256": feedback["raw_sha256"]},
                {
                    "name": "read_only_positive_feedback_verification",
                    "assistant_found": bool(assistant),
                    "thumbup_true": assistant.get("thumbup") is True,
                    "positive_feedback_text_removed_by_contract": "feedback" not in assistant,
                    "referenced_chunk_count": referenced_chunk_count,
                    "chunk_feedback_applicable": referenced_chunk_count > 0,
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "thumbup": True, "chunk_feedback_condition": "only when references exist"},
        }

    return _run_case(case_id, execute)


def run_cs037() -> dict[str, Any]:
    case_id = "TC-CS-037"
    prefix = "fresh-cs-037"
    message_id = "fresh-cs-037-message"
    feedback_text = "回答不准确"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "create_assistant_message",
            chat_id,
            session_id,
            [{"role": "user", "content": "give another short fresh answer", "id": message_id}],
        )
        initial_up = _feedback_message(case_id, group, owner["auth"], "establish_prior_thumb_up", chat_id, session_id, message_id, {"thumbup": True})
        feedback = _feedback_message(
            case_id,
            group,
            owner["auth"],
            "change_to_thumb_down",
            chat_id,
            session_id,
            message_id,
            {"thumbup": False, "feedback": feedback_text},
        )
        snapshot = _session_snapshot(group, session_id)
        messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        assistant = next((message for message in messages if isinstance(message, dict) and message.get("id") == message_id and message.get("role") == "assistant"), {})
        references = snapshot.get("reference") if isinstance(snapshot.get("reference"), list) else []
        referenced_chunk_count = sum(len(reference.get("chunks", [])) for reference in references if isinstance(reference, dict) and isinstance(reference.get("chunks"), list))
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": feedback["http_status"],
            "code": feedback["code"],
            "assistant_found": bool(assistant),
            "thumbup_matches": assistant.get("thumbup") is False,
            "feedback_matches": assistant.get("feedback") == feedback_text,
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and completion["code"] == 0 and initial_up["code"] == 0 and cleanup,
        }
        passed = feedback_contract_ok(observed, expected_code=0)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_assistant_message_through_completion_api", "http_status": completion["http_status"], "code": completion["code"], "raw_sha256": completion["raw_sha256"]},
                {
                    "name": "establish_positive_then_submit_negative_feedback",
                    "initial_up_code": initial_up["code"],
                    "down_http_status": feedback["http_status"],
                    "down_code": feedback["code"],
                    "raw_sha256": [initial_up["raw_sha256"], feedback["raw_sha256"]],
                },
                {
                    "name": "read_only_negative_feedback_verification",
                    "assistant_found": bool(assistant),
                    "thumbup_false": assistant.get("thumbup") is False,
                    "feedback_text_matches": assistant.get("feedback") == feedback_text,
                    "referenced_chunk_count": referenced_chunk_count,
                    "direction_reversal_applicable": referenced_chunk_count > 0,
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "thumbup": False, "feedback_persisted": True, "prior_direction_reversed_when_referenced": True},
        }

    return _run_case(case_id, execute)


def run_cs038() -> dict[str, Any]:
    case_id = "TC-CS-038"
    prefix = "fresh-cs-038"
    message_id = "fresh-cs-038-message"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "create_assistant_message",
            chat_id,
            session_id,
            [{"role": "user", "content": "give a final short fresh answer", "id": message_id}],
        )
        before = _session_snapshot(group, session_id)
        rejected = _feedback_message(
            case_id,
            group,
            owner["auth"],
            "reject_feedback_without_thumbup",
            chat_id,
            session_id,
            message_id,
            {"feedback": "缺少thumbup"},
        )
        after = _session_snapshot(group, session_id)
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": rejected["http_status"],
            "code": rejected["code"],
            "message": rejected["message"],
            "messages_unchanged": before.get("messages") == after.get("messages"),
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and completion["code"] == 0 and cleanup,
        }
        passed = feedback_contract_ok(observed, expected_code=102)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_assistant_message_through_completion_api", "http_status": completion["http_status"], "code": completion["code"], "raw_sha256": completion["raw_sha256"]},
                {
                    "name": "reject_feedback_without_boolean_thumbup",
                    "http_status": rejected["http_status"],
                    "code": rejected["code"],
                    "message_matches": "thumbup must be a boolean" in str(rejected["message"] or ""),
                    "raw_sha256": rejected["raw_sha256"],
                },
                {"name": "read_only_no_message_mutation_verification", "messages_unchanged": observed["messages_unchanged"]},
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 102], "message_contains": "thumbup must be a boolean", "message_mutation": 0},
        }

    return _run_case(case_id, execute)


def _sse_final_event_matches(events: list[Any]) -> bool:
    return bool(events) and events[-1] == {"code": 0, "message": "", "data": True}


def _sse_answer_events(events: list[Any]) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("code") == 0
        and isinstance(event.get("data"), dict)
        and bool(str(event["data"].get("answer") or "").strip())
        and not str(event["data"].get("answer") or "").startswith("**ERROR**")
    ]


def run_cs039() -> dict[str, Any]:
    case_id = "TC-CS-039"
    prefix = "fresh-cs-039-chat"
    dataset_prefix = "fresh-cs-039-dataset"
    prologue = "fresh streaming prologue"
    question = "你好"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dataset = _dataset_fixture(case_id, group, owner, dataset_prefix, with_chunk=True)
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_streaming_retrieval_chat",
            {
                "name": prefix,
                "dataset_ids": [dataset["id"]],
                "similarity_threshold": 0.0,
                "prompt_config": {"prologue": prologue},
            },
        )
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        streamed = _complete_stream(
            case_id,
            group,
            owner["auth"],
            "stream_completion_new_session",
            chat_id,
            [{"role": "user", "content": question}],
        )
        events = streamed.get("events") if isinstance(streamed.get("events"), list) else []
        answer_events = _sse_answer_events(events)
        rows = _session_rows(group, chat_id)
        snapshot = rows[0] if len(rows) == 1 else {"count": len(rows)}
        messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        user_message = next(
            (message for message in messages if isinstance(message, dict) and message.get("role") == "user" and message.get("content") == question),
            {},
        )
        message_id = str(user_message.get("id") or "")
        assistant_message = next(
            (message for message in messages if isinstance(message, dict) and message.get("role") == "assistant" and message.get("id") == message_id and message_id),
            {},
        )
        response_session_ids = {str(event["data"].get("session_id") or "") for event in answer_events if isinstance(event.get("data"), dict)}
        session_id = str(snapshot.get("id") or "")
        chat_cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        observed = {
            "http_status": streamed["http_status"],
            "content_type_is_sse": str(streamed.get("content_type") or "").startswith("text/event-stream"),
            "event_count": streamed.get("data_line_count", 0),
            "all_events_parsed": streamed.get("parse_error_count") == 0 and streamed.get("data_line_count") == len(events),
            "final_event_matches": _sse_final_event_matches(events),
            "answer_event_present": bool(answer_events),
            "session_id_matches": bool(session_id) and response_session_ids == {session_id},
            "database_session_count": len(rows),
            "history_preserved": bool(messages) and messages[0] == {"role": "assistant", "content": prologue},
            "message_pair_appended": bool(user_message) and bool(assistant_message),
            "cleanup_succeeded": preclean and dataset["ready"] and created["code"] == 0 and chat_cleanup and dataset_cleanup,
        }
        passed = stream_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chunked_dataset_and_streaming_chat", "dataset_ready": dataset["ready"], "chat_code": created["code"]},
                {
                    "name": "stream_completion_without_session_id",
                    "http_status": streamed["http_status"],
                    "content_type_is_sse": observed["content_type_is_sse"],
                    "event_count": observed["event_count"],
                    "parse_error_count": streamed.get("parse_error_count"),
                    "raw_sha256": streamed["raw_sha256"],
                },
                {
                    "name": "verify_current_rest_sse_terminator",
                    "final_event_matches": observed["final_event_matches"],
                    "answer_event_present": observed["answer_event_present"],
                    "session_id_matches": observed["session_id_matches"],
                },
                {
                    "name": "read_only_new_session_message_verification",
                    "database_session_count": len(rows),
                    "history_preserved": observed["history_preserved"],
                    "user_message_present": bool(user_message),
                    "assistant_message_present": bool(assistant_message),
                },
                {"name": "cleanup_session_chat_and_dataset_through_apis", "chat_cleanup": chat_cleanup, "dataset_cleanup": dataset_cleanup},
            ],
            "oracle": {"content_type": "text/event-stream", "terminator": {"code": 0, "message": "", "data": True}, "new_session_persisted": True, "message_pair_appended": True},
        }

    return _run_case(case_id, execute)


def run_cs040() -> dict[str, Any]:
    case_id = "TC-CS-040"
    prefix = "fresh-cs-040"
    prologue = "fresh continuation prologue"
    first_id = "fresh-cs-040-first"
    second_id = "fresh-cs-040-second"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat",
            {"name": prefix, "prompt_config": {"prologue": prologue}},
        )
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        seed = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "seed_history_pair",
            chat_id,
            session_id,
            [{"role": "user", "content": "first context question", "id": first_id}],
        )
        before = _session_snapshot(group, session_id)
        before_messages = before.get("messages") if isinstance(before.get("messages"), list) else []
        streamed = _complete_stream(
            case_id,
            group,
            owner["auth"],
            "stream_existing_session",
            chat_id,
            [{"role": "user", "content": "请继续", "id": second_id}],
            session_id=session_id,
        )
        events = streamed.get("events") if isinstance(streamed.get("events"), list) else []
        answer_events = _sse_answer_events(events)
        after = _session_snapshot(group, session_id)
        after_messages = after.get("messages") if isinstance(after.get("messages"), list) else []
        appended = after_messages[len(before_messages) :]
        response_session_ids = {str(event["data"].get("session_id") or "") for event in answer_events if isinstance(event.get("data"), dict)}
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": streamed["http_status"],
            "content_type_is_sse": str(streamed.get("content_type") or "").startswith("text/event-stream"),
            "event_count": streamed.get("data_line_count", 0),
            "all_events_parsed": streamed.get("parse_error_count") == 0 and streamed.get("data_line_count") == len(events),
            "final_event_matches": _sse_final_event_matches(events),
            "answer_event_present": bool(answer_events),
            "session_id_matches": bool(session_id) and response_session_ids == {session_id},
            "database_session_count": after.get("count", 0),
            "history_preserved": after_messages[: len(before_messages)] == before_messages,
            "message_pair_appended": len(appended) == 2
            and [message.get("role") for message in appended if isinstance(message, dict)] == ["user", "assistant"]
            and all(isinstance(message, dict) and message.get("id") == second_id for message in appended),
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and seed["code"] == 0 and cleanup,
        }
        passed = stream_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_session_and_seed_history_through_api",
                    "chat_code": created["code"],
                    "session_code": session["code"],
                    "seed_http_status": seed["http_status"],
                    "seed_code": seed["code"],
                    "history_count_before": len(before_messages),
                },
                {
                    "name": "stream_completion_for_existing_session",
                    "http_status": streamed["http_status"],
                    "event_count": observed["event_count"],
                    "parse_error_count": streamed.get("parse_error_count"),
                    "raw_sha256": streamed["raw_sha256"],
                },
                {
                    "name": "verify_sse_and_existing_session_identity",
                    "content_type_is_sse": observed["content_type_is_sse"],
                    "final_event_matches": observed["final_event_matches"],
                    "answer_event_present": observed["answer_event_present"],
                    "session_id_matches": observed["session_id_matches"],
                },
                {
                    "name": "read_only_history_append_verification",
                    "history_preserved": observed["history_preserved"],
                    "message_count_before": len(before_messages),
                    "message_count_after": len(after_messages),
                    "message_pair_appended": observed["message_pair_appended"],
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"content_type": "text/event-stream", "same_session": True, "history_preserved": True, "appended_messages": 2},
        }

    return _run_case(case_id, execute)


def run_cs041() -> dict[str, Any]:
    case_id = "TC-CS-041"
    prefix = "fresh-cs-041"
    question = "什么是RAG？"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "nonstream_completion",
            chat_id,
            "",
            [{"role": "user", "content": question}],
        )
        data = completion["data"] if isinstance(completion["data"], dict) else {}
        session_id = str(data.get("session_id") or "")
        snapshot = _session_snapshot(group, session_id)
        messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        user_message = next((message for message in messages if isinstance(message, dict) and message.get("role") == "user" and message.get("content") == question), {})
        message_id = str(user_message.get("id") or "")
        assistant = next((message for message in messages if isinstance(message, dict) and message.get("role") == "assistant" and message.get("id") == message_id and message_id), {})
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": completion["http_status"],
            "code": completion["code"],
            "answer_nonempty": bool(str(data.get("answer") or "").strip()),
            "reference_present": "reference" in data and isinstance(data.get("reference"), dict),
            "session_id_present": bool(session_id) and snapshot.get("count") == 1,
            "user_content_matches": bool(user_message),
            "message_pair_present": bool(user_message) and bool(assistant),
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = nonstream_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_fixture", "http_status": created["http_status"], "code": created["code"]},
                {
                    "name": "request_nonstream_completion",
                    "http_status": completion["http_status"],
                    "code": completion["code"],
                    "answer_nonempty": observed["answer_nonempty"],
                    "reference_present": observed["reference_present"],
                    "raw_sha256": completion["raw_sha256"],
                },
                {
                    "name": "read_only_generated_session_verification",
                    "session_id_present": observed["session_id_present"],
                    "user_content_matches": observed["user_content_matches"],
                    "message_pair_present": observed["message_pair_present"],
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "answer_nonempty": True, "reference_field": True},
        }

    return _run_case(case_id, execute)


def run_cs042() -> dict[str, Any]:
    case_id = "TC-CS-042"
    prefix = "fresh-cs-042"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        count_before = _conversation_count(group, chat_id)
        requests = [
            _request(
                case_id,
                group,
                "reject_messages_string",
                owner["auth"],
                "POST",
                "/chat/completions",
                payload={"chat_id": chat_id, "messages": "not a list", "stream": False},
            ),
            _request(
                case_id,
                group,
                "reject_messages_empty",
                owner["auth"],
                "POST",
                "/chat/completions",
                payload={"chat_id": chat_id, "messages": [], "stream": False},
            ),
            _request(
                case_id,
                group,
                "reject_assistant_only",
                owner["auth"],
                "POST",
                "/chat/completions",
                payload={"chat_id": chat_id, "messages": [{"role": "assistant", "content": "hi"}], "stream": False},
            ),
        ]
        count_after = _conversation_count(group, chat_id)
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_statuses": [item["http_status"] for item in requests],
            "codes": [item["code"] for item in requests],
            "list_messages_match": all("non-empty list" in str(item["message"] or "") for item in requests[:2]),
            "assistant_only_message_mentions_user": "user" in str(requests[2]["message"] or "").lower(),
            "database_delta": count_after - count_before,
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = completion_validation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_empty_chat_fixture", "http_status": created["http_status"], "code": created["code"]},
                {
                    "name": "reject_string_and_empty_messages",
                    "http_statuses": observed["http_statuses"][:2],
                    "codes": observed["codes"][:2],
                    "messages_match": observed["list_messages_match"],
                    "raw_sha256": [requests[0]["raw_sha256"], requests[1]["raw_sha256"]],
                },
                {
                    "name": "reject_assistant_only_history",
                    "http_status": requests[2]["http_status"],
                    "code": requests[2]["code"],
                    "message_mentions_user": observed["assistant_only_message_mentions_user"],
                    "raw_sha256": requests[2]["raw_sha256"],
                },
                {"name": "read_only_zero_session_delta_verification", "database_count_before": count_before, "database_count_after": count_after},
                {"name": "cleanup_chat_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"responses": [[200, 101], [200, 101], [200, 101]], "session_database_delta": 0},
        }

    return _run_case(case_id, execute)


def run_cs043() -> dict[str, Any]:
    case_id = "TC-CS-043"
    prefix = "fresh-cs-043"
    question = "测试question回退"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        completion = _request(
            case_id,
            group,
            "question_fallback_completion",
            owner["auth"],
            "POST",
            "/chat/completions",
            payload={"chat_id": chat_id, "question": question, "stream": False},
        )
        data = completion["data"] if isinstance(completion["data"], dict) else {}
        session_id = str(data.get("session_id") or "")
        snapshot = _session_snapshot(group, session_id)
        messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        user_message = next((message for message in messages if isinstance(message, dict) and message.get("role") == "user" and message.get("content") == question), {})
        message_id = str(user_message.get("id") or "")
        assistant = next((message for message in messages if isinstance(message, dict) and message.get("role") == "assistant" and message.get("id") == message_id and message_id), {})
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": completion["http_status"],
            "code": completion["code"],
            "answer_nonempty": bool(str(data.get("answer") or "").strip()),
            "reference_present": "reference" in data and isinstance(data.get("reference"), dict),
            "session_id_present": bool(session_id) and snapshot.get("count") == 1,
            "user_content_matches": bool(user_message),
            "message_pair_present": bool(user_message) and bool(assistant),
            "cleanup_succeeded": preclean and created["code"] == 0 and cleanup,
        }
        passed = nonstream_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chat_fixture", "http_status": created["http_status"], "code": created["code"]},
                {
                    "name": "complete_with_question_field_only",
                    "http_status": completion["http_status"],
                    "code": completion["code"],
                    "answer_nonempty": observed["answer_nonempty"],
                    "raw_sha256": completion["raw_sha256"],
                },
                {
                    "name": "read_only_question_fallback_verification",
                    "session_id_present": observed["session_id_present"],
                    "question_became_user_message": observed["user_content_matches"],
                    "assistant_pair_present": observed["message_pair_present"],
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "question_used_as_user_message": True, "answer_nonempty": True},
        }

    return _run_case(case_id, execute)


def run_cs044() -> dict[str, Any]:
    case_id = "TC-CS-044"
    prefix = "fresh-cs-044"
    first_id = "fresh-cs-044-m1"
    second_id = "fresh-cs-044-m2"
    baseline = [
        {"role": "user", "content": "第一问", "id": first_id},
        {"role": "assistant", "content": "第一答", "id": first_id},
        {"role": "user", "content": "总结之前的对话", "id": second_id},
    ]

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(case_id, group, owner["auth"], "create_session", chat_id, {})
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        initial = _get_session(case_id, group, owner["auth"], "read_current_session_history", chat_id, session_id)
        initial_data = initial["data"] if isinstance(initial["data"], dict) else {}
        initial_messages = initial_data.get("messages") if isinstance(initial_data.get("messages"), list) else []
        completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "pass_all_client_history",
            chat_id,
            session_id,
            baseline,
            pass_all_history_messages=True,
        )
        data = completion["data"] if isinstance(completion["data"], dict) else {}
        snapshot = _session_snapshot(group, session_id)
        messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), list) else []
        appended = messages[3] if len(messages) == 4 and isinstance(messages[3], dict) else {}
        cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": completion["http_status"],
            "code": completion["code"],
            "answer_nonempty": bool(str(data.get("answer") or "").strip()),
            "session_id_matches": str(data.get("session_id") or "") == session_id,
            "client_baseline_exact": messages[:3] == baseline,
            "assistant_appended": appended.get("role") == "assistant" and appended.get("id") == second_id and appended.get("content") == data.get("answer"),
            "final_message_count": len(messages),
            "cleanup_succeeded": preclean and created["code"] == 0 and session["code"] == 0 and initial["code"] == 0 and len(initial_messages) == 1 and cleanup,
        }
        passed = pass_all_history_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_and_read_current_session_history",
                    "chat_code": created["code"],
                    "session_code": session["code"],
                    "history_read_code": initial["code"],
                    "initial_message_count": len(initial_messages),
                    "raw_sha256": initial["raw_sha256"],
                },
                {
                    "name": "submit_complete_client_history_with_pass_all_true",
                    "http_status": completion["http_status"],
                    "code": completion["code"],
                    "answer_nonempty": observed["answer_nonempty"],
                    "raw_sha256": completion["raw_sha256"],
                },
                {
                    "name": "read_only_client_baseline_and_append_verification",
                    "session_id_matches": observed["session_id_matches"],
                    "client_baseline_exact": observed["client_baseline_exact"],
                    "assistant_appended": observed["assistant_appended"],
                    "final_message_count": len(messages),
                },
                {"name": "cleanup_session_then_chat_through_apis", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "client_baseline_message_count": 3, "assistant_appended": 1, "server_history_auto_merge": False},
        }

    return _run_case(case_id, execute)


def _mindmap_stats(value: Any) -> tuple[bool, int, bool]:
    if not isinstance(value, dict):
        return False, 0, False
    node_id = value.get("id")
    children = value.get("children")
    if not isinstance(node_id, str) or not node_id.strip() or not isinstance(children, list):
        return False, 0, False
    valid = True
    count = 1
    hierarchy = bool(children)
    for child in children:
        child_valid, child_count, child_hierarchy = _mindmap_stats(child)
        valid = valid and child_valid
        count += child_count
        hierarchy = hierarchy or child_hierarchy
    return valid, count, hierarchy


def run_cs045() -> dict[str, Any]:
    case_id = "TC-CS-045"
    prefix = "fresh-cs-045"
    text = "你好，这是语音测试"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(case_id, group, owner["auth"], "create_chat", {"name": prefix})
        chat_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        prior_default, prior_sha = _default_model_entry(case_id, group, owner["auth"], "tts")
        stale_clear_code = 0
        if prior_default and prior_default.get("model_provider") == "OpenAI" and prior_default.get("model_instance") == _audio_instance_name(case_id, group):
            stale_clear = _set_default_model(case_id, group, owner["auth"], "tts", None, "clear_stale_tts_default")
            stale_clear_code = stale_clear["code"]
            prior_default = None
        with _audio_model_stub() as base_url:
            instance = _create_audio_provider_instance(case_id, group, owner["auth"], base_url)
            desired = _controlled_audio_default_entry(instance["instance_name"], "tts")
            set_default = _set_default_model(
                case_id,
                group,
                owner["auth"],
                "tts",
                desired,
                "set_controlled_tts_default",
            )
            current_default, current_sha = _default_model_entry(case_id, group, owner["auth"], "tts")
            speech = _binary_request(
                case_id,
                group,
                "request_tts_audio",
                owner["auth"],
                "POST",
                "/chat/audio/speech",
                payload={"text": text, "chat_id": chat_id},
            )
            calls = list(_AudioModelStubHandler.calls)
            restore = _set_default_model(
                case_id,
                group,
                owner["auth"],
                "tts",
                prior_default,
                "restore_prior_tts_default",
            )
            restored_default, restored_sha = _default_model_entry(case_id, group, owner["auth"], "tts")
            instance_removed = _drop_provider_instance(
                case_id,
                group,
                owner["auth"],
                instance["instance_name"],
                "OpenAI",
            )
            provider_removed = _remove_provider_if_added(
                case_id,
                group,
                owner["auth"],
                "OpenAI",
                instance["provider_added"],
            )
        actual_inputs = [call.get("input") for call in calls if call.get("kind") == "tts" and call.get("input") in {"你好", "这是语音测试"}]
        expected_body = _AudioModelStubHandler.audio_bytes * 2
        chat_cleanup = _cleanup_created_session_chat(case_id, group, owner["auth"], owner["tenant_id"], prefix, chat_id)
        observed = {
            "http_status": speech["http_status"],
            "content_type_is_mpeg": str(speech.get("content_type") or "").startswith("audio/mpeg"),
            "body_matches_stub": speech.get("response_length") == len(expected_body) and speech.get("body_sha256") == hashlib.sha256(expected_body).hexdigest(),
            "stub_call_observed": sorted(actual_inputs) == sorted(["你好", "这是语音测试"]),
            "default_model_set": stale_clear_code == 0
            and instance["provider_ready"]
            and instance["precleaned"]
            and instance["create_code"] == 0
            and instance["visible"]
            and set_default["code"] == 0
            and _default_entry_matches(current_default, desired),
            "default_model_restored": restore["code"] == 0 and _default_entry_matches(restored_default, prior_default),
            "provider_instance_removed": instance_removed and provider_removed,
            "chat_cleanup_succeeded": preclean and created["code"] == 0 and chat_cleanup,
        }
        passed = audio_speech_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_and_controlled_tts_provider",
                    "chat_code": created["code"],
                    "provider_ready": instance["provider_ready"],
                    "provider_added": instance["provider_added"],
                    "provider_precleaned": instance["precleaned"],
                    "provider_create_code": instance["create_code"],
                    "provider_visible": instance["visible"],
                    "raw_sha256": instance["raw_sha256"],
                },
                {
                    "name": "set_and_read_controlled_tts_default",
                    "set_code": set_default["code"],
                    "default_matches": _default_entry_matches(current_default, desired),
                    "prior_default_read_sha256": prior_sha,
                    "current_default_read_sha256": current_sha,
                },
                {
                    "name": "request_streamed_mpeg_audio",
                    "http_status": speech["http_status"],
                    "content_type_is_mpeg": observed["content_type_is_mpeg"],
                    "body_length": speech.get("response_length"),
                    "body_matches_stub": observed["body_matches_stub"],
                    "actual_stub_segment_count": len(actual_inputs),
                    "raw_sha256": speech["raw_sha256"],
                },
                {
                    "name": "restore_default_and_remove_provider_instance",
                    "restore_code": restore["code"],
                    "default_restored": observed["default_model_restored"],
                    "restored_default_read_sha256": restored_sha,
                    "provider_instance_removed": instance_removed,
                    "added_provider_removed": provider_removed,
                },
                {"name": "cleanup_chat_through_api", "cleanup_succeeded": chat_cleanup},
            ],
            "oracle": {"response": [200, "audio/mpeg"], "controlled_audio_bytes": True, "default_model_restored": True, "provider_residue": 0},
        }

    return _run_case(case_id, execute)


def run_cs046() -> dict[str, Any]:
    case_id = "TC-CS-046"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        prior_default, prior_sha = _default_model_entry(case_id, group, owner["auth"], "asr")
        stale_clear_code = 0
        if prior_default and prior_default.get("model_provider") == "OpenAI" and prior_default.get("model_instance") == _audio_instance_name(case_id, group):
            stale_clear = _set_default_model(case_id, group, owner["auth"], "asr", None, "clear_stale_asr_default")
            stale_clear_code = stale_clear["code"]
            prior_default = None
        wav_bytes = _wav_fixture_bytes()
        with _audio_model_stub() as base_url:
            instance = _create_audio_provider_instance(case_id, group, owner["auth"], base_url)
            desired = _controlled_audio_default_entry(instance["instance_name"], "asr")
            set_default = _set_default_model(
                case_id,
                group,
                owner["auth"],
                "asr",
                desired,
                "set_controlled_asr_default",
            )
            current_default, current_sha = _default_model_entry(case_id, group, owner["auth"], "asr")
            transcription = _request(
                case_id,
                group,
                "transcribe_controlled_wav",
                owner["auth"],
                "POST",
                "/chat/audio/transcription",
                files=[("file", ("fresh-controlled.wav", wav_bytes, "audio/wav"))],
            )
            calls = list(_AudioModelStubHandler.calls)
            restore = _set_default_model(
                case_id,
                group,
                owner["auth"],
                "asr",
                prior_default,
                "restore_prior_asr_default",
            )
            restored_default, restored_sha = _default_model_entry(case_id, group, owner["auth"], "asr")
            instance_removed = _drop_provider_instance(
                case_id,
                group,
                owner["auth"],
                instance["instance_name"],
                "OpenAI",
            )
            provider_removed = _remove_provider_if_added(
                case_id,
                group,
                owner["auth"],
                "OpenAI",
                instance["provider_added"],
            )
        data = transcription["data"] if isinstance(transcription["data"], dict) else {}
        asr_calls = [call for call in calls if call.get("kind") == "asr"]
        observed = {
            "http_status": transcription["http_status"],
            "code": transcription["code"],
            "text_matches": data.get("text") == _AudioModelStubHandler.transcription_text,
            "multipart_call_observed": len(asr_calls) == 1 and asr_calls[0].get("content_type_is_multipart") is True and int(asr_calls[0].get("body_length") or 0) > len(wav_bytes),
            "valid_wav_fixture": wav_bytes.startswith(b"RIFF") and wav_bytes[8:12] == b"WAVE" and len(wav_bytes) > 44,
            "default_model_set": stale_clear_code == 0
            and instance["provider_ready"]
            and instance["precleaned"]
            and instance["create_code"] == 0
            and instance["visible"]
            and set_default["code"] == 0
            and _default_entry_matches(current_default, desired),
            "default_model_restored": restore["code"] == 0 and _default_entry_matches(restored_default, prior_default),
            "provider_instance_removed": instance_removed and provider_removed,
        }
        passed = audio_transcription_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_controlled_audio_provider",
                    "provider_ready": instance["provider_ready"],
                    "provider_added": instance["provider_added"],
                    "provider_precleaned": instance["precleaned"],
                    "provider_create_code": instance["create_code"],
                    "provider_visible": instance["visible"],
                    "raw_sha256": instance["raw_sha256"],
                },
                {
                    "name": "set_and_read_controlled_asr_default",
                    "set_code": set_default["code"],
                    "default_matches": _default_entry_matches(current_default, desired),
                    "prior_default_read_sha256": prior_sha,
                    "current_default_read_sha256": current_sha,
                },
                {
                    "name": "upload_fresh_in_memory_wav_for_transcription",
                    "wav_size": len(wav_bytes),
                    "wav_sha256": hashlib.sha256(wav_bytes).hexdigest(),
                    "valid_wav_fixture": observed["valid_wav_fixture"],
                    "http_status": transcription["http_status"],
                    "code": transcription["code"],
                    "text_matches": observed["text_matches"],
                    "multipart_call_observed": observed["multipart_call_observed"],
                    "raw_sha256": transcription["raw_sha256"],
                },
                {
                    "name": "restore_default_and_remove_provider_instance",
                    "restore_code": restore["code"],
                    "default_restored": observed["default_model_restored"],
                    "restored_default_read_sha256": restored_sha,
                    "provider_instance_removed": instance_removed,
                    "added_provider_removed": provider_removed,
                },
            ],
            "oracle": {"response": [200, 0], "transcription_text": _AudioModelStubHandler.transcription_text, "valid_wav": True, "provider_residue": 0},
        }

    return _run_case(case_id, execute)


def run_cs047() -> dict[str, Any]:
    case_id = "TC-CS-047"
    dataset_prefix = "fresh-cs-047-dataset"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        dataset = _dataset_fixture(
            case_id,
            group,
            owner,
            dataset_prefix,
            with_chunk=True,
            chunk_content=chat_mindmap_fixture_content(),
        )
        response = _request(
            case_id,
            group,
            "generate_dataset_mindmap",
            owner["auth"],
            "POST",
            "/chat/mindmap",
            payload={"question": "RAGFlow的架构", "kb_ids": [dataset["id"]]},
            timeout=180,
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        shape_valid, node_count, hierarchy = _mindmap_stats(data)
        cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "root_shape_matches": shape_valid,
            "node_count": node_count,
            "hierarchy_present": hierarchy,
            "cleanup_succeeded": dataset["ready"] and cleanup,
        }
        passed = mindmap_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {"name": "create_chunked_mindmap_dataset", "dataset_ready": dataset["ready"], "api_codes": dataset["api_codes"]},
                {"name": "generate_mindmap_from_dataset", "http_status": response["http_status"], "code": response["code"], "raw_sha256": response["raw_sha256"]},
                {"name": "verify_recursive_node_hierarchy", "root_shape_matches": shape_valid, "node_count": node_count, "hierarchy_present": hierarchy},
                {"name": "cleanup_dataset_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "node_shape": {"id": "string", "children": "array"}, "minimum_node_count": 2},
        }

    return _run_case(case_id, execute)


def run_cs048() -> dict[str, Any]:
    case_id = "TC-CS-048"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        before = _tenant_active_chat_count(group, owner["tenant_id"]) + _tenant_conversation_count(group, owner["tenant_id"])
        response = _request(
            case_id,
            group,
            "generate_recommended_questions",
            owner["auth"],
            "POST",
            "/chat/recommendation",
            payload={"question": "什么是文档解析？"},
            timeout=180,
        )
        data = response["data"] if isinstance(response["data"], list) else []
        after = _tenant_active_chat_count(group, owner["tenant_id"]) + _tenant_conversation_count(group, owner["tenant_id"])
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "list_nonempty": bool(data),
            "all_items_nonempty_strings": bool(data) and all(isinstance(item, str) and bool(item.strip()) for item in data),
            "database_delta": after - before,
        }
        passed = recommendation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "generate_recommendations_with_default_chat_model",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "returned_count": len(data),
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "verify_nonempty_string_list", "list_nonempty": observed["list_nonempty"], "all_items_nonempty_strings": observed["all_items_nonempty_strings"]},
                {"name": "read_only_no_chat_or_session_write_verification", "combined_count_before": before, "combined_count_after": after},
            ],
            "oracle": {"response": [200, 0], "data_type": "nonempty string array", "chat_session_database_delta": 0},
        }

    return _run_case(case_id, execute)


def run_cs049() -> dict[str, Any]:
    case_id = "TC-CS-049"
    prefix = "fresh-cs-049"
    title = f"{prefix}-测试Agent"
    dsl = _agent_dsl()

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        response = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_minimal_agent_without_tags",
            title=title,
            dsl=dsl,
            tags=None,
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        agent_id = str(data.get("id") or "")
        snapshot = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        versions_before_delete = _agent_version_count(group, agent_id) if agent_id else 0
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        versions_after_delete = _agent_version_count(group, agent_id) if agent_id else 0
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "id_present": bool(agent_id),
            "title_matches": data.get("title") == title and snapshot.get("title") == title,
            "canvas_category": data.get("canvas_category"),
            "database_count": snapshot.get("count"),
            "owner_matches": snapshot.get("user_id") == owner["tenant_id"],
            "database_dsl_matches": snapshot.get("dsl") == dsl,
            "physical_tags_is_empty": snapshot.get("physical_tags_is_empty"),
            "physical_tags_is_null": snapshot.get("physical_tags_is_null"),
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_create_contract_ok(group, observed)
        findings: list[dict[str, Any]] = []
        if not passed and group == "experiment" and snapshot.get("count") == 0:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "user_canvas.tags",
                    "summary": "minimal Agent create without tags did not persist a row",
                    "response_code": response["code"],
                }
            )
        if versions_after_delete:
            findings.append(
                {
                    "type": "cleanup_residue",
                    "area": "user_canvas_version",
                    "summary": "Agent DELETE left version rows after API cleanup",
                    "row_count": versions_after_delete,
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_minimal_agent_without_tags_through_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_user_canvas_json_and_tags_verification",
                    "database_count": snapshot.get("count"),
                    "owner_matches": observed["owner_matches"],
                    "database_dsl_matches": observed["database_dsl_matches"],
                    "physical_tags_is_empty": observed["physical_tags_is_empty"],
                    "physical_tags_is_null": observed["physical_tags_is_null"],
                    "version_count": versions_before_delete,
                },
                {
                    "name": "delete_created_agent_through_api",
                    "cleanup_succeeded": cleanup,
                    "final_canvas_count": _agent_snapshot(group, agent_id).get("count") if agent_id else 0,
                    "version_residue_count": versions_after_delete,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "canvas_category": "agent_canvas",
                "dsl_json_roundtrip": True,
                "tags_storage": {
                    "control": "empty string",
                    "experiment": "NULL with application-level empty-string semantics",
                },
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs050() -> dict[str, Any]:
    case_id = "TC-CS-050"
    prefix = "fresh-cs-050"
    title = f"{prefix}-重复Agent"
    dsl = _agent_dsl()

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        fixture = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_duplicate_title_fixture",
            title=title,
            dsl=dsl,
        )
        fixture_data = fixture["data"] if isinstance(fixture["data"], dict) else {}
        fixture_id = str(fixture_data.get("id") or "")
        fixture_snapshot = _agent_snapshot(group, fixture_id) if fixture_id else {"count": 0}
        fixture_ready = fixture["code"] == 0 and fixture_snapshot.get("count") == 1
        before = _visible_agent_count(group, owner["tenant_id"])
        duplicate = _create_agent(
            case_id,
            group,
            owner["auth"],
            "submit_case_insensitive_duplicate_title",
            title=title.upper(),
            dsl=dsl,
            tags=None,
        )
        after = _visible_agent_count(group, owner["tenant_id"])
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [fixture_id],
        )
        observed = {
            "http_status": duplicate["http_status"],
            "code": duplicate["code"],
            "message": duplicate["message"],
            "database_delta": after - before,
            "fixture_ready": fixture_ready,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_duplicate_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_duplicate_title_precondition_through_api",
                    "code": fixture["code"],
                    "fixture_ready": observed["fixture_ready"],
                    "agent_id_fingerprint": DB._fingerprint(fixture_id),
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "submit_case_insensitive_duplicate_title",
                    "http_status": duplicate["http_status"],
                    "code": duplicate["code"],
                    "message_matches": "already exists" in str(duplicate["message"] or ""),
                    "raw_sha256": duplicate["raw_sha256"],
                },
                {
                    "name": "read_only_zero_delta_and_api_cleanup_verification",
                    "database_delta": after - before,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 102],
                "case_insensitive_duplicate": True,
                "database_delta": 0,
            },
        }

    return _run_case(case_id, execute)


def run_cs051() -> dict[str, Any]:
    case_id = "TC-CS-051"
    prefix = "fresh-cs-051"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = [
            _create_agent(
                case_id,
                group,
                owner["auth"],
                f"create_agent_{index}",
                title=f"{prefix}-Agent-{index}",
                dsl=_agent_dsl(probe=str(index)),
            )
            for index in (1, 2)
        ]
        fixture_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in created]
        expected_total = _visible_agent_count(group, owner["tenant_id"])
        response = _list_agents(
            case_id,
            group,
            owner["auth"],
            "list_agents_page_one",
            {"page": 1, "page_size": 10},
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        canvas = data.get("canvas") if isinstance(data.get("canvas"), list) else []
        returned_ids = {str(item.get("id")) for item in canvas if isinstance(item, dict)}
        required = {"id", "title", "canvas_category", "tags"}
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            fixture_ids,
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fixture_ready": all(item["code"] == 0 for item in created) and all(fixture_ids),
            "canvas_is_list": isinstance(data.get("canvas"), list),
            "fixture_ids_present": set(fixture_ids).issubset(returned_ids),
            "total_matches_database": data.get("total") == expected_total,
            "required_fields_present": bool(canvas) and all(isinstance(item, dict) and required.issubset(item) for item in canvas),
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_agent_fixtures_through_api",
                    "create_codes": [item["code"] for item in created],
                    "fixture_id_fingerprints": [DB._fingerprint(item) for item in fixture_ids],
                },
                {
                    "name": "list_agent_page_one",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "returned_count": len(canvas),
                    "fixture_ids_present": observed["fixture_ids_present"],
                    "required_fields_present": observed["required_fields_present"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_total_and_api_cleanup_verification",
                    "api_total": data.get("total"),
                    "database_total": expected_total,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "shape": {"canvas": "array", "total": "database count"},
                "fixture_ids_present": True,
            },
        }

    return _run_case(case_id, execute)


def run_cs052() -> dict[str, Any]:
    case_id = "TC-CS-052"
    prefix = "fresh-cs-052"
    target_tag = "测试标签"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        matched = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_matching_tag_agent",
            title=f"{prefix}-matched",
            dsl=_agent_dsl(probe="matched"),
            tags=f"fresh-agent,{target_tag}",
        )
        unmatched = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_nonmatching_boundary_agent",
            title=f"{prefix}-unmatched",
            dsl=_agent_dsl(probe="unmatched"),
            tags=f"fresh-agent,{target_tag}扩展",
        )
        matched_data = matched["data"] if isinstance(matched["data"], dict) else {}
        unmatched_data = unmatched["data"] if isinstance(unmatched["data"], dict) else {}
        matched_id = str(matched_data.get("id") or "")
        unmatched_id = str(unmatched_data.get("id") or "")
        matched_snapshot = _agent_snapshot(group, matched_id) if matched_id else {"count": 0}
        unmatched_snapshot = _agent_snapshot(group, unmatched_id) if unmatched_id else {"count": 0}
        database_tags_match = matched_snapshot.get("tags") == f"fresh-agent,{target_tag}" and unmatched_snapshot.get("tags") == f"fresh-agent,{target_tag}扩展"
        response = _list_agents(
            case_id,
            group,
            owner["auth"],
            "filter_agents_by_unicode_tag",
            {"tags": target_tag},
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        canvas = data.get("canvas") if isinstance(data.get("canvas"), list) else []
        returned_ids = {str(item.get("id")) for item in canvas if isinstance(item, dict)}
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [matched_id, unmatched_id],
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fixture_ready": matched["code"] == 0 and unmatched["code"] == 0 and bool(matched_id) and bool(unmatched_id),
            "matched_id_present": matched_id in returned_ids,
            "unmatched_id_absent": unmatched_id not in returned_ids,
            "all_returned_tags_match": all(target_tag in [tag.strip() for tag in str(item.get("tags") or "").split(",")] for item in canvas if isinstance(item, dict)),
            "database_tags_match": database_tags_match,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_tag_filter_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_unicode_tag_boundary_fixtures_through_api",
                    "create_codes": [matched["code"], unmatched["code"]],
                    "fixture_ready": observed["fixture_ready"],
                },
                {
                    "name": "filter_agents_by_exact_unicode_tag",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "returned_count": len(canvas),
                    "matched_id_present": observed["matched_id_present"],
                    "unmatched_boundary_id_absent": observed["unmatched_id_absent"],
                    "all_returned_tags_match": observed["all_returned_tags_match"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_tags_storage_and_api_cleanup_verification",
                    "database_tags_match": observed["database_tags_match"],
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "exact_comma_delimited_tag_match": target_tag,
                "partial_tag_match": False,
            },
        }

    return _run_case(case_id, execute)


def run_cs053() -> dict[str, Any]:
    case_id = "TC-CS-053"
    prefix = "fresh-cs-053"
    dsl = _agent_dsl(probe="detail-json-roundtrip")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_detail_fixture",
            title=f"{prefix}-Agent",
            dsl=dsl,
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        snapshot = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        response = _get_agent(case_id, group, owner["auth"], "get_agent_detail", agent_id)
        data = response["data"] if isinstance(response["data"], dict) else {}
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fixture_ready": created["code"] == 0 and snapshot.get("count") == 1,
            "id_matches": data.get("id") == agent_id,
            "dsl_matches_request": data.get("dsl") == dsl,
            "dsl_matches_database": data.get("dsl") == snapshot.get("dsl"),
            "last_publish_time_present": "last_publish_time" in data,
            "versions_absent": "versions" not in data,
            "datasets_absent": "datasets" not in data,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_detail_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_detail_fixture_through_api",
                    "code": created["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "get_agent_detail",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "id_matches": observed["id_matches"],
                    "last_publish_time_field_present": observed["last_publish_time_present"],
                    "versions_absent": observed["versions_absent"],
                    "datasets_absent": observed["datasets_absent"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_user_canvas_dsl_and_api_cleanup_verification",
                    "dsl_matches_request": observed["dsl_matches_request"],
                    "dsl_matches_database": observed["dsl_matches_database"],
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "full_dsl_json_roundtrip": True,
                "embedded_versions": False,
                "embedded_datasets_for_agent_canvas": False,
            },
        }

    return _run_case(case_id, execute)


def run_cs054() -> dict[str, Any]:
    case_id = "TC-CS-054"
    prefix = "fresh-cs-054"
    initial_dsl = _agent_dsl(probe="before-update")
    updated_dsl = _agent_update_dsl()
    updated_title = f"{prefix}-更新后Agent"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_update_fixture",
            title=f"{prefix}-更新前Agent",
            dsl=initial_dsl,
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        before = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        versions_before = _agent_version_rows(group, agent_id) if agent_id else []
        updated = _put_agent(
            case_id,
            group,
            owner["auth"],
            "update_agent_title_and_dsl",
            agent_id,
            {"title": updated_title, "dsl": updated_dsl},
        )
        after = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        versions_after = _agent_version_rows(group, agent_id) if agent_id else []
        replica = _agent_replica_snapshot(group, agent_id, owner["tenant_id"]) if agent_id else {"exists": False, "payload": None, "ttl": -2}
        replica_payload = replica["payload"] if isinstance(replica.get("payload"), dict) else {}
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "http_status": updated["http_status"],
            "code": updated["code"],
            "fixture_ready": created["code"] == 0 and before.get("count") == 1 and before.get("dsl") == initial_dsl and len(versions_before) == 1,
            "title_matches": after.get("title") == updated_title,
            "database_dsl_matches": after.get("dsl") == updated_dsl,
            "version_count_delta": len(versions_after) - len(versions_before),
            "latest_version_dsl_matches": bool(versions_after) and versions_after[-1].get("dsl") == updated_dsl,
            "replica_exists": replica.get("exists"),
            "replica_dsl_matches": replica_payload.get("dsl") == updated_dsl,
            "replica_title_matches": replica_payload.get("title") == updated_title,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_initial_version_through_api",
                    "code": created["code"],
                    "fixture_ready": observed["fixture_ready"],
                    "initial_version_count": len(versions_before),
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "update_agent_title_and_dsl_through_api",
                    "http_status": updated["http_status"],
                    "code": updated["code"],
                    "raw_sha256": updated["raw_sha256"],
                },
                {
                    "name": "read_only_canvas_and_version_verification",
                    "title_matches": observed["title_matches"],
                    "database_dsl_matches": observed["database_dsl_matches"],
                    "version_count_before": len(versions_before),
                    "version_count_after": len(versions_after),
                    "latest_version_dsl_matches": observed["latest_version_dsl_matches"],
                },
                {
                    "name": "read_only_canvas_replica_verification",
                    "replica_exists": replica.get("exists"),
                    "replica_ttl_positive": int(replica.get("ttl") or -2) > 0,
                    "replica_dsl_matches": observed["replica_dsl_matches"],
                    "replica_title_matches": observed["replica_title_matches"],
                    "key_fingerprint": replica.get("key_fingerprint"),
                },
                {
                    "name": "delete_updated_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "user_canvas_updated": True,
                "new_version_delta": 1,
                "replica_synchronized": True,
            },
        }

    return _run_case(case_id, execute)


def run_cs055() -> dict[str, Any]:
    case_id = "TC-CS-055"
    prefix = "fresh-cs-055"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_delete_fixture",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="delete-cascade"),
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        session = (
            _create_agent_session(
                case_id,
                group,
                owner["auth"],
                "create_agent_session_delete_fixture",
                agent_id,
                {"name": f"{prefix}-Session"},
            )
            if agent_id
            else {
                "http_status": 0,
                "code": None,
                "data": None,
                "raw_sha256": None,
            }
        )
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        versions_before = _agent_version_rows(group, agent_id) if agent_id else []
        sessions_before = _agent_session_rows(group, agent_id) if agent_id else []
        replica_before = _agent_replica_snapshot(group, agent_id, owner["tenant_id"]) if agent_id else {"exists": False}
        fixture_ready = (
            preclean
            and created["code"] == 0
            and session["code"] == 0
            and bool(agent_id)
            and bool(session_id)
            and len(versions_before) >= 1
            and [row["id"] for row in sessions_before] == [session_id]
            and replica_before.get("exists") is True
        )
        deleted = (
            _delete_agent(
                case_id,
                group,
                owner["auth"],
                "delete_agent_as_owner",
                agent_id,
            )
            if agent_id
            else {
                "http_status": 0,
                "code": None,
                "data": None,
                "raw_sha256": None,
            }
        )
        canvas_after = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        versions_after = _agent_version_rows(group, agent_id) if agent_id else []
        sessions_after = _agent_session_rows(group, agent_id) if agent_id else []
        replica_after = _agent_replica_snapshot(group, agent_id, owner["tenant_id"]) if agent_id else {"exists": False, "ttl": -2}
        if canvas_after.get("count") == 1:
            _cleanup_created_agents(
                case_id,
                group,
                owner["auth"],
                owner["tenant_id"],
                prefix,
                [agent_id],
            )
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "fixture_ready": fixture_ready,
            "canvas_count_after": canvas_after.get("count"),
            "version_count_after": len(versions_after),
            "session_count_after": len(sessions_after),
            "replica_exists_after": replica_after.get("exists"),
        }
        passed = agent_delete_contract_ok(observed)
        findings = []
        for area, count in (
            ("user_canvas_version", len(versions_after)),
            ("api_4_conversation", len(sessions_after)),
            ("canvas_replica", int(bool(replica_after.get("exists")))),
        ):
            if count:
                findings.append(
                    {
                        "type": "product_defect",
                        "area": area,
                        "summary": "Agent owner DELETE left unreachable related state",
                        "residue_count": count,
                    }
                )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_version_session_and_replica_fixtures",
                    "agent_code": created["code"],
                    "session_code": session["code"],
                    "fixture_ready": fixture_ready,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "session_id_fingerprint": DB._fingerprint(session_id),
                    "version_id_fingerprints": [DB._fingerprint(row["id"]) for row in versions_before],
                    "raw_sha256": [created["raw_sha256"], session["raw_sha256"]],
                },
                {
                    "name": "delete_agent_as_owner_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "read_only_cascade_and_replica_residue_verification",
                    "canvas_count_after": canvas_after.get("count"),
                    "version_count_before": len(versions_before),
                    "version_count_after": len(versions_after),
                    "session_count_before": len(sessions_before),
                    "session_count_after": len(sessions_after),
                    "replica_exists_before": replica_before.get("exists"),
                    "replica_exists_after": replica_after.get("exists"),
                    "replica_ttl_after": replica_after.get("ttl"),
                },
            ],
            "oracle": {
                "response": [200, 0],
                "user_canvas_count_after": 0,
                "user_canvas_version_count_after": 0,
                "api_4_conversation_count_after": 0,
                "canvas_replica_exists_after": False,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs056() -> dict[str, Any]:
    case_id = "TC-CS-056"
    prefix = "fresh-cs-056"
    email = "cs-056-user-b@fresh.invalid"
    password = "Fresh-CS-056-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        owner_preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = _prepare_secondary_user(case_id, group, email, password)
        secondary_ready = secondary["preclean"] and secondary["registration"]["code"] == 0 and secondary["login"]["code"] == 0 and bool(secondary["auth"])
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_user_a_agent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="owner-only-delete"),
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        before = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        denied = _delete_agent(
            case_id,
            group,
            secondary["auth"],
            "user_b_attempt_delete_user_a_agent",
            agent_id,
        )
        after = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        secondary_cleanup = _cleanup_secondary_user(case_id, group, email)
        observed = {
            "http_status": denied["http_status"],
            "code": denied["code"],
            "message": denied["message"],
            "fixture_ready": owner_preclean and created["code"] == 0 and before.get("count") == 1,
            "canvas_unchanged": before == after,
            "secondary_ready": secondary_ready,
            "agent_cleanup_succeeded": agent_cleanup,
            "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
        }
        passed = nonowner_agent_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "prepare_independent_user_b_and_user_a_agent",
                    "secondary_ready": secondary_ready,
                    "agent_create_code": created["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                },
                {
                    "name": "user_b_attempt_delete_user_a_agent",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "exact_message_matches": denied["message"] == "Only the owner of the agent is authorized for this operation.",
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "read_only_no_mutation_and_api_cleanup_verification",
                    "canvas_unchanged": before == after,
                    "agent_cleanup_succeeded": agent_cleanup,
                    "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "response": [200, 103],
                "message": "Only the owner of the agent is authorized for this operation.",
                "canvas_mutation": False,
            },
        }

    return _run_case(case_id, execute)


def run_cs057() -> dict[str, Any]:
    case_id = "TC-CS-057"
    prefix = "fresh-cs-057"
    runtime_dsl = _agent_runtime_dsl()
    original_topology = _component_topology(runtime_dsl)

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_modified_runtime_agent",
            title=f"{prefix}-Agent",
            dsl=runtime_dsl,
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        before = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        reset = _reset_agent(case_id, group, owner["auth"], "reset_agent_runtime_dsl", agent_id)
        data = reset["data"] if isinstance(reset["data"], dict) else {}
        after = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        replica = _agent_replica_snapshot(group, agent_id, owner["tenant_id"]) if agent_id else {"exists": False, "payload": None}
        replica_payload = replica["payload"] if isinstance(replica.get("payload"), dict) else {}
        globals_value = data.get("globals") if isinstance(data.get("globals"), dict) else {}
        expected_system = {
            "sys.query": "",
            "sys.user_id": "",
            "sys.conversation_turns": 0,
            "sys.files": [],
            "sys.history": [],
            "sys.date": "",
        }
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "http_status": reset["http_status"],
            "code": reset["code"],
            "fixture_ready": created["code"] == 0 and before.get("count") == 1 and before.get("dsl") == runtime_dsl,
            "path_cleared": data.get("path") == [],
            "history_cleared": data.get("history") == [],
            "retrieval_cleared": data.get("retrieval") == [],
            "memory_cleared": data.get("memory") == [],
            "system_globals_cleared": all(globals_value.get(key) == value for key, value in expected_system.items()),
            "custom_globals_preserved": globals_value.get("custom.user") == "preserve-custom-value" and globals_value.get("env.keep") == "environment-default",
            "component_topology_preserved": _component_topology(data) == original_topology,
            "component_runtime_cleared": _component_runtime_cleared(data),
            "database_dsl_matches": after.get("dsl") == data,
            "replica_dsl_matches": replica_payload.get("dsl") == data,
            "cleanup_succeeded": preclean and cleanup,
        }
        passed = agent_reset_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_with_modified_runtime_state",
                    "code": created["code"],
                    "fixture_ready": observed["fixture_ready"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "reset_agent_runtime_dsl_through_api",
                    "http_status": reset["http_status"],
                    "code": reset["code"],
                    "path_cleared": observed["path_cleared"],
                    "history_cleared": observed["history_cleared"],
                    "retrieval_cleared": observed["retrieval_cleared"],
                    "memory_cleared": observed["memory_cleared"],
                    "raw_sha256": reset["raw_sha256"],
                },
                {
                    "name": "verify_system_and_component_reset_semantics",
                    "system_globals_cleared": observed["system_globals_cleared"],
                    "custom_globals_preserved": observed["custom_globals_preserved"],
                    "component_topology_preserved": observed["component_topology_preserved"],
                    "component_runtime_cleared": observed["component_runtime_cleared"],
                },
                {
                    "name": "read_only_database_replica_and_api_cleanup_verification",
                    "database_dsl_matches": observed["database_dsl_matches"],
                    "replica_exists": replica.get("exists"),
                    "replica_dsl_matches": observed["replica_dsl_matches"],
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "runtime_state_cleared": True,
                "edited_component_topology_preserved": True,
                "database_and_replica_synchronized": True,
            },
        }

    return _run_case(case_id, execute)


def run_cs058() -> dict[str, Any]:
    case_id = "TC-CS-058"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        database_rows = _canvas_template_rows(group)
        response = _list_agent_templates(case_id, group, owner["auth"], "list_agent_templates")
        data = response["data"] if isinstance(response["data"], list) else []
        required = {"id", "title", "description", "canvas_category", "dsl"}
        api_rows = {str(item.get("id")): item for item in data if isinstance(item, dict)}
        database_by_id = {row["id"]: row for row in database_rows}
        required_fields_present = bool(data) and all(isinstance(item, dict) and required.issubset(item) for item in data)
        multilingual_json_types = bool(data) and all(
            isinstance(item.get("title"), dict) and {"en", "zh"}.issubset(item["title"]) and isinstance(item.get("description"), dict) and {"en", "zh"}.issubset(item["description"])
            for item in data
            if isinstance(item, dict)
        )
        database_rows_match = set(api_rows) == set(database_by_id) and all(
            api_rows[template_id].get("title") == row["title"]
            and api_rows[template_id].get("description") == row["description"]
            and api_rows[template_id].get("canvas_category") == row["canvas_category"]
            and api_rows[template_id].get("dsl") == row["dsl"]
            for template_id, row in database_by_id.items()
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "list_nonempty": bool(data),
            "required_fields_present": required_fields_present,
            "multilingual_json_types": multilingual_json_types,
            "database_count_matches": len(data) == len(database_rows),
            "database_rows_match": database_rows_match,
        }
        passed = agent_template_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "list_agent_templates",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "template_count": len(data),
                    "required_fields_present": required_fields_present,
                    "multilingual_json_types": multilingual_json_types,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_canvas_template_json_verification",
                    "database_count": len(database_rows),
                    "database_count_matches": observed["database_count_matches"],
                    "database_rows_match": database_rows_match,
                    "template_id_fingerprints": [DB._fingerprint(template_id) for template_id in sorted(database_by_id)],
                },
            ],
            "oracle": {
                "response": [200, 0],
                "list_nonempty": True,
                "title_description_type": "multilingual JSON objects",
                "database_roundtrip": True,
            },
        }

    return _run_case(case_id, execute)


def run_cs059() -> dict[str, Any]:
    case_id = "TC-CS-059"
    expected_keys = {
        "task_analysis",
        "plan_generation",
        "reflection",
        "citation_guidelines",
    }

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        response = _get_agent_prompts(case_id, group, owner["auth"], "get_builtin_agent_prompts")
        data = response["data"] if isinstance(response["data"], dict) else {}
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "keys_exact": set(data) == expected_keys,
            "all_nonempty_strings": all(isinstance(data.get(key), str) and bool(data[key].strip()) for key in expected_keys),
        }
        passed = agent_prompt_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "get_builtin_agent_prompts",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "returned_keys": sorted(data),
                    "keys_exact": observed["keys_exact"],
                    "all_nonempty_strings": observed["all_nonempty_strings"],
                    "value_lengths": {key: len(data.get(key, "")) for key in sorted(expected_keys)},
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            "oracle": {
                "response": [200, 0],
                "keys": sorted(expected_keys),
                "values": "nonempty strings",
            },
        }

    return _run_case(case_id, execute)


def run_cs060() -> dict[str, Any]:
    case_id = "TC-CS-060"
    prefix = "fresh-cs-060"
    dsls = [
        _agent_dsl(probe="version-initial"),
        _agent_dsl(probe="version-second"),
        _agent_dsl(probe="version-third"),
    ]

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_version_fixture",
            title=f"{prefix}-Agent",
            dsl=dsls[0],
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        updates = [
            _put_agent(
                case_id,
                group,
                owner["auth"],
                f"save_agent_version_{index}",
                agent_id,
                {"dsl": dsl},
            )
            for index, dsl in enumerate(dsls[1:], start=2)
        ]
        database_rows = _agent_version_rows(group, agent_id) if agent_id else []
        response = _list_agent_versions(case_id, group, owner["auth"], "list_agent_versions", agent_id)
        data = response["data"] if isinstance(response["data"], list) else []
        required = {"id", "title", "description", "release", "create_time"}
        api_by_id = {str(item.get("id")): item for item in data if isinstance(item, dict)}
        database_by_id = {row["id"]: row for row in database_rows}
        required_fields_present = len(data) == 3 and all(isinstance(item, dict) and required.issubset(item) for item in data)
        update_times = [int(item.get("update_time") or 0) for item in data if isinstance(item, dict)]
        descending_order = update_times == sorted(update_times, reverse=True)
        database_scalar_fields_match = set(api_by_id) == set(database_by_id) and all(
            api_by_id[version_id].get("title") == row["title"]
            and api_by_id[version_id].get("description") == row["description"]
            and bool(api_by_id[version_id].get("release")) == row["release"]
            and api_by_id[version_id].get("create_time") == row["create_time"]
            for version_id, row in database_by_id.items()
        )
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fixture_ready": preclean and created["code"] == 0 and all(item["code"] == 0 for item in updates) and [row["dsl"] for row in database_rows] == dsls,
            "version_count": len(data),
            "required_fields_present": required_fields_present,
            "descending_order": descending_order,
            "database_ids_match": set(api_by_id) == set(database_by_id),
            "database_scalar_fields_match": database_scalar_fields_match,
            "cleanup_succeeded": cleanup,
        }
        passed = agent_version_list_contract_ok(observed)
        missing_fields = sorted({field for item in data if isinstance(item, dict) for field in required - set(item)})
        findings = (
            [
                {
                    "type": "product_defect",
                    "area": "agent_version_list",
                    "summary": "version list omitted plan-required fields",
                    "missing_fields": missing_fields,
                }
            ]
            if missing_fields
            else []
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_save_multiple_versions_through_api",
                    "create_code": created["code"],
                    "update_codes": [item["code"] for item in updates],
                    "database_version_count": len(database_rows),
                    "database_dsls_match_save_order": [row["dsl"] for row in database_rows] == dsls,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                },
                {
                    "name": "list_agent_versions",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "version_count": len(data),
                    "required_fields_present": required_fields_present,
                    "missing_fields": missing_fields,
                    "descending_order": descending_order,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_version_mapping_and_api_cleanup_verification",
                    "database_ids_match": observed["database_ids_match"],
                    "database_scalar_fields_match": database_scalar_fields_match,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "version_count": 3,
                "required_fields": sorted(required),
                "order": "update_time descending",
                "database_mapping": True,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs061() -> dict[str, Any]:
    case_id = "TC-CS-061"
    prefix = "fresh-cs-061"
    dsl = _agent_dsl(probe="version-detail")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_version_detail_fixture",
            title=f"{prefix}-Agent",
            dsl=dsl,
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        agent_id = str(created_data.get("id") or "")
        database_rows = _agent_version_rows(group, agent_id) if agent_id else []
        database_row = database_rows[0] if len(database_rows) == 1 else {}
        version_id = str(database_row.get("id") or "")
        response = _get_agent_version(
            case_id,
            group,
            owner["auth"],
            "get_agent_version_detail",
            agent_id,
            version_id,
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        scalar_fields_match = all(data.get(field) == database_row.get(field) for field in ("title", "description", "release", "create_time", "update_time"))
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fixture_ready": preclean and created["code"] == 0 and len(database_rows) == 1,
            "id_matches": str(data.get("id") or "") == version_id,
            "agent_id_matches": str(data.get("user_canvas_id") or "") == agent_id,
            "dsl_present": isinstance(data.get("dsl"), dict),
            "dsl_matches_database": data.get("dsl") == database_row.get("dsl") == dsl,
            "scalar_fields_match": scalar_fields_match,
            "cleanup_succeeded": cleanup,
        }
        passed = agent_version_detail_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_capture_version_id",
                    "create_code": created["code"],
                    "database_version_count": len(database_rows),
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "version_id_fingerprint": DB._fingerprint(version_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "get_agent_version_detail",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "id_matches": observed["id_matches"],
                    "agent_id_matches": observed["agent_id_matches"],
                    "dsl_present": observed["dsl_present"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_version_dsl_and_api_cleanup_verification",
                    "dsl_matches_database": observed["dsl_matches_database"],
                    "scalar_fields_match": scalar_fields_match,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "full_dsl_snapshot": True,
                "database_roundtrip": True,
            },
        }

    return _run_case(case_id, execute)


def run_cs062() -> dict[str, Any]:
    case_id = "TC-CS-062"
    prefix = "fresh-cs-062"
    session_name = "Agent测试会话"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_session_parent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="session-create"),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        session = _create_agent_session(
            case_id,
            group,
            owner["auth"],
            "create_agent_session",
            agent_id,
            {"name": session_name},
        )
        data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(data.get("id") or "")
        rows = _agent_session_rows(group, agent_id) if agent_id else []
        row = next((item for item in rows if item["id"] == session_id), {})
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        observed = {
            "http_status": session["http_status"],
            "code": session["code"],
            "fixture_ready": preclean and agent["code"] == 0 and bool(agent_id),
            "id_present": bool(session_id),
            "name_matches": data.get("name") == session_name and row.get("name") == session_name,
            "dsl_present": isinstance(data.get("dsl"), dict),
            "database_count": len(rows),
            "database_mapping_matches": row.get("dialog_id") == agent_id and row.get("user_id") == owner["tenant_id"] and row.get("exp_user_id") == owner["tenant_id"] and row.get("source") == "agent",
            "json_fields_match": data.get("dsl") == row.get("dsl") and data.get("message") == row.get("message") and row.get("reference") == [],
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = agent_session_create_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_parent_through_api",
                    "code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "create_named_agent_session_through_api",
                    "http_status": session["http_status"],
                    "code": session["code"],
                    "session_id_fingerprint": DB._fingerprint(session_id),
                    "name_matches": observed["name_matches"],
                    "dsl_present": observed["dsl_present"],
                    "raw_sha256": session["raw_sha256"],
                },
                {
                    "name": "read_only_api4conversation_json_verification",
                    "database_count": len(rows),
                    "database_mapping_matches": observed["database_mapping_matches"],
                    "json_fields_match": observed["json_fields_match"],
                },
                {
                    "name": "cleanup_session_then_agent_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "source": "agent",
                "dsl_message_reference_json_roundtrip": True,
                "physical_session_row": 1,
            },
        }

    return _run_case(case_id, execute)


def run_cs063() -> dict[str, Any]:
    case_id = "TC-CS-063"
    prefix = "fresh-cs-063"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_session_list_parent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="session-list"),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        sessions = [
            _create_agent_session(
                case_id,
                group,
                owner["auth"],
                f"create_agent_session_{index}",
                agent_id,
                {"name": f"{prefix}-Session-{index}"},
            )
            for index in (1, 2, 3)
        ]
        session_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in sessions]
        database_rows = _agent_session_rows(group, agent_id) if agent_id else []
        response = _list_agent_sessions(
            case_id,
            group,
            owner["auth"],
            "list_agent_sessions_page_one",
            agent_id,
            {"page": 1, "page_size": 10},
        )
        data = response["data"] if isinstance(response["data"], list) else []
        required = {"id", "name", "source", "round", "duration", "thumb_up"}
        api_by_id = {str(item.get("id")): item for item in data if isinstance(item, dict)}
        database_by_id = {row["id"]: row for row in database_rows}
        update_times = [int(item.get("update_time") or 0) for item in data if isinstance(item, dict)]
        mapping_matches = set(api_by_id) == set(database_by_id) and all(
            api_by_id[item_id].get("name") == row["name"]
            and api_by_id[item_id].get("source") == row["source"]
            and int(api_by_id[item_id].get("round") or 0) == row["round"]
            and float(api_by_id[item_id].get("duration") or 0) == row["duration"]
            and int(api_by_id[item_id].get("thumb_up") or 0) == row["thumb_up"]
            for item_id, row in database_by_id.items()
        )
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "fixture_ready": preclean and agent["code"] == 0 and all(item["code"] == 0 for item in sessions) and len(database_rows) == 3,
            "session_count": len(data),
            "total_matches": response.get("total") == len(database_rows),
            "ids_exact": set(api_by_id) == set(session_ids),
            "required_fields_present": len(data) == 3 and all(isinstance(item, dict) and required.issubset(item) for item in data),
            "descending_order": update_times == sorted(update_times, reverse=True),
            "database_mapping_matches": mapping_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = agent_session_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_three_sessions_through_apis",
                    "agent_code": agent["code"],
                    "session_codes": [item["code"] for item in sessions],
                    "session_id_fingerprints": [DB._fingerprint(item) for item in session_ids],
                    "database_count": len(database_rows),
                },
                {
                    "name": "list_agent_sessions_page_one",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "session_count": len(data),
                    "total": response.get("total"),
                    "ids_exact": observed["ids_exact"],
                    "required_fields_present": observed["required_fields_present"],
                    "descending_order": observed["descending_order"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_session_mapping_and_api_cleanup_verification",
                    "database_mapping_matches": mapping_matches,
                    **cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "session_count": 3,
                "required_fields": sorted(required),
                "order": "update_time descending",
            },
        }

    return _run_case(case_id, execute)


def run_cs064() -> dict[str, Any]:
    case_id = "TC-CS-064"
    prefix = "fresh-cs-064"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_single_delete_parent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="single-session-delete"),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        session = _create_agent_session(
            case_id,
            group,
            owner["auth"],
            "create_single_delete_session",
            agent_id,
            {"name": f"{prefix}-Session"},
        )
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        before = _agent_session_rows(group, agent_id) if agent_id else []
        deleted = _delete_agent_session_item(
            case_id,
            group,
            owner["auth"],
            "delete_single_agent_session",
            agent_id,
            session_id,
        )
        after = _agent_session_rows(group, agent_id) if agent_id else []
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "fixture_ready": preclean and agent["code"] == 0 and session["code"] == 0 and [row["id"] for row in before] == [session_id],
            "target_count_after": sum(row["id"] == session_id for row in after),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = agent_session_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_single_session_fixture",
                    "agent_code": agent["code"],
                    "session_code": session["code"],
                    "session_id_fingerprint": DB._fingerprint(session_id),
                    "database_count_before": len(before),
                },
                {
                    "name": "delete_single_agent_session_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "read_only_physical_removal_and_agent_cleanup",
                    "target_count_after": observed["target_count_after"],
                    **cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "api4conversation_target_count_after": 0,
            },
        }

    return _run_case(case_id, execute)


def run_cs065() -> dict[str, Any]:
    case_id = "TC-CS-065"
    prefix = "fresh-cs-065"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_bulk_delete_parent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="bulk-session-delete"),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        sessions = [
            _create_agent_session(
                case_id,
                group,
                owner["auth"],
                f"create_bulk_delete_session_{index}",
                agent_id,
                {"name": f"{prefix}-Session-{index}"},
            )
            for index in (1, 2, 3)
        ]
        session_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in sessions]
        before = _agent_session_rows(group, agent_id) if agent_id else []
        targeted = session_ids[:2]
        untargeted = session_ids[2] if len(session_ids) == 3 else ""
        deleted = _bulk_delete_agent_sessions(
            case_id,
            group,
            owner["auth"],
            "bulk_delete_selected_agent_sessions",
            agent_id,
            {"ids": targeted},
        )
        after = _agent_session_rows(group, agent_id) if agent_id else []
        remaining_ids = {row["id"] for row in after}
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        observed = {
            "http_status": deleted["http_status"],
            "code": deleted["code"],
            "fixture_ready": preclean and agent["code"] == 0 and all(item["code"] == 0 for item in sessions) and len(before) == 3,
            "target_ids_absent": all(item not in remaining_ids for item in targeted),
            "untargeted_id_present": untargeted in remaining_ids,
            "remaining_count": len(after),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = agent_session_bulk_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_three_bulk_delete_sessions",
                    "agent_code": agent["code"],
                    "session_codes": [item["code"] for item in sessions],
                    "database_count_before": len(before),
                    "target_id_fingerprints": [DB._fingerprint(item) for item in targeted],
                    "untargeted_id_fingerprint": DB._fingerprint(untargeted),
                },
                {
                    "name": "bulk_delete_selected_agent_sessions_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "read_only_targeted_delete_and_survivor_verification",
                    "target_ids_absent": observed["target_ids_absent"],
                    "untargeted_id_present": observed["untargeted_id_present"],
                    "remaining_count": len(after),
                    **cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "targeted_session_count_after": 0,
                "untargeted_session_count_after": 1,
            },
        }

    return _run_case(case_id, execute)


def run_cs066() -> dict[str, Any]:
    case_id = "TC-CS-066"
    prefix = "fresh-cs-066"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_tool_logging_agent",
            title=f"{prefix}-Agent",
            dsl=_agent_tool_logging_dsl(llm_id),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        session = _create_agent_session(
            case_id,
            group,
            owner["auth"],
            "create_tool_logging_session",
            agent_id,
            {"name": f"{prefix}-Session"},
        )
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        executions = []
        selected_message_id = ""
        redis_log: dict[str, Any] = {
            "exists": False,
            "payload": None,
            "ttl": -2,
            "key_fingerprint": None,
        }
        for round_number in (1, 2, 3):
            completion = _complete_agent_nonstream(
                case_id,
                group,
                owner["auth"],
                f"execute_agent_for_log_round_{round_number}",
                agent_id,
                session_id,
                (f"Use the retrieval tool and return a brief controlled log probe answer for round {round_number}."),
            )
            completion_data = completion["data"] if isinstance(completion["data"], dict) else {}
            message_id = str(completion_data.get("message_id") or "")
            current_log = (
                _agent_log_redis_snapshot(group, agent_id, message_id)
                if message_id
                else {
                    "exists": False,
                    "payload": None,
                    "ttl": -2,
                    "key_fingerprint": None,
                }
            )
            executions.append(
                {
                    "round": round_number,
                    "response": completion,
                    "message_id": message_id,
                    "redis_log": current_log,
                }
            )
            if current_log.get("exists"):
                selected_message_id = message_id
                redis_log = current_log
                break
        log_response = _get_agent_logs(
            case_id,
            group,
            owner["auth"],
            "get_agent_execution_logs",
            agent_id,
            selected_message_id or "missing-message-id",
        )
        log_data = log_response["data"]
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        fixture_ready = preclean and agent["code"] == 0 and session["code"] == 0 and bool(agent_id) and bool(session_id)
        execution_succeeded = bool(executions) and all(item["response"]["http_status"] == 200 and item["response"]["code"] == 0 for item in executions)
        observed = {
            "http_status": log_response["http_status"],
            "code": log_response["code"],
            "fixture_ready": fixture_ready,
            "execution_succeeded": execution_succeeded,
            "message_id_present": bool(selected_message_id),
            "redis_log_present": redis_log.get("exists"),
            "log_nonempty": isinstance(log_data, (list, dict)) and bool(log_data),
            "endpoint_matches_redis": log_data == redis_log.get("payload"),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = agent_log_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_tool_logging_agent_and_session",
                    "agent_code": agent["code"],
                    "session_code": session["code"],
                    "fixture_ready": fixture_ready,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "session_id_fingerprint": DB._fingerprint(session_id),
                },
                {
                    "name": "execute_agent_until_log_is_generated",
                    "attempt_count": len(executions),
                    "response_codes": [item["response"]["code"] for item in executions],
                    "message_id_fingerprints": [DB._fingerprint(item["message_id"]) for item in executions],
                    "redis_log_observed_by_round": [bool(item["redis_log"].get("exists")) for item in executions],
                    "execution_succeeded": execution_succeeded,
                    "raw_sha256": [item["response"]["raw_sha256"] for item in executions],
                },
                {
                    "name": "get_agent_execution_logs_and_compare_redis",
                    "http_status": log_response["http_status"],
                    "code": log_response["code"],
                    "message_id_present": bool(selected_message_id),
                    "redis_log_present": redis_log.get("exists"),
                    "redis_ttl_positive": int(redis_log.get("ttl") or -2) > 0,
                    "log_nonempty": observed["log_nonempty"],
                    "endpoint_matches_redis": observed["endpoint_matches_redis"],
                    "key_fingerprint": redis_log.get("key_fingerprint"),
                    "raw_sha256": log_response["raw_sha256"],
                },
                {
                    "name": "cleanup_agent_session_then_agent_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "log_source": "Redis",
                "log_nonempty": True,
                "endpoint_matches_redis": True,
            },
        }

    return _run_case(case_id, execute)


def _run_immediate_webhook_case(
    case_id: str,
    *,
    method: str,
    payload: dict[str, Any] | None,
    params: dict[str, Any] | None,
    trace_values: tuple[str, ...],
) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    expected_body = {"accepted": True, "case": case_id}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_immediate_webhook_agent",
            title=f"{prefix}-Agent",
            dsl=_webhook_agent_dsl(expected_body),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        initial_logs = _request(
            case_id,
            group,
            "read_webhook_trace_cursor_before_trigger",
            owner["auth"],
            "GET",
            f"/agents/{agent_id}/webhook/logs",
        )
        initial_data = initial_logs["data"] if isinstance(initial_logs["data"], dict) else {}
        since_ts = float(initial_data.get("next_since_ts") or 0)
        sessions_before = len(_agent_session_rows(group, agent_id)) if agent_id else 0
        response = _webhook_request(
            case_id,
            group,
            "trigger_immediate_webhook",
            method,
            f"/agents/{agent_id}/webhook",
            payload=payload,
            params=params,
        )
        trace = _poll_webhook_trace(
            case_id,
            group,
            owner["auth"],
            agent_id,
            since_ts,
            label_prefix="poll_immediate_webhook_trace",
            max_attempts=5,
        )
        sessions_after = len(_agent_session_rows(group, agent_id)) if agent_id else 0
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "http_status": response["http_status"],
            "response_body_matches": response["body"] == expected_body,
            "fixture_ready": preclean and agent["code"] == 0 and initial_logs["code"] == 0 and since_ts > 0,
            "trace_webhook_id_present": bool(trace["webhook_id"]),
            "trace_events_present": bool(trace["events"]),
            "trace_finished": trace["finished"],
            "trace_payload_matches": _trace_contains(trace["events"], *trace_values),
            "session_database_delta": sessions_after - sessions_before,
            "cleanup_succeeded": cleanup,
        }
        passed = webhook_immediate_contract_ok(observed)
        findings = []
        if response["http_status"] == 202 and not trace["webhook_id"]:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "webhook_trace",
                    "summary": "normal immediate webhook produced no pollable background trace",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_webhook_agent_and_capture_trace_cursor",
                    "agent_code": agent["code"],
                    "initial_trace_code": initial_logs["code"],
                    "next_since_ts_present": since_ts > 0,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                },
                {
                    "name": "trigger_immediate_webhook",
                    "method": method,
                    "http_status": response["http_status"],
                    "response_body_matches": observed["response_body_matches"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "poll_background_webhook_trace",
                    "poll_count": len(trace["responses"]),
                    "poll_codes": [item["code"] for item in trace["responses"]],
                    "webhook_id_present": bool(trace["webhook_id"]),
                    "event_count": len(trace["events"]),
                    "finished": trace["finished"],
                    "payload_matches": observed["trace_payload_matches"],
                    "raw_sha256": [item["raw_sha256"] for item in trace["responses"]],
                },
                {
                    "name": "verify_no_agent_session_write_and_cleanup_agent",
                    "session_count_before": sessions_before,
                    "session_count_after": sessions_after,
                    "session_database_delta": sessions_after - sessions_before,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "http_status": 202,
                "response_body": expected_body,
                "background_trace_finished": True,
                "trace_payload_values": list(trace_values),
                "agent_session_database_delta": 0,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs067() -> dict[str, Any]:
    return _run_immediate_webhook_case(
        "TC-CS-067",
        method="POST",
        payload={"input": "webhook数据"},
        params=None,
        trace_values=("webhook数据",),
    )


def run_cs068() -> dict[str, Any]:
    return _run_immediate_webhook_case(
        "TC-CS-068",
        method="GET",
        payload=None,
        params={"param1": "value1"},
        trace_values=("param1", "value1"),
    )


def run_cs069() -> dict[str, Any]:
    case_id = "TC-CS-069"
    prefix = "fresh-cs-069"
    expected_body = {"authenticated": True, "case": case_id}
    token_header = "X-Webhook-Token"
    token_value = "secret-token-123"
    security = {
        "auth_type": "token",
        "allow_anonymous": False,
        "max_body_size": "1MB",
        "token": {"token_header": token_header, "token_value": token_value},
    }

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_token_webhook_agent",
            title=f"{prefix}-Agent",
            dsl=_webhook_agent_dsl(expected_body, response_status=200, security=security),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        valid = _webhook_request(
            case_id,
            group,
            "trigger_webhook_with_valid_token",
            "POST",
            f"/agents/{agent_id}/webhook",
            payload={},
            custom_headers={token_header: token_value},
        )
        invalid = _webhook_request(
            case_id,
            group,
            "trigger_webhook_without_token",
            "POST",
            f"/agents/{agent_id}/webhook",
            payload={},
        )
        time.sleep(0.2)
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["code"] == 0 and bool(agent_id),
            "valid_http_status": valid["http_status"],
            "valid_body_matches": valid["body"] == expected_body,
            "invalid_http_status": invalid["http_status"],
            "invalid_code": invalid["code"],
            "invalid_message_matches": "Invalid token authentication" in str(invalid["message"] or ""),
            "cleanup_succeeded": cleanup,
        }
        passed = webhook_token_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_token_authenticated_webhook_agent",
                    "agent_code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "token_header_name": token_header,
                    "token_value_present": True,
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "trigger_webhook_with_valid_token",
                    "http_status": valid["http_status"],
                    "body_matches": observed["valid_body_matches"],
                    "raw_sha256": valid["raw_sha256"],
                },
                {
                    "name": "trigger_webhook_without_token",
                    "http_status": invalid["http_status"],
                    "code": invalid["code"],
                    "message_matches": observed["invalid_message_matches"],
                    "raw_sha256": invalid["raw_sha256"],
                },
                {
                    "name": "cleanup_token_webhook_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "valid_request": [200, expected_body],
                "missing_or_invalid_token": [400, 400],
                "error_contains": "Invalid token authentication",
            },
        }

    return _run_case(case_id, execute)


def run_cs070() -> dict[str, Any]:
    case_id = "TC-CS-070"
    prefix = "fresh-cs-070"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_non_webhook_agent",
            title=f"{prefix}-Agent",
            dsl=_non_webhook_agent_dsl(),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        response = _webhook_request(
            case_id,
            group,
            "trigger_unconfigured_webhook",
            "POST",
            f"/agents/{agent_id}/webhook",
            payload={},
        )
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["code"] == 0 and bool(agent_id),
            "http_status": response["http_status"],
            "code": response["code"],
            "message": response["message"],
            "cleanup_succeeded": cleanup,
        }
        passed = webhook_unconfigured_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_conversational_non_webhook_agent",
                    "agent_code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "trigger_unconfigured_webhook",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "exact_message_matches": response["message"] == "Webhook not configured for this agent.",
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "cleanup_non_webhook_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [400, 400],
                "message": "Webhook not configured for this agent.",
            },
        }

    return _run_case(case_id, execute)


def run_cs071() -> dict[str, Any]:
    case_id = "TC-CS-071"
    prefix = "fresh-cs-071"
    email = "cs-071-user-b@fresh.invalid"
    password = "Fresh-CS-071-User-B@1234"
    expected_body = {"test": True, "case": case_id}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = _prepare_secondary_user(case_id, group, email, password)
        secondary_ready = secondary["preclean"] and secondary["registration"]["code"] == 0 and secondary["login"]["code"] == 0 and bool(secondary["auth"])
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_webhook_test_agent",
            title=f"{prefix}-Agent",
            dsl=_webhook_agent_dsl(expected_body, response_status=200),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        owner_response = _webhook_request(
            case_id,
            group,
            "owner_trigger_webhook_test",
            "POST",
            f"/agents/{agent_id}/webhook/test",
            auth=owner["auth"],
            payload={"input": "测试数据"},
        )
        nonowner_response = _webhook_request(
            case_id,
            group,
            "nonowner_trigger_webhook_test",
            "POST",
            f"/agents/{agent_id}/webhook/test",
            auth=secondary["auth"],
            payload={"input": "测试数据"},
        )
        time.sleep(0.2)
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        secondary_cleanup = _cleanup_secondary_user(case_id, group, email)
        observed = {
            "fixture_ready": preclean and secondary_ready and agent["code"] == 0 and bool(agent_id),
            "owner_http_status": owner_response["http_status"],
            "owner_body_matches": owner_response["body"] == expected_body,
            "nonowner_http_status": nonowner_response["http_status"],
            "nonowner_code": nonowner_response["code"],
            "nonowner_message": nonowner_response["message"],
            "agent_cleanup_succeeded": agent_cleanup,
            "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
        }
        passed = webhook_owner_test_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "prepare_owner_secondary_user_and_webhook_agent",
                    "secondary_ready": secondary_ready,
                    "agent_code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                },
                {
                    "name": "owner_trigger_webhook_test",
                    "http_status": owner_response["http_status"],
                    "body_matches": observed["owner_body_matches"],
                    "raw_sha256": owner_response["raw_sha256"],
                },
                {
                    "name": "nonowner_trigger_webhook_test",
                    "http_status": nonowner_response["http_status"],
                    "code": nonowner_response["code"],
                    "exact_message_matches": nonowner_response["message"] == "Only the owner of the agent is authorized for this operation.",
                    "raw_sha256": nonowner_response["raw_sha256"],
                },
                {
                    "name": "cleanup_agent_and_secondary_user_through_apis",
                    "agent_cleanup_succeeded": agent_cleanup,
                    "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "owner_response": [200, expected_body],
                "nonowner_response": [200, 103],
                "nonowner_message": "Only the owner of the agent is authorized for this operation.",
            },
        }

    return _run_case(case_id, execute)


def run_cs072() -> dict[str, Any]:
    case_id = "TC-CS-072"
    prefix = "fresh-cs-072"
    expected_body = {"trace": True, "case": case_id}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_webhook_trace_agent",
            title=f"{prefix}-Agent",
            dsl=_webhook_agent_dsl(expected_body, response_status=200),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        initial = _request(
            case_id,
            group,
            "get_initial_webhook_trace_cursor",
            owner["auth"],
            "GET",
            f"/agents/{agent_id}/webhook/logs",
        )
        initial_data = initial["data"] if isinstance(initial["data"], dict) else {}
        since_ts = float(initial_data.get("next_since_ts") or 0)
        trigger = _webhook_request(
            case_id,
            group,
            "trigger_webhook_test_for_trace",
            "POST",
            f"/agents/{agent_id}/webhook/test",
            auth=owner["auth"],
            payload={"input": "trace测试数据"},
        )
        trace = _poll_webhook_trace(
            case_id,
            group,
            owner["auth"],
            agent_id,
            since_ts,
            label_prefix="poll_webhook_test_trace",
            max_attempts=20,
        )
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["code"] == 0 and bool(agent_id),
            "initial_http_status": initial["http_status"],
            "initial_code": initial["code"],
            "next_since_ts_present": since_ts > 0,
            "trigger_http_status": trigger["http_status"],
            "webhook_id_present": bool(trace["webhook_id"]),
            "events_present": bool(trace["events"]),
            "finished": trace["finished"],
            "payload_matches": _trace_contains(trace["events"], "trace测试数据"),
            "cleanup_succeeded": cleanup,
        }
        passed = webhook_trace_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_webhook_agent_and_get_initial_cursor",
                    "agent_code": agent["code"],
                    "initial_http_status": initial["http_status"],
                    "initial_code": initial["code"],
                    "next_since_ts_present": since_ts > 0,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": initial["raw_sha256"],
                },
                {
                    "name": "trigger_webhook_test_for_trace",
                    "http_status": trigger["http_status"],
                    "body_matches": trigger["body"] == expected_body,
                    "raw_sha256": trigger["raw_sha256"],
                },
                {
                    "name": "poll_webhook_trace_until_finished",
                    "poll_count": len(trace["responses"]),
                    "webhook_id_present": bool(trace["webhook_id"]),
                    "event_count": len(trace["events"]),
                    "finished": trace["finished"],
                    "payload_matches": observed["payload_matches"],
                    "raw_sha256": [item["raw_sha256"] for item in trace["responses"]],
                },
                {
                    "name": "cleanup_webhook_trace_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "initial_cursor": True,
                "trigger_http_status": 200,
                "webhook_id_present": True,
                "events_present": True,
                "finished": True,
                "trace_contains_payload": "trace测试数据",
            },
        }

    return _run_case(case_id, execute)


def run_cs073() -> dict[str, Any]:
    case_id = "TC-CS-073"
    prefix = "fresh-cs-073"
    expected_body = {"should_not_execute": True, "case": case_id}
    limit = 1024 * 1024
    raw_body = b'{"payload":"' + (b"x" * (limit + 128)) + b'"}'

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_limited_body_webhook_agent",
            title=f"{prefix}-Agent",
            dsl=_webhook_agent_dsl(expected_body, response_status=200),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        initial = _request(
            case_id,
            group,
            "get_trace_cursor_before_oversized_body",
            owner["auth"],
            "GET",
            f"/agents/{agent_id}/webhook/logs",
        )
        initial_data = initial["data"] if isinstance(initial["data"], dict) else {}
        since_ts = float(initial_data.get("next_since_ts") or 0)
        response = _webhook_request(
            case_id,
            group,
            "submit_oversized_webhook_body",
            "POST",
            f"/agents/{agent_id}/webhook",
            raw_body=raw_body,
        )
        trace = _poll_webhook_trace(
            case_id,
            group,
            owner["auth"],
            agent_id,
            since_ts,
            label_prefix="poll_oversized_webhook_trace",
            max_attempts=3,
        )
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["code"] == 0 and initial["code"] == 0 and since_ts > 0,
            "http_status": response["http_status"],
            "code": response["code"],
            "message_matches": "Request body too large" in str(response["message"] or ""),
            "request_over_limit": len(raw_body) > limit,
            "trace_absent": not trace["webhook_id"] and not trace["events"] and not trace["finished"],
            "cleanup_succeeded": cleanup,
        }
        passed = webhook_body_size_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_one_megabyte_limited_webhook_agent",
                    "agent_code": agent["code"],
                    "next_since_ts_present": since_ts > 0,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                },
                {
                    "name": "submit_oversized_webhook_body",
                    "request_body_length": len(raw_body),
                    "configured_limit": limit,
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message_matches": observed["message_matches"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "verify_no_execution_trace_and_cleanup_agent",
                    "poll_count": len(trace["responses"]),
                    "trace_absent": observed["trace_absent"],
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [400, 400],
                "message_contains": "Request body too large",
                "request_size_greater_than": limit,
                "execution_trace": False,
            },
        }

    return _run_case(case_id, execute)


def run_cs074() -> dict[str, Any]:
    case_id = "TC-CS-074"
    prefix = "fresh-cs-074"
    prologue = "Fresh chatbot prologue."
    first_question = "你好"
    second_question = "请继续回答"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_chatbot_beta_credential")
        beta = credential["_beta"]
        token = credential["_token"]
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_shared_chatbot",
            {
                "name": f"{prefix}-Chatbot",
                "icon": "fresh-chatbot-avatar",
                "prompt_config": {"prologue": prologue},
            },
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        first = _sse_post(
            case_id,
            group,
            beta,
            "create_iframe_session_with_prologue",
            f"/chatbots/{chat_id}/completions",
            {
                "question": first_question,
                "user_id": owner["tenant_id"],
                "stream": True,
            },
        )
        first_events = first.get("events") if isinstance(first.get("events"), list) else []
        first_answers = [event["data"] for event in first_events if isinstance(event, dict) and event.get("code") == 0 and isinstance(event.get("data"), dict)]
        session_id = next(
            (str(item.get("session_id") or "") for item in first_answers if item.get("session_id")),
            "",
        )
        first_rows = [row for row in _agent_session_rows(group, chat_id) if row["id"] == session_id] if chat_id and session_id else []
        first_row = first_rows[0] if len(first_rows) == 1 else {}
        first_messages = first_row.get("message") if isinstance(first_row.get("message"), list) else []
        second = _sse_post(
            case_id,
            group,
            beta,
            "continue_existing_iframe_session",
            f"/chatbots/{chat_id}/completions",
            {
                "question": second_question,
                "user_id": owner["tenant_id"],
                "session_id": session_id,
                "stream": True,
            },
        )
        second_events = second.get("events") if isinstance(second.get("events"), list) else []
        second_answers = [
            event["data"]
            for event in second_events
            if isinstance(event, dict)
            and event.get("code") == 0
            and isinstance(event.get("data"), dict)
            and bool(str(event["data"].get("answer") or "").strip())
            and not str(event["data"].get("answer") or "").startswith("**ERROR**")
        ]
        second_rows = [row for row in _agent_session_rows(group, chat_id) if row["id"] == session_id] if chat_id and session_id else []
        second_row = second_rows[0] if len(second_rows) == 1 else {}
        second_messages = second_row.get("message") if isinstance(second_row.get("message"), list) else []
        second_user = next(
            (item for item in reversed(second_messages) if isinstance(item, dict) and item.get("role") == "user" and item.get("content") == second_question),
            {},
        )
        second_assistant = next(
            (
                item
                for item in reversed(second_messages)
                if isinstance(item, dict) and item.get("role") == "assistant" and item.get("id") == second_user.get("id") and bool(str(item.get("content") or "").strip())
            ),
            {},
        )
        chat_cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        retained_iframe_session_count = len([row for row in _agent_session_rows(group, chat_id) if row["id"] == session_id]) if chat_id and session_id else 0
        observed = {
            "fixture_ready": preclean and credential["code"] == 0 and len(beta) == 32 and credential["_tenant_id"] == owner["tenant_id"] and chat["code"] == 0 and bool(chat_id),
            "first_http_status": first["http_status"],
            "first_content_type_is_sse": "text/event-stream" in str(first.get("content_type") or ""),
            "first_parse_error_count": first.get("parse_error_count"),
            "prologue_matches": any(item.get("answer") == prologue for item in first_answers),
            "session_id_present": bool(session_id),
            "first_final_event_matches": _sse_final_event_matches(first_events),
            "first_database_mapping_matches": len(first_rows) == 1
            and first_row.get("user_id") == owner["tenant_id"]
            and first_messages
            == [
                {
                    "role": "assistant",
                    "content": prologue,
                    "created_at": first_messages[0].get("created_at"),
                }
            ]
            if len(first_messages) == 1 and isinstance(first_messages[0], dict)
            else False,
            "second_http_status": second["http_status"],
            "second_content_type_is_sse": "text/event-stream" in str(second.get("content_type") or ""),
            "second_parse_error_count": second.get("parse_error_count"),
            "second_answer_nonempty": bool(second_answers),
            "second_final_event_matches": _sse_final_event_matches(second_events),
            "second_database_history_matches": bool(second_user) and bool(second_assistant),
            "token_cleanup_succeeded": token_cleanup,
            "chat_cleanup_succeeded": chat_cleanup,
        }
        passed = chatbot_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chatbot_and_beta_credential_through_apis",
                    "chat_code": chat["code"],
                    "credential_create_code": credential["code"],
                    "credential_length": len(beta),
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "credential_fingerprint": DB._fingerprint(beta),
                },
                {
                    "name": "create_iframe_session_and_receive_prologue_sse",
                    "http_status": first["http_status"],
                    "event_count": len(first_events),
                    "parse_error_count": first.get("parse_error_count"),
                    "prologue_matches": observed["prologue_matches"],
                    "session_id_fingerprint": DB._fingerprint(session_id),
                    "database_mapping_matches": observed["first_database_mapping_matches"],
                    "raw_sha256": first["raw_sha256"],
                },
                {
                    "name": "continue_iframe_session_and_verify_persisted_history",
                    "http_status": second["http_status"],
                    "event_count": len(second_events),
                    "parse_error_count": second.get("parse_error_count"),
                    "answer_nonempty": observed["second_answer_nonempty"],
                    "database_history_matches": observed["second_database_history_matches"],
                    "raw_sha256": second["raw_sha256"],
                },
                {
                    "name": "cleanup_token_and_chat_through_apis",
                    "credential_cleanup_succeeded": token_cleanup,
                    "chat_cleanup_succeeded": chat_cleanup,
                    "iframe_session_retained_without_delete_api": retained_iframe_session_count,
                    "sql_cleanup_used": False,
                },
            ],
            "oracle": {
                "first_stream": "prologue plus session_id plus terminal true",
                "second_stream": "nonempty assistant answer plus terminal true",
                "database_history": "prologue, user, assistant",
                "cleanup": "business APIs only",
            },
        }

    return _run_case(case_id, execute)


def run_cs075() -> dict[str, Any]:
    case_id = "TC-CS-075"
    prefix = "fresh-cs-075"
    title = f"{prefix}-Chatbot"
    avatar = "fresh-chatbot-info-avatar"
    prologue = "Fresh chatbot info prologue."

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_chatbot_info_beta_credential")
        beta = credential["_beta"]
        token = credential["_token"]
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chatbot_for_info",
            {
                "name": title,
                "icon": avatar,
                "prompt_config": {
                    "prologue": prologue,
                    "tavily_api_key": "",
                },
            },
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        info = _request(
            case_id,
            group,
            "get_chatbot_info_with_beta_credential",
            beta,
            "GET",
            f"/chatbots/{chat_id}/info",
        )
        data = info["data"] if isinstance(info["data"], dict) else {}
        chat_cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        observed = {
            "fixture_ready": preclean and credential["code"] == 0 and len(beta) == 32 and chat["code"] == 0 and bool(chat_id),
            "http_status": info["http_status"],
            "code": info["code"],
            "required_fields_exact": set(data) == {"title", "avatar", "prologue", "llm_id", "has_tavily_key"},
            "title_matches": data.get("title") == title,
            "avatar_matches": data.get("avatar") == avatar,
            "prologue_matches": data.get("prologue") == prologue,
            "llm_id_matches": data.get("llm_id") == llm_id,
            "has_tavily_key_matches": data.get("has_tavily_key") is False,
            "token_cleanup_succeeded": token_cleanup,
            "chat_cleanup_succeeded": chat_cleanup,
        }
        passed = chatbot_info_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chatbot_and_beta_credential",
                    "chat_code": chat["code"],
                    "credential_create_code": credential["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "credential_fingerprint": DB._fingerprint(beta),
                },
                {
                    "name": "get_chatbot_info_and_compare_persisted_metadata",
                    "http_status": info["http_status"],
                    "code": info["code"],
                    "required_fields_exact": observed["required_fields_exact"],
                    "all_values_match": all(
                        observed[key]
                        for key in (
                            "title_matches",
                            "avatar_matches",
                            "prologue_matches",
                            "llm_id_matches",
                            "has_tavily_key_matches",
                        )
                    ),
                    "raw_sha256": info["raw_sha256"],
                },
                {
                    "name": "cleanup_token_and_chat_through_apis",
                    "credential_cleanup_succeeded": token_cleanup,
                    "chat_cleanup_succeeded": chat_cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "fields": [
                    "title",
                    "avatar",
                    "prologue",
                    "llm_id",
                    "has_tavily_key",
                ],
            },
        }

    return _run_case(case_id, execute)


def run_cs076() -> dict[str, Any]:
    case_id = "TC-CS-076"
    prefix = "fresh-cs-076"
    query = "测试agentbot"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_agentbot_beta_credential")
        beta = credential["_beta"]
        token = credential["_token"]
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_shared_agentbot",
            title=f"{prefix}-Agentbot",
            dsl=_agent_dsl(probe="agentbot-completion"),
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        streamed = _sse_post(
            case_id,
            group,
            beta,
            "stream_agentbot_completion",
            f"/agentbots/{agent_id}/completions",
            {"query": query, "stream": True},
        )
        events = streamed.get("events") if isinstance(streamed.get("events"), list) else []
        message_events = [
            event for event in events if isinstance(event, dict) and event.get("event") == "message" and isinstance(event.get("data"), dict) and bool(str(event["data"].get("content") or "").strip())
        ]
        session_id = next(
            (str(event.get("session_id") or "") for event in events if isinstance(event, dict) and event.get("session_id")),
            "",
        )
        rows = [row for row in _agent_session_rows(group, agent_id) if row["id"] == session_id] if agent_id and session_id else []
        row = rows[0] if len(rows) == 1 else {}
        messages = row.get("message") if isinstance(row.get("message"), list) else []
        user_message = next(
            (item for item in messages if isinstance(item, dict) and item.get("role") == "user" and item.get("content") == query),
            {},
        )
        assistant_message = next(
            (item for item in messages if isinstance(item, dict) and item.get("role") == "assistant" and item.get("id") == user_message.get("id") and bool(str(item.get("content") or "").strip())),
            {},
        )
        error_event_absent = not any(isinstance(event, dict) and (event.get("code") not in (None, 0) or str((event.get("data") or {}).get("content") or "").startswith("Error ")) for event in events)
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        observed = {
            "fixture_ready": preclean and credential["code"] == 0 and len(beta) == 32 and agent["code"] == 0 and bool(agent_id),
            "http_status": streamed["http_status"],
            "content_type_is_sse": "text/event-stream" in str(streamed.get("content_type") or ""),
            "parse_error_count": streamed.get("parse_error_count"),
            "event_count_positive": bool(events),
            "message_event_nonempty": bool(message_events),
            "error_event_absent": error_event_absent,
            "session_id_present": bool(session_id),
            "database_message_pair_matches": len(rows) == 1 and bool(user_message) and bool(assistant_message),
            "session_cleanup_succeeded": cleanup["sessions_removed"],
            "agent_cleanup_succeeded": cleanup["agent_removed"],
            "token_cleanup_succeeded": token_cleanup,
        }
        passed = agentbot_completion_contract_ok(observed)
        findings = []
        serialized_events = json.dumps(events, ensure_ascii=False, sort_keys=True)
        if not error_event_absent and "user_id" in serialized_events and "not-null constraint" in serialized_events:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "gaussdb_empty_string_agentbot_session",
                    "summary": ("Agentbot omits user_id, and GaussDB converts the default empty string to NULL for a NOT NULL session column"),
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agentbot_and_beta_credential",
                    "agent_code": agent["code"],
                    "credential_create_code": credential["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "credential_fingerprint": DB._fingerprint(beta),
                },
                {
                    "name": "stream_agentbot_completion_with_beta_credential",
                    "http_status": streamed["http_status"],
                    "content_type_is_sse": observed["content_type_is_sse"],
                    "event_count": len(events),
                    "parse_error_count": streamed.get("parse_error_count"),
                    "message_event_nonempty": bool(message_events),
                    "error_event_absent": error_event_absent,
                    "session_id_fingerprint": DB._fingerprint(session_id),
                    "raw_sha256": streamed["raw_sha256"],
                },
                {
                    "name": "read_only_agentbot_session_message_verification",
                    "database_session_count": len(rows),
                    "user_message_matches": bool(user_message),
                    "assistant_message_matches": bool(assistant_message),
                },
                {
                    "name": "cleanup_session_agent_and_token_through_apis",
                    **cleanup,
                    "credential_cleanup_succeeded": token_cleanup,
                },
            ],
            "oracle": {
                "http_status": 200,
                "content_type": "text/event-stream",
                "message_event_nonempty": True,
                "database_message_pair": True,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs077() -> dict[str, Any]:
    case_id = "TC-CS-077"
    prefix = "fresh-cs-077"
    prologue = "Fresh agentbot input prologue."
    input_form = {
        "topic": {
            "type": "string",
            "label": "Topic",
            "optional": False,
        }
    }

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_agentbot_inputs_beta_credential")
        beta = credential["_beta"]
        token = credential["_token"]
        dsl = _agent_dsl(probe="agentbot-inputs")
        dsl["components"]["begin"]["obj"]["params"] = {
            "mode": "task",
            "prologue": prologue,
            "inputs": input_form,
        }
        title = f"{prefix}-Agentbot"
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agentbot_with_input_form",
            title=title,
            dsl=dsl,
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        info = _request(
            case_id,
            group,
            "get_agentbot_input_form",
            beta,
            "GET",
            f"/agentbots/{agent_id}/inputs",
        )
        data = info["data"] if isinstance(info["data"], dict) else {}
        required_fields = {"input_form", "prologue", "mode", "title", "avatar"}
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        observed = {
            "fixture_ready": preclean and credential["code"] == 0 and len(beta) == 32 and agent["code"] == 0 and bool(agent_id),
            "http_status": info["http_status"],
            "code": info["code"],
            "required_fields_present": required_fields.issubset(data),
            "input_form_matches": data.get("input_form") == input_form,
            "prologue_matches": data.get("prologue") == prologue,
            "mode_matches": data.get("mode") == "task",
            "title_matches": data.get("title") == title,
            "avatar_matches": data.get("avatar") == agent_data.get("avatar", ""),
            "agent_cleanup_succeeded": agent_cleanup,
            "token_cleanup_succeeded": token_cleanup,
        }
        passed = agentbot_inputs_contract_ok(observed)
        findings = []
        if info["http_status"] == 200 and info["code"] == 0 and "inputs" in data and "input_form" not in data:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "agentbot_inputs_contract",
                    "summary": "endpoint returns inputs instead of plan-required input_form",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agentbot_with_input_form_and_beta_credential",
                    "agent_code": agent["code"],
                    "credential_create_code": credential["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                },
                {
                    "name": "get_agentbot_inputs_and_validate_plan_contract",
                    "http_status": info["http_status"],
                    "code": info["code"],
                    "response_field_names": sorted(data),
                    "required_fields_present": observed["required_fields_present"],
                    "input_form_matches": observed["input_form_matches"],
                    "other_metadata_matches": all(
                        observed[key]
                        for key in (
                            "prologue_matches",
                            "mode_matches",
                            "title_matches",
                            "avatar_matches",
                        )
                    ),
                    "raw_sha256": info["raw_sha256"],
                },
                {
                    "name": "cleanup_agent_and_token_through_apis",
                    "agent_cleanup_succeeded": agent_cleanup,
                    "credential_cleanup_succeeded": token_cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "fields": [
                    "input_form",
                    "prologue",
                    "mode",
                    "title",
                    "avatar",
                ],
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs078() -> dict[str, Any]:
    case_id = "TC-CS-078"
    prefix = "fresh-cs-078"
    question = "什么是向量检索？"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        search_preclean = _cleanup_search_prefix(case_id, group, owner["auth"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_searchbot_ask_beta_credential")
        beta = credential["_beta"]
        token = credential["_token"]
        dataset = _dataset_fixture(
            case_id,
            group,
            owner,
            f"{prefix}-dataset",
            with_chunk=True,
            chunk_content=searchbot_ask_fixture_content(),
        )
        search = _create_search_app(
            case_id,
            group,
            owner["auth"],
            "create_deterministic_searchbot_app",
            f"{prefix}-Searchbot",
            dataset["id"],
        )
        search_data = search["data"] if isinstance(search["data"], dict) else {}
        search_id = str(search_data.get("search_id") or "")
        streamed = _sse_post(
            case_id,
            group,
            beta,
            "ask_searchbot_with_dataset",
            "/searchbots/ask",
            {
                "question": question,
                "kb_ids": [dataset["id"]],
                "search_id": search_id,
            },
            timeout=180,
        )
        events = streamed.get("events") if isinstance(streamed.get("events"), list) else []
        answer_events = [
            event["data"]
            for event in events
            if isinstance(event, dict)
            and event.get("code") == 0
            and isinstance(event.get("data"), dict)
            and bool(str(event["data"].get("answer") or "").strip())
            and not str(event["data"].get("answer") or "").startswith("**ERROR**")
        ]
        error_event_absent = not any(
            isinstance(event, dict) and (event.get("code") not in (0, None) or (isinstance(event.get("data"), dict) and str(event["data"].get("answer") or "").startswith("**ERROR**")))
            for event in events
        )
        expected_chunk_referenced = bool(dataset["chunk_id"]) and dataset["chunk_id"] in json.dumps(events, ensure_ascii=False, sort_keys=True)
        search_count_before_cleanup = _search_app_count(group, search_id) if search_id else 0
        search_cleanup = _cleanup_search_app(case_id, group, owner["auth"], search_id)
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        observed = {
            "fixture_ready": search_preclean and credential["code"] == 0 and len(beta) == 32 and dataset["ready"] and search["code"] == 0 and bool(search_id) and search_count_before_cleanup == 1,
            "http_status": streamed["http_status"],
            "content_type_is_sse": "text/event-stream" in str(streamed.get("content_type") or ""),
            "parse_error_count": streamed.get("parse_error_count"),
            "answer_nonempty": bool(answer_events),
            "error_event_absent": error_event_absent,
            "expected_chunk_referenced": expected_chunk_referenced,
            "final_event_matches": _sse_final_event_matches(events),
            "search_cleanup_succeeded": search_cleanup,
            "dataset_cleanup_succeeded": dataset_cleanup,
            "token_cleanup_succeeded": token_cleanup,
        }
        passed = searchbot_ask_contract_ok(observed)
        findings = []
        if group == "experiment" and not dataset["ready"]:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "gauss_docengine_unicode_chunk",
                    "summary": ("valid Unicode chunk creation did not produce one readable DocEngine chunk for the Searchbot fixture"),
                }
            )
        if streamed["http_status"] == 200 and not error_event_absent:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "searchbot_ask",
                    "summary": "searchbot SSE returned an execution error event",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_searchbot_app_dataset_chunk_and_beta_credential",
                    "dataset_ready": dataset["ready"],
                    "api_codes": dataset["api_codes"],
                    "credential_create_code": credential["code"],
                    "search_code": search["code"],
                    "search_preclean_succeeded": search_preclean,
                    "search_id_fingerprint": DB._fingerprint(search_id),
                    "dataset_id_fingerprint": DB._fingerprint(dataset["id"]),
                    "chunk_id_fingerprint": DB._fingerprint(dataset["chunk_id"]),
                },
                {
                    "name": "ask_searchbot_and_parse_sse",
                    "http_status": streamed["http_status"],
                    "content_type_is_sse": observed["content_type_is_sse"],
                    "event_count": len(events),
                    "parse_error_count": streamed.get("parse_error_count"),
                    "answer_nonempty": bool(answer_events),
                    "error_event_absent": error_event_absent,
                    "expected_chunk_referenced": expected_chunk_referenced,
                    "final_event_matches": observed["final_event_matches"],
                    "raw_sha256": streamed["raw_sha256"],
                },
                {
                    "name": "cleanup_search_dataset_and_token_through_apis",
                    "search_cleanup_succeeded": search_cleanup,
                    "dataset_cleanup_succeeded": dataset_cleanup,
                    "credential_cleanup_succeeded": token_cleanup,
                },
            ],
            "oracle": {
                "http_status": 200,
                "content_type": "text/event-stream",
                "answer_nonempty": True,
                "expected_chunk_referenced": True,
                "terminal_event": {"code": 0, "message": "", "data": True},
                "search_config": searchbot_fixture_config("<dataset_id>"),
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs079() -> dict[str, Any]:
    case_id = "TC-CS-079"
    prefix = "fresh-cs-079"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        credential = _create_beta_credential(
            case_id,
            group,
            owner["auth"],
            "create_searchbot_retrieval_beta_credential",
        )
        beta = credential["_beta"]
        token = credential["_token"]
        dataset = _dataset_fixture(
            case_id,
            group,
            owner,
            f"{prefix}-dataset",
            with_chunk=True,
            chunk_content=searchbot_retrieval_fixture_content(),
        )
        response = _request(
            case_id,
            group,
            "run_searchbot_retrieval_test",
            beta,
            "POST",
            "/searchbots/retrieval_test",
            payload={
                "question": "文档解析流程",
                "kb_id": dataset["id"],
                "similarity_threshold": 0.0,
            },
            timeout=180,
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        chunks = data.get("chunks") if isinstance(data.get("chunks"), list) else []
        expected_chunk_present = bool(dataset["chunk_id"]) and dataset["chunk_id"] in json.dumps(chunks, ensure_ascii=False, sort_keys=True)
        score_keys = {
            "similarity",
            "vector_similarity",
            "term_similarity",
            "score",
        }
        scores_present = bool(chunks) and all(any(key in chunk and isinstance(chunk.get(key), (int, float)) for key in score_keys) for chunk in chunks if isinstance(chunk, dict))
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        observed = {
            "fixture_ready": credential["code"] == 0 and len(beta) == 32 and dataset["ready"],
            "http_status": response["http_status"],
            "code": response["code"],
            "chunks_nonempty": bool(chunks),
            "expected_chunk_present": expected_chunk_present,
            "scores_present": scores_present,
            "dataset_cleanup_succeeded": dataset_cleanup,
            "token_cleanup_succeeded": token_cleanup,
        }
        passed = searchbot_retrieval_contract_ok(observed)
        findings = []
        if response["http_status"] == 200 and response["code"] != 0:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "searchbot_retrieval",
                    "summary": "retrieval_test returned a business error for a valid fresh chunk",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_retrieval_dataset_chunk_and_beta_credential",
                    "dataset_ready": dataset["ready"],
                    "api_codes": dataset["api_codes"],
                    "credential_create_code": credential["code"],
                    "dataset_id_fingerprint": DB._fingerprint(dataset["id"]),
                    "chunk_id_fingerprint": DB._fingerprint(dataset["chunk_id"]),
                },
                {
                    "name": "run_searchbot_retrieval_test_and_validate_chunks",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "chunk_count": len(chunks),
                    "expected_chunk_present": expected_chunk_present,
                    "scores_present": scores_present,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_and_token_through_apis",
                    "dataset_cleanup_succeeded": dataset_cleanup,
                    "credential_cleanup_succeeded": token_cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "chunks_nonempty": True,
                "expected_chunk_present": True,
                "numeric_score_present": True,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs080() -> dict[str, Any]:
    case_id = "TC-CS-080"
    prefix = "fresh-cs-080"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        search_preclean = _cleanup_search_prefix(case_id, group, owner["auth"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_searchbot_mindmap_beta_credential")
        beta = credential["_beta"]
        token = credential["_token"]
        dataset = _dataset_fixture(
            case_id,
            group,
            owner,
            f"{prefix}-dataset",
            with_chunk=True,
            chunk_content=searchbot_mindmap_fixture_content(),
        )
        search = _create_search_app(
            case_id,
            group,
            owner["auth"],
            "create_deterministic_mindmap_search_app",
            f"{prefix}-Searchbot",
            dataset["id"],
        )
        search_data = search["data"] if isinstance(search["data"], dict) else {}
        search_id = str(search_data.get("search_id") or "")
        response = _request(
            case_id,
            group,
            "generate_searchbot_mindmap",
            beta,
            "POST",
            "/searchbots/mindmap",
            payload={
                "question": "系统架构",
                "kb_ids": [dataset["id"]],
                "search_id": search_id,
            },
            timeout=240,
        )
        data = response["data"]
        structure_valid = isinstance(data, dict) and isinstance(data.get("id"), str) and bool(data.get("id")) and isinstance(data.get("children"), list)
        search_count_before_cleanup = _search_app_count(group, search_id) if search_id else 0
        search_cleanup = _cleanup_search_app(case_id, group, owner["auth"], search_id)
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        token_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], token)
        observed = {
            "fixture_ready": search_preclean and credential["code"] == 0 and len(beta) == 32 and dataset["ready"] and search["code"] == 0 and bool(search_id) and search_count_before_cleanup == 1,
            "http_status": response["http_status"],
            "code": response["code"],
            "mindmap_nonempty": isinstance(data, dict) and bool(data),
            "mindmap_structure_valid": structure_valid,
            "search_cleanup_succeeded": search_cleanup,
            "dataset_cleanup_succeeded": dataset_cleanup,
            "token_cleanup_succeeded": token_cleanup,
        }
        passed = searchbot_mindmap_contract_ok(observed)
        findings = []
        if group == "experiment" and not dataset["ready"]:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "gauss_docengine_unicode_chunk",
                    "summary": ("valid Unicode chunk creation did not produce one readable DocEngine chunk for the Mindmap fixture"),
                }
            )
        if response["http_status"] == 200 and response["code"] != 0:
            findings.append(
                {
                    "type": "product_defect",
                    "area": "searchbot_mindmap",
                    "summary": "mindmap generation returned a business error for a valid fresh chunk",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_mindmap_search_app_dataset_and_beta_credential",
                    "dataset_ready": dataset["ready"],
                    "api_codes": dataset["api_codes"],
                    "credential_create_code": credential["code"],
                    "search_code": search["code"],
                    "search_preclean_succeeded": search_preclean,
                    "search_id_fingerprint": DB._fingerprint(search_id),
                    "dataset_id_fingerprint": DB._fingerprint(dataset["id"]),
                },
                {
                    "name": "generate_searchbot_mindmap_and_validate_structure",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "mindmap_nonempty": observed["mindmap_nonempty"],
                    "root_id_present": isinstance(data, dict) and bool(data.get("id")),
                    "children_is_list": isinstance(data, dict) and isinstance(data.get("children"), list),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "cleanup_search_dataset_and_token_through_apis",
                    "search_cleanup_succeeded": search_cleanup,
                    "dataset_cleanup_succeeded": dataset_cleanup,
                    "credential_cleanup_succeeded": token_cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "structure": {"id": "nonempty string", "children": "list"},
                "search_config": searchbot_fixture_config("<dataset_id>"),
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs081() -> dict[str, Any]:
    return _run_empty_chat_field_case("TC-CS-081", "llm_id")


def run_cs082() -> dict[str, Any]:
    return _run_empty_chat_field_case("TC-CS-082", "rerank_id")


def run_cs083() -> dict[str, Any]:
    case_id = "TC-CS-083"
    prefix = "fresh-cs-083"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_model_roundtrip",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(created_data.get("id") or "")
        initial_clear = _put_chat(
            case_id,
            group,
            owner["auth"],
            "establish_initial_empty_llm",
            chat_id,
            {"llm_id": ""},
        )
        initial_detail = _get_chat(
            case_id,
            group,
            owner["auth"],
            "read_initial_empty_llm",
            chat_id,
        )
        initial_data = initial_detail["data"] if isinstance(initial_detail["data"], dict) else {}
        initial_snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        valid_update = _put_chat(
            case_id,
            group,
            owner["auth"],
            "update_empty_llm_to_valid_value",
            chat_id,
            {"llm_id": llm_id},
        )
        valid_detail = _get_chat(
            case_id,
            group,
            owner["auth"],
            "read_valid_llm_value",
            chat_id,
        )
        valid_data = valid_detail["data"] if isinstance(valid_detail["data"], dict) else {}
        valid_snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        final_clear = _put_chat(
            case_id,
            group,
            owner["auth"],
            "clear_valid_llm_again",
            chat_id,
            {"llm_id": ""},
        )
        final_detail = _get_chat(
            case_id,
            group,
            owner["auth"],
            "read_final_empty_llm",
            chat_id,
        )
        final_data = final_detail["data"] if isinstance(final_detail["data"], dict) else {}
        final_snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        observed = {
            "fixture_ready": preclean and created["code"] == 0 and initial_clear["code"] == 0 and initial_detail["code"] == 0,
            "initial_api_empty": initial_data.get("llm_id") == "" and initial_snapshot.get("llm_id") == "",
            "initial_physical_empty": initial_snapshot.get("physical_llm_is_empty"),
            "initial_physical_null": initial_snapshot.get("physical_llm_is_null"),
            "valid_update_response": [valid_update["http_status"], valid_update["code"]],
            "valid_api_matches": valid_data.get("llm_id") == llm_id,
            "valid_database_matches": valid_snapshot.get("llm_id") == llm_id and not valid_snapshot.get("physical_llm_is_empty") and not valid_snapshot.get("physical_llm_is_null"),
            "final_update_response": [final_clear["http_status"], final_clear["code"]],
            "final_api_empty": final_data.get("llm_id") == "" and final_snapshot.get("llm_id") == "",
            "final_physical_empty": final_snapshot.get("physical_llm_is_empty"),
            "final_physical_null": final_snapshot.get("physical_llm_is_null"),
            "cleanup_succeeded": cleanup,
        }
        passed = chat_model_roundtrip_contract_ok(group, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_and_establish_initial_empty_llm",
                    "create_code": created["code"],
                    "clear_code": initial_clear["code"],
                    "api_empty": observed["initial_api_empty"],
                    "physical_empty": observed["initial_physical_empty"],
                    "physical_null": observed["initial_physical_null"],
                    "raw_sha256": [
                        created["raw_sha256"],
                        initial_clear["raw_sha256"],
                        initial_detail["raw_sha256"],
                    ],
                },
                {
                    "name": "update_empty_llm_to_valid_and_verify_database",
                    "http_status": valid_update["http_status"],
                    "code": valid_update["code"],
                    "api_matches": observed["valid_api_matches"],
                    "database_matches": observed["valid_database_matches"],
                    "raw_sha256": [
                        valid_update["raw_sha256"],
                        valid_detail["raw_sha256"],
                    ],
                },
                {
                    "name": "clear_valid_llm_and_verify_group_storage",
                    "http_status": final_clear["http_status"],
                    "code": final_clear["code"],
                    "api_empty": observed["final_api_empty"],
                    "physical_empty": observed["final_physical_empty"],
                    "physical_null": observed["final_physical_null"],
                    "raw_sha256": [
                        final_clear["raw_sha256"],
                        final_detail["raw_sha256"],
                    ],
                },
                {
                    "name": "soft_delete_chat_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "control_roundtrip": "empty string -> valid string -> empty string",
                "experiment_roundtrip": "NULL -> valid string -> NULL",
                "api_empty_value": "",
            },
        }

    return _run_case(case_id, execute)


def run_cs084() -> dict[str, Any]:
    case_id = "TC-CS-084"
    prefix = "fresh-cs-084"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_nested_empty_json",
            {
                "name": prefix,
                "prompt_config": {
                    "prologue": "preserve nested prologue",
                    "system": "nonempty before patch",
                    "empty_response": "nonempty before patch",
                },
            },
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(created_data.get("id") or "")
        patched = _patch_chat(
            case_id,
            group,
            owner["auth"],
            "patch_nested_json_values_to_empty_strings",
            chat_id,
            {"prompt_config": {"system": "", "empty_response": ""}},
        )
        data = patched["data"] if isinstance(patched["data"], dict) else {}
        api_prompt = data.get("prompt_config") if isinstance(data.get("prompt_config"), dict) else {}
        snapshot = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        db_prompt = snapshot.get("prompt_config") if isinstance(snapshot.get("prompt_config"), dict) else {}
        cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        observed = {
            "fixture_ready": preclean and created["code"] == 0 and bool(chat_id),
            "http_status": patched["http_status"],
            "code": patched["code"],
            "api_values_are_empty_strings": api_prompt.get("system") == "" and api_prompt.get("empty_response") == "",
            "database_values_are_empty_strings": db_prompt.get("system") == "" and db_prompt.get("empty_response") == "",
            "other_prompt_fields_preserved": api_prompt.get("prologue") == "preserve nested prologue" and db_prompt.get("prologue") == "preserve nested prologue",
            "cleanup_succeeded": cleanup,
        }
        passed = nested_json_empty_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_with_nonempty_nested_json_values",
                    "code": created["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "patch_system_and_empty_response_to_json_empty_strings",
                    "http_status": patched["http_status"],
                    "code": patched["code"],
                    "api_values_are_empty_strings": observed["api_values_are_empty_strings"],
                    "raw_sha256": patched["raw_sha256"],
                },
                {
                    "name": "read_only_nested_json_storage_verification",
                    "database_values_are_empty_strings": observed["database_values_are_empty_strings"],
                    "other_prompt_fields_preserved": observed["other_prompt_fields_preserved"],
                },
                {
                    "name": "soft_delete_chat_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "prompt_config.system": "",
                "prompt_config.empty_response": "",
                "column_level_empty_field_conversion": False,
            },
        }

    return _run_case(case_id, execute)


def run_cs085() -> dict[str, Any]:
    case_id = "TC-CS-085"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        before = _tenant_active_chat_count(group, owner["tenant_id"]) + _visible_agent_count(group, owner["tenant_id"])
        chat = _webhook_request(
            case_id,
            group,
            "get_chats_without_authorization_header",
            "GET",
            "/chats",
        )
        agent = _webhook_request(
            case_id,
            group,
            "create_agent_without_authorization_header",
            "POST",
            "/agents",
            payload={
                "title": "fresh-cs-085-unauthenticated",
                "tags": "unauthenticated",
                "dsl": _agent_dsl(probe="unauthenticated"),
            },
        )
        after = _tenant_active_chat_count(group, owner["tenant_id"]) + _visible_agent_count(group, owner["tenant_id"])
        observed = {
            "chat_authorization_header_absent": True,
            "chat_http_status": chat["http_status"],
            "agent_authorization_header_absent": True,
            "agent_http_status": agent["http_status"],
            "database_delta": after - before,
        }
        passed = unauthenticated_chat_agent_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "get_chats_with_no_bearer_header",
                    "bearer_header_present": False,
                    "http_status": chat["http_status"],
                    "code": chat["code"],
                    "raw_sha256": chat["raw_sha256"],
                },
                {
                    "name": "post_agent_with_no_bearer_header",
                    "bearer_header_present": False,
                    "http_status": agent["http_status"],
                    "code": agent["code"],
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "read_only_no_business_row_creation_verification",
                    "before_count": before,
                    "after_count": after,
                    "database_delta": after - before,
                },
            ],
            "oracle": {
                "chat_http_status": 401,
                "agent_http_status": 401,
                "authorization_header": "absent",
                "database_delta": 0,
            },
        }

    return _run_case(case_id, execute)


def run_cs086() -> dict[str, Any]:
    case_id = "TC-CS-086"
    names = ["Chat<script>alert(1)</script>", "Chat🎉Emoji"]

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        existing_ids = _active_chat_ids_by_names(group, owner["tenant_id"], names)
        if existing_ids:
            preclean_response = _bulk_delete_chats(
                case_id,
                group,
                owner["auth"],
                "cleanup_existing_special_name_chats",
                {"ids": existing_ids},
            )
            preclean_code = preclean_response["code"]
        else:
            preclean_code = 0
        preclean = preclean_code == 0 and not _active_chat_ids_by_names(group, owner["tenant_id"], names)
        responses = [
            _create_chat(
                case_id,
                group,
                owner["auth"],
                f"create_special_name_chat_{index}",
                {"name": name},
            )
            for index, name in enumerate(names, start=1)
        ]
        data_rows = [response["data"] if isinstance(response["data"], dict) else {} for response in responses]
        chat_ids = [str(data.get("id") or "") for data in data_rows]
        snapshots = [_chat_snapshot(group, chat_id) if chat_id else {"count": 0} for chat_id in chat_ids]
        cleanup_response = _bulk_delete_chats(
            case_id,
            group,
            owner["auth"],
            "cleanup_special_name_chats",
            {"ids": [chat_id for chat_id in chat_ids if chat_id]},
        )
        cleanup = cleanup_response["code"] == 0 and not _active_chat_ids_by_names(group, owner["tenant_id"], names)
        observed = {
            "fixture_ready": preclean and all(chat_ids),
            "response_statuses": [[response["http_status"], response["code"]] for response in responses],
            "api_names_exact": [data.get("name") for data in data_rows] == names,
            "database_names_exact": [snapshot.get("name") for snapshot in snapshots] == names,
            "utf8_byte_lengths_valid": all(len(name.encode("utf-8")) <= 255 for name in names),
            "cleanup_succeeded": cleanup,
        }
        passed = special_chat_names_contract_ok(observed)
        findings = []
        if any(response["code"] != 0 and "Illegal mix of collations" in str(response.get("message") or "") for response in responses):
            findings.append(
                {
                    "type": "product_defect",
                    "area": "mysql_chat_name_collation",
                    "summary": ("emoji duplicate-name lookup mixes utf8mb4 and utf8mb3 collations and rejects an otherwise valid name"),
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "preclean_exact_special_names_through_api",
                    "existing_count": len(existing_ids),
                    "cleanup_code": preclean_code,
                    "preclean_succeeded": preclean,
                },
                {
                    "name": "create_xss_literal_and_emoji_chat_names",
                    "response_statuses": observed["response_statuses"],
                    "api_names_exact": observed["api_names_exact"],
                    "utf8_byte_lengths": [len(name.encode("utf-8")) for name in names],
                    "chat_id_fingerprints": [DB._fingerprint(chat_id) for chat_id in chat_ids],
                    "raw_sha256": [response["raw_sha256"] for response in responses],
                },
                {
                    "name": "read_only_exact_database_name_verification",
                    "database_counts": [snapshot.get("count") for snapshot in snapshots],
                    "database_names_exact": observed["database_names_exact"],
                },
                {
                    "name": "soft_delete_both_chats_through_api",
                    "cleanup_code": cleanup_response["code"],
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "responses": [[200, 0], [200, 0]],
                "names": names,
                "xss_handling": "front-end responsibility; backend stores exact text",
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs087() -> dict[str, Any]:
    case_id = "TC-CS-087"
    prefix = "fresh-cs-087"
    prologue = "fresh large history prologue"
    fixed_answer = "fresh large history answer"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dataset = _dataset_fixture(
            case_id,
            group,
            owner,
            f"{prefix}-dataset",
            with_chunk=True,
            chunk_content="fresh deterministic large history retrieval fixture",
        )
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_large_history_chat",
            {
                "name": prefix,
                "dataset_ids": [dataset["id"]],
                "similarity_threshold": 2.0,
                "prompt_config": {
                    "prologue": prologue,
                    "empty_response": fixed_answer,
                },
            },
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(
            case_id,
            group,
            owner["auth"],
            "create_large_history_session",
            chat_id,
            {"name": f"{prefix}-Session"},
        )
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        baseline_completions: list[dict[str, Any]] = []
        baseline_questions: list[str] = []
        for round_number in range(1, 51):
            question = f"fresh history round {round_number:02d}"
            baseline_questions.append(question)
            baseline_completions.append(
                _complete_nonstream(
                    case_id,
                    group,
                    owner["auth"],
                    f"complete_large_history_round_{round_number:02d}",
                    chat_id,
                    session_id,
                    [{"role": "user", "content": question}],
                )
            )
        baseline_snapshot = _session_snapshot(group, session_id) if session_id else {"count": 0}
        baseline_messages = baseline_snapshot.get("messages") if isinstance(baseline_snapshot.get("messages"), list) else []
        final_question = "fresh history final verification"
        final_completion = _complete_nonstream(
            case_id,
            group,
            owner["auth"],
            "complete_after_fifty_round_baseline",
            chat_id,
            session_id,
            [{"role": "user", "content": final_question}],
        )
        final_snapshot = _session_snapshot(group, session_id) if session_id else {"count": 0}
        final_messages = final_snapshot.get("messages") if isinstance(final_snapshot.get("messages"), list) else []
        all_messages_valid = bool(final_messages) and all(
            isinstance(message, dict) and message.get("role") in {"user", "assistant"} and isinstance(message.get("content"), str) and bool(message.get("id")) for message in final_messages
        )
        no_truncation = history_messages_not_truncated(
            final_messages,
            prologue,
            baseline_questions + [final_question],
        )
        session_chat_cleanup = _cleanup_created_session_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        dataset_cleanup = _cleanup_dataset_fixture(case_id, group, owner, [dataset["id"]])
        cleanup = session_chat_cleanup and dataset_cleanup
        observed = {
            "fixture_ready": preclean and dataset["ready"] and chat["code"] == 0 and session["code"] == 0 and bool(chat_id) and bool(session_id),
            "baseline_completion_count": len(baseline_completions),
            "baseline_all_success": all(item["http_status"] == 200 and item["code"] == 0 for item in baseline_completions),
            "baseline_message_count": len(baseline_messages),
            "final_http_status": final_completion["http_status"],
            "final_code": final_completion["code"],
            "final_message_delta": len(final_messages) - len(baseline_messages),
            "final_message_count": len(final_messages),
            "all_messages_valid": all_messages_valid,
            "no_truncation": no_truncation,
            "cleanup_succeeded": cleanup,
        }
        passed = large_history_contract_ok(observed)
        findings = []
        if len(final_messages) == 103 and isinstance(final_messages[0], dict) and final_messages[0].get("role") == "assistant" and not final_messages[0].get("id"):
            findings.append(
                {
                    "type": "product_defect",
                    "area": "session_prologue_message_id",
                    "summary": "large history is complete but the initial prologue has no id",
                }
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset_chat_and_session_with_deterministic_empty_response",
                    "dataset_ready": dataset["ready"],
                    "dataset_id_fingerprint": DB._fingerprint(dataset["id"]),
                    "chat_code": chat["code"],
                    "session_code": session["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "session_id_fingerprint": DB._fingerprint(session_id),
                },
                {
                    "name": "build_fifty_round_history_through_completion_api",
                    "completion_count": len(baseline_completions),
                    "all_responses_successful": observed["baseline_all_success"],
                    "baseline_message_count": len(baseline_messages),
                    "raw_sha256": [item["raw_sha256"] for item in baseline_completions],
                },
                {
                    "name": "append_final_round_and_verify_large_json_storage",
                    "http_status": final_completion["http_status"],
                    "code": final_completion["code"],
                    "message_count_before": len(baseline_messages),
                    "message_count_after": len(final_messages),
                    "message_delta": observed["final_message_delta"],
                    "all_messages_have_id_role_content": all_messages_valid,
                    "no_content_truncation": no_truncation,
                    "raw_sha256": final_completion["raw_sha256"],
                },
                {
                    "name": "cleanup_session_chat_and_dataset_through_apis",
                    "session_chat_cleanup_succeeded": session_chat_cleanup,
                    "dataset_cleanup_succeeded": dataset_cleanup,
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "baseline_rounds": 50,
                "baseline_message_count": 101,
                "final_message_count": 103,
                "final_delta": 2,
                "each_message": ["id", "role", "content"],
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_cs088() -> dict[str, Any]:
    case_id = "TC-CS-088"
    prefix = "fresh-cs-088"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_concurrent_sessions",
            {"name": prefix},
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        barrier = threading.Barrier(5)

        def create_one(index: int) -> dict[str, Any]:
            barrier.wait(timeout=10)
            return _create_session(
                case_id,
                group,
                owner["auth"],
                f"create_concurrent_session_{index}",
                chat_id,
                {"name": f"并发会话{index}"},
            )

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_one, index) for index in range(1, 6)]
            responses = [future.result(timeout=30) for future in futures]
        data_rows = [response["data"] if isinstance(response["data"], dict) else {} for response in responses]
        session_ids = [str(data.get("id") or "") for data in data_rows]
        expected_names = [f"并发会话{index}" for index in range(1, 6)]
        rows = _session_rows(group, chat_id) if chat_id else []
        cleanup = _cleanup_created_session_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        observed = {
            "fixture_ready": preclean and chat["code"] == 0 and bool(chat_id),
            "response_statuses": [[response["http_status"], response["code"]] for response in responses],
            "unique_id_count": len({item for item in session_ids if item}),
            "database_count": len(rows),
            "database_ids_exact": {row["id"] for row in rows} == set(session_ids),
            "database_names_exact": {row["name"] for row in rows} == set(expected_names),
            "cleanup_succeeded": cleanup,
        }
        passed = concurrent_session_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chat_for_concurrent_session_requests",
                    "chat_code": chat["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "raw_sha256": chat["raw_sha256"],
                },
                {
                    "name": "send_five_barrier_synchronized_session_creates",
                    "worker_count": 5,
                    "response_statuses": observed["response_statuses"],
                    "unique_id_count": observed["unique_id_count"],
                    "session_id_fingerprints": [DB._fingerprint(session_id) for session_id in session_ids],
                    "raw_sha256": [response["raw_sha256"] for response in responses],
                },
                {
                    "name": "read_only_five_unique_session_rows_verification",
                    "database_count": len(rows),
                    "database_ids_exact": observed["database_ids_exact"],
                    "database_names_exact": observed["database_names_exact"],
                },
                {
                    "name": "cleanup_sessions_then_chat_through_apis",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "responses": [[200, 0]] * 5,
                "unique_session_ids": 5,
                "database_rows": 5,
            },
        }

    return _run_case(case_id, execute)


def run_cs089() -> dict[str, Any]:
    case_id = "TC-CS-089"
    prefix = "fresh-cs-089"
    fixed_answer = "fresh strict JSON answer"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        isolated = _run_isolated_float_sanitizer_probe(case_id, group)
        preclean = _cleanup_session_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chat_for_strict_json_completion",
            {
                "name": prefix,
                "prompt_config": {
                    "prologue": "fresh strict JSON prologue",
                    "empty_response": fixed_answer,
                },
            },
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        session = _create_session(
            case_id,
            group,
            owner["auth"],
            "create_session_for_strict_json_completion",
            chat_id,
            {"name": f"{prefix}-Session"},
        )
        session_data = session["data"] if isinstance(session["data"], dict) else {}
        session_id = str(session_data.get("id") or "")
        nonstream = _strict_json_post(
            case_id,
            group,
            owner["auth"],
            "strict_parse_nonstream_completion",
            "/chat/completions",
            {
                "chat_id": chat_id,
                "session_id": session_id,
                "messages": [{"role": "user", "content": "strict nonstream probe"}],
                "stream": False,
            },
        )
        streamed = _sse_post(
            case_id,
            group,
            owner["auth"],
            "strict_parse_stream_completion",
            "/chat/completions",
            {
                "chat_id": chat_id,
                "session_id": session_id,
                "messages": [{"role": "user", "content": "strict stream probe"}],
                "stream": True,
            },
        )
        events = streamed.get("events") if isinstance(streamed.get("events"), list) else []
        stream_error_event_absent = not any(isinstance(event, dict) and event.get("code") not in (None, 0) for event in events)
        cleanup = _cleanup_created_session_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        observed = {
            "fixture_ready": preclean and chat["code"] == 0 and session["code"] == 0 and bool(chat_id) and bool(session_id),
            "isolated_exit_code": isolated["exit_code"],
            "isolated_nested_values_sanitized": isolated["nested_values_sanitized"],
            "isolated_other_values_unchanged": isolated["other_values_unchanged"],
            "nonstream_http_status": nonstream["http_status"],
            "nonstream_code": nonstream["code"],
            "nonstream_strict_parse_ok": nonstream["strict_parse_ok"],
            "stream_http_status": streamed["http_status"],
            "stream_strict_parse_error_count": streamed.get("parse_error_count"),
            "stream_final_event_matches": _sse_final_event_matches(events),
            "stream_error_event_absent": stream_error_event_absent,
            "nonstandard_literal_absent": nonstream["strict_parse_ok"] and streamed.get("parse_error_count") == 0,
            "cleanup_succeeded": cleanup,
        }
        passed = float_sanitization_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "call_current_float_sanitizer_in_isolated_process",
                    "exit_code": isolated["exit_code"],
                    "nested_values_sanitized": isolated["nested_values_sanitized"],
                    "other_values_unchanged": isolated["other_values_unchanged"],
                    "raw_sha256": isolated["raw_sha256"],
                },
                {
                    "name": "strict_parse_real_nonstream_completion",
                    "http_status": nonstream["http_status"],
                    "code": nonstream["code"],
                    "strict_parse_ok": nonstream["strict_parse_ok"],
                    "body_length": nonstream["body_length"],
                    "body_sha256": nonstream["body_sha256"],
                    "raw_sha256": nonstream["raw_sha256"],
                },
                {
                    "name": "strict_parse_every_real_sse_data_frame",
                    "http_status": streamed["http_status"],
                    "event_count": len(events),
                    "strict_parse_error_count": streamed.get("parse_error_count"),
                    "final_event_matches": observed["stream_final_event_matches"],
                    "error_event_absent": stream_error_event_absent,
                    "raw_sha256": streamed["raw_sha256"],
                },
                {
                    "name": "cleanup_session_then_chat_through_apis",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "isolated_sanitizer": "all nested NaN and infinities become null",
                "strict_json": "no NaN, Infinity, or -Infinity constants",
                "real_modes": ["nonstream", "stream"],
            },
        }

    return _run_case(case_id, execute)


def run_cs090() -> dict[str, Any]:
    case_id = "TC-CS-090"
    prefix = "fresh-cs-090"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dsl = _large_agent_dsl()
        canonical_request = json.dumps(dsl, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_hundred_component_large_dsl_agent",
            title=f"{prefix}-Agent",
            dsl=dsl,
            tags="large-dsl",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        detail = _get_agent(
            case_id,
            group,
            owner["auth"],
            "get_hundred_component_large_dsl_agent",
            agent_id,
        )
        detail_data = detail["data"] if isinstance(detail["data"], dict) else {}
        api_dsl = detail_data.get("dsl") if isinstance(detail_data.get("dsl"), dict) else {}
        snapshot = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        database_dsl = snapshot.get("dsl") if isinstance(snapshot.get("dsl"), dict) else {}
        canonical_api = json.dumps(api_dsl, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        canonical_database = json.dumps(
            database_dsl,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and bool(agent_id),
            "http_status": agent["http_status"],
            "code": agent["code"],
            "component_count": len(dsl["components"]),
            "request_json_bytes_over_100k": len(canonical_request) > 100 * 1024,
            "detail_http_status": detail["http_status"],
            "detail_code": detail["code"],
            "api_dsl_exact": api_dsl == dsl,
            "database_dsl_exact": database_dsl == dsl,
            "serialized_lengths_exact": len(canonical_api) == len(canonical_request) == len(canonical_database),
            "cleanup_succeeded": cleanup,
        }
        passed = large_agent_dsl_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "build_and_create_hundred_component_large_dsl",
                    "component_count": len(dsl["components"]),
                    "request_json_bytes": len(canonical_request),
                    "http_status": agent["http_status"],
                    "code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "get_large_dsl_and_compare_database_roundtrip",
                    "http_status": detail["http_status"],
                    "code": detail["code"],
                    "api_json_bytes": len(canonical_api),
                    "database_json_bytes": len(canonical_database),
                    "api_dsl_exact": observed["api_dsl_exact"],
                    "database_dsl_exact": observed["database_dsl_exact"],
                    "serialized_lengths_exact": observed["serialized_lengths_exact"],
                    "raw_sha256": detail["raw_sha256"],
                },
                {
                    "name": "cleanup_large_dsl_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "component_count": 100,
                "minimum_json_bytes": 102401,
                "api_database_dsl_exact": True,
            },
        }

    return _run_case(case_id, execute)


def _cleanup_agent_session_prefix(
    case_id: str,
    group: str,
    auth: str,
    tenant_id: str,
    prefix: str,
) -> bool:
    ok = True
    for agent_id in _agent_ids_by_prefix(group, tenant_id, prefix):
        result = _cleanup_agent_sessions_and_agent(case_id, group, auth, tenant_id, prefix, agent_id)
        ok = result["succeeded"] and ok
    return ok and not _agent_ids_by_prefix(group, tenant_id, prefix)


def _agent_events_answer(events: list[Any]) -> str:
    return "".join(str(event.get("data", {}).get("content") or "") for event in events if isinstance(event, dict) and event.get("event") == "message" and isinstance(event.get("data"), dict))


def _agent_events_error_absent(events: list[Any]) -> bool:
    return not any(
        isinstance(event, dict) and (event.get("code") not in (None, 0) or (isinstance(event.get("data"), dict) and str(event["data"].get("content") or "").startswith(("Error ", "**ERROR**"))))
        for event in events
    )


def _agent_sessions_have_completed_turns(rows: list[dict[str, Any]], expected_queries: set[str]) -> bool:
    if len(rows) != len(expected_queries):
        return False
    observed_queries: set[str] = set()
    for row in rows:
        messages = row.get("message")
        if not isinstance(messages, list) or len(messages) != 2:
            return False
        user, assistant = messages
        if (
            not isinstance(user, dict)
            or not isinstance(assistant, dict)
            or user.get("role") != "user"
            or assistant.get("role") != "assistant"
            or not str(user.get("id") or "")
            or assistant.get("id") != user.get("id")
            or not str(assistant.get("content") or "").strip()
        ):
            return False
        observed_queries.add(str(user.get("content") or ""))
    return observed_queries == expected_queries


def _agent_session_appended_completed_turn(before: dict[str, Any], after: dict[str, Any], question: str) -> bool:
    before_messages = before.get("message")
    after_messages = after.get("message")
    if not isinstance(before_messages, list) or not isinstance(after_messages, list):
        return False
    if len(after_messages) != len(before_messages) + 2:
        return False
    if after_messages[: len(before_messages)] != before_messages:
        return False
    user, assistant = after_messages[-2:]
    return (
        isinstance(user, dict)
        and isinstance(assistant, dict)
        and user.get("role") == "user"
        and user.get("content") == question
        and bool(str(user.get("id") or ""))
        and assistant.get("role") == "assistant"
        and assistant.get("id") == user.get("id")
        and bool(str(assistant.get("content") or "").strip())
    )


def _load_gauss_connection_fixture() -> dict[str, Any]:
    path = EXECUTE_DIR.parent / "gaussdb_info.md"
    values: dict[str, str] = {}
    pattern = re.compile(
        r"^\s*(?:[-*]\s*)?(user|host|port|dbname|password)\s*:\s*(.*?)\s*$",
        re.IGNORECASE,
    )
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "`\"'":
            value = value[1:-1]
        values[match.group(1).lower()] = value
    required = {"user", "host", "port", "dbname", "password"}
    if required - values.keys():
        raise RuntimeError("gaussdb_info.md is missing required connection fields")
    try:
        port = int(values["port"])
    except ValueError as exception:
        raise RuntimeError("gaussdb_info.md contains an invalid port") from exception
    return {
        "db_type": "postgres",
        "database": values["dbname"],
        "username": values["user"],
        "host": values["host"],
        "port": port,
        "password": values["password"],
    }


def _host_is_global(host: str) -> bool:
    try:
        addresses = {str(item[4][0]).split("%", 1)[0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
        parsed = [ipaddress.ip_address(address) for address in addresses]
    except (OSError, ValueError):
        return False
    return bool(parsed) and all(address.is_global for address in parsed)


def _private_connection_request(
    case_id: str,
    group: str,
    label: str,
    auth: str,
    payload: dict[str, Any],
    *,
    target_class: str,
) -> dict[str, Any]:
    """Call the connection probe without persisting raw connection parameters."""
    started = time.monotonic()
    try:
        response = DD.requests.post(
            f"{DB._api_base(group)}/agents/test_db_connection",
            headers={"Authorization": f"Bearer {auth}"},
            json=payload,
            timeout=30,
        )
        elapsed = time.monotonic() - started
        try:
            body = response.json()
        except ValueError:
            body = {}
        status = response.status_code
        transport_exception_type = None
    except DD.requests.RequestException as exception:
        elapsed = time.monotonic() - started
        body = {}
        status = 0
        transport_exception_type = type(exception).__name__

    message = str(body.get("message") or "")
    data = body.get("data")
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RAW_DIR.chmod(0o700)
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    _evidence_module().write_evidence(
        raw_path,
        {
            "request": {
                "method": "POST",
                "path": "/agents/test_db_connection",
                "target_class": target_class,
                "db_type": str(payload.get("db_type") or ""),
                "host_fingerprint": DB._fingerprint(payload.get("host")),
                "connection_fields_present": all(
                    payload.get(field) not in (None, "")
                    for field in (
                        "database",
                        "username",
                        "host",
                        "port",
                        "password",
                    )
                ),
                "authorization_present": bool(auth),
            },
            "response": {
                "http_status": status,
                "code": body.get("code"),
                "message_length": len(message),
                "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest(),
                "message_mentions_non_public": "non-public" in message.lower(),
                "message_mentions_not_allowed": "not allowed" in message.lower(),
                "data_is_connection_success": data == "Database Connection Successful!",
                "transport_exception_type": transport_exception_type,
            },
        },
    )
    return {
        "http_status": status,
        "code": body.get("code"),
        "message": message,
        "data": data,
        "elapsed_seconds": elapsed,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }


def _dataflow_template() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[4] / "agent/templates/advanced_ingestion_pipeline.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    dsl = payload.get("dsl")
    if not isinstance(dsl, dict):
        raise RuntimeError("current product DataFlow template has no DSL")
    return dsl


def _pipeline_log_rows(group: str, document_id: str, pipeline_id: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,document_id,pipeline_id,dsl,task_type,operation_status,progress,create_time FROM pipeline_operation_log WHERE document_id=%s AND pipeline_id=%s ORDER BY create_time,id",
                (document_id, pipeline_id),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "document_id": str(row[1]),
            "pipeline_id": str(row[2]),
            "dsl": _json_value(row[3], {}),
            "task_type": "" if row[4] is None else str(row[4]),
            "operation_status": "" if row[5] is None else str(row[5]),
            "progress": float(row[6] or 0),
            "create_time": int(row[7]) if row[7] is not None else None,
        }
        for row in rows
    ]


def _dataflow_task_rows(group: str, document_id: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,task_type,progress,progress_msg,create_time FROM task WHERE doc_id=%s ORDER BY create_time,id",
                (document_id,),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "task_type": "" if row[1] is None else str(row[1]),
            "progress": float(row[2] or 0),
            "progress_msg_length": len(str(row[3] or "")),
            "create_time": int(row[4]) if row[4] is not None else None,
        }
        for row in rows
    ]


def _task_snapshot(group: str, task_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,doc_id,task_type,progress,progress_msg,create_time FROM task WHERE id=%s",
                (task_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    return {
        "count": 1,
        "id": str(row[0]),
        "doc_id": str(row[1]),
        "task_type": "" if row[2] is None else str(row[2]),
        "progress": float(row[3] or 0),
        "progress_msg_length": len(str(row[4] or "")),
        "create_time": int(row[5]) if row[5] is not None else None,
    }


def _wait_task_snapshot(
    group: str,
    task_id: str,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 120,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    snapshot = {"count": 0}
    while time.monotonic() < deadline:
        snapshot = _task_snapshot(group, task_id)
        if predicate(snapshot):
            return snapshot
        time.sleep(0.1)
    return snapshot


def _poll_document_terminal(
    case_id: str,
    group: str,
    auth: str,
    dataset_id: str,
    document_id: str,
    phase: str,
    *,
    timeout: float = 300,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout
    observations: list[dict[str, Any]] = []
    raw_sha256: list[str] = []
    last_signature: tuple[Any, ...] | None = None
    poll_count = 0
    while time.monotonic() < deadline:
        poll_count += 1
        response = _request(
            case_id,
            group,
            f"{phase}_document_progress_{poll_count:03d}",
            auth,
            "GET",
            f"/datasets/{dataset_id}/documents",
            params={"id": document_id},
        )
        raw_sha256.append(response["raw_sha256"])
        data = response["data"] if isinstance(response["data"], dict) else {}
        docs = data.get("docs") if isinstance(data.get("docs"), list) else []
        row = docs[0] if len(docs) == 1 and isinstance(docs[0], dict) else {}
        current = {
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "code": response["code"],
            "total": int(data.get("total") or 0),
            "run": row.get("run"),
            "progress": float(row.get("progress") or 0),
            "progress_msg_length": len(str(row.get("progress_msg") or "")),
            "chunk_count": int(row.get("chunk_count") or 0),
            "token_count": int(row.get("token_count") or 0),
        }
        signature = tuple(current.values())[1:]
        if signature != last_signature:
            observations.append(current)
            last_signature = signature
        if response["code"] == 0 and current["run"] in {
            "DONE",
            "FAIL",
            "CANCEL",
        }:
            return {
                "terminal": True,
                "timed_out": False,
                "poll_count": poll_count,
                "observations": observations,
                "raw_sha256": raw_sha256,
                "final": current,
            }
        time.sleep(0.5)
    return {
        "terminal": False,
        "timed_out": True,
        "poll_count": poll_count,
        "observations": observations,
        "raw_sha256": raw_sha256,
        "final": observations[-1] if observations else {},
    }


def _poll_dataflow_logs(
    case_id: str,
    group: str,
    auth: str,
    dataset_id: str,
    document_id: str,
    pipeline_id: str,
    phase: str,
    *,
    minimum_count: int,
    timeout: float = 120,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    poll_count = 0
    raw_sha256: list[str] = []
    matches: list[dict[str, Any]] = []
    last_code: Any = None
    while time.monotonic() < deadline:
        poll_count += 1
        response = _request(
            case_id,
            group,
            f"{phase}_ingestion_logs_{poll_count:03d}",
            auth,
            "GET",
            f"/datasets/{dataset_id}/ingestions",
            params={
                "page": 1,
                "page_size": 100,
                "orderby": "create_time",
                "desc": "true",
                "log_type": "file",
            },
        )
        raw_sha256.append(response["raw_sha256"])
        last_code = response["code"]
        data = response["data"] if isinstance(response["data"], dict) else {}
        logs = data.get("logs") if isinstance(data.get("logs"), list) else []
        matches = [item for item in logs if isinstance(item, dict) and str(item.get("document_id") or "") == document_id and str(item.get("pipeline_id") or "") == pipeline_id]
        if response["code"] == 0 and len(matches) >= minimum_count:
            break
        time.sleep(0.5)
    return {
        "code": last_code,
        "poll_count": poll_count,
        "raw_sha256": raw_sha256,
        "matches": matches,
        "minimum_count_met": len(matches) >= minimum_count,
    }


def run_cs091() -> dict[str, Any]:
    case_id = "TC-CS-091"
    prefix = "fresh-cs-091"
    stream_query = "测试流式"
    nonstream_query = "测试非流式"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_session_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_llm_agent_for_both_completion_modes",
            title=f"{prefix}-Agent",
            dsl=_agent_llm_dsl(llm_id),
            tags="completion-modes",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        streamed = _agent_sse_post(
            case_id,
            group,
            owner["auth"],
            "stream_agent_completion",
            {"agent_id": agent_id, "query": stream_query, "stream": True},
            timeout=240,
        )
        events = streamed.get("events") if isinstance(streamed.get("events"), list) else []
        stream_session_ids = {str(event.get("session_id") or "") for event in events if isinstance(event, dict) and event.get("session_id")}
        nonstream = _request(
            case_id,
            group,
            "nonstream_agent_completion",
            owner["auth"],
            "POST",
            "/agents/chat/completions",
            payload={
                "agent_id": agent_id,
                "query": nonstream_query,
                "stream": False,
            },
            timeout=240,
        )
        nonstream_data = nonstream["data"] if isinstance(nonstream["data"], dict) else {}
        nested_nonstream_data = nonstream_data.get("data") if isinstance(nonstream_data.get("data"), dict) else {}
        nonstream_session_id = str(nonstream_data.get("session_id") or "")
        rows = _agent_session_rows(group, agent_id) if agent_id else []
        row_ids = {row["id"] for row in rows}
        expected_session_ids = set(stream_session_ids)
        if nonstream_session_id:
            expected_session_ids.add(nonstream_session_id)
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        observed = {
            "fixture_ready": preclean and agent["http_status"] == 200 and agent["code"] == 0 and bool(agent_id),
            "stream_http_status": streamed["http_status"],
            "stream_content_type_ok": str(streamed.get("content_type") or "").lower().startswith("text/event-stream"),
            "stream_done_seen": streamed.get("done_count") == 1,
            "stream_parse_error_count": streamed.get("parse_error_count"),
            "stream_error_absent": _agent_events_error_absent(events),
            "stream_answer_nonempty": bool(_agent_events_answer(events).strip()),
            "nonstream_http_status": nonstream["http_status"],
            "nonstream_code": nonstream["code"],
            "nonstream_answer_nonempty": bool(str(nested_nonstream_data.get("content") or "").strip()),
            "session_count": len(rows),
            "session_ids_match": len(expected_session_ids) == 2 and row_ids == expected_session_ids,
            "sessions_have_messages": _agent_sessions_have_completed_turns(rows, {stream_query, nonstream_query}),
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = agent_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_with_real_llm_component",
                    "http_status": agent["http_status"],
                    "code": agent["code"],
                    "llm_id_fingerprint": DB._fingerprint(llm_id),
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "run_strictly_parsed_stream_agent_completion",
                    "http_status": streamed["http_status"],
                    "event_count": len(events),
                    "done_count": streamed.get("done_count"),
                    "parse_error_count": streamed.get("parse_error_count"),
                    "error_absent": observed["stream_error_absent"],
                    "answer_nonempty": observed["stream_answer_nonempty"],
                    "session_id_count": len(stream_session_ids),
                    "raw_sha256": streamed["raw_sha256"],
                },
                {
                    "name": "run_nonstream_agent_completion",
                    "http_status": nonstream["http_status"],
                    "code": nonstream["code"],
                    "answer_nonempty": observed["nonstream_answer_nonempty"],
                    "session_id_present": bool(nonstream_session_id),
                    "raw_sha256": nonstream["raw_sha256"],
                },
                {
                    "name": "read_only_session_persistence_and_api_cleanup",
                    "session_count": len(rows),
                    "session_ids_match": observed["session_ids_match"],
                    "sessions_have_messages": observed["sessions_have_messages"],
                    "cleanup_succeeded": cleanup["succeeded"],
                },
            ],
            "oracle": {
                "stream": {"http_status": 200, "done_count": 1},
                "nonstream": [200, 0],
                "persisted_sessions": 2,
                "messages_per_session": 2,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-AGENT-COMPLETION-001",
                    "summary": f"{group} Agent stream/nonstream completion or persisted sessions did not meet the contract",
                    "code_location": "api/apps/restful_apis/agent_api.py:agent_chat_completion",
                }
            ],
        }

    return _run_case(case_id, execute)


def _meaningful_output(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_meaningful_output(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_meaningful_output(item) for item in value)
    return value is not None


def run_cs093() -> dict[str, Any]:
    case_id = "TC-CS-093"
    prefix = "fresh-cs-093"
    component_id = "LLM:Fresh"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        llm_id = _tenant_default_llm(group, owner["tenant_id"])
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_llm_agent_for_component_debug",
            title=f"{prefix}-Agent",
            dsl=_agent_llm_dsl(llm_id),
            tags="component-debug",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        input_form = _request(
            case_id,
            group,
            "get_llm_component_input_form",
            owner["auth"],
            "GET",
            f"/agents/{agent_id}/components/{component_id}/input-form",
        )
        form_data = input_form["data"] if isinstance(input_form["data"], dict) else {}
        params = {key: {"value": "fresh controlled component debug query"} for key in form_data}
        debug = _request(
            case_id,
            group,
            "debug_llm_component_with_input_form",
            owner["auth"],
            "POST",
            f"/agents/{agent_id}/components/{component_id}/debug",
            payload={"params": params},
            timeout=240,
        )
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["http_status"] == 200 and agent["code"] == 0 and bool(agent_id),
            "input_form_http_status": input_form["http_status"],
            "input_form_code": input_form["code"],
            "input_key_present": "sys.query" in form_data and set(params) == set(form_data),
            "debug_http_status": debug["http_status"],
            "debug_code": debug["code"],
            "output_nonempty": _meaningful_output(debug["data"]),
            "cleanup_succeeded": cleanup,
        }
        passed = agent_debug_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_with_real_llm_component",
                    "http_status": agent["http_status"],
                    "code": agent["code"],
                    "component_id": component_id,
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "get_component_input_form_and_build_exact_params",
                    "http_status": input_form["http_status"],
                    "code": input_form["code"],
                    "input_keys": sorted(form_data),
                    "sys_query_present": "sys.query" in form_data,
                    "raw_sha256": input_form["raw_sha256"],
                },
                {
                    "name": "debug_real_llm_component",
                    "http_status": debug["http_status"],
                    "code": debug["code"],
                    "output_nonempty": observed["output_nonempty"],
                    "raw_sha256": debug["raw_sha256"],
                },
                {
                    "name": "cleanup_debug_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "input_form_key": "sys.query",
                "debug_response": [200, 0],
                "output_nonempty": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-AGENT-COMPONENT-DEBUG-001",
                    "summary": f"{group} LLM component input form or debug output did not meet the contract",
                    "code_location": "api/apps/restful_apis/agent_api.py:debug_agent_component",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_cs092() -> dict[str, Any]:
    case_id = "TC-CS-092"
    prefix = "fresh-cs-092"
    filename = "fresh-cs-092-agent-upload.txt"
    content = ("fresh Agent upload and download roundtrip\ncontrol and experiment must return these exact UTF-8 bytes\n").encode("utf-8")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_agent_for_file_roundtrip",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="file-roundtrip"),
            tags="file-roundtrip",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        uploaded = _request(
            case_id,
            group,
            "upload_agent_file",
            owner["auth"],
            "POST",
            f"/agents/{agent_id}/upload",
            files=[("file", (filename, content, "text/plain"))],
            timeout=120,
        )
        upload_data = uploaded["data"] if isinstance(uploaded["data"], dict) else {}
        file_id = str(upload_data.get("id") or "")
        downloaded = _binary_get(
            case_id,
            group,
            "download_agent_file",
            owner["auth"],
            "/agents/download",
            {"id": file_id},
        )
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["http_status"] == 200 and agent["code"] == 0 and bool(agent_id),
            "upload_http_status": uploaded["http_status"],
            "upload_code": uploaded["code"],
            "file_id_present": bool(file_id),
            "download_http_status": downloaded["http_status"],
            "download_bytes_exact": downloaded.get("_body") == content,
            "no_file_delete_endpoint_recorded": True,
            "agent_cleanup_succeeded": agent_cleanup,
        }
        passed = agent_file_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_and_upload_in_memory_text_file",
                    "agent_code": agent["code"],
                    "upload_http_status": uploaded["http_status"],
                    "upload_code": uploaded["code"],
                    "filename_matches": upload_data.get("name") == filename,
                    "file_id_fingerprint": DB._fingerprint(file_id),
                    "expected_bytes": len(content),
                    "expected_sha256": hashlib.sha256(content).hexdigest(),
                    "raw_sha256": [agent["raw_sha256"], uploaded["raw_sha256"]],
                },
                {
                    "name": "download_agent_file_and_compare_exact_bytes",
                    "http_status": downloaded["http_status"],
                    "body_length": downloaded["response_length"],
                    "body_sha256": downloaded["body_sha256"],
                    "bytes_exact": observed["download_bytes_exact"],
                    "raw_sha256": downloaded["raw_sha256"],
                },
                {
                    "name": "record_missing_agent_file_delete_api_and_cleanup_agent",
                    "agent_upload_creates_file_row": False,
                    "agent_file_delete_endpoint_available": False,
                    "direct_storage_cleanup_performed": False,
                    "agent_cleanup_succeeded": agent_cleanup,
                },
            ],
            "oracle": {
                "upload_response": [200, 0],
                "download_http_status": 200,
                "download_sha256": hashlib.sha256(content).hexdigest(),
                "cleanup_limitation": "no Agent file delete API exists",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-AGENT-FILE-ROUNDTRIP-001",
                    "summary": f"{group} Agent file upload/download did not preserve exact bytes",
                    "code_location": "api/apps/restful_apis/agent_api.py:upload_agent_file",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_cs094() -> dict[str, Any]:
    case_id = "TC-CS-094"
    prefix = "fresh-cs-094"
    initial_tag = "fresh-cs-094-initial"
    updated_tags = "标签A,标签B"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_tagged_agent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="tag-management"),
            tags=initial_tag,
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        listed = _request(
            case_id,
            group,
            "list_visible_agent_tag_aggregation",
            owner["auth"],
            "GET",
            "/agents/tags",
        )
        tag_rows = listed["data"] if isinstance(listed["data"], list) else []
        tag_counts = {str(item.get("tag")): int(item.get("count") or 0) for item in tag_rows if isinstance(item, dict) and item.get("tag") is not None}
        updated = _request(
            case_id,
            group,
            "update_agent_tags",
            owner["auth"],
            "PUT",
            f"/agents/{agent_id}/tags",
            payload={"tags": updated_tags},
        )
        snapshot = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        observed = {
            "fixture_ready": preclean and agent["http_status"] == 200 and agent["code"] == 0 and bool(agent_id),
            "list_http_status": listed["http_status"],
            "list_code": listed["code"],
            "initial_count_exact": tag_counts.get(initial_tag) == 1,
            "update_http_status": updated["http_status"],
            "update_code": updated["code"],
            "database_tags_exact": snapshot.get("tags") == updated_tags,
            "cleanup_succeeded": cleanup,
        }
        passed = agent_tag_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_agent_with_unique_initial_tag",
                    "http_status": agent["http_status"],
                    "code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "list_agent_tags_and_verify_unique_aggregation",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "returned_tag_count": len(tag_rows),
                    "initial_tag_count": tag_counts.get(initial_tag),
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "update_unicode_tags_and_verify_physical_value",
                    "http_status": updated["http_status"],
                    "code": updated["code"],
                    "database_tags_exact": observed["database_tags_exact"],
                    "physical_tags_is_null": snapshot.get("physical_tags_is_null"),
                    "raw_sha256": updated["raw_sha256"],
                },
                {
                    "name": "cleanup_tagged_agent_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "initial_tag_count": 1,
                "updated_physical_tags": updated_tags,
                "responses": [[200, 0], [200, 0]],
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-AGENT-TAGS-001",
                    "summary": f"{group} Agent tag aggregation or Unicode update did not meet the contract",
                    "code_location": "api/apps/restful_apis/agent_api.py:update_agent_tags",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_cs095() -> dict[str, Any]:
    case_id = "TC-CS-095"
    prefix = "fresh-cs-095"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        credential = _create_beta_credential(case_id, group, owner["auth"], "create_beta_and_ordinary_tokens")
        beta = credential["_beta"]
        ordinary = credential["_token"]
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_chatbot_for_auth_type_separation",
            {"name": f"{prefix}-Chatbot"},
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        beta_response = _request(
            case_id,
            group,
            "get_chatbot_info_with_beta_token",
            beta,
            "GET",
            f"/chatbots/{chat_id}/info",
        )
        ordinary_response = _request(
            case_id,
            group,
            "get_chatbot_info_with_ordinary_token",
            ordinary,
            "GET",
            f"/chatbots/{chat_id}/info",
        )
        chat_cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        credential_cleanup = _cleanup_beta_credential(case_id, group, owner["auth"], ordinary)
        observed = {
            "fixture_ready": preclean
            and credential["http_status"] == 200
            and credential["code"] == 0
            and bool(beta)
            and bool(ordinary)
            and beta != ordinary
            and chat["http_status"] == 200
            and chat["code"] == 0
            and bool(chat_id),
            "beta_status": beta_response["http_status"],
            "beta_code": beta_response["code"],
            "ordinary_status": ordinary_response["http_status"],
            "ordinary_code": ordinary_response["code"],
            "credential_cleanup_succeeded": credential_cleanup,
            "chat_cleanup_succeeded": chat_cleanup,
        }
        passed = bot_credential_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_chatbot_and_paired_beta_ordinary_tokens",
                    "credential_code": credential["code"],
                    "chat_code": chat["code"],
                    "beta_length": len(beta),
                    "ordinary_length": len(ordinary),
                    "beta_fingerprint": DB._fingerprint(beta),
                    "ordinary_fingerprint": DB._fingerprint(ordinary),
                    "raw_sha256": [
                        credential["raw_sha256"],
                        chat["raw_sha256"],
                    ],
                },
                {
                    "name": "call_bot_info_with_beta_token_without_cookie",
                    "http_status": beta_response["http_status"],
                    "code": beta_response["code"],
                    "raw_sha256": beta_response["raw_sha256"],
                },
                {
                    "name": "call_same_bot_info_with_ordinary_token_without_cookie",
                    "http_status": ordinary_response["http_status"],
                    "code": ordinary_response["code"],
                    "raw_sha256": ordinary_response["raw_sha256"],
                },
                {
                    "name": "cleanup_token_and_chat_through_apis",
                    "credential_cleanup_succeeded": credential_cleanup,
                    "chat_cleanup_succeeded": chat_cleanup,
                },
            ],
            "oracle": {
                "beta_response": [200, 0],
                "ordinary_response": [401, 401],
                "cookie_client_used": False,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-BOT-AUTH-TYPE-001",
                    "summary": f"{group} Chatbot endpoint did not separate beta and ordinary tokens",
                    "code_location": "api/apps/restful_apis/bot_api.py",
                }
            ],
        }

    return _run_case(case_id, execute)


def _membership_snapshot(group: str, user_id: str, tenant_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT role,status FROM user_tenant WHERE user_id=%s AND tenant_id=%s ORDER BY id",
                (user_id, tenant_id),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return {
        "count": len(rows),
        "role": rows[0][0] if len(rows) == 1 else None,
        "status": str(rows[0][1]) if len(rows) == 1 else None,
    }


def _invite_and_accept_member(
    case_id: str,
    group: str,
    owner: dict[str, str],
    secondary: dict[str, Any],
    email: str,
) -> dict[str, Any]:
    invited = _request(
        case_id,
        group,
        "invite_secondary_user_to_owner_tenant",
        owner["auth"],
        "POST",
        f"/tenants/{owner['tenant_id']}/users",
        payload={"email": email},
    )
    accepted = _request(
        case_id,
        group,
        "secondary_user_accept_owner_tenant_invitation",
        secondary["auth"],
        "PATCH",
        f"/tenants/{owner['tenant_id']}",
    )
    snapshot = _membership_snapshot(group, secondary["tenant_id"], owner["tenant_id"])
    return {"invite": invited, "accept": accepted, "snapshot": snapshot}


def _remove_secondary_membership(
    case_id: str,
    group: str,
    owner_tenant_id: str,
    secondary: dict[str, Any],
) -> dict[str, Any]:
    before = _membership_snapshot(group, secondary["tenant_id"], owner_tenant_id)
    if before["count"]:
        response = _request(
            case_id,
            group,
            "secondary_user_leave_owner_tenant",
            secondary["auth"],
            "DELETE",
            f"/tenants/{owner_tenant_id}/users",
            payload={"user_id": secondary["tenant_id"]},
        )
    else:
        response = {"http_status": 200, "code": 0, "raw_sha256": None}
    after = _membership_snapshot(group, secondary["tenant_id"], owner_tenant_id)
    return {
        "response": response,
        "before_count": before["count"],
        "after_count": after["count"],
        "succeeded": response.get("code") == 0 and after["count"] == 0,
    }


def run_cs096() -> dict[str, Any]:
    case_id = "TC-CS-096"
    prefix = "fresh-cs-096"
    email = "cs-096-user-b@fresh.invalid"
    password = "Fresh-CS-096-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = _prepare_secondary_user(case_id, group, email, password)
        secondary_ready = (
            secondary["preclean"] and secondary["registration"]["code"] == 0 and secondary["login"]["code"] == 0 and bool(secondary["auth"]) and secondary["tenant_id"] != owner["tenant_id"]
        )
        chat = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_user_a_private_chat",
            {"name": f"{prefix}-Chat"},
        )
        chat_data = chat["data"] if isinstance(chat["data"], dict) else {}
        chat_id = str(chat_data.get("id") or "")
        before = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        denied_get = _request(
            case_id,
            group,
            "user_b_get_user_a_chat",
            secondary["auth"],
            "GET",
            f"/chats/{chat_id}",
        )
        denied_delete = _request(
            case_id,
            group,
            "user_b_delete_user_a_chat",
            secondary["auth"],
            "DELETE",
            f"/chats/{chat_id}",
        )
        after = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        chat_cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        secondary_cleanup = _cleanup_secondary_user(case_id, group, email)
        observed = {
            "fixture_ready": preclean and secondary_ready and chat["http_status"] == 200 and chat["code"] == 0 and before.get("count") == 1,
            "get_status": denied_get["http_status"],
            "get_code": denied_get["code"],
            "get_message_exact": denied_get["message"] == "No authorization.",
            "delete_status": denied_delete["http_status"],
            "delete_code": denied_delete["code"],
            "delete_message_exact": denied_delete["message"] == "No authorization.",
            "chat_unchanged": before == after,
            "cleanup_succeeded": chat_cleanup and secondary_cleanup["succeeded"],
        }
        passed = cross_tenant_chat_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_user_b_and_user_a_private_chat",
                    "secondary_ready": secondary_ready,
                    "chat_code": chat["code"],
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "raw_sha256": chat["raw_sha256"],
                },
                {
                    "name": "user_b_attempt_get_user_a_chat",
                    "http_status": denied_get["http_status"],
                    "code": denied_get["code"],
                    "message_exact": observed["get_message_exact"],
                    "raw_sha256": denied_get["raw_sha256"],
                },
                {
                    "name": "user_b_attempt_delete_user_a_chat",
                    "http_status": denied_delete["http_status"],
                    "code": denied_delete["code"],
                    "message_exact": observed["delete_message_exact"],
                    "raw_sha256": denied_delete["raw_sha256"],
                },
                {
                    "name": "read_only_no_mutation_and_api_cleanup",
                    "chat_unchanged": observed["chat_unchanged"],
                    "chat_cleanup_succeeded": chat_cleanup,
                    "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "get_response": [200, 109],
                "delete_response": [200, 109],
                "message": "No authorization.",
                "mutation": False,
            },
        }

    return _run_case(case_id, execute)


def run_cs097() -> dict[str, Any]:
    case_id = "TC-CS-097"
    prefix = "fresh-cs-097"
    email = "cs-097-user-b@fresh.invalid"
    password = "Fresh-CS-097-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = _prepare_secondary_user(case_id, group, email, password)
        secondary_ready = (
            secondary["preclean"] and secondary["registration"]["code"] == 0 and secondary["login"]["code"] == 0 and bool(secondary["auth"]) and secondary["tenant_id"] != owner["tenant_id"]
        )
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_user_a_private_agent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe="private-agent"),
            tags="private-agent",
            permission="me",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        before = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        denied = _get_agent(
            case_id,
            group,
            secondary["auth"],
            "user_b_get_user_a_private_agent",
            agent_id,
        )
        after = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        secondary_cleanup = _cleanup_secondary_user(case_id, group, email)
        observed = {
            "fixture_ready": preclean and secondary_ready and agent["http_status"] == 200 and agent["code"] == 0 and before.get("count") == 1 and before.get("permission") == "me",
            "get_status": denied["http_status"],
            "get_code": denied["code"],
            "agent_unchanged": before == after,
            "cleanup_succeeded": agent_cleanup and secondary_cleanup["succeeded"],
        }
        passed = cross_tenant_agent_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_independent_user_b_and_user_a_private_agent",
                    "secondary_ready": secondary_ready,
                    "agent_code": agent["code"],
                    "physical_permission": before.get("permission"),
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "user_b_attempt_get_user_a_private_agent",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "read_only_no_mutation_and_api_cleanup",
                    "agent_unchanged": observed["agent_unchanged"],
                    "agent_cleanup_succeeded": agent_cleanup,
                    "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
                },
            ],
            "oracle": {"response": [200, 103], "mutation": False},
            "findings": []
            if passed
            else [
                {
                    "id": "CS-AGENT-CROSS-TENANT-CODE-001",
                    "summary": f"{group} private Agent rejection returned an unexpected business code",
                    "code_location": "api/apps/restful_apis/agent_api.py:get_agent",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_cs098() -> dict[str, Any]:
    case_id = "TC-CS-098"
    prefix = "fresh-cs-098"
    email = "cs-098-user-b@fresh.invalid"
    password = "Fresh-CS-098-User-B@1234"
    original_title = f"{prefix}-Agent"
    attempted_title = "篡改"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary = _prepare_secondary_user(case_id, group, email, password)
        secondary_ready = (
            secondary["preclean"] and secondary["registration"]["code"] == 0 and secondary["login"]["code"] == 0 and bool(secondary["auth"]) and secondary["tenant_id"] != owner["tenant_id"]
        )
        membership = _invite_and_accept_member(case_id, group, owner, secondary, email)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_user_a_team_agent",
            title=original_title,
            dsl=_agent_dsl(probe="team-read-only"),
            tags="team-read-only",
            permission="team",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        readable = _get_agent(
            case_id,
            group,
            secondary["auth"],
            "team_member_get_user_a_agent",
            agent_id,
        )
        before = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        attempted = _put_agent(
            case_id,
            group,
            secondary["auth"],
            "team_member_attempt_update_user_a_agent",
            agent_id,
            {"title": attempted_title},
        )
        after_attempt = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        update_rejected = attempted["code"] != 0
        agent_unchanged = before == after_attempt
        restore = (
            _put_agent(
                case_id,
                group,
                owner["auth"],
                "owner_restore_agent_after_security_probe",
                agent_id,
                {"title": original_title},
            )
            if not agent_unchanged
            else {"http_status": 200, "code": 0, "raw_sha256": None}
        )
        restored = _agent_snapshot(group, agent_id) if agent_id else {"count": 0}
        membership_cleanup = _remove_secondary_membership(case_id, group, owner["tenant_id"], secondary)
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        secondary_cleanup = _cleanup_secondary_user(case_id, group, email)
        observed = {
            "fixture_ready": preclean
            and secondary_ready
            and membership["invite"]["code"] == 0
            and membership["accept"]["code"] == 0
            and agent["http_status"] == 200
            and agent["code"] == 0
            and before.get("count") == 1
            and before.get("permission") == "team",
            "membership_role": membership["snapshot"].get("role"),
            "get_status": readable["http_status"],
            "get_code": readable["code"],
            "update_status": attempted["http_status"],
            "update_rejected": update_rejected,
            "agent_unchanged": agent_unchanged,
            "membership_cleanup_succeeded": membership_cleanup["succeeded"],
            "agent_cleanup_succeeded": agent_cleanup,
            "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
        }
        passed = team_agent_contract_ok(observed)
        security_finding = attempted["code"] == 0 and not agent_unchanged
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "register_invite_and_accept_normal_team_member",
                    "secondary_ready": secondary_ready,
                    "invite_code": membership["invite"]["code"],
                    "accept_code": membership["accept"]["code"],
                    "database_role": membership["snapshot"].get("role"),
                    "raw_sha256": [
                        membership["invite"]["raw_sha256"],
                        membership["accept"]["raw_sha256"],
                    ],
                },
                {
                    "name": "create_team_agent_and_read_as_normal_member",
                    "agent_code": agent["code"],
                    "physical_permission": before.get("permission"),
                    "get_http_status": readable["http_status"],
                    "get_code": readable["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "raw_sha256": [agent["raw_sha256"], readable["raw_sha256"]],
                },
                {
                    "name": "team_member_attempt_owner_only_agent_update",
                    "http_status": attempted["http_status"],
                    "code": attempted["code"],
                    "update_rejected": update_rejected,
                    "agent_unchanged": agent_unchanged,
                    "title_changed_to_attempted_value": after_attempt.get("title") == attempted_title,
                    "raw_sha256": attempted["raw_sha256"],
                },
                {
                    "name": "owner_restore_then_api_cleanup_all_fixtures",
                    "restore_code": restore["code"],
                    "title_restored": restored.get("title") == original_title,
                    "membership_cleanup_succeeded": membership_cleanup["succeeded"],
                    "agent_cleanup_succeeded": agent_cleanup,
                    "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "membership_role": "normal",
                "read_response": [200, 0],
                "write_allowed": False,
                "database_mutation": False,
            },
            "findings": [
                {
                    "id": "CS-AGENT-TEAM-UPDATE-IDOR-001",
                    "summary": f"{group} normal team member successfully modified another tenant's Agent",
                    "code_location": "api/apps/restful_apis/agent_api.py:update_agent",
                    "severity": "high",
                }
            ]
            if security_finding
            else (
                []
                if passed
                else [
                    {
                        "id": "CS-AGENT-TEAM-ACCESS-001",
                        "summary": f"{group} team read or cleanup precondition did not meet the contract",
                        "code_location": "api/db/services/canvas_service.py:accessible",
                    }
                ]
            ),
        }

    return _run_case(case_id, execute)


def run_cs099() -> dict[str, Any]:
    case_id = "TC-CS-099"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        loopback = _private_connection_request(
            case_id,
            group,
            "reject_loopback_database_target",
            owner["auth"],
            {
                "db_type": "mysql",
                "host": "127.0.0.1",
                "port": 3306,
                "database": "test",
                "username": "root",
                "password": "test",
            },
            target_class="loopback",
        )
        allowed_payload = _load_gauss_connection_fixture()
        allowed_target_is_global = _host_is_global(str(allowed_payload["host"]))
        allowed = _private_connection_request(
            case_id,
            group,
            "connect_to_controlled_global_postgres_target",
            owner["auth"],
            allowed_payload,
            target_class="controlled_global_postgres",
        )
        lowered_message = str(loopback["message"] or "").lower()
        observed = {
            "loopback_status": loopback["http_status"],
            "loopback_failed": loopback["code"] not in (None, 0),
            "loopback_message_mentions_unsafe": any(fragment in lowered_message for fragment in ("non-public", "not allowed", "unsafe")),
            "allowed_target_is_global": allowed_target_is_global,
            "allowed_status": allowed["http_status"],
            "allowed_code": allowed["code"],
            "allowed_data_exact": allowed["data"] == "Database Connection Successful!",
        }
        passed = db_connection_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "reject_loopback_target_with_ssrf_guard",
                    "http_status": loopback["http_status"],
                    "code": loopback["code"],
                    "business_failure": observed["loopback_failed"],
                    "message_mentions_unsafe": observed["loopback_message_mentions_unsafe"],
                    "raw_sha256": loopback["raw_sha256"],
                },
                {
                    "name": "resolve_controlled_target_without_disclosing_it",
                    "target_is_global": allowed_target_is_global,
                    "host_fingerprint": DB._fingerprint(allowed_payload["host"]),
                    "connection_fields_present": all(
                        allowed_payload.get(field) not in (None, "")
                        for field in (
                            "database",
                            "username",
                            "host",
                            "port",
                            "password",
                        )
                    ),
                },
                {
                    "name": "execute_select_one_on_controlled_postgres_target",
                    "http_status": allowed["http_status"],
                    "code": allowed["code"],
                    "data_exact": observed["allowed_data_exact"],
                    "elapsed_seconds": round(float(allowed["elapsed_seconds"]), 3),
                    "raw_sha256": allowed["raw_sha256"],
                },
            ],
            "oracle": {
                "loopback": "HTTP 200 and business rejection identifying unsafe target",
                "allowed": [
                    200,
                    0,
                    "Database Connection Successful!",
                ],
                "connection_parameters_persisted": False,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-DB-CONNECTION-SSRF-001" if not (observed["loopback_failed"] and observed["loopback_message_mentions_unsafe"]) else "CS-DB-CONNECTION-GLOBAL-001",
                    "summary": (f"{group} database connection endpoint did not reject loopback safely or did not complete the controlled PostgreSQL SELECT 1 path"),
                    "code_location": "api/apps/restful_apis/agent_api.py:test_db_connection",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_cs100() -> dict[str, Any]:
    case_id = "TC-CS-100"
    prefix = "fresh-cs-100"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        dataset_preclean = DD._cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent_preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        dsl = _dataflow_template()
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_current_product_dataflow_agent",
            title=f"{prefix}-Pipeline",
            dsl=dsl,
            tags="dataflow-rerun",
            canvas_category="dataflow_canvas",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        dataset = DD._create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_dataflow_rerun_dataset",
            {"name": prefix},
        )
        dataset_data = dataset["data"] if isinstance(dataset["data"], dict) else {}
        dataset_id = str(dataset_data.get("id") or "")
        uploaded = DD._upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_real_dataflow_text_document",
            dataset_id,
            [
                (
                    f"{prefix}-document.txt",
                    ("Fresh DataFlow rerun validation document.\nAlpha beta gamma remain deterministic for both runs.\n").encode("ascii"),
                    "text/plain",
                )
            ],
        )
        uploaded_items = DD._uploaded_items([uploaded])
        document_id = str(uploaded_items[0].get("id") or "") if uploaded_items else ""
        attached = _request(
            case_id,
            group,
            "attach_document_to_dataflow_pipeline",
            owner["auth"],
            "PATCH",
            f"/datasets/{dataset_id}/documents/{document_id}",
            payload={"pipeline_id": agent_id},
        )
        parsed = _request(
            case_id,
            group,
            "start_initial_dataflow_parse",
            owner["auth"],
            "POST",
            f"/datasets/{dataset_id}/documents/parse",
            payload={"document_ids": [document_id]},
            timeout=120,
        )
        initial_progress = (
            _poll_document_terminal(
                case_id,
                group,
                owner["auth"],
                dataset_id,
                document_id,
                "initial",
            )
            if parsed["code"] == 0 and document_id
            else {
                "terminal": False,
                "timed_out": False,
                "poll_count": 0,
                "observations": [],
                "raw_sha256": [],
                "final": {},
            }
        )
        initial_logs = _poll_dataflow_logs(
            case_id,
            group,
            owner["auth"],
            dataset_id,
            document_id,
            agent_id,
            "initial",
            minimum_count=1,
        )
        initial_matches = initial_logs["matches"]
        first_log = initial_matches[0] if initial_matches else {}
        log_id = str(first_log.get("id") or "")
        detail = (
            _request(
                case_id,
                group,
                "get_real_dataflow_ingestion_log_detail",
                owner["auth"],
                "GET",
                f"/datasets/{dataset_id}/ingestions/{log_id}",
            )
            if log_id
            else {
                "http_status": 0,
                "code": None,
                "data": None,
                "raw_sha256": None,
            }
        )
        detail_data = detail["data"] if isinstance(detail.get("data"), dict) else {}
        detail_dsl = _json_value(detail_data.get("dsl"), {})
        if not isinstance(detail_dsl, dict):
            detail_dsl = {}
        components = detail_dsl.get("components") if isinstance(detail_dsl.get("components"), dict) else {}
        component_id = next(
            (
                str(component_key)
                for component_key, component in components.items()
                if isinstance(component, dict) and isinstance(component.get("obj"), dict) and component["obj"].get("component_name") == "Parser"
            ),
            "",
        )
        initial_database_logs = _pipeline_log_rows(group, document_id, agent_id) if document_id and agent_id else []
        rerun = (
            _request(
                case_id,
                group,
                "rerun_real_dataflow_from_parser_component",
                owner["auth"],
                "POST",
                "/agents/rerun",
                payload={
                    "id": log_id,
                    "dsl": detail_dsl,
                    "component_id": component_id,
                },
                timeout=120,
            )
            if log_id and detail_dsl and component_id
            else {
                "http_status": 0,
                "code": None,
                "raw_sha256": None,
            }
        )
        task_deadline = time.monotonic() + 60
        rerun_tasks: list[dict[str, Any]] = []
        while document_id and time.monotonic() < task_deadline:
            rerun_tasks = _dataflow_task_rows(group, document_id)
            if any(row.get("task_type") == "dataflow_rerun" for row in rerun_tasks):
                break
            time.sleep(0.25)
        rerun_task_observed = any(row.get("task_type") == "dataflow_rerun" for row in rerun_tasks)
        rerun_progress = (
            _poll_document_terminal(
                case_id,
                group,
                owner["auth"],
                dataset_id,
                document_id,
                "rerun",
            )
            if rerun.get("code") == 0 and document_id
            else {
                "terminal": False,
                "timed_out": False,
                "poll_count": 0,
                "observations": [],
                "raw_sha256": [],
                "final": {},
            }
        )
        final_logs = _poll_dataflow_logs(
            case_id,
            group,
            owner["auth"],
            dataset_id,
            document_id,
            agent_id,
            "rerun",
            minimum_count=len(initial_matches) + 1,
        )
        final_database_logs = _pipeline_log_rows(group, document_id, agent_id) if document_id and agent_id else []
        deleted_dataset = (
            DD._delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_dataflow_rerun_dataset",
                [dataset_id],
            )
            if dataset_id
            else {"code": None, "raw_sha256": None}
        )
        dataset_cleanup = (
            bool(dataset_id) and deleted_dataset["code"] == 0 and DD._dataset_snapshot(group, dataset_id).get("count") == 0 and not DD._dataset_ids_by_prefix(group, owner["tenant_id"], prefix)
        )
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        initial_final = initial_progress.get("final", {})
        rerun_final = rerun_progress.get("final", {})
        log_count_increased = len(final_logs["matches"]) > len(initial_matches) and len(final_database_logs) > len(initial_database_logs)
        observed = {
            "fixture_ready": dataset_preclean
            and agent_preclean
            and agent["code"] == 0
            and dataset["code"] == 0
            and uploaded["code"] == 0
            and attached["code"] == 0
            and parsed["code"] == 0
            and bool(agent_id)
            and bool(dataset_id)
            and bool(document_id),
            "initial_terminal": initial_progress["terminal"],
            "initial_progress_success": initial_final.get("run") == "DONE" and initial_final.get("progress") == 1.0,
            "log_id_present": bool(log_id),
            "dsl_present": bool(components),
            "component_present": bool(component_id),
            "rerun_status": rerun.get("http_status"),
            "rerun_code": rerun.get("code"),
            "rerun_task_observed": rerun_task_observed,
            "rerun_terminal": rerun_progress["terminal"],
            "rerun_progress_success": rerun_final.get("run") == "DONE" and rerun_final.get("progress") == 1.0,
            "log_count_increased": log_count_increased,
            "dataset_cleanup_succeeded": dataset_cleanup,
            "agent_cleanup_succeeded": agent_cleanup,
        }
        passed = dataflow_rerun_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_pipeline_dataset_and_real_text_document",
                    "precondition_clean": dataset_preclean and agent_preclean,
                    "agent_code": agent["code"],
                    "dataset_code": dataset["code"],
                    "upload_code": uploaded["code"],
                    "attach_code": attached["code"],
                    "parse_code": parsed["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "dataset_id_fingerprint": DB._fingerprint(dataset_id),
                    "document_id_fingerprint": DB._fingerprint(document_id),
                    "raw_sha256": [
                        agent["raw_sha256"],
                        dataset["raw_sha256"],
                        uploaded["raw_sha256"],
                        attached["raw_sha256"],
                        parsed["raw_sha256"],
                    ],
                },
                {
                    "name": "wait_for_initial_dataflow_and_get_real_log_dsl",
                    "terminal": initial_progress["terminal"],
                    "final": initial_final,
                    "progress_poll_count": initial_progress["poll_count"],
                    "log_poll_count": initial_logs["poll_count"],
                    "api_log_count": len(initial_matches),
                    "database_log_count": len(initial_database_logs),
                    "log_id_fingerprint": DB._fingerprint(log_id),
                    "detail_code": detail.get("code"),
                    "dsl_component_count": len(components),
                    "parser_component_id_fingerprint": DB._fingerprint(component_id),
                    "raw_sha256": [
                        *initial_progress["raw_sha256"],
                        *initial_logs["raw_sha256"],
                        detail.get("raw_sha256"),
                    ],
                },
                {
                    "name": "post_rerun_and_observe_dataflow_rerun_task",
                    "http_status": rerun.get("http_status"),
                    "code": rerun.get("code"),
                    "rerun_task_observed": rerun_task_observed,
                    "task_count": len(rerun_tasks),
                    "task_types": sorted({str(row.get("task_type") or "") for row in rerun_tasks}),
                    "raw_sha256": rerun.get("raw_sha256"),
                },
                {
                    "name": "wait_for_second_terminal_and_new_history_log",
                    "terminal": rerun_progress["terminal"],
                    "final": rerun_final,
                    "progress_poll_count": rerun_progress["poll_count"],
                    "log_poll_count": final_logs["poll_count"],
                    "api_log_count_before": len(initial_matches),
                    "api_log_count_after": len(final_logs["matches"]),
                    "database_log_count_before": len(initial_database_logs),
                    "database_log_count_after": len(final_database_logs),
                    "log_count_increased": log_count_increased,
                    "raw_sha256": [
                        *rerun_progress["raw_sha256"],
                        *final_logs["raw_sha256"],
                    ],
                },
                {
                    "name": "cleanup_dataset_and_agent_through_apis",
                    "dataset_delete_code": deleted_dataset["code"],
                    "dataset_cleanup_succeeded": dataset_cleanup,
                    "agent_cleanup_succeeded": agent_cleanup,
                    "historical_pipeline_log_count_after_cleanup": len(_pipeline_log_rows(group, document_id, agent_id)) if document_id and agent_id else 0,
                    "raw_sha256": deleted_dataset["raw_sha256"],
                },
            ],
            "oracle": {
                "initial_document": ["DONE", 1.0],
                "rerun_response": [200, 0],
                "physical_task_type": "dataflow_rerun",
                "second_document": ["DONE", 1.0],
                "new_pipeline_log": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CS-AGENT-DATAFLOW-RERUN-001",
                    "summary": f"{group} real DataFlow rerun did not satisfy the full task/log/terminal contract",
                    "code_location": "api/apps/restful_apis/agent_api.py:rerun_agent",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_supplement_chat_delete(case_id: str, mode: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    dedicated_email = f"{case_id.lower()}@fresh.invalid"
    dedicated_password = f"Fresh-{case_id}-Dedicated@1234"

    def execute(group: str, primary_owner: dict[str, str]) -> dict[str, Any]:
        secondary: dict[str, Any] | None = None
        owner = primary_owner
        secondary_ready = True
        if mode == "all":
            secondary = _prepare_secondary_user(case_id, group, dedicated_email, dedicated_password)
            owner = {
                "auth": secondary["auth"],
                "tenant_id": secondary["tenant_id"],
            }
            secondary_ready = secondary["preclean"] and secondary["registration"]["code"] == 0 and secondary["login"]["code"] == 0 and bool(owner["auth"]) and bool(owner["tenant_id"])
        preclean = (
            _cleanup_chat_prefix(
                case_id,
                group,
                owner["auth"],
                owner["tenant_id"],
                prefix,
            )
            if owner["auth"] and owner["tenant_id"]
            else False
        )
        fixture_count = 3 if mode in {"bulk", "all"} else 1
        created = _create_chat_batch(case_id, group, owner, prefix, fixture_count)
        ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in created]
        if mode == "bulk":
            deleted = _bulk_delete_chats(
                case_id,
                group,
                owner["auth"],
                "bulk_soft_delete_two_supplement_chats",
                {"ids": ids[:2]},
            )
        elif mode == "all":
            deleted = _bulk_delete_chats(
                case_id,
                group,
                owner["auth"],
                "delete_all_supplement_chats",
                {"delete_all": True},
            )
        else:
            deleted = _delete_chat(
                case_id,
                group,
                owner["auth"],
                "soft_delete_supplement_chat",
                ids[0] if ids else "",
            )
        rows = {row["id"]: row for row in _dialog_rows(group, ids)}
        listed = (
            _list_chats(
                case_id,
                group,
                owner["auth"],
                "list_after_supplement_soft_delete",
                {"keywords": prefix},
            )
            if mode == "list"
            else {
                "http_status": 200,
                "code": 0,
                "data": {"chats": []},
                "raw_sha256": None,
            }
        )
        listed_chats, _listed_total = _response_chats(listed)
        detail = (
            _get_chat(
                case_id,
                group,
                owner["auth"],
                "get_supplement_soft_deleted_chat",
                ids[0] if ids else "",
            )
            if mode == "detail"
            else {
                "http_status": 200,
                "code": 109,
                "message": "No authorization.",
                "data": False,
                "raw_sha256": None,
            }
        )
        delete_data = deleted["data"] if isinstance(deleted.get("data"), dict) else {}
        success_count = int(delete_data.get("success_count") or 0) if mode in {"bulk", "all"} else 1
        remaining_active = _tenant_active_chat_count(group, owner["tenant_id"])
        active_cleanup = _cleanup_chat_prefix(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
        )
        secondary_cleanup = _cleanup_secondary_user(case_id, group, dedicated_email) if secondary is not None else {"succeeded": True}
        fixture_ready = secondary_ready and preclean and len(ids) == fixture_count and len(set(ids)) == fixture_count and all(item["code"] == 0 for item in created)
        observed = {
            "fixture_ready": fixture_ready,
            "delete_status": deleted["http_status"],
            "delete_code": deleted["code"],
            "dialog_count": 1 if ids and ids[0] in rows else 0,
            "dialog_status": rows.get(ids[0], {}).get("status") if ids else None,
            "list_status": listed["http_status"],
            "list_code": listed["code"],
            "list_absent": all(str(item.get("id") or "") not in ids for item in listed_chats if isinstance(item, dict)),
            "detail_status": detail["http_status"],
            "detail_code": detail["code"],
            "detail_message_exact": detail.get("message") == "No authorization.",
            "detail_data_absent": detail.get("data") in (None, False),
            "success_count": success_count,
            "target_statuses_exact": len(ids) >= 2 and all(rows.get(item, {}).get("status") == "0" for item in ids[:2]),
            "untargeted_active": len(ids) == 3 and rows.get(ids[2], {}).get("status") == "1",
            "remaining_active": remaining_active,
            "all_statuses_zero": bool(ids) and all(rows.get(item, {}).get("status") == "0" for item in ids),
            "cleanup_succeeded": active_cleanup and secondary_cleanup["succeeded"],
        }
        passed = supplement_chat_delete_contract_ok(mode, observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_fresh_supplement_chat_fixture",
                    "mode": mode,
                    "secondary_ready": secondary_ready,
                    "precondition_clean": preclean,
                    "create_codes": [item["code"] for item in created],
                    "unique_id_count": len(set(ids)),
                    "id_fingerprints": [DB._fingerprint(item) for item in ids],
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "soft_delete_requested_supplement_scope",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "success_count": success_count,
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "read_only_soft_delete_oracle",
                    "dialog_count": observed["dialog_count"],
                    "dialog_status": observed["dialog_status"],
                    "list_absent": observed["list_absent"],
                    "detail_code": detail["code"],
                    "detail_message_exact": observed["detail_message_exact"],
                    "target_statuses_exact": observed["target_statuses_exact"],
                    "untargeted_active": observed["untargeted_active"],
                    "remaining_active_before_cleanup": remaining_active,
                    "all_statuses_zero": observed["all_statuses_zero"],
                    "raw_sha256": [
                        listed.get("raw_sha256"),
                        detail.get("raw_sha256"),
                    ],
                },
                {
                    "name": "cleanup_active_chat_and_dedicated_user_through_apis",
                    "active_chat_cleanup_succeeded": active_cleanup,
                    "secondary_cleanup_succeeded": secondary_cleanup["succeeded"],
                },
            ],
            "oracle": {
                "mode": mode,
                "soft_deleted_status": "0",
                "physical_row_retained": mode in {"single", "list", "detail"},
                "list_absent": mode == "list",
                "detail_response": [200, 109] if mode == "detail" else None,
                "bulk_success_count": 2 if mode == "bulk" else None,
                "delete_all_remaining_active": 0 if mode == "all" else None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CHAT-SUPPLEMENT-SOFT-DELETE-001",
                    "summary": f"{group} supplement soft-delete mode {mode} did not meet its physical/API contract",
                    "code_location": "api/apps/restful_apis/chat_api.py:delete",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_chat_del001() -> dict[str, Any]:
    return _run_supplement_chat_delete("TC-CHAT-DEL-001", "single")


def run_chat_del002() -> dict[str, Any]:
    return _run_supplement_chat_delete("TC-CHAT-DEL-002", "list")


def run_chat_del003() -> dict[str, Any]:
    return _run_supplement_chat_delete("TC-CHAT-DEL-003", "detail")


def run_chat_del004() -> dict[str, Any]:
    return _run_supplement_chat_delete("TC-CHAT-DEL-004", "bulk")


def run_chat_del005() -> dict[str, Any]:
    return _run_supplement_chat_delete("TC-CHAT-DEL-005", "all")


def _run_supplement_chat_patch(case_id: str, mode: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_chat_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        if mode == "prompt_config":
            field = "prompt_config"
            initial = {"system": "old prompt", "temperature": 0.7}
            patch_payload = {field: {"system": "new prompt"}}
        elif mode == "llm_setting":
            field = "llm_setting"
            initial = {"model": "gpt-4", "max_tokens": 1000}
            patch_payload = {field: {"max_tokens": 2000}}
        elif mode == "preserve_prompt":
            field = "prompt_config"
            initial = {
                "system": "unchanged supplement prompt",
                "prologue": "unchanged supplement prologue",
            }
            patch_payload = {"name": f"{prefix}-New-Name"}
        else:
            raise ValueError("unknown supplement chat patch mode")
        created = _create_chat(
            case_id,
            group,
            owner["auth"],
            "create_supplement_patch_chat",
            {"name": prefix, field: initial},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        chat_id = str(created_data.get("id") or "")
        before = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        baseline = before.get(field) if isinstance(before.get(field), dict) else {}
        expected = dict(baseline)
        if mode == "prompt_config":
            expected["system"] = "new prompt"
        elif mode == "llm_setting":
            expected["max_tokens"] = 2000
        patched = _patch_chat(
            case_id,
            group,
            owner["auth"],
            "patch_supplement_chat",
            chat_id,
            patch_payload,
        )
        patched_data = patched["data"] if isinstance(patched["data"], dict) else {}
        after = _chat_snapshot(group, chat_id) if chat_id else {"count": 0}
        api_value = patched_data.get(field)
        database_value = after.get(field)
        if mode == "preserve_prompt":
            api_value_exact = api_value == baseline
            database_value_exact = database_value == baseline
            unsubmitted_preserved = database_value == baseline
            name_updated = patched_data.get("name") == patch_payload["name"] and after.get("name") == patch_payload["name"]
        else:
            api_value_exact = api_value == expected
            database_value_exact = database_value == expected
            preserved_key = "temperature" if mode == "prompt_config" else "model"
            unsubmitted_preserved = expected.get(preserved_key) == baseline.get(preserved_key) and database_value.get(preserved_key) == baseline.get(preserved_key)
            name_updated = patched_data.get("name") == prefix and after.get("name") == prefix
        cleanup = _cleanup_created_chat(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            chat_id,
        )
        initial_exact = all(baseline.get(key) == value for key, value in initial.items())
        observed = {
            "fixture_ready": preclean and created["code"] == 0 and before.get("count") == 1 and initial_exact,
            "patch_status": patched["http_status"],
            "patch_code": patched["code"],
            "api_value_exact": api_value_exact,
            "database_value_exact": database_value_exact,
            "unsubmitted_value_preserved": unsubmitted_preserved,
            "name_updated": name_updated,
            "cleanup_succeeded": cleanup,
        }
        passed = supplement_chat_patch_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_exact_supplement_patch_baseline",
                    "mode": mode,
                    "create_code": created["code"],
                    "initial_exact": initial_exact,
                    "baseline_keys": sorted(baseline),
                    "chat_id_fingerprint": DB._fingerprint(chat_id),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "patch_only_requested_supplement_fields",
                    "http_status": patched["http_status"],
                    "code": patched["code"],
                    "api_value_exact": api_value_exact,
                    "name_updated_or_preserved": name_updated,
                    "raw_sha256": patched["raw_sha256"],
                },
                {
                    "name": "read_only_merge_and_preservation_verification",
                    "database_value_exact": database_value_exact,
                    "unsubmitted_value_preserved": unsubmitted_preserved,
                },
                {
                    "name": "soft_delete_supplement_patch_chat_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "mode": mode,
                "patch_response": [200, 0],
                "merge_not_replace": mode != "preserve_prompt",
                "prompt_unchanged": mode == "preserve_prompt",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "CHAT-SUPPLEMENT-PATCH-MERGE-001",
                    "summary": f"{group} supplement PATCH mode {mode} did not preserve or merge the expected JSON",
                    "code_location": "api/apps/restful_apis/chat_api.py:patch",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_chat_patch001() -> dict[str, Any]:
    return _run_supplement_chat_patch("TC-CHAT-PATCH-001", "prompt_config")


def run_chat_patch002() -> dict[str, Any]:
    return _run_supplement_chat_patch("TC-CHAT-PATCH-002", "llm_setting")


def run_chat_patch003() -> dict[str, Any]:
    return _run_supplement_chat_patch("TC-CHAT-PATCH-003", "preserve_prompt")


def _run_supplement_webhook(case_id: str, mode: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    expected_body = {"accepted": True, "case": case_id}
    limit = 1024 * 1024

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        security: dict[str, Any] = {
            "auth_type": "none",
            "allow_anonymous": True,
            "max_body_size": "1MB",
        }
        custom_headers: dict[str, str] | None = None
        raw_body: bytes | None = None
        message_fragment = ""
        expect_success = mode in {"token", "basic", "jwt", "anonymous"}
        if mode == "max_body":
            raw_body = b'{"payload":"' + (b"x" * (limit + 128)) + b'"}'
            message_fragment = "Request body too large"
        elif mode == "ip_whitelist":
            security["ip_whitelist"] = ["192.168.1.0/24"]
            message_fragment = "is not allowed by whitelist"
        elif mode in {"token", "wrong_token"}:
            security.update(
                {
                    "auth_type": "token",
                    "allow_anonymous": False,
                    "token": {
                        "token_header": "X-Webhook-Token",
                        "token_value": "secret123",
                    },
                }
            )
            custom_headers = {"X-Webhook-Token": "secret123" if mode == "token" else "wrong_token"}
            if mode == "wrong_token":
                message_fragment = "Invalid token authentication"
        elif mode == "basic":
            security.update(
                {
                    "auth_type": "basic",
                    "allow_anonymous": False,
                    "basic_auth": {"username": "user", "password": "pass"},
                }
            )
            encoded = base64.b64encode(b"user:pass").decode("ascii")
            custom_headers = {"Authorization": f"Basic {encoded}"}
        elif mode == "jwt":
            jwt_secret = "secret"
            security.update(
                {
                    "auth_type": "jwt",
                    "allow_anonymous": False,
                    "jwt": {"secret": jwt_secret, "algorithm": "HS256"},
                }
            )
            now = int(time.time())
            signed = jwt.encode(
                {"sub": "fresh-webhook", "iat": now, "exp": now + 300},
                jwt_secret,
                algorithm="HS256",
            )
            custom_headers = {"Authorization": f"Bearer {signed}"}
        elif mode == "anonymous":
            pass
        elif mode == "anonymous_denied":
            security["allow_anonymous"] = False
            message_fragment = "allow_anonymous"
        elif mode == "rate_limit":
            security["rate_limit"] = {"limit": 10, "per": "minute"}
        else:
            raise ValueError("unknown supplement webhook mode")
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_supplement_security_webhook_agent",
            title=f"{prefix}-Agent",
            dsl=_webhook_agent_dsl(expected_body, response_status=200, security=security),
            tags="supplement-webhook-security",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        responses: list[dict[str, Any]] = []
        rate_key = f"rl:tb:{agent_id}"
        rate_key_precleaned = True
        rate_key_cleanup_succeeded = True
        if mode == "rate_limit":
            redis = _redis_client(group)
            redis.delete(rate_key)
            rate_key_precleaned = not bool(redis.exists(rate_key))
            for index in range(1, 16):
                responses.append(
                    _webhook_request(
                        case_id,
                        group,
                        f"rate_limited_webhook_request_{index:02d}",
                        "POST",
                        f"/agents/{agent_id}/webhook",
                        payload={"data": f"rate-{index:02d}"},
                    )
                )
            redis.delete(rate_key)
            rate_key_cleanup_succeeded = not bool(redis.exists(rate_key))
        else:
            responses.append(
                _webhook_request(
                    case_id,
                    group,
                    "exercise_supplement_webhook_security",
                    "POST",
                    f"/agents/{agent_id}/webhook",
                    payload={"data": "test"},
                    custom_headers=custom_headers,
                    raw_body=raw_body,
                )
            )
        time.sleep(0.2)
        agent_cleanup = _cleanup_created_agents(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            [agent_id],
        )
        fixture_ready = preclean and agent["http_status"] == 200 and agent["code"] == 0 and bool(agent_id)
        if mode == "rate_limit":
            observed = {
                "fixture_ready": fixture_ready,
                "request_count": len(responses),
                "first_ten_statuses": [item["http_status"] for item in responses[:10]],
                "last_five_statuses": [item["http_status"] for item in responses[10:]],
                "last_five_codes": [item["code"] for item in responses[10:]],
                "last_five_messages_match": all("rate limit" in str(item["message"] or "").lower() for item in responses[10:]),
                "rate_key_precleaned": rate_key_precleaned,
                "rate_key_cleanup_succeeded": rate_key_cleanup_succeeded,
                "agent_cleanup_succeeded": agent_cleanup,
            }
            passed = supplement_webhook_rate_contract_ok(observed)
        else:
            response = responses[0]
            observed = {
                "fixture_ready": fixture_ready,
                "http_status": response["http_status"],
                "code": response["code"],
                "body_matches": response["body"] == expected_body,
                "message": response["message"],
                "cleanup_succeeded": agent_cleanup,
            }
            passed = supplement_webhook_security_contract_ok(
                observed,
                expect_success=expect_success,
                message_fragment=message_fragment,
            )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_exact_supplement_webhook_security_fixture",
                    "mode": mode,
                    "precondition_clean": preclean,
                    "agent_code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "auth_type": security.get("auth_type"),
                    "allow_anonymous": security.get("allow_anonymous"),
                    "max_body_size": security.get("max_body_size"),
                    "custom_header_names": sorted((custom_headers or {}).keys()),
                    "raw_sha256": agent["raw_sha256"],
                },
                {
                    "name": "exercise_supplement_webhook_security_contract",
                    "request_count": len(responses),
                    "http_statuses": [item["http_status"] for item in responses],
                    "codes": [item["code"] for item in responses],
                    "message_fragment": message_fragment,
                    "message_matches": all(not message_fragment or message_fragment.lower() in str(item["message"] or "").lower() for item in (responses[10:] if mode == "rate_limit" else responses)),
                    "request_body_length": len(raw_body) if raw_body is not None else None,
                    "configured_limit": limit if mode == "max_body" else None,
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_or_plan_authorized_rate_key_cleanup",
                    "rate_key_fingerprint": DB._fingerprint(rate_key) if mode == "rate_limit" else None,
                    "rate_key_precleaned": rate_key_precleaned,
                    "rate_key_cleanup_succeeded": rate_key_cleanup_succeeded,
                    "redis_fixture_write_authorized_by_plan": mode == "rate_limit",
                },
                {
                    "name": "cleanup_supplement_webhook_agent_through_api",
                    "agent_cleanup_succeeded": agent_cleanup,
                },
            ],
            "oracle": {
                "mode": mode,
                "success_http_status": 200 if expect_success else None,
                "rejection": [400, 400] if not expect_success and mode != "rate_limit" else None,
                "rate_limit_success_count": 10 if mode == "rate_limit" else None,
                "rate_limit_rejection_count": 5 if mode == "rate_limit" else None,
                "message_fragment": message_fragment or None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "AGENT-WEBHOOK-SECURITY-001",
                    "summary": f"{group} supplement webhook security mode {mode} did not meet its contract",
                    "code_location": "api/apps/restful_apis/agent_api.py:_webhook_impl",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_agent_wh001() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-001", "max_body")


def run_agent_wh002() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-002", "ip_whitelist")


def run_agent_wh003() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-003", "rate_limit")


def run_agent_wh004() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-004", "token")


def run_agent_wh005() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-005", "wrong_token")


def run_agent_wh006() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-006", "basic")


def run_agent_wh007() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-007", "jwt")


def run_agent_wh008() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-008", "anonymous")


def run_agent_wh009() -> dict[str, Any]:
    return _run_supplement_webhook("TC-AGENT-WH-009", "anonymous_denied")


def _run_supplement_agent_completion(case_id: str, mode: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    question = "Follow up question" if mode == "continuation" else "What is RAG?"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_session_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_supplement_completion_agent",
            title=f"{prefix}-Agent",
            dsl=_agent_dsl(probe=f"supplement-{mode}"),
            tags="supplement-completion",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        replica = _agent_replica_snapshot(group, agent_id, owner["tenant_id"]) if agent_id else {"exists": False, "ttl": -2}
        created_session = None
        requested_session_id = ""
        before_rows: list[dict[str, Any]] = []
        if mode == "continuation":
            created_session = _create_agent_session(
                case_id,
                group,
                owner["auth"],
                "create_existing_supplement_agent_session",
                agent_id,
                {"name": f"{prefix}-Session"},
            )
            created_session_data = created_session["data"] if isinstance(created_session["data"], dict) else {}
            requested_session_id = str(created_session_data.get("id") or "")
            before_rows = [row for row in _agent_session_rows(group, agent_id) if row["id"] == requested_session_id]
        if mode == "stream":
            streamed = _agent_sse_post(
                case_id,
                group,
                owner["auth"],
                "stream_supplement_agent_completion",
                {
                    "agent_id": agent_id,
                    "question": question,
                    "stream": True,
                },
                timeout=180,
            )
            events = streamed["events"] if isinstance(streamed.get("events"), list) else []
            session_ids = {str(event.get("session_id") or "") for event in events if isinstance(event, dict) and event.get("session_id")}
            returned_session_id = next(iter(session_ids)) if len(session_ids) == 1 else ""
            message_ids = {str(event.get("message_id") or "") for event in events if isinstance(event, dict) and event.get("message_id")}
            response: dict[str, Any] = {
                "http_status": streamed["http_status"],
                "code": 0 if streamed.get("parse_error_count") == 0 else None,
                "data": None,
                "raw_sha256": streamed["raw_sha256"],
            }
            output_nonempty = bool(_agent_events_answer(events).strip())
            returned_message_id = next(iter(message_ids)) if len(message_ids) == 1 else ""
        else:
            payload: dict[str, Any] = {
                "agent_id": agent_id,
                "question": question,
                "stream": False,
            }
            if requested_session_id:
                payload["session_id"] = requested_session_id
            response = _request(
                case_id,
                group,
                "run_supplement_agent_completion",
                owner["auth"],
                "POST",
                "/agents/chat/completions",
                payload=payload,
                timeout=180,
            )
            response_data = response["data"] if isinstance(response["data"], dict) else {}
            returned_session_id = str(response_data.get("session_id") or "")
            returned_message_id = str(response_data.get("message_id") or "")
            nested_data = response_data.get("data") if isinstance(response_data.get("data"), dict) else {}
            output_nonempty = bool(str(nested_data.get("content") or "").strip())
            events = []
            streamed = {}
        after_rows = [row for row in _agent_session_rows(group, agent_id) if row["id"] == returned_session_id] if agent_id and returned_session_id else []
        messages_persisted = (
            len(before_rows) == 1 and len(after_rows) == 1 and _agent_session_appended_completed_turn(before_rows[0], after_rows[0], question)
            if mode == "continuation"
            else _agent_sessions_have_completed_turns(after_rows, {question})
        )
        session_updated = mode != "continuation" or (
            len(before_rows) == 1
            and len(after_rows) == 1
            and before_rows[0].get("message") != after_rows[0].get("message")
            and int(after_rows[0].get("update_time") or 0) >= int(before_rows[0].get("update_time") or 0)
        )
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        fixture_ready = (
            preclean
            and agent["http_status"] == 200
            and agent["code"] == 0
            and bool(agent_id)
            and replica.get("exists") is True
            and (mode != "continuation" or (created_session is not None and created_session["code"] == 0 and bool(requested_session_id)))
        )
        common_observed = {
            "fixture_ready": fixture_ready,
            "http_status": response["http_status"],
            "code": response["code"],
            "session_id_present": bool(returned_session_id),
            "message_id_present": bool(returned_message_id),
            "database_session_count": len(after_rows),
            "messages_persisted": messages_persisted,
            "output_nonempty": output_nonempty,
            "session_id_unchanged": mode != "continuation" or returned_session_id == requested_session_id,
            "session_updated": session_updated,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        if mode == "stream":
            observed = {
                "fixture_ready": fixture_ready,
                "http_status": streamed["http_status"],
                "content_type_is_sse": "text/event-stream" in str(streamed.get("content_type") or ""),
                "parse_error_count": streamed.get("parse_error_count"),
                "done_count": streamed.get("done_count"),
                "message_event_present": any(isinstance(event, dict) and event.get("event") == "message" for event in events),
                "message_end_present": any(isinstance(event, dict) and event.get("event") == "message_end" for event in events),
                "answer_nonempty": output_nonempty,
                "error_absent": _agent_events_error_absent(events),
                "session_id_present": bool(returned_session_id),
                "database_session_count": len(after_rows),
                "messages_persisted": messages_persisted,
                "cleanup_succeeded": cleanup["succeeded"],
            }
            passed = supplement_agent_stream_contract_ok(observed)
        elif mode == "continuation":
            observed = common_observed
            passed = supplement_agent_continuation_contract_ok(observed)
        else:
            observed = common_observed
            passed = supplement_agent_new_session_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_supplement_agent_and_runtime_replica",
                    "mode": mode,
                    "agent_code": agent["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "replica_exists": replica.get("exists"),
                    "replica_ttl_positive": int(replica.get("ttl") or -2) > 0,
                    "created_session_code": created_session.get("code") if created_session is not None else None,
                    "requested_session_id_fingerprint": DB._fingerprint(requested_session_id) if requested_session_id else None,
                    "raw_sha256": [
                        agent["raw_sha256"],
                        created_session.get("raw_sha256") if created_session is not None else None,
                    ],
                },
                {
                    "name": "run_supplement_agent_completion_mode",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "session_id_fingerprint": DB._fingerprint(returned_session_id),
                    "message_id_fingerprint": DB._fingerprint(returned_message_id),
                    "output_nonempty": output_nonempty,
                    "event_count": len(events) if mode == "stream" else None,
                    "done_count": streamed.get("done_count") if mode == "stream" else None,
                    "parse_error_count": streamed.get("parse_error_count") if mode == "stream" else None,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_workflow_session_persistence_verification",
                    "database_session_count": len(after_rows),
                    "messages_persisted": messages_persisted,
                    "session_id_unchanged": common_observed["session_id_unchanged"],
                    "session_updated": session_updated,
                },
                {
                    "name": "cleanup_supplement_agent_session_and_agent_through_apis",
                    **cleanup,
                },
            ],
            "oracle": {
                "mode": mode,
                "response_http_status": 200,
                "new_or_same_session_persisted": True,
                "messages_per_completed_session": 2,
                "stream_message_and_end_events": mode == "stream",
                "stream_done_count": 1 if mode == "stream" else None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "AGENT-SUPPLEMENT-COMPLETION-001",
                    "summary": f"{group} supplement Agent completion mode {mode} did not meet its session/wire contract",
                    "code_location": "api/apps/restful_apis/agent_api.py:agent_chat_completion",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_agent_comp001() -> dict[str, Any]:
    return _run_supplement_agent_completion("TC-AGENT-COMP-001", "new_session")


def run_agent_comp002() -> dict[str, Any]:
    return _run_supplement_agent_completion("TC-AGENT-COMP-002", "continuation")


def run_agent_comp003() -> dict[str, Any]:
    return _run_supplement_agent_completion("TC-AGENT-COMP-003", "stream")


def _run_supplement_dataflow_completion(case_id: str, *, cancel: bool) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    filename = f"{prefix}-input.txt"
    content = ("Fresh supplement DataFlow completion input.\nThis ASCII content is unique to the current execution.\n").encode("ascii")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        preclean = _cleanup_agent_session_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        agent = _create_agent(
            case_id,
            group,
            owner["auth"],
            "create_supplement_dataflow_agent",
            title=f"{prefix}-Pipeline",
            dsl=_dataflow_template(),
            tags="supplement-dataflow-completion",
            canvas_category="dataflow_canvas",
        )
        agent_data = agent["data"] if isinstance(agent["data"], dict) else {}
        agent_id = str(agent_data.get("id") or "")
        uploaded = _request(
            case_id,
            group,
            "upload_supplement_dataflow_input",
            owner["auth"],
            "POST",
            f"/agents/{agent_id}/upload",
            files=[("file", (filename, content, "text/plain"))],
            timeout=120,
        )
        upload_data = uploaded["data"] if isinstance(uploaded["data"], dict) else {}
        completion = _request(
            case_id,
            group,
            "queue_supplement_dataflow_completion",
            owner["auth"],
            "POST",
            "/agents/chat/completions",
            payload={
                "agent_id": agent_id,
                "question": "Process data",
                "files": [upload_data],
                "stream": False,
            },
            timeout=120,
        )
        completion_data = completion["data"] if isinstance(completion["data"], dict) else {}
        message_id = str(completion_data.get("message_id") or "")
        session_id = str(completion_data.get("session_id") or "")
        queued_snapshot = (
            _wait_task_snapshot(
                group,
                message_id,
                lambda item: item.get("count") == 1,
                timeout=10,
            )
            if message_id
            else {"count": 0}
        )
        cancel_response: dict[str, Any] = {
            "http_status": None,
            "code": None,
            "data": None,
            "raw_sha256": None,
        }
        redis_cancel_flag_present = False
        if cancel and message_id:
            cancel_response = _request(
                case_id,
                group,
                "cancel_supplement_dataflow_task",
                owner["auth"],
                "POST",
                f"/tasks/{message_id}/cancel",
            )
            task_snapshot = _wait_task_snapshot(
                group,
                message_id,
                lambda item: item.get("progress") == -1.0,
                timeout=30,
            )
            redis_cancel_flag_present = _redis_client(group).get(f"{message_id}-cancel") is not None
            time.sleep(0.5)
            task_snapshot = _task_snapshot(group, message_id)
        else:
            task_snapshot = (
                _wait_task_snapshot(
                    group,
                    message_id,
                    lambda item: item.get("progress") in {-1.0, 1.0},
                    timeout=180,
                )
                if message_id
                else {"count": 0}
            )
        session_rows = [row for row in _agent_session_rows(group, agent_id) if row["id"] == session_id] if agent_id and session_id else []
        cleanup = _cleanup_agent_sessions_and_agent(
            case_id,
            group,
            owner["auth"],
            owner["tenant_id"],
            prefix,
            agent_id,
        )
        observed = {
            "fixture_ready": preclean and agent["code"] == 0 and uploaded["code"] == 0 and bool(agent_id) and bool(upload_data.get("id")),
            "http_status": completion["http_status"],
            "code": completion["code"],
            "message_id_present": bool(message_id),
            "session_id_present": bool(session_id),
            "task_count": task_snapshot.get("count"),
            "task_id_matches": task_snapshot.get("id") == message_id,
            "task_type": task_snapshot.get("task_type"),
            "doc_id": task_snapshot.get("doc_id"),
            "session_count": len(session_rows),
            "agent_cleanup_succeeded": cleanup["agent_removed"],
            "session_cleanup_succeeded": cleanup["sessions_removed"],
            "cancel_status": cancel_response["http_status"],
            "cancel_code": cancel_response["code"],
            "cancel_data_true": cancel_response["data"] is True,
            "redis_cancel_flag_present": redis_cancel_flag_present,
            "task_progress": task_snapshot.get("progress"),
        }
        passed = supplement_task_cancel_contract_ok(observed) if cancel else supplement_dataflow_completion_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataflow_agent_and_upload_fresh_input",
                    "precondition_clean": preclean,
                    "agent_code": agent["code"],
                    "upload_code": uploaded["code"],
                    "agent_id_fingerprint": DB._fingerprint(agent_id),
                    "file_id_fingerprint": DB._fingerprint(upload_data.get("id")),
                    "file_size": len(content),
                    "file_sha256": hashlib.sha256(content).hexdigest(),
                    "raw_sha256": [agent["raw_sha256"], uploaded["raw_sha256"]],
                },
                {
                    "name": "queue_dataflow_through_agent_completion",
                    "http_status": completion["http_status"],
                    "code": completion["code"],
                    "message_id_fingerprint": DB._fingerprint(message_id),
                    "session_id_fingerprint": DB._fingerprint(session_id),
                    "task_observed_immediately": queued_snapshot.get("count") == 1,
                    "raw_sha256": completion["raw_sha256"],
                },
                {
                    "name": "cancel_and_verify_flag_if_requested",
                    "cancel_requested": cancel,
                    "http_status": cancel_response["http_status"],
                    "code": cancel_response["code"],
                    "data_true": cancel_response["data"] is True,
                    "redis_cancel_flag_present": redis_cancel_flag_present,
                    "cancel_key_fingerprint": DB._fingerprint(f"{message_id}-cancel") if message_id else None,
                    "raw_sha256": cancel_response["raw_sha256"],
                },
                {
                    "name": "read_only_task_and_workflow_session_verification",
                    "task_count": task_snapshot.get("count"),
                    "task_id_matches": task_snapshot.get("id") == message_id,
                    "task_type": task_snapshot.get("task_type"),
                    "doc_id": task_snapshot.get("doc_id"),
                    "progress": task_snapshot.get("progress"),
                    "session_count": len(session_rows),
                    "session_source": session_rows[0].get("source") if len(session_rows) == 1 else None,
                },
                {
                    "name": "cleanup_dataflow_session_and_agent_through_apis",
                    **cleanup,
                    "agent_file_delete_endpoint_available": False,
                    "task_delete_endpoint_available": False,
                    "direct_storage_or_database_cleanup_performed": False,
                },
            ],
            "oracle": {
                "completion_response": [200, 0],
                "task_type": "dataflow",
                "doc_id": "dataflow_x",
                "workflow_session_count": 1,
                "cancel_response": [200, 0] if cancel else None,
                "cancel_progress": -1.0 if cancel else None,
                "redis_cancel_flag": True if cancel else None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "AGENT-SUPPLEMENT-DATAFLOW-CANCEL-001" if cancel else "AGENT-SUPPLEMENT-DATAFLOW-QUEUE-001",
                    "summary": f"{group} supplement DataFlow completion/cancel contract did not pass",
                    "code_location": "api/apps/restful_apis/task_api.py:_cancel_task" if cancel else "api/apps/restful_apis/agent_api.py:agent_chat_completion",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_agent_comp004() -> dict[str, Any]:
    return _run_supplement_dataflow_completion("TC-AGENT-COMP-004", cancel=False)


def run_agent_comp005() -> dict[str, Any]:
    return _run_supplement_dataflow_completion("TC-AGENT-COMP-005", cancel=True)


RUNNERS: dict[str, Callable[[], dict[str, Any]]] = {
    "TC-CS-001": run_cs001,
    "TC-CS-002": run_cs002,
    "TC-CS-003": run_cs003,
    "TC-CS-004": run_cs004,
    "TC-CS-005": run_cs005,
    "TC-CS-006": run_cs006,
    "TC-CS-007": run_cs007,
    "TC-CS-008": run_cs008,
    "TC-CS-009": run_cs009,
    "TC-CS-010": run_cs010,
    "TC-CS-011": run_cs011,
    "TC-CS-012": run_cs012,
    "TC-CS-013": run_cs013,
    "TC-CS-014": run_cs014,
    "TC-CS-015": run_cs015,
    "TC-CS-016": run_cs016,
    "TC-CS-017": run_cs017,
    "TC-CS-018": run_cs018,
    "TC-CS-019": run_cs019,
    "TC-CS-020": run_cs020,
    "TC-CS-021": run_cs021,
    "TC-CS-022": run_cs022,
    "TC-CS-023": run_cs023,
    "TC-CS-024": run_cs024,
    "TC-CS-025": run_cs025,
    "TC-CS-026": run_cs026,
    "TC-CS-027": run_cs027,
    "TC-CS-028": run_cs028,
    "TC-CS-029": run_cs029,
    "TC-CS-030": run_cs030,
    "TC-CS-031": run_cs031,
    "TC-CS-032": run_cs032,
    "TC-CS-033": run_cs033,
    "TC-CS-034": run_cs034,
    "TC-CS-035": run_cs035,
    "TC-CS-036": run_cs036,
    "TC-CS-037": run_cs037,
    "TC-CS-038": run_cs038,
    "TC-CS-039": run_cs039,
    "TC-CS-040": run_cs040,
    "TC-CS-041": run_cs041,
    "TC-CS-042": run_cs042,
    "TC-CS-043": run_cs043,
    "TC-CS-044": run_cs044,
    "TC-CS-045": run_cs045,
    "TC-CS-046": run_cs046,
    "TC-CS-047": run_cs047,
    "TC-CS-048": run_cs048,
    "TC-CS-049": run_cs049,
    "TC-CS-050": run_cs050,
    "TC-CS-051": run_cs051,
    "TC-CS-052": run_cs052,
    "TC-CS-053": run_cs053,
    "TC-CS-054": run_cs054,
    "TC-CS-055": run_cs055,
    "TC-CS-056": run_cs056,
    "TC-CS-057": run_cs057,
    "TC-CS-058": run_cs058,
    "TC-CS-059": run_cs059,
    "TC-CS-060": run_cs060,
    "TC-CS-061": run_cs061,
    "TC-CS-062": run_cs062,
    "TC-CS-063": run_cs063,
    "TC-CS-064": run_cs064,
    "TC-CS-065": run_cs065,
    "TC-CS-066": run_cs066,
    "TC-CS-067": run_cs067,
    "TC-CS-068": run_cs068,
    "TC-CS-069": run_cs069,
    "TC-CS-070": run_cs070,
    "TC-CS-071": run_cs071,
    "TC-CS-072": run_cs072,
    "TC-CS-073": run_cs073,
    "TC-CS-074": run_cs074,
    "TC-CS-075": run_cs075,
    "TC-CS-076": run_cs076,
    "TC-CS-077": run_cs077,
    "TC-CS-078": run_cs078,
    "TC-CS-079": run_cs079,
    "TC-CS-080": run_cs080,
    "TC-CS-081": run_cs081,
    "TC-CS-082": run_cs082,
    "TC-CS-083": run_cs083,
    "TC-CS-084": run_cs084,
    "TC-CS-085": run_cs085,
    "TC-CS-086": run_cs086,
    "TC-CS-087": run_cs087,
    "TC-CS-088": run_cs088,
    "TC-CS-089": run_cs089,
    "TC-CS-090": run_cs090,
    "TC-CS-091": run_cs091,
    "TC-CS-092": run_cs092,
    "TC-CS-093": run_cs093,
    "TC-CS-094": run_cs094,
    "TC-CS-095": run_cs095,
    "TC-CS-096": run_cs096,
    "TC-CS-097": run_cs097,
    "TC-CS-098": run_cs098,
    "TC-CS-099": run_cs099,
    "TC-CS-100": run_cs100,
    "TC-CHAT-DEL-001": run_chat_del001,
    "TC-CHAT-DEL-002": run_chat_del002,
    "TC-CHAT-DEL-003": run_chat_del003,
    "TC-CHAT-DEL-004": run_chat_del004,
    "TC-CHAT-DEL-005": run_chat_del005,
    "TC-CHAT-PATCH-001": run_chat_patch001,
    "TC-CHAT-PATCH-002": run_chat_patch002,
    "TC-CHAT-PATCH-003": run_chat_patch003,
    "TC-AGENT-WH-001": run_agent_wh001,
    "TC-AGENT-WH-002": run_agent_wh002,
    "TC-AGENT-WH-003": run_agent_wh003,
    "TC-AGENT-WH-004": run_agent_wh004,
    "TC-AGENT-WH-005": run_agent_wh005,
    "TC-AGENT-WH-006": run_agent_wh006,
    "TC-AGENT-WH-007": run_agent_wh007,
    "TC-AGENT-WH-008": run_agent_wh008,
    "TC-AGENT-WH-009": run_agent_wh009,
    "TC-AGENT-COMP-001": run_agent_comp001,
    "TC-AGENT-COMP-002": run_agent_comp002,
    "TC-AGENT-COMP-003": run_agent_comp003,
    "TC-AGENT-COMP-004": run_agent_comp004,
    "TC-AGENT-COMP-005": run_agent_comp005,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh chat/session/agent cases")
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
