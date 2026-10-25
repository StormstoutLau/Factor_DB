# Factor_DB 架构设计文档

> 本文档反映当前代码库的真实状态（最后更新：2026-10）。
> 架构决策的完整依据见 [ADR 索引](adr/README.md)。开发流程见 [spec](spec/DEVELOPMENT_PROCESS.md)。

---

## 一、项目概述

Factor_DB 是基于 **DuckDB** 的高性能量化金融数据存储系统，专为 A 股市场设计，覆盖日 K 行情、因子数据、Level 1 分钟数据三大数据类型，支持 PIT（Point-in-Time）时间点版本查询。

### 核心特性
- **高性能**: DuckDB 向量化查询，比 pandas 快 10-100 倍
- **PIT 版本管理**: `loaded_at` + `MAX(loaded_at)` 子查询实现时间点回溯
- **双轨因子存储**: 宽表（日常查询）+ 长表（PIT/审计）
- **海量 Level 1**: Hive 分区 Parquet，突破内存限制
- **模块化**: 分层架构，各模块独立可替换

### 数据规模现状（2026-10 实测）
| 表 | 行数 | 说明 |
|----|------|------|
| `factor_history` | 5,203,062,078 | 长表（原 241 因子 16.8 亿 + CSMAR 35.2 亿） |
| `factor_wide` | 9,307,121 | 宽表（241 列 + PK） |
| `factor_info` | 5,439+ | 因子元数据（CSMAR 5,439 条 + 原有因子） |
| `daily_prices` | 43,226,352 | 日 K 行情（OHLCV + 复权） |
| `stock_info` | 7,472 | 股票基本信息 |
| `trade_calendar` | 1,475 | 交易日历 |
| DB 文件 | 93.43 GB | factor_db.duckdb（重排后膨胀，checkpoint 仅部分回收） |
| Level 1 Parquet | 2,107,016,487 行 / 32.84 GB | 2,250 个 Hive 分区（不在 DuckDB 内） |

---

## 二、总体架构

```
Factor_DB/
├── core/                    # 核心引擎层
│   ├── connection.py        # DuckDB 连接管理（读写分离单例）
│   ├── schema.py            # 表结构 + 宽表迁移 + PIVOT/UNPIVOT
│   └── metadata_manager.py  # 元数据管理
├── loaders/                 # 数据加载层
│   ├── base.py              # BaseLoader + LoaderConfig
│   ├── daily_loader.py      # 日 K + 因子（含宽表双写）
│   ├── level1_loader.py     # Level 1 分钟（Feather→Parquet 转换）
│   ├── csmar_loader.py      # CSMAR 国泰安格式
│   ├── macro_loader.py      # 宏观经济数据
│   ├── news_loader.py       # 舆情新闻/研报
│   └── alternative_loader.py# 另类数据
├── query/                   # 查询接口层
│   ├── base.py              # BaseQuery + QueryFilter + PIT 子查询
│   ├── price_query.py       # 价格查询（daily + level1）
│   ├── factor_query.py      # 因子查询（宽表优先，PIT 回退长表）
│   ├── macro_query.py       # 宏观查询
│   ├── sentiment_query.py   # 舆情查询
│   ├── alternative_query.py # 另类数据查询
│   ├── screen.py            # 条件选股
│   ├── osint_query.py       # OSINT 查询
│   └── cache.py             # LRU 查询缓存
├── adapters/                # 适配器层
│   ├── factor_calculator.py # 因子计算
│   ├── engine_adapter.py    # 回测引擎适配
│   └── pandas_adapter.py    # pandas 适配
├── analytics/               # 分析层
│   ├── macro_factor_link.py
│   ├── multi_source_analysis.py
│   └── sentiment_factor.py
├── osint/                   # OSINT 数据采集
│   ├── collectors/          # akshare/government/news_rss/worldbank
│   ├── base.py / pipeline.py / registry.py
├── utils/                   # 工具模块
│   ├── code_utils.py        # normalize_stock_code()
│   ├── sql_utils.py         # 参数绑定 / 标识符白名单与转义
│   ├── config.py            # AppLoaderConfig
│   ├── logger.py / validators.py
├── scripts/                 # 导入脚本（按 phase 组织）
├── tests/                   # 测试套件（266 测试）
├── benchmarks/              # 基准测试
├── cli.py                   # CLI（7 子命令）
└── docs/                    # 文档（含 spec/adr）
```

### 分层依赖

```
应用层 (cli / 用户代码)
    ↓
适配器层 (adapters)
    ↓
查询层 (query)          ← 只读连接 + LRU 缓存
    ↓
核心层 (core: connection / schema)   ← 读写分离
    ↓
加载层 (loaders)        ← 写连接
    ↓
数据源 (Feather / CSV / Parquet / Tushare / CSMAR)
```

---

## 三、核心表结构

### 3.1 版本化表（含 `loaded_at`，支持 PIT）

**`daily_prices`** — 日 K 行情
```
trade_date DATE, stock_code VARCHAR, loaded_at TIMESTAMP,
open/high/low/close DOUBLE, volume BIGINT, amount DOUBLE,
adj_factor DOUBLE, turnover DOUBLE
PRIMARY KEY (trade_date, stock_code, loaded_at)
```

**`factor_data`** — 因子 EAV 长表（当前 16.8 亿行）
```
trade_date DATE, stock_code VARCHAR, factor_name VARCHAR,
factor_value DOUBLE, loaded_at TIMESTAMP
PRIMARY KEY (trade_date, stock_code, factor_name, loaded_at)
```

### 3.2 双轨因子存储（ADR-0004 / ADR-0008）

| 表 | 结构 | 主键 | 用途 | 状态 |
|----|------|------|------|------|
| `factor_wide` | 宽表（每因子一列，241 列） | `(trade_date, stock_code)` | 99% 日常查询 | ✅ 9,307,121 行 |
| `factor_history` | 长表（EAV） | 无 | PIT 回测/审计 + CSMAR 批量因子 | ✅ 5,203,062,078 行 |
| `factor_data` | 视图（动态 UNPIVOT wide） | - | 向后兼容 | ✅ 已替换为视图 |
| `factor_data_backup_20261023_162924` | 原长表备份 | 无（索引已删） | 回滚/审计 | ✅ 1,683,530,224 行 |
| `factor_info` | 因子元数据 | `factor_name` | 因子目录 / 分类 | ✅ Phase 3 登记 |
| `csmar_import_log` | CSMAR 导入日志 | `source_file` | 断点续跑 | ✅ Phase 3 |

> **关键状态**: 生产库双轨迁移已于 2026-10-23 完成。`factor_data` 为动态视图（`UNPIVOT COLUMNS(* EXCLUDE (trade_date, stock_code, loaded_at))`），新因子列自动可见。原表保留为 `factor_data_backup_20261023_162924`。注意：原 `idx_factor_name_date` 已删除（索引依赖阻止 RENAME）；回滚时如需该索引需重建。

> **`factor_history` 物理顺序**: 2026-10-25 已按 `(factor_name, trade_date, stock_code)` 全量重排（CTAS 外部排序 19.2 小时），单因子查询可依赖 zonemap 跳过绝大多数行组；写入配置需保持 `preserve_insertion_order=true`。

> **CSMAR 批量因子（ADR-0009）**: 仅写 `factor_history`，不进入 `factor_wide`。原因：因子列数量大、键范围覆盖 1990–2026、含指数/行业/市场组合等非股票实体，全部进宽表会导致列数与行宽失控。`factor_info` 登记因子目录，`csmar_import_log` 支持断点续跑。命名规则：个股/指数/行业级维度编码进因子名（`_MT_P9706_Q_30`），市场级维度编码进伪实体（`MKT_MT_P9709`）；表级参数列通过 `STEM_DIMENSIONS` 生效（如 `HF_VPIN` 的 `N` → `_N_50` / `_N_8`）。

### 3.3 Level 1 分钟数据（ADR-0005）

不存入 DuckDB 表，而是 Hive 分区 Parquet：
```
level1_parquet/trade_date=YYYY-MM-DD/data.parquet
```
查询用 `read_parquet('level1_parquet/**/*.parquet', hive_partitioning=true)`。

### 3.4 扩展表
`factor_info`（5,439+ 因子目录，Phase 3 已启用）为实际使用；其余 `data_categories`、`data_sources`、`data_dictionary`、`macro_data`、`macro_indicators`、`news_sentiment`、`report_sentiment`、`alternative_data`、`alternative_types` 仍为预留空表。

---

## 四、PIT 查询架构（ADR-0003 + ADR-0006）

核心方法 `BaseQuery._build_pit_subquery()` 生成：
```sql
(trade_date, stock_code, loaded_at) IN (
    SELECT trade_date, stock_code, MAX(loaded_at)
    FROM table
    WHERE 1=1 [AND loaded_at <= 'as_of'] [AND col IS NOT NULL]
    GROUP BY trade_date, stock_code
)
```

- `as_of`：时间点回溯
- `non_null_columns`：排除 NULL 空壳行（ADR-0006，修复 83% NULL 导致的 NaN ICIR）

### 因子查询三层路由（`factor_query.py`）
| 场景 | 数据源 |
|------|--------|
| `as_of=None` + 全部因子在宽表 | `factor_wide`（最快，O(1) 列访问） |
| `as_of!=None` | `factor_history` + PIT 子查询 |
| `as_of=None` + 因子不在宽表（CSMAR 批量因子） | `factor_history` + PIT 子查询（ADR-0006） |
| 回退 | `factor_data` + PIT 子查询 |

---

## 五、数据流

### 5.1 导入流程
```
数据源文件 (pkl/csv/feather/xlsx)
    ↓
loaders (DailyLoader / Level1Loader / CSMARLoader / ...)
    ↓
UNPIVOT 宽→长转换（DuckDB 原生，避免 pandas melt）
    ↓
BaseLoader.write_factor_long() 单事务双写
    ├─ factor_history（保留 loaded_at 版本）
    └─ factor_wide（ON CONFLICT 更新最新值）
```

### 5.2 查询流程
```
用户请求 → BaseQuery 参数解析 → 构建 SQL（含 PIT 子查询）
    → DuckDB 只读连接执行 → LRU 缓存 → 返回 DataFrame
```

---

## 六、工程规范（Hard Constraints）

见 [spec/DEVELOPMENT_PROCESS.md](spec/DEVELOPMENT_PROCESS.md) 与项目 memory。核心约束：

1. `daily_prices` 和 `factor_data` 必须有 `loaded_at`，查询必须用 `as_of`
2. 股票代码统一 `normalize_stock_code()` 为 6 位数字
3. 导入顺序：Phase 1 → 2 → 3 → 4
4. 索引创建用 `safe_only=True` 跳过 OOM 风险索引
5. 宽→长转换用 DuckDB `UNPIVOT`，禁用 pandas `melt()`
6. 内存约束 32GB，ART 索引对 >800M 行表会 OOM

---

## 七、已知问题（Remaining Issues）

- ~~**[P0]** `factor_data` 主键缺失~~ → 已由 `factor_wide` 主键解决；原长表备份不再需要主键
- ~~**[P0]** `idx_factor_code_date`、`idx_factor_pit` 索引缺失~~ → 查询已切到 `factor_wide` / `factor_history`，不再依赖长表索引
- ~~**[P0]** 双轨迁移未在生产库执行~~ → 2026-10-23 已完成
- ~~**[P2]** CSMAR 因子待导入~~ → 2026-10-24 完成：5,439 个因子 / 35.20 亿行（ADR-0009）
- ~~**[P2]** Level 1 历史导入未完成~~ → 2026-10-23 完成：2,250 分区 / 21.07 亿行；`level1_snapshots` 按 ADR-0005 保持空表
- **[P1 - 已沉淀]** 逐因子 upsert 迁移路径（`migrate_factor_data_to_wide`）在 241 列宽表上性能不可用（实测 130+ 小时）；已新增 `SchemaManager.rebuild_factor_data_dual_track()` 两阶段重建（5 个测试），后续迁移优先使用该方法
- ~~**[P2]** `factor_history` 物理顺序为导入顺序~~ → 2026-10-25 已按 `(factor_name, trade_date, stock_code)` 全量重排（19.2 小时），热缓存单因子查询 36.5s → 0.45s
- **[P2]** DB 文件 free blocks 35.3 GiB（`pragma_database_size` 实测；重排后 `DROP` 旧表，`CHECKPOINT` 仅部分回收）；如需压缩需重建/复制数据库
- **[P2]** `factor_history` 冷缓存首次访问仍 21–25s（HDD 上行组元数据分散读取）；热因子可考虑迁至 SSD
- **[P2]** `factor_data_backup_20261023_162924` 约占 11.6 GiB（`pragma_storage_info` block 估算，非旧文档中的 46 GB），确认无需回滚后可删除
- **[P2]** 压缩重建方案已调研：删备份 + `COPY FROM DATABASE`（约 30–90 分钟，保留物理顺序），见 [遗留问题调研](research/remaining_issues_research.md)
- **[P2]** `LIQ_ROLL_Y` 无有效数值列，导入时跳过（数据本身为空/文本列）

---

## 八、未来扩展

TB 级数据 + SQL 深度优化的演进路径见 [FUTURE_EXTENSION_PLAN.md](FUTURE_EXTENSION_PLAN.md)：
1. DuckDB + External Parquet Lakehouse（宽表 Parquet 分区，46GB→15GB）
2. DuckDB + S3/MinIO + Apache Iceberg（500GB→2TB）
3. ClickHouse 分布式集群（2TB+）
