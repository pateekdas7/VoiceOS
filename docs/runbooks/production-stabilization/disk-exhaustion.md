# Disk exhaustion
1. Identify usage: `df -h`.
2. Identify VoiceOS logs: `du -sh /opt/voiceos/logs/* 2>/dev/null | sort -h`.
3. Check logrotate: `cat scripts/logrotate/voiceos`.
4. Do not delete application/database data blindly.
5. Verify free space and service health after remediation.
Environment: target CPU/GPU host.