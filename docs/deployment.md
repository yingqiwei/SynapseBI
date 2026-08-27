# 部署说明

## Docker Compose（推荐）

```bash
docker compose -f docker/docker-compose.yml up -d
```

包含 Qdrant（向量库）、Ollama（可选本地大模型）、SynapseBI（Streamlit + FastAPI）。

## 本地开发

```bash
# 1. 安装依赖
pip install -r requirements.txt
# 或可编辑安装（src 布局）
pip install -e ".[dev]"

# 2. 启动向量数据库（原生二进制或 Docker）
# Qdrant 监听 6333/6334

# 3. 启动大模型服务（Ollama 等，需先 pull 模型）
# ollama pull qwen2.5:7b

# 4. 运行 ETL / 数据入库
python scripts/run_etl.py --source-dir ./data
python scripts/ingest_to_qdrant.py --source-dir ./data

# 5. 启动服务
streamlit run module_3_frontend/app.py          # 前端看板 :8501
uvicorn module_3_frontend.api:create_app --factory --host 0.0.0.0 --port 8000  # API :8000
```

## 环境变量

参考 [.env.example](../.env.example)：

- `QDRANT_HOST` / `QDRANT_PORT`：向量数据库地址
- `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_BASE`：大模型配置
- `DB_PATH`：SQLite 数据库路径
- `API_KEY`：FastAPI 认证密钥（留空关闭）
