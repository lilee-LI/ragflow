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

from common.doc_store.gaussdb_conn_base import (
    GaussDBDDLBuilder,
    InvalidGaussDBObjectName,
)


def test_rejects_unsafe_identifier():
    with pytest.raises(InvalidGaussDBObjectName):
        GaussDBDDLBuilder(schema="public;drop schema public")

    builder = GaussDBDDLBuilder(schema="public")
    with pytest.raises(InvalidGaussDBObjectName):
        builder.qualified_name("ragflow_x;drop table users")


def test_accepts_hash_and_dollar_in_identifier():
    builder = GaussDBDDLBuilder(schema="schema#tenant$1")

    assert builder.qualified_name("ragflow#tenant$1") == '"schema#tenant$1"."ragflow#tenant$1"'


def test_accepts_high_bit_identifier():
    builder = GaussDBDDLBuilder(schema="租户_schema1")

    assert builder.qualified_name("ragflow_租户1") == '"租户_schema1"."ragflow_租户1"'


def test_rejects_empty_identifier():
    with pytest.raises(InvalidGaussDBObjectName):
        GaussDBDDLBuilder(schema="")

    builder = GaussDBDDLBuilder(schema="public")
    with pytest.raises(InvalidGaussDBObjectName):
        builder.qualified_name("")
    with pytest.raises(InvalidGaussDBObjectName):
        builder.index_name("ragflow_tenant_a", "")


def test_rejects_overlong_identifier():
    with pytest.raises(InvalidGaussDBObjectName):
        GaussDBDDLBuilder(schema="s" * 64)

    builder = GaussDBDDLBuilder(schema="public")
    with pytest.raises(InvalidGaussDBObjectName):
        builder.qualified_name("t" * 64)
    with pytest.raises(InvalidGaussDBObjectName):
        builder.index_name("ragflow_tenant_a", "s" * 64)


def test_rejects_unsafe_index_suffix():
    builder = GaussDBDDLBuilder(schema="public")
    with pytest.raises(InvalidGaussDBObjectName):
        builder.index_name("ragflow_tenant_a", "kb_id;drop")


def test_index_name_hashes_long_tenant_names_under_gaussdb_limit():
    builder = GaussDBDDLBuilder(schema="public")
    table = "ragflow_" + "a" * 32

    name = builder.index_name(table, "knowledge_graph_kwd")
    same_name = builder.index_name(table, "knowledge_graph_kwd")
    other_name = builder.index_name(table, "entity_type_kwd")

    assert len(name) <= 63
    assert name == same_name
    assert name != other_name
    assert name.startswith("idx_gdb_ragflow_")


def test_chunk_table_ddl_uses_composite_pk_and_ustore():
    builder = GaussDBDDLBuilder(schema="public")
    ddl = builder.build_chunk_table_ddl("ragflow_tenant_a")

    assert 'CREATE TABLE IF NOT EXISTS "public"."ragflow_tenant_a"' in ddl
    assert "PRIMARY KEY (kb_id, id)" in ddl
    assert "WITH (storage_type=USTORE)" in ddl
    assert "important_kwd JSONB" in ddl
    assert "position_int JSONB" in ddl
    assert "chunk_data JSONB" in ddl


def test_doc_meta_table_ddl_uses_ustore_and_kb_index():
    builder = GaussDBDDLBuilder(schema="public")
    statements = builder.build_doc_meta_table_ddls("ragflow_doc_meta_tenant_a")
    joined = "\n".join(statements)

    assert 'CREATE TABLE IF NOT EXISTS "public"."ragflow_doc_meta_tenant_a"' in joined
    assert "meta_fields JSONB" in joined
    assert "PRIMARY KEY (id)" in joined
    assert "WITH (storage_type=USTORE)" in joined
    assert 'CREATE INDEX IF NOT EXISTS "idx_gdb_ragflow_doc_meta_tenant_a_kb_id"' in joined


def test_regular_index_ddls_cover_common_filter_columns():
    builder = GaussDBDDLBuilder(schema="public")
    statements = builder.build_regular_index_ddls("ragflow_tenant_a")
    joined = "\n".join(statements)

    assert "doc_id" in joined
    assert "available_int" in joined
    assert "knowledge_graph_kwd" in joined
    assert "entity_type_kwd" in joined
    assert "removed_kwd" in joined
    assert all('"public"."ragflow_tenant_a"' in statement for statement in statements)


def test_regular_index_ddls_keep_long_tenant_indexes_under_gaussdb_limit():
    builder = GaussDBDDLBuilder(schema="public")
    table = "ragflow_" + "a" * 32
    statements = builder.build_regular_index_ddls(table)

    for statement in statements:
        index_name = statement.split("CREATE INDEX IF NOT EXISTS ", 1)[1].split(" ON ", 1)[0].strip('"')
        assert len(index_name) <= 63


def test_fulltext_ugin_uses_single_space_fallback():
    builder = GaussDBDDLBuilder(schema="public")
    ddl = builder.build_fulltext_ugin_ddl("ragflow_tenant_a")

    assert "USING ugin(to_tsvector('simple'" in ddl
    assert "coalesce(title_tks, ' ')" in ddl
    assert "coalesce(content_sm_ltks, ' ')" in ddl
    assert "''" not in ddl


def test_vector_column_ddl_adds_valid_flag_and_zero_default():
    builder = GaussDBDDLBuilder(schema="public")
    statements = builder.build_vector_column_ddls("ragflow_tenant_a", 768)

    joined = "\n".join(statements)
    assert "ADD COLUMN IF NOT EXISTS q_768_vec floatvector(768)" in joined
    assert "array_fill(0, ARRAY[768])::text::floatvector(768)" in joined
    assert "ADD COLUMN IF NOT EXISTS q_768_vec_valid BOOLEAN DEFAULT FALSE NOT NULL" in joined


@pytest.mark.parametrize("dim", ["not-int", 0, -1])
def test_rejects_non_integer_or_non_positive_vector_dimensions(dim):
    builder = GaussDBDDLBuilder(schema="public")

    with pytest.raises(ValueError):
        builder.build_vector_column_ddls("ragflow_tenant_a", dim)


def test_rejects_vector_dimensions_above_gaussdb_limit():
    builder = GaussDBDDLBuilder(schema="public")
    with pytest.raises(ValueError, match="4096"):
        builder.build_vector_column_ddls("ragflow_tenant_a", 4097)


def test_diskann_high_dimension_disables_vector_copy():
    builder = GaussDBDDLBuilder(schema="public")
    ddl = builder.build_diskann_index_ddl("ragflow_tenant_a", 3072)

    assert "USING gsdiskann (q_3072_vec COSINE)" in ddl
    assert "subgraph_count=1" in ddl
    assert "enable_vector_copy=false" in ddl


def test_diskann_index_keeps_long_tenant_index_under_gaussdb_limit():
    builder = GaussDBDDLBuilder(schema="public")
    table = "ragflow_" + "a" * 32
    ddl = builder.build_diskann_index_ddl(table, 3072)

    index_name = ddl.split("CREATE INDEX IF NOT EXISTS ", 1)[1].split(" ON ", 1)[0].strip('"')
    assert len(index_name) <= 63


def test_diskann_low_dimension_omits_vector_copy_option():
    builder = GaussDBDDLBuilder(schema="public")
    ddl = builder.build_diskann_index_ddl("ragflow_tenant_a", 768)

    assert "WITH (subgraph_count=1)" in ddl
    assert "enable_vector_copy" not in ddl


def test_advisory_lock_sql_is_parameterized():
    builder = GaussDBDDLBuilder(schema="public")
    sql, params = builder.build_advisory_lock_sql("create_idx:ragflow_tenant_a")

    assert "pg_advisory_xact_lock" in sql
    assert "%s" in sql
    assert params == ["create_idx:ragflow_tenant_a"]
