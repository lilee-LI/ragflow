# Chat/Session/Agent TC-CS-091～100 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改产品代码、不读取任何备份测试资产的前提下，为 TC-CS-091～100 建立可重复执行的双组 runner、契约自测、私密证据与现场结论。

**Architecture:** 继续扩展现有 `fresh_05_chat_session_agent.py`，复用统一 API、数据库只读快照、证据写入和 API 清理层。新增的协议能力分为 Agent Completion/SSE、二进制文件、租户权限、SSRF 连接和异步 DataFlow 五类；每个正式 runner 仍由 `_run_case` 保证 control→experiment 顺序。

**Tech Stack:** Python 3.13、pytest、requests、MySQL/GaussDB 只读连接、Redis 只读 key 校验、RAGFlow REST API。

## Global Constraints

- 不读取或引用任何备份测试脚本、结果或结论。
- 所有业务写入通过 REST API；数据库和 Redis 仅用于只读校验。
- 不修改产品代码，只修改本批次 fresh runner、测试、证据和进度文档。
- 原始证据目录为 `evidence_private/05_chat_session_agent`，文件 0600、目录 0700，凭据仅保留长度或指纹。
- 每案先 control，后 experiment；无效 runner 尝试必须清理后从头重跑，不能计入正式统计。

---

### Task 1: Agent Completion 与组件调试协议

**Files:**
- Modify: `docs/administrator/configurations/gaussdb_test_plan_execute/fresh_05_chat_session_agent.py`
- Test: `docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

**Interfaces:**
- Consumes: `_run_case`, `_request`, `_agent_session_rows`, `_cleanup_agent_sessions_and_agent`, `_tenant_default_llm`。
- Produces: `_agent_llm_dsl(llm_id)`, `_agent_sse_post(case_id, group, auth, label, payload)`, `agent_completion_contract_ok(observed)`, `agent_debug_contract_ok(observed)`, `run_cs091()`, `run_cs093()`。

- [ ] **Step 1: Write failing contract tests**

```python
def test_agent_completion_contract_requires_both_modes_and_persistence():
    observed = {
        "fixture_ready": True,
        "stream_http_status": 200,
        "stream_content_type_ok": True,
        "stream_done_seen": True,
        "stream_error_absent": True,
        "stream_answer_nonempty": True,
        "nonstream_http_status": 200,
        "nonstream_code": 0,
        "nonstream_answer_nonempty": True,
        "session_count": 2,
        "sessions_have_messages": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_completion_contract_ok(observed)

def test_agent_debug_contract_requires_real_input_form_and_output():
    observed = {
        "fixture_ready": True,
        "input_form_code": 0,
        "input_key_present": True,
        "debug_http_status": 200,
        "debug_code": 0,
        "output_nonempty": True,
        "cleanup_succeeded": True,
    }
    assert module.agent_debug_contract_ok(observed)
```

- [ ] **Step 2: Run the focused tests and confirm missing-symbol failures**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py -k 'agent_completion_contract or agent_debug_contract'`

Expected: FAIL because the new contracts do not exist.

- [ ] **Step 3: Implement minimal protocol helpers and runners**

```python
def _agent_llm_dsl(llm_id: str) -> dict[str, Any]:
    return {
        "components": {
            "begin": {"obj": {"component_name": "Begin", "params": {}}, "downstream": ["LLM:Fresh"], "upstream": []},
            "LLM:Fresh": {"obj": {"component_name": "LLM", "params": {"llm_id": llm_id, "sys_prompt": "Answer briefly.", "prompts": [{"role": "user", "content": "{sys.query}"}], "cite": False, "max_tokens": 128}}, "downstream": ["Message:Fresh"], "upstream": ["begin"]},
            "Message:Fresh": {"obj": {"component_name": "Message", "params": {"content": ["{LLM:Fresh@content}"]}}, "downstream": [], "upstream": ["LLM:Fresh"]},
        },
        "history": [], "messages": [], "reference": [], "retrieval": {"chunks": [], "doc_aggs": []},
        "path": [], "answer": [],
        "globals": {"sys.query": "", "sys.user_id": "", "sys.conversation_turns": 0, "sys.files": []},
        "variables": {},
    }

def _parse_agent_sse_frame(payload_text: str) -> tuple[str, Any]:
    if payload_text == "[DONE]":
        return "done", None
    return "event", _strict_json_loads(payload_text)
```

`_agent_sse_post` 必须在现有 `_sse_post` 请求/证据结构上调用 `_parse_agent_sse_frame`，分别累计 `events`、`done_count` 和 `parse_error_count`，不能把 `[DONE]` 计为 JSON 错误。

TC-CS-091 必须创建带非空 tags 的 LLM Agent，分别执行计划中的 stream=true/false 请求，确认两个持久化 Session 都有 user/assistant 消息，再经 Session API 和 Agent API 清理。TC-CS-093 必须先 GET input-form，以返回的 `sys.query` key 构造 `{"params":{"sys.query":{"value":"fresh debug probe"}}}`，再断言真实 LLM 输出非空。

- [ ] **Step 4: Run focused and full runner tests**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

Expected: all tests PASS.

---

### Task 2: 文件、标签与 Bot 凭据

**Files:**
- Modify: `docs/administrator/configurations/gaussdb_test_plan_execute/fresh_05_chat_session_agent.py`
- Test: `docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

**Interfaces:**
- Consumes: `_create_agent`, `_create_beta_credential`, `_cleanup_beta_credential`, `_agent_snapshot`。
- Produces: `_binary_get(case_id, group, label, auth, path, params)`, `agent_file_contract_ok(observed)`, `agent_tag_contract_ok(observed)`, `bot_credential_contract_ok(observed)`, `run_cs092()`, `run_cs094()`, `run_cs095()`。

- [ ] **Step 1: Add failing tests for exact bytes, tags, and credential separation**

```python
assert module.agent_file_contract_ok({
    "fixture_ready": True, "upload_code": 0, "file_id_present": True,
    "download_http_status": 200, "download_bytes_exact": True,
    "no_file_delete_endpoint_recorded": True, "agent_cleanup_succeeded": True,
})
assert module.agent_tag_contract_ok({
    "fixture_ready": True, "list_code": 0, "initial_count_exact": True,
    "update_code": 0, "database_tags_exact": True,
    "cleanup_succeeded": True,
})
assert module.bot_credential_contract_ok({
    "fixture_ready": True, "beta_status": 200, "beta_code": 0,
    "ordinary_status": 401, "ordinary_code": 401,
    "cleanup_succeeded": True,
})
```

- [ ] **Step 2: Confirm focused tests fail before implementation**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py -k 'agent_file_contract or agent_tag_contract or bot_credential_contract'`

- [ ] **Step 3: Implement API-only fixtures and cleanup**

TC-CS-092 上传内存生成的固定 UTF-8 文件，下载证据只保存 length/hash/prefix。当前 `FileService.upload_info` 只写 blob、不创建 `file` 行，产品也没有 Agent 文件删除 API；runner 必须记录该清理限制并保留 location 指纹，不能错误调用 `/files` 或直连 MinIO 删除。TC-CS-094 创建带初始标签 Agent，核对 `/agents/tags` 聚合后 PUT 中文标签并只读检查物理字段。TC-CS-095 使用同一 `/system/tokens` 响应中的 beta 与 ordinary token，以无 cookie 请求分别命中 200/0 和 401/401，最后删除 token 与 Chat。

- [ ] **Step 4: Run focused and full runner tests**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

Expected: all tests PASS.

---

### Task 3: 跨租户只读与越权写入判定

**Files:**
- Modify: `docs/administrator/configurations/gaussdb_test_plan_execute/fresh_05_chat_session_agent.py`
- Test: `docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

**Interfaces:**
- Consumes: `_prepare_secondary_user`, `_cleanup_secondary_user`, `_request`, `_chat_snapshot`, `_agent_snapshot`。
- Produces: `_membership_snapshot(group, user_id, tenant_id)`, `_invite_and_accept_member(case_id, group, owner, secondary)`, `cross_tenant_chat_contract_ok(observed)`, `cross_tenant_agent_contract_ok(observed)`, `team_agent_contract_ok(observed)`, `run_cs096()`, `run_cs097()`, `run_cs098()`。

- [ ] **Step 1: Add failing security contract tests**

```python
assert module.cross_tenant_chat_contract_ok({
    "fixture_ready": True, "get_status": 200, "get_code": 109,
    "delete_status": 200, "delete_code": 109,
    "chat_unchanged": True, "cleanup_succeeded": True,
})
assert module.cross_tenant_agent_contract_ok({
    "fixture_ready": True, "get_status": 200, "get_code": 103,
    "agent_unchanged": True, "cleanup_succeeded": True,
})
assert module.team_agent_contract_ok({
    "fixture_ready": True, "membership_role": "normal",
    "get_status": 200, "get_code": 0,
    "update_rejected": True, "agent_unchanged": True,
    "cleanup_succeeded": True,
})
```

- [ ] **Step 2: Confirm focused tests fail**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py -k 'cross_tenant or team_agent_contract'`

- [ ] **Step 3: Implement owner/member lifecycle and security oracles**

TC-CS-096/097 使用完全独立的 B 用户。TC-CS-098 由 A POST 邀请、B PATCH 接受并只读确认 role=normal；GET 必须成功，PUT 必须业务失败且数据库不变。若 PUT 成功，正式状态为 FAIL，记录 security finding，由 A 恢复标题后清理 Agent、成员关系和 B 用户，绝不把现状降级为预期。

- [ ] **Step 4: Run focused and full runner tests**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

Expected: contract tests PASS; formal TC-CS-098 may legitimately expose a product FAIL.

---

### Task 4: SSRF 与真实 DataFlow rerun

**Files:**
- Modify: `docs/administrator/configurations/gaussdb_test_plan_execute/fresh_05_chat_session_agent.py`
- Test: `docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

**Interfaces:**
- Consumes: current `gaussdb_info.md`, DD dataset/document API helpers, `_request`, `_create_agent`。
- Produces: `_load_gauss_connection_fixture()`, `db_connection_contract_ok(observed)`, `_dataflow_template()`, `_pipeline_log_rows(group, document_id, pipeline_id)`, `dataflow_rerun_contract_ok(observed)`, `run_cs099()`, `run_cs100()`。

- [ ] **Step 1: Add failing SSRF and rerun contract tests**

```python
assert module.db_connection_contract_ok({
    "loopback_status": 200, "loopback_failed": True,
    "loopback_message_mentions_unsafe": True,
    "allowed_status": 200, "allowed_code": 0,
    "allowed_data_exact": True,
})
assert module.dataflow_rerun_contract_ok({
    "fixture_ready": True, "initial_terminal": True,
    "log_id_present": True, "dsl_present": True,
    "component_present": True, "rerun_status": 200, "rerun_code": 0,
    "rerun_task_observed": True, "rerun_terminal": True,
    "cleanup_succeeded": True,
})
```

- [ ] **Step 2: Confirm focused tests fail**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py -k 'db_connection_contract or dataflow_rerun_contract'`

- [ ] **Step 3: Implement private fixture loading and async polling**

TC-CS-099 从当前信息文件解析 host/port/db/user/password，证据 sanitizer 必须对 password 字段脱敏；先断言 127.0.0.1 被 unsafe-host guard 拒绝，再用解析为公网的专用 GaussDB 执行 postgres `SELECT 1` 成功路径。TC-CS-100 从当前产品 `agent/templates/ingestion_pipeline_one.json` 读取 DSL，经 API 创建 DataFlow Agent、Dataset、TXT Document，PATCH `pipeline_id` 后触发 parse；轮询 terminal 与 file ingestion log，使用 detail 返回的真实 DSL 和 Parser component id 调 `/agents/rerun`，只读确认 `dataflow_rerun` task 并再次 terminal，最后 API 清理 Dataset 和 Agent。

- [ ] **Step 4: Run full tests and syntax compilation**

Run: `.venv/bin/pytest -q docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

Run: `.venv/bin/python -m py_compile docs/administrator/configurations/gaussdb_test_plan_execute/fresh_05_chat_session_agent.py docs/administrator/configurations/gaussdb_test_plan_execute/test_fresh_05_chat_session_agent.py`

Expected: tests and compilation exit 0.

---

### Task 5: Formal execution and checkpoint audit

**Files:**
- Modify: `docs/administrator/configurations/gaussdb_test_plan_execute/PROGRESS.md`
- Create/overwrite: `docs/administrator/configurations/gaussdb_test_plan_execute/evidence_private/05_chat_session_agent/TC-CS-091.json` through `TC-CS-100.json`

**Interfaces:**
- Consumes: `RUNNERS["TC-CS-091"]` through `RUNNERS["TC-CS-100"]`。
- Produces: ten fresh case records, updated cumulative counts, hygiene and residual-resource audit.

- [ ] **Step 1: Run each case in numeric order**

Run separately for each ID:

```bash
.venv/bin/python docs/administrator/configurations/gaussdb_test_plan_execute/fresh_05_chat_session_agent.py --case TC-CS-091
```

Expected: each command completes both control and experiment; a product FAIL is recorded rather than converted to runner failure.

- [ ] **Step 2: Audit exact coverage and hygiene**

Require 100 contiguous case JSON files, 200 ordered group records, zero 0600/0700 violations, zero known-runtime-secret matches, zero `sk-`/`ragflow-` credential shapes, and zero active `fresh-cs-` Agent/Chat/Dataset/Search resources.

- [ ] **Step 3: Update progress from evidence only**

Record each result, invalid-attempt correction, residual orphan counts, test command output, and the next numeric case. Do not infer unexecuted results.

## Self-Review

- TC-CS-091～100 每个计划 ID 均映射到唯一 runner 和契约。
- multipart、SSE sentinel、普通/beta token、team membership、SSRF 双路径、真实 DataFlow log/rerun 均有明确判定。
- 所有存在删除 API 的失败路径都有 API 清理；Agent upload blob、DataFlow 历史 log 和 Agent delete 残留只记录、不直写清除。
- 文件中没有未决占位要求；所有 helper 名称、参数和执行命令均已写明。
