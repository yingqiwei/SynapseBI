"""
模块1：数据接入与预处理管线 (ETL)
===================================
负责解析企业内部异构数据（PDF报告、Excel表格等），
输出结构化的文档块、表格数据与元数据 Schema。
"""

from .pipeline import ETLPipeline
from .pdf_parser import PDFParser
from .excel_processor import ExcelProcessor, SchemaExtractor

__all__ = [
    "ETLPipeline",
    "PDFParser",
    "ExcelProcessor",
    "SchemaExtractor",
]
