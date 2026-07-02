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
import sys
import types

import pytest


class StubDocEngineConnection:
    def db_type(self):
        return "stub"


class StubStorage:
    def health(self):
        return True


def _install_module(name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


@pytest.fixture
def dealer_cls():
    module_names = [
        "common.settings",
        "rag.nlp.search",
        "rag.nlp.query",
        "rag.utils.redis_conn",
        "memory.utils",
        "json_repair",
    ]
    module_names.extend(f"rag.utils.{name}" for name in (
        "es_conn",
        "infinity_conn",
        "ob_conn",
        "opensearch_conn",
        "azure_sas_conn",
        "azure_spn_conn",
        "gcs_conn",
        "minio_conn",
        "opendal_conn",
        "s3_conn",
        "oss_conn",
    ))
    module_names.extend(f"memory.utils.{name}" for name in ("es_conn", "infinity_conn", "ob_conn"))
    saved_modules = {name: sys.modules.get(name) for name in module_names}
    import common
    import memory

    common_module = common
    saved_common_settings = getattr(common_module, "settings", None) if common_module else None
    memory_module = memory
    saved_memory_utils = getattr(memory_module, "utils", None) if memory_module else None

    settings_stub = _install_module(
        "common.settings",
        DOC_ENGINE_GAUSSDB=True,
        DOC_ENGINE_INFINITY=False,
        DOC_ENGINE_OCEANBASE=False,
    )
    if common_module is not None:
        setattr(common_module, "settings", settings_stub)

    _install_module("rag.utils.redis_conn", REDIS_CONN=types.SimpleNamespace(health=lambda: True, is_alive=lambda: False, REDIS=None))
    _install_module("json_repair", loads=lambda value: value)
    for short_name, attrs in {
        "es_conn": {"ESConnection": StubDocEngineConnection},
        "infinity_conn": {"InfinityConnection": StubDocEngineConnection},
        "ob_conn": {"OBConnection": StubDocEngineConnection},
        "opensearch_conn": {"OSConnection": StubDocEngineConnection},
        "azure_sas_conn": {"RAGFlowAzureSasBlob": StubStorage},
        "azure_spn_conn": {"RAGFlowAzureSpnBlob": StubStorage},
        "gcs_conn": {"RAGFlowGCS": StubStorage},
        "minio_conn": {"RAGFlowMinio": StubStorage},
        "opendal_conn": {"OpenDALStorage": StubStorage},
        "s3_conn": {"RAGFlowS3": StubStorage},
        "oss_conn": {"RAGFlowOSS": StubStorage},
    }.items():
        _install_module(f"rag.utils.{short_name}", **attrs)

    memory_utils = types.ModuleType("memory.utils")
    memory_utils.__path__ = []
    sys.modules["memory.utils"] = memory_utils
    if memory_module is not None:
        setattr(memory_module, "utils", memory_utils)
    for short_name in ("es_conn", "infinity_conn", "ob_conn"):
        module = _install_module(
            f"memory.utils.{short_name}",
            ESConnection=StubDocEngineConnection,
            InfinityConnection=StubDocEngineConnection,
            OBConnection=StubDocEngineConnection,
        )
        setattr(memory_utils, short_name, module)

    try:
        import importlib

        yield importlib.import_module("rag.nlp.search").Dealer
    finally:
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if common_module is not None:
            if saved_common_settings is None:
                if hasattr(common_module, "settings"):
                    delattr(common_module, "settings")
            else:
                setattr(common_module, "settings", saved_common_settings)
        if memory_module is not None:
            if saved_memory_utils is None:
                if hasattr(memory_module, "utils"):
                    delattr(memory_module, "utils")
            else:
                setattr(memory_module, "utils", saved_memory_utils)


def make_dealer(dealer_cls):
    dealer = dealer_cls.__new__(dealer_cls)
    dealer.dataStore = FakeGaussDBStore()
    return dealer


class FakeGaussDBStore:
    def db_type(self):
        return "gaussdb"

    def search(self, *args, **kwargs):
        self.last_search = (args, kwargs)
        return type("SearchResult", (), {"total": 0, "chunks": []})()

    def get_total(self, res):
        return res.total

    def get_doc_ids(self, _res):
        return []

    def get_highlight(self, *_args):
        return {}

    def get_aggregation(self, *_args):
        return {}

    def get_fields(self, *_args):
        return {}


def test_dealer_passes_vector_similarity_weight_to_gaussdb_search(dealer_cls):
    dealer = make_dealer(dealer_cls)

    kwargs = dealer._prepare_gaussdb_search_kwargs(
        {
            "question": "risk",
            "vector": [0.1, 0.2, 0.3, 0.4],
            "vector_similarity_weight": 0.75,
            "similarity_threshold": 0.2,
        }
    )

    assert "rank_feature" not in kwargs
    assert kwargs["gaussdb_search_params"]["vector_similarity_weight"] == 0.75
    assert kwargs["gaussdb_search_params"]["term_similarity_weight"] == 0.25
    assert kwargs["gaussdb_search_params"]["similarity_threshold"] == 0.2


def test_dealer_uses_text_only_when_request_has_no_vector(dealer_cls):
    dealer = make_dealer(dealer_cls)

    kwargs = dealer._prepare_gaussdb_search_kwargs({"question": "risk contract"})

    assert kwargs["gaussdb_search_params"]["vector_similarity_weight"] == 0.0
    assert kwargs["gaussdb_search_params"]["term_similarity_weight"] == 1.0
    assert kwargs["vector"] is None
    assert kwargs["keywords"] == ["risk", "contract"]


def test_dealer_uses_vector_only_when_request_has_no_keywords(dealer_cls):
    dealer = make_dealer(dealer_cls)

    kwargs = dealer._prepare_gaussdb_search_kwargs({"vector": [0.1, 0.2, 0.3, 0.4], "question": ""})

    assert kwargs["gaussdb_search_params"]["vector_similarity_weight"] == 1.0
    assert kwargs["gaussdb_search_params"]["term_similarity_weight"] == 0.0
    assert kwargs["keywords"] == []


def test_dealer_passes_page_offset_without_resetting_deep_pages(dealer_cls):
    dealer = make_dealer(dealer_cls)

    kwargs = dealer._prepare_gaussdb_search_kwargs({"page": 5, "size": 20, "question": "risk"})

    assert kwargs["offset"] == 80
    assert kwargs["limit"] == 20
