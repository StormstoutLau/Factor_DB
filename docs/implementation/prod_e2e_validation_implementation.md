# 生产库端到端验收实施

> 日期：2026-10-30
> 依据：[设计文档](../design/prod_e2e_validation_design.md)

---

## 一、文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `query/factor_query.py` | 修改 | `get_ic_series` 数据源改 `factor_history`；前向收益改 `LEAD`；`forward_return_days` 生效 |
| `tests/test_wide_query.py` | 修改 | 新增 `test_ic_series_reads_history_not_view` |
| `scripts/validate_production.py` | 新增 | 只读端到端验收 CLI（S1–S5 + 报告） |
| `tests/test_validate_production.py` | 新增 | 临时库 fixture 覆盖脚本编排与判定 |
| `docs/research/prod_e2e_validation_research.md` | 新增 | 调研 |
| `docs/design/prod_e2e_validation_design.md` | 新增 | 设计 |
| `docs/adr/0011-ic-query-path-fix.md` | 新增 | IC 路径修复决策 |
| `docs/reports/prod_e2e_validation_2026-10-30.{json,md}` | 新增 | 生产库实跑报告 |
| `docs/spec/REVIEW_CHECKLIST.md` | 修改 | 新增第十二节 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `docs/adr/README.md` | 修改 | 同步 |

---

## 二、分步计划

### 步骤 1：IC 路径修复（先行，已红→绿）

1. 新增红灯测试 `test_ic_series_reads_history_not_view`：断言 `get_ic_series` 的 SQL 不引用 `factor_data`（临时库删掉 `factor_wide` 后仍可算 IC）。
2. 改 `get_ic_series`：
   - `factor_table = self._history_table or 'factor_data'`
   - `px` CTE 用 `LEAD(close, {days}) OVER (PARTITION BY stock_code ORDER BY trade_date)`
   - `days = max(1, int(forward_return_days))`
3. 定向回归：`test_wide_query.py` 12 passed；`test_wide_query + test_pit + test_query` 31 passed。

### 步骤 2：验收脚本

1. `run_validation(db_path, start, end, memory_limit)` 返回报告 dict（结构见设计 §3.2）。
2. `main(argv)` 解析参数、打印摘要、写 JSON+Markdown、返回退出码。
3. 每步 `try/except` → `fail`，保证报告完整。
4. 抽样探测函数：`_pick_wide_factors`、`_pick_history_factor`、`_pick_stock_codes`。

### 步骤 3：脚本测试

临时库夹具（`DailyLoader.load_factor_to_wide` + 手动 INSERT `factor_history` / `daily_prices`），覆盖设计 §六 四个用例。

### 步骤 4：生产库实跑

```
py -3 scripts/validate_production.py --report-dir docs/reports
```

记录每步耗时与结果，产物落到 `docs/reports/`。

### 步骤 5：文档同步与提交

ARCHITECTURE / DEVLOG / ADR-0011 / checklist 第十二节；提交并推送 `origin/master`。

---

## 三、接口变更 Before / After

### 3.1 `FactorQuery.get_ic_series`

签名不变：

```python
def get_ic_series(self, factor_name: str, forward_return_days: int = 1,
                  start_date: Optional[date] = None,
                  end_date: Optional[date] = None) -> pd.DataFrame
```

| 项 | Before | After |
|----|--------|-------|
| 因子数据源 | `factor_data` 视图（241 列 UNPIVOT），生产库 OOM | `factor_history`（EAV，按 `factor_name` 物理排序） |
| 前向收益 | `daily_prices` 自连接 + `MIN(trade_date)` 相关子查询，36.69 s 后 OOM | `LEAD(close, days) OVER (PARTITION BY stock_code ORDER BY trade_date)`，限定 `[start, end]` |
| `forward_return_days` | 参数被忽略（SQL 固定 1 天） | `days = max(1, int(forward_return_days))` 后插值 |
| 价格 PIT | 自连接内嵌子查询 | 复用 `_build_pit_subquery('daily_prices', ..., non_null_columns=['close'])` |

行为差异：`forward_return_days` 由「无效」变为「生效」。现有调用方均传默认 1，输出不变。

### 3.2 新增 `scripts/validate_production.py`

无既有接口，契约见设计 §3.1 / §3.2。

---

## 四、依赖与资源

| 项 | 值 | 来源 |
|----|----|------|
| Python | 3.12.10（`py -3`） | 本机实测（默认 `python` 为 hermes venv，无 pytest） |
| duckdb | 1.5.3 | 本机实测 |
| pandas / numpy | 已由项目依赖声明 | `pyproject.toml` |
| 内存 | 脚本默认 `memory_limit=8GB`，可覆盖 | 生产库 32 GB 机器 |
| 磁盘 | 只读，不产生临时文件 | — |

---

## 五、TDD 与验收

| 用例 | 断言 |
|------|------|
| `test_ic_series_reads_history_not_view` | 仅建 `factor_history`（无宽表）时 IC 可算 |
| `test_run_validation_all_pass` | `summary.fail == 0`，`checks` 含 S1–S5 |
| `test_run_validation_missing_table_fails` | 缺 `daily_prices` → S4 `fail`，报告不抛异常 |
| `test_render_markdown_contains_conclusion` | Markdown 含 `E2E_VALIDATION=` 与 S1–S5 |
| `test_ic_out_of_daily_range_warns` | 区间早于 `daily_prices` → S5 `warn` |

全量回归：`py -3 -m pytest -q`（当前基线 306 passed，预期 ≥ 311 passed）。

---

## 六、风险与回滚

| 风险 | 缓解 |
|------|------|
| 只读连接被写进程阻塞 | 退出码 2 + 明确提示先停写任务 |
| 冷缓存导致 S1/S3 慢 | 阈值 30 s/项，超时记 `warn` |
| 抽样因子为空 | 探测时逐个试，全空则记 `fail` 并写明原因 |
| 脚本影响生产库 | 全程 `read_only=True`，无临时表 |
| 回滚 | 脚本为纯新增文件，删除即可；IC 修复可回退为原 SQL（不推荐，原路径 OOM） |
