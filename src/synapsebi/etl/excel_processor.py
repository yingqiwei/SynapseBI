"""
Excel 数据清洗与规范化
======================
使用 Pandas 自动清洗、规范化 Excel 数据，提取元数据（Metadata）与结构化 Schema。

功能：
  - 多 Sheet 自动识别与合并
  - 空行/空列清理
  - 数据类型推断与强制转换
  - 日期列标准化
  - 重复行检测与去重
  - 异常值标记
  - Schema / Profile 自动生成
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

ColumnType = Literal[
    "string", "integer", "float", "date", "datetime", "boolean", "category", "unknown"
]


@dataclass
class ColumnProfile:
    """单个列的统计画像"""

    name: str
    dtype_db: ColumnType  # 推断的逻辑类型
    dtype_raw: str  # pandas 原始 dtype
    non_null_count: int
    null_count: int
    null_ratio: float
    unique_count: int
    unique_ratio: float
    sample_values: list[Any]  # 前 5 个非空样本
    # 数值列专用
    min_value: float | None = None
    max_value: float | None = None
    mean_value: float | None = None
    median_value: float | None = None
    std_value: float | None = None
    # 异常检测
    outlier_count: int = 0
    outlier_ratio: float = 0.0


@dataclass
class SheetProfile:
    """单个 Sheet 的画像"""

    sheet_name: str
    row_count: int
    column_count: int
    columns: list[ColumnProfile] = field(default_factory=list)
    duplicate_row_count: int = 0
    empty_row_count: int = 0
    empty_col_count: int = 0


@dataclass
class ExcelDocument:
    """一份 Excel 的完整处理结果"""

    file_path: str
    file_name: str
    sheet_count: int
    sheets: dict[str, pd.DataFrame] = field(default_factory=dict)
    profiles: dict[str, SheetProfile] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    cleaned_at: str = ""

    def to_records(self, sheet_name: str | None = None) -> list[dict]:
        """将指定 Sheet 转为字典列表（方便入库）"""
        if sheet_name:
            return self.sheets[sheet_name].to_dict(orient="records")
        all_records = []
        for name, df in self.sheets.items():
            df_copy = df.copy()
            df_copy["_sheet"] = name
            all_records.extend(df_copy.to_dict(orient="records"))
        return all_records


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _infer_column_type(series: pd.Series) -> ColumnType:
    """推断列的语义类型"""
    clean = series.dropna()
    if len(clean) == 0:
        return "unknown"

    # 布尔型
    if set(clean.unique()) <= {True, False, 0, 1, "True", "False", "true", "false", "0", "1"}:
        return "boolean"

    # 整数型
    if pd.api.types.is_integer_dtype(clean):
        return "integer"

    # 浮点型
    if pd.api.types.is_float_dtype(clean):
        return "float"

    # 日期/时间
    if pd.api.types.is_datetime64_any_dtype(clean):
        # 如果所有时间分量都是 0，推断为纯日期
        if (clean.dt.hour == 0).all() and (clean.dt.minute == 0).all():
            return "date"
        return "datetime"

    # 尝试字符串解析
    sample = clean.head(100)
    parsed = pd.to_datetime(sample, errors="coerce")
    if parsed.notna().mean() > 0.85:
        if (parsed.dt.hour == 0).all() and (parsed.dt.minute == 0).all():
            return "date"
        return "datetime"

    # 尝试数值
    numeric = pd.to_numeric(sample, errors="coerce")
    if numeric.notna().mean() > 0.85:
        if (numeric.dropna() % 1 == 0).all():
            return "integer"
        return "float"

    # 类别型（低基数）
    if clean.nunique() / len(clean) < 0.1:
        return "category"

    return "string"


def _coerce_boolean(value: Any) -> Any:
    """
    将单个值统一转换为布尔语义（保留缺失值）。

    pandas 读取 Excel 布尔单元格时通常会得到 1.0 / 0.0 浮点数，
    因此除了字符串之外还必须处理数值类型，避免 str(1.0) == "1.0"
    导致 True 被误判为 False。
    """
    if pd.isna(value):
        return pd.NA
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        return value == 1.0
    return str(value).strip().lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# Excel 处理器
# ---------------------------------------------------------------------------


class ExcelProcessor:
    """
    Excel 文件清洗与规范化处理器。

    使用示例::

        processor = ExcelProcessor()
        doc = processor.process("sales_data.xlsx")
        print(doc.profiles["Sheet1"])
    """

    def __init__(
        self,
        drop_empty_rows: bool = True,
        drop_empty_cols: bool = True,
        drop_duplicates: bool = True,
        normalize_dates: bool = True,
        detect_outliers: bool = True,
        outlier_std_threshold: float = 3.0,
        max_sample_values: int = 5,
    ):
        self.drop_empty_rows = drop_empty_rows
        self.drop_empty_cols = drop_empty_cols
        self.drop_duplicates = drop_duplicates
        self.normalize_dates = normalize_dates
        self.detect_outliers = detect_outliers
        self.outlier_std_threshold = outlier_std_threshold
        self.max_sample_values = max_sample_values

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def process(
        self,
        file_path: str,
        sheet_names: list[str] | None = None,
    ) -> ExcelDocument:
        """
        处理一个 Excel 文件的所有 Sheet，返回清洗结果与画像。
        """
        if not Path(file_path).is_file():
            raise FileNotFoundError(f"Excel 文件不存在: {file_path}")

        logger.info("ExcelProcessor 开始处理: %s", file_path)

        # 读取所有 Sheet
        raw_sheets = self._read_sheets(file_path, sheet_names)

        doc = ExcelDocument(
            file_path=file_path,
            file_name=Path(file_path).name,
            sheet_count=len(raw_sheets),
            cleaned_at=datetime.now().isoformat(),
        )

        for name, df in raw_sheets.items():
            logger.debug("处理 Sheet: %s (%d rows)", name, len(df))

            # 清洗流水线
            df_clean = self._clean_dataframe(df)

            # 推断并转换列类型
            df_clean = self._coerce_types(df_clean)

            doc.sheets[name] = df_clean

            # 画像
            doc.profiles[name] = self._profile_sheet(name, df, df_clean)

        # 全局元数据
        doc.metadata = self._build_metadata(doc)
        logger.info(
            "ExcelProcessor 完成: %s → %d sheets",
            doc.file_name,
            doc.sheet_count,
        )
        return doc

    # ------------------------------------------------------------------
    # 内部步骤
    # ------------------------------------------------------------------

    def _read_sheets(
        self, file_path: str, sheet_names: list[str] | None
    ) -> dict[str, pd.DataFrame]:
        """读取所有 Sheet（或指定 Sheet）"""
        xl = pd.ExcelFile(file_path)
        names = sheet_names or xl.sheet_names
        result: dict[str, pd.DataFrame] = {}
        for name in names:
            try:
                df = pd.read_excel(xl, sheet_name=name, header=0)
                result[name] = df
            except Exception as exc:
                logger.warning("跳过 Sheet '%s': %s", name, exc)
        if not result:
            raise ValueError(f"未能从 {file_path} 读取任何有效 Sheet")
        return result

    def _clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """清洗 DataFrame"""
        df = df.copy()

        # 规范化列名：去空格、统一为 snake_case
        df.columns = [
            str(col).strip().replace(" ", "_").replace("\n", "_").lower()
            for col in df.columns
        ]

        # 删除全空行
        if self.drop_empty_rows:
            before = len(df)
            df = df.dropna(how="all")
            if before != len(df):
                logger.debug("  删除 %d 全空行", before - len(df))

        # 删除全空列
        if self.drop_empty_cols:
            before = df.shape[1]
            df = df.dropna(axis=1, how="all")
            if before != df.shape[1]:
                logger.debug("  删除 %d 全空列", before - df.shape[1])

        # 去重
        if self.drop_duplicates:
            before = len(df)
            df = df.drop_duplicates()
            if before != len(df):
                logger.debug("  删除 %d 重复行", before - len(df))

        # 首行可能含表头重复 → 如果第一行的值与列名高度重复则删除
        if len(df) > 0:
            first_row = df.iloc[0].astype(str)
            col_names = pd.Series(df.columns, index=df.columns).astype(str)
            match_count = (first_row == col_names).sum()
            if match_count / len(df.columns) > 0.5:
                df = df.iloc[1:].reset_index(drop=True)
                logger.debug("  移除了疑似重复表头的首行")

        return df.reset_index(drop=True)

    def _coerce_types(self, df: pd.DataFrame) -> pd.DataFrame:
        """推断并转换列类型"""
        df = df.copy()

        for col in df.columns:
            col_type = _infer_column_type(df[col])

            if col_type == "date":
                converted = pd.to_datetime(df[col], errors="coerce")
                if converted.notna().sum() > 0:
                    df[col] = converted.dt.date
            elif col_type == "datetime":
                converted = pd.to_datetime(df[col], errors="coerce")
                if converted.notna().sum() > 0:
                    df[col] = converted
            elif col_type == "integer":
                numeric = pd.to_numeric(df[col], errors="coerce")
                if numeric.notna().sum() > 0:
                    # 检查是否都能无损转 int
                    if (numeric.dropna() % 1 == 0).all():
                        df[col] = numeric.astype("Int64")  # nullable int
                    else:
                        df[col] = numeric
            elif col_type == "float":
                numeric = pd.to_numeric(df[col], errors="coerce")
                if numeric.notna().sum() > 0:
                    df[col] = numeric
            elif col_type == "boolean":
                df[col] = df[col].apply(_coerce_boolean)

        return df

    # ------------------------------------------------------------------
    # 画像生成
    # ------------------------------------------------------------------

    def _profile_sheet(
        self, name: str, raw: pd.DataFrame, clean: pd.DataFrame
    ) -> SheetProfile:
        """生成 Sheet 的统计画像"""
        profile = SheetProfile(
            sheet_name=name,
            row_count=len(clean),
            column_count=len(clean.columns),
            duplicate_row_count=len(raw) - len(raw.drop_duplicates()),
            empty_row_count=raw.isna().all(axis=1).sum(),
            empty_col_count=raw.isna().all(axis=0).sum(),
        )

        for col in clean.columns:
            series = clean[col]
            col_type = _infer_column_type(series)

            cp = ColumnProfile(
                name=col,
                dtype_db=col_type,
                dtype_raw=str(series.dtype),
                non_null_count=series.notna().sum(),
                null_count=series.isna().sum(),
                null_ratio=round(series.isna().mean(), 4),
                unique_count=series.nunique(),
                unique_ratio=round(series.nunique() / max(len(series), 1), 4),
                sample_values=series.dropna().head(self.max_sample_values).tolist(),
            )

            # 数值列统计
            if col_type in ("integer", "float"):
                nums = pd.to_numeric(series, errors="coerce").dropna()
                if len(nums) > 0:
                    cp.min_value = round(float(nums.min()), 4)
                    cp.max_value = round(float(nums.max()), 4)
                    cp.mean_value = round(float(nums.mean()), 4)
                    cp.median_value = round(float(nums.median()), 4)
                    cp.std_value = round(float(nums.std()), 4)

                    # 异常值检测（3-sigma）
                    if self.detect_outliers:
                        z_scores = np.abs((nums - nums.mean()) / nums.std())
                        cp.outlier_count = int((z_scores > self.outlier_std_threshold).sum())
                        cp.outlier_ratio = round(cp.outlier_count / len(nums), 4)

            profile.columns.append(cp)

        return profile

    def _build_metadata(self, doc: ExcelDocument) -> dict[str, Any]:
        """生成文件级元数据"""
        total_rows = sum(s.row_count for s in doc.profiles.values())
        total_cols = sum(s.column_count for s in doc.profiles.values())
        return {
            "file_name": doc.file_name,
            "file_path": doc.file_path,
            "sheet_count": doc.sheet_count,
            "sheet_names": list(doc.sheets.keys()),
            "total_rows": total_rows,
            "total_columns": total_cols,
            "processed_at": doc.cleaned_at,
            "file_hash": self._file_hash(doc.file_path),
        }

    @staticmethod
    def _file_hash(file_path: str) -> str:
        sha = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()[:16]


# ---------------------------------------------------------------------------
# Schema 提取器（快捷入口）
# ---------------------------------------------------------------------------


class SchemaExtractor:
    """
    提取结构化 Schema 描述 —— 供 Text-to-SQL / 向量入库时使用。

    使用示例::

        extractor = SchemaExtractor()
        schema = extractor.extract(excel_doc)
        print(schema["sheets"]["Sheet1"]["ddl"])
    """

    def extract(self, doc: ExcelDocument) -> dict[str, Any]:
        result: dict[str, Any] = {
            "file_name": doc.file_name,
            "sheets": {},
        }
        for name, df in doc.sheets.items():
            profile = doc.profiles.get(name)
            result["sheets"][name] = {
                "row_count": len(df),
                "column_count": len(df.columns),
                "columns": [
                    {
                        "name": col,
                        "type": _infer_column_type(df[col]),
                        "nullable": bool(df[col].isna().any()),
                        "unique_count": int(df[col].nunique()),
                        "sample": df[col].dropna().head(3).tolist(),
                    }
                    for col in df.columns
                ],
                "ddl": self._to_ddl(name, df),
            }
        return result

    def _to_ddl(self, table_name: str, df: pd.DataFrame) -> str:
        """将 DataFrame 推断为 SQL CREATE TABLE DDL"""
        type_map: dict[ColumnType, str] = {
            "integer": "INTEGER",
            "float": "FLOAT",
            "date": "DATE",
            "datetime": "TIMESTAMP",
            "boolean": "BOOLEAN",
            "category": "VARCHAR(128)",
            "string": "TEXT",
            "unknown": "TEXT",
        }
        lines = [f"CREATE TABLE {table_name} ("]
        for col in df.columns:
            col_type = _infer_column_type(df[col])
            sql_type = type_map.get(col_type, "TEXT")
            lines.append(f"    {col} {sql_type},")
        lines[-1] = lines[-1].rstrip(",")
        lines.append(");")
        return "\n".join(lines)
