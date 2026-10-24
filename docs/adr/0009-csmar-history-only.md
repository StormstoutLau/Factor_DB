# ADR-0009: CSMAR 批量因子仅写入长表

- **状态**: 已接受
- **日期**: 2026-10-23
- **相关**: ADR-0004（宽表+长表双轨）

## 背景
Phase 3 需要导入 `E:\因子研究` 下的 CSMAR 数据：313 个 zip / 783 个 xlsx，
覆盖 1990–2026 的日/周/月/年频表。与已有 241 个因子不同，这批数据具有：

1. **因子列数量大**：单个日频表可达 42 个数值列（如 `HF_BSImbalance`），
   全部导入会产生数千个因子序列；
2. **键范围超出宽表**：`factor_wide` 的键为 2009-01-23 ~ 2026-02-28 的
   9,307,121 个 `(trade_date, stock_code)`，CSMAR 数据从 1990 年开始，
   且有指数（`IDX...`）、行业（`IND...`）、市场组合（`MKT_MT_...`）等
   非股票实体；
3. **大量稀疏列**：月度/季度/组合级因子在每个交易日只有极少数非空值。

## 决策
CSMAR 批量导入**只写 `factor_history`**，不改动 `factor_wide`：

- `CSMARLoader(history_only=True)` 走单条 `UNPIVOT` 语句整文件追加；
- 因子元数据写入 `factor_info`（`factor_type='csmar'`）；
- 新增 `csmar_import_log` 记录已处理文件，支持断点续跑；
- `Query.FactorQuery` 在因子不在宽表时回退 `factor_history`
  （`_fallback_table()`），`list_factors()` 合并宽表列与 `factor_info`；
- `factor_data` 视图保持为宽表 UNPIVOT，语义不变。

## 维度处理
- 个股级：维度（市场类型、分位数、形成期等）编码进**因子名**后缀，
  如 `STK_MKT_LOSEORWIN_IfWin_52weeks_MT_P9701_Q_30_C_0_F_1`；
- 市场/组合级：维度编码进**伪实体代码**，如 `MKT_MT_P9706_Q_30_C_1`；
- 指数级：`IDX<6位代码>`；行业级：`IND<行业码>`；
- 仅当维度确实在同一 `(日期, 实体)` 下产生多行时才进入命名
  （避免把 `MarketType` 这类个股属性拆成多个因子）。

## 理由
- 宽表继续承载「日频、稠密、股票截面」因子，保持 O(1) 列访问性能；
- 避免宽表膨胀到上千列 × 千万行（写入放大、行宽、存储均不可接受）；
- `factor_history` 物理顺序按 `(factor_name, trade_date, stock_code)`，
  单因子查询可依赖 zonemap 跳过绝大多数行组；
- 市场/行业/指数因子本质是时间序列，不需要宽表截面访问。

## 备选方案
- **全部进宽表**：列数上千、键范围扩到 1990 年，快照更新需重写全行，
  与 ADR-0004 记录的 upsert 性能问题同源 → 否决；
- **单独 `csmar_factor_wide`**：可作为后续优化（若某批日频因子成为高频查询）；
- **只导入股票级**：会丢失 Fama-French、DGTW、行业羊群等基准因子 → 否决。

## 后果
- ✅ 写路径为纯追加，无 `ON CONFLICT` 行版本放大，导入吞吐稳定；
- ✅ `factor_wide` / `factor_data` 保持不变，旧查询不受影响；
- ⚠️ `SELECT * FROM factor_data WHERE factor_name='<CSMAR 因子>'` 返回空，
  必须走 `FactorQuery`（已自动回退）或直接查 `factor_history`；
- ⚠️ `list_factors()` 依赖 `factor_info` 登记，绕过时需手工查询历史表。

## 实施记录（2026-10-24）
- **规模**：782 个源文件 → 3,519,531,854 行、5,439 个因子；`factor_history`
  1,683,530,224 → 5,203,062,078 行；`factor_wide` 不变（9,307,121 行 / 241 列）。
- **表级参数维度**：`HF_VPIN` 在同一 `(股票, 日期)` 有 `N=50` 与 `N=8` 两行，
  `N` 不在通用维度表中，导致 `keep='last'` 去重丢弃 50%（1,632 万行）。
  新增 `STEM_DIMENSIONS`（仅对指定基名生效），`N` 作为维度进入因子名
  （`_N_50` / `_N_8`）；定向删除旧 4 个因子并重导 13 个 zip。
  教训：任何“同一 (日期, 实体) 多行”的表都必须显式声明拆分维度，
  否则去重会静默丢数；导入日志对 >20% 的去重比例给出 WARNING。
- **loaded_at**：生产 `factor_history` 由迁移重建，`loaded_at` 无 DEFAULT，
  而批量 INSERT 未列该列 → 34.9 亿行 NULL，`(…, loaded_at) IN (…)` 的 PIT
  子查询恒假，`FactorQuery` 返回 0 行。修复：`BaseLoader._write_factor_from_temp`
  与 CSMAR 批量 INSERT 显式写入 `CURRENT_TIMESTAMP`；并回填历史 NULL 行
  （统一为导入完成时间，保持诚实 PIT 语义：2026-10 之前的数据导入不可见于更早的 `as_of`）。
  教训：`CREATE TABLE IF NOT EXISTS` 不会为既有表补默认值，写路径不得依赖 DDL 默认。
