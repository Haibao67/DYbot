# 5.5C 本地调度实现与验证

状态：2026-09-27 已部署；未做真实平台测试发送。沿用 5.5B 的生产默认全局 Socket 间隔 2 秒和私聊 5 秒；平台速率上限仍决定实际吞吐。

## 调度与配置

- 各房间只比较按 `created,id` 排序的队首；`leased`、`sending`、`uncertain` 以及尚未到 `available` 的队首挡住该房间后续任务。
- 可领队首中，原生引用回复优先。连续领取 8 条回复后，若有公告队首，则给公告一次机会。相同类别按房间上次领取时间轮转。
- PostgreSQL 通过 `world` 行锁、SQLite 通过 `BEGIN IMMEDIATE` 串行化领取事务；领取更新附带 `pending` 状态条件。查询只取单个候选，不在 Python 加载全部 outbox。
- `DZMM_OUTBOUND_INFLIGHT_LIMIT` 范围 1–4，默认 1。真实 Browser Gateway 的 Socket 锁仍为单发送者，实际领取容量保持 1；模拟网关可验证较高上限。不要凭此配置宣称平台并发已开放。
- 新迁移 `0012_outbound_scheduler` 给房间加 `last_dispatched_at`，给 outbox 加 `(room,status,created,id)` 索引；旧消息、余额、库存、账本和广播记录不重写。

## 固定时钟假负载

每组先放入 N 个不同房间公告，再放入 10 条不同房间玩家回复；所有消息均可领取。每次模拟发送占 2 秒。旧方案按全局 `created,id` 领取；新方案调用真实 `Store.claim`，每 8 条回复给公告一次机会。单位为秒；数据库领取耗时为本机 SQLite 实测毫秒，不代表服务器 PostgreSQL。

| 公告房间 | 旧回复等待 P50/P95 | 新回复等待 P50/P95 | 新领取查询 P50/P95 |
| ---: | ---: | ---: | ---: |
| 10 | 39 / 47 | 19 / 29 | 11.59 / 21.43 ms |
| 100 | 219 / 227 | 19 / 29 | 8.07 / 12.05 ms |
| 500 | 1019 / 1027 | 19 / 29 | 8.26 / 12.23 ms |

这些数值只说明公告积压时的领取顺序改善。真实平台测试消息 ACK 与生产回复 P95：**NOT RUN**。本地无 PostgreSQL 测试库；服务器上已对生产库副本完成迁移、重复迁移、全部旧字段摘要和 PostgreSQL 并发领取校验，均通过。正式迁移时复用同一数据保持检查器并通过。生产 Worker 心跳中限流事件为 0，不能据此推断高峰吞吐。

## 运行与回退

本地定点、根目录与 `tests/` 全套、隔离 `smoke_test.py` 通过；服务器候选根目录 108 项、`tests/` 85 项及隔离冒烟通过。真实 Browser Gateway 保持单发送者，不需调整现有速率配置。发布 `5a4e915be7e6-20260927-164155`，SHA256 `b8add8220098e763e7a2910d48ddbd3008dc3ad171c1708d1d60c4cde6004e5f`；备份 `/var/backups/dzmm/5a4e915be7e6-20260927-164155`。Core/Worker/PostgreSQL active，Core live 健康，Worker connected、心跳新鲜，重启计数 0，资产对账差额 0，历史 failed 15、uncertain 0。若实际顺序或不确定消息异常，先保持/恢复 `DZMM_OUTBOUND_INFLIGHT_LIMIT=1`，再按维护手册回退兼容代码；不得自动重放 uncertain。
