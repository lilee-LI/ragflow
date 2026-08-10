import importlib.util
import subprocess
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_08_file.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_08_file", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_current_plans_contain_exactly_89_unique_cases_in_order():
    module = load_module()

    assert len(module.CASE_TITLES) == 89
    assert len(set(module.CASE_TITLES)) == 89
    assert list(module.CASE_TITLES)[:2] == ["TC-FM-001", "TC-FM-002"]
    assert list(module.CASE_TITLES)[69:72] == [
        "TC-FM-070",
        "TC-FILE-DEL-001",
        "TC-FILE-DEL-002",
    ]
    assert list(module.CASE_TITLES)[-1] == "TC-FILE-ACL-002"


def test_group_result_shape_requires_steps_oracle_and_valid_status():
    module = load_module()

    assert module.group_result_shape_ok(
        {
            "status": "PASS",
            "steps": [{"name": "exercise"}],
            "oracle": {"expected": True},
        }
    )
    assert not module.group_result_shape_ok({"status": "PASS", "steps": [], "oracle": {}})


def test_folder_create_contract_checks_dialect_and_cleanup():
    module = load_module()
    common = {
        "response": [200, 0],
        "id_present": True,
        "name_matches": True,
        "parent_matches": True,
        "type": "folder",
        "tenant_matches": True,
        "creator_matches": True,
        "orm_source_type": "",
        "api_source_type": "",
        "cleanup_succeeded": True,
    }
    control = {
        **common,
        "physical_source_is_empty": True,
        "physical_source_is_null": False,
    }
    experiment = {
        **common,
        "physical_source_is_empty": False,
        "physical_source_is_null": True,
    }

    assert module.folder_create_contract_ok("control", control)
    assert module.folder_create_contract_ok("experiment", experiment)
    experiment["physical_source_is_null"] = False
    assert not module.folder_create_contract_ok("experiment", experiment)


def test_rejection_contract_requires_expected_code_message_and_zero_delta():
    module = load_module()
    observed = {
        "response": [200, 102],
        "message": "Folder not found!",
        "file_delta": 0,
        "object_delta": 0,
        "fixture_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.rejection_contract_ok(observed, expected_code=102, expected_message="Folder not found!")
    observed["file_delta"] = 1
    assert not module.rejection_contract_ok(observed, expected_code=102, expected_message="Folder not found!")


def test_upload_contract_requires_exact_database_and_object_bytes():
    module = load_module()
    observed = {
        "response": [200, 0],
        "returned_count": 1,
        "database_count": 1,
        "parent_matches": True,
        "size_matches": True,
        "type_matches": True,
        "location_nonempty": True,
        "object_exists": True,
        "object_size_matches": True,
        "object_sha_matches": True,
        "source_contract_ok": True,
        "cleanup_succeeded": True,
    }

    assert module.upload_contract_ok(observed, expected_count=1)
    observed["object_sha_matches"] = False
    assert not module.upload_contract_ok(observed, expected_count=1)


def test_expected_upload_type_uses_product_file_categories_not_suffixes():
    module = load_module()

    assert module.expected_upload_type("one.pdf") == "pdf"
    assert module.expected_upload_type("two.docx") == "doc"
    assert module.expected_upload_type("three.txt") == "doc"


def test_list_contract_requires_exact_fixture_set_total_and_parent():
    module = load_module()
    observed = {
        "response": [200, 0],
        "fixture_ids_expected": ["a", "b"],
        "fixture_ids_returned": ["a", "b"],
        "total_matches_database": True,
        "all_parent_matches": True,
        "parent_folder_matches": True,
        "folder_sizes_match": True,
        "has_child_folder_boolean": True,
        "cleanup_succeeded": True,
    }

    assert module.list_contract_ok(observed)
    observed["fixture_ids_returned"] = ["a"]
    assert not module.list_contract_ok(observed)


def test_list_oracle_uses_query_folders_parent_and_keyword_filtered_database_scope():
    module = load_module()
    query_folder = {"id": "folder", "parent_id": "root", "name": "Folder"}
    response_parent = {"id": "root", "parent_id": "root", "name": "/"}
    rows = [
        {"id": "a", "name": "ReportAlpha.txt", "parent_id": "folder"},
        {"id": "b", "name": "reportBeta.txt", "parent_id": "folder"},
        {"id": "c", "name": "noise.txt", "parent_id": "folder"},
    ]

    assert module.response_parent_matches_query_folder(response_parent, query_folder)
    assert not module.response_parent_matches_query_folder(query_folder, query_folder)
    assert [row["id"] for row in module.filter_rows_by_keywords(rows, "report")] == [
        "a",
        "b",
    ]


def test_pagination_contract_requires_disjoint_complete_pages_and_validation():
    module = load_module()
    observed = {
        "legal_responses": [[200, 0], [200, 0]],
        "page_lengths": [5, 5],
        "pages_disjoint": True,
        "pages_complete": True,
        "descending": True,
        "total_matches": True,
        "invalid_responses": [[200, 101], [200, 101], [200, 101], [200, 101]],
        "database_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.pagination_contract_ok(observed)
    observed["invalid_responses"][3] = [200, 102]
    assert not module.pagination_contract_ok(observed)


def test_move_contract_requires_database_and_object_store_transition():
    module = load_module()
    observed = {
        "response": [200, 0],
        "row_count": 1,
        "parent_matches": True,
        "name_matches": True,
        "location_changed": True,
        "old_object_absent": True,
        "new_object_exists": True,
        "new_object_length_matches": True,
        "new_object_sha_matches": True,
        "cleanup_succeeded": True,
    }

    assert module.move_contract_ok(observed, require_location_change=True)
    observed["old_object_absent"] = False
    assert not module.move_contract_ok(observed, require_location_change=True)


def test_unchanged_file_contract_requires_response_row_and_storage_stability():
    module = load_module()
    observed = {
        "response": [200, 102],
        "message": "Parent folder not found!",
        "row_unchanged": True,
        "object_still_exists": True,
        "object_sha_unchanged": True,
        "cleanup_succeeded": True,
    }

    assert module.unchanged_file_contract_ok(observed, expected_code=102, expected_message="Parent folder not found!")
    observed["row_unchanged"] = False
    assert not module.unchanged_file_contract_ok(observed, expected_code=102, expected_message="Parent folder not found!")


def test_delete_contract_requires_saved_metadata_relations_and_objects_to_be_absent():
    module = load_module()
    observed = {
        "response": [200, 0],
        "success_count": 1,
        "file_rows_remaining": 0,
        "file_links_remaining": 0,
        "documents_remaining": 0,
        "tasks_remaining": 0,
        "objects_remaining": 0,
        "cleanup_succeeded": True,
    }

    assert module.delete_contract_ok(observed, expected_success_count=1)
    observed["objects_remaining"] = 1
    assert not module.delete_contract_ok(observed, expected_success_count=1)


def test_knowledgebase_cleanup_requires_document_api_before_dataset_api():
    module = load_module()
    observed = {
        "document_delete_response": [200, 0],
        "dataset_delete_response": [200, 0],
        "artifact_counts": {
            "files": 0,
            "file_links": 0,
            "documents": 0,
            "tasks": 0,
        },
        "object_absent": True,
        "dataset_absent": True,
    }

    assert module.knowledgebase_cleanup_contract_ok(observed)
    observed["document_delete_response"] = None
    assert not module.knowledgebase_cleanup_contract_ok(observed)


def test_link_contract_requires_exact_real_file_set_and_dataset_configuration():
    module = load_module()
    observed = {
        "response": [200, 0],
        "data_true": True,
        "expected_file_ids": ["a", "b"],
        "linked_file_ids": ["a", "b"],
        "document_count": 2,
        "all_dataset_ids_match": True,
        "all_document_metadata_match": True,
        "all_dataset_configuration_match": True,
        "cleanup_succeeded": True,
    }

    assert module.link_contract_ok(observed)
    observed["linked_file_ids"] = ["a", "b", "virtual"]
    observed["document_count"] = 3
    assert not module.link_contract_ok(observed)


def test_probe_retry_retries_nonzero_exit_and_returns_next_success():
    module = load_module()
    attempts = iter(
        [
            subprocess.CompletedProcess(["probe"], 1, "", "temporary"),
            subprocess.CompletedProcess(["probe"], 0, "ok", ""),
        ]
    )
    failures = []

    completed = module.run_probe_with_retry(
        lambda: next(attempts),
        max_attempts=2,
        on_failure=lambda attempt, kind, _result: failures.append((attempt, kind)),
        pause=lambda: None,
    )

    assert completed.returncode == 0
    assert failures == [(1, "nonzero")]


def test_hierarchy_contract_requires_exact_order_and_complete_folder_fields():
    module = load_module()
    observed = {
        "response": [200, 0],
        "expected_ids": ["file", "c", "b", "a", "root"],
        "returned_ids": ["file", "c", "b", "a", "root"],
        "all_required_fields_present": True,
        "database_path_matches": True,
        "cleanup_succeeded": True,
    }

    assert module.hierarchy_contract_ok(observed)
    observed["returned_ids"] = ["root", "a", "b", "c", "file"]
    assert not module.hierarchy_contract_ok(observed)


def test_commit_add_contract_requires_row_item_tree_and_content_object():
    module = load_module()
    observed = {
        "response": [200, 0],
        "response_id_present": True,
        "commit_row_count": 1,
        "item_row_count": 1,
        "parent_is_null": True,
        "operation": "add",
        "tree_entry_matches": True,
        "object_exists": True,
        "object_sha_matches": True,
    }

    assert module.commit_add_contract_ok(observed)
    observed["tree_entry_matches"] = False
    assert not module.commit_add_contract_ok(observed)


def test_commit_access_denial_requires_no_data_and_zero_mutation():
    module = load_module()
    observed = {
        "response": [200, 108],
        "data_absent": True,
        "commit_delta": 0,
        "file_unchanged": True,
        "object_unchanged": True,
    }

    assert module.commit_access_denied_contract_ok(observed)
    observed["response"] = [200, 0]
    assert not module.commit_access_denied_contract_ok(observed)


def test_commit_findings_point_to_commit_api():
    module = load_module()

    result = module._result(
        False,
        [{"name": "exercise"}],
        {"expected": True},
        "FM-COMMIT-IDOR-001",
        "commit finding",
    )

    assert result["findings"][0]["code_location"] == ("api/apps/restful_apis/file_commit_api.py")


def test_download_contract_requires_exact_bytes_type_size_and_cleanup():
    module = load_module()
    observed = {
        "response": [200, None],
        "bytes_match": True,
        "content_type_matches": True,
        "database_size_matches": True,
        "storage_size_matches": True,
        "storage_sha_matches": True,
        "cleanup_succeeded": True,
        "object_absent_after_cleanup": True,
    }

    assert module.download_contract_ok(observed)
    observed["bytes_match"] = False
    assert not module.download_contract_ok(observed)


def test_path_filename_contract_accepts_only_rejection_or_safe_basename():
    module = load_module()

    assert module.path_filename_attempt_ok(
        {
            "response": [200, 102],
            "new_row_count": 0,
            "returned_name": None,
            "returned_parent_is_requested": False,
            "unsafe_intermediate_count": 0,
        }
    )
    assert module.path_filename_attempt_ok(
        {
            "response": [200, 0],
            "new_row_count": 1,
            "returned_name": "escape.txt",
            "expected_basename": "escape.txt",
            "returned_parent_is_requested": True,
            "unsafe_intermediate_count": 0,
        }
    )
    assert not module.path_filename_attempt_ok(
        {
            "response": [200, 0],
            "new_row_count": 2,
            "returned_name": "escape.txt",
            "expected_basename": "escape.txt",
            "returned_parent_is_requested": False,
            "unsafe_intermediate_count": 1,
        }
    )


def test_concurrent_folder_contract_requires_one_winner_and_four_duplicates():
    module = load_module()
    observed = {
        "response_pairs": [[200, 0]] + [[200, 102]] * 4,
        "messages": [None] + ["Duplicated folder name in the same folder."] * 4,
        "database_row_count": 1,
        "all_requests_completed": True,
        "cleanup_succeeded": True,
    }

    assert module.concurrent_folder_contract_ok(observed, request_count=5)
    observed["database_row_count"] = 2
    assert not module.concurrent_folder_contract_ok(observed, request_count=5)


def test_unauthenticated_contract_requires_auth_rejection_and_zero_mutation():
    module = load_module()
    observed = {
        "http_status": 401,
        "code": 401,
        "data_absent": True,
        "file_delta": 0,
    }

    assert module.unauthenticated_contract_ok(observed)
    observed["http_status"] = 200
    observed["code"] = 0
    assert not module.unauthenticated_contract_ok(observed)


def test_cross_tenant_matrix_requires_every_path_denied_and_injected_rows_preserved():
    module = load_module()
    observed = {
        "denial_results": [True] * 8,
        "victim_delete_response": [200, 0],
        "attacker_rows_preserved": True,
        "attacker_objects_preserved": True,
        "active_cleanup_succeeded": True,
    }

    assert module.cross_tenant_matrix_contract_ok(observed)
    observed["denial_results"][3] = False
    assert not module.cross_tenant_matrix_contract_ok(observed)


def test_source_type_contract_preserves_application_empty_and_dialect_storage():
    module = load_module()
    common = {
        "api_source_type": "",
        "orm_source_type": "",
        "orm_query_hit": True,
        "cleanup_succeeded": True,
    }

    assert module.source_type_empty_contract_ok(
        "control",
        {**common, "physical_is_empty": True, "physical_is_null": False},
    )
    assert module.source_type_empty_contract_ok(
        "experiment",
        {**common, "physical_is_empty": False, "physical_is_null": True},
    )
    assert not module.source_type_empty_contract_ok(
        "experiment",
        {**common, "physical_is_empty": True, "physical_is_null": False},
    )


def test_dataset_document_cleanup_uses_document_endpoint_argument_order():
    module = load_module()
    calls = []
    module.DD._delete_documents = lambda *args: calls.append(args) or {"code": 0}

    result = module._delete_dataset_documents("TC-FM-066", "control", "auth", "cleanup", "dataset", ["document"])

    assert result == {"code": 0}
    assert calls == [
        (
            "TC-FM-066",
            "control",
            "auth",
            "cleanup",
            "dataset",
            ["document"],
        )
    ]


def test_storage_folder_cleanup_respects_fixed_and_per_bucket_modes():
    module = load_module()
    common = {
        "delete_response": [200, 0],
        "file_rows_remaining": 0,
        "object_exists_after": False,
        "logical_object_count_after": 0,
        "cleanup_succeeded": True,
    }

    assert module.storage_folder_cleanup_contract_ok({**common, "mode": "fixed_bucket", "physical_bucket_exists_after": True})
    assert module.storage_folder_cleanup_contract_ok({**common, "mode": "per_bucket", "physical_bucket_exists_after": False})
    assert not module.storage_folder_cleanup_contract_ok({**common, "mode": "fixed_bucket", "physical_bucket_exists_after": False})
    assert not module.storage_folder_cleanup_contract_ok(
        {
            **common,
            "mode": "fixed_bucket",
            "physical_bucket_exists_after": True,
            "logical_object_count_after": 1,
        }
    )


def test_repeated_link_contract_requires_one_rebuilt_relation_without_old_document():
    module = load_module()
    observed = {
        "responses": [[200, 0], [200, 0]],
        "first_relation_count": 1,
        "final_relation_count": 1,
        "final_dataset_matches": True,
        "old_document_absent_if_replaced": True,
        "cleanup_succeeded": True,
    }

    assert module.repeated_link_contract_ok(observed)
    observed["final_relation_count"] = 2
    assert not module.repeated_link_contract_ok(observed)


def test_skill_delete_contract_requires_go_success_gate_and_failure_preservation():
    module = load_module()
    observed = {
        "success_response": [200, 0],
        "success_go_called": True,
        "success_rows_absent": True,
        "success_object_absent": True,
        "failure_response_nonzero": True,
        "failure_go_called": True,
        "failure_rows_preserved": True,
        "failure_object_preserved": True,
        "cleanup_succeeded": True,
    }

    assert module.skill_delete_contract_ok(observed)
    observed["failure_rows_preserved"] = False
    assert not module.skill_delete_contract_ok(observed)


def test_team_file_download_contract_requires_membership_relation_and_exact_bytes():
    module = load_module()
    observed = {
        "membership_active": True,
        "dataset_permission": "team",
        "relation_count": 1,
        "download_response": [200, None],
        "bytes_match": True,
        "cleanup_succeeded": True,
    }

    assert module.team_file_download_contract_ok(observed)
    observed["membership_active"] = False
    assert not module.team_file_download_contract_ok(observed)


def test_secondary_user_cleanup_accepts_non_json_admin_success_after_row_is_gone():
    module = load_module()
    counts = iter([1, 0])
    module.DB._email_count = lambda _group, _emails: next(counts)

    def deleted_then_non_json(*_args):
        raise module.requests.exceptions.JSONDecodeError("empty response after successful delete", "", 0)

    module.DB._disable_and_delete_user = deleted_then_non_json

    assert module._cleanup_secondary_user("TC-FILE-ACL-002", "control", "secondary@fresh.invalid")


def test_registry_exposes_only_explicitly_implemented_cases_for_execution():
    module = load_module()

    assert set(module.RUNNERS) == set(module.CASE_TITLES)
    assert module.IMPLEMENTED_CASE_IDS == set(module.CASE_TITLES)
