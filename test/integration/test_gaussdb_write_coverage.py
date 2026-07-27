import json
import re
import threading
import time

import psycopg2
import pytest
from psycopg2 import sql

from gaussdb_test_markers import mark_gaussdb_both_by_default


pytestmark = pytest.mark.gaussdb_integration


def _qualified(schema, table):
    return sql.Identifier(schema, table)


def _fetch_one(admin_conn, statement, params):
    with admin_conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchone()


def _fetch_all(admin_conn, statement, params):
    with admin_conn.cursor() as cur:
        cur.execute(statement, params)
        return cur.fetchall()


def _decode_vector(value):
    if isinstance(value, str):
        return json.loads(value)
    return list(value)


def _ugin_index_defs(index_defs, config=None):
    matches = [definition for definition in index_defs if " using ugin " in definition]
    if config is None:
        return matches
    return [definition for definition in matches if f"to_tsvector('{config}'" in definition]


def _insert_rows(admin_conn, schema, table, rows):
    assert rows
    columns = list(rows[0])
    assert all(set(row) == set(columns) for row in rows)
    placeholders = []
    encoded_rows = []
    for column in columns:
        vector_match = re.fullmatch(r"q_(\d+)_vec", column)
        if vector_match:
            placeholders.append(sql.SQL(f"%s::floatvector({vector_match.group(1)})"))
        elif any(isinstance(row[column], (dict, list)) for row in rows):
            placeholders.append(sql.SQL("%s::jsonb"))
        else:
            placeholders.append(sql.SQL("%s"))
    for row in rows:
        values = []
        for column in columns:
            value = row[column]
            if re.fullmatch(r"q_(\d+)_vec", column) or isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            values.append(value)
        encoded_rows.append(values)
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        _qualified(schema, table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join(placeholders),
    )
    with admin_conn.cursor() as cur:
        cur.executemany(statement, encoded_rows)
    admin_conn.commit()


def test_tc_wrt_003_create_idx_adds_zero_vector_and_false_valid_defaults(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_003")
    assert conn.create_idx(table, "kb-wrt-003", 1024) is True
    rows = _fetch_all(
        gaussdb_admin_conn,
        """
        SELECT column_name, column_default, data_type, is_nullable
          FROM information_schema.columns
         WHERE table_schema = %s AND table_name = %s
           AND column_name IN ('q_1024_vec', 'q_1024_vec_valid')
        """,
        [gaussdb_env["schema"], table],
    )
    columns = {row[0]: row[1:] for row in rows}
    assert columns == {
        "q_1024_vec": ("((array_fill(0, ARRAY[1024]))::text)::floatvector(1024)", "floatvector", "YES"),
        "q_1024_vec_valid": ("false", "boolean", "NO"),
    }


@pytest.mark.gaussdb_variant_specific
def test_tc_wrt_009_create_idx_uses_diskann_copy_option_above_1024(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    if gaussdb_env["variant_evidence"].get("variant") == "distributed":
        for dim in (1536, 3072):
            table = table_name(gaussdb_env, f"wrt_009_{dim}")
            assert conn.ddl.validate_vector_dim(dim) == dim
            with pytest.raises(psycopg2.Error) as exc_info:
                conn.create_idx(table, f"kb-wrt-009-{dim}", dim)
            assert exc_info.value.pgcode == "22023"
            assert "dimensions for type vector cannot exceed 1024" in str(exc_info.value).lower()
            assert _fetch_one(
                gaussdb_admin_conn,
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                [gaussdb_env["schema"], table],
            )[0] == 0
            assert _fetch_one(
                gaussdb_admin_conn,
                "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                [gaussdb_env["schema"], table],
            )[0] == 0
            assert conn.create_idx(table, f"kb-wrt-009-recovery-{dim}", 1024) is True
            assert _fetch_one(
                gaussdb_admin_conn,
                """
                SELECT format_type(a.atttypid, a.atttypmod)
                  FROM pg_attribute a
                  JOIN pg_class c ON c.oid = a.attrelid
                  JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = %s AND c.relname = %s
                   AND a.attname = 'q_1024_vec' AND a.attnum > 0 AND NOT a.attisdropped
                """,
                [gaussdb_env["schema"], table],
            ) == ("floatvector(1024)",)
            recovered_indexes = [
                str(row[0]).lower()
                for row in _fetch_all(
                    gaussdb_admin_conn,
                    "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                    [gaussdb_env["schema"], table],
                )
            ]
            assert sum(" using gsdiskann " in definition for definition in recovered_indexes) == 1
            assert len(_ugin_index_defs(recovered_indexes, "simple")) == 1
            assert len(_ugin_index_defs(recovered_indexes, "ngram")) == 1
            assert conn.insert(
                [{"id": f"recovery-{dim}", "kb_id": f"kb-wrt-009-recovery-{dim}", "q_1024_vec": [0.0] * 1024}],
                table,
                f"kb-wrt-009-recovery-{dim}",
            ) == []
            assert conn.get(f"recovery-{dim}", table, [f"kb-wrt-009-recovery-{dim}"])["q_1024_vec_valid"] is True
        return

    for dim in (1024, 1536, 3072):
        table = table_name(gaussdb_env, f"wrt_009_{dim}")
        assert conn.create_idx(table, f"kb-wrt-009-{dim}", dim) is True
        index_defs = [
            str(row[0]).lower()
            for row in _fetch_all(
                gaussdb_admin_conn,
                "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
                [gaussdb_env["schema"], table],
            )
        ]
        diskann_indexes = [definition for definition in index_defs if " using gsdiskann " in definition]
        assert len(diskann_indexes) == 1
        assert len(_ugin_index_defs(index_defs, "simple")) == 1
        assert len(_ugin_index_defs(index_defs, "ngram")) == 1
        assert (f"enable_vector_copy=false" in diskann_indexes[0]) is (dim > 1024)
        assert _fetch_one(
            gaussdb_admin_conn,
            """
            SELECT format_type(a.atttypid, a.atttypmod)
              FROM pg_attribute a
              JOIN pg_class c ON c.oid = a.attrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relname = %s
               AND a.attname = %s AND a.attnum > 0 AND NOT a.attisdropped
            """,
            [gaussdb_env["schema"], table, f"q_{dim}_vec"],
        ) == (f"floatvector({dim})",)


def test_tc_wrt_011_create_idx_advisory_lock_serializes_live_creates(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_011_lock")
    results = {}
    errors = {}
    distributed = gaussdb_env["variant_evidence"].get("variant") == "distributed"
    barrier = threading.Barrier(3)

    def worker(label):
        try:
            barrier.wait(timeout=30)
            results[label] = GaussDBConnection().create_idx(table, "kb-wrt-011", 1024)
        except BaseException as exc:  # keep both worker failures visible to the assertion
            errors[label] = exc

    threads = [threading.Thread(target=worker, args=(label,), name=f"wrt-011-{label}", daemon=True) for label in ("t1", "t2")]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=30)
    deadline = time.monotonic() + (180 if distributed else 60)
    for thread in threads:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    assert [thread.name for thread in threads if thread.is_alive()] == []
    assert errors == {}
    assert results == {"t1": True, "t2": True}
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    )[0] == 1
    columns = {
        row[0]
        for row in _fetch_all(
            gaussdb_admin_conn,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            [gaussdb_env["schema"], table],
        )
    }
    assert len(columns) == 46
    assert {"id", "kb_id", "q_1024_vec", "q_1024_vec_valid", "chunk_data", "metadata"} <= columns
    primary_key_columns = _fetch_all(
        gaussdb_admin_conn,
        """
        SELECT kcu.column_name, kcu.ordinal_position
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_schema = tc.constraint_schema
           AND kcu.constraint_name = tc.constraint_name
         WHERE tc.table_schema = %s
           AND tc.table_name = %s
           AND tc.constraint_type = 'PRIMARY KEY'
         ORDER BY kcu.ordinal_position
        """,
        [gaussdb_env["schema"], table],
    )
    assert primary_key_columns == [("kb_id", 1), ("id", 2)]
    assert {
        row[0]
        for row in _fetch_all(
            gaussdb_admin_conn,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_name LIKE %s",
            [gaussdb_env["schema"], f"{table}%"],
        )
    } == {table}
    index_defs = [
        str(row[0]).lower()
        for row in _fetch_all(
            gaussdb_admin_conn,
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            [gaussdb_env["schema"], table],
        )
    ]
    assert len(index_defs) == 9
    assert len(_ugin_index_defs(index_defs, "simple")) == 1
    assert len(_ugin_index_defs(index_defs, "ngram")) == 1
    assert sum(" using gsdiskann " in definition for definition in index_defs) == 1
    assert sum(" using ubtree " in definition for definition in index_defs) == 6
    for column in ("doc_id", "available_int", "knowledge_graph_kwd", "entity_type_kwd", "removed_kwd"):
        assert sum(f"({column})" in definition for definition in index_defs) == 1
    reloptions = _fetch_one(
        gaussdb_admin_conn,
        """
        SELECT c.reloptions
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND c.relname = %s
        """,
        [gaussdb_env["schema"], table],
    )[0]
    assert "storage_type=ustore" in str(reloptions).lower()

    reusable = GaussDBConnection()
    assert reusable.insert(
        [
            {
                "id": "post-concurrency",
                "kb_id": "kb-wrt-011",
                "content_with_weight": "connection pool remains writable",
                "q_1024_vec": [0.0] * 1024,
            }
        ],
        table,
        "kb-wrt-011",
    ) == []
    assert reusable.get("post-concurrency", table, ["kb-wrt-011"])["content_with_weight"] == (
        "connection pool remains writable"
    )
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT content_with_weight, q_1024_vec_valid FROM {} WHERE kb_id = %s AND id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["kb-wrt-011", "post-concurrency"],
    ) == ("connection pool remains writable", True)


def test_tc_wrt_102_insert_writes_vector_and_default_state(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_102")
    kb_id = "kb-wrt-102"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert(
        [{"id": "c1", "kb_id": kb_id, "q_1024_vec": [0.1] * 1024, "content_with_weight": "x"}], table, kb_id
    ) == []
    row = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, q_1024_vec_valid, available_int, removed_kwd FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", kb_id],
    )
    assert row == ("c1", kb_id, True, 1, "N")


def test_tc_wrt_103_insert_aligns_heterogeneous_batch_columns(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_103")
    kb_id = "kb-wrt-103"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert(
        [
            {"id": "c1", "kb_id": kb_id, "q_1024_vec": [0.1] * 1024},
            {"id": "c2", "kb_id": kb_id, "important_kwd": ["x"]},
        ],
        table,
        kb_id,
    ) == []
    rows = _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, q_1024_vec, q_1024_vec_valid, important_kwd FROM {} WHERE kb_id = %s ORDER BY id").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        [kb_id],
    )
    assert [row[0] for row in rows] == ["c1", "c2"]
    assert rows[0][2] is True
    assert rows[1][2] is False
    assert rows[1][3] == ["x"]
    placeholder = _decode_vector(rows[1][1])
    assert len(placeholder) == 1024
    assert set(float(value) for value in placeholder) == {0.0}


def test_tc_wrt_104_insert_upsert_overwrites_same_kb_chunk(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_104")
    kb_id = "kb-wrt-104"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert([{"id": "c1", "kb_id": kb_id, "q_1024_vec": [0.1] * 1024, "content_with_weight": "old", "docnm_kwd": "OldDoc"}], table, kb_id) == []
    assert conn.insert([{"id": "c1", "kb_id": kb_id, "q_1024_vec": [0.2] * 1024, "content_with_weight": "new", "docnm_kwd": "NewDoc"}], table, kb_id) == []
    row = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT content_with_weight, docnm_kwd, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", kb_id],
    )
    assert row == ("new", "NewDoc", True)
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT COUNT(*) FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    )[0] == 1


def test_tc_wrt_105_insert_keeps_same_chunk_id_across_kbs(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_105")
    kb_a, kb_b = "kb-wrt-105-a", "kb-wrt-105-b"
    assert conn.create_idx(table, kb_a, 1024) is True
    assert conn.insert([{"id": "c1", "kb_id": kb_a, "q_1024_vec": [0.1] * 1024, "content_with_weight": "kb1 content"}], table, kb_a) == []
    assert conn.insert([{"id": "c1", "kb_id": kb_b, "q_1024_vec": [0.2] * 1024, "content_with_weight": "kb2 content"}], table, kb_b) == []
    rows = _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, content_with_weight, q_1024_vec_valid FROM {} WHERE id = %s ORDER BY kb_id").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1"],
    )
    assert rows == [
        ("c1", kb_a, "kb1 content", True),
        ("c1", kb_b, "kb2 content", True),
    ]
    assert len(rows) == 2


def test_tc_wrt_106_insert_extracts_metadata_and_infers_batch_dimension(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_106")
    kb_id = "kb-wrt-106"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert(
        [
            {"id": "c1", "kb_id": kb_id, "doc_id": "d1", "metadata": {"_group_id": "g1", "_title": "Doc", "other": 1}, "q_1024_vec": [0.1] * 1024},
            {"id": "c2", "kb_id": kb_id, "doc_id": "d2"},
        ],
        table,
        kb_id,
    ) == []
    row = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT group_id, docnm_kwd, metadata, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", kb_id],
    )
    assert row == ("g1", "Doc", {"_group_id": "g1", "_title": "Doc", "other": 1}, True)
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c2", kb_id],
    )[0] is False


def test_tc_wrt_107_insert_merges_unknown_fields_into_extra(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_107")
    kb_id = "kb-wrt-107"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert([{"id": "c1", "kb_id": kb_id, "q_1024_vec": [0.1] * 1024, "custom_field1": "value1", "custom_field2": 2}], table, kb_id) == []
    row = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT extra, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    )
    assert row == ({"custom_field1": "value1", "custom_field2": 2}, True)


def test_tc_wrt_108_insert_normalizes_single_kb_id_list_to_scalar(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_108")
    kb_id = "kb-wrt-108"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert(
        [
            {
                "id": "c1",
                "kb_id": [kb_id],
                "q_1024_vec": [0.1] * 1024,
                "content_with_weight": "test",
            }
        ],
        table,
        kb_id,
    ) == []
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL(
            "SELECT id, kb_id, content_with_weight, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s"
        ).format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", kb_id],
    ) == ("c1", kb_id, "test", True)


def test_tc_wrt_112_insert_rejects_mismatched_vector_dimension_without_row(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection, vector_literal

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_112")
    kb_id = "kb-wrt-112"
    assert conn.create_idx(table, kb_id, 1024) is True
    invalid_vector = [0.1] * 512
    result = conn.insert([{"id": "c1", "kb_id": kb_id, "q_1024_vec": invalid_vector}], table, kb_id)
    assert result == ["c1"]
    with pytest.raises(ValueError, match=r"vector dimension mismatch: expected 1024, got 512"):
        vector_literal(invalid_vector, 1024)
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT COUNT(*) FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    )[0] == 0


def test_tc_wrt_114_insert_overwrites_jsonb_columns_as_whole_values(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_114")
    kb_id = "kb-wrt-114"
    assert conn.create_idx(table, kb_id, 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "c1",
                "kb_id": kb_id,
                "chunk_data": {"stale": "remove", "key": "old"},
                "extra": {"stale": True, "x": 0},
                "q_1024_vec": [0.2] * 1024,
                "q_1024_vec_valid": True,
            }
        ],
    )
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT chunk_data, extra FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    ) == ({"stale": "remove", "key": "old"}, {"stale": True, "x": 0})
    assert conn.insert([{"id": "c1", "kb_id": kb_id, "q_1024_vec": [0.1] * 1024, "chunk_data": {"key": "val"}, "extra": {"x": 1}}], table, kb_id) == []
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT chunk_data, extra, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    ) == ({"key": "val"}, {"x": 1}, True)


def test_tc_wrt_201_insert_writes_invalid_zero_vector_placeholder(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_201")
    kb_id = "kb-wrt-201"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert([{"id": "c2", "kb_id": kb_id, "content_with_weight": "mother"}], table, kb_id) == []
    row = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c2", kb_id],
    )
    assert row[1] is False
    vector = row[0]
    if isinstance(vector, str):
        vector = vector.strip("[]").split(",")
    assert len(vector) == 1024
    assert set(float(value) for value in vector) == {0.0}


def test_tc_wrt_202_insert_rejects_placeholder_when_dimension_cannot_be_inferred(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_202")
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, content_with_weight TEXT, PRIMARY KEY (kb_id, id))").format(
                _qualified(gaussdb_env["schema"], table)
            )
        )
    gaussdb_admin_conn.commit()
    conn = GaussDBConnection()
    result = conn.insert([{"id": "c2", "kb_id": "kb-wrt-202", "content_with_weight": "mother"}], table, "kb-wrt-202")
    assert result == ["c2"]
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT COUNT(*) FROM {} WHERE id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c2"],
    )[0] == 0


def test_tc_wrt_203_placeholder_is_excluded_from_vector_search(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_203")
    kb_id = "kb-wrt-203"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert([{"id": "c2", "kb_id": kb_id, "content_with_weight": "mother"}], table, kb_id) == []
    result = conn.search(
        ["id"], [], {"kb_id": kb_id}, [MatchDenseExpr("q_1024_vec", [0.1] * 1024, "float", "cosine", 10)], OrderByExpr(), 0, 10, table, [kb_id]
    )
    assert result.total == 0
    assert result.chunks == []
    stored = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c2", kb_id],
    )
    assert stored[1] is False
    assert _decode_vector(stored[0]) == pytest.approx([0.0] * 1024)


def test_tc_wrt_204_get_omits_placeholder_vector_but_keeps_valid_flag(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_204")
    kb_id = "kb-wrt-204"
    assert conn.create_idx(table, kb_id, 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c2", "kb_id": kb_id, "content_with_weight": "mother"}],
    )
    result = conn.get("c2", table, [kb_id])
    assert result is not None
    assert result["id"] == "c2"
    assert result["kb_id"] == kb_id
    assert result["content_with_weight"] == "mother"
    assert result["q_1024_vec_valid"] is False
    assert "q_1024_vec" not in result
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c2", kb_id],
    ) == (False,)


def test_tc_wrt_205_upserted_vector_changes_placeholder_to_searchable(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_205")
    kb_id = "kb-wrt-205"
    assert conn.create_idx(table, kb_id, 1024) is True
    assert conn.insert([{"id": "c2", "kb_id": kb_id, "content_with_weight": "mother"}], table, kb_id) == []
    placeholder = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c2", kb_id],
    )
    assert placeholder[1] is False
    assert _decode_vector(placeholder[0]) == pytest.approx([0.0] * 1024)
    assert conn.insert([{"id": "c2", "kb_id": kb_id, "content_with_weight": "mother", "q_1024_vec": [0.1] * 1024}], table, kb_id) == []
    stored = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c2", kb_id],
    )
    assert stored[1] is True
    assert _decode_vector(stored[0]) == pytest.approx([0.1] * 1024)
    result = conn.search(
        ["id"], [], {"kb_id": kb_id}, [MatchDenseExpr("q_1024_vec", [0.1] * 1024, "float", "cosine", 10)], OrderByExpr(), 0, 10, table, [kb_id]
    )
    assert result.total == 1
    assert [row["id"] for row in result.chunks] == ["c2"]


def test_tc_wrt_301_doc_meta_insert_persists_jsonb(gaussdb_env, register_table, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = f"ragflow_doc_meta_{gaussdb_env['table_prefix']}_wrt_301"
    register_table(table)
    assert conn.create_doc_meta_idx(table) is True
    assert conn.insert([{"id": "d1", "kb_id": "kb-wrt-301", "meta_fields": {"status": "active"}}], table, "kb-wrt-301") == []
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, meta_fields FROM {} WHERE id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["d1"],
    ) == ("d1", "kb-wrt-301", {"status": "active"})
    assert _fetch_all(
        gaussdb_admin_conn,
        """
        SELECT tc.constraint_type, kcu.column_name, kcu.ordinal_position
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage kcu
            ON kcu.constraint_schema = tc.constraint_schema
           AND kcu.constraint_name = tc.constraint_name
         WHERE tc.table_schema = %s AND tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY'
         ORDER BY kcu.ordinal_position
        """,
        [gaussdb_env["schema"], table],
    ) == [("PRIMARY KEY", "id", 1)]
    index_defs = [
        row[0].lower()
        for row in _fetch_all(
            gaussdb_admin_conn,
            "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
            [gaussdb_env["schema"], table],
        )
    ]
    assert any(" using ubtree " in definition and "(kb_id)" in definition for definition in index_defs)


def test_tc_wrt_404_get_returns_single_kb_chunk(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_404")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "c1",
                "kb_id": "k1",
                "content_with_weight": "test content",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
                "q_4_vec_valid": True,
            }
        ],
    )
    result = conn.get("c1", table, ["k1"])
    assert result is not None
    assert result["id"] == "c1"
    assert result["kb_id"] == "k1"
    assert result["content_with_weight"] == "test content"
    assert result["q_4_vec"] == pytest.approx([0.1, 0.2, 0.3, 0.4])
    assert result["q_4_vec_valid"] is True


def test_tc_wrt_405_get_rejects_cross_kb_duplicate_chunk_id(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection, GaussDBError

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_405")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "q_4_vec": [0.1] * 4, "q_4_vec_valid": True},
            {"id": "c1", "kb_id": "k2", "q_4_vec": [0.2] * 4, "q_4_vec_valid": True},
        ],
    )
    with pytest.raises(GaussDBError, match="cross-KB duplicate chunk id: c1"):
        conn.get("c1", table, ["k1", "k2"])
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id FROM {} WHERE id = %s ORDER BY kb_id").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1"],
    ) == [("c1", "k1"), ("c1", "k2")]


def test_tc_wrt_406_get_omits_invalid_placeholder_vector(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_406")
    assert conn.create_idx(table, "k1", 1024) is True
    _insert_rows(gaussdb_admin_conn, gaussdb_env["schema"], table, [{"id": "c1", "kb_id": "k1"}])
    result = conn.get("c1", table, ["k1"])
    assert result["id"] == "c1"
    assert result["kb_id"] == "k1"
    assert result["q_1024_vec_valid"] is False
    assert "q_1024_vec" not in result


def test_tc_wrt_407_get_decodes_jsonb_and_vector_values(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_407")
    assert conn.create_idx(table, "k1", 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "c1",
                "kb_id": "k1",
                "doc_id": "d1",
                "content_with_weight": "fixed content",
                "chunk_data": {"key": "val"},
                "extra": {"x": 1},
                "metadata": {"status": "active"},
                "q_1024_vec": [0.1] * 1024,
                "q_1024_vec_valid": True,
            }
        ],
    )
    result = conn.get("c1", table, ["k1"])
    assert result["chunk_data"] == {"key": "val"}
    assert result["extra"] == {"x": 1}
    assert result["metadata"] == {"status": "active"}
    assert isinstance(result["q_1024_vec"], list)
    assert result["q_1024_vec"] == pytest.approx([0.1] * 1024)
    assert result["q_1024_vec_valid"] is True
    stored = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL(
            "SELECT id, kb_id, doc_id, content_with_weight, chunk_data, extra, metadata, q_1024_vec, q_1024_vec_valid "
            "FROM {} WHERE id = %s AND kb_id = %s"
        ).format(_qualified(gaussdb_env["schema"], table)),
        ["c1", "k1"],
    )
    assert stored[:7] == ("c1", "k1", "d1", "fixed content", {"key": "val"}, {"x": 1}, {"status": "active"})
    assert _decode_vector(stored[7]) == pytest.approx([0.1] * 1024)
    assert stored[8] is True


def test_tc_wrt_408_search_orders_page_position_and_top(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import OrderByExpr
    from common.doc_store.gaussdb_conn_base import GaussDBSearchBuilder

    table = table_name(gaussdb_env, "wrt_408")
    kb_id = "k1"
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, "
                "page_num_int JSONB, position_int JSONB, top_int JSONB, content_with_weight TEXT, "
                "PRIMARY KEY (kb_id, id))"
            ).format(_qualified(gaussdb_env["schema"], table))
        )
    gaussdb_admin_conn.commit()
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": kb_id, "page_num_int": [2], "position_int": [1, 5], "top_int": [10], "content_with_weight": "page2"},
            {"id": "c2", "kb_id": kb_id, "page_num_int": [1], "position_int": [2, 3], "top_int": [20], "content_with_weight": "page1"},
            {"id": "c3", "kb_id": kb_id, "page_num_int": [1], "position_int": [1, 3], "top_int": [15], "content_with_weight": "page1_pos1"},
        ],
    )
    builder = GaussDBSearchBuilder(schema=gaussdb_env["schema"])
    sql_text, params = builder.build_search_sql(
        table=table,
        select_fields=["id", "page_num_int", "position_int", "top_int"],
        condition={"kb_id": kb_id},
        keywords=[],
        vector=None,
        vector_dim=None,
        vector_weight=0.0,
        similarity_threshold=None,
        topn=None,
        offset=0,
        limit=10,
        highlight_fields=None,
        order_by=OrderByExpr().asc("page_num_int"),
        pagerank_weight=0.0,
    )
    order_clause = sql_text.split(" ORDER BY ", 1)[1].split(" LIMIT ", 1)[0]
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(sql_text, params)
        rows = cur.fetchall()
    assert order_clause == (
        "COALESCE((page_num_int #>> '{0}')::int, 100000000) ASC, "
        "COALESCE((position_int #>> '{0,3}')::int, 100000000) ASC, "
        "COALESCE((top_int #>> '{0}')::int, 100000000) ASC"
    )
    assert params == [kb_id, 10, 0]
    assert [row[0] for row in rows] == ["c3", "c2", "c1"]


def test_tc_wrt_503_update_changes_scoped_normal_column(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_503")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "available_int": 1},
            {"id": "c1", "kb_id": "k2", "available_int": 1},
        ],
    )
    assert conn.update({"id": "c1", "kb_id": "k1"}, {"available_int": 0}, table, "k1") is True
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT kb_id, available_int FROM {} WHERE id = %s ORDER BY kb_id").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1"],
    ) == [("k1", 0), ("k2", 1)]

    constraint = f"ck_{gaussdb_env['table_prefix'][:20]}_wrt503"
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK (available_int <> 99)").format(
                _qualified(gaussdb_env["schema"], table),
                sql.Identifier(constraint),
            )
        )
    gaussdb_admin_conn.commit()
    assert conn.update(
        {"id": "c1", "kb_id": "k1"},
        {"available_int": 99, "doc_id": "must-not-commit"},
        table,
        "k1",
    ) is False
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT available_int, doc_id FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", "k1"],
    ) == (0, None)
    assert conn.update({"id": "c1", "kb_id": "k1"}, {"doc_id": "connection-reused"}, table, "k1") is True
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT available_int, doc_id FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", "k1"],
    ) == (0, "connection-reused")


def test_tc_wrt_504_update_overwrites_metadata_and_derives_title(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_504")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1", "metadata": {"_title": "Old", "x": 0}, "docnm_kwd": "Old"}],
    )
    assert conn.update({"id": "c1", "kb_id": "k1"}, {"metadata": {"_title": "New", "x": 1}}, table, "k1") is True
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT metadata, docnm_kwd FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", "k1"],
    ) == ({"_title": "New", "x": 1}, "New")


def test_tc_wrt_505_update_remove_string_sets_jsonb_column_null(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_505")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1", "important_kwd": ["x", "y"], "content_with_weight": "test"}],
    )
    assert conn.update({"id": "c1", "kb_id": "k1"}, {"remove": "important_kwd"}, table, "k1") is True
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT important_kwd, content_with_weight FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", "k1"],
    ) == (None, "test")


def test_tc_wrt_506_update_remove_dict_deletes_jsonb_element(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_506")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1", "tag_kwd": ["t1", "t2", "t3"]}],
    )
    assert conn.update({"id": "c1", "kb_id": "k1"}, {"remove": {"tag_kwd": "t1"}}, table, "k1") is True
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT tag_kwd FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", "k1"],
    )[0] == ["t2", "t3"]


def test_tc_wrt_507_update_add_dict_appends_jsonb_element(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_507")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "new-member", "kb_id": "k1", "tag_kwd": ["t1", "t2"]},
            {"id": "existing-member", "kb_id": "k1", "tag_kwd": ["t1", "t2"]},
            {"id": "rename-target", "kb_id": "k1", "tag_kwd": ["A", "B"]},
            {"id": "other-kb", "kb_id": "k2", "tag_kwd": ["A", "B"]},
        ],
    )

    assert conn.update(
        {"id": "new-member", "kb_id": "k1"},
        {"add": {"tag_kwd": "t3"}},
        table,
        "k1",
    ) is True
    assert conn.update(
        {"id": "existing-member", "kb_id": "k1"},
        {"add": {"tag_kwd": "t2"}},
        table,
        "k1",
    ) is True
    assert conn.update(
        {"id": "existing-member", "kb_id": "k1"},
        {"add": {"tag_kwd": "t2"}},
        table,
        "k1",
    ) is True

    rename_condition = {"tag_kwd": "A", "kb_id": ["k1"]}
    rename_value = {"remove": {"tag_kwd": "A"}, "add": {"tag_kwd": "B"}}
    assert conn.update(rename_condition, rename_value, table, "k1") is True
    assert conn.update(rename_condition, rename_value, table, "k1") is True

    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, tag_kwd FROM {} ORDER BY kb_id, id").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        [],
    ) == [
        ("existing-member", "k1", ["t1", "t2"]),
        ("new-member", "k1", ["t1", "t2", "t3"]),
        ("rename-target", "k1", ["B"]),
        ("other-kb", "k2", ["A", "B"]),
    ]


def test_tc_wrt_602_delete_removes_id_list_only_in_scoped_kb(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_602")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1"}, {"id": "c2", "kb_id": "k1"}, {"id": "c3", "kb_id": "k2"}],
    )
    assert conn.delete({"id": ["c1", "c2"]}, table, "k1") == 2
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == [("c3", "k2")]


def test_tc_wrt_603_delete_removes_all_chunks_for_doc_id(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_603")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "doc_id": "d1"},
            {"id": "c2", "kb_id": "k1", "doc_id": "d1"},
            {"id": "c3", "kb_id": "k1", "doc_id": "d2"},
        ],
    )
    assert conn.delete({"doc_id": "d1"}, table, "k1") == 2
    assert _fetch_all(gaussdb_admin_conn, sql.SQL("SELECT id FROM {} WHERE kb_id = %s ORDER BY id").format(_qualified(gaussdb_env["schema"], table)), ["k1"]) == [("c3",)]


def test_tc_wrt_604_delete_kb_scope_preserves_other_kb(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_604")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1"}, {"id": "c2", "kb_id": "k1"}, {"id": "c3", "kb_id": "k2"}],
    )
    assert conn.delete({}, table, "k1") == 0
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT COUNT(*) FROM {}").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == (3,)
    assert conn.delete({"kb_id": "k1"}, table, None) == 2
    assert _fetch_all(gaussdb_admin_conn, sql.SQL("SELECT id, kb_id FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)), []) == [("c3", "k2")]


def test_tc_wrt_605_delete_graph_rag_condition(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_605")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "knowledge_graph_kwd": "graph_node"},
            {"id": "c2", "kb_id": "k1", "knowledge_graph_kwd": "graph_edge"},
            {"id": "c3", "kb_id": "k1", "knowledge_graph_kwd": None},
            {"id": "c4", "kb_id": "k2", "knowledge_graph_kwd": "graph_node"},
        ],
    )
    assert conn.delete({"knowledge_graph_kwd": "graph_node"}, table, "k1") == 1
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == [("c2", "k1"), ("c3", "k1"), ("c4", "k2")]


def test_tc_wrt_606_delete_raptor_condition(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_606")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "raptor_kwd": "raptor_node", "raptor_layer_int": 1},
            {"id": "c2", "kb_id": "k1", "raptor_kwd": "raptor_node", "raptor_layer_int": 2},
            {"id": "c3", "kb_id": "k1", "raptor_kwd": None, "raptor_layer_int": None},
        ],
    )
    assert conn.delete({"raptor_kwd": "raptor_node"}, table, "k1") == 2
    assert _fetch_all(gaussdb_admin_conn, sql.SQL("SELECT id FROM {} WHERE kb_id = %s ORDER BY id").format(_qualified(gaussdb_env["schema"], table)), ["k1"]) == [("c3",)]


def test_tc_wrt_607_delete_must_not_exists_condition(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_607")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "tag_kwd": ["x"]},
            {"id": "c2", "kb_id": "k1", "tag_kwd": None},
            {"id": "c3", "kb_id": "k1", "tag_kwd": []},
        ],
    )
    assert conn.delete({"must_not": {"exists": "tag_kwd"}}, table, "k1") == 1
    assert _fetch_all(gaussdb_admin_conn, sql.SQL("SELECT id FROM {} WHERE kb_id = %s ORDER BY id").format(_qualified(gaussdb_env["schema"], table)), ["k1"]) == [("c1",), ("c3",)]


def test_tc_wrt_608_delete_exists_condition(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_608")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "tag_kwd": ["x"]},
            {"id": "c2", "kb_id": "k1", "tag_kwd": None},
            {"id": "c3", "kb_id": "k2", "tag_kwd": ["x"]},
        ],
    )
    assert conn.delete({"exists": "tag_kwd"}, table, "k1") == 1
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, tag_kwd FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == [("c2", "k1", None), ("c3", "k2", ["x"])]


def test_tc_wrt_611_delete_jsonb_contains_value(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_611")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "tag_kwd": ["t1", "t2"]},
            {"id": "c2", "kb_id": "k1", "tag_kwd": ["t1"]},
            {"id": "c3", "kb_id": "k1", "tag_kwd": ["t2"]},
            {"id": "c4", "kb_id": "k2", "tag_kwd": ["t1"]},
        ],
    )
    assert conn.delete({"tag_kwd": ["t1"]}, table, "k1") == 2
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, tag_kwd FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == [("c3", "k1", ["t2"]), ("c4", "k2", ["t1"])]


def test_tc_wrt_612_delete_doc_id_compensates_cancelled_task(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_612")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {"id": "c1", "kb_id": "k1", "doc_id": "d1"},
            {"id": "c2", "kb_id": "k1", "doc_id": "d1"},
            {"id": "c3", "kb_id": "k2", "doc_id": "d1"},
        ],
    )
    assert conn.delete({"doc_id": "d1"}, table, "k1") == 2
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, doc_id FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == [("c3", "k2", "d1")]

    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "protected", "kb_id": "k1", "doc_id": "d2", "content_with_weight": "must survive failed delete"}],
    )
    function_name = f"wrt612_fail_delete_{gaussdb_env['table_prefix'][:20]}"
    trigger_name = f"trg_wrt612_{gaussdb_env['table_prefix'][:20]}"
    try:
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE FUNCTION {}.{}() RETURNS trigger AS "
                    "$body$ BEGIN RAISE EXCEPTION 'forced integration delete failure'; END; $body$ LANGUAGE plpgsql"
                ).format(
                    sql.Identifier(gaussdb_env["schema"]),
                    sql.Identifier(function_name),
                )
            )
            cur.execute(
                sql.SQL("CREATE TRIGGER {} BEFORE DELETE ON {} FOR EACH ROW EXECUTE PROCEDURE {}.{}()").format(
                    sql.Identifier(trigger_name),
                    _qualified(gaussdb_env["schema"], table),
                    sql.Identifier(gaussdb_env["schema"]),
                    sql.Identifier(function_name),
                )
            )
        gaussdb_admin_conn.commit()

        assert conn.delete({"doc_id": "d2"}, table, "k1") == 0
        assert _fetch_all(
            gaussdb_admin_conn,
            sql.SQL("SELECT id, kb_id, doc_id, content_with_weight FROM {} ORDER BY kb_id, id").format(
                _qualified(gaussdb_env["schema"], table)
            ),
            [],
        ) == [
            ("protected", "k1", "d2", "must survive failed delete"),
            ("c3", "k2", "d1", None),
        ]
    finally:
        gaussdb_admin_conn.rollback()
        with gaussdb_admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL("DROP TRIGGER IF EXISTS {} ON {}").format(
                    sql.Identifier(trigger_name),
                    _qualified(gaussdb_env["schema"], table),
                )
            )
            cur.execute(
                sql.SQL("DROP FUNCTION IF EXISTS {}.{}()").format(
                    sql.Identifier(gaussdb_env["schema"]),
                    sql.Identifier(function_name),
                )
            )
        gaussdb_admin_conn.commit()

    assert _fetch_one(
        gaussdb_admin_conn,
        """
        SELECT COUNT(*)
          FROM pg_proc p
          JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = %s AND p.proname = %s
        """,
        [gaussdb_env["schema"], function_name],
    ) == (0,)
    assert conn.delete({"doc_id": "d2"}, table, "k1") == 1
    assert conn.insert(
        [{"id": "post-delete-failure", "kb_id": "k1", "content_with_weight": "connection reused"}],
        table,
        "k1",
    ) == []
    assert conn.get("post-delete-failure", table, ["k1"])["content_with_weight"] == "connection reused"


def test_tc_wrt_701_delete_idx_dataset_cleans_only_selected_kb(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_701")
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1"}, {"id": "c2", "kb_id": "k2"}],
    )
    conn.delete_idx(table, "k1")
    assert _fetch_all(gaussdb_admin_conn, sql.SQL("SELECT id, kb_id FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)), []) == [("c2", "k2")]
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (1,)


def test_tc_wrt_702_delete_idx_without_dataset_drops_table(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_702")
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, "
                "content_with_weight TEXT, PRIMARY KEY (kb_id, id))"
            ).format(_qualified(gaussdb_env["schema"], table))
        )
    gaussdb_admin_conn.commit()
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c1", "kb_id": "k1", "content_with_weight": "content"}],
    )

    GaussDBConnection().delete_idx(table, None)

    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (0,)
    gaussdb_admin_conn.rollback()
    with pytest.raises(psycopg2.Error) as exc_info:
        _fetch_one(
            gaussdb_admin_conn,
            sql.SQL("SELECT COUNT(*) FROM {}").format(_qualified(gaussdb_env["schema"], table)),
            [],
        )
    assert exc_info.value.pgcode in {"42P01", "42P18"}
    assert "does not exist" in str(exc_info.value).lower()
    gaussdb_admin_conn.rollback()


def test_tc_wrt_703_delete_idx_metadata_does_not_drop_chunk_table(gaussdb_env, table_name, register_table, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    chunk_table = table_name(gaussdb_env, "wrt_703_chunk")
    meta_table = f"ragflow_doc_meta_{gaussdb_env['table_prefix']}_wrt_703"
    register_table(meta_table)
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, "
                "content_with_weight TEXT, PRIMARY KEY (kb_id, id))"
            ).format(_qualified(gaussdb_env["schema"], chunk_table))
        )
        cur.execute(
            sql.SQL(
                "CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, "
                "meta_fields JSONB, PRIMARY KEY (id))"
            ).format(_qualified(gaussdb_env["schema"], meta_table))
        )
    gaussdb_admin_conn.commit()
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        chunk_table,
        [{"id": "c1", "kb_id": "k1", "content_with_weight": "chunk canary"}],
    )
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        meta_table,
        [{"id": "d1", "kb_id": "k1", "meta_fields": {"status": "active"}}],
    )

    GaussDBConnection().delete_idx(meta_table, None)

    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], meta_table],
    ) == (0,)
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], chunk_table],
    ) == (1,)
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, content_with_weight FROM {} WHERE id = %s").format(
            _qualified(gaussdb_env["schema"], chunk_table)
        ),
        ["c1"],
    ) == ("c1", "k1", "chunk canary")


def test_tc_wrt_704_delete_multiple_kbs_uses_single_in_and_keeps_table(
    gaussdb_env, table_name, gaussdb_admin_conn
):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_704")
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, "
                "content_with_weight TEXT, PRIMARY KEY (kb_id, id))"
            ).format(_qualified(gaussdb_env["schema"], table))
        )
        cur.executemany(
            sql.SQL("INSERT INTO {} (id, kb_id, content_with_weight) VALUES (%s, %s, %s)").format(
                _qualified(gaussdb_env["schema"], table)
            ),
            [("c1", "k1", "kb1 chunk"), ("c2", "k2", "kb2 chunk"), ("c3", "k3", "kb3 chunk")],
        )
    gaussdb_admin_conn.commit()

    assert conn.delete({"kb_id": ["k1", "k2"]}, table, None) == 2

    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id FROM {} ORDER BY kb_id, id").format(_qualified(gaussdb_env["schema"], table)),
        [],
    ) == [("c3", "k3")]
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (1,)


def test_tc_wrt_705_index_exist_is_true_for_created_table(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_705")
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, PRIMARY KEY (kb_id, id))").format(
                _qualified(gaussdb_env["schema"], table)
            )
        )
    gaussdb_admin_conn.commit()

    assert GaussDBConnection().index_exist(table) is True
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (1,)


def test_tc_wrt_706_index_exist_is_false_for_missing_table(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_706_missing")
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (0,)
    assert GaussDBConnection().index_exist(table) is False
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (0,)


def test_tc_wrt_707_index_exist_is_read_only_and_idempotent(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_707")
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "CREATE TABLE {} (id VARCHAR(256) NOT NULL, kb_id VARCHAR(256) NOT NULL, "
                "content_with_weight TEXT, PRIMARY KEY (kb_id, id))"
            ).format(_qualified(gaussdb_env["schema"], table))
        )
    gaussdb_admin_conn.commit()
    catalog_sql = (
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position"
    )
    before = _fetch_all(gaussdb_admin_conn, catalog_sql, [gaussdb_env["schema"], table])
    assert conn.index_exist(table, "k1") is True
    assert conn.index_exist(table, None) is True
    after = _fetch_all(gaussdb_admin_conn, catalog_sql, [gaussdb_env["schema"], table])
    assert after == before
    assert [column[0] for column in after] == ["id", "kb_id", "content_with_weight"]
    assert _fetch_one(
        gaussdb_admin_conn,
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
        [gaussdb_env["schema"], table],
    ) == (1,)


def test_tc_wrt_709_delete_idx_missing_table_is_idempotent_and_pool_recovers(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_709_missing")
    schema = gaussdb_env["schema"]
    table_count_sql = "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s"
    index_count_sql = "SELECT COUNT(*) FROM pg_indexes WHERE schemaname = %s AND tablename = %s"

    assert _fetch_one(gaussdb_admin_conn, table_count_sql, [schema, table]) == (0,)
    assert _fetch_one(gaussdb_admin_conn, index_count_sql, [schema, table]) == (0,)
    gaussdb_admin_conn.rollback()

    conn.delete_idx(table, "kb-wrt-709")
    conn.delete_idx(table, "kb-wrt-709")

    assert _fetch_one(gaussdb_admin_conn, table_count_sql, [schema, table]) == (0,)
    assert _fetch_one(gaussdb_admin_conn, index_count_sql, [schema, table]) == (0,)
    gaussdb_admin_conn.rollback()

    try:
        assert conn.create_idx(table, "kb-wrt-709", 4) is True
        assert conn.insert(
            [{"id": "recovery", "kb_id": "kb-wrt-709", "content_with_weight": "pool recovered"}],
            table,
            "kb-wrt-709",
        ) == []
        assert conn.get("recovery", table, ["kb-wrt-709"])["content_with_weight"] == "pool recovered"
        assert _fetch_one(
            gaussdb_admin_conn,
            sql.SQL("SELECT id, kb_id, content_with_weight FROM {} WHERE id = %s AND kb_id = %s").format(
                _qualified(schema, table)
            ),
            ["recovery", "kb-wrt-709"],
        ) == ("recovery", "kb-wrt-709", "pool recovered")
    finally:
        gaussdb_admin_conn.rollback()
        conn.delete_idx(table, None)

    assert _fetch_one(gaussdb_admin_conn, table_count_sql, [schema, table]) == (0,)
    assert _fetch_one(gaussdb_admin_conn, index_count_sql, [schema, table]) == (0,)


def test_tc_wrt_801_pagerank_repeated_calls_atomically_accumulate(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_801")
    conn = GaussDBConnection()
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(gaussdb_admin_conn, gaussdb_env["schema"], table, [{"id": "c1", "kb_id": "k1", "pagerank_fea": 50}])
    pagerank_sql = sql.SQL("SELECT pagerank_fea FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table))
    assert conn.adjust_chunk_pagerank_fea("c1", table, "k1", 5) is True
    assert _fetch_one(gaussdb_admin_conn, pagerank_sql, ["c1", "k1"]) == (55,)
    assert conn.adjust_chunk_pagerank_fea("c1", table, "k1", 5) is True
    assert _fetch_one(gaussdb_admin_conn, pagerank_sql, ["c1", "k1"]) == (60,)


def test_tc_wrt_802_pagerank_clamps_to_lower_and_upper_bounds(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_802")
    conn = GaussDBConnection()
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(gaussdb_admin_conn, gaussdb_env["schema"], table, [{"id": "c1", "kb_id": "k1", "pagerank_fea": 50}])
    assert conn.adjust_chunk_pagerank_fea("c1", table, "k1", 200, max_w=100) is True
    assert _fetch_one(gaussdb_admin_conn, sql.SQL("SELECT pagerank_fea FROM {} WHERE id = %s").format(_qualified(gaussdb_env["schema"], table)), ["c1"])[0] == 100
    assert conn.adjust_chunk_pagerank_fea("c1", table, "k1", -200, min_w=0) is True
    assert _fetch_one(gaussdb_admin_conn, sql.SQL("SELECT pagerank_fea FROM {} WHERE id = %s").format(_qualified(gaussdb_env["schema"], table)), ["c1"])[0] == 0


def test_tc_wrt_803_pagerank_treats_null_as_zero(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_803")
    conn = GaussDBConnection()
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(gaussdb_admin_conn, gaussdb_env["schema"], table, [{"id": "c1", "kb_id": "k1", "pagerank_fea": None}])
    assert conn.adjust_chunk_pagerank_fea("c1", table, "k1", 5) is True
    assert _fetch_one(gaussdb_admin_conn, sql.SQL("SELECT pagerank_fea FROM {} WHERE id = %s").format(_qualified(gaussdb_env["schema"], table)), ["c1"])[0] == 5


def test_tc_wrt_804_pagerank_uses_kb_and_chunk_id_ignoring_extra_kwargs(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    table = table_name(gaussdb_env, "wrt_804")
    conn = GaussDBConnection()
    assert conn.create_idx(table, "k1", 4) is True
    _insert_rows(gaussdb_admin_conn, gaussdb_env["schema"], table, [{"id": "c1", "kb_id": "k1", "pagerank_fea": 10}])
    assert conn.adjust_chunk_pagerank_fea("c1", table, "k1", 5, row_id="wrong", extra_param="ignored") is True
    assert _fetch_one(gaussdb_admin_conn, sql.SQL("SELECT pagerank_fea FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)), ["c1", "k1"])[0] == 15


def test_tc_wrt_1002_lifecycle_placeholder_is_not_retrieved_by_vector_search(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1002")
    kb_id = "kb-wrt-1002"
    assert conn.create_idx(table, kb_id, 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c2", "kb_id": kb_id, "doc_id": "d2", "content_with_weight": "mother"}],
    )
    stored = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c2", kb_id],
    )
    assert stored[1] is False
    assert _decode_vector(stored[0]) == pytest.approx([0.0] * 1024)
    chunk = conn.get("c2", table, [kb_id])
    assert chunk["q_1024_vec_valid"] is False
    assert "q_1024_vec" not in chunk
    result = conn.search(["id"], [], {"kb_id": kb_id}, [MatchDenseExpr("q_1024_vec", [0.1] * 1024, "float", "cosine", 10)], OrderByExpr(), 0, 10, table, [kb_id])
    assert result.total == 0
    assert result.chunks == []


def test_tc_wrt_1003_lifecycle_vector_backfill_enables_search(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchDenseExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1003")
    kb_id = "kb-wrt-1003"
    assert conn.create_idx(table, kb_id, 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [{"id": "c2", "kb_id": kb_id, "doc_id": "d2", "content_with_weight": "mother"}],
    )
    assert conn.insert([{"id": "c2", "kb_id": kb_id, "content_with_weight": "mother", "q_1024_vec": [0.1] * 1024}], table, kb_id) == []
    stored = _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c2", kb_id],
    )
    assert stored[1] is True
    assert _decode_vector(stored[0]) == pytest.approx([0.1] * 1024)
    chunk = conn.get("c2", table, [kb_id])
    assert chunk["q_1024_vec_valid"] is True
    assert chunk["q_1024_vec"] == pytest.approx([0.1] * 1024)
    result = conn.search(["id"], [], {"kb_id": kb_id}, [MatchDenseExpr("q_1024_vec", [0.1] * 1024, "float", "cosine", 10)], OrderByExpr(), 0, 10, table, [kb_id])
    assert result.total == 1
    assert [row["id"] for row in result.chunks] == ["c2"]


def test_tc_wrt_1004_lifecycle_delete_removes_get_text_vector_and_filter_visibility(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchTextExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1004")
    kb_id = "kb-wrt-1004"
    assert conn.create_idx(table, kb_id, 4) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "c1",
                "kb_id": kb_id,
                "doc_id": "d1",
                "content_with_weight": "hello",
                "content_ltks": "hello",
                "q_4_vec": [0.1, 0.2, 0.3, 0.4],
                "q_4_vec_valid": True,
            }
        ],
    )
    text_expr = MatchTextExpr(["content_ltks"], "hello", 10)
    vector_expr = MatchDenseExpr("q_4_vec", [0.1, 0.2, 0.3, 0.4], "float", "cosine", 10)
    before_get = conn.get("c1", table, [kb_id])
    before_text = conn.search(["id"], [], {"kb_id": kb_id}, [text_expr], OrderByExpr(), 0, 10, table, [kb_id])
    before_vector = conn.search(["id"], [], {"kb_id": kb_id}, [vector_expr], OrderByExpr(), 0, 10, table, [kb_id])
    before_hybrid = conn.search(
        ["id"],
        [],
        {"kb_id": kb_id},
        [
            text_expr,
            vector_expr,
            FusionExpr("weighted_sum", 10, {"weights": "0.5,0.5"}),
        ],
        OrderByExpr(),
        0,
        10,
        table,
        [kb_id],
    )
    before_filter = conn.search(["id"], [], {"kb_id": kb_id, "doc_id": "d1"}, [], OrderByExpr(), 0, 10, table, [kb_id])
    assert before_get["id"] == "c1"
    assert [result.chunks[0]["id"] for result in (before_text, before_vector, before_hybrid, before_filter)] == ["c1"] * 4

    assert conn.delete({"id": "c1"}, table, kb_id) == 1
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT COUNT(*) FROM {} WHERE id = %s AND kb_id = %s").format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    ) == (0,)
    assert conn.get("c1", table, [kb_id]) is None
    text_result = conn.search(["id"], [], {"kb_id": kb_id}, [text_expr], OrderByExpr(), 0, 10, table, [kb_id])
    vector_result = conn.search(["id"], [], {"kb_id": kb_id}, [vector_expr], OrderByExpr(), 0, 10, table, [kb_id])
    hybrid_result = conn.search(
        ["id"],
        [],
        {"kb_id": kb_id},
        [text_expr, vector_expr, FusionExpr("weighted_sum", 10, {"weights": "0.5,0.5"})],
        OrderByExpr(),
        0,
        10,
        table,
        [kb_id],
    )
    filter_result = conn.search(["id"], [], {"kb_id": kb_id, "doc_id": "d1"}, [], OrderByExpr(), 0, 10, table, [kb_id])
    assert [result.chunks for result in (text_result, vector_result, hybrid_result, filter_result)] == [[], [], [], []]


def test_tc_wrt_1005_lifecycle_rebuilds_same_doc_with_new_content(gaussdb_env, table_name, gaussdb_admin_conn):
    from common.doc_store.doc_store_base import MatchTextExpr, OrderByExpr
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1005")
    kb_id = "kb-wrt-1005"
    assert conn.create_idx(table, kb_id, 4) is True
    with gaussdb_admin_conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "INSERT INTO {} (id, kb_id, doc_id, content_with_weight, content_ltks, q_4_vec, q_4_vec_valid) "
                "VALUES (%s, %s, %s, %s, %s, %s::floatvector(4), TRUE)"
            ).format(_qualified(gaussdb_env["schema"], table)),
            ["c1", kb_id, "d1", "old", "old", "[0.1,0.1,0.1,0.1]"],
        )
    gaussdb_admin_conn.commit()

    assert conn.delete({"id": "c1"}, table, kb_id) == 1
    assert conn.insert(
        [
            {
                "id": "c1",
                "kb_id": kb_id,
                "doc_id": "d1",
                "content_with_weight": "new",
                "content_ltks": "new",
                "q_4_vec": [0.2, 0.2, 0.2, 0.2],
            }
        ],
        table,
        kb_id,
    ) == []

    rebuilt = conn.get("c1", table, [kb_id])
    assert rebuilt["content_with_weight"] == "new"
    assert rebuilt["content_ltks"] == "new"
    assert rebuilt["doc_id"] == "d1"
    assert _decode_vector(rebuilt["q_4_vec"]) == pytest.approx([0.2] * 4)

    result = conn.search(
        ["id", "doc_id", "content_with_weight", "content_ltks"],
        [],
        {"kb_id": kb_id, "doc_id": "d1"},
        [MatchTextExpr(["content_ltks"], "new", 10)],
        OrderByExpr(),
        0,
        10,
        table,
        [kb_id],
    )
    assert [(row["id"], row["doc_id"], row["content_with_weight"], row["content_ltks"]) for row in result.chunks] == [
        ("c1", "d1", "new", "new")
    ]
    old_result = conn.search(
        ["id"],
        [],
        {"kb_id": kb_id, "doc_id": "d1"},
        [MatchTextExpr(["content_ltks"], "old", 10)],
        OrderByExpr(),
        0,
        10,
        table,
        [kb_id],
    )
    assert old_result.chunks == []

    rows = _fetch_all(
        gaussdb_admin_conn,
        sql.SQL(
            "SELECT id, kb_id, doc_id, content_with_weight, content_ltks, q_4_vec, q_4_vec_valid "
            "FROM {} WHERE id = %s AND kb_id = %s"
        ).format(_qualified(gaussdb_env["schema"], table)),
        ["c1", kb_id],
    )
    assert len(rows) == 1
    assert rows[0][:5] == ("c1", kb_id, "d1", "new", "new")
    assert _decode_vector(rows[0][5]) == pytest.approx([0.2] * 4)
    assert rows[0][6] is True


def test_tc_wrt_1008_lifecycle_duplicate_upsert_keeps_one_latest_row(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1008")
    kb_id = "kb-wrt-1008"
    assert conn.create_idx(table, kb_id, 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "c1",
                "kb_id": kb_id,
                "doc_id": "d1",
                "content_with_weight": "old content",
                "docnm_kwd": "OldDoc",
                "q_1024_vec": [0.1] * 1024,
                "q_1024_vec_valid": True,
            }
        ],
    )
    assert conn.insert(
        [
            {
                "id": "c1",
                "kb_id": kb_id,
                "doc_id": "d1",
                "content_with_weight": "new content",
                "docnm_kwd": "NewDoc",
                "q_1024_vec": [0.2] * 1024,
            }
        ],
        table,
        kb_id,
    ) == []
    result = conn.get("c1", table, [kb_id])
    assert result["content_with_weight"] == "new content"
    assert result["docnm_kwd"] == "NewDoc"
    assert result["q_1024_vec_valid"] is True
    rows = _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT content_with_weight, docnm_kwd, q_1024_vec, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1", kb_id],
    )
    assert len(rows) == 1
    assert rows[0][:2] == ("new content", "NewDoc")
    assert _decode_vector(rows[0][2]) == pytest.approx([0.2] * 1024)
    assert rows[0][3] is True


def test_tc_wrt_1009_lifecycle_keeps_cross_kb_same_id_isolated(gaussdb_env, table_name, gaussdb_admin_conn):
    from rag.utils.gaussdb_conn import GaussDBConnection

    conn = GaussDBConnection()
    table = table_name(gaussdb_env, "wrt_1009")
    other_tenant_table = table_name(gaussdb_env, "wrt1009b")
    kb_a, kb_b = "kb-wrt-1009-a", "kb-wrt-1009-b"
    assert conn.create_idx(table, kb_a, 1024) is True
    assert conn.create_idx(other_tenant_table, kb_a, 1024) is True
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        table,
        [
            {
                "id": "c1",
                "kb_id": kb_a,
                "doc_id": "d1",
                "content_with_weight": "kb1 content",
                "q_1024_vec": [0.1] * 1024,
                "q_1024_vec_valid": True,
            },
            {
                "id": "c1",
                "kb_id": kb_b,
                "doc_id": "d1",
                "content_with_weight": "kb2 content",
                "q_1024_vec": [0.2] * 1024,
                "q_1024_vec_valid": True,
            },
        ],
    )
    _insert_rows(
        gaussdb_admin_conn,
        gaussdb_env["schema"],
        other_tenant_table,
        [
            {
                "id": "c1",
                "kb_id": kb_a,
                "doc_id": "d1",
                "content_with_weight": "other tenant sentinel",
                "q_1024_vec": [0.3] * 1024,
                "q_1024_vec_valid": True,
            }
        ],
    )
    result_a = conn.get("c1", table, [kb_a])
    result_b = conn.get("c1", table, [kb_b])
    assert result_a["content_with_weight"] == "kb1 content"
    assert result_a["q_1024_vec"] == pytest.approx([0.1] * 1024)
    assert result_b["content_with_weight"] == "kb2 content"
    assert result_b["q_1024_vec"] == pytest.approx([0.2] * 1024)
    assert _fetch_all(
        gaussdb_admin_conn,
        sql.SQL("SELECT id, kb_id, content_with_weight FROM {} WHERE id = %s ORDER BY kb_id").format(
            _qualified(gaussdb_env["schema"], table)
        ),
        ["c1"],
    ) == [("c1", kb_a, "kb1 content"), ("c1", kb_b, "kb2 content")]

    assert conn.update(
        {"id": "c1", "kb_id": kb_a},
        {"content_with_weight": "tenant-a updated"},
        table,
        kb_a,
    ) is True
    assert conn.delete({"id": "c1"}, table, kb_a) == 1
    assert conn.get("c1", table, [kb_a]) is None
    assert conn.get("c1", table, [kb_b])["content_with_weight"] == "kb2 content"
    other_tenant = conn.get("c1", other_tenant_table, [kb_a])
    assert other_tenant["content_with_weight"] == "other tenant sentinel"
    assert other_tenant["q_1024_vec"] == pytest.approx([0.3] * 1024)
    assert _fetch_one(
        gaussdb_admin_conn,
        sql.SQL("SELECT content_with_weight, q_1024_vec_valid FROM {} WHERE id = %s AND kb_id = %s").format(
            _qualified(gaussdb_env["schema"], other_tenant_table)
        ),
        ["c1", kb_a],
    ) == ("other tenant sentinel", True)


mark_gaussdb_both_by_default(globals())
