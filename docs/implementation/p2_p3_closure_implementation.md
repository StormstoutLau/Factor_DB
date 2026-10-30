# P2/P3 遗留项闭环实施计划

> 日期：2026-10-30
> 依据：[调研](../research/p2_p3_closure_research.md) / [设计](../design/p2_p3_closure_design.md)

---

## 一、分步实施计划（每步可独立验证）

| 步骤 | 内容 | 验证方式 |
|------|------|---------|
| S1 | 新增 `utils/data_version.py`（版本计数器） | 单元导入 + 递增探针 |
| S2 | 重写 `query/cache.py` 存储为 `OrderedDict` + 版本校验 + 命中刷新 | `tests/test_cache.py` 新增用例 |
| S3 | `core/connection.py`：写连接 `__exit__`/`execute` 递增版本 | `tests/test_cache.py::test_write_invalidates_cached_entry` |
| S4 | `core/connection.py`：`close_on_exit` 可更新、`__del__` 条件关闭 | `tests/test_connection.py` 新增用例 |
| S5 | `core/schema.py::compact_table` 返回真实删除行数 | `tests/test_pit.py` 断言改为精确值 |
| S6 | `LICENSE` + `pyproject.toml`（license / scripts） | `tests/test_packaging.py` |
| S7 | 删除 0 字节 `tests/test_pivot_optimization.py` | 目录检查 |
| S8 | 全量回归 | `py -3 -m pytest` |

---

## 二、工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 无新增依赖；`OrderedDict` 属标准库；Python 3.9+ 兼容（不使用 3.10+ 语法） |
| **依赖** | 不修改 `dependencies`；仅 `pyproject.toml` 增加元数据与脚本入口 |
| **兼容** | `QueryCache` 公共方法签名不变；`DuckDBConnection.__init__` 默认值变化但行为兼容；`compact_table` 返回值语义由"标志"变"行数"，唯一调用方 `cli.py#L126` 只判断 `> 0`，兼容 |
| **接口** | 见第四节 Before/After |
| **签名** | `close_on_exit: Optional[bool] = None`；无可变默认参数 |
| **低效操作** | 缓存命中 O(1)；失效仅整数递增；`compact_table` 多一次同谓词 COUNT 扫描（维护操作） |
| **内存** | 缓存条目结构增加一个 int；`COUNT + DELETE` 避免 `RETURNING` 百万行物化 |
| **数据一致性** | 不做跨进程失效；写连接退出即失效；读连接不失效 |

---

## 三、涉及修改的文件清单

| 文件 | 操作 |
|------|------|
| `utils/data_version.py` | 新增 |
| `query/cache.py` | 重写存储与失效逻辑 |
| `core/connection.py` | 版本钩子、单例生命周期 |
| `core/schema.py` | `compact_table` 返回值 |
| `pyproject.toml` | `license` + `[project.scripts]` |
| `LICENSE` | 新增（MIT） |
| `tests/test_pivot_optimization.py` | 删除（0 字节） |
| `tests/test_cache.py` | 新增 3 个用例 |
| `tests/test_connection.py` | 新增 3 个用例 |
| `tests/test_pit.py` | 精确断言删除行数 |
| `tests/test_packaging.py` | 新增 |

---

## 四、接口/签名变更清单（Before/After）

| 位置 | Before | After |
|------|--------|-------|
| `DuckDBConnection.__init__` | `close_on_exit: bool = True` | `close_on_exit: Optional[bool] = None`（首次解析为 bool） |
| `DuckDBConnection.__del__` | 无条件 `close()` | 仅当不在 `_instances` 时 `close()` |
| `QueryCache._cache` | `dict[str, tuple[float, DataFrame]]` | `OrderedDict[str, tuple[float, int, DataFrame]]` |
| `SchemaManager.compact_table` | 成功 `return 1` | `return` 实际删除行数 |
| `pyproject.toml` | 无 scripts/license | `factor-db = "Factor_DB.cli:main"`；`license = {text = "MIT"}` |

---

## 五、测试计划（TDD）

| 测试 | 覆盖 |
|------|------|
| `test_true_lru_on_access` | 访问后再插入，淘汰的是"最久未使用"而非"最早写入" |
| `test_write_invalidates_cached_entry` | 写连接执行 INSERT 后，已缓存结果立即 miss |
| `test_read_does_not_invalidate` | 只读查询不递增版本，缓存仍命中 |
| `test_close_on_exit_can_update` | 单例二次初始化显式传 `False` 生效 |
| `test_default_keeps_existing_close_on_exit` | 不传参不覆盖已有 `False` |
| `test_del_closes_only_detached_instance` | 共享实例被 `del` 别名后仍可用；脱离注册表的实例析构后文件可删除 |
| `test_compact_returns_actual_deleted_rows` | 精确 `== 2`（test_pit 数据集） |
| `test_license_file_exists` | 根目录 `LICENSE` 存在且含 MIT |
| `test_pyproject_declares_script_and_license` | TOML 解析出入口与 license |
| `test_entry_point_target_callable` | `Factor_DB.cli:main` 可导入且可调用 |
