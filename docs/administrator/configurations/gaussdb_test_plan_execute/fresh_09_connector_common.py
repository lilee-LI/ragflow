#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    evidence_dir,
)

GROUP_ORDER = ("control", "experiment")
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
EVIDENCE_DIR = evidence_dir("09_connector")
RAW_DIR = EVIDENCE_DIR / "raw"
API_BASES = {
    "control": "http://127.0.0.1:9380/api/v1",
    "experiment": "http://127.0.0.1:9480/api/v1",
}
LEGACY_LLM_BASES = {
    "control": "http://127.0.0.1:9380/v1/llm",
    "experiment": "http://127.0.0.1:9480/v1/llm",
}
ADMIN_BASES = {
    "control": "http://127.0.0.1:9381/api/v1",
    "experiment": "http://127.0.0.1:9481/api/v1",
}
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}
FORBIDDEN_MARKERS = (
    "gaussdb_test_plan_execute" + "_bak",
    "gaussdb" + "_test/",
    "mysql_control" + "_test/",
)
EXACT_SENSITIVE_KEYS = {
    "api_key",
    "api-key",
    "authorization",
    "authorization_token",
    "beta",
    "client_secret",
    "cookie",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
    "access_token",
}
SECRET_PATTERNS = (
    re.compile(r"^Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"^ragflow-[A-Za-z0-9._-]{8,}$"),
    re.compile(r"^sk-[A-Za-z0-9._-]{8,}$"),
)


class CaseBlocked(RuntimeError):
    """Raised only for a genuine environment or dependency blocker."""


def expected_pair_status(statuses: list[str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PASS"


def _fingerprint(value: Any) -> str:
    if isinstance(value, bytes):
        material = value
    elif isinstance(value, str):
        material = value.encode("utf-8")
    else:
        material = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


def _redacted(value: Any) -> dict[str, Any]:
    return {"redacted": True, "fingerprint": _fingerprint(value)}


def _looks_like_secret(value: Any) -> bool:
    return isinstance(value, str) and any(pattern.match(value) for pattern in SECRET_PATTERNS)


def deep_redact(value: Any, *, _key: str = "") -> Any:
    key = _key.lower()
    if key in EXACT_SENSITIVE_KEYS and not isinstance(value, (dict, list)):
        return _redacted(value)
    if isinstance(value, dict):
        return {str(item_key): deep_redact(item_value, _key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [deep_redact(item, _key=_key) for item in value]
    if isinstance(value, tuple):
        return [deep_redact(item, _key=_key) for item in value]
    if _looks_like_secret(value):
        return _redacted(value)
    return value


def redact_known_values(text: str, values: set[str]) -> dict[str, Any]:
    match_count = 0
    result = text
    for value in sorted((item for item in values if item), key=len, reverse=True):
        count = result.count(value)
        if count:
            result = result.replace(value, "<redacted>")
            match_count += count
    return {"text": result, "match_count": match_count}


def redact_known_values_in_value(value: Any, values: set[str]) -> dict[str, Any]:
    if isinstance(value, str):
        result = redact_known_values(value, values)
        return {"value": result["text"], "match_count": result["match_count"]}
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        match_count = 0
        for key, item in value.items():
            nested = redact_known_values_in_value(item, values)
            result[str(key)] = nested["value"]
            match_count += int(nested["match_count"])
        return {"value": result, "match_count": match_count}
    if isinstance(value, (list, tuple)):
        result_list: list[Any] = []
        match_count = 0
        for item in value:
            nested = redact_known_values_in_value(item, values)
            result_list.append(nested["value"])
            match_count += int(nested["match_count"])
        return {"value": result_list, "match_count": match_count}
    return {"value": value, "match_count": 0}


def build_http_record(
    request_summary: dict[str, Any],
    http_status: int,
    body: Any,
    *,
    content_type: str | None,
    response_bytes: bytes,
    known_values: set[str] | None = None,
) -> dict[str, Any]:
    request_known = redact_known_values_in_value(request_summary, known_values or set())
    body_known = redact_known_values_in_value(body, known_values or set())
    return {
        "request": deep_redact(request_known["value"]),
        "response": {
            "http_status": http_status,
            "content_type": content_type,
            "body": deep_redact(body_known["value"]),
            "body_length": len(response_bytes),
            "body_sha256": hashlib.sha256(response_bytes).hexdigest(),
            "known_value_match_count": int(request_known["match_count"]) + int(body_known["match_count"]),
        },
    }


def write_private_json(path: Path, payload: Any) -> None:
    missing: list[Path] = []
    parent = path.parent
    cursor = parent
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in missing:
        directory.chmod(0o700)
    parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _base_url(group: str, kind: str) -> str:
    if group not in GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    mapping = {
        "api": API_BASES,
        "legacy_llm": LEGACY_LLM_BASES,
        "admin": ADMIN_BASES,
    }
    if kind not in mapping:
        raise ValueError(f"unknown API base kind: {kind}")
    return mapping[kind][group]


def http_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    *,
    auth: str | None = None,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    base_kind: str = "api",
    base_url: str | None = None,
    known_values: set[str] | None = None,
    timeout: float = 120,
    allow_redirects: bool = True,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {auth}"} if auth else {}
    started = time.monotonic()
    response = requests.request(
        method,
        f"{base_url or _base_url(group, base_kind)}{path}",
        headers=headers,
        json=payload,
        params=params,
        timeout=timeout,
        allow_redirects=allow_redirects,
    )
    elapsed = time.monotonic() - started
    content_type = response.headers.get("Content-Type")
    try:
        body: Any = response.json()
        json_body = body if isinstance(body, dict) else {}
    except ValueError:
        text_result = redact_known_values(
            response.text if len(response.content) <= 131072 else "",
            known_values or set(),
        )
        body = {
            "non_json_length": len(response.content),
            "non_json_sha256": hashlib.sha256(response.content).hexdigest(),
            "text": text_result["text"],
            "known_value_match_count": text_result["match_count"],
        }
        json_body = {}
    request_summary = {
        "method": method,
        "path": path,
        "params": params,
        "json": payload,
        "Authorization": headers.get("Authorization"),
    }
    evidence_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    write_private_json(
        evidence_path,
        build_http_record(
            request_summary,
            response.status_code,
            body,
            content_type=content_type,
            response_bytes=response.content,
            known_values=known_values,
        ),
    )
    return {
        "http_status": response.status_code,
        "code": json_body.get("code"),
        "message": json_body.get("message"),
        "data": json_body.get("data"),
        "content_type": content_type,
        "response_length": len(response.content),
        "response_sha256": hashlib.sha256(response.content).hexdigest(),
        "elapsed_seconds": elapsed,
        "raw_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "_content": response.content,
        "_text": response.text,
        "_headers": dict(response.headers),
    }


_BASE_MODULE: Any | None = None


def _load_runtime_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def execution_base():
    global _BASE_MODULE
    if _BASE_MODULE is None:
        module = _load_runtime_module(
            EXECUTE_DIR / "fresh_04_dataset_document.py",
            "fresh_09_dataset_execution_base",
        )
        for item in (module, module.AUTH, module.DB):
            item.EVIDENCE_DIR = EVIDENCE_DIR
            item.RAW_DIR = RAW_DIR
        _BASE_MODULE = module
    return _BASE_MODULE


def owner(case_id: str, group: str) -> dict[str, str]:
    return execution_base()._ensure_owner(case_id, group)


def open_database(group: str):
    return execution_base().DB._open_database(group)


def redis_client(group: str):
    return execution_base().AUTH._redis_client(group)


def register_secondary_user(
    case_id: str,
    group: str,
    label: str,
    *,
    email: str,
    nickname: str,
    password: str,
) -> dict[str, Any]:
    return execution_base().DB._register(
        case_id,
        group,
        label,
        {"email": email, "nickname": nickname, "password": password},
    )


def email_count(group: str, emails: list[str]) -> int:
    return execution_base().DB._email_count(group, emails)


def cleanup_secondary_user(case_id: str, group: str, email: str) -> bool:
    if email_count(group, [email]) == 0:
        return True
    try:
        result = execution_base().DB._disable_and_delete_user(case_id, group, email)
    except requests.exceptions.JSONDecodeError:
        return email_count(group, [email]) == 0
    return result["disabled"].get("code") == 0 and result["deleted"].get("code") == 0 and email_count(group, [email]) == 0


def case_group_result(
    status: str,
    steps: list[dict[str, Any]],
    oracle: dict[str, Any],
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid case status: {status}")
    if not steps or not all(isinstance(step, dict) and step.get("name") for step in steps):
        raise ValueError("group result requires named execution steps")
    if not isinstance(oracle, dict):
        raise ValueError("group result requires an oracle mapping")
    return {
        "status": status,
        "steps": steps,
        "oracle": oracle,
        "findings": findings or [],
    }


def pass_or_fail(
    passed: bool,
    steps: list[dict[str, Any]],
    oracle: dict[str, Any],
    finding_id: str,
    summary: str,
    *,
    code_location: str | None = None,
) -> dict[str, Any]:
    findings = []
    if not passed:
        finding: dict[str, Any] = {"id": finding_id, "summary": summary}
        if code_location:
            finding["code_location"] = code_location
        findings.append(finding)
    return case_group_result("PASS" if passed else "FAIL", steps, oracle, findings)


def run_paired_case(
    case_id: str,
    title: str,
    execute_group: Callable[[str], dict[str, Any]],
    *,
    evidence_path: Path | None = None,
) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for group in GROUP_ORDER:
        try:
            result = execute_group(group)
        except CaseBlocked as error:
            result = case_group_result(
                "BLOCKED",
                [{"name": "environment_blocker", "error_class": type(error).__name__}],
                {"required_environment": "available"},
                [{"id": f"{case_id}-BLOCKED", "summary": str(error)}],
            )
        result = dict(result)
        if result.get("status") not in VALID_STATUSES:
            raise ValueError(f"{case_id}/{group} returned an invalid status")
        if not result.get("steps") or not isinstance(result.get("oracle"), dict):
            raise ValueError(f"{case_id}/{group} returned an invalid result shape")
        result["group"] = group
        result["recorded_at"] = datetime.now(timezone.utc).isoformat()
        groups.append(result)
    statuses = [str(item["status"]) for item in groups]
    record = {
        "batch_id": BATCH_ID,
        "case_id": case_id,
        "title": title,
        "group_order": list(GROUP_ORDER),
        "pair_status": expected_pair_status(statuses),
        "groups": groups,
    }
    write_private_json(evidence_path or EVIDENCE_DIR / f"{case_id}.json", record)
    return record
