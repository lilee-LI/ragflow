# 05 Chat / Session / Agent GaussDB 兼容性测试计划

> **适用范围**：Chat（Dialog）CRUD、Session（Conversation）CRUD、消息操作、Completion、音频/思维导图/推荐、Agent（UserCanvas）CRUD、模板/版本/会话/日志/Webhook、Bot 端点。
>
> **核心关注**：GaussDB 下 JSONField 存储/查询、EmptyStringCharField（`llm_id`/`rerank_id` 空串 → NULL）、级联删除、分页排序、流式 SSE、NaN/Infinity 清理。
>
> **前置依赖**：已完成 01_startup_migration 中的数据库迁移；默认 Chat LLM 使用本批次配置的 Qwen OpenAI-compatible 模型，默认 embedding 使用 Ollama；另为 TTS/ASR/Rerank 专项准备本批次受控模型或协议 stub，不得因缺少外部服务跳过；至少一个已解析含 chunk 的 Dataset。

> **当前 API 响应约定**：`api/apps/restful_apis/*` 下多数 REST helper 成功和业务错误均返回 HTTP 200，通过响应体 `code=0` / `code!=0` 区分成功与失败；未认证等装饰器级错误仍可返回 HTTP 401，Webhook 安全校验直接返回 HTTP 400。Chat 列表响应为 `data.chats` + `data.total`，不是顶层 `data` 数组。创建类接口当前返回 HTTP 200 + `code=0`，不是 HTTP 201。

> **本批次执行约束**：每个用例先执行 MySQL+Infinity 对照组，再执行 GaussDB+GaussDB 实验组；fixture 名称带运行 ID 和用例 ID。delete_all、软删除、Agent 删除等破坏性用例只能使用本用例专属 fixture，不能删除后续用例的公共 Chat/Agent。业务写入全部走 API，metadata/doc store/Redis 只读核对；不引用任何备份脚本或历史实测结论。

---

## 一、Chat 创建和配置

### TC-CS-001: 创建 Chat — 最小参数
**前置条件**：已登录；tenant 默认 Chat LLM 已按本批次要求配置。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：
   ```json
   {"name": "GaussDB最小Chat"}
   ```
2. 预期响应：HTTP 200 且 `code=0`，返回 `data.id`、`data.name`、`data.dataset_ids=[]`、`data.llm_id`、`data.top_n=6`、`data.top_k=1024`、`data.similarity_threshold=0.1`、`data.vector_similarity_weight=0.3`。
3. 数据库验证：`dialog` 表中 `name='GaussDB最小Chat'`，`tenant_id=<当前用户>`，`status='1'`，`kb_ids` 为 `[]`（JSON 空数组），`llm_id` 继承 tenant 默认 Qwen 模型；默认 `rerank_id` 为空时 MySQL 物理存 `''`、GaussDB 物理存 NULL，API 两组回读 `""`。
**预期结果**：Chat 创建成功，默认值正确填充；模型继承与空 rerank 字段方言语义正确。

---

### TC-CS-002: 创建 Chat — 完整参数含 kb_ids 和 prompt_config
**前置条件**：已有一个含 chunk 的 Dataset（`kb_id` 已知），可用 LLM 模型 `llm_id`，可用 Rerank 模型 `rerank_id`。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：
   ```json
   {
     "name": "完整配置Chat",
     "dataset_ids": ["<kb_id>"],
     "llm_id": "<llm_id>",
     "llm_setting": {"temperature": 0.5, "top_p": 0.8, "max_tokens": 1024},
     "rerank_id": "<rerank_id>",
     "prompt_config": {
       "system": "你是RAG助手",
       "prologue": "你好，请问有什么可以帮助？",
       "parameters": [{"key": "knowledge", "optional": false}],
       "empty_response": "未找到相关内容",
       "quote": true,
       "tts": false,
       "refine_multiturn": true
     },
     "description": "测试完整配置",
     "top_n": 10,
     "top_k": 2048,
     "similarity_threshold": 0.3,
     "vector_similarity_weight": 0.5
   }
   ```
2. 预期响应：HTTP 200 且 `code=0`，返回所有字段正确回显；`dataset_ids` 替代 `kb_ids`，额外返回 `kb_names` 列表。
3. 数据库验证：`dialog.prompt_config` JSON 包含 `system`、`prologue`、`parameters`、`empty_response`、`quote`、`tts`、`refine_multiturn`；`llm_setting` JSON 正确存储；`llm_id`/`rerank_id` 存实际值（非 NULL）。
**预期结果**：所有配置字段在 GaussDB JSONField 中正确存储和回显；`kb_ids` ↔ `dataset_ids` 映射正确。

---

### TC-CS-003: 创建 Chat — name 重复拒绝
**前置条件**：已存在 `name="重复测试Chat"` 的 Dialog。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "重复测试Chat"}`
2. 预期响应：HTTP 200、`code=102`，message 为 `Duplicated chat name in creating chat.`。
**预期结果**：同一租户下完全相同的有效 Chat 名称被拒绝；当前创建路径没有显式 `lower()` 比较，不额外宣称跨数据库大小写不敏感。

---

### TC-CS-004: 创建 Chat — name 超长拒绝
**前置条件**：已登录。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "<256字节UTF-8字符串>"}`
2. 预期响应：HTTP 200、`code=102`，错误信息明确 name 超过 255 bytes。
**预期结果**：`_validate_name` 校验生效，GaussDB CharField(255) 不产生截断写入。

---

### TC-CS-005: 创建 Chat — 传入 tenant_id 被拒绝
**前置条件**：已登录。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "禁止字段测试", "tenant_id": "some-id"}`
2. 预期响应：HTTP 200、`code=102`，message 为 `` `tenant_id` must not be provided.``
**预期结果**：`tenant_id` 被明确拒绝；其他只读字段由 persisted/read-only 字段过滤规则单独处理，不能由本用例外推为都拒绝。

---

### TC-CS-006: 创建 Chat — dataset_ids 中 kb_id 不存在
**前置条件**：已登录。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "无效KB测试", "dataset_ids": ["nonexistent-kb-id"]}`
2. 预期响应：HTTP 200、`code=102`，提示不拥有该 dataset。
**预期结果**：`_validate_dataset_ids` 通过 `KnowledgebaseService.accessible` 校验数据集归属和权限。

---

### TC-CS-007: 创建 Chat — dataset 中无 chunk 数据
**前置条件**：有一个 Dataset 已完成解析但 chunk 数为 0（或 Dataset 为空）。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "空KB测试", "dataset_ids": ["<空dataset_id>"]}`
2. 预期响应：HTTP 200、`code=102`，提示 dataset 不含 parsed file。
**预期结果**：`_validate_dataset_ids` 验证 dataset 含有 chunk。

---

### TC-CS-008: 创建 Chat — 多个 dataset embedding 模型不一致
**前置条件**：两个 Dataset 分别使用不同的 embedding 模型。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "Embedding不一致", "dataset_ids": ["<kb1_id>", "<kb2_id>"]}`
2. 预期响应：HTTP 200、`code=102`，提示 datasets 使用不同 embedding 模型。
**预期结果**：embedding 模型一致性校验生效。

---

### TC-CS-009: 创建 Chat — 无效 llm_id
**前置条件**：已登录。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "无效LLM", "llm_id": "nonexistent-model-xyz"}`
2. 预期响应：HTTP 200、`code=102`，提示 `llm_id` 不存在。
**预期结果**：`_validate_llm_id` 通过 `get_model_config_from_provider_instance` 校验模型有效性。

---

### TC-CS-010: 创建 Chat — 无效 rerank_id（非豁免模型）
**前置条件**：已登录。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "无效Rerank", "rerank_id": "fake-rerank-model"}`
2. 预期响应：HTTP 200、`code=102`，提示 `rerank_id` 不存在。
**预期结果**：`_validate_rerank_id` 校验非豁免 rerank 模型；两个内置豁免模型名会绕过可用性校验（这是代码分支，不代表跳过本测试）。

---

## 二、Chat 查询和分页

### TC-CS-011: 查询 Chat 列表 — 默认分页
**前置条件**：租户下存在 ≥ 3 个有效 Chat。
**步骤**：
1. 发送请求：`GET /api/v1/chats`
2. 预期响应：HTTP 200 且 `code=0`，`data.chats` 数组非空，每条记录含 `id`、`name`、`dataset_ids`、`llm_id`、`kb_names`；`data.total` 字段 ≥ 3；响应中 NaN/Infinity 已替换为 null（`_sanitize_json_floats`）。
**预期结果**：GaussDB 下分页查询正常，JSON 浮点清理生效。

---

### TC-CS-012: 查询 Chat 列表 — 分页参数
**前置条件**：租户下存在 ≥ 5 个有效 Chat。
**步骤**：
1. 发送请求：`GET /api/v1/chats?page=1&page_size=2&orderby=create_time&desc=true`
2. 预期响应：HTTP 200 且 `code=0`，`data.chats` 数组长度 ≤ 2，按 `create_time` 降序排列。
3. 发送请求：`GET /api/v1/chats?page=2&page_size=2&orderby=create_time&desc=true`
4. 预期响应：返回第 2 页数据，与第 1 页无重复。
**预期结果**：GaussDB 分页（LIMIT/OFFSET）和排序正确。

---

### TC-CS-013: 查询 Chat 列表 — 按名称过滤
**前置条件**：存在名为 "过滤测试Chat" 的 Chat。
**步骤**：
1. 发送请求：`GET /api/v1/chats?keywords=过滤测试`
2. 预期响应：HTTP 200 且 `code=0`，`data.chats` 数组中所有记录 `name` 包含 "过滤测试"。
**预期结果**：名称模糊匹配过滤在 GaussDB 下正常工作。

---

### TC-CS-014: 查询单个 Chat
**前置条件**：已知有效 `chat_id`。
**步骤**：
1. 发送请求：`GET /api/v1/chats/<chat_id>`
2. 预期响应：HTTP 200，返回完整 Chat 信息，包含 `dataset_ids`、`kb_names`、`llm_setting`、`prompt_config`。
3. 数据库验证：与 `dialog` 表记录一致。
**预期结果**：单条查询正确返回，GaussDB JSONField 字段反序列化正常。

---

### TC-CS-015: 查询不存在的 Chat
**前置条件**：无。
**步骤**：
1. 发送请求：`GET /api/v1/chats/nonexistent-chat-id`
2. 预期响应：HTTP 200 且响应体 `code=109`、`message="No authorization."`。
**预期结果**：不存在的 Chat 返回业务失败，不返回成功数据。

---

### TC-CS-016: 查询已软删除的 Chat
**前置条件**：一个 Chat 已被删除（`status='0'`）。
**步骤**：
1. 发送请求：`GET /api/v1/chats/<deleted_chat_id>`
2. 预期响应：HTTP 200、`code=109`、`message="No authorization."`。
3. 发送请求：`GET /api/v1/chats`
4. 预期响应：列表中不包含已软删除的 Chat。
**预期结果**：软删除 Chat（`status='0'`）在列表和详情查询中均被过滤。

---

## 三、Chat 更新

### TC-CS-017: PUT 更新指定 Chat 字段
**前置条件**：已知有效 `chat_id`，新 `llm_id`、`rerank_id` 可用。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>`，请求体：
   ```json
   {
     "name": "更新后名称",
     "description": "更新后描述",
     "llm_id": "<new_llm_id>",
     "rerank_id": "<new_rerank_id>",
     "dataset_ids": ["<kb_id>"],
     "prompt_config": {"system": "新系统提示", "prologue": "新开场白"},
     "similarity_threshold": 0.5,
     "vector_similarity_weight": 0.6,
     "top_n": 8,
     "top_k": 512
   }
   ```
2. 预期响应：HTTP 200，所有字段更新成功。
3. 数据库验证：`dialog` 表对应记录所有字段已更新。
**预期结果**：PUT 更新请求中指定的字段；该实现不是“缺失字段清零”的全量替换，未提交字段保持原值。

---

### TC-CS-018: PATCH 部分更新 — prompt_config 合并
**前置条件**：Chat 已有 `prompt_config: {system: "旧", prologue: "旧开场", quote: true}`。
**步骤**：
1. 发送请求：`PATCH /api/v1/chats/<chat_id>`，请求体：
   ```json
   {"prompt_config": {"system": "新系统提示"}}
   ```
2. 预期响应：HTTP 200，`prompt_config.system` 更新为 "新系统提示"，`prologue` 和 `quote` 保持不变。
3. 数据库验证：JSON 合并正确，未传入的字段保留原值。
**预期结果**：PATCH 对 `prompt_config` 执行 JSON 合并（merge），而非替换。GaussDB JSONField 的更新操作正确。

---

### TC-CS-019: PATCH 部分更新 — llm_setting 合并
**前置条件**：Chat 的 `llm_setting` 为 `{temperature: 0.1, top_p: 0.3, max_tokens: 512}`。
**步骤**：
1. 发送请求：`PATCH /api/v1/chats/<chat_id>`，请求体：
   ```json
   {"llm_setting": {"temperature": 0.9}}
   ```
2. 预期响应：HTTP 200，`temperature` 更新为 0.9，`top_p` 和 `max_tokens` 保持原值。
**预期结果**：`llm_setting` JSON 合并正确。

---

### TC-CS-020: 更新 Chat — 清空 llm_id 触发 EmptyStringCharField
**前置条件**：Chat 已有有效 `llm_id`。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>`，请求体中 `llm_id: ""`。
2. 预期响应：HTTP 200 / `code=0`；`_validate_llm_id()` 对空值直接放行。
3. 数据库验证：MySQL 物理存 `''`，GaussDB 物理存 NULL；ORM/API 两组回读 `llm_id=""`。
**预期结果**：EmptyStringCharField 在 GaussDB 下空串 → NULL 的转换正确，不产生数据库约束错误。

---

### TC-CS-021: 更新 Chat — 清空 rerank_id 触发 EmptyStringCharField
**前置条件**：Chat 已有有效 `rerank_id`。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>`，请求体中 `rerank_id: ""`。
2. 预期响应：HTTP 200 / `code=0`。
3. 数据库验证：MySQL 物理存 `''`，GaussDB 物理存 NULL，ORM/API 两组回读 `""`。
**预期结果**：`rerank_id` 的 EmptyStringCharField 行为与 `llm_id` 一致。

---

### TC-CS-022: 更新 Chat — name 重复检查
**前置条件**：租户下已有 "ChatA" 和 "ChatB"。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chatB_id>`，请求体：`{"name": "ChatA"}`
2. 预期响应：HTTP 200、`code=102`、message 为 `Duplicated chat name.`。
**预期结果**：更新为同租户另一个 Chat 的完全相同名称时去重校验生效；大小写差异另受数据库 collation 影响，本用例不外推跨库大小写契约。

---

### TC-CS-023: 更新他人 Chat — 权限拒绝
**前置条件**：用户 B 尝试更新用户 A 创建的 Chat。
**步骤**：
1. 用户 B 发送请求：`PUT /api/v1/chats/<userA_chat_id>`，请求体：`{"name": "篡改名称"}`
2. 预期响应：HTTP 200 且响应体 `code=109` / `message="No authorization."`（`_ensure_owned_chat` 校验 `tenant_id=current_user.id`）。
**预期结果**：非所有者无法更新他人 Chat。

---

## 四、Chat 删除和级联

### TC-CS-024: 删除单个 Chat — 软删除
**前置条件**：已知有效 `chat_id`，该 Chat 下有 Session。
**步骤**：
1. 发送请求：`DELETE /api/v1/chats/<chat_id>`
2. 预期响应：HTTP 200。
3. 数据库验证：`dialog.status` 更新为 `'0'`（INVALID），记录未物理删除。该 Chat 下的 Conversation 记录状态不变（Chat 软删除不级联删 Session）。
**预期结果**：单条删除为软删除，不影响 Session 数据。

---

### TC-CS-025: 批量删除 Chat — ids 列表
**前置条件**：存在 3 个有效 Chat。
**步骤**：
1. 发送请求：`DELETE /api/v1/chats`，请求体：`{"ids": ["<id1>", "<id2>"]}`
2. 预期响应：HTTP 200，删除数量 = 2。
3. 数据库验证：`id1`、`id2` 的 `status='0'`，`id3` 的 `status='1'` 不变。
**预期结果**：批量软删除正确执行。

---

### TC-CS-026: 批量删除 Chat — delete_all
**前置条件**：租户下有多个有效 Chat。
**步骤**：
1. 发送请求：`DELETE /api/v1/chats`，请求体：`{"delete_all": true}`
2. 预期响应：HTTP 200，所有 Chat 被软删除。
3. 数据库验证：该租户所有 Dialog 的 `status='0'`。
**预期结果**：`delete_all` 正确软删除租户全部 Chat。

---

## 五、Session 创建和管理

### TC-CS-027: 创建 Session — 最小参数
**前置条件**：已知有效 `chat_id`，Chat 的 `prompt_config.prologue` 为 "你好，有什么可以帮助？"。
**步骤**：
1. 发送请求：`POST /api/v1/chats/<chat_id>/sessions`，请求体：`{}`
2. 预期响应：HTTP 200 且 `code=0`，返回 `data.id`、`data.name="New session"`、`data.chat_id=<chat_id>`、`data.messages` 包含 prologue 消息。
3. 数据库验证：`conversation` 表新增记录，`dialog_id=<chat_id>`，`name="New session"`，`message` JSON 含 prologue 条目。
**预期结果**：默认名称 "New session" 正确设置，prologue 自动写入 message。GaussDB JSONField（`message`）正确存储数组。

---

### TC-CS-028: 创建 Session — 自定义名称
**前置条件**：已知有效 `chat_id`。
**步骤**：
1. 发送请求：`POST /api/v1/chats/<chat_id>/sessions`，请求体：`{"name": "自定义会话名"}`
2. 预期响应：HTTP 200 且 `code=0`，`data.name="自定义会话名"`。
**预期结果**：自定义名称正确存储。

---

### TC-CS-029: 查询 Session 列表
**前置条件**：Chat 下有 ≥ 3 个 Session。
**步骤**：
1. 发送请求：`GET /api/v1/chats/<chat_id>/sessions?page=1&page_size=2&orderby=create_time&desc=true`
2. 预期响应：HTTP 200，`data` 数组 ≤ 2 条，按 `create_time` 降序；响应字段 `chat_id`（由 `dialog_id` 映射）正确。
**预期结果**：分页、排序、字段映射在 GaussDB 下正确。

---

### TC-CS-030: 查询单个 Session — 含 messages 和 references
**前置条件**：Session 已有对话消息和引用。
**步骤**：
1. 发送请求：`GET /api/v1/chats/<chat_id>/sessions/<session_id>`
2. 预期响应：HTTP 200，`data.messages` 含消息数组（每条含 `role`、`content`、`id`），`data.reference` 含引用信息（`chunks_format`），`data.avatar` 取 Dialog 的 `icon`。
3. 数据库验证：`conversation.message` JSON 数组与响应一致，`conversation.reference` 正确反序列化。
**预期结果**：GaussDB JSONField 大 JSON 数据（消息历史 + 引用）正确存储和返回。

---

### TC-CS-031: 更新 Session — 仅允许修改名称
**前置条件**：已知有效 `session_id`。
**步骤**：
1. 发送请求：`PATCH /api/v1/chats/<chat_id>/sessions/<session_id>`，请求体：`{"name": "新会话名"}`
2. 预期响应：HTTP 200，`name` 更新成功。
3. 发送请求：`PATCH /api/v1/chats/<chat_id>/sessions/<session_id>`，请求体：`{"messages": [{"role":"user","content":"注入"}]}`
4. 预期响应：HTTP 200、`code=102`（不允许修改消息）。
5. 发送请求：`PATCH /api/v1/chats/<chat_id>/sessions/<session_id>`，请求体：`{"reference": [{"id":"fake"}]}`
6. 预期响应：HTTP 200、`code=102`。
**预期结果**：Session 仅允许更新 `name`，`messages` 和 `reference` 被拒绝。

---

### TC-CS-032: 批量删除 Session
**前置条件**：Chat 下有 ≥ 3 个 Session。
**步骤**：
1. 发送请求：`DELETE /api/v1/chats/<chat_id>/sessions`，请求体：`{"ids": ["<sid1>", "<sid2>"]}`
2. 预期响应：HTTP 200。
3. 数据库验证：`conversation` 表中 `sid1`、`sid2` 记录已物理删除。上传文件 blob 已从存储中清理。
**预期结果**：Session 为物理删除，关联文件 blob 一并清理。

---

### TC-CS-033: 批量删除 Session — delete_all
**前置条件**：Chat 下有多个 Session。
**步骤**：
1. 发送请求：`DELETE /api/v1/chats/<chat_id>/sessions`，请求体：`{"delete_all": true}`
2. 预期响应：HTTP 200，所有 Session 被删除。
**预期结果**：`delete_all` 正确删除 Chat 下全部 Session。

---

### TC-CS-034: 查询不存在的 Session
**前置条件**：已知有效 `chat_id`。
**步骤**：
1. 发送请求：`GET /api/v1/chats/<chat_id>/sessions/nonexistent-session-id`
2. 预期响应：HTTP 200、`code=102`。
**预期结果**：不存在的 Session 返回业务失败，不返回成功数据。

---

## 六、Session 消息操作

### TC-CS-035: 删除消息对
**前置条件**：Session 中已有多轮对话（≥ 2 对 user/assistant 消息）。
**步骤**：
1. 发送请求：`DELETE /api/v1/chats/<chat_id>/sessions/<session_id>/messages/<msg_id>`
2. 预期响应：HTTP 200。
3. 数据库验证：`conversation.message` JSON 数组中对应的 user 和 assistant 消息对已移除；`conversation.reference` 中对应索引的引用已移除。
**预期结果**：消息对（user + assistant）和对应引用正确删除，JSON 数组索引维护正确。

---

### TC-CS-036: 消息反馈 — 点赞（thumb up）
**前置条件**：Session 中有 assistant 消息。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>/sessions/<session_id>/messages/<msg_id>/feedback`，请求体：
   ```json
   {"thumbup": true, "feedback": "回答很有帮助"}
   ```
2. 预期响应：HTTP 200。
3. 数据库验证：`conversation.message` 对应 assistant 消息写入 `thumbup=true`。本批次两组以 `CHUNK_FEEDBACK_ENABLED=true` 启动；若该消息带引用，再从 doc store 只读确认被分配到的 chunk `pagerank_fea` 原子增加。
**预期结果**：点赞状态持久化；启用的 chunk feedback 正确调整引用 chunk 权重（该服务没有独立 metadata 表，不能检查虚构记录）。

---

### TC-CS-037: 消息反馈 — 点踩（thumb down）
**前置条件**：Session 中有 assistant 消息。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>/sessions/<session_id>/messages/<msg_id>/feedback`，请求体：
   ```json
   {"thumbup": false, "feedback": "回答不准确"}
   ```
2. 预期响应：HTTP 200。
**预期结果**：`conversation.message` 写入 `thumbup=false` 和反馈文本；若先前为点赞，doc-store 权重先撤销旧方向再应用点踩，最终值按实际引用与 relevance budget 核对。

---

### TC-CS-038: 消息反馈 — 缺少 thumbup 字段
**前置条件**：无。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>/sessions/<session_id>/messages/<msg_id>/feedback`，请求体：`{"feedback": "缺少thumbup"}`
2. 预期响应：HTTP 200、`code=102`，提示 `thumbup must be a boolean`。
**预期结果**：参数校验生效。

---

## 七、Completion 对话完成

### TC-CS-039: 流式 Completion — 新建 Session
**前置条件**：有效 `chat_id`，已配置 LLM 模型和 Dataset。
**步骤**：
1. 发送请求：`POST /api/v1/chat/completions`，请求体：
   ```json
   {
     "chat_id": "<chat_id>",
     "messages": [{"role": "user", "content": "你好"}],
     "stream": true
   }
   ```
2. 预期响应：SSE 流式响应，`Content-Type: text/event-stream`，多个 `data: {...}` 事件，最后一个结束事件为 `data: {"code": 0, "message": "", "data": true}`（当前 REST 实现不返回 OpenAI 风格的 `data: [DONE]`）。
3. 数据库验证：`conversation` 表新增 Session 记录，`message` JSON 含 user 和 assistant 消息。
**预期结果**：流式响应格式正确，新 Session 自动创建并持久化到 GaussDB。

---

### TC-CS-040: 流式 Completion — 续接已有 Session
**前置条件**：已有 Session（`session_id`），内含历史消息。
**步骤**：
1. 发送请求：`POST /api/v1/chat/completions`，请求体：
   ```json
   {
     "chat_id": "<chat_id>",
     "session_id": "<session_id>",
     "messages": [{"role": "user", "content": "请继续"}],
     "stream": true
   }
   ```
2. 预期响应：SSE 流式，assistant 回复基于历史上下文。
3. 数据库验证：`conversation.message` JSON 数组追加新的 user + assistant 消息对。
**预期结果**：Session 续接正确，历史消息作为上下文传递，JSON 数组追加正确。

---

### TC-CS-041: 非流式 Completion
**前置条件**：有效 `chat_id`。
**步骤**：
1. 发送请求：`POST /api/v1/chat/completions`，请求体：
   ```json
   {
     "chat_id": "<chat_id>",
     "messages": [{"role": "user", "content": "什么是RAG？"}],
     "stream": false
   }
   ```
2. 预期响应：HTTP 200，JSON 响应含 `data.answer`、`data.reference`（如有引用）。
**预期结果**：非流式模式返回完整 JSON 响应。

---

### TC-CS-042: Completion — messages 格式校验
**前置条件**：无。
**步骤**：
1. 发送请求：`POST /api/v1/chat/completions`，请求体：`{"chat_id": "<id>", "messages": "not a list", "stream": false}`
2. 预期响应：HTTP 200、`code=101`，messages 必须为数组。
3. 发送请求：`POST /api/v1/chat/completions`，请求体：`{"chat_id": "<id>", "messages": [], "stream": false}`
4. 预期响应：HTTP 200、`code=101`，messages 不能为空。
5. 发送请求：`POST /api/v1/chat/completions`，请求体：`{"chat_id": "<id>", "messages": [{"role": "assistant", "content": "hi"}], "stream": false}`
6. 预期响应：HTTP 200、`code=101`，最后一条消息必须是 user。
**预期结果**：messages 校验（非空数组、每项含 role/content、末尾为 user）全部生效。

---

### TC-CS-043: Completion — question 回退（无 messages）
**前置条件**：有效 `chat_id`。
**步骤**：
1. 发送请求：`POST /api/v1/chat/completions`，请求体：
   ```json
   {"chat_id": "<chat_id>", "question": "测试question回退", "stream": false}
   ```
2. 预期响应：HTTP 200，使用 `question` 字段作为用户输入生成回复。
**预期结果**：当 `messages` 未提供时，`question` 字段作为回退输入。

---

### TC-CS-044: Completion — pass_all_history_messages 标志
**前置条件**：Session 有多轮历史消息。
**步骤**：
1. 从 Session API 读取当前历史，构造一个由客户端显式提交的完整 `messages` 数组（包含多轮历史并以新 user 消息结尾），发送：
   ```json
   {
     "chat_id": "<chat_id>",
     "session_id": "<session_id>",
     "messages": [
       {"role": "user", "content": "第一问", "id": "<m1>"},
       {"role": "assistant", "content": "第一答", "id": "<m1>"},
       {"role": "user", "content": "总结之前的对话", "id": "<m2>"}
     ],
     "stream": false,
     "pass_all_history_messages": true
   }
   ```
2. 预期响应：HTTP 200；数据库只读确认 Conversation 的 message 以客户端提交的完整数组为基线并追加 assistant 回复。
**预期结果**：`pass_all_history_messages=true` 表示信任并使用客户端提交的完整 `messages`，不是服务端自动把只含新问题的数组与旧历史拼接；不得用单消息请求误判该分支。

---

## 八、音频和思维导图

### TC-CS-045: TTS 文本转语音
**前置条件**：租户已配置 TTS 模型。
**步骤**：
1. 发送请求：`POST /api/v1/chat/audio/speech`，请求体：
   ```json
   {"text": "你好，这是语音测试", "chat_id": "<chat_id>"}
   ```
2. 预期响应：HTTP 200，`Content-Type: audio/mpeg`，响应体为音频二进制流。
**预期结果**：TTS 流式返回音频数据。

---

### TC-CS-046: ASR 语音转文本
**前置条件**：准备一段音频文件，租户已配置 ASR 模型。
**步骤**：
1. 发送请求：`POST /api/v1/chat/audio/transcription`，`Content-Type: multipart/form-data`，字段 `file` 上传音频文件。
2. 预期响应：HTTP 200，返回识别文本。
**预期结果**：ASR 正确将音频转换为文本。

---

### TC-CS-047: 思维导图生成
**前置条件**：有效 Dataset 含 chunk。
**步骤**：
1. 发送请求：`POST /api/v1/chat/mindmap`，请求体：
   ```json
   {"question": "RAGFlow的架构", "kb_ids": ["<kb_id>"]}
   ```
2. 预期响应：HTTP 200，返回思维导图 JSON 结构（含节点和层级关系）。
**预期结果**：思维导图基于 Dataset 内容正确生成。

---

## 九、推荐问题

### TC-CS-048: 获取推荐问题
**前置条件**：tenant 默认 Chat LLM 可用；可选 `search_id` 用于读取 Search 配置，`chat_id` 不是该端点的选择参数。
**步骤**：
1. 发送请求：`POST /api/v1/chat/recommendation`，请求体：
   ```json
   {"question": "什么是文档解析？"}
   ```
2. 预期响应：HTTP 200，返回推荐问题列表（数组）。
**预期结果**：基于用户问题生成相关推荐问题。

---

## 十、Agent/Canvas CRUD

### TC-CS-049: 创建 Agent — 最小参数
**前置条件**：已登录。
**步骤**：
1. 发送请求：`POST /api/v1/agents`，请求体：
   ```json
   {
     "title": "测试Agent",
     "dsl": {
       "components": {
         "begin": {
           "obj": {"component_name": "Begin", "params": {}},
           "downstream": ["message"],
           "upstream": []
         },
         "message": {
           "obj": {"component_name": "Message", "params": {"content": ["{sys.query}"]}},
           "downstream": [],
           "upstream": ["begin"]
         }
       },
      "history": [],
      "messages": [],
      "reference": [],
      "retrieval": {"chunks": [], "doc_aggs": []},
      "path": [],
      "answer": [],
      "globals": {
         "sys.query": "",
         "sys.user_id": "",
         "sys.conversation_turns": 0,
         "sys.files": []
       },
       "variables": {}
     }
   }
   ```
2. 预期响应：HTTP 200 且响应体 `code=0`，返回 `data.id`、`data.title`、`data.canvas_category="agent_canvas"`。
3. 数据库验证：`user_canvas` 表新增记录且 `dsl` 为完整 JSON；请求未传 `tags` 时，MySQL 物理保存空串，GaussDB A 模式物理保存 `NULL`，但 ORM 应向应用层还原为 `""`。
**预期结果**：不传 `tags` 的最小 Agent 创建在两组均成功；两组应用层空值语义一致，物理存储符合各自数据库约束。

本用例完成后，另用相同 DSL 且显式 `"tags":"fresh-agent"` 通过 API 创建后续 Agent 用例的公共 fixture，保证即使最小创建暴露字段缺陷，后续用例仍可继续执行；不得直接写数据库绕过创建 API。

---

### TC-CS-050: 创建 Agent — 标题重复拒绝
**前置条件**：已存在 `title="重复Agent"` 且 `canvas_category="agent_canvas"` 的 Agent。
**步骤**：
1. 发送请求：`POST /api/v1/agents`，请求体：`{"title": "重复Agent", "dsl": {...}}`
2. 预期响应：HTTP 200、`code=102`，标题重复拒绝。
**预期结果**：同一租户、同一 `canvas_category` 下标题大小写不敏感去重。

---

### TC-CS-051: 查询 Agent 列表
**前置条件**：存在 ≥ 2 个 Agent。
**步骤**：
1. 发送请求：`GET /api/v1/agents?page=1&page_size=10`
2. 预期响应：HTTP 200 且 `code=0`，`data.canvas` 数组含 Agent 列表，`data.total` 正确。
**预期结果**：Agent 列表分页查询正常。

---

### TC-CS-052: 查询 Agent 列表 — 按 tags 过滤
**前置条件**：Agent 已设置标签。
**步骤**：
1. 发送请求：`GET /api/v1/agents?tags=测试标签`
2. 预期响应：HTTP 200，返回列表仅含标签匹配的 Agent。
**预期结果**：标签过滤在 GaussDB 下正确工作（`tags` 为 CharField(512) 逗号分隔）。

---

### TC-CS-053: 查询单个 Agent — 含 DSL、版本、数据集
**前置条件**：已知有效 `agent_id`。
**步骤**：
1. 发送请求：`GET /api/v1/agents/<agent_id>`
2. 预期响应：HTTP 200，返回 `data.dsl`（完整 Canvas DSL）及 `last_publish_time`；普通 Agent 不内嵌 `versions` 或 `datasets`，版本使用专门端点查询，只有 DataFlow Canvas 才附加关联 `datasets`。
3. 数据库验证：DSL 与 `user_canvas.dsl` JSONField 一致。
**预期结果**：GaussDB 下大 JSON（DSL）正确存储和反序列化。

---

### TC-CS-054: 更新 Agent — 修改 DSL
**前置条件**：已知有效 `agent_id`，拥有所有权。
**步骤**：
1. 发送请求：`PUT /api/v1/agents/<agent_id>`，请求体：
   ```json
   {
     "title": "更新后Agent",
     "dsl": {
       "components": {
         "begin": {
           "obj": {"component_name": "Begin", "params": {}},
           "downstream": ["message"],
           "upstream": []
         },
         "message": {
           "obj": {"component_name": "Message", "params": {"content": ["{sys.query}"]}},
           "downstream": [],
           "upstream": ["begin"]
         }
       },
       "history": [],
       "retrieval": [],
       "path": [],
       "globals": {"sys.query": "", "plan_probe": "updated"},
       "variables": {}
     }
   }
   ```
2. 预期响应：HTTP 200。
3. 数据库验证：`user_canvas` 的 `title` 和 `dsl` 更新；`user_canvas_version` 新增版本记录；CanvasReplica 同步更新。
**预期结果**：Agent 更新触发版本记录和 Replica 同步。

---

### TC-CS-055: 删除 Agent — 仅所有者可操作
**前置条件**：Agent 所有者登录。
**步骤**：
1. 发送请求：`DELETE /api/v1/agents/<agent_id>`
2. 预期响应：HTTP 200。
3. 删除前保存版本与 Session ID；数据库验证 `user_canvas` 记录物理删除，同时核对 `user_canvas_version` 和 `api_4_conversation` 是否残留。
**预期结果**：Agent 删除后不应留下不可达版本/Session/Replica 数据。当前路由代码只显式删除 `user_canvas`，这是待本批次实测的一致性风险；若残留则记录产品缺陷，不能把“状态不变或已清理”写成双重通过标准。

---

### TC-CS-056: 删除 Agent — 非所有者拒绝
**前置条件**：用户 B 尝试删除用户 A 的 Agent。
**步骤**：
1. 用户 B 发送请求：`DELETE /api/v1/agents/<userA_agent_id>`
2. 预期响应：HTTP 200，响应体 `code=103`、`message="Only the owner of the agent is authorized for this operation."`；`_require_canvas_owner_sync` 校验 `user_id=tenant_id`。
**预期结果**：非所有者删除被拒绝。

---

### TC-CS-057: 重置 Agent DSL
**前置条件**：Agent 已被修改过。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/reset`
2. 预期响应：HTTP 200，返回 reset 后 DSL。
**预期结果**：`Canvas.reset()` 清空执行 path、history、retrieval、memory 和系统变量/组件运行态，并同步 Redis Replica；它不会从模板恢复用户已编辑掉的组件结构，不能按“回到初始模板”判定。

---

## 十一、Agent 模板

### TC-CS-058: 查询 Agent 模板列表
**前置条件**：`canvas_template` 表中有预置模板数据。
**步骤**：
1. 发送请求：`GET /api/v1/agents/templates`
2. 预期响应：HTTP 200，`data` 数组含模板列表，每个模板含 `id`、`title`（JSON 类型，含多语言）、`description`（JSON 类型）、`canvas_category`、`dsl`。
3. 数据库验证：`canvas_template.title` 和 `canvas_template.description` 为 JSONField（非字符串），GaussDB 下正确反序列化。
**预期结果**：模板列表正确返回，JSONField 类型的 title/description 正确解析。

---

### TC-CS-059: 查询 Agent 内置 Prompt 模板
**前置条件**：无。
**步骤**：
1. 发送请求：`GET /api/v1/agents/prompts`
2. 预期响应：HTTP 200，返回内置 prompt 模板（`task_analysis`、`plan_generation`、`reflection`、`citation_guidelines`）。
**预期结果**：内置 prompt 模板正确返回。

---

## 十二、Agent 版本管理

### TC-CS-060: 查询 Agent 版本列表
**前置条件**：Agent 已保存多次（产生多个版本）。
**步骤**：
1. 发送请求：`GET /api/v1/agents/<agent_id>/versions`
2. 预期响应：HTTP 200，`data` 数组按时间降序排列，每条含 `id`、`title`、`description`、`release`、`create_time`。
3. 数据库验证：`user_canvas_version` 表中 `user_canvas_id=<agent_id>` 的记录与响应一致。
**预期结果**：版本列表正确返回，GaussDB 下 `user_canvas_version` 查询正常。

---

### TC-CS-061: 查询单个 Agent 版本
**前置条件**：已知有效 `agent_id` 和 `version_id`。
**步骤**：
1. 发送请求：`GET /api/v1/agents/<agent_id>/versions/<version_id>`
2. 预期响应：HTTP 200，返回完整版本信息含 `dsl` JSON（DSL 快照）。
3. 数据库验证：`user_canvas_version.dsl` JSONField 与响应一致。
**预期结果**：版本详情含 DSL 快照正确返回。

---

## 十三、Agent Session 和日志

### TC-CS-062: 创建 Agent Session
**前置条件**：有效 `agent_id`。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/sessions`，请求体：`{"name": "Agent测试会话"}`
2. 预期响应：HTTP 200 且 `code=0`，返回 `data.id`、`data.name`、`data.dsl`（Canvas DSL 快照）。
3. 数据库验证：`api_4_conversation` 表新增记录，`dialog_id=<agent_id>`，`source` 字段正确设置，`dsl` JSONField 存 DSL 快照。
**预期结果**：Agent Session 创建成功，`api_4_conversation` 表 GaussDB JSONField（`dsl`、`message`、`reference`）正确存储。

---

### TC-CS-063: 查询 Agent Session 列表
**前置条件**：Agent 下有 ≥ 2 个 Session。
**步骤**：
1. 发送请求：`GET /api/v1/agents/<agent_id>/sessions?page=1&page_size=10`
2. 预期响应：HTTP 200，`data` 数组含 Session 列表，每条含 `id`、`name`、`source`、`round`、`duration`、`thumb_up`。
**预期结果**：Agent Session 列表分页查询正常。

---

### TC-CS-064: 删除单个 Agent Session
**前置条件**：已知有效 `agent_id` 和 `session_id`。
**步骤**：
1. 发送请求：`DELETE /api/v1/agents/<agent_id>/sessions/<session_id>`
2. 预期响应：HTTP 200。
3. 数据库验证：`api_4_conversation` 记录已物理删除。
**预期结果**：Agent Session 物理删除成功。

---

### TC-CS-065: 批量删除 Agent Session
**前置条件**：Agent 下有多个 Session。
**步骤**：
1. 发送请求：`DELETE /api/v1/agents/<agent_id>/sessions`，请求体：`{"ids": ["<sid1>", "<sid2>"]}`
2. 预期响应：HTTP 200。
3. 数据库验证：指定 Session 记录已删除。
**预期结果**：批量删除 Agent Session 正确执行。

---

### TC-CS-066: 查询 Agent 执行日志
**前置条件**：Agent Session 已执行过（Redis 中有日志）。
**步骤**：
1. 发送请求：`GET /api/v1/agents/<agent_id>/logs/<message_id>`
2. 预期响应：HTTP 200，返回执行日志详情。
**预期结果**：日志从 Redis 正确读取和返回。

---

## 十四、Agent Webhook

### TC-CS-067: Webhook — POST 触发
**前置条件**：Agent DSL 中有 `Begin` 组件且 `mode="Webhook"`，允许 POST/GET，安全配置 `auth_type="none", allow_anonymous=true`，`execution_mode="Immediately"`，响应配置固定为 HTTP 202 和可判定 JSON body。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/webhook`，`Content-Type: application/json`，请求体：`{"input": "webhook数据"}`
2. 预期响应：HTTP 202，body 与 DSL `response.body_template` 完全一致；后台 Canvas 执行由日志轮询证明。
**预期结果**：Webhook POST 立即返回配置响应并在后台执行；该路径不创建 Chat/Agent Session，不能检查虚构的 Session 写入。

---

### TC-CS-068: Webhook — GET 触发
**前置条件**：同 TC-CS-067。
**步骤**：
1. 发送请求：`GET /api/v1/agents/<agent_id>/webhook?param1=value1`
2. 预期响应：HTTP 202，返回同一配置响应，并由 webhook trace 证明 GET 参数进入执行 payload。
**预期结果**：GET 方法正确触发 Webhook。

---

### TC-CS-069: Webhook — Token 认证
**前置条件**：Agent Webhook 配置 `auth_type="token"`，`security.token.token_header="X-Webhook-Token"`，`security.token.token_value="secret-token-123"`。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/webhook`，Header: `X-Webhook-Token: secret-token-123`，请求体：`{}`
2. 预期响应：HTTP 200，执行成功。
3. 发送请求：`POST /api/v1/agents/<agent_id>/webhook`，无 `X-Webhook-Token` Header 或值错误。
4. 预期响应：HTTP 400，错误信息包含 token 认证失败。
**预期结果**：Token 认证正确校验，缺失或错误 token 被拒绝。

---

### TC-CS-070: Webhook — 无 Begin Webhook 组件拒绝
**前置条件**：Agent DSL 的 `Begin` 组件 `mode` 不为 `"Webhook"`。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/webhook`
2. 预期响应：HTTP 400，提示 Agent 未配置 Webhook 模式。
**预期结果**：未配置 Webhook 的 Agent 拒绝 Webhook 请求。

---

### TC-CS-071: Webhook — 测试端点（仅所有者）
**前置条件**：Agent 所有者登录。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/webhook/test`，请求体：`{"input": "测试数据"}`
2. 预期响应：HTTP 200，返回测试结果。
3. 非所有者发送相同请求。
4. 预期响应：HTTP 200，响应体 `code=103`、message 为 only-owner 拒绝信息。
**预期结果**：Webhook 测试端点仅所有者可访问。

---

### TC-CS-072: Webhook — 查询执行日志
**前置条件**：Webhook 已触发过。
**步骤**：
1. 先发送 `GET /api/v1/agents/<agent_id>/webhook/logs` 取得 `data.next_since_ts`。
2. 通过 `/webhook/test` 触发一次；再以 `since_ts=<上一步时间>` 轮询日志，直到 `finished=true` 或达到明确超时。
3. 预期响应：HTTP 200，返回该次 Webhook 的 `webhook_id`、事件和结束状态。
**预期结果**：Webhook 执行日志正确返回。

---

### TC-CS-073: Webhook — 请求体超过 max_body_size
**前置条件**：Webhook `security.max_body_size="1MB"`。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/webhook`，请求体 > 1MB。
2. 预期响应：HTTP 400，提示请求体过大（当前实现返回 `Request body too large`）。
**预期结果**：`max_body_size` 限制生效，不超过 10MB 硬上限。

---

## 十五、Bot 操作

### TC-CS-074: Chatbot Completion
**前置条件**：已创建共享 Chatbot（Dialog），拥有 beta API token。
**步骤**：
1. 首次发送请求创建 iframe 会话：`POST /api/v1/chatbots/<dialog_id>/completions`，请求体：
   ```json
   {
     "question": "你好",
     "user_id": "<beta_token_tenant_id>",
     "stream": true
   }
   ```
2. 预期响应：SSE 流式响应，返回 prologue 和 `session_id`，最后结束事件为 `data: {"code": 0, "message": "", "data": true}`。
3. 使用上一步返回的 `session_id` 再次发送请求：
   ```json
   {
     "question": "请继续回答",
     "user_id": "<beta_token_tenant_id>",
     "session_id": "<session_id>",
     "stream": true
   }
   ```
4. 预期响应：SSE 流式返回 assistant 回复。
**预期结果**：Chatbot 端点正确创建 iframe 会话并返回流式 Completion。

---

### TC-CS-075: Chatbot 信息查询
**前置条件**：有效 `dialog_id`。
**步骤**：
1. 发送请求：`GET /api/v1/chatbots/<dialog_id>/info`
2. 预期响应：HTTP 200，返回 `title`、`avatar`、`prologue`、`llm_id`、`has_tavily_key`。
**预期结果**：Chatbot 元数据正确返回。

---

### TC-CS-076: Agentbot Completion
**前置条件**：已创建共享 Agent，拥有 beta API token。
**步骤**：
1. 发送请求：`POST /api/v1/agentbots/<agent_id>/completions`，请求体：
   ```json
   {"query": "测试agentbot", "stream": true}
   ```
2. 预期响应：SSE 流式响应。
**预期结果**：Agentbot 端点正确返回流式 Completion。

---

### TC-CS-077: Agentbot 输入表单查询
**前置条件**：有效 `agent_id`。
**步骤**：
1. 发送请求：`GET /api/v1/agentbots/<agent_id>/inputs`
2. 预期响应：HTTP 200，返回 `input_form`、`prologue`、`mode`、`title`、`avatar`。
**预期结果**：Agent 输入表单和元数据正确返回。

---

### TC-CS-078: Searchbot 问答
**前置条件**：已配置搜索 Bot，拥有 beta API token。
**步骤**：
1. 发送请求：`POST /api/v1/searchbots/ask`，请求体：
   ```json
   {"question": "什么是向量检索？", "kb_ids": ["<kb_id>"]}
   ```
2. 预期响应：SSE 流式响应，包含基于 Dataset 的回答。
**预期结果**：Searchbot 问答流式响应正常。

---

### TC-CS-079: Searchbot 检索测试
**前置条件**：已配置搜索 Bot。
**步骤**：
1. 发送请求：`POST /api/v1/searchbots/retrieval_test`，请求体：
   ```json
   {"question": "文档解析流程", "kb_id": "<kb_id>"}
   ```
2. 预期响应：HTTP 200，返回检索结果（chunk 列表和分数）。
**预期结果**：检索测试返回相关 chunk。

---

### TC-CS-080: Searchbot 思维导图
**前置条件**：已配置搜索 Bot。
**步骤**：
1. 发送请求：`POST /api/v1/searchbots/mindmap`，请求体：
   ```json
   {"question": "系统架构", "kb_ids": ["<kb_id>"]}
   ```
2. 预期响应：HTTP 200，返回思维导图结构。
**预期结果**：Searchbot 思维导图生成正常。

---

## 十六、空字符串兼容字段验证

### TC-CS-081: Chat llm_id 空串在 GaussDB 存为 NULL
**前置条件**：两组均执行，Chat 初始有有效 `llm_id`。
**步骤**：
1. 通过 `PUT /api/v1/chats/<chat_id>` 提交 `{"llm_id":""}`；创建时省略该字段会继承 tenant 默认模型，不能用于空串测试。
2. 直接查询数据库：`SELECT llm_id FROM dialog WHERE id = '<chat_id>'`
3. 验证结果：MySQL 为 `''`，GaussDB A/ORA 为 NULL。
4. 通过 API 查询该 Chat：`GET /api/v1/chats/<chat_id>`
5. 验证两组响应中 `llm_id` 均为 `""`。
**预期结果**：EmptyStringCharField 只改变实验组物理存储，ORM/API 契约保持空字符串。

---

### TC-CS-082: Chat rerank_id 空串在 GaussDB 存为 NULL
**前置条件**：两组均执行。
**步骤**：
1. 同 TC-CS-081 流程，验证 `rerank_id` 字段。
2. 数据库查询：`SELECT rerank_id FROM dialog WHERE id = '<chat_id>'`
3. 验证 MySQL 为 `''`、GaussDB 为 NULL，API 两组均为 `""`。
**预期结果**：`rerank_id` 的 EmptyStringCharField 行为一致。

---

### TC-CS-083: 创建 Chat 后更新 llm_id 从 NULL → 有效值
**前置条件**：已通过 TC-CS-081 将两组 Chat 的应用层 `llm_id` 清空（MySQL `''`、GaussDB NULL）。
**步骤**：
1. 发送请求：`PUT /api/v1/chats/<chat_id>`，请求体：`{"llm_id": "<valid_llm_id>"}`
2. 预期响应：HTTP 200。
3. 数据库验证：空值更新为有效字符串。
4. 再次更新为空串：`PUT /api/v1/chats/<chat_id>`，请求体：`{"llm_id": ""}`
5. 数据库验证：MySQL 回到 `''`，GaussDB 回到 NULL；API 均回读 `""`。
**预期结果**：EmptyStringCharField 支持应用层空串 ↔ 有效值的往返转换。

---

### TC-CS-084: JSONField 中嵌套空串值不被 EmptyStringCharField 处理
**前置条件**：无。
**步骤**：
1. 先创建 Chat，再通过 PATCH 设置 `prompt_config: {"system": "", "empty_response": ""}`。创建路径会把空 `system` 替换成默认系统提示，因此不能用 create 请求制造该 fixture。
2. 数据库与 API 验证：`prompt_config` JSONField 中 `system` 和 `empty_response` 均保持 JSON 空串 `""`。
**预期结果**：EmptyStringCharField 仅作用于列级别字段（`llm_id`、`rerank_id`），不影响 JSONField 内部的空串值。

---

## 十七、边界和异常处理

### TC-CS-085: 未认证访问
**前置条件**：不携带认证 token。
**步骤**：
1. 发送请求：`GET /api/v1/chats`（无 Authorization Header）
2. 预期响应：HTTP 401。
3. 发送请求：`POST /api/v1/agents`（无 Authorization Header）
4. 预期响应：HTTP 401。
**预期结果**：所有 Chat/Session/Agent 端点在未认证时返回 401。

---

### TC-CS-086: Chat 名称含特殊字符
**前置条件**：无。
**步骤**：
1. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "Chat<script>alert(1)</script>"}`
2. 预期响应：HTTP 200 且 `code=0`，名称原样存储（XSS 由前端处理）。
3. 发送请求：`POST /api/v1/chats`，请求体：`{"name": "Chat🎉Emoji"}`
4. 预期响应：HTTP 200 且 `code=0`，UTF-8 emoji 正确存储。
**预期结果**：特殊字符和 emoji 在 GaussDB 下正确存储（CharField(255) 按字节限制）。

---

### TC-CS-087: 大量消息的 Session Completion
**前置条件**：通过 Completion API（不得直接写 JSON 列）让专用 Session 已有 50 轮 user/assistant 对话，并记录请求前 `message` 数组长度；初始 prologue assistant 也计入数组。
**步骤**：
1. 发送请求：`POST /api/v1/chat/completions`，请求体含新 user 消息，`stream: false`。
2. 预期响应：HTTP 200，Completion 正常返回。
3. 数据库验证：`conversation.message` 数组长度在基线基础上增加 2（新 user + assistant）；若含初始 prologue，50 轮后的常见最终总数为 103，而不是固定 102。逐条 ID/role/content 均可回读且无截断。
**预期结果**：大 JSON 数组（100+ 消息）在 GaussDB JSONField 中正确存储和查询。

---

### TC-CS-088: 并发创建 Session
**前置条件**：有效 `chat_id`。
**步骤**：
1. 同时发送 5 个请求：`POST /api/v1/chats/<chat_id>/sessions`，请求体：`{"name": "并发会话N"}`
2. 预期响应：全部 HTTP 200 且 `code=0`，5 个不同 Session ID。
3. 数据库验证：`conversation` 表新增 5 条记录，无主键冲突。
**预期结果**：并发 Session 创建无竞态条件，GaussDB 主键生成无冲突。

---

### TC-CS-089: Completion 响应中 NaN/Infinity 清理
**前置条件**：无须修改产品代码或伪造历史结果。
**步骤**：
1. 在隔离的测试进程直接调用当前 `_sanitize_json_floats`，输入包含嵌套 Python/NumPy NaN、Infinity、-Infinity，断言全部变为 None 且其他字段不变。
2. 再发送真实 `POST /api/v1/chat/completions`（stream false/true 各一次），用拒绝非标准常量的严格 JSON parser 解析响应和每个 SSE data 帧，确认没有 NaN/Infinity 字面量。
**预期结果**：GaussDB 查询结果中的 NaN/Infinity 在 JSON 序列化前被清理。

---

### TC-CS-090: Agent DSL 超大 JSON
**前置条件**：无。
**步骤**：
1. 创建 Agent，DSL 含 100 个组件节点，总 DSL JSON > 100KB。
2. 发送请求：`POST /api/v1/agents`，请求体含大 DSL，并显式设置非空 `tags="large-dsl"`，使本用例只验证大 JSON 存储。
3. 预期响应：HTTP 200 且 `code=0`。
4. 发送请求：`GET /api/v1/agents/<agent_id>`
5. 预期响应：HTTP 200，`data.dsl` 完整返回，无截断。
6. 数据库验证：`user_canvas.dsl` JSONField 完整存储，GaussDB JSON 类型无大小限制问题。
**预期结果**：大 JSON（100KB+）在 GaussDB JSONField 中正确存储和返回。

---

### TC-CS-091: Agent Chat Completion — 流式和非流式
**前置条件**：有效 Agent（含 LLM 组件）。
**步骤**：
1. 流式请求：`POST /api/v1/agents/chat/completions`，请求体：
   ```json
   {"agent_id": "<agent_id>", "query": "测试流式", "stream": true}
   ```
2. 预期响应：SSE 流式，`data: {...}` 事件序列。
3. 非流式请求：`POST /api/v1/agents/chat/completions`，请求体：
   ```json
   {"agent_id": "<agent_id>", "query": "测试非流式", "stream": false}
   ```
4. 预期响应：HTTP 200，JSON 含完整回复。
**预期结果**：Agent Completion 流式/非流式模式均正常，GaussDB 下 Session 数据正确更新。

---

### TC-CS-092: Agent 文件上传和下载
**前置条件**：有效 `agent_id`。
**步骤**：
1. 上传请求：`POST /api/v1/agents/<agent_id>/upload`，`Content-Type: multipart/form-data`，字段 `file` 上传文件。
2. 预期响应：HTTP 200，返回文件 ID。
3. 下载请求：`GET /api/v1/agents/download?id=<file_id>`
4. 预期响应：HTTP 200，文件内容正确返回。
**预期结果**：Agent 文件上传/下载在 GaussDB 环境下正常。

---

### TC-CS-093: Agent 组件调试
**前置条件**：Agent 含 LLM 组件。
**步骤**：
1. 发送请求：`POST /api/v1/agents/<agent_id>/components/<component_id>/debug`，请求体必须含 `params` 对象，并按组件 input-form 提供 `{input_name:{"value":...}}`。
2. 预期响应：HTTP 200，返回组件调试输出。
**预期结果**：单组件调试功能正常。

---

### TC-CS-094: Agent Tag 管理
**前置条件**：已存在带标签的 Agent。
**步骤**：
1. 查询标签：`GET /api/v1/agents/tags`
2. 预期响应：HTTP 200，返回标签及聚合计数。
3. 更新标签：`PUT /api/v1/agents/<canvas_id>/tags`，请求体：`{"tags": "标签A,标签B"}`
4. 预期响应：HTTP 200。
5. 数据库验证：`user_canvas.tags` 更新为 `"标签A,标签B"`。
**预期结果**：Agent 标签 CRUD 在 GaussDB CharField(512) 上正常工作。

---

### TC-CS-095: Bot 端点 — beta token 认证
**前置条件**：拥有 beta API token（`APIToken` 表中记录）。
**步骤**：
1. 使用 beta token 发送请求：`GET /api/v1/chatbots/<dialog_id>/info`
2. 预期响应：HTTP 200。
3. 使用普通 token 发送相同请求。
4. 预期响应：HTTP 401、响应体 `code=401`（`@login_required(auth_types=AUTH_BETA)` 校验）；请求使用无 cookie 客户端，避免 session fallback。
**预期结果**：Bot 端点仅接受 beta API token 认证。

---

### TC-CS-096: 跨租户访问 Chat 拒绝
**前置条件**：用户 A 创建了 Chat，用户 B 尝试访问。
**步骤**：
1. 用户 B 发送请求：`GET /api/v1/chats/<userA_chat_id>`
2. 预期响应：HTTP 200 且响应体 `code=109` / `message="No authorization."`（`_ensure_owned_chat` 校验）。
3. 用户 B 发送请求：`DELETE /api/v1/chats/<userA_chat_id>`
4. 预期响应：HTTP 200 且响应体 `code=109` / `message="No authorization."`。
**预期结果**：跨租户访问 Chat 被拒绝，不会泄露其他租户数据。

---

### TC-CS-097: 跨租户访问 Agent 拒绝
**前置条件**：用户 A 创建了 Agent（`permission="me"`），用户 B 尝试访问。
**步骤**：
1. 用户 B 发送请求：`GET /api/v1/agents/<userA_agent_id>`
2. 预期响应：HTTP 200，响应体 `code=103`（`_require_canvas_access_sync` 校验 `UserCanvasService.accessible`）。
**预期结果**：`permission="me"` 的 Agent 对其他租户不可见。

---

### TC-CS-098: Agent permission="team" 的跨租户只读访问
**前置条件**：用户 A 创建 Agent 并设置 `permission="team"`；用户 B 已通过租户成员 API 以 normal 角色加入 A 的 tenant。完全无成员关系的另一 tenant 不具备 team 可见性。
**步骤**：
1. 用户 B 发送请求：`GET /api/v1/agents/<agent_id>`
2. 预期响应：HTTP 200（team 权限允许同租户查看）。
3. 用户 B 发送请求：`PUT /api/v1/agents/<agent_id>`，请求体：`{"title": "篡改"}`
4. 安全预期：HTTP 200 且业务失败，非所有者不可修改。
**预期结果**：`permission="team"` 允许同租户只读访问，修改仍需所有权。当前 update 路由使用 access 而非 owner 装饰器，是代码审查发现的越权风险；若用户 B 实测修改成功，记录产品安全缺陷，不能降低预期为允许写入。

---

### TC-CS-099: DB 连接测试端点
**前置条件**：无。
**步骤**：
1. 先发送以下请求，验证 SSRF 防护明确拒绝 loopback/private target：
   ```json
   {"db_type": "mysql", "host": "127.0.0.1", "port": 3306, "database": "test", "username": "root", "password": "test"}
   ```
2. 预期响应：HTTP 200 / 业务失败，说明 host 不安全；不能把 127.0.0.1 当作成功连接样例。
3. 若 `gaussdb_info.md` 的专用测试主机解析为公网地址，则两组再以 `db_type="postgres"` 和该专用只读连接参数执行成功路径，预期 `data="Database Connection Successful!"`；请求与日志必须脱敏。若解析为私网地址，改用本批次隔离网络中映射到非保留测试地址的只读 PostgreSQL proxy，仍不得关闭 SSRF 校验。
**预期结果**：私网/loopback 被 SSRF guard 拒绝，受控允许目标能执行 `SELECT 1`；失败与成功路径都实际运行。

---

### TC-CS-100: Agent Rerun Pipeline
**前置条件**：Agent 为 Pipeline 类型，已有文档处理记录。
**步骤**：
1. 从 DataFlow ingestion log API 获取真实 log ID、当前 DSL 与要重跑的 `component_id`，发送 `POST /api/v1/agents/rerun`：`{"id":"<log_id>","dsl":<dsl>,"component_id":"<component_id>"}`。
2. 预期响应：HTTP 200，Pipeline 重新执行。
**预期结果**：Agent Pipeline 重跑功能在 GaussDB 环境下正常。

---

## 附录 A：数据库表字段与 GaussDB 兼容性关注点

| 表名 | 关键字段 | 类型 | GaussDB 关注点 |
|------|---------|------|---------------|
| `dialog` | `llm_id` | EmptyStringCharField(128) | 空串 → NULL |
| `dialog` | `rerank_id` | EmptyStringCharField(128) | 空串 → NULL |
| `dialog` | `prompt_config` | JSONField | 大 JSON 存储/查询 |
| `dialog` | `llm_setting` | JSONField | JSON 合并更新 |
| `dialog` | `kb_ids` | JSONField | JSON 数组 |
| `conversation` | `message` | JSONField, nullable | 大消息数组（100+ 条） |
| `conversation` | `reference` | JSONField, nullable | 引用数据 |
| `api_4_conversation` | `dsl` | JSONField, nullable | Agent DSL 快照 |
| `api_4_conversation` | `message` | JSONField, nullable | Agent 消息历史 |
| `user_canvas` | `dsl` | JSONField, nullable | 大 Canvas DSL |
| `user_canvas` | `tags` | CharField(512) | 逗号分隔标签 |
| `canvas_template` | `title` | JSONField, nullable | 多语言标题（JSON 非字符串） |
| `canvas_template` | `description` | JSONField, nullable | 多语言描述（JSON 非字符串） |
| `user_canvas_version` | `dsl` | JSONField, nullable | DSL 版本快照 |

## 附录 B：测试优先级矩阵

| 优先级 | 测试用例 | 原因 |
|--------|---------|------|
| P0 | TC-CS-001, 014, 017, 024, 027, 039, 049, 081, 082 | 核心 CRUD + GaussDB 空串兼容 |
| P1 | TC-CS-002, 011, 018, 025, 030, 035, 040, 041, 051, 054, 062, 067 | 完整功能 + 分页 + 级联 |
| P2 | TC-CS-003~010, 019~023, 031~034, 042~048, 055~061, 068~080 | 校验、边界、Bot |
| P3 | TC-CS-083~100 | 极端场景、并发、大 JSON |
