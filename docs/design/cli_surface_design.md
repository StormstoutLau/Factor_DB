# CLI 收口设计：`ic` 与 `validate` 子命令

> 日期：2026-10-30
> 上游：[调研](cli_surface_research.md)

---

## 一、命令契约

### 1.1 `factor-db ic`

```
factor-db ic --factor NAME [--days N] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--limit N] [--db PATH]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--factor` | 必填 | 因子名 |
| `--days` | `1` | 前向收益天数（`int`，传给 `get_ic_series`） |
| `--start` / `--end` | `None` | 复用 `_parse_date` |
| `--limit` | `10` | 展示行数；`<= 0` 表示全部 |
| `--db` | `factor_db.duckdb` | 数据库路径 |

输出（stdout）：

```
IC 序列: LT_debt_ratio, forward_return_days=1, 57 期
ic_mean=0.017640  ic_std=0.112507  ic_ir=0.156787  max|ic|=0.224024
 trade_date      ic
...
```

- 空结果：打印 `无 IC 数据: <factor>`，不抛异常，退出码 0。
- `ic_std` 用 `ddof=1`，与 `get_ic_decay` 口径一致；期数 `< 2` 时为 `nan`。
- 连接由 `FactorQuery` 自行关闭（与 `cmd_ic_decay` 相同）。

### 1.2 `factor-db validate`

```
factor-db validate [--db PATH] [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--memory-limit SIZE] [--report-dir DIR] [--no-report]
```

- 全部参数**原样转发**给 `scripts.validate_production.main(argv)`，不重复实现。
- `--db` 未指定时不下传，沿用脚本 `DEFAULT_DB`（单一事实来源）。
- 退出码：`0` 全通过 / `1` 存在 fail / `2` 环境错误，由 `main()` 返回给 `console_scripts`。

## 二、`main()` 返回值改造

```python
handlers = {...}
code = handlers[args.command](args)
return code if isinstance(code, int) else 0
```

- 既有 handler 返回 `None` → 0，行为不变。
- `if __name__ == '__main__': sys.exit(main())`，与 `console_scripts` 语义一致。

## 三、测试设计（TDD）

| 用例 | 位置 | 断言 |
|------|------|------|
| `test_ic_prints_summary_and_limit` | `test_ic_decay.py`（复用既有种子，IC 恒为 1） | 输出含 `ic_mean`、`IC 序列`；`--limit 2` 时数据行数受限 |
| `test_ic_empty_result_message` | `test_ic_decay.py` | 未知因子输出 `无 IC 数据`，不抛异常 |
| `test_validate_missing_db_returns_2` | `test_cli.py` | 返回码 2，stderr 含 `环境错误` |

## 四、非目标

- 不新增 `ic` 的「分组/行业中性化」等参数（属因子评估扩展，另立任务）。
- 不改 `validate` 的检查项与判定阈值。
- 不改动脚本自身的 CLI 入口（保留直接执行能力）。
