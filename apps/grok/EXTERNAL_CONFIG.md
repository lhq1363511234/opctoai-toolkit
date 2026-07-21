# Grok 外接配置完整指南（Web / Docker）

> 适用页面：`/grok/`、`/grok-container/`  
> 目标：让容器**不依赖本机** `127.0.0.1`、本机代理、本机 CLIProxyAPI 热加载目录，全部通过「填表」接外部服务。

很多人反馈「不会用」——通常不是程序坏了，而是 **邮箱 / 代理 / 远程 CPA 三项没填齐**，或仍指向本机地址。

---

## 1. 一分钟理解

注册机要工作，最少需要三样**外部**东西：

| 依赖 | 干什么 | 能不能用本机 127.0.0.1？ |
|------|--------|--------------------------|
| **邮箱 API** | 收 xAI 注册验证码 | ❌ 外接模式禁止 |
| **HTTP 代理** | 访问 accounts.x.ai / grok（多数地区需要） | ❌ 外接模式禁止（可留空=直连） |
| **远程 CLIProxyAPI（CPA）** | 接收 OIDC 凭证，供 Grok 4.5 调用 | ❌ 不要填 localhost；也不要 copy 到本机 hotload |

成功后会得到：

1. `accounts_cli.txt`：账号 + SSO  
2. `cpa_auths/xai-邮箱.json`：容器内备份  
3. 远程 CPA：`POST` 到你的 `cpa_remote_url`（真正给 API 用的凭证）

> **网页上的「CPA 数」默认只数容器本地 `cpa_auths`。**  
> 真正可用数量以 **CLIProxyAPI 管理端 /auth 文件** 为准。本地 100、远程 250 都正常。

---

## 2. 最快上手（推荐路径）

### 2.1 拉镜像

```bash
docker pull cirstein/grok-register-web:latest
```

Hub：https://hub.docker.com/r/cirstein/grok-register-web

### 2.2 准备数据目录 + 配置

```bash
mkdir -p ./grok-data/cpa_auths
# 二选一：
cp apps/grok/config.external.example.json ./grok-data/config.json
# 或 monorepo：
# cp config/grok/config.json.example ./grok-data/config.json
```

用编辑器打开 `./grok-data/config.json`，先改下面 **必改项**（示例值请换成你自己的）：

```json
{
  "external_mode": true,

  "email_provider": "cloudflare",
  "defaultDomains": "mail.example.com",
  "cloudflare_api_base": "https://mail.example.com",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/domains",
  "cloudflare_path_accounts": "/accounts",
  "cloudflare_path_token": "/token",
  "cloudflare_path_messages": "/messages",

  "proxy": "http://USER:PASS@your-proxy-host:7890",
  "cpa_proxy": "http://USER:PASS@your-proxy-host:7890",

  "cpa_auto_add": true,
  "cpa_export_enabled": true,
  "cpa_auth_dir": "/data/cpa_auths",
  "cpa_remote_url": "https://your-domain.com/cliproxyapi",
  "cpa_management_key": "mgmt-xxxxxxxx",
  "cpa_copy_to_hotload": false,
  "cpa_hotload_dir": "",
  "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
  "cpa_force_standalone": true,
  "cpa_headless": false,

  "register_count": 1,
  "register_workers": 1,
  "register_threads": 1
}
```

### 2.3 启动容器

```bash
docker run -d --name grok-register \
  -p 18425:18425 \
  --shm-size=1g \
  -e HOST=0.0.0.0 \
  -e PORT=18425 \
  -e GROK_CONFIG_FILE=/data/config.json \
  -e GROK_DATA_DIR=/data \
  -e GROK_CPA_DIR=/data/cpa_auths \
  -v "$PWD/grok-data:/data" \
  cirstein/grok-register-web:latest
```

浏览器打开：

```text
http://服务器IP:18425/
# 若前面有 Nginx 反代：
# https://toolkit.example.com/grok-container/
```

### 2.4 在网页里验收（非常重要）

1. 打开 **配置管理** → 确认字段已保存  
2. 点 **测试当前邮箱配置** → 必须成功  
3. 点 **测试外接连通性** → 邮箱 / 代理 / 远程 CPA 尽量全绿  
4. 看顶部横幅：
   - 绿色 **外接模式就绪** → 可以点开始注册  
   - 黄色 **未就绪** → 按提示改（常见是还写着 `127.0.0.1`）

---

## 3. 网页「配置管理」怎么填（按分组）

打开页面 → **配置管理**。字段按组说明如下。

### 3.1 外接模式

| 字段 | 必填 | 填什么 |
|------|------|--------|
| `external_mode` | 是 | 填 `true`。开启后禁止依赖本机服务。 |

### 3.2 邮箱服务（必填外接）

| 字段 | 说明 |
|------|------|
| `email_provider` | 选一个：`cloudflare` / `cloudmail` / `duckmail` / `yyds` / `mailnest` |
| `defaultDomains` | 注册用的域名，逗号分隔。cloudflare/cloudmail/yyds 强烈建议填 |

#### A) Cloudflare 临时邮箱（最常用）

你需要先有一套可 HTTP 调用的邮件 API（例如本仓库 `apps/cloudflare-mail` Worker，或兼容接口）。

| 字段 | 示例 | 说明 |
|------|------|------|
| `cloudflare_api_base` | `https://mail.example.com` | **公网 HTTPS**，不要 `http://127.0.0.1` |
| `cloudflare_api_key` | （可空） | Worker 若要求 key 再填 |
| `cloudflare_auth_mode` | `none` | 常见：`none`；有全局密码可用 `x-custom-auth` 等 |
| `cloudflare_custom_auth` | （可空） | Worker `PASSWORDS` 一类全局口令 |
| `cloudflare_path_domains` | `/domains` | 域名列表路径，按你的 Worker 实际路径改 |
| `cloudflare_path_accounts` | `/accounts` | 创建邮箱 |
| `cloudflare_path_token` | `/token` | 取收件 token |
| `cloudflare_path_messages` | `/messages` | 拉邮件列表 |
| `defaultDomains` | `mail.example.com` | 你的收信域名 |

> 路径两套常见风格：  
> - 短路径：`/domains` `/accounts` `/token` `/messages`  
> - 长路径：`/api/domains` `/api/new_address` `/api/token` `/api/mails`  
> **以你 Worker 实际路由为准**，填错会「邮件数量一直 0」。

#### B) CloudMail

| 字段 | 说明 |
|------|------|
| `cloudmail_url` | 管理端根 URL，如 `https://mail.example.com` |
| `cloudmail_admin_email` | 管理员邮箱 |
| `cloudmail_password` | 管理员密码 |
| `defaultDomains` | catch-all 域名 |

#### C) DuckMail

| 字段 | 说明 |
|------|------|
| `duckmail_api_base` | 默认 `https://api.duckmail.sbs` |
| `duckmail_api_key` | 你的 key |

#### D) YYDS / Mailnest

按服务商后台给的 `api_key` / `jwt` / `project_code` 填写对应字段即可。

### 3.3 网络与浏览器

| 字段 | 说明 |
|------|------|
| `proxy` | **注册浏览器 + 拉邮件** 用的 HTTP 代理 |
| `cpa_proxy` | **OIDC mint** 专用代理；空则跟随 `proxy` |
| `user_agent` | 一般保持默认 Chrome UA |
| `debug_mode` | 调试开 `true`（会更啰嗦、可能保留浏览器） |
| `close_browser_on_stop` | 停止任务时是否关浏览器，建议 `true` |
| `log_level` | `info` 即可 |

**代理写法：**

```text
http://127.0.0.1:7890          ← 外接模式会判定「未就绪」（本机）
http://1.2.3.4:7890            ← OK，远程 IP
http://proxy.example.com:7890  ← OK
http://user:pass@host:7890     ← OK（HTTP 库可用；Chromium 会剥掉账号只留 host:port）
（留空）                        ← 直连；能访问 xAI 才行
```

代理优先级：

```text
cpa_proxy  >  proxy  >  环境变量 https_proxy/http_proxy
```

### 3.4 CPA 外接（远程 Management API）

| 字段 | 建议值 | 说明 |
|------|--------|------|
| `cpa_auto_add` | `true` | 注册成功后自动 mint + 写入/上传 |
| `cpa_export_enabled` | `true` | 打开 OIDC 导出 |
| `cpa_auth_dir` | `/data/cpa_auths` | 容器内备份目录（挂载卷） |
| `cpa_remote_url` | `https://你的域名/cliproxyapi` | **远程** CLIProxyAPI 根地址 |
| `cpa_management_key` | `mgmt-...` | 管理密钥，上传 auth 必填 |
| `cpa_copy_to_hotload` | **`false`** | 外接不要开 |
| `cpa_hotload_dir` | **留空** | 不要填 `/var/lib/cliproxyapi/auths` |
| `cpa_base_url` | 固定 | `https://cli-chat-proxy.grok.com/v1` |
| `cpa_force_standalone` | `true` | 独立浏览器完成授权 |
| `cpa_headless` | `false` | 推荐有头（容器内 Xvfb） |

正确数据流：

```text
注册成功
  → mint OIDC
  → 写容器 /data/cpa_auths/xai-xxx.json
  → 上传到 cpa_remote_url（CLIProxyAPI Management API）
  → 用 CLIProxyAPI 的 API Key 调 model=grok-4.5
```

**错误示范（外接模式）：**

```text
cpa_copy_to_hotload = true
cpa_hotload_dir = /var/lib/cliproxyapi/auths   ← 绑死本机
cpa_remote_url = http://127.0.0.1:8317         ← 外接检查失败
```

---

## 4. 完整可复制示例

### 4.1 Cloudflare 邮箱 + 远程代理 + 远程 CPA（推荐）

```json
{
  "external_mode": true,
  "email_provider": "cloudflare",
  "defaultDomains": "mail.example.com,example.com",
  "cloudflare_api_base": "https://mail.example.com",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_custom_auth": "",
  "cloudflare_path_domains": "/domains",
  "cloudflare_path_accounts": "/accounts",
  "cloudflare_path_token": "/token",
  "cloudflare_path_messages": "/messages",

  "proxy": "http://user:pass@proxy.example.com:7890",
  "cpa_proxy": "http://user:pass@proxy.example.com:7890",

  "cpa_auto_add": true,
  "cpa_export_enabled": true,
  "cpa_auth_dir": "/data/cpa_auths",
  "cpa_remote_url": "https://api.example.com/cliproxyapi",
  "cpa_management_key": "mgmt-请换成你的密钥",
  "cpa_copy_to_hotload": false,
  "cpa_hotload_dir": "",
  "cpa_base_url": "https://cli-chat-proxy.grok.com/v1",
  "cpa_force_standalone": true,
  "cpa_headless": false,

  "register_count": 1,
  "register_workers": 1,
  "register_threads": 1,
  "close_browser_on_stop": true,
  "debug_mode": false,
  "log_level": "info",
  "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
}
```

### 4.2 DuckMail（没有自建 Worker 时）

```json
{
  "external_mode": true,
  "email_provider": "duckmail",
  "duckmail_api_base": "https://api.duckmail.sbs",
  "duckmail_api_key": "你的-duckmail-key",
  "defaultDomains": "",

  "proxy": "http://proxy.example.com:7890",
  "cpa_proxy": "http://proxy.example.com:7890",

  "cpa_auto_add": true,
  "cpa_export_enabled": true,
  "cpa_auth_dir": "/data/cpa_auths",
  "cpa_remote_url": "https://api.example.com/cliproxyapi",
  "cpa_management_key": "mgmt-请换成你的密钥",
  "cpa_copy_to_hotload": false,
  "cpa_hotload_dir": "",
  "cpa_base_url": "https://cli-chat-proxy.grok.com/v1"
}
```

### 4.3 只导出本地 CPA 文件、暂不上传远程

```json
{
  "external_mode": true,
  "cpa_auto_add": true,
  "cpa_export_enabled": true,
  "cpa_auth_dir": "/data/cpa_auths",
  "cpa_remote_url": "",
  "cpa_management_key": "",
  "cpa_copy_to_hotload": false
}
```

之后把 `/data/cpa_auths/xai-*.json` 拷到任意 CLIProxyAPI 的 auth 目录即可。

---

## 5. 启动前检查清单

- [ ] `external_mode = true`
- [ ] 邮箱：provider + 对应 API 地址/密钥  
- [ ] `defaultDomains` 已填（cloudflare/cloudmail 必查）  
- [ ] `proxy` / `cpa_proxy` **不是** `127.0.0.1` / `localhost`（要代理就填远程）  
- [ ] 若要自动进 API 池：`cpa_remote_url` + `cpa_management_key`  
- [ ] `cpa_copy_to_hotload = false`，`cpa_hotload_dir` 为空  
- [ ] 网页「测试邮箱」通过  
- [ ] 网页「测试外接连通性」通过  
- [ ] 顶部横幅：**外接模式就绪**

---

## 6. 常见报错与处理

| 现象 | 原因 | 处理 |
|------|------|------|
| 顶部「外接模式未就绪」且提示 `proxy=http://127.0.0.1:...` | 仍用本机代理 | 改远程代理或清空直连 |
| `缺少 cloudflare_api_base` | 邮箱根地址空 | 填公网 Worker URL |
| `缺少 defaultDomains` | 域名空 | 填你的收信域名 |
| `缺少 cpa_management_key` | 开了远程上传但没 key | 填 management key |
| 日志 `Cloudflare 本轮邮件数量: 0` | 路径/域名/鉴权不对，或邮件还没到 | 核对 path；加长 `mail_timeout`；看 Worker 日志 |
| `Turnstile 获取 token 失败` | CF 人机/代理质量差 | 换干净住宅代理；`cpa_headless=false` |
| 注册成功但网页 CPA 数远小于账号数 | **正常**：UI 只数本地 `cpa_auths` | 去 CLIProxyAPI 管理端看真实 xai 数量 |
| 远程上传 403 | 公网 management 被禁/反代鉴权 | 检查反代是否放行 management；key 是否正确 |
| 浏览器启动失败 | 无 `--no-sandbox` / shm 太小 | 官方镜像已处理；确保 `--shm-size=1g` |

日志里出现类似下面几行，说明 **CPA 链路是通的**：

```text
[CPA] ✅ access_token ... + refresh_token
[CPA] 已写入本地 /data/cpa_auths/xai-xxx.json
[CPA] 已上传远程 https://.../cliproxyapi/.../xai-xxx.json
[Web] 新增 CPA 凭证: N
```

---

## 7. 和「本机模式」的区别

| 项目 | 本机开发模式 | 外接模式（推荐上生产/搬机） |
|------|--------------|------------------------------|
| 代理 | 可用 `127.0.0.1:7890` | 必须远程代理或直连 |
| 邮件 | 可本机服务 | 必须公网 API |
| CPA | 可 copy 到 `/var/lib/cliproxyapi/auths` | 只上传 `cpa_remote_url` |
| 搬到另一台服务器 | 容易断 | 改配置即可跑 |

外接检查逻辑（概念）：

- 若 `proxy` / `cpa_proxy` / 邮件 API / `cpa_remote_url` 含 `127.0.0.1`、`localhost` → 判定未就绪  
- 若 `cpa_copy_to_hotload` 指向本机 cliproxy 路径 → 判定未就绪  
- 邮箱/远程 CPA 必填字段缺失 → 判定未就绪  

接口：

- `GET /api/status` → 含 `external` 报告  
- `GET /api/external_check` → 只查外接状态  

---

## 8. monorepo Compose 用法

```bash
git clone https://github.com/lhq1363511234/opctoai-toolkit.git
cd opctoai-toolkit
cp config/grok/config.json.example config/grok/config.json
# 按本文第 4 节编辑 config/grok/config.json
docker compose up -d grok gateway
```

访问：`http://localhost:8080/grok/`

邮件自建请同时阅读：

- [apps/smtp/README.md](../smtp/README.md)  
- [apps/cloudflare-mail/README.md](../cloudflare-mail/README.md)  

---

## 9. 模板文件位置

| 文件 | 用途 |
|------|------|
| [`config.external.example.json`](./config.external.example.json) | 外接空模板（字段最全的简表） |
| [`config.example.json`](./config.example.json) | 带 `//` 注释的详细模板 |
| [`config/grok/config.json.example`](../../config/grok/config.json.example) | Compose 数据目录模板 |
| 本文 | 人类可读的填表说明 |

运行时真正读取的是挂载卷里的 **`/data/config.json`**，不要把真实密钥提交进 Git。

---

## 10. 合规提醒

请遵守 xAI / 上游服务条款、当地法律与社区规范。  
本工具仅供学习与自用运维；请勿用于滥用、批量违规注册或未授权访问。

仓库已链接 [LINUX DO](https://linux.do) 社区讨论；协议 [Apache-2.0](../../LICENSE)。

---

## 11. 还是不会？按这个顺序提问/自查

1. 顶部横幅是绿还是黄？黄的完整提示原文是什么？  
2. 「测试邮箱」输出？  
3. 「测试外接连通性」输出？  
4. `email_provider` + `cloudflare_api_base`（可打码）？  
5. `proxy` 是否仍是 `127.0.0.1`？  
6. `cpa_remote_url` 是否公网可达？management key 是否配对？  

把以上 6 点贴出来，一般可以直接定位。
