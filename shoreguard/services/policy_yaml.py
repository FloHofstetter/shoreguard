"""Deterministic YAML round-trip for sandbox policies (M23 GitOps)."""

from __future__ import annotations

import datetime
import hashlib
from typing import Any

import yaml


class PolicyYamlError(ValueError):
    """Raised when a YAML payload is malformed or missing the policy block."""


def render_yaml(
    policy: dict[str, Any],
    *,
    gateway: str,
    sandbox: str,
    version: int | None = None,
    policy_hash: str | None = None,
    exported_at: datetime.datetime | None = None,
) -> str:
    """Serialise a policy dict to deterministic YAML with a metadata header.

    Args:
        policy: Policy dict (as produced by ``_policy_to_dict``).
        gateway: Gateway name to embed in metadata.
        sandbox: Sandbox name to embed in metadata.
        version: Optional active policy version.
        policy_hash: Optional OpenShell-computed policy hash (etag).
        exported_at: Optional export timestamp (defaults to ``datetime.now(UTC)``).

    Returns:
        str: YAML document with ``metadata`` + ``policy`` blocks.
    """
    metadata: dict[str, Any] = {"gateway": gateway, "sandbox": sandbox}
    if version is not None:
        metadata["version"] = version
    if policy_hash:
        metadata["policy_hash"] = policy_hash
    metadata["exported_at"] = (exported_at or datetime.datetime.now(datetime.UTC)).isoformat()

    document = {"metadata": metadata, "policy": policy}
    body = yaml.safe_dump(document, sort_keys=True, default_flow_style=False)
    return "# managed-by: shoreguard-gitops\n" + body


def parse_yaml(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Parse a YAML policy document into ``(policy, metadata)``.

    Args:
        text: YAML document body.

    Returns:
        tuple[dict[str, Any], dict[str, Any]]: ``(policy_dict, metadata_dict)``.
            Metadata is ``{}`` if absent.

    Raises:
        PolicyYamlError: If YAML is malformed or the ``policy`` key is missing.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyYamlError(f"Malformed YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PolicyYamlError("Top-level YAML must be a mapping")
    if "policy" not in loaded:
        raise PolicyYamlError("Missing required 'policy' key")
    policy = loaded["policy"]
    if not isinstance(policy, dict):
        raise PolicyYamlError("'policy' must be a mapping")
    metadata = loaded.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise PolicyYamlError("'metadata' must be a mapping")
    return policy, metadata


def yaml_fingerprint(text: str) -> str:
    """Return a stable 16-char sha256 hex prefix of a YAML body.

    Used as the synthetic chunk-id for apply proposals under M19 workflows.

    Args:
        text: YAML document body.

    Returns:
        str: First 16 hex characters of ``sha256(text)``.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
