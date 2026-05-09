"""gRPC wrapper for OpenShell's provider-profile registry RPCs.

Provider profiles are reusable, gateway-managed templates that describe
how a provider type (e.g. ``claude``, ``gitlab``) wires up credentials,
network endpoints, and binaries — they are the schema source for the
provider-creation form. The registry was added upstream in
`NVIDIA/OpenShell#1170 <https://github.com/NVIDIA/OpenShell/pull/1170>`_
alongside the ``providers_v2_enabled`` gateway setting.

Imports go through a lint-then-apply cycle so callers can preview
diagnostics before mutating state.
"""

from __future__ import annotations

from typing import Any

from ._proto import openshell_pb2, openshell_pb2_grpc

# Map upstream ``ProviderProfileCategory`` enum integer to its slug. Kept
# narrow on purpose: every category we know about is listed; an unseen
# category number falls back to its raw int so newer gateways don't break
# the client.
_CATEGORY_SLUGS = {
    0: "unspecified",
    1: "other",
    2: "inference",
    3: "agent",
    4: "source_control",
    5: "messaging",
    6: "data",
    7: "knowledge",
}


def _credential_to_dict(cred: Any) -> dict[str, Any]:
    return {
        "name": cred.name,
        "description": cred.description,
        "env_vars": list(cred.env_vars),
        "required": bool(cred.required),
        "auth_style": cred.auth_style,
        "header_name": cred.header_name,
        "query_param": cred.query_param,
    }


def _profile_to_dict(profile: Any) -> dict[str, Any]:
    """Convert a ``ProviderProfile`` protobuf to a plain dict.

    Args:
        profile: ``openshell.gateway.v1.ProviderProfile`` protobuf message.

    Returns:
        dict[str, Any]: Flat dict with the slim fields that ShoreGuard
            REST/UI consumers need (``id``, ``display_name``,
            ``description``, ``category`` slug, ``credentials``,
            ``endpoint_count``, ``binary_count``, ``inference_capable``).
    """
    return {
        "id": profile.id,
        "display_name": profile.display_name,
        "description": profile.description,
        "category": _CATEGORY_SLUGS.get(profile.category, profile.category),
        "credentials": [_credential_to_dict(c) for c in profile.credentials],
        "endpoint_count": len(profile.endpoints),
        "binary_count": len(profile.binaries),
        "inference_capable": bool(profile.inference_capable),
    }


def _diagnostic_to_dict(diag: Any) -> dict[str, Any]:
    return {
        "source": diag.source,
        "profile_id": diag.profile_id,
        "field": diag.field,
        "message": diag.message,
        "severity": diag.severity,
    }


class ProviderProfileManager:
    """ProviderProfile registry CRUD against OpenShell gateway.

    Args:
        stub: OpenShell gRPC stub.
        timeout: gRPC call timeout in seconds.
    """

    def __init__(self, stub: openshell_pb2_grpc.OpenShellStub, *, timeout: float = 30.0) -> None:  # noqa: D107
        self._stub = stub
        self._timeout = timeout

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """List provider profiles registered on the gateway.

        Args:
            limit: Maximum number of profiles to return.
            offset: Pagination offset.

        Returns:
            list[dict[str, Any]]: Profile dicts.
        """
        resp = self._stub.ListProviderProfiles(
            openshell_pb2.ListProviderProfilesRequest(limit=limit, offset=offset),
            timeout=self._timeout,
        )
        return [_profile_to_dict(p) for p in resp.profiles]

    def get(self, profile_id: str) -> dict[str, Any]:
        """Fetch a single profile by ID.

        Args:
            profile_id: Profile ID (e.g. ``"claude"``).

        Returns:
            dict[str, Any]: Profile dict.
        """
        resp = self._stub.GetProviderProfile(
            openshell_pb2.GetProviderProfileRequest(id=profile_id),
            timeout=self._timeout,
        )
        return _profile_to_dict(resp.profile)

    def lint(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate a batch of profiles without mutating state.

        Args:
            items: List of ``{"profile": <ProviderProfile dict>,
                "source": <origin label>}`` records. Only the slim subset
                of fields exposed by ``_profile_to_dict`` (plus optional
                ``credentials``) is honoured by the wire protocol — the
                remaining fields are validated by the gateway, which
                reports diagnostics back.

        Returns:
            dict[str, Any]: ``{"valid": bool, "diagnostics": list}``.
        """
        request = openshell_pb2.LintProviderProfilesRequest(
            profiles=[_dict_to_import_item(it) for it in items]
        )
        resp = self._stub.LintProviderProfiles(request, timeout=self._timeout)
        return {
            "valid": bool(resp.valid),
            "diagnostics": [_diagnostic_to_dict(d) for d in resp.diagnostics],
        }

    def import_(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Import a batch of profiles after validation.

        Args:
            items: Same shape as :meth:`lint`.

        Returns:
            dict[str, Any]: ``{"imported": bool, "profiles": list,
                "diagnostics": list}``. ``imported`` is False when the
                request was rejected as a whole; ``diagnostics`` carry
                per-profile errors.
        """
        request = openshell_pb2.ImportProviderProfilesRequest(
            profiles=[_dict_to_import_item(it) for it in items]
        )
        resp = self._stub.ImportProviderProfiles(request, timeout=self._timeout)
        return {
            "imported": bool(resp.imported),
            "profiles": [_profile_to_dict(p) for p in resp.profiles],
            "diagnostics": [_diagnostic_to_dict(d) for d in resp.diagnostics],
        }

    def delete(self, profile_id: str) -> bool:
        """Delete a custom profile by ID.

        Args:
            profile_id: Profile ID to delete.

        Returns:
            bool: True if the profile was deleted.
        """
        resp = self._stub.DeleteProviderProfile(
            openshell_pb2.DeleteProviderProfileRequest(id=profile_id),
            timeout=self._timeout,
        )
        return bool(resp.deleted)


def _dict_to_import_item(item: dict[str, Any]) -> openshell_pb2.ProviderProfileImportItem:
    """Convert a public dict to an ``ImportItem`` protobuf.

    Only the subset of ``ProviderProfile`` fields that callers need at
    import-time is wired through. The gateway is the source of truth for
    schema and rejects anything ill-shaped.

    Args:
        item: Public dict of the form
            ``{"profile": <ProviderProfile dict>, "source": <str>}``.

    Returns:
        openshell_pb2.ProviderProfileImportItem: Populated import-item
            protobuf.
    """
    profile_dict = item.get("profile") or {}
    profile_proto = openshell_pb2.ProviderProfile(
        id=profile_dict.get("id", ""),
        display_name=profile_dict.get("display_name", ""),
        description=profile_dict.get("description", ""),
        inference_capable=bool(profile_dict.get("inference_capable", False)),
    )
    return openshell_pb2.ProviderProfileImportItem(
        profile=profile_proto,
        source=item.get("source", ""),
    )
