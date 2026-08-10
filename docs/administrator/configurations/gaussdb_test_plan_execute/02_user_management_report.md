# 02 - 用户管理测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 16:03:35～2026-07-27 16:05:14（Asia/Shanghai）
- 当前计划：`02_user_management.md`
- 覆盖：62/62 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：57 PASS、4 FAIL、1 BLOCKED
- 对照组结果：58 PASS、3 FAIL、1 BLOCKED
- 实验组结果：57 PASS、4 FAIL、1 BLOCKED
- 适配回归结论：**FAIL**（实验组独有失败 1 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/02_user_management/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-UM-014 | PASS | FAIL | 实验组独有 |
| TC-UM-044 | FAIL | FAIL | 对照组也存在 |
| TC-UM-045 | BLOCKED | BLOCKED | 两组均阻塞 |
| TC-UM-046 | FAIL | FAIL | 对照组也存在 |
| TC-UM-047 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 1，对照组也存在 3，对照组独有 0，两组均阻塞 1，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-UM-014 | 边界值 nickname 注册（恰好 100 字符） | 实验组独有 | UM-TENANT-NAME-LENGTH-001 | a valid 100-character nickname makes the derived tenant name exceed tenant.name VARCHAR(100), so GaussDB rejects registration | — |
| TC-UM-044 | 完整删除用户数据（级联清理） | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-UM-045 | 物理删除后邮箱可重新注册 | 两组均阻塞 | UM-CASCADE-DELETE-001 | TC-UM-044 physical deletion precondition is not satisfied | — |
| TC-UM-046 | 级联删除 tenant 后的数据一致性 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-UM-047 | 创建 → 删除 → 重新创建同邮箱用户 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-UM-001 | 正常用户注册 - 最小参数集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-001.json) |
| TC-UM-002 | 重复邮箱注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-002.json) |
| TC-UM-003 | 缺少 email 字段注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-003.json) |
| TC-UM-004 | 缺少 password 字段注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-004.json) |
| TC-UM-005 | 缺少 nickname 字段注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-005.json) |
| TC-UM-006 | 无效邮箱格式注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-006.json) |
| TC-UM-007 | 空字符串密码注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-007.json) |
| TC-UM-008 | 空字符串邮箱注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-008.json) |
| TC-UM-009 | 空字符串 nickname 注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-009.json) |
| TC-UM-010 | 纯空格 nickname 注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-010.json) |
| TC-UM-011 | nickname 包含非法字符注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-011.json) |
| TC-UM-012 | SQL 注入尝试注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-012.json) |
| TC-UM-013 | 超长 nickname 注册（101 字符） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-013.json) |
| TC-UM-014 | 边界值 nickname 注册（恰好 100 字符） | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-014.json) |
| TC-UM-015 | 中文 nickname 注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-015.json) |
| TC-UM-016 | 正确邮箱密码登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-016.json) |
| TC-UM-017 | 错误密码登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-017.json) |
| TC-UM-018 | 未注册邮箱登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-018.json) |
| TC-UM-019 | 空邮箱登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-019.json) |
| TC-UM-020 | 空密码登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-020.json) |
| TC-UM-021 | 邮箱大小写敏感性验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-021.json) |
| TC-UM-022 | Authorization Header 验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-022.json) |
| TC-UM-023 | 多次登录会话管理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-023.json) |
| TC-UM-024 | 空请求体登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-024.json) |
| TC-UM-025 | 缺失 password 字段登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-025.json) |
| TC-UM-026 | 注册 → 登录完整流程 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-026.json) |
| TC-UM-027 | 登录后获取个人资料 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-027.json) |
| TC-UM-028 | 更新用户个人资料 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-028.json) |
| TC-UM-029 | 更新资料后验证不可修改受保护字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-029.json) |
| TC-UM-030 | 修改密码流程 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-030.json) |
| TC-UM-031 | 管理员停用用户（设置 is_active=0） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-031.json) |
| TC-UM-032 | 被停用用户登录失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-032.json) |
| TC-UM-033 | 重新激活用户 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-033.json) |
| TC-UM-034 | 设置 status=0（无效用户） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-034.json) |
| TC-UM-035 | status=0 与 is_active=0 的区别验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-035.json) |
| TC-UM-036 | 恢复 status 和 is_active 后完整验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-036.json) |
| TC-UM-037 | 设置用户为 superuser | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-037.json) |
| TC-UM-038 | superuser 访问管理员功能 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-038.json) |
| TC-UM-039 | 取消 superuser 权限 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-039.json) |
| TC-UM-040 | 通过 PATCH /users/me 尝试设置 is_superuser（应被忽略） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-040.json) |
| TC-UM-041 | 通过 PATCH /users/me 尝试修改 status（应被忽略） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-041.json) |
| TC-UM-042 | 通过数据库软删除用户（status=0） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-042.json) |
| TC-UM-043 | 软删除后用户登录失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-043.json) |
| TC-UM-044 | 完整删除用户数据（级联清理） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-044.json) |
| TC-UM-045 | 物理删除后邮箱可重新注册 | BLOCKED | BLOCKED | BLOCKED | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-045.json) |
| TC-UM-046 | 级联删除 tenant 后的数据一致性 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-046.json) |
| TC-UM-047 | 创建 → 删除 → 重新创建同邮箱用户 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-047.json) |
| TC-UM-048 | 软删除用户后同邮箱不可重新注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-048.json) |
| TC-UM-049 | 管理员获取所有用户列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-049.json) |
| TC-UM-050 | 租户内用户列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-050.json) |
| TC-UM-051 | 非 owner 访问租户用户列表被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-051.json) |
| TC-UM-052 | 邀请用户加入租户 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-052.json) |
| TC-UM-053 | 更新 nickname 字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-053.json) |
| TC-UM-054 | 更新 language 字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-054.json) |
| TC-UM-055 | 更新 color_schema 字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-055.json) |
| TC-UM-056 | 更新 avatar 字段（GaussDB 空字符串兼容验证） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-056.json) |
| TC-UM-057 | 设置 nickname 为空字符串（EmptyStringCharField 验证） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-057.json) |
| TC-UM-058 | 特殊字符 nickname 更新验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-058.json) |
| TC-UM-059 | EmptyStringCharField nickname 存储验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-059.json) |
| TC-UM-060 | 多用户并发注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-060.json) |
| TC-UM-061 | 修改密码时旧密码错误验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-061.json) |
| TC-UM-062 | 退出登录后 Token 失效验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/02_user_management/TC-UM-062.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 62 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **FAIL**。本组共有 3 个双方共同失败、0 个对照组独有失败、1 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
