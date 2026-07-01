# P1/P2 详细方案分析

> 续接 [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md) 中的 P0 分析

---

## P1 — 架构增强

### P1.1 查询缓存层

#### 现状分析

[QueryConfig](file:///f:/Coding/Factor_DB/utils/config.py#L36-L41) 已定义 `cache_size` 和 `cache_ttl` 字段，但 [BaseQuery](file:///f:/Coding/Factor_DB/query/base.py) 中完全没有使用：

```python
# utils/config.py - 有字段但未使用
@dataclass
class QueryConfig:
    cache_size: int = 1000
    cache_ttl: int = 300
    max_rows: int = 1000000
    timeout: int = 30
```

**重复查询场景**：
- 回测循环中每天调用 `get_cross_section('PE', date)` → 因子截面一天内不变，无需重复查询
- `get_price_matrix` 在 `DataLoaderV3Adapter.to_data_loader_v3` 中对每个字段各调用一次 → 同一日期范围可复用

#### 方案设计

采用**两级缓存**策略：

**L1：方法级 LRU 缓存**（`functools.lru_cache`）

```python
from functools import lru_cache

class CachedBaseQuery(BaseQuery):
    """带缓存的查询基类"""
    
    def __init__(self, db_path, cache_size=128):
        super().__init__(db_path)
        self._setup_cache(cache_size)
    
    def _setup_cache(self, cache_size):
        # 对关键方法应用 LRU 缓存
        self._cached_execute = lru_cache(maxsize=cache_size)(self._execute_query)
    
    def cache_clear(self):
        """清空缓存"""
        self._cached_execute.cache_clear()
    
    def cache_info(self):
        """缓存统计"""
        return self._cached_execute.cache_info()
```

**L2：结果集缓存**（SQL 结果复用）

```python
class QueryCache:
    """SQL 查询结果缓存"""
    
    def __init__(self, max_size=1000, ttl=300):
        self._cache = {}  # {hashed_sql: (timestamp, dataframe)}
        self.max_size = max_size
        self.ttl = ttl
    
    def get(self, sql):
        key = hashlib.md5(sql.encode()).hexdigest()
        if key in self._cache:
            ts, df = self._cache[key]
            if time.time() - ts < self.ttl:
                return df.copy()
        return None
    
    def put(self, sql, df):
        key = hashlib.md5(sql.encode()).hexdigest()
        if len(self._cache) >= self.max_size:
            # 淘汰最旧的条目
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.time(), df.copy())
    
    def clear(self):
        self._cache.clear()
```

**集成方式**：修改 `BaseQuery._execute_query` 增加缓存逻辑：

```python
def _execute_query(self, sql: str) -> pd.DataFrame:
    # 尝试从缓存获取
    if self._cache is not None:
        cached = self._cache.get(sql)
        if cached is not None:
            return cached
    
    # 执行查询
    df = self.conn.fetchdf(sql)
    
    # 存入缓存
    if self._cache is not None:
        self._cache.put(sql, df)
    
    return df
```

**影响范围**：
- 所有 Query 类自动受益（无需修改子类代码）
- `BaseQuery.__init__` 新增 `cache_size` 参数
- `QueryCache` 类新增到 `query/cache.py`

---

### P1.2 消除命名冲突与代码重复

#### 问题 1：LoaderConfig 命名冲突

两个文件各自定义了 `LoaderConfig`，字段不同：

| 文件 | 字段 | 用途 |
|------|------|------|
| [loaders/base.py](file:///f:/Coding/Factor_DB/loaders/base.py#L21-L27) | db_path, batch_size, skip_existing, validate_data, show_progress | 加载器使用 |
| [utils/config.py](file:///f:/Coding/Factor_DB/utils/config.py#L26-L32) | batch_size, skip_existing, validate_data, show_progress, max_workers | 主配置的子配置 |

**解决方案**：将 `utils/config.py` 中的 `LoaderConfig` 重命名为 `AppLoaderConfig`，作为主配置的聚合子配置，不再与加载器类混淆。

#### 问题 2：schema_extension.py 重复定义

[schema_extension.py](file:///f:/Coding/Factor_DB/core/schema_extension.py) 中的 `TABLES_EXTENSION` 和 `INDEXES_EXTENSION` 与 [schema.py](file:///f:/Coding/Factor_DB/core/schema.py#L37-L287) 中的 `TABLES` 和 `INDEXES` 完全重复（扩展表已经内联到 `SchemaManager` 中）。

**解决方案**：删除 `schema_extension.py`，保留 `merge_with_extension()` 函数逻辑（如果外部有引用），或直接废弃。

**影响范围检查**：
- `schema_extension.py` 未被任何文件 import（可以安全删除）
- `utils/config.py` 的 `LoaderConfig` 被 `Config` 类内部使用，重命名即可

---

### P1.3 因子计算引擎扩展

#### 现状分析

[PandasAdapter.add_technical_indicators](file:///f:/Coding/Factor_DB/adapters/pandas_adapter.py#L137-L199) 当前仅支持 4 个指标：

| 指标 | 实现方式 | 问题 |
|------|---------|------|
| MA{n} | `rolling(n).mean()` | OK |
| RSI | 14日固定窗口 | 不可配置窗口 |
| MACD | EMA12-EMA26 | 不可配置参数 |
| BBANDS | MA20 ± 2σ | 不可配置参数 |

**缺失的能力**：无 Alpha 因子定量计算（参考 Qlib 的 Alpha158）。

#### 方案设计

**Phase 1**：扩展 `PandasAdapter` 为更完整的因子计算器

新增因子类别：

```python
class FactorCalculator:
    """因子计算引擎"""
    
    # 量价因子
    @staticmethod
    def momentum(df, periods=[5, 10, 20]):
        """动量因子"""
    
    @staticmethod
    def volatility(df, window=20):
        """波动率因子"""
    
    @staticmethod
    def turnover_adj(df):
        """换手率调整因子"""
    
    @staticmethod
    def volume_price_trend(df):
        """量价趋势因子"""
    
    # 技术因子
    @staticmethod
    def bollinger_position(df):
        """布林带位置"""
    
    @staticmethod
    def atr(df, window=14):
        """平均真实波幅"""
    
    # 截面因子
    @staticmethod
    def industry_neutralize(df, factor_col, industry_col):
        """行业中性化"""
    
    @staticmethod
    def market_cap_neutralize(df, factor_col, cap_col):
        """市值中性化"""
```

**设计原则**：所有计算使用 pandas 向量化操作，对每只股票独立计算（通过 `groupby(stock_code)`）。

---

## P2 — 体验优化

### P2.1 补全类型标注

#### 当前状态扫描

| 文件 | 状态 | 缺失标注 |
|------|------|---------|
| core/connection.py | 完整 | — |
| core/schema.py | 完整 | — |
| core/metadata_manager.py | 完整 | — |
| core/schema_extension.py | 完整 | — |
| loaders/base.py | 完整 | — |
| **loaders/daily_loader.py** | 部分 | `_load_single_file`, `_load_directory` 返回类型 |
| **loaders/level1_loader.py** | 部分 | 同上 |
| **loaders/macro_loader.py** | 部分 | 多个内部方法 |
| **loaders/news_loader.py** | 缺 | 多个方法无类型标注 |
| **loaders/alternative_loader.py** | 部分 | 内部方法 |
| query/base.py | 完整 | — |
| query/price_query.py | 完整 | — |
| query/factor_query.py | 完整 | — |
| query/screen.py | 完整 | — |
| query/macro_query.py | 完整 | — |
| query/sentiment_query.py | 完整 | — |
| query/alternative_query.py | 完整 | — |
| analytics/macro_factor_link.py | 完整 | — |
| analytics/sentiment_factor.py | 完整 | — |
| analytics/multi_source_analysis.py | 完整 | — |
| adapters/pandas_adapter.py | 完整 | — |
| adapters/engine_adapter.py | 完整 | — |
| utils/config.py | 完整 | — |
| utils/logger.py | 完整 | — |
| utils/validators.py | 完整 | — |

**策略**：集中于 Loader 层（5 个文件）补全类型标注，Query 层已基本完整。

### P2.2 CLI 工具

#### 设计

```bash
# 数据库管理
factor-db init --db market.db
factor-db stats --db market.db

# 数据加载
factor-db load daily --db market.db --path ./data/
factor-db load level1 --db market.db --path ./level1/

# 数据查询
factor-db query --db market.db --sql "SELECT ..."
factor-db list-factors --db market.db

# 工具
factor-db cache-clear --db market.db
factor-db compact --db market.db --before 2024-01-01
```

#### 实现

使用 Python 标准库 `argparse`，创建 `cli.py` 作为入口点，通过 `setup.py` / `pyproject.toml` 注册 `factor-db` 命令。

### P2.3 Jupyter 可视化

**方案**：提供 `%factor_db` IPython magic，在 Notebook 中直接预览数据。

```python
# 在 Jupyter 中使用
%load_ext factor_db

%factor_db connect market.db
%factor_db stats
%factor_db factors
%factor_db query "SELECT COUNT(*) FROM daily_prices"
```

---

## 实施顺序总览

```
P0 ──→ PIT 数据版本管理  ──→ DailyLoader 测试
         │
P1 ──→ 消除命名冲突  ──→ 查询缓存  ──→ 因子计算引擎
         │
P2 ──→ 补全类型标注  ──→ CLI 工具  ──→ 最终验证
```

每个阶段严格遵循 TDD：先写测试 → 测试失败 → 实现代码 → 测试通过。