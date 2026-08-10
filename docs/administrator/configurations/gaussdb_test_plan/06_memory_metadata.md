# 06 - Memory 元数据 CRUD 测试

## 概述

本文档覆盖 Memory 元数据的 CRUD 测试。Memory 元数据存储在 metadata DB 的 `memory` 表中，与 Memory Store（消息存储）分开测试。Memory Store 的消息操作见 [07_memory_store_e2e.md](./07_memory_store_e2e.md)。

## 前置条件

- 两组 RAGFlow API 均独立运行；每个用例先执行 MySQL+Infinity 对照组，再执行 GaussDB metadata + GaussDB DocEngine 实验组
- 已登录用户，持有有效 `Authorization` Token
- 使用本批次实际配置的 Ollama embedding ID 和 Qwen chat ID，不使用占位模型名
- fixture 名称带运行 ID/用例 ID；破坏性用例使用专属 Memory。业务写入只走 API，metadata/DocEngine/Redis 仅只读核对，不引用备份脚本或历史结果

下文示例中的 `embedding-model` / `chat-model` 仅为字段说明，runner 必须在发请求前替换为上述真实 ID；原始请求证据中不得出现未替换占位符。

---

## 1. 创建 Memory

### TC-MM-001: 正常创建 Memory

**前置条件**：用户已登录，已取得本批次真实 `<ollama_embedding_id>` 和 `<qwen_chat_id>`

**步骤**：

1. 发送创建请求：
   ```bash
   curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "gaussdb-test-mem-001",
       "memory_type": ["raw"],
       "embd_id": "<ollama_embedding_id>",
       "llm_id": "<qwen_chat_id>"
     }'
   ```

2. 预期响应：HTTP 200，响应体包含 `id` 字段

3. 数据库验证：
   ```sql
   SELECT id, name, memory_type, embd_id, llm_id, permissions,
          memory_size, forgetting_policy, temperature, storage_type
    FROM memory WHERE name = 'gaussdb-test-mem-001';
   ```
   预期：一行记录，`memory_type=1`，`embd_id` 和 `llm_id` 非空；`permissions='me'`、`memory_size=5242880`、`forgetting_policy='FIFO'`、`temperature=0.5`、`storage_type='table'` 使用模型默认值。

4. 验证未创建 Memory Store 物理表数据：
   ```sql
   -- Memory Store 物理表不应有该 memory_id 的行
   -- （物理表在首次写入消息时才创建）
   ```

**预期结果**：Memory 元数据创建成功，不触发 Memory Store 建表

---

### TC-MM-002: 创建多类型 Memory

**步骤**：

1. 创建包含多种类型的 Memory：
   ```bash
   curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "gaussdb-test-mem-002",
       "memory_type": ["raw", "semantic", "episodic"],
       "embd_id": "embedding-model",
       "llm_id": "chat-model"
     }'
   ```

2. 数据库验证：
   ```sql
   SELECT name, memory_type FROM memory WHERE name = 'gaussdb-test-mem-002';
   ```
   预期：`memory_type` 为位运算组合值（raw=1, semantic=2, episodic=4 → 7）

**预期结果**：多类型 Memory 的 `memory_type` 正确存储为位标记

---

### TC-MM-003: 重复名称创建自动重命名

**步骤**：

1. 使用已有 Memory 名称再次创建：
   ```bash
   curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "gaussdb-test-mem-001",
       "memory_type": ["raw"],
       "embd_id": "embedding-model",
       "llm_id": "chat-model"
     }'
   ```

2. 预期响应：业务成功，返回的新 memory 名称不是原始重复名

3. 数据库验证：
   ```sql
   SELECT name
     FROM memory
    WHERE tenant_id = '<current_user_id>'
      AND name LIKE 'gaussdb-test-mem-001%'
    ORDER BY create_time;
   ```
   预期：原始记录仍为 `gaussdb-test-mem-001`，新记录被 `duplicate_name()` 改为 `gaussdb-test-mem-001(1)` 或下一个可用序号。

**预期结果**：同 tenant 重复名称不会覆盖旧记录，后端自动生成唯一名称。

---

### TC-MM-004: 缺少必填字段

**步骤**：分别缺少 `name`、`memory_type`、`embd_id`、`llm_id` 发送请求：

```bash
# 缺少 name
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model"}'

# 缺少 memory_type
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"no-type","embd_id":"embedding-model","llm_id":"chat-model"}'

# 缺少 embd_id
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"no-embd","memory_type":["raw"],"llm_id":"chat-model"}'

# 缺少 llm_id
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"no-llm","memory_type":["raw"],"embd_id":"embedding-model"}'
```

预期：每个请求业务失败，响应体 `code != 0`，错误信息指明缺少字段；DB 不写入对应 `name` 的 memory 行。

**预期结果**：必填字段缺失时返回明确错误

---

### TC-MM-005: 非法 memory_type 值

**步骤**：

```bash
# 非法枚举值
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"bad-type","memory_type":["invalid_type"],"embd_id":"embedding-model","llm_id":"chat-model"}'

# 空列表
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"empty-type","memory_type":[],"embd_id":"embedding-model","llm_id":"chat-model"}'

# 非列表类型
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"string-type","memory_type":"raw","embd_id":"embedding-model","llm_id":"chat-model"}'

# 重复项
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"dup-type","memory_type":["raw","raw"],"embd_id":"embedding-model","llm_id":"chat-model"}'
```

数据库验证：
```sql
SELECT name, memory_type FROM memory
 WHERE name IN ('bad-type', 'empty-type', 'string-type', 'dup-type');
```

当前代码判定：
- `invalid_type` 和非列表类型应返回业务失败，DB 不写入。
- 空列表应作为非法 `memory_type` 拒绝，DB 不写入；若当前接口接受并写入 `memory_type=0`，判定为参数校验缺陷。
- 重复项会被 `set()` 去重，`["raw","raw"]` 预期写入 `memory_type=1`。

对照组：前端创建表单默认 `memory_type=["raw"]` 且校验必须包含 `raw`；绕过前端直接调 API 时以上 DB 实态是最终判定依据。

---

### TC-MM-006: 不存在的模型 ID

**步骤**：

```bash
# 不存在的 embd_id
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"bad-embd","memory_type":["raw"],"embd_id":"nonexistent-model","llm_id":"chat-model"}'

# 不存在的 llm_id
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"bad-llm","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"nonexistent-llm"}'
```

数据库验证：
```sql
SELECT name, embd_id, llm_id
  FROM memory
 WHERE name IN ('bad-embd', 'bad-llm');
```

通过/失败判定：公开 API 应在创建时拒绝不存在的 `embd_id`/`llm_id`，返回 `code != 0` 且 DB 不写入对应 memory 行；若请求成功且 DB 写入不存在的模型 ID，判定为模型可用性校验缺口。后续写入消息或检索时才会因模型配置不可用失败。

对照组：使用存在的模型 ID 创建 memory，后续写入消息应能进入 embedding 流程。

---

### TC-MM-007: 名称边界值

**步骤**：

```bash
# 空字符串名称
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model"}'

# 纯空白名称
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"   ","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model"}'

# 超长名称（超过 MEMORY_NAME_LIMIT）
LONG_NAME=$(python3 -c "print('A' * 2000)")
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$LONG_NAME\",\"memory_type\":[\"raw\"],\"embd_id\":\"embedding-model\",\"llm_id\":\"chat-model\"}"

# 特殊字符名称
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"test'\''; DROP TABLE memory;--","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model"}'

# 中文名称
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"测试记忆-001","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model"}'
```

数据库验证：
```sql
SELECT name FROM memory
 WHERE name IN ('test''; DROP TABLE memory;--', '测试记忆-001');
```

**预期结果**：空、空白、超长名称被拒绝；特殊字符名称和中文名称可正常创建且不会破坏 SQL。特殊字符名称的通过条件是参数化写入成功、`memory` 表仍存在、对照查询正常。

---

### TC-MM-008: permissions 创建参数忽略与更新参数验证

**步骤**：

```bash
# 创建时传合法值 me
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"perm-me","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model","permissions":"me"}'

# 创建时传合法值 team
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"perm-team","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model","permissions":"team"}'

# 创建时传非法值
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"perm-bad","memory_type":["raw"],"embd_id":"embedding-model","llm_id":"chat-model","permissions":"public"}'
```

创建阶段数据库验证：
```sql
SELECT name, permissions FROM memory
 WHERE name IN ('perm-me', 'perm-team', 'perm-bad');
```

当前代码判定：`POST /memories` 路由只传 `name/memory_type/embd_id/llm_id` 到 service，创建阶段的 `permissions` 请求字段会被忽略；三条记录若创建成功，DB 中均应为默认 `permissions='me'`。

更新阶段验证：
```http
PUT /api/v1/memories/<memory_id>
{ "permissions": "team" }

PUT /api/v1/memories/<memory_id>
{ "permissions": "public" }
```

预期：`team` 更新成功，DB 为 `team`；`public` 返回业务失败且 DB 保持原值。

---

## 2. 查询 Memory 列表

### TC-MM-009: 获取 Memory 列表

**前置条件**：已创建多个 Memory

**步骤**：

1. 获取所有 Memory：
   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:9380/api/v1/memories | python3 -m json.tool
   ```
   预期：返回当前用户可见的 Memory 列表

2. 数据库验证：
   ```sql
   SELECT id, name, tenant_id, memory_type, permissions FROM memory ORDER BY create_time DESC;
   ```

3. 对比 API 响应和当前用户可访问的数据库行数；跨 tenant 或无权限记录只能作为对照组，不应出现在当前用户响应里。

**预期结果**：API 返回的 Memory 列表与 `_memory_accessible` 规则一致。

---

### TC-MM-010: 按 memory_type 过滤

**步骤**：

```bash
# 按单个类型过滤
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?memory_type=raw"

# 按多个类型过滤
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?memory_type=raw,semantic"

# 非法类型
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?memory_type=invalid"
```

验证 SQL trace 中包含位运算过滤 `memory_type & value > 0`

数据库验证：
```sql
SELECT id, name, memory_type
  FROM memory
 WHERE tenant_id = '<current_user_id>'
   AND (memory_type & 1) > 0;
```

预期：
- `memory_type=raw` 返回所有包含 raw 位的 memory。
- `memory_type=raw,semantic` 返回包含 raw 或 semantic 位的 memory。
- `memory_type=invalid` 当前会计算为 0，预期返回空列表；如果产品要求非法过滤值报参数错误，应登记为校验缺口。

对照组：MySQL 元数据库执行相同过滤，返回 ID 集合应与 GaussDB 一致。

---

### TC-MM-011: 分页和关键词搜索

**步骤**：

```bash
# 分页
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?page=1&page_size=2"

# 关键词搜索
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?keywords=test"

# 非法分页/边界
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?page=0&page_size=-1"

# 超大 page_size
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:9380/api/v1/memories?page_size=9999"
```

数据库验证：只读请求不应修改 `memory` 表。

当前代码判定：
- 合法分页和关键词搜索应成功，关键词搜索使用 `name.contains()`。
- `page_size > 100` 与非整数在路由 `try` 外抛 ValueError，当前全局 handler 预期返回 HTTP 200 / `code=100`；协议期望是参数错误 `code=101`，差异记为参数处理缺陷。
- `page=0`、`page_size=-1/0` 当前没有下界校验；安全契约要求拒绝，若进入数据库分页或返回成功，记录参数校验缺口并比较两组是否出现方言差异。

**预期结果**：合法分页参数正确生效；非法分页按实际响应和 DB 无变更判定是否为校验缺口。

---

## 3. 更新 Memory

### TC-MM-012: 更新 Memory 名称和配置

**步骤**：

1. 更新名称和描述：
   ```bash
   curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name":"updated-name","description":"Updated description"}'
   ```

2. 数据库验证：
   ```sql
   SELECT name, description FROM memory WHERE id = '<memory_id>';
   ```
   预期：名称和描述已更新

**预期结果**：Memory 元数据更新成功

---

### TC-MM-013: 更新 memory_size 边界值

**步骤**：

```bash
# 最小合法值
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_size":1}'

# 0（当前实现按未提供字段处理，保持原 memory_size 不变）
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_size":0}'

# 负数
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_size":-1}'

# 字符串数字
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_size":"100"}'
```

**预期结果**：
- `memory_size=1` 被接受。
- `memory_size=0` 在当前服务层因 `if new_memory_setting.get("memory_size")` 判断被视为未传字段，接口返回成功且原 `memory_size` 保持不变；该行为不是 GaussDB 特有差异。
- `memory_size=-1` 返回参数错误。
- 字符串数字（如 `"100"`）会被转换为整数并接受。

---

### TC-MM-014: 更新 temperature

**步骤**：

```bash
# 正常值
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"temperature":0.5}'

# 边界值 0
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"temperature":0}'

# 边界值 1
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"temperature":1}'

# 负数
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"temperature":-0.5}'

# 大于 1
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"temperature":2.0}'
```

**预期结果**：0~1 范围内合法，越界值被拒绝

---

### TC-MM-015: 已有消息时禁止修改 embd_id 和 memory_type

**前置条件**：Memory 已通过消息 API 写入消息。当前 POST 会同步写 raw 行、更新 size cache 后再投递非 raw 提取任务；仍须轮询到 Message Store 可读并以只读方式确认 memory size cache 大于 0，不能只把 HTTP 成功响应当作存储证据

**步骤**：

```bash
# 尝试修改 embd_id
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"embd_id":"different-model"}'

# 尝试修改 memory_type
curl -s -X PUT http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"memory_type":["raw","semantic"]}'
```

预期：两个请求都返回错误，提示有消息时不允许修改

数据库验证：`embd_id` 和 `memory_type` 未改变

**预期结果**：已有消息的 Memory 禁止修改 embedding 模型和 memory type

---

## 4. 获取 Memory 配置

### TC-MM-016: 获取单个 Memory 配置

**步骤**：

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9380/api/v1/memories/<memory_id>/config | python3 -m json.tool
```

数据库验证：
```sql
SELECT * FROM memory WHERE id = '<memory_id>';
```

对比 API 响应字段与数据库行

**预期结果**：API 返回的配置与数据库一致

---

### TC-MM-017: 获取不存在的 Memory 配置

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:9380/api/v1/memories/nonexistent-id/config
```

预期：当前 REST helper 通过 HTTP 200 返回 JSON 业务错误，响应体 `code=404`。

---

### TC-MM-018: 跨租户访问 Memory 配置

**前置条件**：用户 A 创建了 Memory，用户 B 尝试访问

**步骤**：

```bash
# 用户 B 使用用户 A 的 memory_id
curl -s -H "Authorization: Bearer $TOKEN_B" \
  http://127.0.0.1:9380/api/v1/memories/<user_a_memory_id>/config
```

预期：当前实现把无访问权限的 memory 按 NotFound 处理，HTTP 200，响应体 `code=404`，不返回其他租户的数据。

**预期结果**：跨租户访问被拒绝

---

## 5. 删除 Memory

### TC-MM-019: 正常删除 Memory

**步骤**：

1. 记录删除前数据库状态：
   ```sql
   SELECT COUNT(*) FROM memory WHERE id = '<memory_id>';
   ```

2. 发送删除请求：
   ```bash
   curl -s -X DELETE http://127.0.0.1:9380/api/v1/memories/<memory_id> \
     -H "Authorization: Bearer $TOKEN"
   ```

3. 验证 Memory 已删除：
   ```sql
   SELECT COUNT(*) FROM memory WHERE id = '<memory_id>';
   ```
   预期：0 行

4. 再次获取配置验证 404：
   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:9380/api/v1/memories/<memory_id>/config
   ```

**预期结果**：Memory 元数据删除成功

---

### TC-MM-020: 删除有消息的 Memory

**前置条件**：Memory 已写入消息（Memory Store 物理表有数据）

**步骤**：

1. 删除 Memory：
   ```bash
   curl -s -X DELETE http://127.0.0.1:9380/api/v1/memories/<memory_id> \
     -H "Authorization: Bearer $TOKEN"
   ```

2. 验证 metadata 已删除

3. 验证 Message Store 中对应 `memory_id` 的数据已清理。对照组通过 Infinity 只读检索/计数验证，实验组再执行以下 GaussDB 物理表只读验证：
   ```sql
   -- 查找对应的物理表
   SELECT table_name FROM information_schema.tables
    WHERE table_schema = current_schema()
    AND table_name LIKE 'ragflow_mem_%';

   -- 检查物理表中是否还有该 memory_id 的行
   SELECT COUNT(*) FROM <physical_table> WHERE memory_id = '<memory_id>';
   ```
   预期：0 行

4. 验证同 tenant 其他 Memory 的数据不受影响

**预期结果**：Memory 删除时同时清理 Memory Store 中对应数据

---

### TC-MM-021: 重复删除

```bash
# 第一次删除
curl -s -X DELETE http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN"

# 第二次删除
curl -s -X DELETE http://127.0.0.1:9380/api/v1/memories/<memory_id> \
  -H "Authorization: Bearer $TOKEN"
```

预期：第二次返回 404 或约定错误码
当前 REST helper 通过 HTTP 200 返回 JSON 业务错误，响应体 `code=404`。

---

### TC-MM-022: 跨租户删除

```bash
curl -s -X DELETE http://127.0.0.1:9380/api/v1/memories/<other_tenant_memory_id> \
  -H "Authorization: Bearer $TOKEN"
```

预期：当前实现把无访问权限的 memory 按 NotFound 处理，HTTP 200，响应体 `code=404`。

数据库验证：其他租户的 Memory 未被删除

**预期结果**：跨租户删除被拒绝

---

## 6. GaussDB 空字符串兼容字段

### TC-MM-023: embd_id 和 llm_id 空值兼容

**步骤**：

1. 创建 Memory 时使用空字符串 `embd_id`：
   ```bash
   curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name":"empty-models","memory_type":["raw"],"embd_id":"","llm_id":""}'
   ```

2. 数据库验证：
   ```sql
   SELECT name, embd_id, embd_id IS NULL AS embd_is_null,
          llm_id, llm_id IS NULL AS llm_is_null
    FROM memory WHERE name = 'empty-models';
   ```
   预期：MySQL 对照组底层存储为空字符串；GaussDB 实验组因 `EmptyStringCharField` 适配，底层 SQL 查询显示为 NULL。

3. API 回读：
   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:9380/api/v1/memories/<id>/config
   ```
   预期：两组 API 回读的 `embd_id` 和 `llm_id` 均返回 `""`，GaussDB 的 NULL 语义不泄漏到业务层。

**预期结果**：GaussDB 空字符串兼容字段正确工作

---

## 7. 无认证访问

### TC-MM-024: 无 Token 访问 Memory API

```bash
curl -s http://127.0.0.1:9380/api/v1/memories
curl -s -X POST http://127.0.0.1:9380/api/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"name":"no-auth","memory_type":["raw"]}'
```

预期：HTTP 401

**预期结果**：未认证请求被拒绝
