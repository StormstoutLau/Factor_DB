# 生产库端到端验收报告

> 生成时间：2026-10-30T22:34:16 · 窗口：2024-01-01 ~ 2024-03-31
> 数据库：`D:\Factor_DB\factor_db_compacted.duckdb`（46.19 GB）
> 环境：Python 3.12.10 / DuckDB 1.5.3 / memory_limit=8GB

## 摘要

| 通过 | 告警 | 失败 | 总耗时 |
|------|------|------|--------|
| 5 | 0 | 0 | 2.442 s |

## 检查明细

| 步骤 | 名称 | 状态 | 耗时(s) | 指标 | 说明 |
|------|------|------|---------|------|------|
| S1 | 结构与行数 | pass | 0.107 | `{"factor_wide": 9307121, "factor_history": 5203062078, "daily_prices": 43226352}` | 18 个对象，行数 {'factor_wide': 9307121, 'factor_history': 5203062078, 'daily_prices': 43226352} |
| S2 | 宽表矩阵 | pass | 0.338 | `{"factors": ["LT_debt_ratio", "UTR_turnover", "UTR_turnover_new", "accounts_pay", "accounts_receiv", "accounts_receiv_bill", "acct_payable", "admin_exp"], "rows": 373029, "non_null_ratio": 0.6714}` | 8 因子 × 373029 行，非空率 67.1% |
| S3 | history + PIT | pass | 1.292 | `{"history_factor": "STK_MKT_DGTWBREAKPOINTS_MomMax", "history_rows": 250, "wide_factor": "LT_debt_ratio", "pit_rows": 323445}` | STK_MKT_DGTWBREAKPOINTS_MomMax 250 行；PIT 323445 行一致 |
| S4 | 价格查询 | pass | 0.54 | `{"stocks": ["000001", "000002", "000004", "000006", "000007", "000008", "000009", "000010"], "daily_rows": 464, "matrix_shape": [58, 2]}` | get_daily 464 行；get_price_matrix (58, 2) |
| S5 | IC 序列 | pass | 0.165 | `{"factor": "LT_debt_ratio", "periods": 57, "ic_mean": 0.01764, "ic_abs_max": 0.223971, "window": ["2024-01-01", "2024-03-31"]}` | LT_debt_ratio 57 期，IC 均值 0.017640 |

## 结论

`E2E_VALIDATION=OK`
