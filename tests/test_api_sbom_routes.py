"""Integration tests for SBOM API routes (M21)."""

from __future__ import annotations

import json

GW = "test"
SB = "sb1"
BASE = f"/api/gateways/{GW}/sandboxes/{SB}/sbom"


def _minimal_cdx() -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "components": [],
        "vulnerabilities": [],
    }


def _full_cdx() -> dict:
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": "urn:uuid:api-test",
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
                "bom-ref": "pkg:npm/lodash@4.17.20",
                "type": "library",
                "name": "lodash",
                "version": "4.17.20",
                "purl": "pkg:npm/lodash@4.17.20",
                "licenses": [{"license": {"id": "MIT"}}],
            },
        ],
        "vulnerabilities": [
            {
                "id": "CVE-2021-23337",
                "ratings": [{"severity": "critical", "score": 9.8}],
                "affects": [{"ref": "pkg:npm/lodash@4.17.20"}],
                "description": "lodash command injection",
                "advisories": [{"url": "https://example/CVE-2021-23337"}],
            }
        ],
    }


class TestUploadSBOM:
    async def test_upload_minimal(self, api_client):
        resp = await api_client.post(BASE, content=json.dumps(_minimal_cdx()))
        assert resp.status_code == 201
        data = resp.json()
        assert data["component_count"] == 0
        assert data["vulnerability_count"] == 0
        assert data["max_severity"] is None

    async def test_upload_full(self, api_client):
        resp = await api_client.post(BASE, content=json.dumps(_full_cdx()))
        assert resp.status_code == 201
        data = resp.json()
        assert data["component_count"] == 2
        assert data["vulnerability_count"] == 1
        assert data["max_severity"] == "CRITICAL"
        assert data["serial_number"] == "urn:uuid:api-test"

    async def test_upload_invalid_json(self, api_client):
        resp = await api_client.post(BASE, content="{not json")
        assert resp.status_code == 400
        assert "JSON" in resp.json()["detail"]

    async def test_upload_wrong_format(self, api_client):
        resp = await api_client.post(
            BASE, content=json.dumps({"bomFormat": "SPDX", "specVersion": "2.3"})
        )
        assert resp.status_code == 400

    async def test_upload_missing_spec_version(self, api_client):
        resp = await api_client.post(BASE, content=json.dumps({"bomFormat": "CycloneDX"}))
        assert resp.status_code == 400

    async def test_upload_empty_body(self, api_client):
        resp = await api_client.post(BASE, content="")
        assert resp.status_code == 400

    async def test_upload_replaces_prior(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.post(BASE, content=json.dumps(_minimal_cdx()))
        assert resp.status_code == 201
        # Verify only the new snapshot remains.
        get_resp = await api_client.get(BASE)
        assert get_resp.status_code == 200
        assert get_resp.json()["component_count"] == 0


class TestGetSBOM:
    async def test_get_existing(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.get(BASE)
        assert resp.status_code == 200
        data = resp.json()
        assert data["sandbox_name"] == SB
        assert data["component_count"] == 2

    async def test_get_404_when_missing(self, api_client):
        resp = await api_client.get(BASE)
        assert resp.status_code == 404


class TestListComponents:
    async def test_returns_components(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.get(f"{BASE}/components")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["offset"] == 0
        assert data["limit"] == 50

    async def test_search_filter(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.get(f"{BASE}/components?search=lodash")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "lodash"

    async def test_severity_filter(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.get(f"{BASE}/components?severity=CRITICAL")
        assert resp.json()["total"] == 1
        resp = await api_client.get(f"{BASE}/components?severity=CLEAN")
        assert resp.json()["total"] == 1

    async def test_pagination(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.get(f"{BASE}/components?offset=0&limit=1")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 1

    async def test_empty_when_no_snapshot(self, api_client):
        resp = await api_client.get(f"{BASE}/components")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestListVulnerabilities:
    async def test_returns_vulns(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.get(f"{BASE}/vulnerabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["vulnerabilities"][0]["id"] == "CVE-2021-23337"
        assert data["vulnerabilities"][0]["severity"] == "CRITICAL"

    async def test_404_when_missing(self, api_client):
        resp = await api_client.get(f"{BASE}/vulnerabilities")
        assert resp.status_code == 404


class TestGetRaw:
    async def test_returns_raw_payload(self, api_client):
        raw = json.dumps(_full_cdx())
        await api_client.post(BASE, content=raw)
        resp = await api_client.get(f"{BASE}/raw")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/vnd.cyclonedx+json")
        # The raw body must be byte-identical to what we uploaded.
        assert resp.text == raw

    async def test_404_when_missing(self, api_client):
        resp = await api_client.get(f"{BASE}/raw")
        assert resp.status_code == 404


class TestDeleteSBOM:
    async def test_delete_existing(self, api_client):
        await api_client.post(BASE, content=json.dumps(_full_cdx()))
        resp = await api_client.delete(BASE)
        assert resp.status_code == 204
        get_resp = await api_client.get(BASE)
        assert get_resp.status_code == 404

    async def test_delete_missing(self, api_client):
        resp = await api_client.delete(BASE)
        assert resp.status_code == 404
