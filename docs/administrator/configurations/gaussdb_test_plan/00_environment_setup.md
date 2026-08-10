# 00 - 测试环境搭建与前置条件

## 1. 环境要求

| 组件 | 最低版本 | 说明 |
| --- | --- | --- |
| GaussDB | A-compatible 版本 | 支持 Oracle-compatible 和 PostgreSQL-compatible 两种模式 |
| RAGFlow | 当前分支（含 GaussDB 适配代码） | `DB_TYPE=gaussdb` 分支 |
| Python | 3.10 - 3.13 | 推荐使用 3.13 |
| Docker | 24+ | 用于依赖服务 |
| Redis | 7+ | 任务队列和缓存 |
| MinIO | 最新稳定版 | 文件存储 |
| MySQL | 8.0+ | 仅对照组测试使用 |

## 2. GaussDB 服务端准备

### 2.1 确认专用测试数据库和用户

只使用 `gaussdb_info.md` 明确授权的专用测试数据库/账号，不在共享 GaussDB 上临时创建、删除或授权普通运行账号。启动前以脱敏连接探针确认该账号只能访问测试范围；不得把 host/user/password 原值写入报告。

### 2.2 准备本批次专属 Schema

```sql
-- 仅在专用测试账号已有 CREATE 权限时执行；名称由测试驱动安全生成。
CREATE SCHEMA <fr_20260710_fresh_001_experiment_metadata_schema>;
CREATE SCHEMA <fr_20260710_fresh_001_experiment_docstore_schema>;
```

metadata 与 DocEngine/Memory schema 必须不同。若专用账号无创建 schema 权限，则只能使用 `gaussdb_info.md` 已分配的两个专用 schema，并在开始前全量清理；不能退回共享 `public` 后继续测试。

### 2.3 验证 GaussDB 连通性

```bash
# 使用 psql 或 GaussDB 客户端工具验证
psql -h <metadata-fault-proxy-host> -p <metadata-fault-proxy-port> \
  -U <experiment-metadata-user> -d <experiment-metadata-database>

# 执行基本验证
SELECT version();
SELECT current_database(), current_user, current_schema();
SHOW sql_compatibility;  -- 应为 A 或 ORA（Memory Store 需要）
```

## 3. RAGFlow 服务部署

### 3.1 环境变量配置

为每个实例创建独立运行根和独立 `conf/service_conf.yaml`。不要让两个实例在启动时共享同一个配置文件；源码模式可分别设置 `RAG_PROJECT_BASE=<control_root>` 和
`RAG_PROJECT_BASE=<experimental_root>`。敏感变量由启动进程显式传入，不使用
`export $(cat .env.test | xargs)`，以免带空格的 libpq options 或特殊字符密码被错误拆分。

实验组 metadata 环境变量：

```bash
# 元数据库配置
DB_TYPE=gaussdb
GAUSSDB_METADATA_HOST=<experiment-metadata-fault-proxy-host>
GAUSSDB_METADATA_PORT=<experiment-metadata-fault-proxy-port>
GAUSSDB_METADATA_USER=<experiment-metadata-user>
GAUSSDB_METADATA_PASSWORD=<secret-from-private-runtime-config>
GAUSSDB_METADATA_DBNAME=<experiment-metadata-database>
GAUSSDB_METADATA_SCHEMA=<fresh_experiment_metadata_schema>
GAUSSDB_METADATA_MAX_CONNECTIONS=50
GAUSSDB_METADATA_STALE_TIMEOUT=30

DOC_ENGINE=gaussdb
```

DocEngine/Memory 连接不是由 `common.settings` 直接读取 `GAUSSDB_METADATA_*`，也不能把 metadata 连接参数复用进去。实验运行根的最终 `conf/service_conf.yaml` 必须包含独立配置：

```yaml
ragflow:
  http_port: 9480
admin:
  http_port: 9481
gaussdb:
  config:
    host: <experiment-docstore-fault-proxy-host>
    port: <experiment-docstore-fault-proxy-port>
    database: <experiment-docstore-database>
    user: <experiment-docstore-user>
    password: <secret-from-private-runtime-config>
    schema: <fresh_experiment_docstore_schema>
redis:
  db: <experiment_redis_db>
  host: <experiment-redis-fault-proxy-host:port>
minio:
  host: <experiment-minio-fault-proxy-host:port>
  bucket: <fresh_experiment_bucket>
  prefix_path: <fresh_experiment_prefix>
```

对照运行根必须渲染 `DB_TYPE=mysql`、`DOC_ENGINE=infinity`、9380/9381、独立 MySQL database、Infinity database、Redis DB、MinIO bucket/prefix 和 secret key。两组配置、日志、PID、临时目录、credential 与业务 namespace 不共享。

所有需要故障注入的 metadata、DocEngine、Redis 和 MinIO 连接从进程第一次启动起就通过各组独占 fault proxy。不得在中途修改共享服务地址、停止共享数据库或对宿主机做全局网络整形。

### 3.2 模型配置

- 两组分别通过公开 Provider/Model API 配置同一个实际 Qwen chat 模型与同一个实际 Ollama embedding 模型；配置写入各自 metadata DB，不能复制数据库行。
- 每组都调用真实模型探针并记录 provider/model 名、embedding 维度、时间和 secret 指纹；报告不保存 API key。
- happy path 不使用 fake model。只有计划明确的外部错误/协议分支可用受控本地兼容桩，并必须与真实模型 happy path 分开取证。

### 3.3 启动依赖服务

```bash
# 仅启动尚未运行的共享基础进程；业务 namespace 仍按组隔离。
docker compose -f docker/docker-compose-base.yml up -d redis minio

# 等待服务就绪
docker compose -f docker/docker-compose-base.yml ps
```

### 3.4 启动 RAGFlow API/Admin/worker

```bash
# 两个实例分别在隔离环境中启动，RAG_PROJECT_BASE 指向各自运行根。
source .venv/bin/activate
export PYTHONPATH=$(pwd)
export RAG_PROJECT_BASE=<control_or_experimental_root>
bash docker/launch_backend_service.sh
```

启动驱动必须为两组分别保存实际 argv 的脱敏快照、环境白名单指纹、PID、端口监听、启动日志时间窗和配置 hash。不得在同一 shell 中反复 export 两组变量后启动后台进程。

### 3.5 验证服务就绪

```bash
# 匿名检查 API 进程就绪（对照组示例）
curl -s http://127.0.0.1:9380/api/v1/system/ping

# 登录后检查 DocEngine、元数据库、存储和 Redis 状态
curl -s -H "Authorization: Bearer <token>" \
  http://127.0.0.1:9380/api/v1/system/status | python3 -m json.tool

# 检查 Admin 服务
curl -s http://127.0.0.1:9381/api/v1/admin/ping
```

## 4. 默认管理员账号

RAGFlow 启动时会自动创建默认管理员账号：

| 字段 | 值 |
| --- | --- |
| 邮箱 | `admin@ragflow.io`（或 `DEFAULT_SUPERUSER_EMAIL` 配置值） |
| 密码 | 当前独立运行根配置值；仅由私有运行配置注入 |
| 角色 | superuser |

验证管理员登录：

```bash
curl -s -X POST http://127.0.0.1:9380/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@ragflow.io","password":"<encrypted_password>"}'
```

## 5. 测试数据清理

本轮测试开始前必须对专用测试资源做全量清理并保存空基线，而不是只按名称删除若干业务行：

- 对照组重建独立 MySQL 测试库、清空独立 Infinity database、Redis DB 和 MinIO bucket。
- 实验组只清空 `gaussdb_info.md` 授权的本批次 metadata/DocEngine schema 中的表、索引和序列，并清空独立 Redis DB、MinIO bucket/prefix。
- 此处属于测试环境初始化，允许直接数据库操作；环境就绪后，除计划明确要求的迁移/故障构造外，业务写入只能通过 API，数据库只读校验。
- 清理前后都要核对 database/schema/Redis DB/bucket/prefix 身份；任何身份不匹配立即停止，不能执行模糊 wildcard 删除。
- 每个 case 的业务 fixture 通过 API 自清理；adapter fixture 按创建 manifest 精确删除。清理失败记入该 case，不得靠下一轮全库清理掩盖残留。

## 6. 日志和调试

### 6.1 SQL 与连接证据

使用当前 logger 的 case 时间窗、driver spy 或专属 proxy 时间线记录 SQL/重试行为。不得依赖代码中不存在或未经证明生效的环境变量；SQL 参数、DSN、Token 和模型/OAuth secret 在落盘前必须脱敏。

### 6.2 GaussDB 活动与锁

仅在当前账号已具备只读 catalog 权限时，按本批次 database/user/application name 和已登记 relation OID 查询活动与锁；无权限时使用 driver/proxy 时间线，不扩大授权。不得把跨账号的全局活动列表写入证据。

### 6.3 RAGFlow 日志位置

```bash
# 分别读取 control/experiment 运行根中本 case 起止时间窗的日志；不把 tail -f
# 或整份日志中的“未找到 ERROR”作为通过条件。
```

## 7. 测试工具

### 7.1 API 请求模板

```python
client = FreshCaseClient(base_url=current_group_url, cookie_jar=None)
login_response = client.post(
    "/api/v1/auth/login",
    json={"email": case_email, "password": rsa_encrypted_password},
)
token = SecretValue(login_response.headers["Authorization"])
evidence.record_header_fingerprint("Authorization", token.fingerprint())

response = client.get(
    "/api/v1/<endpoint>",
    headers={"Authorization": token.reveal_for_request_only()},
)
evidence.record_redacted_response(response)
```

### 7.2 GaussDB 直查模板

```python
read_only_query(
    "SELECT id,email,nickname,status,is_superuser FROM \"user\" WHERE email=%s",
    (case_email,),
)
read_only_query(
    "SELECT id,name,embd_id,permission FROM knowledgebase WHERE id=%s",
    (dataset_id,),
)
read_only_query(
    "SELECT id,name,memory_type,embd_id,llm_id FROM memory WHERE id=%s",
    (memory_id,),
)
read_only_catalog_query(schema=expected_case_schema, object_ids=case_object_manifest)
```
