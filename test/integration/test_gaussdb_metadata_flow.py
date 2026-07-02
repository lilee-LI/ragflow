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


OP_GE = "\u2265"


@pytest.mark.asyncio
async def test_metadata_filter_pushdown_full_chain(gaussdb_env, monkeypatch):
    from common import settings
    from api.db.services import doc_metadata_service as metadata_module
    from api.db.services.doc_metadata_service import DocMetadataService
    from common.metadata_utils import apply_meta_data_filter
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = uuid.uuid4().hex
    tenant_id = gaussdb_env["table_prefix"]
    monkeypatch.setattr(settings, "docStoreConn", conn, raising=False)
    monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", True, raising=False)
    monkeypatch.setattr(
        metadata_module.Knowledgebase,
        "get_by_id",
        staticmethod(lambda _kb_id: type("KB", (), {"tenant_id": tenant_id})()),
    )
    meta_table = DocMetadataService._get_doc_meta_index_name(tenant_id)
    conn.create_doc_meta_idx(meta_table)
    assert conn.insert(
        [
            {
                "id": "doc-risk",
                "kb_id": kb_id,
                "meta_fields": {
                    "active": True,
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
                    "active": False,
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
                    "active": True,
                    "author": "Cindy",
                    "amount": None,
                    "tags": ["draft"],
                    "status": None,
                },
            },
        ],
        meta_table,
        kb_id,
    ) == []

    filters = [
        {"key": "amount", "op": OP_GE, "value": 100},
        {"key": "tags", "op": "contains", "value": "audit"},
        {"key": "status", "op": "not empty", "value": None},
    ]

    filtered_doc_ids = await apply_meta_data_filter(
        {"method": "manual", "manual": filters, "logic": "and"},
        kb_ids=[kb_id],
        metas_loader=lambda: (_ for _ in ()).throw(
            AssertionError("GaussDB metadata pushdown should avoid in-memory fallback")
        ),
    )

    assert filtered_doc_ids == ["doc-risk"]

    bool_and_null_doc_ids = await apply_meta_data_filter(
        {
            "method": "manual",
            "manual": [
                {"key": "active", "op": "=", "value": True},
                {"key": "status", "op": "empty", "value": None},
            ],
            "logic": "and",
        },
        kb_ids=[kb_id],
        metas_loader=lambda: (_ for _ in ()).throw(
            AssertionError("GaussDB metadata pushdown should avoid in-memory fallback")
        ),
    )
    assert bool_and_null_doc_ids == ["doc-null"]

    not_in_doc_ids = await apply_meta_data_filter(
        {
            "method": "manual",
            "manual": [
                {"key": "author", "op": "not in", "value": ["Bob", "Cindy"]},
            ],
            "logic": "and",
        },
        kb_ids=[kb_id],
        metas_loader=lambda: (_ for _ in ()).throw(
            AssertionError("GaussDB metadata pushdown should avoid in-memory fallback")
        ),
    )
    assert not_in_doc_ids == ["doc-risk"]


def test_metadata_upsert_failure_keeps_existing_row_in_live_gaussdb(gaussdb_env):
    from api.db.services.doc_metadata_service import DocMetadataService
    from common.doc_store.doc_store_base import OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = "kb-meta-rollback"
    meta_table = DocMetadataService._get_doc_meta_index_name(gaussdb_env["table_prefix"])
    conn.create_doc_meta_idx(meta_table)
    assert conn.insert(
        [{"id": "doc-keep", "kb_id": kb_id, "meta_fields": {"author": "Alice", "status": "old"}}],
        meta_table,
        kb_id,
    ) == []

    errors = conn.insert(
        [{"id": "doc-keep", "kb_id": kb_id, "meta_fields": {"bad": object()}}],
        meta_table,
        kb_id,
    )

    assert errors == ["doc-keep"]
    result = conn.search(
        ["id", "kb_id", "meta_fields"],
        [],
        {"id": "doc-keep"},
        [],
        OrderByExpr(),
        0,
        10,
        meta_table,
        [kb_id],
    )
    assert result.total == 1
    assert result.chunks[0]["meta_fields"] == {"author": "Alice", "status": "old"}
