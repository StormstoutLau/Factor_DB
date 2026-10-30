# 开发日志 (DEVLOG)

> 记录 Factor_DB 项目的开发历史、关键里程碑与当前状态。按时间倒序。
> 架构决策详见 [ADR 索引](adr/README.md)。

---

## 2026-10（最近）

### 工程化基线 + IC 衰减分析 + 根目录清理（2026-10-30）
- **CI**: 新增 [.github/workflows/ci.yml](../.github/workflows/ci.yml)（push/PR → `ruff check .` + `pytest`，Ubuntu / Python 3.12 / `pip install -e ".[dev]"`）
- **静态检查**: `pyproject.toml` 新增 dev extras（pytest/ruff）与 ruff 保守规则集（`E4,E7,E9,F`）；`ruff --fix` 清理 67 项未使用导入/空 f-string，人工处理 6 个 F841（DuckDB `FROM df` 引用，加 `# noqa`）
- **真实缺陷**: 新增 CLI 测试立即暴露 `cmd_query` / `cmd_list_factors` 调用 `c.fetchdf(sql)` 在 DuckDB 1.x 报 `TypeError`（两条命令生产不可用）→ 改为 `execute(...).fetchdf()`；`main(argv=None)` 可测试化
- **测试**: 新增 `tests/test_cli.py`（5）与 `tests/test_ic_decay.py`（4）；全量 **319 passed**（110 s），`ruff` 零告警
- **IC 衰减**: 新增 `FactorQuery.get_ic_decay`（复用 `get_ic_series`，返回 `forward_return_days/periods/ic_mean/ic_std/ic_ir`）与 CLI `factor-db ic-decay`
- **生产验证**: `LT_debt_ratio` 2024Q1 五个 horizon，IC 在 5 日附近达峰（0.0336），57→38 期，总耗时约 1.5 s
- **清理**: 删除根目录 `factor_data_export.parquet`（0.47 GB，已被 `.gitignore` 忽略）
- **文档**: [调研](../docs/research/engineering_baseline_research.md) / [设计](../docs/design/engineering_baseline_design.md) / [实施](../docs/implementation/engineering_baseline_implementation.md)；[IC 衰减调研](../docs/research/ic_decay_research.md) / [设计](../docs/design/ic_decay_design.md) / [实施](../docs/implementation/ic_decay_implementation.md)；checklist 第十三节

### 生产库端到端验收 + IC 查询路径修复（2026-10-30）
- **背景**: 306 个测试全部基于临时库，46 GiB 生产库从未被测试路径覆盖（[调研](research/prod_e2e_validation_research.md)）
- **真实缺陷**: `get_ic_series` 第一轮走 `factor_data`（241 列 UNPIVOT 视图）OOM；改用 `factor_history` 后第二轮仍 OOM，隔离定位到 `daily_prices` 自连接 + `MIN(trade_date)` 相关子查询（36.69 s 后 `Allocation failure`）
- **修复**（ADR-0011）: IC 数据源固定 `factor_history`（EAV，按 `factor_name` 物理排序）；前瞻收益改 `LEAD(close, days) OVER (PARTITION BY stock_code ORDER BY trade_date)`；`forward_return_days` 参数生效
- **红→绿**: `test_ic_series_reads_history_not_view`（红灯 `CatalogException: factor_data does not exist`）→ 定向 `test_wide_query.py` 12 passed
- **验收脚本**: 新增 `scripts/validate_production.py`（只读，S1 结构/行数、S2 宽表矩阵、S3 history+PIT、S4 价格、S5 IC；JSON+Markdown 报告，硬性/软性判定分离）
- **测试**: 新增 `tests/test_validate_production.py` 4 个（正常/缺表/报告渲染/IC 区间越界降级）
- **生产实跑**: `E2E_VALIDATION=OK`，总耗时 2.44 s
  - S1 0.10 s（18 对象；wide 9,307,121 / history 5,203,062,078 / daily 43,226,352）
  - S2 0.34 s（8 因子 × 373,029 行，非空率 67.1%）
  - S3 1.29 s（`STK_MKT_DGTWBREAKPOINTS_MomMax` 250 行；PIT 323,445 行一致）
  - S4 0.54 s（`get_daily` 464 行；`get_price_matrix` 58×2）
  - S5 0.17 s（`LT_debt_ratio` 57 期，IC 均值 0.017640）
- **报告**: [JSON](reports/prod_e2e_validation_2026-10-30.json) / [Markdown](reports/prod_e2e_validation_2026-10-30.md)
- **文档**: [调研](research/prod_e2e_validation_research.md) / [设计](design/prod_e2e_validation_design.md) / [实施](implementation/prod_e2e_validation_implementation.md) / [ADR-0011](adr/0011-ic-query-path-fix.md) / checklist 第十二节；ARCHITECTURE §4 增补 IC 路径约束

### P2/P3 评审遗留项闭环（2026-10-30）
- **背景**: [Claude 评审核实分析](reviews/2026-10-claude-audit-analysis.md) §五 中 MEDIUM 1/2（缓存非真 LRU、无写后失效、单例 `close_on_exit` 冲突）与 LOW 4 项此前仅登记未修复
- **关键事实修正**: loader 与 `SchemaManager` 的写操作经 `with self.conn` 拿到原生 duckdb 连接，单靠 `DuckDBConnection.execute()` 识别写语句会漏掉主写入路径
- **缓存**: `QueryCache` 改 `OrderedDict` 真 LRU（命中 `move_to_end`、淘汰 `popitem(last=False)`）；新增 `utils/data_version.py` 全局版本号，写连接 `__exit__` 与写语句 `execute` 递增版本，版本不符即 miss（TTL 保留）
- **单例**: `close_on_exit` 改 `Optional[bool]`（`None` 沿用已有配置，显式传值可更新）；`__del__` 仅关闭已脱离 `_instances` 的实例，避免销毁共享连接
- **compact**: `compact_table` 改为同谓词 `SELECT COUNT(*)` + `DELETE`，返回实际删除行数（避免 `DELETE ... RETURNING` 在百万行级物化结果集）
- **打包**: 新增 MIT `LICENSE`；`pyproject` 补 `license` 与 `[project.scripts] factor-db = "Factor_DB.cli:main"`；删除 0 字节 `tests/test_pivot_optimization.py`
- **测试**: 红 7 failed → 绿；定向 37 passed，全量 **305 passed**（原 295）
- **文档**: [调研](research/p2_p3_closure_research.md) / [设计](design/p2_p3_closure_design.md) / [实施](implementation/p2_p3_closure_implementation.md) / [ADR-0010](adr/0010-cache-invalidation-singleton-lifecycle.md) / checklist 第十一节；ARCHITECTURE 缓存表述与测试数同步

### DB 压缩重建与备份表清理（2026-10-26）
- **背景**: `factor_history` 重排后 DB 93.43 GiB，其中 free blocks 35.3 GiB；另存 1,683,530,224 行的迁移备份表
- **对账门**: 241 个宽表因子，备份表与 `factor_history` 行数逐一比对 → **1,683,530,224 = 1,683,530,224，0 差异**
- **删备份**: `DROP TABLE factor_data_backup_20261023_162924` + `CHECKPOINT`；used_blocks 238,106 → 190,667（释放 11.6 GiB 逻辑空间）
- **压缩**: `COPY FROM DATABASE factor_db TO newdb`，目标先落 **D: NVMe**；耗时 **7,104 秒（118.4 分钟）**，新库 **46.19 GiB**、`used_blocks=189,187 / free_blocks=0`
- **校验**: `factor_history` 5,203,062,078 行、`loaded_at` 全非空；`factor_wide` 9,307,121 行 / 241 因子；5 个抽样因子行数与日期区间一致；首末行保持 `(factor_name, trade_date, stock_code)` 顺序；`factor_data` 视图可用
- **吞吐实测**: 3–5 MB/s（有序写入单线程 + HDD 列读取）；磁盘未饱和（F: idle 80–94%，queue 0.1）；调研阶段“30–90 分钟”估算被证伪
- **切换**: 管理员创建符号链接 `F:\Coding\Factor_DB\factor_db.duckdb` → `D:\Factor_DB\factor_db_compacted.duckdb`（创建文件符号链接需要管理员权限）；旧库改名 `factor_db_pre_compact_20261026.duckdb`（93.43 GiB）保留作回滚
- **性能（NVMe）**: 跨文件单因子 2024Q1 冷 **0.92s** / 热 0.23s（原 HDD 冷 21–25s、热 0.45s）；全量单因子 0.39s；VPIN 冷 0.04s；`pragma_database_size` 46.1 GiB
- **运行参数**: 通过符号链接打开时，DuckDB 将 `temp_directory` 解析到真实路径 `D:\Factor_DB\factor_db_compacted.duckdb.tmp`（溢写在 NVMe）；目录名（catalog）为 `factor_db_compacted`
- **清理**: 删除 killed 进程遗留的 `factor_db.duckdb.tmp`（约 26 GB 溢写文件）；F: 剩余 1,458.7 GB
- **删除前全量核对（删除归档前执行，耗时约 17 分钟）**:
  - 结构：18 个对象（表/视图）、380 列（名称/类型/顺序/可空性）、33 个约束、19 个索引、`factor_data` 视图 SQL 全部一致
  - 行数：18 张表逐一相等（`factor_history` 5,203,062,078 / `factor_wide` 9,307,121 / `daily_prices` 43,226,352 等）
  - 内容：12 张小表双向 `EXCEPT ALL` 均为 0；`factor_history` 逐因子行数（5,680 因子）与 `SUM(hash(trade_date, stock_code, factor_value, loaded_at))` 校验和全部一致；`factor_wide` 241 列非空计数一致；`daily_prices` 行数/股票数/`close`/`volume` 求和/日期范围一致
  - 浮点噪声辨析：抽样因子 `UTR_turnover` 的并行 `SUM` 相对差异 0.35%，行级 `EXCEPT ALL` 双向为 0、串行求和完全相同（-7.71171926317038e-09）、分布（无 NULL/NaN/Inf）一致 → 判定为并行归约顺序噪声，非数据差异
- **归档删除**: 核对通过后删除 `factor_db_pre_compact_20261026.duckdb`；删除默认进入回收站（空间未释放），定位 `$RSVZIVN.duckdb`/`$ISVZIVN.duckdb` 配对永久删除，F: 剩余 1,551.1 GB

### factor_history 全量物理重排（2026-10-25）
- **背景**: 迁移 + CSMAR 导入后 `factor_history` 物理顺序等于导入顺序（按文件追加），跨年份多文件因子（如输赢信息表）单因子区间查询需 36.5 秒，zonemap 难以跳过行组
- **方案**: CTAS 重排 `CREATE TABLE factor_history_sorted AS SELECT * FROM factor_history ORDER BY factor_name, trade_date, stock_code`，设置 `preserve_insertion_order=true`
  - 连接配置：`threads=4`、`memory_limit='24GB'`、`temp_directory='F:\factor_db_tmp'`（1,438 GB 可用）
  - 校验：行数与 `loaded_at` 计数对账 → 三因子抽样（1,039 / 741 / 493,127 行及其日期区间）前后一致 → 首末行顺序检查 → `RENAME` 交换 → `DROP` 旧表 → `CHECKPOINT`
- **结果**: 重排 5,203,062,078 行，CTAS 耗时 **69,082 秒（约 19.2 小时）**；首行 `BF_CRASHRISK_CRASH_Cmdeq / 1991-01-01 / 000001`、末行 `working_capital / 2026-01-27 / 920992`（升序正确）；DB 文件 69.69 → **93.43 GB**（旧表空间仅部分回收）
- **性能（重排后，`threads=4`，HDD 存储）**:

  | 场景 | 重排前 | 重排后（热缓存） | 重排后（冷缓存，首次访问） |
  |------|--------|------------------|------------------------------|
  | `get_factor` 跨文件因子 2024Q1（3,822 行） | 36.5s | **0.45s** | 21.0s |
  | `get_factor` 跨文件因子全量（211,515 行） | 未测 | 0.63s | 9.0s |
  | `get_factor` VPIN 2024Q1（314,821 行） | 5.0s | 1.40s | 7.5s |
  | `get_cross_section`（5,419 行） | 0.1s | 0.05s | 0.24s |
  | `get_factor_matrix`（103,096 × 4） | 6.8s | 2.6–3.0s | 7.0s |
  | `get_factor_stats` | 0.7s | 0.05s | 0.04s |
  | `EXPLAIN ANALYZE` 单因子计数 | — | 0.55s | 25.3s |

- **关键教训**:
  - 首次尝试把 `temp_directory` 设在 D:（NVMe，200GB），外部排序中间数据达 **179.9 GiB** 撑爆磁盘 → `OutOfMemoryException: failed to offload data block`；改到 F: 后成功
  - DuckDB 1.5.3 **不支持多 temp_directory**：`D:\...;F:\...` 与 `D:\...,F:\...` 均被当作单一路径，报 `IOException: Failed to create directory`
  - 排序收益集中在热缓存：zonemap 可跳过绝大多数行组，单因子查询 ~80x；但 HDD 上冷启动仍需 20+ 秒（需读取散落的行组元数据）
  - `DROP TABLE` + `CHECKPOINT` 只回收少量空间，DB 膨胀约 24 GB 死空间；如需压缩需重建/复制数据库
- **说明**: 写入路径已配置 `preserve_insertion_order=true`；后续任何 `factor_history` 重建/重导都必须保持 `(factor_name, trade_date, stock_code)` 顺序，否则查询性能回退

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
| 2026-06 | 宽表+长表双轨（ADR-0004） | ✅ 已迁移（2026-10-23） |
| 2026-06 | Level 1 Parquet（ADR-0005） | ✅ |
| 2026-07 | NULL 空壳行修复（ADR-0006） | ✅ |
| 2026-07 | 数据源合并（ADR-0007） | ✅ |
| 2026-10 | 双轨迁移安全化（ADR-0008） | ✅ 已接受 |
| 2026-10 | CSMAR 批量因子仅写长表（ADR-0009） | ✅ 已接受 |
| 2026-10 | 缓存失效与单例生命周期（ADR-0010） | ✅ 已接受 |
| 2026-10 | IC 查询路径修复 + 生产库 E2E 验收（ADR-0011） | ✅ 已接受 |

---

## 当前待办（Backlog）

- ~~**[P0]** `factor_data` 主键缺失~~ → 2026-10-23 双轨迁移完成（ADR-0004/0008）
- ~~**[P0]** 双轨迁移未在生产库执行~~ → 2026-10-23 完成（9,307,121 行宽表）
- ~~**[P2]** Level 1 历史导入未完成~~ → 2026-10-23 完成（2,250 分区 / 21.07 亿行）
- ~~**[P2]** CSMAR 因子导入~~ → 2026-10-24 完成（5,439 因子 / 35.20 亿行）
- ~~**[P2/P3]** Claude 评审遗留（缓存 LRU/失效、单例生命周期、LICENSE、scripts、空测试、compact 返回值）~~ → 2026-10-30 闭环（ADR-0010）
- 文档体系规范化（本日志 + spec + adr）→ 已完成
