# CPI_MONTHLY 的 beta 有限性调研

> 日期：2026-11-04
> 范围：解释 `get_sector_sensitivity('CPI_MONTHLY')` 返回的 31 个 beta 为何是有限值（非 NaN/±Inf），以及数值为何很小；只做调研，不改代码
> 方法：生产库只读实测（2026-11-04，`factor_db.duckdb`）+ 源码核对 + 外部方法学来源
> 上游：[宏观采集器日期与取值列修复（P6）](../implementation/macro_collector_date_value_implementation.md) / [行业宏观敏感度（P5）](../design/sector_sensitivity_design.md)

---

## 1. 问题定义与范围

P6 重采后，`CPI_MONTHLY` 入库的是 **CPI 当月环比变化率**（单位 %，例如 0.4、-0.1），不是 CPI 水平指数。P5 的 `get_sector_sensitivity` 默认 `transform='pct_change'`，即对“变化率”再求百分比变化：

```
x_t = (cpi_t - cpi_{t-1}) / cpi_{t-1}
beta_i = Cov(x, r_i) / Var(x)
```

实测 31 个行业的 beta 全部有限（`-7.8e-05 ~ 0.0074`），但数值很小。本调研回答两个问题：

1. **为什么 beta 是有限值而不是 NaN/±Inf？**
2. **为什么 beta 数值如此小、是否可信？**

不在本次范围：修改 P5 默认参数、新增指标元数据、实现 P7。

---

## 2. 本地实测事实（生产库，2026-11-04）

### 2.1 CPI 月度序列

| 项 | 实测 |
|----|------|
| 入库月份数 | **354**（1996-02 ~ 2025-08） |
| 值范围 | **-1.8 ~ 2.6**（%） |
| 值为 0.0 的月份 | **21**（5.9%） |
| 值为负的月份 | **142**（40.1%） |

**同类指标类型判别（生产库值域）**：

- 率型（可为零/负）：`CPI_YEARLY -2.2~28.4`、`GDP_YEARLY -6.8~18.3`、`PPI_YEARLY -8.2~13.5`、`M2_YEARLY 0~29.7`、`INTEREST_RATE 3.0~5.77`；
- 水平型（恒正）：`PMI 35.7~59.2`、`PPI_MONTHLY 91.8~113.5`、`M2_MONTHLY 41.8万~359.6万`、`FX 1.59万~3.99万`、`GDP_QUARTERLY 4.78万~140.19万`、`IMPORT_EXPORT 0.65亿~4.47亿`。

> 判别依据：是否可能为 0/负值。率型指标做 `pct_change` 会出现 Inf 或符号反转；水平型恒正指标不受影响。

### 2.2 `pct_change` 与过滤

| 项 | 实测 |
|----|------|
| 原始 `pct_change` 输出 | 353 个变化（首月 1 个 NaN） |
| ±Inf 数量 | **21**（均由前值为 0.0 产生） |
| 被丢弃总数 | **22**（21 Inf + 首月 NaN） |
| 有效变化 | **332** |
| 有效 x 范围 | **-8.0 ~ +8.0** |
| 有效 x 均值/标准差 | **-0.3889 / 2.1131** |
| `Sxx` | **1478.035 > 0** |
| x 恰为 0 的变化 | 13 |
| `\|x\| > 1`（±100%） | **165** |
| `\|x\| > 5`（±500%） | **12** |

> 代码在 [macro_factor_link.py#L261-L276](file:///f:/Coding/Factor_DB/analytics/macro_factor_link.py#L261-L276) 将 ±Inf 替换为 NaN 后 `dropna()`；若缺少这一步，`Sxx=NaN`，beta 也会是 NaN。

### 2.3 负基数导致符号反转

CPI 环比有 142 个负值。当上一期为负时，`pct_change` 的符号与真实变化方向相反：

| 期间 | CPI（%） | 实际变化（百分点） | `pct_change` |
|------|----------|--------------------|--------------|
| 1997-01 | 前值 -0.1 → 0.7 | **+0.8** | **-8.0** |
| 1996-09 | 前值 -0.8 → 1.5 | **+2.3** | -2.875 |
| 1996-08 | 前值 -1.6 → -0.8 | +0.8 | -0.5 |

全样本中，**137 个**“前值为负”的月度转移全部出现符号反转（负基数时代数学上 `(v_t-v_{t-1})/v_{t-1}` 的符号与 `v_t-v_{t-1}` 相反）。

### 2.4 对齐样本与回归量

| 项 | 实测（`start_date=2010-01-01`） |
|----|----------------------------------|
| 对齐后宏观 x 数 | **173** 个月 |
| 31 个行业 `n_obs` | **40 ~ 173**，全部 ≥ `min_obs=12` |
| beta 范围 | **-7.8e-05 ~ 0.0074** |
| `se` 最小值 | **0.0013 > 0** |
| SSE 为 0 的行业 | **0** |
| 缺失/丢弃造成的间隔 | 2010+ 有 **1 个 x 跨 2 个月**（水平序列缺 1 个月）；另有 **14 个零基数 Inf** 被丢弃 |

---

## 3. 候选方案对比

| # | 方案 | 原理 | 优点 | 缺点 | 适用场景 | 结论 |
|---|------|------|------|------|----------|------|
| 1 | `pct_change` + Inf 过滤（现状） | 对 CPI 环比率再求百分比变化 | 数值上不会 NaN；对水平型指标通用 | 零基数产生 Inf；负基数符号反转；±800% 离群值；beta 量纲难解释 | 水平型、恒正指标 | ⚠️ 仅保留为水平型默认 |
| 2 | `diff`（百分点变化） | `x_t = cpi_t - cpi_{t-1}` | 与 CPI 单位一致；符号正确；无除以零 | 量纲为百分点，跨指标不可比 | **率型指标（CPI_MONTHLY/CPI_YEARLY/PPI_YEARLY/M2_YEARLY/GDP_YEARLY/INTEREST_RATE）** | ✅ 推荐 |
| 3 | `level`（直接使用读数） | `x_t = cpi_t` | 最直观；无变换失真；适合围绕 0 波动的率 | 需注意平稳性/自相关；跨指标不可比 | 率型指标、水平型平稳指标 | ✅ 可选 |
| 4 | 缩尾/截断 `pct_change` | 将 ±Inf 或极端值截到分位数 | 保留样本量 | 阈值任意；仍无法修复负基数符号反转 | 仅作稳健性检查 | ⚠️ 不推荐为主口径 |
| 5 | 对数变化 | `log(cpi_t) - log(cpi_{t-1})` | 近似百分比变化 | CPI 环比有 142 个负值，**对数无定义** | 恒正水平序列 | ❌ 不可用 |
| 6 | 标准化 x（z-score） | `x=(x-mean)/std` | 跨指标 beta 可比（每 1 SD） | 损失单位解释；仍以原变换为基础 | 多指标横向比较 | ⏸ 未来可选 |

---

## 4. 社区/学术现状

1. **零基数百分比变化在数学上无定义**。百分比变化公式分母为旧值，旧值为 0 时除零；多个独立来源均建议报告绝对变化或标记 N/A，而不是把 Inf 当作有效观测[1][2]。
2. **pandas `pct_change` 计算的是分数变化（fractional change）**，即当前值相对前值的变化比例；前值为 0 时结果为 Inf，`fill_method` 只处理缺失值、不能处理零分母[3]。
3. **百分数变化与百分点变化必须区分**。计量经济学教材与社区讨论均强调：增长率是“百分比”，而利率/通胀率的绝对变动是“百分点”；对率型变量直接做百分比变化会混淆两种量纲[4][5]。
4. **宏观利率冲击通常以基点/百分点计量**。Bernanke 在美联储演讲中以“联邦基金利率 25bp 的意外变化 → 股价变动 0.75~1.5 个百分点”表述股市对货币政策的敏感度[6]；Becher 等对货币政策指标与股票收益的回归也以指标水平/变化为单位[7]。这与“对率再取百分比变化”的做法不同。
5. **水平序列回归需警惕伪相关**。Ventosa-Santaulària 综述指出含趋势/单位根的水平序列做最小二乘会产生虚假显著关系，应使用差分/去趋势或协整框架[8]。这支持对率型指标优先使用 `diff`，而不是对水平值做百分比变化。
6. **项目内方法基线**：P5 调研已确定“行业月收益对宏观月度变化做单因子 OLS”，并明确 `transform` 可选 `pct_change/diff/level`[9]；P6 明确了 CPI 入库值为环比率[10]。

> “β 有限源于 Inf 过滤”是**代码事实**；“pct_change 对率型指标不合适、应改用 diff”是**基于上述来源的推断**，需在 P7 设计阶段确认默认映射。

---

## 5. 关键结论与推荐方向

### 5.1 为什么 beta 有限

1. **零值产生的 ±Inf 被过滤**：354 个月中 21 个月 CPI=0.0，`pct_change` 产生 21 个 ±Inf；代码替换为 NaN 并丢弃，保留 332 个有限变化。若不过滤，`Sxx=NaN` → beta=NaN。
2. **分母有方差**：过滤后 `Sxx=1478.035 > 0`，满足 `beta=Sxy/Sxx` 的非零分母条件；代码另有 `Sxx<=0` 跳过守卫。
3. **样本量足够**：31 个行业对齐后 `n_obs=40~173`，全部 ≥ `min_obs=12`，没有行业因样本不足被跳过。
4. **残差与标准误有限**：实际数据 SSE 均非 0，`se` 最小 0.0013 > 0，`t=beta/se` 有限；即使 `se=0`，代码也只会把 t 置 0，beta 仍是有限值。
5. **行业收益有限**：月度复利收益由日收益 `pct_change` 得到，价格序列恒正，不会产生 Inf；`dropna` 后进入回归。

因此，“beta 有限”是过滤与守卫的**必然结果**，不代表估计在经济上可靠。

### 5.2 为什么 beta 很小、且不应直接解释

1. **x 的量纲是“率的百分比变化”**：CPI 环比本身在 -1.8%~2.6% 间波动，且 40% 的月份为负；对率再取百分比变化会产生 ±8 的极端值，抬高 `Sxx`、压低 `beta`。
2. **符号不可靠**：137 个负基数转移的 `pct_change` 符号与 CPI 实际变化方向相反（如 -0.1→0.7 实际 +0.8pp，`pct_change=-8`）。因此当前 beta 的正负不能解释为“CPI 上行/下行对行业的影响”。
3. **样本被 silently 缩小**：21 个零值转移被丢弃（21/353≈5.9% 的变化观测），且丢弃发生在“前值为 0”的月份，可能引入样本选择偏差。

### 5.3 推荐

1. **率型指标（CPI_MONTHLY/CPI_YEARLY/PPI_YEARLY/M2_YEARLY/GDP_YEARLY/INTEREST_RATE）优先使用 `transform='diff'`**：x 为百分点变化，符号正确、量纲可解释（beta = 每 1 个百分点变化对应的行业月收益）。
2. **水平型、恒正指标（PPI_MONTHLY 的当月指数、PMI、M2_MONTHLY 数量、FX_RESERVES、GDP_QUARTERLY 绝对值、IMPORT_EXPORT 金额）继续使用 `pct_change`**。
3. **`level` 作为率型指标的可选口径**，适合围绕 0 波动的率；需在文档中注明平稳性假设。
4. **不要用对数变化**：CPI 环比存在大量负值，对数无定义。
5. **P7 采用模块常量而非元数据**：在 `macro_factor_link.py` 内维护 `RATE_TYPE_INDICATORS`，`transform=None` 时自动映射（率型 → `diff`、其余 → `pct_change`）；`value_kind`/`default_transform` 等指标元数据留待后续，不在本次引入 schema 变更。显式 `transform` 仍优先，避免破坏既有调用。
6. **保留 Inf 过滤作为安全网，但建议记录丢弃数量**（当前过滤为静默；可在 P7 增加 warning/统计字段）。
7. **报告 beta 时同时给出 `transform` 与 `n_obs`**，避免把“有限”误读为“稳健”。

---

## 6. 风险与限制

| 风险 | 影响 | 缓解 |
|------|------|------|
| 21 个零值转移被丢弃 | 样本减少 21/353≈5.9%，可能有偏 | 改用 diff 可保留全部 353 个变化 |
| 负基数符号反转 | beta 方向不可解释 | 率型指标改用 diff/level |
| ±8 极端 x | 估计不稳定、beta 被压缩 | diff；或报告稳健性（缩尾） |
| CPI 发布日 vs 统计期 | 同期对齐存在错位 | P5 文档已注明非 PIT；不做事后回移 |
| 1 个 x 跨 2 个月（2010+） | 个别观测为多月变化 | 在数据质量报告中标注；必要时按完整月过滤 |
| 默认参数未改 | 现有调用仍得到小 beta | P7 设计阶段决定默认映射 |

---

## 7. 参考文献

1. *Percent Change From Zero & Between Negative Numbers*：<https://www.wildandfreetools.com/blog/percent-change-zero-negative-numbers/>（旧值为 0 时百分比变化无定义）
2. *Percentage Change Calculator*：<https://www.quantacalculator.com/math/percentage-change>（零基数拒绝计算，建议报告绝对变化）
3. pandas 官方文档 · `DataFrame.pct_change`（分数变化、`fill_method`）：<https://pandas.pydata.org/pandas-docs/version/2.0/reference/api/pandas.DataFrame.pct_change.html>
4. *Intro to Econometrics* · Recapitulation and practical tips（百分比变化 vs 百分点变化）：<https://www.bookdown.org/mkevane/Intro_Metrics_MKWS/recapitulation-of-and-practical-tips-for-regression-analysis.html>
5. Statalist 讨论 · 增长率用百分比、差异用百分点：<https://www.statalist.org/forums/forum/general-stata-discussion/general/1772296-interpreting-log-difference-in-log-log-model>
6. Bernanke, B. (2003). *Monetary policy and the stock market*（25bp 冲击对应股价 0.75~1.5 个百分点）：<https://www.federalreserve.gov/boarddocs/speeches/2003/20031002/default.htm>
7. Becher, D. et al. (2008). *Monetary Policy Indicators as Predictors of Stock Returns*. Journal of Financial Research：<https://onlinelibrary.wiley.com/doi/full/10.1111/j.1475-6803.2008.00243.x>
8. Ventosa-Santaulària, D. (2009). *Spurious Regression*. DOI: <https://doi.org/10.1155/2009/802975>
9. 项目内 · [行业宏观敏感度调研（P5）](sector_sensitivity_research.md)
10. 项目内 · [宏观采集器日期与取值列修复（P6）](../implementation/macro_collector_date_value_implementation.md)
