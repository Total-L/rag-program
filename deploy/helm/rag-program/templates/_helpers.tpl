{{/* Common labels */}}
{{- define "rag-program.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "rag-program.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* Selector labels (used by Service / HPA / PDB) */}}
{{- define "rag-program.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Fullname (truncated to 63 chars per k8s limit) */}}
{{- define "rag-program.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if eq .Release.Name $name -}}
{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}
{{ printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end -}}
{{- end -}}

{{/* ServiceAccount name */}}
{{- define "rag-program.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "rag-program.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}