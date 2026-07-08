"""STT (Speech-to-Text) adapter service package.

Exposes the STTAdapter protocol and WhisperAdapter implementation.
Architecture: V1 Ch8; DocSuite-02.
"""

from .protocol import STTAdapter
from .service import STTService

__all__ = ["STTAdapter", "STTService"]
