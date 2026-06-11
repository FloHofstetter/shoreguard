/** Standalone auth pages (login / register / setup / invite) as islands. */

import { useEffect, useState } from "preact/hooks";

interface OidcProvider {
  name: string;
  display_name?: string;
}

function safeNext(): string {
  let next = new URLSearchParams(window.location.search).get("next") || "/";
  if (!next.startsWith("/") || next.startsWith("//")) next = "/";
  return next;
}

async function postJson(
  url: string,
  body: Record<string, unknown>,
): Promise<{ ok: boolean; detail: string }> {
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (resp.ok) return { ok: true, detail: "" };
    const data = await resp.json().catch(() => ({}));
    return { ok: false, detail: data.detail || "" };
  } catch {
    return { ok: false, detail: "Network error — is the server running?" };
  }
}

function AuthCardHeader({ title, subtitle, glow }: {
  title: string;
  subtitle: string;
  glow?: boolean;
}) {
  return (
    <div class="text-center mb-4">
      <i class={`bi bi-shield-check fs-1 sg-text-accent${glow ? " login-glow" : ""}`} />
      <h4 class="mt-2 mb-1">{title}</h4>
      <p class="text-muted small mb-0">{subtitle}</p>
    </div>
  );
}

function PasswordPair({ password, confirm, setPassword, setConfirm, labels }: {
  password: string;
  confirm: string;
  setPassword: (v: string) => void;
  setConfirm: (v: string) => void;
  labels?: boolean;
}) {
  return (
    <div>
      <div class="mb-3">
        {labels && <label class="form-label">Password</label>}
        <input
          type="password"
          class="form-control"
          placeholder={labels ? "Choose a strong password" : "Password"}
          autocomplete="new-password"
          minLength={8}
          required
          value={password}
          onInput={(e) => setPassword((e.target as HTMLInputElement).value)}
        />
        <div class="form-text">Minimum 8 characters</div>
      </div>
      <div class="mb-3">
        {labels && <label class="form-label">Confirm Password</label>}
        <input
          type="password"
          class="form-control"
          placeholder={labels ? "Repeat password" : "Confirm password"}
          autocomplete="new-password"
          required
          value={confirm}
          onInput={(e) => setConfirm((e.target as HTMLInputElement).value)}
        />
      </div>
    </div>
  );
}

function validatePasswords(password: string, confirm: string): string {
  if (password !== confirm) return "Passwords do not match";
  if (password.length < 8) return "Password must be at least 8 characters";
  return "";
}

export function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [registrationEnabled, setRegistrationEnabled] = useState(false);
  const [oidcProviders, setOidcProviders] = useState<OidcProvider[]>([]);
  const oidcError = new URLSearchParams(window.location.search).get("error") || "";
  const nextUrl = safeNext();

  useEffect(() => {
    fetch("/api/auth/check")
      .then((r) => r.json())
      .then((d) => {
        if (d.registration_enabled) setRegistrationEnabled(true);
        if (d.oidc_providers) setOidcProviders(d.oidc_providers);
      })
      .catch(() => undefined);
  }, []);

  const submit = async (e: Event) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    const result = await postJson(`/api/auth/login`, { email: email.trim(), password });
    setLoading(false);
    if (result.ok) {
      window.location.href = nextUrl;
    } else {
      setError(result.detail || "Invalid credentials");
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body p-4">
        <div class="text-center mb-4">
          <i class="bi bi-shield-check fs-1 login-glow sg-text-accent" />
          <h4 class="mt-2 mb-0">Shoreguard</h4>
          <p class="text-muted small mb-1">Control Plane for NVIDIA OpenShell</p>
          <p class="text-muted small">Sign in to continue.</p>
        </div>

        <form onSubmit={(e) => void submit(e)}>
          <div class="mb-3">
            <input
              type="email"
              class="form-control"
              placeholder="Email"
              autocomplete="email"
              autofocus
              required
              value={email}
              onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
            />
          </div>
          <div class="mb-3">
            <input
              type="password"
              class="form-control"
              placeholder="Password"
              autocomplete="current-password"
              required
              value={password}
              onInput={(e) => setPassword((e.target as HTMLInputElement).value)}
            />
          </div>
          {error && <div class="text-danger small mb-3">{error}</div>}
          <button type="submit" class="btn btn-success w-100" disabled={loading}>
            <i class="bi bi-box-arrow-in-right me-1" />
            Log in
          </button>
        </form>

        {oidcError && (
          <div class="text-danger small mt-3">
            {oidcError === "oidc_denied" && <span>Login was denied by the identity provider.</span>}
            {oidcError === "oidc_failed" && <span>Authentication failed. Please try again.</span>}
          </div>
        )}

        {oidcProviders.length > 0 && (
          <div>
            <div class="d-flex align-items-center my-3">
              <hr class="flex-grow-1" />
              <span class="px-2 text-muted small">or</span>
              <hr class="flex-grow-1" />
            </div>
            {oidcProviders.map((p) => (
              <a
                key={p.name}
                href={`/api/auth/oidc/login/${p.name}?next=${encodeURIComponent(nextUrl)}`}
                class="btn btn-outline-secondary w-100 mb-2 d-flex align-items-center justify-content-center gap-2"
              >
                <i class="bi bi-box-arrow-in-right" />
                <span>Sign in with {p.display_name || p.name}</span>
              </a>
            ))}
          </div>
        )}

        {registrationEnabled && (
          <div class="text-center mt-3">
            <a href="/register" class="text-muted small">
              Don't have an account? Register
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

export function RegisterPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: Event) => {
    e.preventDefault();
    const validation = validatePasswords(password, confirm);
    if (validation) {
      setError(validation);
      return;
    }
    setError("");
    setLoading(true);
    const result = await postJson(`/api/auth/register`, { email: email.trim(), password });
    setLoading(false);
    if (result.ok) {
      window.location.href = "/";
    } else {
      setError(result.detail || "Registration failed");
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body p-4">
        <AuthCardHeader title="Create Account" subtitle="Register for a Shoreguard account." />
        <form onSubmit={(e) => void submit(e)}>
          <div class="mb-3">
            <input
              type="email"
              class="form-control"
              placeholder="Email"
              autocomplete="email"
              autofocus
              required
              value={email}
              onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
            />
          </div>
          <PasswordPair
            password={password}
            confirm={confirm}
            setPassword={setPassword}
            setConfirm={setConfirm}
          />
          {error && <div class="text-danger small mb-3">{error}</div>}
          <button type="submit" class="btn btn-success w-100" disabled={loading}>
            <i class="bi bi-person-plus me-1" />
            Register
          </button>
        </form>
        <div class="text-center mt-3">
          <a href="/login" class="text-muted small">
            Already have an account? Sign in
          </a>
        </div>
      </div>
    </div>
  );
}

export function SetupPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: Event) => {
    e.preventDefault();
    const validation = validatePasswords(password, confirm);
    if (validation) {
      setError(validation);
      return;
    }
    setError("");
    setLoading(true);
    const result = await postJson(`/api/auth/setup`, { email: email.trim(), password });
    setLoading(false);
    if (result.ok) {
      window.location.href = safeNext();
    } else {
      setError(result.detail || "Setup failed");
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body p-4">
        <AuthCardHeader
          title="Welcome to Shoreguard"
          subtitle="Create your admin account to get started."
        />
        <form onSubmit={(e) => void submit(e)}>
          <div class="mb-3">
            <label class="form-label">Email</label>
            <input
              type="email"
              class="form-control"
              placeholder="admin@example.com"
              autocomplete="email"
              autofocus
              required
              value={email}
              onInput={(e) => setEmail((e.target as HTMLInputElement).value)}
            />
          </div>
          <PasswordPair
            password={password}
            confirm={confirm}
            setPassword={setPassword}
            setConfirm={setConfirm}
            labels
          />
          {error && <div class="text-danger small mb-3">{error}</div>}
          <button type="submit" class="btn btn-success w-100" disabled={loading}>
            <i class="bi bi-check-circle me-1" />
            Create Admin Account
          </button>
        </form>
      </div>
    </div>
  );
}

export function InvitePage() {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e: Event) => {
    e.preventDefault();
    const validation = validatePasswords(password, confirm);
    if (validation) {
      setError(validation);
      return;
    }
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setError("Missing invite token");
      return;
    }
    setError("");
    setLoading(true);
    const result = await postJson(`/api/auth/accept-invite`, { token, password });
    setLoading(false);
    if (result.ok) {
      window.location.href = "/";
    } else {
      setError(result.detail || "Failed to accept invite");
    }
  };

  return (
    <div class="card sg-card-themed">
      <div class="card-body p-4">
        <AuthCardHeader
          title="Welcome to Shoreguard"
          subtitle="Set your password to activate your account."
        />
        <form onSubmit={(e) => void submit(e)}>
          <PasswordPair
            password={password}
            confirm={confirm}
            setPassword={setPassword}
            setConfirm={setConfirm}
            labels
          />
          {error && <div class="text-danger small mb-3">{error}</div>}
          <button type="submit" class="btn btn-success w-100" disabled={loading}>
            <i class="bi bi-check-circle me-1" />
            Set Password & Login
          </button>
        </form>
      </div>
    </div>
  );
}

export default LoginPage;
