# 双轨迁移调研文档

> 主题：因子数据 `factor_data`（16.8 亿行）向 `factor_wide` + `factor_history` 双轨架构迁移的可行性
> 日期：2026-07
> 状态：调研完成

---

## 1. 问题定义与范围

### 问题
双轨迁移代码（`SchemaManager.migrate_factor_data_to_wide()`）已实现并通过 6 个单元测试，但**生产库从未执行迁移**。`factor_wide`/`factor_history` 表当前不存在，数据仍全部堆积在 EAV 长表 `factor_data` 中。

### 范围
- **In scope**: 判断迁移为何未执行；评估在 32GB 内存约束下迁移 16.8 亿行数据的可行性；找出内存 OOM 风险点。
- **Out of scope**: Level 1 数据导入、CSMAR 因子导入、其他表的索引重建。

---

## 2. 现状实测（数据说话）

### 2.1 生产库当前状态

| 指标 | 实测值 |
|------|--------|
| `factor_data` 行数 | 1,683,530,224（16.8 亿） |
| 因子数量 | 241 |
| DB 物理文件 | 46.39 GB |
| `factor_data` 物理数据 | ~8 GB（13,764 行组 × 5 列） |
| 现有索引 | 仅 `idx_factor_name_date` |
| 内存上限 | 25.0 GiB（DuckDB 默认 80%） |
| 机器内存 | 32 GB |

### 2.2 关键发现：46GB 文件 vs 8GB 数据

`factor_data` 物理数据仅 ~8GB，但 DB 文件 46GB。差值 ~38GB 来自：
- `idx_factor_name_date` ART 索引（在 16.8 亿行上，ART 全内存驻留）
- 历史碎片（多次 UNPIVOT 导入产生的行组碎片，project_memory 记载 7,832 → 7,094 行组）

### 2.3 迁移代码现状

已实现 `migrate_factor_data_to_wide()`（[schema.py](file:///f:/Coding/Factor_DB/core/schema.py)），流程：
1. 获取 241 个因子列表
2. `create_factor_wide()` 建宽表 + PK `(trade_date, stock_code)`
3. `create_factor_history()` 建历史长表
4. 逐因子 `_migrate_single_factor()` 迁移
5. `_replace_factor_data_with_view()` 用视图替换 `factor_data`

---

## 3. 内存风险分析

### 3.1 核心风险点：`ROW_NUMBER() OVER (PARTITION BY ...)` 窗口函数

现有 `_migrate_single_factor()` 的去重逻辑：

```sql
INSERT INTO factor_wide (trade_date, stock_code, loaded_at, "{factor}")
SELECT trade_date, stock_code, COALESCE(loaded_at, CURRENT_TIMESTAMP), factor_value
FROM (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY trade_date, stock_code
        ORDER BY loaded_at DESC NULLS LAST
    ) AS rn
    FROM factor_data WHERE factor_name = '{factor}' AND factor_value IS NOT NULL
) sub WHERE rn = 1
ON CONFLICT (trade_date, stock_code) DO UPDATE ...
```

**风险**（[DuckDB 官方文档](https://duckdb.org/docs/lts/guides/troubleshooting/oom_errors) 明确）：
- `OVER (PARTITION BY ... ORDER BY ...)` 是 **blocking operator**，必须 materialize 整个输入并排序
- 每个因子约 700 万行（16.8 亿 / 241），单因子排序本身可承受
- 但 241 个因子逐个执行窗口函数，若内存未正确配置，累计碎片化分配可能导致 OOM

### 3.2 官方推荐替代：`arg_max()` 聚合

[DuckDB 官方文档](https://www.duckdb.org/docs/current/guides/performance/how_to_tune_workloads) 和社区实践一致推荐：
- 用 `GROUP BY trade_date, stock_code` + `arg_max(factor_value, loaded_at)` 替代窗口函数去重
- `arg_max` 是聚合函数，内存效率远高于窗口函数

### 3.3 PIVOT 操作的风险

DuckDB 官方文档将 `PIVOT` 列为潜在 OOM 源。迁移过程中：
- `create_factor_wide` 建 241 列宽表本身不 OOM（无数据）
- 但逐因子 `INSERT ... ON CONFLICT` 反复 upsert 到带 PK 的宽表，PK ART 索引持续增长（10.5M 行 × 2 列 PK ≈ 600MB，可承受）

---

## 4. 社区/学术现状

### 4.1 DuckDB 官方文档（权威来源）
- [Out of Memory Errors](https://duckdb.org/docs/lts/guides/troubleshooting/oom_errors)：明确 PIVOT、多个 blocking operator、窗口函数是 OOM 主因；给出缓解策略（降低 threads、`preserve_insertion_order=false`、`memory_limit` 设为 50-60%）
- [Tuning Workloads](https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads)：blocking operator 列表含 `windowing`（OVER ... PARTITION BY）

### 4.2 社区实践（Stack Overflow / 技术博客）
- [technetexperts.com](https://www.technetexperts.com/duckdb-oom-groupby-max-tuning/)：**核心结论**——用 `arg_max()` 替代 `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ... DESC)` 做去重，内存效率显著更高。建议 `memory_limit` 设为 80-90% 物理内存，`threads` 对齐物理核数。

### 4.3 学术论文（arxiv/semantic，相关性低）
检索到 3 篇列存压缩/查询优化论文（1609.07823、2102.02440），均为 2016-2021 年通用列存数据库研究，**与 DuckDB 大表迁移无直接关系**，不作为决策依据。

---

## 5. 关键结论与推荐方向

1. **迁移是可行的**，但现有代码的内存配置和去重方式存在风险
2. **必须优化**：`ROW_NUMBER() OVER` → `arg_max()` 聚合去重
3. **必须显式配置**：`memory_limit`、`threads`、`temp_directory`（磁盘溢出）、`preserve_insertion_order=false`
4. **必须分批**：逐因子迁移（现有 batch_size 机制已存在，需验证真正生效）
5. **磁盘空间需预留**：迁移过程 `factor_data`（46GB）+ 新表双存，需 >100GB 磁盘空间

---

## 6. 参考文献

- [DuckDB Out of Memory Errors (官方)](https://duckdb.org/docs/lts/guides/troubleshooting/oom_errors)
- [DuckDB Tuning Workloads (官方)](https://duckdb.org/docs/current/guides/performance/how_to_tune_workloads)
- [DuckDB OOM on GroupBy Max: Tuning (technetexperts)](https://www.technetexperts.com/duckdb-oom-groupby-max-tuning/)
- [DuckDB Memory Management & Performance Tuning (duckdblab)](https://duckdblab.org/en/post/duckdb-memory-management-performance-tuning/)
- [GitHub issue #6893: row_number + OOM excessive disk spilling](https://github.com/duckdb/duckdb/issues/6893)
- arXiv 1609.07823（列存压缩，相关性低，仅供参考）
