"""Per-failure-class recovery strategies (V3 Ch7 crash recovery matrix)."""

from __future__ import annotations

from .cpu_restart import CPURestartStrategy
from .db_outage import DBOutageStrategy
from .gpu_failure import GPUFailoverPort, GPUFailureStrategy, TTSHaltPort
from .network_partition import NetworkPartitionStrategy
from .redis_outage import RedisOutageStrategy
from .twilio_disconnect import TwilioDisconnectStrategy

__all__ = [
    "CPURestartStrategy",
    "DBOutageStrategy",
    "GPUFailoverPort",
    "GPUFailureStrategy",
    "NetworkPartitionStrategy",
    "RedisOutageStrategy",
    "TTSHaltPort",
    "TwilioDisconnectStrategy",
]
