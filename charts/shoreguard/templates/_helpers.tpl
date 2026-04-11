{{/*
Expand the name of the chart.
*/}}
{{- define "shoreguard.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Fully qualified app name. Truncated to 63 chars for k8s DNS compliance.
*/}}
{{- define "shoreguard.fullname" -}}
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

{{- define "shoreguard.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Common labels
*/}}
{{- define "shoreguard.labels" -}}
helm.sh/chart: {{ include "shoreguard.chart" . }}
{{ include "shoreguard.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: shoreguard
{{- end -}}

{{/*
Selector labels
*/}}
{{- define "shoreguard.selectorLabels" -}}
app.kubernetes.io/name: {{ include "shoreguard.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Service account name
*/}}
{{- define "shoreguard.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "shoreguard.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/*
Secret name. Returns .Values.existingSecret when set (BYO secret), otherwise
the chart-managed name "<fullname>-secrets".
*/}}
{{- define "shoreguard.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "shoreguard.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/*
ConfigMap name (fullname + "-config")
*/}}
{{- define "shoreguard.configMapName" -}}
{{- printf "%s-config" (include "shoreguard.fullname" .) -}}
{{- end -}}

{{/*
Resolve the session secret key.

Precedence:
  1. Explicit .Values.secretKey (with a hard-fail if shorter than 32 chars).
  2. Existing Secret from a previous release (lookup) — keeps value stable
     across upgrades so sessions do not get invalidated.
  3. Freshly generated 48-char random string on first install.

Returns the raw string. Templates base64-encode it where needed.
*/}}
{{- define "shoreguard.secretKeyValue" -}}
{{- if .Values.secretKey -}}
{{- if lt (len .Values.secretKey) 32 -}}
{{- fail "secretKey must be at least 32 characters (ShoreGuard refuses shorter keys via enforce_production_safety)" -}}
{{- end -}}
{{- .Values.secretKey -}}
{{- else -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace (include "shoreguard.secretName" .) -}}
{{- if and $existing (hasKey $existing.data "secret-key") -}}
{{- index $existing.data "secret-key" | b64dec -}}
{{- else -}}
{{- randAlphaNum 48 -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Values-level validation. Called once from deployment.yaml so the checks
fire at `helm template` / `helm install` time rather than at pod startup.
Covers footgun combinations the backend can't catch until it's already
running.
*/}}
{{- define "shoreguard.validate" -}}
{{- if .Values.existingSecret -}}
{{- if .Values.admin.password -}}
{{- fail "existingSecret and admin.password are mutually exclusive — choose one. existingSecret means the user manages the Secret externally; admin.password tells the chart to write one." -}}
{{- end -}}
{{- if .Values.secretKey -}}
{{- fail "existingSecret and secretKey are mutually exclusive — choose one." -}}
{{- end -}}
{{- else -}}
{{- if not .Values.admin.password -}}
{{- fail "admin.password is required — set it via --set admin.password=... or a values file (or provide an existingSecret with admin-password and secret-key keys)." -}}
{{- end -}}
{{- end -}}
{{- if and (gt (int .Values.replicaCount) 1) .Values.persistence.enabled (not .Values.database.url) -}}
{{- fail "replicaCount > 1 with persistence.enabled=true and no database.url is unsupported: ReadWriteOnce PVC can attach to only one pod. Set database.url to an external Postgres when scaling out." -}}
{{- end -}}
{{- if and (gt (int .Values.replicaCount) 1) (not .Values.secretKey) (not .Values.existingSecret) -}}
{{- fail "replicaCount > 1 requires an explicit secretKey (or an existingSecret that provides one). Without it, each replica derives its own on-disk key and sessions break on every load-balancer decision." -}}
{{- end -}}
{{- end -}}

{{/*
Image reference — repository:tag with Chart.AppVersion as default tag.
*/}}
{{- define "shoreguard.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}
