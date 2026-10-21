# ADR-0008: 双轨迁移安全机制

- **状态**: 已接受
- **日期**: 2026-10
- **相关**: 修订 ADR-0004 的迁移执行方式

## 背景

`migrate_factor_data_to_wide()` 原实现存在数据丢失风险（Claude 代码评审 CRITICAL）：

- `_migrate_single_factor()` 捕获异常后静默返回 0，单个因子失败不阻断流程
- `_replace_factor_data_with_view()` 无条件 `DROP TABLE factor_data`
- 结果：部分因子未迁移时，源表仍被删除，未迁移数据永久丢失
- 另有：重跑不幂等（历史表重复追加）、无行数校验

## 决策

迁移遵循 6 条安全不变量（详见 [设计文档](../design/migration_safety_design.md)）：

| 编号 | 不变量 |
|------|--------|
| I1 | 任一因子失败 → 中止迁移，源表 `factor_data` 不变 |
| I2 | 每因子的宽表 upsert + 历史表写入在同一事务内原子提交 |
| I3 | 事务内行数对账：宽表非空数 == 源 distinct 键数；历史 == 源非空行数 |
| I4 | 替换使用 `ALTER TABLE ... RENAME TO factor_data_backup_<ts>`，绝不 DROP |
| I5 | 幂等：`factor_data` 已是视图 → 短路返回；历史已完整 → 只补宽表 |
| I6 | 资源配置（memory_limit 等）失败 → 抛 `MigrationError` 中止 |

实现要点：

- `MigrationError(RuntimeError)`：以前失败返回 0，现在抛异常（行为变更，仅测试为调用方）
- RENAME + `CREATE VIEW` 在同一 `BEGIN/COMMIT` 内，失败 `ROLLBACK` + 兜底改回原名
- 部分历史（`0 < hist_existing < src_rows`）视为异常状态，中止并要求人工介入

## 理由

- DuckDB 1.5.3 实测：`INSERT` / `DROP TABLE` / `ALTER TABLE RENAME` / `CREATE VIEW` 在事务中均可完整回滚（6 项探针，见 [调研文档](../research/migration_safety_research.md)）
- RENAME 保留原表，即使视图创建失败或迁移逻辑有误，数据仍可从备份恢复
- fail-fast 把"部分迁移 + 删源表"这一不可逆状态变为"部分迁移 + 源表完好"，可安全重跑

## 备选方案

- **COPY 备份再 DROP**：16.8 亿行复制耗时且额外占用 ~46GB 磁盘，成本高于 RENAME
- **迁移前要求用户手工备份**：依赖人工，不可自动化，且无法防止代码路径本身出错
- **逐因子 DROP 源数据**：不可恢复，明确拒绝

## 后果

- ✅ 失败/中断不会丢数据，源表可继续使用
- ✅ 迁移可重跑（幂等），支持分批/断点
- ✅ 备份表命名带时间戳，便于审计与回滚
- ⚠️ 磁盘需预留备份表空间（迁移完成后可 `DROP TABLE factor_data_backup_<ts>` 回收约 46GB）
- ⚠️ 全部成功后才替换视图，迁移过程占用时间更长（可接受）
