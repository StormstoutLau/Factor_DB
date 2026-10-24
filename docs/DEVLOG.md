# 开发日志 (DEVLOG)

> 记录 Factor_DB 项目的开发历史、关键里程碑与当前状态。按时间倒序。
> 架构决策详见 [ADR 索引](adr/README.md)。

---

## 2026-10（最近）

### Phase 2 Level 1 Parquet 分区完成（2026-10-23）
- **结果**: `data/level1_parquet/` 2,250 个 Hive 分区（`trade_date=YYYY-MM-DD/data.parquet`），共 **2,107,016,487 行**、32.84 GB
- **校验**: 源 Feather 文件 2,250 个 → 分区 2,250 个，缺失 0、多余 0；抽样分区日期与目录名一致（2014-01-02 / 2018-06-01 / 2023-03-30）
- **方式**: `Level1ParquetConverter.convert_directory()` 增量转换（已存在分区自动跳过）
- **说明**: `level1_snapshots` DuckDB 表保持 0 行（ADR-0005：分钟数据不进入 DuckDB 表）

### Phase 3 CSMAR 因子导入完成（2026-10-24）
- **结果**: 313 个 zip（782 个源 xlsx），`factor_history` 新增 **35.20 亿行**（1,683,530,224 → **5,203,062,078**）；`factor_info` 登记 **5,439** 个 CSMAR 因子（57 个基表）
- **分类分布（Top）**: 输赢信息表 4,144 / 长期收益排名表(月) 535 / 短期收益排名表(周) 184 / 惯性策略 86 / 羊群效应 57 / IML 43 / 买卖不平衡 40
- **导入方式**: `CSMARLoader.load_directory()` + `history_only=True`；分片文件（1,000,000 行切分）按基名合并；整文件单条 `UNPIVOT` 追加；`csmar_import_log` 断点续跑
- **命名规则**: 个股/指数/行业级维度编码进因子名（`_MT_P9706_Q_30_C_0_F_1`）；市场级维度编码进伪实体（`MKT_MT_P9709`）；仅当同一 (日期, 实体) 下维度确实产生多行时才启用
- **校验**: 导入日志 782 源 / 0 失败；命名非法 0、最长 54 字符、描述冲突 0；宽表不变（9,307,121 行 / 241 因子）；抽样因子行数、日期区间、实体数一致
- **问题修复**:
  - `HF_VPIN` 的 `N`（篮子数 50/8）未识别为维度 → `keep='last'` 去重丢弃 50%（1,632 万行）；新增 `STEM_DIMENSIONS` 表级维度映射并定向重导 13 个 zip（9,798 万行 / 18 个因子，含 N=46–49 的极少数边界行）
  - `factor_history` 由迁移重建时无 `loaded_at` 默认值，批量 INSERT 未显式写入 → 34.9 亿行 `loaded_at` 为 NULL，PIT 子查询 `(…, loaded_at) IN (…)` 恒假，CSMAR 因子查询返回 0 行；改为显式 `CURRENT_TIMESTAMP`（`BaseLoader` 与 CSMAR 批量路径），并回填历史 NULL 行
- **耗时**: 全量约 7.5 小时（10-23 18:56 → 10-24 02:24），受 F: 机械硬盘 I/O 限制

### 生产库双轨迁移完成（2026-10-23，ADR-0004）
- **结果**: `factor_data` 1,683,530,224 行 / 241 因子 → `factor_wide` 9,307,121 行（241 列 + PK）、`factor_history` 1,683,530,224 行；`factor_data` 已替换为视图，原表备份为 `factor_data_backup_20261023_162924`
- **执行方式**: 弃用逐因子 upsert 迁移（`migrate_factor_data_to_wide`），改用两阶段重建
  - Phase A：单次顺序扫描 `factor_data` → `factor_history`（49.9 分钟）
  - Phase B：按 24 因子分块 `arg_max(CASE WHEN factor_name=... THEN factor_value END, loaded_at)`（11 块）→ 4 轮 `FULL JOIN` 合并
  - Phase C：创建 `factor_wide` + PK → 抽样对账 → RENAME 备份 → 动态视图
- **关键教训**:
  - 逐因子 `INSERT ... ON CONFLICT DO UPDATE` 在 241 列宽表上产生大量行版本，吞吐从 1,171K rows/min 退化到 155K rows/min，预计需 130+ 小时，实测 18.3 小时仅完成 56/241，已中止
  - 分块 pivot 必须带因子过滤：`arg_max` 缺少 `CASE WHEN factor_name=...` 会把整个分块聚合成同一列（首次尝试被行数对账拦截）
  - `FILTER (WHERE ...)` 聚合实测退化为串行（0.25 核），改用 `CASE WHEN` 后可并行
  - `ALTER TABLE factor_data RENAME` 会被 `idx_factor_name_date` 索引依赖阻止，需先 `DROP INDEX`
  - F: 为 2TB 机械硬盘（约 48 MB/s），分块扫描受 I/O 限制（18.8 → 75 分钟/块）
- **校验**: 视图/宽表/历史表对象类型正确；241 个因子列；抽样因子行数全量一致（如 LT_debt_ratio 7,418,391、working_capital 8,545,471）；视图与宽表区间计数一致，均值差异仅为浮点求和顺序
- **代码化**: 新增 `SchemaManager.rebuild_factor_data_dual_track(chunk_size, sample_size, temp_directory)`；`_replace_factor_data_with_view` 自动删除 `factor_data` 索引以解除 RENAME 依赖；新增 `test_rebuild_dual_track.py` 5 个用例，全量 **281 passed**

### 因子写路径统一（迁移前置，ADR-0004）
- **背景**: 迁移后 `factor_data` 变为视图（不可写），daily/csmar/phase1 三条写路径会失效；`factor_wide` 已存在时 `create_factor_wide` 的 `IF NOT EXISTS` 不补列
- **spec 流程**: [调研](research/factor_write_path_research.md)（C1–C8 代码实证 + E1/E2 DuckDB 探针） → [设计](design/factor_write_path_design.md)（D1–D9、I1–I5） → [实施](implementation/factor_write_path_implementation.md) → checklist → TDD
- **实证**: `INSERT INTO view` 报 `CatalogException`；`UNPIVOT COLUMNS(* EXCLUDE ...)` 视图在 `ADD COLUMN` 后自动包含新列（静态 `UNION ALL` 视图不会）
- **实现**:
  - `SchemaManager.object_type()` 公有化；迁移 Step 2 后补齐 `factor_wide` 缺失列
  - `BaseLoader.write_factor_long()` / `_write_factor_from_temp()` / `_ensure_factor_tables()`：单事务双写 `factor_history` + `factor_wide`（`ON CONFLICT` 更新），`factor_data` 仍为实体表时附加 legacy 写入
  - `DailyLoader` 因子分支改走统一写路径，`load_factor_to_wide` 复用同一事务，删除死代码 `_import_to_factor_table`
  - `CSMARLoader` 财务/市场因子改走统一写路径（按主键去重），失败改为抛异常
  - Phase 1 barra 脚本按 `factor_name` 分组调用 `write_factor_long`
  - 兼容视图改为动态 `UNPIVOT COLUMNS(* EXCLUDE (trade_date, stock_code, loaded_at))`
- **测试**: 新增 `test_factor_write_path.py` 10 个；定向 59 passed；全量 **276 passed**

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
