# 08 File fresh execution implementation plan

批次：`20260710_fresh_001`

## Scope and invariants

- 唯一输入为当前 `gaussdb_test_plan/08_file_management.md` 的 70 个用例和 `08_file_supplement.md` 的 19 个用例，合计 89 个唯一 ID。
- 每个用例必须完整执行 control 后 experiment；中途退出不形成正式成对结果，修复执行工具后用新 fixture 从 control 重跑。
- File/Folder/Dataset/Document/Commit 的业务写入和清理走公开 API/Admin API。数据库、Message Store、Redis、MinIO 默认只读；仅计划明确授权的 `source_type`/skill space 字段 fixture 使用最小 ORM 更新并恢复。
- 所有原始证据写入新的 `evidence_private/08_file`，目录 0700、文件 0600；认证、连接和模型秘密不得进入证据或公开文档。
- 不修改产品代码；产品不符合当前计划时保存真实响应、零副作用/残留证据并登记 Finding。

## Execution steps

1. 建立 `fresh_08_file.py`、`test_fresh_08_file.py` 和独立私密证据目录；从两份当前计划动态提取 89 个 ID/标题并验证计划顺序、唯一性和正式记录结构。
2. 以失败测试驱动共享工具：登录、JSON/multipart/binary 请求取证、根目录初始化、File/Folder API fixture、只读 DB 快照、MinIO 对象读取、Dataset/Document/link 轮询、二级用户与 team 关系、API 清理和 Commit 快照。
3. 顺序实现并执行 TC-FM-001～018：folder 创建、上传、根/子目录列表、关键词、分页/排序和异常参数；完成阶段覆盖、权限、秘密与 fixture 残留核对。
4. 顺序实现并执行 TC-FM-019～044：移动/重命名、递归删除、link-to-datasets 异步关联、父级/祖先查询及 owner/team/cross-tenant 权限；保存对象地址前后快照。
5. 顺序实现并执行 TC-FM-045～057：Commit add/modify/delete/rename、分页、详情、items、diff、changes、tree、历史内容、versions 和 Dataset resolver；每个端点以独立用户重放验证 IDOR，失败事务核对零半提交。
6. 顺序实现并执行 TC-FM-058～070：下载、空文件、特殊/Unicode/路径名称、并发同名、15 层目录、无认证、跨租户注入面及 `source_type` 方言/知识库标记。
7. 顺序实现并执行 TC-FILE-DEL-001～004 与 TC-FILE-LINK-001～004：级联 File/Document/Task/DocEngine/object 清理、存储 namespace、受控 Go skill stub 和 link 替换语义。
8. 顺序实现并执行 TC-FILE-VER-001～006、TC-FILE-STOR-001～003、TC-FILE-ACL-001～002：版本 API、对象内容寻址、存储移动一致性及 team/me 权限。
9. 终审 89 个计划/runner/证据 ID、178 个组别记录、顺序/结构、权限/秘密、活动 File/Dataset/Document/Commit/用户/object 残留和 8/8 服务；生成唯一中文报告并更新 `PROGRESS.md`。

## Checkpoints

- 每个阶段先运行定向契约测试，再运行当前 08 全量测试、`py_compile` 和 `git diff --check`。
- 阶段审计必须报告计划/runner/证据精确覆盖、PASS/FAIL/BLOCKED、两组差异、私密证据模式和业务/辅助残留。
- 全组完成前不声称 08 通过；全量 784 例完成前不结束总目标。
