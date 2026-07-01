# Factor_DB Code Wiki

> 基于 DuckDB 的高性能量化金融数据存储系统，专为 A 股市场设计。

---

## 目录

1. [项目概述](#1-项目概述)
2. [整体架构](#2-整体架构)
3. [模块详解](#3-模块详解)
   - [core — 核心引擎层](#31-core--核心引擎层)
   - [loaders — 数据加载层](#32-loaders--数据加载层)
   - [query — 查询接口层](#33-query--查询接口层)
   - [analytics — 分析模块](#34-analytics--分析模块)
   - [adapters — 适配器层](#35-adapters--适配器层)
   - [utils — 工具模块](#36-utils--工具模块)
4. [数据库表结构](#4-数据库表结构)
5. [关键类与函数索引](#5-关键类与函数索引)
6. [模块依赖关系](#6-模块依赖关系)
7. [项目运行方式](#7-项目运行方式)
8. [测试体系](#8-测试体系)
9. [性能基准](#9-性能基准)

---

## 1. 项目概述

Factor_DB 是一套面向量化金融的本地数据存储与查询系统，以 DuckDB 为底层引擎，提供以下核心能力：

- **价格数据管理**：日 K 数据与 Level 1 分钟级行情数据的加载、存储与查询
- **因子数据管理**：因子截面、因子矩阵、IC 计算与因子覆盖率分析
- **条件选股**：基于 SQL 的条件筛选、多因子打分与分位数选股
- **宏观经济数据**：GDP/CPI/PMI/M2 等宏观指标的加载、查询与宏观环境识别
- **舆情数据**：新闻与研报情感数据的加载、聚合查询与情感选股
- **另类数据**：产业链/卫星/电商等另类数据的加载、查询与价格相关性分析
- **多源融合分析**：跨数据类型的综合评分与多维度选股
- **回测引擎兼容**：与 Factor_Trading_v3.0 回测引擎无缝对接

### 技术栈

| 组件 | 版本要求 | 用途 |
|------|---------|------|
| DuckDB | >= 0.10.0 | 列式数据库引擎 |
| pandas | >= 2.0.0 | 数据处理与 DataFrame 操作 |
| NumPy | >= 1.24.0 | 数值计算 |
| PyArrow | >= 15.0.0 | Feather 文件读写 |
| pydantic | >= 2.0.0 | 数据校验（预留） |
| tqdm | >= 4.66.0 | 进度条显示 |
| python-dotenv | >= 1.0.0 | 环境变量管理 |
| pytest | >= 8.0.0 | 测试框架 |
| pytest-benchmark | >= 4.0.0 | 性能基准测试 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    应用层 (用户代码)                             │
│         BacktestEngine / FactorPipeline / MultiSourceStrategy    │
├─────────────────────────────────────────────────────────────────┤
│                    适配器层 (adapters)                           │
│    PandasAdapter / FactorTradingAdapter / DataLoaderV3Adapter   │
├─────────────────────────────────────────────────────────────────┤
│                    分析层 (analytics)                            │
│    MacroFactorLink / SentimentFactor / MultiSourceAnalysis      │
├─────────────────────────────────────────────────────────────────┤
│                    查询层 (query)                                │
│    PriceQuery / FactorQuery / StockScreener                     │
│    MacroQuery / SentimentQuery / AlternativeQuery               │
├─────────────────────────────────────────────────────────────────┤
│                    核心层 (core)                                 │
│    DuckDBConnection / SchemaManager / MetadataManager           │
├─────────────────────────────────────────────────────────────────┤
│                    加载层 (loaders)                              │
│    Level1Loader / DailyLoader / MacroLoader                     │
│    NewsLoader / AlternativeLoader                               │
├─────────────────────────────────────────────────────────────────┤
│                    工具层 (utils)                                │
│    Config / Logger / Validators                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 分层设计原则

- **高内聚**：每个模块只负责单一职责
- **低耦合**：模块间通过接口交互，可独立替换
- **自顶向下依赖**：上层可依赖下层，下层不依赖上层
- **插件式扩展**：新增数据源只需实现 `BaseLoader` 和 `BaseQuery` 接口

---

## 3. 模块详解

### 3.1 core — 核心引擎层

核心层是整个系统的基础，负责数据库连接管理、表结构定义和元数据管理。

#### 3.1.1 connection.py — 连接管理

| 类 | 说明 |
|----|------|
| `DuckDBConnection` | DuckDB 连接管理器，单例模式 + 线程安全 |
| `ConnectionPool` | 连接池（预留扩展） |

**DuckDBConnection 关键设计**：

- **单例模式**：相同 `db_path + read_only` 组合返回同一实例，通过 `__new__` + 双重检查锁实现
- **读写分离**：`read_only=True` 和 `read_only=False` 是不同的单例实例
- **上下文管理器**：支持 `with` 语句自动管理连接生命周期
- **延迟连接**：调用 `connect()` 时才真正建立连接

```
DuckDBConnection
├── __new__(db_path, read_only)     # 单例：双重检查锁
├── __init__(db_path, read_only)    # 初始化（防重复）
├── connect() -> DuckDBPyConnection # 建立连接
├── close()                         # 关闭连接
├── execute(query, parameters)      # 执行 SQL
├── fetchdf(query, parameters)      # 执行 SQL 并返回 DataFrame
├── __enter__ / __exit__            # 上下文管理器
└── __del__()                       # 析构时关闭连接
```

**单例键格式**：`"{db_path}:{read_only}"`，存储在类变量 `_instances` 字典中。

#### 3.1.2 schema.py — 表结构管理

| 类 | 说明 |
|----|------|
| `SchemaManager` | 数据库表结构管理器，负责创建和维护所有表与索引 |

**SchemaManager 核心属性**：

- `TABLES`：类变量，字典类型，定义了全部 14 张表的 DDL 语句
- `INDEXES`：类变量，字典类型，定义了全部 20 个索引的创建语句

```
SchemaManager
├── __init__(connection)            # 接收 DuckDBConnection 实例
├── create_table(table_name) -> bool
├── create_all_tables() -> dict[str, bool]
├── create_index(index_name) -> bool
├── create_all_indexes() -> dict[str, bool]
├── drop_table(table_name) -> bool
├── table_exists(table_name) -> bool
├── get_table_stats(table_name) -> Optional[dict]
└── init_database() -> bool         # 一键初始化：创建所有表 + 索引
```

#### 3.1.3 metadata_manager.py — 元数据管理

| 类 | 说明 |
|----|------|
| `MetadataManager` | 元数据统一管理器，管理数据分类、数据源和数据字典 |

```
MetadataManager
├── __init__(db_path)               # 内部创建 DuckDBConnection
├── add_category(id, name, desc, priority) -> bool
├── get_categories() -> DataFrame
├── add_data_source(id, name, provider, ...) -> bool
├── get_data_sources(is_active) -> DataFrame
├── register_field(id, category_id, name, type, ...) -> bool
├── get_data_dictionary(category_id) -> DataFrame
└── init_default_metadata() -> bool  # 注册5个默认分类 + 4个默认数据源
```

**默认数据分类**：price(价格数据)、factor(因子数据)、macro(宏观经济)、sentiment(舆情数据)、alternative(另类数据)

**默认数据源**：tushare、akshare、eastmoney、sina

#### 3.1.4 schema_extension.py — 扩展表定义

提供 `TABLES_EXTENSION` 和 `INDEXES_EXTENSION` 字典，以及 `merge_with_extension()` 函数用于将扩展表定义与基础表定义合并。当前扩展表已直接集成到 `SchemaManager.TABLES` 中，此文件保留作为独立扩展定义的参考。

---

### 3.2 loaders — 数据加载层

数据加载层负责从外部数据源（文件、DataFrame）将数据导入 DuckDB。所有加载器继承自 `BaseLoader` 抽象基类。

#### 3.2.1 base.py — 加载器基类

| 类/数据类 | 说明 |
|-----------|------|
| `LoaderConfig` | 加载器配置数据类 |
| `BaseLoader` | 抽象基类，定义加载器统一接口 |

**LoaderConfig 字段**：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `db_path` | str | `'factor_db.duckdb'` | 数据库路径 |
| `batch_size` | int | 10000 | 批量插入大小 |
| `skip_existing` | bool | True | 跳过已存在数据 |
| `validate_data` | bool | True | 是否验证数据 |
| `show_progress` | bool | True | 显示进度条 |

> **注意**：`utils/config.py` 也定义了一个同名的 `LoaderConfig` 数据类（位于 `Config` 的子配置中），但字段不同（额外包含 `max_workers`）。实际加载器使用的是 `loaders/base.py` 中的 `LoaderConfig`，两者互不干扰但容易混淆。

**BaseLoader 抽象接口**：

```
BaseLoader (ABC)
├── __init__(config)                # 初始化连接
├── load(data_path) -> int          # [抽象] 加载数据
├── validate(data) -> bool          # [抽象] 验证数据格式
├── get_loaded_count() -> int
├── reset_counter()
├── _execute_batch(query, data) -> int  # 批量插入
└── __enter__ / __exit__            # 上下文管理器
```

#### 3.2.2 daily_loader.py — 日 K 数据加载器

| 类 | 说明 |
|----|------|
| `DailyLoader` | 日 K 数据加载器，支持 pkl/csv/parquet/feather 格式 |

**支持的文件格式**：`.pkl`、`.pickle`、`.csv`、`.parquet`、`.feather`

**数据流转过程**：

```
文件读取 → DataFrame 验证 → 宽格式转长格式(melt) → 导入目标表
                                                    ├── 价格字段 → daily_prices
                                                    └── 其他字段 → factor_data
```

```
DailyLoader
├── load(data_path) -> int          # 单文件或目录
├── validate(data) -> bool          # 验证日期索引/股票代码列/数值列/空值比例
├── load_stock_info(info_path) -> int  # 加载股票基本信息
├── _load_single_file(file_path) -> int
├── _load_directory(data_dir) -> int
├── _import_price_data(df, field_name) -> int
├── _import_to_daily_table(df, field_name) -> int
└── _import_to_factor_table(df, factor_name) -> int
```

**关键逻辑**：文件名（不含扩展名）作为字段名，如 `close.pkl` 导入为 close 字段，`PE.pkl` 导入为 PE 因子。

#### 3.2.3 level1_loader.py — Level 1 分钟数据加载器

| 类 | 说明 |
|----|------|
| `Level1Loader` | Level 1 分钟级行情数据加载器，仅支持 Feather 格式 |

**数据转换逻辑**：

- 时间格式转换：`93000` → `09:30:00`
- 股票代码格式化：`1` → `000001.SZ`（6开头为SH，其余为SZ）
- 从文件名提取交易日期（格式 `YYYY-MM-DD`）

```
Level1Loader
├── load(data_path) -> int
├── validate(data) -> bool          # 检查必需列：time/stock_code/OHLCV
├── get_date_range() -> tuple       # 获取已加载数据的日期范围
├── _load_single_file(file_path) -> int
├── _load_directory(data_dir) -> int
├── _transform_data(df, trade_date) -> DataFrame
├── _format_stock_code(code) -> str
└── _import_to_duckdb(df) -> int    # 支持 skip_existing
```

#### 3.2.4 macro_loader.py — 宏观经济数据加载器

| 类 | 说明 |
|----|------|
| `MacroLoader` | 宏观数据加载器，支持 DataFrame 直接导入和文件导入 |

**支持的文件格式**：`.csv`、`.xls/.xlsx`、`.pkl`、`.parquet`

**列名自动标准化**：支持中英文列名映射（如 `日期` → `trade_date`，`数值` → `value`）

```
MacroLoader
├── load(data_path, indicator_id) -> int  # 支持 DataFrame 或文件路径
├── validate(data) -> bool
├── load_indicator_definitions(indicators_df) -> int
├── _read_file(file_path) -> DataFrame
├── _standardize_columns(df, indicator_id) -> DataFrame
├── _load_indicator_info(df) -> None
└── _insert_macro_data(df) -> int
```

#### 3.2.5 news_loader.py — 舆情数据加载器

| 类 | 说明 |
|----|------|
| `NewsLoader` | 舆情数据加载器，支持新闻和研报两种数据类型 |

```
NewsLoader
├── load(data_path, data_type) -> int  # data_type: 'news' / 'report'
├── load_news(news_df) -> int          # 快捷方法
├── load_reports(reports_df) -> int    # 快捷方法
├── validate(data) -> bool
├── _read_file(file_path) -> DataFrame
├── _insert_news(df) -> int            # 列名标准化 + 数组列转换
└── _insert_reports(df) -> int
```

**特殊处理**：`related_stocks` 和 `related_industries` 列支持逗号分隔字符串自动转为列表。

#### 3.2.6 alternative_loader.py — 另类数据加载器

| 类 | 说明 |
|----|------|
| `AlternativeLoader` | 另类数据加载器，支持产业链/卫星/电商等多种数据类型 |

```
AlternativeLoader
├── load(data_path, data_type) -> int
├── validate(data) -> bool
├── register_data_type(data_type, type_name, ...) -> bool
├── _read_file(file_path) -> DataFrame  # 额外支持 .json
├── _standardize_columns(df, data_type) -> DataFrame
└── _insert_data(df) -> int             # ON CONFLICT DO NOTHING
```

**特殊处理**：
- 自动生成 `data_id`：`{data_type}_{data_subtype}_{entity_id}_{trade_date}`
- `metadata` 列支持 dict 自动序列化为 JSON
- 支持中英文列名映射

---

### 3.3 query — 查询接口层

查询层提供面向业务的数据查询接口，所有查询器继承自 `BaseQuery` 抽象基类。

#### 3.3.1 base.py — 查询基类

| 类/数据类 | 说明 |
|-----------|------|
| `QueryFilter` | 查询过滤条件数据类 |
| `BaseQuery` | 查询基类，提供公共 SQL 构建方法 |

**QueryFilter 字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `start_date` | Optional[date] | 开始日期 |
| `end_date` | Optional[date] | 结束日期 |
| `stock_codes` | Optional[list[str]] | 股票代码列表 |
| `fields` | Optional[list[str]] | 查询字段列表 |

**BaseQuery 公共方法**：

```
BaseQuery (ABC)
├── __init__(db_path)               # 以只读模式创建连接
├── _build_date_filter(filter) -> str    # 构建日期 WHERE 子句
├── _build_stock_filter(filter) -> str   # 构建股票代码 WHERE 子句
├── _build_field_selector(filter, defaults) -> str  # 构建 SELECT 字段
└── _execute_query(sql) -> DataFrame    # 执行 SQL 并返回 DataFrame
```

#### 3.3.2 price_query.py — 价格数据查询

| 类 | 说明 |
|----|------|
| `PriceQuery` | 价格数据查询器，支持日 K 和 Level 1 数据 |

```
PriceQuery
├── get_daily(stock_codes, start_date, end_date, fields) -> DataFrame
├── get_level1(stock_codes, trade_date, start_time, end_time, fields) -> DataFrame
├── get_price_matrix(field, stock_codes, start_date, end_date, adjust) -> DataFrame
├── get_trade_dates(start_date, end_date) -> list[date]
├── get_stock_list(trade_date, industry) -> list[str]
├── get_date_range() -> tuple[Optional[date], Optional[date]]
└── _get_adjusted_price_expr(field, adjust) -> str
```

**复权方式**：

| adjust | 表达式 | 说明 |
|--------|--------|------|
| `none` | `field` | 不复权 |
| `forward` | `field * adj_factor` | 前复权 |
| `backward` | `field / adj_factor` | 后复权 |

**价格矩阵**：`get_price_matrix()` 返回 `dates × stocks` 的宽格式 DataFrame，直接用于回测引擎的向量化计算。

#### 3.3.3 factor_query.py — 因子数据查询

| 类 | 说明 |
|----|------|
| `FactorQuery` | 因子数据查询器，支持截面/矩阵/IC/统计 |

```
FactorQuery
├── get_factor(factor_name, stock_codes, start_date, end_date) -> DataFrame
├── get_cross_section(factor_name, trade_date) -> DataFrame
├── get_factor_matrix(factor_names, stock_codes, start_date, end_date) -> DataFrame
├── get_factor_stats(factor_name, trade_date) -> dict
├── get_ic_series(factor_name, forward_return_days, start_date, end_date) -> DataFrame
├── list_factors() -> list[str]
├── get_factor_coverage(factor_name, start_date, end_date) -> DataFrame
```

**IC 计算**：使用 Spearman Rank IC，通过 SQL 窗口函数 `RANK()` + `CORR()` 实现，衡量因子对前瞻收益率的预测能力。

**因子统计**：返回 count/mean/std/min/max/median 六项统计指标。

#### 3.3.4 screen.py — 条件选股

| 类 | 说明 |
|----|------|
| `StockScreener` | 条件选股器，支持 SQL 条件筛选和多因子打分 |

```
StockScreener
├── screen(trade_date, conditions, sort_by, ascending, limit) -> DataFrame
├── rank_by_factors(trade_date, factors, stock_codes, limit) -> DataFrame
├── get_industry_distribution(stock_codes, trade_date) -> DataFrame
└── get_quantile_stocks(factor_name, trade_date, quantile, top) -> DataFrame
```

**多因子打分逻辑**：
1. 对每个因子按方向（1=正向/-1=负向）进行排名
2. 将各因子排名相加得到总分
3. 按总分降序排列

**factors 参数格式**：`{'PE': -1, 'ROE': 1, 'PB': -1}`，1 表示正向因子（值越大越好），-1 表示负向因子（值越小越好）。

#### 3.3.5 macro_query.py — 宏观数据查询

| 类 | 说明 |
|----|------|
| `MacroQuery` | 宏观数据查询器，支持矩阵/同比/统计/宏观环境识别 |

```
MacroQuery
├── get_macro_data(indicator_ids, start_date, end_date, value_type) -> DataFrame
├── get_macro_matrix(indicator_ids, start_date, end_date, value_type) -> DataFrame
├── get_indicators(category) -> DataFrame
├── get_indicator_stats(indicator_id, start_date, end_date) -> dict
├── get_latest_values(indicator_ids) -> DataFrame
├── get_macro_by_date_range(start_date, end_date, categories) -> DataFrame
├── calculate_yoy(indicator_id, periods) -> DataFrame
└── identify_macro_regime(start_date, end_date, n_regimes) -> DataFrame
```

**宏观环境分类逻辑**（基于 PMI 和 CPI）：

| PMI | CPI | 环境 |
|-----|-----|------|
| >= 50 | >= 2 | 过热 |
| >= 50 | < 2 | 复苏 |
| < 50 | >= 2 | 滞胀 |
| < 50 | < 2 | 衰退 |

**同比计算**：使用 SQL 窗口函数 `LAG(value, periods)` 实现同比计算，月度数据默认 periods=12。

#### 3.3.6 sentiment_query.py — 舆情数据查询

| 类 | 说明 |
|----|------|
| `SentimentQuery` | 舆情数据查询器，支持股票舆情/情感聚合/选股 |

```
SentimentQuery
├── get_stock_sentiment(stock_code, start_date, end_date) -> DataFrame
├── get_sentiment_aggregation(stock_codes, start_date, end_date, freq) -> DataFrame
├── get_report_sentiment(stock_code, start_date, end_date) -> DataFrame
├── get_sentiment_stocks(sentiment_label, query_date, limit) -> DataFrame
├── get_sentiment_trend(stock_code, window, start_date, end_date) -> DataFrame
└── get_hot_news(query_date, limit) -> DataFrame
```

**DuckDB 数组查询**：使用 `list_position(related_stocks, 'code')` 查询包含指定股票的新闻。

**情感聚合**：支持日(D)/周(W)/月(M)频率聚合，返回 news_count/avg_sentiment/sentiment_std/正负面数量。

**情感趋势**：使用 SQL 窗口函数计算移动平均情感得分。

#### 3.3.7 alternative_query.py — 另类数据查询

| 类 | 说明 |
|----|------|
| `AlternativeQuery` | 另类数据查询器，支持时间序列/统计/价格相关性 |

```
AlternativeQuery
├── get_data(data_type, data_subtype, entity_type, entity_id, start_date, end_date) -> DataFrame
├── get_data_types() -> DataFrame
├── get_entities(data_type, data_subtype) -> DataFrame
├── get_time_series(data_type, data_subtype, entity_id, start_date, end_date) -> DataFrame
├── get_latest_data(data_type, data_subtype) -> DataFrame
├── get_data_stats(data_type, data_subtype, entity_id) -> DataFrame
├── get_correlation_with_price(data_type, data_subtype, entity_id, stock_code, price_field, lag) -> float
├── get_satellite_data(data_subtype, ...) -> DataFrame    # 快捷方法
├── get_chain_data(data_subtype, ...) -> DataFrame        # 快捷方法
└── get_e_commerce_data(data_subtype, ...) -> DataFrame   # 快捷方法
```

**价格相关性**：使用 SQL `CORR()` 函数计算另类数据与股价的 Pearson 相关系数，支持滞后天数参数。

---

### 3.4 analytics — 分析模块

分析层位于查询层之上，组合多个查询器实现跨数据类型的分析功能。

#### 3.4.1 macro_factor_link.py — 宏观-因子关联分析

| 类 | 说明 |
|----|------|
| `MacroFactorLink` | 宏观指标与股票因子的关联分析 |

**内部依赖**：`MacroQuery` + `FactorQuery`

```
MacroFactorLink
├── calculate_correlation(macro_indicator, factor_name, start_date, end_date, method) -> float
├── calculate_all_correlations(macro_indicators, factor_names, ...) -> DataFrame
├── build_macro_factor(macro_indicators, lookback, method) -> DataFrame
├── identify_macro_regime(start_date, end_date, n_regimes) -> DataFrame
└── get_sector_sensitivity(macro_indicator, sectors, ...) -> DataFrame  # 预留接口
```

**相关性方法**：支持 `pearson` 和 `spearman` 两种方法。

**宏观因子构建**：
- `equal_weight`：标准化后等权合成
- `pca`：PCA 第一主成分（需 sklearn）

标准化公式：`(value - rolling_mean) / rolling_std`

#### 3.4.2 sentiment_factor.py — 舆情因子构建

| 类 | 说明 |
|----|------|
| `SentimentFactor` | 舆情因子构建与信号生成 |

**内部依赖**：`SentimentQuery` + `FactorQuery`

```
SentimentFactor
├── build_sentiment_factor(stock_codes, start_date, end_date, window) -> DataFrame
├── get_sentiment_signals(trade_date, top_n, min_news_count) -> DataFrame
├── analyze_sentiment_impact(stock_code, event_date, window) -> dict
└── get_sentiment_factor_ic(factor_name, forward_return_days, ...) -> DataFrame  # 预留
```

**舆情因子构建**：基于情感聚合数据的移动平均（MA）和变化率（diff）。

**舆情事件影响分析**：计算事件前后情感得分变化。

#### 3.4.3 multi_source_analysis.py — 多源融合分析

| 类 | 说明 |
|----|------|
| `MultiSourceAnalysis` | 多源数据融合的综合评分与多维度选股 |

**内部依赖**：`PriceQuery` + `FactorQuery` + `MacroQuery` + `SentimentQuery` + `AlternativeQuery`

```
MultiSourceAnalysis
├── combine_score(stock_codes, trade_date, weights) -> DataFrame
├── multi_dimension_screen(trade_date, stock_pool, min_score, top_n) -> DataFrame
├── get_multi_source_panel(stock_codes, trade_date) -> DataFrame
├── _calculate_factor_score(stock_code, trade_date) -> float
├── _calculate_sentiment_score(stock_code, trade_date) -> float
├── _calculate_macro_score(trade_date) -> float
├── _calculate_alternative_score(stock_code, trade_date) -> float
└── _calculate_price_score(stock_code, trade_date) -> float
```

**默认权重配置**：

| 维度 | 权重 | 评分逻辑 |
|------|------|---------|
| factor | 0.30 | PE 分位数排名（低 PE 得高分） |
| sentiment | 0.20 | 情感得分 [-1,1] 映射到 [0,1] |
| macro | 0.20 | PMI 扩张/收缩映射到 [0,1] |
| alternative | 0.15 | 产业链数据标准化 |
| price | 0.15 | 20 日动量映射到 [0,1] |

---

### 3.5 adapters — 适配器层

适配器层提供与外部系统的兼容接口，使 Factor_DB 可以无缝对接回测引擎和 pandas 工作流。

#### 3.5.1 pandas_adapter.py — pandas 适配器

| 类 | 说明 |
|----|------|
| `PandasAdapter` | 数据格式转换与技术指标计算（全部静态方法） |

```
PandasAdapter
├── to_wide_format(df, index, columns, values) -> DataFrame     # 长→宽
├── to_long_format(df, value_name, id_vars) -> DataFrame        # 宽→长
├── to_multi_index(df) -> DataFrame                              # 设置 (date, code) 多级索引
├── fill_missing_dates(df, date_col, method) -> DataFrame       # 填充缺失日期
├── add_technical_indicators(df, indicators, ...) -> DataFrame  # 添加技术指标
└── resample_to_panel(df, freq, agg_func, ...) -> DataFrame    # 重采样
```

**支持的技术指标**：

| 指标 | 说明 | 实现方式 |
|------|------|---------|
| MA{n} | 移动平均线 | `rolling(n).mean()` |
| RSI | 相对强弱指标 | 14 日 RSI |
| MACD | 指数平滑异同移动平均线 | EMA12 - EMA26 + Signal |
| BBANDS | 布林带 | MA20 ± 2σ |

#### 3.5.2 engine_adapter.py — 回测引擎适配器

| 类 | 说明 |
|----|------|
| `FactorTradingAdapter` | Factor_Trading_v3.0 兼容层 |
| `DataLoaderV3Adapter` | DataLoaderV3 NumPy 数组格式转换 |

**FactorTradingAdapter**：

```
FactorTradingAdapter
├── get_adj_price(price_type, adjust, start_date, end_date, stock_codes) -> DataFrame
├── get_trade_dates(start, end) -> list[str]
├── get_stock_list(trade_date, industry) -> list[str]
├── get_factor_data(factor_name, start_date, end_date, stock_codes) -> DataFrame
├── get_factor_matrix(factor_names, start_date, end_date) -> DataFrame
└── get_daily_data(stock_codes, start_date, end_date, fields) -> DataFrame
```

**DataLoaderV3Adapter**：

```
DataLoaderV3Adapter
├── to_data_loader_v3(stock_codes, start_date, end_date, fields, adjust) -> dict
├── to_numpy_arrays(df, index_col, columns_col, values_col) -> dict
└── get_level1_panel(stock_codes, trade_date, fields) -> dict
```

**DataLoaderV3 输出格式**：

```python
{
    'dates': np.ndarray[str],           # 日期数组
    'stocks': np.ndarray[str],          # 股票代码数组
    'close': np.ndarray[shape=(n_dates, n_stocks)],  # 价格矩阵
    'open': np.ndarray[shape=(n_dates, n_stocks)],
    ...
}
```

---

### 3.6 utils — 工具模块

#### 3.6.1 config.py — 配置管理

| 数据类 | 说明 |
|--------|------|
| `DatabaseConfig` | 数据库配置（db_path/read_only/max_memory/threads） |
| `LoaderConfig` | 加载器配置（batch_size/skip_existing/validate_data/show_progress/max_workers） |
| `QueryConfig` | 查询配置（cache_size/cache_ttl/max_rows/timeout） |
| `Config` | 主配置，聚合以上三个子配置 + log_level/log_file |

> **注意**：此处的 `LoaderConfig` 与 `loaders/base.py` 中的 `LoaderConfig` 同名但字段不同（本处多了 `max_workers`）。加载器实际使用的是 `loaders/base.py` 中的定义。建议后续重构统一为一个。

**配置加载方式**：

```python
# 从 JSON 文件
config = Config.from_file('config.json')

# 从环境变量
config = Config.from_env()
# 支持: FACTOR_DB_PATH, FACTOR_DB_THREADS, FACTOR_LOG_LEVEL

# 保存到文件
config.to_file('config.json')
```

#### 3.6.2 logger.py — 日志配置

| 类/函数 | 说明 |
|---------|------|
| `ColoredFormatter` | 带颜色的日志格式化器（非 Windows 平台） |
| `setup_logging()` | 日志系统初始化函数 |

**日志颜色**：DEBUG=青色、INFO=绿色、WARNING=黄色、ERROR=红色、CRITICAL=紫色

**日志格式**：`2024-01-01 12:00:00 [INFO] module_name: message`

#### 3.6.3 validators.py — 数据验证

| 类 | 说明 |
|----|------|
| `ValidationError` | 验证错误异常 |
| `DataValidator` | 统一数据验证器 |

```
DataValidator
├── validate_dataframe(df, required_cols, name) -> bool
├── validate_price_data(df) -> bool       # OHLC 逻辑 + 非负 + 空值比例
├── validate_factor_data(df) -> bool      # 因子名非空 + 数值类型 + 无穷值检查
├── validate_level1_data(df) -> bool      # 时间格式 + 必需列
├── validate_stock_codes(codes) -> bool   # SH/SZ/BJ 交易所校验
└── validate_date_range(start, end, max_range_days) -> bool
```

---

## 4. 数据库表结构

### 4.1 核心数据表

| 表名 | 用途 | 主键 | 关键索引 |
|------|------|------|---------|
| `daily_prices` | 日 K 数据 | (trade_date, stock_code) | idx_daily_date, idx_daily_code, idx_daily_date_code |
| `level1_snapshots` | Level 1 分钟数据 | (trade_date, trade_time, stock_code) | idx_level1_date_code, idx_level1_time, idx_level1_code_time |
| `stock_info` | 股票基本信息 | stock_code | — |
| `trade_calendar` | 交易日历 | trade_date | — |
| `factor_data` | 因子数据 | (trade_date, stock_code, factor_name) | idx_factor_name_date, idx_factor_code_date |
| `factor_info` | 因子元数据 | factor_name | — |

### 4.2 元数据管理表

| 表名 | 用途 | 主键 |
|------|------|------|
| `data_categories` | 数据分类 | category_id |
| `data_sources` | 数据源信息 | source_id |
| `data_dictionary` | 数据字典 | field_id |

### 4.3 宏观经济数据表

| 表名 | 用途 | 主键 | 关键索引 |
|------|------|------|---------|
| `macro_data` | 宏观经济数据 | (trade_date, indicator_id, value_type) | idx_macro_date, idx_macro_indicator, idx_macro_date_indicator |
| `macro_indicators` | 宏观指标信息 | indicator_id | idx_macro_indicator_category |

### 4.4 舆情数据表

| 表名 | 用途 | 主键 | 关键索引 |
|------|------|------|---------|
| `news_sentiment` | 新闻舆情数据 | news_id | idx_news_date, idx_news_sentiment |
| `report_sentiment` | 研报舆情数据 | report_id | idx_report_date, idx_report_stock |

**news_sentiment 特殊列**：`related_stocks` (VARCHAR[]) 和 `related_industries` (VARCHAR[]) 使用 DuckDB 数组类型。

### 4.5 另类数据表

| 表名 | 用途 | 主键/约束 | 关键索引 |
|------|------|----------|---------|
| `alternative_data` | 另类数据 | data_id (PK) + UNIQUE(trade_date, data_type, data_subtype, entity_type, entity_id) | idx_alt_date, idx_alt_type, idx_alt_entity, idx_alt_full |
| `alternative_types` | 另类数据类型 | data_type | — |

**alternative_data 特殊列**：`value_array` (DOUBLE[])、`metadata` (JSON)。

---

## 5. 关键类与函数索引

### 核心层

| 类 | 文件 | 职责 |
|----|------|------|
| `DuckDBConnection` | core/connection.py | 单例连接管理，读写分离，上下文管理器 |
| `ConnectionPool` | core/connection.py | 连接池（预留） |
| `SchemaManager` | core/schema.py | 表/索引的创建、删除、检查、统计 |
| `MetadataManager` | core/metadata_manager.py | 数据分类/数据源/数据字典的 CRUD |

### 加载层

| 类 | 文件 | 支持格式 | 目标表 |
|----|------|---------|--------|
| `BaseLoader` | loaders/base.py | — (抽象基类) | — |
| `DailyLoader` | loaders/daily_loader.py | pkl/csv/parquet/feather | daily_prices / factor_data |
| `Level1Loader` | loaders/level1_loader.py | feather | level1_snapshots |
| `MacroLoader` | loaders/macro_loader.py | csv/xlsx/pkl/parquet/DataFrame | macro_data / macro_indicators |
| `NewsLoader` | loaders/news_loader.py | csv/xlsx/pkl/parquet/DataFrame | news_sentiment / report_sentiment |
| `AlternativeLoader` | loaders/alternative_loader.py | csv/xlsx/pkl/parquet/json/DataFrame | alternative_data / alternative_types |

### 查询层

| 类 | 文件 | 核心方法 |
|----|------|---------|
| `BaseQuery` | query/base.py | _build_date_filter, _build_stock_filter, _execute_query |
| `PriceQuery` | query/price_query.py | get_daily, get_level1, get_price_matrix, get_trade_dates |
| `FactorQuery` | query/factor_query.py | get_factor, get_cross_section, get_factor_matrix, get_ic_series |
| `StockScreener` | query/screen.py | screen, rank_by_factors, get_quantile_stocks |
| `MacroQuery` | query/macro_query.py | get_macro_data, get_macro_matrix, calculate_yoy, identify_macro_regime |
| `SentimentQuery` | query/sentiment_query.py | get_stock_sentiment, get_sentiment_aggregation, get_sentiment_stocks |
| `AlternativeQuery` | query/alternative_query.py | get_data, get_time_series, get_correlation_with_price |

### 分析层

| 类 | 文件 | 核心方法 |
|----|------|---------|
| `MacroFactorLink` | analytics/macro_factor_link.py | calculate_correlation, build_macro_factor |
| `SentimentFactor` | analytics/sentiment_factor.py | build_sentiment_factor, get_sentiment_signals |
| `MultiSourceAnalysis` | analytics/multi_source_analysis.py | combine_score, multi_dimension_screen |

### 适配器层

| 类 | 文件 | 核心方法 |
|----|------|---------|
| `PandasAdapter` | adapters/pandas_adapter.py | to_wide_format, add_technical_indicators, resample_to_panel |
| `FactorTradingAdapter` | adapters/engine_adapter.py | get_adj_price, get_trade_dates, get_factor_data |
| `DataLoaderV3Adapter` | adapters/engine_adapter.py | to_data_loader_v3, to_numpy_arrays, get_level1_panel |

### 工具层

| 类 | 文件 | 核心方法 |
|----|------|---------|
| `Config` | utils/config.py | from_file, from_env, to_file |
| `DataValidator` | utils/validators.py | validate_price_data, validate_factor_data |
| `ColoredFormatter` | utils/logger.py | format |

---

## 6. 模块依赖关系

### 层间依赖

```
analytics ──→ query ──→ core
adapters ──→ query ──→ core
loaders  ──→ core
loaders  ──→ utils (validators)
query    ──→ core
core     ──→ (无内部依赖)
utils    ──→ (无内部依赖)
```

### 具体导入关系

```
core/connection.py    ← duckdb
core/schema.py        ← core.connection
core/metadata_manager.py ← core.connection, pandas

loaders/base.py       ← core.connection
loaders/daily_loader.py ← loaders.base, pandas, tqdm
loaders/level1_loader.py ← loaders.base, pandas, pyarrow, tqdm
loaders/macro_loader.py ← loaders.base, utils.validators, pandas
loaders/news_loader.py ← loaders.base, pandas
loaders/alternative_loader.py ← loaders.base, pandas, json

query/base.py         ← core.connection
query/price_query.py  ← query.base, pandas
query/factor_query.py ← query.base, pandas
query/screen.py       ← query.base, pandas
query/macro_query.py  ← query.base, pandas
query/sentiment_query.py ← query.base, pandas
query/alternative_query.py ← query.base, pandas

analytics/macro_factor_link.py ← query.macro_query, query.factor_query, pandas, numpy
analytics/sentiment_factor.py ← query.sentiment_query, query.factor_query, pandas, numpy
analytics/multi_source_analysis.py ← query.price_query, query.factor_query, query.macro_query,
                                      query.sentiment_query, query.alternative_query, pandas, numpy

adapters/pandas_adapter.py ← pandas, numpy
adapters/engine_adapter.py ← core.connection, query.price_query, query.factor_query, pandas, numpy

utils/config.py       ← json, os, dataclasses
utils/logger.py       ← logging, sys
utils/validators.py   ← pandas
```

### 第三方库依赖图

```
DuckDB ←── core/connection
pandas ←── core/metadata_manager, loaders/*, query/*, analytics/*, adapters/*
numpy  ←── analytics/*, adapters/*
pyarrow ←── loaders/level1_loader
tqdm   ←── loaders/daily_loader, loaders/level1_loader
sklearn ←── analytics/macro_factor_link (可选，PCA方法)
```

---

## 7. 项目运行方式

### 7.1 环境准备

```bash
# 安装依赖
pip install -r requirements.txt

# 可选：PCA 宏观因子构建需要 scikit-learn
pip install scikit-learn
```

### 7.2 数据库初始化

```python
from core.connection import DuckDBConnection
from core.schema import SchemaManager

conn = DuckDBConnection('market.db')
schema = SchemaManager(conn)
schema.init_database()  # 创建所有 14 张表 + 20 个索引
```

### 7.3 数据加载

```python
from pathlib import Path
from loaders.daily_loader import DailyLoader
from loaders.level1_loader import Level1Loader
from loaders.macro_loader import MacroLoader
from loaders.news_loader import NewsLoader
from loaders.alternative_loader import AlternativeLoader

# 日 K 数据
DailyLoader().load(Path('E:/Ashare_data/market_data'))

# Level 1 分钟数据
Level1Loader().load(Path('E:/Level 1 Data'))

# 宏观数据
MacroLoader().load(Path('macro_data.csv'))

# 舆情数据
NewsLoader().load(news_df, data_type='news')

# 另类数据
AlternativeLoader().load(alt_df, data_type='chain')
```

### 7.4 数据查询

```python
from query.price_query import PriceQuery
from query.factor_query import FactorQuery
from query.screen import StockScreener
from query.macro_query import MacroQuery
from query.sentiment_query import SentimentQuery
from query.alternative_query import AlternativeQuery
from datetime import date

# 价格查询
pq = PriceQuery('market.db')
df = pq.get_daily(stock_codes=['000001.SZ'], start_date=date(2024, 1, 1))
matrix = pq.get_price_matrix(field='close', adjust='forward')

# 因子查询
fq = FactorQuery('market.db')
cross = fq.get_cross_section('PE', date(2024, 6, 30))
ic = fq.get_ic_series('PE', forward_return_days=1)

# 条件选股
sc = StockScreener('market.db')
result = sc.rank_by_factors(date(2024, 6, 30), factors={'PE': -1, 'ROE': 1})

# 宏观查询
mq = MacroQuery('market.db')
regime = mq.identify_macro_regime(date(2020, 1, 1), date(2024, 12, 31))

# 舆情查询
sq = SentimentQuery('market.db')
sentiment = sq.get_stock_sentiment('000001.SZ')

# 另类数据查询
aq = AlternativeQuery('market.db')
corr = aq.get_correlation_with_price('chain', 'steel_price', 'steel', '000001.SZ')
```

### 7.5 分析与融合

```python
from analytics.macro_factor_link import MacroFactorLink
from analytics.sentiment_factor import SentimentFactor
from analytics.multi_source_analysis import MultiSourceAnalysis

# 宏观-因子关联
mfl = MacroFactorLink('market.db')
corr = mfl.calculate_correlation('M2', 'PE')
macro_factor = mfl.build_macro_factor(['M2', 'CPI', 'PMI'])

# 舆情因子
sf = SentimentFactor('market.db')
signal = sf.get_sentiment_signals(date(2024, 6, 30))

# 多源融合
msa = MultiSourceAnalysis('market.db')
scores = msa.combine_score(['000001.SZ', '600000.SH'], date(2024, 6, 30))
screened = msa.multi_dimension_screen(date(2024, 6, 30), min_score=0.3)
```

### 7.6 回测引擎集成

```python
from adapters.engine_adapter import FactorTradingAdapter, DataLoaderV3Adapter

# Factor_Trading_v3.0 兼容
adapter = FactorTradingAdapter('market.db')
close_df = adapter.get_adj_price('close', adjust='forward')

# DataLoaderV3 格式
v3 = DataLoaderV3Adapter('market.db')
data = v3.to_data_loader_v3(
    stock_codes=['000001.SZ', '600000.SH'],
    start_date='2024-01-01',
    end_date='2024-12-31'
)
```

### 7.7 运行测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行单个测试文件
python -m pytest tests/test_connection.py -v
python -m pytest tests/test_schema.py -v
python -m pytest tests/test_query.py -v
python -m pytest tests/test_adapters.py -v
python -m pytest tests/test_extension.py -v
```

### 7.8 运行性能基准

```bash
python -m benchmarks.benchmark
```

---

## 8. 测试体系

### 测试文件与覆盖范围

| 测试文件 | 覆盖模块 | 测试类 |
|---------|---------|--------|
| test_connection.py | core/connection | TestDuckDBConnection |
| test_schema.py | core/schema | TestSchemaManager |
| test_query.py | query/price_query, query/factor_query, query/screen | TestPriceQuery, TestFactorQuery, TestStockScreener |
| test_adapters.py | adapters/pandas_adapter, adapters/engine_adapter | TestPandasAdapter, TestFactorTradingAdapter, TestDataLoaderV3Adapter |
| test_extension.py | core/metadata_manager, loaders/macro_loader, loaders/news_loader, loaders/alternative_loader, query/macro_query, query/sentiment_query, query/alternative_query, analytics/* | TestMetadataManager, TestMacroData, TestSentimentData, TestAlternativeData, TestAnalytics |

### 测试要点

- **连接管理**：单例模式验证、读写分离验证、上下文管理器验证、线程安全验证
- **表结构管理**：单表创建、全表创建、索引创建、表存在检查、统计信息、完整初始化
- **价格查询**：日 K 查询、价格矩阵、日期范围
- **因子查询**：因子查询、截面查询、因子矩阵、因子列表
- **条件选股**：条件筛选、分位数选股
- **适配器**：宽长格式转换、多级索引、技术指标、复权价格、DataLoaderV3 格式
- **元数据管理**：分类/数据源/字段的 CRUD、默认元数据初始化
- **宏观数据**：查询、矩阵、统计、最新值、加载器
- **舆情数据**：股票舆情、情感聚合、情感选股、加载器
- **另类数据**：数据查询、时间序列、最新数据、加载器
- **分析模块**：宏观-因子关联、宏观因子构建、多源综合评分

### 测试约定

- 每个测试类使用 `tempfile.mkdtemp()` 创建临时目录
- `tearDown` 中清理 `DuckDBConnection._instances` 缓存并删除临时数据库文件
- 测试数据直接通过 SQL INSERT 语句插入

---

## 9. 性能基准

### 基准测试方法

`benchmarks/benchmark.py` 对比 DuckDB 和 pandas 在四种查询场景下的性能：

1. **单股票历史查询**：按股票代码过滤并排序
2. **全市场截面查询**：按日期过滤并排序
3. **价格矩阵查询**：日期范围过滤
4. **聚合统计查询**：按股票分组聚合

### 参考性能数据

| 数据规模 | 查询类型 | DuckDB | pandas | 加速比 |
|---------|---------|--------|--------|--------|
| 252天 × 500股 | 聚合统计 | 3.8 ms | 6.0 ms | 1.6x |
| 252天 × 500股 | 矩阵查询 | 8.4 ms | 12.7 ms | 1.5x |
| 756天 × 1000股 | 聚合统计 | 6.5 ms | 32.7 ms | 5.0x |
| 756天 × 1000股 | 矩阵查询 | 14.8 ms | 59.3 ms | 4.0x |

**结论**：数据规模越大，DuckDB 向量化查询引擎的优势越明显。

---

## 附录：项目文件结构

```
Factor_DB/
├── core/                          # 核心引擎层
│   ├── __init__.py                # 导出: DuckDBConnection, SchemaManager, MetadataManager
│   ├── connection.py              # 连接管理（单例/读写分离/上下文管理器）
│   ├── schema.py                  # 表结构定义（14表 + 20索引）
│   ├── metadata_manager.py        # 元数据管理（分类/数据源/数据字典）
│   └── schema_extension.py        # 扩展表定义（独立参考）
├── loaders/                       # 数据加载层
│   ├── __init__.py                # 导出: BaseLoader, LoaderConfig, 5个具体加载器
│   ├── base.py                    # 加载器抽象基类 + LoaderConfig
│   ├── daily_loader.py            # 日 K 数据加载（pkl/csv/parquet/feather）
│   ├── level1_loader.py           # Level 1 分钟数据加载（feather）
│   ├── macro_loader.py            # 宏观数据加载
│   ├── news_loader.py             # 舆情数据加载（新闻/研报）
│   └── alternative_loader.py      # 另类数据加载
├── query/                         # 查询接口层
│   ├── __init__.py                # 导出: BaseQuery, QueryFilter, 5个具体查询器
│   ├── base.py                    # 查询抽象基类 + QueryFilter
│   ├── price_query.py             # 价格数据查询（日K/Level1/矩阵/复权）
│   ├── factor_query.py            # 因子数据查询（截面/矩阵/IC/统计）
│   ├── screen.py                  # 条件选股（SQL筛选/多因子打分/分位数）
│   ├── macro_query.py             # 宏观数据查询（矩阵/同比/环境识别）
│   ├── sentiment_query.py         # 舆情数据查询（聚合/趋势/选股）
│   └── alternative_query.py       # 另类数据查询（时间序列/相关性）
├── analytics/                     # 分析模块
│   ├── __init__.py
│   ├── macro_factor_link.py       # 宏观-因子关联（相关性/宏观因子构建）
│   ├── sentiment_factor.py        # 舆情因子（信号生成/事件影响分析）
│   └── multi_source_analysis.py   # 多源融合（综合评分/多维筛选）
├── adapters/                      # 适配器层
│   ├── __init__.py                # 导出: PandasAdapter, FactorTradingAdapter, DataLoaderV3Adapter
│   ├── pandas_adapter.py          # pandas 格式转换 + 技术指标
│   └── engine_adapter.py          # 回测引擎兼容层
├── utils/                         # 工具模块
│   ├── __init__.py
│   ├── config.py                  # 配置管理（JSON/环境变量）
│   ├── logger.py                  # 日志配置（彩色输出/文件记录）
│   └── validators.py              # 数据验证（价格/因子/Level1/股票代码）
├── tests/                         # 测试套件
│   ├── __init__.py
│   ├── test_connection.py         # 连接管理测试
│   ├── test_schema.py             # 表结构测试
│   ├── test_query.py              # 查询接口测试
│   ├── test_adapters.py           # 适配器测试
│   └── test_extension.py          # 扩展模块测试
├── benchmarks/                    # 性能基准测试
│   └── benchmark.py               # DuckDB vs pandas 对比
├── docs/                          # 文档
│   ├── ARCHITECTURE.md            # 架构设计文档
│   ├── LOCAL_DATA_STORAGE_ANALYSIS.md  # 本地存储方案分析
│   ├── LEVEL1_DUCKDB_ANALYSIS.md       # Level 1 数据支持性分析
│   └── FUTURE_EXTENSION_PLAN.md        # 扩展方案设计文档
├── README.md                      # 项目说明
└── requirements.txt               # 依赖管理
```
