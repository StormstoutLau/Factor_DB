# 因子写路径统一：设计文档

> 依据：[调研文档](../research/factor_write_path_research.md) · [ADR-0004](../../adr/0004-wide-history-dual-track.md)
> 日期：2026-10-21

---

## 1. 目标与非目标

### 目标
- 所有因子写入统一走 `factor_history` + `factor_wide` 双写
- 迁移前（`factor_data` 为表）与迁移后（为视图）行为一致
- OHLCV 写入 `daily_prices` 的行为不变
- 写入失败不产生半成品（单事务）

### 非目标
- 不执行生产迁移（后续任务）
- 不改查询层接口
- 不改 `factor_wide` / `factor_history` 表结构
- 不处理 CSMAR 原始 zip 数据准备

---

## 2. 写入路径

```
因子宽表 DataFrame ──UNPIVOT──┐
CSMAR 长表 DataFrame ─────────┼──> BaseLoader.write_factor_long()
Phase 1 barra 长表 ───────────┘          │
                                         ├─ 1. 确保 factor_history / factor_wide 存在
                                         ├─ 2. BEGIN
                                         ├─ 3. INSERT factor_history（保留版本）
                                         ├─ 4. UPSERT factor_wide（最新值）
                                         ├─ 5. 若 factor_data 为 BASE TABLE：INSERT factor_data（兼容）
                                         └─ 6. COMMIT / ROLLBACK
```

`DailyLoader._import_price_data` 的 OHLCV 分支保持原样（写 `daily_prices`）。

## 3. 接口契约

```python
class BaseLoader:
    def write_factor_long(self, df: pd.DataFrame, factor_name: str) -> int:
        """长格式因子 → history + wide（+ 迁移前的 factor_data）

        Args:
            df: 必须包含 trade_date / stock_code / factor_value 三列
            factor_name: 因子名（标识符白名单）

        Returns:
            factor_wide 总行数（与 load_factor_to_wide 返回值语义一致）

        Raises:
            ValueError: 列缺失 / 空表 / 非法因子名
        """

    def _write_factor_from_temp(self, conn, temp_table: str, factor_name: str) -> int:
        """从已注册的长格式临时表执行双写（供高频路径复用）"""
```

`DailyLoader.load_factor_to_wide` 保持公共签名与返回值，内部改为调用 `_write_factor_from_temp`。

## 4. 关键决策

| 编号 | 决策 | 理由 |
|------|------|------|
| D1 | 双写逻辑集中在 `BaseLoader`，不新建类 | DailyLoader / CSMARLoader 均已继承 BaseLoader，避免重复 SQL |
| D2 | 以长表 `trade_date/stock_code/factor_value` 为统一中间格式 | 宽表入口可 UNPIVOT；CSMAR/barra 原生就是长表 |
| D3 | history + wide + legacy 写入放在单事务 | 任一失败回滚，避免 history 多出版本而宽表缺值 |
| D4 | legacy 写入仅当 `factor_data` 是 BASE TABLE | 用 `SchemaManager.object_type()` 判定；视图只读兼容 |
| D5 | 宽表列不存在时自动 `add_factor_column`；表不存在时用当前因子建表 | 支持迁移前先写入部分因子 |
| D6 | 迁移开始时对已存在的 `factor_wide` 补齐缺失列 | 解决 C6 的部分宽表问题，避免迁移插入缺列失败 |
| D7 | 删除 `_import_to_factor_table` | C2：死代码且目标表迁移后不可写 |
| D8 | barra 按 `factor_name` 分组后逐因子调用公共接口 | 一个 DataFrame 含多个因子，公共接口按因子写入 |
| D9 | `factor_data` 兼容视图改为动态 `UNPIVOT ... COLUMNS(* EXCLUDE (...))` 定义 | 实测该视图在 `ALTER TABLE ADD COLUMN` 后会自动包含新因子列；原静态 UNION ALL 视图会漏掉迁移后新增因子 |

## 5. 不变量

| 编号 | 不变量 |
|------|--------|
| I1 | 写入成功后，`factor_history` 行数 ≥ 输入行数；`factor_wide` 每个 `(trade_date, stock_code)` 保存最新值 |
| I2 | 迁移后，任何写入不直接 `INSERT INTO factor_data`（由视图状态判定跳过） |
| I3 | 迁移前，`factor_data` / `factor_history` / `factor_wide` 三处数据一致（同一批输入） |
| I4 | 任意一步失败时事务回滚，history 与 wide 不出现单边写入 |
| I5 | 因子名必须通过 `validate_identifier`；SQL 值必须参数绑定 |

## 6. 事务边界与异常

- 建表 / 加列在 `BEGIN` 之前完成（DDL 自动提交语义不影响事务内 DML）
- 事务内异常：`ROLLBACK` 后重新抛出，不吞异常
- 临时表由调用方注册，`write_factor_long` 负责注册/反注册 `_factor_write_tmp`

## 7. 迁移配合

`migrate_factor_data_to_wide()` 在 `create_factor_wide(all_factors)` 之后：

```python
existing = set(self.get_factor_columns('factor_wide'))
for name in all_factors:
    if name not in existing:
        self.add_factor_column('factor_wide', name)
```

其余迁移逻辑不变（ADR-0008 安全语义保持）。

### 7.1 兼容视图动态化

`_replace_factor_data_with_view()` 的视图定义改为：

```sql
CREATE VIEW factor_data AS
SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
FROM factor_wide
UNPIVOT (
    factor_value FOR factor_name IN (COLUMNS(* EXCLUDE (trade_date, stock_code, loaded_at)))
)
ORDER BY factor_name, trade_date, stock_code
```

实证：同结构视图在 `ALTER TABLE factor_wide ADD COLUMN` 后，下一次查询自动包含新列（DuckDB 1.5.3 探针）；显式 UNION ALL 视图则不会。
