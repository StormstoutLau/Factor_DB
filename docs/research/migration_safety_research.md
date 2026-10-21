# 迁移安全化调研文档

> 主题：`factor_data` → `factor_wide` + `factor_history` 迁移的失败安全（P0）
> 来源：[Claude 评审核实分析](../reviews/2026-10-claude-audit-analysis.md) CRITICAL 项
> 日期：2026-10

---

## 1. 问题定义

现有迁移流程的三个数据安全缺陷：

1. `_migrate_single_factor` 异常被吞、返回 0，调用方不检查返回值
2. Step 5 无条件执行 `_replace_factor_data_with_view()`，其中先 `DROP TABLE factor_data` 再建视图
3. 宽表 upsert 与历史表插入为两条自动提交语句，可能只成功一半

任一因子迁移失败（磁盘满/内存不足）→ 源长表被删除、数据不可恢复。

## 2. 关键技术问题：DuckDB 是否支持事务化 DDL？

方案的根基。做本机实证探针（DuckDB 1.5.3，临时库）：

| 探针 | 操作 | 结果 | 结论 |
|------|------|------|------|
| 1 | `BEGIN; INSERT; ROLLBACK` | 行数未变 | INSERT 可回滚 |
| 2 | `BEGIN; DROP TABLE t; ROLLBACK` | 表及数据恢复 | **DROP 可回滚** |
| 3 | `BEGIN; ALTER TABLE t RENAME TO t2; ROLLBACK` | `t=1, t2=0` | **RENAME 可回滚** |
| 4 | `BEGIN; RENAME; CREATE VIEW 同名; COMMIT` | 视图生效 | 替换路径可行 |
| 5 | `BEGIN; CREATE VIEW 引用不存在表; ROLLBACK` | 无残留视图 | 失败自动回滚 |
| 6 | `BEGIN; RENAME 备份→原名; ROLLBACK` | 恢复 | 兜底改回可行 |

官方文档佐证：
- [ALTER TABLE](https://duckdb.org/docs/lts/sql/statements/alter_table)：*"All the changes made by ALTER TABLE fully respect the transactional semantics, i.e., ... can be fully reverted through a rollback."*
- [Transaction Management](https://www.duckdb.org/docs/lts/sql/statements/transactions)：ACID 事务，`ROLLBACK` 丢弃全部变更
- [Changing Data with Confidence and ACID](https://www.duckdb.org/2024/09/25/changing-data-with-confidence-and-acid)：schema changes 也在事务内

## 3. 候选方案对比

| 方案 | 原理 | 优点 | 缺点 | 采用 |
|------|------|------|------|------|
| **A. 事务化迁移 + RENAME 备份** | 每因子单事务；替换用 `RENAME` + 单事务 | 失败零损失；备份可回溯；无额外磁盘峰值（RENAME 是元数据操作） | 备份表需后续人工清理 | ✅ |
| B. 文件级复制备份 | 迁移前 `Copy-Item factor_db.duckdb` | 最简单 | 46GB×2 磁盘压力；复制期间不可写 | ❌ |
| C. 仅 RENAME 不做事务 | 无事务，失败后手工恢复 | 改动小 | 视图创建失败时窗口期仍有损 | ❌ |
| D. 先写新库再切换 | 双库并行 | 最安全 | 需双倍磁盘 + 切换成本高 | ❌ |

## 4. 结论

事务化 DDL 已实证可用，方案 A 可行：
- **每因子一个事务**：宽表 upsert + 历史插入 + 行数校验 → 一次 COMMIT
- **替换单事务**：`RENAME factor_data → backup` + `CREATE VIEW factor_data` → 一次 COMMIT
- 任一因子失败即中止，源表保持原样（表状态，不是视图）
- 配置 SET 失败改为中止（不再静默降级）

## 5. 参考文献

- [DuckDB ALTER TABLE](https://duckdb.org/docs/lts/sql/statements/alter_table)
- [DuckDB Transaction Management](https://www.duckdb.org/docs/lts/sql/statements/transactions)
- [DuckDB ACID 博客](https://www.duckdb.org/2024/09/25/changing-data-with-confidence-and-acid)
- 本机探针：`scripts/_probe_tx.py` 运行结果（DuckDB 1.5.3）
