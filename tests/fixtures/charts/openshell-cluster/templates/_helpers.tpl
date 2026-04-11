{{/*
Expand the name of the chart.
*/}}
{{- define "openshell.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name. Truncated to 63 chars for k8s DNS compliance.
*/}}
{{- define "openshell.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "openshell.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "openshell.labels" -}}
helm.sh/chart: {{ include "openshell.chart" . }}
{{ include "openshell.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: shoreguard
openshell.io/env: {{ .Values.label.env | quote }}
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "openshell.selectorLabels" -}}
app.kubernetes.io/name: {{ include "openshell.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Outer-cluster Secret that holds the exported client mTLS material
(ca.crt, client.crt, client.key). scripts/m12_demo.py reads this via
`kubectl get secret` to build the /api/gateway/register payload.
*/}}
{{- define "openshell.clientSecretName" -}}
{{- if .Values.bootstrap.clientSecretName -}}
{{- .Values.bootstrap.clientSecretName -}}
{{- else -}}
{{- printf "%s-client-tls" (include "openshell.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
Image reference — repository:tag with Chart.AppVersion as default tag.
*/}}
{{- define "openshell.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{/*
Values-level validation. Called once from statefulset.yaml so the checks
fire at `helm template` / `helm install` time.
*/}}
{{- define "openshell.validate" -}}
{{- if not .Values.label.env -}}
{{- fail "label.env is required — pass --set label.env=<dev|staging|...>. It is used as the openshell.io/env pod label and as the env label on the registered gateway, so the federation label-filter assertions (m12_demo Phase D) only pass when it is set." -}}
{{- end -}}
{{- if not .Values.containerSecurityContext.privileged -}}
{{- fail "containerSecurityContext.privileged=true is required: the openshell cluster image runs k3s inside the container and will not start without it." -}}
{{- end -}}
{{- end -}}
