"""VAD & Endpointing service package.

Implements voice activity detection (VAD), adaptive endpointing,
barge-in detection, and backchannel discrimination per V1 Ch6.

Public API:
    VADEndpointingService  — orchestrating service
    VADEngine              — Silero VAD v4 facade
    SileroVADModel         — ONNX-backed production model
    EnergyVADModel         — energy-based model (testing / fallback)
    EndpointDetector       — adaptive silence threshold state machine
    BargeinDetector        — real-time barge-in during agent playback
    BackchannelDiscriminator — short-utterance filler classification
"""

from .backchannel import BackchannelDiscriminator
from .bargein_detector import BargeinDetector
from .endpoint_detector import EndpointDetector, SpeechState
from .service import VADEndpointingService
from .vad_engine import EnergyVADModel, SileroVADModel, VADEngine, VADModelProtocol

__all__ = [
    "BackchannelDiscriminator",
    "BargeinDetector",
    "EndpointDetector",
    "EnergyVADModel",
    "SileroVADModel",
    "SpeechState",
    "VADEndpointingService",
    "VADEngine",
    "VADModelProtocol",
]
