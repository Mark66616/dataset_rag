# dataset-rag

面向产品文档的 RAG（检索增强生成）服务。项目提供文档离线入库和在线问答两套 LangGraph 工作流：离线侧将 PDF/Markdown 解析、切分、实体识别和混合向量写入 Milvus；在线侧结合产品名澄清、普通向量检索、HyDE 检索、MCP Web 搜索、RRF 融合和重排生成答案。

## 功能

- PDF 通过 MinerU 异步解析为 Markdown，也支持直接导入 Markdown。
- Markdown 图片可由视觉模型生成摘要，并上传至 MinIO 后替换为对象链接。
- 使用 BGE-M3 同时生成稠密/稀疏向量，支持 Milvus 混合检索。
- 按文档识别产品（实体）名称，查询时先进行实体确认或反问。
- 并行执行普通检索、HyDE 检索和 MCP Web 搜索；前两路经 RRF 融合，Web 结果在重排前合入。
- FastAPI 提供上传、任务状态、问答、SSE 流式响应及会话历史接口。

## 项目结构

```text
dataset-rag/
├── app/
│   ├── clients/                    # Milvus、MinIO、MongoDB、Neo4j 客户端
│   ├── conf/                       # .env 配置映射
│   ├── core/                       # 日志、Prompt 加载、项目路径
│   ├── import_process/             # 离线导入服务与 LangGraph
│   │   ├── api/file_import_service.py
│   │   └── agent/nodes/            # PDF 转 MD、图片处理、切分、向量化、入库
│   ├── query_process/              # 在线查询服务与 LangGraph
│   │   └── agent/nodes/            # 实体确认、检索、RRF、重排、答案生成
│   ├── lm/                         # LLM、BGE Embedding、Reranker 封装
│   └── tool/                       # BGE-M3 / Reranker 模型下载脚本
├── prompts/                        # 实体识别、HyDE、图片摘要、回答等 Prompt
├── tests/                          # 两条工作流的图执行示例
├── doc/                            # 示例文档
├── volumes/                        # Docker 中间件持久化数据（运行时生成）
├── docker-compose.yml              # Milvus、MinIO、MongoDB、Neo4j 编排
├── .env.example                    # 脱敏配置模板
└── pyproject.toml                  # Python 依赖与 uv 配置
```

## 工作流

### 离线导入流程

```mermaid
flowchart LR
    A[POST /upload] --> B[保存文件并创建后台任务]
    B --> C[node_entry]
    C -->|PDF| D[MinerU: PDF 转 Markdown]
    C -->|Markdown| E[node_md_img]
    D --> E
    E --> F[图片摘要 + MinIO 上传 + 链接替换]
    F --> G[按标题及长度切分文档]
    G --> H[LLM 识别产品名称]
    H --> I[写入产品名称及其混合向量]
    I --> J[BGE-M3 生成稠密和稀疏向量]
    J --> K[Milvus: 写入文档分块]
    K --> L[任务完成]
```

### 在线检索与回答流程

```mermaid
flowchart LR
    A[POST /query] --> B[读取 MongoDB 会话历史]
    B --> C[LLM 提取/改写产品名称]
    C --> D{产品名称可确认?}
    D -->|否或需澄清| E[直接输出反问/拒答]
    D -->|是| F1[普通混合向量检索]
    D -->|是| F2[HyDE 生成假设答案后检索]
    D -->|是| F3[MCP WebSearch]
    F1 --> G[RRF 融合（本地两路）]
    F2 --> G
    G --> H[BGE Reranker 重排]
    F3 --> H
    H --> I[LLM 基于上下文生成答案]
    I --> J[写入 MongoDB 历史 / 可选 SSE 推送]
```

## 快速开始

### 1. 准备环境

要求：Python 3.12+、Docker Compose，以及可用的 MinerU、OpenAI 兼容 LLM 和（可选）百炼 MCP WebSearch 服务。

```bash
cp .env.example .env
uv sync
```

Windows PowerShell 可使用：

```powershell
Copy-Item .env.example .env
uv sync
```

编辑 `.env`，至少填写以下密钥及服务地址：

- `OPENAI_API_KEY=your-openai-api-key`
- `MINERU_API_TOKEN=your-mineru-api-key`
- `NEO4J_PASSWORD=your-neo4j-password`
- `MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`
- 外部部署时，将 `your-neo4j-host`、`your-mongodb-host` 替换为实际主机名或 IP。

### 2. 启动中间件

根目录原先没有 `Dockerfile`，而是使用 Compose 管理基础设施。本仓库的 `docker-compose.yml` 已覆盖所有代码实际使用的中间件：Milvus（含 etcd/MinIO）、MongoDB 和 Neo4j。

```bash
docker compose up -d
docker compose ps
```

使用本地 Compose 时，`.env` 请使用：

```dotenv
MILVUS_URL=http://localhost:19530
MINIO_ENDPOINT=localhost:9000
MONGO_URL=mongodb://localhost:27017
NEO4J_URI=bolt://localhost:7687
```

服务端口：Milvus `19530`（健康检查 `9091`）、MinIO API `9002` / Console `9003`、MongoDB `27017`、Neo4j HTTP `7474` / Bolt `7687`。

如不使用 Docker，可分别安装并启动以下中间件：

| 中间件 | 用途 | 安装方式 |
| --- | --- | --- |
| Milvus Standalone | 稠密/稀疏向量与混合检索 | 使用 [Milvus Docker Compose](https://milvus.io/docs/install_standalone-docker.md)，同时启动 etcd 和 MinIO |
| MinIO | 解析图片对象存储 | 使用 [MinIO Server](https://min.io/docs/minio/linux/index.html) 启动服务，并创建 `MINIO_BUCKET_NAME` 指定的桶 |
| MongoDB | 对话历史 | 按 [MongoDB Community Server](https://www.mongodb.com/docs/manual/installation/) 安装，或使用 `docker run -d -p 27017:27017 mongo:8.0` |
| Neo4j | 图数据库连接能力 | 按 [Neo4j Community](https://neo4j.com/docs/operations-manual/current/installation/) 安装，或使用 `docker run -d -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/your-neo4j-password neo4j:5-community` |

> 当前导入主链路写入的是 Milvus；项目保留了 Neo4j 客户端，配置并启动它可供图能力扩展使用。

### 3. 准备本地模型（可选）

若使用本地 BGE 模型，请先设置 `.env` 中的 `MODELSCOPE_CACHE`，再执行：

```bash
uv run python -m app.tool.download_bgem3
uv run python -m app.tool.download_reranker
```

模型路径需与 `BGE_M3_PATH`、`BGE_RERANKER_LARGE` 相匹配；CPU 环境应保持 `BGE_DEVICE=cpu`、`BGE_FP16=0`。

### 4. 启动 API

导入服务和查询服务是两个独立进程：

```bash
uv run uvicorn app.import_process.api.file_import_service:app --host 0.0.0.0 --port 8000
uv run uvicorn app.query_process.agent.api.query_service:app --host 0.0.0.0 --port 8011
```

打开 `http://localhost:8000/import.html` 上传文件，打开 `http://localhost:8011/chat.html` 使用问答页面。

## API 概览

| 服务 | 方法与路径 | 说明 |
| --- | --- | --- |
| 导入 | `POST /upload` | `multipart/form-data` 字段为 `files`，批量上传并创建后台导入任务 |
| 导入 | `GET /status/{task_id}` | 查询单个文件的导入状态及已完成节点 |
| 查询 | `POST /query` | 请求体：`{"query":"…","session_id":"可选","is_stream":false}` |
| 查询 | `GET /stream/{session_id}` | 获取 `is_stream=true` 查询的 SSE 结果 |
| 查询 | `GET /history/{session_id}` | 查询会话历史 |
| 查询 | `DELETE /history/{session_id}` | 删除会话历史 |
| 查询 | `GET /health` | 查询服务健康检查 |

非流式查询示例：

```bash
curl -X POST http://localhost:8011/query \
  -H "Content-Type: application/json" \
  -d '{"query":"某产品如何配置？","is_stream":false}'
```

## 配置说明

`.env.example` 是从当前 `.env` 提炼的可提交模板：所有 API Key、访问密钥、数据库密码及原有 IP 均已脱敏。请勿提交真实 `.env`。

主要配置组如下：

- LLM/VLM：`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`LLM_DEFAULT_MODEL`、`VL_MODEL`。
- 向量与重排：`BGE_M3_*`、`BGE_RERANKER_*`、`EMBEDDING_DIM`。
- 数据服务：`MILVUS_*`、`MINIO_*`、`MONGO_*`、`NEO4J_*`。
- 外部解析与搜索：`MINERU_*`、`MCP_DASHSCOPE_*`。
- 运行行为：`MODELSCOPE_OFFLINE`、模型缓存目录、日志级别和保留天数。

## 落地清单与设计文档

- [RAG 项目能力审计与落地清单](RAG_项目能力审计.md)：当前能力盘点、问题优先级、题库对应关系、已决定方案与 TODO。
- [MongoDB 文档版本存储设计](DOCUMENT_VERSION_DESIGN.md)：多租户权限、文档版本、MinIO `object_key`、Milvus 同步、Celery 任务与死信队列的可落地设计。

## 开发与验证

```bash
uv run pytest
uv run python -m tests.test_main_graph
uv run python -m tests.test_query_main_graph
```

两份测试脚本会构建并执行对应 LangGraph；它们需要已配置的模型、外部 API 和中间件。

## 注意事项

- 当前 API 的 CORS 允许任意来源，生产环境应改为允许的前端域名。
- `docker-compose.yml` 的 MongoDB 未启用认证，仅适用于受控本地开发环境；生产环境请启用账户、密码、网络隔离与持久化备份。
- MinIO Compose 服务的容器内 API 端口为 `9000`，宿主机映射为 `9002`；应用若运行在宿主机，应将 `MINIO_ENDPOINT` 设为 `localhost:9002`。若应用也运行在同一 Compose 网络中，则使用 `minio:9000`。
