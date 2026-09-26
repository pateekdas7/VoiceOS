"""Phase 9 Verification Tests — Voice Runtime Hardening

9a. GPU service authentication — X-GPU-Secret header wired in all three adapters
9c. STT failure handling — STTRetryExhaustedError, retry loop, orchestrator handling
9d. TTS failure handling — TTSFailureError, retry logic in _stream_clause
9e. Circuit breaker verification — all three GPU clients wired to breaker in app.py
9f. Runbook existence check

Run with: python3 tests/unit/voice/test_phase9_voice_hardening.py
Source-inspection strategy: reads .py source as text, no eval, no imports of src modules.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]

passed = 0
failed = 0
results: list[tuple[str, str, str]] = []


def test(name: str) -> "object":
    """Decorator-style test runner."""
    def _dec(fn: "object") -> "object":
        global passed, failed
        try:
            fn()  # type: ignore[operator]
            results.append((name, "PASS", ""))
            passed += 1
        except AssertionError as e:
            results.append((name, "FAIL", str(e)))
            failed += 1
        except Exception as e:
            results.append((name, "FAIL", f"{type(e).__name__}: {e}"))
            failed += 1
        return fn
    return _dec


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

WHISPER_SRC = (ROOT / "src/services/stt/adapters/whisper_http_adapter.py").read_text()
VLLM_SRC    = (ROOT / "src/services/llm_runtime/adapters/vllm_adapter.py").read_text()
VEENA_SRC   = (ROOT / "src/services/tts/adapters/veena_adapter.py").read_text()
APP_SRC     = (ROOT / "deployment/cpu/app.py").read_text()
WS_SRC      = (ROOT / "src/services/media_gateway/twilio_ws_entrypoint.py").read_text()

# ---------------------------------------------------------------------------
# 9a: GPU service authentication
# ---------------------------------------------------------------------------

@test("9a: WhisperHTTPAdapter accepts gpu_secret parameter")
def _(): assert "gpu_secret" in WHISPER_SRC

@test("9a: WhisperHTTPAdapter sends X-GPU-Secret header")
def _(): assert "X-GPU-Secret" in WHISPER_SRC

@test("9a: WhisperHTTPAdapter._post accepts headers parameter")
def _(): assert "headers" in WHISPER_SRC and "_post" in WHISPER_SRC

@test("9a: vLLMAdapter accepts gpu_secret parameter")
def _(): assert "gpu_secret" in VLLM_SRC

@test("9a: vLLMAdapter sends X-GPU-Secret header")
def _(): assert "X-GPU-Secret" in VLLM_SRC

@test("9a: vLLMAdapter._open_stream accepts headers parameter")
def _(): assert "headers" in VLLM_SRC and "_open_stream" in VLLM_SRC

@test("9a: VeenaAdapter accepts gpu_secret parameter")
def _(): assert "gpu_secret" in VEENA_SRC

@test("9a: VeenaAdapter sends X-GPU-Secret header")
def _(): assert "X-GPU-Secret" in VEENA_SRC

@test("9a: VeenaAdapter._open_stream accepts headers parameter")
def _(): assert "headers" in VEENA_SRC and "_open_stream" in VEENA_SRC

@test("9a: app.py reads GPU_SHARED_SECRET env var")
def _(): assert "GPU_SHARED_SECRET" in APP_SRC

@test("9a: app.py passes gpu_secret to LLM adapter")
def _():
    llm_idx = APP_SRC.index("def build_llm_service")
    region = APP_SRC[llm_idx:llm_idx + 900]
    assert "gpu_secret=" in region

@test("9a: app.py passes gpu_secret to TTS adapter")
def _():
    tts_idx = APP_SRC.index("def build_tts_service")
    region = APP_SRC[tts_idx:tts_idx + 900]
    assert "gpu_secret=" in region

@test("9a: app.py passes gpu_secret to STT adapter")
def _():
    stt_idx = APP_SRC.index("def build_stt_service")
    region = APP_SRC[stt_idx:stt_idx + 1400]
    assert "gpu_secret=" in region

@test("9a: .env.example documents GPU_SHARED_SECRET")
def _():
    env_ex = (ROOT / "deployment/cpu/.env.example").read_text()
    assert "GPU_SHARED_SECRET" in env_ex

# ---------------------------------------------------------------------------
# 9c: STT failure handling
# ---------------------------------------------------------------------------

@test("9c: STTRetryExhaustedError is defined in whisper_http_adapter")
def _(): assert "class STTRetryExhaustedError" in WHISPER_SRC

@test("9c: WhisperHTTPAdapter has max_retries parameter")
def _(): assert "max_retries" in WHISPER_SRC

@test("9c: WhisperHTTPAdapter has retry_backoff_s parameter")
def _(): assert "retry_backoff_s" in WHISPER_SRC

@test("9c: whisper_http_adapter raises STTRetryExhaustedError")
def _(): assert "raise STTRetryExhaustedError" in WHISPER_SRC

@test("9c: whisper_http_adapter has retry loop")
def _(): assert "for attempt in range" in WHISPER_SRC

@test("9c: orchestrator imports STTRetryExhaustedError")
def _(): assert "STTRetryExhaustedError" in WS_SRC

@test("9c: orchestrator catches STTRetryExhaustedError")
def _(): assert "except STTRetryExhaustedError" in WS_SRC

@test("9c: orchestrator tracks _consecutive_stt_failures")
def _(): assert "_consecutive_stt_failures" in WS_SRC

@test("9c: orchestrator closes call after 3 consecutive STT failures")
def _():
    assert "_consecutive_stt_failures >= 3" in WS_SRC

@test("9c: orchestrator has _speak_stt_clarify method")
def _(): assert "_speak_stt_clarify" in WS_SRC

@test("9c: _CLARIFY_ASK_REPEAT dict is defined")
def _(): assert "_CLARIFY_ASK_REPEAT" in WS_SRC

@test("9c: orchestrator resets failure counter on successful turn")
def _(): assert "_consecutive_stt_failures = 0" in WS_SRC

# ---------------------------------------------------------------------------
# 9d: TTS failure handling
# ---------------------------------------------------------------------------

@test("9d: TTSFailureError is defined in veena_adapter")
def _(): assert "class TTSFailureError" in VEENA_SRC

@test("9d: VeenaAdapter has tts_retry_wait_s parameter")
def _(): assert "tts_retry_wait_s" in VEENA_SRC

@test("9d: veena_adapter raises TTSFailureError after retry")
def _(): assert "raise TTSFailureError" in VEENA_SRC

@test("9d: veena_adapter has retry loop in _stream_clause")
def _(): assert "for _attempt in range" in VEENA_SRC

@test("9d: orchestrator imports TTSFailureError")
def _(): assert "TTSFailureError" in WS_SRC

@test("9d: orchestrator catches TTSFailureError")
def _(): assert "except TTSFailureError" in WS_SRC

@test("9d: orchestrator sets _closing=True on TTS failure")
def _():
    idx = WS_SRC.index("except TTSFailureError")
    region = WS_SRC[idx : idx + 500]
    assert "_closing = True" in region

# ---------------------------------------------------------------------------
# 9e: Circuit breaker wiring
# ---------------------------------------------------------------------------

@test("9e: circuit breaker registry created for llm in app.py")
def _(): assert 'get_or_create("llm")' in APP_SRC

@test("9e: circuit breaker registry created for tts in app.py")
def _(): assert 'get_or_create("tts")' in APP_SRC

@test("9e: circuit breaker registry created for stt in app.py")
def _(): assert 'get_or_create("stt")' in APP_SRC

@test("9e: circuit breaker passed to LLM adapter")
def _():
    llm_region = APP_SRC[APP_SRC.index("build_llm_service"):APP_SRC.index("build_llm_service") + 600]
    assert "breaker=breaker" in llm_region

@test("9e: circuit breaker passed to TTS adapter")
def _():
    tts_region = APP_SRC[APP_SRC.index("build_tts_service"):APP_SRC.index("build_tts_service") + 600]
    assert "breaker=breaker" in tts_region

@test("9e: circuit breaker passed to STT adapter")
def _():
    stt_region = APP_SRC[APP_SRC.index("def build_stt_service"):APP_SRC.index("def build_stt_service") + 1400]
    assert "breaker=breaker" in stt_region

@test("9e: circuit breaker registry is process-level singleton")
def _(): assert "_CIRCUIT_BREAKER_REGISTRY" in APP_SRC

@test("9e: on_state_change wired to record_circuit_breaker_state")
def _(): assert "record_circuit_breaker_state" in APP_SRC

# ---------------------------------------------------------------------------
# 9f: Blue-green runbook
# ---------------------------------------------------------------------------

@test("9f: blue-green runbook file exists")
def _():
    assert (ROOT / "docs/runbooks/blue-green-model-deployment.md").exists()

@test("9f: runbook covers STT service")
def _():
    content = (ROOT / "docs/runbooks/blue-green-model-deployment.md").read_text()
    assert "STT" in content

@test("9f: runbook covers LLM service")
def _():
    content = (ROOT / "docs/runbooks/blue-green-model-deployment.md").read_text()
    assert "LLM" in content or "vLLM" in content

@test("9f: runbook covers TTS service")
def _():
    content = (ROOT / "docs/runbooks/blue-green-model-deployment.md").read_text()
    assert "TTS" in content

@test("9f: runbook has rollback procedure")
def _():
    content = (ROOT / "docs/runbooks/blue-green-model-deployment.md").read_text()
    assert "Rollback" in content or "rollback" in content

@test("9f: runbook has health-check step")
def _():
    content = (ROOT / "docs/runbooks/blue-green-model-deployment.md").read_text()
    assert "health" in content.lower()

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

print("\n── Phase 9 Voice Hardening Tests ─────────────────────────────")
for name, status, detail in results:
    line = f"  [{status}] {name}"
    if detail:
        line += f"\n         {detail}"
    print(line)
print(f"\n  {passed} passed, {failed} failed\n")
sys.exit(1 if failed > 0 else 0)
