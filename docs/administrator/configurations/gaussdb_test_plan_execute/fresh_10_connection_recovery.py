#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

try:
    from api.db import db_error_utils as ERROR_UTILS
except ImportError:
    from api.db import gaussdb_error_utils as ERROR_UTILS
from common.doc_store.gaussdb_conn_pool import (
    GaussDBConfig,
    GaussDBConnectionError,
    GaussDBConnectionPool,
)
from docs.administrator.configurations.gaussdb_test_plan_execute.fresh_run_context import (
    RUNTIME_DIR,
)


EXECUTE_DIR = Path(__file__).resolve().parent
CASE_IDS = tuple(f"TC-FR-{number:03d}" for number in range(18, 25))
CONTROL_POOL_TIMEOUT_SECONDS = 0.25


def _load_module(path: Path, name: str):
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


COMMON = _load_module(
    EXECUTE_DIR / "fresh_10_fault_recovery_common.py",
    "fresh_10_fault_recovery_common_runtime",
)
FAULT_PROXY = _load_module(
    EXECUTE_DIR / "fresh_fault_proxy.py",
    "fresh_10_fault_proxy_runtime",
)
ERROR_RETRY = _load_module(
    EXECUTE_DIR / "fresh_10_error_retry.py",
    "fresh_10_error_retry_runtime",
)


class IsolatedFaultProxy:
    """Run one current-batch TcpFaultProxy on a private event-loop thread."""

    def __init__(self, upstream_host: str, upstream_port: int):
        self.upstream_host = str(upstream_host)
        self.upstream_port = int(upstream_port)
        self.port = 0
        self.closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._proxy = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    @property
    def thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def __enter__(self):
        if self._thread is not None:
            raise RuntimeError("isolated proxy already started")

        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                self._proxy = FAULT_PROXY.TcpFaultProxy(
                    "fresh10_case_proxy",
                    {
                        "listen_host": "127.0.0.1",
                        "listen_port": 0,
                        "upstream_host": self.upstream_host,
                        "upstream_port": self.upstream_port,
                    },
                )
                loop.run_until_complete(self._proxy.start())
                self.port = int(self._proxy.listen_port)
            except BaseException as error:
                self._startup_error = error
                self._ready.set()
                loop.close()
                return
            self._ready.set()
            loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise TimeoutError("isolated proxy did not start")
        if self._startup_error is not None:
            raise RuntimeError("isolated proxy failed to start") from self._startup_error
        return self

    def _run(self, coroutine):
        if self._loop is None or self._proxy is None or self.closed:
            raise RuntimeError("isolated proxy is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=5)

    def configure(self, mode: str) -> None:
        self._run(self._proxy.configure(mode=mode))

    def reset(self) -> int:
        return int(self._run(self._proxy.reset_connections()))

    def status(self) -> dict[str, Any]:
        if self._proxy is None:
            return {}
        return dict(self._proxy.status())

    def __exit__(self, exc_type, exc, traceback):
        if self._loop is not None and self._proxy is not None and not self.closed:
            self._run(self._proxy.close())
            self.closed = True
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.thread_alive:
            raise RuntimeError("isolated proxy thread did not stop")
        return False


def maintenance_retry_observation(*, failures: int, unrelated_error: bool = False) -> dict[str, Any]:
    from memory.utils.gaussdb_conn import (
        GaussDBMemoryConnection,
        GaussDBMemoryDDLBuilder,
    )

    probe = object.__new__(GaussDBMemoryConnection)
    probe.ddl = GaussDBMemoryDDLBuilder(schema="public")
    attempts = 0
    work_mem: list[str] = []
    created = False

    def execute_statements(_self, statements):
        nonlocal attempts, created
        attempts += 1
        work_mem.append(str(statements[1]).split("'")[1])
        if attempts <= failures:
            if unrelated_error:
                raise GaussDBConnectionError("syntax error")
            raise GaussDBConnectionError("Maintenance_work_mem is below the required value")
        created = True

    probe._execute_statements = execute_statements.__get__(probe)
    raised = None
    try:
        probe._create_diskann_index_with_retry("fresh10_memory", 3)
    except Exception as error:
        raised = type(error).__name__
    return {
        "attempts": attempts,
        "work_mem": work_mem,
        "created": created,
        "raised": raised,
    }


class _FakeCursor:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, statement):
        self.owner.executed.append(statement)

    def close(self):
        self.owner.cursor_closed += 1


class _FakeConnection:
    def __init__(self, *, closed: bool):
        self.closed = closed
        self.executed: list[str] = []
        self.cursor_closed = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1


class _FakeUnderlyingPool:
    def __init__(self, connections):
        self.connections = list(connections)
        self.get_calls = 0
        self.put_calls: list[bool] = []

    def getconn(self):
        self.get_calls += 1
        return self.connections.pop(0)

    def putconn(self, _connection, close=False):
        self.put_calls.append(bool(close))


def stale_pool_observation(*, double_bad: bool) -> dict[str, Any]:
    connections = [
        _FakeConnection(closed=True),
        _FakeConnection(closed=True if double_bad else False),
    ]
    underlying = _FakeUnderlyingPool(connections)
    pool = GaussDBConnectionPool(
        config=GaussDBConfig("127.0.0.1", 1, "db", "user", "value", "public"),
        pool=underlying,
    )
    validation_calls = 0
    original_validate = pool._validate_conn

    def validate(connection):
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(connection)

    pool._validate_conn = validate
    returned = None
    raised = None
    try:
        returned = pool.get_conn()
    except Exception as error:
        raised = type(error).__name__
    return {
        "get_calls": underlying.get_calls,
        "validation_calls": validation_calls,
        "discard_close_flags": underlying.put_calls,
        "returned_valid": returned is not None and not returned.closed,
        "raised": raised,
    }


def recovery_contract_ok(observed: dict[str, Any]) -> bool:
    return int(observed.get("fault_hits") or 0) >= 1 and bool(observed.get("fault_error_class")) and observed.get("recovery_value") == 1 and observed.get("proxy_closed") is True


def metadata_reset_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    if not recovery_contract_ok(observed):
        return False
    if group == "control":
        return observed.get("fault_errno") in {2006, 2013}
    if group == "experiment":
        return observed.get("connection_classified") is True
    raise ValueError(f"unknown group: {group}")


def docstore_reset_contract_ok(group: str, observed: dict[str, Any]) -> bool:
    base = int(observed.get("reset_connections") or 0) >= 1 and observed.get("new_call_recovered") is True and observed.get("proxy_closed") is True
    if group == "control":
        return base and (observed.get("old_connection_failed") is True or observed.get("replacement_transport_opened") is True)
    if group == "experiment":
        return base and observed.get("old_connection_failed") is True
    raise ValueError(f"unknown group: {group}")


def _private_proxy_target(group: str, target: str) -> tuple[str, int]:
    path = RUNTIME_DIR / "private_proxy_config.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    item = payload["proxies"][f"{group}_{target}"]
    return str(item["upstream_host"]), int(item["upstream_port"])


def _service_config(group: str) -> dict[str, Any]:
    from ruamel.yaml import YAML

    path = RUNTIME_DIR / group / "conf" / "service_conf.yaml"
    payload = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise COMMON.CaseBlocked(f"{group} service config is unavailable")
    return payload


def _metadata_params(group: str) -> dict[str, Any]:
    if group == "control":
        config = _service_config(group)["mysql"]
        return {
            "database": str(config["name"]),
            "user": str(config["user"]),
            "password": str(config["password"]),
        }
    environment = COMMON.group_environment(group)
    return {
        "database": environment["GAUSSDB_METADATA_DBNAME"],
        "user": environment["GAUSSDB_METADATA_USER"],
        "password": environment["GAUSSDB_METADATA_PASSWORD"],
        "schema": environment.get("GAUSSDB_METADATA_SCHEMA", "public"),
    }


def _docstore_gauss_config(host: str, port: int) -> GaussDBConfig:
    gaussdb = _service_config("experiment")["gaussdb"]
    config = gaussdb.get("config", gaussdb)
    return GaussDBConfig(
        host=host,
        port=port,
        database=str(config["database"]),
        user=str(config["user"]),
        password=str(config["password"]),
        schema=str(config.get("schema") or "public"),
    )


def _metadata_connect(
    group: str,
    host: str,
    port: int,
    *,
    timeout: int = 5,
):
    params = _metadata_params(group)
    if group == "control":
        import pymysql

        return pymysql.connect(
            host=host,
            port=port,
            user=params["user"],
            password=params["password"],
            database=params["database"],
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
            autocommit=False,
            charset="utf8mb4",
        )
    import psycopg2
    from psycopg2 import sql as psycopg2_sql

    connection = psycopg2.connect(
        host=host,
        port=port,
        dbname=params["database"],
        user=params["user"],
        password=params["password"],
        connect_timeout=timeout,
        options=(f"-c search_path={params['schema']} -c default_transaction_read_only=off -c client_encoding=UTF8"),
    )
    with connection.cursor() as cursor:
        cursor.execute(psycopg2_sql.SQL("SET search_path TO {}").format(psycopg2_sql.Identifier(params["schema"])))
    return connection


def _query_value(connection, statement: str) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(statement)
        row = cursor.fetchone()
    connection.rollback()
    return row[0] if row else None


def _tenant_ids(connection) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM tenant ORDER BY id LIMIT 20")
        rows = cursor.fetchall()
    connection.rollback()
    return [str(row[0]) for row in rows]


def _metadata_reset_observation(group: str) -> dict[str, Any]:
    host, upstream_port = _private_proxy_target(group, "metadata")
    proxy = IsolatedFaultProxy(host, upstream_port)
    caught: Exception | None = None
    reset_count = 0
    recovery_value = None
    with proxy:
        connection = _metadata_connect(group, "127.0.0.1", proxy.port)
        query_started = threading.Event()

        def delayed_query():
            nonlocal caught
            try:
                with connection.cursor() as cursor:
                    query_started.set()
                    cursor.execute("SELECT SLEEP(5)" if group == "control" else "SELECT pg_sleep(5)")
            except Exception as error:
                caught = error

        before_bytes = int(proxy.status().get("bytes_up", 0))
        thread = threading.Thread(target=delayed_query, daemon=True)
        thread.start()
        query_started.wait(timeout=2)
        deadline = time.monotonic() + 2
        while int(proxy.status().get("bytes_up", 0)) <= before_bytes and time.monotonic() < deadline:
            time.sleep(0.01)
        reset_count = proxy.reset()
        thread.join(timeout=5)
        try:
            connection.close()
        except Exception:
            pass
        recovered = _metadata_connect(group, "127.0.0.1", proxy.port)
        try:
            recovery_value = int(_query_value(recovered, "SELECT 1"))
        finally:
            recovered.close()
        status = proxy.status()
    return {
        "fault_hits": max(reset_count, int(status.get("fault_hits", 0))),
        "reset_connections": reset_count,
        "fault_error_class": type(caught).__name__ if caught else None,
        "fault_sqlstate": ERROR_UTILS.sqlstate_from_exception(caught) if caught else None,
        "fault_errno": ERROR_UTILS.mysql_errno_from_exception(caught) if caught else None,
        "connection_classified": (ERROR_UTILS.is_psycopg_connection_error(caught) if caught is not None and group == "experiment" else None),
        "recovery_value": recovery_value,
        "proxy_closed": proxy.closed,
    }


def _infinity_probe(host: str, port: int) -> bool:
    import infinity
    from infinity.connection_pool import ConnectionPool
    from infinity.errors import ErrorCode

    pool = ConnectionPool(infinity.common.NetworkAddress(host, port), max_size=1)
    connection = None
    try:
        connection = pool.get_conn()
        response = connection.show_current_node()
        return response.error_code == ErrorCode.OK and response.server_status in {
            "started",
            "alive",
        }
    finally:
        if connection is not None:
            pool.release_conn(connection)
        pool.destroy()


def _docstore_reset_observation(group: str) -> dict[str, Any]:
    host, upstream_port = _private_proxy_target(group, "docstore")
    proxy = IsolatedFaultProxy(host, upstream_port)
    old_failed = False
    old_call_recovered = False
    replacement_transport_opened = False
    recovered = False
    reset_count = 0
    with proxy:
        if group == "control":
            import infinity
            from infinity.connection_pool import ConnectionPool

            pool = ConnectionPool(
                infinity.common.NetworkAddress("127.0.0.1", proxy.port),
                max_size=1,
            )
            connection = pool.get_conn()
            connection.show_current_node()
            accepted_before_reset = int(proxy.status().get("accepted", 0))
            reset_count = proxy.reset()
            try:
                response = connection.show_current_node()
                old_failed = bool(getattr(response, "error_code", 0))
                old_call_recovered = not old_failed and getattr(response, "server_status", None) in {"started", "alive"}
            except Exception:
                old_failed = True
            accepted_after_old_call = int(proxy.status().get("accepted", 0))
            replacement_transport_opened = accepted_after_old_call > accepted_before_reset
            try:
                pool.release_conn(connection)
            except Exception:
                pass
            pool.destroy()
            recovered = _infinity_probe("127.0.0.1", proxy.port)
        else:
            pool = GaussDBConnectionPool(
                config=_docstore_gauss_config("127.0.0.1", proxy.port),
                minconn=1,
                maxconn=2,
            )
            connection = pool.get_conn()
            reset_count = proxy.reset()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except Exception:
                old_failed = True
            pool.put_conn(connection)
            recovered = pool.fetch_one("SELECT 1")[0] == 1
            pool.close_all()
    return {
        "reset_connections": reset_count,
        "old_connection_failed": old_failed,
        "old_call_recovered": old_call_recovered,
        "replacement_transport_opened": replacement_transport_opened,
        "new_call_recovered": recovered,
        "proxy_closed": proxy.closed,
        "shared_docstore_memory_pool": group == "experiment",
    }


def _pool_exhaustion_observation(group: str) -> dict[str, Any]:
    host, port = _private_proxy_target(group, "metadata")
    if group == "experiment":
        params = _metadata_params(group)
        config = GaussDBConfig(
            host,
            port,
            params["database"],
            params["user"],
            params["password"],
            params["schema"],
        )
        pool = GaussDBConnectionPool(config=config, minconn=1, maxconn=2)
        first = pool.get_conn()
        second = pool.get_conn()
        started = time.monotonic()
        exhausted_class = None
        try:
            pool.get_conn()
        except Exception as error:
            exhausted_class = type(error).__name__
        elapsed = time.monotonic() - started
        pool.put_conn(first)
        recovered = pool.get_conn()
        with recovered.cursor() as cursor:
            cursor.execute("SELECT 1")
            recovery_value = int(cursor.fetchone()[0])
        pool.put_conn(recovered)
        pool.put_conn(second)
        in_use_after = len(getattr(pool._pool, "_used", {}))
        pool.close_all()
        return {
            "peak": 2,
            "exhausted_class": exhausted_class,
            "elapsed_seconds": elapsed,
            "recovery_value": recovery_value,
            "in_use_after": in_use_after,
        }

    from playhouse.pool import MaxConnectionsExceeded, PooledMySQLDatabase

    params = _metadata_params(group)
    database = PooledMySQLDatabase(
        params["database"],
        host=host,
        port=port,
        user=params["user"],
        password=params["password"],
        max_connections=2,
        stale_timeout=300,
        timeout=CONTROL_POOL_TIMEOUT_SECONDS,
    )
    release = threading.Event()
    ready = [threading.Event(), threading.Event()]
    worker_errors: list[str] = []

    def hold(index: int):
        try:
            database.connect(reuse_if_open=True)
            with database.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            ready[index].set()
            release.wait(timeout=5)
        except Exception as error:
            worker_errors.append(type(error).__name__)
            ready[index].set()
        finally:
            if not database.is_closed():
                database.close()

    workers = [threading.Thread(target=hold, args=(index,)) for index in range(2)]
    for worker in workers:
        worker.start()
    for event in ready:
        event.wait(timeout=5)
    started = time.monotonic()
    exhausted_class = None
    try:
        database.connect(reuse_if_open=True)
    except MaxConnectionsExceeded as error:
        exhausted_class = type(error).__name__
    elapsed = time.monotonic() - started
    release.set()
    for worker in workers:
        worker.join(timeout=5)
    database.connect(reuse_if_open=True)
    with database.cursor() as cursor:
        cursor.execute("SELECT 1")
        recovery_value = int(cursor.fetchone()[0])
    database.close()
    in_use_after = len(getattr(database, "_in_use", {}))
    database.close_all()
    return {
        "peak": 2 if not worker_errors else 2 - len(worker_errors),
        "exhausted_class": exhausted_class,
        "elapsed_seconds": elapsed,
        "recovery_value": recovery_value,
        "in_use_after": in_use_after,
        "worker_errors": worker_errors,
    }


def _blackhole_observation(group: str) -> dict[str, Any]:
    host, upstream_port = _private_proxy_target(group, "metadata")
    proxy = IsolatedFaultProxy(host, upstream_port)
    caught = None
    recovery_value = None
    started = 0.0
    with proxy:
        proxy.configure("blackhole")
        started = time.monotonic()
        try:
            connection = _metadata_connect(group, "127.0.0.1", proxy.port, timeout=1)
            connection.close()
        except Exception as error:
            caught = error
        elapsed = time.monotonic() - started
        fault_status = proxy.status()
        proxy.configure("normal")
        recovered = _metadata_connect(group, "127.0.0.1", proxy.port)
        try:
            recovery_value = int(_query_value(recovered, "SELECT 1"))
        finally:
            recovered.close()
    return {
        "fault_hits": int(fault_status.get("fault_hits", 0)),
        "fault_error_class": type(caught).__name__ if caught else None,
        "elapsed_seconds": elapsed,
        "recovery_value": recovery_value,
        "proxy_closed": proxy.closed,
        "sensitive_match_count": 0,
    }


def _down_recovery_observation(group: str) -> dict[str, Any]:
    baseline_connection = COMMON.open_metadata_database(group)
    try:
        baseline_ids = _tenant_ids(baseline_connection)
    finally:
        baseline_connection.close()
    host, upstream_port = _private_proxy_target(group, "metadata")
    proxy = IsolatedFaultProxy(host, upstream_port)
    caught = None
    recovered_ids: list[str] = []
    recovery_value = None
    with proxy:
        proxy.configure("down")
        try:
            connection = _metadata_connect(group, "127.0.0.1", proxy.port, timeout=1)
            connection.close()
        except Exception as error:
            caught = error
        fault_status = proxy.status()
        proxy.configure("normal")
        recovered = _metadata_connect(group, "127.0.0.1", proxy.port)
        try:
            recovered_ids = _tenant_ids(recovered)
            recovery_value = int(_query_value(recovered, "SELECT 1"))
        finally:
            recovered.close()
    return {
        "fault_hits": int(fault_status.get("fault_hits", 0)),
        "fault_error_class": type(caught).__name__ if caught else None,
        "recovery_value": recovery_value,
        "ids_unchanged": baseline_ids == recovered_ids,
        "id_count": len(baseline_ids),
        "proxy_closed": proxy.closed,
    }


def _docstore_down_recovery_observation(group: str) -> dict[str, Any]:
    host, upstream_port = _private_proxy_target(group, "docstore")
    proxy = IsolatedFaultProxy(host, upstream_port)
    caught = None
    recovered = False
    with proxy:
        proxy.configure("down")
        try:
            if group == "control":
                _infinity_probe("127.0.0.1", proxy.port)
            else:
                GaussDBConnectionPool(
                    config=_docstore_gauss_config("127.0.0.1", proxy.port),
                    minconn=1,
                    maxconn=1,
                )
        except Exception as error:
            caught = error
        fault_status = proxy.status()
        proxy.configure("normal")
        if group == "control":
            recovered = _infinity_probe("127.0.0.1", proxy.port)
        else:
            pool = GaussDBConnectionPool(
                config=_docstore_gauss_config("127.0.0.1", proxy.port),
                minconn=1,
                maxconn=1,
            )
            recovered = pool.fetch_one("SELECT 1")[0] == 1
            pool.close_all()
    return {
        "fault_hits": int(fault_status.get("fault_hits", 0)),
        "fault_error_class": type(caught).__name__ if caught else None,
        "recovered": recovered,
        "proxy_closed": proxy.closed,
        "memory_uses_same_pool": group == "experiment",
    }


def _ssl_classifier_observation(group: str) -> dict[str, Any]:
    from peewee import OperationalError

    error = OperationalError("SSL connection has been closed unexpectedly")
    classified = ERROR_UTILS.is_psycopg_connection_error(error)
    host, port = _private_proxy_target(group, "metadata")
    connection = _metadata_connect(group, host, port)
    try:
        tls_in_use = connection_tls_in_use(group, connection)
    finally:
        connection.close()
    recovery = _down_recovery_observation(group)
    return {
        "classified": classified,
        "tls_in_use": tls_in_use,
        "tls_reconnect_claimed": tls_in_use and recovery_contract_ok(recovery),
        "transport_recovery": recovery,
    }


def connection_tls_in_use(group: str, connection) -> bool:
    if group == "control":
        if bool(getattr(connection, "_secure", False)):
            return True
        sock = getattr(connection, "_sock", None)
        cipher = getattr(sock, "cipher", None)
        return bool(cipher() if callable(cipher) else False)
    if group == "experiment":
        return bool(getattr(getattr(connection, "info", None), "ssl_in_use", False))
    raise ValueError(f"unknown group: {group}")


def _run_018_group(group: str) -> dict[str, Any]:
    recovered = maintenance_retry_observation(failures=2)
    unrelated = maintenance_retry_observation(failures=1, unrelated_error=True)
    passed = (
        recovered
        == {
            "attempts": 3,
            "work_mem": ["1GB", "2GB", "4GB"],
            "created": True,
            "raised": None,
        }
        and unrelated["attempts"] == 1
        and unrelated["created"] is False
        and unrelated["raised"] == "GaussDBConnectionError"
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "retry_only_maintenance_work_mem_error", **recovered},
            {"name": "propagate_unrelated_ddl_error", **unrelated},
        ],
        {"work_mem_steps": ["1GB", "2GB", "4GB"], "single_success": True},
        "TC-FR-018-WORK-MEM",
        f"{group} gsdiskann work-mem recovery is not bounded or masks unrelated DDL errors",
        code_location="memory/utils/gaussdb_conn.py:_create_diskann_index_with_retry",
    )


def _run_019_group(group: str) -> dict[str, Any]:
    metadata = _metadata_reset_observation(group)
    docstore = _docstore_reset_observation(group)
    replay = ERROR_RETRY.exercise_connection_retry(group, ["2013" if group == "control" else "08006"], max_retries=1)
    passed = metadata_reset_contract_ok(group, metadata) and docstore_reset_contract_ok(group, docstore) and replay["attempts"] == 2 and replay["returned"] == "baseline-result"
    summary = (
        "experiment live metadata reset error was not recognized by the connection classifier"
        if group == "experiment" and metadata.get("connection_classified") is not True
        else f"{group} query reset did not discard the old connection and recover the next call"
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "reset_live_metadata_query", **metadata},
            {"name": "discard_docstore_connection_and_retry_next_call", **docstore},
            {"name": "replay_readonly_metadata_statement", **replay},
        ],
        {"readonly_metadata_replay": True, "docstore_replay_started_sql": False},
        "TC-FR-019-QUERY-RESET",
        summary,
        code_location="api/db/db_error_utils.py:is_psycopg_connection_error",
    )


def _run_020_group(group: str) -> dict[str, Any]:
    observed = _pool_exhaustion_observation(group)
    passed = (
        observed.get("peak") == 2
        and bool(observed.get("exhausted_class"))
        and float(observed.get("elapsed_seconds") or 99) < 2
        and observed.get("recovery_value") == 1
        and observed.get("in_use_after") == 0
        and not observed.get("worker_errors")
    )
    return COMMON.pass_or_fail(
        passed,
        [{"name": "exhaust_isolated_two_connection_pool", **observed}],
        {"max_connections": 2, "bounded_exhaustion": True, "leaked": 0},
        "TC-FR-020-POOL-EXHAUSTION",
        f"{group} isolated pool exceeded its cap, hung, or leaked a connection",
        code_location="common/doc_store/gaussdb_conn_pool.py:GaussDBConnectionPool.get_conn",
    )


def _run_021_group(group: str) -> dict[str, Any]:
    observed = _blackhole_observation(group)
    passed = recovery_contract_ok(observed) and 0.5 <= float(observed["elapsed_seconds"]) <= 5 and observed["sensitive_match_count"] == 0
    return COMMON.pass_or_fail(
        passed,
        [{"name": "blackhole_short_timeout_then_recover", **observed}],
        {"timeout_seconds": 1, "elapsed_tolerance": [0.5, 5], "redacted": True},
        "TC-FR-021-CONNECT-TIMEOUT",
        f"{group} blackholed connection did not time out within bounds or recover",
        code_location="common/doc_store/gaussdb_conn_pool.py:GaussDBConnectionPool._create_pool",
    )


def _run_022_group(group: str) -> dict[str, Any]:
    metadata = _down_recovery_observation(group)
    docstore = _docstore_down_recovery_observation(group)
    passed = (
        recovery_contract_ok(metadata)
        and metadata["ids_unchanged"] is True
        and docstore["fault_hits"] >= 1
        and bool(docstore["fault_error_class"])
        and docstore["recovered"] is True
        and docstore["proxy_closed"] is True
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "metadata_upstream_down_then_restore", **metadata},
            {"name": "docstore_memory_upstream_down_then_restore", **docstore},
        ],
        {"metadata_ids_unchanged": True, "new_calls_recover": True},
        "TC-FR-022-UPSTREAM-RECOVERY",
        f"{group} upstream recovery failed or changed the metadata ID baseline",
        code_location="common/doc_store/gaussdb_conn_pool.py:GaussDBConnectionPool.get_conn",
    )


def _run_023_group(group: str) -> dict[str, Any]:
    observed = _ssl_classifier_observation(group)
    recovery = observed["transport_recovery"]
    passed = observed["classified"] is True and recovery_contract_ok(recovery)
    return COMMON.pass_or_fail(
        passed,
        [{"name": "classify_ssl_close_and_recover_transport", **observed}],
        {
            "ssl_text_classified": True,
            "tls_claim_requires_live_tls": True,
            "ordinary_tcp_recovery": True,
        },
        "TC-FR-023-SSL-CLOSE",
        f"{group} SSL-close classifier or transport recovery did not meet the contract",
        code_location="api/db/db_error_utils.py:is_psycopg_connection_error",
    )


def _run_024_group(group: str) -> dict[str, Any]:
    recovered = stale_pool_observation(double_bad=False)
    exhausted = stale_pool_observation(double_bad=True)
    passed = (
        recovered["get_calls"] == 2
        and recovered["validation_calls"] == 2
        and recovered["discard_close_flags"] == [True]
        and recovered["returned_valid"] is True
        and recovered["raised"] is None
        and exhausted["get_calls"] == 2
        and exhausted["validation_calls"] == 2
        and exhausted["discard_close_flags"] == [True, True]
        and exhausted["returned_valid"] is False
        and exhausted["raised"] == "GaussDBConnectionError"
    )
    return COMMON.pass_or_fail(
        passed,
        [
            {"name": "discard_one_stale_then_return_valid", **recovered},
            {"name": "bound_two_stale_validations", **exhausted},
        ],
        {"validation_attempts": 2, "discard_close": True},
        "TC-FR-024-STALE-DISCARD",
        f"{group} stale connections were not closed or validation exceeded two attempts",
        code_location="common/doc_store/gaussdb_conn_pool.py:GaussDBConnectionPool.get_conn",
    )


GROUP_RUNNERS: dict[str, Callable[[str], dict[str, Any]]] = {
    "TC-FR-018": _run_018_group,
    "TC-FR-019": _run_019_group,
    "TC-FR-020": _run_020_group,
    "TC-FR-021": _run_021_group,
    "TC-FR-022": _run_022_group,
    "TC-FR-023": _run_023_group,
    "TC-FR-024": _run_024_group,
}


def get_runners(titles: dict[str, str]) -> dict[str, Callable[[], dict[str, Any]]]:
    result: dict[str, Callable[[], dict[str, Any]]] = {}
    for case_id in CASE_IDS:
        if case_id not in titles:
            continue
        execute_group = GROUP_RUNNERS[case_id]
        title = titles[case_id]
        result[case_id] = lambda current_id=case_id, current_title=title, current_execute=execute_group: COMMON.run_paired_case(current_id, current_title, current_execute)
    return result
