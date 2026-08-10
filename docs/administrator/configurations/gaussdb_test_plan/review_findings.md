# 测试方案审查报告

基于当前工作树代码审查发现的问题和潜在 bug，补充到 `20260710_fresh_001` 测试方案中。本文只定义待重测风险与覆盖映射，不包含任何历史实测结论；正式 `TC-*` 与本文 `RF-AUDIT-*` 审计项都必须先对照组、后实验组产生新证据。

## 1. 代码审查发现的潜在 Bug

### 1.1 软删除用户的安全隐患

**问题描述**：`status="0"` 表示软删除，`is_active="0"` 表示停用。两者语义不同，但存在以下隐患：

| 场景 | 问题 | 测试用例 |
|------|------|----------|
| 软删除用户重新注册 | `UserService.query(email=...)` 无 status 过滤，软删除用户的 email 仍存在，阻止重新注册 | TC-UM-048、TC-AT-SOFTDEL-001 |
| 软删除用户密码重置 | `query_user_by_email()` 无 status 过滤，忘记密码流程对软删除用户仍有效 | TC-AT-SOFTDEL-002 |
| OAuth 登录软删除用户 | OAuth callback 用 `UserService.query(email=...)` 无 status 检查，软删除用户可能通过 OAuth 登录 | TC-AT-SOFTDEL-003 |

**安全判定**：密码重置和 OAuth 必须拒绝软删除用户；同邮箱能否重新注册按明确产品契约判定，但不能同时生成两套可登录身份。本文不修改产品代码。

### 1.2 Tenant 模型更新缺少权限验证

**问题描述**：`PATCH /users/me/models` 接收 `tenant_id` 参数并直接更新，无 ownership 验证。理论上用户可以修改其他 tenant 的模型配置。

**审计项**：RF-AUDIT-001。A/B 两个专属 tenant 分别配置模型，A 重放 B 的 `tenant_id` 必须被拒绝且 B 配置指纹不变；业务写入仍走 API。

### 1.3 Memory API 异常处理缺失

**问题描述**：
- `POST /messages` 无 try/except，NotFoundException 会传播到框架层
- `GET /messages/search` 无 try/except，所有异常未捕获
- `GET /messages/search` 的 `query` 参数可为 None，无非空验证
- `GET /messages` 的 `limit` 参数无上界验证
- `GET /memories` 和 `GET /memories/<id>` 的 `page/page_size` 类型转换在 `try` 块外，非法类型可能走框架层错误

**测试用例**：TC-MM-SUP-021/022/023、TC-MS-109、TC-MS-212

**安全判定**：非法参数/不可访问资源必须得到稳定业务错误且无 Message/Task/cache 写入；500/`code=100` 或部分写入均记录共同产品缺陷，本文不实施修复。

### 1.4 Admin API 错误信息不一致

**问题描述**：`PUT /users/<username>/admin` 和 `DELETE /users/<username>/admin` 都检查 `current_user.email == username`，但两个端点的错误信息都是 "can't grant current user"。revoke 路径应该说 "can't revoke"。

**测试用例**：TC-UM-ADMIN-016

### 1.5 Admin 创建用户 nickname 为空

**问题描述**：`UserMgr.create_user()` 设置 `nickname=""`，跳过了 nickname 验证。但前端可能期望非空 nickname。

**测试用例**：TC-UM-ADMIN-001

### 1.6 SettingsMgr 自动创建设置

**问题描述**：`SettingsMgr.update_by_name()` 如果设置名不存在，会自动创建新行。可能导致拼写错误创建幽灵设置。

**审计项**：RF-AUDIT-002。用 Admin Settings API 提交本批次唯一拼写错误 key，安全预期拒绝且 settings 行集合不变；若当前实现创建新行，记录共同校验缺陷并经 API/专属清理路径恢复。

---

## 2. 前端交互发现的测试场景

### 2.1 密码加密使用硬编码公钥

**问题描述**：前端 `rsaPsw()` 使用硬编码的 RSA 公钥加密密码。如果后端 `conf/private.pem` 不匹配，所有登录都会失败。

**测试用例**：TC-AT-KEYPAIR-001、TC-AT-KEYPAIR-002

### 2.2 前端表单验证 vs 后端验证

| 字段 | 前端验证 | 后端验证 | 测试场景 |
|------|----------|----------|----------|
| nickname | `^[\p{L}\p{N} ._'-]+$/u`，max 100 | 等价 Unicode 正则 | TC-UM-011/013/014/015、TC-UM-SET-005 |
| admin 创建用户密码 | min 6 | 当前未见同等最小长度校验 | RF-AUDIT-003 |
| admin 修改密码 | min 8 | 当前未见同等最小长度校验 | RF-AUDIT-004 |
| 注册密码 | min 1 | 后端未设置有效最小强度 | TC-AT-PWD-LENGTH-001 |

### 2.3 Token 存储和 401 重定向

**问题描述**：
- localStorage 存储三个 key：`Authorization`、`Token`、`UserInfo`
- 401 响应触发 `removeAll()` + 重定向到 `/login`
- OAuth callback 通过 `?auth=` URL 参数传递 token

**测试用例**：TC-AT-TOKEN-401-001、TC-AT-OAUTH-CALLBACK-001

### 2.4 文件上传跳过 tenant 参数注入

**问题描述**：`FormData` 请求跳过 `addTenantParams()`，后端必须从 auth header 提取 tenant。

**测试用例/审计项**：TC-FM-008、TC-FM-066 与 RF-AUDIT-005；用 A/B 无 cookie 客户端证明 multipart 归属只来自有效认证，不能由 query/body 注入 tenant。

---

## 3. Dataset/Document API 发现的测试场景

### 3.1 Embedding model 格式验证

**问题描述**：`embedding_model` 格式为 `<model>@<provider>`，需要验证 `@` 分隔符，两部分非空且无空白。

**测试用例**：TC-DD-008、TC-DD-VAL-003、TC-DD-VAL-005

### 3.2 Avatar base64 格式验证

**问题描述**：`avatar` 必须是 `data:image/png;base64,...` 或 `data:image/jpeg;base64,...` 前缀，只允许 png/jpeg。

**审计项**：RF-AUDIT-006；通过当前 Dataset API 覆盖 png/jpeg、错误 MIME、坏 base64 与超长值，并核对 metadata 无部分写入。

### 3.3 Parser config JSON 长度限制

**问题描述**：`parser_config` 序列化 JSON 长度最大 65535 字符。

**测试用例/审计项**：TC-DD-VAL-004、TC-DD-064 与 RF-AUDIT-007（精确覆盖 65,535 前后边界）。

### 3.4 URL SSRF 保护

**问题描述**：Web 上传使用 `assert_url_is_safe` 防止 SSRF 攻击。

**审计项**：RF-AUDIT-008；受控 loopback/RFC1918/link-local/重定向哨兵必须零请求，公共允许目标走本地隔离映射成功路径。

### 3.5 删除级联细节

**问题描述**：
- 删除文档时清理 chunk images 和 document thumbnail
- 删除知识图谱引用（从 entities/relations 移除 source_id，删除孤立 entities）
- TABLE parser 特殊处理：当 table 文档数降为 0 时删除 KB field map

**测试用例/审计项**：TC-DD-043/044、TC-DD-DEL-003/004 与 RF-AUDIT-009；删除前保存 image/thumbnail/graph/field-map 对象 ID，再逐个核对。

### 3.6 Chunk 操作与 metadata DB 关系

**问题描述**：Chunks 存储在 doc store（ES/Infinity/GaussDB），不在 metadata DB。`chunk_num` 和 `token_num` 在 Document 模型上跟踪计数。

**测试用例**：TC-DD-048/049/050/051，并按 11 判定矩阵只读核对 metadata 计数和 Infinity/Gauss store 行。

### 3.7 可能遗漏的 API

- `POST /documents/ingest` - 直接 ingest 端点（不在 datasets 下）
- `POST /retrieval` - 检索测试端点
- `GET /documents/<doc_id>/preview` - 无 dataset context 的预览
- `GET /documents/artifact/<filename>` - sandbox artifact 下载
- `GET /documents/images/<image_id>` - 图片服务
- `GET /thumbnails` - 批量缩略图查询
- `PATCH /datasets/<dataset_id>/documents/metadatas` - 备用元数据批量更新端点
- `POST /datasets/<dataset_id>/embedding/check` - embedding 兼容性检查
- `GET /datasets/<dataset_id>/ingestions/<log_id>` - 单个 ingestion log（含 DSL）
- `PUT /datasets/<dataset_id>/documents/<document_id>/metadata/config` - 每文档元数据配置
- `POST /datasets/<dataset_id>/documents/batch-update-status` - 批量状态切换（含 doc store 同步）

---

## 4. Memory/Connector/File/Agent/Chat 发现的测试场景

### 4.1 Memory type 枚举值

**问题描述**：`memory_type` 枚举值为 `"raw"/"semantic"/"episodic"/"procedural"`，当前后端输入校验大小写敏感；前端只提交小写枚举并要求包含 `"raw"`。

**测试用例**：已在 06_memory_supplement.md 中覆盖。大写输入应判定为非法输入，不应写成自动归一化成功。

### 4.2 Memory size 精确限制

**问题描述**：`memory_size` 范围 `0 < value <= 10,485,760`（10MB）。

**测试用例**：已在 06_memory_supplement.md 中覆盖，补充边界值 10,485,760。

### 4.3 System prompt 自动更新逻辑

**问题描述**：当 `memory_type` 变更时：
1. 仅允许空 memory（`memory_size == 0`）
2. 如果 `system_prompt` 未在本次请求中显式更新
3. 且当前 `system_prompt` 匹配旧 memory type 的默认值（通过 `judge_system_prompt_is_default` 检查）
4. 则使用 `PromptAssembler.assemble_system_prompt` 生成新默认 prompt

**测试用例**：TC-MM-SUP-015 的空 Memory comparator 分支。

### 4.4 Webhook 安全层

**问题描述**：5 层安全机制按顺序应用：
1. Max body size（默认 10MB，格式 `<number><kb|mb>`）
2. IP whitelist（支持单 IP 和 CIDR）
3. Rate limiting（Redis token bucket，默认 60/分钟）
4. Authentication（none/token/basic/jwt）
5. Schema validation（query/headers/body）

**测试用例**：已在 05_chat_agent_supplement.md 中覆盖。

### 4.5 Chat 软删除行为

**问题描述**：`DELETE /chats/<chat_id>` 设置 `status = StatusEnum.INVALID.value`，记录保留在 DB。所有查询过滤 `status=VALID`。

**测试用例**：已在 05_chat_agent_supplement.md 中覆盖。

### 4.7 PATCH 合并行为

**问题描述**：
- `prompt_config` 合并：深拷贝现有配置，然后**浅合并**（`.update()`）— 顶层键替换，嵌套对象**不合并**
- `llm_setting` 合并：同样浅合并模式

**测试用例**：已在 05_chat_agent_supplement.md 中覆盖，补充嵌套对象不合并的验证。

---

## 5. GaussDB Memory Store 发现的测试场景

### 5.1 物理表结构细节

**问题描述**：
- 逻辑 index：`memory_<tenant_id>` 或带 `ES_INDEX_PREFIX` 的 `memory_<prefix>_<tenant_id>`
- 物理表名：`ragflow_mem_` + `sha1(logical_index).hexdigest()[:32]`
- 存储：`WITH (storage_type=USTORE)`
- 7 个常规索引 + UGIN 全文索引 + DiskANN 向量索引
- 向量列动态添加：`q_{dim}_vec floatvector({dim})` + `q_{dim}_vec_empty BOOLEAN`；FALSE 为真实向量，TRUE 为零占位

**测试用例**：已在 07_memory_store_e2e.md 中覆盖。

### 5.2 SQL 注入防护

**问题描述**：`_validate_column()` 限制列名为 `BASE_COLUMN_SET` 或受控动态向量列；identifier builder、参数化 value 和 schema 校验共同构成防护，不能把其中任一层写成“唯一防护”。

**测试用例**：TC-MS-005、TC-MS-213、TC-FR-038。

### 5.3 多表搜索性能

**问题描述**：多表搜索在 Python 中聚合、排序、分页。多表场景下可能较慢。

**审计项**：RF-AUDIT-010。使用少量专属 tenant/memory 做双组同 fixture 基线，只记录 p50/p95 和结果 ID 集合，不设脱离机器配置的固定 SLA。

---

## 6. 本文档独立审计清单

正式计划中的 784 个 `TC-*` 由各业务报告覆盖。下列没有独立 `TC-*` 标题的代码审查差距，统一在 `review_findings_report.md` 中以双组新执行关闭，不能引用不存在的旧 case ID：

| 审计 ID | 场景 | 必需 oracle |
| --- | --- | --- |
| RF-AUDIT-001 | `/users/me/models` 跨 tenant 更新 | A 请求 B tenant 必须拒绝，B 模型配置/时间戳/secret 指纹不变 |
| RF-AUDIT-002 | Settings 拼写错误 key | Admin API 明确拒绝且 settings 集合不新增幽灵行 |
| RF-AUDIT-003 | Admin 创建用户短/空密码 | 前端边界与直接 API 安全契约、无部分 User/Tenant/File 图 |
| RF-AUDIT-004 | Admin 修改为短/空密码 | 明确拒绝或登记后端强度校验缺陷；旧密码/Token 状态按实际链路核对 |
| RF-AUDIT-005 | multipart tenant 归属 | A 上传只归属 A；query/body 注入 B tenant 不生效，B canary 不变 |
| RF-AUDIT-006 | Dataset avatar 格式 | png/jpeg 合法；错误 MIME、坏 base64、超长值拒绝且无部分更新 |
| RF-AUDIT-007 | parser_config 65,535 边界 | 边界内可持久化，边界外标准错误且旧 JSON hash 不变 |
| RF-AUDIT-008 | Web upload SSRF | loopback/RFC1918/link-local/重定向哨兵零请求；受控允许目标成功 |
| RF-AUDIT-009 | Document 删除深层级联 | 删除前保存 image/thumbnail/graph/field-map ID，删除后逐个消失且 canary 不变 |
| RF-AUDIT-010 | Memory 多表检索基线 | Infinity/Gauss 同 fixture 的结果 ID/排序边界正确，记录 p50/p95，不设固定 SLA |
| RF-AUDIT-011 | 3.7 列出的附加 Document API | 逐条确认当前路由存在性、鉴权、合法/非法请求与 DB/store 无越权副作用 |

每项先在对照实例执行，再在实验实例执行；业务写入走 API，DB/store 只读校验。受控 SSRF/故障 adapter 是计划明确例外，只能访问本批次哨兵。报告同时保存源码位置、请求/响应、状态查询、清理结果和秘密指纹。

## 7. 2026-07-10 当前复核结论

- 当前清单为 22 个计划文件、784 个唯一正式用例、双组至少 1,568 次判定；本文另有 11 个 RF 审计项。
- Dataset 没有 `memory_type` 位运算字段；Memory type 大写输入应拒绝，不能写成自动归一化。
- `tenant_llm_id` / `tenant_embd_id` 当前路由提取但 service 不更新，按静默 no-op 契约缺陷重测。
- `GET /messages` recent 与 `/messages/search` 的 `status_int` 默认过滤不同；Gauss Memory 使用 `q_<dim>_vec_empty`，不是 `*_valid`。
- metadata MySQL/Gauss 重试与 DocEngine/Memory pool 必须分开；认证故障不能只验证某一个数据库 adapter。
- Qwen chat/Ollama embedding 的核心 happy path 使用实际模型；OAuth/MCP/Langfuse/REST/SSRF 等外部协议分支使用受控桩时必须与真实模型证据分开。
- 执行不能只看接口或页面状态；每项同时保留 API、metadata、Infinity/Gauss store、Redis/MinIO、日志/任务、隔离 canary 与清理结果。
