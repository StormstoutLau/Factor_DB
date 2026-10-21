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
