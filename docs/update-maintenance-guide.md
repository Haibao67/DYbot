# DYbot / 铃露内容更新与维护手册

当前正式发布标记：`5a4e915be7e6-20260930-142029`，迁移 head `0027_red_packet_duration`。V0.8.9 已发布：喂食自动收取、播种自动收获、投产回执精简、普通回复安全短语压缩及群聊强制接生修复。发布包 SHA256 `e05a7b097c23641161145c324a6599f1b67dadb5f6b5f149573733855920288b`。详见本文末尾最新发布记录。

Core、Worker、PostgreSQL 均 active，Core 健康，Worker connected 且心跳新鲜；最新备份目录 `/var/backups/dzmm/5a4e915be7e6-20260930-142029/`。上线后新增1条短群回复因平台重复内容拒绝失败；V0.8.9公告6群全部发送成功。此前未部署说明为开发历史记录，当前状态以本文末尾发布记录为准。

最后更新：2026-09-30。适用于 Windows 本地开发、Ubuntu 服务器代码包发布及现有 Core + Browser Worker 架构。

本文是后续更新维护的主要入口。内容采用中文，文件名固定为 `update-maintenance-guide.md`。项目文档统一放在现有 `docs/`，不另建 `doc/`。

## 1. 任务入口与执行范围

每次接到“更新内容”“修改功能”“推送服务器”“部署”“维护”“修复运行故障”时：

1. 先执行 `git status --short`、`git diff --stat`，读取 `docs/ruihe-progress.md`。
2. 根据下表读取本文相关章节；按需读取 `docs/codex-workflow.md` 和本轮计划小节。
3. 明确本轮改动、目标版本、测试范围及是否已获服务器部署授权。
4. 保留未跟踪文件、未提交改动、老指令、历史迁移和玩家资产；不顺手重构。
5. 以实际执行结果报告完成情况，并更新本文末尾发布记录与开发进度。
6. 每次服务器更新必须以 Bot 恢复运行为交付条件：启动 Core 和 Worker，验证 Core 健康、Worker 已连接且心跳未过期；不得在上传、解压或迁移后直接结束任务。仅用户明确要求保持停止时例外。

| 任务 | 应读取章节 | 完成条件 |
| --- | --- | --- |
| 文案、帮助、面板调整 | 3、4、14 | 输出符合规则，相关快照和引用链验证完成 |
| 游戏玩法、经济规则修改 | 3、4、8、14 | 定点/关联测试通过；涉及旧数据时完成迁移验证 |
| 已提交版本发布 | 2、5—11、14 | 包哈希一致、备份可恢复、迁移通过、服务及心跳健康 |
| SSH 或运行故障 | 5、12 | 找到具体原因并恢复；不重复尝试无效命令 |
| 回退或数据恢复 | 10、13、14 | 明确兼容性和数据损失边界，完成恢复后验收 |

部署授权包含本次更新必要的连接、上传、停启服务、备份和已审阅迁移，不必逐步重新询问。工具要求的沙盒外授权仍按工具机制处理。仅请求修改代码或文档不表示授权部署。新增 SSH 访问权限、改变防火墙或将正式数据库恢复到旧时间点，需要确认具体范围。

文档中的命令是操作模板，不是定时任务；读取本文不会自动连接服务器。不要在未经授权的正式群发送测试消息或公告。

## 2. 当前已验证环境

以下信息最近核验于 2026-09-27；以后每次发布都要确认目标没有变化。

| 项目 | 当前值 |
| --- | --- |
| 本地仓库 | `E:\game\bot` |
| 本地 Shell | PowerShell |
| 本地 Python | `.venv\Scripts\python.exe` |
| 服务器 | `119.28.232.86`，Ubuntu 24.04 |
| SSH 账号 / 端口 | `ubuntu` / `22`，可使用 sudo |
| 本机私钥 | `C:\Users\ADMIN\Downloads\bot.pem` |
| 服务账号 | `dzmmbot` |
| 正式代码 | `/srv/dzmm/app`，现有代码由 root 管理 |
| 候选发布目录 | `/srv/dzmm/releases/<release-id>` |
| 正式 Python | `/srv/dzmm/app/.venv/bin/python`，已验证 Python 3.12.3 |
| 配置与密钥 | `/etc/dzmm/dzmm.env` |
| 浏览器登录数据 | `/var/lib/dzmm/profile` |
| 浏览器程序 | `/var/lib/dzmm/browsers` |
| PostgreSQL | 数据库 `dzmm`，角色 `dzmmbot`，本机 Unix socket / peer 认证 |
| 服务 | `dzmm-core`、`dzmm-worker`、`postgresql` |
| Core HTTP | `127.0.0.1:18120`，仅本机监听 |
| 发布方式 | SSH/SCP 代码包；服务器未配置 Git 远端 |
| 当前发布基准提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e`（含经验证工作区覆盖，以包 SHA 锁定实际内容） |
| 当前发布包 / SHA256 | `5a4e915be7e6-20260928-230002` / `bed68e61529321af3de0050eaa97dd69af5fc72de4315fd64aae787f9d51a39d` |
| 当前迁移版本 | `0024_horse_feed_budget` |

GitHub 推送成功不代表服务器已更新；上传成功也不代表程序已发布。不要在正式目录直接执行旧指南中的 `git pull`。

配置、数据库和浏览器登录数据独立于代码。代码包不得包含 `.env`、私钥、Cookie、数据库、备份、Windows `.venv` 或浏览器 Profile。只运行一个 Core 和一个 Worker，避免本地和服务器同时处理同一群。

## 3. 内容和玩法修改规范

| 改动内容 | 优先查看 |
| --- | --- |
| 玩法规则、阶段目标 | `docs/reference/ruihe.md`、`docs/ruihe-codex-plan.md` 对应小节 |
| 指令解析、别名、参数 | `dzmm_bot/application/command_router.py` |
| 游戏事务、库存和结算 | `dzmm_bot/application/services.py` |
| 生产公式、倍率、周期 | `dzmm_bot/domain/economy.py` |
| 中文消息与动态面板 | `dzmm_bot/presentation/` |
| 数据结构与迁移 | `dzmm_bot/persistence/schema.py`、`dzmm_bot/persistence/migrations/` |
| 原生引用及收发链 | `dzmm_bot/core.py`、`worker.py`、`gateway.py`、`store.py`、`persistence/transport.py` |

- 先扩展现有函数；保持 Core 负责状态和事务、Worker 负责平台连接及收发的边界。
- 所有玩家指令回复，包括错误和长面板，都必须指向对应的原消息；主动公告不引用玩家消息。
- presentation 只展示准备好的状态，不修改资产、喂食或生产状态。
- Schema 变更只追加 Alembic migration，不修改已发布迁移或历史规则快照。
- 余额、库存、账本和 outbox 的事务一致性必须保留；重复收取、重试和重复迁移不得重复发放资产。
- 产品规则缺失或冲突记录到 `docs/ruihe-open-questions.md`。天气概率仍待提供，当前不能自行启用天气。
- 内容快照变化必须来自已确认的产品要求，不通过删断言或机械重录输出掩盖错误。

### 回复性能观测（5.5A，已发布）

管理员可读指标、时间戳口径、脱敏日志与本地模拟基线见 `docs/reply-performance-5.5A-operations.md`。正式环境已部署 `0011_outbound_timing`。不得把本地固定时钟测试值当作生产速度；5.5B 调整发送策略前先采集同一窗口的生产基线。

5.5B 已以安全默认值发布；Socket 限速配置、管理员可读状态和模拟负载见 `docs/reply-performance-5.5B-operations.md`。更短间隔另需授权测试群与同窗口基线，不因代码发布自动启用。

### 出站队列止塞与全局限速

出站消息由 Core 持久化排队、Worker 异步发送。发送领取全局匀速限制为每 1 秒一条（最多 60 条/分钟、无突发额度），跨群共用并跨进程/重启保留。平台发送失败不自动重发；`uncertain` 表示平台是否已收到无法确认，保持待人工核对，但不再挡住同群后续消息。Worker 对 Core 结果 ACK 的短暂 HTTP 重试只重报发送结果，不会再次发送平台消息。

只有管理员明确要求清空队列时，才在确认服务状态后运行 `python -m dzmm_bot.manage clear-outbound`（Linux 正式环境通过 `sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh clear-outbound` 运行）。该操作将 `pending/leased/sending/uncertain` 标记为 `cancelled` 并保留行及审计；公告投递同步标为失败。先记录 `/admin/status` 中的队列计数，清空后再复核。不要用 SQL 删除历史消息。

## 4. 本地开发、测试和提交

以下命令在本地 PowerShell 执行：

```powershell
Set-Location 'E:\game\bot'
git status --short
git diff --stat
git diff --check
```

只读本轮涉及的源码和测试。修改后先运行定点及直接相关测试，例如：

```powershell
& '.\.venv\Scripts\python.exe' -m unittest test_game.GameTests test_game.RuleTests test_game.MigrationTests -q
if ($LASTEXITCODE -ne 0) { throw 'Related tests failed' }
```

发布前集中全套；已有同一代码状态的有效结果可复用，不无故反复执行：

```powershell
& '.\.venv\Scripts\python.exe' -m unittest discover -s . -p 'test_*.py' -q
if ($LASTEXITCODE -ne 0) { throw 'Release tests failed' }
```

Core/Worker 变更或发布前需要隔离冒烟：`python smoke_test.py`，该脚本使用临时 SQLite 和 simulate 模式。数据库迁移需要旧库副本演练，不能用新建空库测试代替。真实 Reply UI 验收只在明确授权的测试群进行。

纯文档变更执行 `git diff --check` 和命令/路径审阅即可，不运行游戏全套测试。测试出现预期异常日志时以最终断言和退出码判断，不能只看到 traceback 就宣称失败，也不能只看到部分成功日志就宣称通过。

提交时只添加本轮明确修改的文件，不使用无差别 `git add .`。先检查 `git diff --cached`，再按用户授权提交和推送。不擅自提交未跟踪文件。发布包应来自已审阅且已提交的版本；如用户要求 GitHub 与服务器同步，先核实远端包含该提交。

## 5. SSH 连接与私钥权限

PowerShell 设置本轮变量：

```powershell
$DeployTarget = 'ubuntu@119.28.232.86'
$DeployKey = Join-Path $env:USERPROFILE 'Downloads\bot.pem'
if (-not (Test-Path -LiteralPath $DeployKey)) { throw 'SSH key not found' }
ssh -i $DeployKey -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=15 $DeployTarget 'id -un; hostname; sudo -n true'
if ($LASTEXITCODE -ne 0) { throw 'SSH preflight failed' }
```

首次连接出现未知主机指纹时，通过可信控制台核验后再接受。不要通过关闭主机校验绕过问题。`BatchMode=yes` 下，加密私钥需要事先安全解锁到 agent，或由用户交互登录；不把口令写入脚本。

自动更新脚本现在在测试和上传前显式读取当前 Windows 用户的 `.ssh/known_hosts`，要求其中已有目标主机记录，并使用该文件进行严格校验。受限沙盒若无法读取此文件会在预检阶段停止；在可读取原有记录的受信会话执行，不要改用 `StrictHostKeyChecking=no`。脏工作区发布另需 `-BaselinePackage` 指向与服务器当前 `DEPLOYED_PACKAGE_SHA256` 一致的本地发布包；脚本将逐文件输出包差异，要求输入新包哈希短码后才上传。具体命令见 `docs/server-update-script-usage.md`。

已知故障：`bot.pem` 曾因允许 `CodexSandboxUsers` 读取而被 OpenSSH 拒绝。只有出现 `UNPROTECTED PRIVATE KEY FILE` / `bad permissions` 时，才修复这个确定文件的 ACL：

```powershell
$KeyOwner = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$KeyAcl = New-Object System.Security.AccessControl.FileSecurity
$KeyAcl.SetOwner($KeyOwner)
$KeyAcl.SetAccessRuleProtection($true, $false)
$KeyRule = New-Object System.Security.AccessControl.FileSystemAccessRule($KeyOwner, 'FullControl', 'Allow')
$KeyAcl.AddAccessRule($KeyRule)
Set-Acl -LiteralPath $DeployKey -AclObject $KeyAcl
```

本段只将指定私钥限制为当前用户使用，不改密钥内容。不要递归修改 `.ssh` 或下载目录权限。没有 ssh-agent 不等于无法登录，当前未加密密钥可用 `ssh -i` 直接指定。修复权限后仍 `Permission denied (publickey)`，再核对用户、密钥对应关系和服务端授权，不循环重试，不自行绑定新云密钥。

## 6. 生成可追溯发布包并上传

推荐使用 `git archive` 打包已提交内容，保留旧包。以下继续在本地 PowerShell 执行：

```powershell
Set-Location 'E:\game\bot'
$PendingChanges = git status --porcelain
if ($LASTEXITCODE -ne 0) { throw 'Cannot read Git status' }
if ($PendingChanges) { throw 'Review uncommitted/untracked files before release; do not delete them automatically' }
$DeployCommit = (git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve release commit' }
$DeployShort = $DeployCommit.Substring(0, 12)
$ReleaseId = '{0}-{1}' -f $DeployShort, (Get-Date -Format 'yyyyMMdd-HHmmss')
$DeployPackage = "data/deploy-$ReleaseId.tar.gz"
if (Test-Path -LiteralPath $DeployPackage) { throw 'Release package already exists' }
New-Item -ItemType Directory -Path 'data' -Force | Out-Null
git archive --format=tar.gz --output=$DeployPackage $DeployCommit app.py legacy_webhook.py requirements.txt requirements.lock.txt README.md alembic.ini test_game.py test_bot.py test_core.py smoke_test.py simulate.py install_socket_client.py dzmm_bot deploy docs tests
if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
Get-FileHash -LiteralPath $DeployPackage -Algorithm SHA256
Write-Output "Release ID: $ReleaseId"
Write-Output "Commit: $DeployCommit"
scp -i $DeployKey -o IdentitiesOnly=yes -o BatchMode=yes $DeployPackage "${DeployTarget}:/home/ubuntu/deploy-$ReleaseId.tar.gz"
if ($LASTEXITCODE -ne 0) { throw 'Upload failed' }
```

白名单必须覆盖本轮新增的运行时文件；增加顶层模块或测试文件时同步审阅白名单。确认归档中包含迁移文件与规则 JSON。`git archive` 不包含未提交内容，不能把工作区测试通过误当成另一个提交已测试。

`deploy/package_release.py` 支持 `--output` 指定唯一归档路径。默认行为仍写入固定 `data/deploy-upload.tar.gz`；一键更新脚本总是传唯一发布路径。只有显式 `--worktree` 时会打包已跟踪和未跟踪工作区文件，并受其目录白名单限制。

### 一键更新脚本

完整参数、工作区模式和故障处理见 [服务器更新脚本使用说明](server-update-script-usage.md)。

Windows PowerShell 中从仓库根目录运行：

```powershell
pwsh -NoProfile -File .\deploy\Update-Server.ps1
```

脚本会检查 Git 干净状态、运行本地全套测试、生成唯一发布包并上传，在服务器候选目录运行全套测试和隔离冒烟；候选通过后要求手动输入 `DEPLOY`，再备份应用和 PostgreSQL、更新正式目录、启动 Core 与 Worker 并核验健康、心跳和账本。默认只打包干净的已提交 HEAD。只有审阅过完整工作区改动后才使用 `-AllowDirtyWorktree`；它会把允许目录中的已跟踪及未跟踪内容一并打包，但不会打包 `.env`、`data/`、数据库或浏览器目录。SSH 主机仍使用系统默认主机密钥校验。

该脚本目前仅自动部署“不改变 Alembic head”的代码更新。若迁移 head 与生产库不同，候选验证会停止且不触碰正式服务；按第 8 节完成人工数据库副本演练和数据校验后，再执行正式更新步骤。正式切换失败时，由脚本保留故障目录、恢复备份代码并重新启动旧 Core/Worker；数据库不自动回滚。

## 7. 服务器预检与候选目录

先通过 SSH 登录；本节后续命令在服务器 Bash 中逐段执行，不能直接粘贴到 PowerShell。将下面两个占位值替换为第 6 节实际输出；不要运行未替换的模板。

```bash
set -euo pipefail
export RELEASE='REPLACE_WITH_RELEASE_ID'
export COMMIT='REPLACE_WITH_FULL_COMMIT'
[[ "$RELEASE" =~ ^[0-9a-f]{12}-[0-9]{8}-[0-9]{6}$ ]]
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]]
export STAGE="/srv/dzmm/releases/$RELEASE"
export BACKUP="/var/backups/dzmm/$RELEASE"
export PACKAGE="/srv/dzmm/releases/deploy-$RELEASE.tar.gz"
export CHECK_DB="dzmm_check_${RELEASE//-/_}"
sudo -n true
systemctl is-active dzmm-core dzmm-worker postgresql
sudo cat /srv/dzmm/app/DEPLOYED_COMMIT
sudo -u postgres psql -d dzmm -Atc 'SELECT version_num FROM alembic_version'
df -h /srv/dzmm /var/lib/postgresql
sudo du -sh /srv/dzmm/app
sudo -u postgres psql -d dzmm -Atc "SELECT pg_size_pretty(pg_database_size('dzmm'))"
sha256sum "/home/ubuntu/deploy-$RELEASE.tar.gz"
```

核对哈希与本地完全一致；空间须能容纳候选目录、完整旧代码/虚拟环境备份、数据库副本及恢复余量。基线服务异常先诊断，不把更新当作未知故障的通用修复。记录当前服务重启计数和队列状态供前后比较。

```bash
test ! -e "$STAGE"
sudo test ! -e "$BACKUP"
sudo install -d -o dzmmbot -g dzmmbot -m 700 "$STAGE"
sudo install -d -o postgres -g postgres -m 700 "$BACKUP"
sudo install -o dzmmbot -g dzmmbot -m 600 "/home/ubuntu/deploy-$RELEASE.tar.gz" "$PACKAGE"
sudo -u dzmmbot tar -xzf "$PACKAGE" -C "$STAGE"
sudo sed -i 's/\r$//' "$STAGE/deploy/manage.sh"
sudo -u dzmmbot sh -n "$STAGE/deploy/manage.sh"
```

服务账号不能穿过受限的 `/home/ubuntu` 目录，所以先用 `sudo install` 把包放到发布目录，再解压。不能通过放宽整个 home 目录权限解决。

### 7.1 依赖未变化

```bash
sudo cmp /srv/dzmm/app/requirements.lock.txt "$STAGE/requirements.lock.txt"
export TEST_PY='/srv/dzmm/app/.venv/bin/python'
```

`cmp` 相同才使用此分支；不同不是游戏测试失败，改走下节。本文使用 `set -e`，失败可能退出当前 Shell；重新登录时重新设置本轮变量，不重复创建或覆盖已有发布目录。

### 7.2 依赖变化

在独立候选虚拟环境安装，避免提前改变正在运行的正式依赖：

```bash
sudo -u dzmmbot python3 -m venv "$STAGE/.venv"
sudo -u dzmmbot "$STAGE/.venv/bin/python" -m pip install -r "$STAGE/requirements.lock.txt"
sudo -u dzmmbot "$STAGE/.venv/bin/python" -m pip check
export TEST_PY="$STAGE/.venv/bin/python"
```

若 Playwright 版本变化，先准备匹配的 Linux Chromium，保留旧浏览器以便回退；必要的系统依赖另按实际差异安装。不得删除当前 profile 或把 Windows 浏览器拷到服务器。

```bash
sudo -u dzmmbot env PLAYWRIGHT_BROWSERS_PATH=/var/lib/dzmm/browsers PLAYWRIGHT_SKIP_BROWSER_GC=1 "$TEST_PY" -m playwright install chromium --no-shell
```

### 7.3 候选代码验证

```bash
sudo -u dzmmbot sh -c 'cd "$1" && "$2" -m unittest discover -s . -p "test_*.py" -q' sh "$STAGE" "$TEST_PY"
sudo -u dzmmbot sh -c 'cd "$1" && "$2" smoke_test.py' sh "$STAGE" "$TEST_PY"
```

候选测试不加载 `/etc/dzmm/dzmm.env`，不得指向正式库，不启动正式 Worker。任何失败先处理，尚未停服时继续保留旧服务。

## 8. 数据迁移演练

没有 schema 变化也应核对迁移版本；有变化时必须在正式备份的副本上演练，不能直接试正式库。

```bash
sudo -u postgres pg_dump -Fc -f "$BACKUP/rehearsal.dump" dzmm
sudo chmod 600 "$BACKUP/rehearsal.dump"
sudo -u postgres pg_restore --list "$BACKUP/rehearsal.dump" >/dev/null
sudo -u postgres createdb --owner=dzmmbot "$CHECK_DB"
sudo -u postgres pg_restore --exit-on-error --dbname="$CHECK_DB" "$BACKUP/rehearsal.dump"
```

以下检查器适用于当前 P0/P2 的“新增字段、追加配置，旧值保持不变”迁移。它比较所有旧表原有字段的数据摘要，不打印玩家内容。新版本若需要合法的数据转换，必须先设计转换前后对账断言；不能删除失败检查后强行上线。大型库应改为分块核验，避免一次把整表读入内存。

在服务器创建本轮临时检查器；它不属于正式业务源码：

```bash
sudo -u dzmmbot tee "$STAGE/deploy_migration_check.py" >/dev/null <<'PY'
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from alembic.config import Config
from alembic.script import ScriptDirectory
import dzmm_bot.persistence.migrate as migration_module

database = sys.argv[1]
if database != 'dzmm' and not re.fullmatch(r'dzmm_check_[a-f0-9]{12}_[0-9]{8}_[0-9]{6}', database):
    raise SystemExit('Unexpected database name')
engine = create_engine('postgresql+psycopg://dzmmbot@/' + database)
cfg = Config()
cfg.set_main_option('script_location', str(Path(migration_module.__file__).parent / 'migrations'))
expected_head = ScriptDirectory.from_config(cfg).get_current_head()
columns = {table: [c['name'] for c in inspect(engine).get_columns(table)]
           for table in inspect(engine).get_table_names() if table != 'alembic_version'}

def snapshot():
    result = {}
    with engine.connect() as conn:
        for table, names in columns.items():
            fields = ', '.join('"' + name.replace('"', '""') + '"' for name in names)
            quoted = '"' + table.replace('"', '""') + '"'
            rows = conn.execute(text('SELECT ' + fields + ' FROM ' + quoted))
            result[table] = Counter(hashlib.sha256(json.dumps(list(row), default=str,
                ensure_ascii=False).encode()).hexdigest() for row in rows)
    return result

before = snapshot()
migration_module.migrate(engine)
after = snapshot()
for table, original in before.items():
    if table == 'game_config_versions':
        assert not (original - after[table]), 'Existing configuration lost'
    else:
        assert original == after[table], 'Existing data changed: ' + table
migration_module.migrate(engine)
assert after == snapshot(), 'Repeated migration changed data'
with engine.connect() as conn:
    assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == expected_head
print('PASS: migration, repeat migration, existing-data preservation; head=' + expected_head)
PY
sudo -u dzmmbot sh -c 'cd "$1" && "$2" deploy_migration_check.py "$3"' sh "$STAGE" "$TEST_PY" "$CHECK_DB"
```

以上成功代表迁移和备份恢复演练通过。还需要按本轮迁移内容检查新增字段默认值、约束和资产守恒；遇到任何不一致保持旧服务运行并调查。

## 9. 正式更新

### 9.1 发布前门槛

- 已确认用户授权、目标服务器、完整 commit、包哈希及预期 Alembic head。
- 候选代码测试和必要的迁移演练通过。
- 已审阅包内文件及删除/重命名清单：覆盖解压不会自动移除旧文件；有删除或重命名必须制定精确处理清单，不能留旧模块掩盖遗漏，也不能清空整个 app。
- 已记录旧版状态，确认磁盘和回退路径。
- 不在本轮顺带启用天气、后续阶段或改变目标群。

维护期间暂不能处理新指令，不保证自动补拉停机期间消息。需要通知时由用户或已明确获准的渠道发送。

### 9.2 停服并保存最终备份

```bash
sudo systemctl stop dzmm-worker
sudo systemctl stop dzmm-core
test "$(systemctl show dzmm-worker -p ActiveState --value)" = inactive
test "$(systemctl show dzmm-core -p ActiveState --value)" = inactive
sudo test ! -e "$BACKUP/app-before.tar.gz"
sudo test ! -e "$BACKUP/production-before.dump"
sudo tar -czf "$BACKUP/app-before.tar.gz" -C /srv/dzmm app
sudo chmod 600 "$BACKUP/app-before.tar.gz"
sudo -u postgres pg_dump -Fc -f "$BACKUP/production-before.dump" dzmm
sudo chmod 600 "$BACKUP/production-before.dump"
sudo -u postgres pg_restore --list "$BACKUP/production-before.dump" >/dev/null
sudo tar -tzf "$BACKUP/app-before.tar.gz" >/dev/null
```

最终备份必须在停服后完成，不能只依赖第 8 节演练前的旧快照。应用备份包含旧 Linux 虚拟环境；若以后 `.venv` 改成外部符号链接，要另外保存链接目标。检查归档目录可读不等于完整恢复演练；本次数据库恢复能力由第 8 节验证，重要依赖变化还需检查旧运行环境可恢复。

### 9.3 解压、依赖和迁移

```bash
sudo tar --no-same-owner -xzf "$PACKAGE" -C /srv/dzmm/app
sudo sed -i 's/\r$//' /srv/dzmm/app/deploy/manage.sh
sudo -u dzmmbot sh -n /srv/dzmm/app/deploy/manage.sh
```

必须使用 sudo 更新 root 管理的正式代码；不要改成服务账号拥有整个应用目录来绕过权限。Windows CRLF 会使 `sh` 报 `set: Illegal option`，因此对管理脚本执行 LF 规范化。

仅当依赖锁变化时执行：

```bash
sudo /srv/dzmm/app/.venv/bin/python -m pip install -r /srv/dzmm/app/requirements.lock.txt
sudo /srv/dzmm/app/.venv/bin/python -m pip check
```

正式虚拟环境已在备份中。依赖安装失败保持服务停止，按第 13 节恢复，不忽略错误继续启动。

使用正式目录的模块执行已演练迁移，并在停服窗口对账。检查器从 stdin 执行，确保导入正式代码而非候选目录：

```bash
sudo -u dzmmbot sh -c 'cd /srv/dzmm/app && .venv/bin/python - dzmm < "$1/deploy_migration_check.py"' sh "$STAGE"
```

若本轮改用专用数据转换检查器，候选和正式阶段使用同一份已审阅脚本。迁移后对账失败时不得自动恢复消息处理；先定位和制定恢复方案。

只有审阅确认 systemd 单元也需要更新时，备份实际单元及 drop-in，合并必要差异，再执行 `systemctl daemon-reload`。常规应用更新不复制仓库单元覆盖生产配置。

```bash
printf '%s\n' "$COMMIT" | sudo tee /srv/dzmm/app/DEPLOYED_COMMIT >/dev/null
sudo systemctl start dzmm-core
curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 1 http://127.0.0.1:18120/healthz
sudo systemctl start dzmm-worker
```

## 10. 发布验收（每次更新必做）

完成代码和迁移检查后必须执行第 9 节启动步骤，再进行本节核验。不能只在报告中建议用户自行启动。若只是恢复运行，`systemctl start` 可安全确保服务已启动；对已运行但连接异常的 Worker，查明原因后可单独 restart，避免同时运行第二个手工 Worker。

```bash
systemctl is-active dzmm-core dzmm-worker postgresql
systemctl show dzmm-core dzmm-worker -p Id -p ActiveState -p SubState -p NRestarts
curl -fsS http://127.0.0.1:18120/healthz
sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status
sudo -u postgres psql -d dzmm -Atc 'SELECT version_num FROM alembic_version'
sudo cat /srv/dzmm/app/DEPLOYED_COMMIT
```

应观察到：

- 三个服务正常；Core 返回 `ok: true`，当前生产模式为 `live`。
- Worker 状态为 `connected` 且 `worker_stale: false`；刚启动可能需要等待一个心跳周期后再查。
- Core/Worker 不持续重启；对比预检基线，不能仅靠单次 `active` 判断稳定。
- 将 `balance_difference`、`pool_tax_difference`、`pool_ledger_difference` 和 `account_mismatches` 与停服前基线逐项比较；任何新增差异都阻止完成。已记录的历史差异可保留，但必须与基线完全一致并在发布记录中说明。
- Alembic head 和 `DEPLOYED_COMMIT` 等于本轮目标；旧玩家、库存、牧场、账本通过迁移校验。
- 对比发布前队列状态。历史 `failed` 不等于本次失败；新增 `uncertain` 需要人工查证，不能自动重发。

Worker 启动后每隔数秒检查一次，最多观察 60 秒；仍未 connected 或心跳过期则转入第 12 节诊断，不能无限循环等待或把 `active` 当作通过。重新启动后至少观察一次新的心跳；报告区分“服务运行”“连接正常”和“真实回复验证”。

如果状态为 connected 但失败回复数增加，查看失败分类和本轮日志；`rejected` 表示平台明确拒绝发送，不能归因于服务未启动，也不能假设重启已解决。当前错误记录没有保存完整平台拒绝原因时，只报告已知分类，后续补充脱敏诊断或经授权测试确认，不编造平台协议字段。

**完成门槛：** Core/Worker 均 active、Core 健康、Worker connected 且心跳未过期，并明确新增失败与未完成验收。启动失败时继续修复或按第 13 节恢复兼容旧版；迁移、数据或安全问题尚未解决而必须停服时，明确说明 Bot 当前未恢复，不能报告更新完成。

`world_state.config_version` 仍为 `m1-v1` 不代表 P2 未发布：当前实现保留旧世界配置，牧场新规则通过独立 `ruihe-ranch-v1` 快照使用。不要为了状态显示而手改世界版本。

原生 Reply 是否在平台 UI 正确展示需要授权测试群验收，健康检查和模拟测试不能代替。未执行就明确记录“未执行”。

## 11. 发布后收尾

确认第 10 节通过后，清理本轮新建的独立演练库：

```bash
[[ "$CHECK_DB" =~ ^dzmm_check_[a-f0-9]{12}_[0-9]{8}_[0-9]{6}$ ]]
sudo -u postgres dropdb "$CHECK_DB"
```

保留代码包、候选目录和最终备份，直到通过维护策略确认可以清理。删除之前检查绝对路径、所属发布及是否仍被当前环境使用。不要清空 `/var/backups/dzmm`、`/var/lib/dzmm` 或 `/srv/dzmm/app`。

生产数据副本、配置和登录状态均不下载到公开位置，不提交 Git。应用备份和数据库备份放在受限目录；异机加密备份与定期恢复演练属于后续运维安排，本文不表示已经自动配置了这些任务。

记录本轮版本、迁移、测试、备份路径和未完成验收。更新 `docs/ruihe-progress.md` 的开发状态；部署事实写入本文第 14 节，避免只写在被忽略的 `data/` 中。

## 12. 日常维护和故障处理

| 现象或需求 | 检查和处理 |
| --- | --- |
| SSH `publickey` 拒绝 | 核对 `ubuntu`、`-i bot.pem` 和 ACL；修复后只做一次有依据的重试 |
| 本地沙盒拒绝读取密钥或访问网络 | 通过工具申请所需范围的沙盒外执行，不改密钥为公开可读 |
| 解压提示 `File exists` / `Permission denied` | 正式代码为 root 管理，使用 sudo；`/home/ubuntu` 包先 install 到发布目录 |
| `set: Illegal option` | 检查管理脚本 CRLF，按第 9 节转换 LF |
| Core 启动失败 | 查看 Core 日志，核对依赖、迁移版本、配置权限；不要重置数据库 |
| Worker disconnected | Core 健康后检查 Worker 日志、网络与登录状态；只启动一个 Worker |
| Worker active 但心跳过期 | 检查重启计数、日志与 Core 连通性，不以进程存在代替业务健康 |
| `uncertain` 出站任务 | 先确认平台是否已收到；无证据不得再次发送或随意标为失败 |
| 磁盘紧张 | 查看目录用量，按明确保留策略清理历史发布；不得删当前 Profile 或数据库文件 |
| 依赖无法安装 | 候选阶段解决锁定版本和 Linux 兼容性；不在生产临时换版本凑启动 |

常用只读命令：

```bash
sudo systemctl status dzmm-core dzmm-worker --no-pager
sudo journalctl -u dzmm-core -n 80 --no-pager
sudo journalctl -u dzmm-worker -n 80 --no-pager
free -h
df -h
```

日志可能含消息、用户或连接信息，返回报告前脱敏；不要打印 `/etc/dzmm/dzmm.env`、私钥或 `.auth-state.json`。

### 12.1 登录失效后的人工恢复

用户规则：以后更新后仅因登录/身份认证失败，保留新代码及发布标记，不回退代码；恢复服务器浏览器登录。Core保持正常，登录期间停止Worker以释放Profile。Worker重新连接且心跳正常前仍属于未完成验收。

先停止 Worker，再打开现有临时登录服务；由用户完成登录和验证码：

```bash
sudo systemctl stop dzmm-worker
sudo systemctl start dzmm-login-web
sudo -u dzmmbot env DISPLAY=:99 sh /srv/dzmm/app/deploy/manage.sh login
```

本地另开 PowerShell；先设置第 5 节的 `$DeployKey`、`$DeployTarget`：

```powershell
ssh -i $DeployKey -o IdentitiesOnly=yes -N -L 127.0.0.1:6080:127.0.0.1:6080 $DeployTarget
```

访问 `http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale`，登录并进入目标群；回到登录命令终端按 Enter 保存。然后执行：

```bash
sudo systemctl stop dzmm-login-web dzmm-login-vnc dzmm-display
sudo systemctl start dzmm-worker
sudo -u dzmmbot sh /srv/dzmm/app/deploy/manage.sh status
```

关闭本地隧道。不要向公网开放 5901、6080、18120 或 PostgreSQL 端口，不删除 Profile 来“重新开始”。

## 13. 回退与数据库恢复

### 13.1 判定回退类型

| 情况 | 处理 |
| --- | --- |
| 尚未停服，候选验证失败 | 保留旧服务，修复候选，不需要回退正式库 |
| 已停服但尚未迁移，解压/依赖失败 | 恢复旧代码和虚拟环境，再启动验收 |
| 新迁移已提交，随后启动或验收失败 | 保持停服，判断旧程序是否支持新 schema/head；不直接降级代码 |
| 新版已处理玩家操作 | 恢复旧数据库会丢失后续操作，优先向前修复；需明确数据恢复授权 |

当前迁移没有安全的自动 downgrade：`0003`、`0004` 的 downgrade 明确要求恢复已验证备份。旧程序可能不认识新 Alembic head，即使只新增字段也不能据此假设可直接回退。

### 13.2 恢复旧代码

确认本轮 `$BACKUP` 对应正确的旧版本，服务已停止，并确认数据库兼容后执行。变量仍使用第 7 节本轮值：

```bash
sudo systemctl stop dzmm-worker
sudo systemctl stop dzmm-core
sudo test -s "$BACKUP/app-before.tar.gz"
export FAILED_APP="/srv/dzmm/app-failed-$RELEASE"
test ! -e "$FAILED_APP"
test "$(readlink -f /srv/dzmm/app)" = /srv/dzmm/app
sudo mv /srv/dzmm/app "$FAILED_APP"
sudo tar -xzf "$BACKUP/app-before.tar.gz" -C /srv/dzmm
```

移动旧故障目录保留现场，恢复到原绝对路径以保持虚拟环境可用。不能只覆盖解压备份，因为新版本新增文件会残留。如果修改过 systemd 单元，使用本轮保存的实际单元备份恢复并 daemon-reload。

### 13.3 恢复数据库：必须先确认时间点和影响

仅在明确选择恢复 `$BACKUP/production-before.dump` 且确认后续玩家操作的处置方案后执行。正式服务保持停止；先给当前故障数据库另做快照，避免丢失调查和补偿依据。

```bash
export RESTORE_DB="dzmm_restore_${RELEASE//-/_}"
export FAILED_DB="dzmm_failed_${RELEASE//-/_}"
sudo test ! -e "$BACKUP/before-recovery.dump"
sudo -u postgres pg_dump -Fc -f "$BACKUP/before-recovery.dump" dzmm
sudo chmod 600 "$BACKUP/before-recovery.dump"
sudo -u postgres createdb --owner=dzmmbot "$RESTORE_DB"
sudo -u postgres pg_restore --exit-on-error --dbname="$RESTORE_DB" "$BACKUP/production-before.dump"
sudo -u postgres psql -d "$RESTORE_DB" -Atc 'SELECT version_num FROM alembic_version'
```

核对恢复库版本、玩家/库存/账本与备份时预期一致。不要启动新版 Store 去检查旧库，它会触发迁移。确认没有应用或其他管理连接占用待切换数据库：

```bash
sudo -u postgres psql -d postgres -c "SELECT datname, count(*) FROM pg_stat_activity WHERE datname IN ('dzmm', '$RESTORE_DB') GROUP BY datname"
```

有连接时定位并正常关闭；不盲目终止未知客户端。没有连接后，通过改名保留原库而非删除：

```bash
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c "ALTER DATABASE dzmm RENAME TO \"$FAILED_DB\""
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres -c "ALTER DATABASE \"$RESTORE_DB\" RENAME TO dzmm"
```

如果第二次改名失败，保持服务停止并将 `$FAILED_DB` 改回 `dzmm`，不要在数据库名称缺失时启动。确认已恢复匹配的旧代码后，按第 9 节顺序启动 Core/Worker，再执行第 10 节验收。保留故障库和备份，后续单独核对及处理恢复点之后的玩家操作；不要重放已经发送的平台消息。

## 14. 发布记录与交接模板

### 上一版已提交发布（历史）

| 项目 | 结果 |
| --- | --- |
| 日期 | 2026-09-26 |
| 提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` |
| 范围 | P0/P1/P2 已实现代码；天气保持未启用 |
| 发布包 | `data/deploy-5a4e915.tar.gz` |
| SHA256 | `5e1f2d12ac5651b558ce01210fefc3c3a32fbfe5e4ecaa5c9269087958993fc9` |
| 服务器候选目录 | `/srv/dzmm/releases/5a4e915` |
| 备份目录 | `/var/backups/dzmm/5a4e915` |
| 备份文件 | `app-before.tar.gz`、`production-before.dump` |
| 迁移 | `0002_ranch` → `0003_reply_context` → `0004_ruihe_ranch` |
| 本地 / 服务器测试 | 各 68 项通过 |
| 隔离 Core/Worker 冒烟 | 服务器通过 |
| PostgreSQL 演练与正式迁移 | 重复迁移通过，所有旧表原有数据保持一致 |
| 上线状态 | Core 健康，Worker connected，心跳未过期，重启计数 0，账本差额 0 |
| 外部验收 | 未向正式群发测试消息；原生 Reply 真实 UI 仍未验收 |
| 已解决故障 | 私钥 ACL、服务账号无法读取 ubuntu home 中包、root 代码写入权限、脚本 CRLF |

上次发布使用短 commit 目录；今后按第 6 节生成“12 位 commit + 时间”标识，避免重跑覆盖备份。上述记录是历史实测结果，不能代替下一次版本验收。

### 2026-09-26 P3–P5 工作区发布（当前）

| 项目 | 结果 |
| --- | --- |
| 基准提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e`；包含 P3–P5 经测试的工作区覆盖，未创建 Git 提交或推送 |
| 发布 ID / 包 | `628292f6414d-20260926-195010` / `data/deploy-628292f6414d-20260926-195010.tar.gz` |
| SHA256 | `628292f6414d93eb83b83743499ad85940c8437c5261120a300e2b35f0f6c48c` |
| 服务器目录 | `/srv/dzmm/releases/628292f6414d-20260926-195010`；正式目录 `/srv/dzmm/app` |
| 备份 | `/var/backups/dzmm/628292f6414d-20260926-195010/app-before.tar.gz`、`production-before.dump`；迁移演练使用 `rehearsal.dump` |
| 迁移 | `0004_ruihe_ranch` → `0007_buffs_factory_advanced`；独立 PostgreSQL 副本演练与正式库迁移/重复迁移均通过，旧数据保留，P3 货币值按确认规则换算为分 |
| 测试 | 本地和服务器 `unittest discover -s . -p test_*.py` 各 103 项通过；服务器 Core/Worker 隔离冒烟通过；P5 定点 28 项通过 |
| 运行状态 | Core、Worker、PostgreSQL active；Core `ok: true`；Worker connected、心跳未过期；重启计数 0；账本/奖池差额 0、账号不匹配为空 |
| 外部验收 | 未向正式群发送测试消息；无真实玩家指令验收 |
| 清理 | 本次创建的隔离 PostgreSQL 检查库和一次性迁移校验脚本已移除；发布包、候选目录、最终备份保留 |

该发布包含工作区覆盖，`DEPLOYED_COMMIT` 记录基准提交，`DEPLOYED_PACKAGE_SHA256` 与 `DEPLOYED_RELEASE_ID` 精确标识实际内容。工作区改动仍未提交/推送；后续发布前先决定如何纳入 Git 历史，不能把基准提交误报为完整发布源码。

### 2026-09-26 消息分段热修复（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 范围 | `dzmm_bot/store.py`：回复超过 10 个换行时按行分段，顺序入队并为每段保留原消息 Reply 上下文 |
| 本地文件 SHA256 | `f2cd9f39f7c80c0fc70b24dfceabc36c951a26e0d6bba41d94119a1ff1bf1f61` |
| 相关测试 | `python -m unittest test_core -q`，19 项通过；`git diff --check` 通过 |
| 服务器更新 | 单文件安装到 `/srv/dzmm/app/dzmm_bot/store.py`；首次健康探测过早触发自动回滚，第二次使用重试健康探测后成功 |
| 备份 | `/var/backups/dzmm/sendfix-20260926/production.dump`、`store.py.before` |
| 新回复验证 | 一条原失败牧场面板拆成两个新 Reply outbox，由正常 Worker 发送；两条 sent，failed 未增加，uncertain 为 0 |
| 服务状态 | Core/Worker/PostgreSQL active；Core 健康，Worker connected、心跳未过期，重启计数 0 |
| Git 状态 | 修复仍是工作区改动，没有创建提交；正式版本之后需要纳入发布提交，不能误报原部署 commit 已包含该修复 |

### 2026-09-26 出站失败诊断部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 范围 | Socket / Bot API 发送失败的脱敏结构化原因、HTTP/ACK 分类、字符与换行数量、失败尝试历史；管理状态仅返回最近失败记录；不重放旧失败消息 |
| 基准提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e`；发布内容由包 SHA 锁定，未提交或推送 |
| 发布 ID / 包 | `9de6855e3153-20260926-205530` / `data/working-tree-20260926-205530.tar.gz` |
| SHA256 | `9de6855e3153188e3eb685ffe2ad670f6fc9556f8d84171e3928d66d3a99c94a` |
| 迁移 | `0007_buffs_factory_advanced` → `0008_outbound_failure_details`；仅新增 nullable `outbound_messages.error_detail`；老记录保持 NULL |
| 测试 | 本地与服务器显式运行 `test_core test_game test_bot tests.test_ruihe_p5 tests.test_ruihe_factory`，各 132 项通过；本地与服务器 Core/Worker 隔离冒烟通过；语法与 `git diff --check` 通过 |
| 迁移演练 | 正式库 `rehearsal.dump` 恢复到隔离库；迁移及重复迁移通过；全部表行数、账户余额合计、库存合计及货币/库存账本行数一致 |
| 生产备份 | `/var/backups/dzmm/9de6855e3153-20260926-205530/app-before.tar.gz`、`production-before.dump`；备份列表/归档可读检查通过 |
| 运行状态 | Core、Worker、PostgreSQL active；Core live 健康；Worker connected、心跳未过期；重启计数 0；账本/奖池差额 0；Alembic head `0008_outbound_failure_details` |
| 既有失败任务 | 10 条历史 rejected 记录未重发，新增 `error_detail` 为空；新诊断仅作用于此后产生的失败 |
| 外部验收 | 未向正式群发送测试消息；真实 Reply UI 未验证 |
| 保留物 | 最终候选目录、包和备份均保留；此前两次因测试发现问题而废弃的候选目录/包未覆盖 |

### 2026-09-26 回复时间戳格式部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 范围 | 所有 Socket 回复、拆分后的每段回复及 Bot API 消息尾部使用北京时间格式 `---HH:mm:ss---`；无额外换行 |
| 发布 ID / 包 | `053be64648a3-20260926-211513` / `data/working-tree-20260926-211513.tar.gz` |
| SHA256 | `053be64648a3302743443387ac53d6fb3bf172ce09266517a4b03856e3e6c15f` |
| 迁移 | None；生产 Alembic head 保持 `0008_outbound_failure_details` |
| 测试 | 本地与服务器显式模块测试各 133 项通过；两端 Core/Worker 隔离冒烟通过；编译与 `git diff --check` 通过 |
| 备份 | `/var/backups/dzmm/053be64648a3-20260926-211513/app-before.tar.gz`、`production-before.dump`；备份归档检查通过 |
| 上线状态 | Core、Worker、PostgreSQL active；Core live 健康；Worker connected 且心跳新鲜；重启计数 0；账本/奖池差额 0；无新增失败或 uncertain |
| 外部验收 | 未向正式群发送测试消息 |

### 2026-09-26 长回复重复拒绝处理（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 现象 | 3 条新长回复（13–14 个换行）被平台以“请勿发送重复内容”明确拒绝；Socket 连接正常，短错误回复已成功发送 |
| 修复 | 长回复收到重复内容拒绝时按行拆分并发送，每段保留原生 Reply 和 `---HH:mm:ss---`；短消息重复拒绝仍不重试 |
| 发布 ID / 包 | `1969b7f93b27-20260926-212716` / `data/working-tree-20260926-212716.tar.gz` |
| SHA256 | `1969b7f93b27c61c5fc730ecf2dad9854067771b3b0181d17e5ea4c656464b03` |
| 迁移 | None；数据库 head 保持 `0008_outbound_failure_details` |
| 测试 | 本地及服务器各 134 项测试通过；隔离 Core/Worker 冒烟通过；编译和 `git diff --check` 通过 |
| 备份 | `/var/backups/dzmm/1969b7f93b27-20260926-212716/app-before.tar.gz`、`production-before.dump`，归档检查通过 |
| 上线状态 | Core/Worker/PostgreSQL active；Core healthy；Worker connected 且心跳新鲜；对账差额 0；发布前后失败数保持 15、uncertain 为 0 |
| 外部验收 | 未向正式群发送测试消息；需观察后续真实长回复是否成功分段 |

### 2026-09-26 牧场面板紧凑格式部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 范围 | 按成功面板样例压缩动物汇总行；隐藏数量为 0 的库存及饲料行；保留产量、进度、饲料、行情和指令 |
| 基准提交 / 发布 ID | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` / `5a4e915be7e6-20260926-223000` |
| 包 SHA256 | `dbc7e05dc7ec3848cef58dd0b57717a995080dbe66d77c720cef351453404aae` |
| 候选验证 | 本地全套 106 项通过；服务器候选全套 106 项通过；隔离 Core/Worker 冒烟通过 |
| 迁移 | None；生产 head 保持 `0008_outbound_failure_details` |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260926-223000/app-before.tar.gz`、`production-before.dump`，归档可读检查通过 |
| 上线状态 | Core/Worker/PostgreSQL active；Core live 健康；Worker connected、心跳新鲜；重启计数 0；账本/奖池差额 0；failed 保持 15、uncertain 为 0 |
| 外部验收 | 未发送正式群测试消息；原生 Reply UI 未验证 |
| 保留物 | 发布包、候选目录和备份均保留；工作区仍未提交/推送 |

### 2026-09-27 P5.5 / P6A 信息发布与玩家引导部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 基准提交 / 发布 ID | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` / `5a4e915be7e6-20260926-232929` |
| 包 SHA256 | `9e1881c63e0d068bcad8c98df9bb22775fee99533bc91b2fdbc6d74e81e50005` |
| 迁移 | `0008_outbound_failure_details` → `0009_player_information`；恢复生产库副本后迁移、重复迁移和旧表数据摘要一致性均通过；正式迁移后 head 为 `0009_player_information` |
| 测试 | 服务器候选 `unittest discover -s . -p 'test_*.py'`：106 项通过；隔离 Core/Worker 冒烟通过。信息系统定点 22 项和联合回归 153 项此前在本地通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260926-232929`：`rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`；数据库与应用归档读取校验通过 |
| 上线状态 | Core/Worker/PostgreSQL active；Core live 健康；Worker connected、心跳新鲜；Core/Worker 重启计数 0；余额/奖池对账差额 0；uncertain 0；历史 failed 保持 15 |
| 外部公告 | 用户确认预览后，v0.5.5 文案经现有广播 outbox/Worker 发送到唯一启用群；任务 `f699c060-f616-46d5-909e-273b3d9ea080`，目标 1、成功 1、失败 0，投递一次，平台返回消息 ID。普通群公告未写入版本更新记录；`DZMM_GAME_ADMINS` 未配置 |
| 保留物 | 包、候选目录、迁移演练库和最终备份按发布流程保留；未提交或推送 Git |

### 2026-09-27 私聊邀请审批部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 发布 ID / 包 SHA256 | `5a4e915be7e6-20260927-113357` / `cbfed6195c6500e8119c05b73bc263e54656cd4e88739a48995e09a0f6112430` |
| 基准提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` 加经验证工作区内容；未提交、未推送 Git |
| 迁移 | `0009_player_information` → `0010_group_invites`；新增邀请表、outbox 可空卡片引用字段；数据库副本演练、重复迁移与 25 张旧表原字段数据摘要一致，正式库重复迁移和旧表摘要一致 |
| 测试 | 本地和服务器候选全套各 106 项通过；两端隔离 Core/Worker 冒烟通过；`git diff --check` 通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-113357`：`rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`，归档可读校验通过 |
| 真实邀请 | 从 Bot 既有一对一私聊确认 `share/group_invite` 卡片 `C66OxXko`，登记后等待同意的私聊回复 `sent`；用户授权批准后，平台返回群 `ad1ec2d6-70b7-4b15-b821-d6a38a6a70e6`，已启用；入群成功的私聊回复 `sent`。两条均引用原卡片并有平台消息 ID；未另外向群发测试消息 |
| 上线状态 | Core/Worker/PostgreSQL active，Core live 健康，Worker connected 且心跳新鲜，服务重启计数 0；余额/奖池对账差额 0；uncertain 0，历史 failed 保持 15 |
| 留待观察 | 新到邀请卡片的 Socket 实时事件、私聊 UI 引用呈现；首个未修正候选包 `5a4e915be7e6-20260927-112601` 只用于隔离检查，未安装到正式目录 |

### 2026-09-27 回复性能观测 5.5A 部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 发布 ID / 包 SHA256 | `5a4e915be7e6-20260927-122425` / `2af0f75af2e828da18545eae85739069ae4d05f6966794da1d78a261de01fc74` |
| 基准提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` 加已审阅工作区覆盖；未提交、未推送 Git。包内 107 个代码/测试/文档文件，无密钥、数据库或浏览器资料 |
| 迁移 | `0010_group_invites` → `0011_outbound_timing`；只加三个可空时间字段和三个查询索引；旧 outbox 时间字段保持 null |
| 候选与演练 | 服务器候选显式全套 171 项通过，隔离 Core/Worker 冒烟通过。PostgreSQL 正式库副本迁移、重复迁移、所有旧表原字段摘要及旧 outbox null 校验通过；正式迁移复用同一检查器并通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-122425`：`rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`；归档可读校验通过。隔离演练库已删除；包、候选目录与备份保留 |
| 上线状态 | Core/Worker/PostgreSQL active，Core live 健康，Worker connected、心跳新鲜；Core/Worker 重启计数 0；余额/奖池对账差额 0；outbox sent 91、历史 failed 15、uncertain 0，较发布前无新增失败 |
| 性能接口 | `/admin/performance?sample_limit=200` 已验证；首次查询 `recent_results=0`、pending 0、限流 0、失败 15、不确定 0，各耗时 P50/P95 为 null。生产新样本尚待自然消息，不把本地模拟值当作实际速度 |
| 外部验收 | 未发送正式群测试消息；平台原生 Reply UI 未在本轮重新验收 |

### 2026-09-27 回复效率 5.5B 安全默认值部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 发布 ID / 包 SHA256 | `5a4e915be7e6-20260927-130353` / `1c2ad5bc78c4473f5cbc85795afac65b025f5856db145629391745c6ec26a077` |
| 基准提交 | `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` 加已审阅工作区覆盖；未提交、未推送 Git。包内 110 个代码/测试/文档文件，无密钥、数据库或浏览器资料 |
| 候选与迁移 | 候选与正式代码差异仅 `core.py`、`gateway.py`、`settings.py`、`worker.py` 和新增 `socket_limiter.py`；依赖锁未变，Alembic head 仍为 `0011_outbound_timing`，无迁移。服务器候选显式全套 184 项通过，隔离 Core/Worker 冒烟通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-130353`：`app-before.tar.gz`、`production-before.dump`，归档可读检查通过；代码包和候选目录保留 |
| 上线状态 | Core/Worker/PostgreSQL active，Core live 健康，Worker connected 且心跳新鲜；重启计数 0，资产对账差额 0；历史 failed 15、uncertain 0，无新增失败 |
| 实际限速 | 正式环境无 `DZMM_SOCKET_*` 覆盖，`INITIAL=2`、`MIN=2`、`PRIVATE=5` 秒；Worker 心跳当前 2 秒、限流 0、调整 0。`/admin/performance` 已验证可用 |
| 首次性能读数 | 最近自然结果 10 条：队列等待 P50/P95 1.065/1.996 秒，发送尝试 0.195/4.527 秒；pending 0、当前限流 0。样本可能来自切换前，未据此宣称提速 |
| 外部验收 | 未配置 1.5 秒/1 秒，未发送正式群合成测试消息；平台原生 Reply UI 未在本轮重新验收 |

### 2026-09-27 回复效率 5.5C 部署（工作区文件，未提交）

| 项目 | 结果 |
| --- | --- |
| 发布 ID / 包 SHA256 | `5a4e915be7e6-20260927-164155` / `b8add8220098e763e7a2910d48ddbd3008dc3ad171c1708d1d60c4cde6004e5f`；基准提交 `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` 加已审阅工作区内容，未提交或推送 Git |
| 范围 | 队首优先与跨房间轮转、公告防饥饿、领取事务、有限在途配置；真实 Browser Gateway 仍保持单 Socket 发送者。与上一正式包相比新增 4 个文件、无删除；本包 114 个文件，无密钥、数据库或浏览器 Profile |
| 迁移 | `0011_outbound_timing` → `0012_outbound_scheduler`；生产库副本和正式库均通过迁移、重复迁移、全部旧表原字段摘要、房间新字段默认值及索引校验。独立 PostgreSQL 副本上的并发领取、同房间顺序和结果转移通过 |
| 测试 | 服务器候选根目录 108 项、`tests/` 85 项通过；隔离 Core/Worker 冒烟通过。本地同组测试与冒烟此前通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-164155`：`rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`，归档读取校验通过；候选目录和发布包保留 |
| 上线状态 | Core/Worker/PostgreSQL active，Core live 健康，Worker connected 且心跳新鲜；重启计数 0；余额、奖池对账差额 0；outbox sent 102、历史 failed 15、uncertain 0，无新增失败 |
| 性能与外部验收 | Socket 间隔仍为 2 秒，限流事件 0；固定时钟假负载见 `reply-performance-5.5C-operations.md`。未发送真实平台测试消息，实际 Reply UI、平台 ACK 与生产回复 P95 尚未重新验收 |

### 2026-09-27 文案修复、回复时间标记移除与 P1–P5 档案部署

| 项目 | 结果 |
| --- | --- |
| 发布 ID / 包 SHA256 | `5a4e915be7e6-20260927-182928` / `ae5eaa757e9860daf0fb4b7f139c9d389487820ea5fd901f33bcc51750b59ca0`；基准提交 `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e` 加审阅过的工作区差异，未提交或推送 Git |
| 变更范围 | 与上一发布包逐文件比较：新增 `docs/history/` 六份档案、修改 15 个文件、无删除；玩家可见改动为牧场加工品库存、行情基准/涨跌幅、出售数量必填、加工厂配方移至 `/配方`、帮助重排、回复末尾时间标记移除。另含相关测试及文档 |
| 迁移与依赖 | 正式库和候选 Alembic head 均为 `0012_outbound_scheduler`；依赖锁一致，无新迁移 |
| 测试 | 本地与服务器候选各 109 项通过；候选独立 Core/Worker 冒烟通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-182928` 包含发布前应用及 PostgreSQL 备份，脚本已验证归档可读取；候选目录和发布包保留 |
| 上线状态 | 脚本打印新版发布标记及健康检查后，在末尾 `trap - EXIT` 报 `invalid signal specification`，故脚本退出码为 1。另行只读复核确认正式发布 ID 和 SHA256 为上述新版，Core/Worker/PostgreSQL 均 active，Core `live` 健康，Worker connected 且心跳新鲜，余额/奖池对账差额 0，历史 failed 15。该脚本收尾错误待修复，不能将退出码 1 解释为成功验收 |
| 更新公告 | 使用现有 `AnnouncementService` / outbox / Worker 发送临时更新公告，任务 `c204ce9d-983a-4ae8-ba83-bb37261e0e3b`；启用群 2、成功 2、失败 0，两条 outbox 均 `sent` 且各有平台消息 ID。临时公告不创建版本更新记录，`/更新`、`/更新日志` 不会增加该条 |
| 剩余验收 | 平台 ACK 已确认公告发送；本轮未目视检查各客户端渲染。原生 Reply UI 仍按开放问题记录 |

**本次故障复盘（脚本修订仅在本地，尚未再次部署验证）：** 原脚本在沙盒中无法读取已有 `known_hosts`，导致本地测试和打包后才在 SCP 阶段停止；现将主机密钥及服务器发布哈希预检提前。原 `-AllowDirtyWorktree` 会把白名单目录内的全部未跟踪文件打包；现要求与正式包 SHA 匹配的基准归档、逐文件差异及复核短码。正式切换完成后末行 `trap - EXIT` 报错，与 PowerShell 管道附加 CRLF 的推断相符；现把管道末尾落在 Bash 注释，并在错误时输出只读服务器状态。停服开始前即标记恢复范围，避免只停了 Worker 时失去自动恢复路径。不要把这些本地脚本改动误认为已在上次发布中执行过。

### 2026-09-27 快速发布：P6/P7 与指令调整

| 项目 | 记录 |
| --- | --- |
| 发布 | `5a4e915be7e6-20260927-195313`，基准提交 `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e`，含未提交工作区改动；SHA256 `f424fafafeea5eee93e5eae8de26be5ebf5156fa57889108e75858e1bb577bb5` |
| 迁移 | `0012_outbound_scheduler` → `0015_horse_growth_ranks`；隔离正式库副本演练通过，旧表数据摘要不变，重复迁移无副作用；正式迁移同样通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-195313`：`rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`；归档可读取；候选目录与发布包保留 |
| 游戏测试 | 按用户本轮“不要测试”要求未运行本地、候选或正式游戏测试，也未发送群内验证消息；迁移副本演练与服务健康检查属于发布安全核验 |
| 运行状态 | Core、Worker、PostgreSQL 均 active，Core `/healthz` 返回 live/ok，Worker connected 且心跳新鲜，两个服务重启计数均为 0；余额和奖池账本差额均为 0 |
| 发送状态 | 检查时历史 failed 15、uncertain 0；没有真实新回复验收，不能据健康状态断言平台回复正常 |
| 说明 | 标准脚本会强制执行测试且在迁移变更时停止，因此本次按维护手册人工发布。PowerShell 管道向 SSH 传脚本时必须经 `tr -d '\r'` 清除 CR，避免变量末尾混入回车。Git 工作区改动未提交或推送。 |

### 2026-09-27 钱包签到、管理员审批与转账发布

| 项目 | 记录 |
| --- | --- |
| 发布 | `5a4e915be7e6-20260927-215931`；基准提交 `5a4e915be7e6cde1e3eb702aa0712fbaaa3fff1e`，实际内容由包 SHA256 `abc33fc8f9ea9bed52e82261875c9a68d7662db872dd21a338601bd191cf4899` 锁定；工作区改动未提交、未推送 |
| 内容 | 播种缺种自动补买、`/钱包`、`/签到`、私聊管理员登录与邀请审批、引用转账、管理员发放资产 |
| 迁移 | `0015_horse_growth_ranks` → `0016_game_admin_login`。隔离正式库副本及正式库迁移均通过旧表数据保留和重复迁移核验 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-215931`，含 `rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`、`dzmm.env-before`；候选目录及发布包保留 |
| 配置 | 仅将管理员登录密码的 PBKDF2 哈希写入 `/etc/dzmm/dzmm.env`，不存明文。哈希含 `$`，配置行必须单引号包裹，避免 `manage.sh` 的 Shell 变量展开；本次首次写入后发现并修正该问题，再依序启动 Core、Worker |
| 验收 | Core、Worker、PostgreSQL active，前两者重启计数 0；Core `/healthz` 返回 `ok/live`，Worker connected、心跳未过期。余额及奖池账本差额 0；历史 failed 15、uncertain 0。未运行游戏测试或真实命令 Reply UI 测试 |
| 公告 | 临时公告经现有 `AnnouncementService` / outbox / Worker 发送；任务 `b1d99624-8a54-43c2-a708-c255fe27e3e1`，启用群 2、成功 2、失败 0，均有平台消息 ID。临时公告不写入 `/更新` 版本历史 |
| 过程 | 发布前平台连接一度失败、网站 HTTPS 超时，随后自行恢复；公告在连接恢复后完成。PowerShell 经 SSH 传入脚本使用 `tr -d '\r'` 消除 CRLF |

后续每次发布追加以下信息，并同步第 2 节的当前版本：

### 2026-09-27 P7A/P7B 发布与公告

| 项目 | 记录 |
| --- | --- |
| 发布 | `5a4e915be7e6-20260927-235010`，包 SHA256 `e60897967a0c72bbe713beec17b1f0def50e8cec88edb5ef9dae6f62ea94e4b7`；本地工作区未提交、未推送 Git |
| 内容 | P7A 拍卖场；P7B 经济作物、加工品、行情与配方入口 |
| 迁移 | `0016_game_admin_login` → `0017_auctions`；正式库副本迁移演练与正式迁移通过 |
| 备份 | `/var/backups/dzmm/5a4e915be7e6-20260927-235010`，包含正式库副本、停服前应用和正式库备份 |
| 检查 | 本地编译与 `git diff --check`、候选代码导入、迁移演练、服务健康及资产账本核对通过；按本轮约束未运行游戏测试或真实玩家命令测试 |
| 运行 | Core、Worker、PostgreSQL active；Core live/ok，Worker connected 且心跳新鲜；重启计数 0，余额与奖池账本差额 0，历史 failed 15、uncertain 0 |
| 公告 | 临时公告任务 `e50ef486-82e4-4417-b52d-d594aabded3d`；启用群 2、成功 2、失败 0，均有平台消息 ID；不写入 `/更新` 版本历史 |


```text
Date / Operator:
Scope / Commit / Release ID:
Package SHA256:
Migration before -> after:
Backup directory / restore rehearsal:
Tests actually run / results:
Core health / Worker heartbeat / restarts:
Ledger and inventory preservation:
Live Reply UI: verified in authorized test room | not run
Remaining issues / rollback notes:
```

任务结束报告只需列改动、实测、备份、阻塞和下一步。没有执行的测试不得记为通过，没有真实发送验收不得声称平台 Reply 已确认。

## 15. 其他文档的职责

- `AGENTS.md`：要求更新维护任务读取本文的入口规则。
- `docs/ruihe-progress.md`：开发阶段、最近测试、开放问题与下一轮入口。
- `docs/codex-workflow.md`：开发与测试分层，迁移和原生 Reply 的验收要求。
- `docs/ruihe-codex-plan.md`、`docs/reference/ruihe.md`：计划与产品规则。
- `docs/server-guide.md`：历史首次部署参考；其中 Git 拉取等模板不代表当前生产流程。
- `data/server-deployment.md`：最初的本机操作记录，可能不受 Git 管理；后续共享维护事实以本文为入口。

服务器账号、路径、端口、部署方式或备份策略改变后，立即更新本文；不可让旧示例继续充当当前环境说明。

## 2026-09-28 P7.5 发布验证

- 用户授权快速部署并发送更新公告；使用追加迁移人工发布流程，未运行游戏测试。包新增7项、修改19项、删除0项，依赖锁未变化。SSH使用既有known_hosts严格校验。
- 发布：`5a4e915be7e6-20260928-120000`；SHA256：`1946663ccea1102f2f0d14a8ab21de10256bf921cc01d8f4c91a280efb68b980`；下次脏工作区发布基准：`data/deploy-p75-20260928.tar.gz`。
- 备份：`/var/backups/dzmm/5a4e915be7e6-20260928-120000/` 的 `app.tar.gz`、`production.dump`、`rehearsal.dump`。隔离库：`dzmm_check_5a4e915be7e6_20260928_120000`。
- 0018/0019/0020副本及正式迁移成功，旧表字段逐行摘要一致，重复迁移数据不变；迁移head为 `0020_p75_compact_replies`。
- Core与Worker active、Core live健康、Worker connected且心跳新鲜、服务重启0。余额与账本、奖池与税费差异0；outbox sent506、历史failed15、uncertain0，无新增失败。
- P7.5临时公告任务 `579efae7-9406-4b3d-874e-b42fc7d075ba` completed；两个有效群success且outbox sent，均保存平台消息ID。未伪造更新registry，未重复发送成功群。
- 未运行游戏测试或真实游戏指令验收；公告ACK不等于全部玩法通过。发布记录文档在打包后补写，下一包会包含本记录。

## 2026-09-28 P7.5 易用性更新发布验证

- 用户授权部署与P7.5公告。发布包：`data/deploy-p75-usability-20260928.tar.gz`，SHA256 `efa79cba758a32966d4c482995d17fa16f5075cd3767ce84d582ee0dcf662fef`，发布ID `5a4e915be7e6-20260928-180000`。下次发布以此包为基准。
- 对比上一正式包：新增1、修改14、删除0；依赖文件与历史迁移逐项一致，head仍为 `0020_p75_compact_replies`。候选路由导入成功，未运行游戏测试。
- 正式备份：`/var/backups/dzmm/5a4e915be7e6-20260928-180000/app.tar.gz`、`production.dump`。代码覆盖后Core先启动并健康，再启动Worker。
- 最终Core/Worker active、NRestarts0，Worker connected且心跳未过期；余额/账本、奖池/税费差异0。sent921、failed15（与发布前相同）、uncertain0，无待发送积压。
- 临时P7.5公告任务 `7b9cfdd4-371c-433d-9214-34b1ade9148a` completed，2/2 delivery success，outbox sent且有平台ID。公告经现有Worker链路，无玩家引用，按正文幂等防重复。
- 未创建新的更新registry版本，未运行功能测试或主动游戏群测试；文档记录在发布后补写。

## 2026-09-28 P8养马、繁育、每日赛马发布验证

- 发布ID：`5a4e915be7e6-20260928-230001`；SHA256：`6f19b13be133ee6dbf0f43a8f17fd37b817d10accb1605d4480f77718ec4e689`。下一次脏工作区基准包为 `data/deploy-p8-20260928.tar.gz`。工作区未提交或推送Git。
- 相对已验证P7.5基准新增65项、修改25项、删除0项；依赖锁与requirements一致。按维护手册追加迁移人工流程发布，没有绕过自动脚本迁移保护；SSH使用受信Windows会话已有known_hosts严格校验。
- 备份目录 `/var/backups/dzmm/5a4e915be7e6-20260928-230001/`：`rehearsal.dump`、`app-before.tar.gz`、`production-before.dump`。隔离库 `dzmm_check_5a4e915be7e6_20260928_230001`、候选目录和归档保留；真实数据库副本恢复成功。
- 0020→0024：副本与正式迁移均通过全部旧表原字段逐行摘要、重复迁移及旧马喂食累计回填检查。候选Core导入与RaceRuntimeFactory构建成功，本輪未运行游戏功能/经济测试；此前R2的113项结果不能代替最新改动测试。
- Core先启动并验证live/ok，再启动Worker。最终两服务active、NRestarts0，Worker connected且心跳新鲜；今天和明天自动日赛分别REGISTRATION/DRAFT。
- 20个账户、627条货币账本、余额合计883821.39不变；余额/账本和奖池/账本差额0。pool_tax_difference=88源自既有auction_fee 8800分入池，transaction_tax 358176分，合计奖池366976分；不能要求奖池与仅交易税额相同。
- 更新公告重点养马、繁育、赛马，复用AnnouncementService/outbox/Worker，无玩家引用。任务 `48e5ac83-f27f-4206-9463-a751e947a98c` completed；两个有效群均success、sent且平台ID存在。正文幂等检查未重新入队。
- 发布前failed17/sent1340，公告后failed17/sent1342、uncertain0，无新增失败；未执行真实玩家游戏指令Reply UI验收或完整日赛生产结算验收。
- 本记录在发布后补写，包内文档为发布前内容；后续包应包含本记录。

## 2026-09-28 P8回复入队故障与热修复

- 故障：上一版store.receive给每条回复生成41字符批次ID，但生产outbound_messages.id为VARCHAR(36)。Core回500并整体回滚玩家命令；Core健康、Worker connected以及公告ACK成功并不能证明玩家Reply正常。
- 修复：批次前缀96位随机+32位递增序号，编码为标准36字符UUID，保留同批分段顺序。仅业务store.py变化，另两份发布记录更新；无依赖/迁移变化，旧资产及失败计数保留。
- 热修复ID `5a4e915be7e6-20260928-230002`，SHA256 `bed68e61529321af3de0050eaa97dd69af5fc72de4315fd64aae787f9d51a39d`；下一包基准 `data/deploy-p8-hotfix-20260928.tar.gz`。
- 备份 `/var/backups/dzmm/5a4e915be7e6-20260928-230002/app-before.tar.gz`、`production-before.dump` 均归档可读；候选导入成功。停Worker→Core，更新后先Core健康再Worker，未运行自动功能测试。
- 实际恢复：新自然玩家命令产生2条sent，ID均36字符；用户确认收到回复。Core/Worker active、NRestarts0，Worker connected且心跳新鲜，head0024，failed17与基线相同，uncertain0。余额/账本和奖池/账本一致。
- 注意：超长ID报错的原命令事务整体回滚，需玩家重发；不得批量重放经济指令。平台原生Reply目视格式和全部长面板仍需后续验收。
- 后续入队改动验收应读取正式列宽，并在隔离PostgreSQL副本验证真实玩家命令→outbox入队；SQLite不强制VARCHAR长度，单纯SQLite与健康检查不足以发现此类错误。


## V0.8 发布与公告验证

- 发布ID `5a4e915be7e6-20260928-230003`；SHA256 `f4c1f741716e059a4764f8592cef7b7c3cd0c0bc4c81ae846f038b7dd3d0dd3d`；下一次脏工作区基准 `data/deploy-v08-20260928.tar.gz`。未提交、未推送Git。
- 与0024热修复正式包相比新增2、修改45、删除0；历史迁移和依赖锁一致。按新增迁移人工流程，未调用自动脚本或绕过其保护。
- 备份 `/var/backups/dzmm/5a4e915be7e6-20260928-230003/`，包含rehearsal.dump、app-before.tar.gz、production-before.dump；归档可读。隔离库 `dzmm_check_5a4e915be7e6_20260928_230003` 恢复成功。
- 0024→0025新增红包表，副本和正式迁移的全部旧表原字段摘要一致，重复迁移数据不变；候选Core及赛马运行时导入成功。未运行游戏功能、经济测试或主动游戏群测试。
- Core先健康后启动Worker，两服务active、NRestarts0；Core live/ok，Worker connected且心跳新鲜。21账户、647条ledger、余额总计871959币保留；余额/账本与奖池/账本差额0。pool_tax_difference88为历史拍卖费，非新差额。
- 发布前failed18/sent1442；最终failed18/sent1446、无uncertain，无新增失败。新增自然回复sent，但本轮未目视检查玩家Reply。
- V0.8临时公告任务 `1ccb1f3d-307c-491c-9545-03d25a533bbb` completed，2/2有效群success，outbox sent且均有平台ID。公告详细介绍玩家成长/培养/繁育/澄露杯/效率/收获/行情/红包，无管理员命令；临时公告不新增更新registry历史。
- 演练首次遇到ubuntu家目录包权限、候选目录权限及PowerShell引号错误，正式服务不受影响；将包安装到服务账号可读候选目录，并以sudo -u dzmmbot执行后完成。
- 发布记录在打包后补写，下一包包含本记录。


## 注册昵称诊断快速发布

发布 `5a4e915be7e6-20260928-230004`，包 `data/deploy-v08-nickname-diagnostics.tar.gz`，SHA256 `78d7d3eee559307c36f05a2982fbd659a8f81729982271db375da6f4ae6f43f4`，下次工作区发布使用此基准。相对V0.8仅browser.py/gateway.py与两份文档，新增删除0；依赖和迁移文件一致、head0025，无迁移。备份 `/var/backups/dzmm/5a4e915be7e6-20260928-230004/` 包含app-before.tar.gz和production-before.dump，归档可读。候选导入完成，未运行功能测试。先Core健康再启动Worker；仅诊断日志发布，不发送公告，注册故障尚需真实重试采样确认。

快速诊断发布未通过Worker连接验收：Core健康但Worker持续login_or_identity_required，failed18无新增、资产对账0。已恢复发布前browser.py/gateway.py与发布标记（230003），不恢复数据库、不覆盖浏览器登录资料；诊断候选保留。仍需确认旧版重连是否恢复，未发送公告。230004诊断包不可作为当前正式基准。

登录恢复完成：用户在服务器浏览器完成登录并进入群，保存会话后关闭login-web/login-vnc/display及本机隧道。当前仍为230003；Core live/ok，两服务active、NRestarts0，Worker connected且心跳新鲜，failed18、sent1473、无uncertain；资产对账0。自然自动赛事取消产生退款50币并新增一条ledger，属于正常结算。未发公告，注册昵称诊断补丁230004尚未重新上线。登录失败保留新版规则与脚本改动目前在本地，脚本未实跑验证。


## 统一拍卖入口发布完成

发布 `5a4e915be7e6-20260928-230005`，SHA256 `fd28df5150e981e60c1d7496e2ad36687f2bfa2e6cfabd13cfd4ff4e5d7d57dd`；下一次正式基准 `data/deploy-v08-auction.tar.gz`。相对230003修改11项，无新增删除，含统一拍卖入口、昵称诊断及登录失败不回退的脚本/文档。依赖和历史迁移一致，head0025无新迁移。备份 `/var/backups/dzmm/5a4e915be7e6-20260928-230005/` app-before.tar.gz和production-before.dump归档可读。候选Core/拍卖模块导入成功；未运行功能/经济测试或正式拍卖操作。先Core live/ok再Worker，最终两服务active重启0、Worker connected且心跳新鲜，资产对账0，failed18/sent1477与发布前一致、无uncertain。未发送公告，昵称故障仍待注册重试采样。


## 赛况播报与繁育修正快速发布

发布 `5a4e915be7e6-20260928-230006`，SHA256 `fdc575a785accf57f87b7ef5be2b8570f2b80f54b4010c46f1380f09f68b4702`，下一次基准 `data/deploy-v08-race-breeding.tar.gz`。相对230005修改11项，无新增/删除，依赖及迁移一致，head0025。包含检查点10秒播报及赛果查看门控、赛事名称提示、管理员强制接生、繁育公母顺序兼容。备份 `/var/backups/dzmm/5a4e915be7e6-20260928-230006/` app-before.tar.gz/production-before.dump可读。候选模块导入完成，未运行功能测试或真实赛马/繁育测试。Core先live/ok后Worker，最终两服务active、重启0，Worker connected心跳新鲜，资产对账0；failed18/sent1579与本轮发布前一致，无uncertain。未发公告，旧已完成赛事不追加播报。


## 强制接生主键冲突热修复

发布230007（完整5a4e915be7e6-20260928-230007），SHA256 `871160eac8e134e4821afbec6c443eb0ca6e702e61c84a1a904ca9caa8ad1446`，基准data/deploy-v08-delivery-fix.tar.gz。相对230006仅horse_service及两份文档，无依赖迁移变化、head0025。备份/var/backups/dzmm/5a4e915be7e6-20260928-230007下app-before.tar.gz与production-before.dump可读。候选导入完成，Core先live健康后启动Worker。未执行接生、未重放失败命令、未发公告、未运行功能测试；失败原事务整体回滚，需用户重发。


## 完整赛事排名与统一 NPC 规则发布

发布 `5a4e915be7e6-20260928-230008`，SHA256 `93ffd73407dc4b75afdf4c076b9320973744fb804d923c0027150599902c2a50`；下次基准 `data/deploy-v08-npc-rankings.tar.gz`。对比230007修改9项、无新增删除，依赖与历史迁移一致，head仍0025。包含全部排名自动多条播报和新旧未结算赛事采用买马NPC生成规则；已结算赛果不重算。备份 `/var/backups/dzmm/5a4e915be7e6-20260928-230008/` 中app-before.tar.gz和production-before.dump归档可读。候选Core导入与运行时构建成功，未运行功能测试。先Core live/ok后Worker，两服务active、NRestarts0，Worker connected且心跳新鲜。21账户、683条ledger、余额865867币不变；余额与奖池账本差额0；failed18/sent1617不变、无uncertain。未发送公告、未进行真实赛马指令验收、未提交或推送Git。


## 每日天气及累计修复发布（2026-09-29）

发布 `5a4e915be7e6-20260929-112536`，包 SHA256 `20956c43a63830ea99a13b10267bae7f68c7379d519e9f1c80c1dce6a040dc4f`；基准包 `data/deploy-v08-npc-rankings.tar.gz` 与服务器发布哈希一致。包差异为新增5项、修改29项、删除0项，依赖锁未变化。新增迁移 `0026_daily_weather`，head 从 `0025_red_packets` 更新到 `0026_daily_weather`。`0024_horse_feed_budget` 的列创建增加已存在检查，解决旧的未版本化数据库从当前元数据启动时重复加列；该兼容检查不删除或重置数据。

本地与服务器候选全套测试均为110项通过；Core/Worker隔离冒烟通过。正式库副本恢复后迁移、重复迁移与旧数据逐表摘要核对通过；正式迁移重复执行也通过。正式停服后备份保存在 `/var/backups/dzmm/5a4e915be7e6-20260929-112536/`（`app-before.tar.gz`、`production-before.dump`）；临时演练库已按精确名称清理，发布包与候选目录保留。

最终 Core、Worker、PostgreSQL 均 active，Core live/ok，Worker connected 且心跳新鲜，两服务 NRestarts=0；余额差额0、账户不匹配0、账本差额0。既有 `pool_tax_difference=120` 与发布前一致，未新增账本异常。outbox failed保持18、uncertain为0；sent由1989增至2001。生产配置 `DZMM_WEATHER_ENABLED=true`；香港日期 `2026-09-29` 的全服天气记录已生成（drought），公告已入队。未提交或推送 Git，未另发版本更新公告。


## DeepSeek解说与牧场/马粮修正发布（2026-09-29）

发布 `5a4e915be7e6-20260929-135400`，包 SHA256 `834893d06fecb703a2473d2cda2018de623fe9aae142e9ec3b830211fd096c06`；基准包为 `data/deploy-5a4e915be7e6-20260929-112536.tar.gz`。包内新增3项、修改25项、删除0项；依赖锁和 Alembic head 未变化（仍为 `0026_daily_weather`）。包括 DeepSeek赛马检查点/赛果解说、此前本地牧场面板与马粮配方/批次增产改动，以及部署对账门禁修正。

本地与服务器候选全套测试均为110项通过，Core/Worker隔离冒烟通过。首次切换因门禁错误要求历史 `pool_tax_difference` 必须归零而自动恢复旧代码；核对确认该120差异已在上次发布记录中存在，且未变化。发布脚本已改为对比停服前后的余额/奖池账本差异及账户不匹配快照，任何新增差异仍会阻止完成。第二次发布成功；正式备份位于 `/var/backups/dzmm/5a4e915be7e6-20260929-135400/`，含 `app-before.tar.gz`、`production-before.dump`；服务环境文件原样备份为 `dzmm.env-before`（权限600）。

服务器 `/etc/dzmm/dzmm.env` 已启用赛马AI并配置用户此前提供的密钥，密钥未进入代码包或日志。Core按维护流程重启后 healthy/live，Worker connected 且心跳新鲜，Core/Worker/PostgreSQL 均 active。生产配置下通过已部署的 `RaceCommentaryService` 完成一次不入群真实 API 验证：`source=ai`、无失败，约705ms；未发送群消息或公告。余额差异0、账户不匹配0、pool ledger差异0；既有 pool tax 差异120保持不变。failed为20、uncertain为0；相较上次记录的18，新增两条记录实际发生在本次部署前（香港时间11:41），部署期间没有增加。未提交或推送 Git。


## 赛马逐马播报候选发布阻断（2026-09-29）

脏工作区候选包 `5a4e915be7e6-20260929-143917`，SHA256 `1c695955c0fe68438396616485ecd094714c19f316779dd70c3a8c6ec2e2d338`；使用已核验的 `135400` 基准包和现有 SSH `known_hosts`。差异新增1项、修改9项、删除0项；迁移 head 不变。逐文件审阅并上传后，本地全套110项通过；服务器候选全套测试110项中，`test_production_cap_and_replay_verification` 失败，产出重放核对报告大量序号不匹配。该测试在本机独立重复5次也出现1次失败，确认是不稳定门禁而非赛马播报定点测试失败。按脚本保护在正式切换前停止；没有停正式服务、备份/迁移/替换正式代码。候选包与候选目录保留供调查。


## 审计修复后切换自动恢复（2026-09-29）

审计器按产品行中持久化的基础产量、动物数量、增产奖励与倍率核对最终数量；测试固定闪光动物确保覆盖增产批次。旧版随机购买到闪光动物时，旧校验器将合法增产报成差异。候选 `5a4e915be7e6-20260929-145057` SHA256 `1abc47011a1f80f4b07a6f4b079e82da0b7afcf4523cdb5a251bfd680d5d21bd`，逐文件复核后上传；本地和服务器全套测试各110项通过，Core/Worker隔离冒烟通过，迁移head保持 `0026_daily_weather`。

脚本获准切换后短暂启动新Core/Worker，但最终健康/不变量门禁失败，自动恢复 `135400` 并重新启动服务。脚本输出没有记录具体失败断言；候选启动日志显示Core启动完成、Worker已建立Socket连接。当前只读复核确认旧发布标记与SHA仍在，Core/Worker/PostgreSQL active，Core `/healthz` 正常，Worker connected且心跳新鲜；余额差异0、账户不匹配0、pool ledger差异0、既有pool tax差异120。没有迁移或恢复数据库。新候选目录、发布包与正式切换前备份保留。此处记录首次失败后的状态；后续成功重试与验收见下文“2026-09-29 赛马播报部署重试成功”。


## 2026-09-29 赛马播报部署重试成功

- 发布ID：`5a4e915be7e6-20260929-150011`；包SHA256：`a71014e5143151fe3d1782c6c21cdc41893afd61898bd6eb2f1b6c25ae5cedaa`。基准为当前服务器 `135400` 包；脏工作区包差异新增1、修改12、删除0，逐项审阅后上传。
- 解决措施：部署脚本最终 Core 健康请求增加短暂启动重试，并输出发送失败计数、账本对账快照、服务重启计数的基线/当前值。首次失败的具体断言无法从旧脚本日志还原；本次三组门禁均明确匹配。另将所有门禁诊断输出置于比较之前，供后续发布脚本使用。
- 本地全套测试110项通过；服务器候选全套测试110项通过，Core/Worker隔离冒烟通过。迁移head仍为 `0026_daily_weather`，无迁移执行。
- 备份目录：`/var/backups/dzmm/5a4e915be7e6-20260929-150011/`，含切换前应用包及正式数据库备份。
- 独立只读验收：发布标记与包哈希匹配；Core、Worker、PostgreSQL均active，Core `/healthz` 为live/ok，Worker connected且心跳新鲜，服务重启计数0。发送failed 20、uncertain 0；余额差异0、账户不匹配0、pool ledger差异0；既有pool tax差异120未变化。
- 无公告或正式群测试消息；未提交或推送Git。

### 公测删档（2026-09-29）

- 发布：`5a4e915be7e6-20260929-160315`；包 SHA256 `e1e72b041f052ae0700bcb27671da9a2748ed9be29068a82d362bee00838831c8`；候选110项通过，Core/Worker隔离冒烟通过；迁移head `0026_daily_weather`，无迁移。
- 在用户确认删除玩家账号、全部游戏数据和管理员授权后，先停Worker再停Core，并完成删档前数据库快照：`/var/backups/dzmm/5a4e915be7e6-20260929-160315/beta-wipe-before.dump`。文件权限600，大小2,056,379 bytes，PostgreSQL归档读取验证通过。
- PostgreSQL 单事务清除所有玩家/账户/资产/账本、牧场/农场/加工、马匹/繁育/赛事/拍卖、红包、游戏消息队列、公告投递、世界动态、天气历史及 `game_admin_members`；奖池清零。保留表结构、`group_chats`、`admin_audit_log`、`game_config_versions`、`skill_definitions`、`game_updates`、Bot登录会话和群邀请记录。验证用户及授权数据为0；保留4个群、4个配置版本、48个技能定义。服务启动后调度器重新创建了2条新赛事和2条当日天气，并按每日天气公告机制向3个启用群发送当日天气公告；这是删档后新生成的系统状态，不是旧玩家进度或手动更新公告。
- 提交事务后的远程脚本因 here-document CRLF 收尾提示语法错误并返回非零；随后独立只读核验事务确已提交，Core、Worker、PostgreSQL均active，Core health正常，Worker connected且心跳新鲜，重启计数0，余额/账本/奖池对账差异0。未恢复旧数据库，未发公告或测试群消息，未提交/推送Git。

### 马厩容量规则部署（2026-09-29）

- 发布 `5a4e915be7e6-20260929-162354`；包SHA256 `0071bde3dd492f010049257b64b228c19bfe1875e1b90332173d24285cd9fb67`；基准为删档后发布 `160315`。包差异修改5项、无新增/删除，迁移head仍为 `0026_daily_weather`。
- 容量调整至 Lv.1=3，之后每级+1（Lv.5=7）；买马和拍卖托管入厩继续共用容量常量。定点容量测试通过；部署流程本地与服务器全套测试各110项通过，Core/Worker隔离冒烟通过。
- 首次切换被部署终检门禁自动恢复：删档后 `manage.sh status` 对零失败队列省略 `outbound.failed` 字段，脚本直接索引触发KeyError。脚本两处改用 `.get('failed', 0)` 后再次部署成功。无需迁移；最终两服务及PostgreSQL active，Core health live，Worker connected且心跳新鲜，服务重启计数0，账务对账与切换前一致。保留首次尝试恢复的旧版和候选备份；正式切换备份目录 `/var/backups/dzmm/5a4e915be7e6-20260929-162354/`。未发送更新公告，未提交/推送Git。
### 管理员私聊公告命令部署（2026-09-29）

- 发布 `5a4e915be7e6-20260929-164418`；SHA256 `a8d3302397838056269fe44f1b3248d536c3e8d97f106b14531a78c010574bf9`；基准包为 `162354`。包差异修改6项，无新增/删除；迁移head `0026_daily_weather`，无迁移。
- `/公告 <内容>` 仅允许配置管理员在 Bot 私聊触发，群内使用会拒绝并提示私聊；管理员普通文本不会广播。消息通过现有outbox/Worker发往所有启用群，回执引用私聊原消息；单条长度/换行超限时不创建群播任务，返回最终字数、换行数和超出量。
- 定点测试5项通过，本地/服务器全套测试各110项通过，Core/Worker隔离冒烟通过。首次正式切换遇到状态字段缺省导致门禁KeyError，自动恢复旧版；部署脚本已修正后重试成功。正式备份目录 `/var/backups/dzmm/5a4e915be7e6-20260929-164418/`。最终Core、Worker、PostgreSQL均active，Core `/healthz`正常，Worker connected且心跳新鲜，重启计数0，账务对账与切换前一致。未手动发送公告；未提交/推送Git。


### 私聊公告无回执修复部署（2026-09-29）

- 发布 `0c51e03124d2-20260929-172541`，包 SHA256 `471b072b109ec50eb94f16c53131a980ceb8cfeaf715c975068dd4a1dc12777d`；基准包 `164418` 的哈希与服务器发布标记匹配。包内仅修改 `dzmm_bot/gateway.py`、`dzmm_bot/application/command_router.py` 及两份相关测试，无新增/删除文件；Alembic head `0026_daily_weather`，无需迁移。
- 原因：私聊网关允许 `/管理员登陆` 等命令，但白名单遗漏 `/公告`，故消息未送达 Core。修复将 `/公告` 加入私聊白名单，并与管理员登录一样在命令分发早期处理；空参数只返回用法，不会创建群发任务。
- 本地定点测试14项通过；本地与服务器候选全套测试各110项通过，Core/Worker隔离 smoke 通过。正式备份目录 `/var/backups/dzmm/0c51e03124d2-20260929-172541`。
- 更新后 Core、Worker、PostgreSQL 均 active，Core health 正常，Worker connected 且心跳新鲜；failed/uncertain 无新增，服务重启数0，账务核对差异未变化。未发送群公告；待管理员在私聊重试 `/公告` 空命令确认平台回执。

### 红包时限、管理员结束红包与回复效率更新（2026-09-29）

- 发布 `5a4e915be7e6-20260929-202019`，包 SHA256 `77bdef0c0642d81d7ef301217a0ac76c7985c32cf355dd80ea73b210b90e8f22`；基准为服务器 `0c51e03124d2-20260929-172541`，其 SHA256 与服务器发布标记一致。包差异新增2项、修改16项、删除0项，依赖锁未变化。
- 发布内容包括 `/发红包 <金额> [红包数量] [持续分钟]`（默认10份、2分钟；每次领取重置时限）、管理员 `/结束红包`（退回未领金额并写入既有账本）、多配方加工逐线占用、`/加工 <配方> <数量>` 数量按产线数解释，以及每群独立回复节流和默认 Socket 间隔0.2秒。
- 本地全套测试110项通过；服务器候选全套110项通过，Core/Worker 隔离 smoke 通过。PostgreSQL 正式库副本的 `0026_daily_weather → 0027_red_packet_duration` 迁移及重复迁移通过；所有原有表数据摘要一致，既有红包默认时限为120秒。正式迁移后重复运行检查器也通过。
- 正式备份目录 `/var/backups/dzmm/5a4e915be7e6-20260929-202019/`，包含权限600的应用、正式数据库和演练数据库归档。正式 Alembic head 为 `0027_red_packet_duration`。
- Core、Worker、PostgreSQL 均 active；Core `/healthz` 返回 live/ok；Worker connected 且心跳新鲜；Core/Worker 重启计数均0。余额差异0、账户不匹配0、账本差异0；既有 `pool_tax_difference=36.8` 未变化。outbox failed 25、uncertain 3 均与切换前相同；pending 3，sent 从1661增至1662。未发送更新公告或进行真实红包操作，未提交/推送 Git。

### 全局匀速出站限制及队列清理（2026-09-29）

- 发布 `5a4e915be7e6-20260929-211206`，包 SHA256 `25e8506f1c554f4676a2df5e2ce8001094c6fc96550d1ec8be7647b81cfff105`；基准 `202019` 包 SHA256 与正式服务器标记一致。候选包相对基准修改9项，无新增/删除；Alembic head 仍为 `0027_red_packet_duration`，无迁移。
- 全局队列领取间隔设为1秒，最多60条/分钟且无突发额度，跨群和重启保持；平台发送失败不自动重发，`uncertain` 不再阻挡同群后续消息。增加管理员清队列工具，采用状态取消并保留记录/审计。
- 本地与服务器全套测试各111项通过，Core/Worker 隔离 smoke 通过。正式备份目录 `/var/backups/dzmm/5a4e915be7e6-20260929-211206/`；Core、Worker、PostgreSQL 均 active，Core health 正常，Worker connected 且心跳新鲜；账务差异与基线一致。
- 清理前队列有157条 pending、3条 uncertain；按用户明确要求通过管理员工具将160条标记 `cancelled`，未删除历史行。复核时无 pending/leased/sending/uncertain，failed 28、sent 1709、cancelled 160。未发送群公告，未提交/推送 Git。


### 马粮商店直购价格调整（2026-09-29）

- 发布 `5a4e915be7e6-20260929-213719`，包 SHA256 `33e4ea21d3dee80351d8124f502afca83667df7b1ae229864cb26c2ebc104811`；基准 `5a4e915be7e6-20260929-211206` 包哈希与服务器标记一致。候选包新增1项、修改5项、删除0项；Alembic head 仍为 `0027_red_packet_duration`，无迁移。
- 马粮商店直购单价为参考价×100，菜单展示和实际扣款共用统一价格函数；农场配制不变。
- 本地与服务器全套测试各111项通过，Core/Worker 隔离 smoke 通过。备份目录 `/var/backups/dzmm/5a4e915be7e6-20260929-213719/`。Core、Worker、PostgreSQL 均 active，Core health 正常，Worker connected 且心跳新鲜；账务对账与发布前基线一致。


### 全局出站上限调整为每分钟120条（服务器直接热修改，2026-09-29）

- 服务器 `dzmm_bot/store.py` 的持久化全局无突发调度间隔由1.0秒改为0.5秒，理论吞吐上限由60条/分钟变为120条/分钟。本地源码同步修改，后续发布不会覆盖该设置。
- 原文件备份 `/var/backups/dzmm/manual-global-limit-120-20260929/store.py-before`，权限600。此次只修改该文件，未改环境变量、发布标记、数据库或队列。
- 按 Core 先启动并验证健康、再启动 Worker 的顺序重启。Core/Worker/PostgreSQL active，Core health 正常，Worker connected且心跳新鲜；复核时 pending 0、uncertain 0、failed 140。未运行测试。
- 发布标记和包 SHA 不变；下次发布应以该正式发布包为基准，并包含源码中的0.5秒间隔。


## 马匹繁育遗传规则修正（开发记录；后续随 V0.8.8 部署）

新配种时冻结五维属性：每项在父母较低值至较高值×1.2之间均匀抽取整数；保留既有成长等级潜能遗传规则。各距离/场地/跑法适应性等级逐项从父母随机遗传，独立有10%提升一级并封顶S。距离、场地和跑法类别分别从父母继承；每类有5%同时继承双方，父母已有多选时先各抽一个候选。幼马名字随机采用“父名前半+母名后半”或反向组合；数值、类别、名字均写入配种快照，接生使用已冻结值。无数据库迁移，旧待产记录保持原快照规则。专属遗传及类别测试、马库规则测试共14项通过；compileall和diff check通过。完整旧 `tests.test_horse_catalog` 回归有14项因夹具仍用已弃用的 `/注册 测试玩家` 格式而报错，非本次遗传逻辑失败。未部署。

## V0.8.8 繁育与页面体验部署及公告（2026-09-29）

- 发布 `5a4e915be7e6-20260929-224819`，包 SHA256 `e17c0651cac2a64ee89d6c38302eacb424e9068d74a0e4441ea48ffd34112ad4`；正式基准包 `5a4e915be7e6-20260929-213719` SHA256 与服务器发布标记一致。候选包新增1项、修改13项、删除0项；依赖与 Alembic head 未变（`0027_red_packet_duration`），无迁移。
- 包含幼马五维范围、双亲适性/类别继承及组合命名；马匹详情显示多项继承适性；同步 `store.py` 中0.5秒全局调度，与服务器每分钟120条配置一致；更新牧场亲密度、种子商店和马粮配方展示，并调整对应测试快照。
- 本地全套111项通过；服务器候选全套111项通过，Core/Worker隔离 smoke 通过。备份 `/var/backups/dzmm/5a4e915be7e6-20260929-224819/`。Core、Worker、PostgreSQL 均 active，Core health 正常，Worker connected且心跳新鲜；服务重启数0，failed数184与切换前一致，账务对账快照无变化。
- V0.8.8 公告经 `AnnouncementService` → outbox → Worker 广播，任务 `d6bee251-0495-4b97-8ece-57d2742f05a4`：目标6群、成功6、失败0；投递均为 `success`，outbox 均为 `sent`。公告覆盖幼马遗传、赛马播报、农场/马粮、牧场面板、红包/交易和群回复体验。


## V0.8.9 自动收获与回复体验部署（2026-09-30）

- 发布 `5a4e915be7e6-20260930-142029`，包 SHA256 `e05a7b097c23641161145c324a6599f1b67dadb5f6b5f149573733855920288b`；核验基准包与服务器 `20260929-224819` 哈希一致，逐文件审阅新增3、修改18、删除0。无依赖/迁移变化，head `0027_red_packet_duration`。
- 包含喂食自动收取、播种自动收获、投产回执精简、非豁免命令安全短语压缩、群聊强制接生可选品质修复、私聊命令规范化及Socket ACK分段耗时。指定保留页面、马匹/赛马/更新/拍卖回复继续使用原有格式；共用函数默认不启用短语替换。精简文档个别模板仍未逐条覆盖。
- 首次本地检查3处旧预期失败，修正获准行为对应的断言与快照；自动收取断言同时核验库存到账及二次收取不重复发放。最终本地111项、服务器111项、5项新功能定点和隔离Core/Worker smoke通过。
- 标准脚本完成应用/正式数据库备份，目录 `/var/backups/dzmm/5a4e915be7e6-20260930-142029/`。Core先健康后启动Worker，两服务和PostgreSQL active、Worker connected且心跳新鲜、重启数0；余额及奖池账本差额0，既有税费统计差额50.88保持不变。
- 部署门禁failed633未变；门禁后新增1条27字符、1换行的群Reply被平台明确以“请勿发送重复内容”拒绝。最终failed634、uncertain5（既有）未变；未自动重发、未回退代码。自然回复及公告有sent记录，未另发真实游戏测试命令或目视验收Reply。
- 用户授权V0.8.9公告，manifest验证285 UTF-16字符、9换行。经AnnouncementService创建版本更新和广播任务 `c635603a-d9c7-4c0a-addd-a785cbcce6d3`；6个有效群均success、6条outbox均sent且均有平台消息ID、引用玩家数0，任务completed。发布脚本重复调用只查询既有published版本，不重复群发。


## 部署工具优化记录（2026-09-30，仅本地）

基于V0.8.9部署经验，更新 `deploy/Update-Server.ps1` 与发布包工具：自动定位并验证基准包、仅候选验证、显式部署授权、阶段日志/JSON回执、切换锁和基准复核、Worker超时与登录诊断。删除文件、依赖或迁移变化转人工流程；原有备份/对账门禁保留。无输出SSH失败也执行只读状态核查。

快捷命令及故障处理见 `docs/server-update-script-usage.md`。已完成8项离线流程场景、2项发布包测试和PowerShell/Bash语法检查；未连接生产服务器或部署。本记录不改变本文最近验证的正式发布标记。正式使用时仍须完成服务器候选测试、备份、Core/Worker健康与平台发送验收。
