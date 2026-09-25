# GPU-service failure
1. On the GPU host run: `bash deployment/gpu/healthcheck.sh`.
2. Check: `systemctl status voiceos-stt voiceos-llm voiceos-tts`.
3. Inspect: `journalctl -u voiceos-stt -u voiceos-llm -u voiceos-tts -n 150 --no-pager`.
4. Verify NVIDIA state: `nvidia-smi`.
5. Use `infra/dr/runbooks/gpu-node-failure.md` for node failure/drain.
6. Do not declare recovery until STT, LLM and TTS readiness checks succeed.
Environment: actual GPU host.