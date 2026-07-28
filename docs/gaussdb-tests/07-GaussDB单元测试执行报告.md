# GaussDB 单元测试执行报告

> **冻结历史报告**：下述 570/570 结果仅适用于“基线与可追溯性”所列执行代码树，本报告不得作为当前 HEAD 已通过的证明。当前分支已另行复跑三个 GaussDB feat 提交包含的 22 个无需密钥单测模块（674 passed、1 个真实 GaussDB opt-in 用例 skipped、0 failed），但未重建本报告所附的冻结 JUnit、coverage 与 traceability artifact，因此以下统计仍保持历史值。

## 结论

本次记录的 GaussDB 单元测试集合共 **570 项，570 passed，failed/error/skipped 均为 0**。执行覆盖 01–05 中要求单元或组件单元验证的 **394 个 TC**；文档中的 450 个唯一 pytest node 与代码中的 450 个测试函数双向一致，未发现失效 node、未映射函数或未编号缺口。

本报告不包含真实数据库集成测试或 06 E2E；它们分别见 [08-GaussDB集成测试执行报告.md](08-GaussDB集成测试执行报告.md) 和 06 测试方案。

## 基线与可追溯性

| 项目 | 值 |
| --- | --- |
| pytest 执行时间 | 2026-07-27 17:16:56 +08:00 |
| 执行时 HEAD | `e78b6a761e418ed3fb0990a9ce5c583160bb3cff` |
| 执行时 GaussDB 代码基线 | `929586d6ef4b380ed0a4d5f7c5fa268f16b66a26` |
| autosquash 后 DocEngine commit | `7314e6a28704fc710fff176c38369402aa4da6ab` |
| autosquash 后当前文档 / E2E commit | `d5530a785e1dde0d67a4bbd5c88978ef8d003671` |
| 覆盖率数据 | `test/unit_test/artifacts/929586d-20260727-1630/coverage.data` |
| 追踪审计 | `test/unit_test/artifacts/929586d-20260727-1630/traceability-audit.json` |

执行发生在 autosquash 之前；autosquash 仅将已确认的最终代码和测试补丁合入所属提交。为避免提交哈希改写造成误解，保留了执行时 HEAD 与改写后的提交号两者。

## 执行范围与结果

执行的是 16 个 GaussDB 单元测试模块：配置/部署、连接池与 DDL、写入/检索、metadata filter、Text-to-SQL、service 与 API 编排。命令使用 pytest、JUnit XML、branch coverage；不连接真实 GaussDB，也不运行 E2E。

| JUnit 分组 | pytest items | passed | failed | error | skipped |
| --- | ---: | ---: | ---: | ---: | ---: |
| `rag-utils.xml` | 466 | 466 | 0 | 0 | 0 |
| `api-services.xml` | 73 | 73 | 0 | 0 | 0 |
| `support.xml` | 19 | 19 | 0 | 0 | 0 |
| `deploy.xml` | 12 | 12 | 0 | 0 | 0 |
| **合计** | **570** | **570** | **0** | **0** | **0** |

测试证据文件的 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `rag-utils.xml` | `342d9fbf52e2806837ee0dbd99467724c39f0526d4c2f86ac16949904a612a48` |
| `api-services.xml` | `ded5da224d7a40c7b573611e00a8c9768068470d5c3dbf2922b87d80d2e1584f` |
| `support.xml` | `2a8ea02828fc0ed79b221a05c6f0ac320613d92e6ad6c5f5eee5195408b2244c` |
| `deploy.xml` | `53089c1e64caabcbfbd0fa7cc637847ccce6b89b10eddc0771a92a43a0a33d12` |
| `coverage.data` | `1883cfb3084c409ea35fb1f37ad0f7b80a6447fdef61fa41d2ad827e60c26ade` |
| `traceability-audit.json` | `6ea896d7dc256ec9959b652183f017ef6ba9199b4d37d79fa47d2c3771cf66f0` |

## TC 与代码映射审计

| 检查项 | 结果 |
| --- | ---: |
| 01–05 单元 / 组件单元 TC | 394 |
| 文档中的唯一 pytest node | 450 |
| 实际唯一测试函数 | 450 |
| 实际 pytest items | 570 |
| 文档有 TC 但无 node | 0 |
| 代码有函数但未映射 | 0 |
| 失效文档 node | 0 |
| 未编号函数 | 0 |

参数化会使 pytest item 数高于唯一测试函数数；它不表示一个 TC 被多个主函数替代。

## 覆盖率

覆盖率包含两种口径，不能混用：

| 口径 | statements | statement coverage | branches | branch coverage | 含义 |
| --- | ---: | ---: | ---: | ---: | --- |
| 本次测量的所有导入模块 | 8,450 | 49.01% | 3,452 | 40.21% | 含大型 service/API 依赖模块，非 GaussDB 新增代码的验收阈值 |
| 以 `929586d` blame 归属的可执行增量 | 2,684 | 97.95% | 1,090 | 94.50% | GaussDB 适配增量的审计口径 |

完整逐文件 HTML 报告见 [07-单元测试覆盖率-html/index.html](07-单元测试覆盖率-html/index.html)。其中展示命中行、缺失行和 branch 详情；它由本次保留的 coverage 数据生成。

## 结论与限制

- 570 个单元 pytest item 全部通过，且 01–05 的单元 TC 映射完整。
- 该结果只证明单元/组件单元行为；真实 SQL、事务、schema 隔离和向量能力由 08 的双环境集成报告证明。
- 报告不包含密码、DSN、API Key 或完整连接串。
