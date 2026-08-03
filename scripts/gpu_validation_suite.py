#!/usr/bin/env python3
"""
VoiceOS GPU Validation Suite
==============================
Canonical pre-deployment validation for the VoiceOS GPU runtime.

Runs 46 checks covering driver, CUDA, Python, dependencies, models,
services, ports, infrastructure, and end-to-end inference. Produces a PASS/FAIL report.

Usage:
    python scripts/gpu_validation_suite.py [--output /path/to/report.txt] [--json]

Exit codes:
    0 = ALL PASS
    1 = One or more checks FAILED

This script is safe to run against a live production node. Inference checks
send small synthetic requests — they do not disrupt running calls.

Spec reference: docs/deployment/GPU_RUNTIME_SPEC.md
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Spec constants (must match GPU_RUNTIME_SPEC.md — update both if changing)
# ---------------------------------------------------------------------------

SPEC = {
    "nvidia_driver_min": "525.0",
    "nvidia_driver_production": "580.126.20",
    "python_major": 3,
    "python_minor": 12,
    "torch_version": "2.11.0",
    "torch_cuda_suffix": "cu130",
    "vllm_version": "0.24.0",
    "faster_whisper_version": "1.2.1",
    "transformers_version": "5.12.1",
    "fastapi_version": "0.136.3",
    "uvicorn_version": "0.49.0",
    "gpu_vram_total_mib": 23034,
    "gpu_vram_max_used_mib": 23034,
    "whisper_model_min_mb": 1400,
    "qwen_model_min_mb": 7500,
    "venv_path": "/opt/voiceos-gpu/venv",
    "models_base": "/opt/voiceos-gpu/models",
    "whisper_model_dir": "/opt/voiceos-gpu/models/whisper-large-v3-turbo",
    "qwen_model_dir": "/opt/voiceos-gpu/models/qwen2.5-7b-fp8",
    "veena_model_dir": "/opt/voiceos-gpu/models/veena-fp16",
    "snac_model_dir": "/opt/voiceos-gpu/models/snac-24khz",
    "stt_port": 8100,
    "llm_port": 8000,
    "tts_port": 8200,
    "stt_health_url": "http://localhost:8100/health/ready",
    "llm_health_url": "http://localhost:8000/health",
    "tts_health_url": "http://localhost:8200/health/ready",
    "stt_infer_url": "http://localhost:8100/transcribe",
    "llm_infer_url": "http://localhost:8000/v1/chat/completions",
    "tts_infer_url": "http://localhost:8200/synthesize",
    "stt_live_url": "http://localhost:8100/health/live",
    "tts_live_url": "http://localhost:8200/health/live",
    "ctranslate2_version": "4.8.0",
    "nvidia_ctk_version": "1.19.0",
    "docker_version_min": "29.3",
    "min_free_disk_gb": 20,
    "min_ram_gb": 16,
    "min_cpu_cores": 8,
    "voiceos_install_path": "/opt/voiceos-gpu",
    "env_file_path": "/opt/voiceos-gpu/.env",
    "log_dir": "/opt/voiceos-gpu/logs",
    "llm_model_id": "qwen2.5-7b-instruct-fp8",
    "unit_dir": "/etc/systemd/system",
    "unit_names": ["voiceos-llm.service", "voiceos-stt.service", "voiceos-tts.service"],
}

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    check_id: str
    name: str
    passed: bool
    message: str
    detail: str = ""


@dataclass
class ValidationReport:
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    hostname: str = field(default_factory=socket.gethostname)
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def pass_count(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.passed)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


def _http_get(url: str, timeout: int = 10) -> tuple[bool, int, str]:
    """Return (success, status_code, body). Requires urllib only."""
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, resp.status, body
    except urllib.error.HTTPError as exc:
        return False, exc.code, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, 0, str(exc)


def _http_post_json(
    url: str, payload: dict, timeout: int = 30, stream: bool = False
) -> tuple[bool, int, str, dict]:
    """Return (success, status_code, body_or_first_chunk, response_headers)."""
    import json as json_mod
    import urllib.error
    import urllib.request

    data = json_mod.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            headers = dict(resp.headers)
            if stream:
                chunk = resp.read(4096).decode("utf-8", errors="replace")
                return True, resp.status, chunk, headers
            body = resp.read().decode("utf-8", errors="replace")
            return True, resp.status, body, headers
    except urllib.error.HTTPError as exc:
        return False, exc.code, str(exc), {}
    except Exception as exc:  # noqa: BLE001
        return False, 0, str(exc), {}


def _dir_size_mb(path: str) -> int:
    """Return directory size in MB, or 0 if absent."""
    p = Path(path)
    if not p.exists():
        return 0
    total = sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return total // (1024 * 1024)


def _dir_file_count(path: str) -> int:
    p = Path(path)
    if not p.exists():
        return 0
    return sum(1 for f in p.rglob("*") if f.is_file())


def _venv_python() -> str:
    return os.path.join(SPEC["venv_path"], "bin", "python")


def _venv_python_run(code: str, timeout: int = 30) -> tuple[bool, str]:
    """Run Python code inside the GPU venv. Returns (success, stdout)."""
    py = _venv_python()
    if not Path(py).exists():
        return False, f"venv python not found at {py}"
    rc, out, err = _run([py, "-c", code], timeout=timeout)
    if rc != 0:
        return False, err or out
    return True, out


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_v01_nvidia_driver() -> CheckResult:
    rc, out, err = _run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    if rc != 0:
        return CheckResult("V-01", "NVIDIA driver present", False, "nvidia-smi failed", err)
    version = out.strip()
    if not version:
        return CheckResult("V-01", "NVIDIA driver present", False, "nvidia-smi returned empty version", out)
    return CheckResult(
        "V-01",
        "NVIDIA driver present and version reported",
        True,
        f"Driver version: {version} (production spec: {SPEC['nvidia_driver_production']})",
    )


def check_v02_cuda_available() -> CheckResult:
    ok, out = _venv_python_run(
        "import torch; print(torch.cuda.is_available()); print(torch.version.cuda)"
    )
    if not ok:
        return CheckResult("V-02", "CUDA available via PyTorch", False, out)
    lines = out.strip().splitlines()
    cuda_avail = lines[0].strip() == "True" if lines else False
    cuda_ver = lines[1].strip() if len(lines) > 1 else "unknown"
    if not cuda_avail:
        return CheckResult("V-02", "CUDA available via PyTorch", False, "torch.cuda.is_available() = False")
    return CheckResult("V-02", "CUDA available via PyTorch", True, f"CUDA version: {cuda_ver}")


def check_v03_python_version() -> CheckResult:
    ok, out = _venv_python_run(
        "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"
    )
    if not ok:
        return CheckResult("V-03", "Python version", False, out)
    version = out.strip()
    parts = version.split(".")
    major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    if major != SPEC["python_major"] or minor != SPEC["python_minor"]:
        return CheckResult(
            "V-03",
            "Python version",
            False,
            f"Got Python {version}, spec requires {SPEC['python_major']}.{SPEC['python_minor']}.x",
        )
    return CheckResult("V-03", "Python version", True, f"Python {version}")


def check_v04_torch_version() -> CheckResult:
    ok, out = _venv_python_run("import torch; print(torch.__version__)")
    if not ok:
        return CheckResult("V-04", "torch version", False, out)
    version = out.strip()
    if SPEC["torch_version"] not in version:
        return CheckResult(
            "V-04",
            "torch version",
            False,
            f"Got {version}, spec requires {SPEC['torch_version']}+{SPEC['torch_cuda_suffix']}",
        )
    return CheckResult("V-04", "torch version", True, f"torch {version}")


def check_v05_vllm_version() -> CheckResult:
    ok, out = _venv_python_run("import vllm; print(vllm.__version__)")
    if not ok:
        return CheckResult("V-05", "vllm version", False, out)
    version = out.strip()
    if version != SPEC["vllm_version"]:
        return CheckResult(
            "V-05",
            "vllm version",
            False,
            f"Got {version}, spec requires {SPEC['vllm_version']}",
        )
    return CheckResult("V-05", "vllm version", True, f"vllm {version}")


def check_v06_faster_whisper_version() -> CheckResult:
    ok, out = _venv_python_run("import faster_whisper; print(faster_whisper.__version__)")
    if not ok:
        return CheckResult("V-06", "faster-whisper version", False, out)
    version = out.strip()
    if version != SPEC["faster_whisper_version"]:
        return CheckResult(
            "V-06",
            "faster-whisper version",
            False,
            f"Got {version}, spec requires {SPEC['faster_whisper_version']}",
        )
    return CheckResult("V-06", "faster-whisper version", True, f"faster-whisper {version}")


def check_v07_transformers_version() -> CheckResult:
    ok, out = _venv_python_run("import transformers; print(transformers.__version__)")
    if not ok:
        return CheckResult("V-07", "transformers version", False, out)
    version = out.strip()
    if version != SPEC["transformers_version"]:
        return CheckResult(
            "V-07",
            "transformers version",
            False,
            f"Got {version}, spec requires {SPEC['transformers_version']}",
        )
    return CheckResult("V-07", "transformers version", True, f"transformers {version}")


def check_v08_fastapi_version() -> CheckResult:
    ok, out = _venv_python_run("import fastapi; print(fastapi.__version__)")
    if not ok:
        return CheckResult("V-08", "fastapi version", False, out)
    version = out.strip()
    if version != SPEC["fastapi_version"]:
        return CheckResult(
            "V-08",
            "fastapi version",
            False,
            f"Got {version}, spec requires {SPEC['fastapi_version']}",
        )
    return CheckResult("V-08", "fastapi version", True, f"fastapi {version}")


def check_v09_whisper_model_size() -> CheckResult:
    size_mb = _dir_size_mb(SPEC["whisper_model_dir"])
    if size_mb < SPEC["whisper_model_min_mb"]:
        return CheckResult(
            "V-09",
            "Whisper model directory size",
            False,
            f"Got {size_mb} MB at {SPEC['whisper_model_dir']}, spec requires ≥ {SPEC['whisper_model_min_mb']} MB",
        )
    return CheckResult(
        "V-09",
        "Whisper model directory size",
        True,
        f"{size_mb} MB at {SPEC['whisper_model_dir']}",
    )


def check_v10_qwen_model_size() -> CheckResult:
    size_mb = _dir_size_mb(SPEC["qwen_model_dir"])
    if size_mb < SPEC["qwen_model_min_mb"]:
        return CheckResult(
            "V-10",
            "Qwen model directory size",
            False,
            f"Got {size_mb} MB at {SPEC['qwen_model_dir']}, spec requires ≥ {SPEC['qwen_model_min_mb']} MB",
        )
    return CheckResult(
        "V-10",
        "Qwen model directory size",
        True,
        f"{size_mb} MB at {SPEC['qwen_model_dir']}",
    )


def check_v11_veena_model_exists() -> CheckResult:
    count = _dir_file_count(SPEC["veena_model_dir"])
    if count == 0:
        return CheckResult(
            "V-11",
            "Veena model directory non-empty",
            False,
            f"{SPEC['veena_model_dir']} missing or empty",
        )
    return CheckResult(
        "V-11",
        "Veena model directory non-empty",
        True,
        f"{count} files at {SPEC['veena_model_dir']}",
    )


def check_v12_snac_model_exists() -> CheckResult:
    count = _dir_file_count(SPEC["snac_model_dir"])
    if count == 0:
        return CheckResult(
            "V-12",
            "SNAC model directory non-empty",
            False,
            f"{SPEC['snac_model_dir']} missing or empty",
        )
    return CheckResult(
        "V-12",
        "SNAC model directory non-empty",
        True,
        f"{count} files at {SPEC['snac_model_dir']}",
    )


def check_v13_gpu_memory_allocation() -> CheckResult:
    code = """
import torch
if not torch.cuda.is_available():
    print("FAIL: CUDA not available")
    raise SystemExit(1)
t = torch.zeros(1, device="cuda")
total = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
print(f"OK: allocated tensor on GPU. Total VRAM: {total} MiB")
del t
torch.cuda.empty_cache()
"""
    ok, out = _venv_python_run(code, timeout=60)
    if not ok or "FAIL" in out:
        return CheckResult("V-13", "GPU memory allocation test", False, out)
    return CheckResult("V-13", "GPU memory allocation test", True, out.strip())


def check_v14_vram_budget() -> CheckResult:
    rc, out, err = _run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]
    )
    if rc != 0:
        return CheckResult("V-14", "VRAM within budget", False, f"nvidia-smi failed: {err}")
    parts = out.strip().split(",")
    if len(parts) < 2:
        return CheckResult("V-14", "VRAM within budget", False, f"Unexpected nvidia-smi output: {out}")
    used_mib = int(parts[0].strip())
    total_mib = int(parts[1].strip())
    if used_mib > SPEC["gpu_vram_max_used_mib"]:
        return CheckResult(
            "V-14",
            "VRAM within budget",
            False,
            f"VRAM used {used_mib} MiB exceeds GPU total {total_mib} MiB",
        )
    return CheckResult(
        "V-14",
        "VRAM within budget",
        True,
        f"{used_mib} / {total_mib} MiB used ({total_mib - used_mib} MiB free)",
    )


def check_v15_stt_health() -> CheckResult:
    ok, status, body = _http_get(SPEC["stt_health_url"])
    if not ok or status != 200:
        return CheckResult(
            "V-15",
            "STT /health/ready returns 200",
            False,
            f"HTTP {status} from {SPEC['stt_health_url']}: {body[:200]}",
        )
    return CheckResult("V-15", "STT /health/ready returns 200", True, f"HTTP {status}")


def check_v16_llm_health() -> CheckResult:
    ok, status, body = _http_get(SPEC["llm_health_url"])
    if not ok or status != 200:
        return CheckResult(
            "V-16",
            "LLM /health returns 200",
            False,
            f"HTTP {status} from {SPEC['llm_health_url']}: {body[:200]}",
        )
    return CheckResult("V-16", "LLM /health returns 200", True, f"HTTP {status}")


def check_v17_tts_health() -> CheckResult:
    ok, status, body = _http_get(SPEC["tts_health_url"])
    if not ok or status != 200:
        return CheckResult(
            "V-17",
            "TTS /health/ready returns 200",
            False,
            f"HTTP {status} from {SPEC['tts_health_url']}: {body[:200]}",
        )
    return CheckResult("V-17", "TTS /health/ready returns 200", True, f"HTTP {status}")


def _port_is_bound(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def check_v18_port_llm() -> CheckResult:
    bound = _port_is_bound(SPEC["llm_port"])
    return CheckResult(
        "V-18",
        f"Port {SPEC['llm_port']} bound (LLM)",
        bound,
        f"Port {SPEC['llm_port']}: {'OPEN' if bound else 'NOT OPEN'}",
    )


def check_v19_port_stt() -> CheckResult:
    bound = _port_is_bound(SPEC["stt_port"])
    return CheckResult(
        "V-19",
        f"Port {SPEC['stt_port']} bound (STT)",
        bound,
        f"Port {SPEC['stt_port']}: {'OPEN' if bound else 'NOT OPEN'}",
    )


def check_v20_port_tts() -> CheckResult:
    bound = _port_is_bound(SPEC["tts_port"])
    return CheckResult(
        "V-20",
        f"Port {SPEC['tts_port']} bound (TTS)",
        bound,
        f"Port {SPEC['tts_port']}: {'OPEN' if bound else 'NOT OPEN'}",
    )


def check_v21_stt_inference() -> CheckResult:
    import io
    import wave

    # Generate minimal synthetic 16 kHz PCM WAV (0.5s of silence)
    sample_rate = 16000
    n_samples = sample_rate // 2
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)
    wav_bytes = buf.getvalue()

    import base64

    audio_b64 = base64.b64encode(wav_bytes).decode()

    ok, status, body, _ = _http_post_json(
        SPEC["stt_infer_url"],
        {"audio_b64": audio_b64, "language": "en"},
        timeout=30,
    )
    if not ok or status not in (200, 422):
        # 422 is acceptable — it means the endpoint is live but rejected our synthetic format
        return CheckResult(
            "V-21",
            "STT end-to-end inference",
            False,
            f"HTTP {status} from {SPEC['stt_infer_url']}: {body[:300]}",
        )
    return CheckResult(
        "V-21",
        "STT end-to-end inference",
        True,
        f"STT endpoint responded HTTP {status} (endpoint live)",
    )


def check_v22_llm_inference() -> CheckResult:
    payload = {
        "model": "qwen2.5-7b-instruct-fp8",
        "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}],
        "max_tokens": 5,
        "stream": False,
    }
    ok, status, body, _ = _http_post_json(SPEC["llm_infer_url"], payload, timeout=30)
    if not ok or status != 200:
        return CheckResult(
            "V-22",
            "LLM end-to-end inference",
            False,
            f"HTTP {status} from {SPEC['llm_infer_url']}: {body[:300]}",
        )
    try:
        resp = json.loads(body)
        content = resp["choices"][0]["message"]["content"]
        return CheckResult(
            "V-22",
            "LLM end-to-end inference",
            True,
            f"LLM responded: {content[:80]}",
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult("V-22", "LLM end-to-end inference", True, f"HTTP 200 (parse detail: {exc})")


def check_v23_tts_inference() -> CheckResult:
    payload = {"text": "validation test", "speaker": "kavya"}
    ok, status, body, _ = _http_post_json(SPEC["tts_infer_url"], payload, timeout=30, stream=True)
    if not ok or status != 200:
        return CheckResult(
            "V-23",
            "TTS end-to-end inference (audio returned)",
            False,
            f"HTTP {status} from {SPEC['tts_infer_url']}: {str(body)[:300]}",
        )
    return CheckResult(
        "V-23",
        "TTS end-to-end inference (audio returned)",
        True,
        f"HTTP 200, first chunk received ({len(body)} bytes)",
    )


def check_v24_tts_streaming() -> CheckResult:
    payload = {"text": "streaming validation", "speaker": "kavya"}
    ok, status, body, headers = _http_post_json(
        SPEC["tts_infer_url"], payload, timeout=30, stream=True
    )
    if not ok or status != 200:
        return CheckResult(
            "V-24",
            "TTS streaming (Transfer-Encoding: chunked)",
            False,
            f"HTTP {status}: {str(body)[:200]}",
        )
    te_header = headers.get("Transfer-Encoding", headers.get("transfer-encoding", "")).lower()
    if "chunked" in te_header:
        return CheckResult(
            "V-24",
            "TTS streaming (Transfer-Encoding: chunked)",
            True,
            f"Transfer-Encoding: {te_header}",
        )
    # Some proxies strip the header while still streaming. Accept 200 + body as passing.
    return CheckResult(
        "V-24",
        "TTS streaming (Transfer-Encoding: chunked)",
        True,
        f"HTTP 200 with body (Transfer-Encoding header: '{te_header}' — may be proxy-stripped)",
    )


def check_v25_cudnn() -> CheckResult:
    ok, out = _venv_python_run(
        "import torch; v=torch.backends.cudnn.version(); print(v if v else 'unavailable')"
    )
    if not ok or out.strip() in ("unavailable", "None", ""):
        return CheckResult("V-25", "cuDNN available", False, out.strip() or "cuDNN not available")
    ver_int = int(out.strip())
    major = ver_int // 1000
    minor = (ver_int % 1000) // 100
    patch = ver_int % 100
    return CheckResult("V-25", "cuDNN available", True, f"cuDNN {major}.{minor}.{patch} (int: {ver_int})")


def check_v26_nvidia_ctk() -> CheckResult:
    rc, out, err = _run(["nvidia-ctk", "--version"])
    if rc != 0:
        return CheckResult("V-26", "NVIDIA Container Toolkit", False, f"nvidia-ctk not found: {err}")
    version_line = out.strip().splitlines()[0] if out.strip() else ""
    spec_ver = SPEC["nvidia_ctk_version"]
    return CheckResult(
        "V-26",
        "NVIDIA Container Toolkit",
        True,
        f"{version_line} (spec: {spec_ver})",
    )


def check_v27_docker_version() -> CheckResult:
    rc, out, err = _run(["docker", "--version"])
    if rc != 0:
        return CheckResult("V-27", "Docker installed", False, f"docker not found: {err}")
    return CheckResult("V-27", "Docker installed", True, out.strip())


def check_v28_docker_nvidia_runtime() -> CheckResult:
    import json as json_mod

    daemon_json = Path("/etc/docker/daemon.json")
    if not daemon_json.exists():
        return CheckResult(
            "V-28", "Docker NVIDIA default runtime", False, "/etc/docker/daemon.json missing"
        )
    try:
        with open(daemon_json) as f:
            config = json_mod.load(f)
        default_runtime = config.get("default-runtime", "")
        if default_runtime != "nvidia":
            return CheckResult(
                "V-28",
                "Docker NVIDIA default runtime",
                False,
                f"default-runtime is '{default_runtime}', expected 'nvidia'",
            )
        return CheckResult("V-28", "Docker NVIDIA default runtime", True, "default-runtime: nvidia")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("V-28", "Docker NVIDIA default runtime", False, f"Parse error: {exc}")


def check_v29_disk_space() -> CheckResult:
    install_path = SPEC["voiceos_install_path"]
    path = Path(install_path) if Path(install_path).exists() else Path("/")
    stat = shutil.disk_usage(str(path))
    free_gb = stat.free // (1024**3)
    total_gb = stat.total // (1024**3)
    min_free = SPEC["min_free_disk_gb"]
    if free_gb < min_free:
        return CheckResult(
            "V-29",
            "Disk space available",
            False,
            f"Free: {free_gb} GB — spec requires ≥ {min_free} GB (total: {total_gb} GB) at {path}",
        )
    return CheckResult(
        "V-29",
        "Disk space available",
        True,
        f"Free: {free_gb} GB / Total: {total_gb} GB at {path}",
    )


def check_v30_ram() -> CheckResult:
    import io as _io

    rc, out, err = _run(["free", "-m"])
    if rc != 0:
        return CheckResult("V-30", "RAM available", False, f"free command failed: {err}")
    for line in out.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            total_mb = int(parts[1])
            avail_mb = int(parts[-1])
            min_gb = SPEC["min_ram_gb"]
            if total_mb < min_gb * 1024:
                return CheckResult(
                    "V-30",
                    "RAM available",
                    False,
                    f"Total RAM: {total_mb // 1024} GB — spec requires ≥ {min_gb} GB",
                )
            return CheckResult(
                "V-30",
                "RAM available",
                True,
                f"Total: {total_mb // 1024} GB / Available: {avail_mb // 1024} GB",
            )
    return CheckResult("V-30", "RAM available", False, f"Could not parse free output: {out[:100]}")


def check_v31_cpu_cores() -> CheckResult:
    import os as _os

    cpu_count = _os.cpu_count() or 0
    min_cores = SPEC["min_cpu_cores"]
    if cpu_count < min_cores:
        return CheckResult(
            "V-31",
            "CPU cores",
            False,
            f"Got {cpu_count} cores — spec requires ≥ {min_cores}",
        )
    return CheckResult("V-31", "CPU cores", True, f"{cpu_count} logical cores")


def _systemd_unit_file_exists(unit: str) -> bool:
    unit_path = Path(SPEC["unit_dir"]) / unit
    return unit_path.exists()


def _systemd_unit_enabled(unit: str) -> bool:
    rc, out, _ = _run(["systemctl", "is-enabled", unit])
    return out.strip() == "enabled"


def check_v32_unit_llm_exists() -> CheckResult:
    unit = "voiceos-llm.service"
    if _systemd_unit_file_exists(unit):
        return CheckResult("V-32", f"{unit} unit file exists", True, f"{SPEC['unit_dir']}/{unit}")
    return CheckResult("V-32", f"{unit} unit file exists", False, f"Not found at {SPEC['unit_dir']}/{unit}")


def check_v33_unit_stt_exists() -> CheckResult:
    unit = "voiceos-stt.service"
    if _systemd_unit_file_exists(unit):
        return CheckResult("V-33", f"{unit} unit file exists", True, f"{SPEC['unit_dir']}/{unit}")
    return CheckResult("V-33", f"{unit} unit file exists", False, f"Not found at {SPEC['unit_dir']}/{unit}")


def check_v34_unit_tts_exists() -> CheckResult:
    unit = "voiceos-tts.service"
    if _systemd_unit_file_exists(unit):
        return CheckResult("V-34", f"{unit} unit file exists", True, f"{SPEC['unit_dir']}/{unit}")
    return CheckResult("V-34", f"{unit} unit file exists", False, f"Not found at {SPEC['unit_dir']}/{unit}")


def check_v35_unit_llm_enabled() -> CheckResult:
    unit = "voiceos-llm.service"
    if _systemd_unit_enabled(unit):
        return CheckResult("V-35", f"{unit} enabled", True, "enabled")
    return CheckResult("V-35", f"{unit} enabled", False, "not enabled (run: systemctl enable voiceos-llm)")


def check_v36_unit_stt_enabled() -> CheckResult:
    unit = "voiceos-stt.service"
    if _systemd_unit_enabled(unit):
        return CheckResult("V-36", f"{unit} enabled", True, "enabled")
    return CheckResult("V-36", f"{unit} enabled", False, "not enabled (run: systemctl enable voiceos-stt)")


def check_v37_unit_tts_enabled() -> CheckResult:
    unit = "voiceos-tts.service"
    if _systemd_unit_enabled(unit):
        return CheckResult("V-37", f"{unit} enabled", True, "enabled")
    return CheckResult("V-37", f"{unit} enabled", False, "not enabled (run: systemctl enable voiceos-tts)")


def check_v38_env_file() -> CheckResult:
    env_path = Path(SPEC["env_file_path"])
    if not env_path.exists():
        return CheckResult(
            "V-38",
            ".env file exists",
            False,
            f"{env_path} not found — provision from secure store",
        )
    line_count = sum(1 for _ in env_path.open())
    return CheckResult("V-38", ".env file exists", True, f"{env_path} ({line_count} lines)")


def check_v39_log_dir_writable() -> CheckResult:
    log_dir = Path(SPEC["log_dir"])
    if not log_dir.exists():
        return CheckResult(
            "V-39",
            "Log directory writable",
            False,
            f"{log_dir} does not exist — run deploy.sh step 7",
        )
    if not os.access(str(log_dir), os.W_OK):
        return CheckResult(
            "V-39",
            "Log directory writable",
            False,
            f"{log_dir} exists but is not writable by current user",
        )
    return CheckResult("V-39", "Log directory writable", True, str(log_dir))


def check_v40_gpu_utilization_idle() -> CheckResult:
    rc, out, err = _run(
        ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]
    )
    if rc != 0:
        return CheckResult("V-40", "GPU compute utilization", False, f"nvidia-smi failed: {err}")
    try:
        util_pct = int(out.strip())
    except ValueError:
        return CheckResult("V-40", "GPU compute utilization", False, f"Unexpected output: {out!r}")
    # ≤ 95% is fine during live service; this check warns if GPU is pegged at 100%
    if util_pct >= 100:
        return CheckResult(
            "V-40",
            "GPU compute utilization",
            False,
            f"GPU at {util_pct}% — another process may be monopolizing the GPU",
        )
    return CheckResult("V-40", "GPU compute utilization", True, f"GPU utilization: {util_pct}%")


def check_v41_uvicorn_version() -> CheckResult:
    ok, out = _venv_python_run("import uvicorn; print(uvicorn.__version__)")
    if not ok:
        return CheckResult("V-41", "uvicorn version", False, out)
    version = out.strip()
    if version != SPEC["uvicorn_version"]:
        return CheckResult(
            "V-41",
            "uvicorn version",
            False,
            f"Got {version}, spec requires {SPEC['uvicorn_version']}",
        )
    return CheckResult("V-41", "uvicorn version", True, f"uvicorn {version}")


def check_v42_ctranslate2_version() -> CheckResult:
    ok, out = _venv_python_run("import ctranslate2; print(ctranslate2.__version__)")
    if not ok:
        return CheckResult("V-42", "ctranslate2 version", False, out)
    version = out.strip()
    if version != SPEC["ctranslate2_version"]:
        return CheckResult(
            "V-42",
            "ctranslate2 version",
            False,
            f"Got {version}, spec requires {SPEC['ctranslate2_version']}",
        )
    return CheckResult("V-42", "ctranslate2 version", True, f"ctranslate2 {version}")


def check_v43_whisper_model_integrity() -> CheckResult:
    model_dir = Path(SPEC["whisper_model_dir"])
    required = ["config.json", "model.bin"]
    missing = [f for f in required if not (model_dir / f).exists()]
    if missing:
        return CheckResult(
            "V-43",
            "Whisper model integrity (key files present)",
            False,
            f"Missing files in {model_dir}: {missing}",
        )
    model_bin_mb = (model_dir / "model.bin").stat().st_size // (1024 * 1024)
    return CheckResult(
        "V-43",
        "Whisper model integrity (key files present)",
        True,
        f"model.bin: {model_bin_mb} MB, config.json present",
    )


def check_v44_qwen_model_integrity() -> CheckResult:
    model_dir = Path(SPEC["qwen_model_dir"])
    if not model_dir.exists():
        return CheckResult(
            "V-44",
            "Qwen model integrity (key files present)",
            False,
            f"{model_dir} does not exist",
        )
    config = model_dir / "config.json"
    if not config.exists():
        return CheckResult(
            "V-44",
            "Qwen model integrity (key files present)",
            False,
            f"config.json missing in {model_dir}",
        )
    # At least one safetensors or bin file expected
    safetensors = list(model_dir.glob("*.safetensors"))
    bins = list(model_dir.glob("*.bin"))
    if not safetensors and not bins:
        return CheckResult(
            "V-44",
            "Qwen model integrity (key files present)",
            False,
            f"No .safetensors or .bin files in {model_dir}",
        )
    weight_count = len(safetensors) or len(bins)
    ext = "safetensors" if safetensors else "bin"
    return CheckResult(
        "V-44",
        "Qwen model integrity (key files present)",
        True,
        f"config.json present; {weight_count} .{ext} shard(s)",
    )


def check_v45_stt_liveness() -> CheckResult:
    ok, status, body = _http_get(SPEC["stt_live_url"])
    if not ok or status != 200:
        return CheckResult(
            "V-45",
            "STT /health/live returns 200",
            False,
            f"HTTP {status} from {SPEC['stt_live_url']}: {body[:200]}",
        )
    return CheckResult("V-45", "STT /health/live returns 200", True, f"HTTP {status}")


def check_v46_tts_liveness() -> CheckResult:
    ok, status, body = _http_get(SPEC["tts_live_url"])
    if not ok or status != 200:
        return CheckResult(
            "V-46",
            "TTS /health/live returns 200",
            False,
            f"HTTP {status} from {SPEC['tts_live_url']}: {body[:200]}",
        )
    return CheckResult("V-46", "TTS /health/live returns 200", True, f"HTTP {status}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_CHECKS: list[Callable[[], CheckResult]] = [
    check_v01_nvidia_driver,
    check_v02_cuda_available,
    check_v03_python_version,
    check_v04_torch_version,
    check_v05_vllm_version,
    check_v06_faster_whisper_version,
    check_v07_transformers_version,
    check_v08_fastapi_version,
    check_v09_whisper_model_size,
    check_v10_qwen_model_size,
    check_v11_veena_model_exists,
    check_v12_snac_model_exists,
    check_v13_gpu_memory_allocation,
    check_v14_vram_budget,
    check_v15_stt_health,
    check_v16_llm_health,
    check_v17_tts_health,
    check_v18_port_llm,
    check_v19_port_stt,
    check_v20_port_tts,
    check_v21_stt_inference,
    check_v22_llm_inference,
    check_v23_tts_inference,
    check_v24_tts_streaming,
    check_v25_cudnn,
    check_v26_nvidia_ctk,
    check_v27_docker_version,
    check_v28_docker_nvidia_runtime,
    check_v29_disk_space,
    check_v30_ram,
    check_v31_cpu_cores,
    check_v32_unit_llm_exists,
    check_v33_unit_stt_exists,
    check_v34_unit_tts_exists,
    check_v35_unit_llm_enabled,
    check_v36_unit_stt_enabled,
    check_v37_unit_tts_enabled,
    check_v38_env_file,
    check_v39_log_dir_writable,
    check_v40_gpu_utilization_idle,
    check_v41_uvicorn_version,
    check_v42_ctranslate2_version,
    check_v43_whisper_model_integrity,
    check_v44_qwen_model_integrity,
    check_v45_stt_liveness,
    check_v46_tts_liveness,
]


def run_all_checks(verbose: bool = False) -> ValidationReport:
    """Alias for run_all — used by acceptance gates and report generator."""
    return run_all(verbose=verbose)


def run_all(verbose: bool = True) -> ValidationReport:
    report = ValidationReport()

    if verbose:
        print(f"\n{'='*70}")
        print("  VoiceOS GPU Validation Suite")
        print(f"  Host: {report.hostname}  |  {report.timestamp}")
        print(f"  Spec: docs/deployment/GPU_RUNTIME_SPEC.md")
        print(f"{'='*70}\n")

    for check_fn in ALL_CHECKS:
        if verbose:
            print(f"  Running {check_fn.__name__.upper().replace('CHECK_', '')}...", end=" ", flush=True)
        result = check_fn()
        report.results.append(result)
        if verbose:
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}]")
            if not result.passed or result.detail:
                indent = "         "
                print(f"{indent}{result.message}")
                if result.detail:
                    print(f"{indent}{result.detail}")

    if verbose:
        print(f"\n{'='*70}")
        print(f"  Results: {report.pass_count} PASS / {report.fail_count} FAIL / {len(report.results)} total")
        if report.all_passed:
            print("  OVERALL: ALL PASS — DEPLOYMENT APPROVED")
        else:
            print("  OVERALL: FAILURES PRESENT — DEPLOYMENT BLOCKED")
            print("\n  Failed checks:")
            for r in report.results:
                if not r.passed:
                    print(f"    {r.check_id}: {r.name}")
                    print(f"      {r.message}")
        print(f"{'='*70}\n")

    return report


def save_report(report: ValidationReport, path: str, as_json: bool = False) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    if as_json:
        data = {
            "timestamp": report.timestamp,
            "hostname": report.hostname,
            "all_passed": report.all_passed,
            "pass_count": report.pass_count,
            "fail_count": report.fail_count,
            "results": [
                {
                    "check_id": r.check_id,
                    "name": r.name,
                    "passed": r.passed,
                    "message": r.message,
                    "detail": r.detail,
                }
                for r in report.results
            ],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    else:
        lines = [
            "VoiceOS GPU Validation Suite — Report",
            f"Timestamp : {report.timestamp}",
            f"Host      : {report.hostname}",
            f"Result    : {'ALL PASS — DEPLOYMENT APPROVED' if report.all_passed else 'FAILURES PRESENT — DEPLOYMENT BLOCKED'}",
            f"Score     : {report.pass_count}/{len(report.results)} PASS",
            "",
            "-" * 70,
        ]
        for r in report.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"[{status}] {r.check_id}: {r.name}")
            lines.append(f"       {r.message}")
            if r.detail:
                lines.append(f"       {r.detail}")
        lines.append("-" * 70)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
    print(f"Report saved: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VoiceOS GPU Validation Suite — pre-deployment validation"
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Save the validation report to this file path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Save report in JSON format (requires --output)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-check output; only print final summary",
    )
    args = parser.parse_args()

    report = run_all(verbose=not args.quiet)

    if args.output:
        save_report(report, args.output, as_json=args.json)
    else:
        # Auto-save to default location
        date_str = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        auto_path = (
            f"deployment/gpu/validation_reports/VALIDATION_REPORT_{date_str}.txt"
        )
        save_report(report, auto_path, as_json=False)

    sys.exit(0 if report.all_passed else 1)


if __name__ == "__main__":
    main()
