#!/usr/bin/env python3
"""Regenerate Python gRPC stubs from OpenShell .proto definitions.

Shoreguard communicates with the OpenShell gateway over gRPC.  The message
and service definitions live in the upstream NVIDIA/OpenShell repository
(https://github.com/NVIDIA/OpenShell) under ``proto/``.

This script fetches the .proto files — either from a Git tag / branch /
commit on GitHub or from a local directory — compiles them into Python
stubs, fixes the generated imports so they work as a package-internal
submodule, regenerates ``__init__.py``, and validates the result.

Typical usage::

    # Latest release tag (default: fetches the newest vX.Y.Z tag)
    uv run python scripts/generate_proto.py

    # Pin to a specific version
    uv run python scripts/generate_proto.py --ref v0.0.12

    # Use a local checkout instead of cloning
    uv run python scripts/generate_proto.py --local /home/user/OpenShell/proto
"""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from grpc_tools import protoc
except ImportError:
    sys.exit(
        "grpc_tools is not installed.  "
        "Install it with:  uv sync  (it is a dev dependency in pyproject.toml)"
    )

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
STUB_DIR = REPO_ROOT / "shoreguard" / "client" / "_proto"
OPENSHELL_REPO = "https://github.com/NVIDIA/OpenShell.git"

# test.proto is an OpenShell-internal testing helper — not part of the API.
SKIP_PROTOS = {"test.proto"}

# Proto modules whose names may appear as bare ``import <name>_pb2`` in the
# generated code.  We rewrite those to relative imports so the stubs work
# inside the ``shoreguard.client._proto`` package.
_PROTO_MODULES = {"datamodel", "inference", "openshell", "sandbox"}
_IMPORT_RE = re.compile(
    r"^(import (" + "|".join(sorted(_PROTO_MODULES)) + r")_pb2\b)",
    re.MULTILINE,
)

log = logging.getLogger("generate_proto")

# ---------------------------------------------------------------------------
# Proto source resolution
# ---------------------------------------------------------------------------


def _resolve_git_ref(ref: str | None) -> str:
    """Return the concrete Git ref to check out.

    If *ref* is ``None`` the latest ``v*`` tag is queried via ``git ls-remote``.
    """
    if ref is not None:
        return ref

    log.info("Querying latest release tag from %s …", OPENSHELL_REPO)
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--sort=-v:refname", OPENSHELL_REPO],
        capture_output=True,
        text=True,
        check=True,
    )

    for line in result.stdout.splitlines():
        tag = line.split("refs/tags/")[-1]
        # Skip dereferenced tag objects (^{}) and non-version tags
        if tag.endswith("^{}") or not tag.startswith("v"):
            continue
        log.info("Resolved latest tag: %s", tag)
        return tag

    sys.exit("Could not determine latest release tag.  Specify --ref explicitly.")


def _fetch_protos_git(ref: str, target: Path) -> Path:
    """Shallow-clone the OpenShell repo at *ref* into *target*.

    Returns the path to the ``proto/`` directory inside the clone.
    """
    log.info("Cloning %s @ %s …", OPENSHELL_REPO, ref)
    subprocess.run(
        [
            "git",
            "clone",
            "--depth=1",
            f"--branch={ref}",
            "--single-branch",
            "--filter=blob:none",
            OPENSHELL_REPO,
            str(target / "OpenShell"),
        ],
        check=True,
    )
    proto_dir = target / "OpenShell" / "proto"
    if not proto_dir.is_dir():
        sys.exit(f"Cloned repo does not contain proto/ directory at {proto_dir}")
    return proto_dir


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------


def _clean_stub_dir() -> None:
    """Remove previously generated files but keep ``__init__.py``."""
    for pattern in ("*_pb2*.py", "*_pb2*.pyi"):
        for f in STUB_DIR.glob(pattern):
            f.unlink()
    pycache = STUB_DIR / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)


def _compile_protos(proto_dir: Path) -> list[Path]:
    """Run ``protoc`` and return the list of generated files."""
    proto_files = sorted(p for p in proto_dir.glob("*.proto") if p.name not in SKIP_PROTOS)
    if not proto_files:
        sys.exit(f"No .proto files found in {proto_dir}")

    log.info("Compiling: %s", ", ".join(p.name for p in proto_files))

    rc = protoc.main(
        [
            "",  # argv[0] placeholder required by protoc
            f"--proto_path={proto_dir}",
            f"--python_out={STUB_DIR}",
            f"--grpc_python_out={STUB_DIR}",
            f"--pyi_out={STUB_DIR}",
            *(str(p) for p in proto_files),
        ]
    )

    if rc != 0:
        sys.exit(f"protoc exited with code {rc}")

    return sorted(STUB_DIR.glob("*_pb2*"))


def _fix_imports(generated: list[Path]) -> int:
    """Rewrite bare ``import <mod>_pb2`` to ``from . import <mod>_pb2``.

    protoc generates top-level imports because it does not know the files
    will live inside a Python package.  Without this fix, imports fail at
    runtime.

    Returns the number of files that were patched.
    """
    patched = 0
    for path in generated:
        if path.suffix not in (".py", ".pyi"):
            continue
        text = path.read_text()
        new_text = _IMPORT_RE.sub(r"from . \1", text)
        if new_text != text:
            path.write_text(new_text)
            patched += 1
            log.debug("  Patched imports in %s", path.name)
    return patched


def _generate_init(generated: list[Path]) -> None:
    """Regenerate ``__init__.py`` so it exports all compiled modules."""
    modules = sorted({p.stem for p in generated if p.suffix == ".py"})
    lines = [
        '"""Generated protobuf stubs for the OpenShell gRPC API.',
        "",
        "Auto-generated by scripts/generate_proto.py — do not edit manually.",
        '"""',
        "",
    ]
    for mod in modules:
        lines.append(f"from . import {mod}")
    lines.append("")
    lines.append(f"__all__ = {modules!r}")
    lines.append("")

    init_path = STUB_DIR / "__init__.py"
    init_path.write_text("\n".join(lines))
    log.info("Wrote %s with %d modules.", init_path.name, len(modules))


def _validate_imports() -> None:
    """Try importing the generated package to catch obvious breakage early.

    We run the import in a subprocess so it works regardless of whether
    Shoreguard is installed in the current environment.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import shoreguard.client._proto"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**__import__("os").environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    if result.returncode != 0:
        sys.exit(
            f"Validation failed — generated stubs are not importable:\n"
            f"  {result.stderr.strip()}\n"
            "This usually means a protoc version mismatch or a missing dependency."
        )
    log.info("Validation passed — stubs import successfully.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--ref",
        metavar="TAG|BRANCH|SHA",
        default=None,
        help=(
            "Git ref to fetch from NVIDIA/OpenShell.  "
            "Examples: v0.0.12, main, a1b2c3d.  "
            "Defaults to the latest v* release tag."
        ),
    )
    source.add_argument(
        "--local",
        metavar="DIR",
        type=Path,
        default=None,
        help="Path to a local directory containing .proto files (skips cloning).",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the post-generation import validation step.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Fetch, compile, patch, and validate OpenShell proto stubs."""
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    # --- Resolve proto source ---
    tmpdir = None
    if args.local is not None:
        proto_dir = args.local.resolve()
        if not proto_dir.is_dir():
            sys.exit(f"Local directory not found: {proto_dir}")
        log.info("Using local protos: %s", proto_dir)
    else:
        ref = _resolve_git_ref(args.ref)
        tmpdir = tempfile.mkdtemp(prefix="shoreguard-proto-")
        proto_dir = _fetch_protos_git(ref, Path(tmpdir))

    try:
        # --- Generate ---
        _clean_stub_dir()
        generated = _compile_protos(proto_dir)
        log.info("Generated %d files in %s", len(generated), STUB_DIR)

        # --- Post-process ---
        patched = _fix_imports(generated)
        log.info("Patched imports in %d file(s).", patched)

        _generate_init(generated)

        # --- Validate ---
        if not args.skip_validation:
            _validate_imports()

        log.info("Done.")

    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
