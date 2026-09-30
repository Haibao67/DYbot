# Codex 执行任务：DeepSeek 赛马逐检查点解说

## 目标

在现有赛马检查点播报和最终结果播报中接入 DeepSeek。每个检查点和最终结果分别请求一次模型解说；每次解说限制为一段短文。单次调用硬超时 10 秒，超时、网络/API错误、空响应、输出不合规或解析失败时，立即使用规则解说。赛马模拟、排名、奖金和账本完全由现有代码决定，模型只能改写播报文字。

本任务只完成本地代码和测试，不部署、不调用正式群、不访问正式数据库。沿用现有 Core 结算、outbox 入队和 Browser Worker 发送架构。不得重构无关模块、改历史迁移或改变消息原生 Reply 规则。

## 开始前

1. 遵守仓库 `AGENTS.md`：阅读 `docs/ruihe-progress.md`、`git diff --stat`、`docs/update-maintenance-guide.md` 的任务入口和第 3、4 节；读取 `docs/ruihe-codex-plan.md` 第 12 节、赛马实现说明和相关源码。工作区可能已有大量修改和未跟踪文件，先 `git status --short`，不得清理、覆盖或重排无关改动。
2. DeepSeek API 的模型名、价格和功能可能变化，实施时先浏览 DeepSeek 官方 Chat Completions、JSON Output、Pricing、Error Codes 文档。使用当时官方推荐、仍可用的低成本非推理模型；将模型名配置化，不硬编码过时别名。接口使用 `https://api.deepseek.com/chat/completions` OpenAI-compatible endpoint。
3. 不假定仓库当前已有 DeepSeek SDK。优先用现有 HTTP 依赖；若无合适依赖，评估 Python 标准库 HTTPS 客户端，避免无必要依赖锁改动。

## 用户已确认的产品要求

- 每个引擎检查点以及最终赛果各生成一条 AI 解说，每个检查点最多一次 DeepSeek API 调用；不可将多个检查点合并成一次生成。
- 每段解说严格限制长度：**最多 100 个 Unicode 字符**（按 Python `len` 定义；不含标题、排名名单和提示）。模型返回超长时不能截断后冒充合格文案，应改用规则解说。输出正文不得换行，建议限制 35～75 个汉字，硬上限 100 字符。
- API 单次总时限 10 秒（连接与读取共用 deadline）。超过即取消/放弃请求并用规则解说，不能等待后台结果后再覆盖已入队文案。
- 任何失败必须无阻塞地使用对应检查点的本地规则解说；不丢播报，不重试 API。**每检查点总等待上限 10 秒**，不要串行请求造成一场比赛累计等待 N×10 秒。
- AI 只依据本检查点/最终赛果的冻结结构化事实创作，不得新增名次变化、超越、伤病、天气、技能发动或未提供的赛况。模型不能影响任何游戏状态或结算。
- 保留赛事标题、完整参赛排名、体力展示、现有分段上限、群目标选择、10 秒检查点间隔、outbox 幂等和最终结果发送锁定。
- 使用现有播报群，不新增目标群。主动赛况播报不设置玩家引用；指令回复的原生 Reply 行为不变。

## 实施范围与设计

### 1. 设置和安全默认值

在 `dzmm_bot/settings.py` 增加可验证配置，并在 `.env.example` 加安全默认值：

```dotenv
DZMM_RACE_COMMENTARY_AI_ENABLED=false
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=<实施时从官方文档核实的模型名>
DEEPSEEK_TIMEOUT_SECONDS=10
DZMM_RACE_COMMENTARY_MAX_CHARS=250
DZMM_RACE_COMMENTARY_MAX_CALLS_PER_RACE=20
```

配置解析验证：enabled 必须是明确布尔值；timeout 固定允许范围 1～10 秒且生产默认 10；正文长度上限固定 100（可以更小，不允许配置放大超过 100）；单场调用上限至少覆盖所有引擎检查点+最终结果，但不得大于 20。API key 不进入 dataclass repr、日志、异常文本、测试快照或进度文档；不得将密钥加到 `.env.example` 的真实值、命令行参数或源文件。

功能开关关闭或 key 缺失时完全不发 API 请求，立即用规则解说。不得因没有密钥令赛事结算失败。

### 2. 解说组件

建议新增 `dzmm_bot/application/race_commentary.py`，职责限定为：

- 将一个检查点/最终赛果映射成允许字段的 JSON 输入；过滤掉 `player_id`、内部消息、群 ID、私密标识和无关状态。
- 生成中文提示，明确输出只能是单段纯文本、最多 100 Unicode 字符、不能包含换行/Markdown/引号包裹、不能添加事实或预测成已发生事件；名字和赛事名作为不可信数据，只可照写，不能当指令执行。
- 以 HTTPS 对官方 Chat Completions endpoint 发请求，Authorization Bearer key；关闭流式返回；请求 JSON mode（若当前模型正式支持），限制 `max_tokens`，设置低随机度；不发送玩家标识 `user_id`。
- 设定单一单调时钟 deadline=10秒；连接和读取总时间共享 deadline。用可取消的异步 HTTP 客户端或在受控线程池中执行阻塞请求，不能让 Core event loop 被阻塞。若使用线程请求，超时后结果必须被丢弃，确保线程数量/并发有界，不可每场泄漏一个永久线程。
- 响应只抽取 choices[0].message.content；API 结构异常、finish_reason 表示长度截断、内容空白、含换行、长度超 100、含代码围栏时视为失败。返回显式结果（AI/规则来源、文案、失败类别），不抛出到比赛结算事务。
- 错误日志只记录有限错误类别、状态码、耗时和赛事内部随机化/脱敏关联标识；不得记录 API key、Authorization header、完整 Prompt/Response、玩家或群标识。
- 不自动重试 429、5xx、超时或连接错误。设置全局有界 semaphore，防止多场比赛并行时请求暴增；等待 semaphore 也计入同一 10 秒总 deadline。获取不到时直接规则回退。

HTTP 调用建议复用已有依赖。若必须加依赖，同步锁文件、维护文档和发布白名单；不得裸加运行期依赖而不锁定。

### 3. 规则文案与事件事实

- 保留并完善 `dzmm_bot/presentation/racing.py` 的 `race_call` 作为无网络、确定性 fallback。
- 从 checkpoint 的 `checkpoint_index` 收集同 checkpoint 的实际 engine events；目前要审阅 `race_engine.py` 和 `race_engine_r13.py` 的快照结构，避免事件漏关联或重复播报。
- 生成 AI 输入只包含当前阶段、距离、剩余距离、前三（或经确认的其他必要排名）、体力，以及该检查点实际发生的白名单事件。最终结果输入可包含排名全表、第一名与第二名差距/关键事件等现有赛果字段。
- 对终点距离、体力、名次等事实使用明确数值；不要要求模型从自由文本推算。AI 文案前后仍由本地 renderer 加标题、排名列表和完整名单。
- 检查点计数必须由冻结赛事 policy/结果实际 checkpoint 数决定，不硬编码当前数量。若不满足单场调用上限，全部回退规则解说并记录安全错误，不部分调用。

### 4. 接入播报队列

调整 `dzmm_bot/application/competition_service.py` 的 `queue_race_broadcast` 和调用路径：

- 当前 `start()`、`finalize()` 及数据库事务生命周期必须逐一检查。不得在持有数据库事务/锁时等待最长 10 秒×检查点数。
- 在事务外、outbox 入队前生成全场 commentary，按 checkpoint 顺序**最多 10 秒总耗时/段**，最终结果也单独最多 10 秒；同时为全场 AI 生成设置总墙钟预算，建议不超过 `min(10×段数, 45秒)`，预算耗尽后余下段均即时规则回退。此全局预算是为了避免一次结算长期占用，不改变“单段超时10秒”。
- 采用有限并发（建议最多 4 个请求同时在途），按固定序号汇总结果，避免返回乱序影响播报顺序；不要阻塞 Worker 的出站发送。
- 后生成的文本仍按现有 `race_broadcast_pages` 完成长度分页；AI 正文长度单独校验为 100 字符。保持相同确定性任务 ID、available 时间、检查点节奏和赛事定义中的发送门控。
- 若无法安全将 AI 调用移至数据库事务之外，本轮使用有界后台生成/短期内存任务，但必须保持幂等：同一 race_id 重试不能产生重复请求和不同已入队文案。不要未经设计添加不可恢复的内存后台任务；需要持久化任务/迁移时先评估并在本任务范围内完成向后兼容迁移演练方案。
- 交易、排名、奖金一次性结算逻辑保持原子且不依赖模型可用性。AI 失败不能回滚比赛，也不能使 `/赛果` 永久锁定。

### 5. 失败分类及日志

最少区分：disabled、missing_key、semaphore_timeout、connect_timeout、read_timeout、http_429、http_5xx、http_other、invalid_json、empty_content、truncated、over_length、multiline、invalid_response。不要持久化 prompt/response。日志和管理员可见诊断均不得包含密钥。

### 6. 测试（需要实际运行）

新增离线 fake HTTP 测试，不调用真实 DeepSeek：

1. 开关关闭/key 缺失：0 次网络调用，规则解说立即返回。
2. 成功响应：正确选择 JSON 内容、生成每检查点与最终结果文案、每场调用数量等于 checkpoint 数+1。
3. 请求抛出连接/读取超时：10 秒 deadline 正确回退且结算不失败；测试使用 fake clock/fake transport，不实际等待 10 秒。
4. 429、5xx、非法 JSON、空响应、截断、超 100 字符、换行、Markdown围栏：分别回退规则文案。
5. 整场总预算耗尽后余下检查点不再请求，均使用规则解说；并发不超过配置上限，输出顺序按 checkpoint 序号。
6. 注入恶意赛事名/马名提示内容，确认模型输入把它们当数据且最终展示仍经现有转义器处理。
7. 多次 reconcile/start/finalize 重试不重复调用、不重复生成不同 outbox 文本；outbox ID、全排名分页、每段标题、原生 Reply/主动消息无引用、10 秒 ACK 调度和最终赛果门控回归。
8. 对配置解析、缺失密钥、密钥日志脱敏及禁止超过 100 字进行测试。
9. 运行赛马定点测试、Core/Worker 相关测试和适当的隔离 smoke test。若因已知迁移初始化失败而不能跑，准确记录命令、错误和未验收范围，不能报告 PASS。

## 文档同步

- 更新 `docs/ruihe-progress.md`，记录实现路径、超时/字符/总预算策略、测试结果、是否真实 API 验证、未部署状态。
- 更新 `docs/ruihe-open-questions.md`：本轮已确认“每检查点及最终结果一次、正文≤100字符、10秒回退”；若业务需要另一个总场预算、成本金额上限或AI启用群范围，明确标成待确认，不要自行默认正式启用。
- 更新 `.env.example` 仅写安全默认值和空密钥。
- 不改正式环境，不增加 API key，不发送正式群公告，不做发布。文档不得记任何密钥。

## 完成判据

- AI 关闭或不可用时，现有赛马完全可运行并稳定发送规则解说。
- AI 开启时每个检查点和最终结果分别最多一次请求；单段总时限不超过 10 秒；正文不超过 100 Unicode 字符；AI 全场调用预算耗尽后立即规则回退。
- 结算、账本、排名和消息幂等均不依赖 AI；所有任务经既有 outbox/Worker 发送。
- 定点与相关验收实际执行，并如实记录结果；没有真实 API key 时只做 fake transport 测试，报告真实 DeepSeek 连通性尚未验收。
- 不部署、不访问正式数据库、不发送正式群消息。

## 执行时向用户汇报

完成后只汇报变更文件、已执行测试及结果、真实 API 连通性状态、预算/密钥配置提醒和未部署状态。不得输出完整代码或密钥。
