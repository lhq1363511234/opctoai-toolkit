# SMTP Console / 邮件控制台

[English](#english) · [中文](#中文)

---

## 中文

SMTP 模块是一个轻量 Web 控制台，提供：

- SMTP 发信测试与发送
- 临时邮箱创建
- 收件查看
- 验证码提取
- 邮箱服务健康检查

### 配置

复制示例配置：

```bash
cp config/smtp/.env.example config/smtp/.env
```

必填项：

| 变量 | 说明 |
| --- | --- |
| `SMTP_HOST` | SMTP 服务器地址 |
| `SMTP_PORT` | 端口，常见 `465` / `587` |
| `SMTP_SECURE` | `true` 表示 SSL/TLS |
| `SMTP_USER` | SMTP 用户名 |
| `SMTP_PASSWORD` | SMTP 密码或应用专用密码 |
| `SMTP_FROM` | 发件人邮箱 |
| `SMTP_FROM_NAME` | 发件人显示名 |

可选项：

| 变量 | 说明 |
| --- | --- |
| `OPC_MAIL_BASE` | 自建临时邮箱 API 地址 |
| `OPC_MAIL_DOMAINS` | 自建邮箱可用域名，逗号分隔 |
| `TELE_OPC_BASE` | 远程发信服务地址（可选） |
| `TELE_OPC_DEV_TOKEN` | 远程发信服务 Token（可选） |

> 不要把真实 `.env` 提交到 Git。仓库里只保留 `.env.example`。

### 启动

作为 monorepo 一部分：

```bash
docker compose up -d --build smtp gateway
```

访问：

```text
http://localhost:8080/smtp/
```

单独开发启动：

```bash
cd apps/smtp
cp ../../config/smtp/.env.example .env
uvicorn app:app --host 127.0.0.1 --port 18430
```

### API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/status` | SMTP 状态与最近日志 |
| `POST` | `/api/test` | 测试 SMTP 登录 |
| `POST` | `/api/send` | 直接 SMTP 发送 |
| `GET` | `/api/providers` | 邮箱服务列表 |
| `POST` | `/api/mailboxes` | 创建临时邮箱 |
| `GET` | `/api/mailboxes/{id}/messages` | 拉取收件 |

### 数据持久化

- 发信日志：`/var/lib/smtp-console`
- 临时邮箱状态：`/var/lib/mail-console`

Compose 部署时会映射到 Docker volumes。

### 安全建议

1. 使用独立、最小权限的 SMTP 账号。
2. 不要在仓库、日志或镜像层中保存真实密码。
3. 生产环境请放在反向代理后，并启用访问控制。
4. 如需公开访问，务必启用 HTTPS。

---

## English

The SMTP module is a lightweight web console for:

- SMTP login testing and message sending
- temporary mailbox creation
- inbox viewing
- verification-code extraction
- provider health checks

### Configuration

```bash
cp config/smtp/.env.example config/smtp/.env
```

Required:

| Variable | Description |
| --- | --- |
| `SMTP_HOST` | SMTP host |
| `SMTP_PORT` | Port, usually `465` or `587` |
| `SMTP_SECURE` | `true` for SSL/TLS |
| `SMTP_USER` | SMTP username |
| `SMTP_PASSWORD` | SMTP password or app password |
| `SMTP_FROM` | From address |
| `SMTP_FROM_NAME` | From display name |

Optional:

| Variable | Description |
| --- | --- |
| `OPC_MAIL_BASE` | Self-hosted temporary mailbox API |
| `OPC_MAIL_DOMAINS` | Comma-separated domains for the custom mailbox API |
| `TELE_OPC_BASE` | Optional remote send API base URL |
| `TELE_OPC_DEV_TOKEN` | Optional remote send API token |

Do not commit a real `.env` file. Only `.env.example` belongs in Git.

### Run

As part of the monorepo:

```bash
docker compose up -d --build smtp gateway
```

Open:

```text
http://localhost:8080/smtp/
```

Standalone development:

```bash
cd apps/smtp
cp ../../config/smtp/.env.example .env
uvicorn app:app --host 127.0.0.1 --port 18430
```

### Security

1. Use a dedicated least-privilege SMTP account.
2. Keep real credentials out of Git, logs, and image layers.
3. Put the service behind reverse-proxy authentication in production.
4. Use HTTPS for any public exposure.
