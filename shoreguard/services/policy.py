"""High-level sandbox policy operations for UI and API callers.

Wraps the gRPC ``ShoreGuardClient.policies`` surface with
additional behavior the raw client does not offer: atomic
add/delete of individual network rules and filesystem paths over
a read-modify-write loop, eager preset resolution into the stored
policy so the gateway's effective view matches the declared
document, and submit-time capture of denial context so the
approval detail modal can render binary ancestry and L7 samples
without a round-trip.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shoreguard.client import ShoreGuardClient
from shoreguard.client._converters import _dict_to_policy
from shoreguard.exceptions import NotFoundError, PolicyError
from shoreguard.presets import get_preset

logger = logging.getLogger(__name__)


class PolicyService:
    """Policy management shared by the Web UI, TUI, and REST API.

    Thin wrapper over ``ShoreGuardClient.policies`` that lifts a
    few operations up from the raw gRPC surface: atomic single-rule
    CRUD (against an API that only exposes replace-whole-policy),
    preset application with server-side merge, and denial-context
    capture for approvals. Everything is synchronous because the
    gRPC client is synchronous; callers that need to avoid blocking
    the event loop wrap individual methods in ``asyncio.to_thread``.

    Args:
        client: OpenShell gRPC client scoped to the target gateway.
    """

    def __init__(self, client: ShoreGuardClient) -> None:  # noqa: D107
        self._client = client

    def get(self, sandbox_name: str) -> dict[str, Any]:
        """Get the current active policy for a sandbox.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            dict[str, Any]: Policy data.
        """
        return self._client.policies.get(sandbox_name)

    def get_effective(self, sandbox_name: str) -> dict[str, Any]:
        """Get the effective policy — what the gateway currently enforces.

        In the current architecture, presets are merged eagerly into the
        declared policy at apply time (read-modify-write in
        :meth:`apply_preset`), so the gateway's stored policy is already
        the fully resolved document. This method returns that document
        under a stable ``/policy/effective`` contract, giving the UI and
        API clients a dedicated endpoint that will keep working if
        OpenShell ever separates declared from effective server-side.

        The return value is the same envelope ``policies.get`` returns
        (``active_version``, ``revision``, ``policy``) with an added
        ``source: "gateway_runtime"`` marker so callers can distinguish
        this response from the plain ``GET /policy`` route.

        Args:
            sandbox_name: Name of the sandbox.

        Returns:
            dict[str, Any]: Effective policy envelope.
        """
        result = self._client.policies.get(sandbox_name)
        if isinstance(result, dict):
            return {**result, "source": "gateway_runtime"}
        return {"policy": result, "source": "gateway_runtime"}

    def submit_analysis(
        self,
        sandbox_name: str,
        *,
        summaries: list[dict[str, Any]],
        proposed_chunks: list[dict[str, Any]],
        analysis_mode: str = "",
    ) -> dict[str, Any]:
        """Forward a policy analysis submission to the gateway.

        Thin pass-through to :meth:`PolicyManager.submit_analysis`. The
        gateway merges accepted chunks into the draft policy; rejected
        chunks come back with a per-chunk reason. Used by external
        analyzers (LLM-backed or rule-based) that observe sandbox denials
        and propose fixes — ShoreGuard itself does not generate analysis
        results, it only brokers them.

        Args:
            sandbox_name: Target sandbox name.
            summaries: ``DenialSummary`` dicts. See the OpenShell proto
                for the field layout.
            proposed_chunks: ``PolicyChunk`` dicts containing the rules
                that would fix the denials described in *summaries*.
            analysis_mode: Opaque mode tag forwarded verbatim, e.g.
                ``"auto"`` or ``"manual"``.

        Returns:
            dict[str, Any]: ``{"accepted_chunks": int, "rejected_chunks":
            int, "rejection_reasons": list[str]}``.
        """
        logger.info(
            "Submitting policy analysis for sandbox '%s' (%d summaries, %d chunks, mode=%r)",
            sandbox_name,
            len(summaries),
            len(proposed_chunks),
            analysis_mode,
        )

        # Cache denial summaries so the approval detail modal can
        # render binary ancestry + L7 samples without a round-trip.
        from shoreguard.services.denial_context import denial_context_service

        if denial_context_service is not None:
            denial_context_service.ingest_summaries(sandbox_name, summaries)

        return self._client.policies.submit_analysis(
            sandbox_name,
            summaries=summaries,
            proposed_chunks=proposed_chunks,
            analysis_mode=analysis_mode,
        )

    def update(self, sandbox_name: str, policy_dict: dict) -> dict[str, Any]:
        """Push a new policy version and return the full PolicyResponse.

        Args:
            sandbox_name: Name of the sandbox.
            policy_dict: Policy content as a dict.

        Returns:
            dict[str, Any]: Updated policy response.
        """
        logger.info("Updating policy for sandbox '%s'", sandbox_name)
        proto_policy = _dict_to_policy(policy_dict)
        self._client.policies.update(sandbox_name, proto_policy)
        return self._client.policies.get(sandbox_name)

    def get_version(self, sandbox_name: str, version: int) -> dict[str, Any]:
        """Get a specific policy revision by version number.

        Args:
            sandbox_name: Name of the sandbox.
            version: Revision version number.

        Returns:
            dict[str, Any]: Policy revision data.
        """
        return self._client.policies.get_version(sandbox_name, version)

    def diff_revisions(self, sandbox_name: str, version_a: int, version_b: int) -> dict[str, Any]:
        """Fetch two revisions and return both for client-side diffing.

        Args:
            sandbox_name: Name of the sandbox.
            version_a: First revision version number.
            version_b: Second revision version number.

        Returns:
            dict[str, Any]: Both policy revisions for comparison.
        """
        rev_a = self.get_version(sandbox_name, version_a)
        rev_b = self.get_version(sandbox_name, version_b)
        return {
            "version_a": version_a,
            "version_b": version_b,
            "policy_a": rev_a.get("policy"),
            "policy_b": rev_b.get("policy"),
            "revision_a": rev_a.get("revision"),
            "revision_b": rev_b.get("revision"),
        }

    def list_revisions(
        self, sandbox_name: str, *, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List policy revision history.

        Args:
            sandbox_name: Name of the sandbox.
            limit: Maximum number of revisions to return.
            offset: Number of revisions to skip.

        Returns:
            list[dict[str, Any]]: Revision history entries.
        """
        return self._client.policies.list_revisions(sandbox_name, limit=limit, offset=offset)

    def apply_preset(self, sandbox_name: str, preset_name: str) -> dict[str, Any]:
        """Apply a policy preset to a sandbox (merges network_policies).

        Args:
            sandbox_name: Name of the sandbox.
            preset_name: Name of the preset to apply.

        Returns:
            dict[str, Any]: Updated policy response.

        Raises:
            NotFoundError: If the preset name is not found.
        """
        logger.info("Applying preset '%s' to sandbox '%s'", preset_name, sandbox_name)
        preset_data = get_preset(preset_name)
        if not preset_data:
            raise NotFoundError(f"Preset '{preset_name}' not found")

        policy_content = preset_data.get("policy", preset_data)
        preset_rules = policy_content.get("network_policies", {})

        def _merge(policy: dict) -> None:
            """Merge preset network rules into the policy.

            Args:
                policy: Policy dict to modify in place.
            """
            policy.setdefault("network_policies", {}).update(preset_rules)

        return self._read_modify_write(sandbox_name, _merge)

    # ── Read-modify-write helper ──────────────────────────────────────────

    def _read_modify_write(self, sandbox_name: str, fn: Callable[[dict], None]) -> dict[str, Any]:
        """Read the current policy, apply fn to it, then write it back.

        Args:
            sandbox_name: Name of the sandbox.
            fn: Mutation function applied to the policy dict.

        Returns:
            dict[str, Any]: Updated policy response.

        Raises:
            PolicyError: If the current policy cannot be read.
        """
        current = self.get(sandbox_name)
        policy = current.get("policy")
        if not policy:
            raise PolicyError(f"Could not read current policy for sandbox {sandbox_name}")
        fn(policy)
        return self.update(sandbox_name, policy)

    # ── Atomic network rule CRUD ──────────────────────────────────────────

    def add_network_rule(self, sandbox_name: str, key: str, rule: dict[str, Any]) -> dict[str, Any]:
        """Add or update a single network rule (read-modify-write).

        Args:
            sandbox_name: Name of the sandbox.
            key: Network rule key.
            rule: Rule definition dict.

        Returns:
            dict[str, Any]: Updated policy response.
        """
        logger.info("Adding network rule '%s' to sandbox '%s'", key, sandbox_name)

        def _add(policy: dict) -> None:
            """Insert or replace the network rule in the policy.

            Args:
                policy: Policy dict to modify in place.
            """
            policy.setdefault("network_policies", {})[key] = rule

        return self._read_modify_write(sandbox_name, _add)

    def delete_network_rule(self, sandbox_name: str, key: str) -> dict[str, Any]:
        """Delete a single network rule (read-modify-write).

        Args:
            sandbox_name: Name of the sandbox.
            key: Network rule key to remove.

        Returns:
            dict[str, Any]: Updated policy response.
        """
        logger.info("Deleting network rule '%s' from sandbox '%s'", key, sandbox_name)

        def _delete(policy: dict) -> None:
            """Remove the network rule from the policy.

            Args:
                policy: Policy dict to modify in place.
            """
            policy.get("network_policies", {}).pop(key, None)

        return self._read_modify_write(sandbox_name, _delete)

    # ── Atomic filesystem path CRUD ──────────────────────────────────────

    def add_filesystem_path(self, sandbox_name: str, path: str, access: str) -> dict[str, Any]:
        """Add a filesystem path (read-modify-write).

        Args:
            sandbox_name: Name of the sandbox.
            path: Filesystem path to add.
            access: Access mode ("ro" or "rw").

        Returns:
            dict[str, Any]: Updated policy response.
        """
        logger.info(
            "Adding filesystem path '%s' (access=%s) to sandbox '%s'",
            path,
            access,
            sandbox_name,
        )

        def _add(policy: dict) -> None:
            """Add the filesystem path to the appropriate access list.

            Args:
                policy: Policy dict to modify in place.
            """
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
        """Delete a filesystem path (read-modify-write).

        Args:
            sandbox_name: Name of the sandbox.
            path: Filesystem path to remove.

        Returns:
            dict[str, Any]: Updated policy response.
        """
        logger.info("Deleting filesystem path '%s' from sandbox '%s'", path, sandbox_name)

        def _delete(policy: dict) -> None:
            """Remove the filesystem path from both access lists.

            Args:
                policy: Policy dict to modify in place.
            """
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
        """Update process and landlock settings (read-modify-write).

        Args:
            sandbox_name: Name of the sandbox.
            run_as_user: User to run processes as.
            run_as_group: Group to run processes as.
            landlock_compatibility: Landlock compatibility level.

        Returns:
            dict[str, Any]: Updated policy response.
        """
        logger.info("Updating process policy for sandbox '%s'", sandbox_name)

        def _update(policy: dict) -> None:
            """Apply process and landlock settings to the policy.

            Args:
                policy: Policy dict to modify in place.
            """
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
