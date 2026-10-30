# IC 衰减分析设计

> 日期：2026-10-30
> 依据：[调研](../research/ic_decay_research.md)

## 一、API

```python
FactorQuery.get_ic_decay(
    factor_name: str,
    periods: tuple[int, ...] = (1, 2, 3, 5, 10, 20),
    start_date: date | None = None,
    end_date: date | None = None,
) -> pd.DataFrame
```

返回列（固定顺序）：

| 列 | 类型 | 语义 |
|----|------|------|
| `forward_return_days` | int | 前向收益天数 h |
| `periods` | int | 有效 IC 期数 |
| `ic_mean` | float | 各期 IC 均值；无期数时为 NaN |
| `ic_std` | float | 样本标准差 ddof=1；期数 ≤ 1 为 NaN |
| `ic_ir` | float | `ic_mean/ic_std`（日频未年化）；`ic_std` 为 0/NaN 时为 NaN |

## 二、行为规则

1. `periods` 先去重、升序，并**过滤 < 1 的值**；全部被过滤时抛 `ValueError`。
2. 每个 horizon 调用 `get_ic_series`，因此数据源、PIT 语义、NaN 过滤与既有 IC 完全一致（ADR-0011）。
3. 空结果行不丢弃，保留 `periods=0` + NaN 指标，便于对比。
4. 无缓存（`cache_size=0` 默认），重复调用结果确定。

## 三、CLI

```
factor-db ic-decay --db PATH --factor NAME [--days 1,2,3,5,10,20] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
```

- `--days` 逗号分隔，解析失败由 argparse/`int()` 抛错（非零退出）。
- 日期用 `_parse_date`（`datetime.strptime`）校验格式。
- 输出 `DataFrame.to_string(index=False)`，列顺序同 API。
- 使用 `FactorQuery` 只读连接，结束后 `query.conn.close()`。

## 四、测试设计（`tests/test_ic_decay.py`）

夹具：6 个交易日 × 4 只股票；因子值恒定且排名 A<B<C<D；收益率单调且排名一致 → IC 恒为 1。

| 用例 | 断言 |
|------|------|
| `test_ic_decay_returns_horizon_curve` | 列名/顺序正确；horizons=[1,2,3]；periods=[5,4,3]；`ic_mean > 0.999` |
| `test_ic_decay_matches_ic_series` | h=1 的 `ic_mean` 与 `get_ic_series` 均值相等（9 位小数） |
| `test_ic_decay_rejects_empty_periods` | `periods=()` 与 `(0,-1)` 抛 `ValueError` |
| `test_cli_ic_decay_prints_table` | CLI 输出含 `forward_return_days` / `ic_mean` |

## 五、非目标

- 不做年化 ICIR、不做 Newey-West 修正、不做分层组合收益/换手率（后续独立任务）。
