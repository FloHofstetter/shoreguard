"""Z3-based formal verification engine for OpenShell sandbox policies.

The :class:`ProverService` translates a policy dict (as returned by
:meth:`PolicyService.get`) into Z3 constraints and checks user-supplied
security queries against them.  Each query asks: *"Does there exist an
assignment of (binary, host, port, ...) that the policy PERMITS but that
VIOLATES a desired security property?"*  If Z3 returns SAT the policy has
a vulnerability and the model provides a concrete counterexample.
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

import z3

from .prover_queries import (
    PRESET_QUERIES,
    FsVars,
    NetVars,
)

logger = logging.getLogger(__name__)


class ProverResult(TypedDict):
    """Result of a single verification query.

    Attributes:
        query (str): Human-readable query description.
        query_id (str): Machine identifier (e.g. ``"can_exfiltrate"``).
        satisfiable (bool): ``True`` if a vulnerability was found.
        counterexample (dict[str, Any] | None): Concrete values if SAT.
        z3_time_ms (float): Solver wall-clock time in milliseconds.
        verdict (str): ``"SAFE"``, ``"VULNERABLE"``, ``"TIMEOUT"``, or ``"ERROR"``.
    """

    query: str
    query_id: str
    satisfiable: bool
    counterexample: dict[str, Any] | None
    z3_time_ms: float
    verdict: str


class ProverService:
    """Stateless Z3 policy prover.

    Each :meth:`verify_policy` call creates a fresh solver, encodes the
    policy, runs the requested queries, and returns results.  Thread-safe
    because no mutable state is shared across calls.

    Args:
        timeout_ms: Per-query Z3 solver timeout in milliseconds.
    """

    def __init__(self, *, timeout_ms: int = 5000) -> None:  # noqa: D107
        self.timeout_ms = timeout_ms

    def verify_policy(
        self,
        policy: dict[str, Any],
        queries: list[dict[str, Any]],
    ) -> list[ProverResult]:
        """Run a list of verification queries against a policy.

        Args:
            policy: Policy dict (the ``"policy"`` key from ``PolicyService.get()``).
            queries: List of query specs, each with ``query_id`` and optional ``params``.

        Returns:
            list[ProverResult]: One result per query.
        """
        results: list[ProverResult] = []
        for q in queries:
            query_id = q.get("query_id", "")
            params = q.get("params", {})
            results.append(self._run_query(policy, query_id, params))
        return results

    def _run_query(
        self,
        policy: dict[str, Any],
        query_id: str,
        params: dict[str, Any],
    ) -> ProverResult:
        """Execute a single query and return the result.

        Args:
            policy: Policy dict.
            query_id: Preset query identifier.
            params: Query-specific parameters.

        Returns:
            ProverResult: Verification result with verdict and optional counterexample.
        """
        preset = PRESET_QUERIES.get(query_id)
        if preset is None:
            return ProverResult(
                query=f"Unknown query: {query_id}",
                query_id=query_id,
                satisfiable=False,
                counterexample=None,
                z3_time_ms=0.0,
                verdict="ERROR",
            )

        try:
            v = NetVars()
            formula, description = preset["fn"](policy, params, v)
        except (ValueError, KeyError, TypeError) as exc:
            return ProverResult(
                query=str(exc),
                query_id=query_id,
                satisfiable=False,
                counterexample=None,
                z3_time_ms=0.0,
                verdict="ERROR",
            )

        # Determine which variables to extract based on query type
        is_fs_query = query_id == "write_despite_readonly"

        solver = z3.Solver()
        solver.set("timeout", self.timeout_ms)
        solver.add(formula)

        t0 = time.perf_counter()
        result = solver.check()
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if result == z3.sat:
            model = solver.model()
            if is_fs_query:
                counterexample = self._extract_fs_counterexample(model)
            else:
                counterexample = self._extract_net_counterexample(model, v)
            return ProverResult(
                query=description,
                query_id=query_id,
                satisfiable=True,
                counterexample=counterexample,
                z3_time_ms=round(elapsed_ms, 2),
                verdict="VULNERABLE",
            )
        if result == z3.unsat:
            return ProverResult(
                query=description,
                query_id=query_id,
                satisfiable=False,
                counterexample=None,
                z3_time_ms=round(elapsed_ms, 2),
                verdict="SAFE",
            )
        # z3.unknown — typically timeout
        return ProverResult(
            query=description,
            query_id=query_id,
            satisfiable=False,
            counterexample=None,
            z3_time_ms=round(elapsed_ms, 2),
            verdict="TIMEOUT",
        )

    @staticmethod
    def _extract_net_counterexample(
        model: z3.ModelRef,
        v: NetVars,
    ) -> dict[str, Any]:
        """Extract concrete values from a SAT network model.

        Args:
            model: Z3 model from a SAT result.
            v: Z3 network variables to evaluate.

        Returns:
            dict[str, Any]: Counterexample with binary, host, port, etc.
        """
        ce: dict[str, Any] = {}
        for name, var in [
            ("binary", v.binary),
            ("host", v.host),
            ("protocol", v.protocol),
            ("method", v.method),
            ("path", v.path),
            ("matched_rule", v.rule_tag),
        ]:
            val = model.eval(var, model_completion=True)
            ce[name] = val.as_string() if hasattr(val, "as_string") else str(val)  # pyright: ignore[reportAttributeAccessIssue]

        port_val = model.eval(v.port, model_completion=True)
        ce["port"] = port_val.as_long() if hasattr(port_val, "as_long") else str(port_val)  # pyright: ignore[reportAttributeAccessIssue]
        return ce

    @staticmethod
    def _extract_fs_counterexample(model: z3.ModelRef) -> dict[str, Any]:
        """Extract concrete values from a SAT filesystem model.

        Args:
            model: Z3 model from a SAT result.

        Returns:
            dict[str, Any]: Counterexample with fs_path and access type.
        """
        fsv = FsVars()
        val = model.eval(fsv.fs_path, model_completion=True)
        return {
            "fs_path": val.as_string() if hasattr(val, "as_string") else str(val),  # pyright: ignore[reportAttributeAccessIssue]
            "access": "write",
        }
