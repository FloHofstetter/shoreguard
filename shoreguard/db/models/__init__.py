"""All persistent models, re-exported from their domain modules."""

from shoreguard.db.models.audit import AuditEntry
from shoreguard.db.models.auth import (
    Group,
    GroupGatewayRole,
    GroupMember,
    ServicePrincipal,
    SPGatewayRole,
    User,
    UserGatewayRole,
)
from shoreguard.db.models.base import Base
from shoreguard.db.models.gateway import Gateway, KillSwitchEntry
from shoreguard.db.models.operations import OperationRecord
from shoreguard.db.models.policy import (
    ApprovalDecision,
    ApprovalWorkflow,
    PolicyApplyProposal,
    PolicyPin,
)
from shoreguard.db.models.sandbox import (
    SandboxBootHook,
    SandboxBudget,
    SandboxMeta,
    SandboxUsage,
    UsageCursor,
)
from shoreguard.db.models.sbom import SBOMComponent, SBOMSnapshot
from shoreguard.db.models.webhooks import Webhook, WebhookDelivery

__all__ = (
    "ApprovalDecision",
    "ApprovalWorkflow",
    "AuditEntry",
    "Base",
    "Gateway",
    "Group",
    "GroupGatewayRole",
    "GroupMember",
    "KillSwitchEntry",
    "OperationRecord",
    "PolicyApplyProposal",
    "PolicyPin",
    "SBOMComponent",
    "SBOMSnapshot",
    "SPGatewayRole",
    "SandboxBootHook",
    "SandboxBudget",
    "SandboxMeta",
    "SandboxUsage",
    "ServicePrincipal",
    "UsageCursor",
    "User",
    "UserGatewayRole",
    "Webhook",
    "WebhookDelivery",
)
