# OneDrive + Microsoft Graph setup

Template Hub can fetch, permission-check, version, and update documents in OneDrive using Microsoft Graph.

## Modes

| `GRAPH_MODE` | Behavior |
|---|---|
| `mock` (default) | Local demo drive with sample files, ACL, and versions — no Azure app needed |
| `live` | Real OneDrive via MSAL sign-in + Graph APIs |

## What was added

1. **Fetch files** — list/search OneDrive under `YMSLI-Template-Hub`
2. **Access control** — effective `read` / `write` / `owner` from Graph permissions
3. **GitHub-style versions**
   - OneDrive native snapshots (`/items/{id}/versions`)
   - Hub commits with message, author, sha, parent (like git commits)
   - Restore previous version (write access required)
4. **Chat intents**
   - `Search OneDrive for QMM`
   - `Show OneDrive version history for QMM`
   - `Check my access to QMM on OneDrive`

## Azure App Registration (live mode)

1. Azure Portal → **App registrations** → New registration
2. Name: `YMSLI Template Hub`
3. Supported account types: single tenant (or multi if needed)
4. Redirect URI (SPA): `http://localhost:5173`
5. API permissions (delegated):
   - `User.Read`
   - `Files.ReadWrite.All` (or `Files.ReadWrite`)
6. Grant admin consent if required by your tenant
7. Copy **Application (client) ID** and tenant ID into `backend/.env`:

```env
GRAPH_MODE=live
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_ID=<your-client-id>
AZURE_REDIRECT_URI=http://localhost:5173
```

8. Also set in frontend if you prefer build-time overrides (optional):

```env
# frontend/.env
VITE_API_BASE=
```

9. Restart backend + frontend, click **Sign in with Microsoft**.

## Access rules (demo mock)

| User switch | OneDrive identity | QMM file | MOM / Plan |
|---|---|---|---|
| `consultant` / `approver` | `demo.user@ymsli.com` | **read-only** | **write** |
| `joiner` | `joiner@ymsli.com` | **read** (if granted) | limited |

In live mode, access is taken from real Graph `/permissions` on each drive item.

## API surface

- `GET /api/onedrive/auth-config`
- `GET /api/onedrive/me`
- `GET /api/onedrive/files?folder=Templates`
- `POST /api/onedrive/search`
- `GET /api/onedrive/files/{id}/access`
- `GET /api/onedrive/files/{id}/versions`
- `GET /api/onedrive/files/{id}/content`
- `POST /api/onedrive/upload`
- `POST /api/onedrive/commit`
- `POST /api/onedrive/restore`
- `POST /api/onedrive/push-generated`

Pass the MSAL access token as `X-Graph-Token` (or `Authorization: Bearer ...`).

## Version model (like GitHub)

```
Hub commit (sha, message, author, parent_sha)
        │
        ▼
OneDrive item content update  →  creates a Graph version snapshot
        │
        ▼
Restore version  →  new hub commit ("revert: ...")
```

Uploads/commits through the hub always require **write** (or owner). Read-only users can browse and view history only.
