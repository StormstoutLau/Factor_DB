# 迁移安全化设计文档

> 依据：[调研文档](../research/migration_safety_research.md)
> 前置：[双轨迁移设计](wide_migration_design.md)（arg_max 内存安全）
> 日期：2026-10

---

## 1. 目标与非目标

### 目标（P0）
1. 任何因子迁移失败 → **中止迁移，源表 `factor_data` 不动**
2. 每因子宽表 + 历史表原子提交
3. 替换前逐因子行数对账
4. 替换 = `RENAME` 备份 + `CREATE VIEW`，单事务，失败回滚
5. 迁移重试幂等（不重复写历史）
6. 资源配置失败 → 中止（不静默降级）

### 非目标
- 不改查询层（P1 参数化另批处理）
- 不改 `factor_wide` 表结构
- 不清理历史备份表（运维动作）

---

## 2. 安全不变量（Invariants）

| 编号 | 不变量 | 保障手段 |
|------|--------|---------|
| I1 | 因子失败 ⇒ 源表不变（仍是 TABLE，行数不变） | 失败即 raise，Step 5 不执行 |
| I2 | 单因子的 wide + history 要么都成功要么都失败 | `BEGIN ... COMMIT/ROLLBACK` |
| I3 | 迁移成功的因子满足：history 行数 == 源非空行数；wide 非空值数 == 源 distinct 键数 | 事务内校验，不符即 ROLLBACK + raise |
| I4 | 替换失败 ⇒ 表/视图状态可恢复 | `RENAME` + `CREATE VIEW` 单事务；失败 `ROLLBACK`，附显式改回兜底 |
| I5 | 重跑幂等 | 已为视图直接返回；`history` 已有完整数据时跳过历史写入 |
| I6 | 配置失败可见 | `_configure_migration` 失败 raise `MigrationError` |

---

## 3. 流程设计

```
migrate_factor_data_to_wide(batch_size)
 ├─ 0. factor_data 已是视图？ → 直接返回（I5）
 ├─ 1. 取因子列表
 ├─ 2/3. 建 factor_wide / factor_history（IF NOT EXISTS）
 ├─ 4. 逐因子 _migrate_single_factor（fail-fast）
 │     ├─ 取 src_rows（非空源行数）/ src_keys（distinct 键数）
 │     ├─ src_rows == 0 → 返回 0（全空因子，非失败）
 │     ├─ history 已有该因子：
 │     │    ├─ < src_rows → raise（部分历史异常，人工介入）
 │     │    └─ ≥ src_rows → 仅 upsert 宽表 + 校验，不重复写历史
 │     └─ 全新迁移：BEGIN → wide upsert → 校验 → history insert → 校验 → COMMIT
 ├─ 失败收集：任一失败 → raise MigrationError（源表未动，I1）
 └─ 5. _replace_factor_data_with_view()
        BEGIN → RENAME factor_data → factor_data_backup_<ts>
              → CREATE VIEW factor_data → COMMIT
        失败 → ROLLBACK + 兜底改回 → raise
```

## 4. 接口契约

```python
class MigrationError(RuntimeError): ...

class SchemaManager:
    def migrate_factor_data_to_wide(self, batch_size: int = 5) -> dict:
        """成功：{factor: rows}；失败：raise MigrationError（源表不变）"""

    def _migrate_single_factor(self, factor_name: str) -> int:
        """成功：wide 非空行数；全空因子：0；失败：raise MigrationError"""

    def _replace_factor_data_with_view(self) -> bool:
        """成功：True（原表已 RENAME 为备份）；失败：raise（已回滚）"""

    def _configure_migration(self, conn) -> None:
        """SET 失败：raise MigrationError（I6）"""
```

### 兼容性说明（行为变更，有意为之）
- 失败路径：静默返回 0 → **抛异常**。调用方（仅测试）无生产依赖
- 成功路径签名与返回值不变

## 5. 风险与权衡

| 风险 | 缓解 |
|------|------|
| 备份表长期占用磁盘（46GB 级） | 文档提示迁移后确认无误可 `DROP` 备份 |
| `hist ≥ src` 跳过会掩盖历史重复 | 历史目前为空，无存量重复；异常值（0<hist<src）强制人工介入 |
| 16.8 亿行下每因子事务开销 | 每因子约 700 万行，单事务可控；事务仅包裹写入不做全表操作 |
| 视图 UNION ALL 241 列 | 事务内创建，失败自动回滚（探针 5 验证） |

## 6. Review 记录（排除幻觉）

- [x] 事务化 DDL 本机实证（DuckDB 1.5.3，6 项探针）
- [x] 官方文档佐证 ALTER TABLE 事务语义
- [x] 调用方已核查：仅 `tests/test_migration.py` 使用（无生产脚本）
- [x] 现有测试兼容性已推演（`test_upsert_same_factor_no_duplicate` 走 hist≥src 跳过分支）
- [x] 未臆造 DuckDB 语法（BEGIN TRANSACTION / ROLLBACK / ALTER TABLE RENAME TO 均实测）
