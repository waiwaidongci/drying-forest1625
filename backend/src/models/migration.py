from sqlalchemy import Column,Integer,String,JSON
from ..config.database import Base
class MigrationState(Base):
    __tablename__='migration_state'
    id=Column(Integer,primary_key=True)
    state=Column(String(20),default='idle')
    cursor=Column(Integer,default=0)
    verify_report=Column(JSON,nullable=True)
    started_at=Column(String(40),default='')
    verified_at=Column(String(40),default='')
    activated_at=Column(String(40),default='')
    updated_at=Column(String(40),default='')
