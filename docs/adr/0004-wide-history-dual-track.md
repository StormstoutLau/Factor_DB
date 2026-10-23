# ADR-0004: 宽表+长表双轨存储架构

- **状态**: 已接受（修订 ADR-0002）
- **日期**: 2026-06

## 背景
EAV 长表（ADR-0002）在 241 个因子、16.8 亿行规模下暴露致命问题：`factor_name` VARCHAR 使 ART 索引内存膨胀 80 倍，32GB 机器无法为长表建主键/索引（需 >26GB 内存）。

## 决策
采用**宽表+长表双轨架构**：
- `factor_wide`：宽表，`(trade_date, stock_code)` 为主键，每个因子一列。用于 99% 的日常查询。
- `factor_history`：长表（EAV），无主键。保留所有历史版本，用于 PIT 回测与审计。
- `factor_data`：替换为视图，`UNPIVOT(factor_wide)` 的结果，保持向后兼容。

## 理由
- 宽表行数 = 股票数 × 交易日数 ≈ 10.5M（而非 16.8 亿），PK ART 索引从 ~45GB 降至 ~600MB
- 宽表 `ADD COLUMN` 是元数据操作，秒级完成，动态加因子无成本
- 长表保留完整版本链，满足 PIT 需求
- 视图保持旧代码 `SELECT * FROM factor_data` 兼容

## 备选方案
- **纯宽表**：丢失历史版本，无法 PIT
- **纯长表 + 分区**：分区可缓解但 VARCHAR 膨胀仍存在
- **外部 ClickHouse**：功能强但部署重（见 ADR-0001）

## 后果
- ✅ 日常查询性能提升 5-20x（zonemap + 物理排序）
- ✅ 32GB 机器可行
- ✅ 双写已收敛到 `BaseLoader.write_factor_long()`（`factor_history` + `factor_wide` 单事务）；`factor_data` 为实体表时附加 legacy 写入，迁移后自动跳过
- ✅ 兼容视图为动态 `UNPIVOT COLUMNS(* EXCLUDE ...)`，迁移后新增因子列无需重建视图
- ✅ 迁移自动补齐已有 `factor_wide` 的缺失因子列
- ✅ **生产库已于 2026-10-23 完成迁移**：`factor_wide` 9,307,121 行、`factor_history` 1,683,530,224 行，原表备份为 `factor_data_backup_20261023_162924`
- ⚠️ 逐因子 `ON CONFLICT` upsert 迁移在 241 列宽表上性能不可用（实测 18.3 小时仅完成 56/241，预计 130+ 小时）；实际采用「history 单次重建 + 24 因子分块 pivot + FULL JOIN 合并」两阶段方案，约 13 小时完成（受 F: 机械硬盘 I/O 限制）
- ⚠️ 迁移时 `ALTER TABLE factor_data RENAME` 会被 `idx_factor_name_date` 索引依赖阻止，需先 `DROP INDEX`；备份表目前无该索引
