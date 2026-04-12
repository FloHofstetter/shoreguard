"""SBOM ingestion + query service (M21).

Operators upload CycloneDX JSON SBOMs from their CI pipelines via the
``POST /api/gateways/{gw}/sandboxes/{name}/sbom`` endpoint.  This service
parses the document, persists a denormalised view in
``sbom_snapshots`` + ``sbom_components``, and exposes paginated /
filterable read APIs for the SBOM viewer page.

Vulnerabilities are read **only** from the CycloneDX ``vulnerabilities``
array — there is no online lookup against NVD/OSV.  Keeping ingestion
deterministic + offline avoids tying ShoreGuard to a third-party
availability/rate-limit story.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from shoreguard.exceptions import InvalidSBOMError
from shoreguard.models import SBOMComponent, SBOMSnapshot

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker as SessionMaker

logger = logging.getLogger(__name__)

#: Module-level singleton — set during app lifespan (see shoreguard.api.main).
sbom_service: SBOMService | None = None

#: Severity ordering, highest first.  ``UNKNOWN`` is treated as the lowest
#: confident severity but still ranked above ``None`` (no vulns at all).
_SEVERITY_RANK: dict[str, int] = {
    "CRITICAL": 5,
    "HIGH": 4,
    "MEDIUM": 3,
    "LOW": 2,
    "INFO": 1,
    "NONE": 1,
    "UNKNOWN": 0,
}

_SEVERITY_ORDER: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO", "NONE", "UNKNOWN")


def _normalise_severity(value: Any) -> str | None:
    """Normalise a CycloneDX severity string to its uppercase canonical form.

    Args:
        value: Raw severity from a CycloneDX rating (any case, may be None).

    Returns:
        str | None: Uppercase severity, or ``None`` if not parseable.
    """
    if not isinstance(value, str):
        return None
    upper = value.strip().upper()
    if not upper:
        return None
    if upper in _SEVERITY_RANK:
        return upper
    return "UNKNOWN"


def _max_severity(a: str | None, b: str | None) -> str | None:
    """Return the higher-ranked severity of two values.

    Args:
        a: First severity.
        b: Second severity.

    Returns:
        str | None: The higher-ranked, or ``None`` if both inputs are ``None``.
    """
    if a is None:
        return b
    if b is None:
        return a
    return a if _SEVERITY_RANK.get(a, 0) >= _SEVERITY_RANK.get(b, 0) else b


def _extract_licenses(licenses_node: Any) -> str | None:
    """Reduce a CycloneDX ``licenses`` node to a comma-joined string.

    Args:
        licenses_node: The component's ``licenses`` field (list or None).

    Returns:
        str | None: Joined license identifiers, or ``None`` if empty.
    """
    if not isinstance(licenses_node, list):
        return None
    out: list[str] = []
    for entry in licenses_node:
        if not isinstance(entry, dict):
            continue
        license_obj = entry.get("license")
        if isinstance(license_obj, dict):
            ident = license_obj.get("id") or license_obj.get("name")
            if isinstance(ident, str) and ident.strip():
                out.append(ident.strip())
                continue
        expression = entry.get("expression")
        if isinstance(expression, str) and expression.strip():
            out.append(expression.strip())
    return ", ".join(out) if out else None


@dataclasses.dataclass(slots=True)
class ParsedSBOM:
    """Lightweight value-object holding parsed CycloneDX content.

    Attributes:
        bom_format: ``"CycloneDX"`` (only supported format).
        spec_version: CycloneDX spec version (e.g. ``"1.5"``).
        serial_number: Optional CycloneDX serial number URN.
        components: List of dicts ready for ``SBOMComponent`` insert.
        vulnerabilities: List of structured vulnerability dicts (for the
            ``GET /sbom/vulnerabilities`` response).
        max_severity: Highest severity across the whole document, or ``None``.
    """

    bom_format: str
    spec_version: str
    serial_number: str | None
    components: list[dict[str, Any]]
    vulnerabilities: list[dict[str, Any]]
    max_severity: str | None


def parse_cyclonedx(raw_json: str) -> ParsedSBOM:
    """Parse a CycloneDX JSON document into the value-object used for ingest.

    Validates that the document is JSON, declares ``bomFormat == "CycloneDX"``,
    and carries a ``specVersion``.  Components and vulnerabilities are then
    flattened into row-shaped dicts and joined via the ``bom-ref`` link so
    each component picks up an aggregate ``vuln_count`` + ``max_severity``.

    Args:
        raw_json: The raw CycloneDX JSON string from the upload body.

    Returns:
        ParsedSBOM: Parsed document ready for persistence.

    Raises:
        InvalidSBOMError: If the document is not parseable JSON, not
            CycloneDX, or missing the spec version.
    """
    try:
        doc = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise InvalidSBOMError(f"SBOM is not valid JSON: {exc.msg}") from exc

    if not isinstance(doc, dict):
        raise InvalidSBOMError("SBOM root must be a JSON object")

    bom_format = doc.get("bomFormat")
    if bom_format != "CycloneDX":
        raise InvalidSBOMError(
            f"Unsupported bomFormat: {bom_format!r} (only CycloneDX is supported)"
        )

    spec_version = doc.get("specVersion")
    if not isinstance(spec_version, str) or not spec_version.strip():
        raise InvalidSBOMError("CycloneDX document missing specVersion")

    serial_number_raw = doc.get("serialNumber")
    serial_number = serial_number_raw if isinstance(serial_number_raw, str) else None

    raw_components = doc.get("components", [])
    if raw_components is None:
        raw_components = []
    if not isinstance(raw_components, list):
        raise InvalidSBOMError("CycloneDX 'components' must be a list")
    raw_vulns = doc.get("vulnerabilities", [])
    if raw_vulns is None:
        raw_vulns = []
    if not isinstance(raw_vulns, list):
        raise InvalidSBOMError("CycloneDX 'vulnerabilities' must be a list")

    # First pass: flatten components.
    components: list[dict[str, Any]] = []
    bom_ref_index: dict[str, int] = {}
    for entry in raw_components:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            # CycloneDX requires components[].name; skip silently if missing.
            continue
        bom_ref_value = entry.get("bom-ref")
        bom_ref = bom_ref_value if isinstance(bom_ref_value, str) else None
        version_value = entry.get("version")
        version = version_value if isinstance(version_value, str) else None
        purl_value = entry.get("purl")
        purl = purl_value if isinstance(purl_value, str) else None
        type_value = entry.get("type")
        type_ = type_value if isinstance(type_value, str) else None
        component_row: dict[str, Any] = {
            "bom_ref": bom_ref,
            "name": name.strip(),
            "version": version,
            "purl": purl,
            "type": type_,
            "licenses": _extract_licenses(entry.get("licenses")),
            "vuln_count": 0,
            "max_severity": None,
        }
        if bom_ref:
            bom_ref_index[bom_ref] = len(components)
        components.append(component_row)

    # Second pass: flatten vulnerabilities + aggregate per component.
    vulnerabilities: list[dict[str, Any]] = []
    snapshot_max: str | None = None
    for entry in raw_vulns:
        if not isinstance(entry, dict):
            continue
        vuln_id_value = entry.get("id")
        vuln_id = vuln_id_value if isinstance(vuln_id_value, str) else None
        if not vuln_id:
            continue

        # Pick the highest-severity rating.  CycloneDX rates separately per
        # source — we want the worst case across all sources.
        ratings = entry.get("ratings") or []
        severity: str | None = None
        cvss_score: float | None = None
        if isinstance(ratings, list):
            for rating in ratings:
                if not isinstance(rating, dict):
                    continue
                rating_severity = _normalise_severity(rating.get("severity"))
                if rating_severity is None:
                    continue
                if severity is None or _SEVERITY_RANK.get(rating_severity, 0) > _SEVERITY_RANK.get(
                    severity, 0
                ):
                    severity = rating_severity
                    score = rating.get("score")
                    if isinstance(score, (int, float)):
                        cvss_score = float(score)
        if severity is None:
            severity = "UNKNOWN"

        affected_refs: list[str] = []
        affects = entry.get("affects") or []
        if isinstance(affects, list):
            for affect in affects:
                if isinstance(affect, dict):
                    ref = affect.get("ref")
                    if isinstance(ref, str):
                        affected_refs.append(ref)

        # Bump per-component aggregates.
        for ref in affected_refs:
            idx = bom_ref_index.get(ref)
            if idx is None:
                continue
            row = components[idx]
            row["vuln_count"] = int(row["vuln_count"]) + 1
            row["max_severity"] = _max_severity(row["max_severity"], severity)

        snapshot_max = _max_severity(snapshot_max, severity)

        references_out: list[str] = []
        for ref_entry in entry.get("advisories") or []:
            if isinstance(ref_entry, dict):
                url = ref_entry.get("url")
                if isinstance(url, str):
                    references_out.append(url)
        for ref_entry in entry.get("references") or []:
            if isinstance(ref_entry, dict):
                url = ref_entry.get("url")
                if isinstance(url, str):
                    references_out.append(url)

        description_value = entry.get("description")
        description = description_value if isinstance(description_value, str) else None

        vulnerabilities.append(
            {
                "id": vuln_id,
                "severity": severity,
                "cvss_score": cvss_score,
                "description": description,
                "affects": affected_refs,
                "references": references_out,
            }
        )

    return ParsedSBOM(
        bom_format=bom_format,
        spec_version=spec_version,
        serial_number=serial_number,
        components=components,
        vulnerabilities=vulnerabilities,
        max_severity=snapshot_max,
    )


def _component_to_dict(c: SBOMComponent) -> dict[str, Any]:
    """Convert an ``SBOMComponent`` ORM instance to a plain dict.

    Args:
        c: The component row.

    Returns:
        dict[str, Any]: Component data ready for JSON serialisation.
    """
    return {
        "id": c.id,
        "bom_ref": c.bom_ref,
        "name": c.name,
        "version": c.version,
        "purl": c.purl,
        "type": c.type,
        "licenses": c.licenses,
        "vuln_count": c.vuln_count,
        "max_severity": c.max_severity,
    }


def _snapshot_to_dict(s: SBOMSnapshot) -> dict[str, Any]:
    """Convert an ``SBOMSnapshot`` ORM instance to a plain dict (no raw_json).

    Args:
        s: The snapshot row.

    Returns:
        dict[str, Any]: Snapshot metadata, omitting the raw JSON payload.
    """
    return {
        "id": s.id,
        "gateway_name": s.gateway_name,
        "sandbox_name": s.sandbox_name,
        "bom_format": s.bom_format,
        "spec_version": s.spec_version,
        "serial_number": s.serial_number,
        "uploaded_by": s.uploaded_by,
        "uploaded_at": s.uploaded_at.isoformat() if s.uploaded_at else None,
        "component_count": s.component_count,
        "vulnerability_count": s.vulnerability_count,
        "max_severity": s.max_severity,
    }


class SBOMService:
    """DB-backed SBOM ingestion + query service.

    Args:
        session_factory: SQLAlchemy session factory for database access.
    """

    def __init__(self, session_factory: SessionMaker) -> None:  # noqa: D107
        self._session_factory = session_factory

    def ingest(
        self,
        gateway_name: str,
        sandbox_name: str,
        raw_json: str,
        uploaded_by: str,
    ) -> dict[str, Any]:
        """Parse + persist a CycloneDX SBOM, replacing any prior snapshot.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox the SBOM describes.
            raw_json: Raw CycloneDX JSON document.
            uploaded_by: Identity of the user uploading the snapshot.

        Note:
            Raises :class:`~shoreguard.exceptions.InvalidSBOMError` from
            :func:`parse_cyclonedx` if the document is not a valid CycloneDX
            SBOM.

        Returns:
            dict[str, Any]: Snapshot metadata for the freshly stored row.
        """
        parsed = parse_cyclonedx(raw_json)
        # Persist the **original** payload for later download. Re-serialising
        # the parsed view would lose unknown fields and fail signature
        # verification if the user later wires that up.
        with self._session_factory() as session:
            existing = self._get_snapshot_row(session, gateway_name, sandbox_name)
            if existing is not None:
                # Explicit delete of dependent rows: SQLite with the default
                # connection pragma does not enforce ON DELETE CASCADE, so
                # we cannot rely on the FK alone.
                session.execute(
                    delete(SBOMComponent).where(SBOMComponent.snapshot_id == existing.id)
                )
                session.delete(existing)
                session.flush()
            now = datetime.datetime.now(datetime.UTC)
            snapshot = SBOMSnapshot(
                gateway_name=gateway_name,
                sandbox_name=sandbox_name,
                bom_format=parsed.bom_format,
                spec_version=parsed.spec_version,
                serial_number=parsed.serial_number,
                uploaded_by=uploaded_by,
                uploaded_at=now,
                component_count=len(parsed.components),
                vulnerability_count=len(parsed.vulnerabilities),
                max_severity=parsed.max_severity,
                raw_json=raw_json,
            )
            session.add(snapshot)
            session.flush()
            for row in parsed.components:
                session.add(SBOMComponent(snapshot_id=snapshot.id, **row))
            session.commit()
            session.refresh(snapshot)
            logger.info(
                "SBOM ingested (gateway=%s, sandbox=%s, components=%d, vulns=%d, max=%s, actor=%s)",
                gateway_name,
                sandbox_name,
                snapshot.component_count,
                snapshot.vulnerability_count,
                snapshot.max_severity,
                uploaded_by,
            )
            return _snapshot_to_dict(snapshot)

    def get_snapshot(self, gateway_name: str, sandbox_name: str) -> dict[str, Any] | None:
        """Return snapshot metadata for a sandbox, or ``None`` if not present.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            dict[str, Any] | None: Snapshot metadata or ``None``.
        """
        with self._session_factory() as session:
            row = self._get_snapshot_row(session, gateway_name, sandbox_name)
            return _snapshot_to_dict(row) if row is not None else None

    def get_raw_json(self, gateway_name: str, sandbox_name: str) -> str | None:
        """Return the original CycloneDX JSON payload for download.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            str | None: Raw JSON document, or ``None`` if no snapshot exists.
        """
        with self._session_factory() as session:
            row = self._get_snapshot_row(session, gateway_name, sandbox_name)
            return row.raw_json if row is not None else None

    def delete_snapshot(self, gateway_name: str, sandbox_name: str) -> bool:
        """Delete a sandbox's SBOM snapshot, cascading components.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to clear.

        Returns:
            bool: ``True`` if a snapshot was removed, ``False`` if none existed.
        """
        with self._session_factory() as session:
            row = self._get_snapshot_row(session, gateway_name, sandbox_name)
            if row is None:
                return False
            session.execute(delete(SBOMComponent).where(SBOMComponent.snapshot_id == row.id))
            session.delete(row)
            session.commit()
            return True

    def search_components(
        self,
        gateway_name: str,
        sandbox_name: str,
        *,
        search: str | None = None,
        severity: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a paginated, filtered slice of the component list.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.
            search: Case-insensitive substring matched against ``name`` and
                ``purl``.  ``None`` or empty string returns everything.
            severity: Filter to components whose ``max_severity`` equals this
                uppercase value (``CRITICAL``/``HIGH``/.../``CLEAN``).  Pass
                ``"CLEAN"`` to fetch components with **no** vulnerabilities.
                ``None`` returns everything.
            offset: Pagination offset.
            limit: Pagination page size (capped at 500).

        Returns:
            tuple[list[dict[str, Any]], int]: ``(page, total_after_filter)``.
            Returns ``([], 0)`` when no snapshot exists.
        """
        if limit <= 0:
            limit = 50
        if limit > 500:
            limit = 500
        if offset < 0:
            offset = 0

        with self._session_factory() as session:
            snapshot = self._get_snapshot_row(session, gateway_name, sandbox_name)
            if snapshot is None:
                return [], 0

            stmt = select(SBOMComponent).where(SBOMComponent.snapshot_id == snapshot.id)

            if search:
                term = f"%{search.lower()}%"
                stmt = stmt.where(
                    func.lower(SBOMComponent.name).like(term)
                    | func.lower(func.coalesce(SBOMComponent.purl, "")).like(term)
                )

            if severity:
                sev_upper = severity.strip().upper()
                if sev_upper == "CLEAN":
                    stmt = stmt.where(SBOMComponent.vuln_count == 0)
                elif sev_upper in _SEVERITY_RANK:
                    stmt = stmt.where(SBOMComponent.max_severity == sev_upper)
                # Unknown severity values silently match nothing only if we
                # added a tautology — instead we ignore them so the page
                # behaves predictably for the operator.

            total_stmt = select(func.count()).select_from(stmt.subquery())
            total = int(session.execute(total_stmt).scalar() or 0)

            stmt = stmt.order_by(SBOMComponent.name.asc()).offset(offset).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [_component_to_dict(r) for r in rows], total

    def get_vulnerabilities(
        self, gateway_name: str, sandbox_name: str
    ) -> list[dict[str, Any]] | None:
        """Return the structured vulnerability list for a sandbox.

        Vulnerabilities are derived from the original CycloneDX document
        — re-parsed each call to keep the on-disk schema lean.  SBOMs are
        bounded in size (DB row), so the cost is acceptable for the rare
        rate this endpoint is hit at.

        Args:
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            list[dict[str, Any]] | None: Vulnerability list, or ``None`` if
            no snapshot exists.  Sorted highest-severity first.
        """
        raw = self.get_raw_json(gateway_name, sandbox_name)
        if raw is None:
            return None
        parsed = parse_cyclonedx(raw)
        return sorted(
            parsed.vulnerabilities,
            key=lambda v: (
                _SEVERITY_ORDER.index(v["severity"])
                if v["severity"] in _SEVERITY_ORDER
                else len(_SEVERITY_ORDER)
            ),
        )

    @staticmethod
    def _get_snapshot_row(
        session: Any, gateway_name: str, sandbox_name: str
    ) -> SBOMSnapshot | None:
        """Fetch the raw ``SBOMSnapshot`` row, if any.

        Args:
            session: Active SQLAlchemy session.
            gateway_name: Gateway the sandbox belongs to.
            sandbox_name: Sandbox to query.

        Returns:
            SBOMSnapshot | None: The snapshot row, or ``None``.
        """
        return session.execute(
            select(SBOMSnapshot).where(
                SBOMSnapshot.gateway_name == gateway_name,
                SBOMSnapshot.sandbox_name == sandbox_name,
            )
        ).scalar_one_or_none()
