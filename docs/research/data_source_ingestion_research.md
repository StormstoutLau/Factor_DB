# 数据源接入（P3）调研：宏观/舆情在线入库闭环

> 日期：2026-11-01
> 范围：把已有 OSINT 采集能力接入 CLI，实现「在线抓取 → 入库 → 状态查询」最小闭环
> 方法：本地环境实测（AKShare 1.18.64、Python 3.12.10、仓库提交 `a15ddd2`）+ AKShare 官方文档交叉核对

---

## 1. 问题定义与范围

### 现状（仓库事实）

| 组件 | 现状 | 证据 |
|------|------|------|
| 采集器 | 4 个：`akshare_macro` / `worldbank` / `news_rss` / `government` | `osint/collectors/`，`meta['name']` 已核对 |
| 管道 | `OSINTPipeline.run / run_all / get_status / _store` 已能把 DataFrame 路由进 `macro_data` / `news_sentiment` / `alternative_data` | `osint/pipeline.py#L50-L217` |
| 表结构 | `macro_data` / `macro_indicators` / `news_sentiment` / `report_sentiment` / `alternative_data` / `data_sources` 均已存在 | `core/schema.py#L134-L244` |
| CLI 入口 | **缺失**：11 个子命令中无采集入口 | `cli.py` |
| 自动发现 | **失效**：`CollectorRegistry.auto_discover()` 注册 0 个 | 见 §3.2 |

结论：**能力已存在，缺的是「入口 + 可发现性 + 函数名正确性」**，不需要新建采集器。

### 范围（本次）

1. 修复 `CollectorRegistry.auto_discover()` 的包路径硬编码，使 4 个采集器可被发现。
2. 修正 `AKShareMacroCollector.INDICATOR_MAP` 中已不存在的 AKShare 函数名，并增加候选名回退。
3. 新增 CLI `collect` 子命令：`--list` 列出采集器；`--source` 运行指定采集器；`--indicator` 传递指标参数。
4. 测试全部使用假 AKShare 模块，**不依赖外网**（CI 可跑）。

### 非目标

- 不做定时调度（cron/APScheduler），不做增量水位管理。
- 不新增采集器（World Bank / RSS / gov.cn 沿用现有实现）。
- 不改 `macro_data` / `news_sentiment` 表结构与 loader 写入逻辑。

---

## 2. 候选方案对比

| 方案 | 原理 | 优点 | 缺点 | 适用性 |
|------|------|------|------|--------|
| A. CLI 薄封装 + 修复现有缺陷（采用） | `factor-db collect` 直接调用 `OSINTPipeline.run` | 复用 100% 现有逻辑；改动面 < 100 行；可离线测试 | 无调度、无增量水位 | ✅ 本次 |
| B. 新建采集服务/调度层 | 独立进程 + cron + 任务表 | 可定时、可重试 | 需新增依赖与运维面，超出 P3 最小闭环 | ❌ 后续 |
| C. 直接用 AKShare 脚本替代 collector | 脚本内直接 `ak.xxx()` + `to_sql` | 实现最短 | 破坏已有 4 采集器与 pipeline 抽象，重复存储逻辑 | ❌ 倒退 |

---

## 3. 现状核实（事实 vs 推断）

### 3.1 AKShare 函数名漂移（事实）

`AKShareMacroCollector.INDICATOR_MAP` 声明 12 个指标 → 函数映射。对本地 **AKShare 1.18.64** 逐一 `hasattr` 实测：

| 指标键 | 代码中的函数 | 本地是否存在 |
|--------|--------------|--------------|
| `GDP_YEARLY` | `macro_china_gdp_yearly` | ✅ |
| `GDP_QUARTERLY` | `macro_china_gdp_quarterly` | ❌ |
| `CPI_YEARLY` | `macro_china_cpi_yearly` | ✅ |
| `CPI_MONTHLY` | `macro_china_cpi_monthly` | ✅ |
| `PPI_YEARLY` | `macro_china_ppi_yearly` | ✅ |
| `PPI_MONTHLY` | `macro_china_ppi_monthly` | ❌ |
| `PMI_MANUFACTURING` | `macro_china_pmi` | ✅ |
| `M2_YEARLY` | `macro_china_money_supply_yearly` | ❌ |
| `M2_MONTHLY` | `macro_china_money_supply_monthly` | ❌ |
| `INTEREST_RATE` | `macro_china_interest_rate` | ❌ |
| `FX_RESERVES` | `macro_china_fx_reserves` | ❌ |
| `IMPORT_EXPORT` | `macro_china_import_export` | ❌ |

**7/12 不存在**。`dir(akshare)` 实测 85 个 `macro_china_*` 函数，其中可替代项包括：

- `macro_china_gdp`（替代 `gdp_quarterly`）
- `macro_china_ppi`（替代 `ppi_monthly`）
- `macro_china_m2_yearly` / `macro_china_money_supply`（替代 money supply 两个旧名）
- `macro_china_lpr`（替代 `interest_rate`）
- `macro_china_fx_gold` / `macro_china_fx_reserves_yearly`（替代 `fx_reserves`）
- `macro_china_hgjck`（替代 `import_export`）

官方文档（AKShare 1.20.21，最后更新 2026-10-31）中 `macro_china_lpr`、`macro_china_gdp_yearly`、`macro_china_ppi_yearly`、`macro_china_exports_yoy` 均作为独立接口列出；1.4.92 文档使用 `macro_china_money_supply`（无 `_monthly` 后缀），可与本地实测互证命名演进。

> 注：`macro_china_pmi` 在本地存在，但官方文档另有 `macro_china_pmi_yearly`；本次保留原映射，不猜测语义差异。

### 3.2 自动发现失效（事实，可复现）

```
F:\Coding 下执行:
>>> from Factor_DB.osint.registry import CollectorRegistry
>>> CollectorRegistry().auto_discover()
加载收集器模块失败 akshare_collector: No module named 'osint'
...
0
```

根因：`osint/registry.py#L140` 硬编码 `package='osint'`。以 `Factor_DB.*` 包路径导入时，顶层名 `osint` 不存在；仅当工作目录为 `F:\Coding\Factor_DB`（`osint/` 作为顶层包）时偶然可用——这也是现有测试能通过的原因（测试用 `from osint.base import ...`）。

### 3.3 双重导入风格（事实）

仓库同时支持两种导入：

- 包模式：`from Factor_DB.osint import ...`（`factor-db` 入口、`F:\Coding` 工作目录）
- 扁平模式：`from osint import ...`（`F:\Coding\Factor_DB` 工作目录，测试使用）

修复必须两种模式都可用 → 使用 `__package__` 动态推导，而非写死任一方。

---

## 4. 关键结论与推荐方向

1. **采用方案 A**：CLI `collect` 薄封装 + 修复 auto_discover + 修正指标映射。
2. 指标函数解析采用「候选列表 + 运行时存在性检查」：既修正当前漂移，也避免 AKShare 再次改名时直接失效（失败时返回空并记录 error，不抛异常）。
3. 所有网络调用在测试中被假模块替换；生产实跑仅在用户本地按需执行，不作为 CI 验收条件。

---

## 5. 参考文献

- [AKShare 宏观数据（1.20.21，2026-10-31 更新）](https://akshare.akfamily.xyz/data/macro/macro.html)
- [AKShare 中国宏观数据接口页](https://akshare.akfamily.xyz/data/macro/macro_china.html)（含 `macro_china_lpr`、`macro_china_gdp_yearly`、`macro_china_ppi_yearly`、`macro_china_exports_yoy`）
- [AKShare 1.4.92 快速入门（含 `macro_china_money_supply` 命名）](https://akshare-hh.readthedocs.io/en/latest/tutorial.html)
- [AKShare 更新日志（1.20.x）](https://akshare.akfamily.xyz/changelog.html)
- 本地实测：AKShare 1.18.64 / Python 3.12.10 / Windows 11；`dir(akshare)` 共 85 个 `macro_china_*` 符号（2026-11-01）
