# HIGH 级问题修复实施文档

> 依据：[设计文档](../design/high_fixes_design.md) · [调研文档](../research/high_fixes_research.md)
> 执行顺序 S1–S7，每阶段独立跑测试。

---

## S1 文档与红灯测试

新增：
- `tests/test_sql_safety.py`
- `tests/test_wide_loader.py` 追加 3 个用例
- `tests/test_daily_loader.py` 追加 2 个用例

红灯预期：
- `test_sql_safety.py`：`ImportError: cannot import name 'sql_utils'`（文件未建）
- 宽表长表用例：`ValueError` 未被抛出（当前 `return 0`）
- `LoaderConfig` 环境变量用例：`TypeError: unexpected keyword argument 'memory_limit'`

---

## S2 查询基础设施

### 新增 `utils/sql_utils.py`
实现 `bind` / `bind_in` / `quote_identifier` / `validate_identifier` / `escape_literal`。

工程细节：
- `bind` 同名重复绑定：允许**相同值**重复（幂等），不同值抛 `ValueError`
- `bind_in` 空序列抛 `ValueError`，调用方需先判空
- `quote_identifier` 不校验内容（允许任意字符），只负责转义
- `validate_identifier` 使用 `re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name)`
- `escape_literal`：`str(value).replace("'", "''")` 后包单引号

### `query/base.py`
Before → After：

| Before | After |
|--------|-------|
| `_build_date_filter(filter) -> str` | `_build_date_filter(filter, params) -> str` |
| `f"trade_date >= '{filter.start_date}'"` | `"trade_date >= $start_date"` + `params['start_date']` |
| `_build_stock_filter(filter) -> str` | `_build_stock_filter(filter, params) -> str` |
| `', '.join([f"'{code}'" ...])` | `bind_in(params, 'stock_code', filter.stock_codes)` |
| `_build_pit_subquery(..., as_of)` | 增加 `params: Optional[dict] = None`，`AND loaded_at <= $as_of` |
| `_execute_query(sql)` | `_execute_query(sql, params=None)`；缓存 key 含参数 |
| `_build_field_selector` 直接拼 | 逐项 `validate_identifier` 后拼接 |

缓存 key 实现：
```python
def _cache_key(sql: str, params: Optional[dict]) -> str:
    if not params:
        return sql
    return sql + '|' + json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
```

---

## S3 七个 query 模块

统一模式：
```python
params: dict = {}
date_filter = self._build_date_filter(filter, params)
stock_filter = self._build_stock_filter(filter, params)
pit = self._build_pit_subquery(..., extra_conditions="factor_name = $factor_name",
                               params=params)
params['factor_name'] = factor_name
df = self._execute_query(sql, params)
```

逐文件要点：

| 文件 | 关键改动 |
|------|----------|
| `factor_query.py` | 宽表列名 `quote_identifier(factor_name)`（成员校验后）；历史路径值 `$factor_name`；`get_ic_series` 的 `date_filter` 复用只绑定一次；`get_factor_stats` 的 `trade_date` 条件改 `$stat_date` |
| `price_query.py` | `trade_time >= CAST($start_time AS TIME)`，值传 `start_time.isoformat()`；`trade_date` 传 `date` 对象；`read_parquet` 路径 `escape_literal`；`industry = $industry` |
| `screen.py` | `factors` 名作为值 `$factor_0...`，作为别名 `validate_identifier`；`sort_by` 白名单；`limit` 转 `int`；`quantile` 校验 0–1；`conditions` 保持原生 SQL 并在 docstring 标注可信输入 |
| `macro_query.py` | `indicator_ids` → `bind_in`；`value_type`/`category`/`indicator_id` → `$...`；日期区间 → `$start_date`/`$end_date` |
| `sentiment_query.py` | `stock_code`/`sentiment_label`/`query_date` → 参数；日期过滤统一 builder |
| `alternative_query.py` | `data_type`/`data_subtype`/`entity_type`/`entity_id`/日期 → 参数；`price_field` 白名单（`open/high/low/close/volume/amount`）；`lag` 转 `int` |
| `osint_query.py` | 命名参数替换位置参数；UNION ALL 两个 WHERE 复用同一组 `$` 参数（修复 C6） |

`macro_query.py` / `sentiment_query.py` / `alternative_query.py` 中不经过 `BaseQuery` builder 的日期条件也一并改为 `$` 参数。

---

## S4 loader + config + README（HIGH 2 + HIGH 3）

### `loaders/base.py`
```python
@dataclass
class LoaderConfig:
    db_path: str = 'factor_db.duckdb'
    batch_size: int = 10000
    skip_existing: bool = True
    validate_data: bool = True
    show_progress: bool = True
    memory_limit: Optional[str] = None
    threads: Optional[int] = None
    temp_directory: Optional[str] = None
    preserve_insertion_order: bool = False

    def __post_init__(self):
        if self.memory_limit is None:
            self.memory_limit = os.getenv('FACTOR_DB_MEMORY_LIMIT', '20GB')
        if self.threads is None:
            self.threads = int(os.getenv('FACTOR_DB_THREADS', '4'))
        if self.temp_directory is None:
            self.temp_directory = os.getenv('FACTOR_DB_TEMP_DIR', f'{self.db_path}.tmp')
```

`BaseLoader._apply_settings(conn)`：
```python
conn.execute(f"SET memory_limit='{escape_literal(self.config.memory_limit)}'")
conn.execute(f"SET threads={int(self.config.threads)}")
conn.execute(f"SET temp_directory={escape_literal(self.config.temp_directory)}")
if not self.config.preserve_insertion_order:
    conn.execute("SET preserve_insertion_order=false")
```
数值/标识符先做类型校验，字面量用 `escape_literal`。

### `loaders/daily_loader.py`
- `__init__` 与 `load_factor_to_wide` 的三组 SET 全部替换为 `self._apply_settings(conn)`
- `_import_price_data` 的 SET 同样替换
- 新增 `_validate_wide_factor_input(df, factor_name)`
- `load_factor_to_wide` 失败路径 `raise`
- 写入 SQL：`factor_history` 用 `$factor_name`；`factor_wide` 列名 `quote_identifier`

### `utils/config.py`
`_from_dict` 中 `LoaderConfig(...)` → `AppLoaderConfig(...)`（修复 NameError）。

### README
`README.md` / `README_zh.md` 的 `load_factor_to_wide` 示例改为：
```python
df = pd.DataFrame(
    [[15.3, 22.1], [16.0, 23.4]],
    index=pd.to_datetime(['2024-01-02', '2024-01-03']),
    columns=['000001', '000002'],
)
loader.load_factor_to_wide(df, factor_name='PE_ratio')
```
同步测试数量（以实际全量结果为准）。

---

## S5 `core/schema.py`

| 方法 | 改动 |
|------|------|
| `_migrate_single_factor` | 所有 `factor_name = '{...}'` → `$factor_name`，同一 conn 内传 dict；`"{factor_name}"` → `quote_identifier` |
| `pivot_long_to_wide` | 因子名值 → `bind_in`；case 列名 → `quote_identifier` |
| `unpivot_wide_to_long` | `factor_list` 标识符 → `quote_identifier` |
| `create_factor_wide` / `add_factor_column` / `_replace_factor_data_with_view` | 动态列名统一 `quote_identifier` + `validate_identifier` |
| `get_factor_columns` | 表名 `validate_identifier` |

注意：迁移 SQL 中 `factor_name` 来自数据库自身，但仍统一参数化，避免后续被外部输入污染。

---

## S6 脚本路径

`scripts/import_phase1_ashare.py`：
```python
ASHARE_ROOT = Path(os.getenv('ASHARE_DATA_PATH', r'E:\Ashare_data'))
FACTOR_DIR = ASHARE_ROOT / 'financial_factors'
IMPORT_MEMORY_LIMIT = os.getenv('FACTOR_DB_IMPORT_MEMORY_LIMIT', '16GB')
...
factor_files = sorted(FACTOR_DIR.rglob('*.pkl'))
```
脚本内 4 处 `SET memory_limit='16GB'` → `{IMPORT_MEMORY_LIMIT}`（先校验格式 `^\d+(GB|MB)$`）。

只读验证：`FACTOR_DIR.exists()`、`len(list(FACTOR_DIR.rglob('*.pkl'))) > 0`。

---

## S7 文档同步与回归

- `docs/spec/REVIEW_CHECKLIST.md` 勾选本轮
- `docs/DEVLOG.md` 增加 2026-10 HIGH 修复条目
- `docs/reviews/2026-10-claude-audit-analysis.md` 增加「修复状态」表
- 全量 pytest；静态 grep 验收

---

## 测试清单

| 测试 | 断言 |
|------|------|
| `test_sql_safety.py::test_injection_string_is_bound` | stock_code 传 `000001') OR ('1'='1` 返回 0 行、不抛语法错 |
| `test_sql_safety.py::test_factor_name_with_quote` | factor_name 含单引号走历史路径不抛语法错 |
| `test_sql_safety.py::test_cache_key_includes_params` | 同模板不同参数两次查询结果不同，`misses == 2` |
| `test_sql_safety.py::test_reused_date_filter` | `get_ic_series` 复用 date_filter 返回预期行数/无异常 |
| `test_sql_safety.py::test_invalid_identifiers_raise` | 非法 `sort_by` / `fields` / `factor_name` → `ValueError` |
| `test_sql_safety.py::test_quote_identifier` | `quote_identifier('a"b') == '"a""b"'` |
| `test_sql_safety.py::test_osint_union_params` | `get_data_by_source` 返回两表数据（确认/证伪 C6） |
| `test_wide_loader.py::test_rejects_long_format` | 长表输入 → `ValueError` |
| `test_wide_loader.py::test_rejects_empty_or_invalid_name` | 空 df / 非法 factor_name → `ValueError` |
| `test_daily_loader.py::test_loader_config_env` | 环境变量覆盖 `memory_limit/threads/temp_directory` |
| `test_daily_loader.py::test_loader_config_defaults` | 默认 `20GB`/`4`/`f"{db_path}.tmp"` |

---

## 前置条件与工程细节

- Python：`C:\Users\Peng\AppData\Local\Programs\Python\Python312\python.exe`
- DuckDB：1.5.3；命名参数与 dict 传参已确认（E1）
- 不使用 `conn.execute(sql)` 拼接值；SQL 中的 `$` 参数由 dict 提供
- 缓存 key 序列化 `default=str` 保证 `date`/`time` 可序列化
- 所有新增 helper 中文注释，与项目风格一致
