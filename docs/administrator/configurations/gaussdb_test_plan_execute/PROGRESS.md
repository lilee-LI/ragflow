# GaussDB 全量测试进度

- 执行批次：`20260727_combined_001`
- 正式执行完成时间：2026-07-28 10:38:42（Asia/Shanghai）
- 当前阶段：643/643 个正式用例重跑完成，报告与覆盖终审完成
- 执行模式：01 组 21 个用例先完成；随后 10 个计划组、622 个用例按组单进程执行
- 用例对结果：517 PASS、121 FAIL、5 BLOCKED
- 双组签名：PASS/PASS 517，FAIL/FAIL 95，FAIL/PASS 16，PASS/FAIL 10，BLOCKED/BLOCKED 4，BLOCKED/PASS 1
- 适配回归：FAIL（实验组独有失败 10 个）
- 环境终态：operational_ready=true，8/8 服务通过只读验收

## 阶段状态

| 阶段 | 状态 | 证据 |
|---|---|---|
| 当前计划清单 | 完成 | `PLAN_COVERAGE.md` |
| 双组环境与隔离 | 完成 | `00_environment_setup_report.md` |
| 11 组正式重跑 | 完成 | `runs/20260727_combined_001/evidence_private/formal_execution_status.json` |
| 12 份测试报告 | 完成 | `*report.md` |
| 最终覆盖审计 | 完成 | `FINAL_COVERAGE_AUDIT.md` |

双方共同失败和对照组独有失败均继续保留在报告中；它们不阻断后续组执行，也不计作 GaussDB 适配缺陷。
