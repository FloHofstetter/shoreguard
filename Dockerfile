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
