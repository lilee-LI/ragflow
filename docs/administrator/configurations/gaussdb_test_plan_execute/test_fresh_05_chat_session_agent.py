from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


EXECUTE_DIR = Path(__file__).resolve().parent
MODULE_PATH = EXECUTE_DIR / "fresh_05_chat_session_agent.py"


def load_runner():
    assert MODULE_PATH.exists(), "fresh 05 runner must be created"
    spec = importlib.util.spec_from_file_location("fresh_05_chat_session_agent", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_runner_module_exists_and_loads() -> None:
    module = load_runner()
    assert module.BATCH_ID == module.DD.BATCH_ID


def test_current_plans_have_122_unique_ordered_cases() -> None:
    module = load_runner()
    ids = list(module.CASE_TITLES)
    assert len(ids) == 122
    assert len(ids) == len(set(ids))
    assert ids[:3] == ["TC-CS-001", "TC-CS-002", "TC-CS-003"]
    assert ids[99] == "TC-CS-100"
    assert ids[-3:] == [
        "TC-AGENT-COMP-003",
        "TC-AGENT-COMP-004",
        "TC-AGENT-COMP-005",
    ]


def test_runner_registry_is_exactly_all_plan_cases() -> None:
    module = load_runner()
    expected = [f"TC-CS-{number:03d}" for number in range(1, 101)]
    expected.extend(f"TC-CHAT-DEL-{number:03d}" for number in range(1, 6))
    expected.extend(f"TC-CHAT-PATCH-{number:03d}" for number in range(1, 4))
    expected.extend(f"TC-AGENT-WH-{number:03d}" for number in range(1, 10))
    expected.extend(f"TC-AGENT-COMP-{number:03d}" for number in range(1, 6))
    assert list(module.RUNNERS) == expected


def test_minimal_chat_contract_requires_defaults_and_group_storage() -> None:
    module = load_runner()
    common = {
        "http_status": 200,
        "code": 0,
        "id_present": True,
        "name_matches": True,
        "dataset_ids": [],
        "llm_inherited": True,
        "api_rerank_id": "",
        "top_n": 6,
        "top_k": 1024,
        "similarity_threshold": 0.1,
        "vector_similarity_weight": 0.3,
        "database_count": 1,
        "tenant_matches": True,
        "status": "1",
        "kb_ids": [],
        "cleanup_succeeded": True,
    }
    assert module.minimal_chat_contract_ok(
        "control",
        {**common, "physical_rerank_is_empty": True, "physical_rerank_is_null": False},
    )
    assert module.minimal_chat_contract_ok(
        "experiment",
        {**common, "physical_rerank_is_empty": False, "physical_rerank_is_null": True},
    )
    assert not module.minimal_chat_contract_ok(
        "experiment",
        {**common, "physical_rerank_is_empty": True, "physical_rerank_is_null": False},
    )


def test_full_chat_contract_requires_json_and_dataset_mapping() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "database_count": 1,
        "dataset_ids_match": True,
        "kb_names_match": True,
        "llm_matches": True,
        "rerank_matches": True,
        "llm_setting_matches": True,
        "prompt_config_matches": True,
        "scalar_fields_match": True,
        "database_json_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.full_chat_contract_ok(observed)
    assert not module.full_chat_contract_ok({**observed, "database_json_matches": False})


def test_chat_rejection_contract_requires_no_database_delta() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 102,
        "message": "Duplicated chat name in creating chat.",
        "database_delta": 0,
        "cleanup_succeeded": True,
    }
    assert module.chat_rejection_contract_ok(observed, expected_code=102, message_fragment="Duplicated chat name")
    assert not module.chat_rejection_contract_ok(
        {**observed, "database_delta": 1},
        expected_code=102,
        message_fragment="Duplicated chat name",
    )


def test_dataset_validation_contract_distinguishes_empty_and_mixed_embeddings() -> None:
    module = load_runner()
    base = {
        "http_status": 200,
        "code": 102,
        "database_delta": 0,
        "cleanup_succeeded": True,
    }
    assert module.chat_rejection_contract_ok(
        {**base, "message": "The dataset x doesn't own parsed file"},
        expected_code=102,
        message_fragment="doesn't own parsed file",
    )
    assert module.chat_rejection_contract_ok(
        {**base, "message": "Datasets use different embedding models"},
        expected_code=102,
        message_fragment="different embedding models",
    )


def test_group_result_shape_requires_status_steps_and_oracle() -> None:
    module = load_runner()
    assert module.group_result_shape_ok({"status": "PASS", "steps": [{"name": "probe"}], "oracle": {"code": 0}})
    assert not module.group_result_shape_ok({"status": "PASS", "steps": [], "oracle": {"code": 0}})
    assert not module.group_result_shape_ok({"status": "UNKNOWN", "steps": [{"name": "probe"}], "oracle": {}})


def test_chat_list_contract_checks_shape_order_filter_and_database() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "actual_count": 3,
        "expected_count": 3,
        "total_matches": True,
        "ids_exact": True,
        "required_fields_present": True,
        "descending_order": True,
        "pages_disjoint": True,
        "filter_exact": True,
        "cleanup_succeeded": True,
    }
    assert module.chat_list_contract_ok(observed)
    assert not module.chat_list_contract_ok({**observed, "pages_disjoint": False})


def test_chat_detail_contract_requires_api_database_json_match() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "id_matches": True,
        "dataset_mapping_matches": True,
        "json_fields_match": True,
        "database_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.chat_detail_contract_ok(observed)
    assert not module.chat_detail_contract_ok({**observed, "database_matches": False})


def test_chat_update_contract_requires_requested_changes_and_preservation() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "response_matches": True,
        "database_matches": True,
        "json_merge_matches": True,
        "unsubmitted_fields_preserved": True,
        "cleanup_succeeded": True,
    }
    assert module.chat_update_contract_ok(observed)
    assert not module.chat_update_contract_ok({**observed, "unsubmitted_fields_preserved": False})


def test_empty_chat_field_contract_distinguishes_database_dialects() -> None:
    module = load_runner()
    common = {
        "http_status": 200,
        "code": 0,
        "api_value": "",
        "orm_value": "",
        "cleanup_succeeded": True,
    }
    assert module.empty_chat_field_contract_ok("control", {**common, "physical_is_empty": True, "physical_is_null": False})
    assert module.empty_chat_field_contract_ok("experiment", {**common, "physical_is_empty": False, "physical_is_null": True})
    assert not module.empty_chat_field_contract_ok("experiment", {**common, "physical_is_empty": True, "physical_is_null": False})


def test_chat_soft_delete_contract_preserves_row_and_session() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "dialog_count_after": 1,
        "dialog_status_after": "0",
        "list_absent": True,
        "detail_denied": True,
        "session_count_unchanged": True,
        "cleanup_succeeded": True,
    }
    assert module.chat_soft_delete_contract_ok(observed)
    assert not module.chat_soft_delete_contract_ok({**observed, "session_count_unchanged": False})


def test_session_create_contract_requires_api_database_and_prologue() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "id_present": True,
        "chat_id_matches": True,
        "name": "New session",
        "database_count": 1,
        "database_matches": True,
        "prologue_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.session_create_contract_ok(observed, expected_name="New session")
    assert not module.session_create_contract_ok({**observed, "prologue_matches": False}, expected_name="New session")


def test_session_list_contract_requires_paging_order_mapping_and_database() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "actual_count": 2,
        "expected_count": 2,
        "ids_exact": True,
        "chat_id_mapping_matches": True,
        "descending_order": True,
        "database_count": 3,
        "cleanup_succeeded": True,
    }
    assert module.session_list_contract_ok(observed)
    assert not module.session_list_contract_ok({**observed, "chat_id_mapping_matches": False})


def test_session_detail_contract_requires_messages_references_and_avatar() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "id_matches": True,
        "message_fields_present": True,
        "messages_match_database": True,
        "reference_matches_database": True,
        "chunks_format_present": True,
        "avatar_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.session_detail_contract_ok(observed)
    assert not module.session_detail_contract_ok({**observed, "reference_matches_database": False})


def test_session_update_contract_allows_only_name() -> None:
    module = load_runner()
    observed = {
        "name_http_status": 200,
        "name_code": 0,
        "name_response_matches": True,
        "name_database_matches": True,
        "messages_http_status": 200,
        "messages_code": 102,
        "reference_http_status": 200,
        "reference_code": 102,
        "messages_unchanged": True,
        "reference_unchanged": True,
        "cleanup_succeeded": True,
    }
    assert module.session_update_contract_ok(observed)
    assert not module.session_update_contract_ok({**observed, "messages_code": 0})


def test_session_delete_contract_requires_physical_removal() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "deleted_count": 2,
        "expected_deleted_count": 2,
        "remaining_count": 1,
        "expected_remaining_count": 1,
        "targets_physically_absent": True,
        "cleanup_succeeded": True,
    }
    assert module.session_delete_contract_ok(observed)
    assert not module.session_delete_contract_ok({**observed, "targets_physically_absent": False})


def test_message_delete_contract_requires_pair_and_reference_removal() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "target_pair_absent": True,
        "message_count_delta": 2,
        "reference_count_delta": 1,
        "untargeted_pair_present": True,
        "cleanup_succeeded": True,
    }
    assert module.message_delete_contract_ok(observed)
    assert not module.message_delete_contract_ok({**observed, "reference_count_delta": 0})


def test_feedback_contract_handles_valid_and_rejected_updates() -> None:
    module = load_runner()
    valid = {
        "http_status": 200,
        "code": 0,
        "assistant_found": True,
        "thumbup_matches": True,
        "feedback_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.feedback_contract_ok(valid, expected_code=0)
    assert not module.feedback_contract_ok({**valid, "thumbup_matches": False}, expected_code=0)

    rejected = {
        "http_status": 200,
        "code": 102,
        "message": "thumbup must be a boolean",
        "messages_unchanged": True,
        "cleanup_succeeded": True,
    }
    assert module.feedback_contract_ok(rejected, expected_code=102)


def test_request_records_transport_failure_without_secret_or_exception_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = load_runner()
    module.RAW_DIR = tmp_path

    def raise_timeout(*_args, **_kwargs):
        raise module.DD.requests.ReadTimeout("sensitive transport detail")

    monkeypatch.setattr(module.DD, "_request", raise_timeout)
    result = module._request(
        "TC-CS-030",
        "control",
        "completion_timeout",
        "fresh-sensitive-auth-value",
        "POST",
        "/chat/completions",
        payload={"question": "fresh"},
    )

    assert result["http_status"] == 0
    assert result["code"] is None
    assert result["message"] == "transport failure: ReadTimeout"
    raw_files = list(tmp_path.glob("*.json"))
    assert len(raw_files) == 1
    raw = raw_files[0].read_text()
    assert "fresh-sensitive-auth-value" not in raw
    assert "sensitive transport detail" not in raw


def test_stream_completion_contract_requires_sse_terminator_and_persistence() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "content_type_is_sse": True,
        "event_count": 3,
        "all_events_parsed": True,
        "final_event_matches": True,
        "answer_event_present": True,
        "session_id_matches": True,
        "database_session_count": 1,
        "history_preserved": True,
        "message_pair_appended": True,
        "cleanup_succeeded": True,
    }
    assert module.stream_completion_contract_ok(observed)
    assert not module.stream_completion_contract_ok({**observed, "final_event_matches": False})


def test_nonstream_completion_contract_requires_answer_reference_and_json_pair() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "answer_nonempty": True,
        "reference_present": True,
        "session_id_present": True,
        "user_content_matches": True,
        "message_pair_present": True,
        "cleanup_succeeded": True,
    }
    assert module.nonstream_completion_contract_ok(observed)
    assert not module.nonstream_completion_contract_ok({**observed, "answer_nonempty": False})


def test_completion_validation_contract_requires_three_101s_and_zero_delta() -> None:
    module = load_runner()
    observed = {
        "http_statuses": [200, 200, 200],
        "codes": [101, 101, 101],
        "list_messages_match": True,
        "assistant_only_message_mentions_user": True,
        "database_delta": 0,
        "cleanup_succeeded": True,
    }
    assert module.completion_validation_contract_ok(observed)
    assert not module.completion_validation_contract_ok({**observed, "codes": [101, 101, 0]})


def test_pass_all_history_contract_requires_exact_client_baseline_plus_answer() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "answer_nonempty": True,
        "session_id_matches": True,
        "client_baseline_exact": True,
        "assistant_appended": True,
        "final_message_count": 4,
        "cleanup_succeeded": True,
    }
    assert module.pass_all_history_contract_ok(observed)
    assert not module.pass_all_history_contract_ok({**observed, "client_baseline_exact": False})


def test_audio_speech_contract_requires_mpeg_bytes_and_model_cleanup() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "content_type_is_mpeg": True,
        "body_matches_stub": True,
        "stub_call_observed": True,
        "default_model_set": True,
        "default_model_restored": True,
        "provider_instance_removed": True,
        "chat_cleanup_succeeded": True,
    }
    assert module.audio_speech_contract_ok(observed)
    assert not module.audio_speech_contract_ok({**observed, "body_matches_stub": False})


def test_audio_transcription_contract_requires_text_and_fixture_cleanup() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "text_matches": True,
        "multipart_call_observed": True,
        "valid_wav_fixture": True,
        "default_model_set": True,
        "default_model_restored": True,
        "provider_instance_removed": True,
    }
    assert module.audio_transcription_contract_ok(observed)
    assert not module.audio_transcription_contract_ok({**observed, "multipart_call_observed": False})


def test_mindmap_contract_requires_recursive_node_shape_and_cleanup() -> None:
    module = load_runner()
    assert "RAGFlow的架构" in module.chat_mindmap_fixture_content()
    observed = {
        "http_status": 200,
        "code": 0,
        "root_shape_matches": True,
        "node_count": 3,
        "hierarchy_present": True,
        "cleanup_succeeded": True,
    }
    assert module.mindmap_contract_ok(observed)
    assert not module.mindmap_contract_ok({**observed, "node_count": 1})


def test_recommendation_contract_requires_nonempty_string_list() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "list_nonempty": True,
        "all_items_nonempty_strings": True,
        "database_delta": 0,
    }
    assert module.recommendation_contract_ok(observed)
    assert not module.recommendation_contract_ok({**observed, "list_nonempty": False})


def test_ensure_provider_adds_only_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_runner()
    calls = []
    responses = iter(
        [
            {"code": 0, "data": [], "raw_sha256": "before"},
            {"code": 0, "data": None, "raw_sha256": "add"},
            {
                "code": 0,
                "data": [{"name": "OpenAI"}],
                "raw_sha256": "after",
            },
        ]
    )

    def fake_request(*args, **kwargs):
        calls.append((args, kwargs))
        return next(responses)

    monkeypatch.setattr(module, "_request", fake_request)
    result = module._ensure_provider("TC-CS-045", "control", "auth", "OpenAI")

    assert result["ready"] is True
    assert result["added"] is True
    assert [call[0][4] for call in calls] == ["GET", "PUT", "GET"]


def test_agent_create_contract_requires_api_database_dsl_and_cleanup() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "id_present": True,
        "title_matches": True,
        "canvas_category": "agent_canvas",
        "database_count": 1,
        "owner_matches": True,
        "database_dsl_matches": True,
        "physical_tags_is_empty": True,
        "physical_tags_is_null": False,
        "cleanup_succeeded": True,
    }
    assert module.agent_create_contract_ok("control", observed)
    assert module.agent_create_contract_ok(
        "experiment",
        {
            **observed,
            "physical_tags_is_empty": False,
            "physical_tags_is_null": True,
        },
    )
    assert not module.agent_create_contract_ok("experiment", observed)
    assert not module.agent_create_contract_ok("experiment", {**observed, "code": 100, "database_count": 0})


def test_agent_duplicate_contract_requires_case_insensitive_rejection() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 102,
        "message": "FRESH-CS-050-DUPLICATE already exists.",
        "database_delta": 0,
        "fixture_ready": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_duplicate_contract_ok(observed)
    assert not module.agent_duplicate_contract_ok({**observed, "database_delta": 1})


def test_agent_list_contract_requires_exact_fixture_page_and_total() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "canvas_is_list": True,
        "fixture_ids_present": True,
        "total_matches_database": True,
        "required_fields_present": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_list_contract_ok(observed)
    assert not module.agent_list_contract_ok({**observed, "fixture_ids_present": False})


def test_agent_tag_filter_contract_requires_only_exact_tag_matches() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "matched_id_present": True,
        "unmatched_id_absent": True,
        "all_returned_tags_match": True,
        "database_tags_match": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_tag_filter_contract_ok(observed)
    assert not module.agent_tag_filter_contract_ok({**observed, "unmatched_id_absent": False})


def test_agent_detail_contract_requires_full_dsl_without_embedded_relations() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "id_matches": True,
        "dsl_matches_request": True,
        "dsl_matches_database": True,
        "last_publish_time_present": True,
        "versions_absent": True,
        "datasets_absent": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_detail_contract_ok(observed)
    assert not module.agent_detail_contract_ok({**observed, "datasets_absent": False})


def test_agent_snapshot_query_avoids_release_reserved_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def execute(self, query, params):
            queries.append((query, params))
            assert "release" not in query.lower()

        def fetchone(self):
            return (
                "agent-id",
                "owner-id",
                "title",
                "agent_canvas",
                "me",
                "",
                {"components": {}},
                0,
            )

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(module.DB, "_open_database", lambda _group: (Connection(), "user", "schema"))
    snapshot = module._agent_snapshot("control", "agent-id")

    assert snapshot["count"] == 1
    assert snapshot["physical_tags_is_empty"] is True
    assert queries[0][1] == ("agent-id",)


def test_duplicate_agent_fixture_is_snapshotted_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    state = {"cleaned": False}
    responses = iter(
        [
            {
                "http_status": 200,
                "code": 0,
                "message": None,
                "data": {"id": "fixture-id"},
                "raw_sha256": "fixture",
            },
            {
                "http_status": 200,
                "code": 102,
                "message": "already exists",
                "data": False,
                "raw_sha256": "duplicate",
            },
        ]
    )
    counts = iter([1, 1])
    monkeypatch.setattr(module, "_run_case", lambda _case_id, execute: execute("control", {"auth": "a", "tenant_id": "t"}))
    monkeypatch.setattr(module, "_cleanup_agent_prefix", lambda *_args: True)
    monkeypatch.setattr(module, "_create_agent", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(module, "_visible_agent_count", lambda *_args: next(counts))
    monkeypatch.setattr(
        module,
        "_agent_snapshot",
        lambda *_args: {"count": 0 if state["cleaned"] else 1},
    )

    def cleanup(*_args):
        state["cleaned"] = True
        return True

    monkeypatch.setattr(module, "_cleanup_created_agents", cleanup)
    assert module.run_cs050()["status"] == "PASS"


def test_agent_tags_are_snapshotted_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_runner()
    state = {"cleaned": False}
    created = iter(
        [
            {"code": 0, "data": {"id": "matched"}},
            {"code": 0, "data": {"id": "unmatched"}},
        ]
    )
    monkeypatch.setattr(module, "_run_case", lambda _case_id, execute: execute("control", {"auth": "a", "tenant_id": "t"}))
    monkeypatch.setattr(module, "_cleanup_agent_prefix", lambda *_args: True)
    monkeypatch.setattr(module, "_create_agent", lambda *_args, **_kwargs: next(created))
    monkeypatch.setattr(
        module,
        "_list_agents",
        lambda *_args, **_kwargs: {
            "http_status": 200,
            "code": 0,
            "data": {"canvas": [{"id": "matched", "tags": "fresh-agent,测试标签"}]},
            "raw_sha256": "list",
        },
    )

    def snapshot(_group, agent_id):
        if state["cleaned"]:
            return {"count": 0}
        suffix = "测试标签" if agent_id == "matched" else "测试标签扩展"
        return {"count": 1, "tags": f"fresh-agent,{suffix}"}

    monkeypatch.setattr(module, "_agent_snapshot", snapshot)

    def cleanup(*_args):
        state["cleaned"] = True
        return True

    monkeypatch.setattr(module, "_cleanup_created_agents", cleanup)
    assert module.run_cs052()["status"] == "PASS"


def test_agent_update_contract_requires_version_and_replica_sync() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "title_matches": True,
        "database_dsl_matches": True,
        "version_count_delta": 1,
        "latest_version_dsl_matches": True,
        "replica_exists": True,
        "replica_dsl_matches": True,
        "replica_title_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_update_contract_ok(observed)
    assert not module.agent_update_contract_ok({**observed, "replica_dsl_matches": False})


def test_agent_delete_contract_requires_no_canvas_version_session_or_replica() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "canvas_count_after": 0,
        "version_count_after": 0,
        "session_count_after": 0,
        "replica_exists_after": False,
    }
    assert module.agent_delete_contract_ok(observed)
    assert not module.agent_delete_contract_ok({**observed, "version_count_after": 1})


def test_nonowner_agent_delete_contract_requires_exact_denial_and_no_mutation() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 103,
        "message": "Only the owner of the agent is authorized for this operation.",
        "fixture_ready": True,
        "canvas_unchanged": True,
        "secondary_ready": True,
        "agent_cleanup_succeeded": True,
        "secondary_cleanup_succeeded": True,
    }
    assert module.nonowner_agent_delete_contract_ok(observed)
    assert not module.nonowner_agent_delete_contract_ok({**observed, "message": "permission denied"})


def test_agent_reset_contract_requires_runtime_clear_and_topology_preservation() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "path_cleared": True,
        "history_cleared": True,
        "retrieval_cleared": True,
        "memory_cleared": True,
        "system_globals_cleared": True,
        "custom_globals_preserved": True,
        "component_topology_preserved": True,
        "component_runtime_cleared": True,
        "database_dsl_matches": True,
        "replica_dsl_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_reset_contract_ok(observed)
    assert not module.agent_reset_contract_ok({**observed, "component_topology_preserved": False})


def test_agent_template_contract_requires_multilingual_json_and_database_match() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "list_nonempty": True,
        "required_fields_present": True,
        "multilingual_json_types": True,
        "database_count_matches": True,
        "database_rows_match": True,
    }
    assert module.agent_template_contract_ok(observed)
    assert not module.agent_template_contract_ok({**observed, "multilingual_json_types": False})


def test_agent_prompt_contract_requires_all_four_nonempty_strings() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "keys_exact": True,
        "all_nonempty_strings": True,
    }
    assert module.agent_prompt_contract_ok(observed)
    assert not module.agent_prompt_contract_ok({**observed, "keys_exact": False})


def test_agent_version_list_contract_requires_fields_order_and_database_mapping() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "version_count": 3,
        "required_fields_present": True,
        "descending_order": True,
        "database_ids_match": True,
        "database_scalar_fields_match": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_version_list_contract_ok(observed)
    assert not module.agent_version_list_contract_ok({**observed, "required_fields_present": False})


def test_agent_version_detail_contract_requires_full_dsl_database_roundtrip() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "id_matches": True,
        "agent_id_matches": True,
        "dsl_present": True,
        "dsl_matches_database": True,
        "scalar_fields_match": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_version_detail_contract_ok(observed)
    assert not module.agent_version_detail_contract_ok({**observed, "dsl_matches_database": False})


def test_agent_session_create_contract_requires_json_database_roundtrip() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "id_present": True,
        "name_matches": True,
        "dsl_present": True,
        "database_count": 1,
        "database_mapping_matches": True,
        "json_fields_match": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_session_create_contract_ok(observed)
    assert not module.agent_session_create_contract_ok({**observed, "json_fields_match": False})


def test_agent_session_list_contract_requires_fields_total_order_and_database() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "session_count": 3,
        "total_matches": True,
        "ids_exact": True,
        "required_fields_present": True,
        "descending_order": True,
        "database_mapping_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_session_list_contract_ok(observed)
    assert not module.agent_session_list_contract_ok({**observed, "required_fields_present": False})


def test_agent_session_delete_contract_requires_physical_removal() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "target_count_after": 0,
        "cleanup_succeeded": True,
    }
    assert module.agent_session_delete_contract_ok(observed)
    assert not module.agent_session_delete_contract_ok({**observed, "target_count_after": 1})


def test_agent_session_bulk_delete_contract_preserves_untargeted_session() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "target_ids_absent": True,
        "untargeted_id_present": True,
        "remaining_count": 1,
        "cleanup_succeeded": True,
    }
    assert module.agent_session_bulk_delete_contract_ok(observed)
    assert not module.agent_session_bulk_delete_contract_ok({**observed, "untargeted_id_present": False})


def test_agent_log_contract_requires_nonempty_endpoint_and_redis_match() -> None:
    module = load_runner()
    observed = {
        "http_status": 200,
        "code": 0,
        "fixture_ready": True,
        "execution_succeeded": True,
        "message_id_present": True,
        "redis_log_present": True,
        "log_nonempty": True,
        "endpoint_matches_redis": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_log_contract_ok(observed)
    assert not module.agent_log_contract_ok({**observed, "log_nonempty": False})


def test_webhook_immediate_contract_requires_body_trace_and_no_session_write() -> None:
    module = load_runner()
    observed = {
        "http_status": 202,
        "response_body_matches": True,
        "fixture_ready": True,
        "trace_webhook_id_present": True,
        "trace_events_present": True,
        "trace_finished": True,
        "trace_payload_matches": True,
        "session_database_delta": 0,
        "cleanup_succeeded": True,
    }
    assert module.webhook_immediate_contract_ok(observed)
    assert not module.webhook_immediate_contract_ok({**observed, "trace_finished": False})


def test_webhook_token_contract_requires_valid_success_and_invalid_400() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "valid_http_status": 200,
        "valid_body_matches": True,
        "invalid_http_status": 400,
        "invalid_code": 400,
        "invalid_message_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.webhook_token_contract_ok(observed)
    assert not module.webhook_token_contract_ok({**observed, "invalid_http_status": 200})


def test_webhook_unconfigured_contract_requires_explicit_400_message() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 400,
        "code": 400,
        "message": "Webhook not configured for this agent.",
        "cleanup_succeeded": True,
    }
    assert module.webhook_unconfigured_contract_ok(observed)
    assert not module.webhook_unconfigured_contract_ok({**observed, "message": "internal error"})


def test_webhook_owner_test_contract_requires_owner_success_and_exact_denial() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "owner_http_status": 200,
        "owner_body_matches": True,
        "nonowner_http_status": 200,
        "nonowner_code": 103,
        "nonowner_message": "Only the owner of the agent is authorized for this operation.",
        "agent_cleanup_succeeded": True,
        "secondary_cleanup_succeeded": True,
    }
    assert module.webhook_owner_test_contract_ok(observed)
    assert not module.webhook_owner_test_contract_ok({**observed, "nonowner_code": 0})


def test_webhook_trace_contract_requires_id_events_and_finished_state() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "initial_http_status": 200,
        "initial_code": 0,
        "next_since_ts_present": True,
        "trigger_http_status": 200,
        "webhook_id_present": True,
        "events_present": True,
        "finished": True,
        "payload_matches": True,
        "cleanup_succeeded": True,
    }
    assert module.webhook_trace_contract_ok(observed)
    assert not module.webhook_trace_contract_ok({**observed, "events_present": False})


def test_webhook_body_size_contract_requires_400_and_no_execution_trace() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 400,
        "code": 400,
        "message_matches": True,
        "request_over_limit": True,
        "trace_absent": True,
        "cleanup_succeeded": True,
    }
    assert module.webhook_body_size_contract_ok(observed)
    assert not module.webhook_body_size_contract_ok({**observed, "trace_absent": False})


def test_chatbot_completion_contract_requires_two_streams_and_database_history() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "first_http_status": 200,
        "first_content_type_is_sse": True,
        "first_parse_error_count": 0,
        "prologue_matches": True,
        "session_id_present": True,
        "first_final_event_matches": True,
        "first_database_mapping_matches": True,
        "second_http_status": 200,
        "second_content_type_is_sse": True,
        "second_parse_error_count": 0,
        "second_answer_nonempty": True,
        "second_final_event_matches": True,
        "second_database_history_matches": True,
        "token_cleanup_succeeded": True,
        "chat_cleanup_succeeded": True,
    }
    assert module.chatbot_completion_contract_ok(observed)
    assert not module.chatbot_completion_contract_ok({**observed, "second_database_history_matches": False})


def test_chatbot_info_contract_requires_exact_metadata_fields() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "required_fields_exact": True,
        "title_matches": True,
        "avatar_matches": True,
        "prologue_matches": True,
        "llm_id_matches": True,
        "has_tavily_key_matches": True,
        "token_cleanup_succeeded": True,
        "chat_cleanup_succeeded": True,
    }
    assert module.chatbot_info_contract_ok(observed)
    assert not module.chatbot_info_contract_ok({**observed, "required_fields_exact": False})


def test_agentbot_completion_contract_requires_sse_session_and_database_pair() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "content_type_is_sse": True,
        "parse_error_count": 0,
        "event_count_positive": True,
        "message_event_nonempty": True,
        "error_event_absent": True,
        "session_id_present": True,
        "database_message_pair_matches": True,
        "session_cleanup_succeeded": True,
        "agent_cleanup_succeeded": True,
        "token_cleanup_succeeded": True,
    }
    assert module.agentbot_completion_contract_ok(observed)
    assert not module.agentbot_completion_contract_ok({**observed, "error_event_absent": False})


def test_agentbot_inputs_contract_requires_plan_field_names_and_values() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "required_fields_present": True,
        "input_form_matches": True,
        "prologue_matches": True,
        "mode_matches": True,
        "title_matches": True,
        "avatar_matches": True,
        "agent_cleanup_succeeded": True,
        "token_cleanup_succeeded": True,
    }
    assert module.agentbot_inputs_contract_ok(observed)
    assert not module.agentbot_inputs_contract_ok({**observed, "required_fields_present": False})


def test_searchbot_ask_contract_requires_answer_reference_and_terminal_event() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "content_type_is_sse": True,
        "parse_error_count": 0,
        "answer_nonempty": True,
        "error_event_absent": True,
        "expected_chunk_referenced": True,
        "final_event_matches": True,
        "search_cleanup_succeeded": True,
        "dataset_cleanup_succeeded": True,
        "token_cleanup_succeeded": True,
    }
    assert module.searchbot_ask_contract_ok(observed)
    assert not module.searchbot_ask_contract_ok({**observed, "expected_chunk_referenced": False})


def test_searchbot_config_forces_deterministic_zero_threshold_retrieval() -> None:
    module = load_runner()
    config = module.searchbot_fixture_config("dataset-id")
    assert config["kb_ids"] == ["dataset-id"]
    assert config["similarity_threshold"] == 0.0
    assert config["vector_similarity_weight"] == 1.0
    assert "什么是向量检索？" in module.searchbot_ask_fixture_content()


def test_searchbot_retrieval_contract_requires_expected_chunk_and_scores() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "chunks_nonempty": True,
        "expected_chunk_present": True,
        "scores_present": True,
        "dataset_cleanup_succeeded": True,
        "token_cleanup_succeeded": True,
    }
    assert module.searchbot_retrieval_contract_ok(observed)
    assert not module.searchbot_retrieval_contract_ok({**observed, "scores_present": False})


def test_searchbot_retrieval_and_mindmap_fixtures_match_plan_questions() -> None:
    module = load_runner()
    assert "文档解析流程" in module.searchbot_retrieval_fixture_content()
    assert "系统架构" in module.searchbot_mindmap_fixture_content()


def test_searchbot_mindmap_contract_requires_nonempty_structure() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "mindmap_nonempty": True,
        "mindmap_structure_valid": True,
        "search_cleanup_succeeded": True,
        "dataset_cleanup_succeeded": True,
        "token_cleanup_succeeded": True,
    }
    assert module.searchbot_mindmap_contract_ok(observed)
    assert not module.searchbot_mindmap_contract_ok({**observed, "mindmap_structure_valid": False})


def test_chat_model_roundtrip_contract_requires_group_physical_semantics() -> None:
    module = load_runner()
    common = {
        "fixture_ready": True,
        "initial_api_empty": True,
        "valid_update_response": [200, 0],
        "valid_api_matches": True,
        "valid_database_matches": True,
        "final_update_response": [200, 0],
        "final_api_empty": True,
        "cleanup_succeeded": True,
    }
    assert module.chat_model_roundtrip_contract_ok(
        "control",
        {
            **common,
            "initial_physical_empty": True,
            "initial_physical_null": False,
            "final_physical_empty": True,
            "final_physical_null": False,
        },
    )
    assert module.chat_model_roundtrip_contract_ok(
        "experiment",
        {
            **common,
            "initial_physical_empty": False,
            "initial_physical_null": True,
            "final_physical_empty": False,
            "final_physical_null": True,
        },
    )
    assert not module.chat_model_roundtrip_contract_ok(
        "experiment",
        {
            **common,
            "initial_physical_empty": True,
            "initial_physical_null": False,
            "final_physical_empty": True,
            "final_physical_null": False,
        },
    )


def test_nested_json_empty_contract_requires_exact_database_and_api_values() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "api_values_are_empty_strings": True,
        "database_values_are_empty_strings": True,
        "other_prompt_fields_preserved": True,
        "cleanup_succeeded": True,
    }
    assert module.nested_json_empty_contract_ok(observed)
    assert not module.nested_json_empty_contract_ok({**observed, "database_values_are_empty_strings": False})


def test_unauthenticated_contract_requires_real_missing_header_and_http_401() -> None:
    module = load_runner()
    observed = {
        "chat_authorization_header_absent": True,
        "chat_http_status": 401,
        "agent_authorization_header_absent": True,
        "agent_http_status": 401,
        "database_delta": 0,
    }
    assert module.unauthenticated_chat_agent_contract_ok(observed)
    assert not module.unauthenticated_chat_agent_contract_ok({**observed, "chat_authorization_header_absent": False})


def test_special_chat_names_contract_requires_exact_utf8_roundtrip() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "response_statuses": [[200, 0], [200, 0]],
        "api_names_exact": True,
        "database_names_exact": True,
        "utf8_byte_lengths_valid": True,
        "cleanup_succeeded": True,
    }
    assert module.special_chat_names_contract_ok(observed)
    assert not module.special_chat_names_contract_ok({**observed, "database_names_exact": False})


def test_large_history_contract_requires_fifty_round_baseline_plus_two_messages() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "baseline_completion_count": 50,
        "baseline_all_success": True,
        "baseline_message_count": 101,
        "final_http_status": 200,
        "final_code": 0,
        "final_message_delta": 2,
        "final_message_count": 103,
        "all_messages_valid": True,
        "no_truncation": True,
        "cleanup_succeeded": True,
    }
    assert module.large_history_contract_ok(observed)
    assert not module.large_history_contract_ok({**observed, "final_message_delta": 1})


def test_history_no_truncation_accepts_arbitrary_nonempty_assistant_text() -> None:
    module = load_runner()
    messages = [
        {"role": "assistant", "content": "prologue"},
        {"role": "user", "content": "q1", "id": "id1"},
        {"role": "assistant", "content": "arbitrary complete a1", "id": "id1"},
        {"role": "user", "content": "q2", "id": "id2"},
        {"role": "assistant", "content": "different complete a2", "id": "id2"},
    ]
    assert module.history_messages_not_truncated(messages, "prologue", ["q1", "q2"])
    assert not module.history_messages_not_truncated(messages[:-1], "prologue", ["q1", "q2"])


def test_concurrent_session_contract_requires_five_unique_database_rows() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "response_statuses": [[200, 0]] * 5,
        "unique_id_count": 5,
        "database_count": 5,
        "database_ids_exact": True,
        "database_names_exact": True,
        "cleanup_succeeded": True,
    }
    assert module.concurrent_session_contract_ok(observed)
    assert not module.concurrent_session_contract_ok({**observed, "unique_id_count": 4})


def test_float_sanitization_contract_requires_isolated_and_strict_json_checks() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "isolated_exit_code": 0,
        "isolated_nested_values_sanitized": True,
        "isolated_other_values_unchanged": True,
        "nonstream_http_status": 200,
        "nonstream_code": 0,
        "nonstream_strict_parse_ok": True,
        "stream_http_status": 200,
        "stream_strict_parse_error_count": 0,
        "stream_final_event_matches": True,
        "stream_error_event_absent": True,
        "nonstandard_literal_absent": True,
        "cleanup_succeeded": True,
    }
    assert module.float_sanitization_contract_ok(observed)
    assert not module.float_sanitization_contract_ok({**observed, "stream_strict_parse_error_count": 1})
    assert not module.float_sanitization_contract_ok({**observed, "stream_error_event_absent": False})


def test_isolated_float_probe_extracts_current_function_without_service_import() -> None:
    module = load_runner()
    source = module.isolated_float_probe_source()
    assert "ast.parse" in source
    assert "_sanitize_json_floats" in source
    assert "from api.apps.restful_apis.chat_api import" not in source


def test_large_agent_dsl_contract_requires_hundred_nodes_over_100k_roundtrip() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "component_count": 100,
        "request_json_bytes_over_100k": True,
        "detail_http_status": 200,
        "detail_code": 0,
        "api_dsl_exact": True,
        "database_dsl_exact": True,
        "serialized_lengths_exact": True,
        "cleanup_succeeded": True,
    }
    assert module.large_agent_dsl_contract_ok(observed)
    assert not module.large_agent_dsl_contract_ok({**observed, "component_count": 99})


def test_agent_completion_contract_requires_both_modes_and_persistence() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "stream_http_status": 200,
        "stream_content_type_ok": True,
        "stream_done_seen": True,
        "stream_parse_error_count": 0,
        "stream_error_absent": True,
        "stream_answer_nonempty": True,
        "nonstream_http_status": 200,
        "nonstream_code": 0,
        "nonstream_answer_nonempty": True,
        "session_count": 2,
        "session_ids_match": True,
        "sessions_have_messages": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_completion_contract_ok(observed)
    assert not module.agent_completion_contract_ok({**observed, "stream_done_seen": False})
    assert not module.agent_completion_contract_ok({**observed, "sessions_have_messages": False})


def test_agent_sse_frame_parser_recognizes_done_and_strict_json() -> None:
    module = load_runner()
    assert module._parse_agent_sse_frame("[DONE]") == ("done", None)
    assert module._parse_agent_sse_frame('{"event":"message"}') == (
        "event",
        {"event": "message"},
    )
    try:
        module._parse_agent_sse_frame('{"value":NaN}')
    except ValueError:
        pass
    else:
        raise AssertionError("nonstandard JSON constant must be rejected")


def test_agent_llm_dsl_has_real_llm_path_and_query_input() -> None:
    module = load_runner()
    dsl = module._agent_llm_dsl("fresh-model")
    assert list(dsl["components"]) == ["begin", "LLM:Fresh", "Message:Fresh"]
    assert dsl["components"]["LLM:Fresh"]["obj"]["component_name"] == "LLM"
    params = dsl["components"]["LLM:Fresh"]["obj"]["params"]
    assert params["llm_id"] == "fresh-model"
    assert params["prompts"] == [{"role": "user", "content": "{sys.query}"}]
    assert dsl["retrieval"] == []


def test_runtime_agent_dsl_initializes_canvas_retrieval_as_history_list() -> None:
    module = load_runner()
    assert module._agent_dsl(probe="runtime")["retrieval"] == []


def test_agent_debug_contract_requires_real_input_form_and_output() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "input_form_http_status": 200,
        "input_form_code": 0,
        "input_key_present": True,
        "debug_http_status": 200,
        "debug_code": 0,
        "output_nonempty": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_debug_contract_ok(observed)
    assert not module.agent_debug_contract_ok({**observed, "input_key_present": False})


def test_agent_file_contract_requires_exact_download_and_api_cleanup() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "upload_http_status": 200,
        "upload_code": 0,
        "file_id_present": True,
        "download_http_status": 200,
        "download_bytes_exact": True,
        "no_file_delete_endpoint_recorded": True,
        "agent_cleanup_succeeded": True,
    }
    assert module.agent_file_contract_ok(observed)
    assert not module.agent_file_contract_ok({**observed, "download_bytes_exact": False})


def test_agent_tag_contract_requires_aggregation_and_exact_physical_value() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "list_http_status": 200,
        "list_code": 0,
        "initial_count_exact": True,
        "update_http_status": 200,
        "update_code": 0,
        "database_tags_exact": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_tag_contract_ok(observed)
    assert not module.agent_tag_contract_ok({**observed, "initial_count_exact": False})


def test_bot_credential_contract_separates_beta_and_ordinary_token() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "beta_status": 200,
        "beta_code": 0,
        "ordinary_status": 401,
        "ordinary_code": 401,
        "credential_cleanup_succeeded": True,
        "chat_cleanup_succeeded": True,
    }
    assert module.bot_credential_contract_ok(observed)
    assert not module.bot_credential_contract_ok({**observed, "ordinary_status": 200})


def test_cross_tenant_chat_contract_requires_get_and_delete_rejection() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "get_status": 200,
        "get_code": 109,
        "get_message_exact": True,
        "delete_status": 200,
        "delete_code": 109,
        "delete_message_exact": True,
        "chat_unchanged": True,
        "cleanup_succeeded": True,
    }
    assert module.cross_tenant_chat_contract_ok(observed)
    assert not module.cross_tenant_chat_contract_ok({**observed, "delete_code": 0})


def test_cross_tenant_agent_contract_requires_private_agent_rejection() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "get_status": 200,
        "get_code": 103,
        "agent_unchanged": True,
        "cleanup_succeeded": True,
    }
    assert module.cross_tenant_agent_contract_ok(observed)
    assert not module.cross_tenant_agent_contract_ok({**observed, "get_code": 0})


def test_team_agent_contract_keeps_read_only_security_oracle() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "membership_role": "normal",
        "get_status": 200,
        "get_code": 0,
        "update_status": 200,
        "update_rejected": True,
        "agent_unchanged": True,
        "membership_cleanup_succeeded": True,
        "agent_cleanup_succeeded": True,
        "secondary_cleanup_succeeded": True,
    }
    assert module.team_agent_contract_ok(observed)
    assert not module.team_agent_contract_ok({**observed, "update_rejected": False, "agent_unchanged": False})


def test_db_connection_contract_requires_ssrf_rejection_and_allowed_success() -> None:
    module = load_runner()
    observed = {
        "loopback_status": 200,
        "loopback_failed": True,
        "loopback_message_mentions_unsafe": True,
        "allowed_target_is_global": True,
        "allowed_status": 200,
        "allowed_code": 0,
        "allowed_data_exact": True,
    }
    assert module.db_connection_contract_ok(observed)
    assert not module.db_connection_contract_ok({**observed, "loopback_failed": False})
    assert not module.db_connection_contract_ok({**observed, "allowed_target_is_global": False})


def test_dataflow_template_uses_current_product_pipeline() -> None:
    module = load_runner()
    dsl = module._dataflow_template()
    component_names = {component["obj"]["component_name"] for component in dsl["components"].values()}
    assert {"File", "Parser", "TokenChunker", "Tokenizer"} <= component_names
    assert len(dsl["components"]) == 4


def test_dataflow_rerun_contract_requires_real_task_and_second_terminal() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "initial_terminal": True,
        "initial_progress_success": True,
        "log_id_present": True,
        "dsl_present": True,
        "component_present": True,
        "rerun_status": 200,
        "rerun_code": 0,
        "rerun_task_observed": True,
        "rerun_terminal": True,
        "rerun_progress_success": True,
        "log_count_increased": True,
        "dataset_cleanup_succeeded": True,
        "agent_cleanup_succeeded": True,
    }
    assert module.dataflow_rerun_contract_ok(observed)
    assert not module.dataflow_rerun_contract_ok({**observed, "rerun_task_observed": False})


def test_supplement_chat_delete_contracts_keep_each_physical_oracle() -> None:
    module = load_runner()
    common = {
        "fixture_ready": True,
        "delete_status": 200,
        "delete_code": 0,
        "dialog_count": 1,
        "dialog_status": "0",
        "list_status": 200,
        "list_code": 0,
        "list_absent": True,
        "detail_status": 200,
        "detail_code": 109,
        "detail_message_exact": True,
        "detail_data_absent": True,
        "success_count": 2,
        "target_statuses_exact": True,
        "untargeted_active": True,
        "remaining_active": 0,
        "all_statuses_zero": True,
        "cleanup_succeeded": True,
    }
    for mode in ("single", "list", "detail", "bulk", "all"):
        assert module.supplement_chat_delete_contract_ok(mode, common)
    assert not module.supplement_chat_delete_contract_ok("single", {**common, "dialog_status": "1"})
    assert not module.supplement_chat_delete_contract_ok("detail", {**common, "detail_code": 0})


def test_supplement_patch_contract_requires_exact_merge_and_preservation() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "patch_status": 200,
        "patch_code": 0,
        "api_value_exact": True,
        "database_value_exact": True,
        "unsubmitted_value_preserved": True,
        "name_updated": True,
        "cleanup_succeeded": True,
    }
    assert module.supplement_chat_patch_contract_ok(observed)
    assert not module.supplement_chat_patch_contract_ok({**observed, "unsubmitted_value_preserved": False})


def test_webhook_security_contract_distinguishes_success_and_rejection() -> None:
    module = load_runner()
    common = {
        "fixture_ready": True,
        "http_status": 200,
        "code": None,
        "body_matches": True,
        "message": "",
        "cleanup_succeeded": True,
    }
    assert module.supplement_webhook_security_contract_ok(common, expect_success=True)
    rejected = {
        **common,
        "http_status": 400,
        "code": 400,
        "body_matches": False,
        "message": "Invalid token authentication",
    }
    assert module.supplement_webhook_security_contract_ok(
        rejected,
        expect_success=False,
        message_fragment="Invalid token authentication",
    )
    assert not module.supplement_webhook_security_contract_ok(
        {**rejected, "http_status": 200},
        expect_success=False,
        message_fragment="Invalid token authentication",
    )


def test_webhook_rate_limit_contract_requires_ten_successes_and_five_rejections() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "request_count": 15,
        "first_ten_statuses": [200] * 10,
        "last_five_statuses": [400] * 5,
        "last_five_codes": [400] * 5,
        "last_five_messages_match": True,
        "rate_key_precleaned": True,
        "rate_key_cleanup_succeeded": True,
        "agent_cleanup_succeeded": True,
    }
    assert module.supplement_webhook_rate_contract_ok(observed)
    assert not module.supplement_webhook_rate_contract_ok({**observed, "last_five_statuses": [200] + [400] * 4})


def test_webhook_dsl_preserves_explicit_security_shape() -> None:
    module = load_runner()
    security = {
        "auth_type": "token",
        "allow_anonymous": False,
        "max_body_size": "1MB",
        "token": {
            "token_header": "X-Webhook-Token",
            "token_value": "fixture-value",
        },
    }
    dsl = module._webhook_agent_dsl({"ok": True}, response_status=200, security=security)
    params = dsl["components"]["begin"]["obj"]["params"]
    assert params["security"] == security
    assert params["response"]["status"] == 200
    assert dsl["retrieval"] == []


def test_supplement_agent_completion_contracts_require_persisted_sessions() -> None:
    module = load_runner()
    common = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "session_id_present": True,
        "message_id_present": True,
        "database_session_count": 1,
        "messages_persisted": True,
        "output_nonempty": True,
        "session_id_unchanged": True,
        "session_updated": True,
        "cleanup_succeeded": True,
    }
    assert module.supplement_agent_new_session_contract_ok(common)
    assert module.supplement_agent_continuation_contract_ok(common)
    assert not module.supplement_agent_new_session_contract_ok({**common, "database_session_count": 0})
    assert not module.supplement_agent_continuation_contract_ok({**common, "session_id_unchanged": False})


def test_agent_continuation_preserves_prologue_and_appends_one_turn() -> None:
    module = load_runner()
    baseline = {"message": [{"role": "assistant", "content": "prologue"}]}
    completed = {
        "message": [
            {"role": "assistant", "content": "prologue"},
            {"role": "user", "content": "Follow up question", "id": "turn-1"},
            {"role": "assistant", "content": "answer", "id": "turn-1"},
        ]
    }
    assert module._agent_session_appended_completed_turn(baseline, completed, "Follow up question")
    assert not module._agent_session_appended_completed_turn(
        baseline,
        {**completed, "message": completed["message"][:2]},
        "Follow up question",
    )


def test_supplement_agent_stream_contract_requires_messages_end_and_done() -> None:
    module = load_runner()
    observed = {
        "fixture_ready": True,
        "http_status": 200,
        "content_type_is_sse": True,
        "parse_error_count": 0,
        "done_count": 1,
        "message_event_present": True,
        "message_end_present": True,
        "answer_nonempty": True,
        "error_absent": True,
        "session_id_present": True,
        "database_session_count": 1,
        "messages_persisted": True,
        "cleanup_succeeded": True,
    }
    assert module.supplement_agent_stream_contract_ok(observed)
    assert not module.supplement_agent_stream_contract_ok({**observed, "message_end_present": False})


def test_supplement_dataflow_completion_and_cancel_contracts() -> None:
    module = load_runner()
    queued = {
        "fixture_ready": True,
        "http_status": 200,
        "code": 0,
        "message_id_present": True,
        "session_id_present": True,
        "task_count": 1,
        "task_id_matches": True,
        "task_type": "dataflow",
        "doc_id": "dataflow_x",
        "session_count": 1,
        "agent_cleanup_succeeded": True,
        "session_cleanup_succeeded": True,
    }
    assert module.supplement_dataflow_completion_contract_ok(queued)
    assert not module.supplement_dataflow_completion_contract_ok({**queued, "task_type": ""})
    canceled = {
        **queued,
        "cancel_status": 200,
        "cancel_code": 0,
        "cancel_data_true": True,
        "redis_cancel_flag_present": True,
        "task_progress": -1.0,
    }
    assert module.supplement_task_cancel_contract_ok(canceled)
    assert not module.supplement_task_cancel_contract_ok({**canceled, "redis_cancel_flag_present": False})
