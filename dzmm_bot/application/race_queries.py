"""Read-only views. Watching never invokes the simulation engine."""
import json
from sqlalchemy import select, func, case
from dzmm_bot.persistence.schema import race_definitions as races, race_entries as entries, horse_race_performances as performances, horses
from dzmm_bot.domain.economy import from_minor
from dzmm_bot.presentation.formatters import name_escape, long_duration
from dzmm_bot.presentation.racing import render_race_event
from .race_service import RaceService
from .competition_service import CompetitionError

STATUS_NAMES={'DRAFT':'未开放','REGISTRATION':'报名中','LOCKED':'待开赛','RUNNING':'比赛中','FINISHED':'已结束','CANCELLED':'已取消'}


def race_label(race_id):
    return race_id.split(':', 1)[1] if race_id.startswith('ruihe_medium_open_v1:') else race_id


def race_name(value):
    return name_escape(value.replace('\u745e\u79be\u676f', '澄露杯').replace('\u745e\u79be', '铃露'))


def record_summary(db, horse_id):
    rows = db.execute(select(performances.c.rank,performances.c.prize).where(performances.c.horse_id == horse_id)).all()
    return {'starts':len(rows),'wins':sum(r.rank == 1 for r in rows),
        'seconds':sum(r.rank == 2 for r in rows),'thirds':sum(r.rank == 3 for r in rows),
        'total_prize':sum(r.prize for r in rows)}


class RaceQueries:
    def __init__(self, db, now):
        self.db,self.now = db,now

    def schedule(self):
        rows = self.db.execute(select(races).where(races.c.status.in_(['DRAFT','REGISTRATION','LOCKED','RUNNING']))
            .order_by(races.c.registration_close_at,races.c.starts_at).limit(6)).mappings().all()
        lines = ['🏇 铃露赛程']
        for r in rows:
            d=json.loads(r['definition_json'])
            count=self.db.execute(select(func.count()).select_from(entries).where(entries.c.race_id == r['race_id'],
                entries.c.status.in_(['REGISTERED','LOCKED']))).scalar_one()
            lines.append(f"{race_name(r['name'])}｜{d['distance']}m｜{count}/{d['max_horses']}｜{STATUS_NAMES[r['status']]}")
        return '\n'.join(lines + (['/比赛详情 赛事 | /报名 赛事 马 跑法'] if rows else ['暂无赛事，请稍后查看。']))

    def row(self,race_id,registration=False):
        if race_id == '澄露杯':
            query=select(races).where(races.c.template_id=='ruihe_medium_open_v1')
            if registration:
                query=query.where(races.c.status=='REGISTRATION',
                    races.c.registration_open_at<=self.now,races.c.registration_close_at>self.now)
            else:
                query=query.where(races.c.status.in_(['DRAFT','REGISTRATION','LOCKED','RUNNING']))
            row=self.db.execute(query.order_by(races.c.starts_at).limit(1)).mappings().first()
            if row is None and not registration:
                row=self.db.execute(select(races).where(races.c.template_id=='ruihe_medium_open_v1',
                    races.c.status=='FINISHED').order_by(races.c.starts_at.desc()).limit(1)).mappings().first()
            if row is None:
                raise CompetitionError('澄露杯暂无可报名赛事，请用 /赛程 查看。' if registration else '暂无澄露杯赛事，请用 /赛程 查看。')
            return row
        row=self.db.execute(select(races).where(races.c.race_id == race_id)).mappings().first()
        if row is None:
            named=self.db.execute(select(races).where(races.c.name==race_id,
                races.c.status.in_(['DRAFT','REGISTRATION','LOCKED','RUNNING']))).mappings().all()
            if len(named)==1:
                return named[0]
            if not named and not registration:
                finished=self.db.execute(select(races).where(races.c.name==race_id,races.c.status=='FINISHED')
                    .order_by(races.c.starts_at.desc()).limit(1)).mappings().first()
                if finished: return finished
            if len(named)>1:
                raise CompetitionError('同名赛事有多场，请用 /赛程 查看并指定赛事编号。')
            matches=self.db.execute(select(races).where(races.c.race_date==race_id)).mappings().all()
            if len(matches)==1:
                row=matches[0]
            elif len(matches)>1:
                raise CompetitionError('该日期有多场赛事，请指定完整赛事编号。')
        if row is None:
            raise CompetitionError('找不到已公布赛事，请用 /赛程 查看。')
        return row

    def current_race_id(self, registration=False):
        query=select(races.c.race_id).where(races.c.registration_open_at<=self.now)
        if registration:
            query=query.where(races.c.status=='REGISTRATION',races.c.registration_close_at>self.now)
        else:
            query=query.where(races.c.status.in_(['REGISTRATION','LOCKED','RUNNING','FINISHED']))
        race_id=self.db.execute(query.order_by(races.c.starts_at.desc()).limit(1)).scalar()
        if not race_id: raise CompetitionError('当前没有可用赛事，请用 /赛程 查看。')
        return race_id

    def detail(self,race_id):
        row=self.row(race_id); d=json.loads(row['definition_json'])
        race_id=race_name(row['name'])
        surface={'turf':'草地','dirt':'泥地'}[d['surface']]
        return (f"🏇 {race_name(row['name'])}｜{STATUS_NAMES[row['status']]}\n{d['distance']}m｜{surface}·{d['track_condition']}"
            f"\n报名费 {d['entry_fee']} 币｜奖金 {' / '.join(map(str,d['prizes']))} 币"
            f"\n距报名截止：{long_duration(max(0,row['registration_close_at']-self.now))}"
            f"\n/报名 {race_id} 马名 逃 | /观赛 {race_id} | /赛果 {race_id}")

    def result(self,race_id,watch=False):
        row=self.row(race_id)
        race_id=row['race_id']
        if row['status'] != 'FINISHED':
            return f"🏇 {race_name(row['name'])}尚未完赛｜/比赛详情 {race_name(row['name'])}"
        definition=json.loads(row['definition_json'])
        if definition.get('race_commentary_status') in {'pending','generating'}:
            return f"🏇 {race_name(row['name'])}正在准备赛况播报，完成后即可查看赛果。"
        task_ids=definition.get('race_broadcast_task_ids',[])
        if task_ids:
            from dzmm_bot.persistence.transport import outbox
            statuses=self.db.execute(select(outbox.c.status).where(outbox.c.id.in_(task_ids))).scalars().all()
            if len(statuses)!=len(task_ids) or any(status not in ('sent','simulated') for status in statuses):
                return f'🏇 {race_name(row["name"])}正在播报中，最终播报完成后可查看赛果。'
        result=RaceService(self.db,self.now,None).get_result(race_id)
        if result is None:
            raise CompetitionError('赛果暂不可读取，请稍后再试。')
        snapshot=json.loads(row['snapshot_json'])
        names={h['horse_id']:h.get('name','NPC') for h in snapshot['participants']}
        lines=[f"🏁 {race_name(row['name'])}｜{'观赛回放' if watch else '赛果'}"]
        if watch:
            events=[e for ranking in result['rankings'] for e in ranking['key_events'] if e['type'] in
                {'OVERTAKE_SUCCESS','BLOCKED','BLOCK_AVOIDED','LANE_CHANGE','STAMINA_LOW','SKILL_ACTIVATED','HEAD_TO_HEAD','PHOTO_FINISH'}]
            from dzmm_bot.domain.race_engine import RacePhase
            events.sort(key=lambda e:(e['checkpoint'] if e.get('checkpoint') is not None else list(RacePhase).index(RacePhase(e['phase'])),
                -e.get('importance',1),e['horse_id']))
            lines += [render_race_event(e,names) for e in events[:7]] or ['本场暂无关键事件。']
        else:
            from dzmm_bot.presentation.formatters import money
            lines += [f"{r['rank']}. 🐎{name_escape(names.get(r['horse_id'],'参赛马'))}｜"+
                (f"{money(r['finish_time'])}秒｜" if 'finish_time' in r else '')+f"剩余体力 {money(r['remaining_stamina'])}"
                for r in result['rankings']]
        return '\n'.join(lines+[f'/观赛 {race_name(row['name'])} | /赛果 {race_name(row['name'])} | /战绩 马名'])

    def record(self,horse):
        summary=record_summary(self.db,horse['id'])
        rows=self.db.execute(select(performances).where(performances.c.horse_id == horse['id'])
            .order_by(performances.c.created_at.desc(),performances.c.race_id).limit(4)).mappings().all()
        rate=round(summary['wins']*100/summary['starts'],1) if summary['starts'] else 0
        lines=[f"📊 🐎{name_escape(horse['name'])}的战绩",f"{summary['starts']}战{summary['wins']}胜｜胜率 {rate}%｜累计奖金 {from_minor(summary['total_prize'])} 币"]
        for r in rows:
            p=json.loads(r['performance_json'])
            lines.append(f"{name_escape(race_label(r['race_id']))}：第{r['rank']}名｜"+'；'.join(p['analysis']))
        return '\n'.join(lines+['/赛程 | /马 '+str(horse['name'])])

    def ranking(self, metric='奖金'):
        if metric not in ('奖金','胜场'):
            raise CompetitionError('赛马榜可查看：/排行榜 赛马 奖金 | /排行榜 赛马 胜场；积分规则待确认。')
        wins=func.sum(case((performances.c.rank == 1,1),else_=0))
        order=func.sum(performances.c.prize) if metric == '奖金' else wins
        rows=self.db.execute(select(performances.c.horse_id,func.count().label('starts'),
            wins.label('wins'),func.sum(performances.c.prize).label('prize')).group_by(performances.c.horse_id)
            .order_by(order.desc(),performances.c.horse_id).limit(8)).mappings().all()
        lines=['🏆 赛马'+metric+'榜']
        for i,r in enumerate(rows,1):
            name=self.db.execute(select(horses.c.name).where(horses.c.id == r['horse_id'])).scalar()
            lines.append(f"{i}. 🐎{name_escape(name or '参赛马')}｜{r['starts']}战{r['wins']}胜｜{from_minor(r['prize'])} 币")
        return '\n'.join(lines+(['暂无战绩。'] if not rows else [])+['/赛程 | /战绩 马名'])
