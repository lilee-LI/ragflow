# 10 - GaussDB 故障恢复与方言边界测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-28 10:34:52～2026-07-28 10:38:42（Asia/Shanghai）
- 当前计划：`10_fault_recovery.md`
- 覆盖：27/27 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：26 PASS、1 FAIL、0 BLOCKED
- 对照组结果：26 PASS、1 FAIL、0 BLOCKED
- 实验组结果：26 PASS、1 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/10_fault_recovery/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-FR-027 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 1，对照组独有 0，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-FR-027 | 并发 Memory 消息写入 | 对照组也存在 | TC-FR-027-MEMORY-SIZE-CACHE | control Memory size cache differs from the persisted-message product formula；experiment Memory size cache differs from the persisted-message product formula | memory/services/messages.py:MessageService.calculate_message_size |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-FR-001 | 08xxx 连接错误触发 metadata 重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-001.json) |
| TC-FR-002 | 57P01/57P02/57P03 服务关闭错误 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-002.json) |
| TC-FR-003 | 40P01 死锁重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-003.json) |
| TC-FR-004 | 40001 序列化冲突重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-004.json) |
| TC-FR-005 | 55P03 行锁不可用重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-005.json) |
| TC-FR-006 | 42701 重复列幂等分类 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-006.json) |
| TC-FR-007 | 42P07 重复对象幂等分类 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-007.json) |
| TC-FR-008 | 42704 未定义对象幂等分类 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-008.json) |
| TC-FR-009 | 23505 唯一冲突不重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-009.json) |
| TC-FR-010 | 23502 非空冲突不重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-010.json) |
| TC-FR-011 | 42601 语法错误不重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-011.json) |
| TC-FR-012 | 22P02 类型转换错误不重试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-012.json) |
| TC-FR-018 | gsdiskann maintenance_work_mem 恢复 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-018.json) |
| TC-FR-019 | 查询过程中连接中断 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-019.json) |
| TC-FR-020 | 连接池耗尽 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-020.json) |
| TC-FR-021 | 连接超时 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-021.json) |
| TC-FR-022 | 上游不可达并恢复 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-022.json) |
| TC-FR-023 | SSL 连接断开分类 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-023.json) |
| TC-FR-024 | 坏连接验证与丢弃 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-024.json) |
| TC-FR-027 | 并发 Memory 消息写入 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-027.json) |
| TC-FR-037 | Schema 配置标识符注入 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-037.json) |
| TC-FR-038 | 索引名称标识符注入 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-038.json) |
| TC-FR-044 | Schema 权限检查 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-044.json) |
| TC-FR-045 | 事务错误后回滚 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-045.json) |
| TC-FR-051 | floatvector NULL/空向量处理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-051.json) |
| TC-FR-052 | A/ORA 兼容模式约束 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-052.json) |
| TC-FR-053 | 标识符长度限制 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/10_fault_recovery/TC-FR-053.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 27 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 1 个双方共同失败、0 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
