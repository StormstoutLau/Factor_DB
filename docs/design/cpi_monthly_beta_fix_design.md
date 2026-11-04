# CPI_MONTHLY beta 修复设计（P7）

> 日期：2026-11-04
> 依据：[CPI_MONTHLY beta 有限性调研](../research/cpi_monthly_beta_finiteness_research.md)
> 上游：[行业宏观敏感度设计（P5）](sector_sensitivity_design.md) / [宏观采集归一化（P6）](../implementation/macro_collector_date_value_implementation.md)
> 范围：修复率型宏观指标在 `get_sector_sensitivity` 默认 `pct_change` 下的符号反转、极端值与 beta 失真；不改表结构、采集器、CLI

---

## 1. 目标与非目标

### 目标

1. 对**已知率型指标**（可为 0/负值的增速、利率）默认使用 `transform='diff'`，消除零基数 Inf 与负基数符号反转。
2. 对**水平型、恒正指标**保持默认 `pct_change`，不改变既有行为。
3. 显式传入 `transform='pct_change'|'diff'|'level'` 时仍优先使用调用方口径。
4. 默认解析结果写入日志，便于复现。

### 非目标

- 不引入 `macro_indicators.value_kind` 元数据或数据驱动自动判别（调研 §5.3 建议留待后续）。
- 不改变 `macro_data` / `alternative_data` schema、采集器、CLI。
- 不改变 P5 输出的 10 列结构、排序与 OLS 公式。
- 不为 PMI 等扩散指数新增 `diff` 默认（当前按水平型处理，后续可评估）。

---

## 2. 数据流与解析顺序

```
get_sector_sensitivity(macro_indicator, ..., transform=None)
    ├─ if transform is None:
    │     ├─ macro_indicator ∈ RATE_TYPE_INDICATORS → 'diff'
    │     └─ 否则 → 'pct_change'
    ├─ else: 校验 transform ∈ {'pct_change','diff','level'}，非法抛 ValueError
    ├─ 宏观月度序列 → 应用解析后的 transform
    └─ 后续 OLS / 输出保持不变
```

`RATE_TYPE_INDICATORS`（依据 P6 后的生产库值域与调研 §2.1）：

| 指标 | 值域/类型 | 默认 |
|------|-----------|------|
| `CPI_MONTHLY` | -1.8~2.6（环比 %） | `diff` |
| `CPI_YEARLY` | -2.2~28.4（同比 %） | `diff` |
| `PPI_YEARLY` | -8.2~13.5（同比 %） | `diff` |
| `M2_YEARLY` | 0~29.7（同比 %） | `diff` |
| `GDP_YEARLY` | -6.8~18.3（年率 %） | `diff` |
| `INTEREST_RATE` | 3.0~5.77（LPR %） | `diff` |
| 其余（PMI/PPI_MONTHLY/M2_MONTHLY/FX/GDP_QUARTERLY/IMPORT_EXPORT 等） | 恒正水平 | `pct_change` |

---

## 3. 接口契约

### 3.1 签名变更

```python
def get_sector_sensitivity(
    self,
    macro_indicator: str,
    sectors: Optional[List[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    level: Optional[str] = 'first',
    transform: Optional[str] = None,
    min_obs: int = 12,
) -> pd.DataFrame:
```

| 参数 | Before | After |
|------|--------|-------|
| `transform` | `str = 'pct_change'` | `Optional[str] = None`，`None` 按指标类型解析 |

- `transform=None`：率型 → `diff`；其余 → `pct_change`。
- 显式值：`pct_change` / `diff` / `level`，非法值 `ValueError`。
- 未知指标（不在映射中）→ `pct_change`（保持旧默认）。

### 3.2 输出与错误语义

- 输出列、排序、`min_obs`、空表语义均不变。
- 日志中的 `transform` 字段记录**解析后**的值，例如 `CPI_MONTHLY ... (diff, level=first)`。

---

## 4. 关键决策及依据

| # | 决策 | 依据 |
|---|------|------|
| D1 | 率型指标默认 `diff` | 调研 §2/§5：CPI 21 个零值产生 21 个 Inf；142 个负值导致 137 次符号反转；`diff` 无除以零、符号正确 |
| D2 | 水平型指标保持 `pct_change` | 调研 §2.1：PMI/PPI 当月/M2 数量/外储/GDP 绝对值/进出口金额均为恒正水平，百分比变化合理 |
| D3 | 用硬编码集合而非数据驱动判别 | 调研 §5.3：自动判别可能静默改变口径；显式指标清单可审计、可测试 |
| D4 | 显式 `transform` 优先 | 保留调用方覆盖能力；旧 `pct_change` 结果可复现 |
| D5 | 不改变输出列 | 避免破坏 P5 验收与下游；仅修默认口径 |
| D6 | 未知指标回落 `pct_change` | 与 P5 旧行为一致，不引入额外 breaking change |
| D7 | 不在 P7 引入 `value_kind` 元数据 | 需要 schema/采集器/指标目录联动，超出本次修复范围；先以模块常量落地 |

---

## 5. 风险与权衡

| 风险 | 影响 | 应对 |
|------|------|------|
| CPI 默认结果变化 | 旧调用（当前仓库内无）得到不同 beta | 设计上即修复目标；显式传 `pct_change` 可复现旧值；DEVLOG/文档注明 |
| 率型清单维护 | 新增率型指标可能漏配 | 清单集中在模块常量；新增指标时评审；文档给出判别规则（是否可为 0/负） |
| 硬编码与元数据脱节 | 指标目录变更后清单过期 | 后续 P8 可迁移到 `macro_indicators.value_kind`；当前以测试锁定 6 个已知指标 |
| `PMI` 仍用 `pct_change` | 扩散指数百分比变化不直观 | 调研已注明为水平型；后续可单独评估 `diff` |
| 未知指标默认不变 | 仍可能出现零基数 Inf | 安全网（Inf 过滤）保留；调用方可显式传 `diff` |

---

## 6. 兼容性

- 公共方法签名仅把默认值从 `'pct_change'` 改为 `None`；显式调用完全兼容。
- 仓库内 `get_sector_sensitivity` 无生产调用方，仅测试；测试中 `TEST_MACRO` 不在率型集合，默认仍为 `pct_change`。
- 生产 `CPI_MONTHLY` 默认行为改变，属本次修复目标。
- 不修改 `MacroQuery` / `AlternativeQuery` / loader / 表 / CLI。
