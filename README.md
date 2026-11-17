# 槐序干燥时长试算台

RandomForest回归、训练内特征子集交叉验证与留出集MAE。为槐序研究协作组的12位分析与业务人员提供从样本核对、独立worker计算到批准快照交付的完整工作流。

## 真实业务场景

物料初始湿度/厚度/温度→干燥时长估计→工艺试验备忘；解释用留出置换重要性，禁止用留出集选参。团队原先将 `drying-forest-202611-01.csv` 复制到多份人工表格，复核时再逐项对照。三个痛点是口径不可追溯、重复计算易错、业务说明与结果脱节。上游提供合成业务观察，系统承担研究和复核，下游业务人员读取批准快照。全部演示数据是固定随机种子生成的模拟数据。

## 业务模块

1. 业务总览：批次、样本、任务与批准交付账册。
2. 干燥研究：创建/修改来源说明、查看样本、用真实业务字段追加记录；列表分页。
3. 分析配置：冻结来源、参数和全部样本，进入Redis队列。
4. 完整结果：真实算法摘要、专属诊断、全部CSV导出和可恢复失败态。
5. 工艺试验备忘：分析师草案→负责人批准→阅读者下载批准快照。
6. 单位迁移：旧数据 ℃/min 在线回填为 K/s，新设备按声明单位（含 ℉）接收，缺失/未知声明隔离，核对通过后切换生效，预测等价回核。
7. 审计：登录、来源修改、任务worker完成、业务说明批准等事件。

## 单位迁移（开尔文/秒）

实验室现行单位为开尔文（K）与秒（s）；历史数据为摄氏度（℃）与分钟（min），新设备可按华氏度（℉）上报。迁移在线进行，录入与新建研究不中断。

- **每批声明单位并保留原值**：样本带 `unit`（如 `C/min`、`F/s`、`K/s`）、`original_values`（申报原值）与 `unit_state`（`pending` 待换算 / `ready` 现行 K/s / `quarantined` 隔离）。
- **旧入口仍按原单位接收**：`POST /sources/{id}/records` 隐含 `C/min` 声明，样本进入待换算，由回填批次统一换算。新入口 `POST /sources/{id}/batches` 按批声明单位，合法声明即时换算为 K/s 并保留原值；**缺失或未知声明的新批隔离**，不参与冻结，声明单位后解除。
- **分批回填可恢复**：`POST /migration/sweep` 每批一个事务、只取 `pending` 记录；中断后重试不重复换算，期间新增样本随后续批次补齐，游标与进度在迁移页可见。
- **每次冻结只取完整一致的单位版本**：迁移未开始（`idle`）冻结取旧 ℃/min 一致口径（已换算记录等价回核、隔离记录排除）；开始迁移后只取已换算的 K/s 样本。任务落库 `unit_basis`，结果键与标签随量纲（`actual_minutes`/`actual_seconds`）。
- **核对完才切换生效**：`POST /migration/verify` 重算全部已换算记录并往返回核（相对误差 ≤1e-6），无待换算且无不一致才通过；负责人 `POST /migration/activate` 切换为 `active`。切换后旧入口新增样本继续分批换算。
- **以往分析与获准说明沿用当时数值及量纲**：任务与批准快照在创建时冻结，历史任务保持 ℃/min 标签，不回写。
- **页面核对**：单位迁移页展示进度、换算依据（K = ℃ + 273.15；K = (℉ − 32) × 5/9 + 273.15；s = min × 60）、换算样例、隔离区与核对报告；预测等价核对 `POST /runs/{id}/equivalence` 用等价输入换回原单位重算确定性算法，比对留出预测（特征子集一致 · 存储结果可复算 · 留出MAE相对差≤10%；float32 分裂阈值在不同量级单位下存在固有舍入差，换算公式错误会呈数量级差异）。

既有库启动时自动完成幂等增量（`config/schema_updates.py`，留档 `migrations/002_units.sql`）：样本加单位三列并回填 `pending`，任务加 `unit_basis` 并回填 `C/min`，新建 `migration_state` 单例（`idle → backfilling → verified → active`）。

## 快速启动

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
# 可重复执行，不覆盖人工修改
docker compose exec -T backend python -m scripts.seed_demo
```

访问 [前端](http://localhost:18248)；[API文档](http://localhost:19248/docs)。数据库仅演示用途映射 `17748`。首次准备依赖和初始化数据库需要等待健康状态：`docker compose ps`，五个服务均需healthy。

| 组件 | 技术与职责 |
|---|---|
| frontend | 原生 HTML / CSS / JS ES Modules，Nginx代理 `/api/` |
| backend | Python3.11 / FastAPI / SQLAlchemy / JWT |
| worker | 独立Python进程，Redis BLPOP队列，持久化结果/CSV/心跳 |
| db | PostgreSQL16，命名卷持久化 |
| redis | Redis7，AOF命名卷 |

恰好五服务，均配置healthcheck；后端/worker依赖数据库与Redis健康，前端依赖后端健康；worker与后端共享 `artifacts` 命名卷。重置演示环境：`docker compose down -v --remove-orphans`，会删除本项目卷。

## 账号与权限

所有演示账号密码：`Research2026!`。

| 邮箱 | 角色 | 权限 |
|---|---|---|
| admin@drying-forest.demo | admin | 全部录入/执行、批准业务说明 |
| analyst@drying-forest.demo | analyst | 录入来源/样本、执行、提交说明，无批准权 |
| viewer@drying-forest.demo | viewer | 只读、导出结果及已批准快照 |

时间在数据库/API中以UTC存储，总览与审计页面明确转换为Asia/Shanghai北京时间。

JWT有效期8小时；PBKDF2盐化哈希保存密码；角色每请求从数据库读取。访客写操作403，匿名访问业务数据401，重复样本409，非法业务字段422，重复批准409。每客户端/路径写请求90次/分钟，超限429。生产使用需修改JWT及演示数据库凭据。

## 实体与字段

DryingStudy / DryingSample / RegressionRun / ProcessMemo 是核心实体；另有User、Audit和MigrationState。数据库实际表：`drying_studies` → `drying_samples` → `regression_runs` → `process_memos`。样本以固定字段JSON保存，后端按以下字段严格验证，不接受未知字段替代必填值：初始湿度 %（`moisture`）、厚度 mm（`thickness`）、设定温度（`temperature`，单位随批次声明）、风量 m³/min（`airflow`）、实测时长（`minutes`，单位随批次声明，min 或 s）。温度与时长的单位由批次 `unit` 声明决定，现行存储口径为 K/s。

实体贯穿 `backend/src/models`、`schemas`、`services`、`controllers`、`routes`，以及 `frontend/src/api/research.js`、`stores/research.js` 和 `pages`。`Source.key` 与 `(Record.source_id, reference)` 为自然键；来源改名不会修改已冻结任务输入。业务说明在创建时保存结果快照，批准后从该快照导出。

## 算法与口径

RandomForest回归、训练内特征子集交叉验证与留出集MAE，全部计算位于 `backend/src/services/algorithm.py`，前端不拟合模型、不生成模拟结果。输入是冻结业务观察及参数，输出summary、专属诊断、逐行计算明细、seed与输入SHA256。CSV包含全部结果行；页面仅预览前30行。参数：

- `trees`：森林树数，默认 `60`。
- `depth`：最大深度，默认 `6`。

先固定留出集，特征选择与交叉验证仅在训练行完成；留出集只作最后一次评估。置换重要性是模型依赖度，不是因果贡献。

失败态：业务字段缺失/非有限数值、参数越界、样本不足、退化特征或结构错误均明确拒绝或将任务标failed，错误消息保存在任务中。可回到来源或配置修正后重新执行，历史失败记录保留。

已知边界：只比较三个预定义特征子集，超参数由分析前固定；留出集不用于选择。置换重要性来自最后留出集，不可反复依据它改模再宣称独立验证。不含预测区间，不用于外推工艺。

## 演示数据与一天剧本

固定seed=42，3个来源 ` HX-202611-01/02/03`，每来源240条，共720条 ℃/min 旧业务记录（待换算）；另在 `HX-202611-03` 演示 24 条新设备 ℉/s 声明批（已换算并保留原值）与 6 条缺失声明隔离批，合计750条；三个账号和首轮种子任务（℃/min 历史量纲）。自然键检查后仅插入缺失数据，连跑seed不重复、不覆盖用户修改。`SEED_DEMO=true` 时启动自动执行；失败记录日志但不阻塞服务。首轮任务同样由独立worker真实计算。

1. 09:00 负责人查看三份来源和720条样本。
2. 09:10 分析师选择 `HX-202611-01 秋季常规组`，核对 `drying-forest-202611-01.csv`。
3. 09:15 在“干燥研究”核对初始湿度 %（`moisture`）、厚度 mm（`thickness`）、设定温度 ℃（`temperature`）、风量 m³/min（`airflow`）、实测时长 min（`minutes`），追加编号 `HX-NEW-001` 的样本。
4. 09:20 新建“十一月复核研究”，按默认参数冻结241条输入。
5. 09:21 独立worker消费Redis队列，返回实算结果与输入校验和。
6. 09:25 分析师查看专属诊断及逐行结果，并下载CSV复算。
7. 09:30 提交“十一月业务复核意见”，负责人核对并批准。
8. 09:40 viewer下载批准快照，查看审计中的worker完成与批准事件。

迁移日剧本（单位切换演示）：

1. 10:00 分析师在“单位迁移”核对换算依据与进度（720 条待换算、24 条 ℉/s 已换算、6 条隔离）。
2. 10:05 点击“开始回填”，分批换算期间旧入口照常录入 ℃/min 样本、新设备按 ℉/s 声明上报；中断后重点继续，不重复、不漏新。
3. 10:20 回填完成，点击“核对换算”：重算与往返回核全部通过。
4. 10:25 负责人点击“切换生效”，K/s 单位版本生效；新建研究冻结秒级量纲。
5. 10:30 对切换后研究执行“预测等价核对”，确认等价输入换回原单位后留出预测一致；隔离批声明单位后解除。

## 目录与本地开发

```text
frontend/src/api, stores, types, components/common, hooks, pages, router, utils, constants
backend/src/routes, controllers, services, models, schemas, middlewares, utils, constants, config
backend/scripts/seed_demo.py
backend/migrations/001_initial.sql
backend/migrations/002_units.sql
```

本地后端：Python3.11虚拟环境安装 `backend/requirements.txt`，设置 `DATABASE_URL`、`REDIS_URL`、`ARTIFACT_DIR`，在backend目录运行 `uvicorn src.main:app --reload`；另开终端运行 `python -m src.worker`。原生前端无需编译，推荐以Compose Nginx访问，保证 `/api/` 代理。前端为Google Fonts双字体、oklch色板，离线字体回退serif；1280/768/390响应布局。

| 环境变量 | 默认与说明 |
|---|---|
| COMPOSE_PROJECT_NAME | `drying-forest`，隔离容器/网络/命名卷 |
| FRONTEND_PORT / BACKEND_PORT / DB_PORT | `18248 / 19248 / 17748` |
| SEED_DEMO | `true` |
| JWT_SECRET | `.env.example`演示值，部署前修改 |
| DATABASE_URL / REDIS_URL | Compose内部连接，定义在compose的env锚点 |
| ARTIFACT_DIR | `/artifacts`，后端worker共享 |

共享枚举：`backend/src/constants/enums.py` 与 `frontend/src/constants/enums.js` 都定义 admin/analyst/viewer、queued/running/completed/failed、draft/approved、idle/backfilling/verified/active、pending/ready/quarantined 与六种单位声明。后端service检查对应字符串；README为全量值说明。共享前端组件table/status/chart/business跨总览、结果、配置与复核复用；actions hook和format utils负责交互/格式。后端errors与rate_limit是两个中间件。

测试及截图在批根 `test-artifacts/gb-0044/`，不混入源码。独立算法oracle、HTTP完整流程和真实Playwright浏览器流程见该目录 `TESTING.md`。License：MIT。
