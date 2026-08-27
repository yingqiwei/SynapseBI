"""
Text-to-SQL 引擎
=================
将自然语言问题转换为安全的 SQL 查询，直接读取企业关系型数据库。

安全策略：
  - 只读限制：仅允许 SELECT 语句，禁止 INSERT/UPDATE/DELETE/DDL
  - 白名单表：仅允许查询授权表
  - SQL 语法校验与注入防护
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class TableSchema:
    """表结构描述 —— 供 LLM 生成 SQL 时参考"""

    table_name: str
    columns: list[dict[str, Any]]  # [{name, type, nullable, description}, ...]
    row_count: int | None = None
    description: str = ""
    sample_rows: list[dict[str, Any]] = field(default_factory=list)  # 前 3 行样本


@dataclass
class SQLResult:
    """SQL 查询结果"""

    query: str
    natural_language: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    elapsed_ms: float = 0.0
    error: str | None = None
    explanation: str = ""


# ---------------------------------------------------------------------------
# SQL 安全校验
# ---------------------------------------------------------------------------


class SQLGuard:
    """
    SQL 安全守卫 —— 确保只执行只读、安全的查询。
    """

    # 仅允许的 SQL 关键字
    ALLOWED_STATEMENTS = {"SELECT", "WITH", "EXPLAIN", "DESCRIBE", "SHOW"}

    # 明确禁止的关键字（防止绕过）
    FORBIDDEN_KEYWORDS = [
        "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
        "TRUNCATE", "REPLACE", "MERGE", "GRANT", "REVOKE",
        "EXEC", "EXECUTE", "CALL", "LOAD", "IMPORT",
        "ATTACH", "DETACH", "PRAGMA",
        "--", "/*", "*/",  # 注释可能被用于注入
        "xp_cmdshell", "sp_executesql",  # SQL Server 特例
    ]

    def __init__(self, allowed_tables: list[str] | None = None):
        self.allowed_tables = set(t.upper() for t in (allowed_tables or []))

    def validate(self, sql: str) -> tuple[bool, str]:
        """
        校验 SQL 语句的安全性。

        Returns:
            (is_safe, reason)
        """
        sql_upper = sql.upper().strip()

        # 1. 检查是否以允许的关键字开头
        first_word = sql_upper.split()[0] if sql_upper.split() else ""
        if first_word not in self.ALLOWED_STATEMENTS:
            return False, f"禁止的 SQL 语句类型: {first_word}"

        # 2. 检查禁止的关键字
        for kw in self.FORBIDDEN_KEYWORDS:
            if kw in sql_upper:
                return False, f"SQL 中包含禁止关键字: {kw}"

        # 3. 检查表名白名单
        if self.allowed_tables:
            table_refs = self._extract_table_names(sql)
            for table in table_refs:
                if table.upper() not in self.allowed_tables:
                    return False, f"未授权的表: {table}"

        return True, "ok"

    def _extract_table_names(self, sql: str) -> list[str]:
        """从 SQL 中提取表名（简化版，剔除 CTE 别名）"""
        tables: list[str] = []
        # 匹配 FROM / JOIN 后的标识符
        pattern = r"(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([`\[\"\']?\w+[`\]\"\']?(?:\s*,\s*[`\[\"\']?\w+[`\]\"\']?)*)"
        matches = re.findall(pattern, sql, re.IGNORECASE)
        cte_names = self._extract_cte_names(sql)
        for m in matches:
            for token in re.split(r"\s*,\s*", m):
                clean = token.strip("`[]\"'")
                if clean and clean.upper() not in cte_names:
                    tables.append(clean)
        return tables

    @staticmethod
    def _extract_cte_names(sql: str) -> set[str]:
        """提取 WITH 子句中定义的 CTE 别名，避免被误当作表名"""
        ctes: set[str] = set()
        # 匹配每个 CTE 定义: WITH <name> AS ( 以及后续的 , <name> AS (
        pattern = r"(?:WITH|,)\s*([`\[\"\']?\w+[`\]\"\']?)\s+AS\s*\("
        for m in re.finditer(pattern, sql, re.IGNORECASE):
            ctes.add(m.group(1).strip("`[]\"'").upper())
        return ctes

    def set_allowed_tables(self, tables: list[str]) -> None:
        self.allowed_tables = set(t.upper() for t in tables)


# ---------------------------------------------------------------------------
# LLM 接口（用于 SQL 生成）
# ---------------------------------------------------------------------------


class LLMProvider:
    """
    简化的大模型调用接口。

    支持：
      - OpenAI 兼容 API（本地 vLLM / Ollama / LM Studio）
      - Anthropic Claude API
    """

    def __init__(
        self,
        provider: Literal["openai", "anthropic", "ollama"] = "openai",
        model: str = "gpt-4",
        api_base: str | None = None,
        api_key: str | None = None,
        temperature: float = 0.0,
    ):
        self.provider = provider
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.temperature = temperature

    def chat(self, system_prompt: str, user_message: str) -> str:
        """发送对话请求，返回模型回复文本"""
        if self.provider == "openai":
            return self._chat_openai(system_prompt, user_message)
        elif self.provider == "anthropic":
            return self._chat_anthropic(system_prompt, user_message)
        elif self.provider == "ollama":
            return self._chat_ollama(system_prompt, user_message)
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    def _chat_openai(self, system: str, user: str) -> str:
        from openai import OpenAI

        client = OpenAI(
            base_url=self.api_base,
            api_key=self.api_key or "not-needed",
        )
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=self.temperature,
        )
        return resp.choices[0].message.content or ""

    def _chat_anthropic(self, system: str, user: str) -> str:
        from anthropic import Anthropic

        client = Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text if resp.content else ""

    def _chat_ollama(self, system: str, user: str) -> str:
        import requests

        base = (self.api_base or "http://localhost:11434").rstrip("/")
        # 兼容两种配置：http://host:11434 与 OpenAI 风格 http://host:11434/v1
        if base.endswith("/v1"):
            base = base[:-3]
        resp = requests.post(
            f"{base}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": 512,
                    "seed": 42,
                },
            },
            timeout=120,
        )
        return resp.json()["message"]["content"]


# ---------------------------------------------------------------------------
# Text-to-SQL 引擎
# ---------------------------------------------------------------------------


class TextToSQLEngine:
    """
    Text-to-SQL 引擎。

    使用示例::

        engine = TextToSQLEngine(
            llm=LLMProvider(provider="ollama", model="qwen2.5:7b"),
            db_path="enterprise.db",
        )
        engine.register_schema(TableSchema(...))
        result = engine.query("今年哪个部门的销售额最高？")
        print(result.rows)
    """

    SYSTEM_PROMPT = """你是一个 SQL 专家。将用户问题转换为 SQLite 语法的一条只读 SELECT 语句。

规则：
1. 仅输出 SQL 语句，不要有任何解释或 markdown 包裹。
2. 只使用 SELECT / WITH 语句，禁止任何写操作。
3. 表名和列名必须严格使用下方 schema 中出现的原始名称（例如 "销售数据"、"部门"、"sales_amount"），禁止翻译、改写或臆造名称；名称含括号、空格等特殊字符时必须用双引号包裹，例如 "利润_(万元)"。
4. 仅当问题明显属于文档/语义类（如解释概念、描述流程、总结报告内容）且与表格数据无关时，才输出: NOT_SQL；涉及数值、聚合、分组、排序、比较或筛选的问题，必须输出 SQL 查询语句。
5. SQL 中不要包含注释。
6. 不要使用任何函数或语法糖来绕过只读限制。"""

    def __init__(
        self,
        llm: LLMProvider,
        db_path: str | None = None,
        db_connection: Any | None = None,
    ):
        self.llm = llm
        self.db_path = db_path
        self._conn = db_connection
        self._guard = SQLGuard()
        self._schemas: dict[str, TableSchema] = {}

    @property
    def connection(self):
        """获取数据库连接"""
        if self._conn is None:
            if self.db_path:
                # FastAPI 等 Web 框架会在不同线程执行请求，
                # 必须允许跨线程复用连接，否则 SQL 查询会抛
                # "SQLite objects created in a thread can only be used in that same thread"
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            else:
                raise ValueError("需要提供 db_path 或 db_connection")
        return self._conn

    def register_schema(self, schema: TableSchema) -> None:
        """注册一个表的结构信息"""
        self._schemas[schema.table_name] = schema
        self._guard.set_allowed_tables(list(self._schemas.keys()))

    def register_schema_from_df(
        self,
        table_name: str,
        df: pd.DataFrame,
        description: str = "",
    ) -> None:
        """从 DataFrame 自动生成并注册表结构"""
        columns = []
        for col in df.columns:
            dtype = str(df[col].dtype)
            columns.append(
                {
                    "name": col,
                    "type": dtype,
                    "nullable": bool(df[col].isna().any()),
                    "description": "",
                }
            )

        schema = TableSchema(
            table_name=table_name,
            columns=columns,
            row_count=len(df),
            description=description,
            sample_rows=df.head(3).to_dict(orient="records"),
        )
        self.register_schema(schema)

    def build_schema_context(self) -> str:
        """构建 SQL schema 的文本描述，供 LLM 使用"""
        parts: list[str] = []
        for name, schema in self._schemas.items():
            parts.append(f"\n## 表: {name}")
            if schema.description:
                parts.append(f"  说明: {schema.description}")
            parts.append(f"  行数: {schema.row_count or '未知'}")
            parts.append("  列:")
            for col in schema.columns:
                nullable = "可空" if col.get("nullable") else "非空"
                parts.append(
                    f"    - {col['name']} ({col['type']}, {nullable})"
                )
            if schema.sample_rows:
                parts.append("  样本数据:")
                for i, row in enumerate(schema.sample_rows, 1):
                    # default=str 兜底：样本中可能含 Timestamp/date 等非 JSON 类型
                    parts.append(
                        f"    [{i}] {json.dumps(row, ensure_ascii=False, default=str)}"
                    )
        return "\n".join(parts)

    def query(
        self,
        question: str,
        execute: bool = True,
    ) -> SQLResult:
        """
        将自然语言转为 SQL 并（可选）执行。

        Args:
            question: 自然语言问题
            execute: 是否执行生成的 SQL

        Returns:
            SQLResult
        """
        import time

        start = time.perf_counter()

        # 1. 调用 LLM 生成 SQL（执行失败时最多纠错重试 1 次）
        schema_ctx = self.build_schema_context()
        logger.info("Text-to-SQL 请求: %s", question[:80])

        from datetime import date

        today = date.today().isoformat()
        columns: list[str] = []
        row_data: list[Any] = []
        final_query = ""
        last_error: str | None = None

        for attempt in range(2):
            if attempt == 0:
                user_msg = (
                    f"当前日期：{today}。\n"
                    f"数据库 Schema:\n{schema_ctx}\n\n用户问题:\n{question}"
                )
            else:
                user_msg = (
                    f"当前日期：{today}。\n"
                    f"数据库 Schema:\n{schema_ctx}\n\n用户问题:\n{question}\n\n"
                    f"注意：上一次生成的 SQL 执行失败：{last_error}\n"
                    '请检查表名和列名是否与 schema 完全一致（含括号等特殊字符时用双引号包裹，例如 "利润_(万元)"），'
                    "重新生成一条只读 SQL。"
                )

            sql_raw = self.llm.chat(self.SYSTEM_PROMPT, user_msg).strip()

            # 去除可能的 markdown 包裹
            sql_raw = re.sub(r"^```(?:sql)?\s*", "", sql_raw)
            sql_raw = re.sub(r"\s*```$", "", sql_raw)
            sql_raw = sql_raw.strip().rstrip(";")
            final_query = sql_raw

            # 2. 检查是否为非 SQL 问题
            if sql_raw.upper() == "NOT_SQL" or not sql_raw:
                elapsed = (time.perf_counter() - start) * 1000
                return SQLResult(
                    query="",
                    natural_language=question,
                    columns=[],
                    rows=[],
                    row_count=0,
                    elapsed_ms=round(elapsed, 1),
                    error="NOT_SQL",
                    explanation="该问题不适合用 SQL 回答，建议使用向量检索。",
                )

            # 3. 安全校验
            is_safe, reason = self._guard.validate(sql_raw)
            if not is_safe:
                logger.warning("SQL 安全校验失败: %s → %s", sql_raw, reason)
                elapsed = (time.perf_counter() - start) * 1000
                return SQLResult(
                    query=sql_raw,
                    natural_language=question,
                    columns=[],
                    rows=[],
                    row_count=0,
                    elapsed_ms=round(elapsed, 1),
                    error=f"SQL 安全校验失败: {reason}",
                    explanation="",
                )

            # 4. 执行
            if not execute:
                break
            try:
                cursor = self.connection.execute(sql_raw)
                rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                row_data = [list(row) for row in rows]
                last_error = None
                break
            except Exception as exc:
                last_error = str(exc)
                logger.error("SQL 执行失败（第 %d 次）: %s", attempt + 1, exc)

        if execute and last_error is not None:
            elapsed = (time.perf_counter() - start) * 1000
            return SQLResult(
                query=final_query,
                natural_language=question,
                columns=[],
                rows=[],
                row_count=0,
                elapsed_ms=round(elapsed, 1),
                error=last_error,
                explanation="SQL 执行时发生错误",
            )

        elapsed = (time.perf_counter() - start) * 1000
        return SQLResult(
            query=final_query,
            natural_language=question,
            columns=columns,
            rows=row_data,
            row_count=len(row_data),
            elapsed_ms=round(elapsed, 1),
        )

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
