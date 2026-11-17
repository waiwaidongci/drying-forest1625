from fastapi import HTTPException
from sqlalchemy import select,func
from ..models.migration import MigrationState
from ..models.record import Record
from ..models.source import Source
from ..utils.serialization import row,now
from .audit import log
from .units import UNIT_PROFILES,LEGACY_PROFILE,CANONICAL_PROFILE,FORMULAS,parse_profile,to_canonical,from_canonical
from .domain_validation import validate_record
from .algorithm import calculate
TOLERANCE=1e-6
def get_job(db):
    job=db.get(MigrationState,1)
    if not job:
        job=MigrationState(id=1,state='idle',cursor=0,updated_at=now());db.add(job);db.flush()
    return job
def _count(db,*where):
    return db.scalar(select(func.count()).select_from(Record).where(*where))
def _counts(db):
    return {'total':_count(db),'ready':_count(db,Record.unit_state=='ready'),'pending':_count(db,Record.unit_state=='pending'),'quarantined':_count(db,Record.unit_state=='quarantined')}
def _per_source(db):
    out=[]
    for s in db.scalars(select(Source).order_by(Source.id)):
        c={st:_count(db,Record.source_id==s.id,Record.unit_state==st) for st in ('ready','pending','quarantined')}
        out.append({'source_id':s.id,'name':s.name,**c,'total':sum(c.values())})
    return out
def _examples(db,limit=3):
    rows=db.scalars(select(Record).where(Record.unit_state=='ready',Record.original_values.isnot(None)).order_by(Record.id).limit(limit))
    return [{'reference':r.reference,'unit':r.unit,'original':r.original_values,'canonical':r.values} for r in rows]
def _quarantine(db,limit=100):
    rows=db.scalars(select(Record).where(Record.unit_state=='quarantined').order_by(Record.id).limit(limit))
    return [{'id':r.id,'source_id':r.source_id,'reference':r.reference,'unit':r.unit,'values':r.values} for r in rows]
def status(db):
    job=get_job(db);db.commit()
    return {'state':job.state,'cursor':job.cursor,'verify_report':job.verify_report,'started_at':job.started_at,'verified_at':job.verified_at,'activated_at':job.activated_at,'updated_at':job.updated_at,'counts':_counts(db),'per_source':_per_source(db),'examples':_examples(db),'quarantine':_quarantine(db),'formulas':FORMULAS,'canonical_profile':CANONICAL_PROFILE,'legacy_profile':LEGACY_PROFILE,'unit_profiles':list(UNIT_PROFILES)}
def _convert_record(r):
    '''单条换算：未知声明或换算后不满足业务校验的进入隔离；成功则保留原值并写入 K/s。'''
    unit=r.unit or LEGACY_PROFILE
    if not parse_profile(unit):r.unit_state='quarantined';return 'quarantined'
    try:canonical=validate_record(to_canonical(r.values,unit))
    except ValueError:r.unit_state='quarantined';return 'quarantined'
    if r.original_values is None:r.original_values=r.values
    r.unit=unit;r.values=canonical;r.unit_state='ready';return 'ready'
def sweep(db,user,data):
    '''分批回填：每批一个事务，只取 pending 记录；中断后重入不重复换算，期间新增样本随后续批次补齐。'''
    job=get_job(db)
    if job.state=='idle':job.state='backfilling';job.started_at=now()
    rows=db.scalars(select(Record).where(Record.unit_state=='pending').order_by(Record.id).limit(data.batch_size)).all()
    converted=quarantined=0
    for r in rows:
        outcome=_convert_record(r)
        converted+=outcome=='ready';quarantined+=outcome=='quarantined'
        job.cursor=max(job.cursor or 0,r.id)
    if converted and job.state=='verified':job.state='backfilling'  # 核对后又有换算，原核对失效需重新核对
    job.updated_at=now()
    log(db,user,'migration.sweep',job.id,f'本批换算{converted}条 · 隔离{quarantined}条 · 游标{job.cursor}')
    db.commit()
    return {'converted':converted,'quarantined':quarantined,'remaining':_count(db,Record.unit_state=='pending'),'state':job.state}
def verify(db,user):
    '''核对：全部已换算记录重算比对 + 往返回核；无待换算且无不一致才通过，通过后才允许切换生效。'''
    job=get_job(db)
    counts=_counts(db);checked=mismatches=0;examples=[]
    for r in db.scalars(select(Record).where(Record.unit_state=='ready',Record.original_values.isnot(None))):
        checked+=1
        recomputed=to_canonical(r.original_values,r.unit)
        back=from_canonical(r.values,r.unit)
        ok=all(abs(recomputed[k]-r.values[k])<=TOLERANCE*max(1,abs(r.values[k])) for k in ('temperature','minutes'))
        rt=all(abs(back[k]-float(r.original_values[k]))<=TOLERANCE*max(1,abs(float(r.original_values[k]))) for k in ('temperature','minutes'))
        if not(ok and rt):
            mismatches+=1
            if len(examples)<5:examples.append({'reference':r.reference,'unit':r.unit})
    passed=counts['pending']==0 and mismatches==0
    report={'checked':checked,'mismatches':mismatches,'mismatch_examples':examples,'pending':counts['pending'],'quarantined':counts['quarantined'],'passed':passed,'verified_at':now(),'tolerance':'相对误差 ≤ 1e-6（换算重算与往返回核）'}
    job.verify_report=report;job.updated_at=now()
    if passed and job.state in ('idle','backfilling'):job.state='verified';job.verified_at=now()
    log(db,user,'migration.verified' if passed else 'migration.verify_failed',job.id,f'核对{checked}条 · 不一致{mismatches}条 · 待换算{counts["pending"]}条')
    db.commit();return report
def activate(db,user):
    job=get_job(db)
    if job.state!='verified':raise HTTPException(409,'换算核对通过后才能切换生效')
    pending=_count(db,Record.unit_state=='pending')
    if pending:raise HTTPException(409,f'仍有{pending}条待换算样本，请回填后重新核对')
    job.state='active';job.activated_at=now();job.updated_at=now()
    log(db,user,'migration.activated',job.id,'开尔文/秒单位版本生效')
    db.commit();return row(job)
def resolve_quarantine(db,user,data):
    if not parse_profile(data.unit):raise HTTPException(422,'未知单位声明：'+str(data.unit))
    resolved=0;failed=[]
    for rid in data.ids:
        r=db.get(Record,rid)
        if not r or r.unit_state!='quarantined':continue
        try:canonical=validate_record(to_canonical(r.values,data.unit))
        except ValueError as e:failed.append({'reference':r.reference,'reason':str(e)});continue
        if r.original_values is None:r.original_values=r.values
        r.unit=data.unit;r.values=canonical;r.unit_state='ready';resolved+=1
    if resolved:
        job=get_job(db)
        if job.state=='verified':job.state='backfilling';job.updated_at=now()
    log(db,user,'migration.resolved','quarantine',f'按{data.unit}解除{resolved}条 · 失败{len(failed)}条')
    db.commit()
    if failed and not resolved:raise HTTPException(422,'按该声明换算后未通过业务校验：'+failed[0]['reason'])
    return {'resolved':resolved,'failed':failed}
def verify_equivalence(db,user,run_id):
    '''预测等价核对：把冻结输入换算为对侧单位重算确定性算法，比对留出预测。
    口径：特征子集一致 + 存储结果可复算 + 留出MAE相对差≤10%；逐行偏差如实报告
    （float32 分裂阈值在不同量级单位下存在固有舍入差，公式级错误会呈数量级差异）。'''
    from .runs import get_run
    r=get_run(db,run_id)
    if r.state!='completed':raise HTTPException(409,'仅已完成任务可核对预测等价')
    records=r.snapshot['records'];params=r.parameters
    basis=r.unit_basis or LEGACY_PROFILE
    canonical=basis==CANONICAL_PROFILE
    converted=[{**rec,'values':from_canonical(rec['values'],LEGACY_PROFILE) if canonical else to_canonical(rec['values'],basis)} for rec in records]
    tu_a='s' if canonical else 'min';tu_b='min' if canonical else 's'
    a=calculate(records,params,tu_a);b=calculate(converted,params,tu_b)
    suffix_a='seconds' if canonical else 'minutes';suffix_b='minutes' if canonical else 'seconds'
    key_a='predicted_'+suffix_a;key_b='predicted_'+suffix_b
    stored=r.result or {'rows':[]}
    stored_match=len(stored.get('rows',[]))==len(a['rows']) and all(abs(x[key_a]-y[key_a])<=1e-6*max(1,abs(y[key_a])) for x,y in zip(a['rows'],stored['rows']))
    scale=60 if canonical else 1/60
    devs=[abs(x[key_a]-y[key_b]*scale) for x,y in zip(a['rows'],b['rows'])]
    mae_run=sum(abs(x['error_'+suffix_a]) for x in a['rows'])/len(a['rows'])
    mae_round=sum(abs(y['error_'+suffix_b]*scale) for y in b['rows'])/len(b['rows'])
    mae_delta_rel=abs(mae_run-mae_round)/max(mae_run,1e-9)
    max_dev=max(devs) if devs else 0.0
    mean_dev=sum(devs)/len(devs) if devs else 0.0
    features_match=a['selected_features']==b['selected_features']
    passed=features_match and stored_match and mae_delta_rel<=0.1
    samples=[{'reference':x['reference'],'run_prediction':x[key_a],'roundtrip_prediction':y[key_b]*scale,'deviation':abs(x[key_a]-y[key_b]*scale)} for x,y in list(zip(a['rows'],b['rows']))[:10]]
    report={'run_id':r.id,'basis':basis,'counter_basis':LEGACY_PROFILE if canonical else CANONICAL_PROFILE,'time_unit':tu_a,'checked_rows':len(devs),'mae_run':mae_run,'mae_roundtrip':mae_round,'mae_delta_relative':mae_delta_rel,'mean_deviation':mean_dev,'max_deviation':max_dev,'features_match':features_match,'stored_matches_recompute':stored_match,'passed':passed,'criterion':'特征子集一致 · 存储结果可复算 · 留出MAE相对差≤10%（float32 分裂阈值在不同量级单位下存在固有舍入差；换算公式错误会呈数量级差异）','samples':samples}
    log(db,user,'run.equivalence_checked',r.id,f'MAE相对差{mae_delta_rel:.3%} · 平均偏差{mean_dev:.3g}{tu_a} · '+('通过' if passed else '未通过'))
    db.commit();return report
