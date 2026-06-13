"""Estimated-dollar overlay for request-count metering.

OpenShell exposes no token/usage RPC and its L7 proxy strips usage
metadata, so ShoreGuard meters inference *request counts* only (see
:mod:`shoreguard.services.budgets`). This pure helper turns those counts
into an **estimated** dollar figure using the operator-configured price
table (:class:`shoreguard.settings.PricingSettings`). It performs no I/O
and no gRPC, so budgets, the digest, and any future provider-failover
logic can all reuse it. The figure is honestly "estimated": until an
upstream usage RPC lands there is no token-accurate cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shoreguard.settings import PricingSettings


def estimate_cost(requests: int, provider_type: str | None, settings: PricingSettings) -> float:
    """Estimate the dollar cost of a number of inference requests.

    Args:
        requests: Number of metered inference requests.
        provider_type: The sandbox's provider type, used to look up a
            per-type rate; ``None`` (or an unknown type) falls back to the
            default rate.
        settings: Active pricing settings (price table + default rate).

    Returns:
        float: Estimated cost, or ``0.0`` when pricing is disabled.
    """
    if not settings.enabled:
        return 0.0
    rate = settings.usd_per_request_default
    if provider_type is not None:
        rate = settings.usd_per_request_by_type.get(provider_type, rate)
    return round(requests * rate, 6)


def annotate_rows(rows: list[dict[str, Any]], settings: PricingSettings) -> float:
    """Set ``estimated_cost`` on each usage/summary row and return the total.

    Rows are priced at the default rate (the cross-gateway summary does not
    carry a provider type); callers that know a row's provider type can use
    :func:`estimate_cost` directly. The annotation is additive — it never
    removes the ``requests`` count — and is ``0.0`` when pricing is disabled.

    Args:
        rows: Row dicts each carrying a ``requests`` count.
        settings: Active pricing settings.

    Returns:
        float: Sum of the rows' estimated costs.
    """
    total = 0.0
    for row in rows:
        cost = estimate_cost(int(row.get("requests", 0)), None, settings)
        row["estimated_cost"] = cost
        total += cost
    return round(total, 6)
