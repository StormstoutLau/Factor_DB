# CPI_MONTHLY beta 修复实施规划（P7）

> 日期：2026-11-04
> 上游：[调研](../research/cpi_monthly_beta_finiteness_research.md) / [设计](../design/cpi_monthly_beta_fix_design.md)
> 本文档为编码前规划；生产复跑结果在 §6 回填。

---

## 1. 分步实施计划（每步可独立验证）

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | 新增模块常量 `RATE_TYPE_INDICATORS`（6 个率型指标） | 单测：集合包含 CPI_MONTHLY/CPI_YEARLY/PPI_YEARLY/M2_YEARLY/GDP_YEARLY/INTEREST_RATE |
| S2 | `transform` 默认改为 `None`；`None` 按指标类型解析 | 单测：率型 → diff；水平型 → pct_change |
| S3 | 显式 `transform` 校验与覆盖 | 单测：`pct_change` 仍可复现旧结果；非法值 ValueError |
| S4 | 日志记录解析后的 transform | 人工检查日志/行为 |
| S5 | 全量回归 + 生产 CPI 复跑 | 371 passed；31 行、n_obs/ beta 与 diff 口径一致 |

TDD 顺序：先补 `tests/test_macro_factor_link.py` 4 个用例 → 确认红 → 实现 S1-S4 → 定向绿 → 全量回归。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 无新增依赖；仅用现有 `typing.Optional`；Python `>=3.9` 兼容 |
| **依赖** | 不改 `pyproject.toml` |
| **兼容** | 显式 `transform` 调用完全兼容；`TEST_MACRO` 等水平型默认不变；生产 CPI 默认变化为修复目标 |
| **接口** | `transform: Optional[str] = None`；`None` 自动、显式值优先；输出 10 列不变 |
| **签名** | 无可变默认参数；`RATE_TYPE_INDICATORS` 为 `frozenset` |
| **低效操作** | 解析在查询前完成，无额外 SQL；无 pandas `melt`/逐行操作 |
| **内存** | 不新增数据结构；仅一个 6 元素集合 |
| **数据一致性** | 同一 `macro_indicator` 默认口径固定；显式覆盖可复现旧结果；日志记录实际 transform |
| **失败语义** | 非法 `transform` 立即 `ValueError`；未知指标回落 `pct_change`；Inf 安全网保留 |

---

## 3. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `analytics/macro_factor_link.py` | 新增 `RATE_TYPE_INDICATORS`；`transform` 默认 `None` 与解析逻辑；docstring |
| `tests/test_macro_factor_link.py` | 新增 4 个用例（默认率型 diff、None 解析、显式覆盖、水平型回归） |
| `docs/research/cpi_monthly_beta_finiteness_research.md` | 已有（P7 调研） |
| `docs/design/cpi_monthly_beta_fix_design.md` | 已有（P7 设计） |
| `docs/implementation/cpi_monthly_beta_fix_implementation.md` | 本文件（§6 回填） |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第二十节 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `CODE_WIKI.md` / `README.md` / `README_zh.md` | 实施后同步（测试数 367→371、默认口径说明） |

---

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `get_sector_sensitivity.transform` 默认 | `'pct_change'` | `None`（按指标解析） |
| `CPI_MONTHLY` 默认变换 | `pct_change`（21 个 Inf 被丢弃、137 次符号反转） | `diff`（353 个变化保留、符号正确） |
| `CPI_YEARLY/PPI_YEARLY/M2_YEARLY/GDP_YEARLY/INTEREST_RATE` 默认 | `pct_change` | `diff` |
| 水平型指标默认 | `pct_change` | `pct_change`（不变） |
| 显式 `transform` | 直接使用 | 直接使用（优先于默认解析） |
| 输出列 / 排序 / min_obs / 空表 | 不变 | 不变 |

---

## 5. 测试计划（TDD）

在 `tests/test_macro_factor_link.py` 新增 4 个用例。合成 `TEST_RATE`：

```
RATE_VALUES = [0.0, 0.5, -0.2, 0.3, 0.0, 0.7, -0.1, 0.4, 0.2, -0.3,
               0.0, 0.5, -0.2, 0.3, 0.1, -0.1, 0.4, 0.0, 0.6, -0.2,
               0.3, 0.1, -0.1, 0.2]        # 24 个月，含 4 个 0 与多个负值
RET_RATE = [0.0] + [2*dx + noise for dx, noise in zip(diff(RATE_VALUES), NOISE)]
行业代码 801050
```

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_default_rate_uses_diff` | 默认走 pct_change，beta 与 diff 手算不符、n_obs < 23 | 默认 beta = `_beta(diff(RATE_VALUES), RET_RATE[1:])`；n_obs=23 |
| 2 | `test_transform_none_resolves_rate_to_diff` | 当前签名不接受 `None` → ValueError | `transform=None` 返回与用例 1 相同 beta |
| 3 | `test_explicit_pct_change_overrides_rate_default` | 当前显式 pct_change 已可用（回归守卫） | beta = 过滤 Inf 后的 pct 手算；n_obs < 23 |
| 4 | `test_default_level_indicator_stays_pct_change` | 当前默认 pct_change 已可用（回归守卫） | `TEST_MACRO` 默认 beta/n_obs 与显式 pct_change 完全一致 |

红灯实际预期：新增 4 个中 **2 failed / 2 passed**；文件合计 **13 passed / 2 failed**。
绿灯目标：文件 **15 passed**；全量 **371 passed**（367 + 4），`ruff check .` 零告警。

---

## 6. 生产库复跑与验收（实施后回填）

命令（只读）：

```python
from Factor_DB.analytics.macro_factor_link import MacroFactorLink
link = MacroFactorLink('factor_db.duckdb')
df_default = link.get_sector_sensitivity('CPI_MONTHLY', start_date=date(2010, 1, 1))
df_old = link.get_sector_sensitivity('CPI_MONTHLY', start_date=date(2010, 1, 1),
                                     transform='pct_change')
```

验收锚点（2026-11-04 生产库实测）：

- 默认（diff）：**31 行**，`n_obs=45~186`，beta 范围 **-0.0113~0.0232**（典型 **0.0232/百分点**，电子/综合/农林牧渔居前），全部有限；
- 显式 `pct_change`：**31 行**，`n_obs=40~172`，beta 范围 **-7.8e-05~0.00765**（典型 **0.0076**），可复现旧口径；
- 默认结果与显式 `diff` 完全一致；与显式 `pct_change` 的 beta 不同；
- 两种口径均无 NaN/Inf；默认结果不使用 21 个零基数 Inf 月份。

回归结果：定向 **15 passed**；全量 **371 passed**；`ruff check .` All checks passed。
