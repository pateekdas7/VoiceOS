"""Runtime invariant guards — RI-1 through RI-8.

Each guard is a callable Python function that checks one runtime invariant.
When the invariant is violated, the guard raises InvariantViolationError with
a stable ``invariant_id``, a human-readable message, and structured context.

These guards are the mechanical encoding of V1 Appendix E into enforceable
Python assertions. They are called at boundary points throughout the system:
  - RI-1: called before any blocking I/O is attempted on the media path.
  - RI-2: called before any state mutation that requires a distributed lock.
  - RI-3: called before enqueue operations on bounded buffers/queues.
  - RI-4: called before any externally-visible action (calls, SMS, PTP writes).
  - RI-5: called in the OutputValidator before LLM output reaches TTS.
  - RI-6: called by the OutputValidator to verify plan coherence.
  - RI-7: called by the PromptBuilder after assembling a prompt.
  - RI-8: called by the GPU Scheduler before allocating VRAM.

Architecture: V1 Appendix E; V6 Ch4 AR-9 through AR-14.
"""

from __future__ import annotations

from typing import Any

from .errors import InvariantViolationError


def assert_ri1_realtime_purity(is_blocking_call: bool, duration_ms: float) -> None:
    """RI-1: No blocking I/O on the real-time audio path.

    Blocking calls must never execute on the media thread. If a blocking call
    is detected and its duration exceeds 1 ms, this invariant is violated.
    The guard accepts both conditions so callers can report the combination
    atomically — a blocking call that finishes in <1 ms is still a violation
    waiting to happen and should be refactored.

    Args:
        is_blocking_call: True if the operation is a blocking (synchronous) I/O call.
        duration_ms: Duration of the operation in milliseconds.

    Raises:
        InvariantViolationError: When is_blocking_call is True and duration_ms > 1.0.

    Architecture: V1 Appendix E RI-1; V6 AR-9 (MUST NOT block real-time path).
    """
    if is_blocking_call and duration_ms > 1.0:
        raise InvariantViolationError(
            invariant_id="RI-1",
            message=(f"Blocking I/O on the real-time audio path: duration {duration_ms:.3f}ms exceeds 1ms limit"),
            context={
                "is_blocking_call": is_blocking_call,
                "duration_ms": duration_ms,
                "limit_ms": 1.0,
            },
        )


def assert_ri2_single_writer(resource_id: str, writer_id: str, lock_token: str) -> None:
    """RI-2: Single-writer state.

    Any state modification must hold the distributed lock for that resource.
    A missing or empty lock token indicates the write is unprotected, which
    risks concurrent mutations and race conditions in the call-state machine.

    Args:
        resource_id: The resource being mutated (e.g., 'call:abc:state').
        writer_id: Identifier of the writer attempting the mutation.
        lock_token: The distributed-lock fencing token held by the writer.
                    Must be non-empty.

    Raises:
        InvariantViolationError: When lock_token is empty or missing.

    Architecture: V1 Appendix E RI-2; V6 AR-10; V3 Ch4 (Redis lock).
    """
    if not lock_token:
        raise InvariantViolationError(
            invariant_id="RI-2",
            message=(
                f"State mutation attempted without a distributed lock: resource='{resource_id}', writer='{writer_id}'"
            ),
            context={
                "resource_id": resource_id,
                "writer_id": writer_id,
                "lock_token": lock_token or "<empty>",
            },
        )


def assert_ri3_bounded_buffer(queue_depth: int, max_depth: int, queue_name: str) -> None:
    """RI-3: All buffers and queues must have a declared maximum depth.

    Attempting to enqueue beyond max_depth is a violation. Unbounded queues
    hide backpressure and lead to OOM under load. This guard is called before
    each enqueue operation.

    Args:
        queue_depth: Current number of items in the queue.
        max_depth: Declared maximum capacity of the queue.
        queue_name: Human-readable name for the queue (for logging/alerting).

    Raises:
        InvariantViolationError: When queue_depth >= max_depth.

    Architecture: V1 Appendix E RI-3; V6 AR-11; V3 Ch10 (bounded queues).
    """
    if queue_depth >= max_depth:
        raise InvariantViolationError(
            invariant_id="RI-3",
            message=(f"Queue '{queue_name}' is at capacity: depth={queue_depth} >= max_depth={max_depth}"),
            context={
                "queue_name": queue_name,
                "queue_depth": queue_depth,
                "max_depth": max_depth,
            },
        )


def assert_ri4_commit_before_act(event_committed: bool, action_name: str) -> None:
    """RI-4: Commit-before-act.

    The state-change event must be durably committed (persisted to the event
    log / Postgres) before any externally-visible effect (call, SMS, PTP write)
    is performed. This guards against duplicate or lost effects under failure.

    Args:
        event_committed: True if the domain event has been durably committed.
        action_name: Description of the external action about to be performed.

    Raises:
        InvariantViolationError: When event_committed is False.

    Architecture: V1 Appendix E RI-4; V6 AR-12; V3 Ch8 (idempotency).
    """
    if not event_committed:
        raise InvariantViolationError(
            invariant_id="RI-4",
            message=(f"External action '{action_name}' attempted before its state-change event was durably committed"),
            context={
                "event_committed": event_committed,
                "action_name": action_name,
            },
        )


def assert_ri5_law_of_authority(
    fact_key: str,
    value: Any,
    authorized_sources: set[str],
    source: str,
) -> None:
    """RI-5: Law of Authority — every fact presented to the customer must originate
    from an authoritative source. LLM-generated facts are never authoritative.

    This guard is called by the OutputValidator (V1 Ch14) for every fact in
    the LLM output that touches a customer-visible value (amounts, dates,
    account references).

    Args:
        fact_key: The fact being verified (e.g., 'outstanding_balance').
        value: The value being asserted as fact (for logging context).
        authorized_sources: The set of source identifiers that are authoritative
                            for this fact (e.g., {'crm', 'collections_system'}).
        source: The actual source of the value being presented.

    Raises:
        InvariantViolationError: When source is not in authorized_sources.

    Architecture: V1 Appendix E RI-5; Law of Authority; V6 AR-3.
    """
    if source not in authorized_sources:
        raise InvariantViolationError(
            invariant_id="RI-5",
            message=(
                f"Fact '{fact_key}' presented from unauthorized source '{source}'. "
                f"Authorized sources: {sorted(authorized_sources)}"
            ),
            context={
                "fact_key": fact_key,
                "source": source,
                "authorized_sources": ",".join(sorted(authorized_sources)),
            },
        )


def assert_ri6_output_coherence(response_plan_id: str, llm_output_plan_id: str) -> None:
    """RI-6: Output coherence.

    The OutputValidator must verify that LLM output is coherent with the
    current ResponsePlan. Mismatched plan IDs indicate the LLM was given a
    stale or incorrect plan — the output must be rejected.

    Args:
        response_plan_id: The plan_id of the current sealed ResponsePlan.
        llm_output_plan_id: The plan_id embedded in the LLM output/validator.

    Raises:
        InvariantViolationError: When the plan IDs do not match.

    Architecture: V1 Appendix E RI-6; V1 Ch14 (Output Validator); V6 AR-6.
    """
    if response_plan_id != llm_output_plan_id:
        raise InvariantViolationError(
            invariant_id="RI-6",
            message=(
                f"LLM output plan mismatch: current plan='{response_plan_id}', output plan='{llm_output_plan_id}'"
            ),
            context={
                "response_plan_id": response_plan_id,
                "llm_output_plan_id": llm_output_plan_id,
            },
        )


def assert_ri7_deterministic_prompt(prompt_hash: str, prompt_version: str, expected_hash: str) -> None:
    """RI-7: Prompts must be deterministic.

    Same inputs (sealed ResponsePlan + CustomerContext + prompt version) must
    produce the same prompt every time. Hash mismatch indicates non-deterministic
    prompt construction — a violation that breaks replay and audit.

    Args:
        prompt_hash: SHA-256 hex digest of the assembled prompt.
        prompt_version: Prompt template version identifier (e.g., 'v3.1.2').
        expected_hash: The expected hash for this plan+version combination.

    Raises:
        InvariantViolationError: When prompt_hash != expected_hash.

    Architecture: V1 Appendix E RI-7; V1 Ch12 (Prompt Builder); V6 AR-13.
    """
    if prompt_hash != expected_hash:
        raise InvariantViolationError(
            invariant_id="RI-7",
            message=(
                f"Non-deterministic prompt detected for version '{prompt_version}': "
                f"got hash '{prompt_hash}', expected '{expected_hash}'"
            ),
            context={
                "prompt_hash": prompt_hash,
                "prompt_version": prompt_version,
                "expected_hash": expected_hash,
            },
        )


def assert_ri8_oom_by_construction(
    requested_vram_mb: int,
    available_vram_mb: int,
    service_name: str,
) -> None:
    """RI-8: GPU memory allocation must never exceed tracked available VRAM.

    The GPU Scheduler must reject inference requests when they would exceed
    the tracked available VRAM, rather than allowing allocation to proceed
    and risk an OOM crash. The system prefers clean admission failure over
    undefined OOM behaviour.

    Args:
        requested_vram_mb: VRAM required for this inference request (MB).
        available_vram_mb: VRAM currently available on the target GPU (MB).
        service_name: Name of the service requesting the allocation (for logs).

    Raises:
        InvariantViolationError: When requested_vram_mb > available_vram_mb.

    Architecture: V1 Appendix E RI-8; V1 Ch7 (GPU Scheduler); V6 AR-14.
    """
    if requested_vram_mb > available_vram_mb:
        raise InvariantViolationError(
            invariant_id="RI-8",
            message=(
                f"GPU VRAM request from '{service_name}' exceeds available capacity: "
                f"requested={requested_vram_mb}MB, available={available_vram_mb}MB"
            ),
            context={
                "service_name": service_name,
                "requested_vram_mb": requested_vram_mb,
                "available_vram_mb": available_vram_mb,
            },
        )
