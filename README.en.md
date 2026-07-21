# opcToai Toolkit

[中文说明](README.md) · [Security](SECURITY.md)

A Docker Compose monorepo that packages two independently deployable tools behind one gateway:

- **SMTP Console** — SMTP sending, temporary mailbox management, inbound message viewing, verification-code extraction, and provider health checks.
- **Grok Web** — a browser workflow and configuration dashboard. You must provide your own compliant external mail, proxy, and CPA service settings.

## Routes

| Route | Service | Internal port |
| --- | --- | --- |
| `/smtp/` | SMTP Console | 18430 |
| `/grok/` | Grok Web | 18425 |

## Design

- One repository and one `compose.yaml` manage both services.
- Runtime settings are separated from source code. Passwords, tokens, proxy settings, mailbox data, and generated files are excluded from Git and image layers.
- The Grok container includes Xvfb, Chromium, and shared-memory settings suitable for browser-based container workloads.
- SMTP, mailbox APIs, gateway, proxy, and remote services can be replaced through configuration.

## Module docs

- [SMTP Console bilingual README](apps/smtp/README.md)
- See `apps/grok/README.md` for the Grok module

## Quick start

```bash
git clone git@github.com:lhq1363511234/opctoai-toolkit.git
cd opctoai-toolkit
cp config/smtp/.env.example config/smtp/.env
cp config/grok/config.json.example config/grok/config.json
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
docker compose config
```

## Third-party notices

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the preserved upstream source reference and dependency notice.
