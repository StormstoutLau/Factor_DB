# CLI 收口实施记录：`ic` 与 `validate`

> 日期：2026-10-30
> 上游：[调研](../research/cli_surface_research.md) / [设计](../design/cli_surface_design.md)

---

## 一、TDD 过程

1. **红灯**：新增 3 个用例后运行
   `pytest tests/test_cli.py tests/test_ic_decay.py` → `3 failed, 9 passed`
   ```
   factor-db: error: argument command: invalid choice: 'ic'
   SystemExit: 2
   ```
2. **实现**：`cli.py` 新增 `cmd_ic` / `cmd_validate`、两个子命令解析器，`main()` 返回退出码
3. **绿灯**：同命令 → `12 passed`；`ruff check` All checks passed

## 二、代码改动

| 位置 | 改动 |
|------|------|
| `cli.py#L152-L176` | `cmd_ic`：调用 `FactorQuery.get_ic_series`，输出期数、`ic_mean/ic_std(ddof=1)/ic_ir/max|ic|` 与前 `--limit` 行；空结果打印 `无 IC 数据` |
| `cli.py#L179-L196` | `cmd_validate`：按需拼接 argv 并委托 `scripts.validate_production.main`，返回值即退出码 |
| `cli.py` 参数注册 | `ic`（`--factor` 必填、`--days` 默认 1、`--limit` 默认 10）；`validate`（`--db/--start/--end/--memory-limit/--report-dir/--no-report`） |
| `cli.py` 分发 | `handlers` 增加两项；`code = handlers[...](args)` → `return code if isinstance(code, int) else 0` |
| `cli.py` 入口 | `sys.exit(main())`，与 `console_scripts` 包装器一致 |

## 三、测试

| 用例 | 断言 |
|------|------|
| `test_cli_ic_prints_summary_and_honours_limit` | 含 `IC 序列` / `ic_mean=`；`--limit 2` 时不含末日 `2024-01-06` |
| `test_cli_ic_reports_empty_result` | 未知因子输出 `无 IC 数据`，不抛异常 |
| `test_validate_missing_db_returns_2` | 返回码 2，stderr 含 `环境错误` |

## 四、生产库实跑（只读）

```
ic --factor LT_debt_ratio --days 1 --start 2024-01-01 --end 2024-03-31 --limit 5
IC 序列: LT_debt_ratio, forward_return_days=1, 57 期
ic_mean=0.017640  ic_std=0.112507  ic_ir=0.156787  max|ic|=0.223971
EXIT 0
```

```
validate --no-report
[PASS] S1 结构与行数 0.09s — 18 个对象
[PASS] S2 宽表矩阵 0.304s — 8 因子 × 373029 行，非空率 67.1%
[PASS] S3 history + PIT 1.118s — PIT 323445 行一致
[PASS] S4 价格查询 0.52s
[PASS] S5 IC 序列 0.145s — 57 期，IC 均值 0.017640
E2E_VALIDATION=OK
EXIT 0
```

`ic_mean` 与上一轮验收报告一致，确认 CLI 与脚本走同一条 IC 计算路径。

## 五、回归

- 定向：`tests/test_cli.py` + `tests/test_ic_decay.py` → **12 passed**
- 全量：**322 passed**（新增 3）；`ruff check .` All checks passed
