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


async def test_csp_default_mode_contains_unsafe(api_client: AsyncClient):
    """Default CSP retains 'unsafe-*' until M4 ships the Alpine refactor."""
    resp = await api_client.get("/healthz")
    csp = resp.headers["Content-Security-Policy"]
    assert "'unsafe-inline'" in csp
    assert "'unsafe-eval'" in csp
    assert "nonce-" not in csp


async def test_csp_strict_mode_emits_nonce(monkeypatch):
    """csp_strict=True replaces {nonce} in csp_policy_strict and drops unsafe-*."""
    monkeypatch.setenv("SHOREGUARD_CSP_STRICT", "true")

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/healthz")

    csp = resp.headers["Content-Security-Policy"]
    assert "nonce-" in csp
    assert "'unsafe-inline'" not in csp
    assert "'unsafe-eval'" not in csp
    assert "{nonce}" not in csp  # placeholder must be interpolated


async def test_csp_strict_nonce_differs_per_request(monkeypatch):
    """Each request must get a fresh cryptographic nonce."""
    import re

    monkeypatch.setenv("SHOREGUARD_CSP_STRICT", "true")

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    pattern = re.compile(r"nonce-([A-Za-z0-9_-]+)")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        r1 = await client.get("/healthz")
        r2 = await client.get("/healthz")

    m1 = pattern.search(r1.headers["Content-Security-Policy"])
    m2 = pattern.search(r2.headers["Content-Security-Policy"])
    assert m1 is not None and m2 is not None
    assert m1.group(1) != m2.group(1)
    # urlsafe base64 of 16 random bytes ≥ ~21 chars
    assert len(m1.group(1)) >= 16


async def test_theme_init_is_external_in_strict_mode(monkeypatch):
    """M2: theme-init is loaded as an external script, not inline."""
    monkeypatch.setenv("SHOREGUARD_CSP_STRICT", "true")

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/login")

    assert resp.status_code == 200
    # Theme-init is now an external script, not inline.
    assert '<script src="/static/js/theme-init.js">' in resp.text
    # The CSP header still carries a nonce (mechanism stays for future M3 use).
    assert "nonce-" in resp.headers["Content-Security-Policy"]


async def test_no_inline_scripts_on_login():
    """M2: no inline <script> blocks on /login — only external src= tags."""
    import re

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/login")

    assert resp.status_code == 200
    # Match any <script> tag without src= that has non-whitespace content inside.
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>\s*\S", resp.text)
    assert inline == [], f"Unexpected inline <script> blocks on /login: {inline}"
