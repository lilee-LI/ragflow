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
import logging
from timeit import default_timer as timer

from common.doc_store.doc_store_base import DocStoreConnection, MatchExpr, OrderByExpr
from common.doc_store.gaussdb_conn_pool import GaussDBConnectionError, GaussDBConnectionPool


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
