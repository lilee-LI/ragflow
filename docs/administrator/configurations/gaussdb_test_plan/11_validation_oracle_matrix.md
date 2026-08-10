# 11 - GaussDB 测试判定矩阵

> 本文档补充“如何判断有问题/没问题”。所有 GaussDB 元数据库和 Memory Store 测试都应同时看 API 结果、日志/任务状态、数据库实态和对照组；只看页面或接口返回不能作为通过依据。

本批次固定为 `20260710_fresh_001`。当前 643 个用例均先执行 MySQL metadata + Infinity 对照组，再执行 GaussDB metadata + GaussDB DocEngine/Memory 实验组；其中第 09/10 组分别为 6/27 个适配用例。本文档自身也必须产生独立审计报告。旧脚本、旧结果和备份目录不属于证据源。

## 1. 判定原则

### 1.1 必须同时记录的证据

| 证据 | 判定内容 | 失败信号 |
| --- | --- | --- |
| API/页面结果 | HTTP 状态、响应体 `code/message/data`、前端页面状态 | HTTP 成功但 `code != 0` 未被记录；页面成功但响应体失败 |
| 元数据库实态 | `user`、`tenant`、`knowledgebase`、`document`、`task`、`memory` 等表的行、字段、索引和时间戳 | API 成功但 DB 未写入、写错 tenant、空字符串/NULL 语义不符合约定 |
| Doc/Memory Store 实态 | GaussDB 物理表、向量列、全文列、索引、行数、`memory_id`/`kb_id` 隔离 | 只更新 metadata，不更新 store；store 行残留或跨资源可见 |
| 日志/任务状态 | `task`、ingestion log、worker/API 日志 | 任务失败但页面仍显示成功；错误被吞掉 |
| 对照组 | MySQL 元数据库、其他 tenant、同 tenant 非目标资源、不同 memory/dataset | GaussDB 特有差异未说明；目标操作污染对照资源 |
| 原始执行身份 | 批次、组别、case ID、request ID、fixture ID、开始/结束时间、配置指纹 | 无法证明是本批次新执行，或两组复用了同一响应/数据库状态 |

每份证据必须能回链到唯一 `case_id + group + attempt`。Token、密码、DSN、模型 Key、OAuth/MCP/Langfuse credential 只保留哈希前缀或脱敏值；响应若意外回显秘密，原始明文不得复制进报告，只记录字段路径、指纹和安全缺陷。

计划正文中的固定 email/name/ID/SQL 只是逻辑示例。执行驱动必须为当前 `case_id + group` 生成批次唯一值，经 API 建立等价前置，并把只读 SQL 编译成当前数据库方言和参数绑定；不得原样复用示例值、前一轮 ID 或字符串拼接 SQL。

### 1.2 HTTP 与业务码

RAGFlow 多数 REST 接口使用 `get_json_result()` 返回 JSON，业务错误常表现为 HTTP 200 + 响应体 `code != 0`。因此测试记录必须包含：

```text
transport_http_status=<实际 HTTP 状态>
body.code=<响应体 code>
body.message=<响应体 message>
```

判定规则：

- 业务成功：`body.code == 0`，且 DB/store 状态满足预期。
- 业务失败：`body.code != 0` 或框架层 HTTP 4xx/5xx，且 DB/store 不发生不应发生的写入。
- 缺陷：HTTP/页面看似成功，但 DB/store 状态错误；或 API 失败但 DB/store 已部分写入。
- 流式/下载/重定向/HTML 端点不强求 JSON `body.code`，按各计划的精确 transport/content 契约判定。
- 异步接口的成功响应只证明已受理；必须 condition poll 到明确终态，并保存任务、store 和日志时间线。固定 sleep 后“没有看到错误”不能判通过。

### 1.3 空字符串与 NULL

GaussDB A/ORA-compatible 环境可能把 `""` 存为 `NULL`。这不是天然缺陷，必须按当前 ORM 字段契约判定：

| 字段类型 | 通过条件 | 失败条件 |
| --- | --- | --- |
| 声明为空字符串兼容的字段 | DB 可为 `NULL`，API 回读必须恢复为 `""` 或上层约定值 | API 回读为 `null` 导致前端/业务逻辑异常 |
| 非兼容字段且业务要求非空 | API 必须拒绝 `""`/空白；DB 不应出现新行 | DB 写入 `NULL` 或绕过必填校验 |
| 普通 nullable/可选字段 | 按该用例规定分别记录 MySQL 与 GaussDB 物理值，并验证 API 消费方可接受 | 未经字段审查就把所有 `NULL` 强行恢复为 `""`，或把方言差异误判为兼容字段缺陷 |

## 2. 对照组要求

### 2.1 元数据库对照

对照组强制使用同一当前工作树、独立运行根中的 MySQL 元数据库 + Infinity。不得因执行成本改用“同 tenant 非目标资源”替代整组；后者只是每个组内部额外的污染哨兵。

| 场景 | 必需对照 |
| --- | --- |
| 用户、认证、Tenant、文件、知识库、文档、Chat、Memory metadata | MySQL 元数据库同请求，并同时保留同 tenant/cross-tenant canary |
| GaussDB 空字符串兼容 | MySQL 中 `""` 的存储/回读；GaussDB 中 `NULL` 的存储/回读 |
| 位运算过滤 | MySQL 与 GaussDB 查询结果数量和 ID 集合一致 |
| 分页/排序/LIKE | MySQL 与 GaussDB 返回顺序、总数、特殊字符匹配一致 |

### 2.2 Memory Store 对照

Memory Store 的对照实现是 Infinity，实验实现是 GaussDB；两组都还必须使用资源隔离 canary：

| 场景 | 必需对照 |
| --- | --- |
| 单 memory 写入/查询 | Infinity 专属表与 Gauss 同 tenant 共享物理表分别按各自结构取证；同一 tenant 下另一个 memory 不应出现目标消息 |
| 跨 tenant 查询 | 其他 tenant 的物理表或同表内不同 `memory_id` 不应返回 |
| 删除/遗忘/停用 | 删除目标消息后，对照 memory 的同 `message_id` 或同内容消息不变 |
| 向量空标记 | Gauss `q_{dim}_vec_empty=TRUE` 行不得参与向量和融合检索，`FALSE` 行可参与；Infinity 不强求该物理列 |

Memory 物理表名计算：

```python
import hashlib

def memory_index_name(uid: str, prefix: str = "") -> str:
    return f"memory_{prefix}_{uid}" if prefix else f"memory_{uid}"

def memory_physical_table(uid: str, prefix: str = "") -> str:
    logical = memory_index_name(uid, prefix)
    return "ragflow_mem_" + hashlib.sha1(logical.encode("utf-8")).hexdigest()[:32]

def infinity_memory_table(uid: str, memory_id: str, prefix: str = "") -> str:
    return f"{memory_index_name(uid, prefix)}_{memory_id}"
```

上述 `uid` 是 Memory owner tenant ID，真实维度必须由本批次 Ollama embedding 响应读取，禁止硬编码 3/5/1024。

## 3. 模块判定矩阵

### 3.1 启动和迁移

| 检查点 | DB 实态 SQL | 对照组 | 通过条件 |
| --- | --- | --- | --- |
| 表初始化 | 参数化查询 `information_schema.tables`，schema 绑定为本批次专属值 | MySQL 表清单 | 必要业务表存在，字段/索引与设计一致 |
| DDL 幂等 | 重启前后 `information_schema.columns/statistics` 快照 | 重复启动 MySQL | 无重复字段/索引，无启动失败 |
| raw SQL 迁移 | 针对迁移目标表查列、默认值、索引 | MySQL 迁移结果 | GaussDB 方言执行成功，数据不丢失 |

### 3.2 用户和认证

| 检查点 | DB 实态 SQL | 对照组 | 通过条件 |
| --- | --- | --- | --- |
| 注册 | `SELECT id,email,nickname,status,is_active FROM "user" WHERE email=...;` | MySQL 注册同邮箱 | `user`、`tenant`、`user_tenant`、root folder 一致写入 |
| 登录/登出 | 参数化查询 `access_token,last_login_time`，落盘只存 token 指纹 | 旧 token 的无 cookie 请求 | 登出后旧 token 不可用，DB token 具有 `INVALID_` 前缀 |
| 空 nickname | `SELECT nickname, nickname IS NULL FROM "user" WHERE email=...;` | MySQL 空串 | GaussDB 可存 NULL，但 API/前端显示按兼容契约恢复 |

### 3.3 Dataset/Document

| 检查点 | DB 实态 SQL | Store/任务实态 | 对照组 |
| --- | --- | --- | --- |
| 创建 dataset | `SELECT id,name,tenant_id,embd_id,parser_config FROM knowledgebase WHERE id=...;` | 无 chunk 写入 | 另一个 tenant 不可见 |
| 上传文档 | `SELECT id,kb_id,name,chunk_num,token_num,parser_id,status FROM document WHERE id=...;` | `task` 行、doc store chunk 行 | 同 KB 其他 document 不变 |
| 解析成功 | `document.chunk_num > 0`、任务终态成功 | 对照 Infinity / 实验 Gauss DocEngine 中 `kb_id/doc_id` 行存在 | 双组相同输入的业务字段与 chunk canary 一致，物理结构按各组取证 |
| 解析失败 | `document.chunk_num = 0` 或旧值不被污染，`task.progress = -1` 且 `progress_msg` 保存错误 | doc store 无半成品或可清理 | 对照文档可继续解析 |
| 删除 | `document/file2document/task` 目标行清理 | doc store 目标 chunk 清理 | 同 KB 其他文档和跨 tenant 文档不变 |

### 3.4 Memory Metadata

| 检查点 | DB 实态 SQL | Store 实态 | 对照组 |
| --- | --- | --- | --- |
| 创建 memory | `SELECT id,name,memory_type,tenant_id,embd_id,llm_id FROM memory WHERE id=...;` | 首次创建不应产生 `ragflow_mem_*` 消息行 | MySQL `memory_type` 位值一致 |
| 列表过滤 | 记录 SQL trace 或查响应 ID 集合 | 无 store 变更 | 同请求 MySQL ID 集合一致 |
| 更新配置 | `SELECT update_time,... FROM memory WHERE id=...;` | 若无消息，store 不变 | 未更新字段保持不变 |
| 删除 memory | `SELECT COUNT(*) FROM memory WHERE id=...;` | 目标 `memory_id` 消息行清理 | 同表其他 `memory_id` 行不变 |

### 3.5 Memory Store

| 检查点 | DB 实态 SQL | 对照组 | 通过条件 |
| --- | --- | --- | --- |
| 写入消息 | 实验组参数化查询 `id,message_id,memory_id,status_int,content_ltks,q_<dim>_vec_empty`；对照组用 Infinity table API | 其他 memory 无目标内容 | 行存在；Gauss 真实向量的 `q_<dim>_vec_empty=FALSE` |
| 查询/检索 | 查 `status_int`、`forget_at`、向量空标记 | Gauss `q_<dim>_vec_empty=TRUE` 专属 adapter 行；Infinity 空/错维输入 | 返回集排除遗忘、search 中停用、以及空向量行；recent 的 status 差异按计划单列 |
| 遗忘/停用 | `SELECT forget_at,status_int FROM ragflow_mem_<sha1> WHERE id=...;` | 同 memory 其他消息不变 | 字段更新且列表/检索不可见 |
| 删除 | `SELECT COUNT(*) FROM ragflow_mem_<sha1> WHERE memory_id=...;` | 其他 memory 行数不变 | 目标行清理，无残留 |
| 动态维度 | `information_schema.columns` 查运行时维度对应的 `q_<dim>_vec` 与 `q_<dim>_vec_empty` | 原维度消息 | 新维度列新增，旧维度被重置为零占位且 empty=TRUE；Infinity 按固定 per-memory schema 判定 |

### 3.6 Connector/System 与故障恢复适配

| 检查点 | 实验组实态 | 对照组 | 通过条件 |
| --- | --- | --- | --- |
| 调度时间方言 | `refresh_freq/prune_freq * INTERVAL '1 minute'`，真实到期查询成功 | MySQL `INTERVAL ... MINUTE` | 两组只返回同一语义的到期任务，排除项均不返回 |
| Connector 空串 | `sync_logs.error_msg/full_exception_trace` 物理可为 NULL，ORM/API 为空串；非空追加使用 COALESCE | 物理空串，ORM/API 为空串 | 空 suffix 不改值，非空 suffix 不丢失且不产生字符串 `NULL` |
| DISTINCT/ORDER BY | SELECT 显式含 `update_time`，真实查询成功 | 相同 ID 集合和顺序 | API/service/DB 均按 `update_time DESC` 返回同一集合 |
| 系统健康 | metadata/DocEngine 均报告 GaussDB，专用端点含 health/performance | MySQL + Infinity，专用端点 not_configured | 组件健康、组间心跳隔离、响应无 DSN/password |
| SQLSTATE/重试 | 08xxx/57P0x、40P01/40001/55P03 按适配分类 | MySQL 对应 errno/真实锁语义 | 只重试允许的完整操作，次数有界，非重试错误原样传播 |
| Pool/权限/事务 | 坏连接丢弃、Schema USAGE/CREATE 检查、错误事务完整回滚 | 等价连接/事务恢复 | 故障命中后可恢复，无泄漏、重复写或半提交 |

## 4. 问题分类

| 分类 | 判定标准 | 处理方式 |
| --- | --- | --- |
| 共同代码缺陷 | 两组均违反 API、安全、一致性或权限契约 | 记录两组独立复现、源码边界和无副作用/残留证据 |
| GaussDB 适配缺口 | MySQL 正常，GaussDB 因方言、NULL、DDL、索引、连接池差异失败 | 优先在 GaussDB adapter/方言层适配，不改业务逻辑 |
| 测试用例错误 | 用例引用不存在字段、错误 API、错误响应文案或错误表结构 | 修正文档后再执行，不作为产品缺陷 |
| 环境/模型问题 | DB 状态正确，但依赖不可达或凭据不可用导致任务失败 | 记录依赖探针和错误；修复环境后仍使用实际 Qwen chat + Ollama embedding 重测，不用 fake model 代替核心 happy path |

“环境问题”不是通过或跳过状态。每个 case 的最终状态只能在两组完整执行后判 PASS/FAIL；确有外部阻断时保持未完成并记录 blocker，恢复后从该 case 的对照组重新执行。

## 5. 前端用户路径到 API/DB 的映射

| 用户路径 | 前端入口 | API | 必查 DB/store 状态 | 对照组 |
| --- | --- | --- | --- | --- |
| 创建 Memory | `/memories` 创建弹窗，默认 `memory_type=["raw"]` | `POST /api/v1/memories` | `memory` 表只落 `name/memory_type/embd_id/llm_id` 和模型默认值；不创建 `ragflow_mem_*` 消息行 | 同 tenant 重名自动改名；大写/空 `memory_type` 绕过前端直调 API |
| Memory 列表筛选 | `/memories` 搜索、owner/type/storage 过滤 | `GET /api/v1/memories?keywords=&owner_ids=&memory_type=&storage_type=` | 响应 ID 集合符合 `tenant_id`、`permissions` 和 bitwise 过滤 | MySQL 同请求；不可访问 owner 不返回 |
| Memory 设置页 | `/memory/:id/memory-setting` | `GET /api/v1/memories/<id>/config`、`PUT /api/v1/memories/<id>` | `memory` 表更新请求允许的字段；已有消息时 `embd_id/memory_type` 不变 | 空 memory 可更新 `embd_id/memory_type`；非空 memory 被拒绝 |
| Memory 消息页 | `/memory/:id/memory-message` | `GET /memories/<id>`、`DELETE/PUT /messages/<memory_id>:<message_id>`、`GET /messages/<memory_id>:<message_id>/content` | `ragflow_mem_<sha1>` 中 `status_int/forget_at/content_ltks` 与页面一致 | 同 `message_id` 的其他 memory 不被影响 |
| 最近消息/检索 | 应用调用或 API 调试 | `GET /api/v1/messages`、`GET /api/v1/messages/search` | recent 默认隐藏 `forget_at IS NOT NULL`；search 默认追加 `status_int=1`，Gauss 向量路径排除 `q_<dim>_vec_empty=TRUE` | 停用消息对 recent/search 的差异；专属空向量 adapter 行 |
| Dataset 创建和文档上传 | 知识库页面创建、上传文件、启动解析 | `POST/GET/PATCH /api/v1/datasets`、`POST /api/v1/datasets/<id>/documents`、文档解析相关接口 | `knowledgebase`、`document`、`task`、doc store chunk/metadata 行；失败任务保留错误且不污染 chunk 计数 | 同 KB 其他文档、跨 tenant dataset、MySQL metadata + Infinity |
