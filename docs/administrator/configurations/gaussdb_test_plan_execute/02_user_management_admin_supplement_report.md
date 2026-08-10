# 02 - 用户管理 Admin/Settings 补充测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 16:05:14～2026-07-27 16:06:21（Asia/Shanghai）
- 当前计划：`02_user_management_admin_supplement.md`
- 覆盖：25/25 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：22 PASS、3 FAIL、0 BLOCKED
- 对照组结果：22 PASS、3 FAIL、0 BLOCKED
- 实验组结果：22 PASS、3 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-UM-ADMIN-006 | FAIL | FAIL | 对照组也存在 |
| TC-UM-ADMIN-016 | FAIL | FAIL | 对照组也存在 |
| TC-UM-ADMIN-020 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 3，对照组独有 0，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-UM-ADMIN-006 | Admin 删除用户 - 完整流程 | 对照组也存在 | product_cleanup_defect | 用户无 knowledgebase 时根目录文件记录未随用户删除。 | api/db/joint_services/user_account_service.py::delete_user_data |
| TC-UM-ADMIN-016 | Admin revoke admin - 自我操作被拒绝 | 对照组也存在 | admin_api_message_defect | revoke 自我操作分支错误复用了 grant 文案。 | admin/server/routes.py::revoke_admin |
| TC-UM-ADMIN-020 | 非 superuser 访问 Admin API - 被拒绝 | 对照组也存在 | admin_exception_mapping_defect | AdminException(code=403) 未注册全局异常映射，最终返回 HTTP 500。 | admin/server/auth.py::check_admin_auth; admin/server/admin_server.py |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-UM-ADMIN-001 | Admin 创建用户 - 正常流程 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-001.json) |
| TC-UM-ADMIN-002 | Admin 创建用户 - 重复邮箱 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-002.json) |
| TC-UM-ADMIN-003 | Admin 创建用户 - 无效邮箱 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-003.json) |
| TC-UM-ADMIN-004 | Admin 创建 superuser | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-004.json) |
| TC-UM-ADMIN-005 | Admin 删除用户 - 需要先停用 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-005.json) |
| TC-UM-ADMIN-006 | Admin 删除用户 - 完整流程 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-006.json) |
| TC-UM-ADMIN-007 | Admin 删除 superuser - 被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-007.json) |
| TC-UM-ADMIN-008 | Admin 修改密码 - 不需要旧密码 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-008.json) |
| TC-UM-ADMIN-009 | Admin 修改密码 - 相同密码 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-009.json) |
| TC-UM-ADMIN-010 | Admin 激活用户 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-010.json) |
| TC-UM-ADMIN-011 | Admin 停用用户 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-011.json) |
| TC-UM-ADMIN-012 | Admin 激活状态 - 无效值 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-012.json) |
| TC-UM-ADMIN-013 | Admin grant admin - 正常流程 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-013.json) |
| TC-UM-ADMIN-014 | Admin grant admin - 自我操作被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-014.json) |
| TC-UM-ADMIN-015 | Admin revoke admin - 正常流程 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-015.json) |
| TC-UM-ADMIN-016 | Admin revoke admin - 自我操作被拒绝 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-016.json) |
| TC-UM-ADMIN-017 | Admin 查看用户详情 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-017.json) |
| TC-UM-ADMIN-018 | Admin 查看用户数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-018.json) |
| TC-UM-ADMIN-019 | Admin 查看用户 agents | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-019.json) |
| TC-UM-ADMIN-020 | 非 superuser 访问 Admin API - 被拒绝 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-ADMIN-020.json) |
| TC-UM-SET-001 | 修改个人资料 - 受保护字段被忽略 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-SET-001.json) |
| TC-UM-SET-002 | 修改密码 - 需要旧密码 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-SET-002.json) |
| TC-UM-SET-003 | 修改密码 - 旧密码错误 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-SET-003.json) |
| TC-UM-SET-004 | 修改 nickname - 自动 strip | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-SET-004.json) |
| TC-UM-SET-005 | 修改 nickname - 验证正则 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management_admin_supplement/TC-UM-SET-005.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 25 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 3 个双方共同失败、0 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
