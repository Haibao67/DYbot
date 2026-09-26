# M0 / M1 实现与验收记录

## 已交付

- M0：全局一次性加入、初始 100 币、香港日期救济、个人资料、中文命令路由、冻结/解冻、带工单号的正负补偿、账本查询、管理审计。
- 货币/库存账本与余额投影在同一事务提交；数据库禁止更新或删除账本、配置快照、业务事件及管理审计。系统奖池使用空玩家 ID，独立唯一索引防止空值绕过去重。
- M1：牧场等级 1–9、独立动物时钟、购买动物与饲料、喂食、惰性产出、收获、按香港时间波动的市场售价、逐品类出售计税、即时升级及全局余额总榜。
- 所有游戏命令保留原收件箱/发件箱事务；失败的业务子事务整体回滚，入站重放不会重复扣款。意外异常回滚整条入站事务，允许 Worker 安全重试。
- 旧收发队列表已抽至 `persistence/transport.py`，游戏分派移至 `application/command_router.py`；玩家文本集中于 `presentation/messages.py`，纯规则集中于 `domain/economy.py`。
- 生产事件保存配置版本、随机材料和动物快照。购买/喂食使用 HMAC 派生种子；可从持久化快照复核已产出的产品。单命令最多补算 1000 批，超出时提示继续收获。

不开放马厩、赛马、彩票购票/开奖、竞猜或果园。奖池只累计交易税。

## 规则口径

- 新玩家首次加入必须来自已启用的群；随后任意已启用群或已登记私聊访问同一账户。
- 按 M1 第 4.4 节，生产期已结束的动物不能再次喂食；提前喂食结算旧产出后重置 48 小时，舍弃旧的剩余间隔。停产动物仍占容量。上线前应向玩家明确这一规则；本次没有擅自增加复活动物或出售动物功能。
- 升级不会修改已有动物的时间与等级快照；新购、重新喂食时采用当时等级。
- 基础榜单采用当前余额总榜；日赛/周赛积分榜留给后续赛事阶段。
- 旧英文玩家指令明确拒绝并引导 `/帮助`，历史旧消息内容保持原样。

## 可复现验证

在项目虚拟环境运行：

```powershell
.\.venv\Scripts\python -m unittest discover -q
.\.venv\Scripts\python smoke_test.py
.\.venv\Scripts\python -m dzmm_bot.simulate_economy
```

42 项自动测试通过，覆盖：100 次并发/重复加入、多连接跨群加入、重启重放、救济余额边界与香港跨日、账户冻结、管理员认证与补偿重放、失败事务与注入崩溃回滚、账本不可变、买卖税额、容量、满级、非法数量、跨玩家动物访问、收获/出售并发、分页、生产 48 小时边界、1000 批上限和生产复核。

独立 Core/Worker 进程测试通过：中文加入、资料、购买鸡、查看牧场；账户余额 47，奖池 3，消息全部模拟完成，心跳正常。

28 天模拟结果见 [逐日 JSON 报告](economy-simulation-28d.json)。12 名玩家从正常初始资源开始，每日收获、出售、按资格救济、喂食，并按不同动物偏好购买或升级。共复核 129 只动物、16741 个产品，无生产差异；每日逐玩家账本对账、总余额对账、交易税/系统奖池账本对账全部为零差异。第 28 日总余额 21812、奖池累计税额 8786。鸡偏好玩家成长明显快于羊/牛偏好玩家，数值配置保持原方案，报告供灰度期间评估。

## 迁移

启动 Store 自动运行 Alembic 至 `0002_ranch`。空库、合成旧库、重复启动迁移已有自动测试。

真实 `data/core.sqlite3` 使用 SQLite backup API 复制到 `data/migration-rehearsal.sqlite3` 后迁移，原库未变更。旧表记录数均保留：players=1、memberships=1、inbound_messages=2、outbound_messages=2、group_chats=1。

PostgreSQL 离线迁移 SQL 已成功生成至 `data/migration-postgresql.sql`，覆盖表、唯一索引和不可变触发器。当前环境无 PostgreSQL 服务，**尚未完成 PostgreSQL 实库迁移/并发演练**；不得把离线 SQL 验证视为生产数据库验收。

正式升级前停止 Core/Worker，备份数据库及关联配置，更新依赖后启动单 Core。SQLite 迁移与入站写入使用 `BEGIN IMMEDIATE`；PostgreSQL 迁移使用事务级 advisory lock，游戏写事务先锁世界单例。仍按单 Core 部署，不宣称支持多 Core 发件箱调度。账本迁移禁止破坏性 downgrade，回滚须恢复经验证的完整备份。

## 管理接口

均要求现有 `X-Admin-Token`；管理员不能通过群聊发管理指令。

| 接口 | 用途 |
|---|---|
| `POST /admin/players/{id}/freeze` | 冻结玩家 |
| `POST /admin/players/{id}/unfreeze` | 解冻玩家 |
| `POST /admin/players/{id}/compensate` | JSON：`amount` 非零整数、`reason` 原因、`ticket` 工单号；同玩家相同工单重放返回当前余额，不重复变更 |
| `GET /admin/players/{id}/ledger?page=1` | 每页 100 条货币账本 |
| `GET /admin/players/{id}/ranch?page=1` | 动物及市场交易分页查询 |
| `GET /admin/players/{id}/verify-production` | 只读复核产品与购买/喂食快照 |
| `GET /admin/status` | 原消息/Worker 状态，以及账户数、账本数、配置版本、余额/税收对账指标 |

`python -m dzmm_bot.manage status` 自动显示这些游戏指标。

## 尚需上线环境执行

代码、离线测试和模拟已完成；尚未向真实群发布或重启在用服务。M0 的 24 小时观察，以及 M1 白名单测试群、延迟开放出售与 72 小时观察，属于真实运行验收，不能由加速模拟替代。

灰度配置：`DZMM_GAME_STAGE=m0` 只开放账户；`ranch` 开放购买/查看/喂食/收获，关闭出售与升级；`full` 开放完整 M1（默认）。`DZMM_GAME_WHITELIST` 为逗号分隔的平台玩家 ID，非空时仅名单内玩家可访问牧场与榜单。更改配置后重启 Core；帮助文本跟随开放阶段变化。应使用测试群及隔离数据库执行灰度，PostgreSQL 实库演练和上述观察完成后，再签署生产上线验收。
