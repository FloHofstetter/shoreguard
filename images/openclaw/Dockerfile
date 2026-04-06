# ShoreGuard OpenClaw sandbox image — hardened OpenClaw for OpenShell.
#
# Layers privilege separation, directory hardening, and a startup script
# on top of the upstream OpenClaw image.  Config (openclaw.json) is NOT
# baked at build time — ShoreGuard injects it at deploy time via exec,
# then locks it down with the entrypoint script.
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
# libcap2-bin: capsh for capability dropping
# e2fsprogs: chattr for immutable symlinks (best-effort)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcap2-bin \
        e2fsprogs \
        python3 \
    && rm -rf /var/lib/apt/lists/*

# gosu 1.19 for privilege separation (gateway vs sandbox user).
# Pinned with per-arch SHA256 checksums.
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

# Create gateway user for privilege separation.
# The upstream image already has 'node' (uid 1000) which becomes our sandbox user.
# The gateway process runs as 'gateway' so the sandbox user cannot kill/restart it.
RUN groupadd -r gateway \
    && useradd -r -g gateway -d /home/node -s /usr/sbin/nologin gateway

# Split .openclaw into immutable config dir + writable state dir.
# Landlock policy will enforce /home/node/.openclaw as read-only.
# Writable state lives in .openclaw-data, reached via symlinks.
RUN rm -rf /home/node/.openclaw \
    && mkdir -p /home/node/.openclaw-data/agents/main/agent \
        /home/node/.openclaw-data/extensions \
        /home/node/.openclaw-data/workspace \
        /home/node/.openclaw-data/skills \
        /home/node/.openclaw-data/hooks \
        /home/node/.openclaw-data/identity \
        /home/node/.openclaw-data/devices \
        /home/node/.openclaw-data/canvas \
        /home/node/.openclaw-data/cron \
        /home/node/.openclaw-data/memory \
        /home/node/.openclaw-data/credentials \
    && mkdir -p /home/node/.openclaw \
    && ln -s /home/node/.openclaw-data/agents /home/node/.openclaw/agents \
    && ln -s /home/node/.openclaw-data/extensions /home/node/.openclaw/extensions \
    && ln -s /home/node/.openclaw-data/workspace /home/node/.openclaw/workspace \
    && ln -s /home/node/.openclaw-data/skills /home/node/.openclaw/skills \
    && ln -s /home/node/.openclaw-data/hooks /home/node/.openclaw/hooks \
    && ln -s /home/node/.openclaw-data/identity /home/node/.openclaw/identity \
    && ln -s /home/node/.openclaw-data/devices /home/node/.openclaw/devices \
    && ln -s /home/node/.openclaw-data/canvas /home/node/.openclaw/canvas \
    && ln -s /home/node/.openclaw-data/cron /home/node/.openclaw/cron \
    && ln -s /home/node/.openclaw-data/memory /home/node/.openclaw/memory \
    && ln -s /home/node/.openclaw-data/credentials /home/node/.openclaw/credentials \
    && touch /home/node/.openclaw-data/update-check.json \
    && ln -s /home/node/.openclaw-data/update-check.json /home/node/.openclaw/update-check.json \
    && touch /home/node/.openclaw-data/exec-approvals.json \
    && ln -s /home/node/.openclaw-data/exec-approvals.json /home/node/.openclaw/exec-approvals.json \
    && chown -R node:node /home/node/.openclaw-data

# .openclaw directory owned by root — sandbox user can read but not write.
# openclaw.json will be injected by ShoreGuard at deploy time, then locked.
RUN chown root:root /home/node/.openclaw \
    && find /home/node/.openclaw -mindepth 1 -maxdepth 1 -exec chown -h root:root {} + \
    && chmod 755 /home/node/.openclaw

COPY entrypoint.sh /usr/local/bin/shoreguard-start
RUN chmod +x /usr/local/bin/shoreguard-start

USER node
WORKDIR /home/node

ENTRYPOINT ["/usr/local/bin/shoreguard-start"]
CMD ["openclaw", "gateway", "run", "--bind", "lan", "--port", "18789", "--allow-unconfigured"]
