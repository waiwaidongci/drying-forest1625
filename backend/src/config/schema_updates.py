'''既有库的幂等增量列：新部署由 SQLAlchemy 元数据直接建全量表，此处为空转。'''
from sqlalchemy import inspect,text
from .settings import CONFIG
ADDITIVE_COLUMNS={
    CONFIG['record_table']:[('unit','VARCHAR(20)'),('original_values','JSON'),('unit_state','VARCHAR(20)')],
    CONFIG['run_table']:[('unit_basis','VARCHAR(20)')],
}
BACKFILL=(
    f"UPDATE {CONFIG['record_table']} SET unit_state='pending' WHERE unit_state IS NULL",
    f"UPDATE {CONFIG['run_table']} SET unit_basis='C/min' WHERE unit_basis IS NULL",
)
def _existing(conn,table):
    if conn.dialect.name=='sqlite':return {r[1] for r in conn.exec_driver_sql(f'PRAGMA table_info({table})')}
    return {r[0] for r in conn.execute(text('SELECT column_name FROM information_schema.columns WHERE table_name=:t'),{'t':table})}
def ensure_schema(engine):
    with engine.begin() as conn:
        inspector=inspect(conn)
        for table,columns in ADDITIVE_COLUMNS.items():
            if not inspector.has_table(table):continue
            existing=_existing(conn,table)
            for name,ddl in columns:
                if name not in existing:conn.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
        tables={t for t in (CONFIG['record_table'],CONFIG['run_table']) if inspector.has_table(t)}
        for sql in BACKFILL:
            if sql.split()[1] in tables:conn.exec_driver_sql(sql)
