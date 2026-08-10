import importlib.util
import json
import stat
from email.message import EmailMessage
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_auth_protocol_stub.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_auth_protocol_stub", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_oauth_codes_are_one_time_and_tokens_resolve_the_profile():
    module = load_module()
    store = module.OAuthStore()
    profile = {"email": "oauth@fresh.invalid", "nickname": "OAuth User"}

    code = store.issue_code(profile)
    token = store.exchange_code(code)

    assert token is not None
    assert store.exchange_code(code) is None
    assert store.profile_for_token(token) == profile


def test_extract_message_text_decodes_plain_mime_body():
    module = load_module()
    message = EmailMessage()
    message["Subject"] = "OTP"
    message.set_content("Your code is ABCD")

    assert module.extract_message_text(message.as_bytes()) == "Your code is ABCD\n"


def test_validate_config_rejects_non_loopback_control_listener():
    module = load_module()
    config = {
        "http_host": "0.0.0.0",
        "http_port": 14660,
        "smtp_host": "127.0.0.1",
        "smtp_port": 14650,
        "control_token": "x" * 32,
        "oauth_client_id": "client",
        "oauth_client_secret": "s" * 32,
        "smtp_username": "user",
        "smtp_password": "p" * 32,
        "tls_cert": "/tmp/cert",
        "tls_key": "/tmp/key",
        "messages_path": "/tmp/messages",
    }

    with pytest.raises(ValueError, match="loopback"):
        module.validate_config(config)


def test_append_message_record_writes_private_jsonl(tmp_path):
    module = load_module()
    path = tmp_path / "private" / "messages.jsonl"
    item = {"subject": "OTP", "body": "safe body"}

    module.append_message_record(path, item)

    assert json.loads(path.read_text(encoding="utf-8")) == item
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
