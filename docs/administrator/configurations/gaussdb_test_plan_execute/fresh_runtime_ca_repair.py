#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    BATCH_ID,
    RUNTIME_DIR,
)

EXECUTE_DIR = Path(__file__).resolve().parent
SYSTEM_CA = Path("/etc/ssl/certs/ca-certificates.crt")
GROUPS = ("control", "experiment")


def _certificate_count(content: str) -> int:
    return content.count("-----BEGIN CERTIFICATE-----")


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def compose_ca_bundle(system_ca: Path, local_ca: Path, output_path: Path) -> dict[str, Any]:
    system_content = system_ca.read_text(encoding="utf-8")
    local_content = local_ca.read_text(encoding="utf-8")
    system_count = _certificate_count(system_content)
    local_count = _certificate_count(local_content)
    if system_count < 1:
        raise ValueError("system CA file contains no PEM certificates")
    if local_count != 1:
        raise ValueError("localhost CA file must contain exactly one PEM certificate")
    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_path.parent.chmod(0o700)
    content = system_content.rstrip() + "\n" + local_content.strip() + "\n"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(output_path)
    output_path.chmod(0o600)
    return {
        "bundle_path": str(output_path),
        "certificate_count": system_count + local_count,
        "system_certificate_count": system_count,
        "local_certificate_count": local_count,
    }


def repair_runtime_ca(runtime_dir: Path = RUNTIME_DIR, system_ca: Path = SYSTEM_CA) -> dict[str, Any]:
    protocol_dir = runtime_dir / "auth_protocol"
    local_ca = protocol_dir / "localhost.crt"
    bundle_path = protocol_dir / "combined_ca_bundle.pem"
    bundle = compose_ca_bundle(system_ca, local_ca, bundle_path)

    private_path = runtime_dir / "private_environments.json"
    environments = json.loads(private_path.read_text(encoding="utf-8"))
    if not isinstance(environments, dict):
        raise ValueError("private environments must be an object")
    changed_groups = []
    for group in GROUPS:
        environment = environments.get(group)
        if not isinstance(environment, dict):
            raise ValueError(f"missing private environment for {group}")
        if environment.get("SSL_CERT_FILE") != str(bundle_path):
            changed_groups.append(group)
        environment["SSL_CERT_FILE"] = str(bundle_path)
    _atomic_private_json(private_path, environments)
    return {
        "status": "repaired",
        "batch_id": BATCH_ID,
        "changed_groups": changed_groups,
        "bundle_path": str(bundle_path),
        "certificate_count": bundle["certificate_count"],
        "private_environment_mode": oct(private_path.stat().st_mode & 0o777),
        "bundle_mode": oct(bundle_path.stat().st_mode & 0o777),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine the system and fresh localhost CA bundles")
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME_DIR)
    parser.add_argument("--system-ca", type=Path, default=SYSTEM_CA)
    args = parser.parse_args()
    print(
        json.dumps(
            repair_runtime_ca(args.runtime_dir, args.system_ca),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
