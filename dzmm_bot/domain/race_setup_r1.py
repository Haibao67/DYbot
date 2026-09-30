"""Approved fixed schedule and NORMAL NPC configuration; explicit caller activation."""
from datetime import datetime, time
from .economy import HK
from .horse_racing import AFFINITY_KEYS, stable_rng, RacingConfigurationError
from .race_rules_r1 import FIXED_RACE, race_affinity
from .race_rules_r11 import VERSION
from .race_npc import NpcHorseTemplate, NpcFactory

NPC_VERSION='npc_template_v1'
TRAITS=('speed','stamina','power','wisdom','grit')
NPC_ROWS=(
    ('A','领放型',((700,780),(620,700),(650,730),(600,690),(520,620)),('A','B','A','B','C'),
     ('C','A','A','C'),('A','C'),('S','B','D','D'),'front',('P08','C01','C03','W03','W02','C07','P13')),
    ('B','稳定先行型',((660,740),(650,730),(620,700),(680,760),(560,650)),('B','B','B','A','C'),
     ('C','B','S','B'),('A','C'),('B','S','B','C'),'stalker',('P09','C04','C05','C06','W02','W13','C14')),
    ('C','力量差马',((650,730),(620,700),(720,800),(620,700),(620,720)),('B','B','A','B','B'),
     ('C','B','A','B'),('A','B'),('D','B','S','B'),'mid',('P10','C07','C09','W06','W07','C13','C14')),
    ('D','毅力追马',((670,760),(600,690),(650,740),(580,660),(740,830)),('A','B','B','C','A'),
     ('D','C','A','A'),('A','C'),('D','C','B','S'),'closer',('P11','P15','C14','C13','W07','W08','W10')),
    ('E','智慧战术型',((630,710),(640,720),(580,660),(760,850),(560,640)),('B','B','C','S','C'),
     ('C','A','S','B'),('A','C'),('C','A','A','B'),'stalker',('P13','W01','W02','W03','W05','W06','W13')),
    ('F','均衡型',((640,720),(630,710),(630,710),(630,710),(630,710)),('B','B','B','B','B'),
     ('B','B','A','B'),('A','B'),('B','A','A','B'),'stalker',('P13','C05','C06','C07','C14','W02','W13')),
)


def daily_definition(now,rng_seed):
    date=datetime.fromtimestamp(now,HK).date()
    def stamp(text):
        return datetime.combine(date,time.fromisoformat(text),HK).timestamp()
    return {'race_id':'ruihe-'+date.strftime('%Y%m%d'),'name':FIXED_RACE['name'],
        'distance':2000,'distance_type':'medium','surface':'turf','track_condition':'良','weather':'晴',
        'min_horses':8,'max_horses':10,'min_real_players':2,'max_entries_per_player':1,
        'registration_open_at':stamp('12:00'),'registration_close_at':stamp('19:45'),'starts_at':stamp('20:00'),
        'entry_fee':'50','prizes':('200','100','50'),'refund_ratio':'.90','full_refund_ratio':'1',
        'season_id':'UNSEASONED','rng_seed':rng_seed,'simulation_version':VERSION,'npc_fill':True,
        'skill_engine_version':'skill_engine_v1','affinity_version':'affinity_generation_v1',
        'npc_template_version':NPC_VERSION,'race_rule_version':'p8-r1-v1'}


def npc_templates():
    result=[]
    for code,name,ranges,growth,distance,surface,styles,style,skills in NPC_ROWS:
        affinity={category:dict(zip(AFFINITY_KEYS[category],values)) for category,values in
            (('distance',distance),('surface',surface),('running_style',styles))}
        result.append(NpcHorseTemplate('R1-'+code,(name,),dict(zip(TRAITS,ranges)),dict(zip(TRAITS,growth)),
            affinity,style,(),'NORMAL',True,skills,(1,4)))
    return tuple(result)


class R1NpcFactory:
    def __init__(self,registry):
        self.registry=registry

    def __call__(self,definition,count):
        if type(count) is not int or not 0 <= count <= 6:
            raise RacingConfigurationError('R1 NPC fill requires 2..7 real entries and fills to eight')
        templates=list(npc_templates())
        # With at least four places, reserve one template per running style.
        rng,_=stable_rng(definition['rng_seed'],NPC_VERSION,'template-order')
        if count >= 4:
            core=templates[:4]; rng.shuffle(core)
            rest=templates[4:]; rng.shuffle(rest)
            chosen=(core+rest)[:count]
        else:
            rng.shuffle(templates); chosen=templates[:count]
        result=[]
        for index,template in enumerate(chosen):
            # No silent removal of an unimplemented skill from the official pool.
            for code in template.skill_pool:
                if not self.registry.resolve_catalog_code(code).enabled:
                    raise RacingConfigurationError('Official NPC skill definition not ready: '+code)
            nested={**definition,'rng_seed':str(definition['rng_seed'])+':npc:'+str(index)}
            factory=NpcFactory(version=NPC_VERSION,templates=(template,),difficulty_pool=('NORMAL',),
                affinity_modifiers=None,affinity_resolver=race_affinity,registry=self.registry)
            horse=factory(nested,1)[0]
            horse['horse_id']=f"npc:{definition['race_id']}:{index}"
            result.append(horse)
        return result
