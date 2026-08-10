# GaussDB 适配完整测试方案

本目录包含 RAGFlow 使用 GaussDB 作为元数据库（`DB_TYPE=gaussdb`）和 Memory Store（`DOC_ENGINE=gaussdb`）时的完整端到端测试方案。

## 设计依据

- [GaussDB 业务元数据库 DB_TYPE 适配设计](../gaussdb_metadata_database.md)
- [GaussDB Oracle-compatible Memory Store 适配设计](../gaussdb_memory_store_adaptation.md)
- [GaussDB 业务元数据库测试设计方案](../gaussdb_metadata_database_test_design.md)
- [GaussDB Memory Store 测试设计方案](../gaussdb_memory_store_test_design.md)

## 文档索引

### 基础测试文档

| 文件 | 内容 | 覆盖范围 |
| --- | --- | --- |
| [00_environment_setup.md](./00_environment_setup.md) | 测试环境搭建、前置条件、通用配置 | 所有测试的环境基础 |
| [01_startup_migration.md](./01_startup_migration.md) | GaussDB 空库启动、重复启动、结构迁移、DDL 幂等 | 元数据库初始化和迁移 |
| [02_user_management.md](./02_user_management.md) | 用户注册、登录、状态切换、角色管理、删除、重名 | 用户全生命周期 |
| [03_auth_token_session.md](./03_auth_token_session.md) | 认证、Token、Session、OAuth、密码重置 | 认证体系 |
| [04_dataset_document.md](./04_dataset_document.md) | 数据集 CRUD、文档上传/解析/分块、检索 | 知识库和文档管理 |
| [05_chat_session_agent.md](./05_chat_session_agent.md) | Chat 应用、会话管理、Agent/Canvas、Webhook | 对话和智能体 |
| [06_memory_metadata.md](./06_memory_metadata.md) | Memory 元数据 CRUD、权限、配置 | Memory 元数据表 |
| [07_memory_store_e2e.md](./07_memory_store_e2e.md) | Memory Store 消息写入/查询/检索/遗忘/删除 | Memory Store 全链路 |
| [08_file_management.md](./08_file_management.md) | 文件/文件夹 CRUD、移动、链接、版本管理 | 文件管理 |
| [09_connector_system.md](./09_connector_system.md) | Connector 调度方言、系统健康、空串与 DISTINCT/ORDER BY 兼容 | Connector/System 的 GaussDB 适配分支 |
| [10_fault_recovery.md](./10_fault_recovery.md) | SQLSTATE 分类、连接恢复、事务、权限、向量与方言边界 | GaussDB 适配故障和恢复 |
| [11_validation_oracle_matrix.md](./11_validation_oracle_matrix.md) | 通过/失败判定矩阵、DB 实态校验、对照组设计 | 所有测试的验收标准 |

### 补充测试文档

基于代码审查发现的差距，补充以下测试用例：

| 文件 | 内容 | 补充范围 |
| --- | --- | --- |
| [02_user_management_admin_supplement.md](./02_user_management_admin_supplement.md) | Admin API 用户管理、用户设置细节 | Admin API 路径、权限控制、级联删除 |
| [03_auth_supplement.md](./03_auth_supplement.md) | 登出细节、OAuth 错误处理 | Token 失效机制、OAuth 回调错误 |
| [04_dataset_document_supplement.md](./04_dataset_document_supplement.md) | Pydantic 验证、级联删除、Tag/Metadata/索引操作 | 高级功能、GaussDB 特有场景 |
| [05_chat_agent_supplement.md](./05_chat_agent_supplement.md) | Chat 软删除、PATCH 合并、Webhook 安全、Completion 模式 | 软删除行为、合并逻辑、安全层 |
| [06_memory_supplement.md](./06_memory_supplement.md) | Memory 验证逻辑、访问控制、字段规范化 | 验证细节、权限检查、更新限制 |
| [08_file_supplement.md](./08_file_supplement.md) | 文件级联删除、链接 datasets、版本管理、存储一致性 | 级联行为、存储层验证 |

### 分析文档

| 文件 | 内容 |
| --- | --- |
| [gap_analysis.md](./gap_analysis.md) | 测试方案差距分析报告 |
| [review_findings.md](./review_findings.md) | 当前源码审查风险与用例覆盖映射 |

## 测试方法论

### 交叉验证原则

当前 643 个用例均先执行 MySQL metadata + Infinity 对照组，再执行 GaussDB metadata + GaussDB DocEngine/Memory 实验组。第 09 组仅保留 6 个适配用例，第 10 组仅保留 27 个适配用例；已移除的通用业务、协议集成和安全泛测不再计入当前计划。每个用例都必须按 [11_validation_oracle_matrix.md](./11_validation_oracle_matrix.md) 保存本批次独立证据：

1. **API 响应验证**：检查 HTTP 状态码、响应体结构和业务码。注意部分接口会返回 HTTP 200 + `code != 0` 表示业务失败。
2. **数据库/Store 实态验证**：对 metadata DB、Infinity/Gauss DocEngine、Memory Store、Redis/MinIO 做只读校验。
3. **日志/任务时间线**：异步操作必须轮询终态，记录 request/task/ingestion ID 和限定时间窗的日志。
4. **隔离对照验证**：除两组相同业务请求外，还要检查同 tenant 非目标资源与跨 tenant canary 未被污染。

业务 fixture 只能通过公开 API 写入。直接 SQL/adapter 写入仅限计划明确要求的迁移、错误码、锁、事务、DDL、catalog、权限或故障 fixture，并且只能操作本批次专属对象。

### 通过/失败判定

- **通过**：API/页面结果符合预期，GaussDB 元数据库和 Doc/Memory Store 实态符合预期，对照组没有被污染。
- **失败**：任一层状态不一致，例如 API 成功但 DB 未写入、DB 写入但对照资源被污染、任务失败但页面显示成功。
- **测试用例问题**：用例引用不存在字段、错误 API、错误表结构或错误文案时，先修正用例，不应直接登记为产品缺陷。
- **环境问题**：依赖、模型或网络不可用时不能把 case 判通过或跳过；修复环境后从该 case 的对照组重新执行。核心 happy path 始终使用实际 Qwen chat 与 Ollama embedding。

### 参数覆盖规则

每个 API 参数必须覆盖以下分区：

| 分区 | 说明 | 示例 |
| --- | --- | --- |
| 省略 | 不传该参数 | 缺省 `page_size` |
| `null` | 显式传 `null` | `"name": null` |
| 空字符串 | 传 `""` | `"name": ""` |
| 空白字符串 | 传纯空白 | `"name": "   "` |
| 合法最小值 | 最小合法输入 | `"page": 1` |
| 合法普通值 | 正常业务输入 | `"name": "test-dataset"` |
| 合法边界 | 由当前端点 schema/源码定义的精确边界 | 许多列表端点的 `page_size` 上界为 100；以各 case 约定为准 |
| 非法类型 | 错误数据类型 | `"page": "abc"` |
| 非法枚举 | 不在枚举范围内 | `"permission": "unknown"` |
| 不存在资源 | 引用不存在的 ID | `"id": "nonexistent"` |
| 跨租户资源 | 使用其他租户的资源 | 另一个 tenant 的 dataset ID |
| 特殊字符 | SQL/HTML 特殊字符 | `"name": "test'; DROP TABLE"` |

### 测试数据命名规范

所有测试数据使用包含批次、组别和 case ID 的唯一前缀，便于清理和追踪；`<group>` 取 `control` 或 `experiment`：

- 用户邮箱：`fr-20260710-fresh-001-<group>-<case>-<seq>@example.com`
- 数据集名称：`fr_20260710_fresh_001_<group>_<case>_ds_<seq>`
- Chat 名称：`fr_20260710_fresh_001_<group>_<case>_chat_<seq>`
- Memory 名称：`fr_20260710_fresh_001_<group>_<case>_mem_<seq>`
- 文件名：`fr_20260710_fresh_001_<group>_<case>_file_<seq>.txt`
- 直接数据库对象：`fr_20260710_fresh_001_<group>_<case>_<object>`，超过标识符上限时使用测试驱动的稳定 hash 缩短函数

各历史计划正文中出现的 `gaussdb-test-*`、`test@example.com`、`<run_id>` 等均只视为逻辑别名，不得原样执行。新驱动在每个 case 开始时把别名物化为上述批次/组别/case 唯一值；计划显式依赖前一 case 时也重新经 API 创建等价前置，并保存新 ID，不复用旧响应或旧数据库行。SQL 示例同理必须由驱动编译为当前 MySQL/Gauss 方言并参数绑定，不能直接拼接正文字符串。

### 通用环境

所有测试共享以下双实例假设（具体配置见 [00_environment_setup.md](./00_environment_setup.md)）：

| 资源 | 对照组 | 实验组 |
| --- | --- | --- |
| API / Admin | `127.0.0.1:9380` / `9381` | `127.0.0.1:9480` / `9481` |
| metadata | `DB_TYPE=mysql`，全新独立 MySQL database | `DB_TYPE=gaussdb`，全新独立 Gauss database/schema |
| Doc/Memory Store | `DOC_ENGINE=infinity`，独立 Infinity database | `DOC_ENGINE=gaussdb`，独立 Gauss schema |
| Redis / MinIO | 独立 DB 编号 / logical namespace | 与对照组不同的 DB 编号 / logical namespace |
| 配置、日志、PID、secret key | 独立 control `RAG_PROJECT_BASE` | 独立 experiment `RAG_PROJECT_BASE` |

DocEngine 的 Gauss 连接来自实验运行根的 `conf/service_conf.yaml` 中 `gaussdb.config`，不是 `GAUSSDB_HOST/GAUSSDB_DATABASE` 环境变量拼装。metadata Gauss 连接才读取 `GAUSSDB_METADATA_*`。两组从启动起通过各自本批次 fault proxy 访问需要注入故障的依赖。

### 认证方式

API 请求覆盖两种认证方式，并明确区分：

1. **登录认证**：登录响应的签名 Authorization token；token 失效/权限用例使用无 cookie client，session 专项才显式保存 Cookie。
2. **Tenant API Token 认证**：由 Token API 创建的 API key，按当前认证加载器支持的 Authorization 格式发送。

获取认证 Token 的标准流程：

```bash
# 1. 从当前运行根对应的 RSA 公钥构造加密密码；禁止发送明文密码。
# 2. 调用当前组登录接口获取 Authorization Header。
curl -s -X POST "$HOST_ADDRESS/api/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$ENCRYPTED\"}" \
  -D - | grep -i "^authorization:"
```

报告中不得保存上面 header 的完整值，只保存指纹。测试脚本也不得把 secret 放入命令行、stdout 或全局 shell 环境快照。

## 全新执行与报告

- 执行批次固定为 `20260710_fresh_001`，只在 `gaussdb_test_plan_execute/` 创建新脚本、原始证据和中文报告。
- 禁止读取或复用 `gaussdb_test_plan_execute_bak*`、`gaussdb_test/`、`mysql_control_test/` 及任何旧报告/结果。
- 21 个当前计划文件各有独立报告；无 `TC-*` 标题的 00、11、README、gap_analysis、review_findings 也按审计组执行并报告。
- 每个 case 的对照组和实验组都必须有独立请求、响应、状态查询和最终判定；任何一组缺失都不能标记完成。
