import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_10_error_retry.py")
EXPECTED_IDS = [f"TC-FR-{number:03d}" for number in range(1, 13)]


def load_module():
    name = "fresh_10_error_retry_contract"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_connection_retry_probe_uses_bounded_exponential_backoff():
    module = load_module()

    recovered = module.exercise_connection_retry("experiment", ["08006", "57P03"], max_retries=3)

    assert recovered == {
        "attempts": 3,
        "reconnects": 2,
        "sleeps": [0.1, 0.2],
        "returned": "baseline-result",
        "raised": None,
    }


def test_deadlock_retry_replays_full_operation_without_duplicate_commit():
    module = load_module()

    control = module.exercise_transaction_retry("control", "1213", failures=2)
    experiment = module.exercise_transaction_retry("experiment", "40P01", failures=2)

    for observed in (control, experiment):
        assert observed["attempts"] == 3
        assert observed["sleeps"] == [0.1, 0.2]
        assert observed["committed_values"] == ["committed-once"]
        assert observed["returned"] == "committed-once"


def test_classifier_matrix_distinguishes_idempotent_and_nonretry_errors():
    module = load_module()

    assert module.classifier_observation("control", "duplicate_column")["matched"]
    assert module.classifier_observation("experiment", "duplicate_column")["matched"]
    assert module.classifier_observation("control", "duplicate_object")["matched"]
    assert module.classifier_observation("experiment", "duplicate_object")["matched"]
    assert module.classifier_observation("control", "undefined_object")["matched"]
    assert module.classifier_observation("experiment", "undefined_object")["matched"]
    for label in ("unique", "not_null", "syntax", "type_conversion"):
        observed = module.classifier_observation("experiment", label)
        assert observed["retryable_transaction"] is False
        assert observed["idempotent_ddl"] is False


def test_nonretry_contract_detects_blind_connection_text_retry():
    module = load_module()

    safe = module.evaluate_nonretry_contract({"attempts": 1, "reconnects": 0, "raised": "OperationalError"})
    unsafe = module.evaluate_nonretry_contract({"attempts": 2, "reconnects": 1, "raised": None})

    assert safe is True
    assert unsafe is False


def test_domain_registers_only_error_retry_cases():
    module = load_module()
    titles = {case_id: case_id for case_id in EXPECTED_IDS}

    runners = module.get_runners(titles)

    assert list(runners) == EXPECTED_IDS
    assert all(callable(item) for item in runners.values())
