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
import pytest
import sys
import types


def _install_dialog_service_import_stubs(monkeypatch):
    def install_module(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    class Dummy:
        def __init__(self, *_args, **_kwargs):
            pass

    class Context:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __call__(self, func):
            return func

    class DummyDB:
        @staticmethod
        def connection_context():
            return Context()

        @staticmethod
        def atomic():
            return Context()

    if "langfuse" not in sys.modules:
        install_module("langfuse", Langfuse=Dummy, propagate_attributes=lambda **_kwargs: Context())
    if "mcp.client.session" not in sys.modules:
        install_module("mcp")
        install_module("mcp.client")
        install_module("mcp.client.session", ClientSession=Dummy)
        install_module("mcp.client.sse", sse_client=lambda *_args, **_kwargs: Context())
        install_module("mcp.client.streamable_http", streamablehttp_client=lambda *_args, **_kwargs: Context())
        install_module("mcp.types", CallToolResult=Dummy, ListToolsResult=Dummy, TextContent=Dummy, Tool=Dummy)
    if "beartype" not in sys.modules:
        install_module("beartype", beartype=lambda obj=None, **_kwargs: obj if obj is not None else (lambda wrapped: wrapped))
        install_module("beartype.claw", beartype_this_package=lambda *_args, **_kwargs: None)
    install_module("api.db.services.user_service", UserService=Dummy)
    install_module("api.db.services.file_service", FileService=Dummy)
    install_module("api.db.services.common_service", CommonService=Dummy)
    install_module("api.db.services.doc_metadata_service", DocMetadataService=Dummy)
    install_module("api.db.services.knowledgebase_service", KnowledgebaseService=types.SimpleNamespace(get_field_map=lambda _kb_ids: {}))
    install_module("api.db.services.langfuse_service", TenantLangfuseService=Dummy)
    install_module("api.db.services.llm_service", LLMBundle=Dummy)
    install_module(
        "api.db.joint_services.tenant_model_service",
        get_tenant_default_model_by_type=lambda *_args, **_kwargs: {},
        get_model_config_from_provider_instance=lambda *_args, **_kwargs: {},
        get_model_type_by_name=lambda *_args, **_kwargs: [],
    )
    install_module("api.db.db_models", DB=DummyDB, Dialog=Dummy)
    install_module("common.metadata_utils", apply_meta_data_filter=lambda *_args, **_kwargs: None)
    install_module(
        "api.utils.reference_metadata_utils",
        enrich_chunks_with_document_metadata=lambda chunks, *_args, **_kwargs: chunks,
        resolve_reference_metadata_preferences=lambda *_args, **_kwargs: {},
    )
    install_module("rag.graphrag.general.mind_map_extractor", MindMapExtractor=Dummy)
    install_module("rag.advanced_rag", DeepResearcher=Dummy)
    install_module("rag.app.tag", label_question=lambda *_args, **_kwargs: None)
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
    install_module("rag.utils.tavily_conn", Tavily=Dummy)
    install_module("rag.utils.tts_cache", synthesize_with_cache=lambda *_args, **_kwargs: None)


class FakeSQLChatModel:
    def __init__(self, sql):
        self.sql = sql
        self.calls = []

    async def async_chat(self, sys_prompt, messages, params):
        self.calls.append((sys_prompt, messages, params))
        return self.sql


@pytest.mark.asyncio
async def test_text_to_sql_use_sql_to_gaussdb_adapter_full_chain(gaussdb_env, monkeypatch):
    from common import settings
    from common.doc_store.gaussdb_conn_base import UnsafeGaussDBSQL
    from rag.nlp.search import Dealer
    from rag.utils.gaussdb_conn import GaussDBConnection
    _install_dialog_service_import_stubs(monkeypatch)
    from api.db.services import dialog_service

    conn = GaussDBConnection()
    tenant_id = gaussdb_env["table_prefix"]
    kb_id = "abcdefabcdefabcdefabcdefabcdefab"
    table = f"ragflow_{tenant_id}"
    monkeypatch.setattr(settings, "docStoreConn", conn, raising=False)
    monkeypatch.setattr(settings, "retriever", Dealer(conn), raising=False)
    conn.create_idx(table, kb_id, 4)
    assert conn.insert(
        [
            {
                "id": "row-1",
                "kb_id": kb_id,
                "doc_id": "doc1",
                "docnm_kwd": "finance.csv",
                "content_ltks": "finance amount one hundred twenty",
                "chunk_data": {"amount": 120, "dept": "finance"},
                "q_4_vec": [1, 0, 0, 0],
            },
            {
                "id": "row-2",
                "kb_id": kb_id,
                "doc_id": "doc2",
                "docnm_kwd": "risk.csv",
                "content_ltks": "risk amount twenty",
                "chunk_data": {"amount": 20, "dept": "risk"},
                "q_4_vec": [0, 1, 0, 0],
            },
            {
                "id": "row-3",
                "kb_id": kb_id,
                "doc_id": "doc3",
                "docnm_kwd": "finance-low.csv",
                "content_ltks": "finance low amount twenty",
                "chunk_data": {"amount": 20, "dept": "finance"},
                "q_4_vec": [0, 0, 1, 0],
            },
        ],
        table,
        kb_id,
    ) == []

    chat = FakeSQLChatModel(
        f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount "
        f"FROM {table} "
        f"WHERE kb_id = '{kb_id}' "
        f"AND chunk_data #>> '{{dept}}' = 'finance' "
        f"AND (chunk_data #>> '{{amount}}')::DOUBLE PRECISION > 100"
    )
    result = await dialog_service.use_sql(
        "finance amount greater than 100",
        {"amount": "number", "dept": "string"},
        tenant_id,
        chat,
        quota=False,
        kb_ids=[kb_id],
    )

    assert result is not None
    assert "120" in result["answer"]
    assert any("chunk_data #>> '{amount}'" in call[0] for call in chat.calls)
    assert all("json_extract_string" not in call[0] for call in chat.calls)

    with pytest.raises(UnsafeGaussDBSQL):
        conn.sql(f"DELETE FROM {table} WHERE kb_id = '{kb_id}'")
