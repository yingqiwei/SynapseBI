"""
Streamlit 前端看板
===================
简洁的业务端看板，支持：
  - 自然语言查询
  - 结果展示（文档片段 + 数据表格 + 图表）
  - 一键导出图表
  - ETL 管线操作面板
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# 将项目根目录加入 sys.path 以便导入模块
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _init_session_state():
    """初始化 Streamlit session state"""
    import streamlit as st

    defaults = {
        "chat_history": [],  # [{role, content, results, time}]
        "search_engine": None,
        "etl_pipeline": None,
        "current_tab": "query",
        "vector_collection": "default",
        "pdf_backend": "pymupdf",
        "top_k": 5,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def _lazy_init_engine():
    """延迟初始化检索引擎（仅首次查询时加载）"""
    import streamlit as st

    if st.session_state.search_engine is not None:
        return

    try:
        from module_2_retrieval.vector_store import BGEEmbedder, VectorStore
        from module_2_retrieval.text_to_sql import LLMProvider, TextToSQLEngine
        from module_2_retrieval.router import IntentRouter
        from module_2_retrieval.search_engine import HybridSearchEngine

        # 嵌入模型
        embedder = BGEEmbedder(model_name="BAAI/bge-small-zh-v1.5", device="cpu")

        # 向量数据库
        vector_store = VectorStore(
            embedder=embedder,
            backend="qdrant",
            host=os.getenv("QDRANT_HOST", "localhost"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
        )

        # Text-to-SQL
        llm = LLMProvider(
            provider=os.getenv("LLM_PROVIDER", "ollama"),
            model=os.getenv("LLM_MODEL", "qwen2.5:7b"),
            api_base=os.getenv("LLM_API_BASE", "http://localhost:11434/v1"),
        )
        sql_engine = TextToSQLEngine(llm=llm, db_path=os.getenv("DB_PATH", "enterprise.db"))

        # 自动从 SQLite 数据库注册表结构，否则 Text-to-SQL 没有 schema 上下文
        try:
            import pandas as pd

            conn = sql_engine.connection
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]
            for table in tables:
                df = pd.read_sql(f'SELECT * FROM "{table}" LIMIT 50', conn)
                sql_engine.register_schema_from_df(table, df, description=f"{table} 业务表")
            if tables:
                st.success(f"已注册数据库表结构: {tables}")
        except Exception as exc:
            logger.warning("数据库表结构注册失败: %s", exc)

        # 路由器
        router = IntentRouter(mode="rule")

        # 混合检索引擎
        st.session_state.search_engine = HybridSearchEngine(
            vector_store=vector_store,
            sql_engine=sql_engine,
            router=router,
            collection_name=st.session_state.vector_collection,
            top_k=st.session_state.top_k,
            generate_answer=True,
            answer_llm=llm,
        )

        st.success("检索引擎初始化成功 ✓")
    except Exception as exc:
        st.error(f"检索引擎初始化失败: {exc}")
        logger.exception("引擎初始化失败")


# ---------------------------------------------------------------------------
# 页面组件
# ---------------------------------------------------------------------------


def _render_sidebar():
    """渲染侧边栏"""
    import streamlit as st

    with st.sidebar:
        st.markdown("## 🧠 SynapseBI")

        st.markdown("---")

        # 导航
        tab = st.radio(
            "导航",
            ["🔍 智能查询", "📊 数据看板", "⚙️ ETL 管理", "📋 系统状态"],
            key="nav",
        )
        tab_map = {
            "🔍 智能查询": "query",
            "📊 数据看板": "dashboard",
            "⚙️ ETL 管理": "etl",
            "📋 系统状态": "status",
        }
        st.session_state.current_tab = tab_map[tab]

        st.markdown("---")

        # 配置
        st.caption("查询设置")
        st.session_state.top_k = st.slider("返回条数", 1, 20, st.session_state.top_k)
        st.session_state.vector_collection = st.text_input(
            "向量集合", value=st.session_state.vector_collection
        )

        st.markdown("---")
        st.caption(f"© 2026 SynapseBI")


def _render_query_page():
    """渲染智能查询页面"""
    import streamlit as st

    st.header("🔍 智能查询")

    # 初始化引擎
    _lazy_init_engine()

    # 快速提问示例
    examples = [
        "今年的销售额是多少？",
        "哪个部门的利润率最高？",
        "什么是公司的报销流程？",
        "对比上一季度和本季度的营收变化",
        "近半年的客户投诉主要集中在哪些方面？",
    ]

    cols = st.columns(len(examples))
    for i, example in enumerate(examples):
        if cols[i].button(example[:12] + "...", key=f"ex_{i}", use_container_width=True):
            st.session_state.prompt = example
            st.rerun()

    # 输入框
    question = st.chat_input("输入你的问题...")
    if "prompt" in st.session_state and st.session_state.prompt:
        question = st.session_state.pop("prompt")

    if not question:
        # 显示历史
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])
                if msg.get("results"):
                    with st.expander("查看详细结果"):
                        st.json(msg["results"])
        return

    # 添加用户消息
    st.session_state.chat_history.append({"role": "user", "content": question, "time": time.time()})
    with st.chat_message("user"):
        st.write(question)

    # 执行查询
    if st.session_state.search_engine is None:
        st.error("检索引擎未就绪，请检查配置")
        return

    with st.spinner("正在分析..."):
        try:
            result = st.session_state.search_engine.query(question)
        except Exception as exc:
            st.error(f"查询出错: {exc}")
            return

    # 渲染结果
    with st.chat_message("assistant"):
        st.markdown(f"**意图:** `{result.intent.value}` (置信度: {result.route_decision.confidence:.0%})")

        if result.final_answer:
            st.markdown("### 📝 回答")
            st.write(result.final_answer)

        # SQL 结果
        if result.has_sql:
            with st.expander(f"📊 SQL 查询结果 ({result.sql_results.row_count} 行)", expanded=True):
                st.code(result.sql_results.query, language="sql")
                if result.sql_results.rows:
                    import pandas as pd

                    df = pd.DataFrame(
                        result.sql_results.rows,
                        columns=result.sql_results.columns,
                    )
                    st.dataframe(df, use_container_width=True)

                    # 图表按钮
                    if st.button("📈 生成图表", key="gen_chart"):
                        st.session_state.show_chart = True
                        st.session_state.chart_data = df

        # 向量检索结果
        if result.has_vector:
            with st.expander(f"📄 文档检索结果 ({len(result.vector_results.results)} 条)"):
                for r in result.vector_results.results:
                    st.markdown(f"**[{r.score:.3f}]** 来源: {r.metadata.get('source', 'N/A')}")
                    st.text(r.content[:400] + ("..." if len(r.content) > 400 else ""))
                    st.divider()

        st.caption(f"⏱ 耗时: {result.elapsed_total_ms:.0f}ms")

    # 记录历史
    st.session_state.chat_history.append(
        {
            "role": "assistant",
            "content": result.final_answer or "(无文本回答)",
            "results": result.summary(),
            "time": time.time(),
        }
    )


def _render_dashboard_page():
    """渲染数据看板页面"""
    import streamlit as st

    st.header("📊 数据看板")

    if "chart_data" in st.session_state:
        df = st.session_state.chart_data
        st.dataframe(df, use_container_width=True)

        col1, col2, col3 = st.columns(3)
        chart_type = col1.selectbox("图表类型", ["bar", "line", "pie", "scatter"])

        if len(df.columns) >= 2:
            x_col = col2.selectbox("X 轴", df.columns.tolist())
            y_col = col3.selectbox("Y 轴", [c for c in df.columns if c != x_col])

            if st.button("生成图表", type="primary"):
                _render_chart(df, x_col, y_col, chart_type)

                # 导出按钮
                if st.button("⬇️ 导出为 PNG"):
                    _export_chart(df, x_col, y_col, chart_type, "png")
                if st.button("⬇️ 导出为 Excel"):
                    _export_chart(df, x_col, y_col, chart_type, "excel")
    else:
        st.info('请先在查询页面提出一个数据类问题（如"今年的销售额是多少？"），然后点击"生成图表"。')
        st.markdown("也可以上传一个 CSV/Excel 文件来快速可视化：")
        uploaded = st.file_uploader("上传数据文件", type=["csv", "xlsx"])
        if uploaded:
            import pandas as pd

            if uploaded.name.endswith(".csv"):
                df = pd.read_csv(uploaded)
            else:
                df = pd.read_excel(uploaded)
            st.session_state.chart_data = df
            st.rerun()


def _render_chart(df, x_col: str, y_col: str, chart_type: str):
    """根据类型渲染图表"""
    import streamlit as st

    try:
        import plotly.express as px

        chart_func = {
            "bar": px.bar,
            "line": px.line,
            "pie": px.pie,
            "scatter": px.scatter,
        }.get(chart_type, px.bar)

        fig = chart_func(df, x=x_col, y=y_col, title=f"{y_col} by {x_col}")
        fig.update_layout(template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)
    except ImportError:
        st.warning("请安装 plotly: pip install plotly")


def _export_chart(df, x_col: str, y_col: str, chart_type: str, fmt: str):
    """导出图表"""
    import streamlit as st

    try:
        from module_3_frontend.chart_exporter import ChartExporter

        data = df.to_dict(orient="records")
        exporter = ChartExporter()
        output_path = exporter.export(
            chart_type=chart_type,
            data=data,
            title=f"{y_col} by {x_col}",
            fmt=fmt,
            x_col=x_col,
            y_col=y_col,
        )
        with open(output_path, "rb") as f:
            st.download_button(
                label=f"下载 {fmt.upper()}",
                data=f,
                file_name=Path(output_path).name,
                mime={
                    "png": "image/png",
                    "svg": "image/svg+xml",
                    "html": "text/html",
                    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                }.get(fmt, "application/octet-stream"),
            )
    except ImportError as exc:
        st.error(f"缺少依赖: {exc}")


def _render_etl_page():
    """渲染 ETL 管理页面"""
    import streamlit as st

    st.header("⚙️ ETL 数据接入管理")

    col1, col2 = st.columns([2, 1])

    with col1:
        source_dir = st.text_input("数据源目录", value="./data", placeholder="./data")
        pdf_backend = st.selectbox("PDF 解析后端", ["pymupdf", "unstructured", "paddleocr"])
        recursive = st.checkbox("递归扫描子目录", value=True)

    with col2:
        st.markdown("### 支持的文件")
        st.markdown("- PDF (.pdf)")
        st.markdown("- Excel (.xlsx, .xls)")
        st.markdown("- CSV (.csv)")

    if st.button("🚀 启动 ETL", type="primary", use_container_width=True):
        if not Path(source_dir).is_dir():
            st.error(f"目录不存在: {source_dir}")
            return

        try:
            from module_1_etl.pipeline import ETLPipeline

            with st.spinner(f"正在处理 {source_dir}..."):
                pipeline = ETLPipeline(
                    source_dir=source_dir,
                    pdf_backend=pdf_backend,
                    recursive=recursive,
                )
                st.session_state.etl_pipeline = pipeline
                result = pipeline.run()

            # 显示摘要
            summary = result.summary()
            st.success(f"ETL 完成！处理了 {summary['pdf_count']} 个 PDF, {summary['excel_count']} 个 Excel")

            col_a, col_b, col_c = st.columns(3)
            col_a.metric("PDF 文档", summary["pdf_count"])
            col_b.metric("Excel 文件", summary["excel_count"])
            col_c.metric("总产出块", summary["total_chunks"])

            if result.errors:
                st.warning(f"{len(result.errors)} 个文件处理失败")
                with st.expander("查看错误详情"):
                    for err in result.errors:
                        st.error(f"{err['file']}: {err['error']}")

            # 导出按钮
            if result.all_chunks:
                import tempfile

                with tempfile.NamedTemporaryFile(
                    suffix=".jsonl", delete=False, mode="w", encoding="utf-8"
                ) as tmp:
                    for chunk in result.all_chunks:
                        tmp.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                    tmp_path = tmp.name

                with open(tmp_path, "rb") as f:
                    st.download_button(
                        "⬇️ 导出数据 (JSONL)",
                        data=f,
                        file_name="etl_output.jsonl",
                        mime="application/jsonl",
                    )

        except Exception as exc:
            st.error(f"ETL 执行失败: {exc}")
            logger.exception("ETL 失败")


def _render_status_page():
    """渲染系统状态页面"""
    import streamlit as st

    st.header("📋 系统状态")

    col1, col2, col3 = st.columns(3)

    # 检索引擎
    engine = st.session_state.get("search_engine")
    if engine:
        col1.metric("检索引擎", "✓ 就绪")
        try:
            cols = engine.vector_store.list_collections()
            col2.metric("向量集合数", len(cols))
            col3.metric("后端", engine.vector_store.backend)
        except Exception:
            col2.metric("向量数据库", "✗ 未连接")
    else:
        col1.metric("检索引擎", "未初始化")

    # ETL
    etl = st.session_state.get("etl_pipeline")
    if etl:
        st.metric("ETL 后端", etl.pdf_parser.backend_name)
    else:
        st.metric("ETL 管线", "待启动")

    # 环境变量
    with st.expander("环境配置"):
        env_vars = ["QDRANT_HOST", "QDRANT_PORT", "LLM_PROVIDER", "LLM_MODEL", "DB_PATH"]
        for var in env_vars:
            val = os.getenv(var, "(未设置)")
            st.text(f"{var} = {val}")

    # 会话统计
    with st.expander("会话统计"):
        history = st.session_state.get("chat_history", [])
        st.text(f"本轮对话数: {len(history)}")
        st.text(f"查询次数: {sum(1 for h in history if h['role'] == 'user')}")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def main():
    """Streamlit 应用入口"""
    import streamlit as st

    # 页面配置
    st.set_page_config(
        page_title="SynapseBI — 智能决策中枢",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # 自定义样式
    # 注意：不要覆盖 .stApp 背景色，否则深色主题下主区域会保持白色
    st.markdown(
        """
        <style>
        .stChatMessage { border-radius: 12px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _init_session_state()
    _render_sidebar()

    # 路由到对应页面
    tab = st.session_state.current_tab
    if tab == "query":
        _render_query_page()
    elif tab == "dashboard":
        _render_dashboard_page()
    elif tab == "etl":
        _render_etl_page()
    elif tab == "status":
        _render_status_page()


if __name__ == "__main__":
    main()
