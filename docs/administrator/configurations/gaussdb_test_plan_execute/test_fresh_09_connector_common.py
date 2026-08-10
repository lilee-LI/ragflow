import importlib.util
import json
from pathlib import Path


EXECUTE_DIR = Path(__file__).resolve().parent
COMMON_PATH = EXECUTE_DIR / "fresh_09_connector_common.py"
RUNNER_PATH = EXECUTE_DIR / "fresh_09_connector.py"


def load(path: Path, name: str):
    assert path.exists(), f"required fresh module is missing: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_expected_pair_status_prioritizes_fail_then_blocked():
    module = load(COMMON_PATH, "fresh_09_common_status")

    assert module.expected_pair_status(["PASS", "PASS"]) == "PASS"
    assert module.expected_pair_status(["PASS", "BLOCKED"]) == "BLOCKED"
    assert module.expected_pair_status(["FAIL", "BLOCKED"]) == "FAIL"


def test_deep_redact_preserves_structure_without_plaintext_secrets():
    module = load(COMMON_PATH, "fresh_09_common_redact")
    payload = {
        "name": "connector-a",
        "config": {
            "credentials": {
                "api_key": "stub-key-material",
                "client_secret": "oauth-secret-material",
            },
            "url": "https://public.invalid/api",
        },
        "headers": {"Authorization": "Bearer private-token-material"},
        "token": "ragflow-private-token-material",
        "nested": [{"password": "password-material"}],
    }

    redacted = module.deep_redact(payload)
    serialized = json.dumps(redacted, sort_keys=True)

    assert redacted["name"] == "connector-a"
    assert redacted["config"]["url"] == "https://public.invalid/api"
    for secret in (
        "stub-key-material",
        "oauth-secret-material",
        "private-token-material",
        "password-material",
    ):
        assert secret not in serialized
    leaves = [
        redacted["config"]["credentials"]["api_key"],
        redacted["headers"]["Authorization"],
        redacted["token"],
        redacted["nested"][0]["password"],
    ]
    assert all(item["redacted"] is True and len(item["fingerprint"]) == 12 for item in leaves)


def test_redact_known_values_replaces_text_and_reports_match_count():
    module = load(COMMON_PATH, "fresh_09_common_text_redact")
    text = "prefix secret-one middle secret-two suffix secret-one"

    result = module.redact_known_values(text, {"secret-one", "secret-two"})

    assert result["text"] == "prefix <redacted> middle <redacted> suffix <redacted>"
    assert result["match_count"] == 3
    assert "secret-one" not in json.dumps(result)


def test_deep_redact_hides_scalar_serialized_credentials():
    module = load(COMMON_PATH, "fresh_09_common_scalar_credentials")
    payload = {"data": {"credentials": '{"access_token":"oauth-access-material"}'}}

    redacted = module.deep_redact(payload)

    assert redacted["data"]["credentials"]["redacted"] is True
    assert "oauth-access-material" not in json.dumps(redacted)


def test_redact_known_values_in_nested_json_preserves_shape_and_counts_matches():
    module = load(COMMON_PATH, "fresh_09_common_nested_known_values")
    payload = {
        "message": "dsn password-material",
        "nested": ["password-material", {"detail": "safe"}],
    }

    result = module.redact_known_values_in_value(payload, {"password-material"})

    assert result["value"] == {
        "message": "dsn <redacted>",
        "nested": ["<redacted>", {"detail": "safe"}],
    }
    assert result["match_count"] == 2


def test_private_json_writer_enforces_0600_files_and_0700_directories(tmp_path):
    module = load(COMMON_PATH, "fresh_09_common_writer")
    path = tmp_path / "private" / "raw" / "one.json"

    module.write_private_json(path, {"value": 1})

    assert json.loads(path.read_text()) == {"value": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.parent.parent.stat().st_mode & 0o777 == 0o700


def test_build_case_record_requires_control_then_experiment(tmp_path):
    module = load(COMMON_PATH, "fresh_09_common_pair")
    calls = []

    def execute(group):
        calls.append(group)
        return module.case_group_result(
            "PASS",
            [{"name": "exercise", "group": group}],
            {"expected": True},
        )

    path = tmp_path / "TC-CONN-015.json"
    record = module.run_paired_case("TC-CONN-015", "title", execute, evidence_path=path)

    assert calls == ["control", "experiment"]
    assert record["group_order"] == ["control", "experiment"]
    assert [item["group"] for item in record["groups"]] == calls
    assert record["pair_status"] == "PASS"
    assert path.stat().st_mode & 0o777 == 0o600


def test_http_record_builder_redacts_request_and_json_response_secrets():
    module = load(COMMON_PATH, "fresh_09_common_http_record")
    record = module.build_http_record(
        {"method": "POST", "path": "/x", "Authorization": "Bearer owner-secret"},
        200,
        {"code": 0, "data": {"token": "ragflow-response-secret"}},
        content_type="application/json",
        response_bytes=b"ignored-json-body",
    )

    serialized = json.dumps(record, sort_keys=True)
    assert "owner-secret" not in serialized
    assert "response-secret" not in serialized
    assert record["response"]["body"]["code"] == 0
    assert record["response"]["body"]["data"]["token"]["redacted"] is True


def test_current_plan_and_runner_are_exactly_six_adaptation_cases():
    module = load(RUNNER_PATH, "fresh_09_runner_registry")

    planned = module._plan_case_ids()
    runner_ids = list(module.RUNNERS)
    expected = [
        "TC-CONN-015",
        "TC-CONN-016",
        "TC-CONN-031",
        "TC-CONN-032",
        "TC-CONN-093",
        "TC-CONN-094",
    ]

    assert planned == runner_ids == list(module.CASE_TITLES) == expected
    assert len(planned) == len(set(planned)) == 6
    assert all(runner.__name__ != "_unimplemented" for runner in module.RUNNERS.values())


def test_case_family_routes_only_retained_adaptation_domains():
    module = load(RUNNER_PATH, "fresh_09_runner_family")

    assert module.case_family("TC-CONN-015") == "scheduling"
    assert module.case_family("TC-CONN-031") == "system_health"
    assert module.case_family("TC-CONN-093") == "gaussdb_compatibility"


def test_forbidden_markers_are_assembled_without_self_matching():
    module = load(COMMON_PATH, "fresh_09_common_forbidden")

    source = COMMON_PATH.read_text()
    assert all(marker not in source for marker in module.FORBIDDEN_MARKERS)
