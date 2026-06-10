"""Declarative base shared by every ShoreGuard model."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all Shoreguard models."""
