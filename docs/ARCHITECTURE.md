# Factor_DB 架构设计文档

## 一、项目概述

Factor_DB 是一个基于 DuckDB 的高性能量化金融数据存储系统，专为 A 股市场设计。

### 核心特性
- **高性能**: 向量化查询，比 pandas 快 10-100 倍
- **低耦合**: 模块化设计，各组件独立可替换
- **高内聚**: 单一职责，每个模块只做一件事
- **兼容性**: 支持 Level 1 分钟数据、日 K 数据、因子数据
- **易扩展**: 插件式架构，易于添加新数据源

---

## 二、总体架构

```
Factor_DB/
├── core/                    # 核心引擎层
│   ├── __init__.py
│   ├── connection.py        # DuckDB 连接管理
│   ├── schema.py           # 数据库表结构定义
│   └── transaction.py      # 事务管理
├── loaders/                 # 数据加载层
│   ├── __init__.py
│   ├── base.py             # 加载器基类
│   ├── level1_loader.py    # Level 1 数据加载
│   ├── daily_loader.py     # 日 K 数据加载
│   └── factor_loader.py    # 因子数据加载
├── query/                   # 查询接口层
│   ├── __init__.py
│   ├── base.py             # 查询基类
│   ├── price_query.py      # 价格数据查询
│   ├── factor_query.py     # 因子数据查询
│   └── screen.py           # 条件选股
├── adapters/                # 适配器层 (兼容性)
│   ├── __init__.py
│   ├── pandas_adapter.py   # pandas DataFrame 适配
│   ├── numpy_adapter.py    # NumPy 数组适配
│   └── engine_adapter.py   # 回测引擎适配
├── pipeline/                # 数据处理管道
│   ├── __init__.py
│   ├── resample.py         # 重采样 (1min -> 5min -> day)
│   ├── adjust.py           # 复权处理
│   └── filter.py           # 数据过滤
├── utils/                   # 工具模块
│   ├── __init__.py
│   ├── logger.py           # 日志配置
│   ├── config.py           # 配置管理
│   └── validators.py       # 数据验证
├── tests/                   # 测试套件
│   ├── __init__.py
│   ├── test_connection.py
│   ├── test_loaders.py
│   ├── test_query.py
│   └── test_adapters.py
├── docs/                    # 文档
│   ├── ARCHITECTURE.md     # 本文件
│   ├── API.md              # API 文档
│   └── PERFORMANCE.md      # 性能报告
├── data/                    # 数据目录 (gitignore)
│   ├── raw/                # 原始数据
│   └── processed/          # 处理后数据
├── benchmarks/              # 基准测试
│   └── benchmark.py
├── README.md               # 项目说明
├── requirements.txt        # 依赖
└── setup.py               # 安装配置
```

---

## 三、模块设计原则

### 3.1 高内聚

每个模块只负责一个明确的职责：

| 模块 | 职责 | 不做什么 |
|------|------|---------|
| `connection.py` | 管理 DuckDB 连接生命周期 | 不处理业务逻辑 |
| `level1_loader.py` | 导入 Level 1 数据 | 不查询数据 |
| `price_query.py` | 查询价格数据 | 不导入数据 |
| `pandas_adapter.py` | 转换为 pandas | 不执行计算 |

### 3.2 低耦合

模块间通过接口通信，不直接依赖实现：

```python
# 好的设计：依赖抽象接口
class BaseLoader(ABC):
    @abstractmethod
    def load(self, data_path: Path) -> None:
        pass

# 具体实现
class Level1Loader(BaseLoader):
    def load(self, data_path: Path) -> None:
        ...

# 使用时依赖接口
class DataManager:
    def __init__(self, loader: BaseLoader):
        self.loader = loader
```

### 3.3 依赖关系图

```
┌─────────────────────────────────────────┐
│              应用层 (用户代码)             │
│         BacktestEngine / Strategy        │
├─────────────────────────────────────────┤
│              适配器层 (adapters)          │
│    pandas_adapter / engine_adapter       │
├─────────────────────────────────────────┤
│              查询层 (query)               │
│    price_query / factor_query / screen   │
├─────────────────────────────────────────┤
│              核心层 (core)                │
│    connection / schema / transaction     │
├─────────────────────────────────────────┤
│              加载层 (loaders)             │
│    level1_loader / daily_loader          │
├─────────────────────────────────────────┤
│              数据源                       │
│    Feather / CSV / Parquet / Tushare     │
└─────────────────────────────────────────┘
```

---

## 四、核心模块设计

### 4.1 连接管理 (core/connection.py)

```python
class DuckDBConnection:
    """DuckDB 连接管理器
    
    职责：
        - 管理数据库连接生命周期
        - 支持读写分离
        - 连接池管理（预留）
    """
    
    def __init__(self, db_path: str, read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only
        self._conn = None
    
    def connect(self) -> duckdb.DuckDBPyConnection:
        """建立连接"""
        if self._conn is None:
            self._conn = duckdb.connect(self.db_path, self.read_only)
        return self._conn
    
    def close(self) -> None:
        """关闭连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def __enter__(self):
        return self.connect()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
```

### 4.2 表结构定义 (core/schema.py)

```python
class SchemaManager:
    """数据库表结构管理
    
    职责：
        - 定义所有表结构
        - 管理索引
        - 版本迁移（预留）
    """
    
    TABLES = {
        'level1_snapshots': '''
            CREATE TABLE IF NOT EXISTS level1_snapshots (
                trade_date DATE,
                trade_time TIME,
                stock_code VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                PRIMARY KEY (trade_date, trade_time, stock_code)
            )
        ''',
        'daily_prices': '''
            CREATE TABLE IF NOT EXISTS daily_prices (
                trade_date DATE,
                stock_code VARCHAR,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                adj_factor DOUBLE DEFAULT 1.0,
                PRIMARY KEY (trade_date, stock_code)
            )
        ''',
        'stock_info': '''
            CREATE TABLE IF NOT EXISTS stock_info (
                stock_code VARCHAR PRIMARY KEY,
                stock_name VARCHAR,
                list_date DATE,
                delist_date DATE,
                industry VARCHAR,
                market VARCHAR,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'trade_calendar': '''
            CREATE TABLE IF NOT EXISTS trade_calendar (
                trade_date DATE PRIMARY KEY,
                is_trading_day BOOLEAN,
                week_day INTEGER
            )
        ''',
        'factor_data': '''
            CREATE TABLE IF NOT EXISTS factor_data (
                trade_date DATE,
                stock_code VARCHAR,
                factor_name VARCHAR,
                factor_value DOUBLE,
                PRIMARY KEY (trade_date, stock_code, factor_name)
            )
        '''
    }
    
    INDEXES = {
        'idx_level1_date_code': 
            'CREATE INDEX IF NOT EXISTS idx_level1_date_code ON level1_snapshots(trade_date, stock_code)',
        'idx_level1_time': 
            'CREATE INDEX IF NOT EXISTS idx_level1_time ON level1_snapshots(trade_time)',
        'idx_daily_date': 
            'CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(trade_date)',
        'idx_daily_code': 
            'CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_prices(stock_code)',
        'idx_factor_name': 
            'CREATE INDEX IF NOT EXISTS idx_factor_name ON factor_data(factor_name, trade_date)'
    }
```

---

## 五、数据流设计

### 5.1 导入流程

```
原始数据 (Feather/CSV)
    ↓
[loaders/base.py] 统一接口
    ↓
[loaders/level1_loader.py] 解析 Level 1 格式
    ↓
[pipeline/resample.py] 重采样 (可选)
    ↓
[pipeline/adjust.py] 复权处理 (可选)
    ↓
[utils/validators.py] 数据验证
    ↓
[core/schema.py] 写入 DuckDB
    ↓
[core/transaction.py] 事务提交
```

### 5.2 查询流程

```
用户请求 (如：获取某股票日K)
    ↓
[query/base.py] 参数解析
    ↓
[query/price_query.py] 构建 SQL
    ↓
[core/connection.py] 执行查询
    ↓
[adapters/pandas_adapter.py] 结果转换
    ↓
返回 DataFrame / NumPy 数组
```

---

## 六、兼容性设计

### 6.1 与 Factor_Trading_v3.0 兼容

```python
# adapters/engine_adapter.py
class FactorTradingAdapter:
    """Factor_Trading 回测引擎适配器
    
    提供与原有 DataManager 兼容的接口
    """
    
    def __init__(self, db_path: str):
        self.db = DuckDBManager(db_path)
    
    def get_adj_price(self, price_type: str = 'close', adjust: str = 'forward') -> pd.DataFrame:
        """兼容原有接口"""
        return self.db.query.get_price_matrix(
            field=price_type,
            adjust=adjust
        )
    
    def get_trade_dates(self, start: str, end: str) -> List[str]:
        """兼容原有接口"""
        return self.db.query.get_trade_dates(start, end)
```

### 6.2 与 DataLoaderV3 兼容

```python
# adapters/numpy_adapter.py
class NumPyAdapter:
    """NumPy 数组适配器
    
    支持 VectorBacktestEngine 的 NumPy 接口
    """
    
    def to_data_loader_v3(self, query_result: pd.DataFrame) -> Dict[str, np.ndarray]:
        """转换为 DataLoaderV3 格式"""
        return {
            'dates': query_result.index.values.astype(str),
            'stocks': query_result.columns.values.astype(str),
            'close': query_result.values
        }
```

---

## 七、性能设计

### 7.1 查询优化策略

| 策略 | 实现 | 效果 |
|------|------|------|
| **索引优化** | 复合索引 (date, code) | 查询提速 100x |
| **列式存储** | DuckDB 原生支持 | 只读需要的列 |
| **分区策略** | 按年份分表 | 减少扫描范围 |
| **预聚合** | 物化视图 (预留) | 常用查询秒开 |
| **缓存层** | LRU 缓存热点数据 | 重复查询毫秒级 |

### 7.2 写入优化策略

| 策略 | 实现 | 效果 |
|------|------|------|
| **批量导入** | COPY / INSERT FROM | 比逐条快 1000x |
| **事务合并** | 每日一个事务 | 减少 WAL 开销 |
| **并行导入** | 多文件并行 | 利用多核 CPU |

---

## 八、测试策略

### 8.1 测试金字塔

```
        /\
       /  \     E2E 测试 (完整导入+查询流程)
      /____\        
     /      \   集成测试 (模块间交互)
    /________\      
   /          \ 单元测试 (单个函数)
  /____________\
```

### 8.2 测试覆盖目标

| 模块 | 测试类型 | 覆盖率目标 |
|------|---------|-----------|
| connection.py | 单元测试 | 100% |
| schema.py | 单元测试 | 100% |
| level1_loader.py | 集成测试 | 90% |
| price_query.py | 单元+集成 | 95% |
| adapters | 集成测试 | 90% |

---

## 九、实施路线图

### Phase 1: 基础设施 (Day 1)
- [ ] 创建项目结构
- [ ] 实现 connection.py
- [ ] 实现 schema.py
- [ ] 编写基础测试

### Phase 2: 数据加载 (Day 2-3)
- [ ] 实现 level1_loader.py
- [ ] 实现 daily_loader.py
- [ ] 实现数据验证
- [ ] 导入测试数据

### Phase 3: 查询接口 (Day 4)
- [ ] 实现 price_query.py
- [ ] 实现 factor_query.py
- [ ] 实现 screen.py
- [ ] 性能测试

### Phase 4: 适配器层 (Day 5)
- [ ] 实现 pandas_adapter.py
- [ ] 实现 engine_adapter.py
- [ ] 兼容性测试

### Phase 5: 优化与文档 (Day 6)
- [ ] 性能优化
- [ ] 编写文档
- [ ] 完整测试

---

## 十、总结

Factor_DB 采用分层架构设计：
- **core**: 底层 DuckDB 引擎
- **loaders**: 数据导入（支持 Level 1 / 日 K / 因子）
- **query**: 查询接口（SQL 封装）
- **adapters**: 兼容层（pandas / numpy / 回测引擎）
- **pipeline**: 数据处理（重采样 / 复权 / 过滤）

通过高内聚、低耦合的设计，实现：
1. **独立演进**: 各模块可独立升级
2. **易于测试**: 模块间依赖清晰
3. **灵活扩展**: 新数据源只需添加 loader
4. **高性能**: 向量化查询 + 索引优化
