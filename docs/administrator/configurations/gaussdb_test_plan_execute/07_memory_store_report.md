# 07 - Memory Store E2E 测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 17:43:22～2026-07-28 09:40:19（Asia/Shanghai）
- 当前计划：`07_memory_store_e2e.md`
- 覆盖：79/79 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：60 PASS、19 FAIL、0 BLOCKED
- 对照组结果：60 PASS、19 FAIL、0 BLOCKED
- 实验组结果：71 PASS、8 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/07_memory_store/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-MS-001 | FAIL | FAIL | 对照组也存在 |
| TC-MS-100 | FAIL | PASS | 对照组独有 |
| TC-MS-101 | FAIL | PASS | 对照组独有 |
| TC-MS-103 | FAIL | PASS | 对照组独有 |
| TC-MS-104 | FAIL | PASS | 对照组独有 |
| TC-MS-107 | FAIL | PASS | 对照组独有 |
| TC-MS-205 | FAIL | FAIL | 对照组也存在 |
| TC-MS-206 | FAIL | FAIL | 对照组也存在 |
| TC-MS-207 | FAIL | FAIL | 对照组也存在 |
| TC-MS-208 | FAIL | FAIL | 对照组也存在 |
| TC-MS-212 | FAIL | FAIL | 对照组也存在 |
| TC-MS-306 | FAIL | FAIL | 对照组也存在 |
| TC-MS-400 | FAIL | PASS | 对照组独有 |
| TC-MS-401 | FAIL | PASS | 对照组独有 |
| TC-MS-405 | FAIL | PASS | 对照组独有 |
| TC-MS-406 | FAIL | FAIL | 对照组也存在 |
| TC-MS-500 | FAIL | PASS | 对照组独有 |
| TC-MS-702 | FAIL | PASS | 对照组独有 |
| TC-MS-803 | FAIL | PASS | 对照组独有 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 8，对照组独有 11，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-MS-001 | 写入单条原始消息并验证全字段 | 对照组也存在 | MS-API-KEY-SUBJECT-ATTRIBUTION-001 | control API token caller could not preserve documented external user_id；experiment API token caller could not preserve documented external user_id | api/apps/restful_apis/memory_api.py:add_message |
| TC-MS-100 | GET /memories/{memory_id} 列出消息（默认参数） | 对照组独有 | MS-LIST-ORDER-001 | control message list was not ordered by valid_at DESC | memory/services/messages.py:list_message |
| TC-MS-101 | GET /memories/{memory_id} 按 agent_id 过滤 | 对照组独有 | MS-LIST-ORDER-001 | control message list was not ordered by valid_at DESC | memory/services/messages.py:list_message |
| TC-MS-103 | GET /memories/{memory_id} 分页参数 | 对照组独有 | MS-LIST-PAGINATION-001 | control raw message pagination returned overlapping or incomplete pages | memory/services/messages.py:list_message |
| TC-MS-104 | GET /messages 获取最近消息 | 对照组独有 | MS-RECENT-ORDER-001 | control recent messages were not ordered by valid_at DESC | memory/services/messages.py:get_recent_messages |
| TC-MS-107 | GET /messages 多个 memory_id 扇出查询 | 对照组独有 | MS-RECENT-MULTI-MEMORY-001 | control recent endpoint did not merge both accessible memories | memory/services/messages.py:get_recent_messages |
| TC-MS-205 | 融合检索（weighted_sum 文本+向量） | 对照组也存在 | MS-KEYWORD-WEIGHT-DIRECTION-001 | control public keyword weight was wired as vector weight；experiment public keyword weight was wired as vector weight | api/db/joint_services/memory_message_service.py:query_message |
| TC-MS-206 | top_n 参数限制返回数量 | 对照组也存在 | MS-SEARCH-TOP-N-VALIDATION-001 | control search did not reject every non-positive or non-integer top_n with code 101；experiment search did not reject every non-positive or non-integer top_n with code 101 | api/apps/restful_apis/memory_api.py:search_message |
| TC-MS-207 | similarity_threshold 边界值测试 | 对照组也存在 | MS-SEARCH-THRESHOLD-VALIDATION-001 | control search did not enforce the public similarity_threshold range with code 101；experiment search did not enforce the public similarity_threshold range with code 101 | api/apps/restful_apis/memory_api.py:search_message |
| TC-MS-208 | keywords_similarity_weight 边界值 | 对照组也存在 | MS-SEARCH-WEIGHT-VALIDATION-001、MS-KEYWORD-WEIGHT-DIRECTION-001 | control search did not enforce the public keywords_similarity_weight range with code 101；control public keyword weight was wired as vector weight；experiment search did not enforce the public keywords_similarity_weight range with code 101；experiment public key… | api/apps/restful_apis/memory_api.py:search_message；api/db/joint_services/memory_message_service.py:query_message |
| TC-MS-212 | 空查询处理和缺失 query 参数 | 对照组也存在 | MS-SEARCH-QUERY-VALIDATION-001 | control search did not reject empty and missing query with code 101；experiment search did not reject empty and missing query with code 101 | api/apps/restful_apis/memory_api.py:search_message |
| TC-MS-306 | 更新不存在的消息状态 | 对照组也存在 | MS-STATUS-NONEXISTENT-SUCCESS-001 | control nonexistent message status update reported success；experiment nonexistent message status update reported success | api/apps/services/memory_api_service.py:update_message_status |
| TC-MS-400 | 遗忘一条消息 | 对照组独有 | MS-INFINITY-FORGET-TIMESTAMP-PRECISION-001 | control Infinity float forget timestamp fell outside the API request window | conf/message_infinity_mapping.json:forget_at_flt |
| TC-MS-401 | 验证遗忘后 forget_at 在 DB 中的值 | 对照组独有 | MS-INFINITY-FORGET-TIMESTAMP-PRECISION-001 | control Infinity float forget timestamp fell outside the API request window | conf/message_infinity_mapping.json:forget_at_flt |
| TC-MS-405 | 重复遗忘同一消息 | 对照组独有 | MS-INFINITY-FORGET-TIMESTAMP-PRECISION-001 | control Infinity float forget timestamp did not advance after a repeated forget | conf/message_infinity_mapping.json:forget_at_flt |
| TC-MS-406 | 遗忘不存在的消息 | 对照组也存在 | MS-FORGET-NONEXISTENT-SUCCESS-001 | control nonexistent message forget reported success；experiment nonexistent message forget reported success | api/apps/services/memory_api_service.py:forget_message |
| TC-MS-500 | 获取消息内容 | 对照组独有 | MS-INFINITY-CONTENT-ID-OMITTED-001 | control Infinity content response omitted the composite id field | memory/utils/infinity_conn.py:get |
| TC-MS-702 | 同 id 跨维度覆写后旧维度标记 empty=TRUE | 对照组独有 | MS-INFINITY-CROSS-DIMENSION-DESTRUCTIVE-UPSERT-001 | control Infinity rejected a cross-dimension upsert only after deleting the original row | memory/utils/infinity_conn.py:insert |
| TC-MS-803 | get_forgotten_messages - 返回已遗忘消息 | 对照组独有 | MS-INFINITY-FORGOTTEN-EXISTS-FILTER-001 | control Infinity bound the exists filter as a nonexistent column and rejected the forgotten scan | memory/utils/infinity_conn.py:get_forgotten_messages |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-MS-001 | 写入单条原始消息并验证全字段 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-001.json) |
| TC-MS-002 | 写入消息到多个 memory（扇出写入） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-002.json) |
| TC-MS-003 | 使用不同 agent_id 和 session_id 写入 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-003.json) |
| TC-MS-004 | 写入包含中文内容的消息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-004.json) |
| TC-MS-005 | 写入包含特殊字符的消息（SQL 注入字符、引号、百分号） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-005.json) |
| TC-MS-006 | 写入包含 Emoji 的消息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-006.json) |
| TC-MS-007 | 写入超长内容消息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-007.json) |
| TC-MS-008 | 缺少必填字段写入（缺少 user_input） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-008.json) |
| TC-MS-009 | 写入到不存在的 memory | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-009.json) |
| TC-MS-010 | 跨租户写入被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-010.json) |
| TC-MS-011 | MERGE INTO Upsert 行为验证（相同 id 覆写） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-011.json) |
| TC-MS-012 | 验证 tokenized_content_ltks 分词一致性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-012.json) |
| TC-MS-100 | GET /memories/{memory_id} 列出消息（默认参数） | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-100.json) |
| TC-MS-101 | GET /memories/{memory_id} 按 agent_id 过滤 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-101.json) |
| TC-MS-102 | GET /memories/{memory_id} 按 keywords（session_id）过滤 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-102.json) |
| TC-MS-103 | GET /memories/{memory_id} 分页参数 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-103.json) |
| TC-MS-104 | GET /messages 获取最近消息 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-104.json) |
| TC-MS-105 | GET /messages 默认隐藏已遗忘消息，但不自动过滤停用消息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-105.json) |
| TC-MS-106 | GET /messages 不传 memory_id 返回错误 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-106.json) |
| TC-MS-107 | GET /messages 多个 memory_id 扇出查询 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-107.json) |
| TC-MS-108 | GET /memories/{memory_id} 空结果 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-108.json) |
| TC-MS-109 | GET /messages limit 无上界校验 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-109.json) |
| TC-MS-200 | 英文全文候选参与融合检索 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-200.json) |
| TC-MS-201 | 全文检索中文文本 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-201.json) |
| TC-MS-202 | 验证分词器一致性（写入侧与查询侧使用相同分词器） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-202.json) |
| TC-MS-203 | 向量检索 - 余弦距离验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-203.json) |
| TC-MS-204 | 向量检索 - 空向量不参与匹配 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-204.json) |
| TC-MS-205 | 融合检索（weighted_sum 文本+向量） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-205.json) |
| TC-MS-206 | top_n 参数限制返回数量 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-206.json) |
| TC-MS-207 | similarity_threshold 边界值测试 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-207.json) |
| TC-MS-208 | keywords_similarity_weight 边界值 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-208.json) |
| TC-MS-209 | 跨多个 memory 检索 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-209.json) |
| TC-MS-210 | 检索带 agent_id/session_id/user_id 过滤 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-210.json) |
| TC-MS-211 | 检索默认隐藏已遗忘和不活跃消息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-211.json) |
| TC-MS-212 | 空查询处理和缺失 query 参数 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-212.json) |
| TC-MS-213 | 全文检索特殊字符 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-213.json) |
| TC-MS-300 | 设置消息状态为 false（停用） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-300.json) |
| TC-MS-301 | 设置消息状态为 true（重新激活） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-301.json) |
| TC-MS-302 | 验证 DB 中 status_int 值（1/0 映射） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-302.json) |
| TC-MS-303 | 不活跃消息从默认检索中隐藏 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-303.json) |
| TC-MS-304 | 非布尔 status 值被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-304.json) |
| TC-MS-305 | 跨租户状态更新被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-305.json) |
| TC-MS-306 | 更新不存在的消息状态 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-306.json) |
| TC-MS-400 | 遗忘一条消息 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-400.json) |
| TC-MS-401 | 验证遗忘后 forget_at 在 DB 中的值 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-401.json) |
| TC-MS-402 | 已遗忘消息从默认检索中隐藏 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-402.json) |
| TC-MS-403 | 已遗忘消息在显式查询中可见（list_message 使用 hide_forgotten=False） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-403.json) |
| TC-MS-404 | 跨租户遗忘被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-404.json) |
| TC-MS-405 | 重复遗忘同一消息 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-405.json) |
| TC-MS-406 | 遗忘不存在的消息 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-406.json) |
| TC-MS-500 | 获取消息内容 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-500.json) |
| TC-MS-501 | 跨租户获取消息内容被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-501.json) |
| TC-MS-502 | 获取不存在的消息内容 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-502.json) |
| TC-MS-503 | 获取已遗忘/不活跃消息的内容 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-503.json) |
| TC-MS-600 | 同一租户两个 memory 的操作互不影响 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-600.json) |
| TC-MS-601 | 跨租户数据隔离 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-601.json) |
| TC-MS-602 | 删除 memory A 只清理 A 的行，不影响 B | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-602.json) |
| TC-MS-603 | 不同 memory 中相同 message_id 的行独立存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-603.json) |
| TC-MS-700 | 首次写入创建向量列 q_{dim}_vec | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-700.json) |
| TC-MS-701 | 使用不同维度写入新增向量列 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-701.json) |
| TC-MS-702 | 同 id 跨维度覆写后旧维度标记 empty=TRUE | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-702.json) |
| TC-MS-703 | 向量检索只命中 empty=FALSE 的行 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-703.json) |
| TC-MS-704 | get_fields 只返回真实向量的 content_embed | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-704.json) |
| TC-MS-705 | 向量列 DDL 幂等性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-705.json) |
| TC-MS-800 | init_message_id_sequence - Redis 种子值等于 DB max message_id | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-800.json) |
| TC-MS-801 | init_message_id_sequence - Redis key 已存在时跳过 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-801.json) |
| TC-MS-802 | init_memory_size_cache - 缓存与 DB 实际大小一致 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-802.json) |
| TC-MS-803 | get_forgotten_messages - 返回已遗忘消息 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-803.json) |
| TC-MS-804 | get_missing_field_message - 返回缺少指定字段的消息 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-804.json) |
| TC-MS-805 | init_message_id_sequence - 无 memory 时种子为 1 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-805.json) |
| TC-MS-900 | 物理表名格式为 ragflow_mem_<sha1> | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-900.json) |
| TC-MS-901 | 基础表包含所有必要列 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-901.json) |
| TC-MS-902 | 基础索引全部存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-902.json) |
| TC-MS-903 | UGIN 全文索引存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-903.json) |
| TC-MS-904 | gsdiskann 向量索引存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-904.json) |
| TC-MS-905 | DDL 幂等性（重复 create_idx 不失败） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-905.json) |
| TC-MS-906 | delete_idx 删除整个物理表（仅在无其他 memory 共享时安全） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-906.json) |
| TC-MS-907 | index_exist 验证表和索引完整性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-907.json) |
| TC-MS-908 | advisory lock 防止并发 DDL 冲突 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/07_memory_store/TC-MS-908.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 79 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 8 个双方共同失败、11 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
