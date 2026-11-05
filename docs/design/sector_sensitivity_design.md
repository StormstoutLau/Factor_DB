# 行业宏观敏感度（`get_sector_sensitivity`）设计

> 日期：2026-11-02
> 依据：[sector_sensitivity_research.md](../research/sector_sensitivity_research.md)（方法调研） / [industry_return_data_source_research.md](../research/industry_return_data_source_research.md)（数据源）
> 范围：补齐 `MacroFactorLink.get_sector_sensitivity()`，不新增表、不改 CLI、不引入新依赖

---

## 1. 目标与非目标

### 目标

1. 读取 `macro_data` 单指标与 `alternative_data` 行业日收益，按月对齐后估计**单因子 OLS beta**。
2. 输出每个行业一行：`beta` / `correlation` / `t_stat` / `r_squared` / `n_obs` / 区间。
3. 支持行业子集、级别（`first/second/third`）、宏观变换（`pct_change/diff/level`）。
4. 空数据、样本不足、宏观无变化时返回**带固定列的空表**，不抛异常。
5. 测试使用临时 DuckDB + 合成数据，不触网。

### 非目标

- 滚动 beta、多因子回归、VAR/VECM（调研 §2 方案 4/5，后续版本再做）。
- PIT / 发布时点对齐（`macro_data`、`alternative_data` 均无 `loaded_at`）。
- 新增 `industry_returns` 表或 CLI 子命令。
- 输出 p 值（依赖无 `scipy`，见调研 §5）。

---

## 2. 数据流

```
get_sector_sensitivity(macro_indicator, sectors?, start, end, level, transform, min_obs)
    ├─ MacroQuery.get_macro_data([macro_indicator], start, end)
    │     └─ 按自然月取最后一个观测 → 月度序列
    │     └─ transform: pct_change | diff | level → dropna
    ├─ AlternativeQuery.get_data('industry_return', data_subtype=level, start, end)
    │     └─ 可选 sectors 过滤 entity_id
    │     └─ 月度复利: (1 + value).groupby(entity, period).prod() - 1
    ├─ 按 period 内连接（宏观月度变化 × 行业月度收益）
    ├─ 逐行业 OLS: r_i = α + β·Δm + ε
    │     └─ β = Sxy / Sxx；t = β / se；R² = 1 - SSE/SST
    └─ 汇总、按 |β| 降序排序、输出固定列
```

### 2.1 频率与边界

- 行业日收益按月**复利**（`∏(1+r_d) - 1`），与月末到月末的收益口径一致。
- 宏观按 `trade_date` 的 `Period('M')` 分组取最后值；季度指标自然退化为“相邻观测变化”。
- `start_date` / `end_date` 直接下推到两个查询；首尾月份可能为不完整月，输出 `start_date/end_date` 以便识别。

### 2.2 对齐与因果语义

- 合并键为自然月，属于**同期关联**；`macro_data.trade_date` 是原始采集日期（语义混合：发布日/统计期起点/季末/日频观测），不能当作发布时点可得的信号（调研 §4.5）。
- ~~不做前视填充、不做 PIT 回溯。~~（P8-1 更新：默认按 `period_date` 统计期对齐；`as_of` 可按 `publish_date` 做 PIT 截断，ADR-0012）

---

## 3. 接口契约

### 3.1 函数签名（`analytics/macro_factor_link.py`）

```python
def get_sector_sensitivity(
    self,
    macro_indicator: str,
    sectors: Optional[List[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    level: Optional[str] = 'first',
    transform: str = 'pct_change',
    min_obs: int = 12,
) -> pd.DataFrame:
```

| 参数 | 类型 | 默认 | 规则 |
|------|------|------|------|
| `macro_indicator` | `str` | 必填 | `macro_data.indicator_id` |
| `sectors` | `Optional[List[str]]` | `None` | `None`=全部；传列表则按 `entity_id` 过滤 |
| `start_date` / `end_date` | `Optional[date]` | `None` | 下推到宏观与行业查询 |
| `level` | `Optional[str]` | `'first'` | 仅 `None/first/second/third`，否则 `ValueError` |
| `transform` | `str` | `'pct_change'` | 仅 `pct_change/diff/level`，否则 `ValueError` |
| `min_obs` | `int` | `12` | `<3` 抛 `ValueError`；样本不足的行业跳过 |

### 3.2 输出列（固定顺序）

| 列 | 类型 | 说明 |
|----|------|------|
| `industry_code` | str | `entity_id`（6 位行业代码） |
| `industry_name` | str/None | `entity_name` |
| `level` | str/None | `data_subtype` |
| `beta` | float | 宏观月度变化每变动 1 单位，行业月收益的变动 |
| `correlation` | float | 同期 Pearson 相关（`Sxy / sqrt(Sxx·Syy)`） |
| `t_stat` | float | `beta / se`；`se=0` 时置 0 |
| `r_squared` | float | `1 - SSE/SST`；`SST=0` 时置 0 |
| `n_obs` | int | 有效月度观测数 |
| `start_date` | date | 首个有效月份起始日 |
| `end_date` | date | 末个有效月份结束日 |

- 排序：`abs(beta)` 降序，再按 `industry_code` 升序，保证确定性。
- 空结果：返回上述 10 列的空 `DataFrame`。

### 3.3 错误语义

| 场景 | 行为 |
|------|------|
| 宏观数据为空 | 返回空表（带列），`logger.warning` |
| 行业数据为空 / `sectors` 无匹配 | 返回空表（带列），`logger.warning` |
| 宏观月度变化全为 NaN / 方差为 0 | 返回空表，`logger.warning` |
| 单个行业 `n_obs < min_obs` 或 `Sxx≤0` | 跳过该行业，记 `debug`；其余行业正常 |
| `transform` / `level` 非法、`min_obs<3` | 立即 `ValueError` |

---

## 4. 关键决策及依据

| # | 决策 | 依据 |
|---|------|------|
| D1 | 主口径为单因子 OLS beta | 调研 §3/§4：APT 因子敏感度即 beta；文献以月度收益对宏观变量回归 [1][2][3] |
| D2 | 行业日收益按月复利，宏观按月取最后值 | 日频收益 × 月频宏观直接合并会产生伪显著；月度对齐是文献常见做法 [1][3]（调研 §4.2） |
| D3 | 默认对宏观水平做 `pct_change` | 水平序列含趋势/单位根，易伪回归 [5]（调研 §4.3） |
| D4 | 提供 `diff` / `level` 变换 | PMI 等扩散指数以绝对变化/水平更有解释力；把口径选择交给调用方 |
| D5 | 输出 `t_stat` 但不输出 `p_value` | 依赖无 `scipy`，无法计算 t 分布 CDF（调研 §5）；|t|≈2 可作大样本近似判据 |
| D6 | `min_obs=12` | 月度回归样本过少时系数不稳；12 个月为可解释的最低年限（调研 §4.6） |
| D7 | 默认 `level='first'` | P4 默认只采集一级行业（31 个），二级/三级数据量与外发接口不适合作为默认；调用方可显式传 `second/third/None` |
| D8 | 返回带固定列的空表而非异常 | 与 `MacroFactorLink` 现有方法（`calculate_correlation` 返回 0.0、`identify_macro_regime` 返回空表）的错误语义一致，便于调用方链式处理 |
| D9 | 不新建表、不改 CLI | 数据已在 `alternative_data`；本任务是补齐 analytics 方法，最小改动 |

---

## 5. 风险与权衡

| 风险 | 影响 | 设计应对 |
|------|------|----------|
| 单因子遗漏变量 | beta 吸收其他宏观因素 | 定位为横向粗筛；文档注明；后续可扩展多因子 |
| 同期关联被误读为预测 | 策略误用 | docstring 与文档明确“非 PIT、非预测” |
| 月末边界不完整 | 首尾月 beta 偏差 | 输出 `start_date/end_date`；必要时调用方收紧区间 |
| 行业分类漂移 | 长样本 beta 不可比 | 输出 `n_obs` 与区间；建议分段 |
| 生产库无数据 | 无法生产实跑 | TDD 临时库；E2E 先 `collect` 数据（见实施文档 §6） |
| 多级别重复代码 | `level=None` 时输出量增大 | 默认一级；`level` 列标识来源 |

---

## 6. 与现有代码的兼容性

- `MacroFactorLink.__init__` 新增 `self.alternative_query = AlternativeQuery(db_path)`；只读连接，与现有 `MacroQuery` / `FactorQuery` 并列。
- 原签名前 4 个参数保持不变，新参数均为带默认值的关键字参数，现有调用（仓库内无调用方）不受影响。
- 不修改 `MacroQuery` / `AlternativeQuery` / 表结构 / CLI。
- 新增导入 `from Factor_DB.query.alternative_query import AlternativeQuery`。
