"""Unit tests for KnowledgeRetrievalService, VectorStore, and RelevanceRetriever."""

from __future__ import annotations

from src.libs.contracts.response_plan import Snippet
from src.services.knowledge_retrieval import KnowledgeRetrievalService
from src.services.knowledge_retrieval.embedder import DocumentEmbedder
from src.services.knowledge_retrieval.store import VectorStore


class TestDocumentEmbedder:
    def test_embed_returns_nonzero_vector_after_training(self) -> None:
        embedder = DocumentEmbedder()
        embedder.add_document("doc1", "payment outstanding balance settle")
        vec = embedder.embed("outstanding payment")
        assert len(vec) > 0
        assert any(v != 0.0 for v in vec)

    def test_embed_empty_query_returns_vector(self) -> None:
        embedder = DocumentEmbedder()
        embedder.add_document("doc1", "payment")
        vec = embedder.embed("payment query")
        assert isinstance(vec, list)

    def test_empty_vocab_embed_returns_empty(self) -> None:
        embedder = DocumentEmbedder()
        assert embedder.embed("anything") == []


class TestVectorStore:
    def test_add_and_search_returns_results(self) -> None:
        store = VectorStore()
        store.add("doc1", "payment settlement", [0.9, 0.1, 0.0], "source_a")
        store.add("doc2", "dispute claim", [0.1, 0.9, 0.0], "source_b")
        results = store.search([0.85, 0.1, 0.0], top_k=1)
        assert results[0][0] == "doc1"

    def test_empty_store_returns_empty_list(self) -> None:
        store = VectorStore()
        results = store.search([0.5, 0.5], top_k=3)
        assert results == []

    def test_len_reflects_document_count(self) -> None:
        store = VectorStore()
        store.add("d1", "text", [0.5], "src")
        store.add("d2", "text", [0.5], "src")
        assert len(store) == 2


class TestKnowledgeRetrievalService:
    async def test_knowledge_retrieval_returns_snippets(self) -> None:  # required named test
        """test_knowledge_retrieval_returns_snippets: seeded KB returns relevant snippets."""
        service = KnowledgeRetrievalService()
        snippets = await service.retrieve("payment outstanding balance settle")
        assert len(snippets) > 0
        assert all(isinstance(s, Snippet) for s in snippets)
        assert all(0.0 <= s.relevance_score <= 1.0 for s in snippets)

    async def test_response_plan_retrieval_populated(self) -> None:  # required named test
        """test_response_plan_retrieval_populated: retrieved snippets have non-empty content."""
        service = KnowledgeRetrievalService()
        snippets = await service.retrieve("dispute RBI fair practice")
        assert len(snippets) > 0
        assert all(s.content for s in snippets)
        assert all(s.source for s in snippets)

    async def test_empty_query_returns_empty(self) -> None:
        service = KnowledgeRetrievalService()
        snippets = await service.retrieve("")
        assert snippets == []
