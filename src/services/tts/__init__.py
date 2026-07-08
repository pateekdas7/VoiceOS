"""TTS (Text-to-Speech) adapter service package.

Exposes the TTSAdapter protocol, VeenaAdapter, ClauseSplitter, TTSService,
and HindiScriptConverter (Devanagari conversion stage).

Architecture: V1 Ch15-18; DocSuite-02; DocSuite-07.
"""

from .clause_splitter import ClauseSplitter
from .protocol import TTSAdapter
from .script_converter import HindiScriptConverter
from .service import TTSService

__all__ = ["ClauseSplitter", "HindiScriptConverter", "TTSAdapter", "TTSService"]
