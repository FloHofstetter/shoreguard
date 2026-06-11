"""Detection of OpenAI-compatible inference servers on the local machine.

Solo-dev convenience for local mode: probe the well-known loopback ports of
popular local LLM servers (Ollama, vLLM/NIM, llama.cpp, LM Studio) so the UI
can offer a one-click provider setup instead of asking the operator to know
ports and ``base_url`` conventions by heart.

Only fixed, hardcoded loopback URLs are ever probed — no user input reaches
the probe, so the SSRF checks in :mod:`shoreguard.api.validation` do not
apply here.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# How long a single probe may take. Loopback connection refusals return in
# microseconds; the timeout only matters for a port held by something slow.
_PROBE_TIMEOUT = httpx.Timeout(1.0, connect=0.5)

_MAX_MODELS = 8

# Well-known local inference servers. ``probe_url`` must answer GET with JSON
# when the server is up; ``base_url`` is the OpenAI-compatible endpoint that a
# provider's config should point at.
_KNOWN_SERVERS: tuple[dict[str, str], ...] = (
    {
        "kind": "ollama",
        "label": "Ollama",
        "probe_url": "http://127.0.0.1:11434/api/tags",
        "base_url": "http://127.0.0.1:11434/v1",
        "provider_type": "openai",
        "suggested_name": "ollama-local",
    },
    {
        "kind": "openai-compatible",
        "label": "vLLM / NIM",
        "probe_url": "http://127.0.0.1:8000/v1/models",
        "base_url": "http://127.0.0.1:8000/v1",
        "provider_type": "openai",
        "suggested_name": "vllm-local",
    },
    {
        "kind": "llama.cpp",
        "label": "llama.cpp server",
        "probe_url": "http://127.0.0.1:8080/v1/models",
        "base_url": "http://127.0.0.1:8080/v1",
        "provider_type": "openai",
        "suggested_name": "llamacpp-local",
    },
    {
        "kind": "lmstudio",
        "label": "LM Studio",
        "probe_url": "http://127.0.0.1:1234/v1/models",
        "base_url": "http://127.0.0.1:1234/v1",
        "provider_type": "openai",
        "suggested_name": "lmstudio-local",
    },
)


def _extract_models(payload: Any) -> list[str]:
    """Pull model names out of a probe response, tolerating both API shapes.

    Args:
        payload: Decoded JSON from a probe — either an OpenAI ``/v1/models``
            list (``{"data": [{"id": ...}]}``) or an Ollama ``/api/tags``
            response (``{"models": [{"name": ...}]}``).

    Returns:
        list[str]: Up to ``_MAX_MODELS`` model identifiers (may be empty).
    """
    if not isinstance(payload, dict):
        return []
    entries = payload.get("data") or payload.get("models") or []
    if not isinstance(entries, list):
        return []
    models: list[str] = []
    for entry in entries[:_MAX_MODELS]:
        if isinstance(entry, dict):
            name = entry.get("id") or entry.get("name")
            if isinstance(name, str):
                models.append(name)
    return models


def probe_endpoint(base_url: str) -> dict[str, Any]:
    """Probe an operator-supplied OpenAI-compatible endpoint for models.

    The multi-box homelab counterpart to :func:`detect_local_inference`:
    the Spark serves models for the whole LAN, so the endpoint worth
    testing is often *not* loopback. The probe is restricted to
    **private/LAN addresses** — public endpoints don't need probing and
    allowing them would turn this into a general SSRF primitive. The
    probe is read-only: one GET to ``<base>/models`` (with an Ollama
    ``/api/tags`` fallback), response parsed for model names only.

    Synchronous — call via ``asyncio.to_thread`` from async routes.

    Args:
        base_url: The OpenAI-compatible base URL (e.g.
            ``http://192.168.1.20:11434/v1``).

    Returns:
        dict[str, Any]: ``{"ok", "models", "error"}``.
    """
    from urllib.parse import urlsplit

    from shoreguard.config import is_private_ip

    base_url = base_url.strip().rstrip("/")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        return {"ok": False, "models": [], "error": "URL scheme must be http or https"}
    if not parts.hostname:
        return {"ok": False, "models": [], "error": "URL must include a hostname"}
    if not is_private_ip(parts.hostname):
        return {
            "ok": False,
            "models": [],
            "error": "Probe is for local/LAN endpoints only (private addresses)",
        }

    candidates = [f"{base_url}/models"]
    if base_url.endswith("/v1"):
        # Ollama's native API answers /api/tags even when /v1/models does.
        candidates.append(f"{parts.scheme}://{parts.netloc}/api/tags")

    last_error = "no response"
    with httpx.Client(timeout=httpx.Timeout(3.0, connect=1.5), follow_redirects=False) as client:
        for url in candidates:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                payload = resp.json()
            except (httpx.HTTPError, ValueError) as e:
                last_error = str(e) or e.__class__.__name__
                continue
            return {"ok": True, "models": _extract_models(payload), "error": None}
    return {"ok": False, "models": [], "error": last_error}


def detect_local_inference() -> list[dict[str, Any]]:
    """Probe well-known loopback ports for running local inference servers.

    Synchronous (call via ``asyncio.to_thread`` from async routes). A probe
    counts as a hit only when the port answers with decodable JSON, so a
    random web app on a known port is not misreported as an LLM server.

    Returns:
        list[dict[str, Any]]: One entry per detected server with ``kind``,
            ``label``, ``base_url``, ``provider_type``, ``suggested_name``,
            and ``models`` (possibly empty list of model identifiers).
    """
    detected: list[dict[str, Any]] = []
    with httpx.Client(timeout=_PROBE_TIMEOUT, follow_redirects=False) as client:
        for spec in _KNOWN_SERVERS:
            try:
                resp = client.get(spec["probe_url"])
                resp.raise_for_status()
                payload = resp.json()
            except httpx.HTTPError, ValueError:
                continue
            detected.append(
                {
                    "kind": spec["kind"],
                    "label": spec["label"],
                    "base_url": spec["base_url"],
                    "provider_type": spec["provider_type"],
                    "suggested_name": spec["suggested_name"],
                    "models": _extract_models(payload),
                }
            )
    if detected:
        logger.info(
            "Detected local inference server(s): %s",
            ", ".join(d["label"] for d in detected),
        )
    return detected
