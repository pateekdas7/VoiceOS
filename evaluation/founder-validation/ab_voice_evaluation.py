#!/usr/bin/env python3
"""
VoiceOS A/B Voice Evaluation — Polly vs Veena
Sprint-029 Phase 2

Identical test text through both voices. Three arms:
  ARM-A1: Veena raw (24kHz float32 → WAV, no telephony codec)
  ARM-A2: Veena telephony (A1 → ffmpeg 8kHz MP3 → Twilio <Play> → recording)
  ARM-B:  Polly telephony (Twilio <Say voice="Polly.Aditi"> → recording)

Arms A2 and B both go through the Twilio telephone network — same codec path.
Qwen2.5-Omni evaluates all three. Latency measured only where we control synthesis.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import base64
import wave
import struct
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

TEST_TEXT = (
    "Namaste, Prateek Das ji. Main Kavya hun, Rajat Finance ke collections team se. "
    "Aapke account mein pachaas hazaar rupay ka outstanding balance hai jo tees July tak due hai. "
    "Kya aap aaj is baare mein baat kar sakte hain?"
)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM        = os.environ.get("TWILIO_FROM", "")
TWILIO_TO          = os.environ.get("TWILIO_TO", "")

VEENA_URL     = "http://localhost:8200/synthesize"
EVALUATOR_URL = "http://localhost:8300/evaluate"

OUTPUT_DIR = Path("/tmp/ab-evaluation")
OUTPUT_DIR.mkdir(exist_ok=True)

TWILIO_CREDS = base64.b64encode(
    f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()
).decode()


def log(msg: str) -> None:
    print(msg, flush=True)


# ── Veena: stream float32 PCM, measure TTFA ────────────────────────────────────

def generate_veena(text: str) -> tuple[bytes, dict]:
    """
    Returns (raw_pcm_bytes, latency_metrics).
    raw_pcm_bytes: float32 LE, 24kHz mono — no WAV header.
    """
    payload = json.dumps({"text": text, "speaker": "kavya"}).encode()
    req = urllib.request.Request(
        VEENA_URL, data=payload,
        headers={"Content-Type": "application/json"},
    )

    t_start = time.perf_counter()
    t_first_chunk: float | None = None
    chunks: list[bytes] = []

    with urllib.request.urlopen(req, timeout=90) as resp:
        while True:
            chunk = resp.read(8192)
            if not chunk:
                break
            if t_first_chunk is None:
                t_first_chunk = time.perf_counter()
            chunks.append(chunk)

    t_done = time.perf_counter()
    pcm = b"".join(chunks)

    # float32 LE → sample count → duration
    n_samples = len(pcm) // 4
    duration_s = n_samples / 24000.0

    metrics = {
        "ttfa_ms":        round((t_first_chunk - t_start) * 1000) if t_first_chunk else None,
        "full_gen_ms":    round((t_done - t_start) * 1000),
        "pcm_bytes":      len(pcm),
        "duration_s":     round(duration_s, 3),
        "realtime_factor": round((t_done - t_start) / duration_s, 3) if duration_s else None,
    }
    return pcm, metrics


def pcm_float32_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    """Wrap raw float32 LE PCM in a standard int16 WAV."""
    n = len(pcm) // 4
    samples_f = struct.unpack(f"<{n}f", pcm)
    samples_i16 = [max(-32768, min(32767, int(s * 32767))) for s in samples_f]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(struct.pack(f"<{n}h", *samples_i16))
    return buf.getvalue()


def wav_to_telephony_mp3(wav_bytes: bytes) -> bytes:
    """24kHz WAV → 8kHz telephony MP3 via ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fin:
        fin.write(wav_bytes)
        in_path = fin.name
    out_path = in_path.replace(".wav", ".mp3")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", in_path,
                "-af", "highpass=f=300,lowpass=f=3400,volume=2dB",
                "-ar", "8000", "-ac", "1", "-b:a", "32k", out_path,
            ],
            check=True, capture_output=True,
        )
        return Path(out_path).read_bytes()
    finally:
        os.unlink(in_path)
        if os.path.exists(out_path):
            os.unlink(out_path)


# ── Catbox upload ───────────────────────────────────────────────────────────────

def upload_catbox(data: bytes, filename: str) -> str:
    boundary = "VoiceOSBoundary7x"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="reqtype"\r\n\r\nuserfile\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="fileToUpload"; filename="{filename}"\r\n'
        f"Content-Type: audio/mpeg\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://catbox.moe/user/api.php",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode().strip()


# ── Twilio helpers ──────────────────────────────────────────────────────────────

def _twilio_request(path: str, data: dict | None = None) -> dict:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}{path}"
    encoded = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=encoded, method="POST" if data else "GET")
    req.add_header("Authorization", f"Basic {TWILIO_CREDS}")
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def place_call(twiml: str) -> str:
    resp = _twilio_request("/Calls.json", {
        "From":   TWILIO_FROM,
        "To":     TWILIO_TO,
        "Twiml":  twiml,
        "Record": "true",
    })
    return resp["sid"]


def wait_call(call_sid: str, max_s: int = 120) -> str:
    for i in range(max_s // 5):
        time.sleep(5)
        d = _twilio_request(f"/Calls/{call_sid}.json")
        status = d["status"]
        log(f"    [{(i+1)*5:3d}s] {status}  dur={d.get('duration','?')}s")
        if status in ("completed", "failed", "canceled", "no-answer", "busy"):
            return status
    return "timeout"


def download_recording(call_sid: str) -> bytes:
    time.sleep(4)  # let Twilio finalise the recording
    recs = _twilio_request(f"/Calls/{call_sid}/Recordings.json")["recordings"]
    if not recs:
        raise RuntimeError(f"No recording for call {call_sid}")
    rec_sid = recs[0]["sid"]
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"
        f"/Recordings/{rec_sid}.wav"
    )
    req.add_header("Authorization", f"Basic {TWILIO_CREDS}")
    with urllib.request.urlopen(req) as r:
        return r.read()


# ── Evaluator ───────────────────────────────────────────────────────────────────

def evaluate(wav_bytes: bytes, label: str) -> dict:
    boundary = "EvalBoundary9z"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="wav_file"; filename="{label}.wav"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        EVALUATOR_URL, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    log("=" * 70)
    log("VoiceOS A/B Voice Evaluation — Polly vs Veena")
    log(f"Test text ({len(TEST_TEXT)} chars):")
    log(f"  {TEST_TEXT[:100]}…")
    log("=" * 70)

    results: dict = {"test_text": TEST_TEXT}

    # ══════════════════════════════════════════════════════════════════════════
    # ARM A1 — Veena raw (24kHz, no telephony)
    # ══════════════════════════════════════════════════════════════════════════
    log("\n[ARM-A1] Generating Veena audio …")
    pcm, veena_metrics = generate_veena(TEST_TEXT)
    log(f"  TTFA           : {veena_metrics['ttfa_ms']} ms")
    log(f"  Full gen       : {veena_metrics['full_gen_ms']} ms")
    log(f"  Duration       : {veena_metrics['duration_s']} s")
    log(f"  Realtime factor: {veena_metrics['realtime_factor']}x")

    veena_wav = pcm_float32_to_wav(pcm)
    (OUTPUT_DIR / "arm_a1_veena_raw.wav").write_bytes(veena_wav)
    log(f"  WAV saved      : {len(veena_wav):,} bytes")

    log("[ARM-A1] Evaluating raw Veena audio with Qwen2.5-Omni …")
    a1_eval = evaluate(veena_wav, "arm_a1_veena_raw")
    log(f"  Eval time      : {a1_eval['elapsed_ms']} ms")
    (OUTPUT_DIR / "arm_a1_eval.json").write_text(json.dumps(a1_eval, indent=2))

    results["arm_a1_veena_raw"] = {
        "latency": veena_metrics,
        "wav_info": a1_eval["wav_info"],
        "evaluation": a1_eval["evaluation"],
        "eval_ms": a1_eval["elapsed_ms"],
    }

    # ══════════════════════════════════════════════════════════════════════════
    # ARM A2 — Veena through telephony (same path as Polly)
    # ══════════════════════════════════════════════════════════════════════════
    log("\n[ARM-A2] Converting Veena to 8kHz telephony MP3 …")
    mp3_bytes = wav_to_telephony_mp3(veena_wav)
    log(f"  MP3 size       : {len(mp3_bytes):,} bytes")

    log("[ARM-A2] Uploading to catbox.moe …")
    veena_url = upload_catbox(mp3_bytes, "ab-arm-a2-veena.mp3")
    log(f"  Public URL     : {veena_url}")

    safe_url = veena_url.replace("&", "&amp;")
    veena_twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Play>{safe_url}</Play>'
        '<Pause length="2"/><Hangup/></Response>'
    )

    log("[ARM-A2] Placing Veena telephony call …")
    a2_sid = place_call(veena_twiml)
    log(f"  Call SID       : {a2_sid}")
    a2_status = wait_call(a2_sid)
    log(f"  Final status   : {a2_status}")

    log("[ARM-A2] Downloading Veena telephony recording …")
    a2_rec = download_recording(a2_sid)
    (OUTPUT_DIR / "arm_a2_veena_telephony.wav").write_bytes(a2_rec)
    log(f"  Recording size : {len(a2_rec):,} bytes")

    log("[ARM-A2] Evaluating Veena telephony audio …")
    a2_eval = evaluate(a2_rec, "arm_a2_veena_telephony")
    log(f"  Eval time      : {a2_eval['elapsed_ms']} ms")
    (OUTPUT_DIR / "arm_a2_eval.json").write_text(json.dumps(a2_eval, indent=2))

    results["arm_a2_veena_telephony"] = {
        "call_sid": a2_sid,
        "call_status": a2_status,
        "recording_bytes": len(a2_rec),
        "wav_info": a2_eval["wav_info"],
        "evaluation": a2_eval["evaluation"],
        "eval_ms": a2_eval["elapsed_ms"],
    }

    # ══════════════════════════════════════════════════════════════════════════
    # ARM B — Polly through telephony (Twilio-native)
    # ══════════════════════════════════════════════════════════════════════════
    safe_text = (TEST_TEXT
                 .replace("&", "and")
                 .replace("<", "")
                 .replace(">", "")
                 .replace('"', "'"))
    polly_twiml = (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Say language="hi-IN" voice="Polly.Aditi">{safe_text}</Say>'
        '<Pause length="2"/><Hangup/></Response>'
    )

    log("\n[ARM-B] Placing Polly telephony call …")
    b_sid = place_call(polly_twiml)
    log(f"  Call SID       : {b_sid}")
    b_status = wait_call(b_sid)
    log(f"  Final status   : {b_status}")

    log("[ARM-B] Downloading Polly telephony recording …")
    b_rec = download_recording(b_sid)
    (OUTPUT_DIR / "arm_b_polly_telephony.wav").write_bytes(b_rec)
    log(f"  Recording size : {len(b_rec):,} bytes")

    log("[ARM-B] Evaluating Polly telephony audio …")
    b_eval = evaluate(b_rec, "arm_b_polly_telephony")
    log(f"  Eval time      : {b_eval['elapsed_ms']} ms")
    (OUTPUT_DIR / "arm_b_eval.json").write_text(json.dumps(b_eval, indent=2))

    results["arm_b_polly_telephony"] = {
        "call_sid": b_sid,
        "call_status": b_status,
        "recording_bytes": len(b_rec),
        "wav_info": b_eval["wav_info"],
        "evaluation": b_eval["evaluation"],
        "eval_ms": b_eval["elapsed_ms"],
    }

    # ══════════════════════════════════════════════════════════════════════════
    # Full report
    # ══════════════════════════════════════════════════════════════════════════
    (OUTPUT_DIR / "ab_evaluation_report.json").write_text(json.dumps(results, indent=2))

    log("\n" + "=" * 70)
    log("A/B EVALUATION REPORT")
    log("=" * 70)

    log("\n── LATENCY (Veena only — Polly is Twilio-managed, not measurable) ───")
    m = veena_metrics
    log(f"  TTFA (first audio chunk) : {m['ttfa_ms']} ms")
    log(f"  Full generation          : {m['full_gen_ms']} ms")
    log(f"  Audio duration           : {m['duration_s']} s")
    log(f"  Realtime factor          : {m['realtime_factor']}x  (target: <1.0 for streaming)")

    log("\n── ARM-A1: VEENA RAW (24kHz — no telephony codec) ──────────────────")
    log(results["arm_a1_veena_raw"]["evaluation"])

    log("\n── ARM-A2: VEENA TELEPHONY (8kHz → MP3 → Twilio <Play> → recording) ─")
    log(results["arm_a2_veena_telephony"]["evaluation"])

    log("\n── ARM-B: POLLY TELEPHONY (Twilio <Say Polly.Aditi> → recording) ────")
    log(results["arm_b_polly_telephony"]["evaluation"])

    log("\n── FILES ────────────────────────────────────────────────────────────")
    for f in sorted(OUTPUT_DIR.iterdir()):
        log(f"  {f.name:45s}  {f.stat().st_size:>10,} bytes")

    log("\nFull report: " + str(OUTPUT_DIR / "ab_evaluation_report.json"))
    log("=" * 70)


if __name__ == "__main__":
    main()
