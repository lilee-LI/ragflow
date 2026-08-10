#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CASE_ID = re.compile(r"^(?:TC-[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{3}|RF-AUDIT-\d{3})$")
KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9._-]{8,}\b")
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "beta",
    "api_key",
    "access_key",
    "authorization",
    "cookie",
)
VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def sanitize(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(part in lowered for part in SENSITIVE_KEY_PARTS):
        return {"redacted": True, "fingerprint": fingerprint(value)}
    if isinstance(value, dict):
        return {str(item_key): sanitize(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item, key) for item in value]
    if isinstance(value, str):
        return KEY_PATTERN.sub("<redacted>", value)
    return value


class CaseRecorder:
    def __init__(self, case_id: str, title: str):
        if not CASE_ID.fullmatch(case_id):
            raise ValueError("invalid case id")
        if not title.strip():
            raise ValueError("title is required")
        self.case_id = case_id
        self.title = title.strip()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.groups: list[dict[str, Any]] = []

    def add_group(
        self,
        group: str,
        status: str,
        steps: list[dict[str, Any]],
        *,
        oracle: dict[str, Any] | None = None,
        findings: list[dict[str, Any]] | None = None,
    ) -> None:
        if group not in {"control", "experiment"}:
            raise ValueError("invalid group")
        expected = "control" if not self.groups else "experiment"
        if group != expected:
            if not self.groups and group == "experiment":
                raise ValueError("control group must be recorded first")
            raise ValueError(f"expected {expected} group")
        if len(self.groups) >= 2:
            raise ValueError("both groups already recorded")
        if status not in VALID_STATUSES:
            raise ValueError("invalid status")
        if not isinstance(steps, list):
            raise ValueError("steps must be a list")
        self.groups.append(
            sanitize(
                {
                    "group": group,
                    "status": status,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "steps": steps,
                    "oracle": oracle or {},
                    "findings": findings or [],
                }
            )
        )

    def finalize(self) -> dict[str, Any]:
        if [item["group"] for item in self.groups] != ["control", "experiment"]:
            raise ValueError("both groups must be recorded in order")
        statuses = {item["status"] for item in self.groups}
        pair_status = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
        return {
            "case_id": self.case_id,
            "title": self.title,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "group_order": ["control", "experiment"],
            "pair_status": pair_status,
            "groups": self.groups,
        }


def write_evidence(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(sanitize(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)
