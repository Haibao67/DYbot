# Windows 上使用 Docker

本配置默认运行模拟模式：Core 和 Worker 分别在容器里运行，不连接聊天平台。
继续使用 `data/core.sqlite3`，数据保存在 Windows 项目目录，删除容器不会删除它。
首次运行前停止原先启动的 Core 和 Worker，避免端口占用或同时处理同一数据库。

## 1. 安装 Docker Desktop

按 [Docker 官方 Windows 安装说明](https://docs.docker.com/desktop/setup/install/windows-install/) 安装 Docker Desktop，使用 WSL 2 后端和 Linux 容器。
如果安装程序提示缺少 WSL，在管理员 PowerShell 执行 `wsl --install`，按提示重启后继续安装。
打开 Docker Desktop，等待引擎启动，再新开 PowerShell 验证：

```powershell
docker --version
docker compose version
docker info
```

## 2. 启动当前项目

```powershell
cd E:\game\bot
.\.venv\Scripts\python.exe -m dzmm_bot.manage init
docker compose up -d --build
docker compose ps
docker compose exec core python -m dzmm_bot.manage status
docker compose exec core python -m dzmm_bot.manage simulate /join
docker compose logs --tail 50 worker
```

首次构建需要联网下载 Python 镜像和依赖。Worker 日志出现 `[模拟回复]` 即表示模拟消息已处理。
健康检查地址为 http://127.0.0.1:18120/healthz 。
`.env` 中的内部密钥由初始化命令补齐，不会复制进镜像。
Docker 默认强制使用模拟模式，不受 `.env` 中 `DZMM_WORKER_MODE` 的影响。

## 3. 常用命令

```powershell
docker compose logs -f --tail 50
docker compose stop
docker compose start
docker compose down
```

`logs -f` 按 Ctrl+C 退出查看，不会停止容器。修改代码后执行 `docker compose up -d --build` 重建。

## 4. 真实聊天模式

当前 Docker 镜像不包含浏览器。真实模式采用 Docker Core + Windows Worker，便于在 Windows 手动登录。
在 `.env` 中设置 `DZMM_WORKER_MODE=live`，确认聊天地址和账号配置正确，并保持 `DZMM_CORE_URL=http://127.0.0.1:18120`。
停止其他 Worker 后，在 PowerShell 执行：

```powershell
docker compose stop worker
$env:DOCKER_WORKER_MODE = 'live'
docker compose up -d --build core
$env:PLAYWRIGHT_BROWSERS_PATH = 'E:\game\bot\data\browsers'
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m dzmm_bot.manage login
.\.venv\Scripts\python.exe -m dzmm_bot.manage room '替换为真实群ID'
.\.venv\Scripts\python.exe -m dzmm_bot.worker
```

登录时按窗口提示完成登录并按 Enter 保存。以后重新创建真实模式 Core 时，要在当前 PowerShell 重新设置 `DOCKER_WORKER_MODE=live`，只启动 `core`。
真实平台接口和账号权限仍需单独验证；Docker 不会修复 `chatroom.addBot` 的 GET/POST 错误。
此方案不涉及 PostgreSQL 安装或数据库迁移。
