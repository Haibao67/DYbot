"""R1.3 immutable identities. No registration, grants or race opening at import."""
from dataclasses import replace
from .horse_racing import SkillDefinition, SkillRegistry
from .skill_catalog import CATALOG, linear_curve, _r12_execution_registry

VERSION='1.3'


def registry(*, strategy_costs=None):
    drafts={r['skill_id']:r for r in CATALOG['skills']}
    rows=[]
    for row in _r12_execution_registry().definitions():
        code=row.code.removeprefix('R12_')
        effects=tuple({**e,'stack_policy':'REFRESH_DURATION',
            **({'operation':'ADD'} if e.get('stat_or_mechanic') == 'overtake' or e['type'] == 'IMPROVE_OVERTAKE' else {})} for e in row.effect_definition)
        rows.append(replace(row,skill_id='R13_'+code,code='R13_'+code,version=VERSION,effect_definition=effects,
            max_attempts=row.max_activations if row.type != 'PASSIVE' else None,
            pools=('inheritance_random_pool',) if drafts[code]['inheritance_random_pool'] else ()))
    def effect(mechanic,key='value',operation='MULTIPLY',**kwargs):
        return dict(type='APPLY_TEMPORARY_MODIFIER',stat_or_mechanic=mechanic,scaling_key=key,
                    operation=operation,stack_policy='REFRESH_DURATION',**kwargs)
    def add(code,trigger,effects,curves,success=1,attempts=None,cooldown=0,per_phase=None,enabled=True):
        row=drafts[code]
        curves={key:linear_curve(dict(zip((1,5,10),values))) for key,values in curves.items()}
        scaling={level:{key:curve[level] for key,curve in curves.items()} for level in range(1,11)}
        rows.append(SkillDefinition(skill_id='R13_'+code,code='R13_'+code,display_name=row['display_name'],type=row['type'],
            description=row['source_rules'],trigger_definition=trigger,effect_definition=tuple(effects),level_scaling=scaling,
            activation_policy='MULTIPLE' if success>1 else 'ONCE_PER_RACE',max_activations=success,
            cooldown_checkpoints=cooldown,tags=tuple(row['tags']),pools=('inheritance_random_pool',) if row['inheritance_random_pool'] and enabled else (),
            enabled=enabled,version=VERSION,max_attempts=attempts,max_attempts_per_phase=per_phase))
    cp={'duration_checkpoints':1}
    def flag(code,hook='on_checkpoint',**kwargs): return {'hook':hook,'flags':{'r13_'+code:True},**kwargs}
    add('P08',{'running_style':'front'},[effect('stamina_consumption','cost',conditions={'distance_before':'.50'}),
        effect('position_control','position','ADD')],{'cost':('-.015','-.035','-.06'),'position':('.01','.03','.05')})
    add('P09',{'running_style':'stalker'},[effect('position_control',operation='ADD',conditions={'percentile_max':'.40'})],{'value':('.02','.05','.08')})
    add('P10',{'running_style':'mid'},[effect('stamina_consumption','cost',conditions={'distance_before':'.65'}),
        effect('overtake','overtake','ADD',conditions={'progress_min':'.65'})],{'cost':('-.01','-.025','-.04'),'overtake':('.01','.03','.05')})
    add('P11',{'running_style':'closer'},[effect('stamina_consumption','cost',conditions={'distance_before':'.50'}),
        effect('grit','grit',conditions={'progress_min':'.80'})],{'cost':('-.015','-.03','-.05'),'grit':('.01','.03','.05')})
    add('P14',{},[effect('acceleration',conditions={'phases':['CORNER_BATTLE','FINAL_CORNER']}),
        effect('exit_acceleration',conditions={'exit_corner':True})],{'value':('.02','.04','.07')})
    add('C02',flag('C02'),[effect('position_push',**cp),effect('stamina_consumption','cost',**cp)],
        {'value':('.04','.08','.13'),'cost':('.02','.01','0')})
    add('C05',flag('C05'),[effect('speed',**cp)],{'value':('.03','.06','.09')})
    add('C08',flag('C08'),[effect('exit_recovery_acceleration',**cp)],{'value':('.05','.10','.16')},2)
    add('C09',flag('C09','on_overtake_attempt'),[effect('overtake',operation='ADD',**cp)],{'value':('.04','.08','.13')},2,cooldown=1)
    add('C10',flag('C10','on_block_cleared'),[effect('acceleration',**cp)],{'value':('.05','.10','.15')})
    add('C11',flag('C11','on_overtake_success'),[effect('speed',delay_checkpoints=1,**cp)],{'value':('.02','.045','.075')},2)
    add('C12',flag('C12'),[effect('acceleration',**cp)],{'value':('.03','.06','.10')},2,cooldown=1)
    add('C13',flag('C13'),[effect('acceleration','accel',**cp),effect('speed','speed',**cp)],
        {'accel':('.03','.06','.10'),'speed':('.01','.025','.04')})
    add('C16',flag('C16','on_duel_computed'),[effect('duel_speed','speed',**cp),effect('duel_grit','grit',**cp)],
        {'speed':('.02','.04','.06'),'grit':('.03','.07','.12')})
    add('W02',flag('W02'),[effect('pace_correction',operation='ADD',**cp)],{'value':('.25','.50','.80')},2,3,1)
    add('W03',flag('W03'),[effect('overpace_reduction',operation='ADD',**cp)],{'value':('.15','.35','.60')},2,3,1)
    add('W04',flag('W04','on_route_decision'),[effect('route_inner',operation='ADD',**cp)],{'value':('.02','.05','.08')},2,3,1)
    add('W05',flag('W05','on_route_decision'),[effect('preemptive_lane',operation='ADD',**cp)],{'value':('.05','.12','.20')},2,3)
    add('W06',flag('W06','on_blocked'),[effect('block_penalty',**cp)],{'value':('-.15','-.35','-.60')},2,3,1)
    add('W07',flag('W07','on_overtake_attempt'),[effect('overtake',operation='ADD',**cp)],{'value':('.04','.09','.15')},2,4,1)
    add('W11',flag('W11'),[effect('position_control',operation='ADD',**cp),effect('congestion_penalty','congestion',**cp)],
        {'value':('.03','.07','.12'),'congestion':('-.10','-.10','-.10')},2,3)
    add('W12',flag('W12','on_route_decision'),[effect('counter_decision',operation='ADD',**cp)],{'value':('.04','.10','.18')},2,3)
    add('W13',flag('W13','on_route_decision'),[effect('route_loss_reduction',operation='ADD',**cp)],{'value':('.03','.07','.12')},2,2,per_phase=1)
    add('W14',flag('W14','on_route_decision'),[effect('route_strategy',operation='ADD',expires_phase='FINAL_CORNER')],
        {'value':('.04','.09','.15')},1,1)
    add('W15',flag('W15'),[effect('fatigue_penalty',expires_phase='FINAL_STRAIGHT')],{'value':('-.03','-.07','-.12')},1,1)
    add('W16',flag('W16','on_route_decision'),[effect('route_grip',operation='ADD',**cp)],{'value':('.04','.09','.15')},2,3)
    return SkillRegistry(rows)
