#!/usr/bin/env bash
# Generates/regenerates every VoiceOS service sub-chart under
# infra/helm/voiceos-platform/charts/ from the canonical template at
# infra/helm/_chart_template/ (Sprint-026, V7 Ch5).
#
# Usage: bash scripts/helm/generate_service_charts.sh
#
# Re-run this any time the shared template (deployment/service/configmap/
# hpa/pdb/networkpolicy/serviceaccount templates or _helpers.tpl) changes --
# it overwrites every service chart's templates/ directory and regenerates
# Chart.yaml/values.yaml from the table below, so the 25 charts never drift
# from each other's shared shape. Chart-specific values (port, namespace,
# GPU toleration, resource sizing, network policy allow-list) live only in
# this table, not hand-edited per chart.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEMPLATE_DIR="${ROOT_DIR}/infra/helm/_chart_template"
CHARTS_DIR="${ROOT_DIR}/infra/helm/voiceos-platform/charts"

# name|port|namespace|gpu_tolerate|egress_ports|allowed_namespaces|description
#
# Sprint-027: every non-voiceos-ops chart's allowed_namespaces gained
# `,voiceos-ops` -- Prometheus (deployed into voiceos-ops) must reach
# every pod's /metrics port to scrape it, and the deny-all-default
# NetworkPolicy baseline (infra/k8s/cluster-policies/default-deny.yaml)
# blocks that cross-namespace traffic without an explicit ingress
# allow-list entry. The voiceos-ops-namespace charts themselves
# (policy-engine/auth/authz/ai-governance) don't need it added --
# same-namespace traffic is already unconditionally allowed.
SERVICES=(
  "media-gateway|8010|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|Telephony ingress boundary (TransportAdapter: Twilio WebSocket, SIP/RTP)"
  "audio-preprocessing|8011|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|AEC3/NS/AGC/resampling pipeline"
  "vad-endpointing|8012|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|Voice activity detection, endpointing, barge-in"
  "gpu-scheduler|8013|voiceos-runtime|true|5432,6379,8000,8100,8200|voiceos-runtime,voiceos-ops|System-wide VRAM ledger and GPU admission control"
  "stt|8100|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|Whisper STT adapter service"
  "llm-runtime|8000|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|vLLM LLM adapter service"
  "tts|8200|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|Veena TTS adapter service"
  "conversation-engine|8014|voiceos-runtime|false|5432,6379,27017|voiceos-runtime,voiceos-ops|Full CIL orchestration (ConversationEngine)"
  "dialogue-manager|8015|voiceos-runtime|false|5432,6379|voiceos-runtime,voiceos-ops|Turn coordination: barge-in, turn yield, backchannel gating"
  "policy-engine|8020|voiceos-ops|false|5432,6379|voiceos-runtime,voiceos-platform|Unified Policy Decision Point"
  "auth|8021|voiceos-ops|false|5432,6379|voiceos-runtime,voiceos-platform|Authentication (JWT/OIDC/mTLS/API-key)"
  "authz|8022|voiceos-ops|false|5432,6379|voiceos-runtime,voiceos-platform|Authorization (RBAC/ABAC/JIT/tenant isolation)"
  "ai-governance|8023|voiceos-ops|false|5432,6379|voiceos-runtime,voiceos-platform|Law of Authority + GovernanceVerdict gate"
  "tenant-management|8030|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Tenant lifecycle, isolation profiles, provisioning"
  "crm|8031|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Customer/party data, CustomerContext assembly"
  "collections|8032|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Loan accounts, EMI schedules, PTP, settlements"
  "campaign-management|8033|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Campaign lifecycle, audience selection, scheduling"
  "contact-center|8034|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Skills routing, live transfer, supervisor controls"
  "billing|8035|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Subscriptions, entitlements, invoicing"
  "metering|8036|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Usage event capture and limit enforcement"
  "analytics|8037|voiceos-platform|false|5432,6379,27017|voiceos-runtime,voiceos-ops|Call/campaign analytics, daily aggregation"
  "admin-portal|8038|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Admin API backend"
  "ai-config|8039|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Prompt versioning, model configuration"
  "integration-platform|8040|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Webhook registration and delivery"
  "api-platform|8041|voiceos-platform|false|5432,6379|voiceos-runtime,voiceos-ops|Public REST API (OpenAPI 3.1)"
)

mkdir -p "${CHARTS_DIR}"

for entry in "${SERVICES[@]}"; do
  IFS='|' read -r NAME PORT NAMESPACE GPU_TOLERATE EGRESS_PORTS ALLOWED_NS DESCRIPTION <<< "${entry}"

  CHART_DIR="${CHARTS_DIR}/${NAME}"
  mkdir -p "${CHART_DIR}/templates"

  # Templates are byte-identical across every chart -- copied verbatim from
  # the canonical source, never hand-edited per chart.
  cp "${TEMPLATE_DIR}/templates/"*.tpl "${CHART_DIR}/templates/" 2>/dev/null || true
  cp "${TEMPLATE_DIR}/templates/"*.yaml "${CHART_DIR}/templates/"

  cat > "${CHART_DIR}/Chart.yaml" <<EOF
apiVersion: v2
name: ${NAME}
description: "${DESCRIPTION}"
type: application
version: 0.1.0
appVersion: "2.0.0"
EOF

  # Convert comma lists to YAML sequences.
  EGRESS_YAML=""
  IFS=',' read -ra PORTS <<< "${EGRESS_PORTS}"
  for p in "${PORTS[@]}"; do
    EGRESS_YAML="${EGRESS_YAML}    - ${p}\n"
  done

  NS_YAML=""
  IFS=',' read -ra NAMESPACES <<< "${ALLOWED_NS}"
  for n in "${NAMESPACES[@]}"; do
    if [ "${n}" != "${NAMESPACE}" ]; then
      NS_YAML="${NS_YAML}    - ${n}\n"
    fi
  done

  cat > "${CHART_DIR}/values.yaml" <<EOF
# ${NAME} -- generated by scripts/helm/generate_service_charts.sh, do not hand-edit.
enabled: true
namespace: ${NAMESPACE}
environment: dev
nodePool: cpu
priorityClassName: $(case "${NAMESPACE}" in voiceos-runtime) echo "critical";; voiceos-ops) echo "high";; *) echo "normal";; esac)

image:
  repository: voiceos/${NAME}
  tag: "latest"
  pullPolicy: IfNotPresent

replicaCount: 2

service:
  port: ${PORT}

resources:
  cpu: "500m"
  memory: "512Mi"

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 6
  targetCPUUtilizationPercentage: 70

podDisruptionBudget:
  enabled: true
  minAvailable: 1

gpu:
  tolerate: ${GPU_TOLERATE}

serviceAccount:
  automountToken: false

networkPolicy:
  egressPorts:
$(printf '%b' "${EGRESS_YAML}")
  allowedNamespaces:
$(printf '%b' "${NS_YAML}")

config: {}
EOF

done

echo "Generated ${#SERVICES[@]} service charts under ${CHARTS_DIR}"
