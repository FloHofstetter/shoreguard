"""REST endpoints for Z3-backed policy verification.

Thin wrapper around
:class:`~shoreguard.services.prover.ProverService` that lets
operators run the built-in verification templates against a
sandbox's active policy: ``can_exfiltrate``,
``unrestricted_egress``, ``binary_bypass``,
``write_despite_readonly``. Each call returns SAT with a witness
model (the property fails and here is why) or UNSAT (the
property holds for every assignment).

The service owns the translation from policy dict to Z3
constraints plus the query templates; this module handles auth,
request validation, audit logging, and response shaping.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from shoreguard.api.auth import require_role
from shoreguard.api.deps import get_client, get_gateway_name, get_services
from shoreguard.client import ShoreGuardClient
from shoreguard.services.audit import audit_log
from shoreguard.services.policy import PolicyService

if TYPE_CHECKING:
    from shoreguard.services.prover import ProverService

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / response models (route-local, not in schemas.py)
# ---------------------------------------------------------------------------


class VerifyQueryItem(BaseModel):
    """A single verification query.

    Attributes:
        query_id (str): Preset query ID (e.g. ``"can_exfiltrate"``).
        params (dict[str, Any]): Query-specific parameters.
    """

    query_id: str = Field(description="Preset query ID (e.g. 'can_exfiltrate')")
    params: dict[str, Any] = Field(default_factory=dict)


class VerifyRequest(BaseModel):
    """Request body for POST /policy/verify.

    Attributes:
        queries (list[VerifyQueryItem]): List of queries to run (1-10).
    """

    queries: list[VerifyQueryItem] = Field(min_length=1, max_length=10)


class SimulateRequest(BaseModel):
    """Request body for POST /policy/simulate.

    Attributes:
        candidate_policy (dict | None): Candidate policy to replay against;
            ``None`` uses the sandbox's current active policy.
    """

    candidate_policy: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _get_policy_service(client: ShoreGuardClient = Depends(get_client)) -> PolicyService:
    """Build a PolicyService from the injected client.

    Args:
        client: gRPC client for the active gateway.

    Returns:
        PolicyService: Service instance bound to the client.
    """
    return PolicyService(client)


def _get_prover_service() -> ProverService:
    """Build a ProverService with settings from the application config.

    Returns:
        ProverService: A fresh prover instance.

    Raises:
        HTTPException: If the prover feature is disabled.
    """
    from shoreguard.settings import get_settings

    settings = get_settings()
    if not settings.prover.enabled:
        raise HTTPException(503, "Policy prover is disabled")
    return ProverService(timeout_ms=settings.prover.timeout_ms)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/{name}/policy/verify",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def verify_policy(
    name: str,
    body: VerifyRequest,
    gw: str = Depends(get_gateway_name),
    policy_svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Run formal verification queries against a sandbox policy.

    Fetches the current policy from the gateway, encodes it into Z3
    constraints, and checks each query for satisfiability.

    Args:
        name: Sandbox name.
        body: Verification request with query list.
        gw: Gateway name from URL path.
        policy_svc: Injected PolicyService.

    Returns:
        dict[str, Any]: ``{results: [...], total_time_ms: float}``
    """
    import time

    prover_svc = _get_prover_service()

    # Fetch current policy from gateway
    policy_data = await policy_svc.get(name)
    policy = policy_data.get("policy", {})

    queries = [{"query_id": q.query_id, "params": q.params} for q in body.queries]

    t0 = time.perf_counter()
    results = await asyncio.to_thread(prover_svc.verify_policy, policy, queries)
    total_ms = (time.perf_counter() - t0) * 1000

    return {
        "results": results,
        "total_time_ms": round(total_ms, 2),
    }


@router.post(
    "/{name}/policy/simulate",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def simulate_policy(
    name: str,
    body: SimulateRequest,
    request: Request,
    gw: str = Depends(get_gateway_name),
    policy_svc: PolicyService = Depends(_get_policy_service),
) -> dict[str, Any]:
    """Replay the persisted denial corpus against a candidate policy.

    Best-effort denial replay: for each previously-denied request, predicts
    whether the candidate policy (or the current active policy when none is
    supplied) would now allow it. ``allow`` means the candidate would unblock
    that request; ``deny`` means it stays blocked. Deterministic Z3 evaluation
    that may diverge from the live gateway matcher — labelled best-effort.

    Args:
        name: Sandbox name.
        body: Simulate request with an optional candidate policy.
        request: Incoming HTTP request (for audit).
        gw: Gateway name from URL path.
        policy_svc: Injected PolicyService.

    Returns:
        dict[str, Any]: ``{results, summary, best_effort: true}``.

    Raises:
        HTTPException: 503 if denial replay (or the prover) is disabled.
    """
    import time

    from shoreguard.settings import get_settings

    settings = get_settings()
    if not settings.simulator.replay_enabled:
        raise HTTPException(503, "Denial replay is disabled (SHOREGUARD_SIMULATOR_REPLAY_ENABLED)")
    prover_svc = _get_prover_service()

    policy = body.candidate_policy
    if policy is None:
        policy_data = await policy_svc.get(name)
        policy = policy_data.get("policy", {})

    samples = await get_services().denial_store.list_for_sandbox(
        gw, name, limit=settings.simulator.max_replay_samples
    )
    # Expand each denial sample into concrete (host, port, method, path) requests.
    requests: list[dict[str, Any]] = []
    for s in samples:
        l7 = s.get("l7") or []
        if l7:
            for entry in l7:
                requests.append(
                    {
                        "binary": s["binary"],
                        "host": s["host"],
                        "port": s["port"],
                        "method": entry.get("method", ""),
                        "path": entry.get("path", ""),
                    }
                )
        else:
            requests.append(
                {
                    "binary": s["binary"],
                    "host": s["host"],
                    "port": s["port"],
                    "method": "",
                    "path": "",
                }
            )

    t0 = time.perf_counter()
    results = await asyncio.to_thread(prover_svc.replay_denials, policy, requests)
    total_ms = (time.perf_counter() - t0) * 1000

    would_allow = sum(1 for r in results if r["predicted_decision"] == "allow")
    still_deny = sum(1 for r in results if r["predicted_decision"] == "deny")
    await audit_log(
        request,
        "policy.simulate",
        "policy",
        name,
        gateway=gw,
        detail={"samples": len(requests), "would_allow": would_allow, "still_deny": still_deny},
    )
    return {
        "results": results,
        "summary": {
            "total": len(requests),
            "would_allow": would_allow,
            "still_deny": still_deny,
        },
        "best_effort": True,
        "total_time_ms": round(total_ms, 2),
    }


@router.get(
    "/{name}/policy/verify/presets",
    dependencies=[Depends(require_role("viewer"))],
    response_model=None,
)
async def list_verify_presets(
    name: str,
    gw: str = Depends(get_gateway_name),
) -> list[dict[str, Any]]:
    """List available preset verification queries.

    Args:
        name: Sandbox name (unused, kept for URL consistency).
        gw: Gateway name from URL path.

    Returns:
        list[dict[str, Any]]: Preset query descriptors.
    """
    from shoreguard.services.prover_queries import PRESET_QUERIES

    presets = []
    for query_id, spec in PRESET_QUERIES.items():
        presets.append(
            {
                "query_id": query_id,
                "label": spec["label"],
                "description": spec["description"],
                "params": {
                    k: {kk: vv for kk, vv in v.items() if kk != "fn"}
                    for k, v in spec.get("params", {}).items()
                },
            }
        )
    return presets
