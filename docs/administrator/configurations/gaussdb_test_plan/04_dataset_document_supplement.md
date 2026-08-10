# Dataset/Document API 补充测试

## 概述
补充 Dataset/Document 的 Pydantic 验证、级联删除、高级功能（Tag、Metadata、索引操作）和 GaussDB 特有场景。

## Pydantic 模型验证测试

### TC-DD-VAL-001: CreateDatasetReq 验证 - name 必填
**步骤**：
```http
POST /api/v1/datasets
{
  "description": "test"
}
```

预期响应：HTTP 200，响应体 `code = 101`，Pydantic 验证错误定位 `name` 的必填约束

---

### TC-DD-VAL-002: CreateDatasetReq 验证 - 重复 name 自动重命名
**前置条件**：先创建 name 为 `FreshDDVal002` 的 dataset

**步骤**：
```http
POST /api/v1/datasets
{
  "name": "FreshDDVal002"
}
```

第二次请求使用与第一次完全相同的 name，不使用大小写变体。预期响应：业务成功，响应体 `code = 0`，返回的 `data.name` 为 `FreshDDVal002(1)`。当前默认数据库和 GaussDB 均采用精确重复名称自动重命名行为；本用例不验证大小写不敏感或数据库 collation 行为，也不应写成唯一性拒绝。

---

### TC-DD-VAL-003: UpdateDatasetReq 验证 - embedding_model 可更新并校验可用性
**前置条件**：Dataset 已创建

**步骤**：
```http
PUT /api/v1/datasets/<dataset_id>
{
  "embedding_model": "<tenant 已授权的 embedding model>"
}
```

预期响应：业务成功，响应体 `code = 0`，返回数据中的 `embedding_model` 更新为目标模型。

说明：当前 `UpdateDatasetReq` 和 `dataset_api_service.update_dataset()` 支持更新 `embedding_model`，并通过 `verify_embedding_availability()` 校验模型可用性；测试计划不应写成“不可修改”。

---

### TC-DD-VAL-004: parser_config JSON 结构验证
**步骤**：
```http
POST /api/v1/datasets
{
  "name": "Test Dataset",
  "parser_config": {
    "chunk_token_num": "not_a_number",
    "layout_recognize": "DeepDOC"
  }
}
```

预期响应：业务失败，响应体 `code != 0`，Pydantic 类型验证错误

---

### TC-DD-VAL-005: Embedding model 可用性验证
**步骤**：
```http
POST /api/v1/datasets
{
  "name": "Test Dataset",
  "embedding_model": "non-existent-model@Ollama"
}
```

预期响应：HTTP 200，响应体 `code != 0`，错误明确说明当前 tenant 不存在或无权使用该 embedding；数据库无残留 dataset

---

## 级联删除测试

### TC-DD-DEL-001: 删除 dataset 级联删除 documents
**前置条件**：Dataset 包含多个 documents

删除前保存 document ID 与 task ID 集合，删除后按这些已保存 ID 查询；不能在 document 已删除后再用子查询取原 document ID。

**步骤**：
```http
DELETE /api/v1/datasets
{
  "ids": ["<dataset_id>"]
}
```

数据库验证：
```sql
-- Dataset 被删除
SELECT COUNT(*) FROM knowledgebase WHERE id = '<dataset_id>';
-- 预期：0

-- 关联的 documents 被删除
SELECT COUNT(*) FROM document WHERE kb_id = '<dataset_id>';
-- 预期：0

-- 关联的 tasks 被删除
SELECT COUNT(*) FROM task WHERE id IN (<saved_task_ids>);
-- 预期：0
```

---

### TC-DD-DEL-002: 删除 dataset 级联删除 files
**前置条件**：Dataset 的 documents 关联了 files

**步骤**：
```http
DELETE /api/v1/datasets
{
  "ids": ["<dataset_id>"]
}
```

数据库验证：
```sql
-- file2document 关联被删除
SELECT COUNT(*) FROM file2document WHERE document_id IN (<saved_document_ids>);
-- 预期：0
```

---

### TC-DD-DEL-003: 删除 dataset 删除 doc store chunks
**前置条件**：Dataset 的 documents 已被解析，doc store 中有 chunks

**步骤**：
```http
DELETE /api/v1/datasets
{
  "ids": ["<dataset_id>"]
}
```

Doc store 验证：
```python
# 检查 doc store 中是否还有该 dataset 的 chunks
chunks = docStoreConn.search(
    index_name=f"ragflow_{tenant_id}",
    condition={"kb_id": dataset_id}
)
# 预期：0 chunks
```

---

### TC-DD-DEL-004: 删除 dataset 清理对象存储内容
**前置条件**：Dataset 有上传的文件

**步骤**：
```http
DELETE /api/v1/datasets
{
  "ids": ["<dataset_id>"]
}
```

存储验证：
```bash
# 先保存本用例上传对象的 bucket/key，再用 MinIO 只读接口确认这些 key 不存在
# dataset 删除实现不承诺删除物理 bucket 本身；空 bucket 是否保留不作为失败条件
```

---

### TC-DD-DEL-005: delete_all=true 删除所有 datasets
**步骤**：
```http
DELETE /api/v1/datasets
{
  "delete_all": true
}
```

数据库验证：
```sql
SELECT COUNT(*) FROM knowledgebase WHERE tenant_id = '<user_id>';
-- 预期：0
```

---

## Tag 操作测试

### TC-DD-TAG-001: 构造带 tag 的文档/chunk 前置数据
**说明**：当前 `PUT /api/v1/datasets/<dataset_id>/tags` 是重命名接口，不支持 `{"tags": [...]}` 创建 tag。Tag 来源于已入库文档/chunk 的 `tag_kwd`，因此创建 tag 的测试应先通过文档/chunk 入库或测试夹具构造带 tag 的数据。

**步骤**：
1. 通过 `POST /datasets/<dataset_id>/documents/<document_id>/chunks` 创建专用 chunk，请求体显式带 `"tag_kwd":["important","review"]`；不得直接写 doc store。
2. 使用 `GET /api/v1/datasets/<dataset_id>/tags` 验证 tags 可被列出。

**预期结果**：测试数据中存在的 tag 能被后续 list/delete/rename/aggregation 接口读取。

**本批次判定要求**：两组均从 API 创建相同 tag fixture，并重新验证 list/delete/rename/aggregation。GaussDB JSONB 聚合和数组增删是代码审查高风险点，但不得带入任何历史运行结论。

---

### TC-DD-TAG-002: 列出 dataset tags
**步骤**：
```http
GET /api/v1/datasets/<dataset_id>/tags
```

预期响应：HTTP 200 / `code=0`，返回 `[tag, chunk_count]` pair 列表，包含 `important` 与 `review`

---

### TC-DD-TAG-003: 删除 dataset tags
**步骤**：
```http
DELETE /api/v1/datasets/<dataset_id>/tags
{
  "tags": ["review"]
}
```

预期响应：HTTP 200

验证：再次调用 tag list 与 chunk GET，均不再包含 `review`；不能只按删除接口的 `code=0` 判定。

---

### TC-DD-TAG-004: 重命名 tag
**步骤**：
```http
PUT /api/v1/datasets/<dataset_id>/tags
{
  "from_tag": "important",
  "to_tag": "critical"
}
```

验证：再次调用 tag list 与 chunk GET，应包含 `critical` 且不含 `important`；不能只按重命名接口的 `code=0` 判定。

---

### TC-DD-TAG-005: 聚合所有 datasets 的 tags
**步骤**：
```http
GET /api/v1/datasets/tags/aggregation?dataset_ids=<dataset_id>
```

预期响应：HTTP 200 / `code=0`，返回所选 datasets 的 `{value,count}` 列表；计数单位为 chunk，不是 dataset

---

## Metadata 配置测试

### TC-DD-META-001: 获取 auto-metadata 配置
**步骤**：
```http
GET /api/v1/datasets/<dataset_id>/metadata/config
```

预期响应：HTTP 200，返回 metadata 配置

---

### TC-DD-META-002: 更新 auto-metadata 配置
**步骤**：
```http
PUT /api/v1/datasets/<dataset_id>/metadata/config
{
  "metadata": [
    {"key": "author", "type": "string"},
    {"key": "date", "type": "time"}
  ],
  "built_in_metadata": []
}
```

预期响应：HTTP 200

数据库验证：
```sql
SELECT parser_config FROM knowledgebase WHERE id = '<dataset_id>';
-- 预期：parser_config.metadata / built_in_metadata 已更新
```

---

### TC-DD-META-003: 获取 flattened metadata
**步骤**：
```http
GET /api/v1/datasets/metadata/flattened?dataset_ids=<id1>,<id2>
```

预期响应：HTTP 200，返回扁平化的 metadata 列表

---

## 索引操作测试

### TC-DD-IDX-001: 运行 GraphRAG 索引
**步骤**：
```http
POST /api/v1/datasets/<dataset_id>/index?type=graph
```

预期响应：HTTP 200，返回 task_id

数据库验证：
```sql
SELECT graphrag_task_id FROM knowledgebase WHERE id = '<dataset_id>';
SELECT progress, progress_msg FROM task
 WHERE id = '<graphrag_task_id>' AND task_type = 'graphrag';
-- 预期：progress 在 0~1 之间表示处理中，1.0 表示完成，-1 表示失败；错误原因查看 progress_msg
```

---

### TC-DD-IDX-002: 运行 RAPTOR 索引
**步骤**：
```http
POST /api/v1/datasets/<dataset_id>/index?type=raptor
```

预期响应：HTTP 200

---

### TC-DD-IDX-003: 运行 Mindmap 索引
**步骤**：
```http
POST /api/v1/datasets/<dataset_id>/index?type=mindmap
```

预期响应：HTTP 200

---

### TC-DD-IDX-004: 查询索引任务状态
**步骤**：
```http
GET /api/v1/datasets/<dataset_id>/index?type=<graph|raptor|mindmap>
```

预期响应：HTTP 200，返回索引任务状态和进度

---

### TC-DD-IDX-005: 删除索引
**步骤**：
```http
DELETE /api/v1/datasets/<dataset_id>/index?type=<graph|raptor|mindmap>
```

预期响应：HTTP 200

验证：对应 `knowledgebase.*_task_id` 清空、Task 删除，Graph/RAPTOR 的 doc-store artefact 通过检索/图 API 不再可见；metadata DB 没有 `knowledge_graph` 表，不能查询虚构表。

检索校验使用本用例通过 API 创建的已知 `document_id` 作为 `doc_ids` 范围，既覆盖 RAPTOR 产物所属文档，又避免把可选空过滤条件的独立行为混入索引删除判定。

---

## Embedding 操作测试

### TC-DD-EMB-001: 运行 embedding
**步骤**：
```http
POST /api/v1/datasets/<dataset_id>/embedding
```

预期响应：HTTP 200 / `code=0`，返回 `scheduled_count`（按文档数调度），不是单一 `task_id`

---

### TC-DD-EMB-002: 检查 embedding 兼容性
**步骤**：
```http
POST /api/v1/datasets/<dataset_id>/embedding/check
{
  "embd_id": "<configured_model_name>@<provider>"
}
```

预期响应：HTTP 200，返回兼容性检查结果

---

## Ingestion Logs 测试

### TC-DD-INGEST-001: 列出 ingestion logs
**步骤**：
```http
GET /api/v1/datasets/<dataset_id>/ingestions
```

预期响应：HTTP 200，返回 ingestion log 列表

---

### TC-DD-INGEST-002: 获取单个 ingestion log
**步骤**：
```http
GET /api/v1/datasets/<dataset_id>/ingestions/<log_id>
```

预期响应：HTTP 200，返回 log 详情

---

### TC-DD-INGEST-003: 获取 ingestion summary
**步骤**：
```http
GET /api/v1/datasets/<dataset_id>/ingestions/summary
```

预期响应：HTTP 200，返回汇总统计

---

## GaussDB 特有场景补充

### TC-DD-GDB-001: embd_id 空字符串兼容
**步骤**：两组先通过 API 创建专用 dataset 并记录原可用 embedding。本用例是明确的字段语义验证，随后允许直接把该 fixture 的 `embd_id` 更新为 `''`；读取后立即用更新 API 恢复原模型。仅“不指定 embedding_model”会继承 tenant 默认值，不能产生空字段。

数据库验证：
```sql
SELECT embd_id, LENGTH(embd_id) FROM knowledgebase WHERE id = '<dataset_id>';
-- MySQL：'' / 0；GaussDB A/ORA：NULL / NULL
```

API 验证：
```http
GET /api/v1/datasets/<dataset_id>
```

预期响应：`embedding_model` 字段为 ""

---

### TC-DD-GDB-002: Dataset 列表租户隔离和对照组验证
**说明**：`knowledgebase` 没有 `memory_type` 这类位运算字段，位运算过滤应在 `06_memory_metadata.md::TC-MM-010` 覆盖。本用例改为验证 Dataset 元数据最关键的 tenant 隔离，避免保留不可执行的占位测试。

**前置条件**：
- User A 和 User B 分属不同 tenant。
- User A 创建 dataset `gaussdb-test-ds-tenant-a`。
- User B 创建 dataset `gaussdb-test-ds-tenant-b`。

**步骤**：
```http
GET /api/v1/datasets?ext=%7B%22keywords%22%3A%22gaussdb-test-ds-tenant%22%7D
Authorization: Bearer <user_a_token>
```

API 验证：
- 响应体 `code = 0`。
- 返回列表包含 User A 的 dataset。
- 返回列表不包含 User B 的 dataset。

数据库验证：
```sql
SELECT id, name, tenant_id
  FROM knowledgebase
 WHERE name IN ('gaussdb-test-ds-tenant-a', 'gaussdb-test-ds-tenant-b')
 ORDER BY name;
```

预期：DB 中两条记录均存在，tenant_id 分别属于 User A/User B；API 返回集只暴露当前用户可访问记录。

对照组：
- 使用 User B token 发送同样请求，预期只返回 User B 的 dataset。
- 在 MySQL 元数据库环境执行同样流程，按 fixture 角色归一化后的可见集合应与 GaussDB 一致（A 仅见 A、B 仅见 B）。两个完全独立实例会分别生成 dataset UUID，不要求 UUID 字面值跨组相同。

---

### TC-DD-GDB-003: JSON 字段存储和查询
**步骤**：
```http
POST /api/v1/datasets
{
  "name": "Test Dataset",
  "parser_config": {
    "chunk_token_num": 512,
    "layout_recognize": "Plain Text",
    "ext": {
      "custom_field": "value",
      "nested": {
        "key2": [1, 2, 3]
      }
    }
  }
}
```

数据库验证：
```sql
SELECT parser_config FROM knowledgebase WHERE id = '<dataset_id>';
-- 预期：JSON 字段正确存储，GaussDB 使用 JSONB 类型
```

**说明**：`ParserConfig` 使用严格 schema，任意扩展字段必须放入 `parser_config.ext`，不能直接把 `custom_field/custom_fields` 放在 `parser_config` 根节点。

---

### TC-DD-GDB-004: LIKE 查询特殊字符
**步骤**：
```http
GET /api/v1/datasets?ext=%7B%22keywords%22%3A%22test%25dataset%22%7D
```

先分别创建名称含 `%`、`_` 与不含特殊字符的 dataset。预期响应只匹配字面量搜索目标；`name` 参数是精确等值，不用于 LIKE。两组返回 ID 集合一致，防止 `%`/`_` 被当成未转义通配符扩大结果集。

---

### TC-DD-GDB-005: 排序和分页在 GaussDB 下的行为
**步骤**：
```http
GET /api/v1/datasets?orderby=create_time&desc=true&page=2&page_size=10
```

验证：预先创建 25 个具有可区分创建时间的 dataset，分别获取第 1/2/3 页；页内顺序符合 `create_time DESC`，ID 不重复，合并集合与数据库只读查询的前 25 条一致。不要仅凭推测的 SQL 文本判定；记录两组 API ID 序列和实际查询结果。
