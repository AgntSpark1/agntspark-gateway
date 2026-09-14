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

## Production deploy (single host)

`deploy/` holds a one-host production stack: Postgres + Redis + gateway +
Caddy (auto-HTTPS, serves the console SPA and proxies `/v1/*` and
`/healthz`). On a fresh Ubuntu 24.04 VM:

```bash
sudo API_DOMAIN=agntapi.agntspark.com AGENT_DOMAIN=run.agntspark.com bash deploy/bootstrap.sh
```

`AGENT_DOMAIN` needs a DNS-only (not proxied) wildcard record
(`*.run.agntspark.com`) pointing at the host — see Agent ingress below.

The script is idempotent — re-run it to pull `main` of the gateway,
console, core and templates repos, rebuild the agent images
(`agntspark/agent-runtime` plus one `agntspark/template-<name>` per official
template, local to the host) and restart. It also hardens the host:

- `ufw` allows only 22/80/443; Docker publishes nothing except Caddy
  (`daemon.json` binds default port mappings to `127.0.0.1`).
- Tenant containers can't reach the cloud metadata server
  (`agntspark-block-metadata.service` drops 169.254.169.254 in
  `DOCKER-USER`); run the VM without a cloud service account anyway.
  On GCE that address is also the VPC DNS resolver, so `daemon.json`
  points containers at public resolvers instead — without it, no
  container can resolve anything.
- Postgres/Redis sit on an internal `platform` network; agents only join
  `agntspark-net`.
- Agents can't reach each other: `agntspark-net` uses a fixed bridge
  (`agnt-agents`, `10.89.0.0/16`) and
  `agntspark-isolate-agents.service` drops container-to-container traffic
  on it except to/from Caddy (`10.89.0.2`). Tenants talk to each other only
  via public URLs. The unit loads `br_netfilter` and sets
  `net.bridge.bridge-nf-call-iptables=1` first — same-bridge traffic
  otherwise never reaches iptables and the rules silently do nothing.
- `/metrics` is 404 at the edge.
- Nightly database backups: `agntspark-backup.timer` runs `deploy/backup.sh`
  at 03:17 UTC, writing a `pg_dump` archive to `/opt/agntspark/backups`
  (mode 600, 14 days kept; restore command in the script). They share the
  VM's disk, so copy them off-host to survive losing the machine.

Secrets are generated once into `/opt/agntspark/.env` (mode 600). **Back
up `SECRET_ENCRYPTION_KEY`** — losing it makes every stored agent secret
unreadable.

## Agent ingress

Every agent gets a stable public URL, `https://<slug>.<agent_base_domain>`
(`AgentResponse.url`; null when `AGNTSPARK_GATEWAY_AGENT_BASE_DOMAIN` is
unset). The slug is `<agent-name>-<6 random chars>` because agent ids
(`agt_…`) are mixed-case with `_` and can't be DNS labels.

Caddy terminates these hostnames (`deploy/Caddyfile`) using two internal,
non-public gateway endpoints (`routers/ingress.py`):

- `GET /internal/ingress/tls-ask?domain=` — on-demand TLS permission: a
  certificate is only issued for hostnames of existing agents.
- `GET /internal/ingress/route` (`forward_auth`, requires
  `X-Agnt-Internal-Token` = `AGNTSPARK_GATEWAY_INGRESS_INTERNAL_TOKEN`) —
  maps `X-Agnt-Host` to a random running replica and returns it as
  `X-Agnt-Upstream: <container-ip>:<deploy.port>`; unknown agent → 404,
  nothing running → 503, both shown to the visitor.

Traffic goes straight from Caddy to the container over `agntspark-net`;
the gateway is not on that network. Agents must listen on
`DeployConfig.port` (default 8080). Each new agent hostname costs one Let's
Encrypt certificate (50 new certificates per registered domain per week),
fine for an invite-only Alpha; a wildcard certificate via DNS-01 is the
scale-up path.

## Endpoints

| Method & path | Auth | Notes |
|---|---|---|
| `POST /v1/auth/register` | none | `{email,password,name,invite_code?}` → JWT |
| `GET /v1/auth/registration` | none | `{mode}`: `open`, `invite` (register needs `invite_code`) or `closed` |
| `POST /v1/auth/login` | none | `{email,password}` → JWT |
| `GET /v1/auth/me` | Bearer (JWT or key) | current user |
| `POST /v1/api-keys` | JWT only | mint a new API key (raw value shown once) |
| `GET /v1/api-keys` | Bearer | list caller's own keys |
| `DELETE /v1/api-keys/{id}` | Bearer | revoke a key |
| `POST /v1/admin/invites` | admin | `{note?,max_uses?,expires_in_days?}` → invite (raw `code` shown once) |
| `GET /v1/admin/invites` | admin | list invites with status (no raw codes) |
| `DELETE /v1/admin/invites/{id}` | admin | revoke an invite |
| `GET /v1/admin/users` | admin | users with role, plan, active flag and agent count |
| `PATCH /v1/admin/users/{id}` | admin | `{role?,plan?,is_active?}` (can't demote/deactivate yourself) |
| `GET /v1/account/usage` | Bearer | caller's plan, limits and current usage |
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
| `GET /metrics` | none | Prometheus scrape endpoint (platform-wide, not per-agent; blocked at the edge in production) |

## Auth model

- **JWT** (`security/jwt.py`): HS256, 60 min expiry, issued at register/login.
- **API keys** (`security/api_keys.py`): reuses `agntspark_core.auth.APIKey`'s
  `agnt_<token_urlsafe(32)>` format and SHA-256 hashing, backed by Postgres
  instead of the in-memory `APIKeyStore` in agntspark-core.
- A single `Authorization: Bearer <token>` header accepts either — the
  `agnt_` prefix unambiguously distinguishes an API key from a JWT.
- **Registration mode** (`AGNTSPARK_GATEWAY_REGISTRATION_MODE`): `open`
  (default for development), `invite` (production default in
  `deploy/docker-compose.prod.yml`) or `closed`. In invite mode, `register`
  consumes one use of an admin-issued `inv_…` code (stored only as SHA-256,
  row-locked, and counted in the same transaction as the new account); every
  bad-code case returns the same `403 INVALID_INVITE`.
- **Roles are enforced**: new accounts are `developer`; creating, deploying,
  scaling and deleting agents and minting/revoking API keys need
  `developer`, `viewer` is read-only, `/v1/admin/*` needs `admin`.
- **Plan quotas** (`plans.py`, `services/quota_service.py`): each user has a
  `plan` (`free` or `pro`) limiting agent count, running replicas, total
  vCPU/memory across running replicas, and per-replica CPU/memory. Checked
  before create/deploy/scale (and before the scheduler auto-scales), so an
  over-quota request returns `403 QUOTA_EXCEEDED` with
  `details.{resource,limit,current,requested}` and changes nothing. Admins
  are exempt; admins set plans via `PATCH /v1/admin/users/{id}`.
- **Usage metering** (`services/metering_service.py`, table `usage_hours`):
  every scheduler tick adds running replicas × requested vCPU/memory × the
  seconds since the previous tick (capped at 3× the interval, so a stalled
  tick isn't counted as usage) to an hourly bucket per agent.
  `GET /v1/account/usage` includes this month's replica-, vCPU- and
  memory-GB-hours under `period`.

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
  on one Docker host — this matches the current single-VM production
  target (`deploy/`). Kubernetes support (the docs' "K8s Pod
  Manager") is future work, not implemented here or in agntspark-core.
- **Default runtime image.** Omit both `image` and `build_path` to run
  `settings.docker_image` (`agntspark/agent-runtime`, built from
  agntspark-core's Dockerfile), which serves runtime contract v1
  (`GET /health`, `POST /invoke` — see agntspark-core's README). The
  gateway passes `AGENT_ID`, `AGENT_NAME`, `SYSTEM_PROMPT` (empty when unset,
  so a template image keeps its own prompt), `LLM_MODEL` and
  `LLM_PROVIDER` (inferred from the model: `claude-*` → anthropic,
  `gemini-*` → google, else openai).
- **Bring your own LLM key.** `AgentConfigIn.api_key` is stored as a
  Fernet-encrypted secret env var named for the model's provider
  (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`), shown masked,
  and kept across redeploys that don't redefine it.
- **No build pipeline.** Supplying only `build_path` returns
  `501 BUILD_NOT_SUPPORTED`.
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

1. ~~Console `/api` vs gateway `/v1` mismatch~~ — the console now calls
   `/v1` directly (same origin in production via Caddy).
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
4. ~~No per-agent public ingress~~ — see Agent ingress above. Still
   missing: request-level auth in front of agents (they're public), and
   custom domains.
5. Request-level metrics (`request_count`, latency percentiles) need an
   in-path proxy — not built yet, see above.
6. Log/metric streaming is poll-based (every 2s / `interval`s), not a true
   push from Docker's log-follow API — simpler, sufficient for the current
   scale, revisit if polling overhead becomes real.
