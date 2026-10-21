# 因子写路径统一：实施文档

> 依据：[设计文档](../design/factor_write_path_design.md)
> 日期：2026-10-21

---

## 1. 改动清单

| 阶段 | 文件 | 内容 |
|------|------|------|
| S1 | `core/schema.py` | 新增 `object_type(table_name)` 公有方法（`TABLE` / `VIEW` / `None`） |
| S2 | `loaders/base.py` | 新增 `write_factor_long()`、`_write_factor_from_temp()`、`_ensure_factor_tables()` |
| S3 | `loaders/daily_loader.py` | `load_factor_to_wide` 复用 `_write_factor_from_temp`；`_import_price_data` 因子分支改双写；删除 `_import_to_factor_table` |
| S4 | `loaders/csmar_loader.py` | `_import_stock_factor` / `_import_market_factor` 改为注册临时表 + `_write_factor_from_temp` |
| S5 | `scripts/import_phase1_ashare.py` | barra 因子按 `factor_name` 分组后调用 `write_factor_long` |
| S6 | `core/schema.py` | `migrate_factor_data_to_wide` 补齐已有 `factor_wide` 的缺失列 |
| S7 | `core/schema.py` | `_replace_factor_data_with_view` 改为动态 UNPIVOT 视图（新因子列自动可见） |

---

## 2. 关键实现细节

### S2 — BaseLoader

```python
def _ensure_factor_tables(self, conn, factor_name: str) -> str:
    """确保 history/wide 存在且含该因子列，返回引用后的列名"""
    from Factor_DB.core.schema import SchemaManager
    schema = SchemaManager(self.conn)
    if not schema.table_exists('factor_wide'):
        schema.create_factor_wide([factor_name])
    elif factor_name not in schema.get_factor_columns('factor_wide'):
        schema.add_factor_column('factor_wide', factor_name)
    if not schema.table_exists('factor_history'):
        schema.create_factor_history()
    return quote_identifier(factor_name)


def _write_factor_from_temp(self, conn, temp_table: str, factor_name: str) -> int:
    validate_identifier(factor_name, '因子名')
    factor_col = self._ensure_factor_tables(conn, factor_name)

    legacy = SchemaManager(self.conn).object_type('factor_data') == 'BASE TABLE'

    conn.execute('BEGIN TRANSACTION')
    try:
        conn.execute(f'''
            INSERT INTO factor_history (trade_date, stock_code, factor_name, factor_value)
            SELECT trade_date, stock_code, $factor_name, factor_value
            FROM {quote_identifier(temp_table)}
        ''', {'factor_name': factor_name})

        conn.execute(f'''
            INSERT INTO factor_wide (trade_date, stock_code, {factor_col}, loaded_at)
            SELECT trade_date, stock_code, factor_value, CURRENT_TIMESTAMP
            FROM {quote_identifier(temp_table)}
            ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                {factor_col} = EXCLUDED.{factor_col},
                loaded_at = EXCLUDED.loaded_at
        ''')

        if legacy:
            conn.execute('''
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                SELECT trade_date, stock_code, $factor_name, factor_value
                FROM {temp}
            '''.format(temp=quote_identifier(temp_table)), {'factor_name': factor_name})

        wide_count = conn.execute('SELECT COUNT(*) FROM factor_wide').fetchone()[0]
        conn.execute('COMMIT')
    except Exception:
        try:
            conn.execute('ROLLBACK')
        except Exception:
            pass
        raise
    return wide_count
```

注意：`temp_table` 必须是内存临时表或已注册视图，且列名为 `trade_date / stock_code / factor_value`。

### S3 — DailyLoader

- `load_factor_to_wide`：保留校验、宽→长 UNPIVOT，改为调用 `self._write_factor_from_temp(conn, '_long_tmp', factor_name)`，返回其返回值。
- `_import_price_data` 因子分支：`_long` 的第三列别名由 `val` 改为 `factor_value`；OHLCV 分支同步改为 `factor_value AS {field_name}`。因子分支调用 `_write_factor_from_temp(conn, '_long', field_name)`。
- 删除 `_import_to_factor_table`。

### S4 — CSMARLoader

两个方法都已有长表 `sub_df`：

```python
with self.conn as conn:
    conn.register('_csmar_tmp', sub_df[['trade_date', 'stock_code', 'factor_value']])
    self._write_factor_from_temp(conn, '_csmar_tmp', full_factor_name)
    conn.unregister('_csmar_tmp')
```

异常不吞：记录后重新抛出（保持与 `write_factor_long` 一致）。

### S5 — Phase 1 barra

```python
for factor_name, group in df_barra.groupby('factor_name'):
    self.loader.write_factor_long(
        group[['trade_date', 'stock_code', 'factor_value']], factor_name
    )
```

### S6 — 迁移补列

在 `create_factor_wide(all_factors)` 后补齐：

```python
existing = set(self.get_factor_columns('factor_wide'))
missing = [f for f in all_factors if f not in existing]
for f in missing:
    self.add_factor_column('factor_wide', f)
```

---

## 3. 测试矩阵（TDD）

| 测试 | 覆盖 |
|------|------|
| `test_write_factor_long_creates_tables` | 迁移前三表写入（factor_data/history/wide） |
| `test_write_factor_long_after_migration` | `factor_data` 为视图时写入不报错，视图可读到新数据 |
| `test_write_factor_long_rejects_bad_input` | 缺列 / 空表 / 非法因子名 → ValueError |
| `test_load_wide_factor_after_migration` | `DailyLoader.load()` 因子文件在迁移后仍可写 |
| `test_load_ohlcv_unchanged` | OHLCV 仍写 `daily_prices` |
| `test_csmar_market_factor_after_migration` | CSMAR 市场因子路径迁移后可用 |
| `test_migration_adds_missing_wide_columns` | 部分宽表（仅 1 列）迁移后补齐所有列 |
| `test_factor_data_view_sees_new_columns_after_migration` | 迁移后新增因子列在 `factor_data` 视图中可见 |

---

## 4. 验收命令

```powershell
python -m pytest tests/test_factor_write_path.py tests/test_wide_loader.py tests/test_migration.py tests/test_daily_loader.py -q
python -m pytest -q
```

静态检查：`rg "INSERT INTO factor_data" loaders scripts` 只应出现在 `BaseLoader._write_factor_from_temp` 的 legacy 分支（由 `object_type` 判定）。
