# 01 - 启动、迁移与部署配置测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 15:53:48～2026-07-27 16:03:35（Asia/Shanghai）
- 当前计划：`01_startup_migration.md`
- 覆盖：21/21 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：01 组在切换分组模式前按用例进程完成；每个用例固定先 control、后 experiment
- 用例对结果：14 PASS、7 FAIL、0 BLOCKED
- 对照组结果：14 PASS、7 FAIL、0 BLOCKED
- 实验组结果：15 PASS、6 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/01_startup_migration/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-SM-001 | FAIL | FAIL | 对照组也存在 |
| TC-SM-002 | FAIL | FAIL | 对照组也存在 |
| TC-SM-003 | FAIL | FAIL | 对照组也存在 |
| TC-SM-005 | FAIL | FAIL | 对照组也存在 |
| TC-SM-012 | FAIL | PASS | 对照组独有 |
| TC-SM-015 | FAIL | FAIL | 对照组也存在 |
| TC-SM-018 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 6，对照组独有 1，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-SM-001 | 全新 Schema 首次启动 | 对照组也存在 | ENV-DEFECT-001 | current experiment metadata schema did not satisfy the cold-start migration contract | api/db/db_models.py:init_database_tables |
| TC-SM-002 | 重复启动幂等性 | 对照组也存在 | SM-DEFECT-002 | restart in a populated non-public metadata schema treated all tables as absent; index creation then failed because the existing indexes already existed | api/db/db_models.py:init_database_tables |
| TC-SM-003 | 并发启动锁保护 | 对照组也存在 | SM-CONCURRENCY-001 | one or both concurrent initialization processes did not complete cleanly under the database initialization lock | — |
| TC-SM-005 | tenant_llm 主键升级 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-SM-012 | GaussDB 不执行 MySQL 迁移脚本 | 对照组独有 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-SM-015 | DB_TYPE 别名归一化 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-SM-018 | Admin 展示 GaussDB 配置 | 对照组也存在 | SM-ADMIN-SECRET-001 | authenticated Admin services response exposes one or more configured credentials in plaintext | — |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-SM-001 | 全新 Schema 首次启动 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-001.json) |
| TC-SM-002 | 重复启动幂等性 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-002.json) |
| TC-SM-003 | 并发启动锁保护 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-003.json) |
| TC-SM-004 | 缺列补齐 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-004.json) |
| TC-SM-005 | tenant_llm 主键升级 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-005.json) |
| TC-SM-006 | user.email 唯一索引恢复 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-006.json) |
| TC-SM-007 | 历史索引清理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-007.json) |
| TC-SM-008 | GaussDB 空字符串根因验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-008.json) |
| TC-SM-009 | 清单字段 nullable 验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-009.json) |
| TC-SM-010 | ORM 空字符串写入和读取 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-010.json) |
| TC-SM-011 | 空字符串查询改写验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-011.json) |
| TC-SM-012 | GaussDB 不执行 MySQL 迁移脚本 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-012.json) |
| TC-SM-013 | 表结构方言正确性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-013.json) |
| TC-SM-014 | 索引方言正确性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-014.json) |
| TC-SM-015 | DB_TYPE 别名归一化 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-015.json) |
| TC-SM-016 | GAUSSDB_METADATA_SCHEMA 安全校验 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-016.json) |
| TC-SM-017 | GAUSSDB_METADATA_* 与 GAUSSDB_* 隔离 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-017.json) |
| TC-SM-018 | Admin 展示 GaussDB 配置 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-018.json) |
| TC-SM-019 | 健康检查使用 SELECT 1 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-019.json) |
| TC-SM-020 | Docker Compose profile 隔离 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-020.json) |
| TC-SM-021 | Helm values 不强制 MySQL | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/01_startup_migration/TC-SM-021.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 21 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 6 个双方共同失败、1 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
