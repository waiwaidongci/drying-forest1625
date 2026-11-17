'''单位迁移全链路验证：SQLite + 假 Redis + TestClient。
覆盖：旧数据分批回填可续/幂等、期间新增样本不漏、声明批与隔离、冻结口径切换、
核对通过才允许切换、预测等价回核、历史任务量纲不变、schema 增量幂等。
运行：python3 -I unit_migration_check.py（不依赖仓库内模块以外的本地包）'''
import os,sys,json,tempfile,pathlib
TMP=pathlib.Path(tempfile.mkdtemp(prefix='unit-migration-'))
os.environ['DATABASE_URL']='sqlite:///'+str(TMP/'test.db')
os.environ['REDIS_URL']='redis://localhost:6399/9'
os.environ['ARTIFACT_DIR']=str(TMP/'artifacts')
os.environ['JWT_SECRET']='unit-test-secret'
os.environ['SEED_DEMO']='false'
sys.path.insert(0,'/workspace/backend')

from src.config.database import Base,engine,Session
from src.config.schema_updates import ensure_schema
from src.models.record import Record
from src.models.run import Run
from sqlalchemy import select,func
import src.services.runs as runs_svc

class FakeQueue:
    def __init__(self):self.items=[]
    def rpush(self,*a):self.items.append(a);return len(self.items)
    def blpop(self,*a,**k):return None
    def ping(self):return True
runs_svc.queue=FakeQueue()

from scripts.seed_demo import seed
import src.worker as worker
from src.services.units import to_canonical,from_canonical

PASS=[];FAIL=[]
def check(name,cond,detail=''):
    (PASS if cond else FAIL).append(name)
    print(('✓ ' if cond else '✗ ')+name+((' · '+str(detail)) if detail and not cond else ''))

Base.metadata.create_all(engine)
ensure_schema(engine);ensure_schema(engine)  # 幂等
seed();seed()  # 幂等：不重复、不覆盖

from fastapi.testclient import TestClient
from src.main import app
client=TestClient(app)
def login(email):
    r=client.post('/auth/login',json={'email':email,'password':'Research2026!'})
    assert r.status_code==200,(email,r.text)
    return {'Authorization':'Bearer '+r.json()['token']}
ADMIN=login('admin@drying-forest.demo');ANALYST=login('analyst@drying-forest.demo');VIEWER=login('viewer@drying-forest.demo')

def run_one(run_id):
    worker.execute(run_id)
    with Session() as db:return db.get(Run,run_id)

with Session() as db:
    total=db.scalar(select(func.count()).select_from(Record))
    pending=db.scalar(select(func.count()).select_from(Record).where(Record.unit_state=='pending'))
    ready=db.scalar(select(func.count()).select_from(Record).where(Record.unit_state=='ready'))
    quar=db.scalar(select(func.count()).select_from(Record).where(Record.unit_state=='quarantined'))
check('种子数据：720 旧记录 pending + 24 条 ℉/s ready + 6 条隔离',(total,pending,ready,quar)==(750,720,24,6),f'{total}/{pending}/{ready}/{quar}')

seed_run=run_one(1)
check('首轮种子任务完成且为历史 min 量纲',seed_run.state=='completed' and seed_run.result['unit_time']=='min' and 'actual_minutes' in seed_run.result['rows'][0])

st=client.get('/migration/status',headers=VIEWER).json()
check('迁移初始状态 idle，公式与样例齐备',st['state']=='idle' and len(st['formulas'])>=4 and len(st['examples'])>=1)

# 迁移前冻结：idle 口径 ℃/min，ready 记录等价回核，隔离不参与
r=client.post('/runs',headers=ANALYST,json={'source_id':3,'name':'迁移前对照研究','parameters':{'trees':20,'depth':4}})
check('idle 期可新建研究',r.status_code==200,r.text)
pre_run=run_one(r.json()['id'])
with Session() as db: snap=db.get(Run,pre_run.id).snapshot
check('idle 冻结取 ℃/min 一致版本（264 条，隔离 6 条排除，℉/s 回核）',snap['unit_basis']=='C/min' and len(snap['records'])==264 and all(rec['unit_state']!='quarantined' for rec in snap['records']))
check('idle 冻结温度在摄氏区间',all(0<rec['values']['temperature']<200 for rec in snap['records']))

# 旧入口录入（隐含 ℃/min 声明，待换算）
r=client.post('/sources/1/records',headers=ANALYST,json={'records':[{'reference':'HX-NEW-001','values':{'moisture':35,'thickness':4.2,'temperature':75,'airflow':3.1,'minutes':83}}]})
check('旧入口仍按 ℃/min 接收',r.status_code==200 and r.json()['added']==1,r.text)
with Session() as db:
    rec=db.scalar(select(Record).where(Record.reference=='HX-NEW-001'))
check('旧入口新样本为 pending/℃每分',rec.unit=='C/min' and rec.unit_state=='pending' and rec.values['temperature']==75)

# 新设备声明批：℉/s 直接换算并保留原值
r=client.post('/sources/3/batches',headers=ANALYST,json={'unit':'F/s','records':[{'reference':'HX-DEV-001','values':{'moisture':40,'thickness':5,'temperature':167,'airflow':2.5,'minutes':4980}}]})
check('声明批 ℉/s 接收',r.status_code==200 and r.json()['added']==1 and r.json()['quarantined']==0,r.text)
with Session() as db:
    rec=db.scalar(select(Record).where(Record.reference=='HX-DEV-001'))
check('声明批保留原值并写入 K/s',rec.unit_state=='ready' and rec.original_values['temperature']==167 and abs(rec.values['temperature']-(167-32)*5/9-273.15)<1e-9 and rec.values['minutes']==4980)

# 缺失声明 → 隔离
r=client.post('/sources/3/batches',headers=ANALYST,json={'records':[{'reference':'HX-DEV-002','values':{'moisture':40,'thickness':5,'temperature':167,'airflow':2.5,'minutes':4980}}]})
check('缺失声明的新批被隔离',r.status_code==200 and r.json()['quarantined']==1,r.text)
r=client.post('/sources/3/batches',headers=ANALYST,json={'unit':'X/y','records':[{'reference':'HX-DEV-003','values':{'moisture':40,'thickness':5,'temperature':167,'airflow':2.5,'minutes':4980}}]})
check('未知声明的新批被隔离',r.status_code==200 and r.json()['quarantined']==1,r.text)

# 分批回填：小批次 + 中断续跑 + 期间新增
r=client.post('/migration/sweep',headers=ANALYST,json={'batch_size':50})
check('首批回填 50 条并进入 backfilling',r.status_code==200 and r.json()['converted']==50 and r.json()['state']=='backfilling',r.text)
client.post('/sources/1/records',headers=ANALYST,json={'records':[{'reference':'HX-NEW-002','values':{'moisture':36,'thickness':4,'temperature':80,'airflow':3,'minutes':90}}]})
r=client.post('/migration/sweep',headers=ANALYST,json={'batch_size':30})
check('续跑批次换算 30 条',r.status_code==200 and r.json()['converted']==30,r.text)
# 回填中冻结：只取 ready 的 K/s 版本
r=client.post('/runs',headers=ANALYST,json={'source_id':1,'name':'回填中研究','parameters':{'trees':20,'depth':4}})
with Session() as db: snap=db.get(Run,r.json()['id']).snapshot
check('回填中冻结只取 K/s ready 记录',snap['unit_basis']=='K/s' and len(snap['records'])==80 and all(rec['values']['temperature']>200 for rec in snap['records']),len(snap['records']))
# 全部回填
while True:
    r=client.post('/migration/sweep',headers=ANALYST,json={'batch_size':200}).json()
    if r['remaining']==0:break
check('回填至 0 待换算（含期间新增 2 条）',r['remaining']==0)
r=client.post('/migration/sweep',headers=ANALYST,json={'batch_size':200}).json()
check('重试不重复换算',r['converted']==0)
with Session() as db:
    rec=db.scalar(select(Record).where(Record.reference=='HX-NEW-001'))
check('期间新增样本未漏且只换算一次',rec.unit_state=='ready' and abs(rec.values['temperature']-348.15)<1e-9 and rec.original_values['temperature']==75 and rec.values['minutes']==4980)

# 核对 → 切换
r=client.post('/migration/verify',headers=ANALYST)
check('核对通过',r.status_code==200 and r.json()['passed'] and r.json()['checked']>700,r.text)
r=client.post('/migration/activate',headers=ANALYST)
check('非负责人不能切换',r.status_code==403)
# 核对后又有新样本换算 → 核对失效，须重新核对才能切换
client.post('/sources/1/records',headers=ANALYST,json={'records':[{'reference':'HX-NEW-009','values':{'moisture':31,'thickness':3.5,'temperature':68,'airflow':2.8,'minutes':71}}]})
r=client.post('/migration/sweep',headers=ANALYST,json={'batch_size':100}).json()
check('核对后新增样本被换算',r['converted']==1 and r['remaining']==0,r)
st=client.get('/migration/status',headers=VIEWER).json()
check('核对后新换算使状态回退',st['state']=='backfilling',st['state'])
r=client.post('/migration/activate',headers=ADMIN)
check('核对失效后切换被拒',r.status_code==409,r.text)
r=client.post('/migration/verify',headers=ANALYST)
check('重新核对通过',r.status_code==200 and r.json()['passed'])
r=client.post('/migration/activate',headers=ADMIN)
check('核对通过后切换生效',r.status_code==200 and r.json()['state']=='active',r.text)
st=client.get('/migration/status',headers=VIEWER).json()
check('状态页显示已生效',st['state']=='active' and st['counts']['pending']==0)

# 切换后旧入口新增 → 继续分批换算
client.post('/sources/1/records',headers=ANALYST,json={'records':[{'reference':'HX-NEW-003','values':{'moisture':33,'thickness':3.8,'temperature':70,'airflow':2.9,'minutes':77}}]})
st=client.get('/migration/status',headers=VIEWER).json()
check('切换后旧入口新增进入待换算',st['counts']['pending']==1)
r=client.post('/migration/sweep',headers=ANALYST,json={'batch_size':100}).json()
check('切换后回填仍可用且不重复',r['converted']==1 and r['remaining']==0)

# 隔离解除
with Session() as db:
    qid=db.scalar(select(Record.id).where(Record.reference=='HX-DEV-002'))
r=client.post('/migration/quarantine/resolve',headers=ANALYST,json={'ids':[qid],'unit':'F/s'})
check('隔离样本声明 ℉/s 后解除',r.status_code==200 and r.json()['resolved']==1,r.text)
rr=client.post('/migration/quarantine/resolve',headers=ANALYST,json={'ids':[qid],'unit':'X/y'})
check('未知单位声明被拒绝',rr.status_code==422)

# K/s 冻结研究 + worker 实算 + 预测等价回核
r=client.post('/runs',headers=ANALYST,json={'source_id':1,'name':'切换后复核研究','parameters':{'trees':60,'depth':6}})
krun=run_one(r.json()['id'])
check('K/s 研究完成且为秒量纲',krun.state=='completed' and krun.result['unit_time']=='s' and 'actual_seconds' in krun.result['rows'][0],krun.error)
check('K/s 结果 MAE 标签为秒',krun.result['summary'][0]['label']=='留出 MAE / s')
r=client.post(f'/runs/{krun.id}/equivalence',headers=VIEWER)
eq=r.json()
check('K/s 任务预测等价回核通过',r.status_code==200 and eq['passed'] and eq['mae_delta_relative']<=0.1 and eq['stored_matches_recompute'] and eq['features_match'],json.dumps(eq)[:300])
r=client.post('/runs/1/equivalence',headers=VIEWER)
eq=r.json()
check('历史 ℃/min 任务预测等价回核通过',r.status_code==200 and eq['passed'] and eq['basis']=='C/min' and eq['counter_basis']=='K/s',json.dumps(eq)[:300])

# 数据级核对能抓住换算错误：人为破坏一条换算值 → 核对失败
with Session() as db:
    victim=db.scalar(select(Record).where(Record.reference=='HX-NEW-001'))
    victim.values={**victim.values,'temperature':victim.values['temperature']+1}
    db.commit()
r=client.post('/migration/verify',headers=ANALYST)
check('破坏换算值后核对失败',r.status_code==200 and not r.json()['passed'] and r.json()['mismatches']==1,r.text)
with Session() as db:
    victim=db.scalar(select(Record).where(Record.reference=='HX-NEW-001'))
    victim.values=to_canonical(victim.original_values,victim.unit)
    db.commit()
r=client.post('/migration/verify',headers=ANALYST)
check('恢复后核对重新通过',r.status_code==200 and r.json()['passed'])

# 历史任务量纲与结果不被改写
with Session() as db:
    old=db.get(Run,1)
check('历史任务快照与结果保持 min 量纲',old.unit_basis=='C/min' and old.result['unit_time']=='min' and old.snapshot['records'][0]['values']['temperature']<200)

# 换算核心往返
v={'moisture':35,'thickness':4.2,'temperature':75,'airflow':3.1,'minutes':83}
rt=from_canonical(to_canonical(v,'F/s'),'F/s')
check('℉/s 往返等价',abs(rt['temperature']-75)<1e-9 and abs(rt['minutes']-83)<1e-9)
check('K/s 恒等换算',to_canonical(v,'K/s')['temperature']==75 and to_canonical(v,'K/s')['minutes']==83)

# 权限：viewer 不能回填/录入
check('viewer 回填被拒',client.post('/migration/sweep',headers=VIEWER,json={}).status_code==403)
check('viewer 核对被拒',client.post('/migration/verify',headers=VIEWER).status_code==403)
check('viewer 切换被拒',client.post('/migration/activate',headers=VIEWER).status_code==403)
check('匿名访问迁移状态被拒',client.get('/migration/status').status_code==401)

print(f'\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项')
if FAIL:print('失败项：',FAIL);sys.exit(1)
