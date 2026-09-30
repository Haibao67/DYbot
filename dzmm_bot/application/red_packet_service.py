"""One active packet per group, cent-exact escrow, bounded random claims."""
import random
from sqlalchemy import select
from dzmm_bot.domain.economy import GameError, to_minor, from_minor, seed_for
from dzmm_bot.persistence.schema import red_packets, red_packet_claims, world
from dzmm_bot.persistence.transport import players
from .services import GameService, uid

DEFAULT_DURATION_MINUTES = 2
MAX_DURATION_MINUTES = 100000
INACTIVITY_SECONDS = DEFAULT_DURATION_MINUTES * 60


def expire_packets(db, now, secret):
    db.execute(select(world.c.id).where(world.c.id==1).with_for_update()).first()
    rows=db.execute(select(red_packets).where(red_packets.c.status=='active',
        red_packets.c.expires_at<=now).order_by(red_packets.c.id).with_for_update()).mappings().all()
    for row in rows:
        sender=GameService(db,now,secret,row['sender_id'],row['room_id'],row['id'])
        sender.ledger(from_minor(row['remaining_amount']),'red_packet_refund','red_packet_refund',row['id'])
        db.execute(red_packets.update().where(red_packets.c.id==row['id']).values(
            status='expired',remaining_amount=0,remaining_count=0))
    return len(rows)


class RedPacketService(GameService):
    def send(self, amount, count, duration_minutes=DEFAULT_DURATION_MINUTES):
        expire_packets(self.db,self.now,self.secret)
        self.account(True)
        if not 1<=count<=100:
            raise GameError('red_packet_count')
        if type(duration_minutes) is not int or not 1 <= duration_minutes <= MAX_DURATION_MINUTES:
            raise GameError('red_packet_duration')
        duration_seconds = duration_minutes * 60
        total=to_minor(amount)
        lower=(total+2*count-1)//(2*count)
        upper=3*total//(2*count)
        if not lower*count<=total<=upper*count:
            raise GameError('red_packet_precision')
        if self.db.execute(select(red_packets.c.id).where(red_packets.c.room_id==self.room,
            red_packets.c.status=='active')).first():
            raise GameError('red_packet_active')
        packet_id=uid()
        self.ledger(-amount,'red_packet_send','red_packet_send',packet_id)
        self.db.execute(red_packets.insert().values(id=packet_id,room_id=self.room,sender_id=self.player,
            total_amount=total,quantity=count,remaining_amount=total,remaining_count=count,
            status='active',created_at=self.now,expires_at=self.now+duration_seconds,
            duration_seconds=duration_seconds))
        self.event('red_packet_send',{'packet_id':packet_id,'amount':str(amount),'count':count,
            'duration_minutes':duration_minutes})
        return {'kind':'red_packet_send','amount':amount,'count':count,
            'duration_minutes':duration_minutes,'balance':self.balance()}

    def end(self):
        expire_packets(self.db,self.now,self.secret)
        row=self.db.execute(select(red_packets).where(red_packets.c.room_id==self.room,
            red_packets.c.status=='active').with_for_update()).mappings().first()
        if not row:
            raise GameError('red_packet_none')
        refund = from_minor(row['remaining_amount'])
        if row['remaining_amount']:
            sender=GameService(self.db,self.now,self.secret,row['sender_id'],row['room_id'],row['id'])
            sender.ledger(refund,'red_packet_admin_refund','red_packet_admin_end',row['id'])
        self.db.execute(red_packets.update().where(red_packets.c.id==row['id'],
            red_packets.c.status=='active').values(status='expired',remaining_amount=0,
                remaining_count=0,expires_at=self.now))
        sender_name=self.db.execute(select(players.c.name).where(players.c.id==row['sender_id'])).scalar_one()
        self.event('red_packet_admin_end',{'packet_id':row['id'],'refund':str(refund)})
        return {'kind':'red_packet_end','sender':sender_name,'refund':refund}

    def claim(self):
        expire_packets(self.db,self.now,self.secret)
        self.account(True)
        row=self.db.execute(select(red_packets).where(red_packets.c.room_id==self.room,
            red_packets.c.status=='active').with_for_update()).mappings().first()
        if not row:
            raise GameError('red_packet_none')
        if row['sender_id']==self.player:
            raise GameError('red_packet_self')
        if self.db.execute(select(red_packet_claims.c.player_id).where(
            red_packet_claims.c.packet_id==row['id'],red_packet_claims.c.player_id==self.player)).first():
            raise GameError('red_packet_claimed')
        total,n=row['total_amount'],row['quantity']
        lower=(total+2*n-1)//(2*n)
        upper=3*total//(2*n)
        remaining,left=row['remaining_amount'],row['remaining_count']
        low=max(lower,remaining-upper*(left-1))
        high=min(upper,remaining-lower*(left-1))
        rng=random.Random(seed_for(self.secret,row['id'],'red-packet-v1',str(n-left)))
        received=rng.randint(low,high)
        self.ledger(from_minor(received),'red_packet_claim','red_packet_claim',row['id'])
        self.db.execute(red_packet_claims.insert().values(packet_id=row['id'],player_id=self.player,
            amount=received,claimed_at=self.now))
        self.db.execute(red_packets.update().where(red_packets.c.id==row['id']).values(
            remaining_amount=remaining-received,remaining_count=left-1,
            status='completed' if left==1 else 'active',
            expires_at=self.now+row['duration_seconds']))
        sender=self.db.execute(select(players.c.name).where(players.c.id==row['sender_id'])).scalar_one()
        self.event('red_packet_claim',{'packet_id':row['id'],'amount':received})
        return {'kind':'red_packet_claim','amount':from_minor(received),'sender':sender,
            'remaining':left-1,'balance':self.balance()}
