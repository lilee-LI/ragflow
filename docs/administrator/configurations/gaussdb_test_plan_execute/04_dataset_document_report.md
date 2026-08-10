# 04 - 数据集与文档测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 16:10:56～2026-07-27 16:51:16（Asia/Shanghai）
- 当前计划：`04_dataset_document.md`、`04_dataset_document_supplement.md`
- 覆盖：98/98 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：79 PASS、19 FAIL、0 BLOCKED
- 对照组结果：86 PASS、12 FAIL、0 BLOCKED
- 实验组结果：79 PASS、19 FAIL、0 BLOCKED
- 适配回归结论：**FAIL**（实验组独有失败 7 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/04_dataset_document/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-DD-022 | FAIL | FAIL | 对照组也存在 |
| TC-DD-025 | PASS | FAIL | 实验组独有 |
| TC-DD-026 | FAIL | FAIL | 对照组也存在 |
| TC-DD-027 | PASS | FAIL | 实验组独有 |
| TC-DD-028 | PASS | FAIL | 实验组独有 |
| TC-DD-035 | FAIL | FAIL | 对照组也存在 |
| TC-DD-047 | FAIL | FAIL | 对照组也存在 |
| TC-DD-DEL-004 | FAIL | FAIL | 对照组也存在 |
| TC-DD-TAG-001 | PASS | FAIL | 实验组独有 |
| TC-DD-TAG-002 | PASS | FAIL | 实验组独有 |
| TC-DD-TAG-003 | PASS | FAIL | 实验组独有 |
| TC-DD-TAG-004 | FAIL | FAIL | 对照组也存在 |
| TC-DD-TAG-005 | PASS | FAIL | 实验组独有 |
| TC-DD-IDX-001 | FAIL | FAIL | 对照组也存在 |
| TC-DD-IDX-003 | FAIL | FAIL | 对照组也存在 |
| TC-DD-IDX-004 | FAIL | FAIL | 对照组也存在 |
| TC-DD-EMB-001 | FAIL | FAIL | 对照组也存在 |
| TC-DD-INGEST-001 | FAIL | FAIL | 对照组也存在 |
| TC-DD-INGEST-002 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 7，对照组也存在 12，对照组独有 0，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-DD-022 | 删除包含文档的数据集 - 级联删除 | 对照组也存在 | DD-CASCADE-OBJECT-001 | dataset deletion leaves original uploaded objects in storage | api/apps/services/dataset_api_service.py:delete_datasets; api/db/services/document_service.py:remove_document |
| TC-DD-025 | 获取数据集标签列表 | 实验组独有 | DD-GAUSS-UNICODE-001 | Unicode tag chunks are reported successful but are not persisted in GaussDB DocEngine | rag/utils/gaussdb_conn.py:insert |
| TC-DD-026 | 重命名数据集标签 | 对照组也存在 | DD-TAG-RENAME-001、DD-GAUSS-UNICODE-001 | tag rename was not visible in both list and chunk reads；GaussDB DocEngine cannot persist or mutate Unicode tag values under SQL_ASCII | api/apps/services/dataset_api_service.py:rename_tag；rag/utils/gaussdb_conn.py:insert,update |
| TC-DD-027 | 删除数据集标签 | 实验组独有 | DD-GAUSS-UNICODE-001 | GaussDB DocEngine cannot persist or remove Unicode tag values under SQL_ASCII | rag/utils/gaussdb_conn.py:insert,update |
| TC-DD-028 | 获取标签聚合统计 | 实验组独有 | DD-GAUSS-UNICODE-001 | GaussDB DocEngine cannot persist Unicode tags for aggregation under SQL_ASCII | rag/utils/gaussdb_conn.py:insert |
| TC-DD-035 | 上传大小边界文件 | 对照组也存在 | DD-UPLOAD-LIMIT-001 | control upload limit response or residue contract did not hold；experiment upload limit response or residue contract did not hold | api/apps/__init__.py:MAX_CONTENT_LENGTH,error handling |
| TC-DD-047 | 解析失败处理 | 对照组也存在 | DD-DOCUMENT-PARSE-FAILURE-001 | control corrupt PDF did not terminate as FAIL with an error and zero chunks；experiment corrupt PDF did not terminate as FAIL with an error and zero chunks | api/db/services/task_service.py:parse failure handling |
| TC-DD-DEL-004 | 删除 dataset 清理对象存储内容 | 对照组也存在 | DD-CASCADE-OBJECT-001 | control dataset deletion did not remove the required objects resources；experiment dataset deletion did not remove the required objects resources | api/apps/services/dataset_api_service.py:delete_datasets |
| TC-DD-TAG-001 | 构造带 tag 的文档/chunk 前置数据 | 实验组独有 | DD-TAG-FIXTURE-001 | experiment ASCII tag fixture did not match list and chunk readback | api/apps/services/dataset_api_service.py |
| TC-DD-TAG-002 | 列出 dataset tags | 实验组独有 | DD-TAG-LIST-001 | experiment ASCII tag list did not match list and chunk readback | api/apps/services/dataset_api_service.py |
| TC-DD-TAG-003 | 删除 dataset tags | 实验组独有 | DD-TAG-DELETE-001 | experiment ASCII tag delete did not match list and chunk readback | api/apps/services/dataset_api_service.py |
| TC-DD-TAG-004 | 重命名 tag | 对照组也存在 | DD-TAG-RENAME-001 | control ASCII tag rename did not match list and chunk readback；experiment ASCII tag rename did not match list and chunk readback | api/apps/services/dataset_api_service.py |
| TC-DD-TAG-005 | 聚合所有 datasets 的 tags | 实验组独有 | DD-TAG-AGGREGATION-001 | experiment ASCII tag aggregate did not match list and chunk readback | api/apps/services/dataset_api_service.py |
| TC-DD-IDX-001 | 运行 GraphRAG 索引 | 对照组也存在 | DD-INDEX-SCHEDULE-GRAPH-001 | control graph index scheduling response and persisted task did not satisfy the plan；experiment graph index scheduling response and persisted task did not satisfy the plan | api/apps/services/dataset_api_service.py:run_index |
| TC-DD-IDX-003 | 运行 Mindmap 索引 | 对照组也存在 | DD-INDEX-SCHEDULE-MINDMAP-001 | control mindmap index scheduling response and persisted task did not satisfy the plan；experiment mindmap index scheduling response and persisted task did not satisfy the plan | api/apps/services/dataset_api_service.py:run_index |
| TC-DD-IDX-004 | 查询索引任务状态 | 对照组也存在 | DD-INDEX-TRACE-001 | control index trace did not match one or more scheduled tasks；experiment index trace did not match one or more scheduled tasks | api/apps/services/dataset_api_service.py:trace_index |
| TC-DD-EMB-001 | 运行 embedding | 对照组也存在 | DD-EMBEDDING-SCHEDULE-001 | control embedding scheduling did not create one task per document；experiment embedding scheduling did not create one task per document | api/apps/services/dataset_api_service.py:run_embedding |
| TC-DD-INGEST-001 | 列出 ingestion logs | 对照组也存在 | DD-INGESTION-LIST-001 | control dataset ingestion log list did not match persisted log；experiment dataset ingestion log list did not match persisted log | api/apps/services/dataset_api_service.py |
| TC-DD-INGEST-002 | 获取单个 ingestion log | 对照组也存在 | DD-INGESTION-DETAIL-001 | control dataset ingestion log detail did not match persisted log；experiment dataset ingestion log detail did not match persisted log | api/apps/services/dataset_api_service.py |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-DD-001 | 创建基础数据集 - 最小参数集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-001.json) |
| TC-DD-002 | 创建数据集 - 完整参数集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-002.json) |
| TC-DD-003 | 创建数据集 - 重复名称 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-003.json) |
| TC-DD-004 | 创建数据集 - 空名称 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-004.json) |
| TC-DD-005 | 创建数据集 - 无效 chunk_method | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-005.json) |
| TC-DD-006 | 创建数据集 - 不同权限级别 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-006.json) |
| TC-DD-007 | 创建数据集 - 不同 chunk_method | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-007.json) |
| TC-DD-008 | 创建数据集 - 自定义 embedding 模型 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-008.json) |
| TC-DD-009 | 获取数据集列表 - 默认分页 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-009.json) |
| TC-DD-010 | 获取数据集列表 - 自定义分页 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-010.json) |
| TC-DD-011 | 获取数据集列表 - 按名称精确查询 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-011.json) |
| TC-DD-012 | 获取数据集列表 - 不支持的 permission 参数验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-012.json) |
| TC-DD-013 | 获取单个数据集详情 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-013.json) |
| TC-DD-014 | 获取不存在的数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-014.json) |
| TC-DD-015 | 数据集搜索 API | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-015.json) |
| TC-DD-016 | 更新数据集基本信息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-016.json) |
| TC-DD-017 | 更新数据集 parser_config | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-017.json) |
| TC-DD-018 | 更新数据集 - 修改 permission | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-018.json) |
| TC-DD-019 | 更新数据集 - 修改 embedding 模型 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-019.json) |
| TC-DD-020 | 更新数据集 - 无效字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-020.json) |
| TC-DD-021 | 删除空数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-021.json) |
| TC-DD-022 | 删除包含文档的数据集 - 级联删除 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-022.json) |
| TC-DD-023 | 删除不存在的数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-023.json) |
| TC-DD-024 | 批量删除数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-024.json) |
| TC-DD-025 | 获取数据集标签列表 | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-025.json) |
| TC-DD-026 | 重命名数据集标签 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-026.json) |
| TC-DD-027 | 删除数据集标签 | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-027.json) |
| TC-DD-028 | 获取标签聚合统计 | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-028.json) |
| TC-DD-029 | 获取扁平化元数据 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-029.json) |
| TC-DD-030 | 获取数据集元数据配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-030.json) |
| TC-DD-031 | 更新数据集元数据配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-031.json) |
| TC-DD-032 | 上传单个 PDF 文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-032.json) |
| TC-DD-033 | 上传多个文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-033.json) |
| TC-DD-034 | 上传不同格式文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-034.json) |
| TC-DD-035 | 上传大小边界文件 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-035.json) |
| TC-DD-036 | 上传重复文件名 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-036.json) |
| TC-DD-037 | 获取文档列表 - 默认分页 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-037.json) |
| TC-DD-038 | 获取文档列表 - 按名称搜索 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-038.json) |
| TC-DD-039 | 获取文档列表 - 按状态过滤 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-039.json) |
| TC-DD-040 | 通过 ID 获取单个文档信息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-040.json) |
| TC-DD-041 | 更新文档基本信息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-041.json) |
| TC-DD-042 | 更新文档 parser_config | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-042.json) |
| TC-DD-043 | 删除单个文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-043.json) |
| TC-DD-044 | 批量删除文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-044.json) |
| TC-DD-045 | 触发文档解析 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-045.json) |
| TC-DD-046 | 检查解析进度 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-046.json) |
| TC-DD-047 | 解析失败处理 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-047.json) |
| TC-DD-048 | 获取文档 chunks | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-048.json) |
| TC-DD-049 | 手动添加 chunk | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-049.json) |
| TC-DD-050 | 更新 chunk | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-050.json) |
| TC-DD-051 | 删除 chunk | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-051.json) |
| TC-DD-052 | 基础检索测试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-052.json) |
| TC-DD-053 | 多数据集检索 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-053.json) |
| TC-DD-054 | 检索过滤条件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-054.json) |
| TC-DD-055 | 数据集内检索 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-055.json) |
| TC-DD-056 | 获取文档缩略图 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-056.json) |
| TC-DD-057 | 获取不支持缩略图的文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-057.json) |
| TC-DD-058 | 无权限访问数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-058.json) |
| TC-DD-059 | 无效 API Token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-059.json) |
| TC-DD-060 | 并发操作测试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-060.json) |
| TC-DD-061 | 中等规模数据集性能基线 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-061.json) |
| TC-DD-062 | 验证 embd_id 空字符串处理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-062.json) |
| TC-DD-063 | 验证 description 空字符串处理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-063.json) |
| TC-DD-064 | 验证 parser_config JSON 字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-064.json) |
| TC-DD-065 | 验证 NULL 与空字符串区分 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-065.json) |
| TC-DD-VAL-001 | CreateDatasetReq 验证 - name 必填 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-VAL-001.json) |
| TC-DD-VAL-002 | CreateDatasetReq 验证 - 重复 name 自动重命名 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-VAL-002.json) |
| TC-DD-VAL-003 | UpdateDatasetReq 验证 - embedding_model 可更新并校验可用性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-VAL-003.json) |
| TC-DD-VAL-004 | parser_config JSON 结构验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-VAL-004.json) |
| TC-DD-VAL-005 | Embedding model 可用性验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-VAL-005.json) |
| TC-DD-DEL-001 | 删除 dataset 级联删除 documents | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-DEL-001.json) |
| TC-DD-DEL-002 | 删除 dataset 级联删除 files | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-DEL-002.json) |
| TC-DD-DEL-003 | 删除 dataset 删除 doc store chunks | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-DEL-003.json) |
| TC-DD-DEL-004 | 删除 dataset 清理对象存储内容 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-DEL-004.json) |
| TC-DD-DEL-005 | delete_all=true 删除所有 datasets | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-DEL-005.json) |
| TC-DD-TAG-001 | 构造带 tag 的文档/chunk 前置数据 | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-TAG-001.json) |
| TC-DD-TAG-002 | 列出 dataset tags | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-TAG-002.json) |
| TC-DD-TAG-003 | 删除 dataset tags | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-TAG-003.json) |
| TC-DD-TAG-004 | 重命名 tag | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-TAG-004.json) |
| TC-DD-TAG-005 | 聚合所有 datasets 的 tags | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-TAG-005.json) |
| TC-DD-META-001 | 获取 auto-metadata 配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-META-001.json) |
| TC-DD-META-002 | 更新 auto-metadata 配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-META-002.json) |
| TC-DD-META-003 | 获取 flattened metadata | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-META-003.json) |
| TC-DD-IDX-001 | 运行 GraphRAG 索引 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-IDX-001.json) |
| TC-DD-IDX-002 | 运行 RAPTOR 索引 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-IDX-002.json) |
| TC-DD-IDX-003 | 运行 Mindmap 索引 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-IDX-003.json) |
| TC-DD-IDX-004 | 查询索引任务状态 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-IDX-004.json) |
| TC-DD-IDX-005 | 删除索引 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-IDX-005.json) |
| TC-DD-EMB-001 | 运行 embedding | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-EMB-001.json) |
| TC-DD-EMB-002 | 检查 embedding 兼容性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-EMB-002.json) |
| TC-DD-INGEST-001 | 列出 ingestion logs | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-INGEST-001.json) |
| TC-DD-INGEST-002 | 获取单个 ingestion log | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-INGEST-002.json) |
| TC-DD-INGEST-003 | 获取 ingestion summary | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-INGEST-003.json) |
| TC-DD-GDB-001 | embd_id 空字符串兼容 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-GDB-001.json) |
| TC-DD-GDB-002 | Dataset 列表租户隔离和对照组验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-GDB-002.json) |
| TC-DD-GDB-003 | JSON 字段存储和查询 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-GDB-003.json) |
| TC-DD-GDB-004 | LIKE 查询特殊字符 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-GDB-004.json) |
| TC-DD-GDB-005 | 排序和分页在 GaussDB 下的行为 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/04_dataset_document/TC-DD-GDB-005.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 98 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **FAIL**。本组共有 12 个双方共同失败、0 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
