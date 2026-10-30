# P2/P3 遗留项闭环设计

> 日期：2026-10-30
> 依据：[调研文档](../research/p2_p3_closure_research.md) · 来源：[Claude 评审核实分析](../reviews/2026-10-claude-audit-analysis.md) §五 P2/P3
> 关联：ADR-0010

---

## 一、设计目标与非目标

### 目标
1. 缓存实现与文档声明的 LRU 语义一致，且在数据写入后立即失效（不再依赖 TTL 收敛）。
2. 单例连接的 `close_on_exit` 可被显式更新；析构不再关闭仍被注册表共享的连接。
3. `compact_table()` 返回真实删除行数。
4. 补齐打包与整洁项：MIT LICENSE、`[project.scripts]`、删除空测试文件。

### 非目标
- 不做跨进程缓存失效（单机单进程研究库，见调研 §2.2）。
- 不引入按表/按因子粒度的失效（全局版本即可满足正确性）。
- 不重构单例体系、不改 `query` 层调用方式。
- 不修复 `cli.py` 中 `factor_data` 旧表引用等数据层遗留。

---

## 二、数据流

```
写路径（loader / SchemaManager / 直接 execute）
   │  with self.conn as conn:  ← 原生 duckdb 连接
   │        INSERT / UPDATE / DELETE / DDL
   ▼
DuckDBConnection.__exit__（read_only=False）
   └─ bump_version()  →  全局版本 N+1

读路径（BaseQuery._execute_query）
   cache.get(key)
     ├─ 条目版本 != current_version() → 视为 miss（删除条目）
     └─ 命中 → move_to_end(key)（真 LRU）→ 返回副本
```

版本号存放于独立模块 `utils/data_version.py`，避免 `core` 与 `query` 相互依赖，也避免测试中 `query.cache` / `Factor_DB.query.cache` 双导入导致状态分裂（两处均导入规范名 `Factor_DB.utils.data_version`）。

---

## 三、接口契约

### 3.1 新增 `utils/data_version.py`

| 函数 | 签名 | 语义 |
|------|------|------|
| `current_version` | `() -> int` | 当前进程内数据版本（线程安全） |
| `bump_version` | `() -> int` | 版本 +1 并返回新值 |

### 3.2 `query/cache.py`（内部结构变更，公共接口不变）

```python
class QueryCache:
    def __init__(self, max_size: int = 1000, ttl: int = 300)
    def get(self, sql: str) -> Optional[pd.DataFrame]   # 命中刷新 LRU 位置；版本/超时即 miss
    def put(self, sql: str, df: pd.DataFrame) -> None   # 记录写入时版本
    def clear(self) -> None                             # 本实例清空
    def stats(self) -> dict
    def __len__(self) -> int
```

- 存储：`OrderedDict[str, tuple[float, int, pd.DataFrame]]`（写入时间、版本、数据）
- 淘汰：超过 `max_size` 时 `popitem(last=False)`

### 3.3 `core/connection.py`

| 项 | Before | After |
|----|--------|-------|
| `__init__` 签名 | `close_on_exit: bool = True` | `close_on_exit: Optional[bool] = None` |
| 二次初始化 | 完全早退，忽略新值 | 显式传值则更新 `close_on_exit`，`None` 保持原值 |
| `execute()` | 仅执行 SQL | 写连接 + 写语句首关键字 → `bump_version()` |
| `__exit__` | 可选关闭连接 | 写连接退出时 `bump_version()`，再按原逻辑关闭 |
| `__del__` | 无条件 `close()` | 仅当实例已不在 `_instances` 中时 `close()` |

### 3.4 `core/schema.py`

```python
def compact_table(self, table_name: str, keep_before: str) -> int
```
返回值语义：Before「成功固定 1」→ After「实际删除行数（同谓词先 COUNT 再 DELETE）」。

### 3.5 打包

```toml
[project]
license = {text = "MIT"}

[project.scripts]
factor-db = "Factor_DB.cli:main"
```

---

## 四、关键决策及依据

| 决策 | 依据 |
|------|------|
| `OrderedDict` 实现 LRU | 调研 §2.1；Python 官方示例 [[1]](../research/p2_p3_closure_research.md) |
| 全局版本号失效（而非实例注册表） | 调研 §2.2；`BaseQuery` 可任意 `cache_size` 实例化，注册表方案不可靠 |
| 钩子放在 `__exit__` + `execute()` | 调研 §2.2 实测：`with self.conn` 拿原生连接，`execute()` 单独钩子会漏主路径 |
| 只对写连接递增 | 读连接（`read_only=True`）不可能改变数据；避免 CLI/查询误失效 |
| `close_on_exit=None` 表示"沿用" | 兼容现有不传参调用；显式传值才覆盖（调研 §2.3） |
| `__del__` 仅在脱离注册表时关闭 | 避免 `del` 一个别名就关闭共享连接；引用 Python 数据模型 [[3]](../research/p2_p3_closure_research.md) |
| `compact_table` 用 COUNT + DELETE | 调研 §2.4：百万行级 `RETURNING` 会物化结果集；COUNT 方案内存恒定 |
| MIT LICENSE | README 中英均已声明 MIT；PEP 621 [[5]](../research/p2_p3_closure_research.md) |

---

## 五、风险与权衡

| 风险 | 影响 | 缓解 |
|------|------|------|
| 写连接上只读操作（如 `table_exists`）也会递增版本 | 极少数场景多一次 miss，无正确性损失 | 接受：宁可多失效，不可读到旧值 |
| 全局版本在进程内共享，import 路径分裂可能读到不同模块实例 | 失效可能不生效 | 两处均写规范名 `Factor_DB.utils.data_version`；测试覆盖双导入场景 |
| `close_on_exit` 默认值改为 `None` | 直接读取该属性的外部代码可能看到 `None` | 首次初始化即解析为 `True/False`，属性值始终为 `bool` |
| 移除 `__del__` 关闭行为后依赖析构释放文件句柄 | Windows 下临时库删除可能失败 | 保留"脱离注册表则关闭"分支；测试用显式 `close()` + `_instances.clear()` |
| COUNT 与 DELETE 之间并发写入 | 返回行数可能与实际删除略有差异 | 维护操作单进程执行；返回值为参考统计而非事务保证 |
