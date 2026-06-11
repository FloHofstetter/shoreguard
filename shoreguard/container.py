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

    from shoreguard.services.approval_workflow import ApprovalWorkflowService
    from shoreguard.services.audit import AuditService
    from shoreguard.services.audit_export import AuditExporter
    from shoreguard.services.boot_hooks import BootHookService
    from shoreguard.services.budgets import BudgetService
    from shoreguard.services.bypass import BypassService
    from shoreguard.services.cert_rotation import CertRotationService
    from shoreguard.services.curfew import CurfewService
    from shoreguard.services.denial_context import DenialContextService
    from shoreguard.services.digest import DigestService
    from shoreguard.services.discovery import DiscoveryService
    from shoreguard.services.drift_detection import DriftDetectionService
    from shoreguard.services.fleet import FleetService
    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.kill_switch import KillSwitchService
    from shoreguard.services.local_gateway import LocalGatewayManager
    from shoreguard.services.node_alerts import NodeAlertService
    from shoreguard.services.node_stats import NodeStatsService
    from shoreguard.services.operations import AsyncOperationService
    from shoreguard.services.policy_apply_proposal import PolicyApplyProposalService
    from shoreguard.services.policy_pin import PolicyPinService
    from shoreguard.services.push import PushService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.services.sandbox_meta import SandboxMetaStore
    from shoreguard.services.sbom import SBOMService
    from shoreguard.services.timeline import TimelineService
    from shoreguard.services.update_check import UpdateCheckService
    from shoreguard.services.webhooks import WebhookService
    from shoreguard.settings import Settings


@dataclass
class ServiceContainer:
    """All application services, constructed once per process.

    Attributes:
        settings: The settings snapshot the container was built from.
        async_session_factory: Async SQLAlchemy session factory (all data services).
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
        kill_switch: Reversible per-gateway provider kill switch.
        curfew: Quiet-hours schedules driving the kill switch.
        digest: Daily activity digest builder/dispatcher.
        budget: Inference usage metering and per-sandbox budgets.
        node_stats: Host resource stats for the ShoreGuard machine.
        node_alerts: Threshold alerts over the host node-stats sample.
        push: Web Push subscriptions and delivery (PWA notifications).
        update_check: Release update checks and gateway version skew.
        timeline: Per-sandbox merged activity timeline.
        fleet: Cross-gateway overview, drift, and policy sync.
        denial_context: Denial context cache.
        local_gateway: Local gateway manager, or ``None`` outside local mode.
    """

    settings: Settings
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
    kill_switch: KillSwitchService
    curfew: CurfewService
    digest: DigestService
    budget: BudgetService
    node_stats: NodeStatsService
    node_alerts: NodeAlertService
    push: PushService
    update_check: UpdateCheckService
    timeline: TimelineService
    fleet: FleetService
    denial_context: DenialContextService
    local_gateway: LocalGatewayManager | None = None


def build_container(
    settings: Settings,
    async_session_factory: async_sessionmaker[AsyncSession],
    *,
    audit_exporter: AuditExporter | None = None,
) -> ServiceContainer:
    """Construct every service in dependency order.

    Args:
        settings: Settings snapshot to build from.
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
    from shoreguard.services.budgets import BudgetService
    from shoreguard.services.bypass import BypassService
    from shoreguard.services.cert_rotation import CertRotationService
    from shoreguard.services.curfew import CurfewService
    from shoreguard.services.denial_context import DenialContextService
    from shoreguard.services.digest import DigestService
    from shoreguard.services.discovery import DiscoveryService
    from shoreguard.services.drift_detection import DriftDetectionService
    from shoreguard.services.fleet import FleetService
    from shoreguard.services.gateway import GatewayService
    from shoreguard.services.kill_switch import KillSwitchService
    from shoreguard.services.local_gateway import LocalGatewayManager
    from shoreguard.services.node_alerts import NodeAlertService
    from shoreguard.services.node_stats import NodeStatsService
    from shoreguard.services.operations import AsyncOperationService
    from shoreguard.services.policy_apply_proposal import PolicyApplyProposalService
    from shoreguard.services.policy_pin import PolicyPinService
    from shoreguard.services.push import PushService
    from shoreguard.services.registry import GatewayRegistry
    from shoreguard.services.sandbox_meta import SandboxMetaStore
    from shoreguard.services.sbom import SBOMService
    from shoreguard.services.timeline import TimelineService
    from shoreguard.services.update_check import UpdateCheckService
    from shoreguard.services.webhooks import WebhookService

    registry = GatewayRegistry(async_session_factory)
    gateway = GatewayService(registry)
    sandbox_meta = SandboxMetaStore(async_session_factory)
    audit = AuditService(async_session_factory, exporter=audit_exporter)
    node_stats = NodeStatsService()
    kill_switch = KillSwitchService(async_session_factory, gateway)

    async def _resolve_sandbox_service(gateway_name: str):  # type: ignore[no-untyped-def]  # noqa: D103
        # Build a SandboxService for post-create boot-hook dispatch.
        from shoreguard.services.sandbox import SandboxService

        client = await gateway.get_client(gateway_name)
        if client is None:
            return None
        return SandboxService(client, meta_store=sandbox_meta, gateway_name=gateway_name)

    return ServiceContainer(
        settings=settings,
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
        webhooks=WebhookService(async_session_factory),
        bypass=BypassService(),
        sbom=SBOMService(async_session_factory),
        boot_hooks=BootHookService(
            async_session_factory,
            sandbox_service_provider=_resolve_sandbox_service,
        ),
        discovery=DiscoveryService(registry, gateway, settings.discovery),
        policy_pin=PolicyPinService(async_session_factory),
        approval_workflow=ApprovalWorkflowService(async_session_factory),
        policy_apply_proposal=PolicyApplyProposalService(async_session_factory),
        drift_detection=DriftDetectionService(gateway, settings.drift_detection),
        cert_rotation=CertRotationService(
            gateway,
            audit,
            threshold_days=settings.cert_rotation.threshold_days,
            max_retries=settings.cert_rotation.max_retries,
        ),
        kill_switch=kill_switch,
        curfew=CurfewService(async_session_factory, kill_switch),
        digest=DigestService(async_session_factory, registry),
        budget=BudgetService(async_session_factory, gateway, registry, settings.budget),
        node_stats=node_stats,
        node_alerts=NodeAlertService(node_stats, settings.node_alert),
        push=PushService(async_session_factory, settings.push),
        update_check=UpdateCheckService(gateway, settings.updates),
        timeline=TimelineService(async_session_factory),
        fleet=FleetService(registry, gateway),
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
