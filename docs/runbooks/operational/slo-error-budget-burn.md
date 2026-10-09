# Runbook: SLO Error Budget Burn
**Alerts:** AvailabilitySLOFastBurn, FirstAudioSLOFastBurn | **Severity:** critical (page), warning (ticket)

## What it means
- **FastBurn (critical):** Burning error budget at >14.4x the sustainable rate. At this rate, the 30-day budget is exhausted in <2 days. Page on-call.
- **SlowBurn (warning):** Burning at >6x. Create a ticket; investigate this business day.

## Immediate steps — Availability SLO
```bash
# What is the current call failure rate?
curl -s 'http://localhost:9090/api/v1/query?query=voiceos:availability_slo:burn_rate_1h' | python3 -c "import json,sys; r=json.load(sys.stdin); print(r['data']['result'])"

# Which service is causing failures?
# Check BFF error rate
grep '"level":"ERROR"' /opt/voiceos/logs/bff.log | tail -20
# Check webapi
tail -30 /opt/voiceos/logs/webapi.log | grep ERROR
# Check dialer
journalctl -u voiceos-dialer-worker --since "30 minutes ago" | grep -i error | tail -20
```

## Immediate steps — First Audio SLO
```bash
# Is the GPU node reachable?
STT_URL=$(grep STT_BASE_URL /opt/voiceos/.env | cut -d= -f2)
curl -sf "${STT_URL}/health/ready" && echo "STT OK" || echo "STT UNREACHABLE"

LLM_URL=$(grep LLM_BASE_URL /opt/voiceos/.env | cut -d= -f2)
curl -sf "${LLM_URL}/health" && echo "LLM OK" || echo "LLM UNREACHABLE"
```

## Escalate if
- FastBurn alert fires and root cause not identified within 15 minutes
- Both SLOs burning simultaneously (systemic failure)
- GPU node unreachable — ask GPU node owner to restart

## Resolution
- Fix the highest-error-rate component first (follow its individual runbook)
- If GPU is down: follow `gpu-node-failure.md`
- After fix, monitor burn rate for 10 minutes to confirm it drops below 1x
