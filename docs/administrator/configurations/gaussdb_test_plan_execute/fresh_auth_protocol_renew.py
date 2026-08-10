#!/usr/bin/env python3
"""Renew only the current fresh batch's disposable SMTPS certificate."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
)

EXECUTE_DIR = Path(__file__).resolve().parent
PROTOCOL_DIR = RUNTIME_DIR / "auth_protocol"
CONFIG_PATH = PROTOCOL_DIR / "private_config.json"


def validate_targets(protocol_dir: Path, config: dict[str, Any]) -> tuple[Path, Path]:
    root = protocol_dir.resolve()
    cert = Path(str(config["tls_cert"])).resolve()
    key = Path(str(config["tls_key"])).resolve()
    if cert.parent != root or key.parent != root:
        raise ValueError("TLS renewal target is outside the fresh protocol directory")
    if cert.name != "localhost.crt" or key.name != "localhost.key":
        raise ValueError("unexpected TLS renewal target name")
    return cert, key


def file_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def renew(days: int = 30) -> dict[str, Any]:
    if days < 7:
        raise ValueError("renewal must remain valid for at least seven days")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    cert, key = validate_targets(PROTOCOL_DIR, config)
    temporary_cert = PROTOCOL_DIR / ".localhost.crt.renewing"
    temporary_key = PROTOCOL_DIR / ".localhost.key.renewing"
    temporary_cert.unlink(missing_ok=True)
    temporary_key.unlink(missing_ok=True)
    try:
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                str(days),
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost,IP:127.0.0.1",
                "-keyout",
                str(temporary_key),
                "-out",
                str(temporary_cert),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        temporary_cert.chmod(0o600)
        temporary_key.chmod(0o600)
        subprocess.run(
            ["openssl", "x509", "-in", str(temporary_cert), "-noout", "-checkend", "604800"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.replace(temporary_key, key)
        os.replace(temporary_cert, cert)
        key.chmod(0o600)
        cert.chmod(0o600)
    finally:
        temporary_cert.unlink(missing_ok=True)
        temporary_key.unlink(missing_ok=True)
    return {
        "batch_id": BATCH_ID,
        "days": days,
        "certificate_sha256": file_fingerprint(cert),
        "private_key_sha256": file_fingerprint(key),
        "certificate_mode": oct(cert.stat().st_mode & 0o777),
        "private_key_mode": oct(key.stat().st_mode & 0o777),
    }


def main() -> None:
    result = renew()
    print(
        json.dumps(
            {
                "batch_id": result["batch_id"],
                "days": result["days"],
                "certificate_sha256": result["certificate_sha256"],
                "certificate_mode": result["certificate_mode"],
                "private_key_mode": result["private_key_mode"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
