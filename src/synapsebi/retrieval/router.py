"""
意图路由器
===========
轻量级 Router —— 判断用户问题应流向向量检索还是 SQL 查询。

设计思路：
  1. 基于规则的关键词快速匹配（低延迟）
  2. 可选的 LLM 语义判断（高精度）
  3. 支持混合模式 —— 同时执行两路检索后融合排序
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class Intent(Enum):
    """用户意图分类"""

    VECTOR_SEARCH = "vector_search"  # 非结构化语义检索
    SQL_QUERY = "sql_query"  # 结构化 SQL 查询
    HYBRID = "hybrid"  # 需要两路融合
    CHITCHAT = "chitchat"  # 闲聊 / 无关问题
    UNKNOWN = "unknown"


@dataclass
class RouteDecision:
    """路由决策结果"""

    intent: Intent
    confidence: float  # 0.0 ~ 1.0
    reason: str
    # 结构化查询可能包含预提取的实体
    entities: dict[str, Any] = field(default_factory=dict)
    # 子意图：如果 HYBRID，拆分为两路子查询
    sub_queries: dict[Intent, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 路由器
# ---------------------------------------------------------------------------


class IntentRouter:
    """
    意图路由器。

    - 规则模式：基于正则表达式 + 关键词，零延迟
    - LLM 模式：调用模型做语义级别的意图分类
    - 混合模式：规则优先，低置信度时回退到 LLM

    使用示例::

        router = IntentRouter(mode="rule")
        decision = router.route("今年的销售额同比增长了多少？")
        # → RouteDecision(intent=SQL_QUERY, confidence=0.95, ...)
    """

    # SQL 相关关键词（聚合、对比、排名等结构化查询特征）
    SQL_KEYWORDS: dict[str, float] = {
        # 聚合类 — 强 SQL 信号
        "总": 0.9, "总计": 0.9, "合计": 0.9, "汇总": 0.9,
        "平均值": 0.9, "平均": 0.85, "均值": 0.85,
        "最大值": 0.9, "最小值": 0.9, "最多": 0.85, "最少": 0.85,
        "计数": 0.9, "数量": 0.8, "多少个": 0.85, "几条": 0.85,
        # 排序类
        "排名": 0.85, "前十": 0.9, "前10": 0.9, "前三": 0.9,
        "最高": 0.85, "最低": 0.85, "降序": 0.9, "升序": 0.9,
        # 对比类
        "同比": 0.9, "环比": 0.9, "增长率": 0.85, "增长率": 0.85,
        "对比": 0.7, "比较": 0.65, "变化": 0.6,
        # 分组/筛选
        "按": 0.7, "每个": 0.7, "各个": 0.7, "分别": 0.65,
        "超过": 0.7, "大于": 0.75, "小于": 0.75, "等于": 0.7,
        "占比": 0.8, "比例": 0.7, "百分比": 0.8,
        # 时间范围
        "今年": 0.7, "去年": 0.7, "本月": 0.7, "上个月": 0.7,
        "本季度": 0.75, "全年": 0.7, "上半年": 0.75, "下半年": 0.75,
    }

    # 语义理解类关键词（文档、报告、解释类）
    VECTOR_KEYWORDS: dict[str, float] = {
        "是什么": 0.9, "什么是": 0.9, "定义": 0.8, "解释": 0.75,
        "如何": 0.7, "怎么": 0.7, "为什么": 0.65, "原因": 0.65,
        "概述": 0.8, "总结": 0.75, "摘要": 0.8, "概要": 0.8,
        "简介": 0.8, "说明": 0.7, "描述": 0.65, "介绍": 0.65,
        "文档": 0.8, "报告": 0.7, "政策": 0.7, "规定": 0.7,
        "流程": 0.65, "步骤": 0.6, "方案": 0.6, "建议": 0.6,
        "含义": 0.75, "意思": 0.7, "内容": 0.5,
    }

    # 实体提取正则
    ENTITY_PATTERNS: dict[str, str] = {
        "department": r"(销售部|市场部|研发部|财务部|人事部|行政部|技术部|运营部)",
        "time_range": r"(今年|去年|本月|上个月|本季度|上半年|下半年|\d{4}年|\d+月)",
        "product": r"([A-Z]{2,}-\d+|产品[A-Z])",
        "metric": r"(销售额|利润|成本|利润率|ROI|KPI|GMV|营收)",
    }

    def __init__(
        self,
        mode: str = "rule",  # "rule" | "llm" | "hybrid"
        llm: Any = None,  # LLMProvider 实例（LLM 模式时必需）
        rule_threshold: float = 0.6,
    ):
        self.mode = mode
        self.llm = llm
        self.rule_threshold = rule_threshold

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def route(self, question: str) -> RouteDecision:
        """分析用户问题，返回路由决策"""
        question = question.strip()
        if not question:
            return RouteDecision(
                intent=Intent.UNKNOWN,
                confidence=0.0,
                reason="问题为空",
            )

        # 快速闲聊检测
        if self._is_chitchat(question):
            return RouteDecision(
                intent=Intent.CHITCHAT,
                confidence=0.95,
                reason="识别为闲聊",
            )

        # 规则模式
        if self.mode == "rule":
            return self._rule_route(question)

        # LLM 模式
        if self.mode == "llm":
            return self._llm_route(question)

        # 混合模式：规则优先，低置信度回退 LLM
        decision = self._rule_route(question)
        if decision.confidence < self.rule_threshold and self.llm:
            logger.info("规则置信度 %.2f < 阈值 %.2f，回退 LLM", decision.confidence, self.rule_threshold)
            return self._llm_route(question)
        return decision

    # ------------------------------------------------------------------
    # 规则路由
    # ------------------------------------------------------------------

    def _rule_route(self, question: str) -> RouteDecision:
        """基于关键词 + 正则的规则路由"""
        sql_score = self._score_keywords(question, self.SQL_KEYWORDS)
        vector_score = self._score_keywords(question, self.VECTOR_KEYWORDS)

        # 提取实体
        entities = self._extract_entities(question)

        logger.debug("规则评分: SQL=%.3f, Vector=%.3f", sql_score, vector_score)

        # 决策逻辑
        if sql_score > 0.7 and vector_score < 0.3:
            return RouteDecision(
                intent=Intent.SQL_QUERY,
                confidence=min(sql_score, 0.95),
                reason=f"强 SQL 信号 (sql={sql_score:.2f}, vec={vector_score:.2f})",
                entities=entities,
            )
        elif vector_score > 0.6 and sql_score < 0.3:
            return RouteDecision(
                intent=Intent.VECTOR_SEARCH,
                confidence=min(vector_score, 0.95),
                reason=f"强向量检索信号 (sql={sql_score:.2f}, vec={vector_score:.2f})",
                entities=entities,
            )
        elif sql_score > 0.4 and vector_score > 0.4:
            return RouteDecision(
                intent=Intent.HYBRID,
                confidence=max(sql_score, vector_score),
                reason=f"混合信号 (sql={sql_score:.2f}, vec={vector_score:.2f})",
                entities=entities,
                sub_queries=self._build_hybrid_queries(question, entities),
            )
        elif sql_score > 0.3:
            return RouteDecision(
                intent=Intent.SQL_QUERY,
                confidence=sql_score,
                reason=f"弱 SQL 信号",
                entities=entities,
            )
        elif vector_score > 0.2:
            return RouteDecision(
                intent=Intent.VECTOR_SEARCH,
                confidence=vector_score,
                reason=f"弱向量检索信号",
                entities=entities,
            )
        else:
            # 默认为向量检索（语义兜底）
            return RouteDecision(
                intent=Intent.VECTOR_SEARCH,
                confidence=0.4,
                reason="无明确信号，默认向量检索",
            )

    def _score_keywords(self, text: str, keyword_weights: dict[str, float]) -> float:
        """基于关键词权重的评分"""
        if not text:
            return 0.0
        total = 0.0
        found_any = False
        for kw, weight in keyword_weights.items():
            if kw in text:
                total += weight
                found_any = True
        # 归一化：按匹配到的关键词数量 + 最高权重
        if not found_any:
            return 0.0
        # 使用 sigmoid 平滑
        return min(total / (1 + total), 1.0)

    def _is_chitchat(self, question: str) -> bool:
        """检测是否为闲聊"""
        chitchat_patterns = [
            r"^(你好|哈喽|hi|hello|嗨|早上好|下午好|晚上好)[!！。.]*$",
            r"^(谢谢|感谢|多谢|thanks?|thank you)[!！。.]*$",
            r"^(再见|拜拜|bye|goodbye|明天见)[!！。.]*$",
        ]
        for pat in chitchat_patterns:
            if re.match(pat, question.strip(), re.IGNORECASE):
                return True
        return False

    def _extract_entities(self, question: str) -> dict[str, Any]:
        """从问题中提取结构化实体"""
        entities: dict[str, Any] = {}
        for entity_type, pattern in self.ENTITY_PATTERNS.items():
            matches = re.findall(pattern, question)
            if matches:
                entities[entity_type] = list(set(matches))
        return entities

    def _build_hybrid_queries(
        self, question: str, entities: dict[str, Any]
    ) -> dict[Intent, str]:
        """为混合检索构建子查询"""
        return {
            Intent.VECTOR_SEARCH: question,
            Intent.SQL_QUERY: question,
        }

    # ------------------------------------------------------------------
    # LLM 路由
    # ------------------------------------------------------------------

    def _llm_route(self, question: str) -> RouteDecision:
        """使用 LLM 做语义意图分类"""
        if not self.llm:
            logger.warning("LLM 模式但未提供 llm 实例，回退规则路由")
            return self._rule_route(question)

        prompt = (
            "你是一个查询意图分类器。将用户的自然语言问题分类为以下之一：\n\n"
            "1. SQL_QUERY —— 需要查询结构化数据库，涉及聚合、排序、筛选、对比等。\n"
            "2. VECTOR_SEARCH —— 需要从文档/报告中检索语义相关内容。\n"
            "3. HYBRID —— 需要同时进行结构化查询和语义检索。\n"
            "4. CHITCHAT —— 闲聊、问候等不涉及数据的问题。\n\n"
            "输出格式（仅输出 JSON）：\n"
            '{"intent": "SQL_QUERY", "confidence": 0.95, "reason": "..."}\n\n'
            f"用户问题: {question}"
        )

        try:
            response = self.llm.chat("", prompt)
            import json as _json

            data = _json.loads(response)
            intent_map = {
                "SQL_QUERY": Intent.SQL_QUERY,
                "VECTOR_SEARCH": Intent.VECTOR_SEARCH,
                "HYBRID": Intent.HYBRID,
                "CHITCHAT": Intent.CHITCHAT,
            }
            intent = intent_map.get(data.get("intent", "").upper(), Intent.UNKNOWN)
            return RouteDecision(
                intent=intent,
                confidence=float(data.get("confidence", 0.5)),
                reason=data.get("reason", "LLM 分类"),
            )
        except Exception as e:
            logger.error("LLM 路由失败: %s，回退规则路由", e)
            return self._rule_route(question)
