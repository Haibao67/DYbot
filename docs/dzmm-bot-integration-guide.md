# DZMM Bot 接入与运行指南

本文说明本项目如何接入 DZMM、消息如何从平台进入游戏逻辑并返回，以及登录、群聊、原生引用、Bot HTTP API 和常见故障的处理方法。本文以仓库当前实现为准；平台未公开或代码未验证的协议不作为稳定契约。

## 1. 接入方式概览

当前项目保留 Core + Browser Worker 两个进程：

```text
DZMM 网页 / Socket.IO
        │
        ▼
Browser Worker（Playwright 持久浏览器、DZMM 登录态）
        │ 认证后的 Socket.IO：收消息、订阅房间、发原生引用
        │ HTTP：只访问内部 Core API
        ▼
FastAPI Core（身份校验、命令路由、游戏事务、SQLite/PostgreSQL、outbox）
        │
        └── 持久化待发队列 → Worker 领取 → DZMM → ACK 回写 Core
```

这不是一个可用 Telegram `getUpdates` 的轮询 Bot。也不要把下列两种发送通道混为一谈：

| 通道 | 当前用途 | 原生引用 | 认证 |
| --- | --- | --- | --- |
| 浏览器登录态中的 Socket.IO | 接收消息、订阅群、玩家回复、私聊、邀请入群 | 支持，使用已实测的 `content.reference` | 登录态刷新出的短期 access token，由浏览器会话使用 |
| DZMM Bot HTTP API | Worker 对无引用的群广播优先尝试 | 当前请求未携带引用字段；玩家回复不走此通道 | `X-Bot-Token` |
| Core 内部 HTTP API | Worker 与 Core 之间传入站消息、领取/确认 outbox | 这是内部服务接口，不是 DZMM 公共 API | `X-Core-Token` |

`DZMM_BOT_API_TOKEN` 未设置时，群广播也由 Socket 发出。若 HTTP Bot API 明确拒绝，Worker 可按现有逻辑回退到 Socket；超时或 5xx 属于结果不确定，不会盲目当作明确拒绝重发。玩家命令回复、错误提示及私聊始终走 Socket 原生 Reply。

## 2. 运行组件和边界

### Core

Core 负责：

- 验证 Worker 的 `X-Core-Token`。
- 接收规范化后的入站事件，并以平台消息 ID 做幂等处理。
- 执行命令路由和游戏事务。
- 将回复正文、目标房间及原始消息引用信息写入持久化 outbox。
- 向 Worker 暴露房间、消息领取、发送结果、心跳和邀请处理等内部接口。

Core 不直接连接 DZMM Socket，也不在 HTTP 请求里循环向全部群发送公告。

### Browser Worker

Worker 负责：

- 使用 Playwright 持久浏览器 profile 打开 DZMM 网页。
- 从同源认证会话取得身份和短期 token，在浏览器页内连接 Socket.IO。
- 订阅 Core 配置的群聊，并从 `message:new` 接收新消息。
- 校验消息结构、房间、发送者、消息 ID、时间戳和文本长度，再交给 Core。
- 从 outbox 领取消息、调用合适的 DZMM 发送通道，并将 ACK/失败状态回写。
- 同步私聊房间及管理员批准后的群聊。

Socket.IO 客户端在认证的 Chromium 页面上下文中运行，避免把 Cookie 或网页认证状态导出到另一个 HTTP 客户端。浏览器 profile 是敏感登录凭证，不能复制进代码包、提交 Git 或作为普通备份附件传播。

## 3. 配置项

配置由项目根目录 `.env` 提供；`.env.example` 只包含空值或默认值。不要把真实 token、密码、API key、Cookie 或 profile 内容写入文档、命令记录或日志。

常用设置：

| 环境变量 | 用途 | 说明 |
| --- | --- | --- |
| `DZMM_WORKER_MODE` | `simulate` 或 `live` | `simulate` 不连接平台；真实接入使用 `live`，Core 和 Worker 模式必须一致 |
| `DZMM_LOGIN_URL` | 网页登录入口 | 默认配置为 DZMM 登录页 |
| `DZMM_CHAT_URL` | 网页聊天入口 | 应为 `https://`，实际 origin 从此值解析 |
| `DZMM_BROWSER_PROFILE` | Playwright 持久 profile 路径 | 保存登录会话；需要限制文件权限并持久化存储 |
| `DZMM_BROWSER_CHANNEL` | Chromium 浏览器通道 | 支持配置中允许的 `chromium`、`chrome` 或 `msedge` |
| `DZMM_BOT_ID` | Bot 平台身份 ID | 用于排除 Bot 自己发出的入站消息 |
| `DZMM_BOT_API_TOKEN` | HTTP Bot API token | 可选；仅用于无引用群消息广播的 HTTP 发送尝试 |
| `DZMM_ME_PATH` | 可选的同源身份查询路由 | 仅接受本站相对路径；留空时从网页实际 `user.getMe` 响应识别身份 |
| `DZMM_ME_ID_FIELD` | 身份响应中的 ID 路径 | 仅在配置 `DZMM_ME_PATH` 时使用，默认 `id` |
| `DZMM_CORE_URL` | Core 内部地址 | 正式服务器通常为本机地址，不应公开监听 |
| `DZMM_CORE_TOKEN` | Worker 到 Core 的凭证 | 与管理员 token 分开，最少 24 字符 |
| `DZMM_ADMIN_TOKEN` | 管理 API 凭证 | 与 Core token 分开，最少 24 字符 |
| `DZMM_GAME_ADMINS` | 游戏管理员平台 ID | 逗号分隔；管理员命令的授权依据之一 |

其他发送节流配置见 `.env.example` 中 `DZMM_SOCKET_*`。参数需要满足 `Settings` 的范围校验；不要仅改文档值而忽略运行配置。

### 初始化和登录

在已授权的本地开发环境中，按 `docs/server-guide.md`、`docs/update-maintenance-guide.md` 的实际操作章节启动服务。首次初始化项目密钥使用仓库管理命令，不要手工复用或公开 token。生产环境则使用维护手册指定的 `/etc/dzmm/dzmm.env` 和 `dzmmbot` 服务账号。

Worker 用独立持久 profile 打开 DZMM。若日志提示 `login_or_identity_required`、`login_required` 或 `account_identity_unresolved`：

1. 保留当前代码和发布标记，先确认 `dzmm-worker` 与 Core 状态及 Worker 日志分类。
2. 按维护手册的登录恢复章节，在受保护的服务器浏览器中完成 DZMM 登录。
3. 保存 profile 后启动/恢复 Worker，确认 Socket connected、心跳新鲜。
4. 登录成功不等于群消息已验证；仍需看房间订阅、出站 ACK 与队列状态。

不可通过重置数据库、删除 profile 或关闭认证来处理登录失败。

## 4. 入站消息流程

1. Worker 通过 `BrowserSession.credentials()` 刷新聊天页，读取 `user.getMe` 身份和同源 `/api/auth/token` 的短期 access token。
2. `BrowserSocket` 在当前登录浏览器页连接 Socket.IO；项目使用 `ws/matching` 路径及认证对象 `{token: ...}`。
3. Worker 根据 Core 的启用房间和 DZMM 私聊列表，向 Socket 发 `message:join-room`，参数为 `{chatroomId: room_id}`，要求 ACK `success: true`。
4. Socket 的 `message:new` 事件只提供新到消息，不自动回填历史消息。
5. Gateway 只接受已允许房间中的文本消息，校验 `chatroomId`、消息对象里的 `chatroom_id`、`message_id`、`sent_by`、带时区的 `sent_at`、文本内容和平台长度边界；过滤 Bot 自己的消息。
6. Gateway 尽量从消息内的 `sent_by_user`、`sent_by_name` 等已观察字段取昵称。缺失时可能使用同群 `user.getChatroomUser` 查询；昵称只用于显示身份，不从引用消息内容推导。
7. 规范化事件经带 `X-Core-Token` 的内部 `/internal/inbound` 传给 Core；邀请候选走 `/internal/invites`。Core 事务处理成功后再确认请求。
8. 玩家消息 ID 必须贯穿 Core、outbox 到最终 Reply。重复投递由平台消息 ID 幂等保护。

Gateway 当前消费的规范化字段：

```json
{
  "room": "DZMM chatroom ID",
  "message_id": "原始平台消息 ID",
  "sender": "发送者平台用户 ID",
  "name": "发送者显示名（可空）",
  "text": "原始文本",
  "referenced_message_id": "可选：该入站消息自身引用的消息 ID",
  "referenced_sender_id": "可选：该引用消息发送者 ID"
}
```

字段值来自平台事件；不要用命令内容或用户名替代 `message_id` / `sender`。

## 5. 回复和原生引用

玩家主动命令的成功回复、参数错误、未知命令提示和长面板都必须引用触发它的那条原始消息。主动广播没有玩家原消息，不能附加引用。

项目当前的 Socket 发送事件为 `message:send`，外层使用 `chatroomId` 和 `message`。玩家回复的文本内容形状如下（值为示意）：

```json
{
  "type": "text",
  "text": "Bot 回复正文",
  "reference": {
    "id": "玩家原始 message_id",
    "sentBy": "原消息 sent_by",
    "content": {
      "type": "text",
      "text": "原消息正文"
    }
  }
}
```

外层消息还含 Bot `message_id`、Bot `sent_by`、`chatroom_id` 和 UTC `sent_at`。服务端 ACK 必须是对象且 `success: true`，才按成功处理。真实抓包曾确认 ACK 回显 `content.reference.{id,sentBy,content}`；这只是已观察的 Socket 结构。

HTTP `/api/bot/send-message` 当前发送体只有 `chatroom_id` 和 `content`，没有原生引用上下文。因此不得把玩家回复切换到 HTTP API，也不得臆造 HTTP 的 `reply_to` 字段。无引用群广播在满足配置条件时才可尝试该 HTTP API。

## 6. 出站队列、ACK 与消息限制

1. Core 将消息写入持久化 outbox；Worker 通过 `/internal/outbound/claim` 领取并用 lease 开始发送。
2. Worker 按消息类型选择 Socket 或 HTTP Bot API。Bot HTTP 明确拒绝时可以使用既有 Socket fallback；网络超时、5xx 或 ACK 不明时应标成 `uncertain`，不能假装成功或无条件重发。
3. Worker 把 `sent`、明确 `failed`、限流 `retry` 或 `uncertain` 回传 `/internal/outbound/{id}/result`。Core 维护最终状态及审计字段。
4. Socket 发送按当前配置串行化并经过限速器。广播批量发送要使用现有队列，不要在 HTTP handler 里遍历房间同步发送。
5. 代码和平台说明记录的文本上限为 10,000 字符；聊天 UI 另有换行/行数限制。用户提供的网页提示为最多 10 行。Gateway 只在平台明确返回长度/行数拒绝时，才尝试按行分段；短内容重复拒绝不会靠附加空格伪装成成功。
6. 分段过程中部分成功、后续 ACK 不明时，状态应保留为不确定并排查，不能把整个批次视为完全成功。

诊断时优先区分：

- `failed`：平台明确拒绝或本地校验拒绝。
- `uncertain`：请求发出后未能确认结果；平台可能已收到。
- `retry`：明确限流等可重试情况，按队列状态及现有退避处理。
- `sent`：收到可识别成功 ACK；进程运行或 HTTP 200 本身不够。

失败详情应脱敏；不要记录 access token、Cookie、API token、完整登录响应或无必要的玩家正文。

## 7. DZMM Bot HTTP API 参考

以下接口来自本项目已有适配和用户提供的 DZMM 页面示例；使用前以 DZMM 当前控制台/正式文档为准。此 API 并不取代本项目的 Socket 入站和原生 Reply。

### 发送普通文本

```http
POST /api/bot/send-message
X-Bot-Token: <api_token>
Content-Type: application/json
```

```json
{
  "chatroom_id": "目标群 chatroom ID",
  "content": "公告文本"
}
```

成功响应示例：

```json
{
  "ok": true,
  "result": {
    "message_id": "平台生成的消息 ID"
  }
}
```

平台提示 Bot 必须已加入目标群，否则会拒绝（示例中为 HTTP 403）；示例注明 `content` 上限 10,000 字符。项目只把有 `message_id` 的 `{ok:true}` 响应记为发送成功。

### Telegram 兼容路径

提供的 DZMM 页面示例还展示：

```http
POST /api/bot/bot<api_token>/sendMessage
Content-Type: application/json
```

```json
{"chat_id":"目标 chatroom ID","text":"普通文本"}
```

图片路径示例为 `/sendPhoto`，`photo` 使用 URL，当前页面说明不支持 multipart 文件上传。该兼容接口没有在本项目 Worker 出站实现中使用；不要据此推断引用回复或其他 Telegram 参数也受支持。

### Webhook 示例的安全要点

页面给出的最小 webhook 示例使用 `X-Telegram-Bot-Api-Secret-Token` 与 `process.env` 取密钥，并从 `update.message.chat.id`、`update.message.text` 读取入站消息。若自行写外部转发器：

- 先验证 webhook secret，再解析 JSON。
- 处理缺失的 `message`、`chat.id` 和 `text`。
- API Token 与 webhook secret 分开保存为环境变量；不要提交仓库。
- 对重复 webhook update 做幂等处理。
- 不要把截图中的示例密钥名当作项目 `.env` 实际变量；当前项目的入站来自 Browser Worker Socket。

## 8. 群聊启用与邀请审批

普通玩家命令只在 Core 启用的 `rooms` 中处理。Bot 已加入一个群，不代表该群已被项目配置启用；Worker 会订阅 Core 提供的启用群。

用户在 Bot 私聊发送有效 DZMM 群邀请链接或已观察到的群邀请卡片时：

1. Worker 仅监视 DZMM `chat.listAll` 返回的一对一私聊房间。
2. 邀请链接需匹配 `https://www.dzmm.io/invite/<code>`；邀请卡需为 `type=share`、`shareType=group_invite` 且具有 `resourceId`。
3. Worker 通过 `groupChat.getInviteInfo` 验证并把候选及原消息引用交给 Core。Bot 回复“已记录群聊邀请，等待管理员同意”。
4. 管理员使用 `/邀请列表` 查看候选，再以 `/同意邀请 <邀请ID>` 批准。也可按 `docs/group-invite-approval.md` 的管理命令操作。
5. Core 把批准任务交给 Worker；Worker 调用 `groupChat.joinByInvite`。只有获得群 `chatroomId` 才记录 joined 并启用群聊，随后订阅该群。
6. 不确定的 join 结果不能盲目重试；先核对平台上的实际入群状态。

邀请审批是一次明确的管理员授权动作；看到链接、读取邀请或运行本地验证都不会自动同意加入。

## 9. 从零接入检查清单

### 本地开发/新环境

1. 在受信任环境准备 Python、项目依赖、Playwright 浏览器，以及项目要求的 Core 数据库。
2. 阅读 `docs/codex-workflow.md` 对应环境章节、`docs/server-guide.md` 和本文件；生产运行再读 `docs/update-maintenance-guide.md`。
3. 通过项目管理入口初始化随机 Core/Admin 凭证，安全保存到 `.env`；不要把真实值粘贴到聊天或 Git。
4. 设置 Core 数据库、`DZMM_WORKER_MODE=live`、DZMM URL、专用浏览器 profile、Bot 平台 ID；仅在确实使用普通广播 HTTP API 时配置 Bot API Token。
5. 将目标群配置为 Core 启用房间，并确保 Bot 账号已被邀请入群。
6. 启动 Core 并确认 `/healthz` 返回架构 `core-worker` 且模式符合预期。
7. 启动 Worker，在专用浏览器完成登录；确认身份解析成功、Socket connected、群订阅 ACK 成功、Worker 心跳新鲜。
8. 只在获得授权时做真实平台消息验证；否则使用项目测试和模拟模式。正式群不得发送未经授权的测试内容。

### 正式服务器

- 使用本项目既有 `dzmm-core`、`dzmm-worker` 与 PostgreSQL 部署；具体目录、服务账号、备份、迁移与发布以 `docs/update-maintenance-guide.md` 最新验证记录为准。
- 发布脚本和脏工作区基准规则见 `docs/server-update-script-usage.md`。
- 更新后按项目要求先启动/验证 Core，再启动 Worker；确认服务 active、Core 健康、Worker connected 且心跳新鲜。仅看到 systemd active 不代表平台链路已接通。
- 不要把 Windows profile 复制到服务器；登录恢复走受保护的服务器浏览器流程。

## 10. 常见故障定位

| 表现 | 优先检查 | 不要直接做 |
| --- | --- | --- |
| 私聊、群聊都不回复 | Worker 日志中的登录/身份状态、Socket connected、Core heartbeat、outbox 队列和最近发送状态 | 不要反复重启而不读状态；不要直接清库 |
| 私聊正常、群聊无反应 | 群是否已在 Core 启用、Bot 是否仍在群内、`message:join-room` ACK、room ID 是否一致 | 不要把历史消息表当群目录批量启用 |
| Core healthy 但 Bot 不响应 | Core 健康只证明 HTTP 进程；继续检查 Worker connected、新鲜心跳、入站消息处理日志与队列 | 不要把 Core health 等同于端到端可用 |
| 登录后又掉线 | Profile 权限/持久卷、`user.getMe` 身份、`/api/auth/token`、域名与登录状态 | 不要删除或外传浏览器 profile |
| 原生引用丢失 | 是否带了原消息 ID、原作者 ID、原文；是否走 Socket `message:send`；ACK 是否成功 | 不要猜 HTTP `reply_to` 字段；不要退化为普通 HTTP Bot 发送 |
| 重复或不确定发送 | 查看 outbox 状态、`platform_ack`/`outbound_delivery_result` 脱敏日志及平台实际消息 | 不要对 uncertain 任务盲目重新执行游戏命令 |
| HTTP 广播返回 403 | Bot 是否加入目标群、token 是否属于目标 Bot、URL/origin 是否正确 | 不要把凭证贴进日志或改成忽略认证 |
| 明确提示内容过长/行数超限 | 查看平台 ACK 与原消息行数；Gateway 会按已观察的拒绝类型分段 | 不要每次无差别拆分短消息或追加字符掩盖错误 |
| 邀请停在 joining/failed | 对照 DZMM 实际群成员状态、Worker 结果和邀请记录 | 不要对超时 join 自动再次调用 |

排障先收集时间、服务名、任务 ID、状态、脱敏错误类别和 ACK 结构；发给维护者前移除 token、Cookie、个人消息正文和 profile 内容。

## 11. 当前协议限制与维护原则

- Socket `message:new` 是实时新消息事件；项目没有从它推断历史回填能力。
- `user.getChatroomUser` 取昵称的调用来自观察到的 DZMM tRPC 请求/响应形状；平台变化时要重新用网页 Network 面板确认，不能凭空改字段。
- Bot HTTP API 示例展示普通文本字段，不证明支持原生引用、附件上传或任意 Telegram Bot API 参数。
- 平台 ACK、成功 UI 呈现和游戏事务成功是三个不同层面；按相应证据分别记录。
- 变更 Worker/Core 消息协议、数据库 outbox、浏览器依赖或 migration 前，遵守 `AGENTS.md`、`docs/codex-workflow.md` 和维护手册；只跑相关测试，不把未执行的验证报告为通过。
- 对玩家的每一条命令回复（包括错误和长面板）保留该玩家原消息的原生引用。群广播和定时赛况等主动消息不引用玩家消息。

## 12. 相关项目文档

- [`AGENTS.md`](../AGENTS.md)：每轮工作、安全边界和接入维护要求。
- [`update-maintenance-guide.md`](update-maintenance-guide.md)：生产架构、登录恢复、备份、迁移、发布与验收。
- [`server-update-script-usage.md`](server-update-script-usage.md)：Windows 到 Ubuntu 的安全发布流程。
- [`group-invite-approval.md`](group-invite-approval.md)：群邀请审批与状态处理。
- [`ruihe-open-questions.md`](ruihe-open-questions.md)：已知协议证据与仍未确认事项。
- [`command-reference.md`](command-reference.md)：当前玩家命令清单。

