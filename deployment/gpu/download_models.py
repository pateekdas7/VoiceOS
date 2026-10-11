#!/usr/bin/env python3
"""Model downloader for VoiceOS GPU node.

Reads model_manifest.yaml and downloads any model whose local_path does not
already contain the expected files. Called by restore.sh before starting
inference services.

Usage:
    python3 download_models.py --manifest /opt/voiceos-gpu/deployment/model_manifest.yaml
"""

import argparse
import os
import sys
import time
from pathlib import Path

import yaml


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _is_populated(local_path: str) -> bool:
    """Return True if the model directory has at least one model file."""
    p = Path(local_path)
    if not p.exists():
        return False
    for f in p.rglob("*"):
        if f.is_file() and f.stat().st_size > 1024 * 1024:  # > 1 MB
            return True
    return False


def download_whisper(model: dict) -> None:
    """Download via faster-whisper's built-in download_model utility."""
    from faster_whisper.utils import download_model as fw_download

    model_size = model["download"]["model_size"]
    local_path = model["local_path"]
    os.makedirs(local_path, exist_ok=True)

    if _is_populated(local_path):
        log(f"Whisper {model_size}: already present at {local_path} — skipping")
        return

    log(f"Downloading Whisper {model_size} → {local_path} ...")
    log("  Source: mobiuslabsgmbh/faster-whisper-large-v3-turbo (CTranslate2 format)")
    log(f"  Expected size: ~{model['download'].get('download_size_mb', '?')} MB")

    path = fw_download(model_size, output_dir=local_path)
    log(f"Whisper download complete: {path}")


def download_huggingface(model: dict) -> None:
    """Download from HuggingFace Hub via snapshot_download."""
    from huggingface_hub import snapshot_download

    repo_id = model["download"]["repo_id"]
    local_path = model["local_path"]
    ignore_patterns = model["download"].get("ignore_patterns", [])
    os.makedirs(local_path, exist_ok=True)

    if _is_populated(local_path):
        log(f"{model['name']}: already present at {local_path} — skipping")
        return

    log(f"Downloading {repo_id} → {local_path} ...")
    log(f"  Expected size: ~{model['download'].get('download_size_mb', '?')} MB")

    path = snapshot_download(
        repo_id,
        local_dir=local_path,
        ignore_patterns=ignore_patterns or None,
    )
    log(f"Download complete: {path}")


def download_internal(model: dict) -> None:
    """Internal registry model — skip with a clear message."""
    registry = model["download"]["registry_path"]
    local_path = model["local_path"]

    if _is_populated(local_path):
        log(f"{model['name']}: already present at {local_path} — skipping")
        return

    log(f"SKIP: {model['name']} requires internal registry: {registry}")
    log("  Ensure VPN / internal network access and run: ")
    log(f"  docker pull {registry} && docker save {registry} | "
        f"tar -xO > {local_path}/veena.tar")
    log("  Or use the Coqui TTS dev fallback for local testing.")


DOWNLOADERS = {
    "faster_whisper": download_whisper,
    "huggingface": download_huggingface,
    "internal": download_internal,
}


def download_companion_codec(codec: dict) -> None:
    """Download a companion codec from HuggingFace Hub (e.g. SNAC 24 kHz for Veena)."""
    from huggingface_hub import snapshot_download

    repo_id = codec["repo_id"]
    local_path = codec["local_path"]
    os.makedirs(local_path, exist_ok=True)

    if _is_populated(local_path):
        log(f"Companion codec {codec['name']}: already present at {local_path} — skipping")
        return

    log(f"Downloading companion codec {repo_id} → {local_path} ...")
    path = snapshot_download(repo_id, local_dir=local_path)
    log(f"Companion codec download complete: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="VoiceOS GPU model downloader")
    parser.add_argument("--manifest", required=True, help="Path to model_manifest.yaml")
    parser.add_argument(
        "--model",
        default=None,
        help="Download only this model by name (default: all)",
    )
    args = parser.parse_args()

    with open(args.manifest) as f:
        manifest = yaml.safe_load(f)

    models = manifest.get("models", [])
    if not models:
        log("No models defined in manifest — nothing to download.")
        return

    for model in models:
        name = model["name"]
        if args.model and name != args.model:
            continue

        method = model.get("download", {}).get("method", "huggingface")
        downloader = DOWNLOADERS.get(method)
        if downloader is None:
            log(f"WARNING: Unknown download method '{method}' for {name} — skipping")
            continue

        log(f"--- {name} (method={method}) ---")
        try:
            downloader(model)
        except Exception as exc:
            log(f"ERROR downloading {name}: {exc}")
            sys.exit(1)

        codec = model.get("companion_codec")
        if codec:
            log(f"--- {name}: companion codec ({codec['name']}) ---")
            try:
                download_companion_codec(codec)
            except Exception as exc:
                log(f"ERROR downloading companion codec for {name}: {exc}")
                sys.exit(1)

    log("All model downloads complete.")


if __name__ == "__main__":
    main()
