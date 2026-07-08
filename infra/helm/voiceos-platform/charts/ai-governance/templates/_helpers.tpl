{{/*
Common name/label helpers, shared verbatim across every VoiceOS service
chart (Sprint-026, stamped by scripts/helm/generate_service_charts.sh).
*/}}

{{- define "voiceos.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "voiceos.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: voiceos-platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
voiceos.io/node-pool: {{ .Values.nodePool | default "cpu" }}
{{- end -}}

{{- define "voiceos.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
