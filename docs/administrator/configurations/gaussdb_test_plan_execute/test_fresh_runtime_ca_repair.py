from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("fresh_runtime_ca_repair.py")


def load_module():
    assert MODULE_PATH.exists(), "fresh runtime CA repair helper must be created"
    spec = importlib.util.spec_from_file_location("fresh_runtime_ca_repair", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_compose_ca_bundle_preserves_system_and_local_certificates(tmp_path: Path) -> None:
    module = load_module()
    system_ca = tmp_path / "system.pem"
    local_ca = tmp_path / "local.pem"
    bundle = tmp_path / "combined.pem"
    system_ca.write_text("-----BEGIN CERTIFICATE-----\nsystem\n-----END CERTIFICATE-----\n")
    local_ca.write_text("-----BEGIN CERTIFICATE-----\nlocal\n-----END CERTIFICATE-----\n")

    result = module.compose_ca_bundle(system_ca, local_ca, bundle)

    content = bundle.read_text()
    assert "system" in content
    assert "local" in content
    assert content.count("-----BEGIN CERTIFICATE-----") == 2
    assert bundle.stat().st_mode & 0o777 == 0o600
    assert result["certificate_count"] == 2


def test_repair_runtime_ca_updates_only_ssl_cert_file_and_is_idempotent(
    tmp_path: Path,
) -> None:
    module = load_module()
    runtime = tmp_path / "runtime"
    protocol = runtime / "auth_protocol"
    protocol.mkdir(parents=True)
    local_ca = protocol / "localhost.crt"
    local_ca.write_text("-----BEGIN CERTIFICATE-----\nlocal\n-----END CERTIFICATE-----\n")
    system_ca = tmp_path / "system.pem"
    system_ca.write_text("-----BEGIN CERTIFICATE-----\nsystem\n-----END CERTIFICATE-----\n")
    private_path = runtime / "private_environments.json"
    original = {
        "control": {"SSL_CERT_FILE": str(local_ca), "KEEP": "control-value"},
        "experiment": {"SSL_CERT_FILE": str(local_ca), "KEEP": "experiment-value"},
    }
    private_path.write_text(json.dumps(original))

    first = module.repair_runtime_ca(runtime, system_ca)
    second = module.repair_runtime_ca(runtime, system_ca)

    repaired = json.loads(private_path.read_text())
    expected_bundle = str(protocol / "combined_ca_bundle.pem")
    assert repaired["control"] == {
        "SSL_CERT_FILE": expected_bundle,
        "KEEP": "control-value",
    }
    assert repaired["experiment"] == {
        "SSL_CERT_FILE": expected_bundle,
        "KEEP": "experiment-value",
    }
    assert private_path.stat().st_mode & 0o777 == 0o600
    assert first["changed_groups"] == ["control", "experiment"]
    assert second["changed_groups"] == []
    assert "control-value" not in json.dumps(first)
