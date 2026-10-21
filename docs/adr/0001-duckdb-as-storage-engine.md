# ADR-0001: 选择 DuckDB 作为存储引擎

- **状态**: 已接受
- **日期**: 2026-06

## 背景
需要一个高性能的量化金融数据存储引擎，用于 A 股日 K、因子、Level 1 分钟数据的存储与查询。数据规模初始预计百亿行级别。

## 决策
采用 **DuckDB** 作为唯一存储引擎，替代 pandas/Parquet 文件方案。

## 理由
- 嵌入式 OLAP 引擎，无需独立服务进程，适合单机研究场景
- 向量化查询执行，比 pandas 快 10-100 倍
- 原生支持列式存储、Parquet 读写、`UNPIVOT`/`PIVOT` 高性能转换
- Python 一等公民支持，与量化研究栈（pandas/numpy/pyarrow）无缝集成
- 支持 `memory_limit`、`threads` 等资源控制

## 备选方案
- **ClickHouse**: 功能更强但需独立部署，对单机研究过重
- **PostgreSQL**: 行存为主，不适合亿级列式计算
- **SQLite**: 并发与 OLAP 性能不足

## 后果
- ✅ 单文件部署，`factor_db.duckdb` 即完整数据库
- ✅ 开发效率高
- ⚠️ ART 索引全内存驻留，>800M 行表在 32GB 机器上会 OOM（见 ADR-0004/0005 的缓解）
- ⚠️ 单机扩展性受限，TB 级需迁移到外部 Parquet / Iceberg（见 FUTURE_EXTENSION_PLAN）
