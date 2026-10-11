# Kaggle T4×2 kernel sources

Snapshot of the Kaggle notebook source trees used for GPU-side forensics
and validation. Kaggle kernels are pushed with

```
kaggle kernels push -p . --accelerator NvidiaTeslaT4
```

which is the only reliable way to guarantee a T4×2 accelerator — the
REST API's `workerSize` field always allocates P100 (see the
`Kaggle T4×2 CLI push method` memory).

Live logs while a kernel runs:

```
kaggle kernels logs -f <owner>/<slug>
```

`output` only works after the kernel completes.

## Kernels

Each subdirectory is a self-contained Kaggle project (`kernel-metadata.json`
+ `source.py` and/or `.ipynb` + `build_ipynb.py` script that materialises
the notebook from `source.py`).

| Directory | Slug | Purpose |
|---|---|---|
| `maitri_forensic/` | `voiceos-maitri-voice-forensic` | Initial Maitri voice-drift forensic (baseline) |
| `maitri_voice_switch/` | `voiceos-maitri-voice-switch-forensic` | Voice-switch forensic — reproduces the drift at kernel-boot vs mid-generation |
| `maitri_v4_fix/` | `voiceos-maitri-voice-switch-fix-v4` | V4 fix attempt (kavya anchoring) |
| `maitri_v5_fix/` | `voiceos-maitri-voice-switch-fix-v5` | V5 fix (revised anchoring after V4 partial success) |
| `maitri_v6_causal/` | `voiceos-maitri-causal-analysis-v6` | Causal analysis of V5 residual drift |
| `x4a_kaggle/` | `voiceos-x4a-token-forensic` | X4A token forensic (see `X4A_FORENSIC_REPORT.md`) |
| `x4b_kaggle/` | `voiceos-x4b-cross-precision` | X4B cross-precision (BF16 vs FP16 vs FP32) |
| `sprint29_restart/` | `voiceos-sprint29-validation` | Sprint-29 validation restart notebook |
| `mamata_kernel/` | (mamatadas7777 account) | Sprint-29 validation on the Mamata Kaggle account |
| `my-notebook/` | scratch | Ad-hoc validation scratch |

## What was intentionally excluded

- Model checkpoints under `**/hf/**` (redownloadable from HuggingFace)
- Audio outputs (`*.wav`, `*.pcm`) — regenerable by rerunning the kernel
- Results JSONs > 500 kB — full raw numbers land in the kernel's `output`,
  the small summary JSONs are kept
- `.tar.gz` archives — the pre-tar sources are here already
- Third-party wheels — `pip install` from source
