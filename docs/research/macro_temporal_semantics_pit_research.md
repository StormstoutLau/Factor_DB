# 宏观数据时间语义与 PIT 对齐调研（P8-0/P8-1）

> 日期：2026-11-04
> 范围：审计 `macro_data` 12 个指标的日期语义，评估统一时间模型与 Point-in-Time（PIT）方案；只做调研，不改 schema/代码
> 方法：生产库只读审计（`factor_db.duckdb`）+ 采集器/查询/分析源码核对 + 国家统计局发布日程与公开文章
> 上游：[P7 之后的任务分析与优先级](post_p7_backlog_analysis.md)
> 下游：[宏观数据时间语义与 PIT 对齐设计](../design/macro_temporal_semantics_pit_design.md)

---

## 1. 问题定义与范围

### 1.1 要解决的问题

`macro_data.trade_date` 目前同时承载三种语义：

1. **统计期起点/终点**（reference period）：如 PMI、PPI_MONTHLY、GDP_QUARTERLY；
2. **发布日期**（release date）：如 CPI、CPI_YEARLY、PPI_YEARLY、M2_YEARLY、GDP_YEARLY；
3. **日频观测日**：如 INTEREST_RATE。

同一个字段混用三种语义，导致：

- 月度对齐可能错位：CPI 的 `2025-08-09` 描述 7 月 CPI，却被归入 8 月；
- 前视偏差：PMI 的 `2026-10-01` 是 10 月统计期，实际发布在 10 月末，早于发布日约 30 天；
- 无法做 PIT 查询：没有 `publish_date` / `loaded_at`，无法回答“某日当时能看到什么”。

### 1.2 P8-0 与 P8-1 的边界

| 任务 | 目标 | 本文档覆盖 |
|------|------|-----------|
| P8-0 | 修正“`trade_date`=统计期”的错误表述；建立 12 指标日期语义审计表 | ✅ 本文 §2-§3 |
| P8-1 | 选定统一时间模型与 PIT 方案，给出迁移方向 | ✅ 本文 §4-§5 |
| 设计/实施 | schema 变更、loader/query/analytics 改造、TDD | ❌ 后续阶段 |

### 1.3 非目标

- 不在本阶段修改 `macro_data` 表结构、采集器、CLI；
- 不实现修订版本（vintage）的完整存储，只评估是否需要；
- 不重跑 P7 beta，只在结论中标注其受影响范围。

---

## 2. 本地实测事实（生产库，2026-11-04）

### 2.1 12 指标日期语义审计

| 指标 | 行数 | 覆盖区间 | 日期形态 | 月首占比 | 最新日期 | 语义判定 |
|------|------|----------|----------|----------|----------|----------|
| `CPI_MONTHLY` | 354 | 1996-02-01~2025-08-09 | 2011 前基本月首；2012 起发布日（8~18 日） | 195/354 = 55% | 2025-08-09 | **混合** |
| `CPI_YEARLY` | 475 | 1986-02-01~2025-08-09 | 同上 | 298/475 = 63% | 2025-08-09 | **混合** |
| `PPI_YEARLY` | 361 | 1995-08-01~2025-08-09 | 月首；后期发布日（9~11 日） | 184/361 = 51% | 2025-08-09 | **混合** |
| `M2_YEARLY` | 337 | 1998-02-01~2025-08-13 | 月首；后期发布日（11~14 日）；7 组同月多行 | 153/337 = 45% | 2025-08-13 | **混合 + 重复** |
| `GDP_YEARLY` | 59 | 2011-01-20~2025-07-15 | 季后发布日（15~20 日） | 0 | 2025-07-15 | **发布日** |
| `INTEREST_RATE` | 1,539 | 2013-10-25~2026-10-20 | 日频观测（20/22 日为主） | 37/1,539 = 2.4% | 2026-10-20 | **观测/发布日** |
| `FX_RESERVES` | 225 | 2008-01-01~2026-09-01 | 每月 1 日 | 100% | 2026-09-01 | **统计期起点** |
| `IMPORT_EXPORT` | 225 | 2008-01-01~2026-09-01 | 每月 1 日 | 100% | 2026-09-01 | **统计期起点** |
| `M2_MONTHLY` | 225 | 2008-01-01~2026-09-01 | 每月 1 日 | 100% | 2026-09-01 | **统计期起点** |
| `PMI_MANUFACTURING` | 226 | 2008-01-01~2026-10-01 | 每月 1 日 | 100% | 2026-10-01 | **统计期起点** |
| `PPI_MONTHLY` | 249 | 2006-01-01~2026-09-01 | 每月 1 日 | 100% | 2026-09-01 | **统计期起点** |
| `GDP_QUARTERLY` | 83 | 2006-03-31~2026-09-30 | 季末（3-31/6-30/9-30/12-31） | 0 | 2026-09-30 | **统计期末** |

> 月首占比 = `trade_date.day == 1` 的行数 / 总行数。数据来自 2026-11-04 生产库只读查询。

### 2.2 同一指标内部也存在语义切换（事实）

`CPI_MONTHLY` 按年份的日号分布：

| 年份 | 行数 | 最小日 | 中位日 | 最大日 |
|------|------|--------|--------|--------|
| 2006~2011 | 12/年 | 1 | 1 | 1 |
| 2012 | 12 | 1 | 9 | 15 |
| 2013~2024 | 12/年 | 8~9 | 9~10 | 13~18 |
| 2025 | 8 | 9 | 9 | 10 |

即 2011 年及以前基本是**统计期起点**（2012 年尚有 1 条月首），2012 年起改为**发布日期**。`CPI_YEARLY`、`PPI_YEARLY`、`M2_YEARLY` 同样存在早期月首、后期发布日的混合。

### 2.3 重复与异常（事实）

- `M2_YEARLY`：337 行对应 330 个自然月，存在 7 组“同月多行”（14 行）。其中 `2020-02-17 = 0.0`、`2020-02-20 = 8.4`；`2019-04-08 = 8.0`、`2019-04-12 = 8.6`。`0.0` 很可能是缺失值/异常编码，不是真实同比。
- `INTEREST_RATE`：1,539 行 / 157 个月，2014 年、2019 年为**日频**（每个工作日一行，值相同），2024 年起为月度发布日一行。它不是标准月频指标，现有敏感度按月取 `.last()` 只是降采样。
- `CPI_MONTHLY` 有 1 个缺月；`M2_YEARLY` 有 1 个缺月。

### 2.4 五列指标新鲜度（事实）

| 指标 | 最新日期 | 相对 2026-11-04 |
|------|----------|------------------|
| `CPI_MONTHLY` | 2025-08-09 | 约 15 个月 |
| `CPI_YEARLY` | 2025-08-09 | 约 15 个月 |
| `PPI_YEARLY` | 2025-08-09 | 约 15 个月 |
| `M2_YEARLY` | 2025-08-13 | 约 15 个月 |
| `GDP_YEARLY` | 2025-07-15 | 约 16 个月 |

对照：`PPI_MONTHLY` / `PMI_MANUFACTURING` / `M2_MONTHLY` / `FX_RESERVES` / `IMPORT_EXPORT` 已到 2026-09/10。五列发布日历源可能已截断，需在 P8-1b 处理。

### 2.5 代码事实

| 位置 | 事实 | 影响 |
|------|------|------|
| [schema.py](file:///f:/Coding/Factor_DB/core/schema.py#L160-L171) | `macro_data` 主键 `(trade_date, indicator_id, value_type)`，无 `publish_date` / `loaded_at` | 无法存同一统计期的多个版本；无法 PIT |
| [macro_loader.py](file:///f:/Coding/Factor_DB/loaders/macro_loader.py#L198-L208) | 写入固定 `indicator_name=indicator_id`、`source_id=NULL`，`INSERT OR REPLACE` | 溯源缺失；修订会覆盖旧值 |
| [macro_query.py](file:///f:/Coding/Factor_DB/query/macro_query.py#L60-L66) | 查询按 `trade_date` 过滤/排序，无 `as_of` | 无法按“当时可见”查询 |
| [macro_factor_link.py](file:///f:/Coding/Factor_DB/analytics/macro_factor_link.py#L253-L259) | 按 `trade_date` 的 `Period('M')` 分组、取 `.last()` | CPI 发布日被归到发布月；PMI 统计期起点被当作可得日 |

---

## 3. 外部规范与社区现状

### 3.1 国家统计局发布日程（事实）

国家统计局《2026 年主要统计信息发布日程表》给出：

- **CPI / PPI 月度报告**：每月 9/11/9/10/11/10/9/9/9/14/9/9 日 9:30 发布（[发布日程](https://www.stats.gov.cn/sj/fbrc/bnxxfb/)）。
- 表注 2：**“本表公布的发布内容（除采购经理指数外）均为指标上月、上季、上年或累计数据。”** 即 CPI/PPI/GDP 等发布的是上一期数据。
- **PMI 月度报告**：1 月 31 日、3 月 4 日（春节注 5）、3 月 31 日、4 月 30 日……12 月 31 日 9:30 发布；表注 2 将 PMI 作为例外，说明 PMI 发布的是当月数据。
- **GDP 季度数据**：在“国民经济运行情况”发布会发布，2026 年各季度为 1/19、4/16、7/15、10/19 等日期（季末后 15~19 天）。

国家统计局 2026 年 7 月 9 日文章《2026 年 6 月份居民消费价格同比上涨 1.0%》直接证明：**6 月 CPI 在 7 月 9 日发布**，发布日比统计期晚约 9 天（[文章](https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202607/t20260709_1964084.html)）。

> **未核实项**：`M2_MONTHLY`（PBOC）、`FX_RESERVES`（SAFE）、`IMPORT_EXPORT`（海关总署）的具体发布日在本调研中**无官方引用**。设计中的"次月 13/7/10 日"为假设值，实施前需以官方发布日程核实；核实前一律标 `estimated`。

### 3.2 PIT / vintage 数据实践（事实）

- **ALFRED（St. Louis Fed）**：允许检索“某一历史日期当时可得的每一期经济数据发布（vintage）”，即按发布日期重建当时的数据快照（[ALFRED](https://alfred.stlouisfed.org/)）。
- **FXMacroData** 的 PIT 回测指南强调：参考期（reference period）与发布时刻（announcement datetime）混淆是宏观事件回测最常见错误；一个值只有在事件发生前已知才能进入研究（[Point-in-Time Macro Backtesting](https://fxmacrodata.com/articles/point-in-time-macro-backtesting)、[Announcement Timing](https://fxmacrodata.com/articles/economic-announcement-timing)）。
- **Alpha Strategic Growth** 指出：在 3 月 31 日使用“3 月 CPI”是在发布前两周就使用尚不存在的数据；宏观数据通常滞后 1~2 个月（[Look-Ahead Bias](https://alphastrategicgrowth.com/blog/look-ahead-bias/)）。
- **Swoopr Investment** 指出：用最终修订值回测会引入前视；应使用当时可得的数据（[Point-in-Time Macro Data](https://www.getswoopr.com/macro-economics-market-regimes/point-in-time-macro-data-and-revision-risk/)）。

### 3.3 对本地数据的含义（推断）

结合 §2 与 §3.1：

- `CPI_MONTHLY` 的 `2025-08-09 = 0.4` 描述 2025 年 7 月 CPI；若按 `trade_date` 的月份对齐，会被错误归到 8 月。
- `PMI_MANUFACTURING` 的 `2026-10-01 = 49.3` 描述 10 月 PMI，但实际在 10 月 31 日左右发布；把它当作 10 月 1 日可得，构成约 30 天**前视**。
- `PPI_MONTHLY`、`M2_MONTHLY` 的 `2026-09-01`，实际发布在 10 月中旬；`FX_RESERVES`、`IMPORT_EXPORT` 的月初日期也早于实际发布；`GDP_QUARTERLY` 的季末早于季后 15~19 天的发布。
- `CPI_YEARLY`、`PPI_YEARLY`、`M2_YEARLY`、`GDP_YEARLY` 存的是发布日，本身不前视，但代表上一期，且历史早期又混入月首日期。

> 结论：**当前 `get_sector_sensitivity` 的结果既可能月份错位，也可能含前视**；P7 修复了 Inf 与符号反转，但没有修复时间对齐。

---

## 4. 候选方案对比

| # | 方案 | 原理 | 优点 | 缺点 | 适用场景 | 结论 |
|---|------|------|------|------|----------|------|
| A | **双日期单行**：`period_date` + `publish_date` + `loaded_at` | 一个统计期一行；`publish_date` 记录首次可得日 | 语义清晰；查询可 `as_of`；迁移量小（4,358 行）；可保留 `trade_date` 作为兼容别名 | 不能存同一统计期的多个修订版本 | 当前无修订版本数据；P8-1 最小闭环 | ✅ **推荐** |
| B | **双日期 + vintage 表**：`macro_data` 存最新，`macro_data_vintage` 追加每次发布 | 每个 vintage 一行，`publish_date`/`loaded_at` 区分 | 真正 PIT；可回放修订 | 双写一致性；存储翻倍；当前数据源无完整 vintage | 有修订/多版本需求时 | ⏸ P9 可选 |
| C | **仅元数据**：`macro_indicators.date_semantics` + `reference_lag_days` | 不改表，按指标解释 `trade_date` | 改动最小；可立即纠偏文档 | `trade_date` 仍然混合；无法 PIT；易误用 | P8-0 过渡 | ⚠️ 仅作过渡 |
| D | **只规范 `trade_date` 到月末/季末** | 把月首/季末统一为期末，不加发布日期 | 修复月份错位；改动中等 | 仍有前视；无法 PIT | 只做对齐、不做 PIT | ❌ 不单独采用 |

### 4.1 推荐方案 A 的字段与主键

```text
macro_data
  indicator_id        VARCHAR
  value_type          VARCHAR
  period_date         DATE        -- 统计期：月度统一为月末；季度为季末
  publish_date        DATE        -- 首次发布日期；历史不可考时 NULL
  loaded_at           TIMESTAMP   -- 入库时间，PIT 兜底
  value               DOUBLE
  data_quality        INTEGER
  source_id           VARCHAR
  PRIMARY KEY (period_date, indicator_id, value_type)
```

- 兼容策略：保留 `trade_date` 作为 `period_date` 的别名（迁移期视图或同步列），现有 `MacroQuery` / `get_sector_sensitivity` 不需要一次性改签名。
- PIT 查询：`as_of` 优先过滤 `publish_date <= as_of`；`publish_date IS NULL` 时回退 `loaded_at <= as_of`，并在结果中标注“发布日期未知”。
- 月频对齐：分析层按 `period_date` 分组；`get_sector_sensitivity` 默认使用 `period_date`，可选 `use_publish_date=True` 做可得日对齐。

### 4.2 为什么不推荐直接复用 `trade_date`

`trade_date` 已被 12 个指标、`MacroQuery`、`MacroFactorLink`、`MacroLoader` 和现有测试广泛使用。直接改语义会：

- 改变现有查询结果（尤其 CPI 的月份归属）；
- 破坏 `(trade_date, indicator_id, value_type)` 主键的稳定性；
- 让历史数据迁移难以回滚。

方案 A 用 `period_date` 明确新语义、保留 `trade_date` 兼容，是风险最低的路径。

---

## 5. 关键结论与推荐方向

### 5.1 P8-0：立即纠偏（低风险）

1. 修正以下“`trade_date` 为统计期”的表述：
   - [macro_factor_link.py](file:///f:/Coding/Factor_DB/analytics/macro_factor_link.py#L240-L242) 的 Notes；
   - [sector_sensitivity_design.md](file:///f:/Coding/Factor_DB/docs/design/sector_sensitivity_design.md) 与 [sector_sensitivity_implementation.md](file:///f:/Coding/Factor_DB/docs/implementation/sector_sensitivity_implementation.md) 中关于“同期关联”的描述；
   - P5/P7 相关 DEVLOG 条目。
2. 在 `macro_indicators` 或文档中增加 12 指标日期语义表（本文 §2.1 可直接落档）。
3. 明确 P7 beta 的适用边界：Inf/符号修复有效，但月份对齐与前视仍待 P8-1。

### 5.2 P8-1：推荐分步实施

| 步 | 内容 | 验证 |
|----|------|------|
| S1 | 建立 12 指标日期映射表（语义、参考期、发布日规则） | 单测覆盖每个指标 |
| S2 | schema 迁移：新增 `period_date` / `publish_date` / `loaded_at`；`MacroLoader` 写入 `source_id`、真实 `indicator_name` | 迁移前后 4,358 行对账 |
| S3 | 回填：五列指标按“发布月 - 1”推导 `period_date`；多列指标按月首/季末；历史不可考 `publish_date=NULL` | 抽样锚点 + 全量非空率 |
| S4 | `MacroQuery.as_of` + `get_sector_sensitivity` 按 `period_date` 对齐 | TDD；P7 beta 复跑对比 |
| S5 | 五列指标新鲜度：替代源或补齐 2025-08 之后 | 最新日期 ≥ 2026-09 |

### 5.3 指标优先级

1. **CPI / PPI / PMI / GDP**：影响最大，先做；
2. **M2 / FX / 进出口**：发布时间较稳定，可规则化；
3. **INTEREST_RATE**：日频序列，需单独决定“月内取最后”还是保留日频。

### 5.4 对 P7 的影响

- P7 的 `RATE_TYPE_INDICATORS` 与 `diff` 默认仍然正确；
- 但 CPI 按月分组从“发布月”改为“统计期月”后，beta 会变化，需要重新验收；
- `M2_YEARLY` 的 0.0 异常与同月多行应在 S3 一并清理或标注。

---

## 6. 风险与限制

| 风险 | 影响 | 缓解 |
|------|------|------|
| 2012 年前 CPI 日期可能是统计期而非发布日 | 早期 `period_date` 推导可能错误 | 对早期样本标记 `date_semantics='unknown'` 或按统计期处理；文档注明 |
| 历史 `publish_date` 不可考 | PIT 只能回退 `loaded_at`（迁移时间） | 结果中标注“发布日期未知”；不要把迁移时间当真实可得日 |
| 发布日程每年变化 | 规则化滞后日可能失效 | 优先抓取当年日程；规则仅作兜底 |
| M2/外储/进出口滞后日未核实 | 规则化发布日可能偏差数日 | 实施前以 PBOC / SAFE / 海关日程核实；核实前一律 `estimated` |
| 主键/查询迁移破坏现有调用 | CLI/测试/下游失败 | 保留 `trade_date` 兼容视图；迁移前后全量对账；分阶段上线 |
| 数据源无修订版本 | 无法完整回放 vintage | P8-1 只做发布日期 PIT；vintage 留 P9 |
| M2 同月多行/0.0 异常 | 回归使用错误值 | S3 清理规则：同月取最后有效值，0.0 标记异常 |
| INTEREST_RATE 日频 | 月频降采样口径不明确 | 明确 `.last()` 语义，或改为日频分析 |

---

## 7. 参考文献

1. 国家统计局 · 2026 年主要统计信息发布日程表：<https://www.stats.gov.cn/sj/fbrc/bnxxfb/>
2. 国家统计局 · 2026 年 6 月份居民消费价格同比上涨 1.0%（成文日期 2026-07-09）：<https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202607/t20260709_1964084.html>
3. ALFRED · Real-Time Data / Vintage：<https://alfred.stlouisfed.org/>
4. ALFRED · Release Calendar：<https://alfred.stlouisfed.org/releases/calendar>
5. FXMacroData · Point-in-Time Macro Backtesting：<https://fxmacrodata.com/articles/point-in-time-macro-backtesting>
6. FXMacroData · Why Announcement Timing Matters：<https://fxmacrodata.com/articles/economic-announcement-timing>
7. Alpha Strategic Growth · Look-Ahead Bias：<https://alphastrategicgrowth.com/blog/look-ahead-bias/>
8. Swoopr Investment · Point-in-Time Macro Data & Revision Risk：<https://www.getswoopr.com/macro-economics-market-regimes/point-in-time-macro-data-and-revision-risk/>
9. 项目内 · [P6 宏观采集器日期与取值列修复](../implementation/macro_collector_date_value_implementation.md)
10. 项目内 · [P7 CPI_MONTHLY beta 修复](../implementation/cpi_monthly_beta_fix_implementation.md)
