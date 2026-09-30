# 服务器部署与日常维护

本文保留首次搭建的历史模板。当前服务器已部署并完成 Linux/PostgreSQL 验证；现使用 `ubuntu` 账号与代码包更新。日常更新、备份、迁移及故障恢复请先阅读 [更新维护手册](update-maintenance-guide.md)，不要直接套用本文的 `deploy` 账号和 Git 拉取示例。

## 先理解四个地方

| 地方 | 放什么 | 更新代码时怎么处理 |
| --- | --- | --- |
| Windows 本机 `E:\game\bot` | 写代码、测试 | 在这里开发 |
| 私有 Git 仓库 | 代码、依赖清单、版本历史 | 本地提交推送，服务器拉取 |
| 服务器 `/srv/dzmm/app` | 正在运行的程序 | 按已测试的版本更新 |
| 服务器持久目录和数据库 | 玩家数据、登录状态、密钥 | 独立保留，不随代码覆盖 |

`.env`、`data/`、`.venv/`、Cookie、浏览器 Profile 和本地数据库不提交 Git。现有 `.gitignore` 已忽略主要本地目录。
服务器要自己安装 Linux 的 Python 环境和 Chromium，不能上传 Windows 的 `.venv` 或 `chrome.exe` 代替安装。

## 服务器怎么选

建议从 Ubuntu 24.04 LTS、2 vCPU、4 GB 内存、40 GB SSD 起步，运行一个 Core、一个 Worker 和本机 PostgreSQL。
这是开发测试的起始配置，不代表能承载 1000 人同时操作；后续看 CPU、内存、队列和消息量再扩容。
优先验证服务器到 `www.dzmm.io` 的网页、登录和 Socket 连通性。服务器地区的网页验证行为可能与本机不同。
当前是主动建立的 Socket 连接，不需要给 Bot 购买域名、配置公网 HTTPS 或开放 18120 端口。
Windows Server 也能运行，但不能直接使用本文 systemd 模板，通常占用更多资源；其优点是人工登录浏览器更直观。

## 服务器连接

在 Windows PowerShell 使用 `ssh 用户名@服务器IP`，也可用带图形文件管理的 SSH 客户端。
优先用 SSH 密钥登录；私钥保留本机。安全组只开放实际需要的 SSH 入口，最好限制管理员来源 IP。
PostgreSQL、Core、浏览器调试端口和远程桌面不要直接向整个公网开放。

后续命令假定：Ubuntu 24.04、有 sudo 权限的部署账号名为 `deploy`；服务账号固定为 `dzmmbot`。
若你的服务器使用其他账号名，先按实际情况调整 `deploy`，不要在不了解的服务器上照抄用户管理命令。

## 第一次部署

### 1. 建立专用运行账号和目录

以下为首次安装，已经存在的账号/目录不用重复创建：

```bash
sudo apt update
sudo apt install -y python3 python3-venv git postgresql
sudo adduser --system --group --home /var/lib/dzmm --shell /usr/sbin/nologin dzmmbot
sudo install -d -o deploy -g deploy -m 755 /srv/dzmm/app
sudo install -d -o dzmmbot -g dzmmbot -m 700 /etc/dzmm
sudo install -d -o dzmmbot -g dzmmbot -m 700 /var/lib/dzmm/profile /var/lib/dzmm/browsers /var/lib/dzmm/backups
```

### 2. 拉取代码、安装依赖

先把本地项目推送到你自己的私有 Git 仓库。服务器配置只读部署密钥即可拉取代码，不需要获得整个账号的写权限。
下面的 `YOUR_PRIVATE_REPOSITORY_URL` 必须替换成实际仓库地址：

```bash
git clone YOUR_PRIVATE_REPOSITORY_URL /srv/dzmm/app
cd /srv/dzmm/app
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m unittest -q
sudo .venv/bin/python -m playwright install-deps chromium
sudo -u dzmmbot env PLAYWRIGHT_BROWSERS_PATH=/var/lib/dzmm/browsers .venv/bin/python -m playwright install chromium --no-shell
```

`requirements.lock.txt` 固定了本地验证过的依赖版本，但跨平台仍需测试。如果某个锁定版本没有 Linux 包，应调查并同时更新本地/服务器依赖，不能只在服务器随意安装另一版。
服务器安装 Chromium 使用 Playwright 官方命令；`install_chromium.py` 是 Windows 下载备用工具，不能用于 Linux。

### 3. 准备 PostgreSQL 与配置

以下按 Ubuntu 默认的本机 Unix socket/peer 认证配置，数据库账号和服务账号同名，因此不必把数据库密码写在命令中：

```bash
sudo -u postgres createuser dzmmbot
sudo -u postgres createdb --owner=dzmmbot dzmm
sudo -u dzmmbot /srv/dzmm/app/.venv/bin/python /srv/dzmm/app/deploy/init_server_env.py /etc/dzmm/dzmm.env
sudo -u dzmmbot nano /etc/dzmm/dzmm.env
```

把 `DZMM_CHAT_URL` 中的占位符改为真实群链接。没有官方 Bot Token 时留空，使用普通账号发送。
配置文件只供服务账号和管理员读取；不要把内容截图或发送到聊天。
如果 PostgreSQL 的认证规则不是默认 peer，需要按服务器实际配置调整连接方式。

本机 SQLite 玩家数据不会自动变成 PostgreSQL 数据。现在只是测试账号，可在新库重新 `/加入`；如要保留数据，先单独做迁移并核对记录数，不要把 `.sqlite3` 当成 PostgreSQL 备份导入。

### 4. 在服务器上人工登录

这是本项目最容易漏掉的步骤：服务器 Chromium 也需要登录普通 DZMM 账号。
不能指望直接复制 Windows 浏览器 Profile 到 Linux 后仍能使用，系统加密和浏览器环境可能不同。

无桌面的 Ubuntu 需要临时配置远程桌面或 noVNC，或者使用支持图形桌面的服务器镜像。建议只通过 SSH 隧道访问图形桌面。
需要让图形环境中的浏览器以 `dzmmbot` 用户运行，使用 `/var/lib/dzmm/profile`。
仅有 SSH 文本窗口时，下面的 headed 登录命令无法凭空显示窗口；先配置正确的 DISPLAY/XAUTHORITY 与访问权限。

完成图形环境准备后，以服务账号加载服务器配置并登录：

```bash
sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh login
```

人工完成登录和可能的验证，进入目标群；返回登录命令的终端按 Enter，让浏览器正常关闭保存。
程序会在受限 Profile 目录内保存同站点登录状态快照，用于 Worker 重启恢复；该文件与 Cookie 同等敏感，不得下载分享或提交 Git。
不要同时启动 Worker 占用同一个 Profile。后续 Worker 无界面运行，日常无需一直打开远程桌面；登录失效时再停止 Worker、重新人工登录。
远程桌面的具体搭建命令取决于实际服务器环境，应在服务器信息确定后配置。

### 5. 安装后台服务

仓库提供了两份 systemd 模板，固定使用上面的路径和账号：

```bash
sudo cp /srv/dzmm/app/deploy/dzmm-core.service /etc/systemd/system/
sudo cp /srv/dzmm/app/deploy/dzmm-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now dzmm-core
```

先登记实际目标群，再启动 Worker。下方群 ID 是占位符：

```bash
sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh room YOUR_GROUP_ID
sudo systemctl enable --now dzmm-worker
sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status
```

最后让另一个群账号发送 `/加入` 和 `/我的`，检查实际回复与 `sent` 状态。服务器通过这一步才算部署成功。
本机 Bot 保持关闭，避免两个 Worker 用同一个账号处理同一群。

## 本地修改后如何更新服务器

推荐流程：本地修改 → 本地测试 → Git 提交/推送 → 服务器备份 → 停服务更新 → 重启验收。

本地 PowerShell：

```powershell
cd E:\game\bot
.\.venv\Scripts\python -m unittest -q
git status
# 只添加确认过的代码文件，不添加密钥、数据库或浏览器目录
git add dzmm_bot README.md
git diff --cached
git commit -m "说明这次改动"
git push
```

首次使用 Git 还要 `git init`、创建私有远端仓库并配置 remote；上面的示例假设这些已完成。
`git add` 的文件列表按实际改动调整，避免漏提交新增依赖和测试。

服务器以 `deploy` 账号进入代码目录：

```bash
cd /srv/dzmm/app
git status --short
git rev-parse HEAD
git fetch origin
```

确认工作区干净，保存上面的旧版本号，选择测试群通知的维护时段。当前停机期间没有历史消息自动补拉，玩家应等维护完成再发指令。

```bash
sudo systemctl stop dzmm-worker
sudo systemctl stop dzmm-core
sudo -u dzmmbot pg_dump -Fc dzmm -f /var/lib/dzmm/backups/dzmm-$(date +%Y%m%d-%H%M%S).dump
git pull --ff-only
.venv/bin/python -m pip install -r requirements.lock.txt
.venv/bin/python -m unittest -q
```

依赖安装或测试失败，就先不要启动新版。若 Playwright 版本发生变化，需要重新执行对应的浏览器安装；如果 service 模板变了，需要重新复制模板并 `daemon-reload`。

```bash
sudo systemctl start dzmm-core
sudo systemctl start dzmm-worker
sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status
```

再由另一个账号做一次真实群测试。`.env` 在 `/etc/dzmm`，Profile 和备份在 `/var/lib/dzmm`，因此正常 Git 更新不会覆盖它们。
不要在服务器直接改游戏逻辑后忘记同步回本地，否则下一次更新会冲突。

## 常见运维命令

| 目的 | 命令 |
| --- | --- |
| 查看进程状态 | `sudo systemctl status dzmm-core dzmm-worker --no-pager` |
| 查看队列/心跳 | `sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status` |
| 查看最近 Worker 日志 | `sudo journalctl -u dzmm-worker -n 100 --no-pager` |
| 实时看日志，Ctrl+C 只退出查看 | `sudo journalctl -u dzmm-worker -f` |
| 停止自动回复 | `sudo systemctl stop dzmm-worker` |
| 恢复自动回复 | `sudo systemctl start dzmm-worker` |
| 修改配置后重启 | `sudo systemctl restart dzmm-core dzmm-worker` |
| 取消开机自动启动并停止 | `sudo systemctl disable --now dzmm-worker dzmm-core` |
| 查看内存 | `free -h` |
| 查看磁盘 | `df -h` |

`enable` 表示开机自启，`start` 表示现在启动，`stop` 不会取消下次开机自启。
systemd 可在进程异常退出时重启它，但无法自动解决登录过期、人机验证或平台账号限制。

## 备份与回滚

- 每次上线前备份 PostgreSQL；正式运行后设置定时备份，并把加密备份保存到另一台机器/受限存储。单台服务器上的备份不能防止整机损坏。
- 备份必须做恢复演练，不能仅凭文件存在就认为可用。
- `.env` 和 Profile 是敏感资料，若备份，应加密并限制访问；不要与代码一起打包公开传输。跨机器恢复 Profile 不保证登录仍有效。
- 回滚代码：停 Worker/Core，把 Git 切回记录的旧 commit，安装该版本依赖，测试后启动。不要使用 `git reset --hard` 随手丢弃未知改动。
- 当前已使用 Alembic；改表结构必须追加 migration，并按更新维护手册先在备份副本上演练，再执行正式迁移。
- 数据库结构变更后的回滚不能只回滚 Git；恢复旧数据库可能丢失备份之后的玩家操作，应单独计划。

## 当前阶段的限制

只运行一个 Core 和一个 Worker，不要多开副本。数据库已使用 Alembic 管理；停机期间的历史消息补拉和多副本运行能力仍不能作保证。
本指南模板适用于首次搭建和小规模测试；正式扩大玩家规模前，需要验证数据库、重连、备份恢复、监控和容量。

参考：
- Playwright 浏览器安装：https://playwright.dev/python/docs/browsers
- systemd 服务文档：https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
