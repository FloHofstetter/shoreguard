"""Unit tests for SBOMService — CycloneDX SBOM ingestion and component queries."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from shoreguard.exceptions import InvalidSBOMError
from shoreguard.models import Base, SBOMComponent, SBOMSnapshot
from shoreguard.services.sbom import SBOMService, parse_cyclonedx


@pytest.fixture
async def sbom_svc():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield SBOMService(factory), factory
    await engine.dispose()


def _minimal_cdx(components=None, vulnerabilities=None, **overrides):
    """Return a minimal CycloneDX 1.5 dict, with overrides."""
    base = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:abcd-1234",
        "components": components or [],
        "vulnerabilities": vulnerabilities or [],
    }
    base.update(overrides)
    return base


def _full_cdx_doc():
    """A small but realistic CycloneDX document with vulns + multiple ratings."""
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:test-123",
        "components": [
            {
                "bom-ref": "pkg:pypi/requests@2.31.0",
                "type": "library",
                "name": "requests",
                "version": "2.31.0",
                "purl": "pkg:pypi/requests@2.31.0",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            },
            {
                "bom-ref": "pkg:pypi/urllib3@1.26.0",
                "type": "library",
                "name": "urllib3",
                "version": "1.26.0",
                "purl": "pkg:pypi/urllib3@1.26.0",
                "licenses": [{"license": {"id": "MIT"}}],
            },
            {
                "bom-ref": "pkg:npm/lodash@4.17.20",
                "type": "library",
                "name": "lodash",
                "version": "4.17.20",
                "purl": "pkg:npm/lodash@4.17.20",
                "licenses": [{"expression": "MIT OR Apache-2.0"}],
            },
        ],
        "vulnerabilities": [
            {
                "id": "CVE-2023-32681",
                "ratings": [
                    {"severity": "medium", "score": 5.4, "source": {"name": "NVD"}},
                    {"severity": "high", "score": 7.5, "source": {"name": "GitHub"}},
                ],
                "affects": [{"ref": "pkg:pypi/requests@2.31.0"}],
                "description": "Proxy-Authorization leak",
                "advisories": [{"url": "https://github.com/advisories/GHSA-xxxx"}],
            },
            {
                "id": "CVE-2021-23337",
                "ratings": [{"severity": "critical", "score": 9.8}],
                "affects": [{"ref": "pkg:npm/lodash@4.17.20"}],
                "description": "Command injection in lodash template",
                "references": [{"url": "https://nvd.nist.gov/vuln/detail/CVE-2021-23337"}],
            },
        ],
    }


# ---------------------------------------------------------------------------
# parse_cyclonedx — happy paths + edge cases
# ---------------------------------------------------------------------------


class TestParseCycloneDX:
    async def test_minimal_document(self):
        parsed = parse_cyclonedx(json.dumps(_minimal_cdx()))
        assert parsed.bom_format == "CycloneDX"
        assert parsed.spec_version == "1.5"
        assert parsed.serial_number == "urn:uuid:abcd-1234"
        assert parsed.components == []
        assert parsed.vulnerabilities == []
        assert parsed.max_severity is None

    async def test_components_only(self):
        doc = _minimal_cdx(
            components=[
                {"name": "foo", "version": "1.0", "type": "library"},
                {"name": "bar", "version": "2.0", "type": "framework"},
            ]
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert len(parsed.components) == 2
        assert parsed.components[0]["name"] == "foo"
        assert parsed.components[0]["vuln_count"] == 0
        assert parsed.components[0]["max_severity"] is None
        assert parsed.max_severity is None

    async def test_full_document(self):
        parsed = parse_cyclonedx(json.dumps(_full_cdx_doc()))
        assert len(parsed.components) == 3
        assert len(parsed.vulnerabilities) == 2

        by_name = {c["name"]: c for c in parsed.components}
        assert by_name["requests"]["vuln_count"] == 1
        assert by_name["requests"]["max_severity"] == "HIGH"
        assert by_name["urllib3"]["vuln_count"] == 0
        assert by_name["urllib3"]["max_severity"] is None
        assert by_name["lodash"]["vuln_count"] == 1
        assert by_name["lodash"]["max_severity"] == "CRITICAL"

        # Snapshot-level max severity is the worst across the document.
        assert parsed.max_severity == "CRITICAL"

    async def test_picks_highest_rating_when_multiple(self):
        doc = _minimal_cdx(
            components=[{"bom-ref": "ref1", "name": "x", "type": "library"}],
            vulnerabilities=[
                {
                    "id": "CVE-1",
                    "ratings": [
                        {"severity": "low"},
                        {"severity": "high"},
                        {"severity": "medium"},
                    ],
                    "affects": [{"ref": "ref1"}],
                }
            ],
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.vulnerabilities[0]["severity"] == "HIGH"
        assert parsed.components[0]["max_severity"] == "HIGH"

    async def test_unknown_severity_treated_as_unknown(self):
        doc = _minimal_cdx(
            components=[{"bom-ref": "x", "name": "x"}],
            vulnerabilities=[
                {"id": "CVE-X", "ratings": [{"severity": "weird"}], "affects": [{"ref": "x"}]}
            ],
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.vulnerabilities[0]["severity"] == "UNKNOWN"

    async def test_vulnerability_without_id_skipped(self):
        doc = _minimal_cdx(
            components=[{"name": "a"}],
            vulnerabilities=[{"ratings": [{"severity": "high"}]}],
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.vulnerabilities == []

    async def test_component_without_name_skipped(self):
        doc = _minimal_cdx(components=[{"version": "1.0"}, {"name": "valid"}])
        parsed = parse_cyclonedx(json.dumps(doc))
        assert len(parsed.components) == 1
        assert parsed.components[0]["name"] == "valid"

    async def test_licenses_id_form(self):
        doc = _minimal_cdx(
            components=[
                {
                    "name": "x",
                    "licenses": [
                        {"license": {"id": "MIT"}},
                        {"license": {"id": "Apache-2.0"}},
                    ],
                }
            ]
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.components[0]["licenses"] == "MIT, Apache-2.0"

    async def test_licenses_expression_form(self):
        doc = _minimal_cdx(
            components=[{"name": "x", "licenses": [{"expression": "MIT OR Apache-2.0"}]}]
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.components[0]["licenses"] == "MIT OR Apache-2.0"

    async def test_licenses_missing(self):
        doc = _minimal_cdx(components=[{"name": "x"}])
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.components[0]["licenses"] is None

    async def test_affects_pointing_at_unknown_ref_does_not_crash(self):
        doc = _minimal_cdx(
            components=[{"bom-ref": "real", "name": "real"}],
            vulnerabilities=[
                {
                    "id": "CVE-Y",
                    "ratings": [{"severity": "high"}],
                    "affects": [{"ref": "ghost"}, {"ref": "real"}],
                }
            ],
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.components[0]["vuln_count"] == 1
        assert parsed.components[0]["max_severity"] == "HIGH"

    async def test_advisories_and_references_merged(self):
        doc = _minimal_cdx(
            components=[{"bom-ref": "x", "name": "x"}],
            vulnerabilities=[
                {
                    "id": "CVE-Z",
                    "ratings": [{"severity": "low"}],
                    "affects": [{"ref": "x"}],
                    "advisories": [{"url": "https://a"}],
                    "references": [{"url": "https://b"}],
                }
            ],
        )
        parsed = parse_cyclonedx(json.dumps(doc))
        assert parsed.vulnerabilities[0]["references"] == ["https://a", "https://b"]


# ---------------------------------------------------------------------------
# parse_cyclonedx — failure paths
# ---------------------------------------------------------------------------


class TestParseCycloneDXFailures:
    async def test_invalid_json_raises(self):
        with pytest.raises(InvalidSBOMError, match="not valid JSON"):
            parse_cyclonedx("{not json")

    async def test_root_must_be_object(self):
        with pytest.raises(InvalidSBOMError, match="root must be"):
            parse_cyclonedx("[]")

    async def test_missing_bom_format(self):
        with pytest.raises(InvalidSBOMError, match="bomFormat"):
            parse_cyclonedx(json.dumps({"specVersion": "1.5"}))

    async def test_wrong_bom_format(self):
        with pytest.raises(InvalidSBOMError, match="bomFormat"):
            parse_cyclonedx(json.dumps({"bomFormat": "SPDX", "specVersion": "2.3"}))

    async def test_missing_spec_version(self):
        with pytest.raises(InvalidSBOMError, match="specVersion"):
            parse_cyclonedx(json.dumps({"bomFormat": "CycloneDX"}))

    async def test_components_must_be_list(self):
        with pytest.raises(InvalidSBOMError, match="components"):
            parse_cyclonedx(
                json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.5", "components": {}})
            )

    async def test_vulnerabilities_must_be_list(self):
        with pytest.raises(InvalidSBOMError, match="vulnerabilities"):
            parse_cyclonedx(
                json.dumps(
                    {"bomFormat": "CycloneDX", "specVersion": "1.5", "vulnerabilities": "nope"}
                )
            )


# ---------------------------------------------------------------------------
# SBOMService.ingest / get_snapshot / delete_snapshot
# ---------------------------------------------------------------------------


class TestIngest:
    async def test_ingest_minimal(self, sbom_svc):
        svc, _ = sbom_svc
        result = await svc.ingest("gw1", "sb1", json.dumps(_minimal_cdx()), "alice@test.com")
        assert result["gateway_name"] == "gw1"
        assert result["sandbox_name"] == "sb1"
        assert result["component_count"] == 0
        assert result["vulnerability_count"] == 0
        assert result["max_severity"] is None
        assert result["uploaded_by"] == "alice@test.com"
        assert result["serial_number"] == "urn:uuid:abcd-1234"

    async def test_ingest_full_document(self, sbom_svc):
        svc, _ = sbom_svc
        result = await svc.ingest("gw1", "sb1", json.dumps(_full_cdx_doc()), "alice@test.com")
        assert result["component_count"] == 3
        assert result["vulnerability_count"] == 2
        assert result["max_severity"] == "CRITICAL"

    async def test_ingest_persists_components(self, sbom_svc):
        svc, factory = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_full_cdx_doc()), "alice@test.com")
        async with factory() as session:
            count = (
                await session.execute(select(func.count()).select_from(SBOMComponent))
            ).scalar() or 0
            assert count == 3

    async def test_ingest_replaces_prior_snapshot(self, sbom_svc):
        svc, factory = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_full_cdx_doc()), "alice@test.com")
        new_doc = _minimal_cdx(
            components=[{"name": "only-one", "version": "1.0"}],
            serialNumber="urn:uuid:replaced",
        )
        result = await svc.ingest("gw1", "sb1", json.dumps(new_doc), "bob@test.com")
        assert result["component_count"] == 1
        assert result["serial_number"] == "urn:uuid:replaced"
        assert result["uploaded_by"] == "bob@test.com"
        async with factory() as session:
            assert (
                (await session.execute(select(func.count()).select_from(SBOMSnapshot))).scalar()
                or 0
            ) == 1
            assert (
                (await session.execute(select(func.count()).select_from(SBOMComponent))).scalar()
                or 0
            ) == 1

    async def test_ingest_separate_sandboxes_independent(self, sbom_svc):
        svc, factory = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_minimal_cdx()), "a@test.com")
        await svc.ingest("gw1", "sb2", json.dumps(_minimal_cdx()), "a@test.com")
        async with factory() as session:
            assert (
                (await session.execute(select(func.count()).select_from(SBOMSnapshot))).scalar()
                or 0
            ) == 2

    async def test_ingest_invalid_payload_raises(self, sbom_svc):
        svc, _ = sbom_svc
        with pytest.raises(InvalidSBOMError):
            await svc.ingest("gw1", "sb1", "{not json", "a@test.com")

    async def test_ingest_preserves_raw_json(self, sbom_svc):
        svc, _ = sbom_svc
        raw = json.dumps(_full_cdx_doc())
        await svc.ingest("gw1", "sb1", raw, "alice@test.com")
        assert await svc.get_raw_json("gw1", "sb1") == raw


class TestGetSnapshot:
    async def test_get_snapshot_existing(self, sbom_svc):
        svc, _ = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_minimal_cdx()), "a@test.com")
        snap = await svc.get_snapshot("gw1", "sb1")
        assert snap is not None
        assert snap["sandbox_name"] == "sb1"

    async def test_get_snapshot_missing(self, sbom_svc):
        svc, _ = sbom_svc
        assert await svc.get_snapshot("gw1", "missing") is None


class TestDeleteSnapshot:
    async def test_delete_existing(self, sbom_svc):
        svc, factory = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_full_cdx_doc()), "a@test.com")
        assert await svc.delete_snapshot("gw1", "sb1") is True
        assert await svc.get_snapshot("gw1", "sb1") is None
        async with factory() as session:
            assert (
                (await session.execute(select(func.count()).select_from(SBOMComponent))).scalar()
                or 0
            ) == 0

    async def test_delete_missing(self, sbom_svc):
        svc, _ = sbom_svc
        assert await svc.delete_snapshot("gw1", "sb1") is False

    async def test_delete_does_not_affect_other_sandboxes(self, sbom_svc):
        svc, _ = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_minimal_cdx()), "a@test.com")
        await svc.ingest("gw1", "sb2", json.dumps(_minimal_cdx()), "a@test.com")
        await svc.delete_snapshot("gw1", "sb1")
        assert await svc.get_snapshot("gw1", "sb2") is not None


# ---------------------------------------------------------------------------
# search_components
# ---------------------------------------------------------------------------


class TestSearchComponents:
    async def _seed(self, svc):
        await svc.ingest("gw1", "sb1", json.dumps(_full_cdx_doc()), "a@test.com")

    async def test_no_filter_returns_all(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1")
        assert total == 3
        assert len(items) == 3

    async def test_no_snapshot_returns_empty(self, sbom_svc):
        svc, _ = sbom_svc
        items, total = await svc.search_components("gw1", "missing")
        assert items == []
        assert total == 0

    async def test_search_by_name(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1", search="requests")
        assert total == 1
        assert items[0]["name"] == "requests"

    async def test_search_case_insensitive(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, _ = await svc.search_components("gw1", "sb1", search="LODASH")
        assert len(items) == 1
        assert items[0]["name"] == "lodash"

    async def test_search_by_purl(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1", search="pkg:npm")
        assert total == 1
        assert items[0]["name"] == "lodash"

    async def test_severity_filter_critical(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1", severity="CRITICAL")
        assert total == 1
        assert items[0]["name"] == "lodash"

    async def test_severity_filter_clean(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1", severity="CLEAN")
        assert total == 1
        assert items[0]["name"] == "urllib3"

    async def test_search_and_severity_combined(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components(
            "gw1", "sb1", search="lodash", severity="CRITICAL"
        )
        assert total == 1
        items, total = await svc.search_components(
            "gw1", "sb1", search="requests", severity="CRITICAL"
        )
        assert total == 0

    async def test_pagination(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1", limit=2, offset=0)
        assert total == 3
        assert len(items) == 2
        items, _ = await svc.search_components("gw1", "sb1", limit=2, offset=2)
        assert len(items) == 1

    async def test_pagination_defaults(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, _ = await svc.search_components("gw1", "sb1", limit=0)
        assert len(items) == 3  # falls back to default 50

    async def test_pagination_caps_at_500(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, _ = await svc.search_components("gw1", "sb1", limit=10000)
        assert len(items) == 3

    async def test_negative_offset_clamped(self, sbom_svc):
        svc, _ = sbom_svc
        await self._seed(svc)
        items, total = await svc.search_components("gw1", "sb1", offset=-5)
        assert total == 3
        assert len(items) == 3


# ---------------------------------------------------------------------------
# get_vulnerabilities
# ---------------------------------------------------------------------------


class TestGetVulnerabilities:
    async def test_returns_none_when_no_snapshot(self, sbom_svc):
        svc, _ = sbom_svc
        assert await svc.get_vulnerabilities("gw1", "missing") is None

    async def test_empty_when_no_vulns(self, sbom_svc):
        svc, _ = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_minimal_cdx()), "a@test.com")
        assert await svc.get_vulnerabilities("gw1", "sb1") == []

    async def test_full_document(self, sbom_svc):
        svc, _ = sbom_svc
        await svc.ingest("gw1", "sb1", json.dumps(_full_cdx_doc()), "a@test.com")
        vulns = await svc.get_vulnerabilities("gw1", "sb1")
        assert vulns is not None
        assert len(vulns) == 2
        # Sorted highest severity first.
        assert vulns[0]["severity"] == "CRITICAL"
        assert vulns[0]["id"] == "CVE-2021-23337"
        assert vulns[1]["severity"] == "HIGH"
        assert vulns[1]["affects"] == ["pkg:pypi/requests@2.31.0"]
