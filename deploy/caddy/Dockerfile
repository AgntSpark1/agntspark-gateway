# Caddy with the Cloudflare DNS provider, so it can obtain one wildcard
# certificate for every agent hostname (Caddyfile, AGENT_TLS=wildcard).
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
