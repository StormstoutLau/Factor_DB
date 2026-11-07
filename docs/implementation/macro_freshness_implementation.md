# 五列宏观指标新鲜度实施规划（P8-1b）

> 日期：2026-11-08
> 上游：[调研](../research/macro_freshness_research.md) / [设计](../design/macro_freshness_design.md)（含 §9 评审记录）
> 本文档为编码前规划；生产采集与验收结果在 §6 回填。

---

## 1. 分步实施计划

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | 新增 `MacroSourceSpec`（frozen dataclass）与 `FRESHNESS_SOURCES`（4 指标）；导入 `estimate_publish_date` | 单测 1：配置完整性、不含 `GDP_YEARLY` |
| S2 | 修正 `_resolve_function` 解析顺序：先试显式 `primary`，再回退 `FUNCTION_CANDIDATES`（§2 E1） | 单测 10：M2 freshness 不落到旧函数；既有 `TestAKShareResolution` 全绿 |
| S3 | 覆写 `collect`：history = `super().collect(params)`，freshness 追加 `_collect_freshness`；失败隔离 | 单测 5/7/8 |
| S4 | `transform_data` 增加 `query['spec']` 分支；日期列探测抽 `_detect_date_column`；history 分支移除硬编码 `source_id` 列 | 单测 2/3/4/6/9 |
| S5 | 新增 `tests/test_macro_freshness.py` 端到端 6 例；全量回归；生产采集；文档同步 | 全量 **413 passed**；`ruff` 零告警；§6 锚点 |

TDD 顺序：先写 `tests/test_collect.py` 新增 10 例与 `tests/test_macro_freshness.py` 6 例 → 运行确认红 → S1-S4 → 定向绿 → 全量回归 → 生产采集验收。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | Python `>=3.9`（仅用 `dataclasses`、`calendar.monthrange`、`datetime.date`）；AKShare 本地 **1.18.64** 已实测 4 个目标函数与列名（调研 §2/§3） |
| **依赖** | 不新增依赖；不改 `pyproject.toml` |
| **兼容** | `collect(params=None)` 签名与基类一致；`transform_query` 不变；history 输出仅少一列 `source_id`（已 grep：无测试/调用方依赖采集器输出该列）；freshness 仅对 4 指标生效，其余 8 指标走原路径 |
| **接口** | 新增类属性 `FRESHNESS_SOURCES`；`_collect_freshness` 为私有方法；`transform_data` 通过可选 `query['spec']` 分支，不改变 history 调用契约 |
| **签名** | `MacroSourceSpec` 使用 `@dataclass(frozen=True)`；`start_date: date`；无可变默认参数 |
| **低效操作** | 无 `melt`、无逐行 INSERT、无全表扫描；freshness 表仅 225/249 行；入库复用 `MacroLoader` 单次 `INSERT ... SELECT` |
| **内存** | 4 指标合计 ≤ ~950 行、约 10 列，远低于 32GB；concat 后总量 ≤ ~1.5k 行 |
| **数据一致性** | 新行 `trade_date` = 月初 1 日，与旧行发布日（如 2025-08-09）不同键，统计期无重叠；重复采集走 `ON CONFLICT DO UPDATE` 幂等；`publish_date` 由 `COALESCE` 保留 |
| **失败隔离** | `collect` 前置检查 `_error_state`/`_stop`；`_collect_freshness` 内部 `try/except` 仅 warning，不置 `_error_state`（[base.py L166-L189](file:///f:/Coding/Factor_DB/osint/base.py#L166-L189) 异常即置错并短路后续 collect） |
| **计数** | freshness 行数需累加 `self._collected_count`，否则 `collect` 覆写后计数偏少 |
| **事件** | 不重复 `notify_listeners`：基类已用 history 结果通知；pipeline 使用 `collect` 返回值，事件日志不受影响 |
| **网络** | 每指标最多 2 次调用（history + freshness），4 指标共 8 次；仅生产采集时执行，CI 全部用假模块 |
| **日期边界** | `trade_date >= spec.start_date`：2025-07-01 排除、2025-08-01 保留；`period_date` = 当月月末；`publish_date`：CPI/PPI 次月 9 日、M2 次月 13 日 |
| **E1 解析冲突** | **设计稿原写法有隐患**：`_resolve_function` 先遍历 `FUNCTION_CANDIDATES`，而 `FUNCTION_CANDIDATES['M2_YEARLY'] = ('macro_china_m2_yearly', 'macro_china_money_supply_yearly')`；且 `extract_data` 内部会二次解析。若直接按原稿调用，M2 freshness 会解析到**旧源** `macro_china_m2_yearly`，目标列不存在 → 空表。修正为「显式 primary 优先，再回退候选」：现有 history 的 primary 均为候选首项，行为不变；M2 freshness 稳定命中 `macro_china_money_supply` |

---

## 3. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `osint/collectors/akshare_collector.py` | 新增 `MacroSourceSpec`/`FRESHNESS_SOURCES`；`_resolve_function` 解析顺序；`collect` 覆写；`_collect_freshness`；`_detect_date_column`；`transform_data` freshness 分支；移除硬编码 `source_id` |
| `tests/test_collect.py` | 新增 `FreshnessAK` 假模块 + 10 例（不改动既有 `NormalizationAK`/`InvalidDateAK`） |
| `tests/test_macro_freshness.py`（新增） | 采集 → Loader → Query 端到端 6 例 |
| `docs/design/macro_freshness_design.md` | §3.2 补充 E1 解析顺序说明（实施规划回填） |
| `docs/implementation/macro_freshness_implementation.md` | 本文件（§6 回填） |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第二十二节 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` / `CODE_WIKI.md` | 实施验收后同步（测试数 397 → 413、采集器双阶段说明） |

**不改**：`core/schema.py`、`core/macro_temporal.py`、`loaders/macro_loader.py`、`query/macro_query.py`、`analytics/macro_factor_link.py`、`osint/pipeline.py`、`cli.py`。

---

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `AKShareMacroCollector` 类属性 | 无 freshness 配置 | 新增 `FRESHNESS_SOURCES: Dict[str, MacroSourceSpec]`（4 项） |
| `_resolve_function` | `(self, indicator, primary)`：先遍历 `FUNCTION_CANDIDATES` | `(self, indicator, primary)`：先试 `primary`，不存在再回退候选；签名不变 |
| `collect` | 继承 `BaseCollector.collect`（单阶段 TET） | 覆写：history（`super().collect`）+ freshness（`_collect_freshness`）合并 |
| `transform_data` | 仅 history：`query` 需含 `indicator` | `query` 含可选 `spec` 时走 freshness 分支；否则行为不变 |
| history 输出列 | `trade_date, indicator_id, indicator_name, value, value_type, data_quality, source_id` | 去掉 `source_id`（交由 `MacroLoader._fill_source_id` 注入 `akshare_macro`） |
| freshness 输出列 | — | `trade_date, indicator_id, indicator_name, value, value_type, data_quality, period_date, publish_date, publish_date_quality` |
| `MacroLoader.load` / `MacroQuery` / CLI / 表结构 | — | 均不变 |

---

## 5. 测试计划（TDD）

新增 **16** 例；目标全量 **397 + 16 = 413 passed**。

### 5.1 采集器（`tests/test_collect.py`，10 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_freshness_sources_config` | `FRESHNESS_SOURCES` 不存在 | 恰含 4 指标；列名/起点正确；不含 `GDP_YEARLY` |
| 2 | `test_freshness_truncates_to_start_date` | 无 freshness 分支 | 新源含 2008/2025-07 行，输出仅 2025-08 起；`trade_date` 为月初 |
| 3 | `test_freshness_writes_explicit_temporal_columns` | 无 `period_date` 列 | 2025-08 → period `2025-08-31`、publish `2025-09-09`、`estimated` |
| 4 | `test_freshness_uses_configured_value_column` | 走启发式取到指数列 | CPI 环比 0.0 / 同比 -0.4、PPI 同比 -2.9、M2 同比 8.8 |
| 5 | `test_collect_merges_history_and_freshness` | 仅 history | history 2025-07 期 + freshness 2025-08/09 期同时存在 |
| 6 | `test_gdp_yearly_stays_history_only` | 无 | 输出无 `period_date` 列、期数不增 |
| 7 | `test_freshness_failure_does_not_set_error_state` | 异常外抛 | 返回 history；`_error_state is False` |
| 8 | `test_freshness_missing_function_returns_history` | 无 | 缺 `macro_china_ppi` → 仅 history，不抛异常 |
| 9 | `test_history_output_has_no_source_id_column` | 仍含 `source_id` | history 输出无该列 |
| 10 | `test_m2_freshness_prefers_money_supply_over_stale_yearly` | 解析到旧函数 → 空表 | 同时存在两函数时仍取 `macro_china_money_supply` 的同比列 |

### 5.2 端到端（`tests/test_macro_freshness.py`，6 例）

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 11 | `test_end_to_end_latest_period_and_source_id` | 无新期 | 4 指标最新 period `2026-09`；库内 `source_id='akshare_macro'` |
| 12 | `test_history_rows_preserved` | 无 | ≤2025-07-31 行数与值不变 |
| 13 | `test_reload_idempotent_and_publish_preserved` | 重复行 | 两次 load 行数不变；`publish_date` 非空 |
| 14 | `test_pit_boundary` | 无新行 | `as_of=2025-09-05` 不含 2025-08 CPI；`2025-09-15` 含 |
| 15 | `test_gdp_yearly_not_written` | 无 | `GDP_YEARLY` 无新增期，最高期不变 |
| 16 | `test_freshness_failure_keeps_history_usable` | 失败阻断 | freshness 异常时 history 仍可入库并查询 |

### 5.3 红灯 / 绿灯口径（实测）

- **红灯（2026-11-08）**：定向 34 例中 **11 failed / 23 passed**。11 个功能用例失败（`FRESHNESS_SOURCES` 缺失、`_collect_freshness` 不存在、history 仍含 `source_id`、M2 未追加新期、端到端最新期未推进/PIT 无新行等）；另 5 个新用例为历史行为守护（GDP 不受影响、freshness 失败隔离、缺函数回退、历史行保留、GDP 不新增），实现前即通过。
- **绿灯**：定向 `tests/test_collect.py` + `tests/test_macro_freshness.py` **34 passed**；全量 **413 passed**（397 + 16）；`ruff check .` All checks passed。

---

## 6. 生产采集与验收（实施后回填）

### 6.1 执行步骤

1. **基线记录**（采集前）：4 指标 `MAX(period_date)`、`COUNT(*)`、`period_date <= 2025-07-31` 行数；全表 4,358 行。
2. **分指标采集**（避免刷新无关指标）：

```powershell
py -3.12 -m Factor_DB.cli collect --source akshare_macro --indicator CPI_MONTHLY --db factor_db.duckdb
py -3.12 -m Factor_DB.cli collect --source akshare_macro --indicator CPI_YEARLY --db factor_db.duckdb
py -3.12 -m Factor_DB.cli collect --source akshare_macro --indicator PPI_YEARLY --db factor_db.duckdb
py -3.12 -m Factor_DB.cli collect --source akshare_macro --indicator M2_YEARLY --db factor_db.duckdb
```

3. **幂等复跑**：再跑一次 `CPI_MONTHLY`，核对库内总行数不变（CLI 打印的是处理行数，不是新增行数）。
4. **查询验收**：按 §6.2 锚点逐项核对（PIT 用 `MacroQuery.get_macro_data`）。
5. **回归**：`py -3.12 -m pytest -q` 与 `py -3.12 -m ruff check .`。

### 6.2 验收锚点

| 项 | 期望 |
|----|------|
| 4 指标最新统计期 | `2026-09` |
| 各指标新增期数 | 14（2025-08 ~ 2026-09） |
| 全表行数 | 预期 4,358 + 56 = 4,414；若旧源历史补出缺失期（如 CPI 2019-10），允许少量偏差并记录 |
| `CPI_MONTHLY` 2025-08 | 0.0，period `2025-08-31`，publish `2025-09-09`，`estimated` |
| `CPI_YEARLY` 2025-08 | -0.4 |
| `PPI_YEARLY` 2025-08 | -2.9 |
| `M2_YEARLY` 2025-08 | 8.8 |
| 历史 | ≤2025-07-31 行数与值不变 |
| `GDP_YEARLY` | `MAX(period_date)=2025-06-30`，不新增 |
| `source_id` | 4 指标全部 `akshare_macro` |
| 幂等 | 二次采集后总行数不变 |
| PIT | `as_of=2025-09-05` 不含 2025-08 CPI；`2025-09-15` 含 |
| 回归 | 全量 **413 passed**；`ruff` 零告警 |

### 6.3 实测（2026-11-08）

| 项 | 实测 |
|----|------|
| 新增行数 | +56（CPI_MONTHLY 368、CPI_YEARLY 489、PPI_YEARLY 375、M2_YEARLY 351；各 +14） |
| 全表行数 | **4,414**（4,358 + 56，无历史补缺偏差） |
| 4 指标最新统计期 | 均 `2026-09-30`；最新 publish：CPI/PPI `2026-10-09`、M2 `2026-10-13` |
| 拼接锚点 2025-08 | CPI 环比 0.0 / CPI 同比 -0.4 / PPI 同比 -2.9 / M2 同比 8.8；period `2025-08-31`，quality `estimated` |
| 2025-07 历史锚点 | CPI 0.4 / CPI 同比 0.0 / PPI -3.6 / M2 8.8；publish `2025-08-09`（M2 `2025-08-13`），quality `exact` |
| 历史行 | 4 指标 `period_date <= 2025-07-31` 行数保持 354/475/361/337 |
| `GDP_YEARLY` | 59 行，最高期 `2025-06-30`，未新增 |
| `source_id` | 5 指标均单值 `akshare_macro`（`COUNT(DISTINCT source_id)=1`） |
| 幂等 | 二次采集 CPI_MONTHLY 后总行数 4,414、CPI 368 行/368 个 trade_date 不变 |
| PIT | `as_of=2025-09-05` 最高期 `2025-07-31`；`as_of=2025-09-15` 最高期 `2025-08-31` |
| 回归 | 全量 **413 passed**；`ruff check .` All checks passed |

---

## 7. 实施规划 Review 门槛自检

- [x] **依赖版本已核实**：AKShare 1.18.64 真实调用（调研 §2/§3）；不新增依赖
- [x] **文件路径真实存在**：`osint/collectors/akshare_collector.py`、`tests/test_collect.py`、`docs/spec/REVIEW_CHECKLIST.md` 等均已核对
- [x] **无低效操作**：无 `melt`、无逐行 INSERT、无全表扫描；数据量 ≤ ~950 行
- [x] **接口变更列出 Before/After**：见 §4
- [x] **32GB 内存约束**：数据量可忽略
- [x] **工程隐患排除**：E1（M2 freshness 解析冲突）已识别并给出最小修正 + 单测
- [x] **失败隔离**：freshness 异常不影响 history 与其他指标
