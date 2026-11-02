# 行业宏观敏感度（`get_sector_sensitivity`）实施规划

> 日期：2026-11-02
> 上游：[方法调研](../research/sector_sensitivity_research.md) / [设计](../design/sector_sensitivity_design.md)
> 本文档为编码前规划；实现与实跑结果在 §6 回填。

---

## 1. 分步实施计划（每步可独立验证）

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | `MacroFactorLink.__init__` 增加 `AlternativeQuery`；新增导入 | 实例化不报错；现有方法不受影响 |
| S2 | 参数校验：`level` / `transform` / `min_obs` | 单测：非法值抛 `ValueError` |
| S3 | 宏观月度化与变换（`pct_change/diff/level`） | 单测：`diff` 与 `pct_change` 的 beta 不同且与手算一致 |
| S4 | 行业日收益月度复利 + `sectors` 过滤 | 单测：2 条日收益复利结果与手算一致 |
| S5 | 逐行业 OLS（beta/correlation/t/R²/n）与空表语义 | 单测：beta 符号/数值、排序、空数据 |
| S6 | 文档同步与临时库 E2E | 全量回归 + 真实数据小样本 |

实施顺序遵循 TDD：先写 `tests/test_macro_factor_link.py` 全部用例 → 确认红 → 实现 S1-S5 → 定向绿 → 全量回归。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 不新增依赖。`pandas>=2.0`、`numpy>=1.24` 已在 `pyproject.toml`；未使用 `scipy`（无 p 值）；Python `>=3.9`，沿用 `from __future__ import annotations` |
| **依赖** | 无新增/移除；`AlternativeQuery` 为仓库内已有类，只读连接 |
| **兼容** | 原 4 个位置参数不变，新增参数均有默认值；仓库内无 `get_sector_sensitivity` 调用方；不改表结构、loader、CLI |
| **接口** | 见设计 §3.1；返回固定 10 列；空结果也带列 |
| **签名** | `sectors: Optional[List[str]] = None`、`start_date/end_date: Optional[date] = None`、`level: Optional[str] = 'first'`、`transform: str = 'pct_change'`、`min_obs: int = 12`；无可变默认参数 |
| **低效操作** | 无 pandas `melt()`；无逐行 SQL；一次查询宏观 + 一次查询全部行业，随后 pandas 分组；不逐行业重复查库 |
| **内存** | 一级行业 140,989 行 × 10 列（P4 实测）在 pandas 中约数十 MB；三级全量约 217 万行仍远低于 32GB |
| **数据一致性** | 宏观按月取最后观测；行业日收益按月复利；内连接丢弃缺失月；不 fill/不前视；结果区间以实际有效月份为准 |
| **数值稳定** | `pct_change(fill_method=None)` 避免 pandas 2.x 默认填充告警；`Sxx<=0` 跳过；`SST=0` 时 R²置 0；`se=0` 时 t 置 0；`SSE` 取 `max(0, ·)` 防浮点负值 |
| **日志** | 无数据/全无变化 `warning`；单行业样本不足 `debug`；成功时输出行业数与观测区间 |

---

## 3. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `analytics/macro_factor_link.py` | 新增 `AlternativeQuery` 导入与实例；实现 `get_sector_sensitivity` |
| `tests/test_macro_factor_link.py` | **新增**：11 个 TDD 用例 |
| `docs/research/sector_sensitivity_research.md` | **新增**：方法调研 |
| `docs/design/sector_sensitivity_design.md` | **新增**：设计 |
| `docs/implementation/sector_sensitivity_implementation.md` | 本文件（§6 回填） |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第十八节 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` | 实施后同步（测试数 345→356、analytics 能力说明） |

---

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `MacroFactorLink.__init__` | 仅 `MacroQuery` + `FactorQuery` | 增加 `AlternativeQuery`（只读） |
| `get_sector_sensitivity` 签名 | `(macro_indicator, sectors=None, start_date=None, end_date=None)` | 追加 `level='first'`、`transform='pct_change'`、`min_obs=12` |
| 返回 | 恒为空 `DataFrame`（TODO） | 10 列敏感度表；空数据返回同列空表 |
| 错误语义 | 无 | 非法参数 `ValueError`；数据缺失返回空表 |

---

## 5. 测试计划（TDD）

测试文件 `tests/test_macro_factor_link.py`，临时 DuckDB + 合成数据，不触网。

### 5.1 合成数据设计

- 24 个月（2023-01 ~ 2024-12），宏观指标 `TEST_MACRO` 的月度 `pct_change` 为已知序列 `c`。
- 一级行业 `801010` 月收益 `= 2·c`，`801020` 月收益 `= -0.5·c`；二级行业 `801016` 月收益 `= 5·c`（用于级别过滤）。
- 每月插入 1 条日收益（数值即月收益）用于 beta 用例；另建一组每月 2 条日收益用于复利用例。
- `morning` 指标 `TEST_PMI` 为水平值序列，用于 `diff` 变换用例。

### 5.2 用例列表

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_empty_when_macro_missing` | 返回空表（无列）或 TODO 空表 | 空表且列 = 设计 §3.2 |
| 2 | `test_beta_and_columns_for_first_level` | `AttributeError`/返回空表 | 2 行；列序正确；`beta≈2/-0.5`；`n_obs=23`；按 |beta| 降序 |
| 3 | `test_monthly_returns_are_compounded` | 未按复利计算 | 2 条日收益复利后 beta 与手算一致 |
| 4 | `test_level_filter_and_invalid_level` | 未实现级别过滤 | 默认仅 first；`second` 只返回 801016；`None` 返回 3 行；非法级别 `ValueError` |
| 5 | `test_sectors_subset` | 未实现过滤 | 仅返回指定 1 行 |
| 6 | `test_transform_diff_and_level` | 未实现变换 | `diff` beta 与手算一致；非法 transform `ValueError` |
| 7 | `test_min_obs_skips_short_history` | 未实现 | 6 个月历史的行业被跳过，长历史行业保留 |
| 8 | `test_constant_macro_returns_empty` | 除以 0 或异常 | 宏观无变化 → 空表（带列） |
| 9 | `test_min_obs_validation` | 未校验 | `min_obs=2` → `ValueError` |
| 10 | `test_date_range_and_output_dates` | 未实现 | 区间限制后 `n_obs` 正确，`start_date/end_date` 为有效期首末月 |
| 11 | `test_industry_name_and_level_columns` | 缺列 | `industry_name` / `level` 与 `alternative_data` 一致 |

全量回归目标：**345 + 11 = 356 passed**，`ruff check .` 零告警。

---

## 6. 生产实跑与验收（实施后回填）

### 6.1 临时库 E2E 结果（2026-11-02）

| 项 | 结果 |
|----|------|
| 宏观采集 | `CPI_MONTHLY` **356 行**，1996-03-01 ~ 2025-09-10 |
| 行业采集 | 首轮 `801010/801030` 入库 **12,964 行**；补抓 `801040` 后 **19,446 行 / 3 个行业**，2000-01-04 ~ 2026-11-02 |
| 采集耗时 | 首轮（含 CPI）**96.3 s**（`rate_limit=0`，3 个行业代码中有 1 个无效、1 个瞬时失败） |
| 敏感度输出 | **3 行**、`n_obs=179`、区间 **2010-02-01 ~ 2025-09-30**（受 CPI 数据末端限制） |
| 排序 | 按 beta 绝对值降序：`801030` → `801040` → `801010` |
| 数值 | 全部 beta/correlation/t_stat 有限，无 NaN |

明细：

| 行业 | 名称 | beta | correlation | t_stat | r_squared |
|------|------|------|-------------|--------|-----------|
| 801030 | 基础化工 | -0.003568 | -0.094147 | -1.258128 | 0.008864 |
| 801040 | 钢铁 | -0.002179 | -0.057173 | -0.761888 | 0.003269 |
| 801010 | 农林牧渔 | 0.000868 | 0.022426 | 0.298430 | 0.000503 |

### 6.2 偏差与处理

| 偏差 | 说明 | 处理 |
|------|------|------|
| `801020` 采不到 | 该代码**不在当前申万一级行业目录**（首批 31 个行业无此代码），非网络故障 | 改用 `801040` 作为第 3 个行业 |
| `801040` 两次失败 | `Expecting value: line 1 column 1`，与 P4 中 `801040/801770` 的瞬时故障一致 | 第三次重试成功（6482 行），验证 P4 容错结论 |
| CPI 末端 2025-09-10 | AKShare 宏观数据发布滞后 | 内连接后敏感度区间止于 2025-09，属预期 |
| beta 量级小 | CPI 月度变化（`pct_change`）幅度小，beta 为“每单位变化”系数 | 量纲由 `transform` 决定，横向比较看排序与 t 值 |

> P5 验收时生产库 `macro_data` / `alternative_data` 均为 0 行，故 E2E 在临时库完成。P6 后生产库已入库 4,358 行宏观（12 指标）与 144,057 行一级行业（31 个），生产复跑 `get_sector_sensitivity` 返回 31 行、beta 有限。
