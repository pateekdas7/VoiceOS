"""LLM Runtime adapter service package.

Exposes the LLMAdapter protocol, vLLMAdapter implementation,
PromptContract (RI-7), and LLMService.

Architecture: V1 Ch13; DocSuite-02.
"""

from .prompt_contract import PromptContract
from .protocol import LLMAdapter
from .service import LLMService

__all__ = ["LLMAdapter", "LLMService", "PromptContract"]
