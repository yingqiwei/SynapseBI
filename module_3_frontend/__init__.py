"""
模块3：前端交互与安全隔离
==========================
提供 FastAPI 后端 API + Streamlit 前端看板 + 图表导出功能。
支持 Docker 容器化部署，兼容本地私有化大模型。
"""

from .api import create_app
from .chart_exporter import ChartExporter

__all__ = [
    "create_app",
    "ChartExporter",
]
