# ── Stage 1: Build wheel ────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY shoreguard/ shoreguard/
COPY frontend/ frontend/

RUN pip install --no-cache-dir hatchling \
    && python -m hatchling build -t wheel

# ── Stage 2: Runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim

# Pass version at build time: --build-arg SHOREGUARD_VERSION=0.16.0
# CI sets this from the git tag; local builds default to "dev".
ARG SHOREGUARD_VERSION=dev
LABEL org.opencontainers.image.title="ShoreGuard" \
      org.opencontainers.image.description="Open-source control plane for NVIDIA OpenShell" \
      org.opencontainers.image.version="${SHOREGUARD_VERSION}" \
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
