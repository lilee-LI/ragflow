# GaussDB 集成测试执行报告

> **报告口径（2026-08-12）**：本文件同时保留 2026-07-27 冻结执行事实，并记录当前 HEAD 的补充复跑。冻结 JUnit、coverage、时间与哈希不被新结果覆盖；当前补充复跑因未执行需要真实 RAGFlow HTTP 服务的 `TC-CFG-612`，仍不能作为当前 HEAD 全部正式 TC 已通过的证明。

## 当前 HEAD 补充复跑（2026-08-12）

### 结论与验收边界

当前分支已在真实 Centralized 与 Distributed GaussDB 上复跑 01–05 的数据库路径，两份完整 JUnit 分别记录 `155 passed` 与 `153 passed`。但是，原始 item 数不能直接等同于正式 TC 数：本轮主动排除了正式 `TC-CFG-612`，同时收集了未编号补充函数 `test_metadata_filter_complex_pushdown_full_chain`。因此当前正式验收口径为：

| 环境 | 当前 JUnit 原始结果 | 当前正式 TC 通过 / 适用 | 当前未执行正式 TC | 未编号补充函数 |
| --- | --- | ---: | --- | ---: |
| Centralized | 155 passed | 154 / 155 | `TC-CFG-612` | 1 passed |
| Distributed | 153 passed | 152 / 153 | `TC-CFG-612` | 1 passed |

两份 JUnit 均为 `failed=0`、`error=0`、`skipped=0`。原始通过数恰好仍为 155/153 只是集合替换后的数值巧合，不能据此写成当前正式 TC 已 155/155、153/153 全绿。`TC-CFG-612` 必须在目标 variant 的当前分支 RAGFlow 服务以 `DOC_ENGINE=gaussdb` 启动后，通过真实 `/api/v1/system/gaussdb/status` HTTP 链路补跑。

### 当前执行基线

| 项目 | 值 |
| --- | --- |
| 执行日期 | 2026-08-12 |
| 分支 | `gaussdb-adaptation-combined-test` |
| 执行时 HEAD | `a7f8fc35c3e683a169266e775d232c8f325a61ee` |
| 执行时 tree | `ee81f493a5564be2db0f59ace1965b9587b982a4` |
| DocEngine feat | `4b3bfdec62bb9f30a03d66a01c78d65c18521b00` |
| metadata database feat | `ed55a0b5e2c516470547f5d28b56e050303c261e` |
| Memory Store feat | `4ceb8ab5a1e4225531ba4f38bd04b64338076617` |
| integration tests | `ced6357f5d5e25770f1747461954bfbd59ca8acd` |

执行环境同时设置 `DB_TYPE=gaussdb` 与 `DOC_ENGINE=gaussdb`，使元数据库、DocEngine 和 Memory Store 都使用 GaussDB；01–05 的正式 TC 统计仍只采用本文既有 DocEngine 测试方案口径。Centralized 证据为 A 兼容、1024/1536/3072 维；Distributed 证据为 ORA、C3/D3/S6、1024 维。两边均使用专用 integration schema。

### 当前固定结果

| 环境 | JUnit | pytest 时间 | JUnit run ID | SHA-256 |
| --- | --- | ---: | --- | --- |
| Centralized | `test/integration/artifacts/gaussdb-centralized-clean-20260812-151227.xml` | 560.095s | `1bf5699948fe4cd184180b40b25d84b0` | `616a4ef79be31fe22af00cc09b1c52cd6e296af4c37e55f780283aac3bbd1c4a` |
| Distributed | `test/integration/artifacts/gaussdb-distributed-current-20260812-141912.xml` | 2174.307s | `1d6eb92c68964805a649184b7e64511e` | `dd246503511603590e0dc0ae376f538aabbc4122c7833224cbd47f30708b6413` |

评审后改变了断言或 node 名称的以下正式 TC 均在两份当前 JUnit 中各出现一次并通过：

| TC | 当前 pytest node | Centralized | Distributed |
| --- | --- | --- | --- |
| TC-MGF-803 | `test_tc_mgf_803_greater_than_filters_numeric_values_and_rejects_non_numeric` | Passed | Passed |
| TC-WRT-112 | `test_tc_wrt_112_insert_rejects_mismatched_vector_dimension_without_row` | Passed | Passed |
| TC-RET-603 | `test_tc_ret_603_jsonb_array_aggregation` | Passed | Passed |
| TC-RET-704 | `test_tc_ret_704_tag_features_stay_out_of_gaussdb_sql_score` | Passed | Passed |
| TC-SQL-701 | `test_tc_sql_701_aggregate_sql_fetches_real_source_chunks` | Passed | Passed |

本轮没有重新采集 coverage，因此下方 2026-07-27 coverage 与 HTML 继续作为冻结历史证据，不得改写为当前 HEAD 覆盖率。最终独立复核确认 Centralized 与 Distributed 专用 schema、以及误用后已清理的 `zlw` schema 中，`ragflow_it_*`/`ragflow_mem_*` 测试表与索引残留均为 0；测试 KB、用户和文档残留也为 0。

另有 6 个未编号 Memory Store 集成函数在两环境分别通过，但它们不属于 01–05 的 155/153 正式 TC。其 fixture 目前会关闭共享连接池，连续同进程执行会产生测试代码假失败，且本轮没有形成一份覆盖全部 6 个目标函数的固定 JUnit；因此这里只记录为补充验证，不计入正式 TC、Covered 状态或本文 coverage。

## 冻结历史结论（2026-07-27）

在同一冻结代码树 `879d74e4b3c68d14c77b4d3f871f3fe1798f12f8` 上，01–05 的正式 GaussDB 集成测试已在真实 Centralized 和 Distributed 环境重新执行并全部通过：

| 环境 | 正式适用 TC | pytest items | pytest 结果 | 退出码 |
| --- | ---: | ---: | --- | ---: |
| Centralized | 155 | 155 | 155 passed | 0 |
| Distributed | 153 | 153 | 153 passed | 0 |

两套结果均为 `failed=0`、`error=0`、`skipped=0`、`xfailed=0`。06 为 E2E，不在本报告范围内。

## 冻结历史基线

| 项目 | 值 |
| --- | --- |
| `GOAL_STARTED_AT` | `2026-07-27T12:37:19.2726470Z` |
| 分支 | `gaussdb-adaptation-combined-test` |
| HEAD | `d5530a785e1dde0d67a4bbd5c88978ef8d003671` |
| tree | `879d74e4b3c68d14c77b4d3f871f3fe1798f12f8` |
| DocEngine / 集成代码来源 | `7314e6a28704fc710fff176c38369402aa4da6ab` |
| 方案 / E2E 文档来源 | `d5530a785e1dde0d67a4bbd5c88978ef8d003671` |

两套 pytest 均在 `GOAL_STARTED_AT` 后启动，期间没有 checkout、rebase、autosquash 或生产/测试代码变更。

## 冻结历史正式 TC 与 node 审计

| 方案 | 正式集成 TC |
| --- | ---: |
| 01 配置初始化与接入状态 | 10 |
| 02 写入链路 | 62 |
| 03 metadata filter | 15 |
| 04 检索链路 | 47 |
| 05 Text-to-SQL | 21 |
| **合计** | **155** |

环境适用性为 Both 148、Centralized 2、Variant-specific 5。因此 Centralized 执行 155 项，Distributed 执行 153 项。155 个 TC 对应 155 个不同编号主函数；文档 node、代码函数和 JUnit item 三方比对的缺失、重复、未知、失效均为 0。

## 冻结历史真实环境与执行

| 环境 | 实际能力证据 | 执行时间（UTC） | JUnit run ID |
| --- | --- | --- | --- |
| Centralized | UTF8 / A 兼容；集中式拓扑；1024、1536、3072 维；Ustore、默认 Ustore、VectorDB 开启；可写 | `13:00:42–13:14:41` | `94abbdf27e88486199a4827b35cdddf8` |
| Distributed | UTF8 / ORA；C3/D3/S6；1024 维；Ustore、默认 Ustore、VectorDB 开启；可写 | `14:34:21–15:06:58` | `85b7bd8f6117492fb760da40293d2f73` |

两套均使用环境指南指定的专用 schema、真实 GaussDB 驱动和当前分支生产 adapter/service。切换 Distributed 前已重配 RAGFlow，未复用 Centralized 连接池。密码、DSN 和 API Key 没有写入报告。

实际运行入口为：

```text
docker exec docker-ragflow-cpu-1 bash /ragflow/test/integration/artifacts/run_gaussdb_centralized_goal.sh
docker exec docker-ragflow-cpu-1 bash /ragflow/test/integration/artifacts/run_gaussdb_distributed_goal.sh
```

## 冻结历史 `deselected` 的含义

`deselected` 是 pytest 在 collection 阶段主动排除的 item，不是失败、error、skip 或 xfail：

- Centralized 的 1 项是未编号的既有补充函数 `test_metadata_filter_complex_pushdown_full_chain`；它不是 01–05 的正式 TC，也不计入 155。
- Distributed 的另 2 项是 Centralized 专属 TC；加上同一补充函数，合计为 3 deselected。

因此，所有正式适用 TC 都实际执行并通过；报告没有把 deselected 计入通过数。

## 冻结历史固定产物、覆盖率与清理

| 产物 | mtime（UTC） | SHA-256 | 覆盖率 |
| --- | --- | --- | --- |
| Centralized JUnit | `2026-07-27T13:14:33.4694336Z` | `f903824140620a10a60e29943072f55e5a051225e3825aa6c2d7e288db04239b` | — |
| Centralized coverage | `2026-07-27T13:14:33Z` | `b9a1223c4f4155f208760cf6b7c27b109ccc701b0532794b38290494b53c453a` | line 81.71%，branch 70.05% |
| Distributed JUnit | `2026-07-27T15:06:54.1389446Z` | `5466e868390c2fce2b9331f7efa8354ffbe5df616b6d09f270944499cee53ae7` | — |
| Distributed coverage | `2026-07-27T15:06:53Z` | `5c7ff977340aa475284a791ad4b7467ce95bae8470885fbb274bf4e775fd729a` | line 81.81%，branch 69.93% |

双环境的覆盖率比较及逐文件明细见 [08-集成测试覆盖率.html](08-集成测试覆盖率.html)。覆盖率用于展示本次集成路径覆盖，不替代 01–05 的 TC 场景完成度。

每套执行结束后均使用独立连接复核：本轮 `ragflow_it_*` 表、索引、函数，以及 KB、用户、文档测试数据残留均为 0。

## 冻结历史问题归因

- `IT-ISSUE-115`：Distributed 长测中共享 SSH transport 失效，属于本地隧道环境问题。改为每条数据库 TCP 连接独立 SSH session 后，最终 153/153 通过且残留为 0。
- `IT-ISSUE-116`：TC-SQL-006 曾单次瞬态失败；真实定向复现和最终完整顺序均通过，未形成测试或生产缺陷证据。
- 无活跃生产缺陷、无未通过正式 TC。

详细台账位于 `D:/RAGFlow/gaussdb-integration-test-audit-issues.md`，该文件不含秘密信息。
