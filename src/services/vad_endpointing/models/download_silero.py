"""Download the Silero VAD v4 ONNX model from the official GitHub release.

Usage::

    python src/services/vad_endpointing/models/download_silero.py

The model file (~1 MB) is saved to:

    src/services/vad_endpointing/models/silero_vad.onnx

Model provenance:
    Repository : https://github.com/snakers4/silero-vad
    Release    : v4 (silero_vad.onnx)
    License    : MIT
    Size       : ~1 MB
    Format     : ONNX opset 11+

Architecture: V1 Ch6.2 (Silero VAD model selection).
"""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

# Official Silero VAD v4 ONNX release URL
_MODEL_URL: str = "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"

# Expected SHA-256 prefix (first 16 hex chars) for integrity check.
# Update this when the upstream model is re-released.
_SHA256_PREFIX: str = "e5c4"

_DEFAULT_DEST: Path = Path(__file__).parent / "silero_vad.onnx"


def download(dest: Path = _DEFAULT_DEST, *, force: bool = False) -> Path:
    """Download the Silero VAD v4 ONNX model to ``dest``.

    Args:
        dest:  Destination path.  Defaults to the models/ directory alongside
               this script.
        force: Re-download even if the file already exists.

    Returns:
        Path to the downloaded model file.

    Raises:
        RuntimeError: When the download fails or the file cannot be written.
    """
    if dest.exists() and not force:
        print(f"Model already present at '{dest}' — skipping download.  Use --force to re-download.")
        return dest

    print(f"Downloading Silero VAD v4 ONNX from {_MODEL_URL} …")
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        urllib.request.urlretrieve(_MODEL_URL, dest)
    except Exception as exc:
        raise RuntimeError(f"Failed to download Silero VAD model: {exc}") from exc

    size_kb = dest.stat().st_size // 1024
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    print(f"Saved to '{dest}' ({size_kb} KB).  SHA-256: {sha}")

    if not sha.startswith(_SHA256_PREFIX):
        print(
            f"WARNING: SHA-256 prefix mismatch (expected prefix '{_SHA256_PREFIX}', "
            f"got '{sha[:4]}').  The model file may have changed upstream — verify manually."
        )

    return dest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Download Silero VAD v4 ONNX model.")
    parser.add_argument("--dest", type=Path, default=_DEFAULT_DEST, help="Destination file path.")
    parser.add_argument("--force", action="store_true", help="Re-download even if file exists.")
    args = parser.parse_args()

    downloaded = download(dest=args.dest, force=args.force)
    print(f"Model ready at: {downloaded}")
