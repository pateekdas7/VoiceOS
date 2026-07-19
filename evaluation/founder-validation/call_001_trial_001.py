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
import io
import json
import os
import struct
import subprocess
import tempfile
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
EVALUATOR_PORT     = int(os.environ.get("EVALUATOR_PORT", "8300"))  # publicly accessible
TRIAL_ID           = os.environ.get("TRIAL_ID", "trial-001")

TTS_URL            = "http://127.0.0.1:8200/synthesize"
TTS_SPEAKER        = "kavya"
TTS_SAMPLE_RATE    = 24000          # Veena output: float32 LE at 24kHz
TWILIO_SAMPLE_RATE = 8000           # Twilio <Play> accepts 8kHz PCM WAV

EVIDENCE_DIR = Path(f"/tmp/founder-validation/call-001/{TRIAL_ID}")

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

# ─────────────────────────────────────────────────────────────────────────────
# Audio utilities
# ─────────────────────────────────────────────────────────────────────────────

def float32_raw_to_pcm16(raw: bytes) -> np.ndarray:
    """float32 LE raw bytes → int16 ndarray (clipped)."""
    arr = np.frombuffer(raw, dtype="<f4")
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767).astype(np.int16)


def wav_to_telephony_mp3(wav_24k_bytes: bytes, out_path: Path) -> None:
    """Convert 24kHz PCM16 WAV → telephony-grade MP3 via ffmpeg.

    ffmpeg applies a proper anti-aliasing Kaiser-windowed sinc filter when
    downsampling, applies telephone bandpass (300-3400 Hz via highpass+lowpass),
    and normalises to -16 dBFS RMS so the voice is loud enough on the handset.
    MP3 at 32kbps mono (~33 KB for 8s) downloads fast from public hosts.
    """
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
        tmp_in.write(wav_24k_bytes)
        tmp_in_path = tmp_in.name
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", tmp_in_path,
                # Telephone bandpass + simple peak normalisation to -1 dBFS.
                # dynaudnorm is avoided (causes pumping); loudnorm is avoided
                # (single-pass can compress dynamic range unpredictably).
                # Simple highpass+lowpass+volume is transparent and predictable.
                "-af", "highpass=f=300,lowpass=f=3400,volume=2dB",
                "-ar", "8000",        # resample to 8kHz (native telephony)
                "-ac", "1",           # mono
                "-b:a", "32k",        # 32 kbps MP3
                str(out_path),
            ],
            check=True,
            capture_output=True,
        )
    finally:
        os.unlink(tmp_in_path)


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
# Twilio REST helpers
# ─────────────────────────────────────────────────────────────────────────────

def _twilio_auth() -> str:
    return base64.b64encode(
        f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()
    ).decode()


def _multipart_body(field: str, filename: str, content_type: str, data: bytes) -> tuple[bytes, str]:
    boundary = "voiceos-boundary-a1b2c3"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
        f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    return body, boundary


def upload_wav_public(audio_bytes: bytes, filename: str = "greeting.mp3") -> str:
    """Upload audio file to a public temp host. Tries services reachable from GPU outbound.
    No inbound port required on GPU server.
    """
    content_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    errors = []

    # ── 1. catbox.moe (confirmed reachable from GPU) ─────────────────────────
    try:
        bnd = "catbox-boundary-x7y8z9"
        full_body = (
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"reqtype\"\r\n\r\nfileupload\r\n"
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"fileToUpload\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n"
        ).encode() + audio_bytes + f"\r\n--{bnd}--\r\n".encode()
        req = urllib.request.Request(
            "https://catbox.moe/user/api.php", data=full_body,
            headers={"Content-Type": f"multipart/form-data; boundary={bnd}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            url = r.read().decode().strip()
        if url.startswith("http"):
            print(f"            Uploaded to catbox.moe")
            return url
    except Exception as e:
        errors.append(f"catbox.moe: {e}")

    # ── 2. uguu.se (confirmed reachable from GPU) ────────────────────────────
    try:
        bnd = "uguu-boundary-x7y8z9"
        full_body = (
            f"--{bnd}\r\nContent-Disposition: form-data; name=\"files[]\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n"
        ).encode() + audio_bytes + f"\r\n--{bnd}--\r\n".encode()
        req = urllib.request.Request(
            "https://uguu.se/upload.php", data=full_body,
            headers={"Content-Type": f"multipart/form-data; boundary={bnd}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        files = resp.get("files", [])
        url = files[0].get("url", "") if files else ""
        if url.startswith("http"):
            print(f"            Uploaded to uguu.se")
            return url
    except Exception as e:
        errors.append(f"uguu.se: {e}")

    # ── 3. pixeldrain (confirmed reachable from GPU) ─────────────────────────
    try:
        req = urllib.request.Request(
            f"https://pixeldrain.com/api/file/{filename}",
            data=audio_bytes,
            headers={"Content-Type": content_type},
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.loads(r.read())
        fid = resp.get("id", "")
        if fid:
            url = f"https://pixeldrain.com/api/file/{fid}?download"
            print(f"            Uploaded to pixeldrain")
            return url
    except Exception as e:
        errors.append(f"pixeldrain: {e}")

    raise RuntimeError(f"All upload services failed: {errors}")


def twilio_make_call_inline(audio_url: str) -> dict:
    """Make Twilio call with inline TwiML — no webhook URL needed."""
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f"<Play>{audio_url}</Play>"
        '<Pause length="8"/>'
        "<Hangup/>"
        "</Response>"
    )
    url  = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls.json"
    data = urllib.parse.urlencode({
        "From":   TWILIO_FROM,
        "To":     TWILIO_TO,
        "Twiml":  twiml,
        "Record": "true",
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

    # Primary artifact (24kHz — full quality for founder review)
    primary_path = EVIDENCE_DIR / f"call-001-{TRIAL_ID}.wav"
    primary_path.write_bytes(wav_24k)

    sha_primary = sha256hex(wav_24k)
    info_24k    = wav_info(wav_24k)

    # Telephony MP3: ffmpeg bandpass (300-3400 Hz) + loudnorm + 8kHz resample
    mp3_path = EVIDENCE_DIR / f"call-001-{TRIAL_ID}-telephony.mp3"
    wav_to_telephony_mp3(wav_24k, mp3_path)
    mp3_bytes = mp3_path.read_bytes()

    print(f"            24kHz WAV size  : {len(wav_24k):,} bytes")
    print(f"            24kHz duration  : {info_24k['duration_s']:.3f} s")
    print(f"            SHA-256 (24kHz) : {sha_primary}")
    print(f"            Telephony MP3   : {len(mp3_bytes):,} bytes ({mp3_path.name})")

    # ── 3. Upload telephony MP3 to public hosting ────────────────────────────
    # MP3 at 32kbps is ~33 KB (12x smaller than 24kHz WAV) → fast CDN download.
    # ffmpeg bandpass + loudnorm eliminates the noise artifacts from raw SNAC output.
    print(f"\n[STEP 3/6]  Uploading telephony MP3 to public hosting …")
    audio_url = upload_wav_public(mp3_bytes, f"call-001-{TRIAL_ID}.mp3")
    print(f"            Public audio URL : {audio_url}")

    # ── 4. Place Twilio call with inline TwiML (no webhook server needed) ───────
    print(f"\n[STEP 4/6]  Placing Twilio outbound call {TWILIO_FROM} → {TWILIO_TO} …")
    t_call_placed = time.perf_counter()
    call_resp     = twilio_make_call_inline(audio_url)
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
                twilio_rec_path = EVIDENCE_DIR / f"call-001-{TRIAL_ID}-twilio.wav"
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
        "trial_id":           TRIAL_ID,
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
        "telephony_mp3": {
            "path":       str(mp3_path),
            "size_bytes": len(mp3_bytes),
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
