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
import uuid

import pytest
from psycopg2 import sql

pytestmark = [pytest.mark.gaussdb_integration, pytest.mark.gaussdb_both]
OP_GE = "\u2265"


@pytest.mark.asyncio
async def test_tc_mgf_808_metadata_filter_pushdown_full_chain(
    gaussdb_env,
    gaussdb_admin_conn,
    ragflow_kb_context,
    register_table,
    monkeypatch,
):
    from common import settings
    from common import metadata_gaussdb_filter
    from api.db.services.doc_metadata_service import DocMetadataService
    from common.metadata_gaussdb_filter import build_gaussdb_filter
    from common.metadata_utils import apply_meta_data_filter

    tenant_id = ragflow_kb_context["tenant_id"]
    kb_id = ragflow_kb_context["kb_id"]
    other_kb_id = uuid.uuid4().hex
    monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", True, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_INFINITY", False, raising=False)
    meta_table = DocMetadataService._get_doc_meta_index_name(tenant_id)
    register_table(meta_table)
    assert settings.docStoreConn.create_doc_meta_idx(meta_table) is True
    with gaussdb_admin_conn.cursor() as cur:
        cur.executemany(
            sql.SQL("INSERT INTO {} (id, kb_id, meta_fields) VALUES (%s, %s, %s::jsonb)").format(sql.Identifier(gaussdb_env["schema"], meta_table)),
            [
                ("d1", kb_id, json.dumps({"status": "active"}, separators=(",", ":"))),
                ("d2", kb_id, json.dumps({"status": "inactive"}, separators=(",", ":"))),
                ("d3", other_kb_id, json.dumps({"status": "active"}, separators=(",", ":"))),
            ],
        )
    gaussdb_admin_conn.commit()
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.reloptions
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relname = %s
            """,
            [gaussdb_env["schema"], meta_table],
        )
        assert "storage_type=ustore" in str(cur.fetchone()[0]).lower()

    fetch_calls = []
    original_fetch = metadata_gaussdb_filter.fetch_gaussdb_metadata_doc_ids

    def tracked_fetch(*args, **kwargs):
        fetch_calls.append((args, kwargs))
        return original_fetch(*args, **kwargs)

    monkeypatch.setattr(metadata_gaussdb_filter, "fetch_gaussdb_metadata_doc_ids", tracked_fetch)
    filters = [{"key": "status", "op": "=", "value": "nonexistent"}]
    sql_filter, params = build_gaussdb_filter(filters, "and")
    assert sql_filter == ("(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))")
    assert params == ["nonexistent", "nonexistent"]
    no_match_doc_ids = DocMetadataService.filter_doc_ids_by_meta_pushdown(
        [kb_id],
        filters,
        "and",
        limit=100,
    )
    assert no_match_doc_ids == []
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0][1] == meta_table
    assert fetch_calls[0][0][2] == [kb_id]
    assert fetch_calls[0][0][-1] == 101

    active_doc_ids = DocMetadataService.filter_doc_ids_by_meta_pushdown(
        [kb_id],
        [{"key": "status", "op": "=", "value": "active"}],
        "and",
        limit=100,
    )
    assert active_doc_ids == ["d1"]

    fetch_calls.clear()
    fallback_calls = []

    def forbidden_fallback():
        fallback_calls.append(True)
        raise AssertionError("GaussDB metadata pushdown should avoid in-memory fallback")

    caller_result = await apply_meta_data_filter(
        {"method": "manual", "manual": filters, "logic": "and"},
        kb_ids=[kb_id],
        metas_loader=forbidden_fallback,
    )
    # The service-level [] means pushdown completed with no matches. The public
    # manual-filter caller preserves its established no-result sentinel.
    assert caller_result == ["-999"]
    assert fallback_calls == []
    assert len(fetch_calls) == 1
    assert fetch_calls[0][0][2] == [kb_id]
    assert fetch_calls[0][0][-1] == 10001

    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT id, kb_id, meta_fields FROM {} ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], meta_table)))
        assert cur.fetchall() == [
            ("d1", kb_id, {"status": "active"}),
            ("d2", kb_id, {"status": "inactive"}),
            ("d3", other_kb_id, {"status": "active"}),
        ]


def test_tc_wrt_310_metadata_upsert_failure_keeps_existing_row_in_live_gaussdb(gaussdb_env, register_table, gaussdb_admin_conn):
    from api.db.services.doc_metadata_service import DocMetadataService
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = "kb-meta-rollback"
    meta_table = DocMetadataService._get_doc_meta_index_name(gaussdb_env["table_prefix"])
    register_table(meta_table)
    assert conn.create_doc_meta_idx(meta_table) is True
    constraint = f"ck_{gaussdb_env['table_prefix'][:20]}_wrt310"
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK (COALESCE(meta_fields #>> '{{status}}', '') <> 'forbidden')").format(
                sql.Identifier(gaussdb_env["schema"], meta_table),
                sql.Identifier(constraint),
            )
        )
        cur.execute(
            sql.SQL("INSERT INTO {} (id, kb_id, meta_fields) VALUES (%s, %s, %s::jsonb)").format(sql.Identifier(gaussdb_env["schema"], meta_table)),
            ["doc-keep", kb_id, json.dumps({"author": "Alice", "status": "old"}, separators=(",", ":"))],
        )
    gaussdb_admin_conn.commit()

    errors = conn.insert(
        [
            {"id": "doc-new", "kb_id": kb_id, "meta_fields": {"status": "new"}},
            {"id": "doc-keep", "kb_id": kb_id, "meta_fields": {"author": "Mallory", "status": "forbidden"}},
        ],
        meta_table,
        kb_id,
    )

    assert errors == ["doc-new", "doc-keep"]
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT id, kb_id, meta_fields FROM {} ORDER BY id").format(sql.Identifier(gaussdb_env["schema"], meta_table)))
        rows = cur.fetchall()
    assert rows == [("doc-keep", kb_id, {"author": "Alice", "status": "old"})]


@pytest.mark.asyncio
async def test_metadata_filter_complex_pushdown_full_chain(
    ragflow_kb_context,
    register_table,
):
    from api.db.services.doc_metadata_service import DocMetadataService
    from common.metadata_utils import apply_meta_data_filter
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = ragflow_kb_context["kb_id"]
    tenant_id = ragflow_kb_context["tenant_id"]
    meta_table = DocMetadataService._get_doc_meta_index_name(tenant_id)
    register_table(meta_table)
    conn.create_doc_meta_idx(meta_table)
    assert (
        conn.insert(
            [
                {
                    "id": "doc-risk",
                    "kb_id": kb_id,
                    "meta_fields": {
                        "author": "Alice",
                        "amount": 120,
                        "tags": ["audit"],
                        "status": "open",
                    },
                },
                {
                    "id": "doc-low",
                    "kb_id": kb_id,
                    "meta_fields": {
                        "author": "Bob",
                        "amount": 20,
                        "tags": [],
                        "status": "",
                    },
                },
                {
                    "id": "doc-null",
                    "kb_id": kb_id,
                    "meta_fields": {
                        "author": "Cindy",
                        "amount": None,
                        "tags": ["draft"],
                    },
                },
            ],
            meta_table,
            kb_id,
        )
        == []
    )

    filters = [
        {"key": "amount", "op": OP_GE, "value": 100},
        {"key": "tags", "op": "contains", "value": "audit"},
        {"key": "status", "op": "not empty", "value": None},
    ]

    filtered_doc_ids = await apply_meta_data_filter(
        {"method": "manual", "manual": filters, "logic": "and"},
        kb_ids=[kb_id],
        metas_loader=lambda: (_ for _ in ()).throw(AssertionError("GaussDB metadata pushdown should avoid in-memory fallback")),
    )

    assert filtered_doc_ids == ["doc-risk"]
