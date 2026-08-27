"""CLI：运行 ETL 并将文档块向量化写入 Qdrant。

用法:
    python scripts/ingest_to_qdrant.py --source-dir ./data --collection default
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
from synapsebi.retrieval.vector_store import (  # noqa: E402
    BGEEmbedder,
    TextChunk,
    VectorStore,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SynapseBI 数据入库")
    parser.add_argument("--source-dir", default="./data", help="数据源目录")
    parser.add_argument("--collection", default="default", help="Qdrant 集合名")
    parser.add_argument(
        "--embedding-model",
        default="BAAI/bge-small-zh-v1.5",
        help="嵌入模型",
    )
    parser.add_argument("--host", default="localhost", help="Qdrant 地址")
    parser.add_argument("--port", type=int, default=6333, help="Qdrant 端口")
    parser.add_argument("--recreate", action="store_true", help="重建集合")
    args = parser.parse_args(argv)

    result = ETLPipeline(source_dir=args.source_dir).run()
    print("ETL:", result.summary())

    embedder = BGEEmbedder(model_name=args.embedding_model, device="cpu")
    store = VectorStore(embedder=embedder, host=args.host, port=args.port)
    store.create_collection(
        args.collection,
        dimension=embedder.dimension,
        recreate=args.recreate,
    )

    chunks = [
        TextChunk(
            content=c["content"],
            metadata={
                "source": c["source"],
                "source_type": c["source_type"],
                "type": c.get("type", ""),
                "page": c.get("page", 0),
            },
        )
        for c in result.all_chunks
    ]
    written = store.upsert_chunks(chunks, args.collection, batch_size=32)
    print(f"已写入 {written} 条向量 -> collection '{args.collection}'")


if __name__ == "__main__":
    main()
