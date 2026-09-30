"""P8 race reminders from persisted entries only; no unopened feature advertising."""
from sqlalchemy import select
from dzmm_bot.persistence.schema import race_definitions as races, race_entries as entries
from dzmm_bot.presentation.formatters import name_escape


class RaceTodoProvider:
    def __init__(self,db,player,now):
        self.db,self.player,self.now=db,player,now

    def items(self):
        rows=self.db.execute(select(races.c.race_id,races.c.name,races.c.status,
            races.c.registration_close_at,races.c.starts_at).join(entries,entries.c.race_id == races.c.race_id).where(
            entries.c.player_id == self.player,entries.c.status.in_(['REGISTERED','LOCKED']),
            races.c.status.in_(['REGISTRATION','LOCKED','RUNNING'])).distinct().order_by(races.c.starts_at).limit(5)).mappings()
        result=[]
        for row in rows:
            due=row['starts_at']
            label='开赛'
            result.append({'type':'race','priority':1,'due_at':due,
                'text':f"🏇【{name_escape(row['name'])}】约 {max(0,int((due-self.now+59)//60))} 分钟后{label}",
                'action_command':'/比赛详情 '+row['race_id']})
        if not result:
            row=self.db.execute(select(races).where(races.c.status=='REGISTRATION',
                races.c.registration_open_at<=self.now,races.c.registration_close_at>self.now)
                .order_by(races.c.starts_at).limit(1)).mappings().first()
            if row:
                result.append({'type':'race','priority':2,'due_at':row['registration_close_at'],
                    'text':f"🏇【{name_escape(row['name'])}】还有 {max(0,int((row['registration_close_at']-self.now)/60))}分钟截止报名",
                    'action_command':'/赛程'})
        return result
