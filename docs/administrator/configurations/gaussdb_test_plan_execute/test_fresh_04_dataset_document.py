import importlib.util
import inspect
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_04_dataset_document.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_04_dataset_document", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_inventory_contains_exactly_main_and_supplement_dataset_cases():
    module = load_module()

    assert len(module.CASE_TITLES) == 98
    assert "TC-DD-001" in module.CASE_TITLES
    assert "TC-DD-VAL-001" in module.CASE_TITLES
    assert "TC-DD-GDB-005" in module.CASE_TITLES


def test_val002_uses_exact_duplicate_name_contract():
    module = load_module()
    source = inspect.getsource(module.run_val002)

    assert 'original_name = "FreshDDVal002"' in source
    assert "requested_name = original_name" in source
    assert 'requested_name = "freshddval002"' not in source


def test_dataset_create_contract_requires_api_and_database_defaults():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "id_present": True,
        "name_matches": True,
        "database_count": 1,
        "tenant_matches": True,
        "embedding_inherited": True,
        "description_is_null": True,
        "defaults_match": True,
    }

    assert module.dataset_create_contract_ok(observed) is True
    assert module.dataset_create_contract_ok({**observed, "embedding_inherited": False}) is False


def test_validation_contract_requires_nonzero_code_and_no_database_residue():
    module = load_module()
    observed = {"http_status": 200, "code": 101, "database_count": 0}

    assert module.validation_rejection_contract_ok(observed, expected_code=101) is True
    assert module.validation_rejection_contract_ok({**observed, "database_count": 1}, expected_code=101) is False


def test_pagination_contract_requires_disjoint_pages_stable_total_and_database_order():
    module = load_module()
    observed = {
        "page_sizes": [10, 10, 5],
        "pairwise_disjoint": True,
        "stable_total": True,
        "combined_matches_database": True,
        "descending_order": True,
    }

    assert module.pagination_contract_ok(observed, [10, 10, 5]) is True
    assert module.pagination_contract_ok({**observed, "pairwise_disjoint": False}, [10, 10, 5]) is False


def test_empty_storage_contract_distinguishes_mysql_and_gaussdb():
    module = load_module()
    common = {"orm_value": "", "api_value": "", "restored": True}

    assert (
        module.empty_embedding_storage_contract_ok(
            "control",
            {**common, "physical_is_empty": True, "physical_length": 0},
        )
        is True
    )
    assert (
        module.empty_embedding_storage_contract_ok(
            "experiment",
            {**common, "physical_is_null": True, "physical_length": None},
        )
        is True
    )


def test_plan_authorized_gauss_fixture_connection_is_writable_and_schema_scoped():
    module = load_module()

    options = module.gauss_writable_fixture_options("fresh_schema")

    assert "default_transaction_read_only=off" in options
    assert "search_path=fresh_schema" in options
    assert "default_transaction_read_only=on" not in options


def test_tenant_isolation_contract_requires_exact_private_result_sets():
    module = load_module()
    observed = {
        "api_codes": [0, 0],
        "database_row_count": 2,
        "tenant_ids_distinct": True,
        "database_owners_match": True,
        "a_result_ids": ["dataset-a"],
        "b_result_ids": ["dataset-b"],
        "expected_a_id": "dataset-a",
        "expected_b_id": "dataset-b",
        "cleanup_succeeded": True,
    }

    assert module.tenant_isolation_contract_ok(observed) is True
    assert module.tenant_isolation_contract_ok({**observed, "a_result_ids": ["dataset-a", "dataset-b"]}) is False


def test_dataset_search_contract_requires_chunk_results_and_relevance_order():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0, 0, 0, 0, 0],
        "chunks_persisted": True,
        "chunk_shape_present": True,
        "expected_chunk_ids": ["chunk-a", "chunk-b"],
        "result_chunk_ids": ["chunk-a", "chunk-b"],
        "total": 2,
        "similarity_nonincreasing": True,
        "cleanup_succeeded": True,
    }

    assert module.dataset_search_contract_ok(observed) is True
    assert module.dataset_search_contract_ok({**observed, "result_chunk_ids": ["chunk-a"]}) is False
    assert module.dataset_search_contract_ok({**observed, "chunks_persisted": False}) is False


def test_dataset_cascade_contract_requires_every_saved_resource_to_be_removed():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "document_count_before": 3,
        "all_documents_have_multiple_chunks": True,
        "task_count_before": 3,
        "file_count_before": 3,
        "file_link_count_before": 3,
        "all_objects_exist_before": True,
        "all_chunks_exist_before": True,
        "delete_success_count": 1,
        "dataset_count_after": 0,
        "document_count_after": 0,
        "task_count_after": 0,
        "file_count_after": 0,
        "file_link_count_after": 0,
        "object_count_after": 0,
        "chunk_count_after": 0,
    }

    assert module.dataset_cascade_contract_ok(observed) is True
    assert module.dataset_cascade_contract_ok({**observed, "object_count_after": 1}) is False


def test_tag_contract_requires_persisted_chunks_exact_counts_and_visible_mutation():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "fixture_persisted": True,
        "expected_tag_counts": {"important": 2, "review": 1},
        "actual_tag_counts": {"important": 2, "review": 1},
        "mutation_visible_in_chunks": True,
        "cleanup_succeeded": True,
    }

    assert module.tag_contract_ok(observed) is True
    assert module.tag_contract_ok({**observed, "fixture_persisted": False}) is False


def test_flattened_metadata_contract_requires_exact_selected_dataset_result():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0, 0, 0],
        "expected_metadata": {"author": {"alice": ["document-a"]}},
        "actual_metadata": {"author": {"alice": ["document-a"]}},
        "unselected_document_absent": True,
        "cleanup_succeeded": True,
    }

    assert module.flattened_metadata_contract_ok(observed) is True
    assert (
        module.flattened_metadata_contract_ok(
            {
                **observed,
                "actual_metadata": {
                    "author": {
                        "alice": ["document-a"],
                        "bob": ["document-b"],
                    }
                },
                "unselected_document_absent": False,
            }
        )
        is False
    )


def test_metadata_config_contract_requires_api_and_parser_config_agreement():
    module = load_module()
    expected = {
        "metadata": [{"key": "category", "type": "string"}],
        "built_in_metadata": [],
    }
    observed = {
        "api_codes": [0, 0],
        "expected_config": expected,
        "update_config": expected,
        "api_config": expected,
        "database_config": expected,
        "independent_column_absent": True,
        "cleanup_succeeded": True,
    }

    assert module.metadata_config_contract_ok(observed) is True
    assert module.metadata_config_contract_ok({**observed, "database_config": {"metadata": []}}) is False


def test_document_upload_contract_requires_api_metadata_and_file_graph_agreement():
    module = load_module()
    observed = {
        "api_codes": [0, 0],
        "expected_count": 1,
        "response_count": 1,
        "database_count": 1,
        "file_count": 1,
        "file_link_count": 1,
        "response_ids": ["document-1"],
        "database_ids": ["document-1"],
        "actual_names": ["fresh.pdf"],
        "expected_names": ["fresh.pdf"],
        "actual_types": ["pdf"],
        "expected_types": ["pdf"],
        "actual_suffixes": ["pdf"],
        "expected_suffixes": ["pdf"],
        "actual_sizes": [128],
        "expected_sizes": [128],
        "initial_status_and_run_match": True,
        "database_rows_match_dataset": True,
        "file_rows_match_documents": True,
        "response_rows_match_database": True,
        "cleanup_succeeded": True,
    }

    assert module.document_upload_contract_ok(observed) is True
    assert module.document_upload_contract_ok({**observed, "file_link_count": 0}) is False


def test_upload_limit_contract_requires_413_without_residue_and_valid_small_upload():
    module = load_module()
    observed = {
        "over_limit_http_status": 413,
        "over_limit_metadata_count": 0,
        "over_limit_object_absent": True,
        "under_limit_code": 0,
        "under_limit_persisted": True,
        "isolated_process_stopped": True,
        "main_api_healthy": True,
        "cleanup_succeeded": True,
    }

    assert module.upload_limit_contract_ok(observed) is True
    assert module.upload_limit_contract_ok({**observed, "over_limit_http_status": 200}) is False


def test_document_list_contract_requires_counts_filters_database_rows_and_cleanup():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "expected_total": 2,
        "actual_total": 2,
        "expected_returned_count": 2,
        "actual_returned_count": 2,
        "ids_unique": True,
        "all_results_belong_to_dataset": True,
        "filter_exact": True,
        "rows_match_database": True,
        "cleanup_succeeded": True,
    }

    assert module.document_list_contract_ok(observed) is True
    assert module.document_list_contract_ok({**observed, "filter_exact": False}) is False


def test_document_update_contract_requires_api_database_side_effects_and_cleanup():
    module = load_module()
    observed = {
        "api_code": 0,
        "api_matches": True,
        "database_matches": True,
        "side_effects_match": True,
        "docstore_matches": True,
        "cleanup_succeeded": True,
    }

    assert module.document_update_contract_ok(observed) is True
    assert module.document_update_contract_ok({**observed, "docstore_matches": False}) is False


def test_document_delete_contract_requires_all_saved_resources_and_counts_removed():
    module = load_module()
    observed = {
        "api_code": 0,
        "expected_deleted_count": 1,
        "actual_deleted_count": 1,
        "precondition_matches": True,
        "metadata_resources_removed": True,
        "docstore_resources_removed": True,
        "storage_objects_removed": True,
        "dataset_counts_zero": True,
        "cleanup_succeeded": True,
    }

    assert module.document_delete_contract_ok(observed) is True
    assert module.document_delete_contract_ok({**observed, "storage_objects_removed": False}) is False


def test_document_parse_contract_requires_trigger_lifecycle_final_state_chunks_and_cleanup():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "trigger_matches": True,
        "lifecycle_matches": True,
        "final_api_matches": True,
        "chunk_contract_matches": True,
        "cleanup_succeeded": True,
    }

    assert module.document_parse_contract_ok(observed) is True
    assert module.document_parse_contract_ok({**observed, "final_api_matches": False}) is False


def test_chunk_operation_contract_requires_precondition_readback_docstore_counts_cleanup():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "precondition_matches": True,
        "api_readback_matches": True,
        "docstore_matches": True,
        "counter_matches": True,
        "cleanup_succeeded": True,
    }

    assert module.chunk_operation_contract_ok(observed) is True
    assert module.chunk_operation_contract_ok({**observed, "counter_matches": False}) is False


def test_retrieval_contract_requires_ready_fixtures_sources_sort_threshold_shape_cleanup():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "fixtures_ready": True,
        "result_count_positive": True,
        "expected_sources_present": True,
        "only_selected_sources": True,
        "similarity_nonincreasing": True,
        "threshold_satisfied": True,
        "chunk_shape_matches": True,
        "cleanup_succeeded": True,
    }

    assert module.retrieval_contract_ok(observed) is True
    assert module.retrieval_contract_ok({**observed, "expected_sources_present": False}) is False


def test_threshold_filter_contract_allows_empty_high_threshold_subset():
    module = load_module()

    assert (
        module.threshold_filter_contract_ok(
            baseline_ids=["chunk-a"],
            filtered_chunks=[],
            threshold=0.7,
        )
        is True
    )
    assert (
        module.threshold_filter_contract_ok(
            baseline_ids=["chunk-a"],
            filtered_chunks=[{"id": "chunk-b", "similarity": 0.8}],
            threshold=0.7,
        )
        is False
    )


def test_thumbnail_contract_requires_fixture_mapping_value_semantics_and_cleanup():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "precondition_clean": True,
        "document_fixture_ready": True,
        "parse_requirement_satisfied": True,
        "response_mapping_exact": True,
        "thumbnail_value_valid": True,
        "expected_empty_satisfied": True,
        "cleanup_succeeded": True,
    }

    assert module.thumbnail_contract_ok(observed) is True
    assert module.thumbnail_contract_ok({**observed, "response_mapping_exact": False}) is False


def test_private_dataset_denial_contract_requires_exact_denial_no_leak_and_cleanup():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "precondition_clean": True,
        "tenant_ids_distinct": True,
        "private_dataset_ready": True,
        "denial_http_status": 200,
        "denial_code": 102,
        "denial_message_matches": True,
        "data_not_leaked": True,
        "cleanup_succeeded": True,
    }

    assert module.private_dataset_denial_contract_ok(observed) is True
    assert module.private_dataset_denial_contract_ok({**observed, "data_not_leaked": False}) is False


def test_invalid_token_contract_requires_http_and_body_401_without_data():
    module = load_module()
    observed = {
        "http_status": 401,
        "code": 401,
        "unauthorized_message": True,
        "data_not_leaked": True,
    }

    assert module.invalid_token_contract_ok(observed) is True
    assert module.invalid_token_contract_ok({**observed, "code": 1}) is False


def test_concurrency_contract_requires_bounded_completion_consistency_resources_cleanup():
    module = load_module()
    observed = {
        "setup_code": 0,
        "precondition_clean": True,
        "max_workers": 2,
        "group_within_timeout": True,
        "all_tasks_completed": True,
        "all_requests_succeeded": True,
        "upload_count": 5,
        "database_document_count": 5,
        "document_graph_consistent": True,
        "update_persisted": True,
        "list_observed_dataset": True,
        "resource_watermark_observed": True,
        "cleanup_succeeded": True,
    }

    assert module.concurrency_contract_ok(observed) is True
    assert module.concurrency_contract_ok({**observed, "database_document_count": 4}) is False


def test_latency_summary_reports_linear_interpolated_p50_and_p95():
    module = load_module()

    summary = module.latency_summary([1.0, 2.0, 3.0, 4.0, 5.0])

    assert summary == {
        "sample_count": 5,
        "minimum_seconds": 1.0,
        "maximum_seconds": 5.0,
        "p50_seconds": 3.0,
        "p95_seconds": 4.8,
    }


def test_performance_baseline_contract_requires_fixture_measurements_and_cleanup():
    module = load_module()
    observed = {
        "fixture_api_codes_all_zero": True,
        "database_document_count": 100,
        "database_chunk_count": 100,
        "docstore_chunk_count": 100,
        "selected_document_counts_match": True,
        "measurement_shape_matches": True,
        "all_measurement_requests_succeeded": True,
        "all_operation_contracts_match": True,
        "all_latencies_positive": True,
        "cleanup_succeeded": True,
    }

    assert module.performance_baseline_contract_ok(observed) is True
    assert module.performance_baseline_contract_ok({**observed, "database_chunk_count": 99}) is False


def test_docstore_dataset_cleanup_contract_is_backend_specific():
    module = load_module()

    assert (
        module.docstore_dataset_cleanup_contract_ok(
            "control",
            {"index_exists": False, "total": 0, "existing_ids": []},
        )
        is True
    )
    assert (
        module.docstore_dataset_cleanup_contract_ok(
            "experiment",
            {"index_exists": True, "total": 0, "existing_ids": []},
        )
        is True
    )
    assert (
        module.docstore_dataset_cleanup_contract_ok(
            "experiment",
            {"index_exists": True, "total": 1, "existing_ids": ["chunk-a"]},
        )
        is False
    )


def test_cascade_subset_contract_requires_only_named_resources_and_api_cleanup():
    module = load_module()
    observed = {
        "api_codes": [0, 0, 0],
        "precondition_ready": True,
        "delete_http_status": 200,
        "delete_code": 0,
        "delete_success_count": 1,
        "documents_removed": True,
        "files_removed": False,
        "final_metadata_cleanup": True,
    }

    assert module.cascade_subset_contract_ok(observed, required_checks=["documents_removed"]) is True
    assert module.cascade_subset_contract_ok(observed, required_checks=["files_removed"]) is False


def test_delete_all_contract_requires_clean_precondition_three_created_and_zero_after():
    module = load_module()
    observed = {
        "precondition_clean": True,
        "create_codes": [0, 0, 0],
        "created_count": 3,
        "delete_http_status": 200,
        "delete_code": 0,
        "delete_success_count": 3,
        "remaining_tenant_dataset_count": 0,
    }

    assert module.delete_all_contract_ok(observed) is True
    assert module.delete_all_contract_ok({**observed, "remaining_tenant_dataset_count": 1}) is False


def test_index_schedule_contract_requires_api_task_database_identity_and_cleanup():
    module = load_module()
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0],
        "run_http_status": 200,
        "run_code": 0,
        "response_task_id": "task-1",
        "database_task_id": "task-1",
        "task_row_count": 1,
        "expected_task_type": "graphrag",
        "database_task_type": "graphrag",
        "database_progress": 0.25,
        "cleanup_succeeded": True,
    }

    assert module.index_schedule_contract_ok(observed) is True
    assert module.index_schedule_contract_ok({**observed, "database_task_id": "task-2"}) is False
    assert module.index_schedule_contract_ok({**observed, "database_progress": -0.5}) is False


def test_index_trace_contract_requires_all_three_types_and_matching_progress():
    module = load_module()
    operation = {
        "run_code": 0,
        "trace_http_status": 200,
        "trace_code": 0,
        "response_task_id": "task-1",
        "trace_task_id": "task-1",
        "database_task_id": "task-1",
        "trace_progress": 1.0,
        "database_progress": 1.0,
        "cleanup_code": 0,
        "cleanup_task_cleared": True,
    }
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0],
        "operations": {
            "graph": {**operation, "expected_task_type": "graphrag", "trace_task_type": "graphrag"},
            "raptor": {**operation, "expected_task_type": "raptor", "trace_task_type": "raptor"},
            "mindmap": {**operation, "expected_task_type": "mindmap", "trace_task_type": "mindmap"},
        },
        "dataset_cleanup_succeeded": True,
    }

    assert module.index_trace_contract_ok(observed) is True
    assert (
        module.index_trace_contract_ok(
            {
                **observed,
                "operations": {
                    **observed["operations"],
                    "mindmap": {**observed["operations"]["mindmap"], "trace_task_id": "wrong"},
                },
            }
        )
        is False
    )


def test_index_delete_contract_requires_task_removal_and_backend_artifact_checks():
    module = load_module()
    operation = {
        "run_code": 0,
        "delete_http_status": 200,
        "delete_code": 0,
        "task_id_present_before": True,
        "task_row_present_before": True,
        "task_id_cleared_after": True,
        "task_row_removed_after": True,
        "trace_empty_after": True,
    }
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0, 0],
        "operations": {
            "graph": {
                **operation,
                "artifact_check_performed": True,
                "artifact_api_http_status": 200,
                "artifact_api_code": 0,
                "artifact_not_visible_via_api": True,
                "artifact_absent_after": True,
            },
            "raptor": {
                **operation,
                "artifact_check_performed": True,
                "artifact_api_http_status": 200,
                "artifact_api_code": 0,
                "artifact_not_visible_via_api": True,
                "artifact_absent_after": True,
            },
            "mindmap": operation,
        },
        "knowledge_graph_metadata_table_absent": True,
        "dataset_cleanup_succeeded": True,
    }

    assert module.index_delete_contract_ok(observed) is True
    assert (
        module.index_delete_contract_ok(
            {
                **observed,
                "operations": {
                    **observed["operations"],
                    "graph": {**observed["operations"]["graph"], "artifact_absent_after": False},
                },
            }
        )
        is False
    )


def test_index_artifact_search_payload_scopes_the_known_fixture_document():
    module = load_module()

    payload = module.index_artifact_search_payload("document-1")

    assert payload["doc_ids"] == ["document-1"]
    assert payload["question"] == "alpha beta index"
    assert payload["similarity_threshold"] == 0.0


def test_embedding_schedule_contract_requires_count_task_rows_and_cleanup():
    module = load_module()
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0, 0],
        "run_http_status": 200,
        "run_code": 0,
        "expected_scheduled_count": 2,
        "actual_scheduled_count": 2,
        "document_count": 2,
        "documents_with_tasks": 2,
        "cleanup_succeeded": True,
    }

    assert module.embedding_schedule_contract_ok(observed) is True
    assert module.embedding_schedule_contract_ok({**observed, "actual_scheduled_count": 1}) is False


def test_embedding_check_contract_requires_same_model_comparable_vector_and_similarity():
    module = load_module()
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0, 0],
        "check_http_status": 200,
        "check_code": 0,
        "configured_model": "model@provider",
        "reported_model": "model@provider",
        "expected_chunk_id": "chunk-1",
        "result_chunk_ids": ["chunk-1"],
        "sampled": 1,
        "valid": 1,
        "average_similarity": 1.0,
        "vector_dimensions_positive": True,
        "cleanup_succeeded": True,
    }

    assert module.embedding_check_contract_ok(observed) is True
    assert module.embedding_check_contract_ok({**observed, "average_similarity": 0.89}) is False


def test_ingestion_log_contract_requires_real_dataset_log_and_optional_detail():
    module = load_module()
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0, 0],
        "list_http_status": 200,
        "list_code": 0,
        "list_total": 1,
        "log_id": "log-1",
        "list_log_matches_dataset": True,
        "list_log_task_type": "Mindmap",
        "database_log_matches": True,
        "detail_http_status": 200,
        "detail_code": 0,
        "detail_id_matches": True,
        "detail_dataset_matches": True,
        "detail_dsl_present": True,
        "index_cleanup_succeeded": True,
        "dataset_delete_code": 0,
        "dataset_count_after": 0,
        "retained_log_count_after": 1,
    }

    assert module.ingestion_log_contract_ok(observed, require_detail=True) is True
    assert module.ingestion_log_contract_ok({**observed, "detail_dsl_present": False}, require_detail=True) is False
    assert module.ingestion_log_contract_ok({**observed, "detail_dsl_present": False}, require_detail=False) is True


def test_ingestion_poll_readiness_requires_log_and_stable_nonzero_total():
    module = load_module()
    log = {"id": "log-1", "task_type": "Mindmap"}

    assert module.ingestion_poll_ready({"total": 1, "logs": [log]}) is True
    assert module.ingestion_poll_ready({"total": 0, "logs": [log]}) is False
    assert module.ingestion_poll_ready({"total": 1, "logs": []}) is False


def test_ingestion_summary_contract_requires_api_database_counts_status_and_cleanup():
    module = load_module()
    observed = {
        "precondition_clean": True,
        "fixture_api_codes": [0, 0],
        "summary_http_status": 200,
        "summary_code": 0,
        "api_counts": {"doc_num": 1, "chunk_num": 1, "token_num": 8},
        "database_counts": {"doc_num": 1, "chunk_num": 1, "token_num": 8},
        "status_shape_valid": True,
        "cleanup_succeeded": True,
    }

    assert module.ingestion_summary_contract_ok(observed) is True
    assert module.ingestion_summary_contract_ok({**observed, "status_shape_valid": False}) is False
