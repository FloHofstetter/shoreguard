# ShoreGuard OpenClaw sandbox image — hardened OpenClaw for OpenShell.
#
# Layers privilege separation, directory hardening, and a startup script
# on top of the upstream OpenClaw image.  Config (openclaw.json) is NOT
# baked at build time — ShoreGuard injects it at deploy time via exec,
# then locks it down with the entrypoint script.
#
# Uses /sandbox/ as HOME (OpenShell convention), not /home/node/.
#
# Build:
#   docker build -t ghcr.io/shoreguard/openclaw-sandbox:latest .
#
# The image expects ShoreGuard to:
#   1. Create the sandbox (openshell sandbox create --from this-image)
#   2. Write openclaw.json via exec
#   3. Apply the hardened policy preset
#   4. Configure inference routing via set_cluster_inference

FROM ghcr.io/openclaw/openclaw:latest

USER root

ENV DEBIAN_FRONTEND=noninteractive

# Install security and privilege-separation tools.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcap2-bin \
        e2fsprogs \
        iproute2 \
        iptables \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# gosu 1.19 for privilege separation (gateway vs sandbox user).
# hadolint ignore=DL4006
RUN arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
        amd64) gosu_asset="gosu-amd64"; gosu_sha256="52c8749d0142edd234e9d6bd5237dff2d81e71f43537e2f4f66f75dd4b243dd0" ;; \
        arm64) gosu_asset="gosu-arm64"; gosu_sha256="3a8ef022d82c0bc4a98bcb144e77da714c25fcfa64dccc57f6aba7ae47ff1a44" ;; \
        *) echo "Unsupported architecture for gosu: $arch" >&2; exit 1 ;; \
    esac \
    && curl -fsSL -o /usr/local/bin/gosu "https://github.com/tianon/gosu/releases/download/1.19/${gosu_asset}" \
    && echo "${gosu_sha256}  /usr/local/bin/gosu" | sha256sum -c - \
    && chmod +x /usr/local/bin/gosu \
    && gosu --version

# Create gateway and sandbox users (OpenShell convention).
# - sandbox: runs agent code, home=/sandbox, login shell
# - gateway: runs OpenClaw gateway process, non-login shell
RUN groupadd -r gateway \
    && useradd -r -g gateway -d /sandbox -s /usr/sbin/nologin gateway \
    && groupadd -r sandbox \
    && useradd -r -g sandbox -d /sandbox -s /bin/bash sandbox \
    && mkdir -p /sandbox \
    && chown sandbox:sandbox /sandbox

# Split .openclaw into immutable config dir + writable state dir.
# OpenShell's Landlock will enforce /sandbox/.openclaw as read-only.
# Writable state lives in .openclaw-data, reached via symlinks.
RUN mkdir -p /sandbox/.openclaw-data/agents/main/agent \
        /sandbox/.openclaw-data/extensions \
        /sandbox/.openclaw-data/workspace \
        /sandbox/.openclaw-data/skills \
        /sandbox/.openclaw-data/hooks \
        /sandbox/.openclaw-data/identity \
        /sandbox/.openclaw-data/devices \
        /sandbox/.openclaw-data/canvas \
        /sandbox/.openclaw-data/cron \
        /sandbox/.openclaw-data/memory \
        /sandbox/.openclaw-data/credentials \
    && mkdir -p /sandbox/.openclaw \
    && ln -s /sandbox/.openclaw-data/agents /sandbox/.openclaw/agents \
    && ln -s /sandbox/.openclaw-data/extensions /sandbox/.openclaw/extensions \
    && ln -s /sandbox/.openclaw-data/workspace /sandbox/.openclaw/workspace \
    && ln -s /sandbox/.openclaw-data/skills /sandbox/.openclaw/skills \
    && ln -s /sandbox/.openclaw-data/hooks /sandbox/.openclaw/hooks \
    && ln -s /sandbox/.openclaw-data/identity /sandbox/.openclaw/identity \
    && ln -s /sandbox/.openclaw-data/devices /sandbox/.openclaw/devices \
    && ln -s /sandbox/.openclaw-data/canvas /sandbox/.openclaw/canvas \
    && ln -s /sandbox/.openclaw-data/cron /sandbox/.openclaw/cron \
    && ln -s /sandbox/.openclaw-data/memory /sandbox/.openclaw/memory \
    && ln -s /sandbox/.openclaw-data/credentials /sandbox/.openclaw/credentials \
    && touch /sandbox/.openclaw-data/update-check.json \
    && ln -s /sandbox/.openclaw-data/update-check.json /sandbox/.openclaw/update-check.json \
    && touch /sandbox/.openclaw-data/exec-approvals.json \
    && ln -s /sandbox/.openclaw-data/exec-approvals.json /sandbox/.openclaw/exec-approvals.json \
    && chown -R sandbox:sandbox /sandbox/.openclaw-data \
    && chown -R sandbox:sandbox /sandbox/.openclaw

COPY entrypoint.sh /usr/local/bin/shoreguard-start
COPY sandbox-init.sh /usr/local/bin/sleep
RUN chmod +x /usr/local/bin/shoreguard-start /usr/local/bin/sleep

ENV HOME=/sandbox
ENV OPENCLAW_STATE_DIR=/sandbox/.openclaw

USER sandbox
WORKDIR /sandbox

ENTRYPOINT ["/usr/local/bin/shoreguard-start"]
CMD ["openclaw", "gateway", "run", "--bind", "lan", "--port", "18789", "--allow-unconfigured"]
