# 🧠 SynapseBI — 企业级多模态知识库与智能决策中枢

> 打通企业内部异构数据（PDF报告、Excel表格、SQL数据库），实现自然语言的精准问答与BI图表生成。

---

## 📁 项目结构

```
SynapseBI/
├── src/
│   └── synapsebi/                 # 业务代码（src 布局）
│       ├── etl/                   # 数据接入与预处理管线
│       │   ├── pdf_parser.py      # PDF 解析（PyMuPDF / Unstructured / PaddleOCR）
│       │   ├── excel_processor.py # Excel 清洗与 Schema 提取
│       │   └── pipeline.py        # ETL 主流程编排
│       ├── retrieval/             # 混合检索引擎
│       │   ├── vector_store.py    # 向量数据库（Qdrant） + BGE 嵌入
│       │   ├── text_to_sql.py     # Text-to-SQL 引擎 + SQL 安全校验
│       │   ├── router.py          # 意图识别路由器
│       │   └── search_engine.py   # 混合检索引擎主控
│       └── frontend/              # 前端交互与安全隔离
│           ├── api.py             # FastAPI RESTful 后端
│           ├── app.py             # Streamlit 前端看板
│           └── chart_exporter.py  # 图表多格式导出
│
├── module_1_etl/                  # 兼容层：转发至 src/synapsebi/etl
├── module_2_retrieval/            # 兼容层：转发至 src/synapsebi/retrieval
├── module_3_frontend/             # 兼容层：转发至 src/synapsebi/frontend
│
├── configs/
│   └── config.yaml                # 全局配置
├── docker/
│   ├── Dockerfile                 # 容器镜像
│   └── docker-compose.yml         # 一键部署（Qdrant + Ollama + App）
├── docs/                          # 架构 / API / 部署文档
├── scripts/                       # 运维与数据入库脚本
├── tests/                         # 测试与测试数据
├── .github/workflows/ci.yml       # CI 流水线
├── pyproject.toml                 # 项目元数据与打包配置
├── Makefile                       # 常用任务入口
├── requirements.txt
└── README.md
```

> 业务代码位于 `src/synapsebi/`，顶层 `module_*` 为兼容层，
> 旧导入路径与启动命令无需任何改动。

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
# 或可编辑安装（推荐，支持 src 布局与开发工具）
# pip install -e ".[dev]"
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

编辑 `configs/config.yaml` 调整参数：

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

## 🤖 AI Development Prompt

This project was built with an AI-native development workflow. Below is the reusable
prompt (English version) for generating a project like this with an AI coding assistant.

### Master Prompt

````markdown
# Role
You are a senior enterprise AI application engineer, proficient in Python, RAG, vector
search, Text-to-SQL, and web engineering. Your working style is "understand first,
verify before delivering": read the current state, run existing code, then extend.
Every step must be executed and verified for real — never deliver "it should work"
conclusions.

# Project Goal
Build SynapseBI: an enterprise-grade multimodal knowledge base and intelligent decision
hub that connects heterogeneous internal data (PDF reports, Excel spreadsheets, SQLite
databases) to deliver natural-language Q&A and BI chart generation, with support for
locally deployed private LLMs so enterprise data never leaves the intranet.

# Functional Requirements
1. Data ingestion (ETL):
   - PDF parsing: support PyMuPDF (lightweight), Unstructured (layout analysis), and
     PaddleOCR (scanned documents) backends; output document chunks with page number,
     type, and confidence, plus extracted tables
   - Excel cleaning: automatic empty row/column removal, deduplication, column name
     normalization, type inference, date standardization, outlier detection (3-sigma);
     generate column profiles and Schema/DDL
   - Unified output: document chunks (for vector ingestion) + structured schemas
     (for Text-to-SQL) + metadata
2. Hybrid search engine:
   - Vector search: Qdrant + BGE Chinese embedding models (bge-large/small-zh-v1.5),
     with recursive text splitting
   - Text-to-SQL: LLM-generated read-only SQLite queries guarded by a security layer:
     SELECT/WITH only, no writes or comment-based injection, table whitelist, CTE alias
     recognition
   - Intent routing: rule-based keyword routing first (SQL / vector / hybrid / chitchat),
     optionally falling back to LLM on low confidence
   - Unified query entry: dispatch by intent, optionally generate natural-language
     answers with an LLM
3. Frontend & API:
   - Streamlit dashboard: smart query (chat-style), data dashboard (Plotly charts),
     ETL management, system status
   - FastAPI backend: /api/query, /api/etl/run, /api/schemas, /api/export/chart,
     /api/health; API key authentication and CORS whitelist
   - Chart export: PNG / SVG / HTML / Excel

# Tech Stack
Python 3.10+; FastAPI + Uvicorn + Pydantic; Streamlit; Qdrant (qdrant-client);
sentence-transformers (BGE); Ollama (qwen2.5); SQLite; pandas/numpy; PyMuPDF;
matplotlib/plotly; Docker Compose (Qdrant + Ollama + App).

# Engineering Requirements
- Use src layout: business code under src/synapsebi/{etl,retrieval,frontend}
- Keep module_1_etl / module_2_retrieval / module_3_frontend compatibility layers that
  forward to synapsebi.*, so legacy import paths and startup commands stay unchanged
- Provide pyproject.toml (PEP 621), requirements.txt, Makefile, .env.example, CHANGELOG
- Docs: docs/architecture.md, docs/api.md, docs/deployment.md; README with structure
  tree and quick start
- CI: .github/workflows/ci.yml (install deps -> import smoke -> run tests)
- Tests: runnable verification scripts plus a test-data generator under tests/
- Config: configs/config.yaml (vector store, embedding, LLM, ETL, API parameters)
- Docker: Dockerfile + docker-compose.yml (Qdrant + Ollama + App, health checks,
  volumes, environment injection)

# Working Constraints
1. Read and report the existing repository structure before making any changes
2. After every change, verify by actually running things (tests, services, API calls)
   and report evidence
3. Fix issues directly with a clear root-cause explanation; do not work around them
4. Never trust model-generated output (e.g., LLM-generated SQL): always pass it through
   security validation
5. Keep git commits focused with clear messages (feat: / fix: / refactor:)
6. Deliver: feature list, verification results, known limitations, and startup
   instructions

# Acceptance Criteria (all must actually pass)
- ETL: process a dirty Excel file end-to-end (cleaning, profiling, Schema/DDL
  extraction) and export JSONL
- End-to-end: Qdrant collection create / ingest / semantic search; LLM-generated SQL
  queries execute safely against SQLite
- API: /api/health returns 200; /api/query answers both SQL-type and document-type
  questions
- Frontend: streamlit starts and can complete a natural-language query with rendered
  results
- Security: malicious SQL (DROP / UPDATE / comments) and unauthorized tables are
  blocked
````

### Stage Prompts (append sequentially)

````markdown
Stage 1 — Reconnaissance: Read the repository and produce a status report (directory
structure, module responsibilities, runnable entry points, dependencies, known issues),
then run the existing tests and show me the evidence.

Stage 2 — Restructure: Apply the engineering requirements above (src layout +
compatibility layers + scaffolding) without changing business logic. After
restructuring, verify that all legacy commands (streamlit / uvicorn) and import paths
still work.

Stage 3 — Local environment & E2E: Set up the local stack (Qdrant via Docker or a
native binary, Ollama with qwen2.5, download the BGE model), prepare test data, and run
the full pipeline: ETL -> ingestion -> SQL/vector queries -> FastAPI responses. Paste
the real output of each step.

Stage 4 — Fix & re-verify: Fix issues found along the way one by one and re-verify
(e.g., Excel boolean conversion, PDF text-block classification, SQLite cross-thread
usage, qdrant-client version compatibility, Text-to-SQL generation stability).

Stage 5 — Acceptance & delivery: Confirm each acceptance criterion, generate the README
structure tree and quick start, then commit the code following the commit conventions
(ask me about the target branch / repository before pushing if needed).
````

## 📝 License

MIT
