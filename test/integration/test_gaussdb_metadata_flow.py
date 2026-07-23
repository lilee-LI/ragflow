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


OP_GE = "\u2265"


@pytest.mark.asyncio
async def test_metadata_filter_pushdown_full_chain(gaussdb_env, ragflow_kb_context):
    from api.db.services.doc_metadata_service import DocMetadataService
    from common.metadata_utils import apply_meta_data_filter
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    kb_id = ragflow_kb_context["kb_id"]
    tenant_id = ragflow_kb_context["tenant_id"]
    meta_table = DocMetadataService._get_doc_meta_index_name(tenant_id)
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
