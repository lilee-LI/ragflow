# GaussDB E2E 测试执行报告

> **全量基线 + 定向回归报告**：2026-07-28 在两套环境各全量执行 32 条；2026-08-15
> 只对生产修复影响的 TC-E2E-802 完成双环境定向回归。下述有效统计按 TC 采用最后一次有效结果，
> 不把单条定向回归冒充当前 HEAD 的 64 个实例全量重跑。

## 结论

2026-07-28 在同一套正式 TC、E2E 代码和业务语料上，分别完成了真实 GaussDB
Centralized 与 Distributed 环境的 32 条正式 E2E。2026-08-15 修复 TC-E2E-802 对应的
GaussDB pagerank 默认权重后，又在两套真实环境分别完成该 TC 的定向回归。按 TC 采用最后一次
有效结果后，两套环境结果一致：

| 环境 | 正式 TC | passed | failed | error | skipped | xfailed | 场景覆盖率 | 实际通过率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Centralized | 32 | 29 | 3 | 0 | 0 | 0 | 100% | 90.63% |
| Distributed | 32 | 29 | 3 | 0 | 0 | 0 | 100% | 90.63% |
| **双环境实例合计** | **64** | **58** | **6** | **0** | **0** | **0** | **100%** | **90.63%** |

三条 failed 均已排除环境和测试代码问题：

- TC-E2E-501/502/503：同一个 RAGFlow 通用 table parser 配置合并缺陷
  `BUG-E2E-003`。
- TC-E2E-802：历史 `BUG-E2E-004` 已重新归因为 GaussDB adapter 默认 pagerank 权重缺陷；
  最小修复后集中式、分布式均通过。

因此，本轮 E2E 的**执行、审计和归因已完成**，但产品验收仍不是全绿；在 `BUG-E2E-003`
修复并完成双环境回归前，不得写成 `32/32 passed`。`BUG-E2E-004` 已修复并完成对应定向回归，
但本次没有重新执行其余 31 条，不得据此宣称当前 HEAD 已完成全量复跑。

## 执行基线与提交边界

| 项目 | 值 |
| --- | --- |
| 分支 | `gaussdb-adaptation-combined-test` |
| 执行时生产代码 / 06 HEAD | `1254e3c489fc3ff3d43334c612175d7d6c717b68` |
| 执行开始时 E2E commit | `bdfe0521e4c90ce9bb6f00718cf6059b828da8ba` |
| E2E commit 父提交 | `bcf4bf8db74bc94e220538070564938510f6ef85` |
| E2E 修正 autosquash 后 commit | `8f252b8646229de6ab79a6a659200b19340e5e6a` |
| 2026-08-15 TC802 回归对应生产提交 | `bdc2253ae3302057223e85ed9e039f734ced2158` |
| 2026-08-15 TC802 回归对应 E2E 提交 | `d33a5fbdbb03943360b39d5bb33c5ef319739f61` |
| 测试方案 | `docs/gaussdb-tests/06-UI-E2E测试方案.md` |
| 测试模块 | `test/playwright/e2e/test_gaussdb_docengine_e2e.py` |

2026-07-28 全量执行期间生产代码和 06 没有变化。E2E 专用 runner/helper 的问题按最小修改原则
修正后，只定向复跑受影响 TC；当时最终修正 autosquash 到 `8f252b864`。2026-08-15 的 TC802
回归使用与最终 `bdc2253ae` / `d33a5fbdb` 相同的生产与 E2E tree；产物目录名保留 autosquash 前
短哈希 `1f13113`。本报告不把被后续复跑推翻的中间结果计入通过率。

当前 DocEngine E2E commit 共 25 个文件；2026-08-15 为可靠复用环境和避免 Windows D 盘源码
bind mount 冷启动，额外修改的 runner 文件只有：

- `docker/docker-compose.gaussdb-dual-e2e.yml`
- `docker/entrypoint.gaussdb-dual-e2e.sh`
- `docker/run-gaussdb-e2e.ps1`

完整文件清单：

```text
docker/Dockerfile.gaussdb-dual-e2e
docker/docker-compose.gaussdb-dual-e2e.yml
docker/entrypoint.gaussdb-dual-e2e.sh
docker/gaussdb-e2e-model-preflight.py
docker/gpu-embedding-proof.py
docker/run-gaussdb-e2e.ps1
docker/seed-gaussdb-e2e-account.py
test/fixtures/gaussdb/.gitattributes
test/fixtures/gaussdb/basic.txt
test/fixtures/gaussdb/basic_canary.txt
test/fixtures/gaussdb/batch_a.txt
test/fixtures/gaussdb/batch_b.txt
test/fixtures/gaussdb/large_12mb.txt
test/fixtures/gaussdb/long_text.txt
test/fixtures/gaussdb/mixed_en_canary.txt
test/fixtures/gaussdb/mixed_target.txt
test/fixtures/gaussdb/mixed_zh_canary.txt
test/fixtures/gaussdb/mother.md
test/fixtures/gaussdb/multikb_a.csv
test/fixtures/gaussdb/multikb_b.csv
test/fixtures/gaussdb/table.csv
test/fixtures/gaussdb/unicode_zh.txt
test/fixtures/gaussdb/unicode_zh_canary.txt
test/playwright/conftest.py
test/playwright/e2e/test_gaussdb_docengine_e2e.py
```

没有把生产代码、单元测试、集成测试或运行产物混入 DocEngine E2E commit。历史上误加入顶部
E2E commit 的 `docs/administrator/configurations/gaussdb_test_plan_execute/runs/` 与 `runtime/`
已从原提交移除并由 `.gitignore` 精确排除；同目录测试脚本和正式报告继续保留。

## TC、函数与 node 审计

| 检查项 | 结果 |
| --- | ---: |
| 06 正式 TC | 32 |
| 测试模块 | 1 |
| 带 TC 编号的测试函数 | 32 |
| pytest collect node | 32 |
| 06 有 TC、代码无 node | 0 |
| 代码有 node、06 无 TC | 0 |
| 重复映射 | 0 |
| 未编号函数 | 0 |
| 失效 node | 0 |
| Mock / 伪 E2E | 0 |

双环境正式矩阵为 64 个唯一“环境 × TC”实例。因为修正测试问题后执行了定向复跑，所有 JUnit
一共记录 72 个实际 testcase；最终证据集合包含 70 个 testcase，并按 TC 采用最后一次有效结果
去重为 64 个正式实例。两次使用旧函数名的长测命令均为 `collected 0 items`，没有进入 pytest，
不计入执行项。

## 真实环境

| 项目 | Centralized | Distributed |
| --- | --- | --- |
| RAGFlow | `v0.26.4`，镜像 `ragflow-gaussdb-e2e-dual:v0.26.4-deps` | 同左 |
| GaussDB | Kernel 507.0.0 build `1268bd4d` | Kernel 507.0.0 build `19fa72ae` |
| 兼容与拓扑 | 集中式，实际 1024 维；环境同时具备 1536/3072 维能力 | ORA，C3/D3/S6，`enable_vectordb=on`，1024 维 |
| 索引 | 当前实际维度的 GSDiskANN + 全文索引 | 1024 维 GSDiskANN + 全文索引 |
| schema / user | 环境指南指定的 E2E `zlw` | 环境指南指定的 E2E `zlw` |

公共执行环境：

- GPU：NVIDIA GeForce GTX 1050 Ti 4 GiB。
- embedding provider：`CUDAExecutionProvider`。
- embedding：`BAAI/bge-large-en-v1.5`，1024 维，RAGFlow 容器通过 `tei` 网络别名完成真实请求。
- embedding batch：8；未观察到 OOM、请求失败或进度不稳定，因此没有降为 4。
- 聊天模型：`glm-4.5-air`；正式 profile 前真实 preflight 返回 HTTP 200。
- Playwright 1.57.0，Chromium 143.0.7499.4。
- Docker 29.6.2，Docker Compose 5.3.1。
- 账号由 `docker/seed-gaussdb-e2e-account.py` 准备；数据库和模型凭据只从
  `D:/RAGFlow/GaussDB_environment_access_guide.md` 读取，本文不复制密码、DSN 或 API Key。

## 执行编排

统一入口：

```powershell
& .\docker\run-gaussdb-e2e.ps1 `
  -Variant centralized|distributed `
  -ResultName <profile-name> `
  -NodeList '<显式 pytest nodes，以分号分隔>'
```

每个环境依次执行：

1. TC101/201 短门禁。
2. 普通 profile。
3. TC1102/1103 CUDA batch 8 长测。
4. TC1101 独占故障代理。
5. TC1105 独占 RAGFlow 服务重启。
6. 启用 `CHUNK_FEEDBACK_ENABLED=true` 后集中执行 TC801/802。

runner 只在 variant、feedback、batch、DocEngine、TEI 模型、RAGFlow 镜像或健康状态不兼容时重建
对应服务；配置一致时复用健康环境。TC1105 因测试目标就是服务重启，必须真实重建 RAGFlow。

普通 API/chat/model read timeout 为 1200 秒；pytest suite 防挂死上限为 6 小时。它们只是防止环境
无限挂起，不是产品性能 SLA，也不以“超过 300 秒”判定产品失败。

## 最终逐 TC 结果

`P` 表示通过，`F` 表示已确认生产缺陷。耗时是 pytest testcase wall time，只用于诊断和排期。

| TC | Centralized | Distributed | 最终归因 |
| ---: | ---: | ---: | --- |
| 101 | P 144.613s | P 167.521s | — |
| 201 | P 69.826s | P 64.220s | — |
| 202 | P 65.880s | P 76.072s | — |
| 203 | P 65.700s | P 88.152s | — |
| 204 | P 34.327s | P 51.973s | — |
| 205 | P 39.744s | P 53.074s | — |
| 206 | P 41.674s | P 258.143s | 历史 BUG-E2E-005 未复现，已排除 |
| 207 | P 35.383s | P 56.543s | — |
| 401 | P 60.617s | P 132.402s | — |
| 402 | P 49.697s | P 88.345s | — |
| 403 | P 69.859s | P 126.388s | — |
| 404 | P 79.300s | P 321.415s | — |
| 405 | P 71.179s | P 136.594s | — |
| 406 | P 125.238s | P 130.423s | 纯中文 ngram |
| 407 | P 129.623s | P 199.428s | — |
| 408 | P 130.826s | P 274.801s | 中英混合检索与回答 |
| 501 | **F 42.084s** | **F 365.824s** | BUG-E2E-003 |
| 502 | **F 28.694s** | **F 110.639s** | BUG-E2E-003 |
| 503 | **F 33.236s** | **F 100.059s** | BUG-E2E-003 |
| 504 | P 75.359s | P 244.737s | 纯英文检索与回答 |
| 701 | P 117.881s | P 225.641s | — |
| 801 | P 180.031s | P 294.772s | feedback 落库与幂等通过 |
| 802 | P 167.571s | P 452.835s | BUG-E2E-004 已修复；双环境定向回归通过 |
| 901 | P 62.388s | P 140.567s | — |
| 902 | P 71.278s | P 167.874s | — |
| 904 | P 28.717s | P 145.833s | — |
| 1001 | P 49.476s | P 70.822s | — |
| 1101 | P 135.365s | P 202.982s | — |
| 1102 | P 3261.175s | P 4514.238s | 12 MiB，2706 chunks，CUDA batch 8 |
| 1103 | P 875.657s | P 1377.185s | 1 MiB，1357 chunks，CUDA batch 8 |
| 1104 | P 90.294s | P 87.123s | — |
| 1105 | P 571.750s | P 1059.856s | 真实服务重启后数据/索引可用 |

按每条 TC 最后一次有效结果求和，Centralized testcase 时间为 7004.442 秒，Distributed 为
11786.481 秒。这些时间跨越 2026-07-28 全量基线和 2026-08-15 TC802 定向回归，不代表一次
连续 suite 的总耗时，也不包含环境切换、RAGFlow 冷启动和失败诊断时间。

## 语言检索与固定语料

三类语言场景均在双环境通过：

| 场景 | TC | 目标语料 | 干扰语料 | 核心断言 |
| --- | --- | --- | --- | --- |
| 纯英文 | TC504 | `basic.txt`：Atlas-A17、4 C、Elena Brooks | `basic_canary.txt`：Helios-H22、8 C | 答案含唯一目标事实，不含 canary；search/chat reference 和 DB 对应 |
| 纯中文 | TC406 | `unicode_zh.txt`：苍穹冷却泵、林岚、每分钟48升 | `unicode_zh_canary.txt`：星河冷却泵、赵岳、每分钟42升 | 中文 ngram 只召回目标，chunk 高亮、答案、引用、metadata 和 DB 原文一致 |
| 中英混合 | TC408 | 星桥机器人 + EdgePilot X7 + SLA 99.95% + 38ms | 中文侧和英文侧各一个只满足部分条件的 canary | 组合查询只命中 target；答案含四个组合条件和因果，不含 99.50%/83ms |

TC1102/1103 的固定资产不是重复文本：每条记录具有唯一事件编号、责任人、依赖和结果，并在首、
中、尾设置不同事实锚点；测试分别验证三个锚点和不存在的 canary。

## 失败归因

### BUG-E2E-003：TC501/502/503

- 分类：RAGFlow 通用生产缺陷，不是 GaussDB adapter 专属。
- 环境证据：两套 GaussDB、RAGFlow、CUDA embedding、账号和网络均健康；其它解析/检索 TC 正常。
- 测试证据：Distributed 初次运行中 TC502/503 曾被 30 秒客户端超时遮挡；修正 E2E 客户端 timeout
  后定向复跑，二者与 TC501 一样稳定进入相同 parser traceback。
- 场景依据：创建 `parser_id=table` 的 dataset、上传合法 UTF-8 CSV 并解析，是 RAGFlow 现有真实流程。
- 生产证据：`api/db/services/knowledgebase_service.py::update_parser_config` 的 `dfs_update`
  在旧字段为 `None`、新 table 配置字段为 `list` 时执行
  `assert isinstance(old[k], list)`；CSV 已读取，但在 embedding 和 DocEngine 写入之前中断。
- 影响范围：该失败发生在通用 parser 配置合并层，理论上所有经过此流程的 DocEngine 都可能受影响；
  本报告只把 Centralized/Distributed GaussDB 的真实复现计为已验证。

### BUG-E2E-004（已修复）：TC802

- 最终分类：GaussDB adapter 默认 pagerank 权重缺陷，不是 feedback 写入缺陷。
- 历史行为：TC801 已在双环境证明目标 pagerank 精确 +1、同 KB/另一 KB canary 不变、重复 feedback
  幂等、非法输入不改变数据库；但 TC802 的普通检索仍保持原排序。
- 根因：普通未配置 tag KB 的 `label_question(...)` 返回 `None`。ES 与 OceanBase 的现有路径仍会
  消费持久化 pagerank，而 GaussDB `_parse_match_expressions` 把缺省 `pagerank_weight` 初始化为 0，
  因而生成的查询不包含 pagerank 排序贡献。
- 最小修复：GaussDB 缺省权重改为 10，与检索层既有默认语义一致；显式
  `{"pagerank_fea": 0}` 仍可关闭，显式其它值仍覆盖默认值。生产修复为 `bdc2253ae`。
- 验证：相关单元测试 1 passed，PageRank SQL/显式零权重/不重复计分定向测试 3 passed；随后
  TC802 在 Centralized、Distributed 真实 feedback profile 分别 1/1 passed，目标排序真实前移，
  feedback 写入、幂等、canary、API 与 GaussDB 最终状态断言保持不变。
- 验证边界：只宣称 GaussDB Centralized/Distributed 已真实回归；未把其它 DocEngine 的静态对照
  冒充其 E2E 结果。

### 已排除的 BUG-E2E-005

TC206 在双环境均完整执行 PATCH、列表读取、GaussDB 最终状态、检索和清理断言，`tag_kwd` 一致。
2026-07-23 的历史观察在当前固定基线无法复现，因此不再作为当前生产缺陷，也不提出生产修复。

## 本轮修正的测试问题

以下问题均由最小 E2E 修改解决，并已定向复跑：

| 影响 TC | 问题 | 修正与复验 |
| --- | --- | --- |
| Centralized gate | cleanup 使用已失效认证，首次 teardown 401 | cleanup 获取新的真实 API auth；TC101/201 门禁 2/2 通过 |
| TC408 | 对模型回答的因果句式 regex 过窄 | 保留目标事实/干扰事实/引用断言，只修正合法中文表达匹配；双环境通过 |
| Distributed TC401/408 | multipart upload 使用固定 30 秒客户端 read timeout | 与 E2E API timeout 统一为 1200 秒；两条定向复跑通过 |
| Distributed TC407 | 驱动返回 list row，被测试放入 set 时不可 hash | 测试只在构造独立 oracle 前转为 tuple；定向复跑通过 |
| Distributed TC502/503 | 登录/上传 30 秒超时遮挡 parser 真实失败 | timeout 修正后均复现 BUG-E2E-003 |
| 全部 profile | runner 曾倾向重复冷启动 | 按实际 runtime contract 自动复用，只重建不兼容服务；保留显式强制重建 |

没有通过降低断言、改用 mock、缩小业务流程或回退 CPU embedding 来消除失败。

## JUnit 与运行证据

以下为最终证据集合。结果列依次为 `passed/failed/error/skipped`。路径均相对于
`test/playwright/artifacts/e2e/gaussdb-dual/`；原始文件保持未跟踪，不进入 docs commit。

| profile 目录 | items | 结果 | pytest 时间 | JUnit SHA-256 | mtime |
| --- | ---: | --- | ---: | --- | --- |
| `centralized/centralized-gate-rerun1-1254e3c` | 2 | 2/0/0/0 | 215.500s | `340e8c40cbc8930cfae763958821fcba9302087c09c520bb06fd15e99ca500a7` | 2026-07-28 11:19:50 +08:00 |
| `centralized/centralized-normal-1254e3c` | 24 | 20/4/0/0 | 1616.308s | `438c46267e1ae637dee8af656e4f07bbdcb954387d370c17b544253bda855d09` | 2026-07-28 11:48:02 +08:00 |
| `centralized/centralized-tc408-rerun1-1254e3c` | 1 | 1/0/0/0 | 133.784s | `abbdc64def481a76eb890a357792b5e45491951ee9c8fdac611850b4e96fc641` | 2026-07-28 11:51:38 +08:00 |
| `centralized/centralized-large-batch8-rerun1-1254e3c` | 2 | 2/0/0/0 | 4138.142s | `ac9493a19ea5f6c6ea90155fad27bb2ae2767cffdfbc90288ba7ee1929b10726` | 2026-07-28 13:02:01 +08:00 |
| `centralized/centralized-tc1101-1254e3c` | 1 | 1/0/0/0 | 136.896s | `01504ff444224b35540d34076f72392c14cfe0639b5fded0529d5b34004a3826` | 2026-07-28 13:05:48 +08:00 |
| `centralized/centralized-tc1105-1254e3c` | 1 | 1/0/0/0 | 572.871s | `01f07f2d203ca17957aa5894b696f287903f980281ba7d67be61772c24652e7b` | 2026-07-28 13:16:20 +08:00 |
| `centralized/centralized-feedback-1254e3c` | 2 | 1/1/0/0 | 265.252s | `28e3b5ebbba68bbffa54d33a4686c0c3a68d090aa071e34632505289caac69c6` | 2026-07-28 13:26:07 +08:00 |
| `distributed/distributed-gate-1254e3c` | 2 | 2/0/0/0 | 233.428s | `d2f224ed30a5204bea45455805edeab79e15dcf9c10226e45a96e21a8f76cb4d` | 2026-07-28 13:35:22 +08:00 |
| `distributed/distributed-normal-1254e3c` | 24 | 18/6/0/0 | 3862.334s | `afd92e76b24744d9037c4277e8ff141b68d569080d68a0523280bd79c79e952b` | 2026-07-28 14:41:03 +08:00 |
| `distributed/distributed-recheck-401-407-408-1254e3c` | 3 | 3/0/0/0 | 610.250s | `aa88b49592c6ae4f7ccfda2410f5c4bbf78ee1dce7de8ac06585d58b4b5a3b9a` | 2026-07-28 14:53:52 +08:00 |
| `distributed/distributed-recheck-502-503-1254e3c` | 2 | 0/2/0/0 | 212.542s | `398d0ec57b733b13ff01bbef7e9d40915fba3ac2e5fa45bafdc379e901f367b6` | 2026-07-28 14:58:38 +08:00 |
| `distributed/distributed-large-batch8-rerun1-1254e3c` | 2 | 2/0/0/0 | 5893.237s | `e5432e3bbbacc0266b61fd5bc934eb324fedb5969342f893c149de0d811e4866` | 2026-07-28 16:38:31 +08:00 |
| `distributed/distributed-tc1101-1254e3c` | 1 | 1/0/0/0 | 204.042s | `3dece8208fc8ae4d29b9df858bdc74cef2829d968030d044c482be771a9bb191` | 2026-07-28 16:43:50 +08:00 |
| `distributed/distributed-tc1105-1254e3c` | 1 | 1/0/0/0 | 1060.885s | `3255ab6a8a36585cee2aa12b0b7a9b25bea39ec7305361cb518f0cb9432b8be8` | 2026-07-28 17:02:35 +08:00 |
| `distributed/distributed-feedback-1254e3c` | 2 | 1/1/0/0 | 479.693s | `798da84a16dcdfdd7550514e308a2ceef3bba6e2f8b6ea1b14c33b29caa530e4` | 2026-07-28 17:20:16 +08:00 |
| `centralized/centralized-tc802-pagerank-default10-r2-linux-copy-reslink-1f13113-20260815` | 1 | 1/0/0/0 | 168.535s | `557f40807f80bf15ae6ab50efbde5168ab33377088e676a7e40de5de2837be4d` | 2026-08-15 10:41:23 +08:00 |
| `distributed/distributed-tc802-pagerank-default10-r4-linux-copy-reslink-1f13113-20260815` | 1 | 1/0/0/0 | 461.081s | `3363b6e7845c639e4c622709550dca114c84b02a371aae27bf6fc197e9beccc9` | 2026-08-15 10:33:02 +08:00 |

中间诊断产物：

- `fresh-centralized-gate-1254e3c`：首次 cleanup auth 测试问题，修正后由正式 gate 结果取代。
- `centralized-large-batch8-1254e3c`、`distributed-large-batch8-1254e3c`：旧 node 名导致
  `collected 0 items`，没有执行测试，不计入结果。
- Distributed normal 中 TC401/407/408 的测试代码问题由三条定向复跑结果取代；TC502/503 的定向
  复跑用于排除 timeout 干扰并确认生产缺陷。

失败截图、HTML、日志与 Playwright trace 位于：

```text
test/playwright/artifacts/e2e/test_gaussdb_docengine_e2e/
```

TC1102 性能明细和 TC1105 重启证据位于：

```text
test/playwright/artifacts/e2e/gaussdb/
```

## 覆盖率

本报告严格区分场景覆盖率、执行通过率和代码覆盖率：

| 指标 | 结果 |
| --- | --- |
| 06 E2E 场景映射覆盖率 | 32/32，100% |
| Centralized E2E 有效通过率 | 29/32，90.63% |
| Distributed E2E 有效通过率 | 29/32，90.63% |
| E2E 自身命中的 production statement/branch coverage | **未采集** |
| 单元 + 集成 + E2E 三层合并增量 coverage | **未采集，不能计算** |

原因是 RAGFlow 后端没有用 `coverage.py` 插桩启动。客户端 pytest coverage 不能代表容器内 production
代码命中；也不能把 07 的单元报告或 08 的集成报告冒充 E2E coverage。

当前只读参考：

- Centralized integration：statement 1613/1974（81.71%），branch 587/838（70.05%）。
- Distributed integration：statement 1615/1974（81.81%），branch 586/838（69.93%）。
- 07 的单元增量报告来自不同执行 HEAD：statement 2629/2684（97.95%），branch
  1030/1090（94.50%）。

由于 E2E production coverage 缺失且单元报告执行 HEAD 不同，不能计算或宣称三层合并覆盖率 100%。

## 最终验收状态

| 类别 | 结果 |
| --- | --- |
| 已完成 | 2026-07-28 两套环境全部 32 条均真实执行；2026-08-15 TC802 双环境定向回归通过；有效结果 29 条/环境通过 |
| 环境问题 | 最终阻断为 0；CUDA、模型、浏览器、网络、账号和两套 GaussDB 均可用 |
| 测试问题 | 已最小修正并定向复跑，无剩余测试代码阻断 |
| 开发缺陷 | BUG-E2E-003 仍影响 TC501/502/503；BUG-E2E-004 已修复并完成 TC802 双环境回归 |
| 未知/失效/未编号/伪 E2E | 全部为 0 |
| 生产代码修改 | `rag/utils/gaussdb_conn.py`：缺省 pagerank 权重 0 → 10；对应单元断言同步更新 |
| E2E commit 范围外修改 | 0 |

缺陷修复后的回归要求：

1. BUG-E2E-003 修复后在两套环境分别复跑 TC501/502/503，并要求三条全部通过。
2. BUG-E2E-004 已完成 TC802 双环境回归；下次全量执行时 TC801 仍须继续证明写入和幂等，
   TC802 仍须证明目标排序真实前移。
3. 复跑不得弱化现有 API/reference/数据库/清理断言，也不得把其它 DocEngine 的静态判断冒充其
   真实 E2E 结果。
