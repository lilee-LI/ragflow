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
