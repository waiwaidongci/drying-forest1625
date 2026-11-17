from enum import Enum
class Role(str,Enum):
    admin='admin'; analyst='analyst'; viewer='viewer'
class RunState(str,Enum):
    queued='queued'; running='running'; completed='completed'; failed='failed'
class ReviewState(str,Enum):
    draft='draft'; approved='approved'
class MigrationState(str,Enum):
    idle='idle'; backfilling='backfilling'; verified='verified'; active='active'
class UnitState(str,Enum):
    pending='pending'; ready='ready'; quarantined='quarantined'
