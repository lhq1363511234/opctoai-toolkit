# opcToai Toolkit

[中文说明](README.md) · [Security](SECURITY.md) · [Apache-2.0](LICENSE) · [LINUX DO](https://linux.do)

A Docker Compose monorepo that packages independently deployable tools behind one gateway:

- **SMTP Console** — SMTP sending, temporary mailbox management, inbound message viewing, verification-code extraction, and provider health checks.
- **Cloudflare Mail** — a self-hosted Workers + Email Routing + D1 + R2 inbound mail service.
- **Grok Web** — a browser workflow and configuration dashboard. You must provide your own compliant external mail, proxy, and CPA service settings.

## Routes

| Route | Service | Internal port |
| --- | --- | --- |
| `/smtp/` | SMTP Console | 18430 |
| `/grok/` | Grok Web | 18425 |

## Design

- One repository and one `compose.yaml` manage the services.
- Runtime settings are separated from source code. Passwords, tokens, proxy settings, mailbox data, and generated files are excluded from Git and image layers.
- The Grok container includes Xvfb, Chromium, and shared-memory settings suitable for browser-based container workloads.
- SMTP, mailbox APIs, gateway, proxy, and remote services can be replaced through configuration.

## Module docs

| Module | Doc |
| --- | --- |
| SMTP Console + Cloudflare deployment | [apps/smtp/README.md](apps/smtp/README.md) |
| Cloudflare Worker mail source | [apps/cloudflare-mail/README.md](apps/cloudflare-mail/README.md) |
| Grok module | [apps/grok/README.md](apps/grok/README.md) |
| Grok Docker packaging | [apps/grok/docker/README.md](apps/grok/docker/README.md) |

## Quick start

```bash
git clone git@github.com:lhq1363511234/opctoai-toolkit.git
cd opctoai-toolkit
cp config/smtp/.env.example config/smtp/.env
cp config/grok/config.json.example config/grok/config.json
```

Edit the two config files with your own SMTP, mailbox, proxy, and remote service settings. **Never commit real credentials.**

To self-host Cloudflare inbound mail, deploy the Worker first using [apps/cloudflare-mail/README.md](apps/cloudflare-mail/README.md), then set:

```env
FREEMAIL_BASE=https://your-mail-worker.example.workers.dev
FREEMAIL_API_KEY=your-worker-jwt-token
```

Start:

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:8080/smtp/
http://localhost:8080/grok/
```

Use a different host port when needed:

```bash
TOOLKIT_PORT=18080 docker compose up -d --build
```


### 3. Grok published image quick start (no local build)

Prerequisites: fill `config/grok/config.json` (mailbox API + proxy, etc.).

Docker Hub:

```text
cirstein/grok-register-web:latest
```

```bash
docker pull cirstein/grok-register-web:latest
cp config/grok/config.json.example config/grok/config.json
# edit config/grok/config.json
docker compose up -d grok gateway
```

Open: `http://localhost:8080/grok/`


Details: [apps/grok/docker/README.md](apps/grok/docker/README.md)

## Persistent data

| Data | Location |
| --- | --- |
| SMTP send logs | Docker volume `smtp-data` |
| Temporary mailbox state | Docker volume `mail-console-data` |
| Grok runtime config, exports, and CPA files | `./config/grok/` |

Back up `config/grok/` and the Docker volumes before upgrades.

## Production guidance

1. Put the gateway behind HTTPS and authentication; do not expose raw service ports publicly.
2. Use separate, least-privilege credentials for SMTP and API integrations.
3. Apply network controls and request limits to proxy, mailbox API, and CPA integrations.
4. Keep Chromium, base images, and Python dependencies updated.
5. Follow applicable laws and upstream service terms. Do not use this project to bypass access controls, conduct bulk abuse, or operate accounts without authorization.

## Validation

```bash
python3 -m py_compile apps/smtp/app.py apps/grok/web_app.py
node --check apps/cloudflare-mail/src/server.js
docker compose config
```

## Layout

```text
apps/
  smtp/              SMTP Console source + full mail chain docs
  cloudflare-mail/   Cloudflare Worker mail source + wrangler template
  grok/              Grok Web source and Dockerfile
docker/
  gateway/           shared Nginx gateway
config/
  smtp/              local SMTP env (not committed)
  grok/              local Grok runtime config (not committed)
compose.yaml
```


## Community

This project is recognized / linked by the [LINUX DO](https://linux.do) community.

- Community site: https://linux.do

## License

This repository is licensed under the [Apache License 2.0](LICENSE).

## Third-party notices

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The Cloudflare mail module is based on Apache-2.0 `idinging/freemail`; the license is preserved under `apps/cloudflare-mail/LICENSE`.
