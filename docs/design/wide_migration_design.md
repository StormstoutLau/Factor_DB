# 双轨迁移设计文档

> 主题：`factor_data` → `factor_wide` + `factor_history` 迁移的内存安全设计
> 依据：[调研文档](../research/wide_migration_research.md)
> 日期：2026-07

---

## 1. 设计目标与非目标

### 目标
- 在 32GB 内存约束下，将 16.8 亿行 EAV 长表安全迁移到宽表 + 历史长表双轨
- 迁移过程不 OOM、可断点续传、可验证
- 保持 `factor_data` 视图向后兼容

### 非目标
- 不改动查询层接口（`factor_query.py` 已支持双轨路由）
- 不重构 `factor_wide` 表结构设计（ADR-0004 已定）
- 不处理 Level 1 / CSMAR 数据

---

## 2. 架构设计

### 2.1 迁移数据流

```
factor_data (16.8亿行 EAV, 46GB)
    │
    ├── [逐因子] arg_max 去重 → factor_wide (宽表, 10.5M行)
    │        GROUP BY (trade_date, stock_code)
    │        arg_max(factor_value, loaded_at)
    │
    └── [逐因子] 全量版本 → factor_history (长表, 无PK)
             INSERT ... SELECT (保留所有 loaded_at 版本)
    │
    ▼
factor_data 替换为视图 (UNPIVOT factor_wide)
```

### 2.2 核心改进：去重逻辑替换

**现有（风险高）**：
```sql
ROW_NUMBER() OVER (PARTITION BY trade_date, stock_code ORDER BY loaded_at DESC NULLS LAST) AS rn
```

**设计（内存安全）**：
```sql
SELECT trade_date, stock_code, arg_max(factor_value, loaded_at) AS factor_value,
       max(loaded_at) AS loaded_at
FROM factor_data
WHERE factor_name = '{factor}' AND factor_value IS NOT NULL
GROUP BY trade_date, stock_code
```

依据：调研文档 §4.2，`arg_max()` 是聚合函数，内存效率远高于窗口函数。

### 2.3 资源配置设计

| 参数 | 值 | 依据 |
|------|-----|------|
| `memory_limit` | 24GB（75% 物理内存） | 留 8GB 给 OS + 其他进程 |
| `threads` | 4 | 减少并发内存碎片化（调研 §3.1） |
| `temp_directory` | 独立大磁盘目录 | 磁盘溢出安全网 |
| `preserve_insertion_order` | false | 官方缓解 OOM 建议 |

### 2.4 断点续传设计

迁移记录已完成因子，支持中断后跳过已迁移因子（`factor_wide` 已有该因子的列即有数据，或维护迁移状态表）。

---

## 3. 接口契约

### 3.1 保留现有公共接口（向后兼容）

```python
class SchemaManager:
    def migrate_factor_data_to_wide(self, batch_size: int = 5) -> dict:
        """迁移入口，签名不变"""
    
    def _migrate_single_factor(self, factor_name: str) -> int:
        """单因子迁移，内部实现替换 arg_max，签名不变"""
```

### 3.2 新增方法（内部）

```python
def _configure_migration(self, conn) -> None:
    """设置迁移专用资源参数（memory_limit/threads/temp_directory）"""
```

---

## 4. 关键决策及依据

| 决策 | 依据 |
|------|------|
| 用 `arg_max()` 替代 `ROW_NUMBER() OVER` | 调研 §3.2、§4.2 |
| `memory_limit=24GB` 而非默认 25GB | 调研 §4.1（OOM reaper 建议 50-60%，此处取 75% 平衡速度与安全） |
| `threads=4` | 调研 §4.1（过多线程加剧内存碎片化） |
| 逐因子分批（batch_size） | 调研 §5.4，避免单次全量 materialize |
| 保留 `factor_data` 视图 | ADR-0004 向后兼容要求 |

---

## 5. 风险与权衡

| 风险 | 缓解 | 残留风险 |
|------|------|---------|
| 磁盘空间不足（需 >100GB） | 迁移前检查磁盘剩余空间 | 若不足则中止并提示 |
| `arg_max` 在 NULL loaded_at 上的行为 | 用 `COALESCE(loaded_at, CURRENT_TIMESTAMP)` 兜底 | 历史 NULL 版本语义需确认 |
| 迁移耗时（16.8 亿行） | 逐因子 + 磁盘溢出 | 可能数小时，需后台执行 |
| 视图 `UNPIVOT` 查询性能 | 宽表已物理排序 + zonemap | 冷查询首次慢 |

---

## 6. Review 记录（排除幻觉）

- [x] 现有代码签名已核实（`_migrate_single_factor` 实际存在于 schema.py L900）
- [x] `arg_max` 语法已核实（DuckDB 聚合函数，`arg_max(arg, val)`）
- [x] 物理数据 8GB vs 文件 46GB 差异已实测（非臆测）
- [x] `idx_factor_name_date` 索引已实测存在（迁移的 WHERE 过滤可用）
- [x] 无臆造不存在的 DuckDB 函数/特性
