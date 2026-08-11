"""
向量数据库操作层
================
封装向量数据库（Milvus / Qdrant）的常见操作：
  - 文档切片 → 向量化（BGE / text2vec 系列）→ 入库
  - 语义相似度检索
  - 混合检索（向量 + 关键词过滤）

当前实现以 Qdrant 为主（轻量、Docker 友好），保留 Milvus 接口位置。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class TextChunk:
    """待入库的文本切片"""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    chunk_id: str = ""

    def __post_init__(self):
        if not self.chunk_id:
            self.chunk_id = hashlib.md5(
                (self.content[:200] + str(self.metadata)).encode()
            ).hexdigest()[:16]


@dataclass
class SearchResult:
    """单条检索结果"""

    chunk_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
    rank: int = 0


@dataclass
class SearchResponse:
    """一次检索的完整响应"""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total_hits: int = 0
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# 嵌入模型接口
# ---------------------------------------------------------------------------


class BaseEmbedder:
    """嵌入模型统一接口"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @property
    def dimension(self) -> int:
        raise NotImplementedError


class BGEEmbedder(BaseEmbedder):
    """
    BGE 系列嵌入模型（推荐 bge-large-zh-v1.5 用于中文）。

    依赖: pip install sentence-transformers
    """

    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("加载嵌入模型: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        # BGE 模型建议对查询添加前缀 "为这个句子生成表示以用于检索相关文章："
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        return self._load().get_sentence_embedding_dimension()


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI 兼容嵌入接口（如本地 vLLM 部署的 Qwen/Llama）"""

    def __init__(
        self,
        api_base: str = "http://localhost:8000/v1",
        model_name: str = "text-embedding-3-small",
        api_key: str = "not-needed",
    ):
        self.api_base = api_base
        self.model_name = model_name
        self.api_key = api_key
        self._dimension: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        client = OpenAI(base_url=self.api_base, api_key=self.api_key)
        resp = client.embeddings.create(model=self.model_name, input=texts)
        embeddings = [d.embedding for d in resp.data]
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])
        return embeddings

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            # 触发一次调用来获取维度
            self.embed(["dimension probe"])
        return self._dimension or 768


# ---------------------------------------------------------------------------
# 文本切片器
# ---------------------------------------------------------------------------


class TextSplitter:
    """
    将长文本按语义边界切分为适合向量入库的小块。

    策略：
      - 优先按段落（\\n\\n）分割
      - 段内按句子（。！？\\n）分割
      - 保证 chunk_size 附近截断，重叠 overlap_size
    """

    def __init__(
        self,
        chunk_size: int = 512,
        overlap_size: int = 64,
        separators: list[str] | None = None,
    ):
        self.chunk_size = chunk_size
        self.overlap_size = overlap_size
        self.separators = separators or ["\n\n", "\n", "。", "！", "？", ".", "!", "?", "；", ";"]

    def split(self, text: str, metadata: dict[str, Any] | None = None) -> list[TextChunk]:
        """将长文本切分为多个 TextChunk"""
        meta = metadata or {}
        segments = self._recursive_split(text.strip())
        chunks: list[TextChunk] = []
        for i, seg in enumerate(segments):
            chunks.append(
                TextChunk(
                    content=seg,
                    metadata={**meta, "chunk_index": i, "chunk_count": len(segments)},
                )
            )
        return chunks

    def _recursive_split(self, text: str) -> list[str]:
        """递归分割直到每个片段 ≤ chunk_size"""
        if len(text) <= self.chunk_size:
            return [text] if text else []

        # 选择最合适的 separator
        for sep in self.separators:
            if sep in text:
                parts = text.split(sep)
                # 合并短片段
                merged: list[str] = []
                buf = ""
                for part in parts:
                    candidate = buf + (sep if buf else "") + part
                    if len(candidate) <= self.chunk_size:
                        buf = candidate
                    else:
                        if buf:
                            merged.append(buf)
                        buf = part
                if buf:
                    merged.append(buf)

                # 递归处理仍然超长的片段
                final: list[str] = []
                for m in merged:
                    if len(m) > self.chunk_size:
                        final.extend(self._recursive_split(m))
                    else:
                        final.append(m)
                return final

        # 没有匹配的 separator → 硬截断
        hard_chunks: list[str] = []
        for i in range(0, len(text), self.chunk_size - self.overlap_size):
            hard_chunks.append(text[i : i + self.chunk_size])
        return hard_chunks


# ---------------------------------------------------------------------------
# 向量数据库（Qdrant）
# ---------------------------------------------------------------------------


class VectorStore:
    """
    向量数据库操作封装。

    支持后端：
      - qdrant (默认): pip install qdrant-client
      - milvus (预留): pip install pymilvus

    使用示例::

        embedder = BGEEmbedder()
        store = VectorStore(embedder=embedder, host="localhost", port=6333)
        store.create_collection("docs", dimension=1024)
        store.upsert_chunks(chunks)
        results = store.search("今年的销售额是多少？", top_k=5)
    """

    def __init__(
        self,
        embedder: BaseEmbedder,
        backend: Literal["qdrant", "milvus"] = "qdrant",
        host: str = "localhost",
        port: int = 6333,
        grpc_port: int = 6334,
        api_key: str | None = None,
    ):
        self.embedder = embedder
        self.backend = backend
        self.host = host
        self.port = port
        self.grpc_port = grpc_port
        self.api_key = api_key
        self._client: Any = None
        self._splitter = TextSplitter()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    @property
    def client(self):
        if self._client is None:
            if self.backend == "qdrant":
                from qdrant_client import QdrantClient

                self._client = QdrantClient(
                    host=self.host,
                    port=self.port,
                    grpc_port=self.grpc_port,
                    api_key=self.api_key,
                    prefer_grpc=False,
                )
                logger.info("已连接 Qdrant: %s:%s", self.host, self.port)
            else:
                raise NotImplementedError("Milvus 后端尚未实现，请使用 'qdrant'")
        return self._client

    def close(self):
        if self._client is not None:
            if self.backend == "qdrant":
                self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Collection 管理
    # ------------------------------------------------------------------

    def create_collection(
        self,
        collection_name: str,
        dimension: int | None = None,
        distance: str = "Cosine",
        recreate: bool = False,
    ) -> None:
        """创建向量集合"""
        dim = dimension or self.embedder.dimension

        from qdrant_client.http import models as qmodels

        if recreate:
            self._recreate_collection_inner(collection_name, dim, distance)
            return

        # 检查是否已存在
        collections = [c.name for c in self.client.get_collections().collections]
        if collection_name not in collections:
            self._create_collection_inner(collection_name, dim, distance)
        else:
            logger.info("Collection '%s' 已存在，跳过创建", collection_name)

    def _create_collection_inner(self, name: str, dim: int, distance: str):
        from qdrant_client.http import models as qmodels

        dist_map = {
            "Cosine": qmodels.Distance.COSINE,
            "Euclid": qmodels.Distance.EUCLID,
            "Dot": qmodels.Distance.DOT,
        }
        self.client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(
                size=dim,
                distance=dist_map.get(distance, qmodels.Distance.COSINE),
            ),
        )
        logger.info("创建 Collection '%s' (dim=%d, distance=%s)", name, dim, distance)

    def _recreate_collection_inner(self, name: str, dim: int, distance: str):
        from qdrant_client.http import models as qmodels

        dist_map = {
            "Cosine": qmodels.Distance.COSINE,
            "Euclid": qmodels.Distance.EUCLID,
            "Dot": qmodels.Distance.DOT,
        }
        self.client.recreate_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(
                size=dim,
                distance=dist_map.get(distance, qmodels.Distance.COSINE),
            ),
        )
        logger.info("重建 Collection '%s'", name)

    def list_collections(self) -> list[str]:
        return [c.name for c in self.client.get_collections().collections]

    def delete_collection(self, collection_name: str) -> None:
        self.client.delete_collection(collection_name)
        logger.info("删除 Collection '%s'", collection_name)

    # ------------------------------------------------------------------
    # 数据写入
    # ------------------------------------------------------------------

    def upsert_chunks(
        self,
        chunks: list[TextChunk],
        collection_name: str,
        batch_size: int = 64,
    ) -> int:
        """将 TextChunk 列表向量化并写入向量库。返回写入条数。"""
        from qdrant_client.http import models as qmodels

        if not chunks:
            return 0

        total = 0
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [c.content for c in batch]
            vectors = self.embedder.embed(texts)

            points = []
            for chunk, vector in zip(batch, vectors):
                point_id = str(uuid.uuid4())
                points.append(
                    qmodels.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "chunk_id": chunk.chunk_id,
                            "content": chunk.content,
                            **chunk.metadata,
                        },
                    )
                )

            self.client.upsert(
                collection_name=collection_name,
                points=points,
            )
            total += len(points)

        logger.info("写入 %d 条向量 → '%s'", total, collection_name)
        return total

    def upsert_texts(
        self,
        texts: list[str],
        collection_name: str,
        metadata_list: list[dict[str, Any]] | None = None,
    ) -> int:
        """便捷方法：直接写入原始文本（内部自动切片）"""
        all_chunks: list[TextChunk] = []
        defaults = metadata_list or [{}] * len(texts)
        for text, meta in zip(texts, defaults):
            all_chunks.extend(self._splitter.split(text, meta))
        return self.upsert_chunks(all_chunks, collection_name)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
        filter_condition: dict[str, Any] | None = None,
    ) -> SearchResponse:
        """语义相似度检索"""
        import time

        start = time.perf_counter()

        query_vector = self.embedder.embed([query])[0]

        from qdrant_client.http import models as qmodels

        query_filter = None
        if filter_condition:
            query_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key=k,
                        match=qmodels.MatchValue(value=v),
                    )
                    for k, v in filter_condition.items()
                ]
            )

        hits = self.client.search(
            collection_name=collection_name,
            query_vector=query_vector,
            limit=top_k,
            score_threshold=score_threshold,
            query_filter=query_filter,
        )

        elapsed = (time.perf_counter() - start) * 1000

        results = [
            SearchResult(
                chunk_id=h.payload.get("chunk_id", str(h.id)),
                content=h.payload.get("content", ""),
                score=h.score,
                metadata={
                    k: v
                    for k, v in h.payload.items()
                    if k not in ("chunk_id", "content")
                },
                rank=idx + 1,
            )
            for idx, h in enumerate(hits)
        ]

        logger.info(
            "检索 '%s' → %d 结果 (%.1fms)",
            query[:60],
            len(results),
            elapsed,
        )
        return SearchResponse(
            query=query,
            results=results,
            total_hits=len(results),
            elapsed_ms=round(elapsed, 1),
        )
