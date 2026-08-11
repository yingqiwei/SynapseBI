"""
模块1 ETL 功能测试
==================
使用 test_dirty_data.xlsx 验证：
  1. ExcelProcessor -- 数据清洗与规范化
  2. ColumnProfile -- 列画像统计
  3. SchemaExtractor -- Schema/DDL 提取
  4. ETLPipeline -- 主流程编排

输出可视化对比：清洗前 vs 清洗后
"""

import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=UserWarning, module="pandas")

# 确保模块可导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from tabulate import tabulate

from module_1_etl import ETLPipeline, ExcelProcessor, SchemaExtractor

# ============================================================
# 配置
# ============================================================

TEST_FILE = Path(__file__).resolve().parent / "test_data" / "test_dirty_data.xlsx"
SEP = "=" * 80
SUB = "-" * 60


def print_section(title: str):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def print_sub(title: str):
    print(f"\n{SUB}")
    print(f"  {title}")
    print(SUB)


# ============================================================
# 阶段 1: 查看原始数据
# ============================================================

print_section("阶段 1: 原始数据预览")

raw_sheets = pd.read_excel(TEST_FILE, sheet_name=None)

for name, df in raw_sheets.items():
    print_sub(f"Sheet: [{name}] -- 原始数据")
    print(f"  行数: {len(df)}, 列数: {len(df.columns)}")
    print(f"  列名: {list(df.columns)}")
    print(f"  缺失值总量: {df.isna().sum().sum()}")
    print(f"  重复行数: {df.duplicated().sum()}")
    print(f"  全空行数: {df.isna().all(axis=1).sum()}")
    print(f"  全空列数: {df.isna().all(axis=0).sum()}")
    print()
    print(tabulate(df.head(10), headers="keys", tablefmt="grid", showindex=True, maxcolwidths=20))
    print(f"\n  ... 共 {len(df)} 行")

# ============================================================
# 阶段 2: ExcelProcessor 清洗
# ============================================================

print_section("阶段 2: ExcelProcessor 数据清洗")

processor = ExcelProcessor(
    drop_empty_rows=True,
    drop_empty_cols=True,
    drop_duplicates=True,
    normalize_dates=True,
    detect_outliers=True,
    outlier_std_threshold=3.0,
)

doc = processor.process(str(TEST_FILE))

for name, df_clean in doc.sheets.items():
    profile = doc.profiles[name]

    print_sub(f"Sheet: [{name}] -- 清洗后")
    print(f"  原始行数 -> 清洗后行数: {raw_sheets[name].shape[0]} -> {df_clean.shape[0]}")
    print(f"  原始列数 -> 清洗后列数: {raw_sheets[name].shape[1]} -> {df_clean.shape[1]}")
    print(f"  清洗后列名: {list(df_clean.columns)}")
    print(f"  删除空行: {profile.empty_row_count}")
    print(f"  删除空列: {profile.empty_col_count}")
    print(f"  删除重复: {profile.duplicate_row_count}")
    print()

    # 展示前 10 行清洗后数据
    print(tabulate(df_clean.head(10), headers="keys", tablefmt="grid", showindex=True, maxcolwidths=20))
    print(f"\n  ... 共 {len(df_clean)} 行")

    # ---- 列画像 ----
    print_sub(f"列画像统计")
    profile_rows = []
    for cp in profile.columns:
        profile_rows.append(
            [
                cp.name,
                cp.dtype_db,
                f"{cp.non_null_count}/{cp.non_null_count + cp.null_count}",
                f"{cp.null_ratio:.1%}",
                cp.unique_count,
                f"{cp.unique_ratio:.1%}",
                (
                    f"min={cp.min_value}, max={cp.max_value}, mean={cp.mean_value}"
                    if cp.dtype_db in ("integer", "float")
                    else str(cp.sample_values)[:60]
                ),
                f"{cp.outlier_count} ({cp.outlier_ratio:.1%})" if cp.outlier_count else "--",
            ]
        )

    print(
        tabulate(
            profile_rows,
            headers=["列名", "推断类型", "非空", "空值率", "唯一值数", "唯一率", "统计/样本", "异常值"],
            tablefmt="grid",
            maxcolwidths=30,
        )
    )

# ============================================================
# 阶段 3: Schema 提取
# ============================================================

print_section("阶段 3: SchemaExtractor -- Schema & DDL 提取")

extractor = SchemaExtractor()
schemas = extractor.extract(doc)

for sheet_name, schema_info in schemas["sheets"].items():
    print_sub(f"Schema: [{sheet_name}]")

    # 列定义
    col_rows = [
        [c["name"], c["type"], "Yes" if c["nullable"] else "No", c["unique_count"], str(c["sample"])[:60]]
        for c in schema_info["columns"]
    ]
    print(
        tabulate(
            col_rows,
            headers=["列名", "类型", "可空", "唯一值数", "样本值"],
            tablefmt="grid",
            maxcolwidths=30,
        )
    )

    # DDL
    print(f"\n  [DDL] 推断的 CREATE TABLE DDL:")
    print(f"  {SUB}")
    for line in schema_info["ddl"].split("\n"):
        print(f"    {line}")

# ============================================================
# 阶段 4: ETLPipeline 统一编排
# ============================================================

print_section("阶段 4: ETLPipeline 统一编排")

pipeline = ETLPipeline(
    source_dir=str(TEST_FILE.parent),
    pdf_backend="pymupdf",
    recursive=False,
)

# 自定义错误处理
pipeline.on_excel_error = lambda fp, e: print(f"  [ERROR] {fp}: {e}")

result = pipeline.run()

print(f"\n  ETL 结果摘要:")
print(f"    PDF 文档:   {len(result.pdf_documents)}")
print(f"    Excel 文档: {len(result.excel_documents)}")
print(f"    总产出块:   {len(result.all_chunks)}")
print(f"    Schema 数:  {len(result.all_schemas)}")
print(f"    错误数:     {len(result.errors)}")

# 导出 JSONL
jsonl_path = TEST_FILE.parent / "test_output.jsonl"
pipeline.export_jsonl(result, str(jsonl_path))
print(f"\n  [OK] 已导出 {len(result.all_chunks)} 条 chunks -> {jsonl_path}")

# 预览前 5 条 chunk
print_sub("Chunks 预览（前 5 条）")
for i, chunk in enumerate(result.all_chunks[:5]):
    print(f"  [{i+1}] source={chunk['source']}, type={chunk['type']}")
    content_preview = chunk["content"][:120]
    print(f"       content: {content_preview}...")
    print()

# ============================================================
# 阶段 5: 元数据总览
# ============================================================

print_section("阶段 5: 文件级元数据")

for excel_doc in result.excel_documents:
    print_sub(f"文件: {excel_doc.file_name}")
    meta = excel_doc.metadata
    print(f"  文件路径:   {meta['file_path']}")
    print(f"  Sheet 数:   {meta['sheet_count']}")
    print(f"  Sheet 名称: {meta['sheet_names']}")
    print(f"  总行数:     {meta['total_rows']}")
    print(f"  总列数:     {meta['total_columns']}")
    print(f"  文件哈希:   {meta['file_hash']}")
    print(f"  处理时间:   {meta['processed_at']}")

print(f"\n{'='*80}")
print(f"  [OK] 模块1 ETL 测试全部完成！")
print(f"{'='*80}")
