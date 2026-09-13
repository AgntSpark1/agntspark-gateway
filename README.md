# agntspark-gateway

API Gateway + Auth + Control Plane for the AgntSpark AI Agent hosting platform.

This service implements the REST API contract already assumed by
[`agntspark-sdk`](../agntspark-sdk) and [`agntspark-console`](../agntspark-console):
`Authorization: Bearer <token>` auth (accepting either a JWT session token or
a long-lived API key), served under `/v1`. It's also the Control Plane: it
owns agent identity/config in Postgres, drives
[`agntspark-core`](../agntspark-core)'s Docker-backed `AgentRuntime` to
actually deploy/scale/stop containers, and runs a background scheduler that
health-checks and auto-scales running agents.

## Quick start

```bash
cp .env.example .env
docker compose up -d postgres redis
pip install -e ".[dev]"
alembic upgrade head
uvicorn agntspark_gateway.main:app --reload
```

Deploying agents requires a local Docker daemon and the `agntspark-net`
network (`docker network create agntspark-net`) — `docker compose up`
creates both automatically for the containerized gateway; running the
gateway directly on the host needs a Docker socket the current user can
reach (`unix:///var/run/docker.sock` by default).

`docker compose up` (no service names) also brings up Prometheus
(`:9090`) and Grafana (`:3000`, anonymous viewer access, Prometheus
datasource pre-provisioned) alongside Postgres/Redis/gateway — see
Observability below.

## Endpoints

| Method & path | Auth | Notes |
|---|---|---|
| `POST /v1/auth/register` | none | `{email,password,name}` → JWT |
| `POST /v1/auth/login` | none | `{email,password}` → JWT |
| `GET /v1/auth/me` | Bearer (JWT or key) | current user |
| `POST /v1/api-keys` | JWT only | mint a new API key (raw value shown once) |
| `GET /v1/api-keys` | Bearer | list caller's own keys |
| `DELETE /v1/api-keys/{id}` | Bearer | revoke a key |
| `POST /v1/agents` | Bearer | create (+ deploy immediately if `deploy` is given) |
| `GET /v1/agents` | Bearer | list caller's own agents (filter by `status`/`tag`) |
| `GET /v1/agents/{id}` | Bearer | get one |
| `DELETE /v1/agents/{id}` | Bearer | stop + remove containers, delete the record |
| `POST /v1/agents/{id}/deploy` | Bearer | (re)deploy, optionally with a new `DeployConfig` |
| `POST /v1/agents/{id}/scale` | Bearer | manual scale up/down |
| `GET /v1/agents/{id}/logs` | Bearer | recent container logs (tail-based) |
| `GET /v1/agents/{id}/logs/stream` | Bearer | SSE, polls every 2s |
| `GET /v1/agents/{id}/metrics` | Bearer | live CPU/memory from Docker stats |
| `GET /v1/agents/{id}/metrics/stream` | Bearer | SSE, polls every `interval`s (min 5) |
| `GET /metrics` | none | Prometheus scrape endpoint (platform-wide, not per-agent) |

## Auth model

- **JWT** (`security/jwt.py`): HS256, 60 min expiry, issued at register/login.
- **API keys** (`security/api_keys.py`): reuses `agntspark_core.auth.APIKey`'s
  `agnt_<token_urlsafe(32)>` format and SHA-256 hashing, backed by Postgres
  instead of the in-memory `APIKeyStore` in agntspark-core.
- A single `Authorization: Bearer <token>` header accepts either — the
  `agnt_` prefix unambiguously distinguishes an API key from a JWT.

See `agntspark_gateway/roles.py` for the `Role` (core) ↔ `"viewer"/"developer"/"admin"`
(console) string mapping.

## Agent Runtime / Control Plane

- `agntspark_gateway/runtime.py` bridges async request handlers to
  `agntspark_core.runtime.AgentRuntime`'s blocking docker-py calls via
  `asyncio.to_thread`. One `AgentRuntime` instance lives on `app.state` for
  the process's lifetime and is reconciled against live Docker state at
  startup (`runtime.reconcile()`), so a gateway restart doesn't orphan
  already-deployed agents.
- `agntspark_gateway/scheduler.py` runs a background loop
  (`AGNTSPARK_GATEWAY_SCHEDULER_INTERVAL_SECONDS`, default 30s) that
  health-checks every active agent, syncs its DB status/replica count from
  live Docker state, and evaluates auto-scaling for agents with
  `auto_scale: true`.
- **Secrets**: `DeployConfig.env` entries with `secret: true` are
  Fernet-encrypted (`security/secrets.py`) before being stored in the
  `agents` table and only decrypted right before container injection —
  this is the MVP's "secret store." Generate a real production key with
  `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
  and set `AGNTSPARK_GATEWAY_SECRET_ENCRYPTION_KEY`.
- **Single-host, Docker-only.** `AgentRuntime` tracks containers in-memory
  on one Docker host — this matches the platform's current Oracle Cloud A1
  Flex single/few-node target. Kubernetes support (the docs' "K8s Pod
  Manager") is future work, not implemented here or in agntspark-core.
- **No build pipeline.** Deploys need a pre-built `image` (or omit both
  `image`/`build_path` to use the platform's default generic runtime
  image). Supplying only `build_path` returns `501 BUILD_NOT_SUPPORTED`.
- **Metrics are partial by design.** `cpu_percent`/`memory_mb`/`replicas`
  in `/metrics` are real, live Docker stats. `request_count`/`request_rate`/
  latency percentiles are always 0 — they need an in-path request proxy
  that doesn't exist yet.
- **Login/register rate limiting**: `security/rate_limit.py` is a
  Redis-backed fixed-window counter per client IP
  (`AGNTSPARK_GATEWAY_LOGIN_RATE_LIMIT_MAX_ATTEMPTS`, default 10 per
  `AGNTSPARK_GATEWAY_LOGIN_RATE_LIMIT_WINDOW_SECONDS`, default 300s).
  Fails **open** (allows the request) if Redis is unreachable or
  `AGNTSPARK_GATEWAY_REDIS_URL` is unset — an MVP tradeoff that never
  blocks legitimate logins over a Redis outage, at the cost of losing
  brute-force protection during that outage.

## Data layer

- **Postgres** via `pgvector/pgvector:pg16` (drop-in for `postgres:16`,
  extension pre-built). `alembic/versions/0003_pgvector.py` enables the
  `vector` extension — no table uses it yet; it's there so an
  embedding-backed agent-memory/RAG feature doesn't need a database swap
  later.
- **Redis** (`docker-compose.yml`) currently backs the login/register rate
  limiter above. `agntspark_core.config.MemoryConfig` (per-agent
  conversation history) also defaults to a `redis_url` — the gateway
  doesn't wire that up itself; an agent template that wants it connects
  directly using whatever `REDIS_URL`-style env var you inject via
  `DeployConfig.env`.

## Observability

- **`GET /metrics`**: Prometheus exposition format, backed by
  `agntspark_core.metrics` — the same registry `AgentRuntime` already
  updates on every deploy/scale/health-check
  (`agntspark_containers_active`, `_container_cpu_percent`,
  `_container_memory_mb`, `_agents_total`, ...). Real numbers reflecting
  what this gateway process is actually doing, not a separate/fake source.
- **Prometheus + Grafana** (`docker-compose.yml`, `observability/`):
  Prometheus scrapes the gateway's `/metrics` every 15s
  (`observability/prometheus.yml`); Grafana comes up with that Prometheus
  pre-provisioned as its default datasource
  (`observability/grafana-datasource.yml`) and anonymous viewer access
  for local dev — no dashboards are pre-built yet, build them in the
  Grafana UI against the `agntspark_*` metric names above.
- **Deliberately not built**: Jaeger (distributed tracing) and an ELK/log-
  aggregation stack. There's one service to trace today (no inter-service
  request chains yet) and Docker's own log buffer plus `/logs` already
  covers debugging at this scale — revisit both once there's an actual
  multi-hop request path or log volume that outgrows `docker logs`.

## Known follow-ups (not in this slice)

1. `agntspark-console`'s dev proxy forwards `/api/*`; this gateway serves bare
   `/v1/*` (matching the SDK). Simplest fix is updating the console's
   `VITE_API_URL` rather than adding an `/api` prefix here.
2. ~~No path to create the first `ADMIN` user~~ — `register` still always
   issues `VIEWER` by design (no public API should let a caller self-grant
   elevated privileges), but `scripts/set_user_role.py --email ... --role
   admin` now does this as a maintenance operation. **Role doesn't gate
   anything yet** — `require_role()` exists (`security/dependencies.py`)
   but no route uses it; every `/v1/agents*`/`/v1/api-keys*` check is
   ownership-only. Wiring real RBAC into those routes is team-management
   work, still not started.
3. Multi-project scoping (`X-AgntSpark-Project`) and refresh tokens are
   reserved (config fields exist) but not implemented.
4. No per-agent public ingress/routing — `AgentResponse.url` is always
   `null`. Reaching a deployed agent today means exec-ing into Docker
   directly; a real ingress is Phase 3-shaped work.
5. Request-level metrics (`request_count`, latency percentiles) need an
   in-path proxy — not built yet, see above.
6. Log/metric streaming is poll-based (every 2s / `interval`s), not a true
   push from Docker's log-follow API — simpler, sufficient for the current
   scale, revisit if polling overhead becomes real.
