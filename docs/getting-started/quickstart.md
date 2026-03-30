# Quick Start

Get from zero to a running sandbox in under five minutes.

## Step 1 — Install and start

```bash
pip install shoreguard && shoreguard
```

ShoreGuard starts on port **8888** by default.

## Step 2 — Open the UI

Navigate to <http://localhost:8888>. On a fresh install the **setup wizard**
appears automatically.

## Step 3 — Create an admin account

Enter an email address and a password. This becomes the first admin user and
cannot be deleted from the UI.

## Step 4 — Register a gateway

Click **Register Gateway** and provide:

- A unique name (e.g. `lab-gpu-01`)
- The gateway endpoint (e.g. `1.2.3.4:8443`)
- The mTLS client certificate and key if the gateway requires mutual TLS

ShoreGuard tests the connection before saving.

## Step 5 — Create a sandbox

Open the **Sandbox Wizard** and walk through the steps:

1. Select a container image
2. Choose compute providers
3. Pick policy presets (e.g. `pypi`, `huggingface`)
4. Launch the sandbox

## Step 6 — Monitor logs

Once the sandbox is running, click its name to open the real-time log view.
Logs stream over WebSocket and are stored for later review.

---

!!! tip "Local mode"

    If you do not have a remote gateway yet, start ShoreGuard with the
    `--local` flag. This enables Docker-based gateway lifecycle management
    on your machine — handy for development and demos.

    ```bash
    shoreguard --local
    ```
