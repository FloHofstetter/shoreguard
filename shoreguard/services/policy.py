"""Policy operations including atomic network rule CRUD."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shoreguard.client import ShoreGuardClient
from shoreguard.client._converters import _dict_to_policy
from shoreguard.exceptions import NotFoundError, PolicyError
from shoreguard.presets import get_preset

logger = logging.getLogger("shoreguard")


class PolicyService:
    """Policy management shared by Web UI and TUI.

    Wraps client.policies with higher-level operations like
    atomic add/delete of individual network rules.
    """

    def __init__(self, client: ShoreGuardClient) -> None:
        """Initialize with an OpenShell client."""
        self._client = client

    def get(self, sandbox_name: str) -> dict[str, Any]:
        """Get the current active policy for a sandbox."""
        return self._client.policies.get(sandbox_name)

    def update(self, sandbox_name: str, policy_dict: dict) -> dict[str, Any]:
        """Push a new policy version and return the full PolicyResponse."""
        logger.info("Updating policy for sandbox '%s'", sandbox_name)
        proto_policy = _dict_to_policy(policy_dict)
        self._client.policies.update(sandbox_name, proto_policy)
        return self._client.policies.get(sandbox_name)

    def list_revisions(
        self, sandbox_name: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List policy revision history."""
        return self._client.policies.list_revisions(sandbox_name, limit=limit, offset=offset)

    def apply_preset(self, sandbox_name: str, preset_name: str) -> dict[str, Any]:
        """Apply a policy preset to a sandbox (merges network_policies)."""
        logger.info("Applying preset '%s' to sandbox '%s'", preset_name, sandbox_name)
        preset_data = get_preset(preset_name)
        if not preset_data:
            raise NotFoundError(f"Preset '{preset_name}' not found")

        policy_content = preset_data.get("policy", preset_data)
        preset_rules = policy_content.get("network_policies", {})

        def _merge(policy: dict) -> None:
            policy.setdefault("network_policies", {}).update(preset_rules)

        return self._read_modify_write(sandbox_name, _merge)

    # ── Read-modify-write helper ──────────────────────────────────────────

    def _read_modify_write(self, sandbox_name: str, fn: Callable[[dict], None]) -> dict[str, Any]:
        """Read the current policy, apply fn to it, then write it back."""
        current = self.get(sandbox_name)
        policy = current.get("policy")
        if not policy:
            raise PolicyError(f"Could not read current policy for sandbox {sandbox_name}")
        fn(policy)
        return self.update(sandbox_name, policy)

    # ── Atomic network rule CRUD ──────────────────────────────────────────

    def add_network_rule(self, sandbox_name: str, key: str, rule: dict[str, Any]) -> dict[str, Any]:
        """Add or update a single network rule (read-modify-write)."""
        logger.info("Adding network rule '%s' to sandbox '%s'", key, sandbox_name)

        def _add(policy: dict) -> None:
            policy.setdefault("network_policies", {})[key] = rule

        return self._read_modify_write(sandbox_name, _add)

    def delete_network_rule(self, sandbox_name: str, key: str) -> dict[str, Any]:
        """Delete a single network rule (read-modify-write)."""
        logger.info("Deleting network rule '%s' from sandbox '%s'", key, sandbox_name)

        def _delete(policy: dict) -> None:
            policy.get("network_policies", {}).pop(key, None)

        return self._read_modify_write(sandbox_name, _delete)

    # ── Atomic filesystem path CRUD ──────────────────────────────────────

    def add_filesystem_path(self, sandbox_name: str, path: str, access: str) -> dict[str, Any]:
        """Add a filesystem path (read-modify-write)."""
        logger.info(
            "Adding filesystem path '%s' (access=%s) to sandbox '%s'",
            path,
            access,
            sandbox_name,
        )

        def _add(policy: dict) -> None:
            if "filesystem" not in policy:
                policy["filesystem"] = {"read_only": [], "read_write": [], "include_workdir": False}
            fs = policy["filesystem"]
            # Remove from both lists first to avoid duplicates
            fs["read_only"] = [p for p in fs.get("read_only", []) if p != path]
            fs["read_write"] = [p for p in fs.get("read_write", []) if p != path]
            if access == "rw":
                fs["read_write"].append(path)
            else:
                fs["read_only"].append(path)

        return self._read_modify_write(sandbox_name, _add)

    def delete_filesystem_path(self, sandbox_name: str, path: str) -> dict[str, Any]:
        """Delete a filesystem path (read-modify-write)."""
        logger.info("Deleting filesystem path '%s' from sandbox '%s'", path, sandbox_name)

        def _delete(policy: dict) -> None:
            if "filesystem" in policy:
                fs = policy["filesystem"]
                fs["read_only"] = [p for p in fs.get("read_only", []) if p != path]
                fs["read_write"] = [p for p in fs.get("read_write", []) if p != path]

        return self._read_modify_write(sandbox_name, _delete)

    # ── Atomic process/landlock update ───────────────────────────────────

    def update_process_policy(
        self,
        sandbox_name: str,
        *,
        run_as_user: str | None = None,
        run_as_group: str | None = None,
        landlock_compatibility: str | None = None,
    ) -> dict[str, Any]:
        """Update process and landlock settings (read-modify-write)."""
        logger.info("Updating process policy for sandbox '%s'", sandbox_name)

        def _update(policy: dict) -> None:
            if "process" not in policy:
                policy["process"] = {}
            if run_as_user is not None:
                policy["process"]["run_as_user"] = run_as_user
            if run_as_group is not None:
                policy["process"]["run_as_group"] = run_as_group
            if landlock_compatibility is not None:
                if "landlock" not in policy:
                    policy["landlock"] = {}
                policy["landlock"]["compatibility"] = landlock_compatibility

        return self._read_modify_write(sandbox_name, _update)
