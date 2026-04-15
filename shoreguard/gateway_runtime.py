"""Gateway runtime taxonomy for ShoreGuard.

Upstream NVIDIA/OpenShell supports three gateway backends today — the
default Docker-host variant, a Kubernetes compute driver (PR #817/#839),
and the new ``libkrun`` microVM gateway (PR #611). The gRPC wire format
is identical across all three, so ShoreGuard does not need a typed
polymorphic model in the database. Instead this module defines a
convention: operators tag a gateway's runtime via
``metadata.runtime = "<runtime>"`` at registration time and ShoreGuard
validates, surfaces, and filters on that tag uniformly.

Keeping the list closed (strict validator) protects against typos that
would otherwise silently fragment filter buckets — "libkrun" vs
"libKrun" vs "krun" would appear as three separate runtimes without it.
"""

from __future__ import annotations

from typing import Any

#: Docker-backed gateway (OpenShell default).
GATEWAY_RUNTIME_DOCKER = "docker"

#: Kubernetes compute-driver gateway.
GATEWAY_RUNTIME_KUBERNETES = "kubernetes"

#: libkrun microVM gateway introduced in upstream PR #611.
GATEWAY_RUNTIME_LIBKRUN = "libkrun"

#: Closed set of runtimes ShoreGuard recognises for registration-time
#: validation, filtering, and audit logging.
KNOWN_RUNTIMES: frozenset[str] = frozenset(
    {
        GATEWAY_RUNTIME_DOCKER,
        GATEWAY_RUNTIME_KUBERNETES,
        GATEWAY_RUNTIME_LIBKRUN,
    }
)

#: Canonical metadata key under which the runtime tag is persisted.
METADATA_RUNTIME_KEY = "runtime"


def get_runtime(metadata: dict[str, Any] | None) -> str | None:
    """Extract the runtime tag from a gateway metadata dict.

    Args:
        metadata: Gateway metadata dict as returned by the registry
            (may be ``None`` or an empty dict).

    Returns:
        str | None: The runtime string if present and non-empty,
            otherwise ``None``.
    """
    if not metadata:
        return None
    value = metadata.get(METADATA_RUNTIME_KEY)
    if isinstance(value, str) and value:
        return value
    return None


def validate_runtime(value: Any) -> str:
    """Validate a runtime tag against :data:`KNOWN_RUNTIMES`.

    Called from the registration request validator so typos (``libKrun``,
    ``krun``) are rejected with a clear error instead of silently
    splintering filter buckets downstream.

    Args:
        value: Candidate runtime value as received from the API client.

    Returns:
        str: The validated runtime string, always lowercase.

    Raises:
        ValueError: If *value* is not a string or is not in the known set.
    """
    if not isinstance(value, str) or not value:
        msg = "runtime must be a non-empty string"
        raise ValueError(msg)
    normalized = value.lower()
    if normalized not in KNOWN_RUNTIMES:
        known = ", ".join(sorted(KNOWN_RUNTIMES))
        msg = f"runtime {value!r} is not recognised; known runtimes: {known}"
        raise ValueError(msg)
    return normalized
