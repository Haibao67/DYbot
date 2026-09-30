"""P8-7/8 lifecycle. Caller owns transaction; no implicit commit or platform send."""
import json
import logging
from decimal import Decimal
from sqlalchemy import select, func
from dzmm_bot.persistence.schema import (race_definitions as races, race_entries as entries,
    horse_race_locks as locks, race_state_log, horse_race_performances as performances,
    horse_pregnancies, horses)
from dzmm_bot.domain.economy import to_minor, from_minor
from dzmm_bot.domain.horse_rules import stage
from dzmm_bot.domain.horse_racing import AFFINITY_KEYS, AffinityResolver, RacingConfigurationError
from dzmm_bot.domain.race_engine import RaceContext
from .services import GameService, uid
from .horse_service import HorseService
from .horse_asset_lock import assert_horse_mutable
from .horse_racing_service import HorseRacingService
from .race_service import RaceService


class CompetitionError(ValueError):
    pass

log=logging.getLogger(__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class CompetitionService(GameService):
    def __init__(self, *args, engine=None, affinity_modifiers=None, npc_factory=None,
                 race_commentary_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.engine, self.affinity_modifiers, self.npc_factory = engine, affinity_modifiers, npc_factory
        self.race_commentary_options = dict(race_commentary_options or {})

    def require_enabled(self):
        from dzmm_bot.domain.race_rules_r11 import VERSION
        from dzmm_bot.domain.race_rules_r12 import VERSION as R12_VERSION
        from dzmm_bot.domain.race_rules_r13 import VERSION as R13_VERSION
        r11=self.engine is not None and self.engine.policy is not None and self.engine.policy.version in (VERSION,R12_VERSION,R13_VERSION)
        if self.engine is None or self.engine.policy is None or self.engine.skill_runtime is None or (not r11 and not self.affinity_modifiers):
            raise CompetitionError('赛马规则配置待确认，报名与比赛暂未开放。')

    def resolve_affinity(self, profile, definition, style):
        from dzmm_bot.domain.race_rules_r11 import VERSION
        from dzmm_bot.domain.race_rules_r12 import VERSION as R12_VERSION
        from dzmm_bot.domain.race_rules_r13 import VERSION as R13_VERSION
        if definition['simulation_version'] in (VERSION,R12_VERSION,R13_VERSION):
            from dzmm_bot.domain.race_rules_r1 import race_affinity
            return race_affinity(profile['affinities'],definition['distance_type'],definition['surface'],style)
        return AffinityResolver.resolve(profile['affinities'],definition['distance_type'],definition['surface'],style,self.affinity_modifiers)

    def race(self, race_id):
        if self.db.dialect.name == 'sqlite':
            self.db.execute(races.update().where(races.c.race_id == race_id).values(race_id=race_id))
        row = self.db.execute(select(races).where(races.c.race_id == race_id).with_for_update()).mappings().first()
        if row is None:
            raise CompetitionError('找不到该赛事，请用 /赛程 查看。')
        return dict(row)

    def transition(self, row, target):
        allowed = {'DRAFT': {'REGISTRATION', 'CANCELLED'}, 'REGISTRATION': {'LOCKED', 'CANCELLED'},
                   'LOCKED': {'RUNNING', 'CANCELLED'}, 'RUNNING': {'FINISHED'}, 'FINISHED': set(), 'CANCELLED': set()}
        if target not in allowed[row['status']]:
            raise CompetitionError('赛事状态不允许此操作。')
        self.db.execute(races.update().where(races.c.race_id == row['race_id']).values(status=target))
        self.db.execute(race_state_log.insert().values(id=uid(), race_id=row['race_id'],
            from_status=row['status'], to_status=target, created_at=self.now))

    def create(self, definition, *, admin=False):
        # Internal API: chat does not expose creation; caller must authorize administrator.
        if not admin:
            raise CompetitionError('仅管理员可以创建赛事。')
        self.require_enabled()
        d = dict(definition)
        required = {'race_id','name','distance','distance_type','surface','track_condition',
            'min_horses','max_horses','registration_open_at','registration_close_at','starts_at',
            'entry_fee','prizes','refund_ratio','season_id','rng_seed','simulation_version','npc_fill'}
        if not required <= d.keys():
            raise CompetitionError('赛事配置不完整。')
        if not all(type(d[k]) is int for k in ('distance','min_horses','max_horses')) or not (
                d['distance'] > 0 and 1 <= d['min_horses'] <= d['max_horses']):
            raise CompetitionError('赛事人数或距离不合法。')
        if d['distance_type'] not in AFFINITY_KEYS['distance'] or d['surface'] not in AFFINITY_KEYS['surface'] or d['track_condition'] not in ('良','稍重','重','不良'):
            raise CompetitionError('赛事场地不合法。')
        if not d['registration_open_at'] < d['registration_close_at'] <= d['starts_at']:
            raise CompetitionError('赛事时间不合法。')
        if len(d['prizes']) != 3 or any(Decimal(str(v)) < 0 for v in [d['entry_fee'], *d['prizes']]):
            raise CompetitionError('赛事金额不合法。')
        if not Decimal(0) <= Decimal(str(d['refund_ratio'])) <= 1 or type(d['npc_fill']) is not bool:
            raise CompetitionError('退款或 NPC 规则不合法。')
        if d['simulation_version'] != self.engine.policy.version:
            raise CompetitionError('模拟规则版本不一致。')
        if d['npc_fill'] and self.npc_factory is None:
            raise CompetitionError('NPC 补位配置尚未提供。')
        self.engine.policy.validate(d['distance'])
        if self.db.execute(select(races.c.race_id).where(races.c.race_id == d['race_id'])).first():
            raise CompetitionError('赛事编号已存在。')
        self.db.execute(races.insert().values(race_id=d['race_id'], name=d['name'], definition_json=dumps(d),
            template_id=d.get('template_id'),race_date=d.get('race_date'),
            status='DRAFT', registration_open_at=d['registration_open_at'],
            registration_close_at=d['registration_close_at'], starts_at=d['starts_at'], created_at=self.now))

    def open_registration(self, race_id):
        self.require_enabled()
        row = self.race(race_id)
        self.transition(row, 'REGISTRATION')

    def admin_create(self, name, distance, minutes, surface='turf', max_horses=20, admins=None, password_hash=''):
        from .admin_login_service import AdminLoginService
        from dzmm_bot.domain.race_runtime import DailyRaceTemplate
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from hashlib import sha256
        if not AdminLoginService(self.db,self.now,password_hash,admins or set()).is_admin(self.player):
            raise CompetitionError('该指令仅限管理员使用。')
        if not name or len(name)>40 or not 1<=minutes<=1440:
            raise CompetitionError('名称最多40字，报名时长为1～1440分钟。')
        if type(distance) is not int or distance <= 1000 or distance % 100:
            raise CompetitionError('距离须大于1000米，且为100的整数倍。')
        if type(max_horses) is not int or not 8 <= max_horses <= 20:
            raise CompetitionError('参赛上限须为8～20匹。')
        if surface not in AFFINITY_KEYS['surface']:
            raise CompetitionError('场地：草地 | 泥地。')
        distance_type = ('short' if distance <= 1500 else 'mile' if distance <= 1900
                         else 'medium' if distance <= 2400 else 'long')
        definition=DailyRaceTemplate().definition(datetime.fromtimestamp(self.now,ZoneInfo('Asia/Shanghai')).date())
        race_id='custom:'+uid()
        definition.update(race_id=race_id,template_id=race_id,name=name,distance=distance,
            distance_type=distance_type,surface=surface,max_horses=max_horses,
        reward_items={},broadcast_room_id=self.room,registration_open_at=self.now,
            registration_close_at=self.now+minutes*60,starts_at=self.now+minutes*60,
            rng_seed=sha256((race_id+':1.3').encode()).hexdigest())
        self.create(definition,admin=True)
        self.open_registration(race_id)
        self.event('admin_race_create',{'race_id':race_id,'actor':self.player,'definition':definition})
        return race_id

    def admin_force_start(self, race_id, admins=None, password_hash=''):
        from .admin_login_service import AdminLoginService
        if not AdminLoginService(self.db,self.now,password_hash,admins or set()).is_admin(self.player):
            raise CompetitionError('该指令仅限管理员使用。')
        self.require_enabled()
        with self.db.begin_nested():
            row=self.race(race_id)
            if row['status']=='FINISHED' and row['settled_at'] is not None:
                return self.start(race_id)
            if row['status'] not in ('REGISTRATION','LOCKED','RUNNING'):
                raise CompetitionError('只有开放报名或已锁定的赛事可强制开赛。')
            definition=json.loads(row['definition_json'])
            if row['status']=='REGISTRATION':
                count=self.db.execute(select(func.count(func.distinct(entries.c.player_id))).where(
                    entries.c.race_id==race_id,entries.c.status=='REGISTERED')).scalar_one()
                if count < definition.get('min_real_players',2):
                    raise CompetitionError('至少需要两名真实玩家报名，暂不能强制开赛。')
                if self.now <= row['registration_open_at']:
                    raise CompetitionError('报名刚刚开放，请稍后再强制开赛。')
                definition.update(registration_close_at=self.now,starts_at=self.now)
                self.db.execute(races.update().where(races.c.race_id==race_id).values(
                    registration_close_at=self.now,starts_at=self.now,definition_json=dumps(definition)))
                self.lock_entries(race_id)
            else:
                self.db.execute(races.update().where(races.c.race_id==race_id).values(starts_at=self.now))
            result=self.start(race_id)
            self.event('admin_race_force_start',{'race_id':race_id,'actor':self.player})
            return result

    def register(self, race_id, identifier, running_style):
        self.require_enabled()
        self.account(True)
        row = self.race(race_id)
        d = json.loads(row['definition_json'])
        if row['status'] != 'REGISTRATION' or not row['registration_open_at'] <= self.now < row['registration_close_at']:
            raise CompetitionError('当前不在报名时间内。')
        if running_style not in AFFINITY_KEYS['running_style']:
            raise CompetitionError('跑法可填：逃 | 先行 | 差 | 追。')
        horse = HorseService(self.db,self.now,self.secret,self.player,self.room,self.reference).resolve_horse(identifier)
        assert_horse_mutable(self.db, horse['id'])
        if stage(horse['born_at'], self.now) == '幼驹':
            raise CompetitionError('幼驹暂不能报名，成长至青年马后即可参赛。')
        if horse['breeding_cooldown_until'] > self.now or self.db.execute(select(horse_pregnancies.c.id).where(
                horse_pregnancies.c.mother_id == horse['id'],horse_pregnancies.c.status == 'pending')).first():
            raise CompetitionError('繁育中的马匹不能报名。')
        prior=self.db.execute(select(entries).where(entries.c.race_id == race_id, entries.c.horse_id == horse['id'])).mappings().first()
        if prior and prior['status'] != 'CANCELLED':
            raise CompetitionError('该马已经报过此赛事。')
        player_count=self.db.execute(select(func.count()).select_from(entries).where(entries.c.race_id == race_id,
            entries.c.player_id == self.player,entries.c.status.in_(['REGISTERED','LOCKED']))).scalar_one()
        if player_count >= d.get('max_entries_per_player',d['max_horses']):
            raise CompetitionError('已达到本场个人报名上限。')
        count = self.db.execute(select(func.count()).select_from(entries).where(entries.c.race_id == race_id,
            entries.c.status == 'REGISTERED')).scalar_one()
        if count >= d['max_horses']:
            raise CompetitionError('赛事名额已满。')
        profile = HorseService(self.db,self.now,self.secret,self.player,self.room,self.reference).racing_horse(horse,True)
        affinity = self.resolve_affinity(profile,d,running_style)
        if profile['skills_state'] != 'FINALIZED':
            raise CompetitionError('马匹技能尚未补定，暂不能参赛。')
        for skill in profile['skills']:
            try:
                definition = self.engine.skill_runtime.registry.get(skill['skill_id'])
            except KeyError as exc:
                raise CompetitionError('该马技能版本不适用于当前赛事，保留原技能等待处理。') from exc
            if not definition.enabled or not definition.implemented or (definition.type == 'WISDOM_TRIGGER' and
                    self.engine.skill_runtime.wisdom_resolver.probability_formula is None):
                raise CompetitionError('参赛技能执行规则未配置完整，暂不能报名。')
        entry_id = uid()
        self.ledger(-Decimal(str(d['entry_fee'])),'race_entry','race_entry',entry_id)
        values=dict(entry_id=entry_id,race_id=race_id,horse_id=horse['id'],player_id=self.player,
            running_style=running_style,status='REGISTERED',entry_fee_paid=to_minor(d['entry_fee']),registered_at=self.now)
        if prior:
            self.db.execute(entries.update().where(entries.c.entry_id == prior['entry_id']).values(**values))
        else:
            self.db.execute(entries.insert().values(**values))
        self.db.execute(locks.insert().values(horse_id=horse['id'],race_id=race_id,created_at=self.now))
        warning = '｜⚠️ 所选相性含 D' if any(affinity[k] == 'D' for k in affinity if k.endswith('_grade')) else ''
        return f"🏇 已报名【{row['name']}】：🐎{horse['name']}{warning}\n/比赛详情 {race_id} | /取消报名 {race_id} {horse['name']}"

    def cancel_entry(self, race_id, identifier):
        self.account(True)
        row = self.race(race_id)
        if row['status'] != 'REGISTRATION' or self.now >= row['registration_close_at']:
            raise CompetitionError('报名截止后不能取消。')
        horse = HorseService(self.db,self.now,self.secret,self.player,self.room).resolve_horse(identifier)
        entry = self.db.execute(select(entries).where(entries.c.race_id == race_id,entries.c.horse_id == horse['id'],
            entries.c.player_id == self.player)).mappings().first()
        if not entry or entry['status'] != 'REGISTERED':
            raise CompetitionError('没有可取消的报名。')
        refund = from_minor(entry['entry_fee_paid']) * Decimal(str(json.loads(row['definition_json'])['refund_ratio']))
        paid=from_minor(entry['entry_fee_paid'])
        self.ledger(paid,'race_entry_refund','race_entry_refund',entry['entry_id'])
        if paid-refund > 0:
            self.ledger(-(paid-refund),'race_cancel_fee','race_cancel_fee',entry['entry_id'])
        self.db.execute(entries.update().where(entries.c.entry_id == entry['entry_id']).values(status='CANCELLED'))
        self.db.execute(locks.delete().where(locks.c.horse_id == horse['id'],locks.c.race_id == race_id))
        return f'已取消报名｜返还 {from_minor(to_minor(refund))} 币｜/赛程'

    def lock_entries(self, race_id):
        self.require_enabled()
        row = self.race(race_id)
        if row['status'] in ('RUNNING','FINISHED') or (row['status']=='LOCKED' and row['snapshot_json']):
            return
        if row['status'] not in ('REGISTRATION','LOCKED') or self.now < row['registration_close_at']:
            raise CompetitionError('尚未到报名截止时间。')
        d = json.loads(row['definition_json'])
        participants = []
        real_players=self.db.execute(select(func.count(func.distinct(entries.c.player_id))).where(
            entries.c.race_id == race_id,entries.c.status == 'REGISTERED')).scalar_one()
        if real_players < d.get('min_real_players',0):
            self.cancel_race(race_id,admin=True,refund_ratio=d['full_refund_ratio'])
            return
        for entry in self.db.execute(select(entries).where(entries.c.race_id == race_id,entries.c.status == 'REGISTERED').order_by(entries.c.horse_id)).mappings():
            horse = dict(self.db.execute(select(horses).where(horses.c.id == entry['horse_id']).with_for_update()).mappings().one())
            profile = HorseRacingService(self.db,self.now,registry=self.engine.skill_runtime.registry).profile(horse['id'])
            affinity = self.resolve_affinity(profile,d,entry['running_style'])
            participants.append({**horse,'horse_id':horse['id'],'running_style':entry['running_style'],
                'affinity':affinity,'skills':profile['skills'],'affinities':profile['affinities'],'is_npc':False,
                'owner_id':horse['player_id'],'chosen_running_style':entry['running_style'],
                'npc_template_id':None,'condition':'NORMAL','lane_initial_state':{'lane':len(participants)%3}})
        if len(participants) < d['min_horses']:
            if not d['npc_fill'] or self.npc_factory is None:
                raise CompetitionError('参赛人数不足，NPC 配置未开放；赛事保留等待处理。')
            self.runtime_stage='NPC_GENERATION_FAILED'
            log.info('npc fill started race_id=%s template_id=%s versions=%s',race_id,d.get('template_id'),d.get('versions'))
            participants += (self.npc_factory.fill(d,participants) if hasattr(self.npc_factory,'fill')
                             else self.npc_factory(d, d['min_horses'] - len(participants)))
            log.info('npc fill completed race_id=%s',race_id)
        self.runtime_stage='SNAPSHOT_GENERATION_FAILED'
        context = RaceContext(race_id,d['distance'],d['distance_type'],d['surface'],d['track_condition'],
            participants,d['starts_at'],d['simulation_version'],d['rng_seed'])
        from dataclasses import asdict
        from dzmm_bot.domain.race_rules_r11 import VERSION
        from dzmm_bot.domain.race_rules_r12 import VERSION as R12_VERSION
        from dzmm_bot.domain.race_rules_r13 import VERSION as R13_VERSION
        if d['simulation_version'] in (VERSION,R12_VERSION,R13_VERSION):
            skill_ids=sorted({s['skill_id'] for horse in participants for s in horse['skills']})
            context.rule_metadata={'weather':d.get('weather','晴'),'snapshot_created_at':self.now,
                'race_engine_version':d['simulation_version'],'race_rule_version':d.get('race_rule_version','p8-r1-v1'),
                'affinity_version':d.get('affinity_version','affinity_generation_v1'),
                'npc_template_version':d.get('npc_template_version','npc_template_v1'),
                'skill_definitions':{code:asdict(self.engine.skill_runtime.registry.get(code)) for code in skill_ids}}
        if d['simulation_version'] == R13_VERSION:
            context.rule_metadata['route_strategy_config']=self.engine.strategy_config
        if d.get('versions'):
            context.rule_metadata.update(versions=d['versions'],template_id=d.get('template_id'),race_date=d.get('race_date'))
            from dzmm_bot.domain.horse_racing import stable_rng
            grid=sorted(h['horse_id'] for h in participants)
            grid_rng,_=stable_rng(d['rng_seed'],d['simulation_version']); grid_rng.shuffle(grid)
            for horse in participants:
                position=grid.index(horse['horse_id'])
                horse['lane_initial_state']={'lane':position%3,'position':position+1}
        d['npc_template_version']='horse_catalog_v1'
        context.rule_metadata['npc_template_version']='horse_catalog_v1'
        if d.get('versions'):
            d['versions']={**d['versions'],'npc_template':'horse_catalog_v1'}
            context.rule_metadata['versions']=d['versions']
        self.db.execute(races.update().where(races.c.race_id == race_id).values(
            snapshot_json=dumps(asdict(context)),definition_json=dumps(d)))
        self.db.execute(entries.update().where(entries.c.race_id == race_id,entries.c.status == 'REGISTERED').values(status='LOCKED'))
        if row['status'] != 'LOCKED': self.transition(row,'LOCKED')

    def cancel_race(self, race_id, *, admin=False, refund_ratio=None):
        if not admin:
            raise CompetitionError('仅管理员可以取消赛事。')
        if refund_ratio is None or not Decimal(0) <= Decimal(str(refund_ratio)) <= 1:
            raise CompetitionError('需要明确配置赛事取消退款比例。')
        with self.db.begin_nested():
            row=self.race(race_id)
            if row['status'] == 'CANCELLED':
                return
            if row['status'] not in ('DRAFT','REGISTRATION','LOCKED'):
                raise CompetitionError('当前赛事不能取消。')
            for entry in self.db.execute(select(entries).where(entries.c.race_id == race_id,
                    entries.c.status.in_(['REGISTERED','LOCKED'])).order_by(entries.c.player_id,entries.c.entry_id)).mappings():
                service=GameService(self.db,self.now,self.secret,entry['player_id'],self.room,race_id)
                service.ledger(from_minor(entry['entry_fee_paid'])*Decimal(str(refund_ratio)),
                    'race_entry_refund','race_entry_refund',entry['entry_id'])
            self.db.execute(entries.update().where(entries.c.race_id == race_id,
                entries.c.status.in_(['REGISTERED','LOCKED'])).values(status='CANCELLED'))
            self.db.execute(locks.delete().where(locks.c.race_id == race_id))
            self.transition(row,'CANCELLED')
            log.info('race cancelled refund completed race_id=%s versions=%s',race_id,
                     json.loads(row['definition_json']).get('versions'))

    def queue_race_broadcast(self, row, definition, context, result):
        from dzmm_bot.persistence.transport import rooms, outbox
        from dzmm_bot.presentation.racing import race_broadcast_pages, race_call, race_final_call, race_checkpoint_lines
        from dzmm_bot.presentation.formatters import name_escape
        from .race_queries import race_name
        name=race_name(row['name'])
        targets=select(rooms.c.id).where(rooms.c.kind=='group',rooms.c.enabled==1)
        source=definition.get('broadcast_room_id')
        if source:
            targets=targets.where(rooms.c.id==source)
        names={h['horse_id']:h.get('name','NPC') for h in context.participants}
        messages=[]
        saved_commentary=definition.get('race_commentary', {})
        by_checkpoint=saved_commentary.get('checkpoints', {}) if isinstance(saved_commentary,dict) else {}
        for checkpoint in result['checkpoints']:
            states=checkpoint['states']
            event_rows=[]
            checkpoint_index=checkpoint.get('checkpoint_index')
            for state in states:
                for event in state.get('events',[]):
                    if checkpoint_index is not None and event.get('checkpoint') == checkpoint_index:
                        event_rows.append(event)
            # Engine snapshots currently key events by phase/checkpoint in each state.
            # Use only facts from this checkpoint; deterministic narration is the fallback.
            fallback=race_call(name,checkpoint['phase'],checkpoint['distance_marker'],states,names,event_rows,context.distance)
            commentary=by_checkpoint.get(str(checkpoint.get('checkpoint_index')), fallback)
            rows=race_checkpoint_lines(name,checkpoint['phase'],checkpoint['distance_marker'],
                states,names,event_rows,context.distance)
            # The renderer owns the repeated header; keep commentary after all horse facts.
            messages.append('\n'.join([*rows,f'🎙️ {commentary}']))
        final_commentary=(saved_commentary.get('final') if isinstance(saved_commentary,dict) else None)
        if not final_commentary:
            final_commentary=race_final_call(result['rankings'],names)
        messages.append('\n'.join([f'🏁 {name} 最终赛果',final_commentary]+[
            f'{r["rank"]}. 🐎{name_escape(names.get(r["horse_id"],"参赛马"))}' for r in result['rankings']]+
            [f'/观赛 {name} | /赛果 {name} | /战绩 马名']))
        import hashlib,uuid
        task_ids=[]
        for room in self.db.execute(targets).scalars():
            prefix='acebca57'+hashlib.sha256(f'{row["race_id"]}:{room}'.encode()).hexdigest()[:16]
            index=0
            for checkpoint_index,message in enumerate(messages):
                for page in race_broadcast_pages(message):
                    task_id=str(uuid.UUID(hex=prefix+f'{index:08x}'))
                    task_ids.append(task_id)
                    if not self.db.execute(select(outbox.c.id).where(outbox.c.id==task_id)).first():
                        self.db.execute(outbox.insert().values(id=task_id,room=room,kind='group',text=page,
                            status='pending',created=self.now+index*0.001,available=self.now+checkpoint_index*10,attempts=0,
                            reply_to_message_id=None,reply_to_sender_id=None,reply_to_text=None))
                    index+=1
        definition['race_broadcast_task_ids']=task_ids
        self.db.execute(races.update().where(races.c.race_id==row['race_id']).values(definition_json=dumps(definition)))

    def start(self, race_id):
        self.require_enabled()
        # A failure rolls back result, prizes and state together; retry uses frozen seed.
        with self.db.begin_nested():
            row = self.race(race_id)
            if row['status'] == 'FINISHED' and row['settled_at'] is not None:
                return RaceService(self.db,self.now,self.engine).get_result(race_id)
            if row['status'] not in ('LOCKED','RUNNING','FINISHED') or self.now < row['starts_at']:
                raise CompetitionError('赛事尚不可开赛。')
            d = json.loads(row['definition_json'])
            if not row['snapshot_json']:
                raise CompetitionError('赛事快照缺失，保持原状态等待修复。')
            if row['status']=='LOCKED': self.transition(row,'RUNNING')
            context = RaceContext(**json.loads(row['snapshot_json']))
            existing=RaceService(self.db,self.now,None).get_result(race_id)
            if row['status']=='FINISHED' and existing is None:
                raise CompetitionError('已结束赛事缺失赛果，禁止重新模拟。')
            if existing is None and context.rule_metadata.get('npc_template_version') != 'horse_catalog_v1':
                from dataclasses import asdict
                from dzmm_bot.domain.race_runtime import NpcHorseFactory, SkillPoolRegistry
                registry=self.engine.skill_runtime.registry
                factory=NpcHorseFactory(registry,SkillPoolRegistry(registry),None)
                real=[h for h in context.participants if not h['is_npc']]
                generated=factory.fill({**d,'min_horses':len(context.participants)},real)
                old_npcs=[h for h in context.participants if h['is_npc']]
                for new,old in zip(generated,old_npcs):
                    new['horse_id']=old['horse_id']
                    new['lane_initial_state']=old['lane_initial_state']
                replacements={h['horse_id']:h for h in generated}
                context.participants=[replacements.get(h['horse_id'],h) for h in context.participants]
                d['npc_template_version']='horse_catalog_v1'
                context.rule_metadata['npc_template_version']='horse_catalog_v1'
                if context.rule_metadata.get('versions'):
                    versions={**context.rule_metadata['versions'],'npc_template':'horse_catalog_v1'}
                    context.rule_metadata['versions']=versions
                    d['versions']=versions
                skill_ids={s['skill_id'] for h in context.participants for s in h['skills']}
                context.rule_metadata['skill_definitions']={code:asdict(registry.get(code)) for code in sorted(skill_ids)}
                self.db.execute(races.update().where(races.c.race_id==race_id).values(
                    definition_json=dumps(d),snapshot_json=dumps(asdict(context))))
                log.info('race NPC snapshot upgraded race_id=%s',race_id)
            if context.rule_metadata.get('versions'):
                from dzmm_bot.domain.race_runtime import RaceRuntimeFactory
                runtime=RaceRuntimeFactory.build(context.rule_metadata['versions'],
                    strategy_config=context.rule_metadata.get('route_strategy_config'))
                runtime.validate_snapshot(context)
                self.engine=runtime.engine
            self.runtime_stage='RACE_SIMULATION_FAILED'
            log.info('race started race_id=%s template_id=%s versions=%s',race_id,d.get('template_id'),d.get('versions'))
            result = existing or RaceService(self.db,self.now,self.engine).finalize(context)
            self.runtime_stage='SETTLEMENT_FAILED'
            by_id = {h['horse_id']:h for h in context.participants}
            from dzmm_bot.persistence.schema import race_rewards, race_events
            # Structured events are stored in the same transaction as results and rewards.
            if not self.db.execute(select(race_events.c.event_index).where(race_events.c.race_id==race_id)).first():
                events=[e for ranking in result['rankings'] for e in ranking['key_events']]
                for index,event in enumerate(events):
                    self.db.execute(race_events.insert().values(race_id=race_id,event_index=index,
                        event_json=dumps(event),created_at=self.now))
            for ranking in result['rankings']:
                horse = by_id[ranking['horse_id']]
                if horse['is_npc']:
                    continue
                prize = to_minor(d['prizes'][ranking['rank']-1]) if ranking['rank'] <= 3 else 0
                performance = (runtime.components['PostRaceAnalysisService'](ranking)
                               if context.rule_metadata.get('versions') else analyze_performance(ranking))
                if not self.db.execute(select(performances.c.horse_id).where(
                        performances.c.race_id==race_id,performances.c.horse_id==horse['horse_id'])).first():
                    self.db.execute(performances.insert().values(race_id=race_id,horse_id=horse['horse_id'],player_id=horse['player_id'],
                        rank=ranking['rank'],prize=prize,season_id=d['season_id'],performance_json=dumps(performance),created_at=self.now))
                rewarded=self.db.execute(select(race_rewards.c.horse_id).where(race_rewards.c.race_id==race_id,
                    race_rewards.c.horse_id==horse['horse_id'],race_rewards.c.reward_type=='prize')).first()
                if not rewarded:
                    service=GameService(self.db,self.now,self.secret,horse['player_id'],self.room,race_id)
                    # Older lifecycle versions used one aggregate ledger entry per player/race.
                    from dzmm_bot.persistence.schema import currency
                    legacy=self.db.execute(select(currency.c.id).where(currency.c.player_id==horse['player_id'],
                        currency.c.reference_type=='race_prize',currency.c.reference_id==race_id)).first()
                    if not legacy:
                        service.ledger(from_minor(prize),'race_prize','race_horse_prize',race_id+':'+horse['horse_id'])
                    self.db.execute(race_rewards.insert().values(race_id=race_id,horse_id=horse['horse_id'],
                        reward_type='prize',player_id=horse['player_id'],amount=prize,created_at=self.now))
            self.db.execute(entries.update().where(entries.c.race_id == race_id,entries.c.status == 'LOCKED').values(status='FINISHED'))
            self.db.execute(locks.delete().where(locks.c.race_id == race_id))
            if row['status']!='FINISHED': self.transition({**row,'status':'RUNNING'},'FINISHED')
            if self.race_commentary_options.get('enabled'):
                d['race_commentary_status']='pending'
                self.db.execute(races.update().where(races.c.race_id==race_id).values(definition_json=dumps(d)))
            else:
                self.queue_race_broadcast(row,d,context,result)
            self.db.execute(races.update().where(races.c.race_id == race_id).values(settled_at=self.now))
            log.info('settlement completed race_id=%s',race_id)
            return result

    def tick(self):
        """Core background caller, each race isolated. Errors retain resumable state."""
        self.require_enabled()
        ids = self.db.execute(select(races.c.race_id).where(races.c.status.in_(['REGISTRATION','LOCKED']),
            races.c.registration_close_at <= self.now).order_by(races.c.starts_at).limit(10)).scalars().all()
        outcomes = {}
        for race_id in ids:
            try:
                with self.db.begin_nested():
                    self.lock_entries(race_id)
                    current=self.race(race_id)
                    if current['status'] == 'LOCKED' and current['starts_at'] <= self.now:
                        self.start(race_id)
                outcomes[race_id] = 'OK'
            except (CompetitionError,RacingConfigurationError) as exc:
                outcomes[race_id] = str(exc)
        return outcomes


from dzmm_bot.domain.race_runtime import analyze_performance
