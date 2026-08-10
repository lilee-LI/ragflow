# 10 - GaussDB 适配故障与恢复测试

## 目标与边界

本组只验证 GaussDB metadata/DocEngine/Memory 适配直接涉及的错误分类、重试、连接恢复、事务、权限、向量和方言边界。通用认证、安全、业务 CRUD、压力和存储引擎泛测不属于本组。

当前共 **27 个**用例：`TC-FR-001`～`TC-FR-012`、`TC-FR-018`～`TC-FR-024`、`TC-FR-027`、`TC-FR-037`、`TC-FR-038`、`TC-FR-044`、`TC-FR-045`、`TC-FR-051`～`TC-FR-053`。

主要实现位置：

- `api/db/db_error_utils.py`：SQLSTATE/errno 分类；
- `api/db/db_models.py`：`PsycopgRetryMixin`；
- `api/db/services/common_service.py`：`retry_deadlock_operation()`；
- `common/doc_store/gaussdb_conn_pool.py`：连接验证、丢弃和 Schema 权限；
- `common/doc_store/gaussdb_conn_base.py`：标识符、健康和 A/ORA 兼容检查；
- `memory/utils/gaussdb_conn.py`：Memory 表、向量和 gsdiskann 索引恢复。

## 执行与安全约定

- 每例均从新 fixture 开始，先执行对照组、再执行实验组；禁止复用备份脚本、备份结果、旧连接或旧业务 ID。
- Live 故障只通过本轮专属 TCP proxy/专属 schema/专属表注入；禁止停止共享数据库、广泛终止连接、修改宿主网络或撤销共享运行账号权限。
- 不能安全稳定制造的驱动错误使用确定性 fake driver/pool，但必须调用真实 classifier/retry/pool 代码；fake 不替代被测实现。
- 故障用例必须证明故障命中、恢复动作、恢复后请求和数据一致性；一次 HTTP 200 不足以判定通过。
- 密码、DSN、Token 只允许保存脱敏值或指纹。

## 一、错误分类与重试（12 例）

以下用例均使用真实 classifier/retry 代码。正向连接恢复用专属 proxy 补充 live 证据；不能安全制造的 SQLSTATE 使用包装异常链 fixture。

### TC-FR-001: 08xxx 连接错误触发 metadata 重试

`is_psycopg_connection_error()` 必须为真；`PsycopgRetryMixin` 触发有界退避，并在连接恢复后返回与基准一致的只读结果。

### TC-FR-002: 57P01/57P02/57P03 服务关闭错误

三个 SQLSTATE 均进入连接恢复分支；预算内恢复成功，预算耗尽时受控失败且不泄露连接信息。

### TC-FR-003: 40P01 死锁重试

`retry_deadlock_operation()` 总尝试不超过 3 次，退避为 0.1/0.2 秒；重试完整操作并且不产生重复写。

### TC-FR-004: 40001 序列化冲突重试

错误被判为可重试事务冲突；完整事务重试后只得到一个一致结果。

### TC-FR-005: 55P03 行锁不可用重试

GaussDB 分支可重试；对照组按真实 MySQL 锁错误语义取证，不伪造相同错误码或相同实现。

### TC-FR-006: 42701 重复列幂等分类

`is_duplicate_column_error()` 为真，迁移代码只跳过重复列错误，其他 DDL 错误仍传播。

### TC-FR-007: 42P07 重复对象幂等分类

`is_duplicate_object_error()` 为真，重复表/索引进入幂等处理且不掩盖无关异常。

### TC-FR-008: 42704 未定义对象幂等分类

`is_undefined_object_error()` 为真，删除缺失对象可幂等处理且 catalog 保持正确。

### TC-FR-009: 23505 唯一冲突不重试

该错误不属于连接或事务可重试错误；异常原样传播，写操作不得被盲重放。

### TC-FR-010: 23502 非空冲突不重试

该错误不重试；事务回滚，连接退出 aborted 状态后仍可使用。

### TC-FR-011: 42601 语法错误不重试

该错误不重试，也不得误判为可忽略的幂等 DDL。

### TC-FR-012: 22P02 类型转换错误不重试

该错误不重试，且不能因异常文本中偶然出现连接相关单词而误触发恢复。

## 二、索引与连接恢复（7 例）

### TC-FR-018: gsdiskann maintenance_work_mem 恢复

使用确定性 cursor 先返回 `maintenance_work_mem` 不足，再成功建索引；验证仅该错误按固定上限提高 work mem 重试，其他 DDL 错误立即传播，最终索引存在且无重复对象。

### TC-FR-019: 查询过程中连接中断

通过专属 proxy 在只读延迟查询已发送后 reset。metadata adapter 可安全重放只读语句；DocEngine/Memory 不承诺重放已开始 SQL，但必须丢弃坏连接并使下一次调用恢复。

### TC-FR-020: 连接池耗尽

使用 `maxconn=2` 的隔离 pool 并发借出连接。峰值不得超过 2；耗尽表现必须有界；归还后 `SELECT 1` 成功且无连接泄漏。

### TC-FR-021: 连接超时

专属 proxy 对隔离连接启用 blackhole，并设置短 `connect_timeout`。超时在容差内发生且异常脱敏；清除 fault 后新连接成功。

### TC-FR-022: 上游不可达并恢复

只将当前组 proxy upstream 置为 down 并 reset 旧连接。证明故障命中；恢复 upstream 后 metadata 新连接、DocEngine/Memory 新调用均恢复，数据 ID 集合不变。

### TC-FR-023: SSL 连接断开分类

确定性异常文本 `SSL connection has been closed unexpectedly` 必须被连接 classifier 识别。只有 live 环境确实启用 TLS 时才声明 TLS 重连成功；否则只验证 classifier 与普通 TCP 恢复。

### TC-FR-024: 坏连接验证与丢弃

fake pool 依次返回 stale/valid connection。`get_conn()` 必须以 `putconn(close=True)` 丢弃 stale connection 并返回 valid connection；连续两个坏连接时最多验证两次并抛分类异常。

## 三、并发与配置边界（3 例）

### TC-FR-027: 并发 Memory 消息写入

通过公开 Message API 并发提交唯一消息。所有消息只出现一次；GaussDB MERGE/序列分配不产生重复 ID，向量维度正确，Memory size cache 与产品公式一致；两组 fixture 相互隔离。

### TC-FR-037: Schema 配置标识符注入

实验组分别向 metadata schema 归一化和 DocEngine `load_gaussdb_config()` 提交恶意标识符。前者抛 `ValueError`，后者抛 `InvalidGaussDBConfig`，且在建连/DDL 前失败；对照组不读取 Gauss-only 配置；catalog 无变化。

### TC-FR-038: 索引名称标识符注入

直接调用 `GaussDBDDLBuilder.validate_identifier()`。非法名称抛 `InvalidGaussDBObjectName`，不得发送到数据库；catalog canary 和 metadata 表保持不变。

## 四、权限与事务（2 例）

### TC-FR-044: Schema 权限检查

Live 正向 `check_schema_access()` 成功；fake cursor 分别模拟缺少 USAGE/CREATE。真实 pool 方法必须抛 `GaussDBPermissionError`、归还连接并在消息中列出缺失权限且不含密码。

### TC-FR-045: 事务错误后回滚

在当前组专属表的单一真实事务中先插入两行，再触发唯一冲突。事务退出后两行均不存在，连接离开 aborted 状态且 `SELECT 1` 成功。

## 五、Memory 向量与 GaussDB 方言（3 例）

### TC-FR-051: floatvector NULL/空向量处理

分别提交 `content_embed=None`、空列表和错误维度，均不得产生新行或破坏旧行。GaussDB 未使用维度为零向量且 `q_<dim>_vec_empty=TRUE`，真实维度为 FALSE；跨维覆盖时旧维度重置。`vector_literal()` 的输入边界单独核对。

### TC-FR-052: A/ORA 兼容模式约束

实验组 `SHOW sql_compatibility` 必须为 `A` 或 `ORA`，并完成产品依赖的 VARCHAR2/JSONB/USTORE 专属 DDL 与事务 canary；对照组执行等价 MySQL DDL，不解析 GaussDB 方言。对象最终全部清理。

### TC-FR-053: 标识符长度限制

`validate_identifier()` 接受合法 63 字符、拒绝 64 字符及非法字符；`index_name()` 对组合超长名称生成不超过 63 字符且稳定的哈希后缀。只允许使用 builder 返回的安全名称创建专属对象。

## 完成标准

- 当前计划恰好包含上述 27 个唯一 ID；
- 每个已执行用例都有两组独立、脱敏、可复核证据；
- 故障全部恢复，专属 proxy/toxic/schema/table/row 均清理；
- 通用产品测试不计入本组适配通过率。
