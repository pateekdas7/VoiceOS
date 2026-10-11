#!/usr/bin/env python3
"""
VoiceOS Founder Validation — Trial Evaluator
Sprint-029 Phase 2

Usage:
    python evaluate_trial.py <wav_path> [--metadata <json_path>] [--call-id <id>] [--trial-id <id>]

Produces 3 reports in the same directory as the WAV:
    report-A-engineering.md     — Objective engineering measurements
    report-B-omni-review.md     — Qwen2.5-Omni independent audio review
    report-C-combined.md        — Combined Founder Validation Summary

Does NOT mark PASS/FAIL. Waits for founder decision.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

EVALUATOR_URL = os.environ.get("EVALUATOR_URL", "http://127.0.0.1:8300")


# ─────────────────────────────────────────────────────────────────────────────
# Path A — Engineering measurements
# ─────────────────────────────────────────────────────────────────────────────

def measure_wav(wav_bytes: bytes) -> dict:
    """Extract all measurable properties from a WAV file."""
    buf = io.BytesIO(wav_bytes)
    with wave.open(buf) as w:
        n_frames    = w.getnframes()
        sample_rate = w.getframerate()
        n_channels  = w.getnchannels()
        sample_width = w.getsampwidth()
        raw_frames  = w.readframes(n_frames)

    duration_s = n_frames / sample_rate
    encoding   = f"PCM{sample_width * 8}"

    # Convert to float64 for analysis
    if sample_width == 2:
        samples = np.frombuffer(raw_frames, dtype="<i2").astype(np.float64) / 32768.0
    elif sample_width == 4:
        samples = np.frombuffer(raw_frames, dtype="<i4").astype(np.float64) / 2147483648.0
    else:
        samples = np.frombuffer(raw_frames, dtype=np.uint8).astype(np.float64) / 128.0 - 1.0

    if n_channels > 1:
        samples = samples[::n_channels]  # take first channel

    peak_amplitude  = float(np.max(np.abs(samples)))
    rms_level       = float(np.sqrt(np.mean(samples ** 2)))
    rms_dbfs        = 20 * np.log10(rms_level) if rms_level > 0 else -120.0
    peak_dbfs       = 20 * np.log10(peak_amplitude) if peak_amplitude > 0 else -120.0

    # Clipping detection (>= 0.999 full-scale)
    clip_threshold = 0.999
    clipped_samples = int(np.sum(np.abs(samples) >= clip_threshold))
    clipping_detected = clipped_samples > 0

    # Loudness (ITU-R BS.1770 approximation — simple energy gating)
    block_size = int(sample_rate * 0.4)  # 400ms blocks
    loudness_values = []
    for i in range(0, len(samples) - block_size, block_size // 4):
        block = samples[i : i + block_size]
        block_rms = np.sqrt(np.mean(block ** 2))
        if block_rms > 1e-5:
            loudness_values.append(20 * np.log10(block_rms))
    integrated_loudness_dbfs = float(np.mean(loudness_values)) if loudness_values else -120.0

    # Silence detection (< -50 dBFS)
    silence_threshold = 10 ** (-50 / 20)
    frame_size = int(sample_rate * 0.01)  # 10ms frames
    is_silent  = []
    for i in range(0, len(samples) - frame_size, frame_size):
        frame_rms = np.sqrt(np.mean(samples[i : i + frame_size] ** 2))
        is_silent.append(frame_rms < silence_threshold)

    silent_frames = sum(is_silent)
    total_frames  = len(is_silent)
    silence_duration_s = silent_frames * 0.01
    silence_ratio      = silent_frames / total_frames if total_frames > 0 else 0.0

    # Silence gaps (consecutive silent frames > 50ms → 5 frames)
    silence_gaps: list[dict] = []
    in_gap = False
    gap_start = 0
    for i, s in enumerate(is_silent):
        if s and not in_gap:
            in_gap = True
            gap_start = i
        elif not s and in_gap:
            gap_len = (i - gap_start) * 0.01
            if gap_len >= 0.05:  # >= 50ms
                silence_gaps.append({
                    "start_s": round(gap_start * 0.01, 3),
                    "end_s":   round(i * 0.01, 3),
                    "duration_s": round(gap_len, 3),
                })
            in_gap = False
    if in_gap:
        gap_len = (len(is_silent) - gap_start) * 0.01
        if gap_len >= 0.05:
            silence_gaps.append({
                "start_s": round(gap_start * 0.01, 3),
                "end_s":   round(duration_s, 3),
                "duration_s": round(gap_len, 3),
            })

    # Energy envelope for chunk boundary detection (energy variance per 20ms window)
    win_size = int(sample_rate * 0.02)
    energy_envelope = []
    for i in range(0, len(samples) - win_size, win_size):
        block_rms = float(np.sqrt(np.mean(samples[i : i + win_size] ** 2)))
        energy_envelope.append(block_rms)

    # Detect energy discontinuities (potential chunk boundaries)
    chunk_boundary_candidates: list[dict] = []
    if len(energy_envelope) > 2:
        for i in range(1, len(energy_envelope) - 1):
            ratio = (energy_envelope[i] / max(energy_envelope[i-1], 1e-9))
            if ratio < 0.1 or ratio > 10.0:  # >10x energy drop/spike
                chunk_boundary_candidates.append({
                    "time_s": round(i * 0.02, 3),
                    "energy_before": round(energy_envelope[i-1], 4),
                    "energy_at":     round(energy_envelope[i], 4),
                    "ratio":         round(ratio, 3),
                })

    # Leading / trailing silence
    leading_silence_s  = 0.0
    trailing_silence_s = 0.0
    for i, s in enumerate(is_silent):
        if not s:
            leading_silence_s = i * 0.01
            break
    for i in range(len(is_silent) - 1, -1, -1):
        if not is_silent[i]:
            trailing_silence_s = (len(is_silent) - 1 - i) * 0.01
            break

    return {
        "format":              f"WAV {encoding} {sample_rate}Hz {n_channels}ch",
        "encoding":            encoding,
        "sample_rate_hz":      sample_rate,
        "channels":            n_channels,
        "duration_s":          round(duration_s, 3),
        "n_frames":            n_frames,
        "file_size_bytes":     len(wav_bytes),
        "peak_amplitude":      round(peak_amplitude, 6),
        "peak_dbfs":           round(peak_dbfs, 2),
        "rms_level":           round(rms_level, 6),
        "rms_dbfs":            round(rms_dbfs, 2),
        "integrated_loudness_dbfs": round(integrated_loudness_dbfs, 2),
        "clipping_detected":   clipping_detected,
        "clipped_samples":     clipped_samples,
        "silence_duration_s":  round(silence_duration_s, 3),
        "silence_ratio":       round(silence_ratio, 3),
        "silence_gaps":        silence_gaps,
        "n_silence_gaps":      len(silence_gaps),
        "leading_silence_s":   round(leading_silence_s, 3),
        "trailing_silence_s":  round(trailing_silence_s, 3),
        "chunk_boundary_candidates": chunk_boundary_candidates,
        "n_chunk_boundaries":  len(chunk_boundary_candidates),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Path B — Qwen2.5-Omni audio review
# ─────────────────────────────────────────────────────────────────────────────

def submit_to_evaluator(wav_path: Path) -> dict:
    """POST WAV to the evaluator service, return parsed response."""
    wav_bytes = wav_path.read_bytes()
    boundary  = "----VoiceOSEvalBoundary"
    body      = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="wav_file"; filename="{wav_path.name}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n"
    ).encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{EVALUATOR_URL}/evaluate",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read())
        result["request_elapsed_ms"] = round((time.perf_counter() - t0) * 1000)
        return result
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"Evaluator HTTP {e.code}: {body_text}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Evaluator unreachable at {EVALUATOR_URL}: {e.reason}")


# ─────────────────────────────────────────────────────────────────────────────
# Report generators
# ─────────────────────────────────────────────────────────────────────────────

def generate_report_a(
    wav_path: Path,
    measurements: dict,
    metadata: dict | None,
    call_id: str,
    trial_id: str,
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta_section = ""
    if metadata:
        meta_section = f"""
## TTS Generation Metrics
| Metric | Value |
|---|---|
| TTFA | **{metadata.get('tts_ttfa_ms', 'N/A')} ms** |
| Total generation time | **{metadata.get('tts_total_gen_ms', 'N/A')} ms** |
| TTS model | {metadata.get('tts_model', 'N/A')} |
| TTS speaker | {metadata.get('tts_speaker', 'N/A')} |
| Text synthesized | `{metadata.get('greeting_text', '')[:120]}...` |

## Call Telemetry
| Metric | Value |
|---|---|
| Twilio Call SID | `{metadata.get('call_sid', 'N/A')}` |
| Call status | {metadata.get('twilio_call_status', 'N/A')} |
| PSTN call duration | {metadata.get('twilio_call_duration_s', 'N/A')} s |
| Borrower | {metadata.get('borrower_name', 'N/A')} |
| Outstanding | ₹{metadata.get('outstanding_inr', 'N/A')} |
| Due date | {metadata.get('due_date', 'N/A')} |
"""

    gap_section = ""
    gaps = measurements.get("silence_gaps", [])
    if gaps:
        gap_section = "\n### Silence Gaps (≥ 50ms)\n| # | Start | End | Duration |\n|---|---|---|---|\n"
        for i, g in enumerate(gaps, 1):
            gap_section += f"| {i} | {g['start_s']}s | {g['end_s']}s | **{g['duration_s']}s** |\n"
    else:
        gap_section = "\n### Silence Gaps\nNo gaps ≥ 50ms detected.\n"

    boundary_section = ""
    boundaries = measurements.get("chunk_boundary_candidates", [])
    if boundaries:
        boundary_section = "\n### Potential Chunk Boundaries (energy discontinuity)\n| Time | Energy Before | Energy At | Ratio |\n|---|---|---|---|\n"
        for b in boundaries:
            boundary_section += f"| {b['time_s']}s | {b['energy_before']} | {b['energy_at']} | {b['ratio']}× |\n"
    else:
        boundary_section = "\n### Chunk Boundaries\nNo significant energy discontinuities detected.\n"

    return f"""# Report A — Engineering Measurements
## {call_id} / {trial_id}
**Generated:** {ts}
**WAV file:** `{wav_path.name}`
**Evaluator:** Automated waveform analysis (Path A)

---

## Audio Properties
| Property | Value |
|---|---|
| Format | {measurements['format']} |
| Duration | **{measurements['duration_s']} s** |
| Sample rate | {measurements['sample_rate_hz']} Hz |
| Channels | {measurements['channels']} |
| Encoding | {measurements['encoding']} |
| File size | {measurements['file_size_bytes']:,} bytes |
| Total frames | {measurements['n_frames']:,} |
{meta_section}
## Amplitude & Loudness
| Metric | Value |
|---|---|
| Peak amplitude | {measurements['peak_amplitude']} ({measurements['peak_dbfs']} dBFS) |
| RMS level | {measurements['rms_level']} ({measurements['rms_dbfs']} dBFS) |
| Integrated loudness | {measurements['integrated_loudness_dbfs']} dBFS |
| Clipping detected | {'⚠️ YES — ' + str(measurements['clipped_samples']) + ' samples' if measurements['clipping_detected'] else '✅ No'} |

## Silence Analysis
| Metric | Value |
|---|---|
| Total silence duration | {measurements['silence_duration_s']} s ({measurements['silence_ratio']*100:.1f}% of audio) |
| Leading silence | {measurements['leading_silence_s']} s |
| Trailing silence | {measurements['trailing_silence_s']} s |
| Silence gaps (≥ 50ms) | {measurements['n_silence_gaps']} detected |
{gap_section}
## Streaming / Chunk Analysis
| Metric | Value |
|---|---|
| Chunk boundary candidates | {measurements['n_chunk_boundaries']} detected |
{boundary_section}
---

*Report A is generated from waveform mathematics. No subjective judgment is made here.*
"""


def generate_report_b(
    omni_result: dict,
    call_id: str,
    trial_id: str,
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"""# Report B — Qwen2.5-Omni Independent Audio Review
## {call_id} / {trial_id}
**Generated:** {ts}
**Model:** {omni_result.get('model', 'Qwen/Qwen2.5-Omni-7B')}
**WAV file:** `{omni_result.get('filename', 'unknown')}`
**Inference time:** {omni_result.get('elapsed_ms', 'N/A')} ms
**Evaluator:** Qwen2.5-Omni listening as an independent human reviewer (Path B)

**⚠️ Important:** Qwen2.5-Omni has no access to system internals, model names, or code.
It describes only what is audible in the WAV file. Its findings are independent of Report A.

---

{omni_result.get('evaluation', '*(No evaluation text returned)*')}

---

*Report B is generated solely from Qwen2.5-Omni's audio perception. No engineering data was provided to it.*
"""


def generate_report_c(
    report_a_text: str,
    report_b_text: str,
    measurements: dict,
    omni_result: dict,
    metadata: dict | None,
    call_id: str,
    trial_id: str,
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    meta_summary = ""
    if metadata:
        meta_summary = f"""
## Trial Context
- **Borrower:** {metadata.get('borrower_name', 'N/A')}
- **Greeting text:** {metadata.get('greeting_text', '')[:120]}...
- **TTS TTFA:** {metadata.get('tts_ttfa_ms', 'N/A')} ms
- **TTS total gen:** {metadata.get('tts_total_gen_ms', 'N/A')} ms
- **Call SID:** `{metadata.get('call_sid', 'N/A')}`
- **Call status:** {metadata.get('twilio_call_status', 'N/A')}
"""

    return f"""# Report C — Combined Founder Validation Summary
## {call_id} / {trial_id}
**Generated:** {ts}
**Status:** ⏳ AWAITING FOUNDER DECISION

---
{meta_summary}
## Engineering Evidence (Report A)
| Metric | Value |
|---|---|
| Audio duration | {measurements['duration_s']} s |
| Sample rate | {measurements['sample_rate_hz']} Hz |
| Peak dBFS | {measurements['peak_dbfs']} |
| RMS dBFS | {measurements['rms_dbfs']} |
| Integrated loudness | {measurements['integrated_loudness_dbfs']} dBFS |
| Clipping | {'⚠️ YES (' + str(measurements['clipped_samples']) + ' samples)' if measurements['clipping_detected'] else '✅ None'} |
| Silence duration | {measurements['silence_duration_s']} s ({measurements['silence_ratio']*100:.1f}%) |
| Silence gaps ≥ 50ms | {measurements['n_silence_gaps']} |
| Chunk boundary candidates | {measurements['n_chunk_boundaries']} |
| Leading silence | {measurements['leading_silence_s']} s |
| Trailing silence | {measurements['trailing_silence_s']} s |

## Qwen2.5-Omni Audio Review (Report B — summary excerpt)
*Full review in Report B. Key observations:*

{omni_result.get('evaluation', '*(No evaluation text)*')[:800]}...

*(See report-B-omni-review.md for the complete evaluation.)*

---

## Founder Review Checklist

Please listen to the WAV file and review Reports A and B, then answer:

1. **Naturalness:** Does it sound like a real person?
2. **Hindi pronunciation:** Are all Hindi words clear and correct?
3. **Hinglish flow:** Does code-switching sound natural?
4. **Name / number / date:** Prateek Das ji / ₹50,000 / 30 July 2026 — correct?
5. **Pace:** Too fast, too slow, or natural?
6. **Artifacts:** Any robotics, clicks, gaps, or stitching points you notice?
7. **Customer experience:** Would a borrower understand and trust this voice?
8. **Overall:** What is your subjective quality rating?

---

## ⛔ DO NOT ADVANCE

This report does not make a PASS or FAIL determination.
Do not proceed to the next trial.
**The Founder's explicit decision is required before any action is taken.**

---

*Report C synthesizes engineering data (Path A) and AI listening review (Path B).*
*It does not substitute for founder judgment.*
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS Founder Validation Trial Evaluator")
    parser.add_argument("wav_path", help="Path to WAV file to evaluate")
    parser.add_argument("--metadata", help="Path to call-metadata.json (optional)")
    parser.add_argument("--call-id", default="call-XXX", help="Call ID label")
    parser.add_argument("--trial-id", default="trial-XXX", help="Trial ID label")
    parser.add_argument("--skip-omni", action="store_true", help="Skip Qwen2.5-Omni (Path B only engineering report)")
    args = parser.parse_args()

    wav_path = Path(args.wav_path).resolve()
    if not wav_path.exists():
        print(f"ERROR: WAV file not found: {wav_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = wav_path.parent
    metadata: dict | None = None
    if args.metadata:
        meta_path = Path(args.metadata)
        if meta_path.exists():
            metadata = json.loads(meta_path.read_text())
        else:
            print(f"WARNING: Metadata file not found: {meta_path}")

    call_id  = args.call_id
    trial_id = args.trial_id

    print("=" * 70)
    print(f"VoiceOS Founder Validation — {call_id} / {trial_id}")
    print(f"WAV: {wav_path}")
    print(f"Output: {output_dir}")
    print("=" * 70)

    # ── Path A: Engineering measurements ─────────────────────────────────────
    print("\n[PATH A] Running engineering measurements …")
    t0 = time.perf_counter()
    wav_bytes    = wav_path.read_bytes()
    measurements = measure_wav(wav_bytes)
    print(f"         Duration:    {measurements['duration_s']} s")
    print(f"         Peak:        {measurements['peak_dbfs']} dBFS")
    print(f"         RMS:         {measurements['rms_dbfs']} dBFS")
    print(f"         Clipping:    {'YES ⚠️' if measurements['clipping_detected'] else 'No ✅'}")
    print(f"         Silence:     {measurements['silence_duration_s']} s ({measurements['silence_ratio']*100:.1f}%)")
    print(f"         Gaps ≥50ms:  {measurements['n_silence_gaps']}")
    print(f"         Boundaries:  {measurements['n_chunk_boundaries']}")
    print(f"         Done in {(time.perf_counter()-t0)*1000:.0f}ms")

    report_a = generate_report_a(wav_path, measurements, metadata, call_id, trial_id)
    (output_dir / "report-A-engineering.md").write_text(report_a)
    print(f"         → {output_dir}/report-A-engineering.md")

    # ── Path B: Qwen2.5-Omni review ──────────────────────────────────────────
    omni_result: dict = {}
    if not args.skip_omni:
        print(f"\n[PATH B] Submitting to Qwen2.5-Omni evaluator at {EVALUATOR_URL} …")

        # Check evaluator is ready
        try:
            with urllib.request.urlopen(f"{EVALUATOR_URL}/health/ready", timeout=10) as r:
                health = json.loads(r.read())
            print(f"         Evaluator: {health.get('status')} | VRAM free: {health.get('vram_free_gib')} GiB")
        except Exception as e:
            print(f"         WARNING: Evaluator health check failed: {e}")
            print("         Proceeding anyway …")

        t0 = time.perf_counter()
        try:
            omni_result = submit_to_evaluator(wav_path)
            elapsed = omni_result.get("request_elapsed_ms", round((time.perf_counter()-t0)*1000))
            print(f"         Evaluation received in {elapsed}ms")
            print(f"         Preview: {omni_result.get('evaluation','')[:120]}…")
        except Exception as exc:
            print(f"         ERROR: {exc}")
            omni_result = {
                "evaluation": f"[Evaluation failed: {exc}]",
                "model": "Qwen/Qwen2.5-Omni-7B",
                "filename": wav_path.name,
                "elapsed_ms": 0,
            }

        report_b = generate_report_b(omni_result, call_id, trial_id)
        (output_dir / "report-B-omni-review.md").write_text(report_b)
        print(f"         → {output_dir}/report-B-omni-review.md")
    else:
        print("\n[PATH B] Skipped (--skip-omni flag set)")
        omni_result = {"evaluation": "[Skipped]", "model": "N/A", "filename": wav_path.name, "elapsed_ms": 0}

    # ── Report C: Combined summary ────────────────────────────────────────────
    print("\n[REPORT C] Generating combined founder summary …")
    report_c = generate_report_c(report_a, report_b if not args.skip_omni else "", measurements, omni_result, metadata, call_id, trial_id)
    (output_dir / "report-C-combined.md").write_text(report_c)
    print(f"         → {output_dir}/report-C-combined.md")

    print("\n" + "=" * 70)
    print("EVALUATION COMPLETE — AWAITING FOUNDER DECISION")
    print("=" * 70)
    print(f"  Report A (Engineering):  {output_dir}/report-A-engineering.md")
    if not args.skip_omni:
        print(f"  Report B (Omni Review):  {output_dir}/report-B-omni-review.md")
    print(f"  Report C (Combined):     {output_dir}/report-C-combined.md")
    print()
    print("  ⛔ Do NOT advance to next trial without founder approval.")
    print("=" * 70)


if __name__ == "__main__":
    main()
