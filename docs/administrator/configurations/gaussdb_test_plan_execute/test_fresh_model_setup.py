import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_model_setup.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_model_setup", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_redact_text_removes_supplied_and_key_shaped_secrets():
    module = load_module()
    rendered = module.redact_text(
        "password=private-value token=sk-exampleSecret123456789",
        secrets_to_remove=["private-value"],
    )

    assert "private-value" not in rendered
    assert "sk-exampleSecret123456789" not in rendered
    assert rendered.count("<redacted>") == 2


def test_model_info_payloads_use_real_requested_models():
    module = load_module()

    assert module.model_info_payloads() == {
        "chat": [
            {
                "model_name": "qwen3.7-plus",
                "model_type": ["chat"],
                "max_tokens": 8192,
            }
        ],
        "embedding": [
            {
                "model_name": "qwen3-embedding:0.6b",
                "model_type": ["embedding"],
                "max_tokens": 8192,
            }
        ],
    }


def test_control_group_is_always_processed_before_experiment():
    module = load_module()

    assert module.GROUP_ORDER == ("control", "experiment")


def test_safe_response_summary_does_not_copy_headers_or_arbitrary_data():
    module = load_module()

    class Response:
        status_code = 400
        headers = {"Authorization": "must-not-leak"}

        @staticmethod
        def json():
            return {
                "code": 100,
                "message": "bad sk-exampleSecret123456789",
                "data": {"api_key": "must-not-leak"},
            }

    summary = module.safe_response_summary(Response(), [])

    assert summary == {
        "http_status": 400,
        "code": 100,
        "message": "bad <redacted>",
        "data_kind": "dict",
    }
    assert "must-not-leak" not in str(summary)
