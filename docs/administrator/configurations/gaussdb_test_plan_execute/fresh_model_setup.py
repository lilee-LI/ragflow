#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from Cryptodome.Cipher import PKCS1_v1_5
from Cryptodome.PublicKey import RSA

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
    evidence_dir,
)

GROUP_ORDER = ("control", "experiment")
CHAT_PROVIDER = "Tongyi-Qianwen"
CHAT_MODEL = "qwen3.7-plus"
CHAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_PROVIDER = "Ollama"
EMBEDDING_MODEL = "qwen3-embedding:0.6b"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
EXECUTE_DIR = Path(__file__).resolve().parent
DEFAULT_RUNTIME = RUNTIME_DIR
DEFAULT_EVIDENCE = evidence_dir("00_environment_setup") / "model_setup.json"
KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")


class ApiCallError(RuntimeError):
    def __init__(self, step: str, summary: dict[str, Any]):
        super().__init__(step)
        self.step = step
        self.summary = summary


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def redact_text(text: Any, secrets_to_remove: list[str]) -> str:
    rendered = str(text)
    for secret in sorted((item for item in secrets_to_remove if item), key=len, reverse=True):
        rendered = rendered.replace(secret, "<redacted>")
    return KEY_PATTERN.sub("<redacted>", rendered)[:500]


def safe_response_summary(response, secrets_to_remove: list[str]) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    data = payload.get("data")
    return {
        "http_status": int(response.status_code),
        "code": payload.get("code"),
        "message": redact_text(payload.get("message", ""), secrets_to_remove),
        "data_kind": type(data).__name__,
    }


def model_info_payloads() -> dict[str, list[dict[str, Any]]]:
    return {
        "chat": [
            {
                "model_name": CHAT_MODEL,
                "model_type": ["chat"],
                "max_tokens": 8192,
            }
        ],
        "embedding": [
            {
                "model_name": EMBEDDING_MODEL,
                "model_type": ["embedding"],
                "max_tokens": 8192,
            }
        ],
    }


def encrypt_password(password: str, public_key_path: Path) -> str:
    key = RSA.import_key(public_key_path.read_text(encoding="utf-8"), "Welcome")
    cipher = PKCS1_v1_5.new(key)
    encoded = base64.b64encode(password.encode("utf-8"))
    return base64.b64encode(cipher.encrypt(encoded)).decode("ascii")


class RAGFlowApi:
    def __init__(
        self,
        base_url: str,
        secrets_to_remove: list[str],
        evidence_steps: list[dict[str, Any]],
    ):
        self.base_url = base_url.rstrip("/")
        self.secrets_to_remove = secrets_to_remove
        self.evidence_steps = evidence_steps
        self.session = requests.Session()

    def request(
        self,
        step: str,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        timeout: float = 180,
    ) -> dict[str, Any]:
        started = time.monotonic()
        response = self.session.request(
            method,
            self.base_url + path,
            json=payload,
            timeout=timeout,
        )
        summary = safe_response_summary(response, self.secrets_to_remove)
        summary.update({"step": step, "elapsed_ms": round((time.monotonic() - started) * 1000)})
        self.evidence_steps.append(summary)
        try:
            body = response.json()
        except Exception:
            body = {}
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("code") != 0:
            raise ApiCallError(step, summary)
        return body

    def login(self, email: str, password: str, public_key_path: Path) -> str:
        response = self.session.post(
            self.base_url + "/api/v1/auth/login",
            json={
                "email": email,
                "password": encrypt_password(password, public_key_path),
            },
            timeout=30,
        )
        summary = safe_response_summary(response, self.secrets_to_remove)
        summary.update({"step": "login"})
        self.evidence_steps.append(summary)
        try:
            body = response.json()
        except Exception:
            body = {}
        token = response.headers.get("Authorization", "")
        if response.status_code >= 400 or body.get("code") != 0 or not token:
            raise ApiCallError("login", summary)
        self.secrets_to_remove.append(token)
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        return token


def _instance_name(group: str, kind: str) -> str:
    return f"fr-{BATCH_ID}-{group}-{kind}"


def _assert_instance_absent(api: RAGFlowApi, provider: str, instance: str) -> None:
    body = api.request(
        f"list_{provider}_instances_before_create",
        "GET",
        f"/api/v1/providers/{quote(provider, safe='')}/instances",
    )
    instances = body.get("data") or []
    if any(item.get("instance_name") == instance for item in instances if isinstance(item, dict)):
        raise RuntimeError(f"target instance unexpectedly exists: {provider}/{instance}")


def _add_provider(api: RAGFlowApi, provider: str) -> None:
    api.request(
        f"add_provider_{provider}",
        "PUT",
        "/api/v1/providers",
        payload={"provider_name": provider},
    )


def _create_instance(
    api: RAGFlowApi,
    provider: str,
    instance: str,
    api_key: str,
    base_url: str,
    model_info: list[dict[str, Any]],
) -> None:
    _assert_instance_absent(api, provider, instance)
    api.request(
        f"create_instance_{provider}",
        "POST",
        f"/api/v1/providers/{quote(provider, safe='')}/instances",
        payload={
            "instance_name": instance,
            "api_key": api_key,
            "base_url": base_url,
            "region": "",
            "model_info": model_info,
        },
    )


def _set_default(
    api: RAGFlowApi,
    provider: str,
    instance: str,
    model_name: str,
    model_type: str,
) -> None:
    api.request(
        f"set_default_{model_type}",
        "PATCH",
        "/api/v1/models/default",
        payload={
            "model_provider": provider,
            "model_instance": instance,
            "model_name": model_name,
            "model_type": model_type,
        },
    )


def _verify_defaults(api: RAGFlowApi, chat_instance: str, embedding_instance: str) -> bool:
    body = api.request("get_default_models", "GET", "/api/v1/models/default")
    data = body.get("data") or {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return False
    actual = {
        item.get("model_type"): (
            item.get("model_provider"),
            item.get("model_instance"),
            item.get("model_name"),
            item.get("enable"),
        )
        for item in models
        if isinstance(item, dict)
    }
    return actual.get("chat") == (
        CHAT_PROVIDER,
        chat_instance,
        CHAT_MODEL,
        True,
    ) and actual.get("embedding") == (
        EMBEDDING_PROVIDER,
        embedding_instance,
        EMBEDDING_MODEL,
        True,
    )


def _probe_chat(api: RAGFlowApi, instance: str) -> dict[str, Any]:
    started = time.monotonic()
    body = api.request(
        "probe_chat_model",
        "POST",
        (f"/api/v1/providers/{quote(CHAT_PROVIDER, safe='')}/instances/{quote(instance, safe='')}/models/{quote(CHAT_MODEL, safe='')}"),
        payload={"message": "Reply with exactly OK.", "stream": False, "thinking": False},
        timeout=180,
    )
    data = body.get("data")
    return {
        "ok": data is not None and bool(str(data).strip()),
        "nonempty": data is not None and bool(str(data).strip()),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def _probe_embedding() -> dict[str, Any]:
    started = time.monotonic()
    response = requests.post(
        OLLAMA_BASE_URL + "/api/embed",
        json={"model": EMBEDDING_MODEL, "input": ["fresh GaussDB test probe"]},
        timeout=180,
    )
    response.raise_for_status()
    body = response.json()
    embeddings = body.get("embeddings") if isinstance(body, dict) else None
    if not isinstance(embeddings, list) or len(embeddings) != 1:
        raise RuntimeError("invalid embedding response shape")
    vector = embeddings[0]
    if not isinstance(vector, list) or not vector:
        raise RuntimeError("empty embedding vector")
    return {
        "ok": True,
        "dimension": len(vector),
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


def configure_group(
    group: str,
    runtime: Path,
    environment: dict[str, str],
    qwen_key: str,
) -> dict[str, Any]:
    port = 9380 if group == "control" else 9480
    secrets_to_remove = [
        qwen_key,
        environment["DEFAULT_SUPERUSER_EMAIL"],
        environment["DEFAULT_SUPERUSER_PASSWORD"],
        environment["RAGFLOW_SECRET_KEY"],
    ]
    steps: list[dict[str, Any]] = []
    api = RAGFlowApi(f"http://127.0.0.1:{port}", secrets_to_remove, steps)
    token = api.login(
        environment["DEFAULT_SUPERUSER_EMAIL"],
        environment["DEFAULT_SUPERUSER_PASSWORD"],
        runtime / group / "conf" / "public.pem",
    )
    payloads = model_info_payloads()
    chat_instance = _instance_name(group, "qwen")
    embedding_instance = _instance_name(group, "ollama")

    _add_provider(api, CHAT_PROVIDER)
    _create_instance(
        api,
        CHAT_PROVIDER,
        chat_instance,
        qwen_key,
        CHAT_BASE_URL,
        payloads["chat"],
    )
    _set_default(api, CHAT_PROVIDER, chat_instance, CHAT_MODEL, "chat")
    chat_probe = _probe_chat(api, chat_instance)

    _add_provider(api, EMBEDDING_PROVIDER)
    _create_instance(
        api,
        EMBEDDING_PROVIDER,
        embedding_instance,
        "x",
        OLLAMA_BASE_URL,
        payloads["embedding"],
    )
    _set_default(
        api,
        EMBEDDING_PROVIDER,
        embedding_instance,
        EMBEDDING_MODEL,
        "embedding",
    )
    embedding_probe = _probe_embedding()
    defaults_verified = _verify_defaults(api, chat_instance, embedding_instance)
    return {
        "group": group,
        "ok": bool(chat_probe["ok"] and embedding_probe["ok"] and defaults_verified),
        "auth_token_fingerprint": fingerprint(token),
        "qwen_key_fingerprint": fingerprint(qwen_key),
        "models": {
            "chat": {"provider": CHAT_PROVIDER, "model": CHAT_MODEL},
            "embedding": {
                "provider": EMBEDDING_PROVIDER,
                "model": EMBEDDING_MODEL,
            },
        },
        "probes": {"chat": chat_probe, "embedding": embedding_probe},
        "defaults_verified": defaults_verified,
        "api_steps": steps,
    }


def run_setup(runtime: Path, qwen_key: str) -> dict[str, Any]:
    private_environment = json.loads((runtime / "private_environments.json").read_text(encoding="utf-8"))
    results = []
    for group in GROUP_ORDER:
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            result = configure_group(group, runtime, private_environment[group], qwen_key)
        except ApiCallError as exc:
            result = {
                "group": group,
                "ok": False,
                "failed_step": exc.step,
                "error": exc.summary,
                "qwen_key_fingerprint": fingerprint(qwen_key),
            }
        except Exception as exc:
            result = {
                "group": group,
                "ok": False,
                "failed_step": "unexpected",
                "error_type": type(exc).__name__,
                "error": redact_text(str(exc), [qwen_key]),
                "qwen_key_fingerprint": fingerprint(qwen_key),
            }
        result["started_at"] = started_at
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        results.append(result)
    return {
        "batch_id": BATCH_ID,
        "group_order": list(GROUP_ORDER),
        "results": results,
    }


def write_evidence(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure fresh real models via API")
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--qwen-key-stdin", action="store_true", required=True)
    args = parser.parse_args()
    qwen_key = sys.stdin.readline().rstrip("\r\n")
    if not qwen_key:
        parser.error("a non-empty key must be supplied on stdin")
    result = run_setup(args.runtime, qwen_key)
    write_evidence(args.evidence, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if all(item.get("ok") for item in result["results"]) else 1)


if __name__ == "__main__":
    main()
