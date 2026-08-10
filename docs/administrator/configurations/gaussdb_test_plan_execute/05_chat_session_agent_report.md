# 05 - Chat / Session / Agent 测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-27 16:51:16～2026-07-27 17:34:26（Asia/Shanghai）
- 当前计划：`05_chat_session_agent.md`、`05_chat_agent_supplement.md`
- 覆盖：122/122 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：97 PASS、25 FAIL、0 BLOCKED
- 对照组结果：99 PASS、23 FAIL、0 BLOCKED
- 实验组结果：100 PASS、22 FAIL、0 BLOCKED
- 适配回归结论：**FAIL**（实验组独有失败 2 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/05_chat_session_agent/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-CS-001 | FAIL | FAIL | 对照组也存在 |
| TC-CS-003 | FAIL | FAIL | 对照组也存在 |
| TC-CS-022 | FAIL | FAIL | 对照组也存在 |
| TC-CS-026 | FAIL | PASS | 对照组独有 |
| TC-CS-030 | FAIL | FAIL | 对照组也存在 |
| TC-CS-035 | FAIL | FAIL | 对照组也存在 |
| TC-CS-036 | FAIL | FAIL | 对照组也存在 |
| TC-CS-037 | FAIL | FAIL | 对照组也存在 |
| TC-CS-038 | FAIL | FAIL | 对照组也存在 |
| TC-CS-039 | PASS | FAIL | 实验组独有 |
| TC-CS-046 | FAIL | FAIL | 对照组也存在 |
| TC-CS-055 | FAIL | FAIL | 对照组也存在 |
| TC-CS-060 | FAIL | FAIL | 对照组也存在 |
| TC-CS-066 | FAIL | FAIL | 对照组也存在 |
| TC-CS-067 | FAIL | FAIL | 对照组也存在 |
| TC-CS-068 | FAIL | FAIL | 对照组也存在 |
| TC-CS-075 | FAIL | FAIL | 对照组也存在 |
| TC-CS-077 | FAIL | FAIL | 对照组也存在 |
| TC-CS-086 | FAIL | PASS | 对照组独有 |
| TC-CS-087 | FAIL | FAIL | 对照组也存在 |
| TC-CS-091 | PASS | FAIL | 实验组独有 |
| TC-CS-095 | FAIL | FAIL | 对照组也存在 |
| TC-CS-097 | FAIL | FAIL | 对照组也存在 |
| TC-CS-098 | FAIL | FAIL | 对照组也存在 |
| TC-CHAT-DEL-005 | FAIL | PASS | 对照组独有 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 2，对照组也存在 20，对照组独有 3，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-CS-001 | 创建 Chat — 最小参数 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-003 | 创建 Chat — name 重复拒绝 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-022 | 更新 Chat — name 重复检查 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-026 | 批量删除 Chat — delete_all | 对照组独有 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-030 | 查询单个 Session — 含 messages 和 references | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-035 | 删除消息对 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-036 | 消息反馈 — 点赞（thumb up） | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-037 | 消息反馈 — 点踩（thumb down） | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-038 | 消息反馈 — 缺少 thumbup 字段 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-039 | 流式 Completion — 新建 Session | 实验组独有 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-046 | ASR 语音转文本 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-055 | 删除 Agent — 仅所有者可操作 | 对照组也存在 | product_defect | Agent owner DELETE left unreachable related state | — |
| TC-CS-060 | 查询 Agent 版本列表 | 对照组也存在 | product_defect | version list omitted plan-required fields | — |
| TC-CS-066 | 查询 Agent 执行日志 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-067 | Webhook — POST 触发 | 对照组也存在 | product_defect | normal immediate webhook produced no pollable background trace | — |
| TC-CS-068 | Webhook — GET 触发 | 对照组也存在 | product_defect | normal immediate webhook produced no pollable background trace | — |
| TC-CS-075 | Chatbot 信息查询 | 对照组也存在 | — | 失败细节记录在该用例的 oracle/steps 中 | — |
| TC-CS-077 | Agentbot 输入表单查询 | 对照组也存在 | product_defect | endpoint returns inputs instead of plan-required input_form | — |
| TC-CS-086 | Chat 名称含特殊字符 | 对照组独有 | product_defect | emoji duplicate-name lookup mixes utf8mb4 and utf8mb3 collations and rejects an otherwise valid name | — |
| TC-CS-087 | 大量消息的 Session Completion | 对照组也存在 | product_defect | large history is complete but the initial prologue has no id | — |
| TC-CS-091 | Agent Chat Completion — 流式和非流式 | 实验组独有 | CS-AGENT-COMPLETION-001 | experiment Agent stream/nonstream completion or persisted sessions did not meet the contract | api/apps/restful_apis/agent_api.py:agent_chat_completion |
| TC-CS-095 | Bot 端点 — beta token 认证 | 对照组也存在 | CS-BOT-AUTH-TYPE-001 | control Chatbot endpoint did not separate beta and ordinary tokens；experiment Chatbot endpoint did not separate beta and ordinary tokens | api/apps/restful_apis/bot_api.py |
| TC-CS-097 | 跨租户访问 Agent 拒绝 | 对照组也存在 | CS-AGENT-CROSS-TENANT-CODE-001 | control private Agent rejection returned an unexpected business code；experiment private Agent rejection returned an unexpected business code | api/apps/restful_apis/agent_api.py:get_agent |
| TC-CS-098 | Agent permission="team" 的跨租户只读访问 | 对照组也存在 | CS-AGENT-TEAM-UPDATE-IDOR-001 | control normal team member successfully modified another tenant's Agent；experiment normal team member successfully modified another tenant's Agent | api/apps/restful_apis/agent_api.py:update_agent |
| TC-CHAT-DEL-005 | delete_all 删除所有 chats | 对照组独有 | CHAT-SUPPLEMENT-SOFT-DELETE-001 | control supplement soft-delete mode all did not meet its physical/API contract | api/apps/restful_apis/chat_api.py:delete |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-CS-001 | 创建 Chat — 最小参数 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-001.json) |
| TC-CS-002 | 创建 Chat — 完整参数含 kb_ids 和 prompt_config | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-002.json) |
| TC-CS-003 | 创建 Chat — name 重复拒绝 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-003.json) |
| TC-CS-004 | 创建 Chat — name 超长拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-004.json) |
| TC-CS-005 | 创建 Chat — 传入 tenant_id 被拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-005.json) |
| TC-CS-006 | 创建 Chat — dataset_ids 中 kb_id 不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-006.json) |
| TC-CS-007 | 创建 Chat — dataset 中无 chunk 数据 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-007.json) |
| TC-CS-008 | 创建 Chat — 多个 dataset embedding 模型不一致 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-008.json) |
| TC-CS-009 | 创建 Chat — 无效 llm_id | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-009.json) |
| TC-CS-010 | 创建 Chat — 无效 rerank_id（非豁免模型） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-010.json) |
| TC-CS-011 | 查询 Chat 列表 — 默认分页 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-011.json) |
| TC-CS-012 | 查询 Chat 列表 — 分页参数 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-012.json) |
| TC-CS-013 | 查询 Chat 列表 — 按名称过滤 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-013.json) |
| TC-CS-014 | 查询单个 Chat | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-014.json) |
| TC-CS-015 | 查询不存在的 Chat | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-015.json) |
| TC-CS-016 | 查询已软删除的 Chat | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-016.json) |
| TC-CS-017 | PUT 更新指定 Chat 字段 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-017.json) |
| TC-CS-018 | PATCH 部分更新 — prompt_config 合并 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-018.json) |
| TC-CS-019 | PATCH 部分更新 — llm_setting 合并 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-019.json) |
| TC-CS-020 | 更新 Chat — 清空 llm_id 触发 EmptyStringCharField | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-020.json) |
| TC-CS-021 | 更新 Chat — 清空 rerank_id 触发 EmptyStringCharField | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-021.json) |
| TC-CS-022 | 更新 Chat — name 重复检查 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-022.json) |
| TC-CS-023 | 更新他人 Chat — 权限拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-023.json) |
| TC-CS-024 | 删除单个 Chat — 软删除 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-024.json) |
| TC-CS-025 | 批量删除 Chat — ids 列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-025.json) |
| TC-CS-026 | 批量删除 Chat — delete_all | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-026.json) |
| TC-CS-027 | 创建 Session — 最小参数 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-027.json) |
| TC-CS-028 | 创建 Session — 自定义名称 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-028.json) |
| TC-CS-029 | 查询 Session 列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-029.json) |
| TC-CS-030 | 查询单个 Session — 含 messages 和 references | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-030.json) |
| TC-CS-031 | 更新 Session — 仅允许修改名称 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-031.json) |
| TC-CS-032 | 批量删除 Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-032.json) |
| TC-CS-033 | 批量删除 Session — delete_all | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-033.json) |
| TC-CS-034 | 查询不存在的 Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-034.json) |
| TC-CS-035 | 删除消息对 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-035.json) |
| TC-CS-036 | 消息反馈 — 点赞（thumb up） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-036.json) |
| TC-CS-037 | 消息反馈 — 点踩（thumb down） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-037.json) |
| TC-CS-038 | 消息反馈 — 缺少 thumbup 字段 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-038.json) |
| TC-CS-039 | 流式 Completion — 新建 Session | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-039.json) |
| TC-CS-040 | 流式 Completion — 续接已有 Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-040.json) |
| TC-CS-041 | 非流式 Completion | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-041.json) |
| TC-CS-042 | Completion — messages 格式校验 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-042.json) |
| TC-CS-043 | Completion — question 回退（无 messages） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-043.json) |
| TC-CS-044 | Completion — pass_all_history_messages 标志 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-044.json) |
| TC-CS-045 | TTS 文本转语音 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-045.json) |
| TC-CS-046 | ASR 语音转文本 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-046.json) |
| TC-CS-047 | 思维导图生成 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-047.json) |
| TC-CS-048 | 获取推荐问题 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-048.json) |
| TC-CS-049 | 创建 Agent — 最小参数 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-049.json) |
| TC-CS-050 | 创建 Agent — 标题重复拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-050.json) |
| TC-CS-051 | 查询 Agent 列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-051.json) |
| TC-CS-052 | 查询 Agent 列表 — 按 tags 过滤 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-052.json) |
| TC-CS-053 | 查询单个 Agent — 含 DSL、版本、数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-053.json) |
| TC-CS-054 | 更新 Agent — 修改 DSL | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-054.json) |
| TC-CS-055 | 删除 Agent — 仅所有者可操作 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-055.json) |
| TC-CS-056 | 删除 Agent — 非所有者拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-056.json) |
| TC-CS-057 | 重置 Agent DSL | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-057.json) |
| TC-CS-058 | 查询 Agent 模板列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-058.json) |
| TC-CS-059 | 查询 Agent 内置 Prompt 模板 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-059.json) |
| TC-CS-060 | 查询 Agent 版本列表 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-060.json) |
| TC-CS-061 | 查询单个 Agent 版本 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-061.json) |
| TC-CS-062 | 创建 Agent Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-062.json) |
| TC-CS-063 | 查询 Agent Session 列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-063.json) |
| TC-CS-064 | 删除单个 Agent Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-064.json) |
| TC-CS-065 | 批量删除 Agent Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-065.json) |
| TC-CS-066 | 查询 Agent 执行日志 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-066.json) |
| TC-CS-067 | Webhook — POST 触发 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-067.json) |
| TC-CS-068 | Webhook — GET 触发 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-068.json) |
| TC-CS-069 | Webhook — Token 认证 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-069.json) |
| TC-CS-070 | Webhook — 无 Begin Webhook 组件拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-070.json) |
| TC-CS-071 | Webhook — 测试端点（仅所有者） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-071.json) |
| TC-CS-072 | Webhook — 查询执行日志 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-072.json) |
| TC-CS-073 | Webhook — 请求体超过 max_body_size | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-073.json) |
| TC-CS-074 | Chatbot Completion | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-074.json) |
| TC-CS-075 | Chatbot 信息查询 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-075.json) |
| TC-CS-076 | Agentbot Completion | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-076.json) |
| TC-CS-077 | Agentbot 输入表单查询 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-077.json) |
| TC-CS-078 | Searchbot 问答 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-078.json) |
| TC-CS-079 | Searchbot 检索测试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-079.json) |
| TC-CS-080 | Searchbot 思维导图 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-080.json) |
| TC-CS-081 | Chat llm_id 空串在 GaussDB 存为 NULL | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-081.json) |
| TC-CS-082 | Chat rerank_id 空串在 GaussDB 存为 NULL | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-082.json) |
| TC-CS-083 | 创建 Chat 后更新 llm_id 从 NULL → 有效值 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-083.json) |
| TC-CS-084 | JSONField 中嵌套空串值不被 EmptyStringCharField 处理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-084.json) |
| TC-CS-085 | 未认证访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-085.json) |
| TC-CS-086 | Chat 名称含特殊字符 | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-086.json) |
| TC-CS-087 | 大量消息的 Session Completion | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-087.json) |
| TC-CS-088 | 并发创建 Session | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-088.json) |
| TC-CS-089 | Completion 响应中 NaN/Infinity 清理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-089.json) |
| TC-CS-090 | Agent DSL 超大 JSON | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-090.json) |
| TC-CS-091 | Agent Chat Completion — 流式和非流式 | PASS | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-091.json) |
| TC-CS-092 | Agent 文件上传和下载 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-092.json) |
| TC-CS-093 | Agent 组件调试 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-093.json) |
| TC-CS-094 | Agent Tag 管理 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-094.json) |
| TC-CS-095 | Bot 端点 — beta token 认证 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-095.json) |
| TC-CS-096 | 跨租户访问 Chat 拒绝 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-096.json) |
| TC-CS-097 | 跨租户访问 Agent 拒绝 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-097.json) |
| TC-CS-098 | Agent permission="team" 的跨租户只读访问 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-098.json) |
| TC-CS-099 | DB 连接测试端点 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-099.json) |
| TC-CS-100 | Agent Rerun Pipeline | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CS-100.json) |
| TC-CHAT-DEL-001 | 删除 chat 是软删除 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-DEL-001.json) |
| TC-CHAT-DEL-002 | 软删除的 chat 不出现在列表 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-DEL-002.json) |
| TC-CHAT-DEL-003 | 软删除的 chat 无法访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-DEL-003.json) |
| TC-CHAT-DEL-004 | 批量删除 chats | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-DEL-004.json) |
| TC-CHAT-DEL-005 | delete_all 删除所有 chats | FAIL | PASS | FAIL | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-DEL-005.json) |
| TC-CHAT-PATCH-001 | PATCH 合并 prompt_config | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-PATCH-001.json) |
| TC-CHAT-PATCH-002 | PATCH 合并 llm_setting | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-PATCH-002.json) |
| TC-CHAT-PATCH-003 | PATCH 不传 prompt_config 不影响原值 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-CHAT-PATCH-003.json) |
| TC-AGENT-WH-001 | Webhook max_body_size 限制 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-001.json) |
| TC-AGENT-WH-002 | Webhook IP 白名单 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-002.json) |
| TC-AGENT-WH-003 | Webhook Rate Limiting | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-003.json) |
| TC-AGENT-WH-004 | Webhook Token Auth | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-004.json) |
| TC-AGENT-WH-005 | Webhook Token Auth - 错误 token | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-005.json) |
| TC-AGENT-WH-006 | Webhook Basic Auth | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-006.json) |
| TC-AGENT-WH-007 | Webhook JWT Auth | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-007.json) |
| TC-AGENT-WH-008 | Webhook allow_anonymous | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-008.json) |
| TC-AGENT-WH-009 | Webhook allow_anonymous = false | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-WH-009.json) |
| TC-AGENT-COMP-001 | 新会话模式（无 session_id） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-COMP-001.json) |
| TC-AGENT-COMP-002 | Session 模式（有 session_id） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-COMP-002.json) |
| TC-AGENT-COMP-003 | Streaming 模式 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-COMP-003.json) |
| TC-AGENT-COMP-004 | DataFlow 模式（异步任务） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-COMP-004.json) |
| TC-AGENT-COMP-005 | 取消运行中的任务 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/05_chat_session_agent/TC-AGENT-COMP-005.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 122 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **FAIL**。本组共有 20 个双方共同失败、3 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
