"""Unit tests for L7 request-path canonicalization.

Parity with upstream NVIDIA/OpenShell PR #878 (commit ``c960d480``).
"""

from __future__ import annotations

import pytest
import z3  # pyright: ignore[reportMissingTypeStubs]

from shoreguard.services.prover_queries import (
    canonicalize_request_path,
    encode_path_match,
)

# ---------------------------------------------------------------------------
# canonicalize_request_path: table-driven parity tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Identity — already canonical.
        ("/api/foo", "/api/foo"),
        ("/", "/"),
        # Dot-segment resolution.
        ("/api/./foo", "/api/foo"),
        ("/api/foo/.", "/api/foo"),
        ("/./api/foo", "/api/foo"),
        ("/api/../foo", "/foo"),
        ("/api/foo/../bar", "/api/bar"),
        # `..` clamps at root (prover is permissive for patterns; the
        # enforcement path rejects with 400, which is stricter).
        ("/../secret", "/secret"),
        ("/../../../etc/passwd", "/etc/passwd"),
        # Double- and triple-slash collapse.
        ("/api//foo", "/api/foo"),
        ("/api///foo", "/api/foo"),
        ("//api/foo", "/api/foo"),
        # Trailing slash / glob suffix markers are NOT touched here
        # (encode_path_match handles glob grammar separately).
        ("/api/foo/", "/api/foo"),
        # Percent-decoding of unreserved bytes.
        ("/api/%66oo", "/api/foo"),  # %66 == 'f'
        ("/api/%2Dbar", "/api/-bar"),  # %2D == '-'
        # Mixed-case %HH normalizes to uppercase and survives.
        ("/api/%3abar", "/api/%3Abar"),  # %3A == ':' (reserved, preserved)
        ("/api/%3Abar", "/api/%3Abar"),
        # `;params` strip per RFC 3986 / Tomcat ACL bypass mitigation.
        ("/api/foo;jsessionid=abc", "/api/foo"),
        ("/api;v=2/foo;x=1", "/api/foo"),
        # Query and fragment stripped.
        ("/api/foo?x=1&y=2", "/api/foo"),
        ("/api/foo#frag", "/api/foo"),
        ("/api/foo?q=1#frag", "/api/foo"),
        # Empty / missing leading slash.
        ("api/foo", "/api/foo"),
        # Combined mess.
        ("//api/./foo/..//bar/;p=1", "/api/bar"),
    ],
)
def test_canonicalize_strict_mode(raw: str, expected: str) -> None:
    """Strict mode (allow_encoded_slash=False) decodes %2F to /."""
    assert canonicalize_request_path(raw) == expected


def test_canonicalize_empty_input_preserved() -> None:
    """Empty string is preserved — callers treat it as 'no path constraint'."""
    assert canonicalize_request_path("") == ""


# ---------------------------------------------------------------------------
# %2F handling — depends on allow_encoded_slash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/repos/a%2Fb", "/repos/a%2Fb"),
        ("/repos/a%2fb", "/repos/a%2Fb"),  # uppercased on emit
        ("/repos/mygroup%2Fmyrepo/merge_requests", "/repos/mygroup%2Fmyrepo/merge_requests"),
        # Combined with other encodings and dot segments.
        ("/api/./repos/a%2Fb/../c%2Fd", "/api/repos/c%2Fd"),
    ],
)
def test_canonicalize_allow_encoded_slash_preserves_2f(raw: str, expected: str) -> None:
    assert canonicalize_request_path(raw, allow_encoded_slash=True) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Without the opt-in, %2F decodes to '/' and participates in collapse.
        ("/repos/a%2Fb", "/repos/a/b"),
        ("/repos/a%2fb", "/repos/a/b"),
        # %2F-adjacent dot-segments still resolve.
        ("/api/%2F.%2Fsecret", "/api/secret"),
    ],
)
def test_canonicalize_without_opt_in_decodes_2f(raw: str, expected: str) -> None:
    assert canonicalize_request_path(raw) == expected


# ---------------------------------------------------------------------------
# encode_path_match: non-canonical patterns normalize to canonical Z3 encoding
# ---------------------------------------------------------------------------


def _request_matches(
    pattern: str, concrete_path: str, *, allow_encoded_slash: bool = False
) -> bool:
    """Solve the prover's pattern constraint against a concrete request path.

    Returns ``True`` when the path satisfies the pattern constraint.
    """
    path_var = z3.String("path")
    solver = z3.Solver()
    solver.add(encode_path_match(pattern, path_var, allow_encoded_slash=allow_encoded_slash))
    solver.add(path_var == z3.StringVal(concrete_path))
    return solver.check() == z3.sat


@pytest.mark.parametrize(
    "pattern_variant",
    [
        "/api/foo",
        "/api/./foo",
        "/api//foo",
        "/api/%66oo",
        "//api/./foo",
        "/./api/foo",
    ],
)
def test_encode_path_match_non_canonical_patterns_accept_canonical_request(
    pattern_variant: str,
) -> None:
    """All canonically-equal patterns must match the same concrete path."""
    assert _request_matches(pattern_variant, "/api/foo") is True


def test_encode_path_match_dot_dot_pattern_resolves() -> None:
    """`/api/../admin` as a pattern is `/admin`, not `/api/admin`."""
    assert _request_matches("/api/../admin", "/admin") is True
    assert _request_matches("/api/../admin", "/api/admin") is False


def test_encode_path_match_glob_suffix_survives_canonicalization() -> None:
    """`/api/**` still does prefix matching after pattern canonicalization."""
    assert _request_matches("/api/**", "/api/x/y/z") is True
    assert _request_matches("/api/**", "/api") is True
    assert _request_matches("/api/**", "/other/x") is False


def test_encode_path_match_single_glob_constrains_one_segment() -> None:
    """`/api/*` matches single segment, not deeper."""
    assert _request_matches("/api/*", "/api/foo") is True
    assert _request_matches("/api/*", "/api/foo/bar") is False


def test_encode_path_match_encoded_slash_opt_in_preserves_literal() -> None:
    """With allow_encoded_slash, `%2F` in a request stays literal and
    does not collapse to a slash that would satisfy a broader pattern."""
    # Pattern and request both carry the literal %2F — match succeeds.
    assert _request_matches("/repos/a%2Fb", "/repos/a%2Fb", allow_encoded_slash=True) is True
    # Pattern carries literal %2F, request has a real slash — no match
    # (they are NOT equivalent under allow_encoded_slash).
    assert _request_matches("/repos/a%2Fb", "/repos/a/b", allow_encoded_slash=True) is False


def test_encode_path_match_encoded_slash_strict_mode_collapses() -> None:
    """In strict mode, `%2F` in the request canonicalizes to `/` —
    so the prover patterns that were also written with %2F collapse to
    the equivalent slash form."""
    # Pattern written with %2F in strict mode becomes `/repos/a/b`, so a
    # concrete path `/repos/a/b` matches it.
    assert _request_matches("/repos/a%2Fb", "/repos/a/b") is True
