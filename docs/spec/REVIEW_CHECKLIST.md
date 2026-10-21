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
