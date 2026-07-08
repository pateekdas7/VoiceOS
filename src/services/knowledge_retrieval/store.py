"""VectorStore — in-memory vector store for Sprint-012.

Stores document embeddings and performs cosine-similarity retrieval.
Sprint-013 will replace this with a Redis + HNSW-backed store (V3 Ch10).

Architecture: V2 Ch20 (Knowledge Retrieval).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _StoredDoc:
    doc_id: str
    text: str
    embedding: list[float]
    source: str


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two L2-normalised vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    # Vectors are already L2-normalised by the embedder.
    return max(-1.0, min(1.0, dot))


class VectorStore:
    """In-memory document embedding store with cosine-similarity retrieval.

    Architecture: V2 Ch20; Sprint-012 (in-memory; Redis+HNSW in Sprint-013).
    """

    def __init__(self) -> None:
        self._docs: list[_StoredDoc] = []

    def add(self, doc_id: str, text: str, embedding: list[float], source: str = "kb") -> None:
        """Add a document embedding to the store.

        Args:
            doc_id: Unique document identifier.
            text: Original document text (returned in search results).
            embedding: Pre-computed embedding vector.
            source: Source identifier for audit (e.g., 'faq_v3').
        """
        self._docs.append(_StoredDoc(doc_id=doc_id, text=text, embedding=embedding, source=source))

    def search(self, query_embedding: list[float], top_k: int = 3) -> list[tuple[str, str, str, float]]:
        """Return the top-k most similar documents.

        Args:
            query_embedding: Query vector from the embedder.
            top_k: Number of results to return.

        Returns:
            List of (doc_id, text, source, score) tuples, highest score first.
        """
        if not self._docs or not query_embedding:
            return []

        scored = [(doc.doc_id, doc.text, doc.source, _cosine(query_embedding, doc.embedding)) for doc in self._docs]
        scored.sort(key=lambda t: t[3], reverse=True)
        return scored[:top_k]

    def __len__(self) -> int:
        return len(self._docs)
