# 07 - Memory Store 端到端测试计划

> **文档定位**：本文档是 RAGFlow GaussDB Memory Store 最核心的端到端测试设计，覆盖从 API 到物理表的全链路验证。
>
> **与 06 文档的关系**：`06_memory_metadata.md` 覆盖 `memory` 元数据表（memory 的 CRUD、权限、配置），本文档仅覆盖**消息数据**（存储在 `ragflow_mem_<sha1>` 物理表中的消息行）。
>
> **适用范围**：所有用例均先在 MySQL metadata + Infinity Memory Store 对照组执行，再在 GaussDB metadata + GaussDB Memory Store 实验组执行。业务契约比较两组 API 结果；GaussDB 专属物理结构在实验组做 catalog 证明，对照组执行同一 adapter 操作并证明 Infinity 的等价结构/行为，不能把 GaussDB SQL 套到 Infinity，也不能把对照组记为跳过。

### 本批次强制执行约定

- 本文件中的 `M1/M2/T1/T2` 和数值 `message_id` 都是符号名。runner 必须通过本批次公开 API 新建 fixture，把实际 ID 写入 case context；消息 ID 必须从 API/只读存储结果捕获，不得把 `100/200/999` 当成真实固定 ID，也不得修改 Redis 序列来凑 ID（启动维护和明确的序列故障用例除外）。
- `<nonexistent_message_id>` 必须在请求前由只读 max/精确查询证明在目标 Memory 中不存在；不能硬编码 `999999` 后假设它永远不存在。
- `BASE_URL` 在对照组为 `http://127.0.0.1:9380`，实验组为 `http://127.0.0.1:9480`。下文请求统一使用 `${BASE_URL}`，两组 token、metadata DB、DocEngine、Redis DB、MinIO bucket、日志和运行根完全独立。
- 所有 API 请求都必须携带当前组有效的 `Authorization`；写请求还必须携带正确 `Content-Type`。个别为突出参数而省略 header 的短片段不表示允许匿名执行，runner 必须补齐并保存实际请求证据。
- `$JWT_T1/$JWT_T2` 表示无 cookie 的签名登录凭据，作为普通业务用例默认认证；`$API_TOKEN_T1` 只在明确验证 API key 分支时使用。两组分别获取，绝不复用 token。
- 除非用例明确测试非 raw 提取，07 的 Memory fixture 一律以 `memory_type=["raw"]` 创建，避免 Qwen 提取消息改变 raw 数量、Task 时间和 size cache oracle；模型 ID 仍使用本批次真实配置。
- 每个用例使用包含批次/组别/case ID 的唯一 name、agent_id、session_id，按这些键取证；不得用“最新一行”代替精确 fixture 过滤。
- 使用本批次真实 Ollama embedding 和 Qwen chat 配置；向量维度从一次真实 embedding 响应/实际表结构读取为 `<embedding_dim>`，不得假定为 3。只有明确标为 adapter 维度/故障注入的用例可以使用受控小维度向量。
- 业务消息写入只走 API。DB、Infinity、GaussDB 和 Redis 默认只读；MERGE、跨维度、缺字段、索引破坏、启动序列等明确的 adapter/故障/维护用例可在专属 fixture 上做最小直接操作，必须记录前后状态并清理，且不得使用备份脚本、旧 fixture 或历史结果。
- `POST /messages` 当前在 HTTP 请求内同步生成 raw message ID、完成 embedding、写入 raw 行并更新 size cache；之后才创建 Task 并把非 raw 提取任务放入队列。成功只证明 raw 行已写入且任务已投递，仍须按唯一 `agent_id/session_id` 轮询并只读核对；非 raw extract 完成情况需另外轮询 Task/列表。
- 对照组的 Memory Store 每个 memory 是独立 Infinity 表 `memory_<tenant>_<memory>`；实验组同 tenant 的多个 memory 共享 `ragflow_mem_<sha1(index_name)>`，由 `memory_id` 行边界隔离。所有物理证据均按该差异分别采集。

---

## 目录

1. [测试架构概述](#1-测试架构概述)
2. [消息写入 (Message Write)](#2-消息写入-message-write)
3. [消息列表和最近消息 (Message List & Recent)](#3-消息列表和最近消息-message-list--recent)
4. [消息检索 (Message Search)](#4-消息检索-message-search)
5. [消息状态更新 (Status Update)](#5-消息状态更新-status-update)
6. [消息遗忘 (Message Forget)](#6-消息遗忘-message-forget)
7. [消息内容获取 (Content Retrieval)](#7-消息内容获取-content-retrieval)
8. [Memory 内数据隔离 (Data Isolation)](#8-memory-内数据隔离-data-isolation)
9. [向量维度管理 (Vector Dimension)](#9-向量维度管理-vector-dimension)
10. [启动维护 (Startup Maintenance)](#10-启动维护-startup-maintenance)
11. [物理表和索引验证 (Physical Table & Index)](#11-物理表和索引验证-physical-table--index)

---

## 1. 测试架构概述

### 1.1 系统组件关系

```
POST /api/v1/messages
  → memory_api_service.add_message()
    → _filter_accessible_memories()      # 权限过滤
    → queue_save_to_memory_task()         # 对每个可访问 memory_id 循环
      → REDIS_CONN.generate_auto_increment_id(namespace="memory")
      → embed_and_save()                  # raw 消息在 HTTP 请求内同步写入
        → embedding_model.encode()        # 向量化
        → MessageService.create_index()   # 首次写入时建表
        → MessageService.insert_message() # MERGE INTO
          → GaussDBMemoryConnection.insert()
            → _message_to_row()           # 字段映射 + tokenize
            → _ensure_vector_column_exists()
            → _build_merge_sql()          # 实验组 MERGE INTO ... ON (t.id = s.id)
      → 创建 Task 并投递非 raw 提取任务       # raw-only Memory 仍会创建任务，worker 很快结束
```

### 1.2 物理表结构

下表是 GaussDB 实验组结构；Infinity 对照组使用 `conf/message_infinity_mapping.json` 加当前维度向量列，字段映射后的 API 契约相同，但物理列名/类型不要求相同。

| 列名 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR2(96) PK | 格式 `{memory_id}_{message_id}` |
| message_id | NUMBER(19) | 全局递增 ID（Redis 生成） |
| message_type_kwd | VARCHAR2(64) | raw / 提取类型 |
| source_id | NUMBER(19) | 0=原始消息，否则指向 raw 消息的 message_id |
| memory_id | VARCHAR2(32) | 所属 memory |
| user_id | VARCHAR2(64) | 用户 ID |
| agent_id | VARCHAR2(64) | Agent ID |
| session_id | VARCHAR2(128) | Session ID |
| zone_id | NUMBER(10) | 时区，默认 0 |
| valid_at | TIMESTAMP | 消息生效时间 |
| invalid_at | TIMESTAMP | 消息失效时间 |
| forget_at | TIMESTAMP | NULL=未遗忘，非 NULL=已遗忘 |
| status_int | NUMBER(10) | 1=活跃，0=不活跃 |
| content_ltks | TEXT | 原始内容 |
| tokenized_content_ltks | TEXT | 分词后内容（fine_grained_tokenize(tokenize(...))） |
| q_{dim}_vec | floatvector(dim) | 向量，动态列 |
| q_{dim}_vec_empty | BOOLEAN | FALSE=真实向量，TRUE=占位零向量 |

### 1.3 测试环境要求

- 两套独立服务均运行：对照组 `DOC_ENGINE=infinity`，实验组 `DOC_ENGINE=gaussdb`
- 两组独立 Redis DB 可用（message_id 序列、内存缓存、任务队列）
- 使用本批次实际配置的 Ollama embedding；受控 fake embedding 只用于明确的 adapter 维度/故障注入用例
- 两个不同租户的用户账号（跨租户测试）
- 实验组 DocEngine 连接读取独立运行根的 `conf/gaussdb.config`；不得假设 `GAUSSDB_HOST/GAUSSDB_DATABASE/...` 环境变量会被当前 DocEngine 初始化代码直接消费

### 1.4 通用测试辅助函数

```python
# 物理表名计算
def physical_table_name(index_name: str) -> str:
    digest = hashlib.sha1(index_name.encode("utf-8")).hexdigest()[:32]
    return f"ragflow_mem_{digest}"

# index_name 计算
def index_name(uid: str) -> str:
    prefix = os.environ.get("ES_INDEX_PREFIX", "").strip()
    return f"memory_{prefix}_{uid}" if prefix else f"memory_{uid}"
```

### 1.5 测试编号规则

| 前缀 | 所属章节 |
|------|---------|
| TC-MS-0XX | 消息写入 |
| TC-MS-1XX | 消息列表和最近消息 |
| TC-MS-2XX | 消息检索 |
| TC-MS-3XX | 消息状态更新 |
| TC-MS-4XX | 消息遗忘 |
| TC-MS-5XX | 消息内容获取 |
| TC-MS-6XX | 数据隔离 |
| TC-MS-7XX | 向量维度管理 |
| TC-MS-8XX | 启动维护 |
| TC-MS-9XX | 物理表和索引 |

---

## 2. 消息写入 (Message Write)

本节所有成功 POST 的精确响应均为 HTTP 200 / `code=0` / `message="All add to task."`；随后必须用本用例唯一 agent/session 捕获 raw 行和 Task。若 queue 投递失败，当前代码可能已经写入 raw 行却返回失败，该部分一致性由实际前后证据记录，不能只看响应码。

### TC-MS-001: 写入单条原始消息并验证全字段

**前置条件**：已通过 API 创建专属 memory（符号名 M1），并取得本批次实际 `<embedding_dim>`

**步骤**：
1. 先使用公开 API key（`$API_TOKEN_T1`）发送请求；公开参考文档允许该认证方式透传外部参与者 `user_id="user-001"`：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $API_TOKEN_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-write-001",
    "session_id": "session-write-001",
    "user_id": "user-001",
    "user_input": "I like coriander in my soup",
    "agent_response": "Noted your preference for coriander"
  }'
```
2. 再使用 JWT/session credential 重复写入，改用唯一 agent/session，并在 body 传 `user_id="spoofed-user"`；该行必须归属当前登录用户，不能被 body 冒充。
3. 两次预期响应均为 HTTP 200，`code=0`，`message="All add to task."`。分别按唯一 agent/session 轮询并捕获两个实际 message_id；raw 行应在 POST 返回前已写入，但轮询仍作为端到端证据，超时 90 秒。
4. 对照组以 Infinity adapter/表只读核对同一业务字段；实验组执行以下物理表查询（把维度替换为实际值，并按两个唯一 agent/session 取行）：
```sql
SELECT id, message_id, message_type_kwd, source_id, memory_id,
       user_id, agent_id, session_id, zone_id, status_int,
       content_ltks, tokenized_content_ltks,
       q_<embedding_dim>_vec_empty, forget_at
  FROM ragflow_mem_<sha1_of_index_name>
 WHERE memory_id = 'M1'
   AND message_type_kwd = 'raw'
   AND agent_id IN ('agent-write-001', '<jwt_unique_agent_id>')
 ORDER BY message_id;
```
5. 预期结果：
   - `id` = `M1_{message_id}`
   - `message_id` > 0（NUMBER 类型，Redis 自增分配）
   - `message_type_kwd` = `'raw'`
   - `source_id` = 0
   - `memory_id` = `'M1'`
   - API key 行按公开 API 契约应为 `'user-001'`；JWT/session 行必须等于当前登录用户 ID，`spoofed-user` 不得生效
   - API key 行为 `agent-write-001/session-write-001`；JWT 行为 runner 生成的另一组唯一 agent/session
   - `zone_id` = 0
   - `status_int` = 1
   - `content_ltks` 包含 `"I like coriander in my soup"` 和 `"Noted your preference for coriander"`
   - `tokenized_content_ltks` 非空且为分词后的内容
   - `q_<embedding_dim>_vec_empty` = FALSE（对应向量是真实值）
   - `forget_at` IS NULL

**预期结果**：消息完整写入，所有字段符合预期，向量列非空。

代码审查风险：认证加载器当前设置的是 `g.auth_type=AUTH_API`，没有任何代码设置路由读取的 `g.auth_via_api_token`，所以 API key 行很可能也被覆写为当前用户。若两组复现，登记公开 `user_id` 契约失效的共同缺陷；JWT 防冒充仍应通过。

---

### TC-MS-002: 写入消息到多个 memory（扇出写入）

**前置条件**：已创建 memory M1 和 M2（同一 tenant T1）

**步骤**：
1. 发送请求：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1", "M2"],
    "agent_id": "agent-fanout",
    "session_id": "session-fanout",
    "user_input": "remember guava preference",
    "agent_response": "stored in both memories"
  }'
```
2. 预期响应：HTTP 200，`code=0`，`message="All add to task."`
3. 等待两个 memory 都有消息（分别轮询 M1、M2）
4. 数据库验证：
```sql
-- 查 M1 的行
SELECT memory_id, agent_id, content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw';
-- 查 M2 的行
SELECT memory_id, agent_id, content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M2' AND message_type_kwd = 'raw';
```
5. 验证：
   - M1 和 M2 各有一条 raw 消息
   - 两条消息的 `content_ltks` 相同
   - 两条消息的 `memory_id` 分别为 'M1' 和 'M2'
   - 两条消息的 `message_id` 不同（各自独立分配）
   - 实验组两行位于同一 tenant 共享物理表并由 `memory_id` 隔离；对照组位于两个独立 Infinity memory 表，API 结果语义相同

**预期结果**：扇出写入成功，每个 memory 各有一份独立消息。

---

### TC-MS-003: 使用不同 agent_id 和 session_id 写入

**前置条件**：已创建 memory M1

**步骤**：
1. 发送请求写入消息 A（agent_a, session_a）：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-alpha",
    "session_id": "session-alpha",
    "user_input": "alpha input",
    "agent_response": "alpha response"
  }'
```
2. 发送请求写入消息 B（agent_b, session_b）：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-beta",
    "session_id": "session-beta",
    "user_input": "beta input",
    "agent_response": "beta response"
  }'
```
3. 等待两条消息就绪
4. 数据库验证：
```sql
SELECT agent_id, session_id, content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw'
 ORDER BY message_id;
```
5. 验证两行的 agent_id 和 session_id 分别为 `agent-alpha/session-alpha` 和 `agent-beta/session-beta`

**预期结果**：不同 agent/session 的消息独立存储，字段正确。

---

### TC-MS-004: 写入包含中文内容的消息

**前置条件**：已创建 memory M1

**步骤**：
1. 发送请求：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-cn",
    "session_id": "session-cn",
    "user_input": "我喜欢在汤里放香菜",
    "agent_response": "已记录您对香菜的偏好"
  }'
```
2. 预期响应：HTTP 200
3. 数据库验证：
```sql
SELECT content_ltks, tokenized_content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw'
 ORDER BY message_id DESC LIMIT 1;
```
4. 验证：
   - `content_ltks` 包含完整中文文本 "我喜欢在汤里放香菜"
   - `tokenized_content_ltks` 非空，包含中文分词结果

**预期结果**：中文内容正确存储和分词。

---

### TC-MS-005: 写入包含特殊字符的消息（SQL 注入字符、引号、百分号）

**前置条件**：已创建 memory M1

**步骤**：
1. 发送请求：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-special",
    "session_id": "session-special",
    "user_input": "test '\'' OR 1=1 -- and \"quotes\" and 100% discount",
    "agent_response": "value with ; DROP TABLE users; -- and backslash \\ end"
  }'
```
2. 预期响应：HTTP 200，写入成功
3. 数据库验证：
```sql
SELECT content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw'
 ORDER BY message_id DESC LIMIT 1;
```
4. 验证：
   - `content_ltks` 包含原始特殊字符，未被截断或执行
   - 物理表未被注入破坏

**预期结果**：特殊字符通过参数化查询安全写入，无 SQL 注入风险。

---

### TC-MS-006: 写入包含 Emoji 的消息

**前置条件**：已创建 memory M1

**步骤**：
1. 发送请求：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-emoji",
    "session_id": "session-emoji",
    "user_input": "I love this food 🍜👍🔥",
    "agent_response": "Noted your love for ramen 🍜"
  }'
```
2. 预期响应：HTTP 200
3. 实验组验证 `content_ltks` 精确保留全部 emoji；`tokenized_content_ltks` 精确等于当前 tokenizer 对原文的输出，不要求 tokenizer 一定保留 emoji。对照组验证 API 内容精确保留且原生 analyzer 可正常索引，不要求存在 tokenized 物理列

**预期结果**：Emoji 内容正确存储，不会导致编码错误。

---

### TC-MS-007: 写入超长内容消息

**前置条件**：已创建 memory M1

**步骤**：
1. 构造一段 5000 字符的文本内容（重复的 lorem ipsum + 随机标识字符串）
2. 发送请求：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-long",
    "session_id": "session-long",
    "user_input": "<5000字符的长文本>",
    "agent_response": "<5000字符的长文本>"
  }'
```
3. 预期响应：HTTP 200
4. 数据库验证：
```sql
SELECT LENGTH(content_ltks) AS content_len,
       LENGTH(tokenized_content_ltks) AS tokenized_len
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw'
 ORDER BY message_id DESC LIMIT 1;
```
5. 在客户端按 UTF-8 解码后的 Python 字符串复算 `len(f"User Input: {user_input}\nAgent Response: {agent_response}")`，DB `LENGTH(content_ltks)` 应与其字符数相等；另核对首尾随机标识，不能只用 `>5000` 放过截断

**预期结果**：TEXT 类型列可容纳超长内容，不截断。

---

### TC-MS-008: 缺少必填字段写入（缺少 user_input）

**前置条件**：已创建 memory M1

**步骤**：
1. 发送请求（缺少 user_input）：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-missing",
    "session_id": "session-missing",
    "agent_response": "some response"
  }'
```
2. 预期响应：HTTP 200，`{"code": 101}`（参数校验失败，`@validate_request` 拦截）
3. 数据库验证：无新行写入

**预期结果**：缺少 `user_input` 字段时，请求被 `@validate_request("memory_id", "agent_id", "session_id", "user_input", "agent_response")` 拦截。

---

### TC-MS-009: 写入到不存在的 memory

**前置条件**：无

**步骤**：
1. 发送请求：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["nonexistent_memory_id"],
    "agent_id": "agent-x",
    "session_id": "session-x",
    "user_input": "test input",
    "agent_response": "test response"
  }'
```
2. 精确预期：HTTP 200，`code=500`，`message="Some messages failed to add. Detail:Memory not found."`
3. 通过唯一 agent/session 的只读前后计数证明未创建 raw 行、Task 或队列消息

**预期结果**：`_filter_accessible_memories()` 过滤掉不存在的 memory_id，返回空列表，`add_message()` 返回失败。

---

### TC-MS-010: 跨租户写入被拒绝

**前置条件**：T1 创建了 memory M1；T2 是另一个租户用户

**步骤**：
1. 以 T2 的身份发送请求写入 M1：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T2" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-cross",
    "session_id": "session-cross",
    "user_input": "cross tenant write",
    "agent_response": "should be rejected"
  }'
```
2. 精确预期：HTTP 200，`code=500`，`message="Some messages failed to add. Detail:Memory not found."`
3. 以唯一 agent/session 做两组各自 Message Store 的只读前后计数，M1 中无新消息行；metadata Task 也无对应记录

**预期结果**：`_filter_accessible_memories()` 基于 `_memory_accessible()` 检查，T2 对 M1 无权限，memory 被过滤掉。

---

### TC-MS-011: MERGE INTO Upsert 行为验证（相同 id 覆写）

**前置条件**：已创建专属 M1，并通过 API 写入一条 raw 消息、捕获实际 ID。本用例是明确的 adapter upsert 语义测试，允许在该专属行上做最小 adapter 写入

**步骤**：
1. 保存 API 原始行完整内容，使用当前组的 `settings.msgStoreConn.insert()` 对同一 `id='<M1>_<actual_message_id>'` 再写一次不同内容；不修改 Redis 序列，不伪称公开 API 可指定 message ID
2. 对照组预期 Infinity adapter 先按 id 删除再插入，实验组预期 GaussDB `MERGE ... WHEN MATCHED` 更新；两组都应只保留一行并读到新内容
3. 实验组数据库验证（对照组用 Infinity 只读 adapter 验证）：
```sql
SELECT id, content_ltks, message_id
  FROM ragflow_mem_<sha1>
 WHERE id = '<M1>_<actual_message_id>';
```
4. 验证只有一行，内容为最新写入的值（MERGE INTO WHEN MATCHED THEN UPDATE SET 生效）

**预期结果**：业务层统一实现同 id 覆写；实验组额外证明 `MERGE INTO` 命中 UPDATE。用例结束后恢复或删除专属 fixture，不能影响后续 size cache 判定。

---

### TC-MS-012: 验证 tokenized_content_ltks 分词一致性

**前置条件**：已创建 memory M1

**步骤**：
1. 写入消息：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{
    "memory_id": ["M1"],
    "agent_id": "agent-tokenize",
    "session_id": "session-tokenize",
    "user_input": "coriander is great for health",
    "agent_response": "Yes, coriander has many benefits"
  }'
```
2. 按唯一 agent/session 捕获 `<actual_message_id>`，实验组数据库验证：
```sql
SELECT content_ltks, tokenized_content_ltks
  FROM ragflow_mem_<sha1>
 WHERE id = '<M1>_<actual_message_id>';
```
3. 验证 `tokenized_content_ltks` 是 `fine_grained_tokenize(tokenize(content_ltks))` 的结果；对照组没有该物理列，改为用相同消息执行全文/融合 API 命中及 Infinity analyzer 只读证据
4. 在 Python 中验证：
```python
from rag.nlp.rag_tokenizer import fine_grained_tokenize, tokenize
expected = fine_grained_tokenize(tokenize(content_ltks_value))
assert tokenized_content_ltks_value == expected
```

**预期结果**：写入侧使用 `fine_grained_tokenize(tokenize())` 处理内容，保证与检索侧的分词方式一致。

---

## 3. 消息列表和最近消息 (Message List & Recent)

### TC-MS-100: GET /memories/{memory_id} 列出消息（默认参数）

**前置条件**：memory M1 中已有 3 条 raw 消息

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/memories/M1" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "messages": {
      "message_list": [...],
      "total_count": 3
    },
    "storage_type": "table"
  }
}
```
3. 验证：
   - `message_list` 包含 3 条 raw 消息
   - 每条消息有 `extract` 字段（提取类型消息列表，raw 类型时为 `[]`）
   - `total_count` = 3

**预期结果**：`list_message` 默认返回 raw 类型消息，按 `valid_at DESC` 排序。fixture 的 `valid_at` 必须可区分；若同秒写入，只验证时间非递增和集合完整，不能臆断同值行的顺序。

---

### TC-MS-101: GET /memories/{memory_id} 按 agent_id 过滤

**前置条件**：M1 中有 agent_a 和 agent_b 的消息各 2 条

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/memories/M1?agent_id=agent_a" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证返回的 `message_list` 只包含 agent_a 的消息

**预期结果**：agent_id 过滤生效，只返回匹配的消息。

---

### TC-MS-102: GET /memories/{memory_id} 按 keywords（session_id）过滤

**前置条件**：M1 中有 session_x 和 session_y 的消息

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/memories/M1?keywords=session_x" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证：list_message 中 `keywords` 参数被映射到 `filter_dict["session_id"]`，只返回 session_x 的消息

**预期结果**：`keywords` 参数实际是 `session_id` 的精确等值过滤，不是内容/名称模糊搜索；只返回 session_id 完全等于 `session_x` 的消息。

---

### TC-MS-103: GET /memories/{memory_id} 分页参数

**前置条件**：M1 中有 10 条 raw 消息

**步骤**：
1. 发送请求（第 1 页，每页 3 条）：
```bash
curl "${BASE_URL}/api/v1/memories/M1?page=1&page_size=3" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证：`message_list` 包含 3 条消息，`total_count` = 10
3. 发送请求（第 2 页）：
```bash
curl "${BASE_URL}/api/v1/memories/M1?page=2&page_size=3" \
  -H "Authorization: Bearer $JWT_T1"
```
4. 验证：`message_list` 包含 3 条不同的消息

**预期结果**：分页正确，offset = (page-1)*page_size。

---

### TC-MS-104: GET /messages 获取最近消息

**前置条件**：M1 中有若干消息

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&agent_id=agent_a&session_id=session_a&limit=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，返回最多 5 条消息
3. 验证：
   - 所有消息的 `agent_id` = `agent_a`
   - 所有消息的 `session_id` = `session_a`
   - 按 valid_at DESC 排序
   - 默认隐藏已遗忘（`forget_at IS NULL`）
   - 当前代码未在 `get_recent_messages()` 中自动追加 `status_int=1`，因此需单独记录停用消息是否仍返回

**预期结果**：get_recent_messages 按条件过滤并排序返回。

---

### TC-MS-105: GET /messages 默认隐藏已遗忘消息，但不自动过滤停用消息

**前置条件**：M1 中有 3 条消息，其中 1 条已被遗忘（forget_at 非 NULL），1 条已停用（status_int=0）

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=10" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证返回结果不包含已遗忘消息。
3. 验证停用消息是否返回：
   - 当前实现精确预期：`status_int=0` 且 `forget_at IS NULL` 的消息仍会返回。
   - 若公开契约要求 recent messages 只返回 active，则把该共同行为记录为契约缺陷；不能写成“可能”。
4. 数据库验证确认 3 条消息都存在：
```sql
SELECT message_id, status_int, forget_at
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw';
```

**预期结果**：`get_recent_messages()` 通过 `search(..., hide_forgotten=True)` 隐藏已遗忘消息；它不会像 `search_message()` 那样默认添加 `condition_dict["status"] = 1`。

---

### TC-MS-106: GET /messages 不传 memory_id 返回错误

**前置条件**：无

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，`{"code": 101, "message": "memory_ids is required."}`

**预期结果**：memory_id 为必填参数。

---

### TC-MS-107: GET /messages 多个 memory_id 扇出查询

**前置条件**：M1 和 M2 各有消息

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&memory_id=M2&limit=10" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证返回结果包含来自 M1 和 M2 的消息
3. 验证 `memory_id` 字段的值集合包含 M1 和 M2

**预期结果**：支持多个 memory_id 同时查询，结果合并后排序。

---

### TC-MS-108: GET /memories/{memory_id} 空结果

**前置条件**：memory M1 刚创建，无消息

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/memories/M1" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：
```json
{
  "code": 0,
  "data": {
    "messages": {
      "message_list": [],
      "total_count": 0
    },
    "storage_type": "table"
  }
}
```

**预期结果**：空 memory 返回空列表和 total_count=0；当前 Memory 元数据的 `storage_type` 返回 `"table"`，不区分底层 MySQL/GaussDB/Infinity 实现。

---

### TC-MS-109: GET /messages limit 无上界校验

**前置条件**：M1 中有多条消息

**步骤**：
1. 发送超大 limit：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=100000" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 发送非法类型 limit：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=abc" \
  -H "Authorization: Bearer $JWT_T1"
```

当前代码判定：
- `limit=int(args.get("limit", 10))` 没有上界校验。
- `limit=abc` 在 `try` 块外解析，当前会返回 HTTP 200 且响应体 `code=100`，错误信息包含 `ValueError`。

数据库验证：只读请求，不应修改 `ragflow_mem_<sha1>`。

对照组：`limit=10` 返回成功，结果数量不超过 10。

通过/失败判定：如果 API 契约要求统一参数错误响应或限制最大返回量，超大/非法 limit 未被标准化处理应登记为代码缺陷。

---

## 4. 消息检索 (Message Search)

### TC-MS-200: 英文全文候选参与融合检索

**前置条件**：M1 中有消息内容包含 "coriander is great for health"

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=coriander&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，返回包含 coriander 的消息
3. 验证返回结果中至少有一条消息的 content 包含 "coriander"

**预期结果**：公开 `/messages/search` 总是构造 text + dense + fusion 三个表达式，不是纯全文端点。对照组由 Infinity fulltext 生成候选；实验组通过 `plainto_tsquery('simple', ...)` 匹配 `to_tsvector('simple', tokenized_content_ltks)` 生成候选，再做向量阈值和融合排序；目标消息应进入结果。

---

### TC-MS-201: 全文检索中文文本

**前置条件**：M1 中有消息 "我喜欢在汤里放香菜"

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=香菜&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：返回包含"香菜"的消息

**预期结果**：中文消息进入公开融合检索结果；对照组用 Infinity analyzer，实验组用 RAG tokenizer 归一化后的 `simple` tsvector/UGIN 候选。两组要求语义命中，不要求内部 token 串或浮点分数逐字相同。

---

### TC-MS-202: 验证分词器一致性（写入侧与查询侧使用相同分词器）

**前置条件**：M1 中有消息 "coriander soup recipe"

**步骤**：
1. 验证写入侧分词：
```python
from rag.nlp.rag_tokenizer import fine_grained_tokenize, tokenize
write_result = fine_grained_tokenize(tokenize("coriander soup recipe"))
# 记录当前 tokenizer 的精确输出；不得硬编码臆测的词干
```
2. 验证查询侧分词（`normalize_fulltext_query`）：
```python
from memory.utils.gaussdb_conn import normalize_fulltext_query
query_result = normalize_fulltext_query("coriander")
# 预期: 使用 fine_grained_tokenize(tokenize("coriander")) 处理
```
3. 发送检索请求：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=coriander&similarity_threshold=0.0&keywords_similarity_weight=1.0&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
4. 验证能匹配到包含 coriander 的消息

**预期结果**：实验组写入侧 `fine_grained_tokenize(tokenize(content))` 和查询侧 `normalize_fulltext_query` 使用同一函数链，精确比较本次运行输出并证明可命中；不得把 `coriander→coriand` 等猜测写成固定 oracle。对照组用其原生 analyzer 验证等价语义命中。

---

### TC-MS-203: 向量检索 - 余弦距离验证

**前置条件**：M1 中有至少两条可区分消息，使用实际 Ollama `<embedding_dim>`；已取得查询向量

**步骤**：
1. 先发送公开融合请求，验证相关消息可返回；公开响应由 `MessageService.get_fields()` 投影，当前不包含 `_score`，不得要求 API 暴露内部 score：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=coriander&similarity_threshold=0.0&keywords_similarity_weight=0.0&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 再以只读 adapter 调用仅传 `MatchDenseExpr`（不传 text/fusion），读取原始 search result 的 `_score` 和顺序；这才是纯向量路径。
3. 实验组验证 `_score = 1 - (q_<embedding_dim>_vec <+> query_vec)`，并在浮点容差内与 SQL 重算一致；对照组用 Infinity 返回的 cosine similarity 做对应验证。
4. 实验组数据库验证向量值：
```sql
SELECT message_id, q_<embedding_dim>_vec, q_<embedding_dim>_vec_empty
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw';
```

**预期结果**：向量检索使用余弦距离 `<+>` 运算符，score = 1 - cosine_distance。

---

### TC-MS-204: 向量检索 - 空向量不参与匹配

**前置条件**：M1 中有 2 条真实消息。本用例是明确的向量空标记字段故障注入：实验组在专属行上经受控 adapter 把 `q_<embedding_dim>_vec` 置零并将 `q_<embedding_dim>_vec_empty=TRUE`；对照组另用唯一新 ID 尝试缺失 `content_embed` 的 native insert，预期写入被拒绝且不存在该 ID，避免臆造 Infinity 没有的 empty 列

**步骤**：
1. 通过只读 adapter 只传 `MatchDenseExpr` 执行纯向量查询；公开 API 即使 weight 为 0 仍构造全文候选，不能作为这个字段条件的唯一证据。
2. 实验组验证结果不包含 `q_<embedding_dim>_vec_empty=TRUE` 的消息。
3. 实验组数据库确认；对照组只读确认失败 fixture 没有残留行且原有两行可读：
```sql
SELECT message_id, q_<embedding_dim>_vec_empty
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1';
```

**预期结果**：实验组向量检索 SQL 以 `{empty_col} = FALSE` 过滤占位行；对照组不允许持久化缺向量消息且原数据不变。任一后端让业务空向量进入匹配或破坏既有行均失败。

---

### TC-MS-205: 融合检索（weighted_sum 文本+向量）

**前置条件**：M1 中有消息 "coriander is great for health"

**步骤**：
1. 发送请求（当前代码把该值构造成 weights=`0.3,0.7`）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=coriander&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证返回结果
3. 发送 0/1 边界请求比较排序，但注意公开路径仍同时构造全文和向量表达式，不能把它们命名为真正的“纯文本/纯向量”；真正单表达式由 TC-MS-203/204 的只读 adapter 调用覆盖：
```bash
# weight=1 边界
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=coriander&similarity_threshold=0.0&keywords_similarity_weight=1.0&top_n=5"
# weight=0 边界
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=coriander&similarity_threshold=0.0&keywords_similarity_weight=0.0&top_n=5"
```
4. API 不返回 `_score`；使用同参数的只读 adapter 原始结果验证当前实现分数为 `text_relevance * 0.3 + vector_similarity * 0.7`，并比较 API 返回顺序。

**预期结果**：实验组使用全文候选 CTE + weighted_sum；Infinity 用原生 weighted_sum。公开参数名/参考文档把 `keywords_similarity_weight` 描述为关键词相对语义的影响，但当前代码把它放在第二个（vector）权重；若 0/1 实测证明方向反转，登记共同 API 语义缺陷，不能为了迁就代码把参数解释成 vector weight。

---

### TC-MS-206: top_n 参数限制返回数量

**前置条件**：M1 中有 10 条消息

**步骤**：
1. 发送请求（top_n=3）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=3" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证返回最多 3 条消息
3. 分别发送 `top_n=0`、`top_n=-1`、`top_n=abc`。公开契约要求正整数：三者都应 HTTP 200 / `code=101` 且不执行检索。当前 0/负值可能退化为 10000，非整数在路由 try 外会成为 HTTP 200 / `code=100`；任一复现均登记参数校验缺陷。

**预期结果**：合法 `top_n=3` 最多返回 3；非法值应被参数层拒绝。实验组合法路径的 `effective_limit(limit, topn)` 取正数最小值，不能把其 10000 fallback 当成非法值的合理 API 行为。

---

### TC-MS-207: similarity_threshold 边界值测试

**前置条件**：M1 中有消息

**步骤**：
1. 测试 similarity_threshold=0（匹配所有非空相似度的结果）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0&keywords_similarity_weight=0.0&top_n=100"
```
2. 测试 similarity_threshold=1（几乎无结果，除非完全匹配）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=1.0&keywords_similarity_weight=0.0&top_n=100"
```
3. 测试 similarity_threshold=0.0（公开范围外）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=-1.0&keywords_similarity_weight=0.0&top_n=100"
```
4. 测试 similarity_threshold=2.0（公开范围外）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=2.0&keywords_similarity_weight=0.0&top_n=100"
```
5. 再发送 `similarity_threshold=abc`。
6. 判定：0/1 是合法边界；-1/2 应 HTTP 200 / `code=101` 拒绝，`abc` 也应 `code=101`。当前负值/超 1 值会下推，非数值在路由 try 外会成为 `code=100`；按公开 `[0,1]` 契约登记缺陷。若只读 adapter 内部测试需用 -1 放宽 SQL 阈值，可直接构造 MatchDenseExpr，但公开 API 用例不得借此绕过参数契约。

**预期结果**：合法边界下 `similarity_threshold` 作为 `1 - distance >= threshold` 的过滤条件；非法值由 API 参数层拒绝。

---

### TC-MS-208: keywords_similarity_weight 边界值

**前置条件**：M1 中有消息

**步骤**：
1. 测试 keywords_similarity_weight=1.0（按公开参数语义应偏向关键词）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=1.0&top_n=5"
```
2. 测试 keywords_similarity_weight=0.0（按公开参数语义应偏向向量）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=0.0&top_n=5"
```
3. 测试 keywords_similarity_weight=0.5（均衡融合）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=0.5&top_n=5"
```
4. 精确记录三个请求的返回顺序，并用只读 adapter 原始 score 复算。公开路径三个值都仍会建立 text+dense+fusion，0/1 只改变加权，不移除全文候选或向量阈值。
5. 发送 `keywords_similarity_weight=-0.1`、`1.1`、`abc`；公开 `[0,1]` 契约要求统一 `code=101`。当前范围外浮点会生成负权重/大于 1 权重，非数字在路由 try 外会返回 `code=100`，复现即为参数校验缺陷。

**预期结果**：`FusionExpr` 的格式是 `"text_weight,vector_weight"`，但当前 service 传入 `"1-keywords_weight,keywords_weight"`，所以代码实际把参数当 vector weight：1.0→向量、0.0→文本，和公开字段名/参考说明方向相反。若两组实测复现，登记 API 语义缺陷；0.5 应保持均衡。

---

### TC-MS-209: 跨多个 memory 检索

**前置条件**：M1 和 M2 各有包含 "guava" 的消息

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&memory_id=M2&query=guava&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证返回结果中 `memory_id` 集合包含 M1 和 M2

**预期结果**：search 方法支持多 index_names 和 memory_ids，结果合并排序。

---

### TC-MS-210: 检索带 agent_id/session_id/user_id 过滤

**前置条件**：M1 设为 team；两个已通过公开 tenant API 建立合法关系的用户分别用 JWT/session 写入可区分消息，使 `user_id` 为各自真实用户 ID。不能靠 body 伪造，也不依赖 TC-MS-001 正在验证的 API key 外部 subject 风险

**步骤**：
1. 发送请求（仅 agent_a）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&agent_id=agent_a&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证所有结果的 agent_id = agent_a
3. 发送请求（agent_a + session_a）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&agent_id=agent_a&session_id=session_a&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10"
```
4. 验证所有结果的 agent_id = agent_a 且 session_id = session_a
5. 发送请求（user_id 过滤）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&user_id=user_a&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10"
```
6. 验证所有结果的 user_id = user_a

**预期结果**：filter_dict 中的 agent_id、session_id、user_id 作为 WHERE 条件过滤。

---

### TC-MS-211: 检索默认隐藏已遗忘和不活跃消息

**前置条件**：M1 中有 3 条消息：A（正常）、B（已遗忘）、C（status_int=0）

**步骤**：
1. 发送请求（不指定 status）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证结果只包含消息 A
3. 对照数据库：
```sql
SELECT message_id, status_int, forget_at
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw';
```

**预期结果**：
- `search_message()` 中 `condition_dict["status"] = 1` 默认过滤不活跃消息
- `search()` 中 `hide_forgotten=True` 默认过滤已遗忘消息

---

### TC-MS-212: 空查询处理和缺失 query 参数

**前置条件**：M1 中有消息

**步骤**：
1. 发送请求（query 为空字符串）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=&similarity_threshold=0.2&keywords_similarity_weight=0.7&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 发送请求（不传 query）：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&similarity_threshold=0.2&keywords_similarity_weight=0.7&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
3. 数据库验证：
```sql
SELECT COUNT(*)
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1';
-- 预期：两次查询均不修改消息行数和字段
```

当前代码判定：
- `query=""` 会进入空字符串 embedding；`MsgTextQuery.question("")` 返回 `None`，adapter 最终走向量路径或后端对应行为，当前可能返回数据而非参数错误。安全契约要求非空，若成功则登记校验缺口。
- 缺失 `query` 时 `query=None`，`query.strip()` 会抛异常；当前返回 HTTP 200 且响应体 `code=100`，错误信息包含 `AttributeError`。

对照组：传入合法 `query=test`，预期业务成功且不修改 DB。

通过/失败判定：`query` 应作为必填且非空参数校验；缺失 `query` 未返回标准参数错误（`code=101`）应登记为代码缺陷。空字符串 query 的完整检索路径需要可用 embedding 模型。

---

### TC-MS-213: 全文检索特殊字符

**前置条件**：M1 中有消息内容包含 "100% discount on SELECT * FROM items"

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=discount+SELECT&similarity_threshold=0.0&keywords_similarity_weight=1.0&top_n=5" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证能返回包含特殊字符的消息
3. 验证不会导致 SQL 注入或解析错误

**预期结果**：`plainto_tsquery('simple', ...)` 安全处理特殊字符，参数化查询防止注入。

---

## 5. 消息状态更新 (Status Update)

### TC-MS-300: 设置消息状态为 false（停用）

**前置条件**：M1 中有一条已捕获 `<message_id>` 的消息，status_int=1

**步骤**：
1. 发送请求：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{"status": false}'
```
2. 预期响应：HTTP 200，`{"code": 0, "message": true}`
3. 数据库验证：
```sql
SELECT status_int
  FROM ragflow_mem_<sha1>
 WHERE memory_id = '<M1>' AND message_id = <message_id>;
```
4. 验证 `status_int` = 0

**预期结果**：`update_message_status()` → `MessageService.update_message(condition, {"status": False})` → `_build_update_set()` 将 `status` 映射为 `status_int`，值为 `0`。

---

### TC-MS-301: 设置消息状态为 true（重新激活）

**前置条件**：M1 中 `<message_id>` 的消息 status_int=0

**步骤**：
1. 发送请求：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{"status": true}'
```
2. 预期响应：HTTP 200
3. 数据库验证：`status_int` = 1

**预期结果**：状态从 0 恢复为 1。

---

### TC-MS-302: 验证 DB 中 status_int 值（1/0 映射）

**前置条件**：M1 中有一条消息

**步骤**：
1. 设置 status=false：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -d '{"status": false}'
```
2. 数据库验证：`status_int = 0`
3. 设置 status=true：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -d '{"status": true}'
```
4. 数据库验证：`status_int = 1`
5. 验证映射逻辑：
```python
# memory_api_service.py: "status": 1 if update_dict["status"] else 0
# _build_update_set: NUMERIC_COLUMNS 中 status_int 用 to_int_or_none
```

**预期结果**：布尔值 true→1、false→0 正确映射到 NUMBER(10) 类型。

---

### TC-MS-303: 不活跃消息从默认检索中隐藏

**前置条件**：M1 中有消息 A（status_int=1）和消息 B（status_int=0），两条都使用能被查询词 `test` 命中的内容，避免把“不命中”误当状态过滤

**步骤**：
1. 搜索消息：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10"
```
2. 验证结果只包含消息 A
3. 获取最近消息：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=10"
```
4. 验证 recent 结果：
   - 当前实现只保证隐藏已遗忘消息。
   - `status_int=0` 消息是否被返回应按 TC-MS-105 判定。

**预期结果**：`search_message()` 默认添加 `condition_dict["status"] = 1`，因此检索结果隐藏停用消息；`get_recent_messages()` 默认 `hide_forgotten=True`，但不自动过滤 `status_int=0`。

---

### TC-MS-304: 非布尔 status 值被拒绝

**前置条件**：M1 中有已捕获 `<message_id>`

**步骤**：
1. 发送请求（status 为字符串）：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{"status": "false"}'
```
2. 预期响应：HTTP 200，`{"code": 101, "message": "Status must be a boolean."}`
3. 发送请求（status 为数字）：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{"status": 1}'
```
4. 预期响应：HTTP 200，`{"code": 101, "message": "Status must be a boolean."}`

**预期结果**：`memory_api.py` 中 `if not isinstance(status, bool)` 检查拦截非布尔值。

---

### TC-MS-305: 跨租户状态更新被拒绝

**前置条件**：T1 创建了 memory M1（有消息），T2 是另一个租户

**步骤**：
1. 以 T2 身份发送请求：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T2" \
  -H "Content-Type: application/json" \
  -d '{"status": false}'
```
2. 预期响应：HTTP 200，`{"code": 404, "message": "Memory 'M1' not found."}`
3. 数据库验证：M1 中消息的 status_int 未改变

**预期结果**：`_require_memory_access()` 对 T2 抛出 NotFoundException。

---

### TC-MS-306: 更新不存在的消息状态

**前置条件**：M1 存在，并已只读证明 `<nonexistent_message_id>` 不存在

**步骤**：
1. 发送请求：
```bash
curl -X PUT "${BASE_URL}/api/v1/messages/<M1>:<nonexistent_message_id>" \
  -H "Authorization: Bearer $JWT_T1" \
  -H "Content-Type: application/json" \
  -d '{"status": false}'
```
2. 安全契约：HTTP 200 / `code=404`，明确指出消息不存在；不应返回 `code=0`。

**预期结果**：UPDATE WHERE 条件不匹配任何行时必须对 API 表现为失败；如果底层 adapter 返回成功但无实际变更，API 层应补充影响行数/存在性校验。

代码审查判定（仍须本批次实测）：GaussDB adapter 在表存在且 SQL 无异常时直接返回 True，Infinity adapter 也不检查受影响行数，因此当前两组应返回 HTTP 200 / `code=0`。仍按上述安全契约判失败并登记共同缺陷，不能把伪成功改成通过预期。

---

## 6. 消息遗忘 (Message Forget)

### TC-MS-400: 遗忘一条消息

**前置条件**：M1 中有 `<message_id>` 的消息，forget_at IS NULL

**步骤**：
1. 发送请求：
```bash
curl -X DELETE "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，`{"code": 0, "message": true}`
3. 数据库验证：
```sql
SELECT forget_at
  FROM ragflow_mem_<sha1>
 WHERE memory_id = '<M1>' AND message_id = <message_id>;
```
4. 验证 `forget_at` IS NOT NULL，且为当前时间附近的 TIMESTAMP

**预期结果**：`forget_message()` 调用 `MessageService.update_message(condition, {"forget_at": forget_time})`，设置 `forget_at = timestamp_to_date(current_timestamp())`。

---

### TC-MS-401: 验证遗忘后 forget_at 在 DB 中的值

**前置条件**：M1 中有另一条已捕获 `<message_id>`

**步骤**：
1. 记录当前时间 T_before
2. 发送遗忘请求：
```bash
curl -X DELETE "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1"
```
3. 记录当前时间 T_after
4. 数据库验证：
```sql
SELECT forget_at
  FROM ragflow_mem_<sha1>
 WHERE memory_id = '<M1>' AND message_id = <message_id>;
```
5. 验证 T_before <= forget_at <= T_after

**预期结果**：forget_at 值为遗忘操作发生时的时间戳。

---

### TC-MS-402: 已遗忘消息从默认检索中隐藏

**前置条件**：M1 中有消息 A（正常）和消息 B（已遗忘），两条都使用能被查询词 `test` 命中的内容

**步骤**：
1. 搜索消息：
```bash
curl "${BASE_URL}/api/v1/messages/search?memory_id=M1&query=test&similarity_threshold=0.0&keywords_similarity_weight=0.7&top_n=10"
```
2. 验证结果不包含消息 B
3. 获取最近消息：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=10"
```
4. 验证结果不包含消息 B

**预期结果**：`hide_forgotten=True`（默认）使 WHERE 条件包含 `forget_at IS NULL`。

---

### TC-MS-403: 已遗忘消息在显式查询中可见（list_message 使用 hide_forgotten=False）

**前置条件**：M1 中有消息 A（正常）和消息 B（已遗忘）

**步骤**：
1. 通过 GET /memories/{memory_id} 查看：
```bash
curl "${BASE_URL}/api/v1/memories/M1" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 验证：`list_message()` 中 `hide_forgotten=False`，所以返回结果包含已遗忘的消息 B
3. 数据库确认消息 B 仍然存在：
```sql
SELECT message_id, forget_at
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND message_type_kwd = 'raw';
```

**预期结果**：`MessageService.list_message()` 调用 `search()` 时传入 `hide_forgotten=False`，已遗忘消息在管理视图中可见。

---

### TC-MS-404: 跨租户遗忘被拒绝

**前置条件**：T1 创建了 M1（有消息），T2 是另一个租户

**步骤**：
1. 以 T2 身份发送请求：
```bash
curl -X DELETE "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T2"
```
2. 预期响应：HTTP 200，`{"code": 404, "message": "Memory 'M1' not found."}`
3. 数据库验证：消息的 forget_at 仍为 NULL

**预期结果**：`_require_memory_access()` 对 T2 抛出 NotFoundException。

---

### TC-MS-405: 重复遗忘同一消息

**前置条件**：M1 中有已捕获 `<message_id>` 的消息

**步骤**：
1. 第一次遗忘：
```bash
curl -X DELETE "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，成功
3. 记录第一次的 forget_at 值
4. 等待跨过存储时间精度边界（约 1.1 秒）后第二次遗忘同一消息：
```bash
curl -X DELETE "${BASE_URL}/api/v1/messages/<M1>:<message_id>" \
  -H "Authorization: Bearer $JWT_T1"
```
5. 预期响应：HTTP 200，成功（幂等操作）
6. 数据库验证：forget_at 更新为第二次操作的时间

**预期结果**：重复操作保持“已遗忘”状态且不新增行；当前实现会把 `forget_at` 更新为第二次操作时间。若时间仍相同，先检查是否未跨秒，不能直接误判 adapter 未更新。

---

### TC-MS-406: 遗忘不存在的消息

**前置条件**：M1 存在，并已只读证明 `<nonexistent_message_id>` 不存在

**步骤**：
1. 发送请求：
```bash
curl -X DELETE "${BASE_URL}/api/v1/messages/<M1>:<nonexistent_message_id>" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 安全契约：HTTP 200 / `code=404`，明确指出消息不存在；不应返回 `code=0`。

**预期结果**：UPDATE WHERE 不匹配任何行时必须对 API 表现为失败；如果底层 adapter 返回成功但无实际变更，API 层应补充影响行数/存在性校验。

代码审查判定与 TC-MS-306 相同：在表存在、SQL 无异常时当前两组应在零命中仍返回 True，导致 HTTP 200 / `code=0`；实测复现即登记接口伪成功缺陷。

---

## 7. 消息内容获取 (Content Retrieval)

### TC-MS-500: 获取消息内容

**前置条件**：M1 中有已捕获 `<message_id>` 的消息，内容为 "User Input: hello\nAgent Response: hi there"

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<message_id>/content" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "message": true,
  "data": {
    "id": "<actual_M1>_12345",
    "message_id": 12345,
    "content": "User Input: hello\nAgent Response: hi there",
    "content_embed": [...],
    ...
  }
}
```
上例 `12345` 仅展示 JSON number 类型，实际断言必须使用本用例捕获的 ID。验证 `data.content` 包含原始内容。

**预期结果**：`get_message_content()` → `MessageService.get_by_message_id()`，两组都以 `id='{memory_id}_{message_id}'` 精确读取；实验组进入 `GaussDBMemoryConnection.get()`，对照组进入 Infinity adapter。API 结果不得串 Memory。

---

### TC-MS-501: 跨租户获取消息内容被拒绝

**前置条件**：T1 创建了 M1，T2 是另一个租户

**步骤**：
1. 以 T2 身份发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<message_id>/content" \
  -H "Authorization: Bearer $JWT_T2"
```
2. 预期响应：HTTP 200，`{"code": 404, "message": "Memory 'M1' not found."}`

**预期结果**：`_require_memory_access()` 拦截跨租户访问。

---

### TC-MS-502: 获取不存在的消息内容

**前置条件**：M1 存在，并已只读证明 `<nonexistent_message_id>` 不存在

**步骤**：
1. 发送请求：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<nonexistent_message_id>/content" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，`code=404`，message 精确包含该 `<nonexistent_message_id>` 和 `<M1>`

**预期结果**：`get_by_message_id()` 返回 None，`get_message_content()` 抛出 NotFoundException。

---

### TC-MS-503: 获取已遗忘/不活跃消息的内容

**前置条件**：M1 中 `<forgotten_message_id>` 已遗忘，`<inactive_message_id>` 已停用

**步骤**：
1. 获取已遗忘消息的内容：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<forgotten_message_id>/content" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：HTTP 200，返回消息内容（get 方法不过滤 forget_at 和 status_int）
3. 获取不活跃消息的内容：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<inactive_message_id>/content" \
  -H "Authorization: Bearer $JWT_T1"
```
4. 预期响应：HTTP 200，返回消息内容

**预期结果**：`get()` 方法通过 `id` 主键直接查询，不附加 forget_at 或 status_int 过滤条件。已遗忘/不活跃消息的内容仍可通过精确 ID 获取。

---

## 8. Memory 内数据隔离 (Data Isolation)

### TC-MS-600: 同一租户两个 memory 的操作互不影响

**前置条件**：T1 创建了 M1 和 M2

**步骤**：
1. 向 M1 写入消息：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -d '{"memory_id": ["M1"], "agent_id": "a1", "session_id": "s1", "user_input": "M1 content", "agent_response": "M1 response"}'
```
2. 向 M2 写入消息：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -d '{"memory_id": ["M2"], "agent_id": "a2", "session_id": "s2", "user_input": "M2 content", "agent_response": "M2 response"}'
```
3. 查询 M1 的消息：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=10"
```
4. 验证：只包含 M1 的消息，不包含 M2 的
5. 查询 M2 的消息：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M2&limit=10"
```
6. 验证：只包含 M2 的消息
7. 数据库验证：
```sql
SELECT memory_id, content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id IN ('M1', 'M2')
 ORDER BY memory_id, message_id;
```
8. 实验组验证两行在同一物理表中且 `memory_id` 分别为 M1/M2；对照组验证两个独立 Infinity memory 表各自只含自己的行

**预期结果**：两组 API 数据都隔离；Infinity 以 per-memory 表隔离，GaussDB 以 tenant 共享表内 `memory_id` 强制条件隔离。

---

### TC-MS-601: 跨租户数据隔离

**前置条件**：T1 创建了 M1（有消息），T2 创建了 M2（有消息）

**步骤**：
1. T1 尝试查询 M2：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M2&limit=10" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 预期响应：返回空列表（M2 不在 T1 的可访问列表中）
3. T2 尝试查询 M1：
```bash
curl "${BASE_URL}/api/v1/messages?memory_id=M1&limit=10" \
  -H "Authorization: Bearer $JWT_T2"
```
4. 预期响应：返回空列表
5. 存储验证：对照组表名同时包含各 tenant/memory 逻辑 ID；实验组 `index_name(T1) != index_name(T2)`，SHA1 后物理表不同

**预期结果**：不同租户的 `index_name` 不同，物理表不同，天然隔离。即使通过 API 传入对方的 memory_id，`_filter_accessible_memories()` 也会过滤掉。

---

### TC-MS-602: 删除 memory A 只清理 A 的行，不影响 B

**前置条件**：T1 的 M1 和 M2 各有消息；实验组二者共享 tenant 物理表，对照组是两个 Infinity 表

**步骤**：
1. 删除 M1：
```bash
curl -X DELETE "${BASE_URL}/api/v1/memories/M1" \
  -H "Authorization: Bearer $JWT_T1"
```
2. 数据库验证：
```sql
-- M1 的行应被删除
SELECT COUNT(*) FROM ragflow_mem_<sha1> WHERE memory_id = 'M1';
-- 预期: 0

-- M2 的行应仍然存在
SELECT COUNT(*) FROM ragflow_mem_<sha1> WHERE memory_id = 'M2';
-- 预期: > 0
```
3. 验证 M2 的消息仍可通过 API 正常查询

**预期结果**：`delete_memory()` 只清 M1 数据且不影响 M2。实验组额外证明共享表上的 DELETE 强制带 M1 边界；对照组证明只修改 M1 的独立 Infinity 表。

---

### TC-MS-603: 不同 memory 中相同 message_id 的行独立存在

**前置条件**：全局 Redis 序列不会通过正常 API 自然产生相同数值 ID。本用例是明确的隔离碰撞注入：先经 API 创建两条合法专属消息，再通过当前组受控 adapter 在 M1/M2 构造相同 `<collision_message_id>`；禁止修改 Redis 序列，记录并清理 fixture

**步骤**：
1. 数据库验证：
```sql
SELECT id, memory_id, message_id, content_ltks
  FROM ragflow_mem_<sha1>
 WHERE message_id = <collision_message_id> AND memory_id IN ('M1', 'M2');
```
2. 实验组验证共享表有两行，id 分别为 `M1_<collision_message_id>` 和 `M2_<collision_message_id>`；对照组在两个 Infinity 表分别验证一行
3. 获取 M1 的消息内容：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<collision_message_id>/content"
```
4. 获取 M2 的消息内容：
```bash
curl "${BASE_URL}/api/v1/messages/<M2>:<collision_message_id>/content"
```
5. 验证返回各自独立的内容

**预期结果**：复合字符串主键 `id='{memory_id}_{message_id}'` 与 API 按 memory 定位共同保证碰撞 ID 不串读；用例后删除专属碰撞行。

---

## 9. 向量维度管理 (Vector Dimension)

### TC-MS-700: 首次写入创建向量列 q_{dim}_vec

**前置条件**：memory M1 刚通过 API 创建且从未写消息；实验组 tenant 物理表尚不存在（若同 tenant 其它 Memory 已写入，则本用例必须改用专属新 tenant）

**步骤**：
1. 用真实 Ollama 模型经 API 写入消息，记录实际 `<embedding_dim>`：
```bash
curl -X POST ${BASE_URL}/api/v1/messages \
  -d '{"memory_id": ["M1"], "agent_id": "a", "session_id": "s", "user_input": "test", "agent_response": "response"}'
```
2. 等待写入完成
3. 数据库验证：
```sql
SELECT column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = '<schema>'
   AND table_name = 'ragflow_mem_<sha1>'
   AND column_name LIKE 'q_%'
 ORDER BY column_name;
```
4. 实验组预期列：
   - `q_<embedding_dim>_vec` (`floatvector(<embedding_dim>)`)
   - `q_<embedding_dim>_vec_empty` (`boolean`，该行值为 FALSE)
5. 对照组执行同一 API，验证独立 Infinity M1 表含 `q_<embedding_dim>_vec` 和原生 HNSW 索引；Infinity 没有 `*_empty` 列，不把该结构差异判成失败

**预期结果**：首次 raw 写入同时创建当前后端的 message 表和向量结构；实验组 `create_idx()` 通过 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 创建向量列及 empty 标记。

---

### TC-MS-701: 使用不同维度写入新增向量列

**前置条件**：专属 adapter fixture 已有第一维度 `<dim_a>`。公开 API 在非空 Memory 上禁止切换 embedding，因此本用例只测试存储 adapter 的动态维度能力，不伪装成业务 API 场景

**步骤**：
1. 对照组先通过 Infinity adapter 以唯一新 ID 向同一 M1 尝试写入受控 `<dim_b>` 向量：当前 per-memory 固定 schema 预期受控拒绝，新 ID 不残留，原 `<dim_a>` 行/索引保持可读；完整记录异常，不能跳过
2. 实验组通过 GaussDB adapter 向同一 tenant 表写入唯一 ID 的 `<dim_b>` 向量
3. 实验组数据库验证：
```sql
SELECT column_name
  FROM information_schema.columns
 WHERE table_schema = '<schema>'
   AND table_name = 'ragflow_mem_<sha1>'
   AND column_name LIKE 'q_%'
 ORDER BY column_name;
```
4. 预期列：
   - `q_<dim_a>_vec`, `q_<dim_a>_vec_empty`（原有）
   - `q_<dim_b>_vec`, `q_<dim_b>_vec_empty`（新增）

**预期结果**：新维度的写入触发 `_ensure_vector_column_exists()` 添加新列，不影响旧列。

---

### TC-MS-702: 同 id 跨维度覆写后旧维度标记 empty=TRUE

**前置条件**：实验组专属表已有 `<dim_a>` 和 `<dim_b>` 两套列；保存一条 `<dim_a>` 行的完整内容

**步骤**：
1. 用 GaussDB adapter 以相同 `id` 写入 `<dim_b>` 向量，触发 MERGE 覆写；这是一条明确的 adapter fixture 写入
2. 实验组数据库验证：
```sql
SELECT message_id,
       q_<dim_a>_vec, q_<dim_a>_vec_empty,
       q_<dim_b>_vec, q_<dim_b>_vec_empty
  FROM ragflow_mem_<sha1>
 WHERE id = '<M1>_<actual_message_id>';
```
3. 验证：
   - `q_<dim_b>_vec` 为真实向量且 `q_<dim_b>_vec_empty = FALSE`
   - `q_<dim_a>_vec` 为对应维度零向量且 `q_<dim_a>_vec_empty = TRUE`
4. 对照组执行同 id 跨维度 adapter 操作，安全预期是固定维度校验在删除旧行前拒绝、旧行不损坏。代码审查显示 Infinity 当前先按 id delete 再 insert，未知 `<dim_b>` 列失败时可能丢旧行；若实测丢失，登记破坏性 upsert 缺陷，而不是接受为设计差异

**预期结果**：实验组 `_build_merge_sql()` 的 `reset_other_dims` 将非当前维度重置为零并设 `empty=TRUE`；用例后清理专属 fixture。

---

### TC-MS-703: 向量检索只命中 empty=FALSE 的行

**前置条件**：实验组 M1 中另建 A（目标维度真实向量/`empty=FALSE`），并复用 TC-MS-702 产生的 B（同维度零向量/`empty=TRUE`）；对照组只有 native 有效向量行，缺向量新 ID 已按 TC-MS-204 被拒绝

**步骤**：
1. 通过只读 adapter 仅传对应维度 `MatchDenseExpr`，避免公开融合 API 的全文候选干扰
2. 实验组验证结果包含 A、不包含 B；对照组验证检索只含 native 有效行且失败 fixture 不存在，不要求存在 empty 列
3. 数据库确认：
```sql
SELECT message_id, q_<dim>_vec_empty FROM ragflow_mem_<sha1> WHERE memory_id = 'M1';
```

**预期结果**：实验组向量检索 SQL 中 `{empty_col} = FALSE` 过滤占位行。

---

### TC-MS-704: get_fields 只返回真实向量的 content_embed

**前置条件**：在实验组专属行上最后一次用 `<dim_a>` 覆写，使 `<dim_a>` 真实（empty=FALSE）、`<dim_b>` 为零占位（empty=TRUE）；记录实际 ID

**步骤**：
1. 通过 API 获取消息（包含 content_embed 字段）：
```bash
curl "${BASE_URL}/api/v1/messages/<M1>:<actual_message_id>/content"
```
2. 验证返回 `content_embed` 是 `<dim_a>` 的非空向量
3. 验证不是 `<dim_b>` 的零向量；对照组用原生固定维度行验证同一 API 字段

**预期结果**：实验组 `_content_embed_from_row()` 只选择 `empty is False` 的维度；若故障 fixture 造成多个维度同时非空，当前实现记录 warning 并取最大维度。

---

### TC-MS-705: 向量列 DDL 幂等性

**前置条件**：M1 已有当前真实 `<embedding_dim>` 向量结构

**步骤**：
1. 多次经 API 写入相同真实维度消息；另在专属 adapter fixture 重复调用相同维度的 create/ensure
2. 验证不出现 DDL 错误
3. 数据库验证：
```sql
SELECT COUNT(*)
  FROM information_schema.columns
 WHERE table_schema = '<schema>'
   AND table_name = 'ragflow_mem_<sha1>'
   AND column_name = 'q_<embedding_dim>_vec';
```
4. 预期：COUNT = 1（不会重复创建）

**预期结果**：实验组 `ADD COLUMN IF NOT EXISTS` 保证单列；对照组重复 create/write 由 Infinity `ConflictType.Ignore` 保证单表/单向量索引。两组都不得丢既有消息。

---

## 10. 启动维护 (Startup Maintenance)

本节是 Redis/启动状态故障与维护测试的明确例外。只能操作当前组独立 Redis DB 中的本批次 key；先停止该组 API/worker 防止并发分配，再做最小 DEL/SET，运行一次性初始化或重启，取证后恢复正常服务。`redis-cli` 必须显式使用当前组 host/port/DB/认证，禁止连接默认 DB，也不得在两组之间复制 key/value。

### TC-MS-800: init_message_id_sequence - Redis 种子值等于 DB max message_id

**前置条件**：当前组 Redis 中无 `id_generator:memory` key，当前组 Message Store 中有多条消息；已停掉该组所有 ID 生产者

**步骤**：
1. 删除 Redis key：
```bash
<redis_cli_group> DEL id_generator:memory
```
2. 通过 `MessageService.get_max_message_id()` 及后端只读证据记录当前组所有 Memory 的最大 message_id；实验组再执行物理 SQL：
```sql
-- 枚举当前 schema 中本批次所有 ragflow_mem_* tenant 表并分别取 MAX，
-- 再在 runner 中取全局最大值；不得只查任意一张表
SELECT MAX(message_id) FROM <each_ragflow_mem_table>;
```
3. 重启 RAGFlow 服务（触发 `init_data.py` → `init_message_id_sequence()`）
4. 验证 Redis 中的值：
```bash
<redis_cli_group> GET id_generator:memory
```
5. 隔离环境无并发写入，预期 Redis 值精确等于 Message Store max message_id；随后生成的新 ID 必须为 max+1 且不碰撞

**预期结果**：`init_message_id_sequence()` 检查 Redis key 是否存在，不存在时通过 `MessageService.get_max_message_id()` 获取 DB 中的最大值并设置为种子。

---

### TC-MS-801: init_message_id_sequence - Redis key 已存在时跳过

**前置条件**：先只读取得当前 Message Store max message_id，选择安全种子 `<existing_seed> = max + 100`；已停掉该组 ID 生产者

**步骤**：
1. 在当前组专用 Redis DB 设置 key：
```bash
<redis_cli_group> SET id_generator:memory <existing_seed>
```
2. 重启 RAGFlow 服务
3. 验证 Redis 值不变：
```bash
<redis_cli_group> GET id_generator:memory
```
4. 预期：仍为 `<existing_seed>`（不重新初始化），不得用可能小于 DB max 的固定 500 制造碰撞

**预期结果**：`init_message_id_sequence()` 中 `REDIS_CONN.exist(message_id_redis_key)` 为 True 时跳过初始化，保留现有值。

---

### TC-MS-802: init_memory_size_cache - 缓存与 DB 实际大小一致

**前置条件**：Redis 中无 `memory_{memory_id}` key，M1 中有消息

**步骤**：
1. 删除 Redis key：
```bash
<redis_cli_group> DEL memory_M1
```
2. 重启 RAGFlow 服务（触发 `init_memory_size_cache()`）
3. 验证 Redis 中的值：
```bash
<redis_cli_group> GET memory_M1
```
4. 预期：值 > 0，反映 M1 中消息的实际大小
5. 用与产品相同的 Python 运行时逐行复算：`sys.getsizeof(content) + sys.getsizeof(content_embed[0]) * len(content_embed)`，包含该 Memory 的全部 raw/extract 行；与缓存精确比较。不能用字符串长度或向量维度相加替代 `sys.getsizeof`

**预期结果**：`init_memory_size_cache()` 遍历所有 memory，通过 `get_memory_size_cache()` → `MessageService.calculate_memory_size()` 计算并缓存。

---

### TC-MS-803: get_forgotten_messages - 返回已遗忘消息

**前置条件**：M1 中有 2 条消息，其中 1 条已被遗忘

**步骤**：
1. 通过当前组 `settings.msgStoreConn` 调用 `get_forgotten_messages`；对照组返回 DataFrame，实验组返回 SearchResult，统一再用各 adapter 的 `get_fields()` 规范化，不直接假设 `.total`：
```python
from common import settings
conn = settings.msgStoreConn
result = conn.get_forgotten_messages(
    select_fields=["message_id", "content", "content_embed", "forget_at"],
    index_name=index_name(tenant_id),
    memory_id="M1",
    limit=512
)
```
2. 验证规范化结果恰有 1 条、message_id 为实际遗忘行且所选 `forget_at` 非空；若将来删掉该 select field，结果不会凭空包含它
3. 实验组数据库验证；对照组用 Infinity 只读 adapter 核对：
```sql
SELECT message_id, forget_at
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND forget_at IS NOT NULL;
```

**预期结果**：`get_forgotten_messages()` 通过 `WHERE memory_id = %s AND forget_at IS NOT NULL ORDER BY forget_at ASC` 查询已遗忘消息。

---

### TC-MS-804: get_missing_field_message - 返回缺少指定字段的消息

**前置条件**：本用例是明确的缺字段故障注入。实验组在专属消息上通过受控 adapter 将 `tokenized_content_ltks` 置 NULL；对照组执行同名 adapter 方法，当前 `equivalent_condition_to_str()` 应因该列不在 Infinity schema 而抛 AssertionError，原生数据保持不变；该受控不支持结果也必须保存，不跳过

**步骤**：
1. 通过当前组 adapter 调用 `get_missing_field_message`；实验组示例：
```python
result = conn.get_missing_field_message(
    select_fields=["message_id", "content"],
    index_name=index_name(tenant_id),
    memory_id="M1",
    field_name="tokenized_content_ltks",
    limit=512
)
```
2. 实验组验证只返回专属缺字段消息；对照组记录原生 analyzer/schema 的差异且不得破坏已有数据
3. 实验组数据库验证：
```sql
SELECT message_id, tokenized_content_ltks
  FROM ragflow_mem_<sha1>
 WHERE memory_id = 'M1' AND tokenized_content_ltks IS NULL;
```

**预期结果**：实验组 `get_missing_field_message()` 使用 `WHERE memory_id = %s AND {db_field} IS NULL` 找到该行。当前 `fix_missing_tokenized_memory()` 仅在 `DOC_ENGINE=elasticsearch` 时运行，Infinity 和 GaussDB 启动都会直接跳过，因此本用例只验证 adapter 扫描能力，不得声称重启会自动修复；用例后通过 adapter 更新原 `content` 触发重新分词并只读确认恢复。

---

### TC-MS-805: init_message_id_sequence - 无 memory 时种子为 1

**前置条件**：在当前组专用新库/Redis DB 中，通过公开 API 删除本批次全部 Memory 并以 metadata 只读查询确认总数为 0；已停止该组服务

**步骤**：
1. 不直接 DELETE metadata 表；确认 API 清理完成且 Message Store 无残留本批次行
2. 在当前组专用 Redis DB 删除 `id_generator:memory`
3. 重启 RAGFlow 服务
4. 验证 Redis 值：
```bash
<redis_cli_group> GET id_generator:memory
```
5. 预期：值为 1

**预期结果**：`init_message_id_sequence()` 中 `exist_memory_list` 为空时，设置 `max_id = 1`。

---

## 11. 物理表和索引验证 (Physical Table & Index)

本节每个用例仍按“对照组先、实验组后”执行，但 catalog oracle 按后端区分：

| 用例 | Infinity 对照组必须执行的等价证明 | GaussDB 实验组物理证明 |
|---|---|---|
| TC-MS-900 | 表名 `memory_<prefix?>_<tenant>_<memory>`，每 Memory 一表 | tenant `index_name` 的 SHA1 前 32 位表名 |
| TC-MS-901 | `message_infinity_mapping.json` 字段 + 当前维度向量列 | `BASE_COLUMNS` + 动态 vector/empty 列、USTORE |
| TC-MS-902 | 当前 mapping 生成的 HNSW + 两个 fulltext 索引 | 7 个常规索引 + PK |
| TC-MS-903 | `content` 的 `rag-coarse/rag-fine` fulltext 索引 | tokenized expression UGIN |
| TC-MS-904 | 当前向量列的 `q_vec_idx` HNSW/cosine | 当前维度 gsdiskann/cosine |
| TC-MS-905 | `ConflictType.Ignore` 重复 create | `IF NOT EXISTS` + advisory lock 重复 create |
| TC-MS-906 | 只删除专属 M1 的独立表 | 删除专属 tenant 的整张共享表 |
| TC-MS-907 | `index_exist` 当前只证明表存在 | 同时验证基础列与 regular+UGIN 索引完整 |
| TC-MS-908 | 并发 create 的 conflict-ignore/meta retry | 事务 advisory lock + 幂等 DDL |

所有破坏索引/删除表/并发首次 DDL 都使用专属 tenant 与专属 Memory，先保存 catalog 只读证据，结束后通过正常 create 路径重建或删除 fixture；绝不在承载其它用例数据的 tenant 表上执行。

### TC-MS-900: 物理表名格式为 ragflow_mem_<sha1>

**前置条件**：已创建专属 memory M1（tenant_id=T1）并至少写入一条消息；仅创建 metadata 不会创建 Message Store 表

**步骤**：
1. 计算预期的物理表名：
```python
import hashlib
index = f"memory_{T1}"  # 或带 ES_INDEX_PREFIX
digest = hashlib.sha1(index.encode("utf-8")).hexdigest()[:32]
expected_table = f"ragflow_mem_{digest}"
```
2. 数据库验证：
```sql
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = '<schema>'
   AND table_name = '<expected_table>';
```
3. 验证表存在，且同 tenant 的 M2（若有）映射到同一张表
4. 对照组按上表计算/读取 Infinity 的 per-memory 表名并验证存在，不执行 SHA1 oracle

**预期结果**：实验组 `physical_table_name()` 使用 SHA1 前 32 位；对照组使用 Infinity 原生逻辑表名。两者都必须来自本批次实际 tenant/memory ID。

---

### TC-MS-901: 基础表包含所有必要列

**前置条件**：M1 的物理表已创建

**步骤**：
1. 数据库验证：
```sql
SELECT column_name, data_type, is_nullable
  FROM information_schema.columns
 WHERE table_schema = '<schema>'
   AND table_name = 'ragflow_mem_<sha1>'
 ORDER BY ordinal_position;
```
2. 验证包含以下列：
   - `id` VARCHAR2(96) NOT NULL（主键）
   - `message_id` NUMBER(19) NOT NULL
   - `message_type_kwd` VARCHAR2(64)
   - `source_id` NUMBER(19)
   - `memory_id` VARCHAR2(32) NOT NULL
   - `user_id` VARCHAR2(64)
   - `agent_id` VARCHAR2(64)
   - `session_id` VARCHAR2(128)
   - `zone_id` NUMBER(10) DEFAULT 0
   - `valid_at` TIMESTAMP
   - `invalid_at` TIMESTAMP
   - `forget_at` TIMESTAMP
   - `status_int` NUMBER(10) DEFAULT 1 NOT NULL
   - `content_ltks` TEXT
   - `tokenized_content_ltks` TEXT

3. 对照组通过 Infinity `show_columns()` 与当前 mapping 核对原生列和实际维度向量列，不要求 `content_ltks/tokenized_content_ltks/*_empty` 这些 GaussDB 专属物理名。

**预期结果**：实验组包含全部 `BASE_COLUMNS`、当前向量/empty 列并使用 USTORE；对照组 schema 完整且 API 字段映射等价。

---

### TC-MS-902: 基础索引全部存在

**前置条件**：M1 的物理表已创建

**步骤**：
1. 数据库验证：
```sql
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = '<schema>'
   AND tablename = 'ragflow_mem_<sha1>';
```
2. 验证包含以下索引（`REGULAR_INDEXES` 中定义的 7 个）：
   - `message_id` 上的索引
   - `memory_id` 上的索引
   - `message_type_kwd` 上的索引
   - `source_id` 上的索引
   - `(agent_id, session_id)` 复合索引
   - `(status_int, valid_at)` 复合索引
   - `forget_at` 上的索引
3. 验证主键索引存在

4. 对照组用 Infinity `list_indexes()` 核对当前 mapping 生成的 `q_vec_idx` HNSW 及 `content` 的两个 fulltext 索引；该 mapping 没有 secondary `index_type`，不凭空要求 secondary 索引，也不要求 7 个 GaussDB 索引。

**预期结果**：实验组 `build_regular_index_ddls()` 创建 7 个常规索引并有 PK；对照组原生必要索引完整。

---

### TC-MS-903: UGIN 全文索引存在

**前置条件**：M1 的物理表已创建

**步骤**：
1. 数据库验证：
```sql
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = '<schema>'
   AND tablename = 'ragflow_mem_<sha1>'
   AND indexdef LIKE '%ugin%';
```
2. 验证：
   - 索引名包含 `tokenized_ugin`
   - indexdef 包含 `USING ugin (to_tsvector('simple', tokenized_content_ltks))`

3. 对照组验证 `content` 上 `rag-coarse` 与 `rag-fine` 对应全文索引存在，不查询 UGIN。

**预期结果**：两组都有可用全文索引；实验组额外精确证明 `'simple'` tokenized expression UGIN。

---

### TC-MS-904: gsdiskann 向量索引存在

**前置条件**：M1 已用真实 Ollama 模型写入消息，维度为 `<embedding_dim>`

**步骤**：
1. 数据库验证：
```sql
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE schemaname = '<schema>'
   AND tablename = 'ragflow_mem_<sha1>'
   AND indexdef LIKE '%gsdiskann%';
```
2. 验证：
   - 索引名包含 `q_<embedding_dim>_vec_diskann`
   - indexdef 包含 `USING gsdiskann (q_<embedding_dim>_vec cosine)`
3. 对照组验证 `q_vec_idx` 是当前维度向量列上的 HNSW/cosine 索引，不查询 gsdiskann。

**预期结果**：`build_diskann_index_ddl()` 创建 gsdiskann 索引，使用 cosine 距离度量。`_create_diskann_index_with_retry()` 在 `maintenance_work_mem` 不足时逐步增加（1GB→2GB→4GB）重试。

---

### TC-MS-905: DDL 幂等性（重复 create_idx 不失败）

**前置条件**：M1 的物理表和索引已创建

**步骤**：
1. 再次调用 create_idx：
```python
    conn.create_idx(index_name, memory_id, vector_size=<embedding_dim>)
```
2. 预期：无异常抛出
3. 数据库验证：表和索引结构不变，无重复索引

**预期结果**：实验组依赖 `IF NOT EXISTS`/advisory lock，对照组依赖 `ConflictType.Ignore`；重复调用均不得异常、重复索引或丢数据。

---

### TC-MS-906: delete_idx 删除整个物理表（仅在无其他 memory 共享时安全）

**前置条件**：使用专属新 tenant，M1 是该 tenant 唯一 Memory 且不承载其它用例数据

**步骤**：
1. 调用 delete_idx：
```python
conn.delete_idx(index_name, memory_id)
```
2. 数据库验证：
```sql
SELECT COUNT(*)
  FROM information_schema.tables
 WHERE table_schema = '<schema>'
   AND table_name = 'ragflow_mem_<sha1>';
```
3. 预期：COUNT = 0

4. 对照组调用同一接口只删除 M1 的独立 Infinity 表并验证其它 tenant/memory 表存在。

**预期结果**：实验组 `delete_idx()` 执行 `DROP TABLE ... PURGE` 删除整个 tenant 表，因此只能用于专属 tenant；业务 `delete_memory` 不调用它。对照组只删除指定 Memory 表。

---

### TC-MS-907: index_exist 验证表和索引完整性

**前置条件**：M1 的物理表和所有索引已创建

**步骤**：
1. 调用 index_exist：
```python
result = conn.index_exist(index_name, memory_id)
```
2. 预期：True
3. 在专属 fixture 上删除一个常规索引后再次调用：
```sql
DROP INDEX <schema>.<index_name_for_message_id>;
```
```python
result = conn.index_exist(index_name, memory_id)
```
4. 实验组预期 False（required_indexes 不完整），随后调用 create_idx 恢复并再次验证 True。
5. 对照组先验证表存在时 True；在专属表删除一个非表结构索引后，当前 Infinity `index_exist()` 仍只检查表存在，预期仍为 True。记录这是 adapter 语义差异；恢复索引后再结束。

**预期结果**：实验组 `index_exist()` 验证 `BASE_COLUMNS` 全部存在且 `REGULAR_INDEXES` + `tokenized_ugin` 完整；对照组当前实现只验证表存在，破坏索引后的语义差异必须如实记录。

---

### TC-MS-908: advisory lock 防止并发 DDL 冲突

**前置条件**：两个并发写入请求同时触发首次建表

**步骤**：
1. 使用两个并发线程同时向新 memory 写入消息
2. 保存两请求结果、时间线和 DDL/锁相关日志；不强求从外部证明“只有一个线程执行”，第二个事务也可能在拿锁后执行幂等 no-op DDL
3. 数据库验证：只存在一张表、一套基础/全文/向量索引，两条业务消息均存在，无重复或冲突
4. 对照组执行同样的两个并发首次写入，验证 Infinity conflict-ignore/meta retry 后单表结构和两条消息完整

**预期结果**：`build_advisory_lock_sql()` 生成 `SELECT pg_advisory_xact_lock(hashtext('gaussdb_memory_create_table:{table}'))`，保证事务级别的 DDL 互斥。

---

## 附录 A: API 端点汇总

| 方法 | 路径 | 说明 | 必填参数 |
|------|------|------|---------|
| POST | /api/v1/messages | 写入消息 | memory_id, agent_id, session_id, user_input, agent_response |
| GET | /api/v1/messages | 获取最近消息 | memory_id（必填） |
| GET | /api/v1/messages/search | 搜索消息 | memory_id, query |
| GET | /api/v1/messages/{memory_id}:{message_id}/content | 获取消息内容 | URL 路径参数 |
| PUT | /api/v1/messages/{memory_id}:{message_id} | 更新消息状态 | status (boolean) |
| DELETE | /api/v1/messages/{memory_id}:{message_id} | 遗忘消息 | URL 路径参数 |
| GET | /api/v1/memories/{memory_id} | 列出 memory 的消息 | URL 路径参数 |

## 附录 B: 关键源码文件索引

| 文件 | 职责 |
|------|------|
| `api/apps/restful_apis/memory_api.py` | HTTP 路由定义和参数校验 |
| `api/apps/services/memory_api_service.py` | 业务逻辑层（权限检查、参数组装） |
| `api/db/joint_services/memory_message_service.py` | 消息写入编排（LLM 提取、embedding、任务队列） |
| `memory/services/messages.py` | MessageService（封装存储层调用） |
| `memory/services/query.py` | MsgTextQuery（全文检索表达式构建） |
| `memory/utils/gaussdb_conn.py` | GaussDBMemoryConnection（核心存储实现） |
| `common/doc_store/gaussdb_conn_base.py` | GaussDBDDLBuilder（DDL 生成基类） |
| `common/doc_store/gaussdb_conn_pool.py` | GaussDBConnectionPool（连接池管理） |
| `api/db/init_data.py` | 启动初始化（init_message_id_sequence、init_memory_size_cache） |

## 附录 C: 测试用例统计

| 章节 | 用例数 | 编号范围 |
|------|--------|---------|
| 消息写入 | 12 | TC-MS-001 ~ TC-MS-012 |
| 消息列表和最近消息 | 10 | TC-MS-100 ~ TC-MS-109 |
| 消息检索 | 14 | TC-MS-200 ~ TC-MS-213 |
| 消息状态更新 | 7 | TC-MS-300 ~ TC-MS-306 |
| 消息遗忘 | 7 | TC-MS-400 ~ TC-MS-406 |
| 消息内容获取 | 4 | TC-MS-500 ~ TC-MS-503 |
| 数据隔离 | 4 | TC-MS-600 ~ TC-MS-603 |
| 向量维度管理 | 6 | TC-MS-700 ~ TC-MS-705 |
| 启动维护 | 6 | TC-MS-800 ~ TC-MS-805 |
| 物理表和索引 | 9 | TC-MS-900 ~ TC-MS-908 |
| **合计** | **79** | |
