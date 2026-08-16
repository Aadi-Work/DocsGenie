# Admin / Employee versioning — implementation notes

## Modified files
- `backend/app/services/auth.py` — roles `admin` | `employee`, demo users, legacy role migration
- `backend/app/api/auth_routes.py` — `require_admin`, public register → employee only
- `backend/app/models/schemas.py` — version statuses `draft|published|archived`, diff lines, restore/create template requests, `template_version` on compose response
- `backend/app/services/catalog.py` — ACL for admin/employee, latest published helper, seed status normalize
- `backend/app/services/versioning.py` — immutable append-only versions, restore-as-new, LCS GitHub-like diff
- `backend/app/services/ingest.py` — always use latest published; record generated doc version
- `backend/app/api/routes.py` — role-enforced APIs (create/edit/compare/restore admin-only)
- `backend/app/deps.py` — audit + generated docs wiring
- `backend/app/main.py` — seed auth/audit/generated docs
- `frontend/src/App.tsx` — role-based dashboards
- `frontend/src/AuthScreen.tsx` — admin/employee demo credentials
- `frontend/src/api.ts` — new APIs
- `frontend/src/styles.css` — dashboards + green/red diff UI

## New files
- `backend/app/services/audit.py` — audit_events table + logging
- `backend/app/services/generated_docs.py` — generated_documents registry with template_version
- `frontend/src/AdminDashboard.tsx` — template mgmt, edit→new version, history, compare, restore, audit
- `frontend/src/EmployeeDashboard.tsx` — generate from latest published only
- `VERSIONING.md` (this file)

## Database / schema changes (SQLite `template_hub.db`)
| Table | Purpose |
|-------|---------|
| `users.role` | now `admin` / `employee` (legacy roles migrated) |
| `templates.payload` | version `status`: published/archived/draft; `previous_version`; full snapshots |
| `audit_events` | user, action, template, old/new version, timestamp, description |
| `generated_documents` | filename, template_id, **template_version**, generated_by, created_at |

## API changes
| Method | Path | Who |
|--------|------|-----|
| POST | `/api/templates` | Admin create |
| GET | `/api/templates` | Both (employee sees published only in versions list) |
| POST | `/api/templates/{id}/versions` | Admin — always creates **new** version |
| POST | `/api/templates/{id}/versions/restore` | Admin — restore creates **new** version |
| GET | `/api/templates/{id}/versions/compare` | Admin — GitHub-like unified diff |
| GET | `/api/audit` | Admin |
| GET | `/api/generated-documents` | Both (scoped) |
| POST | `/api/compose` | Both — uses **latest published** version; response includes `template_version` |

## Versioning logic
1. Editing never overwrites an existing version row’s content.
2. Save creates a new version (auto bump e.g. 1.2 → 1.3) with full snapshot + `previous_version`.
3. Publishing archives prior `published` versions (status only; content immutable).
4. Employees always generate against latest `published`.
5. Restore copies an old snapshot into a **new** version (history preserved).
6. Generated docs permanently store the version string used at generation time.

## GitHub-like diff
- Field-level LCS unified diff for description, placeholders, outline, questions, changelog, status.
- UI: green `+` added, red `-` removed, muted unchanged (see `.diff-added` / `.diff-removed`).

## Run & test
```bat
cd backend
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

cd frontend
npm install
npm run dev
```

### Admin test (`admin@ymsli.com` / `demo123`)
1. Open Template Management → select MOM.
2. Edit a placeholder → enter changelog → **Save as new version**.
3. Version history shows new published + previous archived.
4. Compare old → new → see green/red diff.
5. Restore an old version → creates a new version (does not delete history).
6. Check Audit trail.

### Employee test (`employee@ymsli.com` / `demo123`)
1. Select template — shows Latest Version badge.
2. Enter content → Generate — document uses latest published.
3. Confirm result shows `Version used: vX.Y`.
4. Attempting admin APIs returns 403.
