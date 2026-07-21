# Grok Register Docker Image

## Docker Hub 镜像

| 镜像 | 说明 |
| --- | --- |
| `cirstein/grok-register-web:latest` | Web 控制台（唯一推荐） |

Hub：

```text
https://hub.docker.com/r/cirstein/grok-register-web
```

```bash
docker pull cirstein/grok-register-web:latest
```

镜像体积主要来自 Chromium + Xvfb。

---

## 前置条件

```bash
cp config/grok/config.json.example config/grok/config.json
```

至少填好：

| 字段 | 说明 |
| --- | --- |
| `email_provider` | 邮箱服务类型，如 `cloudflare` |
| `cloudflare_api_base` / `cloudflare_api_key` | 外接邮件 API |
| `proxy` | 访问 xAI 的代理 |
| `cpa_*` | 如需导出 CPA/OIDC 再填 |

---

## 最快启动

```bash
cp config/grok/config.json.example config/grok/config.json
# 编辑 config/grok/config.json

docker compose up -d grok gateway
```

访问：`http://localhost:8080/grok/`

---

## 单容器启动

```bash
mkdir -p ./config/grok/cpa_auths
cp config/grok/config.json.example config/grok/config.json
# 编辑 config/grok/config.json

docker run -d --name grok-register \
  -p 18425:18425 \
  --shm-size=1g \
  -e HOST=0.0.0.0 \
  -e PORT=18425 \
  -e GROK_CONFIG_FILE=/data/config.json \
  -e GROK_DATA_DIR=/data \
  -e GROK_CPA_DIR=/data/cpa_auths \
  -v "$PWD/config/grok:/data" \
  cirstein/grok-register-web:latest
```

---

## 数据卷

| 容器路径 | 宿主机 | 内容 |
| --- | --- | --- |
| `/data/config.json` | `config/grok/config.json` | 运行配置 |
| `/data/cpa_auths` | `config/grok/cpa_auths/` | 导出认证 |

---

## 本地源码构建（可选）

```bash
docker build -f apps/grok/Dockerfile -t grok-register:local apps/grok
GROK_IMAGE=grok-register:local docker compose up -d grok gateway
```

---

## 外接配置（不会填请看这里）

容器默认 **外接模式**：邮箱、代理、CPA 都通过 `config.json` 填外部服务，不绑定宿主机 `127.0.0.1`。

详细图文级说明：

- [../EXTERNAL_CONFIG.md](../EXTERNAL_CONFIG.md)

最少必填：

1. `email_provider` + 对应 API（如 `cloudflare_api_base` + `defaultDomains`）
2. `proxy`（远程 HTTP 代理；能直连 xAI 可留空）
3. 若要自动进 CLIProxyAPI：`cpa_remote_url` + `cpa_management_key`，且 `cpa_copy_to_hotload=false`

启动后打开 Web → **配置管理** → **测试邮箱** / **测试外接连通性** → 顶部「外接模式就绪」。

