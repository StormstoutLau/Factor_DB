# Factor_DB

A high-performance quantitative financial data storage system built on **DuckDB**, designed for the China A-share market.

## Key Features

- **High Performance**: Vectorized query engine, 2-5x faster than pandas (advantage grows with data scale)
- **Wide Table Architecture**: Factor data stored as `(trade_date, stock_code)` primary key with one column per factor — 50x lower index memory vs. traditional EAV long-table design
- **Dual-Track Storage**: `factor_wide` (latest version for daily queries) + `factor_history` (all versions for PIT backtesting & audit)
- **Level 1 Parquet Partitioning**: Hive-style partitioned Parquet files with partition pruning — handles 2.1 billion rows (21 billion tick records) on 32GB RAM
- **PIT (Point-in-Time) Queries**: Query factor/price state at any historical timestamp with NULL-safe filtering
- **Modular Design**: Loosely coupled, highly cohesive — each module has a single responsibility
- **Multi-Source Analytics**: Macro-factor linkage, sentiment factors, multi-dimensional fusion analysis
- **Plugin Architecture**: Easily extendable to new data sources

## Project Structure

```
Factor_DB/
├── core/                        # Core Engine
│   ├── connection.py            #   DuckDB connection pool (singleton, thread-safe, read/write split)
│   ├── schema.py                #   Schema management (wide table, history table, migration, PIVOT/UNPIVOT)
│   └── metadata_manager.py      #   Metadata manager (categories / sources / dictionary)
├── loaders/                     # Data Loaders
│   ├── base.py                  #   Abstract base class
│   ├── daily_loader.py          #   Daily OHLCV + wide-table factor loader (Upsert + dual-write)
│   ├── level1_loader.py         #   Level 1 tick data loader (Feather → Parquet partition converter)
│   ├── csmar_loader.py          #   CSMAR factor data loader (zip/xlsx auto-parsing)
│   ├── macro_loader.py          #   Macro economic data loader (GDP/CPI/PMI/M2)
│   ├── news_loader.py           #   News & report sentiment loader
│   └── alternative_loader.py    #   Alternative data loader (satellite/supply chain/e-commerce)
├── query/                       # Query Layer
│   ├── base.py                  #   Base query class (SQL builder, PIT subquery with NULL filtering)
│   ├── price_query.py           #   Price queries (daily K / Level 1 Parquet / price matrix)
│   ├── factor_query.py          #   Factor queries (wide-table first, PIT fallback to history)
│   ├── screen.py                #   Stock screening (multi-factor scoring / quantile)
│   ├── macro_query.py           #   Macro data queries (matrix / YoY / regime identification)
│   ├── sentiment_query.py       #   Sentiment queries (stock sentiment / aggregation / screening)
│   ├── alternative_query.py     #   Alternative data queries (time series / price correlation)
│   ├── osint_query.py           #   OSINT (open-source intelligence) queries
│   └── cache.py                 #   Query result cache (LRU)
├── analytics/                   # Analytics Modules
│   ├── macro_factor_link.py     #   Macro-factor correlation analysis
│   ├── sentiment_factor.py      #   Sentiment factor construction & signal generation
│   └── multi_source_analysis.py #   Multi-source fusion analysis (composite scoring)
├── adapters/                    # Adapter Layer (Compatibility)
│   ├── pandas_adapter.py        #   pandas DataFrame conversion & technical indicators
│   ├── engine_adapter.py        #   Backtesting engine compatibility layer
│   └── factor_calculator.py     #   Factor calculation engine
├── osint/                       # Open-Source Intelligence
│   ├── collectors/              #   Data collectors (AKShare / World Bank / RSS / gov.cn)
│   ├── pipeline.py              #   Collection pipeline
│   └── registry.py              #   Source registry
├── utils/                       # Utilities
│   ├── logger.py                #   Logging (colored output, file rotation)
│   ├── config.py                #   Configuration (JSON / env vars)
│   ├── validators.py            #   Data validation (price / factor / Level 1)
│   └── code_utils.py            #   Stock code utilities
├── scripts/                     # Import & Migration Scripts
│   ├── import_phase1_ashare.py  #   Phase 1: A-share daily data import
│   ├── import_phase2_level1.py  #   Phase 2: Level 1 data import
│   ├── import_phase3_csmar.py   #   Phase 3: CSMAR factor data import
│   ├── convert_level1_parquet.py#   Feather → Parquet partition conversion
│   └── rebuild_factor_data.py   #   Rebuild factor_data view from wide table
├── tests/                       # Test Suite (240 tests, all passing)
├── benchmarks/                  # Performance Benchmarks
├── docs/                        # Documentation
└── cli.py                       # CLI Entry Point
```

## Quick Start

### Installation

```bash
pip install -r requirements.txt
```

### Initialize Database

```python
from core.connection import DuckDBConnection
from core.schema import SchemaManager

conn = DuckDBConnection('factor_db.duckdb')
schema = SchemaManager(conn)
schema.init_database()  # Create all tables and indexes
```

### Load Factor Data (Wide Table Architecture)

```python
from loaders.daily_loader import DailyLoader
import pandas as pd

loader = DailyLoader()

# Load factor into wide table (auto-creates column if needed)
df = pd.DataFrame({
    'trade_date': ['2024-01-02', '2024-01-02'],
    'stock_code': ['000001', '000002'],
    'factor_value': [15.3, 22.1],
})
count = loader.load_factor_to_wide(df, factor_name='PE_ratio')
print(f"Loaded {count} rows into factor_wide")
```

### Load Daily Price Data

```python
from loaders.daily_loader import DailyLoader

loader = DailyLoader()
count = loader.load(Path('E:/Ashare_data/market_data'))
print(f"Loaded {count} records")
```

### Convert Level 1 Feather → Parquet Partitions

```python
from loaders.level1_loader import Level1ParquetConverter

converter = Level1ParquetConverter()
count = converter.convert_directory(
    source_dir=Path('E:/Level 1 Data'),
    output_dir=Path('data/level1_parquet')
)
print(f"Converted {count} files to Parquet partitions")
```

### Query Price Data

```python
from query.price_query import PriceQuery
from datetime import date

query = PriceQuery('factor_db.duckdb')

# Daily K-line
df = query.get_daily(
    stock_codes=['000001', '600000'],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31)
)

# Price matrix for backtesting (NULL-safe PIT filtering)
matrix = query.get_price_matrix(
    field='close',
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    adjust='forward'  # Forward-adjusted
)
```

### Query Level 1 Data (Parquet Partition Pruning)

```python
from query.price_query import PriceQuery
from datetime import date, time

query = PriceQuery(
    'factor_db.duckdb',
    level1_parquet_dir='data/level1_parquet'
)

df = query.get_level1(
    stock_codes=['000001'],
    trade_date=date(2023, 3, 30),
    start_time=time(9, 30),
    end_time=time(15, 0)
)
```

### Query Factor Data (Wide Table First, PIT Fallback)

```python
from query.factor_query import FactorQuery
from datetime import date

query = FactorQuery('factor_db.duckdb')

# Cross-section (from wide table)
df = query.get_cross_section('PE_ratio', date(2024, 6, 30))

# Factor matrix
matrix = query.get_factor_matrix(['PE_ratio', 'PB_ratio', 'ROE'])

# PIT query (falls back to factor_history)
df = query.get_factor('PE_ratio', as_of=date(2024, 6, 30))
```

### Stock Screening

```python
from query.screen import StockScreener
from datetime import date

screener = StockScreener('factor_db.duckdb')

result = screener.rank_by_factors(
    trade_date=date(2024, 6, 30),
    factors={'PE_ratio': -1, 'ROE': 1, 'PB_ratio': -1},  # 1=positive, -1=negative
    limit=50
)
```

### Backtesting Engine Integration

```python
from adapters.engine_adapter import FactorTradingAdapter

adapter = FactorTradingAdapter('factor_db.duckdb')

close_df = adapter.get_adj_price('close', adjust='forward')
trade_dates = adapter.get_trade_dates('2024-01-01', '2024-12-31')
stock_list = adapter.get_stock_list()
```

## Architecture

### Wide Table Design (Solution A)

Traditional EAV long-table stores each factor as a row, leading to massive row counts and index memory bloat. The wide table design stores each factor as a column:

```
factor_wide (PK: trade_date, stock_code)
├── trade_date      DATE
├── stock_code      VARCHAR
├── PE_ratio        DOUBLE
├── PB_ratio        DOUBLE
├── ROE             DOUBLE
├── ...             (117+ factor columns)
└── loaded_at       TIMESTAMP
```

**Performance**: Index memory reduced ~50x (PK from `(factor_code, trade_date, stock_code)` → `(trade_date, stock_code)`).

### Dual-Track Storage

| Table | Purpose | Data |
|-------|---------|------|
| `factor_wide` | Daily queries (latest version) | Upsert on `(trade_date, stock_code)` |
| `factor_history` | PIT backtesting & audit | Append-only, all versions |
| `factor_data` (view) | Backward compatibility | `UNPIVOT` of `factor_wide` |

### Level 1 Parquet Partitioning

```
data/level1_parquet/
├── trade_date=2014-01-02/data.parquet
├── trade_date=2014-01-03/data.parquet
├── ...
└── trade_date=2023-03-30/data.parquet
```

DuckDB reads Parquet with `hive_partitioning=true` — only relevant partitions are scanned (partition pruning + Zonemap index).

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                    Application Layer                             │
│         BacktestEngine / FactorPipeline / MultiSourceStrategy    │
├─────────────────────────────────────────────────────────────────┤
│                    Adapter Layer (adapters/)                      │
│    pandas_adapter / engine_adapter / factor_calculator           │
├─────────────────────────────────────────────────────────────────┤
│                    Query Layer (query/)                           │
│    price_query / factor_query / screen / macro_query             │
│    sentiment_query / alternative_query / osint_query             │
├─────────────────────────────────────────────────────────────────┤
│                    Analytics Layer (analytics/)                   │
│    macro_factor_link / sentiment_factor / multi_source_analysis │
├─────────────────────────────────────────────────────────────────┤
│                    Core Layer (core/)                             │
│    connection / schema / metadata_manager                        │
├─────────────────────────────────────────────────────────────────┤
│                    Loader Layer (loaders/)                        │
│    daily_loader / level1_loader / csmar_loader                   │
│    macro_loader / news_loader / alternative_loader               │
└─────────────────────────────────────────────────────────────────┘
```

## Database Schema

### Core Tables

| Table | Purpose | Primary Key |
|-------|---------|-------------|
| `daily_prices` | Daily OHLCV data | (trade_date, stock_code) |
| `factor_wide` | Factor data (wide format) | (trade_date, stock_code) |
| `factor_history` | Factor history (all versions, PIT) | (trade_date, stock_code, factor_name, loaded_at) |
| `factor_data` | Backward-compatible view (UNPIVOT) | — |
| `stock_info` | Stock metadata | stock_code |
| `trade_calendar` | Trading calendar | trade_date |

### Level 1 Data

| Storage | Purpose |
|---------|---------|
| `data/level1_parquet/` | Hive-partitioned Parquet files (2,250 days × 7,337 stocks = 2.1B rows) |

### Extension Tables

| Table | Purpose |
|-------|---------|
| `macro_data` / `macro_indicators` | Macro economic data |
| `news_sentiment` / `report_sentiment` | Sentiment data |
| `alternative_data` / `alternative_types` | Alternative data |
| `data_categories` / `data_sources` / `data_dictionary` | Metadata |

## Performance Benchmarks

| Data Scale | Query Type | DuckDB | pandas | Speedup |
|-----------|-----------|--------|--------|---------|
| 252 days × 500 stocks | Aggregation | 3.8 ms | 6.0 ms | 1.6x |
| 252 days × 500 stocks | Matrix query | 8.4 ms | 12.7 ms | 1.5x |
| 756 days × 1000 stocks | Aggregation | 6.5 ms | 32.7 ms | **5.0x** |
| 756 days × 1000 stocks | Matrix query | 14.8 ms | 59.3 ms | **4.0x** |
| Level 1: 2.1B rows, single day | Parquet scan | 0.9 s | — | — |
| Level 1: 2.1B rows, single stock | Partition pruning | 0.05 s | — | — |

## Testing

```bash
python -m pytest tests/ -v
```

**240 tests, all passing.** Coverage includes:

| Test Suite | Tests | What's Covered |
|-----------|-------|----------------|
| `test_connection.py` | — | Connection pool (singleton, read/write split, thread-safe) |
| `test_schema.py` | — | Schema management (tables, indexes, wide table creation) |
| `test_wide_loader.py` | 9 | Wide table loading (Upsert, dual-write, NaN handling) |
| `test_wide_query.py` | 10 | Wide table queries (cross-section, matrix, PIT fallback) |
| `test_migration.py` | 6 | Data migration (factor_data → factor_wide + factor_history) |
| `test_level1_parquet.py` | 12 | Feather→Parquet conversion, partition pruning, PriceQuery adapter |
| `test_pit_null_filter.py` | 5 | PIT NULL filtering (root cause fix for all-NaN price matrix) |
| `test_query.py` | — | Price/factor queries, stock screening |
| `test_daily_loader.py` | — | Daily data loading |
| `test_adapters.py` | — | pandas adapter, engine compatibility |
| `test_extension.py` | — | Macro/news/alternative data loading & query |
| `test_osint.py` | — | OSINT collectors & pipeline |
| `test_cache.py` | — | Query cache (LRU) |

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — Architecture design
- [LOCAL_DATA_STORAGE_ANALYSIS.md](docs/LOCAL_DATA_STORAGE_ANALYSIS.md) — Local storage deep-dive
- [LEVEL1_DUCKDB_ANALYSIS.md](docs/LEVEL1_DUCKDB_ANALYSIS.md) — Level 1 data feasibility analysis
- [FUTURE_EXTENSION_PLAN.md](docs/FUTURE_EXTENSION_PLAN.md) — Extension roadmap (macro/sentiment/alternative)
- [IMPROVEMENT_PLAN.md](docs/IMPROVEMENT_PLAN.md) — PIT query & performance improvement plan

## Dependencies

- duckdb >= 0.10.0
- pandas >= 2.0.0
- numpy >= 1.24.0
- pyarrow >= 15.0.0
- pytest >= 8.0.0

## Data Sources

| Source | Data | Volume |
|--------|------|--------|
| CSMAR (国泰安) | 117 financial factors | 871M rows |
| A-share market data | Daily OHLCV | 43M rows |
| Level 1 tick data | Minute-level snapshots | 2.1B rows (32GB Parquet) |

## License

MIT
