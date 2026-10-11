#!/usr/bin/env python3
"""MOS (Mean Opinion Score) evaluation for Veena 3B TTS audio output.

Implements the Sprint-029 Phase 2 Audio Quality gate: audio MOS ≥ 3.5/5
(ITU-T P.800 scale — Good quality, acceptable for enterprise deployments).

Two modes are supported:

1. Automated MOS estimation via DNSMOS P.835 (Microsoft research model):
   Runs offline on WAV files using the DNSMOS ONNX model.
   Install: pip install onnxruntime soundfile

2. Crowdsourcing stub (manual):
   Produces a CSV of clips for human MOS annotation. Human annotators
   use the ITU-T P.800 5-point scale.

Usage:
    # Estimate MOS on a directory of WAV files (automated, requires DNSMOS)
    python3 scripts/validate/mos_check.py \\
        --audio-dir evaluation/audio-samples/production/ \\
        --mode dnsmos

    # Produce annotation CSV for human crowdsourcing
    python3 scripts/validate/mos_check.py \\
        --audio-dir evaluation/audio-samples/production/ \\
        --mode crowdsource \\
        --output evaluation/mos-annotation-tasks.csv

    # Record audio from live TTS server for scoring
    python3 scripts/validate/mos_check.py \\
        --mode capture \\
        --tts-host 217.18.55.78 \\
        --texts evaluation/mos-sample-texts.txt \\
        --output-dir evaluation/audio-samples/production/

Gate: MOS ≥ 3.5/5 across all evaluated clips (Sprint-029 AC, Phase 2).

Architecture: Sprint-029.md Phase 2; ITU-T P.800; DocSuite-10 §10.4.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voiceos.validate.mos")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# ITU-T P.800 MOS scale
MOS_GATE = 3.5
_QUALITY_LABELS = {
    1: "Bad",
    2: "Poor",
    3: "Fair",
    4: "Good",
    5: "Excellent",
}

# Sample Hindi/Hinglish TTS test texts (representative of production agent turns)
_DEFAULT_TEST_TEXTS = [
    "Namaste, main VoiceOS se bol raha hun. Aapka bakaya Rs. 25,000 hai.",
    "Kya aap aaj kuch payment kar sakte hain? Hum aapki madad karna chahte hain.",
    "Aapki financial situation samajh aata hai. Kya hum ek installment plan bana sakte hain?",
    "Theek hai, main yeh note kar leta hun. Aap kal tak confirm karein.",
    "Shukriya aapke saath baat karne ke liye. Hum jald hi sampark karenge.",
    "Aapka loan ID LOAN-2024-001 hai aur next EMI 10 August ko hai.",
    "Agar aap abhi Rs. 12,500 de dete hain, toh hum account regularize kar sakte hain.",
    "Main samajhta hun ki situation mushkil hai. Kya koi aur rasta hai jisse hum madad kar sakein?",
]


# ---------------------------------------------------------------------------
# Mode: capture — record audio from TTS server
# ---------------------------------------------------------------------------

def mode_capture(tts_host: str, texts_file: str | None, output_dir: Path) -> None:
    """Synthesize texts via the live TTS server and save WAV files."""
    import base64
    import io
    import struct
    import urllib.request

    try:
        import soundfile  # type: ignore[import-untyped]  # noqa: F401
        _has_soundfile = True
    except ImportError:
        _has_soundfile = False

    texts = _DEFAULT_TEST_TEXTS
    if texts_file:
        texts = Path(texts_file).read_text(encoding="utf-8").splitlines()
        texts = [t.strip() for t in texts if t.strip()]

    output_dir.mkdir(parents=True, exist_ok=True)
    tts_url = f"http://{tts_host}:8200/synthesize"
    sample_rate = 24000

    for i, text in enumerate(texts):
        slug = f"mos_sample_{i:03d}"
        payload = json.dumps({"text": text, "speaker": "kavya"}).encode()
        req = urllib.request.Request(tts_url, data=payload, headers={"Content-Type": "application/json"})
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(req, timeout=30) as resp:
                pcm_bytes = resp.read()
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.info("Synthesized sample %03d in %.0f ms (%d bytes PCM)", i, elapsed_ms, len(pcm_bytes))
        except Exception as exc:
            logger.error("TTS synthesis failed for sample %03d: %s", i, exc)
            continue

        # PCM float32 LE → WAV via struct
        wav_path = output_dir / f"{slug}.wav"
        n_samples = len(pcm_bytes) // 4
        with open(wav_path, "wb") as fout:
            # WAV header (44 bytes, PCM format)
            data_size = n_samples * 2  # 16-bit output
            fout.write(b"RIFF")
            fout.write(struct.pack("<I", 36 + data_size))
            fout.write(b"WAVEfmt ")
            fout.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
            fout.write(b"data")
            fout.write(struct.pack("<I", data_size))
            # Convert float32 → int16
            import array
            floats = array.array("f")
            floats.frombytes(pcm_bytes)
            ints = array.array("h", (max(-32768, min(32767, int(f * 32767))) for f in floats))
            fout.write(ints.tobytes())
        logger.info("Saved %s", wav_path)

        # Save metadata sidecar
        meta_path = output_dir / f"{slug}.json"
        meta_path.write_text(
            json.dumps({"text": text, "speaker": "kavya", "ttfa_ms": elapsed_ms, "wav": slug + ".wav"},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    logger.info("Captured %d audio samples to %s", len(texts), output_dir)
    logger.info("Next step: run --mode dnsmos or --mode crowdsource on %s", output_dir)


# ---------------------------------------------------------------------------
# Mode: dnsmos — automated MOS estimation
# ---------------------------------------------------------------------------

def mode_dnsmos(audio_dir: Path) -> None:
    """Run DNSMOS P.835 on all WAV files in audio_dir and report MOS."""
    try:
        import numpy as np  # type: ignore[import-untyped]
        import soundfile as sf  # type: ignore[import-untyped]
        import onnxruntime as ort  # type: ignore[import-untyped]
    except ImportError as exc:
        logger.error("DNSMOS requires: pip install onnxruntime soundfile numpy. Missing: %s", exc)
        sys.exit(1)

    # DNSMOS model path — download from:
    # https://github.com/microsoft/DNS-Challenge/tree/master/DNSMOS/DNSMOS
    dnsmos_model_path = os.environ.get("DNSMOS_MODEL_PATH", "models/dnsmos/sig_bak_ovr.onnx")
    if not Path(dnsmos_model_path).exists():
        logger.error(
            "DNSMOS model not found at %s. Set DNSMOS_MODEL_PATH env var or download from "
            "https://github.com/microsoft/DNS-Challenge/tree/master/DNSMOS/DNSMOS",
            dnsmos_model_path,
        )
        sys.exit(1)

    sess = ort.InferenceSession(dnsmos_model_path)
    wav_files = sorted(audio_dir.glob("*.wav"))
    if not wav_files:
        logger.error("No WAV files found in %s", audio_dir)
        sys.exit(1)

    scores: list[float] = []
    results = []
    for wav_path in wav_files:
        try:
            audio, sr = sf.read(str(wav_path))
            if sr != 16000:
                # DNSMOS expects 16 kHz — resample if needed
                import scipy.signal as sig  # type: ignore[import-untyped]
                audio = sig.resample(audio, int(len(audio) * 16000 / sr))
            if audio.ndim > 1:
                audio = audio[:, 0]
            audio = audio.astype(np.float32)
            input_length = 9 * 16000  # 9s DNSMOS window
            if len(audio) < input_length:
                audio = np.pad(audio, (0, input_length - len(audio)))
            audio = audio[:input_length].reshape(1, -1)
            out = sess.run(None, {"input_1": audio})[0]
            ovrl_mos = float(out[0][2])  # OVRl column
            scores.append(ovrl_mos)
            results.append({"file": wav_path.name, "mos": round(ovrl_mos, 3)})
            logger.info("%s — MOS: %.2f (%s)", wav_path.name, ovrl_mos, _quality_label(ovrl_mos))
        except Exception as exc:
            logger.error("DNSMOS failed for %s: %s", wav_path.name, exc)

    if not scores:
        logger.error("No scores computed")
        sys.exit(1)

    avg_mos = sum(scores) / len(scores)
    gate_pass = avg_mos >= MOS_GATE
    logger.info("=" * 60)
    logger.info("MOS Results: %d clips | Average: %.3f | Gate ≥%.1f: %s",
                len(scores), avg_mos, MOS_GATE, "PASS ✓" if gate_pass else "FAIL ✗")
    logger.info("=" * 60)

    report_path = audio_dir.parent / "mos-report.json"
    report_path.write_text(json.dumps({"clips": results, "average_mos": avg_mos,
                                        "gate": MOS_GATE, "pass": gate_pass}, indent=2),
                           encoding="utf-8")
    logger.info("Report written to %s", report_path)
    sys.exit(0 if gate_pass else 1)


def _quality_label(mos: float) -> str:
    for threshold, label in sorted(_QUALITY_LABELS.items(), reverse=True):
        if mos >= threshold - 0.5:
            return label
    return "Bad"


# ---------------------------------------------------------------------------
# Mode: crowdsource — CSV for human annotation
# ---------------------------------------------------------------------------

def mode_crowdsource(audio_dir: Path, output_csv: Path) -> None:
    """Generate a CSV of WAV files for human MOS annotation (ITU-T P.800)."""
    wav_files = sorted(audio_dir.glob("*.wav"))
    if not wav_files:
        logger.error("No WAV files found in %s", audio_dir)
        sys.exit(1)

    with open(output_csv, "w", newline="", encoding="utf-8") as fout:
        writer = csv.writer(fout)
        writer.writerow(["clip_id", "file_path", "text", "mos_score", "annotator_notes"])
        for i, wav_path in enumerate(wav_files):
            meta_path = wav_path.with_suffix(".json")
            text = ""
            if meta_path.exists():
                text = json.loads(meta_path.read_text(encoding="utf-8")).get("text", "")
            writer.writerow([f"MOS-{i:04d}", str(wav_path.absolute()), text, "", ""])

    logger.info("Crowdsource CSV written to %s (%d clips)", output_csv, len(wav_files))
    logger.info("Annotators: score each clip 1–5 per ITU-T P.800 (1=Bad, 3=Fair, 5=Excellent)")
    logger.info("Gate: average ≥ %.1f/5 required for Sprint-029 Phase 2 PASS", MOS_GATE)


# ---------------------------------------------------------------------------
# Mode: score — read completed annotation CSV and report
# ---------------------------------------------------------------------------

def mode_score(annotation_csv: Path) -> None:
    """Read a completed annotation CSV and compute average MOS."""
    with open(annotation_csv, newline="", encoding="utf-8") as fin:
        rows = list(csv.DictReader(fin))

    scores = []
    for row in rows:
        raw = row.get("mos_score", "").strip()
        if raw:
            try:
                scores.append(float(raw))
            except ValueError:
                pass

    if not scores:
        logger.error("No annotated scores found in %s", annotation_csv)
        sys.exit(1)

    avg_mos = sum(scores) / len(scores)
    gate_pass = avg_mos >= MOS_GATE
    logger.info("Human MOS: %d clips annotated | Average: %.3f | Gate ≥%.1f: %s",
                len(scores), avg_mos, MOS_GATE, "PASS ✓" if gate_pass else "FAIL ✗")
    sys.exit(0 if gate_pass else 1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS Veena TTS MOS evaluation (Sprint-029 Phase 2)")
    parser.add_argument("--mode", choices=["capture", "dnsmos", "crowdsource", "score"], required=True)
    parser.add_argument("--audio-dir", default="evaluation/audio-samples/production/",
                        help="Directory of WAV files (for dnsmos/crowdsource modes)")
    parser.add_argument("--output", help="Output path (CSV for crowdsource; dir for capture)")
    parser.add_argument("--tts-host", default="217.18.55.78", help="GPU node IP for capture mode")
    parser.add_argument("--texts", help="File with TTS texts (one per line) for capture mode")
    args = parser.parse_args()

    if args.mode == "capture":
        output_dir = Path(args.output or "evaluation/audio-samples/production/")
        mode_capture(args.tts_host, args.texts, output_dir)
    elif args.mode == "dnsmos":
        mode_dnsmos(Path(args.audio_dir))
    elif args.mode == "crowdsource":
        output_csv = Path(args.output or "evaluation/mos-annotation-tasks.csv")
        mode_crowdsource(Path(args.audio_dir), output_csv)
    elif args.mode == "score":
        if not args.output:
            logger.error("--output <completed-csv> required for score mode")
            sys.exit(1)
        mode_score(Path(args.output))


if __name__ == "__main__":
    main()
