# 05 - Text-to-SQL 测试方案

<!-- GAUSSDB-DISTRIBUTED-INTEGRATION-MATRIX:START -->
## 集成环境结果矩阵

本轮必须使用当前分支分别在真实 Centralized 与 Distributed GaussDB 上执行。历史结果仅作参考，不进入本轮验收；下表只有固定 JUnit 中真实存在且通过的 node 才能改为 `Passed`。

> 当前分支已于 2026-08-12 完成双环境数据库路径补充复跑。评审后调整 aggregate source `LIMIT/OFFSET` 处理的 `TC-SQL-701` 已在两份当前 JUnit 中通过；其它行保留的 `gaussdb-*-latest.xml` 只表示冻结历史证据，当前总体口径见 08 报告。

| TC | 环境适用性 | 集中式 pytest node | 集中式结果 | 集中式证据 | 分布式 pytest node | 分布式结果 | 分布式证据 | 整体状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-SQL-006 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_006_table_chunk_persists_real_field_map` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_006_table_chunk_persists_real_field_map` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-007 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_007_table_chunk_data_reaches_real_jsonb_table` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_007_table_chunk_data_reaches_real_jsonb_table` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-411 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_411_jsonb_missing_null_and_empty_values` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_411_jsonb_missing_null_and_empty_values` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-501 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_501_sql_uses_real_validator_and_returns_rows` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_501_sql_uses_real_validator_and_returns_rows` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-504 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_504_sql_statement_timeout_is_reset_after_checkout` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_504_sql_statement_timeout_is_reset_after_checkout` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-505 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_505_sql_returns_columns_and_rows` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_505_sql_returns_columns_and_rows` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-507 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_507_sql_preserves_columns_for_empty_rows` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_507_sql_preserves_columns_for_empty_rows` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-508 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_508_sql_returns_real_markdown` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_508_sql_returns_real_markdown` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-509 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_509_adapter_database_error_and_use_sql_retry_are_real` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_509_adapter_database_error_and_use_sql_retry_are_real` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-701 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_701_aggregate_sql_fetches_real_source_chunks` | Passed | `test/integration/artifacts/gaussdb-centralized-clean-20260812-151227.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_701_aggregate_sql_fetches_real_source_chunks` | Passed | `test/integration/artifacts/gaussdb-distributed-current-20260812-141912.xml` | Covered |
| TC-SQL-702 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_702_aggregate_source_lookup_is_real_validator_scoped` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_702_aggregate_source_lookup_is_real_validator_scoped` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-704 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_704_aggregate_without_where_gets_scoped_source_lookup` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_704_aggregate_without_where_gets_scoped_source_lookup` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-801 | Both | `test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_801_text_to_sql_multikb_reference_completes_kb_id_in_live_gaussdb` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_801_text_to_sql_multikb_reference_completes_kb_id_in_live_gaussdb` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-802 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_802_single_kb_reference_gets_kb_id_without_lookup` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_802_single_kb_reference_gets_kb_id_without_lookup` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-901 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_901_use_sql_answer_is_markdown` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_901_use_sql_answer_is_markdown` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-902 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_902_unmapped_exposed_column_keeps_original_name` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_902_unmapped_exposed_column_keeps_original_name` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-903 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_903_reference_chunks_have_source_shape` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_903_reference_chunks_have_source_shape` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-904 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_904_reference_doc_aggs_count_real_rows` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_904_reference_doc_aggs_count_real_rows` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-905 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_905_use_sql_returns_answer_reference_and_prompt` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_905_use_sql_returns_answer_reference_and_prompt` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-1006 | Both | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_1006_async_chat_falls_back_to_real_dealer_retrieval` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_1006_async_chat_falls_back_to_real_dealer_retrieval` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-SQL-1007 | Both | `test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_1007_text_to_sql_use_sql_to_gaussdb_adapter_full_chain` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_1007_text_to_sql_use_sql_to_gaussdb_adapter_full_chain` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
<!-- GAUSSDB-DISTRIBUTED-INTEGRATION-MATRIX:END -->


## 概述

本测试方案覆盖 GaussDB DocEngine 的 Text-to-SQL **支路**：基于 `sqlglot` 的只读 validator、`kb_id` 边界补丁、limit 补丁、JSONB 语法校验、`use_sql()` 的 prompt/repair/retry/aggregate fallback，以及**失败后由检索链路兜底**的分支切换。对标技术设计 3.5 节、3.5.6 测试与验收。

**支路定位**：Text-to-SQL 是支路，不是主检索路径。dataset 指定 `table` 且存在 `field_map` 时先尝试 Text-to-SQL。只有 validator 拒绝、主 SQL 执行失败，或在尚未得到可用 SQL 结果前 retry 最终失败导致 `use_sql()` 返回 `None` 时，才由 04 检索链路兜底；主 SQL 已成功但 source-column/aggregate source 补查失败时返回 best-effort answer，不切普通检索。

**环境标记**：每条用例「类型」带 `· 单元` / `· 集成`：
- `· 单元`：纯函数 / validator 校验（1xx-4xx 多数）/ mock 反向错误（503 超时等）
- `· 组件单元`：真实执行 `use_sql`/validator/流程编排，只在 chat model 或 retriever 外部边界使用确定 fake
- `· 集成`：需 `GAUSSDB_INTEGRATION=1` + 真实 GaussDB；不得读取 mock 的 `call_args` 冒充真实库断言

**本次补齐状态**：05 中 21 条集成 TC 均已有不同主函数：`006/007/411/501/504/505/507/508/509/701/702/704/801/802/901/902/903/904/905/1006/1007`。`TC-SQL-005` 仅验证空 `kb_ids` 的组件前置校验，归入组件单元，不进入真实 GaussDB 集成文件。两环境当前结果只以顶部矩阵及各自固定 JUnit 为准。除明确将生产 `insert()` 作为被测动作的 TC 外，数据库前置数据由独立 psycopg2 SQL 写入；所有结果与清理由固定 expected 或独立 SQL/metadata DB 探针验证。Fake LLM 仅提供确定 SQL 或最终回答，`sql()`、`Dealer`、`use_sql()` 和 `async_chat()` 均走真实实现。

**重新分类**：`TC-SQL-703` 要求注入 source lookup 异常，归为组件单元，不在集成测试中伪造 retriever 异常；`TC-SQL-604` 覆盖 `None` 执行结果进入既有 retry 路径。05 的 21 个集成 node 已有 Centralized 历史执行；Distributed 必须单独生成带环境 properties 的新结果，不使用静态检查替代运行结果。

**字段继承**：未单列 Setup/Cleanup 的用例严格继承 [README 的 S/A/A/C 契约](README.md#setup--action--assert--cleanup-继承契约)，不得自行选择其它 fixture 或清理方式。

**测试范围**：
- 支路进入条件（`table` + `field_map` + `DOC_ENGINE_GAUSSDB`）
- validator 拒绝矩阵（多语句 / DML / DDL / CALL / COPY / SELECT * / 窗口函数 / 非白名单表列 / 禁用函数 / JSON 函数 / JOIN·UNION·OR / 动态 path / 跨 schema / 跨 tenant）
- `kb_id` 边界补丁（自动注入 / 已有合法边界 / 跨 KB 拒绝 / 复杂 SQL 拒绝 / 多表拒绝）
- limit 补丁（缺失补 / 过大改 / 复杂 scope 前置拒绝）
- JSONB 语法校验（`#>>` / `#>` / 多键 / 引号 / CAST / to_date / IS NULL / 未声明 path）
- `sql()` 执行与返回（validator 接入、timeout、columns+rows、markdown）
- 初始 prompt / repair / retry
- aggregate fallback / source chunk 补查
- 多 KB reference 补全
- 返回结构（answer / reference.chunks / doc_aggs）
- 失败兜底切检索

**涉及文件**：

| 文件 | 职责 |
| --- | --- |
| `common/doc_store/gaussdb_conn_base.py` | `GaussDBSQLValidator`（sqlglot 只读校验 + 补丁） |
| `rag/utils/gaussdb_text_to_sql.py` | GaussDB 专属 prompt、repair/retry、聚合 source SQL 与多 KB 引用补全 |
| `api/db/services/dialog_service.py` | `use_sql()` 中最小 GaussDB 分支选择与流程调用；非 GaussDB 原路径保持不变 |
| `rag/app/table.py` | `table` 解析方式 + `field_map` 生成 + `chunk_data` 存储 |
| `rag/utils/table_es_metadata.py` | `chunk_data` 分支聚合 |
| `rag/utils/gaussdb_conn.py` | `sql()` 执行只读 SQL |

**关键方法**：

| 方法 | 签名 | 说明 |
| --- | --- | --- |
| `GaussDBSQLValidator.__init__` | `(tables, kb_ids, default_limit=128, readonly_only=False, runtime_readonly_guard=False)` | validator 构造 |
| `validate_and_patch` | `(raw_sql) -> ValidatedGaussDBSQL(sql, columns, is_aggregation)` | 校验 + 补 kb_id / limit |
| `gaussdb_text_to_sql.build_sql_prompt` | `(table_name, field_map, question) -> str` | 生成 GaussDB 专属 SQL prompt |
| `use_sql` | `async (question, field_map, tenant_id, chat_mdl, quota=True, kb_ids=None, doc_ids=None) -> dict \| None` | 支路主流程；先注入可选文档 scope，再由 validator 补 KB 边界；返回 None 触发兜底 |
| `sql` | `(sql, fetch_size=128, format="json") -> dict` | 执行只读 SQL，返回 columns+rows |
| `chunk` | `(filename, binary, from_page, to_page, lang, callback, **kwargs)` | table 解析，生成 field_map / chunk_data |

**白名单**：
- 表：`ragflow_[A-Za-z0-9_]{1,56}` 格式或显式 `tables` 参数
- 列：`{doc_id, docnm_kwd, kb_id, chunk_data}`
- JSONB 访问：仅 `#>` / `#>>`（禁止 `json_extract*`）

**validator 拒绝矩阵速查**：

| 拒绝类型 | 触发 | 异常消息 |
| --- | --- | --- |
| 多语句 | 含 `;` | multiple statements are not allowed |
| 非 SELECT | AST 非 `exp.Select` | only SELECT statements are allowed |
| SELECT * | `exp.Star` 非 COUNT(*) | SELECT * is not allowed |
| 窗口函数 | `exp.Window` | window functions are not allowed |
| 非白名单表 | 表名不合规 | table {t} is not allowed / cross-schema |
| 非白名单列 | 列名不合规 | column {c} is not allowed |
| 禁用函数 | `pg_sleep`/`now`/`current_user`/`version` | function {f} is not allowed |
| JSON 函数 | `json_extract*` | only #> / #>> allowed |
| JOIN/UNION/OR | `exp.Or` 或正则 | complex SQL must use simpler single-table kb_id boundary |
| 动态 path | `exp.Literal` 非 string | dynamic JSONB path is not allowed |
| kb_id 缺失 | 无 kb_ids | kb_id boundary is required |
| 多表 | base_tables != 1 | must reference exactly one base table |
| 未声明 path | path not in allowed_paths | JSONB path {p} is not exposed |
| chunk_data 裸访问 | 未通过 #>>/#> | chunk_data may only be accessed through #> / #>> |

**环境连接**：单元层（1xx-4xx、5xx 部分）无需 DB；集成层见 [README 集成测试环境连接](README.md#集成测试环境连接)。

**调用约定**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["k1"], default_limit=128)
validated = v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{amount}' = '100'")
```

**集成用例数据约定**：
- 使用 `tenant_id = gaussdb_env["table_prefix"]`，`table = f"ragflow_{tenant_id}"`，或使用现有 helper 生成带 TC 编号的唯一表名；不得硬编码共享表 `ragflow_t1`。
- 对话链路和 `use_sql` 用例使用合法 UUID 形态 KB ID：`kb_id = "abcdefabcdefabcdefabcdefabcdefab"`；多 KB 用例使用 `kb1 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"`、`kb2 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"`。纯 validator 单元用例可以继续使用短 ID。
- 清理只删除/清空本用例创建的唯一表和行；优先用 helper/fixture cleanup，禁止直接删除共享表 `ragflow_t1`。

> 🔗 环境连接（环境变量、`gaussdb_env` fixture、schema 隔离、跑命令）见 [README 集成测试环境连接](README.md#集成测试环境连接)。

---

## 一、支路进入条件（0xx）

### TC-SQL-001: table + field_map + GaussDB 进入支路

**类型**：正向 · 组件单元 · P0

**前置条件**：无需数据库；`dialog.kb_ids=[kb_id]`，其中 `kb_id="abcdefabcdefabcdefabcdefabcdefab"`；`dialog.tenant_id=tenant_id`；`DOC_ENGINE_GAUSSDB=True`；mock `KnowledgebaseService.get_field_map(dialog.kb_ids)` 返回 `{"amount": "number"}`；mock `dialog_service.use_sql` 为 `AsyncMock(return_value={"answer": "|amount|\n|---|\n|100|", "reference": {"chunks": [], "doc_aggs": []}, "prompt": "gaussdb prompt"})`；mock `settings.retriever.retrieval` 为 `AsyncMock`。

**步骤**：
1. 调用 `async_chat(dialog, [{"role": "user", "content": "What is the total amount?"}], stream=False)` 并收集 async generator 输出。
2. 断言 `use_sql.assert_awaited_once()` 且 `settings.retriever.retrieval.assert_not_called()`。

**预期结果**：
- `KnowledgebaseService.get_field_map.assert_called_once_with([kb_id])`。
- `use_sql.assert_awaited_once()`，调用参数中 `question == "What is the total amount?"`、`field_map == {"amount": "number"}`、`tenant_id == dialog.tenant_id`、`kb_ids == [kb_id]`；不传 `parser_id`，因为 `use_sql` 签名为 `(question, field_map, tenant_id, chat_mdl, quota=True, kb_ids=None)`。
- `settings.retriever.retrieval.assert_not_called()`。
- async generator yield 的对象为 dict，至少含 `answer`、`reference`、`prompt`；`answer` 非空。
- 异常断言：本用例不抛异常；若 mock `use_sql` 抛 `ValueError("GaussDB Text-to-SQL requires kb_ids")`，测试应失败而不是吞掉。

**清理**：pytest `monkeypatch`/mock 自动恢复；不创建表或数据库行。

**验收口径**：技术设计 3.5「table+field_map+GaussDB 进入支路」

**优先级**：P0

### TC-SQL-002: field_map 为空走检索

**类型**：边界 · 组件单元 · P0

**前置条件**：无需数据库；`dialog.kb_ids=[kb_id]`，其中 `kb_id="abcdefabcdefabcdefabcdefabcdefab"`；mock `KnowledgebaseService.get_field_map(dialog.kb_ids)` 返回 `{}`；mock `dialog_service.use_sql` 为 `AsyncMock`；mock `settings.retriever.retrieval` 为 `AsyncMock(return_value={"chunks": [{"doc_id": "d1", "docnm_kwd": "doc.xlsx", "kb_id": kb_id, "content_ltks": "content", "vector": []}], "doc_aggs": [{"doc_id": "d1", "doc_name": "doc.xlsx", "count": 1}]})`；`DOC_ENGINE_GAUSSDB=True`。

**步骤**：
1. 调用 `async_chat(dialog, [{"role": "user", "content": "What is the content?"}], stream=False)` 并收集输出。
2. 断言 `use_sql.assert_not_awaited()` 且 `settings.retriever.retrieval.assert_awaited_once()`。

**预期结果**：
- `KnowledgebaseService.get_field_map.assert_called_once_with([kb_id])`。
- `use_sql.assert_not_awaited()`。
- `settings.retriever.retrieval.assert_awaited_once()`，调用参数含 `kb_ids=[kb_id]`。
- async generator yield 的对象为 dict，至少含 `answer` 和 `reference`；最终引用来自 `retriever.retrieval` 的 `chunks/doc_aggs`，不是裸 `SearchResult`。
- 日志不含 `"Use SQL to retrieval"`；异常断言：本用例不抛异常。

**清理**：pytest `monkeypatch`/mock 自动恢复；不创建表或数据库行。

**验收口径**：技术设计 3.5「field_map 为空跳过支路」

**优先级**：P0

### TC-SQL-004: 非 GaussDB 引擎不走 GaussDB 分支

**类型**：边界 · 组件单元 · P1

**前置条件**：
```python
from api.db.services.dialog_service import use_sql
from unittest.mock import Mock, AsyncMock, patch
import common.settings as settings
chat_mdl = Mock()
chat_mdl.async_chat = AsyncMock(return_value="SELECT doc_id FROM ragflow_t1")
field_map = {"amount": "number"}
tenant_id = "t1"
kb_ids = ["abcdefabcdefabcdefabcdefabcdefab"]
```

**步骤**：
1. 补前置：`monkeypatch.setattr(settings, "DOC_ENGINE_GAUSSDB", False)`
2. 调用 `use_sql("What is the amount?", field_map, tenant_id, chat_mdl, kb_ids=kb_ids)`
3. 再开启 Infinity 标志，以 fake retriever 模拟“首次 SQL 抛错、retry 返回缺少引用列、source repair 成功”，完整执行非 GaussDB retry/repair 链。

**预期结果**：
- `use_sql` 选择 `doc_engine = "es"`；`gaussdb_text_to_sql.build_sql_prompt` 与 `GaussDBSQLValidator` 均为零调用。
- LLM system prompt 精确为 ES direct-field 规则，user prompt 精确列出 `ragflow_<tenant_id>`、`amount (number)` 与问题 `show docs`，不含 GaussDB A/ORA 规则。
- 执行 SQL 精确为 `SELECT doc_id, docnm_kwd FROM ragflow_<tenant_id> WHERE kb_id = '<kb_id>'`。
- 返回对象精确包含 Markdown answer、带 `doc_id/docnm_kwd/kb_id` 的 chunk、对应 `doc_aggs` 和 ES system prompt。
- Infinity retry 与 source repair prompt 均继续使用 `json_extract_string`，分别包含原始错误和“补齐 doc_id/docnm”要求；不得出现 GaussDB `#>>` 或 A/ORA 修复规则。
- 三次 Infinity SQL 均进入 fake retriever，最终引用列完整；`gaussdb_text_to_sql.build_sql_prompt` 与 `GaussDBSQLValidator` 在 ES 和 Infinity 两个子场景中始终零调用。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_004_use_sql_non_gaussdb_engine_does_not_call_gaussdb_prompt`。

**验收口径**：技术设计 3.5「非 GaussDB 引擎跳过 GaussDB 分支」

**优先级**：P1

### TC-SQL-005: kb_ids 为空拒绝

**类型**：异常 · 组件单元 · P0

**前置条件**：无需数据库；使用现有 `use_sql` 组件单元边界，准备非空 field_map；但调用时 `kb_ids=[]`

**步骤**：
1. 调用 `use_sql("What is the total amount?", field_map, tenant_id, chat_mdl, kb_ids=[])`

**预期结果**：
- 抛 `ValueError`
- 固定断言：`with pytest.raises(ValueError, match="GaussDB Text-to-SQL requires kb_ids")`
- **不进入 validator 校验**（前置检查失败）

**验收口径**：技术设计 3.5「kb_ids 为空拒绝」

**优先级**：P0

### TC-SQL-006: table.py 生成 field_map

**类型**：正向 · 集成 + 组件单元 · P1

**前置条件**：`gaussdb_env` fixture 就绪；准备 Excel binary（含表格数据：amount, status 列）；已建 KB（`parser_id="table"`）

**步骤**：
1. 调用 `chunk(filename="sales.xlsx", binary=excel_bytes, from_page=0, to_page=10, lang="en", callback=lambda *a: None)`
2. DB 探针验证 field_map 存入 KB `parser_config`。
3. 用唯一 GaussDB 表和独立 SQL seed 写入首个 parser chunk 的 `chunk_data`，再由生产 `conn.sql()` 使用持久化 field_map 的 `amount` path 执行真实 JSONB 查询。

**预期结果**：
- `Excel.__call__` 返回 `(res, tables)`，其中 `res` 是 DataFrame 列表
- `field_map = {"amount": "amount", "status": "status"}`，key 使用 `py_clmns.lower()`
- KB `parser_config["field_map"]` 精确等于 `field_map`
- DB 探针 `SELECT parser_config FROM knowledgebase WHERE id=%s` 使用 `ragflow_kb_context["kb_id"]` 或 `kb_id` fixture 变量作为参数，返回 JSON 中 `parser_config["field_map"] == field_map`
- 真实 GaussDB 查询返回 `{"columns":[{"name":"amount","type":"text"}],"rows":[["100"]]}`，证明该 field_map 能驱动 DocEngine JSONB 路径，不是只构造连接。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「table.py 生成 field_map」

**组件单元自动化**：`test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_006_chunk_gaussdb_updates_field_map_and_column_names`。该测试执行真实 `chunk()` 并精确断言 `KnowledgebaseService.update_parser_config` payload；不替代本用例的真实 DB 探针。

**优先级**：P1

### TC-SQL-007: chunk_data 存储（GaussDB backend）

**类型**：正向 · 集成 + 组件单元 · P0

**前置条件**：`gaussdb_env` fixture 就绪；准备 Excel binary（含表格数据）；已建 KB（`parser_id="table"`）

**步骤**：
1. 调用 `chunk(filename="sales.xlsx", binary=excel_bytes, from_page=0, to_page=10, lang="en", callback=lambda *a: None)`（GaussDB backend）
2. DB 探针验证 chunk_data 存储

**预期结果**：
- `Excel.__call__` 返回 `(res, tables)`
- `d["chunk_data"] = {"amount": 200, "status": "active"}`（Excel 数值单元格保持数值类型）
- 生产 `GaussDBConnection.insert()` 写入后，独立 DB 探针 `SELECT chunk_data, q_4_vec_valid FROM <table> WHERE id='chunk-007'` 返回 `({"amount": 200, "status": "active"}, true)`
- 非 GaussDB 时 `d.update(stored)`（typed fields，非 JSONB）
- chunk_data 字段类型为 JSONB（GaussDB 特有）

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「chunk_data 存储 JSONB」

**组件单元自动化**：`test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_007_chunk_manual_mode_gaussdb_stores_metadata_in_chunk_data`。该测试执行真实 `chunk()`，断言 `chunk_data` dict、索引文本与 ES typed field 的互斥；不伪装为真实 JSONB 落库。

**优先级**：P0

### TC-SQL-008: GaussDB `row_id` 的 chunk / field_map / prompt 闭环

**类型**：正向 · 组件单元 · P1

**前置条件与输入**：使用固定 CSV，列为 `row_id/title/content/country/category`；启用 GaussDB，关闭 Infinity/OceanBase；mock 仅限 `KnowledgebaseService.update_parser_config` 与 tokenizer；parser config 使用 auto 模式 `{}`。

**步骤**：
1. 调用公开 `rag.app.table.chunk()`。
2. 检查第一行 `content_with_weight`、`chunk_data` 和 KB parser config 中的 `field_map`。
3. 将该 `field_map` 传给真实 `gaussdb_text_to_sql.build_sql_prompt`，检查生成的 JSONB path。

**预期结果**：
- `content_with_weight` 精确包含 `- row_id: 1`。
- `chunk_data["row_id"] == 1`，不得变成 `"row id"` key。
- `field_map["row_id"] == "row_id"`，且完整 field_map 的每个 path 都真实存在于 `chunk_data`。
- prompt 精确包含 `row_id (row_id): chunk_data #>> '{row_id}'`；不得生成无法命中的 `'{row id}'`。
- 自动化：`test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_008_chunk_auto_mode_preserves_row_id_text_and_prompt_path`。上游非 GaussDB 原测试保持原名、原逻辑，不修改也不作为本用例的唯一证据。

**清理**：pytest fixture 恢复 settings、tokenizer 和 KB service patch；无 DB 数据。

**优先级**：P1

### TC-SQL-009: parser config 与 table_column_names 传播

**类型**：正向 · 组件单元 · P1

**前置条件与输入**：task 的文档级 `parser_config={"llm_id":"x"}`，KB 级配置含 `table_column_mode="manual"`、roles 和 `table_column_names=["a","b"]`；另用固定 CSV 调用 `chunk()`；清理键输入覆盖 names 存在、names 为空和仅 roles 三种形态。

**步骤**：
1. 调用公开 `merge_table_parser_config_from_kb(task)`。
2. 调用公开 `table_parser_strip_doc_metadata_keys(effective_config)`。
3. 调用公开 `chunk()` 并读取 `update_parser_config` payload。

**预期结果**：
- 合并结果保留文档级 `llm_id`，并以 KB 级 table mode/roles/names 补齐表格配置；KB 不含 table keys 时不复制无关键。
- 清理键优先使用去空白后的 `table_column_names`；names 空或缺失时回退到 role keys。
- `chunk()` 传播顺序稳定的 `table_column_names == ["row_id","title","content","country","category"]`。
- 自动化：`test/unit_test/rag/svr/test_gaussdb_table_column_roles_helpers.py::test_tc_sql_009_parser_config_merge_and_metadata_cleanup_keys`；chunk 传播复用 GaussDB 自有 `test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_006_chunk_gaussdb_updates_field_map_and_column_names`。不修改或依赖 RAGFlow 原测试文件。

**清理**：pytest fixture 恢复 patch；无 DB 数据。

**优先级**：P1

### TC-SQL-010: ES 公共 chunk 结构聚合文档元数据

**类型**：正向 · 单元 · P1

**前置条件与输入**：关闭 SQL DocEngine；输入公开 chunk 结构，包含 `guojia_tks/guojia_raw/category_tks/score_long`；field_map 把 typed key 映射到 `country/category/score`；roles 将 `title` 设为 `indexing`，其余为 `metadata/both`。

**步骤**：调用公开 `aggregate_table_doc_metadata(chunks, task)`，不直接调用 `_probe_*`、`_resolve_*`、`_es_*` 私有 helper。

**预期结果**：
- `country` 优先聚合 raw 值并去重为 `["Brazil"]`。
- 缺 raw 的 token list 通过公共行为聚合为 `category == ["Risk Audit","Operations"]`。
- 数值 typed field 归一为字符串 `score == ["3","4"]`。
- indexing-only 的 `title` 不进入文档元数据。
- 自动化：`test/unit_test/rag/svr/test_gaussdb_table_column_roles_helpers.py::test_tc_sql_010_es_aggregates_public_chunk_metadata`。

**优先级**：P1

### TC-SQL-011: GaussDB chunk_data 聚合文档元数据

**类型**：正向 · 单元 · P0

**前置条件与输入**：仅启用 GaussDB；chunks 含重复 country、两个非空 category、一个 JSON null，以及一个只有 `country_raw` 而无 `chunk_data` 的干扰项；roles 将 `title` 设为 `indexing`。

**步骤**：调用公开 `aggregate_table_doc_metadata(chunks, task)`。

**预期结果**：
- 仅从 `chunk_data` 读取原始值，不读取 ES `*_raw` 干扰项。
- `country == ["Turkey","EU"]`，保持首次出现顺序并去重。
- `category == ["Disaster","Economy"]`，跳过 JSON null。
- indexing-only 的 `title` 不进入结果。
- 自动化：`test/unit_test/rag/svr/test_gaussdb_table_column_roles_helpers.py::test_tc_sql_011_gaussdb_aggregates_chunk_data_metadata`。

**优先级**：P0

---

## 二、validator 拒绝矩阵（1xx）

> 本组为纯函数 validator 校验，单元层验证（无需 DB）。

### TC-SQL-101: 合法单表 SELECT 通过

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id, docnm_kwd FROM ragflow_t1 WHERE kb_id = 'kb-1' ORDER BY doc_id LIMIT 5")`

**预期结果**：
- 通过验证，返回 `ValidatedGaussDBSQL` 对象
- `validated.sql == "SELECT doc_id, docnm_kwd FROM ragflow_t1 WHERE kb_id = 'kb-1' ORDER BY doc_id LIMIT 5"`，已有安全边界与小 LIMIT 不被改写。
- `validated.columns == ["doc_id", "docnm_kwd"]`
- `validated.is_aggregation == False`
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_101_validator_allows_single_table_select_and_returns_columns`。

**验收口径**：技术设计 3.5.6「validator 只读校验 + kb_id 补丁」

**优先级**：P0

### TC-SQL-102: 只读 WITH 通过

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 调用 `v.validate_and_patch("WITH x AS (SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1') SELECT doc_id FROM x")`

**预期结果**：
- 基线 SQL 精确返回 `WITH x AS (SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1') SELECT doc_id FROM x LIMIT 128`，`columns == ["doc_id"]`，`is_aggregation is False`。
- CTE 内将已暴露 JSONB path 声明为 `amount` 后，外层 `SELECT amount FROM rows` 与 `SELECT rows.amount FROM rows` 均通过；两者完整 SQL 保留 CTE 内 `kb_id = 'kb-1'` 并补 `LIMIT 128`。
- 两个 alias 场景均返回 `columns == ["amount"]`、`is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_102_validator_allows_readonly_cte_and_exposed_output_aliases`。

**验收口径**：技术设计 3.5.6「只读 WITH CTE 支持」

**优先级**：P0

### TC-SQL-103: 多语句拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1; INSERT INTO ragflow_t1 VALUES (1)")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "multiple statements are not allowed" in str(exc)`

**验收口径**：技术设计 3.5.6「多语句拒绝」

**优先级**：P0

### TC-SQL-104: DML 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("INSERT INTO ragflow_t1 VALUES (1)")`
   - `v.validate_and_patch("UPDATE ragflow_t1 SET doc_id = 'new'")`
   - `v.validate_and_patch("DELETE FROM ragflow_t1")`

**预期结果**：
- INSERT、UPDATE、DELETE 三次均抛 `UnsafeGaussDBSQL("only SELECT statements are allowed")`。
- `WITH x AS (DELETE ... RETURNING doc_id) SELECT ...` 抛 `UnsafeGaussDBSQL("SQL contains a non-read-only expression")`，证明 DML 不能藏在只读根节点中。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_104_validator_rejects_dml_including_nested_cte`。

**验收口径**：技术设计 3.5.6「DML 拒绝」

**优先级**：P0

### TC-SQL-105: DDL 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("CREATE TABLE test (id int)")`
   - `v.validate_and_patch("DROP TABLE ragflow_t1")`
   - `v.validate_and_patch("ALTER TABLE ragflow_t1 ADD COLUMN x int")`

**预期结果**：
- CREATE、DROP、ALTER 三次均精确抛 `UnsafeGaussDBSQL("only SELECT statements are allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_105_validator_rejects_ddl`。

**验收口径**：技术设计 3.5.6「DDL 拒绝」

**优先级**：P0

### TC-SQL-106: CALL 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 调用 `v.validate_and_patch("CALL my_proc()")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "only SELECT statements are allowed" in str(exc)` or `assert "SQL contains a non-read-only expression" in str(exc)`
- CALL is rejected as non-SELECT

**验收口径**：技术设计 3.5.6「CALL 拒绝」

**优先级**：P1

### TC-SQL-107: COPY 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 调用 `v.validate_and_patch("COPY ragflow_t1 TO '/tmp/out.csv'")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "only SELECT statements are allowed" in str(exc)` or `assert "SQL contains a non-read-only expression" in str(exc)`
- COPY is rejected as non-SELECT

**验收口径**：技术设计 3.5.6「COPY 拒绝」

**优先级**：P1

### TC-SQL-108: SELECT * 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT * FROM ragflow_t1")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "SELECT * is not allowed" in str(exc)`

**验收口径**：技术设计 3.5.6「SELECT * 拒绝」

**优先级**：P0

### TC-SQL-109: COUNT(*) 通过

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT COUNT(*) AS cnt FROM ragflow_t1")`

**预期结果**：
- 通过验证，返回 `ValidatedGaussDBSQL` 对象
- `validated.sql` 含 `COUNT(*) AS cnt`
- `validated.sql` 含 `kb_id = 'kb-1'`
- `validated.sql` 含 `LIMIT 128`
- `validated.is_aggregation == True`

**验收口径**：技术设计 3.5.6「COUNT(*) 聚合通过」

**优先级**：P0

### TC-SQL-110: 窗口函数拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT doc_id, RANK() OVER (ORDER BY chunk_data #>> '{amount}') AS rnk FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT doc_id, ROW_NUMBER() OVER (PARTITION BY kb_id ORDER BY doc_id) AS rn FROM ragflow_t1")`

**预期结果**：
- RANK 与 ROW_NUMBER 两次均精确抛 `UnsafeGaussDBSQL("window functions are not allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_110_validator_rejects_rank_and_row_number_windows`。

**验收口径**：技术设计 3.5.6「窗口函数拒绝」

**优先级**：P1

### TC-SQL-111: 系统表 / 跨 schema 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT doc_id FROM pg_catalog.tables")`
   - `v.validate_and_patch("SELECT doc_id FROM other_schema.ragflow_t1")`

**预期结果**：
- `pg_catalog.tables` 与 `other_schema.ragflow_t1` 两次均精确抛 `UnsafeGaussDBSQL("cross-schema SQL is not allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_111_validator_rejects_system_table_and_cross_schema_table`。

**验收口径**：技术设计 3.5.6「跨 schema 拒绝」

**优先级**：P0

### TC-SQL-112: 非白名单表拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM unknown_table")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- `assert "table unknown_table is not allowed" in str(exc)`

**验收口径**：技术设计 3.5.6「非白名单表拒绝」

**优先级**：P0

### TC-SQL-113: 非白名单列拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT content_with_weight FROM ragflow_t1")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- `assert "column content_with_weight is not allowed" in str(exc)`

**验收口径**：技术设计 3.5.6「非白名单列拒绝」

**优先级**：P0

### TC-SQL-114: 禁用函数拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT pg_sleep(1) FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT now() FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT current_user FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT version() FROM ragflow_t1")`

**预期结果**：
- `pg_sleep` 精确抛 `function pg_sleep is not allowed`；`version()` 精确抛 `function version is not allowed`。
- `now()` 与 `current_user` 由 sqlglot 系统表达式节点识别，均精确抛 `system functions are not allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_114_validator_rejects_forbidden_system_functions`。

**验收口径**：技术设计 3.5.6「禁用函数拒绝」

**优先级**：P0

### TC-SQL-115: JSON 函数拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT json_extract_string(chunk_data, '$.amount') FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT json_extract(chunk_data, '$.amount') FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT json_extract_isnull(chunk_data, '$.amount') FROM ragflow_t1")`

**预期结果**：
- 三次均抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- `json_extract_string` 固定断言：`match="function json_extract_string is not allowed"`
- `json_extract` 固定断言：`match="only GaussDB #> / #>> JSONB operators are allowed"`
- `json_extract_isnull` 固定断言：`match="function json_extract_isnull is not allowed"`
- GaussDB prompt 要求使用 `#>>` / `#>` 而非 `json_extract_string` / `json_extract` / `json_extract_isnull`

**验收口径**：技术设计 3.5.6「非目标 JSON helper 拒绝」

**优先级**：P0

### TC-SQL-116: 禁用 JSONB 函数拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE jsonb_each(chunk_data)")`
   - `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE jsonb_array_elements(chunk_data, '$.arr')")`

**预期结果**：
- 两次均抛 `UnsafeGaussDBSQL`
- `assert "function jsonb_each is not allowed" in str(exc)` or `assert "function jsonb_array_elements is not allowed" in str(exc)`
- `jsonb_each` / `jsonb_array_elements` 被 validator 的禁用函数集合拒绝

**验收口径**：技术设计 3.5.6「禁用 JSONB 函数拒绝」

**优先级**：P1

### TC-SQL-117: JOIN 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT a.doc_id FROM ragflow_t1 a JOIN ragflow_t1 b ON a.doc_id = b.doc_id WHERE a.kb_id = 'kb-1' AND b.kb_id = 'kb-1'")`。

**预期结果**：
- 即使两个 alias 都写出合法 KB 条件，仍精确抛 `UnsafeGaussDBSQL("complex SQL must use a simpler single-table kb_id boundary")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_117_validator_rejects_join_even_when_both_aliases_are_scoped`。

**验收口径**：技术设计 3.5.6「JOIN 拒绝」

**优先级**：P0

### TC-SQL-118: UNION 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' UNION SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-2'")`

**预期结果**：
- sqlglot 将 UNION 解析为非 `Select` 根节点，精确抛 `UnsafeGaussDBSQL("only SELECT statements are allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_118_validator_rejects_union_as_non_select_root`。

**验收口径**：技术设计 3.5.6「UNION 拒绝」

**优先级**：P0

### TC-SQL-119: OR 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' OR chunk_data #>> '{amount}' = '100'")`

**预期结果**：
- 精确抛 `UnsafeGaussDBSQL("complex SQL must use a simpler single-table kb_id boundary")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_119_validator_rejects_or_predicate`。

**验收口径**：技术设计 3.5.6「OR 拒绝」

**优先级**：P0

### TC-SQL-120: 动态 JSONB path 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> ('{' || param || '}') = '100'")`
2. 再验证 `chunk_data #>> path_col` 与错误 source `other_data #>> '{amount}'`。

**预期结果**：
- 拼接 path 与列 path 均精确抛 `UnsafeGaussDBSQL("dynamic JSONB path is not allowed")`。
- 非 `chunk_data` source 精确抛 `UnsafeGaussDBSQL("only chunk_data JSONB paths are allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_120_validator_rejects_dynamic_or_wrong_source_jsonb_paths`。

**验收口径**：技术设计 3.5.6「动态 JSONB path 拒绝」

**优先级**：P0

### TC-SQL-121: 空 SQL 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("")`
   - `v.validate_and_patch("   ")`
   - `v.validate_and_patch("```sql```")`

**预期结果**：
- 三次均精确抛 `UnsafeGaussDBSQL("empty SQL")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_121_validator_rejects_empty_sql_variants`。

**验收口径**：技术设计 3.5.6「空 SQL 拒绝」

**优先级**：P1

### TC-SQL-122: EXPLAIN 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("EXPLAIN SELECT doc_id FROM ragflow_t1")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 精确错误消息为 `only SELECT statements are allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_122_validator_rejects_explain`。
- EXPLAIN 不是只读 SELECT，拒绝

**验收口径**：技术设计 3.5.6「EXPLAIN 拒绝」

**优先级**：P1

### TC-SQL-123: SET 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SET statement_timeout = 30000")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 精确错误消息为 `only SELECT statements are allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_123_validator_rejects_set_command`。
- SET 不是只读 SELECT，拒绝

**验收口径**：技术设计 3.5.6「SET 拒绝」

**优先级**：P1

### TC-SQL-124: SHOW 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SHOW statement_timeout")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 精确错误消息为 `only SELECT statements are allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_124_validator_rejects_show_command`。
- SHOW 不是只读 SELECT，拒绝

**验收口径**：技术设计 3.5.6「SHOW 拒绝」

**优先级**：P1

### TC-SQL-125: LATERAL 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用普通 `CROSS JOIN LATERAL (SELECT ...)`、逗号 LATERAL 子查询和 `LATERAL jsonb_each(...)` 三种形态

**预期结果**：
- 三种形态均抛 `UnsafeGaussDBSQL`，稳定原因表示 `LATERAL` 不允许
- 普通 LATERAL 子查询即使不含 `jsonb_each` 也必须被拒绝，不能只靠禁止表函数偶然覆盖
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_125_validator_rejects_every_lateral_shape`。

**验收口径**：技术设计 3.5.6「LATERAL 拒绝」

**优先级**：P1

### TC-SQL-126: jsonb_path_query 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE jsonb_path_query(chunk_data, '$.amount') IS NOT NULL")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 精确错误消息为 `function jsonb_path_query is not allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_126_validator_rejects_jsonb_path_query`。
- GaussDB A 模式不支持 jsonb_path_query，拒绝

**验收口径**：技术设计 3.5.6「jsonb_path_query 拒绝」

**优先级**：P1

### TC-SQL-127: jsonb_to_record 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1, jsonb_to_record(chunk_data) AS t(amount text)")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 精确错误消息为 `function jsonb_to_record is not allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_127_validator_rejects_jsonb_to_record`。
- GaussDB A 模式不支持 jsonb_to_record，拒绝

**验收口径**：技术设计 3.5.6「jsonb_to_record 拒绝」

**优先级**：P1

### TC-SQL-128: generate_series 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1, generate_series(1, 10) AS g")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 稳定错误原因标识 `generate_series` 不允许，不暴露 sqlglot 内部 AST 类名。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_128_validator_rejects_generate_series`。
- generate_series 不来自受控 DocEngine 表，拒绝

**验收口径**：技术设计 3.5.6「generate_series 拒绝」

**优先级**：P1

### TC-SQL-129: unnest 拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1, unnest(ARRAY[1,2,3]) AS u")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert isinstance(exc, UnsafeGaussDBSQL)`
- 精确错误消息为 `function unnest is not allowed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_129_validator_rejects_unnest`。
- unnest 不来自受控 DocEngine 表，拒绝

**验收口径**：技术设计 3.5.6「unnest 拒绝」

**优先级**：P1

### TC-SQL-130: 其他写入、管理与权限语句拒绝

**类型**：安全 · 单元 · P0

**前置条件**：构造与 TC-SQL-104 相同的 `GaussDBSQLValidator`；无需数据库。

**步骤**：分别校验 `MERGE`、`TRUNCATE`、`ANALYZE`、`VACUUM`、`GRANT`、`REVOKE`、`LOCK TABLE` 和 `DO` 语句。

**预期结果**：
- 八种输入全部抛 `UnsafeGaussDBSQL`，不得返回 `ValidatedGaussDBSQL`。
- 稳定语义是“只允许单条只读 SELECT”；解析器可以在根节点、命令节点或多语句边界拒绝，但测试不得依赖 sqlglot 私有 AST 类名。
- 本用例补齐 TC-SQL-104～107 未显式列出的写入、维护、权限和匿名过程语句，不与已有 INSERT/UPDATE/DELETE/DDL/CALL/COPY 用例重复计数。

**自动化**：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_130_validator_rejects_additional_write_and_admin_statements`

**验收口径**：技术设计 3.5.6「Text-to-SQL 只能执行单条只读 SELECT」

**优先级**：P0

---

## 三、kb_id 边界补丁（2xx）

> 本组为纯函数 validator 补丁，单元层验证。

### TC-SQL-201: 单表单 Select 自动注入 kb_id

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1")`

**预期结果**：
- `validated.sql == "SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 128"`。
- `validated.columns == ["doc_id"]` 且 `validated.is_aggregation is False`。
- 不进数据库执行，validator 在内存完成补丁
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_201_validator_injects_kb_id_for_simple_single_table_query`。

**验收口径**：技术设计 3.5.6「kb_id 边界补丁」

**优先级**：P0

### TC-SQL-202: 多 KB IN 列表注入

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1", "kb-2"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1")`

**预期结果**：
- `validated.sql` == `"SELECT doc_id FROM ragflow_t1 WHERE kb_id IN ('kb-1', 'kb-2') LIMIT 128"`
- 多 KB 补丁用 `IN` 列表而非单值

**验收口径**：技术设计 3.5.6「多 KB IN 列表注入」

**优先级**：P0

### TC-SQL-203: 已有合法边界不改语义

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1'")`

**预期结果**：
- `validated.sql` == `"SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 128"`
- 不改原 WHERE 语义，仅补 LIMIT
- `kb_id = 'kb-1'` 在 allowed_kb_ids 内，通过验证

**验收口径**：技术设计 3.5.6「已有合法 kb_id 边界不改语义」

**优先级**：P0

### TC-SQL-204: 跨 KB 边界拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-other'")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "SQL crosses the allowed kb_id boundary" in str(exc)`

**验收口径**：技术设计 3.5.6「跨 KB 边界拒绝」

**优先级**：P0

### TC-SQL-205: WHERE 含 kb_id 但非顶层谓词拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别验证 `NOT kb_id = 'kb-1'`、`(kb_id = 'kb-1') IS FALSE`、`kb_id IN (doc_id)` 与空 `kb_id IN ()`。

**预期结果**：
- NOT 与 `IS FALSE` 均精确抛 `kb_id boundary must be a positive top-level predicate`。
- 动态 IN 精确抛 `kb_id IN must use static string literals`；空 IN 精确抛 `SQL crosses the allowed kb_id boundary`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_205_validator_rejects_non_positive_or_dynamic_kb_predicates`。

**验收口径**：技术设计 3.5.6「kb_id 非顶层谓词拒绝」

**优先级**：P1

### TC-SQL-206: 复杂 SQL 拒绝补丁

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT a.doc_id FROM ragflow_t1 a JOIN ragflow_t1 b ON a.doc_id = b.doc_id")`，不提供可注入的单表边界。

**预期结果**：
- 精确抛 `UnsafeGaussDBSQL("complex SQL must use a simpler single-table kb_id boundary")`，不尝试向 JOIN scope 注入边界。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_206_validator_rejects_complex_sql_when_scope_cannot_be_patched`。

**验收口径**：技术设计 3.5.6「复杂 SQL 拒绝」

**优先级**：P0

### TC-SQL-207: 多表拒绝补丁

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT t1.doc_id FROM ragflow_t1 t1 JOIN ragflow_t2 t2 ON t1.doc_id = t2.doc_id")`

**预期结果**：
- 精确抛 `UnsafeGaussDBSQL("table ragflow_t2 is not allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_207_validator_rejects_second_non_whitelisted_table`。

**验收口径**：技术设计 3.5.6「多表拒绝」

**优先级**：P0

### TC-SQL-208: kb_id 缺失拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL
# validator 构造时未提供 kb_ids，tables 为 set 会自动创建 ExposedGaussDBTable
# 但 ExposedGaussDBTable.from_field_map(table_name, kb_ids=(), field_map={}) 会导致 required_kb_ids 为空
v = GaussDBSQLValidator(tables={"ragflow_t1"})  # 未配置 kb_ids，tables 为 set
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "kb_id boundary is required" in str(exc)`
- validator 未配置 kb_ids（`ExposedGaussDBTable.required_kb_ids` 为空），拒绝无边界 SQL

**验收口径**：技术设计 3.5.6「kb_id 缺失拒绝」

**优先级**：P0

### TC-SQL-209: 受限只读 WITH / 子查询须证明边界

**类型**：边界 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("WITH x AS (SELECT doc_id FROM ragflow_t1) SELECT doc_id FROM x")`（子查询缺 kb_id）
   - `v.validate_and_patch("WITH x AS (SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1') SELECT doc_id FROM x")`（子查询有 kb_id）
   - 外层/内层 `EXISTS (SELECT ... FROM ragflow_t1 ...)` 组合，分别覆盖外层缺边界、内层缺边界、内层越权和两层合法

**预期结果**：
- 缺边界 CTE 与仅投影 `'kb-1' AS kb_id` 的伪边界 CTE 均精确抛 `UnsafeGaussDBSQL("each base table scope must include a kb_id boundary")`。
- 合法 CTE 精确返回 `WITH x AS (SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1') SELECT doc_id FROM x LIMIT 128`，`columns == ["doc_id"]`，`is_aggregation is False`。
- 相关与非相关 `EXISTS` 子查询的每一个 base-table scope 都必须独立证明 KB 边界；越权 `kb-other` 必须拒绝
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_209_validator_requires_each_cte_base_scope_to_prove_kb_boundary`、`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_209_validator_requires_each_subquery_scope_to_prove_kb_boundary`。

**验收口径**：技术设计 3.5.6「WITH/子查询证明 kb_id 边界」

**优先级**：P1

---

## 四、limit 补丁（3xx）

> 本组为纯函数 validator 补丁，单元层验证。

### TC-SQL-301: 缺失 LIMIT 自动补

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1'")`

**预期结果**：
- `validated.sql` == `"SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 128"`
- 缺失 LIMIT 时自动补 `LIMIT 128`（default_limit）
- 显式 `default_limit=-1` 时不自动补 LIMIT；这只控制补丁，不允许绕过已有 LIMIT 的合法性校验

**验收口径**：技术设计 3.5.6「LIMIT 自动补」

**优先级**：P0

### TC-SQL-302: LIMIT 过大强制改

**类型**：边界 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 200")`

**预期结果**：
- `validated.sql` == `"SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 128"`
- LIMIT 200 强制改为 `LIMIT 128`
- `assert "LIMIT 200" not in validated.sql`

**验收口径**：技术设计 3.5.6「LIMIT 过大强制改」

**优先级**：P0

### TC-SQL-303: LIMIT 100 不补

**类型**：边界 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 100")`

**预期结果**：
- `validated.sql` == `"SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 100"`
- LIMIT 100 <= 128，不改动
- `assert "LIMIT 128" not in validated.sql`

**验收口径**：技术设计 3.5.6「LIMIT <= default_limit 不改」

**优先级**：P1

### TC-SQL-304: 非正数、动态及非整数 LIMIT 拒绝

**类型**：边界 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 分别输入 `LIMIT 0`、`LIMIT -1`、`LIMIT 1.5`、`LIMIT $1`、`LIMIT doc_id`、`LIMIT '1'`
2. 输入 `FETCH FIRST 0 ROWS ONLY` 和嵌套子查询中的 `LIMIT 0`

**预期结果**：
- 所有输入均抛 `UnsafeGaussDBSQL`，稳定原因表示 LIMIT 必须是正的静态整数
- 校验覆盖顶层、`FETCH FIRST` 和嵌套查询，不把 0/负数/动态表达式静默改写为默认值

**验收口径**：技术设计 3.5.6「LIMIT 必须为正静态整数」

**优先级**：P1

### TC-SQL-305: 复杂 scope 缺 LIMIT 前置拒绝

**类型**：异常 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT a.doc_id FROM ragflow_t1 a JOIN ragflow_t1 b ON a.doc_id = b.doc_id LIMIT 500")`（JOIN）
   - `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' OR chunk_data #>> '{status}' = 'active' LIMIT 500")`（OR）

**预期结果**：
- 两次均精确抛 `UnsafeGaussDBSQL("complex SQL must use a simpler single-table kb_id boundary")`，而不是先把 500 裁剪为 128。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_305_validator_rejects_complex_scope_before_limit_patch`。

**验收口径**：技术设计 3.5.6「复杂 SQL 前置拒绝」

**优先级**：P1

### TC-SQL-306: fetch_size 透传 default_limit

**类型**：正向 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator
from unittest.mock import patch
# Mock GaussDBConnection.sql call with fetch_size=64
```

**步骤**：
1. 调用 `GaussDBSQLValidator.readonly_guard(default_limit=64).validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1'")`。
2. 通过 `GaussDBConnection.sql(source_sql, fetch_size=64)` 的公开入口 spy `readonly_guard`，确认调用点传入相同上限并执行补丁后的 SQL。

**预期结果**：
- `readonly_guard` 返回启用 runtime readonly guard 且使用指定 default limit 的 validator
- validator constructed with `default_limit=64`
- `validated.sql == "SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 64"`
- 公开 `sql()` 入口精确调用 `readonly_guard(default_limit=64)`，再以原始 scoped SQL 调 `validate_and_patch`
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_306_readonly_guard_uses_fetch_size_as_default_limit`；`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_306_sql_passes_fetch_size_to_readonly_guard`

**验收口径**：技术设计 3.5.6「fetch_size 透传 default_limit」

**优先级**：P1

### TC-SQL-307: FETCH FIRST 裁剪

**类型**：边界 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' FETCH FIRST 200 ROWS ONLY")`

**预期结果**：
- `validated.sql` 含 `FETCH FIRST 128 ROWS ONLY`（裁剪为 default_limit）
- FETCH FIRST 超过 default_limit 时截断

**验收口径**：技术设计 3.5.6「FETCH FIRST 裁剪」

**优先级**：P1

### TC-SQL-308: literal / 子查询含 SQL 关键字不影响 AST

**类型**：边界 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"limit": "string"})
v = GaussDBSQLValidator({"ragflow_t1": table}, default_limit=128)
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{limit}' = '100'")`

**预期结果**：
- field 名为 `limit`、子查询自带 `LIMIT 1`、literal 值为 `update` 或 `order by dept` 的四个场景均通过。
- 四个完整 SQL 均保留原 literal/子查询语义，顶层补 `LIMIT 128`；需要注入边界的 ORDER BY 场景在 ORDER BY 前精确加入 `AND kb_id = 'kb-1'`。
- 四个结果均为 `columns == ["doc_id"]`、`is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_308_clause_tokens_in_literals_or_subqueries_do_not_block_ast_patches`。

**验收口径**：技术设计 3.5.6「literal 含 limit 不影响 AST」

**优先级**：P1

---

## 五、JSONB 语法校验（4xx）

> 本组为纯函数 validator 校验，单元层验证；411 集成层验证命中。

### TC-SQL-401: #>> 单键合法

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT chunk_data #>> '{amount}' AS amount FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 10")`

**预期结果**：
- 通过验证，返回 `ValidatedGaussDBSQL` 对象
- `validated.sql == "SELECT chunk_data #>> '{amount}' AS amount FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 10"`。
- `validated.columns == ["amount"]` 且 `validated.is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_401_validator_allows_single_key_jsonb_text_path`。

**验收口径**：技术设计 3.5.6「JSONB #>> 单键合法」

**优先级**：P0

### TC-SQL-402: #>> 多键合法

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"profile.name": "string"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{profile,name}' = 'Alice'")`

**预期结果**：
- 完整 SQL 精确为 `SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{profile,name}' = 'Alice' AND kb_id = 'kb-1' LIMIT 128`。
- `columns == ["doc_id"]`，`is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_402_validator_allows_multi_key_jsonb_text_path`。

**验收口径**：技术设计 3.5.6「JSONB #>> 多键合法」

**优先级**：P0

### TC-SQL-403: 含逗号键需引号

**类型**：边界 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"a,b": "string"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{\"a,b\"}' = 'value'")`

**预期结果**：
- 引号场景完整 SQL 精确为 `SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{"a,b"}' = 'value' AND kb_id = 'kb-1' LIMIT 128`，`columns == ["doc_id"]`，`is_aggregation is False`。
- 未加引号的 `'{a,b}'` 被解析为两段 path，精确抛 `JSONB path ('a', 'b') is not exposed`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_403_validator_requires_quotes_for_jsonb_key_containing_comma`。

**验收口径**：技术设计 3.5.6「含逗号键需引号」

**优先级**：P1

### TC-SQL-404: #> JSON 对象提取合法

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"profile": "json"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #> '{profile}' @> '{\"name\": \"Alice\"}'::jsonb")`

**预期结果**：
- 完整 SQL 精确为 `SELECT doc_id FROM ragflow_t1 WHERE chunk_data #> '{profile}' @> CAST('{"name": "Alice"}' AS JSONB) AND kb_id = 'kb-1' LIMIT 128`。
- `columns == ["doc_id"]`，`is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_404_validator_allows_jsonb_object_extraction_operator`。

**验收口径**：技术设计 3.5.6「JSONB #> 对象提取合法」

**优先级**：P0

### TC-SQL-405: 数值 CAST 合法

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE CAST(chunk_data #>> '{amount}' AS DOUBLE PRECISION) > 100")`

**预期结果**：
- 通过验证
- `validated.sql` 含 `CAST(chunk_data #>> '{amount}' AS DOUBLE PRECISION) > 100`
- CAST 用于数值比较

**验收口径**：技术设计 3.5.6「JSONB 数值 CAST 合法」

**优先级**：P0

### TC-SQL-406: 日期 to_date 合法

**类型**：正向 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"dt": "date"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE to_date(chunk_data #>> '{dt}', 'YYYY-MM-DD') > '2026-01-01'")`

**预期结果**：
- 完整规范化 SQL 精确为 `SELECT doc_id FROM ragflow_t1 WHERE TO_DATE(chunk_data #>> '{dt}', 'YYYY-MM-DD') > '2026-01-01' AND kb_id = 'kb-1' LIMIT 128`。
- `columns == ["doc_id"]`，`is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_406_validator_allows_to_date_on_exposed_jsonb_field`。

**验收口径**：技术设计 3.5.6「日期 to_date 合法」

**优先级**：P1

### TC-SQL-407: IS NULL / IS NOT NULL 合法

**类型**：正向 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"status": "string"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE (chunk_data #>> '{status}') IS NULL")`

**预期结果**：
- 通过验证
- `validated.sql` 含 `(chunk_data #>> '{status}') IS NULL`
- 用于 key missing / JSON null / JSON 空串判断

**验收口径**：技术设计 3.5.6「JSONB IS NULL 合法」

**优先级**：P0

### TC-SQL-408: 未声明 path 拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})  # 仅声明 amount
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT doc_id FROM ragflow_t1 WHERE chunk_data #>> '{non_exposed_field}' = '100'")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "JSONB path ('non_exposed_field',) is not exposed" in str(exc)`
- path 不在 field_map allowed_paths

**验收口径**：技术设计 3.5.6「未声明 JSONB path 拒绝」

**优先级**：P0

### TC-SQL-409: chunk_data 裸访问拒绝

**类型**：异常 · 单元 · P0

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, UnsafeGaussDBSQL, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 调用 `v.validate_and_patch("SELECT chunk_data FROM ragflow_t1")`

**预期结果**：
- 抛 `UnsafeGaussDBSQL`
- `assert "chunk_data may only be accessed through #> / #>>" in str(exc)`
- 未通过 #>>/#>，直接 SELECT chunk_data

**验收口径**：技术设计 3.5.6「chunk_data 裸访问拒绝」

**优先级**：P0

### TC-SQL-410: 空值 prompt 禁止规则与 validator 行为

**类型**：边界 · 单元 · P0

**前置条件**：
```python
from rag.utils.gaussdb_text_to_sql import build_sql_prompt
from common.doc_store.gaussdb_conn_base import ExposedGaussDBTable, GaussDBSQLValidator
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"status": "string"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. Prompt 禁止规则测试：调用 `build_sql_prompt("ragflow_t1", {"status": "string"}, "show rows where status is empty")`。
2. Validator 行为测试：对 `#>>` 空串比较覆盖 `=` / `<>`、左右反转、cast 包裹、`IS [NOT] DISTINCT FROM` 等 AST 形态。
3. 正向调用 validator 校验 `chunk_data #> '{status}' = 'null'::jsonb`。

**预期结果**：
- Prompt 断言：`"NULL check: (chunk_data #>> '{FieldName}') IS NOT NULL"` 在 prompt 中。
- Prompt 断言：`"Do not use json_extract, json_extract_string, or json_extract_isnull; use #>> / #> instead"` 在 prompt 中。
- Prompt 明确说明 A/ORA 将 `''` 视为 NULL，禁止 `= ''` / `<> ''`，要求使用 `IS NULL` / `IS NOT NULL`。
- 所有 JSONB 文本与 SQL 空字符串比较形态均抛 `UnsafeGaussDBSQL`，稳定原因表示该比较在 A/ORA 下不安全。
- JSONB `null` literal 比较通过，规范化为 `CAST('null' AS JSONB)`，保留 `doc_id` 输出并补 `LIMIT 128`。

**优先级**：P0

### TC-SQL-411: key missing / JSON null / 空串行为

**类型**：边界 · 集成 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建本用例唯一表 `table = f"ragflow_{gaussdb_env['table_prefix']}"`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表插三行：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'missing-key', 'doc.xlsx', '{"other": 1}'::jsonb),
  ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'json-null', 'doc.xlsx', '{"status": null}'::jsonb),
  ('c3', 'abcdefabcdefabcdefabcdefabcdefab', 'empty-string', 'doc.xlsx', '{"status": ""}'::jsonb);
```
field_map={"status": "string"}

**步骤**：
1. 执行 `conn.sql(f"SELECT doc_id FROM {table} WHERE (chunk_data #>> '{{status}}') IS NULL AND kb_id = '{kb_id}' ORDER BY doc_id LIMIT 128")`。
2. 执行 `conn.sql(f"SELECT doc_id FROM {table} WHERE (chunk_data #>> '{{status}}') IS NOT NULL AND kb_id = '{kb_id}' ORDER BY doc_id LIMIT 128")`。

**预期结果**：
- `IS NULL` 命中 `["json-null", "missing-key"]`（key missing + JSON null）
- `IS NOT NULL` 命中 `["empty-string"]`（JSON 空串）
- `null_rows["columns"] == [{"name": "doc_id", "type": "text"}]`，`not_null_rows["columns"] == [{"name": "doc_id", "type": "text"}]`
- validator 通过 SQL，补丁后 SQL 含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'` 和 `LIMIT 128`。
- 异常断言：两次 `conn.sql` 均不抛异常。

**清理**：只清理 `<table>` 中本用例插入的三行，或由 helper 删除本用例唯一表；清理后 `SELECT COUNT(*) FROM <table> WHERE id IN ('c1','c2','c3')` 为 0。

**验收口径**：技术设计 3.5「JSONB 空值态 IS NULL 区分」

**优先级**：P0

### TC-SQL-412: JSONB path literal 编码与拒绝

**类型**：边界 · 单元 · P1

**步骤**：
1. 对普通、多段、逗号、双引号、反斜杠、空格、中文和 SQL 单引号 key 调用 `jsonb_path_literal`。
2. 对空 path、空 segment、未包 `{}`、尾随逗号和未闭合引号调用编码器/解析器。

**预期结果**：
- 普通与多段 path 精确编码为 `'{amount}'`、`'{customer,name}'`；特殊 key 使用双引号，反斜杠/双引号按 JSON path 规则转义，SQL 单引号加倍（`O'Brien` 不得闭合 literal）。
- 空 path 精确抛 `empty JSONB path`；空 segment 精确抛 `empty JSONB path segment`。
- 动态形状精确抛 `dynamic JSONB path is not allowed`；尾随逗号或未闭合引号精确抛 `invalid JSONB path literal`。
- 编码器 expected 使用测试文档固定 literal；parser 使用另一组硬编码 literal，禁止把编码器实际输出直接传给同一生产 parser 作为自证结果。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_412_jsonb_path_literal_encodes_special_keys`、`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_412_jsonb_path_literal_rejects_empty_path_or_segment`、`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_412_jsonb_path_literal_parser_handles_escapes_and_invalid_shapes`。

**优先级**：P1

---

## 六、sql() 执行与返回（5xx）

> 真实库执行用例标 `· 集成`；超时等反向场景 mock 标 `· 单元`。

### TC-SQL-501: validator 接入

**类型**：正向 · 集成 + 组件单元 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建本用例唯一表 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表并插入两行 `d1`、`d2`，用于验证运行时 limit：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'doc.xlsx', '{"amount": "100"}'::jsonb);
INSERT INTO <table> VALUES ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'd2', 'doc.xlsx', '{"amount": "200"}'::jsonb);
```

**步骤**：
1. 调用 `conn.sql(f"SELECT doc_id FROM {table} WHERE kb_id = '{kb_id}' ORDER BY doc_id", fetch_size=1)`。
2. 断言返回结果受运行时 `fetch_size` 限制，并且查询经过真实 validator/runtime guard；Distributed 环境不得再出现 DN `42P01`。

**预期结果**：
- SQL 先经 `GaussDBSQLValidator.readonly_guard(default_limit=fetch_size, execution_schema=conn.schema).validate_and_patch`
- 用户 SQL 中的 schema / catalog 限定仍被拒绝；全部安全校验完成后，runtime guard 只给非 CTE 的已验证物理表补配置 schema，最终 SQL 以 `"<configured schema>".<table>` 执行
- 真实查询只在配置 schema 中解析并成功返回，不依赖 Distributed USTORE/FQS 对连接启动 `search_path` 的下推；timeout 的精确执行顺序由 TC-SQL-504 独立验证
- validator/runtime guard 对只读 SQL 生效；本用例通过 `fetch_size=1` 验证实际返回行数限制
- validator 校验白名单表列、kb_id 边界
- 返回 `{"columns": [{"name": "doc_id", "type": "text"}], "rows": [["d1"]]}`，而不是未受限制的 `d1`、`d2` 两行
- 异常断言：不抛 `UnsafeGaussDBSQL`；如表名不是 `ragflow_` 前缀则固定抛 `UnsafeGaussDBSQL`，message 含 `"table <name> is not allowed"`。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「validator 接入 sql()」

**优先级**：P0

### TC-SQL-502: statement_timeout 生效

**类型**：边界 · 单元 · P0

**前置条件**：单元层用 mock pool/cursor；`SQL_QUERY_TIMEOUT_MS=30000`；`conn = GaussDBConnection.__new__(GaussDBConnection)` 后补齐 `logger`，并 monkeypatch `conn.pool.get_conn()` 返回 mock connection。

**步骤**：
1. 调用 `conn._fetch_all_with_description("SELECT doc_id FROM ragflow_0123456789abcdef0123456789abcdef WHERE kb_id = 'abcdefabcdefabcdefabcdefabcdefab'", [], statement_timeout_ms=SQL_QUERY_TIMEOUT_MS)`。
2. 检查 mock cursor 的 `execute` 调用顺序。

**预期结果**：
- `cursor.execute.mock_calls[0].args == ("SET LOCAL statement_timeout = 30000",)`。
- `cursor.execute.mock_calls[1].args == (业务 SQL, [])`。
- `pool.put_conn.assert_called_once_with(mock_conn)`，cursor 被关闭。
- 不使用 `caplog` 断 timeout 设置；源码没有为 `SET LOCAL statement_timeout` 打日志。
- 异常断言：本用例不抛异常；若 cursor 第二次 `execute` 抛 `TimeoutError("query timeout")`，异常向外传播。

**清理**：mock 单元测试无需 DB 清理。

**验收口径**：技术设计 3.5「statement_timeout 生效」

**优先级**：P0

### TC-SQL-503: 超时中断

**类型**：异常传播 · 单元 · P0

**前置条件**：
```python
from rag.utils.gaussdb_conn import GaussDBConnection
from unittest.mock import Mock, patch, MagicMock
# Mock _fetch_all_with_description to raise TimeoutError
conn = GaussDBConnection.__new__(GaussDBConnection)
conn._fetch_all_with_description = Mock(side_effect=TimeoutError("query timeout"))
```

**步骤**：
1. 调用 `conn.sql("SELECT doc_id FROM ragflow_0123456789abcdef0123456789abcdef WHERE kb_id = 'abcdefabcdefabcdefabcdefabcdefab'")` 触发超时。

**预期结果**：
- 固定断言：`with pytest.raises(TimeoutError, match="query timeout")`。
- `_fetch_all_with_description` 只调用一次，调用参数含独立固定契约 `statement_timeout_ms=30000`，不得从生产 `SQL_QUERY_TIMEOUT_MS` 导入 expected。
- 完整调用精确为物理表已补 `"<configured schema>".` 的业务 SQL加 `LIMIT 128`、params `[]`、`statement_timeout_ms=30000`，不能只检查 timeout。
- `GaussDBConnection.sql` 不 catch 超时，异常原样向外抛出。
- 本单元用例只证明超时参数透传和异常传播；数据库端真实语句中断、事务清理与连接复用属于集成验证，不能由 mock 用例宣称完成。

**验收口径**：技术设计 4「Text-to-SQL 执行超时」

**优先级**：P0

### TC-SQL-504: 事务级 timeout 自动 reset

**类型**：边界 · 集成 · P1

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>` 并插入固定 d000；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；测试进程只将生产超时常量临时设为 `100ms`，另一个真实数据库连接持有该表的 `ACCESS EXCLUSIVE` 锁，不得替换 connection、cursor、SQL 返回或数据库异常。

**步骤**：
1. 记录真实连接池当前 `statement_timeout`。
2. 独立连接对唯一测试表执行 `LOCK TABLE ... IN ACCESS EXCLUSIVE MODE` 并保持事务。
3. 通过生产 `conn.sql()` 执行 validator 可接受的单表 `COUNT(*)`，使真实数据库等待表锁并超过 `100ms`。
4. 捕获真实数据库 timeout 异常及 SQLSTATE，并在 `finally` 回滚锁持有连接。
5. 重新从 pool 获取连接，检查 `statement_timeout`，再执行 d000 canary 查询。

**预期结果**：
- 慢查询由真实 GaussDB 取消，SQLSTATE 精确为 `57014`；不得伪造异常或用调用次数替代。
- 超时必须发生在 validator 放行后的真实数据库锁等待阶段，不得使用会先被安全校验拒绝的复杂 SQL。
- `conn.sql()` 的失败事务完成回滚，连接不得停留在 aborted transaction。
- 连接归还后 `statement_timeout` 精确恢复为步骤 1 的原值，后续查询不继承 `100ms`。
- canary 查询精确返回首行 `d000`，证明同一生产连接池可继续复用。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「会话级 timeout 自动 reset」

**优先级**：P1

### TC-SQL-505: 返回 columns + rows

**类型**：正向 · 集成 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'doc.xlsx', '{"amount": "100"}'::jsonb),
  ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'd2', 'doc.xlsx', '{"amount": "200"}'::jsonb);
```

**步骤**：
1. 调用 `conn.sql(f"SELECT doc_id, docnm_kwd FROM {table} WHERE kb_id = '{kb_id}' ORDER BY doc_id LIMIT 128")`

**预期结果**：
- 返回 `{"columns": [{"name": "doc_id", "type": "text"}, {"name": "docnm_kwd", "type": "text"}], "rows": [["d1", "doc.xlsx"], ["d2", "doc.xlsx"]]}`
- `columns` 为列表，每项含 `name` 和 `type`
- `rows` 为二维列表，每项为行值数组
- 行数 == 2，列数 == 2
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「columns+rows 返回结构」

**优先级**：P0

### TC-SQL-506: rows 值类型转换

**类型**：正向 · 组件单元 · P1

**前置条件**：不连接 DB；用 `conn = object.__new__(GaussDBConnection)` 绕过会创建连接池的构造器，并设置 `conn.logger = logging.getLogger("test.gaussdb")`；patch `_fetch_all_with_description` 固定返回 `rows=[(b"abc", {"a":1}, [1,"x"], None)]`、`description=[("bytes_col",),("object_col",),("list_col",),("null_col",)]`；validator 真实执行。

**步骤**：
1. 调用 `conn.sql("SELECT doc_id AS bytes_col, chunk_data #> '{object_col}' AS object_col, chunk_data #> '{list_col}' AS list_col, chunk_data #>> '{null_col}' AS null_col FROM ragflow_tc_sql_506 WHERE kb_id = 'kb-506'", format="json")`。输入必须读取合法 `ragflow_*` DocEngine 表并带静态 `kb_id`，不得用会被 runtime guard 拒绝的 `SELECT 1`。

**预期结果**：
- `columns == [{"name":"bytes_col","type":"text"},{"name":"object_col","type":"text"},{"name":"list_col","type":"text"},{"name":"null_col","type":"text"}]`
- `rows == [["abc", "{\"a\": 1}", "[1, \"x\"]", None]]`
- `_fetch_all_with_description` 精确调用一次，statement timeout 为 `SQL_QUERY_TIMEOUT_MS`；bytes 按 UTF-8 解码，dict/list 按 `json.dumps(..., ensure_ascii=False)` 转换，None 原样保留
- 实际执行 SQL 精确为上述 SQL 的物理表补 `"<configured schema>".` 后再加 `LIMIT 128`，params 为 `[]`。
- 异常断言：不抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_506_sql_converts_row_values_after_runtime_validation`。

**清理**：pytest `monkeypatch` 恢复方法；无 DB/文件资源。

**验收口径**：技术设计 3.5「rows 值类型转换」

**优先级**：P1

### TC-SQL-507: 空 rows

**类型**：边界 · 集成 · P1

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据（查询条件使用同一合法 UUID 但不匹配 doc_id）：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'doc.xlsx', '{"amount": "100"}'::jsonb);
```

**步骤**：
1. 调用 `conn.sql(f"SELECT doc_id FROM {table} WHERE kb_id = '{kb_id}' AND doc_id = 'missing-doc'")`。

**预期结果**：
- 返回 `{"columns": [{"name": "doc_id", "type": "text"}], "rows": []}`
- `rows=[]` 空列表，`columns` 仍存在
- 不会返回 `None`
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「空 rows 返回结构」

**优先级**：P1

### TC-SQL-508: format=markdown

**类型**：正向 · 集成 + 组件单元 · P1

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'sales.xlsx', '{"amount": "100"}'::jsonb),
  ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'd2', 'sales.xlsx', '{"amount": "200"}'::jsonb);
```

**步骤**：
1. 调用 `conn.sql(f"SELECT doc_id, chunk_data #>> '{{amount}}' AS amount FROM {table} WHERE kb_id = '{kb_id}' ORDER BY doc_id LIMIT 128", format="markdown")`

**预期结果**：
- 返回 Markdown table 字符串：
  ```
  |doc_id|amount|
  |---|---|
  |d1|100|
  |d2|200|
  ```
- 列名为 `doc_id` / `amount`
- 行值用 `|` 分隔
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「format=markdown 返回」

**优先级**：P1

### TC-SQL-509: validator 放行后的真实数据库错误与 dialog retry

**类型**：异常 · 集成 · P1

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`，并写入 `chunk_data.status="active"` 与 `chunk_data.amount="100"`；`field_map={"amount":"amount","status":"string"}`，确保首轮 `status` path 会被 validator 放行；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；`DOC_ENGINE_GAUSSDB=True`；dialog retry 子测试使用确定性的 chat boundary fake，首次返回数据库必然失败的 `CAST(chunk_data #>> '{status}' AS INTEGER)` 查询，第二次返回按 `doc_id` 排序的合法 amount 查询；`Dealer` 和 `GaussDBConnection` 使用真实实现。

**步骤**：
1. adapter 异常子测试：调用生产 `conn.sql()` 执行 `CAST("active" AS INTEGER)` 的只读 SQL。
2. dialog retry 子测试：调用 `use_sql("show docs", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`。
3. 失败后从同一生产连接池执行 canary 查询。

**预期结果**：
- 首轮 SQL 通过 validator 后由真实 GaussDB 返回类型转换错误，SQLSTATE 为 `22P02` 或 `22018`，不是 parser/fake exception。
- 失败事务完成回滚；canary 查询精确成功，证明连接池可继续复用。
- dialog retry 子测试中，`use_sql` 捕获首轮真实数据库错误后生成 retry prompt。
- chat boundary fake 收到两次请求，第二次请求包含首轮真实数据库错误；该调用次数只用于证明 retry 分支，不代替数据库结果断言。
- 第二轮 SQL 经 validator 后执行成功，`answer == "|amount|Source|\n|------|------|\n|100| ##0$$|"`，reference chunk 精确为 d1/doc.xlsx/kb_id，doc_aggs 精确为 d1 一条。
- 透明 recorder 精确记录两次真实 `Dealer.sql_retrieval`：第一次到达数据库并失败，第二次含 source 列、amount JSONB path、kb scope、固定顺序和 limit，且返回真实行 d1/100。
- validator 可将输入的 `INTEGER` 规范化为等价的 GaussDB `INT`；recorder 断言 CAST 语义和 status path，不依赖这两个等价类型别名的文本拼写。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「真实数据库执行错误 → repair」

**优先级**：P1

### TC-SQL-510: validator 拒绝不执行

**类型**：安全 · 单元 · P0

**前置条件**：
```python
cursor = RecordingCursor()
conn = make_conn(cursor)
conn._fetch_all_with_description = fail_if_called
```

**步骤**：
1. 实际调用 `conn.sql("SELECT * FROM ragflow_tenant WHERE kb_id = 'kb1'")`，经过 `GaussDBConnection.sql` runtime readonly guard。
2. 断言 cursor、连接池获取/归还记录均保持零调用。

**预期结果**：
- runtime guard 精确抛 `UnsafeGaussDBSQL("SELECT * is not allowed")`，注入的 `_fetch_all_with_description` 不被调用。
- `cursor.executed == []`，连接获取次数为 0，`conn.pool.put_back == []`。
- 自动化（Covered）：`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_510_validator_rejection_prevents_database_fetch`。
- runtime adapter 拒绝矩阵补充：`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_510_sql_runtime_guard_rejects_unscoped_or_unsafe_sql`，覆盖系统表、跨 schema、缺 KB 边界、OR 绕过、非白名单列、非 GaussDB JSON 操作符与系统函数，逐项断言数据库 cursor 零执行。

**验收口径**：技术设计 3.5「adapter runtime guard 在数据库 fetch 前拒绝」；`use_sql` repair/普通检索兜底分别归 TC-SQL-603、TC-SQL-1002，不重复计入本用例。

**优先级**：P0

### TC-SQL-511: 查询异常后归还连接

**类型**：异常 · 单元 · P1

**步骤**：调用 `GaussDBConnection.sql("SELECT doc_id FROM ragflow_tenant WHERE kb_id = 'kb1'")`，令业务 SQL execute 抛 `RuntimeError("query timeout")`。

**预期结果**：
- cursor 先记录 `SET LOCAL statement_timeout = 30000`，随后 schema-qualified 业务 SQL 抛原始 `RuntimeError("query timeout")`；不得执行 `SET LOCAL search_path`。
- cursor 被关闭且连接被放回 pool，不遗留借出的连接。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_511_sql_returns_connection_after_query_failure`。

**优先级**：P1

### TC-SQL-512: runtime readonly guard 表边界与安全 scope

**类型**：安全 · 单元 · P0

**步骤**：
1. 使用 `GaussDBSQLValidator.readonly_guard()` 校验不读取 DocEngine 表的 `SELECT 1`。
2. 校验从受控表读取并通过 CTE 暴露 alias 的查询。
3. 校验普通业务谓词位于静态 `kb_id` 边界之前的查询。

**预期结果**：
- `SELECT 1` 精确拒绝为 `SQL must read from a DocEngine table`。
- CTE 查询保留内部静态 `kb_id = 'kb-1'`，输出列为 `amount`，非聚合并补 `LIMIT 128`。
- 普通谓词与静态 KB 边界均保留，输出列为 `doc_id`，非聚合并补 `LIMIT 128`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_512_runtime_readonly_guard_enforces_table_and_preserves_safe_scopes`。

**优先级**：P0

---

## 七、初始 prompt / repair / retry（6xx）

> 本组验证 `use_sql` 的确定性 prompt/repair/retry 状态机，统一归为组件单元；真实 GaussDB 执行只在 5xx、7xx/8xx 中单独保留。fake chat/retriever 必须返回固定序列，不能 mock 掉 validator 或 `use_sql` 自身。

### TC-SQL-601: 初始 prompt 含 #>> 规则

**类型**：正向 · 组件单元 · P0

**前置条件**：
```python
from rag.utils.gaussdb_text_to_sql import build_sql_prompt
field_map = {"amount": "number", "status": "string"}
```

**步骤**：
1. 调用 `build_sql_prompt("ragflow_t1", field_map, "What is the total amount?")`
2. 真实调用 `use_sql`，用记录型 fake chat 捕获 `async_chat(sys_prompt, messages, params)`，retriever 只返回固定结果
3. 检查生成的 prompt 文本、user message、LLM 参数和经 validator 规范化后的执行 SQL
4. 用固定 `doc_ids=[doc_id]` 再调用 `use_sql`，让 LLM 返回不含 WHERE 的合法查询，验证文档 scope 在 validator 之前注入

**预期结果**：
- prompt 含 `"chunk_data #>> '{FieldName}'"`（`gaussdb_text_to_sql.build_sql_prompt`）
- prompt 含 `"chunk_data #> '{FieldName}'"`
- 断言：`assert "chunk_data #>>" in prompt` 且 `assert "chunk_data #>" in prompt`
- prompt 含 `"Do not use json_extract, json_extract_string, or json_extract_isnull; use #>> / #> instead"`
- 精确断言：`assert "Do not use json_extract, json_extract_string, or json_extract_isnull; use #>> / #> instead" in prompt`
- prompt 含 `"Fields:\n  - amount (number): chunk_data #>> '{amount}'\n  - status (string): chunk_data #>> '{status}'"`
- 断言：`assert "chunk_data #>> '{amount}'" in prompt`
- 精确断言：prompt 含 `"Do not add kb_id to SELECT or WHERE; the system injects and validates the KB scope."`
- 精确断言：prompt 含 `"Numeric cast: CAST(chunk_data #>> '{FieldName}' AS DOUBLE PRECISION)"` 和 `"Date cast: to_date(chunk_data #>> '{FieldName}', 'YYYY-MM-DD')"`
- 精确断言：prompt 含 `"NULL check: (chunk_data #>> '{FieldName}') IS NOT NULL"`
- prompt 明确包含以下独立安全规则：仅允许静态已列出 path、禁止动态 path、A/ORA 空串使用 `IS NULL/IS NOT NULL`、只返回一个只读 SELECT、禁止 LATERAL/JOIN/集合操作/OR、禁止 JSON 展开函数、禁止完整 DML/DDL/权限/过程语句矩阵。
- 测试侧规则列表必须是本方案的独立 literal，不得导入生产 prompt 常量生成 expected。
- `use_sql` 传给模型的 system prompt 含表名和两个固定 field path；user message 含同样的表/字段和原问题；LLM 参数精确为 `{"temperature": 0.06}`。
- retriever 只收到一条经 validator 规范化的 GaussDB SQL，数值 cast 为 `CAST(... AS DOUBLE PRECISION)` 并补 `LIMIT 128`。
- `doc_ids` 场景的最终 SQL 精确包含 `WHERE doc_id = '<doc_id>' AND kb_id = '<kb_id>' LIMIT 128`；文档边界先由 `inject_doc_scope()` 加入，随后 validator 在同一简单单表 WHERE 上补 KB 边界，不得因先校验无 KB SQL而错误拒绝。
- 异常断言：本用例不抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_601_gaussdb_prompt_uses_jsonb_path_not_oceanbase_json_helpers`；路由执行补充：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_601_use_sql_routes_gaussdb_prompt_through_retriever`；文档 scope 顺序：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_601_gaussdb_injects_doc_scope_before_validator`。

**验收口径**：技术设计 3.5「GaussDB prompt 规则」

**优先级**：P0

### TC-SQL-602: row_count_override 强制 COUNT(*)

**类型**：边界 · 组件单元 · P1

**前置条件**：
```python
from api.db.services.dialog_service import use_sql
from unittest.mock import Mock, AsyncMock, patch
field_map = {"amount": "number"}
tenant_id = "0123456789abcdef0123456789abcdef"
table = f"ragflow_{tenant_id}"
kb_id = "abcdefabcdefabcdefabcdefabcdefab"
kb_ids = [kb_id]
chat_mdl = Mock()
chat_mdl.async_chat = AsyncMock(return_value=f"SELECT COUNT(*) AS rows FROM {table}")
fake_retriever = Mock()
fake_retriever.sql_retrieval = Mock(return_value={"columns": [{"name": "rows"}], "rows": [[5]]})
monkeypatch.setattr(settings, "retriever", fake_retriever)
```

**步骤**：
1. 调用 `use_sql("How many rows in the dataset?", field_map, tenant_id, chat_mdl, kb_ids=kb_ids)` with question matching `is_row_count_question`

**预期结果**：
- `row_count_override` = `f"SELECT COUNT(*) AS rows FROM {table}"`（`use_sql` 的 GaussDB prompt 分支）
- SQL forced to `f"SELECT COUNT(*) AS rows FROM {table}"`
- `chat_mdl.async_chat.assert_not_awaited()`（row_count_override used）
- `settings.retriever.sql_retrieval` 精确调用两次：首条为 `SELECT COUNT(*) AS rows FROM <table> WHERE kb_id = '<kb_id>' LIMIT 128`；GaussDB 聚合 source 补查同样经 validator 限制为 `LIMIT 128`。
- 返回 `{"answer": "|rows|\n|------\n|5|", "reference": {"chunks": [], "doc_aggs": []}}`
- 断言 `result["answer"].startswith("|rows|")`，`result["reference"] == {"chunks": [], "doc_aggs": []}`
- 异常断言：不抛异常；`kb_ids` 必须是合法 UUID，否则先抛 `ValueError`，message 含 `"Invalid kb_id format"`
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_602_row_count_override_skips_llm_and_executes_scoped_count`。

**清理**：恢复 monkeypatch；无真实 DB 对象。

**验收口径**：技术设计 3.5「row_count_override 强制 COUNT(*)」

**优先级**：P1

### TC-SQL-603: validator 拒绝触发 repair

**类型**：异常 · 组件单元 · P0

**前置条件**：固定 `tenant_id/table/kb_id/field_map`；fake chat 依次返回 `SELECT *` 和合法 SQL；fake retriever 只返回确定成功表格。真实执行 validator 和 `use_sql`。

**步骤**：
1. Mock `chat_mdl.async_chat` side_effect: first call returns `f"SELECT * FROM {table}"`, second call returns `f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount FROM {table}"`
2. 调用 `use_sql("Show all amounts", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`
3. 断言 `chat_mdl.async_chat.await_count == 2`，并检查第二次 prompt 含 `"SELECT * is not allowed"`。

**预期结果**：
- 初始 SQL `f"SELECT * FROM {table}"` 被 validator 拒绝，抛 `UnsafeGaussDBSQL("SELECT * is not allowed")`
- 进入 initial execution except 分支，通过 `gaussdb_text_to_sql.build_retry_prompt` 构造带原错误的 retry prompt 并再次执行
- repair prompt 含 `"SELECT * is not allowed"` 错误信息
- retry/repair prompt 同时包含 TC-SQL-601 的完整静态 path、空串、单语句、复杂 SQL、JSON 展开及 DML/DDL 安全规则，不能只补一条错误消息
- repair 后 SQL 通过 validator，含 `"SELECT doc_id, docnm_kwd, chunk_data #>> '{amount}'"`
- 最终返回 `{"answer": "|number|Source|\n|------|------|\n|120| ##0$$|", "reference": {"chunks": [{"doc_id": "d1", "docnm_kwd": "doc.xlsx", "kb_id": "abcdefabcdefabcdefabcdefabcdefab"}], "doc_aggs": [{"doc_id": "d1", "doc_name": "doc.xlsx", "count": 1}]}}`
- `chat_mdl.async_chat.await_count == 2`（初始 + repair）
- `settings.retriever.sql_retrieval` 最后一次调用 SQL 含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`
- 异常断言：首轮内部异常 type 为 `UnsafeGaussDBSQL`，message 含 `"SELECT * is not allowed"`；最终不向外抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_603_validator_rejection_builds_retry_prompt`。

**验收口径**：技术设计 3.5「validator 拒绝 → repair → retry」

**优先级**：P0

### TC-SQL-604: 执行失败触发 retry

**类型**：异常 · 组件单元 · P0

**前置条件**：固定 tenant/table/kb/field_map；fake `sql_retrieval` 第一次返回 `None`，第二次返回成功表格；fake chat 返回合法 SQL 后返回 retry SQL。测试验证既有异常捕获能够进入 retry，不约束内部异常文本。

**步骤**：
1. Mock `settings.retriever.sql_retrieval` side_effect: first call returns `None`, second call returns `{"columns": [{"name": "doc_id"}, {"name": "docnm_kwd"}, {"name": "amount"}], "rows": [["d1", "doc.xlsx", "120"]]}`
2. 调用 `use_sql("Show all amounts", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`
3. 断言 `settings.retriever.sql_retrieval.call_count == 2`，并检查第二次 SQL 通过 validator 后含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`。

**预期结果**：
- validator 通过 SQL
- `sql_retrieval` 第一次返回 `None`，GaussDB 分支将其转为可进入 retry 的执行失败
- 触发 retry；不对 retry prompt 中的内部异常文本做精确断言
- retry 后 SQL 执行成功，返回 `{"columns": [{"name": "doc_id"}, {"name": "docnm_kwd"}, {"name": "amount"}], "rows": [["d1", "doc.xlsx", "120"]]}`
- 最终返回 `{"answer": "|amount|Source|\n|------|------|\n|120| ##0$$|", "reference": {"chunks": [{"doc_id": "d1", "docnm_kwd": "doc.xlsx", "kb_id": "abcdefabcdefabcdefabcdefabcdefab"}], "doc_aggs": [{"doc_id": "d1", "doc_name": "doc.xlsx", "count": 1}]}}`
- `settings.retriever.sql_retrieval.call_count == 2`
- `chat_mdl.async_chat.await_count == 2`
- 异常断言：不向外抛异常；若第二次仍失败，返回 `None`。
- 自动化（Covered）：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_604_execution_none_retries`。

**验收口径**：技术设计 3.5「执行失败 → retry」

**优先级**：P0

### TC-SQL-605: repair/retry 用尽返回 None

**类型**：异常 · 组件单元 · P0

**前置条件**：使用 `pytest.mark.parametrize("mode", ["validator_rejected", "execution_failed"])`；两个 mode 使用独立 fake 和调用计数，禁止在一个断言中使用 `or`。

**步骤**：
1. `validator_rejected`：chat 连续两次返回同一非法 SQL，retriever 设置为一旦调用就抛 AssertionError
2. `execution_failed`：chat 两次返回合法 SQL，retriever 两次返回 `None`
3. 分别调用 `use_sql(...)`

**预期结果**：
- 两个 mode 均精确返回 `None`
- `validator_rejected`：`chat.await_count == 2`、`sql_retrieval.call_count == 0`
- `execution_failed`：`chat.await_count == 2`、`sql_retrieval.call_count == 2`
- **后续走检索兜底**（TC-SQL-1001 验证）
- 两个 mode 的日志类别分别为 validator error 与 execution/retry error，不接受任一即可
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_605_retry_exhaustion_returns_none`，pytest 参数实例为 `[validator_rejected]` 与 `[execution_failed]`。

**验收口径**：技术设计 3.5「没有可用 SQL 结果时 validator/执行 retry 用尽 → 返回 None → 检索兜底」

**优先级**：P0

### TC-SQL-606: repair SQL 仍过 validator

**类型**：安全 · 组件单元 · P0

**前置条件**：固定合法 `tenant_id/table/kb_id`；fake chat 初始返回裸访问 `chunk_data` 的非法 SQL，repair 后返回不含 kb_id/LIMIT 的合法 `#>>` SQL；fake retriever 返回确定表格。真实执行两轮 validator。

**步骤**：
1. 调用 `use_sql("Show all amounts", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`
2. 断言 `settings.retriever.sql_retrieval.call_args.args[0]` 含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`、`chunk_data #>> '{amount}'`、`LIMIT 128`。

**预期结果**：
- 初始 SQL `f"SELECT doc_id, chunk_data FROM {table}"` 被 validator 以 `chunk_data may only be accessed through #> / #>>` 拒绝。
- repair 后 SQL `f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount FROM {table}"`
- repair SQL 重新经 `validate_and_patch` 校验
- repair SQL 通过 validator，含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`、`chunk_data #>> '{amount}'`、`LIMIT 128`
- repair SQL 执行成功
- 异常断言：首轮内部异常 type 为 `UnsafeGaussDBSQL`；最终不向外抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_606_retry_sql_is_revalidated_and_scoped`。

**验收口径**：技术设计 3.5「repair SQL 仍须过 validator」

**优先级**：P0

### TC-SQL-607: 缺 source 列 repair

**类型**：正向 · 组件单元 · P0

**前置条件**：固定合法 `tenant_id/table/kb_id/field_map`；fake chat 初始返回缺 source 列 SQL、第二轮返回带 `doc_id/docnm_kwd` 的合法 SQL；fake retriever 按顺序返回两份确定表格。

**步骤**：
1. Mock `chat_mdl.async_chat` returns `f"SELECT chunk_data #>> '{{amount}}' AS amount FROM {table}"` (缺少 doc_id/docnm_kwd)
2. 调用 `use_sql("Show all amounts", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`
3. 断言 `settings.retriever.sql_retrieval.call_args_list[1].args[0]` 是源列补全 SQL，且包含 `SELECT doc_id, docnm_kwd`、`FROM <table>`、`kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`。

**预期结果**：
- `has_source_columns(tbl["columns"])` 返回 False
- 触发 `repair_table_for_missing_source_columns`，其 GaussDB prompt 由 `gaussdb_text_to_sql.build_repair_prompt` 生成
- repair prompt 包含源列补全指令
- repair prompt 同时保留 TC-SQL-601 的完整安全规则；不得调用生产 prompt helper 生成测试 expected
- repair 返回非空 rows 且 source columns 完整后替换首轮结果；最终 SQL 的 SELECT 含 `doc_id, docnm_kwd`
- 最终返回包含源列的结果
- `chat_mdl.async_chat.await_count == 2`
- 异常断言：不抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_607_missing_source_columns_trigger_dedicated_repair`。

**优先级**：P0

### TC-SQL-608: repair 失败仍返回 answer

**类型**：边界 · 组件单元 · P1

**前置条件**：固定合法 `tenant_id/table/kb_id/field_map`；fake retriever 首次返回 `{"columns": [{"name": "amount"}], "rows": [["120"]]}`、第二次抛 `RuntimeError("source lookup failed")`；fake chat 依次返回缺 source SQL 和带 source SQL。

**步骤**：
1. 用 `caplog.at_level(logging.WARNING)` 调用 `use_sql("Show all amounts", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`。
2. 通过第二轮 `sql_retrieval` 抛错间接触发源列 repair 失败。

**预期结果**：
- 初始 SQL 缺 `doc_id`/`docnm_kwd`
- 不 patch 内部嵌套函数 `repair_table_for_missing_source_columns`；通过第二轮 chat/sql_retrieval 抛错间接触发。
- `use_sql` catches exception, logs warning
- 仍返回 `{"answer": "|number|\n|------\n|120|", "reference": {"chunks": [], "doc_aggs": []}}`（`field_map["amount"] == "number"` 映射列名；answer 存在，chunks 空）
- `caplog.text` 含 `"Source-column SQL repair failed"`
- 初始 prompt 与 source repair prompt 都包含 TC-SQL-601 的完整独立安全规则
- 异常断言：内部 `RuntimeError("source lookup failed")` 不向外抛。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_608_source_repair_failure_returns_best_effort_answer`。

**清理**：恢复 monkeypatch；无真实 DB 对象。

**验收口径**：技术设计 3.5「repair 失败仍返回 answer」

**优先级**：P1

### TC-SQL-609: field_map value 作为 JSONB path

**类型**：正向 · 单元 · P1

**步骤**：调用 `gaussdb_text_to_sql.build_sql_prompt`，传入 `field_map={"customer_alias": "customer_name", "amount": "number"}`；另由 TC-SQL-006/007/008 验证 `table.chunk()` 生成的 field_map value 与实际 `chunk_data` key 一致。

**预期结果**：
- 完整 prompt 精确列出 `customer_alias (customer_name): chunk_data #>> '{customer_name}'`，即非类型 descriptor 作为 JSONB path。
- 类型 descriptor `number` 不作为 path，`amount` 行精确为 `amount (number): chunk_data #>> '{amount}'`。
- 生产 `table.chunk()` 生成的 field_map 不得把 `row_id` 改写成 `row id` 等不存在于 `chunk_data` 的 path。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_609_gaussdb_prompt_uses_field_map_value_as_jsonb_path`。

**优先级**：P1

---

## 八、aggregate fallback / source chunk 补查（7xx）

### TC-SQL-701: 聚合 SQL 缺 source 触发 fallback

**类型**：正向 · 集成 + 组件单元 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'sales.xlsx', '{"amount": "200", "status": "active"}'::jsonb),
  ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'sales.xlsx', '{"amount": "150", "status": "active"}'::jsonb),
  ('c3', 'abcdefabcdefabcdefabcdefabcdefab', 'd2', 'sales.xlsx', '{"amount": "100", "status": "active"}'::jsonb),
  ('c4', 'abcdefabcdefabcdefabcdefabcdefab', 'd3', 'sales.xlsx', '{"amount": "50", "status": "active"}'::jsonb);
```
field_map={"amount": "number", "status": "string"}；fake chat model 返回 `f"SELECT SUM(CAST(chunk_data #>> '{{amount}}' AS DOUBLE PRECISION)) AS total FROM {table} WHERE chunk_data #>> '{{status}}' = 'active' LIMIT 3"`

**步骤**：
1. 调用 `use_sql("What is the total amount?", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`
2. 初始聚合 SQL 带业务谓词 `chunk_data #>> '{status}' = 'active'` 和 `LIMIT 3`；断言第二次 retrieval 是 source chunk 补查 SQL。
3. 直接用带 `GROUP BY`、`ORDER BY`、`LIMIT`、`OFFSET` 的双 KB SQL 调 source builder，断投影包含 `kb_id` 且移除聚合结果级分页；再输入带 `HAVING` 的聚合 SQL，验证无法安全保留时明确拒绝。

**预期结果**：
- SQL 为聚合查询，`is_aggregate_sql(sql) == True`
- `has_source_columns(tbl["columns"]) == False`（缺 doc_id/docnm_kwd）
- 触发 aggregate fallback；源码先构建源字段补查 SQL，再经 `gaussdb_validator.validate_and_patch(chunks_sql).sql` 注入 KB 边界。
- 第二次 SQL 投影替换为 `doc_id, docnm_kwd`，保留原业务 WHERE 和 validator 注入的 KB scope，但不继承聚合结果级 `LIMIT/OFFSET`；补查重新经过 validator 后使用独立的 `LIMIT 128`。
- 双 KB source SQL 精确投影 `doc_id, docnm_kwd, kb_id`；带 `HAVING` 时抛 `ValueError("GaussDB aggregate source lookup cannot preserve HAVING safely")`，不得静默丢失 HAVING 语义。
- 优先断最终 `result["reference"]["chunks"]` 中每项 `kb_id == "abcdefabcdefabcdefabcdefabcdefab"`，且 doc_id 覆盖 `{"d1", "d2", "d3"}`，证明原 `LIMIT 3` 未截断 4 条来源记录。
- answer 为 Markdown table，含 `"total"` 列，数值 `500`（与 DB SUM 一致）
- reference.chunks 含补查结果，doc_aggs 含 `"d1": {"doc_name": "sales.xlsx", "count": 2}, "d2": {"doc_name": "sales.xlsx", "count": 1}, "d3": {"doc_name": "sales.xlsx", "count": 1}`
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「聚合缺 source → fallback → 补查」

**优先级**：P0

### TC-SQL-702: 补查 SQL 过 validator

**类型**：安全 · 集成 · P0

**前置条件**：同 TC-SQL-701；已触发 aggregate fallback；透明 `RecordingDealer` 以 `dealer.sqls` 记录真实 Dealer 每次执行的 SQL。

**步骤**：
1. 调用 `use_sql(...)` 后取 `sql = dealer.sqls[1]`。
2. 断言 `sql` 是补查 SQL，且包含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`、`LIMIT 128`、`SELECT doc_id, docnm_kwd`。

**预期结果**：
- 补查 SQL 经 `gaussdb_validator.validate_and_patch(chunks_sql).sql`
- 补查 SQL 含 `kb_id = 'abcdefabcdefabcdefabcdefabcdefab'` 和 `LIMIT 128`
- 补查 SQL 符合白名单表列规则
- 补查 SQL 执行成功，返回 chunks
- 异常断言：不抛异常。

**验收口径**：技术设计 3.5「补查 SQL 仍须过 validator」

**优先级**：P0

### TC-SQL-703: 补查 SQL 执行异常返回空 chunks

**类型**：异常 · 组件单元 · P1

**前置条件**：不连接 DB；真实执行 `use_sql` 的聚合 fallback 编排，但在 retriever 外部边界注入确定错误：第一次返回聚合行，第二次抛 `RuntimeError("source lookup failed")`。

**步骤**：
1. 调用 `use_sql("What is the total amount?", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`。
2. 让 fake retriever 的第二次 `sql_retrieval` 抛异常。

**预期结果**：
- 第二次 `settings.retriever.sql_retrieval` 抛 `RuntimeError("source lookup failed")`。
- `use_sql` 捕获异常，返回 `{"answer": "|total_number|\n|------\n|120|", "reference": {"chunks": [], "doc_aggs": []}, "prompt": "gaussdb prompt"}`。
- `caplog.text` 含 `"Failed to fetch chunks"`。
- 异常断言：内部 `RuntimeError("source lookup failed")` 不向外抛。

**清理**：无 DB 资源；恢复本用例创建的 fake retriever。

**验收口径**：技术设计 3.5「补查 SQL 执行异常 → 无 chunks」

**自动化对照**：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_703_aggregate_source_lookup_failure_returns_empty_chunks`。

**优先级**：P1

### TC-SQL-704: WHERE 为空补查

**类型**：边界 · 集成 · P1

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'sales.xlsx', '{"amount": "200"}'::jsonb),
  ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'd2', 'sales.xlsx', '{"amount": "150"}'::jsonb);
```
field_map={"amount": "number"}；聚合 SQL `f"SELECT SUM(CAST(chunk_data #>> '{{amount}}' AS DOUBLE PRECISION)) AS total FROM {table}"`（无 WHERE）

**步骤**：
1. 调用 `use_sql("What is the total amount?", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`
2. 断言原始聚合 SQL 无 WHERE；再取透明 `RecordingDealer` 的 `dealer.sqls[1]`，断言补查 SQL 含 validator 注入的 `WHERE kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`。

**预期结果**：
- 原始聚合 SQL 无 WHERE，但 `gaussdb_validator.validate_and_patch` 后的 SQL 已注入 `WHERE kb_id = 'abcdefabcdefabcdefabcdefabcdefab'`，`where_match` 从已注入 SQL 中提取该 WHERE。
- 第二次真实 Dealer 执行的补查 SQL 含 `SELECT doc_id, docnm_kwd FROM <table> WHERE kb_id = 'abcdefabcdefabcdefabcdefabcdefab'` 和 `LIMIT 128`。
- 优先断最终 `result["reference"]["chunks"]` 非空，且每个 chunk 的 `kb_id == "abcdefabcdefabcdefabcdefabcdefab"`。
- answer 含聚合结果，`reference.doc_aggs` 按 `doc_id` 聚合。
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「WHERE 为空补查」

**优先级**：P1

### TC-SQL-705: is_aggregation 检测

**类型**：正向 · 单元 · P1

**前置条件**：
```python
from api.db.services.dialog_service import use_sql
# is_aggregate_sql is a local function, test via validator
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator
v = GaussDBSQLValidator(tables={"ragflow_t1"}, kb_ids=["kb-1"])
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT COUNT(*) AS cnt FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT SUM(CAST(chunk_data #>> '{amount}' AS DOUBLE PRECISION)) AS total FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT AVG(CAST(chunk_data #>> '{amount}' AS DOUBLE PRECISION)) AS avg_amt FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT MAX(CAST(chunk_data #>> '{amount}' AS DOUBLE PRECISION)) AS max_amt FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT MIN(CAST(chunk_data #>> '{amount}' AS DOUBLE PRECISION)) AS min_amt FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT doc_id FROM ragflow_t1")`
   - `is_gaussdb_aggregate_sql` / validator 处理只在字符串 literal、SQL 注释或 alias 中出现 `sum(` 的非聚合 SQL
   - `is_gaussdb_aggregate_sql("SELECT COUNT(*) FROM ragflow_t1; SELECT 1")`

**预期结果**：
- COUNT/SUM/AVG/MAX/MIN 五个场景分别断言完整补丁 SQL、对应 alias `columns` 与 `is_aggregation is True`。
- 非聚合 `SELECT doc_id` 精确返回 `SELECT doc_id FROM ragflow_t1 WHERE kb_id = 'kb-1' LIMIT 128`、`columns == ["doc_id"]`、`is_aggregation is False`。
- literal/comment/alias 中的 `sum(` 均为 `False`，证明检测来自 AST aggregate node 而不是文本正则。
- 多语句输入抛 `UnsafeGaussDBSQL("exactly one SQL statement is allowed")`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_705_validator_detects_all_supported_aggregations_and_non_aggregate`、`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_705_aggregate_detection_ignores_sum_text_outside_ast_functions`、`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_705_aggregate_detection_rejects_multiple_statements`、`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_705_use_sql_does_not_treat_aggregate_names_in_literals_or_comments_as_aggregates`。

**验收口径**：技术设计 3.5.6「is_aggregation 检测」

**优先级**：P1

### TC-SQL-706: columns 返回

**类型**：正向 · 单元 · P1

**前置条件**：
```python
from common.doc_store.gaussdb_conn_base import GaussDBSQLValidator, ExposedGaussDBTable
table = ExposedGaussDBTable.from_field_map("ragflow_t1", ["kb-1"], {"amount": "number"})
v = GaussDBSQLValidator({"ragflow_t1": table})
```

**步骤**：
1. 分别调用：
   - `v.validate_and_patch("SELECT doc_id, chunk_data #>> '{amount}' AS amt FROM ragflow_t1")`
   - `v.validate_and_patch("SELECT doc_id FROM ragflow_t1")`

**预期结果**：
- alias 场景完整 SQL 补 `WHERE kb_id = 'kb-1' LIMIT 128`，`columns == ["doc_id", "amt"]`。
- 普通列场景返回 `columns == ["doc_id"]`；`SELECT 1, doc_id` 返回 `columns == ["1", "doc_id"]`，不再使用私有假 AST。
- 三个场景均断言 `is_aggregation is False`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_706_validator_returns_selected_column_names_and_aliases`。

**验收口径**：技术设计 3.5.6「columns 返回」

**优先级**：P1

### TC-SQL-707: SELECT alias 可用于 GROUP BY / ORDER BY

**类型**：正向 · 单元 · P1

**步骤**：分别用 exposed-table validator 与 runtime readonly guard 校验 `SELECT chunk_data #>> '{dept}' AS dept, COUNT(*) AS cnt FROM ragflow_t1 WHERE kb_id = 'kb-1' GROUP BY dept ORDER BY dept`。

**预期结果**：
- 两个 guard 均精确返回原 SQL 加 `LIMIT 128`，不把 `dept` 误判为非白名单列。
- 两个结果均为 `columns == ["dept", "cnt"]`、`is_aggregation is True`。
- 自动化：`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_707_select_alias_is_allowed_in_group_and_order_by_for_both_guards`。

**优先级**：P1

---

## 九、多 KB reference 补全（8xx）

### TC-SQL-801: 多 KB chunks 缺 kb_id 补查

**类型**：正向 · 集成 + 组件单元 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb1="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"`、`kb2="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"`；DB 行保留合法 `kb_id`，首轮 SQL 只是不返回 `kb_id` 列：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'd1', 'doc.xlsx', '{"amount": "100"}'::jsonb),
  ('c2', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'd2', 'doc.xlsx', '{"amount": "200"}'::jsonb),
  ('c3', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'd3', 'doc.xlsx', '{"amount": "300"}'::jsonb);
```
kb_ids=[kb1, kb2]；field_map={"amount": "number"}

**步骤**：
1. 使用真实 `GaussDBConnection` 和 `Dealer` 执行 `use_sql`；fake chat 只负责返回固定首轮 SELECT，首轮 columns 不含 `kb_id`
2. 读取最终 answer/reference；不得访问真实 Dealer 上不存在的 mock `call_args_list`

**预期结果**：
- 首轮 SQL 返回 `{"columns": [{"name": "doc_id"}, {"name": "docnm_kwd"}], "rows": [["d1", "doc.xlsx"], ["d2", "doc.xlsx"], ["d3", "doc.xlsx"]]}`，因此 chunks 初始缺 `kb_id`。
- 构建补查 SQL `SELECT doc_id, kb_id FROM <table> WHERE doc_id IN ('d1', 'd2', 'd3')`，经 validator 后含 `kb_id IN ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')`。
- 补查返回 doc_id→kb_id 映射。
- chunks 最终含正确 `kb_id`：`d1/d2 -> kb1`，`d3 -> kb2`。
- `result["answer"]` 含三行对应值，所有 reference chunk 的 `kb_id` 属于 `{kb1,kb2}`
- 组件单元 fake 同样返回三条不同 doc_id，精确断言三条 chunk 的 KB 映射及一次 `doc_id IN (...) AND kb_id IN (...) LIMIT 128` 补查；不得用单行结果宣称完成多 KB。
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「多 KB chunks 缺 kb_id 补查」

**优先级**：P0

### TC-SQL-802: 单 KB chunks 补 kb_ids[0]

**类型**：正向 · 集成 · P0

**前置条件**：`gaussdb_env` fixture 就绪；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`（单 KB）；chunks 缺 kb_id

**步骤**：
1. 执行 SQL 返回 chunks（部分缺 kb_id）
2. 断言 `_chunk_kb_id_for_doc(row_dict, kb_ids=[kb_id], doc_id="d1") == kb_id`。

**预期结果**：
- `_chunk_kb_id_for_doc` 在 `len(kb_ids) == 1` 时直接返回 `kb_ids[0] = "abcdefabcdefabcdefabcdefabcdefab"`
- 不构建补查 SQL（单 KB 无需补查）
- chunks 的 `kb_id` 补为 `"abcdefabcdefabcdefabcdefabcdefab"`
- 验证方式：通过 use_sql 返回结果的 chunks 字段验证
- 异常断言：不抛异常。

**验收口径**：技术设计 3.5「单 KB chunks 补 kb_ids[0]」

**优先级**：P0

### TC-SQL-803: 补查失败仍返回 chunks

**类型**：边界 · 组件单元 · P1

**前置条件**：无需 DB；fake retriever 首轮返回固定缺 `kb_id` 的 chunks。参数化 lookup 结果为：抛 `RuntimeError("lookup failed")`、缺 `kb_id` column、`rows=[]`、长度小于 column 索引要求的短 row。真实执行 `use_sql` 的内联补查分支。

**步骤**：
1. 对上述 lookup 变体逐一调用 `use_sql`
2. 保存首轮 reference 深拷贝，与最终 reference 比较

**预期结果**：
- 当前 `use_sql` 内联补查分支捕获异常；不存在 `complete_gaussdb_reference_kb_ids` helper
- 四个变体均精确返回首轮 chunk `{"doc_id":"doc1","docnm_kwd":"finance.csv"}`，不抛异常、不伪造 `kb_id`。
- 多 KB 且首轮结果缺 `kb_id` 时，补查失败后 chunks 保持缺 `kb_id`
- 仅抛异常/非法 row 导致异常的变体记录 `"Failed to complete GaussDB reference kb_id values"`；单纯空 rows/缺列不要求 warning
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_803_multikb_lookup_failures_preserve_reference`，pytest 参数实例为 `[exception]`、`[missing-kb-column]`、`[empty-rows]`、`[short-row]`。

**清理**：恢复 fake retriever；无 DB 对象。

**验收口径**：技术设计 3.5「补查失败仍返回 chunks」

**优先级**：P1

### TC-SQL-804: 多 KB 补查 SQL 过 validator

**类型**：安全 · 组件单元 · P0

**前置条件**：无需 DB；`kb_ids=["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]`；fake retriever 首轮返回缺 kb_id 的 `doc1`，第二轮返回 `doc1 -> kb2`。

**步骤**：
1. 真实调用 `use_sql`，读取第二次 `sql_retrieval` 的 SQL。
2. 精确断言 SQL 为 `SELECT doc_id, kb_id FROM <table> WHERE doc_id IN ('doc1') AND kb_id IN ('<kb1>', '<kb2>') LIMIT 128`。

**预期结果**：
- 补查 SQL 经 `gaussdb_validator.validate_and_patch(lookup_sql).sql`
- 补查 SQL 含 `kb_id IN ('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')`（validator 补丁）
- 补查 SQL 符合白名单表列规则（doc_id, kb_id）
- 异常断言：不抛异常。
- 补查 SQL 执行成功
- 最终 reference chunk 的 `kb_id == kb2`。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_804_multikb_lookup_is_validated_and_scoped`。

**验收口径**：技术设计 3.5「多 KB 补查 SQL 过 validator」

**优先级**：P0

### TC-SQL-805: 结果已含 kb_id 时跳过补查

**类型**：边界 · 组件单元 · P1

**步骤**：多 KB 调用 `use_sql`，首轮结果 columns 已包含 `doc_id/docnm_kwd/kb_id`。

**预期结果**：
- 最终 chunk 保留首轮返回的正确 `kb_id`。
- `settings.retriever.sql_retrieval` 精确只调用一次，不生成 `SELECT doc_id, kb_id ...` 补查 SQL。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_805_use_sql_uses_inline_multikb_reference_kb_id`。

**优先级**：P1

---

## 十、返回结构（9xx）

### TC-SQL-901: answer 为 Markdown table

**类型**：正向 · 集成 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表并在 `chunk_data` 的 `Amount` path 写入固定数据；`field_map={"amount": "Amount"}`；chat model 返回 SQL `f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{Amount}}' AS amount FROM {table} WHERE kb_id = '{kb_id}' ORDER BY doc_id"`

**步骤**：
1. 调用 `result = await use_sql("show amount", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`，断言 `result["answer"]` 是 Markdown table。

**预期结果**：
- `answer` 为 Markdown table 格式：
  ```
  |Amount|Source|
  |------|------|
  | 100| ##0$$|
  | 200| ##1$$|
  ```
- 列名优先使用 alias 对应的 field_map 显示名；无映射时保留 alias/原名
- field_map 值是显示名（如"Amount"）而非类型描述
- 含 `"Source"` 列（doc_id+docnm_kwd）
- 列名无映射时保留原名（TC-SQL-902）
- `reference.chunks[*].kb_id == "abcdefabcdefabcdefabcdefabcdefab"`
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「answer Markdown table」

**优先级**：P0

### TC-SQL-902: 列名无映射保留原名

**类型**：边界 · 集成 · P1

**前置条件**：`gaussdb_env` fixture 就绪；建表+插数据；真实表字段映射仅为 `field_map={"amount": "amount"}`；使用已暴露 JSONB path `amount`，但给输出列一个不在 field_map 中的别名 `unlabelled_amount`。

**步骤**：
1. 执行 SQL `f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS unlabelled_amount FROM {table} ORDER BY doc_id"`。
2. 断言 `result["answer"].splitlines()[0] == "|unlabelled_amount|Source|"`。

**预期结果**：
- validator 仍只允许已暴露 path `amount`，不会为测试未映射显示名而放宽字段白名单
- 输出别名 `unlabelled_amount` 无显示名映射，精确保留原名
- `doc_id/docnm_kwd` 仅组成 `Source`，不出现在业务列标题中
- `map_column_name` returns alias if no mapping found
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「列名无映射保留原名」

**优先级**：P1

### TC-SQL-903: reference.chunks 结构

**类型**：正向 · 集成 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据；执行 SQL `f"SELECT doc_id, docnm_kwd, chunk_data #>> '{{amount}}' AS amount FROM {table} WHERE kb_id = '{kb_id}'"`

**步骤**：
1. 调用 `result = await use_sql("show amount", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`，断言 `result["reference"]["chunks"]` 结构。

**预期结果**：
- `reference.chunks` 为列表：`[{"doc_id": "d1", "docnm_kwd": "doc.xlsx", "kb_id": "abcdefabcdefabcdefabcdefabcdefab"}, {"doc_id": "d2", "docnm_kwd": "doc.xlsx", "kb_id": "abcdefabcdefabcdefabcdefabcdefab"}]`
- 每项含 `"doc_id", "docnm_kwd", "kb_id"`（source columns）
- `"kb_id"` 从 SQL 结果或补查获得（TC-SQL-801/802）
- chunks 数量 == SQL rows 数量
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「reference.chunks 结构」

**优先级**：P0

### TC-SQL-904: reference.doc_aggs 结构

**类型**：正向 · 集成 · P1

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；建表+插数据：
```sql
CREATE TABLE <table> (id text PRIMARY KEY, kb_id text, doc_id text, docnm_kwd text, chunk_data jsonb);
INSERT INTO <table> VALUES
  ('c1', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'doc.xlsx', '{"amount": "100"}'::jsonb),
  ('c2', 'abcdefabcdefabcdefabcdefabcdefab', 'd1', 'doc.xlsx', '{"amount": "150"}'::jsonb),
  ('c3', 'abcdefabcdefabcdefabcdefabcdefab', 'd2', 'sales.xlsx', '{"amount": "200"}'::jsonb);
```

**步骤**：
1. 执行 SQL `f"SELECT doc_id, docnm_kwd FROM {table} WHERE kb_id = '{kb_id}'"`
2. 检查 `reference.doc_aggs` 结构

**预期结果**：
- `reference.doc_aggs == [{"doc_id": "d1", "doc_name": "doc.xlsx", "count": 2}, {"doc_id": "d2", "doc_name": "sales.xlsx", "count": 1}]`
- 按 doc_id 聚合，count 为 chunk 数
- `doc_name` 为 docnm_kwd
- 无 chunks 时为空列表 `[]`
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「reference.doc_aggs 结构」

**优先级**：P1

### TC-SQL-905: dialog_service 生成 answer + reference

**类型**：正向 · 集成 · P0

**前置条件**：`gaussdb_env` fixture 就绪；使用 helper 创建 `<table>`；`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；独立 SQL 插入同一文档的两行，amount 分别为 200 和 150；field_map={"amount": "amount"}；执行 SQL `f"SELECT SUM(CAST(chunk_data #>> '{{amount}}' AS DOUBLE PRECISION)) AS total FROM {table}"`

**步骤**：
1. 调用 `result = await use_sql("sum amount", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])`，断言 `answer`、`reference`、`prompt` 三个键存在。

**预期结果**：
- 返回结果只含 `answer/reference/prompt` 三个顶层键；`answer` 精确包含人工计算和真实 DB SUM 均为 350 的聚合值
- `answer` 为 `|total|\n|------\n|350.0|`；Markdown 分隔行允许省略尾随 `|`
- `reference.chunks` 含 source chunks（补查结果）
- `reference.doc_aggs` 含文档聚合：`[{"doc_id": "d1", "doc_name": "doc.xlsx", "count": 2}]`
- `prompt` 含 GaussDB SQL prompt 规则
- result 含 `answer + reference + prompt` 三部分
- `reference.chunks[*].kb_id == "abcdefabcdefabcdefabcdefabcdefab"`
- 异常断言：不抛异常。

**清理**：只清理 `<table>` 中本用例数据，或由 helper 删除本用例唯一表。

**验收口径**：技术设计 3.5「dialog_service 生成 answer+reference」

**优先级**：P0

---

## 十一、失败兜底切检索（10xx）

> 本组呼应「Text-to-SQL 是支路、失败由检索兜底」。`use_sql` 返回 None 或无效时，`async_chat` 切到 `retriever.retrieval`。

### TC-SQL-1001: use_sql 返回 None 切检索

**类型**：流程 · 组件单元 · P0

**前置条件**：不连接 DB；构造最小 dialog，`dialog.kb_ids=[kb_id]`、`kb_id="abcdefabcdefabcdefabcdefabcdefab"`；patch `KnowledgebaseService.get_field_map` 返回 `{"amount":"number"}`，patch `use_sql` 为 `AsyncMock(return_value=None)`；`settings.retriever.retrieval` 为固定返回一条 d1 chunk/doc_agg 的 `AsyncMock`；其余 LLM/session 依赖使用测试模块已有确定 fake。

**步骤**：
1. Mock `use_sql` return_value=None
2. 调用 `async_chat`，由 `KnowledgebaseService.get_field_map(dialog.kb_ids)` 返回非空 field_map 触发 SQL 尝试
3. 尝试 `use_sql("What is the total amount?", field_map, dialog.tenant_id, chat_mdl, kb_ids=[kb_id])` 返回 `None`
4. 断言 `settings.retriever.retrieval.assert_awaited_once()`，并检查 await 参数中的 `kb_ids == [kb_id]`。

**预期结果**：
- `use_sql` 返回 `None`；`async_chat` 检查 answer/chunks 后进入 fallback。
- 进入 `retriever.retrieval`；`settings.retriever.retrieval.assert_awaited_once()`。
- 日志含英文 `"SQL failed or returned no results, falling back to vector search"`。
- 收集 async generator 的 final payload，精确断言 `answer == "fallback answer"`，reference chunk 的 `(doc_id,docnm_kwd,kb_id)==("d1","doc.xlsx",kb_id)`；`reference["chunks"]` 来自兜底检索，不是裸 `SearchResult`。
- 异常断言：不抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1001_use_sql_none_falls_back_to_retrieval`。

**清理**：pytest `monkeypatch` 恢复模块属性；无 DB/文件资源。

**验收口径**：技术设计 3.4/3.5「Text-to-SQL 失败 → 检索兜底」

**优先级**：P0

### TC-SQL-1002: validator 拒绝切检索

**类型**：流程 · 组件单元 · P0

**前置条件**：不连接 DB；真实执行 `use_sql`/validator/`async_chat`；固定 dialog/kb/field_map；chat fake 连续两次返回同一跨 schema SQL；`sql_retrieval` 设置为调用即失败，fallback `retrieval` 为返回 d1 的 `AsyncMock`。

**步骤**：
1. 调用 `async_chat` 尝试 `use_sql("What is the total amount?", field_map, dialog.tenant_id, chat_mdl, kb_ids=[kb_id])`
2. validator 拒绝跨 tenant SQL，repair 后仍拒绝
3. 断言 `settings.retriever.retrieval.assert_awaited_once()`，并检查 `caplog.text` 含 `"SQL failed or returned no results, falling back to vector search"`。

**预期结果**：
- 初始 SQL `"SELECT doc_id FROM other_tenant.ragflow_t1"` 被 validator 拒绝，抛 `UnsafeGaussDBSQL("cross-schema SQL is not allowed")`
- repair 后仍返回跨 tenant SQL，再次被拒绝
- `use_sql` 返回 `None`
- `use_sql` 返回 `None` 后切换到检索链路；`settings.retriever.retrieval.assert_awaited_once()`。
- async generator yield dict，最终输出含 `answer` 和 `reference`；`reference["chunks"]` 含 `"doc_id", "docnm_kwd", "kb_id"`。
- **兜底切换断言**：`caplog.text` 含 `"SQL failed or returned no results, falling back to vector search"` 和 `"cross-schema SQL is not allowed"`。
- 异常断言：内部 `UnsafeGaussDBSQL("cross-schema SQL is not allowed")` 不向外抛。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1002_validator_rejection_falls_back_to_retrieval`。

**清理**：pytest `monkeypatch` 恢复模块属性；无 DB/文件资源。

**验收口径**：技术设计 3.4/3.5「validator 拒绝 → 检索兜底」

**优先级**：P0

### TC-SQL-1003: 执行超时切检索

**类型**：流程 · 组件单元 · P0

**前置条件**：不连接 DB；真实执行 `use_sql`/validator/`async_chat`；固定 dialog/kb/field_map；chat fake 连续返回合法 SQL；`settings.retriever.sql_retrieval` 连续两次抛 `TimeoutError("query timeout")`，fallback `retrieval` 为返回 d1 的 `AsyncMock`。

**步骤**：
1. 调用 `async_chat` 尝试 `use_sql("What is the total amount?", field_map, dialog.tenant_id, chat_mdl, kb_ids=[kb_id])`
2. validator 通过 SQL，`sql_retrieval` 抛超时异常
3. 断言 `settings.retriever.retrieval.assert_awaited_once()`，并检查 `caplog.text` 含 `"query timeout"`。

**预期结果**：
- validator 通过 SQL
- `sql_retrieval` 抛 `"query timeout"` 异常
- retry 后仍超时
- `use_sql` 返回 `None`
- `use_sql` 返回 `None` 后切换到检索链路；`settings.retriever.retrieval.assert_awaited_once()`。
- async generator yield dict，最终输出含 `answer` 和 `reference`。
- **兜底切换断言**：`caplog.text` 含 `"query timeout"` 和 `"SQL failed or returned no results, falling back to vector search"`。
- 异常断言：内部 `TimeoutError("query timeout")` 不向外抛。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1003_sql_timeout_falls_back_to_retrieval`。

**清理**：pytest `monkeypatch` 恢复模块属性；无 DB/文件资源。

**验收口径**：技术设计 3.4/3.5「执行超时 → 检索兜底」

**优先级**：P0

### TC-SQL-1004: aggregate fallback answer 有效不切检索

**类型**：流程 · 组件单元 · P1

**前置条件**：不连接 DB；真实执行 `use_sql`/`async_chat`；聚合首查固定返回 `columns=[total], rows=[[450]]`，source 补查抛 `RuntimeError("source lookup failed")`；fallback `retrieval` 为 `AsyncMock`。

**步骤**：
1. 调用 `async_chat(dialog, [{"role": "user", "content": "What is the total amount?"}], stream=False)`，使 `use_sql("What is the total amount?", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])` 返回 answer 非空但 chunks 为空。
2. 聚合 SQL 缺 doc_id/docnm_kwd，补查失败
3. 断言 `settings.retriever.retrieval.assert_not_awaited()`，并断言最终 yield 的 `answer` 来自 SQL answer。

**预期结果**：
- 聚合 SQL 返回精确 answer `"|total|\n|------\n|450|"`。
- 补查 SQL 经 validator 后由 fake retriever 精确抛 `RuntimeError("source lookup failed")`
- `use_sql` 返回 `{"answer": "|total|\n|------\n|450|", "reference": {"chunks": [], "doc_aggs": []}, "prompt": <GaussDB prompt>}`。
- **不切检索**（answer 有效，chunks 空）
- `settings.retriever.retrieval.assert_not_awaited()`。
- 日志含 `"Failed to fetch chunks"`。
- 异常断言：内部补查异常不向外抛。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1004_aggregate_answer_survives_source_lookup_failure`。

**清理**：pytest `monkeypatch` 恢复模块属性；无 DB/文件资源。

**验收口径**：技术设计 3.4/3.5「aggregate fallback 失败但 answer 有效 → 不切检索」

**优先级**：P1

### TC-SQL-1005: answer 有效但 chunks 空仍返回

**类型**：边界 · 组件单元 · P1

**前置条件**：不连接 DB；patch `use_sql` 精确返回 `{"answer":"|total|\n|------|\n|450|","reference":{"chunks":[],"doc_aggs":[]}}`；fallback `retrieval` 为 `AsyncMock`；其余 async_chat 依赖使用确定 fake。

**步骤**：
1. 调用 `async_chat(dialog, [{"role": "user", "content": "What is the total amount?"}], stream=False)`，使 `use_sql("What is the total amount?", field_map, tenant_id, chat_mdl, kb_ids=[kb_id])` 返回 answer 但 chunks 空。
2. SQL 返回 `{"answer": "|total|\n|---|\n|450|", "reference": {"chunks": [], "doc_aggs": []}}`
3. 断言 `settings.retriever.retrieval.assert_not_awaited()`，证明 `async_chat` 因 answer 非空直接 yield SQL 结果。

**预期结果**：
- `ans.get("answer")` 有效（非空字符串）
- `chunks` 为空列表 `[]`
- `async_chat` 仍 yield ans，**不切检索**
- 返回 `{"answer": "|total|\n|---|\n|450|", "reference": {"chunks": [], "doc_aggs": []}}`
- `settings.retriever.retrieval.assert_not_awaited()`。
- 日志不含 `"use_sql: No rows returned"`
- 异常断言：不抛异常。
- 自动化：`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1005_nonempty_answer_with_empty_chunks_is_returned`。

**清理**：pytest `monkeypatch` 恢复模块属性；无 DB/文件资源。

**验收口径**：技术设计 3.4/3.5「answer 有效 chunks 空仍返回」

**优先级**：P1

### TC-SQL-1006: 兜底检索走 04 入口

**类型**：流程 · 集成 · P0

**前置条件**：`gaussdb_env` 和 `ragflow_kb_context` 就绪；生产 `create_idx()` 建表、独立 psycopg2 SQL 写入 d-sql-1006/kb_id 及确定全文词 `fallback-gaussdb-query`；真实 KB parser_config 写入非空 field_map；`settings.retriever` 使用透明 recorder 包裹真实 `Dealer(conn)`；chat boundary fake 依次返回两次会被 validator 拒绝的 `SELECT *` 和最终固定回答，不 mock `use_sql` 或检索。

**步骤**：
1. 调用 `async_chat`，问题为唯一词 `fallback-gaussdb-query`；真实 `use_sql` 两轮 validator 均拒绝并返回 None。
2. 等待 async generator 完成；读取真实 Dealer spy 的一次调用参数和最终 reference。

**预期结果**：
- 真实 `Dealer.retrieval` recorder 精确调用一次，参数中的 question 为 `fallback-gaussdb-query`、`kb_ids == [kb_id]`。
- 真实 GaussDB 检索命中本用例唯一 `ragflow_<tenant>` 表且使用真实 adapter 路径；不经过 ES/Infinity adapter。
- 最终 answer 精确为 `fallback answer [ID:0]`；reference 精确指向 `doc_id=="d-sql-1006"`、`kb_id==kb_id`，doc_aggs 为该文档一条记录；独立 GaussDB SQL 对该行计数为 1。
- 不再次调用同一生产 Dealer 生成 expected；expected 来自固定文档 ID、固定输入词和固定 final-answer boundary。
- **兜底入口断言**：`caplog.text` 含 `"SQL failed or returned no results, falling back to vector search"`。
- 异常断言：不抛异常。

**清理**：测试体在 `finally` 中删除本用例文档对象，并用 metadata DB 原始 SQL 断言残留计数为 0；集成 fixture 负责唯一表、行和数据库连接的最终清理，并对残留资源断言失败。

**验收口径**：技术设计 3.4「兜底检索走 04 检索入口」

**优先级**：P0

---

### TC-SQL-1007: use_sql 真实 GaussDB adapter 成功链路与安全拒绝

**类型**：集成 · P0

本主用例验证 Text-to-SQL 生成的只读 SQL 在真实 GaussDB chunk 表上执行，JSONB `chunk_data` 路径能够返回业务结果，并且写操作会被 GaussDB SQL guard 拒绝。它与 TC-SQL-1001/1006 的失败兜底检索语义相互独立。

**对应代码**：`test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_1007_text_to_sql_use_sql_to_gaussdb_adapter_full_chain`

**验收要求**：生产 `create_idx()` 建表，独立 psycopg2 SQL 写入三条数据并回读主键；通过真实 `dialog_service.use_sql` 调用 GaussDB adapter；精确断言 answer、reference.chunks、doc_aggs、kb scope、`LIMIT 128`、JSONB prompt guardrail 和危险 DML 拒绝；fixture teardown 验证唯一表零残留。两环境结果分别取自本轮固定 JUnit。

**优先级**：P0

## 测试清理

集成用例通过 fixture/helper 记录本用例创建的唯一表名和行主键，teardown 只清理本用例资源：

- 表清理断言：helper 仅对本用例生成的 `<table>` 执行 cleanup；不得对共享 `ragflow_t1` 或宽泛 `ragflow_%` 直接清理。
- 行清理断言：清理后 `SELECT COUNT(*) FROM <table> WHERE id IN (<case_ids>)` 返回 `0`；若 helper 删除整张唯一表，则 `information_schema.tables` 中不存在该 `<table>`。
- 异常断言：cleanup 不吞掉非本用例表名校验异常；非法表名触发 `ValueError("cleanup table is outside current test prefix")` 或 helper 中固定的同义 message。

---

## 用例汇总表

状态口径统一见 [README 用例状态口径](README.md#用例状态口径)。本表中单元/组件单元只有在真实执行被测逻辑、断言有效且 `.venv` 实跑通过时才标为 **Covered**；外部 chat/retriever 可以 fake，但不能 mock 掉 `use_sql`、validator 或流程本身。

| 编号 | 分类 | 名称 | 环境 | 优先级 | 自动化对照 |
| --- | --- | --- | --- | --- | --- |
| TC-SQL-001 | 进入 | table+field_map+GaussDB | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_001_async_chat_routes_table_field_map_to_gaussdb_use_sql`|
| TC-SQL-002 | 进入 | field_map 空走检索 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_002_field_map_empty_uses_normal_retrieval` |
| TC-SQL-004 | 进入 | 非 GaussDB 不走 | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_004_use_sql_non_gaussdb_engine_does_not_call_gaussdb_prompt`|
| TC-SQL-005 | 进入 | kb_ids 空拒绝 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_005_use_sql_requires_kb_ids_for_gaussdb`；不纳入真实 GaussDB 集成代码|
| TC-SQL-006 | 进入 | field_map 生成 | 集成 + 组件单元 | P1 | 集成: `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_006_table_chunk_persists_real_field_map`；组件单元 Covered: `test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_006_chunk_gaussdb_updates_field_map_and_column_names`|
| TC-SQL-007 | 进入 | chunk_data 存储 | 集成 + 组件单元 | P0 | 集成: `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_007_table_chunk_data_reaches_real_jsonb_table`；组件单元 Covered: `test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_007_chunk_manual_mode_gaussdb_stores_metadata_in_chunk_data`|
| TC-SQL-008 | 表格列 | GaussDB `row_id` chunk/field_map/prompt 闭环 | 组件单元 | P1 | Covered: `test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_008_chunk_auto_mode_preserves_row_id_text_and_prompt_path`|
| TC-SQL-009 | 表格列 | parser config/列名传播 | 组件单元 | P1 | Covered: `test/unit_test/rag/svr/test_gaussdb_table_column_roles_helpers.py::test_tc_sql_009_parser_config_merge_and_metadata_cleanup_keys`；`test/unit_test/rag/app/test_gaussdb_table_chunk_column_roles.py::test_tc_sql_006_chunk_gaussdb_updates_field_map_and_column_names`|
| TC-SQL-010 | 表格列 | ES 公共元数据聚合 | 单元 | P1 | Covered: `test/unit_test/rag/svr/test_gaussdb_table_column_roles_helpers.py::test_tc_sql_010_es_aggregates_public_chunk_metadata`|
| TC-SQL-011 | 表格列 | GaussDB chunk_data 聚合 | 单元 | P0 | Covered: `test/unit_test/rag/svr/test_gaussdb_table_column_roles_helpers.py::test_tc_sql_011_gaussdb_aggregates_chunk_data_metadata`|
| TC-SQL-101 | 拒绝 | 合法单表 SELECT | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_101_validator_allows_single_table_select_and_returns_columns` |
| TC-SQL-102 | 拒绝 | 只读 WITH | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_102_validator_allows_readonly_cte_and_exposed_output_aliases` |
| TC-SQL-103 | 拒绝 | 多语句/解析失败 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_103_validator_rejects_multiple_statements_and_wraps_parser_failures`|
| TC-SQL-104 | 拒绝 | DML | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_104_validator_rejects_dml_including_nested_cte` |
| TC-SQL-105 | 拒绝 | DDL | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_105_validator_rejects_ddl`|
| TC-SQL-106 | 拒绝 | CALL | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_106_validator_rejects_call`|
| TC-SQL-107 | 拒绝 | COPY | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_107_validator_rejects_copy`|
| TC-SQL-108 | 拒绝 | SELECT * | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_108_validator_rejects_select_star`|
| TC-SQL-109 | 拒绝 | COUNT(*) | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_109_validator_allows_count_star_and_marks_aggregation`|
| TC-SQL-110 | 拒绝 | 窗口函数 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_110_validator_rejects_rank_and_row_number_windows` |
| TC-SQL-111 | 拒绝 | 系统表/跨 schema | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_111_validator_rejects_system_table_and_cross_schema_table` |
| TC-SQL-112 | 拒绝 | 非白名单表 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_112_validator_rejects_non_whitelisted_table`|
| TC-SQL-113 | 拒绝 | 非白名单列 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_113_validator_rejects_non_whitelisted_column`|
| TC-SQL-114 | 拒绝 | 禁用函数 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_114_validator_rejects_forbidden_system_functions` |
| TC-SQL-115 | 拒绝 | JSON 函数 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_115_validator_rejects_oceanbase_json_functions`|
| TC-SQL-116 | 拒绝 | 禁用 JSONB 函数 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_116_validator_rejects_forbidden_jsonb_set_returning_functions`|
| TC-SQL-117 | 拒绝 | JOIN | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_117_validator_rejects_join_even_when_both_aliases_are_scoped` |
| TC-SQL-118 | 拒绝 | UNION | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_118_validator_rejects_union_as_non_select_root` |
| TC-SQL-119 | 拒绝 | OR | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_119_validator_rejects_or_predicate`|
| TC-SQL-120 | 拒绝 | 动态 path | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_120_validator_rejects_dynamic_or_wrong_source_jsonb_paths` |
| TC-SQL-121 | 拒绝 | 空 SQL | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_121_validator_rejects_empty_sql_variants` |
| TC-SQL-122 | 拒绝 | EXPLAIN | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_122_validator_rejects_explain`|
| TC-SQL-123 | 拒绝 | SET | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_123_validator_rejects_set_command`|
| TC-SQL-124 | 拒绝 | SHOW | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_124_validator_rejects_show_command`|
| TC-SQL-125 | 拒绝 | 所有 LATERAL 形态 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_125_validator_rejects_every_lateral_shape` |
| TC-SQL-126 | 拒绝 | jsonb_path_query | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_126_validator_rejects_jsonb_path_query`|
| TC-SQL-127 | 拒绝 | jsonb_to_record | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_127_validator_rejects_jsonb_to_record`|
| TC-SQL-128 | 拒绝 | generate_series | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_128_validator_rejects_generate_series`|
| TC-SQL-129 | 拒绝 | unnest | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_129_validator_rejects_unnest` |
| TC-SQL-130 | 拒绝 | 其他写入/管理/权限语句 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_130_validator_rejects_additional_write_and_admin_statements` |
| TC-SQL-201 | kb_id | 自动注入 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_201_validator_injects_kb_id_for_simple_single_table_query` |
| TC-SQL-202 | kb_id | 多 KB IN | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_202_validator_injects_multi_kb_boundary` |
| TC-SQL-203 | kb_id | 已有合法边界 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_203_validator_preserves_allowed_kb_boundary_shapes` |
| TC-SQL-204 | kb_id | 跨 KB 拒绝 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_204_validator_rejects_cross_kb_boundary`|
| TC-SQL-205 | kb_id | 非顶层谓词 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_205_validator_rejects_non_positive_or_dynamic_kb_predicates` |
| TC-SQL-206 | kb_id | 复杂 SQL 拒绝 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_206_validator_rejects_complex_sql_when_scope_cannot_be_patched`|
| TC-SQL-207 | kb_id | 多表拒绝 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_207_validator_rejects_second_non_whitelisted_table`|
| TC-SQL-208 | kb_id | 缺失拒绝 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_208_validator_rejects_missing_or_unsafe_required_kb_boundary` |
| TC-SQL-209 | kb_id | WITH/子查询逐 scope 证明 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_209_validator_requires_each_cte_base_scope_to_prove_kb_boundary`<br>`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_209_validator_requires_each_subquery_scope_to_prove_kb_boundary` |
| TC-SQL-301 | limit | 缺失补 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_301_validator_adds_default_limit_when_missing` |
| TC-SQL-302 | limit | 过大改 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_302_validator_caps_large_limit_to_default_limit`|
| TC-SQL-303 | limit | 100 不补 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_303_validator_preserves_limit_under_default_limit`|
| TC-SQL-304 | limit | 非正数/动态/非整数拒绝 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_304_validator_rejects_non_positive_dynamic_or_non_integer_limits`|
| TC-SQL-305 | limit | 复杂 scope 前置拒绝 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_305_validator_rejects_complex_scope_before_limit_patch`|
| TC-SQL-306 | limit | fetch_size 透传 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_306_readonly_guard_uses_fetch_size_as_default_limit`；`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_306_sql_passes_fetch_size_to_readonly_guard`|
| TC-SQL-307 | limit | FETCH FIRST 裁剪 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_307_validator_caps_fetch_first_to_default_limit`|
| TC-SQL-308 | limit | literal/子查询关键字不影响 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_308_clause_tokens_in_literals_or_subqueries_do_not_block_ast_patches` |
| TC-SQL-401 | JSONB | #>> 单键 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_401_validator_allows_single_key_jsonb_text_path` |
| TC-SQL-402 | JSONB | #>> 多键 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_402_validator_allows_multi_key_jsonb_text_path`|
| TC-SQL-403 | JSONB | 含逗号引号 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_403_validator_requires_quotes_for_jsonb_key_containing_comma` |
| TC-SQL-404 | JSONB | #> 对象 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_404_validator_allows_jsonb_object_extraction_operator`|
| TC-SQL-405 | JSONB | 数值 CAST | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_405_validator_allows_numeric_cast_on_exposed_jsonb_field`|
| TC-SQL-406 | JSONB | to_date | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_406_validator_allows_to_date_on_exposed_jsonb_field` |
| TC-SQL-407 | JSONB | IS NULL/NOT NULL | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_407_validator_allows_jsonb_text_null_checks`|
| TC-SQL-408 | JSONB | 未声明 path | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_408_validator_rejects_unexposed_or_empty_map_jsonb_path` |
| TC-SQL-409 | JSONB | 裸访问 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_409_validator_rejects_bare_or_non_gaussdb_chunk_data_access` |
| TC-SQL-410 | JSONB | A/ORA SQL 空串拒绝、JSONB null 允许 | 单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_410_empty_value_prompt_rules_and_validator_rejects_empty_string_comparison`<br>`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_410_validator_rejects_jsonb_text_comparisons_with_sql_empty_string`<br>`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_410_validator_allows_jsonb_null_literal_comparison`|
| TC-SQL-411 | JSONB | 空值态行为 | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_411_jsonb_missing_null_and_empty_values` |
| TC-SQL-412 | JSONB | path literal 编码/拒绝 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_412_jsonb_path_literal_encodes_special_keys`；`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_412_jsonb_path_literal_rejects_empty_path_or_segment`；`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_412_jsonb_path_literal_parser_handles_escapes_and_invalid_shapes` |
| TC-SQL-501 | sql() | validator 接入 | 集成 + 组件单元 | P0 | 集成: `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_501_sql_uses_real_validator_and_returns_rows`；组件单元 Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_501_sql_executes_scoped_docengine_select_with_runtime_guard` |
| TC-SQL-502 | sql() | timeout 生效 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_502_fetch_all_with_description_sets_statement_timeout_before_business_sql`|
| TC-SQL-503 | sql() | 超时中断（mock） | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_503_sql_propagates_timeout_from_fetch_all_with_statement_timeout`|
| TC-SQL-504 | sql() | 自动 reset | 集成 | P1 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_504_sql_statement_timeout_is_reset_after_checkout` |
| TC-SQL-505 | sql() | columns+rows | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_505_sql_returns_columns_and_rows` |
| TC-SQL-506 | sql() | 值转换 | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_506_sql_converts_row_values_after_runtime_validation` |
| TC-SQL-507 | sql() | 空 rows | 集成 | P1 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_507_sql_preserves_columns_for_empty_rows` |
| TC-SQL-508 | sql() | markdown | 集成 + 组件单元 | P1 | 集成: `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_508_sql_returns_real_markdown`；组件单元 Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_508_sql_markdown_serializes_bytes_and_json_values` |
| TC-SQL-509 | sql() | 真实数据库执行错误/dialog retry | 集成 | P1 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_509_adapter_database_error_and_use_sql_retry_are_real` |
| TC-SQL-510 | sql() | 拒绝不执行 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_510_validator_rejection_prevents_database_fetch`<br>`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_510_sql_runtime_guard_rejects_unscoped_or_unsafe_sql`|
| TC-SQL-511 | sql() | 异常后归还连接 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_sql_511_sql_returns_connection_after_query_failure` |
| TC-SQL-512 | sql() | runtime guard 表边界与安全 scope | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_512_runtime_readonly_guard_enforces_table_and_preserves_safe_scopes` |
| TC-SQL-601 | prompt / scope | 初始 prompt 完整规则与 doc scope 前置注入 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_601_gaussdb_prompt_uses_jsonb_path_not_oceanbase_json_helpers`<br>`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_601_use_sql_routes_gaussdb_prompt_through_retriever`<br>`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_601_gaussdb_injects_doc_scope_before_validator`|
| TC-SQL-602 | prompt | row_count_override | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_602_row_count_override_skips_llm_and_executes_scoped_count` |
| TC-SQL-603 | prompt | validator 拒绝 repair | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_603_validator_rejection_builds_retry_prompt` |
| TC-SQL-604 | prompt | 执行失败 retry | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_604_execution_none_retries` |
| TC-SQL-605 | prompt | 两种 retry 用尽返回 None | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_605_retry_exhaustion_returns_none` |
| TC-SQL-606 | prompt | repair 过 validator | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_606_retry_sql_is_revalidated_and_scoped` |
| TC-SQL-607 | prompt | 缺 source repair | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_607_missing_source_columns_trigger_dedicated_repair` |
| TC-SQL-608 | prompt | repair 失败仍 answer | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_608_source_repair_failure_returns_best_effort_answer` |
| TC-SQL-609 | prompt | field_map value path | 单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_609_gaussdb_prompt_uses_field_map_value_as_jsonb_path` |
| TC-SQL-701 | fallback | 聚合缺 source | 集成 + 组件单元 | P0 | 集成双环境 Passed：`test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_701_aggregate_sql_fetches_real_source_chunks`（证据见 2026-08-12 当前 JUnit）；组件单元 Passed: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_701_use_sql_aggregate_fallback_fetches_sources_with_kb_scope`<br>`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_701_aggregate_source_builder_projects_multikb_scope_and_rejects_having`|
| TC-SQL-702 | fallback | 补查过 validator | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_702_aggregate_source_lookup_is_real_validator_scoped` |
| TC-SQL-703 | fallback | 补查执行异常 | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_703_aggregate_source_lookup_failure_returns_empty_chunks`|
| TC-SQL-704 | fallback | WHERE 空 | 集成 | P1 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_704_aggregate_without_where_gets_scoped_source_lookup` |
| TC-SQL-705 | fallback | AST is_aggregation / 文本误判防护 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_705_validator_detects_all_supported_aggregations_and_non_aggregate`<br>`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_705_aggregate_detection_ignores_sum_text_outside_ast_functions`<br>`test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_705_aggregate_detection_rejects_multiple_statements`<br>`test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_705_use_sql_does_not_treat_aggregate_names_in_literals_or_comments_as_aggregates` |
| TC-SQL-706 | fallback | columns 返回 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_706_validator_returns_selected_column_names_and_aliases`|
| TC-SQL-707 | fallback | alias GROUP/ORDER | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_sql_validator.py::test_tc_sql_707_select_alias_is_allowed_in_group_and_order_by_for_both_guards` |
| TC-SQL-801 | 多KB | 缺 kb_id 补查 | 集成 + 组件单元 | P0 | 集成: `test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_801_text_to_sql_multikb_reference_completes_kb_id_in_live_gaussdb`；组件单元 Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_801_use_sql_completes_multikb_reference_kb_id`|
| TC-SQL-802 | 多KB | 单 KB 补 kb_ids[0] | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_802_single_kb_reference_gets_kb_id_without_lookup` |
| TC-SQL-803 | 多KB | 补查异常/缺列/空行/非法行保持原 reference | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_803_multikb_lookup_failures_preserve_reference` |
| TC-SQL-804 | 多KB | 补查过 validator | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_804_multikb_lookup_is_validated_and_scoped` |
| TC-SQL-805 | 多KB | 已有 kb_id 跳过补查 | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_805_use_sql_uses_inline_multikb_reference_kb_id` |
| TC-SQL-901 | 返回 | answer markdown | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_901_use_sql_answer_is_markdown` |
| TC-SQL-902 | 返回 | 列名无映射 | 集成 | P1 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_902_unmapped_exposed_column_keeps_original_name` |
| TC-SQL-903 | 返回 | chunks 结构 | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_903_reference_chunks_have_source_shape` |
| TC-SQL-904 | 返回 | doc_aggs 结构 | 集成 | P1 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_904_reference_doc_aggs_count_real_rows` |
| TC-SQL-905 | 返回 | dialog 生成 | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_905_use_sql_returns_answer_reference_and_prompt` |
| TC-SQL-1001 | 兜底 | None 切检索 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1001_use_sql_none_falls_back_to_retrieval` |
| TC-SQL-1002 | 兜底 | validator 拒绝切 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1002_validator_rejection_falls_back_to_retrieval` |
| TC-SQL-1003 | 兜底 | 超时切 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1003_sql_timeout_falls_back_to_retrieval` |
| TC-SQL-1004 | 兜底 | answer 有效不切 | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1004_aggregate_answer_survives_source_lookup_failure` |
| TC-SQL-1005 | 兜底 | answer 有效 chunks 空 | 组件单元 | P1 | Covered: `test/unit_test/api/db/services/test_gaussdb_dialog_sql.py::test_tc_sql_1005_nonempty_answer_with_empty_chunks_is_returned` |
| TC-SQL-1006 | 兜底 | 走 04 入口 | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_1006_async_chat_falls_back_to_real_dealer_retrieval` |
| TC-SQL-1007 | 兜底/成功链 | use_sql 真实 GaussDB adapter 成功链路与安全拒绝 | 集成 | P0 | `test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_1007_text_to_sql_use_sql_to_gaussdb_adapter_full_chain`；本轮双环境结果见顶部矩阵 |

**用例统计**：正文共 114 条，P0 65 条、P1 49 条；正式汇总同为 114 行。所有带 `TC-SQL-*` 标题的用例都计入同一分母，不再区分“主方案”与“不计入分母的补充场景”。覆盖状态以当前真实 `file::test` 和断言为准。

## 本轮双环境补强 Detailed Cases

### TC-SQL-501 真实只读执行与 tenant 安全边界

- **环境适用性 / 优先级**：Both / P0。
- **主测试函数**：`test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_501_sql_uses_real_validator_and_returns_rows`。
- **前置条件**：两个唯一合法 `ragflow_*` tenant 表，真实生产 SQL validator/adapter。
- **测试数据 / Inputs**：目标 tenant 两行；另一 tenant 一条 `other-tenant-secret` sentinel；固定 KB。
- **Steps**：目标表执行真实 SELECT/fetch_size；验证非法前缀和跨 schema 拒绝；证明底层 DB 中另一表 sentinel 实际存在；用目标 tenant validator 校验另一合法 tenant SQL。
- **Expected Assertions**：目标查询精确返回 d1；错误表/跨 schema/跨 tenant 在执行前抛 `UnsafeGaussDBSQL`；另一 tenant sentinel 始终不变。
- **Negative Assertions**：不得只测试非法表名；不得通过 Mock 证明未进 DB；错误 tenant 不得泄露行。
- **Transaction**：种子独立提交，查询只读。
- **Cleanup / Cleanup Verification**：fixture 删除两个唯一表并独立确认 0。
- **Replay**：两环境定向执行。

### TC-SQL-504 真实数据库 statement timeout 与连接恢复

- **环境适用性 / 优先级**：Both / P0。
- **主测试函数**：`test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_504_sql_statement_timeout_is_reset_after_checkout`。
- **前置条件**：真实生产 pool/driver；测试进程只将 timeout 常量缩短为 100ms，不替换 connection/cursor/SQL 返回。
- **测试数据 / Inputs**：固定 d000；独立真实连接持有唯一测试表的 `ACCESS EXCLUSIVE` 锁；生产 adapter 执行 validator 可接受的单表 COUNT。
- **Steps**：记录 session timeout；持锁后由生产 adapter 执行单表只读 SQL并在真实锁等待中超过 100ms；捕获真实 SQLSTATE 57014；`finally` 回滚持锁事务；重新 checkout 并执行 canary SELECT。
- **Expected Assertions**：数据库明确因 statement timeout 取消；归还后 timeout 恢复为原值；canary 精确返回 d000。
- **Negative Assertions**：不得用 fake exception、RecordingConnection 或调用次数代替真实取消；不得使用会被 validator 提前拒绝的复杂 SQL；连接不得停留 aborted transaction。
- **Transaction**：锁持有连接与被测连接相互独立；持锁事务在 `finally` 回滚；`SET LOCAL` 只在本次被测事务生效；异常 rollback 后连接复用。
- **Cleanup / Cleanup Verification**：fixture 清表并由独立连接确认 0。
- **Replay**：两环境分别定向执行并记录耗时。

### TC-SQL-505/508/901 固定顺序和 field_map 输入

- **环境适用性 / 优先级**：Both / P0/P1。
- **主测试函数**：`test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_505_sql_returns_columns_and_rows`、`::test_tc_sql_508_sql_returns_real_markdown`、`::test_tc_sql_901_use_sql_answer_is_markdown`。
- **前置条件**：真实表和生产 adapter/use_sql。
- **测试数据 / Inputs**：505/508 的 SQL 明确包含 `ORDER BY doc_id`；901 的 `field_map={"amount":"Amount"}` 对应查询 JSONB path `{Amount}`。
- **Steps**：独立 SQL写入固定 d1/d2；执行真实查询或 use_sql。
- **Expected Assertions**：columns、rows 与 Markdown 行顺序固定；field_map descriptor 与 JSONB path 一致。
- **Negative Assertions**：不得在无 ORDER BY 时断言顺序；不得让文档字段名与实际 SQL不同。
- **Transaction / Cleanup / Verification**：只读查询；fixture 清理唯一表并确认 0。
- **Replay**：三个 node 两环境分别执行。

### TC-SQL-509 validator 放行后的真实 DB 错误与 retry

- **环境适用性 / 优先级**：Both / P0。
- **主测试函数**：`test/integration/test_gaussdb_text_to_sql_coverage.py::test_tc_sql_509_adapter_database_error_and_use_sql_retry_are_real`。
- **前置条件**：真实 Dealer、adapter、GaussDB；确定性 LLM 仅提供两条 SQL。
- **测试数据 / Inputs**：首条 SQL 将字符串 status CAST 为 INTEGER，语法/validator 合法但真实数据库执行失败；第二条 SQL合法返回 amount/source。
- **Steps**：先直接证明首条 SQL收到真实数据类型 SQLSTATE；执行 canary 查询证明连接恢复；再调用 use_sql 触发一次 repair/retry。
- **Expected Assertions**：首轮真实 DB 错误；chat 被调用两次且第二次上下文含错误；首轮 recorder 保留 status CAST 语义并接受等价 `INT`/`INTEGER` 规范化；真实 SQL只在成功后形成精确 answer/chunks/doc_aggs。
- **Negative Assertions**：不得以 SQLGlot parse failure 替代数据库执行失败；不得 Mock retriever SQL返回。
- **Transaction**：失败 SQL回滚，retry 使用可复用 pool 连接。
- **Cleanup / Cleanup Verification**：fixture 清表并确认 0。
- **Replay**：两环境定向执行。

### TC-SQL-1007 use_sql 真实成功链与安全拒绝

- **环境适用性 / 优先级**：Both / P0。
- **主测试函数**：`test/integration/test_gaussdb_text_to_sql_flow.py::test_tc_sql_1007_text_to_sql_use_sql_to_gaussdb_adapter_full_chain`。
- **前置条件**：当前分支真实 use_sql/Dealer/adapter；真实专用 schema；Fake LLM 只提供固定 SQL。
- **测试数据 / Inputs**：唯一 tenant 表、固定 KB、可人工聚合的 JSONB 行、合法与越界 SQL。
- **Steps**：执行成功 SQL；核对 prompt、validator 补丁、真实返回和引用；再提交跨表/跨 KB/非只读输入。
- **Expected Assertions**：成功链精确 answer/reference/prompt；安全输入在 DB 执行前拒绝；合法表数据不变。
- **Negative Assertions**：不得把 HTTP/非空或调用次数作为业务 oracle；不得跨 tenant/KB 泄露。
- **Transaction**：SQL只读，失败后连接可继续执行 canary。
- **Cleanup / Cleanup Verification**：fixture 清表和 metadata 记录并独立确认 0。
- **Replay**：两环境分别定向执行，随后完整 21 个 SQL node 全量与复跑。
