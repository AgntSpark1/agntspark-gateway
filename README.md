# agntspark-gateway

API Gateway + Auth service for the AgntSpark AI Agent hosting platform.

This service implements the REST API contract already assumed by
[`agntspark-sdk`](../agntspark-sdk) and [`agntspark-console`](../agntspark-console):
`Authorization: Bearer <token>` auth (accepting either a JWT session token or
a long-lived API key), served under `/v1`.

**Current scope**: user registration/login + API key management only.
`/v1/agents/*` routes exist as reserved stubs (return `501`) — real Agent
CRUD/deploy/scale/logs logic is a separate future task (Agent Runtime).

## Quick start

```bash
cp .env.example .env
docker compose up -d postgres
pip install -e ".[dev]"
alembic upgrade head
uvicorn agntspark_gateway.main:app --reload
```

## Endpoints

| Method & path | Auth | Notes |
|---|---|---|
| `POST /v1/auth/register` | none | `{email,password,name}` → JWT |
| `POST /v1/auth/login` | none | `{email,password}` → JWT |
| `GET /v1/auth/me` | Bearer (JWT or key) | current user |
| `POST /v1/api-keys` | JWT only | mint a new API key (raw value shown once) |
| `GET /v1/api-keys` | Bearer | list caller's own keys |
| `DELETE /v1/api-keys/{id}` | Bearer | revoke a key |
| `ANY /v1/agents...` | Bearer | `501` placeholder (auth still enforced) |

## Auth model

- **JWT** (`security/jwt.py`): HS256, 60 min expiry, issued at register/login.
- **API keys** (`security/api_keys.py`): reuses `agntspark_core.auth.APIKey`'s
  `agnt_<token_urlsafe(32)>` format and SHA-256 hashing, backed by Postgres
  instead of the in-memory `APIKeyStore` in agntspark-core.
- A single `Authorization: Bearer <token>` header accepts either — the
  `agnt_` prefix unambiguously distinguishes an API key from a JWT.

See `agntspark_gateway/roles.py` for the `Role` (core) ↔ `"viewer"/"developer"/"admin"`
(console) string mapping.

## Known follow-ups (not in this slice)

1. `agntspark-console`'s dev proxy forwards `/api/*`; this gateway serves bare
   `/v1/*` (matching the SDK). Simplest fix is updating the console's
   `VITE_API_URL` rather than adding an `/api` prefix here.
2. No path to create the first `ADMIN` user yet (`register` always issues
   `VIEWER`) — needs a seed script or manual DB update before team-management
   work starts.
3. Redis, multi-project scoping (`X-AgntSpark-Project`), and refresh tokens
   are reserved (config fields exist) but not implemented.
