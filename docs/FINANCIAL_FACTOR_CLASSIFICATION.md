# 财务指标因子分类方案

## 命名原则

1. **英文命名**：文件夹和因子名统一使用英文，遵循 `snake_case` 规范
2. **语义清晰**：类名即含义，如 `profitability`（盈利能力）、`growth`（成长性）
3. **互斥完整**：每个因子只属于一个分类，无遗漏无重叠
4. **前缀标识**：季度因子以 `q_` 前缀归入 `quarterly`，避免与年度因子混淆

## 分类体系（7 个类别）

### 1. profitability — 盈利能力（17 个）

衡量公司创造利润的核心能力，是最重要的因子类别。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `roe` | 净资产收益率 | ROE |
| `roe_dt` | 扣非净资产收益率 | 扣除非经常性损益 |
| `roe_waa` | 加权净资产收益率 | 加权平均 |
| `roe_yearly` | 年化净资产收益率 | 年化 |
| `roe_yoy` | 净资产收益率同比 | 同比增长率 |
| `roa` | 总资产收益率 | ROA |
| `roa_yearly` | 年化总资产收益率 | 年化 |
| `roa2_yearly` | 年化总资产收益率(2) | 备选口径 |
| `roa_dp` | 总资产收益率(扣非) | 扣非口径 |
| `roic` | 投入资本回报率 | ROIC |
| `gross_margin` | 毛利率 | |
| `grossprofit_margin` | 毛利利润率 | 备选口径 |
| `netprofit_margin` | 净利率 | |
| `ebit` | 息税前利润 | |
| `ebitda` | 息税折旧摊销前利润 | EBITDA |
| `npta` | 非经常性损益 | 扣非净利润 |
| `op_income` | 营业利润 | |

### 2. growth — 成长性（15 个）

衡量公司各项指标的同比增长能力，是动量因子的基础。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `or_yoy` | 营业收入同比增长率 | |
| `op_yoy` | 营业利润同比增长率 | |
| `netprofit_yoy` | 净利润同比增长率 | |
| `assets_yoy` | 总资产同比增长率 | |
| `equity_yoy` | 净资产同比增长率 | |
| `eqt_yoy` | 股东权益同比增长率 | 备选口径 |
| `tr_yoy` | 营业总收入同比增长率 | |
| `ebt_yoy` | 利润总额同比增长率 | |
| `dt_netprofit_yoy` | 扣非净利润同比增长率 | |
| `dt_eps_yoy` | 扣非每股收益同比增长率 | |
| `basic_eps_yoy` | 基本每股收益同比增长率 | |
| `op_of_gr` | 营业利润增速 | 备选口径 |
| `ebit_of_gr` | 息税前利润增速 | |
| `profit_to_gr` | 利润总额增速 | |
| `gc_of_gr` | 营业总成本增速 | |

### 3. valuation — 估值与每股指标（16 个）

衡量公司估值水平和每股价值，是价值因子的核心。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `bps` | 每股净资产 | Book Value Per Share |
| `bps_yoy` | 每股净资产同比增长 | |
| `cfps` | 每股现金流 | Cash Flow Per Share |
| `cfps_yoy` | 每股现金流同比增长 | |
| `ocfps` | 每股经营性现金流 | Operating CF Per Share |
| `fcff_ps` | 每股企业自由现金流 | FCFF Per Share |
| `fcfe_ps` | 每股股权自由现金流 | FCFE Per Share |
| `ebit_ps` | 每股息税前利润 | EBIT Per Share |
| `diluted2_eps` | 稀释每股收益 | |
| `retainedps` | 每股留存收益 | |
| `surplus_rese_ps` | 每股盈余公积 | |
| `undist_profit_ps` | 每股未分配利润 | |
| `capital_rese_ps` | 每股资本公积 | |
| `total_mv` | 总市值 | |
| `retained_earnings` | 留存收益 | |
| `extra_item` | 非经常性损益 | |

### 4. leverage_solvency — 杠杆与偿债能力（18 个）

衡量公司财务杠杆水平和短期/长期偿债能力。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `debt_to_assets` | 资产负债率 | |
| `debt_to_eqt` | 产权比率 | 负债/权益 |
| `dp_assets_to_eqt` | 扣非资产/权益 | 备选口径 |
| `assets_to_eqt` | 权益乘数 | 资产/权益 |
| `eqt_to_debt` | 权益负债比 | 权益/负债 |
| `current_ratio` | 流动比率 | |
| `quick_ratio` | 速动比率 | |
| `cash_ratio` | 现金比率 | |
| `eqt_to_interestdebt` | 权益/带息负债 | |
| `eqt_to_talcapital` | 权益/总资本 | |
| `currentdebt_to_debt` | 流动负债/总负债 | |
| `longdeb_to_debt` | 长期负债/总负债 | |
| `int_to_talcap` | 带息负债/总资本 | |
| `tangibleasset_to_debt` | 有形资产/负债 | |
| `tangasset_to_intdebt` | 有形资产/带息负债 | |
| `interestdebt` | 带息负债 | |
| `netdebt` | 净负债 | |
| `invest_capital` | 投入资本 | |

### 5. cash_flow — 现金流（9 个）

衡量公司现金创造能力和现金流质量。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `fcff` | 企业自由现金流 | Free Cash Flow to Firm |
| `fcfe` | 股权自由现金流 | Free Cash Flow to Equity |
| `ocf_to_debt` | 经营现金流/负债 | |
| `ocf_to_shortdebt` | 经营现金流/短期负债 | |
| `ocf_yoy` | 经营现金流同比增长 | |
| `profit_dedt` | 利润/负债 | |
| `profit_to_op` | 利润/营业利润 | 利润质量 |
| `current_exint` | 当期利息保障倍数 | |
| `noncurrent_exint` | 非当期利息保障倍数 | |

### 6. operating_efficiency — 营运效率（12 个）

衡量公司资产运营效率和管理能力。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `assets_turn` | 总资产周转率 | |
| `ar_turn` | 应收账款周转率 | |
| `fa_turn` | 固定资产周转率 | |
| `ca_turn` | 流动资产周转率 | |
| `turn_days` | 营业周期（天） | |
| `working_capital` | 营运资本 | |
| `networking_capital` | 净营运资本 | |
| `fixed_assets` | 固定资产 | |
| `tangible_asset` | 有形资产 | |
| `ca_to_assets` | 流动资产/总资产 | |
| `nca_to_assets` | 非流动资产/总资产 | |
| `tbassets_to_totalassets` | 有形资产/总资产 | |

### 7. quarterly — 季度指标（9 个）

季度频率的特殊指标，以 `q_` 为前缀，与年度因子区分。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `q_roe` | 季度净资产收益率 | |
| `q_dt_roe` | 季度扣非净资产收益率 | |
| `q_op_qoq` | 营业利润环比增长 | |
| `q_sales_yoy` | 季度营收同比增长 | |
| `q_npta` | 季度非经常性损益 | |
| `q_gc_to_gr` | 季度总成本增速 | |
| `q_saleexp_to_gr` | 季度销售费用增速 | |
| `q_ocf_to_sales` | 季度经营现金流/营收 | |

### 8. cost_structure — 成本费用结构（5 个）

衡量公司成本费用占比和运营效率的结构性因子。

| 因子名 | 中文含义 | 说明 |
|--------|---------|------|
| `cogs_of_sales` | 营业成本/营收 | |
| `expense_of_sales` | 营业费用/营收 | |
| `adminexp_of_gr` | 管理费用增速 | |
| `finaexp_of_gr` | 财务费用增速 | |
| `saleexp_to_gr` | 销售费用增速 | |

---

## 文件夹结构

```
E:\Ashare_data\financial_factors\     ← 英文重命名根目录
├── profitability\                     ← 盈利能力（17）
│   ├── roe.pkl
│   ├── roe_dt.pkl
│   ├── ...
│   └── op_income.pkl
├── growth\                            ← 成长性（15）
│   ├── or_yoy.pkl
│   ├── netprofit_yoy.pkl
│   ├── ...
│   └── gc_of_gr.pkl
├── valuation\                         ← 估值与每股指标（16）
│   ├── bps.pkl
│   ├── total_mv.pkl
│   ├── ...
│   └── extra_item.pkl
├── leverage_solvency\                 ← 杠杆与偿债（18）
│   ├── debt_to_assets.pkl
│   ├── current_ratio.pkl
│   ├── ...
│   └── invest_capital.pkl
├── cash_flow\                         ← 现金流（9）
│   ├── fcff.pkl
│   ├── fcfe.pkl
│   ├── ...
│   └── noncurrent_exint.pkl
├── operating_efficiency\              ← 营运效率（12）
│   ├── assets_turn.pkl
│   ├── ar_turn.pkl
│   ├── ...
│   └── tbassets_to_totalassets.pkl
├── quarterly\                         ← 季度指标（9）
│   ├── q_roe.pkl
│   ├── q_op_qoq.pkl
│   ├── ...
│   └── q_ocf_to_sales.pkl
└── cost_structure\                    ← 成本费用结构（5）
    ├── cogs_of_sales.pkl
    ├── adminexp_of_gr.pkl
    └── ...
```

## 分类统计

| 类别 | 英文 | 数量 | 占比 |
|------|------|------|------|
| 盈利能力 | `profitability` | 17 | 16.8% |
| 成长性 | `growth` | 15 | 14.9% |
| 估值与每股指标 | `valuation` | 16 | 15.8% |
| 杠杆与偿债能力 | `leverage_solvency` | 18 | 17.8% |
| 现金流 | `cash_flow` | 9 | 8.9% |
| 营运效率 | `operating_efficiency` | 12 | 11.9% |
| 季度指标 | `quarterly` | 9 | 8.9% |
| 成本费用结构 | `cost_structure` | 5 | 5.0% |
| **合计** | | **101** | **100%** |

## 命名规范总结

| 规则 | 示例 |
|------|------|
| 文件夹名：`snake_case` | `operating_efficiency`, `cash_flow` |
| 因子名：保持原名 | `roe`, `debt_to_assets` |
| 季度前缀：`q_` | `q_roe`, `q_op_qoq` |
| 增长率后缀：`_yoy` | `or_yoy`, `assets_yoy` |
| 比值类：`_to_` | `debt_to_assets` |
| 每股后缀：`_ps` | `bps`, `cfps`, `ocfps` |