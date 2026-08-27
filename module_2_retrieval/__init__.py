"""
兼容层：module_2_retrieval → src/synapsebi/retrieval

业务代码已迁移到 src 布局，此包仅做导入转发，
保证旧路径的 import（如 from module_2_retrieval.search_engine import ...）继续可用。
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
for _p in (_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import synapsebi.retrieval.router as router  # noqa: E402
import synapsebi.retrieval.search_engine as search_engine  # noqa: E402
import synapsebi.retrieval.text_to_sql as text_to_sql  # noqa: E402
import synapsebi.retrieval.vector_store as vector_store  # noqa: E402
import synapsebi.retrieval as _package  # noqa: E402

sys.modules["module_2_retrieval.router"] = router
sys.modules["module_2_retrieval.search_engine"] = search_engine
sys.modules["module_2_retrieval.text_to_sql"] = text_to_sql
sys.modules["module_2_retrieval.vector_store"] = vector_store
sys.modules["module_2_retrieval"] = _package
