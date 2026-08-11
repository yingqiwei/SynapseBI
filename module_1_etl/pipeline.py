"""
ETL 主流程编排
==============
协调 PDF 解析器与 Excel 处理器，将异构数据统一输出为：
  1. 文档块列表（用于向量入库）
  2. 结构化表格数据（用于 Text-to-SQL / 数据库写入）
  3. 元数据与 Schema 描述
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from .pdf_parser import PDFDocument, PDFParser
from .excel_processor import ExcelDocument, ExcelProcessor, SchemaExtractor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _to_json_safe(obj: Any) -> Any:
    """将 pandas/numpy 类型转换为 JSON 可序列化类型"""
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        if np.isnan(obj):
            return None
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, (pd.Period,)):
        return str(obj)
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return obj


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ETLResult:
    """一次 ETL 运行的完整产出"""

    source_dir: str
    pdf_documents: list[PDFDocument] = field(default_factory=list)
    excel_documents: list[ExcelDocument] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    @property
    def all_chunks(self) -> list[dict[str, Any]]:
        """所有文档块（PDF + Excel），统一格式便于向量入库"""
        chunks: list[dict[str, Any]] = []
        for pdf in self.pdf_documents:
            for c in pdf.chunks:
                chunks.append(
                    {
                        "source": pdf.file_name,
                        "source_type": "pdf",
                        "chunk_id": c.chunk_id,
                        "page": c.page_number,
                        "type": c.chunk_type,
                        "content": c.content,
                        "confidence": c.confidence,
                    }
                )
        for excel in self.excel_documents:
            for sheet_name, df in excel.sheets.items():
                for _, row in df.iterrows():
                    row_dict = row.dropna().to_dict()
                    row_dict = {k: _to_json_safe(v) for k, v in row_dict.items()}
                    chunks.append(
                        {
                            "source": excel.file_name,
                            "source_type": "excel",
                            "sheet": sheet_name,
                            "chunk_id": "",
                            "page": 0,
                            "type": "row",
                            "content": json.dumps(row_dict, ensure_ascii=False),
                            "confidence": 1.0,
                        }
                    )
        return chunks

    @property
    def all_schemas(self) -> list[dict[str, Any]]:
        """所有结构化 Schema 描述"""
        extractor = SchemaExtractor()
        return [extractor.extract(doc) for doc in self.excel_documents]

    def summary(self) -> dict[str, Any]:
        return {
            "pdf_count": len(self.pdf_documents),
            "excel_count": len(self.excel_documents),
            "total_chunks": len(self.all_chunks),
            "total_schemas": len(self.all_schemas),
            "error_count": len(self.errors),
        }


# ---------------------------------------------------------------------------
# ETL Pipeline
# ---------------------------------------------------------------------------


class ETLPipeline:
    """
    ETL 主流水线。

    使用示例::

        pipeline = ETLPipeline(
            pdf_backend="pymupdf",
            source_dir="./data",
        )
        pipeline.on_pdf_error = lambda fp, e: print(f"跳过: {fp}")
        result = pipeline.run()
        print(result.summary())
    """

    # 支持的文件扩展名
    PDF_EXTENSIONS = {".pdf"}
    EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb", ".csv"}

    def __init__(
        self,
        source_dir: str,
        pdf_backend: str = "pymupdf",
        pdf_backend_kwargs: dict[str, Any] | None = None,
        excel_processor: ExcelProcessor | None = None,
        recursive: bool = True,
    ):
        self.source_dir = Path(source_dir).resolve()
        self.recursive = recursive
        self.pdf_parser = PDFParser(
            backend=pdf_backend,
            backend_kwargs=pdf_backend_kwargs,
        )
        self.excel_processor = excel_processor or ExcelProcessor()

        # 错误回调 —— 用户可以覆盖
        self.on_pdf_error: Callable[[str, Exception], None] = lambda fp, e: logger.error(
            "PDF 解析失败: %s → %s", fp, e
        )
        self.on_excel_error: Callable[[str, Exception], None] = lambda fp, e: logger.error(
            "Excel 处理失败: %s → %s", fp, e
        )

    def run(self) -> ETLResult:
        """执行完整的 ETL 流程"""
        if not self.source_dir.is_dir():
            raise NotADirectoryError(f"源目录不存在: {self.source_dir}")

        logger.info("ETLPipeline 启动，源目录: %s", self.source_dir)

        all_files = list(self._iter_files())

        pdf_files = [f for f in all_files if f.suffix.lower() in self.PDF_EXTENSIONS]
        excel_files = [f for f in all_files if f.suffix.lower() in self.EXCEL_EXTENSIONS]

        logger.info(
            "发现 %d 个 PDF, %d 个 Excel/CSV",
            len(pdf_files),
            len(excel_files),
        )

        result = ETLResult(source_dir=str(self.source_dir))

        # 处理 PDF
        for fp in pdf_files:
            try:
                doc = self.pdf_parser.parse(str(fp))
                result.pdf_documents.append(doc)
            except Exception as e:
                result.errors.append({"file": str(fp), "type": "pdf", "error": str(e)})
                self.on_pdf_error(str(fp), e)

        # 处理 Excel
        for fp in excel_files:
            try:
                doc = self.excel_processor.process(str(fp))
                result.excel_documents.append(doc)
            except Exception as e:
                result.errors.append({"file": str(fp), "type": "excel", "error": str(e)})
                self.on_excel_error(str(fp), e)

        logger.info("ETLPipeline 完成: %s", result.summary())
        return result

    def _iter_files(self):
        """遍历源目录中的所有文件"""
        pattern = "**/*" if self.recursive else "*"
        for path in self.source_dir.glob(pattern):
            if path.is_file():
                yield path

    # ------------------------------------------------------------------
    # 便捷: 导出为统一 JSONL
    # ------------------------------------------------------------------

    def export_jsonl(self, result: ETLResult, output_path: str) -> None:
        """将 ETL 结果导出为 JSONL 文件（每行一个 chunk）"""
        with open(output_path, "w", encoding="utf-8") as f:
            for chunk in result.all_chunks:
                f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
        logger.info("已导出 %d chunks → %s", len(result.all_chunks), output_path)
