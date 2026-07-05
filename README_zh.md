# Factor_DB

基于 DuckDB 的高性能量化金融数据存储系统，专为 A 股市场设计。

## 核心特性

- **高性能**: 向量化查询引擎，比 pandas 快 2-5 倍（数据规模越大优势越明显）
- **低耦合**: 模块化设计，各组件独立可替换
- **高内聚**: 单一职责，每个模块只做一件事
- **兼容性**: 支持 Level 1 分钟数据、日 K 数据、因子数据
- **易扩展**: 插件式架构，易于添加新数据源
- **多数据源**: 支持宏观经济数据、舆情数据、另类数据存储与查询
- **多源分析**: 支持宏观-因子关联、舆情因子、多维度融合分析

## 项目结构

```
Factor_DB/
├── core/                    # 核心引擎层
│   ├── connection.py        # DuckDB 连接管理（单例模式、线程安全）
│   ├── schema.py            # 数据库表结构定义与索引管理
│   └── metadata_manager.py  # 元数据统一管理器（数据分类/数据源/数据字典）
├── loaders/                 # 数据加载层
│   ├── base.py              # 加载器基类（抽象接口）
│   ├── level1_loader.py     # Level 1 分钟数据加载（Feather 格式）
│   ├── daily_loader.py      # 日 K 数据加载（pkl/csv/parquet/feather）
│   ├── macro_loader.py      # 宏观经济数据加载（GDP/CPI/PMI/M2等）
│   ├── news_loader.py       # 舆情数据加载（新闻/研报情感分析）
│   └── alternative_loader.py # 另类数据加载（卫星/产业链/电商数据）
├── query/                   # 查询接口层
│   ├── price_query.py       # 价格数据查询（日 K / Level 1 / 矩阵）
│   ├── factor_query.py      # 因子数据查询（截面 / IC / 统计）
│   ├── screen.py            # 条件选股（多因子打分 / 分位数）
│   ├── macro_query.py       # 宏观数据查询（矩阵/同比/统计/宏观环境识别）
│   ├── sentiment_query.py   # 舆情数据查询（股票舆情/情感聚合/选股）
│   └── alternative_query.py # 另类数据查询（时间序列/与价格相关性）
├── analytics/               # 分析模块（新增）
│   ├── macro_factor_link.py # 宏观-因子关联分析（相关性/宏观因子构建）
│   ├── sentiment_factor.py  # 舆情因子构建与信号生成
│   └── multi_source_analysis.py # 多源数据融合分析（综合评分/多维筛选）
├── adapters/                # 适配器层（兼容性）
│   ├── pandas_adapter.py    # pandas DataFrame 格式转换、技术指标
│   └── engine_adapter.py    # Factor_Trading_v3.0 回测引擎兼容层
├── utils/                   # 工具模块
│   ├── logger.py            # 日志配置（彩色输出、文件记录）
│   ├── config.py            # 配置管理（JSON/环境变量）
│   └── validators.py        # 数据验证（价格/因子/Level 1）
├── tests/                   # 测试套件（49 个测试用例，全部通过）
├── benchmarks/              # 性能基准测试
├── docs/                    # 文档
│   ├── ARCHITECTURE.md      # 架构设计文档
│   ├── LOCAL_DATA_STORAGE_ANALYSIS.md  # 本地存储方案分析
│   ├── LEVEL1_DUCKDB_ANALYSIS.md       # Level 1 数据支持性分析
│   └── FUTURE_EXTENSION_PLAN.md        # 扩展方案设计文档
└── requirements.txt         # 依赖管理
```

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 初始化数据库

```python
from core.connection import DuckDBConnection
from core.schema import SchemaManager

conn = DuckDBConnection('market.db')
schema = SchemaManager(conn)
schema.init_database()  # 创建所有表和索引
```

### 加载日 K 数据

```python
from loaders.daily_loader import DailyLoader

loader = DailyLoader()
count = loader.load(Path('E:/Ashare_data/market_data'))
print(f"加载完成: {count} 条记录")
```

### 加载 Level 1 数据

```python
from loaders.level1_loader import Level1Loader

loader = Level1Loader()
count = loader.load(Path('E:/Level 1 Data'))
print(f"加载完成: {count} 条记录")
```

### 查询价格数据

```python
from query.price_query import PriceQuery
from datetime import date

query = PriceQuery('market.db')

# 查询日 K
df = query.get_daily(
    stock_codes=['000001.SZ', '600000.SH'],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31)
)

# 获取价格矩阵（用于回测）
matrix = query.get_price_matrix(
    field='close',
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    adjust='forward'  # 前复权
)
```

### 查询因子数据

```python
from query.factor_query import FactorQuery

query = FactorQuery('market.db')

# 获取因子截面
df = query.get_cross_section('PE', date(2024, 6, 30))

# 获取因子矩阵
matrix = query.get_factor_matrix(['PE', 'PB', 'ROE'])

# 计算 IC 序列
ic_df = query.get_ic_series('PE', forward_return_days=1)
```

### 条件选股

```python
from query.screen import StockScreener

screener = StockScreener('market.db')

# 多因子打分
result = screener.rank_by_factors(
    trade_date=date(2024, 6, 30),
    factors={'PE': -1, 'ROE': 1, 'PB': -1},  # 方向: 1正向, -1负向
    limit=50
)
```

### 宏观经济数据

```python
from loaders.macro_loader import MacroLoader
from query.macro_query import MacroQuery

# 加载宏观数据
loader = MacroLoader('market.db')
count = loader.load('macro_data.csv')

# 查询宏观数据
query = MacroQuery('market.db')
df = query.get_macro_data(['GDP', 'CPI', 'M2'], date(2020, 1, 1), date(2024, 12, 31))

# 获取宏观数据矩阵（日期 x 指标）
matrix = query.get_macro_matrix(['GDP', 'CPI', 'M2'], date(2020, 1, 1), date(2024, 12, 31))

# 计算同比
yoy_df = query.calculate_yoy('CPI')

# 识别宏观环境
regime = query.identify_macro_regime(date(2020, 1, 1), date(2024, 12, 31))
```

### 舆情数据

```python
from loaders.news_loader import NewsLoader
from query.sentiment_query import SentimentQuery

# 加载舆情数据
loader = NewsLoader('market.db')
loader.load_news(news_df)
loader.load_reports(report_df)

# 查询舆情
query = SentimentQuery('market.db')

# 获取股票舆情
df = query.get_stock_sentiment('000001.SZ', date(2024, 1, 1), date(2024, 12, 31))

# 情感聚合
agg = query.get_sentiment_aggregation(['000001.SZ', '600000.SH'], freq='W')

# 根据情感选股
stocks = query.get_sentiment_stocks('positive', date(2024, 6, 30), limit=50)
```

### 另类数据

```python
from loaders.alternative_loader import AlternativeLoader
from query.alternative_query import AlternativeQuery

# 加载另类数据
loader = AlternativeLoader('market.db')
count = loader.load(alternative_df)

# 查询另类数据
query = AlternativeQuery('market.db')
df = query.get_data('satellite', date(2024, 1, 1), date(2024, 12, 31))

# 与价格相关性分析
corr = query.get_correlation_with_price('000001.SZ', 'satellite')
```

### 多源数据分析

```python
from analytics.macro_factor_link import MacroFactorLink
from analytics.sentiment_factor import SentimentFactor
from analytics.multi_source_analysis import MultiSourceAnalysis

# 宏观-因子关联分析
mfl = MacroFactorLink('market.db')
corr = mfl.calculate_correlation('M2', 'PE')
macro_factor = mfl.build_macro_factor(['M2', 'CPI', 'PMI'])

# 舆情因子
sf = SentimentFactor('market.db')
sentiment_signal = sf.build_sentiment_factor(date(2024, 6, 30))

# 多源融合分析
msa = MultiSourceAnalysis('market.db')
scores = msa.combine_score(date(2024, 6, 30), ['000001.SZ', '600000.SH'])
screened = msa.multi_dimension_screen(date(2024, 6, 30), min_sentiment=0.3)
```

### 与回测引擎集成

```python
from adapters.engine_adapter import FactorTradingAdapter

adapter = FactorTradingAdapter('market.db')

# 兼容原有接口
close_df = adapter.get_adj_price('close', adjust='forward')
trade_dates = adapter.get_trade_dates('2024-01-01', '2024-12-31')
stock_list = adapter.get_stock_list()
```

## 性能基准

| 数据规模 | 查询类型 | DuckDB | pandas | 加速比 |
|---------|---------|--------|--------|--------|
| 252天 x 500股 | 聚合统计 | 3.8 ms | 6.0 ms | 1.6x |
| 252天 x 500股 | 矩阵查询 | 8.4 ms | 12.7 ms | 1.5x |
| 756天 x 1000股 | 聚合统计 | 6.5 ms | 32.7 ms | **5.0x** |
| 756天 x 1000股 | 矩阵查询 | 14.8 ms | 59.3 ms | **4.0x** |

数据规模越大，DuckDB 的向量化查询优势越明显。

## 运行测试

```bash
python -m pytest tests/ -v
```

当前测试覆盖:
- 连接管理（单例、读写分离、上下文管理器、线程安全）
- 表结构管理（创建表、索引、统计信息）
- 价格查询（日 K、Level 1、价格矩阵、日期范围）
- 因子查询（因子数据、截面、矩阵、IC 计算）
- 条件选股（筛选、分位数、多因子打分）
- 适配器（pandas 转换、引擎兼容、DataLoaderV3 格式）
- 元数据管理（分类、数据源、数据字典）
- 宏观数据（加载、查询、矩阵、同比、统计、宏观环境）
- 舆情数据（加载、查询、聚合、情感选股）
- 另类数据（加载、查询、时间序列、相关性）
- 分析模块（宏观-因子关联、舆情因子、多源融合）

## 架构设计

```
┌─────────────────────────────────────────────────────────────────┐
│                    应用层 (用户代码)                             │
│         BacktestEngine / FactorPipeline / MultiSourceStrategy    │
├─────────────────────────────────────────────────────────────────┤
│                    适配器层 (adapters)                            │
│    pandas_adapter / engine_adapter / analysis_adapter           │
├─────────────────────────────────────────────────────────────────┤
│                    查询层 (query)                                 │
│    price_query / factor_query / screen                           │
│    macro_query / sentiment_query / alternative_query             │
├─────────────────────────────────────────────────────────────────┤
│                    分析层 (analytics)                             │
│    macro_factor_link / sentiment_factor / multi_source_analysis │
├─────────────────────────────────────────────────────────────────┤
│                    核心层 (core)                                  │
│    connection / schema / metadata_manager                        │
├─────────────────────────────────────────────────────────────────┤
│                    加载层 (loaders)                               │
│    level1_loader / daily_loader / factor_loader                 │
│    macro_loader / news_loader / alternative_loader               │
└─────────────────────────────────────────────────────────────────┘
```

## 数据库表结构

### 核心数据表

| 表名 | 用途 | 主键 |
|------|------|------|
| `daily_prices` | 日 K 数据 | (trade_date, stock_code) |
| `level1_snapshots` | Level 1 分钟数据 | (trade_date, trade_time, stock_code) |
| `stock_info` | 股票基本信息 | stock_code |
| `trade_calendar` | 交易日历 | trade_date |
| `factor_data` | 因子数据 | (trade_date, stock_code, factor_name) |
| `factor_info` | 因子元数据 | factor_name |

### 扩展数据表

| 表名 | 用途 | 主键 |
|------|------|------|
| `data_categories` | 数据分类（宏观/舆情/另类） | category_id |
| `data_sources` | 数据源信息 | source_id |
| `data_dictionary` | 数据字典（字段说明） | field_id |
| `macro_data` | 宏观经济数据 | (trade_date, indicator_id, value_type) |
| `macro_indicators` | 宏观指标信息 | indicator_id |
| `news_sentiment` | 新闻舆情数据 | news_id |
| `report_sentiment` | 研报舆情数据 | report_id |
| `alternative_data` | 另类数据 | data_id |
| `alternative_types` | 另类数据类型 | data_type |

## 文档

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) - 架构设计文档
- [LOCAL_DATA_STORAGE_ANALYSIS.md](docs/LOCAL_DATA_STORAGE_ANALYSIS.md) - 本地数据存储方案深度分析
- [LEVEL1_DUCKDB_ANALYSIS.md](docs/LEVEL1_DUCKDB_ANALYSIS.md) - Level 1 行情数据 DuckDB 支持性分析
- [FUTURE_EXTENSION_PLAN.md](docs/FUTURE_EXTENSION_PLAN.md) - 宏观/舆情/另类数据扩展方案

## 依赖

- duckdb >= 0.10.0
- pandas >= 2.0.0
- numpy >= 1.24.0
- pyarrow >= 15.0.0
- pytest >= 8.0.0

## License

MIT
