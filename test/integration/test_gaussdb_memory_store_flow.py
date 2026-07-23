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
import os
import uuid

import pytest
from psycopg2 import sql

from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchTextExpr, OrderByExpr


@pytest.fixture
def memory_gaussdb_env(monkeypatch):
    if os.getenv("GAUSSDB_INTEGRATION") != "1":
        pytest.skip("set GAUSSDB_INTEGRATION=1 to run live GaussDB Memory Store tests")

    from common import settings

    schema = os.getenv("GAUSSDB_SCHEMA")
    if not schema:
        pytest.fail("set GAUSSDB_SCHEMA to the pre-created integration schema")

    monkeypatch.setenv("DOC_ENGINE", "gaussdb")
    monkeypatch.setattr(settings, "DOC_ENGINE", "gaussdb", raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", True, raising=False)
    monkeypatch.setattr(
        settings,
        "GAUSSDB",
        {
            "config": {
                "host": os.environ["GAUSSDB_HOST"],
                "port": os.environ.get("GAUSSDB_PORT", "19995"),
                "database": os.environ["GAUSSDB_DATABASE"],
                "user": os.environ["GAUSSDB_USER"],
                "password": os.environ["GAUSSDB_PASSWORD"],
                "schema": schema,
            }
        },
        raising=False,
    )
    yield {"schema": schema, "table_prefix": uuid.uuid4().hex}


def _message(
    memory_id: str,
    message_id: int,
    *,
    message_type: str = "raw",
    source_id: int = 0,
    user_id: str = "user-a",
    agent_id: str = "agent-a",
    session_id: str = "session-a",
    zone_id: int = 0,
    content: str = "contract risk memory",
    vector: list[float] | None = None,
    valid_at: str | None = None,
    invalid_at: str | None = None,
    forget_at: str | None = None,
    status: bool = True,
):
    return {
        "message_id": message_id,
        "message_type": message_type,
        "source_id": source_id,
        "memory_id": memory_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "session_id": session_id,
        "zone_id": zone_id,
        "content": content,
        "valid_at": valid_at or f"2026-06-29 12:{message_id:02d}:00",
        "invalid_at": invalid_at,
        "forget_at": forget_at,
        "status": status,
        "content_embed": vector or [0.1, 0.2, 0.3],
    }


@pytest.fixture
def memory_conn(memory_gaussdb_env):
    from memory.utils.gaussdb_conn import GaussDBMemoryConnection

    conn = GaussDBMemoryConnection()
    try:
        yield conn
    finally:
        conn.pool.close_all()


@pytest.fixture
def memory_index_factory(memory_gaussdb_env, memory_conn):
    created: list[tuple[str, str]] = []

    def factory(suffix: str = "tenant", memory_id: str = "cleanup") -> str:
        index_name = f"memory_{memory_gaussdb_env['table_prefix']}_{suffix}_{uuid.uuid4().hex[:8]}"
        created.append((index_name, memory_id))
        return index_name

    try:
        yield factory
    finally:
        for index_name, memory_id in reversed(created):
            try:
                memory_conn.delete_idx(index_name, memory_id)
            except Exception:
                pass


def _catalog_columns(admin_conn, schema: str, table: str) -> set[str]:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
            """,
            [schema, table],
        )
        return {row[0] for row in cur.fetchall()}


def _catalog_index_defs(admin_conn, schema: str, table: str) -> str:
    with admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = %s
               AND tablename = %s
            """,
            [schema, table],
        )
        return "\n".join(row[0] for row in cur.fetchall()).lower()


def _row_flags(admin_conn, schema: str, table: str, doc_id: str):
    with admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT q_3_vec_empty, q_5_vec_empty FROM {} WHERE id = %s").format(sql.Identifier(schema, table)),
            [doc_id],
        )
        return cur.fetchone()


def _ids(conn, res, fields=None):
    fields = fields or ["message_id"]
    return {doc["message_id"] for doc in conn.get_fields(res, fields).values()}


def test_memory_lifecycle_catalog_and_helper_interfaces(
    memory_gaussdb_env,
    gaussdb_admin_conn,
    memory_conn,
    memory_index_factory,
):
    from memory.utils.gaussdb_conn import SearchResult

    index_name = memory_index_factory("catalog", "mem-catalog")
    table = memory_conn.physical_table(index_name)

    assert memory_conn.db_type() == "gaussdb"
    health = memory_conn.health()
    assert health["status"] == "healthy"
    assert health["sql_compatibility"] in {"A", "ORA"}
    assert table.startswith("ragflow_mem_")
    assert "mem-catalog" not in table
    assert memory_conn.index_exist(index_name, "mem-catalog") is False

    assert memory_conn.create_idx(index_name, "mem-catalog", 3) is True
    assert memory_conn.index_exist(index_name, "mem-catalog") is True
    assert memory_conn.index_exist(index_name, "another-memory") is True

    columns = _catalog_columns(gaussdb_admin_conn, memory_gaussdb_env["schema"], table)
    assert {
        "id",
        "message_id",
        "message_type_kwd",
        "source_id",
        "memory_id",
        "user_id",
        "agent_id",
        "session_id",
        "zone_id",
        "valid_at",
        "invalid_at",
        "forget_at",
        "status_int",
        "content_ltks",
        "tokenized_content_ltks",
        "q_3_vec",
        "q_3_vec_empty",
    }.issubset(columns)

    index_defs = _catalog_index_defs(gaussdb_admin_conn, memory_gaussdb_env["schema"], table)
    assert "using ugin" in index_defs
    assert "to_tsvector('simple'" in index_defs
    assert "using gsdiskann" in index_defs
    assert "q_3_vec" in index_defs
    assert "q_3_vec_empty" in index_defs

    result = SearchResult(total=1, messages=[{"id": "doc-1"}, {"id": ""}])
    assert memory_conn.get_total(result) == 1
    assert memory_conn.get_doc_ids(result) == ["doc-1"]
    assert memory_conn.sql("SELECT 1") is None

    assert memory_conn.delete_idx(index_name, "mem-catalog") is True
    assert memory_conn.delete_idx(index_name, "mem-catalog") is True
    assert memory_conn.index_exist(index_name, "mem-catalog") is False


def test_memory_insert_get_update_and_all_business_columns(
    memory_gaussdb_env,
    gaussdb_admin_conn,
    memory_conn,
    memory_index_factory,
):
    index_name = memory_index_factory("columns", "mem-columns")
    memory_id = "mem-columns"
    memory_conn.create_idx(index_name, memory_id, 3)

    errors = memory_conn.insert(
        [
            _message(
                memory_id,
                1,
                source_id=0,
                user_id="",
                zone_id=7,
                content="raw contract memory",
                invalid_at=None,
            ),
            _message(
                memory_id,
                2,
                message_type="semantic",
                source_id=1,
                user_id="user-b",
                agent_id="agent-b",
                session_id="session-b",
                zone_id=8,
                content="semantic risk memory",
                invalid_at="2026-07-01 00:00:00",
            ),
        ],
        index_name,
        memory_id,
    )
    assert errors == []

    raw = memory_conn.get(f"{memory_id}_1", index_name, [memory_id])
    assert raw["message_id"] == 1
    assert raw["message_type"] == "raw"
    assert raw["source_id"] == 0
    assert raw["user_id"] == ""
    assert raw["zone_id"] == 7
    assert raw["invalid_at"] == "-"
    assert raw["content_embed"] == pytest.approx([0.1, 0.2, 0.3])

    order = OrderByExpr().asc("message_id")
    res, total = memory_conn.search(
        [
            "message_id",
            "message_type",
            "source_id",
            "memory_id",
            "user_id",
            "agent_id",
            "session_id",
            "zone_id",
            "valid_at",
            "invalid_at",
            "forget_at",
            "status",
            "content",
            "content_embed",
        ],
        [],
        {},
        [],
        order,
        0,
        10,
        index_name,
        [memory_id],
        hide_forgotten=False,
    )
    fields = memory_conn.get_fields(
        res,
        [
            "message_id",
            "message_type",
            "source_id",
            "memory_id",
            "user_id",
            "agent_id",
            "session_id",
            "zone_id",
            "valid_at",
            "invalid_at",
            "forget_at",
            "status",
            "content",
            "content_embed",
        ],
    )
    assert total == 2
    assert fields[f"{memory_id}_1"]["status"] is True
    assert fields[f"{memory_id}_2"]["message_type"] == "semantic"
    assert fields[f"{memory_id}_2"]["invalid_at"] == "2026-07-01 00:00:00"

    assert memory_conn.update({"message_id": 1}, {"content": "updated contract memory", "status": False}, index_name, memory_id)
    updated = memory_conn.get(f"{memory_id}_1", index_name, [memory_id])
    assert updated["content"] == "updated contract memory"
    assert updated["status"] is False

    assert memory_conn.update({"message_id": 1}, {"content_embed": [0.1, 0.2, 0.3, 0.4, 0.5]}, index_name, memory_id)
    updated = memory_conn.get(f"{memory_id}_1", index_name, [memory_id])
    assert updated["content_embed"] == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])

    table = memory_conn.physical_table(index_name)
    assert _row_flags(gaussdb_admin_conn, memory_gaussdb_env["schema"], table, f"{memory_id}_1") == (True, False)


def test_memory_filter_search_covers_conditions_ordering_and_hide_forgotten(
    memory_conn,
    memory_index_factory,
):
    index_name = memory_index_factory("filters", "mem-filter-a")
    mem_a = "mem-filter-a"
    mem_b = "mem-filter-b"
    memory_conn.create_idx(index_name, mem_a, 3)
    assert (
        memory_conn.insert(
            [
                _message(mem_a, 10, message_type="raw", source_id=0, user_id="user-a", agent_id="agent-a", session_id="session-a", content="raw a"),
                _message(mem_a, 11, message_type="semantic", source_id=10, user_id="user-a", agent_id="agent-a", session_id="session-a", content="extract a"),
                _message(mem_a, 12, message_type="raw", source_id=0, user_id="user-b", agent_id="agent-b", session_id="session-b", content="raw b", status=False),
                _message(mem_a, 13, message_type="raw", source_id=0, user_id="user-a", agent_id="agent-a", session_id="session-a", content="forgotten", forget_at="2026-06-29 12:59:00"),
                _message(mem_b, 20, message_type="raw", source_id=0, user_id="user-a", agent_id="agent-a", session_id="session-a", content="other memory"),
            ],
            index_name,
            mem_a,
        )
        == []
    )

    desc = OrderByExpr().desc("valid_at")
    res, total = memory_conn.search(
        ["message_id", "memory_id", "content"],
        [],
        {},
        [],
        desc,
        0,
        20,
        index_name,
        [mem_a],
        hide_forgotten=False,
    )
    assert total == 4
    assert _ids(memory_conn, res) == {10, 11, 12, 13}

    res, _total = memory_conn.search(
        ["message_id"],
        [],
        {"message_type": "raw", "agent_id": ["agent-a"], "session_id": "session-a", "user_id": "user-a", "status": 1},
        [],
        desc,
        0,
        20,
        index_name,
        [mem_a],
        hide_forgotten=True,
    )
    assert _ids(memory_conn, res) == {10}

    res, _total = memory_conn.search(["message_id"], [], {"source_id": [10]}, [], desc, 0, 20, index_name, [mem_a], hide_forgotten=True)
    assert _ids(memory_conn, res) == {11}

    res, _total = memory_conn.search(["message_id"], [], {"message_id": [10, 12]}, [], desc, 0, 20, index_name, [mem_a], hide_forgotten=False)
    assert _ids(memory_conn, res) == {10, 12}

    res, _total = memory_conn.search(["message_id"], [], {"id": [f"{mem_a}_10", f"{mem_a}_11"]}, [], desc, 0, 20, index_name, [mem_a], hide_forgotten=False)
    assert _ids(memory_conn, res) == {10, 11}

    res, _total = memory_conn.search(["message_id"], [], {"exists": "forget_at"}, [], desc, 0, 20, index_name, [mem_a], hide_forgotten=False)
    assert _ids(memory_conn, res) == {13}

    res, _total = memory_conn.search(["message_id"], [], {"must_not": {"exists": "forget_at"}}, [], desc, 0, 20, index_name, [mem_a], hide_forgotten=False)
    assert _ids(memory_conn, res) == {10, 11, 12}

    res, _total = memory_conn.search(["message_id"], [], {"agent_id": ""}, [], OrderByExpr().asc("message_id"), 0, 20, index_name, [mem_a], hide_forgotten=False)
    assert list(memory_conn.get_fields(res, ["message_id"]).values())[0]["message_id"] == 10


def test_memory_text_vector_fusion_search_scores_and_result_helpers(
    memory_conn,
    memory_index_factory,
):
    index_name = memory_index_factory("expressions", "mem-expr")
    memory_id = "mem-expr"
    memory_conn.create_idx(index_name, memory_id, 3)
    assert (
        memory_conn.insert(
            [
                _message(memory_id, 1, content="contract risk audit", vector=[0.1, 0.2, 0.3]),
                _message(memory_id, 2, content="budget plan", vector=[0.9, 0.1, 0.1]),
                _message(memory_id, 3, content="contract archived", vector=[0.1, 0.2, 0.3]),
            ],
            index_name,
            memory_id,
        )
        == []
    )
    assert memory_conn.update({"message_id": 3}, {"content_embed": [0.1, 0.2, 0.3, 0.4, 0.5]}, index_name, memory_id)

    order = OrderByExpr().desc("valid_at")
    text = MatchTextExpr(["content_ltks"], "ignored", 10, {"original_query": "contract"})
    dense = MatchDenseExpr("q_3_vec", [0.1, 0.2, 0.3], "float", "cosine", 10, {"similarity": 0.99})
    fusion = FusionExpr("weighted_sum", 10, {"weights": "0.3,0.7"})

    text_res, text_total = memory_conn.search(["message_id", "message_type", "content"], [], {}, [text], order, 0, 10, index_name, [memory_id])
    assert text_total == 2
    assert _ids(memory_conn, text_res, ["message_id", "content"]) == {1, 3}

    vector_res, vector_total = memory_conn.search(["message_id", "content"], [], {}, [dense], order, 0, 10, index_name, [memory_id])
    assert vector_total == 1
    assert _ids(memory_conn, vector_res, ["message_id", "content"]) == {1}
    assert memory_conn.get_total(vector_res) == 1
    assert memory_conn.get_doc_ids(vector_res) == [f"{memory_id}_1"]

    fusion_res, fusion_total = memory_conn.search(["message_id", "content"], [], {}, [text, dense, fusion], order, 0, 10, index_name, [memory_id])
    assert fusion_total == 1
    assert _ids(memory_conn, fusion_res, ["message_id", "content"]) == {1}

    highlight = memory_conn.get_highlight(text_res, ["contract"], "content_ltks")
    assert f"{memory_id}_1" in highlight
    assert "<em>contract</em>" in highlight[f"{memory_id}_1"]

    aggregation = memory_conn.get_aggregation(text_res, "message_type_kwd")
    assert aggregation == [("raw", 2)]


def test_memory_maintenance_interfaces_missing_forgotten_and_delete(
    memory_conn,
    memory_index_factory,
):
    index_name = memory_index_factory("maintenance", "mem-maint")
    memory_id = "mem-maint"
    memory_conn.create_idx(index_name, memory_id, 3)
    assert (
        memory_conn.insert(
            [
                _message(memory_id, 1, content="needs tokenized refresh"),
                _message(memory_id, 2, content="will be forgotten"),
            ],
            index_name,
            memory_id,
        )
        == []
    )

    assert memory_conn.update({"message_id": 1}, {"remove": "tokenized_content_ltks"}, index_name, memory_id)
    missing = memory_conn.get_missing_field_message(["message_id", "content"], index_name, memory_id, "tokenized_content_ltks")
    assert _ids(memory_conn, missing, ["message_id", "content"]) == {1}

    assert memory_conn.update({"message_id": 2}, {"forget_at": "2026-06-29 13:00:00"}, index_name, memory_id)
    forgotten = memory_conn.get_forgotten_messages(["message_id", "content", "content_embed"], index_name, memory_id)
    assert _ids(memory_conn, forgotten, ["message_id", "content", "content_embed"]) == {2}

    assert memory_conn.delete({"message_id": []}, index_name, memory_id) == 0
    assert memory_conn.delete({"message_id": [2]}, index_name, memory_id) == 1
    assert memory_conn.delete({"id": [f"{memory_id}_1"]}, index_name, memory_id) == 1

    res, total = memory_conn.search(["message_id"], [], {}, [], OrderByExpr(), 0, 10, index_name, [memory_id], hide_forgotten=False)
    assert total == 0
    assert memory_conn.get_fields(res, ["message_id"]) == {}


def test_memory_multi_table_fanout_and_user_behavior_sequence(
    memory_conn,
    memory_index_factory,
):
    index_a = memory_index_factory("tenant_a", "mem-a")
    index_b = memory_index_factory("tenant_b", "mem-b")
    mem_a = "mem-user-a"
    mem_a_other = "mem-user-a-other"
    mem_b = "mem-user-b"
    memory_conn.create_idx(index_a, mem_a, 3)
    memory_conn.create_idx(index_b, mem_b, 3)

    assert (
        memory_conn.insert(
            [
                _message(mem_a, 1, message_type="raw", source_id=0, content="user asked about contract"),
                _message(mem_a, 2, message_type="semantic", source_id=1, content="contract preference"),
                _message(mem_a_other, 3, message_type="raw", source_id=0, content="same tenant other memory"),
            ],
            index_a,
            mem_a,
        )
        == []
    )
    assert (
        memory_conn.insert(
            [_message(mem_b, 4, message_type="raw", source_id=0, content="remote tenant contract")],
            index_b,
            mem_b,
        )
        == []
    )

    order = OrderByExpr().desc("valid_at")
    raw_res, raw_total = memory_conn.search(
        ["message_id", "message_type", "source_id", "memory_id", "content"],
        [],
        {"message_type": "raw"},
        [],
        order,
        0,
        20,
        index_a,
        [mem_a],
        hide_forgotten=False,
    )
    assert raw_total == 1
    assert _ids(memory_conn, raw_res, ["message_id", "message_type", "source_id", "memory_id", "content"]) == {1}

    extracted_res, extracted_total = memory_conn.search(
        ["message_id", "message_type", "source_id", "memory_id", "content"],
        [],
        {"source_id": [1]},
        [],
        order,
        0,
        20,
        index_a,
        [mem_a],
        hide_forgotten=False,
    )
    assert extracted_total == 1
    assert _ids(memory_conn, extracted_res, ["message_id", "message_type", "source_id", "memory_id", "content"]) == {2}

    fanout_res, fanout_total = memory_conn.search(
        ["message_id", "memory_id", "content", "content_embed"],
        [],
        {"status": 1},
        [MatchTextExpr(["content_ltks"], "ignored", 10, {"original_query": "contract"})],
        order,
        0,
        20,
        [index_a, index_b],
        [mem_a, mem_b],
        hide_forgotten=True,
    )
    assert fanout_total == 3
    fanout_fields = memory_conn.get_fields(fanout_res, ["message_id", "memory_id", "content", "content_embed"])
    assert {doc["memory_id"] for doc in fanout_fields.values()} == {mem_a, mem_b}
    assert all(doc["content_embed"] for doc in fanout_fields.values())

    capacity_res, capacity_total = memory_conn.search(
        ["memory_id", "content", "content_embed"],
        [],
        {},
        [],
        OrderByExpr().asc("message_id"),
        0,
        20,
        [index_a, index_b],
        [mem_a, mem_b],
        hide_forgotten=False,
    )
    assert capacity_total == 3
    capacity_docs = memory_conn.get_fields(capacity_res, ["memory_id", "content", "content_embed"])
    assert sum(len(doc["content_embed"]) for doc in capacity_docs.values()) == 9

    assert memory_conn.delete({"memory_id": mem_a}, index_a, mem_a) == 2
    remaining_res, remaining_total = memory_conn.search(["message_id", "memory_id"], [], {}, [], order, 0, 20, index_a, [mem_a_other], hide_forgotten=False)
    assert remaining_total == 1
    assert _ids(memory_conn, remaining_res, ["message_id", "memory_id"]) == {3}
