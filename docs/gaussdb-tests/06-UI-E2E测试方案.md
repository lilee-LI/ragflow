# 06 - GaussDB DocEngine API E2E 与必要 UI 测试方案

> **当前验收边界（2026-08-12）**：本方案表中的 passed/failed 状态及 09 报告均来自冻结执行基线。当前分支在该基线后又合入了 DocEngine 评审修复，尚未对当前 HEAD 统一复跑；因此这些状态只作历史证据，不能视为当前代码已验收。当前结果以本轮统一测试完成后的新证据为准。

## 一、范围与分层

本方案只验收本次 GaussDB DocEngine 改动的部署后全链路：

```text
真实 HTTP API
→ RAGFlow 服务
→ 异步解析 / 检索 / Text-to-SQL
→ GaussDB 持久化或查询
→ HTTP 响应
→ 删除 API 与残留检查
```

- **API E2E**：本文件主路径。不得 mock RAGFlow、GaussDB、解析任务、retriever 或 `use_sql`。
- **UI E2E**：当前通用管理前端对 GaussDB、OceanBase 等 DocEngine 均未提供状态卡或对应用户入口，因此不凭空建立 GaussDB 专属 UI TC；状态接口由 TC-E2E-1001 真实验收，不创建占位、xfail 或重复 API 用例。
- **Unit/Integration**：validator 拒绝、确定 SQL timeout、精确 fusion/pagerank score、非法 lookup row 等内部可控分支归属 02—05，不在 API E2E 重复枚举。
- 通用登录、密码修改、多语言、KB 复制、文档重命名、聊天历史/导出等交由 RAGFlow 原有 UI/API suite，不计入 GaussDB 专项。

当前生产代码在 dataset 创建时只写元数据库；GaussDB chunk/meta 表和索引在解析任务进入 `create_idx` 后初始化。因此本方案按**当前实现**在解析成功后检查对象，同时把技术设计“新建 KB 即创建对象”的表述记录为设计/实现差异，不以错误时点编写测试。

## 二、公共执行契约

### 2.1 环境

1. 按 `D:/RAGFlow/GaussDB_environment_access_guide.md` 第 8 节启动独立 E2E 环境。
2. `DOC_ENGINE=gaussdb`；base URL、管理员身份和 GaussDB 连接只从环境变量/guide 读取，本文不复制地址、账号、密码或完整连接串。
3. `gaussdb_read_conn` 必须设置 `default_transaction_read_only=on`，只允许查询本次 tenant 的 `ragflow_<tenant_id>`、`ragflow_doc_meta_<tenant_id>`、`information_schema` 和 `pg_indexes`。
4. 每条测试使用独立 `run_id = uuid.uuid4().hex`，不得依赖上一条测试的数据。
5. 固定资产必须提交到 `test/fixtures/gaussdb/`。全部文本采用 UTF-8（无 BOM）和 LF；下表的 `\n` 表示单字节 `0A`，sha256 在替换 `{{RUN_ID}}` 之前计算：

| 文件 | 精确字节定义 | 字节数 | sha256 |
| --- | --- | ---: | --- |
| `basic.txt` | 纯英文 Northstar 深圳冷链审计：日期、地点、负责人、Atlas-A17、4 C、原因与 `{{RUN_ID}}` | 379 | `d4a52faf5e561b2bda30efceb64008907f423a895e978c5ae7b89427be8e1ec2` |
| `basic_canary.txt` | 同领域纯英文旧记录：Helios-H22、8 C，明确非 Elena 的正式批准 | 270 | `e33a7ad9d0ca4e34fb971b149602e63fce830b9a75ff3c8a5ae7624f56b62298` |
| `batch_a.txt` | East Harbor 冷链连续性报告，含 alpha/`ALPHA_ONLY_<run_id>` 精确信号和完整因果 | 329 | `edae32b02edb4868fd661a8721bf21862d62b5a947ae8a37886c681ea9c8eade` |
| `batch_b.txt` | West Harbor 同领域报告，只含 beta 控制码并保留语义相近的冷链故障事实 | 357 | `93b58a9a6e677187ae1a9d80d40195b50d104c0b528034771b3e89d3f448d272` |
| `table.csv` | `Amount,Date,Description\n120,2026-01-01,office chair\n80,2026-01-02,monitor arm\n250,2026-01-03,standing desk\n300,2026-01-04,conference camera\n150,2026-01-05,keyboard\n` | 164 | `012a359737b999440116ba2db0684a072ce1f42438f0dd6af41024fc538715cf` |
| `multikb_a.csv` | KB_A 的日期、地点、负责人、金额和上游 Atlas 传感器更换原因 | 124 | `a637ab1058b09ab755524a8bf202742051a01fd1f8f16b5588993b8c2a3d0a95` |
| `multikb_b.csv` | KB_B 的日期、地点、负责人、金额和装卸控制器维修原因 | 124 | `8326093dd224b9f5b1c3c0c0060b393dc51d546649651c84419ce913dee5200d` |
| `mother.md` | Harbor 恢复计划 parent 与两条具有负责人、依赖、设备和限值关系的 child | 353 | `10d1750fe6d04332cb31469ad1a8d0629f73cdcb5c1691d8023937531cf85543` |
| `unicode_zh.txt` | 纯中文南岭冷链正式巡检：苍穹冷却泵、林岚、日期、上下游、每分钟48升及特殊字符 | 360 | `10ed662b88b18beb00de3955cb7f3a763cf43804cf2dac0f7167c9759afc6b14` |
| `unicode_zh_canary.txt` | 同领域纯中文旧记录：星河冷却泵、赵岳、每分钟42升 | 304 | `6b4427cc0c1431c64ef2ee97963b68b507060f70e5867274d0580c46d6d2a0a2` |
| `mixed_target.txt` | 星桥机器人 + EdgePilot X7 + SLA 99.95% + 38ms 的中英混合正式事实 | 372 | `806b548ca2edb51847257c3be5c085ff54012416cdcb37d8ffffd04c502d5fc9` |
| `mixed_zh_canary.txt` | 中文旧产线业务事实：星桥机器人、周宁、南京、每分钟12次和单链路原因，不含目标英文组合 | 318 | `55ef0b78c6bf331cd1eb4e5dd166c482a3227c35a55f88025d315da88d5d195c` |
| `mixed_en_canary.txt` | Helios Robotics/Bristol 业务事实，含 EdgePilot X7、99.50%/83ms 和单链路原因但缺少目标中文实体/数值 | 358 | `c8d410426a76aa4658535d5340acbec3d2fa0dd0a40aa1c3f93880f3538dacf4` |
| `large_12mb.txt` | 32,469 条按事件编号唯一的连贯运维记录；头/中/尾锚点分别描述 Atlas、Orion、Polar 恢复事实；末段只做确定性定长收口 | 12582912 | `d879526c0ede14a9252dfb98324eec83cc9b74f2a021c1955c2ab47d9b4cf39c` |
| `long_text.txt` | 2,711 条按时间连续且事件编号唯一的恢复纪要；含 `FIRST/MIDDLE/LAST_GAUSSDB_LONG_TOKEN` 三个事实锚点 | 1048576 | `1b9e5791d8af5c60e33f566dbe3e14ff6af8e800c0726404ffa483eb3fb46433` |

包含占位符的模板先验 sha256 必须与表格一致，再复制到 `tmp_path` 并把所有 `{{RUN_ID}}` 替换为本用例 run_id；原模板不得被修改。大文件、长文本和不含占位符的语言资产保持内容不变，唯一 run_id 写入 dataset/document 名；隔离性同时由 dataset id 和本次 metadata 验证。资产缺失、长度或 sha256 不符时测试直接失败。

### 2.2 HTTP helper

所有 JSON 请求统一使用当前测试模块签名：

```python
def _api_json(page, method, base_url, path, auth_header, data=None): ...

payload = _api_json(
    page,
    "POST",
    base_url,
    f"/api/v1/datasets/{dataset_id}/search",
    auth_header,
    {"question": query, "top_k": 10, "page": 1, "size": 5},
)
```

helper 必须断言 HTTP 2xx、JSON object、业务 `code == 0`，失败消息包含 method、path、HTTP status 和最多 2 KiB response body。无认证请求使用单独 helper，不得把 `None` 写入 Authorization header。

有效 route 固定为：

| 行为 | Method + route |
| --- | --- |
| 登录 | `POST /api/v1/auth/login` |
| dataset | `POST/GET/DELETE /api/v1/datasets` |
| 上传 | `POST /api/v1/datasets/{dataset_id}/documents` multipart |
| 文档更新 | `PATCH /api/v1/datasets/{dataset_id}/documents/{document_id}` |
| 文档启停 | `POST /api/v1/datasets/{dataset_id}/documents/batch-update-status` |
| 开始/停止解析 | `POST /api/v1/datasets/{dataset_id}/documents/parse`、`POST .../documents/stop` |
| chunk 列表/新增/删除 | `GET/POST/DELETE /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks` |
| chunk 编辑 | `PATCH /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}` |
| embedding 检查 | `POST /api/v1/datasets/{dataset_id}/embedding/check` |
| 单 dataset 检索 | `POST /api/v1/datasets/{dataset_id}/search` |
| 多 dataset 检索 | `POST /api/v1/datasets/search`，body 使用 `dataset_ids`、`page`、`size` |
| chat/session | `POST /api/v1/chats`、`POST /api/v1/chats/{chat_id}/sessions`；清理用 `DELETE /api/v1/chats/{chat_id}/sessions` 和 `DELETE /api/v1/chats/{chat_id}` |
| completion | `POST /api/v1/chat/completions`，固定 `stream=false` |
| feedback | `PUT /api/v1/chats/{chat_id}/sessions/{session_id}/messages/{msg_id}/feedback` |
| 状态 | `GET /api/v1/system/gaussdb/status`、`GET /api/v1/system/healthz` |

不存在 `/api/v1/search`、`/api/v1/completion` 或 `/api/v1/chunks/{chunk_id}/feedback` 的兼容写法。

### 2.3 异步轮询

```python
deadline = time.monotonic() + 300
history = []
while time.monotonic() < deadline:
    doc = find_document(...)
    history.append(doc)
    state = str(doc.get("run") or doc.get("status") or "").upper()
    if state in {"4", "FAIL", "FAILED"}:
        pytest.fail(f"parse failed: history={history!r}")
    if state in {"3", "DONE", "SUCCESS"} or float(doc.get("progress") or 0) >= 1:
        break
    time.sleep(2)
else:
    pytest.fail(f"parse timeout: history={history!r}")
```

示例中的 300 秒是普通解析条件轮询的防挂死默认值，不是产品性能 SLA。大文件和超长文本必须使用
`GAUSSDB_E2E_LONG_RUNNING_TIMEOUT_MS`；HTTP、聊天模型和 suite watchdog 也只用于防止环境无限
挂起，不得仅因超过某个固定时长判定产品失败。

必须先判断失败状态，再判断 progress；每条失败保留最后响应、poll history、dataset_id、document_id、run_id 和相关服务日志定位信息。

### 2.4 DB 与清理

- 表名只由已校验的 tenant id 派生；所有 kb/doc/chunk 值使用 `%s` 参数绑定。
- 每条创建数据的测试必须使用 `try/finally`。finally 调删除 API后，分别断言 chunk 表和 metadata 表对本次 kb_id 的行数为 0。
- dataset 删除不要求 drop tenant 共享表；必须断言表仍存在且其它 canary KB 数据不变。
- 单条回放命令固定为：

```bash
pytest -q test/playwright/e2e/test_gaussdb_docengine_e2e.py::<suggested_test_function> -s
```

## 三、详细用例

### TC-E2E-101 dataset 创建与解析后对象初始化

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_101_dataset_parse_initializes_gaussdb_objects`
- **Setup**：认证；记录 tenant 共享表是否已存在及本次 kb 行数；创建 naive dataset，上传 `basic.txt`。
- **Action**：创建 dataset 后先查 DB；再调用 parse 并轮询成功。
- **Assert**：创建后本次 kb 的 chunk/meta 行均为 0，不要求新表立即出现；解析成功后 chunk/meta 表存在，未设置 metadata 时允许没有该 doc 的 meta 行；从该 doc 的 valid 状态确定实际 embedding 维度，chunk 表含对应 `q_<dim>_vec/q_<dim>_vec_valid`，全文索引存在且恰有一个以 `q_<dim>_vec` 为目标列的 GSDiskANN 索引（索引名允许按 GaussDB 标识符上限确定性缩短），本次 kb 行数大于 0。
- **Cleanup**：删除 dataset；本次 kb 的两表残留均为 0；tenant 表存在性恢复到可解释状态。
- **Replay**：`pytest -q test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_101_dataset_parse_initializes_gaussdb_objects -s`

### TC-E2E-201 单文件上传、解析、持久化、检索

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_201_parse_persists_and_searches_fixed_document`
- **Setup**：独立 naive dataset；上传替换了 `{{RUN_ID}}` 的固定 `basic.txt`；取得 doc_id。
- **Action**：parse→poll→`POST /datasets/{id}/search`，payload 固定 `question="GaussDB E2E Shenzhen audit <run_id>"`、`top_k=10,page=1,size=5,similarity_threshold=0,vector_similarity_weight=0`。
- **Assert**：parse 响应 `success_count==1`；DB 至少一行且 content 含 run_id、valid 为 TRUE；HTTP chunks 至少一条属于 doc_id/kb_id，total 与返回数量关系正确。
- **Cleanup**：删除 dataset并断言两表残留 0。
- **Replay**：`...::test_tc_e2e_201_parse_persists_and_searches_fixed_document -s`

### TC-E2E-202 批量上传与数据隔离

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_202_batch_upload_parses_both_documents_independently`
- **Setup**：一个 dataset；同一 multipart 请求上传 `batch_a.txt/batch_b.txt`；A 只含精确控制词 `alpha`，B 只含精确控制词 `beta`，两份文件共享冷链语义但不得在说明文字中交叉出现对方控制词。
- **Action**：一次 parse 请求传两个 document_ids；分别轮询完成；分别用单一唯一词 alpha/beta 做纯全文搜索，并设置最小正 threshold 排除零分 hybrid 候选。
- **Assert**：两个 doc 均成功且 chunk_count>0；DB 两个 doc_id 都有行；alpha 结果只属于 A，beta 结果只属于 B；任何一个失败时输出两个 poll history。不得把同一 run_id 和通用词拼进查询后再假设两个文档全文得分互斥。
- **Cleanup**：删除 dataset，两个 doc 的 chunk/meta 均为 0。
- **Replay**：`...::test_tc_e2e_202_batch_upload_parses_both_documents_independently -s`

### TC-E2E-203 文档重解析替换旧 chunk

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_203_reparse_replaces_old_chunks_without_orphans`
- **Setup**：上传包含标题、批准事实、温度/校准因果和 run_id 归属四段业务事实的 `basic.txt`；PATCH 文档为 `{"chunk_method":"naive","parser_config":{"chunk_token_num":1,"delimiter":"\\n"}}`；首次解析完成并记录该 doc 的旧 chunk id 集合，断言至少两条。`chunk_token_num=1` 用于保证多段事实确定分开，不依赖 tokenizer 模型差异。
- **Action**：PATCH 同一 document_id 为 `{"parser_config":{"chunk_token_num":1,"delimiter":"@@@"}}`，再次 POST parse 并轮询完成；第二次 delimiter 不命中文本，确定生成与首轮不同的 chunk 集合。
- **Assert**：第二次解析后 API `chunk_count` 等于 DB 当前行数；DB 中旧 id 集合均不存在；当前行内容合并后同时含标题、`Elena Brooks`、`Atlas-A17`、`4 C`、`0.2 C` 校准原因和 run_id 归属；`(kb_id,id)` 无重复，检索结果只引用当前 id。PATCH 和 parse 任一步业务 code 非 0 时立即失败。
- **Cleanup**：删除 dataset并检查残留。
- **Replay**：`...::test_tc_e2e_203_reparse_replaces_old_chunks_without_orphans -s`

### TC-E2E-204 mother chunk 占位向量不可召回

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_204_mother_chunk_is_invalid_for_vector_retrieval`
- **Setup**：创建 naive dataset；按数据集设置入口 PUT `{"parser_config":{"chunk_token_num":512,"delimiter":"@@@","parent_child":{"use_parent_child":true,"children_delimiter":"\\n"}}}` 后上传 `mother.md`。数据集入口负责把正式嵌套配置转换为解析器字段；主 delimiter 不命中文本，children delimiter 把同一 parent 拆为多条 child；解析后要求至少一条 child 带 `mom_id`，否则测试失败。
- **Action**：解析完成；DB 读取 mother/child 标识；执行 `vector_similarity_weight=1` 检索。
- **Assert**：mother 行 `q_<dim>_vec_valid=FALSE`，child valid=TRUE；向量候选只由 valid child 产生，随后通用 `retrieval_by_children` 按产品行为折叠为 mother 供用户查看；API 响应 id 属于对应 mother、带实际相似度，且不暴露任何 `q_<dim>_vec` 或 valid 内部列。
- **Cleanup**：删除 dataset并检查残留。
- **Replay**：`...::test_tc_e2e_204_mother_chunk_is_invalid_for_vector_retrieval -s`

### TC-E2E-205 embedding 检查跳过 invalid mother

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_205_embedding_check_skips_invalid_mother_chunk`
- **Setup**：按 TC-E2E-204 创建同一类 parent/child dataset；读取 DB 得到 mother/child id 与实际 valid 列；从真实账号模型配置读取当前 `embd_id`。
- **Action**：`POST /datasets/{id}/embedding/check`，`check_num` 等于实际总行数，使全部 mother/child 都进入真实抽样与重新 embedding。
- **Assert**：invalid mother 不进入抽样结果，全部 valid child 均进入且含实际 `vector_dim/cos_sim`；summary.sampled/valid 均等于 child 数；检查前后 DB valid 映射完全不变。
- **Cleanup**：删除 dataset并检查 chunk/meta 残留为 0。
- **Replay**：`...::test_tc_e2e_205_embedding_check_skips_invalid_mother_chunk -s`

### TC-E2E-206 手工 chunk 新增、编辑、启停、字段回读与删除

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_206_manual_chunk_crud_round_trips_persisted_fields`
- **Setup**：创建并解析一个 naive 文档；通过真实 chunk POST 新增两条独立内容，记录 API chunk_count 和 DB 行。
- **Action**：PATCH 第一条的 content、tag 和 `positions=[[1,0,0,20,30]]`，第二条设为 `positions=[[1,0,0,10,20]]`；读取 chunk 列表；依次禁用/启用第一条并搜索；最后 DELETE 两条。positions 每项固定为 `[page,left,right,top,bottom]` 五元组，与公开 API 校验契约一致。
- **Assert**：编辑后的 content/tag/position 与 DB 一致，列表 API 按 chunk id 回读两条 positions 且值与 PATCH 一致；两条手工 chunk 在当前真实维度的 `vec_valid=TRUE`，重新启用后纯向量检索命中更新后的同一 chunk；不要求 positions 改变无关键词列表顺序，因为通用 RAGFlow 的公开列表排序按 `chunk_order/page/top/create_time`，不存在 position 五元组排序契约；旧词不可检索、新词可检索；禁用时 DB `available_int=0` 且不召回，启用后同 id 恢复；删除后 API/DB 都无两条且 document chunk_count 等于 DB 当前行数。
- **Cleanup**：删除 dataset并检查残留。
- **Replay**：`...::test_tc_e2e_206_manual_chunk_crud_round_trips_persisted_fields -s`

### TC-E2E-207 文档禁用与重新启用

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_207_document_disable_and_reenable_preserves_chunks`
- **Setup**：创建 naive dataset，上传并解析 `basic.txt`；记录 doc 的全部 chunk id 和 DB `available_int`。
- **Action**：通过 batch-update-status 把文档设为 0，再设回 1；每一步执行真实搜索和文档列表读取。
- **Assert**：禁用后 API status=0、全部 DB 行 `available_int=0`、检索不命中；重新启用后 status=1、全部行恢复为 1、同一组 chunk id 重新可检索，无删除重建或重复行。
- **Cleanup**：删除 dataset并检查残留。
- **Replay**：`...::test_tc_e2e_207_document_disable_and_reenable_preserves_chunks -s`

### TC-E2E-401 全文检索

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_401_fulltext_search_returns_scoped_document`
- **Setup**：两个固定文档，只有一个含唯一全文词；均解析成功。
- **Action**：dataset search，使用单一唯一词、`vector_similarity_weight=0` 和最小正 threshold。
- **Assert**：结果包含目标 doc、不含零分 canary；所有 chunks 的 kb_id 等于当前 dataset；DB 行和 HTTP doc_id 可对应。
- **Cleanup**：删除 dataset。
- **Replay**：`...::test_tc_e2e_401_fulltext_search_returns_scoped_document -s`

### TC-E2E-402 向量检索

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_402_vector_search_returns_only_valid_vectors`
- **Setup**：固定语义相近/不相近文档及有效 embedding provider。
- **Action**：dataset search，`vector_similarity_weight=1`、threshold=0、top_k=10。
- **Assert**：至少一个目标 doc；所有返回 chunk 在 DB 中 `q_<dim>_vec_valid=TRUE`；响应不暴露内部 vector 列。
- **Cleanup**：删除 dataset。
- **Replay**：`...::test_tc_e2e_402_vector_search_returns_only_valid_vectors -s`

### TC-E2E-403 hybrid 用户权重进入结果

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_403_hybrid_weight_changes_scores_without_cross_kb_results`
- **Setup**：固定两个有业务关系但检索信号不同的文档：A 含 query 的精确全文词，B 描述同一业务问题但不含该精确词；embedding provider/version 固定并记录。
- **Action**：同一 query 分别以权重 0、0.5、1 请求。
- **Assert**：三次 HTTP 成功且两个业务文档都进入当前候选窗口；读取公开响应字段 `similarity`，相同 chunk 的分数至少一组发生变化；所有 kb_id 正确；不要求跨模型环境固定绝对 score 或固定排序。精确 SQL 权重已由 04 unit/integration 负责。
- **Cleanup**：删除 dataset。
- **Replay**：`...::test_tc_e2e_403_hybrid_weight_changes_scores_without_cross_kb_results -s`

### TC-E2E-404 threshold、top_k、分页

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_404_threshold_topk_and_pagination_are_consistent`
- **Setup**：解析 `basic.txt` 后，通过真实 chunk POST 新增 12 条都含 `PAGINATION_TOKEN_<run_id>` 的小型确定数据；不复用 1 MiB 性能资产，避免把分页功能验收变成 embedding 吞吐测试。
- **Action**：page1/page2/page3 均使用 size=5、top_k=12，并用最小正 threshold 排除不含分页 token 的解析原始 chunk；阈值比较另用覆盖当前全部 chunk 的相同 top_k/size 窗口，再提高 threshold。
- **Assert**：page1/page2/page3 分别返回 5/5/2 条，三页 chunk_id 两两不重复且并集精确等于 12 条手工 chunk；公共检索服务把 `total` 定义为当前响应 chunk 数，不把它误作全量命中数；在未被 top_k 截断的同一候选窗口内，高 threshold 结果是低 threshold 结果子集；非法 top_k=0 返回确定业务错误且 DB 不变。
- **Cleanup**：删除 dataset。
- **Replay**：`...::test_tc_e2e_404_threshold_topk_and_pagination_are_consistent -s`

### TC-E2E-405 doc_ids 省略、空列表与显式范围

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_405_doc_ids_omitted_empty_and_explicit_scope_are_consistent`
- **Setup**：同一 dataset 上传并解析 `batch_a/b.txt`，DB 先验为两个 doc_id。
- **Action**：同一全文问题分别省略 `doc_ids`、传 `doc_ids=[]`、传 `doc_ids=[doc_a]`。
- **Assert**：前两次均成功且返回相同两个文档、total 相同；显式列表只返回 doc_a；所有结果 kb_id 正确。默认空列表是正式 API 契约，不得被 adapter 翻译成非法空 `IN`。
- **Cleanup**：删除 dataset并检查残留。
- **Replay**：`...::test_tc_e2e_405_doc_ids_omitted_empty_and_explicit_scope_are_consistent -s`

### TC-E2E-406 纯中文 ngram 召回、高亮、回答与引用

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_406_utf8_chinese_fulltext_search_and_highlight`
- **Setup**：上传自然中文业务事实 `unicode_zh.txt` 与同领域、近义但数值不同的 `unicode_zh_canary.txt`，真实解析；两份文档仅使用中文、数字和标点，目标资产同时含日期、人物、地点、上下游关系以及单引号、`%`、`_`、反斜杠。为两文档写入本次语言/角色 metadata，并创建真实 chat/session。
- **Action**：先通过数据集检索入口以 `vector_similarity_weight=0`、最小正 threshold 搜索目标多字词“苍穹冷却泵”，真实经过中文 ngram；再通过文档 chunk 列表 `keywords` 获取高亮；最后用纯中文问题询问目标日期、负责人、设备和流量。
- **Assert**：数据集检索只返回中文目标 doc，chunk id、kb_id、文档名与 DB 一致；chunk 搜索含中文 `<em>` 高亮；用户可见 answer 含“苍穹冷却泵”和“每分钟48升”，不含 canary 的“星河冷却泵/每分钟42升”；chat 候选 `reference.chunks` 必须包含目标 doc，answer 的 `[ID:n]` 必须解析到目标 doc，全部候选的 `document_id/dataset_id/document_name/chunk id` 均须回查到当前 KB 与 DB；DB 原文、meta 和特殊字符完整。数据集检索接口不声明高亮契约，不要求暴露 ngram SQL 或索引内部名。
- **Cleanup**：删除 dataset并检查残留。
- **Replay**：`...::test_tc_e2e_406_utf8_chinese_fulltext_search_and_highlight -s`

### TC-E2E-407 普通多 dataset 检索

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_407_multidataset_search_returns_only_requested_kbs`
- **Setup**：创建 A/B/canary 三个 naive datasets，各解析真实文档并新增一条含同一唯一 token 的 chunk；建立 doc_id→kb_id DB 先验。
- **Action**：`POST /datasets/search` 只传 A/B；再传 A/B 加 `doc_ids=[doc_a]`。
- **Assert**：第一次同时返回 A/B 的目标 chunk，doc_id→kb_id 精确匹配且不出现 canary；第二次只返回 doc_a；三个 KB 的 DB 行互相隔离。
- **Cleanup**：删除三个 datasets并逐一检查残留。
- **Replay**：`...::test_tc_e2e_407_multidataset_search_returns_only_requested_kbs -s`

### TC-E2E-408 中英混合 ngram/simple 组合检索、回答与引用

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_408_mixed_language_search_and_chat_require_combined_terms`
- **Setup**：同一 naive dataset 上传 `mixed_target.txt`、只满足中文侧条件的 `mixed_zh_canary.txt`、只满足英文产品侧条件但数值不同的 `mixed_en_canary.txt`；三个文档各自具备机构、人物、时间、地点、数值和因果关系，不用“缺少某字段”的测试说明冒充业务内容；解析后写入 language/role metadata，创建真实 chat/session。
- **Action**：以包含中文实体、英文产品/缩写和数字的查询“星桥机器人 EdgePilot X7 SLA 99.95% 38ms”执行全文检索，再用同样需要组合条件的问题发起非流式 completion。
- **Assert**：全文检索的 ngram/simple 条件组合只命中 target，不命中两个仅满足一半条件的 canary；用户可见 answer 同时含“星桥机器人”“EdgePilot X7”“99.95%”“38ms”，并说明采用该阈值是因为上游视觉网关完成双链路切换，不含 canary 的“99.50%/83ms”；search reference 的 doc 集合精确为目标 doc；chat 候选 `reference.chunks` 必须包含目标 doc，answer 的 `[ID:n]` 必须解析到目标 doc，全部候选的 chunk/doc/kb/name 均须回查到当前 KB 与 DB；三份 metadata 正确，清理后本次 dataset 的 chunk/meta 均为 0。不要求公开 SQL、query plan 或 ngram 索引名。
- **Cleanup**：finally 删除 session、chat、dataset并检查残留。
- **Replay**：`...::test_tc_e2e_408_mixed_language_search_and_chat_require_combined_terms -s`

### TC-E2E-501 table dataset Text-to-SQL

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_501_table_chat_returns_markdown_and_references`
- **Setup**：创建 `chunk_method="table"` 的 dataset；上传固定 `table.csv`，multipart parser_config 为 `{"table_column_mode":"auto"}`；解析成功后从 dataset 详情读取 KB parser_config，断言 GaussDB 生成的 field_map 精确为 `{"amount":"Amount","date":"Date","description":"Description"}`；不得从 document parser_config 读取，也不得把显示名误当内部 key；创建 chat：`POST /api/v1/chats`，body `{"name":"sql-<run_id>","dataset_ids":[dataset_id]}`，再 `POST /api/v1/chats/{chat_id}/sessions`。
- **Action**：`POST /api/v1/chat/completions`，body 为 `{"chat_id":chat_id,"session_id":session_id,"messages":[{"id":"q-<run_id>","role":"user","content":"列出 Amount 大于 200 的行"}],"stream":false}`。
- **Assert**：HTTP/业务 code 均成功；`data.answer` 为 Markdown table 且恰含金额 250、300，不含 120、80、150；`data.reference.chunks` 非空且每条公开字段 `document_id/document_name/dataset_id` 精确对应 DB 的 `doc_id/docnm_kwd/kb_id`；参数化 DB 查询 `chunk_data #>> '{Amount}'` 的符合行集合等于 `{250,300}` 并与 answer 对应。
- **Cleanup**：finally 依次 `DELETE /api/v1/chats/{chat_id}/sessions`，body `{"ids":[session_id]}`；`DELETE /api/v1/chats/{chat_id}`；删除 dataset；检查 chunk/meta 残留。
- **Replay**：`...::test_tc_e2e_501_table_chat_returns_markdown_and_references -s`

### TC-E2E-502 多 KB Text-to-SQL reference dataset_id

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_502_multikb_chat_references_have_correct_kb_ids`
- **Setup**：两个 table datasets，分别上传替换 run_id 后的 `multikb_a.csv/multikb_b.csv` 并解析；同一 chat 的 `dataset_ids=[kb1,kb2]`，创建 session；DB 先验 doc_id→kb_id 映射固定为 `{doc_a:kb1,doc_b:kb2}`。
- **Action**：非流式 completion 问 `列出 Source 和 Amount，必须同时包含 KB_A_<run_id> 与 KB_B_<run_id>`。
- **Assert**：`data.answer` 同时含 `KB_A_<run_id>/111` 与 `KB_B_<run_id>/222`；reference 同时含 doc_a/doc_b；每个公开 `document_id→dataset_id` 映射与 DB `doc_id→kb_id` 先验精确一致；不得出现空 dataset_id、第三个 dataset_id 或跨 tenant 数据。
- **Cleanup**：finally 删除 session、chat、两个 datasets，并逐一检查两表残留。
- **Replay**：`...::test_tc_e2e_502_multikb_chat_references_have_correct_kb_ids -s`

### TC-E2E-503 COUNT 聚合与 source 引用

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_503_count_query_returns_exact_answer_and_sources`
- **Setup**：用 `table.csv` 创建固定 header+5 数据行的 table dataset，解析成功并创建 chat/session。
- **Action**：非流式 completion 问“表格共有多少条数据行？不要把表头计入”。
- **Assert**：`data.answer` 明确回答 5 行；`data.reference.chunks` 与 `doc_aggs` 至少一类非空，并对实际非空结构逐项校验：chunks 的公开 `document_id/dataset_id` 对应本次 doc/dataset，doc_aggs 的 `doc_id` 等于本次 doc 且 `doc_name` 精确等于 `table.csv`；DB 同 kb 的非空 `chunk_data` 行计数精确为 5。source 补查失败但 answer 有效的内部分支由 05 component test 覆盖，不在此故障注入。
- **Cleanup**：finally 删除 session、chat、dataset并检查残留。
- **Replay**：`...::test_tc_e2e_503_count_query_returns_exact_answer_and_sources -s`

### TC-E2E-504 纯英文 RAG 检索、回答与引用

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_504_naive_chat_returns_grounded_answer_and_reference`
- **Setup**：创建 naive dataset，解析纯英文业务事实 `basic.txt` 与同领域、设备/温度不同的 `basic_canary.txt`，写入 language/role metadata，并创建真实 chat/session；不使用 table parser 或 `use_sql`。
- **Action**：先以纯英文唯一事实执行 dataset search，再通过非流式 `/chat/completions` 询问 Elena Brooks 在 2026-03-14 为 Shenzhen vaccine route 批准的 sensor model 与 temperature limit。
- **Assert**：用户可见 answer 含 `Atlas-A17` 与 `4 C`，不含 canary 的 `Helios-H22/8 C`；search reference 精确对应目标 DB doc/kb/name；chat 候选 `reference.chunks` 必须包含目标 doc，answer 的 `[ID:n]` 必须解析到目标 doc，全部候选的 `document_id/dataset_id/document_name/chunk id` 均须回查到当前 KB 与 DB；DB chunk 原文和两份 metadata 正确。只有真实检索→上下文→外部聊天模型→回答全链路完成才通过。
- **Cleanup**：finally 删除 session、chat、dataset并检查残留。
- **Replay**：`...::test_tc_e2e_504_naive_chat_returns_grounded_answer_and_reference -s`

### TC-E2E-701 metadata 编辑、合法空值与检索

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_701_metadata_update_and_filter_full_chain`
- **Setup**：从已校验 `basic.txt` 模板复制四个独立文件并解析；不 PATCH 的文档表示 missing，两个文档分别设 `{"state":""}`、`{"state":[]}`；最后一个先设 `{"state":"draft","old_key":"remove"}`，再完整替换为 `{"state":"active","category":"blue"}`。正式接口不接受 JSON null/空对象，因此这些内部状态仅留在 03。
- **Action**：`POST /api/v1/datasets/search`，使用 `dataset_ids` 和 `meta_data_filter`，依次验证 `=`、`empty`、`not empty`、AND/OR 代表场景。
- **Assert**：每次返回的 doc_id 集合精确；DB 最后一个文档只保留 `state/category`，旧 key 已删除，证明完整覆盖语义；结果无跨 KB doc。14 operator 的内部 SQL 矩阵由 03 unit/integration 完成，API E2E 保留 `=`、`contains`、`empty/not empty` 与 AND/OR 的代表全链路。
- **Cleanup**：删除 dataset；meta/chunk 残留为 0。
- **Replay**：`...::test_tc_e2e_701_metadata_update_and_filter_full_chain -s`

### TC-E2E-801 feedback 更新 pagerank

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_801_message_feedback_updates_scoped_pagerank_once`
- **Setup**：服务启动前固定 `CHUNK_FEEDBACK_ENABLED=true`、`CHUNK_FEEDBACK_WEIGHTING=uniform`，否则本 profile 失败；创建 chat/session并取得包含 reference 的 assistant msg_id；消息生成后在同 KB 新增一条确定不在 reference 中的 canary，并另建其它 KB canary；记录 reference、同 KB 非引用和其它 KB 三组 pagerank。
- **Action**：PUT feedback，body `{"thumbup":true}`；重复同一请求一次。
- **Assert**：首次 code=0，每个 reference 公开 `(dataset_id,id)` 映射到 DB `(kb_id,id)` 后的 pagerank 变为 `min(old+1,100)`；同 KB 非引用和其它 KB canary 均不变；重复同一 thumbup 后全部值不再变化。body `{"thumbup":"true"}` 返回业务 code 102、message 精确为 `thumbup must be a boolean`，DB 不变。
- **Cleanup**：finally 删除 session、chat、datasets并检查残留。
- **Replay**：`...::test_tc_e2e_801_message_feedback_updates_scoped_pagerank_once -s`

### TC-E2E-802 feedback 后 pagerank 改变用户检索排序

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_802_feedback_changes_user_visible_search_order`
- **Setup**：feedback profile 开启；同一 dataset 的两个文档各新增一条内容相同、问题关键词不同的手工 chunk；先用共同词记录两条的确定基线顺序，并把基线靠后的 chunk 作为目标。由于混合检索会合法返回同 KB 的向量候选，聊天前通过真实 chunk API 临时禁用除目标外的 chunks，聊天完成后立即恢复，建立确定的单目标 reference 前置条件。
- **Action**：用目标唯一问题完成真实 chat，确认只允许目标 chunk 进入 reference；恢复其它 chunks 后 thumbup，再次搜索共同词并重复同一 feedback。
- **Assert**：第一次只把目标 `(kb_id,id)` pagerank 加 1、canary 不变；目标在第二次用户检索中的名次严格前移；重复 feedback 不再加权。该 TC 验证最终排序，区别于 TC801 只验证值和幂等。
- **Cleanup**：删除 session、chat、dataset并检查残留。
- **Replay**：`...::test_tc_e2e_802_feedback_changes_user_visible_search_order -s`

### TC-E2E-901 删除文档后不可检索

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_901_delete_document_removes_chunks_metadata_and_search_hits`
- **Setup**：一个 dataset 两个文档，均解析成功。
- **Action**：DELETE 其中一个 document；再次全文/向量/hybrid/metadata-filter 请求。
- **Assert**：被删 doc 的 chunk/meta DB 行均为 0，四类响应都不含该 doc；metadata-filter 及普通搜索都正向返回未删除 doc，避免用空结果冒充删除成功。
- **Cleanup**：删除 dataset并检查剩余数据。
- **Replay**：`...::test_tc_e2e_901_delete_document_removes_chunks_metadata_and_search_hits -s`

### TC-E2E-902 删除 dataset 清理且不 drop tenant 表

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_902_delete_dataset_cleans_only_target_kb`
- **Setup**：同 tenant 两个 datasets，各有 canary chunk/meta。
- **Action**：DELETE dataset A。
- **Assert**：A 的 chunk/meta 为 0；B 行数、内容、metadata 不变且仍可真实检索；tenant chunk/meta 表仍存在；查询 A 返回无权限/不存在的确定业务错误。
- **Cleanup**：删除 B；最终两表对 A/B 均无残留。
- **Replay**：`...::test_tc_e2e_902_delete_dataset_cleans_only_target_kb -s`

### TC-E2E-904 解析取消清理部分写入

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_904_stop_parse_removes_partial_chunks`
- **Setup**：使用足够大的固定资产；开始解析并等待 RUNNING 或观察到首批 chunk。
- **Action**：POST `/datasets/{id}/documents/stop`，body `{"document_ids":[doc_id]}`；轮询 CANCEL。
- **Assert**：stop `success_count==1`；最终 chunk_num=0；该 doc 的 chunk DB 行为 0，metadata 不留下孤儿；再次 stop 按现有 API 契约幂等返回 `code=0,success_count=1`，CANCEL 状态和 DB 零残留不变。
- **Cleanup**：删除 dataset。
- **Replay**：`...::test_tc_e2e_904_stop_parse_removes_partial_chunks -s`

### TC-E2E-1001 status 认证、结构与脱敏

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_1001_gaussdb_status_is_authenticated_structured_and_masked`
- **Setup**：DOC_ENGINE=gaussdb，库连通。
- **Action**：先无 Authorization GET status，再带认证 GET status；另无认证 GET healthz。
- **Assert**：status 无认证为 401 且 data 为空；认证响应 `code=0,data.status="alive",data.message.health.status="healthy",data.message.performance.connection="connected"`；序列化 payload 不含 password、token、完整 URI/userinfo，也不含环境中的实际密码值；healthz 健康环境为 HTTP 200/status ok。故障态由 TC-E2E-1101 验证，不写不可观察的“探针未执行”断言。
- **Cleanup**：无业务数据。
- **Replay**：`...::test_tc_e2e_1001_gaussdb_status_is_authenticated_structured_and_masked -s`

### TC-E2E-1101 GaussDB 断连与恢复（隔离运维 profile）

**类型**：Ops API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_1101_gaussdb_disconnect_and_recovery`
- **Setup**：仅在 `GAUSSDB_FAULT_INJECTION=1` 的独占环境执行；在阻断前同时预检故障代理命令和恢复命令均非空，并确认命令指向本轮 RAGFlow 实际使用的代理容器；普通共享环境不运行且不计通过。
- **Action**：保持 RAGFlow HTTP 可达，阻断 RAGFlow→GaussDB；请求 status 和检索；恢复链路并轮询 status。
- **Assert**：断连时 status 为 timeout、检索连续两次返回相同的确定业务失败，响应既不匹配通用敏感模式也不包含当前实际 `GAUSSDB_PASSWORD`；恢复后 status alive、原数据可检索、DB 行数不变。
- **Cleanup**：finally 无条件恢复代理；验证 status alive 后删除 dataset。
- **Replay**：`...::test_tc_e2e_1101_gaussdb_disconnect_and_recovery -s`

### TC-E2E-1102 固定大文件

**类型**：API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_1102_fixed_large_file_preserves_data_integrity`
- **Setup**：校验 `large_12mb.txt` 长度/sha256；资产由按事件编号唯一的连贯运维记录组成，并在开头、中部、末尾设置三个互不重复的业务事实锚点；naive parser_config 固定为 `{"chunk_token_num":1024,"delimiter":"\\n\\n"}`；集中式和分布式均复用已通过健康检查的 GTX 1050 Ti CUDA session，embedding batch 默认为 8；只有出现 OOM、请求失败或进度不稳定的真实证据时才降为 4，并记录实际值、provider/model/dimension、RAGFlow 容器到 `tei` 的真实请求、RAGFlow/GaussDB 版本和机器信息。
- **Action**：upload→parse→条件轮询终态；长任务只使用 `GAUSSDB_E2E_LONG_RUNNING_TIMEOUT_MS` 作为防止无限挂起的环境看门狗，不把看门狗时长作为产品性能标准。预计或实际超过 30 分钟时，先核对 RAGFlow 实际 embedding batch、GPU 利用率和任务进度，再判断是否存在环境或测试配置问题。
- **Assert**：HTTP/业务成功；`1 <= chunk_count <= 20000` 且等于该 doc 的 DB 行数，本次 KB 的全部 DB 行集合也精确等于该 doc 行集合；无空 content 和重复 `(kb_id,id)`，当前实际维度的普通 chunk 全部 `vec_valid=TRUE`；分别检索头/中/尾锚点都只命中本 doc，公开 chunk/doc/kb/name 与 DB 对应，不存在的 canary 事实不命中；upload/parse 实际时长只写入 artifact，不参与通过/失败判定。任务内部候选数不是产品契约，不硬编码。
- **Cleanup**：若失败或超时时任务仍为 RUNNING，先 stop 并条件等待 CANCEL，再删除 dataset。
- **Replay**：`...::test_tc_e2e_1102_fixed_large_file_preserves_data_integrity -s`

### TC-E2E-1103 固定超长文本

**类型**：Performance API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_1103_fixed_long_text_has_bounded_chunk_count`
- **Setup**：校验 `long_text.txt` 长度/sha256；资产为按时间连续的恢复演练纪要，每段有唯一事件编号、责任人、依赖和结果，并在首/中/尾设置三个事实锚点；naive parser_config 固定为 `{"chunk_token_num":128,"delimiter":"\\n"}`；集中式和分布式均使用与 TC1102 相同的已验证 CUDA embedding profile，batch 默认为 8，只有出现 OOM、请求失败或进度不稳定的真实证据时才降为 4。
- **Action**：upload→parse→条件轮询终态→search 首/中/尾唯一事实与一个不存在的 canary；长任务只使用 `GAUSSDB_E2E_LONG_RUNNING_TIMEOUT_MS` 作为防止无限挂起的环境看门狗，不设产品耗时通过标准。
- **Assert**：`1 <= chunk_count <= 20000` 且等于 DB 行数；分别搜索 `FIRST_GAUSSDB_LONG_TOKEN`、`MIDDLE_GAUSSDB_LONG_TOKEN`、`LAST_GAUSSDB_LONG_TOKEN` 都返回本 doc，不存在的 canary 不返回；所有 DB 行属于本次 doc/kb，普通 child 行 `q_<dim>_vec_valid=TRUE`，无重复 `(kb_id,id)`。
- **Cleanup**：若失败或超时时任务仍为 RUNNING，先 stop 并条件等待 CANCEL，再删除 dataset。
- **Replay**：`...::test_tc_e2e_1103_fixed_long_text_has_bounded_chunk_count -s`

### TC-E2E-1104 两个 dataset 并发首次 parse 与写入隔离

**类型**：API E2E

**优先级**：P0
- **建议函数**：`test_tc_e2e_1104_concurrent_first_parses_isolate_two_datasets`
- **Setup**：同一真实 tenant 创建两个全新 datasets，各上传一个不同固定文档；创建两个独立 HTTP clients，不跨线程共享 Playwright page/context。
- **Action**：barrier 后同时 POST 两个 dataset 的首次 parse；分别条件轮询终态。
- **Assert**：两个请求均 HTTP 200/code=0/success_count=1；两个 doc 的 API chunk_count 分别等于 DB 行数，chunk 按 kb/doc 隔离且 id 集合不交叉，并分别证明两个 doc 当前实际维度的全部普通 chunk `vec_valid=TRUE`；未设置 metadata 时允许没有 meta 行；tenant 全文和当前维度向量索引各只有一个定义，索引名允许按 GaussDB 标识符上限确定性缩短；两个 dataset 的唯一词都可检索。固定 E2E 账号的 tenant 表会在前序场景后保留，因此本 TC 不声称重新制造 tenant 表首次初始化，只验证两个新 dataset 的首次 parse 并发写入与既有索引幂等。
- **Cleanup**：等待两个任务终止后删除两个 datasets；逐一检查残留。
- **Replay**：`...::test_tc_e2e_1104_concurrent_first_parses_isolate_two_datasets -s`

### TC-E2E-1105 服务重启后数据与索引可用

**类型**：Ops API E2E

**优先级**：P1
- **建议函数**：`test_tc_e2e_1105_service_restart_preserves_gaussdb_state`
- **Setup**：仅显式执行该 node，并要求 `GAUSSDB_E2E_RESTART_COMMAND` 与 `GAUSSDB_E2E_COMPOSE_DIR`；父进程必须显式提供 `DOC_ENGINE=gaussdb`、包含 `gaussdb,cpu,tei-*` 的 `COMPOSE_PROFILES`、六项非空 GaussDB 连接参数，以及与真实 TEI 容器一致的 `TEI_MODEL/TEI_HOST`；restart command 使用 `--no-deps` 严格只重建 `ragflow-cpu`；普通 profile 排除；记录 compose 工作目录、重启前 status、chunk ids、索引定义。
- **Action**：按环境指南重启 RAGFlow 主服务；每 5s 轮询 ping/status，使用 `GAUSSDB_E2E_RESTART_WATCHDOG_S` 防止环境无限挂起；重新认证并搜索。恢复耗时只记录，不作为产品通过标准。
- **Assert**：服务恢复、status alive；重启前 chunk ids/内容/按索引名稳定排序后的索引定义仍在；检索命中同一 doc；无重复行；固定 JSON artifact 记录重启前后 status、chunk ids、索引定义摘要和实际恢复耗时。
- **Cleanup**：finally 保证服务处于 healthy，再删除 dataset；失败保存 compose ps/logs。
- **Replay**：`...::test_tc_e2e_1105_service_restart_preserves_gaussdb_state -s`

## 四、用例汇总

本表用于保持正式 TC 与 pytest node 的一一映射。2026-07-28 双环境全量基线以及 2026-08-15
TC-E2E-802 修复后的双环境定向回归统计、逐 TC 耗时、失败归因、JUnit SHA-256 和覆盖率限制见
[09-GaussDB E2E 测试执行报告](09-GaussDB-E2E测试执行报告.md)。下表按每条 TC 的最后一次有效结果更新；
定向回归只替换对应 TC 的旧结果，不冒充当前 HEAD 的 32 条全量重跑。

| 用例 | 建议层级 | 优先级 | 最后一次有效执行状态 | 自动化对照 |
| --- | --- | --- | --- | --- |
| TC-E2E-101 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_101_dataset_parse_initializes_gaussdb_objects` |
| TC-E2E-201 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_201_parse_persists_and_searches_fixed_document` |
| TC-E2E-202 | API E2E | P1 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_202_batch_upload_parses_both_documents_independently` |
| TC-E2E-203 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_203_reparse_replaces_old_chunks_without_orphans` |
| TC-E2E-204 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_204_mother_chunk_is_invalid_for_vector_retrieval` |
| TC-E2E-205 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_205_embedding_check_skips_invalid_mother_chunk` |
| TC-E2E-206 | API E2E | P0 | Centralized + Distributed passed after contract correction (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_206_manual_chunk_crud_round_trips_persisted_fields` |
| TC-E2E-207 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_207_document_disable_and_reenable_preserves_chunks` |
| TC-E2E-401 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_401_fulltext_search_returns_scoped_document` |
| TC-E2E-402 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_402_vector_search_returns_only_valid_vectors` |
| TC-E2E-403 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_403_hybrid_weight_changes_scores_without_cross_kb_results` |
| TC-E2E-404 | API E2E | P1 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_404_threshold_topk_and_pagination_are_consistent` |
| TC-E2E-405 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_405_doc_ids_omitted_empty_and_explicit_scope_are_consistent` |
| TC-E2E-406 | API E2E | P0 | Centralized + Distributed passed after pure-Chinese ngram/answer hardening (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_406_utf8_chinese_fulltext_search_and_highlight` |
| TC-E2E-407 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_407_multidataset_search_returns_only_requested_kbs` |
| TC-E2E-408 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_408_mixed_language_search_and_chat_require_combined_terms` |
| TC-E2E-501 | API E2E | P0 | Centralized + Distributed reproduced generic BUG-E2E-003 | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_501_table_chat_returns_markdown_and_references` |
| TC-E2E-502 | API E2E | P0 | Centralized + Distributed reproduced generic BUG-E2E-003 | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_502_multikb_chat_references_have_correct_kb_ids` |
| TC-E2E-503 | API E2E | P1 | Centralized + Distributed reproduced generic BUG-E2E-003 | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_503_count_query_returns_exact_answer_and_sources` |
| TC-E2E-504 | API E2E | P0 | Centralized + Distributed passed after pure-English target/canary hardening (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_504_naive_chat_returns_grounded_answer_and_reference` |
| TC-E2E-701 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_701_metadata_update_and_filter_full_chain` |
| TC-E2E-801 | API E2E | P1 | Centralized + Distributed feedback profile passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_801_message_feedback_updates_scoped_pagerank_once` |
| TC-E2E-802 | API E2E | P1 | Centralized + Distributed passed after GaussDB pagerank default-weight fix (2026-08-15) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_802_feedback_changes_user_visible_search_order` |
| TC-E2E-901 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_901_delete_document_removes_chunks_metadata_and_search_hits` |
| TC-E2E-902 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_902_delete_dataset_cleans_only_target_kb` |
| TC-E2E-904 | API E2E | P1 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_904_stop_parse_removes_partial_chunks` |
| TC-E2E-1001 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_1001_gaussdb_status_is_authenticated_structured_and_masked` |
| TC-E2E-1101 | Ops API E2E | P1 | Centralized + Distributed exclusive fault-proxy profile passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_1101_gaussdb_disconnect_and_recovery` |
| TC-E2E-1102 | API E2E | P1 | Centralized + Distributed GTX 1050 Ti CUDA profile passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_1102_fixed_large_file_preserves_data_integrity` |
| TC-E2E-1103 | API E2E | P1 | Centralized + Distributed GTX 1050 Ti CUDA profile passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_1103_fixed_long_text_has_bounded_chunk_count` |
| TC-E2E-1104 | API E2E | P0 | Centralized + Distributed passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_1104_concurrent_first_parses_isolate_two_datasets` |
| TC-E2E-1105 | Ops API E2E | P1 | Centralized + Distributed restart profile passed (2026-07-25 / 2026-07-27) | `test/playwright/e2e/test_gaussdb_docengine_e2e.py::test_tc_e2e_1105_service_restart_preserves_gaussdb_state` |

**正文共 32 条：P0 22、P1 10、P2 0。** 本轮只新增 TC-E2E-408，因为纯英文回答链路和纯中文 ngram 链路分别已有 TC-E2E-504/406，可通过增强现有正式场景闭环；真正缺失的是中文实体、英文术语/缩写和数字必须共同满足的中英混合检索。没有把 01—05 的 SQL、validator、operator、维度或故障注入矩阵重复搬入 E2E。TC-E2E-1203 已删除：RAGFlow 对 GaussDB、OceanBase 等 DocEngine 均无状态卡用户入口，该场景不是现有 GaussDB 业务行为；后端状态接口已由 TC-E2E-1001 覆盖。

**2026-08-15 有效结果更新**：在生产修复提交 `bdc2253ae` 和 DocEngine E2E 提交
`d33a5fbdb` 对应代码树上，TC-E2E-802 已分别在集中式、分布式 feedback profile 定向通过。
与 2026-07-28 全量基线按 TC 取最后一次有效结果后，两套环境均为 29 passed、3 failed、
0 error、0 skip、0 xfail；双环境实例合计 58/64 passed。剩余 TC-E2E-501/502/503 均由
通用 `BUG-E2E-003` 阻断。本次只复跑 TC-E2E-802，不宣称当前 HEAD 已重新全量执行 64 个实例。

**历史记录（不作为本轮结果）**：2026-07-17 普通 profile 全量实跑收集并启动 31 项：22 passed、9 failed、0 error、0 skip、0 xfail，JUnit 为 `test/playwright/artifacts/e2e/gaussdb-final-full-31.xml`。9 项失败分别为：TC-E2E-501/502/503（通用 BUG-E2E-003）、TC-E2E-801/802（普通 profile 未启用 feedback；其中 TC-E2E-801 已在真实 feedback profile 通过，TC-E2E-802 仍由通用 BUG-E2E-004 阻断）、TC-E2E-1101/1105（普通 profile 未提供独占命令）以及 TC-E2E-1102/1103（当时的模型服务/测试看门狗阻断）。随后 TC-E2E-1101 在真实独占 TCP 代理 profile 通过；TC-E2E-1103/1105 和旧版重复内容 TC-E2E-1102 也分别在专项 profile 通过。旧资产、旧 node 数和旧通过率均不得替代本轮 32 条的双环境重新执行。

**历史记录（不作为本轮结果）**：2026-07-22 至 2026-07-23 曾在真实分布式环境执行旧版 31 个 node，按专项 profile 去重后为 26 passed、5 failed、0 error、0 skip、0 xfail。证据位于 `test/playwright/artifacts/e2e/distributed/d3ae5ce3/`；本轮修改了正式 TC、node、断言和固定资产，必须在集中式与分布式各自重新执行，不能继承该结果。

覆盖率与场景通过率分开统计。冻结单元 coverage 见 07，冻结双环境集成 coverage 见 08，冻结 E2E
统计见 09；三者都不代表当前 HEAD。E2E 调用的容器内 RAGFlow 后端没有使用 `coverage.py` 插桩，因此 E2E 自身命中的
production statement/branch coverage 以及单元+集成+E2E 三层合并覆盖率均未采集、不能计算；
不得把客户端 pytest coverage、旧执行 HEAD 的增量结果或单元/集成两层值冒充 E2E coverage，
也不得据此宣称三层合并覆盖率 100%。
