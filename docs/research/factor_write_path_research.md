# 因子写路径统一：调研文档

> 主题：迁移后 `factor_data` 变为视图，现有因子写入路径全部失效
> 日期：2026-10-21
> 前置：[ADR-0004](../../adr/0004-wide-history-dual-track.md) / [双轨迁移设计](../design/wide_migration_design.md)

---

## 1. 问题定义

生产库 `factor_data` 仍为 16.83 亿行实体表；双轨迁移尚未执行。迁移完成后 `factor_data` 会被替换为 `UNPIVOT(factor_wide)` 视图。需要确认：现有写入代码在迁移后是否仍可工作。

## 2. 代码实证

| 编号 | 事实 | 位置 |
|------|------|------|
| C1 | `DailyLoader._import_price_data` 因子分支 `INSERT INTO factor_data` | `loaders/daily_loader.py#L240` |
| C2 | `DailyLoader._import_to_factor_table` 也写 `factor_data`，但全仓库无调用方（死代码） | `loaders/daily_loader.py#L287-L318` |
| C3 | `CSMARLoader._import_stock_factor` / `_import_market_factor` 直接 `INSERT INTO factor_data` | `loaders/csmar_loader.py#L314`、`#L368` |
| C4 | Phase 1 的 barra 因子展开后直接 `INSERT INTO factor_data` | `scripts/import_phase1_ashare.py#L431` |
| C5 | `load_factor_to_wide()`（宽表 + 历史双写）仅被测试调用，无生产调用方 | grep `load_factor_to_wide` 结果：tests/*、loaders 定义，无 scripts/cli |
| C6 | `create_factor_wide` 使用 `CREATE TABLE IF NOT EXISTS`；若表已存在则不会补列 | `core/schema.py#L660` |
| C7 | 迁移的写入方为 `arg_max` 宽表 upsert + 历史表追加，历史已存在时只校验 `hist_existing < src_rows` | `core/schema.py#L915`、`#L1057` |
| C8 | `load()` / `load_dataframe()` 共用的因子入口是 `_import_price_data`，影响 Phase 1 财务/特殊因子与 Phase 4 | `loaders/daily_loader.py#L76`、`#L240` |

## 3. 外部行为实证

### E1 — DuckDB 1.5.3 不支持向视图写入（本机探针）

```
INSERT INTO view: FAIL -> CatalogException: factor_data is not an table
CREATE OR REPLACE VIEW: OK
```

探针步骤：建表 → `ALTER TABLE ... RENAME` → `CREATE VIEW` 同名 → `INSERT INTO` 视图。结论：迁移后 C1/C3/C4 三条路径会直接报错，属于**迁移前必须解决**的阻塞项。

### E2 — DDL 可事务化（沿用 ADR-0008 的 6 项探针结论）

`CREATE/ALTER/RENAME/VIEW` 在 DuckDB 中可回滚，因此写路径可在单事务内完成 history + wide + 兼容表写入。

## 4. 方案对比

| 方案 | 做法 | 结论 |
|------|------|------|
| A | 迁移后保留 `factor_data` 实体表，不用视图 | 放弃 ADR-0004 的兼容视图；双份存储、双份写入，查询仍走旧表 | 否决 |
| B | 只写 `factor_history`，宽表由定时任务重建 | 迁移后日常查询退化到长表；违背宽表为 99% 查询服务的决策 | 否决 |
| C | 所有因子写入统一走「history + wide 双写」，迁移前额外写 `factor_data` 保持旧读者一致 | 一次改造覆盖所有入口；迁移前/后行为一致 | **采用** |

## 5. 幻觉排除

| 候选说法 | 核查 | 结论 |
|---------|------|------|
| “DuckDB 视图支持 INSERT（自动转发基表）” | E1 实测报 `Catalog Error: ... is not an table` | 证伪 |
| “可用 INSTEAD OF 触发器让视图可写” | DuckDB 1.5.3 无 `CREATE TRIGGER ... INSTEAD OF` 语法支持记录；本项目未使用触发器 | 证伪/不采用 |
| “现有 loader 已双写宽表” | C5：`load_factor_to_wide` 无生产调用方；C1 仍写长表 | 证伪 |
| “迁移会自动补齐宽表缺列” | C6：`IF NOT EXISTS` 不补列 | 证伪（需在迁移中补） |
| “`factor_data` 视图可承载新写入” | 视图对 `SELECT` 兼容，对 `INSERT` 不可用 | 部分成立（只读兼容） |

## 6. 结论

必须先统一写路径，再执行生产迁移。写路径需同时满足：迁移前（`factor_data` 为表）与迁移后（为视图）都能写入并被新旧查询读取。
