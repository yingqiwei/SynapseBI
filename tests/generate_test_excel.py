"""
生成测试用 Excel 文件，包含各类"脏数据"：
  - 空行 / 空列
  - 重复行
  - 不规范日期格式
  - 异常值（离群值）
  - 混合类型列
  - 缺失值
  - 不规范列名（空格、大小写）
"""

import random
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# 输出目录
output_dir = Path(__file__).resolve().parent / "test_data"
output_dir.mkdir(parents=True, exist_ok=True)

# ============================================================
# Sheet 1: 销售数据（含各种问题）
# ============================================================

np.random.seed(42)
random.seed(42)

n = 50

# 基础数据
dates = [
    datetime(2025, 1, 15) + timedelta(days=random.randint(0, 180))
    for _ in range(n)
]
departments = random.choices(
    ["销售部", "市场部", "研发部", "财务部", "人事部", "技术部"],
    weights=[0.3, 0.2, 0.15, 0.1, 0.1, 0.15],
    k=n,
)
sales_amounts = [round(random.gauss(50000, 20000), 2) for _ in range(n)]
profits = [round(s * random.uniform(0.05, 0.35), 2) for s in sales_amounts]
employee_counts = [random.randint(3, 50) for _ in range(n)]

# ---- 故意制造脏数据 ----

# 1. 不规范列名（大小写混合 + 空格）
columns_raw = [
    " 日期 ",           # 前后空格
    "部门",             # 正常
    "SALES AMOUNT",    # 大写 + 空格
    "利润 (万元)",      # 含括号
    "员工 数量",        # 含空格
    "备注",             # 正常
]

df_sales = pd.DataFrame(
    {
        " 日期 ": pd.to_datetime(dates),
        "部门": departments,
        "SALES AMOUNT": sales_amounts,
        "利润 (万元)": profits,
        "员工 数量": employee_counts,
        "备注": random.choices(
            ["重要客户", "老客户", "", "续约", None, "新签"],
            k=n,
        ),
    }
)

# 2. 插入 2 个全空行（在位置 5, 20）
empty_row = pd.DataFrame([[pd.NaT, None, None, None, None, None]], columns=df_sales.columns)
df_sales = pd.concat(
    [df_sales.iloc[:5], empty_row, df_sales.iloc[5:20], empty_row, df_sales.iloc[20:]],
    ignore_index=True,
)

# 3. 插入 1 个全空列
df_sales["EMPTY_COL"] = None

# 4. 插入 3 行重复行（复制前 3 行追加到末尾）
duplicates = df_sales.iloc[:3].copy()
df_sales = pd.concat([df_sales, duplicates], ignore_index=True)

# 5. 插入异常值（极高销售额）
df_sales.loc[48, "SALES AMOUNT"] = 999999.99
df_sales.loc[49, "利润 (万元)"] = -50000.00  # 极端亏损

# 6. 不规范日期格式：几行改为字符串格式
df_sales.loc[10, " 日期 "] = "2025/03/15"
df_sales.loc[11, " 日期 "] = "2025年4月1日"
df_sales.loc[12, " 日期 "] = "invalid_date"

# 7. 制造缺失值
df_sales.loc[7, "SALES AMOUNT"] = None
df_sales.loc[8, "利润 (万元)"] = None
df_sales.loc[9, "部门"] = None

print(f"[Sheet1] 销售数据: {len(df_sales)} 行 x {len(df_sales.columns)} 列")

# ============================================================
# Sheet 2: 产品清单（另一个表格）
# ============================================================

products = [
    "智能网关", "数据分析平台", "云存储服务", "安全审计系统",
    "物联网传感器", "边缘计算节点", "AI 推理引擎", "区块链存证",
]

df_products = pd.DataFrame(
    {
        "产品 名称": products,
        "单价": [1299.00, 50000.00, 2999.00, 15800.00, 299.00, 3500.00, 88000.00, 5999.00],
        "库存": [120, 15, 200, 45, 0, 80, 8, 300],
        "上架状态": [True, True, False, True, True, False, True, False],
        "发布日期": pd.to_datetime(
            [
                "2024-01-15",
                "2024-03-20",
                "invalid",
                "2024-06-01",
                "2024-08-10",
                None,
                "2024-11-05",
                "2025-01-01",
            ],
            errors="coerce",
        ),
    }
)

# 空行
df_products = pd.concat(
    [df_products, pd.DataFrame([[None]*5], columns=df_products.columns)],
    ignore_index=True,
)

# 重复行
df_products = pd.concat([df_products, df_products.iloc[0:1].copy()], ignore_index=True)

print(f"[Sheet2] 产品清单: {len(df_products)} 行 x {len(df_products.columns)} 列")

# ============================================================
# 写入 Excel
# ============================================================

output_path = output_dir / "test_dirty_data.xlsx"
with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
    df_sales.to_excel(writer, sheet_name="销售数据", index=False)
    df_products.to_excel(writer, sheet_name="产品清单", index=False)

print(f"\n测试 Excel 已生成: {output_path}")
print(f"文件大小: {output_path.stat().st_size:,} bytes")
