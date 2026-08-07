# ============================================================
# dataset-rag 应用镜像（多阶段构建）
#
# 目标运行环境：带 NVIDIA 显卡的 x86_64 Linux 主机。
# 默认使用 PyTorch CUDA 12.8 (cu128) 源；如确需 CPU 推理可覆盖：
#   docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu -t dataset-rag:latest .
#
# 说明：
# - builder 阶段不依赖 ghcr.io（国内网络拉取不稳定），使用 docker.io 的
#   python:3.12-slim + pip 安装 uv，docker.io 可配置镜像加速器。
# - 若 PyPI 下载慢，可传 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#
# 运行：导入服务 / 查询服务 共用一个镜像，通过 command 区分，
#       推荐直接使用 docker-compose.yml（含中间件编排与应用服务）。
# ============================================================

# ---------- 阶段 1：构建（安装依赖） ----------
FROM python:3.12-slim AS builder

# 编译工具链（部分依赖无 wheel 时兜底编译）+ SSL 证书（uv/依赖下载需要）
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（pip 安装，避免依赖 ghcr.io）
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} uv

WORKDIR /app

# 仅复制依赖清单，利用 Docker 层缓存，依赖未变时跳过重装
COPY pyproject.toml uv.lock ./

# torch 源切换：pyproject.toml 默认强制 NVIDIA cu128 源。
# GPU 构建（默认）保持 cu128；CPU 构建传入 --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu。
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
RUN sed -i "s|https://download.pytorch.org/whl/cu128|${TORCH_INDEX_URL}|g" pyproject.toml

# 安装全部依赖（不安装项目自身，应用代码通过 PYTHONPATH 提供）。
# 注意：必须 --no-frozen。uv.lock 中锁定的是 cu128 的 wheel URL 与 hash，
# 当 TORCH_INDEX_URL 被替换为 CPU 源时，lock 与 pyproject 不一致，
# --frozen 会直接失败；--no-frozen 会仅重新解析受影响的 torch 三件套。
RUN uv sync --no-dev --no-install-project --no-frozen

# ---------- 阶段 2：运行（精简镜像） ----------
FROM python:3.12-slim AS runtime

# torch / onnxruntime / FlagEmbedding 依赖 OpenMP 运行库
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# 复制虚拟环境与应用代码（不携带源码外的开发文件）
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY prompts ./prompts

# 挂载点：output=导入中间产物，models=本地模型目录（BGE-M3 / Reranker）
VOLUME ["/app/output", "/models"]

EXPOSE 8000 8011

# 默认启动导入服务；查询服务通过 command 覆盖：
#   docker run ... dataset-rag:latest \
#     uvicorn app.query_process.agent.api.query_service:app --host 0.0.0.0 --port 8011
CMD ["uvicorn", "app.import_process.api.file_import_service:app", "--host", "0.0.0.0", "--port", "8000"]
