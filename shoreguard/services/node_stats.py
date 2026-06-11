"""Host resource stats for the machine running ShoreGuard.

On the homelab single-box (a DGX Spark running gateway + ShoreGuard
side by side) the host IS the gateway node, and on GB10's unified
memory architecture host RAM *is* GPU memory — so host-level stats
answer "is my box melting / out of memory?" without any gateway-side
support. GPU metrics come from ``nvidia-smi`` when present (works on
DGX OS out of the box); CPU/memory/disk come from the OS.

OpenShell's gRPC surface has no node-resources RPC yet, so this is
explicitly scoped to the ShoreGuard host: the API response carries
``scope: shoreguard-host`` and the UI labels it "this machine". When
upstream grows a node-stats RPC, per-remote-gateway resources hang off
the same dashboard card.

Samples are cached for a few seconds so the dashboard can poll freely.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NVIDIA_SMI_QUERY = "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu"


def _read_meminfo() -> dict[str, int]:
    """Parse ``/proc/meminfo`` into a kB-valued dict.

    Returns:
        dict[str, int]: Field name → value in kB (empty off-Linux).
    """
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                try:
                    result[key.strip()] = int(parts[0])
                except ValueError:
                    continue
    except OSError:  # pragma: no cover — non-Linux platform
        pass
    return result


def parse_nvidia_smi_csv(output: str) -> list[dict[str, Any]]:
    """Parse ``nvidia-smi --format=csv,noheader,nounits`` output.

    Args:
        output: Raw stdout from the nvidia-smi query.

    Returns:
        list[dict[str, Any]]: One dict per GPU with name, utilisation,
            memory, and temperature (fields ``None`` when unparsable).
    """

    def _num(value: str) -> float | None:
        try:
            return float(value.strip())
        except ValueError:
            return None

    gpus: list[dict[str, Any]] = []
    for line in output.strip().splitlines():
        fields = [f.strip() for f in line.split(",")]
        if len(fields) < 5:
            continue
        gpus.append(
            {
                "name": fields[0],
                "utilization_pct": _num(fields[1]),
                "memory_used_mb": _num(fields[2]),
                "memory_total_mb": _num(fields[3]),
                "temperature_c": _num(fields[4]),
            }
        )
    return gpus


def _collect_sync() -> dict[str, Any]:
    """Gather one host stats sample (blocking; run off the event loop).

    Returns:
        dict[str, Any]: CPU, memory, disk, and GPU stats.
    """
    stats: dict[str, Any] = {"scope": "shoreguard-host"}

    try:
        load1, load5, load15 = os.getloadavg()
        stats["cpu"] = {
            "count": os.cpu_count() or 0,
            "load_1m": round(load1, 2),
            "load_5m": round(load5, 2),
            "load_15m": round(load15, 2),
        }
    except OSError:  # pragma: no cover — platform without getloadavg
        stats["cpu"] = None

    meminfo = _read_meminfo()
    if meminfo.get("MemTotal"):
        total_kb = meminfo["MemTotal"]
        available_kb = meminfo.get("MemAvailable", 0)
        stats["memory"] = {
            "total_mb": total_kb // 1024,
            "available_mb": available_kb // 1024,
            "used_pct": round(100 * (1 - available_kb / total_kb), 1),
        }
    else:
        stats["memory"] = None

    try:
        usage = shutil.disk_usage("/")
        stats["disk"] = {
            "total_gb": round(usage.total / 1e9, 1),
            "free_gb": round(usage.free / 1e9, 1),
            "used_pct": round(100 * usage.used / usage.total, 1),
        }
    except OSError:  # pragma: no cover
        stats["disk"] = None

    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            proc = subprocess.run(  # nosec B603
                [smi, _NVIDIA_SMI_QUERY, "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            stats["gpus"] = parse_nvidia_smi_csv(proc.stdout) if proc.returncode == 0 else []
        except OSError, subprocess.TimeoutExpired:
            stats["gpus"] = []
    else:
        stats["gpus"] = []

    return stats


class NodeStatsService:
    """Cached host resource sampling for the dashboard.

    Args:
        cache_ttl: Seconds a sample stays fresh before re-collecting.
    """

    def __init__(self, cache_ttl: float = 5.0) -> None:  # noqa: D107
        self._cache_ttl = cache_ttl
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._lock = asyncio.Lock()

    async def collect(self) -> dict[str, Any]:
        """Return the current host stats sample (cached).

        Returns:
            dict[str, Any]: CPU, memory, disk, and GPU stats plus a
                ``sampled_at`` monotonic age marker.
        """
        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self._cache_ttl:
                return self._cached
            stats = await asyncio.to_thread(_collect_sync)
            self._cached = stats
            self._cached_at = time.monotonic()
            return stats
