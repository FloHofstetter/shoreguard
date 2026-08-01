# ── Stage 0: Build the Preact/TypeScript island bundle ──────────────────────
# frontend/dist is a Vite build artifact (git-ignored), so it must be compiled
# here — the wheel force-includes it as shoreguard/_frontend/dist.
FROM node:25-slim AS frontend

WORKDIR /build/frontend
# Install against the lockfile first so the layer caches across source edits.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ── Stage 1: Build wheel ────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE hatch_build.py ./
COPY shoreguard/ shoreguard/
COPY frontend/ frontend/
# Bring the compiled bundle from the node stage so hatchling's force-include
# ships the real islands, not an empty directory.
COPY --from=frontend /build/frontend/dist/ frontend/dist/

RUN pip install --no-cache-dir hatchling \
    && python -m hatchling build -t wheel

# ── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM python:3.14-slim

# Pass build identity at build time. CI sets these from the git tag, commit,
# and build timestamp; local builds default to "dev"/"unknown".
#   --build-arg SHOREGUARD_VERSION=0.28.0
#   --build-arg SHOREGUARD_GIT_SHA=a1b2c3d
#   --build-arg SHOREGUARD_BUILD_TIME=2026-04-10T12:00:00Z
ARG SHOREGUARD_VERSION=dev
ARG SHOREGUARD_GIT_SHA=unknown
ARG SHOREGUARD_BUILD_TIME=unknown
ENV SHOREGUARD_VERSION=${SHOREGUARD_VERSION} \
    SHOREGUARD_GIT_SHA=${SHOREGUARD_GIT_SHA} \
    SHOREGUARD_BUILD_TIME=${SHOREGUARD_BUILD_TIME}
LABEL org.opencontainers.image.title="ShoreGuard" \
      org.opencontainers.image.description="Open-source control plane for NVIDIA OpenShell" \
      org.opencontainers.image.version="${SHOREGUARD_VERSION}" \
      org.opencontainers.image.revision="${SHOREGUARD_GIT_SHA}" \
      org.opencontainers.image.created="${SHOREGUARD_BUILD_TIME}" \
      org.opencontainers.image.url="https://github.com/FloHofstetter/shoreguard" \
      org.opencontainers.image.source="https://github.com/FloHofstetter/shoreguard" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN groupadd -g 1000 shoreguard \
    && useradd -u 1000 -g shoreguard -m shoreguard

COPY --from=builder /build/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl "psycopg[binary]>=3.1" \
    && rm -rf /tmp/*.whl

USER shoreguard
WORKDIR /home/shoreguard

ENV SHOREGUARD_RELOAD=false
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/healthz')"

ENTRYPOINT ["shoreguard"]
