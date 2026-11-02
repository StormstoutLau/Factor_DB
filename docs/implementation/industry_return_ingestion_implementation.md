# 行业收益率入库（P4）实施规划

> 日期：2026-11-02
> 上游：[调研](../research/industry_return_data_source_research.md) / [设计](../design/industry_return_ingestion_design.md)
> 本文档为编码前规划；实现与实跑结果将在 §6 回填。

---

## 1. 分步实施计划（每步可独立验证）

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | 修复 `OSINTPipeline._detect_data_type`：在 macro 判定前增加 `entity_id/entity_type/data_subtype` → `alternative` | 单测：含 `entity_id` 返回 `alternative`；3 条既有路由不变；`government` 改判 alternative 并实际入库 |
| S2 | 新增 `SWIndustryCollector`（`setup/transform_query/extract_data/transform_data/produced_events`） | 单测：假 AKShare 注入，标准 9 列、收益率手算核对、首行 NaN 丢弃 |
| S3 | 采集器容错与级别/日期参数 | 单测：单指数失败继续、全失败返回空、`level` 默认/别名/非法回退、`start_date` 在收益计算后过滤 |
| S4 | 导出与可发现性 | 单测：`auto_discover` 注册 ≥5 且含 `sw_industry` |
| S5 | CLI 端到端（mock Pipeline） | 单测：`collect --source sw_industry` 输出条数、EXIT 0 |

实施顺序遵循 TDD：先写 `tests/test_sw_industry.py` 全部用例 → 运行确认红 → 实现 S1/S2/S3/S4 → 定向绿 → 全量回归。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | 不新增依赖。`akshare>=1.14.0` 已在 `pyproject.toml`；本地实测 1.18.64（申万接口可用），官方文档 1.20.22。Python `>=3.9`（`from __future__ import annotations` 已用于 `osint` 包） |
| **依赖** | 无新增/移除；CI 通过 `pip install -e ".[dev]"` 已安装 akshare，测试仍注入假模块，不触网 |
| **兼容** | `_detect_data_type` 仅新增前置判定。实测既有 4 个采集器：`akshare_macro` / `worldbank`（含 `indicator_id`）→ macro 不变；`news_rss`（`publish_date`/`title`，无 `trade_date`）→ news 不变；`government`（含 `entity_id`/`entity_type`/`data_subtype` 且带 `trade_date`）当前被误判 macro、`MacroLoader.validate` 以「缺少指标标识列」拒绝、`_store` 返回 0 → **修复后改走 alternative 正常入库（缺陷修复，非回归）**。`alternative_data` 表结构与 loader 不改；CLI 参数不改（子命令数保持 12） |
| **接口** | `SWIndustryCollector(BaseCollector)`，`meta['name']='sw_industry'`；`collect(params)` 由基类提供；`transform_query(params) -> Dict[str, Any]`；`extract_data(query) -> Optional[pd.DataFrame]`；`transform_data(query, raw_data) -> pd.DataFrame` |
| **签名** | `transform_query` / `extract_data` 默认参数不引入可变默认值；`level` 走 `params.get('level')` → `params.get('indicator')` → `'first'` 三级回退；`codes: Optional[List[str]] = None`、`start_date: Optional[str] = None` 均为 `None` 默认 |
| **低效操作** | 不使用 pandas `melt()`；不做逐行 INSERT（`AlternativeLoader` 用 `register` + `INSERT ... SELECT` 批量写入）；无全表扫描。唯一耗时项是 31 次串行网络请求（I/O 密集），按 `config.rate_limit` 节流 |
| **内存** | 默认一级：31 × 6481 ≈ **20.1 万行 × 9 列**，估算 <100 MB；二级约 85 万行；三级约 217 万行（估算数百 MB），仍远低于 32GB。构建时按指数逐个 append 后 `concat`，峰值 = 全部结果 + 单指数临时表 |
| **数据一致性** | `alternative_data` 无 PIT `loaded_at`，`update_time` 由 loader 写 `CURRENT_TIMESTAMP`；主键 `data_id` + `ON CONFLICT DO NOTHING` 保证重跑幂等，但**历史值被上游修订时不会覆盖**（已知限制，见 §5 风险）。`value` 为价格指数日收益率，首行无前收盘时丢弃，避免 NaN 入库 |
| **失败语义** | 单指数异常记 `warning` 继续；全部失败返回 `None` → `collect()` 产出空表 → `pipeline.run` 返回 0 → CLI `0 条` / EXIT 0（与 `akshare_macro` 一致）；`setup()` ImportError 置 `_error_state` |
| **限流** | 每指数 `time.sleep(self.config.rate_limit)`；`OSINTPipeline` 默认 `CollectorConfig.rate_limit=1.0` → 31 指数约 3.6 分钟；测试显式传 `rate_limit=0` |

---

## 3. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `osint/collectors/sw_industry_collector.py` | **新增**：`SWIndustryCollector` |
| `osint/collectors/__init__.py` | 导出 `SWIndustryCollector` 并加入 `__all__`（与现有 4 个采集器保持公共 API 一致；`auto_discover` 不依赖此导出） |
| `osint/pipeline.py` | `_detect_data_type` 增加 alternative 前置判定（约 2 行） |
| `tests/test_sw_industry.py` | **新增**：10 个 TDD 用例 |
| `docs/implementation/industry_return_ingestion_implementation.md` | 本文件（§6 实施后回填） |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` / `docs/spec/REVIEW_CHECKLIST.md` | 实施后同步（测试基线 335→345、采集器 4→5） |

---

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `osint/pipeline.py#_detect_data_type` | 先判 `macro_keywords`（含 `trade_date`），带 `trade_date` 的 DataFrame 一律进 macro | 先判 `alternative_keywords = {'entity_id','entity_type','data_subtype'}` → `'alternative'`；其余判定顺序不变 |
| `government` 采集器路由 | macro → `MacroLoader.validate` 拒绝（「缺少指标标识列」）→ `_store` 返回 0，静默丢数 | alternative → 正常入库（行为变化，缺陷修复） |
| 采集器注册 | 4 个：`akshare_macro/worldbank/news_rss/government` | 5 个：新增 `sw_industry` |
| `osint/collectors/__init__.py` | `__all__` 4 项 | `__all__` 5 项 |
| CLI | `collect --source ...`（12 子命令） | 不变；`--indicator` 对 `sw_industry` 解释为级别（`first/second/third`） |

**误路由影响（Before 状态下，已实测复现）**：行业收益与 government 数据含 `trade_date` 但无 `indicator_id` → 被判为 macro → `MacroLoader.validate` 检查指标列，日志「缺少指标标识列」并返回 `False`（`loaders/macro_loader.py#L228-L233`）→ `load()` 返回 0 → `pipeline._store` 记 0 条。**数据静默丢失，CLI 仍显示 `0 条` EXIT 0**。新库实测：`_detect_data_type → 'macro'`、`_store → 0`、`alternative_data`/`macro_data` 均 0 行。这是必须修 S1 的原因。

---

## 5. 测试计划（TDD）

测试文件 `tests/test_sw_industry.py`，假 AKShare 模块 + 纯内存，不触网。

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_routing_entity_columns_to_alternative` | 现有逻辑返回 `macro`（`AssertionError`） | 含 `entity_id` → `'alternative'` |
| 2 | `test_routing_regression` | 修复后若顺序写反会破坏既有路由 | `indicator_id`→macro、`publish_date/title`→news、`analyst`→report、纯 `trade_date+value`→macro 不变；`government` 列→alternative（修正） |
| 3 | `test_transform_produces_standard_columns` | `AttributeError: SWIndustryCollector` | 9 列齐全；`entity_id='801010'`；收益率 = `close.pct_change()` 手算值 |
| 4 | `test_first_row_nan_dropped` | 未实现 | 每指数首行（无前收盘）不入结果；总行数 = Σ(行数-1) |
| 5 | `test_partial_failure_tolerated` | 异常上抛或整批为空 | 第二个指数抛异常时仍返回第一个指数的数据 |
| 6 | `test_all_failure_returns_empty` | 异常上抛 | 返回空 DataFrame，不抛异常 |
| 7 | `test_level_default_and_alias` | 未实现 | 无参→`first`；`indicator='second'`→`second`；非法值→`first` |
| 8 | `test_start_date_filter_after_returns` | 先过滤后计算会导致首行 NaN/丢收益 | `start_date` 在收益计算后过滤，首日收益率保留 |
| 9 | `test_auto_discover_finds_sw_industry` | 注册 4 个 | 注册 ≥5 且 `sw_industry in ids` |
| 10 | `test_cli_collect_sw_industry` | argparse 无该 source 时退出码 2 | mock Pipeline 下输出 `sw_industry: 1 条`，EXIT 0 |

全量回归目标：**335 + 10 = 345 passed**，`ruff check .` 零告警。

---

## 6. 生产实跑与验收（实施后回填）

计划步骤（临时库，避免污染生产库）：

```
factor-db init --db %TEMP%\sw_test.duckdb
factor-db collect --db %TEMP%\sw_test.duckdb --list
factor-db collect --db %TEMP%\sw_test.duckdb --source sw_industry
factor-db collect --db %TEMP%\sw_test.duckdb --source sw_industry --indicator second   # 抽样少量 codes 时改程序调用
```

验收口径：
- `--list` 显示 5 个采集器（含 `sw_industry`）；
- 一级行业入库行数 ≈ 31 × 6481 ≈ 20.1 万（允许接口缺失个别指数，误差以实际为准）；
- 只读复查 `alternative_data`：`data_type='industry_return'`、`entity_type='industry'`、`entity_id` 覆盖 31 个一级行业、日期范围 1999-12-30 起；
- 重跑同一命令，行数不增长（`ON CONFLICT DO NOTHING` 幂等）；
- `EXIT 0`。

> 实跑结果与偏差说明：待实施阶段回填。

---

## 7. 文档一致性对齐（编码前检查）

| 检查项 | 结果 |
|--------|------|
| 调研结论 → 设计决策 → 实施步骤 | ✅ 申万 `index_hist_sw`（调研 §4）→ 采集器方案（设计 §4）→ S2 实现 |
| 术语统一 | ✅ `alternative_data` / `entity_id` / 级别 `first/second/third` / `data_type='industry_return'` 三文档一致 |
| 数据规模数字 | ✅ 31/131/335、6481~6482 行、1999-12-30 起、20.1 万行，调研 §3 与实施 §2 一致 |
| 接口签名 | ✅ 设计 §3.2 与实施 §2/§4 一致（`level` 回退链、`codes/start_date` 可选） |
| 文件路径真实存在 | ✅ `osint/pipeline.py`、`loaders/alternative_loader.py`、`loaders/macro_loader.py`、`osint/collectors/__init__.py`、`tests/` 均已核对 |
