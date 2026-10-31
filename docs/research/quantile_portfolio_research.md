# 分位数组合分层收益调研：与 factor_pipeline 的边界

> 日期：2026-10-31
> 目标：评估「分位数组合/多空收益、换手率」是否已有实现，明确增量与接入边界

---

## 一、结论

| 能力 | factor_pipeline 现状 | Factor_DB 是否需要实现 |
|------|----------------------|------------------------|
| 换手率 | `backtest/factor_metrics.py` `compute_turnover` = `0.5·Σ|Δw|`，有测试 | **否** |
| 多空收益 | `compute_long_short_returns(factor, returns, top_n)`，有测试 | **否** |
| 多空 Sharpe / Spread | `compute_spread`，有测试 | **否** |
| IC / ICIR / IC Decay / Hit Rate | 同文件已实现（`max_lag` 矩阵口径） | 已实现（SQL 口径，ADR-0011） |
| **N 分位逐组收益 + 单调性** | **未实现**（只有 top20%-bottom20% 单点分解与指纹分位桶） | **是（唯一增量）** |

`factor_pipeline` 的 `three_channel_decomposition.py` 仅计算 `R_factor = mean(r_top20%) - mean(r_bottom20%)`；
`fingerprint_performance_logger.py` 的 `qcut` 分支用于指纹维度分桶，与因子收益曲线无关。

## 二、依赖与边界（接入前置事实）

1. `factor_pipeline/pyproject.toml` 将 `factor-db` 列为**硬依赖**。Factor_DB 若反向 import factor_pipeline 会成环，故 **Factor_DB 不得依赖 factor_pipeline**。
2. `FactorBacktestEngine` 依赖 `Factor_Trading_v3_0.core.data_v3.DataLoaderV3`（`backtest` extra = `factor-trading-v3`），未安装时 `data_bridge` 抛 `AdapterImportError`。Factor_DB 的 CI 不应引入该依赖。
3. `factor_pipeline` 的 `CachedDataLoader(db_path=...)` 已能直读本仓库：`FactorPivotAdapter` 读 `factor_wide`，价格走 `Factor_DB.query.price_query.PriceQuery`。
4. IC Decay 两种口径：pipeline 是 `compute_ic_decay(factor, fwd_ret, max_lag)`（滞后维度）；Factor_DB 的 `get_ic_decay` 是固定窗口逐 horizon。需在文档中区分，避免误用。

## 三、增量范围

在 `FactorQuery` 新增 `get_quantile_portfolio`：

- **数据源**：`factor_history`（EAV 布局，与 `get_ic_series` 同路径，避免 `factor_data` 视图物化 OOM）+ `daily_prices`（`LEAD(close, days)`）。
- **分组**：每个交易日按 `factor_value` 升序 `NTILE(n_groups)`，1 = 最低值组。
- **收益**：组内等权前向收益均值（`AVG(fwd_ret)`）。
- **输出**：逐期长表 `(trade_date, quantile, avg_return, n_stocks)`，并在 `df.attrs` 附 `monotonicity`（组序与组均收益的 Spearman）。
- **不实现**：换手率、多空组合权重、夏普——均归 factor_pipeline。

## 四、可运行性

`NTILE` 为窗口函数，每期在有效股票集合内排序，规模为「单期股票数 × 期数」，与 IC 序列同量级，生产库预计 1–2 s。
因子全空或价格缺失的交易日会被 `fwd_ret IS NOT NULL` 过滤，不做插补。
