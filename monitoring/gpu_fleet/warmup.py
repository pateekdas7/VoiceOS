"""ModelWarmupOrchestrator -- warm-before-admit at fleet level (V7 Ch6, GPU-1).

On a GPU node join event, warms every required model pool (STT, LLM, TTS)
before that node is added to the serving pool -- the fleet-level
expression of GPU-1 (Sprint-008's per-slot "new model slot warms before
the old slot is released" guarantee, applied here to whole-node
admission rather than a single model swap).

Each pool's warmup is delegated to an injected ``WarmupProbe`` callable so
this orchestrator has no direct dependency on the GPU node's HTTP
servers -- unit tests inject a fake; a real deployment injects a probe
that polls each service's ``/health/ready`` endpoint (or triggers an
explicit warm-up inference call) until it reports ready.

Architecture: V1 Ch7 (GPU-1 warm-before-admit); V7 Ch6 (GPU Fleet Management).
"""

from __future__ import annotations

from collections.abc import Callable

WarmupProbe = Callable[[str, str], bool]
"""``(node_id, model_pool) -> True`` once that pool is warm and ready to serve."""

REQUIRED_MODEL_POOLS: tuple[str, ...] = ("stt", "llm", "tts")


class ModelWarmupOrchestrator:
    """Warms every required model pool on a newly joined GPU node before admitting it."""

    def __init__(
        self,
        warmup_probe: WarmupProbe,
        *,
        on_node_admitted: Callable[[str], None] | None = None,
    ) -> None:
        self._warmup_probe = warmup_probe
        self._on_node_admitted = on_node_admitted
        self._warmed_pools: dict[str, set[str]] = {}

    def on_node_join(
        self,
        node_id: str,
        *,
        model_pools: tuple[str, ...] = REQUIRED_MODEL_POOLS,
    ) -> bool:
        """Warm every pool in ``model_pools`` for ``node_id``.

        Returns True (and invokes ``on_node_admitted``) only once *every*
        required pool has warmed successfully. If any pool fails to warm,
        the node is never admitted to serving -- GPU-1's "no traffic
        before fully warm" guarantee, at fleet-node granularity.
        """
        warmed = {pool for pool in model_pools if self._warmup_probe(node_id, pool)}
        self._warmed_pools[node_id] = warmed
        all_warm = warmed == set(model_pools)
        if all_warm and self._on_node_admitted is not None:
            self._on_node_admitted(node_id)
        return all_warm

    def is_node_warm(
        self,
        node_id: str,
        *,
        model_pools: tuple[str, ...] = REQUIRED_MODEL_POOLS,
    ) -> bool:
        """Whether ``node_id`` has every required pool already warmed."""
        return self._warmed_pools.get(node_id, set()) == set(model_pools)


__all__ = ["REQUIRED_MODEL_POOLS", "ModelWarmupOrchestrator", "WarmupProbe"]
