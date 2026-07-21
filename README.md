# opcToai Toolkit

[English README](README.en.md) · [安全说明](SECURITY.md)

一个由 Docker Compose 管理的双模块工具箱：

- **SMTP Console**：统一管理发件 SMTP、临时邮箱、收件、验证码提取和邮件服务连接状态。
- **Grok Web**：浏览器工作流与账号/配置管理界面。该模块需要你自行提供合规的外部邮件、代理和 CPA 等服务配置。

两个模块由同一个 Nginx 网关提供入口：

| 路径 | 服务 | 默认容器端口 |
| --- | --- | --- |
| `/smtp/` | 邮件控制台 | 18430 |
| `/grok/` | Grok Web | 18425 |

## 特性

- 单仓库、单个 `compose.yaml` 管理两个服务。
- 运行配置与源码分离：密码、Token、代理、邮箱和账户数据不进入 Git，也不被写入镜像层。
- Grok 服务使用 Xvfb、Chromium 与共享内存配置，适合容器运行环境。
- 可替换网关、SMTP、邮箱 API、CPA 服务和代理，无需修改源代码。

## 快速开始

### 1. 准备配置

```bash
git clone git@github.com:lhq1363511234/opctoai-toolkit.git
cd opctoai-toolkit
cp config/smtp/.env.example config/smtp/.env
cp config/grok/config.json.example config/grok/config.json
```

编辑两个配置文件，填入你自己的 SMTP、邮箱服务、代理和远程服务地址。**不要把真实凭据提交到 Git。**

### 2. 启动

```bash
docker compose up -d --build
```

默认入口：

```text
http://localhost:8080/smtp/
http://localhost:8080/grok/
```

如需修改入口端口：

```bash
TOOLKIT_PORT=18080 docker compose up -d --build
```

## 数据持久化

| 数据 | 位置 |
| --- | --- |
| SMTP 发信日志 | Docker volume `smtp-data` |
| 临时邮箱状态 | Docker volume `mail-console-data` |
| Grok 运行配置、账户导出与 CPA 文件 | `./config/grok/` |

部署前请备份 `config/grok/` 和 Docker volumes。

## 生产部署建议

1. 使用 HTTPS 反向代理并启用身份验证；不要直接公开 Docker 端口。
2. 使用独立的、最小权限的 SMTP 凭据与 API Token。
3. 对代理、邮箱 API、CPA 服务配置网络访问控制和请求限流。
4. 定期更新 Chromium、基础镜像与 Python 依赖。
5. 遵守上游服务的条款、当地法律和平台规则；不要将本项目用于绕过访问控制、批量滥用或未经授权的账户操作。

## 开发检查

```bash
python3 -m py_compile apps/smtp/app.py apps/grok/web_app.py
docker compose config
```

## 目录结构

```text
apps/
  smtp/       SMTP Console 源码和 Dockerfile
  grok/       Grok Web 源码和 Dockerfile
docker/
  gateway/    统一 Nginx 网关配置
config/
  smtp/       本地 SMTP 环境变量（不提交）
  grok/       Grok 本地运行配置（不提交）
compose.yaml
```

## 致谢与许可证

Grok 模块保留原有上游来源信息，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。在二次分发前请核对上游许可证及依赖许可证。
