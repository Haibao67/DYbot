# P8-5 技能执行器交付记录

状态：本地实现，未部署；按AGENTS每轮一个垂直切片，本轮仅实现P8-5，P8-6～10顺序待办。缺正式配置的功能继续关闭。

## 实现

- `TriggerEvaluator`：阶段、排名、体力、距离、场地、状态、跑法、堵塞、超车、并排、领先差距以及显式机制flags。未知条件拒绝；缺失必要flag不假装满足。连续两个clean checkpoint供稳态呼吸使用。
- `WisdomTriggerResolver`：仅使用调用方注入的正式公式，记录概率、roll与结果；未配置时不判定、不伪造失败，运行状态记录门控原因。
- `SkillEffectExecutor`：统一处理速度、加速度、体力消耗/恢复、力量、智慧、毅力及路线/超车/配速等机制Modifier；复合效果全部验证后才修改状态。通用路线机制的实际消费待P8-6。
- `ModifierStore`：SET→ADD→MULTIPLY固定顺序；STACK、REFRESH_DURATION、KEEP_STRONGEST、REPLACE、UNIQUE；按阶段和checkpoint到期。纯Presentation不参与计算。
- `SkillRuntime`：PASSIVE初始化或固定条件满足后注册，CONDITIONAL条件触发，WISDOM_TRIGGER公式判定后执行；一次/每阶段/冷却/多次策略、最大次数与失败记录，同checkpoint不重复尝试。
- RaceEngine通过可选 `skill_runtime` 注册Hook，消费速度/加速/消耗/体力效率及力量/智慧/毅力修正，无具体技能名分支。比赛快照增加技能尝试/成功/失败/冷却等统计，直播支持智慧发动失败文案。

## 技能库

从用户提供V1文档生成 `skill_catalog_v1.json`，保存48项名称、类型、标签、Lv1/Lv5/Lv10原文、随机池资格和来源规则。C16/W14不进入随机池，46项保留为资格目录；这不是46项都已可执行的声明。

明确规则：中间等级两段线性插值；随机等级权重Lv1→5为30/27/22/14/7。配置常量独立保存，不因读到配置自动给玩家补技能或启用继承。

首批12项：P08/P13/C01/C06/C07/C14/W01/W05/W06/W07/W10/W15。

可直接装配的执行定义4项：强健心肺、破闸、稳态呼吸、末脚爆发；全部仍需显式Registry导入和引擎配置。8项待配置：领放节律需前半程映射；弯道突进需发动次数策略；6项智慧技能需概率、发动次数或路线/安全线/代价等具体规则。`FIRST_BATCH_PENDING` 列出门控，不猜测默认值。

## 装配接口

由正式配置装配层创建 `SkillRuntime(registry, WisdomTriggerResolver(approved_formula))`，再传入 `RaceEngine(policy, skill_runtime=runtime)`。无公式时智慧技能保持关闭；无模拟policy时整场比赛拒绝启动。对外不新增比赛命令，不发奖励，不修改财富榜或历史马。

`ModifierStore.apply(state, mechanic, base, phase)` 给P8-6消费路线、超车、堵塞和配速修正；所需机制数据放在 `HorseRaceState.mechanic_flags`。不将未实现的路线效果宣传为已生效。

## 检查与限制

无新迁移；复用未部署0021定义与结果结构。本轮仅静态编译、模块导入、差异格式检查；未新增/运行功能测试、未迁移演练、未真实比赛验证，不能报告测试PASS。正式数值、P8-6机制和对应定点验证完成前不得启用真实比赛。

下一切片：P8-6 跑法/体力/位置/堵塞/超车；仍需比赛数值公式，保留配置门控。
