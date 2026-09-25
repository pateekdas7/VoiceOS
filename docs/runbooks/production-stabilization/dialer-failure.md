# Dialer failure
1. Check: `systemctl status voiceos-dialer-worker`.
2. Inspect: `journalctl -u voiceos-dialer-worker -n 150 --no-pager`.
3. Check Redis and queue state: `bash deployment/cpu/healthcheck.sh`.
4. Restart only after checking whether calls are active: `systemctl restart voiceos-dialer-worker`.
5. Verify queue reconciliation and terminal call states before resuming campaigns.
Environment: CPU node with Redis and telephony connectivity.