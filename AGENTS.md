# DYbot / 瑞禾 Codex 最小工作约定

- 先读 `docs/ruihe-progress.md`（若存在）、`git diff --stat`；本轮只看所需的 `docs/ruihe-codex-plan.md` 小节和相关源码，避免每轮读取全仓。
- 首次接手阶段或测试策略变化时，按需阅读 `docs/codex-workflow.md` 的对应章节；后续依进度文件续做，避免反复通读。产品规则以 `docs/reference/ruihe.md` 为依据。冲突或缺失写 `docs/ruihe-open-questions.md`，不得暗自猜规则。
- 一轮只交付一个可验收的垂直切片，保留原有 Core + Browser Worker、老指令、Alembic 历史、账本和数据；禁止无关重构。
- 测试按风险分层：改动后定点/相关测试，阶段末集中全套；涉及 Core/Worker、迁移、经济、真实 Reply 再做对应集成验收。测试未跑不得报 PASS。
- 任何玩家指令的回复（包括报错和长面板）必须指向**该条玩家原消息**的原生 Reply；未知平台协议不得编造字段；主动公告不可误引玩家。
- 默认输出紧凑报告：变更路径、实测结果、阻塞、下一步；勿贴完整代码、长日志或重复计划。未经授权不得访问正式数据库、账号或发送正式群消息。
