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
import json
import os
import sys
import types
import uuid

import pytest


pytestmark = pytest.mark.p1


def _install_settings_import_stubs(monkeypatch):
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
        "redis_conn": {"REDIS_CONN": types.SimpleNamespace(health=lambda: True)},
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


def _admin_conn():
    import psycopg2

    return psycopg2.connect(
        host=os.environ["GAUSSDB_HOST"],
        port=int(os.environ.get("GAUSSDB_PORT", "19995")),
        dbname=os.environ["GAUSSDB_DATABASE"],
        user=os.environ["GAUSSDB_USER"],
        password=os.environ["GAUSSDB_PASSWORD"],
    )


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


def _drop_generated_test_tables(schema, table_prefix):
    from psycopg2 import sql

    prefixes = [
        f"ragflow_it_{table_prefix}",
        f"ragflow_{table_prefix}",
        f"ragflow_doc_meta_{table_prefix}",
    ]
    with _admin_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = %s
                """,
                [schema],
            )
            for (table_name,) in cur.fetchall():
                if not any(table_name.startswith(prefix) for prefix in prefixes):
                    continue
                cur.execute(
                    sql.SQL("DROP TABLE IF EXISTS {}.{} CASCADE").format(
                        sql.Identifier(schema),
                        sql.Identifier(table_name),
                    )
                )


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


@pytest.fixture
def gaussdb_env(monkeypatch):
    if os.getenv("GAUSSDB_INTEGRATION") != "1":
        pytest.skip("set GAUSSDB_INTEGRATION=1 to run live GaussDB DocEngine tests")

    _install_settings_import_stubs(monkeypatch)

    from common import settings

    configured_schema = os.getenv("GAUSSDB_SCHEMA")
    schema = configured_schema or "public"
    table_prefix = uuid.uuid4().hex
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
    if configured_schema:
        monkeypatch.setenv("GAUSSDB_SCHEMA", schema)
    else:
        monkeypatch.delenv("GAUSSDB_SCHEMA", raising=False)

    try:
        yield {"schema": schema, "table_prefix": table_prefix}
    finally:
        _drop_generated_test_tables(schema, table_prefix)


def _table(env, suffix="tenant"):
    return f"ragflow_it_{env['table_prefix']}_{suffix}"


@pytest.fixture
def table_name():
    return _table


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
            pytest.skip(f"RAGFlow metadata DB is unavailable for real KB/tenant resolution: {exc}")
        raise
    created_kb = True

    try:
        yield {"tenant_id": tenant_id, "kb_id": kb_id}
    finally:
        if created_kb:
            with DB.connection_context():
                Knowledgebase.delete().where(Knowledgebase.id == kb_id).execute()


@pytest.fixture
def gaussdb_admin_conn():
    with _admin_conn() as conn:
        yield conn
