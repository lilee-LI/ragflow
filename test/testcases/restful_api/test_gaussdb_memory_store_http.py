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
import json
import time
import uuid
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import psycopg2
from psycopg2 import sql


@pytest.fixture(scope="session", autouse=True)
def set_tenant_info():
    # This suite provisions its own local OpenAI-compatible embedding model.
    # The global REST conftest initializes external ZHIPU/SILICONFLOW models,
    # which is unrelated to GaussDB Memory Store and would make the live test
    # depend on third-party API keys.
    return None


def _require_gaussdb_memory_http(rest_client) -> None:
    if os.getenv("GAUSSDB_MEMORY_HTTP") != "1":
        pytest.skip("set GAUSSDB_MEMORY_HTTP=1 to run live GaussDB Memory Store HTTP tests")

    engine = (os.getenv("DOC_ENGINE") or "").strip().lower()
    if not engine:
        status = rest_client.get("/system/status")
        if status.status_code == 200:
            payload = status.json()
            engine = str(payload.get("data", {}).get("doc_engine", {}).get("type", "")).lower()

    if engine != "gaussdb":
        pytest.skip(f"live service DOC_ENGINE is {engine or 'unknown'}, not gaussdb")


class _GaussDBMemoryInspector:
    def __init__(self):
        missing = [name for name in ("GAUSSDB_HOST", "GAUSSDB_DATABASE", "GAUSSDB_USER", "GAUSSDB_PASSWORD", "GAUSSDB_SCHEMA") if not os.getenv(name)]
        if missing:
            pytest.skip(f"set {', '.join(missing)} to inspect live GaussDB Memory Store rows")
        self.schema = os.environ["GAUSSDB_SCHEMA"]
        self.conn = psycopg2.connect(
            host=os.environ["GAUSSDB_HOST"],
            port=int(os.environ.get("GAUSSDB_PORT", "19995")),
            dbname=os.environ["GAUSSDB_DATABASE"],
            user=os.environ["GAUSSDB_USER"],
            password=os.environ["GAUSSDB_PASSWORD"],
            options=f"-c search_path={self.schema},public",
        )

    def close(self):
        self.conn.close()

    def _candidate_tables(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                  FROM information_schema.columns
                 WHERE table_schema = %s
                   AND table_name LIKE 'ragflow_mem_%%'
                   AND column_name = 'memory_id'
                 ORDER BY table_name
                """,
                [self.schema],
            )
            return [row[0] for row in cur.fetchall()]

    def find_table(self, memory_id: str) -> str:
        for table in self._candidate_tables():
            with self.conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT COUNT(*) FROM {} WHERE memory_id = %s").format(sql.Identifier(self.schema, table)),
                    [memory_id],
                )
                if int(cur.fetchone()[0]) > 0:
                    return table
        pytest.fail(f"Could not find GaussDB memory table containing memory_id={memory_id}")

    def rows_for_memory(self, memory_id: str, table: str | None = None) -> tuple[str, list[dict]]:
        table = table or self.find_table(memory_id)
        with self.conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT * FROM {} WHERE memory_id = %s ORDER BY message_id").format(sql.Identifier(self.schema, table)),
                [memory_id],
            )
            columns = [desc[0] for desc in cur.description]
            return table, [dict(zip(columns, row)) for row in cur.fetchall()]

    def all_rows_for_memory(self, memory_id: str) -> list[dict]:
        rows: list[dict] = []
        for table in self._candidate_tables():
            _table, table_rows = self.rows_for_memory(memory_id, table)
            rows.extend(table_rows)
        return rows

    def index_defs(self, table: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexdef
                  FROM pg_indexes
                 WHERE schemaname = %s
                   AND tablename = %s
                """,
                [self.schema, table],
            )
            return "\n".join(row[0] for row in cur.fetchall()).lower()


class _FakeEmbeddingHandler(BaseHTTPRequestHandler):
    server_version = "RAGFlowFakeEmbedding/1.0"

    def log_message(self, _format, *_args):
        return

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/embeddings":
            self._send_json({"error": {"message": "not found"}}, status=404)
            return
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        inputs = payload.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        data = [
            {
                "object": "embedding",
                "index": idx,
                "embedding": _deterministic_embedding(str(text)),
            }
            for idx, text in enumerate(inputs)
        ]
        self._send_json(
            {
                "object": "list",
                "data": data,
                "model": payload.get("model", "ragflow-memory-test-embed"),
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            }
        )

    def _send_json(self, payload: dict, status: int = 200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _deterministic_embedding(text: str) -> list[float]:
    seed = sum(ord(ch) for ch in text) or 1
    return [
        round(((seed % 97) + 1) / 100.0, 6),
        round((((seed // 3) % 89) + 1) / 100.0, 6),
        round((((seed // 7) % 83) + 1) / 100.0, 6),
    ]


@pytest.fixture(scope="session")
def fake_embedding_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeEmbeddingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def gaussdb_memory_inspector():
    if os.getenv("GAUSSDB_MEMORY_HTTP") != "1":
        pytest.skip("set GAUSSDB_MEMORY_HTTP=1 to inspect live GaussDB Memory Store rows")
    inspector = _GaussDBMemoryInspector()
    try:
        yield inspector
    finally:
        inspector.close()


@pytest.fixture
def gaussdb_memory_embedding_model(rest_client, fake_embedding_server):
    _require_gaussdb_memory_http(rest_client)
    instance_name = f"gaussdb-memory-http-{uuid.uuid4().hex[:8]}"
    model_name = f"ragflow-memory-test-embed-{uuid.uuid4().hex[:8]}"

    add_provider = rest_client.put("/providers", json={"provider_name": "OpenAI-API-Compatible"}, timeout=60)
    assert add_provider.status_code == 200, add_provider.text
    add_provider_payload = add_provider.json()
    assert add_provider_payload["code"] == 0 or "already" in add_provider_payload.get("message", "").lower(), add_provider_payload

    create_instance = rest_client.post(
        "/providers/OpenAI-API-Compatible/instances",
        json={
            "instance_name": instance_name,
            "api_key": "fake-key",
            "base_url": fake_embedding_server,
            "model_info": [
                {
                    "model_name": model_name,
                    "model_type": ["embedding"],
                    "max_tokens": 8192,
                }
            ],
        },
        timeout=120,
    )
    assert create_instance.status_code == 200, create_instance.text
    create_instance_payload = create_instance.json()
    assert create_instance_payload["code"] == 0, create_instance_payload

    return {
        "embedding_id": f"{model_name}@{instance_name}@OpenAI-API-Compatible",
        "llm_id": f"unused-chat@{instance_name}@OpenAI-API-Compatible",
    }


@pytest.fixture
def gaussdb_memory_cleanup(rest_client):
    memory_ids: list[str] = []

    yield memory_ids

    errors = []
    for memory_id in reversed(memory_ids):
        res = rest_client.delete(f"/memories/{memory_id}")
        if res.status_code != 200:
            errors.append((memory_id, res.status_code, res.text))
            continue
        payload = res.json()
        if payload.get("code") not in (0, 404):
            errors.append((memory_id, res.status_code, payload))
    assert not errors, f"GaussDB memory HTTP cleanup failed: {errors}"


@pytest.fixture
def create_gaussdb_memory(rest_client, gaussdb_memory_cleanup, gaussdb_memory_embedding_model):
    def _create(name_prefix: str = "gaussdb_http_memory") -> dict:
        _require_gaussdb_memory_http(rest_client)
        res = rest_client.post(
            "/memories",
            json={
                "name": f"{name_prefix}_{uuid.uuid4().hex[:8]}",
                "memory_type": ["raw"],
                "embd_id": gaussdb_memory_embedding_model["embedding_id"],
                "llm_id": gaussdb_memory_embedding_model["llm_id"],
            },
            timeout=60,
        )
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["code"] == 0, payload
        gaussdb_memory_cleanup.append(payload["data"]["id"])
        return payload["data"]

    return _create


def _add_message(rest_client, memory_ids: list[str], *, agent_id: str, session_id: str, user_input: str, agent_response: str, user_id: str = "") -> None:
    res = rest_client.post(
        "/messages",
        json={
            "memory_id": memory_ids,
            "agent_id": agent_id,
            "session_id": session_id,
            "user_id": user_id,
            "user_input": user_input,
            "agent_response": agent_response,
        },
        timeout=120,
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["code"] == 0, payload


def _memory_messages(rest_client, memory_id: str, **params) -> list[dict]:
    res = rest_client.get(f"/memories/{memory_id}", params=params or None, timeout=60)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["code"] == 0, payload
    return payload["data"]["messages"]["message_list"]


def _wait_for_messages(rest_client, memory_id: str, expected: int = 1, *, params: dict | None = None, timeout: float = 60.0) -> list[dict]:
    deadline = time.time() + timeout
    last_messages: list[dict] = []
    while time.time() < deadline:
        last_messages = _memory_messages(rest_client, memory_id, **(params or {}))
        if len(last_messages) >= expected:
            return last_messages
        time.sleep(0.5)
    pytest.fail(f"Timed out waiting for {expected} memory messages in {memory_id}; last={last_messages}")


def _recent_messages(rest_client, memory_ids: list[str], **params) -> list[dict]:
    query = [("memory_id", memory_id) for memory_id in memory_ids]
    query.extend((key, value) for key, value in params.items())
    res = rest_client.get("/messages", params=query, timeout=60)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["code"] == 0, payload
    return payload["data"]


def _search_messages(rest_client, memory_ids: list[str], **params) -> list[dict]:
    query = [("memory_id", memory_id) for memory_id in memory_ids]
    query.extend((key, value) for key, value in params.items())
    res = rest_client.get("/messages/search", params=query, timeout=120)
    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["code"] == 0, payload
    return payload["data"]


@pytest.mark.p1
def test_gaussdb_memory_store_http_user_lifecycle_and_filters(rest_client, create_gaussdb_memory, gaussdb_memory_inspector):
    memory = create_gaussdb_memory("gaussdb_http_lifecycle")
    memory_id = memory["id"]
    suffix = uuid.uuid4().hex[:10]
    agent_a = f"agent-http-a-{suffix}"
    agent_b = f"agent-http-b-{suffix}"
    session_a = f"session-http-a-{suffix}"
    session_b = f"session-http-b-{suffix}"
    query_word = f"gaussdbhttp {suffix} coriander contract"

    _add_message(
        rest_client,
        [memory_id],
        agent_id=agent_a,
        session_id=session_a,
        user_id=f"user-http-{suffix}",
        user_input=f"remember {query_word}",
        agent_response="the customer prefers coriander in contract summaries",
    )
    _add_message(
        rest_client,
        [memory_id],
        agent_id=agent_b,
        session_id=session_b,
        user_id=f"user-http-other-{suffix}",
        user_input=f"remember gaussdbhttp {suffix} pineapple",
        agent_response="the second session uses a different fruit",
    )

    all_messages = _wait_for_messages(rest_client, memory_id, expected=2, timeout=90)
    assert {msg["agent_id"] for msg in all_messages} >= {agent_a, agent_b}

    table, db_rows = gaussdb_memory_inspector.rows_for_memory(memory_id)
    raw_rows = [row for row in db_rows if row["message_type_kwd"] == "raw"]
    assert len(raw_rows) >= 2
    assert {row["memory_id"] for row in raw_rows} == {memory_id}
    assert {row["agent_id"] for row in raw_rows} >= {agent_a, agent_b}
    assert {row["session_id"] for row in raw_rows} >= {session_a, session_b}
    assert {int(row["status_int"]) for row in raw_rows} == {1}
    assert all(row["source_id"] == 0 for row in raw_rows)
    assert all(row["content_ltks"] and row["tokenized_content_ltks"] for row in raw_rows)
    vector_empty_columns = [column for column in raw_rows[0] if column.startswith("q_") and column.endswith("_vec_empty")]
    assert vector_empty_columns
    assert any(row[column] is False for row in raw_rows for column in vector_empty_columns)
    index_defs = gaussdb_memory_inspector.index_defs(table)
    assert "using ugin" in index_defs
    assert "using gsdiskann" in index_defs

    filtered = _memory_messages(rest_client, memory_id, agent_id=agent_a, keywords=session_a, page=1, page_size=5)
    assert filtered
    assert all(msg["agent_id"] == agent_a for msg in filtered)
    assert all(msg["session_id"] == session_a for msg in filtered)

    recent = _recent_messages(rest_client, [memory_id], agent_id=agent_a, session_id=session_a, limit=10)
    assert recent
    message_id = recent[0]["message_id"]
    assert recent[0]["agent_id"] == agent_a
    assert recent[0]["session_id"] == session_a

    content_res = rest_client.get(f"/messages/{memory_id}:{message_id}/content", timeout=60)
    assert content_res.status_code == 200, content_res.text
    content_payload = content_res.json()
    assert content_payload["code"] == 0, content_payload
    assert query_word in content_payload["data"]["content"]

    search_without_agent_filter = _search_messages(
        rest_client,
        [memory_id],
        query="coriander",
        top_n=5,
        similarity_threshold=-1.0,
        keywords_similarity_weight=0.7,
    )
    assert any(msg["message_id"] == message_id for msg in search_without_agent_filter), {
        "search": search_without_agent_filter,
        "raw_rows": [
            {
                "message_id": row["message_id"],
                "agent_id": row["agent_id"],
                "session_id": row["session_id"],
                "status_int": row["status_int"],
                "forget_at": row["forget_at"],
                "content_ltks": row["content_ltks"],
                "tokenized_content_ltks": row["tokenized_content_ltks"],
            }
            for row in raw_rows
        ],
    }

    search_before_disable = _search_messages(
        rest_client,
        [memory_id],
        query="coriander",
        agent_id=agent_a,
        session_id=session_a,
        top_n=5,
        similarity_threshold=-1.0,
        keywords_similarity_weight=0.7,
    )
    assert any(msg["message_id"] == message_id for msg in search_before_disable), {
        "search": search_before_disable,
        "raw_rows": [
            {
                "message_id": row["message_id"],
                "agent_id": row["agent_id"],
                "session_id": row["session_id"],
                "status_int": row["status_int"],
                "forget_at": row["forget_at"],
                "content_ltks": row["content_ltks"],
                "tokenized_content_ltks": row["tokenized_content_ltks"],
            }
            for row in raw_rows
        ],
    }

    update_res = rest_client.put(f"/messages/{memory_id}:{message_id}", json={"status": False}, timeout=60)
    assert update_res.status_code == 200, update_res.text
    update_payload = update_res.json()
    assert update_payload["code"] == 0, update_payload

    _table, db_rows_after_status = gaussdb_memory_inspector.rows_for_memory(memory_id, table)
    disabled_rows = [row for row in db_rows_after_status if int(row["message_id"]) == message_id]
    assert len(disabled_rows) == 1
    assert int(disabled_rows[0]["status_int"]) == 0

    search_after_disable = _search_messages(
        rest_client,
        [memory_id],
        query="coriander",
        agent_id=agent_a,
        session_id=session_a,
        top_n=5,
        similarity_threshold=-1.0,
        keywords_similarity_weight=0.7,
    )
    assert all(msg["message_id"] != message_id for msg in search_after_disable)

    forget_res = rest_client.delete(f"/messages/{memory_id}:{message_id}", timeout=60)
    assert forget_res.status_code == 200, forget_res.text
    forget_payload = forget_res.json()
    assert forget_payload["code"] == 0, forget_payload

    _table, db_rows_after_forget = gaussdb_memory_inspector.rows_for_memory(memory_id, table)
    forgotten_rows = [row for row in db_rows_after_forget if int(row["message_id"]) == message_id]
    assert len(forgotten_rows) == 1
    assert forgotten_rows[0]["forget_at"] is not None

    recent_after_forget = _recent_messages(rest_client, [memory_id], agent_id=agent_a, session_id=session_a, limit=10)
    assert all(msg["message_id"] != message_id for msg in recent_after_forget)


@pytest.mark.p1
def test_gaussdb_memory_store_http_multi_memory_fanout_and_delete(rest_client, create_gaussdb_memory, gaussdb_memory_inspector):
    memory_a = create_gaussdb_memory("gaussdb_http_fanout_a")
    memory_b = create_gaussdb_memory("gaussdb_http_fanout_b")
    mem_a = memory_a["id"]
    mem_b = memory_b["id"]
    suffix = uuid.uuid4().hex[:10]
    agent_id = f"agent-http-fanout-{suffix}"
    session_id = f"session-http-fanout-{suffix}"
    query_word = f"gaussdbhttp fanout {suffix} guava"

    _add_message(
        rest_client,
        [mem_a, mem_b],
        agent_id=agent_id,
        session_id=session_id,
        user_input=f"remember {query_word}",
        agent_response="store this fanout memory in both memories",
    )
    _wait_for_messages(rest_client, mem_a, expected=1, timeout=90)
    _wait_for_messages(rest_client, mem_b, expected=1, timeout=90)

    table_a, rows_a = gaussdb_memory_inspector.rows_for_memory(mem_a)
    table_b, rows_b = gaussdb_memory_inspector.rows_for_memory(mem_b)
    raw_rows_a = [row for row in rows_a if row["message_type_kwd"] == "raw"]
    raw_rows_b = [row for row in rows_b if row["message_type_kwd"] == "raw"]
    assert table_a == table_b
    assert len(raw_rows_a) >= 1
    assert len(raw_rows_b) >= 1
    assert raw_rows_a[0]["memory_id"] == mem_a
    assert raw_rows_b[0]["memory_id"] == mem_b
    assert raw_rows_a[0]["agent_id"] == agent_id
    assert raw_rows_b[0]["agent_id"] == agent_id
    assert raw_rows_a[0]["content_ltks"] == raw_rows_b[0]["content_ltks"]

    recent = _recent_messages(rest_client, [mem_a, mem_b], agent_id=agent_id, session_id=session_id, limit=10)
    assert {msg["memory_id"] for msg in recent} >= {mem_a, mem_b}

    search = _search_messages(
        rest_client,
        [mem_a, mem_b],
        query="guava",
        agent_id=agent_id,
        session_id=session_id,
        top_n=10,
        similarity_threshold=-1.0,
        keywords_similarity_weight=0.7,
    )
    assert {msg["memory_id"] for msg in search} >= {mem_a, mem_b}

    delete_a = rest_client.delete(f"/memories/{mem_a}", timeout=60)
    assert delete_a.status_code == 200, delete_a.text
    delete_payload = delete_a.json()
    assert delete_payload["code"] == 0, delete_payload

    _table, deleted_rows = gaussdb_memory_inspector.rows_for_memory(mem_a, table_a)
    assert deleted_rows == []
    _table, remaining_rows = gaussdb_memory_inspector.rows_for_memory(mem_b, table_b)
    assert remaining_rows
    assert {row["memory_id"] for row in remaining_rows} == {mem_b}

    remaining = _recent_messages(rest_client, [mem_b], agent_id=agent_id, session_id=session_id, limit=10)
    assert remaining
    assert {msg["memory_id"] for msg in remaining} == {mem_b}

    missing_config = rest_client.get(f"/memories/{mem_a}/config", timeout=60)
    assert missing_config.status_code == 200, missing_config.text
    assert missing_config.json()["code"] == 404


@pytest.mark.p2
def test_gaussdb_memory_store_http_validation_and_empty_result_contracts(rest_client, create_gaussdb_memory, gaussdb_memory_inspector):
    memory = create_gaussdb_memory("gaussdb_http_validation")
    memory_id = memory["id"]

    missing_recent = rest_client.get("/messages", timeout=60)
    assert missing_recent.status_code == 200, missing_recent.text
    missing_payload = missing_recent.json()
    assert missing_payload["code"] == 101, missing_payload
    assert "memory_ids is required" in missing_payload["message"]

    missing_search = rest_client.get("/messages/search", params={"query": "nothing"}, timeout=60)
    assert missing_search.status_code == 200, missing_search.text
    missing_search_payload = missing_search.json()
    assert missing_search_payload["code"] == 0, missing_search_payload
    assert missing_search_payload["data"] == []

    invalid_status = rest_client.put(f"/messages/{memory_id}:1", json={"status": "false"}, timeout=60)
    assert invalid_status.status_code == 200, invalid_status.text
    invalid_status_payload = invalid_status.json()
    assert invalid_status_payload["code"] == 101, invalid_status_payload
    assert "Status must be a boolean" in invalid_status_payload["message"]

    missing_content = rest_client.get(f"/messages/{memory_id}:999999999/content", timeout=60)
    assert missing_content.status_code == 200, missing_content.text
    missing_content_payload = missing_content.json()
    assert missing_content_payload["code"] == 404, missing_content_payload

    assert memory["tenant_id"]
    assert gaussdb_memory_inspector.all_rows_for_memory(memory_id) == []
