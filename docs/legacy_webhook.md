# 旧版 Webhook 测试 Bot（归档，请使用项目根目录的新说明）

对接用户提供的 dzmm.io Bot 文档。支持 `/start`、`/help`、`/join`、`/profile`。
普通聊天、机器人消息和其他指令会被忽略。玩家账号全局唯一，每个群分别记录加入状态。

## Windows 本地启动

在此目录打开 PowerShell，首次安装：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

如果已有 `.env`，保留现有配置，不要覆盖。启动：

```powershell
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

保持窗口运行，在另一个 PowerShell 窗口进入此目录：

```powershell
.\.venv\Scripts\python simulate.py /start
.\.venv\Scripts\python simulate.py /join
.\.venv\Scripts\python simulate.py /profile
```

默认 `BOT_DRY_RUN=true`，回复显示在请求结果中，不会发往平台。
健康检查：http://127.0.0.1:8000/health 。SQLite 数据保存在 `data/bot.sqlite3`，重启后保留。
运行自动测试：

```powershell
.\.venv\Scripts\python -m unittest -v
```

## 接入真实群聊

1. 为本机 8000 端口配置公网 HTTPS 转发或部署到有 HTTPS 的服务器。Webhook 填写 `https://你的公网域名/webhook`，不能填写 localhost。
2. 在平台 `/studio/bots` 创建私有测试 Bot，填写上面的 Webhook，保存 API Token，并复制 Webhook Secret。
3. 编辑 `.env`：将 `BOT_DRY_RUN` 改为 `false`，将 `DZMM_BOT_TOKEN` 和 `DZMM_BOT_SECRET` 填成平台的真实值；保持 `BOT_API_BASE=https://dzmm.io`。
4. 停止并重启服务，使配置生效。健康检查应显示 `live`。
5. 由创建者在自己管理的普通群聊，通过输入框 `+` → Add Bot 添加机器人；scenario 群聊不支持。
6. 在群里发送 `/join` 和 `/profile` 验证。

不要把 `.env` 提交或分享。Token 轮换后需更新配置并重启。

## 错误与测试范围

- 401：Webhook Secret 不匹配，检查 `X-Telegram-Bot-Api-Secret-Token`。
- 502：发送接口失败，检查网络、API Token 以及 Bot 是否仍在目标群内。平台返回 403 时也会在本服务体现为 502。
- 没有群内回复：确认已关闭模拟模式、Webhook 公网可访问、Bot 在普通群中，并使用上述四个指令。

此版本用于本地功能测试，使用单进程、单 worker，串行处理指令。不是 100～1000 人并发的生产配置。
消息按群 ID + 消息 ID 去重；业务处理结果先保存，发送失败后同一请求可重新发送。
平台是否自动重试未知，此版本没有后台重试队列。若平台已接受回复但网络中断，或程序在发送后、记录成功前退出，重试仍可能重复发出回复。
测试覆盖认证、指令、重启持久化、群隔离、去重及模拟上游失败重试；真实平台联调需要你自己的凭证和公网 HTTPS 地址。
后续按策划案引入 PostgreSQL、数据库迁移、后台任务与游戏逻辑。
