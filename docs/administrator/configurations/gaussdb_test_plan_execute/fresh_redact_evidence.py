#!/usr/bin/env python3
"""Redact runtime credentials from private fresh-run raw evidence.

The formal case status is never changed. When raw files change, exact SHA256
references in JSON evidence are updated and a private audit manifest is
written at the evidence root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


EXECUTE_DIR = Path(__file__).resolve().parent
SENSITIVE_KEYS = {
    "access_key",
    "api_key",
    "authorization",
    "client_secret",
    "cookie",
    "password",
    "private_key",
    "secret",
    "secret_key",
    "token",
}
SENSITIVE_SUFFIXES = tuple(f"_{key}" for key in SENSITIVE_KEYS)
TOKEN_PATTERN = re.compile(rb"(?i)(Bearer\s+|\bsk-)[A-Za-z0-9._~+/=-]{8,}")


def _collect_sensitive_values(value: Any, result: set[str], key: str = "") -> None:
    if isinstance(value, dict):
        for child_key, child in value.items():
            _collect_sensitive_values(child, result, str(child_key).lower())
    elif isinstance(value, list):
        for child in value:
            _collect_sensitive_values(child, result, key)
    elif isinstance(value, str) and len(value) >= 8 and (key in SENSITIVE_KEYS or key.endswith(SENSITIVE_SUFFIXES)) and value.lower() not in {"<redacted>", "changeme", "none", "null"}:
        result.add(value)


def _runtime_secrets(runtime_root: Path) -> set[bytes]:
    values: set[str] = set()
    for path in (
        runtime_root / "private_environments.json",
        runtime_root / "private_proxy_config.json",
        runtime_root / "auth_protocol" / "private_config.json",
    ):
        if path.is_file():
            _collect_sensitive_values(json.loads(path.read_text(encoding="utf-8")), values)

    yaml = YAML(typ="safe", pure=True)
    for path in (
        runtime_root / "control" / "conf" / "service_conf.yaml",
        runtime_root / "experiment" / "conf" / "service_conf.yaml",
    ):
        if path.is_file():
            _collect_sensitive_values(yaml.load(path.read_text(encoding="utf-8")), values)
    return {value.encode("utf-8") for value in values if value and value != "<redacted>"}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _replace_hash_references(evidence_root: Path, replacements: dict[str, str]) -> None:
    for path in sorted(evidence_root.rglob("*.json")):
        content = path.read_bytes()
        updated = content
        for old_hash, new_hash in replacements.items():
            updated = updated.replace(old_hash.encode("ascii"), new_hash.encode("ascii"))
        if updated != content:
            path.write_bytes(updated)
            os.chmod(path, 0o600)


def redact(run_id: str) -> dict[str, Any]:
    runtime_root = EXECUTE_DIR / "runtime" / run_id
    evidence_root = EXECUTE_DIR / "runs" / run_id / "evidence_private"
    if not runtime_root.is_dir() or not evidence_root.is_dir():
        raise FileNotFoundError(f"missing runtime or evidence directory for {run_id}")

    secrets = sorted(_runtime_secrets(runtime_root), key=len, reverse=True)
    replacements: dict[str, str] = {}
    artifacts: list[dict[str, Any]] = []
    for path in sorted(evidence_root.rglob("*")):
        if not path.is_file() or "raw" not in path.parts:
            continue
        original = path.read_bytes()
        redacted = original
        for secret in secrets:
            redacted = redacted.replace(secret, b"<redacted>")
        redacted = TOKEN_PATTERN.sub(lambda match: match.group(1) + b"<redacted>", redacted)
        if redacted == original:
            continue

        old_hash = _sha256(original)
        new_hash = _sha256(redacted)
        path.write_bytes(redacted)
        os.chmod(path, 0o600)
        replacements[old_hash] = new_hash
        artifacts.append(
            {
                "path": str(path.relative_to(evidence_root)),
                "pre_redaction_sha256": old_hash,
                "post_redaction_sha256": new_hash,
                "redacted_marker_count": redacted.count(b"<redacted>"),
            }
        )

    _replace_hash_references(evidence_root, replacements)
    manifest = {
        "schema_version": 1,
        "batch_id": run_id,
        "case_status_unchanged": True,
        "plaintext_copy_retained": False,
        "known_runtime_secret_count": len(secrets),
        "artifacts": artifacts,
    }
    manifest_path = evidence_root / "evidence_redaction_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(manifest_path, 0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    manifest = redact(args.run_id)
    print(f"redacted {len(manifest['artifacts'])} raw artifacts; formal case statuses unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
