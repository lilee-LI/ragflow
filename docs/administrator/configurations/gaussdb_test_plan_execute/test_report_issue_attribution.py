import hashlib
import json
import re
import stat
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute import (
    fresh_generate_reports as reports,
)


EXECUTE_DIR = Path(__file__).resolve().parent
RUN_ID = "20260727_combined_001"
EXPECTED_SIGNATURES = {
    "BLOCKED/BLOCKED": 4,
    "BLOCKED/PASS": 1,
    "FAIL/FAIL": 95,
    "FAIL/PASS": 16,
    "PASS/FAIL": 10,
    "PASS/PASS": 517,
}
EXPECTED_ATTRIBUTIONS = {
    "non_pass_cases": 126,
    "实验组独有": 10,
    "对照组也存在": 95,
    "对照组独有": 16,
    "两组均阻塞": 4,
    "对照环境阻塞/实验组通过": 1,
}


@pytest.fixture(scope="module")
def run() -> reports.RunResult:
    return reports.load_run(RUN_ID)


def _report_attribution_rows(text: str) -> list[dict[str, str]]:
    section = text.split("<!-- ISSUE_ATTRIBUTION_START -->", 1)[1].split("<!-- ISSUE_ATTRIBUTION_END -->", 1)[0]
    pattern = re.compile(
        r"^\|\s*(TC-[A-Z0-9-]+)\s*"
        r"\|\s*(PASS|FAIL|BLOCKED)\s*"
        r"\|\s*(PASS|FAIL|BLOCKED)\s*"
        r"\|\s*([^|]+?)\s*\|$",
        re.MULTILINE,
    )
    return [
        {
            "case_id": case_id,
            "control": control,
            "experiment": experiment,
            "attribution": attribution.strip(),
        }
        for case_id, control, experiment, attribution in pattern.findall(section)
    ]


def test_current_formal_run_is_complete_and_operational(
    run: reports.RunResult,
) -> None:
    assert run.run_id == RUN_ID
    assert run.formal_status["state"] == "complete"
    assert run.formal_status["stop_reason"] is None
    assert run.formal_status["completed_count"] == 643
    assert run.formal_status["group_signature_counts"] == EXPECTED_SIGNATURES
    assert len(run.cases) == 643
    assert len({case.planned.case_id for case in run.cases}) == 643
    assert run.environment["operational_ready"] is True
    assert run.environment["processes"]["all_alive"] is True
    assert run.environment["processes"]["service_count"] == 8
    assert run.environment["security_findings"]["has_unredacted_secret"] is False


@pytest.mark.parametrize("section_index", range(len(reports.REPORT_SPECS)))
def test_report_exactly_matches_current_evidence(run: reports.RunResult, section_index: int) -> None:
    section = run.sections[section_index]
    path = EXECUTE_DIR / section.spec.report_name
    text = path.read_text(encoding="utf-8")
    assert text == reports.render_report(run, section)
    assert text.count("<!-- ISSUE_ATTRIBUTION_START -->") == 1
    assert text.count("<!-- ISSUE_ATTRIBUTION_END -->") == 1

    expected = [{key: row[key] for key in ("case_id", "control", "experiment", "attribution")} for row in reports._attribution_rows(section.cases)]
    assert _report_attribution_rows(text) == expected


def test_all_generated_artifacts_are_deterministic_current_run_outputs(
    run: reports.RunResult,
) -> None:
    outputs = reports.generated_outputs(run)
    assert len(outputs) == 17
    for path, expected in outputs.items():
        assert path.is_file(), path
        assert path.read_text(encoding="utf-8") == expected, path


def test_exact_report_set() -> None:
    expected = {
        "00_environment_setup_report.md",
        *(spec.report_name for spec in reports.REPORT_SPECS),
    }
    actual = {path.name for path in EXECUTE_DIR.glob("*report.md")}
    assert actual == expected


def test_machine_readable_attribution_matches_current_evidence(
    run: reports.RunResult,
) -> None:
    path = EXECUTE_DIR / "report_issue_attribution.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == reports._matrix(run)
    assert payload["schema_version"] == 1
    assert payload["batch_id"] == RUN_ID
    assert payload["totals"] == EXPECTED_ATTRIBUTIONS
    for rows in payload["reports"].values():
        for row in rows:
            assert row["evidence"].startswith(f"runs/{RUN_ID}/evidence_private/")


def test_final_coverage_audit_matches_current_formal_status(
    run: reports.RunResult,
) -> None:
    path = run.evidence_root / "final_coverage_audit.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == reports._coverage_payload(run)
    assert payload["planned_case_count"] == 643
    assert payload["evidence_case_count"] == 643
    assert payload["group_record_count"] == 1286
    assert payload["missing_case_ids"] == []
    assert payload["extra_case_ids"] == []
    assert payload["duplicate_plan_case_ids"] == []
    assert payload["group_signature_counts"] == EXPECTED_SIGNATURES
    assert payload["experiment_only_failure_count"] == 10
    assert payload["coverage_pass"] is True
    assert payload["adaptation_regression_pass"] is False


def test_reports_explain_attribution_and_do_not_reference_prior_evidence() -> None:
    current_prefix = f"runs/{RUN_ID}/evidence_private/"
    for path in sorted(EXECUTE_DIR.glob("*report.md")):
        text = path.read_text(encoding="utf-8")
        assert "问题归属判定" in text
        assert "实验组独有" in text
        assert "对照组也存在" in text
        assert "对照组独有" in text
        assert "20260710_fresh_001" not in text
        assert "20260717_rerun_00" not in text.replace(RUN_ID, "")
        assert "evidence_private/" not in text.replace(current_prefix, "")


def test_public_reports_contain_no_token_shaped_secret() -> None:
    token_pattern = re.compile(r"(?i)(?:Bearer\s+|\b(?:sk-|ragflow-))[A-Za-z0-9._~+/=-]{8,}")
    public_paths = [
        *sorted(EXECUTE_DIR.glob("*report.md")),
        EXECUTE_DIR / "FINAL_COVERAGE_AUDIT.md",
        EXECUTE_DIR / "PLAN_COVERAGE.md",
        EXECUTE_DIR / "PROGRESS.md",
    ]
    for path in public_paths:
        assert token_pattern.search(path.read_text(encoding="utf-8")) is None, path


def test_environment_report_uses_final_read_only_validation(
    run: reports.RunResult,
) -> None:
    path = EXECUTE_DIR / "00_environment_setup_report.md"
    text = path.read_text(encoding="utf-8")
    assert text == reports.render_environment_report(run)
    assert f"执行批次：`{RUN_ID}`" in text
    assert "环境就绪：**PASS**" in text
    assert "8/8 存活" in text
    assert "server=UTF8，client=UTF8" in text
    assert "本批次最终环境验收没有记录需要归属的环境失败" in text


def test_startup_structured_raw_evidence_is_redacted_and_hash_linked(
    run: reports.RunResult,
) -> None:
    section = run.evidence_root / "01_startup_migration"
    manifest_path = section / "raw" / "structured_secret_redaction_manifest.json"
    structured_artifacts = [
        ("TC-SM-020", "control", "raw/TC-SM-020_control_config.json"),
        ("TC-SM-020", "experiment", "raw/TC-SM-020_experiment_config.json"),
        ("TC-SM-021", "control", "raw/TC-SM-021_control_template.yaml"),
        ("TC-SM-021", "experiment", "raw/TC-SM-021_experiment_template.yaml"),
    ]
    manifest = None
    manifest_sha = None
    if manifest_path.is_file():
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        assert manifest["batch_id"] == RUN_ID
        assert manifest["case_status_unchanged"] is True
        assert manifest["plaintext_copy_retained"] is False
        assert len(manifest["artifacts"]) == 4
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

    for case_id, group_name, relative_path in structured_artifacts:
        path = section / relative_path
        content = path.read_bytes()
        assert b"<redacted>" in content

        case = json.loads((section / f"{case_id}.json").read_text())
        group = next(item for item in case["groups"] if item["group"] == group_name)
        recorded_hash = group["steps"][1]["raw_sha256"]["config_stdout"] if case_id == "TC-SM-020" else group["steps"][0]["stdout_sha256"]
        actual_hash = hashlib.sha256(content).hexdigest()
        assert recorded_hash == actual_hash

        if manifest is not None:
            artifact = next(item for item in manifest["artifacts"] if item["path"] == relative_path)
            assert actual_hash == artifact["post_redaction_sha256"]
            assert artifact["pre_redaction_sha256"] != actual_hash
            assert artifact["redacted_marker_count"] > 0
            assert case["evidence_postprocessing"] == {
                "case_status_unchanged": True,
                "manifest": "raw/structured_secret_redaction_manifest.json",
                "manifest_sha256": manifest_sha,
                "plaintext_copy_retained": False,
                "type": "structured_secret_redaction",
            }
        else:
            assert "evidence_postprocessing" not in case

        if path.suffix == ".json":
            parsed = json.loads(content)
        else:
            parsed = list(YAML(typ="safe", pure=True).load_all(content.decode()))
        sensitive_values = []

        def visit(value: object) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    normalized = str(key).upper()
                    sensitive = normalized in {
                        "AUTHORIZATION",
                        "PASSWORD",
                        "SECRET",
                        "TOKEN",
                        "API_KEY",
                        "ACCESS_KEY",
                        "SECRET_KEY",
                        "CLIENT_SECRET",
                        "PRIVATE_KEY",
                        "COOKIE",
                    } or normalized.endswith(
                        (
                            "_PASSWORD",
                            "_SECRET",
                            "_TOKEN",
                            "_API_KEY",
                            "_ACCESS_KEY",
                            "_SECRET_KEY",
                            "_CLIENT_SECRET",
                            "_PRIVATE_KEY",
                            "_COOKIE",
                        )
                    )
                    if sensitive and child not in (None, "", "<redacted>"):
                        sensitive_values.append(True)
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(parsed)
        assert sensitive_values == []


def test_entire_current_run_and_public_reports_have_no_runtime_secret(
    run: reports.RunResult,
) -> None:
    runtime = EXECUTE_DIR / "runtime" / RUN_ID
    exact_keys = {
        "password",
        "secret",
        "token",
        "api_key",
        "access_key",
        "secret_key",
        "client_secret",
        "private_key",
        "authorization",
        "cookie",
    }
    suffixes = tuple(f"_{value}" for value in exact_keys)
    known_values: set[str] = set()

    def collect(value: object, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                collect(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                collect(child, key)
        elif isinstance(value, str) and len(value) >= 8 and (key in exact_keys or key.endswith(suffixes)) and value.lower() not in {"<redacted>", "none", "null", "changeme"}:
            known_values.add(value)

    for path in (
        runtime / "private_environments.json",
        runtime / "private_proxy_config.json",
        runtime / "auth_protocol" / "private_config.json",
    ):
        if path.is_file():
            collect(json.loads(path.read_text(encoding="utf-8")))
    yaml = YAML(typ="safe", pure=True)
    for path in (
        runtime / "control" / "conf" / "service_conf.yaml",
        runtime / "experiment" / "conf" / "service_conf.yaml",
    ):
        if path.is_file():
            collect(yaml.load(path.read_text(encoding="utf-8")))

    private_files = [path for path in run.evidence_root.rglob("*") if path.is_file()]
    public_files = [
        *sorted(EXECUTE_DIR.glob("*report.md")),
        EXECUTE_DIR / "FINAL_COVERAGE_AUDIT.md",
        EXECUTE_DIR / "PLAN_COVERAGE.md",
        EXECUTE_DIR / "PROGRESS.md",
        EXECUTE_DIR / "report_issue_attribution.json",
    ]
    encoded_values = [value.encode("utf-8") for value in known_values]
    token_pattern = re.compile(rb"(?i)(?:Bearer\s+|\bsk-)[A-Za-z0-9._~+/=-]{8,}")
    prior_run_pattern = re.compile(rb"202607(?:10_fresh_001|17_rerun_00[1-4])")
    excluded_source_markers = (
        b"gaussdb_test_plan_execute_bak",
        b"mysql_control_test",
        b"/gaussdb_test/",
    )
    violations: list[str] = []
    for path in (*private_files, *public_files):
        content = path.read_bytes()
        if any(value in content for value in encoded_values) or token_pattern.search(content) or prior_run_pattern.search(content) or any(marker in content for marker in excluded_source_markers):
            violations.append(str(path.relative_to(EXECUTE_DIR)))
    assert violations == []

    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in private_files)
    private_dirs = [
        run.evidence_root,
        *[path for path in run.evidence_root.rglob("*") if path.is_dir()],
    ]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in private_dirs)
    assert not any(path.is_symlink() for path in run.evidence_root.rglob("*"))
