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
import time

import pytest
import uuid


class FakeEmbeddingModel:
    def encode_queries(self, _text):
        return [0.1, 0.2, 0.3, 0.4], 4


@pytest.mark.asyncio
async def test_dealer_search_fulltext_vector_and_hybrid_full_chain(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.nlp.search import Dealer
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    dealer = Dealer(conn)
    kb_id = uuid.uuid4().hex
    table = table_name(gaussdb_env)
    conn.create_idx(table, kb_id, 4)
    assert (
        conn.insert(
            [
                {
                    "id": "txt-hit",
                    "kb_id": kb_id,
                    "doc_id": "doc-a",
                    "title_tks": "contract",
                    "content_with_weight": "risk contract audit",
                    "content_ltks": "risk contract audit",
                    "content_sm_ltks": "risk contract audit",
                    "q_4_vec": [0.01, 0.01, 0.01, 0.01],
                },
                {
                    "id": "vec-hit",
                    "kb_id": kb_id,
                    "doc_id": "doc-b",
                    "title_tks": "budget",
                    "content_with_weight": "budget memo",
                    "content_ltks": "budget memo",
                    "content_sm_ltks": "budget memo",
                    "q_4_vec": [0.1, 0.2, 0.3, 0.4],
                },
                {
                    "id": "placeholder",
                    "kb_id": kb_id,
                    "doc_id": "doc-c",
                    "title_tks": "risk",
                    "content_with_weight": "risk placeholder",
                    "content_ltks": "risk placeholder",
                    "content_sm_ltks": "risk placeholder",
                },
            ],
            table,
            kb_id,
        )
        == []
    )

    text_res = await dealer.search(
        {"question": "risk contract", "page": 1, "size": 5},
        table,
        [kb_id],
        emb_mdl=None,
        highlight=True,
    )
    assert "txt-hit" in text_res.ids

    vector_only_res = conn.search(
        ["id", "content_with_weight", "doc_id"],
        [],
        {},
        [MatchDenseExpr("q_4_vec", [0.1, 0.2, 0.3, 0.4], "float", "cosine", 10)],
        OrderByExpr(),
        0,
        5,
        table,
        [kb_id],
        gaussdb_search_params={"vector_similarity_weight": 1.0},
    )
    vector_only_ids = conn.get_doc_ids(vector_only_res)
    assert "vec-hit" in vector_only_ids
    assert "placeholder" not in vector_only_ids

    hybrid_res = await dealer.search(
        {"question": "risk budget", "page": 1, "size": 5, "vector_similarity_weight": 0.7},
        table,
        [kb_id],
        emb_mdl=FakeEmbeddingModel(),
    )
    assert hybrid_res.total >= 1
    assert set(hybrid_res.ids).issubset({"txt-hit", "vec-hit"})


def test_search_sql_filter_and_aggregation_remain_available_during_batch_writes(gaussdb_env, table_name):
    from common.doc_store.doc_store_base import OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    writer_conn = GaussDBConnection()
    reader_conn = GaussDBConnection()
    kb_id = uuid.uuid4().hex
    table = table_name(gaussdb_env, "batch")
    writer_conn.create_idx(table, kb_id, 4)
    first_batch_committed = Event()

    def writer():
        assert writer_conn.insert(
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
        ) == []
        first_batch_committed.set()
        time.sleep(0.2)
        assert writer_conn.insert(
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
        ) == []

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert first_batch_committed.wait(timeout=10)

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
        assert "batch-1" in reader_conn.get_doc_ids(search_res)

        sql_res = reader_conn.sql(f"SELECT doc_id FROM {table} WHERE kb_id = '{kb_id}' AND doc_id = 'doc-batch' ORDER BY doc_id")
        assert ["doc-batch"] in sql_res["rows"]

        future.result(timeout=10)

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
    assert aggregation.chunks == [{"value": "doc-batch", "count": 2}]
