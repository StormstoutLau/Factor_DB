# Factor_DB 未来扩展方案：宏观、舆情、另类数据支持

> **状态**: 已完成 ✅ | **完成日期**: 2026-05-30 | **测试覆盖**: 49 个测试用例全部通过

## 一、概述

本方案基于现有的 Factor_DB 高内聚、低耦合架构，设计支持宏观经济数据、舆情数据、另类数据的存储和查询功能。核心原则是：

- **保持架构一致性**：遵循现有的分层设计和接口规范
- **插件式扩展**：新增数据类型只需添加对应模块，无需修改现有核心代码
- **统一元数据管理**：所有数据类型共享统一的元数据管理机制
- **灵活的关联查询**：支持跨数据类型的关联分析（如宏观数据与股票因子的关联）

---

## 二、数据类型设计

### 2.1 宏观经济数据

| 数据类别 | 示例指标 | 频率 | 数据来源 |
|---------|---------|------|---------|
| 国内生产总值 | GDP、GDP同比、GDP环比 | 季度 | 国家统计局 |
| 价格指数 | CPI、PPI、PMI | 月度 | 国家统计局 |
| 货币政策 | M0、M1、M2、利率 | 月度 | 央行 |
| 财政数据 | 财政收入、财政支出 | 月度 | 财政部 |
| 外贸数据 | 进出口、顺差/逆差 | 月度 | 海关总署 |
| 就业数据 | 城镇登记失业率 | 月度 | 人社部 |

### 2.2 舆情数据

| 数据类别 | 示例内容 | 频率 | 数据来源 |
|---------|---------|------|---------|
| 新闻舆情 | 新闻标题、内容、情感分析 | 实时/日报 | 财联社、东方财富 |
| 研报舆情 | 券商研报标题、摘要、评级 | 日报 | 东方财富、同花顺 |
| 社交媒体 | 微博、股吧热帖 | 实时 | 微博、雪球 |
| 公告舆情 | 上市公司公告摘要、类型 | 实时 | 交易所 |

### 2.3 另类数据

| 数据类别 | 示例内容 | 频率 | 数据来源 |
|---------|---------|------|---------|
| 产业链数据 | 产品价格、销量、库存 | 日/周/月 | 第三方数据商 |
| 卫星数据 | 工厂开工率、港口吞吐量 | 周/月 | 卫星服务商 |
| 互联网数据 | 搜索指数、电商销量 | 日/周 | 百度指数、阿里指数 |
| 高频数据 | 货运量、电力消耗 | 日 | 政府/第三方 |

---

## 三、数据库表结构设计

### 3.1 元数据统一管理

```sql
-- 数据分类表
CREATE TABLE IF NOT EXISTS data_categories (
    category_id VARCHAR PRIMARY KEY,    -- 分类ID：macro/news/alternative
    category_name VARCHAR,              -- 分类名称：宏观/舆情/另类
    description VARCHAR,                -- 分类描述
    priority INTEGER DEFAULT 0,         -- 优先级
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 数据源表
CREATE TABLE IF NOT EXISTS data_sources (
    source_id VARCHAR PRIMARY KEY,      -- 数据源ID
    source_name VARCHAR,                -- 数据源名称
    provider VARCHAR,                   -- 数据提供方
    update_frequency VARCHAR,           -- 更新频率：daily/weekly/monthly
    contact_info VARCHAR,               -- 联系方式
    is_active BOOLEAN DEFAULT TRUE,
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 数据字典表（所有数据类型的字段说明）
CREATE TABLE IF NOT EXISTS data_dictionary (
    field_id VARCHAR PRIMARY KEY,       -- 字段ID
    category_id VARCHAR,                -- 所属分类ID
    field_name VARCHAR,                 -- 字段名称
    field_type VARCHAR,                 -- 字段类型：numeric/string/date
    description VARCHAR,                -- 字段描述
    unit VARCHAR,                       -- 单位：%/万元/万吨
    source_id VARCHAR,                  -- 数据源ID
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES data_categories(category_id),
    FOREIGN KEY (source_id) REFERENCES data_sources(source_id)
);
```

### 3.2 宏观经济数据表

```sql
-- 宏观数据表（宽表设计，支持动态扩展）
CREATE TABLE IF NOT EXISTS macro_data (
    trade_date DATE NOT NULL,               -- 数据日期
    indicator_id VARCHAR NOT NULL,          -- 指标ID
    indicator_name VARCHAR,                 -- 指标名称
    value DOUBLE,                           -- 指标值
    value_type VARCHAR DEFAULT 'raw',       -- 值类型：raw/yoy/change/seasonal
    data_quality INTEGER DEFAULT 100,       -- 数据质量评分（0-100）
    source_id VARCHAR,                      -- 数据源ID
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (trade_date, indicator_id, value_type)
);

-- 宏观指标信息表
CREATE TABLE IF NOT EXISTS macro_indicators (
    indicator_id VARCHAR PRIMARY KEY,       -- 指标ID
    indicator_name VARCHAR,                 -- 指标名称
    category VARCHAR,                       -- 指标类别：GDP/CPI/PMI等
    frequency VARCHAR,                      -- 频率：daily/monthly/quarterly
    unit VARCHAR,                           -- 单位
    is_leading BOOLEAN DEFAULT FALSE,       -- 是否领先指标
    description VARCHAR,                    -- 指标描述
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 宏观数据索引
CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_data(trade_date);
CREATE INDEX IF NOT EXISTS idx_macro_indicator ON macro_data(indicator_id);
CREATE INDEX IF NOT EXISTS idx_macro_date_indicator ON macro_data(trade_date, indicator_id);
```

### 3.3 舆情数据表

```sql
-- 新闻舆情表
CREATE TABLE IF NOT EXISTS news_sentiment (
    news_id VARCHAR PRIMARY KEY,            -- 新闻ID
    publish_date DATE NOT NULL,             -- 发布日期
    publish_time TIME,                      -- 发布时间
    title VARCHAR,                          -- 新闻标题
    content TEXT,                           -- 新闻内容（可选，摘要）
    source_id VARCHAR,                      -- 数据源ID
    related_stocks VARCHAR[],               -- 相关股票代码数组
    related_industries VARCHAR[],           -- 相关行业数组
    sentiment_score DOUBLE,                 -- 情感得分：-1(负面) 到 1(正面)
    sentiment_label VARCHAR,                -- 情感标签：positive/negative/neutral
    keyword_count INTEGER,                  -- 关键词数量
    read_count INTEGER,                     -- 阅读量（如有）
    comment_count INTEGER,                  -- 评论数（如有）
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 研报舆情表
CREATE TABLE IF NOT EXISTS report_sentiment (
    report_id VARCHAR PRIMARY KEY,          -- 研报ID
    publish_date DATE NOT NULL,             -- 发布日期
    title VARCHAR,                          -- 研报标题
    analyst VARCHAR,                        -- 分析师
    brokerage VARCHAR,                      -- 券商
    related_stock VARCHAR,                  -- 相关股票
    rating_change VARCHAR,                  -- 评级变动：up/down/hold
    target_price DOUBLE,                    -- 目标价
    current_price DOUBLE,                   -- 当前价
    sentiment_score DOUBLE,                 -- 情感得分
    content_summary TEXT,                   -- 内容摘要
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 舆情数据索引
CREATE INDEX IF NOT EXISTS idx_news_date ON news_sentiment(publish_date);
CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news_sentiment(sentiment_label, publish_date);
CREATE INDEX IF NOT EXISTS idx_report_date ON report_sentiment(publish_date);
CREATE INDEX IF NOT EXISTS idx_report_stock ON report_sentiment(related_stock);
```

> **注意**: DuckDB 不支持 GIN 索引，因此移除了 `idx_news_gin_stocks`。数组查询通过 `list_position` 函数或 Python 端解析实现。

### 3.4 另类数据表

```sql
-- 另类数据表（通用设计，支持多种数据类型）
CREATE TABLE IF NOT EXISTS alternative_data (
    data_id VARCHAR PRIMARY KEY,            -- 数据ID
    trade_date DATE NOT NULL,               -- 数据日期
    data_type VARCHAR NOT NULL,             -- 数据类型：satellite/chain/e_commerce
    data_subtype VARCHAR,                   -- 数据子类型
    entity_type VARCHAR,                    -- 实体类型：stock/industry/region
    entity_id VARCHAR,                      -- 实体ID（股票代码/行业ID/区域ID）
    entity_name VARCHAR,                    -- 实体名称
    value DOUBLE,                           -- 数值型值
    value_text TEXT,                        -- 文本型值
    value_array DOUBLE[],                   -- 数组型值
    data_quality INTEGER DEFAULT 100,       -- 数据质量评分
    source_id VARCHAR,                      -- 数据源ID
    metadata JSON,                          -- 元数据字段（灵活存储）
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (trade_date, data_type, data_subtype, entity_type, entity_id)
);

-- 另类数据类型信息表
CREATE TABLE IF NOT EXISTS alternative_types (
    data_type VARCHAR PRIMARY KEY,          -- 数据类型ID
    type_name VARCHAR,                      -- 类型名称
    description VARCHAR,                    -- 类型描述
    default_entity_type VARCHAR,            -- 默认实体类型
    is_time_series BOOLEAN DEFAULT TRUE,    -- 是否时间序列
    update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 另类数据索引
CREATE INDEX IF NOT EXISTS idx_alt_date ON alternative_data(trade_date);
CREATE INDEX IF NOT EXISTS idx_alt_type ON alternative_data(data_type, data_subtype);
CREATE INDEX IF NOT EXISTS idx_alt_entity ON alternative_data(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_alt_full ON alternative_data(trade_date, data_type, entity_type, entity_id);
```

---

## 四、架构升级设计

### 4.1 新增模块结构

```
Factor_DB/
├── core/
│   ├── connection.py                    # 现有（无需修改）
│   ├── schema.py                        # 扩展（新增表定义）
│   └── metadata_manager.py              # 新增：元数据统一管理器
├── loaders/
│   ├── base.py                          # 现有（无需修改）
│   ├── level1_loader.py                 # 现有
│   ├── daily_loader.py                  # 现有
│   ├── macro_loader.py                  # 新增：宏观数据加载器
│   ├── news_loader.py                   # 新增：舆情数据加载器
│   └── alternative_loader.py            # 新增：另类数据加载器
├── query/
│   ├── base.py                          # 现有
│   ├── price_query.py                   # 现有
│   ├── factor_query.py                  # 现有
│   ├── screen.py                        # 现有
│   ├── macro_query.py                   # 新增：宏观数据查询器
│   ├── sentiment_query.py               # 新增：舆情数据查询器
│   └── alternative_query.py             # 新增：另类数据查询器
├── analytics/                           # 新增：分析模块
│   ├── macro_factor_link.py             # 宏观-因子关联分析
│   ├── sentiment_factor.py              # 舆情-因子分析
│   └── multi_source_analysis.py         # 多源数据融合分析
└── docs/
    └── FUTURE_EXTENSION_PLAN.md         # 本文档
```

### 4.2 升级后的总体架构

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

---

## 五、核心模块设计

### 5.1 元数据管理器 (core/metadata_manager.py)

```python
class MetadataManager:
    """元数据管理器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = DuckDBConnection(db_path, read_only=False)

    def add_category(self, category_id: str, category_name: str,
                     description: str, priority: int = 0) -> bool:
        """添加数据分类"""

    def add_data_source(self, source_id: str, source_name: str,
                        provider: str, update_frequency: str) -> bool:
        """添加数据源"""

    def register_field(self, field_id: str, category_id: str,
                       field_name: str, field_type: str,
                       description: str, unit: str) -> bool:
        """注册数据字典字段"""

    def init_default_metadata(self) -> None:
        """初始化默认元数据（5个分类 + 4个数据源）"""
```

### 5.2 宏观数据加载器 (loaders/macro_loader.py)

```python
class MacroLoader(BaseLoader):
    """宏观数据加载器"""

    def load(self, data_path: Union[Path, pd.DataFrame],
             indicator_id: Optional[str] = None) -> int:
        """加载宏观数据（支持 CSV/Excel/DataFrame）"""

    def load_indicator_info(self, indicators: pd.DataFrame) -> int:
        """加载指标信息"""
```

### 5.3 舆情数据加载器 (loaders/news_loader.py)

```python
class NewsLoader(BaseLoader):
    """舆情数据加载器"""

    def load_news(self, news_df: pd.DataFrame) -> int:
        """加载新闻数据"""

    def load_reports(self, reports_df: pd.DataFrame) -> int:
        """加载研报数据"""
```

### 5.4 宏观数据查询器 (query/macro_query.py)

```python
class MacroQuery(BaseQuery):
    """宏观数据查询器"""

    def get_macro_data(self, indicator_ids: List[str],
                       start_date: Optional[date] = None,
                       end_date: Optional[date] = None,
                       value_type: str = 'raw') -> pd.DataFrame:
        """获取宏观数据"""

    def get_macro_matrix(self, indicator_ids: List[str],
                         start_date: date, end_date: date) -> pd.DataFrame:
        """获取宏观数据矩阵（日期 x 指标）"""

    def calculate_yoy(self, indicator_id: str,
                      periods: int = 12) -> pd.DataFrame:
        """计算同比"""

    def identify_macro_regime(self, start_date: date,
                              end_date: date) -> pd.DataFrame:
        """识别宏观环境（基于M2和CPI的四象限分类）"""
```

### 5.5 舆情数据查询器 (query/sentiment_query.py)

```python
class SentimentQuery(BaseQuery):
    """舆情数据查询器"""

    def get_stock_sentiment(self, stock_code: str,
                            start_date: Optional[date] = None,
                            end_date: Optional[date] = None) -> pd.DataFrame:
        """获取指定股票的舆情数据"""

    def get_sentiment_aggregation(self, stock_codes: Optional[List[str]] = None,
                                  start_date: Optional[date] = None,
                                  end_date: Optional[date] = None,
                                  freq: str = 'D') -> pd.DataFrame:
        """获取情感数据聚合（按日/周/月）"""

    def get_sentiment_stocks(self, sentiment_label: str = 'positive',
                             query_date: Optional[date] = None,
                             limit: int = 100) -> pd.DataFrame:
        """获取指定情感类型的股票列表（用于选股）"""
```

> **技术细节**: DuckDB 数组类型在 Python 边界返回字符串格式（如 `"['000001.SZ']"`），查询器使用 `ast.literal_eval` 进行多层回退解析。

### 5.6 另类数据查询器 (query/alternative_query.py)

```python
class AlternativeQuery(BaseQuery):
    """另类数据查询器"""

    def get_data(self, data_type: str,
                 start_date: Optional[date] = None,
                 end_date: Optional[date] = None,
                 entity_type: Optional[str] = None,
                 entity_id: Optional[str] = None) -> pd.DataFrame:
        """获取另类数据"""

    def get_correlation_with_price(self, stock_code: str,
                                   data_type: str,
                                   price_field: str = 'close') -> Optional[float]:
        """计算另类数据与价格的相关性"""
```

### 5.7 宏观-因子关联分析 (analytics/macro_factor_link.py)

```python
class MacroFactorLink:
    """宏观-因子关联分析"""

    def __init__(self, db_path: str):
        self.macro_query = MacroQuery(db_path)
        self.factor_query = FactorQuery(db_path)

    def calculate_correlation(self, macro_indicator: str,
                              factor_name: str,
                              method: str = 'pearson') -> float:
        """计算宏观指标与因子的相关性"""

    def build_macro_factor(self, macro_indicators: List[str],
                           lookback: int = 20,
                           method: str = 'equal_weight') -> pd.DataFrame:
        """构建宏观因子（等权或PCA加权）"""
```

### 5.8 多源数据融合分析 (analytics/multi_source_analysis.py)

```python
class MultiSourceAnalysis:
    """多源数据融合分析"""

    def combine_score(self, trade_date: date,
                      stock_codes: Optional[List[str]] = None,
                      weights: Optional[Dict[str, float]] = None) -> pd.DataFrame:
        """综合评分（价格/因子/舆情/另类数据加权）"""

    def multi_dimension_screen(self, trade_date: date,
                               min_sentiment: Optional[float] = None,
                               min_momentum: Optional[float] = None,
                               max_volatility: Optional[float] = None,
                               limit: int = 50) -> pd.DataFrame:
        """多维度筛选（舆情/动量/波动率）"""
```

---

## 六、与现有因子系统的集成设计

### 6.1 跨数据类型查询设计

```python
# 在 screen.py 中扩展条件选股支持
class StockScreener(BaseQuery):
    # ... 现有代码

    def screen_with_macro(self, trade_date: date,
                         macro_conditions: Dict[str, Any],
                         factor_conditions: Dict[str, Any]) -> pd.DataFrame:
        """结合宏观条件和因子条件选股"""

    def screen_with_sentiment(self, trade_date: date,
                            min_sentiment: float = 0.3,
                            factor_conditions: Dict[str, Any]) -> pd.DataFrame:
        """结合舆情条件和因子条件选股"""
```

### 6.2 数据融合分析设计

```python
# analytics/multi_source_analysis.py
class MultiSourceAnalysis:
    """多源数据融合分析"""

    def combine_analysis(self, date: date,
                        stock_codes: Optional[List[str]] = None,
                        data_sources: List[str] = ['price', 'factor', 'macro', 'sentiment']) -> pd.DataFrame:
        """多源数据合并分析

        返回格式：
            股票代码 | 价格数据 | 因子数据 | 宏观环境标签 | 舆情得分 | 另类数据指标...
        """
```

---

## 七、实施路线图

### Phase 1: 基础设施升级 ✅
- [x] 扩展 `core/schema.py`，新增 10+ 表定义
- [x] 实现 `core/metadata_manager.py` 元数据管理
- [x] 更新 `requirements.txt`，添加依赖
- [x] 编写基础设施测试

### Phase 2: 宏观数据模块 ✅
- [x] 实现 `loaders/macro_loader.py` 宏观数据加载器
- [x] 实现 `query/macro_query.py` 宏观数据查询器
- [x] 编写宏观数据测试（5个测试用例）

### Phase 3: 舆情数据模块 ✅
- [x] 实现 `loaders/news_loader.py` 舆情数据加载器
- [x] 实现 `query/sentiment_query.py` 舆情数据查询器
- [x] 编写舆情数据测试（4个测试用例）

### Phase 4: 另类数据模块 ✅
- [x] 实现 `loaders/alternative_loader.py` 另类数据加载器
- [x] 实现 `query/alternative_query.py` 另类数据查询器
- [x] 编写另类数据测试（4个测试用例）

### Phase 5: 分析与集成模块 ✅
- [x] 实现 `analytics/macro_factor_link.py`
- [x] 实现 `analytics/sentiment_factor.py`
- [x] 实现 `analytics/multi_source_analysis.py`
- [x] 编写分析模块测试（4个测试用例）

### Phase 6: 集成测试与性能测试 ✅
- [x] 全部 49 个测试用例通过
- [x] 原有 28 个测试保持通过（向后兼容）
- [x] 新增 21 个扩展测试全部通过

### Phase 7: 文档更新 ✅
- [x] 更新 `README.md`（项目结构、使用示例、表结构）
- [x] 更新 `FUTURE_EXTENSION_PLAN.md`（标记完成状态）

---

## 八、测试统计

| 测试模块 | 测试数量 | 状态 |
|---------|---------|------|
| 连接管理 | 5 | ✅ 通过 |
| 表结构管理 | 6 | ✅ 通过 |
| 价格查询 | 9 | ✅ 通过 |
| 适配器 | 8 | ✅ 通过 |
| 元数据管理 | 4 | ✅ 通过 |
| 宏观数据 | 5 | ✅ 通过 |
| 舆情数据 | 4 | ✅ 通过 |
| 另类数据 | 4 | ✅ 通过 |
| 分析模块 | 4 | ✅ 通过 |
| **总计** | **49** | **✅ 全部通过** |

---

## 九、技术要点与注意事项

### 9.1 DuckDB 数组处理

DuckDB 的 `VARCHAR[]` 数组类型在 Python 边界可能返回字符串格式（如 `"['000001.SZ']"`）。解决方案：

```python
import ast

def parse_array(value):
    """解析 DuckDB 数组"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return value.strip('[]').replace("'", "").split(', ') if value != '[]' else []
    return []
```

### 9.2 冲突处理

DuckDB 的 `INSERT OR REPLACE` 需要显式指定冲突目标。对于多列 UNIQUE 约束：

```python
# 使用 ON CONFLICT DO NOTHING
conn.execute("""
    INSERT INTO alternative_data (...)
    VALUES (...)
    ON CONFLICT DO NOTHING
""")
```

### 9.3 索引限制

DuckDB 不支持 GIN 索引和数组列索引。已移除相关索引定义，通过标准 B-tree 索引和 SQL 函数实现等效查询。

---

## 十、总结

本升级方案的关键优势：

1. **保持架构一致性**：完全遵循现有高内聚、低耦合的架构设计
2. **插件式扩展**：新增数据类型只需添加对应 loader/query，不影响现有代码
3. **元数据统一管理**：提供一致的数据字典和数据源管理
4. **强大的分析能力**：支持宏观-因子关联、多源数据融合、舆情选股等高级功能
5. **向后兼容**：所有现有模块和接口保持不变，原有 28 个测试全部通过
6. **完整测试覆盖**：新增 21 个测试，总计 49 个测试用例全部通过
