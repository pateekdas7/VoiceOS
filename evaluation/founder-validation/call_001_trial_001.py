#!/usr/bin/env python3
"""
VoiceOS Founder Validation — Call-001 Trial-001
Sprint-029 Phase 2: AUDIO AND VOICE EXPERIENCE

Objective: Prove the customer can hear a clear, natural, responsive AI voice.

Flow:
  1. Generate Hindi/Hinglish collections greeting via Veena TTS (Kavya voice)
  2. Measure TTS TTFA and full generation latency
  3. Convert float32-24kHz → PCM16-24kHz WAV (reference quality for founder review)
  4. Convert → PCM16-8kHz WAV (Twilio-compatible)
  5. Serve both + TwiML webhook over HTTP on port 5000
  6. Make Twilio outbound call to founder (+919911954448)
  7. Twilio plays the greeting WAV when founder answers
  8. Record call via Twilio (captures telephone-quality playback evidence)
  9. Download Twilio recording after call completes
 10. Save all evidence with SHA-256 checksums and timing metadata

Evidence saved to: /tmp/founder-validation/call-001/trial-001/
Primary WAV:        call-001-trial-001.wav        (24kHz PCM16 — quality review artifact)
Twilio recording:   call-001-trial-001-twilio.wav (8kHz — telephone experience artifact)
Metadata:           call-metadata.json
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import io
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]   # required — no default
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]    # required — no default
TWILIO_FROM        = os.environ.get("TWILIO_FROM", "+13502204241")
TWILIO_TO          = os.environ.get("TWILIO_TO",   "+919911954448")
GPU_PUBLIC_IP      = os.environ.get("GPU_PUBLIC_IP", "185.216.21.53")
HTTP_PORT          = 5000

TTS_URL            = "http://127.0.0.1:8200/synthesize"
TTS_SPEAKER        = "kavya"
TTS_SAMPLE_RATE    = 24000          # Veena output: float32 LE at 24kHz
TWILIO_SAMPLE_RATE = 8000           # Twilio <Play> accepts 8kHz PCM WAV

EVIDENCE_DIR = Path("/tmp/founder-validation/call-001/trial-001")

# ─────────────────────────────────────────────────────────────────────────────
# Collections greeting — Hindi/Hinglish (Prateek Das, test loan account)
# Tests: Hindi phonemes, Hinglish code-switching, name, ₹50,000, date, empathy
# ─────────────────────────────────────────────────────────────────────────────

GREETING_TEXT = (
    "Namaste, Prateek Das ji. "
    "Main Kavya hun, Rajat Finance ke collections team se baat kar rahi hun. "
    "Aapke loan account mein pachaas hazaar rupay ka outstanding balance hai, "
    "jo tees July do hazaar chhabbees tak due hai. "
    "Kya aap aaj payment ke baare mein baat kar sakte hain? "
    "Main aapki poori madad karna chahti hun."
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared state (written before HTTP server starts)
# ─────────────────────────────────────────────────────────────────────────────

_state: dict = {
    "wav_8k_bytes": b"",
    "recording_callback_body": "",
    "status_events": [],
}

# ─────────────────────────────────────────────────────────────────────────────
# Audio utilities
# ─────────────────────────────────────────────────────────────────────────────

def float32_raw_to_pcm16(raw: bytes) -> np.ndarray:
    """float32 LE raw bytes → int16 ndarray (clipped)."""
    arr = np.frombuffer(raw, dtype="<f4")
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767).astype(np.int16)


def resample_pcm16(pcm: np.ndarray, src_hz: int, dst_hz: int) -> np.ndarray:
    """Linear-interpolation resample — sufficient quality for 8kHz telephony."""
    if src_hz == dst_hz:
        return pcm
    n_out = int(len(pcm) * dst_hz / src_hz)
    idx   = np.linspace(0, len(pcm) - 1, n_out)
    lo    = idx.astype(np.int64)
    hi    = np.minimum(lo + 1, len(pcm) - 1)
    frac  = idx - lo
    out   = pcm[lo] * (1.0 - frac) + pcm[hi] * frac
    return out.astype(np.int16)


def pcm16_to_wav(pcm: np.ndarray, sample_rate: int) -> bytes:
    """int16 ndarray → WAV bytes (RIFF header + PCM payload)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)          # int16 = 2 bytes
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wav_info(data: bytes) -> dict:
    buf = io.BytesIO(data)
    with wave.open(buf) as w:
        return {
            "channels":    w.getnchannels(),
            "sample_rate": w.getframerate(),
            "sample_width_bytes": w.getsampwidth(),
            "n_frames":    w.getnframes(),
            "duration_s":  round(w.getnframes() / w.getframerate(), 3),
        }


# ─────────────────────────────────────────────────────────────────────────────
# TTS generation
# ─────────────────────────────────────────────────────────────────────────────

def generate_tts(text: str) -> tuple[bytes, float, float]:
    """
    POST to Veena TTS server, return (raw_float32_bytes, ttfa_ms, total_ms).
    TTFA = wall time to first audio byte received.
    """
    payload = json.dumps({"text": text, "speaker": TTS_SPEAKER}).encode()
    req = urllib.request.Request(
        TTS_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0       = time.perf_counter()
    ttfa_ms  = 0.0
    chunks: list[bytes] = []

    with urllib.request.urlopen(req, timeout=120) as resp:
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            if not chunks:
                ttfa_ms = (time.perf_counter() - t0) * 1000
            chunks.append(chunk)

    total_ms = (time.perf_counter() - t0) * 1000
    return b"".join(chunks), round(ttfa_ms, 1), round(total_ms, 1)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP server (TwiML webhook + WAV serving)
# ─────────────────────────────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    """Minimal HTTP handler: serves WAV for <Play> and TwiML for Twilio webhook."""

    def log_message(self, fmt: str, *args: object) -> None:
        ts = time.strftime("%H:%M:%S")
        print(f"[HTTP {ts}] {self.address_string()} {fmt % args}")

    # ── GET: serve audio or health ──────────────────────────────────────────

    def do_GET(self) -> None:
        if self.path == "/greeting.wav":
            body = _state["wav_8k_bytes"]
            self._send(200, "audio/wav", body)
        elif self.path in ("/health", "/"):
            self._send(200, "text/plain", b"ok")
        else:
            self._send(404, "text/plain", b"not found")

    # ── POST: TwiML, status, recording callbacks ────────────────────────────

    def do_POST(self) -> None:
        length  = int(self.headers.get("Content-Length", 0))
        body    = self.rfile.read(length) if length else b""
        body_s  = body.decode(errors="replace")

        if self.path == "/twiml":
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<Response>\n"
                f'    <Play>http://{GPU_PUBLIC_IP}:{HTTP_PORT}/greeting.wav</Play>\n'
                "    <Pause length=\"8\"/>\n"
                "    <Hangup/>\n"
                "</Response>"
            ).encode()
            self._send(200, "text/xml; charset=utf-8", twiml)

        elif self.path == "/recording":
            _state["recording_callback_body"] = body_s
            print(f"[RECORDING CB] {body_s[:300]}")
            self._send(200, "text/plain", b"ok")

        elif self.path == "/status":
            _state["status_events"].append(body_s)
            call_status = dict(urllib.parse.parse_qsl(body_s)).get("CallStatus", "")
            print(f"[STATUS CB] {call_status}")
            self._send(200, "text/plain", b"ok")

        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_http_server() -> None:
    srv = http.server.HTTPServer(("0.0.0.0", HTTP_PORT), _Handler)
    print(f"[HTTP] Listening on 0.0.0.0:{HTTP_PORT}")
    srv.serve_forever()


# ─────────────────────────────────────────────────────────────────────────────
# Twilio REST helpers
# ─────────────────────────────────────────────────────────────────────────────

def _twilio_auth() -> str:
    return base64.b64encode(
        f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()
    ).decode()


def twilio_make_call() -> dict:
    url  = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json"
    data = urllib.parse.urlencode({
        "From":                        TWILIO_FROM,
        "To":                          TWILIO_TO,
        "Url":                         f"http://{GPU_PUBLIC_IP}:{HTTP_PORT}/twiml",
        "Record":                      "true",
        "RecordingStatusCallback":     f"http://{GPU_PUBLIC_IP}:{HTTP_PORT}/recording",
        "RecordingStatusCallbackMethod": "POST",
        "StatusCallback":              f"http://{GPU_PUBLIC_IP}:{HTTP_PORT}/status",
        "StatusCallbackMethod":        "POST",
        "StatusCallbackEvent":         "initiated ringing answered completed",
    }).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Basic {_twilio_auth()}",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def twilio_get_call(sid: str) -> dict:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{sid}.json"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {_twilio_auth()}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def twilio_get_recordings(call_sid: str) -> list:
    url = (f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"
           f"/Calls/{call_sid}/Recordings.json")
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {_twilio_auth()}"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read()).get("recordings", [])


def twilio_download_recording(rec_sid: str, path: Path) -> int:
    url = (f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"
           f"/Recordings/{rec_sid}.wav")
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {_twilio_auth()}"})
    with urllib.request.urlopen(req) as r:
        data = r.read()
    path.write_bytes(data)
    return len(data)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    ts_start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print("=" * 70)
    print("VoiceOS Founder Validation — Sprint-029 Phase 2")
    print("CALL-001 TRIAL-001  |  Objective: Audio & Voice Experience")
    print(f"Started: {ts_start}")
    print("=" * 70)
    print(f"Borrower:  Prateek Das")
    print(f"Loan:      LOAN-TEST-001  |  Outstanding: ₹50,000  |  Due: 30-Jul-2026")
    print(f"Greeting:  {GREETING_TEXT[:90]}...")
    print()

    # ── 1. Generate TTS ──────────────────────────────────────────────────────
    print("[STEP 1/6]  Generating TTS greeting (Veena / kavya voice) …")
    raw_f32, ttfa_ms, total_gen_ms = generate_tts(GREETING_TEXT)
    n_f32_samples = len(raw_f32) // 4          # float32 = 4 bytes
    duration_s    = n_f32_samples / TTS_SAMPLE_RATE
    print(f"            TTFA            : {ttfa_ms:.0f} ms")
    print(f"            Full generation : {total_gen_ms:.0f} ms")
    print(f"            Float32 bytes   : {len(raw_f32):,}")
    print(f"            Audio duration  : {duration_s:.3f} s")

    # ── 2. Convert + save WAV files ──────────────────────────────────────────
    print("\n[STEP 2/6]  Converting audio …")
    pcm24k = float32_raw_to_pcm16(raw_f32)

    wav_24k = pcm16_to_wav(pcm24k, TTS_SAMPLE_RATE)
    pcm8k   = resample_pcm16(pcm24k, TTS_SAMPLE_RATE, TWILIO_SAMPLE_RATE)
    wav_8k  = pcm16_to_wav(pcm8k, TWILIO_SAMPLE_RATE)

    _state["wav_8k_bytes"] = wav_8k

    # Primary artifact (24kHz — full quality for founder review)
    primary_path = EVIDENCE_DIR / "call-001-trial-001.wav"
    primary_path.write_bytes(wav_24k)

    path_24k = EVIDENCE_DIR / "call-001-trial-001-24khz.wav"
    path_8k  = EVIDENCE_DIR / "call-001-trial-001-8khz.wav"
    path_24k.write_bytes(wav_24k)
    path_8k.write_bytes(wav_8k)

    sha_primary = sha256hex(wav_24k)
    info_24k    = wav_info(wav_24k)
    info_8k     = wav_info(wav_8k)

    print(f"            24kHz WAV size  : {len(wav_24k):,} bytes")
    print(f"            24kHz duration  : {info_24k['duration_s']:.3f} s")
    print(f"            8kHz WAV size   : {len(wav_8k):,} bytes")
    print(f"            SHA-256 (24kHz) : {sha_primary}")

    # ── 3. Start HTTP server ──────────────────────────────────────────────────
    print(f"\n[STEP 3/6]  Starting HTTP server on port {HTTP_PORT} …")
    t = threading.Thread(target=start_http_server, daemon=True)
    t.start()
    time.sleep(1.5)
    print(f"            TwiML URL       : http://{GPU_PUBLIC_IP}:{HTTP_PORT}/twiml")
    print(f"            Audio URL       : http://{GPU_PUBLIC_IP}:{HTTP_PORT}/greeting.wav")

    # ── 4. Place Twilio outbound call ─────────────────────────────────────────
    print(f"\n[STEP 4/6]  Placing Twilio outbound call {TWILIO_FROM} → {TWILIO_TO} …")
    t_call_placed = time.perf_counter()
    call_resp     = twilio_make_call()
    call_sid      = call_resp.get("sid", "UNKNOWN")
    call_status   = call_resp.get("status", "unknown")
    print(f"            Call SID        : {call_sid}")
    print(f"            Initial status  : {call_status}")
    if call_status in ("failed", "busy", "no-answer", "canceled"):
        print(f"ERROR: Call failed immediately with status={call_status}")
        print(json.dumps(call_resp, indent=2))
        return

    # ── 5. Wait for call completion ───────────────────────────────────────────
    print(f"\n[STEP 5/6]  Monitoring call … (will auto-hangup after greeting + 8s pause)")
    final_status  = ""
    call_duration = 0
    for tick in range(120):              # max 10 minutes
        time.sleep(5)
        try:
            info         = twilio_get_call(call_sid)
            final_status = info.get("status", "")
            call_duration = int(info.get("duration") or 0)
            elapsed = (tick + 1) * 5
            print(f"            [{elapsed:3d}s] status={final_status}  duration={call_duration}s")
            if final_status in ("completed", "failed", "busy", "no-answer", "canceled"):
                break
        except Exception as exc:
            print(f"            [{(tick+1)*5:3d}s] status-check error: {exc}")

    t_call_elapsed = time.perf_counter() - t_call_placed
    print(f"            Call completed in {t_call_elapsed:.1f}s wall-clock")

    # ── 6. Collect Twilio recording ───────────────────────────────────────────
    print(f"\n[STEP 6/6]  Retrieving Twilio call recording …")
    twilio_rec_path: Path | None = None
    twilio_rec_sha  = ""
    twilio_rec_info: dict = {}

    for attempt in range(6):
        time.sleep(10)
        try:
            recs = twilio_get_recordings(call_sid)
            if recs:
                rec      = recs[0]
                rec_sid  = rec.get("sid", "")
                rec_dur  = rec.get("duration", "?")
                print(f"            Recording SID   : {rec_sid}")
                print(f"            Duration        : {rec_dur}s")
                twilio_rec_path = EVIDENCE_DIR / "call-001-trial-001-twilio.wav"
                nbytes = twilio_download_recording(rec_sid, twilio_rec_path)
                twilio_rec_sha  = sha256hex(twilio_rec_path.read_bytes())
                twilio_rec_info = wav_info(twilio_rec_path.read_bytes())
                print(f"            Saved            : {twilio_rec_path} ({nbytes:,} bytes)")
                print(f"            SHA-256 (twilio) : {twilio_rec_sha}")
                break
            else:
                print(f"            Attempt {attempt+1}/6 — recording not ready yet …")
        except Exception as exc:
            print(f"            Attempt {attempt+1}/6 — error: {exc}")
    else:
        print("            WARNING: Could not retrieve Twilio recording.")

    # ── Save metadata JSON ────────────────────────────────────────────────────
    metadata = {
        "call_id":            "call-001",
        "trial_id":           "trial-001",
        "objective":          "Audio & Voice Experience",
        "call_sid":           call_sid,
        "timestamp_start_utc": ts_start,
        "timestamp_end_utc":  time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "borrower_name":      "Prateek Das",
        "loan_id":            "LOAN-TEST-001",
        "outstanding_inr":    50000,
        "due_date":           "2026-07-30",
        "greeting_text":      GREETING_TEXT,
        "tts_model":          "maya-research/Veena",
        "tts_speaker":        TTS_SPEAKER,
        "tts_ttfa_ms":        ttfa_ms,
        "tts_total_gen_ms":   total_gen_ms,
        "tts_duration_s":     round(duration_s, 3),
        "wav_24k": {
            "path":       str(primary_path),
            "size_bytes": len(wav_24k),
            "sha256":     sha_primary,
            "info":       info_24k,
        },
        "wav_8k": {
            "path":       str(path_8k),
            "size_bytes": len(wav_8k),
            "info":       info_8k,
        },
        "twilio_from":        TWILIO_FROM,
        "twilio_to":          TWILIO_TO,
        "twilio_call_status": final_status,
        "twilio_call_duration_s": call_duration,
        "twilio_recording": {
            "path":       str(twilio_rec_path) if twilio_rec_path else None,
            "sha256":     twilio_rec_sha,
            "info":       twilio_rec_info,
        },
        "gpu_host":           GPU_PUBLIC_IP,
        "status_events":      _state["status_events"],
    }

    meta_path = EVIDENCE_DIR / "call-metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("CALL-001 TRIAL-001  —  ARTIFACTS READY")
    print("=" * 70)
    print(f"  Primary WAV (24kHz, for founder review):")
    print(f"    {primary_path}")
    print(f"    Duration : {info_24k['duration_s']:.3f}s | {info_24k['sample_rate']}Hz | {len(wav_24k):,} bytes")
    print(f"    SHA-256  : {sha_primary}")
    if twilio_rec_path and twilio_rec_path.exists():
        print(f"  Twilio recording (telephone quality):")
        print(f"    {twilio_rec_path}")
        print(f"    Duration : {twilio_rec_info.get('duration_s','?')}s")
        print(f"    SHA-256  : {twilio_rec_sha}")
    print(f"  Metadata JSON:")
    print(f"    {meta_path}")
    print(f"  TTS TTFA         : {ttfa_ms:.0f} ms")
    print(f"  TTS total gen    : {total_gen_ms:.0f} ms")
    print(f"  Call SID         : {call_sid}")
    print(f"  Call final status: {final_status}")
    print(f"  Call duration    : {call_duration}s")
    print("=" * 70)


if __name__ == "__main__":
    main()
