# DZMM 游戏测试 Bot：Core + Browser Worker

服务器部署、Git 更新、后台运行、备份与回滚见 [服务器使用手册](docs/server-guide.md)。部署模板在 `deploy/`，尚未在云服务器实机验收。

新版以普通账号浏览器登录和 Socket.IO 接收消息，Core 执行游戏指令并保存收发队列，Worker 发回平台。
Socket.IO 实际运行在专用 Chromium 内，使用同一个浏览器网络通道和登录会话；Python 负责事件验证、业务与队列。
无需为 Core 配置公网 HTTPS，也不要将 Core 管理接口通过隧道公开。原 Webhook 示例归档在 `legacy_webhook.py`。

## 已实现与边界

- M0：中文 `/加入`、`/我的`、`/帮助`、`/救济`；全局唯一账户、不可变货币/库存账本、管理补偿与冻结。
- M1：`/牧场` 购买、喂食、收获、出售、升级；确定性生产、动态市场价、5% 交易税奖池与 `/排行 总榜`。
- Alembic 自动迁移保留旧收发数据；交付与上线说明见 [M0/M1 验收记录](docs/m0-m1-delivery.md)。
- 持久化收件箱去重；业务更新与待发消息在同一数据库事务提交。
- 收发分离、任务租约、限次重试、每个房间有序处理、Worker 心跳。
- 专用 Chromium Profile 手动登录；重连时重新获取网页短期 Token。
- 每 5 秒同步启用房间；群文本优先官方 Bot，明确拒绝后才尝试普通账号；私聊只走普通账号。
- Bot 发送必须收到 HTTP 200、`ok=true` 和非空 `result.message_id`。
- 模拟 Worker 不连接平台，结果标记 `simulated`，不会假装真实发送成功。

**这是按指南重建的测试版本，未取得指南引用的原项目源码。已验证真实账号登录、浏览器 Socket 认证、目标群订阅及真实群指令回复；尚未验证私聊发送及 PostgreSQL。**

2026-09-25 旧版测试指令的真实群验收（不代表 M0/M1 已上线）：另一个账号依次发送 `/join` 和 `/profile`，Core 分别接收并处理一次，两个出站任务均为 `sent` 且有平台消息 ID，用户确认收到两条回复。本次使用普通账号发送，没有配置官方 Bot Token。
`user.getMe` 默认从网页自身的响应中识别，已适配平台实际的 tRPC 批量请求和 HTTP 207。未登录时会明确要求重新登录；格式变化时不会猜测发送者身份。
当前游戏仅处理文本；图片上传、分享、引用、撤回、自动发现新私聊、Web 管理后台/noVNC 暂未实现。Windows 使用手动登录窗口和管理命令。

## 1. 本地启动与测试

所有命令都在 PowerShell 的 `E:\game\bot` 目录执行。

```powershell
cd E:\game\bot
# 已有虚拟环境时跳过下面这一行
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m dzmm_bot.manage init
```

官方 Socket.IO 浏览器客户端随代码保存在 `dzmm_bot/vendor`，保留 MIT 许可证和版本来源。
如该目录缺失，可运行 `python install_socket_client.py` 从 npm 官方仓库下载，并验证 SHA-512 完整性。

`init` 只补齐缺失配置，并生成内部通信与管理密钥，不覆盖旧 Token。不要把 `.env` 内容发到聊天里。
默认以 SQLite `data/core.sqlite3` 测试新架构。旧 `data/bot.sqlite3` 保留，新旧数据不会自动合并。

窗口 A 启动 Core（仅一个进程，不添加 `--workers`）：

```powershell
.\.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 18120
```

窗口 B 启动 Worker：

```powershell
.\.venv\Scripts\python -m dzmm_bot.worker
```

窗口 C 注入本地模拟指令：

```powershell
.\.venv\Scripts\python simulate.py /加入
.\.venv\Scripts\python simulate.py /我的
.\.venv\Scripts\python -m dzmm_bot.manage status
```

在窗口 B 查看模拟回复；状态应包含 `simulated` 计数。健康检查为 `http://127.0.0.1:18120/healthz`。
默认入口已经换为 Core，没有 `/webhook`。旧版 uvicorn/Cloudflare 窗口如仍运行，请先自行关闭；新版不需要这些隧道。

## 2. 使用自己的普通账号登录

默认使用 Playwright 配套 Chromium，并创建项目专用 Profile。`.env` 中设置 `DZMM_BROWSER_CHANNEL=chromium`。
如需备用浏览器，可明确设为 `chrome` 或 `msedge`；程序不会自动切换到 Edge。
首次安装配套 Chromium（已安装则跳过）：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH='E:\game\bot\data\browsers'
$env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT='120000'
.\.venv\Scripts\python -m playwright install chromium --no-shell
```

若 Playwright 下载超时，但系统网络能访问官方地址，可使用备用安装工具：

```powershell
.\.venv\Scripts\python install_chromium.py
.\.venv\Scripts\python verify_chromium.py
```

备用工具下载当前 Playwright 要求的官方 Windows 64 位安装包，分段下载并校验完整性；自检使用临时 Profile，不登录 DZMM。

编辑 `.env` 中两个网址，使用你实际登录的站点和目标群：

```dotenv
DZMM_LOGIN_URL=https://www.ivorune.xyz/sign-in
DZMM_CHAT_URL=https://www.ivorune.xyz/chat?c=你的群聊ID
DZMM_BROWSER_PROFILE=data/browser-profile
```

示例网址来自指南；本次实际验证的站点为 `https://www.dzmm.io`，当前 `.env` 已配置该站点。
新环境请同时修改登录和群聊网址，不要把旧站点 Token 自动用于新域名。
在 Worker 停止时运行：

```powershell
.\.venv\Scripts\python -m dzmm_bot.manage login
```

在打开的专用浏览器手动登录，完成必要验证，进入目标群。返回 PowerShell 按 Enter，程序正常关闭浏览器并保留 Profile。
不要与 Worker 同时打开同一个 Profile。Profile 含账号登录凭证，保留在当前用户的受限磁盘目录，不上传或共享。

## 3. 登记群聊与切换真实模式

Core 运行时，用管理员命令登记房间（账号必须已经加入）：

```powershell
.\.venv\Scripts\python -m dzmm_bot.manage room "你的群聊ID"
```

私聊必须是账号已建立且可访问的会话，登记的是房间 ID，不是对方用户 ID：

```powershell
.\.venv\Scripts\python -m dzmm_bot.manage room "已有私聊房间ID" --kind private
```

切换前停止 Core 和 Worker，编辑 `.env`：

```dotenv
DZMM_WORKER_MODE=live
# 下面两项可选；不填就使用普通账号发群文本
DZMM_BOT_API_TOKEN=你的官方BotToken
DZMM_BOT_ID=你的Bot身份ID
```

然后重启 Core 和 Worker。在群里发送 `/加入`、`/我的`，观察真实回复和 `manage status` 的 `sent` 数量。
Bot 必须已加入对应群；`DZMM_BOT_ID` 用于过滤自己的 Bot 消息。配置不支持的旧 `DZMM_BOT_TOKEN` 不会自动复制到新域名。
模拟产生的未发送任务应先由模拟 Worker 消化，再切换真实模式。正式使用建议另建 PostgreSQL 数据库并重新登记真实群。

如果 Worker 报 `login_or_identity_required`：先重新人工登录。若仍失败，核对网页中 `user.getMe` 的实际 GET 地址和 JSON 结构，并配置：

```dotenv
DZMM_ME_PATH=/实际的本站接口路径
DZMM_ME_ID_FIELD=result.data.json.id
```

这里的路径和字段只是配置形式，**不是已确认的平台接口**。默认也支持从网页的单个 `user.getMe` 响应中解析常见 RPC 包装。短期 Token 接口按指南读取 `/api/auth/token` 的 `access_token`；平台若改变结构，需要调整 `browser.py`。

## 4. PostgreSQL

本机未检测到 PostgreSQL 或 Docker，因此没有自动安装系统级数据库。已使用 SQLAlchemy + psycopg 支持 PostgreSQL。
准备好数据库后，把 `.env` 改为：

```dotenv
DZMM_DATABASE_URL=postgresql+psycopg://你的用户:你的密码@127.0.0.1:5432/dzmm
```

重启 Core 首次建表。SQLite 数据不会自动迁移；新库需重新登记群。此测试版建表用 `create_all`，尚未引入生产数据库版本迁移。
当前必须使用一个 Core 和一个 Worker；即使用 PostgreSQL，也不要增加进程副本，跨进程任务领取锁尚未实现。

## 5. 故障与恢复

- `disconnected`：检查普通账号登录、站点域名、Socket 连接和 `user.getMe` 解析。
- `worker_stale=true`：Worker 已停止或长时间无法访问 Core。
- `failed`：明确拒绝或限流重试 3 次用尽；不无限重试。
- `uncertain`：发送后超时、返回不完整或 Worker 中断，无法确认平台是否收到。该房间后续发送暂停，其他房间可继续。
- Core 暂时不可用：入站最多重试 3 次，持续不可用时消息可能丢失；没有自动历史补拉。

对于 `uncertain`，先在平台确认是否已发出，`manage status` 列出任务 ID。确认后人工结束任务：

```powershell
# 平台确认已发送，必须提供真实消息 ID
.\.venv\Scripts\python -m dzmm_bot.manage resolve "任务ID" --as sent --platform-id "真实消息ID"
# 确认放弃此条发送，不会重新发送
.\.venv\Scripts\python -m dzmm_bot.manage resolve "任务ID" --as failed
```

关闭某个群的监听和后续任务领取：

```powershell
.\.venv\Scripts\python -m dzmm_bot.manage room "群聊ID" --disable
```

普通账号发送全局至少间隔 2 秒、同一私聊至少间隔 5 秒，明确限流时冷却 60 秒。这是保守测试值，不是平台承诺的限额。
仅当官方 Bot 明确返回 `ok=false` 的非服务端故障时才回退普通账号。超时、5xx、缺少成功消息 ID 都不会自动换通道重发。

## 6. 验证

```powershell
.\.venv\Scripts\python -m unittest -v
```

包含旧版回归以及新 Core 的授权、去重、事务数据、房间隔离、持久化、租约恢复、未知发送状态、限次重试、模拟 Worker、平台发送响应和 Socket 身份测试。
自动测试使用本地数据库和模拟平台，不等同于真实平台验收。
#   D Y b o t  
 