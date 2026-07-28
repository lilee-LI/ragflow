# GaussDB DocEngine 适配 — 测试文档目录

本目录承载 RAGFlow 适配 GaussDB 作为 DocEngine 的完整测试方案。每个模块独立成篇，用例覆盖正常路径、边界值、异常输入、状态切换、逻辑组合与并发。

> **当前验收边界（2026-08-12）**：07、08、09 的冻结数字仍只适用于各自记录的代码树。当前分支已统一复跑三个 GaussDB feat 提交包含的 22 个无需密钥单测模块，结果为 674 passed、1 个真实 GaussDB opt-in 用例 skipped、0 failed；真实 GaussDB 数据库路径也已完成双环境补充复跑，当前正式口径为 Centralized 154/155、Distributed 152/153，唯一未执行正式 TC 均为需要当前分支 RAGFlow HTTP 服务的 `TC-CFG-612`。E2E 尚未复跑，继续保持冻结历史状态。详见 08 的“当前 HEAD 补充复跑”。

当前完整验收分支：`gaussdb-adaptation-combined-test`。需求基准只使用 `D:/RAGFlow/GaussDB_RAGFlow_DocEngine_technical_design.md`；环境与连接基准只使用 `D:/RAGFlow/GaussDB_environment_access_guide.md`。当前源码与技术设计冲突时，用例同时记录设计预期和实现现状。

## 目录拆分

| 编号 | 文档 | 对应技术设计章节 | 核心被测代码 |
| --- | --- | --- | --- |
| 01 | 配置初始化与接入状态 | 3.1 | `common/doc_store/gaussdb_conn_pool.py`、`common/settings.py`、`api/utils/health_utils.py` |
| 02 | 写入链路（文档 chunk CRUD 生命周期） | 3.2 | `rag/utils/gaussdb_conn.py`、`common/doc_store/gaussdb_conn_base.py` |
| 03 | metadata filter 翻译 | 3.3 | `common/metadata_gaussdb_filter.py` |
| 04 | 检索链路（全文 / 向量 / hybrid；**Text-to-SQL 失败的兜底路径**） | 3.4 | `rag/utils/gaussdb_conn.py`、`rag/nlp/search.py` |
| 05 | Text-to-SQL（**支路**：前端解析方式为 `table` 且有 `field_map` 时先走，失败由 04 检索兜底） | 3.5 | `common/doc_store/gaussdb_conn_base.py`(validator)、`api/db/services/dialog_service.py`、`rag/app/table.py` |
| 06 | 部署后 API E2E（仅 GaussDB 专属前端行为保留 UI） | 5.2 | `test/playwright/e2e/test_gaussdb_docengine_e2e.py` |
| 07 | [单元测试执行报告](07-GaussDB单元测试执行报告.md) | 01–05 单元 / 组件单元 | 冻结基线：570 pytest items、TC/node 映射与 Coverage HTML |
| 08 | [集成测试执行报告](08-GaussDB集成测试执行报告.md) | 01–05 真实 GaussDB | 冻结基线：Centralized 155/155、Distributed 153/153；当前补充复跑：154/155、152/153，`TC-CFG-612` 待跑；Coverage HTML 仍为冻结证据 |
| 09 | [E2E 测试执行报告](09-GaussDB-E2E测试执行报告.md) | 06 双环境真实 E2E | 冻结基线：两环境各 32 条，28 passed / 4 failed；失败均已归因 |

## 模块关系：Text-to-SQL 支路与检索兜底

Text-to-SQL（05）是**支路**而非主检索路径：dataset 解析方式为 `table` 且存在 `field_map` 时，对话检索先调用 `use_sql`；只有 `use_sql` 最终返回 `None` 时，才进入 04 的全文 / 向量 / hybrid 检索。validator 拒绝、SQL 执行失败或 repair/retry 耗尽可导致该返回值；首轮 SQL 已得到 answer 后的 source/reference 补查失败是 best-effort 降级，不会重新进入普通检索。因此：

- 05 测试方案须覆盖「支路进入条件」「`use_sql is None`→兜底」「validator/执行/repair 使 `use_sql` 返回 None」「aggregate fallback」以及「source/reference 补查失败仍保留 SQL answer」。
- 04 测试方案须覆盖「作为 Text-to-SQL 失败后的兜底检索」入口，验证调用参数、检索边界和返回结构；不重复建立与 05 相同的兜底编排 TC。

## 测试分层

- **单元测试**：纯函数 / SQL 生成，无 DB。验证生成的 SQL 字符串与绑定参数。
- **组件单元测试**：真实执行 adapter、service 或流程编排，只在外部边界使用 fake/mock，验证编排、事务和可观察结果；统计时归入单元层。
- **集成测试**：真实 GaussDB，`GAUSSDB_INTEGRATION=1` opt-in，否则干净 skip。使用 DocEngine 测试 schema。
- **端到端验收**：对标技术设计 5.2 端到端用例表。

## 用例状态口径

正式汇总表中的状态按以下规则判定：

- **Covered**：测试代码真实存在，汇总表有可由 pytest 精确收集的 `file::test_function` 或 `file::TestClass::test_method` 对照，测试包含对目标行为有意义且预言机独立的断言，并在仓库 `.venv` 中实际执行通过。参数化测试的所有参数实例也必须通过。
- **组件单元 Covered**：真实执行被测 adapter、service 或流程编排，只在外部边界使用 fake/mock；不能只验证 mock 是否被调用，也不能把被测逻辑本身整体 mock 掉。
- **独立测试预言机**：预期结果必须从技术设计、测试方案、稳定业务/API/SQL 契约或独立数学事实推导。不得用被测函数的实际输出、同一生产 helper、同一生产常量、当前实现字符串、另一次相同 mock 调用或未经独立审查的 snapshot/golden 反向生成 expected；源码文件和行号只用于定位被测对象，不是预期依据。跨输入的关系/变形断言只有在关系本身有独立契约、且关键结果另有独立断言时才有效。违反本条的测试标记为 `Weak`，即使 pytest 通过也不得计为 `Covered`。
- **集成待验证 / 静态实现**：集成测试代码已经编写，但尚未连接真实 GaussDB 或尚未完成真实数据库断言；这不算集成测试完成，也不能替代单元测试的 `Covered`。
- **E2E 待验证**：E2E 代码或断言已准备，但尚未启动真实服务、数据库或浏览器环境；不算 E2E 完成。
- **Partial / Weak / Missing**：分别表示覆盖不完整、断言过浅或没有有效测试对照，均不得视为完成。
- **开发缺陷**：测试本身有效，但暴露生产代码缺陷；必须记录到 `D:/RAGFlow/gaussdb-unit-test-development-bugs.md`。缺陷修复前不得标记 `Covered`；修复后必须移除 `xfail` 并以正常断言通过。
- **Skip / xfail**：未完成验证，不得计入通过率或覆盖完成数。

混合用例可以分别记录状态，例如“单元 Covered、集成待验证”；只有对应层级的测试达到上述条件，才能标记该层级完成。

## Setup / Action / Assert / Cleanup 继承契约

每条用例的 Action 与 Assert 必须在正文中显式给出。仅 Setup/Cleanup 允许继承以下唯一默认值；一旦用例创建额外资源、连接、进程或全局状态，就必须在该用例内覆盖默认值并写明清理：

| 层级 | 未单列 Setup 时的唯一默认值 | 未单列 Cleanup 时的唯一默认值 |
| --- | --- | --- |
| Unit | 只创建当前测试函数内的 Python 值；需要替换依赖时只使用 pytest `monkeypatch`/`tmp_path`，不访问网络或数据库 | N/A：没有外部资源；`monkeypatch` 和 `tmp_path` 由 pytest teardown 恢复/删除，局部对象随测试结束释放 |
| Component | 被测真实模块 + 文档点名的确定 fake/mock；不得把被测逻辑本身 mock 掉 | `monkeypatch` 自动恢复；若 fake 持有 cursor/connection，测试在 `finally` 调其 `close()` 并断言调用一次 |
| Integration | `GAUSSDB_INTEGRATION=1`、`gaussdb_env`，所有表名由本用例 `table_prefix` 派生；业务元数据库场景另用 `ragflow_kb_context` | 在 `finally` 关闭显式连接；fixture 只 drop 本用例登记的唯一表。清理失败必须使测试失败，不得用全局 `LIKE 'ragflow_%'` 清扫 |
| API/UI E2E | 不继承；06 每条必须显式写 Setup | 不继承；06 每条必须显式写 Cleanup，并在失败路径执行 |

因此，正文中没有单列 Setup/Cleanup 的 Unit 用例并非遗漏，而是明确采用上表的 N/A/pytest 自动回收；Integration 用例仍须列出其创建的数据和表。

## 集成测试环境连接

集成层用例（01/02/03/04/05 中标「集成」的）需真实 GaussDB，opt-in。本节为通用约定，各模块文档引用此处，不重复。

### 环境来源与隔离

- host、port、database、user、password、schema 和 E2E base URL 只从环境指南或环境变量读取；本文不复制真实值。
- integration 与 E2E 必须使用环境指南指定的不同 schema；不得把 E2E 业务数据写入 integration schema，反之亦然。
- 测试启动时记录非秘密能力：`sql_compatibility`、`enable_ustore`、`enable_vectordb` 和版本；不得把 password、token 或完整连接串写入日志/artifact。

### 环境变量

```bash
GAUSSDB_INTEGRATION=1
GAUSSDB_HOST=<from-guide>
GAUSSDB_PORT=<from-guide>
GAUSSDB_DATABASE=<from-guide>
GAUSSDB_USER=<from-guide>
GAUSSDB_PASSWORD=<见 guide>
GAUSSDB_SCHEMA=<integration-schema-from-guide>
```

### `gaussdb_env` fixture（[test/integration/conftest.py](../../test/integration/conftest.py)）

自动完成：
1. 守卫 `GAUSSDB_INTEGRATION=1` 与 `GAUSSDB_SCHEMA`，并由 session preflight 按 Centralized / Distributed 分别验证兼容模式、Ustore、VectorDB、可写状态和拓扑
2. `_assert_schema_access` 校验 USAGE / CREATE 权限；真实 metadata DB 不可用时测试失败，不以 skip 规避
3. `monkeypatch` `settings.DOC_ENGINE=gaussdb` / `settings.GAUSSDB={host:..., password:...}`（从环境变量读）
4. `table_prefix = uuid.uuid4().hex` 给每条用例隔离表名
5. `yield {schema, table_prefix, created_tables, variant_evidence}`，`finally` 调 `_drop_generated_test_tables` 自动清理并确认残留为 0

`gaussdb_env` 不提供连接对象。集成用例如需真实连接，必须显式构造
`GaussDBConnection()` 或使用文档中给出的 helper，并用 `table_prefix` 生成唯一表名；不得固定使用
`ragflow_t1`、`doc_meta` 等裸表名。

### 两层 fixture 区分

| fixture | 适用 | 依赖 |
| --- | --- | --- |
| `gaussdb_env` | 纯 adapter 用例（`create_idx` / `insert` / `get` / `search` / `sql` 等） | 仅 GaussDB |
| `ragflow_kb_context` | 需真实 KB / tenant 的用例（重解析、KB 删除、feedback、对话） | GaussDB + RAGFlow 元数据库；关键依赖不可用时 fail，不产生 skip |

### 跑命令

```bash
# 先按环境指南在当前 shell 设置 GAUSSDB_HOST/PORT/DATABASE/USER/PASSWORD/SCHEMA
GAUSSDB_INTEGRATION=1 pytest -m gaussdb_integration test/integration/test_gaussdb_*.py
```

上述通配命令会同时收集 01–05 正式 TC、未编号补充函数和 Memory Store 补充测试，不能仅凭最终 item 数推导 155/153 正式 TC 结果。正式验收必须按 01–05 的精确 node 映射审计；其中 `TC-CFG-612` 还要求目标 variant 的当前分支 RAGFlow HTTP 服务已启动。

### 手工能力探测

按 `GaussDB_environment_access_guide.md`：SSH → `su - omm` → gsql，执行 `SELECT version()` / `SHOW sql_compatibility` / `SHOW enable_vectordb` 等确认环境。

### E2E 环境（06 专用，与集成测试分开）

E2E 使用环境指南指定的独立 schema 和 Docker 服务（`docker-compose.gaussdb-local.yml` 覆盖挂载本地代码），通过真实 HTTP API 访问部署后的 RAGFlow，**不**使用 integration schema。

06 的专项 E2E 用例必须先阅读 `D:/RAGFlow/GaussDB_environment_access_guide.md` 第 8 节，并在
`D:/RAGFlow/official_source/ragflow/docker` 下启动服务：

```bash
docker compose -f docker-compose.yml -f docker-compose.gaussdb-local.yml up -d --force-recreate ragflow-cpu
```

主路径使用 `requests`、`httpx` 或 Playwright `APIRequestContext` 命中真实 HTTP 服务，不 mock RAGFlow、
GaussDB、解析任务或 retriever。只有明确验收 GaussDB 专属前端行为时才保留 UI E2E；通用登录、按钮、
表单、路由和页面渲染交由原有 UI suite。缺 selector/testid 的 `xfail` 记为 `Missing`，不得计为功能已覆盖。
管理员账号从 guide 或 `E2E_ADMIN_EMAIL` / `E2E_ADMIN_PASSWORD` 环境变量读取，本文档不复制凭据。

## 用例编号规则

`TC-<模块缩写>-<分组><序号>`，分组约定：

| 组 | 含义 |
| --- | --- |
| 0xx | 基础入口与 plan 组装 |
| 1xx | 14 个 operator 翻译（正向） |
| 2xx | 空值五态（missing / null / 空串 / 空数组 / 空对象） |
| 3xx | 数据类型（数字 / 日期时间 ISO 前缀） |
| 4xx | 逻辑组合 AND / OR |
| 5xx | 别名归一化 |
| 6xx | 异常输入与拒绝 |
| 7xx | helper 公开函数 |
| 8xx | 集成层真实库命中验证 |

## 每条用例的字段

- 用例编号 / 模块 / 类型（正向·反向·边界·异常·组合）
- 前置条件
- 输入（调用与参数）
- 期望输出（SQL 片段 + params，或抛出的异常）
- 集成验证（真实库执行命中/不命中，opt-in）
- 验收口径（对应技术设计 X.6 测试点）
- 优先级（P0/P1/P2）

## 文档用例汇总

- [x] 01 配置初始化（90 条）
- [x] 02 写入链路（116 条）
- [x] 03 metadata filter（88 条）
- [x] 04 检索链路（88 条）
- [x] 05 Text-to-SQL（114 条）
- [x] 06 API E2E（32 条；当前无 GaussDB 专属 UI 用户入口）

**合计 528 条**（P0 287 / P1 238 / P2 3）。其中 01-05 为 496 条，06 为 32 条。新增的 9 条 E2E 均来自技术设计 5.2 与真实公开 API 的全链路缺口：embedding check、手工 chunk 生命周期、文档启停、空 `doc_ids`、纯中文全文、普通多 KB、纯英文 RAG 对话、中英混合检索与对话和 feedback 后排序；没有重复搬入 01-05 的内部矩阵。数量以各文件正式用例汇总表中的唯一 TC 为准；文末补强 Detailed Case 可能重复同一 TC 标题，不重复计数。勾选只表示文档已纳入本目录，不表示对应 pytest 已实现或已通过。06 原 TC-E2E-1203 因通用管理前端对 GaussDB、OceanBase 等 DocEngine 均无状态卡用户入口而删除，不以生产代码新增功能来满足测试方案。

### 按测试层级统计

层级需求数为非互斥统计：同一 TC 同时要求单元和集成验证时，会分别计入两层。「单元」包含纯单元和组件单元；E2E 用例只存在于 06。

| 层级 | 涉及的 TC 数 | 当前验收状态 |
| --- | ---: | --- |
| 单元（含组件单元） | 394 | 冻结证据为 394/394 Covered、570/570 passed；当前三个 feat 的 22 个无需密钥模块补充复跑为 674 passed、1 个真实 GaussDB opt-in 用例 skipped、0 failed，未重建冻结 coverage/traceability artifact |
| 集成 | 155 | 冻结完整结果为 Centralized 155/155、Distributed 153/153；当前数据库路径补充复跑的正式口径为 154/155、152/153，唯一待跑为双环境 `TC-CFG-612`，因此当前整体仍为 Pending rerun |
| E2E | 32 | 冻结结果为 Centralized 与 Distributed 各 `28 passed / 4 failed`；当前 HEAD 尚未复跑，详见 [09 E2E 执行报告](09-GaussDB-E2E测试执行报告.md) |

为了和 528 条唯一 TC 总数直接核对，互斥分布为：

| 互斥分类 | TC 数 |
| --- | ---: |
| 仅单元/组件单元 | 341 |
| 仅集成 | 102 |
| 单元 + 集成 | 53 |
| E2E | 32 |

### 集成环境适用性与当前补充结果

01-05 的 155 条正式集成 TC 按当前方案分为 `Both=148`、`Centralized=2`、`Distributed=0`、`Variant-specific=5`。Centralized 专属为 TC-CFG-601、TC-WRT-004；Variant-specific 为 TC-CFG-612、TC-CFG-901、TC-CFG-902、TC-WRT-009、TC-RET-905。TC-RET-109 已从旧 blocker 升级为 `Both`，因此本轮 Distributed 应收集并执行 `148 + 5 = 153` 个 pytest item，两个 Centralized 专属 node 在 collection 阶段排除，不产生 skip。

| 环境 | 正式适用 TC | 当前正式结果 | 当前独立证据 |
| --- | ---: | --- | --- |
| Centralized | 155 | `154/155 passed`；`TC-CFG-612` 未执行；当前 JUnit 原始 155 passed 另含 1 个未编号补充函数；failed/error/skipped=0；最终残留=0 | `test/integration/artifacts/gaussdb-centralized-clean-20260812-151227.xml` |
| Distributed | 153 | `152/153 passed`；`TC-CFG-612` 未执行；当前 JUnit 原始 153 passed 另含 1 个未编号补充函数；failed/error/skipped=0；最终残留=0 | `test/integration/artifacts/gaussdb-distributed-current-20260812-141912.xml` |

只有每个 `Both` TC 在两套环境分别通过且 oracle 完整，才可把整体状态写成 Covered。当前补充复跑已证明评审后调整的 TC-MGF-803、TC-WRT-112、TC-RET-603、TC-RET-704、TC-SQL-701 在双环境通过；其它被收集的正式数据库 TC 也均通过。由于 `TC-CFG-612` 尚未在当前 HEAD 的真实 HTTP 服务上执行，整体状态继续标记为 Pending rerun。冻结基线中的 1536/3072 拒绝与事务回滚、TC-WRT-108、TC-RET-109、WRT-507、IT-ISSUE-106/107/108 结论仍保留为历史证据，不冒充当前未执行的 HTTP 验收。

### 冻结单元与双环境集成覆盖率验收快照（2026-07-27）

- 01-05 正式汇总共 496 条 TC，其中 394 条要求单元/组件单元验证；394 条均有精确 pytest node、有效断言和仓库 `.venv` 实跑证据。
- 当前集合只包含 16 个 GaussDB 单元模块，共有 450 个 `test_tc_*` 函数；文档中存在 450 个有效 unit node，代码到文档未映射函数为 0，文档到代码失效 node 为 0。
- 本轮单元执行拆分为 `466 + 73 + 19 + 12 = 570/570 passed`；failed/error/skipped/xfail 均为 0。JUnit 固定写入 `test/unit_test/artifacts/929586d-20260727-1630/`。
- 当前集成 coverage 只采集两个生产 adapter 模块，并启用 branch：Centralized line `1613/1974`（81.71%）、branch `587/838`（70.05%）；Distributed line `1615/1974`（81.81%）、branch `586/838`（69.93%）。两套环境不得把各自百分比相加。
- 单元 coverage data 已保留并生成 [07 单元 Coverage HTML](07-单元测试覆盖率-html/index.html)；两套固定 integration XML 已生成 [08 双环境 Coverage HTML](08-集成测试覆盖率.html)。旧 `artifacts/coverage/` 内容仍不是当前证据，也不得冒充单元+集成+E2E 合并 coverage。
- 本轮 E2E 使用的 RAGFlow 后端没有启动 `coverage.py` 插桩，因此 E2E 自身命中的生产代码覆盖率以及单元+集成+E2E 三层合并覆盖率均不可采集；不得把客户端 pytest coverage 或上述单元+集成两层值冒充 E2E coverage。

这些数字口径不同：394 是测试文档中要求单元验证的 TC 数；450 是当前单元测试函数总数且全部带 TC 编号并有映射；570 是参数化展开后的单元 pytest 执行项数；155 是正式集成 TC、主函数和 Centralized item 数，Distributed 因排除两个 Centralized 专属 TC 为 153。单元层每个 TC 至少有一个完整主函数，必要的边界可以由同编号补充函数共同证明；集成层严格保持一 TC 对一个主函数。
