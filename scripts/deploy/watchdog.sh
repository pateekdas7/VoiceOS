#!/bin/bash
# VoiceOS service watchdog — checks all services, restarts dead ones, logs results

LOG_FILE="/var/log/voiceos/watchdog.log"
SERVICES=("voiceos-bff" "voiceos-webapi" "voiceos-frontend" "voiceos-voice-runtime" "voiceos-dialer-worker")
MAX_RESTART_ATTEMPTS=3
ALERT_WEBHOOK="${VOICEOS_ALERT_WEBHOOK:-}"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"
}

send_alert() {
    local service="$1"
    local status="$2"
    log "ALERT: $service is $status"
    if [[ -n "$ALERT_WEBHOOK" ]]; then
        curl -s -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\":\"[VoiceOS Watchdog] $service is $status on $(hostname)\"}" \
            >> "$LOG_FILE" 2>&1 || true
    fi
}

check_and_restart() {
    local svc="$1"
    local active
    active=$(systemctl is-active "$svc" 2>/dev/null)

    if [[ "$active" == "active" ]]; then
        log "OK: $svc is running"
        return 0
    fi

    log "WARN: $svc is $active — attempting restart"
    send_alert "$svc" "$active"

    local attempt=0
    while (( attempt < MAX_RESTART_ATTEMPTS )); do
        (( attempt++ ))
        echo "mamata@1976" | sudo -S systemctl restart "$svc" >> "$LOG_FILE" 2>&1
        sleep 5
        active=$(systemctl is-active "$svc" 2>/dev/null)
        if [[ "$active" == "active" ]]; then
            log "RECOVERED: $svc restarted successfully (attempt $attempt)"
            send_alert "$svc" "RECOVERED after restart attempt $attempt"
            return 0
        fi
        log "RETRY $attempt/$MAX_RESTART_ATTEMPTS: $svc still $active"
    done

    log "CRITICAL: $svc failed to restart after $MAX_RESTART_ATTEMPTS attempts"
    send_alert "$svc" "CRITICAL — failed to restart after $MAX_RESTART_ATTEMPTS attempts"
    return 1
}

main() {
    mkdir -p "$(dirname "$LOG_FILE")"
    log "--- Watchdog run start ---"

    local failed=0
    for svc in "${SERVICES[@]}"; do
        check_and_restart "$svc" || (( failed++ ))
    done

    log "--- Watchdog run end: $failed service(s) in failed state ---"
    return "$failed"
}

main "$@"
