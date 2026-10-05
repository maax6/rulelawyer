"""Index Qdrant embarqué, BM25 + dense, RRF puis reranking avec seuil."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import bm25s
from qdrant_client import QdrantClient, models

from rulelawyer.ingest import Chunk, ChunkPage
from rulelawyer.retrieval_config import DEFAULT_THRESHOLD


class RetrievalModels(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def rerank(self, question: str, texts: list[str]) -> list[float]: ...


class BGEModels:
    """Modèles locaux, chargés seulement par ask, périphérique auto."""

    name = "BAAI/bge-m3:dense:v1"

    @cached_property
    def encoder(self) -> Any:
        from FlagEmbedding import BGEM3FlagModel

        return BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)

    @cached_property
    def ranker(self) -> Any:
        from FlagEmbedding import FlagReranker

        return FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=False)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if any(len(self.encoder.tokenizer(t)["input_ids"]) > 8192 for t in texts):
            raise ValueError(
                "Section trop longue pour BGE-M3 ; découpage sémantique requis."
            )
        vectors = self.encoder.encode(
            texts,
            batch_size=4,
            max_length=8192,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )["dense_vecs"]
        return [[float(value) for value in vector] for vector in vectors]

    def rerank(self, question: str, texts: list[str]) -> list[float]:
        pairs = [[question, text] for text in texts]
        if any(len(self.ranker.tokenizer(q, t)["input_ids"]) > 8192 for q, t in pairs):
            raise ValueError(
                "Contexte trop long pour le reranker ; aucun texte tronqué."
            )
        scores = self.ranker.compute_score(
            pairs,
            normalize=True,
            batch_size=4,
            max_length=8192,
        )
        if isinstance(scores, float):
            return [scores]
        return [float(score) for score in scores]


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    page: ChunkPage
    score: float
    rrf_score: float
    bm25_rank: int | None
    dense_rank: int | None


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), start=1):
            scores[key] += 1 / (k + rank)
    return dict(scores)


def _tokens(texts: list[str]) -> Any:
    # Les nombres d'un seul chiffre et les noms de jeu doivent survivre.
    return bm25s.tokenize(
        texts,
        token_pattern=r"(?u)\b\w+\b",
        stopwords=[],
        show_progress=False,
    )


class HybridIndex:
    def __init__(
        self,
        chunks: list[Chunk],
        client: QdrantClient,
        backend: RetrievalModels,
    ) -> None:
        if not chunks or len({c.book_id for c in chunks}) != 1:
            raise ValueError(
                "L'index de démonstration attend les sections d'un seul livre."
            )
        self.chunks = chunks
        self.client = client
        self.backend = backend
        fingerprint = hashlib.sha256(backend.name.encode())
        for chunk in chunks:
            fingerprint.update(chunk.model_dump_json().encode())
        self.collection = "rules_" + fingerprint.hexdigest()
        self.by_id = {str(UUID(c.id[:32])): c for c in chunks}
        self.keys = list(self.by_id)
        if len(self.keys) != len(chunks):
            raise ValueError("Identifiants de chunks dupliqués.")
        self.sparse = bm25s.BM25()
        self.sparse.index(_tokens([c.text for c in chunks]), show_progress=False)
        complete = client.collection_exists(self.collection) and client.count(
            self.collection, exact=True
        ).count == len(chunks)
        if not complete:
            vectors = backend.embed([c.text for c in chunks])
            if len(vectors) != len(chunks) or not vectors[0]:
                raise ValueError("Nombre ou dimension des embeddings invalide.")
            if client.collection_exists(self.collection):
                client.delete_collection(self.collection)
            client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(
                    size=len(vectors[0]),
                    distance=models.Distance.COSINE,
                ),
            )
            client.upsert(
                self.collection,
                points=[
                    models.PointStruct(
                        id=key, vector=vector, payload={"book_id": chunk.book_id}
                    )
                    for key, chunk, vector in zip(
                        self.keys, chunks, vectors, strict=True
                    )
                ],
                wait=True,
            )

    def _lexical(self, question: str, limit: int) -> list[str]:
        ids, scores = self.sparse.retrieve(
            _tokens([question]),
            k=limit,
            show_progress=False,
        )
        return [
            self.keys[int(index)]
            for index, score in zip(ids[0], scores[0], strict=True)
            if float(score) > 0
        ]

    def _dense(self, question: str, limit: int) -> list[str]:
        vector = self.backend.embed([question])[0]
        result = self.client.query_points(
            self.collection,
            query=vector,
            limit=limit,
            with_payload=False,
        )
        return [str(point.id) for point in result.points]

    def search(
        self,
        question: str,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        top_k: int = 6,
    ) -> list[SearchHit]:
        if not question.strip():
            raise ValueError("La question est vide.")
        if not 0 <= threshold <= 1 or top_k < 1:
            raise ValueError("Seuil ou nombre de résultats invalide.")
        limit = min(30, len(self.chunks))
        with ThreadPoolExecutor(max_workers=2) as pool:
            lexical = pool.submit(self._lexical, question, limit)
            dense = pool.submit(self._dense, question, limit)
            rankings = [lexical.result(), dense.result()]
        fused = reciprocal_rank_fusion(rankings)
        candidates = [
            (key, page)
            for key in sorted(fused, key=lambda key: fused[key], reverse=True)
            for page in self.by_id[key].pages
        ]
        # Les chunks restent des sections. Le dernier reranking choisit le
        # passage et son folio AVANT de donner le texte au modèle génératif.
        scores = self.backend.rerank(
            question,
            [
                f"{self.by_id[key].section_path}\n\n{page.text}"
                for key, page in candidates
            ],
        )
        ranks = [{key: n for n, key in enumerate(r, 1)} for r in rankings]
        hits = []
        for (key, page), score in zip(candidates, scores, strict=True):
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Le reranker a renvoyé un score invalide.")
            hits.append(
                SearchHit(
                    self.by_id[key],
                    page,
                    score,
                    fused[key],
                    ranks[0].get(key),
                    ranks[1].get(key),
                )
            )
        ordered = sorted(hits, key=lambda hit: (hit.score, hit.rrf_score), reverse=True)
        # Le seuil décide si la requête dispose d'une preuve candidate. Il ne
        # supprime pas les preuves complémentaires d'une question composée,
        # auxquelles le reranker attribue souvent un score absolu très faible.
        if not ordered or ordered[0].score < threshold:
            return []
        selected: list[SearchHit] = []
        seen: set[tuple[str, int]] = set()
        for hit in ordered:
            page_key = (hit.chunk.book_id, hit.page.pdf_page)
            if page_key in seen:
                continue
            seen.add(page_key)
            selected.append(hit)
            if len(selected) == top_k:
                break
        return selected


@contextmanager
def open_index(
    chunks: list[Chunk],
    path: Path,
    backend: RetrievalModels,
) -> Iterator[HybridIndex]:
    path.mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=str(path))
    try:
        yield HybridIndex(chunks, client, backend)
    finally:
        client.close()
