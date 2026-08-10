# 用户管理 Admin API 补充测试

## 概述
当前 `/api/v1/admin/*` REST 路由由独立 admin server 提供，不在主 API 端口上执行。本轮 GaussDB 兼容性测试应分别访问：

- 对照组 admin server：`http://127.0.0.1:9381/api/v1/admin/*`
- 实验组 admin server：`http://127.0.0.1:9481/api/v1/admin/*`

执行 `TC-UM-ADMIN-*` 前先通过 admin 登录接口获取 superuser token。不要把主 API 端口上的 404 误判为 Admin API 未注册。

本文示例中的可读密码均为 fixture；发送到 Admin API 前必须调用 `api.utils.crypt.crypt()` 生成 RSA 密文。Admin 错误响应固定使用 HTTP 400，具体语义由响应 JSON 的 `code` 表示；鉴权拒绝应使用 HTTP 403。

## Admin 用户管理测试（admin server）

### TC-UM-ADMIN-001: Admin 创建用户 - 正常流程
**前置条件**：
- 已登录的 superuser
- 目标邮箱未注册

**步骤**：
1. 发送请求：
```http
POST /api/v1/admin/users
Authorization: Bearer <superuser_token>
Content-Type: application/json

{
  "username": "admin-created@example.com",
  "password": "Test@12345",
  "role": "user"
}
```

2. 预期响应：HTTP 200
```json
{
  "code": 0,
  "message": "User created successfully",
  "data": {
    "email": "admin-created@example.com",
    "nickname": "",
    "is_superuser": false,
    "login_channel": "password"
  }
}
```

3. 数据库验证：
```sql
SELECT email, nickname, is_superuser, status FROM "user" 
WHERE email = 'admin-created@example.com';
-- 预期：一行记录，nickname 为空，is_superuser=false

SELECT COUNT(*) FROM tenant WHERE id = (
  SELECT id FROM "user" WHERE email = 'admin-created@example.com'
);
-- 预期：1

SELECT COUNT(*) FROM user_tenant WHERE user_id = (
  SELECT id FROM "user" WHERE email = 'admin-created@example.com'
) AND role = 'owner';
-- 预期：1
```

**预期结果**：用户创建成功，自动创建 tenant 和 user_tenant 关联

---

### TC-UM-ADMIN-002: Admin 创建用户 - 重复邮箱
**前置条件**：邮箱已存在

**步骤**：
```http
POST /api/v1/admin/users
{
  "username": "existing@example.com",
  "password": "Test@12345"
}
```

预期响应：HTTP 400，响应 JSON `code=409`，错误信息包含 "already exists"

---

### TC-UM-ADMIN-003: Admin 创建用户 - 无效邮箱
**步骤**：
```http
POST /api/v1/admin/users
{
  "username": "not-an-email",
  "password": "Test@12345"
}
```

预期响应：HTTP 400，响应 JSON `code=400`，错误信息包含 "Invalid email"

---

### TC-UM-ADMIN-004: Admin 创建 superuser
**步骤**：
```http
POST /api/v1/admin/users
{
  "username": "new-admin@example.com",
  "password": "Test@12345",
  "role": "admin"
}
```

数据库验证：
```sql
SELECT is_superuser FROM "user" WHERE email = 'new-admin@example.com';
-- 预期：true
```

---

### TC-UM-ADMIN-005: Admin 删除用户 - 需要先停用
**前置条件**：目标用户 active 且非 superuser

**步骤**：
```http
DELETE /api/v1/admin/users/active-user@example.com
```

预期响应：HTTP 400，错误信息包含 "is active and can't be deleted"

---

### TC-UM-ADMIN-006: Admin 删除用户 - 完整流程
**前置条件**：
- 目标用户已 inactive
- 目标用户非 superuser

**步骤**：
1. 先停用用户：
```http
PUT /api/v1/admin/users/target@example.com/activate
{
  "activate_status": "off"
}
```

2. 删除用户：
```http
DELETE /api/v1/admin/users/target@example.com
```

3. 数据库验证：
```sql
-- 检查级联删除
SELECT COUNT(*) FROM "user" WHERE email = 'target@example.com';
-- 预期：0

SELECT COUNT(*) FROM tenant WHERE id = '<target_user_id>';
-- 预期：0

SELECT COUNT(*) FROM user_tenant WHERE user_id = '<target_user_id>';
-- 预期：0

SELECT COUNT(*) FROM knowledgebase WHERE tenant_id = '<target_user_id>';
-- 预期：0

SELECT COUNT(*) FROM dialog WHERE tenant_id = '<target_user_id>';
-- 预期：0
```

**预期结果**：用户及其所有关联数据被级联删除

---

### TC-UM-ADMIN-007: Admin 删除 superuser - 被拒绝
**前置条件**：目标用户是已停用的次级 superuser。若仍 active，代码会先返回 active 用户不可删除，无法覆盖 superuser 专属分支

**步骤**：
```http
DELETE /api/v1/admin/users/superuser@example.com
```

预期响应：HTTP 400，错误信息包含 "Can't delete the super user"

---

### TC-UM-ADMIN-008: Admin 修改密码 - 不需要旧密码
**步骤**：
```http
PUT /api/v1/admin/users/target@example.com/password
{
  "new_password": "NewPass@123"
}
```

预期响应：HTTP 200，密码更新成功

数据库验证：
```sql
SELECT password FROM "user" WHERE email = 'target@example.com';
-- 预期：密码哈希已更新
```

---

### TC-UM-ADMIN-009: Admin 修改密码 - 相同密码
**步骤**：传入与当前相同的密码

预期响应：HTTP 200，消息 "Same password, no need to update!"

---

### TC-UM-ADMIN-010: Admin 激活用户
**步骤**：
```http
PUT /api/v1/admin/users/inactive@example.com/activate
{
  "activate_status": "on"
}
```

数据库验证：
```sql
SELECT is_active FROM "user" WHERE email = 'inactive@example.com';
-- 预期："1"
```

---

### TC-UM-ADMIN-011: Admin 停用用户
**步骤**：
```http
PUT /api/v1/admin/users/active@example.com/activate
{
  "activate_status": "off"
}
```

数据库验证：
```sql
SELECT is_active FROM "user" WHERE email = 'active@example.com';
-- 预期："0"
```

---

### TC-UM-ADMIN-012: Admin 激活状态 - 无效值
**步骤**：
```http
PUT /api/v1/admin/users/user@example.com/activate
{
  "activate_status": "invalid"
}
```

预期响应：HTTP 400，错误信息包含 "Invalid activate_status"

---

### TC-UM-ADMIN-013: Admin grant admin - 正常流程
**步骤**：
```http
PUT /api/v1/admin/users/normal@example.com/admin
```

数据库验证：
```sql
SELECT is_superuser FROM "user" WHERE email = 'normal@example.com';
-- 预期：true
```

---

### TC-UM-ADMIN-014: Admin grant admin - 自我操作被拒绝
**前置条件**：当前登录用户尝试 grant 自己

**步骤**：
```http
PUT /api/v1/admin/users/current-admin@example.com/admin
```

预期响应：HTTP 400，响应 JSON 中 `code=409`，错误信息包含 "can't grant current user"

---

### TC-UM-ADMIN-015: Admin revoke admin - 正常流程
**步骤**：
```http
DELETE /api/v1/admin/users/admin@example.com/admin
```

数据库验证：
```sql
SELECT is_superuser FROM "user" WHERE email = 'admin@example.com';
-- 预期：false
```

---

### TC-UM-ADMIN-016: Admin revoke admin - 自我操作被拒绝
**步骤**：
```http
DELETE /api/v1/admin/users/current-admin@example.com/admin
```

预期响应：HTTP 400，响应 JSON 中 `code=409`，错误信息应包含 "can't revoke current user"。当前代码疑似复用了 grant 文案；若实测返回 "can't grant"，记录为 Admin API 文案缺陷

---

### TC-UM-ADMIN-017: Admin 查看用户详情
**步骤**：
```http
GET /api/v1/admin/users/target@example.com
```

预期响应：HTTP 200，返回用户详细信息（avatar, email, language, last_login_time, is_active, is_anonymous, login_channel, status, is_superuser, create_date, update_date）

---

### TC-UM-ADMIN-018: Admin 查看用户数据集
**步骤**：
```http
GET /api/v1/admin/users/target@example.com/datasets
```

预期响应：HTTP 200，返回该用户拥有的所有数据集

---

### TC-UM-ADMIN-019: Admin 查看用户 agents
**步骤**：
```http
GET /api/v1/admin/users/target@example.com/agents
```

预期响应：HTTP 200，返回该用户创建的所有 agents

---

### TC-UM-ADMIN-020: 非 superuser 访问 Admin API - 被拒绝
**前置条件**：普通用户 token

**步骤**：
```http
GET /api/v1/admin/users
Authorization: Bearer <normal_user_token>
```

预期响应：HTTP 403

---

## 用户设置补充测试

### TC-UM-SET-001: 修改个人资料 - 受保护字段被忽略
**步骤**：
```http
PATCH /api/v1/users/me
{
  "nickname": "NewName",
  "email": "newemail@example.com",
  "status": "0",
  "is_superuser": true
}
```

数据库验证：
```sql
SELECT nickname, email, status, is_superuser FROM "user" WHERE id = '<current_user_id>';
-- 预期：只有 nickname 更新，其他字段不变
```

---

### TC-UM-SET-002: 修改密码 - 需要旧密码
**步骤**：
```http
PATCH /api/v1/users/me
{
  "password": "<encrypted_old_password>",
  "new_password": "<encrypted_new_password>"
}
```

预期响应：HTTP 200，密码更新成功

---

### TC-UM-SET-003: 修改密码 - 旧密码错误
**步骤**：传入错误的旧密码

预期响应：HTTP 200，响应 JSON `code=109`，错误信息 "Password error!"

---

### TC-UM-SET-004: 修改 nickname - 自动 strip
**步骤**：
```http
PATCH /api/v1/users/me
{
  "nickname": "  Spaced Name  "
}
```

数据库验证：
```sql
SELECT nickname FROM "user" WHERE id = '<current_user_id>';
-- 预期："Spaced Name"（已 strip）
```

---

### TC-UM-SET-005: 修改 nickname - 验证正则
**步骤**：
```http
PATCH /api/v1/users/me
{
  "nickname": "Invalid@Name#"
}
```

预期响应：HTTP 200，响应 JSON `code=101`，错误信息包含 nickname 验证失败
