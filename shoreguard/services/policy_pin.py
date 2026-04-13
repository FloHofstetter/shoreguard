"""Persistent sandbox policy pins for change-freeze scenarios.

A pin freezes the active policy version of a sandbox. While a
pin is in effect, every policy-write endpoint and both approve
actions raise :class:`~shoreguard.exceptions.PolicyLockedError`,
which the route layer translates into HTTP 423. Read endpoints
(``GET /policy``, ``GET /policy/export``) are unaffected so
callers can still inspect the frozen state.

Pins are upserted per ``(gateway, sandbox)``, carry an optional
free-text reason and an optional expiry. Auto-expiry is checked
on every ``get`` / ``check`` call rather than via a background
loop — a stale row is harmless until someone tries to act on it.
"""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from shoreguard.exceptions import PolicyLockedError
from shoreguard.models import PolicyPin

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

# Module-level singleton — set during app lifespan (see shoreguard.api.main).
policy_pin_service: PolicyPinService | None = None


class PolicyPinService:
    """Database-backed CRUD and enforcement of policy pins.

    Owns the ``policy_pins`` table. Exposes ``pin`` / ``unpin`` /
    ``get`` / ``check`` — the latter is the one write paths call
    before every mutation to surface a clean HTTP 423 via
    :class:`~shoreguard.exceptions.PolicyLockedError`. Expired
    pins are removed lazily on read.

    Args:
        session_factory: SQLAlchemy session factory used to open
            short-lived sessions for each call.
    """

    def __init__(self, session_factory: SessionMaker) -> None:  # noqa: D107
        self._session_factory = session_factory

    def pin(
        self,
        gateway_name: str,
        sandbox_name: str,
        version: int,
        actor: str,
        *,
        reason: str | None = None,
        expires_at: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        """Pin a sandbox's policy at a specific version.

        Upserts: if a pin already exists for this gateway/sandbox, it is
        replaced with the new version/reason/expiry.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to pin.
            version: Policy version to lock at.
            actor: Identity of the user setting the pin.
            reason: Optional human-readable reason.
            expires_at: Optional expiry timestamp.

        Returns:
            dict[str, Any]: The created/updated pin as a dict.
        """
        with self._session_factory() as session:
            existing = self._get_pin_row(session, gateway_name, sandbox_name)
            now = datetime.datetime.now(datetime.UTC)

            if existing is not None:
                existing.pinned_version = version
                existing.pinned_by = actor
                existing.reason = reason
                existing.pinned_at = now
                existing.expires_at = expires_at
                session.commit()
                session.refresh(existing)
                return self._pin_to_dict(existing)

            pin = PolicyPin(
                gateway_name=gateway_name,
                sandbox_name=sandbox_name,
                pinned_version=version,
                pinned_by=actor,
                reason=reason,
                pinned_at=now,
                expires_at=expires_at,
            )
            session.add(pin)
            session.commit()
            session.refresh(pin)
            return self._pin_to_dict(pin)

    def unpin(self, gateway_name: str, sandbox_name: str) -> bool:
        """Remove a policy pin.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to unpin.

        Returns:
            bool: True if a pin was removed, False if no pin existed.
        """
        with self._session_factory() as session:
            result = session.execute(
                delete(PolicyPin).where(
                    PolicyPin.gateway_name == gateway_name,
                    PolicyPin.sandbox_name == sandbox_name,
                )
            )
            session.commit()
            return result.rowcount > 0  # type: ignore[union-attr]

    def get_pin(self, gateway_name: str, sandbox_name: str) -> dict[str, Any] | None:
        """Get the active pin for a sandbox, or None if not pinned.

        Expired pins are automatically deleted and treated as absent.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            dict[str, Any] | None: Pin data or None.
        """
        with self._session_factory() as session:
            pin = self._get_pin_row(session, gateway_name, sandbox_name)
            if pin is None:
                return None

            # Auto-expire (SQLite returns naive datetimes, so compare as UTC)
            if pin.expires_at is not None:
                expires = pin.expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=datetime.UTC)
                if expires <= datetime.datetime.now(datetime.UTC):
                    session.delete(pin)
                    session.commit()
                    logger.info(
                        "Auto-expired policy pin (gw=%s, sandbox=%s)",
                        gateway_name,
                        sandbox_name,
                    )
                    return None

            return self._pin_to_dict(pin)

    def is_pinned(self, gateway_name: str, sandbox_name: str) -> bool:
        """Check whether a sandbox's policy is currently pinned.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to check.

        Returns:
            bool: True if an active (non-expired) pin exists.
        """
        return self.get_pin(gateway_name, sandbox_name) is not None

    def check_pin(self, gateway_name: str, sandbox_name: str) -> None:
        """Raise PolicyLockedError if the sandbox's policy is pinned.

        Intended as a guard call before any policy-mutating operation.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to check.

        Raises:
            PolicyLockedError: If an active pin exists.
        """
        pin = self.get_pin(gateway_name, sandbox_name)
        if pin is not None:
            raise PolicyLockedError(
                f"Policy is pinned at version {pin['pinned_version']} "
                f"by {pin['pinned_by']}. Unpin first."
            )

    @staticmethod
    def _get_pin_row(session: Session, gateway_name: str, sandbox_name: str) -> PolicyPin | None:
        """Fetch the raw PolicyPin row, if any.

        Args:
            session: Active SQLAlchemy session.
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            PolicyPin | None: The pin row, or None if not found.
        """
        return session.execute(
            select(PolicyPin).where(
                PolicyPin.gateway_name == gateway_name,
                PolicyPin.sandbox_name == sandbox_name,
            )
        ).scalar_one_or_none()

    @staticmethod
    def _pin_to_dict(pin: PolicyPin) -> dict[str, Any]:
        """Convert a PolicyPin ORM instance to a plain dict.

        Args:
            pin: The PolicyPin ORM instance.

        Returns:
            dict[str, Any]: Pin data as a plain dict.
        """
        return {
            "gateway_name": pin.gateway_name,
            "sandbox_name": pin.sandbox_name,
            "pinned_version": pin.pinned_version,
            "pinned_by": pin.pinned_by,
            "reason": pin.reason,
            "pinned_at": pin.pinned_at.isoformat(),
            "expires_at": pin.expires_at.isoformat() if pin.expires_at else None,
        }
