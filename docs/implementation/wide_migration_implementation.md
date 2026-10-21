# 双轨迁移实施文档

> 主题：`factor_data` → `factor_wide` + `factor_history` 迁移的内存安全实现
> 依据：[调研文档](../research/wide_migration_research.md)、[设计文档](../design/wide_migration_design.md)
> 日期：2026-07

---

## 1. 分步实施计划

| 步骤 | 内容 | 验证方式 |
|------|------|---------|
| Step 1 | 新增 `_configure_migration()` 资源参数设置 | 单元测试 |
| Step 2 | 替换 `_migrate_single_factor()` 去重逻辑为 `arg_max()` | 单元测试 |
| Step 3 | 迁移入口调用配置方法 | 单元测试 |
| Step 4 | 全量回归测试 | `pytest` |
| Step 5 | 生产库磁盘空间检查 + 实际迁移 | 手动 |

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 实测 DuckDB 1.5.4（`arg_max` 完全支持，已实测验证语义）；`pyproject.toml` 声明 >=0.10.0 偏旧但满足功能需求，无需升级 |
| **依赖** | 无新增/移除依赖 |
| **兼容** | 公共方法签名 `migrate_factor_data_to_wide(batch_size)` 和 `_migrate_single_factor(factor_name)` 不变 |
| **接口** | 仅内部实现变更，外部调用方无感知 |
| **签名** | 新增 `_configure_migration(self, conn)` — 私有方法，接收底层 duckdb 连接 |
| **低效操作** | 消除 `ROW_NUMBER() OVER (PARTITION BY ...)` 窗口函数（blocking operator），改用 `arg_max()` 聚合 |
| **内存** | `memory_limit=24GB`、`threads=4`、`temp_directory` 磁盘溢出 |
| **数据一致性** | `arg_max(factor_value, COALESCE(loaded_at, CURRENT_TIMESTAMP))` 保证取最新版本；NULL 版本兜底 |

---

## 3. 涉及修改的文件清单

- `core/schema.py` — 唯一修改文件
  - 新增方法 `_configure_migration()`
  - 修改方法 `_migrate_single_factor()`（内部 SQL）
  - 修改方法 `migrate_factor_data_to_wide()`（Step 4 前调用配置）
- `tests/test_migration.py` — 新增 arg_max 正确性测试

---

## 4. 接口/签名变更清单

### 4.1 新增方法

```python
def _configure_migration(self, conn) -> None:
    """迁移专用资源参数设置

    Args:
        conn: 底层 duckdb 连接（由 with self.conn as conn 提供）
    """
    conn.execute("SET memory_limit='24GB'")
    conn.execute("SET threads=4")
    conn.execute("SET preserve_insertion_order=false")
    # temp_directory 在连接建立时设置，不在此重复
```

### 4.2 修改 `_migrate_single_factor()` 内部 SQL

**Before**（`ROW_NUMBER` 窗口函数）：
```python
wide_sql = f'''
    INSERT INTO factor_wide (...)
    SELECT ... FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY trade_date, stock_code
            ORDER BY loaded_at DESC NULLS LAST
        ) AS rn
        FROM factor_data WHERE factor_name = '{factor_name}' AND factor_value IS NOT NULL
    ) sub WHERE rn = 1
    ON CONFLICT (trade_date, stock_code) DO UPDATE SET ...
'''
```

**After**（`arg_max` 聚合）：
```python
wide_sql = f'''
    INSERT INTO factor_wide (trade_date, stock_code, loaded_at, "{factor_name}")
    SELECT
        trade_date,
        stock_code,
        max(loaded_at) AS loaded_at,
        arg_max(factor_value, COALESCE(loaded_at, CURRENT_TIMESTAMP)) AS factor_value
    FROM factor_data
    WHERE factor_name = '{factor_name}' AND factor_value IS NOT NULL
    GROUP BY trade_date, stock_code
    ON CONFLICT (trade_date, stock_code) DO UPDATE SET
        "{factor_name}" = EXCLUDED."{factor_name}",
        loaded_at = EXCLUDED.loaded_at
'''
```

> 注：`arg_max(arg, val)` 返回 `val` 最大时对应的 `arg`。此处 `val=COALESCE(loaded_at, CURRENT_TIMESTAMP)`，取 loaded_at 最新版本对应的 factor_value。

### 4.3 修改 `migrate_factor_data_to_wide()` Step 4

在逐因子迁移循环前，先配置资源参数（每个 `with self.conn as conn` 块内）。

---

## 5. 测试计划（TDD）

### 新增测试 `tests/test_migration.py`

| 测试方法 | 验证点 |
|---------|--------|
| `test_migrate_single_factor_uses_arg_max_latest_version` | 多版本因子，arg_max 正确取 loaded_at 最新版本值 |
| `test_configure_migration_sets_params` | `_configure_migration` 设置 memory_limit/threads 生效 |

### 现有测试必须保持通过
- `test_migration.py` 现有 6 个测试（验证迁移后表结构、行数、视图兼容、PK、upsert 无重复）

---

## 6. 风险提示（执行前必查）

1. **磁盘空间**：迁移需 `factor_data`（46GB）+ `factor_wide` + `factor_history` 同时存在，预留 >100GB
2. **迁移耗时**：16.8 亿行逐因子迁移，预计数小时，需后台执行 + 断点续传
3. **备份**：迁移会 `DROP TABLE factor_data`，执行前必须备份 DB 文件
