# MongoDB 文档版本存储设计

> 状态：已确认的可落地设计  
> 关联清单：[RAG 项目能力审计与落地清单](RAG_项目能力审计.md)

## 1. 目标与边界

本设计用于支持多租户知识库中的文档新建、更新上传、版本回滚、私有/公司可见性、历史图片访问和异步导入任务审计。

- MongoDB 保存文档、版本、资源、任务和会话元数据。
- MinIO 保存 PDF、DOCX、Markdown、图片等二进制对象。
- Milvus 保存可检索 chunk、产品名和向量。
- `document_id` 代表逻辑文档，内容更新时保持不变；`version` 代表该文档的一次具体上传。
- 不在 MongoDB 保存向量、完整 chunk 内容或 presigned URL。

## 2. 访问模型

JWT 必须至少包含：

```json
{
  "tenant_id": "company_a",
  "user_id": "user_001",
  "role": "member | admin"
}
```

文档字段：

```json
{
  "tenant_id": "company_a",
  "owner_user_id": "user_001",
  "visibility": "tenant | private"
}
```

`tenant | private` 仅表示枚举候选；实际入库的 `visibility` 必须是单一值 `tenant` 或 `private`。

规则：

- 同公司任意已认证用户可上传公司可见文档。
- 私有文档仅上传者可见。
- 同公司管理员可查看、删除成员私有文档。
- 文档可见性只能由文档所有者或公司管理员修改。
- 查询范围可为 `tenant`、`private`、`both`；服务端从 JWT 推导 `tenant_id/user_id`，前端不可传入或覆盖这两个字段。
- 管理员是否可查看成员私有会话历史需单独授权；默认不允许。

## 3. 集合与数据模型

```text
documents           逻辑文档、权限和当前生效版本
document_versions   每次上传产生的不可变内容版本
document_assets     原文件、解析 Markdown、图片等对象
ingestion_tasks     Celery 导入任务的持久化审计状态
chat_messages       会话历史和引用
```

### 3.1 `documents`

一条记录对应用户端看到的一份文档。

```json
{
  "_id": { "$oid": "..." },
  "document_id": "doc_0198f8f5-...",
  "tenant_id": "company_a",
  "kb_id": "kb_product_manual",

  "owner_user_id": "user_001",
  "visibility": "tenant",

  "display_name": "HAK180 使用说明书",
  "source_type": "pdf",

  "active_version": 2,
  "active_version_id": { "$oid": "..." },
  "latest_version": 2,
  "status": "active",
  "revision": 5,

  "created_at": { "$date": "2026-08-04T08:00:00Z" },
  "updated_at": { "$date": "2026-08-04T09:00:00Z" },
  "deleted_at": null,
  "deleted_by": null
}
```

| 字段 | 说明 |
| --- | --- |
| `document_id` | 服务端首次上传时生成的稳定 UUID，不由文件名或哈希推导。 |
| `active_version` | 当前可被默认查看和检索的版本。 |
| `latest_version` | 最近一次发起上传的版本，可处于解析或失败状态。 |
| `revision` | 文档元数据乐观锁，避免并发更新覆盖。 |
| `status` | 建议为 `active` 或 `deleted`；版本级状态由 `document_versions` 管理。 |

权限字段放在逻辑文档上：更新内容时新版本继承权限，修改可见性不需要逐个修改所有版本的 MongoDB 记录。

### 3.2 `document_versions`

每次“更新上传”新增一条版本记录；历史版本的内容不可覆盖。

```json
{
  "_id": { "$oid": "..." },
  "version_id": "ver_0198f8f5-...",
  "document_id": "doc_0198f8f5-...",
  "tenant_id": "company_a",
  "kb_id": "kb_product_manual",

  "version": 2,
  "status": "staging",

  "source_filename": "HAK180 使用说明书_v2.pdf",
  "source_content_type": "application/pdf",
  "source_size_bytes": 18302451,
  "source_sha256": "4b3f...",
  "source_asset_id": "asset_0198f8f5-...",
  "ingestion_task_id": "task_0198f8f5-...",

  "parser": {
    "name": "mineru",
    "version": "v4",
    "is_scanned_pdf": false
  },
  "statistics": {
    "page_count": 128,
    "chunk_count": 456,
    "image_count": 38
  },

  "created_by": "user_001",
  "created_at": { "$date": "2026-08-04T08:00:00Z" },
  "activated_at": null,
  "superseded_at": null,
  "error": null
}
```

状态机：

```text
queued → processing → staging → active → superseded
              └──────────────────→ failed
```

- `staging`：解析、图片上传、向量写入完成，但尚未向查询开放。
- `active`：当前生效版本。
- `superseded`：旧版本，默认不再检索；在保留期内可服务历史引用。
- `failed`：更新失败，旧 `active` 版本必须继续可用。

### 3.3 `document_assets`

此集合将 MongoDB 元数据和 MinIO 对象键关联起来，不保存签名 URL。

```json
{
  "_id": { "$oid": "..." },
  "asset_id": "asset_0198f8f5-...",
  "tenant_id": "company_a",
  "kb_id": "kb_product_manual",
  "document_id": "doc_0198f8f5-...",
  "version": 2,

  "kind": "image",
  "object_key": "company_a/kb_product_manual/doc_0198f8f5/v2/images/asset_0198f8f5.png",
  "content_type": "image/png",
  "size_bytes": 258100,
  "sha256": "c04c...",
  "original_filename": "page-12-image-1.png",

  "created_at": { "$date": "2026-08-04T08:10:00Z" },
  "deleted_at": null
}
```

`kind` 可取：`source`、`markdown`、`image`、`archive`。

对象键格式：

```text
{tenant_id}/{kb_id}/{document_id}/v{version}/{kind}/{asset_id}.{ext}
```

不要把 `visibility` 或 `owner_user_id` 编码进对象路径，因为权限会变更。对象访问必须先查 MongoDB 权限，再签发 URL。

### 3.4 `ingestion_tasks`

Redis + Celery 负责排队和执行；MongoDB 保存可追踪、可审计的任务状态。

```json
{
  "task_id": "task_0198f8f5-...",
  "celery_task_id": "celery-uuid",
  "tenant_id": "company_a",
  "kb_id": "kb_product_manual",
  "document_id": "doc_0198f8f5-...",
  "version": 2,

  "type": "document_ingestion",
  "status": "processing",
  "attempt": 1,
  "max_attempts": 2,
  "current_step": "embedding",

  "queued_at": { "$date": "2026-08-04T08:00:00Z" },
  "started_at": { "$date": "2026-08-04T08:01:00Z" },
  "finished_at": null,
  "error": null
}
```

最终失败时：

```text
ingestion_tasks.status = dead_letter
parse:dlq 写入失败事件
```

### 3.5 `chat_messages`

现有会话历史至少增加：

```json
{
  "tenant_id": "company_a",
  "user_id": "user_001",
  "session_id": "session_xxx",
  "role": "assistant",
  "text": "……",
  "citations": [
    {
      "document_id": "doc_123",
      "version": 2,
      "chunk_id": "123456",
      "asset_ids": ["asset_img_001"]
    }
  ]
}
```

历史消息、chunk 和答案都只保存 `asset_id/object_key`，不保存 presigned URL。读取历史时，服务端根据当前用户权限重新签发 20 分钟 URL。

## 4. MongoDB 索引

```javascript
db.documents.createIndex({ document_id: 1 }, { unique: true });
db.documents.createIndex({ tenant_id: 1, kb_id: 1, status: 1, updated_at: -1 });
db.documents.createIndex({ tenant_id: 1, kb_id: 1, owner_user_id: 1, visibility: 1 });

db.document_versions.createIndex({ document_id: 1, version: 1 }, { unique: true });
db.document_versions.createIndex({ tenant_id: 1, kb_id: 1, status: 1, created_at: -1 });
db.document_versions.createIndex({ tenant_id: 1, kb_id: 1, source_sha256: 1 });

db.document_assets.createIndex({ asset_id: 1 }, { unique: true });
db.document_assets.createIndex({ document_id: 1, version: 1, kind: 1 });
db.document_assets.createIndex({ object_key: 1 }, { unique: true });

db.ingestion_tasks.createIndex({ task_id: 1 }, { unique: true });
db.ingestion_tasks.createIndex({ tenant_id: 1, document_id: 1, version: 1 });

db.chat_messages.createIndex({ tenant_id: 1, user_id: 1, session_id: 1, ts: -1 });
```

索引只解决性能；权限必须由后端查询条件与 JWT 鉴权保证。

## 5. Milvus 字段与过滤

文档 chunk collection 和产品名 collection 都必须增加以下标量字段：

```text
tenant_id          VARCHAR
kb_id              VARCHAR
document_id        VARCHAR
document_version   INT64
owner_user_id      VARCHAR
visibility         VARCHAR
index_status       VARCHAR
```

产品名 collection 也必须有这些字段，否则产品名确认阶段会泄露其他租户或私有文档中的产品信息。

普通用户的 `both` 查询范围概念上为：

```text
tenant_id == <JWT tenant_id>
&& kb_id == <requested kb_id>
&& index_status == "active"
&& (
  visibility == "tenant"
  || (visibility == "private" && owner_user_id == <JWT user_id>)
)
```

所有字符串必须由服务端转义，身份字段只能来自已验证 JWT。

## 6. API 与工作流

### 6.1 新建文档

```text
POST /documents
```

1. 验证 JWT、租户和知识库。
2. 服务端生成 `document_id`、`version_id`、`asset_id`、`task_id`。
3. 写入 `documents(active_version=null, latest_version=1)`。
4. 写入 `document_versions(version=1, status=queued)` 与 `ingestion_tasks`。
5. 上传原文件到 MinIO。
6. 创建 `source` 类型 `document_assets`。
7. 投递 Celery，返回 `document_id/version/task_id`。

### 6.2 更新上传

```text
POST /documents/{document_id}/versions
```

请求带 `expected_revision`，避免并发更新覆盖。

1. 验证当前用户是文档所有者或同租户管理员。
2. 计算新文件 SHA-256；若与当前 active 版本相同，返回 `409 Content Unchanged`。
3. 在 MongoDB 事务或乐观锁中递增 `latest_version` 与 `revision`。
4. 创建新 `document_versions(version=n, status=queued)`。
5. 写入 `.../{document_id}/v{n}/source/...` 并投递 Celery。
6. 保留旧 `active` 版本，直到新版本验证完成。

文件名或哈希都不能用于自动推断“更新哪份逻辑文档”；用户端必须在已有文档详情页点击“更新上传”，明确传入 `document_id`。

## 7. 版本发布与补偿

Milvus、MongoDB、MinIO 不支持跨库事务，因此采用 Saga。

```text
v1 active
  ↓
v2 queued → processing → staging
  ↓ 成功
v2 Milvus active → Mongo active_version 指向 v2 → v1 superseded
```

具体步骤：

1. v2 的 chunk 和产品名写入 Milvus，初始 `index_status=staging`。
2. 校验 chunk 数、图片资源和 Milvus 插入数量。
3. 获得 `document_id` 级 Redis 锁。
4. 将 v2 的 Milvus 数据置为 `active`。
5. MongoDB 事务更新 `documents.active_version`，将 v2 设为 `active`、v1 设为 `superseded`。
6. 将 v1 Milvus 数据置为 `superseded`。
7. 完成任务。

失败时，v1 保持 active；v2 的 staging 数据清理或标记为 failed，任务进入重试或死信队列。

当前项目按 `item_name` 清理旧 chunk 的逻辑必须替换为按以下范围清理：

```text
tenant_id + kb_id + document_id + document_version
```

否则两个不同文档只要识别出同一产品名，就可能互相删除向量数据。

## 8. Celery 死信队列

已确定：Redis + Celery、Worker 并发 1、最多等待 50 个任务、超时 30 分钟、自动重试 1 次、队列满返回 `503 + Retry-After`。

Redis 没有 Celery 原生 DLQ，因此在第二次失败后由 `Task.on_failure/after_return` 写入 `parse:dlq`：

```json
{
  "task_id": "task_xxx",
  "tenant_id": "company_a",
  "document_id": "doc_123",
  "version": 2,
  "error": "MinerU timeout",
  "retry_count": 1,
  "failed_at": "2026-08-04T08:30:00Z"
}
```

管理员可查看失败原因、人工重新入队或关闭任务；死信任务不得无限自动重试。

队列容量应采用 Redis Lua 脚本或原子预留计数实现，不能只检查非原子的 `LLEN`。

## 9. 实施顺序

1. 创建 MongoDB collection、validator、索引和数据访问层。
2. 实现 JWT 上下文与统一的 MongoDB/Milvus 权限过滤器。
3. 拆分新建与更新版本 API，并实现文档乐观锁。
4. 改造图片处理：URL 改为 `asset_id/object_key`，历史接口按需重签名。
5. 改造 Milvus schema 与写入逻辑，按文档版本而非 `item_name` 清理。
6. 接入 Redis + Celery、有界队列、任务状态和 `parse:dlq`。
7. 为跨租户、私有文档、版本切换、失败回滚和历史图片重签名添加集成测试。
