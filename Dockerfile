# CareerOS — Docker 多阶段构建
# Stage 1: npm build 前端
# Stage 2: pip install + 运行 uvicorn

# ── Stage 1: 前端构建 ──
FROM node:22-alpine AS frontend

WORKDIR /build
COPY app/frontend/package.json app/frontend/package-lock.json ./
RUN npm ci

COPY app/frontend/ ./
RUN npm run build

# ── Stage 2: 后端运行 ──
FROM python:3.12-slim AS backend

WORKDIR /app

# 系统依赖（Chroma 需要 sqlite3）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码
COPY app/backend/ ./app/backend/

# 前端构建产物
COPY --from=frontend /build/dist/ ./app/frontend/dist/

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV DEBUG=false

# 暴露端口
EXPOSE 3000

# 启动
CMD ["uvicorn", "app.backend.main:app", "--host", "0.0.0.0", "--port", "3000"]
