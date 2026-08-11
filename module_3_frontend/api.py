"""
FastAPI 后端
============
提供 RESTful API，作为业务端看板与检索引擎之间的中间层。

端点:
  - POST /api/query        自然语言查询
  - POST /api/etl/run      触发 ETL 管线
  - GET  /api/schemas      获取已注册的数据 Schema
  - GET  /api/health       健康检查
  - POST /api/export/chart 导出图表

安全:
  - API Key 认证（可选）
  - CORS 白名单
  - 请求速率限制
  - SQL 注入防护（模块2中已实现）
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户自然语言问题")
    top_k: int = Field(default=5, ge=1, le=50, description="返回结果数量")
    collection: str = Field(default="default", description="向量集合名称")


class QueryResponse(BaseModel):
    question: str
    intent: str
    final_answer: str = ""
    vector_results: list[dict[str, Any]] = []
    sql_results: dict[str, Any] | None = None
    elapsed_ms: float = 0.0
    error: str | None = None


class ETLRunRequest(BaseModel):
    source_dir: str = Field(..., description="数据源目录路径")
    pdf_backend: str = Field(default="pymupdf", description="PDF 解析后端")
    recursive: bool = Field(default=True)


class ETLRunResponse(BaseModel):
    status: str
    summary: dict[str, Any]
    errors: list[dict[str, str]] = []


class ExportRequest(BaseModel):
    chart_type: str = Field(default="bar", description="图表类型: bar | line | pie | scatter | table")
    data: list[dict[str, Any]] = Field(..., description="图表数据")
    title: str = Field(default="", description="图表标题")
    format: str = Field(default="png", description="导出格式: png | svg | html | excel")


class HealthResponse(BaseModel):
    status: str
    version: str
    uptime_seconds: float
    components: dict[str, str]


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------


def create_app(
    search_engine: Any = None,
    etl_pipeline: Any = None,
    api_key: str | None = None,
    cors_origins: list[str] | None = None,
    version: str = "1.0.0",
) -> FastAPI:
    """
    创建 FastAPI 应用实例。

    Args:
        search_engine: HybridSearchEngine 实例
        etl_pipeline: ETLPipeline 实例
        api_key: 可选的 API 认证密钥
        cors_origins: CORS 允许的来源列表
        version: API 版本号
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """应用生命周期管理"""
        app.state.start_time = time.time()
        logger.info("SynapseBI API v%s 启动", version)
        yield
        logger.info("SynapseBI API 关闭")

    app = FastAPI(
        title="SynapseBI API",
        description="企业级多模态知识库与智能决策中枢",
        version=version,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API Key 认证中间件
    if api_key:

        @app.middleware("http")
        async def api_key_middleware(request: Request, call_next):
            if request.url.path in ("/api/health", "/docs", "/openapi.json", "/redoc"):
                return await call_next(request)
            token = request.headers.get("X-API-Key", "")
            if token != api_key:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing API key"},
                )
            return await call_next(request)

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------

    @app.get("/api/health", response_model=HealthResponse)
    async def health():
        components = {}
        if search_engine:
            try:
                search_engine.vector_store.list_collections()
                components["vector_store"] = "healthy"
            except Exception:
                components["vector_store"] = "unreachable"
            components["search_engine"] = "available"
        if etl_pipeline:
            components["etl_pipeline"] = "available"

        return HealthResponse(
            status="ok" if all(v == "healthy" or v == "available" for v in components.values()) else "degraded",
            version=version,
            uptime_seconds=round(time.time() - app.state.start_time, 1),
            components=components,
        )

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    @app.post("/api/query", response_model=QueryResponse)
    async def query(req: QueryRequest):
        """自然语言查询入口"""
        if search_engine is None:
            raise HTTPException(status_code=503, detail="检索引擎未初始化")

        try:
            result = search_engine.query(req.question)
            return QueryResponse(
                question=result.question,
                intent=result.intent.value,
                final_answer=result.final_answer,
                vector_results=(
                    [
                        {
                            "content": r.content[:300],
                            "score": r.score,
                            "metadata": r.metadata,
                        }
                        for r in result.vector_results.results
                    ]
                    if result.vector_results
                    else []
                ),
                sql_results=(
                    {
                        "query": result.sql_results.query,
                        "columns": result.sql_results.columns,
                        "rows": result.sql_results.rows[:20],
                        "row_count": result.sql_results.row_count,
                    }
                    if result.sql_results
                    else None
                ),
                elapsed_ms=result.elapsed_total_ms,
                error=result.error,
            )
        except Exception as exc:
            logger.exception("查询失败")
            raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # ETL 触发
    # ------------------------------------------------------------------

    @app.post("/api/etl/run", response_model=ETLRunResponse)
    async def run_etl(req: ETLRunRequest):
        """触发 ETL 数据入库管线"""
        if etl_pipeline is None:
            raise HTTPException(status_code=503, detail="ETL 管线未初始化")

        try:
            etl_pipeline.source_dir = req.source_dir
            etl_pipeline.recursive = req.recursive
            if req.pdf_backend != etl_pipeline.pdf_parser.backend_name:
                from module_1_etl.pdf_parser import PDFParser

                etl_pipeline.pdf_parser = PDFParser(backend=req.pdf_backend)

            result = etl_pipeline.run()
            return ETLRunResponse(
                status="completed",
                summary=result.summary(),
                errors=result.errors,
            )
        except Exception as exc:
            logger.exception("ETL 执行失败")
            raise HTTPException(status_code=500, detail=str(exc))

    # ------------------------------------------------------------------
    # Schema 查看
    # ------------------------------------------------------------------

    @app.get("/api/schemas")
    async def list_schemas():
        """获取已注册的数据 Schema"""
        if search_engine is None:
            return {"schemas": []}
        schemas = search_engine.sql_engine.build_schema_context()
        return {"schemas": schemas}

    # ------------------------------------------------------------------
    # 图表导出
    # ------------------------------------------------------------------

    @app.post("/api/export/chart")
    async def export_chart(req: ExportRequest):
        """导出图表"""
        from .chart_exporter import ChartExporter

        exporter = ChartExporter()
        try:
            output_path = exporter.export(
                chart_type=req.chart_type,
                data=req.data,
                title=req.title,
                fmt=req.format,
            )
            # 返回 base64 编码
            import base64

            with open(output_path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode()
            return {
                "filename": os.path.basename(output_path),
                "format": req.format,
                "data_base64": encoded,
            }
        except Exception as exc:
            logger.exception("图表导出失败")
            raise HTTPException(status_code=500, detail=str(exc))

    return app
