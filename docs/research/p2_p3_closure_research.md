# P2/P3 遗留项闭环调研

> 日期：2026-10-30
> 来源：[Claude 评审核实分析](../reviews/2026-10-claude-audit-analysis.md) · 任务：补齐 P2（缓存真 LRU + 写后失效、单例生命周期）与 P3（LICENSE、`[project.scripts]`、空测试、`compact_table` 返回值）
> 说明：本任务是内部架构卫生清理，结论以官方文档 + 本机实测探针为主，不使用学术检索。

---

## 一、问题定义与范围

| 编号 | 问题 | 现状证据 |
|------|------|---------|
| P2-a | 缓存名义 LRU，实为 FIFO；`get()` 不刷新时间戳 | [cache.py](../../query/cache.py#L51-L62) |
| P2-b | 无写后失效，默认 TTL 300 秒内读到陈旧数据 | [cache.py](../../query/cache.py#L24-L26)、[base.py](../../query/base.py#L109-L140) |
| P2-c | 单例二次 `__init__` 忽略 `close_on_exit`；`__del__` 关闭共享连接 | [connection.py](../../core/connection.py#L40-L68)、[connection.py](../../core/connection.py#L148-L150) |
| P3-a | 无 LICENSE 文件（README 已声明 MIT） | 根目录无 `LICENSE*` |
| P3-b | `pyproject.toml` 缺 `[project.scripts]`，`factor-db` 命令装不出 | [pyproject.toml](../../pyproject.toml) |
| P3-c | `tests/test_pivot_optimization.py` 为 0 字节空文件 | 实测 `Length = 0` |
| P3-d | `compact_table()` 成功时固定 `return 1`，无法反映实际删除行数 | [schema.py](../../core/schema.py#L617-L626) |

范围外：`cli.py` 中 `factor_data` 的旧表引用、`factor_history` 冷缓存等数据层问题，不在本任务内。

---

## 二、候选方案对比

### 2.1 缓存淘汰（P2-a）

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| A. 现有 `dict` + 取最早写入时间 | FIFO | 改动最小 | 与"LRU"语义不符，热 key 被误淘汰 |
| **B. `OrderedDict` + `move_to_end`** | 命中时移动到队尾，淘汰队首 | Python 标准库零依赖，O(1)，官方示例即 LRU 实现 | 需改内部存储结构（私有字段） |
| C. `functools.lru_cache` | 函数级缓存 | 代码最少 | 无法缓存动态 SQL/参数、无法按条数控制、无法统一失效 |

Python 官方文档在 `OrderedDict` 示例中直接给出 LRU 实现（`move_to_end` + `popitem(last=False)`），因此选 **B**。[[1]](https://docs.python.org/3/library/collections.html#ordereddict-examples)

### 2.2 写后失效（P2-b）

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| A. 写操作后遍历所有缓存实例 `clear()` | 注册表 + 主动清理 | 精确 | 查询类可任意实例化（`cache_size` 参数），注册表需要弱引用，跨模块易漏 |
| **B. 全局数据版本号（epoch）** | 写入递增版本；缓存条目记录写入时版本，版本不符即视为 miss | 无需注册表；所有实例自动失效；读操作零开销 | 版本是进程内全局，跨进程不共享（本项目为单机单进程研究库，可接受） |
| C. 缩短 TTL | 仅靠时间 | 不改代码 | 仍是陈旧窗口，不解决正确性 |

缓存模式（cache-aside）要求写路径主动失效，否则读到的旧值无法靠 TTL 及时收敛。[[2]](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside) 采用 **B**。

失效钩子位置（实测代码后修正的关键事实）：loader 与 `SchemaManager` 的大量写操作经由 `with self.conn as conn:` 拿到**原生 duckdb 连接**后直接执行，不经过 `DuckDBConnection.execute()` 包装。因此单靠 `execute()` 识别写语句会漏掉主写入路径。最终取两个钩子：

1. `__exit__`：写连接（`read_only=False`）上下文退出后递增版本（覆盖 `with self.conn` 内的全部语句；读连接不触发）
2. `execute()`：写连接上首关键字为写语句时递增版本（覆盖不经 `with` 的直接写入）

二者均只影响进程内版本号，不做逐实例注册表管理。

### 2.3 单例生命周期（P2-c）

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| A. 二次 `__init__` 完全忽略新参数（现状） | 早退 | 无 | 新 `close_on_exit` 静默丢失 |
| **B. 参数默认 `None` = "不修改"，显式传值可更新** | 区分"未指定"与"显式指定" | 兼容现有调用（不传即保持首次配置） | 签名默认值由 `True` 变为 `None`，需同步文档 |
| C. 移除单例 | 每次新建连接 | 语义最干净 | DuckDB 同文件多写连接受限，改动面过大 |

`__del__` 方面，Python 数据模型说明 `__del__` 在对象被回收时调用，若对象仍被注册表强引用则不会触发；因此把析构改为"仅当实例已不在注册表中才关闭"，避免销毁共享连接。[[3]](https://docs.python.org/3/reference/datamodel.html#object.__del__)

### 2.4 `compact_table` 行数（P3-d）

DuckDB 1.5.3 实测（本机探针）：

| 探针 | 结果 |
|------|------|
| `DELETE ... RETURNING 1` | 支持，返回每删除行一行 |
| `SELECT changes()` | 不支持（`Catalog Error`） |

官方文档确认 `DELETE` 支持 `RETURNING` 子句。[[4]](https://duckdb.org/docs/stable/sql/statements/delete)

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. `DELETE ... RETURNING 1` 后统计返回行数 | 单次扫描，精确 | 删除量极大时结果集在内存中物化（每行一个元组） |
| **B. 先 `SELECT COUNT(*)`（同谓词）再 `DELETE`** | 内存恒定、精确 | 多一次谓词扫描（维护操作，可接受） |

考虑到 `factor_history` 为 52 亿行级别、compact 一次可能删除百万行以上，选 **B** 以避免结果集物化。

### 2.5 打包与整洁（P3-a/b/c）

- LICENSE：README（中英）均声明 MIT，直接落地 MIT 全文与 `pyproject` 的 `license` 元数据（PEP 621）。[[5]](https://peps.python.org/pep-0621/)
- `[project.scripts]`：`factor-db = "Factor_DB.cli:main"`，`cli.py` 已存在于包根目录，无需新建模块。[[6]](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/#declaring-project-scripts)
- 空测试：目标功能（UNPIVOT 相关）已由 `test_wide_query.py` / `test_wide_loader.py` 覆盖，直接删除空文件，不新增重复测试。

---

## 三、关键结论

1. 缓存改 `OrderedDict` 真 LRU + 全局数据版本号失效；失效钩子挂在 `DuckDBConnection.execute()` 的写语句识别分支。
2. 单例的 `close_on_exit` 改为 `Optional[bool]`，显式传值可覆盖；`__del__` 仅关闭已脱离注册表的实例。
3. `compact_table` 采用"同谓词 `COUNT(*)` + `DELETE`"，返回真实删除行数。
4. LICENSE = MIT；补 `[project.scripts]`；删除空测试文件。
5. 以上均为进程内行为，不改变表结构、SQL 语义或数据文件。

---

## 参考文献

1. Python 官方文档，`collections.OrderedDict` 示例（LRU cache）— https://docs.python.org/3/library/collections.html#ordereddict-examples
2. Microsoft Azure Architecture Center，Cache-Aside Pattern — https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside
3. Python 官方文档，`object.__del__()` — https://docs.python.org/3/reference/datamodel.html#object.__del__
4. DuckDB 官方文档，`DELETE` 语句（含 `RETURNING` 子句）— https://duckdb.org/docs/stable/sql/statements/delete
5. PEP 621，Storing project metadata in pyproject.toml — https://peps.python.org/pep-0621/
6. Python Packaging User Guide，Writing your pyproject.toml — https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
