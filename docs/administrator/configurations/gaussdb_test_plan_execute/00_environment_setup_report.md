# 00 - 测试环境搭建与前置条件测试报告

## 1. 环境结论

- 执行批次：`20260727_combined_001`
- 最终只读验收时间：2026-07-28 10:39:26（Asia/Shanghai）
- 环境就绪：**PASS**
- 受管服务：8/8 存活且进程身份匹配
- 对照组：mysql metadata + infinity DocEngine
- 实验组：gaussdb metadata + GaussDB DocEngine
- 实验组 GaussDB 数据库：`zws_test2`；metadata 与 DocEngine schema 隔离
- GaussDB 编码：server=UTF8，client=UTF8
- 组间隔离：PASS
- 明文敏感信息检查：PASS
- 00 组正式用例数：0；环境证据只用于前置与终态校验
- 执行顺序：后续每个正式用例固定先 control、后 experiment

当前验收确认 API、Admin、worker、sync 共 8 个服务可用，数据库、DocEngine、Redis 和对象存储健康；两组端口、缓存库、桶、运行目录和数据库范围相互隔离。

## 2. 问题归属判定

口径：`实验组独有` = 仅实验组失败；`对照组也存在` = 双组共同失败；`对照组独有` = 仅对照组失败；`两组均阻塞` = 双组均因同一前置条件阻塞。
本批次最终环境验收没有记录需要归属的环境失败。

<!-- ISSUE_ATTRIBUTION_START -->
| Finding | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
<!-- ISSUE_ATTRIBUTION_END -->

## 3. 环境终态

| 检查项 | 对照组 | 实验组 |
|---|---|---|
| API ready | True | True |
| Admin ping | True | True |
| metadata | green | green |
| DocEngine | green | healthy |
| Redis | green | green |
| 对象存储 | green | green |

正式证据：`runs/20260727_combined_001/evidence_private/00_environment_setup/environment_validation.json`。
