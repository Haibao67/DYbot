"""Explicit offline simulation harness; never runs on import or against production."""
from copy import deepcopy
from collections import defaultdict
from decimal import Decimal


def simulate_balance(engine, context, runs):
    if type(runs) is not int or not 1 <= runs <= 10000:
        raise ValueError('runs must be 1..10000')
    totals=defaultdict(lambda:{'starts':0,'wins':0,'top3':0,'rank_sum':0,
        'remaining_stamina':Decimal(0),'overtake_attempts':0,'overtake_successes':0,'blocked':0,
        'skill_success':0,'skill_failed':0})
    for iteration in range(runs):
        sample=deepcopy(context)
        sample.race_id=f'offline:{context.race_id}:{iteration}'
        sample.rng_seed=f'{context.rng_seed}:balance:{iteration}'
        sample.rng=None
        result=engine.simulate(sample)
        for row in result['rankings']:
            item=totals[row['horse_id']]
            item['starts']+=1; item['wins']+=row['rank']==1; item['top3']+=row['rank']<=3
            item['rank_sum']+=row['rank']; item['remaining_stamina']+=Decimal(str(row['remaining_stamina']))
            for kind,key in (('OVERTAKE_ATTEMPT','overtake_attempts'),('OVERTAKE_SUCCESS','overtake_successes'),
                    ('BLOCKED','blocked'),('SKILL_ACTIVATED','skill_success'),('SKILL_FAILED','skill_failed')):
                item[key]+=sum(e['type']==kind for e in row['key_events'])
    participants={h['horse_id']:h for h in context.participants}
    return {'runs':runs,'simulation_version':context.simulation_version,'seed':context.rng_seed,
        'horses':{horse_id:{**item,'win_rate':Decimal(item['wins'])/runs,
            'top3_rate':Decimal(item['top3'])/runs,'average_rank':Decimal(item['rank_sum'])/runs,
            'average_stamina':item['remaining_stamina']/runs,
            'running_style':participants[horse_id]['running_style'],'affinity':participants[horse_id]['affinity']}
            for horse_id,item in totals.items()}}
