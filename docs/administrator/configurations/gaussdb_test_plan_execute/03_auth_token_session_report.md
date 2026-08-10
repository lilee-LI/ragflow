# 03 - 认证、Token 与 Session 测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 16:06:21～2026-07-27 16:10:56（Asia/Shanghai）
- 当前计划：`03_auth_token_session.md`、`03_auth_supplement.md`
- 覆盖：67/67 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：57 PASS、10 FAIL、0 BLOCKED
- 对照组结果：57 PASS、10 FAIL、0 BLOCKED
- 实验组结果：58 PASS、9 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/03_authentication/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-AT-028 | FAIL | PASS | 对照组独有 |
| TC-AT-048 | FAIL | FAIL | 对照组也存在 |
| TC-AT-050 | FAIL | FAIL | 对照组也存在 |
| TC-AT-SOFTDEL-002 | FAIL | FAIL | 对照组也存在 |
| TC-AT-SOFTDEL-003 | FAIL | FAIL | 对照组也存在 |
| TC-AT-KEYPAIR-002 | FAIL | FAIL | 对照组也存在 |
| TC-AT-PWD-LENGTH-001 | FAIL | FAIL | 对照组也存在 |
| TC-AT-PWD-LENGTH-002 | FAIL | FAIL | 对照组也存在 |
| TC-AT-EXCEPTION-001 | FAIL | FAIL | 对照组也存在 |
| TC-AT-EXCEPTION-003 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 9，对照组独有 1，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-AT-028 | 使用 Beta Token（AUTH_BETA）访问 Bot API | 对照组独有 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-AT-048 | 注册功能关闭时的行为 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-AT-050 | GaussDB 连接中断时的认证行为 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-AT-SOFTDEL-002 | 软删除用户密码重置流程 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-AT-SOFTDEL-003 | OAuth 登录软删除用户 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-AT-KEYPAIR-002 | RSA 密钥对不匹配场景 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-AT-PWD-LENGTH-001 | 注册密码无最小长度验证 | 对照组也存在 | AT-PWD-MIN-001 | public registration accepts a one-character password | api/apps/restful_apis/user_api.py |
| TC-AT-PWD-LENGTH-002 | 登录密码无最小长度验证 | 对照组也存在 | AT-PWD-MIN-002 | an account with a one-character password can authenticate | api/apps/restful_apis/user_api.py |
| TC-AT-EXCEPTION-001 | 非法 JSON 请求体 | 对照组也存在 | AT-HTTP-STATUS-001 | malformed JSON is wrapped in a non-400 response | api/apps/__init__.py:server_error_response |
| TC-AT-EXCEPTION-003 | 超大请求体 | 对照组也存在 | AT-HTTP-STATUS-002 | request-body limit exception is wrapped in HTTP 200 instead of preserving HTTP 413 | api/apps/__init__.py:server_error_response |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-AT-001 | 正常密码登录成功 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-001.json) |
| TC-AT-002 | 未注册邮箱登录失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-002.json) |
| TC-AT-003 | 密码错误登录失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-003.json) |
| TC-AT-004 | 空请求体登录失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-004.json) |
| TC-AT-005 | 已禁用账户登录失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-005.json) |
| TC-AT-006 | RSA 解密失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-006.json) |
| TC-AT-007 | 登录成功后使用 Authorization Token 访问受保护资源 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-007.json) |
| TC-AT-008 | 正常登出 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-008.json) |
| TC-AT-009 | 未认证用户尝试登出 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-009.json) |
| TC-AT-010 | 登出后 Session Cookie 失效 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-010.json) |
| TC-AT-011 | 获取登录通道列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-011.json) |
| TC-AT-012 | OAuth 登录重定向（本地 stub） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-012.json) |
| TC-AT-013 | OAuth 回调 - 新用户自动注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-013.json) |
| TC-AT-014 | OAuth 回调 - State 不匹配（CSRF 防护） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-014.json) |
| TC-AT-015 | OAuth 回调 - 已存在用户直接登录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-015.json) |
| TC-AT-016 | 获取验证码图片（Captcha） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-016.json) |
| TC-AT-017 | 发送 OTP 邮件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-017.json) |
| TC-AT-018 | OTP 重发冷却限制 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-018.json) |
| TC-AT-019 | OTP 验证成功 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-019.json) |
| TC-AT-020 | OTP 验证失败 - 超过尝试次数锁定 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-020.json) |
| TC-AT-021 | 密码重置成功 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-021.json) |
| TC-AT-022 | 密码重置 - 两次密码不一致 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-022.json) |
| TC-AT-023 | 密码重置 - 未通过 OTP 验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-023.json) |
| TC-AT-024 | 创建 API Token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-024.json) |
| TC-AT-025 | 列出 API Token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-025.json) |
| TC-AT-026 | 删除 API Token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-026.json) |
| TC-AT-027 | 使用 API Token（AUTH_API）访问受保护资源 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-027.json) |
| TC-AT-028 | 使用 Beta Token（AUTH_BETA）访问 Bot API | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-028.json) |
| TC-AT-029 | 使用 Beta Token 访问非 Bot API 失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-029.json) |
| TC-AT-030 | JWT Token 时效性验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-030.json) |
| TC-AT-031 | 重新登录后旧 Token 失效 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-031.json) |
| TC-AT-032 | 删除 API Token 后立即失效 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-032.json) |
| TC-AT-033 | Session Cookie 认证成功 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-033.json) |
| TC-AT-034 | Session 过期后访问失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-034.json) |
| TC-AT-035 | Session 中 access_token 被标记 INVALID 后访问失败 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-035.json) |
| TC-AT-036 | 同一账号并发登录 - Token 覆盖 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-036.json) |
| TC-AT-037 | 并发 API Token 使用不互相干扰 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-037.json) |
| TC-AT-038 | 完全无效的 Token 字符串 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-038.json) |
| TC-AT-039 | Authorization 头 Bearer 前缀兼容性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-039.json) |
| TC-AT-040 | 空 Authorization 头 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-040.json) |
| TC-AT-041 | 已登出的 Token 重放（需重新输入密码认证） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-041.json) |
| TC-AT-042 | 被篡改的 JWT Token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-042.json) |
| TC-AT-043 | 使用其他用户的 API Token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-043.json) |
| TC-AT-044 | 密码加密链路完整性验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-044.json) |
| TC-AT-045 | 特殊字符密码加密验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-045.json) |
| TC-AT-046 | 密码哈希算法与字段长度验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-046.json) |
| TC-AT-047 | UserService.query 安全过滤验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-047.json) |
| TC-AT-048 | 注册功能关闭时的行为 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-048.json) |
| TC-AT-049 | active_required 装饰器 - 非活跃用户访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-049.json) |
| TC-AT-050 | GaussDB 连接中断时的认证行为 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-050.json) |
| TC-AT-051 | GaussDB CharField 空字符串处理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-051.json) |
| TC-AT-052 | 用户注册补偿一致性 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-052.json) |
| TC-AT-053 | GaussDB 复合主键 - api_token 表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-053.json) |
| TC-AT-SOFTDEL-001 | 软删除用户无法重新注册 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-SOFTDEL-001.json) |
| TC-AT-SOFTDEL-002 | 软删除用户密码重置流程 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-SOFTDEL-002.json) |
| TC-AT-SOFTDEL-003 | OAuth 登录软删除用户 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-SOFTDEL-003.json) |
| TC-AT-KEYPAIR-001 | RSA 密钥对匹配验证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-KEYPAIR-001.json) |
| TC-AT-KEYPAIR-002 | RSA 密钥对不匹配场景 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-KEYPAIR-002.json) |
| TC-AT-TOKEN-401-001 | 401 响应触发 token 清理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-TOKEN-401-001.json) |
| TC-AT-TOKEN-401-002 | 防止重复重定向 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-TOKEN-401-002.json) |
| TC-AT-OAUTH-CALLBACK-001 | OAuth callback 通过 URL 参数传递 token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-OAUTH-CALLBACK-001.json) |
| TC-AT-OAUTH-CALLBACK-002 | OAuth callback 无 auth 参数 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-OAUTH-CALLBACK-002.json) |
| TC-AT-PWD-LENGTH-001 | 注册密码无最小长度验证 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-PWD-LENGTH-001.json) |
| TC-AT-PWD-LENGTH-002 | 登录密码无最小长度验证 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-PWD-LENGTH-002.json) |
| TC-AT-EXCEPTION-001 | 非法 JSON 请求体 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-EXCEPTION-001.json) |
| TC-AT-EXCEPTION-002 | 缺少必填字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-EXCEPTION-002.json) |
| TC-AT-EXCEPTION-003 | 超大请求体 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/03_authentication/TC-AT-EXCEPTION-003.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 67 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 9 个双方共同失败、1 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
