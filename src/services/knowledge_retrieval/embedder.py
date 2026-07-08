"""DocumentEmbedder — lightweight text-to-vector embedding for Sprint-012.

Sprint-012 walking skeleton uses a bag-of-words TF-IDF proxy embedding
(no model download required, no external dependency). Sprint-034 will
replace this with a multilingual sentence encoder.

Architecture: V2 Ch20 (Knowledge Retrieval); Sprint-012.
"""

from __future__ import annotations

import math
import re
from collections import Counter


def _tokenize(text: str) -> list[str]:
    """Lowercase word tokenisation for Hindi and English text."""
    return re.findall(r"[a-zA-Zऀ-ॿ]+", text.lower())


class DocumentEmbedder:
    """Converts text into a fixed-length term-frequency vector.

    The vocabulary is built lazily from documents added via ``add_document``.
    ``embed`` converts any text into a TF-IDF-style vector over that vocab.

    This implementation is synchronous and CPU-only. It produces valid
    cosine-similarity rankings; quality will improve with Sprint-034.
    """

    def __init__(self) -> None:
        self._vocab: dict[str, int] = {}  # term → index
        self._idf: dict[str, float] = {}  # term → IDF weight
        self._doc_count = 0

    def add_document(self, doc_id: str, text: str) -> None:
        """Register a document and update the vocabulary and IDF estimates.

        Args:
            doc_id: Unique document identifier (unused here; reserved for future).
            text: Document text to index.
        """
        tokens = set(_tokenize(text))
        self._doc_count += 1
        for token in tokens:
            if token not in self._vocab:
                self._vocab[token] = len(self._vocab)
            self._idf[token] = self._idf.get(token, 0) + 1

    def embed(self, text: str) -> list[float]:
        """Embed text into a TF-IDF vector over the current vocabulary.

        Args:
            text: Input text to embed.

        Returns:
            L2-normalised float vector of length len(vocab).
            Returns an all-zero vector if vocab is empty.
        """
        if not self._vocab:
            return []

        tokens = _tokenize(text)
        tf = Counter(tokens)
        vec = [0.0] * len(self._vocab)

        for term, count in tf.items():
            if term in self._vocab:
                idx = self._vocab[term]
                df = self._idf.get(term, 1)
                idf = math.log((self._doc_count + 1) / (df + 1)) + 1.0
                vec[idx] = count * idf

        # L2 normalise.
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]
