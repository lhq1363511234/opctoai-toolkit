# Cloudflare Mail Worker

[中文](#中文) · [English](#english)

---

# 中文

本目录是基于 Apache-2.0 开源项目 `idinging/freemail` 清理后的 Cloudflare Workers 邮件服务源码。

它提供：

- 临时邮箱创建 / 随机生成
- 邮件接收（Cloudflare Email Routing）
- 邮件列表与正文查看
- 验证码相关字段解析
- D1 索引存储 + R2 完整 EML 存储
- 管理后台静态页面（`public/`）

SMTP Console 通过以下环境变量对接本 Worker：

```env
FREEMAIL_BASE=https://your-mail-worker.example.workers.dev
FREEMAIL_API_KEY=与 Worker JWT_TOKEN 相同的值
```

## 快速部署

```bash
cd apps/cloudflare-mail
cp wrangler.example.toml wrangler.toml
npx wrangler login
npx wrangler d1 create your-mail-db
npx wrangler r2 bucket create your-mail-eml
```

编辑 `wrangler.toml`：

1. 填入 D1 `database_id`
2. 填入 R2 `bucket_name`
3. 设置 `MAIL_DOMAIN=your-domain.com`

初始化并部署：

```bash
npx wrangler d1 execute your-mail-db --remote --file=d1-init.sql
npx wrangler secret put ADMIN_PASSWORD
npx wrangler secret put JWT_TOKEN
npx wrangler deploy
```

然后在 Cloudflare Dashboard：

1. 打开域名的 **Email Routing**
2. 配置 Catch-all 动作为 **Send to a Worker**
3. 选择本 Worker

## 关键文件

| 文件 | 说明 |
| --- | --- |
| `wrangler.example.toml` | 部署配置模板，不要提交真实 `wrangler.toml` |
| `d1-init.sql` | 完整 D1 初始化 |
| `d1-init-basic.sql` | 精简 D1 初始化 |
| `docs/api.md` | API 文档 |
| `docs/resend.md` | Resend / 转发相关说明 |
| `src/` | Worker 源码 |
| `public/` | 管理后台静态资源 |
| `LICENSE` | Apache-2.0 上游许可证 |
| `UPSTREAM.md` | 上游来源说明 |

## 绑定名称（不要改）

```text
TEMP_MAIL_DB   # D1
MAIL_EML       # R2
ASSETS         # 静态资源
```

## 常用 API

```bash
# 域名列表
curl https://your-mail-worker.example.workers.dev/api/domains \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 随机生成邮箱
curl https://your-mail-worker.example.workers.dev/api/generate \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"

# 查看邮件
curl "https://your-mail-worker.example.workers.dev/api/emails?mailbox=test@example.com&limit=20" \
  -H "Authorization: Bearer YOUR_JWT_TOKEN"
```

更完整的 SMTP Console 对接、Email Routing、故障排查说明见：

```text
../smtp/README.md
```

## 安全

- 不要提交 `wrangler.toml`、`.dev.vars`、真实域名、D1 ID、Token
- `JWT_TOKEN` 泄露后立刻轮换
- 生产环境建议使用自定义域名 + HTTPS，并限制管理入口

---

# English

This directory contains a cleaned Cloudflare Workers mail service based on the Apache-2.0 project `idinging/freemail`.

It provides temporary mailbox creation, inbound Email Routing, message listing, verification-code helpers, D1 indexes, R2 EML storage, and a static admin UI.

SMTP Console connects through:

```env
FREEMAIL_BASE=https://your-mail-worker.example.workers.dev
FREEMAIL_API_KEY=same-value-as-worker-JWT_TOKEN
```

## Quick deploy

```bash
cd apps/cloudflare-mail
cp wrangler.example.toml wrangler.toml
npx wrangler login
npx wrangler d1 create your-mail-db
npx wrangler r2 bucket create your-mail-eml
```

Edit `wrangler.toml` with your D1 ID, R2 bucket, and `MAIL_DOMAIN`, then:

```bash
npx wrangler d1 execute your-mail-db --remote --file=d1-init.sql
npx wrangler secret put ADMIN_PASSWORD
npx wrangler secret put JWT_TOKEN
npx wrangler deploy
```

In Cloudflare Dashboard, enable Email Routing and set Catch-all to **Send to a Worker**.

## Required bindings

```text
TEMP_MAIL_DB
MAIL_EML
ASSETS
```

For the full SMTP Console + Cloudflare chain, see `../smtp/README.md`.

Do not commit real `wrangler.toml`, secrets, domains, or resource IDs.
