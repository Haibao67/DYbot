# 5.5A 执行文档：回复耗时与队列观测

> 用途：以后用户明确要求“执行 5.5A”时，Codex 直接按本文完成代码、迁移、测试和报告。阅读本文本身不构成服务器部署或真实群测试授权。

## 1. 目标与边界

建立可查询、可复现的回复耗时指标，判断延迟来自 Core 处理、outbox 等待、Worker 发送还是平台 ACK。本阶段**不改变发送间隔、消息顺序、重试策略或游戏规则**。保留 Core + Browser Worker、原生 Reply、既有 outbox、broadcast delivery 与账本。

当前代码入口：`dzmm_bot/gateway.py` 的 `on_message`/`send_socket`、`dzmm_bot/worker.py` 的 `inbound`/`drain_once`、`dzmm_bot/store.py` 的 `receive`/`claim`/`transition`、`dzmm_bot/core.py` 的管理状态接口，以及 `dzmm_bot/persistence/transport.py`。当前 `outbound_messages.created` 已记录入队时间；Worker 串行领取任务，Socket 全局至少间隔 2 秒、同一私聊至少 5 秒。先核对实际代码，若结构变化，以实际路径为准。

## 2. Codex 开始步骤

1. `git status --short`、`git diff --stat`；阅读 `AGENTS.md`、`docs/ruihe-progress.md` 和 `docs/update-maintenance-guide.md` 的任务入口。只看上述收发文件、相关迁移与 `test_core.py`/`tests/test_group_invites.py` 中关联用例。
2. 确认 Alembic 当前 head；若增字段，创建**新的**前向 migration，绝不改已发布的 `0010_group_invites`。迁移名按执行时 head 选择，`0011_outbound_timing` 只是建议名。
3. 记录本轮测试、发布是否另获授权；未获部署授权时仅修改本地代码与文档。

## 3. 事件与指标口径

按同一个 outbox task ID 记录以下 UTC 时间，数据库类型沿用现有时间字段风格：

| 节点 | 来源 | 口径 |
| --- | --- | --- |
| `created` | 既有 outbox 字段 | Core 完成游戏事务并排队回复的时间 |
| `last_claimed_at` | `Store.claim` | 最近一次成功领取时间 |
| `last_send_started_at` | `Store.transition(begin)` | 最近一次即将实际发送的时间 |
| `last_result_at` | `Store.transition` | 最近一次 ACK、明确失败或不确定结果入库时间 |

- 新字段全部 nullable。旧记录保持 null，不伪造历史时间。`retry` 时保留任务原 `created`，更新最近一次尝试时间；`uncertain` 与 `failed` 单独统计，不能算作“成功发送”。
- `queue_wait = last_claimed_at - created`；`send_attempt_duration = last_result_at - last_send_started_at`；`end_to_end = last_result_at - created` 仅对 `sent`/`simulated` 分别统计。负值、缺值和旧数据不纳入分位数，另计无效样本数。
- 额外在 Worker 用单调时钟记录“平台事件到 Core ACK”“实际 Socket/HTTP 调用到平台 ACK”耗时；只写脱敏结构化日志或有界指标，不把平台消息正文、Cookie、Token、完整私人聊天内容写入日志。
- 明确区分 `Socket` 玩家引用回复、Bot HTTP 主动群公告、私聊回复、长消息拆分。拆分任务可记录子发送数量；不得把多个子 ACK 误记为多个玩家任务完成。

## 4. 实施清单

1. 扩展 `outbound_messages` 与 Alembic migration，更新 `Store.claim` 和 `Store.transition`。时间由可注入 clock 产生，重复 result/过期 lease 不得刷新指标。
2. 在 Worker 的发送路径增加有界耗时记录，沿用现有 `failure_detail` 脱敏规则。平台 ACK 失败与超时分开计数；未知结果维持 `uncertain`，不为统计而重发。
3. 增加管理员只读性能接口，或在现有 `/admin/status` 下增加紧凑 `performance` 字段。仅 `X-Admin-Token` 可访问；返回待发数、最旧待发秒数、`sent` 样本数、最近 N 个有效样本的 P50/P95、按 group/private/broadcast 的计数及限流/失败/不确定数量。N 有上限，避免无界扫表。零样本返回 null。
4. 为查询增加必要索引；确认 PostgreSQL 与 SQLite 兼容。指标查询不得锁住业务事务或扫描消息正文。高基数房间 ID 不直接作为公开指标标签。
5. 在运维文档写明字段口径、查询方式和“耗时从 Core 入队算起”的边界。更新 `docs/ruihe-progress.md`，只记录实际完成和实测结果。

## 5. 定点测试与验收

使用 `unittest`、固定 clock、fake gateway；不使用真实日期、`sleep` 或外部平台。

- 入队→领取→开始发送→成功：四个时间点与三种耗时正确；原生引用 ID 和原消息内容不变。
- 重试：原入队时间不变，最近尝试时间更新；平台限流、`failed`、`uncertain` 不进入成功样本。
- 重复 ACK、过期 lease、Worker 重启与旧记录 null：不重复计数，不抛异常。
- 10/100/500 条假任务：待发数、最旧等待时间、P50/P95 和样本上限正确；管理员以外调用被拒绝。
- 空库、SQLite 旧库迁移、重复迁移及 PostgreSQL 副本迁移：旧玩家、outbox、资产和账本原值保留。

先跑本阶段定点及 `test_core` 相关回归；阶段收尾跑一次全套与 `smoke_test.py`。测试数与失败数按实际输出报告。只有获得单独部署授权后，才按维护手册完成候选测试、数据库副本演练、备份、迁移、先 Core 后 Worker 启动与心跳验收。真实群压力测试不属于 5.5A。

## 6. 交付标准与下一阶段输入

交付：改动文件、migration、管理员指标示例（使用假数据）、测试命令/结果、旧数据兼容结论，以及一份**实测基线**：队列等待 P50/P95、发送 P50/P95、限流/失败/不确定数量、观测窗口与样本数。没有生产采样授权时仅交付本地模拟基线，并明确标注。5.5B 必须先读取这些基线，不能根据理论上限宣称提速。
