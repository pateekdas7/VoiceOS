from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

def test_live_w2_composition_injects_existing_otel_tracer():
    text = (ROOT / "deployment/cpu/app.py").read_text(encoding="utf-8")
    assert "def build_otel_tracer()" in text
    assert 'OTelTracer.for_production("voiceos-cpu-media-gateway", endpoint)' in text
    assert "tracer=build_otel_tracer()" in text

def test_w2_media_path_consumes_shared_tracer():
    text = (ROOT / "src/services/media_gateway/twilio_ws_entrypoint.py").read_text(encoding="utf-8")
    for span_name in ("voice.http.inbound", "ws.call.connect", "stt.transcribe", "llm.handle_turn", "tts.greeting", "call.end"):
        assert span_name in text
