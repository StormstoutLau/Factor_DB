# 生产库端到端验收设计

> 日期：2026-10-30
> 依据：[调研文档](../research/prod_e2e_validation_research.md)
> 关联：ADR-0011（IC 查询路径修复）

---

## 一、设计目标与非目标

### 目标
1. 一条命令在**真实生产库**上跑完 S1–S5 五组检查，产出 JSON + Markdown 报告。
2. 只读打开数据库，任何情况下不写生产库。
3. 硬性失败（结构/行数/一致性）与非硬性告警（耗时/非空率/IC 期数）分离，退出码只反映硬性失败。
4. 抽样目标（因子、股票、日期区间）从库内探测，不写死名称。
5. 脚本编排逻辑可被临时库 fixture 测试覆盖。

### 非目标
- 不做逐值对账（2026-10-26 已完成）。
- 不覆盖写入/迁移路径。
- 不接入 CI（46 GiB 库不随 CI 分发）。
- 不做性能基准（只设宽松超时阈值，用于发现退化）。

---

## 二、数据流

```
CLI (scripts/validate_production.py)
  │  --db 默认 <repo>/factor_db.duckdb（符号链接）
  ▼
duckdb.connect(read_only=True) + SET memory_limit
  │
  ├─ S1 结构/行数        → information_schema + COUNT(*)（硬性）
  ├─ S2 宽表矩阵        → FactorQuery.get_factor_matrix（硬性：非空行>0；软性：非空率≥80%）
  ├─ S3 history + PIT   → FactorQuery.get_factor（历史专用因子 / as_of 一致性）（硬性）
  ├─ S4 价格            → PriceQuery.get_daily / get_price_matrix（硬性：行数>0）
  ├─ S5 IC              → FactorQuery.get_ic_series（硬性：期数>0 且 |ic|≤1）
  ▼
report dict → JSON + Markdown（docs/reports/）
  ▼
exit code: 0=全部硬性通过 / 1=有硬性失败 / 2=环境错误（库不存在、被写锁占用）
```

S1–S5 每步独立 `try/except`，单步异常记为 `fail` 并继续，保证报告总能产出。

---

## 三、接口契约

### 3.1 CLI

```
py -3 scripts/validate_production.py \
    [--db PATH]                # 默认 <repo>/factor_db.duckdb
    [--start YYYY-MM-DD]       # 默认 2024-01-01（落在 daily_prices 覆盖内）
    [--end YYYY-MM-DD]         # 默认 2024-03-31
    [--memory-limit STR]       # 默认 8GB
    [--report-dir DIR]         # 默认 <repo>/docs/reports
    [--no-report]              # 只打印摘要，不落盘
```

- 退出码：`0` 硬性全通过；`1` 存在 `fail`；`2` 库不可读（不存在 / `database is locked`）。
- `--start/--end` 必须满足 `start <= end`，且 IC 子区间与 `daily_prices` 覆盖范围求交后为空时，S5 记为 `warn`（跳过）而非 `fail`。

### 3.2 可测试函数（供 `tests/test_validate_production.py`）

| 函数 | 签名 | 语义 |
|------|------|------|
| `run_validation` | `(db_path, start=None, end=None, memory_limit='8GB') -> dict` | 执行 S1–S5，返回报告 dict，不写文件、不 `sys.exit` |
| `render_markdown` | `(report: dict) -> str` | 报告 dict → Markdown 文本 |
| `main` | `(argv=None) -> int` | CLI 入口，返回退出码 |

报告 dict 结构（固定键）：

```python
{
  "meta": {"db_path": str, "real_path": str, "db_size_gb": float,
           "generated_at": "YYYY-MM-DDTHH:MM:SS", "python": str, "duckdb": str,
           "window": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
           "memory_limit": str},
  "checks": [
      {"id": "S1", "name": "结构与行数", "status": "pass|warn|fail",
       "elapsed_s": float, "detail": str, "metrics": {...}}
  ],
  "summary": {"pass": int, "warn": int, "fail": int, "total_elapsed_s": float},
}
```

### 3.3 各步骤判定

| 步骤 | 检查内容 | 硬性条件 | 软性条件 |
|------|---------|---------|---------|
| S1 | `factor_data` 为 VIEW；`factor_wide` / `factor_history` / `daily_prices` 为 BASE TABLE；各表行数 > 0 | 类型与存在性全满足 | 行数与基线（9,307,121 / 5,203,062,078 / 43,226,352）偏差 > 1% → warn |
| S2 | `get_factor_matrix(8 因子, start, end)` | 行数 > 0 | 非空率 ≥ 50%（生产实测 67.1%）；耗时 ≤ 30 s |
| S3 | history-only 因子 `get_factor`；宽表因子 `get_factor(as_of=2099-12-31)` 行数与不带 `as_of` 相同 | 两条查询均 > 0 行；PIT 行数一致 | 耗时 ≤ 30 s |
| S4 | `get_daily(8 股)`、`get_price_matrix(2 股)` | 两者行数 > 0 | 耗时 ≤ 30 s |
| S5 | `get_ic_series(宽表因子, 1, start, end)` | 期数 > 0；`ic` 非 NaN；`|ic| ≤ 1` | 耗时 ≤ 30 s；期数 < 5 → warn（低频因子正常） |

### 3.4 抽样目标探测

| 目标 | 探测方式 | 依据 |
|------|---------|------|
| 宽表因子 | `DESCRIBE factor_wide` 取非键列；在 `[start, end]` 内按列 `COUNT(*) FILTER (WHERE col IS NOT NULL) > 0` 取前 8 个 | 不写死因子名 |
| history 专用因子 | `SELECT factor_name FROM factor_info WHERE factor_name NOT IN (<宽表列>) LIMIT 50`，逐个用单因子 `COUNT(*)`（物理排序，快）试到首个 > 0 | CSMAR 因子仅存 history |
| 股票 | `SELECT DISTINCT stock_code FROM daily_prices WHERE trade_date = <end 附近最近交易日> LIMIT 8` | 保证价格查询有数据 |
| IC 子区间 | `[max(start, daily_min), min(end, daily_max)]` | `daily_prices` 起点 2020-01-02 |

若探测失败（如 `factor_info` 为空），对应步骤记 `fail` 并在 `detail` 写明原因。

---

## 四、只读与安全

1. `duckdb.connect(path, read_only=True)`；不调用 `DuckDBConnection`（其单例 + 写连接语义不适用于验收）。
2. 不创建临时表、不 `CHECKPOINT`、不设 `temp_directory`（只读连接无需外部排序）。
3. 异常信息只记录摘要（避免把完整 SQL 打进报告）。

---

## 五、报告

- 路径：`<report-dir>/prod_e2e_validation_<YYYY-MM-DD>.json` 与同名 `.md`（同日重跑覆盖）。
- Markdown 结构：标题（含库路径与窗口）→ 摘要表（pass/warn/fail/总耗时）→ 逐项结果表（含耗时与关键指标）→ 结论。
- 结论行固定：`E2E_VALIDATION=OK` / `E2E_VALIDATION=DEGRADED`（仅有 warn）/ `E2E_VALIDATION=FAILED`。

---

## 六、测试策略

`tests/test_validate_production.py`（临时库 fixture，不触碰生产库）：

1. `test_run_validation_all_pass`：建 `factor_wide`(pe/pb) + `factor_history`（含历史专用因子）+ `daily_prices`（8 股 × 2 日）→ 断言 `summary.fail == 0`、`checks` 覆盖 S1–S5。
2. `test_run_validation_missing_table_fails`：删掉 `daily_prices` → 断言 S4 为 `fail`，且报告仍完整（不抛异常）。
3. `test_render_markdown_contains_conclusion`：断言 Markdown 含 `E2E_VALIDATION=` 与各步骤 ID。
4. `test_ic_out_of_daily_range_warns`：`start/end` 早于 `daily_prices` 最小值 → S5 为 `warn`（跳过）而非 `fail`。

夹具与 [test_wide_query.py](../../tests/test_wide_query.py) 一致：`DailyLoader.load_factor_to_wide` 建宽表与历史；历史专用因子用 `BaseLoader.write_factor_long` 或直接 INSERT 写入 `factor_history`。

---

## 七、文档同步

- `docs/spec/REVIEW_CHECKLIST.md` 新增「十二、生产库端到端验收」。
- `docs/DEVLOG.md` 追加 2026-10-30 条目（IC 修复 + E2E 验收）。
- `docs/ARCHITECTURE.md` §3 增补「IC 计算数据源：`factor_history`，前向收益用 `LEAD`」。
- 新增 `docs/adr/0011-ic-query-path-fix.md` 并更新 `docs/adr/README.md`。
