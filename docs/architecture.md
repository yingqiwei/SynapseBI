# 架构说明

## 总体设计

SynapseBI 采用三层模块化架构，打通企业内部异构数据（PDF 报告、Excel 表格、SQL 数据库），
实现自然语言的精准问答与 BI 图表生成。

```
用户问题
   │
   ▼
synapsebi.frontend（Streamlit 看板 / FastAPI REST API）
   │
   ▼
synapsebi.retrieval（混合检索引擎）
   ├─ router        意图识别（规则 / LLM / 混合）
   ├─ vector_store  向量语义检索（Qdrant + BGE）
   └─ text_to_sql   结构化 SQL 查询（SQLite + 安全校验）
   │
   ▼
synapsebi.etl（数据接入与预处理）
   ├─ pdf_parser     PDF 解析（PyMuPDF / Unstructured / PaddleOCR）
   ├─ excel_processor Excel 清洗与 Schema 提取
   └─ pipeline       ETL 主流程编排
```

## 模块说明

| 模块 | 职责 |
| --- | --- |
| `synapsebi.etl` | 解析 PDF/Excel，输出文档块（向量入库）与结构化 Schema（Text-to-SQL） |
| `synapsebi.retrieval` | 意图路由 + 向量检索 + Text-to-SQL 安全执行，统一查询入口 |
| `synapsebi.frontend` | Streamlit 看板、FastAPI 后端、图表多格式导出 |

## 安全设计

- SQL 只读限制：`SQLGuard` 拦截 INSERT/UPDATE/DELETE/DDL
- 表名白名单：仅授权表可被查询（CTE 别名自动识别）
- API Key 认证：FastAPI 中间件拦截未认证请求
- Docker 隔离：所有服务容器化部署

## 兼容层说明

历史 `module_1_etl` / `module_2_retrieval` / `module_3_frontend` 路径由兼容层转发至
`synapsebi.*`，保证旧代码与启动命令（`streamlit run module_3_frontend/app.py` 等）无需改动。
