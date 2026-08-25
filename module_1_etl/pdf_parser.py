"""
PDF 解析器
==========
利用 OCR 与版面分析工具解析带表格/图表的复杂 PDF，
输出结构化的文本块与表格数据。

支持后端：
  - PaddleOCR（本地离线 OCR）
  - Unstructured（版面分析 + 表格识别）
  - PyMuPDF（基础文本抽取，作为轻量回退方案）
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class PDFChunk:
    """解析后的文档片段"""

    page_number: int
    chunk_type: Literal["text", "table", "image", "header", "footer"]
    content: str  # 文本内容，或表格的 CSV / Markdown 表示
    bbox: tuple[float, float, float, float] | None = None  # (x0, y0, x1, y1)
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def chunk_id(self) -> str:
        raw = f"{self.page_number}:{self.chunk_type}:{self.content[:120]}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]


@dataclass
class PDFDocument:
    """一份 PDF 的完整解析结果"""

    file_path: str
    file_name: str
    total_pages: int
    chunks: list[PDFChunk] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def iter_text_chunks(self) -> Iterator[PDFChunk]:
        for c in self.chunks:
            if c.chunk_type in ("text", "header"):
                yield c

    def iter_table_chunks(self) -> Iterator[PDFChunk]:
        for c in self.chunks:
            if c.chunk_type == "table":
                yield c


# ---------------------------------------------------------------------------
# 后端基类
# ---------------------------------------------------------------------------


class BasePDFBackend:
    """PDF 解析后端的统一接口"""

    def parse(self, file_path: str) -> PDFDocument:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# PyMuPDF 轻量后端
# ---------------------------------------------------------------------------


class PyMuPDFBackend(BasePDFBackend):
    """基于 PyMuPDF (fitz) 的文本抽取后端 —— 不依赖 GPU，适合纯文本 PDF"""

    def parse(self, file_path: str) -> PDFDocument:
        import fitz  # pip install PyMuPDF

        doc = fitz.open(file_path)
        pdf_doc = PDFDocument(
            file_path=file_path,
            file_name=Path(file_path).name,
            total_pages=len(doc),
            raw_metadata=dict(doc.metadata),
        )

        for page_idx in range(len(doc)):
            page = doc[page_idx]

            # --- 文本块 ---
            text_blocks = page.get_text("blocks")
            for block in text_blocks:
                # get_text("blocks") 元组结构：
                # (x0, y0, x1, y1, 文本, 块编号 block_no, 块类型 block_type)
                # block_type: 0=文本, 1=图片
                x0, y0, x1, y1, text, _, block_type = block
                text = text.strip()
                if not text:
                    continue

                chunk_type = "text"
                if block_type == 1:
                    chunk_type = "image"

                pdf_doc.chunks.append(
                    PDFChunk(
                        page_number=page_idx + 1,
                        chunk_type=chunk_type,
                        content=text,
                        bbox=(x0, y0, x1, y1),
                    )
                )

            # --- 表格 ---
            tables = page.find_tables()
            for table in tables:
                df = table.to_pandas()
                pdf_doc.tables.append(
                    {
                        "page": page_idx + 1,
                        "bbox": tuple(table.bbox),
                        "rows": len(df),
                        "columns": len(df.columns),
                        "dataframe": df.to_dict(orient="records"),
                    }
                )
                pdf_doc.chunks.append(
                    PDFChunk(
                        page_number=page_idx + 1,
                        chunk_type="table",
                        content=df.to_markdown(index=False),
                        bbox=tuple(table.bbox),
                    )
                )

        doc.close()
        logger.info(
            "PyMuPDF: parsed %s → %d chunks, %d tables",
            pdf_doc.file_name,
            len(pdf_doc.chunks),
            len(pdf_doc.tables),
        )
        return pdf_doc


# ---------------------------------------------------------------------------
# Unstructured 后端（版面分析）
# ---------------------------------------------------------------------------


class UnstructuredBackend(BasePDFBackend):
    """
    基于 Unstructured 库的版面分析后端。
    支持分区（partition）策略：auto / fast / hi_res。
    hi_res 模式会调用 OCR 对扫描件做文字识别。
    """

    def __init__(self, strategy: str = "auto", ocr_language: str = "chi_sim+eng"):
        self.strategy = strategy
        self.ocr_language = ocr_language

    def parse(self, file_path: str) -> PDFDocument:
        from unstructured.partition.pdf import partition_pdf

        elements = partition_pdf(
            filename=file_path,
            strategy=self.strategy,
            languages=[self.ocr_language],
            infer_table_structure=True,
        )

        pdf_doc = PDFDocument(
            file_path=file_path,
            file_name=Path(file_path).name,
            total_pages=-1,  # Unstructured 不直接暴露总页数
        )

        for el in elements:
            el_dict = el.to_dict()
            el_type = el_dict.get("type", "UncategorizedText")

            chunk_type: Literal["text", "table", "image", "header", "footer"] = "text"

            if "Table" in el_type:
                chunk_type = "table"
                pdf_doc.tables.append(
                    {
                        "type": el_type,
                        "text": el_dict.get("text", ""),
                        "metadata": el_dict.get("metadata", {}),
                    }
                )
            elif "Header" in el_type:
                chunk_type = "header"
            elif "Footer" in el_type:
                chunk_type = "footer"
            elif "Image" in el_type:
                chunk_type = "image"

            metadata = el_dict.get("metadata", {})
            page_number = metadata.get("page_number", 1)

            pdf_doc.chunks.append(
                PDFChunk(
                    page_number=int(page_number) if page_number else 1,
                    chunk_type=chunk_type,
                    content=el_dict.get("text", ""),
                    confidence=1.0,
                    metadata=metadata,
                )
            )

        logger.info(
            "Unstructured: parsed %s → %d chunks",
            pdf_doc.file_name,
            len(pdf_doc.chunks),
        )
        return pdf_doc


# ---------------------------------------------------------------------------
# PaddleOCR 后端（本地 OCR）
# ---------------------------------------------------------------------------


class PaddleOCRBackend(BasePDFBackend):
    """
    使用 PaddleOCR 对 PDF 逐页做 OCR 识别。
    适合扫描件、图片型 PDF。
    """

    def __init__(self, lang: str = "ch", use_gpu: bool = False):
        self.lang = lang
        self.use_gpu = use_gpu

    def parse(self, file_path: str) -> PDFDocument:
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise ImportError(
                "请安装 PaddleOCR: pip install paddlepaddle paddleocr"
            )

        import fitz  # 用于 PDF → 图片转换

        ocr = PaddleOCR(lang=self.lang, use_angle_cls=True, use_gpu=self.use_gpu)

        pdf_doc = PDFDocument(
            file_path=file_path,
            file_name=Path(file_path).name,
            total_pages=0,
        )

        doc = fitz.open(file_path)
        pdf_doc.total_pages = len(doc)

        for page_idx in range(len(doc)):
            page = doc[page_idx]

            # PDF 页面 → PNG 图片
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")

            # OCR 识别
            results = ocr.ocr(img_bytes, cls=True)
            if results is None or results[0] is None:
                continue

            for line in results[0]:
                bbox_points, (text, confidence) = line
                text = text.strip()
                if not text:
                    continue

                # bbox_points 是 [(x1,y1), (x2,y2), (x3,y3), (x4,y4)]
                xs = [p[0] for p in bbox_points]
                ys = [p[1] for p in bbox_points]

                pdf_doc.chunks.append(
                    PDFChunk(
                        page_number=page_idx + 1,
                        chunk_type="text",
                        content=text,
                        bbox=(min(xs), min(ys), max(xs), max(ys)),
                        confidence=float(confidence),
                    )
                )

        doc.close()
        logger.info(
            "PaddleOCR: parsed %s → %d chunks",
            pdf_doc.file_name,
            len(pdf_doc.chunks),
        )
        return pdf_doc


# ---------------------------------------------------------------------------
# 统一 PDFParser
# ---------------------------------------------------------------------------


class PDFParser:
    """
    统一的 PDF 解析器入口。

    使用示例::

        parser = PDFParser(backend="pymupdf")
        doc = parser.parse("report.pdf")
        for chunk in doc.iter_text_chunks():
            print(chunk.content)
    """

    BACKENDS: dict[str, type[BasePDFBackend]] = {
        "pymupdf": PyMuPDFBackend,
        "unstructured": UnstructuredBackend,
        "paddleocr": PaddleOCRBackend,
    }

    def __init__(
        self,
        backend: str = "pymupdf",
        backend_kwargs: dict[str, Any] | None = None,
    ):
        if backend not in self.BACKENDS:
            raise ValueError(
                f"不支持的 PDF 后端: {backend}，可选: {list(self.BACKENDS)}"
            )
        kwargs = backend_kwargs or {}
        self._backend = self.BACKENDS[backend](**kwargs)
        self._backend_name = backend

    def parse(self, file_path: str) -> PDFDocument:
        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"PDF 文件不存在: {file_path}")
        logger.info("PDFParser [%s] 开始解析: %s", self._backend_name, file_path)
        return self._backend.parse(file_path)

    def parse_batch(self, file_paths: list[str]) -> list[PDFDocument]:
        return [self.parse(fp) for fp in file_paths]

    @property
    def backend_name(self) -> str:
        return self._backend_name
