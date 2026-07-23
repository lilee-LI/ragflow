#
#  Copyright 2024 The InfiniFlow Authors. All Rights Reserved.
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
import inspect
import logging
import operator
import os
import sys
import time
import typing
from datetime import datetime, timezone
from enum import Enum
from functools import wraps

from quart_auth import AuthUser
from itsdangerous.url_safe import URLSafeTimedSerializer as Serializer
from psycopg2 import sql as psycopg2_sql
from peewee import (
    fn,
    InterfaceError,
    OperationalError,
    ProgrammingError,
    BigIntegerField,
    BooleanField,
    CharField,
    CompositeKey,
    DateTimeField,
    Field,
    FloatField,
    IntegerField,
    Metadata,
    Model,
    ModelInsert,
    TextField,
)
from playhouse.migrate import MySQLMigrator, PostgresqlMigrator, migrate
from playhouse.pool import PooledMySQLDatabase, PooledPostgresqlDatabase

from api import utils
from api.db import SerializedType
from api.db.gaussdb_error_utils import (
    is_duplicate_column_error,
    is_duplicate_object_error,
    is_psycopg_connection_error,
    is_undefined_object_error,
    sqlstate_from_exception,
)
from api.utils.json_encode import json_dumps, json_loads
from api.utils.configs import deserialize_b64, serialize_b64

from common.time_utils import current_timestamp, timestamp_to_date, date_string_to_timestamp
from common.decorator import singleton
from common.constants import ParserType, MAXIMUM_TASK_PAGE_NUMBER
from common import settings


CONTINUOUS_FIELD_TYPE = {IntegerField, FloatField, DateTimeField}
AUTO_DATE_TIMESTAMP_FIELD_PREFIX = {"create", "start", "end", "update", "read_access", "write_access"}
GAUSSDB_COMPATIBLE_DATABASE_TYPES = {"GAUSSDB"}


def is_gaussdb_compatible_database() -> bool:
    return settings.DATABASE_TYPE.upper() in GAUSSDB_COMPATIBLE_DATABASE_TYPES


class TextFieldType(Enum):
    MYSQL = "LONGTEXT"
    OCEANBASE = "LONGTEXT"
    POSTGRES = "TEXT"
    # 适配点：GaussDB 不支持 MySQL 的 LONGTEXT 语义，长文本使用 GaussDB
    # compatible 的 TEXT 映射，供 JSONField/LongTextField 使用。这里独立
    # 声明 GAUSSDB 枚举值，避免把 DB_TYPE=gaussdb 等同于 DB_TYPE=postgres。
    GAUSSDB = "TEXT"


class LongTextField(TextField):
    field_type = TextFieldType[settings.DATABASE_TYPE.upper()].value


class GaussDBEmptyStringFieldMixin:
    """GaussDB 空字符串兼容字段。

    适配背景：当前真实 GaussDB A-compatible 实例会把 SQL 字面量和绑定参数
    中的空字符串 `''` 当作 NULL 处理。RAGFlow 历史上有少量字段使用空字符串
    表示“未配置”，这些字段在 MySQL/PostgreSQL 下可以继续是 NOT NULL + ""；
    但在 GaussDB 下如果仍然建成 NOT NULL，初始化数据和业务接口传入 "" 时会
    被数据库按 NULL 拦截。

    适配边界：只把明确列入 5.3 的字段改成这个 Field，不做 DB.execute_sql 或
    cursor 层的全局 NULL->"" 转换，避免把真正有业务含义的 NULL 误改为空字符串。
    """

    __hash__ = Field.__hash__

    def __init__(self, *args, **kwargs):
        if is_gaussdb_compatible_database():
            # GaussDB 适配点：这些字段的“应用层空字符串”在存储层用 NULL 表示。
            # 建表和加列时必须允许 NULL，否则 GaussDB A-compatible 会在写入 ""
            # 时触发 NOT NULL 约束错误。MySQL/PostgreSQL 不进入该分支，仍保持
            # 原有 NOT NULL 约束。
            kwargs["null"] = True
        super().__init__(*args, **kwargs)

    def db_value(self, value):
        if is_gaussdb_compatible_database() and value == "":
            # GaussDB 适配点：显式把应用层 "" 归一化为 NULL。这样即使后续测试库
            # 或用户库切到 PG-compatible 模式，不再由数据库自动把 "" 转 NULL，
            # 这些字段的存储语义仍然稳定。
            return None
        return super().db_value(value)

    def python_value(self, value):
        if is_gaussdb_compatible_database() and value is None:
            # GaussDB 适配点：调用方仍按历史约定看到 ""，不需要在业务代码里到处
            # 增加 `is None` 分支；隔离逻辑收敛在字段类型本身。
            return ""
        return super().python_value(value)

    def __eq__(self, rhs):
        if is_gaussdb_compatible_database() and rhs == "":
            # GaussDB A-compatible 下 `field = ''` 实际等价于 `field = NULL`，
            # WHERE 条件不会命中任何行。这里不再把 "" 作为绑定参数传给
            # 数据库，而是用 `IS NULL OR LENGTH(field) = 0` 表达应用层空值：
            # 既能命中本适配写入的 NULL，也能兼容历史遗留的真实空字符串。
            return self.is_null(True) | (fn.LENGTH(self) == 0)
        return super().__eq__(rhs)

    def __ne__(self, rhs):
        if is_gaussdb_compatible_database() and rhs == "":
            # 与 __eq__ 对称：应用层“不等于空字符串”表示存储层有实际值。
            # 用 LENGTH(field) > 0 避免 `field != ''` 在 A-compatible 下变成
            # 与 NULL 比较，同时自然排除 NULL 和真实空字符串。
            return fn.LENGTH(self) > 0
        return super().__ne__(rhs)


class GaussDBEmptyStringCharField(GaussDBEmptyStringFieldMixin, CharField):
    pass


class GaussDBEmptyStringTextField(GaussDBEmptyStringFieldMixin, TextField):
    pass


class GaussDBEmptyStringLongTextField(GaussDBEmptyStringFieldMixin, LongTextField):
    pass


class JSONField(LongTextField):
    default_value = {}

    def __init__(self, object_hook=None, object_pairs_hook=None, **kwargs):
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if value is None:
            value = self.default_value
        return json_dumps(value)

    def python_value(self, value):
        if not value:
            return self.default_value
        return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)


class ListField(JSONField):
    default_value = []


class SerializedField(LongTextField):
    def __init__(self, serialized_type=SerializedType.PICKLE, object_hook=None, object_pairs_hook=None, **kwargs):
        self._serialized_type = serialized_type
        self._object_hook = object_hook
        self._object_pairs_hook = object_pairs_hook
        super().__init__(**kwargs)

    def db_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return serialize_b64(value, to_str=True)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return None
            return json_dumps(value, with_type=True)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")

    def python_value(self, value):
        if self._serialized_type == SerializedType.PICKLE:
            return deserialize_b64(value)
        elif self._serialized_type == SerializedType.JSON:
            if value is None:
                return {}
            return json_loads(value, object_hook=self._object_hook, object_pairs_hook=self._object_pairs_hook)
        else:
            raise ValueError(f"the serialized type {self._serialized_type} is not supported")


def is_continuous_field(cls: typing.Type) -> bool:
    if cls in CONTINUOUS_FIELD_TYPE:
        return True
    for p in cls.__bases__:
        if p in CONTINUOUS_FIELD_TYPE:
            return True
        elif p is not Field and p is not object:
            if is_continuous_field(p):
                return True
    else:
        return False


def auto_date_timestamp_field():
    return {f"{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def auto_date_timestamp_db_field():
    return {f"f_{f}_time" for f in AUTO_DATE_TIMESTAMP_FIELD_PREFIX}


def remove_field_name_prefix(field_name):
    return field_name[2:] if field_name.startswith("f_") else field_name


class BaseModel(Model):
    create_time = BigIntegerField(null=True, index=True)
    create_date = DateTimeField(null=True, index=True)
    update_time = BigIntegerField(null=True, index=True)
    update_date = DateTimeField(null=True, index=True)

    def to_json(self):
        # This function is obsolete
        return self.to_dict()

    def to_dict(self):
        return self.__dict__["__data__"]

    def to_human_model_dict(self, only_primary_with: list = None):
        model_dict = self.__dict__["__data__"]

        if not only_primary_with:
            return {remove_field_name_prefix(k): v for k, v in model_dict.items()}

        human_model_dict = {}
        for k in self._meta.primary_key.field_names:
            human_model_dict[remove_field_name_prefix(k)] = model_dict[k]
        for k in only_primary_with:
            human_model_dict[k] = model_dict[f"f_{k}"]
        return human_model_dict

    @property
    def meta(self) -> Metadata:
        return self._meta

    @classmethod
    def get_primary_keys_name(cls):
        return cls._meta.primary_key.field_names if isinstance(cls._meta.primary_key, CompositeKey) else [cls._meta.primary_key.name]

    @classmethod
    def getter_by(cls, attr):
        return operator.attrgetter(attr)(cls)

    @classmethod
    def query(cls, reverse=None, order_by=None, **kwargs):
        filters = []
        for f_n, f_v in kwargs.items():
            attr_name = "%s" % f_n
            if not hasattr(cls, attr_name) or f_v is None:
                continue
            if type(f_v) in {list, set}:
                f_v = list(f_v)
                if is_continuous_field(type(getattr(cls, attr_name))):
                    if len(f_v) == 2:
                        for i, v in enumerate(f_v):
                            if isinstance(v, str) and f_n in auto_date_timestamp_field():
                                # time type: %Y-%m-%d %H:%M:%S
                                f_v[i] = date_string_to_timestamp(v)
                        lt_value = f_v[0]
                        gt_value = f_v[1]
                        if lt_value is not None and gt_value is not None:
                            filters.append(cls.getter_by(attr_name).between(lt_value, gt_value))
                        elif lt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) >= lt_value)
                        elif gt_value is not None:
                            filters.append(operator.attrgetter(attr_name)(cls) <= gt_value)
                else:
                    filters.append(operator.attrgetter(attr_name)(cls) << f_v)
            else:
                filters.append(operator.attrgetter(attr_name)(cls) == f_v)
        if filters:
            query_records = cls.select().where(*filters)
            if reverse is not None:
                if not order_by or not hasattr(cls, f"{order_by}"):
                    order_by = "create_time"
                if reverse is True:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").desc())
                elif reverse is False:
                    query_records = query_records.order_by(cls.getter_by(f"{order_by}").asc())
            return [query_record for query_record in query_records]
        else:
            return []

    @classmethod
    def insert(cls, __data=None, **insert):
        if isinstance(__data, dict) and __data:
            __data[cls._meta.combined["create_time"]] = current_timestamp()
        if insert:
            insert["create_time"] = current_timestamp()

        return super().insert(__data, **insert)

    # update and insert will call this method
    @classmethod
    def _normalize_data(cls, data, kwargs):
        normalized = super()._normalize_data(data, kwargs)
        if not normalized:
            return {}

        normalized[cls._meta.combined["update_time"]] = current_timestamp()

        for f_n in AUTO_DATE_TIMESTAMP_FIELD_PREFIX:
            if {f"{f_n}_time", f"{f_n}_date"}.issubset(cls._meta.combined.keys()) and cls._meta.combined[f"{f_n}_time"] in normalized and normalized[cls._meta.combined[f"{f_n}_time"]] is not None:
                normalized[cls._meta.combined[f"{f_n}_date"]] = timestamp_to_date(normalized[cls._meta.combined[f"{f_n}_time"]])

        return normalized


class JsonSerializedField(SerializedField):
    def __init__(self, object_hook=utils.from_dict_hook, object_pairs_hook=None, **kwargs):
        super(JsonSerializedField, self).__init__(serialized_type=SerializedType.JSON, object_hook=object_hook, object_pairs_hook=object_pairs_hook, **kwargs)


class RetryingPooledMySQLDatabase(PooledMySQLDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ["", "Lost connection"]
                should_retry = (hasattr(e, "args") and e.args and e.args[0] in error_codes) or (str(e) in error_messages) or (hasattr(e, "__class__") and e.__class__.__name__ == "InterfaceError")

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"Database connection issue (attempt {attempt + 1}/{self.max_retries}): {e}")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    logging.error(f"DB execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        # self.close_all()
        # self.connect()
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ["", "Lost connection"]

                should_retry = (hasattr(e, "args") and e.args and e.args[0] in error_codes) or (str(e) in error_messages) or (hasattr(e, "__class__") and e.__class__.__name__ == "InterfaceError")

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"Lost connection during transaction (attempt {attempt + 1}/{self.max_retries})")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    raise
        return None


class GaussDBPsycopgRetryMixin:
    """Connection retry behavior used only by the GaussDB adapter."""

    database_display_name = "GaussDB"

    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def _prepare_sql_for_execution(self, sql):
        return sql

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                prepared_sql = self._prepare_sql_for_execution(sql)
                return super().execute_sql(prepared_sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # 适配点：Peewee 包装 psycopg2 异常后，真实 SQLSTATE 可能藏在
                # `__context__`，不能只匹配英文错误文本。这里统一交给
                # gaussdb_error_utils，优先识别 08xxx/57P0x 连接类 SQLSTATE，再用
                # GaussDB 实测的连接关闭文本兜底。
                should_retry = is_psycopg_connection_error(e)

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"{self.database_display_name} connection issue (attempt {attempt + 1}/{self.max_retries}): {e}")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    logging.error(f"{self.database_display_name} execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to {self.database_display_name}: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to {self.database_display_name} on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                # 与 execute_sql 保持同一套连接错误判断，避免事务开始阶段和 SQL
                # 执行阶段对 GaussDB/libpq 错误码的处理不一致。
                should_retry = is_psycopg_connection_error(e)

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"{self.database_display_name} connection lost during transaction (attempt {attempt + 1}/{self.max_retries})")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    raise
        return None


class RetryingPooledPostgresqlDatabase(PooledPostgresqlDatabase):
    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # PostgreSQL specific error codes
                # 57P01: admin_shutdown
                # 57P02: crash_shutdown
                # 57P03: cannot_connect_now
                # 08006: connection_failure
                # 08003: connection_does_not_exist
                # 08000: connection_exception
                error_messages = ["connection", "server closed", "connection refused", "no connection to the server", "terminating connection"]

                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"PostgreSQL connection issue (attempt {attempt + 1}/{self.max_retries}): {e}")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    logging.error(f"PostgreSQL execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to PostgreSQL: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to PostgreSQL on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_messages = ["connection", "server closed", "connection refused", "no connection to the server", "terminating connection"]

                should_retry = any(msg in str(e).lower() for msg in error_messages)

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"PostgreSQL connection lost during transaction (attempt {attempt + 1}/{self.max_retries})")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    raise
        return None


class RetryingPooledGaussDBDatabase(GaussDBPsycopgRetryMixin, PooledPostgresqlDatabase):
    """GaussDB 业务元数据库连接池。

    GaussDB 对外暴露 psycopg/libpq-compatible 协议，因此 Peewee 传输层仍使用
    PooledPostgresqlDatabase。但 adapter 类必须独立保留：DB_TYPE=gaussdb 的
    SQL 方言差异、兼容模式检查、日志标签和诊断逻辑后续都应落在这个类或
    GaussDBMigrator 中，不能通过把 gaussdb 改写成 postgres 来复用路径。
    """

    database_display_name = "GaussDB"
    # These tables have both an id primary key and a business unique key that
    # does not contain id. Distributed HASH tables cannot enforce both sets of
    # constraints globally, so preserve the existing schema with replication.
    distributed_replication_tables = frozenset(
        {
            "compilation_template",
            "compilation_template_group",
            "file_commit_item",
            "tenant_llm",
            "tenant_model_provider",
            "user",
        }
    )

    def __init__(self, *args, **kwargs):
        self._is_distributed = None
        # 适配点：真实 GaussDB 环境里需要显式保证 client_encoding=UTF8，
        # 否则包含中文、JSON 文本或模板数据的元数据读写可能受连接默认编码影响。
        kwargs.setdefault("options", "-c client_encoding=UTF8")
        super().__init__(*args, **kwargs)

    def _initialize_connection(self, conn):
        """Initialize a checked-out connection and detect its deployment type."""
        super()._initialize_connection(conn)

        with conn.cursor() as cursor:
            cursor.execute("SELECT current_schema()")
            row = cursor.fetchone()
            schema = row[0] if row else None
            if not schema:
                raise OperationalError("GaussDB metadata search_path has no existing schema")
            # SET is repeated after connection startup because a distributed CN
            # may not propagate the libpq search_path option to every DN.
            cursor.execute(psycopg2_sql.SQL("SET search_path TO {}").format(psycopg2_sql.Identifier(schema)))

            try:
                cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_catalog.pgxc_node WHERE node_type = 'D')")
                topology_row = cursor.fetchone()
                is_distributed = bool(topology_row and topology_row[0])
            except Exception as exc:
                # Centralized editions may not expose pgxc_node at all, or may
                # reject querying it as an unsupported feature.
                if sqlstate_from_exception(exc) not in {"0A000", "42P01"}:
                    raise
                rollback = getattr(conn, "rollback", None)
                if callable(rollback):
                    rollback()
                # A failed catalog probe can abort the startup transaction and
                # roll back the preceding SET. Restore the configured schema so
                # the centralized connection remains immediately usable.
                cursor.execute(psycopg2_sql.SQL("SET search_path TO {}").format(psycopg2_sql.Identifier(schema)))
                is_distributed = False

        if self._is_distributed is not None and self._is_distributed != is_distributed:
            raise RuntimeError("GaussDB deployment topology changed across pooled connections")
        if self._is_distributed is None:
            logging.info(
                "Detected %s GaussDB metadata deployment",
                "distributed" if is_distributed else "centralized",
            )
        self._is_distributed = is_distributed

    @property
    def is_distributed(self):
        if self._is_distributed is None:
            raise RuntimeError("GaussDB deployment topology is unavailable before connection initialization")
        return self._is_distributed

    def _ensure_topology(self):
        if self._is_distributed is None:
            # This goes through GaussDBPsycopgRetryMixin so a transient first-connect
            # failure gets the same retry treatment as any other SQL execution.
            self.execute_sql("SELECT 1")

    def prepare_update_by_id(self, model, pid, data):
        """Remove only redundant primary-key assignments on distributed GaussDB."""
        self._ensure_topology()
        if not self.is_distributed:
            return data

        primary_key = model._meta.primary_key
        if isinstance(primary_key, CompositeKey) or primary_key.name not in data:
            return data

        field_name = primary_key.name
        locator_value = primary_key.db_value(pid)
        update_value = primary_key.db_value(data[field_name])
        if locator_value != update_value:
            return data

        prepared = data.copy()
        del prepared[field_name]
        return prepared

    def replace_update_by_id(self, model, pid, data):
        """Replace a row when distributed GaussDB must change its primary key.

        A distributed table commonly uses its primary key as the distribution
        key, which GaussDB does not allow an UPDATE statement to modify. Keep
        the generic UPDATE path for centralized GaussDB and for updates that do
        not really change the key. A real key change is performed atomically as
        delete plus insert while preserving every existing column.

        Return ``None`` when the caller should execute its normal UPDATE, or
        the affected-row count when this method handled the replacement.
        """
        self._ensure_topology()
        if not self.is_distributed:
            return None

        primary_key = model._meta.primary_key
        if isinstance(primary_key, CompositeKey) or primary_key.name not in data:
            return None

        field_name = primary_key.name
        locator_value = primary_key.db_value(pid)
        update_value = primary_key.db_value(data[field_name])
        if locator_value == update_value:
            return None

        with self.atomic():
            record = model.get_or_none(primary_key == pid)
            if record is None:
                return 0

            replacement = record.__data__.copy()
            replacement.update(data)
            deleted = model.delete().where(primary_key == pid).execute()
            if deleted != 1:
                return deleted
            # Bypass BaseModel.insert(), which refreshes create_time. Replacing
            # a distribution key must retain the original row's creation data.
            ModelInsert(model, replacement).execute()
            return deleted

    def _prepare_distributed_ddl(self, sql):
        if not self.is_distributed or not isinstance(sql, str):
            return sql

        statement = sql.rstrip()
        trailing_whitespace = sql[len(statement) :]
        terminator = ";" if statement.endswith(";") else ""
        if terminator:
            statement = statement[:-1].rstrip()

        for table_name in self.distributed_replication_tables:
            quoted_name = f'"{table_name}"'
            if statement.startswith(f"CREATE TABLE IF NOT EXISTS {quoted_name} ") or statement.startswith(f"CREATE TABLE {quoted_name} "):
                return f"{statement} DISTRIBUTE BY REPLICATION{terminator}{trailing_whitespace}"
        return sql

    def _prepare_sql_for_execution(self, sql):
        # Topology initialization intentionally runs inside the retry loop in
        # GaussDBPsycopgRetryMixin. This also ensures the first CREATE TABLE receives
        # its distributed table clause before Peewee executes it.
        if self._is_distributed is None:
            self.connection()
        return self._prepare_distributed_ddl(sql)

    def get_tables(self, schema=None):
        if schema is not None:
            return super().get_tables(schema)

        cursor = self.execute_sql("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = current_schema() ORDER BY tablename")
        return [table for (table,) in cursor.fetchall()]


class RetryingPooledOceanBaseDatabase(PooledMySQLDatabase):
    """Pooled OceanBase database with retry mechanism.

    OceanBase is compatible with MySQL protocol, so we inherit from PooledMySQLDatabase.
    This class provides connection pooling and automatic retry for connection issues.
    """

    def __init__(self, *args, **kwargs):
        self.max_retries = kwargs.pop("max_retries", 5)
        self.retry_delay = kwargs.pop("retry_delay", 1)
        super().__init__(*args, **kwargs)

    def execute_sql(self, sql, params=None, commit=True):
        for attempt in range(self.max_retries + 1):
            try:
                return super().execute_sql(sql, params, commit)
            except (OperationalError, InterfaceError) as e:
                # OceanBase/MySQL specific error codes
                # 2013: Lost connection to MySQL server during query
                # 2006: MySQL server has gone away
                error_codes = [2013, 2006]
                error_messages = ["", "Lost connection", "gone away"]

                should_retry = (
                    (hasattr(e, "args") and e.args and e.args[0] in error_codes)
                    or any(msg in str(e).lower() for msg in error_messages)
                    or (hasattr(e, "__class__") and e.__class__.__name__ == "InterfaceError")
                )

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"OceanBase connection issue (attempt {attempt + 1}/{self.max_retries}): {e}")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    logging.error(f"OceanBase execution failure: {e}")
                    raise
        return None

    def _handle_connection_loss(self):
        try:
            self.close()
        except Exception:
            pass
        try:
            self.connect()
        except Exception as e:
            logging.error(f"Failed to reconnect to OceanBase: {e}")
            time.sleep(0.1)
            try:
                self.connect()
            except Exception as e2:
                logging.error(f"Failed to reconnect to OceanBase on second attempt: {e2}")
                raise

    def begin(self):
        for attempt in range(self.max_retries + 1):
            try:
                return super().begin()
            except (OperationalError, InterfaceError) as e:
                error_codes = [2013, 2006]
                error_messages = ["", "Lost connection"]

                should_retry = (hasattr(e, "args") and e.args and e.args[0] in error_codes) or (str(e) in error_messages) or (hasattr(e, "__class__") and e.__class__.__name__ == "InterfaceError")

                if should_retry and attempt < self.max_retries:
                    logging.warning(f"Lost connection during transaction (attempt {attempt + 1}/{self.max_retries})")
                    self._handle_connection_loss()
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    raise
        return None


class PooledDatabase(Enum):
    MYSQL = RetryingPooledMySQLDatabase
    OCEANBASE = RetryingPooledOceanBaseDatabase
    POSTGRES = RetryingPooledPostgresqlDatabase
    # 适配点：DB_TYPE=gaussdb 时创建独立枚举项，避免把 settings.DATABASE_TYPE
    # 强行改写成 postgres；这样日志、健康检查和 Admin 展示仍能看到真实数据库类型。
    GAUSSDB = RetryingPooledGaussDBDatabase


class GaussDBMigrator(PostgresqlMigrator):
    # 适配点：Peewee 没有内置 GaussDBMigrator。GaussDB 常规 DDL 与
    # PostgresqlMigrator 生成的 ADD COLUMN/ALTER COLUMN/ADD INDEX 语法兼容，
    # 因此当前类继承 PostgresqlMigrator；但 enum 入口保持独立，后续一旦发现
    # GaussDB DDL 差异，可以只改这个类，不影响 DB_TYPE=postgres。
    pass


class DatabaseMigrator(Enum):
    MYSQL = MySQLMigrator
    OCEANBASE = MySQLMigrator
    POSTGRES = PostgresqlMigrator
    # 适配点：DB_TYPE=gaussdb 使用 GaussDB-compatible migrator 入口，
    # 不进入 PostgreSQL enum 值。当前底层 DDL 语法继承自 PostgresqlMigrator，
    # 但兼容边界由 GaussDBMigrator 承接。
    GAUSSDB = GaussDBMigrator


@singleton
class BaseDataBase:
    def __init__(self):
        database_config = settings.DATABASE.copy()
        db_name = database_config.pop("name")

        pool_config = {
            "max_retries": 5,
            "retry_delay": 1,
        }
        # 适配点：连接重试参数不是配置文件字段，而是 RAGFlow 运行时给
        # MySQL/PostgreSQL/GaussDB/OceanBase adapter 注入的统一能力。GaussDB
        # adapter 会在 GaussDBPsycopgRetryMixin 中按 SQLSTATE 判断是否重试。
        database_config.update(pool_config)
        self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        # self.database_connection = PooledDatabase[settings.DATABASE_TYPE.upper()].value(db_name, **database_config)
        logging.info("init database on cluster mode successfully")


def with_retry(max_retries=3, retry_delay=1.0):
    """Decorator: Add retry mechanism to database operations

    Args:
        max_retries (int): maximum number of retries
        retry_delay (float): initial retry delay (seconds), will increase exponentially

    Returns:
        decorated function
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for retry in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    # get self and method name for logging
                    self_obj = args[0] if args else None
                    func_name = func.__name__
                    lock_name = getattr(self_obj, "lock_name", "unknown") if self_obj else "unknown"

                    if retry < max_retries - 1:
                        current_delay = retry_delay * (2**retry)
                        logging.warning(f"{func_name} {lock_name} failed: {str(e)}, retrying ({retry + 1}/{max_retries})")
                        time.sleep(current_delay)
                    else:
                        logging.error(f"{func_name} {lock_name} failed after all attempts: {str(e)}")

            if last_exception:
                raise last_exception
            return False

        return wrapper

    return decorator


class PostgresDatabaseLock:
    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        self.lock_id = int(hashlib.md5(lock_name.encode()).hexdigest(), 16) % (2**31 - 1)
        self.timeout = int(timeout)
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        cursor = self.db.execute_sql("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire postgres lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT pg_advisory_unlock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"postgres lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"postgres lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledPostgresqlDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class GaussDBDatabaseLock(PostgresDatabaseLock):
    # 适配点：GaussDB 支持 pg_try_advisory_lock/pg_advisory_unlock，因此当前
    # 锁 SQL 与 PostgreSQL 相同；但这里保留独立类，避免 DB_TYPE=gaussdb
    # 在初始化建表、迁移互斥时落入 PostgreSQL enum。若后续 GaussDB 锁函数、
    # timeout 策略或错误处理存在差异，只需要覆盖这个类。
    database_label = "gaussdb"
    poll_interval = 0.1

    def __init__(self, lock_name, timeout=10, db=None):
        super().__init__(lock_name, timeout=timeout, db=db)
        self.timeout = max(float(timeout), 0.0)

    def lock(self):
        deadline = time.monotonic() + self.timeout
        while True:
            cursor = self.db.execute_sql("SELECT pg_try_advisory_lock(%s)", (self.lock_id,))
            row = cursor.fetchone()
            value = row[0] if row else None
            if value in (1, True):
                return True
            if value not in (0, False):
                raise Exception(f"failed to acquire lock {self.lock_name}")

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Exception(f"acquire {self.database_label} lock {self.lock_name} timeout")
            time.sleep(min(self.poll_interval, remaining))

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT pg_advisory_unlock(%s)", (self.lock_id,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"gaussdb lock {self.lock_name} was not established by this thread")
        if ret[0] == 1:
            return True
        raise Exception(f"gaussdb lock {self.lock_name} does not exist")


class MysqlDatabaseLock:
    def __init__(self, lock_name, timeout=10, db=None):
        self.lock_name = lock_name
        self.timeout = int(timeout)
        self.db = db if db else DB

    @with_retry(max_retries=3, retry_delay=1.0)
    def lock(self):
        # SQL parameters only support %s format placeholders
        cursor = self.db.execute_sql("SELECT GET_LOCK(%s, %s)", (self.lock_name, self.timeout))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"acquire mysql lock {self.lock_name} timeout")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"failed to acquire lock {self.lock_name}")

    @with_retry(max_retries=3, retry_delay=1.0)
    def unlock(self):
        cursor = self.db.execute_sql("SELECT RELEASE_LOCK(%s)", (self.lock_name,))
        ret = cursor.fetchone()
        if ret[0] == 0:
            raise Exception(f"mysql lock {self.lock_name} was not established by this thread")
        elif ret[0] == 1:
            return True
        else:
            raise Exception(f"mysql lock {self.lock_name} does not exist")

    def __enter__(self):
        if isinstance(self.db, PooledMySQLDatabase):
            self.lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if isinstance(self.db, PooledMySQLDatabase):
            self.unlock()

    def __call__(self, func):
        @wraps(func)
        def magic(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return magic


class DatabaseLock(Enum):
    MYSQL = MysqlDatabaseLock
    OCEANBASE = MysqlDatabaseLock
    POSTGRES = PostgresDatabaseLock
    # 适配点：初始化建表和迁移需要跨进程互斥。GaussDB 使用
    # GaussDB-compatible advisory lock，不能走 MySQL 的 GET_LOCK/RELEASE_LOCK，
    # 也不再复用 PostgreSQL enum 值。
    GAUSSDB = GaussDBDatabaseLock


DB = BaseDataBase().database_connection
DB.lock = DatabaseLock[settings.DATABASE_TYPE.upper()].value


def close_connection():
    try:
        if DB:
            if settings.DATABASE_TYPE.upper() in GAUSSDB_COMPATIBLE_DATABASE_TYPES:
                if not DB.is_closed():
                    # Return this worker thread's connection to the GaussDB pool.
                    DB.close()
            else:
                DB.close_stale(age=30)
    except Exception as e:
        logging.exception(e)


class DataBaseModel(BaseModel):
    class Meta:
        database = DB


@DB.connection_context()
@DB.lock("init_database_tables", 60)
def init_database_tables(alter_fields=[]):
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    table_objs = []
    create_failed_list = []
    for name, obj in members:
        if obj != DataBaseModel and issubclass(obj, DataBaseModel):
            table_objs.append(obj)

            if not obj.table_exists():
                logging.debug(f"start create table {obj.__name__}")
                try:
                    obj.create_table(safe=True)
                    logging.debug(f"create table success: {obj.__name__}")
                except Exception as e:
                    logging.exception(e)
                    create_failed_list.append(obj.__name__)
            else:
                logging.debug(f"table {obj.__name__} already exists, skip creation.")

    if create_failed_list:
        logging.error(f"create tables failed: {create_failed_list}")
        raise Exception(f"create tables failed: {create_failed_list}")
    migrate_db()


def fill_db_model_object(model_object, human_model_dict):
    for k, v in human_model_dict.items():
        attr_name = "%s" % k
        if hasattr(model_object.__class__, attr_name):
            setattr(model_object, attr_name, v)
    return model_object


class User(DataBaseModel, AuthUser):
    SENSITIVE_FIELDS = {"password", "access_token", "email"}

    id = CharField(max_length=32, primary_key=True)
    access_token = CharField(max_length=255, null=True, index=True)
    # GaussDB O-compatible 会把应用层 "" 存成 NULL；Admin 创建用户保留
    # 历史默认 nickname=""，由字段层把存储层 NULL 隔离回应用层 ""。
    nickname = GaussDBEmptyStringCharField(max_length=100, null=False, help_text="nicky name", index=True)
    password = CharField(max_length=255, null=True, help_text="password", index=True)
    email = CharField(max_length=255, null=False, help_text="email", unique=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    language = CharField(max_length=32, null=True, help_text="English|Chinese", default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", index=True)
    color_schema = CharField(max_length=32, null=True, help_text="Bright|Dark", default="Bright", index=True)
    timezone = CharField(max_length=64, null=True, help_text="Timezone", default="UTC+8\tAsia/Shanghai", index=True)
    last_login_time = DateTimeField(null=True, index=True)
    is_authenticated = CharField(max_length=1, null=False, default="1", index=True)
    is_active = CharField(max_length=1, null=False, default="1", index=True)
    is_anonymous = CharField(max_length=1, null=False, default="0", index=True)
    login_channel = CharField(null=True, help_text="from which user login", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)
    is_superuser = BooleanField(null=True, help_text="is root", default=False, index=True)

    def __str__(self):
        return self.email

    def get_id(self):
        jwt = Serializer(secret_key=settings.get_secret_key())
        return jwt.dumps(str(self.access_token))

    def to_safe_dict(self, *, for_self: bool = False):
        """Return a dict with sensitive fields stripped for API responses.

        Email is treated as sensitive in generic serialization. Pass for_self=True
        when returning the authenticated user's own record (login, profile, etc.).
        """
        result = {k: v for k, v in self.to_dict().items() if k not in self.SENSITIVE_FIELDS}
        if for_self:
            result["email"] = self.email
        logging.debug("User %s serialized safely, filtered fields: %s", self.id, self.SENSITIVE_FIELDS)
        return result

    class Meta:
        db_table = "user"


class Tenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=100, null=True, help_text="Tenant name", index=True)
    public_key = CharField(max_length=255, null=True, index=True)
    # GaussDB 适配点：这些默认模型 ID 历史上允许用 "" 表示未配置。
    # 在 GaussDB 下由专用 Field 把存储层 NULL 隔离成应用层 ""。
    llm_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default llm ID", index=True)
    tenant_llm_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    embd_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    asr_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default ASR model ID", index=True)
    tenant_asr_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    img2txt_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default image to text model ID", index=True)
    tenant_img2txt_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    rerank_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default rerank model ID", index=True)
    tenant_rerank_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    tts_id = CharField(max_length=256, null=True, help_text="default tts model ID", index=True)
    tenant_tts_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    ocr_id = CharField(max_length=256, null=True, help_text="default OCR model ID", index=True)
    tenant_ocr_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    parser_ids = CharField(max_length=256, null=False, help_text="document processors", index=True)
    credit = IntegerField(default=512, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "tenant"


class UserTenant(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_id = CharField(max_length=32, null=False, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    role = CharField(max_length=32, null=False, help_text="UserTenantRole", index=True)
    invited_by = CharField(max_length=32, null=False, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "user_tenant"


class InvitationCode(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    code = CharField(max_length=32, null=False, index=True)
    visit_time = DateTimeField(null=True, index=True)
    user_id = CharField(max_length=32, null=True, index=True)
    tenant_id = CharField(max_length=32, null=True, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "invitation_code"


class LLMFactories(DataBaseModel):
    name = CharField(max_length=128, null=False, help_text="LLM factory name", primary_key=True)
    logo = TextField(null=True, help_text="llm logo base64")
    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    rank = IntegerField(default=0, index=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "llm_factories"


class LLM(DataBaseModel):
    # LLMs dictionary
    llm_name = CharField(max_length=128, null=False, help_text="LLM name", index=True)
    model_type = CharField(max_length=128, null=False, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    fid = CharField(max_length=128, null=False, help_text="LLM factory id", index=True)
    max_tokens = IntegerField(default=0)

    tags = CharField(max_length=255, null=False, help_text="LLM, Text Embedding, Image2Text, Chat, 32k...", index=True)
    is_tools = BooleanField(null=False, help_text="support tools", default=False)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        primary_key = CompositeKey("fid", "llm_name")
        db_table = "llm"


class TenantLLM(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, index=True)
    llm_factory = CharField(max_length=128, null=False, help_text="LLM factory name", index=True)
    model_type = CharField(max_length=128, null=True, help_text="LLM, Text Embedding, Image2Text, ASR", index=True)
    llm_name = CharField(max_length=128, null=True, help_text="LLM name", default="", index=True)
    api_key = TextField(null=True, help_text="API KEY")
    api_base = CharField(max_length=255, null=True, help_text="API Base")
    max_tokens = IntegerField(default=8192, help_text="Max context token num", index=True)
    used_tokens = IntegerField(default=0, help_text="Used token num", index=True)
    status = CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.llm_name

    class Meta:
        db_table = "tenant_llm"
        indexes = ((("tenant_id", "llm_factory", "llm_name"), True),)


class TenantLangfuse(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, primary_key=True)
    secret_key = CharField(max_length=2048, null=False, help_text="SECRET KEY")
    public_key = CharField(max_length=2048, null=False, help_text="PUBLIC KEY")
    host = CharField(max_length=128, null=False, help_text="HOST")

    def __str__(self):
        return "Langfuse host" + self.host

    class Meta:
        db_table = "tenant_langfuse"


class Knowledgebase(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="KB name", index=True)
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    description = TextField(null=True, help_text="KB description")
    # GaussDB 适配点：dataset 创建时可能继承到应用层 ""，存储层在
    # GaussDB 下允许 NULL，读取时再还原为 ""。
    embd_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default embedding model ID", index=True)
    tenant_embd_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    created_by = CharField(max_length=32, null=False, index=True)
    doc_num = IntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    similarity_threshold = FloatField(default=0.2, index=True)
    vector_similarity_weight = FloatField(default=0.3, index=True)

    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", default=ParserType.NAIVE.value, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    pagerank = IntegerField(default=0, index=False)

    graphrag_task_id = CharField(max_length=32, null=True, help_text="Graph RAG task ID", index=True)
    graphrag_task_finish_at = DateTimeField(null=True)
    raptor_task_id = CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True)
    raptor_task_finish_at = DateTimeField(null=True)
    mindmap_task_id = CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True)
    mindmap_task_finish_at = DateTimeField(null=True)
    artifact_task_id = CharField(max_length=32, null=True, help_text="Artifact compilation task ID", index=True)
    artifact_task_finish_at = DateTimeField(null=True)
    skill_task_id = CharField(max_length=32, null=True, help_text="Skill generation task ID", index=True)
    skill_task_finish_at = DateTimeField(null=True)

    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "knowledgebase"


class Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    thumbnail = TextField(null=True, help_text="thumbnail base64 string")
    kb_id = CharField(max_length=256, null=False, index=True)
    parser_id = CharField(max_length=32, null=False, help_text="default parser ID", index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="pipeline ID", index=True)
    parser_config = JSONField(null=False, default={"pages": [[1, 1000000]], "table_context_size": 0, "image_context_size": 0})
    source_type = CharField(max_length=128, null=False, default="local", help_text="where dose this document come from", index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=True, help_text="file name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    token_num = IntegerField(default=0, index=True)
    chunk_num = IntegerField(default=0, index=True)
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    suffix = CharField(max_length=32, null=False, help_text="The real file extension suffix", index=True)

    content_hash = CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True)

    run = CharField(max_length=1, null=True, help_text="start to run processing or cancel.(1: run it; 2: cancel)", default="0", index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "document"


class File(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    parent_id = CharField(max_length=32, null=False, help_text="parent folder id", index=True)
    tenant_id = CharField(max_length=32, null=False, help_text="tenant id", index=True)
    created_by = CharField(max_length=32, null=False, help_text="who created it", index=True)
    name = CharField(max_length=255, null=False, help_text="file name or folder name", index=True)
    location = CharField(max_length=255, null=True, help_text="where dose it store", index=True)
    size = BigIntegerField(default=0, index=True)
    type = CharField(max_length=32, null=False, help_text="file extension", index=True)
    # GaussDB 适配点：File.source_type 的历史默认值是 ""。仅 GaussDB
    # 存储层允许 NULL；MySQL/PostgreSQL 继续按原模型生成 NOT NULL。
    source_type = GaussDBEmptyStringCharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True)

    class Meta:
        db_table = "file"


class File2Document(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    file_id = CharField(max_length=32, null=True, help_text="file id", index=True)
    document_id = CharField(max_length=32, null=True, help_text="document id", index=True)

    class Meta:
        db_table = "file2document"


class FileCommit(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    folder_id = CharField(max_length=32, null=False, help_text="workspace folder id", index=True)
    parent_id = CharField(max_length=32, null=True, help_text="parent commit id", index=True)
    message = CharField(max_length=512, default="", help_text="commit message")
    author_id = CharField(max_length=32, null=False, help_text="user who created the commit", index=True)
    file_count = IntegerField(default=0, help_text="number of files in this commit")
    tree_state = LongTextField(null=True, help_text="JSON snapshot of the full folder tree at this commit")
    # ---- Artifact-commit extension ----
    # Populated only for commits recorded via
    # ``FileCommitService.record_page_edit`` (i.e. artifact-page saves).
    # For workspace file commits both fields stay null and the ``message``
    # column carries the commit body.
    title = CharField(max_length=255, null=True, help_text="commit title (artifact-page edits)")
    comments = TextField(null=True, help_text="commit body/description (artifact-page edits)")

    class Meta:
        db_table = "file_commit"


class FileCommitItem(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    commit_id = CharField(max_length=32, null=False, help_text="commit id", index=True)
    file_id = CharField(max_length=32, null=False, help_text="file id", index=True)
    operation = CharField(max_length=16, null=False, help_text="add / modify / delete / rename", index=True)
    old_hash = CharField(max_length=64, null=True, help_text="old content hash", index=True)
    new_hash = CharField(max_length=64, null=True, help_text="new content hash", index=True)
    old_location = CharField(max_length=255, null=True, help_text="old storage location")
    new_location = CharField(max_length=255, null=True, help_text="new storage location")
    old_name = CharField(max_length=255, null=True, help_text="old file name (for rename)")
    new_name = CharField(max_length=255, null=True, help_text="new file name (for rename)")
    # ---- Artifact-commit extension ----
    # Populated only for artifact-page saves recorded via
    # ``FileCommitService.record_page_edit``.
    diff = LongTextField(null=True, help_text="pre-computed unified diff (artifact-page edits)")
    content_after_storage = CharField(max_length=16, null=True, help_text="'minio' | 'es' — where the post-save blob lives", index=True)
    content_after_location = CharField(max_length=512, null=True, help_text="storage key/id for the post-save blob")
    slug_kwd = CharField(max_length=512, null=True, help_text="artifact page slug (<page_type>/<name>)", index=True)
    page_type_kwd = CharField(max_length=32, null=True, help_text="artifact page type", index=True)

    class Meta:
        db_table = "file_commit_item"
        indexes = (
            (("commit_id", "file_id"), True),  # unique composite index
        )


# ``ArtifactCommit`` retired — artifact page history is now stored under
# ``FileCommit`` + ``FileCommitItem`` via ``FileCommitService.record_page_edit``
# (see the artifact-commit extension columns on those models above).
# Pre-existing ``artifact_commit`` rows are intentionally left in place;
# no code path reads them.


class Task(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    doc_id = CharField(max_length=32, null=False, index=True)
    from_page = IntegerField(default=0)
    to_page = IntegerField(default=MAXIMUM_TASK_PAGE_NUMBER)
    # GaussDB 适配点：普通文档解析任务历史上使用 task_type="" 表示“非
    # dataflow/graph/memory 等特殊任务”。GaussDB O-compatible 会把绑定参数
    # "" 存成 NULL，如果该列保持 NOT NULL，真实 /documents/parse API 会在
    # 插入 task 时失败。因此只在 DB_TYPE=gaussdb 下允许存储层 NULL，并在
    # ORM 读回时还原为应用层 ""。
    task_type = GaussDBEmptyStringCharField(max_length=32, null=False, default="")
    priority = IntegerField(default=0)

    begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)

    progress = FloatField(default=0, index=True)
    # GaussDB 适配点：progress_msg/chunk_ids 本来就是可空列，但代码中大量按
    # 字符串处理（拼接、strip、split）。字段层把 GaussDB 存储层 NULL 还原为
    # ""，避免调用方到处增加 `is None` 判断，也不影响 MySQL/PostgreSQL。
    progress_msg = GaussDBEmptyStringTextField(null=True, help_text="process message", default="")
    retry_count = IntegerField(default=0)
    digest = TextField(null=True, help_text="task digest", default="")
    chunk_ids = GaussDBEmptyStringLongTextField(null=True, help_text="chunk ids", default="")


class Dialog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="dialog application name", index=True)
    description = TextField(null=True, help_text="Dialog description")
    icon = TextField(null=True, help_text="icon base64 string")
    language = CharField(max_length=32, null=True, default="Chinese" if "zh_CN" in os.getenv("LANG", "") else "English", help_text="English|Chinese", index=True)
    # GaussDB 适配点：chat/reranker 模型 ID 的应用层 "" 由专用 Field
    # 映射成存储层 NULL，避免业务代码分散处理 is None。
    llm_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default llm ID")
    tenant_llm_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)

    llm_setting = JSONField(null=False, default={"temperature": 0.1, "top_p": 0.3, "frequency_penalty": 0.7, "presence_penalty": 0.4, "max_tokens": 512})
    prompt_type = CharField(max_length=16, null=False, default="simple", help_text="simple|advanced", index=True)
    prompt_config = JSONField(
        null=False,
        default={"system": "", "prologue": "Hi! I'm your assistant. What can I do for you?", "parameters": [], "empty_response": "Sorry! No relevant content was found in the knowledge base!"},
    )
    meta_data_filter = JSONField(null=True, default={})

    similarity_threshold = FloatField(default=0.2)
    vector_similarity_weight = FloatField(default=0.3)

    top_n = IntegerField(default=6)

    top_k = IntegerField(default=1024)

    do_refer = CharField(max_length=1, null=False, default="1", help_text="it needs to insert reference index into answer or not")

    rerank_id = GaussDBEmptyStringCharField(max_length=128, null=False, help_text="default rerank model ID")
    tenant_rerank_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    kb_ids = JSONField(null=False, default=[])
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "dialog"


class Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    dialog_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    user_id = CharField(max_length=255, null=True, help_text="user_id", index=True)

    class Meta:
        db_table = "conversation"


class APIToken(DataBaseModel):
    tenant_id = CharField(max_length=32, null=False, index=True)
    token = CharField(max_length=255, null=False, index=True)
    dialog_id = CharField(max_length=32, null=True, index=True)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    beta = CharField(max_length=255, null=True, index=True)

    class Meta:
        db_table = "api_token"
        primary_key = CompositeKey("tenant_id", "token")


class API4Conversation(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=True, help_text="conversation name", index=False)
    dialog_id = CharField(max_length=32, null=False, index=True)
    user_id = GaussDBEmptyStringCharField(max_length=255, null=False, help_text="user_id", index=True)
    exp_user_id = CharField(max_length=255, null=True, help_text="exp_user_id", index=True)
    message = JSONField(null=True)
    reference = JSONField(null=True, default=[])
    tokens = IntegerField(default=0)
    source = CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True)
    dsl = JSONField(null=True, default={})
    duration = FloatField(default=0, index=True)
    round = IntegerField(default=0, index=True)
    thumb_up = IntegerField(default=0, index=True)
    errors = TextField(null=True, help_text="errors")
    version_title = CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False)

    class Meta:
        db_table = "api_4_conversation"


class UserCanvas(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    user_id = CharField(max_length=255, null=False, help_text="user_id", index=True)
    title = CharField(max_length=255, null=True, help_text="Canvas title")

    permission = CharField(max_length=16, null=False, help_text="me|team", default="me", index=True)
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    description = TextField(null=True, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    tags = GaussDBEmptyStringCharField(
        max_length=512,
        null=False,
        default="",
        help_text="Comma-separated tags for organizing agents",
        index=True,
    )
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "user_canvas"


class CanvasTemplate(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    title = JSONField(null=True, default=dict, help_text="Canvas title")
    description = JSONField(null=True, default=dict, help_text="Canvas description")
    canvas_type = CharField(max_length=32, null=True, help_text="Canvas type", index=True)
    canvas_types = ListField(null=True, default=list, help_text="Canvas types")
    canvas_category = CharField(max_length=32, null=False, default="agent_canvas", help_text="Canvas category: agent_canvas|dataflow_canvas", index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "canvas_template"


class UserCanvasVersion(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    user_canvas_id = CharField(max_length=255, null=False, help_text="user_canvas_id", index=True)

    title = CharField(max_length=255, null=True, help_text="Canvas title")
    description = TextField(null=True, help_text="Canvas description")
    release = BooleanField(null=False, help_text="is released", default=False, index=True)
    dsl = JSONField(null=True, default={})

    class Meta:
        db_table = "user_canvas_version"


class MCPServer(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=255, null=False, help_text="MCP Server name")
    tenant_id = CharField(max_length=32, null=False, index=True)
    url = CharField(max_length=2048, null=False, help_text="MCP Server URL")
    server_type = CharField(max_length=32, null=False, help_text="MCP Server type")
    description = TextField(null=True, help_text="MCP Server description")
    variables = JSONField(null=True, default=dict, help_text="MCP Server variables")
    headers = JSONField(null=True, default=dict, help_text="MCP Server additional request headers")

    class Meta:
        db_table = "mcp_server"


class CompilationTemplate(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=True, index=True)
    group_id = CharField(max_length=32, null=True, index=True)
    name = CharField(max_length=128, null=False, index=True)
    description = TextField(null=True, default="")
    kind = CharField(max_length=64, null=False, index=True)
    config = JSONField(null=False, default={})
    is_builtin = BooleanField(null=False, default=False, index=True)
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "compilation_template"
        indexes = ((("tenant_id", "group_id", "name", "is_builtin", "status"), True),)


class CompilationTemplateGroup(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, index=True)
    description = TextField(null=True, default="")
    scope = CharField(max_length=16, null=False, index=True, help_text="file | dataset")
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "compilation_template_group"
        indexes = ((("tenant_id", "name", "status"), True),)


class Search(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=True)
    description = TextField(null=True, help_text="KB description")
    created_by = CharField(max_length=32, null=False, index=True)
    search_config = JSONField(
        null=False,
        default={
            "kb_ids": [],
            "doc_ids": [],
            "similarity_threshold": 0.2,
            "vector_similarity_weight": 0.3,
            "use_kg": False,
            # rerank settings
            "rerank_id": "",
            "top_k": 1024,
            # chat settings
            "summary": False,
            "chat_id": "",
            # Leave it here for reference, don't need to set default values
            "llm_setting": {
                # "temperature": 0.1,
                # "top_p": 0.3,
                # "frequency_penalty": 0.7,
                # "presence_penalty": 0.4,
            },
            "chat_settingcross_languages": [],
            "highlight": False,
            "keyword": False,
            "web_search": False,
            "related_search": False,
            "query_mindmap": False,
        },
    )
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "search"


class PipelineOperationLog(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    document_id = CharField(max_length=32, index=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    pipeline_id = CharField(max_length=32, null=True, help_text="Pipeline ID", index=True)
    pipeline_title = CharField(max_length=32, null=True, help_text="Pipeline title", index=True)
    parser_id = CharField(max_length=32, null=False, help_text="Parser ID", index=True)
    document_name = CharField(max_length=255, null=False, help_text="File name")
    document_suffix = CharField(max_length=255, null=False, help_text="File suffix")
    document_type = CharField(max_length=255, null=False, help_text="Document type")
    source_from = CharField(max_length=255, null=False, help_text="Source")
    progress = FloatField(default=0, index=True)
    progress_msg = TextField(null=True, help_text="process message", default="")
    process_begin_at = DateTimeField(null=True, index=True)
    process_duration = FloatField(default=0)
    dsl = JSONField(null=True, default=dict)
    task_type = CharField(max_length=32, null=False, default="")
    operation_status = CharField(max_length=32, null=False, help_text="Operation status")
    avatar = TextField(null=True, help_text="avatar base64 string")
    status = CharField(max_length=1, null=True, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True)

    class Meta:
        db_table = "pipeline_operation_log"


class Connector(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Search name", index=False)
    source = CharField(max_length=128, null=False, help_text="Data source", index=True)
    input_type = CharField(max_length=128, null=False, help_text="poll/event/..", index=True)
    config = JSONField(null=False, default={})
    refresh_freq = IntegerField(default=0, index=False)
    prune_freq = IntegerField(default=0, index=False)
    timeout_secs = IntegerField(default=3600, index=False)
    indexing_start = DateTimeField(null=True, index=True)
    status = CharField(max_length=16, null=True, help_text="schedule", default="schedule", index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "connector"


class Connector2Kb(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    connector_id = CharField(max_length=32, null=False, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)
    auto_parse = CharField(max_length=1, null=False, default="1", index=False)

    class Meta:
        db_table = "connector2kb"


class ChatChannel(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    tenant_id = CharField(max_length=32, null=False, index=True)
    name = CharField(max_length=128, null=False, help_text="Bot name", index=False)
    channel = CharField(max_length=128, null=False, help_text="Chat channel type", index=True)
    config = JSONField(null=False, default={}, help_text="Channel credential & settings")
    chat_id = CharField(max_length=32, null=True, default=None, help_text="connected chat id", index=True)
    status = IntegerField(default=1, index=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = "chat_channel"


class DateTimeTzField(CharField):
    field_type = "VARCHAR"

    def db_value(self, value: datetime | None) -> str | None:
        if value is not None:
            if value.tzinfo is not None:
                return value.isoformat()
            else:
                return value.replace(tzinfo=timezone.utc).isoformat()
        return value

    def python_value(self, value: str | None) -> datetime | None:
        if value is not None:
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                import pytz

                return dt.replace(tzinfo=pytz.UTC)
            return dt
        return value


class SyncLogs(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    connector_id = CharField(max_length=32, index=True)
    task_type = CharField(max_length=32, null=False, default="sync", index=True)
    status = CharField(max_length=128, null=False, help_text="Processing status", index=True)
    from_beginning = CharField(max_length=1, null=True, help_text="", default="0", index=False)
    new_docs_indexed = IntegerField(default=0, index=False)
    total_docs_indexed = IntegerField(default=0, index=False)
    docs_removed_from_index = IntegerField(default=0, index=False)
    # GaussDB 适配点：Connector 调度日志使用 error_msg="" 表示“当前没有错误”。
    # 真实 GaussDB O-compatible 会把该默认空字符串按 NULL 写入，如果字段仍是
    # NOT NULL，PUT /datasets/<id> 绑定 connector 并调度 sync_logs 时会失败。
    # 字段层在 GaussDB 下允许存储 NULL，并在 ORM 读回时还原成应用层 ""。
    error_msg = GaussDBEmptyStringTextField(null=False, help_text="process message", default="")
    error_count = IntegerField(default=0, index=False)
    # full_exception_trace 虽然本来可空，但代码会把它当字符串追加异常栈；
    # GaussDB 下同样需要把存储层 NULL 隔离成应用层 ""。
    full_exception_trace = GaussDBEmptyStringTextField(null=True, help_text="process message", default="")
    time_started = DateTimeField(null=True, index=True)
    poll_range_start = DateTimeTzField(max_length=255, null=True, index=True)
    poll_range_end = DateTimeTzField(max_length=255, null=True, index=True)
    kb_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "sync_logs"


class Memory(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    name = CharField(max_length=128, null=False, index=False, help_text="Memory name")
    avatar = TextField(null=True, help_text="avatar base64 string")
    tenant_id = CharField(max_length=32, null=False, index=True)
    memory_type = IntegerField(null=False, default=1, index=True, help_text="Bit flags (LSB->MSB): 1=raw, 2=semantic, 4=episodic, 8=procedural. E.g., 5 enables raw + episodic.")
    storage_type = CharField(max_length=32, default="table", null=False, index=True, help_text="table|graph")
    # GaussDB 适配点：Memory Store 仍聚焦元数据库适配；模型 ID 的空值
    # 兼容在 Field 层完成，不把 NULL 语义泄漏给 Memory Store 调用方。
    embd_id = GaussDBEmptyStringCharField(max_length=128, null=False, index=False, help_text="embedding model ID")
    tenant_embd_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    llm_id = GaussDBEmptyStringCharField(max_length=128, null=False, index=False, help_text="chat model ID")
    tenant_llm_id = CharField(max_length=32, null=True, help_text="id in tenant_model", index=True)
    permissions = CharField(max_length=16, null=False, index=True, help_text="me|team", default="me")
    description = TextField(null=True, help_text="description")
    memory_size = IntegerField(default=5242880, null=False, index=False)
    forgetting_policy = CharField(max_length=32, null=False, default="FIFO", index=False, help_text="LRU|FIFO")
    temperature = FloatField(default=0.5, index=False)
    system_prompt = TextField(null=True, help_text="system prompt", index=False)
    user_prompt = TextField(null=True, help_text="user prompt", index=False)

    class Meta:
        db_table = "memory"


class SystemSettings(DataBaseModel):
    name = CharField(max_length=128, primary_key=True)
    source = CharField(max_length=32, null=False, index=False)
    data_type = CharField(max_length=32, null=False, index=False)
    # GaussDB 适配点：system_settings.json 中存在空配置值，GaussDB 下存为
    # NULL、应用层读回 ""，避免全局 SQL 结果转换影响其它真正可空字段。
    value = GaussDBEmptyStringTextField(null=False, help_text="Configuration value (JSON, string, etc.)")

    class Meta:
        db_table = "system_settings"


class TenantModelProvider(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    provider_name = CharField(max_length=128, null=False, index=False, help_text="LLM provider name")
    tenant_id = CharField(max_length=32, null=False, index=True)

    class Meta:
        db_table = "tenant_model_provider"
        indexes = ((("tenant_id", "provider_name"), True),)


class TenantModelInstance(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    instance_name = CharField(max_length=128, null=False, index=False, help_text="Model instance name")
    provider_id = CharField(max_length=32, null=False, index=False)
    api_key = CharField(max_length=512, null=False, index=False, help_text="API key")
    status = CharField(max_length=32, default="active", index=False)
    extra = CharField(max_length=512, default="{}", index=False)

    class Meta:
        db_table = "tenant_model_instance"


class TenantModel(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    model_name = CharField(max_length=128, null=True, index=False, help_text="Model name")
    provider_id = CharField(max_length=32, null=False, index=False)
    instance_id = CharField(max_length=32, null=False, index=True)
    model_type = IntegerField(null=False, default=1, index=True, help_text="Bit flags (LSB->MSB): 1=chat, 2=embedding, 4=asr, 8=vision, 16=rerank, 32=tts, 64=ocr")
    status = CharField(max_length=32, default="active", index=False)
    extra = CharField(max_length=1024, default="{}", index=False)

    class Meta:
        db_table = "tenant_model"


class TenantModelGroup(DataBaseModel):
    id = CharField(max_length=32, primary_key=True)
    group_type = CharField(max_length=32, null=False, index=False, help_text="Group type")
    model_name = CharField(max_length=128, null=True, index=False, help_text="Model name")
    strategy = CharField(max_length=32, default="weighted", index=False, help_text="Routing strategy")

    class Meta:
        db_table = "tenant_model_group"


class TenantModelGroupMapping(DataBaseModel):
    group_id = CharField(max_length=32, null=False, index=True, help_text="Group ID")
    provider_id = CharField(max_length=32, null=False, index=False)
    instance_id = CharField(max_length=32, null=False, index=False)
    model_id = CharField(max_length=32, null=False, index=True)
    weight = IntegerField(default=100, index=False, help_text="Routing weight")
    status = CharField(max_length=32, default="active", index=False)

    class Meta:
        db_table = "tenant_model_group_mapping"
        primary_key = CompositeKey("group_id", "provider_id", "instance_id", "model_id")


GAUSSDB_EMPTY_STRING_COMPATIBLE_COLUMNS = (
    ("user", ("nickname",)),
    ("tenant", ("llm_id", "embd_id", "asr_id", "img2txt_id", "rerank_id")),
    ("knowledgebase", ("embd_id",)),
    ("dialog", ("llm_id", "rerank_id")),
    ("memory", ("embd_id", "llm_id")),
    ("file", ("source_type",)),
    ("system_settings", ("value",)),
    ("task", ("task_type",)),
    ("sync_logs", ("error_msg", "full_exception_trace")),
    ("api_4_conversation", ("user_id",)),
    ("user_canvas", ("tags",)),
)


def _quote_identifier_for_gaussdb(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def relax_gaussdb_empty_string_compatible_columns():
    if not is_gaussdb_compatible_database():
        return

    # GaussDB 适配点：这些字段在应用层仍然是“空字符串可用”的历史语义；
    # 但 GaussDB A-compatible 会把 "" 存成 NULL，所以旧库升级时也必须把
    # 对应列改成 nullable。这里不影响 MySQL/PostgreSQL，也不放宽未列入清单
    # 的字段，避免扩大约束变化范围。
    for table_name, column_names in GAUSSDB_EMPTY_STRING_COMPATIBLE_COLUMNS:
        quoted_table = _quote_identifier_for_gaussdb(table_name)
        for column_name in column_names:
            quoted_column = _quote_identifier_for_gaussdb(column_name)
            try:
                DB.execute_sql(f"ALTER TABLE {quoted_table} ALTER COLUMN {quoted_column} DROP NOT NULL")
            except Exception as ex:
                # 迁移体系里加列/改类型失败通常只记录 critical 后继续启动；这里保持
                # 同样风格，避免某个历史库缺列时直接中断，同时让审查者能在日志里看到
                # 哪个 GaussDB 空字符串兼容列没有完成约束调整。
                logging.critical(
                    "Failed to relax GaussDB empty-string compatible column %s.%s: %s",
                    table_name,
                    column_name,
                    ex,
                )


def alter_db_add_column(migrator, table_name, column_name, column_type):
    if is_gaussdb_compatible_database():
        try:
            migrate(migrator.add_column(table_name, column_name, column_type))
        except (OperationalError, ProgrammingError) as ex:
            if not is_duplicate_column_error(ex):
                logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, operation error: {ex}")
        except Exception as ex:
            logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, error: {ex}")
        return

    try:
        migrate(migrator.add_column(table_name, column_name, column_type))
    except OperationalError as ex:
        error_codes = [1060]
        error_messages = ["Duplicate column name"]

        should_skip_error = (hasattr(ex, "args") and ex.args and ex.args[0] in error_codes) or (str(ex) in error_messages)

        if not should_skip_error:
            logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, operation error: {ex}")

    except Exception as ex:
        logging.critical(f"Failed to add {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name}, error: {ex}")
        pass


def alter_db_column_type(migrator, table_name, column_name, new_column_type):
    try:
        migrate(migrator.alter_column_type(table_name, column_name, new_column_type))
    except Exception as ex:
        logging.critical(f"Failed to alter {settings.DATABASE_TYPE.upper()}.{table_name} column {column_name} type, error: {ex}")
        pass


def alter_db_rename_column(migrator, table_name, old_column_name, new_column_name):
    try:
        migrate(migrator.rename_column(table_name, old_column_name, new_column_name))
    except Exception:
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass


def alter_db_drop_index(migrator, table_name, index_name):
    try:
        migrate(migrator.drop_index(table_name, index_name))
    except Exception:
        # rename fail will lead to a weired error.
        # logging.critical(f"Failed to rename {settings.DATABASE_TYPE.upper()}.{table_name} column {old_column_name} to {new_column_name}, error: {ex}")
        pass


def ensure_model_indexes(migrator):
    """Create indexes declared by the Peewee models when they are missing."""
    members = inspect.getmembers(sys.modules[__name__], inspect.isclass)
    for name, model in members:
        if model == DataBaseModel or not issubclass(model, DataBaseModel):
            continue

        table_name = model._meta.table_name
        expected = {}
        for field in model._meta.fields.values():
            if field.primary_key:
                continue
            if field.index or field.unique:
                expected[(field.name,)] = bool(field.unique)

        for columns, unique in model._meta.indexes:
            expected[tuple(columns)] = bool(unique)

        if not expected:
            continue

        try:
            existing = {tuple(index.columns): bool(index.unique) for index in DB.get_indexes(table_name)}
        except Exception as ex:
            logging.error(f"Failed to inspect indexes on {table_name}: {ex}")
            continue

        for columns, unique in expected.items():
            if columns in existing and (not unique or existing[columns]):
                continue
            try:
                migrate(migrator.add_index(table_name, columns, unique=unique))
                logging.info(f"Created {'unique ' if unique else ''}index on {table_name} ({', '.join(columns)})")
            except Exception as ex:
                logging.error(f"Failed to create {'unique ' if unique else ''}index on {table_name} ({', '.join(columns)}): {ex}")


def _gaussdb_user_email_unique_index_exists() -> bool:
    cursor = DB.execute_sql(
        """
        SELECT COUNT(*)
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = 'user'
          AND lower(indexdef) LIKE %s
          AND (
            lower(indexdef) LIKE %s
            OR lower(indexdef) LIKE %s
          )
    """,
        ("create unique index%", "%(email)%", '%("email")%'),
    )
    result = cursor.fetchone()
    return bool(result and result[0] > 0)


def migrate_add_unique_email(migrator):
    """Deduplicates user emails and add UNIQUE constraint to email column (idempotent)"""
    # step 0: check existing index state on user.email and prepare for unique constraint
    try:
        if is_gaussdb_compatible_database():
            # 适配点：GaussDB 不能使用 MySQL information_schema.statistics
            # 和反引号语法；这里进入 GaussDB-compatible 分支，用 pg_indexes
            # 判断唯一索引状态。入口独立于 PostgreSQL，后续如 GaussDB catalog
            # 查询需要调整，可以只改 _gaussdb_user_email_unique_index_exists。
            if _gaussdb_user_email_unique_index_exists():
                logging.info("UNIQUE index on user.email already exists, skipping migration")
                return
        elif settings.DATABASE_TYPE.upper() == "POSTGRES":
            cursor = DB.execute_sql("""
                SELECT COUNT(*)
                FROM pg_indexes
                WHERE tablename = 'user'
                  AND indexname = 'user_email'
            """)
            result = cursor.fetchone()
            if result and result[0] > 0:
                logging.info("UNIQUE index on user.email already exists, skipping migration")
                return
        else:
            # Fetch the first index on email: tells us both the name and whether it's unique.
            # non_unique=0 means unique, non_unique=1 means non-unique.
            cursor = DB.execute_sql("""
                SELECT index_name, non_unique
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'user'
                  AND column_name = 'email'
                LIMIT 1
            """)
            row = cursor.fetchone()
            if row:
                index_name, non_unique = row
                if non_unique == 0:
                    logging.info("UNIQUE index on user.email already exists, skipping migration")
                    return
                # Non-unique index exists (e.g. from old peewee index=True); drop it so
                # the upcoming ADD UNIQUE INDEX does not hit MySQL error 1061 "Duplicate key name".
                DB.execute_sql(f"ALTER TABLE `user` DROP INDEX `{index_name}`")
                logging.info(f"Dropped non-unique index '{index_name}' on user.email before adding unique index")
    except Exception as ex:
        logging.warning(f"Failed to check/prepare email index on user table: {ex}, continuing with migration")

    # step 1: rename duplicate rows so the UNIQUE constraint can be applied
    try:
        duplicates = User.select(User.email).group_by(User.email).having(fn.COUNT(User.id) > 1).tuples()
        for (dup_email,) in duplicates:
            # Keep the superuser row, or the oldest row if there is no superuser
            rows = list(User.select(User.id).where(User.email == dup_email).order_by(User.is_superuser.desc(), User.create_time.asc()).tuples())
            for (uid,) in rows[1:]:
                new_email = f"{dup_email}_DUPLICATE_{uid[:8]}"
                User.update(email=new_email).where(User.id == uid).execute()
                logging.warning("Renamed duplicate user %s email to %s during migration", uid, new_email)
    except Exception as ex:
        logging.critical("Failed to deduplicate user.email before adding UNIQUE constraint: %s", ex)
        return

    # step 2: add UNIQUE index via migrator
    try:
        migrate(migrator.add_index("user", ("email",), unique=True))
    except (OperationalError, ProgrammingError) as ex:
        msg = str(ex)
        if is_gaussdb_compatible_database():
            already_exists = is_duplicate_object_error(ex)
        else:
            # Preserve the upstream MySQL/PostgreSQL handling.
            already_exists = "1061" in msg or "Duplicate key name" in msg or "already exists" in msg.lower()
        if already_exists:
            pass
        else:
            logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)
    except Exception as ex:
        logging.critical("Failed to add UNIQUE constraint on user.email: %s", ex)


def update_tenant_llm_to_id_primary_key():
    """Add ID and set to primary key step by step."""
    if is_gaussdb_compatible_database():
        # 适配点：GaussDB 的序列、catalog、约束 DDL 走 GaussDB-compatible
        # 实现，不能复用 MySQL 的 AUTO_INCREMENT 和 INFORMATION_SCHEMA 写法，
        # 也不再落入 PostgreSQL 分支名。
        _update_tenant_llm_to_id_primary_key_gaussdb()
    elif settings.DATABASE_TYPE.upper() == "POSTGRES":
        _update_tenant_llm_to_id_primary_key_postgres()
    else:
        _update_tenant_llm_to_id_primary_key_mysql()


def _update_tenant_llm_to_id_primary_key_mysql():
    """MySQL implementation: Add ID column and set as AUTO_INCREMENT primary key."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT COLUMN_NAME
                            FROM INFORMATION_SCHEMA.COLUMNS
                            WHERE TABLE_SCHEMA = DATABASE()
                            AND TABLE_NAME = 'tenant_llm'
                            AND COLUMN_NAME = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INT NULL")

            # 2. Set ID using MySQL user variables
            DB.execute_sql("SET @row = 0;")
            DB.execute_sql("UPDATE tenant_llm SET temp_id = (@row := @row + 1) ORDER BY tenant_id, llm_factory, llm_name;")

            # 3. Drop old primary key
            DB.execute_sql("ALTER TABLE tenant_llm DROP PRIMARY KEY")

            # 4. Update ID column to primary key with AUTO_INCREMENT
            DB.execute_sql("""
            ALTER TABLE tenant_llm
            MODIFY COLUMN temp_id INT NOT NULL AUTO_INCREMENT PRIMARY KEY
            """)

            # 5. Add unique key
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. rename
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key.")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT COLUMN_NAME
                                    FROM INFORMATION_SCHEMA.COLUMNS
                                    WHERE TABLE_SCHEMA = DATABASE()
                                    AND TABLE_NAME = 'tenant_llm'
                                    AND COLUMN_NAME = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def _update_tenant_llm_to_id_primary_key_postgres():
    """PostgreSQL implementation: Add SERIAL primary key column to tenant_llm."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            cursor = DB.execute_sql("""
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_catalog = current_database()
                            AND table_name = 'tenant_llm'
                            AND column_name = 'id'
                        """)
            if cursor.rowcount > 0:
                return

            # 1. Add nullable integer column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INTEGER NULL")

            # 2. Assign sequential row numbers ordered consistently
            DB.execute_sql("""
                UPDATE tenant_llm
                SET temp_id = subq.rn
                FROM (
                    SELECT ctid,
                           ROW_NUMBER() OVER (ORDER BY tenant_id, llm_factory, llm_name) AS rn
                    FROM tenant_llm
                ) AS subq
                WHERE tenant_llm.ctid = subq.ctid
            """)

            # 3. Drop old composite primary key constraint
            cursor = DB.execute_sql("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_catalog = current_database()
                  AND table_name = 'tenant_llm'
                  AND constraint_type = 'PRIMARY KEY'
            """)
            row = cursor.fetchone()
            if row:
                DB.execute_sql(f'ALTER TABLE tenant_llm DROP CONSTRAINT "{row[0]}"')

            # 4. Make temp_id NOT NULL and create a sequence for it
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET NOT NULL")
            DB.execute_sql("CREATE SEQUENCE IF NOT EXISTS tenant_llm_id_seq")
            DB.execute_sql("""
                SELECT setval('tenant_llm_id_seq', COALESCE((SELECT MAX(temp_id) FROM tenant_llm), 0))
            """)
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET DEFAULT nextval('tenant_llm_id_seq')")
            DB.execute_sql("ALTER SEQUENCE tenant_llm_id_seq OWNED BY tenant_llm.temp_id")
            DB.execute_sql("ALTER TABLE tenant_llm ADD PRIMARY KEY (temp_id)")

            # 5. Add unique constraint
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. Rename temp_id to id
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key (PostgreSQL).")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT column_name
                                    FROM information_schema.columns
                                    WHERE table_catalog = current_database()
                                    AND table_name = 'tenant_llm'
                                    AND column_name = 'temp_id'
                                """)
        if cursor.rowcount > 0:
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def _update_tenant_llm_to_id_primary_key_gaussdb():
    """GaussDB-compatible implementation: add sequence-backed primary key."""
    # 适配点：GaussDB 可以使用 pg_attribute/pg_constraint 和 sequence 完成
    # 这段历史迁移，但入口保持独立。后续若 GaussDB 的系统表、ctid 行为或
    # sequence 语义与 PostgreSQL 有差异，只需替换这个函数或下层 helper。
    _update_tenant_llm_to_id_primary_key_gaussdb_catalog()


def _update_tenant_llm_to_id_primary_key_gaussdb_catalog():
    """GaussDB pg_catalog implementation for the tenant_llm migration."""
    try:
        with DB.atomic():
            # 0. Check if 'id' column already exists
            # 适配点：真实 GaussDB 上 information_schema 的 rowcount 行为不稳定；
            # PostgreSQL 分支和 GaussDB-compatible 分支都可以直接查
            # pg_attribute 并 fetchone() 判断列是否存在，避免重复迁移。
            cursor = DB.execute_sql("""
                            SELECT a.attname
                            FROM pg_attribute a
                            JOIN pg_class c ON c.oid = a.attrelid
                            JOIN pg_namespace n ON n.oid = c.relnamespace
                            WHERE n.nspname = current_schema()
                            AND c.relname = 'tenant_llm'
                            AND a.attname = 'id'
                            AND NOT a.attisdropped
                        """)
            if cursor.fetchone():
                return

            # 1. Add nullable integer column
            DB.execute_sql("ALTER TABLE tenant_llm ADD COLUMN temp_id INTEGER NULL")

            # 2. Assign sequential row numbers ordered consistently
            DB.execute_sql("""
                UPDATE tenant_llm
                SET temp_id = subq.rn
                FROM (
                    SELECT ctid,
                           ROW_NUMBER() OVER (ORDER BY tenant_id, llm_factory, llm_name) AS rn
                    FROM tenant_llm
                ) AS subq
                WHERE tenant_llm.ctid = subq.ctid
            """)

            # 3. Drop old composite primary key constraint
            # 适配点：旧版本 tenant_llm 使用 (tenant_id, llm_factory, llm_name)
            # 复合主键；新结构需要 id 主键和复合唯一约束。GaussDB-compatible
            # 和 PostgreSQL 分支都通过 pg_constraint 找到真实主键名，而不是
            # 假设约束名固定。
            cursor = DB.execute_sql("""
                SELECT con.conname
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relname = 'tenant_llm'
                  AND con.contype = 'p'
            """)
            row = cursor.fetchone()
            if row:
                DB.execute_sql(f'ALTER TABLE tenant_llm DROP CONSTRAINT "{row[0]}"')

            # 4. Make temp_id NOT NULL and create a sequence for it
            # 适配点：GaussDB-compatible/PostgreSQL 没有 MySQL AUTO_INCREMENT。
            # 这里用 sequence + nextval() 模拟自增主键，并把 sequence OWNED BY
            # temp_id，后续列重命名为 id 后仍跟随表结构生命周期。
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET NOT NULL")
            DB.execute_sql("CREATE SEQUENCE IF NOT EXISTS tenant_llm_id_seq")
            DB.execute_sql("""
                SELECT setval('tenant_llm_id_seq', COALESCE((SELECT MAX(temp_id) FROM tenant_llm), 0))
            """)
            DB.execute_sql("ALTER TABLE tenant_llm ALTER COLUMN temp_id SET DEFAULT nextval('tenant_llm_id_seq')")
            DB.execute_sql("ALTER SEQUENCE tenant_llm_id_seq OWNED BY tenant_llm.temp_id")
            DB.execute_sql("ALTER TABLE tenant_llm ADD PRIMARY KEY (temp_id)")

            # 5. Add unique constraint
            DB.execute_sql("""
                ALTER TABLE tenant_llm
                ADD CONSTRAINT uk_tenant_llm UNIQUE (tenant_id, llm_factory, llm_name)
            """)

            # 6. Rename temp_id to id
            DB.execute_sql("ALTER TABLE tenant_llm RENAME COLUMN temp_id TO id")

            logging.info("Successfully updated tenant_llm to id primary key (GaussDB).")

    except Exception as e:
        logging.error(str(e))
        cursor = DB.execute_sql("""
                                    SELECT a.attname
                                    FROM pg_attribute a
                                    JOIN pg_class c ON c.oid = a.attrelid
                                    JOIN pg_namespace n ON n.oid = c.relnamespace
                                    WHERE n.nspname = current_schema()
                                    AND c.relname = 'tenant_llm'
                                    AND a.attname = 'temp_id'
                                    AND NOT a.attisdropped
                                """)
        if cursor.fetchone():
            DB.execute_sql("ALTER TABLE tenant_llm DROP COLUMN temp_id")


def migrate_db():
    logging.disable(logging.ERROR)
    migrator = DatabaseMigrator[settings.DATABASE_TYPE.upper()].value(DB)
    alter_db_add_column(migrator, "file", "source_type", GaussDBEmptyStringCharField(max_length=128, null=False, default="", help_text="where dose this document come from", index=True))
    alter_db_add_column(migrator, "tenant", "rerank_id", GaussDBEmptyStringCharField(max_length=128, null=False, default="BAAI/bge-reranker-v2-m3", help_text="default rerank model ID"))
    alter_db_add_column(migrator, "dialog", "rerank_id", GaussDBEmptyStringCharField(max_length=128, null=False, default="", help_text="default rerank model ID"))
    alter_db_column_type(migrator, "dialog", "top_k", IntegerField(default=1024))
    alter_db_add_column(migrator, "tenant_llm", "api_key", CharField(max_length=2048, null=True, help_text="API KEY", index=True))
    alter_db_add_column(migrator, "api_token", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "tenant", "tts_id", CharField(max_length=256, null=True, help_text="default tts model ID", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "source", CharField(max_length=16, null=True, help_text="none|agent|dialog", index=True))
    alter_db_add_column(migrator, "task", "retry_count", IntegerField(default=0))
    alter_db_column_type(migrator, "api_token", "dialog_id", CharField(max_length=32, null=True, index=True))
    alter_db_add_column(migrator, "tenant_llm", "max_tokens", IntegerField(default=8192, index=True))
    alter_db_add_column(migrator, "api_4_conversation", "dsl", JSONField(null=True, default={}))
    alter_db_add_column(migrator, "knowledgebase", "pagerank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_token", "beta", CharField(max_length=255, null=True, index=True))
    alter_db_add_column(migrator, "task", "digest", TextField(null=True, help_text="task digest", default=""))
    alter_db_add_column(migrator, "task", "chunk_ids", GaussDBEmptyStringLongTextField(null=True, help_text="chunk ids", default=""))
    alter_db_add_column(migrator, "conversation", "user_id", CharField(max_length=255, null=True, help_text="user_id", index=True))
    alter_db_add_column(migrator, "task", "task_type", GaussDBEmptyStringCharField(max_length=32, null=False, default=""))
    alter_db_add_column(migrator, "task", "priority", IntegerField(default=0))
    alter_db_add_column(migrator, "user_canvas", "permission", CharField(max_length=16, null=False, help_text="me|team", default="me", index=True))
    alter_db_add_column(migrator, "user_canvas", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "llm", "is_tools", BooleanField(null=False, help_text="support tools", default=False))
    alter_db_add_column(migrator, "mcp_server", "variables", JSONField(null=True, help_text="MCP Server variables", default=dict))
    alter_db_rename_column(migrator, "task", "process_duation", "process_duration")
    alter_db_rename_column(migrator, "document", "process_duation", "process_duration")
    alter_db_add_column(migrator, "document", "suffix", CharField(max_length=32, null=False, default="", help_text="The real file extension suffix", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "errors", TextField(null=True, help_text="errors"))
    alter_db_add_column(migrator, "dialog", "meta_data_filter", JSONField(null=True, default={}))
    alter_db_column_type(migrator, "canvas_template", "title", JSONField(null=True, default=dict, help_text="Canvas title"))
    alter_db_column_type(migrator, "canvas_template", "description", JSONField(null=True, default=dict, help_text="Canvas description"))
    alter_db_add_column(migrator, "user_canvas", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_category", CharField(max_length=32, null=False, default="agent_canvas", help_text="agent_canvas|dataflow_canvas", index=True))
    alter_db_add_column(migrator, "canvas_template", "canvas_types", ListField(null=True, default=list, help_text="Canvas types"))
    alter_db_add_column(migrator, "knowledgebase", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "chat_channel", "dialog_id", CharField(max_length=32, null=True, help_text="connected dialog id", index=True))
    alter_db_add_column(migrator, "document", "pipeline_id", CharField(max_length=32, null=True, help_text="Pipeline ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_id", CharField(max_length=32, null=True, help_text="Gragh RAG task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_id", CharField(max_length=32, null=True, help_text="RAPTOR task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "graphrag_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "raptor_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_id", CharField(max_length=32, null=True, help_text="Mindmap task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "mindmap_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "artifact_task_id", CharField(max_length=32, null=True, help_text="Artifact compilation task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "artifact_task_finish_at", DateTimeField(null=True))
    alter_db_add_column(migrator, "knowledgebase", "skill_task_id", CharField(max_length=32, null=True, help_text="Skill generation task ID", index=True))
    alter_db_add_column(migrator, "knowledgebase", "skill_task_finish_at", DateTimeField(null=True))
    alter_db_column_type(migrator, "tenant_llm", "api_key", TextField(null=True, help_text="API KEY"))
    alter_db_add_column(migrator, "tenant_llm", "status", CharField(max_length=1, null=False, help_text="is it validate(0: wasted, 1: validate)", default="1", index=True))
    alter_db_add_column(migrator, "connector2kb", "auto_parse", CharField(max_length=1, null=False, default="1", index=False))
    alter_db_add_column(migrator, "llm_factories", "rank", IntegerField(default=0, index=False))
    alter_db_add_column(migrator, "api_4_conversation", "name", CharField(max_length=255, null=True, help_text="conversation name", index=False))
    alter_db_add_column(migrator, "api_4_conversation", "exp_user_id", CharField(max_length=255, null=True, help_text="exp_user_id", index=True))
    alter_db_add_column(migrator, "sync_logs", "task_type", CharField(max_length=32, null=False, default="sync", index=True))
    # Migrate system_settings.value from CharField to TextField for longer sandbox configs.
    # GaussDB 适配点：使用专用 TextField，保证 GaussDB 下改类型后仍允许
    # 存储层 NULL，并在 ORM 读取时还原成应用层 ""。
    alter_db_column_type(migrator, "system_settings", "value", GaussDBEmptyStringTextField(null=False, help_text="Configuration value (JSON, string, etc.)"))
    relax_gaussdb_empty_string_compatible_columns()
    alter_db_add_column(migrator, "document", "content_hash", CharField(max_length=32, null=True, help_text="xxhash128 of document content for change detection", default="", index=True))
    alter_db_add_column(migrator, "user_canvas_version", "release", BooleanField(null=False, help_text="is released", default=False, index=True))
    alter_db_add_column(migrator, "user_canvas", "tags", CharField(max_length=512, null=False, default="", help_text="Comma-separated tags for organizing agents", index=True))
    alter_db_add_column(migrator, "api_4_conversation", "version_title", CharField(max_length=255, null=True, help_text="canvas version title when session created", index=False))
    alter_db_column_type(migrator, "document", "size", BigIntegerField(default=0, index=True))
    alter_db_column_type(migrator, "file", "size", BigIntegerField(default=0, index=True))
    alter_db_add_column(migrator, "tenant", "ocr_id", CharField(max_length=128, null=True, help_text="default ocr model ID", index=True))
    alter_db_column_type(migrator, "chat_channel", "status", IntegerField(default=1, index=True))
    alter_db_rename_column(migrator, "chat_channel", "dialog_id", "chat_id")
    # ---- FileCommit / FileCommitItem: artifact-page commit extension ----
    alter_db_add_column(migrator, "file_commit", "title", CharField(max_length=255, null=True))
    alter_db_add_column(migrator, "file_commit", "comments", TextField(null=True))
    alter_db_add_column(migrator, "file_commit_item", "diff", LongTextField(null=True))
    alter_db_add_column(migrator, "file_commit_item", "content_after_storage", CharField(max_length=16, null=True, index=True))
    alter_db_add_column(migrator, "file_commit_item", "content_after_location", CharField(max_length=512, null=True))
    alter_db_add_column(migrator, "file_commit_item", "slug_kwd", CharField(max_length=512, null=True, index=True))
    alter_db_add_column(migrator, "file_commit_item", "page_type_kwd", CharField(max_length=32, null=True, index=True))
    alter_db_drop_index(migrator, "tenant_langfuse", "idx_tenant_langfuse_secret_key")
    alter_db_drop_index(migrator, "tenant_langfuse", "idx_tenant_langfuse_public_key")
    alter_db_drop_index(migrator, "tenant_langfuse", "idx_tenant_langfuse_host")

    # Drop both the explicit "idx_*" name from later migrations AND the
    # Peewee-auto-derived "<table-as-classname>_<col1>_<col2>" name from the
    # original TenantModelInstance definition (commit dc4b82523). Databases
    # created before #15460 dropped the model's `indexes = ((...,), True)`
    # tuple still carry the auto-named compound unique index, which makes a
    # second instance with an empty api_key (e.g. Ollama) fail with
    # "Duplicate entry ... for key 'tenantmodelinstance_api_key_provider_id'"
    # — see #15699.
    legacy_indexes = [
        ("tenant_model_instance", "idx_api_key_provider_id"),
        ("tenant_model_instance", "tenantmodelinstance_api_key_provider_id"),
        ("tenant_model", "idx_provider_model_instance"),
    ]
    for table_name, index_name in legacy_indexes:
        try:
            migrate(migrator.drop_index(table_name, index_name))
        except (OperationalError, ProgrammingError) as ex:
            msg = str(ex)
            if is_gaussdb_compatible_database():
                can_skip = is_undefined_object_error(ex) or is_duplicate_object_error(ex)
            else:
                can_skip = "1091" in msg or "can't DROP" in msg.lower() or "does not exist" in msg.lower() or "already exists" in msg.lower()
            if can_skip:
                pass
            else:
                logging.critical(f"Failed to drop index {index_name} on {table_name}: {ex}")
        except Exception as ex:
            logging.critical(f"Failed to drop index {index_name} on {table_name}: {ex}")
    logging.disable(logging.NOTSET)
    # this is after re-enabling logging to allow logging changed user emails
    migrate_add_unique_email(migrator)
    migrate_model_type_names()
    ensure_model_indexes(migrator)


def migrate_model_type_names():
    """Rename legacy model_type string values to the canonical asr/vision names.

    Previously the code used speech2text / image2text. LLMType now emits asr /
    vision, and the backend compares model_type strings directly. This idempotent
    data migration updates persisted rows in llm and tenant_llm before the new
    enum values are used at runtime.
    """
    RENAME_MAP = {
        "speech2text": "asr",
        "image2text": "vision",
    }
    tables = ["llm", "tenant_llm"]
    for table in tables:
        if not DB.table_exists(table):
            continue
        for old_name, new_name in RENAME_MAP.items():
            try:
                cursor = DB.execute_sql(
                    "UPDATE {} SET model_type = %s WHERE model_type = %s".format(table),
                    (new_name, old_name),
                )
                if cursor.rowcount:
                    logging.info(
                        "Migrated %s rows in %s.model_type from %s to %s",
                        cursor.rowcount,
                        table,
                        old_name,
                        new_name,
                    )
            except Exception as ex:
                logging.warning(
                    "Failed to migrate model_type values in %s (from %s to %s): %s",
                    table,
                    old_name,
                    new_name,
                    ex,
                )
