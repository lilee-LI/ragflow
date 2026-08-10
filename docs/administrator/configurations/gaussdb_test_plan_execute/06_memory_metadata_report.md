# 06 - Memory Metadata 测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 17:35:25～2026-07-27 17:43:22（Asia/Shanghai）
- 当前计划：`06_memory_metadata.md`、`06_memory_supplement.md`
- 覆盖：47/47 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：41 PASS、6 FAIL、0 BLOCKED
- 对照组结果：41 PASS、6 FAIL、0 BLOCKED
- 实验组结果：41 PASS、6 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/06_memory_metadata/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-MM-005 | FAIL | FAIL | 对照组也存在 |
| TC-MM-006 | FAIL | FAIL | 对照组也存在 |
| TC-MM-011 | FAIL | FAIL | 对照组也存在 |
| TC-MM-SUP-013 | FAIL | FAIL | 对照组也存在 |
| TC-MM-SUP-022 | FAIL | FAIL | 对照组也存在 |
| TC-MM-SUP-023 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 6，对照组独有 0，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-MM-005 | 非法 memory_type 值 | 对照组也存在 | MM-EMPTY-TYPE-VALIDATION-001 | control Memory create accepted an empty memory_type or another invalid variant；experiment Memory create accepted an empty memory_type or another invalid variant | api/apps/services/memory_api_service.py:create_memory |
| TC-MM-006 | 不存在的模型 ID | 对照组也存在 | MM-MODEL-VALIDATION-001 | control Memory create accepted nonexistent model identifiers；experiment Memory create accepted nonexistent model identifiers | api/apps/services/memory_api_service.py:create_memory |
| TC-MM-011 | 分页和关键词搜索 | 对照组也存在 | MM-PAGINATION-VALIDATION-001 | control Memory list pagination validation did not use the parameter-error contract；experiment Memory list pagination validation did not use the parameter-error contract | api/apps/restful_apis/memory_api.py:list_memory |
| TC-MM-SUP-013 | 更新 tenant_llm_id 和 tenant_embd_id - 公开字段无效更新 | 对照组也存在 | MM-INTERNAL-MODEL-FIELD-NOOP-001 | control public Memory update silently accepted tenant model fields without mutation；experiment public Memory update silently accepted tenant model fields without mutation | api/apps/services/memory_api_service.py:update_memory |
| TC-MM-SUP-022 | GET /memories 分页非法类型的框架层错误 | 对照组也存在 | MM-PAGINATION-FRAMEWORK-ERROR-001 | control invalid page type escaped route validation and did not return code 101；experiment invalid page type escaped route validation and did not return code 101 | api/apps/restful_apis/memory_api.py:list_memory |
| TC-MM-SUP-023 | GET /memories page_size 上界与下界 | 对照组也存在 | MM-PAGE-SIZE-BOUNDARY-VALIDATION-001 | control page_size upper/lower bounds did not use code 101 validation；experiment page_size upper/lower bounds did not use code 101 validation | api/utils/pagination_utils.py:validate_rest_api_page_size |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-MM-001 | 正常创建 Memory | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-001.json) |
| TC-MM-002 | 创建多类型 Memory | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-002.json) |
| TC-MM-003 | 重复名称创建自动重命名 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-003.json) |
| TC-MM-004 | 缺少必填字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-004.json) |
| TC-MM-005 | 非法 memory_type 值 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-005.json) |
| TC-MM-006 | 不存在的模型 ID | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-006.json) |
| TC-MM-007 | 名称边界值 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-007.json) |
| TC-MM-008 | permissions 创建参数忽略与更新参数验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-008.json) |
| TC-MM-009 | 获取 Memory 列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-009.json) |
| TC-MM-010 | 按 memory_type 过滤 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-010.json) |
| TC-MM-011 | 分页和关键词搜索 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-011.json) |
| TC-MM-012 | 更新 Memory 名称和配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-012.json) |
| TC-MM-013 | 更新 memory_size 边界值 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-013.json) |
| TC-MM-014 | 更新 temperature | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-014.json) |
| TC-MM-015 | 已有消息时禁止修改 embd_id 和 memory_type | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-015.json) |
| TC-MM-016 | 获取单个 Memory 配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-016.json) |
| TC-MM-017 | 获取不存在的 Memory 配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-017.json) |
| TC-MM-018 | 跨租户访问 Memory 配置 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-018.json) |
| TC-MM-019 | 正常删除 Memory | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-019.json) |
| TC-MM-020 | 删除有消息的 Memory | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-020.json) |
| TC-MM-021 | 重复删除 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-021.json) |
| TC-MM-022 | 跨租户删除 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-022.json) |
| TC-MM-023 | embd_id 和 llm_id 空值兼容 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-023.json) |
| TC-MM-024 | 无 Token 访问 Memory API | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-024.json) |
| TC-MM-SUP-001 | Memory name 自动 strip | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-001.json) |
| TC-MM-SUP-002 | Memory name 长度检查 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-002.json) |
| TC-MM-SUP-003 | Memory type 规范化 - 去重与位值回读顺序 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-003.json) |
| TC-MM-SUP-004 | Memory type 大小写敏感 - 大写输入被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-004.json) |
| TC-MM-SUP-005 | Temperature 范围验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-005.json) |
| TC-MM-SUP-006 | Memory size 范围验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-006.json) |
| TC-MM-SUP-007 | Forgetting policy 枚举验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-007.json) |
| TC-MM-SUP-008 | Permission 枚举验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-008.json) |
| TC-MM-SUP-009 | Team permission - 联合租户可访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-009.json) |
| TC-MM-SUP-010 | Team permission - 非联合租户被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-010.json) |
| TC-MM-SUP-011 | Me permission - 仅 owner 可访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-011.json) |
| TC-MM-SUP-012 | 列表 owner_ids/tenant_id 过滤只返回可访问 memory | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-012.json) |
| TC-MM-SUP-013 | 更新 tenant_llm_id 和 tenant_embd_id - 公开字段无效更新 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-013.json) |
| TC-MM-SUP-014 | 已有消息时更新 embd_id - 被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-014.json) |
| TC-MM-SUP-015 | 已有消息时更新 memory_type - 被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-015.json) |
| TC-MM-SUP-016 | 更新 avatar、description、system_prompt、user_prompt 允许空值 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-016.json) |
| TC-MM-SUP-017 | Message 访问控制 - owner 可操作 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-017.json) |
| TC-MM-SUP-018 | Message 访问控制 - team permission 联合租户可操作 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-018.json) |
| TC-MM-SUP-019 | Message 访问控制 - 非授权用户被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-019.json) |
| TC-MM-SUP-020 | 获取 message 内容 - 跨 memory 隔离 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-020.json) |
| TC-MM-SUP-021 | POST /messages 对不可访问 memory 不写入数据 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-021.json) |
| TC-MM-SUP-022 | GET /memories 分页非法类型的框架层错误 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-022.json) |
| TC-MM-SUP-023 | GET /memories page_size 上界与下界 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/06_memory_metadata/TC-MM-SUP-023.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 47 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 6 个双方共同失败、0 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
