# ============================================================
# SynapseBI Dockerfile
# ============================================================
# 多阶段构建：仅包含 Streamlit 前端 + FastAPI 后端
# 向量数据库 (Qdrant) 由 docker-compose 单独启动
# ============================================================

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- 系统依赖 ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# --- Python 依赖 ---
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# --- 源码 ---
COPY . .

# --- 默认暴露 Streamlit 端口 ---
EXPOSE 8501

# --- 健康检查 ---
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# --- 入口 ---
CMD ["streamlit", "run", "module_3_frontend/app.py", \
     "--server.address=0.0.0.0", \
     "--server.port=8501", \
     "--server.headless=true"]
