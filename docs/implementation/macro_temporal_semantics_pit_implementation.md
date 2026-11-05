# 宏观数据时间语义与 PIT 对齐实施规划（P8-1）

> 日期：2026-11-04
> 上游：[调研](../research/macro_temporal_semantics_pit_research.md) / [设计](../design/macro_temporal_semantics_pit_design.md)
> 本文档为编码前规划；生产迁移与复跑结果在 §6 回填。

---

## 1. 分步实施计划

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | 新增 `core/macro_temporal.py`：`MacroTemporalRule`、12 指标规则、`resolve_period_date`、`estimate_publish_date`、`is_legacy_release_series`、`resolve_publish_info` | 单测 8 例：12 指标覆盖、发布日/统计期/季末/观测、legacy、未知指标、发布日估算 |
| S2 | `core/schema.py`：DDL 增加 4 列 + 2 索引；实现 `migrate_macro_data_temporal()` | 单测 5 例：新库列/索引、回填、幂等、source/name、M2 异常标注 |
| S3 | `loaders/macro_loader.py`：可选时间列、自动推导、`source_id`、真实 `indicator_name`、`ON CONFLICT` 保留 publish | 单测 5 例：推导、重载保留、source/name、未知指标、显式列优先 |
| S4 | `query/macro_query.py`：`period_date` 口径 + `as_of` PIT；同步 7 个查询方法（含 `identify_macro_regime` 仅替换日期列） | 单测 6 例：输出列、as_of 边界、NULL publish、同期多行、legacy 兜底、矩阵+其他查询 |
| S5 | `analytics/macro_factor_link.py`：新增 `as_of`，按 `period_date` 对齐 | 单测 2 例：as_of 过滤、默认对齐 |
| S6 | 生产库迁移 + P7 beta 复跑 + P8-0 文档纠偏 + 全量回归 | 4,358 行迁移；397 passed；`ruff` 零告警 |

TDD 顺序：先写 `tests/test_macro_temporal.py` 与 `tests/test_macro_factor_link.py` 新增 2 例 → 确认红 → S1-S5 → 定向绿 → 全量回归 → 生产迁移。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | Python `>=3.9`（仅用 `dataclasses`、`datetime.date`、`calendar.monthrange`、`typing.Optional`）；DuckDB 本地 1.5.3 |
| **依赖** | 不新增依赖；不改 `pyproject.toml` |
| **兼容** | 只加列，不改主键/列名；旧 `INSERT INTO macro_data (trade_date, ...)` 仍可执行；查询用 `COALESCE(period_date, trade_date)` 兜底；`trade_date` 输出语义变为统计期属预期修正 |
| **接口** | `MacroLoader.load(..., *, source_id=None)`；`MacroQuery.get_macro_data/get_macro_matrix(..., as_of=None)`；`MacroFactorLink.get_sector_sensitivity(..., as_of=None)`；新增 `SchemaManager.migrate_macro_data_temporal()` |
| **签名** | 无可变默认参数；`as_of` 追加在既有可选参数之后，不破坏位置调用；规则 dataclass `frozen=True` |
| **低效操作** | 迁移读取 4,358 行到 pandas，一次注册临时表批量更新；禁止逐行 INSERT/UPDATE；`MacroLoader` 沿用单次 `INSERT ... SELECT` |
| **查询过滤** | `BaseQuery._build_date_filter` 硬编码 `trade_date`（[base.py#L60-L79](file:///f:/Coding/Factor_DB/query/base.py#L60-L79)）；`MacroQuery` 需本地构造 `period_date` + `as_of` 过滤，不修改 `BaseQuery` 以免影响 price/factor 查询 |
| **内存** | 4,358 行 × 12 列，远低于 32GB；无 ART 大索引风险 |
| **数据一致性** | 主键 `(trade_date, indicator_id, value_type)` 不变；`ON CONFLICT DO UPDATE` 保留旧 `publish_date`；`loaded_at` 显式赋 `CURRENT_TIMESTAMP`；`publish_date_quality` 区分 exact/estimated/unknown |
| **DuckDB 语法** | 本地 1.5.3 实测 `INSERT ... ON CONFLICT (key) DO UPDATE` 与 `UPDATE ... FROM` 均可用；`pyproject` 最低 `duckdb>=0.10.0` 未单独验证该语法。若目标环境不支持，降级方案：先查旧 `publish_date` 合并进临时 DataFrame，再 `INSERT OR REPLACE` |
| **迁移安全** | 先 `CREATE TABLE macro_data_backup_YYYYMMDD AS SELECT *`；`ALTER` 前查 `information_schema.columns` 幂等；单事务回填；失败保留备份 |
| **日期语义** | 发布日序列按“前一月/前一季度末”；统计期起点按当月月末；季末保持；日频观测按月月末；legacy `day==1` 标 `unknown` |
| **并发** | 迁移需独占写连接；执行前关闭 `MacroQuery` 只读连接与 `DuckDBConnection._instances` 缓存 |

---

## 3. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `core/macro_temporal.py` | **新增**：规则常量与 4 个纯函数 |
| `core/schema.py` | `macro_data` DDL 增加 4 列；`INDEXES` 增加 2 索引；新增 `migrate_macro_data_temporal()` |
| `loaders/macro_loader.py` | 可选时间列标准化/推导；`source_id`；真实 `indicator_name`；`ON CONFLICT` 写入 |
| `osint/pipeline.py` | `_store` 对 macro 调用 `loader.load(df, source_id=source_id)` |
| `query/macro_query.py` | `period_date` 口径、`as_of`、7 个查询方法同步（`identify_macro_regime` 仅替换日期列） |
| `analytics/macro_factor_link.py` | 新增 `as_of`；按 `period_date` 对齐；P8-0 docstring 纠偏 |
| `tests/test_macro_temporal.py` | **新增**：规则 8 + 迁移 5 + Loader 5 + Query 6 = 24 例 |
| `tests/test_macro_factor_link.py` | 新增 2 例（as_of、默认对齐） |
| `tests/test_osint.py` | 更新 `source_id is NULL` 的过时注释（无断言变更） |
| `docs/implementation/macro_temporal_semantics_pit_implementation.md` | 本文件（§6 回填） |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第二十一节 |
| `docs/spec/...`、`docs/DEVLOG.md`、`docs/ARCHITECTURE.md`、`CODE_WIKI.md`、`README.md`、`README_zh.md` | P8-0 纠偏 + 测试数 371 → 397 |

---

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `macro_data` 列 | `trade_date, indicator_id, indicator_name, value, value_type, data_quality, source_id, update_time` | 追加 `period_date`、`publish_date`、`publish_date_quality`、`loaded_at` |
| `macro_data` 索引 | `idx_macro_date`、`idx_macro_indicator`、`idx_macro_date_indicator` | 追加 `idx_macro_period_date`、`idx_macro_publish_date` |
| `MacroLoader.load` | `(data_path, indicator_id=None)` | `(data_path, indicator_id=None, *, source_id=None)` |
| `MacroLoader` 写入 | `INSERT OR REPLACE`，`source_id=NULL`，`indicator_name=indicator_id` | `INSERT ... ON CONFLICT DO UPDATE`；`source_id` 透传；名称查 `macro_indicators` |
| `OSINTPipeline._store` | `loader.load(df)` | `loader.load(df, source_id=source_id)`（仅 macro 分支） |
| `MacroQuery.get_macro_data` | `(..., value_type='raw')` | `(..., value_type='raw', as_of=None)`；输出追加 `period_date`/`publish_date`/`publish_date_quality` |
| `MacroQuery.get_macro_matrix` | `(..., value_type='raw')` | `(..., value_type='raw', as_of=None)` |
| `MacroQuery` 日期列 | `trade_date` | `COALESCE(period_date, trade_date)`，过滤/排序均按此 |
| `MacroFactorLink.get_sector_sensitivity` | `(..., min_obs=12)` | `(..., min_obs=12, as_of=None)` |
| `SchemaManager` | 无迁移方法 | 新增 `migrate_macro_data_temporal() -> dict` |

---

## 5. 测试计划（TDD）

新增 `tests/test_macro_temporal.py` 与 `tests/test_macro_factor_link.py` 2 例，共 **26** 例；目标全量 **371 + 26 = 397 passed**。

### 5.1 规则模块（8 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_rules_cover_all_indicators` | 模块不存在 | 规则键覆盖 12 个 AKShare 指标 |
| 2 | `test_release_monthly_period_and_publish` | 同上 | CPI `2025-08-09` → `2025-07-31`，publish=`2025-08-09` |
| 3 | `test_release_quarterly_period` | 同上 | GDP_YEARLY `2024-04-16` → `2024-03-31` |
| 4 | `test_period_start_month_end` | 同上 | PMI `2026-10-01` → `2026-10-31`；PPI_MONTHLY `2026-09-01` → `2026-09-30` |
| 5 | `test_period_end_preserved` | 同上 | GDP_QUARTERLY `2026-09-30` 不变 |
| 6 | `test_legacy_day_one_unknown` | 同上 | CPI `2010-05-01` → `2010-05-31`，publish=`None`，quality=`unknown` |
| 7 | `test_unknown_indicator_raw_date` | 同上 | `UNKNOWN` `2024-01-01` 原样返回，publish=`None` |
| 8 | `test_estimate_publish_date_rules` | 同上 | CPI → 次月 9 日；PMI 2 月 → 次月 4 日；GDP_QUARTERLY → 季末+17 天 |

### 5.2 Schema 迁移（5 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 9 | `test_fresh_schema_has_temporal_columns_and_indexes` | `period_date` 不存在 | 4 列存在，2 索引存在 |
| 10 | `test_migrate_backfills_release_series` | 方法不存在 | CPI 近期/legacy 两行回填正确 |
| 11 | `test_migrate_backfills_period_start_and_quarter` | 方法不存在 | PMI、GDP_QUARTERLY 回填正确 |
| 12 | `test_migrate_idempotent_and_stats` | 方法不存在 | 连跑两次行数不变；返回统计字段完整 |
| 13 | `test_migrate_source_name_and_m2_zero_anomaly` | 方法不存在 | `source_id='akshare_macro'`；名称回填；M2 `0.0` 行 `data_quality=50` |

### 5.3 Loader（5 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 14 | `test_loader_derives_period_and_publish` | 无 `period_date` 列 | 自动写入 period/publish/quality |
| 15 | `test_loader_preserves_publish_date_on_reload` | 重载覆盖 publish | 旧 `publish_date` 保留 |
| 16 | `test_loader_records_source_and_name` | `source_id=NULL` | `source_id` 与 `indicator_name` 正确 |
| 17 | `test_loader_unknown_indicator_fallback` | 无列 | period=raw，publish=NULL，quality=unknown |
| 18 | `test_loader_explicit_temporal_columns_override` | 忽略显式列 | 显式 `period_date/publish_date/quality` 优先 |

### 5.4 Query（6 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 19 | `test_output_has_period_alias_and_publish` | 无新列 | `trade_date` = period；`publish_date` 存在 |
| 20 | `test_as_of_bounds_release` | 无 `as_of` 参数 | `2025-08-05` 排除；`2025-08-15` 包含 CPI `2025-07-31` |
| 21 | `test_null_publish_uses_loaded_at` | 无 as_of | `loaded_at` 在 as_of 前后分别排除/包含 |
| 22 | `test_duplicate_period_selects_latest` | 无去重 | M2 同期两行，`as_of`/默认均取 publish 最新 |
| 23 | `test_legacy_rows_fallback` | 无兜底 | 仅 `trade_date` 的旧行仍可查询 |
| 24 | `test_matrix_as_of_and_other_queries` | 无 as_of | 矩阵按 as_of；`get_indicator_stats`/`get_latest_values`/`get_macro_by_date_range`/`calculate_yoy` 用 period |

### 5.5 敏感度（2 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 25 | `test_sensitivity_as_of_excludes_latest` | 无 `as_of` 参数 | as_of 截断后期宏观，`n_obs` 减少 |
| 26 | `test_sensitivity_uses_period_alignment` | 无 period 列 | 同一 CPI 值按统计期月对齐，结果与按发布月不同 |

### 5.6 红灯/绿灯口径

- **红灯（已实测 2026-11-05）**：规则 8 例通过；迁移 5 + Loader 5 + Query 6 + 新敏感度 2 = **18 failed**，定向文件合计 **23 passed / 18 failed**（失败原因：`macro_data` 缺新列、`migrate_macro_data_temporal` 不存在、`MacroLoader.load` 无 `source_id`、查询无 `as_of`）。
- **S2 实测（2026-11-05）**：迁移 5 例转绿；全量 **384 passed / 13 failed**，剩余 13 个为 S3-S5 预期红（Loader 5 + Query 6 + 敏感度 2）；`ruff` 通过。
- **S3 实测（2026-11-05）**：Loader 5 例转绿；全量 **389 passed / 8 failed**，剩余 8 个为 S4-S5 预期红（Query 6 + 敏感度 2）；`ruff` 通过。
- **S4 实测（2026-11-05）**：Query 6 例转绿；`test_sensitivity_uses_period_alignment` 随 period 口径同步转绿，剩余 1 个为 S5 预期红（`as_of` 参数缺失）；`ruff` 通过。
- **S5 实测（2026-11-05）**：敏感度 2 例转绿；全量 **397 passed**；`ruff check .` 零告警。
- **绿灯**：定向 `tests/test_macro_temporal.py` 24 passed；`tests/test_macro_factor_link.py` 17 passed；全量 **397 passed**；`ruff check .` 零告警。

---

## 6. 生产库迁移与验收（实施后回填）

命令（读写连接）：

```python
from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager

DuckDBConnection._instances.clear()
conn = DuckDBConnection('factor_db.duckdb')
stats = SchemaManager(conn).migrate_macro_data_temporal()
print(stats)
```

验收锚点：

| 项 | 期望 |
|----|------|
| 迁移覆盖 | 4,358 行；12 指标 `period_date` 非空 100% |
| `source_id` | 12 指标全部 `akshare_macro`（原 NULL） |
| CPI | `2025-08-09` → period `2025-07-31`、publish `2025-08-09`、`exact` |
| PMI | `2026-10-01` → period `2026-10-31`、publish `2026-10-31`、`estimated` |
| GDP_QUARTERLY | `2026-09-30` → period 同、publish `2026-10-17`、`estimated` |
| M2 异常 | `2020-02-17` 值为 0.0、`data_quality=50` |
| PIT | `as_of=2025-08-05` 不含 CPI 2025-07 期；`2025-08-15` 含 |
| P7 复跑 | 默认 CPI 敏感度按 period 对齐；记录新 beta 范围与 `n_obs`，与 P7 旧值对比 |
| 回归 | 全量 **397 passed**；`ruff check .` All checks passed |

### 6.1 生产迁移实测（2026-11-05）

```
{
  "rows_total": 4358,
  "period_date_filled": 4358,
  "publish_date_filled": 3528,
  "publish_date_unknown": 830,
  "duplicate_period_rows": 1393
}
```

| 项 | 实测 |
|----|------|
| 迁移覆盖 | 4,358 / 4,358 行 `period_date` 非空（100%）；12 指标 `source_id='akshare_macro'`、`indicator_name` 单值 |
| 质量分布 | `exact` 2,295 / `estimated` 1,233 / `unknown` 830 |
| CPI | `2025-08-09` → period `2025-07-31`、publish `2025-08-09`、`exact` |
| PMI | `2026-10-01` → period `2026-10-31`、publish `2026-10-31`、`estimated` |
| GDP_QUARTERLY | `2026-09-30` → period 同、publish `2026-10-17`、`estimated` |
| M2 异常 | `2020-02-17` = 0.0、`data_quality=50`，保留原值 |
| 备份 | `macro_data_backup_20261105` |
| 索引 | `idx_macro_period_date`、`idx_macro_publish_date` 已创建 |

### 6.2 PIT 查询实测

| 查询 | 结果 |
|------|------|
| `as_of=2025-08-05` CPI_MONTHLY | 158 行，最高统计期 `2025-06-30`（2025-07 期被排除） |
| `as_of=2025-08-15` CPI_MONTHLY | 159 行，最高统计期 `2025-07-31`，publish `2025-08-09` |

### 6.3 P7 复跑对比（统计期对齐）

| 口径 | 行业数 | `n_obs` | beta 范围 | 中位 beta | 区间 |
|------|--------|---------|-----------|-----------|------|
| P7 旧锚点（发布月对齐） | 31 | 45~186 | -0.0113~0.0232 | — | — |
| P8-1 默认 `diff`（统计期对齐） | 31 | 44~306 | -0.0307~0.0016 | -0.0079 | 2000-01 ~ 2025-07-31 |
| P8-1 显式 `pct_change` | 31 | 39~286 | -0.0097~0.0038 | 0.0005 | 2000-01 ~ 2025-07-31 |

- 默认结果无 NaN/Inf；`|beta|` 最大为环保 `801970`（-0.0307，`n_obs=44`），其后为计算机、建筑装饰、机械设备、建筑材料。
- 差异主因：CPI 分组由发布月改为统计期后，x 相对行业月收益前移一个月；P7 的 Inf/符号修复仍有效，但 beta 数值以本表为准。
- `as_of=2025-08-05` 复跑：31 行、`n_obs=43~157`、最高统计期 `2025-06-30`，证明 PIT 截断生效。
