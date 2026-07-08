"""VoiceOS v2 -- monitoring/ operational-layer Python packages (Sprint-027).

Configuration-only subtrees (prometheus/, grafana/, logging/, tracing/)
live as plain YAML/JSON/conf files, not Python. Only monitoring/gpu_fleet/
is an importable package (fleet-level GPU operational tooling).
"""

from __future__ import annotations
