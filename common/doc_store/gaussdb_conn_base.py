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
import hashlib
import logging
import re
from timeit import default_timer as timer

from common.doc_store.doc_store_base import DocStoreConnection, MatchExpr, OrderByExpr
from common.doc_store.gaussdb_conn_pool import GaussDBConnectionError, GaussDBConnectionPool


class InvalidGaussDBObjectName(ValueError):
    pass


class GaussDBDDLBuilder:
    MAX_IDENTIFIER_LENGTH = 63
    REGULAR_INDEX_COLUMNS = (
        "doc_id",
        "available_int",
        "knowledge_graph_kwd",
        "entity_type_kwd",
        "removed_kwd",
    )
    FTS_COLUMNS = (
        "title_tks",
        "title_sm_tks",
        "important_tks",
        "question_tks",
        "content_ltks",
        "content_sm_ltks",
    )

    def __init__(self, schema: str):
        self.schema = self.validate_identifier(schema)

    def validate_identifier(self, name: str) -> str:
        if not re.fullmatch(r"(?:[A-Za-z_]|[^\x00-\x7F])(?:[A-Za-z0-9_#$]|[^\x00-\x7F]){0,62}", name or ""):
            raise InvalidGaussDBObjectName(name)
        return name

    def quote_identifier(self, name: str) -> str:
        escaped = self.validate_identifier(name).replace('"', '""')
        return f'"{escaped}"'

    def qualified_name(self, table: str) -> str:
        return f"{self.quote_identifier(self.schema)}.{self.quote_identifier(table)}"

    def index_name(self, table: str, suffix: str) -> str:
        name = f"idx_gdb_{self.validate_identifier(table)}_{self.validate_identifier(suffix)}"
        if len(name) <= self.MAX_IDENTIFIER_LENGTH:
            return name
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
        prefix_len = self.MAX_IDENTIFIER_LENGTH - len(digest) - 1
        return f"{name[:prefix_len]}_{digest}"

    def build_chunk_table_ddl(self, table: str) -> str:
        name = self.qualified_name(table)
        return f"""CREATE TABLE IF NOT EXISTS {name} (
  id VARCHAR(256) NOT NULL,
  kb_id VARCHAR(256) NOT NULL,
  doc_id VARCHAR(256),
  docnm_kwd VARCHAR(256),
  doc_type_kwd VARCHAR(256),
  title_tks VARCHAR(256),
  title_sm_tks VARCHAR(256),
  content_with_weight TEXT,
  content_ltks TEXT,
  content_sm_ltks TEXT,
  important_kwd JSONB,
  important_tks TEXT,
  question_kwd JSONB,
  question_tks TEXT,
  tag_kwd JSONB,
  tag_feas JSONB,
  available_int INTEGER DEFAULT 1 NOT NULL,
  pagerank_fea INTEGER,
  create_time VARCHAR(19),
  create_timestamp_flt DOUBLE PRECISION,
  img_id VARCHAR(128),
  position_int JSONB,
  page_num_int JSONB,
  top_int JSONB,
  metadata JSONB,
  chunk_data JSONB,
  extra JSONB,
  _order_id INTEGER,
  group_id VARCHAR(256),
  mom_id VARCHAR(256),
  knowledge_graph_kwd VARCHAR(256),
  source_id JSONB,
  entity_kwd VARCHAR(256),
  entity_type_kwd VARCHAR(256),
  from_entity_kwd VARCHAR(256),
  to_entity_kwd VARCHAR(256),
  weight_int INTEGER,
  weight_flt DOUBLE PRECISION,
  entities_kwd JSONB,
  rank_flt DOUBLE PRECISION,
  n_hop_with_weight TEXT,
  removed_kwd VARCHAR(256) DEFAULT 'N',
  raptor_kwd VARCHAR(256),
  raptor_layer_int INTEGER,
  PRIMARY KEY (kb_id, id)
) WITH (storage_type=USTORE)"""

    def build_doc_meta_table_ddls(self, meta_table: str) -> list[str]:
        name = self.qualified_name(meta_table)
        idx = self.quote_identifier(self.index_name(meta_table, "kb_id"))
        return [
            f"""CREATE TABLE IF NOT EXISTS {name} (
  id VARCHAR(256) NOT NULL,
  kb_id VARCHAR(256) NOT NULL,
  meta_fields JSONB,
  PRIMARY KEY (id)
) WITH (storage_type=USTORE)""",
            f"CREATE INDEX IF NOT EXISTS {idx} ON {name} (kb_id)",
        ]

    def build_regular_index_ddls(self, table: str) -> list[str]:
        name = self.qualified_name(table)
        return [
            f"CREATE INDEX IF NOT EXISTS {self.quote_identifier(self.index_name(table, column))} ON {name} ({column})"
            for column in self.REGULAR_INDEX_COLUMNS
        ]

    def build_fulltext_ugin_ddl(self, table: str) -> str:
        name = self.qualified_name(table)
        idx = self.quote_identifier(self.index_name(table, "fts_all"))
        expression = " || ' ' || ".join(f"coalesce({column}, ' ')" for column in self.FTS_COLUMNS)
        return f"""CREATE INDEX IF NOT EXISTS {idx}
  ON {name}
  USING ugin(to_tsvector('simple', {expression}))"""

    def build_vector_column_ddls(self, table: str, dim: int) -> list[str]:
        dim = self.validate_vector_dim(dim)
        name = self.qualified_name(table)
        vector_col = self.vector_column_name(dim)
        valid_col = self.vector_valid_column_name(dim)
        return [
            f"ALTER TABLE {name} ADD COLUMN IF NOT EXISTS {vector_col} floatvector({dim}) DEFAULT (array_fill(0, ARRAY[{dim}])::text::floatvector({dim}))",
            f"ALTER TABLE {name} ADD COLUMN IF NOT EXISTS {valid_col} BOOLEAN DEFAULT FALSE NOT NULL",
        ]

    def build_diskann_index_ddl(self, table: str, dim: int) -> str:
        dim = self.validate_vector_dim(dim)
        name = self.qualified_name(table)
        vector_col = self.vector_column_name(dim)
        idx = self.quote_identifier(self.index_name(table, f"{vector_col}_diskann"))
        options = "subgraph_count=1"
        if dim > 1024:
            options += ", enable_vector_copy=false"
        return f"CREATE INDEX IF NOT EXISTS {idx} ON {name} USING gsdiskann ({vector_col} COSINE) WITH ({options})"

    def build_advisory_lock_sql(self, lock_name: str) -> tuple[str, list[str]]:
        return ("SELECT pg_advisory_xact_lock(hashtext(%s))", [str(lock_name)])

    def validate_vector_dim(self, dim: int) -> int:
        try:
            value = int(dim)
        except (TypeError, ValueError) as exc:
            raise ValueError("vector dimension must be an integer") from exc
        if value <= 0:
            raise ValueError("vector dimension must be positive")
        if value > 4096:
            raise ValueError("GaussDB floatvector dimensions cannot exceed 4096")
        return value

    def vector_column_name(self, dim: int) -> str:
        return f"q_{self.validate_vector_dim(dim)}_vec"

    def vector_valid_column_name(self, dim: int) -> str:
        return f"q_{self.validate_vector_dim(dim)}_vec_valid"


class GaussDBConnectionBase(DocStoreConnection):
    def __init__(self, pool: GaussDBConnectionPool | None = None, logger_name: str = "ragflow.gaussdb_conn"):
        self.logger = logging.getLogger(logger_name)
        self.pool = pool or GaussDBConnectionPool()
        self.masked_uri = self.pool.masked_uri
        self.resolved_schema = self.pool.resolved_schema
        self.pool.check_schema_access()
        self.logger.info("GaussDB %s connection initialized.", self.masked_uri)

    def db_type(self) -> str:
        return "gaussdb"

    def health(self) -> dict:
        result = {
            "status": "unhealthy",
            "uri": self.masked_uri,
            "version_comment": "unknown",
            "schema": self.resolved_schema,
        }
        try:
            result["version_comment"] = self._query_version()
            result["sql_compatibility"] = self._query_sql_compatibility()
            if result["sql_compatibility"] not in {"A", "ORA"}:
                result["error"] = f"unsupported GaussDB compatibility, expected A/ORA: sql_compatibility={result['sql_compatibility']}"
                return result
            result["status"] = "healthy"
            return result
        except Exception as exc:
            result["error"] = str(exc)
            return result

    def _query_version(self) -> str:
        return self._query_required_scalar("SELECT version()", "version")

    def _query_sql_compatibility(self) -> str:
        return self._query_required_scalar("SHOW sql_compatibility", "sql_compatibility").upper()

    def _query_required_scalar(self, sql: str, field_name: str) -> str:
        row = self.pool.fetch_one(sql)
        if not row or row[0] is None or str(row[0]).strip() == "":
            raise GaussDBConnectionError(f"GaussDB {field_name} query returned no rows")
        return str(row[0]).strip()

    def get_performance_metrics(self) -> dict:
        st = timer()
        try:
            self.pool.fetch_one("SELECT 1")
            return {
                "connection": "connected",
                "latency_ms": round((timer() - st) * 1000.0, 3),
                "schema": self.resolved_schema,
            }
        except Exception as exc:
            return {
                "connection": "disconnected",
                "latency_ms": round((timer() - st) * 1000.0, 3),
                "error": str(exc),
            }

    def create_idx(self, index_name: str, dataset_id: str, vector_size: int, parser_id: str = None):
        raise NotImplementedError("GaussDB create_idx is implemented in the DDL task")

    def delete_idx(self, index_name: str, dataset_id: str):
        raise NotImplementedError("GaussDB delete_idx is implemented in the CRUD task")

    def index_exist(self, index_name: str, dataset_id: str) -> bool:
        raise NotImplementedError("GaussDB index_exist is implemented in the DDL task")

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
        dataset_ids: list[str],
        agg_fields: list[str] | None = None,
        rank_feature: dict | None = None,
    ):
        raise NotImplementedError("GaussDB search is implemented in the search task")

    def get(self, data_id: str, index_name: str, dataset_ids: list[str]) -> dict | None:
        raise NotImplementedError("GaussDB get is implemented in the CRUD task")

    def insert(self, rows: list[dict], index_name: str, dataset_id: str = None) -> list[str]:
        raise NotImplementedError("GaussDB insert is implemented in the CRUD task")

    def update(self, condition: dict, new_value: dict, index_name: str, dataset_id: str) -> bool:
        raise NotImplementedError("GaussDB update is implemented in the CRUD task")

    def delete(self, condition: dict, index_name: str, dataset_id: str) -> int:
        raise NotImplementedError("GaussDB delete is implemented in the CRUD task")

    def get_total(self, res):
        raise NotImplementedError("GaussDB get_total is implemented in the adapter task")

    def get_doc_ids(self, res):
        raise NotImplementedError("GaussDB get_doc_ids is implemented in the adapter task")

    def get_fields(self, res, fields: list[str]) -> dict[str, dict]:
        raise NotImplementedError("GaussDB get_fields is implemented in the adapter task")

    def get_highlight(self, res, keywords: list[str], field_name: str):
        raise NotImplementedError("GaussDB get_highlight is implemented in the search task")

    def get_aggregation(self, res, field_name: str):
        raise NotImplementedError("GaussDB get_aggregation is implemented in the search task")

    def sql(self, sql: str, fetch_size: int, format: str):
        raise NotImplementedError("GaussDB sql is implemented in the Text-to-SQL task")
