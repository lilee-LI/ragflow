#
#  Copyright 2025 The InfiniFlow Authors. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
#
import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


class FakeGaussDBConnection:
    def db_type(self):
        return "gaussdb"

    def health(self):
        return {
            "status": "healthy",
            "uri": "sqlbuilder@db.example:19995/postgres?schema=public",
            "version_comment": "GaussDB",
            "sql_compatibility": "A",
        }

    def get_performance_metrics(self):
        return {"connection": "connected", "latency_ms": 3.2}


class FailingGaussDBConnection:
    def health(self):
        raise RuntimeError("connection failed for password=fake-unit-password")


def _install_module(monkeypatch, name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod


def _import_health_utils(monkeypatch):
    import common

    settings_stub = types.SimpleNamespace(
        docStoreConn=types.SimpleNamespace(health=lambda: {"status": "healthy"}),
        STORAGE_IMPL=types.SimpleNamespace(health=lambda: True),
        STORAGE_IMPL_TYPE="MINIO",
        MINIO={"host": "minio:9000"},
    )
    monkeypatch.setitem(sys.modules, "common.settings", settings_stub)
    monkeypatch.setattr(common, "settings", settings_stub, raising=False)

    _install_module(monkeypatch, "api.db.db_models", DB=types.SimpleNamespace(execute_sql=lambda *_args, **_kwargs: True))
    _install_module(monkeypatch, "rag.utils.redis_conn", REDIS_CONN=types.SimpleNamespace(health=lambda: True))
    _install_module(monkeypatch, "rag.utils.es_conn", ESConnection=lambda: types.SimpleNamespace(get_cluster_stats=lambda: {}))
    _install_module(monkeypatch, "rag.utils.infinity_conn", InfinityConnection=lambda: types.SimpleNamespace(health=lambda: {}))
    _install_module(monkeypatch, "rag.utils.ob_conn", OBConnection=lambda: types.SimpleNamespace(health=lambda: {}, get_performance_metrics=lambda: {}))
    _install_module(monkeypatch, "rag.utils.gaussdb_conn", GaussDBConnection=FakeGaussDBConnection)

    monkeypatch.delitem(sys.modules, "api.utils.health_utils", raising=False)
    return importlib.import_module("api.utils.health_utils")


def test_get_gaussdb_status_not_configured(monkeypatch):
    health_utils = _import_health_utils(monkeypatch)
    monkeypatch.setenv("DOC_ENGINE", "elasticsearch")

    result = health_utils.get_gaussdb_status()

    assert result["status"] == "not_configured"


def test_get_gaussdb_status_healthy(monkeypatch):
    health_utils = _import_health_utils(monkeypatch)
    monkeypatch.setenv("DOC_ENGINE", "gaussdb")
    monkeypatch.setattr(health_utils, "GaussDBConnection", FakeGaussDBConnection)

    result = health_utils.get_gaussdb_status()

    assert result["status"] == "alive"
    assert result["message"]["health"]["status"] == "healthy"
    assert result["message"]["performance"]["connection"] == "connected"


def test_get_gaussdb_status_reuses_settings_doc_store_connection(monkeypatch):
    health_utils = _import_health_utils(monkeypatch)
    doc_store_conn = FakeGaussDBConnection()
    monkeypatch.setattr(health_utils.settings, "docStoreConn", doc_store_conn, raising=False)
    monkeypatch.setenv("DOC_ENGINE", "gaussdb")
    monkeypatch.setattr(health_utils, "GaussDBConnection", FailingGaussDBConnection)

    result = health_utils.get_gaussdb_status()

    assert result["status"] == "alive"
    assert result["message"]["performance"]["connection"] == "connected"


def test_get_gaussdb_status_error_masks_password(monkeypatch):
    health_utils = _import_health_utils(monkeypatch)
    monkeypatch.setenv("DOC_ENGINE", "gaussdb")
    monkeypatch.setattr(health_utils, "GaussDBConnection", FailingGaussDBConnection)

    result = health_utils.get_gaussdb_status()

    assert result["status"] == "timeout"
    assert "fake-unit-password" not in result["message"]
    assert "***" in result["message"]


def test_system_api_exposes_gaussdb_status_route():
    text = (ROOT / "api" / "apps" / "restful_apis" / "system_api.py").read_text(encoding="utf-8")

    assert '"/system/gaussdb/status"' in text
    assert "get_gaussdb_status" in text
