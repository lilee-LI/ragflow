# 06 Memory Metadata 全新执行计划

## 目标

在批次 `20260710_fresh_001` 的两套独立环境中，从当前两份 06 计划重新执行全部 47 个用例。每案固定先对照组（MySQL + Infinity），再实验组（GaussDB metadata + GaussDB Memory Store），不读取任何历史执行脚本或结果，不修改产品代码。

## 代码审查结论

- 当前 REST 路由为 `/api/v1/memories`、`/api/v1/memories/<id>`、`/api/v1/memories/<id>/config` 与 `/api/v1/messages*`；计划中的接口均存在。
- `POST /memories` 仅向 service 传递 `name/memory_type/embd_id/llm_id`，因此创建请求中的 `permissions` 会被忽略；计划 oracle 合理。
- `update_memory()` 静默忽略 `tenant_llm_id/tenant_embd_id`，并以 Redis size cache 限制非空 Memory 的 `embd_id/memory_type` 更新；计划已要求区分成功响应与真实 DB 变化。
- 当前消息 POST 同步保存 raw 行并更新 size cache，随后才投递提取任务；涉及“已有消息”的用例必须轮询 raw 消息可读并只读确认 cache 大于 0。
- `list_memory()` 的整数转换及 page_size 上界检查位于 route 的 `try` 外；非法类型/上界由全局异常处理包装，计划将其作为协议缺陷判定是合理的。
- Message 主键是 `<memory_id>_<message_id>`；跨 Memory 相同数值 ID 用例必须使用计划明确授权的专属 adapter fixture，不能直写 metadata 业务表。
- `avatar/description/system_prompt/user_prompt` 是普通 TextField。A-compatible GaussDB 的空串会物理变 NULL，API 回读为 JSON null；不得套用 `EmptyString*` 的空串 oracle。

## 实施步骤

1. 建立 `fresh_06_memory_metadata.py`、对应回归测试和独立 `evidence_private/06_memory_metadata` 目录；实现计划 ID/标题提取、control→experiment recorder、脱敏 HTTP 证据、真实模型 ID 和只读 metadata 快照。
2. 先实现并执行 TC-MM-001～008：创建、类型位值、重复名、必填字段、非法类型、模型可用性、名称边界和 permissions 创建/更新语义。
3. 实现并执行 TC-MM-009～024：列表/过滤/分页、更新边界、非空更新保护、配置 ACL、删除及空串/无认证语义。
4. 实现并执行 TC-MM-SUP-001～016：规范化、字段校验、团队关系、可访问列表、静默 no-op 与普通 TextField 空值。
5. 实现并执行 TC-MM-SUP-017～023：消息 owner/team/拒绝、跨 Memory ID 隔离、拒绝写入和分页框架错误；消息写入均轮询 raw 行及 size cache。
6. 每案经 API 清理 Memory、消息、团队关系和二级用户；无法通过公开 API 清理的 Task/cache/物理表状态只记录，不直写掩盖。
7. 完成 47/47 覆盖、权限、敏感信息、环境和残留审计，生成唯一中文报告 `06_memory_metadata_report.md`，实时更新 `PROGRESS.md`。

## 验证命令

```bash
.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_06_memory_metadata.py
.venv/bin/python -m py_compile docs/administrator/configurations/gaussdb_test_plan_execute/fresh_06_memory_metadata.py
.venv/bin/python docs/administrator/configurations/gaussdb_test_plan_execute/fresh_06_memory_metadata.py --case <CASE_ID>
```

正式运行每个 `<CASE_ID>` 时，runner 内部必须按 `control`、`experiment` 顺序生成同一份 case 证据。
