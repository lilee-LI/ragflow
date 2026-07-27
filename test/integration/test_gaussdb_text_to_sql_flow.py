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
import json
import os

import psycopg2
import pytest
from psycopg2 import sql


pytestmark = [pytest.mark.gaussdb_integration, pytest.mark.gaussdb_both]


class FakeSQLChatModel:
    def __init__(self, sql):
        self.sql = sql
        self.calls = []

    async def async_chat(self, sys_prompt, messages, params):
        self.calls.append((sys_prompt, messages, params))
        return self.sql


class RecordingDealer:
    def __init__(self, dealer):
        self._dealer = dealer
        self.sqls = []

    def __getattr__(self, name):
        return getattr(self._dealer, name)

    def sql_retrieval(self, statement, fetch_size=128, format="json"):
        self.sqls.append(statement)
        return self._dealer.sql_retrieval(statement, fetch_size=fetch_size, format=format)


def _seed_rows_independently(schema, table, rows):
    connection = psycopg2.connect(
        host=os.environ["GAUSSDB_HOST"],
        port=int(os.environ.get("GAUSSDB_PORT", "19995")),
        dbname=os.environ["GAUSSDB_DATABASE"],
        user=os.environ["GAUSSDB_USER"],
        password=os.environ["GAUSSDB_PASSWORD"],
        options="-c default_transaction_read_only=off",
    )
    try:
        with connection.cursor() as cur:
            for row in rows:
                cur.execute(
                    sql.SQL(
                        "INSERT INTO {}.{} "
                        "(id, kb_id, doc_id, docnm_kwd, content_ltks, chunk_data, q_4_vec, q_4_vec_valid) "
                        "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::floatvector(4), TRUE)"
                    ).format(sql.Identifier(schema), sql.Identifier(table)),
                    [
                        row["id"],
                        row["kb_id"],
                        row["doc_id"],
                        row["docnm_kwd"],
                        row["content_ltks"],
                        json.dumps(row["chunk_data"], ensure_ascii=False),
                        "[" + ",".join(str(value) for value in row["q_4_vec"]) + "]",
                    ],
                )
        connection.commit()
        with connection.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT id, kb_id FROM {}.{} ORDER BY id").format(sql.Identifier(schema), sql.Identifier(table))
            )
            assert cur.fetchall() == sorted((row["id"], row["kb_id"]) for row in rows)
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_tc_sql_1007_text_to_sql_use_sql_to_gaussdb_adapter_full_chain(gaussdb_env, register_table, monkeypatch):
    from common import settings
    from common.doc_store.gaussdb_conn_base import UnsafeGaussDBSQL
    from rag.nlp.search import Dealer
    from rag.utils.gaussdb_conn import GaussDBConnection
    from api.db.services import dialog_service

    conn = GaussDBConnection()
    tenant_id = gaussdb_env["table_prefix"]
    kb_id = "abcdefabcdefabcdefabcdefabcdefab"
    table = register_table(f"ragflow_{tenant_id}")
    monkeypatch.setattr(settings, "docStoreConn", conn, raising=False)
    dealer = RecordingDealer(Dealer(conn))
    monkeypatch.setattr(settings, "retriever", dealer, raising=False)
    assert conn.create_idx(table, kb_id, 4) is True
    _seed_rows_independently(
        gaussdb_env["schema"],
        table,
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
    )

    chat = FakeSQLChatModel(
        f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount "
        f"FROM {table} "
        f"WHERE kb_id = '{kb_id}' "
        f"AND chunk_data #>> '{{dept}}' = 'finance' "
        f"AND (chunk_data #>> '{{amount}}')::DOUBLE PRECISION > 100"
    )
    result = await dialog_service.use_sql(
        "finance amount greater than 100",
        {"amount": "amount", "dept": "dept"},
        tenant_id,
        chat,
        quota=False,
        kb_ids=[kb_id],
    )

    assert result["answer"] == "|amount|Source|\n|------|------|\n|120| ##0$$|"
    assert result["reference"]["chunks"] == [{"doc_id": "doc1", "docnm_kwd": "finance.csv", "kb_id": kb_id}]
    assert result["reference"]["doc_aggs"] == [{"doc_id": "doc1", "doc_name": "finance.csv", "count": 1}]
    assert len(dealer.sqls) == 1
    assert f"kb_id = '{kb_id}'" in dealer.sqls[0]
    assert "LIMIT 128" in dealer.sqls[0].upper()
    assert any("chunk_data #>> '{amount}'" in call[0] for call in chat.calls)
    assert "json_extract_string(" not in chat.sql
    assert any("Do not use json_extract" in call[0] for call in chat.calls)

    with pytest.raises(UnsafeGaussDBSQL):
        conn.sql(f"DELETE FROM {table} WHERE kb_id = '{kb_id}'")


@pytest.mark.asyncio
async def test_tc_sql_801_text_to_sql_multikb_reference_completes_kb_id_in_live_gaussdb(
    gaussdb_env,
    register_table,
    monkeypatch,
):
    from common import settings
    from rag.nlp.search import Dealer
    from rag.utils.gaussdb_conn import GaussDBConnection
    from api.db.services import dialog_service

    conn = GaussDBConnection()
    tenant_id = gaussdb_env["table_prefix"]
    kb1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    kb2 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    table = register_table(f"ragflow_{tenant_id}")
    monkeypatch.setattr(settings, "docStoreConn", conn, raising=False)
    dealer = RecordingDealer(Dealer(conn))
    monkeypatch.setattr(settings, "retriever", dealer, raising=False)
    assert conn.create_idx(table, kb1, 4) is True
    _seed_rows_independently(
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "row-kb1-d1",
                "kb_id": kb1,
                "doc_id": "d1",
                "docnm_kwd": "doc.xlsx",
                "content_ltks": "amount one hundred",
                "chunk_data": {"amount": 100},
                "q_4_vec": [1, 0, 0, 0],
            },
            {
                "id": "row-kb1-d2",
                "kb_id": kb1,
                "doc_id": "d2",
                "docnm_kwd": "doc.xlsx",
                "content_ltks": "amount two hundred",
                "chunk_data": {"amount": 200},
                "q_4_vec": [0, 1, 0, 0],
            },
            {
                "id": "row-kb2-d3",
                "kb_id": kb2,
                "doc_id": "d3",
                "docnm_kwd": "doc.xlsx",
                "content_ltks": "amount three hundred",
                "chunk_data": {"amount": 300},
                "q_4_vec": [0, 0, 1, 0],
            },
        ],
    )

    chat = FakeSQLChatModel(
        f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount "
        f"FROM {table} "
        "ORDER BY doc_id"
    )
    result = await dialog_service.use_sql(
        "show all amounts",
        {"amount": "amount"},
        tenant_id,
        chat,
        quota=False,
        kb_ids=[kb1, kb2],
    )

    assert result["answer"] == "|amount|Source|\n|------|------|\n|100| ##0$$|\n|200| ##1$$|\n|300| ##2$$|"
    assert result["reference"]["chunks"] == [
        {"doc_id": "d1", "docnm_kwd": "doc.xlsx", "kb_id": kb1},
        {"doc_id": "d2", "docnm_kwd": "doc.xlsx", "kb_id": kb1},
        {"doc_id": "d3", "docnm_kwd": "doc.xlsx", "kb_id": kb2},
    ]
    assert len(dealer.sqls) == 2
    assert "SELECT doc_id, kb_id" in dealer.sqls[1]
    assert all(doc_id in dealer.sqls[1] for doc_id in ("d1", "d2", "d3"))
    assert all(kb_id in dealer.sqls[1] for kb_id in (kb1, kb2))
    assert "LIMIT 128" in dealer.sqls[1].upper()
