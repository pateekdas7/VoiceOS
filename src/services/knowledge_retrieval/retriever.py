"""RelevanceRetriever — query-aware snippet retrieval.

Wraps the VectorStore and DocumentEmbedder to produce ranked Snippet lists
suitable for injection into the ResponsePlan.retrieval field.

Architecture: V2 Ch20 (Knowledge Retrieval).
"""

from __future__ import annotations

import logging

from src.libs.contracts.response_plan import Snippet

from .embedder import DocumentEmbedder
from .store import VectorStore

logger = logging.getLogger(__name__)

_MIN_SCORE = 0.01


class RelevanceRetriever:
    """Retrieves relevant Snippets for a query string.

    Args:
        store: VectorStore to search.
        embedder: DocumentEmbedder to embed the query.
        top_k: Maximum number of snippets to return.
        min_score: Minimum cosine score to include a result.
    """

    def __init__(
        self,
        store: VectorStore,
        embedder: DocumentEmbedder,
        top_k: int = 3,
        min_score: float = _MIN_SCORE,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._min_score = min_score

    def retrieve(self, query: str) -> list[Snippet]:
        """Retrieve and rank snippets relevant to the query.

        Args:
            query: The customer utterance or topic to query for.

        Returns:
            Ranked list of Snippet objects (highest relevance first).
        """
        if not query.strip():
            return []

        query_vec = self._embedder.embed(query)
        if not query_vec:
            return []

        results = self._store.search(query_vec, top_k=self._top_k)

        snippets = [
            Snippet(source=source, content=text, relevance_score=round(score, 4))
            for _, text, source, score in results
            if score >= self._min_score
        ]

        logger.debug(
            "RelevanceRetriever: %d snippets retrieved for query %r",
            len(snippets),
            query[:60],
        )
        return snippets
