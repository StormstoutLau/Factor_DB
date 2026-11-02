# 宏观采集器日期与取值列修复实施规划（P6）

> 日期：2026-11-02
> 上游：[调研](../research/macro_collector_date_value_research.md) / [设计](../design/macro_collector_date_value_design.md)
> 本文档为编码前规划；重采结果在 §6 回填。

---

## 1. 分步实施计划（每步可独立验证）

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | `_parse_trade_date`：中文月/季度/完整日期 + 标准日期兜底 | 单测：6 种格式与非法值 |
| S2 | 日期列关键词补 `月份`/`季度`；丢弃非法日期并告警 | 单测：混合有效/非法行只保留有效行 |
| S3 | `VALUE_COLUMNS` 映射 + `_pick_value_column` | 单测：6 个映射、`今值` 优先、未知指标兜底 |
| S4 | `transform_data` 接入解析与取值选择 | 单测：CPI/PMI/GDP/M2/外汇/进出口端到端 |
| S5 | 全量回归 + 生产库重采 | 367 passed；12 指标全部入库 |

TDD 顺序：先补 `tests/test_collect.py` 11 个用例 → 确认红 → 实现 S1-S4 → 定向绿 → 全量回归。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 无新增依赖；仅用标准库 `re`、`calendar`；Python `>=3.9` 兼容 |
| **依赖** | 不改 `pyproject.toml` |
| **兼容** | `collect(params)` 公共接口不变；`INDICATOR_MAP`/`FUNCTION_CANDIDATES` 不变；其他采集器不受影响 |
| **接口** | 新增两个静态/类方法 `_parse_trade_date` / `_pick_value_column` 与类属性 `VALUE_COLUMNS`，均为内部辅助 |
| **签名** | `_parse_trade_date(value: Any) -> Optional[date]`；`_pick_value_column(columns: List[str], indicator: str) -> Optional[str]`；无可变默认参数 |
| **低效操作** | 每指标最多 249 行，`Series.map` 逐行解析开销可忽略；无新增 SQL/无逐行 INSERT（沿用 `MacroLoader` 的 `INSERT OR REPLACE ... SELECT`） |
| **内存** | 宏观数据单指标 ≤1,539 行，远低于 32GB |
| **数据一致性** | `macro_data` 主键 `(trade_date, indicator_id, value_type)`；`MacroLoader._insert_macro_data` 使用 `INSERT OR REPLACE`，覆盖同主键旧值；**非重叠日期上的旧行会残留**，重采前需删除对应指标行 |
| **日期语义** | 五列报告保留发布日；中文月/季度映射统计期起点/季末；不做事后回移 |
| **失败语义** | 非法日期行丢弃并计数；映射列缺失回退 `今值`→首个匹配→第二列；无可用列返回空表 |

---

## 3. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `osint/collectors/akshare_collector.py` | 新增 `VALUE_COLUMNS`、`_parse_trade_date`、`_pick_value_column`；改造 `transform_data` |
| `tests/test_collect.py` | 新增 11 个用例（日期解析 + 取值列 + 6 指标端到端） |
| `docs/research/macro_collector_date_value_research.md` | **新增**：调研 |
| `docs/design/macro_collector_date_value_design.md` | **新增**：设计 |
| `docs/implementation/macro_collector_date_value_implementation.md` | 本文件（§6 回填） |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第十九节 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` / `CODE_WIKI.md` | 实施后同步（测试数 356→367，采集器说明） |

---

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `transform_data` 日期列识别 | `['日期','date','时间','time','年份']` | 追加 `月份`、`季度` |
| 日期解析 | `pd.to_datetime(...).dt.date`，中文格式抛异常 | `_parse_trade_date` 正则解析，无法解析返回 None 并丢弃 |
| 取值列 | 循环覆盖 → 最后一个匹配列 | 指标映射 → `今值` → 首个匹配列 → 第二列 |
| `CPI_MONTHLY` 入库值 | `前值`（如 1996-03-01 = 2.1） | `今值`（1996-03-01 = 2.3） |
| 6 个失败指标 | 0 条 | 预期 83/249/226/225/225/225 条 |
| 公共接口 | `collect(params)` | 不变 |

---

## 5. 测试计划（TDD）

在 `tests/test_collect.py` 新增 `TestMacroNormalization` 类，假 AKShare 模块，不触网。

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_parse_chinese_month` | 方法不存在 | `2026年09月份`→2026-09-01；`2026年9月份`→2026-09-01；`2008年01月份`→2008-01-01 |
| 2 | `test_parse_chinese_quarter` | 方法不存在 | `2026年第1季度`→2026-03-31；`第1-3季度`→2026-09-30；`2025年第4季度`→2025-12-31 |
| 3 | `test_parse_standard_and_invalid` | 方法不存在 | `2026-09-10`→该日；`not-a-date`→None |
| 4 | `test_invalid_dates_are_dropped` | 中文日期抛异常 | 1 有效 + 1 非法 → 仅 1 行 |
| 5 | `test_cpi_prefers_current_value` | 取到前值 | 5 列报告 value=`今值`，trade_date=发布日 |
| 6 | `test_pmi_uses_manufacturing_index` | 解析失败 | value=`制造业-指数`，date=月份 1 日 |
| 7 | `test_gdp_uses_absolute_value` | 解析失败 | value=`国内生产总值-绝对值`，date=季末 |
| 8 | `test_m2_uses_m2_quantity` | 解析失败 | value=`货币和准货币(M2)-数量(亿元)` |
| 9 | `test_fx_uses_forex_reserves` | 解析失败 | value=`国家外汇储备-数值` |
| 10 | `test_import_export_uses_export_amount` | 解析失败 | value=`当月出口额-金额` |
| 11 | `test_unknown_indicator_falls_back_to_first_value_column` | 取到最后一个匹配列 | 列 `['日期','数值','同比']` → `数值` |

全量回归目标：**356 + 11 = 367 passed**，`ruff check .` 零告警。

---

## 6. 生产库重采与验收（实施后回填）

重采命令（逐指标，`MacroLoader` 用 `INSERT OR REPLACE` 覆盖）：

```
py -3 cli.py collect --db factor_db.duckdb --source akshare_macro --indicator <ID>
```

预期行数（依据 2026-11-02 原始表形状）：

| 指标 | 原始行数 | 实际入库 | 说明 |
|------|----------|----------|------|
| GDP_YEARLY | 61 | 59 | 2 行 `今值` 为空被丢弃 |
| GDP_QUARTERLY | 83 | 83 | — |
| CPI_YEARLY | 476 | 475 | 1 行 `今值` 为空 |
| CPI_MONTHLY | 356 | 354 | 2 行 `今值` 为空 |
| PPI_YEARLY | 362 | 361 | 1 行 `今值` 为空 |
| PPI_MONTHLY | 249 | 249 | — |
| PMI_MANUFACTURING | 226 | 226 | — |
| M2_YEARLY | 394 | 337 | 57 行 `今值` 为空（旧逻辑用前值，故显示 394） |
| M2_MONTHLY | 225 | 225 | — |
| INTEREST_RATE | 1,539 | 1,539 | — |
| FX_RESERVES | 225 | 225 | — |
| IMPORT_EXPORT | 225 | 225 | — |
| **合计** | **4,421** | **4,358** | 63 行 `今值` 缺失被丢弃 |

> 重采前先删除 12 个指标的旧行：`INSERT OR REPLACE` 只覆盖同主键，旧逻辑写入的「前值」行在非重叠日期上会残留。

验收锚点（重采后实测）：

- `CPI_MONTHLY` 1996-03-01 = **2.3**（原 2.1）；
- `PMI_MANUFACTURING` 2026-10-01 = **49.3**；
- `GDP_QUARTERLY` 2026-09-30 = **1,071,071.2**；
- `M2_MONTHLY` 2026-09-01 = **3,596,289.70**；
- `FX_RESERVES` 2026-09-01 = **34,002.51**；
- `IMPORT_EXPORT` 2026-09-01 = **447,184,259.939**（当月出口额，原始精度）。

重采后 P5 复跑：

- `get_sector_sensitivity('CPI_MONTHLY')` 返回 **31 行**，beta 全部有限；
- `transform='pct_change'`：`n_obs=40~172`；
- `transform='diff'`：`n_obs=45~186`。
