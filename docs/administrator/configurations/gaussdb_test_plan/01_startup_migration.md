# 01 - GaussDB 启动、初始化与结构迁移测试

## 概述

本文档覆盖 `DB_TYPE=gaussdb` 时的元数据库初始化、结构迁移和 DDL 幂等性测试。测试对象是 `api/db/db_models.py` 中的建表逻辑、`migrate_db()` 迁移函数和 GaussDB 专用 DDL。

---

## 1. 空库启动

### TC-SM-001: 全新 Schema 首次启动

**前置条件**：
- GaussDB 测试 schema 已清空所有 RAGFlow 表和 sequence
- 环境变量已配置 `DB_TYPE=gaussdb` 和 `GAUSSDB_METADATA_*`

**步骤**：

1. 确认 schema 为空：
   ```sql
   SELECT table_name FROM information_schema.tables
    WHERE table_schema = current_schema() ORDER BY table_name;
   ```
   预期：返回 0 行

2. 启动 RAGFlow 后端服务：
   ```bash
   DB_TYPE=gaussdb \
   GAUSSDB_METADATA_HOST=<host> GAUSSDB_METADATA_PORT=8000 \
   GAUSSDB_METADATA_USER=rag_flow_test GAUSSDB_METADATA_PASSWORD=<pwd> \
   GAUSSDB_METADATA_DBNAME=rag_flow_test GAUSSDB_METADATA_SCHEMA=public \
   bash docker/launch_backend_service.sh
   ```

3. 等待匿名 `GET /api/v1/system/ping` 返回 `pong`，并检查启动日志中没有建表失败；当前代码没有固定的 `init_database_tables completed` 日志，不能等待不存在的标记。

4. 验证所有 40 张业务表已创建：
   ```sql
   SELECT table_name FROM information_schema.tables
    WHERE table_schema = current_schema()
    AND table_type = 'BASE TABLE'
    ORDER BY table_name;
   ```
   预期：返回 40 行，包含 `user`、`tenant`、`user_tenant`、`knowledgebase`、`document`、`file`、`file2document`、`task`、`dialog`、`conversation`、`api_token`、`api_4_conversation`、`memory`、`connector`、`connector2kb`、`sync_logs`、`system_settings`、`llm_factories`、`llm`、`tenant_llm`、`tenant_langfuse`、`user_canvas`、`canvas_template`、`user_canvas_version`、`mcp_server`、`search`、`pipeline_operation_log`、`chat_channel`、`evaluation_datasets`、`evaluation_cases`、`evaluation_runs`、`evaluation_results`、`tenant_model_provider`、`tenant_model_instance`、`tenant_model`、`tenant_model_group`、`tenant_model_group_mapping`、`file_commit`、`file_commit_item`、`invitation_code`

5. 验证默认数据已写入：
   ```sql
   -- 默认用户
   SELECT email, nickname, is_superuser FROM "user" WHERE email = 'admin@ragflow.io';
   -- 默认租户
   SELECT id, name FROM tenant LIMIT 5;
   -- legacy LLM factories 表（当前初始化代码不再填充，记录实际数量即可）
   SELECT COUNT(*) FROM llm_factories;
   -- 系统设置
   SELECT * FROM system_settings;
   ```
   预期：admin 用户存在，默认租户存在，`system_settings` 有数据。当前
   `init_web_data()` 已注释 `init_llm_factory()`，系统 provider 清单从
   `conf/llm_factories.json` 加载，故 legacy `llm_factories` 表允许为 0；不得再把
   该表非空作为启动通过条件。tenant 的实际 provider/model 记录由公开 API 另行配置和验证。

6. 验证 SQL trace 中无 MySQL-only 语法：
   - 不应出现反引号（`` ` ``）
   - 不应出现 `AUTO_INCREMENT`
   - 不应出现 `ON DUPLICATE KEY UPDATE`
   - 不应出现 `SHOW PROCESSLIST`

**预期结果**：40 张表创建成功，当前代码仍负责的默认用户/租户/系统设置写入，无
MySQL-only SQL 执行；legacy `llm_factories` 只记录观察值

---

### TC-SM-002: 重复启动幂等性

**前置条件**：已完成 TC-SM-001

**步骤**：

1. 记录当前表数量和索引数量：
   ```sql
   SELECT COUNT(*) AS table_count FROM information_schema.tables
    WHERE table_schema = current_schema() AND table_type = 'BASE TABLE';

   SELECT COUNT(*) AS index_count FROM pg_indexes
    WHERE schemaname = current_schema();
   ```

2. 重启 RAGFlow 后端服务（使用相同配置）

3. 再次查询表数量和索引数量

4. 对比前后结果

**预期结果**：表数量和索引数量完全一致，日志中无 DDL 错误

---

### TC-SM-003: 并发启动锁保护

**前置条件**：使用与 TC-SM-001 相同的独立测试库/schema，并在本用例开始时再次清空

**步骤**：

1. 对当前组的同一配置，同时启动两个只执行初始化的 RAGFlow Python 子进程。不要启动两个使用同一 HTTP 端口的常驻 API：
   ```bash
   RAG_PROJECT_BASE=<group_root> DB_TYPE=<group_db_type> \
     python -c 'from common import settings; settings.init_settings(); from api.db.db_models import init_database_tables; init_database_tables()' &
   PID1=$!

   RAG_PROJECT_BASE=<group_root> DB_TYPE=<group_db_type> \
     python -c 'from common import settings; settings.init_settings(); from api.db.db_models import init_database_tables; init_database_tables()' &
   PID2=$!

   wait $PID1 $PID2
   ```

2. 等待两个进程都完成初始化

3. 验证最终 catalog 完整性：
   ```sql
   SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = current_schema() AND table_type = 'BASE TABLE';
   ```

4. 检查两个子进程退出码和独立日志，确认锁把初始化主体串行化

**预期结果**：两个进程都正常退出，catalog 完整（40 张表），无重复 DDL 错误

---

## 2. 结构迁移

### TC-SM-004: 缺列补齐

**前置条件**：Schema 中有旧版表结构，缺少新增列

**步骤**：

1. 记录默认管理员行和各业务表行数，然后删除当前迁移清单中的明确目标列 `document.content_hash`（模拟旧版）：
   ```sql
   ALTER TABLE document DROP COLUMN content_hash;
   ```

2. 启动 RAGFlow

3. 验证列已补齐：
   ```sql
   SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = current_schema()
    AND table_name = 'document'
    ORDER BY ordinal_position;
   ```

4. 复查默认管理员和用例前记录的业务行，确认未被迁移删除

**预期结果**：`document.content_hash` 按当前模型恢复为可空 `VARCHAR(32)` 并带索引，已有数据不丢失

---

### TC-SM-005: tenant_llm 主键升级

**前置条件**：Schema 中有旧版 `tenant_llm` 表，使用复合主键而非 `id` 主键

**步骤**：

1. 不使用备份。在当前组已初始化的 `tenant_llm` 表中插入一条所有复合键字段均非空的迁移 fixture；随后按当前数据库方言删除 `id` 主键/序列和 `uk_tenant_llm`，恢复 `(tenant_id,llm_factory,llm_name)` 复合主键。构造脚本必须保存构造前后的 catalog 快照。

2. 插入测试数据：
   ```sql
   INSERT INTO tenant_llm
     (tenant_id, llm_factory, model_type, llm_name, api_key,
      max_tokens, used_tokens, status)
   VALUES
     ('migration_fixture_tenant', 'OpenAI', 'chat', 'migration-fixture-model',
      'test-only-key', 8192, 0, '1');
   ```

   `max_tokens`、`used_tokens`、`status` 的 Peewee `default` 属于应用层默认值，手写
   SQL 不会自动获得这些值；迁移 fixture 必须显式提供，不能把夹具缺列导致的 1364
   误判成迁移失败。

3. 启动 RAGFlow，触发 `migrate_db()`

4. 验证迁移结果：
   ```sql
   -- 检查是否有 id 列
   SELECT column_name FROM information_schema.columns
    WHERE table_name = 'tenant_llm' AND column_name = 'id';

   -- 检查主键约束
   SELECT conname, contype FROM pg_constraint
    WHERE conrelid = 'tenant_llm'::regclass AND contype = 'p';

   -- 检查旧数据保留
   SELECT * FROM tenant_llm WHERE tenant_id = 'test_tenant';

   -- 实验组检查 sequence 存在；当前 GaussDB 不保证提供 PostgreSQL
   -- pg_sequences 视图，使用 pg_class/pg_namespace；对照组检查 AUTO_INCREMENT
   SELECT c.relname
     FROM pg_class c
     JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = current_schema()
      AND c.relkind = 'S'
      AND c.relname LIKE '%tenant_llm%';
   ```

**预期结果**：两组都恢复 `id` 主键并保留旧数据且 id 已赋值；复合唯一约束存在。对照组使用 AUTO_INCREMENT，实验组使用 `tenant_llm_id_seq`

---

### TC-SM-006: user.email 唯一索引恢复

**前置条件**：`user.email` 上没有唯一索引

**步骤**：

1. 检查当前索引状态：
   ```sql
   SELECT indexname, indexdef FROM pg_indexes
    WHERE tablename = 'user' AND indexname LIKE '%email%';
   ```

2. 在当前测试库中明确删除 `user.email` 唯一索引，再启动 RAGFlow；删除前后保存 catalog 快照

3. 验证唯一索引已创建：
   ```sql
   SELECT indexname, indexdef FROM pg_indexes
    WHERE tablename = 'user' AND indexname LIKE '%email%';
   ```

4. 测试唯一约束生效：
   ```bash
   # 注册第一个用户
   curl -s -X POST http://127.0.0.1:9380/api/v1/users \
     -H "Content-Type: application/json" \
     -d '{"email":"unique-test@example.com","nickname":"User1","password":"<RSA-encrypted-password>"}'

   # 尝试注册同名用户
   curl -s -X POST http://127.0.0.1:9380/api/v1/users \
     -H "Content-Type: application/json" \
     -d '{"email":"unique-test@example.com","nickname":"User2","password":"<RSA-encrypted-password>"}'
   ```
   第二个请求预期返回错误

**预期结果**：`user.email` 唯一索引存在，重复邮箱注册被拒绝

---

### TC-SM-007: 历史索引清理

**前置条件**：存在已废弃的旧索引

**步骤**：

1. 按当前 `migrate_db()` 的真实清理清单创建三个历史索引（不存在时创建）：
   ```sql
   -- 具体 MySQL/GaussDB 方言由用例脚本分支处理
   CREATE INDEX idx_api_key_provider_id ON tenant_model_instance (api_key, provider_id);
   CREATE INDEX tenantmodelinstance_api_key_provider_id ON tenant_model_instance (api_key, provider_id);
   CREATE INDEX idx_provider_model_instance ON tenant_model (provider_id, model_name, instance_id);
   ```

2. 启动 RAGFlow

3. 验证三个真实历史索引均被清理：
   ```sql
   SELECT indexname FROM pg_indexes
    WHERE indexname IN ('idx_api_key_provider_id',
                        'tenantmodelinstance_api_key_provider_id',
                        'idx_provider_model_instance');
   ```

**预期结果**：迁移逻辑中声明要删除的旧索引已不存在

---

## 3. 空字符串兼容字段

### TC-SM-008: GaussDB 空字符串根因验证

**前置条件**：真实 GaussDB A-compatible 环境

**步骤**：

1. 创建测试表：
   ```sql
   CREATE TABLE test_empty_string (
     id SERIAL PRIMARY KEY,
     name VARCHAR(255) NOT NULL
   );
   ```

2. 尝试插入空字符串：
   ```sql
   INSERT INTO test_empty_string (name) VALUES ('');
   ```
   对照组预期：MySQL 保留真实空字符串，插入成功且 `name IS NULL` 为 false。

   实验组预期：先确认 `SHOW sql_compatibility` 为 `A` 或 `ORA`，插入触发 `23502` NotNullViolation。

3. 清理：
   ```sql
   DROP TABLE IF EXISTS test_empty_string;
   ```

**预期结果**：记录两组实际差异；实验组必须证明 A/ORA-compatible 下空字符串被按 NULL 处理并触发 NOT NULL 约束，对照组必须证明 MySQL 保留空字符串

---

### TC-SM-009: 清单字段 nullable 验证

**前置条件**：已完成 TC-SM-001

**步骤**：

1. 查询所有空字符串兼容字段的 nullable 状态：
   ```sql
   SELECT table_name, column_name, is_nullable
    FROM information_schema.columns
    WHERE table_schema = current_schema()
    AND (table_name, column_name) IN (
      ('tenant', 'llm_id'), ('tenant', 'embd_id'), ('tenant', 'asr_id'),
      ('tenant', 'img2txt_id'), ('tenant', 'rerank_id'),
      ('knowledgebase', 'embd_id'),
      ('dialog', 'llm_id'), ('dialog', 'rerank_id'),
      ('memory', 'embd_id'), ('memory', 'llm_id'),
      ('file', 'source_type'),
      ('system_settings', 'value'),
      ('task', 'task_type'),
      ('sync_logs', 'error_msg'), ('sync_logs', 'full_exception_trace')
    )
    ORDER BY table_name, column_name;
   ```
   预期：两组都必须返回完整 15 个字段。对照组 MySQL 记录当前模型的 nullable
   基线（空字符串不折叠成 NULL，因此不要求全部为 `YES`）；实验组 GaussDB 的
   15 个空字符串兼容字段必须全部为 `YES`。

**预期结果**：字段清单无缺失，所有 GaussDB 空字符串兼容字段在存储层允许 NULL；
MySQL 基线单独记录且不套用 Gauss oracle

---

### TC-SM-010: ORM 空字符串写入和读取

**前置条件**：已创建测试用户并登录

**步骤**：

1. 使用默认 embedding 仍为空的专用测试用户登录，先通过 API/只读 DB 校验该用户 tenant 的 `embd_id` 是应用层空值，再创建数据集且不指定 `embd_id`：
   ```bash
   curl -s -X POST http://127.0.0.1:9380/api/v1/datasets \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"name":"test-empty-embd","chunk_method":"naive"}'
   ```

2. 查询 GaussDB 中 `embd_id` 实际存储值：
   ```sql
   SELECT name, embd_id, embd_id IS NULL AS is_null
    FROM knowledgebase WHERE name = 'test-empty-embd';
   ```
   对照组预期：MySQL 中为真实 `""` 且 `is_null=false`；实验组预期：GaussDB 中为 NULL 且 `is_null=true`

3. 通过 API 查询该数据集（内部模型字段 `embd_id` 会由 REST 层映射为外部字段 `embedding_model`）：
   ```bash
   curl -s -H "Authorization: Bearer $TOKEN" \
     http://127.0.0.1:9380/api/v1/datasets/<id>
   ```
   预期：响应中 `embedding_model` 为 `""`（空字符串），不是 `null`

**预期结果**：对照组 DB 存储真实空串，实验组 DB 存储 NULL；两组 API 的 `embedding_model` 均返回空字符串 `""`

---

### TC-SM-011: 空字符串查询改写验证

**前置条件**：当前代码中存在使用 `EmptyStringCharField` 的模型字段，例如 `Memory.embd_id`

**步骤**：

1. 在对照组环境编译 `Memory.embd_id == ""` 的 ORM 查询，记录生成 SQL 和绑定参数。

2. 在实验组环境编译 `Memory.embd_id == ""` 的 ORM 查询，记录生成 SQL 和绑定参数。

3. 读取 `api/apps/restful_apis/memory_api.py`，确认当前 `/api/v1/memories` 列表接口不会读取 `embd_id` 查询参数；本用例验证点应落在 ORM 字段条件改写，而不是 `GET /api/v1/memories?embd_id=`。

4. 对比两组 SQL：
   - 对照组预期：普通等值条件，例如 ``memory.embd_id = %s``，绑定参数为 `""`
   - 实验组预期：NULL-aware 条件，包含 `IS NULL` 和 `LENGTH(...)=0`

**预期结果**：GaussDB 下空字符串查询被正确改写为 NULL-aware 表达式

---

## 4. MySQL-only 迁移保护

### TC-SM-012: GaussDB 不执行 MySQL 迁移脚本

**前置条件**：两组均从独立运行根启动

**步骤**：

1. 先启动对照组并保存日志，再启动实验组并保存日志

2. 搜索日志中是否出现 MySQL-only 关键词：
   ```bash
   grep -E "mysql_migration|AUTO_INCREMENT|ON DUPLICATE|SHOW PROCESSLIST" logs/ragflow.log
   ```
   对照组允许/预期进入 MySQL migration 与 MySQL 健康分支；实验组不得出现 MySQL migration、`AUTO_INCREMENT`、`ON DUPLICATE` 或 `SHOW PROCESSLIST` 的实际执行记录。注释/静态配置文本不计为执行。

3. 验证 `tools/scripts/mysql_migration.py` 未被调用

**预期结果**：对照组走 MySQL 路径；实验组的 `DB_TYPE=gaussdb` 不执行 MySQL-only 迁移脚本

---

## 5. DDL 方言验证

### TC-SM-013: 表结构方言正确性

**前置条件**：已完成 TC-SM-001

**步骤**：

1. 对照组先记录 MySQL catalog（AUTO_INCREMENT、DATETIME、LONGTEXT 等）作为基线；随后检查实验组所有表的列类型是否符合 PostgreSQL/GaussDB-compatible 方言：
   ```sql
   -- 检查自增主键使用 SERIAL
   SELECT table_name, column_name, data_type, column_default
    FROM information_schema.columns
    WHERE table_schema = current_schema()
    AND column_default LIKE '%nextval%'
    ORDER BY table_name;

   -- 检查时间字段使用 TIMESTAMP（不是 DATETIME）
   SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
    AND data_type = 'timestamp without time zone'
    ORDER BY table_name;

   -- 检查长文本使用 TEXT（不是 LONGTEXT）
   SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
    AND data_type = 'text'
    ORDER BY table_name;
   ```

2. 验证不存在 MySQL-only 类型：
   ```sql
   SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = current_schema()
    AND data_type IN ('longtext', 'mediumtext', 'tinytext', 'datetime');
   ```
   预期：返回 0 行

**预期结果**：对照组使用 MySQL 方言；实验组使用 PostgreSQL/GaussDB-compatible 方言且不存在 MySQL-only 类型

---

### TC-SM-014: 索引方言正确性

**前置条件**：已完成 TC-SM-001

**步骤**：

1. 先检查对照组 MySQL `information_schema.statistics`，再检查实验组 `pg_indexes`：
   ```sql
   SELECT tablename, indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = current_schema()
    ORDER BY tablename, indexname;
   ```

2. 验证：
   - 标识符使用双引号（`"table"`）或未引用，不使用反引号
   - 无 MySQL-only 索引语法

**预期结果**：对照组使用 MySQL 索引方言；实验组所有索引使用 PostgreSQL/GaussDB-compatible 方言

---

## 6. 配置归一化

### TC-SM-015: DB_TYPE 别名归一化

**步骤**：

1. 在两组各自独立运行根中导入当前 `common.settings`，随后以配置单元探针调用 `normalize_database_type()` 检查以下值。`postgresql` 仅验证别名归一化；当前部署并未提供 PostgreSQL 连接配置，不把“缺少 PostgreSQL 密码”误判为别名失败：
   - `DB_TYPE=GaussDB` → 应为 `gaussdb`
   - `DB_TYPE=gauss` → 应为 `gaussdb`
   - `DB_TYPE=GAUSSDB` → 应为 `gaussdb`
   - `DB_TYPE=gaussdb` → 应为 `gaussdb`
   - `DB_TYPE=postgresql` → 应为 `postgres`（不是 gaussdb）

2. 可通过隔离单测验证：
   ```bash
   DB_TYPE=GaussDB .venv/bin/pytest test/unit_test/common/test_gaussdb_settings.py -q
   ```

**预期结果**：所有 GaussDB 别名归一化为 `gaussdb`，`postgresql` 归一化为 `postgres`

---

### TC-SM-016: GAUSSDB_METADATA_SCHEMA 安全校验

**步骤**：

1. 测试合法 schema：
   ```bash
   GAUSSDB_METADATA_SCHEMA=ragflow_meta # 应通过
   GAUSSDB_METADATA_SCHEMA=public       # 应通过
   GAUSSDB_METADATA_SCHEMA=_test_schema # 应通过
   ```

2. 测试非法 schema：
   ```bash
   GAUSSDB_METADATA_SCHEMA="test; DROP TABLE" # 应被拒绝
   GAUSSDB_METADATA_SCHEMA="test-schema"       # 应被拒绝（含减号）
   GAUSSDB_METADATA_SCHEMA="123schema"         # 应被拒绝（数字开头）
   GAUSSDB_METADATA_SCHEMA=""                  # 应使用默认值
   ```

**预期结果**：非法 schema 在配置加载阶段被拒绝，不进入 SQL options

---

### TC-SM-017: GAUSSDB_METADATA_* 与 GAUSSDB_* 隔离

**步骤**：

1. metadata DB 使用 `GAUSSDB_METADATA_*` 环境变量；DocEngine/Memory Store 使用该实例独立运行根中 `conf/service_conf.yaml` 的 `gaussdb.config`。设置两套不同且可识别的值：
   ```bash
   # Metadata DB
   GAUSSDB_METADATA_HOST=meta-host
   GAUSSDB_METADATA_DBNAME=meta_db

   # DocEngine/Memory Store（conf/service_conf.yaml）
   gaussdb.config.host=doc-host
   gaussdb.config.database=doc_db
   ```

2. 对照组先用 `DB_TYPE=mysql,DOC_ENGINE=infinity` 加载，确认 metadata 选择 MySQL、DocEngine 选择 Infinity，且 GaussDB metadata 环境变量不会覆盖 MySQL。随后实验组用 `DB_TYPE=gaussdb,DOC_ENGINE=gaussdb` 加载并检查 `settings.DATABASE`：
   - `host` 应为 `meta-host`（不是 `doc-host`）
   - `name` 应为 `meta_db`（不是 `doc_db`）

3. 检查 `settings.docStoreConn` 的连接目标（如果 `DOC_ENGINE=gaussdb`）：
   - 应使用 `doc-host` 和 `doc_db`

**预期结果**：metadata DB 和 DocEngine 使用各自独立的连接配置

---

## 7. Admin 和健康检查

### TC-SM-018: Admin 展示 GaussDB 配置

**前置条件**：两组 Admin 服务均运行

**步骤**：

1. 先登录对照组 Admin 并访问服务列表，随后登录实验组 Admin 并访问服务列表：
   ```bash
   curl -s -H "Authorization: Bearer <admin-token>" \
     http://127.0.0.1:9481/api/v1/admin/services | python3 -m json.tool
   ```

2. 验证展示内容：
   - 对照组 metadata 类型为 `mysql`、retrieval 类型为 `infinity`
   - 实验组 metadata 类型为 `gaussdb`、retrieval 类型为 `gaussdb`
   - 各组 Host/Port/User/Schema 与该组配置相符
   - 密码或其它 secret 不得显示明文；若响应泄露，按安全缺陷记录

**预期结果**：Admin 正确展示 GaussDB metadata 配置

---

### TC-SM-019: 健康检查使用 SELECT 1

**前置条件**：两组 Admin 均运行，能够获取 superuser token

**步骤**：

1. 先登录对照组 Admin，找到 `meta_type=mysql` 的 service id 并调用详情；再登录实验组 Admin，找到 `meta_type=gaussdb` 的 service id 并调用详情：
   ```bash
   curl -s -H "Authorization: Bearer <admin-token>" \
     http://127.0.0.1:9481/api/v1/admin/services/<metadata-service-id>
   ```

2. 检查 SQL trace：
   - 对照组 MySQL health 可使用 `SHOW PROCESSLIST`
   - 实验组应使用 `SELECT 1;`，不得使用 `SHOW PROCESSLIST`

**预期结果**：健康检查使用 GaussDB-compatible SQL

---

## 8. Docker/Helm 部署

### TC-SM-020: Docker Compose profile 隔离

**步骤**：

1. 先用 `DB_TYPE=mysql,DOC_ENGINE=infinity` 生成对照组服务列表并确认 MySQL 激活；再显式设置实验组 profile 并生成服务列表和完整配置，避免把 `docker compose config` 中“已定义但未激活”的服务误判为会启动：
   ```bash
   DB_TYPE=gaussdb DOC_ENGINE=gaussdb DEVICE=cpu \
   COMPOSE_PROFILES=gaussdb,cpu,metadata-gaussdb \
     docker compose -f docker/docker-compose.yml config --services
   DB_TYPE=gaussdb DOC_ENGINE=gaussdb DEVICE=cpu \
   COMPOSE_PROFILES=gaussdb,cpu,metadata-gaussdb \
     docker compose -f docker/docker-compose.yml config
   ```

2. 验证：
   - MySQL 服务不在启动列表中
   - 应用服务不强制依赖 MySQL
   - `GAUSSDB_METADATA_*` 环境变量正确传递

**预期结果**：Docker Compose 正确隔离 MySQL 和 GaussDB profile

---

### TC-SM-021: Helm values 不强制 MySQL

**步骤**：

1. 先用默认 `DB_TYPE=mysql,mysql.enabled=true` 渲染对照组并确认包含 MySQL Secret/Workload；再渲染实验组：
   ```bash
   helm template ragflow ./helm \
     --set env.DB_TYPE=gaussdb \
     --set mysql.enabled=false \
     --set env.GAUSSDB_METADATA_HOST=gaussdb-host \
     --set env.GAUSSDB_METADATA_PASSWORD=test-pwd
   ```

2. 验证：
   - 不要求 `MYSQL_PASSWORD`
   - `GAUSSDB_METADATA_*` 进入环境变量

**预期结果**：Helm 部署支持 `DB_TYPE=gaussdb` 且不强依赖 MySQL
