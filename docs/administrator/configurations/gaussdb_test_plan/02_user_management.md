# 02 - 用户管理测试方案

## 概述

本测试方案覆盖 RAGFlow 系统中用户管理的完整生命周期测试，包括用户注册、登录、个人资料管理、状态切换、角色管理、删除、重名用户处理、用户列表等核心功能。每个用例先在 MySQL 对照组执行，再在 GaussDB 实验组执行；所有业务写入优先通过主 API 或 Admin API 完成。

**测试范围**：
- 用户注册（含各类边界和异常输入）
- 用户登录与认证
- 新用户完整流程（注册→登录→获取→更新→验证）
- 用户状态切换（激活/停用）
- 用户角色切换（普通用户/superuser）
- 删除用户及级联清理
- 重名用户处理（删除后重建）
- 用户列表查询
- 个人资料更新（含 GaussDB 空字符串兼容字段）

**涉及数据表**：
- `user` — 用户主表
- `tenant` — 租户表
- `user_tenant` — 用户与租户关联表
- `file` — 文件表（注册时创建根目录）

**API 端点前缀**：`/api/v1`

**关键模型字段**（定义于 `api/db/db_models.py:848`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | CharField(32) | 主键，UUID |
| `access_token` | CharField(255) | 认证令牌，登录时刷新 |
| `nickname` | EmptyStringCharField(100) | 昵称，空字符串兼容 |
| `password` | CharField(255) | 密码哈希（werkzeug pbkdf2/scrypt） |
| `email` | CharField(255), unique | 邮箱，唯一索引 |
| `avatar` | TextField | 头像 base64 |
| `language` | CharField(32) | 语言偏好，默认 Chinese/English |
| `color_schema` | CharField(32) | 配色方案，默认 Bright |
| `status` | CharField(1) | 状态：`"1"`=有效，`"0"`=作废 |
| `is_active` | CharField(1) | 是否可用：`"1"`=可用，`"0"`=禁用 |
| `is_superuser` | BooleanField | 是否超级管理员 |
| `last_login_time` | DateTimeField | 最后登录时间 |

**密码处理流程**（`api/utils/crypt.py`）：
- 浏览器端：原始密码 → Base64 → RSA 公钥加密 → POST
- API 端只接受 RSA 加密后的字符串；`decrypt()` 不提供明文回退。本文 HTTP 示例里的可读密码是测试 fixture，测试脚本发送前必须调用 `api.utils.crypt.crypt()` 转成密文
- 存储层：`generate_password_hash(decrypt(password))` — 哈希的是 Base64 后的字符串
- 验证层：`check_password_hash(stored_hash, decrypt(input_password))`

**认证方式**：
- 登录成功后响应头 `Authorization` 返回 itsdangerous 签名 Token
- 后续请求使用 `Authorization: Bearer <token>` 或 `Authorization: <token>`

**Nickname 验证规则**（`api/utils/nickname_validation.py`）：
- 最大长度 100 字符（`NICKNAME_MAX_LENGTH`）
- 正则：`^[\w ._'-]+$`（`re.UNICODE` 标志，`\w` 匹配中文字符）
- 空字符串和纯空格被拒绝
- 错误码：`RetCode.ARGUMENT_ERROR`（101）

**当前错误码基线**（`common/constants.py`）：

- `ARGUMENT_ERROR=101`
- `DATA_ERROR=102`
- `OPERATING_ERROR=103`
- `AUTHENTICATION_ERROR=109`
- `FORBIDDEN=403`
- `SERVER_ERROR=500`

除专门验证非法密文的用例外，所有 `password`、`new_password` 字段都必须由测试脚本在发送前 RSA 加密。所有角色值按代码实际存储为小写：`owner`、`normal`、`invite`。

本文 SQL 示例以 GaussDB 双引号/时间表达式为主。对照组只读校验和明确允许的状态构造必须使用 MySQL 等价方言；不得把 GaussDB SQL 直接发往 MySQL 后把语法错误记成产品失败。

Token 用例必须使用无 cookie 的独立请求，或在每次请求前显式清空 cookie。主 API 支持服务端 session fallback；若复用登录时的 session cookie，会掩盖旧/无效 Authorization Token 的真实失败。

**受保护字段列表**（PATCH `/users/me` 中被 `continue` 跳过）：
`password`, `new_password`, `email`, `status`, `is_superuser`, `login_channel`, `is_anonymous`, `is_active`, `is_authenticated`, `last_login_time`

---

## 一、用户注册测试

### TC-UM-001: 正常用户注册 - 最小参数集

**前置条件**：
- RAGFlow 服务已启动，`REGISTER_ENABLED=1`
- 邮箱 `gaussdb-test-001@example.com` 未被注册

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "nickname": "TestUser001",
  "password": "Test@1234"
}
```

2. 两组共同预期：HTTP 200、`code=0`，并返回新用户资料：

```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "nickname": "TestUser001",
    "email": "gaussdb-test-001@example.com",
    "language": "English",
    "color_schema": "Bright",
    "status": "1",
    "is_active": "1",
    "is_superuser": false
  },
  "message": "TestUser001, welcome aboard!"
}
```
- 响应头包含 `Authorization: <itsdangerous-signed-token>`

3. 数据库验证：
```sql
-- 验证 user 表
SELECT id, email, nickname, status, is_active, is_superuser, login_channel
FROM "user"
WHERE email = 'gaussdb-test-001@example.com';

-- 验证 tenant 表已创建
SELECT t.id, t.name FROM tenant t
JOIN user_tenant ut ON t.id = ut.tenant_id
WHERE ut.user_id = (SELECT id FROM "user" WHERE email = 'gaussdb-test-001@example.com');

-- 验证 user_tenant 关联（角色为 owner）
SELECT ut.user_id, ut.tenant_id, ut.role FROM user_tenant ut
JOIN "user" u ON ut.user_id = u.id
WHERE u.email = 'gaussdb-test-001@example.com';

-- 验证 file 根目录已创建
SELECT f.id, f.name, f.type, f.tenant_id, f.created_by FROM file f
JOIN "user" u ON f.tenant_id = u.id AND f.created_by = u.id
WHERE u.email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 用户注册成功，返回 code=0
- `user` 表中 `status='1'`，`is_active='1'`，`is_superuser=false`，`login_channel='password'`
- `password` 字段存储为 `scrypt:` 或 `pbkdf2:` 格式的哈希值（非明文）
- `tenant` 表已创建对应租户记录
- `user_tenant` 表已创建关联，`role='owner'`
- `file` 表已创建根目录记录
- 响应头包含有效的 Authorization Token

---

### TC-UM-002: 重复邮箱注册

**前置条件**：
- 邮箱 `gaussdb-test-001@example.com` 已被注册（TC-UM-001）

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "nickname": "TestUser002",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 103,
  "data": false,
  "message": "Email: gaussdb-test-001@example.com has already registered!"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 注册失败，`code=103`（RetCode.OPERATING_ERROR）
- 数据库中仍只有一条该邮箱的记录
- 不会创建新的 tenant 或 user_tenant 记录

---

### TC-UM-003: 缺少 email 字段注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "nickname": "TestUser003",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "email is required!"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE nickname = 'TestUser003';
```

**预期结果**：
- `validate_request("nickname", "email", "password")` 装饰器拦截，返回参数缺失错误
- 数据库无新记录

---

### TC-UM-004: 缺少 password 字段注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-004@example.com",
  "nickname": "TestUser004"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "password is required!"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-004@example.com';
```

**预期结果**：
- 注册失败，返回参数缺失错误
- 数据库无新记录

---

### TC-UM-005: 缺少 nickname 字段注册

> 适用范围：本用例只验证公开注册接口 `POST /api/v1/users`。Admin 前端“新建用户”走
> `POST /api/v1/admin/users`，请求体为 `username + password`，不要求填写
> `nickname`，其预期行为在 `02_user_management_admin_supplement.md` 中单独归档。

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-005@example.com",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "nickname is required!"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-005@example.com';
```

**预期结果**：
- 注册失败，返回参数缺失错误
- 数据库无新记录

---

### TC-UM-006: 无效邮箱格式注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 依次发送以下无效邮箱格式请求：

a) 无 @ 符号：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "invalid-email",
  "nickname": "TestUser006a",
  "password": "Test@1234"
}
```

b) 无域名：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "user@",
  "nickname": "TestUser006b",
  "password": "Test@1234"
}
```

c) 无用户名：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "@example.com",
  "nickname": "TestUser006c",
  "password": "Test@1234"
}
```

d) 缺少顶级域名：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "user@example",
  "nickname": "TestUser006d",
  "password": "Test@1234"
}
```

2. 每个请求预期响应：HTTP 200
```json
{
  "code": 103,
  "data": false,
  "message": "Invalid email address: <对应邮箱>!"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email LIKE 'invalid%' OR email LIKE 'user@%' OR email LIKE '@%';
```

**预期结果**：
- 所有无效邮箱格式被正则 `^[\w\._-]+@([\w_-]+\.)+[\w-]{2,}$` 拒绝
- 数据库无新记录

---

### TC-UM-007: 空字符串密码注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-007@example.com",
  "nickname": "TestUser007",
  "password": ""
}
```

2. 测试脚本把空密码按正常协议执行 RSA 加密后发送。预期 HTTP 200、`code=0`；`decrypt(<encrypted-empty>)` 返回空字符串，当前后端没有密码最小长度校验

3. 数据库验证（若成功）：
```sql
SELECT id, email, password FROM "user"
WHERE email = 'gaussdb-test-007@example.com';
```

**预期结果**：
- 当前代码无密码强度校验
- 记录实际行为（成功或失败）

---

### TC-UM-008: 空字符串邮箱注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "",
  "nickname": "TestUser008",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 103,
  "data": false,
  "message": "Invalid email address: !"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = '';
```

**预期结果**：
- 空字符串邮箱不匹配正则，被拒绝
- 数据库无新记录

---

### TC-UM-009: 空字符串 nickname 注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-009@example.com",
  "nickname": "",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname cannot be empty."
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-009@example.com';
```

**预期结果**：
- `validate_nickname()` 拒绝空字符串
- `code=101`（RetCode.ARGUMENT_ERROR）
- 数据库无新记录

---

### TC-UM-010: 纯空格 nickname 注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-010@example.com",
  "nickname": "   ",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname cannot be empty."
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-010@example.com';
```

**预期结果**：
- `validate_nickname()` 内部 `strip()` 后为空，被拒绝
- 数据库无新记录

---

### TC-UM-011: nickname 包含非法字符注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 依次发送含非法字符的 nickname 请求：

a) 包含特殊符号：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-011a@example.com",
  "nickname": "User@#$%",
  "password": "Test@1234"
}
```

b) 包含尖括号：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-011b@example.com",
  "nickname": "<script>alert(1)</script>",
  "password": "Test@1234"
}
```

c) 包含换行符：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-011c@example.com",
  "nickname": "User\nName",
  "password": "Test@1234"
}
```

2. 每个请求预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname contains invalid characters."
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email LIKE 'gaussdb-test-011%@example.com';
```

**预期结果**：
- nickname 正则 `^[\w ._'-]+$` 仅允许字母数字、空格、点、下划线、撇号、连字符
- 所有非法字符被拒绝
- 数据库无新记录

---

### TC-UM-012: SQL 注入尝试注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求（邮箱字段注入）：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "test'; DROP TABLE \"user\"; --@example.com",
  "nickname": "SQLInject",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 103,
  "data": false,
  "message": "Invalid email address: test'; DROP TABLE \"user\"; --@example.com!"
}
```

3. 数据库验证：
```sql
-- 验证 user 表未被删除
SELECT COUNT(*) FROM "user" WHERE email LIKE 'gaussdb-test-%@example.com';
-- 验证注入邮箱未写入
SELECT COUNT(*) FROM "user" WHERE email LIKE '%DROP TABLE%';
```

4. 发送请求（nickname 字段注入）：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-012@example.com",
  "nickname": "Robert'; DROP TABLE students;--",
  "password": "Test@1234"
}
```

5. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname contains invalid characters."
}
```

**预期结果**：
- 邮箱中的 SQL 注入被正则拒绝（`;` 和 `--` 不在允许字符集中）
- nickname 中的 `;` 和 `--` 被正则拒绝（`'` 被允许但 `;` 不被允许）
- Peewee ORM 参数化查询防止 SQL 注入
- `user` 表完整无损

---

### TC-UM-013: 超长 nickname 注册（101 字符）

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求（nickname 101 字符，超过 NICKNAME_MAX_LENGTH=100）：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-013@example.com",
  "nickname": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname must be at most 100 characters."
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-013@example.com';
```

**预期结果**：
- 101 字符 nickname 被 `validate_nickname()` 拒绝
- 数据库无新记录

---

### TC-UM-014: 边界值 nickname 注册（恰好 100 字符）

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求（nickname 恰好 100 字符）：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-014@example.com",
  "nickname": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "email": "gaussdb-test-014@example.com",
    "nickname": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
  }
}
```

3. 数据库验证：
```sql
SELECT id, nickname, LENGTH(nickname) AS nick_len
FROM "user"
WHERE email = 'gaussdb-test-014@example.com';
```

**预期结果**：
- 100 字符 nickname 注册成功
- 数据库中 nickname 长度恰好为 100
- GaussDB 下 `nickname` 字段正常存储长字符串

---

### TC-UM-015: 中文 nickname 注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-015@example.com",
  "nickname": "测试用户",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "email": "gaussdb-test-015@example.com",
    "nickname": "测试用户"
  },
  "message": "测试用户, welcome aboard!"
}
```

3. 数据库验证：
```sql
SELECT id, nickname FROM "user"
WHERE email = 'gaussdb-test-015@example.com';
```

**预期结果**：
- nickname 正则 `^[\w ._'-]+$` 使用 `re.UNICODE` 标志，`\w` 匹配中文字符
- 中文 nickname 注册成功
- GaussDB 正确存储 UTF-8 中文字符

---

## 二、用户登录测试

### TC-UM-016: 正确邮箱密码登录

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已注册，密码为 `Test@1234`

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "nickname": "TestUser001",
    "email": "gaussdb-test-001@example.com",
    "language": "English",
    "color_schema": "Bright",
    "status": "1",
    "is_active": "1",
    "last_login_time": "<timestamp>"
  },
  "message": "Welcome back!"
}
```
- 响应头包含 `Authorization: <itsdangerous-signed-token>`

3. 数据库验证：
```sql
SELECT id, email, last_login_time, access_token
FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 登录成功，`code=0`
- 响应体包含用户信息（含 `email`，因为 `for_self=True`）
- 不包含 `password` 和 `access_token` 字段（被 `to_safe_dict` 过滤）
- 响应头 `Authorization` 包含有效 Token
- `last_login_time` 已更新
- `access_token` 已刷新为新 UUID

---

### TC-UM-017: 错误密码登录

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已注册

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "password": "WrongPassword"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Email and password do not match!"
}
```

3. 数据库验证：
```sql
-- last_login_time 不应被更新
SELECT last_login_time FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 登录失败，`code=109`（RetCode.AUTHENTICATION_ERROR）
- `last_login_time` 不变
- `access_token` 不变

---

### TC-UM-018: 未注册邮箱登录

**前置条件**：
- 邮箱 `nonexistent@example.com` 未注册

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "nonexistent@example.com",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Email: nonexistent@example.com is not registered!"
}
```

3. 数据库验证：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'nonexistent@example.com';
```

**预期结果**：
- 登录失败，提示邮箱未注册
- 数据库无该邮箱记录

---

### TC-UM-019: 空邮箱登录

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Email:  is not registered!"
}
```

3. 数据库验证：无需

**预期结果**：
- 空邮箱查询不到用户，返回认证错误

---

### TC-UM-020: 空密码登录

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已注册

**步骤**：
1. 发送请求：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "password": ""
}
```

2. 测试脚本将空密码正常 RSA 加密后发送。预期 HTTP 200、`code=109`：解密得到空字符串，`check_password_hash` 失败；本用例不发送非法密文

3. 数据库验证：
```sql
SELECT last_login_time FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 空密码无法通过验证
- `last_login_time` 不变

---

### TC-UM-021: 邮箱大小写敏感性验证

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已注册

**步骤**：
1. 发送请求（大写邮箱）：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "GAUSSDB-TEST-001@EXAMPLE.COM",
  "password": "Test@1234"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Email: GAUSSDB-TEST-001@EXAMPLE.COM is not registered!"
}
```

3. 数据库验证：
```sql
-- 检查 GaussDB collation 设置
SELECT datcollate FROM pg_database WHERE datname = current_database();
-- 验证实际存储的邮箱
SELECT email FROM "user" WHERE LOWER(email) = 'gaussdb-test-001@example.com';
```

**预期结果**：
- Peewee 两组都使用 `=`，但最终语义由数据库 collation 决定
- 记录 MySQL 与 GaussDB 的真实大小写行为和 collation
- 若对照组成功而实验组失败，按跨数据库行为差异记录并结合产品邮箱契约判定，不得把对照组成功误判为测试脚本错误

---

### TC-UM-022: Authorization Header 验证

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已登录，获取到 Token

**步骤**：
1. 登录获取 Token：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "password": "Test@1234"
}
```
从响应头提取 `Authorization` 值记为 `$TOKEN`。

2. 使用 Token 访问受保护资源：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

3. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "email": "gaussdb-test-001@example.com",
    "nickname": "TestUser001"
  }
}
```

4. 使用错误 Token 访问：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer invalid_token_here
```

5. 预期响应：HTTP 401、`code=401`、消息 `Unauthorized`
```json
{
  "code": 109,
  "data": false,
  "message": "Unauthorized!"
}
```

**预期结果**：
- 有效 Token 可正常访问
- 无效 Token 返回认证错误
- Token 是 itsdangerous 签名的 `access_token` 序列化值

---

### TC-UM-023: 多次登录会话管理

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已注册

**步骤**：
1. 第一次登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "password": "Test@1234"
}
```
记录响应头 Token 为 `$TOKEN1`。

2. 第二次登录（同一用户）：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com",
  "password": "Test@1234"
}
```
记录响应头 Token 为 `$TOKEN2`。

3. 使用旧 Token 访问：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN1
```

4. 使用新 Token 访问：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN2
```

5. 数据库验证：
```sql
SELECT access_token FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 每次登录刷新 `access_token`（新 UUID）
- 旧 Token `$TOKEN1` 失效（因为 `access_token` 已被替换）
- 新 Token `$TOKEN2` 有效
- 数据库中 `access_token` 为最新值

---

### TC-UM-024: 空请求体登录

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 发送空请求体：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Unauthorized!"
}
```

**预期结果**：
- 空请求体被 `get_request_json()` 识别为无效
- 直接返回认证错误，不进入密码验证流程

---

### TC-UM-025: 缺失 password 字段登录

**前置条件**：
- 用户 `gaussdb-test-001@example.com` 已注册

**步骤**：
1. 发送请求（无 password 字段）：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-001@example.com"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 500,
  "data": false,
  "message": "Fail to crypt password"
}
```

3. 数据库验证：
```sql
-- last_login_time 不应更新
SELECT last_login_time FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- `password` 为 None 时 `decrypt()` 抛出异常
- 登录失败，`last_login_time` 不变

---

## 三、新用户完整流程测试

### TC-UM-026: 注册 → 登录完整流程

**前置条件**：
- 邮箱 `gaussdb-test-026@example.com` 未注册

**步骤**：
1. 注册新用户：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "nickname": "FlowTest026",
  "password": "Test@1234"
}
```
记录响应头 Token 为 `$REG_TOKEN`，响应体 `data.id` 为 `$USER_ID`。

2. 验证注册后自动登录（注册成功即自动登录）：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $REG_TOKEN
```
预期响应：HTTP 200，`code=0`

3. 退出登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/logout
Authorization: Bearer $REG_TOKEN
```
预期响应：HTTP 200，`code=0`，`data=true`

4. 使用旧 Token 访问（应失败）：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $REG_TOKEN
```
预期响应：认证失败

5. 重新登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "Test@1234"
}
```
记录新 Token 为 `$LOGIN_TOKEN`。

6. 在第 3 步退出后、重新登录前先只读校验 `access_token` 为 `INVALID_`；第 5 步重新登录后再校验它已变成新的 UUID：
```sql
-- 该查询在退出后、重新登录前执行
SELECT access_token FROM "user"
WHERE email = 'gaussdb-test-026@example.com';

-- 验证 tenant 和 user_tenant 关联完整
SELECT u.id AS user_id, t.id AS tenant_id, ut.role
FROM "user" u
JOIN user_tenant ut ON u.id = ut.user_id
JOIN tenant t ON ut.tenant_id = t.id
WHERE u.email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- 注册成功后自动登录，可直接使用 Token
- 退出后 `access_token` 被设为 `INVALID_` 前缀
- 重新登录获取新 Token 后可正常使用
- tenant 和 user_tenant 关联完整

---

### TC-UM-027: 登录后获取个人资料

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已登录，Token 为 `$LOGIN_TOKEN`

**步骤**：
1. 获取个人资料：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $LOGIN_TOKEN
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": {
    "id": "<uuid>",
    "nickname": "FlowTest026",
    "email": "gaussdb-test-026@example.com",
    "avatar": null,
    "language": "English",
    "color_schema": "Bright",
    "timezone": "UTC+8\tAsia/Shanghai",
    "last_login_time": "<timestamp>",
    "is_active": "1",
    "is_superuser": false,
    "status": "1",
    "login_channel": "password"
  }
}
```

3. 数据库验证：
```sql
SELECT id, nickname, email, language, color_schema, timezone, status, is_active, is_superuser
FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- 返回完整用户资料（含 `email`，因为 `for_self=True`）
- 不包含 `password` 和 `access_token`
- 所有字段值与注册时设置一致
- `language` 默认值取决于系统 LANG 环境变量

---

### TC-UM-028: 更新用户个人资料

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已登录，Token 为 `$LOGIN_TOKEN`

**步骤**：
1. 更新 nickname 和 language：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $LOGIN_TOKEN
Content-Type: application/json

{
  "nickname": "UpdatedUser026",
  "language": "English",
  "color_schema": "Dark"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": true
}
```

3. 验证更新生效：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $LOGIN_TOKEN
```
预期响应：`nickname` = `UpdatedUser026`，`language` = `English`，`color_schema` = `Dark`

4. 数据库验证：
```sql
SELECT nickname, language, color_schema
FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- 更新成功，`code=0`，`data=true`
- 再次查询返回更新后的值
- 数据库字段已更新

---

### TC-UM-029: 更新资料后验证不可修改受保护字段

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已登录

**步骤**：
1. 尝试修改受保护字段：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $LOGIN_TOKEN
Content-Type: application/json

{
  "email": "hacked@example.com",
  "status": "0",
  "is_superuser": true,
  "is_active": "0",
  "nickname": "ProtectedTest"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": true
}
```

3. 数据库验证：
```sql
SELECT email, status, is_superuser, is_active, nickname
FROM "user"
WHERE id = (SELECT id FROM "user" WHERE email = 'gaussdb-test-026@example.com');
```

**预期结果**：
- `email` 保持不变（被代码 `continue` 跳过）
- `status` 保持不变
- `is_superuser` 保持 `false`
- `is_active` 保持 `"1"`
- `nickname` 更新为 `ProtectedTest`（nickname 不在保护列表中）
- 受保护字段列表：`password`, `new_password`, `email`, `status`, `is_superuser`, `login_channel`, `is_anonymous`, `is_active`, `is_authenticated`, `last_login_time`

---

### TC-UM-030: 修改密码流程

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已登录，当前密码为 `Test@1234`

**步骤**：
1. 修改密码：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $LOGIN_TOKEN
Content-Type: application/json

{
  "password": "Test@1234",
  "new_password": "NewPass@5678"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": true
}
```

3. 使用新密码登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```
预期响应：登录成功

4. 使用旧密码登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "Test@1234"
}
```
预期响应：登录失败

5. 数据库验证：
```sql
SELECT password FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- 密码修改成功
- 新密码可登录
- 旧密码无法登录
- 数据库中 `password` 哈希值已变更

---

## 四、用户状态切换测试

### TC-UM-031: 管理员停用用户（设置 is_active=0）

**前置条件**：
- 管理员已登录，Token 为 `$ADMIN_TOKEN`
- 目标用户 `gaussdb-test-026@example.com` 状态为激活

**步骤**：
1. 管理员通过独立 Admin API 停用目标用户：
```http
PUT /api/v1/admin/users/gaussdb-test-026@example.com/activate
Authorization: Bearer $ADMIN_TOKEN
Content-Type: application/json

{"activate_status":"off"}
```

2. 数据库验证：
```sql
SELECT email, is_active, status FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `is_active` 已更新为 `'0'`
- `status` 保持为 `'1'`（两个字段独立控制）

---

### TC-UM-032: 被停用用户登录失败

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 的 `is_active` 已被设为 `'0'`（TC-UM-031）

**步骤**：
1. 被停用用户尝试登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 403,
  "data": false,
  "message": "This account has been disabled, please contact the administrator!"
}
```

3. 数据库验证：
```sql
-- last_login_time 不应更新
SELECT last_login_time FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `is_active='0'` 的用户被代码拦截：`user.is_active == "0"` 返回 FORBIDDEN
- `code=403`（RetCode.FORBIDDEN）
- `last_login_time` 不变
- 即使密码正确也无法登录

---

### TC-UM-033: 重新激活用户

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 的 `is_active='0'`

**步骤**：
1. 管理员通过 Admin API 重新激活用户：
```http
PUT /api/v1/admin/users/gaussdb-test-026@example.com/activate
Authorization: Bearer $ADMIN_TOKEN
Content-Type: application/json

{"activate_status":"on"}
```

2. 数据库验证：
```sql
SELECT is_active FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

3. 用户重新登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

4. 预期响应：HTTP 200，`code=0`

**预期结果**：
- `is_active` 恢复为 `'1'` 后用户可正常登录
- 密码未变更，仍为 `NewPass@5678`

---

### TC-UM-034: 设置 status=0（无效用户）

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已激活

**步骤**：
1. 设置用户 status 为无效：
```sql
UPDATE "user" SET status = '0', update_time = EXTRACT(EPOCH FROM NOW()) * 1000
WHERE email = 'gaussdb-test-026@example.com';
```

2. 用户尝试登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

3. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Email and password do not match!"
}
```

4. 数据库验证：
```sql
SELECT status, is_active FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `status='0'` 时 `UserService.query_user()` 查询条件 `status == "1"` 不匹配
- 用户无法被查到，返回"Email and password do not match"
- 与 `is_active='0'` 不同，`status='0'` 导致查询不到用户

---

### TC-UM-035: status=0 与 is_active=0 的区别验证

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 当前 `status='0'`，`is_active='1'`

**步骤**：
1. 验证 status=0 的登录行为（应失败，用户查不到）：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```
预期响应：`code=109`，"Email and password do not match!"

2. 直接数据库操作仅恢复本用例专门构造的 `status='1'`；随后通过 Admin API 把 `is_active` 设为 off：
```sql
UPDATE "user" SET status = '1'
WHERE email = 'gaussdb-test-026@example.com';
```

```http
PUT /api/v1/admin/users/gaussdb-test-026@example.com/activate
Authorization: Bearer $ADMIN_TOKEN
Content-Type: application/json

{"activate_status":"off"}
```

3. 再次登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```
预期响应：`code=403`，"This account has been disabled..."

4. 数据库验证：
```sql
SELECT status, is_active FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `status='0'`：用户查不到，返回认证错误（109）
- `is_active='0'`：用户能查到但被禁用，返回 FORBIDDEN（403）
- 两种机制独立运作，错误消息不同

---

### TC-UM-036: 恢复 status 和 is_active 后完整验证

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 当前 `status='1'`，`is_active='0'`

**步骤**：
1. 直接数据库操作仅恢复本组专门测试的 `status='1'`，再通过 Admin API 恢复 `is_active`：
```sql
UPDATE "user" SET status = '1'
WHERE email = 'gaussdb-test-026@example.com';
```

```http
PUT /api/v1/admin/users/gaussdb-test-026@example.com/activate
Authorization: Bearer $ADMIN_TOKEN
Content-Type: application/json

{"activate_status":"on"}
```

2. 登录验证：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

3. 预期响应：HTTP 200，`code=0`

4. 获取资料验证：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $NEW_TOKEN
```

5. 预期响应：HTTP 200，`code=0`，用户资料完整

6. 数据库验证：
```sql
SELECT status, is_active, last_login_time FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- 完全恢复后用户可正常登录和访问
- `last_login_time` 已更新

---

## 五、用户角色切换测试

### TC-UM-037: 设置用户为 superuser

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 当前 `is_superuser=false`

**步骤**：
1. 通过 Admin API 授予 superuser：
```http
PUT /api/v1/admin/users/gaussdb-test-026@example.com/admin
Authorization: Bearer $ADMIN_TOKEN
```

2. 数据库验证：
```sql
SELECT email, is_superuser FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

3. 用户登录后获取资料：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

4. 预期响应：`data.is_superuser = true`

5. 验证 `UserService.is_admin()`：
```sql
-- 确认 is_superuser 字段值
SELECT is_superuser FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `is_superuser` 更新为 `true`
- 登录后返回的 `data.is_superuser = true`
- `UserService.is_admin(user_id)` 返回 `True`

---

### TC-UM-038: superuser 访问管理员功能

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 的 `is_superuser=true`
- 用户已登录

**步骤**：
1. 验证 `is_admin` 检查：
```sql
SELECT is_superuser FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

2. 使用该 superuser token 调用真正的管理员功能，而不只读取 Profile：
```http
GET http://127.0.0.1:9381/api/v1/admin/users
Authorization: Bearer $ADMIN_USER_TOKEN
```

3. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": [
    {
      "email": "gaussdb-test-026@example.com"
    }
  ]
}
```

**预期结果**：
- superuser 可通过 Admin 鉴权并获得用户列表
- 普通用户对相同接口应被拒绝（由补充计划进一步覆盖）

---

### TC-UM-039: 取消 superuser 权限

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 当前 `is_superuser=true`

**步骤**：
1. 由另一个 superuser 通过 Admin API 取消权限：
```http
DELETE /api/v1/admin/users/gaussdb-test-026@example.com/admin
Authorization: Bearer $ADMIN_TOKEN
```

2. 数据库验证：
```sql
SELECT is_superuser FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

3. 重新登录并验证：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

4. 预期响应：`data.is_superuser = false`

**预期结果**：
- `is_superuser` 恢复为 `false`
- 登录后 `data.is_superuser = false`

---

### TC-UM-040: 通过 PATCH /users/me 尝试设置 is_superuser（应被忽略）

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已登录，`is_superuser=false`

**步骤**：
1. 尝试通过 API 自行提权：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "is_superuser": true
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": true
}
```

3. 数据库验证：
```sql
SELECT is_superuser FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

4. 验证资料：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

**预期结果**：
- `is_superuser` 在 `setting_user()` 代码中被 `continue` 跳过
- 数据库中 `is_superuser` 保持 `false`
- 自行提权攻击被阻止

---

### TC-UM-041: 通过 PATCH /users/me 尝试修改 status（应被忽略）

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已登录

**步骤**：
1. 尝试修改 status：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "status": "0"
}
```

2. 预期响应：HTTP 200，`code=0`，`data=true`

3. 数据库验证：
```sql
SELECT status FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `status` 在保护列表中，被 `continue` 跳过
- 数据库中 `status` 保持不变

---

## 六、删除用户测试

### TC-UM-042: 通过数据库软删除用户（status=0）

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 状态正常

**步骤**：
1. 执行软删除（模拟 `UserService.delete_user()` 行为）：
```sql
UPDATE "user" SET status = '0', update_time = EXTRACT(EPOCH FROM NOW()) * 1000
WHERE email = 'gaussdb-test-026@example.com';
```

2. 数据库验证：
```sql
SELECT email, status, is_active FROM "user"
WHERE email = 'gaussdb-test-026@example.com';
```

**预期结果**：
- `status` 更新为 `'0'`
- 用户记录仍存在于数据库中（软删除）

---

### TC-UM-043: 软删除后用户登录失败

**前置条件**：
- 用户 `gaussdb-test-026@example.com` 已被软删除（`status='0'`）

**步骤**：
1. 尝试登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-026@example.com",
  "password": "NewPass@5678"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Email and password do not match!"
}
```

3. 使用之前的 Token 访问：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $OLD_TOKEN
```

4. 预期响应：HTTP 401、`code=401`。鉴权查询强制 `status='1'`，即使数据库中的 `access_token` 未清除也不能通过

**预期结果**：
- `status='0'` 导致 `query_user()` 查不到用户
- 无法登录
- 旧 Token 必须失效；`api.apps._load_user()` 按 `access_token` 查询时同时过滤 `status='1'`

---

### TC-UM-044: 完整删除用户数据（级联清理）

**前置条件**：
- 准备一个新用户 `gaussdb-test-044@example.com` 已注册并创建了数据集等资源

**步骤**：
1. 先注册用户并创建资源：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-044@example.com",
  "nickname": "DeleteTest044",
  "password": "Test@1234"
}
```

2. 获取用户 ID：
```sql
SELECT id FROM "user" WHERE email = 'gaussdb-test-044@example.com';
```
记为 `$DEL_USER_ID`。

3. 从注册响应取得 `$DEL_USER_ID`；通过只读查询取得 owner tenant id 记为 `$DEL_TENANT_ID`。先使用该用户 token 通过 API 创建一个测试 dataset，再由管理员通过 API 停用并删除用户：

```http
PUT /api/v1/admin/users/gaussdb-test-044@example.com/activate
Authorization: Bearer $ADMIN_TOKEN
Content-Type: application/json

{"activate_status":"off"}
```

```http
DELETE /api/v1/admin/users/gaussdb-test-044@example.com
Authorization: Bearer $ADMIN_TOKEN
```

删除请求必须返回 `code=0`；这条路由实际调用 `delete_user_data()`，不得用手写 DELETE 模拟级联逻辑。

4. 数据库验证：
```sql
-- 验证 user 已删除
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-044@example.com';

-- 验证 tenant 已删除
SELECT COUNT(*) FROM tenant WHERE id = '$DEL_TENANT_ID';

-- 验证 user_tenant 已删除
SELECT COUNT(*) FROM user_tenant WHERE user_id = '$DEL_USER_ID';

-- 验证 file 根目录已删除
SELECT COUNT(*) FROM file WHERE tenant_id = '$DEL_USER_ID' OR created_by = '$DEL_USER_ID';
```

**预期结果**：
- 所有关联记录被级联删除
- 各表查询结果均为 0
- 无孤立数据残留

---

### TC-UM-045: 物理删除后邮箱可重新注册

**前置条件**：
- 用户 `gaussdb-test-044@example.com` 已被物理删除（TC-UM-044）

**步骤**：
1. 使用相同邮箱重新注册：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-044@example.com",
  "nickname": "Reborn044",
  "password": "NewPass@9999"
}
```

2. 预期响应：HTTP 200，`code=0`

3. 登录验证：
```http
POST http://127.0.0.1:9380/api/v1/auth/login
Content-Type: application/json

{
  "email": "gaussdb-test-044@example.com",
  "password": "NewPass@9999"
}
```

4. 预期响应：登录成功

5. 数据库验证：
```sql
SELECT id, email, nickname FROM "user"
WHERE email = 'gaussdb-test-044@example.com';
```

**预期结果**：
- 物理删除后邮箱可重新注册
- 新用户的 `id` 与旧用户不同
- 新用户无任何历史数据

---

### TC-UM-046: 级联删除 tenant 后的数据一致性

**前置条件**：
- 已执行 TC-UM-044 的级联删除

**步骤**：
1. 验证无孤立 tenant 数据：
```sql
-- 检查是否有 user_tenant 引用不存在的 user
SELECT ut.id, ut.user_id, ut.tenant_id
FROM user_tenant ut
LEFT JOIN "user" u ON ut.user_id = u.id
WHERE u.id IS NULL;

-- 检查是否有 user_tenant 引用不存在的 tenant
SELECT ut.id, ut.user_id, ut.tenant_id
FROM user_tenant ut
LEFT JOIN tenant t ON ut.tenant_id = t.id
WHERE t.id IS NULL;

-- 检查是否有 file 引用不存在的 user
SELECT f.id, f.tenant_id, f.created_by
FROM file f
WHERE f.tenant_id NOT IN (SELECT id FROM tenant)
   OR f.created_by NOT IN (SELECT id FROM "user");
```

2. 数据库验证：以上所有查询结果应为空

**预期结果**：
- 级联删除后无数据孤岛
- 外键引用完整性得到保持

---

## 七、重名用户测试

### TC-UM-047: 创建 → 删除 → 重新创建同邮箱用户

**前置条件**：
- 邮箱 `gaussdb-test-047@example.com` 未注册

**步骤**：
1. 创建用户：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-047@example.com",
  "nickname": "RecreateUser047",
  "password": "Test@1234"
}
```
记录 `data.id` 为 `$FIRST_ID`。

2. 通过 Admin API 先停用再物理删除用户；保存 `$FIRST_ID` 和只读 catalog 快照：
```http
PUT /api/v1/admin/users/gaussdb-test-047@example.com/activate
Authorization: Bearer $ADMIN_TOKEN
Content-Type: application/json

{"activate_status":"off"}
```

```http
DELETE /api/v1/admin/users/gaussdb-test-047@example.com
Authorization: Bearer $ADMIN_TOKEN
```

3. 验证删除：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'gaussdb-test-047@example.com';
```
预期：0

4. 重新创建同邮箱用户：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-047@example.com",
  "nickname": "NewRecreate047",
  "password": "NewPass@5678"
}
```
记录 `data.id` 为 `$SECOND_ID`。

5. 数据库验证：
```sql
SELECT id, email, nickname, password FROM "user"
WHERE email = 'gaussdb-test-047@example.com';
```

**预期结果**：
- `$FIRST_ID` != `$SECOND_ID`（新的 UUID）
- `nickname` 为新值 `NewRecreate047`
- `password` 为新密码的哈希
- 数据库中仅一条记录

---

### TC-UM-048: 软删除用户后同邮箱不可重新注册

**前置条件**：
- 用户 `gaussdb-test-048@example.com` 已注册

**步骤**：
1. 注册用户：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-048@example.com",
  "nickname": "SoftDelUser048",
  "password": "Test@1234"
}
```

2. 软删除（仅设 status=0，记录仍在数据库）：
```sql
UPDATE "user" SET status = '0' WHERE email = 'gaussdb-test-048@example.com';
```

3. 尝试用同邮箱重新注册：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-048@example.com",
  "nickname": "RetryUser048",
  "password": "Test@1234"
}
```

4. 预期响应：HTTP 200
```json
{
  "code": 103,
  "data": false,
  "message": "Email: gaussdb-test-048@example.com has already registered!"
}
```

5. 数据库验证：
```sql
SELECT COUNT(*), MAX(status) FROM "user"
WHERE email = 'gaussdb-test-048@example.com';
```

**预期结果**：
- `UserService.query(email=...)` 仅按 email 查询，不过滤 status
- 软删除后邮箱仍被占用，不可重新注册
- 数据库中仍只有一条记录

---

## 八、用户列表测试

### TC-UM-049: 管理员获取所有用户列表

**前置条件**：
- 已存在多个测试用户
- 管理员已登录

**步骤**：
1. 通过独立 Admin 服务查询用户列表：
```http
GET http://127.0.0.1:9381/api/v1/admin/users
Authorization: Bearer $ADMIN_TOKEN
```

2. 用只读数据库查询核对返回邮箱集合和排序：
```sql
SELECT email, nickname, status, is_active, is_superuser, create_time
FROM "user"
ORDER BY email ASC;
```
（Admin 服务端口 9381）

**预期结果**：
- 返回所有用户列表
- 按邮箱排序
- 包含 Admin 当前实现公开的 `email`、`nickname`、`create_date`、`is_active`、`is_superuser`；该列表不返回 tenant role 或 `status`

---

### TC-UM-050: 租户内用户列表

**前置条件**：
- 管理员或 tenant owner 已登录
- 存在多用户的 tenant

**步骤**：
1. 调用 `GET /api/v1/tenants` 取得 owner tenant id，记为 `$TENANT_ID`。为本用例注册一个专用成员并由 owner 调用 `POST /tenants/$TENANT_ID/users` 邀请，确保 tenant 中存在非 owner 关联。

2. 获取租户用户列表：
```http
GET http://127.0.0.1:9380/api/v1/tenants/$TENANT_ID/users
Authorization: Bearer $OWNER_TOKEN
```

3. 预期响应：HTTP 200
```json
{
  "code": 0,
  "data": [
    {
      "user_id": "<uuid>",
      "role": "invite"
    }
  ]
}
```

4. 数据库验证：
```sql
SELECT ut.user_id, ut.tenant_id, ut.role, u.email
FROM user_tenant ut
JOIN "user" u ON ut.user_id = u.id
WHERE ut.tenant_id = '$TENANT_ID';
```

**预期结果**：
- 当前 `UserTenantService.get_by_tenant_id()` 明确过滤 `role != owner`，因此返回 tenant 内的非 owner 成员，不返回 owner 自身
- 返回成员包含实际小写角色 `normal` 或 `invite`

---

### TC-UM-051: 非 owner 访问租户用户列表被拒绝

**前置条件**：
- 租户内存在非 owner 用户

**步骤**：
1. 使用非 owner 用户访问：
```http
GET http://127.0.0.1:9380/api/v1/tenants/$TENANT_ID/users
Authorization: Bearer $NON_OWNER_TOKEN
```

2. 预期响应：HTTP 200、`code=109`、消息 `No authorization.`

**预期结果**：
- 非 owner 用户无法查看租户用户列表
- 返回权限错误

---

### TC-UM-052: 邀请用户加入租户

**前置条件**：
- Tenant owner 已登录
- 目标用户 `gaussdb-test-052@example.com` 已注册

**步骤**：
1. 注册用户：
```http
POST http://127.0.0.1:9380/api/v1/users
Content-Type: application/json

{
  "email": "gaussdb-test-052@example.com",
  "nickname": "InviteTest052",
  "password": "Test@1234"
}
```

2. Owner 邀请用户到租户：
```http
POST http://127.0.0.1:9380/api/v1/tenants/$TENANT_ID/users
Authorization: Bearer $OWNER_TOKEN
Content-Type: application/json

{
  "email": "gaussdb-test-052@example.com"
}
```

3. 预期响应：HTTP 200，`code=0`

4. 数据库验证：
```sql
SELECT ut.user_id, ut.tenant_id, ut.role
FROM user_tenant ut
JOIN "user" u ON ut.user_id = u.id
WHERE u.email = 'gaussdb-test-052@example.com';
```

**预期结果**：
- 被邀请用户在 `user_tenant` 表中有新记录
- 角色为 `invite`（`api/db/__init__.py::UserTenantRole.INVITE` 的实际存储值）
- 用户同时拥有自己 tenant（`owner`）和被邀请 tenant（`invite`）的关联

---

## 九、个人资料测试

### TC-UM-053: 更新 nickname 字段

**前置条件**：
- 用户已登录

**步骤**：
1. 更新 nickname：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "nickname": "New Nickname_053"
}
```

2. 预期响应：HTTP 200，`code=0`，`data=true`

3. 验证：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

4. 数据库验证：
```sql
SELECT nickname FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- nickname 更新成功
- 包含空格、下划线的合法 nickname 可正常保存
- 代码会执行 `strip()` 去除首尾空格

---

### TC-UM-054: 更新 language 字段

**前置条件**：
- 用户已登录

**步骤**：
1. 更新 language：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "language": "English"
}
```

2. 预期响应：HTTP 200，`code=0`

3. 数据库验证：
```sql
SELECT language FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- `language` 更新为 `English`
- `language` 不在受保护字段列表中，可正常更新

---

### TC-UM-055: 更新 color_schema 字段

**前置条件**：
- 用户已登录

**步骤**：
1. 更新 color_schema：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "color_schema": "Dark"
}
```

2. 预期响应：HTTP 200，`code=0`

3. 数据库验证：
```sql
SELECT color_schema FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- `color_schema` 更新为 `Dark`

---

### TC-UM-056: 更新 avatar 字段（GaussDB 空字符串兼容验证）

**前置条件**：
- 用户已登录

**步骤**：
1. 设置 avatar 为空字符串：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "avatar": ""
}
```

2. 预期响应：HTTP 200，`code=0`

3. 数据库验证：
```sql
SELECT avatar, avatar IS NULL AS is_null FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

4. 获取资料验证：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

**预期结果**：
- `avatar` 字段为 `TextField(null=True)`，不是 `EmptyStringCharField`
- 对照组 MySQL 预期存储并回读真实 `""`
- 实验组 A/ORA-compatible GaussDB 预期存储 NULL，普通 `TextField` 回读为 JSON `null`
- `avatar` 是可选字段且未列入空字符串兼容清单；上述差异按字段契约记录。若前端或 API 明确要求字符串而因 `null` 失败，再判为兼容缺陷

---

### TC-UM-057: 设置 nickname 为空字符串（EmptyStringCharField 验证）

**前置条件**：
- 用户已登录

**步骤**：
1. 尝试将 nickname 设为空字符串：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "nickname": ""
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname cannot be empty."
}
```

3. 尝试纯空格：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "nickname": "   "
}
```

4. 预期响应：同上

5. 数据库验证：
```sql
SELECT nickname FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- `validate_nickname()` 拦截空字符串和纯空格
- 数据库中 nickname 未变更
- EmptyStringCharField 的隔离机制不影响校验逻辑

---

### TC-UM-058: 特殊字符 nickname 更新验证

**前置条件**：
- 用户已登录

**步骤**：
1. 设置含合法特殊字符的 nickname：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "nickname": "User's Name_Test-01"
}
```

2. 预期响应：HTTP 200，`code=0`

3. 数据库验证：
```sql
SELECT nickname FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

4. 验证返回值：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

5. 尝试非法字符：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "nickname": "User@Name"
}
```

6. 预期响应：
```json
{
  "code": 101,
  "data": false,
  "message": "Nickname contains invalid characters."
}
```

**预期结果**：
- 合法字符（撇号 `'`、下划线 `_`、连字符 `-`、空格、点 `.`）可正常保存
- `@` 等非法字符被拒绝
- GaussDB 正确存储和返回含特殊字符的字符串

---

## 十、GaussDB 特定验证测试

### TC-UM-059: EmptyStringCharField nickname 存储验证

**前置条件**：
- 用户已注册，nickname 为普通字符串

**步骤**：
1. 通过 API 将 nickname 更新为含空格字符串：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "nickname": "Space User"
}
```

2. 数据库直接验证（确认 GaussDB 存储行为）：
```sql
SELECT nickname, LENGTH(nickname), OCTET_LENGTH(nickname)
FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

3. API 读取验证：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

4. 确认返回的 nickname 为 `"Space User"`（非 NULL）

**预期结果**：
- `EmptyStringCharField` 正确存储非空字符串
- 读取时正确返回，不会将空格字符串误判为空
- GaussDB 下 VARCHAR 类型正确存储空格

---

### TC-UM-060: 多用户并发注册

**前置条件**：
- RAGFlow 服务已启动

**步骤**：
1. 并发发送 5 个注册请求（使用不同邮箱）：
```bash
for i in $(seq 1 5); do
  curl -s -X POST http://127.0.0.1:9380/api/v1/users \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"gaussdb-test-concurrent-$i@example.com\",\"nickname\":\"Concurrent$i\",\"password\":\"Test@1234\"}" &
done
wait
```

2. 数据库验证：
```sql
SELECT email, nickname, status, create_time
FROM "user"
WHERE email LIKE 'gaussdb-test-concurrent-%@example.com'
ORDER BY email;
```

3. 验证每个用户都有关联的 tenant 和 user_tenant：
```sql
SELECT u.email, COUNT(ut.id) AS tenant_count
FROM "user" u
JOIN user_tenant ut ON u.id = ut.user_id
WHERE u.email LIKE 'gaussdb-test-concurrent-%@example.com'
GROUP BY u.email;
```

**预期结果**：
- 5 个用户全部注册成功
- 每个用户有且仅有一个 tenant 关联
- 无并发冲突或数据不一致

---

### TC-UM-061: 修改密码时旧密码错误验证

**前置条件**：
- 用户已登录，当前密码为 `Test@1234`

**步骤**：
1. 使用错误旧密码尝试修改：
```http
PATCH http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
Content-Type: application/json

{
  "password": "WrongOldPass",
  "new_password": "NewPass@5678"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 109,
  "data": false,
  "message": "Password error!"
}
```

3. 数据库验证：
```sql
SELECT password FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- 旧密码验证失败，返回认证错误
- 数据库中密码哈希不变
- 新密码未生效

---

### TC-UM-062: 退出登录后 Token 失效验证

**前置条件**：
- 用户已登录，Token 为 `$TOKEN`

**步骤**：
1. 退出登录：
```http
POST http://127.0.0.1:9380/api/v1/auth/logout
Authorization: Bearer $TOKEN
```

2. 预期响应：HTTP 200，`code=0`，`data=true`

3. 使用旧 Token 访问：
```http
GET http://127.0.0.1:9380/api/v1/users/me
Authorization: Bearer $TOKEN
```

4. 预期响应：认证失败

5. 数据库验证：
```sql
SELECT access_token FROM "user"
WHERE email = 'gaussdb-test-001@example.com';
```

**预期结果**：
- `access_token` 被设为 `INVALID_<hex>` 格式
- `UserService.query()` 拒绝 `INVALID_` 前缀的 token
- 旧 Token 无法访问任何受保护资源

---

## 测试清理

每轮测试完成后，先通过 `GET /api/v1/admin/users` 获取本批次测试邮箱；对于被授予 superuser 的 fixture 先 revoke，再逐个调用 Admin activate API 设为 `off`，随后调用 `DELETE /api/v1/admin/users/<email>`。不得用手写 SQL 代替 `delete_user_data()`。最后只读查询 `user`、`tenant`、`user_tenant`、`file` 及已创建资源，确认无本批次残留；清理 API 失败必须记录为本组问题，不能用直接数据库删除掩盖。

---

## 测试用例汇总表

| 编号 | 分类 | 测试名称 | 关键验证点 |
| --- | --- | --- | --- |
| TC-UM-001 | 注册 | 正常注册最小参数集 | user/tenant/user_tenant/file 表创建 |
| TC-UM-002 | 注册 | 重复邮箱 | 唯一约束 |
| TC-UM-003 | 注册 | 缺少 email | validate_request 拦截 |
| TC-UM-004 | 注册 | 缺少 password | validate_request 拦截 |
| TC-UM-005 | 注册 | 缺少 nickname | validate_request 拦截 |
| TC-UM-006 | 注册 | 无效邮箱格式（4 种） | 正则验证 |
| TC-UM-007 | 注册 | 空字符串密码 | 无密码强度校验 |
| TC-UM-008 | 注册 | 空字符串邮箱 | 正则拒绝 |
| TC-UM-009 | 注册 | 空字符串 nickname | validate_nickname 拒绝 |
| TC-UM-010 | 注册 | 纯空格 nickname | strip 后为空被拒 |
| TC-UM-011 | 注册 | 非法字符 nickname（3 种） | 正则验证 |
| TC-UM-012 | 注册 | SQL 注入尝试 | ORM 参数化 + 正则 |
| TC-UM-013 | 注册 | 超长 nickname 101 字符 | NICKNAME_MAX_LENGTH=100 |
| TC-UM-014 | 注册 | 边界值 nickname 100 字符 | 边界验证 |
| TC-UM-015 | 注册 | 中文 nickname | re.UNICODE 匹配 |
| TC-UM-016 | 登录 | 正确邮箱密码 | Token/last_login_time |
| TC-UM-017 | 登录 | 错误密码 | 认证失败 |
| TC-UM-018 | 登录 | 未注册邮箱 | 未注册提示 |
| TC-UM-019 | 登录 | 空邮箱 | 查询不到用户 |
| TC-UM-020 | 登录 | 空密码 | 密码不匹配 |
| TC-UM-021 | 登录 | 邮箱大小写敏感 | GaussDB 字符串比较 |
| TC-UM-022 | 登录 | Authorization Header | Token 有效/无效 |
| TC-UM-023 | 登录 | 多次登录会话 | access_token 刷新 |
| TC-UM-024 | 登录 | 空请求体 | 无效 body 处理 |
| TC-UM-025 | 登录 | 缺失 password 字段 | decrypt 异常处理 |
| TC-UM-026 | 完整流程 | 注册→登录→退出→重登录 | 全链路 |
| TC-UM-027 | 完整流程 | 获取个人资料 | to_safe_dict 输出 |
| TC-UM-028 | 完整流程 | 更新个人资料 | PATCH 更新 |
| TC-UM-029 | 完整流程 | 受保护字段不可修改 | 安全验证 |
| TC-UM-030 | 完整流程 | 修改密码 | 新旧密码验证 |
| TC-UM-031 | 状态切换 | 管理员停用用户 | is_active=0 |
| TC-UM-032 | 状态切换 | 停用用户登录失败 | FORBIDDEN 403 |
| TC-UM-033 | 状态切换 | 重新激活用户 | is_active=1 恢复 |
| TC-UM-034 | 状态切换 | status=0 无效用户 | 查询不到用户 |
| TC-UM-035 | 状态切换 | status vs is_active 区别 | 两种机制独立 |
| TC-UM-036 | 状态切换 | 完全恢复后验证 | 端到端恢复 |
| TC-UM-037 | 角色切换 | 设置 superuser | is_superuser=true |
| TC-UM-038 | 角色切换 | superuser 访问管理功能 | is_admin 验证 |
| TC-UM-039 | 角色切换 | 取消 superuser | is_superuser=false |
| TC-UM-040 | 角色切换 | API 自行提权被拒 | 保护字段拦截 |
| TC-UM-041 | 角色切换 | API 修改 status 被拒 | 保护字段拦截 |
| TC-UM-042 | 删除 | 软删除 status=0 | 数据保留 |
| TC-UM-043 | 删除 | 软删除后登录失败 | 认证拒绝 |
| TC-UM-044 | 删除 | 级联删除完整清理 | user/tenant/user_tenant/file |
| TC-UM-045 | 删除 | 物理删除后邮箱可重注册 | 邮箱唯一约束释放 |
| TC-UM-046 | 删除 | 级联删除数据一致性 | 无孤立数据 |
| TC-UM-047 | 重名 | 创建→删除→重建 | 新 ID，无历史 |
| TC-UM-048 | 重名 | 软删除后邮箱被占用 | query 不过滤 status |
| TC-UM-049 | 列表 | 管理员获取用户列表 | get_all_users |
| TC-UM-050 | 列表 | 租户内用户列表 | tenant users API |
| TC-UM-051 | 列表 | 非 owner 访问被拒 | 权限控制 |
| TC-UM-052 | 列表 | 邀请用户加入租户 | INVITE 角色 |
| TC-UM-053 | 资料 | 更新 nickname | strip + validate |
| TC-UM-054 | 资料 | 更新 language | 非保护字段 |
| TC-UM-055 | 资料 | 更新 color_schema | 非保护字段 |
| TC-UM-056 | 资料 | avatar 空字符串 GaussDB | NULL 兼容 |
| TC-UM-057 | 资料 | nickname 空字符串验证 | EmptyStringCharField |
| TC-UM-058 | 资料 | 特殊字符 nickname | 合法/非法字符 |
| TC-UM-059 | GaussDB | EmptyString 存储验证 | 字段级隔离 |
| TC-UM-060 | GaussDB | 并发注册 | 事务一致性 |
| TC-UM-061 | 资料 | 旧密码错误 | 密码验证 |
| TC-UM-062 | 认证 | 退出后 Token 失效 | INVALID_ 前缀 |
