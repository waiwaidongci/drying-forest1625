from sqlalchemy import select,func
from fastapi import HTTPException
from redis import Redis
from ..config.settings import REDIS_URL
from ..models.run import Run
from ..models.record import Record
from ..utils.serialization import row,now
from .sources import source_or_404,list_records
from .domain_validation import validate_parameters
from .audit import log
from .migration import get_job
from .units import LEGACY_PROFILE,CANONICAL_PROFILE,from_canonical
queue=Redis.from_url(REDIS_URL)
def get_run(db,id):
    r=db.get(Run,id)
    if not r:raise HTTPException(404,'未找到分析任务')
    return r
def create_run(db,user,data):
    '''冻结只取完整一致的单位版本：迁移未开始用旧 ℃/min 口径（已换算记录等价回核），开始迁移后只取 K/s 现行版本。'''
    s=source_or_404(db,data.source_id)
    job=get_job(db)
    records=[];basis=LEGACY_PROFILE
    if job.state=='idle':
        for r in list_records(db,s.id):
            if r['unit_state']=='quarantined':continue
            if r['unit_state']=='ready':r={**r,'values':from_canonical(r['values'],LEGACY_PROFILE)}
            records.append(r)
    else:
        basis=CANONICAL_PROFILE
        records=[r for r in list_records(db,s.id) if r['unit_state']=='ready']
    if len(records)<3:
        raise HTTPException(422,'至少录入3条样本后才能执行' if job.state=='idle' else f'当前仅{len(records)}条样本完成单位换算（K/s），可冻结样本不足')
    try: params=validate_parameters(data.parameters)
    except ValueError as e:raise HTTPException(422,str(e))
    total=db.scalar(select(func.count()).select_from(Record).where(Record.source_id==s.id))
    r=Run(source_id=s.id,name=data.name,parameters=params,unit_basis=basis,snapshot={'source':row(s),'records':records,'unit_basis':basis,'eligible':len(records),'total':total},created_at=now(),state='queued')
    db.add(r);db.flush();log(db,user,'run.queued',r.id,data.name);db.commit()
    try:queue.rpush('analysis:queue',r.id)
    except Exception:
        r.state='failed';r.error='队列暂不可用，请重新创建任务';db.commit();raise HTTPException(503,r.error)
    return row(r)
def list_runs(db):
    return [{k:v for k,v in row(r).items() if k not in ('snapshot','result')} for r in db.scalars(select(Run).order_by(Run.id.desc()))]
