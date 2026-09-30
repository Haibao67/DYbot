"""Compact affinity, skills and structured race-event rendering."""
from .formatters import name_escape

AFFINITY_NAMES = {'short': '短距', 'mile': '英里', 'medium': '中距', 'long': '长距',
    'turf': '草地', 'dirt': '泥地', 'front': '逃', 'stalker': '先行', 'mid': '差', 'closer': '追'}
TYPE_NAMES = {'PASSIVE': '被动', 'CONDITIONAL': '条件', 'WISDOM_TRIGGER': '智慧'}
PHASE_NAMES = {'START': '起步', 'OPENING_POSITION': '前段抢位', 'MID_RACE': '中盘巡航',
    'CORNER_BATTLE': '弯道争夺', 'FINAL_CORNER': '最终弯道', 'FINAL_STRAIGHT': '最后直线', 'FINISH': '终点对抗'}


def affinity_lines(horse):
    affinities = horse.get('affinities', {})
    lines = []
    for category, title in (('distance', '🏁距离'), ('surface', '🌱场地'), ('running_style', '🏇跑法')):
        values = affinities.get(category, {})
        if values:
            lines.append(title + '：' + '｜'.join(AFFINITY_NAMES[key] + grade for key, grade in values.items()))
    if horse.get('skills_state', 'UNINITIALIZED') == 'UNINITIALIZED':
        lines.append('✨技能：未补定（规则配置待确认）')
    else:
        lines.append('✨技能：' + '｜'.join(f"{skill['display_name']} Lv{skill['level']}（{TYPE_NAMES[skill['type']]}）"
                     for skill in horse.get('skills', [])))
    return lines


def recommendation(horse):
    values = horse.get('recommendation', [])
    return '推荐：' + ' / '.join(AFFINITY_NAMES[key] for key in values) if len(values) == 2 and all(values) else '相性未补定'


def inheritance_lines(horse):
    snapshot = horse.get('skill_inheritance')
    affinity = horse.get('affinity_inheritance')
    prefix = []
    if affinity:
        prefix = ['🧬相性血统：' + '｜'.join(AFFINITY_NAMES[key]+grade
            for values in affinity['result'].values() for key,grade in values.items()),
            '🧬遗传版本：'+affinity['algorithm_version']]
    if not snapshot:
        return prefix + ['🧬技能血统：暂无已补定快照']
    names = {row['skill_id']: row['display_name'] for row in horse.get('skills', [])}
    def label(row):
        return f"{names.get(row['skill_id'], '技能')} Lv{row['level']}"
    return prefix + ['父系选中：' + '｜'.join(map(label, snapshot['father_selected_3'])),
            '母系选中：' + '｜'.join(map(label, snapshot['mother_selected_3'])),
            '✨融合：' + ('｜'.join(f"{names.get(row['skill_id'], '技能')} Lv{max(row['parent_levels'])}→Lv{row['level']}"
                                      for row in snapshot['duplicate_groups']) or '无'),
            '🎲补位：' + ('｜'.join(map(label, snapshot['random_fill_skills'])) or '无')]


def render_race_event(event, horse_names):
    name = name_escape(horse_names.get(event['horse_id'], '参赛马'))
    data = event['data']
    if event['type'] == 'OVERTAKE_SUCCESS':
        body = f"{name}完成超车，排名 {data['previous']}→{data['position']}。"
    elif event['type'] == 'BLOCKED':
        body = f"{name}在{data['lane']}号路线受阻。"
    elif event['type'] == 'STAMINA_LOW':
        body = f"{name}体力降至{float(data['threshold']) * 100:g}%以下。"
    elif event['type'] == 'HEAD_TO_HEAD':
        other = name_escape(horse_names.get(event['target_horse_id'], '对手'))
        body = f"{name}与{other}并排争夺终点。"
    elif event['type'] == 'START_RESULT':
        result=data['start_result']
        body = f"{name}起步明显慢了下来。" if result == 'SEVERE_ERROR' else f"{name}起步稍慢了一拍。" if result == 'MINOR_ERROR' else f"{name}顺利起步。"
    elif event['type'] == 'SKILL_ACTIVATED':
        body = f"{name}发动【{data['display_name']} Lv{data['level']}】。"
    elif event['type'] == 'SKILL_FAILED':
        body = f"{name}未能发动【{data['display_name']} Lv{data['level']}】。"
    elif event['type'] == 'LANE_CHANGE':
        body = f"{name}从{data['old_lane']}号路线切换至{data['lane']}号路线。"
    elif event['type'] == 'BLOCK_AVOIDED':
        body = f"{name}成功避开堵塞。"
    elif event['type'] == 'PHOTO_FINISH':
        body = f"📸 {name}进入照片判定，以精确完赛时间决定名次。"
    elif event['type'] == 'OVERTAKE_FAILED':
        body = f"{name}此次超车未能成功。"
    elif event['type'] == 'W14_STRATEGY_EXECUTED':
        strategy={'inner':'内切','hold':'保持路线','outer':'外绕'}[data['executed_strategy']]
        body=f"{name}选择{strategy}。" if not data['fallback'] else f"{name}发现路线不安全，改为保持路线。"
    elif event['type'] == 'BLOCK_CLEARED':
        body = f"{name}摆脱了前方堵塞。"
    elif event['type'] == 'OVERTAKEN_BY_OTHER':
        other=name_escape(horse_names.get(event['target_horse_id'],'对手'))
        body = f"{name}被{other}超越，正在寻找反击机会。"
    elif event['type'] == 'COUNTER_ATTACK_DECISION':
        body = f"{name}选择立即反超。" if data['favorable'] else f"{name}暂缓反击，保存体力。"
    elif event['type'] == 'ROUTE_DECISION':
        body = f"{name}正在判断是否转向{data['lane']}号路线。"
    elif event['type'] == 'OVERTAKE_PARTIAL':
        body = f"{name}开始缩小与前方马匹的差距。"
    else:
        body = f"{name}的比赛状态已更新。"
    return PHASE_NAMES[event['phase']] + '｜' + body


def race_call(name, phase, distance, states, horse_names, events=(), total_distance=None):
    """Energetic, deterministic commentary based only on frozen checkpoint facts."""
    states = list(states)
    if not states:
        return '本检查点暂无有效位置记录。'
    def horse(state):
        return name_escape(horse_names.get(state.get('horse_id'), '参赛马'))
    leader = horse(states[0])
    gap = max(0, int(total_distance or distance) - int(distance))
    phase_name = PHASE_NAMES.get(phase, str(phase))
    opening = f'{phase_name}推进至{distance}米，{leader}暂居首位。'
    if total_distance and phase != 'FINISH':
        opening = f'{opening}距终点约{gap}米。'
    event_lines = []
    for event in events:
        if event.get('type') in {'OVERTAKE_SUCCESS', 'STAMINA_LOW', 'PHOTO_FINISH', 'BLOCKED',
                                  'BLOCK_AVOIDED', 'SKILL_ACTIVATED', 'LANE_CHANGE', 'START_RESULT'}:
            event_lines.append(render_race_event(event, horse_names))
    if event_lines:
        opening += ' ' + ' '.join(dict.fromkeys(event_lines[:2]))
    return opening if len(opening) <= 180 else f'{phase_name}推进至{distance}米，{leader}暂居首位。'


def race_checkpoint_lines(name, phase, distance, states, horse_names, events=(), total_distance=None):
    """Render one factual row per horse, with only this checkpoint's events."""
    from .formatters import money
    from .compact import decorate
    from collections import defaultdict

    phase_name = PHASE_NAMES.get(phase, str(phase))
    title = f'🏇 {str(name)[:60]}｜{phase_name}｜{distance}m'
    if total_distance:
        title += f'/{total_distance}m'
    events_by_horse = defaultdict(list)
    for event in events:
        if event.get('type') in _ALLOWED_RACE_ROW_EVENTS:
            events_by_horse[event.get('horse_id')].append(event)
    lines = [title]
    for position, state in enumerate(states, 1):
        horse_id = state.get('horse_id')
        horse = name_escape(str(horse_names.get(horse_id, '参赛马'))[:60])
        stamina = money(state.get('stamina_remaining', 0))
        stamina_max = state.get('stamina_max')
        stamina_text = f'{stamina}/{money(stamina_max)}' if stamina_max else str(stamina)
        row = f'{position}. 🐎{horse}｜体力 {stamina_text}'
        horse_events = events_by_horse.get(horse_id, [])
        movement = next((event for event in horse_events if event.get('type') == 'OVERTAKE_SUCCESS'), None)
        if movement:
            data = movement.get('data') or {}
            row += f"｜名次 {data.get('previous', '?')}→{data.get('position', position)}"
        actions = []
        for event in horse_events:
            kind = event.get('type')
            data = event.get('data') or {}
            if kind in {'SKILL_ACTIVATED', 'SKILL_FAILED'}:
                label = str(data.get('display_name') or '技能')[:24]
                level = data.get('level')
                skill = f'✨{label}' + (f' Lv{level}' if isinstance(level, int) else '')
                actions.append(skill + ('发动' if kind == 'SKILL_ACTIVATED' else '未发动'))
            elif kind == 'OVERTAKE_SUCCESS':
                actions.append('完成超越')
            elif kind == 'OVERTAKEN_BY_OTHER':
                actions.append('被对手超越')
            elif kind == 'OVERTAKE_PARTIAL':
                actions.append('迫近前方对手')
            elif kind == 'BLOCKED':
                actions.append('遭遇堵塞')
            elif kind == 'BLOCK_CLEARED':
                actions.append('摆脱堵塞')
            elif kind == 'BLOCK_AVOIDED':
                actions.append('成功避开堵塞')
            elif kind == 'LANE_CHANGE':
                actions.append(f"变道至{data.get('lane')}号路线")
            elif kind == 'START_RESULT':
                actions.append({'SEVERE_ERROR': '起步严重受挫', 'MINOR_ERROR': '起步稍慢',
                                'NORMAL': '顺利起步'}.get(data.get('start_result'), '完成起步'))
            elif kind == 'STAMINA_LOW':
                try:
                    actions.append(f"体力低于{float(data.get('threshold')) * 100:g}%")
                except (TypeError, ValueError):
                    actions.append('体力告急')
            elif kind == 'HEAD_TO_HEAD':
                actions.append('进入并跑争夺')
            elif kind == 'W14_STRATEGY_EXECUTED':
                strategy = {'inner': '内切', 'hold': '保持路线', 'outer': '外绕'}.get(data.get('executed_strategy'))
                if strategy:
                    actions.append('路线：' + strategy)
            elif kind == 'COUNTER_ATTACK_DECISION':
                actions.append('准备反超' if data.get('favorable') else '保存体力')
            elif kind == 'ROUTE_DECISION':
                actions.append(f"判断路线{data.get('lane')}")
        row += '｜动作：' + ('、'.join(dict.fromkeys(actions[:3])) if actions else '暂无关键事件')
        if state.get('blocked') and not any('堵塞' in action for action in actions):
            row += '｜受阻'
        lines.append(decorate(row))
    return lines


_ALLOWED_RACE_ROW_EVENTS = {
    'OVERTAKE_SUCCESS', 'OVERTAKEN_BY_OTHER', 'OVERTAKE_PARTIAL', 'BLOCKED',
    'BLOCK_CLEARED', 'BLOCK_AVOIDED', 'LANE_CHANGE', 'START_RESULT', 'STAMINA_LOW',
    'HEAD_TO_HEAD', 'SKILL_ACTIVATED', 'SKILL_FAILED', 'W14_STRATEGY_EXECUTED',
    'COUNTER_ATTACK_DECISION', 'ROUTE_DECISION',
}


def race_final_call(rankings, horse_names):
    """Deterministic final-race fallback, without inferred margins or events."""
    if not rankings:
        return '比赛结束，暂无有效排名记录。'
    winner = name_escape(horse_names.get(rankings[0].get('horse_id'), '参赛马'))
    if len(rankings) > 1:
        runner_up = name_escape(horse_names.get(rankings[1].get('horse_id'), '参赛马'))
        text = f'比赛结束，{winner}获得第一，{runner_up}获得第二。'
    else:
        text = f'比赛结束，{winner}获得第一。'
    return text if len(text) <= 100 else '比赛结束，参赛马获得第一。'


def race_broadcast_pages(text):
    """Paginate whole rows under platform limits, repeating the context header."""
    from .compact import decorate, fits, text_size
    lines=decorate(text).splitlines()
    if not lines: return []
    header=lines[0]
    pages=[]; page=header
    for line in lines[1:]:
        candidate=page+'\n'+line
        if fits(candidate,reserve=5):
            page=candidate
        else:
            if page != header:
                pages.append(page)
            page=header+'\n'+line
            if not fits(page,reserve=5):
                # Keep an oversized free-text row bounded; horse rows are kept whole
                # by their field limits in race_checkpoint_lines.
                clipped='';
                for char in line:
                    if text_size(header+'\n'+clipped+char)>950: break
                    clipped+=char
                page=header+'\n'+clipped
                pages.append(page)
                page=header
    if page and (page != header or not pages): pages.append(page)
    return pages
