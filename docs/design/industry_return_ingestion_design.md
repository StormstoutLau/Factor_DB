# 行业收益率入库（P4）设计：申万行业指数采集器

> 日期：2026-11-02
> 依据调研：[industry_return_data_source_research.md](../research/industry_return_data_source_research.md)（独立复审通过）
> 范围：新增 `sw_industry` 采集器 + 修复 `_detect_data_type` 路由缺陷，跑通「在线抓取行业指数 → 计算收益率 → 入库 alternative_data → CLI 采集」

---

## 1. 目标

1. 新增 `SWIndustryCollector`（source_id `sw_industry`）：抓取申万一级/二级/三级行业指数日线，计算日收益率，经 `OSINTPipeline` 入库 `alternative_data`。
2. 修复 `OSINTPipeline._detect_data_type` 的路由缺陷（见 §3.1），使带 `entity_id` 的数据正确路由到 `alternative`。
3. CLI 零改动：`factor-db collect --source sw_industry` 直接可用（auto_discover 自动发现第 5 个采集器）。
4. CI 测试注入假 AKShare 模块，不触网。

### 非目标

- 不做定时调度、增量水位管理（沿用 `ON CONFLICT DO NOTHING` 幂等重跑）。
- 不新建 `industry_returns` 独立表（复用 `alternative_data`，调研 §5）。
- 不支持全收益指数/分红调整（价格指数口径，调研 §5）。
- 不改 CLI 参数与 `alternative_data` 表结构。

---

## 2. 数据流

```
factor-db collect --source sw_industry
    ↓
OSINTPipeline.run(['sw_industry'])
    ↓
SWIndustryCollector.collect()          # TET 管道（osint/base.py）
    ├─ transform_query: {level, codes?, start_date?}
    ├─ extract_data:
    │    1) ak.sw_index_{first|second|third}_info() → [(行业代码.SI, 行业名称)]
    │    2) 逐个 ak.index_hist_sw(symbol=6位代码, period='day')   # 全量历史，无日期参数
    │    3) 每个指数: 收盘.pct_change() → 日收益率；首行 NaN 丢弃
    │    4) 逐个调用之间 sleep(config.rate_limit)，单指数失败记 warning 继续
    └─ transform_data → 标准列 DataFrame
    ↓
OSINTPipeline._store → _detect_data_type → 'alternative'（§3.1 修复后）
    ↓
AlternativeLoader.load → alternative_data（PK data_id + ON CONFLICT DO NOTHING，幂等）
```

---

## 3. 接口契约

### 3.1 `_detect_data_type` 路由修复（`osint/pipeline.py`）

**缺陷（含一个已存在的静默丢数）**：`macro_keywords = {'indicator_id', 'indicator', '指标', 'value_type', 'trade_date'}` 含 `trade_date`，而 `AlternativeLoader` 强制要求 `trade_date` + `value` 列（[alternative_loader.py#L117-L120](file:///f:/Coding/Factor_DB/loaders/alternative_loader.py#L117-L120)）。任何带 `trade_date` 的另类数据都会被误判为 macro。

实测 4 个采集器的输出列与当前路由：

| 采集器 | 关键列 | 当前路由 | 结果 |
|--------|--------|----------|------|
| `akshare_macro` | `trade_date` / `indicator_id` | macro | ✅ 正常 |
| `worldbank` | `trade_date` / `indicator_id` | macro | ✅ 正常 |
| `news_rss` | `publish_date` / `title` / `sentiment_score`（无 `trade_date`） | news | ✅ 正常 |
| `government` | `trade_date` / `entity_id` / `entity_type` / `data_subtype` | **macro（误判）** | ❌ `MacroLoader.validate` 以「缺少指标标识列」拒绝 → `_store` 返回 **0**，**政府政策采集一直静默丢数** |

复现（新库 `init_database` + 构造 government 输出行）：`_detect_data_type → 'macro'`，`_store → 0`，`alternative_data` 与 `macro_data` 均为 0 行。

**修复**：在 macro 判定**之前**增加另类数据判定：

```python
alternative_keywords = {'entity_id', 'entity_type', 'data_subtype'}
if cols & alternative_keywords:
    return 'alternative'
```

**行为变化（非回归）**：`akshare_macro` / `worldbank` / `news_rss` 路由不变；`government` 由 macro（0 条）修正为 alternative（正常入库），属于缺陷修复。专项测试锁定 3 条不变路由 + government 修正路由 + 1 条新路由。

### 3.2 `SWIndustryCollector`（`osint/collectors/sw_industry_collector.py`）

| 项 | 契约 |
|----|------|
| `meta` | `name='sw_industry'`，`categories=['industry', 'alternative', 'china']`，`required_keys=[]` |
| `setup()` | `import akshare as ak; self._ak = ak`（与 `akshare_macro` 同款注入点，测试直接替换 `collector._ak`）；ImportError → `_error_state=True` |
| `transform_query(params)` | 输出 `{level, codes, start_date}`；`level` 取 `params.get('level')`，缺省回退读 `params.get('indicator')`（兼容 CLI `--indicator second` 别名），默认 `'first'`；非法值回退 `'first'` 并记 warning |
| `extract_data(query)` | `INFO_FN = {...}`；行业代码剥 `.SI` 后缀（实测带后缀 `KeyError`）；若 `query['codes']` 非空则过滤；逐指数拉全量日线，调用间 `time.sleep(config.rate_limit)`（测试传 `rate_limit=0`）；单指数异常记 warning 继续，**全部失败**记 error 返回 `None`（→ `transform_data` 空表 → `collect()` 空表） |
| `transform_data(query, raw)` | 输出列：`trade_date(date) / data_type='industry_return' / data_subtype=level / entity_type='industry' / entity_id(6位代码) / entity_name / value(日收益率) / data_quality=100 / source_id='sw_industry'`；`收盘.pct_change()` 按日期升序计算，首行 NaN 丢弃，`dropna(subset=['value'])`；`start_date` 在收益率计算**之后**过滤（保证首日有前收盘） |
| `produced_events()` | `[OSINTEventType.ALTERNATIVE_DATA]` |

### 3.3 存储契约（无代码改动，核对现有行为）

- `AlternativeLoader._standardize_columns` 自动生成 `data_id = f"{data_type}_{data_subtype}_{entity_id}_{trade_date}"`，主键冲突 `ON CONFLICT DO NOTHING` → **重跑幂等**（返回行数仍按插入尝试计，`_store` 只记日志不影响 CLI 输出）。
- `value` 为价格指数日收益率（`close_t/close_{t-1}-1`），不含分红。
- 预计首跑量：一级 31 × 6481 ≈ **20.1 万行**；二级 131 × ~6481 ≈ 85 万行；三级 335 × ~6480 ≈ 217 万行（默认仅一级）。

### 3.4 CLI（零改动）

`collect --source sw_industry` 经 auto_discover 直接可用；`--indicator` 可作为级别别名（`first/second/third`）传入 `transform_query`；退出码沿用 0/1/2。

---

## 4. 决策依据

| 决策 | 选择 | 理由（调研证据） | 放弃的方案 |
|------|------|------------------|-----------|
| 数据源 | 申万 `index_hist_sw` | 免费、无 token、1999-12-30 起、一/二/三级全覆盖，本机实测 6482 行/指数 ≈6 s | 东财板块（本机整体不可达）、Tushare `sw_daily`（需 5000 积分）、Baostock（未实测）、新浪中证（无申万口径，仅交叉校验） |
| 存储 | `alternative_data` 长表 | 表结构已具备 `entity_type/entity_id/entity_name/value/source_id`；loader 幂等；避免新表迁移 | 独立 `industry_returns` 表（需 schema 迁移，收益仅查询直观） |
| 收益率 | `pct_change` 自算 | 接口无涨跌幅列（实测列仅 `代码/日期/收盘/开盘/最高/最低/成交量/成交额`） | `index_analysis_daily_sw`（1.18.64 实测 `KeyError: 发布日期`） |
| 路由 | 改 `_detect_data_type` 加前置判定 | `trade_date` 关键词冲突是结构性缺陷，不改则数据写错表；改动 2 行且无既有采集器受影响 | 避开 `trade_date` 列名（loader 强制要求，不可行）、给 DataFrame 加假 `stock_code`（语义错误） |
| 失败语义 | 部分失败容忍、全失败返回空 | 与 `akshare_macro` 一致（空 → `run` 返回 0，CLI `0 条`/EXIT 0）；单指数抖动不污染整批 | 全失败抛异常置 error_state（`collect()` 会捕获为空表，行为等价，无增益） |
| 级别参数 | 默认 `first` + `--indicator` 别名 | 一级行业即满足敏感度回归（调研 §1）；不新增 CLI 参数 | CLI 加 `--level`（改 CLI 需同步改测试/文档，收益低） |

---

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 申万站点限流/封禁 | 全量拉取 31 次串行 ≈3.6 min（6 s/次 + rate_limit 1 s） | 串行 + `config.rate_limit` 节流；失败容忍语义保证部分入库 |
| `index_hist_sw` 无日期参数 | 每次全量拉取 6482 行 | 可接受（20 万行级）；`start_date` 仅做本地过滤 |
| 行业分类 2014/2021 版漂移 | 长周期回归口径不一致 | `data_subtype` 记录级别；抓取时间可由 `update_time` 审计；调研 §5 已注明 |
| TLS 中间证书（changelog 1.20.16） | 升级 AKShare 后申万接口行为变化 | 本机 1.18.64 实测可用；锁定说明写入实施文档 |
| 路由修复改变既有行为 | `government` 由静默丢数（0 条）改为正常入库 | 专项测试锁定 3 条不变路由 + `government` 新路由，并断言 `alternative_data` 实际入库 |
| 二/三级全量体积（85 万/217 万行） | 误操作导致大写入 | 默认仅一级；文档标注级别对应的量级 |

---

## 6. 测试计划（TDD 红灯先行，`tests/test_sw_industry.py`）

| # | 用例 | 断言 |
|---|------|------|
| 1 | `test_routing_entity_columns_to_alternative` | 含 `entity_id` 的 DataFrame → `_detect_data_type` 返回 `'alternative'` |
| 2 | `test_routing_regression` | 3 条不变路由（`indicator_id`→macro、`publish_date/title`→news、`analyst`→report）+ 纯 `trade_date+value`→macro；`government` 列→alternative（修正） |
| 3 | `test_transform_produces_standard_columns` | FakeAK（info 返回 `801010.SI`，hist 返回 3 日收盘）→ 输出含 9 个标准列、`entity_id='801010'`、收益率 = 手算 `pct_change` |
| 4 | `test_first_row_nan_dropped` | 每指数首行（无前收盘）不入结果 |
| 5 | `test_partial_failure_tolerated` | FakeAK 第二个指数抛异常 → 仍返回第一个指数的数据 |
| 6 | `test_all_failure_returns_empty` | 所有指数抛异常 → 空 DataFrame |
| 7 | `test_level_default_and_alias` | 无参数→`first`；`{'indicator': 'second'}`→`second`；非法值→回退 `first` |
| 8 | `test_start_date_filter_after_returns` | `start_date` 过滤后首行收益率仍有前收盘可算 |
| 9 | `test_auto_discover_finds_sw_industry` | `auto_discover` 注册数 ≥5 且含 `sw_industry` |
| 10 | `test_cli_collect_sw_industry` | mock Pipeline 下 `collect --source sw_industry` 输出条数、EXIT 0 |

全量回归目标：**335 + 10 = 345 passed**，`ruff` 零告警；真实网络实跑验收（一级 31 指数入库 + 只读复查 + 重跑幂等）写入实施文档。

---

## 7. 文件清单

| 文件 | 动作 |
|------|------|
| `osint/collectors/sw_industry_collector.py` | 新增（采集器） |
| `osint/collectors/__init__.py` | 导出 `SWIndustryCollector` 并加入 `__all__`（与现有 4 个采集器公共 API 一致） |
| `osint/pipeline.py` | 修改（`_detect_data_type` 前置 alternative 判定，约 2 行） |
| `tests/test_sw_industry.py` | 新增（10 用例） |
| `docs/implementation/industry_return_ingestion_implementation.md` | 新增（实施后） |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README*.md` / `docs/spec/REVIEW_CHECKLIST.md` | 实施后同步 |
