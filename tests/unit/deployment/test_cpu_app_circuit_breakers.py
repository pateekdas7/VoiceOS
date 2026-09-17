"""Source-level wiring test: deployment/cpu/app.py must construct a shared
CircuitBreakerRegistry and pass ``breaker=`` into every GPU-node adapter
(WhisperHTTPAdapter, vLLMAdapter, VeenaAdapter).

A pure source-text assertion is used because deployment/cpu/app.py's runtime
import chain pulls in opentelemetry/numpy (not installable on the local test
env), and because the wiring itself is a construction-time contract — if any
of the three ``build_*_service`` functions ever stops passing ``breaker=``,
that adapter silently loses fail-fast protection with no runtime error, and
the ``voiceos_circuit_breaker_state`` gauge silently reports ``CLOSED``
forever for the missing dependency. This test is the tripwire.
"""

from __future__ import annotations

from pathlib import Path

_APP_PY = Path(__file__).resolve().parents[3] / "deployment" / "cpu" / "app.py"


def _app_source() -> str:
    return _APP_PY.read_text(encoding="utf-8")


def _adapter_call_slice(src: str, ctor_name: str) -> str:
    """Return the text ``ctor_name(...)`` with balanced parens."""
    start = src.index(f"{ctor_name}(")
    depth = 0
    i = start + len(ctor_name)
    while i < len(src):
        c = src[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced parens after {ctor_name}( at {start}")


def test_circuit_breaker_registry_factory_exists() -> None:
    src = _app_source()
    assert "def build_circuit_breaker_registry" in src
    assert "CircuitBreakerRegistry" in src
    assert "record_circuit_breaker_state" in src, (
        "Registry must wire ``on_state_change=record_circuit_breaker_state`` so "
        "the voiceos_circuit_breaker_state gauge updates on every trip/close"
    )


def test_llm_adapter_receives_breaker() -> None:
    src = _app_source()
    assert 'get_or_create("llm")' in src
    assert "breaker=" in _adapter_call_slice(src, "vLLMAdapter"), (
        "vLLMAdapter must be constructed with breaker= — otherwise LLM outages "
        "queue behind a dead vLLM instead of failing fast (V3 Ch14 §14.14)."
    )


def test_tts_adapter_receives_breaker() -> None:
    src = _app_source()
    assert 'get_or_create("tts")' in src
    assert "breaker=" in _adapter_call_slice(src, "VeenaAdapter")


def test_stt_adapter_receives_breaker() -> None:
    src = _app_source()
    assert 'get_or_create("stt")' in src
    assert "breaker=" in _adapter_call_slice(src, "WhisperHTTPAdapter")


def test_circuit_breaker_registry_is_process_singleton() -> None:
    """The registry must be cached so LLM/TTS (constructed by
    build_conversation_engine) and STT (constructed by
    build_shared_call_dependencies) share the same breaker instances —
    otherwise the "5 failures / 30 s window" trip threshold splits across
    two independent registries and never fires."""
    src = _app_source()
    assert "_CIRCUIT_BREAKER_REGISTRY" in src, (
        "Registry factory must maintain a module-level cache; otherwise the "
        "llm/tts/stt breakers built in different call trees are separate "
        "instances and defeat the shared-window trip semantics."
    )
