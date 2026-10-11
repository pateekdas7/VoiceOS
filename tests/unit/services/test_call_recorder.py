"""Unit tests for CallRecorder (Sprint-029 Call-002 instrumentation)."""

from __future__ import annotations

import json
import wave

from src.services.media_gateway.call_recorder import CallRecorder


def test_close_writes_events_jsonl(tmp_path: object) -> None:
    recorder = CallRecorder("call-1", str(tmp_path))
    recorder.event("call_start", tenant_id="t1")
    recorder.event("stt_final", transcript="hello", latency_ms=120)
    recorder.close()

    events_path = tmp_path / "call-1_events.jsonl"  # type: ignore[operator]
    lines = events_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "call_start"
    assert first["tenant_id"] == "t1"
    second = json.loads(lines[1])
    assert second["transcript"] == "hello"
    assert second["latency_ms"] == 120


def test_close_writes_inbound_and_outbound_wav_files(tmp_path: object) -> None:
    recorder = CallRecorder("call-2", str(tmp_path))
    recorder.add_inbound_audio(b"\x00\x01" * 100, sample_rate=16000)
    recorder.add_outbound_audio(b"\x02\x03" * 200, sample_rate=24000)
    recorder.close()

    customer_wav = tmp_path / "call-2_customer.wav"  # type: ignore[operator]
    kavya_wav = tmp_path / "call-2_kavya.wav"  # type: ignore[operator]
    assert customer_wav.exists()
    assert kavya_wav.exists()

    with wave.open(str(customer_wav), "rb") as wf:
        assert wf.getframerate() == 16000
        assert wf.getnchannels() == 1
        assert wf.getnframes() == 100

    with wave.open(str(kavya_wav), "rb") as wf:
        assert wf.getframerate() == 24000
        assert wf.getnframes() == 200


def test_close_with_no_audio_skips_wav_files(tmp_path: object) -> None:
    recorder = CallRecorder("call-3", str(tmp_path))
    recorder.event("call_start")
    recorder.close()

    assert (tmp_path / "call-3_events.jsonl").exists()  # type: ignore[operator]
    assert not (tmp_path / "call-3_customer.wav").exists()  # type: ignore[operator]
    assert not (tmp_path / "call-3_kavya.wav").exists()  # type: ignore[operator]
