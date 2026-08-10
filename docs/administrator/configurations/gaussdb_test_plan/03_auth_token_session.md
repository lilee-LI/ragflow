# 03 - 认证、Token 与 Session 测试计划

## 1. 概述

本文档覆盖 RAGFlow 在 GaussDB 适配场景下的认证（Authentication）、API Token 管理、Session 会话管理的完整测试用例集。每个后端/API 用例先在 MySQL+Infinity 对照组执行，再在 GaussDB+GaussDB 实验组执行。测试范围包括：登录认证全流程、登出、OAuth 登录通道、密码忘记与重置、API Token CRUD、Token 过期与刷新、Session 认证、并发登录、无效 Token 处理、密码加密验证。

两组均配置本批次本地 OAuth stub 和 SMTPS 邮件捕获服务，OAuth/OTP 用例不得因缺少外部 GitHub/SMTP 而跳过。Token 轮换用例使用无 cookie 请求，Session 用例则显式保存 cookie，避免两种认证路径互相掩盖。

### 1.1 核心代码文件

| 文件 | 职责 |
|------|------|
| `api/apps/__init__.py` | `login_required` 装饰器、`login_user`/`logout_user`、`_load_user`、Session 配置 |
| `api/apps/restful_apis/user_api.py` | 登录、登出、注册、个人资料、密码重置、OAuth 端点 |
| `api/apps/restful_apis/system_api.py` | API Token CRUD（列表/创建/删除） |
| `api/db/db_models.py` | `User`、`Tenant`、`UserTenant`、`APIToken` 模型定义 |
| `api/db/services/user_service.py` | UserService（认证查询、密码哈希、CRUD） |
| `api/utils/crypt.py` | RSA 加解密（密码传输） |
| `api/utils/api_utils.py` | `generate_confirmation_token`、`active_required` |
| `api/utils/web_utils.py` | OTP/验证码工具、邮件发送、Redis key 辅助函数 |
| `common/settings.py` | `get_secret_key()`、`OAUTH_CONFIG`、`REGISTER_ENABLED` |

### 1.2 涉及数据库表

| 表名 | 关键字段 |
|------|----------|
| `user` | `id`, `access_token`, `nickname`, `password`, `email`, `is_active`, `login_channel`, `status`, `is_superuser`, `last_login_time` |
| `api_token` | `tenant_id`, `token`, `beta`, `dialog_id`, `source` |
| `tenant` | `id`, `name`, `llm_id`, `embd_id` |
| `user_tenant` | `id`, `user_id`, `tenant_id`, `role`, `status` |

### 1.3 认证类型与 Token 格式

```
AUTH_JWT  = "JWT"   -- 登录认证 Token
AUTH_API  = "API"   -- api_token.token 字段（"ragflow-" 前缀）
AUTH_BETA = "BETA"  -- api_token.beta 字段（32 字符截断）
DEFAULT_AUTH_TYPES = (AUTH_JWT, AUTH_API)
```

**密码传输加密规范**（`api/utils/crypt.py::crypt()`）：
```
加密后密码 = base64(RSA(public_key, base64(原始密码)))
```
- 客户端使用 RSA 公钥（`conf/public.pem`）对 Base64 编码后的密码进行加密
- 加密结果再进行 Base64 编码，作为登录请求中 `password` 字段的值

**后端密码解密规范**（`api/utils/crypt.py::decrypt()`）：
```
base64_decode(加密后密码) → RSA_decrypt(private_key) → base64(原始密码)
```
- 后端使用 RSA 私钥（`conf/private.pem`）解密得到 Base64 编码值
- `decrypt()` 返回的是 `base64(原始密码)` 字符串，直接用于 `check_password_hash` 验证
- 完整流程：`base64_decode → RSA(private_key) → base64(password)` → 用于密码哈希校验

**登录请求密码加密规范**：
```
password 字段 = base64(RSA(public_key, base64(原始密码)))
```
- 客户端使用 RSA 公钥（`conf/public.pem`）加密 Base64 编码后的密码
- 后端使用 RSA 私钥（`conf/private.pem`）解密得到 Base64 编码值，用于密码哈希验证
- 后端解密完整流程：`base64_decode → RSA(private_key) → base64_decode → 原始密码 password`

### 1.4 RetCode 常量

| 名称 | 值 | 含义 |
|------|-----|------|
| SUCCESS | 0 | 成功 |
| ARGUMENT_ERROR | 101 | 参数错误 |
| OPERATING_ERROR | 103 | 操作失败 |
| AUTHENTICATION_ERROR | 109 | 认证失败 |
| UNAUTHORIZED | 401 | 未授权 |
| FORBIDDEN | 403 | 禁止访问 |
| SERVER_ERROR | 500 | 服务器错误 |

---

## 2. 登录认证全流程

### TC-AT-001: 正常密码登录成功

**前置条件**：
- GaussDB 中已注册用户 `testuser@example.com`，密码为 `Test@123`（Base64 编码后为 `VGVzdEAxMjM=`，已用 werkzeug 哈希存储）
- 用户 `is_active = "1"`、`status = "1"`

**步骤**：
1. 构造密码（客户端使用 RSA 公钥加密）：
   ```
   password = "Test@123"
   → base64("Test@123") = "VGVzdEAxMjM="
   → RSA(public_key, "VGVzdEAxMjM=")           # 使用 conf/public.pem 加密
   → base64(RSA(public_key, "VGVzdEAxMjM="))   # 外层再 Base64 编码
   = encrypted_password
   ```
2. 发送请求：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "testuser@example.com",
     "password": "<encrypted_password>"
   }
   ```
   - 后端解密流程：`base64_decode → RSA(private_key) → base64_decode → "Test@123"`
3. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 0,
     "message": "Welcome back!",
     "data": {
       "id": "<user_id>",
       "nickname": "testuser",
       "email": "testuser@example.com",
       ...
     }
   }
   ```
   响应头包含 `Authorization: <token>`
   - Authorization token 由 `user.get_id()` 生成，使用 itsdangerous `Serializer(secret_key).dumps(access_token)` 签名
   - 这不是 RSA 加密，而是 itsdangerous 签名的 UUID access_token
4. 数据库验证：
   ```sql
   SELECT access_token, last_login_time, update_date, update_time FROM "user"
   WHERE email = 'testuser@example.com';
   ```
   - `access_token` 应为新的 32 字符 hex UUID（与登录前不同）
   - `last_login_time` 应更新为当前时间
   - `update_date` 和 `update_time` 应同步更新

**预期结果**：
- 返回 `code: 0`
- `Authorization` 头中的 Token 是 itsdangerous 签名的 access_token UUID，可通过 `Serializer(secret_key).loads(token)` 还原出 access_token
- `data` 中不包含 `password` 和 `access_token` 字段（`to_safe_dict(for_self=True)` 过滤）
- Session 中 `_user_id`、`_fresh`、`_id` 已设置
- GaussDB `user` 表 `update_date`、`update_time` 字段同步刷新

---

### TC-AT-002: 未注册邮箱登录失败

**前置条件**：
- GaussDB 中不存在邮箱 `unregistered@example.com`

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "unregistered@example.com",
     "password": "<any_encrypted_password>"
   }
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 109,
     "message": "Email: unregistered@example.com is not registered!",
     "data": false
   }
   ```
3. 数据库验证：无需验证（查询返回空结果集）

**预期结果**：
- 返回 `code: 109`（AUTHENTICATION_ERROR）
- 不产生 Session、不返回 Authorization 头

---

### TC-AT-003: 密码错误登录失败

**前置条件**：
- GaussDB 中已注册用户 `testuser@example.com`，密码为 `Test@123`

**步骤**：
1. 构造错误密码：`Base64("WrongPassword")` → RSA 加密
2. 发送请求：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "testuser@example.com",
     "password": "<encrypted_wrong_password>"
   }
   ```
3. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 109,
     "message": "Email and password do not match!",
     "data": false
   }
   ```
4. 数据库验证：
   ```sql
   SELECT access_token, last_login_time FROM "user"
   WHERE email = 'testuser@example.com';
   ```
   - `access_token` 不应改变
   - `last_login_time` 不应改变

**预期结果**：
- 返回 `code: 109`
- `access_token` 保持不变，不影响已有 Session

---

### TC-AT-004: 空请求体登录失败

**前置条件**：无

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {}
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 109,
     "message": "Unauthorized!",
     "data": false
   }
   ```

**预期结果**：
- 返回 `code: 109`
- 不进入 RSA 解密流程

---

### TC-AT-005: 已禁用账户登录失败

**前置条件**：
- GaussDB 中已注册用户 `disabled@example.com`，`is_active = "0"`

**步骤**：
1. 构造正确密码并 RSA 加密
2. 发送请求：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "disabled@example.com",
     "password": "<correct_encrypted_password>"
   }
   ```
3. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 403,
     "message": "This account has been disabled...",
     "data": false
   }
   ```
4. 数据库验证：
   ```sql
   SELECT is_active, access_token FROM "user"
   WHERE email = 'disabled@example.com';
   ```
   - `is_active` 仍为 `"0"`
   - `access_token` 不应改变

**预期结果**：
- 返回 `code: 403`（FORBIDDEN）
- 即使密码正确也不允许登录

---

### TC-AT-006: RSA 解密失败

**前置条件**：无

**步骤**：
1. 发送请求（提供非法 Base64 字符串作为密码）：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "testuser@example.com",
     "password": "NOT_VALID_RSA_CIPHERTEXT!!!"
   }
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 500,
     "message": "Fail to crypt password"
   }
   ```

**预期结果**：
- RSA 解密失败时返回 `code: 500`
- 不影响任何数据库状态

---

### TC-AT-007: 登录成功后使用 Authorization Token 访问受保护资源

**前置条件**：
- 已通过 TC-AT-001 登录成功，获取到 `Authorization` 头中的 JWT Token

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <jwt_token>
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 0,
     "data": {
       "id": "<user_id>",
       "email": "testuser@example.com",
       ...
     }
   }
   ```
3. 数据库验证：`_load_user` 使用 itsdangerous `Serializer(secret_key).loads(token)` 还原出 access_token UUID，查询 `user` 表匹配 `access_token` 和 `status="1"`

**预期结果**：
- Token 正确解析并定位到用户
- 返回完整用户资料
- 验证 Token 解析链路：`Serializer(secret_key).loads(token) → access_token UUID` → 查询 user 表

---

## 3. 登出

### TC-AT-008: 正常登出

**前置条件**：
- 用户已登录，持有有效的 `Authorization` JWT Token
- 当前 `access_token` 为有效 32 字符 hex UUID

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/logout
   Authorization: Bearer <jwt_token>
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 0,
     "data": true
   }
   ```
3. 数据库验证：
   ```sql
   SELECT access_token FROM "user" WHERE email = 'testuser@example.com';
   ```
   - `access_token` 应变为 `INVALID_<32字符hex>` 格式（`INVALID_` + `secrets.token_hex(16)`）
4. 再次使用同一 JWT Token 访问受保护资源：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <same_jwt_token>
   ```
   预期响应：HTTP 401

**预期结果**：
- 数据库 `access_token` 被覆盖为 `INVALID_` 前缀
- Session 中 `_user_id`、`_fresh`、`_id` 被清除
- 旧 JWT Token 因 `access_token` 变更而失效（`UserService.query()` 拒绝 `INVALID_` 前缀）

---

### TC-AT-009: 未认证用户尝试登出

**前置条件**：
- 未携带任何认证信息

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/logout
   ```
2. 预期响应：HTTP 401，响应体：
   ```json
   {
     "code": 401,
     "message": "<unauthorized description>"
   }
   ```

**预期结果**：
- `@login_required` 装饰器拦截，返回 401

---

### TC-AT-010: 登出后 Session Cookie 失效

**前置条件**：
- 用户已通过登录获取 Session Cookie
- Session 中已设置 `_user_id`

**步骤**：
1. 使用 Session Cookie 发送登出请求：
   ```
   POST /api/v1/auth/logout
   Cookie: session=<session_cookie>
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 使用同一 Session Cookie 访问受保护资源：
   ```
   GET /api/v1/users/me
   Cookie: session=<session_cookie>
   ```
4. 预期响应：HTTP 401

**预期结果**：
- `logout_user()` 清除 Session 中的 `_user_id`、`_fresh`、`_id`
- 后续请求 Session 回退认证失败

---

## 4. OAuth 登录通道

### TC-AT-011: 获取登录通道列表

**前置条件**：
- `settings.OAUTH_CONFIG` 中配置本批次 `testoauth` 通道，指向本地 stub provider

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/auth/login/channels
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 0,
     "data": [
       {
        "channel": "testoauth",
        "display_name": "Test OAuth",
        "icon": "sso"
       }
     ]
   }
   ```

**预期结果**：
- 无需认证即可获取通道列表
- 返回所有配置的 OAuth 通道信息
- 本批次要求安装并启动本地 stub；返回空数组属于环境搭建失败，记录阻塞并修复，不能跳过后续 OAuth 用例

---

### TC-AT-012: OAuth 登录重定向（本地 stub）

**前置条件**：
- 已配置 `testoauth` 通道，client_id 为 `ragflow_test_client`

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/auth/login/testoauth
   ```
2. 预期响应：HTTP 302 重定向
   - 重定向 URL 指向本地 stub `/authorize`
   - URL 参数包含 `client_id=ragflow_test_client`、`response_type=code`、`state=<uuid>`
3. Session 验证：
   ```python
   session["oauth_state"]  # 应为生成的 UUID
   ```

**预期结果**：
- 正确构建 OAuth 授权 URL
- `state` 参数存入 Session 用于 CSRF 防护

---

### TC-AT-013: OAuth 回调 - 新用户自动注册

**前置条件**：
- 本地 OAuth stub 回调返回有效 `code`
- 用户信息中邮箱 `newoauth@example.com` 在 GaussDB 中不存在
- Session 中 `oauth_state` 匹配回调参数

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/auth/oauth/testoauth/callback?code=<auth_code>&state=<matching_state>
   ```
2. 预期响应：HTTP 302 重定向到 `/?auth=<signed_token>`
   - Token 格式：itsdangerous 签名的 access_token UUID（与登录返回的 Authorization header 格式相同）
   - 前端拿到 Token 后通过 `Serializer(secret_key).loads(token)` 解析获取 access_token
3. 数据库验证：
   ```sql
   SELECT id, email, login_channel, access_token FROM "user"
   WHERE email = 'newoauth@example.com';
   ```
   - 新用户已创建，`login_channel = "testoauth"`
   - `access_token` 为新的 32 字符 hex UUID
   ```sql
   SELECT * FROM tenant WHERE id = '<new_user_id>';
   ```
   - 对应 Tenant 记录已创建
   ```sql
   SELECT * FROM user_tenant WHERE user_id = '<new_user_id>';
   ```
   - 对应 UserTenant 记录已创建，`role = "owner"`

**预期结果**：
- 自动完成用户注册（User + Tenant + UserTenant + root File）
- 重定向 URL 中 `auth` 参数为 itsdangerous 签名的 access_token UUID
- 前端可通过 `Serializer(secret_key).loads(token)` 解析获取 access_token 完成登录

---

### TC-AT-014: OAuth 回调 - State 不匹配（CSRF 防护）

**前置条件**：
- Session 中 `oauth_state = "valid_state_uuid"`
- 回调参数中 `state = "invalid_state"`

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/auth/oauth/testoauth/callback?code=<auth_code>&state=invalid_state
   ```
2. 预期响应：HTTP 302 重定向到 `/?error=<csrf_error>`

**预期结果**：
- State 参数不匹配，拒绝处理回调
- 不创建用户、不交换 Token

---

### TC-AT-015: OAuth 回调 - 已存在用户直接登录

**前置条件**：
- GaussDB 中已存在用户 `existing@example.com`，`login_channel = "github"`
- 本地 stub 返回该用户的邮箱

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/auth/oauth/testoauth/callback?code=<auth_code>&state=<matching_state>
   ```
2. 预期响应：HTTP 302 重定向到 `/?auth=<signed_token>`
   - Token 格式：`URLSafeTimedSerializer(secret_key).dumps(access_token)`，与密码 RSA 无关
3. 数据库验证：
   ```sql
   SELECT access_token, last_login_time FROM "user"
   WHERE email = 'existing@example.com';
   ```
   - `access_token` 已更新
   - 当前代码只更新 `access_token` 并 `save()`；`last_login_time` 不会在已有 OAuth 用户 callback 中刷新。若产品契约要求刷新，作为两组共有问题记录

**预期结果**：
- 不创建新用户，直接登录已有用户
- 更新 `access_token`；记录 `last_login_time` 未刷新的当前行为
- 重定向 Token 可通过 `Serializer(secret_key).loads(token)` 解析获取 access_token

---

## 5. 密码忘记和重置

### TC-AT-016: 获取验证码图片（Captcha）

**前置条件**：
- GaussDB 中已注册邮箱 `reset@example.com`

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/password/forgot/captcha?email=reset@example.com
   ```
2. 预期响应：HTTP 200，Content-Type 为 `image/jpeg`
3. Redis 只读验证并取得后续 API 所需 captcha：
   ```
   GET captcha:reset@example.com
   ```
   - 应存在 4 字符验证码字符串，TTL ≤ 60 秒

**预期结果**：
- 生成图片验证码并存入 Redis
- 验证码 TTL 为 60 秒

---

### TC-AT-017: 发送 OTP 邮件

**前置条件**：
- 已通过 TC-AT-016 获取验证码，并从该组 Redis 做只读校验取得真实值
- 本地 SMTPS 捕获服务已运行，RAGFlow 信任测试 CA 且能完成 SMTP auth

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/password/forgot/otp
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "captcha": "<captcha_from_readonly_redis_check>"
   }
   ```
2. 预期响应：HTTP 200，`code: 0`
3. Redis 验证：
   ```
   GET otp:reset@example.com        # "hash_hex:salt_hex" 格式，TTL ≤ 300 秒
   GET otp_last_sent:reset@example.com  # Unix 时间戳
   ```
4. 从本地 SMTPS 捕获的真实邮件正文解析 4 位 OTP，供 TC-AT-019/020 使用；不得直接写 Redis OTP

**预期结果**：
- 验证码正确（不区分大小写），生成 4 位大写字母 OTP
- OTP 以 HMAC-SHA256 哈希存入 Redis（`otp:{email}` = `hash:salt`）
- 设置 60 秒重发冷却（`otp_last_sent:{email}`）
- TTL 为 300 秒（5 分钟）

---

### TC-AT-018: OTP 重发冷却限制

**前置条件**：
- 已在 60 秒内通过 TC-AT-017 成功发送过一次 OTP；不直接修改 `otp_last_sent`

**步骤**：
1. 再次调用 captcha API，读取新 captcha 后立即发送第二次 OTP 请求：
   ```
   POST /api/v1/auth/password/forgot/otp
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "captcha": "<valid_captcha>"
   }
   ```
2. 预期响应：HTTP 200，返回错误码提示需等待

**预期结果**：
- 60 秒冷却期内拒绝重发
- `RESEND_COOLDOWN_SECONDS = 60`

---

### TC-AT-019: OTP 验证成功

**前置条件**：
- 已发送 OTP，Redis 中存有 OTP 哈希
- 实际 OTP 为 `WXYZ`

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/password/forgot/otp/verify
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "otp": "WXYZ"
   }
   ```
2. 预期响应：HTTP 200，`code: 0`
3. Redis 验证：
   ```
   GET otp:verified:reset@example.com  # "1"，TTL ≤ 300 秒
   EXISTS otp:reset@example.com        # 应已删除
   ```

**预期结果**：
- OTP 验证通过后设置 `otp:verified:{email}` 标志，TTL 300 秒
- 清除原始 OTP 数据

---

### TC-AT-020: OTP 验证失败 - 超过尝试次数锁定

**前置条件**：
- 已发送 OTP
- 已尝试 4 次错误 OTP

**步骤**：
1. 第 5 次发送错误 OTP：
   ```
   POST /api/v1/auth/password/forgot/otp/verify
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "otp": "AAAA"
   }
   ```
2. 预期响应：HTTP 200，返回错误
3. Redis 验证：
   ```
   GET otp_attempts:reset@example.com  # 5
   EXISTS otp_lock:reset@example.com   # 存在，TTL ≤ 1800 秒
   ```
4. 第 6 次尝试（即使正确 OTP）：
   ```
   POST /api/v1/auth/password/forgot/otp/verify
   {
     "email": "reset@example.com",
     "otp": "<correct_otp>"
   }
   ```
   预期响应：返回锁定错误

**预期结果**：
- 5 次失败后锁定 30 分钟（`ATTEMPT_LIMIT = 5`，`ATTEMPT_LOCK_SECONDS = 1800`）
- 锁定期间即使 OTP 正确也拒绝

---

### TC-AT-021: 密码重置成功

**前置条件**：
- OTP 验证通过，Redis 中 `otp:verified:reset@example.com = "1"`
- 新密码为 `NewPass@456`

**步骤**：
1. 构造新密码（使用 客户端使用 RSA 公钥加密）：
   ```
   new_password = "NewPass@456"
   → base64("NewPass@456") = "TmV3UGFzc0A0NTY="
   → RSA(public_key, "TmV3UGFzc0A0NTY=")              # 使用 conf/public.pem 加密
   → base64(RSA(public_key, "TmV3UGFzc0A0NTY="))       # 外层再 Base64 编码
   = encrypted_new_password
   ```
2. 发送请求（请求体包含 Base64 编码后的加密密码）：
   ```
   POST /api/v1/auth/password/reset
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "new_password": "<base64(RSA(public_key, base64('NewPass@456')))>",
     "confirm_new_password": "<base64(RSA(public_key, base64('NewPass@456')))>"
   }
   ```
   - `new_password` 和 `confirm_new_password` 均为 `base64(RSA(public_key, base64(密码)))` 格式
   - 后端解密流程：`base64_decode → RSA(private_key) → base64_decode → "NewPass@456"`
3. 预期响应：HTTP 200，`code: 0`
4. 数据库验证：
   ```sql
   SELECT password FROM "user" WHERE email = 'reset@example.com';
   ```
   - 密码哈希已更新，新哈希可通过 `check_password_hash(hash, "TmV3UGFzc0A0NTY=")` 验证
   - 存储的是 Base64 编码后密码的哈希，即 `generate_password_hash("TmV3UGFzc0A0NTY=")`
   - 注意：`check_password_hash(hash, "NewPass@456")` 返回 False（原始密码不参与哈希验证）
5. Redis 验证：
   ```
   EXISTS otp:verified:reset@example.com  # 应已删除
   ```
6. 响应头包含 `Authorization` Token
   - Token 格式：`URLSafeTimedSerializer(secret_key).dumps(user.access_token)`，与密码 RSA 无关
   - 当前 reset 路由不调用 `login_user()`、也不刷新 `access_token`；因此这里验证返回 token 可用，但不能把它描述为新生成的 access_token 或已建立 Session

**预期结果**：
- 密码更新为 `generate_password_hash("TmV3UGFzc0A0NTY=")`（Base64 编码密码的哈希）
- 清除 `otp:verified` 标志
- 重置后返回可用 Authorization Token；当前实现不建立 Session，也不刷新 access_token，按实际记录
- 密码加密链路验证：`base64("NewPass@456")` → RSA 公钥加密 → Base64 编码 → 传输 → Base64 解码 → RSA 私钥解密 → `"TmV3UGFzc0A0NTY="` → `check_password_hash` 验证

---

### TC-AT-022: 密码重置 - 两次密码不一致

**前置条件**：
- OTP 验证通过
- 需要通过真实 `/api/v1/auth/password/forgot/otp/verify` 流程在 API 服务使用的 Redis 中写入 `otp:verified:<email>`；否则接口会先返回 `email not verified`，不会进入两次密码比对分支。

**步骤**：
1. 发送请求（两个不同密码的加密值）：
   ```
   POST /api/v1/auth/password/reset
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "new_password": "<encrypted_password_A>",
     "confirm_new_password": "<encrypted_password_B>"
   }
   ```
2. 预期响应：HTTP 200，返回错误码提示密码不一致

**预期结果**：
- 两次密码不匹配时拒绝重置
- 密码不被修改

---

### TC-AT-023: 密码重置 - 未通过 OTP 验证

**前置条件**：
- Redis 中不存在 `otp:verified:reset@example.com`

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/auth/password/reset
   Content-Type: application/json
   {
     "email": "reset@example.com",
     "new_password": "<encrypted_password>",
     "confirm_new_password": "<encrypted_password>"
   }
   ```
2. 预期响应：HTTP 200，返回错误码提示需先验证 OTP

**预期结果**：
- 无 OTP 验证标志时拒绝重置

---

## 6. API Token 管理

### TC-AT-024: 创建 API Token

**前置条件**：
- 用户已登录，持有有效 JWT Token
- 用户对应 `tenant_id`

**步骤**：
1. 发送请求：
   ```
   POST /api/v1/system/tokens
   Authorization: Bearer <jwt_token>
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   ```sql
   SELECT tenant_id, token, beta FROM api_token
   WHERE tenant_id = '<user_tenant_id>'
   ORDER BY create_time DESC LIMIT 1;
   ```
   - `token` 格式为 `ragflow-<token_urlsafe(32)>`（总长约 51 字符）
   - `beta` 为 32 字符字符串（`ragflow-` 前缀去除后截断）

**预期结果**：
- 生成 `token` 和 `beta` 两个值
- `token` 以 `ragflow-` 开头
- 记录关联到正确的 `tenant_id`

---

### TC-AT-025: 列出 API Token

**前置条件**：
- 已创建至少 2 个 API Token

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/system/tokens
   Authorization: Bearer <jwt_token>
   ```
2. 预期响应：HTTP 200，响应体：
   ```json
   {
     "code": 0,
     "data": [
       {
         "tenant_id": "<tenant_id>",
         "token": "ragflow-xxxxx...",
         "beta": "xxxxx...",
         "create_time": ...
       },
       ...
     ]
   }
   ```
3. 数据库验证：
   ```sql
   SELECT COUNT(*) FROM api_token
   WHERE tenant_id = '<user_tenant_id>';
   ```
   - 返回数量与响应中 Token 数量一致

**预期结果**：
- 返回当前租户下所有 API Token
- 包含 `token`、`beta`、`tenant_id` 等字段

---

### TC-AT-026: 删除 API Token

**前置条件**：
- 存在 API Token `ragflow-abc123...`

**步骤**：
1. 发送请求：
   ```
   DELETE /api/v1/system/tokens/ragflow-abc123...
   Authorization: Bearer <jwt_token>
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   ```sql
   SELECT * FROM api_token WHERE token = 'ragflow-abc123...';
   ```
   - 结果应为空

**预期结果**：
- Token 记录从 `api_token` 表中删除
- 删除后该 Token 无法用于认证

---

### TC-AT-027: 使用 API Token（AUTH_API）访问受保护资源

**前置条件**：
- 已创建 API Token `ragflow-xxxxx...`

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/users/me
   Authorization: Bearer ragflow-xxxxx...
   ```
2. 预期响应：HTTP 200，返回用户资料
3. 认证流程验证：
   - `_load_user` 首先尝试 JWT 解码（失败，因为 `ragflow-` 不是有效 JWT）
   - 然后尝试 `APIToken.query(token="ragflow-xxxxx...")`，成功
   - 通过 `tenant_id` 定位用户

**预期结果**：
- API Token 认证成功
- `g.auth_type` 设置为 `AUTH_API`

---

### TC-AT-028: 使用 Beta Token（AUTH_BETA）访问 Bot API

**前置条件**：
- 已创建 API Token，获取 `beta` 值
- 通过主 API 创建一个属于该 tenant 的 Chat，取得 `<dialog_id>`；真实 Beta-only 路由为 `/api/v1/chatbots/<dialog_id>/info`

**步骤**：
1. 发送请求（访问 Bot API 端点）：
   ```
   GET /api/v1/chatbots/<dialog_id>/info
   Authorization: Bearer <beta_value>
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 认证流程验证：
   - `_load_user` 尝试 `APIToken.query(beta=<beta_value>)`，成功
   - 通过 `tenant_id` 定位用户

**预期结果**：
- Beta Token 认证成功
- `g.auth_type` 设置为 `AUTH_BETA`

---

### TC-AT-029: 使用 Beta Token 访问非 Bot API 失败

**前置条件**：
- 持有有效 `beta` 值

**步骤**：
1. 发送请求（访问需要 `DEFAULT_AUTH_TYPES` 的端点）：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <beta_value>
   ```
2. 预期响应：HTTP 401

**预期结果**：
- `beta` Token 不在 `DEFAULT_AUTH_TYPES (AUTH_JWT, AUTH_API)` 中
- 认证失败，返回 401

---

## 7. Token 过期和刷新

### TC-AT-030: JWT Token 时效性验证

**前置条件**：
- 用户已登录，获取到 JWT Token
- `itsdangerous.URLSafeTimedSerializer` 生成的 Token 包含时间戳

**步骤**：
1. 使用新获取的 JWT Token 访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <fresh_jwt_token>
   ```
   预期：HTTP 200
2. （模拟）等待 Token 过期后使用同一 Token：
   - 注意：RAGFlow 当前未在 `_load_user` 中显式设置 `max_age` 参数，JWT Token 本身不自动过期
   - Token 失效依赖于 `access_token` 字段被修改（如登出、重新登录）
   - 验证旧 Authorization Token 时必须使用无 Session Cookie 的请求客户端；复用已登录客户端会触发 `_load_user_from_session()` fallback，不能证明旧 Authorization Token 仍有效

**预期结果**：
- JWT Token 的有效性取决于 `user.access_token` 字段是否匹配
- 重新登录会生成新的 `access_token`，旧 JWT Token 因解码后无法匹配而失效

---

### TC-AT-031: 重新登录后旧 Token 失效

**前置条件**：
- 用户登录获取 `jwt_token_v1`（对应 `access_token_v1`）

**步骤**：
1. 再次登录（使用相同邮箱密码）：
   ```
   POST /api/v1/auth/login
   { "email": "testuser@example.com", "password": "<encrypted>" }
   ```
   获取 `jwt_token_v2`（对应新的 `access_token_v2`）
2. 数据库验证：
   ```sql
   SELECT access_token FROM "user" WHERE email = 'testuser@example.com';
   ```
   - `access_token` 已变为 `access_token_v2`
3. 使用旧 Token 访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <jwt_token_v1>
   ```
   - 该请求必须不携带第一次登录产生的 Session Cookie，只携带旧 `Authorization` header
4. 预期响应：HTTP 401

**预期结果**：
- 每次登录生成新的 `access_token`，覆盖旧值
- 旧 JWT Token 解码出的 `access_token_v1` 在数据库中找不到匹配记录
- 旧 Token 自动失效

---

### TC-AT-032: 删除 API Token 后立即失效

**前置条件**：
- 已创建 API Token `ragflow-tobedeleted...`

**步骤**：
1. 使用 API Token 访问，确认有效：
   ```
   GET /api/v1/users/me
   Authorization: Bearer ragflow-tobedeleted...
   ```
   预期：HTTP 200
2. 删除该 Token：
   ```
   DELETE /api/v1/system/tokens/ragflow-tobedeleted...
   Authorization: Bearer <jwt_token>
   ```
   预期：HTTP 200
3. 再次使用已删除的 API Token：
   ```
   GET /api/v1/users/me
   Authorization: Bearer ragflow-tobedeleted...
   ```
4. 预期响应：HTTP 401

**预期结果**：
- API Token 删除后立即无法用于认证

---

## 8. Session 认证

### TC-AT-033: Session Cookie 认证成功

**前置条件**：
- 用户通过登录设置了 Session（`_user_id`、`_fresh`、`_id`）
- Session 后端为 Redis

**步骤**：
1. 使用 Session Cookie（不携带 Authorization 头）访问：
   ```
   GET /api/v1/users/me
   Cookie: session=<valid_session_cookie>
   ```
2. 预期响应：HTTP 200，返回用户资料
3. 认证流程验证：
   - 无 Authorization 头，进入 `_load_user_from_session()`
   - 读取 `session["_user_id"]`，查询用户
   - 验证 `access_token` 非空、长度 ≥ 32、不以 `INVALID_` 开头

**预期结果**：
- Session 回退认证成功
- `g.auth_type` 设置为 `AUTH_JWT`

---

### TC-AT-034: Session 过期后访问失败

**前置条件**：
- Session 配置 `SESSION_PERMANENT = False`（浏览器关闭即过期）
- 当前主 API 没有初始化 Flask-Session/Quart-Session 扩展；虽然配置中存在
  `SESSION_TYPE="redis"`，实际使用 Quart 默认签名客户端 cookie，不存在可删除的
  Redis session key

**步骤**：
1. 登录取得 Session Cookie，仅记录 cookie 指纹和长度，不落完整值
2. 模拟浏览器关闭：使用全新客户端、不携带原 Session Cookie 访问：
   ```
   GET /api/v1/users/me
   ```
3. 预期响应：HTTP 401
4. 另复制该测试 cookie 并篡改一个字符，使用篡改后的 cookie 访问，同样预期
   HTTP 401；不得修改服务端 secret 或其它用户状态

**预期结果**：
- 非永久客户端 cookie 在浏览器会话结束后不再发送，回退认证失败
- 签名被篡改的 cookie 不能解析，回退认证失败

---

### TC-AT-035: Session 中 access_token 被标记 INVALID 后访问失败

**前置条件**：
- 用户已通过 Session 登录
- 用户通过其他渠道（如 API 调用）执行了登出，`access_token` 变为 `INVALID_xxx`

**步骤**：
1. 使用原有 Session Cookie 访问：
   ```
   GET /api/v1/users/me
   Cookie: session=<session_cookie>
   ```
2. 预期响应：HTTP 401
3. 认证流程验证：
   - `_load_user_from_session()` 读取 `_user_id`，查询用户
   - 检测到 `access_token` 以 `INVALID_` 开头，拒绝认证

**预期结果**：
- 即使 Session 中 `_user_id` 存在，`access_token` 被标记为 `INVALID_` 后 Session 认证也失败

---

## 9. 并发登录

### TC-AT-036: 同一账号并发登录 - Token 覆盖

**前置条件**：
- 用户 `concurrent@example.com` 已登录，`access_token = token_A`

**步骤**：
1. 在另一个客户端再次登录：
   ```
   POST /api/v1/auth/login
   { "email": "concurrent@example.com", "password": "<encrypted>" }
   ```
   获取新 `access_token = token_B`
2. 数据库验证：
   ```sql
   SELECT access_token FROM "user" WHERE email = 'concurrent@example.com';
   ```
   - `access_token` 为 `token_B`
3. 使用第一个客户端的 JWT Token（对应 `token_A`）访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <jwt_with_token_A>
   ```
   - 该请求必须使用无 Session Cookie 的客户端，仅携带旧 `Authorization` header；否则服务端 Session fallback 可让请求通过
4. 预期响应：HTTP 401
5. 使用第二个客户端的 JWT Token（对应 `token_B`）访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <jwt_with_token_B>
   ```
6. 预期响应：HTTP 200

**预期结果**：
- 每次登录覆盖 `access_token`，导致之前所有 JWT Token 失效
- 同一账号同一时刻只有一个有效的 `access_token`
- API Token（`api_token` 表）不受影响，因为独立于 `access_token`

---

### TC-AT-037: 并发 API Token 使用不互相干扰

**前置条件**：
- 已创建 3 个 API Token：`token_1`、`token_2`、`token_3`

**步骤**：
1. 同时使用 3 个 API Token 分别访问：
   ```
   GET /api/v1/users/me  Authorization: Bearer ragflow-<token_1>
   GET /api/v1/users/me  Authorization: Bearer ragflow-<token_2>
   GET /api/v1/users/me  Authorization: Bearer ragflow-<token_3>
   ```
2. 预期响应：全部 HTTP 200
3. 删除 `token_2`：
   ```
   DELETE /api/v1/system/tokens/ragflow-<token_2>
   ```
4. 再次使用 `token_1` 和 `token_3`：预期 HTTP 200
5. 再次使用 `token_2`：预期 HTTP 401

**预期结果**：
- 多个 API Token 独立有效，互不影响
- 删除一个不影响其他

---

## 10. 无效 Token 处理

### TC-AT-038: 完全无效的 Token 字符串

**前置条件**：无

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/users/me
   Authorization: Bearer not_a_valid_token_at_all
   ```
2. 预期响应：HTTP 401
3. 认证流程验证：
   - JWT 解码失败（不是有效 itsdangerous 签名）
   - `APIToken.query(token="not_a_valid_token_at_all")` 无结果
   - Session 回退无 `_user_id`
   - 所有认证方式均失败

**预期结果**：
- 返回 401 Unauthorized

---

### TC-AT-039: Authorization 头 Bearer 前缀兼容性

**前置条件**：
- 持有有效 Token

**步骤**：
1. 发送请求（正确携带 Bearer 前缀）：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <token>
   ```
2. 预期响应：HTTP 200
3. 发送请求（不带 Bearer 前缀）：
   ```
   GET /api/v1/users/me
   Authorization: <token>
   ```
4. 预期响应：验证是否仍能认证（`_load_user` 会剥离 "Bearer " 前缀，无 "Bearer " 时直接使用整个值）

**预期结果**：
- `_load_user` 中先检测并剥离 "Bearer " 前缀（不区分大小写），无 "Bearer " 前缀时直接使用整个 Authorization 值
- 两种格式均应能成功认证

---

### TC-AT-040: 空 Authorization 头

**前置条件**：无

**步骤**：
1. 发送请求：
   ```
   GET /api/v1/users/me
   Authorization:
   ```
2. 预期响应：HTTP 401
3. 认证流程验证：
   - 空 Authorization 头，进入 Session 回退
   - 无有效 Session，认证失败

**预期结果**：
- 空 Token 不触发任何认证方式
- 回退到 Session 认证，无 Session 则 401

---

### TC-AT-041: 已登出的 Token 重放（需重新输入密码认证）

**前置条件**：
- 用户已登录获取 `token_v1`
- 用户已登出（`access_token` 变为 `INVALID_xxx`）

**步骤**：
1. 使用登出前的 Token 访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <token_before_logout>
   ```
2. 预期响应：HTTP 401（Token 已失效）
3. 尝试通过密码重新认证：
   ```
   POST /api/v1/auth/login
   Content-Type: application/json
   {
     "email": "testuser@example.com",
     "password": "<base64(RSA(public_key, base64('Test@123')))>"
   }
   ```
4. 预期响应：HTTP 200，返回新的 `Authorization` Token
   - Token 格式：`URLSafeTimedSerializer(secret_key).dumps(new_access_token)`
5. 使用新 Token 访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <new_token>
   ```
6. 预期响应：HTTP 200

**预期结果**：
- 登出后旧 Token 不可重放
- 必须重新提供密码进行认证才能获取新 Token
- 密码认证流程：`base64(password)` → RSA 公钥加密 → 传输 → RSA 私钥解密 → 验证密码哈希

---

### TC-AT-042: 被篡改的 JWT Token

**前置条件**：
- 持有有效 JWT Token

**步骤**：
1. 修改 JWT Token 中间部分字符
2. 发送请求：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <tampered_jwt_token>
   ```
3. 预期响应：HTTP 401
4. 认证流程验证：
   - itsdangerous 签名验证失败（`BadSignature` 异常）
   - 进入下一认证方式，均失败

**预期结果**：
- itsdangerous 签名机制防止 Token 篡改
- 返回 401

---

### TC-AT-043: 使用其他用户的 API Token

**前置条件**：
- 用户 A 创建了 API Token `ragflow-userA-token`
- 用户 B 持有自己的 JWT Token

**步骤**：
1. 用户 B 使用用户 A 的 API Token 访问：
   ```
   GET /api/v1/users/me
   Authorization: Bearer ragflow-userA-token
   ```
2. 预期响应：HTTP 200
3. 响应验证：
   - 返回的是用户 A 的资料（因为 API Token 通过 `tenant_id` 定位用户）

**预期结果**：
- API Token 绑定的是 `tenant_id`，不区分使用者
- 持有他人 API Token 即可访问对应租户资源（这是 API Token 的设计意图，Token 需妥善保管）

---

## 11. 密码加密验证

### TC-AT-044: 密码加密链路完整性验证

**前置条件**：
- 准备 `conf/public.pem`（公钥）和 `conf/private.pem`（私钥，passphrase: `"Welcome"`）

**加密链路规范**：
```
登录密码加密链路（请求体 password 字段）：
  原始密码 password
    → base64(password)
    → RSA(public_key, base64(password))           # 客户端使用 RSA 公钥加密（conf/public.pem）
    → base64(RSA(public_key, base64(password)))   # 外层再 Base64 编码

Authorization Token 生成（服务端，与 RSA 无关）：
  access_token = uuid4().hex                       # 32 字符 hex UUID
  Authorization = itsdangerous.Serializer(secret_key).dumps(access_token)
  # Token 是 itsdangerous 签名的 access_token，不包含密码明文

解密验证：
  登录密码解密（服务端）：base64_decode → RSA(private_key) → base64(password)
  Authorization Token 解析（服务端）：itsdangerous.loads(token) → access_token UUID

密码存储验证：
  后端解密请求中的 password 字段：base64_decode → RSA(private_key) → base64(password)
  验证时使用 check_password_hash(stored_hash, base64(password))
  注意：哈希的对象是 base64(password) 而非原始明文 password
```

**步骤**：
1. 模拟登录请求密码加密（客户端使用 RSA 公钥）：
   ```python
   from api.utils.crypt import crypt
   password = "Test@123"
   # crypt() 内部执行: base64(RSA(public_key, base64(password)))
   encrypted_password = crypt(password)
   ```
2. 模拟后端解密（服务端使用 RSA 私钥解密）：
   ```python
   from api.utils.crypt import decrypt
   # decrypt() 内部执行: base64_decode → RSA(private_key) → 得到 base64(password)
   decrypted = decrypt(encrypted_password)
   # decrypted == "VGVzdEAxMjM="  (即 base64("Test@123"))
   ```
3. 验证解密结果为 Base64 编码值（非原始明文）：
   ```python
   import base64
   assert decrypted == base64.b64encode(b"Test@123").decode()
   # decrypted == "VGVzdEAxMjM="
   assert decrypted != "Test@123"  # 不等于原始明文
   ```
4. 验证密码哈希匹配（后端验证逻辑）：
   ```python
   from werkzeug.security import generate_password_hash, check_password_hash
   # 存储时：对 base64(password) 生成哈希
   hash_value = generate_password_hash("VGVzdEAxMjM=")
   # 验证时：使用 base64(password) 进行匹配
   assert check_password_hash(hash_value, "VGVzdEAxMjM=") == True
   # 原始明文无法匹配
   assert check_password_hash(hash_value, "Test@123") == False
   ```
5. 验证 Authorization Token 生成与解析（与 RSA 无关，使用项目实际的 `URLSafeTimedSerializer`）：
   ```python
   from itsdangerous.url_safe import URLSafeTimedSerializer
   # Token 生成（服务端使用 secret_key 签名 access_token UUID）
   access_token = "a" * 32
   secret_key = "ragflow-secret-key"  # 实际来自 common/settings.get_secret_key()
   serializer = URLSafeTimedSerializer(secret_key=secret_key)
   token = serializer.dumps(access_token)
   
   # Token 解析（服务端 _load_user 使用相同 secret_key 验签）
   recovered_access_token = serializer.loads(token)
   assert recovered_access_token == access_token
   # 篡改后的 token 验签失败
   ```

**预期结果**：
- 登录加密链路：`password → base64(password) → RSA(public_key, base64(password)) → base64(...)`
- 登录解密链路：`base64_decode → RSA(private_key) → base64(password)`（得到的是 Base64 值，非原始密码）
- Authorization Token 是 itsdangerous 签名的 access_token UUID，与 RSA 加解密无关
- 密码哈希验证使用 `base64(password)` 而非原始明文
- `check_password_hash(stored_hash, "Test@123")` 一定返回 False（因为哈希对象是 `base64(password)`）

---

### TC-AT-045: 特殊字符密码加密验证

**前置条件**：无

**步骤**：
1. 使用包含特殊字符的密码 `P@$$w0rd!#%^&*()`
2. 前端加密：
   ```python
   plaintext = "P@$$w0rd!#%^&*()"
   b64 = base64.b64encode(plaintext.encode()).decode()
   # b64 = "UEAkJHcwcmQhIyVeJiooKQ=="
   encrypted = crypt(plaintext)
   ```
3. 后端解密并验证：
   ```python
   decrypted = decrypt(encrypted)
   assert decrypted == "UEAkJHcwcmQhIyVeJiooKQ=="
   ```
4. 确认注册/登录时密码哈希基于 Base64 值：
   ```sql
   SELECT password FROM "user" WHERE email = '<test_user>';
   ```
   - `check_password_hash(hash, "UEAkJHcwcmQhIyVeJiooKQ==")` 应为 True

**预期结果**：
- 特殊字符在 Base64 编码后安全传输
- RSA 加密/解密正确处理各种字符集

---

### TC-AT-046: 密码哈希算法与字段长度验证

**前置条件**：
- 数据库中已存储密码哈希

**步骤**：
1. 从 GaussDB 读取密码哈希：
   ```sql
   SELECT password FROM "user" WHERE email = 'testuser@example.com';
   ```
2. 验证哈希格式：
   - 以本批次运行时调用 `generate_password_hash()` 的探针结果为准；不得把预先记录的 Werkzeug 前缀当作本次证据
   - 不得固定断言旧版 Werkzeug 的 `pbkdf2:sha256`
3. 验证哈希不可逆：
   ```python
   from werkzeug.security import check_password_hash
   # 只能通过 check_password_hash 验证，无法从哈希反推密码
   assert check_password_hash(stored_hash, base64_password) == True
   ```

**预期结果**：
- 密码哈希使用当前 Werkzeug 安全默认算法（本环境为 scrypt）
- 每次生成的哈希不同（随机 salt），但同一密码验证通过
- GaussDB 的 CharField(255) 足够存储完整哈希字符串

---

### TC-AT-047: UserService.query 安全过滤验证

**前置条件**：
- 用户已登出，`access_token = "INVALID_abc123..."`

**步骤**：
1. 验证 `UserService.query()` 的安全过滤逻辑：
   ```python
   # 空值过滤
   result = UserService.query(access_token="")         # 返回空
   result = UserService.query(access_token=None)       # 返回空
   result = UserService.query(access_token="   ")      # 返回空（whitespace）

   # 长度过滤
   result = UserService.query(access_token="short")    # 返回空（< 32 字符）

   # INVALID_ 前缀过滤
   result = UserService.query(access_token="INVALID_abc123def456...")  # 返回空

   # 正常值
   result = UserService.query(access_token="a" * 32)   # 正常查询
   ```
2. 数据库验证：
   ```sql
   -- 确认安全过滤不会泄露用户信息
   SELECT * FROM "user" WHERE access_token LIKE 'INVALID_%';
   ```
   - 存在记录但 `UserService.query()` 不会返回

**预期结果**：
- `UserService.query()` 在查询层面过滤无效 Token
- 空值、短值、`INVALID_` 前缀均返回空结果集
- 防止通过异常 Token 值绕过认证

---

## 12. 边界场景和异常处理

### TC-AT-048: 注册功能关闭时的行为

**前置条件**：
- `REGISTER_ENABLED` 在 `settings.init_settings()` 中于进程启动时读取。本用例分别为两组启动一次性隔离 API 进程（独立端口、独立 session/日志，复用该组专用测试数据库），进程环境设置 `REGISTER_ENABLED=0`；不得在常驻主进程运行中修改环境变量后假定配置已热更新

**步骤**：
1. 先请求 `GET /api/v1/system/config`，确认响应中的 `registerEnabled` 为 `0`
2. 发送密码注册请求：
   ```
   POST /api/v1/users
   Content-Type: application/json
   {
     "nickname": "NewUser",
     "email": "new@example.com",
     "password": "<encrypted_password>"
   }
   ```
3. 预期响应：HTTP 200，`code: 103`（OPERATING_ERROR）；数据库只读确认该邮箱没有 User/Tenant/UserTenant/File 残留
4. 在同一隔离进程中走本地 OAuth stub 新用户回调。当前 OAuth 注册路径不检查 `REGISTER_ENABLED`，会创建并登录新用户；将此双组共有行为记录为注册开关绕过缺陷，随后使用 Admin API 删除该 fixture
5. 停止一次性隔离进程，确认两组常驻主服务仍正常

**预期结果**：
- 注册功能关闭时拒绝新用户注册
- `/system/config` 与密码注册端点行为一致
- OAuth 自动注册同样应受注册开关约束；当前实现若仍成功，必须记录安全/配置一致性缺陷，不能把它当成可接受行为

---

### TC-AT-049: active_required 装饰器 - 非活跃用户访问

**前置条件**：
- 用户已登录但 `is_active = "0"`（登录后被管理员禁用）
- 持有有效 JWT Token

**步骤**：
1. 当前生产路由中没有使用 `@active_required` 的端点，不能保留 `<protected_endpoint>` 占位符。测试脚本在隔离的 Quart 测试进程中把当前 `login_required` + `active_required` 组合注册到仅测试用的 `/__test__/active-required`，不修改产品代码；先用 API 登录，再由 Admin API 停用用户，最后请求：
   ```
   GET /__test__/active-required
   Authorization: Bearer <jwt_token>
   ```
2. 预期响应：HTTP 200，`code: 403`（FORBIDDEN）

**预期结果**：
- `active_required` 检查 `is_active == "1"`
- 非活跃用户被拒绝，即使 Token 有效

---

### TC-AT-050: GaussDB 连接中断时的认证行为

**前置条件**：
- 两组 metadata 连接均通过本批次可控 TCP fault proxy；对照组代理到 MySQL，实验组代理到 GaussDB。切断代理不得停止或修改远端 GaussDB 实例

**步骤**：
1. 发送登录请求：
   ```
   POST /api/v1/auth/login
   { "email": "testuser@example.com", "password": "<encrypted>" }
   ```
2. 协议预期 HTTP 500（数据库连接异常）；若当前全局异常处理返回 HTTP 200、`code=100`，按错误状态码缺陷记录
3. 发送需要认证的请求：
   ```
   GET /api/v1/users/me
   Authorization: Bearer <jwt_token>
   ```
4. 登录和认证都必须在有限重试后明确失败，不能静默成功；记录 HTTP 状态、业务码、重试次数和耗时。恢复 proxy 后必须重新成功登录

**预期结果**：
- GaussDB 连接中断时，所有依赖数据库的认证操作失败
- GaussDB 元数据库路径应走 `RetryingPooledGaussDBDatabase` / `PsycopgRetryMixin` 对应的连接池和重试逻辑，不能误判为 MySQL 连接池行为
- 返回 500 错误而非静默失败

---

## 13. GaussDB 特有验证

### TC-AT-051: GaussDB CharField 空字符串处理

**前置条件**：
- GaussDB 对空字符串的处理可能与 MySQL 不同

**步骤**：
1. 本用例明确测试数据库空字符串存储语义，允许在专用 fixture 上直接更新。先在对照组、后在实验组验证 `user.access_token`：
   ```sql
   -- GaussDB 中空字符串可能被存储为 NULL
   UPDATE "user" SET access_token = '' WHERE email = 'test@example.com';
   SELECT access_token FROM "user" WHERE email = 'test@example.com';
   ```
2. 验证 `UserService.query(access_token="")` 的行为：
   - 确认空字符串查询在 GaussDB 中不会意外匹配 NULL 值
3. 验证 `nickname` 字段（`EmptyStringCharField`）：
   ```sql
   UPDATE "user" SET nickname = '' WHERE email = 'test@example.com';
   SELECT nickname FROM "user" WHERE email = 'test@example.com';
   ```

**预期结果**：
- 对照组 MySQL 的 `access_token` 与 `nickname` 存储真实空字符串
- 实验组 GaussDB 的普通 `access_token` 可能存 NULL；`nickname` 存储 NULL 但 ORM 通过 `EmptyStringCharField` 回读 `""`
- 认证流程中的空值过滤在 GaussDB 下行为一致

---

### TC-AT-052: 用户注册补偿一致性

**前置条件**：无

**步骤**：
1. 注册新用户并验证正常路径完整性：
   ```
   POST /api/v1/users
   {
     "nickname": "TxTest",
     "email": "txtest@example.com",
     "password": "<encrypted>"
   }
   ```
2. 先执行正常注册并通过 Admin API 清理 fixture。随后本用例以明确的直接 DDL 故障注入临时把 `tenant` 表重命名，调用相同注册 API 制造 Tenant 创建失败；请求完成后无论成功或异常都必须在 `finally` 中立即恢复表名，并验证补偿清理：
   ```sql
   SELECT * FROM "user" WHERE email = 'txtest@example.com';     -- 应为空
   SELECT * FROM tenant WHERE id = '<would_be_user_id>';          -- 应为空
   SELECT * FROM user_tenant WHERE user_id = '<would_be_user_id>'; -- 应为空
   ```
3. 正常注册后验证所有记录存在：
   ```sql
   SELECT id, email, access_token, login_channel FROM "user" WHERE email = 'txtest@example.com';
   SELECT * FROM tenant WHERE id = '<user_id>';
   SELECT * FROM user_tenant WHERE user_id = '<user_id>' AND role = 'owner';
   ```

**预期结果**：
- 用户注册涉及 4 张表（User、Tenant、UserTenant、File）的写入
- 当前注册函数不是一个跨四表的 `DB.atomic()` 事务；失败一致性依赖 `rollback_user_registration()` 补偿清理
- 两组发生 Tenant 创建失败后均不得残留 user/user_tenant/file fixture；若残留则记录一致性缺陷

---

### TC-AT-053: GaussDB 复合主键 - api_token 表

**前置条件**：
- `api_token` 表使用复合主键 `(tenant_id, token)`

**步骤**：
1. 创建多个 API Token：
   ```
   POST /api/v1/system/tokens  (执行 3 次)
   ```
2. 验证复合主键唯一性：
   ```sql
   SELECT tenant_id, token, COUNT(*) as cnt
   FROM api_token
   GROUP BY tenant_id, token
   HAVING COUNT(*) > 1;
   ```
   - 应为空结果（无重复）
3. 本步骤是明确的唯一约束故障注入，允许直接数据库写入：从 API 创建的其中一个 token 读取完整行，尝试再次插入相同 `(tenant_id, token)` 组合，预期数据库拒绝；除约束失败本身外不得产生新记录
4. 通过 `DELETE /api/v1/system/tokens/<token>` 删除步骤 1 创建的全部 token，并只读确认 fixture 已清理

**预期结果**：
- GaussDB 正确支持复合主键约束
- `api_token` 表中 `(tenant_id, token)` 组合唯一

---

## 14. 测试数据准备

### 14.1 基础用户数据

所有基础用户必须通过 `POST /api/v1/users` 创建，密码由 `crypt()` 加密。保存响应中的用户 ID、Authorization 和 tenant 关系，再用数据库只读查询核对 User、Tenant、UserTenant、File；不得直接 INSERT 伪造注册结果。

禁用用户先通过注册 API 创建，再通过 `PUT /api/v1/admin/users/<email>/activate` 设置 `activate_status=off`。`status=0` 软删除、空字符串字段和约束冲突等没有公开 API 且用例明确要求的故障构造，才允许在对应 TC 内执行直接数据库操作。

### 14.2 密码生成辅助脚本

```python
#!/usr/bin/env python3
"""生成测试用户密码哈希"""
import base64
from werkzeug.security import generate_password_hash

raw_password = "Test@123"
b64_password = base64.b64encode(raw_password.encode()).decode()
# b64_password = "VGVzdEAxMjM="

password_hash = generate_password_hash(b64_password)
# 验证
from werkzeug.security import check_password_hash
assert check_password_hash(password_hash, b64_password)
print("Verification: PASSED")
```

辅助脚本不得把原始密码、密文、私钥或完整哈希写入共享日志；原始结果只记录算法前缀、长度和布尔验证结论。

---

## 15. 测试执行矩阵

| 优先级 | 测试用例编号 | 描述 | 依赖 |
|--------|-------------|------|------|
| P0 | TC-AT-001 | 正常密码登录成功 | 无 |
| P0 | TC-AT-002 | 未注册邮箱登录失败 | 无 |
| P0 | TC-AT-003 | 密码错误登录失败 | 无 |
| P0 | TC-AT-007 | 登录后使用 Token 访问资源 | TC-AT-001 |
| P0 | TC-AT-008 | 正常登出 | TC-AT-001 |
| P0 | TC-AT-024 | 创建 API Token | TC-AT-001 |
| P0 | TC-AT-027 | 使用 API Token 访问 | TC-AT-024 |
| P0 | TC-AT-038 | 完全无效的 Token | 无 |
| P0 | TC-AT-041 | 已登出 Token 重放 | TC-AT-008 |
| P0 | TC-AT-044 | 密码加密链路完整性 | 无 |
| P0 | TC-AT-051 | GaussDB 空字符串处理 | 无 |
| P1 | TC-AT-004 | 空请求体登录 | 无 |
| P1 | TC-AT-005 | 禁用账户登录 | 无 |
| P1 | TC-AT-006 | RSA 解密失败 | 无 |
| P1 | TC-AT-009 | 未认证登出 | 无 |
| P1 | TC-AT-010 | 登出后 Session 失效 | TC-AT-008 |
| P1 | TC-AT-011 | 获取登录通道 | 无 |
| P1 | TC-AT-016~023 | 密码忘记与重置全流程 | 无 |
| P1 | TC-AT-025~026 | Token 列表与删除 | TC-AT-024 |
| P1 | TC-AT-030~031 | Token 时效性 | TC-AT-001 |
| P1 | TC-AT-033~035 | Session 认证 | TC-AT-001 |
| P1 | TC-AT-047 | UserService 安全过滤 | 无 |
| P1 | TC-AT-052 | 用户注册补偿一致性 | 无 |
| P2 | TC-AT-012~015 | OAuth 全流程 | OAuth 配置 |
| P2 | TC-AT-028~029 | Beta Token | TC-AT-024 |
| P2 | TC-AT-036~037 | 并发登录 | TC-AT-001 |
| P2 | TC-AT-039~040 | Authorization 头边界 | 无 |
| P2 | TC-AT-042~043 | Token 篡改与跨用户 | TC-AT-001 |
| P2 | TC-AT-045~046 | 密码加密深度验证 | 无 |
| P2 | TC-AT-048~050 | 边界场景 | 无 |
| P2 | TC-AT-053 | GaussDB 复合主键 | TC-AT-024 |

---

## 16. 测试数据清理

1. 每组在自身用例完成后，通过主 API 删除 API Token，通过 Admin API 停用并删除本组创建的用户；OAuth、密码重置、并发登录和边界场景 fixture 均不得留给下一组。
2. 数据库仅执行只读残留核对。TC-AT-051 的字段语义更新、TC-AT-052 的 DDL 故障注入和 TC-AT-053 的唯一约束故障注入是本计划明确授权的例外，必须限定到专用 fixture，并在 `finally` 中恢复。
3. Redis 中的测试 session/captcha/OTP key、SMTPS 捕获邮件和 OAuth stub 授权码按运行 ID 前缀清除；不得清除另一个测试组或依赖服务的非本批次数据。
4. 清理失败本身记入报告，不能用后续直接 SQL 删除来掩盖 API 级联缺陷。
