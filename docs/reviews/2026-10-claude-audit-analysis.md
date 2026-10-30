# Claude 代码评审核实分析

> 日期：2026-10
> 对象：外部评审（CRITICAL 1 / HIGH 3 / MEDIUM 2 / LOW 1 + 改进建议）
> 方法：逐条对照源码核实，不采信未验证论断
> 结论：**7 条中 6 条完全成立，1 条含事实错误；另有 4 个同类问题评审遗漏**

---

## 一、逐条核实结果

### CRITICAL — 迁移失败被吞、后续无条件 DROP 源表：✅ 成立，且比评审描述更严重

证据（行号因后续插入 `_configure_migration` 有约 17 行位移，评审引用的 :893/:981 现为 :910/:996，实质不变）：

| 环节 | 位置 |
|------|------|
| 异常吞掉、返回 0 | `core/schema.py#L974-L976` |
| 调用方不检查返回值、无条件进入 Step 5 | `core/schema.py#L908-L910` |
| 先 DROP 再 CREATE VIEW | `core/schema.py#L995-L1010` |
| 宽表 upsert 与历史插入为两条自动提交语句，无显式事务 | `core/schema.py#L935-L969` |

评审未覆盖的三层加重：
1. **DROP 先于 CREATE VIEW**：视图创建失败（241 列 UNION ALL）时表已删、视图未建，源数据归零
2. **`factor_history` 无主键、无幂等保护**：迁移重试会重复追加同一版本
3. **无源-目标行数对账**：DROP 前不比较源表与宽表/历史表的因子行数

同类问题：`_configure_migration` 吞掉 SET 失败仅告警（`core/schema.py#L852-L857`），内存安全设计可能运行时未生效。

**前提**：迁移从未在生产库执行（`factor_data` 仍为 16.8 亿行实体表），属"执行前必须修复"的阻断项，尚未造成实际损失。

### HIGH 1 — SQL f-string 拼接：✅ 成立（严重度重校准为 P1 健壮性）→ 已修复

| 位置 | 内容 |
|------|------|
| `query/base.py#L64-L85` | 日期、stock_codes 直接塞引号 |
| `query/factor_query.py#L151/L158/L203/L210/L267/L277` | factor_name 拼接 |
| `core/schema.py#L943/L966/L1002` | 迁移 SQL 拼接 |
| `loaders/daily_loader.py#L463/L469` | 写入时拼接 factor_name |

`connection.execute` 已支持 parameters（`core/connection.py#L109-L122`）但几乎未使用。
**校准**：本地单机研究库无 Web 攻击面，"注入"威胁被高估；真正现实故障是因子名含引号导致语法错误（CSMAR 文件名来源）。建议按"值参数绑定 + 标识符白名单转义"作为 P1 健壮性修复，无需单开安全审计。

### HIGH 2 — README 示例与 `load_factor_to_wide` 输入格式相反：✅ 成立 → 已修复

- README 传长表：`README.md#L100-L107`
- 函数要求宽矩阵（index=日期，columns=股票代码）：`loaders/daily_loader.py#L403-L405`
- 实现将 `stock_code`/`factor_value` 当股票代码 normalize：`loaders/daily_loader.py#L426-L437`
- 失败被吞、返回 0：`loaders/daily_loader.py#L485-L489`

评审推演正确：产生两个同名 `trade_date` 列 + UNPIVOT 失败，且用户看到返回 0 误以为成功。修复需改 README + 入口格式校验抛异常。

### HIGH 3 — 硬编码路径与内存：✅ 成立，且评审有遗漏 → 已修复

- `loaders/daily_loader.py#L49-L54`：`20GB` + `F:\Coding\Factor_DB\...tmp`
- `loaders/daily_loader.py#L440-L441`：二次写死 20GB
- `scripts/import_phase1_ashare.py#L33`：`E:\Ashare_data`

**评审遗漏**：`scripts/import_phase1_ashare.py#L35` 仍指向 `财务指标因子`，该目录已重命名为 `financial_factors` —— 该脚本当前直接失效，与操作系统无关。

有利条件：`utils/config.py` 已支持 `FACTOR_DB_PATH` 环境变量（`utils/config.py#L82-L83`），接入成本低。

### MEDIUM 1 — 单例与 close_on_exit 冲突：✅ 成立（实际影响有缓解）

- 二次 `__init__` 早退忽略新 `close_on_exit`：`core/connection.py#L58-L60`
- `close()` 删除单例缓存：`core/connection.py#L103-L108`
- SchemaManager 全面 `with self.conn`，退出即关连接
- `__del__` 关闭共享连接：`core/connection.py#L147-L149`

**缓解**：DailyLoader 在每个写入操作内重新 SET 内存/线程（`loaders/daily_loader.py#L440-L441`），但 `temp_directory` 未重新应用，属残留风险。

### MEDIUM 2 — 缓存名义 LRU 实为 FIFO、写入不失效：✅ 完全成立

- 淘汰取写入时间最早者：`query/cache.py#L59-L62`
- `get()` 不刷新时间戳：`query/cache.py#L31-L49`
- README 宣称 LRU：`README.md#L43`
- 无写后失效钩子，默认 TTL 300 秒

评审未说：因子研究数据每日更新，5 分钟陈旧窗口足以制造一次错误 IC 分析；失效机制比 LRU 实现更优先。

### LOW — 杂项：⚠️ 部分成立（1 处事实错误）

| 子项 | 判定 | 证据 |
|------|------|------|
| 空测试文件 | ✅ | `tests/test_pivot_optimization.py` 0 字节 |
| 无 LICENSE | ✅ | Glob `LICENSE*` 无结果 |
| `compact_table` 固定返回 1 | ✅ | `core/schema.py#L596-L600` |
| **无 pyproject.toml** | **❌ 错误** | `pyproject.toml` 存在（name=factor-db, version=1.0.0） |
| `factor-db` 命令装不出来 | ✅ | 真实原因：pyproject 缺 `[project.scripts]` |

---

## 二、评审遗漏的同类问题

1. **失效路径**：`scripts/import_phase1_ashare.py#L35` 指向已重命名的 `财务指标因子`
2. **历史表非幂等**：`factor_history` 无主键，迁移重试重复追加版本
3. **DROP 先于 CREATE VIEW**：视图创建失败时表/视图双失（CRITICAL 加重项）
4. **配置静默降级**：`_configure_migration` 吞 SET 失败，内存安全设计可能未生效

## 三、严重度重校准与修复顺序

| 优先级 | 修复项 | 理由 |
|--------|--------|------|
| **P0（阻断迁移执行）** | 迁移事务化 / 失败中止 / 行数对账 / DROP 改 RENAME 备份 / 历史表幂等 | 唯一可造成不可逆数据损失的项 |
| **P1** | 参数绑定 + 标识符白名单；README 修正 + 格式校验；配置收进 config/env；修复失效路径 | 正确性与可移植性 |
| **P2** | 单例生命周期重构；缓存改真 LRU + 写后失效 | 架构卫生 |
| **P3** | LICENSE、`[project.scripts]`、清理空测试、`compact_table` 返回值 | 打包与整洁 |

## 四、评审质量评价

- 精确性：7 条中 6 条成立；1 处事实错误（pyproject 存在）；行号有约 17 行漂移但实质无误
- 完整性：遗漏 4 个同类风险
- 严重度判断：整体合理；仅 SQL 注入一项对本地单机库偏高
- **结论：评审可信度高，可直接作为 P0/P1 修复依据**

## 五、修复落地

| 项 | 状态 | 落地内容 | 验证 |
|----|------|---------|------|
| CRITICAL（迁移安全） | ✅ 已提交 `30f8929` | `MigrationError` + fail-fast + 单因子事务 + 行数对账 + RENAME 备份 | ADR-0008；245 passed |
| HIGH 1（SQL 拼接） | ✅ 已修复 | `utils/sql_utils.py` + 7 个查询模块命名参数 + schema 迁移/DDL 白名单转义 | `test_sql_safety.py`；266 passed |
| HIGH 2（README / 宽表入口） | ✅ 已修复 | README 宽矩阵示例；长表/空表/非法名 → `ValueError` | `test_wide_loader.py` 3 项 |
| HIGH 3（硬编码） | ✅ 已修复 | `LoaderConfig` env 化；脚本 `ASHARE_DATA_PATH` + `financial_factors` + `rglob`；辅助脚本路径全部 env 化 | `TestLoaderConfigEnv`；101 因子文件可发现 |
| MEDIUM 1（P2 单例生命周期） | ✅ 已修复 `2026-10-30` | `close_on_exit: Optional[bool]`（`None` 沿用已有配置、显式传值可更新）；`__del__` 仅关闭已脱离 `_instances` 的实例 | `test_connection.py` 3 项 |
| MEDIUM 2（P2 缓存） | ✅ 已修复 `2026-10-30` | `OrderedDict` 真 LRU（命中 `move_to_end` / 淘汰 `popitem(last=False)`）+ `utils/data_version.py` 写后失效（`__exit__` 与写语句 `execute` 双钩子，版本不符即 miss） | `test_cache.py` 4 项 |
| LOW（P3 打包与整洁） | ✅ 已修复 `2026-10-30` | MIT `LICENSE`；`pyproject` 补 `license` 与 `[project.scripts] factor-db`；删除 0 字节 `test_pivot_optimization.py`；`compact_table` 返回实际删除行数 | `test_packaging.py` 3 项；`test_pit` 精确断言 |

详见 [调研](../research/high_fixes_research.md) / [设计](../design/high_fixes_design.md) / [实施](../implementation/high_fixes_implementation.md)；
P2/P3 见 [调研](../research/p2_p3_closure_research.md) / [设计](../design/p2_p3_closure_design.md) / [实施](../implementation/p2_p3_closure_implementation.md) / [ADR-0010](../adr/0010-cache-invalidation-singleton-lifecycle.md)。
