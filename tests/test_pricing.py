"""Tests for the estimated-dollar pricing overlay helper."""

from __future__ import annotations

from shoreguard.services.pricing import annotate_rows, estimate_cost
from shoreguard.settings import PricingSettings


def test_estimate_cost_disabled_is_zero() -> None:
    settings = PricingSettings(enabled=False, usd_per_request_default=0.01)
    assert estimate_cost(100, None, settings) == 0.0


def test_estimate_cost_default_rate() -> None:
    settings = PricingSettings(enabled=True, usd_per_request_default=0.01)
    assert estimate_cost(100, None, settings) == 1.0
    assert estimate_cost(0, None, settings) == 0.0


def test_estimate_cost_per_type_override_and_fallback() -> None:
    settings = PricingSettings(
        enabled=True,
        usd_per_request_default=0.01,
        usd_per_request_by_type={"openai": 0.05},
    )
    assert estimate_cost(10, "openai", settings) == 0.5
    # Unknown type falls back to the default rate.
    assert estimate_cost(10, "ollama", settings) == 0.1
    # None falls back to the default rate.
    assert estimate_cost(10, None, settings) == 0.1


def test_annotate_rows_sets_estimated_cost_and_returns_total() -> None:
    settings = PricingSettings(enabled=True, usd_per_request_default=0.01)
    rows = [{"requests": 100}, {"requests": 50}]
    total = annotate_rows(rows, settings)
    assert rows[0]["estimated_cost"] == 1.0
    assert rows[1]["estimated_cost"] == 0.5
    assert total == 1.5


def test_annotate_rows_disabled_is_zero() -> None:
    settings = PricingSettings(enabled=False, usd_per_request_default=0.01)
    rows = [{"requests": 100}]
    total = annotate_rows(rows, settings)
    assert rows[0]["estimated_cost"] == 0.0
    assert total == 0.0
