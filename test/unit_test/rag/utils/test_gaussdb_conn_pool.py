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
    GaussDBAuthenticationError,
    GaussDBConfig,
    GaussDBConnectionError,
    GaussDBConnectionPool,
    GaussDBPermissionError,
    InvalidGaussDBConfig,
    classify_gaussdb_exception,
    load_gaussdb_config,
    mask_gaussdb_uri,
)


def test_load_gaussdb_config_defaults_schema_to_public_when_schema_omitted():
    cfg = load_gaussdb_config({
        "config": {
            "host": "127.0.0.1",
            "port": "19995",
            "database": "postgres",
            "user": "sqlbuilder",
            "password": "fake-unit-password",
        }
    })

    assert cfg == GaussDBConfig(
        host="127.0.0.1",
        port=19995,
        database="postgres",
        user="sqlbuilder",
        password="fake-unit-password",
        schema="public",
    )


def test_load_gaussdb_config_uses_configured_test_schema():
    cfg = load_gaussdb_config({
        "config": {
            "host": "127.0.0.1",
            "port": "19995",
            "database": "postgres",
            "user": "sqlbuilder",
            "password": "fake-unit-password",
            "schema": "ragflow_gaussdb_docengine_it",
        }
    })

    assert cfg.schema == "ragflow_gaussdb_docengine_it"


def test_load_gaussdb_config_accepts_hash_and_dollar_in_schema_name():
    cfg = load_gaussdb_config({
        "config": {
            "host": "127.0.0.1",
            "port": "19995",
            "database": "postgres",
            "user": "sqlbuilder",
            "password": "fake-unit-password",
            "schema": "ragflow#tenant$1",
        }
    })

    assert cfg.schema == "ragflow#tenant$1"


def test_load_gaussdb_config_accepts_high_bit_schema_name():
    cfg = load_gaussdb_config({
        "config": {
            "host": "127.0.0.1",
            "port": "19995",
            "database": "postgres",
            "user": "sqlbuilder",
            "password": "fake-unit-password",
            "schema": "租户_schema1",
        }
    })

    assert cfg.schema == "租户_schema1"


def test_load_gaussdb_config_rejects_missing_required_fields():
    with pytest.raises(InvalidGaussDBConfig, match="password"):
        load_gaussdb_config({"config": {"host": "h", "port": 19995, "database": "d", "user": "u"}})


def test_load_gaussdb_config_rejects_unsafe_schema_name():
    with pytest.raises(InvalidGaussDBConfig, match="schema"):
        load_gaussdb_config({
            "config": {
                "host": "h",
                "port": 19995,
                "database": "d",
                "user": "u",
                "password": "fake-unit-password",
                "schema": "public;drop table x",
            }
        })


def test_load_gaussdb_config_rejects_overlong_schema_name():
    with pytest.raises(InvalidGaussDBConfig, match="schema"):
        load_gaussdb_config({
            "config": {
                "host": "h",
                "port": 19995,
                "database": "d",
                "user": "u",
                "password": "fake-unit-password",
                "schema": "s" * 64,
            }
        })


@pytest.mark.parametrize("port", ["not-a-port", 0, 65536])
def test_load_gaussdb_config_rejects_invalid_port_values(port):
    with pytest.raises(InvalidGaussDBConfig, match="port"):
        load_gaussdb_config({
            "config": {
                "host": "h",
                "port": port,
                "database": "d",
                "user": "u",
                "password": "fake-unit-password",
            }
        })


def test_mask_gaussdb_uri_never_exposes_password():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    masked = mask_gaussdb_uri(cfg)

    assert "fake-unit-password" not in masked
    assert "sqlbuilder@db.example:19995/postgres" in masked
    assert "schema=ragflow_gaussdb_docengine_it" in masked


def test_load_gaussdb_config_without_raw_prefers_gaussdb_block(monkeypatch):
    from common import settings

    monkeypatch.setattr(
        settings,
        "GAUSSDB",
        {
            "config": {
                "host": "gaussdb.local",
                "port": "19995",
                "database": "postgres",
                "user": "sqlbuilder",
                "password": "fake-unit-password",
            }
        },
        raising=False,
    )
    monkeypatch.setattr(settings, "get_base_config", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("fallback used")))

    cfg = load_gaussdb_config()

    assert cfg.host == "gaussdb.local"
    assert cfg.schema == "public"


def test_load_gaussdb_config_without_raw_falls_back_to_base_config(monkeypatch):
    from common import settings

    monkeypatch.setattr(settings, "GAUSSDB", None, raising=False)
    monkeypatch.setattr(
        settings,
        "get_base_config",
        lambda _name, _default=None: {
            "config": {
                "host": "gaussdb.local",
                "port": "19995",
                "database": "postgres",
                "user": "sqlbuilder",
                "password": "fake-unit-password",
                "schema": "ragflow_gaussdb_docengine_it",
            }
        },
    )

    cfg = load_gaussdb_config()

    assert cfg.schema == "ragflow_gaussdb_docengine_it"


def test_pool_without_config_loads_settings_config(monkeypatch):
    from common import settings

    fake_pool = object()
    monkeypatch.setattr(
        settings,
        "GAUSSDB",
        {
            "config": {
                "host": "gaussdb.local",
                "port": "19995",
                "database": "postgres",
                "user": "sqlbuilder",
                "password": "fake-unit-password",
                "schema": "ragflow_gaussdb_docengine_it",
            }
        },
        raising=False,
    )

    pool = GaussDBConnectionPool(pool=fake_pool)

    assert pool.config.host == "gaussdb.local"
    assert pool.resolved_schema == "ragflow_gaussdb_docengine_it"
    assert pool._pool is fake_pool


def test_pool_create_pool_uses_configured_search_path(monkeypatch):
    created = {}
    sentinel_pool = object()

    def fake_threaded_pool(*args, **kwargs):
        created["args"] = args
        created["kwargs"] = kwargs
        return sentinel_pool

    monkeypatch.setattr("common.doc_store.gaussdb_conn_pool.psycopg2_pool.ThreadedConnectionPool", fake_threaded_pool)
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")

    pool = GaussDBConnectionPool(cfg, minconn=2, maxconn=4)

    assert pool._pool is sentinel_pool
    assert created["args"] == (2, 4)
    assert created["kwargs"]["options"] == "-c search_path=ragflow_gaussdb_docengine_it,public"
    assert created["kwargs"]["password"] == "fake-unit-password"


def test_pool_create_pool_classifies_driver_failure(monkeypatch):
    def fake_threaded_pool(*_args, **_kwargs):
        raise RuntimeError("password authentication failed")

    monkeypatch.setattr("common.doc_store.gaussdb_conn_pool.psycopg2_pool.ThreadedConnectionPool", fake_threaded_pool)
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "public")

    with pytest.raises(GaussDBAuthenticationError):
        GaussDBConnectionPool(cfg)


@pytest.mark.parametrize(
    ("message", "expected_type"),
    [
        ("password authentication failed", GaussDBAuthenticationError),
        ("permission denied for schema", GaussDBPermissionError),
        ("network timeout", GaussDBConnectionError),
    ],
)
def test_classify_gaussdb_exception_maps_auth_permission_and_generic_errors(message, expected_type):
    classified = classify_gaussdb_exception(RuntimeError(message))

    assert isinstance(classified, expected_type)


def test_classify_gaussdb_exception_returns_existing_connection_error():
    existing = GaussDBPermissionError("already classified")

    assert classify_gaussdb_exception(existing) is existing


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


def test_pool_check_schema_access_rejects_missing_usage_privilege():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    pool = GaussDBConnectionPool(cfg, pool=FakePool(row=(False, True)))

    with pytest.raises(GaussDBPermissionError, match="USAGE"):
        pool.check_schema_access()


def test_pool_check_schema_access_classifies_query_failure():
    class FailingSchemaCursor(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "has_schema_privilege" in sql:
                raise RuntimeError("permission denied while checking schema")

    class FailingSchemaConnection(FakeConnection):
        def __init__(self):
            super().__init__(row=None)
            self.cursor_obj = FailingSchemaCursor(row=None)

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    fake_pool.conn = FailingSchemaConnection()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    with pytest.raises(GaussDBPermissionError):
        pool.check_schema_access()


def test_pool_check_schema_access_classifies_cursor_creation_failure():
    class CursorFailsAfterValidationConnection(FakeConnection):
        def __init__(self):
            super().__init__(row=(True, True))
            self.cursor_calls = 0

        def cursor(self):
            self.cursor_calls += 1
            if self.cursor_calls == 1:
                return self.cursor_obj
            raise RuntimeError("network timeout before privilege check")

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    fake_pool.conn = CursorFailsAfterValidationConnection()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    with pytest.raises(GaussDBConnectionError, match="network timeout"):
        pool.check_schema_access()

    assert fake_pool.returned == [fake_pool.conn]


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


def test_pool_get_conn_discards_closed_connection_and_retries_once():
    class ClosedFirstPool(SequencedPool):
        def __init__(self):
            super().__init__()
            self.dead_conn.closed = True

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = ClosedFirstPool()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    conn = pool.get_conn()

    assert conn is fake_pool.live_conn
    assert fake_pool.closed == [fake_pool.dead_conn]


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


def test_pool_put_conn_ignores_discard_failure_after_rollback_failure():
    class DiscardFailingPool(FakePool):
        def putconn(self, conn, close=False):
            raise RuntimeError("discard failed")

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    pool = GaussDBConnectionPool(cfg, pool=DiscardFailingPool(row=(True, True)))
    conn = RollbackFailingConnection(row=None)

    pool.put_conn(conn)

    assert conn.rollbacks == 1


def test_pool_discard_conn_supports_legacy_putconn_without_close_argument():
    class LegacyPool:
        def __init__(self):
            self.returned = []

        def putconn(self, conn):
            self.returned.append(conn)

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    legacy_pool = LegacyPool()
    pool = GaussDBConnectionPool(cfg, pool=legacy_pool)
    conn = object()

    pool._discard_conn(conn)

    assert legacy_pool.returned == [conn]


def test_pool_get_conn_raises_classified_error_after_two_failed_attempts():
    class AlwaysFailingPool:
        def __init__(self):
            self.closed = []

        def getconn(self):
            raise RuntimeError("permission denied for schema")

        def putconn(self, conn, close=False):
            if close:
                self.closed.append(conn)

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    pool = GaussDBConnectionPool(cfg, pool=AlwaysFailingPool())

    with pytest.raises(GaussDBPermissionError):
        pool.get_conn()


def test_pool_get_conn_classifies_cursor_creation_failure():
    class CursorCreationFailureConnection(FakeConnection):
        def cursor(self):
            raise RuntimeError("network timeout before cursor")

    class CursorCreationFailurePool:
        def __init__(self):
            self.conn = CursorCreationFailureConnection(row=None)
            self.closed = []

        def getconn(self):
            return self.conn

        def putconn(self, conn, close=False):
            if close:
                self.closed.append(conn)

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = CursorCreationFailurePool()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    with pytest.raises(GaussDBConnectionError, match="network timeout"):
        pool.get_conn()

    assert fake_pool.closed == [fake_pool.conn, fake_pool.conn]


def test_pool_put_conn_ignores_none_and_close_all_delegates():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    pool.put_conn(None)
    pool.close_all()

    assert fake_pool.returned == []
    assert fake_pool.closed is True


def test_pool_fetch_one_executes_query_and_returns_connection():
    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=("GaussDB",))
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    row = pool.fetch_one("SELECT version()", ("arg",))

    assert row == ("GaussDB",)
    assert fake_pool.conn.cursor_obj.executed[-1] == ("SELECT version()", ("arg",))
    assert fake_pool.conn.cursor_obj.closed is True
    assert fake_pool.returned == [fake_pool.conn]


def test_pool_fetch_one_classifies_cursor_creation_failure_and_returns_connection():
    class CursorFailsAfterValidationConnection(FakeConnection):
        def __init__(self):
            super().__init__(row=(True,))
            self.cursor_calls = 0

        def cursor(self):
            self.cursor_calls += 1
            if self.cursor_calls == 1:
                return self.cursor_obj
            raise RuntimeError("network timeout before query")

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    fake_pool.conn = CursorFailsAfterValidationConnection()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    with pytest.raises(GaussDBConnectionError, match="network timeout"):
        pool.fetch_one("SELECT 1")

    assert fake_pool.returned == [fake_pool.conn]


def test_pool_fetch_one_classifies_query_failure_and_cleans_up():
    class FailingQueryCursor(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if sql != "SELECT 1":
                raise RuntimeError("authentication failed while querying")

    class FailingQueryConnection(FakeConnection):
        def __init__(self):
            super().__init__(row=None)
            self.cursor_obj = FailingQueryCursor(row=None)

    cfg = GaussDBConfig("db.example", 19995, "postgres", "sqlbuilder", "fake-unit-password", "ragflow_gaussdb_docengine_it")
    fake_pool = FakePool(row=(True, True))
    fake_pool.conn = FailingQueryConnection()
    pool = GaussDBConnectionPool(cfg, pool=fake_pool)

    with pytest.raises(GaussDBAuthenticationError):
        pool.fetch_one("SELECT version()")

    assert fake_pool.conn.cursor_obj.closed is True
    assert fake_pool.returned == [fake_pool.conn]
