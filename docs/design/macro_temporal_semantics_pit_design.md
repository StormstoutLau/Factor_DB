# 宏观数据时间语义与 PIT 对齐设计（P8-1）

> 日期：2026-11-04
> 依据：[宏观数据时间语义与 PIT 对齐调研](../research/macro_temporal_semantics_pit_research.md)
> 上游：[P7 之后的任务分析与优先级](../research/post_p7_backlog_analysis.md)
> 范围：统一 `macro_data` 的统计期/发布日期语义，增加 PIT `as_of` 查询；不改采集器外部接口、CLI 与因子表
> 状态：设计阶段，未编码

---

## 1. 设计目标与非目标

### 1.1 目标

1. 为 12 个宏观指标建立统一时间模型：`period_date`（统计期）+ `publish_date`（首次可得日）+ `loaded_at`（入库时间）。
2. 保留 `trade_date` 作为**原始采集日期**（兼容列），新分析口径统一使用 `period_date`。
3. `MacroQuery` 支持 `as_of`：只返回某日当时已发布的数据。
4. `get_sector_sensitivity` 按 `period_date` 对齐，不再按发布日/统计期起点混用。
5. 补齐 `source_id`、真实 `indicator_name`，为溯源与 PIT 提供依据。
6. 迁移 4,358 行生产数据，保持旧查询/旧测试可运行。

### 1.2 非目标

- 不实现完整 vintage/修订版本表（P9 可选）；
- 不替换五列指标数据源（P8-1b 单独处理）；
- 不改变 `macro_indicators` 的既有字段语义；
- 不修复 `identify_macro_regime` 的指标名（`PMI`/`CPI_yoy`/`M2_yoy`）问题；
- 不改 `factor_data` / `alternative_data` / 舆情表。

---

## 2. 数据模型与数据流

### 2.1 字段语义

| 字段 | 语义 | 来源 | 示例 |
|------|------|------|------|
| `trade_date` | **原始采集日期**（兼容列，不再作为分析口径） | 采集器输出的日期 | CPI `2025-08-09` |
| `period_date` | **统计期**（月度=月末，季度=季末） | 规则推导 | CPI `2025-07-31` |
| `publish_date` | **首次可得日**（发布日期） | 采集器/发布日程/规则估算 | CPI `2025-08-09` |
| `publish_date_quality` | `exact` / `estimated` / `unknown` | 采集器/迁移规则 | CPI `exact` |
| `loaded_at` | 入库时间（PIT 兜底） | DB 默认值 | `2026-11-04 10:00:00` |
| `source_id` | 数据源 | 采集器/管道 | `akshare_macro` |
| `indicator_name` | 指标名称 | `macro_indicators`/采集器 | `CPI_MONTHLY` |

> 设计选择“新增列”而不是重命名 `trade_date`：旧代码/旧测试继续可用，新代码逐步切到 `period_date`。

### 2.2 表结构

```sql
macro_data (
    trade_date            DATE NOT NULL,       -- 原始采集日期（兼容）
    period_date           DATE,                -- 统计期（新分析口径）
    publish_date          DATE,                -- 首次可得日
    publish_date_quality  VARCHAR DEFAULT 'unknown',
    loaded_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    indicator_id          VARCHAR NOT NULL,
    indicator_name        VARCHAR,
    value                 DOUBLE,
    value_type            VARCHAR DEFAULT 'raw',
    data_quality          INTEGER DEFAULT 100,
    source_id             VARCHAR,
    update_time           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, indicator_id, value_type)
)
```

新增索引：

```sql
CREATE INDEX IF NOT EXISTS idx_macro_period_date
    ON macro_data(period_date, indicator_id);
CREATE INDEX IF NOT EXISTS idx_macro_publish_date
    ON macro_data(publish_date, indicator_id);
```

主键保持 `(trade_date, indicator_id, value_type)`：同一统计期若有多条原始日期（如 `M2_YEARLY` 同月多行），允许保留多行；查询层按 `period_date` 去重取最新。完整 vintage 表留待 P9。

### 2.3 数据流

```
AKShareMacroCollector
   │  raw DataFrame（trade_date, value, ...）
   ▼
OSINTPipeline._store(source_id)
   │  MacroLoader.load(df, source_id=source_id)
   ▼
MacroLoader
   │  1) 保留 trade_date
   │  2) 规则推导 period_date
   │  3) 写入 publish_date / quality / loaded_at / source_id
   ▼
macro_data
   ├── MacroQuery.get_macro_data(..., as_of)
   │       └── MacroFactorLink.get_sector_sensitivity(..., as_of)
   └── MacroQuery.get_macro_matrix(..., as_of)
```

### 2.4 指标时间规则

新增 `core/macro_temporal.py`，集中维护 12 指标规则（单一事实来源，供迁移、Loader、Query、测试使用）。

| 指标 | 语义 | 频率 | `period_date` 规则 | `publish_date` 规则 |
|------|------|------|-------------------|---------------------|
| `CPI_MONTHLY` / `CPI_YEARLY` / `PPI_YEARLY` / `M2_YEARLY` | 发布日 | 月 | 发布月的前一月的月末 | 原始日期（`exact`） |
| `GDP_YEARLY` | 发布日 | 季 | 发布日所在季度的上一季季末 | 原始日期（`exact`） |
| `PMI_MANUFACTURING` | 统计期起点 | 月 | 当月月末 | 当月最后一天；2 月为次月 4 日（春节例外，`estimated`） |
| `PPI_MONTHLY` | 统计期起点 | 月 | 当月月末 | 次月 9 日（`estimated`） |
| `M2_MONTHLY` | 统计期起点 | 月 | 当月月末 | 次月 13 日（`estimated`） |
| `FX_RESERVES` | 统计期起点 | 月 | 当月月末 | 次月 7 日（`estimated`） |
| `IMPORT_EXPORT` | 统计期起点 | 月 | 当月月末 | 次月 10 日（`estimated`） |
| `GDP_QUARTERLY` | 统计期末 | 季 | 原始季末 | 季末后 17 天（`estimated`） |
| `INTEREST_RATE` | 观测日 | 日 | 观测日所在月月末 | 原始日期（`exact`） |

> 注：`pbc_m2`（次月 13 日）、`safe_fx`（次月 7 日）、`customs_trade`（次月 10 日）为**假设值**——调研未取得 PBOC / SAFE / 海关总署官方发布日程引用，实施前需核实；核实前一律标 `estimated`。`nbs_gdp` 的"季末后 17 天"为 2026 年实测 19/16/15/19 天的中值估算。

早期样本例外：

- 五列发布日历指标（`CPI_MONTHLY` / `CPI_YEARLY` / `PPI_YEARLY` / `M2_YEARLY`）的早期样本中，`trade_date.day == 1` 的行视为**统计期起点**（按审计结果，CPI 大致 2011 年前、PPI/M2 更早）：`period_date = 当月月末`，`publish_date = NULL`，`publish_date_quality = 'unknown'`；
- 其余历史发布日无法精确回溯的，`publish_date` 用规则估算并标记 `estimated`；
- 规则估算值不得标记为 `exact`。

---

## 3. 接口契约

### 3.1 规则模块

```python
# core/macro_temporal.py

@dataclass(frozen=True)
class MacroTemporalRule:
    semantics: str           # release_date | period_start | period_end | observation
    frequency: str           # monthly | quarterly | daily
    period_offset: str       # previous_month | current_month_end | previous_quarter | current_quarter_end
    publish_rule: Optional[str]  # nbs_cpi_ppi | nbs_pmi | pbc_m2 | safe_fx | customs_trade | nbs_gdp | lpr | none
    publish_date_quality: str    # exact | estimated | unknown

MACRO_TEMPORAL_RULES: dict[str, MacroTemporalRule]

def resolve_period_date(indicator_id: str, raw_date: date,
                        publish_date: Optional[date] = None) -> date: ...

def estimate_publish_date(indicator_id: str, period_date: date) -> Optional[date]: ...

def is_legacy_release_series(indicator_id: str, raw_date: date) -> bool: ...

def resolve_publish_info(indicator_id: str, raw_date: date,
                         period_date: Optional[date] = None
                         ) -> tuple[Optional[date], str]: ...
```

- 发布日由 `publish_rule` 命名规则唯一表达（如 `nbs_cpi_ppi` = 次月 9 日），不另设滞后天数字段，避免"滞后天数"与"月中日"两套表示漂移。
- 未知指标：`resolve_period_date` 返回 `raw_date` **原值**（不平移到月末——月末平移会改变现有查询输出与测试种子数据的日期，不保守），`publish_date=NULL`、`unknown`。
- 规则模块不依赖 pandas/DuckDB，便于单测。

### 3.2 Loader

```python
class MacroLoader(BaseLoader):
    def load(
        self,
        data_path: Union[Path, pd.DataFrame],
        indicator_id: Optional[str] = None,
        *,
        source_id: Optional[str] = None,
    ) -> int: ...
```

DataFrame 可选新增列：

| 列 | 必填 | 缺省行为 |
|----|------|---------|
| `trade_date` | ✅ | 原有行为；同时作为 `period_date` 的兜底 |
| `period_date` | ❌ | 未提供时按规则从 `trade_date` 推导 |
| `publish_date` | ❌ | 未提供时按规则估算；无法估算则 NULL |
| `publish_date_quality` | ❌ | 默认 `unknown`；`publish_date` 由规则估算时写 `estimated` |
| `source_id` | ❌ | 优先取列值，否则取 `load(..., source_id=...)` |
| `indicator_name` | ❌ | 优先取列值，否则查 `macro_indicators`，最后回退 `indicator_id` |

写入语义：

- 主键不变；写入改为 `INSERT ... ON CONFLICT DO UPDATE`：`value` / `indicator_name` / `source_id` 以新值覆盖，`publish_date` / `publish_date_quality` 用 `COALESCE(新值, 旧值)` 保留旧值。**禁止 `INSERT OR REPLACE`**——它删除旧行后插入新行，无法保留已知发布日期；
- `loaded_at` 每次写入**显式赋** `CURRENT_TIMESTAMP`，禁止仅依赖 DDL 默认值（教训：`factor_history` 曾因依赖默认值导致 PIT 子查询恒空，见 project_memory 约束）。

### 3.3 Query

```python
class MacroQuery(BaseQuery):
    def get_macro_data(
        self,
        indicator_ids: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        value_type: str = 'raw',
        as_of: Optional[date] = None,
    ) -> pd.DataFrame: ...

    def get_macro_matrix(
        self,
        indicator_ids: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        value_type: str = 'raw',
        as_of: Optional[date] = None,
    ) -> pd.DataFrame: ...
```

输出契约：

> 注意：表内 `trade_date` 是**原始采集日期**；查询输出列 `trade_date` 是**兼容别名**，值取 `period_date`。两者在 CPI 等发布日序列上不相等。
>
> 实施注意：`BaseQuery._build_date_filter` 硬编码 `trade_date` 列（[query/base.py](file:///f:/Coding/Factor_DB/query/base.py#L60-L79)），`get_macro_data` / `get_indicator_stats` 目前直接复用；按 `period_date` 过滤需本地化日期过滤或扩展基类签名，实施规划需单列此项。`QueryFilter.as_of` 字段已存在（[query/base.py#L35](file:///f:/Coding/Factor_DB/query/base.py#L35)），直接复用，不新增。

- 保留 `trade_date` 列，但其值返回 `COALESCE(period_date, trade_date)`，即**新口径的统计期**；
- 新增 `period_date`、`publish_date`、`publish_date_quality` 列；
- `start_date` / `end_date` 过滤 `period_date`；
- `as_of` 过滤逻辑（按 `indicator_id, value_type, period_date` 分组）：
  1. 候选行 = `publish_date <= as_of`，或 `publish_date IS NULL AND loaded_at <= as_of`；
  2. 候选行按 `publish_date`（NULL 视为最旧）、`loaded_at` 降序取第一行；
  3. 没有候选行则排除；
  4. `as_of=None` 时取 `publish_date` 最新（NULL 视为最旧），再按 `loaded_at` 去重。

其余查询方法同步切换：

| 方法 | 变更 |
|------|------|
| `get_indicators` | 不变（读 `macro_indicators`） |
| `get_indicator_stats` | `period_date` 过滤/聚合 |
| `get_latest_values` | 先按 `period_date` 取最新，再取 `publish_date` 最新 |
| `get_macro_by_date_range` | `period_date BETWEEN` |
| `calculate_yoy` | `ORDER BY period_date` |
| `identify_macro_regime` | 改用 `period_date`；指标名问题不在 P8 范围 |

### 3.4 Analytics

```python
class MacroFactorLink:
    def get_sector_sensitivity(
        self,
        macro_indicator: str,
        sectors: Optional[List[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        level: Optional[str] = 'first',
        transform: Optional[str] = None,
        min_obs: int = 12,
        as_of: Optional[date] = None,
    ) -> pd.DataFrame: ...
```

- 调用 `macro_query.get_macro_data(..., as_of=as_of)`；
- 按返回的 `trade_date`（= `period_date`）做 `Period('M')` 分组，不再按发布日分组；
- 输出 10 列不变；
- `as_of=None` 时为全样本（含 `publish_date_quality='estimated'` 行，但需在文档注明）；
- `as_of` 不为空时只使用当时可得的数据。

### 3.5 迁移

```python
class SchemaManager:
    def migrate_macro_data_temporal(self) -> dict: ...
```

返回统计：`rows_total`、`period_date_filled`、`publish_date_filled`、`publish_date_unknown`、`duplicate_period_rows`。

迁移步骤：

1. 备份 `macro_data` 到 `macro_data_backup_YYYYMMDD`（4,358 行，成本可接受）；
2. `ALTER TABLE` 增加 4 列（幂等：先查 `information_schema`，列已存在则跳过；`loaded_at` 带 `DEFAULT CURRENT_TIMESTAMP` 回填存量行）；
3. 按 `MACRO_TEMPORAL_RULES` 回填 `period_date` / `publish_date` / `publish_date_quality`；
4. 异常标注：`M2_YEARLY` 中 `value = 0.0` 的行（如 2020-02-17）`data_quality` 降为 50 并保留原值，不删除（对应调研 §5.4）；
5. `source_id`：`indicator_id` 属于 12 个 AKShare 指标且为空时写 `akshare_macro`；
6. `indicator_name`：从 `macro_indicators` 回填；缺失时保留 `indicator_id`；
7. 建索引；
8. 输出对账统计，失败则回滚/保留备份。

---

## 4. 关键决策及依据

| # | 决策 | 依据 |
|---|------|------|
| D1 | 新增 `period_date` 列，不重命名 `trade_date` | 调研 §4.2：现有 12 指标、Query、Loader、测试广泛依赖 `trade_date`；新增列风险最低 |
| D2 | `trade_date` 保留为原始采集日期，不作为分析口径 | 调研 §2.1：原始日期语义混合，直接改语义不可逆 |
| D3 | `publish_date` 增加 `publish_date_quality` | 调研 §3.2/§6：历史发布日期不可考，必须区分 exact/estimated/unknown |
| D4 | 主键保持 `(trade_date, indicator_id, value_type)` | 避免破坏直接 INSERT 的测试；允许同一期多行，由查询层去重 |
| D5 | 规则集中到 `core/macro_temporal.py` | 调研 §5.2 S1；迁移、Loader、Query、测试共用同一映射，避免口径漂移 |
| D6 | P8-1 不建 vintage 表 | 调研 §4 方案 B：当前数据源无完整 vintage，先做“发布日期 PIT” |
| D7 | 敏感度按 `period_date` 对齐 | 调研 §3.3：CPI 发布日归到发布月会错位，PMI 月初日期含前视 |
| D8 | `as_of` 用 `publish_date` 优先、`loaded_at` 兜底 | 调研 §3.2：PIT 的可得性应由发布日期决定；兜底仅用于未知发布日期 |
| D9 | 五列指标历史早期按 `unknown` 处理 | 调研 §6：2012 年前 CPI 日期可能是统计期而非发布日，不能强行标 exact |

---

## 5. 风险与权衡

| 风险 | 影响 | 应对 |
|------|------|------|
| `period_date` 可空 | 旧测试/旧 SQL 插入新行时为空 | 查询统一 `COALESCE(period_date, trade_date)`；Loader 保证新写入非空 |
| 同一期多行 | 敏感度重复计算 | Query 按 `(indicator_id, value_type, period_date)` 去重，取最新 `publish_date`/`loaded_at` |
| `publish_date` 估算误差 | PIT 边界日可能提前/滞后 | 标记 `estimated`；文档注明；关键指标优先接真实发布日程 |
| M2/外储/进出口滞后日未核实 | estimated 值偏差可达数日 | 实施前以 PBOC / SAFE / 海关日程核实；未核实不影响 `exact` 类指标 |
| 未来发布日恰逢当月 1 日 | legacy 启发式（`day==1` → 统计期起点）误判 | NBS 日程显示 CPI/PPI 固定 9~14 日发布，风险低；启发式仅对五列指标启用，`unknown` 质量标记可追溯 |
| 主键不变，无法存修订 | 修订会覆盖旧值 | P8-1 接受；P9 评估 vintage 表 |
| 早期 CPI 语义不确定 | period_date 可能偏移 1 月 | 标记 `unknown`；文档列出受影响区间；不把未知当 exact |
| `source_id` 回填改变 OSINT 统计 | 既有测试断言可能变化 | 更新 `test_osint` 相关断言；新增 source summary 用例 |
| `indicator_name` 回填 | 输出名称变化 | 以 `macro_indicators` 为准；保留回退 |
| P7 beta 变化 | 既有验收数值失效 | P8-1 完成后重跑 CPI 敏感度并更新验收锚点 |
| INTEREST_RATE 日频 | 月频降采样口径不清 | 设计只统一 `period_date`，月内 `.last()` 行为不变并在文档注明 |
| 迁移失败 | 数据损坏 | 先备份、幂等 ALTER、单事务回填、失败保留备份 |

---

## 6. 兼容性与验收锚点

### 6.1 兼容性

- 旧调用 `get_macro_data([...])` 不传 `as_of`：返回列增加，`trade_date` 变为统计期；属于预期口径修正。
- 旧测试直接 `INSERT INTO macro_data (...)`：新列可空/有默认值，继续可用；查询用 `COALESCE` 兜底。
- `MacroLoader.load(df)`：不传 `source_id` 仍可用；`period_date` 自动推导。
- CLI `collect` 参数与退出码不变；`OSINTPipeline._store` 只增加 `source_id` 透传。
- 输出 10 列敏感度结构不变；新增 `as_of` 为可选参数。

### 6.2 验收锚点

| 项 | 期望 |
|----|------|
| 迁移覆盖 | 12 指标 `period_date` 非空 100% |
| CPI | `trade_date=2025-08-09` → `period_date=2025-07-31`、`publish_date=2025-08-09`、`quality=exact` |
| PMI | `trade_date=2026-10-01` → `period_date=2026-10-31`、`publish_date≈2026-10-31`、`quality=estimated` |
| GDP_QUARTERLY | `trade_date=2026-09-30` → `period_date=2026-09-30`、`publish_date≈2026-10-17`、`quality=estimated` |
| PIT 查询 | `as_of=2025-08-05` 不含 CPI `2025-07-31`；`as_of=2025-08-15` 含 |
| 敏感度 | 默认按 `period_date`；P7 beta 复跑并记录新旧差异 |
| 回归 | 全量测试通过、`ruff check .` 零告警 |

---

## 7. 下游文档

- 实施规划：[宏观数据时间语义与 PIT 对齐实施规划](../implementation/macro_temporal_semantics_pit_implementation.md)
- 验收 checklist：[REVIEW_CHECKLIST 第二十一节](REVIEW_CHECKLIST.md)（P8-1）

### 7.1 P8-0 文档纠偏清单（与 P8-1 同步执行）

| 文件 | 修正内容 |
|------|----------|
| [macro_factor_link.py](file:///f:/Coding/Factor_DB/analytics/macro_factor_link.py#L240-L242) | 删除“`macro_data.trade_date` 为统计期”的表述，改为“原始日期语义混合；P8-1 后按 `period_date` 对齐” |
| [sector_sensitivity_design.md](file:///f:/Coding/Factor_DB/docs/design/sector_sensitivity_design.md) | 明确同期关联的日期口径与 P8-1 变更 |
| [sector_sensitivity_implementation.md](file:///f:/Coding/Factor_DB/docs/implementation/sector_sensitivity_implementation.md) | 同上 |
| [cpi_monthly_beta_finiteness_research.md](file:///f:/Coding/Factor_DB/docs/research/cpi_monthly_beta_finiteness_research.md) / [cpi_monthly_beta_fix_implementation.md](file:///f:/Coding/Factor_DB/docs/implementation/cpi_monthly_beta_fix_implementation.md) | 标注 P7 beta 的月份对齐/前视边界 |
| `docs/DEVLOG.md` | P5/P7 条目补充日期语义说明 |
| `docs/ARCHITECTURE.md` / `CODE_WIKI.md` | 宏观表字段与 PIT 语义说明 |
