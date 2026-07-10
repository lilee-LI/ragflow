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
import base64
import json
import os
import re
import sys
import time
import types
import uuid
from contextlib import contextmanager

import pytest


pytestmark = pytest.mark.gaussdb_integration

_GAUSSDB_MARKERS = (
    "gaussdb_integration: live GaussDB integration test cases",
    "gaussdb_both: GaussDB integration case applicable to centralized and distributed variants",
    "gaussdb_centralized: GaussDB integration case or implementation exclusive to centralized",
    "gaussdb_distributed: GaussDB integration case or implementation exclusive to distributed",
    "gaussdb_variant_specific: GaussDB integration case with variant-specific expectations",
)

_GAUSSDB_INTEGRATION_SCHEMA = "ragflow_gaussdb_docengine_it"
_RAGFLOW_HTTP_TIMEOUT_SECONDS = 60


def pytest_configure(config):
    for marker in _GAUSSDB_MARKERS:
        config.addinivalue_line("markers", marker)


def _install_settings_import_stubs(monkeypatch):
    # XGBoost 1.6 imports distutils directly; setuptools provides that shim on Python 3.13.
    import setuptools  # noqa: F401

    class StubDocEngineConnection:
        def db_type(self):
            return "stub"

    class StubStorage:
        def health(self):
            return True

    class Dummy:
        def __init__(self, *_args, **_kwargs):
            pass

    def install_module(name, **attrs):
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    try:
        import rag.utils
        import memory.utils
    except Exception:
        return

    rag_modules = {
        "es_conn": {"ESConnection": StubDocEngineConnection},
        "infinity_conn": {"InfinityConnection": StubDocEngineConnection},
        "ob_conn": {"OBConnection": StubDocEngineConnection},
        "opensearch_conn": {"OSConnection": StubDocEngineConnection},
        "azure_sas_conn": {"RAGFlowAzureSasBlob": StubStorage},
        "azure_spn_conn": {"RAGFlowAzureSpnBlob": StubStorage},
        "gcs_conn": {"RAGFlowGCS": StubStorage},
        "minio_conn": {"RAGFlowMinio": StubStorage},
        "opendal_conn": {"OpenDALStorage": StubStorage},
        "redis_conn": {"REDIS_CONN": types.SimpleNamespace(health=lambda: True, is_alive=lambda: False, REDIS=None)},
        "s3_conn": {"RAGFlowS3": StubStorage},
        "oss_conn": {"RAGFlowOSS": StubStorage},
    }
    for short_name, attrs in rag_modules.items():
        module = install_module(f"rag.utils.{short_name}", **attrs)
        monkeypatch.setattr(rag.utils, short_name, module, raising=False)

    for short_name in ("es_conn", "infinity_conn", "ob_conn"):
        module = install_module(
            f"memory.utils.{short_name}",
            ESConnection=StubDocEngineConnection,
            InfinityConnection=StubDocEngineConnection,
            OBConnection=StubDocEngineConnection,
        )
        monkeypatch.setattr(memory.utils, short_name, module, raising=False)

    if "json_repair" not in sys.modules:
        install_module("json_repair", loads=json.loads)
    if "langfuse" not in sys.modules:
        install_module("langfuse", Langfuse=Dummy, propagate_attributes=lambda **_kwargs: Dummy())
    if "mcp.client.session" not in sys.modules:
        async def _unavailable_mcp_client(*_args, **_kwargs):
            raise RuntimeError("mcp client is unavailable in this integration test")

        install_module("mcp")
        install_module("mcp.client")
        install_module("mcp.client.session", ClientSession=Dummy)
        install_module("mcp.client.sse", sse_client=_unavailable_mcp_client)
        install_module("mcp.client.streamable_http", streamablehttp_client=_unavailable_mcp_client)
        install_module("mcp.types", CallToolResult=Dummy, ListToolsResult=Dummy, TextContent=Dummy, Tool=Dummy)
    if "beartype" not in sys.modules:
        install_module("beartype", beartype=lambda obj=None, **_kwargs: obj if obj is not None else (lambda wrapped: wrapped))
        install_module("beartype.claw", beartype_this_package=lambda *_args, **_kwargs: None)
    if "api.utils.api_utils" not in sys.modules:
        install_module(
            "api.utils.api_utils",
            get_parser_config=lambda *_args, **_kwargs: {},
            get_data_error_result=lambda *_args, **_kwargs: {},
        )


@contextmanager
def _admin_conn(connect_attempts=1):
    import psycopg2

    conn = None
    for attempt in range(connect_attempts):
        try:
            conn = psycopg2.connect(
                host=os.environ["GAUSSDB_HOST"],
                port=int(os.environ.get("GAUSSDB_PORT", "19995")),
                dbname=os.environ["GAUSSDB_DATABASE"],
                user=os.environ["GAUSSDB_USER"],
                password=os.environ["GAUSSDB_PASSWORD"],
                connect_timeout=10,
                options="-c default_transaction_read_only=off",
            )
            break
        except psycopg2.OperationalError:
            if attempt + 1 == connect_attempts:
                raise
            time.sleep(1)
    try:
        yield conn
    finally:
        conn.close()


def _assert_schema_access(schema_name):
    with _admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.schemata
                    WHERE schema_name = %s
                )
                """,
                [schema_name],
            )
            (exists,) = cur.fetchone()
            assert exists, f"schema {schema_name!r} must be pre-created before live tests"
            cur.execute(
                """
                SELECT
                has_schema_privilege(current_user, %s, 'USAGE'),
                has_schema_privilege(current_user, %s, 'CREATE')
                """,
                [schema_name, schema_name],
            )
            has_usage, has_create = cur.fetchone()
    assert has_usage and has_create, f"current user must have USAGE and CREATE on schema {schema_name!r}"


def _drop_generated_test_tables(schema, table_names):
    from psycopg2 import sql

    registered = sorted(set(table_names))
    if not registered:
        return

    with _admin_conn(connect_attempts=3) as conn:
        try:
            with conn.cursor() as cur:
                for table_name in registered:
                    cur.execute(
                        sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                            sql.Identifier(schema),
                            sql.Identifier(table_name),
                        )
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    with _admin_conn(connect_attempts=3) as conn:
        with conn.cursor() as cur:
            placeholders = ", ".join(["%s"] * len(registered))
            cur.execute(
                f"""
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = %s
                   AND table_name IN ({placeholders})
                """,
                [schema, *registered],
            )
            leftovers = [row[0] for row in cur.fetchall()]
            assert not leftovers, f"integration cleanup left tables behind: {leftovers}"


def _is_ragflow_metadata_db_unavailable(exc):
    msg = str(exc).lower()
    unavailable_markers = (
        "can't connect",
        "connection refused",
        "connection reset",
        "access denied",
        "unknown database",
        "doesn't exist",
        "no such table",
    )
    return any(marker in msg for marker in unavailable_markers)


def _apply_metadata_db_container_env():
    """Align a fresh test process with the metadata DB used by the service container."""
    from common import config_utils

    database_type = os.getenv("DB_TYPE", "mysql")
    if database_type != "mysql":
        return

    database = dict(config_utils.CONFIGS.get(database_type) or {})
    env_keys = {
        "host": "MYSQL_HOST",
        "port": "MYSQL_PORT",
        "name": "MYSQL_DBNAME",
        "user": "MYSQL_USER",
        "password": "MYSQL_PASSWORD",
    }
    for config_key, env_key in env_keys.items():
        value = os.getenv(env_key)
        if value:
            database[config_key] = int(value) if config_key == "port" else value
    config_utils.CONFIGS[database_type] = database


@pytest.fixture(scope="session", autouse=True)
def gaussdb_variant_preflight(record_testsuite_property):
    if os.getenv("GAUSSDB_INTEGRATION") != "1":
        return {"variant": None}

    variant = os.getenv("GAUSSDB_VARIANT")
    assert variant in {"centralized", "distributed"}, (
        "GAUSSDB_VARIANT must be centralized or distributed for live integration"
    )
    expected_dims = [int(item.strip()) for item in os.getenv("GAUSSDB_EXPECTED_VECTOR_DIMS", "").split(",") if item.strip()]
    required_dims = [1024] if variant == "distributed" else [1024, 1536, 3072]
    assert expected_dims == required_dims, (
        f"{variant.capitalize()} integration requires "
        f"GAUSSDB_EXPECTED_VECTOR_DIMS={','.join(map(str, required_dims))}"
    )
    schema = os.getenv("GAUSSDB_SCHEMA")
    assert schema == _GAUSSDB_INTEGRATION_SCHEMA

    with _admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, version()")
            database, user, version = cur.fetchone()
            cur.execute("SHOW sql_compatibility")
            compatibility = str(cur.fetchone()[0]).upper()
            cur.execute("SHOW enable_ustore")
            enable_ustore = str(cur.fetchone()[0]).lower()
            cur.execute("SHOW enable_default_ustore_table")
            enable_default_ustore = str(cur.fetchone()[0]).lower()
            cur.execute("SHOW enable_vectordb")
            enable_vectordb = str(cur.fetchone()[0]).lower()
            cur.execute("SHOW transaction_read_only")
            transaction_read_only = str(cur.fetchone()[0]).lower()
            cur.execute("SELECT node_type, COUNT(*) FROM pg_catalog.pgxc_node GROUP BY node_type ORDER BY node_type")
            topology = {str(node_type): int(count) for node_type, count in cur.fetchall()}
            cur.execute(
                "SELECT has_schema_privilege(current_user, %s, 'USAGE'), has_schema_privilege(current_user, %s, 'CREATE')",
                [schema, schema],
            )
            schema_privileges = tuple(cur.fetchone())

    assert database == os.environ["GAUSSDB_DATABASE"]
    assert user == os.environ["GAUSSDB_USER"]
    assert compatibility == ("ORA" if variant == "distributed" else "A")
    assert enable_ustore == "on"
    assert enable_default_ustore == "on"
    assert enable_vectordb == "on"
    assert transaction_read_only == "off"
    if variant == "distributed":
        assert topology == {"C": 3, "D": 3, "S": 6}
    else:
        assert topology == {}
    assert schema_privileges == (True, True)
    run_id = uuid.uuid4().hex
    record_testsuite_property("gaussdb.run_id", run_id)
    record_testsuite_property("gaussdb.variant", variant)
    record_testsuite_property("gaussdb.database", database)
    record_testsuite_property("gaussdb.user", user)
    record_testsuite_property("gaussdb.schema", schema)
    record_testsuite_property("gaussdb.version", version)
    record_testsuite_property("gaussdb.sql_compatibility", compatibility)
    record_testsuite_property("gaussdb.vector_dims", json.dumps(expected_dims))
    record_testsuite_property("gaussdb.topology", json.dumps(topology, sort_keys=True))
    record_testsuite_property("gaussdb.enable_ustore", enable_ustore)
    record_testsuite_property("gaussdb.enable_default_ustore_table", enable_default_ustore)
    record_testsuite_property("gaussdb.enable_vectordb", enable_vectordb)
    record_testsuite_property("gaussdb.transaction_read_only", transaction_read_only)
    return {
        "run_id": run_id,
        "variant": variant,
        "database": database,
        "user": user,
        "version": version,
        "sql_compatibility": compatibility,
        "enable_ustore": enable_ustore,
        "enable_default_ustore_table": enable_default_ustore,
        "enable_vectordb": enable_vectordb,
        "topology": topology,
        "schema_privileges": schema_privileges,
    }


@pytest.fixture
def gaussdb_env(monkeypatch, gaussdb_variant_preflight):
    if os.getenv("GAUSSDB_INTEGRATION") != "1":
        pytest.skip("set GAUSSDB_INTEGRATION=1 to run live GaussDB DocEngine tests")

    _apply_metadata_db_container_env()
    _install_settings_import_stubs(monkeypatch)

    from common import settings

    configured_schema = os.getenv("GAUSSDB_SCHEMA")
    if not configured_schema:
        pytest.fail("set GAUSSDB_SCHEMA to the pre-created integration schema")
    if configured_schema != _GAUSSDB_INTEGRATION_SCHEMA:
        pytest.fail(f"GAUSSDB_SCHEMA must be the dedicated integration schema {_GAUSSDB_INTEGRATION_SCHEMA!r}")
    schema = configured_schema
    table_prefix = uuid.uuid4().hex
    created_tables = set()
    _assert_schema_access(schema)

    monkeypatch.setenv("DOC_ENGINE", "gaussdb")
    monkeypatch.setattr(settings, "DOC_ENGINE", "gaussdb", raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", True, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_OCEANBASE", False, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_INFINITY", False, raising=False)
    monkeypatch.setattr(
        settings,
        "GAUSSDB",
        {
            "host": os.environ["GAUSSDB_HOST"],
            "port": os.environ.get("GAUSSDB_PORT", "19995"),
            "database": os.environ["GAUSSDB_DATABASE"],
            "user": os.environ["GAUSSDB_USER"],
            "password": os.environ["GAUSSDB_PASSWORD"],
            "schema": schema,
        },
        raising=False,
    )
    monkeypatch.setenv("GAUSSDB_SCHEMA", schema)

    try:
        yield {
            "schema": schema,
            "table_prefix": table_prefix,
            "created_tables": created_tables,
            "variant_evidence": gaussdb_variant_preflight,
        }
    finally:
        _drop_generated_test_tables(schema, created_tables)


def _table(env, suffix="tenant"):
    if not re.fullmatch(r"[A-Za-z0-9_]+", suffix):
        raise ValueError(f"invalid integration table suffix: {suffix!r}")
    table = f"ragflow_it_{env['table_prefix']}_{suffix}"
    env["created_tables"].add(table)
    return table


@pytest.fixture
def table_name():
    return _table


@pytest.fixture
def register_table(gaussdb_env):
    def _register(table):
        gaussdb_env["created_tables"].add(table)
        return table

    return _register


@pytest.fixture
def ragflow_kb_context(gaussdb_env, monkeypatch):
    from api.db.db_models import DB, Knowledgebase
    from api.db.services.knowledgebase_service import KnowledgebaseService
    from common import settings
    from common.constants import StatusEnum
    from rag.utils.gaussdb_conn import GaussDBConnection

    tenant_id = gaussdb_env["table_prefix"]
    kb_id = uuid.uuid4().hex
    created_kb = False

    monkeypatch.setattr(settings, "docStoreConn", GaussDBConnection(), raising=False)
    try:
        KnowledgebaseService.save(
            id=kb_id,
            tenant_id=tenant_id,
            name=f"gaussdb-it-{gaussdb_env['table_prefix']}",
            embd_id="BAAI/bge-small-en-v1.5@Builtin",
            created_by=tenant_id,
            permission="me",
            parser_id="naive",
            status=StatusEnum.VALID.value,
        )
    except Exception as exc:
        if _is_ragflow_metadata_db_unavailable(exc):
            pytest.fail(f"RAGFlow metadata DB is unavailable for real KB/tenant resolution: {exc}")
        raise
    created_kb = True

    try:
        yield {"tenant_id": tenant_id, "kb_id": kb_id}
    finally:
        if created_kb:
            with DB.connection_context():
                Knowledgebase.delete().where(Knowledgebase.id == kb_id).execute()
                assert Knowledgebase.select().where(Knowledgebase.id == kb_id).count() == 0


@pytest.fixture
def ragflow_http_auth(gaussdb_env):
    import requests

    from api.db.db_models import DB, User
    from api.db.services.user_service import UserService
    from api.utils.crypt import crypt
    from common.constants import StatusEnum

    base_url = (os.getenv("RAGFLOW_BASE_URL") or "http://127.0.0.1").rstrip("/")
    ping = requests.get(
        f"{base_url}/api/v1/system/ping",
        timeout=_RAGFLOW_HTTP_TIMEOUT_SECONDS,
    )
    assert ping.status_code == 200

    user_id = uuid.uuid4().hex
    access_token = uuid.uuid4().hex
    password = uuid.uuid4().hex
    email = f"gaussdb-it-{gaussdb_env['table_prefix']}@example.invalid"
    created_user = False
    try:
        UserService.save(
            id=user_id,
            access_token=access_token,
            password=base64.b64encode(password.encode()).decode(),
            nickname=f"gaussdb-it-{gaussdb_env['table_prefix']}",
            email=email,
            login_channel="password",
            status=StatusEnum.VALID.value,
        )
        created_user = True
        login = requests.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": email, "password": crypt(password)},
            timeout=_RAGFLOW_HTTP_TIMEOUT_SECONDS,
        )
        assert login.status_code == 200
        assert login.json()["code"] == 0
        authorization = login.headers.get("Authorization")
        assert authorization
        yield {
            "base_url": base_url,
            "headers": {"Authorization": authorization},
            "user_id": user_id,
            "request_timeout": _RAGFLOW_HTTP_TIMEOUT_SECONDS,
        }
    finally:
        if created_user:
            with DB.connection_context():
                User.delete().where(User.id == user_id).execute()
                assert User.select().where(User.id == user_id).count() == 0


@pytest.fixture
def gaussdb_admin_conn():
    with _admin_conn() as conn:
        yield conn


@pytest.fixture(scope="session", autouse=True)
def gaussdb_pool_cleanup():
    yield
    if os.getenv("GAUSSDB_INTEGRATION") != "1":
        return
    module = sys.modules.get("common.doc_store.gaussdb_conn_pool")
    if module is not None:
        module.GAUSSDB_CONN.close_all()
