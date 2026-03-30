# Migrating from v0.2

ShoreGuard v0.3+ uses a database-backed gateway registry instead of the flat
file layout that earlier versions (and the OpenShell CLI) use. A one-shot
import command brings your existing gateways across.

## Usage

```bash
shoreguard import-gateways
```

The command reads gateway configurations from the default OpenShell config
directory:

```
~/.config/openshell/gateways/
```

## What gets imported

- Gateway name and endpoint
- mTLS client certificates and keys
- Additional metadata stored in the gateway config files

## What gets skipped

The importer validates every entry and skips gateways that:

- Have an invalid or empty name
- Point to a private IP address (unless `--local` mode is active)
- Contain oversized certificates
- Are already registered in the ShoreGuard database

Skipped entries are logged with a reason so you can fix them manually if
needed.

## Alternative: register fresh

Migration is entirely optional. You can also register gateways from scratch
through the web UI or the REST API — see the [Quick Start](quickstart.md) for
a walkthrough.
