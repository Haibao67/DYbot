# M0 执行方案：全局账户、账本与命令底座

## 1. 目标与完成定义

M0 不开放牧场、赛马或彩票玩法；它交付一个可安全承载所有后续玩法的全局游戏底座。

完成后，任意已启用群中的新玩家可仅成功执行一次 `/加入`，获得一个全局账户和初始余额；可从群聊或私聊查询 `/我的`。每一笔初始发放、救济金或管理员补偿都有账本记录。重复消息、网络重试、Core 重启均不会造成重复变更。

M0 不创建果园相关表、物品、命令或配置。

## 2. 交付范围

| 范围 | M0 交付 | 明确不做 |
|---|---|---|
| 身份 | 全局玩家档案、一次性加入、群入口授权 | 玩家交易、跨平台账号合并 |
| 经济 | 冬宴币余额投影、不可变账本、每日救济金 | 牧场生产、产品销售、税收、彩票奖池 |
| 物品 | 库存与库存账本基础能力 | 可获得或消耗的正式物品 |
| 消息 | 命令路由、统一错误消息、分页基础 | 富媒体、按钮、2D 回放 |
| 管理 | 审计日志、冻结玩家、补偿入口 | Web 后台页面 |
| 数据 | 数据库迁移、配置版本、测试夹具 | PostgreSQL 多实例部署 |

## 3. 实现原则

- **全局账户，不按群分账。** `player_id` 是账户的唯一键；`room_id` 只记录消息来源和通知去向。
- **账本优先。** 禁止直接更新余额；余额更新必须伴随一条账本记录。
- **单命令单事务。** 入站去重、领域状态、账本、审计和待发送消息在同一数据库事务内提交。
- **规则纯函数。** 金额、日期、资格和展示格式与数据库访问隔离，方便测试。
- **金额均为整数。** 冬宴币不用浮点；所有时间存 UTC，以香港日期判定每日救济金。

## 4. 数据库与迁移

现有项目仍在 `Store` 中使用 `MetaData.create_all`。M0 应引入 Alembic，使结构演进可追踪；首次迁移只创建新游戏表，不删除已有 `players`、`memberships`、`inbox`、`outbox` 或 `group_chats` 数据。

### 4.1 新增/调整表

| 表 | 关键字段与约束 | 用途 |
|---|---|---|
| `game_accounts` | `player_id PK`、`balance >= 0`、`joined_at`、`relief_claimed_on`、`status`、`version` | 全局账户与余额投影 |
| `currency_ledger` | `id PK`、`player_id nullable`、`amount`、`reason`、`reference_type`、`reference_id`、`source_room_id nullable`、`created_at`；`(player_id, reference_type, reference_id)` 唯一 | 货币事实来源 |
| `inventory_stacks` | `(player_id, item_code) PK`、`quantity >= 0`、`version` | 可堆叠物品的当前数量 |
| `inventory_ledger` | `id PK`、`player_id`、`item_code`、`delta`、引用字段、时间；引用唯一 | 物品审计基础 |
| `world_state` | `id=1 PK`、`config_version`、`lottery_pool_balance`、`updated_at` | 全局单例，先为未来模块预留 |
| `admin_audit_log` | `id PK`、`actor`、`action`、`target_type`、`target_id`、`payload_json`、`created_at` | 管理动作可审计 |
| `game_config_versions` | `version PK`、`payload_json`、`status`、`published_at` | 已发布数值配置快照 |

`players` 延续全局身份表；旧 `memberships` 可保留为聊天群参与历史，但 M0 的“一次加入”以 `game_accounts` 是否存在为准，不能再以 `room + player` 判断。

### 4.2 初始配置

插入不可变的配置版本 `v1`，至少包含：

```json
{
  "starting_balance": 100,
  "relief_floor": 100,
  "timezone": "Asia/Hong_Kong",
  "currency": "winter_coin"
}
```

`/加入` 的初始发放引用为 `join:<player_id>`；即使不同群同时收到同一玩家的 `/加入`，唯一约束也只允许一条发放记录。

## 5. 领域服务与事务边界

建议新增如下接口；名称可按项目风格调整，但职责不能混合。

```text
AccountService.join(player, source_room)
AccountService.get_profile(player)
AccountService.claim_relief(player, source_room, now)
LedgerService.credit/debit(...)
InventoryService.add/remove(...)
AdminService.freeze/unfreeze/compensate(...)
CommandRouter.dispatch(inbound_event)
```

### 5.1 `/加入` 流程

1. Core 验证房间已启用并写入收件箱去重记录。
2. Router 识别 `/加入`；应用服务以 `player_id` 查询账户。
3. 若账户已存在：只生成“你已加入全局游戏”的回复，不改变余额或库存。
4. 若账户不存在：创建/更新 `players`，创建 `game_accounts`，写入一笔 `join_reward` 账本，创建审计事件和发件箱消息。
5. 事务提交后由现有 Worker 发出消息。

数据库写入应使用行锁或 `INSERT ... ON CONFLICT` 处理并发；SQLite 本地通过现有 Core 锁与事务保护，PostgreSQL 生产环境还要依赖唯一约束作为最终防线。

### 5.2 救济金流程

命令暂定 `/救济`。仅当余额 `< 100` 且香港日期不同于 `relief_claimed_on` 时可领取，补足到 100；若余额为 30，账本金额为 `+70`。同一事务更新领取日期与余额。

这不是凌晨批处理任务：每日资格由日期比较自然恢复，避免一次性扫描所有玩家。

### 5.3 账本写入规则

`LedgerService` 接受正负整数金额。扣款先校验余额，随后插入账本、更新投影余额、校验余额非负。每个业务引用只能写一次；遇到唯一冲突时读取已有结果并返回幂等成功，不再次变更。

系统奖池等未来系统账户用 `player_id = NULL` 与明确的 `reason` 标识，不能伪造玩家 ID。

## 6. 命令、反馈与权限

### 6.1 回复规范落地

M0 在 `presentation/messages.py`（或同职责模块）建立统一模板，不允许应用服务直接拼接玩家可见文本。回复必须准确简洁、格式清晰，并多使用有语义的 Emoji：成功 `✅`、提醒/失败 `⚠️`、账户 `👤`、余额 `🪙`、救济 `🎁`。每条短回复控制在 1–3 个 Emoji，避免为了装饰而重复。

统一顺序为“状态标题 → 核心变化 → 当前余额或限制 → 一个下一步”。例如：

```text
✅ 已加入冬宴游戏 🎉
🪙 获得：⨀ 100
当前余额：⨀ 100

输入 /我的 查看资料
```

`/我的` 采用字段换行，`/救济` 必须显示补足金额和领取后的余额；重复 `/加入`、余额不符合救济条件、冻结账户等情况以 `⚠️` 开头，明确说明原因与下一条可执行指令。所有模板从已提交的领域结果取值，禁止用预估金额或客户端时间。

M0 启用：

```text
/加入        首次加入全局游戏
/我的        昵称、余额、加入时间、账户状态
/救济        条件满足时补足至 100 币
/帮助        仅展示已开放命令
```

在群聊中 `/我的` 只展示命令发送者自己的信息；私聊也只允许查询自己。未知命令以 `⚠️ 未识别的指令` 加一条 `/帮助` 引导回复，不创建任何游戏数据。冻结账户可查询但不能执行资产变更。

管理员命令不接受聊天文本触发。保留现有 `admin_token` 内部 API，增加：

```text
POST /admin/players/{id}/freeze
POST /admin/players/{id}/unfreeze
POST /admin/players/{id}/compensate
GET  /admin/players/{id}/ledger
```

补偿请求必须包含正/负金额、原因和外部工单号；工单号作为账本引用，重复调用不会重复补偿。

## 7. 代码改造顺序

1. 建立 `persistence/`、Alembic 配置与第一个迁移；用导入测试确保旧库可启动。
2. 从 `store.py` 抽出既有收发队列表；新增游戏仓储与事务上下文。
3. 实现账户、账本与配置读取服务，并为规则函数建立单元测试。
4. 将旧英文指令迁移为 `/加入`、`/帮助`、`/我的` 等中文玩家指令，并把游戏分派从 `Store.receive()` 移至 `CommandRouter`；不保留英文玩家别名。
5. 加入 `/救济`、冻结与补偿内部 API、管理员审计。
6. 调整 `manage` 状态命令，输出账户数、账本条数、配置版本和待处理消息数。
7. 完成迁移演练：空库、已有 SQLite 测试库、PostgreSQL 三种路径。

## 8. 测试与验收

### 自动测试

- 100 个并发或重复 `/加入` 仅创建一个账户、一笔初始发放。
- 同一玩家从两个已启用群 `/加入`，第二次无余额变化。
- 余额 0、30、99、100、101 的救济金边界正确；香港 00:00 前后符合预期。
- 任何失败事务不留下孤立账户、余额变化或发件箱消息。
- 账本总和与所有账户余额投影可对账；负数扣款拒绝。
- 补偿 API 重放、冻结账户、未知命令、禁用房间和私聊查询均符合权限。
- 中文 `/加入`、`/我的`、`/帮助` 的回归测试通过；英文玩家指令被明确拒绝并引导至 `/帮助`。
- 对 `/加入`、`/我的`、`/救济`、重复加入、余额不足和冻结状态做消息模板快照测试，校验 Emoji、金额标签、换行和下一步指引。

### 手工验收

1. 在两个群用同一账号执行 `/加入`，确认只获得一次起始币。
2. 在私聊执行 `/我的`，余额与群聊一致。
3. 将余额降至不足 100 后领取救济，检查账本、余额和回复数字一致。
4. 重启 Core/Worker，重复发送同一消息 ID，确认无重复发钱。

## 9. 风险与上线步骤

先备份现有 `data/core.sqlite3`，在副本上跑迁移并核对 `inbox/outbox` 记录数。首次上线以 SQLite 单 Core 验证；需要 PostgreSQL 或多实例前，先完成迁移演练和事务/锁测试。

M0 上线后先观察 24 小时：重复加入率、账本-余额对账、Worker 发送失败、救济金调用量。指标稳定后才能进入 M1。

