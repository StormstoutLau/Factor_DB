# 分位数组合分层收益实施记录

> 日期：2026-10-31
> 上游：[调研](../research/quantile_portfolio_research.md) / [设计](../design/quantile_portfolio_design.md)

---

## 一、TDD 过程

1. **红灯**：新增 `tests/test_quantile_portfolio.py` → `5 failed`
   ```
   AttributeError: 'FactorQuery' object has no attribute 'get_quantile_portfolio'  ×4
   SystemExit: 2（argparse 未知子命令 quantile）
   ```
2. **实现**：`FactorQuery.get_quantile_portfolio` + CLI `quantile`
3. **绿灯**：定向 `5 passed`；`ruff check` All checks passed

## 二、代码改动

| 位置 | 改动 |
|------|------|
| `query/factor_query.py#L513-L612` | `get_quantile_portfolio`：`NTILE(n_groups) OVER (PARTITION BY trade_date ORDER BY factor_value)` + `LEAD(close, days)`，输出 `trade_date/quantile/avg_return/n_stocks`；`df.attrs['monotonicity']` = 组均收益与组序的 Spearman（秩相关） |
| `cli.py#L202-L230` | `cmd_quantile`：按组汇总 `avg_return/avg_n_stocks/periods`，打印单调性；空结果打印 `无分层数据` |
| `cli.py` 参数注册 | `quantile`：`--factor` 必填、`--groups` 默认 5、`--days` 默认 1、`--start/--end/--db` |
| `cli.py` 分发 | `handlers` 增加 `'quantile'` |

数据路径与 `get_ic_series` 一致：`factor_history`（EAV）+ `daily_prices`，复用 `_build_date_filter` / `_build_pit_subquery`，避免 `factor_data` 视图物化 OOM（ADR-0011）。

## 三、测试

| 用例 | 断言 |
|------|------|
| `test_quantile_portfolio_groups_and_returns` | 列名顺序、组集合 `{1,2}`、5 期、组 2 收益 > 组 1 |
| `test_quantile_monotonicity` | 4 组单调 → `attrs['monotonicity'] == 1.0` |
| `test_quantile_matches_manual_avg` | 组 2 首期 = `(1.03+1.04)/2 - 1`（pandas 独立复算） |
| `test_quantile_rejects_invalid_groups` | `n_groups=1` 抛 `ValueError` |
| `test_cli_quantile_prints_table` | 输出含 `分层收益` / `monotonicity=` / `quantile` |

## 四、生产实跑（只读）

```
quantile --factor LT_debt_ratio --groups 5 --days 1 --start 2024-01-01 --end 2024-03-31
分层收益: LT_debt_ratio, forward_return_days=1, 5 组, 57 期
monotonicity=1.000000
 quantile  avg_return  avg_n_stocks  periods
        1   -0.002095   1020.649123       57
        2   -0.001734   1048.210526       57
        3   -0.001678   1047.438596       57
        4   -0.001598   1045.754386       57
        5   -0.001264   1030.561404       57
EXIT 0
```

- 组均收益严格单调递增（`monotonicity=1.0`），组内股票数约 1020–1048，覆盖均衡。
- 2024Q1 全组收益为负与当期市场下跌一致；因子值越高组收益越高，与 `ic_mean=0.017640` 的 IC 方向一致。

## 五、与 factor_pipeline 的边界（落实调研结论）

- 本仓库**不实现**换手率 `compute_turnover`、多空权重 `compute_long_short_returns`、`compute_spread`——均由 `factor_pipeline.backtest.factor_metrics` 提供。
- 本仓库提供 SQL 口径的「分层收益 + 单调性」；factor_pipeline 提供 numpy 矩阵口径的「权重、多空、换手」。
- 为不形成依赖环（`factor_pipeline` 已将 `factor-db` 列为硬依赖），测试**不 import factor_pipeline**。

## 六、回归

- 定向：`tests/test_quantile_portfolio.py` → **5 passed**
- 全量：**327 passed**；`ruff check .` All checks passed
