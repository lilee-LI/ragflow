#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path
from typing import Any, Callable

import requests

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID as BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)
from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_runner_result import pair_exit_code

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
EVIDENCE_DIR = evidence_dir("04_dataset_document")
RAW_DIR = EVIDENCE_DIR / "raw"
PLAN_FILES = (
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "04_dataset_document.md",
    EXECUTE_DIR.parent / "gaussdb_test_plan" / "04_dataset_document_supplement.md",
)


def _load_module(filename: str, name: str):
    path = EXECUTE_DIR / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


AUTH = _load_module("fresh_03_auth.py", "fresh_04_auth_base")
DB = AUTH.BASE
AUTH.EVIDENCE_DIR = EVIDENCE_DIR
AUTH.RAW_DIR = RAW_DIR
DB.EVIDENCE_DIR = EVIDENCE_DIR
DB.RAW_DIR = RAW_DIR


def _case_titles() -> dict[str, str]:
    result: dict[str, str] = {}
    pattern = re.compile(r"^### (TC-DD(?:-[A-Z0-9]+)*-\d{3}):\s*(.+)$", re.MULTILINE)
    for path in PLAN_FILES:
        for case_id, title in pattern.findall(path.read_text(encoding="utf-8")):
            if case_id in result:
                raise ValueError(f"duplicate case id: {case_id}")
            result[case_id] = title.strip()
    if len(result) != 98:
        raise ValueError(f"expected 98 dataset/document cases, found {len(result)}")
    return result


CASE_TITLES = _case_titles()


def dataset_create_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("id_present") is True
        and observed.get("name_matches") is True
        and observed.get("database_count") == 1
        and observed.get("tenant_matches") is True
        and observed.get("embedding_inherited") is True
        and observed.get("description_is_null") is True
        and observed.get("defaults_match") is True
    )


def validation_rejection_contract_ok(observed: dict[str, Any], *, expected_code: int | None = None) -> bool:
    return observed.get("http_status") == 200 and observed.get("code") != 0 and (expected_code is None or observed.get("code") == expected_code) and observed.get("database_count") == 0


def pagination_contract_ok(observed: dict[str, Any], expected_sizes: list[int]) -> bool:
    return (
        observed.get("page_sizes") == expected_sizes
        and observed.get("pairwise_disjoint") is True
        and observed.get("stable_total") is True
        and observed.get("combined_matches_database") is True
        and observed.get("descending_order") is True
    )


def empty_embedding_storage_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    common = observed.get("orm_value") == "" and observed.get("api_value") == "" and observed.get("restored") is True
    if group == "control":
        return common and observed.get("physical_is_empty") is True and observed.get("physical_length") == 0
    if group == "experiment":
        return common and observed.get("physical_is_null") is True and observed.get("physical_length") is None
    raise ValueError("unknown group")


def tenant_isolation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        all(code == 0 for code in observed.get("api_codes", []))
        and bool(observed.get("api_codes"))
        and observed.get("database_row_count") == 2
        and observed.get("tenant_ids_distinct") is True
        and observed.get("database_owners_match") is True
        and observed.get("a_result_ids") == [observed.get("expected_a_id")]
        and observed.get("b_result_ids") == [observed.get("expected_b_id")]
        and observed.get("cleanup_succeeded") is True
    )


def dataset_search_contract_ok(observed: dict[str, Any]) -> bool:
    expected = set(observed.get("expected_chunk_ids", []))
    actual = set(observed.get("result_chunk_ids", []))
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("chunks_persisted") is True
        and observed.get("chunk_shape_present") is True
        and bool(expected)
        and expected.issubset(actual)
        and int(observed.get("total", 0)) >= len(expected)
        and observed.get("similarity_nonincreasing") is True
        and observed.get("cleanup_succeeded") is True
    )


def dataset_cascade_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("document_count_before") == 3
        and observed.get("all_documents_have_multiple_chunks") is True
        and int(observed.get("task_count_before", 0)) >= 3
        and observed.get("file_count_before") == 3
        and observed.get("file_link_count_before") == 3
        and observed.get("all_objects_exist_before") is True
        and observed.get("all_chunks_exist_before") is True
        and observed.get("delete_success_count") == 1
        and all(
            observed.get(field) == 0
            for field in (
                "dataset_count_after",
                "document_count_after",
                "task_count_after",
                "file_count_after",
                "file_link_count_after",
                "object_count_after",
                "chunk_count_after",
            )
        )
    )


def tag_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("fixture_persisted") is True
        and observed.get("actual_tag_counts") == observed.get("expected_tag_counts")
        and observed.get("mutation_visible_in_chunks") is True
        and observed.get("cleanup_succeeded") is True
    )


def flattened_metadata_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("actual_metadata") == observed.get("expected_metadata")
        and observed.get("unselected_document_absent") is True
        and observed.get("cleanup_succeeded") is True
    )


def metadata_config_contract_ok(observed: dict[str, Any]) -> bool:
    expected = observed.get("expected_config")
    update_config = observed.get("update_config")
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and expected == observed.get("api_config")
        and expected == observed.get("database_config")
        and (update_config is None or update_config == expected)
        and ("independent_column_absent" not in observed or observed.get("independent_column_absent") is True)
        and observed.get("cleanup_succeeded") is True
    )


def document_upload_contract_ok(observed: dict[str, Any]) -> bool:
    expected_count = observed.get("expected_count")
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and expected_count is not None
        and observed.get("response_count") == expected_count
        and observed.get("database_count") == expected_count
        and observed.get("file_count") == expected_count
        and observed.get("file_link_count") == expected_count
        and sorted(observed.get("response_ids", [])) == sorted(observed.get("database_ids", []))
        and observed.get("actual_names") == observed.get("expected_names")
        and observed.get("actual_types") == observed.get("expected_types")
        and observed.get("actual_suffixes") == observed.get("expected_suffixes")
        and observed.get("actual_sizes") == observed.get("expected_sizes")
        and observed.get("initial_status_and_run_match") is True
        and observed.get("database_rows_match_dataset") is True
        and observed.get("file_rows_match_documents") is True
        and observed.get("response_rows_match_database") is True
        and observed.get("cleanup_succeeded") is True
    )


def upload_limit_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("over_limit_http_status") == 413
        and observed.get("over_limit_metadata_count") == 0
        and observed.get("over_limit_object_absent") is True
        and observed.get("under_limit_code") == 0
        and observed.get("under_limit_persisted") is True
        and observed.get("isolated_process_stopped") is True
        and observed.get("main_api_healthy") is True
        and observed.get("cleanup_succeeded") is True
    )


def document_list_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("http_status") == 200
        and observed.get("code") == 0
        and observed.get("actual_total") == observed.get("expected_total")
        and observed.get("actual_returned_count") == observed.get("expected_returned_count")
        and observed.get("ids_unique") is True
        and observed.get("all_results_belong_to_dataset") is True
        and observed.get("filter_exact") is True
        and observed.get("rows_match_database") is True
        and observed.get("cleanup_succeeded") is True
    )


def document_update_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("api_code") == 0
        and observed.get("api_matches") is True
        and observed.get("database_matches") is True
        and observed.get("side_effects_match") is True
        and observed.get("docstore_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def document_delete_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("api_code") == 0
        and observed.get("actual_deleted_count") == observed.get("expected_deleted_count")
        and observed.get("precondition_matches") is True
        and observed.get("metadata_resources_removed") is True
        and observed.get("docstore_resources_removed") is True
        and observed.get("storage_objects_removed") is True
        and observed.get("dataset_counts_zero") is True
        and observed.get("cleanup_succeeded") is True
    )


def document_parse_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("trigger_matches") is True
        and observed.get("lifecycle_matches") is True
        and observed.get("final_api_matches") is True
        and observed.get("chunk_contract_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def chunk_operation_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("precondition_matches") is True
        and observed.get("api_readback_matches") is True
        and observed.get("docstore_matches") is True
        and observed.get("counter_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def retrieval_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("fixtures_ready") is True
        and observed.get("result_count_positive") is True
        and observed.get("expected_sources_present") is True
        and observed.get("only_selected_sources") is True
        and observed.get("similarity_nonincreasing") is True
        and observed.get("threshold_satisfied") is True
        and observed.get("chunk_shape_matches") is True
        and observed.get("cleanup_succeeded") is True
    )


def threshold_filter_contract_ok(
    *,
    baseline_ids: list[str],
    filtered_chunks: list[dict[str, Any]],
    threshold: float,
) -> bool:
    baseline = {str(chunk_id) for chunk_id in baseline_ids if chunk_id}
    if not baseline:
        return False
    return all(str(item.get("id") or "") in baseline and item.get("similarity") is not None and float(item["similarity"]) >= threshold for item in filtered_chunks)


def thumbnail_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("precondition_clean") is True
        and observed.get("document_fixture_ready") is True
        and observed.get("parse_requirement_satisfied") is True
        and observed.get("response_mapping_exact") is True
        and observed.get("thumbnail_value_valid") is True
        and observed.get("expected_empty_satisfied") is True
        and observed.get("cleanup_succeeded") is True
    )


def private_dataset_denial_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("precondition_clean") is True
        and observed.get("tenant_ids_distinct") is True
        and observed.get("private_dataset_ready") is True
        and observed.get("denial_http_status") == 200
        and observed.get("denial_code") == 102
        and observed.get("denial_message_matches") is True
        and observed.get("data_not_leaked") is True
        and observed.get("cleanup_succeeded") is True
    )


def invalid_token_contract_ok(observed: dict[str, Any]) -> bool:
    return observed.get("http_status") == 401 and observed.get("code") == 401 and observed.get("unauthorized_message") is True and observed.get("data_not_leaked") is True


def concurrency_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("setup_code") == 0
        and observed.get("precondition_clean") is True
        and observed.get("max_workers") == 2
        and observed.get("group_within_timeout") is True
        and observed.get("all_tasks_completed") is True
        and observed.get("all_requests_succeeded") is True
        and observed.get("upload_count") == 5
        and observed.get("database_document_count") == 5
        and observed.get("document_graph_consistent") is True
        and observed.get("update_persisted") is True
        and observed.get("list_observed_dataset") is True
        and observed.get("resource_watermark_observed") is True
        and observed.get("cleanup_succeeded") is True
    )


def latency_summary(values: list[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "sample_count": 0,
            "minimum_seconds": None,
            "maximum_seconds": None,
            "p50_seconds": None,
            "p95_seconds": None,
        }

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return round(
            ordered[lower] * (1.0 - weight) + ordered[upper] * weight,
            6,
        )

    return {
        "sample_count": len(ordered),
        "minimum_seconds": round(ordered[0], 6),
        "maximum_seconds": round(ordered[-1], 6),
        "p50_seconds": percentile(0.50),
        "p95_seconds": percentile(0.95),
    }


def performance_baseline_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("fixture_api_codes_all_zero") is True
        and observed.get("database_document_count") == 100
        and observed.get("database_chunk_count") == 100
        and observed.get("docstore_chunk_count") == 100
        and observed.get("selected_document_counts_match") is True
        and observed.get("measurement_shape_matches") is True
        and observed.get("all_measurement_requests_succeeded") is True
        and observed.get("all_operation_contracts_match") is True
        and observed.get("all_latencies_positive") is True
        and observed.get("cleanup_succeeded") is True
    )


def docstore_dataset_cleanup_contract_ok(group: str, snapshot: dict[str, Any]) -> bool:
    dataset_rows_removed = int(snapshot.get("total") or 0) == 0 and not snapshot.get("existing_ids")
    if group == "control":
        return dataset_rows_removed and snapshot.get("index_exists") is False
    if group == "experiment":
        return dataset_rows_removed
    return False


def cascade_subset_contract_ok(observed: dict[str, Any], *, required_checks: list[str]) -> bool:
    return (
        bool(observed.get("api_codes"))
        and all(code == 0 for code in observed.get("api_codes", []))
        and observed.get("precondition_ready") is True
        and observed.get("delete_http_status") == 200
        and observed.get("delete_code") == 0
        and observed.get("delete_success_count") == 1
        and bool(required_checks)
        and all(observed.get(check) is True for check in required_checks)
        and observed.get("final_metadata_cleanup") is True
    )


def delete_all_contract_ok(observed: dict[str, Any]) -> bool:
    return (
        observed.get("precondition_clean") is True
        and observed.get("create_codes") == [0, 0, 0]
        and observed.get("created_count") == 3
        and observed.get("delete_http_status") == 200
        and observed.get("delete_code") == 0
        and observed.get("delete_success_count") == 3
        and observed.get("remaining_tenant_dataset_count") == 0
    )


def _valid_index_progress(value: Any) -> bool:
    try:
        progress = float(value)
    except (TypeError, ValueError):
        return False
    return progress == -1.0 or 0.0 <= progress <= 1.0


def index_schedule_contract_ok(observed: dict[str, Any]) -> bool:
    task_id = str(observed.get("response_task_id") or "")
    fixture_codes = observed.get("fixture_api_codes", [])
    return (
        observed.get("precondition_clean") is True
        and bool(fixture_codes)
        and all(code == 0 for code in fixture_codes)
        and observed.get("run_http_status") == 200
        and observed.get("run_code") == 0
        and bool(task_id)
        and str(observed.get("database_task_id") or "") == task_id
        and observed.get("task_row_count") == 1
        and observed.get("database_task_type") == observed.get("expected_task_type")
        and _valid_index_progress(observed.get("database_progress"))
        and observed.get("cleanup_succeeded") is True
    )


def index_trace_contract_ok(observed: dict[str, Any]) -> bool:
    fixture_codes = observed.get("fixture_api_codes", [])
    operations = observed.get("operations")
    if not isinstance(operations, dict) or set(operations) != {
        "graph",
        "raptor",
        "mindmap",
    }:
        return False
    for operation in operations.values():
        if not isinstance(operation, dict):
            return False
        response_task_id = str(operation.get("response_task_id") or "")
        if not (
            operation.get("run_code") == 0
            and operation.get("trace_http_status") == 200
            and operation.get("trace_code") == 0
            and bool(response_task_id)
            and str(operation.get("trace_task_id") or "") == response_task_id
            and str(operation.get("database_task_id") or "") == response_task_id
            and operation.get("trace_task_type") == operation.get("expected_task_type")
            and _valid_index_progress(operation.get("trace_progress"))
            and _valid_index_progress(operation.get("database_progress"))
            and operation.get("cleanup_code") == 0
            and operation.get("cleanup_task_cleared") is True
        ):
            return False
    return observed.get("precondition_clean") is True and bool(fixture_codes) and all(code == 0 for code in fixture_codes) and observed.get("dataset_cleanup_succeeded") is True


def index_delete_contract_ok(observed: dict[str, Any]) -> bool:
    fixture_codes = observed.get("fixture_api_codes", [])
    operations = observed.get("operations")
    if not isinstance(operations, dict) or set(operations) != {
        "graph",
        "raptor",
        "mindmap",
    }:
        return False
    for index_type, operation in operations.items():
        if not isinstance(operation, dict):
            return False
        common = (
            operation.get("run_code") == 0
            and operation.get("delete_http_status") == 200
            and operation.get("delete_code") == 0
            and operation.get("task_id_present_before") is True
            and operation.get("task_row_present_before") is True
            and operation.get("task_id_cleared_after") is True
            and operation.get("task_row_removed_after") is True
            and operation.get("trace_empty_after") is True
        )
        if not common:
            return False
        if index_type in {"graph", "raptor"} and not (
            operation.get("artifact_check_performed") is True
            and operation.get("artifact_api_http_status") == 200
            and operation.get("artifact_api_code") == 0
            and operation.get("artifact_not_visible_via_api") is True
            and operation.get("artifact_absent_after") is True
        ):
            return False
    return (
        observed.get("precondition_clean") is True
        and bool(fixture_codes)
        and all(code == 0 for code in fixture_codes)
        and observed.get("knowledge_graph_metadata_table_absent") is True
        and observed.get("dataset_cleanup_succeeded") is True
    )


def embedding_schedule_contract_ok(observed: dict[str, Any]) -> bool:
    fixture_codes = observed.get("fixture_api_codes", [])
    expected_count = observed.get("expected_scheduled_count")
    return (
        observed.get("precondition_clean") is True
        and bool(fixture_codes)
        and all(code == 0 for code in fixture_codes)
        and observed.get("run_http_status") == 200
        and observed.get("run_code") == 0
        and isinstance(expected_count, int)
        and expected_count > 0
        and observed.get("actual_scheduled_count") == expected_count
        and observed.get("document_count") == expected_count
        and observed.get("documents_with_tasks") == expected_count
        and observed.get("cleanup_succeeded") is True
    )


def embedding_check_contract_ok(observed: dict[str, Any]) -> bool:
    fixture_codes = observed.get("fixture_api_codes", [])
    expected_chunk_id = str(observed.get("expected_chunk_id") or "")
    result_chunk_ids = [str(item) for item in observed.get("result_chunk_ids", [])]
    try:
        similarity = float(observed.get("average_similarity"))
    except (TypeError, ValueError):
        return False
    return (
        observed.get("precondition_clean") is True
        and bool(fixture_codes)
        and all(code == 0 for code in fixture_codes)
        and observed.get("check_http_status") == 200
        and observed.get("check_code") == 0
        and bool(observed.get("configured_model"))
        and observed.get("reported_model") == observed.get("configured_model")
        and bool(expected_chunk_id)
        and expected_chunk_id in result_chunk_ids
        and observed.get("sampled") == 1
        and observed.get("valid") == 1
        and similarity >= 0.9
        and observed.get("vector_dimensions_positive") is True
        and observed.get("cleanup_succeeded") is True
    )


def ingestion_log_contract_ok(observed: dict[str, Any], *, require_detail: bool) -> bool:
    fixture_codes = observed.get("fixture_api_codes", [])
    common = (
        observed.get("precondition_clean") is True
        and bool(fixture_codes)
        and all(code == 0 for code in fixture_codes)
        and observed.get("list_http_status") == 200
        and observed.get("list_code") == 0
        and int(observed.get("list_total") or 0) >= 1
        and bool(observed.get("log_id"))
        and observed.get("list_log_matches_dataset") is True
        and observed.get("list_log_task_type") == "Mindmap"
        and observed.get("database_log_matches") is True
        and observed.get("index_cleanup_succeeded") is True
        and observed.get("dataset_delete_code") == 0
        and observed.get("dataset_count_after") == 0
    )
    if not require_detail:
        return common
    return (
        common
        and observed.get("detail_http_status") == 200
        and observed.get("detail_code") == 0
        and observed.get("detail_id_matches") is True
        and observed.get("detail_dataset_matches") is True
        and observed.get("detail_dsl_present") is True
    )


def ingestion_summary_contract_ok(observed: dict[str, Any]) -> bool:
    fixture_codes = observed.get("fixture_api_codes", [])
    return (
        observed.get("precondition_clean") is True
        and bool(fixture_codes)
        and all(code == 0 for code in fixture_codes)
        and observed.get("summary_http_status") == 200
        and observed.get("summary_code") == 0
        and observed.get("api_counts") == observed.get("database_counts")
        and observed.get("status_shape_valid") is True
        and observed.get("cleanup_succeeded") is True
    )


def _evidence_module():
    return _load_module("fresh_case_evidence.py", "fresh_04_evidence")


def _finalize(recorder, case_id: str) -> dict[str, Any]:
    evidence = _evidence_module()
    result = recorder.finalize()
    evidence.write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def _private_environment(group: str) -> dict[str, str]:
    payload = json.loads((RUNTIME_DIR / "private_environments.json").read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in payload[group].items()}


def _api_resource_snapshot(group: str) -> dict[str, Any]:
    processes = json.loads((RUNTIME_DIR / "processes.json").read_text(encoding="utf-8"))
    process = processes.get(f"{group}:api")
    pid = int(process.get("pid") or 0) if isinstance(process, dict) else 0
    status_path = Path(f"/proc/{pid}/status")
    stat_path = Path(f"/proc/{pid}/stat")
    fd_path = Path(f"/proc/{pid}/fd")
    if not pid or not status_path.exists() or not stat_path.exists():
        return {
            "pid": pid or None,
            "alive": False,
            "rss_kib": None,
            "thread_count": None,
            "fd_count": None,
            "cpu_ticks": None,
        }
    status_values: dict[str, str] = {}
    for line in status_path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status_values[key] = value.strip()
    stat_values = stat_path.read_text(encoding="utf-8").split()

    def leading_integer(value: str) -> int | None:
        match = re.match(r"(\d+)", value)
        return int(match.group(1)) if match else None

    return {
        "pid": pid,
        "alive": True,
        "rss_kib": leading_integer(status_values.get("VmRSS", "")),
        "thread_count": leading_integer(status_values.get("Threads", "")),
        "fd_count": len(list(fd_path.iterdir())) if fd_path.exists() else None,
        "cpu_ticks": int(stat_values[13]) + int(stat_values[14]),
    }


def _resource_watermark(samples: list[dict[str, Any]]) -> dict[str, Any]:
    live = [sample for sample in samples if sample.get("alive") is True]

    def maximum(field: str) -> int | None:
        values = [int(sample[field]) for sample in live if sample.get(field) is not None]
        return max(values) if values else None

    cpu_values = [int(sample["cpu_ticks"]) for sample in live if sample.get("cpu_ticks") is not None]
    return {
        "sample_count": len(samples),
        "live_sample_count": len(live),
        "pid_consistent": len({sample.get("pid") for sample in live}) == 1 if live else False,
        "max_rss_kib": maximum("rss_kib"),
        "max_thread_count": maximum("thread_count"),
        "max_fd_count": maximum("fd_count"),
        "cpu_ticks_delta": max(cpu_values) - min(cpu_values) if cpu_values else None,
    }


def _measure_operation(
    request_factory: Callable[[str], dict[str, Any]],
    *,
    cold_samples: int = 5,
    warmup_samples: int = 2,
    hot_samples: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for phase, count in (
        ("cold", cold_samples),
        ("warmup", warmup_samples),
        ("hot", hot_samples),
    ):
        result[phase] = [request_factory(f"{phase}_{index:02d}") for index in range(1, count + 1)]
    return result


def gauss_writable_fixture_options(schema: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema) is None:
        raise ValueError("invalid GaussDB metadata schema")
    return f"-c client_encoding=UTF8 -c default_transaction_read_only=off -c search_path={schema}"


def _open_writable_fixture_database(group: str):
    if group == "control":
        return DB._open_database(group)

    import psycopg2

    environment = _private_environment(group)
    schema = environment["GAUSSDB_METADATA_SCHEMA"]
    connection = DB.connect_with_retry(
        lambda: psycopg2.connect(
            host=environment["GAUSSDB_METADATA_HOST"],
            port=int(environment["GAUSSDB_METADATA_PORT"]),
            dbname=environment["GAUSSDB_METADATA_DBNAME"],
            user=environment["GAUSSDB_METADATA_USER"],
            password=environment["GAUSSDB_METADATA_PASSWORD"],
            connect_timeout=5,
            options=gauss_writable_fixture_options(schema),
        )
    )
    DB.set_gauss_search_path(connection, schema)
    return connection, '"user"', schema


def _owner_id(group: str, email: str) -> str:
    connection, user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT id FROM {user_table} WHERE email=%s", (email,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError("dataset owner user is missing")
    return str(row[0])


def _ensure_owner(case_id: str, group: str) -> dict[str, str]:
    environment = _private_environment(group)
    email = environment["DEFAULT_SUPERUSER_EMAIL"]
    password = environment["DEFAULT_SUPERUSER_PASSWORD"]
    login = AUTH._login(
        case_id,
        group,
        "dataset_owner_login",
        email,
        password,
    )
    if login["http_status"] != 200 or login["code"] != 0 or not login["_auth"]:
        raise RuntimeError("dataset owner login failed")
    return {"auth": str(login["_auth"]), "tenant_id": _owner_id(group, email)}


def _record_http(
    case_id: str,
    group: str,
    label: str,
    request_metadata: dict[str, Any],
    response: requests.Response,
) -> str:
    evidence = _evidence_module()
    RAW_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    try:
        response_body: Any = response.json()
    except ValueError:
        response_body = {
            "non_json_response_length": len(response.content),
            "content_type": response.headers.get("Content-Type"),
        }
    payload = evidence.sanitize(
        {
            "request": request_metadata,
            "response": {
                "http_status": response.status_code,
                "body": response_body,
            },
        }
    )
    evidence.write_evidence(path, payload)
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    timeout: float = 60,
    base_url: str | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    response = requests.request(
        method,
        f"{base_url or DB._api_base(group)}{path}",
        headers={"Authorization": f"Bearer {auth}"},
        json=payload if files is None else None,
        params=params,
        files=files,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    file_metadata = None
    if files is not None:
        file_metadata = [
            {
                "field": field,
                "name": item[0],
                "size": len(item[1]),
                "sha256": hashlib.sha256(item[1]).hexdigest(),
                "content_type": item[2],
            }
            for field, item in files
        ]
    raw_sha = _record_http(
        case_id,
        group,
        label,
        {
            "method": method,
            "path": path,
            "params": params,
            "json": payload,
            "files": file_metadata,
            "Authorization": auth,
        },
        response,
    )
    try:
        body = response.json()
    except ValueError:
        body = {}
    return {
        "http_status": response.status_code,
        "code": body.get("code"),
        "message": body.get("message"),
        "data": body.get("data"),
        "total_datasets": body.get("total_datasets"),
        "total": body.get("total"),
        "content_type": response.headers.get("Content-Type"),
        "response_length": len(response.content),
        "elapsed_seconds": elapsed,
        "raw_sha256": raw_sha,
    }


def _dataset_snapshot(group: str, dataset_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,tenant_id,name,description,language,permission,parser_id,parser_config,embd_id,chunk_num,doc_num,token_num,status,create_time,update_time FROM knowledgebase WHERE id=%s",
                (dataset_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    parser_config = row[7]
    if isinstance(parser_config, str):
        parser_config = json.loads(parser_config)
    return {
        "count": 1,
        "id": str(row[0]),
        "tenant_id": str(row[1]),
        "name": row[2],
        "description": row[3],
        "language": row[4],
        "permission": row[5],
        "parser_id": row[6],
        "parser_config": parser_config,
        "embedding_model": "" if row[8] is None else str(row[8]),
        "chunk_num": int(row[9] or 0),
        "doc_num": int(row[10] or 0),
        "token_num": int(row[11] or 0),
        "status": str(row[12]),
        "create_time": int(row[13]) if row[13] is not None else None,
        "update_time": int(row[14]) if row[14] is not None else None,
    }


def _knowledgebase_column_exists(group: str, column_name: str) -> bool:
    connection, _user_table, namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='knowledgebase' AND column_name=%s",
                    (column_name,),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s AND table_name='knowledgebase' AND column_name=%s",
                    (namespace, column_name),
                )
            return int(cursor.fetchone()[0]) > 0
    finally:
        connection.close()


def _dataset_count_by_name(group: str, tenant_id: str, name: str) -> int:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM knowledgebase WHERE tenant_id=%s AND name=%s",
                (tenant_id, name),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _dataset_ids_by_prefix(group: str, tenant_id: str, prefix: str) -> list[str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM knowledgebase WHERE tenant_id=%s AND name LIKE %s",
                (tenant_id, prefix + "%"),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _tenant_dataset_ids(group: str, tenant_id: str) -> list[str]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM knowledgebase WHERE tenant_id=%s ORDER BY id",
                (tenant_id,),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _dataset_tenant_rows(group: str, names: tuple[str, ...]) -> list[dict[str, str]]:
    if not names:
        return []
    placeholders = ",".join(["%s"] * len(names))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,name,tenant_id FROM knowledgebase WHERE name IN ({placeholders}) ORDER BY name,id",
                names,
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [{"id": str(row[0]), "name": str(row[1]), "tenant_id": str(row[2])} for row in rows]


def _dataset_rows_by_prefix(group: str, tenant_id: str, prefix: str) -> list[dict[str, Any]]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,name,permission,parser_id,embd_id,description,create_time FROM knowledgebase WHERE tenant_id=%s AND name LIKE %s ORDER BY create_time,id",
                (tenant_id, prefix + "%"),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "name": row[1],
            "permission": row[2],
            "parser_id": row[3],
            "embedding_model": "" if row[4] is None else str(row[4]),
            "description": row[5],
            "create_time": int(row[6]) if row[6] is not None else None,
        }
        for row in rows
    ]


def _tenant_default_embedding(group: str, tenant_id: str) -> str:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT embd_id FROM tenant WHERE id=%s", (tenant_id,))
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None or not row[0]:
        raise RuntimeError("tenant default embedding is not configured")
    return str(row[0])


def _set_empty_embedding(group: str, dataset_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = _open_writable_fixture_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE knowledgebase SET embd_id=%s WHERE id=%s", ("", dataset_id))
        connection.commit()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT embd_id,LENGTH(embd_id) FROM knowledgebase WHERE id=%s",
                (dataset_id,),
            )
            row = cursor.fetchone()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "physical_is_empty": row[0] == "",
        "physical_is_null": row[0] is None,
        "physical_length": int(row[1]) if row[1] is not None else None,
    }


def _orm_dataset_embedding(group: str, dataset_id: str) -> str | None:
    manager = _load_module("fresh_service_manager.py", "fresh_04_orm_manager")
    script = r"""
import json, sys
from api.db.services.knowledgebase_service import KnowledgebaseService
ok, dataset = KnowledgebaseService.get_by_id(sys.argv[1])
print("__FRESH_RESULT__" + json.dumps({"ok": ok, "value": dataset.embd_id if ok else None}))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, dataset_id],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return json.loads(marker)["value"]


def _description_physical(group: str, dataset_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT description,LENGTH(description) FROM knowledgebase WHERE id=%s",
                (dataset_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    return {
        "is_null": row[0] is None,
        "is_empty": row[0] == "",
        "length": int(row[1]) if row[1] is not None else None,
    }


def _ordered_dataset_ids(group: str, tenant_id: str, prefix: str, *, descending: bool) -> list[str]:
    direction = "DESC" if descending else "ASC"
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM knowledgebase WHERE tenant_id=%s AND name LIKE %s ORDER BY create_time {direction},id {direction}",
                (tenant_id, prefix + "%"),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _cleanup_prefix(case_id: str, group: str, auth: str, tenant_id: str, prefix: str) -> bool:
    ids = _dataset_ids_by_prefix(group, tenant_id, prefix)
    if ids:
        result = _request(
            case_id,
            group,
            "cleanup_existing_datasets",
            auth,
            "DELETE",
            "/datasets",
            payload={"ids": ids},
            timeout=120,
        )
        if result["code"] != 0:
            return False
    return not _dataset_ids_by_prefix(group, tenant_id, prefix)


def _create_dataset(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        "/datasets",
        payload=payload,
    )


def _delete_ids(case_id: str, group: str, auth: str, label: str, ids: list[str]) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        "/datasets",
        payload={"ids": ids},
        timeout=120,
    )


def _update_dataset(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PUT",
        f"/datasets/{dataset_id}",
        payload=payload,
    )


def _get_dataset(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}",
    )


def _update_document(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PATCH",
        f"/datasets/{dataset_id}/documents/{document_id}",
        payload=payload,
    )


def _get_flattened_metadata(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_ids: list[str],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        "/datasets/metadata/flattened",
        params={"dataset_ids": ",".join(dataset_ids)},
    )


def _get_metadata_config(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/metadata/config",
    )


def _update_metadata_config(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PUT",
        f"/datasets/{dataset_id}/metadata/config",
        payload=payload,
    )


def _normalize_flattened_metadata(data: Any) -> dict[str, dict[str, list[str]]]:
    if not isinstance(data, dict):
        return {}
    normalized: dict[str, dict[str, list[str]]] = {}
    for key, values in data.items():
        if not isinstance(values, dict):
            continue
        normalized[str(key)] = {str(value): sorted(str(document_id) for document_id in document_ids) for value, document_ids in values.items() if isinstance(document_ids, list)}
    return normalized


def _normalize_metadata_config(data: Any) -> dict[str, list[dict[str, str]]]:
    source = data if isinstance(data, dict) else {}
    result: dict[str, list[dict[str, str]]] = {}
    for field_name in ("metadata", "built_in_metadata"):
        fields = source.get(field_name)
        result[field_name] = (
            [{"key": str(item["key"]), "type": str(item["type"])} for item in fields if isinstance(item, dict) and item.get("key") and item.get("type")] if isinstance(fields, list) else []
        )
    return result


def _list_datasets(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        "/datasets",
        params=params,
    )


def _upload_empty_document(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    name: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/documents",
        params={"type": "empty"},
        payload={"name": name},
    )


def _upload_local_documents(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    documents: list[tuple[str, bytes, str]],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/documents",
        files=[("file", item) for item in documents],
        timeout=120,
    )


def _delete_documents(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/datasets/{dataset_id}/documents",
        payload={"ids": document_ids},
        timeout=120,
    )


def _list_documents(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/documents",
        params=params,
    )


def _parse_documents_rest(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/documents/parse",
        payload={"document_ids": document_ids},
        timeout=120,
    )


def _poll_document_progress_api(
    case_id: str,
    group: str,
    auth: str,
    dataset_id: str,
    document_id: str,
    *,
    timeout: float = 240,
    interval: float = 0.5,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + timeout
    observations: list[dict[str, Any]] = []
    raw_sha256: list[str] = []
    codes: list[Any] = []
    last_signature: tuple[Any, ...] | None = None
    poll_count = 0
    while time.monotonic() < deadline:
        poll_count += 1
        response = _list_documents(
            case_id,
            group,
            auth,
            f"progress_poll_{poll_count:03d}",
            dataset_id,
            params={"id": document_id},
        )
        raw_sha256.append(response["raw_sha256"])
        codes.append(response["code"])
        rows, total = _document_list_payload(response)
        row = rows[0] if total == 1 and len(rows) == 1 else {}
        current = {
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "code": response["code"],
            "total": total,
            "run": row.get("run"),
            "progress": float(row.get("progress") or 0),
            "progress_msg": str(row.get("progress_msg") or ""),
            "chunk_count": int(row.get("chunk_count") or 0),
            "token_count": int(row.get("token_count") or 0),
        }
        signature = (
            current["code"],
            current["total"],
            current["run"],
            current["progress"],
            current["progress_msg"],
            current["chunk_count"],
            current["token_count"],
        )
        if signature != last_signature:
            observations.append(current)
            last_signature = signature
        if response["code"] == 0 and current["run"] in {"DONE", "FAIL", "CANCEL"}:
            return {
                "terminal": True,
                "timed_out": False,
                "poll_count": poll_count,
                "observations": observations,
                "codes": codes,
                "raw_sha256": raw_sha256,
                "final": current,
            }
        time.sleep(interval)
    return {
        "terminal": False,
        "timed_out": True,
        "poll_count": poll_count,
        "observations": observations,
        "codes": codes,
        "raw_sha256": raw_sha256,
        "final": observations[-1] if observations else {},
    }


def _minimal_pdf_bytes() -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length 0 >>\nstream\n\nendstream",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend((f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode("ascii"))
    return bytes(payload)


def _fixture_document(name: str) -> tuple[str, bytes, str]:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return name, _minimal_pdf_bytes(), "application/pdf"
    mime = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".ppt": "application/vnd.ms-powerpoint",
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".html": "text/html",
    }.get(suffix, "application/octet-stream")
    prefix = b"PK\x03\x04" if suffix in {".docx", ".xlsx"} else b""
    return name, prefix + f"fresh fixture for {name}\n".encode("ascii"), mime


def _parse_documents(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_ids: list[str],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/chunks",
        payload={"document_ids": document_ids},
        timeout=120,
    )


def _document_runtime_rows(group: str, document_ids: list[str]) -> list[dict[str, Any]]:
    if not document_ids:
        return []
    placeholders = ",".join(["%s"] * len(document_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,run,progress,chunk_num,token_num FROM document WHERE id IN ({placeholders}) ORDER BY id",
                tuple(document_ids),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    return [
        {
            "id": str(row[0]),
            "run": None if row[1] is None else str(row[1]),
            "progress": float(row[2] or 0),
            "chunk_num": int(row[3] or 0),
            "token_num": int(row[4] or 0),
        }
        for row in rows
    ]


def _document_state_snapshot(group: str, dataset_id: str, document_id: str) -> dict[str, Any]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT name,parser_id,parser_config,run,progress,progress_msg,chunk_num,token_num,process_begin_at,process_duration FROM document WHERE id=%s AND kb_id=%s",
                (document_id, dataset_id),
            )
            row = cursor.fetchone()
            cursor.execute("SELECT COUNT(*) FROM task WHERE doc_id=%s", (document_id,))
            task_count = int(cursor.fetchone()[0])
            cursor.execute(
                "SELECT doc_num,chunk_num,token_num FROM knowledgebase WHERE id=%s",
                (dataset_id,),
            )
            kb_row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0, "task_count": task_count}
    parser_config = row[2]
    if isinstance(parser_config, str):
        parser_config = json.loads(parser_config)
    return {
        "count": 1,
        "name": str(row[0]),
        "parser_id": str(row[1]),
        "parser_config": parser_config if isinstance(parser_config, dict) else {},
        "run": str(row[3]),
        "progress": float(row[4] or 0),
        "progress_msg": str(row[5] or ""),
        "chunk_num": int(row[6] or 0),
        "token_num": int(row[7] or 0),
        "process_begin_at_present": row[8] is not None,
        "process_duration": float(row[9] or 0),
        "task_count": task_count,
        "dataset_doc_num": int(kb_row[0] or 0) if kb_row else None,
        "dataset_chunk_num": int(kb_row[1] or 0) if kb_row else None,
        "dataset_token_num": int(kb_row[2] or 0) if kb_row else None,
    }


_INDEX_TASK_FIELDS = {
    "graph": ("graphrag_task_id", "graphrag"),
    "raptor": ("raptor_task_id", "raptor"),
    "mindmap": ("mindmap_task_id", "mindmap"),
}


def _task_row_snapshot(group: str, task_id: str) -> dict[str, Any]:
    if not task_id:
        return {"count": 0}
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,doc_id,task_type,progress,progress_msg,begin_at,process_duration FROM task WHERE id=%s",
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
        "document_id": str(row[1]),
        "task_type": str(row[2] or ""),
        "progress": float(row[3] or 0),
        "progress_msg": str(row[4] or ""),
        "begin_at_present": row[5] is not None,
        "process_duration": float(row[6] or 0),
    }


def _index_task_snapshot(group: str, dataset_id: str, index_type: str) -> dict[str, Any]:
    if index_type not in _INDEX_TASK_FIELDS:
        raise ValueError(f"unknown index type: {index_type}")
    field_name, expected_task_type = _INDEX_TASK_FIELDS[index_type]
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {field_name} FROM knowledgebase WHERE id=%s",
                (dataset_id,),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {
            "dataset_count": 0,
            "task_id": "",
            "expected_task_type": expected_task_type,
            "task": {"count": 0},
        }
    task_id = str(row[0] or "")
    return {
        "dataset_count": 1,
        "task_id": task_id,
        "expected_task_type": expected_task_type,
        "task": _task_row_snapshot(group, task_id),
    }


def _document_task_counts(group: str, document_ids: list[str]) -> dict[str, int]:
    if not document_ids:
        return {}
    placeholders = ",".join(["%s"] * len(document_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT doc_id,COUNT(*) FROM task WHERE doc_id IN ({placeholders}) GROUP BY doc_id",
                tuple(document_ids),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()
    counts = {str(document_id): 0 for document_id in document_ids}
    counts.update({str(row[0]): int(row[1]) for row in rows})
    return counts


def _metadata_table_exists(group: str, table_name: str) -> bool:
    connection, _user_table, namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            if group == "control":
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s",
                    (table_name,),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
                    (namespace, table_name),
                )
            return int(cursor.fetchone()[0]) > 0
    finally:
        connection.close()


def _pipeline_log_snapshot(group: str, dataset_id: str, log_id: str) -> dict[str, Any]:
    if not log_id:
        return {"count": 0}
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,kb_id,document_id,task_type,operation_status,progress,dsl FROM pipeline_operation_log WHERE id=%s AND kb_id=%s",
                (log_id, dataset_id),
            )
            row = cursor.fetchone()
    finally:
        connection.close()
    if row is None:
        return {"count": 0}
    dsl = row[6]
    if isinstance(dsl, str):
        try:
            dsl = json.loads(dsl)
        except json.JSONDecodeError:
            pass
    return {
        "count": 1,
        "id": str(row[0]),
        "dataset_id": str(row[1]),
        "document_id": str(row[2]),
        "task_type": str(row[3]),
        "operation_status": str(row[4]),
        "progress": float(row[5] or 0),
        "dsl": dsl,
    }


def _docstore_index_artifact_snapshot(group: str, tenant_id: str, dataset_id: str) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", "fresh_04_index_artifact_manager")
    script = r"""
import json, sys
from common import settings
from common.doc_store.doc_store_base import OrderByExpr
settings.init_settings()
from rag.nlp import search
tenant_id, dataset_id = sys.argv[1], sys.argv[2]
index_name = search.index_name(tenant_id)
index_exists = bool(settings.docStoreConn.index_exist(index_name, dataset_id))
graph_ids, raptor_ids, all_ids = [], [], []
if index_exists:
    result = settings.docStoreConn.search(
        ["id", "knowledge_graph_kwd", "raptor_kwd"], [],
        {"kb_id": dataset_id}, [], OrderByExpr(), 0, 1000,
        index_name, [dataset_id]
    )
    all_ids = [str(item) for item in settings.docStoreConn.get_doc_ids(result)]
    for item_id in all_ids:
        try:
            item = settings.docStoreConn.get(item_id, index_name, [dataset_id]) or {}
        except Exception:
            item = {}
        graph_value = item.get("knowledge_graph_kwd", [])
        raptor_value = item.get("raptor_kwd", [])
        graph_values = graph_value if isinstance(graph_value, list) else [graph_value]
        raptor_values = raptor_value if isinstance(raptor_value, list) else [raptor_value]
        if any(str(value) in {"graph", "subgraph", "entity", "relation", "community_report"} for value in graph_values):
            graph_ids.append(item_id)
        if any(str(value) == "raptor" for value in raptor_values):
            raptor_ids.append(item_id)
print("__FRESH_RESULT__" + json.dumps({
    "index_exists": index_exists,
    "total": len(all_ids),
    "graph_artifact_ids": graph_ids,
    "raptor_artifact_ids": raptor_ids,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, tenant_id, dataset_id],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    payload = json.loads(marker)
    return {
        "index_exists": bool(payload.get("index_exists")),
        "total": int(payload.get("total") or 0),
        "graph_artifact_ids": [str(item) for item in payload.get("graph_artifact_ids", [])],
        "raptor_artifact_ids": [str(item) for item in payload.get("raptor_artifact_ids", [])],
    }


def _wait_for_document_parsing(group: str, document_ids: list[str], timeout: float = 180) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        rows = _document_runtime_rows(group, document_ids)
        if len(rows) == len(document_ids) and all(row["run"] in {"3", "4"} for row in rows):
            return {"terminal": True, "timed_out": False, "documents": rows}
        time.sleep(0.5)
    return {"terminal": False, "timed_out": True, "documents": rows}


def _add_chunk(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/documents/{document_id}/chunks",
        payload=payload,
        timeout=120,
    )


def _list_document_chunks(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/documents/{document_id}/chunks",
        params={"page": 1, "size": 100},
    )


def _get_document_chunk(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_id: str,
    chunk_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
    )


def _update_chunk(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_id: str,
    chunk_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PATCH",
        f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
        payload=payload,
        timeout=120,
    )


def _delete_chunks(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    document_id: str,
    chunk_ids: list[str],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/datasets/{dataset_id}/documents/{document_id}/chunks",
        payload={"chunk_ids": chunk_ids},
        timeout=120,
    )


def _list_tags(case_id: str, group: str, auth: str, label: str, dataset_id: str) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/tags",
    )


def _rename_tag(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    from_tag: str,
    to_tag: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "PUT",
        f"/datasets/{dataset_id}/tags",
        payload={"from_tag": from_tag, "to_tag": to_tag},
    )


def _delete_tags(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    tags: list[str],
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/datasets/{dataset_id}/tags",
        payload={"tags": tags},
    )


def _run_index_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    index_type: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/index",
        params={"type": index_type},
        timeout=120,
    )


def _trace_index_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    index_type: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/index",
        params={"type": index_type},
    )


def _delete_index_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    index_type: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "DELETE",
        f"/datasets/{dataset_id}/index",
        params={"type": index_type, "wipe": "true"},
        timeout=120,
    )


def _run_embedding_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/embedding",
        timeout=120,
    )


def _check_embedding_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    embedding_model: str,
    *,
    check_num: int = 1,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "POST",
        f"/datasets/{dataset_id}/embedding/check",
        payload={"embd_id": embedding_model, "check_num": check_num},
        timeout=120,
    )


def _list_ingestions_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/ingestions",
        params={
            "page": 1,
            "page_size": 20,
            "orderby": "create_time",
            "desc": "true",
            "log_type": "dataset",
        },
    )


def _get_ingestion_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
    log_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/ingestions/{log_id}",
    )


def _get_ingestion_summary_request(
    case_id: str,
    group: str,
    auth: str,
    label: str,
    dataset_id: str,
) -> dict[str, Any]:
    return _request(
        case_id,
        group,
        label,
        auth,
        "GET",
        f"/datasets/{dataset_id}/ingestions/summary",
    )


def _tag_count_map(data: Any) -> dict[str, int]:
    if not isinstance(data, list):
        return {}
    result: dict[str, int] = {}
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            result[str(item[0])] = int(item[1])
    return result


def _tag_aggregation_map(data: Any) -> dict[str, int]:
    if not isinstance(data, list):
        return {}
    return {str(item["value"]): int(item["count"]) for item in data if isinstance(item, dict) and item.get("value") is not None and item.get("count") is not None}


def _create_tag_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    tags_by_chunk: list[list[str]],
) -> dict[str, Any]:
    label_key = re.sub(r"[^A-Za-z0-9]+", "_", prefix).strip("_").lower()
    _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
    created = _create_dataset(
        case_id,
        group,
        owner["auth"],
        f"{label_key}_create_tag_dataset",
        {"name": prefix},
    )
    data = created["data"] if isinstance(created["data"], dict) else {}
    dataset_id = str(data.get("id") or "")
    document = _upload_empty_document(
        case_id,
        group,
        owner["auth"],
        f"{label_key}_create_tag_document",
        dataset_id,
        f"{prefix}-document.txt",
    )
    document_data = document["data"] if isinstance(document["data"], dict) else {}
    document_id = str(document_data.get("id") or "")
    chunks = [
        _add_chunk(
            case_id,
            group,
            owner["auth"],
            f"{label_key}_add_tag_chunk_{index}",
            dataset_id,
            document_id,
            {
                "content": f"fresh tag fixture chunk {index}",
                "tag_kwd": tags,
            },
        )
        for index, tags in enumerate(tags_by_chunk, 1)
    ]
    chunk_ids = [str((item["data"].get("chunk") or {}).get("id") or "") if isinstance(item["data"], dict) else "" for item in chunks]
    listed = _list_document_chunks(
        case_id,
        group,
        owner["auth"],
        f"{label_key}_verify_tag_chunks_persisted",
        dataset_id,
        document_id,
    )
    listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
    listed_items = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
    persisted_ids = [str(item.get("id") or item.get("chunk_id") or "") for item in listed_items if isinstance(item, dict) and (item.get("id") or item.get("chunk_id"))]
    verified = [
        _get_document_chunk(
            case_id,
            group,
            owner["auth"],
            f"{label_key}_get_tag_chunk_{index}",
            dataset_id,
            document_id,
            chunk_id,
        )
        for index, chunk_id in enumerate(chunk_ids, 1)
        if chunk_id
    ]
    verified_chunks = [item["data"] if isinstance(item["data"], dict) else {} for item in verified]
    verified_ids = [str(item.get("id") or "") for item in verified_chunks]
    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_ids": chunk_ids,
        "fixture_persisted": bool(chunk_ids) and set(chunk_ids) == set(persisted_ids) and set(chunk_ids) == set(verified_ids) and all(item["code"] == 0 for item in verified),
        "listed_chunks": listed_items,
        "verified_chunks": verified_chunks,
        "api_codes": [
            created["code"],
            document["code"],
            *[item["code"] for item in chunks],
            listed["code"],
            *[item["code"] for item in verified],
        ],
        "raw_sha256": [
            created["raw_sha256"],
            document["raw_sha256"],
            *[item["raw_sha256"] for item in chunks],
            listed["raw_sha256"],
            *[item["raw_sha256"] for item in verified],
        ],
    }


def _docengine_encoding_snapshot(group: str) -> dict[str, Any]:
    if group != "experiment":
        return {"applicable": False}

    import psycopg2

    try:
        gaussdb = DB._load_yaml(RUNTIME_DIR / group / "conf" / "service_conf.yaml")["gaussdb"]
        config = gaussdb.get("config", gaussdb)
        schema = str(config["schema"])
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema) is None:
            raise ValueError("invalid DocEngine schema")

        base_options = f"-c search_path={schema},public -c default_transaction_read_only=on"
        connection = psycopg2.connect(
            host=config["host"],
            port=int(config["port"]),
            dbname=config["database"],
            user=config["user"],
            password=config["password"],
            connect_timeout=5,
            options=base_options,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("SHOW server_encoding")
                server_encoding = str(cursor.fetchone()[0])
                cursor.execute("SHOW client_encoding")
                client_encoding = str(cursor.fetchone()[0])
        finally:
            connection.close()

        utf8_connection = psycopg2.connect(
            host=config["host"],
            port=int(config["port"]),
            dbname=config["database"],
            user=config["user"],
            password=config["password"],
            connect_timeout=5,
            options=f"{base_options} -c client_encoding=UTF8",
        )
        try:
            with utf8_connection.cursor() as cursor:
                cursor.execute("SELECT %s", ("测试",))
                unicode_roundtrip = cursor.fetchone()[0] == "测试"
                cursor.execute("SHOW client_encoding")
                explicit_client_encoding = str(cursor.fetchone()[0])
        finally:
            utf8_connection.close()
        return {
            "applicable": True,
            "server_encoding": server_encoding,
            "default_client_encoding": client_encoding,
            "explicit_client_encoding": explicit_client_encoding,
            "explicit_utf8_unicode_roundtrip": unicode_roundtrip,
        }
    except Exception as exc:
        return {
            "applicable": True,
            "diagnostic_error_type": type(exc).__name__,
        }


def _managed_api_log_path(group: str) -> Path:
    return RUNTIME_DIR / group / "logs" / "managed_api.log"


def _managed_api_log_size(group: str) -> int:
    path = _managed_api_log_path(group)
    return path.stat().st_size if path.exists() else 0


def _managed_api_log_delta_summary(group: str, start_offset: int) -> dict[str, Any]:
    path = _managed_api_log_path(group)
    if not path.exists():
        return {"log_present": False, "delta_size": 0, "docstore_update_error_count": 0}
    with path.open("rb") as stream:
        stream.seek(max(start_offset, 0))
        delta = stream.read()
    text = delta.decode("utf-8", errors="replace")
    error_markers = (
        "GaussDB update failed",
        "Invalid sparse vector value type",
        "Failed to update chunk",
    )
    return {
        "log_present": True,
        "start_offset": start_offset,
        "end_offset": path.stat().st_size,
        "delta_size": len(delta),
        "delta_sha256": hashlib.sha256(delta).hexdigest(),
        "docstore_update_error_count": sum(text.count(marker) for marker in error_markers),
        "traceback_count": text.count("Traceback (most recent call last)"),
    }


def _storage_object_existence(group: str, dataset_id: str, locations: list[str]) -> dict[str, bool]:
    manager = _load_module("fresh_service_manager.py", "fresh_04_storage_manager")
    script = r"""
import json, sys
from common import settings
settings.init_settings()
dataset_id = sys.argv[1]
locations = json.loads(sys.argv[2])
result = {location: bool(settings.STORAGE_IMPL.obj_exist(dataset_id, location)) for location in locations}
print("__FRESH_RESULT__" + json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        [str(manager.PYTHON), "-c", script, dataset_id, json.dumps(locations)],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    return {str(key): bool(value) for key, value in json.loads(marker).items()}


def _docstore_chunk_existence(
    group: str,
    tenant_id: str,
    dataset_id: str,
    chunk_ids: list[str],
) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", "fresh_04_docstore_manager")
    script = r"""
import json, sys
from common import settings
settings.init_settings()
from rag.nlp import search
tenant_id, dataset_id = sys.argv[1], sys.argv[2]
chunk_ids = json.loads(sys.argv[3])
index_name = search.index_name(tenant_id)
index_exists = bool(settings.docStoreConn.index_exist(index_name, dataset_id))
existing = []
if index_exists:
    for chunk_id in chunk_ids:
        try:
            if settings.docStoreConn.get(chunk_id, index_name, [dataset_id]) is not None:
                existing.append(chunk_id)
        except Exception:
            pass
print("__FRESH_RESULT__" + json.dumps({"index_exists": index_exists, "existing_ids": existing}, sort_keys=True))
"""
    completed = subprocess.run(
        [
            str(manager.PYTHON),
            "-c",
            script,
            tenant_id,
            dataset_id,
            json.dumps(chunk_ids),
        ],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    payload = json.loads(marker)
    return {
        "index_exists": bool(payload.get("index_exists")),
        "existing_ids": [str(item) for item in payload.get("existing_ids", [])],
    }


def _docstore_chunk_batch_snapshot(
    group: str,
    tenant_id: str,
    dataset_id: str,
    chunk_ids: list[str],
) -> dict[str, Any]:
    manager = _load_module("fresh_service_manager.py", "fresh_04_docstore_batch_manager")
    script = r"""
import json, sys
from common import settings
from common.doc_store.doc_store_base import OrderByExpr
settings.init_settings()
from rag.nlp import search
tenant_id, dataset_id = sys.argv[1], sys.argv[2]
requested_ids = json.loads(sys.argv[3])
index_name = search.index_name(tenant_id)
index_exists = bool(settings.docStoreConn.index_exist(index_name, dataset_id))
total = 0
found_ids = []
if index_exists:
    result = settings.docStoreConn.search(
        ["id"], [], {"kb_id": dataset_id}, [], OrderByExpr(),
        0, max(len(requested_ids), 1), index_name, [dataset_id]
    )
    total = int(settings.docStoreConn.get_total(result))
    found_ids = [str(item) for item in settings.docStoreConn.get_doc_ids(result)]
found = set(found_ids)
existing = [chunk_id for chunk_id in requested_ids if chunk_id in found]
print("__FRESH_RESULT__" + json.dumps({
    "index_exists": index_exists,
    "total": total,
    "existing_ids": existing,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [
            str(manager.PYTHON),
            "-c",
            script,
            tenant_id,
            dataset_id,
            json.dumps(chunk_ids),
        ],
        cwd=manager.PROJECT_ROOT,
        env=manager.load_group_environment(group),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    marker = next(line.removeprefix("__FRESH_RESULT__") for line in completed.stdout.splitlines() if line.startswith("__FRESH_RESULT__"))
    payload = json.loads(marker)
    return {
        "index_exists": bool(payload.get("index_exists")),
        "total": int(payload.get("total") or 0),
        "existing_ids": [str(item) for item in payload.get("existing_ids", [])],
    }


def _document_count_for_datasets(group: str, dataset_ids: list[str]) -> int:
    if not dataset_ids:
        return 0
    placeholders = ",".join(["%s"] * len(dataset_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT COUNT(*) FROM document WHERE kb_id IN ({placeholders})",
                tuple(dataset_ids),
            )
            return int(cursor.fetchone()[0])
    finally:
        connection.close()


def _document_ids_for_dataset(group: str, dataset_id: str) -> list[str]:
    if not dataset_id:
        return []
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM document WHERE kb_id=%s ORDER BY name,id",
                (dataset_id,),
            )
            return [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()


def _uploaded_document_graph_snapshot(group: str, dataset_id: str, document_ids: list[str]) -> dict[str, Any]:
    if not document_ids:
        return {"documents": [], "files": [], "file_link_count": 0}
    placeholders = ",".join(["%s"] * len(document_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id,kb_id,name,type,suffix,size,status,run,progress,progress_msg,chunk_num,token_num,location FROM document WHERE id IN ({placeholders}) ORDER BY name,id",
                tuple(document_ids),
            )
            document_rows = cursor.fetchall()
            cursor.execute(
                f"SELECT f2d.document_id,f.id,f.name,f.type,f.size,f.location FROM file2document f2d JOIN file f ON f.id=f2d.file_id WHERE f2d.document_id IN ({placeholders}) ORDER BY f.name,f.id",
                tuple(document_ids),
            )
            file_rows = cursor.fetchall()
    finally:
        connection.close()
    return {
        "documents": [
            {
                "id": str(row[0]),
                "dataset_id": str(row[1]),
                "name": str(row[2]),
                "type": str(row[3]),
                "suffix": str(row[4]),
                "size": int(row[5] or 0),
                "status": str(row[6]),
                "run": str(row[7]),
                "progress": float(row[8] or 0),
                "progress_msg": str(row[9] or ""),
                "chunk_num": int(row[10] or 0),
                "token_num": int(row[11] or 0),
                "location": str(row[12] or ""),
            }
            for row in document_rows
        ],
        "files": [
            {
                "document_id": str(row[0]),
                "id": str(row[1]),
                "name": str(row[2]),
                "type": str(row[3]),
                "size": int(row[4] or 0),
                "location": str(row[5] or ""),
            }
            for row in file_rows
        ],
        "file_link_count": len(file_rows),
    }


def _uploaded_items(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for response in responses:
        data = response.get("data")
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            items.append(data)
    return items


def _document_list_payload(response: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    data = response.get("data")
    if not isinstance(data, dict):
        return [], 0
    docs = data.get("docs")
    return (
        [item for item in docs if isinstance(item, dict)] if isinstance(docs, list) else [],
        int(data.get("total") or 0),
    )


def _document_api_rows_match_database(api_rows: list[dict[str, Any]], database_rows: list[dict[str, Any]]) -> bool:
    run_names = {
        "0": "UNSTART",
        "1": "RUNNING",
        "2": "CANCEL",
        "3": "DONE",
        "4": "FAIL",
        "5": "SCHEDULE",
    }
    database_by_id = {item["id"]: item for item in database_rows}
    if len(api_rows) != len(database_rows):
        return False
    for api_row in api_rows:
        document_id = str(api_row.get("id") or "")
        database_row = database_by_id.get(document_id)
        if database_row is None:
            return False
        if (
            str(api_row.get("dataset_id") or "") != database_row["dataset_id"]
            or str(api_row.get("name") or "") != database_row["name"]
            or str(api_row.get("type") or "") != database_row["type"]
            or str(api_row.get("suffix") or "") != database_row["suffix"]
            or int(api_row.get("size") or 0) != database_row["size"]
            or str(api_row.get("run") or "") != run_names.get(database_row["run"])
            or int(api_row.get("chunk_count") or 0) != database_row["chunk_num"]
            or int(api_row.get("token_count") or 0) != database_row["token_num"]
        ):
            return False
    return True


def _document_upload_observed(
    *,
    dataset_id: str,
    dataset_code: Any,
    upload_responses: list[dict[str, Any]],
    expected_files: list[tuple[str, bytes, str]],
    graph: dict[str, Any],
    cleanup_succeeded: bool,
) -> dict[str, Any]:
    response_items = _uploaded_items(upload_responses)
    documents = sorted(graph.get("documents", []), key=lambda item: item["name"])
    files = sorted(graph.get("files", []), key=lambda item: item["name"])
    expected = sorted(expected_files, key=lambda item: item[0])
    expected_names = [item[0] for item in expected]
    expected_types = ["pdf" if Path(item[0]).suffix.lower() == ".pdf" else "doc" for item in expected]
    expected_suffixes = [Path(item[0]).suffix.lower().lstrip(".") for item in expected]
    expected_sizes = [len(item[1]) for item in expected]
    database_ids = [item["id"] for item in documents]
    response_ids = [str(item.get("id") or "") for item in response_items]
    file_rows_match_documents = len(files) == len(documents) and all(
        file_row["document_id"] == document_row["id"]
        and file_row["name"] == document_row["name"]
        and file_row["type"] == document_row["type"]
        and file_row["size"] == document_row["size"]
        and file_row["location"] == document_row["location"]
        for file_row, document_row in zip(files, documents)
    )
    response_initial = all(str(item.get("run")) == "UNSTART" and int(item.get("chunk_count") or 0) == 0 and int(item.get("token_count") or 0) == 0 for item in response_items)
    database_initial = all(item["status"] == "1" and item["run"] == "0" and item["chunk_num"] == 0 and item["token_num"] == 0 for item in documents)
    documents_by_id = {item["id"]: item for item in documents}
    response_rows_match_database = len(response_items) == len(documents) and all(
        str(item.get("id") or "") in documents_by_id and str(item.get("name") or "") == documents_by_id[str(item.get("id") or "")]["name"] and str(item.get("dataset_id") or "") == dataset_id
        for item in response_items
    )
    return {
        "api_codes": [dataset_code, *[item.get("code") for item in upload_responses]],
        "expected_count": len(expected),
        "response_count": len(response_items),
        "database_count": len(documents),
        "file_count": len(files),
        "file_link_count": int(graph.get("file_link_count", 0)),
        "response_ids": response_ids,
        "database_ids": database_ids,
        "actual_names": [item["name"] for item in documents],
        "expected_names": expected_names,
        "actual_types": [item["type"] for item in documents],
        "expected_types": expected_types,
        "actual_suffixes": [item["suffix"] for item in documents],
        "expected_suffixes": expected_suffixes,
        "actual_sizes": [item["size"] for item in documents],
        "expected_sizes": expected_sizes,
        "initial_status_and_run_match": response_initial and database_initial,
        "database_rows_match_dataset": all(item["dataset_id"] == dataset_id for item in documents),
        "file_rows_match_documents": file_rows_match_documents,
        "response_rows_match_database": response_rows_match_database,
        "cleanup_succeeded": cleanup_succeeded,
    }


def _cleanup_uploaded_dataset(
    case_id: str,
    group: str,
    auth: str,
    dataset_id: str,
    graph: dict[str, Any],
    *,
    label_suffix: str = "",
) -> dict[str, Any]:
    document_ids = [item["id"] for item in graph.get("documents", [])]
    locations = [item["location"] for item in graph.get("documents", []) if item["location"]]
    deleted_documents = (
        _delete_documents(
            case_id,
            group,
            auth,
            f"cleanup_uploaded_documents{label_suffix}",
            dataset_id,
            document_ids,
        )
        if document_ids
        else None
    )
    object_state = _storage_object_existence(group, dataset_id, locations) if locations else {}
    deleted_dataset = (
        _delete_ids(
            case_id,
            group,
            auth,
            f"cleanup_upload_dataset{label_suffix}",
            [dataset_id],
        )
        if dataset_id
        else None
    )
    succeeded = bool(
        deleted_documents
        and deleted_documents["code"] == 0
        and not any(object_state.values())
        and deleted_dataset
        and deleted_dataset["code"] == 0
        and _dataset_snapshot(group, dataset_id).get("count") == 0
        and _document_count_for_datasets(group, [dataset_id]) == 0
    )
    return {
        "succeeded": succeeded,
        "document_delete_code": deleted_documents["code"] if deleted_documents else None,
        "dataset_delete_code": deleted_dataset["code"] if deleted_dataset else None,
        "remaining_objects": sum(1 for value in object_state.values() if value),
        "raw_sha256": [
            deleted_documents["raw_sha256"] if deleted_documents else None,
            deleted_dataset["raw_sha256"] if deleted_dataset else None,
        ],
    }


def _create_retrieval_fixtures(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    contents: list[str],
) -> dict[str, Any]:
    if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
        raise RuntimeError("failed to clear retrieval fixture")
    datasets: list[dict[str, Any]] = []
    uploads: list[dict[str, Any]] = []
    parses: list[dict[str, Any]] = []
    parsing: list[dict[str, Any]] = []
    chunk_lists: list[dict[str, Any]] = []
    graphs: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    dataset_ids: list[str] = []
    document_ids: list[str] = []
    chunk_ids_by_dataset: list[list[str]] = []
    for index, content in enumerate(contents, 1):
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            f"create_retrieval_dataset_{index}",
            {
                "name": f"{prefix}-{index}",
                "parser_config": {
                    "chunk_token_num": 128,
                    "delimiter": "\n",
                    "layout_recognize": "Plain Text",
                },
            },
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            f"upload_retrieval_document_{index}",
            dataset_id,
            [
                (
                    f"{prefix}-document-{index}.txt",
                    content.encode("utf-8"),
                    "text/plain",
                )
            ],
        )
        items = _uploaded_items([uploaded])
        document_id = str(items[0].get("id") or "") if items else ""
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            f"parse_retrieval_document_{index}",
            dataset_id,
            [document_id],
        )
        parsed_state = _wait_for_document_parsing(group, [document_id], timeout=240)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            f"list_retrieval_chunks_{index}",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        chunk_ids = [str(item.get("id") or item.get("chunk_id") or "") for item in chunks if isinstance(item, dict) and (item.get("id") or item.get("chunk_id"))]
        datasets.append(created)
        uploads.append(uploaded)
        parses.append(parsed)
        parsing.append(parsed_state)
        chunk_lists.append(listed)
        dataset_ids.append(dataset_id)
        document_ids.append(document_id)
        chunk_ids_by_dataset.append(chunk_ids)
        graphs.append(_uploaded_document_graph_snapshot(group, dataset_id, [document_id]))
        states.append(_document_state_snapshot(group, dataset_id, document_id))
    fixtures_ready = all(
        created["code"] == uploaded["code"] == parsed["code"] == listed["code"] == 0
        and parsed_state.get("terminal") is True
        and len(parsed_state.get("documents", [])) == 1
        and parsed_state["documents"][0].get("run") == "3"
        and len(chunk_ids) >= 1
        and state.get("chunk_num", 0) >= 1
        for created, uploaded, parsed, listed, parsed_state, chunk_ids, state in zip(
            datasets,
            uploads,
            parses,
            chunk_lists,
            parsing,
            chunk_ids_by_dataset,
            states,
        )
    )
    return {
        "datasets": datasets,
        "uploads": uploads,
        "parses": parses,
        "parsing": parsing,
        "chunk_lists": chunk_lists,
        "dataset_ids": dataset_ids,
        "document_ids": document_ids,
        "chunk_ids_by_dataset": chunk_ids_by_dataset,
        "graphs": graphs,
        "states": states,
        "fixtures_ready": fixtures_ready,
        "api_codes": [
            *[item["code"] for item in datasets],
            *[item["code"] for item in uploads],
            *[item["code"] for item in parses],
            *[item["code"] for item in chunk_lists],
        ],
        "raw_sha256": [
            *[item["raw_sha256"] for item in datasets],
            *[item["raw_sha256"] for item in uploads],
            *[item["raw_sha256"] for item in parses],
            *[item["raw_sha256"] for item in chunk_lists],
        ],
    }


def _cleanup_retrieval_fixtures(
    case_id: str,
    group: str,
    auth: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    results = [
        _cleanup_uploaded_dataset(
            case_id,
            group,
            auth,
            dataset_id,
            graph,
            label_suffix=f"_{index}",
        )
        for index, (dataset_id, graph) in enumerate(zip(fixture["dataset_ids"], fixture["graphs"]), 1)
    ]
    return {
        "succeeded": bool(results) and all(item["succeeded"] for item in results),
        "results": results,
    }


def _normalize_retrieval_chunks(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    chunks = data.get("chunks") if isinstance(data, dict) else None
    if not isinstance(chunks, list):
        return []
    result: list[dict[str, Any]] = []
    for item in chunks:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": str(item.get("id") or item.get("chunk_id") or ""),
                "content": str(item.get("content") or item.get("content_with_weight") or ""),
                "document_id": str(item.get("document_id") or item.get("doc_id") or ""),
                "dataset_id": str(item.get("dataset_id") or item.get("kb_id") or ""),
                "similarity": (float(item["similarity"]) if item.get("similarity") is not None else None),
                "positions": item.get("positions", item.get("position_int")),
            }
        )
    return result


def _cascade_resource_snapshot(group: str, dataset_id: str, document_ids: list[str]) -> dict[str, Any]:
    if not document_ids:
        return {
            "document_ids": [],
            "task_ids": [],
            "file_ids": [],
            "file_link_ids": [],
            "locations": [],
        }
    placeholders = ",".join(["%s"] * len(document_ids))
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT id FROM document WHERE kb_id=%s AND id IN ({placeholders}) ORDER BY id",
                (dataset_id, *document_ids),
            )
            saved_document_ids = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                f"SELECT id FROM task WHERE doc_id IN ({placeholders}) ORDER BY id",
                tuple(document_ids),
            )
            task_ids = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute(
                f"SELECT f2d.id,f2d.file_id,f.location FROM file2document f2d JOIN file f ON f.id=f2d.file_id WHERE f2d.document_id IN ({placeholders}) ORDER BY f2d.document_id,f2d.id",
                tuple(document_ids),
            )
            file_rows = cursor.fetchall()
    finally:
        connection.close()
    return {
        "document_ids": saved_document_ids,
        "task_ids": task_ids,
        "file_link_ids": [str(row[0]) for row in file_rows],
        "file_ids": [str(row[1]) for row in file_rows],
        "locations": [str(row[2]) for row in file_rows if row[2]],
    }


def _saved_cascade_resource_counts(
    group: str,
    dataset_id: str,
    document_ids: list[str],
    task_ids: list[str],
    file_ids: list[str],
    file_link_ids: list[str],
) -> dict[str, int]:
    connection, _user_table, _namespace = DB._open_database(group)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM knowledgebase WHERE id=%s", (dataset_id,))
            dataset_count = int(cursor.fetchone()[0])

            def count_ids(table: str, ids: list[str]) -> int:
                if not ids:
                    return 0
                placeholders = ",".join(["%s"] * len(ids))
                cursor.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE id IN ({placeholders})",
                    tuple(ids),
                )
                return int(cursor.fetchone()[0])

            return {
                "dataset_count": dataset_count,
                "document_count": count_ids("document", document_ids),
                "task_count": count_ids("task", task_ids),
                "file_count": count_ids("file", file_ids),
                "file_link_count": count_ids("file2document", file_link_ids),
            }
    finally:
        connection.close()


def _create_named_datasets(
    case_id: str,
    group: str,
    auth: str,
    prefix: str,
    count: int,
) -> list[dict[str, Any]]:
    return [
        _create_dataset(
            case_id,
            group,
            auth,
            f"create_dataset_{index:03d}",
            {"name": f"{prefix}-{index:03d}"},
        )
        for index in range(count)
    ]


def _run_case(
    case_id: str,
    operation: Callable[[str, dict[str, str]], dict[str, Any]],
) -> dict[str, Any]:
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        owner = _ensure_owner(case_id, group)
        outcome = operation(group, owner)
        recorder.add_group(
            group,
            outcome["status"],
            outcome["steps"],
            oracle=outcome.get("oracle", {}),
            findings=outcome.get("findings", []),
        )
    return _finalize(recorder, case_id)


def _validation_operation(
    case_id: str,
    prefix: str,
    payload: dict[str, Any],
    *,
    expected_code: int | None,
) -> Callable[[str, dict[str, str]], dict[str, Any]]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear validation fixture")
        before_ids = set(_tenant_dataset_ids(group, owner["tenant_id"]))
        response = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "submit_invalid_dataset_payload",
            payload,
        )
        after_ids = set(_tenant_dataset_ids(group, owner["tenant_id"]))
        new_ids = sorted(after_ids - before_ids)
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "database_count": len(new_ids),
        }
        cleanup = True
        if new_ids:
            cleanup_response = _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_unexpected_validation_residue",
                new_ids,
            )
            cleanup = cleanup_response["code"] == 0
        passed = validation_rejection_contract_ok(observed, expected_code=expected_code) and cleanup
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "submit_invalid_dataset_payload",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message": response["message"],
                    "database_delta": len(new_ids),
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "verify_no_residue_and_cleanup_if_needed",
                    "unexpected_id_count": len(new_ids),
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "http_status": 200,
                "code": expected_code if expected_code is not None else "nonzero",
                "database_delta": 0,
            },
        }

    return execute


def run_dd001() -> dict[str, Any]:
    case_id = "TC-DD-001"
    prefix = "fresh-dd-001"
    evidence = _evidence_module()
    recorder = evidence.CaseRecorder(case_id, CASE_TITLES[case_id])
    for group in GROUP_ORDER:
        owner = _ensure_owner(case_id, group)
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear dataset fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_minimal_dataset",
            {"name": prefix},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        snapshot = _dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
        observed = {
            "http_status": created["http_status"],
            "code": created["code"],
            "id_present": bool(dataset_id),
            "name_matches": data.get("name") == prefix and snapshot.get("name") == prefix,
            "database_count": snapshot.get("count", 0),
            "tenant_matches": snapshot.get("tenant_id") == owner["tenant_id"],
            "embedding_inherited": bool(snapshot.get("embedding_model")) and data.get("embedding_model") == snapshot.get("embedding_model"),
            "description_is_null": data.get("description") is None and snapshot.get("description") is None,
            "defaults_match": data.get("language") == "English"
            and data.get("permission") == "me"
            and data.get("chunk_method") == "naive"
            and int(data.get("chunk_count", -1)) == 0
            and int(data.get("document_count", -1)) == 0
            and snapshot.get("status") == "1"
            and snapshot.get("chunk_num") == 0
            and snapshot.get("doc_num") == 0,
        }
        cleanup = (
            bool(dataset_id)
            and _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_minimal_dataset",
                [dataset_id],
            )["code"]
            == 0
        )
        cleanup = cleanup and _dataset_snapshot(group, dataset_id).get("count") == 0
        recorder.add_group(
            group,
            "PASS" if dataset_create_contract_ok(observed) and cleanup else "FAIL",
            [
                {
                    "name": "create_minimal_dataset_through_api",
                    "http_status": created["http_status"],
                    "code": created["code"],
                    "dataset_id_fingerprint": DB._fingerprint(dataset_id),
                    "response_name_matches": data.get("name") == prefix,
                    "response_defaults": {
                        "language": data.get("language"),
                        "permission": data.get("permission"),
                        "chunk_method": data.get("chunk_method"),
                        "chunk_count": data.get("chunk_count"),
                        "document_count": data.get("document_count"),
                        "description_is_null": data.get("description") is None,
                    },
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "read_only_database_verification",
                    "database_count": snapshot.get("count"),
                    "tenant_matches": observed["tenant_matches"],
                    "name_matches": snapshot.get("name") == prefix,
                    "embedding_inherited": observed["embedding_inherited"],
                    "description_is_null": snapshot.get("description") is None,
                    "defaults_match": observed["defaults_match"],
                    "create_time_present": snapshot.get("create_time") is not None,
                    "update_time_present": snapshot.get("update_time") is not None,
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "remaining_count": _dataset_snapshot(group, dataset_id).get("count"),
                },
            ],
            oracle={
                "response": [200, 0],
                "database_count": 1,
                "default_language": "English",
                "default_permission": "me",
                "default_chunk_method": "naive",
                "description": None,
                "embedding_inherited": True,
            },
        )
    return _finalize(recorder, case_id)


def run_dd002() -> dict[str, Any]:
    case_id = "TC-DD-002"
    prefix = "fresh-dd-002"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        embedding = _tenant_default_embedding(group, owner["tenant_id"])
        response = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_full_dataset",
            {
                "name": prefix,
                "description": "fresh full dataset",
                "embedding_model": embedding,
                "permission": "team",
                "chunk_method": "qa",
                "parser_config": {
                    "chunk_token_num": 512,
                    "layout_recognize": "DeepDOC",
                },
            },
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        snapshot = _dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
        parser_config = snapshot.get("parser_config") or {}
        passed = (
            response["http_status"] == 200
            and response["code"] == 0
            and snapshot.get("count") == 1
            and snapshot.get("permission") == "team"
            and snapshot.get("parser_id") == "qa"
            and snapshot.get("embedding_model") == embedding
            and snapshot.get("description") == "fresh full dataset"
            and parser_config.get("chunk_token_num") == 512
            and parser_config.get("layout_recognize") == "DeepDOC"
        )
        cleanup_response = _delete_ids(case_id, group, owner["auth"], "cleanup_full_dataset", [dataset_id]) if dataset_id else {"code": None}
        cleanup = cleanup_response["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_full_dataset_through_api",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "response_fields_match": data.get("permission") == "team" and data.get("chunk_method") == "qa" and data.get("embedding_model") == embedding,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_full_field_verification",
                    "database_count": snapshot.get("count"),
                    "permission": snapshot.get("permission"),
                    "chunk_method": snapshot.get("parser_id"),
                    "embedding_matches": snapshot.get("embedding_model") == embedding,
                    "description_matches": snapshot.get("description") == "fresh full dataset",
                    "parser_config_matches": parser_config.get("chunk_token_num") == 512 and parser_config.get("layout_recognize") == "DeepDOC",
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                },
            ],
            "oracle": {
                "response": [200, 0],
                "permission": "team",
                "chunk_method": "qa",
                "parser_config_chunk_token_num": 512,
            },
        }

    return _run_case(case_id, execute)


def run_dd003() -> dict[str, Any]:
    case_id = "TC-DD-003"
    prefix = "fresh-dd-003-duplicate"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        first = _create_dataset(case_id, group, owner["auth"], "create_original_name", {"name": prefix})
        second = _create_dataset(case_id, group, owner["auth"], "create_duplicate_name", {"name": prefix})
        first_data = first["data"] if isinstance(first["data"], dict) else {}
        second_data = second["data"] if isinstance(second["data"], dict) else {}
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        names = [str(row["name"]) for row in rows]
        passed = first["code"] == 0 and second["code"] == 0 and first_data.get("name") == prefix and second_data.get("name") == f"{prefix}(1)" and names == [prefix, f"{prefix}(1)"]
        ids = [row["id"] for row in rows]
        cleanup = bool(ids) and _delete_ids(case_id, group, owner["auth"], "cleanup_duplicate_datasets", ids)["code"] == 0
        cleanup = cleanup and not _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_original_and_duplicate_name",
                    "response_codes": [first["code"], second["code"]],
                    "first_name_matches": first_data.get("name") == prefix,
                    "second_name_has_expected_suffix": second_data.get("name") == f"{prefix}(1)",
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {
                    "name": "read_only_database_name_verification",
                    "row_count": len(rows),
                    "names_match_expected": names == [prefix, f"{prefix}(1)"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"codes": [0, 0], "second_suffix": "(1)", "row_count": 2},
        }

    return _run_case(case_id, execute)


def run_dd004() -> dict[str, Any]:
    return _run_case(
        "TC-DD-004",
        _validation_operation("TC-DD-004", "fresh-dd-004", {"name": ""}, expected_code=101),
    )


def run_dd005() -> dict[str, Any]:
    return _run_case(
        "TC-DD-005",
        _validation_operation(
            "TC-DD-005",
            "fresh-dd-005",
            {"name": "fresh-dd-005", "chunk_method": "invalid_parser"},
            expected_code=101,
        ),
    )


def run_dd006() -> dict[str, Any]:
    case_id = "TC-DD-006"
    prefix = "fresh-dd-006"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        responses = []
        for permission in ("me", "team"):
            responses.append(
                _create_dataset(
                    case_id,
                    group,
                    owner["auth"],
                    f"create_{permission}_dataset",
                    {"name": f"{prefix}-{permission}", "permission": permission},
                )
            )
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        actual = {row["name"]: row["permission"] for row in rows}
        expected = {f"{prefix}-me": "me", f"{prefix}-team": "team"}
        passed = [item["code"] for item in responses] == [0, 0] and actual == expected
        cleanup = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_permission_datasets",
                [row["id"] for row in rows],
            )["code"]
            == 0
        )
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_me_and_team_datasets",
                    "response_codes": [item["code"] for item in responses],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_permission_verification",
                    "row_count": len(rows),
                    "permissions_match": actual == expected,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"permissions": ["me", "team"]},
        }

    return _run_case(case_id, execute)


def run_dd007() -> dict[str, Any]:
    case_id = "TC-DD-007"
    prefix = "fresh-dd-007"
    methods = ("naive", "book", "qa", "table")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        responses = [
            _create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_{method}_dataset",
                {"name": f"{prefix}-{method}", "chunk_method": method},
            )
            for method in methods
        ]
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        actual = {row["name"]: row["parser_id"] for row in rows}
        expected = {f"{prefix}-{method}": method for method in methods}
        snapshots = [_dataset_snapshot(group, row["id"]) for row in rows]
        configs_present = all(isinstance(snapshot.get("parser_config"), dict) and bool(snapshot.get("parser_config")) for snapshot in snapshots)
        passed = all(item["code"] == 0 for item in responses) and actual == expected and configs_present
        cleanup = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_chunk_method_datasets",
                [row["id"] for row in rows],
            )["code"]
            == 0
        )
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_four_chunk_method_datasets",
                    "response_codes": [item["code"] for item in responses],
                    "raw_sha256": [item["raw_sha256"] for item in responses],
                },
                {
                    "name": "read_only_parser_verification",
                    "row_count": len(rows),
                    "parser_ids_match": actual == expected,
                    "default_configs_present": configs_present,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"chunk_methods": list(methods)},
        }

    return _run_case(case_id, execute)


def run_dd008() -> dict[str, Any]:
    case_id = "TC-DD-008"
    prefix = "fresh-dd-008"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        embedding = _tenant_default_embedding(group, owner["tenant_id"])
        response = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_explicit_embedding_dataset",
            {"name": prefix, "embedding_model": embedding},
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        snapshot = _dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
        passed = response["code"] == 0 and data.get("embedding_model") == embedding and snapshot.get("embedding_model") == embedding
        cleanup = bool(dataset_id) and _delete_ids(case_id, group, owner["auth"], "cleanup_embedding_dataset", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_with_tenant_authorized_embedding",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "response_embedding_matches": data.get("embedding_model") == embedding,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_embedding_verification",
                    "database_count": snapshot.get("count"),
                    "database_embedding_matches": snapshot.get("embedding_model") == embedding,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "embedding_matches_request": True},
        }

    return _run_case(case_id, execute)


def run_dd009() -> dict[str, Any]:
    case_id = "TC-DD-009"
    prefix = "fresh-dd-009"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        before_count = len(_tenant_dataset_ids(group, owner["tenant_id"]))
        created = _create_named_datasets(case_id, group, owner["auth"], prefix, 31)
        response = _list_datasets(case_id, group, owner["auth"], "list_default_page")
        data = response["data"] if isinstance(response["data"], list) else []
        returned_ids = [str(item.get("id")) for item in data if isinstance(item, dict)]
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        passed = (
            all(item["code"] == 0 for item in created)
            and response["code"] == 0
            and len(data) == 30
            and response["total_datasets"] == before_count + 31
            and len(set(returned_ids)) == 30
            and sum(str(item.get("name", "")).startswith(prefix) for item in data) == 30
            and len(rows) == 31
        )
        cleanup = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_default_pagination_datasets",
                [row["id"] for row in rows],
            )["code"]
            == 0
        )
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_31_lightweight_datasets",
                    "success_count": sum(item["code"] == 0 for item in created),
                    "database_count": len(rows),
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "list_without_pagination_parameters",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "returned_count": len(data),
                    "unique_id_count": len(set(returned_ids)),
                    "fixture_returned_count": sum(str(item.get("name", "")).startswith(prefix) for item in data),
                    "total_matches_database": response["total_datasets"] == before_count + 31,
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"default_page_size": 30, "total_delta": 31},
        }

    return _run_case(case_id, execute)


def run_dd010() -> dict[str, Any]:
    case_id = "TC-DD-010"
    prefix = "fresh-dd-010"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_named_datasets(case_id, group, owner["auth"], prefix, 12)
        common = {
            "page_size": 5,
            "orderby": "create_time",
            "desc": "true",
            "ext": json.dumps({"keywords": prefix}),
        }
        first = _list_datasets(case_id, group, owner["auth"], "list_page_1", {**common, "page": 1})
        second = _list_datasets(case_id, group, owner["auth"], "list_page_2", {**common, "page": 2})
        first_data = first["data"] if isinstance(first["data"], list) else []
        second_data = second["data"] if isinstance(second["data"], list) else []
        first_ids = {str(item.get("id")) for item in first_data}
        second_ids = {str(item.get("id")) for item in second_data}
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        passed = (
            all(item["code"] == 0 for item in created)
            and first["code"] == second["code"] == 0
            and len(first_data) == len(second_data) == 5
            and not first_ids.intersection(second_ids)
            and first["total_datasets"] == second["total_datasets"] == 12
        )
        cleanup = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_custom_pagination_datasets",
                [row["id"] for row in rows],
            )["code"]
            == 0
        )
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_12_filterable_datasets",
                    "success_count": sum(item["code"] == 0 for item in created),
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "compare_custom_pages",
                    "page_counts": [len(first_data), len(second_data)],
                    "page_ids_disjoint": not first_ids.intersection(second_ids),
                    "stable_total": first["total_datasets"] == second["total_datasets"] == 12,
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"page_counts": [5, 5], "total": 12, "overlap": 0},
        }

    return _run_case(case_id, execute)


def run_dd011() -> dict[str, Any]:
    case_id = "TC-DD-011"
    prefix = "fresh-dd-011"
    exact_name = f"{prefix}-target"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        exact = _create_dataset(case_id, group, owner["auth"], "create_exact_name", {"name": exact_name})
        containing = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_containing_name",
            {"name": f"{exact_name}-extra"},
        )
        response = _list_datasets(
            case_id,
            group,
            owner["auth"],
            "query_exact_name",
            {"name": exact_name},
        )
        data = response["data"] if isinstance(response["data"], list) else []
        names = [item.get("name") for item in data]
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        passed = exact["code"] == containing["code"] == response["code"] == 0 and names == [exact_name] and response["total_datasets"] == 1
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_exact_query_datasets", [row["id"] for row in rows])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_exact_and_containing_names",
                    "response_codes": [exact["code"], containing["code"]],
                    "raw_sha256": [exact["raw_sha256"], containing["raw_sha256"]],
                },
                {
                    "name": "query_name_as_exact_filter",
                    "code": response["code"],
                    "returned_count": len(data),
                    "only_exact_name_returned": names == [exact_name],
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"exact_match_count": 1, "containing_name_excluded": True},
        }

    return _run_case(case_id, execute)


def run_dd012() -> dict[str, Any]:
    case_id = "TC-DD-012"
    prefix = "fresh-dd-012"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_permission_fixture",
            {"name": prefix, "permission": "team"},
        )
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        response = _list_datasets(
            case_id,
            group,
            owner["auth"],
            "submit_unsupported_permission_query",
            {"permission": "team"},
        )
        snapshot = _dataset_snapshot(group, dataset_id)
        passed = created["code"] == 0 and response["http_status"] == 200 and response["code"] == 101 and snapshot.get("permission") == "team"
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_permission_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "submit_unsupported_permission_query",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message": response["message"],
                    "fixture_unchanged": snapshot.get("permission") == "team",
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"http_status": 200, "code": 101},
        }

    return _run_case(case_id, execute)


def run_dd013() -> dict[str, Any]:
    case_id = "TC-DD-013"
    prefix = "fresh-dd-013"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_detail_fixture",
            {"name": prefix, "description": "detail fixture", "permission": "team"},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        response = _get_dataset(case_id, group, owner["auth"], "get_dataset_detail", dataset_id)
        data = response["data"] if isinstance(response["data"], dict) else {}
        snapshot = _dataset_snapshot(group, dataset_id)
        passed = (
            created["code"] == response["code"] == 0
            and data.get("id") == dataset_id
            and data.get("name") == snapshot.get("name") == prefix
            and data.get("description") == snapshot.get("description") == "detail fixture"
            and data.get("permission") == snapshot.get("permission") == "team"
            and data.get("document_count") == snapshot.get("doc_num") == 0
            and data.get("chunk_count") == snapshot.get("chunk_num") == 0
        )
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_detail_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "get_single_dataset_detail",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "id_matches": data.get("id") == dataset_id,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "compare_api_with_read_only_database",
                    "name_matches": data.get("name") == snapshot.get("name") == prefix,
                    "description_matches": data.get("description") == snapshot.get("description") == "detail fixture",
                    "permission_matches": data.get("permission") == snapshot.get("permission") == "team",
                    "statistics_match": data.get("document_count") == snapshot.get("doc_num") == 0 and data.get("chunk_count") == snapshot.get("chunk_num") == 0,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "database_fields_match": True},
        }

    return _run_case(case_id, execute)


def run_dd014() -> dict[str, Any]:
    case_id = "TC-DD-014"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        invalid_id = "invalid-uuid-12345"
        before = set(_tenant_dataset_ids(group, owner["tenant_id"]))
        response = _get_dataset(case_id, group, owner["auth"], "get_nonexistent_dataset", invalid_id)
        after = set(_tenant_dataset_ids(group, owner["tenant_id"]))
        passed = response["http_status"] == 200 and response["code"] == 102 and before == after and "lacks permission" in str(response["message"])
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "get_nonexistent_dataset",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "permission_error_present": "lacks permission" in str(response["message"]),
                    "database_unchanged": before == after,
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            "oracle": {"http_status": 200, "code": 102, "side_effects": 0},
        }

    return _run_case(case_id, execute)


def run_dd015() -> dict[str, Any]:
    case_id = "TC-DD-015"
    prefix = "fresh-dd-015"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        datasets = [
            _create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_search_dataset_{index}",
                {"name": f"{prefix}-{index}"},
            )
            for index in (1, 2)
        ]
        dataset_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in datasets]
        documents = [
            _upload_empty_document(
                case_id,
                group,
                owner["auth"],
                f"create_search_document_{index}",
                dataset_id,
                f"{prefix}-document-{index}.txt",
            )
            for index, dataset_id in enumerate(dataset_ids, 1)
        ]
        document_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in documents]
        chunks = [
            _add_chunk(
                case_id,
                group,
                owner["auth"],
                f"add_search_chunk_{index}",
                dataset_id,
                document_id,
                {
                    "content": f"测试 检索 marker {index}，这是第 {index} 个独立数据集的内容。",
                    "important_keywords": ["测试", "检索", f"marker-{index}"],
                    "questions": ["测试内容在哪里？"],
                },
            )
            for index, (dataset_id, document_id) in enumerate(zip(dataset_ids, document_ids), 1)
        ]
        expected_chunk_ids = [str((item["data"].get("chunk") or {}).get("id") or "") if isinstance(item["data"], dict) else "" for item in chunks]
        listed_chunks = [
            _list_document_chunks(
                case_id,
                group,
                owner["auth"],
                f"verify_search_chunk_{index}_persisted",
                dataset_id,
                document_id,
            )
            for index, (dataset_id, document_id) in enumerate(zip(dataset_ids, document_ids), 1)
        ]
        persisted_chunk_ids = []
        for response in listed_chunks:
            data = response["data"] if isinstance(response["data"], dict) else {}
            listed = data.get("chunks") if isinstance(data.get("chunks"), list) else []
            persisted_chunk_ids.extend(str(item.get("id") or item.get("chunk_id") or "") for item in listed if isinstance(item, dict) and (item.get("id") or item.get("chunk_id")))
        chunks_persisted = set(expected_chunk_ids).issubset(set(persisted_chunk_ids))
        searched = _request(
            case_id,
            group,
            "search_across_selected_datasets",
            owner["auth"],
            "POST",
            "/datasets/search",
            payload={
                "question": "测试",
                "dataset_ids": dataset_ids,
                "doc_ids": document_ids,
                "page": 1,
                "size": 10,
                "top_k": 10,
                "similarity_threshold": 0.0,
                "vector_similarity_weight": 0.3,
            },
            timeout=120,
        )
        search_data = searched["data"] if isinstance(searched["data"], dict) else {}
        result_chunks = search_data.get("chunks") if isinstance(search_data.get("chunks"), list) else []
        result_chunk_ids = [str(item.get("id") or item.get("chunk_id") or "") for item in result_chunks if isinstance(item, dict) and (item.get("id") or item.get("chunk_id"))]
        similarities = [float(item["similarity"]) for item in result_chunks if isinstance(item, dict) and item.get("similarity") is not None]
        similarity_nonincreasing = len(similarities) == len(result_chunks) and all(left >= right for left, right in zip(similarities, similarities[1:]))
        chunk_shape_present = isinstance(search_data.get("chunks"), list) and isinstance(search_data.get("total"), int) and "labels" in search_data
        encoding_snapshot = _docengine_encoding_snapshot(group)

        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_search_datasets",
            dataset_ids,
        )
        cleanup_succeeded = deleted["code"] == 0 and not any(_dataset_snapshot(group, item).get("count") for item in dataset_ids) and _document_count_for_datasets(group, dataset_ids) == 0
        observed = {
            "api_codes": [
                *[item["code"] for item in datasets],
                *[item["code"] for item in documents],
                *[item["code"] for item in chunks],
                *[item["code"] for item in listed_chunks],
                searched["code"],
            ],
            "chunks_persisted": chunks_persisted,
            "chunk_shape_present": chunk_shape_present,
            "expected_chunk_ids": expected_chunk_ids,
            "result_chunk_ids": result_chunk_ids,
            "total": search_data.get("total", 0),
            "similarity_nonincreasing": similarity_nonincreasing,
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = dataset_search_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_datasets_documents_and_embedded_chunks",
                    "dataset_create_codes": [item["code"] for item in datasets],
                    "document_create_codes": [item["code"] for item in documents],
                    "chunk_create_codes": [item["code"] for item in chunks],
                    "chunk_list_codes": [item["code"] for item in listed_chunks],
                    "dataset_count": len([item for item in dataset_ids if item]),
                    "document_count": len([item for item in document_ids if item]),
                    "chunk_count": len([item for item in expected_chunk_ids if item]),
                    "chunks_persisted_after_success_responses": chunks_persisted,
                    "persisted_chunk_count": len(persisted_chunk_ids),
                    "raw_sha256": [
                        *[item["raw_sha256"] for item in datasets],
                        *[item["raw_sha256"] for item in documents],
                        *[item["raw_sha256"] for item in chunks],
                        *[item["raw_sha256"] for item in listed_chunks],
                    ],
                },
                {
                    "name": "search_across_nonempty_dataset_and_document_filters",
                    "http_status": searched["http_status"],
                    "code": searched["code"],
                    "message": searched["message"],
                    "chunk_shape_present": chunk_shape_present,
                    "result_count": len(result_chunks),
                    "total": search_data.get("total"),
                    "both_expected_chunks_present": set(expected_chunk_ids).issubset(set(result_chunk_ids)),
                    "similarity_nonincreasing": similarity_nonincreasing,
                    "term_similarity_field_count": sum(isinstance(item, dict) and "term_similarity" in item for item in result_chunks),
                    "vector_similarity_field_count": sum(isinstance(item, dict) and "vector_similarity" in item for item in result_chunks),
                    "raw_sha256": searched["raw_sha256"],
                },
                {
                    "name": "read_only_docengine_encoding_diagnostic",
                    "observed": encoding_snapshot,
                },
                {
                    "name": "cleanup_datasets_through_api_and_verify_cascade",
                    "delete_code": deleted["code"],
                    "remaining_document_count": _document_count_for_datasets(group, dataset_ids),
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "response_shape": ["chunks", "total", "labels"],
                "expected_selected_chunks": 2,
                "hybrid_vector_similarity_weight": 0.3,
                "relevance_order": "nonincreasing",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-001" if group == "experiment" else "DD-SEARCH-001",
                    "summary": (
                        "GaussDB DocEngine inherits SQL_ASCII: Unicode chunk inserts return API code 0 without persistence and Unicode retrieval raises UnicodeEncodeError"
                        if group == "experiment"
                        else "dataset search did not satisfy the chunk retrieval contract"
                    ),
                    "code_location": (
                        "common/doc_store/gaussdb_conn_pool.py:_create_pool; rag/utils/gaussdb_conn.py:insert,_fetch_all_with_description; api/apps/restful_apis/chunk_api.py:add_chunk"
                        if group == "experiment"
                        else "api/apps/services/dataset_api_service.py:search_datasets"
                    ),
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd016() -> dict[str, Any]:
    case_id = "TC-DD-016"
    prefix = "fresh-dd-016"
    updated_name = f"{prefix}-updated"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(case_id, group, owner["auth"], "create_update_fixture", {"name": prefix})
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        before = _dataset_snapshot(group, dataset_id)
        time.sleep(1.05)
        response = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "update_basic_fields",
            dataset_id,
            {
                "name": updated_name,
                "description": "fresh updated description",
                "language": "Chinese",
            },
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        after = _dataset_snapshot(group, dataset_id)
        passed = (
            created["code"] == response["code"] == 0
            and data.get("name") == after.get("name") == updated_name
            and data.get("description") == after.get("description") == "fresh updated description"
            and data.get("language") == after.get("language") == "Chinese"
            and int(after.get("update_time") or 0) > int(before.get("update_time") or 0)
            and after.get("permission") == before.get("permission")
            and after.get("parser_id") == before.get("parser_id")
        )
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_updated_dataset", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "update_basic_dataset_fields",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "response_fields_match": data.get("name") == updated_name and data.get("description") == "fresh updated description" and data.get("language") == "Chinese",
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_update_verification",
                    "database_fields_match": after.get("name") == updated_name and after.get("description") == "fresh updated description" and after.get("language") == "Chinese",
                    "update_time_advanced": int(after.get("update_time") or 0) > int(before.get("update_time") or 0),
                    "untouched_fields_stable": after.get("permission") == before.get("permission") and after.get("parser_id") == before.get("parser_id"),
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "update_time_advanced": True},
        }

    return _run_case(case_id, execute)


def run_dd017() -> dict[str, Any]:
    case_id = "TC-DD-017"
    prefix = "fresh-dd-017"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(case_id, group, owner["auth"], "create_parser_fixture", {"name": prefix})
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        before = _dataset_snapshot(group, dataset_id)
        response = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "deep_merge_parser_config",
            dataset_id,
            {
                "parser_config": {
                    "chunk_token_num": 1024,
                    "layout_recognize": "Plain Text",
                    "task_page_size": 12,
                }
            },
        )
        after = _dataset_snapshot(group, dataset_id)
        before_config = before.get("parser_config") or {}
        after_config = after.get("parser_config") or {}
        untouched_keys = set(before_config) - {
            "chunk_token_num",
            "layout_recognize",
            "task_page_size",
        }
        old_keys_preserved = all(after_config.get(key) == before_config.get(key) for key in untouched_keys)
        passed = (
            created["code"] == response["code"] == 0
            and after_config.get("chunk_token_num") == 1024
            and after_config.get("layout_recognize") == "Plain Text"
            and after_config.get("task_page_size") == 12
            and old_keys_preserved
        )
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_parser_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "update_parser_config",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_deep_merge_verification",
                    "submitted_values_match": after_config.get("chunk_token_num") == 1024 and after_config.get("layout_recognize") == "Plain Text" and after_config.get("task_page_size") == 12,
                    "untouched_key_count": len(untouched_keys),
                    "untouched_keys_preserved": old_keys_preserved,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"deep_merge": True, "submitted_keys_override": True},
        }

    return _run_case(case_id, execute)


def run_dd018() -> dict[str, Any]:
    case_id = "TC-DD-018"
    prefix = "fresh-dd-018"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_private_fixture",
            {"name": prefix, "permission": "me"},
        )
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        response = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "change_permission_to_team",
            dataset_id,
            {"permission": "team"},
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        snapshot = _dataset_snapshot(group, dataset_id)
        passed = created["code"] == response["code"] == 0 and data.get("permission") == snapshot.get("permission") == "team"
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_permission_update_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "change_permission_me_to_team",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "response_permission": data.get("permission"),
                    "database_permission": snapshot.get("permission"),
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"permission": "team"},
        }

    return _run_case(case_id, execute)


def run_dd019() -> dict[str, Any]:
    case_id = "TC-DD-019"
    prefix = "fresh-dd-019"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        target = _tenant_default_embedding(group, owner["tenant_id"])
        created = _create_dataset(case_id, group, owner["auth"], "create_embedding_update_fixture", {"name": prefix})
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        response = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "update_to_authorized_embedding",
            dataset_id,
            {"embedding_model": target},
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        snapshot = _dataset_snapshot(group, dataset_id)
        passed = created["code"] == response["code"] == 0 and data.get("embedding_model") == target and snapshot.get("embedding_model") == target and snapshot.get("doc_num") == 0
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_embedding_update_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "update_to_tenant_authorized_embedding",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "response_matches_target": data.get("embedding_model") == target,
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_embedding_update_verification",
                    "database_matches_target": snapshot.get("embedding_model") == target,
                    "document_count": snapshot.get("doc_num"),
                    "auto_parse_not_scheduled": snapshot.get("doc_num") == 0,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "embedding_matches_request": True},
        }

    return _run_case(case_id, execute)


def run_dd020() -> dict[str, Any]:
    case_id = "TC-DD-020"
    prefix = "fresh-dd-020"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(case_id, group, owner["auth"], "create_invalid_update_fixture", {"name": prefix})
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        before = _dataset_snapshot(group, dataset_id)
        response = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "submit_unknown_update_field",
            dataset_id,
            {"invalid_field": "some_value"},
        )
        after = _dataset_snapshot(group, dataset_id)
        stable_fields = (
            before.get("name"),
            before.get("permission"),
            before.get("parser_id"),
            before.get("embedding_model"),
            before.get("update_time"),
        ) == (
            after.get("name"),
            after.get("permission"),
            after.get("parser_id"),
            after.get("embedding_model"),
            after.get("update_time"),
        )
        passed = created["code"] == 0 and response["http_status"] == 200 and response["code"] == 101 and stable_fields
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_invalid_update_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "submit_unknown_update_field",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "message": response["message"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_no_mutation_verification",
                    "stable_fields_unchanged": stable_fields,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 101], "database_unchanged": True},
        }

    return _run_case(case_id, execute)


def run_dd021() -> dict[str, Any]:
    case_id = "TC-DD-021"
    prefix = "fresh-dd-021"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(case_id, group, owner["auth"], "create_empty_delete_fixture", {"name": prefix})
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        response = _delete_ids(case_id, group, owner["auth"], "delete_empty_dataset", [dataset_id])
        data = response["data"] if isinstance(response["data"], dict) else {}
        remaining = _dataset_snapshot(group, dataset_id).get("count")
        passed = created["code"] == response["code"] == 0 and data.get("success_count") == 1 and remaining == 0
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "delete_empty_dataset",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "success_count": data.get("success_count"),
                    "database_remaining_count": remaining,
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            "oracle": {"response": [200, 0], "success_count": 1, "remaining": 0},
        }

    return _run_case(case_id, execute)


def run_dd022() -> dict[str, Any]:
    case_id = "TC-DD-022"
    prefix = "fresh-dd-022"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_cascade_dataset",
            {
                "name": prefix,
                "parser_config": {
                    "chunk_token_num": 8,
                    "delimiter": "`###`",
                    "layout_recognize": "Plain Text",
                },
            },
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        upload_files = [
            (
                f"{prefix}-document-{index}.txt",
                (f"cascade {index} segment one ### cascade {index} segment two ### cascade {index} segment three ### cascade {index} segment four").encode("ascii"),
                "text/plain",
            )
            for index in range(1, 4)
        ]
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_three_cascade_documents",
            dataset_id,
            upload_files,
        )
        uploaded_data = uploaded["data"] if isinstance(uploaded["data"], list) else []
        document_ids = [str(item.get("id") or "") for item in uploaded_data if isinstance(item, dict) and item.get("id")]
        parsed = _parse_documents(
            case_id,
            group,
            owner["auth"],
            "parse_three_cascade_documents",
            dataset_id,
            document_ids,
        )
        parsing = _wait_for_document_parsing(group, document_ids, timeout=240)
        runtime_rows = parsing["documents"]
        all_documents_have_multiple_chunks = len(runtime_rows) == 3 and all(row["run"] == "3" and row["chunk_num"] >= 2 for row in runtime_rows)

        listed_chunks = [
            _list_document_chunks(
                case_id,
                group,
                owner["auth"],
                f"list_cascade_chunks_{index}",
                dataset_id,
                document_id,
            )
            for index, document_id in enumerate(document_ids, 1)
        ]
        chunk_ids: list[str] = []
        listed_chunk_counts: list[int] = []
        for response in listed_chunks:
            data = response["data"] if isinstance(response["data"], dict) else {}
            items = data.get("chunks") if isinstance(data.get("chunks"), list) else []
            listed_chunk_counts.append(len(items))
            chunk_ids.extend(str(item.get("id") or item.get("chunk_id") or "") for item in items if isinstance(item, dict) and (item.get("id") or item.get("chunk_id")))

        resources = _cascade_resource_snapshot(group, dataset_id, document_ids)
        objects_before = _storage_object_existence(group, dataset_id, resources["locations"])
        chunks_before = _docstore_chunk_existence(
            group,
            owner["tenant_id"],
            dataset_id,
            chunk_ids,
        )
        all_objects_exist_before = len(resources["locations"]) == 3 and all(objects_before.get(location) for location in resources["locations"])
        all_chunks_exist_before = bool(chunk_ids) and set(chunk_ids) == set(chunks_before["existing_ids"])

        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "delete_dataset_with_documents",
            [dataset_id],
        )
        deleted_data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        after = _saved_cascade_resource_counts(
            group,
            dataset_id,
            resources["document_ids"],
            resources["task_ids"],
            resources["file_ids"],
            resources["file_link_ids"],
        )
        objects_after = _storage_object_existence(group, dataset_id, resources["locations"])
        chunks_after = _docstore_chunk_existence(
            group,
            owner["tenant_id"],
            dataset_id,
            chunk_ids,
        )
        object_count_after = sum(objects_after.values())
        chunk_count_after = len(chunks_after["existing_ids"])
        observed = {
            "api_codes": [
                created["code"],
                uploaded["code"],
                parsed["code"],
                *[item["code"] for item in listed_chunks],
                deleted["code"],
            ],
            "document_count_before": len(resources["document_ids"]),
            "all_documents_have_multiple_chunks": all_documents_have_multiple_chunks,
            "task_count_before": len(resources["task_ids"]),
            "file_count_before": len(resources["file_ids"]),
            "file_link_count_before": len(resources["file_link_ids"]),
            "all_objects_exist_before": all_objects_exist_before,
            "all_chunks_exist_before": all_chunks_exist_before,
            "delete_success_count": deleted_data.get("success_count"),
            "dataset_count_after": after["dataset_count"],
            "document_count_after": after["document_count"],
            "task_count_after": after["task_count"],
            "file_count_after": after["file_count"],
            "file_link_count_after": after["file_link_count"],
            "object_count_after": object_count_after,
            "chunk_count_after": chunk_count_after,
        }
        passed = dataset_cascade_contract_ok(observed)
        final_cleanup = True
        if after["dataset_count"]:
            final_cleanup = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        findings = []
        if object_count_after:
            findings.append(
                {
                    "id": "DD-CASCADE-OBJECT-001",
                    "summary": "dataset deletion leaves original uploaded objects in storage",
                    "code_location": "api/apps/services/dataset_api_service.py:delete_datasets; api/db/services/document_service.py:remove_document",
                }
            )
        if not passed and not findings:
            findings.append(
                {
                    "id": "DD-CASCADE-001",
                    "summary": "dataset deletion did not remove every saved dependent resource",
                    "code_location": "api/apps/services/dataset_api_service.py:delete_datasets",
                }
            )
        return {
            "status": "PASS" if passed and final_cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_upload_and_parse_three_documents",
                    "dataset_create_code": created["code"],
                    "upload_code": uploaded["code"],
                    "uploaded_document_count": len(document_ids),
                    "parse_code": parsed["code"],
                    "parse_terminal": parsing["terminal"],
                    "parse_timed_out": parsing["timed_out"],
                    "document_run_states": [row["run"] for row in runtime_rows],
                    "document_chunk_counts": [row["chunk_num"] for row in runtime_rows],
                    "listed_chunk_counts": listed_chunk_counts,
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        parsed["raw_sha256"],
                        *[item["raw_sha256"] for item in listed_chunks],
                    ],
                },
                {
                    "name": "save_exact_dependent_ids_and_verify_preconditions",
                    "document_count": len(resources["document_ids"]),
                    "task_count": len(resources["task_ids"]),
                    "file_count": len(resources["file_ids"]),
                    "file_link_count": len(resources["file_link_ids"]),
                    "object_count": len(resources["locations"]),
                    "all_objects_exist": all_objects_exist_before,
                    "chunk_count": len(chunk_ids),
                    "all_chunks_exist": all_chunks_exist_before,
                    "docstore_index_exists": chunks_before["index_exists"],
                },
                {
                    "name": "delete_dataset_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "success_count": deleted_data.get("success_count"),
                    "message": deleted["message"],
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "verify_all_saved_resources_after_deletion",
                    "database_counts": after,
                    "remaining_object_count": object_count_after,
                    "remaining_chunk_count": chunk_count_after,
                    "docstore_index_exists": chunks_after["index_exists"],
                    "final_api_cleanup_succeeded": final_cleanup,
                },
            ],
            "oracle": {
                "documents_before": 3,
                "minimum_chunks_per_document": 2,
                "all_saved_database_resources_after": 0,
                "all_saved_storage_objects_after": 0,
                "all_saved_docstore_chunks_after": 0,
            },
            "findings": findings,
        }

    return _run_case(case_id, execute)


def run_dd023() -> dict[str, Any]:
    case_id = "TC-DD-023"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        invalid_id = "550e8400e29b41d4a716446655440000"
        before = set(_tenant_dataset_ids(group, owner["tenant_id"]))
        response = _delete_ids(case_id, group, owner["auth"], "delete_nonexistent_dataset", [invalid_id])
        after = set(_tenant_dataset_ids(group, owner["tenant_id"]))
        passed = response["http_status"] == 200 and response["code"] == 102 and before == after and "lacks permission" in str(response["message"])
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "delete_nonexistent_dataset",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "permission_error_present": "lacks permission" in str(response["message"]),
                    "database_unchanged": before == after,
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            "oracle": {"response": [200, 102], "side_effects": 0},
        }

    return _run_case(case_id, execute)


def run_dd024() -> dict[str, Any]:
    case_id = "TC-DD-024"
    prefix = "fresh-dd-024"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_named_datasets(case_id, group, owner["auth"], prefix, 3)
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        ids = [row["id"] for row in rows]
        response = _delete_ids(case_id, group, owner["auth"], "batch_delete_three_datasets", ids)
        data = response["data"] if isinstance(response["data"], dict) else {}
        remaining = sum(_dataset_snapshot(group, dataset_id).get("count", 0) for dataset_id in ids)
        passed = all(item["code"] == 0 for item in created) and len(ids) == 3 and response["code"] == 0 and data.get("success_count") == 3 and remaining == 0
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_batch_delete_fixtures",
                    "success_count": sum(item["code"] == 0 for item in created),
                    "database_count": len(rows),
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "batch_delete_three_datasets",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "success_count": data.get("success_count"),
                    "database_remaining_count": remaining,
                    "raw_sha256": response["raw_sha256"],
                },
            ],
            "oracle": {"response": [200, 0], "success_count": 3, "remaining": 0},
        }

    return _run_case(case_id, execute)


def run_dd025() -> dict[str, Any]:
    case_id = "TC-DD-025"
    prefix = "fresh-dd-025"
    expected_counts = {"标签1": 5, "标签2": 3}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_tag_fixture(
            case_id,
            group,
            owner,
            prefix,
            [["标签1", "标签2"]] * 3 + [["标签1"]] * 2,
        )
        listed = _list_tags(
            case_id,
            group,
            owner["auth"],
            "list_dataset_tags",
            fixture["dataset_id"],
        )
        actual_counts = _tag_count_map(listed["data"])
        visible_tag_counts: dict[str, int] = {}
        for chunk in fixture["verified_chunks"]:
            for tag in chunk.get("tag_kwd", []) if isinstance(chunk, dict) else []:
                visible_tag_counts[str(tag)] = visible_tag_counts.get(str(tag), 0) + 1
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_tag_list_dataset",
            [fixture["dataset_id"]],
        )
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, fixture["dataset_id"]).get("count") == 0
        observed = {
            "api_codes": [*fixture["api_codes"], listed["code"]],
            "fixture_persisted": fixture["fixture_persisted"],
            "expected_tag_counts": expected_counts,
            "actual_tag_counts": actual_counts,
            "mutation_visible_in_chunks": visible_tag_counts == expected_counts,
            "cleanup_succeeded": cleanup,
        }
        passed = tag_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_tagged_chunks_through_api",
                    "api_codes": fixture["api_codes"],
                    "expected_chunk_count": 5,
                    "fixture_persisted": fixture["fixture_persisted"],
                    "visible_tag_counts": visible_tag_counts,
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "list_dataset_tags_and_verify_chunk_counts",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "actual_tag_counts": actual_counts,
                    "counts_match": actual_counts == expected_counts,
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "delete_code": deleted["code"],
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {"tag_counts": expected_counts, "count_unit": "chunks"},
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-001" if group == "experiment" else "DD-TAG-LIST-001",
                    "summary": (
                        "Unicode tag chunks are reported successful but are not persisted in GaussDB DocEngine" if group == "experiment" else "dataset tag list did not match persisted chunk tags"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:insert" if group == "experiment" else "api/apps/services/dataset_api_service.py:list_tags",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd026() -> dict[str, Any]:
    case_id = "TC-DD-026"
    prefix = "fresh-dd-026"
    expected_counts = {"保留标签": 1, "新标签1": 1}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_tag_fixture(
            case_id,
            group,
            owner,
            prefix,
            [["标签1", "保留标签"]],
        )
        renamed = _rename_tag(
            case_id,
            group,
            owner["auth"],
            "rename_dataset_tag",
            fixture["dataset_id"],
            "标签1",
            "新标签1",
        )
        listed = _list_tags(
            case_id,
            group,
            owner["auth"],
            "list_tags_after_rename",
            fixture["dataset_id"],
        )
        chunk = _get_document_chunk(
            case_id,
            group,
            owner["auth"],
            "get_chunk_after_tag_rename",
            fixture["dataset_id"],
            fixture["document_id"],
            fixture["chunk_ids"][0],
        )
        actual_counts = _tag_count_map(listed["data"])
        chunk_data = chunk["data"] if isinstance(chunk["data"], dict) else {}
        chunk_tags = sorted(str(tag) for tag in chunk_data.get("tag_kwd", []))
        expected_chunk_tags = sorted(expected_counts)
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_tag_rename_dataset",
            [fixture["dataset_id"]],
        )
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, fixture["dataset_id"]).get("count") == 0
        observed = {
            "api_codes": [
                *fixture["api_codes"],
                renamed["code"],
                listed["code"],
                chunk["code"],
            ],
            "fixture_persisted": fixture["fixture_persisted"],
            "expected_tag_counts": expected_counts,
            "actual_tag_counts": actual_counts,
            "mutation_visible_in_chunks": chunk_tags == expected_chunk_tags,
            "cleanup_succeeded": cleanup,
        }
        passed = tag_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_original_tag_fixture",
                    "fixture_persisted": fixture["fixture_persisted"],
                    "api_codes": fixture["api_codes"],
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "rename_and_verify_tag_in_list_and_chunk",
                    "rename_code": renamed["code"],
                    "rename_message": renamed["message"],
                    "list_code": listed["code"],
                    "chunk_get_code": chunk["code"],
                    "actual_tag_counts": actual_counts,
                    "chunk_tags_match": chunk_tags == expected_chunk_tags,
                    "old_tag_absent": "标签1" not in actual_counts and "标签1" not in chunk_tags,
                    "raw_sha256": [
                        renamed["raw_sha256"],
                        listed["raw_sha256"],
                        chunk["raw_sha256"],
                    ],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "old_tag_absent": True,
                "new_tag_count": 1,
                "unrelated_tag_preserved": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-001" if group == "experiment" else "DD-TAG-RENAME-001",
                    "summary": (
                        "GaussDB DocEngine cannot persist or mutate Unicode tag values under SQL_ASCII" if group == "experiment" else "tag rename was not visible in both list and chunk reads"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:insert,update" if group == "experiment" else "api/apps/services/dataset_api_service.py:rename_tag",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd027() -> dict[str, Any]:
    case_id = "TC-DD-027"
    prefix = "fresh-dd-027"
    expected_counts = {"保留标签": 1}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_tag_fixture(
            case_id,
            group,
            owner,
            prefix,
            [["标签1", "标签2", "保留标签"]],
        )
        removed = _delete_tags(
            case_id,
            group,
            owner["auth"],
            "delete_selected_dataset_tags",
            fixture["dataset_id"],
            ["标签1", "标签2"],
        )
        listed = _list_tags(
            case_id,
            group,
            owner["auth"],
            "list_tags_after_delete",
            fixture["dataset_id"],
        )
        chunk = _get_document_chunk(
            case_id,
            group,
            owner["auth"],
            "get_chunk_after_tag_delete",
            fixture["dataset_id"],
            fixture["document_id"],
            fixture["chunk_ids"][0],
        )
        actual_counts = _tag_count_map(listed["data"])
        chunk_data = chunk["data"] if isinstance(chunk["data"], dict) else {}
        chunk_tags = sorted(str(tag) for tag in chunk_data.get("tag_kwd", []))
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_tag_delete_dataset",
            [fixture["dataset_id"]],
        )
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, fixture["dataset_id"]).get("count") == 0
        observed = {
            "api_codes": [
                *fixture["api_codes"],
                removed["code"],
                listed["code"],
                chunk["code"],
            ],
            "fixture_persisted": fixture["fixture_persisted"],
            "expected_tag_counts": expected_counts,
            "actual_tag_counts": actual_counts,
            "mutation_visible_in_chunks": chunk_tags == ["保留标签"],
            "cleanup_succeeded": cleanup,
        }
        passed = tag_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_tag_fixture",
                    "fixture_persisted": fixture["fixture_persisted"],
                    "api_codes": fixture["api_codes"],
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "delete_two_tags_and_verify_list_and_chunk",
                    "delete_code": removed["code"],
                    "delete_message": removed["message"],
                    "list_code": listed["code"],
                    "chunk_get_code": chunk["code"],
                    "actual_tag_counts": actual_counts,
                    "chunk_tags": chunk_tags,
                    "selected_tags_absent": not {"标签1", "标签2"}.intersection(set(actual_counts) | set(chunk_tags)),
                    "unrelated_tag_preserved": chunk_tags == ["保留标签"],
                    "raw_sha256": [
                        removed["raw_sha256"],
                        listed["raw_sha256"],
                        chunk["raw_sha256"],
                    ],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "deleted_tags": ["标签1", "标签2"],
                "preserved_tag": "保留标签",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-001" if group == "experiment" else "DD-TAG-DELETE-001",
                    "summary": (
                        "GaussDB DocEngine cannot persist or remove Unicode tag values under SQL_ASCII" if group == "experiment" else "tag deletion was not visible in both list and chunk reads"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:insert,update" if group == "experiment" else "api/apps/services/dataset_api_service.py:delete_tags",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd028() -> dict[str, Any]:
    case_id = "TC-DD-028"
    expected_counts = {"专用标签": 1, "通用标签": 3}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture_a = _create_tag_fixture(
            case_id,
            group,
            owner,
            "fresh-dd-028-a",
            [["通用标签", "专用标签"], ["通用标签"]],
        )
        fixture_b = _create_tag_fixture(
            case_id,
            group,
            owner,
            "fresh-dd-028-b",
            [["通用标签"]],
        )
        dataset_ids = [fixture_a["dataset_id"], fixture_b["dataset_id"]]
        aggregated = _request(
            case_id,
            group,
            "aggregate_selected_dataset_tags",
            owner["auth"],
            "GET",
            "/datasets/tags/aggregation",
            params={"dataset_ids": ",".join(dataset_ids)},
        )
        missing_filter = _request(
            case_id,
            group,
            "aggregate_without_required_dataset_ids",
            owner["auth"],
            "GET",
            "/datasets/tags/aggregation",
        )
        actual_counts = _tag_aggregation_map(aggregated["data"])
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_tag_aggregation_datasets",
            dataset_ids,
        )
        cleanup = deleted["code"] == 0 and all(_dataset_snapshot(group, dataset_id).get("count") == 0 for dataset_id in dataset_ids)
        observed = {
            "api_codes": [
                *fixture_a["api_codes"],
                *fixture_b["api_codes"],
                aggregated["code"],
            ],
            "fixture_persisted": fixture_a["fixture_persisted"] and fixture_b["fixture_persisted"],
            "expected_tag_counts": expected_counts,
            "actual_tag_counts": actual_counts,
            "mutation_visible_in_chunks": missing_filter["code"] == 102,
            "cleanup_succeeded": cleanup,
        }
        passed = tag_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_tagged_dataset_fixtures",
                    "fixture_a_persisted": fixture_a["fixture_persisted"],
                    "fixture_b_persisted": fixture_b["fixture_persisted"],
                    "api_codes": [
                        *fixture_a["api_codes"],
                        *fixture_b["api_codes"],
                    ],
                    "raw_sha256": [
                        *fixture_a["raw_sha256"],
                        *fixture_b["raw_sha256"],
                    ],
                },
                {
                    "name": "aggregate_only_explicit_dataset_ids",
                    "http_status": aggregated["http_status"],
                    "code": aggregated["code"],
                    "actual_tag_counts": actual_counts,
                    "counts_match": actual_counts == expected_counts,
                    "raw_sha256": aggregated["raw_sha256"],
                },
                {
                    "name": "reject_missing_dataset_ids_filter",
                    "http_status": missing_filter["http_status"],
                    "code": missing_filter["code"],
                    "message": missing_filter["message"],
                    "raw_sha256": missing_filter["raw_sha256"],
                },
                {
                    "name": "cleanup_datasets_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "tag_counts": expected_counts,
                "count_unit": "chunks",
                "missing_dataset_ids_code": 102,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-001" if group == "experiment" else "DD-TAG-AGGREGATION-001",
                    "summary": (
                        "GaussDB DocEngine cannot persist Unicode tags for aggregation under SQL_ASCII" if group == "experiment" else "selected dataset tag aggregation did not match chunk counts"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:insert" if group == "experiment" else "api/apps/services/dataset_api_service.py:aggregate_tags",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd029() -> dict[str, Any]:
    case_id = "TC-DD-029"
    prefix = "fresh-dd-029"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear flattened metadata fixture")
        datasets = [
            _create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_metadata_dataset_{index}",
                {"name": f"{prefix}-{index}"},
            )
            for index in (1, 2)
        ]
        dataset_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in datasets]
        documents = [
            _upload_empty_document(
                case_id,
                group,
                owner["auth"],
                f"create_metadata_document_{index}",
                dataset_id,
                f"{prefix}-document-{index}.txt",
            )
            for index, dataset_id in enumerate(dataset_ids, 1)
        ]
        document_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in documents]
        updates = [
            _update_document(
                case_id,
                group,
                owner["auth"],
                f"set_document_metadata_{author}",
                dataset_id,
                document_id,
                {"meta_fields": {"author": author}},
            )
            for dataset_id, document_id, author in zip(dataset_ids, document_ids, ("alice", "bob"))
        ]
        flattened = _get_flattened_metadata(
            case_id,
            group,
            owner["auth"],
            "get_only_first_dataset_flattened_metadata",
            dataset_ids[:1],
        )
        actual_metadata = _normalize_flattened_metadata(flattened["data"])
        expected_metadata = {"author": {"alice": [document_ids[0]]}} if len(document_ids) == 2 and document_ids[0] else {}
        unselected_document_absent = bool(len(document_ids) == 2 and document_ids[1]) and all(document_ids[1] not in ids for values in actual_metadata.values() for ids in values.values())
        deleted = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_metadata_datasets",
                dataset_ids,
            )
            if len(dataset_ids) == 2 and all(dataset_ids)
            else None
        )
        cleanup = bool(deleted and deleted["code"] == 0 and all(_dataset_snapshot(group, dataset_id).get("count") == 0 for dataset_id in dataset_ids))
        observed = {
            "api_codes": [
                *[item["code"] for item in datasets],
                *[item["code"] for item in documents],
                *[item["code"] for item in updates],
                flattened["code"],
            ],
            "expected_metadata": expected_metadata,
            "actual_metadata": actual_metadata,
            "unselected_document_absent": unselected_document_absent,
            "cleanup_succeeded": cleanup,
        }
        passed = flattened_metadata_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_datasets_and_documents",
                    "dataset_ids_present": len(dataset_ids) == 2 and all(dataset_ids),
                    "document_ids_present": len(document_ids) == 2 and all(document_ids),
                    "api_codes": [
                        *[item["code"] for item in datasets],
                        *[item["code"] for item in documents],
                    ],
                    "raw_sha256": [
                        *[item["raw_sha256"] for item in datasets],
                        *[item["raw_sha256"] for item in documents],
                    ],
                },
                {
                    "name": "set_distinct_document_metadata_through_api",
                    "api_codes": [item["code"] for item in updates],
                    "raw_sha256": [item["raw_sha256"] for item in updates],
                },
                {
                    "name": "query_only_selected_dataset_flattened_metadata",
                    "http_status": flattened["http_status"],
                    "code": flattened["code"],
                    "expected_metadata": expected_metadata,
                    "actual_metadata": actual_metadata,
                    "unselected_document_absent": unselected_document_absent,
                    "raw_sha256": flattened["raw_sha256"],
                },
                {
                    "name": "cleanup_datasets_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"] if deleted else None,
                },
            ],
            "oracle": {
                "shape": "{metadata_key: {metadata_value: [document_id]}}",
                "dataset_filter": "only explicitly selected dataset",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-METADATA-FLATTENED-001",
                    "summary": f"{group} flattened metadata did not exactly match the selected dataset",
                    "code_location": "api/db/services/doc_metadata_service.py:get_flatted_meta_by_kbs",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd030() -> dict[str, Any]:
    case_id = "TC-DD-030"
    prefix = "fresh-dd-030"
    expected_config = {"metadata": [], "built_in_metadata": []}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear metadata config fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_default_metadata_config_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        fetched = _get_metadata_config(
            case_id,
            group,
            owner["auth"],
            "get_default_metadata_config",
            dataset_id,
        )
        snapshot = _dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
        api_config = _normalize_metadata_config(fetched["data"])
        database_config = _normalize_metadata_config(snapshot.get("parser_config"))
        deleted = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_default_metadata_config_dataset",
                [dataset_id],
            )
            if dataset_id
            else None
        )
        cleanup = bool(deleted and deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0)
        observed = {
            "api_codes": [created["code"], fetched["code"]],
            "expected_config": expected_config,
            "api_config": api_config,
            "database_config": database_config,
            "cleanup_succeeded": cleanup,
        }
        passed = metadata_config_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset_with_default_metadata_config",
                    "code": created["code"],
                    "dataset_persisted": snapshot.get("count") == 1,
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "get_default_metadata_config",
                    "http_status": fetched["http_status"],
                    "code": fetched["code"],
                    "expected_config": expected_config,
                    "api_config": api_config,
                    "database_config": database_config,
                    "raw_sha256": fetched["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"] if deleted else None,
                },
            ],
            "oracle": {"default_config": expected_config},
            "findings": []
            if passed
            else [
                {
                    "id": "DD-METADATA-CONFIG-READ-001",
                    "summary": f"{group} default metadata config API and parser_config did not agree",
                    "code_location": "api/apps/services/dataset_api_service.py:get_auto_metadata",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd031() -> dict[str, Any]:
    case_id = "TC-DD-031"
    prefix = "fresh-dd-031"
    expected_config = {
        "metadata": [
            {"key": "category", "type": "string"},
            {"key": "version", "type": "number"},
        ],
        "built_in_metadata": [],
    }

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear metadata config update fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_metadata_config_update_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        updated = _update_metadata_config(
            case_id,
            group,
            owner["auth"],
            "update_metadata_config",
            dataset_id,
            expected_config,
        )
        fetched = _get_metadata_config(
            case_id,
            group,
            owner["auth"],
            "get_updated_metadata_config",
            dataset_id,
        )
        snapshot = _dataset_snapshot(group, dataset_id) if dataset_id else {"count": 0}
        update_config = _normalize_metadata_config(updated["data"])
        api_config = _normalize_metadata_config(fetched["data"])
        database_config = _normalize_metadata_config(snapshot.get("parser_config"))
        independent_column_absent = not _knowledgebase_column_exists(group, "auto_metadata_config")
        deleted = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_updated_metadata_config_dataset",
                [dataset_id],
            )
            if dataset_id
            else None
        )
        cleanup = bool(deleted and deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0)
        observed = {
            "api_codes": [created["code"], updated["code"], fetched["code"]],
            "expected_config": expected_config,
            "update_config": update_config,
            "api_config": api_config,
            "database_config": database_config,
            "independent_column_absent": independent_column_absent,
            "cleanup_succeeded": cleanup,
        }
        passed = metadata_config_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset",
                    "code": created["code"],
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "update_and_get_metadata_config",
                    "update_code": updated["code"],
                    "get_code": fetched["code"],
                    "expected_config": expected_config,
                    "update_config": update_config,
                    "api_config": api_config,
                    "raw_sha256": [updated["raw_sha256"], fetched["raw_sha256"]],
                },
                {
                    "name": "read_only_parser_config_storage_verification",
                    "database_config": database_config,
                    "independent_column_absent": independent_column_absent,
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"] if deleted else None,
                },
            ],
            "oracle": {
                "stored_in": "knowledgebase.parser_config.metadata/built_in_metadata",
                "config": expected_config,
                "independent_auto_metadata_config_column": False,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-METADATA-CONFIG-WRITE-001",
                    "summary": f"{group} metadata config update was not consistently persisted and returned",
                    "code_location": "api/apps/services/dataset_api_service.py:update_auto_metadata",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_standard_upload_case(
    case_id: str,
    prefix: str,
    upload_batches: list[list[tuple[str, bytes, str]]],
    expected_files: list[tuple[str, bytes, str]],
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear document upload fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_upload_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        uploads = [
            _upload_local_documents(
                case_id,
                group,
                owner["auth"],
                f"upload_batch_{index}",
                dataset_id,
                batch,
            )
            for index, batch in enumerate(upload_batches, 1)
        ]
        response_items = _uploaded_items(uploads)
        response_ids = [str(item.get("id") or "") for item in response_items if item.get("id")]
        document_ids = sorted(set(response_ids) | set(_document_ids_for_dataset(group, dataset_id)))
        graph = _uploaded_document_graph_snapshot(group, dataset_id, document_ids)
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        observed = _document_upload_observed(
            dataset_id=dataset_id,
            dataset_code=created["code"],
            upload_responses=uploads,
            expected_files=expected_files,
            graph=graph,
            cleanup_succeeded=cleanup["succeeded"],
        )
        passed = document_upload_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset_and_upload_files_through_api",
                    "dataset_code": created["code"],
                    "upload_codes": [item["code"] for item in uploads],
                    "upload_http_statuses": [item["http_status"] for item in uploads],
                    "expected_file_count": len(expected_files),
                    "response_file_count": observed["response_count"],
                    "raw_sha256": [
                        created["raw_sha256"],
                        *[item["raw_sha256"] for item in uploads],
                    ],
                },
                {
                    "name": "read_only_document_file_graph_verification",
                    "database_document_count": observed["database_count"],
                    "file_count": observed["file_count"],
                    "file_link_count": observed["file_link_count"],
                    "response_ids_match_database": sorted(observed["response_ids"]) == sorted(observed["database_ids"]),
                    "response_rows_match_database": observed["response_rows_match_database"],
                    "actual_names": observed["actual_names"],
                    "expected_names": observed["expected_names"],
                    "actual_types": observed["actual_types"],
                    "expected_types": observed["expected_types"],
                    "actual_suffixes": observed["actual_suffixes"],
                    "expected_suffixes": observed["expected_suffixes"],
                    "actual_sizes": observed["actual_sizes"],
                    "expected_sizes": observed["expected_sizes"],
                    "initial_status_and_run_match": observed["initial_status_and_run_match"],
                    "file_rows_match_documents": observed["file_rows_match_documents"],
                },
                {
                    "name": "cleanup_documents_then_dataset_through_api",
                    **cleanup,
                },
            ],
            "oracle": {
                "document_status": "1",
                "document_run": "0",
                "api_run": "UNSTART",
                "file_graph_per_document": [1, 1],
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-UPLOAD-001",
                    "summary": f"{group} upload response and persisted document/file graph did not match",
                    "code_location": "api/db/services/file_service.py:upload_document",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd032() -> dict[str, Any]:
    fixture = _fixture_document("fresh-dd-032-test.pdf")
    return _run_standard_upload_case("TC-DD-032", "fresh-dd-032", [[fixture]], [fixture])


def run_dd033() -> dict[str, Any]:
    fixtures = [
        _fixture_document("fresh-dd-033-test1.pdf"),
        _fixture_document("fresh-dd-033-test2.docx"),
        _fixture_document("fresh-dd-033-test3.txt"),
    ]
    return _run_standard_upload_case("TC-DD-033", "fresh-dd-033", [fixtures], fixtures)


def run_dd034() -> dict[str, Any]:
    fixtures = [_fixture_document(f"fresh-dd-034-test{suffix}") for suffix in (".pdf", ".docx", ".xlsx", ".ppt", ".md", ".txt", ".html")]
    return _run_standard_upload_case("TC-DD-034", "fresh-dd-034", [[item] for item in fixtures], fixtures)


def run_dd035() -> dict[str, Any]:
    case_id = "TC-DD-035"
    prefix = "fresh-dd-035"
    configured_limit = 16 * 1024 * 1024
    over_name = "fresh-dd-035-over-limit.pdf"
    under_file = _fixture_document("fresh-dd-035-under-limit.pdf")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear upload limit fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_upload_limit_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        port = AUTH._free_loopback_port()
        root, environment = AUTH._prepare_isolated_runtime(
            case_id,
            group,
            port,
            environment_overrides={"MAX_CONTENT_LENGTH": str(configured_limit)},
        )
        process = None
        log_path = None
        ready_seconds = None
        stopped = False
        over_limit = None
        under_limit = None
        over_payload = b"A" * (configured_limit + 4096)
        try:
            process, log_path, ready_seconds = AUTH._launch_isolated_api(case_id, group, environment, port)
            isolated_base = f"http://127.0.0.1:{port}/api/v1"
            over_limit = _request(
                case_id,
                group,
                "upload_file_above_isolated_limit",
                owner["auth"],
                "POST",
                f"/datasets/{dataset_id}/documents",
                files=[("file", (over_name, over_payload, "application/pdf"))],
                timeout=60,
                base_url=isolated_base,
            )
            over_metadata_count = _document_count_for_datasets(group, [dataset_id])
            over_object_absent = not _storage_object_existence(group, dataset_id, [over_name]).get(over_name, False)
            under_limit = _request(
                case_id,
                group,
                "upload_valid_file_below_isolated_limit",
                owner["auth"],
                "POST",
                f"/datasets/{dataset_id}/documents",
                files=[("file", under_file)],
                timeout=60,
                base_url=isolated_base,
            )
        finally:
            stopped = AUTH._stop_isolated_api(process)

        under_items = _uploaded_items([under_limit]) if under_limit else []
        under_ids = [str(item.get("id") or "") for item in under_items if item.get("id")]
        graph = _uploaded_document_graph_snapshot(group, dataset_id, under_ids)
        under_location = graph["documents"][0]["location"] if graph.get("documents") else ""
        under_object_exists = bool(under_location and _storage_object_existence(group, dataset_id, [under_location]).get(under_location, False))
        under_persisted = bool(
            under_limit and under_limit["code"] == 0 and len(graph.get("documents", [])) == 1 and len(graph.get("files", [])) == 1 and graph.get("file_link_count") == 1 and under_object_exists
        )
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        main_api_healthy = AUTH._main_api_healthy(group)
        log_summaries = []
        for candidate in (log_path, root / "logs" / "ragflow_server.log"):
            if candidate is not None and candidate.exists():
                candidate.chmod(0o600)
                log_summaries.append(AUTH._private_log_summary(candidate))
        observed = {
            "over_limit_http_status": over_limit["http_status"] if over_limit else None,
            "over_limit_metadata_count": over_metadata_count,
            "over_limit_object_absent": over_object_absent,
            "under_limit_code": under_limit["code"] if under_limit else None,
            "under_limit_persisted": under_persisted,
            "isolated_process_stopped": stopped,
            "main_api_healthy": main_api_healthy,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = upload_limit_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset_and_launch_isolated_16_mib_api",
                    "dataset_code": created["code"],
                    "configured_limit": configured_limit,
                    "isolated_runtime_root": str(root.relative_to(PROJECT_ROOT)),
                    "ready_seconds": round(float(ready_seconds), 3),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "upload_above_limit_and_verify_no_residue",
                    "payload_size": len(over_payload),
                    "payload_sha256": hashlib.sha256(over_payload).hexdigest(),
                    "http_status": over_limit["http_status"],
                    "code": over_limit["code"],
                    "message": over_limit["message"],
                    "metadata_count": over_metadata_count,
                    "object_absent": over_object_absent,
                    "raw_sha256": over_limit["raw_sha256"],
                },
                {
                    "name": "upload_valid_file_below_limit",
                    "payload_size": len(under_file[1]),
                    "http_status": under_limit["http_status"],
                    "code": under_limit["code"],
                    "persisted_document_file_graph_and_object": under_persisted,
                    "raw_sha256": under_limit["raw_sha256"],
                },
                {
                    "name": "stop_isolated_api_cleanup_and_verify_main_health",
                    "isolated_process_stopped": stopped,
                    "main_api_healthy": main_api_healthy,
                    "cleanup": cleanup,
                    "private_log_summaries": log_summaries,
                },
            ],
            "oracle": {
                "configured_limit": configured_limit,
                "over_limit_http_status": 413,
                "over_limit_residue": 0,
                "under_limit": [200, 0],
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-UPLOAD-LIMIT-001",
                    "summary": f"{group} upload limit response or residue contract did not hold",
                    "code_location": "api/apps/__init__.py:MAX_CONTENT_LENGTH,error handling",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd036() -> dict[str, Any]:
    original = _fixture_document("fresh-dd-036-test.pdf")
    duplicate = ("fresh-dd-036-test(1).pdf", original[1], original[2])
    return _run_standard_upload_case(
        "TC-DD-036",
        "fresh-dd-036",
        [[original], [original]],
        [original, duplicate],
    )


def run_dd037() -> dict[str, Any]:
    case_id = "TC-DD-037"
    prefix = "fresh-dd-037"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear default pagination fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_default_pagination_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        documents = [
            _upload_empty_document(
                case_id,
                group,
                owner["auth"],
                f"create_pagination_document_{index:02d}",
                dataset_id,
                f"{prefix}-document-{index:02d}.txt",
            )
            for index in range(1, 36)
        ]
        document_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in documents]
        listed = _list_documents(
            case_id,
            group,
            owner["auth"],
            "list_documents_with_default_pagination",
            dataset_id,
        )
        api_rows, total = _document_list_payload(listed)
        graph = _uploaded_document_graph_snapshot(group, dataset_id, document_ids)
        api_ids = [str(item.get("id") or "") for item in api_rows]
        returned_database_rows = [item for item in graph["documents"] if item["id"] in set(api_ids)]
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_default_pagination_dataset",
            [dataset_id],
        )
        cleanup = bool(deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0 and _document_count_for_datasets(group, [dataset_id]) == 0)
        observed = {
            "http_status": listed["http_status"],
            "code": listed["code"],
            "expected_total": 35,
            "actual_total": total,
            "expected_returned_count": 30,
            "actual_returned_count": len(api_rows),
            "ids_unique": len(api_ids) == len(set(api_ids)),
            "all_results_belong_to_dataset": all(str(item.get("dataset_id") or "") == dataset_id for item in api_rows),
            "filter_exact": len(graph["documents"]) == 35 and set(api_ids).issubset({item["id"] for item in graph["documents"]}),
            "rows_match_database": _document_api_rows_match_database(api_rows, returned_database_rows),
            "cleanup_succeeded": cleanup,
        }
        passed = created["code"] == 0 and all(item["code"] == 0 for item in documents) and document_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_35_documents_through_api",
                    "dataset_code": created["code"],
                    "document_codes_all_zero": all(item["code"] == 0 for item in documents),
                    "document_id_count": len([item for item in document_ids if item]),
                    "database_document_count": len(graph["documents"]),
                    "raw_sha256": [
                        created["raw_sha256"],
                        *[item["raw_sha256"] for item in documents],
                    ],
                },
                {
                    "name": "verify_default_page_size_and_total",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "total": total,
                    "returned_count": len(api_rows),
                    "ids_unique": observed["ids_unique"],
                    "rows_match_database": observed["rows_match_database"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {"default_page_size": 30, "total": 35},
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-PAGINATION-001",
                    "summary": f"{group} default document pagination did not return 30 of 35 exact rows",
                    "code_location": "api/apps/restful_apis/document_api.py:_get_docs_with_request",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd038() -> dict[str, Any]:
    case_id = "TC-DD-038"
    prefix = "fresh-dd-038"
    names = [
        f"{prefix}-alpha-test.txt",
        f"{prefix}-beta.txt",
        f"{prefix}-gamma-test.pdf",
        f"{prefix}-other.md",
    ]

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear name search fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_name_search_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        documents = [
            _upload_empty_document(
                case_id,
                group,
                owner["auth"],
                f"create_name_search_document_{index}",
                dataset_id,
                name,
            )
            for index, name in enumerate(names, 1)
        ]
        document_ids = [str(item["data"].get("id") or "") if isinstance(item["data"], dict) else "" for item in documents]
        expected_ids = {document_id for document_id, name in zip(document_ids, names) if "test" in name}
        listed = _list_documents(
            case_id,
            group,
            owner["auth"],
            "search_documents_by_name_keyword",
            dataset_id,
            params={"keywords": "test"},
        )
        api_rows, total = _document_list_payload(listed)
        api_ids = [str(item.get("id") or "") for item in api_rows]
        graph = _uploaded_document_graph_snapshot(group, dataset_id, document_ids)
        expected_database_rows = [item for item in graph["documents"] if item["id"] in expected_ids]
        deleted = _delete_ids(case_id, group, owner["auth"], "cleanup_name_search_dataset", [dataset_id])
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "http_status": listed["http_status"],
            "code": listed["code"],
            "expected_total": 2,
            "actual_total": total,
            "expected_returned_count": 2,
            "actual_returned_count": len(api_rows),
            "ids_unique": len(api_ids) == len(set(api_ids)),
            "all_results_belong_to_dataset": all(str(item.get("dataset_id") or "") == dataset_id for item in api_rows),
            "filter_exact": set(api_ids) == expected_ids and all("test" in str(item.get("name") or "") for item in api_rows),
            "rows_match_database": _document_api_rows_match_database(api_rows, expected_database_rows),
            "cleanup_succeeded": cleanup,
        }
        passed = created["code"] == 0 and all(item["code"] == 0 for item in documents) and document_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_named_documents",
                    "api_codes": [created["code"], *[item["code"] for item in documents]],
                    "database_document_count": len(graph["documents"]),
                    "raw_sha256": [
                        created["raw_sha256"],
                        *[item["raw_sha256"] for item in documents],
                    ],
                },
                {
                    "name": "filter_documents_by_test_keyword",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "total": total,
                    "returned_names": sorted(str(item.get("name") or "") for item in api_rows),
                    "exact_expected_ids": set(api_ids) == expected_ids,
                    "rows_match_database": observed["rows_match_database"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {"keyword": "test", "matching_document_count": 2},
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-NAME-FILTER-001",
                    "summary": f"{group} document keyword search did not return the exact matching names",
                    "code_location": "api/db/services/document_service.py:get_by_kb_id",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd039() -> dict[str, Any]:
    case_id = "TC-DD-039"
    prefix = "fresh-dd-039"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear status filter fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_status_filter_dataset",
            {"name": prefix},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        done_file = _fixture_document(f"{prefix}-done.txt")
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_document_to_parse_done",
            dataset_id,
            [done_file],
        )
        uploaded_items = _uploaded_items([uploaded])
        done_id = str(uploaded_items[0].get("id") or "") if uploaded_items else ""
        unstarted = _upload_empty_document(
            case_id,
            group,
            owner["auth"],
            "create_unstarted_document",
            dataset_id,
            f"{prefix}-unstarted.txt",
        )
        unstarted_data = unstarted["data"] if isinstance(unstarted["data"], dict) else {}
        unstarted_id = str(unstarted_data.get("id") or "")
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            "parse_one_document_to_done",
            dataset_id,
            [done_id],
        )
        parsing = _wait_for_document_parsing(group, [done_id], timeout=240)
        listed_text = _list_documents(
            case_id,
            group,
            owner["auth"],
            "filter_documents_by_done_text",
            dataset_id,
            params={"run": "DONE"},
        )
        listed_numeric = _list_documents(
            case_id,
            group,
            owner["auth"],
            "filter_documents_by_done_numeric",
            dataset_id,
            params={"run": "3"},
        )
        api_rows, total = _document_list_payload(listed_text)
        numeric_rows, numeric_total = _document_list_payload(listed_numeric)
        api_ids = [str(item.get("id") or "") for item in api_rows]
        numeric_ids = [str(item.get("id") or "") for item in numeric_rows]
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [done_id, unstarted_id])
        done_database_rows = [item for item in graph["documents"] if item["id"] == done_id]
        locations = [item["location"] for item in graph["documents"] if item["id"] == done_id and item["location"]]
        deleted_done = (
            _delete_documents(
                case_id,
                group,
                owner["auth"],
                "cleanup_parsed_document",
                dataset_id,
                [done_id],
            )
            if done_id
            else None
        )
        object_state = _storage_object_existence(group, dataset_id, locations) if locations else {}
        deleted_dataset = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_status_filter_dataset",
            [dataset_id],
        )
        cleanup = bool(
            deleted_done
            and deleted_done["code"] == 0
            and not any(object_state.values())
            and deleted_dataset["code"] == 0
            and _dataset_snapshot(group, dataset_id).get("count") == 0
            and _document_count_for_datasets(group, [dataset_id]) == 0
        )
        terminal_rows = parsing.get("documents", [])
        done_reached = parsing.get("terminal") is True and len(terminal_rows) == 1 and terminal_rows[0].get("run") == "3"
        unstarted_preserved = any(item["id"] == unstarted_id and item["run"] == "0" for item in graph["documents"])
        observed = {
            "http_status": listed_text["http_status"],
            "code": listed_text["code"],
            "expected_total": 1,
            "actual_total": total,
            "expected_returned_count": 1,
            "actual_returned_count": len(api_rows),
            "ids_unique": len(api_ids) == len(set(api_ids)),
            "all_results_belong_to_dataset": all(str(item.get("dataset_id") or "") == dataset_id for item in api_rows),
            "filter_exact": api_ids == [done_id]
            and numeric_ids == [done_id]
            and numeric_total == 1
            and all(str(item.get("run") or "") == "DONE" for item in api_rows)
            and done_reached
            and unstarted_preserved,
            "rows_match_database": _document_api_rows_match_database(api_rows, done_database_rows),
            "cleanup_succeeded": cleanup,
        }
        parsed_data = parsed["data"] if isinstance(parsed["data"], dict) else {}
        passed = (
            created["code"] == uploaded["code"] == unstarted["code"] == 0
            and parsed["code"] == 0
            and parsed_data.get("success_count") == 1
            and listed_numeric["code"] == 0
            and document_list_contract_ok(observed)
        )
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_unstarted_and_parse_target_documents",
                    "api_codes": [
                        created["code"],
                        uploaded["code"],
                        unstarted["code"],
                        parsed["code"],
                    ],
                    "parse_success_count": parsed_data.get("success_count"),
                    "parsing_terminal": parsing.get("terminal"),
                    "terminal_rows": terminal_rows,
                    "done_reached": done_reached,
                    "unstarted_preserved": unstarted_preserved,
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        unstarted["raw_sha256"],
                        parsed["raw_sha256"],
                    ],
                },
                {
                    "name": "filter_done_by_text_and_numeric_status",
                    "text_code": listed_text["code"],
                    "numeric_code": listed_numeric["code"],
                    "text_ids": api_ids,
                    "numeric_ids": numeric_ids,
                    "totals": [total, numeric_total],
                    "rows_match_database": observed["rows_match_database"],
                    "raw_sha256": [
                        listed_text["raw_sha256"],
                        listed_numeric["raw_sha256"],
                    ],
                },
                {
                    "name": "cleanup_parsed_document_then_dataset_through_api",
                    "document_delete_code": deleted_done["code"] if deleted_done else None,
                    "dataset_delete_code": deleted_dataset["code"],
                    "remaining_objects": sum(1 for value in object_state.values() if value),
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": [
                        deleted_done["raw_sha256"] if deleted_done else None,
                        deleted_dataset["raw_sha256"],
                    ],
                },
            ],
            "oracle": {
                "text_filter": "DONE",
                "numeric_filter": "3",
                "expected_ids": [done_id],
                "excluded_run": "UNSTART",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-STATUS-FILTER-001",
                    "summary": f"{group} DONE text/numeric filter did not return only the completed document",
                    "code_location": "api/apps/restful_apis/document_api.py:_parse_run_status_filter",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd040() -> dict[str, Any]:
    case_id = "TC-DD-040"
    prefix = "fresh-dd-040"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear document id fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_document_id_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        document = _upload_empty_document(
            case_id,
            group,
            owner["auth"],
            "create_document_for_id_filter",
            dataset_id,
            f"{prefix}-document.txt",
        )
        document_data = document["data"] if isinstance(document["data"], dict) else {}
        document_id = str(document_data.get("id") or "")
        listed = _list_documents(
            case_id,
            group,
            owner["auth"],
            "get_document_details_by_id_filter",
            dataset_id,
            params={"id": document_id},
        )
        api_rows, total = _document_list_payload(listed)
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id])
        api_ids = [str(item.get("id") or "") for item in api_rows]
        detail_fields_present = len(api_rows) == 1 and all(field in api_rows[0] for field in ("progress", "progress_msg", "chunk_count", "token_count", "run"))
        deleted = _delete_ids(case_id, group, owner["auth"], "cleanup_document_id_dataset", [dataset_id])
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "http_status": listed["http_status"],
            "code": listed["code"],
            "expected_total": 1,
            "actual_total": total,
            "expected_returned_count": 1,
            "actual_returned_count": len(api_rows),
            "ids_unique": len(api_ids) == len(set(api_ids)),
            "all_results_belong_to_dataset": all(str(item.get("dataset_id") or "") == dataset_id for item in api_rows),
            "filter_exact": api_ids == [document_id] and detail_fields_present,
            "rows_match_database": _document_api_rows_match_database(api_rows, graph["documents"]),
            "cleanup_succeeded": cleanup,
        }
        passed = created["code"] == document["code"] == 0 and document_list_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_document",
                    "api_codes": [created["code"], document["code"]],
                    "document_persisted": len(graph["documents"]) == 1,
                    "raw_sha256": [created["raw_sha256"], document["raw_sha256"]],
                },
                {
                    "name": "get_json_document_details_via_list_id_filter",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "total": total,
                    "returned_ids": api_ids,
                    "detail_fields_present": detail_fields_present,
                    "rows_match_database": observed["rows_match_database"],
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "endpoint": "GET /datasets/<dataset_id>/documents?id=<doc_id>",
                "total": 1,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-ID-FILTER-001",
                    "summary": f"{group} document ID list filter did not return one matching database row",
                    "code_location": "api/apps/restful_apis/document_api.py:_get_docs_with_request",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd041() -> dict[str, Any]:
    case_id = "TC-DD-041"
    prefix = "fresh-dd-041"
    new_name = "新文档名称.pdf"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear basic document update fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_document_update_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        document = _upload_empty_document(
            case_id,
            group,
            owner["auth"],
            "create_document_to_update",
            dataset_id,
            f"{prefix}-original.pdf",
        )
        document_data = document["data"] if isinstance(document["data"], dict) else {}
        document_id = str(document_data.get("id") or "")
        chunk = _add_chunk(
            case_id,
            group,
            owner["auth"],
            "add_chunk_before_chunk_method_change",
            dataset_id,
            document_id,
            {
                "content": "fresh dd041 old chunk content",
                "important_keywords": ["old", "chunk"],
            },
        )
        chunk_data = chunk["data"] if isinstance(chunk["data"], dict) else {}
        chunk_id = str((chunk_data.get("chunk") or {}).get("id") or "")
        before = _document_state_snapshot(group, dataset_id, document_id)
        docstore_before = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, [chunk_id])
        updated = _update_document(
            case_id,
            group,
            owner["auth"],
            "rename_document_and_change_chunk_method",
            dataset_id,
            document_id,
            {"name": new_name, "chunk_method": "qa"},
        )
        updated_data = updated["data"] if isinstance(updated["data"], dict) else {}
        after = _document_state_snapshot(group, dataset_id, document_id)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "verify_old_chunks_cleared_after_chunk_method_change",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        listed_chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        docstore_after = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, [chunk_id])
        deleted = _delete_ids(case_id, group, owner["auth"], "cleanup_document_update_dataset", [dataset_id])
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        api_matches = str(updated_data.get("id") or "") == document_id and updated_data.get("name") == new_name and updated_data.get("chunk_method") == "qa"
        database_matches = after.get("name") == new_name and after.get("parser_id") == "qa"
        side_effects_match = (
            before.get("chunk_num") == 1
            and int(before.get("token_num", 0)) > 0
            and after.get("run") == "0"
            and after.get("progress") == 0
            and after.get("progress_msg") == ""
            and after.get("chunk_num") == 0
            and after.get("token_num") == 0
            and after.get("dataset_chunk_num") == 0
            and after.get("dataset_token_num") == 0
        )
        docstore_matches = (
            chunk_id in docstore_before.get("existing_ids", [])
            and chunk_id not in docstore_after.get("existing_ids", [])
            and listed["code"] == 0
            and listed_chunks == []
            and int(listed_data.get("total") or 0) == 0
        )
        observed = {
            "api_code": updated["code"],
            "api_matches": api_matches,
            "database_matches": database_matches,
            "side_effects_match": side_effects_match,
            "docstore_matches": docstore_matches,
            "cleanup_succeeded": cleanup,
        }
        passed = created["code"] == document["code"] == chunk["code"] == 0 and document_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_document_with_existing_chunk",
                    "api_codes": [created["code"], document["code"], chunk["code"]],
                    "chunk_id_present": bool(chunk_id),
                    "before_state": before,
                    "docstore_before": docstore_before,
                    "raw_sha256": [
                        created["raw_sha256"],
                        document["raw_sha256"],
                        chunk["raw_sha256"],
                    ],
                },
                {
                    "name": "rename_and_change_chunk_method",
                    "http_status": updated["http_status"],
                    "code": updated["code"],
                    "api_matches": api_matches,
                    "database_matches": database_matches,
                    "side_effects_match": side_effects_match,
                    "after_state": after,
                    "raw_sha256": updated["raw_sha256"],
                },
                {
                    "name": "verify_old_chunk_removed_from_docstore",
                    "list_code": listed["code"],
                    "listed_total": int(listed_data.get("total") or 0),
                    "listed_chunk_count": len(listed_chunks),
                    "docstore_after": docstore_after,
                    "docstore_matches": docstore_matches,
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "name": new_name,
                "parser_id": "qa",
                "run": "0",
                "chunk_num": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-BASIC-UPDATE-001",
                    "summary": f"{group} document rename/chunk method reset was not fully visible in metadata and DocEngine",
                    "code_location": "api/apps/services/document_api_service.py:update_document_name_only,update_chunk_method",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd042() -> dict[str, Any]:
    case_id = "TC-DD-042"
    prefix = "fresh-dd-042"
    expected_patch = {"chunk_token_num": 256, "layout_recognize": "DeepDOC"}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear parser config fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_parser_config_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        document = _upload_empty_document(
            case_id,
            group,
            owner["auth"],
            "create_document_for_parser_config_update",
            dataset_id,
            f"{prefix}-document.txt",
        )
        document_data = document["data"] if isinstance(document["data"], dict) else {}
        document_id = str(document_data.get("id") or "")
        before = _document_state_snapshot(group, dataset_id, document_id)
        updated = _update_document(
            case_id,
            group,
            owner["auth"],
            "update_document_parser_config",
            dataset_id,
            document_id,
            {"parser_config": expected_patch},
        )
        updated_data = updated["data"] if isinstance(updated["data"], dict) else {}
        api_parser_config = updated_data.get("parser_config") if isinstance(updated_data.get("parser_config"), dict) else {}
        after = _document_state_snapshot(group, dataset_id, document_id)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "verify_parser_config_update_did_not_schedule_chunks",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        deleted = _delete_ids(case_id, group, owner["auth"], "cleanup_parser_config_dataset", [dataset_id])
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        api_matches = all(api_parser_config.get(key) == value for key, value in expected_patch.items())
        database_matches = all(after.get("parser_config", {}).get(key) == value for key, value in expected_patch.items())
        side_effects_match = all(after.get(key) == before.get(key) for key in ("run", "progress", "progress_msg", "chunk_num", "token_num", "task_count"))
        docstore_matches = listed["code"] == 0 and int(listed_data.get("total") or 0) == 0 and listed_data.get("chunks") == []
        observed = {
            "api_code": updated["code"],
            "api_matches": api_matches,
            "database_matches": database_matches,
            "side_effects_match": side_effects_match,
            "docstore_matches": docstore_matches,
            "cleanup_succeeded": cleanup,
        }
        passed = created["code"] == document["code"] == 0 and document_update_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_unparsed_document",
                    "api_codes": [created["code"], document["code"]],
                    "before_state": before,
                    "raw_sha256": [created["raw_sha256"], document["raw_sha256"]],
                },
                {
                    "name": "update_parser_config_and_verify_database",
                    "http_status": updated["http_status"],
                    "code": updated["code"],
                    "expected_patch": expected_patch,
                    "api_values": {key: api_parser_config.get(key) for key in expected_patch},
                    "database_values": {key: after.get("parser_config", {}).get(key) for key in expected_patch},
                    "api_matches": api_matches,
                    "database_matches": database_matches,
                    "raw_sha256": updated["raw_sha256"],
                },
                {
                    "name": "verify_update_does_not_schedule_or_create_chunks",
                    "side_effects_match": side_effects_match,
                    "list_code": listed["code"],
                    "listed_total": int(listed_data.get("total") or 0),
                    "docstore_matches": docstore_matches,
                    "after_state": after,
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "parser_config_patch": expected_patch,
                "automatic_schedule": False,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-PARSER-CONFIG-001",
                    "summary": f"{group} parser_config patch or no-auto-schedule contract did not hold",
                    "code_location": "api/db/services/document_service.py:update_parser_config",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd043() -> dict[str, Any]:
    case_id = "TC-DD-043"
    prefix = "fresh-dd-043"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear single document delete fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_single_delete_dataset",
            {"name": prefix},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        fixture = _fixture_document(f"{prefix}-parsed.txt")
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_document_for_single_delete",
            dataset_id,
            [fixture],
        )
        uploaded_items = _uploaded_items([uploaded])
        document_id = str(uploaded_items[0].get("id") or "") if uploaded_items else ""
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            "parse_document_before_single_delete",
            dataset_id,
            [document_id],
        )
        parsing = _wait_for_document_parsing(group, [document_id], timeout=240)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "list_chunks_before_single_delete",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        chunk_ids = [str(item.get("id") or item.get("chunk_id") or "") for item in chunks if isinstance(item, dict) and (item.get("id") or item.get("chunk_id"))]
        state_before = _document_state_snapshot(group, dataset_id, document_id)
        resources_before = _cascade_resource_snapshot(group, dataset_id, [document_id])
        objects_before = _storage_object_existence(group, dataset_id, resources_before["locations"])
        docstore_before = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, chunk_ids)
        deleted = _delete_documents(
            case_id,
            group,
            owner["auth"],
            "delete_single_document",
            dataset_id,
            [document_id],
        )
        deleted_data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        resources_after = _saved_cascade_resource_counts(
            group,
            dataset_id,
            resources_before["document_ids"],
            resources_before["task_ids"],
            resources_before["file_ids"],
            resources_before["file_link_ids"],
        )
        objects_after = _storage_object_existence(group, dataset_id, resources_before["locations"])
        docstore_after = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, chunk_ids)
        dataset_after = _dataset_snapshot(group, dataset_id)
        cleaned_dataset = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_empty_dataset_after_single_delete",
            [dataset_id],
        )
        cleanup = cleaned_dataset["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        terminal_rows = parsing.get("documents", [])
        precondition_matches = (
            created["code"] == uploaded["code"] == parsed["code"] == listed["code"] == 0
            and parsing.get("terminal") is True
            and len(terminal_rows) == 1
            and terminal_rows[0].get("run") == "3"
            and state_before.get("chunk_num", 0) >= 1
            and state_before.get("token_num", 0) > 0
            and len(resources_before["document_ids"]) == 1
            and len(resources_before["task_ids"]) >= 1
            and len(resources_before["file_ids"]) == 1
            and len(resources_before["file_link_ids"]) == 1
            and bool(resources_before["locations"])
            and all(objects_before.values())
            and bool(chunk_ids)
            and set(chunk_ids).issubset(set(docstore_before.get("existing_ids", [])))
        )
        metadata_resources_removed = (
            resources_after["dataset_count"] == 1
            and resources_after["document_count"] == 0
            and resources_after["task_count"] == 0
            and resources_after["file_count"] == 0
            and resources_after["file_link_count"] == 0
        )
        docstore_resources_removed = not set(chunk_ids).intersection(docstore_after.get("existing_ids", []))
        storage_objects_removed = not any(objects_after.values())
        dataset_counts_zero = dataset_after.get("count") == 1 and dataset_after.get("doc_num") == 0 and dataset_after.get("chunk_num") == 0 and dataset_after.get("token_num") == 0
        observed = {
            "api_code": deleted["code"],
            "expected_deleted_count": 1,
            "actual_deleted_count": deleted_data.get("deleted"),
            "precondition_matches": precondition_matches,
            "metadata_resources_removed": metadata_resources_removed,
            "docstore_resources_removed": docstore_resources_removed,
            "storage_objects_removed": storage_objects_removed,
            "dataset_counts_zero": dataset_counts_zero,
            "cleanup_succeeded": cleanup,
        }
        passed = document_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_parse_and_verify_single_document_precondition",
                    "api_codes": [
                        created["code"],
                        uploaded["code"],
                        parsed["code"],
                        listed["code"],
                    ],
                    "parsing": parsing,
                    "state_before": state_before,
                    "resource_counts_before": {key: len(resources_before[key]) for key in ("document_ids", "task_ids", "file_ids", "file_link_ids", "locations")},
                    "all_objects_exist_before": all(objects_before.values()),
                    "chunk_count_before": len(chunk_ids),
                    "docstore_before": docstore_before,
                    "precondition_matches": precondition_matches,
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        parsed["raw_sha256"],
                        listed["raw_sha256"],
                    ],
                },
                {
                    "name": "delete_single_document_and_verify_all_resources",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "deleted_count": deleted_data.get("deleted"),
                    "resource_counts_after": resources_after,
                    "metadata_resources_removed": metadata_resources_removed,
                    "docstore_after": docstore_after,
                    "docstore_resources_removed": docstore_resources_removed,
                    "remaining_storage_objects": sum(1 for value in objects_after.values() if value),
                    "dataset_after": dataset_after,
                    "dataset_counts_zero": dataset_counts_zero,
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "cleanup_empty_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": cleaned_dataset["raw_sha256"],
                },
            ],
            "oracle": {
                "deleted_documents": 1,
                "remaining_document_task_file_link_chunk_object_counts": 0,
                "dataset_doc_chunk_token_counts": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-DELETE-001",
                    "summary": f"{group} single document delete left resources or stale dataset counts",
                    "code_location": "api/db/services/file_service.py:delete_docs",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd044() -> dict[str, Any]:
    case_id = "TC-DD-044"
    prefix = "fresh-dd-044"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear batch document delete fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_batch_delete_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        fixtures = [_fixture_document(f"{prefix}-document-{index}.txt") for index in range(1, 4)]
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_three_documents_for_batch_delete",
            dataset_id,
            fixtures,
        )
        items = _uploaded_items([uploaded])
        document_ids = [str(item.get("id") or "") for item in items if item.get("id")]
        resources_before = _cascade_resource_snapshot(group, dataset_id, document_ids)
        objects_before = _storage_object_existence(group, dataset_id, resources_before["locations"])
        states_before = [_document_state_snapshot(group, dataset_id, document_id) for document_id in document_ids]
        deleted = _delete_documents(
            case_id,
            group,
            owner["auth"],
            "batch_delete_three_documents",
            dataset_id,
            document_ids,
        )
        deleted_data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        resources_after = _saved_cascade_resource_counts(
            group,
            dataset_id,
            resources_before["document_ids"],
            resources_before["task_ids"],
            resources_before["file_ids"],
            resources_before["file_link_ids"],
        )
        objects_after = _storage_object_existence(group, dataset_id, resources_before["locations"])
        dataset_after = _dataset_snapshot(group, dataset_id)
        cleaned_dataset = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_empty_dataset_after_batch_delete",
            [dataset_id],
        )
        cleanup = cleaned_dataset["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        precondition_matches = (
            created["code"] == uploaded["code"] == 0
            and len(document_ids) == 3
            and len(resources_before["document_ids"]) == 3
            and len(resources_before["file_ids"]) == 3
            and len(resources_before["file_link_ids"]) == 3
            and len(resources_before["locations"]) == 3
            and all(objects_before.values())
            and all(state.get("run") == "0" and state.get("chunk_num") == 0 and state.get("token_num") == 0 for state in states_before)
        )
        metadata_resources_removed = (
            resources_after["dataset_count"] == 1
            and resources_after["document_count"] == 0
            and resources_after["task_count"] == 0
            and resources_after["file_count"] == 0
            and resources_after["file_link_count"] == 0
        )
        dataset_counts_zero = dataset_after.get("count") == 1 and dataset_after.get("doc_num") == 0 and dataset_after.get("chunk_num") == 0 and dataset_after.get("token_num") == 0
        observed = {
            "api_code": deleted["code"],
            "expected_deleted_count": 3,
            "actual_deleted_count": deleted_data.get("deleted"),
            "precondition_matches": precondition_matches,
            "metadata_resources_removed": metadata_resources_removed,
            "docstore_resources_removed": True,
            "storage_objects_removed": not any(objects_after.values()),
            "dataset_counts_zero": dataset_counts_zero,
            "cleanup_succeeded": cleanup,
        }
        passed = document_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_unparsed_documents",
                    "api_codes": [created["code"], uploaded["code"]],
                    "document_count": len(document_ids),
                    "file_count": len(resources_before["file_ids"]),
                    "file_link_count": len(resources_before["file_link_ids"]),
                    "all_objects_exist_before": all(objects_before.values()),
                    "precondition_matches": precondition_matches,
                    "raw_sha256": [created["raw_sha256"], uploaded["raw_sha256"]],
                },
                {
                    "name": "batch_delete_and_verify_metadata_objects_and_counts",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "deleted_count": deleted_data.get("deleted"),
                    "resource_counts_after": resources_after,
                    "remaining_storage_objects": sum(1 for value in objects_after.values() if value),
                    "dataset_after": dataset_after,
                    "metadata_resources_removed": metadata_resources_removed,
                    "dataset_counts_zero": dataset_counts_zero,
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "cleanup_empty_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": cleaned_dataset["raw_sha256"],
                },
            ],
            "oracle": {
                "deleted_documents": 3,
                "remaining_document_file_link_object_counts": 0,
                "dataset_doc_chunk_token_counts": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-BATCH-DELETE-001",
                    "summary": f"{group} batch document delete left resources or stale dataset counts",
                    "code_location": "api/apps/restful_apis/document_api.py:delete_documents",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd045() -> dict[str, Any]:
    case_id = "TC-DD-045"
    prefix = "fresh-dd-045"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear parse trigger fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_parse_trigger_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        fixture = (
            f"{prefix}-document.txt",
            b"fresh parse trigger document contains stable ascii retrieval content\n",
            "text/plain",
        )
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_unparsed_document",
            dataset_id,
            [fixture],
        )
        items = _uploaded_items([uploaded])
        document_id = str(items[0].get("id") or "") if items else ""
        before = _document_state_snapshot(group, dataset_id, document_id)
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            "trigger_document_parse",
            dataset_id,
            [document_id],
        )
        immediate = _document_state_snapshot(group, dataset_id, document_id)
        progress = _poll_document_progress_api(
            case_id,
            group,
            owner["auth"],
            dataset_id,
            document_id,
            timeout=240,
            interval=0.25,
        )
        final_state = _document_state_snapshot(group, dataset_id, document_id)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "list_chunks_after_parse_done",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id])
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        parsed_data = parsed["data"] if isinstance(parsed["data"], dict) else {}
        trigger_matches = before.get("run") == "0" and before.get("process_begin_at_present") is False and parsed_data.get("success_count") == 1
        running_seen = immediate.get("run") == "1" or any(item.get("run") == "RUNNING" for item in progress["observations"])
        lifecycle_matches = (
            running_seen
            and immediate.get("process_begin_at_present") is True
            and immediate.get("task_count", 0) >= 1
            and progress.get("terminal") is True
            and progress.get("final", {}).get("run") == "DONE"
            and final_state.get("run") == "3"
            and final_state.get("progress") == 1
            and final_state.get("process_begin_at_present") is True
        )
        final_rows = progress.get("final", {})
        final_api_matches = (
            final_rows.get("code") == 0
            and final_rows.get("total") == 1
            and final_rows.get("run") == "DONE"
            and final_rows.get("progress") == 1
            and final_rows.get("chunk_count") == final_state.get("chunk_num")
            and final_rows.get("token_count") == final_state.get("token_num")
        )
        chunk_contract_matches = (
            listed["code"] == 0 and len(chunks) >= 1 and int(listed_data.get("total") or 0) == len(chunks) and len(chunks) == final_state.get("chunk_num") and final_state.get("token_num", 0) > 0
        )
        observed = {
            "api_codes": [
                created["code"],
                uploaded["code"],
                parsed["code"],
                listed["code"],
                *progress["codes"],
            ],
            "trigger_matches": trigger_matches,
            "lifecycle_matches": lifecycle_matches,
            "final_api_matches": final_api_matches,
            "chunk_contract_matches": chunk_contract_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = document_parse_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_unparsed_document",
                    "api_codes": [created["code"], uploaded["code"]],
                    "before_state": before,
                    "raw_sha256": [created["raw_sha256"], uploaded["raw_sha256"]],
                },
                {
                    "name": "trigger_parse_and_capture_running_state",
                    "http_status": parsed["http_status"],
                    "code": parsed["code"],
                    "success_count": parsed_data.get("success_count"),
                    "immediate_state": immediate,
                    "running_seen": running_seen,
                    "trigger_matches": trigger_matches,
                    "raw_sha256": parsed["raw_sha256"],
                },
                {
                    "name": "poll_until_done_and_verify_chunks",
                    "poll_count": progress["poll_count"],
                    "observations": progress["observations"],
                    "terminal": progress["terminal"],
                    "final_state": final_state,
                    "lifecycle_matches": lifecycle_matches,
                    "final_api_matches": final_api_matches,
                    "listed_chunk_count": len(chunks),
                    "listed_total": int(listed_data.get("total") or 0),
                    "chunk_contract_matches": chunk_contract_matches,
                    "progress_raw_sha256": progress["raw_sha256"],
                    "chunk_list_raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_document_and_dataset_through_api",
                    **cleanup,
                },
            ],
            "oracle": {
                "trigger_success_count": 1,
                "lifecycle": ["UNSTART", "RUNNING", "DONE"],
                "final_progress": 1,
                "minimum_chunks": 1,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-PARSE-TRIGGER-001",
                    "summary": f"{group} parse trigger did not expose the required RUNNING-to-DONE lifecycle and chunks",
                    "code_location": "api/apps/restful_apis/document_api.py:parse_documents",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd046() -> dict[str, Any]:
    case_id = "TC-DD-046"
    prefix = "fresh-dd-046"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear parse progress fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_parse_progress_dataset",
            {
                "name": prefix,
                "parser_config": {
                    "chunk_token_num": 32,
                    "delimiter": "`###`",
                    "layout_recognize": "Plain Text",
                },
            },
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        segments = [f"progress segment {index:03d} contains deterministic ascii tokens for embedding" for index in range(1, 161)]
        fixture = (
            f"{prefix}-document.txt",
            " ### ".join(segments).encode("ascii"),
            "text/plain",
        )
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_medium_progress_document",
            dataset_id,
            [fixture],
        )
        items = _uploaded_items([uploaded])
        document_id = str(items[0].get("id") or "") if items else ""
        parsed = _parse_documents(
            case_id,
            group,
            owner["auth"],
            "queue_document_for_progress_polling",
            dataset_id,
            [document_id],
        )
        progress = _poll_document_progress_api(
            case_id,
            group,
            owner["auth"],
            dataset_id,
            document_id,
            timeout=300,
            interval=0.25,
        )
        final_state = _document_state_snapshot(group, dataset_id, document_id)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "list_chunks_after_progress_done",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id])
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        observations = progress["observations"]
        progress_values = [float(item.get("progress") or 0) for item in observations]
        running_seen = any(item.get("run") == "RUNNING" for item in observations)
        progress_in_range = all(0 <= value <= 1 for value in progress_values)
        progress_nondecreasing = all(earlier <= later for earlier, later in zip(progress_values, progress_values[1:]))
        message_seen = any(bool(str(item.get("progress_msg") or "").strip()) for item in observations)
        trigger_matches = parsed["code"] == 0
        lifecycle_matches = (
            progress["terminal"] is True
            and running_seen
            and progress_in_range
            and progress_nondecreasing
            and message_seen
            and progress["final"].get("run") == "DONE"
            and progress["final"].get("progress") == 1
            and final_state.get("run") == "3"
            and final_state.get("progress") == 1
        )
        final_api_matches = (
            progress["final"].get("code") == 0
            and progress["final"].get("total") == 1
            and progress["final"].get("chunk_count") == final_state.get("chunk_num")
            and progress["final"].get("token_count") == final_state.get("token_num")
        )
        chunk_contract_matches = listed["code"] == 0 and len(chunks) >= 2 and int(listed_data.get("total") or 0) == final_state.get("chunk_num")
        observed = {
            "api_codes": [
                created["code"],
                uploaded["code"],
                parsed["code"],
                listed["code"],
                *progress["codes"],
            ],
            "trigger_matches": trigger_matches,
            "lifecycle_matches": lifecycle_matches,
            "final_api_matches": final_api_matches,
            "chunk_contract_matches": chunk_contract_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = document_parse_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_and_queue_medium_document",
                    "api_codes": [created["code"], uploaded["code"], parsed["code"]],
                    "segment_count": len(segments),
                    "payload_size": len(fixture[1]),
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        parsed["raw_sha256"],
                    ],
                },
                {
                    "name": "poll_progress_until_terminal",
                    "poll_count": progress["poll_count"],
                    "observations": observations,
                    "running_seen": running_seen,
                    "progress_in_range": progress_in_range,
                    "progress_nondecreasing": progress_nondecreasing,
                    "progress_message_seen": message_seen,
                    "terminal": progress["terminal"],
                    "final_state": final_state,
                    "lifecycle_matches": lifecycle_matches,
                    "raw_sha256": progress["raw_sha256"],
                },
                {
                    "name": "verify_final_chunk_counts",
                    "list_code": listed["code"],
                    "listed_chunk_count": len(chunks),
                    "listed_total": int(listed_data.get("total") or 0),
                    "final_api_matches": final_api_matches,
                    "chunk_contract_matches": chunk_contract_matches,
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_document_and_dataset_through_api",
                    **cleanup,
                },
            ],
            "oracle": {
                "running_observation_required": True,
                "progress_range": [0, 1],
                "final": ["DONE", 1],
                "minimum_chunks": 2,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-PROGRESS-001",
                    "summary": f"{group} parse progress polling did not expose a valid RUNNING-to-DONE progression",
                    "code_location": "api/db/services/task_service.py:document progress callback",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd047() -> dict[str, Any]:
    case_id = "TC-DD-047"
    prefix = "fresh-dd-047"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear parse failure fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_parse_failure_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        fixture = (
            f"{prefix}-corrupt.pdf",
            b"%PDF-1.4\nintentionally corrupt payload without objects\n%%EOF\n",
            "application/pdf",
        )
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_corrupt_pdf",
            dataset_id,
            [fixture],
        )
        items = _uploaded_items([uploaded])
        document_id = str(items[0].get("id") or "") if items else ""
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            "trigger_corrupt_pdf_parse",
            dataset_id,
            [document_id],
        )
        progress = _poll_document_progress_api(
            case_id,
            group,
            owner["auth"],
            dataset_id,
            document_id,
            timeout=240,
            interval=0.25,
        )
        final_state = _document_state_snapshot(group, dataset_id, document_id)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "verify_failed_parse_has_no_chunks",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id])
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        parsed_data = parsed["data"] if isinstance(parsed["data"], dict) else {}
        final_api = progress.get("final", {})
        trigger_matches = parsed_data.get("success_count") == 1
        lifecycle_matches = progress.get("terminal") is True and final_state.get("run") == "4" and bool(final_state.get("progress_msg", "").strip())
        final_api_matches = final_api.get("code") == 0 and final_api.get("total") == 1 and final_api.get("run") == "FAIL" and bool(str(final_api.get("progress_msg") or "").strip())
        chunk_contract_matches = listed["code"] == 0 and chunks == [] and int(listed_data.get("total") or 0) == 0 and final_state.get("chunk_num") == 0 and final_state.get("token_num") == 0
        observed = {
            "api_codes": [
                created["code"],
                uploaded["code"],
                parsed["code"],
                listed["code"],
                *progress["codes"],
            ],
            "trigger_matches": trigger_matches,
            "lifecycle_matches": lifecycle_matches,
            "final_api_matches": final_api_matches,
            "chunk_contract_matches": chunk_contract_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = document_parse_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "upload_corrupt_pdf_and_trigger_parse",
                    "api_codes": [created["code"], uploaded["code"], parsed["code"]],
                    "parse_success_count": parsed_data.get("success_count"),
                    "payload_size": len(fixture[1]),
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        parsed["raw_sha256"],
                    ],
                },
                {
                    "name": "poll_until_fail_and_verify_error_message",
                    "poll_count": progress["poll_count"],
                    "observations": progress["observations"],
                    "terminal": progress["terminal"],
                    "final_state": final_state,
                    "lifecycle_matches": lifecycle_matches,
                    "final_api_matches": final_api_matches,
                    "raw_sha256": progress["raw_sha256"],
                },
                {
                    "name": "verify_no_chunks_created",
                    "list_code": listed["code"],
                    "listed_total": int(listed_data.get("total") or 0),
                    "listed_chunk_count": len(chunks),
                    "chunk_contract_matches": chunk_contract_matches,
                    "raw_sha256": listed["raw_sha256"],
                },
                {
                    "name": "cleanup_document_and_dataset_through_api",
                    **cleanup,
                },
            ],
            "oracle": {
                "final_run": "FAIL",
                "database_run": "4",
                "progress_message_nonempty": True,
                "chunks": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DOCUMENT-PARSE-FAILURE-001",
                    "summary": f"{group} corrupt PDF did not terminate as FAIL with an error and zero chunks",
                    "code_location": "api/db/services/task_service.py:parse failure handling",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd048() -> dict[str, Any]:
    case_id = "TC-DD-048"
    prefix = "fresh-dd-048"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear chunk list fixture")
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_chunk_list_dataset",
            {
                "name": prefix,
                "parser_config": {
                    "chunk_token_num": 8,
                    "delimiter": "`###`",
                    "layout_recognize": "Plain Text",
                },
            },
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        segments = [f"chunk list segment {index} contains deterministic ascii content" for index in range(1, 13)]
        fixture = (
            f"{prefix}-document.txt",
            " ### ".join(segments).encode("ascii"),
            "text/plain",
        )
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_document_for_chunk_listing",
            dataset_id,
            [fixture],
        )
        items = _uploaded_items([uploaded])
        document_id = str(items[0].get("id") or "") if items else ""
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            "parse_document_for_chunk_listing",
            dataset_id,
            [document_id],
        )
        parsing = _wait_for_document_parsing(group, [document_id], timeout=240)
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "get_all_document_chunks",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        chunk_ids = [str(item.get("id") or item.get("chunk_id") or "") for item in chunks if isinstance(item, dict) and (item.get("id") or item.get("chunk_id"))]
        state = _document_state_snapshot(group, dataset_id, document_id)
        docstore = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, chunk_ids)
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id])
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        precondition_matches = parsing.get("terminal") is True and len(parsing.get("documents", [])) == 1 and parsing["documents"][0].get("run") == "3" and state.get("chunk_num", 0) >= 2
        api_readback_matches = (
            listed["code"] == 0
            and len(chunks) >= 2
            and len(chunk_ids) == len(chunks)
            and all(bool(str(item.get("content") or item.get("content_with_weight") or "")) and isinstance(item.get("positions"), list) for item in chunks if isinstance(item, dict))
        )
        docstore_matches = set(chunk_ids).issubset(set(docstore.get("existing_ids", [])))
        counter_matches = int(listed_data.get("total") or 0) == state.get("chunk_num") and state.get("dataset_chunk_num") == state.get("chunk_num") and state.get("token_num", 0) > 0
        observed = {
            "api_codes": [created["code"], uploaded["code"], parsed["code"], listed["code"]],
            "precondition_matches": precondition_matches,
            "api_readback_matches": api_readback_matches,
            "docstore_matches": docstore_matches,
            "counter_matches": counter_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = chunk_operation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_parse_multi_chunk_document",
                    "api_codes": [created["code"], uploaded["code"], parsed["code"]],
                    "parsing": parsing,
                    "state": state,
                    "precondition_matches": precondition_matches,
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        parsed["raw_sha256"],
                    ],
                },
                {
                    "name": "list_chunks_and_verify_shape_positions_and_counts",
                    "http_status": listed["http_status"],
                    "code": listed["code"],
                    "returned_chunk_count": len(chunks),
                    "total": int(listed_data.get("total") or 0),
                    "api_readback_matches": api_readback_matches,
                    "docstore_matches": docstore_matches,
                    "counter_matches": counter_matches,
                    "raw_sha256": listed["raw_sha256"],
                },
                {"name": "cleanup_document_and_dataset_through_api", **cleanup},
            ],
            "oracle": {
                "minimum_chunks": 2,
                "required_fields": ["content", "positions"],
                "total_equals_document_chunk_num": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-CHUNK-LIST-001",
                    "summary": f"{group} parsed chunk list shape, positions, DocEngine rows, or counters did not agree",
                    "code_location": "api/apps/restful_apis/chunk_api.py:list_chunks",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd049() -> dict[str, Any]:
    case_id = "TC-DD-049"
    prefix = "fresh-dd-049"
    content = "手动添加的测试 chunk 内容"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear manual chunk fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_manual_chunk_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        fixture = _fixture_document(f"{prefix}-parsed.txt")
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_document_before_manual_chunk",
            dataset_id,
            [fixture],
        )
        items = _uploaded_items([uploaded])
        document_id = str(items[0].get("id") or "") if items else ""
        parsed = _parse_documents_rest(
            case_id,
            group,
            owner["auth"],
            "parse_document_before_manual_chunk",
            dataset_id,
            [document_id],
        )
        parsing = _wait_for_document_parsing(group, [document_id], timeout=240)
        before = _document_state_snapshot(group, dataset_id, document_id)
        added = _add_chunk(
            case_id,
            group,
            owner["auth"],
            "add_manual_unicode_chunk",
            dataset_id,
            document_id,
            {
                "content": content,
                "important_keywords": ["manual"],
                "questions": ["这是什么内容？"],
                "tag_kwd": ["manual-test"],
            },
        )
        added_data = added["data"] if isinstance(added["data"], dict) else {}
        chunk_id = str((added_data.get("chunk") or {}).get("id") or "")
        fetched = _get_document_chunk(
            case_id,
            group,
            owner["auth"],
            "get_manual_chunk_after_add",
            dataset_id,
            document_id,
            chunk_id,
        )
        fetched_data = fetched["data"] if isinstance(fetched["data"], dict) else {}
        after = _document_state_snapshot(group, dataset_id, document_id)
        docstore = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, [chunk_id])
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id])
        cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        precondition_matches = parsing.get("terminal") is True and len(parsing.get("documents", [])) == 1 and parsing["documents"][0].get("run") == "3" and before.get("chunk_num", 0) >= 1
        api_readback_matches = (
            added["code"] == 0
            and fetched["code"] == 0
            and fetched_data.get("id") == chunk_id
            and fetched_data.get("content_with_weight") == content
            and fetched_data.get("important_kwd") == ["manual"]
            and fetched_data.get("question_kwd") == ["这是什么内容？"]
            and fetched_data.get("tag_kwd") == ["manual-test"]
        )
        docstore_matches = chunk_id in docstore.get("existing_ids", [])
        counter_matches = (
            after.get("chunk_num") == before.get("chunk_num", 0) + 1
            and after.get("dataset_chunk_num") == before.get("dataset_chunk_num", 0) + 1
            and after.get("token_num", 0) > before.get("token_num", 0)
            and after.get("dataset_token_num", 0) > before.get("dataset_token_num", 0)
        )
        observed = {
            "api_codes": [
                created["code"],
                uploaded["code"],
                parsed["code"],
                added["code"],
                fetched["code"],
            ],
            "precondition_matches": precondition_matches,
            "api_readback_matches": api_readback_matches,
            "docstore_matches": docstore_matches,
            "counter_matches": counter_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = chunk_operation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_parsed_document_precondition",
                    "api_codes": [created["code"], uploaded["code"], parsed["code"]],
                    "parsing": parsing,
                    "before_state": before,
                    "precondition_matches": precondition_matches,
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                        parsed["raw_sha256"],
                    ],
                },
                {
                    "name": "add_and_read_back_manual_chunk",
                    "add_code": added["code"],
                    "get_code": fetched["code"],
                    "chunk_id_present": bool(chunk_id),
                    "api_readback_matches": api_readback_matches,
                    "docstore_matches": docstore_matches,
                    "counter_matches": counter_matches,
                    "after_state": after,
                    "raw_sha256": [added["raw_sha256"], fetched["raw_sha256"]],
                },
                {"name": "cleanup_document_and_dataset_through_api", **cleanup},
            ],
            "oracle": {
                "content": content,
                "document_and_dataset_chunk_delta": 1,
                "docstore_row_required": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-001" if group == "experiment" else "DD-CHUNK-ADD-001",
                    "summary": (
                        "GaussDB DocEngine did not persist the Unicode manual chunk while the add API reported success"
                        if group == "experiment"
                        else "manual chunk API/readback/DocEngine/counters did not agree"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:insert" if group == "experiment" else "api/apps/restful_apis/chunk_api.py:add_chunk",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd050() -> dict[str, Any]:
    case_id = "TC-DD-050"
    prefix = "fresh-dd-050"
    original_content = "original chunk content"
    updated_content = "更新后的 chunk 内容"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear chunk update fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_chunk_update_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        document = _upload_empty_document(
            case_id,
            group,
            owner["auth"],
            "create_document_for_chunk_update",
            dataset_id,
            f"{prefix}-document.txt",
        )
        document_data = document["data"] if isinstance(document["data"], dict) else {}
        document_id = str(document_data.get("id") or "")
        added = _add_chunk(
            case_id,
            group,
            owner["auth"],
            "add_original_ascii_chunk",
            dataset_id,
            document_id,
            {"content": original_content, "important_keywords": ["original"]},
        )
        added_data = added["data"] if isinstance(added["data"], dict) else {}
        chunk_id = str((added_data.get("chunk") or {}).get("id") or "")
        before = _document_state_snapshot(group, dataset_id, document_id)
        fetched_before = _get_document_chunk(
            case_id,
            group,
            owner["auth"],
            "get_original_chunk_before_update",
            dataset_id,
            document_id,
            chunk_id,
        )
        log_offset = _managed_api_log_size(group)
        updated = _update_chunk(
            case_id,
            group,
            owner["auth"],
            "update_chunk_content_and_availability",
            dataset_id,
            document_id,
            chunk_id,
            {
                "content": updated_content,
                "important_keywords": [],
                "available": True,
            },
        )
        fetched_after = _get_document_chunk(
            case_id,
            group,
            owner["auth"],
            "get_chunk_after_update",
            dataset_id,
            document_id,
            chunk_id,
        )
        searched = _request(
            case_id,
            group,
            "retrieve_updated_chunk",
            owner["auth"],
            "POST",
            "/datasets/search",
            payload={
                "question": "chunk",
                "dataset_ids": [dataset_id],
                "doc_ids": [document_id],
                "page": 1,
                "size": 10,
                "top_k": 10,
                "similarity_threshold": 0.0,
                "vector_similarity_weight": 0.3,
            },
            timeout=120,
        )
        log_summary = _managed_api_log_delta_summary(group, log_offset)
        fetched_before_data = fetched_before["data"] if isinstance(fetched_before["data"], dict) else {}
        fetched_after_data = fetched_after["data"] if isinstance(fetched_after["data"], dict) else {}
        search_data = searched["data"] if isinstance(searched["data"], dict) else {}
        search_chunks = search_data.get("chunks") if isinstance(search_data.get("chunks"), list) else []
        matching_search_chunks = [item for item in search_chunks if isinstance(item, dict) and str(item.get("chunk_id") or item.get("id") or "") == chunk_id]
        after = _document_state_snapshot(group, dataset_id, document_id)
        docstore = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, [chunk_id])
        deleted = _delete_ids(case_id, group, owner["auth"], "cleanup_chunk_update_dataset", [dataset_id])
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        precondition_matches = (
            created["code"] == document["code"] == added["code"] == fetched_before["code"] == 0 and fetched_before_data.get("content_with_weight") == original_content and before.get("chunk_num") == 1
        )
        api_readback_matches = (
            updated["code"] == fetched_after["code"] == searched["code"] == 0
            and fetched_after_data.get("content_with_weight") == updated_content
            and fetched_after_data.get("important_kwd") == []
            and fetched_after_data.get("available_int") == 1
            and len(matching_search_chunks) >= 1
            and all(item.get("content_with_weight") == updated_content for item in matching_search_chunks)
        )
        docstore_matches = chunk_id in docstore.get("existing_ids", []) and log_summary.get("docstore_update_error_count") == 0 and log_summary.get("traceback_count") == 0
        counter_matches = (
            after.get("chunk_num") == before.get("chunk_num") == 1
            and after.get("dataset_chunk_num") == before.get("dataset_chunk_num") == 1
            and after.get("token_num") == before.get("token_num")
            and after.get("dataset_token_num") == before.get("dataset_token_num")
        )
        observed = {
            "api_codes": [
                created["code"],
                document["code"],
                added["code"],
                fetched_before["code"],
                updated["code"],
                fetched_after["code"],
                searched["code"],
            ],
            "precondition_matches": precondition_matches,
            "api_readback_matches": api_readback_matches,
            "docstore_matches": docstore_matches,
            "counter_matches": counter_matches,
            "cleanup_succeeded": cleanup,
        }
        passed = chunk_operation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_original_chunk_precondition",
                    "api_codes": [
                        created["code"],
                        document["code"],
                        added["code"],
                        fetched_before["code"],
                    ],
                    "chunk_id_present": bool(chunk_id),
                    "before_state": before,
                    "precondition_matches": precondition_matches,
                    "raw_sha256": [
                        created["raw_sha256"],
                        document["raw_sha256"],
                        added["raw_sha256"],
                        fetched_before["raw_sha256"],
                    ],
                },
                {
                    "name": "update_get_and_retrieve_chunk",
                    "update_code": updated["code"],
                    "get_code": fetched_after["code"],
                    "search_code": searched["code"],
                    "get_content_matches": fetched_after_data.get("content_with_weight") == updated_content,
                    "retrieval_match_count": len(matching_search_chunks),
                    "api_readback_matches": api_readback_matches,
                    "docstore_matches": docstore_matches,
                    "log_delta_summary": log_summary,
                    "raw_sha256": [
                        updated["raw_sha256"],
                        fetched_after["raw_sha256"],
                        searched["raw_sha256"],
                    ],
                },
                {
                    "name": "verify_chunk_counters_unchanged",
                    "before_state": before,
                    "after_state": after,
                    "counter_matches": counter_matches,
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "updated_content": updated_content,
                "available": True,
                "retrieval_readback_required": True,
                "docstore_update_errors": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-UPDATE-001" if group == "experiment" else "DD-CHUNK-UPDATE-001",
                    "summary": (
                        "GaussDB chunk update API reported success but Unicode content was not durably readable/retrievable"
                        if group == "experiment"
                        else "chunk update was not consistently visible through GET, retrieval, logs, and counters"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:update" if group == "experiment" else "api/apps/restful_apis/chunk_api.py:update_chunk",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd051() -> dict[str, Any]:
    case_id = "TC-DD-051"
    prefix = "fresh-dd-051"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if not _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix):
            raise RuntimeError("failed to clear chunk delete fixture")
        created = _create_dataset(case_id, group, owner["auth"], "create_chunk_delete_dataset", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        document = _upload_empty_document(
            case_id,
            group,
            owner["auth"],
            "create_document_for_chunk_delete",
            dataset_id,
            f"{prefix}-document.txt",
        )
        document_data = document["data"] if isinstance(document["data"], dict) else {}
        document_id = str(document_data.get("id") or "")
        added = [
            _add_chunk(
                case_id,
                group,
                owner["auth"],
                f"add_chunk_to_delete_{index}",
                dataset_id,
                document_id,
                {"content": f"fresh chunk delete content {index}"},
            )
            for index in range(1, 4)
        ]
        chunk_ids = [str(((item["data"] or {}).get("chunk") or {}).get("id") or "") if isinstance(item["data"], dict) else "" for item in added]
        before = _document_state_snapshot(group, dataset_id, document_id)
        docstore_before = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, chunk_ids)
        deleted_chunks = _delete_chunks(
            case_id,
            group,
            owner["auth"],
            "delete_first_two_chunks",
            dataset_id,
            document_id,
            chunk_ids[:2],
        )
        listed = _list_document_chunks(
            case_id,
            group,
            owner["auth"],
            "list_chunks_after_delete",
            dataset_id,
            document_id,
        )
        listed_data = listed["data"] if isinstance(listed["data"], dict) else {}
        remaining_chunks = listed_data.get("chunks") if isinstance(listed_data.get("chunks"), list) else []
        remaining_ids = [str(item.get("id") or item.get("chunk_id") or "") for item in remaining_chunks if isinstance(item, dict) and (item.get("id") or item.get("chunk_id"))]
        after = _document_state_snapshot(group, dataset_id, document_id)
        docstore_after = _docstore_chunk_existence(group, owner["tenant_id"], dataset_id, chunk_ids)
        cleaned = _delete_ids(case_id, group, owner["auth"], "cleanup_chunk_delete_dataset", [dataset_id])
        cleanup = cleaned["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        precondition_matches = (
            created["code"] == document["code"] == 0
            and all(item["code"] == 0 for item in added)
            and len(chunk_ids) == 3
            and all(chunk_ids)
            and set(chunk_ids).issubset(set(docstore_before.get("existing_ids", [])))
            and before.get("chunk_num") == 3
            and before.get("dataset_chunk_num") == 3
        )
        api_readback_matches = deleted_chunks["code"] == listed["code"] == 0 and remaining_ids == [chunk_ids[2]] and int(listed_data.get("total") or 0) == 1
        docstore_matches = set(docstore_after.get("existing_ids", [])) == {chunk_ids[2]}
        counter_matches = (
            after.get("chunk_num") == 1
            and after.get("dataset_chunk_num") == 1
            and after.get("token_num", 0) < before.get("token_num", 0)
            and after.get("dataset_token_num", 0) < before.get("dataset_token_num", 0)
        )
        observed = {
            "api_codes": [
                created["code"],
                document["code"],
                *[item["code"] for item in added],
                deleted_chunks["code"],
                listed["code"],
            ],
            "precondition_matches": precondition_matches,
            "api_readback_matches": api_readback_matches,
            "docstore_matches": docstore_matches,
            "counter_matches": counter_matches,
            "cleanup_succeeded": cleanup,
        }
        passed = chunk_operation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_chunk_precondition",
                    "api_codes": [
                        created["code"],
                        document["code"],
                        *[item["code"] for item in added],
                    ],
                    "chunk_ids_present": len(chunk_ids) == 3 and all(chunk_ids),
                    "before_state": before,
                    "docstore_before": docstore_before,
                    "precondition_matches": precondition_matches,
                    "raw_sha256": [
                        created["raw_sha256"],
                        document["raw_sha256"],
                        *[item["raw_sha256"] for item in added],
                    ],
                },
                {
                    "name": "delete_two_chunks_and_verify_one_remains",
                    "delete_code": deleted_chunks["code"],
                    "list_code": listed["code"],
                    "remaining_ids": remaining_ids,
                    "expected_remaining_id": chunk_ids[2] if len(chunk_ids) == 3 else None,
                    "api_readback_matches": api_readback_matches,
                    "docstore_after": docstore_after,
                    "docstore_matches": docstore_matches,
                    "after_state": after,
                    "counter_matches": counter_matches,
                    "raw_sha256": [
                        deleted_chunks["raw_sha256"],
                        listed["raw_sha256"],
                    ],
                },
                {
                    "name": "cleanup_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": cleaned["raw_sha256"],
                },
            ],
            "oracle": {
                "before_chunks": 3,
                "deleted_chunks": 2,
                "remaining_chunks": 1,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-CHUNK-DELETE-001",
                    "summary": f"{group} chunk delete did not remove exactly two rows and decrement both counters",
                    "code_location": "api/apps/restful_apis/chunk_api.py:delete_chunks",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_retrieval_case(
    case_id: str,
    prefix: str,
    contents: list[str],
    question: str,
    *,
    top_k: int,
    similarity_threshold: float | None = None,
    vector_similarity_weight: float | None = None,
    within_dataset: bool = False,
    baseline_for_threshold: bool = False,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_retrieval_fixtures(case_id, group, owner, prefix, contents)
        payload: dict[str, Any] = {"question": question, "top_k": top_k}
        if not within_dataset:
            payload["dataset_ids"] = fixture["dataset_ids"]
        if similarity_threshold is not None:
            payload["similarity_threshold"] = similarity_threshold
        if vector_similarity_weight is not None:
            payload["vector_similarity_weight"] = vector_similarity_weight
        endpoint = f"/datasets/{fixture['dataset_ids'][0]}/search" if within_dataset and fixture["dataset_ids"] else "/retrieval"
        retrieved = _request(
            case_id,
            group,
            "execute_retrieval",
            owner["auth"],
            "POST",
            endpoint,
            payload=payload,
            timeout=180,
        )
        chunks = _normalize_retrieval_chunks(retrieved)
        baseline = None
        baseline_chunks = chunks
        if baseline_for_threshold:
            baseline_payload = dict(payload)
            baseline_payload["similarity_threshold"] = 0.0
            baseline = _request(
                case_id,
                group,
                "execute_retrieval_baseline_threshold_zero",
                owner["auth"],
                "POST",
                endpoint,
                payload=baseline_payload,
                timeout=180,
            )
            baseline_chunks = _normalize_retrieval_chunks(baseline)
        coverage_chunks = baseline_chunks if baseline_for_threshold else chunks
        selected_sources = set(fixture["dataset_ids"])
        expected_sources = {fixture["dataset_ids"][0]} if within_dataset and fixture["dataset_ids"] else selected_sources
        actual_sources = {item["dataset_id"] for item in coverage_chunks if item["dataset_id"]}
        filtered_sources = {item["dataset_id"] for item in chunks if item["dataset_id"]}
        coverage_similarities = [item["similarity"] for item in coverage_chunks if item["similarity"] is not None]
        similarities = [item["similarity"] for item in chunks if item["similarity"] is not None]
        similarity_nonincreasing = (
            len(coverage_similarities) == len(coverage_chunks)
            and all(left >= right for left, right in zip(coverage_similarities, coverage_similarities[1:]))
            and len(similarities) == len(chunks)
            and all(left >= right for left, right in zip(similarities, similarities[1:]))
        )
        threshold_satisfied = (
            True
            if similarity_threshold is None
            else threshold_filter_contract_ok(
                baseline_ids=[item["id"] for item in baseline_chunks],
                filtered_chunks=chunks,
                threshold=similarity_threshold,
            )
            if baseline_for_threshold
            else bool(chunks) and all(item["similarity"] is not None and item["similarity"] >= similarity_threshold for item in chunks)
        )
        chunk_shape_matches = (
            bool(coverage_chunks)
            and all(
                item["id"] and item["content"] and item["document_id"] and item["dataset_id"] and item["similarity"] is not None and isinstance(item["positions"], list) for item in coverage_chunks
            )
            and all(item["id"] and item["content"] and item["document_id"] and item["dataset_id"] and item["similarity"] is not None and isinstance(item["positions"], list) for item in chunks)
        )
        cleanup = _cleanup_retrieval_fixtures(case_id, group, owner["auth"], fixture)
        observed = {
            "api_codes": [
                *fixture["api_codes"],
                retrieved["code"],
                *([baseline["code"]] if baseline is not None else []),
            ],
            "fixtures_ready": fixture["fixtures_ready"],
            "result_count_positive": bool(coverage_chunks),
            "expected_sources_present": expected_sources.issubset(actual_sources),
            "only_selected_sources": (actual_sources | filtered_sources).issubset(expected_sources),
            "similarity_nonincreasing": similarity_nonincreasing,
            "threshold_satisfied": threshold_satisfied,
            "chunk_shape_matches": chunk_shape_matches,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = retrieval_contract_ok(observed)
        result_data = retrieved["data"] if isinstance(retrieved["data"], dict) else {}
        baseline_data = baseline["data"] if baseline is not None and isinstance(baseline["data"], dict) else {}
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_and_parse_retrieval_fixtures",
                    "dataset_count": len(fixture["dataset_ids"]),
                    "document_count": len(fixture["document_ids"]),
                    "api_codes": fixture["api_codes"],
                    "parsing": fixture["parsing"],
                    "chunk_counts": [len(ids) for ids in fixture["chunk_ids_by_dataset"]],
                    "states": fixture["states"],
                    "fixtures_ready": fixture["fixtures_ready"],
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "execute_and_verify_retrieval",
                    "endpoint": endpoint,
                    "http_status": retrieved["http_status"],
                    "code": retrieved["code"],
                    "message": retrieved["message"],
                    "reported_total": result_data.get("total"),
                    "returned_count": len(chunks),
                    "baseline_requested": baseline_for_threshold,
                    "baseline_http_status": (baseline["http_status"] if baseline is not None else None),
                    "baseline_code": baseline["code"] if baseline is not None else None,
                    "baseline_message": (baseline["message"] if baseline is not None else None),
                    "baseline_reported_total": baseline_data.get("total"),
                    "baseline_returned_count": len(baseline_chunks),
                    "expected_source_count": len(expected_sources),
                    "actual_source_count": len(actual_sources),
                    "filtered_source_count": len(filtered_sources),
                    "expected_sources_present": observed["expected_sources_present"],
                    "only_selected_sources": observed["only_selected_sources"],
                    "baseline_similarities": coverage_similarities,
                    "similarities": similarities,
                    "similarity_nonincreasing": similarity_nonincreasing,
                    "threshold_satisfied": threshold_satisfied,
                    "chunk_shape_matches": chunk_shape_matches,
                    "raw_sha256": {
                        "filtered": retrieved["raw_sha256"],
                        "baseline": (baseline["raw_sha256"] if baseline is not None else None),
                    },
                },
                {
                    "name": "cleanup_retrieval_fixtures_through_api",
                    "cleanup_succeeded": cleanup["succeeded"],
                    "results": cleanup["results"],
                },
            ],
            "oracle": {
                "question": question,
                "top_k": top_k,
                "similarity_threshold": similarity_threshold,
                "vector_similarity_weight": vector_similarity_weight,
                "selected_dataset_count": len(expected_sources),
                "similarity_order": "nonincreasing",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-GAUSS-UNICODE-RETRIEVAL-001" if group == "experiment" else "DD-RETRIEVAL-001",
                    "summary": (
                        "GaussDB DocEngine could not establish/read the Chinese retrieval fixture under SQL_ASCII"
                        if group == "experiment"
                        else "retrieval results did not satisfy source, sorting, threshold, or shape requirements"
                    ),
                    "code_location": "rag/utils/gaussdb_conn.py:insert,search" if group == "experiment" else "api/apps/restful_apis/chunk_api.py:retrieval_test",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd052() -> dict[str, Any]:
    return _run_retrieval_case(
        "TC-DD-052",
        "fresh-dd-052",
        ["测试检索问题 测试检索问题 测试检索问题。答案来自基础检索文档。"],
        "测试检索问题",
        top_k=5,
        similarity_threshold=0.5,
    )


def run_dd053() -> dict[str, Any]:
    return _run_retrieval_case(
        "TC-DD-053",
        "fresh-dd-053",
        [f"跨数据集检索问题 跨数据集检索问题。答案来自数据集 {index}。" for index in range(1, 4)],
        "跨数据集检索问题",
        top_k=10,
    )


def run_dd054() -> dict[str, Any]:
    return _run_retrieval_case(
        "TC-DD-054",
        "fresh-dd-054",
        ["测试问题 测试问题 测试问题。该文档用于阈值和向量权重验证。"],
        "测试问题",
        top_k=10,
        similarity_threshold=0.7,
        vector_similarity_weight=0.6,
        baseline_for_threshold=True,
    )


def run_dd055() -> dict[str, Any]:
    return _run_retrieval_case(
        "TC-DD-055",
        "fresh-dd-055",
        ["数据集内检索问题 数据集内检索问题。答案只属于当前数据集。"],
        "数据集内检索问题",
        top_k=5,
        within_dataset=True,
    )


def _thumbnail_value_summary(value: Any) -> dict[str, Any]:
    if value is None:
        return {"kind": "null", "length": 0, "sha256": None}
    if not isinstance(value, str):
        return {"kind": type(value).__name__, "length": None, "sha256": None}
    if not value:
        kind = "empty"
    elif value.startswith("data:image/"):
        kind = "base64_image"
    elif value.startswith("/api/v1/documents/images/"):
        kind = "image_url"
    else:
        kind = "unexpected_string"
    return {
        "kind": kind,
        "length": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def _run_thumbnail_case(
    case_id: str,
    prefix: str,
    filename: str,
    *,
    parse_required: bool,
    expected_empty: bool,
) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_thumbnail_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        document = _fixture_document(filename)
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_thumbnail_document",
            dataset_id,
            [document],
        )
        uploaded_items = _uploaded_items([uploaded])
        document_id = str(uploaded_items[0].get("id") or "") if len(uploaded_items) == 1 else ""
        graph = _uploaded_document_graph_snapshot(group, dataset_id, [document_id] if document_id else [])
        parse_response = None
        parsing = {"terminal": True, "timed_out": False, "documents": []}
        final_state: dict[str, Any] = {}
        if parse_required and document_id:
            parse_response = _parse_documents(
                case_id,
                group,
                owner["auth"],
                "parse_thumbnail_document",
                dataset_id,
                [document_id],
            )
            parsing = _wait_for_document_parsing(group, [document_id], timeout=180)
            final_state = _document_state_snapshot(group, dataset_id, document_id)
        thumbnails = _request(
            case_id,
            group,
            "get_document_thumbnail",
            owner["auth"],
            "GET",
            "/thumbnails",
            params={"doc_ids": [document_id]},
        )
        mapping = thumbnails["data"] if isinstance(thumbnails["data"], dict) else {}
        value = mapping.get(document_id) if document_id in mapping else None
        value_summary = _thumbnail_value_summary(value)
        thumbnail_value_valid = value_summary["kind"] in {
            "null",
            "empty",
            "base64_image",
            "image_url",
        }
        expected_empty_satisfied = not expected_empty or value is None or value == ""
        response_mapping_exact = bool(document_id) and set(mapping) == {document_id}
        document_fixture_ready = (
            created["code"] == uploaded["code"] == 0
            and bool(dataset_id)
            and bool(document_id)
            and len(graph.get("documents", [])) == 1
            and len(graph.get("files", [])) == 1
            and graph["documents"][0]["name"] == filename
        )
        parse_requirement_satisfied = not parse_required or (parse_response is not None and parse_response["code"] == 0 and parsing.get("terminal") is True and final_state.get("run") == "3")
        if graph.get("documents"):
            cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        else:
            deleted = (
                _delete_ids(
                    case_id,
                    group,
                    owner["auth"],
                    "cleanup_thumbnail_dataset_without_document",
                    [dataset_id],
                )
                if dataset_id
                else None
            )
            cleanup = {
                "succeeded": bool(deleted and deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0),
                "raw_sha256": [deleted["raw_sha256"] if deleted else None],
            }
        api_codes = [created["code"], uploaded["code"], thumbnails["code"]]
        if parse_response is not None:
            api_codes.insert(2, parse_response["code"])
        observed = {
            "api_codes": api_codes,
            "precondition_clean": precondition_clean,
            "document_fixture_ready": document_fixture_ready,
            "parse_requirement_satisfied": parse_requirement_satisfied,
            "response_mapping_exact": response_mapping_exact,
            "thumbnail_value_valid": thumbnail_value_valid,
            "expected_empty_satisfied": expected_empty_satisfied,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = thumbnail_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_thumbnail_document_fixture",
                    "precondition_clean": precondition_clean,
                    "dataset_code": created["code"],
                    "upload_code": uploaded["code"],
                    "document_fixture_ready": document_fixture_ready,
                    "document_type": Path(filename).suffix.lower().lstrip("."),
                    "raw_sha256": [created["raw_sha256"], uploaded["raw_sha256"]],
                },
                {
                    "name": "parse_thumbnail_document_if_required",
                    "parse_required": parse_required,
                    "parse_code": (parse_response["code"] if parse_response is not None else None),
                    "parsing_terminal": parsing.get("terminal"),
                    "final_run": final_state.get("run"),
                    "parse_requirement_satisfied": parse_requirement_satisfied,
                    "raw_sha256": (parse_response["raw_sha256"] if parse_response is not None else None),
                },
                {
                    "name": "get_and_verify_thumbnail_mapping",
                    "http_status": thumbnails["http_status"],
                    "code": thumbnails["code"],
                    "mapping_count": len(mapping),
                    "response_mapping_exact": response_mapping_exact,
                    "thumbnail": value_summary,
                    "thumbnail_value_valid": thumbnail_value_valid,
                    "expected_empty_satisfied": expected_empty_satisfied,
                    "raw_sha256": thumbnails["raw_sha256"],
                },
                {
                    "name": "cleanup_thumbnail_fixture_through_api",
                    "cleanup_succeeded": cleanup["succeeded"],
                    "raw_sha256": cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "response_shape": {"document_id": "thumbnail"},
                "accepted_thumbnail_kinds": [
                    "null",
                    "empty",
                    "base64_image",
                    "image_url",
                ],
                "expected_empty": expected_empty,
                "parse_required": parse_required,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-THUMBNAIL-001",
                    "summary": f"{group} thumbnail mapping did not satisfy the document contract",
                    "code_location": "api/apps/restful_apis/document_api.py:list_thumbnails",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd056() -> dict[str, Any]:
    return _run_thumbnail_case(
        "TC-DD-056",
        "fresh-dd-056",
        "fresh-dd-056.pdf",
        parse_required=True,
        expected_empty=False,
    )


def run_dd057() -> dict[str, Any]:
    return _run_thumbnail_case(
        "TC-DD-057",
        "fresh-dd-057",
        "fresh-dd-057.txt",
        parse_required=False,
        expected_empty=True,
    )


def run_dd058() -> dict[str, Any]:
    case_id = "TC-DD-058"
    prefix = "fresh-dd-058"
    secondary_email = "dd-058-user-b@fresh.invalid"
    secondary_password = "DD-058-Fresh-User-B@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        owner_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        secondary_preclean = True
        if DB._email_count(group, [secondary_email]):
            secondary_preclean = DB._delete_user_via_admin(group, secondary_email)
        registration = DB._register(
            case_id,
            group,
            "register_private_dataset_user_b",
            {
                "email": secondary_email,
                "nickname": "FreshDD058UserB",
                "password": secondary_password,
            },
        )
        secondary_login = AUTH._login(
            case_id,
            group,
            "login_private_dataset_user_b",
            secondary_email,
            secondary_password,
        )
        secondary_auth = str(secondary_login.get("_auth") or "")
        secondary_tenant_id = _owner_id(group, secondary_email) if DB._email_count(group, [secondary_email]) else ""
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_user_a_private_dataset",
            {"name": prefix, "permission": "me"},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        snapshot = _dataset_snapshot(group, dataset_id)
        denied = _get_dataset(
            case_id,
            group,
            secondary_auth,
            "user_b_get_user_a_private_dataset",
            dataset_id,
        )
        expected_message = f"User '{secondary_tenant_id}' lacks permission for dataset '{dataset_id}'"
        deleted_dataset = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_user_a_private_dataset",
                [dataset_id],
            )
            if dataset_id
            else None
        )
        if DB._email_count(group, [secondary_email]):
            user_cleanup = DB._disable_and_delete_user(case_id, group, secondary_email)
            user_cleanup_succeeded = user_cleanup["disabled"]["code"] == 0 and user_cleanup["deleted"]["code"] == 0
        else:
            user_cleanup = {
                "disabled": {"code": None, "raw_sha256": None},
                "deleted": {"code": None, "raw_sha256": None},
            }
            user_cleanup_succeeded = False
        cleanup_succeeded = (
            bool(deleted_dataset)
            and deleted_dataset["code"] == 0
            and _dataset_snapshot(group, dataset_id).get("count") == 0
            and user_cleanup_succeeded
            and DB._email_count(group, [secondary_email]) == 0
        )
        observed = {
            "api_codes": [
                registration["code"],
                secondary_login["code"],
                created["code"],
            ],
            "precondition_clean": owner_clean and secondary_preclean,
            "tenant_ids_distinct": (bool(secondary_tenant_id) and owner["tenant_id"] != secondary_tenant_id),
            "private_dataset_ready": (snapshot.get("count") == 1 and snapshot.get("tenant_id") == owner["tenant_id"] and snapshot.get("permission") == "me"),
            "denial_http_status": denied["http_status"],
            "denial_code": denied["code"],
            "denial_message_matches": denied["message"] == expected_message,
            "data_not_leaked": denied["data"] is None,
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = private_dataset_denial_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_distinct_user_b_and_user_a_private_dataset",
                    "precondition_clean": observed["precondition_clean"],
                    "registration_code": registration["code"],
                    "secondary_login_code": secondary_login["code"],
                    "dataset_create_code": created["code"],
                    "tenant_ids_distinct": observed["tenant_ids_distinct"],
                    "private_dataset_ready": observed["private_dataset_ready"],
                    "raw_sha256": [
                        registration["raw_sha256"],
                        secondary_login["raw_sha256"],
                        created["raw_sha256"],
                    ],
                },
                {
                    "name": "deny_user_b_private_dataset_access_without_leak",
                    "http_status": denied["http_status"],
                    "code": denied["code"],
                    "message_matches": observed["denial_message_matches"],
                    "data_not_leaked": observed["data_not_leaked"],
                    "raw_sha256": denied["raw_sha256"],
                },
                {
                    "name": "cleanup_private_dataset_and_user_b_through_apis",
                    "dataset_delete_code": (deleted_dataset["code"] if deleted_dataset else None),
                    "user_disable_code": user_cleanup["disabled"]["code"],
                    "user_delete_code": user_cleanup["deleted"]["code"],
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": [
                        deleted_dataset["raw_sha256"] if deleted_dataset else None,
                        user_cleanup["disabled"]["raw_sha256"],
                        user_cleanup["deleted"]["raw_sha256"],
                    ],
                },
            ],
            "oracle": {
                "denial": {"http_status": 200, "code": 102},
                "message_template": "User '<tenant_b>' lacks permission for dataset '<dataset_id>'",
                "response_data": None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DATASET-ACL-001",
                    "summary": f"{group} private dataset access denial did not satisfy the cross-tenant contract",
                    "code_location": "api/apps/services/dataset_api_service.py:get_dataset",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd059() -> dict[str, Any]:
    case_id = "TC-DD-059"
    invalid_credential = "fresh-dd-059-invalid-token"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        response = _request(
            case_id,
            group,
            "list_datasets_with_invalid_token",
            invalid_credential,
            "GET",
            "/datasets",
        )
        observed = {
            "http_status": response["http_status"],
            "code": response["code"],
            "unauthorized_message": "unauthorized" in str(response["message"] or "").lower(),
            "data_not_leaked": response["data"] is None,
        }
        passed = invalid_token_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "reject_invalid_token_for_dataset_list",
                    **observed,
                    "credential_fingerprint": hashlib.sha256(invalid_credential.encode("utf-8")).hexdigest()[:12],
                    "raw_sha256": response["raw_sha256"],
                }
            ],
            "oracle": {
                "http_status": 401,
                "code": 401,
                "message_contains": "Unauthorized",
                "data": None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-AUTH-INVALID-TOKEN-001",
                    "summary": f"{group} dataset list did not reject an invalid token with HTTP/code 401",
                    "code_location": "api/apps/__init__.py:login_required",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd060() -> dict[str, Any]:
    case_id = "TC-DD-060"
    prefix = "fresh-dd-060"
    updated_description = "fresh-dd-060-concurrent-update"
    max_workers = 2
    group_timeout_seconds = 180.0

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_concurrency_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        documents = [
            (
                f"{prefix}-{index}.txt",
                f"fresh concurrent document {index}\n".encode("ascii"),
                "text/plain",
            )
            for index in range(1, 6)
        ]

        def upload(index: int, document: tuple[str, bytes, str]) -> dict[str, Any]:
            return _request(
                case_id,
                group,
                f"concurrent_upload_{index}",
                owner["auth"],
                "POST",
                f"/datasets/{dataset_id}/documents",
                files=[("file", document)],
                timeout=60,
            )

        def update() -> dict[str, Any]:
            return _update_dataset(
                case_id,
                group,
                owner["auth"],
                "concurrent_update_dataset",
                dataset_id,
                {"description": updated_description},
            )

        def list_dataset() -> dict[str, Any]:
            return _list_datasets(
                case_id,
                group,
                owner["auth"],
                "concurrent_list_dataset",
                {"id": dataset_id},
            )

        task_specs: list[tuple[str, Callable[[], dict[str, Any]]]] = [
            ("upload_1", lambda: upload(1, documents[0])),
            ("update", update),
            ("upload_2", lambda: upload(2, documents[1])),
            ("list", list_dataset),
            ("upload_3", lambda: upload(3, documents[2])),
            ("upload_4", lambda: upload(4, documents[3])),
            ("upload_5", lambda: upload(5, documents[4])),
        ]
        resource_samples = [_api_resource_snapshot(group)]
        task_results: dict[str, dict[str, Any]] = {}
        task_errors: dict[str, dict[str, str]] = {}
        timed_out = False
        group_started = time.monotonic()
        executor = ThreadPoolExecutor(max_workers=max_workers)
        futures = {executor.submit(task): name for name, task in task_specs}
        try:
            for future in as_completed(futures, timeout=group_timeout_seconds):
                name = futures[future]
                try:
                    task_results[name] = future.result()
                except Exception as error:
                    task_errors[name] = {
                        "type": type(error).__name__,
                        "message": str(error)[:200],
                    }
                resource_samples.append(_api_resource_snapshot(group))
        except FuturesTimeoutError:
            timed_out = True
            for future in futures:
                future.cancel()
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
        group_elapsed = time.monotonic() - group_started
        resource_samples.append(_api_resource_snapshot(group))
        watermark = _resource_watermark(resource_samples)

        upload_results = [task_results[name] for name in sorted(task_results) if name.startswith("upload_")]
        uploaded_items = _uploaded_items(upload_results)
        response_document_ids = sorted(str(item.get("id") or "") for item in uploaded_items if item.get("id"))
        database_document_ids = sorted(_document_ids_for_dataset(group, dataset_id))
        graph = _uploaded_document_graph_snapshot(group, dataset_id, database_document_ids)
        snapshot = _dataset_snapshot(group, dataset_id)
        list_response = task_results.get("list", {})
        listed_rows = list_response.get("data") if isinstance(list_response.get("data"), list) else []
        list_observed_dataset = any(isinstance(item, dict) and str(item.get("id") or "") == dataset_id for item in listed_rows)
        expected_names = sorted(document[0] for document in documents)
        graph_names = sorted(item["name"] for item in graph.get("documents", []))
        document_graph_consistent = (
            response_document_ids == database_document_ids
            and len(graph.get("documents", [])) == 5
            and len(graph.get("files", [])) == 5
            and int(graph.get("file_link_count", 0)) == 5
            and graph_names == expected_names
            and len(set(database_document_ids)) == 5
            and all(item["dataset_id"] == dataset_id for item in graph.get("documents", []))
        )
        all_tasks_completed = not timed_out and len(task_results) + len(task_errors) == len(task_specs)
        all_requests_succeeded = all_tasks_completed and not task_errors and all(result.get("http_status") == 200 and result.get("code") == 0 for result in task_results.values())
        resource_watermark_observed = (
            watermark["live_sample_count"] == watermark["sample_count"]
            and watermark["sample_count"] >= 2
            and watermark["pid_consistent"] is True
            and isinstance(watermark["max_rss_kib"], int)
            and watermark["max_rss_kib"] > 0
            and isinstance(watermark["max_thread_count"], int)
            and watermark["max_thread_count"] > 0
            and isinstance(watermark["max_fd_count"], int)
            and watermark["max_fd_count"] > 0
        )
        if graph.get("documents"):
            cleanup = _cleanup_uploaded_dataset(case_id, group, owner["auth"], dataset_id, graph)
        else:
            deleted = (
                _delete_ids(
                    case_id,
                    group,
                    owner["auth"],
                    "cleanup_concurrency_dataset_without_documents",
                    [dataset_id],
                )
                if dataset_id
                else None
            )
            cleanup = {
                "succeeded": bool(deleted and deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0),
                "raw_sha256": [deleted["raw_sha256"] if deleted else None],
            }
        observed = {
            "setup_code": created["code"],
            "precondition_clean": precondition_clean,
            "max_workers": max_workers,
            "group_within_timeout": group_elapsed <= group_timeout_seconds,
            "all_tasks_completed": all_tasks_completed,
            "all_requests_succeeded": all_requests_succeeded,
            "upload_count": len(uploaded_items),
            "database_document_count": len(database_document_ids),
            "document_graph_consistent": document_graph_consistent,
            "update_persisted": snapshot.get("description") == updated_description,
            "list_observed_dataset": list_observed_dataset,
            "resource_watermark_observed": resource_watermark_observed,
            "cleanup_succeeded": cleanup["succeeded"],
        }
        passed = concurrency_contract_ok(observed)
        task_summaries = {
            name: {
                "http_status": result.get("http_status"),
                "code": result.get("code"),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "raw_sha256": result.get("raw_sha256"),
            }
            for name, result in sorted(task_results.items())
        }
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_concurrency_fixture",
                    "precondition_clean": precondition_clean,
                    "dataset_create_code": created["code"],
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "execute_bounded_concurrent_operations",
                    "max_workers": max_workers,
                    "request_timeout_seconds": 60,
                    "group_timeout_seconds": group_timeout_seconds,
                    "group_elapsed_seconds": group_elapsed,
                    "timed_out": timed_out,
                    "all_tasks_completed": all_tasks_completed,
                    "all_requests_succeeded": all_requests_succeeded,
                    "task_results": task_summaries,
                    "task_errors": task_errors,
                    "resource_watermark": watermark,
                    "resource_watermark_observed": resource_watermark_observed,
                },
                {
                    "name": "verify_concurrent_database_consistency",
                    "upload_response_count": len(uploaded_items),
                    "database_document_count": len(database_document_ids),
                    "file_count": len(graph.get("files", [])),
                    "file_link_count": graph.get("file_link_count"),
                    "document_graph_consistent": document_graph_consistent,
                    "update_persisted": observed["update_persisted"],
                    "list_observed_dataset": list_observed_dataset,
                },
                {
                    "name": "cleanup_concurrency_fixture_through_api",
                    "cleanup_succeeded": cleanup["succeeded"],
                    "raw_sha256": cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "max_workers": 2,
                "request_timeout_seconds": 60,
                "group_timeout_seconds": 180,
                "uploads": 5,
                "updates": 1,
                "lists": 1,
                "database_documents": 5,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-CONCURRENCY-001",
                    "summary": f"{group} bounded concurrent operations did not preserve the dataset/document graph",
                    "code_location": "api/apps/restful_apis/document_api.py:upload_document",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_dd061() -> dict[str, Any]:
    case_id = "TC-DD-061"
    prefix = "fresh-dd-061"
    cold_samples = 5
    warmup_samples = 2
    hot_samples = 10

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_performance_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        document_responses = [
            _upload_empty_document(
                case_id,
                group,
                owner["auth"],
                f"create_performance_document_{index:03d}",
                dataset_id,
                f"{prefix}-{index:03d}.txt",
            )
            for index in range(1, 101)
        ]
        document_ids = [str(response["data"].get("id") or "") if isinstance(response["data"], dict) else "" for response in document_responses]
        selected_document_ids = document_ids[:10]
        chunk_responses: list[dict[str, Any]] = []
        for document_index, document_id in enumerate(selected_document_ids, 1):
            for chunk_index in range(1, 11):
                chunk_responses.append(
                    _add_chunk(
                        case_id,
                        group,
                        owner["auth"],
                        f"add_performance_chunk_{document_index:02d}_{chunk_index:02d}",
                        dataset_id,
                        document_id,
                        {
                            "content": (f"performance baseline common query document {document_index:02d} chunk {chunk_index:02d} stable ascii fixture"),
                            "important_keywords": [
                                "performance",
                                "baseline",
                                f"document-{document_index:02d}",
                            ],
                        },
                    )
                )
        chunk_ids = []
        for response in chunk_responses:
            data = response["data"] if isinstance(response["data"], dict) else {}
            chunk = data.get("chunk") if isinstance(data.get("chunk"), dict) else {}
            chunk_id = str(chunk.get("id") or "")
            if chunk_id:
                chunk_ids.append(chunk_id)

        database_document_ids = _document_ids_for_dataset(group, dataset_id)
        dataset_snapshot = _dataset_snapshot(group, dataset_id)
        selected_states = [_document_state_snapshot(group, dataset_id, document_id) for document_id in selected_document_ids if document_id]
        docstore_before = _docstore_chunk_batch_snapshot(
            group,
            owner["tenant_id"],
            dataset_id,
            chunk_ids,
        )
        fixture_api_codes = [
            created["code"],
            *[response["code"] for response in document_responses],
            *[response["code"] for response in chunk_responses],
        ]
        selected_document_counts_match = (
            len(selected_states) == 10 and all(state.get("chunk_num") == 10 for state in selected_states) and dataset_snapshot.get("doc_num") == 100 and dataset_snapshot.get("chunk_num") == 100
        )

        def dataset_list_request(label: str) -> dict[str, Any]:
            return _list_datasets(
                case_id,
                group,
                owner["auth"],
                f"performance_dataset_list_{label}",
                {"id": dataset_id},
            )

        def document_list_request(label: str) -> dict[str, Any]:
            return _list_documents(
                case_id,
                group,
                owner["auth"],
                f"performance_document_list_{label}",
                dataset_id,
                {
                    "page": 1,
                    "page_size": 100,
                    "orderby": "name",
                    "desc": "false",
                },
            )

        def retrieval_request(label: str) -> dict[str, Any]:
            return _request(
                case_id,
                group,
                f"performance_retrieval_{label}",
                owner["auth"],
                "POST",
                "/retrieval",
                payload={
                    "question": "performance baseline common query",
                    "dataset_ids": [dataset_id],
                    "page_size": 20,
                    "top_k": 20,
                    "similarity_threshold": 0.0,
                    "vector_similarity_weight": 0.5,
                },
                timeout=180,
            )

        measurements = {
            "dataset_list": _measure_operation(
                dataset_list_request,
                cold_samples=cold_samples,
                warmup_samples=warmup_samples,
                hot_samples=hot_samples,
            ),
            "document_list": _measure_operation(
                document_list_request,
                cold_samples=cold_samples,
                warmup_samples=warmup_samples,
                hot_samples=hot_samples,
            ),
            "retrieval": _measure_operation(
                retrieval_request,
                cold_samples=cold_samples,
                warmup_samples=warmup_samples,
                hot_samples=hot_samples,
            ),
        }

        def operation_response_matches(operation: str, response: dict[str, Any]) -> bool:
            if response.get("http_status") != 200 or response.get("code") != 0:
                return False
            if operation == "dataset_list":
                rows = response.get("data")
                return isinstance(rows, list) and len(rows) == 1 and str(rows[0].get("id") or "") == dataset_id
            if operation == "document_list":
                rows, total = _document_list_payload(response)
                return total == 100 and len(rows) == 100 and {str(item.get("id") or "") for item in rows if item.get("id")} == set(database_document_ids)
            chunks = _normalize_retrieval_chunks(response)
            return bool(chunks) and len(chunks) <= 20 and all(item["dataset_id"] == dataset_id for item in chunks) and all(item["similarity"] is not None for item in chunks)

        metrics: dict[str, Any] = {}
        all_measurement_responses: list[dict[str, Any]] = []
        all_operation_contracts_match = True
        for operation, phases in measurements.items():
            metrics[operation] = {}
            for phase, responses in phases.items():
                all_measurement_responses.extend(responses)
                phase_contract = all(operation_response_matches(operation, response) for response in responses)
                all_operation_contracts_match = all_operation_contracts_match and phase_contract
                metrics[operation][phase] = {
                    **latency_summary([float(response["elapsed_seconds"]) for response in responses]),
                    "contract_matches": phase_contract,
                    "statuses": [[response["http_status"], response["code"]] for response in responses],
                    "raw_sha256": [response["raw_sha256"] for response in responses],
                }
        measurement_shape_matches = all(len(phases["cold"]) == cold_samples and len(phases["warmup"]) == warmup_samples and len(phases["hot"]) == hot_samples for phases in measurements.values())
        all_measurement_requests_succeeded = all(response["http_status"] == 200 and response["code"] == 0 for response in all_measurement_responses)
        all_latencies_positive = all(float(response["elapsed_seconds"]) > 0 for response in all_measurement_responses)

        cascade_before = _cascade_resource_snapshot(group, dataset_id, database_document_ids)
        deleted_documents = (
            _delete_documents(
                case_id,
                group,
                owner["auth"],
                "cleanup_performance_documents",
                dataset_id,
                database_document_ids,
            )
            if database_document_ids
            else None
        )
        deleted_dataset = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_performance_dataset",
                [dataset_id],
            )
            if dataset_id
            else None
        )
        docstore_after = _docstore_chunk_batch_snapshot(
            group,
            owner["tenant_id"],
            dataset_id,
            chunk_ids,
        )
        cascade_after = _saved_cascade_resource_counts(
            group,
            dataset_id,
            cascade_before["document_ids"],
            cascade_before["task_ids"],
            cascade_before["file_ids"],
            cascade_before["file_link_ids"],
        )
        cleanup_succeeded = (
            bool(deleted_documents)
            and deleted_documents["code"] == 0
            and docstore_dataset_cleanup_contract_ok(group, docstore_after)
            and bool(deleted_dataset)
            and deleted_dataset["code"] == 0
            and all(value == 0 for value in cascade_after.values())
        )
        observed = {
            "fixture_api_codes_all_zero": (bool(fixture_api_codes) and all(code == 0 for code in fixture_api_codes) and precondition_clean),
            "database_document_count": len(database_document_ids),
            "database_chunk_count": int(dataset_snapshot.get("chunk_num") or 0),
            "docstore_chunk_count": len(docstore_before.get("existing_ids", [])),
            "selected_document_counts_match": selected_document_counts_match,
            "measurement_shape_matches": measurement_shape_matches,
            "all_measurement_requests_succeeded": all_measurement_requests_succeeded,
            "all_operation_contracts_match": all_operation_contracts_match,
            "all_latencies_positive": all_latencies_positive,
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = performance_baseline_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_medium_performance_fixture_through_api",
                    "precondition_clean": precondition_clean,
                    "dataset_create_code": created["code"],
                    "document_request_count": len(document_responses),
                    "document_success_count": sum(response["code"] == 0 for response in document_responses),
                    "chunk_request_count": len(chunk_responses),
                    "chunk_success_count": sum(response["code"] == 0 for response in chunk_responses),
                    "database_document_count": len(database_document_ids),
                    "database_chunk_count": dataset_snapshot.get("chunk_num"),
                    "docstore_chunk_count": len(docstore_before.get("existing_ids", [])),
                    "selected_document_counts_match": selected_document_counts_match,
                    "raw_sha256": [
                        created["raw_sha256"],
                        *[response["raw_sha256"] for response in document_responses],
                        *[response["raw_sha256"] for response in chunk_responses],
                    ],
                },
                {
                    "name": "measure_cold_warmup_and_hot_operations",
                    "sample_plan": {
                        "cold_per_operation": cold_samples,
                        "warmup_per_operation": warmup_samples,
                        "hot_per_operation": hot_samples,
                        "operation_order": [
                            "dataset_list",
                            "document_list",
                            "retrieval",
                        ],
                    },
                    "measurement_shape_matches": measurement_shape_matches,
                    "all_measurement_requests_succeeded": all_measurement_requests_succeeded,
                    "all_operation_contracts_match": all_operation_contracts_match,
                    "all_latencies_positive": all_latencies_positive,
                    "metrics": metrics,
                },
                {
                    "name": "cleanup_performance_fixture_through_api",
                    "document_delete_code": (deleted_documents["code"] if deleted_documents else None),
                    "dataset_delete_code": (deleted_dataset["code"] if deleted_dataset else None),
                    "remaining_docstore_chunks": len(docstore_after.get("existing_ids", [])),
                    "remaining_docstore_dataset_rows": docstore_after.get("total"),
                    "docstore_index_exists": docstore_after.get("index_exists"),
                    "docstore_scope_removed": docstore_dataset_cleanup_contract_ok(group, docstore_after),
                    "remaining_resource_counts": cascade_after,
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": [
                        deleted_documents["raw_sha256"] if deleted_documents else None,
                        deleted_dataset["raw_sha256"] if deleted_dataset else None,
                    ],
                },
            ],
            "oracle": {
                "documents": 100,
                "selected_documents": 10,
                "chunks_per_selected_document": 10,
                "chunks": 100,
                "cold_samples_per_operation": cold_samples,
                "warmup_samples_per_operation": warmup_samples,
                "hot_samples_per_operation": hot_samples,
                "retrieval_page_size": 20,
                "retrieval_top_k": 20,
                "fixed_sla_seconds": None,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-PERFORMANCE-BASELINE-001",
                    "summary": f"{group} medium performance fixture or measurement contract was incomplete",
                    "code_location": "api/apps/restful_apis/dataset_api.py:list_datasets",
                }
            ],
        }

    result = _run_case(case_id, execute)
    group_metrics: dict[str, dict[str, Any]] = {}
    for group_result in result["groups"]:
        measurement_step = next(step for step in group_result["steps"] if step.get("name") == "measure_cold_warmup_and_hot_operations")
        group_metrics[group_result["group"]] = measurement_step["metrics"]
    comparison: dict[str, Any] = {}
    for operation in ("dataset_list", "document_list", "retrieval"):
        comparison[operation] = {}
        for phase in ("cold", "hot"):
            comparison[operation][phase] = {}
            for percentile in ("p50_seconds", "p95_seconds"):
                control_value = float(group_metrics["control"][operation][phase][percentile])
                experiment_value = float(group_metrics["experiment"][operation][phase][percentile])
                comparison[operation][phase][percentile] = {
                    "control_seconds": control_value,
                    "experiment_seconds": experiment_value,
                    "experiment_over_control_ratio": round(experiment_value / control_value, 6) if control_value > 0 else None,
                    "experiment_minus_control_seconds": round(experiment_value - control_value, 6),
                }
    result["cross_group_performance_comparison"] = {
        "shared_development_environment": True,
        "production_sla_claimed": False,
        "metrics": comparison,
    }
    _evidence_module().write_evidence(EVIDENCE_DIR / f"{case_id}.json", result)
    return result


def _run_cascade_subset_case(case_id: str, mode: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")
    required_check = {
        "documents": "documents_removed",
        "files": "files_removed",
        "chunks": "chunks_removed",
        "objects": "objects_removed",
    }[mode]

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        payload: dict[str, Any] = {"name": prefix}
        if mode in {"documents", "chunks"}:
            payload["parser_config"] = {
                "chunk_token_num": 8,
                "delimiter": "`###`",
                "layout_recognize": "Plain Text",
            }
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_cascade_subset_dataset",
            payload,
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        upload_responses: list[dict[str, Any]] = []
        if mode == "files":
            upload_responses = [
                _upload_empty_document(
                    case_id,
                    group,
                    owner["auth"],
                    f"create_linked_empty_document_{index}",
                    dataset_id,
                    f"{prefix}-{index}.txt",
                )
                for index in (1, 2)
            ]
        else:
            document_count = 2 if mode == "documents" else 1
            documents = [
                (
                    f"{prefix}-{index}.txt",
                    (f"cascade {index} segment one ### cascade {index} segment two ### cascade {index} segment three").encode("ascii"),
                    "text/plain",
                )
                for index in range(1, document_count + 1)
            ]
            upload_responses = [
                _upload_local_documents(
                    case_id,
                    group,
                    owner["auth"],
                    "upload_cascade_subset_documents",
                    dataset_id,
                    documents,
                )
            ]
        uploaded_items = _uploaded_items(upload_responses)
        document_ids = [str(item.get("id") or "") for item in uploaded_items if item.get("id")]
        parsed = None
        parsing = {"terminal": True, "timed_out": False, "documents": []}
        if mode in {"documents", "chunks"} and document_ids:
            parsed = _parse_documents(
                case_id,
                group,
                owner["auth"],
                "parse_cascade_subset_documents",
                dataset_id,
                document_ids,
            )
            parsing = _wait_for_document_parsing(group, document_ids, timeout=240)
        listed_chunks: list[dict[str, Any]] = []
        chunk_ids: list[str] = []
        if mode == "chunks":
            listed_chunks = [
                _list_document_chunks(
                    case_id,
                    group,
                    owner["auth"],
                    "list_chunks_before_dataset_delete",
                    dataset_id,
                    document_id,
                )
                for document_id in document_ids
            ]
            for response in listed_chunks:
                data = response["data"] if isinstance(response["data"], dict) else {}
                chunks = data.get("chunks") if isinstance(data.get("chunks"), list) else []
                chunk_ids.extend(str(item.get("id") or item.get("chunk_id") or "") for item in chunks if isinstance(item, dict) and (item.get("id") or item.get("chunk_id")))
        resources = _cascade_resource_snapshot(group, dataset_id, document_ids)
        object_state_before = _storage_object_existence(group, dataset_id, resources["locations"])
        docstore_before = (
            _docstore_chunk_batch_snapshot(
                group,
                owner["tenant_id"],
                dataset_id,
                chunk_ids,
            )
            if mode == "chunks"
            else {"index_exists": False, "total": 0, "existing_ids": []}
        )
        if mode == "documents":
            precondition_ready = (
                precondition_clean
                and len(resources["document_ids"]) == 2
                and len(resources["task_ids"]) >= 2
                and parsing.get("terminal") is True
                and all(row.get("run") == "3" for row in parsing.get("documents", []))
            )
        elif mode == "files":
            precondition_ready = precondition_clean and len(resources["document_ids"]) == 2 and len(resources["file_ids"]) == 2 and len(resources["file_link_ids"]) == 2
        elif mode == "chunks":
            precondition_ready = (
                precondition_clean
                and len(resources["document_ids"]) == 1
                and parsing.get("terminal") is True
                and bool(chunk_ids)
                and set(docstore_before.get("existing_ids", [])) == set(chunk_ids)
                and int(docstore_before.get("total") or 0) == len(chunk_ids)
            )
        else:
            precondition_ready = precondition_clean and len(resources["document_ids"]) == 1 and len(resources["locations"]) == 1 and all(object_state_before.values())
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "delete_cascade_subset_dataset",
            [dataset_id],
        )
        deleted_data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        after = _saved_cascade_resource_counts(
            group,
            dataset_id,
            resources["document_ids"],
            resources["task_ids"],
            resources["file_ids"],
            resources["file_link_ids"],
        )
        object_state_after = _storage_object_existence(group, dataset_id, resources["locations"])
        docstore_after = (
            _docstore_chunk_batch_snapshot(
                group,
                owner["tenant_id"],
                dataset_id,
                chunk_ids,
            )
            if mode == "chunks"
            else {"index_exists": False, "total": 0, "existing_ids": []}
        )
        final_metadata_cleanup = True if after["dataset_count"] == 0 else _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        checks = {
            "documents_removed": (after["dataset_count"] == 0 and after["document_count"] == 0 and after["task_count"] == 0),
            "files_removed": (after["dataset_count"] == 0 and after["file_count"] == 0 and after["file_link_count"] == 0),
            "chunks_removed": docstore_dataset_cleanup_contract_ok(group, docstore_after),
            "objects_removed": (bool(resources["locations"]) and all(object_state_before.values()) and not any(object_state_after.values())),
        }
        api_codes = [
            created["code"],
            *[response["code"] for response in upload_responses],
            *([parsed["code"]] if parsed is not None else []),
            *[response["code"] for response in listed_chunks],
            deleted["code"],
        ]
        observed = {
            "api_codes": api_codes,
            "precondition_ready": precondition_ready,
            "delete_http_status": deleted["http_status"],
            "delete_code": deleted["code"],
            "delete_success_count": deleted_data.get("success_count"),
            **checks,
            "final_metadata_cleanup": final_metadata_cleanup,
        }
        passed = cascade_subset_contract_ok(observed, required_checks=[required_check])
        finding_ids = {
            "documents": "DD-DEL-DOCUMENTS-001",
            "files": "DD-DEL-FILES-001",
            "chunks": "DD-DEL-CHUNKS-001",
            "objects": "DD-CASCADE-OBJECT-001",
        }
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_cascade_subset_precondition",
                    "mode": mode,
                    "api_codes": api_codes[:-1],
                    "document_count": len(resources["document_ids"]),
                    "task_count": len(resources["task_ids"]),
                    "file_count": len(resources["file_ids"]),
                    "file_link_count": len(resources["file_link_ids"]),
                    "chunk_count": len(chunk_ids),
                    "object_count": len(resources["locations"]),
                    "parse_terminal": parsing.get("terminal"),
                    "precondition_ready": precondition_ready,
                    "raw_sha256": [
                        created["raw_sha256"],
                        *[response["raw_sha256"] for response in upload_responses],
                        *([parsed["raw_sha256"]] if parsed is not None else []),
                        *[response["raw_sha256"] for response in listed_chunks],
                    ],
                },
                {
                    "name": "delete_dataset_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "success_count": deleted_data.get("success_count"),
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "verify_saved_cascade_subset_after_delete",
                    "database_counts": after,
                    "remaining_object_count": sum(object_state_after.values()),
                    "docstore": docstore_after,
                    **checks,
                    "required_check": required_check,
                    "final_metadata_cleanup": final_metadata_cleanup,
                },
            ],
            "oracle": {
                "required_check": required_check,
                "saved_ids_checked_after_parent_delete": True,
                "unrelated_checks_do_not_control_status": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": finding_ids[mode],
                    "summary": f"{group} dataset deletion did not remove the required {mode} resources",
                    "code_location": "api/apps/services/dataset_api_service.py:delete_datasets",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_del001() -> dict[str, Any]:
    return _run_cascade_subset_case("TC-DD-DEL-001", "documents")


def run_del002() -> dict[str, Any]:
    return _run_cascade_subset_case("TC-DD-DEL-002", "files")


def run_del003() -> dict[str, Any]:
    return _run_cascade_subset_case("TC-DD-DEL-003", "chunks")


def run_del004() -> dict[str, Any]:
    return _run_cascade_subset_case("TC-DD-DEL-004", "objects")


def run_del005() -> dict[str, Any]:
    case_id = "TC-DD-DEL-005"
    prefix = "fresh-dd-del-005"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        before_ids = _tenant_dataset_ids(group, owner["tenant_id"])
        precondition_clean = not before_ids
        if not precondition_clean:
            return {
                "status": "BLOCKED",
                "steps": [
                    {
                        "name": "require_empty_dedicated_tenant_before_delete_all",
                        "precondition_clean": False,
                        "existing_dataset_count": len(before_ids),
                    }
                ],
                "oracle": {"existing_dataset_count": 0},
                "findings": [],
            }
        created = [
            _create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_delete_all_dataset_{index}",
                {"name": f"{prefix}-{index}"},
            )
            for index in (1, 2, 3)
        ]
        created_ids = [str(response["data"].get("id") or "") if isinstance(response["data"], dict) else "" for response in created]
        deleted = _request(
            case_id,
            group,
            "delete_all_tenant_datasets",
            owner["auth"],
            "DELETE",
            "/datasets",
            payload={"delete_all": True},
            timeout=120,
        )
        deleted_data = deleted["data"] if isinstance(deleted["data"], dict) else {}
        remaining_ids = _tenant_dataset_ids(group, owner["tenant_id"])
        saved_counts = [_dataset_snapshot(group, dataset_id).get("count") for dataset_id in created_ids if dataset_id]
        if remaining_ids:
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_delete_all_residue",
                remaining_ids,
            )
        observed = {
            "precondition_clean": precondition_clean,
            "create_codes": [response["code"] for response in created],
            "created_count": len([dataset_id for dataset_id in created_ids if dataset_id]),
            "delete_http_status": deleted["http_status"],
            "delete_code": deleted["code"],
            "delete_success_count": deleted_data.get("success_count"),
            "remaining_tenant_dataset_count": len(remaining_ids),
        }
        passed = delete_all_contract_ok(observed) and saved_counts == [0, 0, 0]
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_delete_all_datasets",
                    "precondition_clean": precondition_clean,
                    "create_codes": observed["create_codes"],
                    "created_count": observed["created_count"],
                    "raw_sha256": [response["raw_sha256"] for response in created],
                },
                {
                    "name": "delete_all_datasets_through_api",
                    "http_status": deleted["http_status"],
                    "code": deleted["code"],
                    "success_count": deleted_data.get("success_count"),
                    "raw_sha256": deleted["raw_sha256"],
                },
                {
                    "name": "verify_tenant_has_zero_datasets",
                    "remaining_tenant_dataset_count": len(remaining_ids),
                    "saved_dataset_counts": saved_counts,
                },
            ],
            "oracle": {
                "created_datasets": 3,
                "delete_success_count": 3,
                "remaining_tenant_datasets": 0,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-DEL-ALL-001",
                    "summary": f"{group} delete_all did not remove exactly the three dedicated datasets",
                    "code_location": "api/apps/services/dataset_api_service.py:delete_datasets",
                }
            ],
        }

    return _run_case(case_id, execute)


def _run_supplement_tag_case(case_id: str, action: str) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        if action == "aggregate":
            fixture_a = _create_tag_fixture(
                case_id,
                group,
                owner,
                f"{prefix}-a",
                [["important", "review"], ["important"]],
            )
            fixture_b = _create_tag_fixture(
                case_id,
                group,
                owner,
                f"{prefix}-b",
                [["important"]],
            )
            fixtures = [fixture_a, fixture_b]
        else:
            tags_by_chunk = [["important", "review"], ["important"]] if action == "list" else [["important", "review"]]
            fixtures = [_create_tag_fixture(case_id, group, owner, prefix, tags_by_chunk)]
        dataset_ids = [fixture["dataset_id"] for fixture in fixtures]
        fixture_persisted = all(fixture["fixture_persisted"] for fixture in fixtures)
        fixture_codes = [code for fixture in fixtures for code in fixture["api_codes"]]
        fixture_hashes = [digest for fixture in fixtures for digest in fixture["raw_sha256"]]
        mutation_response = None
        if action == "delete":
            mutation_response = _delete_tags(
                case_id,
                group,
                owner["auth"],
                "delete_review_tag",
                dataset_ids[0],
                ["review"],
            )
        elif action == "rename":
            mutation_response = _rename_tag(
                case_id,
                group,
                owner["auth"],
                "rename_important_to_critical",
                dataset_ids[0],
                "important",
                "critical",
            )

        if action == "aggregate":
            queried = _request(
                case_id,
                group,
                "aggregate_selected_ascii_tags",
                owner["auth"],
                "GET",
                "/datasets/tags/aggregation",
                params={"dataset_ids": ",".join(dataset_ids)},
            )
            actual_counts = _tag_aggregation_map(queried["data"])
            expected_counts = {"important": 3, "review": 1}
            mutation_visible_in_chunks = True
            chunk_reads: list[dict[str, Any]] = []
            chunk_tags: list[list[str]] = []
        else:
            queried = _list_tags(
                case_id,
                group,
                owner["auth"],
                "list_ascii_dataset_tags",
                dataset_ids[0],
            )
            actual_counts = _tag_count_map(queried["data"])
            expected_counts = {
                "fixture": {"important": 1, "review": 1},
                "list": {"important": 2, "review": 1},
                "delete": {"important": 1},
                "rename": {"critical": 1, "review": 1},
            }[action]
            fixture = fixtures[0]
            chunk_reads = [
                _get_document_chunk(
                    case_id,
                    group,
                    owner["auth"],
                    f"read_ascii_tag_chunk_after_{action}_{index}",
                    fixture["dataset_id"],
                    fixture["document_id"],
                    chunk_id,
                )
                for index, chunk_id in enumerate(fixture["chunk_ids"], 1)
                if chunk_id
            ]
            chunk_tags = [sorted(str(tag) for tag in (response["data"].get("tag_kwd", []) if isinstance(response["data"], dict) else [])) for response in chunk_reads]
            expected_chunk_tags = {
                "fixture": [["important", "review"]],
                "list": [["important", "review"], ["important"]],
                "delete": [["important"]],
                "rename": [["critical", "review"]],
            }[action]
            mutation_visible_in_chunks = chunk_tags == expected_chunk_tags
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_supplement_tag_datasets",
            dataset_ids,
        )
        cleanup = deleted["code"] == 0 and all(_dataset_snapshot(group, dataset_id).get("count") == 0 for dataset_id in dataset_ids)
        api_codes = [
            *fixture_codes,
            *([mutation_response["code"]] if mutation_response is not None else []),
            queried["code"],
            *[response["code"] for response in chunk_reads],
        ]
        observed = {
            "api_codes": api_codes,
            "fixture_persisted": fixture_persisted,
            "expected_tag_counts": expected_counts,
            "actual_tag_counts": actual_counts,
            "mutation_visible_in_chunks": mutation_visible_in_chunks,
            "cleanup_succeeded": cleanup,
        }
        passed = tag_contract_ok(observed)
        finding_ids = {
            "fixture": "DD-TAG-FIXTURE-001",
            "list": "DD-TAG-LIST-001",
            "delete": "DD-TAG-DELETE-001",
            "rename": "DD-TAG-RENAME-001",
            "aggregate": "DD-TAG-AGGREGATION-001",
        }
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_ascii_tag_fixtures_through_api",
                    "dataset_count": len(dataset_ids),
                    "fixture_persisted": fixture_persisted,
                    "api_codes": fixture_codes,
                    "raw_sha256": fixture_hashes,
                },
                {
                    "name": f"execute_and_verify_ascii_tag_{action}",
                    "mutation_code": (mutation_response["code"] if mutation_response is not None else None),
                    "query_code": queried["code"],
                    "expected_tag_counts": expected_counts,
                    "actual_tag_counts": actual_counts,
                    "chunk_tags": chunk_tags,
                    "mutation_visible_in_chunks": mutation_visible_in_chunks,
                    "raw_sha256": [
                        *([mutation_response["raw_sha256"]] if mutation_response is not None else []),
                        queried["raw_sha256"],
                        *[response["raw_sha256"] for response in chunk_reads],
                    ],
                },
                {
                    "name": "cleanup_supplement_tag_datasets_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "action": action,
                "tag_counts": expected_counts,
                "count_unit": "chunks",
                "ascii_fixture": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": finding_ids[action],
                    "summary": f"{group} ASCII tag {action} did not match list and chunk readback",
                    "code_location": "api/apps/services/dataset_api_service.py",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_tag001() -> dict[str, Any]:
    return _run_supplement_tag_case("TC-DD-TAG-001", "fixture")


def run_tag002() -> dict[str, Any]:
    return _run_supplement_tag_case("TC-DD-TAG-002", "list")


def run_tag003() -> dict[str, Any]:
    return _run_supplement_tag_case("TC-DD-TAG-003", "delete")


def run_tag004() -> dict[str, Any]:
    return _run_supplement_tag_case("TC-DD-TAG-004", "rename")


def run_tag005() -> dict[str, Any]:
    return _run_supplement_tag_case("TC-DD-TAG-005", "aggregate")


def _run_supplement_metadata_config_case(case_id: str, *, expected_config: dict[str, list[dict[str, str]]], update: bool) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_supplement_metadata_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        updated = (
            _update_metadata_config(
                case_id,
                group,
                owner["auth"],
                "update_supplement_metadata_config",
                dataset_id,
                expected_config,
            )
            if update
            else None
        )
        fetched = _get_metadata_config(
            case_id,
            group,
            owner["auth"],
            "get_supplement_metadata_config",
            dataset_id,
        )
        snapshot = _dataset_snapshot(group, dataset_id)
        update_config = _normalize_metadata_config(updated["data"]) if updated is not None else None
        api_config = _normalize_metadata_config(fetched["data"])
        database_config = _normalize_metadata_config(snapshot.get("parser_config"))
        independent_column_absent = not _knowledgebase_column_exists(group, "auto_metadata_config")
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_supplement_metadata_dataset",
            [dataset_id],
        )
        cleanup = deleted["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        api_codes = [
            created["code"],
            *([updated["code"]] if updated is not None else []),
            fetched["code"],
        ]
        observed = {
            "api_codes": api_codes,
            "expected_config": expected_config,
            "update_config": update_config,
            "api_config": api_config,
            "database_config": database_config,
            "independent_column_absent": independent_column_absent,
            "cleanup_succeeded": cleanup,
        }
        passed = precondition_clean and metadata_config_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_supplement_metadata_config_fixture",
                    "precondition_clean": precondition_clean,
                    "create_code": created["code"],
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "update_and_get_supplement_metadata_config",
                    "update_requested": update,
                    "update_code": updated["code"] if updated is not None else None,
                    "get_code": fetched["code"],
                    "expected_config": expected_config,
                    "update_config": update_config,
                    "api_config": api_config,
                    "database_config": database_config,
                    "independent_column_absent": independent_column_absent,
                    "raw_sha256": [
                        *([updated["raw_sha256"]] if updated is not None else []),
                        fetched["raw_sha256"],
                    ],
                },
                {
                    "name": "cleanup_supplement_metadata_dataset_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "config": expected_config,
                "stored_in": "knowledgebase.parser_config",
                "update_requested": update,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-METADATA-CONFIG-WRITE-001" if update else "DD-METADATA-CONFIG-READ-001",
                    "summary": f"{group} supplement metadata config API and parser_config did not agree",
                    "code_location": "api/apps/services/dataset_api_service.py",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_meta001() -> dict[str, Any]:
    return _run_supplement_metadata_config_case(
        "TC-DD-META-001",
        expected_config={"metadata": [], "built_in_metadata": []},
        update=False,
    )


def run_meta002() -> dict[str, Any]:
    return _run_supplement_metadata_config_case(
        "TC-DD-META-002",
        expected_config={
            "metadata": [
                {"key": "author", "type": "string"},
                {"key": "date", "type": "time"},
            ],
            "built_in_metadata": [],
        },
        update=True,
    )


def run_meta003() -> dict[str, Any]:
    case_id = "TC-DD-META-003"
    prefix = "fresh-dd-meta-003"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        datasets = [
            _create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_flattened_metadata_dataset_{index}",
                {"name": f"{prefix}-{index}"},
            )
            for index in (1, 2, 3)
        ]
        dataset_ids = [str(response["data"].get("id") or "") if isinstance(response["data"], dict) else "" for response in datasets]
        documents = [
            _upload_empty_document(
                case_id,
                group,
                owner["auth"],
                f"create_flattened_metadata_document_{index}",
                dataset_id,
                f"{prefix}-document-{index}.txt",
            )
            for index, dataset_id in enumerate(dataset_ids, 1)
        ]
        document_ids = [str(response["data"].get("id") or "") if isinstance(response["data"], dict) else "" for response in documents]
        authors = ("alice", "bob", "charlie")
        updates = [
            _update_document(
                case_id,
                group,
                owner["auth"],
                f"set_flattened_metadata_{author}",
                dataset_id,
                document_id,
                {"meta_fields": {"author": author}},
            )
            for dataset_id, document_id, author in zip(dataset_ids, document_ids, authors)
        ]
        selected_dataset_ids = dataset_ids[:2]
        flattened = _get_flattened_metadata(
            case_id,
            group,
            owner["auth"],
            "get_two_selected_datasets_flattened_metadata",
            selected_dataset_ids,
        )
        actual_metadata = _normalize_flattened_metadata(flattened["data"])
        expected_metadata = (
            {
                "author": {
                    "alice": [document_ids[0]],
                    "bob": [document_ids[1]],
                }
            }
            if len(document_ids) == 3 and all(document_ids)
            else {}
        )
        unselected_document_absent = len(document_ids) == 3 and bool(document_ids[2]) and all(document_ids[2] not in ids for values in actual_metadata.values() for ids in values.values())
        deleted = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_flattened_metadata_datasets",
            dataset_ids,
        )
        cleanup = deleted["code"] == 0 and all(_dataset_snapshot(group, dataset_id).get("count") == 0 for dataset_id in dataset_ids)
        observed = {
            "api_codes": [
                *[response["code"] for response in datasets],
                *[response["code"] for response in documents],
                *[response["code"] for response in updates],
                flattened["code"],
            ],
            "expected_metadata": expected_metadata,
            "actual_metadata": actual_metadata,
            "unselected_document_absent": unselected_document_absent,
            "cleanup_succeeded": cleanup,
        }
        passed = precondition_clean and flattened_metadata_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_three_flattened_metadata_fixtures",
                    "precondition_clean": precondition_clean,
                    "dataset_codes": [response["code"] for response in datasets],
                    "document_codes": [response["code"] for response in documents],
                    "update_codes": [response["code"] for response in updates],
                    "raw_sha256": [
                        *[response["raw_sha256"] for response in datasets],
                        *[response["raw_sha256"] for response in documents],
                        *[response["raw_sha256"] for response in updates],
                    ],
                },
                {
                    "name": "get_selected_flattened_metadata",
                    "http_status": flattened["http_status"],
                    "code": flattened["code"],
                    "selected_dataset_count": len(selected_dataset_ids),
                    "expected_metadata": expected_metadata,
                    "actual_metadata": actual_metadata,
                    "unselected_document_absent": unselected_document_absent,
                    "raw_sha256": flattened["raw_sha256"],
                },
                {
                    "name": "cleanup_flattened_metadata_datasets_through_api",
                    "cleanup_succeeded": cleanup,
                    "raw_sha256": deleted["raw_sha256"],
                },
            ],
            "oracle": {
                "shape": "{metadata_key: {metadata_value: [document_id]}}",
                "selected_dataset_count": 2,
                "unselected_dataset_absent": True,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-METADATA-FLATTENED-001",
                    "summary": f"{group} flattened metadata did not match the two selected datasets",
                    "code_location": "api/db/services/doc_metadata_service.py:get_flatted_meta_by_kbs",
                }
            ],
        }

    return _run_case(case_id, execute)


def _create_index_fixture(
    case_id: str,
    group: str,
    owner: dict[str, str],
    prefix: str,
    *,
    with_chunk: bool,
) -> dict[str, Any]:
    precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
    created = _create_dataset(
        case_id,
        group,
        owner["auth"],
        "create_index_fixture_dataset",
        {"name": prefix},
    )
    created_data = created["data"] if isinstance(created["data"], dict) else {}
    dataset_id = str(created_data.get("id") or "")
    document = _upload_empty_document(
        case_id,
        group,
        owner["auth"],
        "create_index_fixture_document",
        dataset_id,
        f"{prefix}-document.txt",
    )
    document_data = document["data"] if isinstance(document["data"], dict) else {}
    document_id = str(document_data.get("id") or "")
    chunk = (
        _add_chunk(
            case_id,
            group,
            owner["auth"],
            "create_index_fixture_chunk",
            dataset_id,
            document_id,
            {
                "content": ("fresh index fixture alpha beta gamma with stable ASCII content for graph and raptor cleanup validation"),
                "important_keywords": ["alpha", "beta", "index"],
            },
        )
        if with_chunk
        else None
    )
    chunk_data = chunk["data"] if chunk and isinstance(chunk["data"], dict) else {}
    chunk_body = chunk_data.get("chunk") if isinstance(chunk_data.get("chunk"), dict) else {}
    return {
        "precondition_clean": precondition_clean,
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": str(chunk_body.get("id") or ""),
        "api_codes": [
            created["code"],
            document["code"],
            *([chunk["code"]] if chunk is not None else []),
        ],
        "raw_sha256": [
            created["raw_sha256"],
            document["raw_sha256"],
            *([chunk["raw_sha256"]] if chunk is not None else []),
        ],
    }


def _run_index_schedule_case(case_id: str, index_type: str) -> dict[str, Any]:
    expected_task_type = _INDEX_TASK_FIELDS[index_type][1]
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_index_fixture(case_id, group, owner, prefix, with_chunk=False)
        dataset_id = fixture["dataset_id"]
        run = _run_index_request(
            case_id,
            group,
            owner["auth"],
            f"run_{index_type}_index",
            dataset_id,
            index_type,
        )
        run_data = run["data"] if isinstance(run["data"], dict) else {}
        response_task_id = str(run_data.get("task_id") or "")
        snapshot = _index_task_snapshot(group, dataset_id, index_type)
        task_snapshot = snapshot.get("task", {})
        index_cleanup = _delete_index_request(
            case_id,
            group,
            owner["auth"],
            f"cleanup_{index_type}_index",
            dataset_id,
            index_type,
        )
        after = _index_task_snapshot(group, dataset_id, index_type)
        original_task_after = _task_row_snapshot(group, response_task_id)
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_index_fixture_dataset",
            [dataset_id],
        )
        cleanup_succeeded = (
            index_cleanup["code"] == 0
            and after.get("task_id") == ""
            and original_task_after.get("count") == 0
            and dataset_cleanup["code"] == 0
            and _dataset_snapshot(group, dataset_id).get("count") == 0
        )
        observed = {
            "precondition_clean": fixture["precondition_clean"],
            "fixture_api_codes": fixture["api_codes"],
            "run_http_status": run["http_status"],
            "run_code": run["code"],
            "response_task_id": response_task_id,
            "database_task_id": snapshot.get("task_id"),
            "task_row_count": task_snapshot.get("count"),
            "expected_task_type": expected_task_type,
            "database_task_type": task_snapshot.get("task_type"),
            "database_progress": task_snapshot.get("progress"),
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = index_schedule_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_fresh_index_fixture_through_api",
                    "precondition_clean": fixture["precondition_clean"],
                    "api_codes": fixture["api_codes"],
                    "dataset_id_present": bool(dataset_id),
                    "document_id_present": bool(fixture["document_id"]),
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": f"schedule_{index_type}_index_and_validate_task",
                    "http_status": run["http_status"],
                    "code": run["code"],
                    "response_task_id_present": bool(response_task_id),
                    "database_task_id_matches": snapshot.get("task_id") == response_task_id,
                    "database_task": task_snapshot,
                    "raw_sha256": run["raw_sha256"],
                },
                {
                    "name": "cleanup_index_and_dataset_through_api",
                    "index_cleanup_code": index_cleanup["code"],
                    "task_id_after": after.get("task_id"),
                    "original_task_row_count_after": original_task_after.get("count"),
                    "dataset_cleanup_code": dataset_cleanup["code"],
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": [
                        index_cleanup["raw_sha256"],
                        dataset_cleanup["raw_sha256"],
                    ],
                },
            ],
            "oracle": {
                "index_type": index_type,
                "task_type": expected_task_type,
                "task_progress": "-1 or 0.0 through 1.0",
                "database_validation": "read only",
            },
            "findings": []
            if passed
            else [
                {
                    "id": f"DD-INDEX-SCHEDULE-{index_type.upper()}-001",
                    "summary": (f"{group} {index_type} index scheduling response and persisted task did not satisfy the plan"),
                    "code_location": "api/apps/services/dataset_api_service.py:run_index",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_idx001() -> dict[str, Any]:
    return _run_index_schedule_case("TC-DD-IDX-001", "graph")


def run_idx002() -> dict[str, Any]:
    return _run_index_schedule_case("TC-DD-IDX-002", "raptor")


def run_idx003() -> dict[str, Any]:
    return _run_index_schedule_case("TC-DD-IDX-003", "mindmap")


def run_idx004() -> dict[str, Any]:
    case_id = "TC-DD-IDX-004"
    prefix = "fresh-dd-idx-004"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_index_fixture(case_id, group, owner, prefix, with_chunk=False)
        dataset_id = fixture["dataset_id"]
        operations: dict[str, dict[str, Any]] = {}
        step_operations: list[dict[str, Any]] = []
        for index_type in ("graph", "raptor", "mindmap"):
            run = _run_index_request(
                case_id,
                group,
                owner["auth"],
                f"run_{index_type}_for_trace",
                dataset_id,
                index_type,
            )
            run_data = run["data"] if isinstance(run["data"], dict) else {}
            response_task_id = str(run_data.get("task_id") or "")
            trace = _trace_index_request(
                case_id,
                group,
                owner["auth"],
                f"trace_{index_type}_index",
                dataset_id,
                index_type,
            )
            trace_data = trace["data"] if isinstance(trace["data"], dict) else {}
            snapshot = _index_task_snapshot(group, dataset_id, index_type)
            task_snapshot = snapshot.get("task", {})
            cleanup = _delete_index_request(
                case_id,
                group,
                owner["auth"],
                f"cleanup_{index_type}_after_trace",
                dataset_id,
                index_type,
            )
            after = _index_task_snapshot(group, dataset_id, index_type)
            original_after = _task_row_snapshot(group, response_task_id)
            operation = {
                "run_code": run["code"],
                "trace_http_status": trace["http_status"],
                "trace_code": trace["code"],
                "response_task_id": response_task_id,
                "trace_task_id": str(trace_data.get("id") or ""),
                "database_task_id": snapshot.get("task_id"),
                "expected_task_type": _INDEX_TASK_FIELDS[index_type][1],
                "trace_task_type": str(trace_data.get("task_type") or ""),
                "trace_progress": trace_data.get("progress"),
                "database_progress": task_snapshot.get("progress"),
                "cleanup_code": cleanup["code"],
                "cleanup_task_cleared": after.get("task_id") == "" and original_after.get("count") == 0,
            }
            operations[index_type] = operation
            step_operations.append(
                {
                    "index_type": index_type,
                    **operation,
                    "trace_progress_msg": str(trace_data.get("progress_msg") or ""),
                    "raw_sha256": [
                        run["raw_sha256"],
                        trace["raw_sha256"],
                        cleanup["raw_sha256"],
                    ],
                }
            )
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_index_trace_dataset",
            [dataset_id],
        )
        dataset_cleanup_succeeded = dataset_cleanup["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "precondition_clean": fixture["precondition_clean"],
            "fixture_api_codes": fixture["api_codes"],
            "operations": operations,
            "dataset_cleanup_succeeded": dataset_cleanup_succeeded,
        }
        passed = index_trace_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_fresh_index_trace_fixture_through_api",
                    "precondition_clean": fixture["precondition_clean"],
                    "api_codes": fixture["api_codes"],
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "run_trace_and_cleanup_each_index_type",
                    "operations": step_operations,
                },
                {
                    "name": "cleanup_index_trace_dataset_through_api",
                    "code": dataset_cleanup["code"],
                    "cleanup_succeeded": dataset_cleanup_succeeded,
                    "raw_sha256": dataset_cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "index_types": ["graph", "raptor", "mindmap"],
                "trace_contains": ["id", "task_type", "progress", "progress_msg"],
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-INDEX-TRACE-001",
                    "summary": f"{group} index trace did not match one or more scheduled tasks",
                    "code_location": "api/apps/services/dataset_api_service.py:trace_index",
                }
            ],
        }

    return _run_case(case_id, execute)


def _artifact_not_visible_in_search(data: Any, marker_field: str) -> bool:
    payload = data if isinstance(data, dict) else {}
    chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
    return all(not chunk.get(marker_field) for chunk in chunks if isinstance(chunk, dict))


def index_artifact_search_payload(document_id: str) -> dict[str, Any]:
    if not document_id:
        raise ValueError("document_id is required for the index artefact search")
    return {
        "question": "alpha beta index",
        "doc_ids": [document_id],
        "page": 1,
        "size": 20,
        "top_k": 20,
        "similarity_threshold": 0.0,
        "vector_similarity_weight": 0.5,
    }


def run_idx005() -> dict[str, Any]:
    case_id = "TC-DD-IDX-005"
    prefix = "fresh-dd-idx-005"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_index_fixture(case_id, group, owner, prefix, with_chunk=True)
        dataset_id = fixture["dataset_id"]
        operations: dict[str, dict[str, Any]] = {}
        step_operations: list[dict[str, Any]] = []
        for index_type in ("graph", "raptor", "mindmap"):
            run = _run_index_request(
                case_id,
                group,
                owner["auth"],
                f"run_{index_type}_for_delete",
                dataset_id,
                index_type,
            )
            run_data = run["data"] if isinstance(run["data"], dict) else {}
            task_id = str(run_data.get("task_id") or "")
            before = _index_task_snapshot(group, dataset_id, index_type)
            task_before = before.get("task", {})
            artifact_before = _docstore_index_artifact_snapshot(group, owner["tenant_id"], dataset_id) if index_type in {"graph", "raptor"} else None
            deleted = _delete_index_request(
                case_id,
                group,
                owner["auth"],
                f"delete_{index_type}_index",
                dataset_id,
                index_type,
            )
            trace_after = _trace_index_request(
                case_id,
                group,
                owner["auth"],
                f"trace_{index_type}_after_delete",
                dataset_id,
                index_type,
            )
            after = _index_task_snapshot(group, dataset_id, index_type)
            task_after = _task_row_snapshot(group, task_id)
            operation: dict[str, Any] = {
                "run_code": run["code"],
                "delete_http_status": deleted["http_status"],
                "delete_code": deleted["code"],
                "task_id_present_before": bool(before.get("task_id")) and before.get("task_id") == task_id,
                "task_row_present_before": task_before.get("count") == 1,
                "task_id_cleared_after": after.get("task_id") == "",
                "task_row_removed_after": task_after.get("count") == 0,
                "trace_empty_after": trace_after["code"] == 0 and trace_after["data"] == {},
            }
            artifact_api = None
            artifact_after = None
            if index_type in {"graph", "raptor"}:
                time.sleep(1.0)
                artifact_after = _docstore_index_artifact_snapshot(group, owner["tenant_id"], dataset_id)
                if index_type == "graph":
                    artifact_api = _request(
                        case_id,
                        group,
                        "get_graph_after_index_delete",
                        owner["auth"],
                        "GET",
                        f"/datasets/{dataset_id}/graph",
                    )
                    api_data = artifact_api["data"] if isinstance(artifact_api["data"], dict) else {}
                    not_visible_via_api = api_data.get("graph") == {} and api_data.get("mind_map") == {}
                    artifact_absent = not artifact_after["graph_artifact_ids"]
                else:
                    artifact_api = _request(
                        case_id,
                        group,
                        "search_after_raptor_index_delete",
                        owner["auth"],
                        "POST",
                        f"/datasets/{dataset_id}/search",
                        payload=index_artifact_search_payload(fixture["document_id"]),
                        timeout=120,
                    )
                    not_visible_via_api = _artifact_not_visible_in_search(artifact_api["data"], "raptor_kwd")
                    artifact_absent = not artifact_after["raptor_artifact_ids"]
                operation.update(
                    {
                        "artifact_check_performed": True,
                        "artifact_api_http_status": artifact_api["http_status"],
                        "artifact_api_code": artifact_api["code"],
                        "artifact_not_visible_via_api": not_visible_via_api,
                        "artifact_absent_after": artifact_absent,
                    }
                )
            operations[index_type] = operation
            step_operations.append(
                {
                    "index_type": index_type,
                    **operation,
                    "task_id_present": bool(task_id),
                    "artifact_snapshot_before": artifact_before,
                    "artifact_snapshot_after": artifact_after,
                    "raw_sha256": [
                        run["raw_sha256"],
                        deleted["raw_sha256"],
                        trace_after["raw_sha256"],
                        *([artifact_api["raw_sha256"]] if artifact_api is not None else []),
                    ],
                }
            )
        knowledge_graph_table_absent = not _metadata_table_exists(group, "knowledge_graph")
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_index_delete_dataset",
            [dataset_id],
        )
        dataset_cleanup_succeeded = dataset_cleanup["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "precondition_clean": fixture["precondition_clean"],
            "fixture_api_codes": fixture["api_codes"],
            "operations": operations,
            "knowledge_graph_metadata_table_absent": knowledge_graph_table_absent,
            "dataset_cleanup_succeeded": dataset_cleanup_succeeded,
        }
        passed = index_delete_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_index_delete_fixture_with_real_vector_chunk",
                    "precondition_clean": fixture["precondition_clean"],
                    "api_codes": fixture["api_codes"],
                    "chunk_id_present": bool(fixture["chunk_id"]),
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "delete_each_index_and_validate_task_and_artifacts",
                    "operations": step_operations,
                    "knowledge_graph_metadata_table_absent": knowledge_graph_table_absent,
                },
                {
                    "name": "cleanup_index_delete_dataset_through_api",
                    "code": dataset_cleanup["code"],
                    "cleanup_succeeded": dataset_cleanup_succeeded,
                    "raw_sha256": dataset_cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "task_id_cleared": True,
                "task_row_removed": True,
                "graph_and_raptor_artifacts_absent": True,
                "knowledge_graph_metadata_table": "must not exist",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-INDEX-DELETE-001",
                    "summary": (f"{group} index deletion did not clear all task state or Graph/RAPTOR artefacts"),
                    "code_location": "api/apps/services/dataset_api_service.py:delete_index",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_emb001() -> dict[str, Any]:
    case_id = "TC-DD-EMB-001"
    prefix = "fresh-dd-emb-001"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        precondition_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_embedding_schedule_dataset",
            {"name": prefix},
        )
        created_data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(created_data.get("id") or "")
        uploaded = _upload_local_documents(
            case_id,
            group,
            owner["auth"],
            "upload_two_embedding_schedule_documents",
            dataset_id,
            [
                _fixture_document(f"{prefix}-one.txt"),
                _fixture_document(f"{prefix}-two.txt"),
            ],
        )
        document_ids = [str(item.get("id") or "") for item in _uploaded_items([uploaded])]
        run = _run_embedding_request(
            case_id,
            group,
            owner["auth"],
            "run_embedding_for_all_documents",
            dataset_id,
        )
        run_data = run["data"] if isinstance(run["data"], dict) else {}
        task_counts = _document_task_counts(group, document_ids)
        terminal = _wait_for_document_parsing(group, document_ids, timeout=180)
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_embedding_schedule_dataset",
            [dataset_id],
        )
        cleanup_succeeded = dataset_cleanup["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "precondition_clean": precondition_clean,
            "fixture_api_codes": [created["code"], uploaded["code"]],
            "run_http_status": run["http_status"],
            "run_code": run["code"],
            "expected_scheduled_count": 2,
            "actual_scheduled_count": run_data.get("scheduled_count"),
            "document_count": len(document_ids),
            "documents_with_tasks": sum(1 for count in task_counts.values() if count > 0),
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = embedding_schedule_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_document_embedding_fixture_through_api",
                    "precondition_clean": precondition_clean,
                    "api_codes": [created["code"], uploaded["code"]],
                    "document_count": len(document_ids),
                    "raw_sha256": [
                        created["raw_sha256"],
                        uploaded["raw_sha256"],
                    ],
                },
                {
                    "name": "schedule_embedding_for_all_documents",
                    "http_status": run["http_status"],
                    "code": run["code"],
                    "scheduled_count": run_data.get("scheduled_count"),
                    "task_counts_by_document": task_counts,
                    "terminal_observation": terminal,
                    "raw_sha256": run["raw_sha256"],
                },
                {
                    "name": "cleanup_embedding_schedule_dataset_through_api",
                    "code": dataset_cleanup["code"],
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": dataset_cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "scheduled_count": 2,
                "count_unit": "documents",
                "response_field": "scheduled_count",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-EMBEDDING-SCHEDULE-001",
                    "summary": f"{group} embedding scheduling did not create one task per document",
                    "code_location": "api/apps/services/dataset_api_service.py:run_embedding",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_emb002() -> dict[str, Any]:
    case_id = "TC-DD-EMB-002"
    prefix = "fresh-dd-emb-002"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_index_fixture(case_id, group, owner, prefix, with_chunk=True)
        dataset_id = fixture["dataset_id"]
        configured_model = _dataset_snapshot(group, dataset_id).get("embedding_model", "")
        checked = _check_embedding_request(
            case_id,
            group,
            owner["auth"],
            "check_configured_embedding_compatibility",
            dataset_id,
            configured_model,
            check_num=1,
        )
        checked_data = checked["data"] if isinstance(checked["data"], dict) else {}
        summary = checked_data.get("summary") if isinstance(checked_data.get("summary"), dict) else {}
        results = checked_data.get("results") if isinstance(checked_data.get("results"), list) else []
        result_chunk_ids = [str(item.get("chunk_id") or "") for item in results if isinstance(item, dict)]
        vector_dimensions_positive = bool(results) and all(int(item.get("vector_dim") or 0) > 0 for item in results if isinstance(item, dict))
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_embedding_check_dataset",
            [dataset_id],
        )
        cleanup_succeeded = dataset_cleanup["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "precondition_clean": fixture["precondition_clean"],
            "fixture_api_codes": fixture["api_codes"],
            "check_http_status": checked["http_status"],
            "check_code": checked["code"],
            "configured_model": configured_model,
            "reported_model": summary.get("model"),
            "expected_chunk_id": fixture["chunk_id"],
            "result_chunk_ids": result_chunk_ids,
            "sampled": summary.get("sampled"),
            "valid": summary.get("valid"),
            "average_similarity": summary.get("avg_cos_sim"),
            "vector_dimensions_positive": vector_dimensions_positive,
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = embedding_check_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_same_model_vector_fixture_through_api",
                    "precondition_clean": fixture["precondition_clean"],
                    "api_codes": fixture["api_codes"],
                    "configured_model_present": bool(configured_model),
                    "chunk_id_present": bool(fixture["chunk_id"]),
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "check_configured_embedding_against_stored_vector",
                    "http_status": checked["http_status"],
                    "code": checked["code"],
                    "summary": summary,
                    "result_chunk_ids": result_chunk_ids,
                    "vector_dimensions_positive": vector_dimensions_positive,
                    "raw_sha256": checked["raw_sha256"],
                },
                {
                    "name": "cleanup_embedding_check_dataset_through_api",
                    "code": dataset_cleanup["code"],
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": dataset_cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "model": configured_model,
                "sampled": 1,
                "valid": 1,
                "minimum_average_cosine_similarity": 0.9,
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-EMBEDDING-COMPATIBILITY-001",
                    "summary": f"{group} configured embedding did not match its stored vector space",
                    "code_location": "api/apps/services/dataset_api_service.py:check_embedding",
                }
            ],
        }

    return _run_case(case_id, execute)


def ingestion_poll_ready(data: Any) -> bool:
    payload = data if isinstance(data, dict) else {}
    logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
    mindmap_logs = [item for item in logs if isinstance(item, dict) and item.get("task_type") == "Mindmap"]
    return bool(mindmap_logs) and int(payload.get("total") or 0) >= len(mindmap_logs)


def _poll_mindmap_ingestion_log(
    case_id: str,
    group: str,
    auth: str,
    dataset_id: str,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    attempts: list[dict[str, Any]] = []
    last_response: dict[str, Any] | None = None
    selected_log: dict[str, Any] = {}
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        response = _list_ingestions_request(
            case_id,
            group,
            auth,
            f"poll_mindmap_ingestion_log_{attempt:03d}",
            dataset_id,
        )
        last_response = response
        data = response["data"] if isinstance(response["data"], dict) else {}
        logs = data.get("logs") if isinstance(data.get("logs"), list) else []
        candidates = [item for item in logs if isinstance(item, dict) and item.get("task_type") == "Mindmap"]
        attempts.append(
            {
                "attempt": attempt,
                "code": response["code"],
                "total": int(data.get("total") or 0),
                "mindmap_log_count": len(candidates),
                "raw_sha256": response["raw_sha256"],
            }
        )
        if response["code"] == 0 and ingestion_poll_ready(data):
            selected_log = candidates[0]
            break
        time.sleep(0.5)
    return {
        "response": last_response
        or {
            "http_status": None,
            "code": None,
            "data": None,
            "raw_sha256": None,
        },
        "log": selected_log,
        "attempts": attempts,
        "timed_out": not bool(selected_log),
    }


def _run_ingestion_log_case(case_id: str, *, require_detail: bool) -> dict[str, Any]:
    prefix = case_id.lower().replace("tc-", "fresh-")

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_index_fixture(case_id, group, owner, prefix, with_chunk=False)
        dataset_id = fixture["dataset_id"]
        run = _run_index_request(
            case_id,
            group,
            owner["auth"],
            "run_mindmap_to_generate_dataset_ingestion_log",
            dataset_id,
            "mindmap",
        )
        run_data = run["data"] if isinstance(run["data"], dict) else {}
        task_id = str(run_data.get("task_id") or "")
        polled = _poll_mindmap_ingestion_log(case_id, group, owner["auth"], dataset_id)
        listed = polled["response"]
        list_data = listed["data"] if isinstance(listed["data"], dict) else {}
        log = polled["log"]
        log_id = str(log.get("id") or "")
        database_log = _pipeline_log_snapshot(group, dataset_id, log_id)
        detail = (
            _get_ingestion_request(
                case_id,
                group,
                owner["auth"],
                "get_selected_ingestion_log",
                dataset_id,
                log_id,
            )
            if require_detail and log_id
            else None
        )
        detail_data = detail["data"] if detail is not None and isinstance(detail["data"], dict) else {}
        index_cleanup = _delete_index_request(
            case_id,
            group,
            owner["auth"],
            "cleanup_mindmap_after_ingestion_log",
            dataset_id,
            "mindmap",
        )
        task_state_after = _index_task_snapshot(group, dataset_id, "mindmap")
        original_task_after = _task_row_snapshot(group, task_id)
        index_cleanup_succeeded = index_cleanup["code"] == 0 and task_state_after.get("task_id") == "" and original_task_after.get("count") == 0
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_ingestion_log_dataset",
            [dataset_id],
        )
        dataset_count_after = int(_dataset_snapshot(group, dataset_id).get("count") or 0)
        retained_log_count_after = int(_pipeline_log_snapshot(group, dataset_id, log_id).get("count") or 0)
        dataset_cleanup_succeeded = dataset_cleanup["code"] == 0 and dataset_count_after == 0
        observed = {
            "precondition_clean": fixture["precondition_clean"],
            "fixture_api_codes": [*fixture["api_codes"], run["code"]],
            "list_http_status": listed["http_status"],
            "list_code": listed["code"],
            "list_total": int(list_data.get("total") or 0),
            "log_id": log_id,
            "list_log_matches_dataset": str(log.get("kb_id") or "") == dataset_id,
            "list_log_task_type": log.get("task_type"),
            "database_log_matches": database_log.get("count") == 1 and database_log.get("id") == log_id and database_log.get("dataset_id") == dataset_id and database_log.get("task_type") == "Mindmap",
            "detail_http_status": detail["http_status"] if detail else None,
            "detail_code": detail["code"] if detail else None,
            "detail_id_matches": str(detail_data.get("id") or "") == log_id,
            "detail_dataset_matches": str(detail_data.get("kb_id") or "") == dataset_id,
            "detail_dsl_present": "dsl" in detail_data,
            "index_cleanup_succeeded": index_cleanup_succeeded,
            "dataset_delete_code": dataset_cleanup["code"],
            "dataset_count_after": dataset_count_after,
            "retained_log_count_after": retained_log_count_after,
        }
        passed = ingestion_log_contract_ok(observed, require_detail=require_detail)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_dataset_and_run_mindmap_ingestion",
                    "precondition_clean": fixture["precondition_clean"],
                    "fixture_api_codes": fixture["api_codes"],
                    "run_http_status": run["http_status"],
                    "run_code": run["code"],
                    "task_id_present": bool(task_id),
                    "raw_sha256": [
                        *fixture["raw_sha256"],
                        run["raw_sha256"],
                    ],
                },
                {
                    "name": "poll_and_validate_dataset_ingestion_log",
                    "timed_out": polled["timed_out"],
                    "attempts": polled["attempts"],
                    "list_http_status": listed["http_status"],
                    "list_code": listed["code"],
                    "list_total": int(list_data.get("total") or 0),
                    "selected_log_id_present": bool(log_id),
                    "selected_log_task_type": log.get("task_type"),
                    "database_log": database_log,
                },
                *(
                    [
                        {
                            "name": "get_selected_ingestion_log_detail",
                            "http_status": detail["http_status"],
                            "code": detail["code"],
                            "id_matches": observed["detail_id_matches"],
                            "dataset_matches": observed["detail_dataset_matches"],
                            "dsl_present": observed["detail_dsl_present"],
                            "raw_sha256": detail["raw_sha256"],
                        }
                    ]
                    if detail is not None
                    else []
                ),
                {
                    "name": "cleanup_ingestion_index_and_dataset_through_api",
                    "index_cleanup_code": index_cleanup["code"],
                    "index_cleanup_succeeded": index_cleanup_succeeded,
                    "dataset_cleanup_code": dataset_cleanup["code"],
                    "dataset_cleanup_succeeded": dataset_cleanup_succeeded,
                    "dataset_count_after": dataset_count_after,
                    "retained_log_count_after": retained_log_count_after,
                    "retained_log_note": ("pipeline operation logs are historical records and the dataset delete API has no log-delete operation"),
                    "raw_sha256": [
                        index_cleanup["raw_sha256"],
                        dataset_cleanup["raw_sha256"],
                    ],
                },
            ],
            "oracle": {
                "log_type": "dataset",
                "task_type": "Mindmap",
                "detail_required": require_detail,
                "database_validation": "read only",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-INGESTION-DETAIL-001" if require_detail else "DD-INGESTION-LIST-001",
                    "summary": (f"{group} dataset ingestion log {'detail' if require_detail else 'list'} did not match persisted log"),
                    "code_location": "api/apps/services/dataset_api_service.py",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_ingest001() -> dict[str, Any]:
    return _run_ingestion_log_case("TC-DD-INGEST-001", require_detail=False)


def run_ingest002() -> dict[str, Any]:
    return _run_ingestion_log_case("TC-DD-INGEST-002", require_detail=True)


def run_ingest003() -> dict[str, Any]:
    case_id = "TC-DD-INGEST-003"
    prefix = "fresh-dd-ingest-003"
    status_keys = {
        "unstart_count",
        "running_count",
        "cancel_count",
        "done_count",
        "fail_count",
    }

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        fixture = _create_index_fixture(case_id, group, owner, prefix, with_chunk=True)
        dataset_id = fixture["dataset_id"]
        database_snapshot = _dataset_snapshot(group, dataset_id)
        summary_response = _get_ingestion_summary_request(
            case_id,
            group,
            owner["auth"],
            "get_ingestion_summary",
            dataset_id,
        )
        summary = summary_response["data"] if isinstance(summary_response["data"], dict) else {}
        status = summary.get("status") if isinstance(summary.get("status"), dict) else {}
        status_shape_valid = (
            set(status) == status_keys
            and all(isinstance(status[key], int) and status[key] >= 0 for key in status_keys)
            and sum(int(status[key]) for key in status_keys) == int(database_snapshot.get("doc_num") or 0)
        )
        api_counts = {
            "doc_num": int(summary.get("doc_num") or 0),
            "chunk_num": int(summary.get("chunk_num") or 0),
            "token_num": int(summary.get("token_num") or 0),
        }
        database_counts = {
            "doc_num": int(database_snapshot.get("doc_num") or 0),
            "chunk_num": int(database_snapshot.get("chunk_num") or 0),
            "token_num": int(database_snapshot.get("token_num") or 0),
        }
        dataset_cleanup = _delete_ids(
            case_id,
            group,
            owner["auth"],
            "cleanup_ingestion_summary_dataset",
            [dataset_id],
        )
        cleanup_succeeded = dataset_cleanup["code"] == 0 and _dataset_snapshot(group, dataset_id).get("count") == 0
        observed = {
            "precondition_clean": fixture["precondition_clean"],
            "fixture_api_codes": fixture["api_codes"],
            "summary_http_status": summary_response["http_status"],
            "summary_code": summary_response["code"],
            "api_counts": api_counts,
            "database_counts": database_counts,
            "status_shape_valid": status_shape_valid,
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = ingestion_summary_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_ingestion_summary_fixture_through_api",
                    "precondition_clean": fixture["precondition_clean"],
                    "api_codes": fixture["api_codes"],
                    "chunk_id_present": bool(fixture["chunk_id"]),
                    "raw_sha256": fixture["raw_sha256"],
                },
                {
                    "name": "get_and_validate_ingestion_summary",
                    "http_status": summary_response["http_status"],
                    "code": summary_response["code"],
                    "api_counts": api_counts,
                    "database_counts": database_counts,
                    "status": status,
                    "status_shape_valid": status_shape_valid,
                    "raw_sha256": summary_response["raw_sha256"],
                },
                {
                    "name": "cleanup_ingestion_summary_dataset_through_api",
                    "code": dataset_cleanup["code"],
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": dataset_cleanup["raw_sha256"],
                },
            ],
            "oracle": {
                "count_fields": ["doc_num", "chunk_num", "token_num"],
                "status_fields": sorted(status_keys),
                "database_validation": "read only",
            },
            "findings": []
            if passed
            else [
                {
                    "id": "DD-INGESTION-SUMMARY-001",
                    "summary": f"{group} ingestion summary did not match dataset counters/status",
                    "code_location": "api/apps/services/dataset_api_service.py:get_ingestion_summary",
                }
            ],
        }

    return _run_case(case_id, execute)


def run_val001() -> dict[str, Any]:
    return _run_case(
        "TC-DD-VAL-001",
        _validation_operation(
            "TC-DD-VAL-001",
            "fresh-dd-val-001",
            {"description": "test"},
            expected_code=101,
        ),
    )


def run_val002() -> dict[str, Any]:
    case_id = "TC-DD-VAL-002"
    original_name = "FreshDDVal002"
    requested_name = original_name

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], original_name)
        first = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_exact_duplicate_original",
            {"name": original_name},
        )
        second = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_exact_duplicate_second",
            {"name": requested_name},
        )
        first_data = first["data"] if isinstance(first["data"], dict) else {}
        second_data = second["data"] if isinstance(second["data"], dict) else {}
        ids = [str(item.get("id")) for item in (first_data, second_data) if item.get("id")]
        snapshots = [_dataset_snapshot(group, dataset_id) for dataset_id in ids]
        names = [snapshot.get("name") for snapshot in snapshots]
        expected_second = f"{requested_name}(1)"
        passed = first["code"] == second["code"] == 0 and first_data.get("name") == original_name and second_data.get("name") == expected_second and names == [original_name, expected_second]
        cleanup = len(ids) == 2 and _delete_ids(case_id, group, owner["auth"], "cleanup_exact_duplicate_datasets", ids)["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_exact_duplicate",
                    "response_codes": [first["code"], second["code"]],
                    "original_name_matches": first_data.get("name") == original_name,
                    "duplicate_auto_renamed": second_data.get("name") == expected_second,
                    "raw_sha256": [first["raw_sha256"], second["raw_sha256"]],
                },
                {
                    "name": "read_only_exact_duplicate_verification",
                    "database_names_match": names == [original_name, expected_second],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"second_name": expected_second, "response_codes": [0, 0]},
        }

    return _run_case(case_id, execute)


def run_val003() -> dict[str, Any]:
    case_id = "TC-DD-VAL-003"
    prefix = "fresh-dd-val-003"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        target = _tenant_default_embedding(group, owner["tenant_id"])
        created = _create_dataset(case_id, group, owner["auth"], "create_update_validation_fixture", {"name": prefix})
        dataset_id = str((created["data"] or {}).get("id") or "") if isinstance(created["data"], dict) else ""
        response = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "update_authorized_embedding",
            dataset_id,
            {"embedding_model": target},
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        snapshot = _dataset_snapshot(group, dataset_id)
        passed = created["code"] == response["code"] == 0 and data.get("embedding_model") == target and snapshot.get("embedding_model") == target
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_update_validation_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "update_to_authorized_embedding",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "response_matches_target": data.get("embedding_model") == target,
                    "database_matches_target": snapshot.get("embedding_model") == target,
                    "raw_sha256": response["raw_sha256"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"response": [200, 0], "availability_check_passed": True},
        }

    return _run_case(case_id, execute)


def run_val004() -> dict[str, Any]:
    return _run_case(
        "TC-DD-VAL-004",
        _validation_operation(
            "TC-DD-VAL-004",
            "fresh-dd-val-004",
            {
                "name": "fresh-dd-val-004",
                "parser_config": {
                    "chunk_token_num": "not_a_number",
                    "layout_recognize": "DeepDOC",
                },
            },
            expected_code=101,
        ),
    )


def run_val005() -> dict[str, Any]:
    return _run_case(
        "TC-DD-VAL-005",
        _validation_operation(
            "TC-DD-VAL-005",
            "fresh-dd-val-005",
            {
                "name": "fresh-dd-val-005",
                "embedding_model": "non-existent-model@Ollama",
            },
            expected_code=None,
        ),
    )


def _run_empty_embedding_case(case_id: str, prefix: str) -> dict[str, Any]:
    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        original_embedding = _tenant_default_embedding(group, owner["tenant_id"])
        created = _create_dataset(case_id, group, owner["auth"], "create_empty_embedding_fixture", {"name": prefix})
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        inherited = _dataset_snapshot(group, dataset_id).get("embedding_model")
        physical = _set_empty_embedding(group, dataset_id)
        orm_value = _orm_dataset_embedding(group, dataset_id)
        detail = _get_dataset(case_id, group, owner["auth"], "read_empty_embedding_via_api", dataset_id)
        detail_data = detail["data"] if isinstance(detail["data"], dict) else {}
        restore = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "restore_original_embedding",
            dataset_id,
            {"embedding_model": original_embedding},
        )
        restored_value = _dataset_snapshot(group, dataset_id).get("embedding_model")
        observed = {
            **physical,
            "orm_value": orm_value,
            "api_value": detail_data.get("embedding_model"),
            "restored": restore["code"] == 0 and restored_value == original_embedding,
        }
        passed = created["code"] == 0 and inherited == original_embedding and detail["code"] == 0 and empty_embedding_storage_contract_ok(group, observed)
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_empty_embedding_fixture", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_fixture_and_confirm_default_embedding",
                    "code": created["code"],
                    "default_embedding_inherited": inherited == original_embedding,
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "plan_authorized_direct_empty_embedding_update",
                    **physical,
                    "orm_value_is_empty": orm_value == "",
                },
                {
                    "name": "api_read_and_restore",
                    "detail_code": detail["code"],
                    "api_value_is_empty": detail_data.get("embedding_model") == "",
                    "restore_code": restore["code"],
                    "restored_to_original": restored_value == original_embedding,
                    "raw_sha256": [detail["raw_sha256"], restore["raw_sha256"]],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "control_physical": "empty_string",
                "experiment_physical": "null",
                "orm_and_api_value": "",
                "restored": True,
            },
        }

    return _run_case(case_id, execute)


def run_gdb001() -> dict[str, Any]:
    return _run_empty_embedding_case("TC-DD-GDB-001", "fresh-dd-gdb-001")


def run_gdb002() -> dict[str, Any]:
    case_id = "TC-DD-GDB-002"
    prefix = "gaussdb-test-ds-tenant"
    names = (f"{prefix}-a", f"{prefix}-b")
    secondary_email = "dd-gdb-002-b@fresh.invalid"
    secondary_password = "DD-GDB-002-Fresh@1234"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        owner_clean = _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)

        existing_secondary = DB._email_count(group, [secondary_email])
        secondary_preclean = existing_secondary == 0
        if existing_secondary:
            prior_login = AUTH._login(
                case_id,
                group,
                "login_existing_secondary_for_precleanup",
                secondary_email,
                secondary_password,
            )
            if prior_login["code"] == 0 and prior_login["_auth"]:
                prior_tenant = _owner_id(group, secondary_email)
                _cleanup_prefix(
                    case_id,
                    group,
                    str(prior_login["_auth"]),
                    prior_tenant,
                    prefix,
                )
            secondary_preclean = DB._delete_user_via_admin(group, secondary_email)

        registration = DB._register(
            case_id,
            group,
            "register_secondary_tenant_owner",
            {
                "email": secondary_email,
                "nickname": "DDGDB002TenantB",
                "password": secondary_password,
            },
        )
        secondary_login = AUTH._login(
            case_id,
            group,
            "login_secondary_tenant_owner",
            secondary_email,
            secondary_password,
        )
        secondary_auth = str(secondary_login.get("_auth") or "")
        if not secondary_auth:
            raise RuntimeError("secondary tenant owner login failed")
        secondary_tenant_id = _owner_id(group, secondary_email)

        created_a = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_tenant_a_dataset",
            {"name": names[0]},
        )
        created_b = _create_dataset(
            case_id,
            group,
            secondary_auth,
            "create_tenant_b_dataset",
            {"name": names[1]},
        )
        data_a = created_a["data"] if isinstance(created_a["data"], dict) else {}
        data_b = created_b["data"] if isinstance(created_b["data"], dict) else {}
        dataset_a_id = str(data_a.get("id") or "")
        dataset_b_id = str(data_b.get("id") or "")

        search_params = {"ext": json.dumps({"keywords": prefix})}
        listed_a = _list_datasets(
            case_id,
            group,
            owner["auth"],
            "list_as_tenant_a",
            search_params,
        )
        listed_b = _list_datasets(
            case_id,
            group,
            secondary_auth,
            "list_as_tenant_b",
            search_params,
        )

        def result_ids(response: dict[str, Any]) -> list[str]:
            data = response["data"] if isinstance(response["data"], list) else []
            return sorted(str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id"))

        a_result_ids = result_ids(listed_a)
        b_result_ids = result_ids(listed_b)
        rows = _dataset_tenant_rows(group, names)
        rows_by_name = {row["name"]: row for row in rows}
        owners_match = (
            len(rows_by_name) == 2
            and rows_by_name[names[0]]["id"] == dataset_a_id
            and rows_by_name[names[0]]["tenant_id"] == owner["tenant_id"]
            and rows_by_name[names[1]]["id"] == dataset_b_id
            and rows_by_name[names[1]]["tenant_id"] == secondary_tenant_id
        )

        delete_a = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_tenant_a_dataset",
                [dataset_a_id],
            )
            if dataset_a_id
            else {"code": None, "raw_sha256": None}
        )
        delete_b = (
            _delete_ids(
                case_id,
                group,
                secondary_auth,
                "cleanup_tenant_b_dataset",
                [dataset_b_id],
            )
            if dataset_b_id
            else {"code": None, "raw_sha256": None}
        )
        user_cleanup = DB._disable_and_delete_user(case_id, group, secondary_email)
        cleanup_succeeded = (
            delete_a["code"] == 0
            and delete_b["code"] == 0
            and not _dataset_tenant_rows(group, names)
            and user_cleanup["disabled"]["code"] == 0
            and user_cleanup["deleted"]["code"] == 0
            and DB._email_count(group, [secondary_email]) == 0
        )
        observed = {
            "api_codes": [
                registration["code"],
                secondary_login["code"],
                created_a["code"],
                created_b["code"],
                listed_a["code"],
                listed_b["code"],
            ],
            "database_row_count": len(rows),
            "tenant_ids_distinct": owner["tenant_id"] != secondary_tenant_id,
            "database_owners_match": owners_match,
            "a_result_ids": a_result_ids,
            "b_result_ids": b_result_ids,
            "expected_a_id": dataset_a_id,
            "expected_b_id": dataset_b_id,
            "cleanup_succeeded": cleanup_succeeded,
        }
        passed = owner_clean and secondary_preclean and tenant_isolation_contract_ok(observed)
        return {
            "status": "PASS" if passed else "FAIL",
            "steps": [
                {
                    "name": "create_two_independent_tenant_owners_and_datasets",
                    "precondition_clean": owner_clean and secondary_preclean,
                    "registration_code": registration["code"],
                    "secondary_login_code": secondary_login["code"],
                    "dataset_create_codes": [created_a["code"], created_b["code"]],
                    "tenant_ids_distinct": owner["tenant_id"] != secondary_tenant_id,
                    "raw_sha256": [
                        registration["raw_sha256"],
                        secondary_login["raw_sha256"],
                        created_a["raw_sha256"],
                        created_b["raw_sha256"],
                    ],
                },
                {
                    "name": "verify_tenant_scoped_dataset_lists",
                    "list_codes": [listed_a["code"], listed_b["code"]],
                    "tenant_a_only_own_dataset": a_result_ids == [dataset_a_id],
                    "tenant_b_only_own_dataset": b_result_ids == [dataset_b_id],
                    "tenant_a_result_count": len(a_result_ids),
                    "tenant_b_result_count": len(b_result_ids),
                    "raw_sha256": [
                        listed_a["raw_sha256"],
                        listed_b["raw_sha256"],
                    ],
                },
                {
                    "name": "read_only_database_tenant_verification",
                    "database_row_count": len(rows),
                    "tenant_ids_distinct": owner["tenant_id"] != secondary_tenant_id,
                    "database_owners_match": owners_match,
                },
                {
                    "name": "cleanup_datasets_and_secondary_user_through_apis",
                    "dataset_delete_codes": [delete_a["code"], delete_b["code"]],
                    "user_disable_code": user_cleanup["disabled"]["code"],
                    "user_delete_code": user_cleanup["deleted"]["code"],
                    "cleanup_succeeded": cleanup_succeeded,
                    "raw_sha256": [
                        delete_a["raw_sha256"],
                        delete_b["raw_sha256"],
                        user_cleanup["disabled"]["raw_sha256"],
                        user_cleanup["deleted"]["raw_sha256"],
                    ],
                },
            ],
            "oracle": {
                "normalized_control_and_experiment_behavior": [
                    "tenant_a_sees_only_a",
                    "tenant_b_sees_only_b",
                ],
                "database_rows": 2,
                "literal_cross_group_uuid_equality_required": False,
            },
        }

    return _run_case(case_id, execute)


def run_gdb003() -> dict[str, Any]:
    case_id = "TC-DD-GDB-003"
    prefix = "fresh-dd-gdb-003"
    expected_ext = {"custom_field": "value", "nested": {"key2": [1, 2, 3]}}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        response = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_complex_json_dataset",
            {
                "name": prefix,
                "parser_config": {
                    "chunk_token_num": 512,
                    "layout_recognize": "Plain Text",
                    "ext": expected_ext,
                },
            },
        )
        data = response["data"] if isinstance(response["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        snapshot = _dataset_snapshot(group, dataset_id)
        config = snapshot.get("parser_config") or {}
        passed = response["code"] == 0 and config.get("chunk_token_num") == 512 and config.get("layout_recognize") == "Plain Text" and config.get("ext") == expected_ext
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_complex_json_dataset", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_complex_parser_json",
                    "http_status": response["http_status"],
                    "code": response["code"],
                    "raw_sha256": response["raw_sha256"],
                },
                {
                    "name": "read_only_json_structure_verification",
                    "scalar_fields_match": config.get("chunk_token_num") == 512 and config.get("layout_recognize") == "Plain Text",
                    "nested_object_matches": config.get("ext") == expected_ext,
                    "nested_array_matches": (config.get("ext") or {}).get("nested", {}).get("key2") == [1, 2, 3],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"json_structure_preserved": True},
        }

    return _run_case(case_id, execute)


def run_gdb004() -> dict[str, Any]:
    case_id = "TC-DD-GDB-004"
    prefix = "fresh-dd-gdb-004"
    names = (
        f"{prefix}-percent%literal",
        f"{prefix}-percentXliteral",
        f"{prefix}-under_score",
        f"{prefix}-underXscore",
    )

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = [
            _create_dataset(
                case_id,
                group,
                owner["auth"],
                f"create_special_name_{index}",
                {"name": name},
            )
            for index, name in enumerate(names)
        ]
        percent = _list_datasets(
            case_id,
            group,
            owner["auth"],
            "search_literal_percent",
            {"ext": json.dumps({"keywords": "percent%literal"})},
        )
        underscore = _list_datasets(
            case_id,
            group,
            owner["auth"],
            "search_literal_underscore",
            {"ext": json.dumps({"keywords": "under_score"})},
        )
        percent_names = [item.get("name") for item in percent["data"] or [] if isinstance(item, dict)]
        underscore_names = [item.get("name") for item in underscore["data"] or [] if isinstance(item, dict)]
        rows = _dataset_rows_by_prefix(group, owner["tenant_id"], prefix)
        passed = all(item["code"] == 0 for item in created) and percent["code"] == underscore["code"] == 0 and percent_names == [names[0]] and underscore_names == [names[2]]
        cleanup = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_special_name_datasets",
                [row["id"] for row in rows],
            )["code"]
            == 0
        )
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_percent_underscore_and_control_names",
                    "success_count": sum(item["code"] == 0 for item in created),
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "verify_literal_like_searches",
                    "percent_result_count": len(percent_names),
                    "percent_only_literal_match": percent_names == [names[0]],
                    "underscore_result_count": len(underscore_names),
                    "underscore_only_literal_match": underscore_names == [names[2]],
                    "raw_sha256": [percent["raw_sha256"], underscore["raw_sha256"]],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"percent_literal": True, "underscore_literal": True},
        }

    return _run_case(case_id, execute)


def run_gdb005() -> dict[str, Any]:
    case_id = "TC-DD-GDB-005"
    prefix = "fresh-dd-gdb-005"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = []
        for index in range(25):
            created.append(
                _create_dataset(
                    case_id,
                    group,
                    owner["auth"],
                    f"create_ordered_dataset_{index:02d}",
                    {"name": f"{prefix}-{index:02d}"},
                )
            )
            time.sleep(0.01)
        common = {
            "page_size": 10,
            "orderby": "create_time",
            "desc": "true",
            "ext": json.dumps({"keywords": prefix}),
        }
        pages = [
            _list_datasets(
                case_id,
                group,
                owner["auth"],
                f"list_ordered_page_{page}",
                {**common, "page": page},
            )
            for page in (1, 2, 3)
        ]
        page_ids = [[str(item.get("id")) for item in page["data"] or []] for page in pages]
        combined = [dataset_id for ids in page_ids for dataset_id in ids]
        database_ids = _ordered_dataset_ids(group, owner["tenant_id"], prefix, descending=True)
        observed = {
            "page_sizes": [len(ids) for ids in page_ids],
            "pairwise_disjoint": len(set(combined)) == len(combined),
            "stable_total": all(page["total_datasets"] == 25 for page in pages),
            "combined_matches_database": combined == database_ids,
            "descending_order": combined == database_ids,
        }
        passed = all(item["code"] == 0 for item in created) and pagination_contract_ok(observed, [10, 10, 5])
        cleanup = (
            _delete_ids(
                case_id,
                group,
                owner["auth"],
                "cleanup_ordered_datasets",
                database_ids,
            )["code"]
            == 0
        )
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_25_ordered_datasets",
                    "success_count": sum(item["code"] == 0 for item in created),
                    "database_count": len(database_ids),
                    "raw_sha256": [item["raw_sha256"] for item in created],
                },
                {
                    "name": "compare_three_api_pages_with_database_order",
                    **observed,
                    "api_id_sequence_fingerprint": DB._fingerprint(combined),
                    "database_id_sequence_fingerprint": DB._fingerprint(database_ids),
                    "raw_sha256": [page["raw_sha256"] for page in pages],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"page_sizes": [10, 10, 5], "database_order_match": True},
        }

    return _run_case(case_id, execute)


def run_dd062() -> dict[str, Any]:
    return _run_empty_embedding_case("TC-DD-062", "fresh-dd-062")


def run_dd063() -> dict[str, Any]:
    case_id = "TC-DD-063"
    prefix = "fresh-dd-063"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_empty_description_dataset",
            {"name": prefix, "description": ""},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        initial_physical = _description_physical(group, dataset_id)
        nonempty = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "update_description_nonempty",
            dataset_id,
            {"description": "fresh nonempty description"},
        )
        empty = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "update_description_empty",
            dataset_id,
            {"description": ""},
        )
        detail = _get_dataset(case_id, group, owner["auth"], "read_final_description", dataset_id)
        detail_data = detail["data"] if isinstance(detail["data"], dict) else {}
        final_physical = _description_physical(group, dataset_id)
        dialect_ok = (
            initial_physical["is_empty"] and final_physical["is_empty"] and detail_data.get("description") == ""
            if group == "control"
            else initial_physical["is_null"] and final_physical["is_null"] and detail_data.get("description") is None
        )
        passed = created["code"] == nonempty["code"] == empty["code"] == detail["code"] == 0 and dialect_ok
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_description_dataset", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_empty_description",
                    "code": created["code"],
                    "physical": initial_physical,
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "update_nonempty_then_empty",
                    "response_codes": [nonempty["code"], empty["code"]],
                    "final_physical": final_physical,
                    "api_value_is_empty": detail_data.get("description") == "",
                    "api_value_is_null": detail_data.get("description") is None,
                    "dialect_contract_ok": dialect_ok,
                    "raw_sha256": [
                        nonempty["raw_sha256"],
                        empty["raw_sha256"],
                        detail["raw_sha256"],
                    ],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "control": "physical/API empty string",
                "experiment": "physical/API null",
            },
        }

    return _run_case(case_id, execute)


def run_dd064() -> dict[str, Any]:
    case_id = "TC-DD-064"
    prefix = "fresh-dd-064"
    ext = {"custom_fields": {"key1": "value1", "key2": [1, 2, 3]}}

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        created = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_empty_parser_config",
            {"name": prefix, "parser_config": {}},
        )
        data = created["data"] if isinstance(created["data"], dict) else {}
        dataset_id = str(data.get("id") or "")
        initial = _dataset_snapshot(group, dataset_id).get("parser_config") or {}
        updated = _update_dataset(
            case_id,
            group,
            owner["auth"],
            "update_complex_parser_config",
            dataset_id,
            {
                "parser_config": {
                    "chunk_token_num": 512,
                    "layout_recognize": "Plain Text",
                    "ext": ext,
                }
            },
        )
        final = _dataset_snapshot(group, dataset_id).get("parser_config") or {}
        passed = (
            created["code"] == updated["code"] == 0
            and bool(initial)
            and final.get("chunk_token_num") == 512
            and final.get("layout_recognize") == "Plain Text"
            and final.get("custom_fields") == ext["custom_fields"]
            and "ext" not in final
        )
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_json_dataset", [dataset_id])["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "verify_empty_object_normalized_to_defaults",
                    "create_code": created["code"],
                    "initial_config_nonempty": bool(initial),
                    "initial_key_count": len(initial),
                    "raw_sha256": created["raw_sha256"],
                },
                {
                    "name": "verify_update_ext_flattening_and_nested_values",
                    "update_code": updated["code"],
                    "scalar_fields_match": final.get("chunk_token_num") == 512 and final.get("layout_recognize") == "Plain Text",
                    "custom_fields_at_root": final.get("custom_fields") == ext["custom_fields"],
                    "ext_key_absent": "ext" not in final,
                    "nested_array_matches": (final.get("custom_fields") or {}).get("key2") == [1, 2, 3],
                    "raw_sha256": updated["raw_sha256"],
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {"create_defaults": True, "update_ext_flattened": True},
        }

    return _run_case(case_id, execute)


def run_dd065() -> dict[str, Any]:
    case_id = "TC-DD-065"
    prefix = "fresh-dd-065"

    def execute(group: str, owner: dict[str, str]) -> dict[str, Any]:
        _cleanup_prefix(case_id, group, owner["auth"], owner["tenant_id"], prefix)
        empty = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_empty_string_description",
            {"name": f"{prefix}-empty", "description": ""},
        )
        null = _create_dataset(
            case_id,
            group,
            owner["auth"],
            "create_null_description",
            {"name": f"{prefix}-null", "description": None},
        )
        empty_data = empty["data"] if isinstance(empty["data"], dict) else {}
        null_data = null["data"] if isinstance(null["data"], dict) else {}
        ids = [str(empty_data.get("id") or ""), str(null_data.get("id") or "")]
        empty_physical = _description_physical(group, ids[0])
        null_physical = _description_physical(group, ids[1])
        dialect_ok = (
            empty_physical["is_empty"] and empty_physical["length"] == 0 and null_physical["is_null"] and empty_data.get("description") == "" and null_data.get("description") is None
            if group == "control"
            else empty_physical["is_null"] and null_physical["is_null"] and empty_data.get("description") is None and null_data.get("description") is None
        )
        passed = empty["code"] == null["code"] == 0 and dialect_ok
        cleanup = _delete_ids(case_id, group, owner["auth"], "cleanup_null_empty_datasets", ids)["code"] == 0
        return {
            "status": "PASS" if passed and cleanup else "FAIL",
            "steps": [
                {
                    "name": "create_empty_and_null_description_datasets",
                    "response_codes": [empty["code"], null["code"]],
                    "empty_api_is_empty": empty_data.get("description") == "",
                    "empty_api_is_null": empty_data.get("description") is None,
                    "null_api_is_null": null_data.get("description") is None,
                    "raw_sha256": [empty["raw_sha256"], null["raw_sha256"]],
                },
                {
                    "name": "read_only_physical_dialect_verification",
                    "empty_description": empty_physical,
                    "null_description": null_physical,
                    "dialect_contract_ok": dialect_ok,
                },
                {"name": "cleanup_through_api", "cleanup_succeeded": cleanup},
            ],
            "oracle": {
                "control": ["empty string", "null"],
                "experiment": ["null", "null"],
            },
        }

    return _run_case(case_id, execute)


RUNNERS: dict[str, Callable[[], dict[str, Any]]] = {
    "TC-DD-001": run_dd001,
    "TC-DD-002": run_dd002,
    "TC-DD-003": run_dd003,
    "TC-DD-004": run_dd004,
    "TC-DD-005": run_dd005,
    "TC-DD-006": run_dd006,
    "TC-DD-007": run_dd007,
    "TC-DD-008": run_dd008,
    "TC-DD-009": run_dd009,
    "TC-DD-010": run_dd010,
    "TC-DD-011": run_dd011,
    "TC-DD-012": run_dd012,
    "TC-DD-013": run_dd013,
    "TC-DD-014": run_dd014,
    "TC-DD-015": run_dd015,
    "TC-DD-016": run_dd016,
    "TC-DD-017": run_dd017,
    "TC-DD-018": run_dd018,
    "TC-DD-019": run_dd019,
    "TC-DD-020": run_dd020,
    "TC-DD-021": run_dd021,
    "TC-DD-022": run_dd022,
    "TC-DD-023": run_dd023,
    "TC-DD-024": run_dd024,
    "TC-DD-025": run_dd025,
    "TC-DD-026": run_dd026,
    "TC-DD-027": run_dd027,
    "TC-DD-028": run_dd028,
    "TC-DD-029": run_dd029,
    "TC-DD-030": run_dd030,
    "TC-DD-031": run_dd031,
    "TC-DD-032": run_dd032,
    "TC-DD-033": run_dd033,
    "TC-DD-034": run_dd034,
    "TC-DD-035": run_dd035,
    "TC-DD-036": run_dd036,
    "TC-DD-037": run_dd037,
    "TC-DD-038": run_dd038,
    "TC-DD-039": run_dd039,
    "TC-DD-040": run_dd040,
    "TC-DD-041": run_dd041,
    "TC-DD-042": run_dd042,
    "TC-DD-043": run_dd043,
    "TC-DD-044": run_dd044,
    "TC-DD-045": run_dd045,
    "TC-DD-046": run_dd046,
    "TC-DD-047": run_dd047,
    "TC-DD-048": run_dd048,
    "TC-DD-049": run_dd049,
    "TC-DD-050": run_dd050,
    "TC-DD-051": run_dd051,
    "TC-DD-052": run_dd052,
    "TC-DD-053": run_dd053,
    "TC-DD-054": run_dd054,
    "TC-DD-055": run_dd055,
    "TC-DD-056": run_dd056,
    "TC-DD-057": run_dd057,
    "TC-DD-058": run_dd058,
    "TC-DD-059": run_dd059,
    "TC-DD-060": run_dd060,
    "TC-DD-061": run_dd061,
    "TC-DD-DEL-001": run_del001,
    "TC-DD-DEL-002": run_del002,
    "TC-DD-DEL-003": run_del003,
    "TC-DD-DEL-004": run_del004,
    "TC-DD-DEL-005": run_del005,
    "TC-DD-TAG-001": run_tag001,
    "TC-DD-TAG-002": run_tag002,
    "TC-DD-TAG-003": run_tag003,
    "TC-DD-TAG-004": run_tag004,
    "TC-DD-TAG-005": run_tag005,
    "TC-DD-META-001": run_meta001,
    "TC-DD-META-002": run_meta002,
    "TC-DD-META-003": run_meta003,
    "TC-DD-IDX-001": run_idx001,
    "TC-DD-IDX-002": run_idx002,
    "TC-DD-IDX-003": run_idx003,
    "TC-DD-IDX-004": run_idx004,
    "TC-DD-IDX-005": run_idx005,
    "TC-DD-EMB-001": run_emb001,
    "TC-DD-EMB-002": run_emb002,
    "TC-DD-INGEST-001": run_ingest001,
    "TC-DD-INGEST-002": run_ingest002,
    "TC-DD-INGEST-003": run_ingest003,
    "TC-DD-VAL-001": run_val001,
    "TC-DD-VAL-002": run_val002,
    "TC-DD-VAL-003": run_val003,
    "TC-DD-VAL-004": run_val004,
    "TC-DD-VAL-005": run_val005,
    "TC-DD-GDB-001": run_gdb001,
    "TC-DD-GDB-002": run_gdb002,
    "TC-DD-GDB-003": run_gdb003,
    "TC-DD-GDB-004": run_gdb004,
    "TC-DD-GDB-005": run_gdb005,
    "TC-DD-062": run_dd062,
    "TC-DD-063": run_dd063,
    "TC-DD-064": run_dd064,
    "TC-DD-065": run_dd065,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fresh dataset/document cases")
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
