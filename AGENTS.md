# DYbot / 铃露 Codex 最小工作约定

- 先读 `docs/ruihe-progress.md`（若存在）、`git diff --stat`；本轮只看所需的 `docs/ruihe-codex-plan.md` 小节和相关源码，避免每轮读取全仓。
- 每次涉及内容更新、功能修改、发布、服务器更新、运维或故障恢复，先读 `docs/update-maintenance-guide.md` 的任务入口与相关章节，再执行；部署现状以该文档最近验证记录及本轮只读检查为准。按文档完成备份、迁移演练、发布和验收，更新后补记版本与结果。仅修改代码不等于获准上线；已有明确部署授权时继续执行，不重复索要授权。
- 使用自动服务器更新脚本前，阅读 `docs/server-update-script-usage.md`；脚本为 `deploy/Update-Server.ps1`。迁移或依赖锁变化时，遵循维护手册的人工演练/依赖更新流程，不绕过脚本的保护性停止。
- 发布脚本先在可读取既有 `known_hosts` 的受信 Windows 会话中核验服务器身份；不可关闭 SSH 主机校验。脏工作区发布必须提供或由脚本自动定位与服务器当前发布 SHA256 匹配的基准包，逐文件审阅新增、修改和删除后再输入脚本要求的复核短码。脚本非零退出时先只读核对实际发布标记和 Core/Worker 状态，不凭退出码猜测上线或回滚。
- 修改更新公告、manifest、玩家引导、今日/待办或世界动态时，先阅读 `docs/update-and-player-information.md`；未确认规则的世界动态配置保持关闭。
- 每次服务器更新后必须启动 Bot：先启动并验证 Core，再启动 Worker；核验两服务 active、Core 健康、Worker connected 且心跳未过期。未达到这些条件不得报告更新完成；继续排障或恢复可运行旧版，无法安全恢复时明确报告停机与阻塞。用户要求保持停止时除外。不得把进程启动等同于平台回复成功，新增 failed/uncertain 必须单独说明。
- 首次接手阶段或测试策略变化时，按需阅读 `docs/codex-workflow.md` 的对应章节；后续依进度文件续做，避免反复通读。产品规则以 `docs/reference/ruihe.md` 为依据。冲突或缺失写 `docs/ruihe-open-questions.md`，不得暗自猜规则。
- 一轮只交付一个可验收的垂直切片，保留原有 Core + Browser Worker、老指令、Alembic 历史、账本和数据；禁止无关重构。
- 测试按风险分层：改动后定点/相关测试，阶段末集中全套；涉及 Core/Worker、迁移、经济、真实 Reply 再做对应集成验收。测试未跑不得报 PASS。
- 任何玩家指令的回复（包括报错和长面板）必须指向**该条玩家原消息**的原生 Reply；未知平台协议不得编造字段；主动公告不可误引玩家。
- 默认输出紧凑报告：变更路径、实测结果、阻塞、下一步；勿贴完整代码、长日志或重复计划。未经授权不得访问正式数据库、账号或发送正式群消息。

- 更新后仅因平台登录/身份认证失败时保留新版代码和发布标记，禁止自动或手动回退代码；走服务器登录界面恢复账号。未恢复Worker连接前不得报告更新完成。其他代码/迁移故障仍按维护手册处理。

## 优化后的服务器更新流程

1. 先读 `docs/update-maintenance-guide.md` 和 `docs/server-update-script-usage.md`，核对 Git 状态、发布范围及本次部署授权。不得将历史发布包路径当作当前基准。
2. 在受信 Windows 会话运行 `deploy/Update-Server.ps1`。干净工作区发布已提交 HEAD；已授权的工作区发布使用 `powershell -NoProfile -File .\deploy\Update-Server.ps1 -AllowDirtyWorktree -Deploy`。`-Deploy` 仅在用户已明确授权本次上线时使用，免去重复的 DEPLOY 确认，不免除任何校验。
3. 脏工作区由脚本读取服务器发布标记，自动定位 `data/deploy-<当前发布ID>.tar.gz` 并核验 SHA256；缺失或不匹配时停止，必要时用 `-BaselinePackage` 指定经核验的包。逐文件审阅差异后输入 `REVIEW <候选包SHA256前12位>`，禁止自动代填复核短码。
4. 需要仅验证候选时使用 `-ValidateOnly`，不可同时使用 `-Deploy`。此模式会上传并创建候选产物，但不切换代码或停服务。脚本先完成本地测试、服务器候选测试与隔离 Core/Worker smoke；任何失败先查对应日志，不绕过验证。
5. 删除文件、依赖或迁移文件变化必须转维护手册的人工流程，完成依赖更新或数据库副本迁移演练，不绕过脚本保护。切换前复核服务器基准未变，通过部署锁串行执行备份、切换和验收；锁冲突或基准变化后重新核查，不强制解除保护。
6. 保留代码及数据库备份，先停 Worker 再停 Core；更新后先启动并验证 Core，再启动 Worker。Worker 必须 connected 且心跳晚于本次启动、未过期；默认等待60秒，必要时使用 `-WorkerTimeoutSeconds`（30～240）。对账新增差额、新增 failed 或健康门禁失败必须排查；新增 uncertain 单独报告并核查发送结果，不自动重发。
7. 日志、完整本地测试输出、包差异 JSON 和结果回执位于 `data/deployment-logs/`；服务器候选日志位于 `/srv/dzmm/releases/<发布ID>/`。非零退出先读取实际发布标记及服务健康，不能仅依据回执或退出码断定上线/回滚。仅登录失败时保留新版并通过服务器登录界面恢复；其他故障按维护手册恢复。
8. 验收后将发布ID、SHA256、备份、测试、服务/心跳、对账及 failed/uncertain 结果记入维护手册与进度文件。真实群回复验证及公告发送须有对应授权。脚本改动按需执行 `deploy/Test-UpdateServer.ps1` 和发布包定点测试；离线通过不等于正式服务器验收通过。
