from src.services.media_gateway import metrics

def test_w2_telephony_metric_labels_are_bounded():
    metrics.record_call_lifecycle("CONNECTED")
    metrics.record_webhook_failure("timeout")
    metrics.record_media_failure("disconnect")
    metrics.record_recording_failure("finalize")
    metrics.record_retry_attempt("provider_failed")
    metrics.record_callback_event("scheduled")
    metrics.record_cps_limit_event()
    assert metrics.CALL_LIFECYCLE.labels(state="CONNECTED")._value.get() >= 1
    assert metrics.WEBHOOK_FAILURES.labels(reason="timeout")._value.get() >= 1


def test_w2_media_failure_call_site_is_present():
    source = Path(__file__).resolve().parents[3] / "src/services/media_gateway/twilio_ws_entrypoint.py"
    text = source.read_text(encoding="utf-8")
    assert 'record_media_failure("pipeline_error")' in text


def test_w2_retry_call_sites_are_real_attempt_paths():
    root = Path(__file__).resolve().parents[3]
    stt = (root / "src/services/stt/adapters/whisper_http_adapter.py").read_text(encoding="utf-8")
    tts = (root / "src/services/tts/adapters/veena_adapter.py").read_text(encoding="utf-8")
    assert 'record_retry_attempt("stt_provider")' in stt
    assert 'record_retry_attempt("tts_provider")' in tts


def test_w2_callback_metric_call_site_is_bounded():
    source = Path(__file__).resolve().parents[3] / "bff.js"
    text = source.read_text(encoding="utf-8")
    assert "voiceos_telephony_callback_events_total" in text
    assert "callbackMetricOutcome" in text
    callback_section = text[text.index("callbackMetricOutcome"):text.index("callbackMetricOutcome") + 450]
    assert "CallSid" not in callback_section
    assert "phone" not in callback_section.lower()


def test_w2_cps_metric_call_site_is_bounded():
    source = Path(__file__).resolve().parents[3] / "dialer_worker.js"
    text = source.read_text(encoding="utf-8")
    assert "voiceos_telephony_cps_limit_events_total" in text
    section = text[text.index("onLimit"):text.index("onLimit") + 350]
    assert "CallSid" not in section
    assert "phone" not in section.lower()
