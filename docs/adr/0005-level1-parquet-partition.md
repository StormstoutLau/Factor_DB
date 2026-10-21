# ADR-0005: Level 1 分钟数据采用 Hive 分区 Parquet

- **状态**: 已接受
- **日期**: 2026-06

## 背景
Level 1 分钟数据规模达 21 亿+ 行，远超 DuckDB 单机内存可承载的 ART 索引范围（ADR-0001 的已知限制）。

## 决策
Level 1 分钟数据**不存入 DuckDB 表**，而是存储为 Hive 分区 Parquet 文件：
```
level1_parquet/
  trade_date=YYYY-MM-DD/
    data.parquet
```
查询时用 `read_parquet('level1_parquet/**/*.parquet', hive_partitioning=true)` 做分区裁剪。

## 理由
- 分区裁剪：时间查询只读目标分区，避免全表扫描
- Zonemap + 物理排序提供 5-20x 加速，替代 ART 索引
- Parquet 列式压缩大幅降低存储
- 无需加载到内存即可查询

## 备选方案
- **DuckDB 表 + ART 索引**：21 亿行必 OOM（已实测验证）
- **ClickHouse**：部署重，单机场景过重

## 后果
- ✅ 突破内存限制，支持海量分钟数据
- ✅ `Level1ParquetConverter` 实现 Feather → Parquet 增量转换
- ⚠️ 查询接口需走 `read_parquet` 而非标准 SQL 表
- ⚠️ 已转换 2,250 个分区，但 `level1_snapshots` 表当前 0 行（历史导入未完成）
