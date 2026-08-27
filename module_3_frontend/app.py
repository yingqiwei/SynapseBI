"""
兼容入口：module_3_frontend/app.py → src/synapsebi/frontend/app.py

Streamlit 需要直接执行脚本文件，因此保留一个转发入口，
实际业务代码位于 src 布局中，此文件不包含任何业务逻辑。
"""

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

if __name__ == "__main__":
    runpy.run_path(
        str(_SRC / "synapsebi" / "frontend" / "app.py"),
        run_name="__main__",
    )
