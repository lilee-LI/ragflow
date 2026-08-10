# 08 - File 管理测试报告

## 1. 执行结论

- 执行批次：`20260727_combined_001`
- 正式执行时间：2026-07-28 09:40:19～2026-07-28 10:31:39（Asia/Shanghai）
- 当前计划：`08_file_management.md`、`08_file_supplement.md`
- 覆盖：89/89 个唯一用例，0 缺失、0 额外、0 重复
- 执行方式：按计划组单进程执行；每个用例固定先 control、后 experiment
- 用例对结果：62 PASS、27 FAIL、0 BLOCKED
- 对照组结果：62 PASS、27 FAIL、0 BLOCKED
- 实验组结果：62 PASS、27 FAIL、0 BLOCKED
- 适配回归结论：**PASS**（实验组独有失败 0 个）
- 产品契约现状：仍有未满足契约；按归属单独跟踪，不计作 GaussDB 适配回归
- 正式证据：`runs/20260727_combined_001/evidence_private/08_file/`

本报告仅由上述当前批次的正式证据和现行计划生成。双组共同失败继续保留，但按约定不判定为 GaussDB 适配问题；对照组独有失败也不归因于实验组。

## 2. 问题归属判定

口径：`实验组独有` = control PASS / experiment FAIL；`对照组也存在` = control FAIL / experiment FAIL；`对照组独有` = control FAIL / experiment PASS；`两组均阻塞` = control BLOCKED / experiment BLOCKED；`对照环境阻塞/实验组通过` = control BLOCKED / experiment PASS。

<!-- ISSUE_ATTRIBUTION_START -->
| 用例 | 对照组 | 实验组 | 问题归属 |
|---|---|---|---|
| TC-FM-004 | FAIL | FAIL | 对照组也存在 |
| TC-FM-005 | FAIL | FAIL | 对照组也存在 |
| TC-FM-017 | FAIL | FAIL | 对照组也存在 |
| TC-FM-019 | FAIL | FAIL | 对照组也存在 |
| TC-FM-037 | FAIL | FAIL | 对照组也存在 |
| TC-FM-045 | FAIL | FAIL | 对照组也存在 |
| TC-FM-046 | FAIL | FAIL | 对照组也存在 |
| TC-FM-047 | FAIL | FAIL | 对照组也存在 |
| TC-FM-048 | FAIL | FAIL | 对照组也存在 |
| TC-FM-049 | FAIL | FAIL | 对照组也存在 |
| TC-FM-050 | FAIL | FAIL | 对照组也存在 |
| TC-FM-051 | FAIL | FAIL | 对照组也存在 |
| TC-FM-052 | FAIL | FAIL | 对照组也存在 |
| TC-FM-053 | FAIL | FAIL | 对照组也存在 |
| TC-FM-054 | FAIL | FAIL | 对照组也存在 |
| TC-FM-055 | FAIL | FAIL | 对照组也存在 |
| TC-FM-056 | FAIL | FAIL | 对照组也存在 |
| TC-FM-057 | FAIL | FAIL | 对照组也存在 |
| TC-FM-060 | FAIL | FAIL | 对照组也存在 |
| TC-FM-061 | FAIL | FAIL | 对照组也存在 |
| TC-FM-066 | FAIL | FAIL | 对照组也存在 |
| TC-FILE-VER-001 | FAIL | FAIL | 对照组也存在 |
| TC-FILE-VER-002 | FAIL | FAIL | 对照组也存在 |
| TC-FILE-VER-003 | FAIL | FAIL | 对照组也存在 |
| TC-FILE-VER-004 | FAIL | FAIL | 对照组也存在 |
| TC-FILE-VER-005 | FAIL | FAIL | 对照组也存在 |
| TC-FILE-VER-006 | FAIL | FAIL | 对照组也存在 |
<!-- ISSUE_ATTRIBUTION_END -->

归属统计：实验组独有 0，对照组也存在 27，对照组独有 0，两组均阻塞 0，对照环境阻塞/实验组通过 0。

## 3. 非 PASS 证据摘要

| 用例 | 名称 | 归属 | Finding | 证据摘要 | 代码位置 |
|---|---|---|---|---|---|
| TC-FM-004 | 创建虚拟文件夹（type=virtual） | 对照组也存在 | FM-FOLDER-TYPE-VALIDATION-001 | control accepted an unknown File type or virtual storage semantics differed；experiment accepted an unknown File type or virtual storage semantics differed | api/apps/restful_apis/file_api.py |
| TC-FM-005 | 创建文件夹 - 父文件夹不存在 | 对照组也存在 | FM-MISSING-PARENT-ERROR-001 | control invalid folder request response or zero-delta contract differed；experiment invalid folder request response or zero-delta contract differed | api/apps/restful_apis/file_api.py |
| TC-FM-017 | 文件列表 - 排序和分页 | 对照组也存在 | FM-LIST-PARAMETER-VALIDATION-001 | control File pagination/order validation contract differed；experiment File pagination/order validation contract differed | api/apps/restful_apis/file_api.py |
| TC-FM-019 | 移动文件到目标文件夹 | 对照组也存在 | FM-MOVE-NONFOLDER-DEST-001 | control valid move or non-folder destination safety contract differed；experiment valid move or non-folder destination safety contract differed | api/apps/restful_apis/file_api.py |
| TC-FM-037 | 链接文件夹到数据集（递归展开） | 对照组也存在 | FM-LINK-RECURSIVE-TYPE-FILTER-001 | control recursive folder link or virtual type-filter contract differed；experiment recursive folder link or virtual type-filter contract differed | api/apps/restful_apis/file_api.py |
| TC-FM-045 | 创建提交（add 操作） | 对照组也存在 | FM-COMMIT-ADD-SCOPE-IDOR-001 | control add commit, workspace scope, or cross-tenant write contract differed；experiment add commit, workspace scope, or cross-tenant write contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-046 | 创建提交（modify 操作） | 对照组也存在 | FM-COMMIT-MODIFY-IDOR-001 | control modify commit chain or cross-tenant write contract differed；experiment modify commit chain or cross-tenant write contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-047 | 创建提交（delete 操作） | 对照组也存在 | FM-COMMIT-DELETE-ATOMIC-IDOR-001 | control delete commit functionality, atomicity, or authorization contract differed；experiment delete commit functionality, atomicity, or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-048 | 创建提交（rename 操作） | 对照组也存在 | FM-COMMIT-RENAME-IDOR-001 | control rename commit or cross-tenant write contract differed；experiment rename commit or cross-tenant write contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-049 | 列出提交（分页） | 对照组也存在 | FM-COMMIT-LIST-PARAM-IDOR-001 | control commit listing, parameter validation, or authorization contract differed；experiment commit listing, parameter validation, or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-050 | 获取提交详情 | 对照组也存在 | FM-COMMIT-DETAIL-IDOR-001 | control commit detail or authorization contract differed；experiment commit detail or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-051 | 列出提交中的文件 | 对照组也存在 | FM-COMMIT-ITEMS-IDOR-001 | control commit item listing or authorization contract differed；experiment commit item listing or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-052 | 比较两个提交的差异 | 对照组也存在 | FM-COMMIT-DIFF-IDOR-001 | control commit diff or authorization contract differed；experiment commit diff or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-053 | 获取未提交的更改 | 对照组也存在 | FM-COMMIT-CHANGES-IDOR-001 | control uncommitted changes or authorization contract differed；experiment uncommitted changes or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-054 | 获取提交树（层级结构） | 对照组也存在 | FM-COMMIT-TREE-SNAPSHOT-IDOR-001 | control historical tree stability or authorization contract differed；experiment historical tree stability or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-055 | 获取提交中的文件内容 | 对照组也存在 | FM-COMMIT-CONTENT-IDOR-001 | control historical content or authorization contract differed；experiment historical content or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-056 | 获取文件版本历史 | 对照组也存在 | FM-FILE-VERSIONS-IDOR-001 | control file version history or authorization contract differed；experiment file version history or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-057 | 通过数据集 ID 访问提交（datasets 路由） | 对照组也存在 | FM-DATASET-COMMIT-RESOLVER-IDOR-001 | control dataset commit resolver or authorization contract differed；experiment dataset commit resolver or authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FM-060 | 下载文件 - 空文件 | 对照组也存在 | FM-DOWNLOAD-EMPTY-001 | control empty file download fallback or explicit error contract differed；experiment empty file download fallback or explicit error contract differed | api/apps/restful_apis/file_api.py |
| TC-FM-061 | 文件名称包含特殊字符 | 对照组也存在 | FM-NAME-PATH-NORMALIZATION-001 | control special filename round trip or path normalization contract differed；experiment special filename round trip or path normalization contract differed | api/apps/restful_apis/file_api.py |
| TC-FM-066 | 跨租户访问文件 | 对照组也存在 | FM-CROSS-TENANT-FOLDER-LIST-001、FM-CROSS-TENANT-PARENT-CREATE-001、FM-CROSS-TENANT-PARENT-UPLOAD-001、FM-CROSS-TENANT-DESTINATION-MOVE-001、FM-CROSS-TENANT-FILE-AS-DESTINATION-001、FM-CROSS-TENANT-COMMIT-VERSION-READ-001、FM-CROSS-TENANT-DATASET-COMMIT-READ-001、FM-CROSS-TENANT-RECURSIVE-DELETE-001 | control foreign folder list was not rejected without mutation；control foreign parent create was not rejected without mutation；control foreign parent upload was not rejected without mutation；control foreign folder move was not rejected without mutation；control… | api/apps/restful_apis/file_api.py；api/apps/restful_apis/file_commit_api.py；api/apps/services/file_api_service.py |
| TC-FILE-VER-001 | 创建 commit | 对照组也存在 | FM-COMMIT-SUP-CREATE-IDOR-001 | control supplement version endpoint or independent-tenant authorization contract differed；experiment supplement version endpoint or independent-tenant authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FILE-VER-002 | 列出文件版本 | 对照组也存在 | FM-COMMIT-SUP-VERSIONS-IDOR-001 | control supplement version endpoint or independent-tenant authorization contract differed；experiment supplement version endpoint or independent-tenant authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FILE-VER-003 | 查看特定 commit | 对照组也存在 | FM-COMMIT-SUP-DETAIL-IDOR-001 | control supplement version endpoint or independent-tenant authorization contract differed；experiment supplement version endpoint or independent-tenant authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FILE-VER-004 | 比较两个 commit 的差异 | 对照组也存在 | FM-COMMIT-SUP-DIFF-IDOR-001 | control supplement version endpoint or independent-tenant authorization contract differed；experiment supplement version endpoint or independent-tenant authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FILE-VER-005 | 获取 commit 的文件树 | 对照组也存在 | FM-COMMIT-SUP-TREE-IDOR-001 | control supplement version endpoint or independent-tenant authorization contract differed；experiment supplement version endpoint or independent-tenant authorization contract differed | api/apps/restful_apis/file_commit_api.py |
| TC-FILE-VER-006 | 获取 commit 中特定文件的内容 | 对照组也存在 | FM-COMMIT-SUP-CONTENT-IDOR-001 | control supplement version endpoint or independent-tenant authorization contract differed；experiment supplement version endpoint or independent-tenant authorization contract differed | api/apps/restful_apis/file_commit_api.py |

## 4. 全量用例结果

| 用例 | 名称 | 对照组 | 实验组 | 用例对 | 证据 |
|---|---|---|---|---|---|
| TC-FM-001 | 创建根目录下的文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-001.json) |
| TC-FM-002 | 创建嵌套文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-002.json) |
| TC-FM-003 | 创建同名文件夹（重复检测） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-003.json) |
| TC-FM-004 | 创建虚拟文件夹（type=virtual） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-004.json) |
| TC-FM-005 | 创建文件夹 - 父文件夹不存在 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-005.json) |
| TC-FM-006 | 创建文件夹 - 名称为空 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-006.json) |
| TC-FM-007 | 创建文件夹 - 名称超长（超过 255 字符） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-007.json) |
| TC-FM-008 | 上传单个文件到根目录 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-008.json) |
| TC-FM-009 | 上传多个文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-009.json) |
| TC-FM-010 | 上传文件到指定文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-010.json) |
| TC-FM-011 | 上传文件 - 目标文件夹不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-011.json) |
| TC-FM-012 | 上传文件 - 无文件部分 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-012.json) |
| TC-FM-013 | 上传文件 - 空文件名 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-013.json) |
| TC-FM-014 | 列出根目录下的文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-014.json) |
| TC-FM-015 | 列出指定文件夹下的文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-015.json) |
| TC-FM-016 | 文件列表 - 关键词搜索 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-016.json) |
| TC-FM-017 | 文件列表 - 排序和分页 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-017.json) |
| TC-FM-018 | 文件列表 - 文件夹不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-018.json) |
| TC-FM-019 | 移动文件到目标文件夹 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-019.json) |
| TC-FM-020 | 移动多个文件到目标文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-020.json) |
| TC-FM-021 | 重命名单个文件（不移动） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-021.json) |
| TC-FM-022 | 移动并重命名文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-022.json) |
| TC-FM-023 | 移动文件 - 目标文件夹不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-023.json) |
| TC-FM-024 | 移动文件夹到自身子文件夹（循环检测） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-024.json) |
| TC-FM-025 | 重命名文件 - 更改扩展名（禁止） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-025.json) |
| TC-FM-026 | 重命名文件 - 同名冲突 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-026.json) |
| TC-FM-027 | 移动文件 - 缺少必要参数 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-027.json) |
| TC-FM-028 | 移动文件 - 多文件重命名（禁止） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-028.json) |
| TC-FM-029 | 删除单个文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-029.json) |
| TC-FM-030 | 删除多个文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-030.json) |
| TC-FM-031 | 删除文件夹（递归删除） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-031.json) |
| TC-FM-032 | 删除文件 - 文件不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-032.json) |
| TC-FM-033 | 删除文件 - 无权限 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-033.json) |
| TC-FM-034 | 删除文件 - 知识库源文件保护 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-034.json) |
| TC-FM-035 | 删除文件 - ids 为空数组 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-035.json) |
| TC-FM-036 | 链接文件到数据集 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-036.json) |
| TC-FM-037 | 链接文件夹到数据集（递归展开） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-037.json) |
| TC-FM-038 | 链接文件到数据集 - 文件不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-038.json) |
| TC-FM-039 | 链接文件到数据集 - 数据集不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-039.json) |
| TC-FM-040 | 获取文件的父文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-040.json) |
| TC-FM-041 | 获取根文件夹的父文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-041.json) |
| TC-FM-042 | 获取文件的所有祖先文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-042.json) |
| TC-FM-043 | 获取祖先文件夹 - 文件不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-043.json) |
| TC-FM-044 | 获取父文件夹 - 无权限 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-044.json) |
| TC-FM-045 | 创建提交（add 操作） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-045.json) |
| TC-FM-046 | 创建提交（modify 操作） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-046.json) |
| TC-FM-047 | 创建提交（delete 操作） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-047.json) |
| TC-FM-048 | 创建提交（rename 操作） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-048.json) |
| TC-FM-049 | 列出提交（分页） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-049.json) |
| TC-FM-050 | 获取提交详情 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-050.json) |
| TC-FM-051 | 列出提交中的文件 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-051.json) |
| TC-FM-052 | 比较两个提交的差异 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-052.json) |
| TC-FM-053 | 获取未提交的更改 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-053.json) |
| TC-FM-054 | 获取提交树（层级结构） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-054.json) |
| TC-FM-055 | 获取提交中的文件内容 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-055.json) |
| TC-FM-056 | 获取文件版本历史 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-056.json) |
| TC-FM-057 | 通过数据集 ID 访问提交（datasets 路由） | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-057.json) |
| TC-FM-058 | 下载文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-058.json) |
| TC-FM-059 | 下载文件 - 文件不存在 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-059.json) |
| TC-FM-060 | 下载文件 - 空文件 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-060.json) |
| TC-FM-061 | 文件名称包含特殊字符 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-061.json) |
| TC-FM-062 | 文件名称包含 Unicode 字符 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-062.json) |
| TC-FM-063 | 并发创建同名文件夹 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-063.json) |
| TC-FM-064 | 深层嵌套文件夹（超过 10 层） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-064.json) |
| TC-FM-065 | 请求缺少 Authorization 头 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-065.json) |
| TC-FM-066 | 跨租户访问文件 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-066.json) |
| TC-FM-067 | File.source_type 默认空字符串 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-067.json) |
| TC-FM-068 | File.source_type 查询空字符串 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-068.json) |
| TC-FM-069 | File.source_type 更新为空字符串 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-069.json) |
| TC-FM-070 | File.source_type 知识库源标记 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FM-070.json) |
| TC-FILE-DEL-001 | 删除文件夹递归删除子项 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-DEL-001.json) |
| TC-FILE-DEL-002 | 删除文件同时删除关联文档 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-DEL-002.json) |
| TC-FILE-DEL-003 | 删除文件夹同时删除存储 bucket | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-DEL-003.json) |
| TC-FILE-DEL-004 | 删除 skill space 下的 skill 文件夹调用 Go 后端 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-DEL-004.json) |
| TC-FILE-LINK-001 | 链接文件到 dataset | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-LINK-001.json) |
| TC-FILE-LINK-002 | 链接文件夹到 dataset（展开内容） | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-LINK-002.json) |
| TC-FILE-LINK-003 | 链接到多个 datasets | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-LINK-003.json) |
| TC-FILE-LINK-004 | 重复链接同一文件 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-LINK-004.json) |
| TC-FILE-VER-001 | 创建 commit | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-VER-001.json) |
| TC-FILE-VER-002 | 列出文件版本 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-VER-002.json) |
| TC-FILE-VER-003 | 查看特定 commit | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-VER-003.json) |
| TC-FILE-VER-004 | 比较两个 commit 的差异 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-VER-004.json) |
| TC-FILE-VER-005 | 获取 commit 的文件树 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-VER-005.json) |
| TC-FILE-VER-006 | 获取 commit 中特定文件的内容 | FAIL | FAIL | FAIL | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-VER-006.json) |
| TC-FILE-STOR-001 | 上传文件同时写入存储和数据库 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-STOR-001.json) |
| TC-FILE-STOR-002 | 下载文件从存储读取 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-STOR-002.json) |
| TC-FILE-STOR-003 | 移动文件更新存储路径 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-STOR-003.json) |
| TC-FILE-ACL-001 | Team permission - 联合租户可访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-ACL-001.json) |
| TC-FILE-ACL-002 | Me permission - 仅 owner 可访问 | PASS | PASS | PASS | [JSON](runs/20260727_combined_001/evidence_private/08_file/TC-FILE-ACL-002.json) |

## 5. 终审

- 计划顺序、正式证据顺序和报告顺序一致，共 89 个用例。
- 每份证据均包含且仅包含 control、experiment 两个组，并保持该顺序。
- 本组由全局覆盖审计校验计划、runner 正式完成状态与证据集合一致性。
- 正式执行状态为 `complete`，没有停止原因。

## 6. 最终判定

GaussDB 适配回归判定为 **PASS**。本组共有 27 个双方共同失败、0 个对照组独有失败、0 个双方共同阻塞、0 个对照环境阻塞但实验组通过；这些结果如实保留，但不归因于实验组适配。
