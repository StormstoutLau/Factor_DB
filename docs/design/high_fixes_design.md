# HIGH 级问题修复设计文档

> 依据：[调研文档](../research/high_fixes_research.md)（E1–E13、C1–C7）
> 范围：HIGH 1 / HIGH 2 / HIGH 3

---

## 1. 设计目标与非目标

### 目标
- HIGH 1：query 层与迁移/写入路径消除运行时值拼接；标识符受白名单/引用保护；缓存不因参数不同而串结果
- HIGH 2：`load_factor_to_wide` 入口拒绝长表并抛异常；README 与实现一致
- HIGH 3：内存、线程、临时目录、数据根目录可配置，默认值与现有行为一致

### 非目标
- 不引入 Web 安全审计、不做通用 SQL 防火墙
- 不重写 query 层 API 签名（除内部 builder）
- 不修改数据导入流程与数据库结构
- 不处理 P2/P3 项（单例、缓存 LRU、LICENSE 等）

---

## 2. 关键决策

### D1：命名参数 `$name`，而非位置参数 `?`
依据 E1：命名参数可在 SQL 中复用，适合 `get_ic_series` 的 `date_filter` 三次出现和 OSINT UNION ALL 两次 WHERE。
位置参数需要按出现顺序重复传值，重复片段容易漏传（C6 即此类问题）。

### D2：值与标识符双轨
- **值**（日期、股票代码、因子名、类别、实体 ID）：`bind()` 写入参数字典，SQL 中只留 `$name`（E2）
- **标识符**（表名、列名、排序字段、宽表因子列）：不能绑定（E3），采用：
  1. 代码常量/成员校验（如 `_factor_in_wide`）
  2. 正则白名单 `^[A-Za-z_][A-Za-z0-9_]*$`
  3. `quote_identifier()` 双引号转义（E4）
- **外部可控标识符**（`fields`、`sort_by`、`load_factor_to_wide` 的 `factor_name`）：非法直接 `ValueError`

### D3：缓存 key 纳入参数
现有 key = `md5(sql)`。参数化后相同模板不同参数必须区分：
`key = sql + "|" + json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)`
无参数时沿用 `md5(sql)`。

### D4：不能绑定的少数位置
| 位置 | 处理 |
|------|------|
| `read_parquet('...')` 路径 | `escape_literal()` 单引号转义（E5） |
| `LIMIT` | `int()` 校验后插值 |
| `PERCENTILE_CONT(q)` | 校验 `0 <= q <= 1` 后插值 |
| 窗口 `PARTITION BY ... ORDER BY factor_value {direction}` | `direction` 限定 `ASC`/`DESC` |

### D5：宽表入口校验与失败语义
```python
def _validate_wide_factor_input(df, factor_name) -> None:
    # 空 DataFrame / 含长表列 / 首列不可转日期 / 无股票列 / 非法标识符 → ValueError
```
失败统一 `raise`（不再 `return 0`）。依据 C2/C3：唯一调用方是测试，无生产破坏面。

### D6：配置外置
`LoaderConfig` 新增字段，`__post_init__` 解析环境变量；默认值保持现状：
| 字段 | 环境变量 | 默认值 |
|------|----------|--------|
| `memory_limit` | `FACTOR_DB_MEMORY_LIMIT` | `20GB`（显式保守值，官方默认是 80% RAM） |
| `threads` | `FACTOR_DB_THREADS` | `4` |
| `temp_directory` | `FACTOR_DB_TEMP_DIR` | `f"{db_path}.tmp"`（与 E6 官方默认一致） |
| `preserve_insertion_order` | — | `False` |

---

## 3. 接口契约

### 3.1 `utils/sql_utils.py`（新增）

```python
def bind(params: dict, name: str, value: Any) -> str
    """params[name] = value; return '$name'。同名重复绑定不同值抛 ValueError。"""

def bind_in(params: dict, prefix: str, values: Sequence[Any]) -> str
    """返回 '$prefix_0, $prefix_1, ...'，逐项写入 params。空序列抛 ValueError。"""

def quote_identifier(name: str) -> str
    """返回 '"name"'，内部双引号重复转义。"""

def validate_identifier(name: str, label: str = 'identifier') -> str
    """校验 ^[A-Za-z_][A-Za-z0-9_]*$；非法抛 ValueError；返回原值。"""

def escape_literal(value: Any) -> str
    """返回单引号包裹的字面量，内部单引号重复转义。仅用于不能绑定的位置。"""
```

### 3.2 `query/base.py` 变更

```python
def _build_date_filter(self, filter: QueryFilter, params: dict) -> str
def _build_stock_filter(self, filter: QueryFilter, params: dict) -> str
def _build_field_selector(self, filter, default_fields) -> str  # 内部 validate_identifier
def _execute_query(self, sql: str, params: Optional[dict] = None) -> pd.DataFrame
def _build_pit_subquery(self, table, pk_columns, extra_conditions='1=1',
                        as_of=None, non_null_columns=None,
                        params: Optional[dict] = None) -> str
```

兼容性：
- `_build_pit_subquery` 的 `params` 可选；`test_pit_null_filter.py` 不传参仍可工作（该用例不涉及 `as_of`）
- `_execute_query` 的 `params` 可选；现有调用不改也能运行（但生产调用将全部迁移）

### 3.3 `loaders/daily_loader.py`

```python
def load_factor_to_wide(self, df: pd.DataFrame, factor_name: str) -> int
    """成功：宽表总行数；失败：抛 ValueError（格式）或原异常（写入）。"""
```

---

## 4. 数据流

### 4.1 查询
```
QueryFilter → params={}
  → _build_date_filter(filter, params)   → "trade_date >= $start_date"
  → _build_stock_filter(filter, params)  → "stock_code IN ($stock_code_0, ...)"
  → _build_pit_subquery(..., params)     → "... AND loaded_at <= $as_of"
  → SQL 组装
  → _execute_query(sql, params)
      → cache key = sql + params 序列化
      → conn.fetchdf(sql, params)
```

### 4.2 宽表写入
```
load_factor_to_wide(df, factor_name)
  → _validate_wide_factor_input（长表/空/非法标识符 → ValueError）
  → validate_identifier(factor_name) + quote_identifier（列名）
  → UNPIVOT → _long_tmp
  → INSERT factor_history ... $factor_name（值参数）
  → INSERT factor_wide ... "{factor_name}"（标识符引用）
```

---

## 5. 安全不变量

| 编号 | 不变量 |
|------|--------|
| S1 | 任何运行时值不得以字符串字面量形式进入 SQL（可绑定位置） |
| S2 | 任何运行时标识符必须经过 `validate_identifier` 或成员校验 + `quote_identifier` |
| S3 | 相同 SQL 模板、不同参数不得命中同一缓存条目 |
| S4 | 非法宽表输入不得静默返回 0，必须抛异常 |
| S5 | 配置默认值必须与现有行为一致，环境变量可覆盖 |

---

## 6. 风险与权衡

| 风险 | 缓解 |
|------|------|
| prepared statement 类型推断差异 | `date`/`time`/`as_of` 显式 `CAST` |
| 参数序列化不稳定导致缓存命中率低 | `sort_keys=True`、日期转 ISO、测试覆盖 |
| 标识符白名单误伤未来中文因子名 | 白名单只用于用户可控标识符；内部列名走 `quote_identifier`；现有因子名已实证为 ASCII（C7） |
| `screen.screen(conditions=...)` 仍为原生 SQL | 文档标注「调用方保证可信」，不声称该入口安全 |
| 大规模改动引入回归 | 分 7 阶段执行，每阶段跑定向测试，最后全量 245+ |

---

## 7. Review 记录（排除幻觉）

- 官方文档与社区案例均已实际抓取，不是凭记忆断言
- 现有因子名 charset 通过 `E:\Ashare_data` 文件名实测（C7），未假设中文/引号
- OSINT 缺参标记为「待测试确认」（C6），未写成已复现
- McKinsey 案例标记为二手媒体来源，仅作佐证
- `load_factor_to_wide` 调用方通过 grep 实证（C3），未假设有生产调用
