# 认证 API 补充测试

## 概述
补充认证体系的细节，包括软删除用户的安全隐患、RSA 密钥对验证、token 管理和 OAuth callback。每个用例都先对照组、后实验组并生成独立证据；前端纯逻辑用例可使用同一构建 hash，但仍须在两个组别上下文各运行一次，不能复用一次输出填两组。所有可读密码 fixture 在发送前必须经 `api.utils.crypt.crypt()` RSA 加密。

## 软删除用户安全隐患测试

### TC-AT-SOFTDEL-001: 软删除用户无法重新注册
**前置条件**：
- User A 已注册并被软删除（status="0"）
- User A 的 email 为 `deleted@example.com`

**步骤**：
```http
POST /api/v1/users
Content-Type: application/json

{
  "email": "deleted@example.com",
  "nickname": "NewUser",
  "password": "Test@12345"
}
```

**预期响应**：HTTP 200，响应 JSON `code=103`，错误信息包含 "has already registered"

**数据库验证**：
```sql
SELECT COUNT(*) FROM "user" WHERE email = 'deleted@example.com';
-- 预期：1（原软删除用户仍存在，未创建新用户）
```

**说明**：当前实现中 `UserService.query(email=...)` 无 status 过滤，会找到软删除用户并阻止重新注册。这是预期行为，但可能不符合业务需求。

---

### TC-AT-SOFTDEL-002: 软删除用户密码重置流程
**前置条件**：
- User A 已软删除（status="0"）
- 两组运行根均配置本批次本地 SMTPS 捕获服务和测试 CA，能够实际接收 OTP 邮件；不得因外部 SMTP 缺失直接跳过

**步骤**：
1. 请求验证码：
```http
POST /api/v1/auth/password/forgot/captcha?email=deleted@example.com
```

接口返回图片后，用该组 Redis DB 对 `captcha:<email>` 做只读校验并取得本次 captcha；不得直接向 Redis 写入验证码。

2. 使用验证码申请邮件 OTP：
```http
POST /api/v1/auth/password/forgot/otp
Content-Type: application/json

{
  "email": "deleted@example.com",
  "captcha": "<valid_captcha>"
}
```

3. 验证 OTP：
```http
POST /api/v1/auth/password/forgot/otp/verify
Content-Type: application/json

{
  "email": "deleted@example.com",
  "otp": "<valid_otp>"
}
```

OTP 必须从本地 SMTPS 捕获到的真实邮件正文解析；不得直接构造或写 Redis OTP。

4. 提交新密码：
```http
POST /api/v1/auth/password/reset
Content-Type: application/json

{
  "email": "deleted@example.com",
  "new_password": "<encrypted_base64_password>",
  "confirm_new_password": "<encrypted_base64_password>"
}
```

**预期响应**：
- 当前实现：验证码/OTP/重置链路使用 `UserService.query()` 或 `query_user_by_email()`，没有 status 过滤；如果邮件 OTP 流程可用，软删除用户可继续重置密码
- 预期行为：HTTP 400（应拒绝软删除用户的密码重置）

**数据库验证**：
```sql
SELECT password FROM "user" WHERE email = 'deleted@example.com';
-- 当前实现：密码哈希已更新
-- 预期行为：密码哈希未更新
```

**说明**：这是一个潜在的安全隐患，建议修复。

---

### TC-AT-SOFTDEL-003: OAuth 登录软删除用户
**前置条件**：
- User A 已软删除（status="0"），email 为 `deleted@example.com`
- 各组 OAuth 配置指向本批次本地 stub provider；stub 提供 authorize/token/userinfo，回传该软删除用户 email。不得依赖真实 GitHub 账号或人工浏览器授权

**步骤**：
1. 发起本批次 `testoauth` 本地 stub OAuth 流程
2. stub callback 返回 email=`deleted@example.com`

**预期响应**：
- 当前实现：callback 预计 HTTP 302 重定向到 `/?auth=<token>`（`UserService.query(email=...)` 无 status 检查）
- 安全预期：HTTP 302 重定向到 `/?error=...`，不得下发 auth token（应检查 status="1"）

**数据库验证**：
```sql
SELECT access_token, last_login_time FROM "user" WHERE email = 'deleted@example.com';
-- 当前 existing-user 分支：access_token 被轮换，但 last_login_time 未更新
-- 安全预期：两者均不应改变
```

**说明**：当前 `oauth_callback()` 的 existing-user 分支只设置并保存新的
`access_token`，没有更新 `last_login_time`。这是一个安全隐患，软删除用户不应能
通过 OAuth 获得任何 auth token；测试不得把源码并不存在的 last-login 更新当成
预期。

---

## RSA 密钥对验证测试

### TC-AT-KEYPAIR-001: RSA 密钥对匹配验证
**前置条件**：
- 前端使用硬编码的 RSA 公钥（`web/src/utils/index.ts::rsaPsw`）
- 后端使用 `conf/private.pem` 私钥

**步骤**：
1. 前端使用硬编码公钥加密密码
2. 发送到后端
3. 后端使用私钥解密

**预期响应**：HTTP 200，登录成功

**验证方法**：
```bash
# 从后端私钥导出公钥（不要输出或复制私钥正文）
openssl pkey -in conf/private.pem -pubout -out /tmp/backend-public.pem
# 规范化并比较 /tmp/backend-public.pem 与 web/src/utils/index.ts 中 rsaPsw 公钥的 SHA-256 指纹
```

**说明**：如果密钥对不匹配，所有登录都会失败。

---

### TC-AT-KEYPAIR-002: RSA 密钥对不匹配场景
**前置条件**：
- 只在本批次一次性独立 `RAG_PROJECT_BASE` 运行根中替换私钥副本；不得修改仓库 `conf/private.pem` 或两套正式测试实例的密钥

**步骤**：
```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "test@example.com",
  "password": "<encrypted_with_frontend_public_key>"
}
```

**预期响应**：当前 login 捕获解密异常后返回 HTTP 200、`code=500`、消息 `Fail to crypt password`

**日志验证**：
```bash
grep "password decryption error" logs/ragflow.log
```

---

## Token 管理测试

### TC-AT-TOKEN-401-001: 401 响应触发 token 清理
**前置条件**：
- User A 已登录，token 存储在 localStorage
- Token 已过期或被撤销

**步骤**：
1. 前端发送请求，携带过期 token
2. 后端返回 401

**预期行为**：
- 前端 `removeAll()` 清理 localStorage 中的 `Authorization`、`Token`、`UserInfo`
- 前端重定向到 `/login`

**验证方法**：
```javascript
// 浏览器控制台
console.log(localStorage.getItem('Authorization'));  // 应为 null
console.log(localStorage.getItem('Token'));           // 应为 null
console.log(localStorage.getItem('UserInfo'));        // 应为 null
console.log(window.location.pathname);                // 应为 '/login'
```

---

### TC-AT-TOKEN-401-002: 防止重复重定向
**前置条件**：
- 多个并发请求同时返回 401

**预期行为**：
- 只触发一次重定向（`isRedirecting` flag 防止重复）

**验证方法**：
```javascript
// 模拟并发 401 响应
// 观察只有一次重定向
```

---

## OAuth Callback 测试

### TC-AT-OAUTH-CALLBACK-001: OAuth callback 通过 URL 参数传递 token
**前置条件**：
- User A 通过本批次 `testoauth` stub 登录

**步骤**：
1. 本地 stub 重定向回 RAGFlow：`http://127.0.0.1:<api-port>/?auth=<token>`
2. 前端 `useOAuthCallback` hook 检测 `?auth=` 参数
3. 前端存储 token 到 localStorage

**预期行为**：
- `localStorage.Authorization = <token>`；`authorizationUtil.setAuthorization()` 不自动添加 `Bearer `
- callback hook 本身不写 `UserInfo`，用户信息由后续 profile 请求加载
- URL 中的 `?auth=` 参数被清理

**验证方法**：
```javascript
// 浏览器控制台
console.log(localStorage.getItem('Authorization'));  // 应为 callback 中的原始 token
console.log(window.location.search);                  // 应为空
```

---

### TC-AT-OAUTH-CALLBACK-002: OAuth callback 无 auth 参数
**前置条件**：
- 用户直接访问 `https://ragflow.example.com/`（无 `?auth=` 参数）

**预期行为**：
- 前端正常加载页面，不触发 OAuth callback 逻辑

---

## 密码长度验证测试

### TC-AT-PWD-LENGTH-001: 注册密码无最小长度验证
**步骤**：
```http
POST /api/v1/users
Content-Type: application/json

{
  "email": "test@example.com",
  "nickname": "TestUser",
  "password": "1"  // 单字符密码
}
```

**预期响应**：
- 当前实现：HTTP 200（无最小长度验证）
- 预期行为：HTTP 400（应要求最小长度 8）

**说明**：后端无密码最小长度验证，依赖前端验证（min 1）。

---

### TC-AT-PWD-LENGTH-002: 登录密码无最小长度验证
**步骤**：
```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "test@example.com",
  "password": "1"
}
```

**预期响应**：
- 当前实现：使用 TC-AT-PWD-LENGTH-001 创建的一字符密码用户登录成功，HTTP 200、`code=0`
- 安全预期：注册阶段应因最小长度而拒绝，因此不应存在可用的一字符密码账号

---

## 异常处理测试

### TC-AT-EXCEPTION-001: 非法 JSON 请求体
**步骤**：
```http
POST /api/v1/auth/login
Content-Type: application/json

{invalid json}
```

**预期响应**：协议期望 HTTP 400。当前全局异常处理可能返回 HTTP 200、`code=100` 和 BadRequest 文本；若实测如此，记录为两组共有的异常状态码缺陷

---

### TC-AT-EXCEPTION-002: 缺少必填字段
**步骤**：
```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "test@example.com"
  // 缺少 password
}
```

**预期响应**：当前实现 HTTP 200、`code=500`、消息 `Fail to crypt password`；同时记录缺字段未返回 `ARGUMENT_ERROR` 的 API 契约问题

---

### TC-AT-EXCEPTION-003: 超大请求体
**前置条件**：
- 当前 API 服务的 `MAX_CONTENT_LENGTH` 已知。默认实现从环境变量 `MAX_CONTENT_LENGTH` 读取，未设置时为 `1073741824` 字节（1GB）。
- 若在共享环境执行，不应为了触发 413 发送超过 1GB 的请求体；应启动独立测试服务并将 `MAX_CONTENT_LENGTH` 设置为较小值（例如 1MB）后验证。

**步骤**：
```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "email": "test@example.com",
  "password": "<超过当前 MAX_CONTENT_LENGTH 的字符串>"
}
```

**预期响应**：
- 当请求体大小超过当前服务 `MAX_CONTENT_LENGTH`：HTTP 413，错误信息表示请求体过大。
- 当请求体没有超过当前服务 `MAX_CONTENT_LENGTH`：请求会进入正常登录处理链路，不应把“10MB 请求体”固定判定为超大请求。

---

## 总结

认证体系补充了 **14 个测试用例**，覆盖：
- ✅ 软删除用户的安全隐患（3 个）
- ✅ RSA 密钥对验证（2 个）
- ✅ Token 管理（2 个）
- ✅ OAuth callback（2 个）
- ✅ 密码长度验证（2 个）
- ✅ 异常处理（3 个）

所有测试用例已整合到测试方案中，认证体系测试覆盖完整。
