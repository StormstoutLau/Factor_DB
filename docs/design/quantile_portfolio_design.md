# 分位数组合分层收益设计

> 日期：2026-10-31
> 上游：[调研](quantile_portfolio_research.md)

---

## 一、接口

```python
FactorQuery.get_quantile_portfolio(
    factor_name: str,
    n_groups: int = 5,
    forward_return_days: int = 1,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> pd.DataFrame
```

返回长表：

| 列 | 类型 | 说明 |
|----|------|------|
| `trade_date` | DATE | 交易日 |
| `quantile` | int | 1..n_groups，1 = 因子值最低组 |
| `avg_return` | float | 组内等权前向收益 |
| `n_stocks` | int | 该组股票数 |

`df.attrs['monotonicity']`：各组平均收益与组序的 Spearman 相关系数（期数不足以计算时为 `nan`）。

`n_groups < 2` 抛 `ValueError`；无有效样本返回空 DataFrame（保留列）。

## 二、SQL 结构

```
factor_rank/quantile : factor_history (EAV) + NTILE(n_groups) OVER (PARTITION BY trade_date ORDER BY factor_value)
px                   : daily_prices + LEAD(close, days)
join + filter        : fwd_ret IS NOT NULL
group by             : trade_date, quantile → AVG(fwd_ret), COUNT(*)
```

与 `get_ic_series` 复用同一 `_build_date_filter` / `_build_pit_subquery`，保证口径一致（PIT 非空过滤 + 日期参数绑定）。

## 三、CLI

```
factor-db quantile --factor NAME [--groups 5] [--days 1] [--start/--end] [--db PATH]
```

输出按组汇总（对期数取均值）：

```
分层收益: LT_debt_ratio, forward_return_days=1, 5 组, 57 期
monotonicity=0.900000
 quantile  avg_return  avg_n_stocks  periods
        1        ...
```

单调性从 `df.attrs` 读取；空结果显示 `无分层数据: <factor>`。

## 四、测试设计（TDD）

| 用例 | 断言 |
|------|------|
| `test_quantile_portfolio_groups_and_returns` | 4 股恒定因子、单调收益 → 2 组时 `quantile` 为 `{1,2}`、组 2 收益 > 组 1，期数 = 5 |
| `test_quantile_monotonicity` | `df.attrs['monotonicity'] == 1.0` |
| `test_quantile_matches_manual_avg` | 用 pandas 独立复算某组某期均值，与返回值一致 |
| `test_quantile_rejects_invalid_groups` | `n_groups=1` 抛 `ValueError` |
| `test_cli_quantile_prints_table` | 输出含 `quantile`、`monotonicity=` |

测试不引入 `factor_pipeline`（避免 CI 额外依赖与依赖成环）。

## 五、非目标

- 不做组内加权（等权即可）、不做行业中性、不做换手率与多空权重（归 factor_pipeline）。
- 不新增 `n_groups` 上限校验以外的参数校验。
