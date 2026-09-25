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
