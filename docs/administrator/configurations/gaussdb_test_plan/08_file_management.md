# 08 - 文件管理测试计划

## 1. 概述

本测试计划覆盖 RAGFlow 文件管理模块的完整功能测试，包括文件/文件夹的 CRUD 操作、文件移动、层级查询、版本控制和数据集链接等功能。测试重点验证 GaussDB 适配后的数据完整性和 API 行为一致性。

### 本批次强制执行约定

- 70 个用例均先在 MySQL metadata + Infinity 对照组执行，再在 GaussDB metadata + GaussDB DocEngine 实验组执行。两组使用独立 API、DB、Redis、MinIO logical namespace、日志和 credential；不引用任何备份脚本、旧结果或历史 fixture。
- 对照组 `${BASE_URL}=http://127.0.0.1:9380/api/v1`，实验组 `${BASE_URL}=http://127.0.0.1:9480/api/v1`。所有 ID 都由本批次 API 创建并映射，`non_existent_*` 在请求前用只读查询证明不存在。
- 业务 File/Folder/Dataset/Document/Commit 写入只走公开 API。metadata DB、DocEngine 和对象存储默认只读核对；`source_type` 空值兼容等明确字段测试可在专属 fixture 做最小 ORM 操作并恢复。
- 每个用例使用包含批次/组别/case ID 的唯一文件夹名、文件名和内容标识。对象存储核对必须按 DB 保存的 `parent_id/location` 解析当前 MinIO 配置：固定物理 bucket 模式验证逻辑 prefix/object 被清理而共享 bucket 保留；per-bucket 模式才要求物理 bucket 删除。
- 根目录 `GET /files` 不是纯读：它会创建根目录并执行 `init_knowledgebase_docs()`、`init_skills_folder()`。涉及根目录总数的用例先完成初始化，再保存基线，不能把 Knowledge Base/Skills 系统目录算成意外新增。
- `POST /files/link-to-datasets` 只表示后台 `_convert_files` 已调度，没有 Task ID。必须轮询 `file2document/document` 和日志到完成或超时；HTTP `code=0` 不能单独证明关联成功。
- File 模型没有 `permissions` 字段。`check_file_team_permission()` 仅在 owner 自己访问，或该 File 已通过 `file2document` 关联到一个 `permission="team"` Dataset 且访问者加入 Dataset owner tenant 时返回 True。所有 ACL 用例按这一真实模型建 fixture。
- `file.source_type` 使用空串兼容 Field：MySQL 物理保存 `''`，A/ORA-compatible GaussDB 物理保存 NULL，ORM/API 两组均回读 `""`。`location` 是普通 nullable CharField，传空串时实验组物理为 NULL；POST 可能从内存对象返回 `""`，后续 DB-backed list 可回读 null，不能套用 source_type 契约。
- 成功响应使用 HTTP 200 / `code=0`；File 路由的数据错误通常为 HTTP 200 / `code=102`，Pydantic 参数错误为 HTTP 200 / `code=101`。所有未认证测试使用无 cookie 客户端。

### 1.1 测试范围

**API 端点**：
- `POST /api/v1/files` - 上传文件或创建文件夹
- `GET /api/v1/files` - 列出文件
- `DELETE /api/v1/files` - 删除文件
- `GET /api/v1/files/<file_id>` - 下载文件
- `POST /api/v1/files/move` - 移动/重命名文件
- `GET /api/v1/files/<file_id>/parent` - 获取父文件夹
- `GET /api/v1/files/<file_id>/ancestors` - 获取所有祖先文件夹
- `POST /api/v1/files/link-to-datasets` - 链接文件到数据集
- `POST/GET /datasets/<entity_id>/commits` - 创建/列出提交
- `GET /datasets/<entity_id>/commits/<commit_id>` - 获取提交详情
- `GET /datasets/<entity_id>/commits/diff` - 比较提交差异
- `GET /datasets/<entity_id>/changes` - 获取未提交更改
- `GET /datasets/<entity_id>/commits/<commit_id>/tree` - 获取提交树
- `GET /files/<file_id>/versions` - 获取文件版本历史

**数据库表**：
- `file` - 文件和文件夹元数据
- `file2document` - 文件与文档的关联关系
- `file_commit` - 提交记录
- `file_commit_item` - 提交项（文件变更）
- `document` - 文档元数据

**GaussDB 适配点**：
- `file.source_type` 使用 `EmptyStringCharField`，历史默认值为空字符串 `""`
- 空字符串与 NULL 的兼容性处理
- 字段索引和查询行为

### 1.2 测试环境

- **对照组**：MySQL metadata + Infinity，API 9380
- **实验组**：GaussDB metadata + GaussDB DocEngine，API 9480
- **存储后端**：两组独立 MinIO/S3 logical namespace
- **认证方式**：各组独立签名登录 token；无 cookie 客户端

### 1.3 前置准备

```bash
# 获取认证令牌
AUTH_TOKEN="Bearer <当前组从登录接口获取的签名令牌>"
BASE_URL="<按当前组选择 9380 或 9480>/api/v1"

# 创建测试用户和数据集（如果尚未存在）
# 确保测试环境已启动并可用
```

---

## 2. 文件夹创建和管理测试

### TC-FM-001: 创建根目录下的文件夹

**前置条件**：
- 已登录用户，拥有有效的 `AUTH_TOKEN`
- 用户的根文件夹已初始化（首次调用 `GET /api/v1/files` 时自动创建）

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "测试文件夹", "type": "folder"}'
   ```
2. 预期响应：HTTP 200，返回 `code: 0`
3. 数据库验证：
   - 查询 `file` 表，验证新文件夹记录存在
   - `parent_id` 应等于用户的根文件夹 ID
   - `type` 字段值为 `"folder"`
   - MySQL 物理 `source_type=''`；GaussDB 物理 `source_type IS NULL`；两组 API/ORM 均为 `""`
   - `tenant_id` 和 `created_by` 应等于当前用户 ID

**预期结果**：
- 响应包含 `data.id`、`data.name`、`data.parent_id` 等字段
- 数据库中成功创建一条 `type="folder"` 的记录
- 文件夹的 `parent_id` 正确指向根文件夹

---

### TC-FM-002: 创建嵌套文件夹

**前置条件**：
- 已存在父文件夹 `parent_folder_id`

**步骤**：
1. 发送请求创建子文件夹：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "子文件夹", "parent_id": "${parent_folder_id}", "type": "folder"}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 新文件夹的 `parent_id` 等于 `parent_folder_id`
   - 层级关系正确

**预期结果**：
- 成功创建嵌套文件夹
- `parent_id` 字段正确关联到父文件夹

---

### TC-FM-003: 创建同名文件夹（重复检测）

**前置条件**：
- 在某个文件夹下已存在名为 "重复文件夹" 的子文件夹

**步骤**：
1. 发送请求创建同名文件夹：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "重复文件夹", "parent_id": "${existing_parent_id}", "type": "folder"}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0（数据错误）

**预期结果**：
- 返回错误信息："Duplicated folder name in the same folder."
- 数据库中没有新增记录
- 原有同名文件夹不受影响

---

### TC-FM-004: 创建虚拟文件夹（type=virtual）

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求创建虚拟文件夹（不指定 type 或指定非 folder 类型）：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "虚拟节点"}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `type` 字段值为 `"virtual"`
   - `size=0`；MySQL `location=''`，GaussDB 物理 NULL；分别保存 POST 响应与后续 list 响应，允许前者因内存对象为 `""`、后者 DB 回读为 null

**预期结果**：
- 成功创建虚拟类型的文件夹
- 数据库中 `type="virtual"`
- 另发送 `type="bogus"`：安全契约要求 `code=101` 拒绝未知类型；当前 service 把任何非 `folder` 值静默变成 `virtual`，若实测成功则登记枚举校验缺陷

---

### TC-FM-005: 创建文件夹 - 父文件夹不存在

**前置条件**：
- 使用一个不存在的 `parent_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "孤儿文件夹", "parent_id": "non_existent_folder_id", "type": "folder"}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 期望返回错误信息："Parent Folder Doesn't Exist!"
- 代码审查判定（仍须本批次实测）：`is_parent_folder_exist()` 在父目录不存在时以 1 个参数调用需要 2 个参数的 `delete_folder_by_pf_id(user_id, folder_id)`，外层应返回 HTTP 200 / `code=102` / `"Internal server error"`；按上述明确错误契约判为产品缺陷
- 数据库中没有新增记录

---

### TC-FM-006: 创建文件夹 - 名称为空

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求（名称为空字符串）：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "", "type": "folder"}'
   ```
2. 预期响应：当前 REST 包装返回 HTTP 200，JSON `code=101` 参数验证错误

**预期结果**：
- Pydantic 验证失败，返回参数错误（`StringConstraints(min_length=1)`）
- 数据库中没有新增记录

---

### TC-FM-007: 创建文件夹 - 名称超长（超过 255 字符）

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求（名称长度为 300 字符）：
   ```bash
   LONG_NAME=$(python3 -c "print('A' * 300)")
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d "{\"name\": \"${LONG_NAME}\", \"type\": \"folder\"}"
   ```
2. 预期响应：当前 REST 包装返回 HTTP 200，JSON `code=101` 参数验证错误

**预期结果**：
- Pydantic 验证失败（`max_length=255`）
- 数据库中没有新增记录

---

## 3. 文件上传测试

### TC-FM-008: 上传单个文件到根目录

**前置条件**：
- 已登录用户
- 准备一个测试文件 `test_document.pdf`

**步骤**：
1. 发送 multipart/form-data 请求：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "file=@test_document.pdf"
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `file` 表中新增一条记录
   - `type` 字段为文件类型（如 `"pdf"`）
   - `size` 字段等于文件大小
   - `location` 字段非空，指向存储位置
   - `source_type` 物理/API 语义按本节统一约定验证
   - 对象存储中以实际 `parent_id/location` 定位的 blob 与上传 bytes 完全一致

**预期结果**：
- 文件成功上传到对象存储
- 数据库记录正确反映文件元数据
- 响应包含 `data[0].id`、`data[0].name` 等字段

---

### TC-FM-009: 上传多个文件

**前置条件**：
- 已登录用户
- 准备多个测试文件

**步骤**：
1. 发送 multipart/form-data 请求（多个 file 字段）：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "file=@file1.pdf" \
     -F "file=@file2.docx" \
     -F "file=@file3.txt"
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `file` 表中新增三条记录
   - 每条记录的 `type` 对应正确的文件类型

**预期结果**：
- 所有文件成功上传
- 响应 `data` 数组包含三个文件的信息

---

### TC-FM-010: 上传文件到指定文件夹

**前置条件**：
- 已存在目标文件夹 `target_folder_id`

**步骤**：
1. 发送请求（包含 `parent_id`）：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "file=@test_document.pdf" \
     -F "parent_id=${target_folder_id}"
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 新文件的 `parent_id` 等于 `target_folder_id`

**预期结果**：
- 文件上传到指定文件夹
- 文件记录正确关联到父文件夹

---

### TC-FM-011: 上传文件 - 目标文件夹不存在

**前置条件**：
- 使用不存在的 `parent_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "file=@test_document.pdf" \
     -F "parent_id=non_existent_folder_id"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Can't find this folder!"
- 文件未上传，数据库无新增记录

---

### TC-FM-012: 上传文件 - 无文件部分

**前置条件**：
- 已登录用户

**步骤**：
1. 发送 multipart/form-data 请求但不包含 file 字段：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "parent_id=${root_folder_id}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："No file part!"

---

### TC-FM-013: 上传文件 - 空文件名

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求（file 字段但文件名为空）：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "file=@empty_fixture.bin;filename="
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："No file selected!"

---

## 4. 文件列表测试

### TC-FM-014: 列出根目录下的文件

**前置条件**：
- 用户的根文件夹下有若干文件和文件夹

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files?page=1&page_size=15" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 返回的文件列表与数据库中 `parent_id` 为根文件夹 ID 的记录一致

**预期结果**：
- 响应 `data.files` 数组包含文件和文件夹信息
- 响应包含 `data.total`（总数）和 `data.parent_folder`（父文件夹信息）
- 文件夹的 `size` 字段为递归计算的子文件大小总和
- 文件夹包含 `has_child_folder` 布尔字段
- 根目录 baseline 已包含系统 Knowledge Base/Skills 目录；只对本用例唯一 fixture 做集合差异断言

---

### TC-FM-015: 列出指定文件夹下的文件

**前置条件**：
- 已存在文件夹 `folder_id`，其中包含子文件

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files?parent_id=${folder_id}&page=1&page_size=10" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 仅返回 `parent_id` 等于 `folder_id` 的文件和文件夹
- 分页参数正确应用

---

### TC-FM-016: 文件列表 - 关键词搜索

**前置条件**：
- 文件夹下有名为 "报告.pdf" 和 "报告v2.pdf" 的文件

**步骤**：
1. 发送请求（带 keywords 参数）：
   ```bash
   curl -X GET "${BASE_URL}/files?keywords=报告&page=1&page_size=15" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 仅返回文件名包含 "报告" 的文件（大小写不敏感）
- 搜索结果正确过滤

---

### TC-FM-017: 文件列表 - 排序和分页

**前置条件**：
- 文件夹下有超过 15 个文件

**步骤**：
1. 发送请求（指定排序和分页）：
   ```bash
   curl -X GET "${BASE_URL}/files?orderby=create_time&desc=true&page=2&page_size=5" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 返回第 2 页，每页 5 条
- 按 `create_time` 降序排列
- `data.total` 反映总文件数
- fixture 使用可区分时间；比较 page 1/2 无重复且并集正确，不能在相同时间上臆断次序
- `page=0`、`page_size=0/101`、`desc=not_bool` 应 `code=101`，由现有 schema 拒绝
- `orderby=__bad__` 安全契约也应 `code=101`；当前 ListFileReq 未限制 orderby，service 可能到字段解析后返回 `code=102/Internal server error`，复现即参数 schema 缺口

---

### TC-FM-018: 文件列表 - 文件夹不存在

**前置条件**：
- 使用不存在的 `parent_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files?parent_id=non_existent_folder_id" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Folder not found!"

---

## 5. 文件移动测试

### TC-FM-019: 移动文件到目标文件夹

**前置条件**：
- 已存在文件 `file_id` 和目标文件夹 `dest_folder_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id}"], "dest_file_id": "${dest_folder_id}"}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 文件的 `parent_id` 更新为 `dest_folder_id`
   - 文件的 `location` 字段更新（存储位置迁移）
   - 以旧 `parent_id/location` 只读确认旧对象不存在，以新 `parent_id/location` 读取并与原 bytes 比较

**预期结果**：
- 文件在对象存储中从原文件夹迁移到目标文件夹
- 数据库记录正确更新
- 另以普通文件 ID 作为 `dest_file_id`：安全契约要求 `code=102` 拒绝“目标不是文件夹”；当前 service 只检查目标记录存在，不检查 `type`，若移动成功并把文件记录当 bucket/parent 使用则登记层级完整性缺陷

---

### TC-FM-020: 移动多个文件到目标文件夹

**前置条件**：
- 已存在多个文件 `file_id_1`, `file_id_2`, `file_id_3`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id_1}", "${file_id_2}", "${file_id_3}"], "dest_file_id": "${dest_folder_id}"}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 所有文件的 `parent_id` 均更新为 `dest_folder_id`

**预期结果**：
- 批量移动成功
- 所有文件正确关联到新父文件夹

---

### TC-FM-021: 重命名单个文件（不移动）

**前置条件**：
- 已存在文件 `file_id`，当前名称为 "old_name.pdf"

**步骤**：
1. 发送请求（仅提供 `new_name`，不提供 `dest_file_id`）：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id}"], "new_name": "new_name.pdf"}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 文件的 `name` 字段更新为 "new_name.pdf"
   - `parent_id` 不变
   - 如果文件关联了文档（`file2document`），文档的 `name` 也同步更新

**预期结果**：
- 文件成功重命名
- 存储位置不变（无存储层操作）
- 关联文档名称同步更新
- 以重命名前后的 `parent_id/location` 读取同一对象并比较 hash，不能只看 DB name

---

### TC-FM-022: 移动并重命名文件

**前置条件**：
- 已存在文件 `file_id` 和目标文件夹 `dest_folder_id`

**步骤**：
1. 发送请求（同时提供 `dest_file_id` 和 `new_name`）：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id}"], "dest_file_id": "${dest_folder_id}", "new_name": "renamed.pdf"}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `parent_id` 更新为 `dest_folder_id`
   - `name` 更新为 "renamed.pdf"
   - `location` 更新（存储迁移）

**预期结果**：
- 文件同时完成移动和重命名

---

### TC-FM-023: 移动文件 - 目标文件夹不存在

**前置条件**：
- 使用不存在的 `dest_file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id}"], "dest_file_id": "non_existent_folder"}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Parent folder not found!"
- 文件未被移动

---

### TC-FM-024: 移动文件夹到自身子文件夹（循环检测）

**前置条件**：
- 文件夹 A 包含子文件夹 B

**步骤**：
1. 发送请求（尝试将 A 移动到 B）：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${folder_A_id}"], "dest_file_id": "${folder_B_id}"}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Cannot move a folder into its own subfolder."
- 防止循环引用

---

### TC-FM-025: 重命名文件 - 更改扩展名（禁止）

**前置条件**：
- 已存在文件 "document.pdf"

**步骤**：
1. 发送请求（尝试将 .pdf 改为 .docx）：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id}"], "new_name": "document.docx"}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："The extension of file can't be changed"
- 文件名未更改

---

### TC-FM-026: 重命名文件 - 同名冲突

**前置条件**：
- 同一文件夹下已存在 "report.pdf"
- 尝试将另一个文件重命名为 "report.pdf"

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${other_file_id}"], "new_name": "report.pdf"}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Duplicated file name in the same folder."

---

### TC-FM-027: 移动文件 - 缺少必要参数

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求（不提供 `dest_file_id` 也不提供 `new_name`）：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id}"]}'
   ```
2. 预期响应：当前 REST 包装返回 HTTP 200，JSON `code=101` 参数验证错误

**预期结果**：
- Pydantic 验证失败："At least one of dest_file_id or new_name must be provided"

---

### TC-FM-028: 移动文件 - 多文件重命名（禁止）

**前置条件**：
- 尝试对多个文件同时使用 `new_name`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/move" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"src_file_ids": ["${file_id_1}", "${file_id_2}"], "new_name": "single_name.pdf"}'
   ```
2. 预期响应：当前 REST 包装返回 HTTP 200，JSON `code=101` 参数验证错误

**预期结果**：
- Pydantic 验证失败："new_name can only be used with a single file"

---

## 6. 文件删除测试

### TC-FM-029: 删除单个文件

**前置条件**：
- 已存在文件 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": ["${file_id}"]}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `file` 表中该记录被删除
   - 对象存储中对应文件被删除
   - 如果存在 `file2document` 关联，关联记录和对应的 `document` 记录也被删除
   - 删除前保存 Document/Task/DocEngine/object 地址，删除后按保存 ID/地址核对；不能删除后用已为空的子查询制造假通过

**预期结果**：
- 文件从存储和数据库中完全删除
- 响应包含 `data.success_count: 1`

---

### TC-FM-030: 删除多个文件

**前置条件**：
- 已存在多个文件 `file_id_1`, `file_id_2`

**步骤**：
1. 发送请求：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": ["${file_id_1}", "${file_id_2}"]}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 两个文件记录均被删除

**预期结果**：
- 批量删除成功
- 响应 `data.success_count: 2`

---

### TC-FM-031: 删除文件夹（递归删除）

**前置条件**：
- 文件夹 `folder_id` 下包含子文件和子文件夹

**步骤**：
1. 发送请求：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": ["${folder_id}"]}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 文件夹及其所有子文件/子文件夹均被删除
   - 对象存储中所有保存的 object key 被删除；固定物理 bucket 模式只要求各 folder logical prefix 清空且共享 bucket 保留，per-bucket 模式要求专属 bucket 不存在

**预期结果**：
- 递归删除成功
- 所有嵌套内容被清理
- 响应 `data.success_count` 等于删除的总文件/文件夹数

---

### TC-FM-032: 删除文件 - 文件不存在

**前置条件**：
- 使用不存在的 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": ["non_existent_file_id"]}'
   ```
2. 精确预期：HTTP 200 / `code=102`，顶层 message 为 `"Deleted files failed with 1 errors"`

**预期结果**：
- 响应 `data.errors` 包含错误信息："File or Folder not found: non_existent_file_id"
- `data.success_count: 0`

---

### TC-FM-033: 删除文件 - 无权限

**前置条件**：
- 文件属于其他租户，未关联 team Dataset，且两用户无 tenant 成员关系

**步骤**：
1. 发送请求（使用当前用户删除他人文件）：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": ["${other_tenant_file_id}"]}'
   ```
2. 精确预期：HTTP 200 / `code=102`，`data.success_count=0` 且 errors 含 `No authorization for file ...`

**预期结果**：
- 响应 `data.errors` 包含："No authorization for file ..."
- 文件未被删除

---

### TC-FM-034: 删除文件 - 知识库源文件保护

**前置条件**：
- 文件 `source_type` 为 `FileSource.KNOWLEDGEBASE`

**步骤**：
1. 发送请求：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": ["${kb_source_file_id}"]}'
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 知识库源文件被跳过（不执行删除）
- 响应 `data.success_count: 0`
- 知识库源文件需要通过知识库 API 删除
- 这是明确执行并验证“跳过保护”的用例，不得把整个 case 标记 SKIP

---

### TC-FM-035: 删除文件 - ids 为空数组

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求：
   ```bash
   curl -X DELETE "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"ids": []}'
   ```
2. 预期响应：当前 REST 包装返回 HTTP 200，JSON `code=101` 参数验证错误

**预期结果**：
- Pydantic 验证失败（`min_length=1`）

---

## 7. 文件链接到数据集测试

### TC-FM-036: 链接文件到数据集

**前置条件**：
- 已存在文件 `file_id` 和数据集 `kb_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/link-to-datasets" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"file_ids": ["${file_id}"], "kb_ids": ["${kb_id}"]}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 响应只表示后台线程已调度；按唯一 file/kb ID 轮询到关系出现后再做数据库验证：
   - `document` 表新增一条记录，`kb_id` 等于目标数据集 ID
   - `file2document` 表新增一条关联记录

**预期结果**：
- 文件成功链接到数据集
- 文档记录被创建，包含正确的解析器配置
- 响应 `data: true`

---

### TC-FM-037: 链接文件夹到数据集（递归展开）

**前置条件**：
- 文件夹 `folder_id` 下包含嵌套的真实文件，并包含一个 `type="virtual"` 节点作为边界 fixture

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/link-to-datasets" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"file_ids": ["${folder_id}"], "kb_ids": ["${kb_id}"]}'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 先保存文件夹下全部 innermost 非 folder 文件 ID，响应后轮询，逐个验证数据库关系：
   - 文件夹下所有非文件夹文件均被链接到数据集
   - 每个文件对应一条 `document` 和 `file2document` 记录

**预期结果**：
- 文件夹被递归展开，所有内部真实文件被链接
- 嵌套文件夹中的文件也被正确处理
- 空文件夹展开为 0 个 ID 时仍会返回调度成功但无关联；单独记录，不误报有文档
- virtual 节点不是可下载文件，不应创建 Document；当前 `get_all_innermost_file_ids()` 把所有非-folder 类型都加入，若 virtual 被链接则登记类型过滤缺陷

---

### TC-FM-038: 链接文件到数据集 - 文件不存在

**前置条件**：
- 使用不存在的 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/link-to-datasets" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"file_ids": ["non_existent_file"], "kb_ids": ["${kb_id}"]}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："File not found!"

---

### TC-FM-039: 链接文件到数据集 - 数据集不存在

**前置条件**：
- 使用不存在的 `kb_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files/link-to-datasets" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"file_ids": ["${file_id}"], "kb_ids": ["non_existent_kb"]}'
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Can't find this dataset!"

---

## 8. 文件层级查询测试

层级/下载/移动/删除的 team 权限来自关联 Dataset，不来自 File 自身。owner-only fixture 不建立 `file2document`；team fixture 必须通过公开 link API 关联到 team Dataset，并通过公开 tenant 邀请/确认 API建立成员关系。

### TC-FM-040: 获取文件的父文件夹

**前置条件**：
- 文件 `file_id` 存在于某个文件夹下

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${file_id}/parent" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 返回的父文件夹 ID 等于文件的 `parent_id`

**预期结果**：
- 响应 `data.parent_folder` 包含父文件夹的完整信息
- 包含 `id`、`name`、`type` 等字段

---

### TC-FM-041: 获取根文件夹的父文件夹

**前置条件**：
- 根文件夹的 `parent_id` 等于自身 `id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${root_folder_id}/parent" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 返回根文件夹自身的信息（因为根文件夹的 `parent_id` 指向自身）

---

### TC-FM-042: 获取文件的所有祖先文件夹

**前置条件**：
- 文件位于多层嵌套文件夹中：根 -> A -> B -> C -> 文件

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${file_id}/ancestors" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data.parent_folders` 数组包含所有祖先文件夹
- 当前实现 `FileService.get_all_parent_folders(start_id)` 会把起始文件/文件夹自身作为数组第一个元素返回，然后依次返回父级直到根文件夹
- 对“根 -> A -> B -> C -> 文件”的路径，当前响应顺序应为：文件、C、B、A、根文件夹

---

### TC-FM-043: 获取祖先文件夹 - 文件不存在

**前置条件**：
- 使用不存在的 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/non_existent_file/ancestors" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 精确返回 HTTP 200 / `code=102` / `"Folder not found!"`

---

### TC-FM-044: 获取父文件夹 - 无权限

**前置条件**：
- 文件属于其他租户，未关联 team Dataset，且两用户无 tenant 成员关系

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${other_tenant_file_id}/parent" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："No authorization."
- 权限检查通过 `check_file_team_permission` 验证

---

## 9. 文件版本和提交测试

本节正常流全部使用 owner 的专属 folder/file。安全契约要求 folder、workspace、dataset commit 路由以及 file versions 都校验当前用户对实体的访问权；用另一无关系租户重复读取/写入时应 HTTP 200 / `code=108`（或等价不泄露的拒绝）且 DB/对象不变。代码审查显示当前 `file_commit_api.py` 只有 `login_required`，没有调用 `check_file_team_permission/check_kb_team_permission`，因此实测若返回数据或允许写入，一律登记高严重度 IDOR，不能按现状放行。

### TC-FM-045: 创建提交（add 操作）

**前置条件**：
- 已存在工作区文件夹 `folder_id`
- 文件夹下有文件 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/folders/${folder_id}/commits" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "初始提交",
       "files": [{
         "file_id": "${file_id}",
         "file_name": "test.txt",
         "operation": "add",
         "content": "Hello World"
       }]
     }'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `file_commit` 表新增一条记录
   - `file_commit_item` 表新增一条记录，`operation="add"`
   - `file_commit.tree_state` 包含文件快照
   - 对象存储中 `.objects/<hash>` 存储了文件内容
   - 请求中的 file 必须真实位于 folder 递归树内；另用专属跨 folder file ID 重放，安全预期拒绝且不得修改该 file。当前 service 未校验归属，若成功即 IDOR/越界写缺陷

**预期结果**：
- 提交成功创建
- 响应包含 `data.id`、`data.message`、`data.file_count` 等字段
- `parent_id` 为 `null`（首次提交）

---

### TC-FM-046: 创建提交（modify 操作）

**前置条件**：
- 已存在一个提交（有 `parent_id`）
- 修改文件内容

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/folders/${folder_id}/commits" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "修改文件内容",
       "files": [{
         "file_id": "${file_id}",
         "file_name": "test.txt",
         "operation": "modify",
         "content": "Hello World Modified"
       }]
     }'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - 新提交的 `parent_id` 指向上一个提交
   - `file_commit_item` 记录了 `old_hash` 和 `new_hash`

**预期结果**：
- 修改操作成功记录
- 提交链正确（`parent_id` 链接）

---

### TC-FM-047: 创建提交（delete 操作）

**前置条件**：
- 文件已存在于提交的 `tree_state` 中

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/folders/${folder_id}/commits" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "删除文件",
       "files": [{
         "file_id": "${file_id}",
         "operation": "delete"
       }]
     }'
   ```
2. 安全/功能契约：HTTP 200，`code=0`，commit item 为 delete、tree_state 标记 `status="0"`，且历史内容仍可读取
3. 代码审查判定（仍须本批次实测）：当前 `File` 模型没有 `status`，`File.update(status="0")` 会触发 `AttributeError`，路由应返回 HTTP 200 / `code=100`；`DB.atomic()` 必须回滚本次 `file_commit` 和 `file_commit_item`

**预期结果**：
- 当前实现复现异常即判产品缺陷；失败路径不得留下半提交
- 修复后的提交记录应正确反映删除，同时不凭空要求当前 `file` 表已有不存在的 status 列

---

### TC-FM-048: 创建提交（rename 操作）

**前置条件**：
- 文件已存在于提交中

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/folders/${folder_id}/commits" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{
       "message": "重命名文件",
       "files": [{
         "file_id": "${file_id}",
         "operation": "rename",
         "old_name": "old_name.txt",
         "new_name": "new_name.txt"
       }]
     }'
   ```
2. 预期响应：HTTP 200，`code: 0`
3. 数据库验证：
   - `file_commit_item.old_name` 和 `new_name` 正确记录
   - `file` 表中文件名更新
   - `tree_state` 中文件名更新

**预期结果**：
- 重命名操作成功
- 提交项记录了新旧名称

---

### TC-FM-049: 列出提交（分页）

**前置条件**：
- 文件夹下有多个提交

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/commits?page=1&page_size=10" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应包含 `data.total`、`data.commits` 数组
- 每个提交包含 `id`、`message`、`author_id`、`create_time` 等字段
- 按 `create_time` 降序排列（最新在前）
- 再发送 `page=abc`、`page=0`、`page_size=0`、`order_by=__bad__`。安全契约要求统一 `code=101`；当前 commit route 没有 Pydantic/上下界校验，非整数或坏字段会 `code=100`，0 值可能禁用分页，复现分别登记参数处理缺陷

---

### TC-FM-050: 获取提交详情

**前置条件**：
- 已存在提交 `commit_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/commits/${commit_id}" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应包含提交的完整信息
- 包含 `data.files` 数组，列出所有文件变更项
- 每个文件项包含 `file_id`、`operation`、`old_hash`、`new_hash`、`old_name`、`new_name`

---

### TC-FM-051: 列出提交中的文件

**前置条件**：
- 已存在提交 `commit_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/commits/${commit_id}/files" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data` 数组包含所有 `file_commit_item` 记录
- 每条记录包含 `id`、`file_id`、`operation`、`old_hash`、`new_hash`、`old_location`、`new_location`、`old_name`、`new_name`

---

### TC-FM-052: 比较两个提交的差异

**前置条件**：
- 存在两个提交 `from_commit_id` 和 `to_commit_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/commits/diff?from=${from_commit_id}&to=${to_commit_id}" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data` 数组包含差异项
- 每个差异项包含 `file_id`、`file_name`、`operation`（add/modify/delete/rename）
- 包含 `old_hash`、`new_hash`、`old_location`、`new_location`
- 基于 `tree_state` 快照比较，而非仅 `file_commit_item`

---

### TC-FM-053: 获取未提交的更改

**前置条件**：
- 自上次提交以来，文件夹中有文件变更

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/changes" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data` 数组包含未提交的变更
- 每个变更包含 `file_id`、`file_name`、`operation`（add/modify/delete）
- 通过比较当前 `file` 表与最新提交的 `tree_state` 得出

---

### TC-FM-054: 获取提交树（层级结构）

**前置条件**：
- 已存在提交 `commit_id`，包含多层文件夹结构

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/commits/${commit_id}/tree" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data` 为层级树结构
- 包含 `id`、`name`、`type`（folder/file）、`children` 等字段
- 子文件夹正确嵌套，通过 `file` 表的 `parent_id` 解析
- 创建 commit 后移动/重命名 live 子文件夹，再读取旧 commit：版本快照应仍显示提交时层级/名称。当前 `_build_hierarchical_tree()` 会查询 live File 表，若历史树随当前目录变化则登记版本快照不稳定缺陷

---

### TC-FM-055: 获取提交中的文件内容

**前置条件**：
- 提交中包含文件 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/folders/${folder_id}/commits/${commit_id}/files/${file_id}/content" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data.content` 包含文件在该提交时的内容
- 优先从 `tree_state` 中解析 hash，再从对象存储读取
- 如果当前提交无该文件的 item，遍历 `parent_id` 链查找

---

### TC-FM-056: 获取文件版本历史

**前置条件**：
- 文件在多个提交中被修改

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${file_id}/versions" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 响应 `data` 数组包含该文件的所有版本记录
- 每条记录包含 `commit_id`、`operation`、`hash`、`create_time`、`message`
- 按 `create_time` 降序排列
- 无关系租户请求同一 file ID 必须拒绝且不泄露 commit message/hash；当前 route 未做 File 权限检查，若返回列表即安全缺陷

---

### TC-FM-057: 通过数据集 ID 访问提交（datasets 路由）

**前置条件**：
- 数据集 `dataset_id` 已通过文档上传等流程创建对应的知识库文件夹（`file.source_type="knowledgebase"` 且名称与数据集名一致）
- 仅创建空数据集时 `_resolve_dataset_folder()` 可能找不到文件夹，此时不能用该数据集验证 `/datasets/<dataset_id>/commits`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/datasets/${dataset_id}/commits?page=1&page_size=10" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 通过 `_resolve_dataset_folder` 解析数据集到文件夹 ID
- 返回与直接访问 `/folders/<folder_id>/commits` 相同的结果
- 无关系租户访问该 Dataset 路由必须拒绝；当前 resolver 只按 ID 查询 KB 和 folder、不检查 KB team 权限，若泄露提交即 IDOR

---

## 10. 文件下载测试

### TC-FM-058: 下载文件

**前置条件**：
- 已存在文件 `file_id`，有实际内容

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${file_id}" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -o downloaded_file.pdf
   ```
2. 预期响应：HTTP 200，返回文件二进制流

**预期结果**：
- 文件内容正确下载
- 响应头包含正确的 `Content-Type`
- 文件大小与数据库记录一致

---

### TC-FM-059: 下载文件 - 文件不存在

**前置条件**：
- 使用不存在的 `file_id`

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/non_existent_file_id" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："Document not found!"

---

### TC-FM-060: 下载文件 - 空文件

**前置条件**：
- 文件存在但存储中内容为空

**步骤**：
1. 发送请求：
   ```bash
   curl -X GET "${BASE_URL}/files/${empty_file_id}" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 期望返回错误信息："This file is empty."
- 代码审查判定（仍须本批次实测）：主存储读到 `b''` 后会调用 `File2DocumentService.get_storage_address(file_id=...)`；未链接文件会触发 `assert doc_id`，外层应返回 HTTP 200 / `code=102` / `"Internal server error"`，按上述明确空文件错误契约判产品缺陷
- 先尝试主存储路径，再尝试 `file2document` 回退路径

---

## 11. 边界和异常测试

### TC-FM-061: 文件名称包含特殊字符

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求（文件名包含特殊字符）：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "测试@#$%^&()_+-=[]{}|;:,.<>?.txt", "type": "folder"}'
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- 特殊字符正确存储和返回
- 无 SQL 注入或 XSS 风险
- 另上传 multipart filename=`../escape.txt` 和绝对路径风格名称：安全契约要求拒绝或只采用安全 basename，不能创建名为 `..` 的层级。当前 upload 按 `/` split 并自动建目录，若产生 `..` folder 则登记路径规范化缺陷；对象仍必须限制在当前组 logical namespace

---

### TC-FM-062: 文件名称包含 Unicode 字符

**前置条件**：
- 已登录用户

**步骤**：
1. 发送请求：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "日本語テスト文件_中文_한국어", "type": "folder"}'
   ```
2. 预期响应：HTTP 200，`code: 0`

**预期结果**：
- Unicode 字符正确处理
- 数据库存储和 API 返回一致

---

### TC-FM-063: 并发创建同名文件夹

**前置条件**：
- 同一父文件夹下

**步骤**：
1. 并发发送多个请求创建同名文件夹：
   ```bash
   for i in {1..5}; do
     curl -X POST "${BASE_URL}/files" \
       -H "Authorization: ${AUTH_TOKEN}" \
       -H "Content-Type: application/json" \
       -d '{"name": "并发文件夹", "type": "folder"}' &
   done
   wait
   ```
2. 预期响应：仅一个成功，其余返回重复错误

**预期结果**：
- 数据库中仅创建一条记录
- 其余请求返回 "Duplicated folder name in the same folder."
- 无数据竞争或死锁
- 代码审查显示 `file` 表没有 `(parent_id,name)` 唯一约束，service 是 check-then-insert；5 请求用 barrier 同时发出并保存全部响应，若最终多于 1 行则登记并发一致性缺陷，不能按每次串行重试掩盖

---

### TC-FM-064: 深层嵌套文件夹（超过 10 层）

**前置条件**：
- 逐层创建 15 层嵌套文件夹

**步骤**：
1. 循环创建嵌套文件夹：
   ```bash
   PARENT_ID="${root_folder_id}"
   for i in {1..15}; do
     RESPONSE=$(curl -s -X POST "${BASE_URL}/files" \
       -H "Authorization: ${AUTH_TOKEN}" \
       -H "Content-Type: application/json" \
       -d "{\"name\": \"level_${i}\", \"parent_id\": \"${PARENT_ID}\", \"type\": \"folder\"}")
     PARENT_ID=$(echo $RESPONSE | jq -r '.data.id')
   done
   ```
2. 验证所有文件夹创建成功

**预期结果**：
- 所有 15 层文件夹均成功创建
- 层级关系正确
- 获取最深层文件的 ancestors 时，当前实现会包含起始文件自身和根文件夹；如果最深层文件位于 15 层目录下，`parent_folders` 长度应为 17（文件 + 15 层文件夹 + 根）

---

### TC-FM-065: 请求缺少 Authorization 头

**前置条件**：
- 不发送认证令牌

**步骤**：
1. 使用全新无 cookie 客户端发送请求（无 Authorization）：
   ```bash
   curl -X GET "${BASE_URL}/files"
   ```
2. 预期响应：HTTP 401 或认证错误

**预期结果**：
- `@login_required` 装饰器拦截请求
- 返回未认证错误

---

### TC-FM-066: 跨租户访问文件

**前置条件**：
- 租户 A 和租户 B 各有自己的 owner-only 文件，彼此无 tenant 关系且文件未关联 team Dataset

**步骤**：
1. 使用租户 A 的令牌访问租户 B 的文件：
   ```bash
   curl -X GET "${BASE_URL}/files/${tenant_b_file_id}/parent" \
     -H "Authorization: ${TENANT_A_TOKEN}"
   ```
2. 预期响应：HTTP 200，但 `code` 非 0

**预期结果**：
- 返回错误信息："No authorization."
- `check_file_team_permission` 验证失败
- 租户 B 的文件未被访问
- 在同一批专属 A/B fixture 上继续执行以下安全子路径，全部应拒绝且 B 数据不变：A 以 B folder 作为创建/上传 parent；A 把自己的 file 移入 B folder；A 以 B file 作为移动目标；A 读取 B file versions/commit/folder commit/dataset commit。当前 create/upload/list destination 和 commit/version 路径存在缺少权限检查的代码风险，任一成功均单独登记 IDOR/跨租户层级注入缺陷
- 若先利用跨租户 parent 注入创建 A 子项，再由 B 删除自己的父文件夹，递归删除不得越权删除 A 子项；仅在两名本批次专属测试用户/对象上验证并完整清理

---

## 12. GaussDB 空字符串兼容字段验证

### TC-FM-067: File.source_type 默认空字符串

**前置条件**：
- 创建文件时不指定 `source_type`

**步骤**：
1. 上传文件或创建文件夹：
   ```bash
   curl -X POST "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -H "Content-Type: application/json" \
     -d '{"name": "测试文件夹", "type": "folder"}'
   ```
2. 数据库验证：
   ```sql
   SELECT id, source_type FROM file WHERE id = '<新创建的文件ID>';
   ```
3. 验证 `source_type` 字段值

**预期结果**：
- MySQL 中 `source_type` 存储为空字符串 `""`；GaussDB A-compatible 存储层会将应用层空字符串归一化为 SQL `NULL`
- 字段类型为 `EmptyStringCharField`，`default=""`；该字段在 GaussDB 下允许 NULL，并通过 `python_value()` 向 API 层还原为 `""`
- API 返回时 `source_type` 为 `""`

---

### TC-FM-068: File.source_type 查询空字符串

**前置条件**：
- 数据库中存在 `source_type=""` 的文件记录

**步骤**：
1. 通过 API 列出文件并按 ID 验证回读：
   ```bash
   curl -X GET "${BASE_URL}/files" \
     -H "Authorization: ${AUTH_TOKEN}"
   ```
2. 在当前组隔离测试进程执行只读 ORM `FileService.query(id=<id>, source_type="")`，并执行物理 SQL：MySQL `source_type=''`、GaussDB `source_type IS NULL`

**预期结果**：
- 应用层 `source_type == ""` 查询在 GaussDB 中正确命中存储层 `NULL` 或历史真实空字符串
- 不因空字符串/NULL 存储差异导致遗漏记录
- `EmptyStringFieldMixin` 正确处理空字符串查询

---

### TC-FM-069: File.source_type 更新为空字符串

**前置条件**：专属字段兼容 fixture 当前 `source_type="field-test"`；公开 File API 没有更新 source_type 的端点，本用例是明确的最小 ORM 字段测试例外

**步骤**：
1. 在当前组隔离测试进程调用 `FileService.update_by_id(<id>, {"source_type": ""})`
2. 物理验证：MySQL 为 `''`，GaussDB 为 NULL；ORM/API 均回读 `""`
3. 再通过移动/重命名 API 更新其它字段，验证 source_type 仍为业务空串；最后用 API 删除 fixture

**预期结果**：
- 空串兼容写入/查询契约成立；GaussDB 物理 NULL 是预期，不得把它误报为“意外变 NULL”

---

### TC-FM-070: File.source_type 知识库源标记

**前置条件**：
- 通过数据集上传文件（自动标记 `source_type`）

**步骤**：
1. 通过数据集 API 上传文件：
   ```bash
   curl -X POST "${BASE_URL}/datasets/${kb_id}/documents" \
     -H "Authorization: ${AUTH_TOKEN}" \
     -F "file=@test.pdf"
   ```
2. 查询关联的 `file` 记录：
   ```sql
   SELECT f.id, f.source_type 
   FROM file f 
   JOIN file2document f2d ON f.id = f2d.file_id
   JOIN document d ON f2d.document_id = d.id
   WHERE d.kb_id = '${kb_id}';
   ```

**预期结果**：
- `source_type` 值为 `FileSource.KNOWLEDGEBASE`（"knowledgebase"）
- 删除此类文件时被跳过（需通过数据集 API 删除）

---

## 13. 测试总结

### 13.1 测试用例分布

| 分类 | 测试用例数 | 覆盖范围 |
|------|-----------|---------|
| 文件夹创建和管理 | 7 | 创建、嵌套、重复检测、虚拟类型、异常 |
| 文件上传 | 6 | 单文件、多文件、指定目录、异常 |
| 文件列表 | 5 | 根目录、子目录、搜索、排序分页、异常 |
| 文件移动 | 10 | 移动、批量、重命名、组合、循环检测、异常 |
| 文件删除 | 7 | 单文件、批量、递归、权限、知识库源、异常 |
| 文件链接到数据集 | 4 | 单文件、文件夹展开、异常 |
| 文件层级查询 | 5 | 父文件夹、祖先列表、权限、异常 |
| 文件版本和提交 | 13 | CRUD 提交、列出、详情、差异、树、版本历史 |
| 文件下载 | 3 | 正常下载、不存在、空文件 |
| 边界和异常 | 6 | 特殊字符、Unicode、并发、深层嵌套、跨租户 |
| GaussDB 空字符串兼容 | 4 | 默认值、查询、更新、知识库标记 |
| **合计** | **70** | |

### 13.2 关键验证点

1. **GaussDB 适配**：
   - `source_type` 字段使用 `EmptyStringCharField`，默认值为 `""`
   - A/ORA-compatible GaussDB 将业务空串物理保存为 NULL，Field 层负责查询改写和回读还原
   - 字段索引正确创建和使用

2. **数据完整性**：
   - `file2document` 关联表正确维护
   - 删除文件时级联删除关联记录
   - 提交链（`parent_id`）正确链接

3. **存储一致性**：
   - 对象存储中的文件位置与数据库 `location` 字段一致
   - 移动操作同时更新存储和数据库
   - 提交操作使用内容寻址存储（`.objects/<hash>`）

4. **权限控制**：
   - `check_file_team_permission` 验证租户权限
   - 跨租户访问被正确拦截
   - 知识库源文件需通过专用 API 管理

5. **API 行为一致性**：
   - 参数验证通过 Pydantic 模型执行
   - 错误响应格式统一
   - 分页、排序参数正确应用

### 13.3 执行建议

1. **优先级**：
   - TC-FM-001 ~ TC-FM-070 全部执行，不得按 P1/P2 跳过；并发与异常也是本批次必测

2. **测试数据准备**：
   - 使用独立测试租户，避免数据污染
   - 测试完成后清理创建的测试数据
   - 准备各种类型的测试文件（PDF、DOCX、TXT、图片等）

3. **GaussDB 特有验证**：
   - 对比 MySQL 对照组和 GaussDB 实验组的业务行为一致性
   - 重点关注空字符串字段的处理
   - 验证索引在 GaussDB 中的使用效率
