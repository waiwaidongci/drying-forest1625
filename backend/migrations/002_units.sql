-- 002_units.sql · 单位迁移（开尔文/秒）在线变更，幂等可重复执行。
-- 应用启动时由 config.schema_updates.ensure_schema 自动完成同等变更；本文件为审计留档。
-- 样本：申报单位、原值保留、单位状态（pending 待换算 / ready 现行 K/s / quarantined 隔离）。
ALTER TABLE drying_samples ADD COLUMN IF NOT EXISTS unit VARCHAR(20);
ALTER TABLE drying_samples ADD COLUMN IF NOT EXISTS original_values JSON;
ALTER TABLE drying_samples ADD COLUMN IF NOT EXISTS unit_state VARCHAR(20);
-- 任务：冻结时采用的单位版本（C/min 历史 / K/s 现行），历史任务回填为 C/min。
ALTER TABLE regression_runs ADD COLUMN IF NOT EXISTS unit_basis VARCHAR(20);
UPDATE drying_samples SET unit_state='pending' WHERE unit_state IS NULL;
UPDATE regression_runs SET unit_basis='C/min' WHERE unit_basis IS NULL;
-- 迁移任务单例：状态机 idle → backfilling → verified → active，游标与核对报告。
CREATE TABLE IF NOT EXISTS migration_state (
  id INTEGER PRIMARY KEY,
  state VARCHAR(20) DEFAULT 'idle',
  cursor INTEGER DEFAULT 0,
  verify_report JSON,
  started_at VARCHAR(40) DEFAULT '',
  verified_at VARCHAR(40) DEFAULT '',
  activated_at VARCHAR(40) DEFAULT '',
  updated_at VARCHAR(40) DEFAULT ''
);
