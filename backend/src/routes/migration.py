from fastapi import APIRouter,Depends
from ..config.database import db_session
from ..schemas.migration import SweepInput,ResolveInput
from ..services.auth import current_user,writer,admin
from ..controllers.migration import MigrationController as C
router=APIRouter()
@router.get('/migration/status')
def status(db=Depends(db_session),u=Depends(current_user)):return C.status(db)
@router.post('/migration/sweep')
def sweep(data:SweepInput,db=Depends(db_session),u=Depends(writer)):return C.sweep(db,u,data)
@router.post('/migration/verify')
def verify(db=Depends(db_session),u=Depends(writer)):return C.verify(db,u)
@router.post('/migration/activate')
def activate(db=Depends(db_session),u=Depends(admin)):return C.activate(db,u)
@router.post('/migration/quarantine/resolve')
def resolve(data:ResolveInput,db=Depends(db_session),u=Depends(writer)):return C.resolve(db,u,data)
@router.post('/runs/{id}/equivalence')
def equivalence(id:int,db=Depends(db_session),u=Depends(current_user)):return C.equivalence(db,u,id)
