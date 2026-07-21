# Grok Register Docker Image

Container packaging notes for the Grok web module in this monorepo.

## Image purpose

- Run the Grok web console with Chromium + Xvfb
- Keep runtime config and generated credentials outside the image
- Support local compose deployment via repository root `compose.yaml`

Typical published tags (example):

```text
your-dockerhub-user/grok-register:slim
your-dockerhub-user/grok-register:latest
```

Image size mainly comes from Chromium + Xvfb, not application code.

## Run with compose (recommended)

From repository root:

```bash
cp config/grok/config.json.example config/grok/config.json
# edit config/grok/config.json
docker compose up -d --build grok gateway
```

Open:

```text
http://localhost:8080/grok/
```

## Standalone run

```bash
docker build -f apps/grok/Dockerfile -t grok-register:local apps/grok

docker run -d --name grok-register \
  -p 18425:18425 \
  --shm-size=1g \
  -e HOST=0.0.0.0 \
  -e PORT=18425 \
  -e GROK_CONFIG_FILE=/data/config.json \
  -e GROK_DATA_DIR=/data \
  -e GROK_CPA_DIR=/data/cpa_auths \
  -v "$PWD/config/grok:/data" \
  grok-register:local
```

## Important volumes

| Path in container | Host path | Content |
| --- | --- | --- |
| `/data/config.json` | `config/grok/config.json` | Runtime settings |
| `/data/cpa_auths` | `config/grok/cpa_auths/` | Exported auth files |

Never bake real proxy URLs, mailbox tokens, CPA keys, or account exports into the image.

## Build notes

```bash
# from monorepo root
docker compose build grok

# or build app context directly
docker build -f apps/grok/Dockerfile -t grok-register:local apps/grok
```

For slim packaging experiments, see Dockerfiles under `apps/grok/docker/`.
