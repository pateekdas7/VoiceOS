"""ModelPool — manages leased instances of a specific model on GPU(s).

Each pool type (STT, LLM, TTS) maintains N pre-warmed model instances.
Callers acquire() a ModelHandle, perform inference, then release() it.

Warm-before-admit guarantee (GPU-1):
    warm_new_instance() allocates VRAM for the new slot and adds it to the
    pool *before* retiring any old slot, so the pool size never drops below
    its pre-scale-up value during the transition.

Architecture: V1 Ch7 (Model Pool); V7 Ch6 (GPU-1 warm-before-admit).
"""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from enum import StrEnum

from .vram_ledger import AllocationToken, VRAMLedger

# ---------------------------------------------------------------------------
# Pool types
# ---------------------------------------------------------------------------


class PoolType(StrEnum):
    """The three model pool types managed by the GPU Scheduler."""

    STT_POOL = "STT_POOL"
    LLM_POOL = "LLM_POOL"
    TTS_POOL = "TTS_POOL"


# ---------------------------------------------------------------------------
# ModelHandle
# ---------------------------------------------------------------------------


@dataclass
class ModelHandle:
    """A leased model instance returned by ModelPool.acquire().

    Hold the handle while performing inference; call pool.release(handle)
    when done to return the slot to the pool.
    """

    handle_id: str
    """Unique identifier for this lease (UUID)."""
    pool_type: PoolType
    """Which pool this handle belongs to."""
    device_id: str
    """GPU device hosting the model instance."""
    allocation_token: AllocationToken
    """VRAM ledger token; returned to the ledger when the handle is retired."""
    is_warm: bool = True
    """True once the model is loaded and ready for inference."""
    _released: bool = False

    def __repr__(self) -> str:
        return f"ModelHandle(handle_id={self.handle_id!r}, pool_type={self.pool_type!r}, device_id={self.device_id!r})"


# ---------------------------------------------------------------------------
# ModelPool
# ---------------------------------------------------------------------------


class ModelPool:
    """Manages N instances of a specific model on one or more GPU devices.

    Thread-safe.  acquire() blocks until an instance is free (up to
    ``timeout_ms`` ms).  release() makes the instance available again.

    warm_new_instance() implements the warm-before-admit guarantee:
        1. VRAM for the new slot is allocated.
        2. The slot is added to the pool (admitted).
        3. Only then is any old slot optionally retired.

    Architecture: V1 Ch7 (Model Pool, warm-before-admit GPU-1); V7 Ch6.
    """

    def __init__(
        self,
        pool_type: PoolType,
        size: int,
        vram_per_instance_mb: int,
        ledger: VRAMLedger,
        devices: list[str],
    ) -> None:
        """Initialise the pool and warm ``size`` instances.

        Args:
            pool_type:             Identifies the pool (STT / LLM / TTS).
            size:                  Number of model instances to pre-warm.
            vram_per_instance_mb:  VRAM cost per instance in MB.
            ledger:                VRAMLedger used for VRAM accounting.
            devices:               Device IDs to spread instances across
                                   (round-robin).
        """
        if not devices:
            raise ValueError("ModelPool requires at least one device")
        self._pool_type = pool_type
        self._vram_per_instance_mb = vram_per_instance_mb
        self._ledger = ledger
        self._devices = list(devices)
        self._available: queue.Queue[ModelHandle] = queue.Queue()
        self._lock: threading.Lock = threading.Lock()
        self._all_handles: list[ModelHandle] = []

        for i in range(size):
            handle = self._create_handle(device_index=i % len(self._devices))
            self._all_handles.append(handle)
            self._available.put(handle)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, timeout_ms: int = 5000) -> ModelHandle:
        """Acquire a model instance from the pool.

        Blocks until an instance becomes available or ``timeout_ms`` elapses.

        Args:
            timeout_ms: Maximum wait time in milliseconds.

        Returns:
            A ModelHandle for the acquired instance.

        Raises:
            TimeoutError: when no instance becomes available within timeout_ms.
        """
        try:
            handle = self._available.get(timeout=timeout_ms / 1000.0)
            handle._released = False
            return handle
        except queue.Empty:
            raise TimeoutError(f"No {self._pool_type.value} instance available within {timeout_ms} ms") from None

    def release(self, handle: ModelHandle) -> None:
        """Return a model instance to the pool.

        Idempotent — releasing an already-released handle is a no-op.
        """
        if not handle._released:
            handle._released = True
            self._available.put(handle)

    def warm_new_instance(self, retire_old: bool = False) -> ModelHandle:
        """Add a new warmed instance to the pool (warm-before-admit, GPU-1).

        The new instance's VRAM is allocated and the instance is admitted to
        the pool *before* any existing instance is retired.  This guarantees
        that at no moment does the pool have fewer available slots than it did
        before the scale-up started.

        Args:
            retire_old: When True, the oldest handle (first created) is
                        retired from the pool after the new one is admitted.

        Returns:
            The new ModelHandle (already admitted to the pool and warm).
        """
        # Step 1 — allocate VRAM + create handle (warm).
        new_handle = self._create_handle(device_index=0)
        new_handle.is_warm = True

        # Step 2 — admit to pool (pool size increases here).
        with self._lock:
            self._all_handles.append(new_handle)
        self._available.put(new_handle)

        # Step 3 — only NOW retire the old slot (if requested).
        if retire_old and len(self._all_handles) > 1:
            with self._lock:
                old_handle = self._all_handles[0]
            if old_handle is not new_handle:
                self._ledger.release(old_handle.allocation_token)
                with self._lock:
                    if old_handle in self._all_handles:
                        self._all_handles.remove(old_handle)

        return new_handle

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def available_count(self) -> int:
        """Number of instances currently available for acquire()."""
        return self._available.qsize()

    @property
    def pool_size(self) -> int:
        """Total pool size (available + currently acquired)."""
        with self._lock:
            return len(self._all_handles)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_handle(self, device_index: int) -> ModelHandle:
        """Allocate VRAM and create a new ModelHandle for the given device."""
        device_id = self._devices[device_index]
        token = self._ledger.allocate(
            device_id=device_id,
            model_id=f"{self._pool_type.value.lower()}_instance",
            vram_mb=self._vram_per_instance_mb,
            service_name=self._pool_type.value,
        )
        return ModelHandle(
            handle_id=str(uuid.uuid4()),
            pool_type=self._pool_type,
            device_id=device_id,
            allocation_token=token,
            is_warm=True,
        )
