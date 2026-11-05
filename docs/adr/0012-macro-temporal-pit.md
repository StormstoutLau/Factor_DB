# ADR-0012：宏观数据双日期模型与 PIT 语义

- 状态：已接受
- 日期：2026-11-05
- 修订：无
- 关联：ADR-0003、ADR-0006、P8-1 设计

## 背景

`macro_data.trade_date` 同时承载三种语义：

1. 统计期起点/期末（PMI、PPI_MONTHLY、GDP_QUARTERLY 等）；
2. 发布日期（CPI、CPI_YEARLY、PPI_YEARLY、M2_YEARLY、GDP_YEARLY 等五列发布日历）；
3. 日频观测日（INTEREST_RATE）。

同一字段混用导致 CPI 发布日被归入发布月、PMI 月初日期早于实际发布约 30 天，且无 `publish_date`/`loaded_at` 无法做 PIT 查询。调研见
[宏观数据时间语义与 PIT 对齐调研](../research/macro_temporal_semantics_pit_research.md)。

## 决策

1. 保留 `trade_date` 为**原始采集日期**（兼容列，不再作为分析口径）。
2. 新增 `period_date`（统计期：月度=月末、季度=季末）、`publish_date`（首次可得日）、`publish_date_quality`（`exact/estimated/unknown`）、`loaded_at`（入库时间）。
3. 主键保持 `(trade_date, indicator_id, value_type)`；同一统计期允许多行，查询层按 `(indicator_id, value_type, period_date)` 取最新 `publish_date`/`loaded_at`。
4. 查询与 `get_sector_sensitivity` 以 `period_date` 对齐；`MacroQuery` 增加 `as_of`，按 `publish_date` 过滤、`loaded_at` 兜底。
5. 12 指标规则集中在 `core/macro_temporal.py`；迁移入口 `SchemaManager.migrate_macro_data_temporal()`。
6. P8-1 不建独立 vintage 表；修订版本回放留 P9。

## 理由

- 只加列、不改主键，旧 `INSERT` 与旧测试继续可用；查询用 `COALESCE(period_date, trade_date)` 兜底。
- `publish_date` 与 `period_date` 分离后，CPI 的“7 月数据 8 月发布”可按统计期对齐，PMI 等不再把统计期起点当作可得日。
- `publish_date_quality` 区分真实发布日期与规则估算，避免把估算值当 exact。

## 备选方案

| 方案 | 未采纳原因 |
|------|-----------|
| 直接改 `trade_date` 语义 | 破坏旧查询/旧测试，历史迁移难回滚 |
| 独立 vintage 表 | 当前数据源无完整修订版本，双写成本高 |
| 仅加指标元数据、不改表 | 无法 PIT，日期仍混合 |
| 只规范到月末/季末 | 修复月份错位但仍有前视 |

## 后果

正面：

- 宏观查询可按 `as_of` 回放“当时可见”数据。
- CPI 敏感度不再错位；PMI/PPI_MONTHLY/M2_MONTHLY 等不再把统计期起点当作可得日。
- `source_id`/`indicator_name` 补齐，溯源可审计。

负面 / 约束：

- 历史 `publish_date` 多为规则估算，必须标记 `estimated`；M2/外储/进出口的假设发布日待官方日程核实。
- 同一统计期多行依赖查询层去重；完整修订回放需 P9 vintage 表。
- 旧 `trade_date` 查询仍保留旧语义，新分析必须使用 `period_date`。
