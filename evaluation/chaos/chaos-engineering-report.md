# Chaos Engineering Report — Sprint-028

**Environment:** Production Alpha (voiceos-runtime namespace)
**Date:** _FILL IN_
**Tool:** `tests/chaos/chaos_scenarios.py` + Chaos Mesh / kubectl disruption
**Status:** ⬜ PENDING (Phase 2)

---

## Scenarios

### S-01: GPU Node-0 Failure

| | |
|---|---|
| **Action** | Delete STT/LLM/TTS pods on GPU node-0 during 200 concurrent calls |
| **Gate** | ≤ 5 calls dropped; GPU node-1 absorbs load within 30 s |
| **Result** | ⬜ PENDING |
| **Calls dropped** | _FILL IN_ |
| **Failover time** | _FILL IN_ |
| **Notes** | |

---

### S-02: Redis Primary Failure

| | |
|---|---|
| **Action** | Delete `redis-master-0` pod during active calls |
| **Gate** | Calls complete in degraded mode; no data loss; standby promotes within 20 s |
| **Result** | ⬜ PENDING |
| **Promotion time** | _FILL IN_ |
| **Data loss events** | _FILL IN_ |
| **Notes** | |

---

### S-03: 20 % RTP Packet Loss

| | |
|---|---|
| **Action** | Inject 20 % packet loss via `tc netem` on media-gateway for 60 s |
| **Gate** | PLC compensates; STT WER within 5 % of baseline |
| **Result** | ⬜ PENDING |
| **Baseline WER** | _FILL IN_ |
| **Under-loss WER** | _FILL IN_ |
| **WER delta** | _FILL IN_ |
| **Notes** | |

---

### S-04: Postgres Primary Failure

| | |
|---|---|
| **Action** | Delete `postgres-primary-0` pod during active calls |
| **Gate** | Standby promotes within 45 s; no PTP duplication; no data corruption |
| **Result** | ⬜ PENDING |
| **Promotion time** | _FILL IN_ |
| **PTP duplicates** | _FILL IN_ |
| **Notes** | |

---

### S-05: Conversation Engine Pod Killed

| | |
|---|---|
| **Action** | Delete one `conversation-engine` pod during active call |
| **Gate** | Replacement pod starts; session state recovered from Redis snapshot |
| **Result** | ⬜ PENDING |
| **Recovery time** | _FILL IN_ |
| **Sessions lost** | _FILL IN_ |
| **Notes** | |

---

## Summary

| Scenario | Pass? | Gate Met? |
|----------|-------|-----------|
| S-01: GPU node-0 failure | ⬜ | ⬜ |
| S-02: Redis primary failure | ⬜ | ⬜ |
| S-03: 20 % RTP packet loss | ⬜ | ⬜ |
| S-04: Postgres primary failure | ⬜ | ⬜ |
| S-05: Conversation engine failure | ⬜ | ⬜ |

**Overall:** ⬜ PENDING

---

*Fill in actual outcomes after running chaos scenarios against the production alpha stack.*
