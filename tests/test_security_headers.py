from httpx import ASGITransport, AsyncClient


async def test_default_security_headers(api_client: AsyncClient):
    resp = await api_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers["Permissions-Policy"]
    assert "Content-Security-Policy" in resp.headers


async def test_no_hsts_by_default(api_client: AsyncClient):
    resp = await api_client.get("/healthz")
    assert "Strict-Transport-Security" not in resp.headers


async def test_hsts_when_enabled(monkeypatch):
    monkeypatch.setenv("SHOREGUARD_HSTS_ENABLED", "true")
    monkeypatch.setenv("SHOREGUARD_HSTS_MAX_AGE", "3600")

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/healthz")
    assert resp.headers["Strict-Transport-Security"] == "max-age=3600; includeSubDomains"


async def test_custom_csp(monkeypatch):
    monkeypatch.setenv("SHOREGUARD_CSP_POLICY", "default-src 'none'")

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/healthz")
    assert resp.headers["Content-Security-Policy"] == "default-src 'none'"
