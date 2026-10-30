# ADR-0011：IC 计算路径修复（数据源与前瞻收益实现）

- 状态：已接受
- 日期：2026-10-30
- 修订：无
- 关联：ADR-0002、ADR-0004、ADR-0009

## 背景

`FactorQuery.get_ic_series` 在临时库（4 股 × 5 日）通过测试，但在 46 GiB 生产库上两处 OOM：

1. 宽表因子也经 `factor_data`（241 列 `UNPIVOT` 动态视图）读取，IC 的窗口函数会物化巨量中间结果。
2. 前向收益用 `daily_prices` 自连接 + `MIN(trade_date)` 相关子查询，隔离实测 36.69 s 后 `Allocation failure`（`memory_limit` 25 GiB）。

另外，`forward_return_days` 参数被 SQL 中的常量 1 忽略。

## 决策

1. IC 的因子数据源固定为 `factor_history`（EAV，按 `factor_name` 物理排序，单因子只扫对应区间）；仅当 history 不存在时回退 `factor_data`。
2. 前向收益改用 `LEAD(close, days) OVER (PARTITION BY stock_code ORDER BY trade_date)`，并将价格区间限定在 `[start_date, end_date]`，复用 `_build_pit_subquery('daily_prices', ..., non_null_columns=['close'])`。
3. `forward_return_days` 生效：`days = max(1, int(forward_return_days))` 后插值进 SQL。

## 理由

- EAV 表按 `factor_name` 物理有序，单因子扫描只读其行块，不会展开 241 列。
- `LEAD` 是单次有序窗口扫描，替代「自连接 × 相关子查询」的平方级中间结果。
- 复用既有 PIT 子查询，保持「排除 NULL 空壳行」语义（ADR-0006）。

## 备选方案

| 方案 | 未采纳原因 |
|------|-----------|
| 继续用 `factor_data` 视图 | 生产实测 OOM，视图展开成本不可控 |
| 自连接取前向收益 | 平方级中间结果，36 s 后 OOM |
| 改从宽表 `factor_wide` 取因子 | 宽表只含 241 因子；CSMAR 5,439 个因子多数仅在 history，宽表路径覆盖不全 |
| 预先物化前向收益表 | 增加写路径与存储（43M 行 × 多周期），超出本任务范围 |

## 后果

正面：
- 宽表因子 IC 36 期 0.29 s、CSMAR 因子 IC 0.39 s（均为生产实测，2024Q1）。
- 覆盖全部 5,680 个因子，且 `forward_return_days` 语义正确。

负面 / 约束：
- 低频/季度因子在短区间内可能只有 1 期 IC，不能对「期数」设硬阈值。
- IC 依赖 `daily_prices` 覆盖（生产为 2020-01-02 起），更早的因子区间无法计算 IC。
- 记录该约束到 [ARCHITECTURE.md](../ARCHITECTURE.md)，并由 `scripts/validate_production.py` 的 S5 持续验证。
