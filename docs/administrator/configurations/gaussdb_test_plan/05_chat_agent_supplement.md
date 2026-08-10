# Chat/Agent API 补充测试

## 概述
补充 Chat 软删除、PATCH 合并行为、Agent Webhook 安全层和 Completion 模式细节。

所有用例先执行 MySQL+Infinity 对照组，再执行 GaussDB+GaussDB 实验组；每个删除、rate-limit、Webhook 和 DataFlow 用例使用独立 fixture，业务写入只走 API，数据库与 Redis 仅只读验证。本文件不得携带或引用旧执行结果。

当前实现补充说明：Chat 软删除写入的是 `StatusEnum.INVALID.value`，数据库值为字符串 `'0'`，不是字面量 `'INVALID'`；Webhook 安全校验失败通常直接返回 HTTP 400，响应体 `code=400`；token 认证读取 DSL 中配置的 `security.token.token_header`，不是固定读取 `Authorization: Bearer ...`。

## Chat 软删除测试

### TC-CHAT-DEL-001: 删除 chat 是软删除
**步骤**：
```http
DELETE /api/v1/chats/<chat_id>
Authorization: Bearer <token>
```

预期响应：HTTP 200

数据库验证：
```sql
SELECT status FROM dialog WHERE id = '<chat_id>';
-- 预期：status = '0'（软删除，不是物理删除）

SELECT COUNT(*) FROM dialog WHERE id = '<chat_id>';
-- 预期：1（记录仍存在）
```

---

### TC-CHAT-DEL-002: 软删除的 chat 不出现在列表
**步骤**：
```http
GET /api/v1/chats
```

预期响应：列表中不包含已软删除的 chat

---

### TC-CHAT-DEL-003: 软删除的 chat 无法访问
**步骤**：
```http
GET /api/v1/chats/<deleted_chat_id>
```

预期响应：HTTP 200，响应体 `code=109`、`message="No authorization."`，且不返回 Chat 数据

---

### TC-CHAT-DEL-004: 批量删除 chats
**步骤**：
```http
DELETE /api/v1/chats
{
  "ids": ["<chat_id_1>", "<chat_id_2>"]
}
```

预期响应：HTTP 200

数据库验证：
```sql
SELECT COUNT(*) FROM dialog 
WHERE id IN ('<chat_id_1>', '<chat_id_2>') AND status = '0';
-- 预期：2
```

---

### TC-CHAT-DEL-005: delete_all 删除所有 chats
**步骤**：
```http
DELETE /api/v1/chats
{
  "delete_all": true
}
```

数据库验证：
```sql
SELECT COUNT(*) FROM dialog 
WHERE tenant_id = '<user_id>' AND status != '0';
-- 预期：0（所有 chat 被软删除）
```

---

## PATCH 合并行为测试

### TC-CHAT-PATCH-001: PATCH 合并 prompt_config
**前置条件**：Chat 有 prompt_config = {"system": "old prompt", "temperature": 0.7}

**步骤**：
```http
PATCH /api/v1/chats/<chat_id>
{
  "prompt_config": {
    "system": "new prompt"
  }
}
```

数据库验证：
```sql
SELECT prompt_config FROM dialog WHERE id = '<chat_id>';
-- 预期：{"system": "new prompt", "temperature": 0.7}
-- temperature 字段保留，system 字段被更新
```

---

### TC-CHAT-PATCH-002: PATCH 合并 llm_setting
**前置条件**：Chat 有 llm_setting = {"model": "gpt-4", "max_tokens": 1000}

**步骤**：
```http
PATCH /api/v1/chats/<chat_id>
{
  "llm_setting": {
    "max_tokens": 2000
  }
}
```

数据库验证：
```sql
SELECT llm_setting FROM dialog WHERE id = '<chat_id>';
-- 预期：{"model": "gpt-4", "max_tokens": 2000}
-- model 字段保留，max_tokens 被更新
```

---

### TC-CHAT-PATCH-003: PATCH 不传 prompt_config 不影响原值
**步骤**：
```http
PATCH /api/v1/chats/<chat_id>
{
  "name": "New Name"
}
```

数据库验证：
```sql
SELECT prompt_config FROM dialog WHERE id = '<chat_id>';
-- 预期：原值不变
```

---

## Agent Webhook 安全层测试

### TC-AGENT-WH-001: Webhook max_body_size 限制
**前置条件**：Agent webhook 配置 max_body_size = 1MB

**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
Content-Type: application/json
[超过 1MB 的 payload]
```

预期响应：HTTP 400，错误信息包含 "Request body too large"

---

### TC-AGENT-WH-002: Webhook IP 白名单
**前置条件**：Agent webhook 配置 ip_whitelist = ["192.168.1.0/24"]

**步骤**：从非白名单 IP 发送请求

预期响应：HTTP 400，错误信息包含 "is not allowed by whitelist"

---

### TC-AGENT-WH-003: Webhook Rate Limiting
**前置条件**：Agent webhook 配置 `security.rate_limit={"limit":10,"per":"minute"}`；使用本批次唯一 agent ID，执行前清除该 ID 对应的 rate-limit key，避免其他用例消费令牌

**步骤**：快速发送 15 个请求

预期响应：
- 前 10 个请求：HTTP 200
- 后续超限请求：HTTP 400，错误信息包含 "Rate limit" 或 "rate limit exceeded"

---

### TC-AGENT-WH-004: Webhook Token Auth
**前置条件**：Agent webhook 配置 `auth_type="token"`，`security.token.token_header="X-Webhook-Token"`，`security.token.token_value="secret123"`

**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
X-Webhook-Token: secret123
{
  "data": "test"
}
```

预期响应：HTTP 200

---

### TC-AGENT-WH-005: Webhook Token Auth - 错误 token
**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
X-Webhook-Token: wrong_token
```

预期响应：HTTP 400，错误信息包含 "Invalid token authentication"

---

### TC-AGENT-WH-006: Webhook Basic Auth
**前置条件**：Agent webhook 配置 auth_type = "basic"，username = "user"，password = "pass"

**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
Authorization: Basic dXNlcjpwYXNz
```

预期响应：HTTP 200

---

### TC-AGENT-WH-007: Webhook JWT Auth
**前置条件**：Agent webhook 配置 auth_type = "jwt"，jwt_secret = "secret"

**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
Authorization: Bearer <valid_jwt_token>
```

预期响应：HTTP 200

---

### TC-AGENT-WH-008: Webhook allow_anonymous
**前置条件**：Agent webhook 配置 auth_type = "none"，allow_anonymous = true

**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
```

预期响应：HTTP 200

---

### TC-AGENT-WH-009: Webhook allow_anonymous = false
**前置条件**：Agent webhook 配置 auth_type = "none"，allow_anonymous = false

**步骤**：
```http
POST /api/v1/agents/<agent_id>/webhook
```

预期响应：HTTP 400，错误信息包含 "allow_anonymous"

---

## Completion 模式测试

### TC-AGENT-COMP-001: 新会话模式（无 session_id）
**步骤**：
```http
POST /api/v1/agents/chat/completions
{
  "agent_id": "<agent_id>",
  "question": "What is RAG?",
  "stream": false
}
```

预期行为：
1. 从 Redis runtime replica 加载 DSL，并生成新的 session_id
2. 运行 agent
3. 返回结果并持久化 workflow session

数据库验证：
```sql
SELECT COUNT(*) FROM api_4_conversation
WHERE dialog_id = '<agent_id>' AND id = '<returned_session_id>';
-- 预期：1
```

---

### TC-AGENT-COMP-002: Session 模式（有 session_id）
**步骤**：
```http
POST /api/v1/agents/chat/completions
{
  "agent_id": "<agent_id>",
  "session_id": "<existing_session_id>",
  "question": "Follow up question",
  "stream": false
}
```

预期行为：
1. 加载已有 session
2. 继续对话
3. 持久化结果

数据库验证：
```sql
SELECT COUNT(*) FROM api_4_conversation
WHERE id = '<session_id>';
-- 预期：1（session 被更新）
```

---

### TC-AGENT-COMP-003: Streaming 模式
**步骤**：
```http
POST /api/v1/agents/chat/completions
{
  "agent_id": "<agent_id>",
  "question": "What is RAG?",
  "stream": true
}
```

预期响应：
```
Content-Type: text/event-stream

data: {"event": "message", "data": {"content": "..."}}
data: {"event": "message_end", "data": {...}}
data: [DONE]
```

---

### TC-AGENT-COMP-004: DataFlow 模式（异步任务）
**前置条件**：Agent 配置为 DataFlow 类型

**步骤**：
```http
POST /api/v1/agents/chat/completions
{
  "agent_id": "<agent_id>",
  "question": "Process data",
  "files": [{"id": "<uploaded_file_id>", "name": "input.txt"}],
  "stream": false
}
```

预期响应：HTTP 200，返回 `data.message_id`（Task ID）和 `data.session_id`

数据库验证：
```sql
SELECT id, doc_id, task_type, progress FROM task
 WHERE id = '<message_id>';
-- 预期：task_type='dataflow'，doc_id 为 CANVAS_DEBUG_DOC_ID，并已加入 common queue
```

---

### TC-AGENT-COMP-005: 取消运行中的任务
**前置条件**：Agent 正在运行

**步骤**：
```http
POST /api/v1/tasks/<message_id>/cancel
```

预期响应：HTTP 200

Redis 验证：
```bash
# 应设置取消标志
redis-cli GET "<message_id>-cancel"
# 预期：存在
```

数据库只读验证 Task `progress=-1`；如果 task 关联普通 Document，document run 还应转为 CANCEL。Agent Completion 没有 `/agents/<agent_id>/cancel` 路由。
