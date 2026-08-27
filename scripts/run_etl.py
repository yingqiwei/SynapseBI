"""CLI：运行 ETL 数据接入管线。

用法:
    python scripts/run_etl.py --source-dir ./data --pdf-backend pymupdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from synapsebi.etl import ETLPipeline  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SynapseBI ETL 管线")
    parser.add_argument("--source-dir", default="./data", help="数据源目录")
    parser.add_argument(
        "--pdf-backend",
        default="pymupdf",
        choices=["pymupdf", "unstructured", "paddleocr"],
        help="PDF 解析后端",
    )
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--export-jsonl", default=None, help="导出 chunks 的 JSONL 路径")
    args = parser.parse_args(argv)

    pipeline = ETLPipeline(
        source_dir=args.source_dir,
        pdf_backend=args.pdf_backend,
        recursive=args.recursive,
    )
    result = pipeline.run()
    print(result.summary())

    if args.export_jsonl:
        pipeline.export_jsonl(result, args.export_jsonl)


if __name__ == "__main__":
    main()
