# 改进方案分析：数据版本管理 & DailyLoader 测试覆盖

> 分析日期：2026-05-30

---

## 第一部分：Point-in-Time（PIT）数据版本管理

### 1.1 问题定义

#### 当前数据写入方式

[DailyLoader](file:///f:/Coding/Factor_DB/loaders/daily_loader.py) 和因子加载使用 `INSERT OR REPLACE`：

```sql
-- daily_loader.py L202
INSERT OR REPLACE INTO daily_prices (trade_date, stock_code, close)
SELECT trade_date, stock_code, close FROM temp_daily
```

```sql
-- daily_loader.py L234
INSERT OR REPLACE INTO factor_data (trade_date, stock_code, factor_name, factor_value)
SELECT trade_date, stock_code, factor_name, factor_value FROM temp_factor
```

**后果**：后加载的数据无条件覆盖先前的数据，没有历史记录。

#### 影响的量化场景

| 场景 | 问题 | 严重程度 |
|------|------|---------|
| 财务数据重述 | 公司修正历史财报后重新加载，旧数据消失，回测无法还原"当时看到的"PE | **高** |
| 成分股调整 | 沪深300成分股变更后，回测时可能使用了未来才加入的股票 | **高** |
| 复权因子更新 | 分红送股后 adj_factor 更新，旧复权价格被覆盖 | **中** |
| 审计追踪 | 无法追溯"某次回测用的是哪批数据" | **中** |
| 数据修复回滚 | 加载错误数据后无法回滚到上一个正确版本 | **低** |

#### 核心需求

```
回测代码在 date=2024-06-30 时，能看到的 PE 数据，
必须是数据加载时间 ≤ 2024-06-30 的最新版本。
```

---

### 1.2 方案对比

#### 方案 A：独立历史表（双表模式）

```
┌─────────────────┐     ┌──────────────────────┐
│  daily_prices   │     │  daily_prices_history │
│  (当前数据)      │ ←── │  (全量历史版本)        │
│  PK: date,code  │     │  PK: date,code,version│
└─────────────────┘     └──────────────────────┘
```

写入时：先查当前值 → INSERT 到 history → UPSERT 到 current

**优点**：
- 当前表查询性能不变（无额外过滤）
- 历史表可独立清理/归档

**缺点**：
- 写入需要两次操作（事务一致性风险）
- 两套表结构需分别维护
- 加载器逻辑复杂化

#### 方案 B：单表 + loaded_at 列（单表模式）

```
┌──────────────────────────────────────┐
│  daily_prices                        │
│  PK: (trade_date, stock_code,        │
│       loaded_at)                     │
│  loaded_at TIMESTAMP NOT NULL        │
│       DEFAULT CURRENT_TIMESTAMP      │
└──────────────────────────────────────┘
```

写入时：直接 INSERT（不再 REPLACE）
查询时：子查询过滤最新版本

**优点**：
- 单表管理，结构简单
- 写入即审计（一次 INSERT 完成）
- DuckDB 列式压缩对重复的 trade_date/stock_code 高效

**缺点**：
- 每次查询需要子查询过滤（增加 SQL 复杂度）
- 数据量增长（每次加载都产生新行）
- 当前用户代码需适配 `as_of` 参数

#### 方案 C：DuckDB Temporal Extension

DuckDB 社区有 `temporal` 扩展讨论，但目前（v1.0+）**尚未正式发布**。

**结论**：不可行，等待社区成熟。

#### 方案 D：Snapshots 模式（外部快照）

```
每个快照一个独立 DuckDB 文件：
factor_db_20240630.duckdb
factor_db_20240705.duckdb
```

查询时动态 ATTACH 多个数据库。

**优点**：改动最小，概念清晰
**缺点**：跨文件查询性能差，磁盘空间浪费，管理复杂

---

### 1.3 推荐方案：方案 B（单表 + loaded_at）

#### 选择理由

| 维度 | 方案 A | 方案 B | 方案 D |
|------|--------|--------|--------|
| 实现复杂度 | 高 | **中** | 低 |
| 查询性能 | 高 | 中（可接受） | 低 |
| 存储效率 | 中 | 中（列式压缩） | 低 |
| 代码改动量 | 大 | **中** | 小 |
| 可维护性 | 低 | **高** | 低 |
| 审计完整性 | 中 | **高** | 低 |

方案 B 在复杂度、性能和可维护性之间取得最佳平衡。DuckDB 的列式存储对重复值压缩效率极高，空间增长可控。

#### 风险评估

| 风险 | 缓解措施 |
|------|---------|
| 数据量膨胀 | DuckDB 列式压缩（trade_date/stock_code 重复值压缩率 >90%） |
| 查询性能下降 | 子查询可被 DuckDB 优化器高效处理；添加 `(trade_date, stock_code, loaded_at DESC)` 索引 |
| 历史数据清理 | 提供 `compact_data(before: date)` 方法，定期清理旧版本 |

---

### 1.4 详细设计

#### 1.4.1 表结构变更

**daily_prices（变更）**：

```sql
-- 旧
CREATE TABLE daily_prices (
    trade_date DATE NOT NULL,
    stock_code VARCHAR NOT NULL,
    ...
    PRIMARY KEY (trade_date, stock_code)
);

-- 新
CREATE TABLE daily_prices (
    trade_date DATE NOT NULL,
    stock_code VARCHAR NOT NULL,
    loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
    volume BIGINT, amount DOUBLE,
    adj_factor DOUBLE DEFAULT 1.0,
    turnover DOUBLE,
    PRIMARY KEY (trade_date, stock_code, loaded_at)
);
```

**factor_data（变更）**：

```sql
-- 旧
CREATE TABLE factor_data (
    trade_date DATE NOT NULL,
    stock_code VARCHAR NOT NULL,
    factor_name VARCHAR NOT NULL,
    factor_value DOUBLE,
    PRIMARY KEY (trade_date, stock_code, factor_name)
);

-- 新
CREATE TABLE factor_data (
    trade_date DATE NOT NULL,
    stock_code VARCHAR NOT NULL,
    factor_name VARCHAR NOT NULL,
    factor_value DOUBLE,
    loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, stock_code, factor_name, loaded_at)
);
```

**新增索引**：

```sql
-- 加速 PIT 查询
CREATE INDEX idx_daily_pit ON daily_prices(trade_date, stock_code, loaded_at DESC);
CREATE INDEX idx_factor_pit ON factor_data(trade_date, stock_code, factor_name, loaded_at DESC);
```

#### 1.4.2 非变更表

以下表**不需要** PIT 支持，保持不变：

| 表 | 原因 |
|----|------|
| `level1_snapshots` | 分钟数据是原始行情，不存在"修正"场景 |
| `stock_info` | 元数据表，变更频率低 |
| `trade_calendar` | 静态数据 |
| `factor_info` | 元数据表 |
| `macro_data` | 宏观数据有独立发布周期，暂不纳入 |
| `news_sentiment` / `report_sentiment` | 舆情数据天然不可修正 |
| `alternative_data` | 另类数据，暂不纳入 |

#### 1.4.3 Loader 层变更

**DailyLoader._import_to_daily_table**（当前 L184-213）：

```python
# 当前：INSERT OR REPLACE（覆盖写入）
conn.execute(f'''
    INSERT OR REPLACE INTO daily_prices (trade_date, stock_code, {field_name})
    SELECT trade_date, stock_code, {field_name}
    FROM temp_daily
''')

# 改为：INSERT（追加写入，loaded_at 自动填充）
conn.execute(f'''
    INSERT INTO daily_prices (trade_date, stock_code, {field_name})
    SELECT trade_date, stock_code, {field_name}
    FROM temp_daily
''')
```

> 注意：`loaded_at` 列由 `DEFAULT CURRENT_TIMESTAMP` 自动填充，无需显式指定。

**DailyLoader._import_to_factor_table**（当前 L215-243）：

```python
# 同理，INSERT OR REPLACE → INSERT
conn.execute('''
    INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
    SELECT trade_date, stock_code, factor_name, factor_value
    FROM temp_factor
''')
```

**停止条件**：`skip_existing=True` 时需要检查"当前 latest 版本"是否已有数据，避免重复加载相同数据：

```python
def _should_skip_existing(self, table: str, trade_date: date, stock_codes: list[str]) -> bool:
    """检查 latest 版本是否已有数据"""
    sql = f'''
        SELECT COUNT(*) FROM (
            SELECT trade_date, stock_code, MAX(loaded_at) as latest
            FROM {table}
            WHERE trade_date = '{trade_date}'
            GROUP BY trade_date, stock_code
        )
    '''
    result = self.conn.execute(sql).fetchone()
    return result[0] > 0
```

#### 1.4.4 Query 层变更

**QueryFilter 新增字段**：

```python
@dataclass
class QueryFilter:
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    stock_codes: Optional[list[str]] = None
    fields: Optional[list[str]] = None
    as_of: Optional[date] = None          # 新增：PIT 截止时间
```

**BaseQuery 新增 PIT 过滤方法**：

```python
def _build_pit_filter(self, filter: QueryFilter, table: str, 
                       pk_cols: list[str]) -> str:
    """构建 PIT 过滤条件
    
    返回 SQL 子查询，确保只取每个主键的最新版本（截至 as_of）。
    """
    if filter.as_of is None:
        return '1=1'
    
    pk_list = ', '.join(pk_cols)
    return f'''
        ({pk_list}, loaded_at) IN (
            SELECT {pk_list}, MAX(loaded_at)
            FROM {table}
            WHERE loaded_at <= '{filter.as_of}'
            GROUP BY {pk_list}
        )
    '''
```

**PriceQuery.get_daily 变更**：

```python
def get_daily(self, ...):
    ...
    # 新增 PIT 过滤
    pit_filter = self._build_pit_filter(
        filter, 'daily_prices', ['trade_date', 'stock_code']
    )
    
    sql = f'''
        SELECT {select_fields}
        FROM daily_prices
        WHERE {date_filter} AND {stock_filter} AND {pit_filter}
        ORDER BY trade_date, stock_code
    '''
```

**FactorQuery.get_cross_section 变更**：

```python
def get_cross_section(self, factor_name: str, trade_date: date,
                       as_of: Optional[date] = None):
    ...
    if as_of:
        sql = f'''
            SELECT stock_code, factor_value
            FROM factor_data
            WHERE factor_name = '{factor_name}'
              AND trade_date = '{trade_date}'
              AND (trade_date, stock_code, factor_name, loaded_at) IN (
                  SELECT trade_date, stock_code, factor_name, MAX(loaded_at)
                  FROM factor_data
                  WHERE factor_name = '{factor_name}'
                    AND trade_date = '{trade_date}'
                    AND loaded_at <= '{as_of}'
                  GROUP BY trade_date, stock_code, factor_name
              )
            ORDER BY factor_value DESC
        '''
    else:
        # 原有逻辑，取最新版本
        sql = f'''
            SELECT stock_code, factor_value
            FROM factor_data
            WHERE factor_name = '{factor_name}'
              AND trade_date = '{trade_date}'
              AND (trade_date, stock_code, factor_name, loaded_at) IN (
                  SELECT trade_date, stock_code, factor_name, MAX(loaded_at)
                  FROM factor_data
                  WHERE factor_name = '{factor_name}'
                    AND trade_date = '{trade_date}'
                  GROUP BY trade_date, stock_code, factor_name
              )
            ORDER BY factor_value DESC
        '''
```

> 注意：即使 `as_of=None`，也需要用子查询取最新版本（因为同一 date+code 可能有多个 loaded_at）。可以将子查询逻辑封装为共享方法。

**封装共享 PIT SQL 构建器**：

```python
class BaseQuery(ABC):
    ...
    def _build_pit_subquery(self, table: str, pk_columns: list[str],
                             extra_conditions: str = '1=1',
                             as_of: Optional[date] = None) -> str:
        """构建 PIT 子查询，返回 latest version 的主键
        
        Args:
            table: 表名
            pk_columns: 主键列（不含 loaded_at）
            extra_conditions: 额外过滤条件
            as_of: PIT 截止时间
        
        Returns:
            SQL 子查询，用于 IN 条件
        """
        pk_list = ', '.join(pk_columns)
        time_filter = f"AND loaded_at <= '{as_of}'" if as_of else ''
        
        return f'''
            SELECT {pk_list}, MAX(loaded_at)
            FROM {table}
            WHERE {extra_conditions} {time_filter}
            GROUP BY {pk_list}
        '''
```

#### 1.4.5 数据清理机制

新增 `SchemaManager.compact_table()` 方法：

```python
def compact_table(self, table: str, keep_before: date) -> int:
    """清理旧版本数据
    
    删除 loaded_at < keep_before 的非最新版本数据。
    
    Args:
        table: 表名
        keep_before: 保留此日期之前的每个主键的最新版本
        
    Returns:
        删除的行数
    """
    pk_cols = {
        'daily_prices': ['trade_date', 'stock_code'],
        'factor_data': ['trade_date', 'stock_code', 'factor_name'],
    }
    
    pk_list = ', '.join(pk_cols[table])
    
    sql = f'''
        DELETE FROM {table}
        WHERE ({pk_list}, loaded_at) NOT IN (
            SELECT {pk_list}, MAX(loaded_at)
            FROM {table}
            WHERE loaded_at < '{keep_before}'
            GROUP BY {pk_list}
        )
        AND loaded_at < '{keep_before}'
    '''
    
    with self.conn as conn:
        result = conn.execute(sql)
    return result.fetchone()[0] if result else 0
```

#### 1.4.6 改动波及范围

| 文件 | 改动类型 | 工作量 |
|------|---------|--------|
| `core/schema.py` | 修改 daily_prices/factor_data DDL；新增 2 个索引；新增 compact_table | 中 |
| `loaders/daily_loader.py` | `INSERT OR REPLACE` → `INSERT`；skip_existing 逻辑适配 | 小 |
| `query/base.py` | QueryFilter 新增 as_of；新增 _build_pit_subquery | 小 |
| `query/price_query.py` | 所有查询添加 PIT 过滤 | 中 |
| `query/factor_query.py` | 所有查询添加 PIT 过滤 | 中 |
| `query/screen.py` | 选股查询添加 PIT 过滤 | 小 |
| `adapters/engine_adapter.py` | FactorTradingAdapter 适配 as_of | 小 |
| `tests/test_*.py` | 新增 PIT 相关测试用例 | 中 |

**总计**：约 8 个文件，预估 200-300 行改动。

---

### 1.5 实施步骤

```
Phase 1: Schema 迁移
  ├── 1.1 修改 daily_prices / factor_data DDL（添加 loaded_at 列）
  ├── 1.2 新增 PIT 索引
  └── 1.3 实现对已有数据库的 ALTER TABLE 迁移脚本

Phase 2: Loader 层适配
  ├── 2.1 INSERT OR REPLACE → INSERT
  ├── 2.2 skip_existing 逻辑适配
  └── 2.3 单元测试

Phase 3: Query 层适配
  ├── 3.1 QueryFilter 新增 as_of
  ├── 3.2 BaseQuery 新增 _build_pit_subquery
  ├── 3.3 PriceQuery 全部方法适配
  ├── 3.4 FactorQuery 全部方法适配
  └── 3.5 StockScreener 适配

Phase 4: Adapter 层适配
  └── 4.1 FactorTradingAdapter 适配

Phase 5: 数据清理
  └── 5.1 SchemaManager.compact_table 实现

Phase 6: 测试与文档
  ├── 6.1 PIT 正确性测试（as_of 查询验证）
  ├── 6.2 性能基准测试（对比 PIT 前后性能）
  └── 6.3 更新 CODE_WIKI.md
```

---

## 第二部分：DailyLoader 测试覆盖

### 2.1 现状分析

#### 当前测试覆盖

| 测试文件 | 覆盖类 | 测试方法数 |
|---------|--------|-----------|
| test_connection.py | DuckDBConnection | 5 |
| test_schema.py | SchemaManager | 6 |
| test_query.py | PriceQuery / FactorQuery / StockScreener | 8 |
| test_adapters.py | PandasAdapter / FactorTradingAdapter / DataLoaderV3Adapter | 8 |
| test_extension.py | MetadataManager / Macro / Sentiment / Alternative / Analytics | 14 |
| **DailyLoader** | **无** | **0** |

DailyLoader 是**用户使用频率最高的入口**，却零测试覆盖，是当前测试体系的最大盲区。

#### 待测方法清单

```
DailyLoader
├── load(data_path)              # 文件/目录分发
├── _load_single_file(file_path) # 单文件读取+导入
├── _load_directory(data_dir)    # 批量文件加载
├── _import_price_data(df, name) # 宽→长转换+分发
├── _import_to_daily_table(df, field)  # 日K表写入
├── _import_to_factor_table(df, name)  # 因子表写入
├── validate(data)               # 数据验证
└── load_stock_info(info_path)   # 股票信息加载
```

#### 关键风险点

| 风险 | 位置 | 影响 |
|------|------|------|
| 宽→长格式转换错误 | `_import_price_data` L162-166 | 数据错位，整列数据偏移 |
| 文件名→字段名映射 | `_load_single_file` L98 | 错误的字段名导致数据写入错误表 |
| 日期解析失败 | `_import_price_data` L172 | 日期列变成 NaN |
| MERGE 覆盖逻辑 | `_import_to_daily_table` L201-205 | 后加载数据覆盖已修正数据 |
| 多格式兼容性 | `_load_single_file` L83-90 | .feather/.parquet 读取失败 |
| 空值处理 | `_import_price_data` L169 | dropna 可能误删有效数据 |

---

### 2.2 测试策略

#### 测试数据构造方法

不使用真实数据文件，而是**在测试中动态构造 DataFrame**，优点：
- 数据量可控，测试执行快
- 边界条件可精确构造
- 不依赖外部数据文件

```python
import pandas as pd
import numpy as np
from datetime import datetime

def _make_test_df(rows=3, cols=3, seed=42):
    """构造测试用宽格式 DataFrame"""
    np.random.seed(seed)
    dates = pd.date_range('2024-01-01', periods=rows, freq='B')
    stocks = [f'{code:06d}.SZ' for code in range(1, cols+1)]
    data = np.random.randn(rows, cols) * 10 + 50
    return pd.DataFrame(data, index=dates, columns=stocks)
```

#### 测试用例设计

##### T1: 文件格式兼容性测试

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T1.1 | 加载 .pkl 文件 | 数据正确写入 daily_prices |
| T1.2 | 加载 .csv 文件 | CSV 解析正确，日期列被识别 |
| T1.3 | 加载 .parquet 文件 | Parquet 读取正确 |
| T1.4 | 加载 .feather 文件 | Feather 读取正确 |
| T1.5 | 不支持的文件格式 (.txt) | 返回 0，不抛异常 |

##### T2: 价格字段导入测试

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T2.1 | 加载 close 数据 | daily_prices 表 close 列值正确 |
| T2.2 | 加载 open 数据 | 先写入 close 再写入 open，open 列值正确，close 不被覆盖 |
| T2.3 | 加载 volume 数据 | volume 列写入正确 |
| T2.4 | 加载 amount 数据 | amount 列写入正确 |
| T2.5 | 加载全部 6 个价格字段 | 逐字段加载后所有列完整 |

##### T3: 因子导入测试

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T3.1 | 加载 PE 因子 | factor_data 表 factor_name='PE'，factor_value 正确 |
| T3.2 | 加载 PB 因子 | factor_data 表 factor_name='PB' |
| T3.3 | 因子+价格混合加载 | 价格字段进 daily_prices，因子字段进 factor_data |

##### T4: 目录加载测试

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T4.1 | 加载包含多个文件的目录 | 所有文件被处理，总数正确 |
| T4.2 | 加载空目录 | 返回 0，不抛异常 |
| T4.3 | 部分文件格式不支持 | 只处理支持的格式，警告不阻塞 |

##### T5: 数据验证测试

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T5.1 | 空 DataFrame | validate() 返回 False |
| T5.2 | 无日期索引 | 警告但不阻塞（当前逻辑） |
| T5.3 | 缺少数值列 | validate() 返回 False |
| T5.4 | 空值比例 >80% | 警告但不阻塞 |
| T5.5 | 正常数据 | validate() 返回 True |

##### T6: 股票信息加载

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T6.1 | 加载 stock_info | stock_info 表写入正确 |
| T6.2 | 列名映射 | 'code'→'stock_code', 'name'→'stock_name' 映射正确 |

##### T7: 边界与异常

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T7.1 | 路径不存在 | 返回 0，不抛异常 |
| T7.2 | 文件损坏 | 捕获异常，返回 0 |
| T7.3 | 空文件 | 返回 0 |
| T7.4 | skip_existing=True | 已存在数据不重复写入 |

##### T8: 宽→长格式转换正确性

| 用例ID | 测试内容 | 验证点 |
|--------|---------|--------|
| T8.1 | 3天×3股 → 9行 | 行数 = days × stocks |
| T8.2 | 股票代码保留 | melt 后 stock_code 列值正确 |
| T8.3 | 日期格式 | trade_date 为 date 类型 |
| T8.4 | NaN 被正确删除 | dropna 后行数正确减少 |

---

### 2.3 测试代码框架

```python
"""
日 K 数据加载器测试

测试 DailyLoader 的数据加载、格式转换、验证等功能。
"""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from loaders.daily_loader import DailyLoader, LoaderConfig


class TestDailyLoader(unittest.TestCase):
    """DailyLoader 测试类"""

    @staticmethod
    def _make_price_df(rows=3, cols=3, seed=42):
        """构造测试用宽格式日K DataFrame
        
        Args:
            rows: 日期行数
            cols: 股票列数
            seed: 随机种子
        
        Returns:
            宽格式 DataFrame (index=日期, columns=股票代码)
        """
        np.random.seed(seed)
        dates = pd.date_range('2024-01-01', periods=rows, freq='B')
        stocks = [f'{code:06d}.SZ' for code in range(1, cols + 1)]
        data = np.random.randn(rows, cols) * 10 + 50
        # 确保价格为正
        data = np.abs(data) + 1
        return pd.DataFrame(data, index=dates, columns=stocks)

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_daily_loader.db')
        self.data_dir = Path(self.temp_dir) / 'test_data'
        self.data_dir.mkdir()

        # 初始化数据库
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()

        # 创建加载器（关闭进度条，方便测试）
        self.config = LoaderConfig(
            db_path=self.db_path,
            show_progress=False,
            skip_existing=False
        )

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        # 清理测试数据文件
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ========== T1: 文件格式兼容性 ==========

    def test_load_pkl(self):
        """T1.1: 加载 .pkl 文件"""
        df = self._make_price_df()
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

        # 验证数据已写入
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(
                "SELECT COUNT(*) FROM daily_prices WHERE trade_date = '2024-01-01'"
            ).fetchone()
        self.assertEqual(result[0], 3)  # 3 stocks
        conn.close()

    def test_load_csv(self):
        """T1.2: 加载 .csv 文件"""
        df = self._make_price_df()
        file_path = self.data_dir / 'close.csv'
        df.to_csv(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_load_parquet(self):
        """T1.3: 加载 .parquet 文件"""
        df = self._make_price_df()
        file_path = self.data_dir / 'close.parquet'
        df.to_parquet(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_load_feather(self):
        """T1.4: 加载 .feather 文件"""
        df = self._make_price_df()
        df = df.reset_index().rename(columns={'index': 'trade_date'})
        file_path = self.data_dir / 'close.feather'
        df.to_feather(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_unsupported_format(self):
        """T1.5: 不支持的文件格式"""
        file_path = self.data_dir / 'data.txt'
        file_path.write_text('not a data file')

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertEqual(count, 0)

    # ========== T2: 价格字段导入 ==========

    def test_import_close(self):
        """T2.1: 加载 close → daily_prices"""
        df = self._make_price_df(rows=2, cols=2, seed=1)
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            loader.load(file_path)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT trade_date, stock_code, close
                FROM daily_prices
                ORDER BY trade_date, stock_code
            """).fetchdf()

        self.assertEqual(len(result), 4)  # 2 days × 2 stocks
        self.assertAlmostEqual(
            result.loc[result['stock_code'] == '000001.SZ', 'close'].iloc[0],
            df.iloc[0, 0]
        )
        conn.close()

    def test_import_multiple_price_fields(self):
        """T2.5: 依次加载全部 6 个价格字段"""
        fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
        for field in fields:
            df = self._make_price_df(rows=2, cols=2, seed=hash(field) % 100)
            file_path = self.data_dir / f'{field}.pkl'
            df.to_pickle(file_path)

            with DailyLoader(self.config) as loader:
                count = loader.load(file_path)
            self.assertGreater(count, 0)

        # 验证所有列都存在
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT open, high, low, close, volume, amount
                FROM daily_prices
                LIMIT 1
            """).fetchdf()

        for col in fields:
            self.assertIn(col, result.columns)
            self.assertFalse(result[col].isna().all(),
                            f"列 {col} 全为 NaN")
        conn.close()

    def test_price_field_not_overwrite_others(self):
        """T2.2: 先后加载 close 和 open，互不覆盖"""
        # 先加载 close
        df_close = self._make_price_df(rows=2, cols=2, seed=1)
        df_close.to_pickle(self.data_dir / 'close.pkl')
        with DailyLoader(self.config) as loader:
            loader.load(self.data_dir / 'close.pkl')

        # 再加载 open
        df_open = self._make_price_df(rows=2, cols=2, seed=2)
        df_open.to_pickle(self.data_dir / 'open.pkl')
        with DailyLoader(self.config) as loader:
            loader.load(self.data_dir / 'open.pkl')

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT close, open FROM daily_prices
                WHERE close IS NOT NULL AND open IS NOT NULL
            """).fetchdf()

        # close 和 open 都应该有值
        self.assertGreater(len(result), 0)
        conn.close()

    # ========== T3: 因子导入 ==========

    def test_import_factor(self):
        """T3.1: 加载 PE 因子 → factor_data"""
        df = self._make_price_df(rows=2, cols=2, seed=10)
        df = df * 0.3  # 模拟 PE 值范围
        file_path = self.data_dir / 'PE.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT trade_date, stock_code, factor_name, factor_value
                FROM factor_data
                WHERE factor_name = 'PE'
                ORDER BY trade_date, stock_code
            """).fetchdf()

        self.assertEqual(len(result), 4)  # 2 days × 2 stocks
        self.assertTrue((result['factor_name'] == 'PE').all())
        conn.close()

    # ========== T4: 目录加载 ==========

    def test_load_directory(self):
        """T4.1: 加载包含多个文件的目录"""
        for field in ['close', 'open', 'PE', 'PB']:
            df = self._make_price_df(rows=2, cols=2, seed=hash(field) % 100)
            df.to_pickle(self.data_dir / f'{field}.pkl')

        with DailyLoader(self.config) as loader:
            total = loader.load(self.data_dir)

        self.assertGreater(total, 0)

        # 验证价格表和因子表都有数据
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            price_count = c.execute(
                "SELECT COUNT(*) FROM daily_prices"
            ).fetchone()[0]
            factor_count = c.execute(
                "SELECT COUNT(*) FROM factor_data"
            ).fetchone()[0]

        self.assertGreater(price_count, 0)
        self.assertGreater(factor_count, 0)
        conn.close()

    def test_load_empty_directory(self):
        """T4.2: 加载空目录"""
        empty_dir = self.data_dir / 'empty'
        empty_dir.mkdir()

        with DailyLoader(self.config) as loader:
            count = loader.load(empty_dir)

        self.assertEqual(count, 0)

    # ========== T5: 数据验证 ==========

    def test_validate_empty_df(self):
        """T5.1: 空 DataFrame 验证失败"""
        loader = DailyLoader(self.config)
        df = pd.DataFrame()
        self.assertFalse(loader.validate(df))

    def test_validate_no_numeric(self):
        """T5.3: 缺少数值列验证失败"""
        loader = DailyLoader(self.config)
        df = pd.DataFrame({
            'A': ['a', 'b', 'c'],
            'B': ['x', 'y', 'z']
        })
        self.assertFalse(loader.validate(df))

    def test_validate_normal(self):
        """T5.5: 正常数据验证通过"""
        loader = DailyLoader(self.config)
        df = self._make_price_df()
        self.assertTrue(loader.validate(df))

    def test_validate_high_null_ratio(self):
        """T5.4: 高空值比例数据（警告但不阻塞）"""
        loader = DailyLoader(self.config)
        df = self._make_price_df(rows=10, cols=10)
        df.iloc[:, :] = np.nan
        df.iloc[0, 0] = 1.0  # 仅 1 个有效值
        # 当前 validate 只 warn 不阻断
        self.assertTrue(loader.validate(df))

    # ========== T6: 股票信息 ==========

    def test_load_stock_info(self):
        """T6.1: 加载股票基本信息"""
        info_df = pd.DataFrame({
            'code': ['000001', '000002'],
            'name': ['平安银行', '万科A'],
            'list_date': ['1991-04-03', '1991-01-29'],
            'industry': ['银行', '房地产'],
            'market': ['SZ', 'SZ']
        })

        file_path = self.data_dir / 'stock_info.pkl'
        info_df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load_stock_info(file_path)

        self.assertEqual(count, 2)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(
                "SELECT stock_code, stock_name FROM stock_info ORDER BY stock_code"
            ).fetchdf()

        self.assertEqual(result['stock_code'].tolist(), ['000001', '000002'])
        self.assertEqual(result['stock_name'].tolist(), ['平安银行', '万科A'])
        conn.close()

    # ========== T7: 边界与异常 ==========

    def test_nonexistent_path(self):
        """T7.1: 路径不存在"""
        with DailyLoader(self.config) as loader:
            count = loader.load(Path('/nonexistent/path'))
        self.assertEqual(count, 0)

    def test_corrupted_file(self):
        """T7.2: 损坏的文件"""
        file_path = self.data_dir / 'corrupted.pkl'
        file_path.write_bytes(b'this is not a valid pickle file')

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertEqual(count, 0)

    # ========== T8: 宽→长格式转换正确性 ==========

    def test_wide_to_long_row_count(self):
        """T8.1: 3天×3股 → 9行（剔除 NaN 后）"""
        df = self._make_price_df(rows=3, cols=3)
        file_path = self.data_dir / 'test.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        # 3 days × 3 stocks = 9 (无 NaN 所以全部保留)
        self.assertEqual(count, 9)

    def test_wide_to_long_stock_codes(self):
        """T8.2: melt 后 stock_code 列值正确"""
        df = self._make_price_df(rows=2, cols=2)
        file_path = self.data_dir / 'test.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            loader.load(file_path)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            codes = c.execute(
                "SELECT DISTINCT stock_code FROM daily_prices ORDER BY stock_code"
            ).fetchdf()

        self.assertEqual(codes['stock_code'].tolist(), ['000001.SZ', '000002.SZ'])
        conn.close()

    def test_wide_to_long_nan_handling(self):
        """T8.4: NaN 被正确删除"""
        df = self._make_price_df(rows=2, cols=2)
        df.iloc[0, 1] = np.nan  # 第1天第2只股票为 NaN
        file_path = self.data_dir / 'test.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        # 4 - 1 NaN = 3
        self.assertEqual(count, 3)


if __name__ == '__main__':
    unittest.main()
```

### 2.4 测试覆盖矩阵

```
方法                           T1  T2  T3  T4  T5  T6  T7  T8
────────────────────────────────────────────────────────────────
load(data_path)                ✓   ✓   ✓   ✓           ✓
_load_single_file              ✓   ✓   ✓               ✓
_load_directory                                ✓
_import_price_data                 ✓   ✓                       ✓
_import_to_daily_table              ✓
_import_to_factor_table                 ✓
validate                                           ✓
load_stock_info                                       ✓
```

**共计 20 个测试用例**，覆盖全部 8 个方法。

### 2.5 测试执行

```bash
# 运行 DailyLoader 测试
python -m pytest tests/test_daily_loader.py -v

# 预期输出
# test_load_pkl ....................... PASSED
# test_load_csv ....................... PASSED
# test_load_parquet ................... PASSED
# test_load_feather ................... PASSED
# test_unsupported_format ............. PASSED
# test_import_close ................... PASSED
# test_import_multiple_price_fields ... PASSED
# test_price_field_not_overwrite_others PASSED
# test_import_factor .................. PASSED
# test_load_directory ................. PASSED
# test_load_empty_directory ........... PASSED
# test_validate_empty_df .............. PASSED
# test_validate_no_numeric ............ PASSED
# test_validate_normal ................ PASSED
# test_validate_high_null_ratio ....... PASSED
# test_load_stock_info ................ PASSED
# test_nonexistent_path ............... PASSED
# test_corrupted_file ................. PASSED
# test_wide_to_long_row_count ......... PASSED
# test_wide_to_long_stock_codes ....... PASSED
# test_wide_to_long_nan_handling ...... PASSED
```

### 2.6 注意事项

1. **Feather 格式的特殊性**：Feather 写入时索引会自动转为列，读取时需额外处理。当前测试中先 `reset_index()` 再写入。

2. **DuckDB 单例清理**：每个 `tearDown` 必须调用 `DuckDBConnection._instances.clear()`，否则跨测试的连接缓存会导致文件锁定。

3. **PIT 改造后的测试兼容**：如果先实施 PIT 方案，`_import_to_daily_table` 从 `INSERT OR REPLACE` 变为 `INSERT`，`test_price_field_not_overwrite_others` 需要改为验证 `loaded_at` 有多个版本而非直接覆盖。

4. **进度条干扰**：测试中设置 `show_progress=False`，避免 tqdm 输出污染测试日志。

---

## 附录：两项改进的优先级与依赖关系

```
PIT 数据版本管理 ←── 影响 DailyLoader 写入逻辑
         │
         └──→ DailyLoader 测试需适配 PIT 后的 INSERT 行为

建议顺序：
  1. 先实施 PIT 方案（Phase 1-2，Schema + Loader 变更）
  2. 再编写 DailyLoader 测试（基于 PIT 后的写入逻辑）
  3. 完成 PIT 方案 Phase 3-6（Query + Adapter + 清理）

或者：
  1. 先编写 DailyLoader 测试（基于当前 INSERT OR REPLACE 逻辑）
  2. 实施 PIT 方案
  3. 更新 DailyLoader 测试以适配新逻辑
```

推荐**第二种顺序**（先测试，后改造），遵循"先有测试保护，再重构"的原则。