# GaussDB 数据集与文档测试方案

## 概述

本测试方案覆盖 RAGFlow 系统中数据集（Dataset）和文档（Document）的完整生命周期测试，包括创建、查询、更新、删除、标签管理、元数据配置、文档上传、解析、分块、检索等核心功能。

**测试范围**：
- 数据集 CRUD 操作及权限控制
- 数据集搜索、标签和元数据配置
- 文档上传、解析、分块全流程
- 文档检索和缩略图功能
- GaussDB 空字符串兼容字段验证
- 级联删除和数据一致性
- 边界条件和异常处理

**涉及数据表**：
- `knowledgebase` - 数据集主表
- `document` - 文档表
- `file2document` - 文件与文档关联表
- `task` - 任务表
- `file` - 文件表
- `api_token` - API 令牌表

**API 端点前缀**：`/api/v1`

---

## 一、数据集创建测试

### TC-DD-001: 创建基础数据集 - 最小参数集
**前置条件**：
- 已登录用户，拥有有效 API Token
- 用户有创建数据集权限

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "测试数据集_基础"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "name": "测试数据集_基础",
    "language": "English",
    "description": null,
    "embedding_model": "<tenant_default_embedding>",
    "permission": "me",
    "chunk_num": 0,
    "doc_num": 0,
    "parser_id": "naive",
    "parser_config": {"...": "naive parser defaults"},
    "similarity_threshold": 0.2,
    "vector_similarity_weight": 0.3,
    "status": "1",
    "token_num": 0,
    "create_time": "<timestamp>",
    "update_time": "<timestamp>"
  }
}
```

3. 数据库验证：
```sql
SELECT id, name, language, permission, parser_id, status 
FROM knowledgebase 
WHERE name = '测试数据集_基础';
```

**预期结果**：
- 数据集成功创建，返回完整数据集信息
- 数据库中存在对应记录，默认值正确填充
- 未指定 `embedding_model` 时继承 tenant 的默认 embedding，API 字段名为 `embedding_model`，数据库字段名为 `embd_id`
- 未指定 `description` 时保存为 NULL，API 回读为 JSON `null`
- `create_time` 和 `update_time` 为当前时间戳

---

### TC-DD-002: 创建数据集 - 完整参数集
**前置条件**：
- 已登录用户，拥有有效 API Token

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "测试数据集_完整",
  "description": "这是一个完整的测试数据集",
  "embedding_model": "<model_name>@<provider>",
  "permission": "team",
  "chunk_method": "qa",
  "parser_config": {
    "chunk_token_num": 512,
    "layout_recognize": "DeepDOC"
  }
}
```

2. 预期响应：HTTP 200，返回完整数据集信息

3. 数据库验证：
```sql
SELECT * FROM knowledgebase WHERE name = '测试数据集_完整';
```

**预期结果**：
- 所有自定义参数正确保存
- `permission` 为 "team"
- `parser_id` 为 "qa"（由请求字段 `chunk_method` 映射保存）
- `embd_id` 为指定 embedding 模型（由请求字段 `embedding_model` 映射保存）
- `parser_config` JSON 字段正确存储
- 创建接口不接受 `language`、`similarity_threshold`、`vector_similarity_weight` 等未在 `CreateDatasetReq` 中定义的字段

---

### TC-DD-003: 创建数据集 - 重复名称
**前置条件**：
- 已存在名称为 "重复数据集" 的数据集

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "重复数据集"
}
```

2. 预期响应：HTTP 200，当前实现会自动重命名新数据集
```json
{
  "code": 0,
  "data": {
    "name": "重复数据集(1)"
  }
}
```

3. 数据库验证：确认新增记录名称被自动追加序号
```sql
SELECT name FROM knowledgebase
WHERE name LIKE '重复数据集%'
ORDER BY create_time DESC;
```

**预期结果**：
- 请求成功，新数据集名称自动变为 `重复数据集(1)` 或下一个可用序号
- GaussDB 与默认对照组行为一致，不应按“重复名称拒绝”判定失败

---

### TC-DD-004: 创建数据集 - 空名称
**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": ""
}
```

2. 预期响应：HTTP 200，响应体 `code: 101`
```json
{
  "code": 101,
  "message": "<Pydantic name 校验错误>"
}
```

**预期结果**：
- 请求失败，返回参数校验错误
- 数据库中没有新增记录

---

### TC-DD-005: 创建数据集 - 无效 chunk_method
**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "测试数据集_无效解析器",
  "chunk_method": "invalid_parser"
}
```

2. 预期响应：HTTP 200，响应体 `code: 101`
```json
{
  "code": 101,
  "message": "<Pydantic chunk_method 枚举错误>"
}
```

**预期结果**：
- 请求失败，`chunk_method` 校验失败
- 有效的 `chunk_method` 包括：naive, qa, table, paper, book, laws, presentation, picture, one, tag 等当前 schema 允许值

---

### TC-DD-006: 创建数据集 - 不同权限级别
**前置条件**：
- 已登录用户

**步骤**：
1. 发送两个请求，分别创建 "me" 和 "team" 权限的数据集：
```http
POST /api/v1/datasets
{
  "name": "私有数据集",
  "permission": "me"
}

POST /api/v1/datasets
{
  "name": "团队数据集",
  "permission": "team"
}
```

2. 预期响应：两个请求都成功

3. 数据库验证：
```sql
SELECT name, permission FROM knowledgebase 
WHERE name IN ('私有数据集', '团队数据集');
```

**预期结果**：
- 两种权限级别都能正确创建
- 数据库中 permission 字段值正确

---

### TC-DD-007: 创建数据集 - 不同 chunk_method
**前置条件**：
- 已登录用户

**步骤**：
1. 依次创建使用不同 chunk_method 的数据集：
```http
POST /api/v1/datasets
{
  "name": "数据集_naive",
  "chunk_method": "naive"
}

POST /api/v1/datasets
{
  "name": "数据集_book",
  "chunk_method": "book"
}

POST /api/v1/datasets
{
  "name": "数据集_qa",
  "chunk_method": "qa"
}

POST /api/v1/datasets
{
  "name": "数据集_table",
  "chunk_method": "table"
}
```

2. 预期响应：所有请求成功

3. 数据库验证：确认每个数据集的 parser_id 正确

**预期结果**：
- 所有有效的 `chunk_method` 都能成功创建数据集
- 数据库 `parser_id` 字段保存为对应的 chunk method
- parser_config 根据 `chunk_method` 自动填充默认配置

---

### TC-DD-008: 创建数据集 - 自定义 embedding 模型
**前置条件**：
- 已登录用户
- 系统中已配置多个 embedding 模型

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "测试数据集_自定义嵌入",
  "embedding_model": "<model_name>@<provider>"
}
```

2. 预期响应：HTTP 200

3. 数据库验证：
```sql
SELECT embd_id FROM knowledgebase WHERE name = '测试数据集_自定义嵌入';
```

**预期结果**：
- 数据库 `embd_id` 字段正确保存请求中的 `embedding_model`
- GaussDB 中空字符串 embd_id 正确处理（EmptyStringCharField）

---

## 二、数据集查询和分页测试

### TC-DD-009: 获取数据集列表 - 默认分页
**前置条件**：
- 用户拥有多个数据集（>10个）

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": [
    {
      "id": "<uuid>",
      "name": "..."
    }
  ],
  "total_datasets": 15
}
```

3. 验证返回的数据集数量（默认 30 个，受 `page_size` 上限约束）

**预期结果**：
- 返回分页数据集列表
- 默认每页 30 条记录
- 包含顶层 `total_datasets` 字段表示总数

---

### TC-DD-010: 获取数据集列表 - 自定义分页
**前置条件**：
- 用户拥有多个数据集

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets?page=2&page_size=5
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，返回第 2 页的 5 条记录

3. 验证返回的数据集 ID 与第 1 页不重复

**预期结果**：
- 分页参数正确生效
- 不同页的数据集不重复
- 顶层 `total_datasets` 字段保持不变

---

### TC-DD-011: 获取数据集列表 - 按名称精确查询
**前置条件**：
- 存在名称包含 "测试" 的数据集

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets?name=测试
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200；`name` 是精确查询参数，仅在存在同名数据集时返回该记录。模糊检索由 `ext={"keywords":"测试"}` 覆盖

**预期结果**：
- 精确名称查询正常工作
- 不把 `name` 错当成模糊匹配参数

---

### TC-DD-012: 获取数据集列表 - 不支持的 permission 参数验证
**前置条件**：
- 存在 "me" 和 "team" 权限的数据集

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets?permission=team
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，`code: 101`（当前 `ListDatasetReq` 未定义 `permission` 查询参数）

**预期结果**：
- 接口拒绝未定义查询参数
- 不应把 `permission` 当成已支持过滤条件

---

### TC-DD-013: 获取单个数据集详情
**前置条件**：
- 已知数据集 ID

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，返回完整数据集信息

3. 数据库验证：比对返回数据与数据库记录

**预期结果**：
- 返回指定数据集的完整信息
- 所有字段与数据库一致
- 包含统计信息（doc_num, chunk_num, token_num）

---

### TC-DD-014: 获取不存在的数据集
**前置条件**：
- 使用无效的数据集 ID

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/invalid-uuid-12345
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，响应体 `code: 102`
```json
{
  "code": 102,
  "message": "User '<tenant_id>' lacks permission for dataset 'invalid-uuid-12345'"
}
```

**预期结果**：
- 返回业务数据错误，不把 HTTP 200 误判成成功
- 错误信息明确

---

### TC-DD-015: 数据集搜索 API
**前置条件**：
- 存在多个数据集

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets/search
Authorization: Bearer <token>
Content-Type: application/json

{
  "question": "测试",
  "dataset_ids": ["<dataset_id_1>", "<dataset_id_2>"],
  "doc_ids": ["<doc_id_1>", "<doc_id_2>"],
  "page": 1,
  "size": 10,
  "top_k": 10
}
```

2. 预期响应：HTTP 200，返回跨所选数据集的 chunk 检索结果（`chunks`、`total`、`labels`），不是数据集列表

**预期结果**：
- 检索功能正常
- 支持全文/向量混合检索
- 返回 chunk 结果按相关性排序

**当前接口约束**：
- `/api/v1/datasets/search` 使用 `question`，不是 `query`。
- 跨数据集检索需要 `dataset_ids`。
- GaussDB 检索条件不能包含空列表；如果测试用例不覆盖全库检索，应显式传入非空 `doc_ids`。

---

## 三、数据集更新测试

### TC-DD-016: 更新数据集基本信息
**前置条件**：
- 已创建数据集，ID 已知

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "更新后的数据集名称",
  "description": "更新后的描述",
  "language": "Chinese"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<dataset_id>",
    "name": "更新后的数据集名称",
    "description": "更新后的描述",
    "language": "Chinese",
    ...
  }
}
```

3. 数据库验证：
```sql
SELECT name, description, language, update_time 
FROM knowledgebase 
WHERE id = '<dataset_id>';
```

**预期结果**：
- 数据集信息成功更新
- update_time 更新为当前时间
- 其他未修改字段保持不变

---

### TC-DD-017: 更新数据集 parser_config
**前置条件**：
- 已创建数据集

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "parser_config": {
    "chunk_token_num": 1024,
    "layout_recognize": "Plain Text",
    "task_page_size": 12
  }
}
```

2. 预期响应：HTTP 200

3. 数据库验证：
```sql
SELECT parser_config FROM knowledgebase WHERE id = '<dataset_id>';
```

**预期结果**：
- parser_config JSON 字段正确更新
- 新配置通过 `deep_merge` 与旧配置合并；未提交的旧键保留，提交的键覆盖，`parent_child` 会展开执行层字段

---

### TC-DD-018: 更新数据集 - 修改 permission
**前置条件**：
- 已创建 permission 为 "me" 的数据集

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "permission": "team"
}
```

2. 预期响应：HTTP 200

3. 数据库验证：确认 permission 字段已更新

**预期结果**：
- permission 字段成功从 "me" 更新为 "team"
- 权限变更立即生效

---

### TC-DD-019: 更新数据集 - 修改 embedding 模型
**前置条件**：
- 已创建数据集，使用默认 embedding 模型

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "embedding_model": "<model_name>@<provider>"
}
```

2. 预期响应：HTTP 200

3. 数据库验证：确认 embd_id 已更新

**预期结果**：
- 数据库 `embd_id` 字段更新为请求中的 `embedding_model`
- 更新接口本身不会自动重解析已有文档；需要由后续 embedding/parse API 明确调度

---

### TC-DD-020: 更新数据集 - 无效字段
**前置条件**：
- 已创建数据集

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "invalid_field": "some_value"
}
```

2. 预期响应：HTTP 200，`code: 101`；`CreateDatasetReq/UpdateDatasetReq` 使用 `extra="forbid"`

**预期结果**：
- 无效字段被拒绝
- 数据库记录保持不变

---

## 四、数据集删除测试

### TC-DD-021: 删除空数据集
**前置条件**：
- 已创建空数据集（无文档）

**步骤**：
1. 发送请求：
```http
DELETE /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "ids": ["<dataset_id>"]
}
```

2. 预期响应：HTTP 200，`code: 0`，`data.success_count: 1`

3. 数据库验证：
```sql
SELECT COUNT(*) FROM knowledgebase WHERE id = '<dataset_id>';
```

**预期结果**：
- 数据集成功删除
- 数据库中无对应记录
- 返回成功消息

---

### TC-DD-022: 删除包含文档的数据集 - 级联删除
**前置条件**：
- 已创建数据集
- 数据集中有 3 个文档，每个文档有多个 chunks

**步骤**：
1. 记录删除前的 document ID、task ID、file/file2document ID 和对象存储 key；后续必须按已保存 ID 校验，不能在 document 已删除后再用子查询取得原 ID：
```sql
SELECT COUNT(*) FROM document WHERE kb_id = '<dataset_id>';
SELECT id, doc_id FROM task WHERE doc_id IN (SELECT id FROM document WHERE kb_id = '<dataset_id>');
```

2. 发送删除请求：
```http
DELETE /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "ids": ["<dataset_id>"]
}
```

3. 预期响应：HTTP 200

4. 数据库验证：
```sql
SELECT COUNT(*) FROM knowledgebase WHERE id = '<dataset_id>';
SELECT COUNT(*) FROM document WHERE kb_id = '<dataset_id>';
SELECT COUNT(*) FROM task WHERE id IN (<saved_task_ids>);
```

**预期结果**：
- 数据集成功删除
- 关联的文档记录全部删除（级联删除）
- 关联的任务记录全部删除
- 向量数据库中的 chunks 数据也应删除

---

### TC-DD-023: 删除不存在的数据集
**前置条件**：
- 使用无效的数据集 ID

**步骤**：
1. 发送请求：
```http
DELETE /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "ids": ["550e8400e29b41d4a716446655440000"]
}
```

2. 预期响应：HTTP 200，JSON `code: 102`
```json
{
  "code": 102,
  "message": "User '<tenant_id>' lacks permission for datasets: '<dataset_id>'"
}
```

**预期结果**：
- 返回业务数据错误
- 无副作用

---

### TC-DD-024: 批量删除数据集
**前置条件**：
- 存在多个数据集

**步骤**：
1. 发送请求：
```http
DELETE /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "ids": ["<dataset_id_1>", "<dataset_id_2>", "<dataset_id_3>"]
}
```

2. 预期响应：HTTP 200

3. 数据库验证：确认所有指定数据集都已删除

**预期结果**：
- 所有指定数据集成功删除
- 支持批量操作

---

## 五、数据集标签测试

### TC-DD-025: 获取数据集标签列表
**前置条件**：
- 数据集已添加多个标签

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/tags
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": [
    ["标签1", 5],
    ["标签2", 3]
  ]
}
```

**预期结果**：
- 返回数据集的所有标签
- 每项为 `[tag, chunk_count]`，计数单位是包含该 tag 的 chunk 数，不是文档数

---

### TC-DD-026: 重命名数据集标签
**前置条件**：
- 已创建数据集
- 数据集已有 tag（tag 来源于文档/chunk 的 `tag_kwd`）

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>/tags
Authorization: Bearer <token>
Content-Type: application/json

{
  "from_tag": "标签1",
  "to_tag": "新标签1"
}
```

2. 预期响应：HTTP 200

3. 再次获取标签列表验证

**预期结果**：
- 指定 tag 成功重命名
- 旧 tag 不再返回，新 tag 可被读取

---

### TC-DD-027: 删除数据集标签
**前置条件**：
- 数据集已有标签

**步骤**：
1. 发送请求：
```http
DELETE /api/v1/datasets/<dataset_id>/tags
Authorization: Bearer <token>
Content-Type: application/json

{
  "tags": ["标签1", "标签2"]
}
```

2. 预期响应：HTTP 200

3. 验证标签已删除

**预期结果**：
- 指定标签成功删除
- 其他标签保持不变

---

### TC-DD-028: 获取标签聚合统计
**前置条件**：
- 多个数据集有不同标签

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/tags/aggregation?dataset_ids=<dataset_id_1>,<dataset_id_2>
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": [
    {"value": "通用标签", "count": 5},
    {"value": "专用标签", "count": 2}
  ]
}
```

**预期结果**：
- 只聚合显式 `dataset_ids` 中的标签；缺少该参数时返回 `code: 102`
- `count` 为跨所选数据集累计的 chunk 数

---

## 六、数据集元数据配置测试

### TC-DD-029: 获取扁平化元数据
**前置条件**：
- 存在多个数据集
- 已知待查询数据集 ID，记为 `<dataset_id>`

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/metadata/flattened?dataset_ids=<dataset_id>
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "author": {
      "alice": ["<doc_id>"]
    }
  }
}
```

**预期结果**：
- 返回指定数据集的扁平化文档 metadata
- data 结构为 `{metadata_key: {metadata_value: [document_id]}}`
- 如果指定数据集没有文档 metadata，data 可以为空对象

---

### TC-DD-030: 获取数据集元数据配置
**前置条件**：
- 已新建数据集，且尚未更新元数据配置

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/metadata/config
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "metadata": [],
    "built_in_metadata": []
  }
}
```

**预期结果**：
- 返回数据集的元数据配置
- 包含字段定义

---

### TC-DD-031: 更新数据集元数据配置
**前置条件**：
- 已创建数据集

**步骤**：
1. 发送请求：
```http
PUT /api/v1/datasets/<dataset_id>/metadata/config
Authorization: Bearer <token>
Content-Type: application/json

{
  "metadata": [
    {"key": "category", "type": "string"},
    {"key": "version", "type": "number"}
  ],
  "built_in_metadata": []
}
```

2. 预期响应：HTTP 200

3. 再次获取配置验证

**预期结果**：
- 元数据配置成功更新
- 字段定义正确保存到 `knowledgebase.parser_config.metadata` / `built_in_metadata`；不存在 `auto_metadata_config` 独立数据库列

---

## 七、文档上传测试

### TC-DD-032: 上传单个 PDF 文档
**前置条件**：
- 已创建数据集
- 准备测试 PDF 文件

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <test.pdf>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": [{
    "id": "<doc_id>",
    "name": "test.pdf",
    "dataset_id": "<dataset_id>",
    "chunk_count": 0,
    "token_count": 0,
    "run": "UNSTART"
  }]
}
```

3. 数据库验证：
```sql
SELECT * FROM document WHERE id = '<doc_id>';
SELECT * FROM file2document WHERE document_id = '<doc_id>';
SELECT * FROM file WHERE id IN (SELECT file_id FROM file2document WHERE document_id = '<doc_id>');
```

**预期结果**：
- 文档成功上传
- document 表创建记录
- file2document 表创建关联
- file 表存储文件信息
- 数据库初始 `status="1"`（记录有效）、`run="0"`（未解析）；API 把 run 映射为 `UNSTART`

---

### TC-DD-033: 上传多个文档
**前置条件**：
- 已创建数据集
- 准备多个测试文件（PDF, DOCX, TXT）

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <test1.pdf>
file: <test2.docx>
file: <test3.txt>
```

2. 预期响应：HTTP 200，返回多个文档信息

3. 数据库验证：确认所有文档都已创建

**预期结果**：
- 多个文档同时上传成功
- 每个文档都有独立记录
- 文件类型正确识别

---

### TC-DD-034: 上传不同格式文档
**前置条件**：
- 已创建数据集
- 准备各种格式文件：PDF, DOCX, XLSX, PPT, MD, TXT, HTML

**步骤**：
1. 依次上传不同格式文件：
```http
POST /api/v1/datasets/<dataset_id>/documents
Content-Type: multipart/form-data

file: <test.pdf>
file: <test.docx>
file: <test.xlsx>
file: <test.ppt>
file: <test.md>
file: <test.txt>
file: <test.html>
```

2. 预期响应：所有请求都成功

3. 数据库验证：确认每个文档的 `type`、`suffix` 正确

**预期结果**：
- 所有支持的格式都能上传
- `document.type` 与 `suffix` 正确识别文件类型
- 文件大小和名称正确记录

---

### TC-DD-035: 上传大小边界文件
**前置条件**：
- 已创建数据集
- 两组 API 都显式设置相同且安全的 `MAX_CONTENT_LENGTH`（本批次为 16 MiB）
- 动态生成一个略小于限制和一个略大于限制的测试文件，避免依赖历史文件或向共享环境传输 100 MiB 以上数据

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <over_limit_file.pdf>
```

2. 超限请求预期 HTTP 413；再上传低于限制的有效小文件，预期 HTTP 200 / `code=0`

**预期结果**：
- 限制内文件上传成功
- 超出配置限制的文件返回 413 Payload Too Large，且 metadata、对象存储均无残留
- 错误信息明确

---

### TC-DD-036: 上传重复文件名
**前置条件**：
- 数据集中已有文件 "test.pdf"

**步骤**：
1. 再次上传同名文件：
```http
POST /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <test.pdf>
```

2. 预期响应：HTTP 200 / `code=0`，新文件由 `duplicate_name()` 自动追加可用序号

**预期结果**：
- 不覆盖旧文件；两个文件都有独立 Document/File/File2Document 记录
- 新名称带 `(1)` 或下一个可用序号

---

## 八、文档列表和过滤测试

### TC-DD-037: 获取文档列表 - 默认分页
**前置条件**：
- 数据集中有 35 个文档（超过默认页大小 30）

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "docs": [...],
    "total": 35
  }
}
```

**预期结果**：
- 返回分页文档列表
- 默认每页 30 条
- 包含 total 字段

---

### TC-DD-038: 获取文档列表 - 按名称搜索
**前置条件**：
- 数据集中有多个文档

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/documents?keywords=test
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，只返回名称包含 "test" 的文档

**预期结果**：
- 搜索功能正常
- 返回匹配的文档

---

### TC-DD-039: 获取文档列表 - 按状态过滤
**前置条件**：
- 数据集中至少有未解析（UNSTART）和已完成（DONE）两种状态的文档

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/documents?run=DONE
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，只返回已完成的文档

**预期结果**：
- 状态过滤功能正常
- `run` 支持数字或文本：0/UNSTART、1/RUNNING、2/CANCEL、3/DONE、4/FAIL、5/SCHEDULE；`status` 是记录是否有效，不是解析进度

---

### TC-DD-040: 通过 ID 获取单个文档信息
**前置条件**：
- 已知文档 ID

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/documents?id=<doc_id>
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200，返回完整文档信息

3. 数据库验证：比对返回数据与数据库记录

**预期结果**：
- 列表过滤结果 `data.total=1` 且 `data.docs[0].id=<doc_id>`
- 包含解析进度、chunk 数量等统计信息
- `GET /datasets/<dataset_id>/documents/<doc_id>` 是文件下载流，不能当作 JSON 详情端点

---

## 九、文档更新测试

### TC-DD-041: 更新文档基本信息
**前置条件**：
- 已上传文档

**步骤**：
1. 发送请求：
```http
PATCH /api/v1/datasets/<dataset_id>/documents/<doc_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "新文档名称.pdf",
  "chunk_method": "qa"
}
```

2. 预期响应：HTTP 200

3. 数据库验证：
```sql
SELECT name, parser_id FROM document WHERE id = '<doc_id>';
```

**预期结果**：
- 文档信息成功更新
- chunk_method 变更会把文档重置为未解析并清除旧 chunks，需要后续显式重新解析

---

### TC-DD-042: 更新文档 parser_config
**前置条件**：
- 已上传文档

**步骤**：
1. 发送请求：
```http
PATCH /api/v1/datasets/<dataset_id>/documents/<doc_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "parser_config": {
    "chunk_token_num": 256,
    "layout_recognize": "DeepDOC"
  }
}
```

2. 预期响应：HTTP 200

3. 数据库验证：确认 parser_config 已更新

**预期结果**：
- parser_config 成功更新
- parser_config 更新本身不自动调度；已解析文档需要后续显式重新解析才会作用于新 chunks

---

## 十、文档删除测试

### TC-DD-043: 删除单个文档
**前置条件**：
- 数据集中有文档，文档已有 chunks

**步骤**：
1. 记录删除前状态：
```sql
SELECT COUNT(*) FROM document WHERE id = '<doc_id>';
SELECT COUNT(*) FROM task WHERE doc_id = '<doc_id>';
```

2. 发送删除请求：
```http
DELETE /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
Content-Type: application/json

{
  "ids": ["<doc_id>"]
}
```

3. 预期响应：HTTP 200

4. 数据库验证：
```sql
SELECT COUNT(*) FROM document WHERE id = '<doc_id>';
SELECT COUNT(*) FROM task WHERE doc_id = '<doc_id>';
```

5. 验证数据集统计信息更新：
```sql
SELECT doc_num, chunk_num FROM knowledgebase WHERE id = '<dataset_id>';
```

**预期结果**：
- 文档成功删除
- 关联的 task 记录删除
- 向量数据库中的 chunks 删除
- 数据集的 doc_num 和 chunk_num 更新

---

### TC-DD-044: 批量删除文档
**前置条件**：
- 数据集中有多个文档

**步骤**：
1. 发送请求：
```http
DELETE /api/v1/datasets/<dataset_id>/documents
Authorization: Bearer <token>
Content-Type: application/json

{
  "ids": ["<doc_id_1>", "<doc_id_2>", "<doc_id_3>"]
}
```

2. 预期响应：HTTP 200

3. 数据库验证：确认所有文档都已删除

**预期结果**：
- 批量删除成功
- 数据集统计信息正确更新

---

## 十一、文档解析测试

### TC-DD-045: 触发文档解析
**前置条件**：
- 已上传未解析的文档

**步骤**：
1. 发送解析请求：
```http
POST /api/v1/datasets/<dataset_id>/documents/parse
Authorization: Bearer <token>
Content-Type: application/json

{
  "document_ids": ["<doc_id>"]
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "success_count": 1
  },
  "message": "success"
}
```

3. 数据库验证：
```sql
SELECT run, status, process_begin_at FROM document WHERE id = '<doc_id>';
```

4. 等待解析完成，轮询状态：
```http
GET /api/v1/datasets/<dataset_id>/documents?id=<doc_id>
```

**预期结果**：
- 解析任务成功创建
- 数据库 `document.run` 更新为 "1"（运行中），API 映射为 `RUNNING`
- process_begin_at 设置为当前时间
- 解析完成后 document.run 变为 "3"（DONE），chunk_num 和 token_num 更新

---

### TC-DD-046: 检查解析进度
**前置条件**：
- 文档正在解析中

**步骤**：
1. 轮询文档状态：
```http
GET /api/v1/datasets/<dataset_id>/documents?id=<doc_id>
Authorization: Bearer <token>
```

2. 预期响应：
```json
{
  "code": 0,
  "data": {
    "id": "<doc_id>",
    "progress": 0.5,
    "progress_msg": "Processing page 5 of 10",
    "run": "RUNNING"
  }
}
```

3. 持续轮询直到 progress = 1.0

**预期结果**：
- progress 字段反映解析进度（0.0 到 1.0）
- progress_msg 提供详细的进度信息
- 解析完成后数据库 run 变为 "3"，API 为 `DONE`

---

### TC-DD-047: 解析失败处理
**前置条件**：
- 上传损坏的文件或系统无法处理的文件

**步骤**：
1. 触发解析：
```http
POST /api/v1/datasets/<dataset_id>/documents/parse
Authorization: Bearer <token>
Content-Type: application/json

{
  "document_ids": ["<doc_id>"]
}
```

2. 等待解析完成

3. 检查状态：
```http
GET /api/v1/datasets/<dataset_id>/documents?id=<doc_id>
```

4. 预期响应：
```json
{
  "code": 0,
  "data": {
    "run": "FAIL",
    "progress_msg": "Error: Unable to parse document"
  }
}
```

**预期结果**：
- 解析失败时数据库 `document.run="4"`、API `run="FAIL"`
- progress_msg 包含错误信息
- 不会创建 chunks

---

## 十二、文档分块测试

### TC-DD-048: 获取文档 chunks
**前置条件**：
- 文档已解析完成，生成了多个 chunks

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets/<dataset_id>/documents/<doc_id>/chunks
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "chunks": [
      {
        "id": "<chunk_id>",
        "content": "...",
        "positions": [...]
      }
    ],
    "total": 25
  }
}
```

**预期结果**：
- 返回文档的所有 chunks
- 包含 chunk 内容和位置信息
- total 字段与 document.chunk_num 一致

---

### TC-DD-049: 手动添加 chunk
**前置条件**：
- 文档已解析

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets/<dataset_id>/documents/<doc_id>/chunks
Authorization: Bearer <token>
Content-Type: application/json

{
  "content": "手动添加的测试 chunk 内容",
  "important_keywords": ["manual"],
  "questions": ["这是什么内容？"],
  "tag_kwd": ["manual-test"]
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {"chunk": {
    "id": "<new_chunk_id>",
    "content": "手动添加的测试 chunk 内容"
  }}
}
```

3. 数据库验证：
```sql
SELECT chunk_num FROM document WHERE id = '<doc_id>';
SELECT chunk_num FROM knowledgebase WHERE id = '<dataset_id>';
```

**预期结果**：
- chunk 成功添加
- document.chunk_num 增加
- knowledgebase.chunk_num 增加
- 向量数据库中创建对应记录

---

### TC-DD-050: 更新 chunk
**前置条件**：
- 文档已有 chunks

**步骤**：
1. 发送请求：
```http
PATCH /api/v1/datasets/<dataset_id>/documents/<doc_id>/chunks/<chunk_id>
Authorization: Bearer <token>
Content-Type: application/json

{
  "content": "更新后的 chunk 内容",
  "important_keywords": [],
  "available": true
}
```

2. 预期响应：HTTP 200

3. 验证 chunk 内容已更新

**预期结果**：
- chunk 内容成功更新
- 向量数据库中的 embedding 重新生成

**本批次判定要求**：
- 不能只看 `PATCH` 的 `code=0`；必须再用 chunk GET 和 retrieval 读回新内容，并检查服务日志无 doc-store update 错误。
- GaussDB 动态向量列更新是当前代码审查的高风险点，但不得引用历史运行结论；本批次重新实测并给出证据。

---

### TC-DD-051: 删除 chunk
**前置条件**：
- 文档已有 chunks

**步骤**：
1. 发送请求：
```http
DELETE /api/v1/datasets/<dataset_id>/documents/<doc_id>/chunks
Authorization: Bearer <token>
Content-Type: application/json

{
  "chunk_ids": ["<chunk_id_1>", "<chunk_id_2>"]
}
```

2. 预期响应：HTTP 200

3. 数据库验证：确认 chunks 已删除，统计信息更新

**预期结果**：
- chunks 成功删除
- document.chunk_num 减少
- knowledgebase.chunk_num 减少

---

## 十三、检索测试

### TC-DD-052: 基础检索测试
**前置条件**：
- 数据集已创建并解析多个文档
- 文档中有相关内容可检索

**步骤**：
1. 发送检索请求：
```http
POST /api/v1/retrieval
Authorization: Bearer <token>
Content-Type: application/json

{
  "question": "测试检索问题",
  "dataset_ids": ["<dataset_id>"],
  "top_k": 5,
  "similarity_threshold": 0.5
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "chunks": [
      {
        "id": "<chunk_id>",
        "content": "...",
        "document_id": "<doc_id>",
        "dataset_id": "<dataset_id>",
        "similarity": 0.85,
        "positions": [...]
      }
    ]
  }
}
```

**预期结果**：
- 返回相关的 chunks
- chunks 按相似度排序
- similarity 字段反映匹配程度
- 结果来自指定的数据集

---

### TC-DD-053: 多数据集检索
**前置条件**：
- 多个数据集都有解析完成的文档

**步骤**：
1. 发送请求：
```http
POST /api/v1/retrieval
Authorization: Bearer <token>
Content-Type: application/json

{
  "question": "跨数据集检索问题",
  "dataset_ids": ["<dataset_id_1>", "<dataset_id_2>", "<dataset_id_3>"],
  "top_k": 10
}
```

2. 预期响应：HTTP 200，返回来自多个数据集的 chunks

**预期结果**：
- 检索结果来自所有指定的数据集
- 结果按相似度统一排序
- 每个 chunk 包含 dataset_id 标识来源

---

### TC-DD-054: 检索过滤条件
**前置条件**：
- 数据集已解析文档

**步骤**：
1. 发送带过滤条件的检索请求：
```http
POST /api/v1/retrieval
Authorization: Bearer <token>
Content-Type: application/json

{
  "question": "测试问题",
  "dataset_ids": ["<dataset_id>"],
  "top_k": 10,
  "similarity_threshold": 0.7,
  "vector_similarity_weight": 0.6
}
```

2. 预期响应：HTTP 200

**预期结果**：
- 相似度阈值过滤低分结果
- 向量权重为 0.6，全文权重由实现取 `1-vector_similarity_weight`；接口没有 `keyword_similarity_weight` 字段
- 返回的结果都满足阈值要求

---

### TC-DD-055: 数据集内检索
**前置条件**：
- 数据集已解析文档

**步骤**：
1. 发送请求：
```http
POST /api/v1/datasets/<dataset_id>/search
Authorization: Bearer <token>
Content-Type: application/json

{
  "question": "数据集内检索问题",
  "top_k": 5
}
```

2. 预期响应：HTTP 200，返回该数据集内的检索结果

**预期结果**：
- 只在指定数据集内检索
- 结果格式与通用检索一致

---

## 十四、缩略图测试

### TC-DD-056: 获取文档缩略图
**前置条件**：
- 文档已解析，支持缩略图生成（如 PDF、图片）

**步骤**：
1. 发送请求：
```http
GET /api/v1/thumbnails?doc_ids=<doc_id>
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200
```
Content-Type: application/json

{"code": 0, "data": {"<doc_id>": "<base64-or-image-url>"}}
```

**预期结果**：
- 返回以文档 ID 为 key 的缩略图映射
- 值为 base64 缩略图、图片 URL 或空值；该端点不是逐页缩略图二进制流

---

### TC-DD-057: 获取不支持缩略图的文档
**前置条件**：
- 文档类型为纯文本（TXT）

**步骤**：
1. 发送请求：
```http
GET /api/v1/thumbnails?doc_ids=<doc_id>
Authorization: Bearer <token>
```

2. 预期响应：HTTP 200 / `code=0`，映射中该文档值为空（若上传阶段未生成缩略图）

**预期结果**：
- 不支持缩略图的文档返回空值而非虚构的 404/默认图片契约

---

## 十五、边界和异常测试

### TC-DD-058: 无权限访问数据集
**前置条件**：
- 用户 A 创建了 permission="me" 的数据集
- 用户 B 尝试访问

**步骤**：
1. 用户 B 发送请求：
```http
GET /api/v1/datasets/<dataset_id>
Authorization: Bearer <token_B>
```

2. 预期响应：HTTP 200，`code: 102`
```json
{
  "code": 102,
  "message": "User '<tenant_b>' lacks permission for dataset '<dataset_id>'"
}
```

**预期结果**：
- 无权限用户无法访问私有数据集
- 当前 REST 业务错误由 HTTP 200 + 数据错误码表达；仍须确认不泄露数据

---

### TC-DD-059: 无效 API Token
**前置条件**：
- 使用无效或过期的 Token

**步骤**：
1. 发送请求：
```http
GET /api/v1/datasets
Authorization: Bearer invalid_token
```

2. 预期响应：HTTP 401
```json
{
  "code": 401,
  "message": "Unauthorized"
}
```

**预期结果**：
- 无效 Token 被拒绝
- 返回 HTTP 401 / `code=401`；消息表达 Unauthorized（允许框架包装文本）

---

### TC-DD-060: 并发操作测试
**前置条件**：
- 已创建数据集

**步骤**：
1. 并发发送多个请求：
- 上传 5 个文档
- 更新数据集信息
- 查询数据集列表

   本批次不得跳过该用例。使用有界并发（2 个 worker）同时提交 5 个小文件上传，并并行执行一次更新和一次列表查询；为请求设置单次超时与整组上限，记录每个请求的状态、耗时和服务资源水位。

2. 验证所有请求都成功完成

3. 数据库验证：确认数据一致性

**预期结果**：
- 并发操作不导致数据不一致
- 所有请求都成功或适当失败
- 数据库状态正确

---

### TC-DD-061: 中等规模数据集性能基线
**前置条件**：
- 通过 API 创建 100 个轻量文档以覆盖分页；选取其中 10 个文档，各通过 API 创建 10 个 chunk（共 100 个 chunk）。两组使用完全相同的文本、顺序和预热步骤

**步骤**：
1. 执行各种操作并测量响应时间：
- 获取数据集列表
- 获取文档列表
- 检索操作：显式固定 `page_size=20`、`top_k=20`、`similarity_threshold=0.0` 和 `vector_similarity_weight=0.5`，避免把候选窗口与响应页大小混为一谈

2. 记录响应时间

**预期结果**：
- 无超时、错误或计数不一致
- 分别记录列表与检索的冷/热请求 p50、p95；共享开发环境不预设无依据的 `<2秒` SLA
- 实验组与对照组按相同 fixture 比较，显著回退以实测比值和绝对耗时记录，不因任一组“本机较快”直接宣称生产性能
- 清理后对照组的 dataset 独立 Infinity 索引必须不存在；实验组的租户共享 GaussDB chunk 表允许继续存在，但该 dataset 的行数和目标 chunk ID 必须均为 0

---

## 十六、GaussDB 空字符串兼容字段验证

### TC-DD-062: 验证 embd_id 空字符串处理
**前置条件**：
- 对照组和实验组均已配置可用的 tenant 默认 embedding
- 本用例是明确的字段存储语义测试，允许只对专用 fixture 做一次直接数据库更新

**步骤**：
1. 两组均通过 API 创建数据集，不指定 `embedding_model`：
```http
POST /api/v1/datasets
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "测试空字符串字段"
}
```

2. 先确认两组都继承 tenant 默认 `embd_id`。随后在专用 fixture 上直接把 `embd_id` 更新为 `''` 并做数据库验证：
```sql
SELECT embd_id, LENGTH(embd_id) FROM knowledgebase 
WHERE name = '测试空字符串字段';
```

3. 用 ORM 与 `GET /api/v1/datasets/<dataset_id>` 回读，再通过更新 API 恢复原可用 embedding，避免污染后续解析用例

**预期结果**：
- 对照组 MySQL 物理存储 `''` 且长度为 0
- 实验组 A/ORA-compatible GaussDB 物理存储 NULL；`EmptyStringCharField` 的 ORM/API 回读仍为 `""`
- API 传 `embedding_model:""` 的业务含义是保留现有模型，不是制造空存储；因此不能用该请求替代字段语义故障构造

---

### TC-DD-063: 验证 description 空字符串处理
**前置条件**：
- 两组均执行；`description` 是普通 nullable `TextField`，不是 `EmptyStringCharField`

**步骤**：
1. 创建数据集，description 为空：
```http
POST /api/v1/datasets
{
  "name": "测试描述字段",
  "description": ""
}
```

2. 数据库验证：
```sql
SELECT description, LENGTH(description) FROM knowledgebase 
WHERE name = '测试描述字段';
```

3. 更新 description 为非空，再更新回空字符串：
```http
PUT /api/v1/datasets/<dataset_id>
{
  "description": "有内容的描述"
}

PUT /api/v1/datasets/<dataset_id>
{
  "description": ""
}
```

4. 验证最终状态

**预期结果**：
- 对照组 MySQL 可物理存储空字符串
- 实验组 A/ORA-compatible GaussDB 把空字符串物理存为 NULL，API 回读 JSON `null`
- 两组更新非空值均成功；再更新为空时按上述方言差异判定，不虚构跨库完全相同的物理语义

---

### TC-DD-064: 验证 parser_config JSON 字段
**前置条件**：
- 使用 GaussDB 数据库

**步骤**：
1. 创建数据集，parser_config 提交空对象：
```http
POST /api/v1/datasets
{
  "name": "测试JSON字段",
  "parser_config": {}
}
```

2. 数据库验证：
```sql
SELECT parser_config FROM knowledgebase WHERE name = '测试JSON字段';
```

3. 更新 parser_config 为复杂 JSON：
```http
PUT /api/v1/datasets/<dataset_id>
{
  "parser_config": {
    "chunk_token_num": 512,
    "layout_recognize": "Plain Text",
    "ext": {
      "custom_fields": {
        "key1": "value1",
        "key2": [1, 2, 3]
      }
    }
  }
}
```

4. 验证 JSON 结构完整，并明确比较创建与更新的 `ext` 处理：创建路径保留 `ext` 子对象；更新路径把 `parser_config.ext` 合并到 parser_config 根层

**预期结果**：
- 空对象会被 Pydantic 规范化为 None，再由服务填充所选 parser 的默认配置；不能断言数据库保存 `{}`
- JSON 结构完整保留
- 自定义扩展字段必须通过 `parser_config.ext` 提交；分别按创建/更新路径的真实合并位置判定
- 嵌套对象和数组正确处理

---

### TC-DD-065: 验证 NULL 与空字符串区分
**前置条件**：
- 使用 GaussDB 数据库

**步骤**：
1. 创建两个数据集：
```http
POST /api/v1/datasets
{
  "name": "数据集_空字符串",
  "description": ""
}

POST /api/v1/datasets
{
  "name": "数据集_NULL",
  "description": null
}
```

2. 数据库验证：
```sql
SELECT name, description, 
       CASE WHEN description IS NULL THEN 'NULL' ELSE 'NOT NULL' END as is_null,
       LENGTH(description) as length
FROM knowledgebase 
WHERE name LIKE '数据集_%';
```

**预期结果**：
- 对照组 MySQL：空字符串为 NOT NULL/长度 0，显式 null 为 NULL
- 实验组 A/ORA-compatible GaussDB：空字符串与显式 null 都物理存为 NULL
- API 两组必须稳定返回可解释的 JSON 值；该普通 TextField 不应套用 `EmptyStringCharField` 的回读 `""` 规则

---

## 测试总结

本测试方案包含 65 个测试用例，覆盖：

1. **数据集创建**（8个）：基础创建、完整参数、重复名称、空名称、无效参数、权限级别、chunk_method、embedding 模型
2. **数据集查询和分页**（7个）：默认分页、自定义分页、名称搜索、权限过滤、单个详情、不存在处理、搜索 API
3. **数据集更新**（5个）：基本信息、parser_config、permission、embedding 模型、无效字段
4. **数据集删除**（4个）：空数据集、级联删除、不存在处理、批量删除
5. **数据集标签**（4个）：获取列表、更新、删除、聚合统计
6. **数据集元数据配置**（3个）：扁平化元数据、获取配置、更新配置
7. **文档上传**（5个）：单个 PDF、多个文档、不同格式、超大文件、重复文件名
8. **文档列表和过滤**（4个）：默认分页、名称搜索、状态过滤、单个详情
9. **文档更新**（2个）：基本信息、parser_config
10. **文档删除**（2个）：单个删除、批量删除
11. **文档解析**（3个）：触发解析、检查进度、失败处理
12. **文档分块**（4个）：获取 chunks、手动添加、更新、删除
13. **检索测试**（4个）：基础检索、多数据集、过滤条件、数据集内检索
14. **缩略图**（2个）：获取缩略图、不支持类型
15. **边界和异常**（4个）：无权限、无效 Token、并发操作、性能测试
16. **GaussDB 空字符串兼容**（4个）：embd_id、description、parser_config JSON、NULL 与空字符串区分

**关键测试点**：
- 所有 CRUD 操作的正确性
- 级联删除的数据一致性
- 权限控制和访问管理
- GaussDB 特有的空字符串处理
- 并发操作和性能
- 边界条件和异常处理

**测试执行建议**：
1. 按顺序执行，前面的测试为后面的测试准备数据
2. 每个测试用例都要验证 API 响应和数据库状态
3. GaussDB 空字符串测试与本批次 MySQL 对照组比较，不引用未运行的 PostgreSQL 结果
4. 性能测试只建立本批次同机双组基线，不在生产环境执行
5. 并发测试由本批次可复现的有界异步 runner 执行，不得跳过

**测试通过标准**：
- 所有测试用例的预期结果都得到验证
- 数据库状态与 API 响应一致
- 无数据丢失或不一致
- 性能指标满足要求
- GaussDB 特有功能正常工作
