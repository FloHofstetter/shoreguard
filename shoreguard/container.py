"""Composition root — builds and owns every application service.

This module is the single place where ShoreGuard's services are
constructed and wired together. The FastAPI lifespan builds one
:class:`ServiceContainer` per process and installs it via
:func:`install`; tests build their own container against in-memory
engines through the same :func:`build_container` code path, so a
service added here is automatically available everywhere.

Import discipline: this module imports services lazily inside
:func:`build_container` so that service modules may import
``shoreguard.container`` at module level without creating cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.orm import Session, sessionmaker

    from shoreguard.services.approval_workflow import ApprovalWorkflowService
    from shoreguard.services.audit import AuditService
    from shoreguard.services.audit_export import AuditExporter
    from shoreguard.services.boot_hooks import BootHookService
    from shoreguard.services.bypass import BypassService
    from shoreguard.services.cert_rotation import CertRotationService
    from shoreguard.services.denial_context import DenialContextService
    from shoreguard.services.discovery import DiscoveryService
    from shoreguard.services.drift_detection import DriftDetectionService
    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.local_gateway import LocalGatewayManager
    from shoreguard.services.operations import AsyncOperationService
    from shoreguard.services.policy_apply_proposal import PolicyApplyProposalService
    from shoreguard.services.policy_pin import PolicyPinService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.services.sandbox_meta import SandboxMetaStore
    from shoreguard.services.sbom import SBOMService
    from shoreguard.services.webhooks import WebhookService
    from shoreguard.settings import Settings


@dataclass
class ServiceContainer:
    """All application services, constructed once per process.

    Attributes:
        settings: The settings snapshot the container was built from.
        session_factory: Sync SQLAlchemy session factory.
        async_session_factory: Async SQLAlchemy session factory.
        registry: Persistent gateway CRUD (what gateways exist).
        gateway: Live gateway connections (clients, health, backoff).
        sandbox_meta: Sandbox metadata store.
        operations: Long-running operation tracking.
        audit: Audit trail service.
        webhooks: Webhook subscription and delivery service.
        bypass: Bypass detection ring buffer.
        sbom: SBOM snapshot service.
        boot_hooks: Sandbox boot hook service.
        discovery: DNS-SRV gateway discovery.
        policy_pin: Policy pin service.
        approval_workflow: Draft policy approval workflow.
        policy_apply_proposal: GitOps apply proposal service.
        drift_detection: Policy drift detection.
        cert_rotation: Proactive client-cert rotation.
        denial_context: Denial context cache.
        local_gateway: Local gateway manager, or ``None`` outside local mode.
    """

    settings: Settings
    session_factory: sessionmaker[Session]
    async_session_factory: async_sessionmaker[AsyncSession]
    registry: GatewayRegistry
    gateway: GatewayService
    sandbox_meta: SandboxMetaStore
    operations: AsyncOperationService
    audit: AuditService
    webhooks: WebhookService
    bypass: BypassService
    sbom: SBOMService
    boot_hooks: BootHookService
    discovery: DiscoveryService
    policy_pin: PolicyPinService
    approval_workflow: ApprovalWorkflowService
    policy_apply_proposal: PolicyApplyProposalService
    drift_detection: DriftDetectionService
    cert_rotation: CertRotationService
    denial_context: DenialContextService
    local_gateway: LocalGatewayManager | None = None


def build_container(
    settings: Settings,
    session_factory: sessionmaker[Session],
    async_session_factory: async_sessionmaker[AsyncSession],
    *,
    audit_exporter: AuditExporter | None = None,
) -> ServiceContainer:
    """Construct every service in dependency order.

    Args:
        settings: Settings snapshot to build from.
        session_factory: Sync SQLAlchemy session factory.
        async_session_factory: Async SQLAlchemy session factory.
        audit_exporter: Optional audit export fan-out (needs a running
            event loop, so the caller constructs it).

    Returns:
        ServiceContainer: The fully wired container. Not yet installed —
        call :func:`install` to make it the process-wide container.
    """
    from shoreguard.services.approval_workflow import ApprovalWorkflowService
    from shoreguard.services.audit import AuditService
    from shoreguard.services.boot_hooks import BootHookService
    from shoreguard.services.bypass import BypassService
    from shoreguard.services.cert_rotation import CertRotationService
    from shoreguard.services.denial_context import DenialContextService
    from shoreguard.services.discovery import DiscoveryService
    from shoreguard.services.drift_detection import DriftDetectionService
    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.local_gateway import LocalGatewayManager
    from shoreguard.services.operations import AsyncOperationService
    from shoreguard.services.policy_apply_proposal import PolicyApplyProposalService
    from shoreguard.services.policy_pin import PolicyPinService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.services.sandbox_meta import SandboxMetaStore
    from shoreguard.services.sbom import SBOMService
    from shoreguard.services.webhooks import WebhookService

    registry = GatewayRegistry(session_factory)
    gateway = GatewayService(registry)
    sandbox_meta = SandboxMetaStore(session_factory)
    audit = AuditService(session_factory, exporter=audit_exporter)

    async def _resolve_sandbox_service(gateway_name: str):  # type: ignore[no-untyped-def]  # noqa: D103
        # Build a SandboxService for post-create boot-hook dispatch.
        from shoreguard.services.sandbox import SandboxService

        client = await gateway.get_client(gateway_name)
        if client is None:
            return None
        return SandboxService(client, meta_store=sandbox_meta, gateway_name=gateway_name)

    return ServiceContainer(
        settings=settings,
        session_factory=session_factory,
        async_session_factory=async_session_factory,
        registry=registry,
        gateway=gateway,
        sandbox_meta=sandbox_meta,
        operations=AsyncOperationService(
            async_session_factory,
            running_ttl=settings.ops.running_ttl,
            retention_days=settings.ops.retention_days,
        ),
        audit=audit,
        webhooks=WebhookService(session_factory),
        bypass=BypassService(),
        sbom=SBOMService(session_factory),
        boot_hooks=BootHookService(
            session_factory,
            sandbox_service_provider=_resolve_sandbox_service,
        ),
        discovery=DiscoveryService(registry, gateway, settings.discovery),
        policy_pin=PolicyPinService(session_factory),
        approval_workflow=ApprovalWorkflowService(session_factory),
        policy_apply_proposal=PolicyApplyProposalService(session_factory),
        drift_detection=DriftDetectionService(gateway, settings.drift_detection),
        cert_rotation=CertRotationService(
            gateway,
            audit,
            threshold_days=settings.cert_rotation.threshold_days,
            max_retries=settings.cert_rotation.max_retries,
        ),
        denial_context=DenialContextService(),
        local_gateway=(LocalGatewayManager(gateway) if settings.server.local_mode else None),
    )


_container: ServiceContainer | None = None


def install(container: ServiceContainer) -> None:
    """Make the given container the process-wide container.

    Args:
        container: The container to install.
    """
    global _container  # noqa: PLW0603
    _container = container


def uninstall() -> None:
    """Clear the process-wide container (lifespan shutdown / test teardown)."""
    global _container  # noqa: PLW0603
    _container = None


def get_container() -> ServiceContainer:
    """Return the installed container.

    Returns:
        ServiceContainer: The process-wide container.

    Raises:
        RuntimeError: If no container is installed (lifespan not started).
    """
    if _container is None:
        raise RuntimeError("ServiceContainer not installed — app lifespan has not started")
    return _container


def try_get_container() -> ServiceContainer | None:
    """Return the installed container, or ``None`` if not installed.

    Returns:
        ServiceContainer | None: The container, or ``None`` outside the
        app lifespan (e.g. import time, some unit tests).
    """
    return _container
