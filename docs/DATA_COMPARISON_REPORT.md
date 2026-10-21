# 数据对比分析报告

## zhuyi vs Ashare_data 数据对比

---

### 一、总体概览

| 维度 | zhuyi (`F:\Code_Hub\data_archive\zhuyi`) | Ashare_data (`E:\Ashare_data`) |
|------|------|------|
| **文件数** | 158 | 118 |
| **总大小** | ~12.1 GB | ~8.5 GB |
| **数据来源** | Tushare API（推断） | 未知 |
| **文件格式** | .pkl + .csv + .rar + .ipynb | .pkl + .csv |
| **文档** | 无 README | 无 README |

---

### 二、行情数据对比

| 维度 | zhuyi | Ashare_data | 结论 |
|------|------|------|------|
| **文件数** | 17 .pkl + 3 .rar | 16 .pkl + 1 .csv | 基本一致 |
| **覆盖股票** | 5,681 只 | 5,688 只 | Ashare 多 7 只 |
| **时间范围** | 2020-01-02 ~ **2026-01-15** | 2020-01-02 ~ **2026-01-30** | **Ashare 更新 15 天** |
| **交易日数** | 1,464 | 1,475 | Ashare 多 11 个交易日 |
| **NaN 比例** | 14.10% | 14.13% | 几乎一致 |
| **股票代码格式** | 6 位纯数字 `000001` | 6 位纯数字 `000001` | 一致 |
| **文件结构** | 每个字段一个 .pkl（矩阵格式） | 每个字段一个 .pkl（矩阵格式） | 一致 |

**行情数据结论**：两套数据内容和结构高度一致，但 **Ashare_data 更新 15 天**（截至 2026-01-30 vs 2026-01-15）。建议以 Ashare_data 为主。

---

### 三、因子数据对比

| 维度 | zhuyi (`fundamental_factors/`) | Ashare_data (`财务指标因子/`) |
|------|------|------|
| **文件数** | 120 个 .csv | 101 个 .pkl |
| **大小** | ~9.4 GB | ~7.2 GB |
| **数据类型** | **原始财务报表科目** | **衍生财务比率/因子** |
| **共同因子** | 仅 1 个：`ebit` | 仅 1 个：`ebit` |
| **时间范围** | 2020-01-02 ~ **2026-01-15** | 2020-01-02 ~ **2026-01-27** |
| **覆盖股票** | 6,125 ~ 6,231 只 | 6,507 只 |
| **NaN 比例** | **15% ~ 53%**（较高） | **9% ~ 11%**（较低） |
| **Index 格式** | 日期字符串 | 整数序列 + `trade_date` 列 |

#### 3.1 zhuyi 因子清单（120 个 — 原始财务报表科目）

zhuyi 的 120 个 CSV 文件是 **Tushare 三大财务报表的原始字段**，来自利润表、资产负债表、现金流量表：

| 报表 | 代表因子 | 数量 |
|------|---------|------|
| **利润表** | `revenue`, `total_revenue`, `oper_cost`, `sell_exp`, `admin_exp`, `fin_exp`, `invest_income`, `operate_profit`, `total_profit`, `net_profit`, `basic_eps`, `diluted_eps`, `rd_exp`, `income_tax` 等 | ~40+ |
| **资产负债表** | `money_cap`, `accounts_receiv`, `inventories`, `total_cur_assets`, `fix_assets`, `total_assets`, `st_borr`, `acct_payable`, `total_liab`, `total_hldr_eqy_exc_min_int`, `undistr_porfit`, `cap_rese` 等 | ~50+ |
| **现金流量表** | `c_fr_sale_sg`, `c_paid_goods_s`, `n_cashflow_act`, `n_cashflow_inv_act`, `n_cash_flows_fnc_act`, `free_cashflow`, `c_pay_dist_dpcp_int_exp` 等 | ~30+ |

#### 3.2 Ashare_data 因子清单（101 个 — 衍生财务比率）

Ashare_data 的 101 个 .pkl 文件是 **已计算好的财务比率/因子**，可直接用于量化分析：

| 类别 | 代表因子 | 数量 |
|------|---------|------|
| **盈利能力** | `roe`, `roa`, `roic`, `gross_margin`, `netprofit_margin`, `roe_dt`, `roe_waa` 等 | ~15 |
| **成长性** | `or_yoy`, `op_yoy`, `netprofit_yoy`, `assets_yoy`, `equity_yoy`, `tr_yoy`, `ebt_yoy` 等 | ~15 |
| **估值** | `bps`, `cfps`, `ocfps`, `total_mv`, `ebit_ps`, `fcff_ps`, `fcfe_ps` 等 | ~12 |
| **偿债/杠杆** | `debt_to_assets`, `debt_to_eqt`, `current_ratio`, `quick_ratio`, `cash_ratio`, `eqt_to_debt` 等 | ~15 |
| **现金流** | `fcff`, `fcfe`, `ocf_to_debt`, `ocf_yoy`, `ocf_to_shortdebt` 等 | ~10 |
| **营运能力** | `assets_turn`, `ar_turn`, `fa_turn`, `ca_turn`, `turn_days`, `working_capital` 等 | ~10 |
| **季度指标** | `q_roe`, `q_op_qoq`, `q_sales_yoy`, `q_dt_roe`, `q_npta`, `q_gc_to_gr` 等 | ~10 |
| **每股指标** | `diluted2_eps`, `basic_eps_yoy`, `retainedps`, `surplus_rese_ps`, `undist_profit_ps` 等 | ~8 |

#### 3.3 因子数据结论

**两套数据是互补关系，而非竞争关系**：
- zhuyi 提供**原始财务报表科目**（可用于自定义因子构建）
- Ashare_data 提供**已计算的衍生财务比率**（可直接用于因子回测）
- 仅 1 个因子重叠（`ebit`），其余 219 个因子互不重合

---

### 四、质量评估

| 质量维度 | zhuyi | Ashare_data | 胜出 |
|---------|------|------|------|
| **数据完整性** | NaN 15%~53% | NaN 9%~11% | **Ashare** |
| **跨股票覆盖** | 5,681~6,231 只 | 5,688~6,507 只 | **Ashare** |
| **数据时效性** | 截至 2026-01-15 | 截至 2026-01-27/30 | **Ashare** |
| **因子丰富度** | 120 个原始科目 | 101 个衍生比率 | 互补 |
| **数据格式一致性** | .csv 日期索引 | .pkl 整数索引+trade_date列 | **zhuyi**（更标准） |
| **可复现性** | 含 Notebook 脚本 | 无脚本 | **zhuyi** |

**综合评分**：Ashare_data 在数据质量（完整性、覆盖度、时效性）上全面优于 zhuyi，但 zhuyi 的数据类型（原始科目）与 Ashare（衍生比率）互补，两套数据合并使用能最大化因子库覆盖。

---

### 五、合并更新建议

#### 5.1 推荐方案：以 Ashare 为主，zhuyi 为补充

```
Factor_DB/
├── daily_prices  ← Ashare_data/market_data（更新更全）
├── factor_wide/
│   ├── 101 个衍生因子 ← Ashare_data/财务指标因子/（质量更高）
│   └── 120 个原始科目 ← zhuyi/fundamental_factors/（补充原始数据）
└── factor_history ← 同上
```

#### 5.2 具体操作步骤

| 步骤 | 操作 | 说明 |
|------|------|------|
| **Step 1** | 用 Ashare_data 行情数据覆盖 daily_prices | 比 zhuyi 多 15 天，更完整 |
| **Step 2** | 导入 Ashare_data 的 101 个衍生因子到 factor_wide | 质量最高，可直接用于回测 |
| **Step 3** | 导入 zhuyi 的 120 个原始科目到 factor_wide | 补充原始财务报表数据 |
| **Step 4** | 纳入 zhuyi 的价量/财务构建因子 | `UTR_turnover`, `debt_ratio`, `current_ratio`, `LT_debt_ratio` |
| **Step 5** | 保留 zhuyi 的 Tushare 数据字典 | 用于因子文档说明 |

#### 5.3 注意事项

1. **股票代码格式**：zhuyi 和 Ashare 都使用 6 位纯数字 `000001`，但与 Factor_DB 当前编码习惯（`000001.SZ`）不同，导入时需做格式转换或统一
2. **zhuyi net_profit NaN 问题**：`net_profit.csv` 有 53% 的 NaN 率（88 只股票完全无数据），导入前需确认是否是数据源问题
3. **zhuyi 索引格式**：CSV 使用日期字符串索引，导入时需转换为 DATE 类型
4. **Ashare 索引格式**：.pkl 使用整数序列索引，第一列 `trade_date` 为日期列，需特殊处理
5. **重复处理**：`ebit` 是唯一共同因子，建议以 Ashare 版本为准（质量更高）
6. **zhuyi 的 .rar 文件**：`基本面数据.rar` 和 `基本面因子.rar` 需解压后检查是否包含额外数据

#### 5.4 预期收益

| 指标 | 当前 | 合并后 |
|------|------|--------|
| 因子总量 | 0（未正式迁移） | **221+ 个** |
| 行情数据时效 | 2026-01-30 | 2026-01-30（不变） |
| 原始财务报表科目 | 0 | 120 个 |
| 衍生财务比率 | 0 | 101 个 |
| 数据质量 | N/A | NaN < 11% |

---

### 六、总结

| 对比 | 结论 |
|------|------|
| **哪个质量更高？** | **Ashare_data > zhuyi**（NaN 率 ~10% vs ~15-53%，覆盖 6507 vs 6231 只股票，更新 15 天） |
| **哪个更新？** | **Ashare_data**（2026-01-30 vs 2026-01-15） |
| **应该保留哪个？** | **两者都保留**，因为它们数据类型互补（原始科目 vs 衍生比率） |
| **能否合并？** | **可以且推荐合并**，可形成 221+ 个因子的完整因子库 |