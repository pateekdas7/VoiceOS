"""Central GPU runtime configuration for VoiceOS.

All GPU services (STT, LLM, TTS) import RuntimeConfig from this module.
Configuration is loaded from environment variables; VOICEOS_MODE=dev
switches every service to CPU + mock mode for local development.

Environment variables
---------------------
VOICEOS_MODE         "dev" (CPU, small/mock models) | "prod" (GPU, full models)

STT_DEVICE           "cuda" | "cpu"  (overrides mode default)
STT_MOCK             "true" to return synthetic transcriptions (skips model load)
STT_MODEL_SIZE       Whisper model size; default "tiny" in dev, "large-v3-turbo" in prod
STT_COMPUTE_TYPE     CTranslate2 compute type; default "int8" in dev, "int8_float16" in prod
WHISPER_MODEL_PATH   Local path or HF model ID for Whisper
STT_SERVICE_PORT     HTTP port (default 8100)

LLM_MOCK             "true" to use mock LLM server instead of vLLM
QWEN_MODEL_PATH      Local path to Qwen FP8 model
LLM_SERVICE_PORT     HTTP port (default 8000)
GPU_MEMORY_FRACTION  vLLM gpu_memory_utilization (default 0.32 for A6000)
LLM_MAX_MODEL_LEN    vLLM max context length (default 4096)

TTS_DEVICE           "cuda" | "cpu"  (overrides mode default)
TTS_MOCK             "true" to return PCM silence (skips model load)
VEENA_MODEL_PATH     Local path to Veena BF16 model
SNAC_MODEL_PATH      Local path to SNAC 24 kHz codec
TTS_SERVICE_PORT     HTTP port (default 8200)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class STTConfig:
    device: str = "cuda"
    compute_type: str = "int8_float16"
    model_size: str = "large-v3-turbo"
    model_path: str = "/opt/voiceos-gpu/models/whisper-large-v3-turbo"
    port: int = 8100
    host: str = "0.0.0.0"
    request_timeout_s: float = 30.0
    mock: bool = False
    default_language: str = "hi"
    default_beam_size: int = 5

    @property
    def effective_compute_type(self) -> str:
        if self.device == "cpu" and self.compute_type == "int8_float16":
            return "int8"
        return self.compute_type


@dataclass
class LLMConfig:
    model_path: str = "/opt/voiceos-gpu/models/qwen2.5-7b-fp8"
    served_model_name: str = "qwen2.5-7b-instruct-fp8"
    port: int = 8000
    host: str = "0.0.0.0"
    gpu_memory_utilization: float = 0.32
    max_model_len: int = 4096
    dtype: str = "auto"
    mock: bool = False


@dataclass
class TTSConfig:
    device: str = "cuda"
    model_path: str = "/opt/voiceos-gpu/models/veena-fp16"
    snac_path: str = "/opt/voiceos-gpu/models/snac-24khz"
    port: int = 8200
    host: str = "0.0.0.0"
    mock: bool = False
    default_speaker: str = "kavya"
    sample_rate: int = 24000


@dataclass
class RuntimeConfig:
    mode: str = "prod"
    stt: STTConfig = field(default_factory=STTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)

    @property
    def is_dev(self) -> bool:
        return self.mode == "dev"

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        mode = os.environ.get("VOICEOS_MODE", "prod")
        dev = mode == "dev"

        stt = STTConfig(
            device=os.environ.get("STT_DEVICE", "cpu" if dev else "cuda"),
            compute_type=os.environ.get("STT_COMPUTE_TYPE", "int8" if dev else "int8_float16"),
            model_size=os.environ.get("STT_MODEL_SIZE", "tiny" if dev else "large-v3-turbo"),
            model_path=os.environ.get(
                "WHISPER_MODEL_PATH",
                "tiny" if dev else "/opt/voiceos-gpu/models/whisper-large-v3-turbo",
            ),
            port=int(os.environ.get("STT_SERVICE_PORT", "8100")),
            request_timeout_s=float(os.environ.get("STT_TIMEOUT_S", "30.0")),
            mock=dev or os.environ.get("STT_MOCK", "").lower() == "true",
        )

        llm = LLMConfig(
            model_path=os.environ.get(
                "QWEN_MODEL_PATH", "/opt/voiceos-gpu/models/qwen2.5-7b-fp8"
            ),
            port=int(os.environ.get("LLM_SERVICE_PORT", "8000")),
            gpu_memory_utilization=float(os.environ.get("GPU_MEMORY_FRACTION", "0.32")),
            max_model_len=int(os.environ.get("LLM_MAX_MODEL_LEN", "4096")),
            mock=dev or os.environ.get("LLM_MOCK", "").lower() == "true",
        )

        tts = TTSConfig(
            device=os.environ.get("TTS_DEVICE", "cpu" if dev else "cuda"),
            model_path=os.environ.get(
                "VEENA_MODEL_PATH", "/opt/voiceos-gpu/models/veena-fp16"
            ),
            snac_path=os.environ.get(
                "SNAC_MODEL_PATH", "/opt/voiceos-gpu/models/snac-24khz"
            ),
            port=int(os.environ.get("TTS_SERVICE_PORT", "8200")),
            mock=dev or os.environ.get("TTS_MOCK", "").lower() == "true",
        )

        return cls(mode=mode, stt=stt, llm=llm, tts=tts)
