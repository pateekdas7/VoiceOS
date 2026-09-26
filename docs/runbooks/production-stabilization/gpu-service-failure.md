# GPU service failure

Use `infra/dr/runbooks/gpu-node-failure.md`.

1. Check Prometheus `GPUUnavailable` / fleet-degradation alerts.
2. Determine whether STT, LLM, TTS, or the whole node is affected.
3. Drain/fence a failing node when a surviving GPU exists.
4. With the current single-node fleet, follow the documented replacement/restore procedure; do not claim failover without a second node.
Runtime GPU evidence is required before declaring recovery.