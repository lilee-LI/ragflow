import importlib.util
import stat
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_case_evidence.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_case_evidence", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_recorder_enforces_control_before_experiment():
    module = load_module()
    recorder = module.CaseRecorder("TC-SM-999", "order test")

    with pytest.raises(ValueError, match="control group must be recorded first"):
        recorder.add_group("experiment", "PASS", [])

    recorder.add_group("control", "PASS", [])
    recorder.add_group("experiment", "PASS", [])
    assert recorder.finalize()["pair_status"] == "PASS"


def test_recorder_accepts_multi_segment_supplement_case_ids():
    module = load_module()

    recorder = module.CaseRecorder("TC-AT-SOFTDEL-001", "supplement case")

    assert recorder.case_id == "TC-AT-SOFTDEL-001"


def test_recorder_redacts_secret_fields_and_key_shaped_values():
    module = load_module()
    recorder = module.CaseRecorder("TC-SM-999", "redaction test")
    recorder.add_group(
        "control",
        "PASS",
        [
            {
                "name": "request",
                "password": "must-not-leak",
                "nested": {
                    "api_key": "sk-exampleSecret123456789",
                    "beta": "must-not-leak-beta-token",
                },
            }
        ],
    )
    recorder.add_group("experiment", "PASS", [])

    result = recorder.finalize()
    rendered = str(result)

    assert "must-not-leak" not in rendered
    assert "must-not-leak-beta-token" not in rendered
    assert "sk-exampleSecret123456789" not in rendered
    assert "fingerprint" in rendered


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (("PASS", "PASS"), "PASS"),
        (("PASS", "FAIL"), "FAIL"),
        (("PASS", "BLOCKED"), "BLOCKED"),
    ],
)
def test_pair_status_is_derived_from_group_results(statuses, expected):
    module = load_module()
    recorder = module.CaseRecorder("TC-SM-999", "status test")
    recorder.add_group("control", statuses[0], [])
    recorder.add_group("experiment", statuses[1], [])

    assert recorder.finalize()["pair_status"] == expected


def test_write_evidence_uses_private_permissions(tmp_path):
    module = load_module()
    recorder = module.CaseRecorder("TC-SM-999", "write test")
    recorder.add_group("control", "PASS", [])
    recorder.add_group("experiment", "PASS", [])
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir(mode=0o775)
    evidence_dir.chmod(0o775)
    path = evidence_dir / "evidence.json"

    module.write_evidence(path, recorder.finalize())

    assert stat.S_IMODE(evidence_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
