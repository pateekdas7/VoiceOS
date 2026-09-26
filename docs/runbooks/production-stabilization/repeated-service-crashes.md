# Repeated service crashes

```bash
systemctl status voiceos-bff voiceos-webapi voiceos-voice-runtime voiceos-dialer-worker
journalctl -u voiceos-bff -u voiceos-webapi -u voiceos-voice-runtime -u voiceos-dialer-worker -n 300 --no-pager
```
Find the first failure, respect StartLimit, capture dependency state, apply the smallest fix, then restart once. Persistent instability should move to rollback/recovery rather than endless restarts.