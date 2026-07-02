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
import asyncio
import importlib
import sys
import types
import warnings

import pytest

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)


def _install_cv2_stub_if_unavailable():
    try:
        import cv2  # noqa: F401

        return
    except Exception:
        pass

    stub = types.ModuleType("cv2")
    stub.INTER_LINEAR = 1
    stub.INTER_CUBIC = 2
    stub.BORDER_CONSTANT = 0
    stub.BORDER_REPLICATE = 1
    stub.COLOR_BGR2RGB = 0
    stub.COLOR_BGR2GRAY = 1
    stub.COLOR_GRAY2BGR = 2
    stub.IMREAD_IGNORE_ORIENTATION = 128
    stub.IMREAD_COLOR = 1
    stub.RETR_LIST = 1
    stub.CHAIN_APPROX_SIMPLE = 2

    def _module_getattr(name):
        if name.isupper():
            return 0
        raise RuntimeError(f"cv2.{name} is unavailable in this test environment")

    stub.__getattr__ = _module_getattr
    sys.modules["cv2"] = stub


def _install_settings_import_stubs(monkeypatch):
    class StubDocEngineConnection:
        def db_type(self):
            return "stub"

    class StubStorage:
        def health(self):
            return True

    def install_module(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    try:
        import rag.utils
        import memory.utils
    except Exception:
        return

    rag_modules = {
        "es_conn": {"ESConnection": StubDocEngineConnection},
        "infinity_conn": {"InfinityConnection": StubDocEngineConnection},
        "ob_conn": {"OBConnection": StubDocEngineConnection},
        "opensearch_conn": {"OSConnection": StubDocEngineConnection},
        "azure_sas_conn": {"RAGFlowAzureSasBlob": StubStorage},
        "azure_spn_conn": {"RAGFlowAzureSpnBlob": StubStorage},
        "gcs_conn": {"RAGFlowGCS": StubStorage},
        "minio_conn": {"RAGFlowMinio": StubStorage},
        "opendal_conn": {"OpenDALStorage": StubStorage},
        "redis_conn": {"REDIS_CONN": types.SimpleNamespace(health=lambda: True, is_alive=lambda: False, REDIS=None)},
        "s3_conn": {"RAGFlowS3": StubStorage},
        "oss_conn": {"RAGFlowOSS": StubStorage},
    }
    for short_name, attrs in rag_modules.items():
        module = install_module(f"rag.utils.{short_name}", **attrs)
        monkeypatch.setattr(rag.utils, short_name, module, raising=False)

    for short_name in ("es_conn", "infinity_conn", "ob_conn"):
        module = install_module(
            f"memory.utils.{short_name}",
            ESConnection=StubDocEngineConnection,
            InfinityConnection=StubDocEngineConnection,
            OBConnection=StubDocEngineConnection,
        )
        monkeypatch.setattr(memory.utils, short_name, module, raising=False)

    if "json_repair" not in sys.modules:
        import json

        install_module("json_repair", loads=json.loads)
    if "langfuse" not in sys.modules:
        class StubLangfuse:
            pass

        def propagate_attributes(**_kwargs):
            class Context:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            return Context()

        install_module("langfuse", Langfuse=StubLangfuse, propagate_attributes=propagate_attributes)
    if "mcp.client.session" not in sys.modules:
        install_module("mcp")
        install_module("mcp.client")

        class ClientSession:
            pass

        async def _client(*_args, **_kwargs):
            raise RuntimeError("mcp client is unavailable in this test")

        class _MCPType:
            pass

        install_module("mcp.client.session", ClientSession=ClientSession)
        install_module("mcp.client.sse", sse_client=_client)
        install_module("mcp.client.streamable_http", streamablehttp_client=_client)
        install_module(
            "mcp.types",
            CallToolResult=_MCPType,
            ListToolsResult=_MCPType,
            TextContent=_MCPType,
            Tool=_MCPType,
        )
    if "beartype" not in sys.modules:
        def beartype(obj=None, **_kwargs):
            if obj is None:
                return lambda wrapped: wrapped
            return obj

        install_module("beartype", beartype=beartype)
        install_module("beartype.claw", beartype_this_package=lambda *_args, **_kwargs: None)

    class _Dummy:
        def __init__(self, *_args, **_kwargs):
            pass

    class _Context:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __call__(self, func):
            return func

    class _DummyDB:
        @staticmethod
        def connection_context():
            return _Context()

        @staticmethod
        def atomic():
            return _Context()

    install_module("api.db.services.user_service", UserService=_Dummy)
    install_module("api.db.services.file_service", FileService=_Dummy)
    install_module("api.db.services.common_service", CommonService=_Dummy)
    install_module("api.db.services.doc_metadata_service", DocMetadataService=_Dummy)
    install_module(
        "api.db.services.knowledgebase_service",
        KnowledgebaseService=types.SimpleNamespace(get_field_map=lambda _kb_ids: {}),
    )
    install_module("api.db.services.langfuse_service", TenantLangfuseService=_Dummy)
    install_module("api.db.services.llm_service", LLMBundle=_Dummy)
    install_module(
        "api.db.joint_services.tenant_model_service",
        get_tenant_default_model_by_type=lambda *_args, **_kwargs: {},
        get_model_config_from_provider_instance=lambda *_args, **_kwargs: {},
        get_model_type_by_name=lambda *_args, **_kwargs: [],
    )
    install_module("api.db.db_models", DB=_DummyDB, Dialog=_Dummy)
    install_module("common.metadata_utils", apply_meta_data_filter=lambda *_args, **_kwargs: None)
    install_module(
        "api.utils.reference_metadata_utils",
        enrich_chunks_with_document_metadata=lambda chunks, *_args, **_kwargs: chunks,
        resolve_reference_metadata_preferences=lambda *_args, **_kwargs: {},
    )
    install_module("rag.graphrag.general.mind_map_extractor", MindMapExtractor=_Dummy)
    install_module("rag.advanced_rag", DeepResearcher=_Dummy)
    install_module("rag.app.tag", label_question=lambda *_args, **_kwargs: None)
    install_module("rag.nlp.search", index_name=lambda tenant_id: f"ragflow_{tenant_id}")
    install_module(
        "rag.prompts.generator",
        chunks_format=lambda *_args, **_kwargs: "",
        citation_prompt=lambda: "",
        cross_languages=lambda *_args, **_kwargs: "",
        full_question=lambda *_args, **_kwargs: "",
        kb_prompt=lambda *_args, **_kwargs: [],
        keyword_extraction=lambda *_args, **_kwargs: "",
        message_fit_in=lambda *_args, **_kwargs: (0, []),
        PROMPT_JINJA_ENV=types.SimpleNamespace(from_string=lambda *_args, **_kwargs: types.SimpleNamespace(render=lambda **_kw: "")),
        ASK_SUMMARY="",
    )
    install_module("common.token_utils", num_tokens_from_string=lambda *_args, **_kwargs: 0)
    install_module("rag.utils.tavily_conn", Tavily=_Dummy)
    install_module("rag.utils.tts_cache", synthesize_with_cache=lambda *_args, **_kwargs: None)


_install_cv2_stub_if_unavailable()


class FakeChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def async_chat(self, sys_prompt, messages, params):
        self.calls.append((sys_prompt, messages, params))
        if not self.responses:
            raise AssertionError("async_chat called more times than expected")
        return self.responses.pop(0)


class RecordingRetriever:
    def __init__(self, fail_first=False, missing_source_once=False):
        self.sqls = []
        self.fail_first = fail_first
        self.missing_source_once = missing_source_once

    def sql_retrieval(self, sql, format="json"):
        assert format == "json"
        self.sqls.append(sql)
        if self.fail_first and len(self.sqls) == 1:
            raise RuntimeError("json_extract_string does not exist")
        missing_source_call = 2 if self.fail_first else 1
        if self.missing_source_once and len(self.sqls) == missing_source_call:
            return {"columns": [{"name": "amount"}], "rows": [["120"]]}
        return {
            "columns": [{"name": "doc_id"}, {"name": "docnm_kwd"}, {"name": "kb_id"}, {"name": "amount"}],
            "rows": [["doc1", "finance.csv", "abcdefabcdefabcdefabcdefabcdefab", "120"]],
        }


class AggregateRecordingRetriever:
    def __init__(self):
        self.sqls = []

    def sql_retrieval(self, sql, format="json"):
        assert format == "json"
        self.sqls.append(sql)
        if len(self.sqls) == 1:
            return {"columns": [{"name": "total_amount"}], "rows": [["120"]]}
        return {
            "columns": [{"name": "doc_id"}, {"name": "docnm_kwd"}, {"name": "kb_id"}],
            "rows": [["doc1", "finance.csv", "abcdefabcdefabcdefabcdefabcdefab"]],
        }


@pytest.fixture
def dialog_service(monkeypatch):
    _install_settings_import_stubs(monkeypatch)
    module = importlib.import_module("api.db.services.dialog_service")
    try:
        yield module
    finally:
        sys.modules.pop("api.db.services.dialog_service", None)
        package = sys.modules.get("api.db.services")
        if package is not None and hasattr(package, "dialog_service"):
            delattr(package, "dialog_service")


@pytest.fixture
def enable_gaussdb_docengine(monkeypatch, dialog_service):
    monkeypatch.setattr(dialog_service.settings, "DOC_ENGINE_INFINITY", False, raising=False)
    monkeypatch.setattr(dialog_service.settings, "DOC_ENGINE_OCEANBASE", False, raising=False)
    monkeypatch.setattr(dialog_service.settings, "DOC_ENGINE_GAUSSDB", True, raising=False)


def test_gaussdb_prompt_uses_jsonb_path_not_oceanbase_json_helpers(dialog_service, enable_gaussdb_docengine):
    prompt = dialog_service._build_gaussdb_sql_prompt(
        table_name="ragflow_0123456789abcdef0123456789abcdef",
        field_map={"amount": "number", "dept": "string"},
        question="amount greater than 100",
    )

    assert "#>> '{amount}'" in prompt
    assert "json_extract_string" not in prompt
    assert "doc_id" in prompt
    assert "docnm_kwd" in prompt


def test_use_sql_routes_gaussdb_prompt_through_retriever(monkeypatch, dialog_service, enable_gaussdb_docengine):
    tenant_id = "0123456789abcdef0123456789abcdef"
    kb_id = "abcdefabcdefabcdefabcdefabcdefab"
    table = f"ragflow_{tenant_id}"
    retriever = RecordingRetriever()
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    chat = FakeChatModel(
        [
            f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount FROM {table} "
            f"WHERE kb_id = '{kb_id}' AND (chunk_data #>> '{{amount}}')::DOUBLE PRECISION > 100"
        ]
    )

    result = asyncio.run(
        dialog_service.use_sql(
            "finance amount greater than 100",
            {"amount": "number", "dept": "string"},
            tenant_id,
            chat,
            quota=False,
            kb_ids=[kb_id],
        )
    )

    assert result is not None
    assert "120" in result["answer"]
    assert retriever.sqls
    assert "chunk_data #>>" in retriever.sqls[-1]
    assert "json_extract_string" not in retriever.sqls[-1]


def test_use_sql_retries_and_repairs_gaussdb_sql(monkeypatch, dialog_service, enable_gaussdb_docengine):
    tenant_id = "0123456789abcdef0123456789abcdef"
    kb_id = "abcdefabcdefabcdefabcdefabcdefab"
    table = f"ragflow_{tenant_id}"
    retriever = RecordingRetriever(missing_source_once=True)
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    chat = FakeChatModel(
        [
            f"SELECT doc_id, docnm_kwd, json_extract_string(chunk_data, '$.amount') AS amount FROM {table} WHERE kb_id = '{kb_id}'",
            f"SELECT chunk_data #>> '{{amount}}' AS amount FROM {table} WHERE kb_id = '{kb_id}'",
            f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount FROM {table} WHERE kb_id = '{kb_id}'",
        ]
    )

    result = asyncio.run(
        dialog_service.use_sql(
            "finance amount greater than 100",
            {"amount": "number", "dept": "string"},
            tenant_id,
            chat,
            quota=False,
            kb_ids=[kb_id],
        )
    )

    assert result is not None
    assert len(retriever.sqls) == 2
    assert all("json_extract_string" not in sql for sql in retriever.sqls)
    assert "doc_id" in retriever.sqls[-1]
    assert "docnm_kwd" in retriever.sqls[-1]
    assert any("GaussDB" in call[0] and "#>>" in call[0] for call in chat.calls)
    retry_prompt = chat.calls[1][1][0]["content"]
    repair_prompt = chat.calls[2][1][0]["content"]
    assert "to_date(chunk_data #>>" in retry_prompt
    assert "to_date(chunk_data #>>" in repair_prompt


def test_use_sql_aggregate_fallback_fetches_sources_with_kb_scope(monkeypatch, dialog_service, enable_gaussdb_docengine):
    tenant_id = "0123456789abcdef0123456789abcdef"
    kb_id = "abcdefabcdefabcdefabcdefabcdefab"
    table = f"ragflow_{tenant_id}"
    retriever = AggregateRecordingRetriever()
    monkeypatch.setattr(dialog_service.settings, "retriever", retriever, raising=False)
    chat = FakeChatModel(
        [
            f"SELECT SUM((chunk_data #>> '{{amount}}')::DOUBLE PRECISION) AS total_amount "
            f"FROM {table} WHERE kb_id = '{kb_id}' AND chunk_data #>> '{{dept}}' = 'finance'"
        ]
    )

    result = asyncio.run(
        dialog_service.use_sql(
            "sum finance amount",
            {"amount": "number", "dept": "string"},
            tenant_id,
            chat,
            quota=False,
            kb_ids=[kb_id],
        )
    )

    assert result is not None
    assert len(retriever.sqls) == 2
    assert "select doc_id" in retriever.sqls[1].lower()
    assert "docnm_kwd" in retriever.sqls[1]
    assert f"kb_id = '{kb_id}'" in retriever.sqls[1]
    assert result["reference"]["chunks"][0]["kb_id"] == kb_id
