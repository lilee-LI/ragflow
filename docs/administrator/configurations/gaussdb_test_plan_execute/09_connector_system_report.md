# 09 - Connector 调度与系统健康适配测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-28 10:31:55～2026-07-28 10:33:24（Asia/Shanghai）
- 当前计划：`09_connector_system.md`
- 覆盖：6/6 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：2 PASS、0 FAIL、4 BLOCKED
- 对照组结果：2 PASS、0 FAIL、4 BLOCKED
- 实验组结果：3 PASS、0 FAIL、3 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/09_connector/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-CONN-015 | BLOCKED | BLOCKED | 两组均阻塞 |
| TC-CONN-016 | BLOCKED | BLOCKED | 两组均阻塞 |
| TC-CONN-093 | BLOCKED | BLOCKED | 两组均阻塞 |
| TC-CONN-094 | BLOCKED | PASS | 对照环境阻塞/实验组通过 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 0，对照组独有 0，两组均阻塞 3，对照环境阻塞/实验组通过 1。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-CONN-015 | 调度任务使用 GaussDB INTERVAL 表达式 | 两组均阻塞 | TC-CONN-015-BLOCKED | control isolated scheduler probe exited 1 without a result marker；experiment isolated scheduler probe exited 1 without a result marker | — |
| TC-CONN-016 | Prune 调度使用 GaussDB INTERVAL 表达式 | 两组均阻塞 | TC-CONN-016-BLOCKED | control isolated scheduler probe exited 1 without a result marker；experiment isolated scheduler probe exited 1 without a result marker | — |
| TC-CONN-093 | sync_logs 空字符串与 NULL 语义一致 | 两组均阻塞 | TC-CONN-093-BLOCKED | control compatibility probe empty exited 1 without a result marker；experiment compatibility probe empty exited 1 without a result marker | — |
| TC-CONN-094 | SELECT DISTINCT 与 ORDER BY 兼容 | 对照环境阻塞/实验组通过 | TC-CONN-094-BLOCKED | control compatibility probe list exited 1 without a result marker | — |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-CONN-015 | 调度任务使用 GaussDB INTERVAL 表达式 | BLOCKED | BLOCKED | BLOCKED | [JSON](runs/20260727_combined_001/evidence_private/09_connector/TC-CONN-015.json) |
| TC-CONN-016 | Prune 调度使用 GaussDB INTERVAL 表达式 | BLOCKED | BLOCKED | BLOCKED | [JSON](runs/20260727_combined_001/evidence_private/09_connector/TC-CONN-016.json) |
| TC-CONN-031 | 系统状态准确报告组内后端 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/09_connector/TC-CONN-031.json) |
| TC-CONN-032 | GaussDB 专用健康端点 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/09_connector/TC-CONN-032.json) |
| TC-CONN-093 | sync_logs 空字符串与 NULL 语义一致 | BLOCKED | BLOCKED | BLOCKED | [JSON](runs/20260727_combined_001/evidence_private/09_connector/TC-CONN-093.json) |
| TC-CONN-094 | SELECT DISTINCT 与 ORDER BY 兼容 | BLOCKED | PASS | BLOCKED | [JSON](runs/20260727_combined_001/evidence_private/09_connector/TC-CONN-094.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 6 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 0 个双方共同失败、0 个对照组独有失败、3 个双方共同阻塞、1 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
