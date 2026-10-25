# 审查验收 Checklist

> 本文件是开发流程规范（[DEVELOPMENT_PROCESS.md](DEVELOPMENT_PROCESS.md)）的验收标准。
> 每个任务在交付前必须逐项勾选。分「通用验收」和「按任务填充」两部分。

---

## 一、通用验收（每个任务必查）

### 1. 调研阶段
- [ ] 使用了 2+ 个 MCP 工具/来源交叉验证
- [ ] 结论有出处（URL/DOI），推断已标注
- [ ] 产出调研文档于 `docs/research/`

### 2. 设计阶段
- [ ] 每个决策回溯到调研依据
- [ ] 接口签名与现有代码库一致（无臆造模块）
- [ ] 数据规模/性能指标来自实测或可靠出处（无幻觉）

### 3. 实施规划阶段
- [ ] 依赖版本已核实（非凭空写）
- [ ] 文件路径真实存在
- [ ] 无低效操作（pandas `melt()`、逐行 INSERT、全表扫描）
- [ ] 接口变更列了 Before/After
- [ ] 考虑了 32GB 内存约束

### 4. 一致性对齐
- [ ] 调研 → 设计 → 实施 逻辑一致
- [ ] 术语统一（`factor_wide`/宽表/wide table）
- [ ] 数据规模数字在各文档一致
- [ ] 接口签名在设计与实施文档一致

### 5. TDD 实现
- [ ] 测试先行（红 → 绿 → 重构）
- [ ] 每个公共方法有 happy-path + 边界/异常测试
- [ ] 全量 `pytest` 通过，无破坏

### 6. 交付
- [ ] DEVLOG 已更新
- [ ] 架构级变更已归档 ADR
- [ ] ARCHITECTURE.md 已同步（如架构变化）

---

## 二、当前文档体系验收（本次任务）

### 文档完整性
- [x] 开发流程规范 `docs/spec/DEVELOPMENT_PROCESS.md`
- [x] 审查验收 checklist `docs/spec/REVIEW_CHECKLIST.md`（本文件）
- [x] ADR 索引 `docs/adr/README.md` + 7 个决策记录
- [x] 架构文档 `docs/ARCHITECTURE.md`（已更新为真实状态）
- [x] 开发日志 `docs/DEVLOG.md`

### 文档一致性对齐（本次已核对）
- [x] 因子数：241（ARCHITECTURE / DEVLOG / ADR-0007 一致）
- [x] 数据行数：1,683,530,224（ARCHITECTURE / DEVLOG 一致）
- [x] DB 大小：46.39 GB（ARCHITECTURE 实测值）
- [x] 双轨状态：代码完成、未迁移（ADR-0004 / ARCHITECTURE / DEVLOG 一致）
- [x] 决策编号：ADR-0001~0007 连续，索引与实际文件一致

---

## 三、遗留问题清单（待后续任务验收）

| 优先级 | 问题 | 关联文档 |
|--------|------|---------|
| P0 | `factor_data` 主键缺失（>26GB 内存） | ADR-0004, ARCHITECTURE §7 |
| P0 | 双轨迁移未执行 | ADR-0004, ARCHITECTURE §3.2 |
| P2 | Level 1 历史导入未完成 | ADR-0005, ARCHITECTURE §7 |
| P2 | CSMAR 因子导入 | DEVLOG backlog |

---

## 四、本次任务：迁移安全化（P0）

来源：[Claude 评审核实分析](../reviews/2026-10-claude-audit-analysis.md) · 文档：[调研](../research/migration_safety_research.md) / [设计](../design/migration_safety_design.md) / [实施](../implementation/migration_safety_implementation.md)

- [x] 事务化 DDL 已实证（DuckDB 1.5.3，6 项探针）+ 官方文档佐证
- [x] 调用方已核查（仅测试引用，无生产脚本）
- [x] 红测试先行：失败中止 / 备份表 / 部分历史中止 / 重跑幂等（`TestMigrationSafety` 4 个）
- [x] 实现：每因子事务 + fail-fast + 行数对账 + RENAME 备份
- [x] 现有 7 个迁移测试保持通过（test_migration.py 11 passed）
- [x] 全量回归 `pytest` 通过（245 passed）
- [x] DEVLOG / ADR / ARCHITECTURE 同步

---

## 五、本次任务：HIGH 1 / 2 / 3 修复

来源：[Claude 评审核实分析](../reviews/2026-10-claude-audit-analysis.md) · 文档：[调研](../research/high_fixes_research.md) / [设计](../design/high_fixes_design.md) / [实施](../implementation/high_fixes_implementation.md)

### 调研与设计
- [x] 官方文档证据锚点（DuckDB 命名参数 / 标识符 / 配置；OWASP；Python sqlite3）
- [x] 学术资料锚点（Halfond & Orso AMNESIA 2005；DVWA 参数化实证）
- [x] 社区案例（Stack Overflow #77884985；McKinsey Lilli 标识符注入）
- [x] 幻觉排除记录（证伪 3 条、降级 2 条）
- [x] 设计文档明确「值参数化 + 标识符白名单」双轨与不能绑定位置

### TDD
- [x] 红测试先行：`test_sql_safety.py` / 宽表长表输入 / LoaderConfig env
- [x] `utils/sql_utils.py` + `query/base.py` + 缓存 key
- [x] 7 个 query 模块参数化
- [x] loader + config + README
- [x] `core/schema.py` 迁移/DDL 参数化
- [x] 脚本路径修复（`financial_factors` + `rglob`）
- [x] 其余辅助脚本路径环境变量化（`ZHUYI_DATA_PATH` / `LEVEL1_DATA_PATH` / `CSMAR_DATA_PATH` / `LEVEL1_PARQUET_DIR`）
- [x] 定向测试与全量回归通过（266 passed）
- [x] DEVLOG / 评审分析文档 / checklist 同步

---

## 六、本次任务：因子写路径统一（迁移前置）

来源：迁移阻塞项分析 · 文档：[调研](../research/factor_write_path_research.md) / [设计](../design/factor_write_path_design.md) / [实施](../implementation/factor_write_path_implementation.md)

### 调研与设计
- [x] 实证 DuckDB 1.5.3 视图不可写（`CatalogException`）
- [x] 全量列出仍写 `factor_data` 的生产路径（daily/csmar/phase1）
- [x] 确认 `load_factor_to_wide` 无生产调用方、`_import_to_factor_table` 为死代码
- [x] 确认 `create_factor_wide` 的 `IF NOT EXISTS` 不补列风险
- [x] 设计明确事务边界、legacy 写入判定与迁移补列

### TDD
- [x] 红测试：迁移前三表写入 / 迁移后写入 / OHLCV 不变 / CSMAR / 迁移补列（红灯 9 failed / 1 passed）
- [x] `SchemaManager.object_type()` 公有方法
- [x] `BaseLoader.write_factor_long()` + `_write_factor_from_temp()`
- [x] `DailyLoader` 双写切换 + 删除死代码
- [x] `CSMARLoader` 双写切换
- [x] Phase 1 barra 脚本切换
- [x] 迁移补齐已有宽表缺失列
- [x] 定向 + 全量回归通过（定向 59 passed；全量 **276 passed**）
- [x] DEVLOG / ADR-0004 / checklist 同步

### 静态验收
- [x] `INSERT INTO factor_data` 仅出现在 `BaseLoader._write_factor_from_temp` 的 legacy 分支（生产代码）
- [x] `_import_to_factor_table` 已删除

---

## 七、生产库双轨迁移（2026-10-23）

### 迁移执行
- [x] 迁移前检查：`factor_data` 实体表 1,683,530,224 行 / 241 因子，`factor_wide`/`factor_history` 不存在，磁盘充足
- [x] 逐因子 upsert 迁移实测不可用（18.3 小时仅 56/241，吞吐持续退化），已中止
- [x] 两阶段重建：history 单次扫描（1,683,530,224 行）→ 24 因子分块 pivot（11 块）→ 4 轮 FULL JOIN 合并
- [x] 分块 pivot 修复：`arg_max(CASE WHEN factor_name=... THEN factor_value END, loaded_at)`，语义切片验证通过
- [x] `factor_wide` 创建 + 主键，9,307,121 行 / 241 列
- [x] 抽样对账：LT_debt_ratio / UTR_turnover / UTR_turnover_new / undist_profit_ps / undistr_porfit / working_capital 全部一致
- [x] 删除 `idx_factor_name_date`（索引依赖阻止 RENAME）
- [x] `factor_data` RENAME 为 `factor_data_backup_20261023_162924`，创建动态兼容视图

### 迁移后校验
- [x] 对象类型：`factor_data`=VIEW、`factor_wide`/`factor_history`/备份=BASE TABLE
- [x] 行数：wide 9,307,121 / history 1,683,530,224 / 备份 1,683,530,224
- [x] 因子列数 241；宽表抽样非空计数与 history 一致
- [x] 视图与宽表区间查询计数一致（均值差异仅为浮点求和顺序）
- [x] 视图点查可用（`factor_name` + 单日，约 3 秒）
- [x] DEVLOG / ARCHITECTURE / ADR-0004 同步

### 后续
- [ ] 确认无需回滚后删除 `factor_data_backup_20261023_162924`（实测约 11.6 GiB，见 [遗留问题调研](../research/remaining_issues_research.md)）
- [x] 新增 `SchemaManager.rebuild_factor_data_dual_track(chunk_size, sample_size, temp_directory)` 两阶段重建方法；`test_rebuild_dual_track.py` 5 个测试通过，全量 **281 passed**
- [x] `_replace_factor_data_with_view` 自动删除 `factor_data` 索引，避免 RENAME 依赖阻塞
- [x] 设计文档补充 §7 两阶段重建；`migrate_factor_data_to_wide` 保留但标记为不适用于大规模迁移

---

## 八、Phase 2 Level 1 + Phase 3 CSMAR 导入（2026-10-23 ~ 10-24）

### Phase 2 Level 1 Parquet（ADR-0005）
- [x] `Level1ParquetConverter.convert_directory()` 增量转换（已存在分区跳过）
- [x] 分区 2,250 个（`trade_date=YYYY-MM-DD/data.parquet`），共 2,107,016,487 行 / 32.84 GB
- [x] 对账：源 Feather 2,250 → 分区 2,250，缺失 0、多余 0
- [x] 抽样分区日期与目录名一致（2014-01-02 / 2018-06-01 / 2023-03-30）
- [x] `level1_snapshots` 保持空表（分钟数据不进入 DuckDB）

### Phase 3 CSMAR（ADR-0009）
- [x] 侦察 125 个 distinct 基表的列名/日期/实体/维度变体；确认尾号文件为分片
- [x] `CSMARLoader` 重写：分片合并、日/周/月/年日期解析、实体识别（stock/index/IND/MKT）、维度激活规则
- [x] `history_only` 写入路径：单事务 `UNPIVOT` 整文件追加，避免宽表膨胀
- [x] `factor_info` 登记 + `csmar_import_log` 断点续跑
- [x] 查询层：非宽表因子回退 `factor_history`（`_fallback_table`），`list_factors` 合并宽表列与 `factor_info`
- [x] 全量导入：782 个源 / 3,519,531,854 行 / 0 失败；`factor_history` 1,683,530,224 → 5,203,062,078
- [x] 命名校验：非法 0、最长 54 字符、描述冲突 0
- [x] 宽表不变性：9,307,121 行 / 241 因子
- [x] **VPIN 修复**：`N`（篮子数）识别为表级维度（`STEM_DIMENSIONS`）；定向删除旧 4 因子 65,330,164 行并重新导入 13 个 zip → 9,798 万行 / 18 个因子
- [x] **loaded_at 修复**：`factor_history` 生产表无默认值，批量 INSERT 显式写入 `CURRENT_TIMESTAMP`（`BaseLoader` + CSMAR 批量）；回填 3,519,531,854 行 NULL
- [x] 定向测试 `tests/test_csmar_loader.py` 13 passed（含 VPIN、loaded_at、分片、周/月/年、市场/指数/行业、DGTW）
- [x] 文档同步：ADR-0009 / ARCHITECTURE / DEVLOG / 本 checklist

---

## 九、factor_history 物理重排（2026-10-25）

### 执行
- [x] 重排前记录基线：行数 5,203,062,078 / `loaded_at` 非空 5,203,062,078 / 三因子抽样行数与日期区间
- [x] CTAS `ORDER BY factor_name, trade_date, stock_code`；`threads=4`、`memory_limit=24GB`、`temp_directory=F:\factor_db_tmp`、`preserve_insertion_order=true`
- [x] 首次尝试 `temp_directory=D:` 因外部排序中间数据 179.9 GiB 触发 OOM，改 F: 后成功（DuckDB 1.5.3 不支持多 temp_directory）
- [x] 重排耗时 69,082 秒（约 19.2 小时）

### 校验
- [x] 行数对账：5,203,062,078 → 5,203,062,078，`loaded_at` 计数一致
- [x] 抽样三因子前后一致：1,039 / 741 / 493,127 行，日期区间 2023-04~2025-12、1992-01~2026-02
- [x] 首末行顺序正确（`BF_CRASHRISK_CRASH_Cmdeq / 1991-01-01` → `working_capital / 2026-01-27`）
- [x] `RENAME` 交换 + `DROP` 旧表 + `CHECKPOINT`（2s）完成；DB 69.69 → 93.43 GB
- [x] 查询基线（热缓存）：跨文件单因子 2024Q1 36.5s → 0.45s；VPIN 2024Q1 5.0s → 1.40s；matrix 6.8s → 2.6–3.0s；stats 0.7s → 0.05s
- [x] 冷缓存首次访问仍 21–25s（HDD 行组元数据分散），已在 DEVLOG 注明
- [x] 文档同步：DEVLOG / ARCHITECTURE / 本 checklist

### 遗留
- [ ] DB 约 24 GB 死空间（`DROP` 旧表后 checkpoint 仅部分回收），如需压缩需重建/复制数据库
- [ ] 冷缓存路径可考虑将热因子迁至 SSD 或增加专用索引缓存层

