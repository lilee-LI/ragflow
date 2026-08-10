# 测试方案差距分析

基于当前工作树代码审查发现的测试用例缺失和不准确之处。本文档是 `20260710_fresh_001` 的覆盖审计输入，不是历史测试结果；其中每一项只能由本批次对照组、实验组的新执行证据关闭。

## 1. 用户管理 (02_user_management.md) 差距

### 1.1 缺失的 Admin API 测试
- Admin API 路径是 `/api/v1/admin/*`，不是 `/api/v1/users/*`
- Admin 创建用户使用 `POST /api/v1/admin/users`，body 字段是 `username`（不是 `email`）
- Admin 删除用户需要先 inactive 且非 superuser
- Admin 修改密码只需 `new_password`，不需要旧密码
- Admin 激活/停用使用 `"on"/"off"` 字符串，不是布尔值
- Admin grant/revoke admin 禁止自我操作（返回 409）

### 1.2 缺失的用户设置细节
- PATCH `/users/me` 受保护字段列表：password, new_password, email, status, is_superuser, login_channel, is_anonymous, is_active, is_authenticated, last_login_time
- nickname 更新时会 strip() 并验证正则
- 密码修改需要同时传 password（旧）和 new_password（新）

### 1.3 缺失的注册细节
- 注册自动创建：User + Tenant + UserTenant(OWNER) + root File 文件夹
- 注册失败时回滚（rollback_user_registration）
- OAuth 回调错误处理：missing_code, token_failed, email_missing, user_inactive

## 2. 认证 (03_auth_token_session.md) 差距

### 2.1 缺失的登出细节
- 登出将 access_token 设为 `INVALID_<random_hex>`
- UserService.query 拒绝 INVALID_ 前缀的 token

### 2.2 缺失的 OAuth 细节
- OAuth state 存储在 session（不是 Redis）
- OAuth 回调处理多种错误：invalid_state, missing_code, token_failed, email_missing
- 已存在但 inactive 的 OAuth 用户重定向到 `/?error=user_inactive`

## 3. 数据集/文档 (04_dataset_document.md) 差距

### 3.1 缺失的验证细节
- Pydantic 模型验证（CreateDatasetReq, UpdateDatasetReq 等）
- Dataset name 唯一性检查（per tenant, case-insensitive）
- Embedding model 验证（verify_embedding_availability）
- Parser config JSON 结构验证

### 3.2 缺失的级联操作
- 删除 dataset 级联删除：documents, files, file2document, tasks, doc store chunks
- 删除 dataset 时 doc store 的 delete_idx 操作

### 3.3 缺失的高级功能
- Tag CRUD 操作
- Metadata config 操作
- Graph/RAPTOR/Mindmap 索引操作
- Embedding 运行和检查操作
- Ingestion logs 查询

## 4. Memory (06/07) 差距

### 4.1 缺失的验证细节
- Memory name strip 和长度检查（MEMORY_NAME_LIMIT）
- Memory type 输入校验：后端只接受小写枚举；返回值是小写列表；不要把大写输入写成“自动归一化通过”
- Temperature 范围 [0, 1]
- Memory size 范围 (0, MEMORY_SIZE_LIMIT]
- Forgetting policy 枚举验证
- Permission 枚举验证（TenantPermission）

### 4.2 缺失的访问控制
- _memory_accessible 检查：owner OR (team permission AND joined tenants)
- _require_memory_access 抛出 NotFoundException
- _filter_accessible_memories 过滤逻辑

### 4.3 缺失的更新字段
- tenant_llm_id 和 tenant_embd_id：路由层提取这两个字段，但当前 service 层不写入，应作为接口契约缺陷或内部字段不生效验证，不能写成“预期已更新”
- 已有消息时禁止修改 embd_id 和 memory_type 的检查

### 4.4 消息查询行为容易误判
- `GET /messages/search` 默认添加 `status=1`，会隐藏 `status_int=0` 消息。
- `GET /messages` recent 查询默认只隐藏 `forget_at IS NOT NULL` 消息，不自动过滤 `status_int=0`。
- `GET /messages/search` 缺失 `query` 会在 service 层 `query.strip()` 处异常，路由未捕获。
- `GET /messages` 的 `limit` 没有上界校验，非法类型在 `try` 块外解析。

## 5. Connector/System (09) 适配差距

本组只保留与适配直接相关的四类风险：

- GaussDB due sync/prune 的 `freq * INTERVAL '1 minute'` 方言；MySQL 对照为 `INTERVAL ... MINUTE`；
- 系统状态必须准确区分 MySQL/Infinity 与 GaussDB metadata/DocEngine；
- `sync_logs.error_msg/full_exception_trace` 的空串物理 NULL 与应用层空串语义；
- `SELECT DISTINCT` 查询必须显式选择 `ORDER BY update_time` 列。

OAuth、Connector 通用 CRUD/Rebuild/Test、模型/Provider、MCP、Langfuse、Admin、Tenant、Token 和安全泛测与 GaussDB 适配无直接关系，已从第 09 组移除。

## 6. 文件 (08) 差距

### 6.1 缺失的细节
- Delete 级联：文件夹递归删除，文件删除 storage blob
- Link to datasets 操作
- 版本/提交操作的完整流程

## 7. Chat/Agent (05) 差距

### 7.1 缺失的细节
- Chat 软删除（status=INVALID）
- PATCH 合并 prompt_config 和 llm_setting 的行为
- Webhook 安全层（max_body_size, ip_whitelist, rate_limit, auth）
- Completion 模式（streaming/non-streaming, draft/session）

## 8. 通用差距

### 8.1 GaussDB 特有场景
- 空字符串兼容字段的 NULL 存储和 "" 读取
- 位运算过滤（memory_type）
- GaussDB INTERVAL 表达式
- 并发 DDL advisory lock
- maintenance_work_mem 重试

### 8.2 前端交互场景
- RSA 加密密码传输
- 前端表单验证 vs 后端验证
- 文件上传 multipart 格式
- 分页和排序参数

### 8.3 错误码覆盖
- 每个 case 必须记录 transport HTTP 状态和当前端点的实际 RetCode；不能只按 HTTP 200 判成功
- 对安全/权限/参数错误验证稳定业务码和关键语义，不把可变异常字符串或整段栈作为唯一 oracle

### 8.4 验收判定缺口
- 仅记录页面/API 执行状态不足以判断 GaussDB 适配是否正确。
- 每个关键用例必须补元数据库 SQL、Doc/Memory Store SQL、日志/任务状态和对照组。
- 统一判定标准见 [11_validation_oracle_matrix.md](./11_validation_oracle_matrix.md)。

### 8.5 双组与执行证据缺口

- 当前 643 个用例必须逐一先执行 MySQL metadata + Infinity，再执行 GaussDB metadata + Gauss DocEngine/Memory；同 tenant canary 不能替代完整对照组。
- 业务写入只走 API；计划明确的迁移、DDL、错误码、事务、锁、权限和故障 adapter fixture 才能直接写专属数据库对象。
- 每个 case 保存批次、组别、请求/任务/fixture ID、起止时间、API、DB/store、日志和清理结果；秘密只保存指纹。
- Qwen chat 与 Ollama embedding happy path 使用实际模型；受控桩只覆盖明确的外部协议/错误分支。
- 故障注入从启动起走独占 TCP proxy，不停止共享数据库、不清空共享 Redis、不做全局网络整形。

## 9. 适配故障与恢复差距

- metadata MySQL/Gauss 重试与 Gauss DocEngine/Memory pool 的语义不同，必须分别验证；后者不承诺重放 in-flight SQL。
- `retry_deadlock_operation()` 总尝试 3 次，只识别 MySQL 1213 和 Gauss 40P01/40001/55P03；MySQL 3572/1205 不应被误写为已重试。
- Memory Gauss 的向量空标记是 `q_<dim>_vec_empty`，FALSE 为真实向量、TRUE 为零占位；Infinity 使用独立物理模型。
- Schema/索引标识符、Schema 权限、A/ORA 兼容、事务回滚和 gsdiskann work mem 恢复必须调用真实适配代码。
- 通用 Token/ACL/SSRF、业务并发、通用 SQL 注入和大事务/存储引擎泛测已从第 10 组移除。
