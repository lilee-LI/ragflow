import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fresh_auth_protocol_renew.py")


def load_module():
    spec = importlib.util.spec_from_file_location("fresh_auth_protocol_renew", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_validate_targets_requires_cert_and_key_inside_protocol_directory(tmp_path):
    module = load_module()
    config = {
        "tls_cert": str(tmp_path / "localhost.crt"),
        "tls_key": str(tmp_path / "localhost.key"),
    }

    assert module.validate_targets(tmp_path, config) == (
        tmp_path / "localhost.crt",
        tmp_path / "localhost.key",
    )

    with pytest.raises(ValueError, match="outside"):
        module.validate_targets(
            tmp_path,
            {**config, "tls_key": str(tmp_path.parent / "outside.key")},
        )
