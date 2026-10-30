# IC 衰减分析实施

> 日期：2026-10-30
> 依据：[设计](../design/ic_decay_design.md)

## 一、文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `tests/test_ic_decay.py` | 新增 | 4 个用例（红 → 绿） |
| `query/factor_query.py` | 修改 | 新增 `get_ic_decay`（L469-L511） |
| `cli.py` | 修改 | 新增 `_parse_date` / `cmd_ic_decay` / `ic-decay` 子命令 |

## 二、TDD 记录

| 阶段 | 结果 |
|------|------|
| 红（夹具修正后） | `AttributeError: 'FactorQuery' object has no attribute 'get_ic_decay'`；CLI 用例 `SystemExit: 2`（未知子命令） |
| 绿 | `tests/test_ic_decay.py` **4 passed (1.09 s)** |

夹具首版曾因 `INSERT INTO daily_prices SELECT * FROM df`（表 11 列 vs DataFrame 4 列）报 BinderError，改为显式列名后进入正常红灯阶段。

## 三、接口 Before / After

| 项 | Before | After |
|----|--------|-------|
| 因子 API | 仅 `get_ic_series`（单 horizon） | 新增 `get_ic_decay`，返回 horizon 曲线 |
| CLI 子命令 | 7 个（无分析命令） | 新增 `ic-decay` |
| 既有接口 | — | `get_ic_series` 签名与行为不变 |

## 四、生产验证（只读，`cli.py ic-decay`）

```
 forward_return_days  periods  ic_mean   ic_std    ic_ir
                   1       57 0.017640 0.112507 0.156787
                   2       56 0.024985 0.113012 0.221084
                   5       53 0.033576 0.120526 0.278575
                  10       48 0.024705 0.129925 0.190145
                  20       38 0.026394 0.129232 0.204234
```

窗口 2024-01-01 ~ 2024-03-31，因子 `LT_debt_ratio`；5 个 horizon 总耗时约 1.5 s。

## 五、风险

| 风险 | 说明 / 缓解 |
|------|------------|
| 重叠样本导致 ICIR 偏乐观 | 文档已标注「日频未年化、仅相对参考」；不做显著性结论 |
| horizon 大时期数下降 | 属口径预期，`periods` 列显式暴露 |
| 低频因子期数过少 | 由返回值 `periods` 暴露，使用方判断 |
