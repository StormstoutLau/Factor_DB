# 行业收益率对宏观指标的敏感度：方法调研

> 日期：2026-11-02
> 范围：为 `MacroFactorLink.get_sector_sensitivity()` 确定敏感度度量口径、频率对齐与统计输出（只做调研，不改代码）
> 上游：[行业收益率数据源调研](industry_return_data_source_research.md)（申万行业指数已入库 `alternative_data`）
> 方法：学术检索（OpenAlex / Crossref，经 `mcp_paper-search`）+ 仓库内数据/接口核对

---

## 1. 问题定义

`analytics/macro_factor_link.py#L195-L217` 的 `get_sector_sensitivity()` 目前只取宏观数据，随后返回空表：

```python
# TODO: 需要行业收益率数据，这里预留接口
```

P4 已将申万一级行业日收益率写入 `alternative_data`（`data_type='industry_return'`、`entity_type='industry'`、`data_subtype` 为级别），本任务补齐敏感度计算。

### 输入 / 输出需求

| 维度 | 要求 |
|------|------|
| 输入 | 单个宏观指标（`macro_data.indicator_id`）+ 可选行业代码列表 + 日期范围 |
| 数据源 | `macro_data`（月频为主）与 `alternative_data`（日频行业收益） |
| 输出 | 每个行业一行：敏感度系数、相关性、显著性、样本数与区间 |
| 语义 | “行业月收益对宏观指标月度变化的线性暴露”，用于横向比较行业板块 |
| 约束 | 不新增依赖、不触网；生产库缺失数据时返回空表而非异常 |

---

## 2. 候选方案对比

| # | 方案 | 原理 | 优点 | 缺点 | 适用场景 | 本次结论 |
|---|------|------|------|------|----------|----------|
| 1 | 水平值 Pearson 相关 | 行业月收益与宏观指标**水平**直接求相关 | 实现最简单 | 宏观水平序列多含趋势/单位根，易产生伪相关；无法给出“每 1% 变化对应多少收益” | 仅作探索 | ❌ 不作主口径 |
| 2 | 变化值 Pearson 相关 | 行业月收益与宏观指标**月度变化**求相关 | 去除趋势；量纲无关，便于跨指标比较 | 只有方向与强度，没有幅度；对非线性/异方差无校正 | 辅助指标 | ✅ 作为辅助输出 |
| 3 | 单因子 OLS beta | `r_i,t = α + β·Δm_t + ε`，β 即 APT 意义下的因子敏感度 | 系数可解释（Δm 每变动 1 单位，行业月收益变动 β）；可给 t 统计量与 R² | 单因子、线性、同期；未控制其他宏观变量 | 轻量行业比较 | ✅ **主口径** |
| 4 | 滚动 beta | 固定窗口（如 36 个月）滚动回归，观察 β 时变 | 能刻画结构变化/政策周期 | 窗口长度需论证；样本末端不稳定；实现与测试成本高 | 时序风险监控 | ⏸ 后续版本 |
| 5 | 多因子 / VAR / VECM | 多个宏观变量联合建模，区分短期与长期 | 控制混淆变量；可做因果/领先滞后 | 需要更多变量与平稳性检验；当前接口一次只传一个指标，改造成本高 | 宏观策略研究 | ⏸ 不纳入本接口 |

---

## 3. 学术与社区现状

1. **APT 因子 beta 是“敏感度”的标准表述**：Sangmi 与 Mubasher 在印度市场使用 2008-04 ~ 2012-06 的**月度**数据，以通胀、汇率、工业生产、货币供应、金价、利率为自变量，对 Sensex/Nifty/BSE-100 做多元回归，并明确“APT 中每个因子的敏感度由因子专属 beta 系数表示”[1]。
2. **宏观变量选择与解释力**：Bilson、Brailsford 与 Hooper 研究新兴市场股票收益的宏观解释变量选择，属于同一“收益 ~ 宏观因子”范式[2]。
3. **行业层面同样成立**：Chau 以 1996-2011 年美国黄金行业股票收益对市场收益、汇率、利率等宏观因素建模，说明“行业收益对宏观变量的暴露”是可估计的行业级对象，而非仅限市场指数[3]。
4. **方法综述**：Tangjitprom 汇总宏观因素与股票收益的研究，指出不同研究因目的不同采用不同方法，但多数证据支持宏观变量与股票收益存在显著关系[4]。
5. **水平值回归存在伪相关风险**：Ventosa-Santaulària 综述伪回归现象，指出含趋势/单位根的水平序列做最小二乘会产生虚假显著关系，应改用差分/去趋势或协整框架[5]。这支持“先对宏观水平做变化，再回归”的口径。
6. **短期解释力有限，需谨慎表述**：Wang 使用 VAR + 协整 + VECM 发现短期内宏观变量对股票收益的解释力较弱，长期实际利率与工业生产更重要[6]。因此本接口定位为**同期暴露度量**，不宣称预测能力。

> 说明：上述文献均通过 OpenAlex/Crossref 元数据检索获得，摘要与 DOI 见 §7；未逐篇下载全文核对回归细节，涉及“月度频率”“APT beta”等表述均来自摘要明确文字，属**事实**；将其用于本项目的接口设计属**推断**。

---

## 4. 关键结论与推荐方向

1. **主口径：单因子 OLS beta**
   - 因变量：行业**月度**收益（由日收益按月复利）；
   - 自变量：宏观指标**月度变化**（默认 `pct_change`，可切换 `diff` / `level`）；
   - 输出 `beta`、`correlation`、`t_stat`、`r_squared`、`n_obs`。
2. **频率对齐到月**
   - 宏观指标以月频为主，日频行业收益直接与宏观月度值合并会造成“一个月一个宏观值对应 20 个收益观测”的自相关与伪显著；按月聚合是文献中的常见做法[1][3]。
   - 宏观数据按自然月取最后一个观测；季度指标则自然退化为“相邻观测变化”（环比），在文档中说明。
3. **使用变化而非水平**
   - 默认对宏观水平做 `pct_change`，避免趋势导致的伪回归[5]；
   - `PMI` 一类本身围绕 50 波动的扩散指数，可传 `transform='diff'` 或 `'level'`，由调用方决定。
4. **不引入滚动窗口与多因子**
   - 当前接口一次只接收一个 `macro_indicator`，多因子/VAR 需要重构签名与数据路径；滚动 beta 需要窗口参数与更多测试，均超出“补齐 TODO”的范围。
5. **定位为同期关联，不做可交易信号**
   - `macro_data.trade_date` 是统计期而非发布日，且 `macro_data` / `alternative_data` 均无 `loaded_at` PIT 语义；结果只能表述为“同期暴露”，不能作为发布时点可得的预测信号。
6. **最小样本 12 个月**
   - 月度回归在 12 个观测以下自由度不足、结论不稳；默认 `min_obs=12`，不足的行业跳过并记日志。

---

## 5. 本地数据与接口事实核查

| 项 | 结果 | 来源 |
|----|------|------|
| `macro_data` 主键 | `(trade_date, indicator_id, value_type)` | `core/schema.py#L161-L171` |
| `alternative_data` 唯一键 | `(trade_date, data_type, data_subtype, entity_type, entity_id)` | `core/schema.py#L226-L242` |
| 行业收益字段 | `data_type='industry_return'`、`entity_type='industry'`、`entity_id` 6 位代码、`value` 为日收益 | P4 实施文档 §6 |
| 生产库现状 | `macro_data` 0 行、`alternative_data` 0 行（2026-11-02 只读实测） | 本次核查 |
| 临时库 P4 实跑 | 30 个一级行业、140,989 行、2000-01-04 ~ 2026-11-02 | P4 实施文档 §6.4 |
| 查询接口 | `MacroQuery.get_macro_data`、`AlternativeQuery.get_data(data_type, data_subtype, entity_id, start_date, end_date)` | 源码核对 |
| 依赖 | 无 `scipy`；`numpy`/`pandas` 已在 `pyproject.toml` | `pyproject.toml#L12-L24` |
| 统计量可行性 | beta、R²、t 统计量可用 numpy 手算；**p 值需要 t 分布 CDF**，当前依赖不含 `scipy` | 本次核查 |

> 因无 `scipy`，本接口不输出 `p_value`，以 `t_stat` 供调用方判断（|t|≈2 对应 5% 显著性，大样本近似）。

---

## 6. 风险与限制

| 风险 | 影响 | 缓解 |
|------|------|------|
| 宏观 `trade_date` 为统计期而非发布日 | 存在同期/前视歧义 | 文档明确“同期关联”；后续如需 PIT 需给宏观表加 `loaded_at` |
| 宏观月度值与行业月收益的月末边界 | 首尾月份可能不完整 | 日期过滤按自然日；文档说明边界月 |
| 行业指数为价格指数（不含分红） | 长期收益偏低 | 结论表述为“价格收益敏感度” |
| 行业分类版本漂移（2014/2021 申万） | 长周期 beta 可比性下降 | 输出 `start_date/end_date/n_obs`；建议分段回归 |
| 单因子遗漏变量 | beta 可能吸收其他宏观因素 | 定位为横向比较的粗筛；后续扩展多因子 |
| 生产库当前无数据 | 无法直接生产实跑 | TDD 用临时库；E2E 需先 `collect` 宏观与行业数据 |
| `pct_change` 对 PMI 等指数不直观 | beta 量纲不易解释 | 提供 `transform='diff'` / `'level'`，文档给示例 |

---

## 7. 参考文献

1. Sangmi, M., & Mubasher, H. M. (2013). *Macroeconomic Variables on Stock Market Interactions: The Indian Experience*. Journal of Management Sciences? — RePEc: <https://EconPapers.repec.org/RePEc:mgn:journl:v:6:y:2013:i:8:a:6>（摘要明确：月度数据、多元回归、APT 因子 beta）
2. Bilson, C. M., Brailsford, T., & Hooper, V. J. (2001). *Selecting macroeconomic variables as explanatory factors of emerging stock market returns*. DOI: <https://doi.org/10.1016/S0927-538X(01)00020-8>
3. Chau, M. (2012). *Macroeconomic Determinants of Gold Industry Stock Returns*. <https://digitalcommons.iwu.edu/busadmin_honproj/26>
4. Tangjitprom, N. (2012). *The Review of Macroeconomic Factors and Stock Returns*. DOI: <https://doi.org/10.5539/ibr.v5n8p107>
5. Ventosa-Santaulària, D. (2009). *Spurious Regression*. DOI: <https://doi.org/10.1155/2009/802975>
6. Wang, F. (2020). *Macroeconomic Variables and Stock Returns Revisited*. DOI: <https://doi.org/10.47191/jefms/v3-i10-03>
7. 项目内：[行业收益率数据源调研](industry_return_data_source_research.md)、[P4 实施规划](../implementation/industry_return_ingestion_implementation.md)、[数据库 schema](../../core/schema.py)
