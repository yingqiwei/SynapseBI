"""
兼容层：module_3_frontend → src/synapsebi/frontend

业务代码已迁移到 src 布局，此包仅做导入转发，
保证旧路径的 import（如 from module_3_frontend.api import create_app）继续可用。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import synapsebi.frontend.api as api  # noqa: E402
import synapsebi.frontend.chart_exporter as chart_exporter  # noqa: E402
import synapsebi.frontend as _package  # noqa: E402

sys.modules["module_3_frontend.api"] = api
sys.modules["module_3_frontend.chart_exporter"] = chart_exporter
sys.modules["module_3_frontend"] = _package
