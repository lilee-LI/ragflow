#
#  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
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
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock


def _install_module(monkeypatch, name, **attrs):
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    if "." in name:
        parent_name, _, child_name = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None:
            monkeypatch.setattr(parent, child_name, module, raising=False)
    return module


class FakeSearchResult:
    pass


class FakeGaussDBStore:
    def db_type(self):
        return "gaussdb"

    def search(self, *_args, **_kwargs):
        return FakeSearchResult()

    def get_total(self, _res):
        return 1

    def get_doc_ids(self, _res):
        return ["chunk-invalid-vector"]

    def get(self, _chunk_id, _index_name, _kb_ids):
        return {
            "id": "chunk-invalid-vector",
            "kb_id": "kb1",
            "doc_id": "doc1",
            "docnm_kwd": "doc.txt",
            "content_with_weight": "sample text",
            "q_4_vec": [0.0, 0.0, 0.0, 0.0],
            "q_4_vec_valid": False,
        }


class FailIfCalledBundle:
    def __init__(self, *_args, **_kwargs):
        pass

    def encode(self, *_args, **_kwargs):
        raise AssertionError("invalid placeholder vectors must be skipped before re-embedding")


def _load_dataset_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[5]
    kb = SimpleNamespace(id="kb1", tenant_id="tenant1", embd_id="old-embd")

    _install_module(
        monkeypatch,
        "api.db.joint_services.tenant_model_service",
        get_model_config_from_provider_instance=lambda *_args, **_kwargs: {"llm_name": "new-embd"},
    )
    _install_module(monkeypatch, "api.db.db_models", File=SimpleNamespace())
    _install_module(
        monkeypatch,
        "api.db.services.document_service",
        DocumentService=SimpleNamespace(),
        queue_raptor_o_graphrag_tasks=MagicMock(),
    )
    _install_module(monkeypatch, "api.db.services.file2document_service", File2DocumentService=SimpleNamespace())
    _install_module(monkeypatch, "api.db.services.file_service", FileService=SimpleNamespace())
    _install_module(
        monkeypatch,
        "api.db.services.knowledgebase_service",
        KnowledgebaseService=SimpleNamespace(
            accessible=staticmethod(lambda *_args: True),
            get_by_id=staticmethod(lambda _dataset_id: (True, kb)),
        ),
    )
    _install_module(monkeypatch, "api.db.services.connector_service", Connector2KbService=SimpleNamespace())
    _install_module(
        monkeypatch,
        "api.db.services.task_service",
        GRAPH_RAPTOR_FAKE_DOC_ID="fake-doc",
        TaskService=SimpleNamespace(),
    )
    _install_module(
        monkeypatch,
        "api.db.services.user_service",
        TenantService=SimpleNamespace(),
        UserService=SimpleNamespace(),
        UserTenantService=SimpleNamespace(),
    )
    _install_module(monkeypatch, "common.settings", docStoreConn=FakeGaussDBStore())
    _install_module(
        monkeypatch,
        "common.constants",
        FileSource=SimpleNamespace(KNOWLEDGEBASE="knowledgebase"),
        LLMType=SimpleNamespace(EMBEDDING="embedding"),
        PAGERANK_FLD="pagerank_fea",
        RetCode=SimpleNamespace(NOT_EFFECTIVE=590),
        StatusEnum=SimpleNamespace(VALID=SimpleNamespace(value="1")),
    )
    _install_module(
        monkeypatch,
        "api.utils.api_utils",
        deep_merge=lambda base, update: {**(base or {}), **(update or {})},
        get_parser_config=lambda *_args, **_kwargs: {},
        remap_dictionary_keys=lambda value: value,
        verify_embedding_availability=lambda *_args, **_kwargs: (True, ""),
    )
    _install_module(monkeypatch, "rag")
    _install_module(monkeypatch, "rag.nlp")
    _install_module(monkeypatch, "rag.nlp.search", index_name=lambda tenant_id: f"ragflow_{tenant_id}")

    spec = importlib.util.spec_from_file_location(
        "api.apps.services.dataset_api_service",
        repo_root / "api" / "apps" / "services" / "dataset_api_service.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "api.apps.services.dataset_api_service", module)
    spec.loader.exec_module(module)
    return module


def test_check_embedding_ignores_gaussdb_invalid_placeholder_vectors(monkeypatch):
    module = _load_dataset_module(monkeypatch)

    _install_module(monkeypatch, "api.db.services.llm_service", LLMBundle=FailIfCalledBundle)

    ok, result = module.check_embedding("kb1", "tenant1", {"embd_id": "new-embd", "check_num": 1})

    assert ok is False
    assert result == "No embedded chunks are available to compare."
