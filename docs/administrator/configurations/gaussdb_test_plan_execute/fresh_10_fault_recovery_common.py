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
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}
EXECUTE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EXECUTE_DIR.parents[3]
EVIDENCE_DIR = evidence_dir("10_fault_recovery")
RAW_DIR = EVIDENCE_DIR / "raw"
RESOURCE_PREFIX = f"fr_{BATCH_ID}"
API_BASES = {
    "control": "http://127.0.0.1:9380/api/v1",
    "experiment": "http://127.0.0.1:9480/api/v1",
}
EXACT_SENSITIVE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "authorization",
    "authorization_token",
    "client_secret",
    "cookie",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "secret_key",
    "token",
}
SECRET_PATTERNS = (
    re.compile(r"^Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"^ragflow-[A-Za-z0-9._-]{8,}$"),
    re.compile(r"^sk-[A-Za-z0-9._-]{8,}$"),
)


class CaseBlocked(RuntimeError):
    """Raised only when the current environment cannot execute a required probe."""


def expected_pair_status(statuses: list[str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "BLOCKED" in statuses:
        return "BLOCKED"
    return "PASS"


def _fingerprint(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _redacted(value: Any) -> dict[str, Any]:
    return {"redacted": True, "fingerprint": _fingerprint(value)}


def deep_redact(value: Any, *, _key: str = "") -> Any:
    key = _key.lower()
    if key in EXACT_SENSITIVE_KEYS and not isinstance(value, (dict, list, tuple)):
        return _redacted(value)
    if isinstance(value, dict):
        return {str(item_key): deep_redact(item_value, _key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, (list, tuple)):
        return [deep_redact(item, _key=_key) for item in value]
    if isinstance(value, str) and any(pattern.match(value) for pattern in SECRET_PATTERNS):
        return _redacted(value)
    return value


def write_private_json(path: Path, payload: Any) -> None:
    missing: list[Path] = []
    cursor = path.parent
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for directory in missing:
        directory.chmod(0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(deep_redact(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


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
    findings: list[dict[str, Any]] = []
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
    started_at = datetime.now(timezone.utc).isoformat()
    for group in GROUP_ORDER:
        try:
            result = execute_group(group)
        except CaseBlocked as error:
            result = case_group_result(
                "BLOCKED",
                [
                    {
                        "name": "environment_blocker",
                        "error_class": type(error).__name__,
                    }
                ],
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
    record = {
        "batch_id": BATCH_ID,
        "case_id": case_id,
        "title": title,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "group_order": list(GROUP_ORDER),
        "pair_status": expected_pair_status([str(item["status"]) for item in groups]),
        "groups": groups,
    }
    write_private_json(evidence_path or EVIDENCE_DIR / f"{case_id}.json", record)
    return deep_redact(record)


_SERVICE_MANAGER: Any | None = None
_EXECUTION_BASE: Any | None = None


def _load_module(path: Path, name: str):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def service_manager():
    global _SERVICE_MANAGER
    if _SERVICE_MANAGER is None:
        _SERVICE_MANAGER = _load_module(
            EXECUTE_DIR / "fresh_service_manager.py",
            "fresh_10_service_manager_runtime",
        )
    return _SERVICE_MANAGER


def group_environment(group: str) -> dict[str, str]:
    if group not in GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    return service_manager().load_group_environment(group)


def execution_base():
    global _EXECUTION_BASE
    if _EXECUTION_BASE is None:
        module = _load_module(
            EXECUTE_DIR / "fresh_04_dataset_document.py",
            "fresh_10_dataset_execution_base",
        )
        for item in (module, module.AUTH, module.DB):
            item.EVIDENCE_DIR = EVIDENCE_DIR
            item.RAW_DIR = RAW_DIR
        _EXECUTION_BASE = module
    return _EXECUTION_BASE


def open_metadata_database(group: str, *, writable: bool = False):
    if group not in GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    base = execution_base()
    opener = base._open_writable_fixture_database if writable else base.DB._open_database
    connection, _user_table, _namespace = opener(group)
    return connection


def owner(case_id: str, group: str) -> dict[str, str]:
    return execution_base()._ensure_owner(case_id, group)


def redis_client(group: str):
    if group not in GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    return execution_base().AUTH._redis_client(group)


def http_request(
    case_id: str,
    group: str,
    label: str,
    method: str,
    path: str,
    *,
    auth: str,
    payload: dict[str, Any] | None = None,
    params: Any = None,
    timeout: float = 180,
) -> dict[str, Any]:
    if group not in GROUP_ORDER:
        raise ValueError(f"unknown group: {group}")
    started = time.monotonic()
    response = requests.request(
        method,
        f"{API_BASES[group]}{path}",
        headers={"Authorization": f"Bearer {auth}"},
        json=payload,
        params=params,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    try:
        body: Any = response.json()
    except ValueError:
        body = {
            "non_json_length": len(response.content),
            "non_json_sha256": hashlib.sha256(response.content).hexdigest(),
        }
    raw_path = RAW_DIR / f"{case_id}_{group}_{label}.json"
    write_private_json(
        raw_path,
        {
            "request": {
                "method": method,
                "path": path,
                "params": params,
                "json": payload,
                "authorization": f"Bearer {auth}",
            },
            "response": {
                "http_status": response.status_code,
                "content_type": response.headers.get("Content-Type"),
                "body": body,
                "body_length": len(response.content),
                "body_sha256": hashlib.sha256(response.content).hexdigest(),
            },
        },
    )
    json_body = body if isinstance(body, dict) else {}
    return {
        "http_status": response.status_code,
        "code": json_body.get("code"),
        "message": json_body.get("message"),
        "data": json_body.get("data"),
        "elapsed_seconds": elapsed,
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
