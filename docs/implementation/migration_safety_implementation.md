# 迁移安全化实施文档

> 依据：[调研](../research/migration_safety_research.md)、[设计](../design/migration_safety_design.md)
> 日期：2026-10

---

## 1. 分步实施计划

| 步骤 | 内容 | 验证 |
|------|------|------|
| S1 | 新增 `MigrationError` 异常类 | 测试导入 |
| S2 | `_configure_migration` 失败改为 raise | 单测（成功路径） |
| S3 | `_migrate_single_factor` 重写（事务 + 对账 + 幂等分支 + raise） | 新增 4 个测试 |
| S4 | `migrate_factor_data_to_wide` 重写（视图短路 + fail-fast + raise） | 新增测试 |
| S5 | `_replace_factor_data_with_view` 重写（RENAME 备份 + 单事务） | 新增测试 |
| S6 | 全量回归 | `pytest` |

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 本机 DuckDB 1.5.3；事务化 DDL 与 `ALTER TABLE ... RENAME TO` 均实测可用；`pyproject.toml` 无需变更 |
| **依赖** | 无新增；仅补 `from datetime import datetime`（标准库） |
| **兼容** | 成功路径签名/返回值不变；失败路径由"返回 0"改为抛 `MigrationError`（有意变更，生产无调用方） |
| **接口** | 新增模块级 `MigrationError(RuntimeError)`；`_table_type(conn, name)` 内部辅助 |
| **签名** | 三个公开/内部方法签名不变；异常为新增导出 |
| **低效操作** | 不做行级 INSERT；不做不必要的全表扫描（每因子 COUNT 走 `idx_factor_name_date`）；事务只包裹写入 |
| **内存** | 沿用 `_configure_migration`（24GB/4 threads）；SET 失败即中止 |
| **数据一致性** | I1–I6 六条不变量见设计文档 §2 |

## 3. 涉及修改的文件

- `core/schema.py`
  - 新增 `MigrationError`
  - 新增 `_table_type(conn, name)` 辅助
  - 重写 `_configure_migration` / `_migrate_single_factor` / `migrate_factor_data_to_wide` / `_replace_factor_data_with_view`
- `tests/test_migration.py` 新增 `TestMigrationSafety`（4 个测试）

## 4. 接口 / SQL 变更（Before → After）

### 4.1 `_migrate_single_factor` 关键变化

**Before**：异常 → `logger.error` + `return 0`；wide/history 两条自动提交语句。

**After**（伪代码）：
```python
src_rows = COUNT(factor_data WHERE factor=f AND value IS NOT NULL)
src_keys = COUNT(DISTINCT trade_date, stock_code ...)
hist = COUNT(factor_history WHERE factor=f)
if src_rows == 0: return 0
if hist > 0:
    if hist < src_rows: raise MigrationError
    upsert wide; verify wide >= src_keys; return wide_count   # 不重复写历史
BEGIN TRANSACTION
  wide upsert
  verify wide_count == src_keys              # I3
  history insert (全量版本)
  verify hist_count == src_rows              # I3
COMMIT
# 任一失败：ROLLBACK + raise MigrationError
```

### 4.2 `migrate_factor_data_to_wide` 关键变化

**Before**：`_replace_factor_data_with_view()` 无条件执行。

**After**：
```python
if _is_view('factor_data'): return stats      # 幂等短路
for factor: try 迁移 except MigrationError → failures[factor]=...
if failures: raise MigrationError(...)        # 源表未动
_replace_factor_data_with_view()              # 仅在全部成功后
```

### 4.3 `_replace_factor_data_with_view` 关键变化

**Before**：`DROP TABLE IF EXISTS factor_data` → `CREATE VIEW`。

**After**：
```python
BEGIN TRANSACTION
  ALTER TABLE factor_data RENAME TO factor_data_backup_<YYYYmmdd_HHMMSS>
  CREATE VIEW factor_data AS <241 列 UNION ALL>
COMMIT
# 失败：ROLLBACK；兜底若原名缺失则把备份 RENAME 回来；raise
```

## 5. 测试计划（TDD）

| 测试 | 验证不变量 |
|------|-----------|
| `test_migration_aborts_on_factor_failure_keeps_source` | I1 |
| `test_migration_creates_backup_table_on_success` | I4 |
| `test_migration_partial_history_aborts` | I3（0<hist<src 人工介入） |
| `test_migration_rerun_is_idempotent` | I5 |

现有 7 个迁移测试必须保持通过。

## 6. 执行前条件（生产迁移时）

1. **磁盘**：`factor_data`（46GB）+ `factor_wide`（~8GB）+ `factor_history`（~8GB）+ 备份 RENAME（元数据，几乎不占额外空间）→ 预留 >100GB
2. **备份**：迁移前仍建议文件级备份一次（双保险）
3. **耗时**：16.8 亿行逐因子事务，预计数小时，后台执行
4. **迁移后**：确认视图与查询正常后可 `DROP TABLE factor_data_backup_<ts>`
