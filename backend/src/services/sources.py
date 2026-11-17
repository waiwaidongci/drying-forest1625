import secrets
from fastapi import HTTPException
from sqlalchemy import select,func
from ..models.source import Source
from ..models.record import Record
from ..utils.serialization import row
from .audit import log
from .domain_validation import validate_record,structural_check
from .units import LEGACY_PROFILE,parse_profile,to_canonical
def list_sources(db):
    rows=[]
    for s in db.scalars(select(Source).order_by(Source.id)):
        d=row(s);d['record_count']=db.scalar(select(func.count()).select_from(Record).where(Record.source_id==s.id));rows.append(d)
    return rows
def source_or_404(db,id):
    s=db.get(Source,id)
    if not s:raise HTTPException(404,'未找到来源批次')
    return s
def save_source(db,user,data,id=None):
    s=source_or_404(db,id) if id else Source(key='user-'+secrets.token_hex(10))
    for k,v in data.model_dump().items():setattr(s,k,v)
    db.add(s);db.flush();log(db,user,'source.updated' if id else 'source.created',s.id,s.name);db.commit();return row(s)
def list_records(db,id):
    source_or_404(db,id)
    return [row(x) for x in db.scalars(select(Record).where(Record.source_id==id).order_by(Record.id))]
def _check_reference(db,id,reference,refs):
    if reference in refs:raise HTTPException(422,'批内样本编号重复')
    refs.add(reference)
    if db.scalar(select(Record.id).where(Record.source_id==id,Record.reference==reference)):raise HTTPException(409,'样本编号已存在：'+reference)
def add_records(db,user,id,data):
    '''旧入口：仍按原单位（℃/min）接收，隐含声明，待回填批次统一换算。'''
    source_or_404(db,id)
    validated=[];refs=set()
    for r in data.records:
        _check_reference(db,id,r.reference,refs)
        try:values=validate_record(r.values)
        except ValueError as e:raise HTTPException(422,str(e))
        validated.append(Record(source_id=id,reference=r.reference,values=values,unit=LEGACY_PROFILE,unit_state='pending'))
    db.add_all(validated);log(db,user,'records.added',id,str(len(validated)));db.commit();return {'added':len(validated)}
def add_declared_batch(db,user,id,data):
    '''新入口：每批声明单位并保留原值；缺失或未知声明的新批隔离，不参与冻结。'''
    source_or_404(db,id)
    known=parse_profile(data.unit)
    validated=[];refs=set();quarantined=0
    for r in data.records:
        _check_reference(db,id,r.reference,refs)
        try:raw=structural_check(r.values)
        except ValueError as e:raise HTTPException(422,str(e))
        if known:
            try:canonical=validate_record(to_canonical(raw,data.unit))
            except ValueError as e:raise HTTPException(422,'按声明单位换算后未通过业务校验：'+str(e))
            validated.append(Record(source_id=id,reference=r.reference,values=canonical,unit=data.unit,original_values=raw,unit_state='ready'))
        else:
            validated.append(Record(source_id=id,reference=r.reference,values=raw,unit=data.unit or None,unit_state='quarantined'));quarantined+=1
    db.add_all(validated);log(db,user,'records.declared',id,f'声明{data.unit or "缺失"} · 接收{len(validated)}条 · 隔离{quarantined}条');db.commit()
    return {'added':len(validated),'quarantined':quarantined}
