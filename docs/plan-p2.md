你现在执行 DYbot 铃露系统更新计划 P2。

本轮目标：
升级现有牧场生产系统，使其支持铃露规则中的：
- 12小时喂食周期
- 动物生产周期
- 亲密度
- 连续准时喂食
- 断喂惩罚
- 精饲料
- 天气生产加成
- 牧场升级生产加速
- 动态牧场面板

不要执行 P3 或之后阶段。

====================
0. 工作原则
====================

严格遵守根目录 AGENTS.md 和 docs/codex-workflow.md。

优先低 Token 工作：

1. 先 git status --short。
2. 不重复扫描整个仓库。
3. 优先读取：
   - application/services.py
   - domain/economy.py
   - persistence/schema.py
   - presentation/messages.py
   - command_router.py
   - 与牧场相关的测试
4. 只有出现依赖关系时才读取其他文件。
5. 不修改与 P2 无关的功能。
6. 不顺手重构。
7. 已有函数能扩展就不要重新设计。
8. 所有 schema 修改必须 Alembic migration。
9. 不破坏现有玩家数据。
10. 不硬编码测试玩家或示例数据。

如果当前代码结构与计划中的文件路径略有不同，
以实际仓库结构为准，不要为了匹配计划移动文件。

====================
1. 先做现状检查
====================

确认当前实现：

- Animal / Ranch 数据结构
- feed 状态如何保存
- production 如何计算
- collect 如何结算
- ranch upgrade 如何影响生产
- inventory 如何增加产品
- 当前 animal production cycle
- 当前时间字段类型与 timezone 处理方式
- 当前测试覆盖范围

只输出必要发现。

不要写长篇架构分析。

====================
2. 牧场基础规则
====================

目标动物参数：

鸡：
- 占用 1 格
- 购买价格 50
- 生产鸡蛋
- 每批生产周期随机 3~5 小时

羊：
- 占用 2 格
- 购买价格 120
- 生产羊毛
- 每批生产周期随机 9~12 小时

牛：
- 占用 3 格
- 购买价格 300
- 生产牛奶
- 每批生产周期随机 6~10 小时

新购买动物：

feed_until = 当前时间 + 12小时

也就是说新动物默认带 12 小时饲料。

不要因为升级规则而破坏已有动物记录。

====================
3. 喂食系统
====================

普通：

/喂食

效果：

feed_until = 当前时间 + 12小时

如果项目当前是按动物分别保存 feed 状态，
优先延续现有模型。

如果是牧场级状态，也优先兼容现有模型。

不要为了形式强制大改 schema。

生产原则：

只有在有效喂食时间内产生生产进度。

断喂后：

生产暂停。

已经生产完成、尚未收取的产品：

不得腐坏
不得丢失

重新喂食后：

继续生产。

必须避免通过重复 /喂食 刷生产进度。

====================
4. 亲密度
====================

每种动物/动物生产组需要保存亲密度状态。

范围：

0~9

显示：

0：
无心或按现有 UI 规则显示

例如 9：

💗💗💗💗💗💗💗💗💗

准时补喂：

亲密度 +1

每一点亲密度：

最终生产数量 +2%

上限：

+18%

不要直接修改基础生产数量。

统一通过 production multiplier 计算。

====================
5. 连续准时喂食
====================

保存：

feed_streak

准时喂食：

feed_streak += 1

达到连续 3 次：

生产 multiplier ×1.10

断掉连续喂食：

feed_streak = 0

注意：

这里的 +10% 是乘法加成，
不要错误实现成 +10 个百分点产量。

====================
6. 断喂规则
====================

feed_until 到期后：

生产暂停。

如果超过：

feed_until + 10小时

仍未补喂：

亲密度清零
feed_streak 清零

必须保证：

这个惩罚是幂等的。

重复查看 /牧场 不应重复产生副作用。

优先在明确的状态结算点处理，
不要让 presentation 层修改游戏状态。

====================
7. 精饲料
====================

新增：

精饲料 inventory item。

购买：

/买精饲料 [数量]

单价：

10币 / 份

默认数量行为根据现有命令风格处理，
不要引入与现有 parser 不一致的特殊规则。

使用：

/喂食 精

效果：

- 消耗精饲料
- 当前喂食周期有效
- 当前生产周期产量 ×1.15
- 本次亲密度额外 +1

因此准时使用精饲料时：

普通准时：
+1

精饲料额外：
+1

总计：
+2

仍然受亲密度上限 9 限制。

需要保存当前 feed cycle 是否使用精饲料，
不能让玩家通过重复命令无限叠加。

同一个 feed cycle：

精饲料效果不能重复叠加。

====================
8. 天气系统
====================

每日天气：

☀️ 晴天
全生产 ×1.2

🌧️ 雨季
羊毛 ×1.5

🏜️ 旱季
鸡蛋 ×0.7

🥵 闷热
牛奶 ×0.8

🍃 微风
×1.0

🎊 丰收祭
全生产 ×1.5

丰收祭目标概率：

10%

天气每天 00:00 刷新。

要求：

同一天全服使用同一个天气结果。

不能：

每个玩家单独随机
每次 /牧场 随机
每次 /收取 随机

必须持久化或使用确定性日种子，
优先选择与当前项目架构最兼容且测试稳定的方式。

====================
9. 牧场等级生产加速
====================

现有 /牧场升级 功能必须保留。

每级：

+2 容量

并按照铃露规则实现：

全队生产速度提升 10%

注意：

生产速度提升 ≠ 产量 +10%。

例如：

基础周期 10小时

速度 +10%

应该通过统一的 duration/speed 计算函数得到缩短后的周期，
不要简单把产品数量乘 1.1。

如果当前代码已经定义升级倍率，
优先兼容现有实现并补充测试。

====================
10. 统一 multiplier
====================

不要把所有加成散落在 service 中。

建立或扩展 domain 层统一计算：

production multiplier

至少覆盖：

亲密度
连续喂食
精饲料
天气
未来 buff 扩展点

目标概念：

final_quantity =
base_quantity
× affection_multiplier
× streak_multiplier
× premium_feed_multiplier
× weather_multiplier
× future_buff_multiplier

最终整数如何取整：

先检查现有代码规则。

如果没有明确规则：

使用统一、可测试的规则，
并记录该决定。

不要不同动物使用不同取整逻辑。

====================
11. 生产周期随机
====================

随机周期必须：

- 在生产批次开始时确定
- 保存或可稳定重建
- 不允许每次查看面板重新随机

否则玩家不断执行 /牧场 会改变完成时间。

测试必须可以注入：

固定 RNG
或 seed

确保测试确定性。

====================
12. /收取
====================

/收取 必须：

1. 结算截止当前时间的有效生产
2. 只计算有饲料期间
3. 应用所有生产 multiplier
4. 增加 inventory
5. 更新下一生产周期
6. 返回收获结果

重复执行：

/收取
/收取

第二次不得重复获得同一批产品。

必须保证结算幂等。

====================
13. 牧场面板
====================

/牧场 输出向 ruihe.md 对齐。

目标结构：

—— 🏡 {玩家} 的牧场 ——
等级：Lv.X/9｜容量：X/X
{天气emoji} 今日天气：{天气名称}（{效果描述}）

鸡xN（✨闪光xN）（亲密度 💗...｜连喂+10%｜🌾✨精料中）
　▓░░░░░ 待收 X 鸡蛋｜约 X.Xh 后新一批
　⏰ 饲料还能撑 X.Xh

……

—— 库存 ——
……

指令：
/喂食｜/收取｜/行情｜/出售｜/买动物｜/回收｜/加工｜/铃露玩法

要求：

所有值来自真实状态。

进度条固定长度。

剩余时间统一格式。

无精饲料时不显示“精料中”。

未达到 streak buff 时不要错误显示“连喂+10%”。

不要在 presentation 层计算复杂业务规则。

====================
14. 数据迁移
====================

如果需要新增：

affection
feed_streak
premium_feed_active
production_cycle
weather
或其他字段

必须：

Alembic migration。

要求：

旧数据迁移后可继续使用。

默认值必须安全。

禁止：

DROP 玩家表
清空 inventory
重置 ranch
重置余额

====================
15. P2 定点测试
====================

先运行相关测试，不运行全套。

至少新增/更新测试：

test_new_animal_has_12h_feed

test_feed_extends_12h

test_unfed_animal_stops_production

test_products_do_not_spoil

test_affection_increases_on_timely_feed

test_affection_cap_9

test_affection_resets_after_10h_overdue

test_feed_streak_three

test_feed_streak_reset

test_premium_feed_consumption

test_premium_feed_affection_bonus

test_premium_feed_not_stackable_same_cycle

test_weather_global_same_day

test_weather_multiplier

test_ranch_upgrade_speed

test_collect_idempotent

test_cycle_random_range_chicken

test_cycle_random_range_sheep

test_cycle_random_range_cow

test_ranch_panel

测试必须：

- 固定时间
- 固定 RNG
- 不依赖真实日期
- 不依赖外部服务
- 不使用 sleep

====================
16. P2 验收
====================

完成后：

先执行 P2 定点测试。

通过后：

运行与 ranch/economy/inventory 直接相关的回归测试。

不要因为 P2 完成自动运行所有慢测试。

最终报告控制在简短范围：

P2 COMPLETE

Changed:
- 文件列表

Migration:
- migration 名称
- 新字段/表

Implemented:
- 功能列表

Tests:
- passed / failed

Compatibility:
- 是否保留旧数据

Remaining:
- 真正未解决的问题

不要重新解释整个项目。
不要输出大段代码 diff。