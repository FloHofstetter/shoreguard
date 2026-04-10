"""Shoreguard — Open source control plane for NVIDIA OpenShell."""

import os
from importlib.metadata import version

__version__ = version("shoreguard")

# Build identity — populated by Dockerfile ARGs at image build time.
# Defaults to "unknown" for local dev runs where the envs are not set.
__git_sha__ = os.environ.get("SHOREGUARD_GIT_SHA", "unknown")
__build_time__ = os.environ.get("SHOREGUARD_BUILD_TIME", "unknown")
