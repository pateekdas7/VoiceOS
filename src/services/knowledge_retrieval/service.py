"""KnowledgeRetrievalService — pre-seeded knowledge base for debt collections.

Manages a VectorStore pre-seeded with RBI fair-practice guidelines, FAQs,
settlement guidelines, dispute procedures, and hardship plan information.

Snippets are used as evidence-only context in the LLM prompt (RI-5: they
are not authoritative facts; the LLM must not quote amounts from them).

Architecture: V2 Ch20 (Knowledge Retrieval); RI-5.
"""

from __future__ import annotations

import logging

from src.libs.contracts.response_plan import Snippet

from .embedder import DocumentEmbedder
from .retriever import RelevanceRetriever
from .store import VectorStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-seeded knowledge base — RBI FPC, FAQs, procedures
# ---------------------------------------------------------------------------

_SEED_DOCUMENTS: list[tuple[str, str, str]] = [
    (
        "rbi_fpc_001",
        "RBI Fair Practice Code requires that collection agents identify themselves and their organization at the start of every call. "
        "Agents must not use abusive or threatening language. Customers have the right to request written communication.",
        "rbi_fair_practice_code",
    ),
    (
        "rbi_fpc_002",
        "Customers may dispute a debt in writing. On receiving a written dispute, the collection activity must be suspended "
        "until the dispute is investigated and resolved. Agents must acknowledge disputes and provide a reference number.",
        "rbi_fair_practice_code",
    ),
    (
        "rbi_fpc_003",
        "Calls must not be made before 8:00 AM or after 7:00 PM local time. "
        "Repeated calls to third parties (relatives, employers) without customer consent are prohibited.",
        "rbi_fair_practice_code",
    ),
    (
        "settlement_001",
        "Settlement offers may be made for amounts at or above 50% of the outstanding balance. "
        "Any settlement must be documented in writing before payment is accepted. "
        "Partial settlement does not waive remaining dues unless explicitly stated in the agreement.",
        "settlement_guidelines",
    ),
    (
        "settlement_002",
        "Restructuring options include EMI revision, tenure extension, moratorium period, and one-time settlement. "
        "The agent must present the option that aligns with the customer's stated ability to pay.",
        "settlement_guidelines",
    ),
    (
        "hardship_001",
        "If a customer demonstrates genuine financial hardship (job loss, medical emergency, disability), "
        "a hardship plan may be offered. Hardship plans typically provide a 3-6 month moratorium "
        "followed by revised EMI amounts.",
        "hardship_procedures",
    ),
    (
        "dispute_001",
        "Dispute resolution procedure: (1) Acknowledge the dispute. (2) Assign a dispute reference number. "
        "(3) Suspend collection activity. (4) Investigate within 30 days. (5) Communicate resolution in writing.",
        "dispute_procedures",
    ),
    (
        "faq_001",
        "Q: Can I pay in instalments? A: Yes, an EMI restructuring plan may be available based on your outstanding balance "
        "and repayment history. Please confirm your preferred payment date and amount.",
        "faq_v1",
    ),
    (
        "faq_002",
        "Q: What happens if I cannot pay now? A: We can discuss options including a callback arrangement, "
        "EMI restructuring, or a promise-to-pay for a future date. A written promise-to-pay is legally binding.",
        "faq_v1",
    ),
    (
        "faq_003",
        "Q: How do I raise a complaint? A: You may raise a complaint in writing to the nodal officer. "
        "Complaints are addressed within 30 days as per RBI grievance redressal guidelines.",
        "faq_v1",
    ),
    (
        "ptp_001",
        "Promise-to-Pay (PTP) arrangements must include a specific date and amount. "
        "The customer must confirm the PTP verbally and the agent must document it immediately. "
        "A confirmation SMS is sent to the customer after every PTP.",
        "promise_to_pay_policy",
    ),
]


class KnowledgeRetrievalService:
    """Retrieves relevant knowledge snippets for injection into the ResponsePlan.

    The service is initialised with a pre-seeded VectorStore. Retrieval is
    synchronous and in-memory for Sprint-012. Sprint-013 will integrate Redis.

    Architecture: V2 Ch20; RI-5 (snippets are evidence, not authority).
    """

    def __init__(self, top_k: int = 3) -> None:
        self._embedder = DocumentEmbedder()
        self._store = VectorStore()
        self._retriever = RelevanceRetriever(
            store=self._store,
            embedder=self._embedder,
            top_k=top_k,
        )
        self._seed()

    def _seed(self) -> None:
        """Populate the vector store with the pre-seeded knowledge base."""
        # Pass 1: build full vocabulary across all documents so every
        # embedding and query share the same vector space.
        for doc_id, text, _source in _SEED_DOCUMENTS:
            self._embedder.add_document(doc_id, text)
        # Pass 2: embed with the complete vocabulary and store.
        for doc_id, text, source in _SEED_DOCUMENTS:
            embedding = self._embedder.embed(text)
            self._store.add(doc_id, text, embedding, source)
        logger.debug("KnowledgeRetrievalService: seeded %d documents", len(self._store))

    async def retrieve(self, query: str) -> list[Snippet]:
        """Retrieve snippets relevant to the query.

        Args:
            query: Customer utterance or topic string.

        Returns:
            Ranked list of Snippet objects for insertion into ResponsePlan.retrieval.
        """
        return self._retriever.retrieve(query)
