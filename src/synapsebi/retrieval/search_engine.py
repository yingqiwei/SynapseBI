"""
混合检索引擎主控
================
整合向量检索、Text-to-SQL 与意图路由器，提供统一的查询入口。

执行流程:
  用户问题 → IntentRouter.route()
    ├─ SQL_QUERY    → TextToSQLEngine.query()
    ├─ VECTOR_SEARCH → VectorStore.search()
    ├─ HYBRID        → 两路并行 → 融合排序
    └─ CHITCHAT      → 礼貌回复
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from .router import Intent, IntentRouter, RouteDecision
from .text_to_sql import LLMProvider, SQLResult, TextToSQLEngine
from .vector_store import SearchResponse, VectorStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class HybridResult:
    """混合检索的统一返回"""

    question: str
    intent: Intent
    route_decision: RouteDecision

    # 向量检索结果
    vector_results: SearchResponse | None = None

    # SQL 查询结果
    sql_results: SQLResult | None = None

    # 融合后的最终答案（可选，由 LLM 生成自然语言回复）
    final_answer: str = ""

    # 元数据
    elapsed_total_ms: float = 0.0
    error: str | None = None

    @property
    def has_vector(self) -> bool:
        return self.vector_results is not None and len(self.vector_results.results) > 0

    @property
    def has_sql(self) -> bool:
        return (
            self.sql_results is not None
            and self.sql_results.error is None
            and len(self.sql_results.rows) > 0
        )

    def summary(self) -> dict[str, Any]:
        """生成可序列化的摘要"""
        return {
            "question": self.question,
            "intent": self.intent.value,
            "route_reason": self.route_decision.reason,
            "vector_hits": len(self.vector_results.results) if self.vector_results else 0,
            "sql_rows": self.sql_results.row_count if self.sql_results else 0,
            "sql_query": self.sql_results.query if self.sql_results else "",
            "elapsed_ms": round(self.elapsed_total_ms, 1),
            "final_answer": self.final_answer[:500] if self.final_answer else "",
        }


# ---------------------------------------------------------------------------
# 检索引擎
# ---------------------------------------------------------------------------


class HybridSearchEngine:
    """
    混合检索引擎 —— 统一查询入口。

    使用示例::

        engine = HybridSearchEngine(
            vector_store=vector_store,
            sql_engine=sql_engine,
            router=router,
            collection_name="enterprise_docs",
        )
        result = engine.query("今年哪个部门的销售额最高？")
        print(result.final_answer)
    """

    # 闲聊回复模板
    CHITCHAT_REPLIES = [
        "你好！我是 SynapseBI 智能助手，可以帮你查询企业数据和文档。请问有什么可以帮你的？",
        "很高兴为你服务！你可以问我任何关于企业数据和文档的问题。",
    ]

    def __init__(
        self,
        vector_store: VectorStore,
        sql_engine: TextToSQLEngine,
        router: IntentRouter,
        collection_name: str = "default",
        top_k: int = 5,
        generate_answer: bool = True,
        answer_llm: LLMProvider | None = None,
    ):
        self.vector_store = vector_store
        self.sql_engine = sql_engine
        self.router = router
        self.collection_name = collection_name
        self.top_k = top_k
        self.generate_answer = generate_answer
        self.answer_llm = answer_llm

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def query(self, question: str) -> HybridResult:
        """执行一次混合查询"""
        import time

        t_start = time.perf_counter()

        # 1. 路由
        decision = self.router.route(question)
        logger.info(
            "路由决策: intent=%s confidence=%.2f reason=%s",
            decision.intent.value,
            decision.confidence,
            decision.reason,
        )

        result = HybridResult(
            question=question,
            intent=decision.intent,
            route_decision=decision,
        )

        # 2. 按意图分发
        if decision.intent == Intent.SQL_QUERY:
            result.sql_results = self.sql_engine.query(question)

        elif decision.intent == Intent.VECTOR_SEARCH:
            result.vector_results = self.vector_store.search(
                query=question,
                collection_name=self.collection_name,
                top_k=self.top_k,
            )

        elif decision.intent == Intent.HYBRID:
            # 两路并行（简化实现为顺序，生产可改为 concurrent.futures）
            result.vector_results = self.vector_store.search(
                query=question,
                collection_name=self.collection_name,
                top_k=self.top_k,
            )
            result.sql_results = self.sql_engine.query(question)

        elif decision.intent == Intent.CHITCHAT:
            import random

            result.final_answer = random.choice(self.CHITCHAT_REPLIES)

        else:
            # UNKNOWN → 默认向量检索
            result.vector_results = self.vector_store.search(
                query=question,
                collection_name=self.collection_name,
                top_k=self.top_k,
            )

        # 3. 生成自然语言答案
        if self.generate_answer and decision.intent != Intent.CHITCHAT:
            result.final_answer = self._generate_answer(question, result)

        result.elapsed_total_ms = (time.perf_counter() - t_start) * 1000
        logger.info("查询完成: %.1fms", result.elapsed_total_ms)
        return result

    # ------------------------------------------------------------------
    # 答案生成
    # ------------------------------------------------------------------

    def _generate_answer(self, question: str, result: HybridResult) -> str:
        """基于检索结果，调用 LLM 生成自然语言答案"""
        if not self.answer_llm:
            # 无 LLM 时返回原始数据摘要
            return self._format_raw_results(result)

        context_parts: list[str] = []

        if result.has_vector:
            context_parts.append("【文档检索结果】")
            for r in result.vector_results.results[:3]:
                context_parts.append(f"- (相关度: {r.score:.2f}) {r.content[:300]}")

        if result.has_sql:
            context_parts.append(f"\n【数据库查询结果】")
            context_parts.append(f"SQL: {result.sql_results.query}")
            context_parts.append(f"列: {result.sql_results.columns}")
            context_parts.append(f"数据: {result.sql_results.rows[:20]}")

        if not context_parts:
            return "抱歉，未能找到相关信息。请尝试换个问法。"

        system_prompt = (
            "你是一个企业数据分析助手。请根据提供的数据回答用户的问题。"
            "回答应简洁、准确，直接针对问题。如果数据不足以回答，请诚实说明。"
        )
        user_msg = (
            f"用户问题: {question}\n\n"
            f"相关数据:\n{chr(10).join(context_parts)}\n\n"
            "请用中文回答。"
        )

        try:
            return self.answer_llm.chat(system_prompt, user_msg)
        except Exception as exc:
            logger.error("LLM 答案生成失败: %s", exc)
            return self._format_raw_results(result)

    def _format_raw_results(self, result: HybridResult) -> str:
        """无 LLM 时的原始结果格式化"""
        lines: list[str] = []

        if result.has_sql:
            lines.append(f"**SQL 查询结果** ({result.sql_results.row_count} 行):")
            if result.sql_results.columns:
                lines.append(" | ".join(str(c) for c in result.sql_results.columns))
                lines.append(" | ".join("---" for _ in result.sql_results.columns))
                for row in result.sql_results.rows[:10]:
                    lines.append(" | ".join(str(v) for v in row))
            lines.append("")

        if result.has_vector:
            lines.append(f"**文档检索结果** ({len(result.vector_results.results)} 条):")
            for r in result.vector_results.results[:5]:
                lines.append(f"- [{r.score:.2f}] {r.content[:150]}...")

        return "\n".join(lines) if lines else "未找到相关信息。"
