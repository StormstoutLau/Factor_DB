# 数据源接入（P3）设计：CLI `collect` 与采集器可发现性

> 日期：2026-11-01
> 上游：[调研](data_source_ingestion_research.md)

---

## 1. 设计目标与非目标

**目标**
1. `factor-db collect --list` 能列出 4 个采集器。
2. `factor-db collect --source akshare_macro --indicator GDP_YEARLY` 能抓取并写入 `macro_data`，输出入库条数。
3. 采集器发现逻辑在「包模式（`Factor_DB.*`）」与「扁平模式（`osint`）」下都成立。
4. 测试不触网。

**非目标**
- 不做调度、增量水位、重试策略增强（沿用 `CollectorConfig` 默认值）。
- 不新增/删除采集器，不改表结构。

## 2. 数据流

```
cli.py collect
   │
   ├─ --list ──→ CollectorRegistry.auto_discover() ──→ 打印 id + summary
   │
   └─ --source ──→ OSINTPipeline.run([source], params)
                        │
                        ├─ registry.get(source) → BaseCollector 实例
                        ├─ collector.collect({indicator}) → DataFrame
                        └─ pipeline._store(df, source)
                              ├─ _detect_data_type → 'macro'
                              ├─ MacroLoader.load(df) → macro_data（UPSERT）
                              └─ MetadataManager.add_data_source('osint_<source>')
```

## 3. 接口契约

### 3.1 采集器发现（修复）

```python
# osint/registry.py
def auto_discover(self, package_path: Optional[str] = None) -> int:
    ...
    package = __package__ or 'osint'          # 包模式 = 'Factor_DB.osint'，扁平模式 = 'osint'
    importlib.import_module(f'.collectors.{name}', package=package)
```
- 返回注册数量；单模块导入失败仅记录 error，不影响其它模块。
- 依据：调研 §3.2 / §3.3（硬编码导致包模式下 0 个）。

### 3.2 指标函数解析

```python
# osint/collectors/akshare_collector.py
INDICATOR_MAP: Dict[str, str] = {...}                    # 主函数名（已修正）
FUNCTION_CANDIDATES: Dict[str, tuple[str, ...]] = {...}  # 主名 + 历史/备选名
def _resolve_function(self, indicator: str, primary: str) -> Optional[str]
```
- 解析顺序：`FUNCTION_CANDIDATES.get(indicator, (primary,))` → 首个 `hasattr(self._ak, name)` 的名字。
- 全部缺失 → `logger.error` + 返回 `None`，`extract_data` 返回 `None`，`collect` 产出空 DataFrame（不抛异常）。
- 依据：调研 §3.1（7/12 映射失效）。

修正后的主映射：

| 指标键 | 主函数 |
|--------|--------|
| `GDP_YEARLY` | `macro_china_gdp_yearly` |
| `GDP_QUARTERLY` | `macro_china_gdp` |
| `CPI_YEARLY` | `macro_china_cpi_yearly` |
| `CPI_MONTHLY` | `macro_china_cpi_monthly` |
| `PPI_YEARLY` | `macro_china_ppi_yearly` |
| `PPI_MONTHLY` | `macro_china_ppi` |
| `PMI_MANUFACTURING` | `macro_china_pmi` |
| `M2_YEARLY` | `macro_china_m2_yearly` |
| `M2_MONTHLY` | `macro_china_money_supply` |
| `INTEREST_RATE` | `macro_china_lpr` |
| `FX_RESERVES` | `macro_china_fx_gold` |
| `IMPORT_EXPORT` | `macro_china_hgjck` |

### 3.3 CLI

```
factor-db collect [--list] [--source ID] [--indicator NAME] [--db PATH]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--list` | `False` | 列出已注册采集器（`source_id` + `summary`）后退出 |
| `--source` | 无 | 采集器 ID；未给且非 `--list` 时为用法错误 |
| `--indicator` | 无 | 仅对 `akshare_macro` 生效，传给 `params[source]['indicator']` |
| `--db` | `factor_db.duckdb` | 数据库路径 |

行为与退出码：

| 场景 | 输出 | 退出码 |
|------|------|--------|
| `--list` | 每个采集器一行 | 0 |
| 未指定 `--source` 且非 `--list` | 错误提示 + 采集器列表 | 2 |
| `--source` 不存在 | 错误提示 + 采集器列表 | 2 |
| 运行成功（含 0 条） | `<source>: N 条` | 0 |
| 采集器返回 `-1` | `<source>: 失败` | 1 |

依据：调研 §1（CLI 无入口）、§4 结论 1。

## 4. 关键决策与依据

| 决策 | 依据 |
|------|------|
| 复用 `OSINTPipeline.run`，CLI 不复制存储逻辑 | 调研 §1：pipeline 已具备 `_store` 自动路由（macro/news/alternative） |
| auto_discover 用 `__package__` 动态推导 | 调研 §3.3：仓库同时存在包/扁平两种导入风格 |
| 指标解析用候选回退而非单名 | 调研 §3.1：AKShare 命名已漂移 7/12，未来仍可能变化 |
| 测试注入假 `akshare` 模块 | 调研 §4 结论 3：CI 不触网 |

## 5. 风险与权衡

| 风险 | 影响 | 缓解 |
|------|------|------|
| AKShare 接口需外网，本地/CI 不稳定 | 无法实跑 | 测试走假模块；生产实跑按需手动执行 |
| 候选名语义差异（如 `macro_china_gdp` 与 `gdp_quarterly`） | 指标口径可能变化 | 主名优先；文档标注候选含义，不做静默跨界映射以外的处理 |
| 采集 0 条与失败的区分 | 用户可能误判 | 退出码语义明确；`-1` 才算失败，0 条为「无数据」 |
| `MetadataManager` 连接未显式关闭 | 资源泄漏 | `cmd_collect` 结束时 `pipeline.metadata.conn.close()`（`MetadataManager.conn` 为 `DuckDBConnection` 单例） |
