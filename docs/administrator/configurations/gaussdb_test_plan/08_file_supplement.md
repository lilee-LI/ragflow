# File API 补充测试

## 概述
补充 File 级联删除、链接到 datasets、版本管理和存储层一致性验证。

本文件 19 个用例全部先执行 MySQL+Infinity 对照组，再执行 GaussDB+GaussDB 实验组；使用两组独立 API/metadata/DocEngine/MinIO logical namespace 和本批次唯一 fixture，不使用备份脚本或旧结果。业务写入只走 API，DB/DocEngine/对象存储只读取证。File 没有 permissions 字段，team ACL 只通过关联的 team Dataset + tenant 成员关系建立。link API 是后台调度，必须轮询关系完成。

## 级联删除测试

### TC-FILE-DEL-001: 删除文件夹递归删除子项
**前置条件**：
- 文件夹 A 包含文件 B 和子文件夹 C
- 子文件夹 C 包含文件 D

**步骤**：
```http
DELETE /api/v1/files
{
  "ids": ["<folder_a_id>"]
}
```

预期响应：HTTP 200 / `code=0`，`data.success_count=4`

数据库验证：
```sql
-- 所有相关记录应被删除
SELECT COUNT(*) FROM file WHERE id IN ('<folder_a_id>', '<file_b_id>', '<folder_c_id>', '<file_d_id>');
-- 预期：0

-- 如果有 file2document 关联，也应被删除
SELECT COUNT(*) FROM file2document WHERE file_id IN ('<file_b_id>', '<file_d_id>');
-- 预期：0
```

存储验证：
```bash
# 删除前保存 B/D 的实际 parent_id/location；删除后按有效 MinIO
# bucket/prefix 解析逐个确认 object 不存在
```

---

### TC-FILE-DEL-002: 删除文件同时删除关联文档
**前置条件**：
- 文件 F 通过 file2document 关联到文档 D
- 已保存 D 的 Task/DocEngine/object 地址；关联由公开 link API 建立并已轮询完成

**步骤**：
```http
DELETE /api/v1/files
{
  "ids": ["<file_f_id>"]
}
```

数据库验证：
```sql
SELECT COUNT(*) FROM file WHERE id = '<file_f_id>';
-- 预期：0

SELECT COUNT(*) FROM file2document WHERE file_id = '<file_f_id>';
-- 预期：0

SELECT COUNT(*) FROM document WHERE id = '<doc_d_id>';
-- 预期：0
```

同时按删除前保存的 ID 核对 Task、DocEngine chunk 和文档对象均已清理；不得在删除后用空子查询作为证据。

---

### TC-FILE-DEL-003: 删除文件夹同时删除存储 bucket
**前置条件**：文件夹下至少有一个 API 上传对象，已记录当前存储是固定共享 bucket 还是 per-folder bucket

**步骤**：
```http
DELETE /api/v1/files
{
  "ids": ["<folder_id>"]
}
```

存储验证：
```bash
# 固定 bucket 模式：folder logical prefix 下无对象，共享物理 bucket 仍存在
# per-bucket 模式：folder bucket 不存在
```

---

### TC-FILE-DEL-004: 删除 skill space 下的 skill 文件夹调用 Go 后端
**前置条件**：
- 存在 `source_type="skill_space"` 的空间文件夹
- 待删除对象是该空间文件夹下的 skill 子文件夹；当前 `delete_files()` 对顶层 `skill_space` 文件夹本身会直接跳过，不会调用 Go 后端

**步骤**：
```http
DELETE /api/v1/files
{
  "ids": ["<skill_folder_id>"]
}
```

预期行为：
1. Python 后端先调用当前组端口 `API_PORT+4` 的 Go backend 删除 skill index
2. 仅当 Go 返回 HTTP 200 / body `code=0` 后递归删除 metadata/object；Go 失败时 folder 删除必须中止，防止 orphan index

日志验证：
```bash
# 成功日志精确匹配 "Successfully deleted skill index"
# 失败分支匹配 "Aborting folder deletion due to index deletion failure"
```

当前 Python/Go 公开路由中没有把普通 File folder 标成 `source_type="skill_space"` 的入口，因此本用例是明确的字段/集成 fixture 例外：先通过 File API 创建专属空间 folder，再以最小 ORM 更新该 folder 的 source_type，skill 子目录仍通过 API 创建。用例后先用 ORM 把空间 source_type 恢复为业务空串（否则顶层删除会保护性跳过），再用 API 清理。若当前组没有独立 Go 服务，启动仅供本 case 的受控 HTTP stub 并分别验证成功/失败响应。

---

## Link to Datasets 测试

### TC-FILE-LINK-001: 链接文件到 dataset
**步骤**：
```http
POST /api/v1/files/link-to-datasets
{
  "file_ids": ["<file_id_1>", "<file_id_2>"],
  "kb_ids": ["<dataset_id>"]
}
```

预期响应：HTTP 200 / `code=0` 只表示已调度；随后轮询到下面关系恰好出现

数据库验证：
```sql
-- 应为每个文件创建文档记录
SELECT COUNT(*) FROM document 
WHERE kb_id = '<dataset_id>' AND source_type = 'local';
-- 预期：2

-- 应有 file2document 关联
SELECT COUNT(*) FROM file2document 
WHERE file_id IN ('<file_id_1>', '<file_id_2>');
-- 预期：2
```

---

### TC-FILE-LINK-002: 链接文件夹到 dataset（展开内容）
**前置条件**：文件夹包含多个文件

**步骤**：
```http
POST /api/v1/files/link-to-datasets
{
  "file_ids": ["<folder_id>"],
  "kb_ids": ["<dataset_id>"]
}
```

预期响应：HTTP 200 / `code=0`；先保存所有 innermost file ID，再逐个轮询关联

数据库验证：
```sql
-- 文件夹内的所有文件应被链接
SELECT COUNT(*) FROM document 
WHERE kb_id = '<dataset_id>' AND source_type = 'local';
-- 预期：文件夹内文件数量
```

---

### TC-FILE-LINK-003: 链接到多个 datasets
**步骤**：
```http
POST /api/v1/files/link-to-datasets
{
  "file_ids": ["<file_id>"],
  "kb_ids": ["<dataset_id_1>", "<dataset_id_2>"]
}
```

数据库验证：
```sql
-- 应在两个 dataset 中都创建文档
SELECT COUNT(*) FROM document 
WHERE id IN (
  SELECT document_id FROM file2document WHERE file_id = '<file_id>'
);
-- 预期：2
```

前置必须是该 file 没有旧关联；当前实现会先删除所有既有关系/Document，再严格按本次 `kb_ids` 重建，不能把它误解为在旧集合上追加。

---

### TC-FILE-LINK-004: 重复链接同一文件
**前置条件**：文件已链接到 dataset

**步骤**：再次链接同一文件到同一 dataset

预期响应：HTTP 200，JSON `code=0`，当前实现会先删除该文件已有的 `file2document` 和对应文档，再按请求的 `kb_ids` 重建关联；同一文件重复链接到同一 dataset 后最终只保留一条关联，但文档 ID 可能变化。

数据库验证：
```sql
SELECT COUNT(*) FROM file2document 
WHERE file_id = '<file_id>' 
  AND document_id IN (SELECT id FROM document WHERE kb_id = '<dataset_id>');
-- 预期：1（不重复）
```

---

## 版本管理测试

所有 happy path 使用 owner fixture；每个 read/write endpoint 都用无关系用户重放一次，安全预期 `code=108` 且无数据泄露/变更。当前 commit/version routes 只有登录校验，任一跨租户成功均记录 IDOR。

### TC-FILE-VER-001: 创建 commit
**步骤**：
```http
POST /api/v1/folders/<folder_id>/commits
{
  "message": "Initial version",
  "files": [
    {
      "file_id": "<file_id>",
      "file_name": "file.txt",
      "operation": "add",
      "content": "plain text content"
    }
  ]
}
```

预期响应：HTTP 200，返回 commit_id

权限验证：用无关系用户对同一 folder 重放，安全预期 `code=108` 且无新增 commit/object；当前 route 若成功则登记 IDOR。

数据库验证：
```sql
SELECT COUNT(*) FROM file_commit WHERE folder_id = '<folder_id>';
-- 预期：1

SELECT message FROM file_commit WHERE folder_id = '<folder_id>';
-- 预期："Initial version"

SELECT operation FROM file_commit_item WHERE commit_id = '<commit_id>' AND file_id = '<file_id>';
-- 预期："add"
```

---

### TC-FILE-VER-002: 列出文件版本
**步骤**：
```http
GET /api/v1/files/<file_id>/versions
```

预期响应：HTTP 200，返回版本列表

用无关系用户请求同一 file ID 必须拒绝且不得泄露 message/hash；当前 route 缺权限检查的成功响应判安全缺陷。

---

### TC-FILE-VER-003: 查看特定 commit
**步骤**：
```http
GET /api/v1/folders/<folder_id>/commits/<commit_id>
```

预期响应：HTTP 200，返回 commit 详情

---

### TC-FILE-VER-004: 比较两个 commit 的差异
**步骤**：
```http
GET /api/v1/folders/<folder_id>/commits/diff?from=<commit_id_1>&to=<commit_id_2>
```

预期响应：HTTP 200，返回差异列表

---

### TC-FILE-VER-005: 获取 commit 的文件树
**步骤**：
```http
GET /api/v1/folders/<folder_id>/commits/<commit_id>/tree
```

预期响应：HTTP 200，返回文件树结构

---

### TC-FILE-VER-006: 获取 commit 中特定文件的内容
**步骤**：
```http
GET /api/v1/folders/<folder_id>/commits/<commit_id>/files/<inner_file_id>/content
```

预期响应：HTTP 200，返回 `data.content` 字符串

---

## 存储一致性测试

### TC-FILE-STOR-001: 上传文件同时写入存储和数据库
**步骤**：
```http
POST /api/v1/files
Content-Type: multipart/form-data

file: [binary data]
parent_id: <folder_id>
```

数据库验证：
```sql
SELECT location FROM file WHERE id = '<new_file_id>';
-- 预期：存储路径
```

存储验证：
```bash
# MinIO 中应存在对应的 blob
# 使用 location 字段验证
```

---

### TC-FILE-STOR-002: 下载文件从存储读取
**步骤**：
```http
GET /api/v1/files/<file_id>
```

预期响应：HTTP 200，返回文件内容

验证：返回的内容应与上传时一致

---

### TC-FILE-STOR-003: 移动文件更新存储路径
**步骤**：
```http
POST /api/v1/files/move
{
  "src_file_ids": ["<file_id>"],
  "dest_file_id": "<new_folder_id>"
}
```

数据库验证：
```sql
SELECT parent_id, location FROM file WHERE id = '<file_id>';
-- 预期：parent_id 更新；location 文本在无冲突时可同名，但对象地址 (parent_id,location) 必须变化
```

存储验证：
```bash
# 旧 (parent_id,location) 不存在；新地址 bytes/hash 与上传时一致
```

---

## 权限测试

### TC-FILE-ACL-001: Team permission - 联合租户可访问
**前置条件**：
- User A 创建文件并通过 link API 关联到 `permission="team"` 的 Dataset
- User B 通过公开邀请/确认 API 加入 User A tenant，且关系已只读确认

**步骤**：
```http
GET /api/v1/files/<file_id>
Authorization: Bearer <user_b_token>
```

预期响应：HTTP 200

---

### TC-FILE-ACL-002: Me permission - 仅 owner 可访问
**前置条件**：User A 的文件未关联 Dataset，或只关联 `permission="me"` 的 Dataset；User B 与 A 无 tenant 关系

**步骤**：
```http
GET /api/v1/files/<file_id>
Authorization: Bearer <other_user_token>
```

预期响应：当前 REST 包装返回 HTTP 200，JSON `code` 非 0，错误信息为 "No authorization."；如果后续统一 HTTP 语义，可升级为 HTTP 403。
