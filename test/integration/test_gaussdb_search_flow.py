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
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import json
import logging

import numpy as np
import pytest
import uuid
from psycopg2 import sql
from types import SimpleNamespace


pytestmark = [pytest.mark.gaussdb_integration, pytest.mark.gaussdb_both]


class FakeEmbeddingModel:
    def encode_queries(self, _text):
        return [0.1, 0.2, 0.3, 0.4], 4

    def encode(self, texts):
        return np.asarray([[0.1, 0.2, 0.3, 0.4] for _ in texts]), len(texts)


class ScriptedChatModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def async_chat(self, system, messages, options):
        self.calls.append((system, messages, options))
        if not self.responses:
            raise AssertionError("chat model received more calls than the TC contract allows")
        return self.responses.pop(0)


class RecordingDealer:
    def __init__(self, dealer):
        self._dealer = dealer
        self.sqls = []
        self.retrieval_calls = []

    def sql_retrieval(self, statement, *args, **kwargs):
        self.sqls.append(statement)
        return self._dealer.sql_retrieval(statement, *args, **kwargs)

    async def retrieval(self, *args, **kwargs):
        self.retrieval_calls.append((args, kwargs))
        return await self._dealer.retrieval(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._dealer, name)


@pytest.mark.asyncio
async def test_tc_ret_906_dealer_search_fulltext_vector_and_hybrid_full_chain(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.nlp.search import Dealer
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    dealer = Dealer(conn)
    kb_id = uuid.uuid4().hex
    table = table_name(gaussdb_env)
    assert conn.create_idx(table, kb_id, 4) is True
    with gaussdb_admin_conn.cursor() as cur:
        cur.executemany(
            sql.SQL(
                "INSERT INTO {} (id, kb_id, doc_id, title_tks, content_with_weight, content_ltks, content_sm_ltks, q_4_vec, q_4_vec_valid) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::floatvector(4), %s)"
            ).format(sql.Identifier(gaussdb_env["schema"], table)),
            [
                (
                    "txt-hit",
                    kb_id,
                    "doc-a",
                    "contract",
                    "risk contract audit",
                    "risk contract audit",
                    "risk contract audit",
                    json.dumps([0.01, 0.01, 0.01, 0.01]),
                    True,
                ),
                (
                    "vec-hit",
                    kb_id,
                    "doc-b",
                    "budget",
                    "budget memo",
                    "budget memo",
                    "budget memo",
                    json.dumps([0.1, 0.2, 0.3, 0.4]),
                    True,
                ),
                (
                    "placeholder",
                    kb_id,
                    "doc-c",
                    "risk",
                    "risk placeholder",
                    "risk placeholder",
                    "risk placeholder",
                    json.dumps([0.0, 0.0, 0.0, 0.0]),
                    False,
                ),
            ],
        )
    gaussdb_admin_conn.commit()
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT id, q_4_vec_valid FROM {} ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], table)))
        assert cur.fetchall() == [("placeholder", False), ("txt-hit", True), ("vec-hit", True)]

    text_res = await dealer.search(
        {"question": "risk contract", "page": 1, "size": 5},
        table,
        [kb_id],
        emb_mdl=None,
        highlight=True,
    )
    assert text_res.ids == ["txt-hit"]
    assert text_res.total == 1
    assert text_res.field["txt-hit"]["_score"] > 0

    vector_only_res = conn.search(
        ["id", "content_with_weight", "doc_id", "q_4_vec"],
        [],
        {},
        [MatchDenseExpr("q_4_vec", [0.1, 0.2, 0.3, 0.4], "float", "cosine", 10)],
        OrderByExpr(),
        0,
        5,
        table,
        [kb_id],
    )
    vector_only_ids = conn.get_doc_ids(vector_only_res)
    assert vector_only_ids == ["vec-hit", "txt-hit"]
    assert vector_only_res.chunks[0]["_score"] > vector_only_res.chunks[1]["_score"] > 0
    assert all(row["q_4_vec_valid"] is True and row["_score"] > 0 for row in vector_only_res.chunks)
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s AND indexname = %s",
            [gaussdb_env["schema"], table, conn.ddl.index_name(table, "q_4_vec_diskann")],
        )
        assert "using gsdiskann" in cur.fetchone()[0].lower()

    hybrid_res = await dealer.search(
        {"question": "risk budget", "page": 1, "size": 5, "vector_similarity_weight": 0.7},
        table,
        [kb_id],
        emb_mdl=FakeEmbeddingModel(),
    )
    assert hybrid_res.total == 2
    assert hybrid_res.ids == ["vec-hit", "txt-hit"]
    assert hybrid_res.field["vec-hit"]["_score"] > hybrid_res.field["txt-hit"]["_score"] > 0


def test_tc_ret_907_search_filter_and_aggregation_remain_available_during_batch_writes(
    gaussdb_env,
    table_name,
    gaussdb_admin_conn,
):
    from common.doc_store.doc_store_base import OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    writer_conn = GaussDBConnection()
    reader_conn = GaussDBConnection()
    kb_id = uuid.uuid4().hex
    table = table_name(gaussdb_env, "batch")
    assert writer_conn.create_idx(table, kb_id, 4) is True
    first_batch_committed = Event()
    mid_read_complete = Event()

    def writer():
        assert (
            writer_conn.insert(
                [
                    {
                        "id": "batch-1",
                        "kb_id": kb_id,
                        "doc_id": "doc-batch",
                        "title_tks": "alpha",
                        "content_with_weight": "alpha committed batch",
                        "content_ltks": "alpha committed batch",
                        "content_sm_ltks": "alpha committed batch",
                        "q_4_vec": [0.1, 0.2, 0.3, 0.4],
                    }
                ],
                table,
                kb_id,
            )
            == []
        )
        first_batch_committed.set()
        assert mid_read_complete.wait(timeout=10)
        assert (
            writer_conn.insert(
                [
                    {
                        "id": "batch-2",
                        "kb_id": kb_id,
                        "doc_id": "doc-batch",
                        "title_tks": "beta",
                        "content_with_weight": "beta committed batch",
                        "content_ltks": "beta committed batch",
                        "content_sm_ltks": "beta committed batch",
                        "q_4_vec": [0.2, 0.3, 0.4, 0.5],
                    }
                ],
                table,
                kb_id,
            )
            == []
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert first_batch_committed.wait(timeout=10)
        try:
            search_res = reader_conn.search(
                ["id", "doc_id"],
                [],
                {"doc_id": "doc-batch"},
                [],
                OrderByExpr(),
                0,
                5,
                table,
                [kb_id],
            )
            assert reader_conn.get_doc_ids(search_res) == ["batch-1"]

            with gaussdb_admin_conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT id, doc_id FROM {} WHERE kb_id = %s AND doc_id = %s ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], table)),
                    [kb_id, "doc-batch"],
                )
                assert cur.fetchall() == [("batch-1", "doc-batch")]
        finally:
            mid_read_complete.set()
        future.result(timeout=10)

    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id, doc_id FROM {} WHERE kb_id = %s ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], table)),
            [kb_id],
        )
        assert cur.fetchall() == [("batch-1", "doc-batch"), ("batch-2", "doc-batch")]
        cur.execute(
            sql.SQL("SELECT doc_id, COUNT(*) FROM {} WHERE kb_id = %s GROUP BY doc_id ORDER BY doc_id").format(sql.Identifier(gaussdb_env["schema"], table)),
            [kb_id],
        )
        expected_aggregation = [{"value": doc_id, "count": count} for doc_id, count in cur.fetchall()]

    filter_only = reader_conn.search(
        ["id", "doc_id"],
        [],
        {"doc_id": "doc-batch"},
        [],
        OrderByExpr(),
        0,
        10,
        table,
        [kb_id],
    )
    assert set(reader_conn.get_doc_ids(filter_only)) == {"batch-1", "batch-2"}

    aggregation = reader_conn.search([], [], {}, [], OrderByExpr(), 0, 0, table, [kb_id], ["doc_id"])
    assert aggregation.chunks == expected_aggregation == [{"value": "doc-batch", "count": 2}]


@pytest.mark.asyncio
async def test_tc_ret_1002_real_sql_execution_failure_falls_back_to_real_retrieval(
    gaussdb_env,
    gaussdb_admin_conn,
    ragflow_kb_context,
    register_table,
    request,
    monkeypatch,
    caplog,
):
    from api.db.db_models import DB, Document
    from api.db.services import dialog_service
    from api.db.services.document_service import DocumentService
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from common import settings
    from rag.nlp.search import Dealer
    from rag.utils.gaussdb_conn import GaussDBConnection

    tenant_id = gaussdb_env["table_prefix"]
    kb_id = ragflow_kb_context["kb_id"]
    table = register_table(f"ragflow_{tenant_id}")
    doc_id = f"doc-ret-1002-{tenant_id[:8]}"
    conn = GaussDBConnection()
    dealer = RecordingDealer(Dealer(conn))
    assert conn.create_idx(table, kb_id, 4) is True

    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {} (id, kb_id, doc_id, docnm_kwd, title_tks, content_with_weight, "
                "content_ltks, content_sm_ltks, q_4_vec, q_4_vec_valid) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::floatvector(4), TRUE)"
            ).format(sql.Identifier(gaussdb_env["schema"], table)),
            [
                "chunk-ret-1002",
                kb_id,
                doc_id,
                "fallback.txt",
                "fallback",
                "fallback-gaussdb-query",
                "fallback-gaussdb-query",
                "fallback-gaussdb-query",
                json.dumps([0.1, 0.2, 0.3, 0.4]),
            ],
        )
        cur.execute(sql.SQL("ALTER TABLE {} DROP COLUMN chunk_data").format(sql.Identifier(gaussdb_env["schema"], table)))
    gaussdb_admin_conn.commit()
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT id, doc_id, content_with_weight, q_4_vec_valid FROM {} WHERE kb_id = %s").format(sql.Identifier(gaussdb_env["schema"], table)),
            [kb_id],
        )
        assert cur.fetchall() == [("chunk-ret-1002", doc_id, "fallback-gaussdb-query", True)]
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = 'chunk_data'",
            [gaussdb_env["schema"], table],
        )
        assert cur.fetchone() == (0,)

    KnowledgebaseService.update_parser_config(kb_id, {"field_map": {"amount": "amount"}})
    DocumentService.save(
        id=doc_id,
        kb_id=kb_id,
        parser_id="naive",
        type="txt",
        created_by=tenant_id,
        name="fallback.txt",
        suffix="txt",
    )

    def cleanup_document():
        DocumentService.delete_by_id(doc_id)
        with DB.connection_context():
            assert Document.select().where(Document.id == doc_id).count() == 0

    request.addfinalizer(cleanup_document)

    invalid_at_execution = f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount FROM {table} WHERE kb_id = '{kb_id}'"
    monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", True, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_INFINITY", False, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_OCEANBASE", False, raising=False)
    monkeypatch.setattr(settings, "docStoreConn", conn, raising=False)
    monkeypatch.setattr(settings, "retriever", dealer, raising=False)
    monkeypatch.setattr(dialog_service, "KnowledgebaseService", KnowledgebaseService, raising=False)
    monkeypatch.setattr(dialog_service, "DocumentService", DocumentService, raising=False)
    monkeypatch.setattr(
        dialog_service,
        "TenantLangfuseService",
        SimpleNamespace(filter_by_tenant=lambda **_kwargs: None),
        raising=False,
    )
    monkeypatch.setattr(
        dialog_service,
        "get_tenant_default_model_by_type",
        lambda *_args, **_kwargs: {"model_type": "chat", "max_tokens": 8192},
    )
    monkeypatch.setattr(dialog_service, "tts", lambda *_args, **_kwargs: None, raising=False)

    probe_chat = ScriptedChatModel([invalid_at_execution, invalid_at_execution])
    with caplog.at_level(logging.DEBUG):
        sql_result = await dialog_service.use_sql(
            "show amount",
            {"amount": "amount"},
            tenant_id,
            probe_chat,
            quota=True,
            kb_ids=[kb_id],
        )
    assert sql_result is None
    assert len(dealer.sqls) == 2
    assert all("chunk_data" in statement for statement in dealer.sqls)
    assert "Retry SQL execution also FAILED, returning None" in caplog.text

    dealer.sqls.clear()
    chat = ScriptedChatModel([invalid_at_execution, invalid_at_execution, "fallback answer"])
    real_kbs = KnowledgebaseService.get_by_ids([kb_id])
    assert [kb.id for kb in real_kbs] == [kb_id]
    monkeypatch.setattr(
        dialog_service,
        "get_models",
        lambda *_args, **_kwargs: (
            real_kbs,
            FakeEmbeddingModel(),
            None,
            chat,
            None,
        ),
    )
    dialog = SimpleNamespace(
        tenant_id=tenant_id,
        kb_ids=[kb_id],
        prompt_config={
            "quote": True,
            "parameters": [{"key": "knowledge", "optional": False}],
            "system": "Use {knowledge}",
        },
        llm_id=None,
        tenant_llm_id=None,
        llm_setting={},
        top_n=6,
        top_k=10,
        similarity_threshold=0.0,
        vector_similarity_weight=1.0,
        meta_data_filter=None,
    )

    with caplog.at_level(logging.DEBUG):
        events = [
            item
            async for item in dialog_service.async_chat(
                dialog,
                [{"role": "user", "content": "fallback-gaussdb-query"}],
                stream=False,
            )
        ]

    assert len(dealer.sqls) == 2
    assert len(dealer.retrieval_calls) == 1
    assert events[-1]["answer"] == "fallback answer [ID:0]"
    assert [(chunk["chunk_id"], chunk["doc_id"], chunk["kb_id"]) for chunk in events[-1]["reference"]["chunks"]] == [("chunk-ret-1002", doc_id, kb_id)]
    assert events[-1]["reference"]["doc_aggs"] == [{"doc_id": doc_id, "doc_name": "fallback.txt", "count": 1}]
    assert "SQL failed or returned no results, falling back to vector search" in caplog.text
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {} WHERE kb_id = %s AND doc_id = %s").format(sql.Identifier(gaussdb_env["schema"], table)),
            [kb_id, doc_id],
        )
        assert cur.fetchone() == (1,)
