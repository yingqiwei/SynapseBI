"""
模块2：混合检索引擎
====================
结合向量检索（非结构化数据）与 Text-to-SQL（结构化数据），
通过意图路由器将用户自然语言问题转发到最合适的检索通道。
"""

from .search_engine import HybridSearchEngine
from .vector_store import VectorStore
from .text_to_sql import TextToSQLEngine
from .router import IntentRouter

__all__ = [
    "HybridSearchEngine",
    "VectorStore",
    "TextToSQLEngine",
    "IntentRouter",
]
