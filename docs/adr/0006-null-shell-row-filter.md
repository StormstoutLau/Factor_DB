# ADR-0006: PIT 查询排除 NULL 空壳行

- **状态**: 已接受
- **日期**: 2026-07

## 背景
`daily_prices` 表 43,226,352 行中 36,021,960 行（83%）价格字段为 NULL。根因：后续增量更新只覆盖了 `adj_factor` 字段，`INSERT` 新行时其他价格字段为 NULL，形成"空壳行"。

PIT 子查询 `MAX(loaded_at)` 选中了这些最新的空壳行，导致 `get_price_matrix()` 返回全 NaN，进而 ICIR 全为 NaN。

## 决策
在 PIT 子查询 `_build_pit_subquery()` 增加 `non_null_columns` 参数，在 `MAX(loaded_at)` 子查询中追加 `col IS NOT NULL` 过滤，排除空壳行。

## 理由
- 只取"目标字段非空"的最新版本，语义正确
- 不改表结构、不删数据，最小侵入

## 备选方案
- 清理空壳行：破坏 PIT 版本链，且需大量重写
- 修改写入逻辑避免空壳行：治本但需重导入历史数据

## 后果
- ✅ NaN 比例从 ~100% → 0.0%
- ✅ 240 个测试全部通过（新增 `test_pit_null_filter.py` 5 个测试）
- ⚠️ 治标方案；写入侧的空壳行问题仍存在，长期应考虑合并写入逻辑
