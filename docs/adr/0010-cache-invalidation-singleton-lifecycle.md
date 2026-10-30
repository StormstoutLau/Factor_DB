# ADR-0010: 查询缓存写后失效与连接单例生命周期

- **状态**: 已接受
- **日期**: 2026-10-30
- **相关**: 落地 [Claude 评审核实分析](../reviews/2026-10-claude-audit-analysis.md) MEDIUM 1/2 与 LOW 项

## 背景

- `QueryCache` 声明 LRU，实际按写入时间 FIFO 淘汰，且无写后失效：默认 TTL 300 秒内可能读到旧数据。
- `BaseQuery` 可按任意 `cache_size` 实例化缓存，逐实例注册清理不可靠。
- `DuckDBConnection` 单例二次初始化静默忽略 `close_on_exit`，`__del__` 会关闭仍被共享的连接。
- `compact_table` 成功时固定 `return 1`，无法反映实际删除量。

## 决策

1. **缓存淘汰**：内部改为 `OrderedDict`，命中 `move_to_end`，超限 `popitem(last=False)`（真 LRU）。
2. **写后失效**：新增 `utils/data_version.py` 进程内版本号；`QueryCache` 条目记录写入时版本，版本不符即视为 miss。
3. **失效钩子**：`DuckDBConnection.__exit__`（仅写连接）与 `execute()`（仅写连接 + 写语句首关键字）递增版本。原因是主写入路径经 `with self.conn` 使用原生连接，`execute()` 单独不能覆盖。
4. **单例生命周期**：`close_on_exit: Optional[bool] = None`，`None` 沿用已有配置、显式传值可更新；`__del__` 仅在实例已脱离 `_instances` 时关闭。
5. **`compact_table`**：同谓词先 `COUNT(*)` 再 `DELETE`，返回实际删除行数。

## 理由

- 全局版本号无需缓存实例注册表，所有查询对象自动失效（调研 §2.2）。
- 只在写连接递增，读连接与 CLI 只读查询不产生误失效。
- `COUNT + DELETE` 避免 `DELETE ... RETURNING` 在百万行级把结果集物化到内存（调研 §2.4）。

## 备选方案

- 逐实例 `clear()` + 弱引用注册表：查询类可任意实例化，易漏、实现复杂，未采纳。
- 仅缩短 TTL：仍是陈旧窗口，不解决正确性，未采纳。
- 移除单例：DuckDB 同文件多写连接受限，改动面过大，未采纳。

## 后果

- ✅ 数据写入后查询缓存立即失效，不再有 5 分钟陈旧窗口；淘汰语义与文档一致。
- ✅ `close_on_exit` 可显式调整；析构不再误关共享连接。
- ✅ `compact_table` 返回值可用于审计。
- ⚠️ 写连接上的只读操作（如 `table_exists`）也会递增版本，造成少量多余 miss（可接受）。
- ⚠️ 版本号仅进程内有效，多进程并发写不会互相失效（本项目为单机单进程研究库）。
- ⚠️ `close_on_exit` 默认值由 `True` 改为 `None`；实例属性经首次初始化后始终为 `bool`。
