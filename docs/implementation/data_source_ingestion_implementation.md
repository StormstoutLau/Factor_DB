# 数据源接入（P3）实施规划

> 日期：2026-11-01
> 上游：[调研](../research/data_source_ingestion_research.md) / [设计](../design/data_source_ingestion_design.md)

---

## 1. 分步实施计划（每步可独立验证）

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | 修复 `CollectorRegistry.auto_discover` 包路径 | 单测：包模式下发现 ≥ 4 个采集器 |
| S2 | 修正 `AKShareMacroCollector` 映射 + 候选回退 | 单测：假模块只暴露备选名时仍可解析；全部缺失返回空 |
| S3 | CLI `collect`（`--list` / `--source` / `--indicator`） | 单测：列表、缺参退出码 2、未知 source 退出码 2、运行输出条数 |
| S4 | 生产实跑（手动、可选） | `factor-db collect --source akshare_macro --indicator CPI_MONTHLY`（需外网） |

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| 版本 | 不新增依赖；`akshare>=1.14.0` 已在 `pyproject.toml`。本地实测 1.18.64，官方最新文档 1.20.21 |
| 依赖 | 无新增；测试注入假模块，不依赖 `akshare` 已安装（`setUp` 中 `hasattr` 判断） |
| 兼容 | `auto_discover()` 签名不变（仅内部 package 推导）；`INDICATOR_MAP` 键不变，仅值修正；旧指标键仍可用 |
| 接口 | `cmd_collect(args) -> Optional[int]`；`OSINTPipeline.run` 未改 |
| 签名 | `auto_discover(package_path: Optional[str] = None) -> int` 保持；新增私有 `_resolve_function(indicator, primary)` |
| 低效操作 | 无 `melt`、无逐行 INSERT（沿用 `MacroLoader` 批量写入） |
| 内存 | 单指标 DataFrame（数百行）级别；无全表扫描 |
| 数据一致性 | 沿用 `MacroLoader` 的 `(trade_date, indicator_id, value_type)` UPSERT 语义；`loaded_at` 由 loader 维护 |

## 3. 涉及文件清单

| 文件 | 改动 |
|------|------|
| `osint/registry.py` | `auto_discover` 用 `__package__` 推导包名 |
| `osint/collectors/akshare_collector.py` | 修正 `INDICATOR_MAP`，新增 `FUNCTION_CANDIDATES` 与 `_resolve_function` |
| `cli.py` | 新增 `cmd_collect`、`collect` 参数注册、handlers、文档串示例 |
| `tests/test_collect.py`（新增） | 发现/解析/CLI 用例 |

## 4. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `registry.py#L140` | `importlib.import_module(f'.collectors.{name}', package='osint')` | `package = __package__ or 'osint'`；`import_module(..., package=package)` |
| `AKShareMacroCollector.INDICATOR_MAP` | 12 个映射中 7 个函数不存在 | 主函数全部为本地实测存在的名字 |
| `AKShareMacroCollector.extract_data` | 直接 `getattr(self._ak, func_name)`，缺失即 `None` | 先经 `_resolve_function` 做候选回退，仍缺失则 error + `None` |
| `cli.py` 子命令数 | 11 | 12 |

## 5. 测试计划（TDD）

| 用例 | 预期（红） | 断言（绿） |
|------|------------|------------|
| `test_auto_discover_finds_packaged_collectors` | 0 个 | `{'akshare_macro','worldbank','news_rss','government'} ⊆ 已注册` |
| `test_akshare_resolves_alias_when_primary_missing` | `AttributeError`/`None` | 假模块仅含 `macro_china_money_supply` → 解析到该名并产出非空 DF |
| `test_akshare_missing_all_candidates_returns_empty` | 抛 `AttributeError` | 返回空 DF，不抛异常 |
| `test_collect_cli_lists_collectors` | `SystemExit: 2`（未知子命令） | 输出含 `akshare_macro` |
| `test_collect_cli_requires_source` | `SystemExit: 2` | 退出码 2 且输出含 `--source` |
| `test_collect_cli_rejects_unknown_source` | `SystemExit: 2` | 退出码 2 且输出含 `未知采集器` |
| `test_collect_cli_runs_registered_source` | `SystemExit: 2` | 输出 `dummy: 1 条`，退出码 0（注册内存假采集器） |

## 6. 生产实跑（真实网络，一次性）

在临时库（已 `init_database`）上执行：

```
collect --list
已注册采集器: 4
  akshare_macro  AKShare 中国宏观经济数据（GDP/CPI/PPI/PMI/M2等）
  government  各国政府政策公告采集
  news_rss  RSS 财经新闻舆情采集
  worldbank  世界银行开放数据 - 全球宏观经济指标
EXIT 0

collect --source akshare_macro --indicator CPI_MONTHLY
akshare_macro: 356 条
EXIT 0
```

入库校验（只读复查）：

```
SELECT indicator_id, COUNT(*), MIN(trade_date), MAX(trade_date) FROM macro_data GROUP BY 1
→ [('CPI_MONTHLY', 356, 1996-03-01, 2025-09-10)]
```

说明：
- 4 个采集器在包模式（`Factor_DB.*`，工作目录 `F:\Coding`）下全部被发现，验证 `__package__` 修复生效。
- `CPI_MONTHLY` 走主函数 `macro_china_cpi_monthly`（本地 AKShare 1.18.64 存在），未触发候选回退。
- 该实跑为一次性人工验证，**不纳入 CI**（CI 仅跑假模块测试）。

