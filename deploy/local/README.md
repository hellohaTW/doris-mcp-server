<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Local Docker deployment

[English](README.md) | [简体中文](README.zh-CN.md)

`docker-compose.yml` in this directory runs **only the Doris MCP Server** in
one container, on Streamable HTTP, published on loopback. It is meant for a
development machine. The root `docker-compose.yml` is a different thing: a
full stack with Doris, Redis, Prometheus, Grafana, and Nginx.

The container binds `0.0.0.0` internally, which the Server treats as a
non-loopback bind: it refuses to start unauthenticated. This file therefore
enables static token authentication, and step 2 below is not optional.

## Prerequisites

- Docker Engine 20.10+ with the Compose v2 plugin (`docker compose version`);
- a reachable Apache Doris 2.0.0+ FE MySQL endpoint, or the optional `doris`
  profile described at the end;
- a Doris account for the Server. Read-only is enough — Doris RBAC stays the
  final authority over what the Server can see.

## 1. Configure the Doris route

```bash
cd deploy/local
cp .env.example .env
```

Edit `.env` and set `DORIS_HOST`, `DORIS_PORT`, `DORIS_USER`, and
`DORIS_DATABASE`. For a Doris running on your own machine, keep
`DORIS_HOST=host.docker.internal`: `localhost` inside a container is the
container itself, not your host.

## 2. Create the two secret files

Secrets are files, not environment variables. Both must contain exactly one
non-empty line; the Server rejects empty or multi-line secret files.

```bash
# The password of the Doris account above.
printf '%s' 'your-doris-password' > secrets/doris_password

# The bearer token your MCP Host will send. Must be at least 32 characters
# with at least 10 distinct characters; placeholder-looking values are rejected.
python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' > secrets/mcp_static_token

chmod 644 secrets/doris_password secrets/mcp_static_token
```

`644` is deliberate. Compose bind-mounts these files into the container with
their host ownership and mode, and the image runs as the non-root `doris`
account, so a `600` file owned by your host user is unreadable inside the
container and the Server exits with `is not a readable file`. Keep the files
`600` only if you also give them the container's uid
(`docker compose exec doris-mcp-server id -u`), and remember that `644` means
every local user can read your Doris password.

`secrets/.gitignore` keeps both out of git. Print the token when you need it
again: `cat secrets/mcp_static_token`.

## 3. Build and start

```bash
docker compose up -d --build
```

The first build takes a few minutes. After that, `--build` is only needed when
the server source or `requirements.txt` changes.

```bash
docker compose ps          # STATUS should reach "healthy"
docker compose logs -f     # follow logs; Ctrl+C stops following, not the container
```

`healthy` here means the Doris-backed readiness probe passed, so a healthy
container is also proof the Doris route works.

## 4. Verify

```bash
TOKEN="$(cat secrets/mcp_static_token)"

# Process liveness, no authentication.
curl --fail http://127.0.0.1:3000/live

# Doris-backed readiness.
curl --fail http://127.0.0.1:3000/ready

# One authenticated MCP initialize round trip.
curl -sS http://127.0.0.1:3000/mcp \
  -H "Authorization: Bearer ${TOKEN}" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2026-07-28",
          "capabilities": {},
          "clientInfo": {"name": "curl", "version": "0"}
        }
      }'
```

A JSON-RPC result naming the server means the container, the authentication
boundary, and the protocol layer are all working.

## 5. Point an MCP Host at it

Endpoint `http://127.0.0.1:3000/mcp`, header
`Authorization: Bearer <contents of secrets/mcp_static_token>`. For a Host
that reads a JSON config:

```json
{
  "mcpServers": {
    "doris": {
      "type": "http",
      "url": "http://127.0.0.1:3000/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_TOKEN"
      }
    }
  }
}
```

Hosts limited to handshake-era Streamable HTTP (Dify 1.16.1, MCP
`2025-06-18`) need `ENABLE_LEGACY_HTTP_ADAPTER=true` in `.env` and the URL
`http://127.0.0.1:3000/mcp/legacy`. See
[Host integrations](../../docs/integrations/hosts.md).

## Day-to-day commands

```bash
docker compose ps                     # status
docker compose logs -f doris-mcp-server
docker compose restart doris-mcp-server   # after editing .env
docker compose up -d --build          # after changing server source
docker compose down                   # stop and remove containers
docker compose down -v                # also delete the optional Doris volumes
```

Changing `.env` requires `docker compose up -d` (or `restart`); a running
container does not pick up new values on its own.

## Optional: a throwaway Doris in the same file

If you have no Doris at all, the `doris` profile starts a single-FE,
single-BE cluster next to the Server. It is for local experiments only.

```bash
# Linux hosts need this before starting Doris BE, or the BE will not come up.
sudo sysctl -w vm.max_map_count=2000000

docker compose --profile doris up -d --build
```

Then set in `.env` and restart the Server:

```bash
DORIS_HOST=doris-fe
DORIS_USER=root
```

The FE and BE mount the same `secrets/doris_password` at
`/etc/basic_auth/password`, the convention the root `docker-compose.yml` uses
for these images. Give the containers a minute: the BE registers with the FE
before readiness turns green. If `/ready` then fails on authentication, set the
password on the cluster yourself and restart the Server:

```bash
mysql -h 127.0.0.1 -P 9030 -u root
# ALTER USER 'root'@'%' IDENTIFIED BY '<contents of secrets/doris_password>';
```

`docker compose down -v` deletes the Doris volumes and all data in them.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Container exits immediately, log says it refuses an unauthenticated non-loopback bind | `secrets/mcp_static_token` missing or weak | Regenerate it per step 2 |
| `TOKEN_ADMIN_FILE is not a readable file` | Secret file absent, or unreadable by the container's non-root user | Create it, then `chmod 644` |
| `must contain one non-empty line` | Secret file empty or has extra lines | Rewrite with `printf '%s'`, no newline |
| `/ready` fails, `/live` passes | Doris route wrong or unreachable | Check `DORIS_HOST`/port; from your host, `mysql -h <host> -P 9030 -u <user> -p` |
| Connection refused to a Doris on your machine | `localhost` used inside the container | Use `host.docker.internal` |
| `401` on `/mcp` | Missing or wrong bearer token | Send `Authorization: Bearer $(cat secrets/mcp_static_token)` |
| `400`/`403` before any tool runs | Host or Origin header not allowlisted | Add the exact host to `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` |
| Port 3000 already allocated | Something else owns the port | Set `MCP_HTTP_PORT` in `.env` |

More detail: [Troubleshooting](../../docs/operations/troubleshooting.md),
[Deployment](../../docs/operations/deployment.md), and the
[Configuration reference](../../docs/reference/configuration.md).

## Security notes

This file is deliberately loopback-only. Before exposing it further, read the
[Security model](../../docs/security/security-model.md). At minimum: keep the
published port on `127.0.0.1`, terminate TLS in a proxy rather than here, give
the Doris account only the reads it needs, and never commit `secrets/` or
`.env`.
