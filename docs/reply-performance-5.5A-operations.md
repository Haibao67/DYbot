# 5.5A 回复耗时观测操作说明

5.5A 已于 2026-09-27 发布到服务器，发布 ID `5a4e915be7e6-20260927-122425`，数据库 head 为 `0011_outbound_timing`。查询接口为 `GET /admin/performance?sample_limit=200`，使用现有 `X-Admin-Token`。`sample_limit` 为 1–1000，默认 200。请在有权限的环境中调用；不要把令牌写入日志或文档。

## 指标口径

- `created` 是 Core 将回复写入 outbox 的时间。玩家发出消息至 Core 入队之前的耗时不在数据库分位数内；Worker 的 `inbound_core_ack` 日志额外记录从接收事件后发往 Core 至 ACK 的单次请求耗时。
- `last_claimed_at`、`last_send_started_at`、`last_result_at` 是任务最近一次领取、开始发送和结果入库时间，均为 UTC Unix 秒。重试保留最初 `created`，更新最近一次尝试的时间点。旧记录为 null。
- `queue_wait`、`send_attempt` 与 `sent_end_to_end` / `simulated_end_to_end` 分别计算相应时间差。最近至多 N 条有结果记录参与样本；缺值、负值和非有限数计入 `invalid_samples`，不参与分位数。分位数采用排序后的最近秩观测值，单位秒；没有有效样本时返回 null。
- `pending_count` 包括 pending、leased、sending；`oldest_pending_seconds` 自最早入队时间算起。`uncertain` 单列，不算成功。
- `channels` 按群引用回复、私聊回复、主动广播列出各状态数。`rate_limited_current` 只计目前最后错误为 `rate_limited` 的任务；成功重试后的历史限流可从 `outbound_delivery_result` 脱敏日志审计。`failed_count`、`uncertain_count` 是当前状态数。
- `platform_ack` 日志计实际 Socket/HTTP 请求至平台 ACK 或传输异常的单调时钟耗时。Socket 新版分段字段分别是 `room_lock_wait_ms`、`limiter_wait_ms`、`socket_lock_wait_ms`、`socket_call_ms`；对 Socket 消息，`elapsed_ms` 与 `socket_call_ms` 一致，仅计获得共享 Socket 锁后至 ACK/异常，不含前述等待。旧版日志的 `elapsed_ms` 从共享 Socket 锁获取前开始，因此包含 `socket_lock_wait`，不能与新版本纯 `socket_call_ms` 混作同一口径。长回复各子发送带 `split_part`，数据库仍只记一个 outbox 任务。`outbound_worker_send` 包括 Worker 等待节流、Socket锁、平台调用与回退的整段耗时，不宜当作纯平台延迟。

接口只查询状态、时间和错误类别，不返回玩家消息、Token、Cookie 或房间 ID。Worker `inbound_core_ack` 记录单次 Core 请求 ACK 总耗时。新增 `inbound_latency` 为 Core 进程内有界直方图，分为 `core_lock_wait`（等待全局异步锁）、`core_inbound`（Store 事务和指令处理）、`core_invite`（邀请入站处理）及 `core_inbound_total`（Core 路由总耗时）；返回样本数、均值、最大值和桶边界分位上限，不保存逐条样本。它随 Core 重启清零，且只覆盖通过认证、完成路由的请求；Worker 日志才包含 Worker 到 Core 网络往返。`core_inbound` 不含锁等待，避免把锁竞争和业务处理混成一个指标。

生产采样应在同一时间窗记录 `inbound_core_ack.event_processing_ms`、`inbound_core_ack.elapsed_ms`、`inbound_latency`、`platform_ack` 的四种分段耗时、`outbound_worker_send.elapsed_ms` 以及 outbox 队列等待/发送 P50/P95。`event_processing_ms` 从 Gateway 收到 Socket 事件、完成必要规范化/昵称或邀请查询至发出 Core 请求前；`inbound_core_ack.elapsed_ms` 是 Worker 到 Core 的请求往返并包含 Core 路由工作；新版 `platform_ack.socket_call_ms` 才是纯 Socket 调用/ACK 等待，其他字段分别显示各锁与限速等待；`outbound_worker_send` 是从 Core 标记 begin 到发送流程结束，包含节流、锁等待、平台调用与可能的回退。各自口径不可简单相加作为严格端到端值。发布后重启 Core 会清空 Core 内存直方图，应记录采样起止时间与累计样本数；不能把本地假任务基线当成生产结果。

## 发布后首次生产读数

2026-09-27 发布后首次查询：`sample_limit=200`、`recent_results=0`、`pending_count=0`、`oldest_pending_seconds=null`、`rate_limited_current=0`、`failed_count=15`、`uncertain_count=0`。所有耗时指标样本数为 0、P50/P95 为 null。15 条 failed 均为发布前历史记录；旧 outbox 的新时间字段保持 null。待自然产生新回复后，才有可用于 5.5B 的生产耗时基线。本轮没有发送正式群测试消息。

## 本地模拟基线

固定时钟假任务的测试覆盖 10、100、500 条；每条模拟 `created=1000`、`last_claimed_at=1001`、`last_send_started_at=1002`、`last_result_at=1004`，接口样本上限 20。因此队列等待 P50/P95 为 1/1 秒，发送 P50/P95 为 2/2 秒；各规模返回的成功样本数均为 20（10 条时为 10），限流、失败、不确定均为 0。这是确定性测试数据，不代表真实平台速度。

## 2026-09-30 生产只读采样

UTC 04:54 左右，通过已核验 SSH 只读查询正式环境，发布标记 `5a4e915be7e6-20260929-224819`。Core/Worker/PostgreSQL 均 active，Core 返回 `ok/live`，Worker `connected` 且心跳新鲜，Core/Worker 重启计数均为 0。Worker 限速器当前间隔 0.2 秒、限流事件 0；这是服务器当前心跳状态，不能据此推断平台允许的长期安全速率。

`/admin/performance` 最近 200 条结果：队列等待 P50/P95 `1.487/12.983s`，发送尝试 `1.146/3.898s`，sent端到端 `2.881/13.945s`；pending 0、failed 584、uncertain 3。Core 内存入站聚合累计 4,211 样本：锁等待最大 `0.1ms`、P95桶上界 `1ms`；普通入站处理均值 `16.6ms`、最大 `220.5ms`、P50/P95桶上界 `20/50ms`；Core路由总耗时均值 `16.6ms`、最大 `220.6ms`、P50/P95桶上界 `20/50ms`。本次观察没有邀请入站样本。

Worker journal 最近30分钟未找到匹配的结构化计时事件，因此没有新鲜的 Gateway→Core 往返、平台 ACK 或节流后发送耗时可供汇总；接口中的 4,211 入站样本是 Core 当前进程累计值，不是该30分钟窗口。failed/uncertain为当前累计状态数，无法仅凭本次读数判定哪些是新发生。全程未重启服务、未发送测试消息。

### 追加连续快照与近窗日志

UTC 约 04:57 与 05:00 连续两次只读采样，间隔约2分钟。两次均为服务 active、Core/Worker 重启数0、Worker心跳新鲜、当前间隔0.2秒、限流0、pending0；failed维持584、uncertain维持3。Core入站累计样本由4,223增至4,226。最近200条出站结果的队列P50约1.44秒/P95 12.983秒，发送尝试P50 1.132秒/P95 3.812秒，sent端到端P50 2.854秒/P95 13.945秒，读数稳定。

UTC约04:57读取近10分钟Worker脱敏日志汇总：68次`inbound_core_ack`全部ACK，Worker→Core请求往返P50/P95/max为17.8/102.9/148.8ms；`event_processing_ms`经0.1ms显示精度后全为0.0ms。35次`platform_ack`中29次accepted、6次rejected_or_invalid_ack，Socket请求耗时P50/P95/max为1,333.1/5,748.5/6,644.2ms；对应35次`outbound_worker_send`为29 sent、6 failed，耗时读数相同至0.1ms。近窗有6条`outbound_delivery_result`，但只按类别计数、未展开错误正文。平台ACK计时在限速等待之后开始，因此该长尾来自平台调用/ACK阶段；总端到端最近秩P95约13.9秒同时包含此前队列等待，不能直接视为同一批次的相加分解。6次拒绝与performance当前failed=584不变并存，说明计数快照不足以作逐项新旧归因；未发现近窗新的不确定发送。未修改配置、未重启、未发送测试消息。

### 2026-09-30 出站 ACK 长尾与拒绝原因

UTC约05:05只读汇总最近30分钟Worker日志：`group_socket_reply` 102次accepted，Socket ACK耗时P50/P95/max为1,298.1/4,429.6/7,531.9ms；9次rejected_or_invalid_ack耗时740.0/3,123.6/3,123.6ms；1次transport_unknown达到15,003.7ms（Socket调用15秒超时）。对应Worker结果为102 sent、9 failed、1 uncertain。Worker总体发送时长与平台调用耗时几乎一致至0.2ms，说明该窗口主要时间花在Socket调用/等待ACK，不是Core结果提交；平台计时已排除限速器等待。

近30分钟9条`outbound_delivery_result`的脱敏错误分类均为`duplicate_content`；`/admin/status`按创建时间最新20条failed任务的failure history也全部为平台“重复内容”拒绝。另有1条`transport_unknown`。当前performance快照failed=584、uncertain=3、rate_limited_current=0；其中uncertain为累计当前状态，日志里的单次事件不能单独证明该状态净增加。没有证据表明这批拒绝由限流或超长消息导致。

根因判断：当前主要功能性拒绝是平台重复内容规则；同一群/同一时段的文本重合或重复投递会触发平台拒绝，任务会终结为failed而不会自动重发。主要耗时长尾是平台Socket ACK响应慢，偶发15秒传输超时进入uncertain。日志只存脱敏长度/错误类别，因此无法在不读取玩家消息正文的前提下验证具体内容重合场景。没有改代码、配置或重发任务。

### Socket ACK 分段观测切片（本地未部署）

本地 `platform_ack` 已拆出 `room_lock_wait_ms`、`limiter_wait_ms`、`socket_lock_wait_ms` 和纯 `socket_call_ms`；`elapsed_ms` 对 Socket 改为只表示 `socket_call_ms`。覆盖原消息发送及超长分段发送，保持串行锁、15秒调用超时、Reply构造与错误状态不变。新字段仅在部署后开始产生；旧日志仍按包含 Socket 锁等待的历史口径解释。定点测试及静态检查结果记于开发进度。
