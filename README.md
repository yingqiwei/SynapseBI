# 🧠 SynapseBI — 企业级多模态知识库与智能决策中枢

> 打通企业内部异构数据（PDF报告、Excel表格、SQL数据库），实现自然语言的精准问答与BI图表生成。

---

## 📁 项目结构

```
SynapseBI/
├── module_1_etl/                  # 模块1：数据接入与预处理管线
│   ├── __init__.py
│   ├── pdf_parser.py              # PDF 解析（PyMuPDF / Unstructured / PaddleOCR）
│   ├── excel_processor.py         # Excel 清洗与 Schema 提取
│   └── pipeline.py                # ETL 主流程编排
│
├── module_2_retrieval/            # 模块2：混合检索引擎
│   ├── __init__.py
│   ├── vector_store.py            # 向量数据库（Qdrant） + BGE 嵌入
│   ├── text_to_sql.py             # Text-to-SQL 引擎 + SQL 安全校验
│   ├── router.py                  # 意图识别路由器
│   └── search_engine.py           # 混合检索引擎主控
│
├── module_3_frontend/             # 模块3：前端交互与安全隔离
│   ├── __init__.py
│   ├── api.py                     # FastAPI RESTful 后端
│   ├── app.py                     # Streamlit 前端看板
│   └── chart_exporter.py          # 图表多格式导出
│
├── config/
│   └── config.yaml                # 全局配置
│
├── docker/
│   ├── Dockerfile                 # 容器镜像
│   └── docker-compose.yml         # 一键部署（Qdrant + Ollama + App）
│
├── requirements.txt
└── README.md
```

## 🚀 快速开始

### 1. 环境准备

```bash
# 克隆/进入项目
cd SynapseBI

# 创建虚拟环境
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 启动向量数据库

```bash
# 方式A: Docker（推荐）
docker run -d -p 6333:6333 -p 6334:6334 --name qdrant qdrant/qdrant

# 方式B: docker-compose 启动全部
docker-compose -f docker/docker-compose.yml up -d
```

### 3. 运行 ETL 管线

```python
from module_1_etl import ETLPipeline

pipeline = ETLPipeline(
    source_dir="./data",
    pdf_backend="pymupdf",   # 或 "unstructured" / "paddleocr"
)
result = pipeline.run()
print(result.summary())
```

### 4. 启动查询服务

```python
from module_2_retrieval.vector_store import BGEEmbedder, VectorStore
from module_2_retrieval.text_to_sql import LLMProvider, TextToSQLEngine
from module_2_retrieval.router import IntentRouter
from module_2_retrieval.search_engine import HybridSearchEngine

# 初始化组件
embedder = BGEEmbedder(model_name="BAAI/bge-large-zh-v1.5")
vector_store = VectorStore(embedder=embedder, host="localhost", port=6333)
vector_store.create_collection("enterprise_docs", dimension=1024)

llm = LLMProvider(provider="ollama", model="qwen2.5:7b")
sql_engine = TextToSQLEngine(llm=llm, db_path="enterprise.db")
router = IntentRouter(mode="rule")

engine = HybridSearchEngine(
    vector_store=vector_store,
    sql_engine=sql_engine,
    router=router,
    collection_name="enterprise_docs",
)

# 查询
result = engine.query("今年哪个部门的销售额最高？")
print(result.final_answer)
```

### 5. 启动前端看板

```bash
# Streamlit 前端
streamlit run module_3_frontend/app.py

# 或 FastAPI 后端
uvicorn module_3_frontend.api:create_app --factory --host 0.0.0.0 --port 8000
```

## ⚙️ 配置

编辑 `config/config.yaml` 调整参数：

- `vector_store.host` — 向量数据库地址
- `embedding.model_name` — BGE 嵌入模型
- `llm.provider` — LLM 后端（ollama / openai / anthropic）
- `etl.pdf_backend` — PDF 解析后端
- `api.api_key` — API 认证密钥

## 🐳 Docker 部署

```bash
# 完整启动（Qdrant + Ollama + App）
docker-compose -f docker/docker-compose.yml up -d

# 仅启动应用（需要外部 Qdrant 和 LLM）
docker build -t synapsebi -f docker/Dockerfile .
docker run -d -p 8501:8501 --name synapsebi synapsebi
```

## 🔒 安全特性

- **SQL 只读限制**：`SQLGuard` 拦截 INSERT/UPDATE/DELETE/DDL
- **表名白名单**：仅授权表可被查询
- **API Key 认证**：FastAPI 中间件拦截未认证请求
- **SQL 注入防护**：结合 LLM 生成 + 正则校验双重防护
- **Docker 隔离**：所有服务容器化，网络隔离

## 📊 模块交互流程

```
用户问题
  │
  ▼
module_3_frontend (Streamlit / FastAPI)
  │
  ▼
module_2_retrieval/search_engine.py
  ├─ router.py          → 意图识别
  ├─ vector_store.py    → 向量语义检索
  └─ text_to_sql.py     → 结构化 SQL 查询
  │
  ▼
module_1_etl/ (数据预处理)
  ├─ pdf_parser.py
  ├─ excel_processor.py
  └─ pipeline.py
```

## 📝 License

MIT
