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
    # SHOREGUARD_CSP_POLICY is the legacy override; only consulted when strict
    # mode is off. Opt out of strict mode explicitly for this test.
    monkeypatch.setenv("SHOREGUARD_CSP_STRICT", "false")
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


def _csp_directives(csp: str) -> dict[str, list[str]]:
    """Split a CSP header into ``{directive: [tokens]}`` for assertion."""
    out: dict[str, list[str]] = {}
    for chunk in csp.split(";"):
        parts = chunk.strip().split()
        if parts:
            out[parts[0]] = parts[1:]
    return out


async def test_csp_default_is_strict(api_client: AsyncClient):
    """As of v0.27.0, the default CSP is strict: nonce-gated, no 'unsafe-inline'.

    'unsafe-eval' is retained because Alpine.js uses Function() internally;
    the @alpinejs/csp build was evaluated but its expression parser was too
    restrictive for this UI. See alpine_loader.html for the rationale.

    ``style-src-attr 'unsafe-inline'`` (added in 45129f4) is intentionally
    permitted — Alpine's x-show / x-cloak / x-transition emit inline ``style``
    attributes that the strict ``style-src 'self'`` directive otherwise blocks.
    The narrower ``-attr`` directive scopes this to attributes only, not
    ``<style>`` blocks or stylesheets.
    """
    resp = await api_client.get("/healthz")
    csp = resp.headers["Content-Security-Policy"]
    directives = _csp_directives(csp)

    assert "nonce-" in csp
    assert "'unsafe-eval'" in csp  # required for Alpine.js Function() constructor
    # Source directives that govern script + style content must NOT carry
    # 'unsafe-inline'. style-src-attr is allowed (see docstring).
    for d in ("default-src", "script-src", "style-src"):
        assert "'unsafe-inline'" not in directives.get(d, []), (
            f"{d} unexpectedly carries 'unsafe-inline': {directives.get(d)}"
        )
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


async def test_csp_legacy_mode_contains_unsafe(monkeypatch):
    """Legacy mode (SHOREGUARD_CSP_STRICT=false) still ships 'unsafe-*'."""
    monkeypatch.setenv("SHOREGUARD_CSP_STRICT", "false")

    from shoreguard.settings import reset_settings

    reset_settings()

    from shoreguard.api.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/healthz")

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
    directives = _csp_directives(csp)
    assert "nonce-" in csp
    assert "'unsafe-eval'" in csp  # retained for Alpine.js Function() constructor
    assert "{nonce}" not in csp  # placeholder must be interpolated
    for d in ("default-src", "script-src", "style-src"):
        assert "'unsafe-inline'" not in directives.get(d, []), (
            f"{d} unexpectedly carries 'unsafe-inline': {directives.get(d)}"
        )


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


async def test_login_uses_regular_alpine_build_in_strict_mode(monkeypatch):
    """v0.27.0: strict mode uses the regular Alpine build, not @alpinejs/csp.

    The CSP build was evaluated during M2.1 but its expression parser only
    supports plain property chains (no operators, no literals, no method
    args) — too restrictive for this UI. Strict mode instead allows
    'unsafe-eval' in script-src for Alpine's Function() constructor while
    keeping all other hardening (no 'unsafe-inline', nonce-gated, etc.).
    """
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
    assert "@alpinejs/csp@" not in resp.text
    assert 'alpinejs@3.14.9/dist/cdn.min.js"' in resp.text


async def test_no_inline_x_data_objects_on_login():
    """M4: /login uses a registered Alpine.data() component, not an inline object."""
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
    # No inline object literal in x-data (neither `x-data="{` nor `x-data='{`).
    assert not re.search(r'x-data\s*=\s*["\'][^"\']*\{', resp.text), (
        "unexpected inline x-data object literal on /login"
    )


async def test_no_inline_styles_on_login():
    """M3: no inline <style> blocks or style="..." attributes on /login."""
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
    assert "<style" not in resp.text, "unexpected inline <style> block on /login"
    # Static style="..." attributes — Alpine :style= bindings are allowed.
    inline_attr = re.search(r'(?<![:@x-])\sstyle\s*=\s*["\']', resp.text)
    assert inline_attr is None, (
        f"unexpected inline style attribute on /login: {inline_attr.group(0)!r}"
    )
