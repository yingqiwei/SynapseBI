"""
兼容层：module_1_etl → src/synapsebi/etl

业务代码已迁移到 src 布局，此包仅做导入转发，
保证旧路径的 import（如 from module_1_etl import ETLPipeline）继续可用。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import synapsebi.etl.pipeline as pipeline  # noqa: E402
import synapsebi.etl.pdf_parser as pdf_parser  # noqa: E402
import synapsebi.etl.excel_processor as excel_processor  # noqa: E402
import synapsebi.etl as _package  # noqa: E402

sys.modules["module_1_etl.pipeline"] = pipeline
sys.modules["module_1_etl.pdf_parser"] = pdf_parser
sys.modules["module_1_etl.excel_processor"] = excel_processor
sys.modules["module_1_etl"] = _package
