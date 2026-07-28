# 03 - metadata filter 翻译测试方案

<!-- GAUSSDB-DISTRIBUTED-INTEGRATION-MATRIX:START -->
## 集成环境结果矩阵

本轮必须使用当前分支分别在真实 Centralized 与 Distributed GaussDB 上执行。历史结果仅作参考，不进入本轮验收；下表只有固定 JUnit 中真实存在且通过的 node 才能改为 `Passed`。

> 当前分支已于 2026-08-12 完成双环境数据库路径补充复跑。评审后改为 `CASE` 数值保护的 `TC-MGF-803` 已在两份当前 JUnit 中通过；其它行保留的 `gaussdb-*-latest.xml` 只表示冻结历史证据，当前总体口径见 08 报告。

| TC | 环境适用性 | 集中式 pytest node | 集中式结果 | 集中式证据 | 分布式 pytest node | 分布式结果 | 分布式证据 | 整体状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TC-MGF-203 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_203_empty_matches_missing_and_excludes_nonempty` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_203_empty_matches_missing_and_excludes_nonempty` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-204 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_204_empty_matches_json_null_and_excludes_nonempty` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_204_empty_matches_json_null_and_excludes_nonempty` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-205 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_205_empty_matches_empty_string_and_not_equal` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_205_empty_matches_empty_string_and_not_equal` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-206 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_206_empty_matches_empty_array_and_excludes_nonempty_array` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_206_empty_matches_empty_array_and_excludes_nonempty_array` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-207 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_207_empty_matches_empty_object_and_excludes_nonempty_object` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_207_empty_matches_empty_object_and_excludes_nonempty_object` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-208 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_208_not_equal_matches_null_and_empty_string_but_not_missing` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_208_not_equal_matches_null_and_empty_string_but_not_missing` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-209 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_209_not_empty_excludes_all_five_empty_states` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_209_not_empty_excludes_all_five_empty_states` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-801 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_801_equal_is_case_insensitive` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_801_equal_is_case_insensitive` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-802 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_802_contains_matches_array_element_and_scalar_substring` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_802_contains_matches_array_element_and_scalar_substring` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-803 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_803_greater_than_filters_numeric_values_and_rejects_non_numeric` | Passed | `test/integration/artifacts/gaussdb-centralized-clean-20260812-151227.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_803_greater_than_filters_numeric_values_and_rejects_non_numeric` | Passed | `test/integration/artifacts/gaussdb-distributed-current-20260812-141912.xml` | Covered |
| TC-MGF-804 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_804_greater_than_normalizes_date_prefix_across_precisions` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_804_greater_than_normalizes_date_prefix_across_precisions` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-805 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_805_empty_and_not_empty_distinguish_all_five_empty_states` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_805_empty_and_not_empty_distinguish_all_five_empty_states` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-806 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_806_not_in_matches_null_and_empty_string_but_not_missing_or_members` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_806_not_in_matches_null_and_empty_string_but_not_missing_or_members` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-808 | Both | `test/integration/test_gaussdb_metadata_flow.py::test_tc_mgf_808_metadata_filter_pushdown_full_chain` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_flow.py::test_tc_mgf_808_metadata_filter_pushdown_full_chain` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
| TC-MGF-809 | Both | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_809_limit_plus_one_probe_returns_none_or_complete_result` | Passed | `test/integration/artifacts/gaussdb-centralized-latest.xml` | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_809_limit_plus_one_probe_returns_none_or_complete_result` | Passed | `test/integration/artifacts/gaussdb-distributed-latest.xml` | Covered |
<!-- GAUSSDB-DISTRIBUTED-INTEGRATION-MATRIX:END -->


## 概述

本测试方案覆盖 RAGFlow 文档 metadata 过滤器到 GaussDB JSONB SQL 谓词的翻译，对标 `metadata_es_filter.py` / `metadata_infinity_filter.py`。核心翻译代码为 `common/metadata_gaussdb_filter.py`，公开下推边界还覆盖 `api/db/services/doc_metadata_service.py` 与 `rag/utils/gaussdb_conn.py`，对应技术设计 3.3 节、3.3.6 测试与验收。

**环境标记**：每条用例「类型」带 `· 单元` / `· 集成`：
- `· 单元`：纯函数 / SQL 生成，无 DB（03 多数）—— 验证生成的 SQL 字符串与 params
- `· 集成`：需 `GAUSSDB_INTEGRATION=1` + 真实库（2xx 空值五态命中、8xx 集成命中）

**字段继承**：未单列 Setup/Cleanup 的用例严格继承 [README 的 S/A/A/C 契约](README.md#setup--action--assert--cleanup-继承契约)，不得自行选择其它 fixture 或清理方式。

**测试范围**：
- 14 个前端 operator 的 JSONB 翻译：`=`、`≠`、`>`、`≥`、`<`、`≤`、`contains`、`not contains`、`start with`、`end with`、`empty`、`not empty`、`in`、`not in`
- 空值五态：key missing、JSON null、JSON 空字符串、空数组、空对象
- 数据类型：数字（整数 / 负数 / 小数 / 字符串数字）、日期时间 ISO 前缀（5 种精度 / 6 种输入格式）
- 逻辑组合 AND / OR、单条 / 多条 / 空 filter
- 别名归一化（`is` / `!=` / `<>` / `>=` / `<=` / `is not` / `not is`）
- 异常输入与拒绝（非法 key / 非标量值 / 未知 op / `before`·`after`）
- helper 公开函数
- 集成层真实库命中验证（opt-in）

**涉及文件**：`common/metadata_gaussdb_filter.py`、`api/db/services/doc_metadata_service.py`、`rag/utils/gaussdb_conn.py`

**关键函数**：

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `build_gaussdb_filter` | `(filters: Sequence[dict], logic: str) -> tuple[str, list]` | 主入口，返回 SQL + 绑定参数 |
| `plan_pushdown` | `(filters, logic) -> GaussDBFilterPlan` | 翻译并组装 plan |
| `GaussDBMetaFilterTranslator.translate` | `(flt: dict) -> TranslatedGaussDBMetaFilter` | 单条 filter 翻译 |
| `is_pushdown_supported` | `(filters) -> bool` | 是否可下推 |
| `fetch_gaussdb_metadata_doc_ids` | `(doc_store, index_name, kb_ids, sql_filter, filter_params, limit) -> list[str]` | 委托 doc store 查询 metadata doc_id |
| `normalize_gaussdb_meta_operator` | `(op) -> str` | op 归一化为标准符号 |
| `coerce_scalar_value` / `coerce_range_value` / `coerce_string_value` / `coerce_membership_values` | `(value, flt) -> Any` | 值强转 |
| `normalize_datetime_prefix` | `(text) -> str \| None` | ISO 前缀补全为完整时间 |
| `escape_like_pattern` / `jsonb_param` | `(value) -> str` | LIKE 转义 / JSON 序列化 |

**默认 JSONB 列**：`meta_fields`（未特别说明时 `jsonb_column="meta_fields"`）。

**路径表达式约定**：
- value 表达式：`meta_fields #> '{key}'`
- text 表达式：`meta_fields #>> '{key}'`
- key 存在（单段）：`(meta_fields #> '{key}') IS NOT NULL`
- key 存在（多段 `a.b`）：`(meta_fields #> '{a,b}') IS NOT NULL`

**空值五态语义（GaussDB 特有，必须显式测试）**：

| 态 | JSONB 存储示例 | `empty` 命中 | `not_empty` 命中 | `= value` | `≠ value` |
| --- | --- | --- | --- | --- | --- |
| key missing | 无该 key | ✅ | ❌ | ❌ | ❌（`key_exists` 为 false） |
| JSON null | `"k": null` | ✅ | ❌ | 仅 `= null` 命中 | ✅（`(<eq>) IS NOT TRUE`） |
| JSON 空串 | `"k": ""` | ✅ | ❌ | 仅 `= ""` 命中 | ✅（`(<eq>) IS NOT TRUE`） |
| 空数组 | `"k": []` | ✅ | ❌ | — | — |
| 空对象 | `"k": {}` | ✅ | ❌ | — | — |

> ⚠️ GaussDB `empty` / `≠` 语义与 Python 内存 fallback、Infinity 在 missing/null 情况下有差异：当前 SQL 中 `≠` / `not in` 不召回 key missing，但会召回已存在 key 的 JSON null 与 JSON 空串。该当前实现语义必须在五态用例中显式断言；若技术设计要求不同，标记设计/实现不一致。

**异常类型**：所有非法输入统一抛 `UnsupportedGaussDBMetaFilter`（`ValueError` 子类）。

**pushdown 返回语义**：
- `None` → 触发内存 fallback
- `[]` → 已下推但无匹配
- 非空 list → 命中 doc_id

**环境连接**：单元层无需 DB；集成层（2xx、8xx）见 [README 集成测试环境连接](README.md#集成测试环境连接)（`GAUSSDB_INTEGRATION=1` + `GAUSSDB_SCHEMA` 配置值）。

**调用约定（单元层）**：
```python
from common.metadata_gaussdb_filter import build_gaussdb_filter
sql, params = build_gaussdb_filter(filters, logic)
# 断言 sql 包含特定片段，params 为特定列表
```

**集成用例 pytest fixture/helper（TC-MGF-203-209、801-806、809 共享）**：

`metadata_scope` 依赖 `ragflow_kb_context` 创建真实 Knowledgebase，并用其真实 `tenant_id` 通过生产 `DocMetadataService._get_doc_meta_index_name()` 派生 `ragflow_doc_meta_<tenant>` 表名。fixture 使用独立 `gaussdb_admin_conn` 创建 schema-qualified 表 `(id, kb_id, meta_fields)` 及 `kb_id` 索引，同时把当前分支真实 `GaussDBConnection` 放入 scope。所有测试数据都带真实 `kb_id`。

`_insert(metadata_scope, rows)` 使用独立管理连接执行参数化 `INSERT ... %s::jsonb`，提交后再以独立 SQL 回读 `id/kb_id/meta_fields` 并与固定输入逐项比较。`_assert_filter_contract()` 对生产 translator 的固定 SQL/params 作结构 oracle；`_match()` 则通过 `DocMetadataService.filter_doc_ids_by_meta_pushdown()` 进入真实 service → adapter → GaussDB 路径，业务 expected 来自各 TC 的固定输入，不从 adapter actual 反推。

fixture 的 `finally` 先 rollback，再执行 schema-qualified `DROP TABLE ... CASCADE`，最后独立查询 `information_schema.tables` 并断言残留为 0；`ragflow_kb_context` 另行清理本 TC 创建的 Knowledgebase。典型主函数形态与当前代码一致：

```python
def test_tc_mgf_xxx(..., metadata_scope):
    _insert(metadata_scope, [{"id": "fixed-id", "meta_fields": {"status": "active"}}])
    filters = [{"key": "status", "op": "=", "value": "active"}]
    _assert_filter_contract(filters, EXPECTED_SQL, EXPECTED_PARAMS)
    assert _match(metadata_scope, filters) == ["fixed-id"]
```

---

## 一、基础入口与 plan 组装（0xx）

### TC-MGF-002: 多条 AND 拼接

**类型**：组合 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":"active"},{"key":"amount","op":">","value":100}], "and")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) AND (CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"`
- `params == ["active", "active", 100]`
- AND 用 ` AND ` 连接，每段 filter 各加外层括号
- params 按 filter 顺序拼接、不交叉
- **SQL 文本不含用户值 "active"、"100"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「逻辑组合 AND / OR 括号边界正确」

**优先级**：P0

### TC-MGF-003: 多条 OR 拼接

**类型**：组合 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":"a"},{"key":"status","op":"=","value":"b"}], "or")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) OR (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))"`
- `params == ["a", "a", "b", "b"]`
- OR 用 ` OR ` 连接，每段加外层括号
- **SQL 文本不含用户值 "a"、"b"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「逻辑组合」

**优先级**：P0

### TC-MGF-004: 空 filter 列表返回 1=1

**类型**：边界 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([], "and")`

**预期结果**：
- `sql == "1=1"`
- `params == []`
- 空 filter 不生成任何 JSONB 表达式，`to_predicate()` 返回恒真谓词 `1=1`（区别于 `None` 触发内存 fallback、`[]` 表示已下推无匹配）

**验收口径**：3.3.6「pushdown 返回语义」

**优先级**：P0

### TC-MGF-005: 嵌套 key 路径生成

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"profile.name","op":"=","value":"Alice"}], "and")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{profile,name}') = %s OR jsonb_exists(meta_fields #> '{profile,name}', %s))"`
- `params == ["alice", "alice"]`
- `jsonb_path_literal("profile.name")` 生成 `'{profile,name}'`（点分段转逗号）
- text / value 表达式均用 `#>>` / `#>` 与该 path
- **SQL 文本不含用户值 "Alice" 或 "alice"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「JSONB path 默认生成 `#>` / `#>>` 并覆盖特殊 key 编码」

**优先级**：P0

### TC-MGF-006: 自定义 jsonb_column

**类型**：正向 · 单元 · P1

**前置条件**：自定义 `jsonb_column="chunk_data"`

**步骤**：
1. `t = GaussDBMetaFilterTranslator(jsonb_column="chunk_data")`
2. `translated = t.translate({"key":"amount","op":"=","value":5})`

**预期结果**：
- `translated.sql == "chunk_data #> '{amount}' @> %s::jsonb"`（直接调用 `translate()` 返回单条裸谓词，不加外层括号）
- `translated.params == ["5"]`（jsonb_param(5) = json.dumps(5) = "5"，字符串类型）
- 如改用 `GaussDBFilterPlan.to_predicate()` 或 `build_gaussdb_filter()`，才断言外层包装为 `(chunk_data #> '{amount}' @> %s::jsonb)`
- 所有路径表达式用 `chunk_data` 而非默认 `meta_fields`
- 非法列名拒绝由 TC-MGF-616 独立验证，本用例只覆盖自定义合法列名的翻译结果

**验收口径**：3.3.6「JSONB path 默认生成 `#>` / `#>>`」

**优先级**：P1

---

## 二、operator 翻译（1xx）

### TC-MGF-101: `=` 字符串值

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":"active"}], "and")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))"`
- `params == ["active", "active"]`
- 字符串值先 `lower()`，等值走 `lower(text) = %s`，同时用 `jsonb_exists` 命中数组元素

**验收口径**：3.3.6「JSONB 条件翻译」

**优先级**：P0

### TC-MGF-102: `≠` 字符串值（key missing 不命中，null / 空串命中）

**类型**：反向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"≠","value":"active"}], "and")`

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"`
- `params == ["active", "active"]`
- `≠` 生成 `<key_exists> AND (<eq>) IS NOT TRUE`，再由 `build_gaussdb_filter()` 包外层括号；key missing 不命中，已存在 key 的 JSON null / JSON 空串因 `(<eq>) IS NOT TRUE` 命中
- **SQL 文本不含用户值 "active"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「equals / not equals 覆盖 JSON null 和 JSON 空字符串」

**优先级**：P0

### TC-MGF-103: `>` 数字

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"amount","op":">","value":100}], "and")`

**预期结果**：
- `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"`
- `params == [100]`
- 只有 `CASE WHEN` 正则分支命中时才执行 `::DOUBLE PRECISION`；非数字行走 `ELSE FALSE`，不得依赖优化器对 `AND` 两侧的求值顺序来保护 cast
- **SQL 文本不含用户值 "100"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-104: `≥` / `<` / `≤` 数字

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别调用 `build_gaussdb_filter([{"key":"amount","op":"≥","value":50}], "and")`
2. `build_gaussdb_filter([{"key":"amount","op":"<","value":50}], "and")`
3. `build_gaussdb_filter([{"key":"amount","op":"≤","value":50}], "and")`

**预期结果**：
- `≥` → `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION >= %s ELSE FALSE END)"`，`params == [50]`
- `<` → `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION < %s ELSE FALSE END)"`，`params == [50]`
- `≤` → `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION <= %s ELSE FALSE END)"`，`params == [50]`
- **SQL 文本不含用户值 "50"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「range 数字比较」

**优先级**：P1

### TC-MGF-105: `>` 日期 ISO 前缀

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"dt","op":">","value":"2026-07"}], "and")`

**预期结果**：
- `sql == "(CASE WHEN meta_fields #>> '{dt}' ~ '^[0-9]{4}-[0-9]{2}$' THEN to_timestamp(meta_fields #>> '{dt}' || '-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS') WHEN meta_fields #>> '{dt}' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN to_timestamp(meta_fields #>> '{dt}' || ' 00:00:00', 'YYYY-MM-DD HH24:MI:SS') WHEN meta_fields #>> '{dt}' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}$' THEN to_timestamp(replace(meta_fields #>> '{dt}', 'T', ' ') || ':00:00', 'YYYY-MM-DD HH24:MI:SS') WHEN meta_fields #>> '{dt}' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}$' THEN to_timestamp(replace(meta_fields #>> '{dt}', 'T', ' ') || ':00', 'YYYY-MM-DD HH24:MI:SS') WHEN meta_fields #>> '{dt}' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}$' THEN to_timestamp(replace(meta_fields #>> '{dt}', 'T', ' '), 'YYYY-MM-DD HH24:MI:SS') END > to_timestamp(%s, 'YYYY-MM-DD HH24:MI:SS'))"`
- `params == ["2026-07-01 00:00:00"]`
- `normalize_datetime_prefix("2026-07")` 补全为 `2026-07-01 00:00:00`
- 字段侧 CASE 覆盖 5 种精度（YYYY-MM / YYYY-MM-DD / +HH / +HH:MM / +HH:MM:SS），`T` 与空格分隔均经 `replace(meta_fields #>> '{dt}', 'T', ' ')` 归一化
- 用户值 `"2026-07"` 不出现在 SQL 文本，只以补全后的 `2026-07-01 00:00:00` 进 params

**验收口径**：3.3.6「日期时间 ISO 前缀归一化」

**优先级**：P0

### TC-MGF-106: `contains` 字符串

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"cust","op":"contains","value":"Acme"}], "and")`

**预期结果**：
- `sql == "(jsonb_exists(meta_fields #> '{cust}', %s) OR lower(meta_fields #>> '{cust}') LIKE %s ESCAPE '\\')"`
- `params == ["acme", "%acme%"]`
- 大小写不敏感，同时命中数组元素（`jsonb_exists`）与子串（`LIKE`）
- **SQL 文本不含用户值 "Acme" 或 "acme"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-107: `not contains`（key missing 不命中，null / 空串命中）

**类型**：反向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"cust","op":"not contains","value":"Acme"}], "and")`

**预期结果**：
- `sql == "((meta_fields #> '{cust}') IS NOT NULL AND (jsonb_exists(meta_fields #> '{cust}', %s) OR lower(meta_fields #>> '{cust}') LIKE %s ESCAPE '\\') IS NOT TRUE)"`
- `params == ["acme", "%acme%"]`
- `not contains` 复用 `contains` 的正向 SQL，先外包 `<key_exists> AND (<contains_sql>) IS NOT TRUE`，再由 `build_gaussdb_filter()` 包外层括号
- **SQL 文本不含用户值 "Acme" 或 "acme"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-108: `start with`

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"cust","op":"start with","value":"Acme"}], "and")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{cust}') LIKE %s ESCAPE '\\')"`
- `params == ["acme%"]`
- **SQL 文本不含用户值 "Acme" 或 "acme"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-109: `end with`

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"cust","op":"end with","value":"Corp"}], "and")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{cust}') LIKE %s ESCAPE '\\')"`
- `params == ["%corp"]`
- **SQL 文本不含用户值 "Corp" 或 "corp"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-110: `in` 多成员

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"in","value":["a","b"]}], "and")`

**预期结果**：
- `sql == "((lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) OR (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)))"`
- `params == ["a", "a", "b", "b"]`
- 每个 member 走 `_equal_predicate` 并各自包成 `(<eq>)`，源码整体拼为 `((<eq_a>) OR (<eq_b>))`
- **SQL 文本不含用户值 "a" 或 "b"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-111: `not in` 多成员（key missing 不命中，null / 空串命中）

**类型**：反向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"not in","value":["a","b"]}], "and")`

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"`
- `params == ["a", "a", "b", "b"]`
- `not in` 对每个 member 生成 `(<eq>) IS NOT TRUE`，并要求 key 存在；key missing 不命中，JSON null 与 JSON 空串都会命中
- **SQL 文本不含用户值 "a" 或 "b"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-112: `empty`

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"empty","value":None}], "and")`
2. 调用 `build_gaussdb_filter([{"key":"vendor.name","op":"empty","value":None}], "and")`，验证嵌套 parent missing 语义

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NULL OR meta_fields #> '{status}' = 'null'::jsonb OR meta_fields #> '{status}' = '\"\"'::jsonb OR meta_fields #> '{status}' = '[]'::jsonb OR meta_fields #> '{status}' = '{}'::jsonb)"`
- `params == []`
- 覆盖 missing + null + 空串 + 空数组 + 空对象五种空态
- 嵌套 key 的 `sql == "((meta_fields #> '{vendor,name}') IS NULL OR meta_fields #> '{vendor,name}' = 'null'::jsonb OR meta_fields #> '{vendor,name}' = '\"\"'::jsonb OR meta_fields #> '{vendor,name}' = '[]'::jsonb OR meta_fields #> '{vendor,name}' = '{}'::jsonb)"`，`params == []`；父路径或叶子缺失时 `#>` 返回 SQL NULL
- **SQL 文本不含裸 `= ''` 或 `<> ''`**，空串等值走 `= '""'::jsonb`（方言安全）

**优先级**：P0

### TC-MGF-113: `not empty`

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"not empty","value":None}], "and")`

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (meta_fields #> '{status}' = 'null'::jsonb) IS NOT TRUE AND (meta_fields #> '{status}' = '\"\"'::jsonb) IS NOT TRUE AND (meta_fields #> '{status}' = '[]'::jsonb) IS NOT TRUE AND (meta_fields #> '{status}' = '{}'::jsonb) IS NOT TRUE)"`
- `params == []`
- **SQL 文本不含裸 `= ''` 或 `<> ''`**，空串等值走 `= '""'::jsonb`（方言安全）

**优先级**：P0

---

## 三、空值五态（2xx）

> 201/202 单元层验证 SQL 片段；203-209 集成层验证命中（需真实库，`GAUSSDB_INTEGRATION=1`）。

### TC-MGF-201: `=` value=None 生成 JSON null 等值

**类型**：边界 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":None}], "and")`

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND meta_fields #> '{status}' = 'null'::jsonb)"`
- `params == []`
- `coerce_scalar_value(None)` 返回 None，走 JSON null 等值分支

**优先级**：P0

### TC-MGF-202: `=` value="" 生成 JSON 空串等值

**类型**：边界 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":""}], "and")`

**预期结果**：
- `sql == "(meta_fields #> '{status}' = '\"\"'::jsonb)"`
- `params == []`
- 空串走 `= '""'::jsonb`，**不**生成 `= ''`（方言安全）

**优先级**：P0

## 本轮真实链路实现映射

目标集成用例已落地在 `test/integration/test_gaussdb_metadata_filter_coverage.py`。每个测试通过 `ragflow_kb_context` 创建真实 `Knowledgebase`，由 `metadata_scope` 使用真实 tenant 派生的 `ragflow_doc_meta_<tenant>` 表；表和种子数据由独立 `gaussdb_admin_conn` 通过参数化 SQL 创建、写入并回读确认，Act/Assert 则通过 `DocMetadataService.filter_doc_ids_by_meta_pushdown` 进入真实 metadata pushdown 生产链路。

本轮真实集成映射不使用 fake Knowledgebase、fake cursor，也不使用被测 adapter 写入或回读 expected。对汇总表中环境为“集成”的 TC，以真实 Knowledgebase、真实 metadata 表、固定 SQL/params 契约、真实 service pushdown、真实返回值和独立数据库状态为自动化验收口径。fixture finally 通过管理连接执行 schema-qualified `DROP TABLE ... CASCADE`，随后独立查询 `information_schema.tables` 断言表已不存在；`gaussdb_env` 负责兜底清理已登记的表。

跨 KB 复用同一 `Document.id` 属于上游全局唯一约束说明，不作为 metadata adapter 的跨 KB 并存能力或 schema bug 验收；不再使用 strict xfail 记录 `BUG-GAUSSDB-IT-002`。TC-MGF-809 包含 5 条真实匹配记录，使用真实 adapter 的 `limit+1` probe 覆盖 `>`、`==`、`<` 三态，并断言超限返回 `None`、其余返回完整结果。

**输入契约说明**：GaussDB metadata DDL 保持 `PRIMARY KEY (id)`，写入路径的同 KB upsert 继续使用 `ON DUPLICATE KEY UPDATE meta_fields = VALUES(meta_fields)`。跨 KB 复用同一 `Document.id` 是上游输入冲突，不要求 metadata 表为其保留第二条记录；本轮不修改生产代码，也不将该非法输入场景计为 adapter 开发缺陷。

### TC-MGF-203: `empty` 命中 key missing（集成）

**类型**：边界 · 集成 · P0

**前置条件**：`GAUSSDB_INTEGRATION=1`；`metadata_scope` 已创建真实 KB、真实 tenant metadata 表和 `kb_id` 索引；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "missing", "meta_fields": {"other": 1}},
    {"id": "ready", "meta_fields": {"status": "ready"}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"status","op":"empty"}], "and")` 取 `(sql, params)`
2. 调真实 `DocMetadataService.filter_doc_ids_by_meta_pushdown()`（由 `_match` 封装）

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NULL OR meta_fields #> '{status}' = 'null'::jsonb OR meta_fields #> '{status}' = '\"\"'::jsonb OR meta_fields #> '{status}' = '[]'::jsonb OR meta_fields #> '{status}' = '{}'::jsonb)"`，`params == []`
- 返回 `["missing"]`；`ready` 不命中，证明不是无条件返回

**清理**：`metadata_scope` finally 删除真实 metadata 表并独立断言 catalog 残留为 0；`ragflow_kb_context` 清理 KB

**验收口径**：3.3.6「empty 覆盖 key missing」

**优先级**：P0

### TC-MGF-204: `empty` 命中 JSON null（集成）

**类型**：边界 · 集成 · P0

**前置条件**：同 TC-MGF-203；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "json-null", "meta_fields": {"status": None}},
    {"id": "active", "meta_fields": {"status": "active"}},
])
```

**步骤**：
1. 分别调 `build_gaussdb_filter` 生成 `empty` 与 `not empty` 的 `(sql, params)`
2. 逐条通过 `_match` 调真实 service pushdown

**预期结果**：
- `empty` 返回 `["json-null"]`
- `not empty` 返回 `["active"]`
- `empty` SQL == `((meta_fields #> '{status}') IS NULL OR meta_fields #> '{status}' = 'null'::jsonb OR meta_fields #> '{status}' = '""'::jsonb OR meta_fields #> '{status}' = '[]'::jsonb OR meta_fields #> '{status}' = '{}'::jsonb)`，`params == []`

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「empty 命中 JSON null」

**优先级**：P0

### TC-MGF-205: `empty` 命中 JSON 空串（集成）

**类型**：边界 · 集成 · P0

**前置条件**：同 TC-MGF-203；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "empty-string", "meta_fields": {"status": ""}},
    {"id": "active", "meta_fields": {"status": "active"}},
])
```

**步骤**：
1. 分别调生产 translator 生成 `empty` / `not empty` / `= ""` / `≠ "active"` 四个固定 SQL/params 契约
2. 逐条通过 `_match` 调真实 service pushdown

**预期结果**：
- `empty` 与 `= ""` 均返回 `["empty-string"]`
- `≠ "active"` 返回 `["empty-string"]`
- `not empty` 返回 `["active"]`
- 四条 SQL 文本均不含 `= ''` 或 `<> ''`

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「empty / not_empty / = 空串 / ≠ 空串命中」

**优先级**：P0

### TC-MGF-206: `empty` 命中空数组（集成）

**类型**：边界 · 集成 · P0

**前置条件**：同 TC-MGF-203；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "empty-array", "meta_fields": {"tags": []}},
    {"id": "tagged", "meta_fields": {"tags": ["audit"]}},
])
```

**步骤**：
1. 分别调 `build_gaussdb_filter` 生成 `empty` 与 `not empty` 的 `(sql, params)`
2. 逐条通过 `_match` 调真实 service pushdown

**预期结果**：
- `empty` 返回 `["empty-array"]`
- `not empty` 返回 `["tagged"]`

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「empty 命中空数组」

**优先级**：P0

### TC-MGF-207: `empty` 命中空对象（集成）

**类型**：边界 · 集成 · P0

**前置条件**：同 TC-MGF-203；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "empty-object", "meta_fields": {"profile": {}}},
    {"id": "profiled", "meta_fields": {"profile": {"name": "Alice"}}},
])
```

**步骤**：
1. 分别调 `build_gaussdb_filter` 生成 `empty` 与 `not empty` 的 `(sql, params)`
2. 逐条通过 `_match` 调真实 service pushdown

**预期结果**：
- `empty` 返回 `["empty-object"]`
- `not empty` 返回 `["profiled"]`

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「empty 命中空对象」

**优先级**：P0

### TC-MGF-208: `≠ value` 命中 JSON null / 空串（集成）

**类型**：反向 · 集成 · P0

**前置条件**：同 TC-MGF-203；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "json-null", "meta_fields": {"status": None}},
    {"id": "empty-string", "meta_fields": {"status": ""}},
    {"id": "active", "meta_fields": {"status": "active"}},
    {"id": "missing", "meta_fields": {"other": 1}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"status","op":"≠","value":"active"}], "and")` 取 `(sql, params)`
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"`，`params == ["active", "active"]`
- 返回 `["empty-string", "json-null"]`
- `active` 与 key missing 的 `missing` 均不命中

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「`field ≠ value` 对 key missing、JSON null、JSON 空字符串的当前 SQL 语义」

**优先级**：P0

### TC-MGF-209: `not empty` 不命中任何空态（集成）

**类型**：反向 · 集成 · P0

**前置条件**：同 TC-MGF-203；独立管理 SQL 写入并回读五种空态与一条非空记录：
```python
_insert(metadata_scope, [
    {"id": "missing", "meta_fields": {"other": 1}},
    {"id": "json-null", "meta_fields": {"status": None}},
    {"id": "empty-string", "meta_fields": {"status": ""}},
    {"id": "empty-array", "meta_fields": {"status": []}},
    {"id": "empty-object", "meta_fields": {"status": {}}},
    {"id": "active", "meta_fields": {"status": "active"}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"status","op":"not empty"}], "and")` 取 `(sql, params)`
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- 返回 `["active"]`；五空态行均不命中
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (meta_fields #> '{status}' = 'null'::jsonb) IS NOT TRUE AND (meta_fields #> '{status}' = '\"\"'::jsonb) IS NOT TRUE AND (meta_fields #> '{status}' = '[]'::jsonb) IS NOT TRUE AND (meta_fields #> '{status}' = '{}'::jsonb) IS NOT TRUE)"`，`params == []`

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「not empty 不命中五空态」

**优先级**：P0

---

## 四、数据类型（3xx）

### TC-MGF-301: `=` 整数 value 走 @> 包含

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"amount","op":"=","value":5}], "and")`

**预期结果**：
- `sql == "(meta_fields #> '{amount}' @> %s::jsonb)"`
- `params == ["5"]`（jsonb_param(5) = json.dumps(5) = "5"，字符串类型）
- `coerce_scalar_value(5)` 返回 5，`jsonb_param(5)="5"`，走 `@>` 包含
- **SQL 文本不含用户值 "5"**，用户值只出现在 params（参数化防注入）

**优先级**：P0

### TC-MGF-302: `=` 布尔 value

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"flag","op":"=","value":True}], "and")`

**预期结果**：
- `sql == "(meta_fields #> '{flag}' @> %s::jsonb)"`
- `params == ["true"]`
- **SQL 文本不含用户值 "true" 或 "True"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-303: `=` 字符串数字 `"123"` 强转为 int

**类型**：边界 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"n","op":"=","value":"123"}], "and")`

**预期结果**：
- `sql == "(meta_fields #> '{n}' @> %s::jsonb)"`
- `params == ["123"]`
- `coerce_scalar_value("123")` 经 `ast.literal_eval` 解析为 int 123
- **SQL 文本不含用户值 "123"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-304: Python literal 字符串与小写 JSON 单词分流

**类型**：边界 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别以 `"True"`、`"False"`、`"None"`、`"true"`、`"null"` 调用公开 `build_gaussdb_filter`
2. 断言完整 SQL/params，并直接验证公开 coercion 结果

**预期结果**：
- `"True"` / `"False"` 分别按 Python bool 处理，绑定 JSONB 参数 `"true"` / `"false"`
- `"None"` 按 JSON null 处理：先以 `(meta_fields #> '{n}') IS NOT NULL` 区分缺 key，再比较固定 `null` JSONB literal
- 小写 `"true"` / `"null"` 保持普通字符串语义，使用参数化大小写不敏感文本/数组等值分支
- 用户文本不得内联进 SQL；`'null'::jsonb` 是方案规定的固定 literal

**验收口径**：3.3.6「字符串强转 int/float/bool/None」

**优先级**：P1

### TC-MGF-305: `>` 负数

**类型**：边界 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"amount","op":">","value":-10}], "and")`

**预期结果**：
- `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"`
- `params == [-10]`
- 正则含负号 `^-?[0-9]+(\\.[0-9]+)?$`，覆盖负数文本匹配
- **SQL 文本不含用户值 "-10"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「range 数字负数支持」

**优先级**：P1

### TC-MGF-306: `>` 小数

**类型**：边界 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"amount","op":">","value":1.5}], "and")`

**预期结果**：
- `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"`
- `params == [1.5]`
- **SQL 文本不含用户值 "1.5"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-307: `>` 字符串数字 `"100"`

**类型**：边界 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"amount","op":">","value":"100"}], "and")`
2. 用 `value="2026"` 重复调用，验证四位数字字符串不误判为年份 datetime
3. 用前导零整数 `value="01"` 和小数 `value="01.5"` 重复调用，验证 Python literal 解析失败时仍走受控数字正则分支

**预期结果**：
- `coerce_range_value("100")` 返回 `("number", 100)`
- 各次调用的 `sql` 均等于 `"(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"`
- `"100"` → `params == [100]`；`coerce_range_value("100") == ("number", 100)`
- `"2026"` → `params == [2026]`；`coerce_range_value("2026") == ("number", 2026)`
- `"01"` / `"01.5"` 分别得到 `[1]` / `[1.5]`，仍使用数字 range SQL
- 两条 SQL 均不含 `to_timestamp`，且不含原始用户值；四位数字保持数字 range 语义

**优先级**：P1

### TC-MGF-308: 日期 ISO 前缀 5 种精度 / 6 种输入格式

**类型**：边界 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别用 `value` = `2026-07` / `2026-07-08` / `2026-07-08T10` / `2026-07-08 10` / `2026-07-08T10:30` / `2026-07-08 10:30:45`，`op=">"`

**预期结果**：
- 6 种均归类为 `datetime`，SQL 含 `to_timestamp(%s, 'YYYY-MM-DD HH24:MI:SS')`
- `params` 分别为 `2026-07-01 00:00:00` / `2026-07-08 00:00:00` / `2026-07-08 10:00:00` / `2026-07-08 10:00:00` / `2026-07-08 10:30:00` / `2026-07-08 10:30:45`

**优先级**：P0

### TC-MGF-309: 非法日期 `2026-13-01` 不当作 datetime

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"dt","op":">","value":"2026-13-01"}], "and")`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，`reason` 稳定表示 range value 既不是合法数字也不是合法日期
- 异常文本不得包含原始值 `2026-13-01`，避免把输入数据写入日志
- 不以内部 `_NUMBER_RE`、`literal_eval` 调用顺序作为验收依据

**优先级**：P1

### TC-MGF-310: `in` 字符串逗号分隔

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"in","value":"a,b,c"}], "and")`

**预期结果**：
- `coerce_membership_values("a,b,c")` 按逗号拆分为 `["a","b","c"]`
- SQL 为三段 `_equal_predicate` OR 连接
- `params == ["a","a","b","b","c","c"]`
- **SQL 文本不含用户值 "a"、"b"、"c"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-311: `in` JSON 数组字符串 `"[1,2]"`

**类型**：边界 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"n","op":"in","value":"[1,2]"}], "and")`

**预期结果**：
- `literal_eval("[1,2]")` → `[1,2]`，members = `[1,2]`
- SQL 为两段 `@> %s::jsonb` OR 连接
- `params == ["1", "2"]`
- **SQL 文本不含用户值 "1"、"2"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-312: `=` 字符串小数 `"1.5"` 强转为 float

**类型**：边界 · 单元 · P1

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"n","op":"=","value":"1.5"}], "and")`

**预期结果**：
- 字符串小数经 scalar 强转路径解析为 float `1.5`，不走字符串大小写比较分支
- `sql == "(meta_fields #> '{n}' @> %s::jsonb)"`
- `params == ["1.5"]`
- SQL 文本不含用户值 `1.5`

**优先级**：P1

### TC-MGF-313: `in` 接受单个 scalar member

**类型**：边界 · 单元 · P1

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"n","op":"in","value":7}], "and")`

**预期结果**：
- 单个 scalar 被归一化为单成员列表，不要求调用方预先包装 list
- `sql == "((meta_fields #> '{n}' @> %s::jsonb))"`
- `params == ["7"]`
- SQL 只含一个 `@> %s::jsonb` predicate，且不含用户值 `7`

**优先级**：P1

---

## 五、逻辑组合（4xx）

### TC-MGF-401: AND 三条混合 operator

**类型**：组合 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":"active"},{"key":"amount","op":">","value":100},{"key":"cust","op":"contains","value":"acme"}], "and")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) AND (CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END) AND (jsonb_exists(meta_fields #> '{cust}', %s) OR lower(meta_fields #>> '{cust}') LIKE %s ESCAPE '\\')"`
- `params == ["active", "active", 100, "acme", "%acme%"]`
- 三段各加外层括号，用 ` AND ` 连接
- params 按 filter 顺序拼接、不交叉（eq 两个、range 一个、contains 两个）
- **SQL 文本不含用户值 "active"、"100"、"acme"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「逻辑组合 AND 括号边界 + params 顺序」

**优先级**：P0

### TC-MGF-402: OR 三条

**类型**：组合 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"=","value":"a"},{"key":"status","op":"=","value":"b"},{"key":"status","op":"=","value":"c"}], "or")`

**预期结果**：
- `sql == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) OR (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) OR (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))"`
- `params == ["a", "a", "b", "b", "c", "c"]`
- 三段各加外层括号，用 ` OR ` 连接
- params 按 filter 顺序拼接，每段 eq 占两个位置
- **SQL 文本不含用户值 "a"、"b"、"c"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「逻辑组合 OR 括号边界 + params 顺序」

**优先级**：P1

### TC-MGF-403: AND 包含 not in（反向 + 正向混合）

**类型**：组合 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"not in","value":["a","b"]},{"key":"type","op":"=","value":"doc"}], "and")`

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE) AND (lower(meta_fields #>> '{type}') = %s OR jsonb_exists(meta_fields #> '{type}', %s))"`
- `params == ["a", "a", "b", "b", "doc", "doc"]`
- 两段各加外层括号，用 ` AND ` 连接
- not_in 段含 `<key_exists> AND (<eq_sql>) IS NOT TRUE` 结构
- params 按 filter 顺序拼接，not_in 占四个位置、eq 占两个
- **SQL 文本不含用户值 "a"、"b"、"doc"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「逻辑组合 AND + not_in IS NOT TRUE 语义」

**优先级**：P1

### TC-MGF-404: 嵌套 key 在 AND 中的 key_exists 多段

**类型**：组合 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `{"key":"a.b","op":"≠","value":"x"}` 与 `{"key":"a.c","op":"=","value":1}`，`logic="and"`

**预期结果**：
- `sql == "((meta_fields #> '{a,b}') IS NOT NULL AND (lower(meta_fields #>> '{a,b}') = %s OR jsonb_exists(meta_fields #> '{a,b}', %s)) IS NOT TRUE) AND (meta_fields #> '{a,c}' @> %s::jsonb)"`
- `params == ["x", "x", "1"]`（jsonb_param(1) = "1"，字符串类型）
- not_equal 段的 key_exists 为 `(meta_fields #> '{a,b}') IS NOT NULL`；不依赖源码行号定位预期
- eq 段的 value_expr 为 `meta_fields #> '{a,c}'`，`@>` 分支 params 只有 1 个占位
- `jsonb_path_literal("a.c") == "'{a,c}'"`（点分段转逗号）
- **SQL 文本不含用户值 "x"、"1"**，用户值只出现在 params（参数化防注入）
- **嵌套 key 经 `jsonb_path_literal` 正确编码**（点分段转逗号，含逗号 key 用花括号包裹）

**优先级**：P1

### TC-MGF-405: 含逗号、引号与 SQL 元字符的 key 安全编码

**类型**：安全 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 以 key `root.a,b.owner') OR TRUE --` 调用 `build_gaussdb_filter`，operator 为 `≠`
2. 直接验证 `jsonb_path_literal` 的 canonical path literal

**预期结果**：
- 生成的 JSONB path 对逗号段使用双引号包裹，对 SQL 单引号加倍
- SQL/params 精确符合 `≠` 契约，用户值 `x` 只出现在 params
- 恶意 key 只能作为静态 JSONB path 数据出现，不能闭合 SQL literal 或形成可执行的 `OR TRUE`

**验收口径**：3.3.6「任意非空 metadata key segment 由 JSONB path encoder 安全编码」

**优先级**：P1

---

## 六、别名归一化（5xx）

### TC-MGF-501: `is` → `=`

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"is","value":"active"}], "and")`

**预期结果**：
- 等价于 TC-MGF-101：`sql` 含 `lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)`，`params == ["active","active"]`
- **SQL 文本不含用户值 "active"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-502: `is not` / `not is` / `!=` / `<>` → `≠`

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别用四种 op，`value="active"`

**预期结果**：
- 四者均等价于 TC-MGF-102：`sql` 含 `<key_exists> AND (<eq>) IS NOT TRUE`，`params == ["active","active"]`
- **SQL 文本不含用户值 "active"**，用户值只出现在 params（参数化防注入）

**优先级**：P1

### TC-MGF-503: `>=` / `<=` → `≥` / `≤`

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"amount","op":">=","value":50}], "and")`
2. 调用 `build_gaussdb_filter([{"key":"amount","op":"<=","value":50}], "and")`

**预期结果**：
- `>=` → `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION >= %s ELSE FALSE END)"`，`params == [50]`
- `<=` → `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION <= %s ELSE FALSE END)"`，`params == [50]`
- `normalize_gaussdb_meta_operator(">=")` → `"≥"`，`normalize_gaussdb_meta_operator("<=")` → `"≤"`
- SQL 比较符为 `>=` / `<=`（由 `_INTERNAL_RANGE_SQL["gte"/"lte"]` 映射）
- **SQL 文本不含用户值 "50"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「operator 别名归一化」

**优先级**：P1

### TC-MGF-504: 大小写 / 空白容错

**类型**：边界 · 单元 · P2

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"  IS  NOT ","value":"acme"}], "and")`
2. 调用 `build_gaussdb_filter([{"key":"status","op":"Contains","value":"acme"}], "and")`
3. 调用 `build_gaussdb_filter([{"key":"status","op":"START WITH","value":"acme"}], "and")`

**预期结果**：
- `"  IS  NOT "` → `sql == "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"`，`params == ["acme", "acme"]`（经 `strip().lower()` + 空白折叠后归一化为 `"is not"` → `"≠"`）
- `"Contains"` → `sql == "(jsonb_exists(meta_fields #> '{status}', %s) OR lower(meta_fields #>> '{status}') LIKE %s ESCAPE '\\')"`，`params == ["acme", "%acme%"]`（归一化为 `"contains"`）
- `"START WITH"` → `sql == "(lower(meta_fields #>> '{status}') LIKE %s ESCAPE '\\')"`，`params == ["acme%"]`（归一化为 `"start with"`）
- 不抛异常，与 P0 用例 102/106/108 等价
- **SQL 文本不含用户值 "acme" 或 "Acme"**，用户值只出现在 params（参数化防注入）

**验收口径**：3.3.6「operator 别名归一化 + strip().lower() 空白折叠」

**优先级**：P2

### TC-MGF-505: `before` / `after` 不支持

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别 `op="before"` / `"after"`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `unsupported metadata filter operator`
- `"before"` / `"after"` 不在 `SUPPORTED_OPERATORS`
- **统一异常类型 + 具体消息**，不抛其他异常类型（设计 3.3.3.3）

**优先级**：P1

---

## 七、异常输入与拒绝（6xx）

### TC-MGF-601: 缺 key

**类型**：异常 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"op":"=","value":"x"}], "and")`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `invalid metadata key`

**优先级**：P0

### TC-MGF-602: 缺 op

**类型**：异常 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","value":"x"}], "and")`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `metadata filter operator is missing`

**优先级**：P0

### TC-MGF-603: 未知 op

**类型**：异常 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `op="regex"`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `unsupported metadata filter operator 'regex'`

**优先级**：P0

### TC-MGF-604: 数字开头 key 段安全编码

**类型**：正向 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `key="1abc"`、`op="="`、`value=1`

**预期结果**：
- SQL 精确为 `(meta_fields #> '{1abc}' @> %s::jsonb)`，params 为 `["1"]`
- `jsonb_path_literal("1abc") == "'{1abc}'"`，不得因 key 不是 SQL identifier 而错误拒绝

**优先级**：P0

### TC-MGF-605: 非法 key（空段）

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别调用 `build_gaussdb_filter([{"key":"a..b","op":"=","value":"x"}], "and")`
2. `build_gaussdb_filter([{"key":"a.","op":"=","value":"x"}], "and")`
3. `build_gaussdb_filter([{"key":".a","op":"=","value":"x"}], "and")`

**预期结果**：
- 三者均抛 `UnsupportedGaussDBMetaFilter`
- 三种空段均以稳定 `reason` 拒绝；另覆盖含 NUL 的段并以独立稳定原因拒绝
- 异常文本不得回显原始 key

**验收口径**：3.3.6「key 段校验」

**优先级**：P1

### TC-MGF-606: 含空格 key 段安全编码

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `key="a.b c"`、`op="="`、`value=1`

**预期结果**：
- path literal 精确为 `'{a,"b c"}'`
- SQL 精确为 `(meta_fields #> '{a,"b c"}' @> %s::jsonb)`，params 为 `["1"]`
- 空格段只能作为 JSONB path 数据，不得改变 SQL 结构

**优先级**：P1

### TC-MGF-618: 中文与 `@` key 段安全编码

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `key="字段"`（中文字符）
2. `key="field@name"`（非 ASCII 特殊字符 @）

**预期结果**：
- `字段` 与 `field@name` 分别编码为安全的单段 JSONB path literal
- 两种输入均生成参数化 JSONB 等值 SQL，params 为 `["1"]`
- Unicode/特殊字符 key 只要非空且不含 NUL 就应通过安全 path encoder，不按 SQL identifier 字符集错误限制

**验收口径**：3.3.6「metadata key 与 SQL identifier 分离，特殊段安全编码」

**优先级**：P1

### TC-MGF-607: `=` 非标量 value

**类型**：异常 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `{"key":"a","op":"=","value":[1,2]}`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `scalar comparison value is non-scalar`

**优先级**：P0

### TC-MGF-608: range value 为 None

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `{"key":"a","op":">","value":None}`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `range comparison value is None`

**优先级**：P1

### TC-MGF-609: range value 为布尔

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `{"key":"a","op":">","value":True}`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `range comparison value is boolean`

**优先级**：P1

### TC-MGF-610: range value 非数字非日期

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `{"key":"a","op":">","value":"abc"}`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `unsupported range comparison value`

**优先级**：P1

### TC-MGF-611: contains value 为 None / 容器

**类型**：异常 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别 `value=None` / `[]` / `{}`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `string operator value must be a scalar`

**优先级**：P0

### TC-MGF-612: contains value 为空串

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `value=""`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `string operator value is empty`

**优先级**：P1

### TC-MGF-613: in value 为 None

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `{"key":"a","op":"in","value":None}`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `membership value is None`

**优先级**：P1

### TC-MGF-614: in value 解析为空列表

**类型**：异常 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 分别 `value=[]` / `""` / `",,"`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `membership value resolved to empty list`

**优先级**：P1

### TC-MGF-615: 非法 logic

**类型**：异常 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. `build_gaussdb_filter([], "xor")`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，消息含 `unknown logic 'xor'`

**优先级**：P0

### TC-MGF-616: 非法 jsonb_column

**类型**：异常 · 单元 · P1

**步骤**：
1. `GaussDBMetaFilterTranslator(jsonb_column="chunk_data;drop")`

**预期结果**：
- 抛 `UnsupportedGaussDBMetaFilter`，完整消息为 `invalid JSONB column 'chunk_data;drop'`

**优先级**：P1

### TC-MGF-617: SQL 注入尝试（value 含特殊字符）

**类型**：安全 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `build_gaussdb_filter([{"key":"status","op":"contains","value":"x%_\\'; DROP TABLE--"}], "and")`

**预期结果**：
- `sql == "(jsonb_exists(meta_fields #> '{status}', %s) OR lower(meta_fields #>> '{status}') LIKE %s ESCAPE '\\')"`
- `params == ["x%_\\'; drop table--", "%x\\%\\_\\\\'; drop table--%"]`
- value 作为绑定参数 `%s` 传入，**不**出现在 SQL 文本
- `escape_like_pattern` 精确转义 `%`、`_`、`\`：`%` → `\%`，`_` → `\_`，`\` → `\\`
- **SQL 文本不含注入字符串 `DROP TABLE`、`drop table` 或 `x%_`**，注入内容只出现在 params（参数化防注入）

**验收口径**：3.3.6「LIKE 转义与参数化防注入」

**优先级**：P0

---

## 八、helper 公开函数（7xx）

### TC-MGF-701: `is_pushdown_supported` 合法返回 True

**类型**：正向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `is_pushdown_supported([{"key":"a","op":"=","value":"x"}])`
2. 调用 `plan_pushdown(..., "and")` 与 `build_gaussdb_filter(...)` 验证 plan 和公开 SQL

**预期结果**：
- `is_pushdown_supported` 返回 `True`（不抛异常）
- plan 的 `logic == "and"`，且只包含一个精确 translated predicate
- 内部 `sql == "(lower(meta_fields #>> '{a}') = %s OR jsonb_exists(meta_fields #> '{a}', %s))"`，`params == ["x", "x"]`
- `plan_pushdown` 成功翻译并返回 plan

**验收口径**：3.3.6「is_pushdown_supported 检测」

**优先级**：P1

### TC-MGF-702: `is_pushdown_supported` 非法返回 False

**类型**：反向 · 单元 · P1

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 调用 `is_pushdown_supported([{"key":"a","op":"before","value":"x"}])`

**预期结果**：
- 返回 `False`（捕获 `UnsupportedGaussDBMetaFilter` 异常，不向外抛）
- `"before"` 不在 `SUPPORTED_OPERATORS`，`normalize_metadata_filter_op` 抛异常被捕获

**验收口径**：3.3.6「is_pushdown_supported 捕获异常返回 False」

**优先级**：P1

### TC-MGF-704: 所有支持 operator 的 normalize 与 translator 契约

**类型**：正向 · 单元 · P0

**步骤**：
1. 为 `=` / `≠` / `>` / `≥` / `<` / `≤` / `in` / `not in` / `contains` / `not contains` / `start with` / `end with` / `empty` / `not empty` 分别准备可翻译输入及精确期望 SQL/params
2. 对每个 canonical operator 调用 `normalize_gaussdb_meta_operator(op)`
3. 对每个输入调用公开 `GaussDBMetaFilterTranslator.translate()`

**预期结果**：
- 测试侧独立 expected 集合固定为 `=` / `≠` / `>` / `≥` / `<` / `≤` / `in` / `not in` / `contains` / `not contains` / `start with` / `end with` / `empty` / `not empty`
- 该独立集合与生产 `SUPPORTED_OPERATORS` 精确相等，无遗漏或额外项；不得直接把生产常量复制为 expected
- 每个 canonical operator 的 normalize 结果等于自身
- 每个 operator 的 `translated.sql` 与 `translated.params` 均精确等于用例矩阵；range 使用数字 cast，membership 使用真实 equal predicate，LIKE 类使用 `ESCAPE '\'`，empty 类使用固定 JSONB literal
- 本用例验证公开 normalize/translator 的可达行为，不通过 monkeypatch 制造 handler 不可达分支

**优先级**：P0

### TC-MGF-707: `coerce_scalar_value` 强转矩阵

**类型**：边界 · 单元 · P1

**步骤**：
1. 分别输入 `"123"` / `"1.5"` / `"true"` / `"false"` / `"null"` / `"True"` / `"False"` / `"None"` / `""` / `"  "` / `"[1,2]"` / `"abc"` / `5` / `None`

**预期结果**：
- `"123"` → `123`；`"1.5"` → `1.5`
- 小写字符串 `"true"` / `"false"` / `"null"` 按源码保留为字符串：分别返回 `"true"` / `"false"` / `"null"`
- 需要强转时使用 Python literal 大小写：`"True"` → `True`；`"False"` → `False`；`"None"` → `None`
- `""` → `""`；`"  "` → `"  "`；`"[1,2]"` → `"[1,2]"`；`"abc"` → `"abc"`；`5` → `5`；`None` → `None`

**优先级**：P1

### TC-MGF-708: `normalize_datetime_prefix` 矩阵

**类型**：边界 · 单元 · P1

**步骤**：
1. 分别输入 `"2026"` / `"2026-07"` / `"2026-07-08 10:30:45"` / `"2026-13-01"` / `"2026-07-32"` / `"abc"`

**预期结果**：
- `"2026"` → `None`；`"2026-07"` → `"2026-07-01 00:00:00"`；`"2026-07-08 10:30:45"` → 同
- `"2026-13-01"` → `None`；`"2026-07-32"` → `None`；`"abc"` → `None`

**优先级**：P1

### TC-MGF-709: `fetch_gaussdb_metadata_doc_ids` 公开委托边界

**类型**：正向 · 单元 · P1

**步骤**：
1. 构造记录调用参数的轻量 `doc_store` fake，其 `fetch_metadata_doc_ids` 返回固定列表 `["doc-1", "doc-2"]`
2. 调用 `fetch_gaussdb_metadata_doc_ids(doc_store, index_name, kb_ids, sql_filter, filter_params, 101)`

**预期结果**：
- 返回对象就是底层 `fetch_metadata_doc_ids` 的返回列表，不做额外转换
- 底层方法只调用一次，且 `index_name`、`kb_ids`、`sql_filter`、`filter_params`、`limit=101` 按原顺序原值透传
- 该用例只验公开委托边界，不声称完成真实 GaussDB 集成

**优先级**：P1

### TC-MGF-710: GaussDB pushdown 探测上限与完整结果判定

**类型**：边界 · 组件单元 · P0

**步骤**：
1. 为 public `filter_doc_ids_by_meta_pushdown` 配置真实 tenant、GaussDB store fake 和合法 equality filter
2. 令底层分别返回恰好 `limit` 条及 `limit + 1` 条 ID

**预期结果**：
- SQL filter、参数、metadata 表名和 KB scope 精确透传
- 底层探测 limit 固定为 `limit + 1`，且每次只调用一次
- 恰好 `limit` 个唯一 ID 时完整返回；重复 ID 去重后未超限仍完整返回；超过 `limit` 个唯一 ID 时返回 `None`
- 只读 pushdown 不调用建表、insert 或 delete

**优先级**：P0

### TC-MGF-711: 不支持的 filter 在 public pushdown 边界安全降级

**类型**：异常 · 组件单元 · P0

**步骤**：
1. tenant、index 和 GaussDB store 前置均有效，分别输入不支持的 `regex` operator 与非法 `xor` logic
2. 调用 public `filter_doc_ids_by_meta_pushdown`

**预期结果**：
- 两种输入均返回 `None`，交由内存过滤回退
- 不调用底层 metadata fetch；不能因 KB 前置缺失而提前返回形成空证据
- 不调用建表、insert 或 delete；异常日志不得包含完整 filter/key/value

**优先级**：P0

### TC-MGF-712: GaussDB metadata fetch 异常不泄露部分结果

**类型**：异常 · 组件单元 · P0

**步骤**：
1. 使用合法 equality filter，并令底层 fetch 抛出数据库异常
2. 从 public pushdown 入口调用

**预期结果**：
- 返回 `None`，不返回任何部分 doc ID
- fetch 恰好调用一次，表名、KB、SQL、参数和 `limit + 1` 均精确
- 不调用建表、insert 或 delete

**优先级**：P0

### TC-MGF-713: adapter metadata doc id 查询边界

**类型**：正反向 · 单元 · P0

**步骤**：
1. 调用 `GaussDBConnection.fetch_metadata_doc_ids`，传入 metadata 表、一个或多个 KB、translator 生成的 JSONB filter、绑定参数和 probe limit。
2. 覆盖空 KB、空 filter 与误传 chunk 表三个反向分支。

**预期结果**：
- 正向 SQL 只查询 `id`，同时包含 metadata 表名、`kb_id IN (...)`、括号包裹的 filter、`ORDER BY id` 与参数化 `LIMIT`；返回顺序保持 cursor 顺序。
- 空 KB 或空 filter 返回空列表且连接池获取次数为 0。
- chunk 表立即抛 `ValueError`，不得把 metadata filter 下推到 chunk 表。
- 正向查询不 commit、不 rollback，cursor 关闭且连接只归还一次；反向分支不获取也不归还连接。

**自动化**：
- `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_713_fetch_metadata_doc_ids_builds_scoped_jsonb_query`
- `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_713_fetch_metadata_doc_ids_returns_empty_for_empty_scope_or_filter`
- `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_713_fetch_metadata_doc_ids_rejects_chunk_table`

**优先级**：P0

### TC-MGF-811: metadata doc_id 查询保留 probe limit 与行形态

**类型**：边界 · 单元 · P1

**前置条件**：不连接数据库；使用记录 cursor，返回 `{"id": "doc1"}`、`{"id": None}`、`("doc2",)` 和空行等混合行形态。

**步骤**：调用 `fetch_metadata_doc_ids`，传入 `kb_ids=["kb1"]`、固定 filter 和 `limit=4`。

**预期结果**：只保留有效 doc_id `['doc1', 'doc2']`；SQL 使用相同的 `limit=4`，KB 参数和 filter 参数顺序不变；空值、空行不产生伪造 doc_id；查询不 commit、不 rollback，cursor 关闭且连接只归还一次。

**自动化对照**：`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_811_fetch_metadata_doc_ids_preserves_probe_limit_and_row_shapes`

**优先级**：P1

---

## 九、集成层真实库命中验证（8xx）

> 需 `GAUSSDB_INTEGRATION=1`。TC-MGF-801-806、809 统一使用上方 `metadata_scope/_insert/_match` 真实链路；TC-MGF-808 单独展开完整 public service 调用链。

### TC-MGF-801: `=` 字符串大小写不敏感命中

**类型**：集成 · P0

**前置条件**：`metadata_scope` 已创建真实 KB、真实 tenant metadata 表和 `kb_id` 索引；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "upper", "meta_fields": {"status": "Active"}},
    {"id": "lower", "meta_fields": {"status": "active"}},
    {"id": "other", "meta_fields": {"status": "inactive"}},
])
```

**步骤**：
1. 调生产 translator 生成值为 `"ACTIVE"` 的固定 SQL/params 契约
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- `sql == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))"`，`params == ["active", "active"]`
- 返回 `['lower', 'upper']`（`lower()` 归一大小写）；`other` 不命中
- **SQL 文本不含用户值 "active" 或 "Active"**，用户值只出现在 params（参数化防注入）

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「`=` 大小写不敏感」

**优先级**：P0

### TC-MGF-802: `contains` 命中数组元素与子串

**类型**：正向 · 集成 · P0

**前置条件**：同 TC-MGF-801；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "array", "meta_fields": {"tags": ["Acme", "Beta"]}},
    {"id": "scalar", "meta_fields": {"tags": "Acme Corp"}},
    {"id": "other", "meta_fields": {"tags": "Contoso"}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"tags","op":"contains","value":"acme"}], "and")` 取 `(sql, params)`
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- `sql == "(jsonb_exists(meta_fields #> '{tags}', %s) OR lower(meta_fields #>> '{tags}') LIKE %s ESCAPE '\\')"`，`params == ["acme", "%acme%"]`
- 返回 `['array', 'scalar']`：前者由 `jsonb_exists` 命中数组元素，后者由 `LIKE` 命中子串；`other` 不命中
- **SQL 文本不含用户值 "acme" 或 "Acme"**，用户值只出现在 params（参数化防注入）

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「contains 同时命中数组元素与子串」

**优先级**：P0

### TC-MGF-803: `>` 数字过滤

**类型**：正向 · 集成 · P0

**前置条件**：同 TC-MGF-801；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "string-number", "meta_fields": {"amount": "200"}},
    {"id": "small-number", "meta_fields": {"amount": 50}},
    {"id": "non-number", "meta_fields": {"amount": "abc"}},
    {"id": "missing", "meta_fields": {"other": 1}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"amount","op":">","value":100}], "and")` 取 `(sql, params)`
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- `sql == "(CASE WHEN meta_fields #>> '{amount}' ~ '^-?[0-9]+(\\.[0-9]+)?$' THEN (meta_fields #>> '{amount}')::DOUBLE PRECISION > %s ELSE FALSE END)"`，`params == [100]`
- 返回 `['string-number']`；`small-number`、`non-number`、`missing` 均不命中
- `non-number` 必须由 `ELSE FALSE` 排除，查询不得尝试将 `abc` cast 为 `DOUBLE PRECISION`；该断言验证真实数据库求值，不把当前执行计划当作契约
- **SQL 文本不含用户值 "100"**，用户值只出现在 params（参数化防注入）

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「range 数字过滤 + 非数字不召回」

**优先级**：P0

### TC-MGF-804: `>` 日期跨精度比较

**类型**：正向 · 集成 · P0

**前置条件**：同 TC-MGF-801；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "with-time", "meta_fields": {"dt": "2026-07-08 10:30:00"}},
    {"id": "date-only", "meta_fields": {"dt": "2026-07-09"}},
    {"id": "boundary", "meta_fields": {"dt": "2026-07-01"}},
    {"id": "old", "meta_fields": {"dt": "2026-06-01"}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"dt","op":">","value":"2026-07"}], "and")` 取 `(sql, params)`
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- `sql` 同 TC-MGF-105 的 CASE 表达式（含 `CASE WHEN meta_fields #>> '{dt}' ~` 与 `END > to_timestamp(%s, 'YYYY-MM-DD HH24:MI:SS')`，外层有括号）
- `params == ["2026-07-01 00:00:00"]`
- 返回 `['date-only', 'with-time']`；严格大于边界，因此 `boundary` 与 `old` 均不命中

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「日期 ISO 前缀跨精度比较」

**优先级**：P0

### TC-MGF-805: `empty` / `not empty` 五态区分

**类型**：正向 · 集成 · P0

**前置条件**：同 TC-MGF-801；独立管理 SQL 写入并回读五种空态与一条非空记录：
```python
_insert(metadata_scope, [
    {"id": "missing", "meta_fields": {"other": 1}},
    {"id": "json-null", "meta_fields": {"status": None}},
    {"id": "empty-string", "meta_fields": {"status": ""}},
    {"id": "empty-array", "meta_fields": {"status": []}},
    {"id": "empty-object", "meta_fields": {"status": {}}},
    {"id": "active", "meta_fields": {"status": "active"}},
])
```

**步骤**：
1. 分别调 `build_gaussdb_filter` 生成 `empty` 与 `not empty` 的 `(sql, params)`
2. 逐条通过 `_match` 调真实 service pushdown

**预期结果**：
- `empty` 精确返回 `['empty-array', 'empty-object', 'empty-string', 'json-null', 'missing']`
- `not empty` 返回 `['active']`
- 两集合不相交，且并集精确等于全部六条固定记录

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「empty / not_empty 五态区分」

**优先级**：P0

### TC-MGF-806: `not in` 命中 JSON null / 空串

**类型**：正向 · 集成 · P0

**前置条件**：同 TC-MGF-801；独立管理 SQL 写入并回读：
```python
_insert(metadata_scope, [
    {"id": "json-null", "meta_fields": {"status": None}},
    {"id": "empty-string", "meta_fields": {"status": ""}},
    {"id": "member-a", "meta_fields": {"status": "a"}},
    {"id": "member-b", "meta_fields": {"status": "b"}},
    {"id": "missing", "meta_fields": {"other": 1}},
])
```

**步骤**：
1. 调 `build_gaussdb_filter([{"key":"status","op":"not in","value":["a","b"]}], "and")` 取 `(sql, params)`
2. 通过 `_match` 调真实 service pushdown

**预期结果**：
- `sql == "((meta_fields #> '{status}') IS NOT NULL AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE AND (lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s)) IS NOT TRUE)"`，`params == ["a", "a", "b", "b"]`
- 返回 `['empty-string', 'json-null']`
- `member-a`、`member-b` 与 key missing 的 `missing` 都不命中
- **SQL 文本不含用户值 "a" 或 "b"**，用户值只出现在 params（参数化防注入）

**清理**：fixture 删除表并独立断言 catalog 残留为 0，同时清理 KB

**验收口径**：3.3.6「`not in` 对 key missing、JSON null、JSON 空字符串的当前 SQL 语义」

**优先级**：P0

### TC-MGF-808: pushdown 返回 `[]` 语义

**类型**：正向 · 集成 · P0

> 本用例验证 `DocMetadataService.filter_doc_ids_by_meta_pushdown` 的返回语义，调用路径为：
> `DocMetadataService → build_gaussdb_filter → fetch_gaussdb_metadata_doc_ids → GaussDBConnection.fetch_metadata_doc_ids`。
> 这是 metadata 模块的跨层集成验收，只在 03 计数和维护，不再重复归属 04。

**前置条件**：`GAUSSDB_INTEGRATION=1`；使用 `ragflow_kb_context` fixture 构造真实 `Knowledgebase` 记录并取得 `tenant_id/kb_id`；`settings.docStoreConn` 为 `GaussDBConnection()`；通过独立管理连接创建服务层真实表并插入含 `kb_id` 的行：
```python
tenant_id = ragflow_kb_context["tenant_id"]
kb_id = ragflow_kb_context["kb_id"]
meta_table = f"ragflow_doc_meta_{tenant_id}"
CREATE TABLE <schema>.<meta_table> (
    id VARCHAR(256) PRIMARY KEY,
    kb_id VARCHAR(256) NOT NULL,
    meta_fields JSONB
);
INSERT INTO <schema>.<meta_table> VALUES
    ('d1', <kb_id>, '{"status":"active"}'::jsonb),
    ('d2', <kb_id>, '{"status":"inactive"}'::jsonb);
```

**步骤**：
1. 对 `metadata_gaussdb_filter.fetch_gaussdb_metadata_doc_ids` 建立调用真实实现的透传 spy
2. 调 `DocMetadataService.filter_doc_ids_by_meta_pushdown(kb_ids=[kb_id], filters=[{"key":"status","op":"=","value":"nonexistent"}], logic="and", limit=100)`
3. 再从 `apply_meta_data_filter(method="manual")` 调用同一过滤条件，并提供 `metas_loader` sentinel；若 fallback 调用该 sentinel 则立即抛 `AssertionError`

**预期结果**：
- service 入口返回 `[]`（已下推、无匹配）——**不**返回 `None`（区别于不支持/超限的 fallback）
- public manual 调用层按既有无结果契约返回 `['-999']`，但 `metas_loader` 调用次数为 0，证明没有进入内存 fallback
- 下推 SQL 由 `GaussDBConnection.fetch_metadata_doc_ids()` 生成：`SELECT id FROM <schema>.ragflow_doc_meta_<tenant> WHERE kb_id IN (%s) AND ((lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))) ORDER BY id LIMIT %s`
- builder 固定契约为 `sql_filter == "(lower(meta_fields #>> '{status}') = %s OR jsonb_exists(meta_fields #> '{status}', %s))"`、`params == ['nonexistent', 'nonexistent']`
- 传给 fetch 的 limit 为 `101`（`limit=100` 时服务层始终用 `limit + 1` 探测），最终返回空列表，不触发 overflow fallback
- fetch 恰好调用 1 次；调用方不触发内存 fallback

**清理**：管理连接执行 schema-qualified `DROP TABLE IF EXISTS <schema>.<meta_table> CASCADE`，并断言 `information_schema.tables` 中该表计数为 0；`ragflow_kb_context` 负责清理 KB 记录

**验收口径**：3.3.6「`None` 触发 fallback；`[]` 表示已下推无匹配」

**优先级**：P0

### TC-MGF-809: limit+1 overflow 检测

**类型**：边界 · 集成 · P0

> 本用例验证 `DocMetadataService.filter_doc_ids_by_meta_pushdown` 的 `limit+1` overflow 检测逻辑，调用路径为：
> `DocMetadataService → build_gaussdb_filter → fetch_gaussdb_metadata_doc_ids`。这是 03 唯一维护的 service 语义集成用例。

**前置条件**：使用 `metadata_scope` 创建真实 KB tenant 和 `ragflow_doc_meta_<tenant>`；`settings.docStoreConn` 为当前分支真实 `GaussDBConnection()`；独立管理连接插入 `active-1` 至 `active-5` 五行匹配记录及一行 `inactive`，提交后独立回读全部 `id/kb_id/meta_fields`：
```python
tenant_id = ragflow_kb_context["tenant_id"]
kb_id = ragflow_kb_context["kb_id"]
meta_table = f"ragflow_doc_meta_{tenant_id}"
CREATE TABLE <schema>.<meta_table> (
    id VARCHAR(256) PRIMARY KEY,
    kb_id VARCHAR(256) NOT NULL,
    meta_fields JSONB
);
-- 使用参数化 executemany 写入 active-1..active-5 和 inactive，meta_fields 参数显式转为 jsonb。
```

**步骤**：
1. 保存真实 `fetch_metadata_doc_ids`，用只记录 args/kwargs 后继续调用真实实现的透传 wrapper 替换；不伪造 SQL 或返回值
2. 在同一主函数中依次调用 `limit=3/5/6`，每轮前清空调用记录，匹配数固定为 5

**预期结果**：
- `limit=3` + 5 匹配行（>limit）→ 下推取 `limit+1=4` 行，发现超限 → 返回 `None`（触发内存 fallback）
- `limit=5` + 5 匹配行（==limit）→ 返回 `['active-1','active-2','active-3','active-4','active-5']`
- `limit=6` + 5 匹配行（<limit）→ 返回同一完整固定列表
- 下推 SQL 形如 `SELECT id FROM <schema>.ragflow_doc_meta_<tenant> WHERE kb_id IN (%s) AND (<sql_filter>) ORDER BY id LIMIT %s`，params 末尾为 `probe_limit = limit+1`（limit=3 时为 4，limit=5 时为 6，limit=6 时为 7）；超限分支返回 `None`，不返回截断后的 3 个
- 每个参数分支 fetch 恰好调用 1 次；`None` 只允许出现在 `match_count > limit` 分支

**清理**：`metadata_scope` finally 删除表并独立断言 catalog 残留为 0；`ragflow_kb_context` 清理 KB 记录

**验收口径**：当前 `doc_metadata_service.py` 的 metadata pushdown 契约：查询 `limit + 1`，仅返回行数超过 limit 时 fallback；同时记录该行为与技术设计的对应关系。

**优先级**：P0

### TC-MGF-810: 禁止生成 `= ''` 方言检查

**类型**：安全 · 单元 · P0

**前置条件**：默认 `jsonb_column="meta_fields"`

**步骤**：
1. 定义 14 operator filter 列表：
```python
ALL_OP_FILTERS = [
    {"key":"status","op":"=","value":"x"},
    {"key":"status","op":"≠","value":"x"},
    {"key":"amount","op":">","value":100},
    {"key":"amount","op":"≥","value":100},
    {"key":"amount","op":"<","value":100},
    {"key":"amount","op":"≤","value":100},
    {"key":"status","op":"contains","value":"x"},
    {"key":"status","op":"not contains","value":"x"},
    {"key":"status","op":"start with","value":"x"},
    {"key":"status","op":"end with","value":"x"},
    {"key":"status","op":"empty","value":None},
    {"key":"status","op":"not empty","value":None},
    {"key":"status","op":"in","value":["x","y"]},
    {"key":"status","op":"not in","value":["x","y"]},
]
```
2. 遍历调 `build_gaussdb_filter([flt], "and")` 取 `(sql, params)`
3. 对每条 sql 断言不含 `= ''` 或 `<> ''`

**预期结果**：
- 所有 14 条 SQL 文本中均不出现 `= ''` 或 `<> ''`
- 空串等值走 `= '""'::jsonb`（TC-MGF-202）
- `empty` 走 JSONB literal：`'null'::jsonb`、`'""'::jsonb`、`'[]'::jsonb`、`'{}'::jsonb`，不生成裸 `= ''`
- `not empty` 走 `(<jsonb literal comparison>) IS NOT TRUE`，不生成裸 `<> ''`
- 禁止裸 `= ''` 方言避免 GaussDB 空串等值歧义

**验收口径**：3.3.6「不允许 `= ''`」

**优先级**：P0

---

## 测试清理

单元测试无副作用。集成测试清理必须针对 schema-qualified 唯一表名执行，不能清理固定裸表名：

```sql
DROP TABLE IF EXISTS <schema>.<meta_table>;
-- 验证
SELECT COUNT(*)
  FROM information_schema.tables
 WHERE table_schema = '<schema>' AND table_name = '<meta_table>';
-- 期望：0
```

---

## 正式用例汇总表

状态口径统一见 [README 用例状态口径](README.md#用例状态口径)。本表是本方案唯一正式自动化映射。单元 TC 的“自动化对照”必须是可收集的精确 pytest node，并且断言有效、`.venv` 实跑通过后才标为 **Covered**；多个 TC 复用同一主测试时直接映射该主测试，不另造重复函数。集成代码已经实现但尚未连接真实库执行时统一标为“集成待验证”，不以 mock 单元测试替代。

| 编号 | 分类 | 名称 | 环境 | 优先级 | 自动化对照 |
| --- | --- | --- | --- | --- | --- |
| TC-MGF-002 | 入口 | 多条 AND 拼接 | 单元 | P0 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_002_multiple_filters_join_with_and`|
| TC-MGF-003 | 入口 | 多条 OR 拼接 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_003_multiple_filters_join_with_or`|
| TC-MGF-004 | 入口 | 空 filter 返回 1=1 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_004_empty_filter_list_returns_tautology`|
| TC-MGF-005 | 入口 | 嵌套 key 路径 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_005_nested_key_path_uses_jsonb_path_literal`|
| TC-MGF-006 | 入口 | 自定义 jsonb_column | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_006_custom_jsonb_column_is_used_for_translation`|
| TC-MGF-101 | operator | `=` 字符串 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_101_equal_string_translates_to_scalar_or_array_match`|
| TC-MGF-102 | operator | `≠` null/空串命中 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_102_not_equal_string_requires_existing_key_and_negated_match`|
| TC-MGF-103 | operator | `>` 数字 | 单元 | P0 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_103_greater_than_number_uses_numeric_cast`|
| TC-MGF-104 | operator | `≥`/`<`/`≤` | 单元 | P1 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_104_numeric_comparisons_use_canonical_sql_operators`|
| TC-MGF-105 | operator | `>` 日期 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_105_date_iso_prefix_uses_exact_timestamp_predicate`|
| TC-MGF-106 | operator | `contains` | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_106_contains_string_matches_array_member_or_substring`|
| TC-MGF-107 | operator | `not contains` null/空串命中 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_107_not_contains_requires_existing_key_and_negated_contains`|
| TC-MGF-108 | operator | `start with` | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_108_start_with_uses_escaped_like_prefix`|
| TC-MGF-109 | operator | `end with` | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_109_end_with_uses_escaped_like_suffix`|
| TC-MGF-110 | operator | `in` 多成员 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_110_in_operator_expands_members_with_or`|
| TC-MGF-111 | operator | `not in` null/空串命中 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_111_not_in_operator_requires_key_and_ands_members`|
| TC-MGF-112 | operator | `empty` | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_112_empty_operator_covers_all_empty_states_and_nested_missing_parent`|
| TC-MGF-113 | operator | `not empty` | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_113_not_empty_operator_excludes_all_empty_states`|
| TC-MGF-201 | 空值 | `=` null | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_201_equal_none_generates_json_null_equality`|
| TC-MGF-202 | 空值 | `=` 空串 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_202_equal_empty_string_uses_jsonb_empty_string_literal`|
| TC-MGF-203 | 空值 | empty 命中 missing | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_203_empty_matches_missing_and_excludes_nonempty` |
| TC-MGF-204 | 空值 | empty 命中 null | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_204_empty_matches_json_null_and_excludes_nonempty` |
| TC-MGF-205 | 空值 | empty 命中空串 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_205_empty_matches_empty_string_and_not_equal` |
| TC-MGF-206 | 空值 | empty 命中空数组 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_206_empty_matches_empty_array_and_excludes_nonempty_array` |
| TC-MGF-207 | 空值 | empty 命中空对象 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_207_empty_matches_empty_object_and_excludes_nonempty_object` |
| TC-MGF-208 | 空值 | `≠` null/空串命中 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_208_not_equal_matches_null_and_empty_string_but_not_missing` |
| TC-MGF-209 | 空值 | `not empty` 不命中空态 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_209_not_empty_excludes_all_five_empty_states` |
| TC-MGF-301 | 类型 | `=` 整数 @> | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_301_equal_integer_uses_jsonb_containment`|
| TC-MGF-302 | 类型 | `=` 布尔 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_302_equal_boolean_uses_jsonb_containment`|
| TC-MGF-303 | 类型 | `=` 字符串数字 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_303_equal_numeric_string_uses_jsonb_numeric_containment`|
| TC-MGF-304 | 类型 | Python literals / 小写 JSON 单词分流 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_304_equal_python_literals_differ_from_lowercase_json_words`|
| TC-MGF-305 | 类型 | `>` 负数 | 单元 | P1 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_305_range_accepts_negative_number`|
| TC-MGF-306 | 类型 | `>` 小数 | 单元 | P1 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_306_range_accepts_decimal_number`|
| TC-MGF-307 | 类型 | `>` 字符串数字 | 单元 | P1 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_307_range_numeric_strings_stay_on_numeric_branch`|
| TC-MGF-308 | 类型 | 日期 5 种精度 / 6 种输入格式 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_308_date_iso_prefixes_normalize_to_exact_timestamp_parameters`|
| TC-MGF-309 | 类型 | 非法日期 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_309_invalid_datetime_prefix_is_rejected_as_range_value`|
| TC-MGF-310 | 类型 | in 逗号分隔 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_310_in_operator_splits_comma_separated_string_members`|
| TC-MGF-311 | 类型 | in JSON 数组串 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_311_in_operator_parses_json_array_string_members`|
| TC-MGF-312 | 类型 | `=` 字符串小数 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_312_equal_decimal_string_uses_jsonb_numeric_containment`|
| TC-MGF-313 | 类型 | in 单个 scalar | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_313_in_operator_accepts_single_scalar_member`|
| TC-MGF-401 | 组合 | AND 三条混合 | 单元 | P0 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_401_and_logic_joins_three_mixed_operators_in_order`|
| TC-MGF-402 | 组合 | OR 三条 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_402_or_logic_joins_three_filters_in_order`|
| TC-MGF-403 | 组合 | AND 含 not in | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_403_and_logic_combines_not_in_with_positive_filter`|
| TC-MGF-404 | 组合 | 嵌套 key key_exists | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_404_nested_key_exists_and_jsonb_number_predicate_join_with_and`|
| TC-MGF-405 | 安全 | 特殊 key path 安全编码 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_405_special_key_segments_are_encoded_without_sql_injection`|
| TC-MGF-501 | 别名 | `is`→`=` | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_501_is_alias_translates_to_equal_sql`|
| TC-MGF-502 | 别名 | `!=`等→`≠` | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_502_not_equal_aliases_translate_to_identical_sql`|
| TC-MGF-503 | 别名 | `>=`→`≥` | 单元 | P1 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_503_range_aliases_normalize_and_translate_to_canonical_operators`|
| TC-MGF-504 | 别名 | 大小写空白 | 单元 | P2 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_504_operator_normalization_tolerates_case_and_whitespace`|
| TC-MGF-505 | 别名 | `before`/`after` | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_505_before_after_operators_are_not_supported`|
| TC-MGF-601 | 异常 | 缺 key | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_601_missing_key_is_rejected_with_stable_reason`|
| TC-MGF-602 | 异常 | 缺 op | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_602_missing_operator_is_rejected_with_exact_message`|
| TC-MGF-603 | 异常 | 未知 op | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_603_unknown_operator_is_rejected_with_exact_message`|
| TC-MGF-604 | 正向 | 数字开头 key 编码 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_604_numeric_leading_key_segment_is_encoded`|
| TC-MGF-605 | 异常 | 空段/NUL key 拒绝 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_605_empty_or_nul_key_segments_are_rejected`|
| TC-MGF-606 | 正向 | 含空格 key 编码 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_606_key_segment_with_space_is_encoded`|
| TC-MGF-618 | 正向 | 中文/`@` key 编码 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_618_non_ascii_and_at_sign_key_segments_are_encoded`|
| TC-MGF-607 | 异常 | `=` 非标量 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_607_equal_operator_rejects_non_scalar_value`|
| TC-MGF-608 | 异常 | range None | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_608_range_operator_rejects_none_value`|
| TC-MGF-609 | 异常 | range bool | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_609_range_operator_rejects_boolean_value`|
| TC-MGF-610 | 异常 | range 非数非日 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_610_range_operator_rejects_non_numeric_non_date_string`|
| TC-MGF-611 | 异常 | contains None/容器 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_611_contains_operator_rejects_none_and_container_values`|
| TC-MGF-612 | 异常 | contains 空串 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_612_contains_operator_rejects_empty_string`|
| TC-MGF-613 | 异常 | in None | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_613_in_operator_rejects_none_value`|
| TC-MGF-614 | 异常 | in 空列表 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_614_in_operator_rejects_every_empty_resolved_member_list`|
| TC-MGF-615 | 异常 | 非法 logic | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_615_unknown_logic_is_rejected`|
| TC-MGF-616 | 异常 | 非法 column | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_616_invalid_jsonb_column_is_rejected`|
| TC-MGF-617 | 异常 | SQL 注入 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_617_value_with_sql_metacharacters_is_bound_and_like_escaped`|
| TC-MGF-701 | helper | is_pushdown_supported True | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_701_is_pushdown_supported_returns_true_for_fully_translatable_filter`|
| TC-MGF-702 | helper | is_pushdown_supported False | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_702_is_pushdown_supported_returns_false_without_raising`|
| TC-MGF-704 | helper | 全部支持 operator normalize+translate | 单元 | P0 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_704_all_supported_operators_normalize_and_translate`|
| TC-MGF-707 | helper | coerce_scalar_value 矩阵 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_707_coerce_scalar_value_covers_python_literal_matrix`|
| TC-MGF-708 | helper | normalize_datetime_prefix | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_708_normalize_datetime_prefix_covers_valid_and_invalid_matrix`|
| TC-MGF-709 | helper | fetch 公开委托边界 | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_709_fetch_gaussdb_metadata_doc_ids_delegates_public_boundary`|
| TC-MGF-710 | service | pushdown limit+1 完整性 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_doc_metadata_service.py::test_tc_mgf_710_gaussdb_pushdown_routes_scoped_filter_with_probe_limit`<br>`test/unit_test/api/db/services/test_gaussdb_doc_metadata_service.py::test_tc_mgf_710_gaussdb_pushdown_enforces_complete_result_cap`|
| TC-MGF-711 | service | 不支持 filter 安全降级且日志脱敏 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_doc_metadata_service.py::test_tc_mgf_711_gaussdb_pushdown_rejects_unsupported_filter_without_fetch`|
| TC-MGF-712 | service | fetch 异常安全降级 | 组件单元 | P0 | Covered: `test/unit_test/api/db/services/test_gaussdb_doc_metadata_service.py::test_tc_mgf_712_gaussdb_pushdown_falls_back_without_partial_ids_on_fetch_failure`|
| TC-MGF-713 | adapter | metadata doc id 查询边界 | 单元 | P0 | Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_713_fetch_metadata_doc_ids_builds_scoped_jsonb_query`<br>`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_713_fetch_metadata_doc_ids_returns_empty_for_empty_scope_or_filter`<br>`test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_713_fetch_metadata_doc_ids_rejects_chunk_table`|
| TC-MGF-801 | 集成 | `=` 大小写不敏感 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_801_equal_is_case_insensitive` |
| TC-MGF-802 | 集成 | contains 数组+子串 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_802_contains_matches_array_element_and_scalar_substring` |
| TC-MGF-803 | 集成 | `>` 数字过滤 | 集成 | P0 | 双环境 Passed：`test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_803_greater_than_filters_numeric_values_and_rejects_non_numeric`；证据见 2026-08-12 当前 JUnit |
| TC-MGF-804 | 集成 | `>` 日期跨精度 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_804_greater_than_normalizes_date_prefix_across_precisions` |
| TC-MGF-805 | 集成 | empty/not empty 五态 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_805_empty_and_not_empty_distinguish_all_five_empty_states` |
| TC-MGF-806 | 集成 | not in null/空串命中 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_806_not_in_matches_null_and_empty_string_but_not_missing_or_members` |
| TC-MGF-808 | 集成 | pushdown `[]` 语义 | 集成 | P0 | `test/integration/test_gaussdb_metadata_flow.py::test_tc_mgf_808_metadata_filter_pushdown_full_chain`；Centralized 真实环境已验证 service 返回 `[]`、manual 调用层返回 `['-999']` 且不触发内存 fallback |
| TC-MGF-809 | 集成 | limit+1 `< / == / >` 三态 | 集成 | P0 | `test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_809_limit_plus_one_probe_returns_none_or_complete_result` |
| TC-MGF-810 | 安全 | 禁止 `= ''` 方言 | 单元 | P0 | Passed: `test/unit_test/rag/utils/test_gaussdb_metadata_filter.py::test_tc_mgf_810_all_operators_avoid_bare_empty_string_sql`|
| TC-MGF-811 | service | metadata doc_id 行形态与 limit | 单元 | P1 | Covered: `test/unit_test/rag/utils/test_gaussdb_conn.py::test_tc_mgf_811_fetch_metadata_doc_ids_preserves_probe_limit_and_row_shapes`|

**用例统计**：正文及正式汇总均为 88 条，P0 50 条、P1 37 条、P2 1 条。其中 73 条要求单元/组件单元验证，15 条要求集成验证。15 条集成 TC 均有不同主函数；跨 KB 重复 `Document.id` 仅作为数据契约说明，TC-MGF-808 由 `test_gaussdb_metadata_flow.py` 维护。Centralized 与 Distributed 的本轮结果只以顶部双环境矩阵及各自固定 JUnit 为准。

## 本轮双环境补强 Detailed Cases

### TC-MGF-801/802/803 完整 14 操作符与 AND/OR

- **环境适用性 / 优先级**：Both / P0。
- **主测试函数**：`test/integration/test_gaussdb_metadata_filter_coverage.py::test_tc_mgf_801_equal_is_case_insensitive`、`::test_tc_mgf_802_contains_matches_array_element_and_scalar_substring`、`::test_tc_mgf_803_greater_than_filters_numeric_values_and_rejects_non_numeric`。
- **前置条件**：通过真实 `create_doc_meta_idx()` 创建 Ustore metadata 表和 KB 索引；真实 metadata DB 存在当前 KB/tenant。
- **测试数据 / Inputs**：固定大小写字符串、数组/标量、前后缀 code、50/200/非数值/missing，以及唯一 KB。
- **Steps**：801 执行 `=`、`in` 与 AND/OR；802 执行 `contains`、`not contains`、`start with`、`end with`；803 执行 `>`、`≥`、`<`、`≤` 与 AND/OR。203–209、805、806 继续覆盖 `≠`、empty、not empty、not in。
- **Expected Assertions**：14 个技术契约操作符均至少一次进入真实 GaussDB JSONB SQL；每个固定输入返回精确有序 doc_id 集合；AND 为交集，OR 为并集。
- **Negative Assertions**：非数值和 missing 不得误命中范围比较；LIKE 特殊字符不得改变字面语义；不得只验证生成 SQL。
- **Transaction**：独立 SQL种子提交；filter 只读；连接归还后可继续查询。
- **Cleanup / Cleanup Verification**：fixture DROP 本轮 metadata 表，独立连接确认表/索引残留为 0，KB metadata 记录由父 fixture 清理并确认 0。
- **Replay**：三个 node 在两环境分别定向执行；完整 15 个 MGF node 随全量执行。

### TC-MGF-808 service pushdown 与 KB 隔离

- **环境适用性 / 优先级**：Both / P0。
- **主测试函数**：`test/integration/test_gaussdb_metadata_flow.py::test_tc_mgf_808_metadata_filter_pushdown_full_chain`。
- **前置条件**：当前分支真实 `DocMetadataService`、生产 adapter、真实 metadata DB 和专用 schema。
- **测试数据 / Inputs**：当前 KB 的 active/inactive 两行，以及另一 KB 的 active sentinel。
- **Steps**：生产 DDL 建 Ustore metadata 表；调用 service pushdown 的 no-match 与 active 过滤；调用公开 `apply_meta_data_filter()` no-match 路径；独立 SQL回读三行。
- **Expected Assertions**：no-match service 返回 `[]`，公开调用返回既有 `["-999"]` sentinel 且不进内存 fallback；active 只返回当前 KB 的 `d1`，另一 KB `d3` 不泄露。
- **Negative Assertions**：不得 Mock GaussDB、cursor 或 SQL 返回；不得把另一个 KB 的同值 metadata 合并进结果。
- **Transaction**：种子独立提交；查询只读。
- **Cleanup / Cleanup Verification**：删除本轮表并查 0；删除 KB metadata 记录并查 0。
- **Replay**：两环境分别定向执行并保存独立 JUnit/coverage。
