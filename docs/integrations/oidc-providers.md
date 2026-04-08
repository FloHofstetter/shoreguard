# OIDC Provider Setup

Step-by-step instructions for configuring common identity providers with
ShoreGuard. For general OIDC configuration, see the
[OIDC / SSO guide](../admin/oidc.md).

The **callback URL** for all providers is:

```
https://<your-shoreguard-domain>/api/auth/oidc/callback
```

---

## Google Workspace

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Navigate to **APIs & Services > Credentials**
4. Click **Create Credentials > OAuth client ID**
5. Application type: **Web application**
6. Add the ShoreGuard callback URL to **Authorized redirect URIs**
7. Copy the **Client ID** and **Client secret**

```json
{
  "name": "google",
  "display_name": "Google",
  "issuer": "https://accounts.google.com",
  "client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
  "client_secret": "GOCSPX-YOUR_SECRET"
}
```

---

## Microsoft Entra ID (Azure AD)

1. Go to the [Azure Portal](https://portal.azure.com/)
2. Navigate to **Microsoft Entra ID > App registrations > New registration**
3. Name: `ShoreGuard`
4. Supported account types: choose based on your needs (single tenant or multi-tenant)
5. Redirect URI: **Web** → your ShoreGuard callback URL
6. Under **Certificates & secrets**, create a new client secret
7. Copy the **Application (client) ID** and **Client secret value**
8. Note your **Directory (tenant) ID**

```json
{
  "name": "entra",
  "display_name": "Microsoft Entra",
  "issuer": "https://login.microsoftonline.com/YOUR_TENANT_ID/v2.0",
  "client_id": "YOUR_APPLICATION_ID",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

### Role mapping via groups

To map Entra group membership to ShoreGuard roles:

1. In the app registration, go to **Token configuration > Add groups claim**
2. Select **Security groups** and choose **Group ID** as the claim format
3. Add the group object IDs to the role mapping:

```json
{
  "name": "entra",
  "display_name": "Microsoft Entra",
  "issuer": "https://login.microsoftonline.com/YOUR_TENANT_ID/v2.0",
  "client_id": "YOUR_APPLICATION_ID",
  "client_secret": "YOUR_CLIENT_SECRET",
  "role_mapping": {
    "claim": "groups",
    "values": {
      "ADMIN_GROUP_OBJECT_ID": "admin",
      "OPS_GROUP_OBJECT_ID": "operator"
    }
  }
}
```

---

## Okta

1. Go to your Okta admin console
2. Navigate to **Applications > Create App Integration**
3. Sign-in method: **OIDC — OpenID Connect**
4. Application type: **Web Application**
5. Add the ShoreGuard callback URL to **Sign-in redirect URIs**
6. Copy the **Client ID** and **Client secret**
7. Note your Okta domain (e.g. `dev-12345.okta.com`)

```json
{
  "name": "okta",
  "display_name": "Okta",
  "issuer": "https://YOUR_DOMAIN.okta.com",
  "client_id": "YOUR_CLIENT_ID",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

---

## Keycloak

1. Open the Keycloak admin console
2. Select your realm (or create a new one)
3. Navigate to **Clients > Create client**
4. Client type: **OpenID Connect**
5. Client ID: `shoreguard`
6. Enable **Client authentication** (confidential client)
7. Add the ShoreGuard callback URL to **Valid redirect URIs**
8. Copy the **Client secret** from the **Credentials** tab
9. Note your realm URL

```json
{
  "name": "keycloak",
  "display_name": "Keycloak",
  "issuer": "https://YOUR_KEYCLOAK_HOST/realms/YOUR_REALM",
  "client_id": "shoreguard",
  "client_secret": "YOUR_CLIENT_SECRET"
}
```

### Role mapping via realm roles

Keycloak includes roles in the `realm_access.roles` claim by default. To map
these to ShoreGuard roles, use a custom claim or configure Keycloak to include
roles in a top-level claim:

```json
{
  "role_mapping": {
    "claim": "roles",
    "values": {
      "shoreguard-admin": "admin",
      "shoreguard-operator": "operator"
    }
  }
}
```

---

## Multiple providers

Configure multiple providers as an array in `SHOREGUARD_OIDC_PROVIDERS_JSON`.
Each provider gets its own login button on the login page.

```bash
export SHOREGUARD_OIDC_PROVIDERS_JSON='[
  {"name": "google", "display_name": "Google", "issuer": "https://accounts.google.com", "client_id": "...", "client_secret": "..."},
  {"name": "entra", "display_name": "Microsoft", "issuer": "https://login.microsoftonline.com/TENANT/v2.0", "client_id": "...", "client_secret": "..."}
]'
```
