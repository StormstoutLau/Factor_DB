# 开发日志 (DEVLOG)

> 记录 Factor_DB 项目的开发历史、关键里程碑与当前状态。按时间倒序。
> 架构决策详见 [ADR 索引](adr/README.md)。

---

## 2026-10（最近）

### 双轨迁移安全化（P0，ADR-0008）
- **背景**: Claude 代码评审 CRITICAL——单因子失败被吞后仍无条件 `DROP TABLE factor_data`，会丢数据
- **落档**: [评审核实分析](reviews/2026-10-claude-audit-analysis.md)
- **spec 流程**: [调研](research/migration_safety_research.md) → [设计](design/migration_safety_design.md) → [实施](implementation/migration_safety_implementation.md) → checklist → TDD
- **实证**: DuckDB 1.5.3 事务化 DDL 6 项探针（INSERT/DROP/RENAME/CREATE VIEW 均可回滚）
- **实现**:
  - 新增 `MigrationError`；资源设置失败直接中止
  - `migrate_factor_data_to_wide()`：幂等短路 + fail-fast + 仅全部成功后替换视图
  - `_migrate_single_factor()`：单因子事务 + 宽/历史行数对账 + 历史完整时只补宽表
  - `_replace_factor_data_with_view()`：`RENAME` 备份 + 单事务建视图，失败回滚兜底
- **测试**: 新增 `TestMigrationSafety` 4 个（失败中止/备份表/部分历史中止/重跑幂等）
- **回归**: `test_migration.py` 11 passed；全量 **245 passed**

### HIGH 1 / 2 / 3 修复（参数化 + 入口校验 + 配置外置）
- **背景**: [Claude 评审核实分析](reviews/2026-10-claude-audit-analysis.md) 中 3 项 HIGH
- **spec 流程**: [调研](research/high_fixes_research.md)（E1–E13 证据锚点 + 幻觉排除） → [设计](design/high_fixes_design.md) → [实施](implementation/high_fixes_implementation.md) → checklist → TDD
- **HIGH 1（SQL 拼接）**:
  - 新增 `utils/sql_utils.py`：`bind`/`bind_in`/`quote_identifier`/`validate_identifier`/`escape_literal`
  - 7 个查询模块全部改为 DuckDB 命名参数；缓存 key 纳入参数
  - `core/schema.py` 迁移/DDL：DML 值参数化 + DDL 标识符白名单转义（实测 `CREATE VIEW` 不支持预处理参数）
  - 不能绑定的位置（`read_parquet` 路径、`LIMIT`、`LAG`、`PERCENTILE_CONT`）走类型校验后插值/字面量转义
- **HIGH 2（README 示例）**: 改为宽矩阵示例；`load_factor_to_wide` 增加长表/空表/非法因子名校验，失败抛 `ValueError`（不再 `return 0`）
- **HIGH 3（硬编码）**: `LoaderConfig` 新增 `memory_limit`/`threads`/`temp_directory`/`preserve_insertion_order`，支持 `FACTOR_DB_MEMORY_LIMIT`/`FACTOR_DB_THREADS`/`FACTOR_DB_TEMP_DIR`；导入脚本支持 `ASHARE_DATA_PATH`，`FACTOR_DIR` 修正为 `financial_factors` 并改 `rglob`；其余辅助脚本路径统一环境变量化（`ASHARE_DATA_PATH` / `ZHUYI_DATA_PATH` / `LEVEL1_DATA_PATH` / `CSMAR_DATA_PATH` / `LEVEL1_PARQUET_DIR`），`convert_level1_parquet` 输出默认改为项目内相对路径
- **测试**: 新增 `test_sql_safety.py` 等 21 个；全量 **266 passed**

---

## 2026-07

### 数据源合并与因子扩充
- **数据源分析**: 对比 `zhuyi`（120 原始财务报表科目）与 `Ashare_data`（101 衍生财务比率），输出 [DATA_COMPARISON_REPORT.md](DATA_COMPARISON_REPORT.md)
- **结论**: 两套数据互补（zhuyi 原始科目 + Ashare 衍生比率），仅 2 个重叠因子
- **文件夹重组**: 
  - `财务指标因子` → `financial_factors`（101 因子，8 类英文子目录）
  - 新增 `raw_fundamentals`（120 原始科目，3 报表 × 10 子类）
  - 新增 `construction_factors`（4 自建因子）、`price_volume_factors`（2 换手率因子）
  - 新增 `raw_tushare`（3 Tushare 原始 API 响应）
- **格式统一**: CSV → pkl（120 个原始科目）
- **因子导入**（Phase 4）: 124 个新因子导入 `factor_data`
  - 因子数 117 → **241**
  - 行数 8.7 亿 → **16.8 亿**
  - 去重 2 个（`ebit`、`current_ratio`），耗时 20 分钟

### PIT NULL 空壳行修复（ADR-0006）
- 发现 `daily_prices` 83% 行价格字段为 NULL（adj_factor 增量更新导致）
- `_build_pit_subquery()` 增加 `non_null_columns` 参数
- NaN 比例 ~100% → 0.0%，240 测试全部通过

### GitHub 仓库与文档
- 创建 .gitignore、初始化 Git、推送到 `StormstoutLau/Factor_DB`
- 英文 README.md（主）+ 中文 README_zh.md

---

## 2026-06

### 宽表改造 + Level 1 Parquet（方案 A）
- **双轨架构**（ADR-0004）: `factor_wide`（宽表）+ `factor_history`（长表）TDD 实现
  - 31 测试通过（test_wide_loader.py:9, test_wide_query.py:10, test_level1_parquet.py:12）
- **Phase 3 迁移**: `migrate_factor_data_to_wide()` 实现，6 测试通过
- **Level 1 Parquet**（ADR-0005）: Hive 分区，Feather → Parquet 转换（2,250 分区）

### 数据库优化
- Parquet Export → Rebuild → Import：80.6 GiB → **46.3 GiB**（-42.6%）
- 行组 7,832 → 7,094

### 因子数据导入
- Phase 1.0: OHLCV → `daily_prices`（43,226,352 行）
- Phase 1.5: 101/101 财务因子 → `factor_data`
- Phase 1.6: 4/4 特殊因子（stock_return/suspend/df + barra_tl 13 子因子）
- 总因子数 117

---

## 关键里程碑汇总

| 时间 | 里程碑 | 状态 |
|------|--------|------|
| 2026-06 | DuckDB 引擎选型（ADR-0001） | ✅ |
| 2026-06 | EAV 长表模型（ADR-0002） | ✅（后被 0004 修订） |
| 2026-06 | PIT 版本管理（ADR-0003） | ✅ |
| 2026-06 | 宽表+长表双轨（ADR-0004） | ⚠️ 代码完成，未迁移 |
| 2026-06 | Level 1 Parquet（ADR-0005） | ✅ |
| 2026-07 | NULL 空壳行修复（ADR-0006） | ✅ |
| 2026-07 | 数据源合并（ADR-0007） | ✅ |
| 2026-10 | 双轨迁移安全化（ADR-0008） | ✅ 代码完成，生产迁移待执行 |

---

## 当前待办（Backlog）

- **[P0]** `factor_data` 主键缺失（需 >26GB 内存机器）
- **[P0]** 双轨迁移未在生产库执行
- **[P2]** Level 1 历史导入未完成（`level1_snapshots` 0 行）
- **[P2]** CSMAR 因子导入
- 文档体系规范化（本日志 + spec + adr）
