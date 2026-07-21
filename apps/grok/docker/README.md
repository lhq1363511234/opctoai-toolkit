# Grok Register Docker Image

## 官方镜像（Docker Hub）

| 镜像 | 说明 | 用途 |
| --- | --- | --- |
| `cirstein/grok-register-web:latest` | **推荐**，含 Web 控制台 | 日常部署 |
| `cirstein/grok-register:slim` | 精简版（同 `latest`） | 体积更小的运行环境 |
| `cirstein/grok-register:latest` | 与 slim 同源 | 兼容拉取 |
| `cirstein/grok-register-node:latest` | 分布式注册节点 | 仅 fleet 场景 |

Hub 地址：

```text
https://hub.docker.com/r/cirstein/grok-register
https://hub.docker.com/r/cirstein/grok-register-web
```

直接拉取：

```bash
docker pull cirstein/grok-register-web:latest
# 或
docker pull cirstein/grok-register:slim
```

镜像体积主要来自 Chromium + Xvfb，不是业务代码。

---

## 前置条件（必须先配好）

1. 复制并编辑配置：

```bash
cp config/grok/config.json.example config/grok/config.json
```

2. 至少填好：

| 字段 | 说明 |
| --- | --- |
| `email_provider` | 邮箱服务类型，如 `cloudflare` |
| `cloudflare_api_base` / `cloudflare_api_key` | 外接邮件 API（或你自己的 FreeMail Worker） |
| `proxy` | 访问 xAI 的代理，例如 `http://host.docker.internal:7898` 或宿主机可达代理 |
| `cpa_*` | 如需导出 CPA/OIDC 再填 |

不要把真实 Token / 代理密码写进镜像。

---

## 最快启动（拉镜像，不本地编译）

在仓库根目录：

```bash
cp config/grok/config.json.example config/grok/config.json
# 编辑 config/grok/config.json

docker compose up -d grok gateway
```

默认使用：

```text
cirstein/grok-register-web:latest
```

换 slim：

```bash
GROK_IMAGE=cirstein/grok-register:slim docker compose up -d grok gateway
```

访问：

```text
http://localhost:8080/grok/
```

---

## 单容器启动（不走 compose）

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

slim 版：

```bash
docker run -d --name grok-register \
  -p 18425:18425 \
  --shm-size=1g \
  -e HOST=0.0.0.0 \
  -e PORT=18425 \
  -e GROK_CONFIG_FILE=/data/config.json \
  -e GROK_DATA_DIR=/data \
  -e GROK_CPA_DIR=/data/cpa_auths \
  -v "$PWD/config/grok:/data" \
  cirstein/grok-register:slim
```

---

## 数据卷

| 容器路径 | 宿主机 | 内容 |
| --- | --- | --- |
| `/data/config.json` | `config/grok/config.json` | 运行配置 |
| `/data/cpa_auths` | `config/grok/cpa_auths/` | 导出认证 |

---

## 本地源码构建（可选）

只有要改代码时才需要：

```bash
docker build -f apps/grok/Dockerfile -t grok-register:local apps/grok
GROK_IMAGE=grok-register:local docker compose up -d grok gateway
```
