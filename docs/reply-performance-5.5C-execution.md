# 5.5C 执行文档：高峰队列调度与有限并发

> 用途：以后用户明确要求“执行 5.5C”时，Codex 按本文完成一个可验收切片。本文不授权直接部署、恢复数据库或向正式群压测。

## 1. 前提与目标

前提：5.5A 已提供可信的待发深度/P95 耗时；5.5B 已建立统一 Socket 限速器，并取得稳定的安全速率。若实际平台限速仍是主要瓶颈，本阶段只能改善公平性和公告对玩家回复的影响，**不能承诺突破平台吞吐上限**。

当前 `Store.claim` 按 `created, id` 扫描待发任务、阻止同房间后续任务越过未完成任务；`Worker.outbound_loop` 每轮只领取并发送一条；`Gateway.send_socket` 用同一个 `socket_lock` 串行平台调用。需保留 Core + 单 Browser Worker、现有数据库 outbox、原生 Reply、广播 delivery 审计及“未知是否已发送时不自动重发”的规则。本阶段不创建第二个使用同一浏览器 Profile 的 Worker。

## 2. Codex 开始步骤

1. 执行 `git status --short`、`git diff --stat`；读 `AGENTS.md`、`docs/ruihe-progress.md`、5.5A/B 结果和维护手册入口。
2. 只看 `dzmm_bot/store.py` 的 `claim`/`transition`、`dzmm_bot/worker.py` 的 `drain_once`/`outbound_loop`、`dzmm_bot/gateway.py` 的发送锁、`dzmm_bot/persistence/transport.py`、broadcast delivery 联动及对应测试。
3. 从 5.5A/B 实测记录“玩家回复 P95、公告队列量、ACK 耗时、限流率”。明确本轮是否只有本地实现；未获真实测试或部署授权时只用 fake gateway。

## 3. 调度契约

| 类别 | 判定 | 调度原则 |
| --- | --- | --- |
| 玩家主动指令回复 | 有 `reply_to_message_id` | 跨房间优先于主动公告；原生引用必须保持 |
| 主动公告、世界动态 | 无 `reply_to_message_id` | 较低优先级，仍须最终发送并保留原 broadcast 状态审计 |
| 同房间任务 | 相同 `room` | 按现有 `created, id` 顺序；前序 `leased`、`sending`、`uncertain` 阻挡后序 |
| 不同房间任务 | 不同 `room` | 可公平轮转；一个繁忙群不得无限占用领取机会 |

优先级只在**可领取的各房间队首之间**比较：不能为了优先发送同一群的新玩家回复而越过该群已排队的旧任务。`uncertain` 需要人工确认，绝不通过调度器偷偷跳过或重发。失败任务对后续任务的影响沿用现有明确规则，并补测试。

## 4. 数据库与领取实现

1. 先用假负载量化当前 `Store.claim` 的查询/扫描成本。优先扩展现有 outbox，而非新建平行队列。需要索引时只追加 migration；执行时以当时 Alembic head 为准。
2. 让 Core 一次只领取**已有发送容量**能立即处理的任务，不预先租赁大量任务等待 5.5B 限速器。否则 60 秒 lease 可能在等待中失效，产生 `uncertain` 或重复领取。
3. 领取必须是原子操作。PostgreSQL 使用适合现有事务隔离的行锁/`SKIP LOCKED` 或等效条件更新；SQLite 测试分支保持正确语义。多个并发领取不可获得同一 task；同一房间同时最多一个在途发送。
4. 候选选择先找每个房间的队首，再按“玩家回复优先、等待时间、公平性”排序。查询必须有界并可解释，不允许每次把全部历史 outbox 加载到 Python。对长期积压公告设置防饥饿规则，并证明不会让玩家回复无限等待。
5. `begin`、平台 ACK、`retry`、`failed`、`uncertain` 及 broadcast delivery 状态继续走现有事务。取消/关闭任务时仍需准确释放房间占用；不得因索引优化破坏幂等。

## 5. Worker 有限并发

1. 在**单个** Worker 内加入小幅、可配置的在途上限，默认先取与现有行为相同的 1。假 Gateway 验证后才考虑 2–4；上限需有合理边界。
2. 将“预备任务”和“实际平台发送”分离。5.5B 的全局限速器对所有 Socket 发送仍唯一有效，同房间锁阻止并发发送；长回复的多个分段视为一个占用房间的任务直到结果确定。
3. 当前 `Gateway.socket_lock` 同时保护发送和入群操作。只有在确认 Playwright 页面并发 `evaluate` 与 Socket ACK 安全后，才缩小锁范围；若验证不安全，保留单 Socket 发送者，只实现跨房间的准备并发和公平领取。不能为了并发绕开原生 Reply 或启动第二个共享浏览器进程。
4. Worker 停止时先停止领取新任务，等待有限时间处理在途结果；超时任务走既有 `uncertain`，不自动重放。心跳应继续反映实际连接状态。Core 不可用时保持有界重试，不能产生无限内存积压。

## 6. 定点测试、模拟与验收

使用固定时钟、fake Core/Gateway、SQLite 与 PostgreSQL 测试库；不使用 `sleep` 或真实群压测。至少覆盖：

- 10/100/500 个假群、不同房间同时请求：领取唯一、同房间顺序正确、跨房间公平。
- 公告积压时其他房间的玩家回复优先；同房间不越队；公告不会永远饿死。
- 两个并发 claim、Worker 中途退出、lease 到期、ACK 丢失、部分长回复成功：任务不会双发，未知结果保持 `uncertain`。
- 广播 job/delivery 成功、失败、重试计数与 outbox 一致；成功房间不会重复公告。
- 入群成功私聊与普通玩家 Reply 仍引用各自原消息；不同房间并发不会串引用。
- 假负载下对比 5.5A/B 相同数据集的 P50/P95 等待、吞吐、数据库查询耗时；不要把 fake ACK 测试称为真实平台容量。

先跑调度定点测试及 `test_core`/broadcast 相关回归；阶段末跑一次全套、`smoke_test.py` 和 PostgreSQL 并发测试。涉及迁移时做旧生产库副本演练与资产对账。真实平台验证仅在获授权的测试群，以少量交错消息确认原生 Reply、顺序和 ACK；不得对正式群做 100/500 群压测。

## 7. 发布、回退与交付

仅在本轮另获部署授权时，按 `docs/update-maintenance-guide.md` 备份、迁移演练、候选测试、先 Core 后 Worker 启动，并验证 active、Core 健康、Worker connected、心跳新鲜、资产对账及新增 `failed`/`uncertain`。若并发导致错序、重复或不确定结果增加，先把在途上限恢复 1；仍不正常则按维护手册恢复兼容代码，保留队列与调查记录，不重放不确定消息。

最终报告列出调度规则、实际文件和 migration、测试数量、同负载前后 P50/P95、平台 ACK/限流变化、是否发生重复或错序，以及不能改善的外部速率边界。未做真实平台测试时明确写 `NOT RUN`。
