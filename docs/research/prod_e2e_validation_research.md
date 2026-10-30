# 生产库端到端验收调研

> 日期：2026-10-30
> 任务：为 46 GiB 生产库补齐可重复的端到端验收脚本与报告（任务 C）
> 说明：本任务不引入外部数据源，结论以本机生产库实测探针 + 项目代码事实为主。

---

## 一、问题定义与范围

| 编号 | 问题 | 现状证据 |
|------|------|---------|
| C-1 | 306 个测试全部使用临时库，生产库从未被测试路径覆盖 | `tests/` 全部 `tempfile.mkdtemp()` 建库 |
| C-2 | 无 `conftest.py`，无「生产库冒烟」开关 | 实测仓库根目录无 `conftest.py` |
| C-3 | 验收依赖人肉探针，结论不可复现、无留档 | 本轮修复全部靠 `%TEMP%` 下临时脚本 |
| C-4 | 生产库专有路径（EAV 大表、241 列宽表、PIT）在真实规模下才暴露问题 | `get_ic_series` 两处 OOM（见 §二） |

范围外：数据正确性逐值对账（已在 2026-10-26 十节完成）、写入/迁移路径、跨机部署。

---

## 二、本轮实测发现（驱动本任务成立的直接原因）

### 2.1 IC 查询路径在真实规模下不可用

`FactorQuery.get_ic_series` 原先有两处只在生产规模暴露的缺陷，临时库（4 股 × 5 日）全部通过：

| 缺陷 | 根因 | 生产实测 |
|------|------|---------|
| D-1 数据源错误 | 宽表因子也走 `factor_data`（241 列 `UNPIVOT` 动态视图） | 视图展开 + 窗口函数物化，直接 OOM |
| D-2 前向收益实现低效 | `daily_prices` 自连接 + `MIN(trade_date)` 相关子查询 | 隔离实测 36.69 s 后 `Allocation failure`（`memory_limit` 25 GiB） |

修复（已落地，见 §六）：

- 数据源固定为 `factor_history`（EAV，按 `factor_name` 物理排序，单因子只扫对应区间）
- 前向收益改为 `LEAD(close, n) OVER (PARTITION BY stock_code ORDER BY trade_date)`，只扫 `[start, end]`
- `forward_return_days` 参数原先被 SQL 中的常量 1 忽略，改为 `days = max(1, int(forward_return_days))` 后插值

修复后生产实测（2024Q1，只读连接）：

| 场景 | 结果 | 耗时 |
|------|------|------|
| 宽表因子 IC（36 期） | 均值 0.0275 | 0.29 s |
| CSMAR 因子 IC（1 期） | 均值 -0.0074 | 0.39 s |
| 宽表矩阵 8 因子 × 2024Q1 | 234,323 行，NaN 7.2% | 0.35 s |
| `factor_history` 单因子 `STK_MKT_LOSEORWIN_IfWin_return_MT_P9701_Q_10_C_0_F_1` | 337 行 | 1.61 s |
| `get_daily` 8 股 × 37 日 | 37×8 | 0.25 s |
| `get_price_matrix` 2 股 × 37 日 | 37×2 | 0.21 s |

结论：**必须在真实库上跑**，临时库无法证伪此类缺陷。

### 2.2 生产库事实基线（2026-10-30 实测）

| 对象 | 类型 | 规模 | 区间 |
|------|------|------|------|
| `factor_wide` | BASE TABLE | 9,307,121 行 / 244 列（241 因子 + 3 键） | 2009-01-23 ~ 2026-02-28 |
| `factor_history` | BASE TABLE | 5,203,062,078 行 / 5,680 个因子 | 1990-01-01 ~ 2026-03-31 |
| `factor_data` | VIEW | `UNPIVOT factor_wide` | 同 wide |
| `daily_prices` | BASE TABLE | 43,226,352 行 | 2020-01-02 ~ 2026-01-30 |
| `factor_info` | BASE TABLE | 5,439 行 | — |
| 库文件 | — | 46.19 GiB（NVMe，`D:\Factor_DB\factor_db_compacted.duckdb`） | — |

注意：`daily_prices` 起点 2020-01-02，而 `factor_wide` 起点 2009-01-23 → **任何跨越 2020 年之前的 IC 计算都不可能得到结果**，验收脚本的 IC 区间必须落在 `daily_prices` 覆盖范围内。

---

## 三、验收口径

### 3.1 指标与阈值

| 指标 | 口径 | 阈值 | 依据 |
|------|------|------|------|
| 结构完整性 | 关键对象存在且类型正确（`factor_data`=VIEW，`factor_wide`/`factor_history`=BASE TABLE） | 全部满足 | 2026-10-23 迁移约定 |
| 行数 | 各表 `COUNT(*)` 与基线偏差 | 允许 ±0（只读，不应变化） | 2026-10-26 核对基线 |
| 宽表矩阵非空率 | 抽样因子 × 抽样区间，非空占比 | ≥ 50% | 生产库实测 2024Q1 前 8 个因子为 67.1%（各因子覆盖差异大，故阈值取 50% 只用于发现「整体塌陷」） |
| PIT 一致性 | `as_of=2099-12-31` 与不带 `as_of` 的宽表结果行数一致 | 完全一致 | `_build_pit_subquery` 语义 |
| IC 可计算性 | 宽表因子 IC 期数 / 值域 | 期数 > 0 且 `|ic| ≤ 1` | 相关系数定义域 |
| IC 数值稳定性 | 同一因子两次调用结果差异 | 0 | 确定性 SQL |
| 单查询耗时 | 上述每项查询墙钟时间 | ≤ 30 s/项 | 实测最慢 1.61 s，留 20 倍余量 |
| 总耗时 | 全脚本 | ≤ 10 min | 冷缓存首次访问 21–25 s 的历史事实 |

### 3.2 判定原则

- **结构/行数/一致性**：硬性失败（退出码非 0）。
- **耗时/非空率**：软性告警（写入报告，不影响退出码），避免机器负载波动造成假失败。
- **IC 期数**：按因子频率区分——CSMAR 季度/低频因子在 2024Q1 可能只有 1 个可用日期，不应用「期数 ≥ 20」这类硬阈值。

---

## 四、候选方案对比

### 4.1 验收载体

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 加进 `pytest`，用环境变量开启 | 复用测试基础设施 | 生产库路径必须硬编码/环境变量；`pytest` 输出不适合作为可留档报告；默认 CI 会误跑 46 GiB 库 |
| B. 独立脚本 `scripts/validate_production.py`（CLI） | 显式运行、参数化、产出 JSON+Markdown 报告、退出码可接 CI | 需要单独维护（少量） |
| C. Notebook | 交互友好 | 不可复现、无退出码 |

选 **B**：验收是「运维动作」而非「单元测试」，且报告需要留档（`docs/reports/`）。同时用一个小 `pytest` 文件（临时库 fixture）覆盖脚本的编排逻辑（S1–S6 的判定函数），保证脚本本身不腐化。

### 4.2 只读保证

| 方案 | 说明 |
|------|------|
| **A. `duckdb.connect(read_only=True)`** | 生产库只读打开，任何写语句直接报错；与本项目 `BaseQuery` 一致 |
| B. 依赖调用方自觉 | 不可靠 |

选 **A**。DuckDB 官方文档明确 `read_only=True` 禁止写操作。[[1]](https://duckdb.org/docs/stable/connect/overview)

### 4.3 报告格式

| 方案 | 说明 |
|------|------|
| **A. JSON（机器可读）+ Markdown（人类可读）** | 两者同源，JSON 用于 diff 历史，Markdown 直接进 `docs/reports/` |
| B. 仅 stdout | 无法留档 |

选 **A**。

---

## 五、风险与约束

| 风险 | 影响 | 缓解 |
|------|------|------|
| 冷缓存首次访问慢（21–25 s） | 总耗时不确定 | 阈值放宽到 30 s/项；报告记录冷/热不区分 |
| Windows 文件锁 | 若其他进程持有写连接，脚本无法只读打开 | 明确失败信息 `database is locked`，提示先停写任务 |
| 内存 | IC 曾在 25 GiB 上限 OOM | 脚本设置 `memory_limit`（默认 8 GB）并可 `--memory-limit` 覆盖 |
| 符号链接 | `factor_db.duckdb` → `D:\...` | 默认路径为仓库根 `factor_db.duckdb`，解析后打印真实路径 |
| 抽样因子不存在 | 硬编码因子名可能被清理 | 因子名来自库内探测（`factor_wide` 列 / `factor_history` 首个可用因子），不写死 |
| 低频 CSMAR 因子期数少 | 误判失败 | IC 期数只作软性信息，硬性断言仅「期数 > 0」 |

---

## 六、结论

1. 生产库验收应以**独立只读脚本**形式交付，产出 JSON + Markdown 报告，硬性/软性判定分离。
2. 验收必须覆盖「真实规模才暴露」的路径：宽表矩阵、`factor_history` EAV 单因子、PIT、`get_ic_series`、价格查询。
3. 本轮已修复的两处 IC 缺陷（`get_ic_series` 数据源 + 前向收益实现）应作为**架构约束**固化：IC 只读 `factor_history`，前向收益用 `LEAD` 而非自连接。
4. 验收区间必须落在 `daily_prices` 覆盖范围（≥ 2020-01-02）。

## 七、参考

1. DuckDB 连接与只读模式：https://duckdb.org/docs/stable/connect/overview
2. DuckDB 内存管理 `memory_limit`：https://duckdb.org/docs/stable/configuration/overview
3. 项目内依据：[factor_query.py](../../query/factor_query.py#L380-L463)、[ARCHITECTURE.md §3](../ARCHITECTURE.md)、[REVIEW_CHECKLIST §七~十](REVIEW_CHECKLIST.md)
