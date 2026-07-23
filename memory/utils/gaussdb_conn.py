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
"""GaussDB Oracle-compatible Memory Store adapter。

本模块只处理 Memory message 存储，不处理 metadata DB 中的 memory 元数据表。
上层按 tenant 生成逻辑 index_name，本 adapter 再把它映射成 GaussDB 物理表；
同一 tenant 表内通过 memory_id 隔离多个 memory。实现上复用
common.doc_store.gaussdb_conn_base 的连接池、标识符校验和部分工具，但表结构、
字段映射、全文/向量/融合查询、容量/FIFO 相关读写都在这里独立实现，避免把
DocEngine chunk 语义误用于 Memory message。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import numpy as np
from pydantic import BaseModel

from common.doc_store.doc_store_base import FusionExpr, MatchDenseExpr, MatchExpr, MatchTextExpr, OrderByExpr
from common.doc_store.gaussdb_conn_base import GaussDBConnectionBase, GaussDBDDLBuilder, InvalidGaussDBObjectName
from common.doc_store.gaussdb_conn_pool import GaussDBConnectionPool, classify_gaussdb_exception
from common.float_utils import get_float
from memory.utils.aggregation_utils import aggregate_by_field
from memory.utils.highlight_utils import get_highlight_from_messages
from rag.nlp import is_english
from rag.nlp.rag_tokenizer import fine_grained_tokenize, tokenize

logger = logging.getLogger("ragflow.memory_gaussdb_conn")

VECTOR_COLUMN_RE = re.compile(r"^q_(?P<dim>\d+)_vec$")
VECTOR_EMPTY_COLUMN_RE = re.compile(r"^q_(?P<dim>\d+)_vec_empty$")

# Memory message 的基础列。向量列按 embedding 维度动态增加，不在这个清单中。
# 这些列名同时作为 SQL 白名单，所有由外部字段名转换来的列都必须命中基础列
# 或 q_{dim}_vec / q_{dim}_vec_empty 这两类动态列。
BASE_COLUMNS = (
    "id",
    "message_id",
    "message_type_kwd",
    "source_id",
    "memory_id",
    "user_id",
    "agent_id",
    "session_id",
    "zone_id",
    "valid_at",
    "invalid_at",
    "forget_at",
    "status_int",
    "content_ltks",
    "tokenized_content_ltks",
)
BASE_COLUMN_SET = set(BASE_COLUMNS)

TIME_COLUMNS = {"valid_at", "invalid_at", "forget_at"}
NUMERIC_COLUMNS = {"message_id", "source_id", "zone_id", "status_int"}
# 上层 MemoryService 仍使用跨存储后端统一字段名；GaussDB 物理表采用后缀化
# 列名，避免与全文/向量列或状态字段含义冲突。所有 SQL 构造前都先做映射。
MEMORY_FIELD_MAP = {
    "message_type": "message_type_kwd",
    "status": "status_int",
    "content": "content_ltks",
}
REVERSE_MEMORY_FIELD_MAP = {
    "message_type_kwd": "message_type",
    "status_int": "status",
    "content_ltks": "content",
}
RESULT_FIELD_DEFAULTS = {
    "source_id": None,
    "user_id": "",
    "zone_id": 0,
    "invalid_at": "-",
    "forget_at": "-",
    "content": "",
    "content_embed": [],
}


def normalize_fulltext_query(text: Any) -> str:
    # 写入端保存的是 fine_grained_tokenize(tokenize(content)) 的 token 串。
    # 查询端必须用同一套分词归一化后再传给 plainto_tsquery，否则短词、英文
    # 词干或中英文混合内容会出现“写入 token 与查询 token 不一致”的假阴性。
    query = str(text or "").strip()
    if not query:
        return ""
    tokenized = fine_grained_tokenize(tokenize(query)).strip()
    return tokenized or query


class SearchResult(BaseModel):
    # 对齐其他 Memory Store adapter 的返回形态：search() 返回
    # (SearchResult, total)，而上层 get_total/get_doc_ids/get_fields 会同时兼容
    # tuple 和裸 SearchResult。
    total: int
    messages: list[dict]


class GaussDBMemoryDDLBuilder(GaussDBDDLBuilder):
    """Memory message 专用 DDL 构造器。

    公共 GaussDBDDLBuilder 只知道标识符、安全表名和通用 advisory lock；
    Memory 表需要 Oracle-compatible 类型、UStore、UGIN 全文索引、gsdiskann
    向量索引以及向量空标记列，因此在这个子类集中维护。
    """

    # 常规索引覆盖列表、删除、容量统计、FIFO、最近消息和 source/raw 关联
    # 查询。向量索引和全文索引单独生成，因为它们依赖动态维度或表达式索引。
    REGULAR_INDEXES = (
        ("message_id", ("message_id",)),
        ("memory_id", ("memory_id",)),
        ("message_type", ("message_type_kwd",)),
        ("source_id", ("source_id",)),
        ("agent_session", ("agent_id", "session_id")),
        ("status_valid", ("status_int", "valid_at")),
        ("forget_at", ("forget_at",)),
    )

    def physical_table_name(self, index_name: str) -> str:
        logical = str(index_name or "").strip()
        if not logical:
            raise InvalidGaussDBObjectName(index_name)
        # 逻辑 index_name 可能包含 tenant id、横线或其它上层生成字符。为了让
        # 物理表名稳定、短且满足 GaussDB 标识符限制，这里只暴露 hash 后缀。
        # memory_id 不参与物理表名；同一 tenant 下的 memory 通过表内列隔离。
        digest = hashlib.sha1(logical.encode("utf-8")).hexdigest()[:32]
        return f"ragflow_mem_{digest}"

    def build_memory_table_ddl(self, table: str) -> str:
        # GaussDB Memory Store 使用 Oracle-compatible 类型：VARCHAR2/NUMBER
        # 表达固定长度文本和整数；全文字段仍用 TEXT。UStore 是 gsdiskann/向量
        # 检索路径的要求，建表时一次性指定。
        name = self.qualified_name(table)
        pk = self.index_name(table, "pk")
        return f"""CREATE TABLE IF NOT EXISTS {name} (
  id VARCHAR2(96) NOT NULL,
  message_id NUMBER(19) NOT NULL,
  message_type_kwd VARCHAR2(64),
  source_id NUMBER(19),
  memory_id VARCHAR2(32) NOT NULL,
  user_id VARCHAR2(64),
  agent_id VARCHAR2(64),
  session_id VARCHAR2(128),
  zone_id NUMBER(10) DEFAULT 0,
  valid_at TIMESTAMP,
  invalid_at TIMESTAMP,
  forget_at TIMESTAMP,
  status_int NUMBER(10) DEFAULT 1 NOT NULL,
  content_ltks TEXT,
  tokenized_content_ltks TEXT,
  CONSTRAINT {pk} PRIMARY KEY (id)
) WITH (storage_type=USTORE)"""

    def build_regular_index_ddls(self, table: str) -> list[str]:
        name = self.qualified_name(table)
        return [f"CREATE INDEX IF NOT EXISTS {self.index_name(table, suffix)} ON {name} ({', '.join(columns)})" for suffix, columns in self.REGULAR_INDEXES]

    def build_fulltext_ugin_ddl(self, table: str) -> str:
        # 全文索引建立在 tokenized_content_ltks 的 simple tsvector 表达式上。
        # 查询端使用同样的 simple + plainto_tsquery，避免依赖数据库默认语言配置。
        name = self.qualified_name(table)
        idx = self.index_name(table, "tokenized_ugin")
        return f"""CREATE INDEX IF NOT EXISTS {idx}
  ON {name}
  USING ugin (to_tsvector('simple', tokenized_content_ltks))"""

    def vector_empty_column_name(self, dim: int) -> str:
        return f"q_{self.validate_vector_dim(dim)}_vec_empty"

    def build_vector_column_ddls(self, table: str, dim: int) -> list[str]:
        # GaussDB floatvector 列不能用真实 NULL 表达“该 message 没有此维度向量”，
        # 因此每个向量列都配一列 *_empty。业务空向量以零向量占位，检索时必须
        # 过滤 *_empty = FALSE，读取时也只还原非空标记的维度。
        dim = self.validate_vector_dim(dim)
        name = self.qualified_name(table)
        vector_col = self.vector_column_name(dim)
        empty_col = self.vector_empty_column_name(dim)
        return [
            f"ALTER TABLE {name} ADD COLUMN IF NOT EXISTS {vector_col} floatvector({dim}) DEFAULT (array_fill(0, ARRAY[{dim}])::text::floatvector({dim})) NOT NULL",
            f"ALTER TABLE {name} ADD COLUMN IF NOT EXISTS {empty_col} BOOLEAN DEFAULT TRUE NOT NULL",
        ]

    def build_vector_empty_index_ddl(self, table: str, dim: int) -> str:
        dim = self.validate_vector_dim(dim)
        name = self.qualified_name(table)
        empty_col = self.vector_empty_column_name(dim)
        idx = self.index_name(table, f"{empty_col}_idx")
        return f"CREATE INDEX IF NOT EXISTS {idx} ON {name} (memory_id, {empty_col})"

    def build_diskann_index_ddl(self, table: str, dim: int) -> str:
        # gsdiskann 是向量近邻检索索引。索引名包含维度列名，允许同一张 tenant
        # 表在模型迁移过程中同时存在多个 embedding 维度。
        dim = self.validate_vector_dim(dim)
        name = self.qualified_name(table)
        vector_col = self.vector_column_name(dim)
        idx = self.index_name(table, f"{vector_col}_diskann")
        return f"CREATE INDEX IF NOT EXISTS {idx} ON {name} USING gsdiskann ({vector_col} cosine)"


class GaussDBMemoryConnection(GaussDBConnectionBase):
    """GaussDB Memory message 存储实现。

    这个类实现 MemoryService 期望的 Message Store 接口。它不暴露任意 SQL，
    也不复用 DocEngine chunk 查询逻辑；所有写入、更新、删除和检索都强制带
    memory_id 表内边界，避免同一 tenant 表中不同 memory 的 message 串读。
    """

    def __init__(self, pool: GaussDBConnectionPool | None = None):
        super().__init__(pool=pool, logger_name="ragflow.memory_gaussdb_conn")
        # 基类先建立共享连接池和 schema 访问检查；Memory adapter 再替换成
        # Memory 专用 DDL builder，确保后续 create_idx/_ensure_vector_column_exists
        # 使用 message 表结构，而不是文档 chunk 表结构。
        self.ddl = GaussDBMemoryDDLBuilder(schema=self.resolved_schema)

    def create_idx(self, index_name: str, memory_id: str, vector_size: int, parser_id: str = None):
        table = self.physical_table(index_name)
        # 创建顺序：先拿 advisory lock，再建基础表、常规索引、全文索引、向量列
        # 和向量空标记索引。DDL 全部使用 IF NOT EXISTS/ADD COLUMN IF NOT EXISTS，
        # 使重复调用、并发初始化和同 tenant 多 memory 首次写入都保持幂等。
        statements: list[str | tuple[str, list[Any]]] = [
            self.ddl.build_advisory_lock_sql(f"gaussdb_memory_create_table:{table}"),
            self.ddl.build_memory_table_ddl(table),
            self.ddl.build_advisory_lock_sql(f"gaussdb_memory_base_index:{table}"),
            *self.ddl.build_regular_index_ddls(table),
            self.ddl.build_advisory_lock_sql(f"gaussdb_memory_fulltext_index:{table}"),
            self.ddl.build_fulltext_ugin_ddl(table),
        ]
        statements.extend(self.ddl.build_vector_column_ddls(table, vector_size))
        statements.append(self.ddl.build_vector_empty_index_ddl(table, vector_size))
        self._execute_statements(statements)
        self._create_diskann_index_with_retry(table, vector_size)
        return True

    def delete_idx(self, index_name: str, memory_id: str):
        # 物理表边界是 tenant 级 index_name，不是 memory_id。上层删除 tenant/user
        # 数据时可能按 memory 循环调用 delete_idx，因此 DROP TABLE IF EXISTS 必须
        # 可重复执行；memory_id 参数只为接口兼容保留。
        table = self.ddl.qualified_name(self.physical_table(index_name))
        self._execute_write(f"DROP TABLE IF EXISTS {table} PURGE", [])
        return True

    def index_exist(self, index_name: str, memory_id: str = None) -> bool:
        # has_index() 不只判断表存在，还校验基础列和基础索引是否完整。这样旧版
        # 或半初始化表会触发 create_idx()/ensure 路径，而不是在后续查询中暴露
        # 缺列、缺索引的数据库错误。
        table = self.physical_table(index_name)
        if not self._table_exists(table):
            return False
        required_columns = set(BASE_COLUMNS)
        existing_columns = set(self._column_names(table))
        if not required_columns.issubset(existing_columns):
            return False
        required_indexes = {self.ddl.index_name(table, suffix) for suffix, _columns in self.ddl.REGULAR_INDEXES}
        required_indexes.add(self.ddl.index_name(table, "tokenized_ugin"))
        existing_indexes = set(self._index_names(table))
        return required_indexes.issubset(existing_indexes)

    def insert(self, documents: list[dict], index_name: str, memory_id: str = None) -> list[str]:
        if not documents:
            return []

        errors: list[str] = []
        rows: list[dict] = []
        dim = None
        for document in documents:
            doc_id = str(document.get("id") or "")
            try:
                # 同一批写入必须使用同一 embedding 维度。不同维度混写会导致
                # MERGE 语句无法只绑定一个 q_{dim}_vec，因此在 SQL 执行前失败并
                # 返回失败 id。
                row, row_dim = self._message_to_row(document, memory_id)
                dim = row_dim if dim is None else dim
                if row_dim != dim:
                    raise ValueError(f"inconsistent content_embed dimension: expected {dim}, got {row_dim}")
                rows.append(row)
            except Exception as exc:
                logger.error("GaussDB memory normalize failed id=%s error=%s", doc_id, exc)
                errors.append(doc_id or str(exc))
        if errors:
            return errors

        table = self.physical_table(index_name)
        if not self._table_exists(table):
            # 首次写入负责创建 tenant message 表。memory_id 不进入物理表名，
            # 只在行数据和后续 WHERE 条件中作为表内隔离边界。
            self.create_idx(index_name, memory_id, dim)
        else:
            # embedding 模型维度变化时，已有 tenant 表可能缺少新维度向量列。
            # 这里按需补列和索引，但不会删除旧维度列，便于读历史数据时正确
            # 根据 *_empty 标记选择有效向量。
            self._ensure_vector_column_exists(table, dim)

        existing_dims = self._vector_dimensions(table)
        sql, params = self._build_merge_sql(table, dim, existing_dims, rows)
        try:
            self._execute_write(sql, params, many=True)
            return []
        except Exception as exc:
            ids = [row["id"] for row in rows]
            logger.error("GaussDB memory insert failed table=%s ids=%s error=%s", table, ids, exc)
            return ids or [str(exc)]

    def update(self, condition: dict, new_value: dict, index_name: str, memory_id: str) -> bool:
        if not condition or not new_value:
            return False
        table = self.physical_table(index_name)
        if not self._table_exists(table):
            return True

        try:
            set_sql, set_params = self._build_update_set(table, index_name, memory_id, new_value)
            if not set_sql:
                return True
            where_sql, where_params = self._build_where_clause(condition, memory_ids=[memory_id], force_memory_filter=True)
            if not where_sql:
                return False
            # UPDATE 永远叠加 memory_id 边界；即使上层 condition 只传 message_id，
            # 也不能跨同 tenant 的其它 memory 更新行。
            sql = f"UPDATE {self.ddl.qualified_name(table)} SET {set_sql} WHERE {where_sql}"
            self._execute_write(sql, [*set_params, *where_params])
            return True
        except Exception as exc:
            logger.error("GaussDB memory update failed table=%s condition=%s error=%s", table, condition, exc)
            return False

    def delete(self, condition: dict, index_name: str, memory_id: str) -> int:
        if not condition:
            return 0
        if self._has_empty_delete_list(condition):
            # 空列表删除在业务上是 no-op。如果继续构造 SQL，容易退化成仅带
            # memory_id 的 DELETE，误删整个 memory。
            return 0
        table = self.physical_table(index_name)
        if not self._table_exists(table):
            return 0
        try:
            where_sql, where_params = self._build_where_clause(condition, memory_ids=[memory_id], force_memory_filter=True)
            if not where_sql:
                return 0
            # delete_message() 经常只传 message_id/source_id；这里统一补 memory_id
            # 边界，确保 tenant 共享表内的数据隔离。
            return self._execute_write(f"DELETE FROM {self.ddl.qualified_name(table)} WHERE {where_sql}", where_params)
        except Exception as exc:
            logger.error("GaussDB memory delete failed table=%s condition=%s error=%s", table, condition, exc)
            return 0

    def get(self, doc_id: str, index_name: str, memory_ids: list[str]) -> dict | None:
        if not doc_id:
            return None
        table = self.physical_table(index_name)
        if not self._table_exists(table):
            return None
        # get() 读取所有基础列以及已存在的全部向量/空标记列，由
        # _message_from_row() 负责把有效维度还原成 content_embed。
        columns = [*BASE_COLUMNS]
        columns.extend(self._vector_columns_for_select(table))
        sql = f"SELECT {', '.join(columns)} FROM {self.ddl.qualified_name(table)} WHERE id = %s"
        row, description = self._fetch_one_with_description(sql, [doc_id])
        if row is None:
            return None
        return self._message_from_row(self._row_to_dict(row, description))

    def search(
        self,
        select_fields: list[str],
        highlight_fields: list[str],
        condition: dict,
        match_expressions: list[MatchExpr],
        order_by: OrderByExpr,
        offset: int,
        limit: int,
        index_names: str | list[str],
        memory_ids: list[str],
        agg_fields: list[str] | None = None,
        rank_feature: dict | None = None,
        hide_forgotten: bool = True,
        **kwargs,
    ):
        tables = [self.physical_table(name) for name in normalize_index_names(index_names)]
        memory_ids = clean_list_values(memory_ids)
        if not tables or not memory_ids:
            return SearchResult(total=0, messages=[]), 0

        parsed = self._parse_match_expressions(match_expressions)
        has_match = bool(parsed["text_query"] or parsed["vector"])
        # 多 tenant fan-out 时，每张表先拉取 offset+limit 个候选，再在内存中按
        # score/order_by 合并排序和切片。单表查询则把 offset/limit 直接下推给 SQL。
        collection_limit = max(int(offset or 0), 0) + max(int(limit or 0), 0)
        if collection_limit <= 0:
            collection_limit = 10000

        result = SearchResult(total=0, messages=[])
        for table in tables:
            if not self._table_exists(table):
                continue
            sql, params = self._build_search_sql(
                table=table,
                select_fields=select_fields,
                highlight_fields=highlight_fields,
                condition=condition,
                parsed=parsed,
                order_by=order_by,
                offset=0 if len(tables) > 1 else max(int(offset or 0), 0),
                limit=collection_limit if len(tables) > 1 else max(int(limit or 0), 0),
                memory_ids=memory_ids,
                hide_forgotten=hide_forgotten,
            )
            rows, description = self._fetch_all_with_description(sql, params)
            table_total, messages = self._rows_to_messages(rows, description)
            result.total += table_total
            result.messages.extend(messages)

        result.messages = self._sort_messages(result.messages, order_by, has_match)
        if len(tables) > 1:
            # 跨 tenant 合并后的最终分页必须在全局排序之后执行，不能直接拼接每张
            # 表的局部结果。
            effective_offset = max(int(offset or 0), 0)
            effective_limit = max(int(limit or 0), 0)
            if effective_limit:
                result.messages = result.messages[effective_offset : effective_offset + effective_limit]
        return result, result.total

    def get_forgotten_messages(self, select_fields: list[str], index_name: str, memory_id: str, limit: int = 512):
        table = self.physical_table(index_name)
        if not self._table_exists(table):
            return None
        columns = self._select_columns(table, select_fields)
        sql = (
            f"SELECT {', '.join(columns)} "
            f"FROM {self.ddl.qualified_name(table)} "
            "WHERE memory_id = %s AND forget_at IS NOT NULL "
            # 当前 GaussDB O-compatible 实例在这些查询上使用 LIMIT 兼容性更好；
            # 因此这里不用 FETCH FIRST，避免维护任务路径触发方言错误。
            "ORDER BY forget_at ASC LIMIT %s"
        )
        rows, description = self._fetch_all_with_description(sql, [memory_id, int(limit)])
        _total, messages = self._rows_to_messages(rows, description)
        return SearchResult(total=len(messages), messages=messages)

    def get_missing_field_message(
        self,
        select_fields: list[str],
        index_name: str,
        memory_id: str,
        field_name: str,
        limit: int = 512,
    ):
        table = self.physical_table(index_name)
        if not self._table_exists(table):
            return None
        db_field = self.convert_field_name(field_name)
        self._validate_column(db_field)
        columns = self._select_columns(table, select_fields)
        sql = (
            f"SELECT {', '.join(columns)} "
            f"FROM {self.ddl.qualified_name(table)} "
            f"WHERE memory_id = %s AND {db_field} IS NULL "
            # 与 get_forgotten_messages 保持一致，维护扫描使用 LIMIT 语法。
            "ORDER BY valid_at ASC LIMIT %s"
        )
        rows, description = self._fetch_all_with_description(sql, [memory_id, int(limit)])
        _total, messages = self._rows_to_messages(rows, description)
        return SearchResult(total=len(messages), messages=messages)

    def get_total(self, res) -> int:
        if isinstance(res, tuple):
            return int(res[1] or 0)
        return int(getattr(res, "total", 0) or 0)

    def get_doc_ids(self, res) -> list[str]:
        if isinstance(res, tuple):
            res = res[0]
        return [row["id"] for row in getattr(res, "messages", []) if row.get("id")]

    def get_fields(self, res, fields: list[str]) -> dict[str, dict]:
        if isinstance(res, tuple):
            res = res[0]
        requested = set(fields or [])
        if not requested:
            return {}
        result: dict[str, dict] = {}
        for row in getattr(res, "messages", []) or []:
            message = self._message_from_row(row)
            doc_id = row.get("id") or message.get("id")
            if not doc_id:
                continue
            item = {}
            for field in fields:
                if field in message:
                    item[field] = message[field]
                elif field in RESULT_FIELD_DEFAULTS:
                    item[field] = RESULT_FIELD_DEFAULTS[field]
            if item:
                result[str(doc_id)] = item
        return result

    def get_highlight(self, res, keywords: list[str], field_name: str):
        if isinstance(res, tuple):
            res = res[0]
        return get_highlight_from_messages(
            getattr(res, "messages", None),
            keywords,
            field_name,
            is_english_fn=lambda s: is_english([s]),
        )

    def get_aggregation(self, res, field_name: str):
        if isinstance(res, tuple):
            res = res[0]
        return aggregate_by_field(getattr(res, "messages", None), field_name)

    def sql(self, sql: str, fetch_size: int = 128, format: str = "json"):
        # Memory Store 不提供任意 SQL 能力。DocEngine 的 SQL 问答有独立的
        # 只读校验器；Memory message 表不应被外部 SQL 绕过 memory_id 边界访问。
        logger.warning("GaussDB Memory Store does not expose raw SQL execution.")
        return None

    def physical_table(self, index_name: str) -> str:
        return self.ddl.physical_table_name(index_name)

    @staticmethod
    def convert_field_name(field_name: str, use_tokenized_content: bool = False) -> str:
        # content 在普通读写中映射到原文列；全文匹配需要显式请求
        # tokenized_content_ltks，避免普通 get_fields() 返回分词后的内容。
        if field_name == "content" and use_tokenized_content:
            return "tokenized_content_ltks"
        return MEMORY_FIELD_MAP.get(field_name, field_name)

    def _message_to_row(self, message: dict, memory_id: str | None) -> tuple[dict, int]:
        content_embed = message.get("content_embed")
        if content_embed is None or len(content_embed) == 0:
            raise ValueError("content_embed is required for GaussDB memory insert")
        dim = self.ddl.validate_vector_dim(len(content_embed))
        target_memory_id = str(message.get("memory_id") or memory_id or "")
        if not target_memory_id:
            raise ValueError("memory_id is required")
        # 行转换阶段完成三类归一化：
        # 1. 应用层字段名映射成物理列名；
        # 2. 空字符串用户/agent/session 归一化为 NULL，避免无意义空串参与过滤；
        # 3. content_embed 写入对应维度向量列，并把 *_empty 标记置为 FALSE。
        row = {
            "id": str(message.get("id") or f"{target_memory_id}_{message['message_id']}"),
            "message_id": to_int_or_none(message.get("message_id")),
            "message_type_kwd": message.get("message_type"),
            "source_id": to_int_or_none(message.get("source_id")),
            "memory_id": target_memory_id,
            "user_id": none_if_empty(message.get("user_id")),
            "agent_id": none_if_empty(message.get("agent_id")),
            "session_id": none_if_empty(message.get("session_id")),
            "zone_id": to_int_or_none(message.get("zone_id", 0)) or 0,
            "valid_at": normalize_timestamp(message.get("valid_at")),
            "invalid_at": normalize_timestamp(message.get("invalid_at")),
            "forget_at": normalize_timestamp(message.get("forget_at")),
            "status_int": 1 if bool(message.get("status")) else 0,
            "content_ltks": message.get("content") or "",
            "tokenized_content_ltks": fine_grained_tokenize(tokenize(message.get("content") or "")),
            self.ddl.vector_column_name(dim): vector_literal(content_embed, dim),
            self.ddl.vector_empty_column_name(dim): False,
        }
        return row, dim

    def _message_from_row(self, row: dict) -> dict:
        # 读回时恢复上层统一字段名和默认值语义。invalid_at/forget_at 为空时继续
        # 对上层表现为 "-"，content/user_id 为空时表现为空字符串，避免调用方为
        # GaussDB 单独处理 NULL。
        message = {
            "id": row.get("id"),
            "message_id": to_int_or_original(row.get("message_id")),
            "message_type": row.get("message_type_kwd"),
            "source_id": to_int_or_original(row.get("source_id")) if row.get("source_id") is not None else None,
            "memory_id": row.get("memory_id"),
            "user_id": row.get("user_id") or "",
            "agent_id": row.get("agent_id"),
            "session_id": row.get("session_id"),
            "zone_id": to_int_or_original(row.get("zone_id")) if row.get("zone_id") is not None else 0,
            "valid_at": format_timestamp(row.get("valid_at")),
            "invalid_at": format_timestamp(row.get("invalid_at")) or "-",
            "forget_at": format_timestamp(row.get("forget_at")) or "-",
            "status": bool(int(row.get("status_int") or 0)),
            "content": row.get("content_ltks") or "",
            "content_embed": self._content_embed_from_row(row),
        }
        if row.get("_score") is not None:
            message["_score"] = float(row.get("_score") or 0.0)
        return message

    def _content_embed_from_row(self, row: dict) -> list[float]:
        candidates = []
        for key, value in row.items():
            match = VECTOR_COLUMN_RE.fullmatch(str(key))
            if not match:
                continue
            dim = int(match.group("dim"))
            if row.get(self.ddl.vector_empty_column_name(dim)) is False:
                candidates.append((dim, parse_vector_value(value)))
        if not candidates:
            return []
        if len(candidates) > 1:
            # 正常情况下一个 message 只有一个非空向量维度。若历史数据或手工修复
            # 造成多个维度同时非空，选择最高维度返回并记录日志，避免直接让读取
            # 流程失败。
            logger.warning("GaussDB memory row %s has multiple non-empty vector dimensions.", row.get("id"))
        return sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]

    def _build_merge_sql(
        self,
        table: str,
        dim: int,
        existing_dims: list[int],
        rows: list[dict],
    ) -> tuple[str, list[list[Any]]]:
        vector_col = self.ddl.vector_column_name(dim)
        empty_col = self.ddl.vector_empty_column_name(dim)
        table_name = self.ddl.qualified_name(table)
        reset_other_dims = []
        for other_dim in existing_dims:
            if other_dim == dim:
                continue
            other_vector = self.ddl.vector_column_name(other_dim)
            other_empty = self.ddl.vector_empty_column_name(other_dim)
            reset_other_dims.append(f"{other_vector} = '{zero_vector_literal(other_dim)}'::floatvector")
            reset_other_dims.append(f"{other_empty} = TRUE")
        reset_clause = "".join(f",\n  {assignment}" for assignment in reset_other_dims)
        # GaussDB O-compatible 使用 MERGE ... USING (SELECT ... FROM dual) 表达
        # upsert，替代 PostgreSQL ON CONFLICT。更新当前维度向量时，把其它维度
        # 重置为零向量且 *_empty=TRUE，保证同一行只暴露一个真实 content_embed。
        sql = f"""
MERGE INTO {table_name} t
USING (
  SELECT
    %s AS id,
    %s AS message_id,
    %s AS message_type_kwd,
    %s AS source_id,
    %s AS memory_id,
    %s AS user_id,
    %s AS agent_id,
    %s AS session_id,
    %s AS zone_id,
    %s::timestamp AS valid_at,
    %s::timestamp AS invalid_at,
    %s::timestamp AS forget_at,
    %s AS status_int,
    %s AS content_ltks,
    %s AS tokenized_content_ltks,
    %s::floatvector({dim}) AS {vector_col}
  FROM dual
) s
ON (t.id = s.id)
WHEN MATCHED THEN UPDATE SET
  message_id = s.message_id,
  message_type_kwd = s.message_type_kwd,
  source_id = s.source_id,
  memory_id = s.memory_id,
  user_id = s.user_id,
  agent_id = s.agent_id,
  session_id = s.session_id,
  zone_id = s.zone_id,
  valid_at = s.valid_at,
  invalid_at = s.invalid_at,
  forget_at = s.forget_at,
  status_int = s.status_int,
  content_ltks = s.content_ltks,
  tokenized_content_ltks = s.tokenized_content_ltks,
  {vector_col} = s.{vector_col},
  {empty_col} = FALSE{reset_clause}
WHEN NOT MATCHED THEN INSERT (
  id, message_id, message_type_kwd, source_id, memory_id, user_id, agent_id,
  session_id, zone_id, valid_at, invalid_at, forget_at, status_int,
  content_ltks, tokenized_content_ltks, {vector_col}, {empty_col}
) VALUES (
  s.id, s.message_id, s.message_type_kwd, s.source_id, s.memory_id, s.user_id,
  s.agent_id, s.session_id, s.zone_id, s.valid_at, s.invalid_at, s.forget_at,
  s.status_int, s.content_ltks, s.tokenized_content_ltks, s.{vector_col}, FALSE
)"""
        params = [
            [
                row.get("id"),
                row.get("message_id"),
                row.get("message_type_kwd"),
                row.get("source_id"),
                row.get("memory_id"),
                row.get("user_id"),
                row.get("agent_id"),
                row.get("session_id"),
                row.get("zone_id"),
                row.get("valid_at"),
                row.get("invalid_at"),
                row.get("forget_at"),
                row.get("status_int"),
                row.get("content_ltks"),
                row.get("tokenized_content_ltks"),
                row.get(vector_col),
            ]
            for row in rows
        ]
        return sql.strip(), params

    def _build_update_set(self, table: str, index_name: str, memory_id: str, new_value: dict) -> tuple[str, list[Any]]:
        fragments: list[str] = []
        params: list[Any] = []
        for field, value in (new_value or {}).items():
            if field == "remove":
                # remove 用于清空字段。向量列不能写 NULL，因此写入零向量并把
                # 对应 empty 标记置为 TRUE；普通列直接置 NULL。
                remove_fields = [value] if isinstance(value, str) else list(value or [])
                for remove_field in remove_fields:
                    db_field = self.convert_field_name(remove_field)
                    if VECTOR_COLUMN_RE.fullmatch(db_field):
                        dim = int(VECTOR_COLUMN_RE.fullmatch(db_field).group("dim"))
                        self._ensure_vector_column_exists(table, dim)
                        fragments.append(f"{db_field} = %s::floatvector({dim})")
                        params.append(zero_vector_literal(dim))
                        fragments.append(f"{self.ddl.vector_empty_column_name(dim)} = TRUE")
                    else:
                        self._validate_column(db_field)
                        fragments.append(f"{db_field} = NULL")
                continue
            if field == "content_embed":
                if value is None or len(value) == 0:
                    continue
                dim = self.ddl.validate_vector_dim(len(value))
                self._ensure_vector_column_exists(table, dim)
                vector_col = self.ddl.vector_column_name(dim)
                # 更新向量时和 insert 一样只保留当前维度为“真实向量”，其它维度
                # 清空为零向量，防止检索时一个 message 被多个维度重复命中。
                fragments.append(f"{vector_col} = %s::floatvector({dim})")
                params.append(vector_literal(value, dim))
                fragments.append(f"{self.ddl.vector_empty_column_name(dim)} = FALSE")
                for other_dim in self._vector_dimensions(table):
                    if other_dim == dim:
                        continue
                    fragments.append(f"{self.ddl.vector_column_name(other_dim)} = %s::floatvector({other_dim})")
                    params.append(zero_vector_literal(other_dim))
                    fragments.append(f"{self.ddl.vector_empty_column_name(other_dim)} = TRUE")
                continue

            db_field = self.convert_field_name(field)
            self._validate_column(db_field)
            if db_field == "content_ltks":
                # content 原文变更时同步刷新 tokenized_content_ltks，确保全文检索
                # 使用的 token 与最新内容一致。
                fragments.append("content_ltks = %s")
                params.append(value or "")
                fragments.append("tokenized_content_ltks = %s")
                params.append(fine_grained_tokenize(tokenize(value or "")))
            elif db_field in TIME_COLUMNS:
                fragments.append(f"{db_field} = %s::timestamp")
                params.append(normalize_timestamp(value))
            else:
                fragments.append(f"{db_field} = %s")
                if db_field in NUMERIC_COLUMNS:
                    params.append(to_int_or_none(value))
                else:
                    params.append(none_if_empty(value))
        return ", ".join(fragments), params

    def _build_search_sql(
        self,
        table: str,
        select_fields: list[str],
        highlight_fields: list[str],
        condition: dict,
        parsed: dict[str, Any],
        order_by: OrderByExpr,
        offset: int,
        limit: int,
        memory_ids: list[str],
        hide_forgotten: bool,
    ) -> tuple[str, list[Any]]:
        text_query = parsed["text_query"]
        vector = parsed["vector"]
        # 查询分派顺序与上层表达式语义一致：文本+向量进入融合查询，只有向量
        # 进入 ANN 排序，只有文本进入全文排序，二者都没有时退化为 filter-only。
        if text_query and vector:
            return self._build_fusion_search_sql(table, select_fields, condition, parsed, offset, limit, memory_ids, hide_forgotten)
        if vector:
            return self._build_vector_search_sql(table, select_fields, condition, parsed, offset, limit, memory_ids, hide_forgotten)
        if text_query:
            return self._build_fulltext_search_sql(table, select_fields, highlight_fields, condition, parsed, offset, limit, memory_ids, hide_forgotten)
        return self._build_filter_search_sql(table, select_fields, condition, order_by, offset, limit, memory_ids, hide_forgotten)

    def _build_filter_search_sql(
        self,
        table: str,
        select_fields: list[str],
        condition: dict,
        order_by: OrderByExpr,
        offset: int,
        limit: int,
        memory_ids: list[str],
        hide_forgotten: bool,
    ) -> tuple[str, list[Any]]:
        columns = self._select_columns(table, select_fields)
        where_sql, where_params = self._build_where_clause(condition, memory_ids=memory_ids, hide_forgotten=hide_forgotten)
        order_sql = self._build_order_by(order_by) or "id ASC"
        # filter-only 列表、最近消息、容量统计等路径复用 search()。COUNT(*) OVER()
        # 用于在同一次查询中带回总数，避免额外 count 查询。
        sql = f"SELECT {', '.join(columns)}, COUNT(*) OVER() AS __total FROM {self.ddl.qualified_name(table)}"
        if where_sql:
            sql += f" WHERE {where_sql}"
        sql += f" ORDER BY {order_sql}"
        params = [*where_params]
        if limit and int(limit) > 0:
            sql += " OFFSET %s LIMIT %s"
            params.extend([max(int(offset or 0), 0), int(limit)])
        return sql, params

    def _build_fulltext_search_sql(
        self,
        table: str,
        select_fields: list[str],
        highlight_fields: list[str],
        condition: dict,
        parsed: dict[str, Any],
        offset: int,
        limit: int,
        memory_ids: list[str],
        hide_forgotten: bool,
    ) -> tuple[str, list[Any]]:
        columns = self._select_columns(table, select_fields)
        text_query = parsed["text_query"]
        fts_expr = "to_tsvector('simple', tokenized_content_ltks)"
        # text_query 已经在 _parse_match_expressions 中按写入端同样的 tokenizer
        # 归一化，这里只负责构造 GaussDB 全文表达式和排名。
        score_expr = f"ts_rank({fts_expr}, plainto_tsquery('simple', %s))"
        match_expr = f"{fts_expr} @@ plainto_tsquery('simple', %s)"
        where_sql, where_params = self._build_where_clause(condition, memory_ids=memory_ids, hide_forgotten=hide_forgotten)
        where_parts = [part for part in (where_sql, match_expr) if part]
        sql = (
            f"SELECT {', '.join(columns)}, {score_expr} AS _score, COUNT(*) OVER() AS __total "
            f"FROM {self.ddl.qualified_name(table)} "
            f"WHERE {' AND '.join(where_parts)} "
            "ORDER BY _score DESC, valid_at DESC OFFSET %s LIMIT %s"
        )
        return sql, [text_query, *where_params, text_query, max(int(offset or 0), 0), effective_limit(limit, parsed["topn"])]

    def _build_vector_search_sql(
        self,
        table: str,
        select_fields: list[str],
        condition: dict,
        parsed: dict[str, Any],
        offset: int,
        limit: int,
        memory_ids: list[str],
        hide_forgotten: bool,
    ) -> tuple[str, list[Any]]:
        dim = parsed["vector_dim"]
        vector_col = self.ddl.vector_column_name(dim)
        empty_col = self.ddl.vector_empty_column_name(dim)
        columns = self._select_columns(table, select_fields)
        vector = parsed["vector"]
        threshold = float(parsed["similarity_threshold"] or 0.0)
        # `<+>` 返回 cosine distance；RAGFlow 上层传入的是 similarity 阈值。
        # 因此打分和过滤都用 1 - distance，并用 *_empty=FALSE 排除占位零向量。
        score_expr = f"1 - ({vector_col} <+> %s::floatvector({dim}))"
        distance_expr = f"{vector_col} <+> %s::floatvector({dim})"
        where_sql, where_params = self._build_where_clause(condition, memory_ids=memory_ids, hide_forgotten=hide_forgotten)
        where_parts = [part for part in (where_sql, f"{empty_col} = FALSE", f"1 - ({vector_col} <+> %s::floatvector({dim})) >= %s") if part]
        sql = (
            f"SELECT {', '.join(columns)}, {score_expr} AS _score, COUNT(*) OVER() AS __total "
            f"FROM {self.ddl.qualified_name(table)} "
            f"WHERE {' AND '.join(where_parts)} "
            f"ORDER BY {distance_expr} ASC OFFSET %s LIMIT %s"
        )
        return sql, [
            vector,
            *where_params,
            vector,
            threshold,
            vector,
            max(int(offset or 0), 0),
            effective_limit(limit, parsed["topn"]),
        ]

    def _build_fusion_search_sql(
        self,
        table: str,
        select_fields: list[str],
        condition: dict,
        parsed: dict[str, Any],
        offset: int,
        limit: int,
        memory_ids: list[str],
        hide_forgotten: bool,
    ) -> tuple[str, list[Any]]:
        dim = parsed["vector_dim"]
        vector_col = self.ddl.vector_column_name(dim)
        empty_col = self.ddl.vector_empty_column_name(dim)
        vector = parsed["vector"]
        threshold = float(parsed["similarity_threshold"] or 0.0)
        vector_weight = float(parsed["vector_weight"])
        text_weight = 1.0 - vector_weight
        text_query = parsed["text_query"]
        columns = self._select_columns(table, select_fields)
        inner_columns = unique_preserve_order([*columns, "tokenized_content_ltks", vector_col, empty_col, "valid_at"])
        fts_expr = "to_tsvector('simple', tokenized_content_ltks)"
        score_expr = f"ts_rank({fts_expr}, plainto_tsquery('simple', %s))"
        match_expr = f"{fts_expr} @@ plainto_tsquery('simple', %s)"
        where_sql, where_params = self._build_where_clause(condition, memory_ids=memory_ids, hide_forgotten=hide_forgotten)
        where_parts = [part for part in (where_sql, match_expr) if part]
        candidate_limit = max(int(parsed["topn"] or 0), effective_limit(limit, parsed["topn"]), 1)
        # 融合查询先取全文候选集，再在候选集内做向量过滤和加权求和。这样避免在
        # 没有全文命中的行上计算向量分数，也符合当前 weighted_sum 表达式的语义。
        sql = (
            "WITH fulltext_results AS ("
            f" SELECT {', '.join(inner_columns)}, {score_expr} AS relevance "
            f"FROM {self.ddl.qualified_name(table)} "
            f"WHERE {' AND '.join(where_parts)} "
            "ORDER BY relevance DESC LIMIT %s"
            ") "
            f"SELECT {', '.join(columns)}, "
            f"relevance * %s + (1 - ({vector_col} <+> %s::floatvector({dim}))) * %s AS _score, "
            "COUNT(*) OVER() AS __total "
            "FROM fulltext_results "
            f"WHERE {empty_col} = FALSE "
            f"AND 1 - ({vector_col} <+> %s::floatvector({dim})) >= %s "
            "ORDER BY _score DESC, valid_at DESC OFFSET %s LIMIT %s"
        )
        return sql, [
            text_query,
            *where_params,
            text_query,
            candidate_limit,
            text_weight,
            vector,
            vector_weight,
            vector,
            threshold,
            max(int(offset or 0), 0),
            effective_limit(limit, parsed["topn"]),
        ]

    def _parse_match_expressions(self, match_expressions: list[MatchExpr] | None) -> dict[str, Any]:
        text_query = ""
        vector = None
        vector_dim = None
        topn = None
        similarity_threshold = 0.0
        vector_weight = 0.5

        for expr in match_expressions or []:
            if isinstance(expr, MatchTextExpr):
                # MsgTextQuery 会把原始 query 放入 extra_options["original_query"]。
                # 优先使用原始 query 再执行本 adapter 的 tokenization，避免直接
                # 使用其它后端生成的 matching_text 方言。
                text_query = normalize_fulltext_query((expr.extra_options or {}).get("original_query") or expr.matching_text)
                topn = expr.topn if topn is None else min(topn, expr.topn)
            elif isinstance(expr, MatchDenseExpr):
                # vector_column_name 可能是 q_{dim}_vec，也可能是上层通用字段
                # content_embed；后者用 embedding_data 长度推导维度。
                vector_dim = parse_vector_dim(expr.vector_column_name) or len(expr.embedding_data)
                vector = vector_literal(expr.embedding_data, vector_dim)
                topn = expr.topn if topn is None else min(topn, expr.topn)
                similarity_threshold = float((expr.extra_options or {}).get("similarity", 0.0))
            elif isinstance(expr, FusionExpr):
                # FusionExpr weights 约定为 text_weight,vector_weight。缺失或格式
                # 不完整时使用 0.5，保持文本/向量均衡。
                weights = (expr.fusion_params or {}).get("weights", "0.5,0.5")
                parts = str(weights).split(",")
                if len(parts) > 1:
                    vector_weight = get_float(parts[1])
                topn = expr.topn if topn is None else min(topn, expr.topn)

        return {
            "text_query": text_query,
            "vector": vector,
            "vector_dim": vector_dim,
            "topn": topn,
            "similarity_threshold": similarity_threshold,
            "vector_weight": vector_weight,
        }

    def _select_columns(self, table: str, select_fields: list[str] | None) -> list[str]:
        requested = select_fields or []
        columns = ["id"]
        wants_content_embed = False
        for field in requested:
            if field in {"_score", "id"}:
                continue
            if field == "content_embed":
                # content_embed 是虚拟业务字段，物理表里可能有多个 q_{dim}_vec
                # 与 *_empty 列。先记录需求，最后按当前表实际列动态展开。
                wants_content_embed = True
                continue
            db_field = self.convert_field_name(field)
            self._validate_column(db_field)
            if db_field not in columns:
                columns.append(db_field)
        if wants_content_embed:
            columns.extend(column for column in self._vector_columns_for_select(table) if column not in columns)
        return columns

    def _vector_columns_for_select(self, table: str) -> list[str]:
        columns: list[str] = []
        # 必须同时读取向量列和 empty 标记列，否则无法区分真实零向量和占位零向量。
        for dim in self._vector_dimensions(table):
            columns.append(self.ddl.vector_column_name(dim))
            columns.append(self.ddl.vector_empty_column_name(dim))
        return columns

    def _build_where_clause(
        self,
        condition: dict | None,
        memory_ids: list[str] | None = None,
        hide_forgotten: bool = False,
        force_memory_filter: bool = False,
    ) -> tuple[str, list[Any]]:
        fragments: list[str] = []
        params: list[Any] = []

        memory_values = clean_list_values(memory_ids)
        if memory_values:
            # memory_id 是同 tenant 共享表内最重要的强制边界。调用方传入的
            # condition 即使不包含 memory_id，这里也会从 search/update/delete
            # 的参数中补上。
            fragments.append(f"memory_id IN ({', '.join(['%s'] * len(memory_values))})")
            params.extend(memory_values)
        elif force_memory_filter:
            # update/delete 等写操作如果没有 memory_id 边界，直接构造永假条件，
            # 避免误操作整张 tenant 表。
            return "1=0", []

        if hide_forgotten:
            fragments.append("forget_at IS NULL")

        for field, value in (condition or {}).items():
            if field == "memory_id" and memory_values:
                continue
            if field == "exists":
                # 启动维护和补字段扫描会使用 exists/must_not exists 语义。列名仍
                # 必须经过字段映射和白名单校验，不能直接拼接外部输入。
                db_field = self.convert_field_name(str(value))
                self._validate_column(db_field)
                fragments.append(f"{db_field} IS NOT NULL")
                continue
            if field == "must_not" and isinstance(value, dict) and "exists" in value:
                db_field = self.convert_field_name(str(value["exists"]))
                self._validate_column(db_field)
                fragments.append(f"{db_field} IS NULL")
                continue
            if value is None or value == "":
                # 空过滤值按“未传条件”处理，与 ES/OB Memory adapter 的调用语义
                # 对齐；真实空字符串不作为可检索业务值存储。
                continue
            db_field = self.convert_field_name(field)
            self._validate_column(db_field)
            if isinstance(value, (list, tuple, set)):
                values = clean_list_values(value)
                if not values:
                    continue
                fragments.append(f"{db_field} IN ({', '.join(['%s'] * len(values))})")
                params.extend(to_int_or_none(v) if db_field in NUMERIC_COLUMNS else v for v in values)
                continue
            fragments.append(f"{db_field} = %s")
            params.append(to_int_or_none(value) if db_field in NUMERIC_COLUMNS else value)
        return " AND ".join(fragments), params

    def _build_order_by(self, order_by: OrderByExpr | None) -> str:
        fields = getattr(order_by, "fields", None) or []
        parts = []
        for field, direction in fields:
            db_field = self.convert_field_name(field)
            # ORDER BY 字段同样走列白名单，避免通过 order_by 注入任意 SQL 片段。
            self._validate_column(db_field)
            parts.append(f"{db_field} {'DESC' if direction else 'ASC'}")
        return ", ".join(parts)

    def _validate_column(self, column: str) -> None:
        # SQL 构造只接受基础列或受正则保护的动态向量列。这里是所有动态字段名
        # 拼接前的最后一道防线。
        if column in BASE_COLUMN_SET or VECTOR_COLUMN_RE.fullmatch(column) or VECTOR_EMPTY_COLUMN_RE.fullmatch(column):
            return
        raise InvalidGaussDBObjectName(column)

    def _ensure_vector_column_exists(self, table: str, dim: int) -> None:
        dim = self.ddl.validate_vector_dim(dim)
        vector_col = self.ddl.vector_column_name(dim)
        empty_col = self.ddl.vector_empty_column_name(dim)
        columns_exist = self._column_exists(table, vector_col) and self._column_exists(table, empty_col)
        statements: list[str | tuple[str, list[Any]]] = [
            self.ddl.build_advisory_lock_sql(f"gaussdb_memory_vector_column:{table}:{dim}"),
        ]
        if not columns_exist:
            # 只有列缺失时才执行 ADD COLUMN；空标记索引使用 IF NOT EXISTS，可以
            # 每次 ensure 时安全执行，修复“列存在但索引缺失”的半初始化状态。
            statements.extend(self.ddl.build_vector_column_ddls(table, dim))
        statements.append(self.ddl.build_vector_empty_index_ddl(table, dim))
        self._execute_statements(statements)
        if not self._diskann_index_exists(table, dim):
            # CREATE INDEX IF NOT EXISTS 在某些 GaussDB 版本上仍可能进入较重的
            # gsdiskann 初始化；先查 pg_indexes，避免每次写入都设置 work_mem。
            self._create_diskann_index_with_retry(table, dim)

    def _create_diskann_index_with_retry(self, table: str, dim: int) -> None:
        ddl = self.ddl.build_diskann_index_ddl(table, dim)
        lock = self.ddl.build_advisory_lock_sql(f"gaussdb_memory_vector_index:{table}:{dim}")
        for work_mem in ("1GB", "2GB", "4GB"):
            try:
                # gsdiskann 建索引对 maintenance_work_mem 敏感。按固定上限递增重试，
                # 只处理 work_mem 不足类错误；其它 DDL 错误继续向上抛出。
                self._execute_statements([lock, f"SET maintenance_work_mem = '{work_mem}'", ddl])
                return
            except Exception as exc:
                if not is_maintenance_work_mem_error(exc) or work_mem == "4GB":
                    raise
                logger.warning("Retrying GaussDB gsdiskann index with larger maintenance_work_mem after: %s", exc)

    def _table_exists(self, table: str) -> bool:
        row = self._fetch_one(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = %s
               AND table_name = %s
            """,
            [self.schema, table],
        )
        return bool(row)

    def _column_exists(self, table: str, column: str) -> bool:
        row = self._fetch_one(
            """
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
               AND column_name = %s
            """,
            [self.schema, table, column],
        )
        return bool(row)

    def _column_names(self, table: str) -> list[str]:
        rows = self._fetch_all(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = %s
               AND table_name = %s
            """,
            [self.schema, table],
        )
        return [row_value(row, "column_name", 0) for row in rows or []]

    def _index_names(self, table: str) -> list[str]:
        rows = self._fetch_all(
            """
            SELECT indexname
              FROM pg_indexes
             WHERE schemaname = %s
               AND tablename = %s
            """,
            [self.schema, table],
        )
        return [row_value(row, "indexname", 0) for row in rows or []]

    def _diskann_index_exists(self, table: str, dim: int) -> bool:
        dim = self.ddl.validate_vector_dim(dim)
        vector_col = self.ddl.vector_column_name(dim)
        expected_index = self.ddl.index_name(table, f"{vector_col}_diskann")
        # 不能只按索引名判断；如果历史索引用同名但不是 gsdiskann 创建，向量 ANN
        # 路径仍不可用。因此同时检查 indexdef 中的 using gsdiskann。
        row = self._fetch_one(
            """
            SELECT indexdef
              FROM pg_indexes
             WHERE schemaname = %s
               AND tablename = %s
               AND indexname = %s
            """,
            [self.schema, table, expected_index],
        )
        if not row:
            return False
        return "using gsdiskann" in str(row_value(row, "indexdef", 0)).lower()

    def _vector_dimensions(self, table: str) -> list[int]:
        dims = []
        # 通过 catalog 中现有 q_{dim}_vec 列反推该 tenant 表历史上出现过的
        # embedding 维度。写入、读取和 content_embed 展开都依赖这个列表。
        for column in self._column_names(table):
            match = VECTOR_COLUMN_RE.fullmatch(str(column or ""))
            if match:
                dims.append(int(match.group("dim")))
        return sorted(set(dims))

    def _execute_statements(self, statements: Iterable[str | tuple[str, list[Any]]]) -> None:
        # DDL/维护语句按一个事务提交；任意语句失败则回滚整组操作，避免留下只建
        # 了列但没建索引的半成品。
        conn = self.pool.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            for statement in statements:
                if isinstance(statement, tuple):
                    cur.execute(statement[0], statement[1])
                else:
                    cur.execute(statement)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            raise classify_gaussdb_exception(exc) from exc
        finally:
            close_cursor(cur)
            self.pool.put_conn(conn)

    def _execute_write(self, sql: str, params: list[Any], many: bool = False) -> int:
        # 写入路径统一在这里提交/回滚，并把底层 psycopg2 异常分类成 GaussDB
        # 连接/权限/认证异常，便于上层日志和健康检查识别。
        conn = self.pool.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            if many:
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params)
            conn.commit()
            return int(getattr(cur, "rowcount", 0) or 0)
        except Exception as exc:
            conn.rollback()
            raise classify_gaussdb_exception(exc) from exc
        finally:
            close_cursor(cur)
            self.pool.put_conn(conn)

    def _fetch_one(self, sql: str, params: list[Any]):
        row, _description = self._fetch_one_with_description(sql, params)
        return row

    def _fetch_one_with_description(self, sql: str, params: list[Any]):
        conn = self.pool.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchone(), getattr(cur, "description", None) or []
        finally:
            close_cursor(cur)
            self.pool.put_conn(conn)

    def _fetch_all(self, sql: str, params: list[Any]):
        conn = self.pool.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            close_cursor(cur)
            self.pool.put_conn(conn)

    def _fetch_all_with_description(self, sql: str, params: list[Any]):
        conn = self.pool.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchall(), getattr(cur, "description", None) or []
        finally:
            close_cursor(cur)
            self.pool.put_conn(conn)

    def _row_to_dict(self, row, description) -> dict:
        columns = [desc[0] for desc in description]
        return dict(zip(columns, row)) if not isinstance(row, dict) else dict(row)

    def _rows_to_messages(self, rows, description) -> tuple[int, list[dict]]:
        total = 0
        messages = []
        for row in rows or []:
            raw = self._row_to_dict(row, description)
            if raw.get("__total") is not None:
                # search SQL 通过 COUNT(*) OVER() 把总数附加到每一行。返回给上层
                # 的 message 中不应暴露内部 __total 列。
                total = int(raw.pop("__total") or 0)
            messages.append(raw)
        if total == 0:
            total = len(messages)
        return total, messages

    def _sort_messages(self, messages: list[dict], order_by: OrderByExpr | None, has_match: bool) -> list[dict]:
        if has_match:
            # 混合多 tenant 结果时，数据库只能保证单表内排序。匹配查询按分数降序、
            # valid_at 降序、id 升序做稳定合并，和 SQL ORDER BY 保持一致。
            return sorted(
                messages,
                key=lambda row: (
                    -float(numeric_sort_value(row.get("_score"))),
                    descending_timestamp_sort_value(row.get("valid_at")),
                    str(row.get("id") or ""),
                ),
            )
        fields = getattr(order_by, "fields", None) or []
        if not fields:
            return sorted(messages, key=lambda row: str(row.get("id") or ""))
        sorted_messages = list(messages)
        for field, direction in reversed(fields):
            db_field = self.convert_field_name(field)
            # Python 合并排序必须按字段类型排序：message_id/status 等按数值，
            # valid_at/forget_at 等按时间，不能退化成字符串比较。
            sorted_messages.sort(key=lambda row, db_field=db_field: sortable_value(row.get(db_field), db_field), reverse=bool(direction))
        return sorted_messages

    @staticmethod
    def _has_empty_delete_list(condition: dict) -> bool:
        for key in ("message_id", "id"):
            if key in condition and isinstance(condition[key], (list, tuple, set)) and not clean_list_values(condition[key]):
                return True
        return False


def normalize_index_names(index_names: str | list[str]) -> list[str]:
    # 上层有的接口传单个 index_name，有的传逗号拼接字符串，有的传列表。
    # 这里统一归一化为物理表名前的逻辑名列表；注意列表元素会先转字符串，
    # 这是当前行为，review 文档中会单独记录 None 被转成 "None" 的风险。
    if isinstance(index_names, str):
        return [name.strip() for name in index_names.split(",") if name.strip()]
    return [str(name).strip() for name in index_names or [] if str(name).strip()]


def clean_list_values(values) -> list[Any]:
    # 过滤 None 和空字符串，避免生成 IN () 或把空字符串当作真实业务过滤值。
    # 对单个字符串做列表化，便于 memory_id/message_id 统一处理。
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    result = []
    for value in values:
        if value is None or value == "":
            continue
        result.append(value)
    return result


def effective_limit(limit: int, topn: int | None = None) -> int:
    # topn 来自匹配表达式，limit 来自分页参数。为了避免返回超过任一上层约束
    # 的候选，取正数中的最小值；二者都没有时给一个保守上限。
    candidates = [int(value) for value in (limit, topn) if value and int(value) > 0]
    return min(candidates) if candidates else 10000


def parse_vector_dim(column: str) -> int | None:
    match = VECTOR_COLUMN_RE.fullmatch(str(column or ""))
    return int(match.group("dim")) if match else None


def vector_literal(value, dim: int) -> str:
    # GaussDB floatvector 参数最终以 "[1.0,2.0]" 形式绑定，并在 SQL 中显式
    # cast 为 floatvector(dim)。如果调用方已经传入字符串且能解析，就重新格式化；
    # 如果是不可解析字符串，保持原样交给数据库报错，便于暴露真实输入问题。
    if isinstance(value, str):
        parsed = parse_vector_value(value)
        if parsed:
            value = parsed
        else:
            return value
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or len(value) != int(dim):
        raise ValueError(f"vector dimension mismatch: expected {dim}, got {len(value) if hasattr(value, '__len__') else 'unknown'}")
    return "[" + ",".join(str(float(item)) for item in value) + "]"


def parse_vector_value(value) -> list[float]:
    # 读取时可能拿到 numpy/list/tuple、JSON 数组字符串或 GaussDB 返回的
    # "[...]" 文本。统一还原成 float list；无法识别时返回空列表，表示业务
    # 上没有可用 content_embed。
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        return [float(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    text = str(value).strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        if text.startswith("[") and text.endswith("]"):
            body = text[1:-1].strip()
            if not body:
                return []
            return [float(item.strip()) for item in body.split(",")]
        return []
    if isinstance(parsed, list):
        return [float(item) for item in parsed]
    return []


def zero_vector_literal(dim: int) -> str:
    # floatvector 空值占位使用全零向量；真实空语义由 q_{dim}_vec_empty 维护。
    return "[" + ",".join(["0"] * int(dim)) + "]"


def normalize_timestamp(value) -> str | None:
    # 写入数据库前把 "-", "", None 都当作 SQL NULL；这是 Memory 旧接口对
    # invalid_at/forget_at 的空值约定。
    if value in (None, "", "-"):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def format_timestamp(value) -> str | None:
    # 读回时只做格式化，不直接补 "-"；具体字段默认值由 _message_from_row 控制，
    # 便于其它内部路径区分真实 None。
    if value in (None, "", "-"):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def none_if_empty(value):
    # user_id/agent_id/session_id 这类可选文本字段不需要保存真实空字符串；
    # 转成 NULL 后过滤逻辑更接近“未传值”。
    if value == "":
        return None
    return value


def to_int_or_none(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, Decimal):
        return int(value)
    return int(value)


def to_int_or_original(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, Decimal):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def row_value(row, key: str, index: int):
    if isinstance(row, dict):
        return row.get(key)
    return row[index]


def unique_preserve_order(values: list[str]) -> list[str]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def sortable_value(value, field_name: str | None = None):
    # 跨表合并时不能简单按字符串排序，否则 message_id=100 会排在 99 前后不稳。
    # 根据物理列类型进入数值或时间排序，剩余字段才按字符串处理。
    if field_name in NUMERIC_COLUMNS:
        return numeric_sort_value(value)
    if field_name in TIME_COLUMNS:
        return timestamp_sort_value(value)
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def numeric_sort_value(value) -> Decimal:
    # Decimal 支持整数、字符串数字和布尔值的稳定比较；非法/空值统一放到
    # 负无穷，DESC 排序时自然落在最后。
    if value in (None, ""):
        return Decimal("-Infinity")
    if isinstance(value, bool):
        return Decimal(int(value))
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("-Infinity")


def timestamp_sort_value(value) -> float:
    # 接受 datetime、ISO 字符串和项目常见 "%Y-%m-%d %H:%M:%S" 格式。
    # 空时间按负无穷处理，便于 ASC/DESC 合并排序。
    if value in (None, "", "-"):
        return float("-inf")
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value).strip()
    if not text:
        return float("-inf")
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return float("-inf")


def descending_timestamp_sort_value(value) -> float:
    # sorted() 默认升序。把有效时间取负值，即可得到 valid_at DESC；空值转换
    # 为正无穷，保证匹配结果里缺时间的行排在后面。
    timestamp = timestamp_sort_value(value)
    if timestamp == float("-inf"):
        return float("inf")
    return -timestamp


def is_maintenance_work_mem_error(exc: Exception) -> bool:
    # gsdiskann 建索引时只对 maintenance_work_mem 不足进行自动重试，其它错误
    # 可能是 SQL 方言、权限或扩展问题，必须向上抛出。
    text = str(exc).lower()
    return "maintenance_work_mem" in text and ("required" in text or "below" in text or "insufficient" in text)


def close_cursor(cur) -> None:
    # 清理游标时吞掉 close 异常，避免掩盖前面真正的 SQL 错误。
    if cur is None:
        return
    try:
        cur.close()
    except Exception:
        pass
