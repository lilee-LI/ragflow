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

from common.doc_store.gaussdb_conn_pool import (
    GaussDBConfig,
    GaussDBConnectionPool,
    GaussDBPermissionError,
    InvalidGaussDBConfig,
    load_gaussdb_config,
    mask_gaussdb_uri,
)


def test_load_gaussdb_config_defaults_schema_to_public_when_schema_omitted():
    cfg = load_gaussdb_config(
        {
            "config": {
                "host": "127.0.0.1",
                "port": "19995",
                "database": "postgres",
                "user": "sqlbuilder",
                "password": "fake-unit-password",
            }
        }
    )

    assert cfg == GaussDBConfig(
        host="127.0.0.1",
        port=19995,
        database="postgres",
        user="sqlbuilder",
        password="fake-unit-password",
        schema="public",
    )


def test_load_gaussdb_config_uses_configured_test_schema():
    cfg = load_gaussdb_config(
        {
            "config": {
                "host": "127.0.0.1",
                "port": "19995",
                "database": "postgres",
                "user": "sqlbuilder",
                "password": "fake-unit-password",
                "schema": "ragflow_gaussdb_docengine_it",
            }
        }
    )

    assert cfg.schema == "ragflow_gaussdb_docengine_it"


def test_load_gaussdb_config_rejects_missing_required_fields():
    with pytest.raises(InvalidGaussDBConfig, match="password"):
        load_gaussdb_config({"config": {"host": "h", "port": 19995, "database": "d", "user": "u"}})


def test_load_gaussdb_config_rejects_unsafe_schema_name():
    with pytest.raises(InvalidGaussDBConfig, match="schema"):
        load_gaussdb_config(
            {
                "config": {
                    "host": "h",
                    "port": 19995,
                    "database": "d",
                    "user": "u",
                    "password": "fake-unit-password",
                    "schema": "public;drop table x",
                }
            }
        )


def test_mask_gaussdb_uri_never_exposes_password():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    masked = mask_gaussdb_uri(cfg)

    assert "fake-unit-password" not in masked
    assert "sqlbuilder@db.example:19995/postgres" in masked
    assert "schema=ragflow_gaussdb_docengine_it" in masked


def test_pool_forces_utf8_client_encoding(monkeypatch):
    captured = {}

    def create_pool(minconn, maxconn, **kwargs):
        captured.update({"minconn": minconn, "maxconn": maxconn, **kwargs})
        return object()

    monkeypatch.setattr(
        "common.doc_store.gaussdb_conn_pool.psycopg2_pool.ThreadedConnectionPool",
        create_pool,
    )
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_doc")

    GaussDBConnectionPool(cfg, minconn=2, maxconn=9)

    options = captured["options"]
    assert "-c search_path=ragflow_doc,public" in options
    assert "-c client_encoding=UTF8" in options
    assert "-c default_transaction_read_only=off" in options


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, row):
        self.cursor_obj = FakeCursor(row)
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, row=(True, True)):
        self.conn = FakeConnection(row)
        self.returned = []
        self.closed = False

    def getconn(self):
        return self.conn

    def putconn(self, conn, close=False):
        if close:
            conn.closed = True
        self.returned.append(conn)

    def closeall(self):
        self.closed = True


def test_pool_check_schema_access_verifies_usage_and_create_privileges():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    pool.check_schema_access()

    sql, params = fake_pool.conn.cursor_obj.executed[1]
    assert "has_schema_privilege" in sql
    assert params == (
        "sqlbuilder",
        "ragflow_gaussdb_docengine_it",
        "USAGE",
        "sqlbuilder",
        "ragflow_gaussdb_docengine_it",
        "CREATE",
    )
    assert fake_pool.returned == [fake_pool.conn]
    assert fake_pool.conn.cursor_obj.closed is True
    assert fake_pool.conn.rollbacks == 2


def test_pool_check_schema_access_rejects_missing_create_privilege():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    pool = GaussDBConnectionPool(cfg, pool=FakePool(row=(True, False)))

    with pytest.raises(GaussDBPermissionError, match="CREATE"):
        pool.check_schema_access()


class FailingPingCursor(FakeCursor):
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if sql == "SELECT 1":
            raise RuntimeError("SSL SYSCALL error: EOF detected")


class SequencedPool:
    def __init__(self):
        self.dead_conn = FakeConnection(row=None)
        self.dead_conn.cursor_obj = FailingPingCursor(row=None)
        self.live_conn = FakeConnection(row=None)
        self.returned = []
        self.closed = []

    def getconn(self):
        if not self.returned:
            return self.dead_conn
        return self.live_conn

    def putconn(self, conn, close=False):
        self.returned.append(conn)
        if close:
            self.closed.append(conn)


def test_pool_get_conn_discards_stale_connection_and_retries_once():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = SequencedPool()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    conn = pool.get_conn()

    assert conn is fake_pool.live_conn
    assert fake_pool.closed == [fake_pool.dead_conn]
    assert fake_pool.dead_conn.cursor_obj.closed is True
    assert fake_pool.live_conn.cursor_obj.closed is True
    assert fake_pool.live_conn.rollbacks == 1


def test_pool_put_conn_rolls_back_before_returning_connection():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    conn = pool.get_conn()
    pool.put_conn(conn)

    assert fake_pool.returned == [conn]
    assert conn.rollbacks == 2
    assert conn.closed is False


class RollbackFailingConnection(FakeConnection):
    def rollback(self):
        self.rollbacks += 1
        raise RuntimeError("connection lost")


def test_pool_put_conn_discards_connection_when_rollback_fails():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)
    conn = RollbackFailingConnection(row=None)

    pool.put_conn(conn)

    assert fake_pool.returned == [conn]
    assert conn.closed is True
    assert conn.rollbacks == 1
