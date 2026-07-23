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
import re
import threading
from dataclasses import dataclass
from typing import Any

from psycopg2 import pool as psycopg2_pool

logger = logging.getLogger("ragflow.gaussdb_conn_pool")
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class GaussDBError(Exception):
    """GaussDB DocEngine/Memory Store 配置和连接异常的统一基类。"""


class InvalidGaussDBConfig(ValueError, GaussDBError):
    """gaussdb.config 缺字段、端口非法或 schema 不安全时抛出。"""


class GaussDBConnectionError(GaussDBError):
    """连接池无法建立连接、连接失效或执行探针失败时抛出。"""


class GaussDBAuthenticationError(GaussDBConnectionError):
    """GaussDB 拒绝当前账号密码时抛出，便于健康检查区分认证失败。"""


class GaussDBPermissionError(GaussDBConnectionError):
    """当前账号缺少目标 schema 的 USAGE/CREATE 权限时抛出。"""


@dataclass(frozen=True)
class GaussDBConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    schema: str = "public"


def _normalize_schema(schema: Any) -> str:
    # schema 会写入 search_path，因此必须限制为单个普通标识符。DocEngine 和
    # Memory Store 都通过这个连接池进入 GaussDB，不能允许调用方把逗号、引号
    # 或额外 -c 参数塞进 schema 字段。
    value = str(schema or "").strip() or "public"
    if not _IDENTIFIER_PATTERN.match(value):
        raise InvalidGaussDBConfig(f"invalid gaussdb schema: {value}")
    return value


def load_gaussdb_config(raw: dict[str, Any] | None = None) -> GaussDBConfig:
    if raw is None:
        from common import settings

        raw = getattr(settings, "GAUSSDB", None) or settings.get_base_config("gaussdb", {})
    config = (raw or {}).get("config", {}) or {}
    # 这里读取的是 DOC_ENGINE=gaussdb 的 gaussdb.config，不是
    # DB_TYPE=gaussdb 的 GAUSSDB_METADATA_*。两条连接路径必须保持隔离。
    missing = [key for key in ("host", "port", "database", "user", "password") if not config.get(key)]
    if missing:
        raise InvalidGaussDBConfig(f"missing gaussdb config field(s): {', '.join(missing)}")

    try:
        port = int(config["port"])
    except (TypeError, ValueError) as exc:
        raise InvalidGaussDBConfig("invalid gaussdb config field: port") from exc
    if port <= 0 or port > 65535:
        raise InvalidGaussDBConfig("invalid gaussdb config field: port")

    return GaussDBConfig(
        host=str(config["host"]).strip(),
        port=port,
        database=str(config["database"]).strip(),
        user=str(config["user"]).strip(),
        password=str(config["password"]),
        schema=_normalize_schema(config.get("schema")),
    )


def mask_gaussdb_uri(cfg: GaussDBConfig) -> str:
    return f"{cfg.user}@{cfg.host}:{cfg.port}/{cfg.database}?schema={cfg.schema}"


def classify_gaussdb_exception(exc: Exception) -> GaussDBConnectionError:
    if isinstance(exc, GaussDBConnectionError):
        return exc
    text = str(exc).lower()
    if "password" in text or "authentication" in text or "invalid username" in text:
        return GaussDBAuthenticationError(str(exc))
    if "permission" in text or "privilege" in text:
        return GaussDBPermissionError(str(exc))
    return GaussDBConnectionError(str(exc))


class GaussDBConnectionPool:
    def __init__(
        self,
        config: GaussDBConfig | None = None,
        pool: Any | None = None,
        minconn: int = 1,
        maxconn: int = 8,
    ):
        self.config = config or load_gaussdb_config()
        self.resolved_schema = self.config.schema
        self.masked_uri = mask_gaussdb_uri(self.config)
        self._pool = pool or self._create_pool(minconn=minconn, maxconn=maxconn)

    def _create_pool(self, minconn: int, maxconn: int):
        try:
            return psycopg2_pool.ThreadedConnectionPool(
                minconn,
                maxconn,
                host=self.config.host,
                port=self.config.port,
                dbname=self.config.database,
                user=self.config.user,
                password=self.config.password,
                # DocEngine/Memory Store 需要写表、建索引和维护向量列，因此这里
                # 显式关闭 default_transaction_read_only。search_path 同时包含
                # 业务 schema 与 public，既让适配表落入目标 schema，也能访问
                # public 下的扩展类型/函数。
                options=(f"-c search_path={self.resolved_schema},public -c client_encoding=UTF8 -c default_transaction_read_only=off"),
            )
        except Exception as exc:
            raise classify_gaussdb_exception(exc) from exc

    def _discard_conn(self, conn) -> None:
        if conn is None:
            return
        try:
            self._pool.putconn(conn, close=True)
        except TypeError:
            self._pool.putconn(conn)

    def _validate_conn(self, conn) -> None:
        if getattr(conn, "closed", False):
            raise GaussDBConnectionError("GaussDB connection is closed")
        cur = None
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            conn.rollback()
        finally:
            if cur is not None:
                cur.close()

    def get_conn(self):
        last_exc = None
        for _attempt in range(2):
            conn = None
            try:
                conn = self._pool.getconn()
                self._validate_conn(conn)
                return conn
            except Exception as exc:
                last_exc = exc
                self._discard_conn(conn)
        if last_exc is not None:
            raise classify_gaussdb_exception(last_exc) from last_exc

    def put_conn(self, conn) -> None:
        if conn is None:
            return
        try:
            conn.rollback()
        except Exception as exc:
            logger.warning("Discarding GaussDB connection after rollback failure: %s", type(exc).__name__)
            try:
                self._discard_conn(conn)
            except Exception as discard_exc:
                logger.warning("Failed to discard GaussDB connection: %s", type(discard_exc).__name__)
            return
        self._pool.putconn(conn)

    def close_all(self) -> None:
        self._pool.closeall()

    def check_schema_access(self) -> None:
        conn = self.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                  has_schema_privilege(%s, %s, %s) AS has_usage,
                  has_schema_privilege(%s, %s, %s) AS has_create
                """,
                (
                    self.config.user,
                    self.resolved_schema,
                    "USAGE",
                    self.config.user,
                    self.resolved_schema,
                    "CREATE",
                ),
            )
            row = cur.fetchone()
            has_usage, has_create = (bool(row[0]), bool(row[1])) if row else (False, False)
            missing = []
            if not has_usage:
                missing.append("USAGE")
            if not has_create:
                missing.append("CREATE")
            if missing:
                raise GaussDBPermissionError(f"GaussDB user {self.config.user} lacks {', '.join(missing)} on schema {self.resolved_schema}")
        except GaussDBPermissionError:
            raise
        except Exception as exc:
            raise classify_gaussdb_exception(exc) from exc
        finally:
            if cur is not None:
                cur.close()
            self.put_conn(conn)

    def fetch_one(self, sql: str, params: tuple[Any, ...] | None = None):
        conn = self.get_conn()
        cur = None
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur.fetchone()
        except Exception as exc:
            raise classify_gaussdb_exception(exc) from exc
        finally:
            if cur is not None:
                cur.close()
            self.put_conn(conn)


class _LazyGaussDBConnectionPool:
    """DocEngine 与 Memory Store 共用的模块级懒加载连接池入口。

    RAGFlow 初始化时会分别创建 rag.utils.gaussdb_conn.GaussDBConnection 和
    memory.utils.gaussdb_conn.GaussDBMemoryConnection。两个 adapter 语义不同，
    但它们应读取同一份 gaussdb.config，并共享同一个 psycopg2 连接池，避免
    每个 adapter 各自预热连接、重复做 schema 权限检查或持有不一致的配置。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pool: GaussDBConnectionPool | None = None

    def get_pool(self) -> GaussDBConnectionPool:
        # 双重检查只控制连接池对象创建；具体连接并发安全仍由
        # psycopg2.pool.ThreadedConnectionPool 负责。
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = GaussDBConnectionPool()
        return self._pool

    def close_all(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.close_all()
                self._pool = None

    def __getattr__(self, name: str):
        # 让懒加载包装对象可以像真实 GaussDBConnectionPool 一样被旧代码访问
        # masked_uri、resolved_schema、get_conn() 等属性/方法。
        return getattr(self.get_pool(), name)


# 模块级共享入口。不要在 adapter 中直接 new GaussDBConnectionPool()，除非是
# 单测显式传入 fake pool；生产路径应通过这个对象保持连接池共享。
GAUSSDB_CONN = _LazyGaussDBConnectionPool()
