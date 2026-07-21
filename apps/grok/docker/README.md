# Grok Register Image

对应线上服务：`https://toolkit.opctoai.com/grok/`

源码：`/root/opt/grok_reg-share`（`grok-reg-web.service` -> `web_app.py:18425`）

## 镜像
- `cirstein/grok-register:slim`
- `cirstein/grok-register:latest`

说明：
- 业务代码本身很小
- 镜像体积主要来自 Chromium + Xvfb（注册机需要浏览器自动化）
- Hub 层压缩后约 430MB 级；本地解压约 1.03GB

## 运行
```bash
docker run -d --name grok-register \
  -p 18425:18425 \
  -e HOST=0.0.0.0 \
  -e PORT=18425 \
  -v $PWD/config.json:/app/config.json \
  -v $PWD/cpa_auths:/app/cpa_auths \
  cirstein/grok-register:slim
```

## 构建
```bash
cd /root/opt/grok_reg-share
DOCKER_BUILDKIT=0 docker build -f docker/Dockerfile.slim -t cirstein/grok-register:slim -t cirstein/grok-register:latest .
docker push cirstein/grok-register:slim
docker push cirstein/grok-register:latest
```
