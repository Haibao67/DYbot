"""Minute reconciliation, durable identities and failure isolation; no platform calls."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import logging
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from dzmm_bot.domain.race_runtime import DailyRaceTemplate, RaceRuntimeFactory
from dzmm_bot.persistence.schema import race_definitions as races, audit
from .competition_service import CompetitionService, dumps
from .horse_racing_service import HorseRacingService
from .services import uid

log=logging.getLogger(__name__)


class RaceBootstrap:
    @staticmethod
    def initialize(db, now, template=None):
        template=template or DailyRaceTemplate()
        template.definition(datetime.fromtimestamp(now,ZoneInfo(template.timezone)).date())
        runtime=RaceRuntimeFactory.build()
        HorseRacingService(db,now,registry=runtime.skill_registry).register_definitions(runtime.skill_registry)
        return runtime


class DailyRaceScheduler:
    def __init__(self, db, now, template=None):
        self.db,self.now,self.template=db,now,template or DailyRaceTemplate()

    def ensure_daily_races(self, target_date):
        if not self.template.enabled: return None
        definition=self.template.definition(target_date)
        current=self.db.execute(select(races.c.race_id).where(
            races.c.template_id==definition['template_id'],races.c.race_date==definition['race_date'])).scalar()
        if current:
            row=self.db.execute(select(races).where(races.c.race_id==current).with_for_update()).mappings().one()
            if row['status'] in ('DRAFT','REGISTRATION') and not row['snapshot_json']:
                previous=json.loads(row['definition_json'])
                if previous['max_horses'] != definition['max_horses']:
                    previous['max_horses']=definition['max_horses']
                    self.db.execute(races.update().where(races.c.race_id==current).values(definition_json=dumps(previous)))
            return current
        try:
            with self.db.begin_nested():
                self.db.execute(races.insert().values(race_id=definition['race_id'],
                    template_id=definition['template_id'],race_date=definition['race_date'],name=definition['name'],
                    definition_json=dumps(definition),status='DRAFT',registration_open_at=definition['registration_open_at'],
                    registration_close_at=definition['registration_close_at'],starts_at=definition['starts_at'],created_at=self.now))
            log.info('daily race created race_id=%s template_id=%s version=1.3',definition['race_id'],definition['template_id'])
        except IntegrityError:
            current=self.db.execute(select(races.c.race_id).where(races.c.template_id==definition['template_id'],
                races.c.race_date==definition['race_date'])).scalar()
            if not current: raise
            return current
        return definition['race_id']

    def ensure_upcoming(self):
        date=datetime.fromtimestamp(self.now,ZoneInfo(self.template.timezone)).date()
        return [self.ensure_daily_races(date+timedelta(days=offset)) for offset in (0,1)]


class RaceExecutionService:
    def __init__(self, competition): self.competition=competition
    def execute_due_race(self,race_id): return self.competition.start(race_id)


class RaceStateReconciler:
    def __init__(self, db, now, secret, race_commentary_options=None):
        self.db,self.now,self.secret=db,now,secret
        self.race_commentary_options=dict(race_commentary_options or {})

    def reconcile(self):
        ids=self.db.execute(select(races.c.race_id).where(races.c.template_id.is_not(None),
            (races.c.status.in_(['DRAFT','REGISTRATION','LOCKED','RUNNING'])) |
            ((races.c.status=='FINISHED') & races.c.settled_at.is_(None)))
            .order_by(races.c.starts_at).limit(30)).scalars().all()
        outcomes={}
        for race_id in ids:
            stage='RUNTIME_VALIDATION_FAILED'
            service=CompetitionService(self.db,self.now,self.secret,
                race_commentary_options=self.race_commentary_options)
            definition={}
            try:
                row=service.race(race_id); definition=json.loads(row['definition_json'])
                if row['status']=='DRAFT' and self.now>=row['registration_open_at']:
                    service.transition(row,'REGISTRATION'); row=service.race(race_id)
                    log.info('registration opened race_id=%s',race_id)
                if row['status']=='REGISTRATION' and self.now>=row['registration_close_at']:
                    service.transition(row,'LOCKED'); row=service.race(race_id)
                    log.info('registration locked race_id=%s',race_id)
                if row['status'] not in ('LOCKED','RUNNING','FINISHED'):
                    outcomes[race_id]=row['status']; continue
                # Keep LOCKED durable if any validation/fill/simulation step fails.
                with self.db.begin_nested():
                    versions=(json.loads(row['snapshot_json'])['rule_metadata']['versions']
                              if row['snapshot_json'] else definition['versions'])
                    runtime=RaceRuntimeFactory.build(versions)
                    HorseRacingService(self.db,self.now,registry=runtime.skill_registry).register_definitions(runtime.skill_registry)
                    service.engine,service.npc_factory=runtime.engine,runtime.npc_factory
                    if row['status']=='LOCKED' and not row['snapshot_json']:
                        stage='SNAPSHOT_GENERATION_FAILED'
                        service.lock_entries(race_id)
                        if service.race(race_id)['snapshot_json']:
                            log.info('snapshot generated race_id=%s template_id=%s versions=%s',race_id,
                                     definition.get('template_id'),definition.get('versions'))
                    current=service.race(race_id)
                    if current['status'] in ('LOCKED','RUNNING','FINISHED') and self.now>=current['starts_at']:
                        stage='RACE_EXECUTION_OR_SETTLEMENT_FAILED'
                        RaceExecutionService(service).execute_due_race(race_id)
                        log.info('race finished settlement completed race_id=%s',race_id)
                    outcomes[race_id]=service.race(race_id)['status']
            except Exception as exc:
                # Retain a recoverable state and make the error observable; no silent skill filtering.
                stage=getattr(service,'runtime_stage',stage)
                log.exception('%s race_id=%s template_id=%s version=%s',stage,race_id,
                    definition.get('template_id'),definition.get('versions'))
                self.db.execute(audit.insert().values(id=uid(),actor='race_scheduler',action=stage,
                    target_type='race',target_id=race_id,payload_json=dumps({'error':str(exc),
                        'template_id':definition.get('template_id'),'versions':definition.get('versions')}),created_at=self.now))
                outcomes[race_id]=stage
        return outcomes
