#!/usr/bin/env bash
# VoiceOS v2 -- deploys the Sprint-027 observability stack into voiceos-ops.
#
# Generates every ConfigMap from monitoring/ (the single source of truth --
# never duplicated into static YAML) and applies the Deployment/Service/
# NetworkPolicy manifests in this directory. Idempotent: re-running updates
# ConfigMaps in place and rolls the affected Deployments.
#
# Usage: bash infra/k8s/observability/deploy.sh
# Requires: KUBECONFIG set, kubectl context pointing at the voiceos cluster,
#           PAGERDUTY_SERVICE_KEY / JIRA_WEBHOOK_URL / SLACK_WEBHOOK_URL /
#           GRAFANA_ADMIN_PASSWORD env vars set (see deployment/cpu/.env.example).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MON_DIR="${ROOT_DIR}/monitoring"
K8S_DIR="${ROOT_DIR}/infra/k8s/observability"
NS="voiceos-ops"

log() { echo "[observability-deploy] $*"; }

log "Ensuring namespace ${NS} exists (should already, from infra/k8s/namespaces.yaml)"
kubectl get namespace "${NS}" >/dev/null

log "Generating Prometheus ConfigMaps..."
kubectl create configmap prometheus-config -n "${NS}" \
  --from-file="${MON_DIR}/prometheus/prometheus.yml" \
  --from-file="${MON_DIR}/prometheus/recording_rules.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap prometheus-rules -n "${NS}" \
  --from-file="${MON_DIR}/prometheus/alert_rules/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap prometheus-targets -n "${NS}" \
  --from-file="${MON_DIR}/prometheus/targets/" \
  --dry-run=client -o yaml | kubectl apply -f -

log "Generating Grafana ConfigMaps..."
kubectl create configmap grafana-datasources -n "${NS}" \
  --from-file="${MON_DIR}/grafana/provisioning/datasources/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashboard-provisioning -n "${NS}" \
  --from-file="${MON_DIR}/grafana/provisioning/dashboards/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashboards-main -n "${NS}" \
  --from-file="${MON_DIR}/grafana/dashboards/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashboards-governance -n "${NS}" \
  --from-file="${MON_DIR}/grafana/dashboards/governance/" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap grafana-dashboards-security -n "${NS}" \
  --from-file="${MON_DIR}/grafana/dashboards/security/" \
  --dry-run=client -o yaml | kubectl apply -f -

log "Generating Alertmanager ConfigMap + routing secret..."
kubectl create configmap alertmanager-config-template -n "${NS}" \
  --from-file="${MON_DIR}/grafana/alerts/alertmanager.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic alertmanager-routing-secrets -n "${NS}" \
  --from-literal=PAGERDUTY_SERVICE_KEY="${PAGERDUTY_SERVICE_KEY:-}" \
  --from-literal=JIRA_WEBHOOK_URL="${JIRA_WEBHOOK_URL:-}" \
  --from-literal=SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic grafana-admin -n "${NS}" \
  --from-literal=password="${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD must be set}" \
  --dry-run=client -o yaml | kubectl apply -f -

log "Generating Loki/FluentBit/OTel/Jaeger ConfigMaps..."
kubectl create configmap loki-config -n "${NS}" \
  --from-file="${MON_DIR}/logging/loki.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap fluentbit-config -n "${NS}" \
  --from-file="${MON_DIR}/logging/fluentbit.conf" \
  --from-file="${MON_DIR}/logging/parsers.conf" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap otel-collector-config -n "${NS}" \
  --from-file="${MON_DIR}/tracing/otel-collector.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap jaeger-config -n "${NS}" \
  --from-file="${MON_DIR}/tracing/jaeger.yml" \
  --dry-run=client -o yaml | kubectl apply -f -

log "Applying NetworkPolicy..."
kubectl apply -f "${K8S_DIR}/networkpolicy.yaml"

log "Applying Deployments/Services..."
kubectl apply -f "${K8S_DIR}/prometheus.yaml"
kubectl apply -f "${K8S_DIR}/grafana.yaml"
kubectl apply -f "${K8S_DIR}/alertmanager.yaml"
kubectl apply -f "${K8S_DIR}/loki.yaml"
kubectl apply -f "${K8S_DIR}/fluentbit.yaml"
kubectl apply -f "${K8S_DIR}/otel-jaeger.yaml"

log "Waiting for rollouts..."
kubectl rollout status deployment/prometheus -n "${NS}" --timeout=180s
kubectl rollout status deployment/grafana -n "${NS}" --timeout=120s
kubectl rollout status deployment/alertmanager -n "${NS}" --timeout=120s
kubectl rollout status deployment/loki -n "${NS}" --timeout=120s
kubectl rollout status deployment/otel-collector -n "${NS}" --timeout=120s
kubectl rollout status deployment/jaeger -n "${NS}" --timeout=120s
kubectl rollout status daemonset/fluentbit -n "${NS}" --timeout=120s

log "Observability stack deployed. Pods in ${NS}:"
kubectl get pods -n "${NS}" -l 'app.kubernetes.io/name in (prometheus,grafana,alertmanager,loki,fluentbit,otel-collector,jaeger)'
