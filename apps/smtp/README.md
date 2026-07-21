# SMTP Console + Cloudflare Mail

[中文](#中文) · [English](#english)

---

# 中文

本模块不是单独的 SMTP 发信页面，而是一套完整的邮件工具链，包含：

1. **SMTP Console**：运行在 Docker/VPS 上的 Web 控制台。
2. **Cloudflare Mail Worker**：运行在 Cloudflare Workers 上的临时邮箱和收件服务。
3. **Cloudflare Email Routing**：把域名收到的邮件投递给 Worker。
4. **Cloudflare D1**：保存邮箱、邮件索引、用户和配置。
5. **Cloudflare R2**：保存完整 EML 邮件内容。
6. **外部 SMTP**：用于发送邮件，可使用任意支持 SMTP 的邮箱服务。

## 架构

```text
接收邮件：
互联网发件方
  → Cloudflare Email Routing / Catch-all
  → Cloudflare Mail Worker
  → D1（索引、邮箱、用户）
  → R2（完整 EML）
  → SMTP Console 查询 Worker API

发送邮件：
SMTP Console
  → 你配置的 SMTP 服务
  → 收件人

可选发送：
SMTP Console
  → 自定义远程发信 API
  → 收件人
```

## 仓库目录

```text
apps/smtp/                 SMTP Web 控制台
apps/cloudflare-mail/      Cloudflare Worker 邮件源码
config/smtp/.env.example   SMTP 控制台配置模板
```

---

## 第一部分：部署 Cloudflare 邮件 Worker

### 1. 前置条件

你需要：

- 一个 Cloudflare 账号
- 一个托管在 Cloudflare DNS 的域名
- 已启用 Workers、D1、R2 和 Email Routing
- 本地安装 Node.js，或使用 Cloudflare 网页控制台部署

命令行部署需要 Wrangler：

```bash
npm install -g wrangler
wrangler login
```

也可以不全局安装，下面所有 `wrangler` 命令改成 `npx wrangler`。

### 2. 准备 Worker 配置

```bash
cd apps/cloudflare-mail
cp wrangler.example.toml wrangler.toml
```

仓库不会提交 `wrangler.toml`，因为其中包含你的 Cloudflare 资源 ID 和域名。

### 3. 创建 D1 数据库

```bash
npx wrangler d1 create your-mail-db
```

命令会返回 `database_id`。把它填入：

```toml
[[d1_databases]]
binding = "TEMP_MAIL_DB"
database_name = "your-mail-db"
database_id = "这里填你自己的 D1 database_id"
```

绑定名称必须保持为：

```text
TEMP_MAIL_DB
```

### 4. 初始化 D1 数据库

完整功能推荐使用：

```bash
npx wrangler d1 execute your-mail-db --remote --file=d1-init.sql
```

精简初始化可使用：

```bash
npx wrangler d1 execute your-mail-db --remote --file=d1-init-basic.sql
```

如果数据库已经初始化，不要反复执行可能产生冲突的建表操作；先备份 D1。

### 5. 创建 R2 存储桶

```bash
npx wrangler r2 bucket create your-mail-eml
```

然后保持以下绑定名：

```toml
[[r2_buckets]]
binding = "MAIL_EML"
bucket_name = "your-mail-eml"
```

`MAIL_EML` 用于保存完整邮件内容。D1 主要保存索引和结构化信息。

### 6. 配置邮箱域名

在 `wrangler.toml` 中填写：

```toml
[vars]
MAIL_DOMAIN = "mail.example.com"
ADMIN_NAME = "admin"
SESSION_EXPIRE_DAYS = "7"
```

多个域名使用逗号分隔：

```toml
MAIL_DOMAIN = "example.com,mail.example.com"
```

不要把真实管理员密码或 JWT Token 明文写入 Git。

### 7. 配置 Worker Secret

```bash
npx wrangler secret put ADMIN_PASSWORD
npx wrangler secret put JWT_TOKEN
```

建议 `JWT_TOKEN` 使用至少 32 字节的随机字符串：

```bash
openssl rand -hex 32
```

可选配置：

```bash
npx wrangler secret put RESEND_API_KEY
npx wrangler secret put GUEST_PASSWORD
```

- `RESEND_API_KEY`：如果需要 Worker 自己发件。
- `GUEST_PASSWORD`：如果需要访客登录。
- SMTP Console 使用 `JWT_TOKEN` 作为 `FREEMAIL_API_KEY` 调用管理员 API。

### 8. 部署 Worker

```bash
npx wrangler deploy
```

部署成功后会得到类似地址：

```text
https://your-mail-worker.your-account.workers.dev
```

也可以在 Cloudflare Workers 中绑定自定义域名，例如：

```text
https://mail-api.example.com
```

### 9. 配置 Cloudflare Email Routing

这是能否收到邮件的关键步骤。

进入 Cloudflare 控制台：

```text
域名 → Email → Email Routing
```

依次完成：

1. 启用 Email Routing。
2. 按 Cloudflare 提示添加或确认 MX/TXT DNS 记录。
3. 进入 Routing Rules。
4. 新建 Catch-all 规则。
5. Catch-all 动作选择 **Send to a Worker**。
6. Worker 选择刚部署的邮件 Worker。
7. 确保规则状态为启用。

邮件链路应为：

```text
任意地址@你的域名 → Catch-all → Worker email() handler
```

如果需要转发到真实邮箱，目标地址必须先在 Cloudflare Email Routing 的 Destination addresses 中验证。

### 10. 验证 Worker

```bash
curl https://your-mail-worker.example.workers.dev/api/domains \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

随机生成邮箱：

```bash
curl https://your-mail-worker.example.workers.dev/api/generate \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

查看某邮箱邮件：

```bash
curl "https://your-mail-worker.example.workers.dev/api/emails?mailbox=test@example.com&limit=20" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

实时日志：

```bash
npx wrangler tail
```

---

## 第二部分：连接 SMTP Console

### 1. 创建配置

在仓库根目录执行：

```bash
cp config/smtp/.env.example config/smtp/.env
```

### 2. 配置 Cloudflare FreeMail

```env
FREEMAIL_BASE=https://your-mail-worker.example.workers.dev
FREEMAIL_API_KEY=这里填 Worker 的 JWT_TOKEN
```

打开 SMTP Console 后，在“邮箱服务”中选择：

```text
Cloudflare FreeMail
```

控制台会调用：

```text
GET  /api/domains
GET  /api/generate
POST /api/create
GET  /api/emails
GET  /api/email/{id}
```

### 3. 配置外部 SMTP 发信

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_SECURE=true
SMTP_USER=service@example.com
SMTP_PASSWORD=replace-me
SMTP_FROM=service@example.com
SMTP_FROM_NAME=Mail Console
```

常见模式：

| 模式 | 端口 | `SMTP_SECURE` |
| --- | ---: | --- |
| SMTP over SSL/TLS | 465 | `true` |
| SMTP + STARTTLS | 587 | `false` |

请使用邮箱服务商提供的应用专用密码，不建议使用主账号登录密码。

### 4. 可选：兼容型 Custom Mail API

控制台还保留另一种兼容接口：

```env
OPC_MAIL_BASE=https://mail-api.example.com
OPC_MAIL_DOMAINS=example.com,mail.example.com
```

这个接口需要兼容：

```text
POST /accounts
GET  /messages?token=<mailbox-token>
GET  /messages/{id}?token=<mailbox-token>
```

它与 Cloudflare FreeMail 的 `/api/*` 接口不是同一种协议。新部署推荐直接使用 `FREEMAIL_BASE`。

### 5. 可选：远程发信接口

```env
TELE_OPC_BASE=https://send-api.example.com
TELE_OPC_DEV_TOKEN=replace-me
```

如果不需要，把两个变量留空。

---

## 第三部分：启动 SMTP Console

作为合并项目的一部分启动：

```bash
docker compose up -d --build smtp gateway
```

访问：

```text
http://localhost:8080/smtp/
```

查看日志：

```bash
docker compose logs -f smtp
```

单独开发启动：

```bash
cd apps/smtp
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp ../../config/smtp/.env.example .env
uvicorn app:app --host 127.0.0.1 --port 18430
```

---

## SMTP Console API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 控制台健康检查 |
| `GET` | `/api/status` | SMTP 状态和最近日志 |
| `POST` | `/api/test` | 测试 SMTP 登录 |
| `POST` | `/api/send` | SMTP 发信 |
| `GET` | `/api/providers` | 邮箱服务列表与健康状态 |
| `GET` | `/api/providers/{provider}/domains` | 获取邮箱域名 |
| `POST` | `/api/mailboxes` | 创建临时邮箱 |
| `GET` | `/api/mailboxes` | 查看本地邮箱列表 |
| `GET` | `/api/mailboxes/{id}/messages` | 拉取邮件并提取验证码 |
| `DELETE` | `/api/mailboxes/{id}` | 删除控制台内的邮箱记录 |

## 数据持久化

| 数据 | 默认位置 |
| --- | --- |
| SMTP 发信日志 | `/var/lib/smtp-console/send.log.jsonl` |
| 控制台邮箱状态 | `/var/lib/mail-console/mailboxes.json` |
| Cloudflare 邮箱和索引 | D1 `TEMP_MAIL_DB` |
| 完整 EML | R2 `MAIL_EML` |

## 故障排查

### Worker 可以打开，但收不到邮件

检查：

1. Email Routing 是否启用。
2. MX 记录是否由 Cloudflare 正确配置。
3. Catch-all 是否指向正确 Worker。
4. `MAIL_DOMAIN` 是否包含收件域名。
5. `wrangler tail` 是否出现 `email()` 事件。

### 控制台显示 FreeMail 401/403

检查：

```env
FREEMAIL_API_KEY=JWT_TOKEN的真实值
```

不要填写管理员密码；这里需要 Worker 的 `JWT_TOKEN`。

### D1 报表不存在

执行：

```bash
npx wrangler d1 execute your-mail-db --remote --file=d1-init.sql
```

并确认绑定名为 `TEMP_MAIL_DB`。

### R2 访问失败

确认：

```toml
binding = "MAIL_EML"
```

并确认桶名称存在。

### SMTP 连接超时

- 云服务器可能限制 25 端口，优先使用 465 或 587。
- 核对 `SMTP_SECURE` 与端口是否匹配。
- 核对应用专用密码。
- 检查服务商是否限制来源 IP。

## 安全要求

1. `wrangler.toml`、`.dev.vars`、`config/smtp/.env` 不得提交到 Git。
2. `JWT_TOKEN`、SMTP 密码、Resend Key 泄漏后必须立即轮换。
3. Worker 管理接口不要在前端暴露管理员 Token。
4. SMTP Console 公开部署时必须加 HTTPS 和登录保护。
5. 定期清理 D1/R2 中的历史邮件，控制数据与费用。

---

# English

This module is a complete mail stack, not only an SMTP form:

```text
Inbound mail
  → Cloudflare Email Routing
  → Cloudflare Mail Worker
  → D1 metadata + R2 EML storage
  → SMTP Console via Worker API

Outbound mail
  → SMTP Console
  → your external SMTP provider
```

## Deploy the Cloudflare Worker

```bash
cd apps/cloudflare-mail
cp wrangler.example.toml wrangler.toml
npx wrangler login
npx wrangler d1 create your-mail-db
npx wrangler r2 bucket create your-mail-eml
```

Put your D1 `database_id`, database name, R2 bucket name, and mail domains into `wrangler.toml`. Keep these binding names unchanged:

```text
TEMP_MAIL_DB
MAIL_EML
```

Initialize D1:

```bash
npx wrangler d1 execute your-mail-db --remote --file=d1-init.sql
```

Configure secrets:

```bash
npx wrangler secret put ADMIN_PASSWORD
npx wrangler secret put JWT_TOKEN
```

Deploy:

```bash
npx wrangler deploy
```

In Cloudflare Dashboard, enable **Email Routing** and configure the domain catch-all action to **Send to a Worker** using this Worker. Without the catch-all Worker route, inbound mail will not arrive.

## Connect the SMTP Console

```bash
cp config/smtp/.env.example config/smtp/.env
```

Configure the Worker API:

```env
FREEMAIL_BASE=https://your-mail-worker.example.workers.dev
FREEMAIL_API_KEY=the-same-value-as-worker-JWT_TOKEN
```

Configure outbound SMTP:

```env
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_SECURE=true
SMTP_USER=service@example.com
SMTP_PASSWORD=replace-me
SMTP_FROM=service@example.com
SMTP_FROM_NAME=Mail Console
```

Use port 465 with `SMTP_SECURE=true`, or port 587 with `SMTP_SECURE=false` for STARTTLS.

Run:

```bash
docker compose up -d --build smtp gateway
```

Open:

```text
http://localhost:8080/smtp/
```

## Security

- Never commit `wrangler.toml`, `.dev.vars`, or `config/smtp/.env`.
- Rotate `JWT_TOKEN`, SMTP passwords, and sending API keys after any leak.
- Protect the public console with HTTPS and authentication.
- Regularly clean old D1/R2 mail data.

The Cloudflare mail module is based on the Apache-2.0 licensed `idinging/freemail` project; its license is preserved in `apps/cloudflare-mail/LICENSE`.
