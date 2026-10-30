# CLI 收口调研：补齐 `ic` 与 `validate` 子命令

> 日期：2026-10-30
> 目标：把已存在的 IC 能力与生产验收能力接入统一的 `factor-db` 入口
> 方法：逐一核对 `cli.py`、`query/factor_query.py`、`scripts/validate_production.py` 的实际签名与行为

---

## 一、现状核查

### 1.1 CLI 子命令清单（`cli.py`）

| 子命令 | 状态 | 说明 |
|--------|------|------|
| `init` / `stats` / `load` / `query` / `list-factors` / `cache-clear` / `compact` | 已有 | 2026-10-30 新增 `tests/test_cli.py` 守护 |
| `ic-decay` | 已有 | 2026-10-30 新增，参数 `--factor/--days/--start/--end` |
| `ic` | **缺失** | `FactorQuery.get_ic_series` 已存在且经生产验证（ADR-0011），但无命令行入口 |
| `validate` | **缺失** | `scripts/validate_production.py` 已有完整实现与 `main(argv)`，只能 `py scripts/...` 调用 |

### 1.2 可复用能力

| 能力 | 位置 | 复用结论 |
|------|------|---------|
| IC 时间序列 | `query/factor_query.py#L380` `get_ic_series(factor_name, forward_return_days=1, start_date=None, end_date=None)` → `DataFrame(trade_date, ic)` | 直接调用，无需新计算逻辑 |
| 生产验收 | `scripts/validate_production.py#L149` `run_validation(db, start, end, memory_limit)` → dict；`#L388` `main(argv)` → 退出码 0/1/2 | 直接委托 `main`，避免复制 S1–S5 逻辑 |
| 只读连接 | `query/base.py#L56` `DuckDBConnection(db_path, read_only=True)` | `ic` 天然只读；`validate` 由脚本自身 `read_only=True` |

### 1.3 包内可导入性（关键前提）

仓库布局为「`Factor_DB/` 目录本身即包目录，父目录 `F:\Coding` 在 `sys.path`」。
实测 `from Factor_DB.scripts import validate_production` 可用（命名空间子包），
`validate_production.DEFAULT_DB` 正确解析为 `F:\Coding\Factor_DB\factor_db.duckdb`。
故无需新增 `scripts/__init__.py`，也不改变打包配置。

## 二、缺口与风险

1. **能力孤岛**：`ic`/`validate` 只能通过 Python 片段或直接执行脚本使用，与其余命令的 `--db` 约定不一致。
2. **退出码丢失**：`main()` 当前忽略 handler 返回值，`validate` 的 0/1/2 语义无法传递给 shell / CI。
3. **参数重复**：`validate` 若在 `cli.py` 内重新实现打印与报告逻辑，将出现两份实现，易漂移。
4. **默认库路径**：`ic` 与其它命令一致默认 `factor_db.duckdb`；`validate` 不指定时沿用脚本的 `DEFAULT_DB`，避免把生产路径写死在两处。

## 三、结论

- `ic`：新增薄封装，仅调用 `get_ic_series` 并输出「统计摘要 + 前 N 行」。
- `validate`：新增薄转发，构造 argv 后委托 `validate_production.main`，退出码原样返回。
- `main()` 改为返回整数退出码（`console_scripts` 包装器会 `sys.exit(main())`），既有命令返回 `None` 时统一映射为 0。
- 参数面保持与既有命令风格一致（`--db` / `--start` / `--end`）。
