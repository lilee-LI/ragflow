# 第 07 组 Memory Store 新鲜执行实施计划

## 目标与范围

- 批次固定为 `20260710_fresh_001`。
- 唯一输入计划为当前 `gaussdb_test_plan/07_memory_store_e2e.md` 的 79 个用例。
- 每案严格先 `control`（MySQL metadata + Infinity）后 `experiment`（GaussDB metadata + GaussDB DocEngine）。
- 正常业务写入和清理只走 API；仅计划明确授权的 adapter、Redis、DDL、启动维护 fixture 做最小直接操作。
- 不读取或调用任何备份执行目录、历史测试脚本、历史结果或旧结论。

## 实现阶段

1. 建立 `fresh_07_memory_store.py`、契约测试和独立 0700/0600 证据目录；从当前计划动态提取 79 个 ID/标题。
2. TC-MS-001～012：消息同步 raw 写入、认证归属、扇出、编码/长文本/参数拒绝、adapter upsert、分词一致性。
3. TC-MS-100～109：管理列表、recent、过滤、分页、遗忘/停用可见性和 limit 参数边界。
4. TC-MS-200～213：真实 Ollama embedding 的融合检索、纯向量 adapter 取证、参数边界、过滤和特殊字符。
5. TC-MS-300～503：状态、遗忘、精确内容获取、跨租户 ACL 和不存在消息伪成功判据。
6. TC-MS-600～705：同/跨租户隔离、删除边界、碰撞 ID、动态维度、empty 标记和 DDL 幂等。
7. TC-MS-800～908：独占服务窗口下的 Redis 初始化、size cache、维护 adapter、双后端 catalog/index/并发 DDL。
8. 终审 79 个计划/runner/证据 ID、158 个组别记录、权限/秘密、fixture 残留和 8/8 服务；生成唯一中文报告并更新 `PROGRESS.md`。

## 证据与判定

- 正式结果：`evidence_private/07_memory_store/TC-MS-*.json`。
- 原始 HTTP/adapter/catalog/时间线：`evidence_private/07_memory_store/raw/`。
- 原始认证值、连接值和模型凭据只在内存使用；证据只保存脱敏结构、长度、指纹和 SHA-256。
- 每个成功 POST 通过唯一 agent/session 捕获实际 message ID；不存在 ID 由当前存储只读最大值和精确查询构造。
- Infinity 与 GaussDB 物理 oracle 分开实现；业务 API 语义统一比较，不把后端结构差异当跳过或失败。
- runner/oracle 修复必须先有失败测试；用全新 fixture 从头重跑后才形成正式结论。
