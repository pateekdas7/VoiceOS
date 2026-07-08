{{/* Umbrella-chart-owned resources (SaaSOpsService) -- distinct from the
     per-service subcharts' own _helpers.tpl, since these templates render
     under the umbrella chart's own Chart.Name ("voiceos-platform"), not a
     subchart's. */}}

{{- define "voiceos-platform.saasOps.fullname" -}}
{{- printf "%s-saas-ops" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "voiceos-platform.saasOps.labels" -}}
app.kubernetes.io/name: saas-ops
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: voiceos-platform
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "voiceos-platform.saasOps.selectorLabels" -}}
app.kubernetes.io/name: saas-ops
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
