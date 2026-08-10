# Memory Store 补充测试

## 概述
补充 Memory API 的验证逻辑、访问控制和字段规范化细节。

所有用例先执行 MySQL+Infinity 对照组，再执行 GaussDB+GaussDB 实验组；示例模型名在运行时必须替换为本批次真实 Ollama embedding/Qwen chat ID。业务写入只走 API，数据库/DocEngine/Redis 仅只读核对，不使用任何备份脚本或历史结果。

## 验证逻辑补充

### TC-MM-SUP-001: Memory name 自动 strip
**步骤**：
```http
POST /api/v1/memories
{
  "name": "  Spaced Memory Name  ",
  "memory_type": ["raw"],
  "embd_id": "embedding-model",
  "llm_id": "chat-model"
}
```

数据库验证：
```sql
SELECT name FROM memory WHERE id = '<new_memory_id>';
-- 预期："Spaced Memory Name"（已 strip）
```

---

### TC-MM-SUP-002: Memory name 长度检查
**步骤**：创建超过 MEMORY_NAME_LIMIT 的名称

预期响应：业务失败，响应体 `code != 0`，错误信息包含 "exceeds limit"

---

### TC-MM-SUP-003: Memory type 规范化 - 去重与位值回读顺序
**步骤**：
```http
POST /api/v1/memories
{
  "name": "Test Memory",
  "memory_type": ["semantic", "raw", "semantic", "episodic"],
  "embd_id": "embedding-model",
  "llm_id": "chat-model"
}
```

数据库验证：
```sql
SELECT memory_type FROM memory WHERE id = '<new_memory_id>';
-- 预期：位运算值 7；API 按 MemoryType 枚举顺序回读 ["raw", "semantic", "episodic"]
```

---

### TC-MM-SUP-004: Memory type 大小写敏感 - 大写输入被拒绝
**步骤**：
```http
POST /api/v1/memories
{
  "name": "Test Memory",
  "memory_type": ["RAW", "Semantic"],
  "embd_id": "embedding-model",
  "llm_id": "chat-model"
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 `not supported`。

数据库验证：
```sql
SELECT COUNT(*) FROM memory WHERE name = 'Test Memory';
-- 预期：0，不应写入 memory 行
```

对照组：
```http
POST /api/v1/memories
{
  "name": "Test Memory Lowercase",
  "memory_type": ["raw", "semantic"],
  "embd_id": "embedding-model",
  "llm_id": "chat-model"
}
```

对照预期：小写枚举创建成功，DB 中 `memory_type = 3`。当前后端只接受 `raw/semantic/episodic/procedural` 小写枚举；前端也只提交小写枚举。

---

### TC-MM-SUP-005: Temperature 范围验证
**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "temperature": 1.5
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 "Temperature should be in range [0, 1]"

---

### TC-MM-SUP-006: Memory size 范围验证
**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "memory_size": -1
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 "Memory size should be in range"

---

### TC-MM-SUP-007: Forgetting policy 枚举验证
**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "forgetting_policy": "invalid_policy"
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 "not supported"

---

### TC-MM-SUP-008: Permission 枚举验证
**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "permissions": "invalid_permission"
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 "Unknown permission"

---

## 访问控制补充

### TC-MM-SUP-009: Team permission - 联合租户可访问
**前置条件**：
- User A 创建 memory，并通过 `PUT /api/v1/memories/<memory_id>` 设置 `permissions="team"`
- 通过用户/团队公开 API 创建 User B，并完成 User A tenant 的邀请与确认；只读查询 `user_tenant` 确认关系已建立，禁止直接写表构造关系

**步骤**：
User B 访问该 memory：
```http
GET /api/v1/memories/<memory_id>
Authorization: Bearer <user_b_token>
```

预期响应：HTTP 200，响应体 `code=0`，成功访问。

---

### TC-MM-SUP-010: Team permission - 非联合租户被拒绝
**前置条件**：
- User A 创建 memory，并通过 `PUT /api/v1/memories/<memory_id>` 设置 `permissions="team"`
- User C 未加入 User A 的 tenant

**步骤**：
```http
GET /api/v1/memories/<memory_id>
Authorization: Bearer <user_c_token>
```

预期响应：当前实现把无访问权限的 memory 按 NotFound 处理，HTTP 200，响应体 `code=404`。

---

### TC-MM-SUP-011: Me permission - 仅 owner 可访问
**前置条件**：
- User A 创建 memory，permissions="me"

**步骤**：
User B 尝试访问：
```http
GET /api/v1/memories/<memory_id>
Authorization: Bearer <user_b_token>
```

预期响应：当前实现把无访问权限的 memory 按 NotFound 处理，HTTP 200，响应体 `code=404`。

---

### TC-MM-SUP-012: 列表 owner_ids/tenant_id 过滤只返回可访问 memory
**前置条件**：
- User A 创建 Memory A，`permissions="team"`。
- User B 通过公开团队邀请/确认 API 加入 User A tenant，并以 `user_tenant` 只读查询作为证据。
- User C 创建 Memory C，User B 未加入 User C tenant。

**步骤**：
```http
GET /api/v1/memories?owner_ids=<user_a_id>,<user_c_id>
Authorization: Bearer <user_b_token>
```

预期响应：只返回 User B 可访问的 Memory A，不返回 User C 的 Memory C。

数据库验证：
```sql
SELECT id, tenant_id, permissions
  FROM memory
 WHERE id IN ('<memory_a_id>', '<memory_c_id>');
```

对照组：User C 使用自己的 token 查询 `owner_ids=<user_c_id>`，应能看到 Memory C。

---

## 更新字段补充

### TC-MM-SUP-013: 更新 tenant_llm_id 和 tenant_embd_id - 公开字段无效更新
**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "tenant_llm_id": "<new_tenant_llm_id>",
  "tenant_embd_id": "<new_tenant_embd_id>"
}
```

数据库验证：
```sql
SELECT tenant_llm_id, tenant_embd_id, update_time
  FROM memory WHERE id = '<memory_id>';
```

代码审查判定（仍须由本批次重新实测）：
- `api/apps/restful_apis/memory_api.py` 会从请求体提取 `tenant_llm_id`、`tenant_embd_id`。
- `api/apps/services/memory_api_service.py::update_memory()` 未把这两个字段写入 `update_dict`。
- 因此当前响应应为 HTTP 200 / `code=0`，但 DB 两个字段和 update_time 保持原值。

通过/失败判定：路由公开提取这两个字段却静默 no-op 属于接口契约缺陷；响应成功不能放行。若字段应为内部字段，也应由接口明确拒绝而不是伪成功。

---

### TC-MM-SUP-014: 已有消息时更新 embd_id - 被拒绝
**前置条件**：通过 `POST /api/v1/messages` 写入专属消息；当前 POST 会同步写 raw 行和 size cache、再投递非 raw 提取任务，仍须轮询 `GET /api/v1/messages` 或搜索接口直到消息可读，并通过只读 Redis/Message Store 证据确认该 Memory 的 size cache 大于 0 后再继续

**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "embd_id": "new-embedding-model"
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 `Can't update ['embd_id'] when memory isn't empty.`

数据库验证：
```sql
SELECT embd_id FROM memory WHERE id = '<memory_id>';
-- 预期：仍为更新前的 embd_id
```

对照组：创建一个没有任何消息的 memory，执行同样的 `embd_id` 更新；预期该空 memory 更新成功，DB 中 `embd_id` 变化。

---

### TC-MM-SUP-015: 已有消息时更新 memory_type - 被拒绝
**前置条件**：通过 `POST /api/v1/messages` 写入专属消息；当前 POST 会同步写 raw 行和 size cache、再投递非 raw 提取任务，仍须轮询 `GET /api/v1/messages` 或搜索接口直到消息可读，并通过只读 Redis/Message Store 证据确认该 Memory 的 size cache 大于 0 后再继续

**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "memory_type": ["raw", "semantic"]
}
```

预期响应：业务失败，响应体 `code != 0`，错误信息包含 `Can't update ['memory_type'] when memory isn't empty.`

数据库验证：
```sql
SELECT memory_type, system_prompt FROM memory WHERE id = '<memory_id>';
-- 预期：memory_type 和 system_prompt 均保持更新前的值
```

对照组：创建一个没有任何消息的 memory，执行同样的 `memory_type` 更新；预期更新成功，DB 中 `memory_type` 位值变化。若原 `system_prompt` 是旧类型默认值且请求未显式传 `system_prompt`，还应验证 `system_prompt` 自动更新为新类型默认值。

---

### TC-MM-SUP-016: 更新 avatar、description、system_prompt、user_prompt 允许空值
**步骤**：
```http
PUT /api/v1/memories/<memory_id>
{
  "avatar": "",
  "description": "",
  "system_prompt": "",
  "user_prompt": ""
}
```

预期响应：HTTP 200 / `code=0`。MySQL 普通 TextField 物理保存 `''`；A/ORA-compatible GaussDB 将空串物理存为 NULL，API 回读为 JSON null。四字段均不是 EmptyString 专用 Field，不能错误要求实验组回读 `""`。

---

## Message 操作补充

### TC-MM-SUP-017: Message 访问控制 - owner 可操作
**前置条件**：owner 已通过消息 API 写入一条专属消息并轮询到该消息可读，记录实际 `<message_id>`

**步骤**：
Memory owner 操作 message：
```http
DELETE /api/v1/messages/<memory_id>:<message_id>
```

预期响应：HTTP 200，响应体 `code=0`。

---

### TC-MM-SUP-018: Message 访问控制 - team permission 联合租户可操作
**前置条件**：Memory 已通过更新接口设置 `permissions="team"`，User B 通过公开团队 API 成为联合租户；owner 已写入专属消息并轮询到可读，记录实际 `<message_id>`

**步骤**：
```http
DELETE /api/v1/messages/<memory_id>:<message_id>
Authorization: Bearer <user_b_token>
```

预期响应：HTTP 200，响应体 `code=0`。

---

### TC-MM-SUP-019: Message 访问控制 - 非授权用户被拒绝
**前置条件**：owner 已写入专属消息并轮询到可读，记录实际 `<message_id>`；未授权用户与 owner 不存在 tenant 关系

**步骤**：
```http
DELETE /api/v1/messages/<memory_id>:<message_id>
Authorization: Bearer <unauthorized_user_token>
```

预期响应：当前实现把无访问权限的 memory 按 NotFound 处理，HTTP 200，响应体 `code=404`。

---

### TC-MM-SUP-020: 获取 message 内容 - 跨 memory 隔离
**前置条件**：
- 使用两个专属 Memory A/B。全局 Redis 序列在正常 API 写入下不会产生相同 `message_id`，因此本用例作为“主键碰撞/隔离故障注入”例外：先通过 API 建立两条合法消息，再由受控 adapter fixture 在专属数据中构造相同 `message_id=123`；记录所有修改并在用例后清理
- 对照组通过 Infinity 测试 adapter 构造等价 fixture，实验组通过 GaussDB Memory adapter 构造；不得用业务 DB 写入脚本或复用历史 fixture

**步骤**：
```http
GET /api/v1/messages/<memory_a_id>:123/content
```

预期响应：返回 Memory A 的 message 内容，不会混淆

数据库验证：
```sql
SELECT id, memory_id, message_id, content_ltks
  FROM ragflow_mem_<sha1>
 WHERE message_id = 123
 ORDER BY memory_id;
```

预期：API 返回的内容只来自 `<memory_a_id>` 对应行；Memory B 的同 `message_id` 行保持存在但不被返回。

补充判定：物理主键是 `id='<memory_id>_<message_id>'`。精确读取由 API 根据 URL 中的 memory ID 组装该主键，因此两个 Memory 的相同数值 ID 必须彼此独立。

---

## 异常处理和参数边界补充

### TC-MM-SUP-021: POST /messages 对不可访问 memory 不写入数据
**前置条件**：
- User A 创建 Memory A。
- User B 未加入 User A tenant。

**步骤**：
```http
POST /api/v1/messages
Authorization: Bearer <user_b_token>
{
  "memory_id": ["<memory_a_id>"],
  "agent_id": "agent-deny",
  "session_id": "session-deny",
  "user_input": "should not be stored",
  "agent_response": "should not be stored"
}
```

预期响应：业务失败，响应体 `code != 0`，message 包含 `Memory not found` 或等价错误。

精确预期：当前路由返回 HTTP 200、`code=500`，`message="Some messages failed to add. Detail:Memory not found."`；若没有返回该错误或产生异步任务/数据行，判定为访问控制缺陷。

数据库验证：
```sql
SELECT COUNT(*) AS denied_fixture_count
  FROM ragflow_mem_<sha1_of_user_a_tenant>
 WHERE memory_id = '<memory_a_id>'
   AND agent_id = 'agent-deny'
   AND session_id = 'session-deny';
-- 请求前后均为 0；tokenized_content_ltks 是分词结果，不用 LIKE 原句作为主证据
```

对照组：User A 使用唯一的 `agent_id/session_id` 写入自己的 Memory A，预期业务成功；轮询到消息可读后，对照环境以 Infinity 只读查询验证，实验环境以 GaussDB 表只读查询验证恰有一条 raw 消息。

---

### TC-MM-SUP-022: GET /memories 分页非法类型的框架层错误
**步骤**：
```http
GET /api/v1/memories?page=abc&page_size=50
```

代码审查判定：
- `page = int(request.args.get("page", 1))` 在 `try` 块外执行。
- 因此 `page=abc` 由全局 exception handler 返回 HTTP 200 / `code=100` 和 ValueError 信息，而不是参数错误 `code=101`；按协议缺陷记录。

数据库验证：只读请求，不应修改 `memory` 表。

对照组：
```http
GET /api/v1/memories?page=1&page_size=50
```

对照预期：业务成功，返回当前用户可访问 memory 列表。

---

### TC-MM-SUP-023: GET /memories page_size 上界与下界
**步骤**：
```http
GET /api/v1/memories?page=1&page_size=101
GET /api/v1/memories?page=1&page_size=-1
GET /api/v1/memories?page=1&page_size=0
```

代码审查判定：
- `validate_rest_api_page_size()` 只限制 `page_size <= 100`，未限制下界。
- `page_size=101` 在路由 try 外抛 ValueError，预期 HTTP 200 / `code=100`；协议应返回 `code=101`。
- `page_size=-1/0` 的安全预期是参数拒绝；当前 helper 未检查下界，若请求成功或进入 DB 查询，记录分页校验缺口和任何双组方言差异。

数据库验证：三次请求均不得修改 `memory` 表。

对照组：`page_size=100` 是合法边界，应返回成功。
