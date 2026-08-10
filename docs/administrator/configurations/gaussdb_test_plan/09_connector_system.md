# 09 - Connector 调度与系统健康适配测试

## 目标与边界

本组只验证 Connector/System 中由 GaussDB metadata 与 GaussDB DocEngine 适配直接引入的分支，不承担 Connector 通用 CRUD、OAuth、LLM/Provider、MCP、Langfuse、Admin、Tenant、API Token、安全或性能回归。

当前共 **6 个**用例：

- 调度 SQL 方言：`TC-CONN-015`、`TC-CONN-016`；
- 系统与 GaussDB 健康：`TC-CONN-031`、`TC-CONN-032`；
- 空字符串与查询方言：`TC-CONN-093`、`TC-CONN-094`。

直接对应的实现位置：

- `api/db/services/connector_service.py`：`_poll_interval_expr()`、`list_due_sync_tasks()`、`list_due_prune_tasks()`、`list_sync_tasks()`、`_append_text_expr()`；
- `api/db/db_models.py`：`sync_logs.error_msg/full_exception_trace` 的空字符串兼容字段；
- `api/apps/restful_apis/system_api.py` 与 `api/utils/health_utils.py`：系统状态和 GaussDB 健康信息。

## 执行约定

- 每例均使用本轮新建 fixture，先执行 MySQL metadata + Infinity 对照组，再执行 GaussDB metadata + GaussDB DocEngine 实验组；禁止复用备份脚本、备份结果或旧 ID。
- API、metadata DB、DocEngine、Redis、对象存储和日志必须组间隔离。
- 业务 fixture 通过公开 API 创建；只有调度到期时间、空串物理值和 SQL 编译核对可使用最小专属 adapter/DB fixture。
- HTTP 200 不是唯一判据；必须同时核对业务码、返回内容、数据库/SQL 证据和清理结果。
- 证据不得包含密码、DSN、Bearer Token 或其他密钥原文。

## 用例

### TC-CONN-015: 调度任务使用 GaussDB INTERVAL 表达式

**适配点**：`_poll_interval_expr("refresh_freq")`。

**步骤**：

1. 两组各创建一个处于 SCHEDULE 状态并已关联知识库的 Connector；设置一条已到期 sync log，并构造未到期、CANCEL、DONE、不同 task type 的排除记录。
2. 调用 `SyncLogsService.list_due_sync_tasks()`，记录编译表达式和返回任务 ID。
3. 清理全部专属 Connector、Dataset 和 sync log。

**通过标准**：MySQL 表达式为 `NOW() - INTERVAL \`t2\`.\`refresh_freq\` MINUTE`；GaussDB 表达式为 `NOW() AT TIME ZONE '<TIMEZONE>' - (t2.refresh_freq * INTERVAL '1 minute')`。两组只返回同一目标语义的到期任务，所有排除记录均不返回。

### TC-CONN-016: Prune 调度使用 GaussDB INTERVAL 表达式

**适配点**：`_poll_interval_expr("prune_freq")`。

**步骤**：

1. 两组各创建 `config.sync_deleted_files=true` 且 `prune_freq>0` 的到期 fixture，并构造开关关闭、频率为零和未到期记录。
2. 调用 `SyncLogsService.list_due_prune_tasks()`，记录编译表达式和返回任务 ID。
3. 清理全部专属 fixture。

**通过标准**：两组分别使用本组方言；只有启用删除同步、频率有效且已到期的 prune 任务被返回。

### TC-CONN-031: 系统状态准确报告组内后端

**适配点**：metadata DB/DocEngine 类型归一化与健康聚合。

**步骤**：

1. 确认当前组 API、worker、sync 服务和组内 executor 心跳可用。
2. 认证调用 `GET /api/v1/system/status`。
3. 核对 `doc_engine`、`storage`、`database`、`redis` 状态以及 `task_executor_heartbeats`。

**通过标准**：组件均为健康；对照组报告 MySQL + Infinity，实验组报告 GaussDB metadata + GaussDB DocEngine；只出现当前组 executor，不泄露连接密钥。

### TC-CONN-032: GaussDB 专用健康端点

**适配点**：`GET /api/v1/system/gaussdb/status`。

**步骤**：

1. 两组使用各自 owner 认证调用专用健康端点。
2. 对返回对象做递归敏感值扫描。

**通过标准**：对照组返回 `status=not_configured`；实验组返回 `status=alive`，且包含结构化 `health` 与 `performance`；两组均不泄露 DSN/password。

### TC-CONN-093: sync_logs 空字符串与 NULL 语义一致

**适配点**：`EmptyStringTextField` 与 `_append_text_expr()` 的 GaussDB `COALESCE`。

**步骤**：

1. 两组创建 Connector 并触发 sync log。
2. 核对 `error_msg/full_exception_trace`：MySQL 物理空串，GaussDB 可为 NULL；再经 ORM/API 读取。
3. 在专属 fixture 分别追加空 suffix 和非空哨兵文本。
4. 清理专属 fixture。

**通过标准**：物理差异符合数据库语义，ORM/API 均读为应用层空串；空 suffix 不改值，非空 suffix 在 GaussDB NULL 基础上正确追加且不产生字符串 `NULL`。

### TC-CONN-094: SELECT DISTINCT 与 ORDER BY 兼容

**适配点**：`list_sync_tasks()` 显式选择排序列 `update_time`。

**步骤**：

1. 两组为同一 Connector 构造多条不同时间的 sync log。
2. 调用日志列表 API，并通过 adapter 记录实际编译 SQL/选择列。
3. 将 API ID 顺序与数据库基准顺序比较。
4. 清理专属 fixture。

**通过标准**：GaussDB 查询不出现 DISTINCT/ORDER BY 兼容错误；SELECT 列显式包含 `update_time`；两组返回相同 ID 集合并按 `update_time DESC` 稳定排序。

## 完成标准

- 6 个计划 ID 与 6 个 runner ID 完全一致且无占位实现；
- 每例都有两组独立证据和清理状态；
- 本组之外的通用功能不计入适配通过率。
