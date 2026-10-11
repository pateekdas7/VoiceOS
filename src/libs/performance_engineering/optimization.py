"""OptimizationPlaybook -- documented procedures for each stage bottleneck (V3 Ch19, Sprint-028).

A static registry consulted whenever :class:`~.regression_gate.RegressionDetector`
or a latency-validation run (``evaluation/latency-validation/``) identifies
a stage exceeding its budget. Each procedure is grounded in this project's
own real, previously-filed technical debt (``implementation/BACKLOG.md``)
rather than generic advice, so the on-call engineer following it lands on
a concrete, already-diagnosed starting point.

Architecture: V3 Ch19 (Performance Engineering -- systematic optimization methodology).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class OptimizationProcedure:
    """A documented, stage-specific bottleneck-investigation procedure."""

    stage_name: str
    likely_causes: tuple[str, ...]
    procedure: tuple[str, ...]
    """Ordered investigation/remediation steps."""
    related_technical_debt: tuple[str, ...] = ()
    """BACKLOG.md TT-IDs already tracking a known instance of this stage's regression class."""


class OptimizationPlaybook:
    """Registry of per-stage optimization procedures (V3 Ch19)."""

    _PROCEDURES: ClassVar[dict[str, OptimizationProcedure]] = {
        "media_gw": OptimizationProcedure(
            stage_name="media_gw",
            likely_causes=(
                "admission_rejections spike (Sprint-004 Prometheus counter) under session-limit pressure",
                "RTP jitter buffer misconfigured for the current network path",
            ),
            procedure=(
                "1. Check the media-gateway Grafana dashboard's active_sessions/admission_rejections panels "
                "(monitoring/grafana/dashboards/slo-overview.json).",
                "2. Confirm AdaptiveJitterBuffer sizing against current measured RTP jitter, not a stale default.",
                "3. Rule out a NetworkPolicy/ingress bottleneck introduced by Sprint-026's Helm chart "
                "(TT-016 -- egress rules only cover datastore ports today).",
            ),
        ),
        "asm": OptimizationProcedure(
            stage_name="asm",
            likely_causes=("PacketLossConcealer invoked excessively (real loss, not transient)", "SessionClock drift"),
            procedure=(
                "1. Correlate with the RTP packet-loss chaos scenario's own baseline "
                "(evaluation/chaos/chaos-engineering-report.md scenario 3) to separate real network loss from a code regression.",
                "2. Profile AdaptiveJitterBuffer.pull() directly with ContinuousProfiler.profile_stage() "
                "before assuming a downstream stage is the true cause.",
            ),
        ),
        "preprocessing": OptimizationProcedure(
            stage_name="preprocessing",
            likely_causes=(
                "NLMS filter convergence slow on this call's noise profile",
                "resample step exceeding its 30ms V1 Ch23 budget",
            ),
            procedure=(
                "1. Check ResamplerStage timings in isolation (scipy.signal.resample_poly cost scales with "
                "input/output rate ratio -- confirm the configured target rate hasn't drifted).",
                "2. If AGC/NLMS dominates, consider a shorter adaptation window before assuming GPU contention "
                "elsewhere is masquerading as a preprocessing regression.",
            ),
        ),
        "vad": OptimizationProcedure(
            stage_name="vad",
            likely_causes=("Silero VAD v4 ONNX session not reused across calls (cold session init cost)",),
            procedure=(
                "1. Confirm the onnxruntime InferenceSession is created once per process, not per call.",
                "2. Check endpointing hangover/threshold tuning isn't causing extra inference passes per frame.",
            ),
        ),
        "stt": OptimizationProcedure(
            stage_name="stt",
            likely_causes=(
                "Whisper model not warm (no ModelWarmupOrchestrator warm-up before admission)",
                "GPU VRAM pressure from a co-scheduled LLM/TTS request on the same node",
                "int8_float16 quantization silently reverted to a slower precision",
            ),
            procedure=(
                "1. Check monitoring/gpu_fleet's ModelWarmupOrchestrator/FleetVRAMBudget for a cold-start or "
                "VRAM-pressure event coinciding with the regression window.",
                "2. Verify WhisperAdapter's quantization setting against GPU_NODE_STATE.md Section 8 "
                "(int8_float16, 1,242MB actual footprint) -- a silent fallback to fp32 would both inflate "
                "VRAM usage and slow inference.",
                "3. Re-run deployment/gpu/validate_latency.py --test stt directly against the GPU node to "
                "isolate STT from everything upstream/downstream of it.",
            ),
            related_technical_debt=("TT-010",),
        ),
        "cil": OptimizationProcedure(
            stage_name="cil",
            likely_causes=(
                "CustomerContextAssembler re-assembling CustomerContext more than once per call (RI-5 violation)",
                "PolicyEngine cache miss (Redis tier down, falling through to the Postgres tier every call)",
            ),
            procedure=(
                "1. Confirm CustomerContextAssembler is invoked at most once per call (Sprint-022 AC) -- a "
                "regression here often means a caller stopped reusing the cached context.",
                "2. Check PolicyEngine's Redis-cache hit rate; a sustained cache-miss storm falls through to "
                "the Postgres PolicyRepository tier on every evaluate() call.",
            ),
        ),
        "llm_ttft": OptimizationProcedure(
            stage_name="llm_ttft",
            likely_causes=(
                "vLLM continuous-batching queue depth increased under load",
                "PromptContract (RI-7) assembling an unusually long prompt for this call",
                "GPU KV-cache pressure from Veena TTS running concurrently (gpu-memory-utilization tuning)",
            ),
            procedure=(
                "1. Check vLLM's own queue-depth/throughput metrics for saturation at the measured concurrency.",
                "2. Confirm PromptContract isn't accumulating unbounded working-memory context across turns.",
                "3. Re-verify the Sprint-009 Phase 2 gpu-memory-utilization split (Whisper/Qwen/Veena, "
                "GPU_NODE_STATE.md Section 3 VRAM Budget) hasn't drifted under a co-scheduled load pattern.",
            ),
            related_technical_debt=("TT-001-residual",),
        ),
        "tts_first_clause": OptimizationProcedure(
            stage_name="tts_first_clause",
            likely_causes=(
                "VeenaAdapter's clause-by-clause synthesis running sequentially with LLM generation rather than concurrently",
                "SNAC decode sliding-window size not tuned for current load",
            ),
            procedure=(
                "1. Read ADR-001 (vLLM TTS streaming) and TT-001-residual first -- this is this project's "
                "single most-documented latency bottleneck class.",
                "2. Confirm _SNACTokenStreamer's 28-token sliding window is still applied (not reverted to "
                "buffered generation).",
                "3. If TT-001-residual's sequential-await pattern (_yield_clause() blocking the LLM-text "
                "consumer loop) is the cause, prioritize the asyncio.create_task() concurrency fix over any "
                "model-level change.",
            ),
            related_technical_debt=("TT-001", "TT-001-residual", "TT-010"),
        ),
    }

    def procedure_for(self, stage_name: str) -> OptimizationProcedure | None:
        return self._PROCEDURES.get(stage_name)

    def all_procedures(self) -> tuple[OptimizationProcedure, ...]:
        return tuple(self._PROCEDURES.values())


__all__ = ["OptimizationPlaybook", "OptimizationProcedure"]
