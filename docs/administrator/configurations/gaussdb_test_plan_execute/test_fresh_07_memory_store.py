import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_07_memory_store.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_07_memory_store", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_current_plan_contains_exactly_79_unique_cases():
    module = load_module()

    assert len(module.CASE_TITLES) == 79
    assert len(set(module.CASE_TITLES)) == 79
    assert list(module.CASE_TITLES)[:2] == ["TC-MS-001", "TC-MS-002"]
    assert list(module.CASE_TITLES)[-1] == "TC-MS-908"


def test_add_message_forwards_long_content_timeout(monkeypatch):
    module = load_module()
    captured = {}

    def fake_request(*args, **kwargs):
        captured.update(kwargs)
        return {"code": 0}

    monkeypatch.setattr(module, "_request", fake_request)
    module._add_message(
        "TC-MS-007",
        "control",
        "auth",
        "add_raw_message",
        ["memory"],
        agent_id="agent",
        session_id="session",
        user_input="input",
        agent_response="response",
        timeout=module.LONG_CONTENT_REQUEST_TIMEOUT_SECONDS,
    )

    assert module.LONG_CONTENT_REQUEST_TIMEOUT_SECONDS == 900
    assert captured["timeout"] == 900


def test_store_snapshot_forwards_long_content_timeout(monkeypatch):
    module = load_module()
    captured = {}

    def fake_probe(*args, **kwargs):
        captured.update(kwargs)
        return {"rows": []}

    monkeypatch.setattr(module.MS, "_run_store_probe", fake_probe)
    module._store_snapshot(
        "TC-MS-007",
        "control",
        "read_only_raw_message_snapshot",
        "tenant",
        ["memory"],
        timeout=module.LONG_CONTENT_REQUEST_TIMEOUT_SECONDS,
    )

    assert captured["timeout"] == 900


def test_group_result_shape_requires_steps_and_oracle():
    module = load_module()

    assert module.group_result_shape_ok(
        {
            "status": "PASS",
            "steps": [{"name": "exercise"}],
            "oracle": {"expected": True},
        }
    )
    assert not module.group_result_shape_ok({"status": "PASS", "steps": [], "oracle": {}})


def test_successful_add_contract_requires_raw_task_cache_and_cleanup():
    module = load_module()
    observed = {
        "http_status": 200,
        "code": 0,
        "message": "All add to task.",
        "exact_raw_count": 1,
        "message_id_positive": True,
        "task_delta": 1,
        "size_cache_positive": True,
        "cleanup_succeeded": True,
    }

    assert module.successful_add_contract_ok(observed)
    observed["exact_raw_count"] = 0
    assert not module.successful_add_contract_ok(observed)


def test_raw_document_contract_checks_identity_content_vector_and_attribution():
    module = load_module()
    observed = {
        "id_matches": True,
        "message_id_positive": True,
        "message_type": "raw",
        "source_id": 0,
        "memory_matches": True,
        "agent_matches": True,
        "session_matches": True,
        "user_matches": True,
        "zone_id": 0,
        "status": True,
        "content_matches": True,
        "tokenized_nonempty": True,
        "vector_dimension_positive": True,
        "vector_is_real": True,
        "forget_at_is_null": True,
    }

    assert module.raw_document_contract_ok(observed)
    observed["user_matches"] = False
    assert not module.raw_document_contract_ok(observed)


def test_fanout_contract_requires_two_independent_rows():
    module = load_module()

    assert module.fanout_contract_ok(
        {
            "http_status": 200,
            "code": 0,
            "message": "All add to task.",
            "memory_counts": [1, 1],
            "memory_ids_match": True,
            "contents_match": True,
            "message_ids_distinct": True,
            "task_delta": 2,
            "cleanup_succeeded": True,
        }
    )


def test_rejected_add_contract_requires_exact_response_and_zero_side_effects():
    module = load_module()

    assert module.rejected_add_contract_ok(
        {
            "http_status": 200,
            "code": 500,
            "message": "Some messages failed to add. Detail:Memory not found.",
            "raw_delta": 0,
            "task_delta": 0,
            "cache_delta": 0,
            "cleanup_succeeded": True,
        },
        expected_code=500,
        expected_message="Some messages failed to add. Detail:Memory not found.",
    )


def test_long_content_contract_requires_exact_length_and_boundary_markers():
    module = load_module()

    assert module.long_content_contract_ok(
        {
            "http_status": 200,
            "code": 0,
            "stored_character_count": 10029,
            "expected_character_count": 10029,
            "prefix_matches": True,
            "suffix_matches": True,
            "tokenized_nonempty": True,
            "cleanup_succeeded": True,
        }
    )


def test_upsert_contract_requires_single_updated_row_without_id_change():
    module = load_module()

    assert module.upsert_contract_ok(
        {
            "before_count": 1,
            "adapter_error_count": 0,
            "after_count": 1,
            "id_unchanged": True,
            "message_id_unchanged": True,
            "updated_content_matches": True,
            "cleanup_succeeded": True,
        }
    )


def test_tokenizer_contract_distinguishes_physical_and_semantic_oracles():
    module = load_module()

    assert module.tokenizer_contract_ok(
        "experiment",
        {
            "http_status": 200,
            "code": 0,
            "physical_tokenized_matches": True,
            "semantic_search_hit": True,
            "cleanup_succeeded": True,
        },
    )
    assert module.tokenizer_contract_ok(
        "control",
        {
            "http_status": 200,
            "code": 0,
            "physical_tokenized_matches": None,
            "semantic_search_hit": True,
            "cleanup_succeeded": True,
        },
    )


def test_secondary_fixture_password_is_local_and_meets_registration_shape():
    module = load_module()

    value = module.secondary_fixture_password("TC-MS-010")

    assert len(value) >= 12
    assert any(character.isupper() for character in value)
    assert any(character.isdigit() for character in value)
    assert "@" in value


def test_document_id_match_accepts_unique_backend_physical_id_fallback():
    module = load_module()

    assert module.document_id_matches("memory-a_12", "memory-a_12", [])
    assert module.document_id_matches("memory-a_12", "", ["memory-a_12"])
    assert not module.document_id_matches("memory-a_12", "", ["memory-a_12", "memory-b_12"])


def test_list_messages_contract_checks_shape_count_order_and_extracts():
    module = load_module()

    observed = {
        "http_status": 200,
        "code": 0,
        "storage_type": "table",
        "total_count": 3,
        "returned_count": 3,
        "expected_count": 3,
        "raw_only": True,
        "extract_lists_present": True,
        "memory_ids_exact": True,
        "message_ids_exact": True,
        "valid_at_nonincreasing": True,
        "valid_at_distinct": True,
        "cleanup_succeeded": True,
    }

    assert module.list_messages_contract_ok(observed)
    observed["total_count"] = 2
    assert not module.list_messages_contract_ok(observed)
    observed["total_count"] = 3
    observed["valid_at_distinct"] = False
    assert not module.list_messages_contract_ok(observed)


def test_pagination_contract_requires_two_disjoint_full_pages():
    module = load_module()

    observed = {
        "responses": [[200, 0], [200, 0]],
        "page_lengths": [3, 3],
        "total_counts": [10, 10],
        "pages_disjoint": True,
        "page_ids_from_fixture": True,
        "valid_at_distinct_across_pages": True,
        "cleanup_succeeded": True,
    }
    assert module.pagination_contract_ok(observed)
    observed["valid_at_distinct_across_pages"] = False
    assert not module.pagination_contract_ok(observed)


def test_recent_messages_contract_checks_filters_limit_order_and_visibility():
    module = load_module()

    observed = {
        "http_status": 200,
        "code": 0,
        "returned_count": 5,
        "limit": 5,
        "agent_ids_exact": True,
        "session_ids_exact": True,
        "memory_ids_exact": True,
        "message_ids_from_fixture": True,
        "valid_at_nonincreasing": True,
        "valid_at_distinct": True,
        "forgotten_absent": True,
        "cleanup_succeeded": True,
    }
    assert module.recent_messages_contract_ok(observed)
    observed["valid_at_distinct"] = False
    assert not module.recent_messages_contract_ok(observed)


def test_recent_visibility_contract_requires_forgotten_hidden_and_disabled_visible():
    module = load_module()

    assert module.recent_visibility_contract_ok(
        {
            "responses": [[200, 0], [200, 0], [200, 0]],
            "physical_raw_count": 3,
            "physical_forgotten": True,
            "physical_disabled": True,
            "recent_count": 2,
            "forgotten_absent": True,
            "disabled_present": True,
            "cleanup_succeeded": True,
        }
    )


def test_recent_limit_contract_requires_bounded_and_standardized_arguments():
    module = load_module()

    expected = {
        "baseline": [200, 0],
        "baseline_count": 10,
        "huge": [200, 101],
        "invalid": [200, 101],
        "raw_unchanged": True,
        "cleanup_succeeded": True,
    }
    assert module.recent_limit_contract_ok(expected)
    expected["huge"] = [200, 0]
    assert not module.recent_limit_contract_ok(expected)


def test_list_recent_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-100")
    assert registered[start : start + 10] == [
        "TC-MS-100",
        "TC-MS-101",
        "TC-MS-102",
        "TC-MS-103",
        "TC-MS-104",
        "TC-MS-105",
        "TC-MS-106",
        "TC-MS-107",
        "TC-MS-108",
        "TC-MS-109",
    ]


def test_list_failure_finding_reports_order_before_filter_mismatch():
    module = load_module()

    finding = module.list_failure_finding(
        "control",
        {"valid_at_nonincreasing": False},
        "agent_id",
        "MS-LIST-AGENT-FILTER-001",
    )

    assert finding["id"] == "MS-LIST-ORDER-001"
    assert "valid_at DESC" in finding["summary"]


def test_recent_failure_finding_reports_order_before_filter_mismatch():
    module = load_module()

    finding = module.recent_failure_finding("control", {"valid_at_nonincreasing": False})

    assert finding["id"] == "MS-RECENT-ORDER-001"
    assert "valid_at DESC" in finding["summary"]


def test_semantic_search_contract_requires_exact_fixture_hit_and_cleanup():
    module = load_module()

    observed = {
        "write": [200, 0],
        "search": [200, 0],
        "physical_raw_count": 1,
        "target_hit": True,
        "target_content_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.semantic_search_contract_ok(observed)
    observed["target_hit"] = False
    assert not module.semantic_search_contract_ok(observed)


def test_pure_vector_contract_requires_backend_score_recomputation():
    module = load_module()

    assert module.pure_vector_contract_ok(
        {
            "public_search": [200, 0],
            "public_target_hit": True,
            "adapter_result_count": 2,
            "query_dimension_positive": True,
            "scores_finite": True,
            "scores_nonincreasing": True,
            "score_recomputation_matches": True,
            "target_ids_exact": True,
            "cleanup_succeeded": True,
        }
    )


def test_vector_score_tolerance_accounts_for_infinity_float32_ann_only():
    module = load_module()

    assert module.vector_score_matches("InfinityConnection", 0.6638775, 0.6641538)
    assert module.vector_score_matches("GaussDBMemoryConnection", 0.6641540, 0.66415399)
    assert not module.vector_score_matches("InfinityConnection", 0.65, 0.6641538)
    assert not module.vector_score_matches("GaussDBMemoryConnection", 0.663, 0.66415399)


def test_empty_vector_contract_distinguishes_gauss_flag_and_infinity_rejection():
    module = load_module()

    assert module.empty_vector_contract_ok(
        "experiment",
        {
            "fixture_action_succeeded": True,
            "target_empty_flag": True,
            "target_absent_from_dense_results": True,
            "other_message_present": True,
            "fixture_residue_count": 0,
            "cleanup_succeeded": True,
        },
    )


def test_empty_vector_update_payload_uses_adapter_remove_semantics():
    module = load_module()

    assert module.empty_vector_update_payload(1024) == {"remove": "q_1024_vec"}
    assert module.empty_vector_contract_ok(
        "control",
        {
            "fixture_action_succeeded": True,
            "target_empty_flag": None,
            "target_absent_from_dense_results": True,
            "other_message_present": True,
            "fixture_residue_count": 0,
            "cleanup_succeeded": True,
        },
    )


def test_search_parameter_contract_requires_all_invalid_values_code_101():
    module = load_module()

    assert module.search_parameter_contract_ok(
        {
            "legal_responses": [[200, 0]],
            "legal_limits_ok": True,
            "invalid_responses": [[200, 101], [200, 101], [200, 101]],
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        },
        invalid_count=3,
    )


def test_search_visibility_contract_hides_forgotten_and_disabled():
    module = load_module()

    observed = {
        "mutation_responses": [[200, 0], [200, 0]],
        "search_response": [200, 0],
        "physical_raw_count": 3,
        "physical_forgotten": True,
        "physical_disabled": True,
        "active_present": True,
        "forgotten_absent": True,
        "disabled_absent": True,
        "cleanup_succeeded": True,
    }
    assert module.search_visibility_contract_ok(observed)
    observed["physical_disabled"] = False
    assert not module.search_visibility_contract_ok(observed)


def test_query_validation_contract_requires_nonempty_query_code_101():
    module = load_module()

    assert module.query_validation_contract_ok(
        {
            "legal": [200, 0],
            "empty": [200, 101],
            "missing": [200, 101],
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )


def test_multi_memory_search_contract_requires_exact_hits_from_both_memories():
    module = load_module()

    assert module.multi_memory_search_contract_ok(
        {
            "writes": [[200, 0], [200, 0]],
            "search": [200, 0],
            "physical_raw_count": 2,
            "returned_memory_ids_exact": True,
            "returned_message_ids_exact": True,
            "cleanup_succeeded": True,
        }
    )


def test_search_filter_contract_requires_exact_agent_session_and_user_rows():
    module = load_module()

    assert module.search_filter_contract_ok(
        {
            "writes": [[200, 0], [200, 0], [200, 0]],
            "searches": [[200, 0], [200, 0], [200, 0]],
            "physical_raw_count": 3,
            "physical_user_ids_exact": True,
            "agent_filter_exact": True,
            "agent_session_filter_exact": True,
            "user_filter_exact": True,
            "cleanup_succeeded": True,
        }
    )


def test_keyword_weight_mapping_treats_public_parameter_as_text_weight():
    module = load_module()

    assert module.keyword_weight_mapping_ok({0.0: [0.0, 1.0], 0.5: [0.5, 0.5], 1.0: [1.0, 0.0]})
    assert not module.keyword_weight_mapping_ok({0.0: [1.0, 0.0], 0.5: [0.5, 0.5], 1.0: [0.0, 1.0]})


def test_fusion_weight_contract_requires_public_keyword_semantics():
    module = load_module()

    observed = {
        "api_responses": [[200, 0], [200, 0], [200, 0]],
        "all_target_hits": True,
        "adapter_results_nonempty": True,
        "scores_finite_and_ordered": True,
        "keyword_weight_mapping_correct": True,
        "cleanup_succeeded": True,
    }
    assert module.fusion_weight_contract_ok(observed)
    observed["keyword_weight_mapping_correct"] = False
    assert not module.fusion_weight_contract_ok(observed)


def test_adapter_probe_select_fields_only_reads_vectors_for_dense_recomputation():
    module = load_module()

    assert "content_embed" in module.adapter_probe_select_fields("dense")
    assert "content_embed" not in module.adapter_probe_select_fields("fusion")


def test_status_transition_contract_requires_physical_zero_mapping():
    module = load_module()

    assert module.status_transition_contract_ok(
        {
            "response": [200, 0],
            "response_message": True,
            "physical_raw_count": 1,
            "status": False,
            "physical_status_int": 0,
            "identity_unchanged": True,
            "cleanup_succeeded": True,
        },
        expected_status=False,
    )


def test_status_roundtrip_contract_requires_zero_then_one():
    module = load_module()

    assert module.status_roundtrip_contract_ok(
        {
            "responses": [[200, 0], [200, 0]],
            "response_messages": [True, True],
            "statuses": [False, True],
            "physical_status_ints": [0, 1],
            "identity_unchanged": True,
            "cleanup_succeeded": True,
        }
    )


def test_status_visibility_contract_distinguishes_search_and_recent():
    module = load_module()

    assert module.status_visibility_contract_ok(
        {
            "disable": [200, 0],
            "search": [200, 0],
            "recent": [200, 0],
            "physical_raw_count": 2,
            "physical_disabled": True,
            "search_active_only": True,
            "recent_includes_both": True,
            "cleanup_succeeded": True,
        }
    )


def test_status_rejection_contracts_require_code_101_or_404_without_mutation():
    module = load_module()

    assert module.invalid_status_contract_ok(
        {
            "responses": [[200, 101], [200, 101]],
            "messages_exact": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )
    assert module.status_access_rejection_contract_ok(
        {
            "response": [200, 404],
            "message_exact": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )
    assert module.missing_message_status_contract_ok(
        {
            "response": [200, 404],
            "message_identifies_missing_message": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )


def test_forget_transition_contract_requires_physical_timestamp_in_window():
    module = load_module()

    assert module.forget_transition_contract_ok(
        {
            "response": [200, 0],
            "response_message": True,
            "physical_raw_count": 1,
            "forget_at_set": True,
            "timestamp_in_window": True,
            "identity_unchanged": True,
            "cleanup_succeeded": True,
        }
    )


def test_forget_visibility_and_management_list_contracts():
    module = load_module()

    assert module.forget_visibility_contract_ok(
        {
            "forget": [200, 0],
            "search": [200, 0],
            "recent": [200, 0],
            "physical_raw_count": 2,
            "physical_forgotten": True,
            "search_active_only": True,
            "recent_active_only": True,
            "cleanup_succeeded": True,
        }
    )
    assert module.forgotten_list_contract_ok(
        {
            "forget": [200, 0],
            "list": [200, 0],
            "physical_raw_count": 2,
            "physical_forgotten": True,
            "list_includes_both": True,
            "cleanup_succeeded": True,
        }
    )


def test_repeat_and_rejected_forget_contracts():
    module = load_module()

    assert module.repeat_forget_contract_ok(
        {
            "responses": [[200, 0], [200, 0]],
            "response_messages": [True, True],
            "first_timestamp_set": True,
            "second_timestamp_later": True,
            "physical_raw_count": 1,
            "identity_unchanged": True,
            "cleanup_succeeded": True,
        }
    )
    assert module.forget_access_rejection_contract_ok(
        {
            "response": [200, 404],
            "message_exact": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )
    assert module.missing_message_forget_contract_ok(
        {
            "response": [200, 404],
            "message_identifies_missing_message": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )


def test_message_content_contract_requires_exact_id_content_and_vector():
    module = load_module()

    assert module.message_content_contract_ok(
        {
            "response": [200, 0],
            "response_message": True,
            "physical_raw_count": 1,
            "id_exact": True,
            "message_id_exact": True,
            "memory_id_exact": True,
            "content_exact": True,
            "vector_nonempty": True,
            "raw_response_consistent": True,
            "cleanup_succeeded": True,
        }
    )


def test_message_content_rejection_contracts_require_404_without_mutation():
    module = load_module()

    assert module.content_access_rejection_contract_ok(
        {
            "response": [200, 404],
            "message_exact": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )
    assert module.missing_message_content_contract_ok(
        {
            "response": [200, 404],
            "message_identifies_missing_message": True,
            "raw_unchanged": True,
            "cleanup_succeeded": True,
        }
    )


def test_hidden_message_content_contract_allows_forgotten_and_inactive_rows():
    module = load_module()

    assert module.hidden_message_content_contract_ok(
        {
            "mutations": [[200, 0], [200, 0]],
            "reads": [[200, 0], [200, 0]],
            "response_messages": [True, True],
            "physical_raw_count": 2,
            "physical_forgotten": True,
            "physical_inactive": True,
            "ids_exact": True,
            "contents_exact": True,
            "cleanup_succeeded": True,
        }
    )


def test_same_tenant_memory_isolation_contract_requires_api_and_layout_boundaries():
    module = load_module()

    assert module.same_tenant_memory_isolation_contract_ok(
        {
            "writes": [[200, 0], [200, 0]],
            "reads": [[200, 0], [200, 0]],
            "physical_raw_count": 2,
            "api_a_only": True,
            "api_b_only": True,
            "physical_memory_ids_exact": True,
            "backend_layout_matches": True,
            "cleanup_succeeded": True,
        }
    )


def test_cross_tenant_memory_isolation_contract_requires_empty_cross_reads():
    module = load_module()

    assert module.cross_tenant_memory_isolation_contract_ok(
        {
            "writes": [[200, 0], [200, 0]],
            "cross_reads": [[200, 0], [200, 0]],
            "cross_results_empty": True,
            "physical_raw_counts": [1, 1],
            "tenant_indexes_distinct": True,
            "physical_relations_distinct": True,
            "cleanup_succeeded": True,
        }
    )


def test_delete_memory_isolation_contract_preserves_survivor():
    module = load_module()

    assert module.memory_target_rows_removed(
        {
            "index_flags": [True, True],
            "rows": [{"memory_id": "memory-b", "message_id": 2}],
        },
        "memory-a",
    )
    assert module.delete_memory_isolation_contract_ok(
        {
            "writes": [[200, 0], [200, 0]],
            "delete": [200, 0],
            "physical_before": 2,
            "physical_after": 1,
            "target_removed": True,
            "survivor_exact": True,
            "survivor_api_exact": True,
            "metadata_boundary_exact": True,
            "cleanup_succeeded": True,
        }
    )


def test_collision_isolation_contract_requires_two_composite_rows_and_cleanup():
    module = load_module()

    assert module.collision_isolation_contract_ok(
        {
            "source_writes": [[200, 0], [200, 0]],
            "fixture_inserted": True,
            "physical_pair_count": 2,
            "physical_ids_exact": True,
            "reads": [[200, 0], [200, 0]],
            "api_a_exact": True,
            "api_b_exact": True,
            "both_rows_survive_reads": True,
            "fixture_cleanup_count": 0,
            "memory_cleanup_succeeded": True,
        }
    )


def test_initial_vector_schema_contract_is_backend_specific():
    module = load_module()
    common = {
        "write": [200, 0],
        "physical_raw_count": 1,
        "dimension_positive": True,
        "vector_column_count": 1,
        "vector_type_ok": True,
        "vector_index_present": True,
        "row_vector_real": True,
        "cleanup_succeeded": True,
    }
    assert module.initial_vector_schema_contract_ok("control", {**common, "empty_column_count": 0, "row_empty_flag": None})
    assert module.initial_vector_schema_contract_ok(
        "experiment",
        {**common, "empty_column_count": 1, "row_empty_flag": False},
    )


def test_new_vector_dimension_contract_distinguishes_reject_and_dynamic_add():
    module = load_module()
    common = {
        "source_dimension_positive": True,
        "dimensions_distinct": True,
        "source_preserved": True,
        "cleanup_succeeded": True,
    }
    assert module.new_vector_dimension_contract_ok(
        "control",
        {
            **common,
            "fixture_rejected": True,
            "candidate_exists": False,
            "vector_dimensions_exact": True,
            "empty_columns_exact": True,
        },
    )
    assert module.new_vector_dimension_contract_ok(
        "experiment",
        {
            **common,
            "fixture_rejected": False,
            "candidate_exists": True,
            "candidate_dimension_exact": True,
            "vector_dimensions_exact": True,
            "empty_columns_exact": True,
        },
    )


def test_cross_dimension_upsert_contract_requires_safe_control_rejection():
    module = load_module()

    assert module.cross_dimension_upsert_contract_ok(
        "control",
        {
            "fixture_rejected": True,
            "source_row_preserved": True,
            "source_content_preserved": True,
            "source_dimension_preserved": True,
            "cleanup_succeeded": True,
        },
    )
    assert module.cross_dimension_upsert_contract_ok(
        "experiment",
        {
            "fixture_rejected": False,
            "source_row_preserved": True,
            "new_dimension_real": True,
            "new_empty_false": True,
            "old_dimension_zero": True,
            "old_empty_true": True,
            "cleanup_succeeded": True,
        },
    )


def test_empty_vector_dense_filter_contract_excludes_placeholder():
    module = load_module()
    common = {
        "fixture_action_succeeded": True,
        "dense_result_source_only": True,
        "source_vector_real": True,
        "fixture_cleanup_succeeded": True,
        "memory_cleanup_succeeded": True,
    }
    assert module.empty_vector_dense_filter_contract_ok("control", {**common, "empty_flag": None, "rejected_fixture_absent": True})
    assert module.empty_vector_dense_filter_contract_ok(
        "experiment",
        {**common, "empty_flag": True, "placeholder_excluded": True},
    )


def test_content_embed_dimension_contract_returns_only_real_dimension():
    module = load_module()
    common = {
        "read": [200, 0],
        "returned_dimension_is_original": True,
        "returned_vector_real": True,
        "content_preserved": True,
        "cleanup_succeeded": True,
    }
    assert module.content_embed_dimension_contract_ok(
        "control",
        {
            **common,
            "cross_dimension_rejected": True,
            "row_restored": True,
        },
    )
    assert module.content_embed_dimension_contract_ok(
        "experiment",
        {
            **common,
            "cross_dimension_rejected": False,
            "original_empty_false": True,
            "other_empty_true": True,
            "other_dimension_zero": True,
        },
    )


def test_vector_ddl_idempotency_contract_requires_single_column_and_all_rows():
    module = load_module()
    common = {
        "writes": [[200, 0], [200, 0], [200, 0]],
        "ensure_error_count": 0,
        "vector_column_count": 1,
        "vector_index_present": True,
        "physical_raw_count": 3,
        "all_rows_preserved": True,
        "cleanup_succeeded": True,
    }
    assert module.vector_ddl_idempotency_contract_ok("control", {**common, "empty_column_count": 0})
    assert module.vector_ddl_idempotency_contract_ok("experiment", {**common, "empty_column_count": 1})


def test_sequence_seed_from_max_contract_requires_physical_max_and_next_id():
    module = load_module()
    observed = {
        "exclusive_window": True,
        "key_absent_before_init": True,
        "service_max_positive": True,
        "service_max_equals_physical": True,
        "initialized_seed_equals_max": True,
        "next_write": [200, 0],
        "new_id_equals_max_plus_one": True,
        "no_collision": True,
        "api_restored": True,
        "cleanup_succeeded": True,
    }

    assert module.sequence_seed_from_max_contract_ok(observed)
    observed["new_id_equals_max_plus_one"] = False
    assert not module.sequence_seed_from_max_contract_ok(observed)


def test_existing_sequence_seed_contract_requires_initializer_to_preserve_seed():
    module = load_module()
    observed = {
        "exclusive_window": True,
        "db_max_positive": True,
        "existing_seed_gt_max": True,
        "seed_before_equals_after": True,
        "initializer_skipped": True,
        "api_restored": True,
        "cleanup_succeeded": True,
    }

    assert module.existing_sequence_seed_contract_ok(observed)
    observed["initializer_skipped"] = False
    assert not module.existing_sequence_seed_contract_ok(observed)


def test_memory_size_cache_contract_uses_python_object_size_exactly():
    module = load_module()
    observed = {
        "exclusive_window": True,
        "cache_absent_before_init": True,
        "raw_count_positive": True,
        "calculated_size_positive": True,
        "service_size_equals_manual": True,
        "cache_equals_manual": True,
        "api_restored": True,
        "memory_cleanup_succeeded": True,
        "cache_fixture_cleanup_succeeded": True,
    }

    assert module.memory_size_cache_contract_ok(observed)
    observed["service_size_equals_manual"] = False
    assert not module.memory_size_cache_contract_ok(observed)


def test_forgotten_scan_contract_requires_selected_field_and_physical_row():
    module = load_module()
    observed = {
        "write_count": 2,
        "forget": [200, 0],
        "returned_count": 1,
        "returned_target_only": True,
        "selected_forget_at_nonempty": True,
        "unselected_forget_at_absent": True,
        "physical_forgotten_count": 1,
        "cleanup_succeeded": True,
    }

    assert module.forgotten_scan_contract_ok(observed)
    observed["adapter_exception"] = "InfinityException"
    assert not module.forgotten_scan_contract_ok(observed)
    observed["adapter_exception"] = None
    observed["unselected_forget_at_absent"] = False
    assert not module.forgotten_scan_contract_ok(observed)


def test_missing_field_scan_contract_distinguishes_backend_capabilities():
    module = load_module()
    common = {
        "raw_before_after_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.missing_field_scan_contract_ok(
        "control",
        {
            **common,
            "native_column_absent": True,
            "unsupported_exception": "AssertionError",
            "returned_count": 0,
        },
    )
    assert module.missing_field_scan_contract_ok(
        "experiment",
        {
            **common,
            "fixture_set_null": True,
            "physical_field_is_null": True,
            "returned_count": 1,
            "returned_target_only": True,
            "selected_fields_exact": True,
            "restore_succeeded": True,
            "physical_field_restored": True,
            "missing_after_restore_count": 0,
        },
    )


def test_empty_store_seed_contract_requires_exact_seed_one():
    module = load_module()
    observed = {
        "exclusive_window": True,
        "metadata_memory_count": 0,
        "key_absent_before_init": True,
        "initialized_seed": 1,
        "api_restored": True,
    }

    assert module.empty_store_seed_contract_ok(observed)
    observed["initialized_seed"] = 0
    assert not module.empty_store_seed_contract_ok(observed)


def test_physical_table_name_contract_is_backend_specific():
    module = load_module()
    common = {
        "writes": [[200, 0], [200, 0]],
        "all_tables_exist": True,
        "physical_raw_count": 2,
        "cleanup_succeeded": True,
    }

    assert module.physical_table_name_contract_ok(
        "control",
        {
            **common,
            "native_names_exact": True,
            "unique_physical_table_count": 2,
        },
    )
    assert module.physical_table_name_contract_ok(
        "experiment",
        {
            **common,
            "sha1_name_exact": True,
            "unique_physical_table_count": 1,
        },
    )


def test_base_schema_contract_requires_backend_native_columns():
    module = load_module()
    common = {
        "write": [200, 0],
        "table_exists": True,
        "mapping_or_base_columns_complete": True,
        "vector_column_exact": True,
        "physical_raw_count": 1,
        "cleanup_succeeded": True,
    }

    assert module.base_schema_contract_ok(
        "control",
        {**common, "empty_column_count": 0, "native_types_complete": True},
    )
    assert module.base_schema_contract_ok(
        "experiment",
        {
            **common,
            "empty_column_count": 1,
            "base_types_exact": True,
            "required_not_null_exact": True,
            "defaults_exact": True,
            "ustore": True,
        },
    )


def test_base_index_contract_requires_backend_specific_index_set():
    module = load_module()
    common = {
        "write": [200, 0],
        "table_exists": True,
        "duplicate_index_count": 0,
        "cleanup_succeeded": True,
    }

    assert module.base_index_contract_ok(
        "control",
        {
            **common,
            "native_index_names_exact": True,
            "native_index_types_exact": True,
        },
    )
    assert module.base_index_contract_ok(
        "experiment",
        {
            **common,
            "regular_index_count": 7,
            "regular_index_columns_exact": True,
            "primary_key_present": True,
        },
    )


def test_fulltext_index_contract_checks_analyzer_or_ugin_expression():
    module = load_module()
    common = {"write": [200, 0], "cleanup_succeeded": True}

    assert module.fulltext_index_contract_ok(
        "control",
        {
            **common,
            "fulltext_index_names_exact": True,
            "fulltext_types_exact": True,
            "fulltext_columns_exact": True,
        },
    )
    assert module.fulltext_index_contract_ok(
        "experiment",
        {
            **common,
            "ugin_index_count": 1,
            "ugin_name_exact": True,
            "ugin_expression_exact": True,
        },
    )


def test_vector_index_contract_checks_hnsw_or_diskann_cosine():
    module = load_module()
    common = {
        "write": [200, 0],
        "dimension_positive": True,
        "vector_index_count": 1,
        "vector_index_dimension_exact": True,
        "cosine_metric": True,
        "cleanup_succeeded": True,
    }

    assert module.vector_index_contract_ok("control", {**common, "hnsw": True, "gsdiskann": False})
    assert module.vector_index_contract_ok("experiment", {**common, "hnsw": False, "gsdiskann": True})


def test_catalog_ddl_idempotency_contract_preserves_structure_and_row():
    module = load_module()
    observed = {
        "write": [200, 0],
        "create_error_count": 0,
        "catalog_signature_unchanged": True,
        "duplicate_index_count": 0,
        "row_preserved": True,
        "cleanup_succeeded": True,
    }

    assert module.catalog_ddl_idempotency_contract_ok(observed)
    observed["catalog_signature_unchanged"] = False
    assert not module.catalog_ddl_idempotency_contract_ok(observed)


def test_delete_index_contract_requires_guard_table_to_survive():
    module = load_module()
    observed = {
        "dedicated_write": [200, 0],
        "guard_write": [200, 0],
        "dedicated_only_before": True,
        "delete_succeeded": True,
        "dedicated_table_absent": True,
        "guard_table_present": True,
        "guard_row_preserved": True,
        "business_cleanup_succeeded": True,
    }

    assert module.delete_index_contract_ok(observed)
    observed["guard_table_present"] = False
    assert not module.delete_index_contract_ok(observed)


def test_index_exist_contract_captures_backend_semantic_difference_and_restore():
    module = load_module()
    common = {
        "write": [200, 0],
        "before": True,
        "dropped_index_absent": True,
        "restored": True,
        "restored_index_present": True,
        "row_preserved": True,
        "cleanup_succeeded": True,
    }

    assert module.index_exist_contract_ok("control", {**common, "after_drop": True})
    assert module.index_exist_contract_ok("experiment", {**common, "after_drop": False})


def test_concurrent_ddl_contract_requires_two_rows_and_one_catalog():
    module = load_module()
    common = {
        "responses": [[200, 0], [200, 0]],
        "barrier_released": True,
        "start_spread_bounded": True,
        "physical_table_count": 1,
        "physical_raw_count": 2,
        "message_ids_unique": True,
        "required_indexes_complete": True,
        "duplicate_index_count": 0,
        "cleanup_succeeded": True,
    }

    assert module.concurrent_ddl_contract_ok("control", {**common, "advisory_lock_sql_exact": None})
    assert module.concurrent_ddl_contract_ok("experiment", {**common, "advisory_lock_sql_exact": True})


def test_search_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-200")
    assert registered[start : start + 14] == [f"TC-MS-{number}" for number in range(200, 214)]


def test_status_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-300")
    assert registered[start : start + 7] == [f"TC-MS-{number}" for number in range(300, 307)]


def test_forget_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-400")
    assert registered[start : start + 7] == [f"TC-MS-{number}" for number in range(400, 407)]


def test_content_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-500")
    assert registered[start : start + 4] == [f"TC-MS-{number}" for number in range(500, 504)]


def test_isolation_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-600")
    assert registered[start : start + 4] == [f"TC-MS-{number}" for number in range(600, 604)]


def test_vector_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-700")
    assert registered[start : start + 6] == [f"TC-MS-{number}" for number in range(700, 706)]


def test_startup_maintenance_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-800")
    assert registered[start : start + 6] == [f"TC-MS-{number}" for number in range(800, 806)]


def test_catalog_runner_batch_is_registered_in_plan_order():
    module = load_module()

    registered = list(module.RUNNERS)
    start = registered.index("TC-MS-900")
    assert registered[start : start + 9] == [f"TC-MS-{number}" for number in range(900, 909)]
