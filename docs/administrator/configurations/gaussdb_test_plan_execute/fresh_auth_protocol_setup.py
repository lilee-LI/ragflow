#!/usr/bin/env python3
from __future__ import annotations

import json
import secrets
import socket
import subprocess
from pathlib import Path

from ruamel.yaml import YAML

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PROTOCOL_DIR = RUNTIME_DIR / "auth_protocol"
CONFIG_PATH = PROTOCOL_DIR / "private_config.json"
HTTP_PORT = 14660
SMTP_PORT = 14650


def _assert_port_free(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", port))


def _write_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> None:
    if CONFIG_PATH.exists():
        raise RuntimeError("fresh auth protocol configuration already exists")
    _assert_port_free(HTTP_PORT)
    _assert_port_free(SMTP_PORT)
    PROTOCOL_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    PROTOCOL_DIR.chmod(0o700)
    cert_path = PROTOCOL_DIR / "localhost.crt"
    key_path = PROTOCOL_DIR / "localhost.key"
    messages_path = PROTOCOL_DIR / "messages.jsonl"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "2",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    key_path.chmod(0o600)
    cert_path.chmod(0o600)
    messages_path.write_text("", encoding="utf-8")
    messages_path.chmod(0o600)

    config = {
        "http_host": "127.0.0.1",
        "http_port": HTTP_PORT,
        "smtp_host": "127.0.0.1",
        "smtp_port": SMTP_PORT,
        "control_token": secrets.token_urlsafe(32),
        "oauth_client_id": "ragflow_test_client",
        "oauth_client_secret": secrets.token_urlsafe(32),
        "smtp_username": "fresh-auth-smtp",
        "smtp_password": secrets.token_urlsafe(32),
        "tls_cert": str(cert_path),
        "tls_key": str(key_path),
        "messages_path": str(messages_path),
    }
    _write_private_json(CONFIG_PATH, config)

    yaml = YAML()
    yaml.preserve_quotes = True
    private_path = RUNTIME_DIR / "private_environments.json"
    environments = json.loads(private_path.read_text(encoding="utf-8"))
    for group in ("control", "experiment"):
        service_path = RUNTIME_DIR / group / "conf" / "service_conf.yaml"
        service = yaml.load(service_path.read_text(encoding="utf-8"))
        api_port = int(service["ragflow"]["http_port"])
        service["oauth"] = {
            "testoauth": {
                "type": "oauth2",
                "display_name": "Test OAuth",
                "icon": "sso",
                "client_id": config["oauth_client_id"],
                "client_secret": config["oauth_client_secret"],
                "authorization_url": f"http://127.0.0.1:{HTTP_PORT}/oauth/authorize",
                "token_url": f"http://127.0.0.1:{HTTP_PORT}/oauth/token",
                "userinfo_url": f"http://127.0.0.1:{HTTP_PORT}/oauth/userinfo",
                "redirect_uri": f"http://127.0.0.1:{api_port}/api/v1/auth/oauth/testoauth/callback",
            }
        }
        service["smtp"] = {
            "mail_server": "localhost",
            "mail_port": SMTP_PORT,
            "mail_use_ssl": True,
            "mail_use_tls": False,
            "mail_username": config["smtp_username"],
            "mail_password": config["smtp_password"],
            "mail_default_sender": ["RAGFlow Fresh Test", "noreply@fresh.invalid"],
            "mail_frontend_url": "http://127.0.0.1/fresh-test",
        }
        with service_path.open("w", encoding="utf-8") as stream:
            yaml.dump(service, stream)
        service_path.chmod(0o600)
        environments[group]["SSL_CERT_FILE"] = str(cert_path)

    _write_private_json(private_path, environments)
    _write_private_json(
        PROTOCOL_DIR / "manifest.redacted.json",
        {
            "batch_id": BATCH_ID,
            "http_endpoint": ["127.0.0.1", HTTP_PORT],
            "smtp_endpoint": ["localhost", SMTP_PORT],
            "oauth_channel": "testoauth",
            "groups_configured": ["control", "experiment"],
            "secrets_redacted": True,
        },
    )
    print(
        json.dumps(
            {
                "status": "configured",
                "http_port": HTTP_PORT,
                "smtp_port": SMTP_PORT,
                "groups": ["control", "experiment"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
