# Load Tests

Load and performance tests for VoiceOS. Implemented in Sprint-028
(Performance Validation, Load Testing & Production Alpha Deploy).

## Tools

| Tool   | Purpose                                | Config file            |
|--------|----------------------------------------|------------------------|
| Locust | Concurrent call simulation             | `locustfile.py`        |
| k6     | HTTP/WebSocket endpoint stress testing | `k6_scripts/`          |

## Target Scenarios

| Scenario                   | Target                          | Sprint    |
|----------------------------|---------------------------------|-----------|
| Baseline first-audio p95   | ≤ 1.5 s single call             | Sprint-012 |
| Concurrent call load       | 500 simultaneous calls          | Sprint-028 |
| GPU utilization under load | ≤ 0.80                          | Sprint-028 |
| Latency degradation        | ≤ 10% vs. baseline under load   | Sprint-028 |

## Running Load Tests

```bash
# Locust interactive
locust -f tests/load/locustfile.py --host http://voiceos-media-gw:8080

# k6 script
k6 run tests/load/k6_scripts/concurrent_calls.js
```

## Architecture Reference

V1 Ch23 (Performance Budget); V7 Ch4 (Deployment Strategy);
V7 Ch15 (Load Testing); DocSuite-09 (Deployment Cookbook).
