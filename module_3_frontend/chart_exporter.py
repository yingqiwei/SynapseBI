"""
图表导出器
==========
支持将查询结果导出为多种格式的图表。

支持格式:
  - PNG (matplotlib / plotly)
  - SVG (matplotlib)
  - HTML (plotly 可交互)
  - Excel (openpyxl)
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ChartExporter:
    """
    图表导出器。

    使用示例::

        exporter = ChartExporter(output_dir="./exports")
        path = exporter.export(
            chart_type="bar",
            data=[{"month": "Jan", "sales": 120}, {"month": "Feb", "sales": 200}],
            title="Monthly Sales",
            fmt="png",
            x_col="month",
            y_col="sales",
        )
    """

    def __init__(self, output_dir: str | None = None):
        self.output_dir = Path(output_dir) if output_dir else Path(tempfile.gettempdir()) / "synapsebi_exports"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def export(
        self,
        chart_type: str,
        data: list[dict[str, Any]],
        title: str = "",
        fmt: str = "png",
        x_col: str | None = None,
        y_col: str | None = None,
        **kwargs,
    ) -> str:
        """
        导出图表。

        Args:
            chart_type: bar | line | pie | scatter | table
            data: 图表数据（字典列表）
            title: 图表标题
            fmt: 导出格式
            x_col: X 轴列名
            y_col: Y 轴列名

        Returns:
            输出文件路径
        """
        if not data:
            raise ValueError("数据为空，无法生成图表")

        if fmt == "excel":
            return self._export_excel(data, title)
        elif fmt == "html":
            return self._export_plotly_html(chart_type, data, title, x_col, y_col)
        else:
            return self._export_image(chart_type, data, title, fmt, x_col, y_col)

    # ------------------------------------------------------------------
    # 图片导出 (matplotlib)
    # ------------------------------------------------------------------

    def _export_image(
        self,
        chart_type: str,
        data: list[dict[str, Any]],
        title: str,
        fmt: str,
        x_col: str | None,
        y_col: str | None,
    ) -> str:
        """使用 matplotlib 导出图片"""
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.font_manager as fm
        import numpy as np

        # 尝试设置中文字体
        self._set_chinese_font()

        # 自动推断列
        x_col, y_col = self._infer_axes(data, x_col, y_col)
        x_labels = [str(row.get(x_col, "")) for row in data]
        y_values = [float(row.get(y_col, 0) or 0) for row in data]

        fig, ax = plt.subplots(figsize=(10, 5))

        if chart_type == "bar":
            colors = plt.cm.Blues(np.linspace(0.4, 0.9, len(y_values)))
            ax.bar(x_labels, y_values, color=colors, edgecolor="white", linewidth=0.5)
        elif chart_type == "line":
            ax.plot(x_labels, y_values, marker="o", linewidth=2, markersize=6, color="#2563eb")
            ax.fill_between(range(len(y_values)), y_values, alpha=0.1, color="#2563eb")
        elif chart_type == "pie":
            wedges, texts, autotexts = ax.pie(
                y_values,
                labels=x_labels,
                autopct="%1.1f%%",
                colors=plt.cm.Set3(np.linspace(0, 1, len(y_values))),
            )
            for at in autotexts:
                at.set_fontsize(9)
            ax.set_aspect("equal")
        elif chart_type == "scatter":
            ax.scatter(x_labels, y_values, s=80, color="#2563eb", alpha=0.7, edgecolors="white")
        else:
            raise ValueError(f"不支持的图表类型: {chart_type}")

        if chart_type != "pie":
            ax.set_xlabel(x_col, fontsize=11)
            ax.set_ylabel(y_col, fontsize=11)
            ax.grid(axis="y", alpha=0.3)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)

        ax.set_title(title or f"{y_col} by {x_col}", fontsize=14, fontweight="bold")
        plt.tight_layout()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"chart_{ts}.{fmt}"
        fig.savefig(output_path, dpi=150, bbox_inches="tight", format=fmt)
        plt.close(fig)

        logger.info("图表已导出: %s", output_path)
        return str(output_path)

    # ------------------------------------------------------------------
    # Plotly HTML 导出（可交互）
    # ------------------------------------------------------------------

    def _export_plotly_html(
        self,
        chart_type: str,
        data: list[dict[str, Any]],
        title: str,
        x_col: str | None,
        y_col: str | None,
    ) -> str:
        """使用 plotly 导出可交互的 HTML 图表"""
        import plotly.express as px
        import pandas as pd

        df = pd.DataFrame(data)
        x_col, y_col = self._infer_axes(data, x_col, y_col)

        chart_func = {
            "bar": px.bar,
            "line": px.line,
            "pie": px.pie,
            "scatter": px.scatter,
        }.get(chart_type, px.bar)

        fig = chart_func(df, x=x_col, y=y_col, title=title)
        fig.update_layout(template="plotly_white")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"chart_{ts}.html"
        fig.write_html(str(output_path))

        logger.info("Plotly HTML 图表已导出: %s", output_path)
        return str(output_path)

    # ------------------------------------------------------------------
    # Excel 导出
    # ------------------------------------------------------------------

    def _export_excel(self, data: list[dict[str, Any]], title: str) -> str:
        """导出数据为 Excel 文件"""
        import pandas as pd

        df = pd.DataFrame(data)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = self.output_dir / f"data_{ts}.xlsx"

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Data", index=False)

            # 自动调整列宽
            ws = writer.sheets["Data"]
            for col_idx, col in enumerate(df.columns, 1):
                max_len = max(
                    df[col].astype(str).str.len().max(),
                    len(str(col)),
                )
                ws.column_dimensions[ws.cell(1, col_idx).column_letter].width = min(max_len + 2, 40)

        logger.info("Excel 数据已导出: %s", output_path)
        return str(output_path)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _infer_axes(
        self,
        data: list[dict[str, Any]],
        x_col: str | None,
        y_col: str | None,
    ) -> tuple[str, str]:
        """自动推断 X/Y 轴列名"""
        if not data:
            return "index", "value"

        keys = list(data[0].keys())

        if x_col and y_col:
            return x_col, y_col

        # 自动推断：第一列为 X，最后一列为 Y
        if len(keys) >= 2:
            return x_col or keys[0], y_col or keys[-1]
        return x_col or keys[0], y_col or keys[0]

    def _set_chinese_font(self):
        """尝试设置中文字体"""
        import matplotlib.font_manager as fm

        # 常见中文字体列表
        candidates = [
            "SimHei",
            "Microsoft YaHei",
            "WenQuanYi Micro Hei",
            "WenQuanYi Zen Hei",
            "Noto Sans CJK SC",
            "Source Han Sans SC",
            "PingFang SC",
            "Hiragino Sans GB",
            "Arial Unicode MS",
        ]

        available = {f.name for f in fm.fontManager.ttflist}
        for font in candidates:
            if font in available:
                import matplotlib.pyplot as plt

                plt.rcParams["font.sans-serif"] = [font, "DejaVu Sans"]
                plt.rcParams["axes.unicode_minus"] = False
                return
