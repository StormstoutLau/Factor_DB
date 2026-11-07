# 五列宏观指标新鲜度设计（P8-1b）

> 日期：2026-11-05
> 范围：为 `CPI_MONTHLY` / `CPI_YEARLY` / `PPI_YEARLY` / `M2_YEARLY` 追加 2025-08 之后的新期；不改 schema、查询层与分析层
> 上游：[五列宏观指标新鲜度调研](../research/macro_freshness_research.md)（含 §11 幻觉排除记录）
> 下游：P8-1b 实施规划（待编写）

---

## 1. 设计目标与非目标

### 1.1 目标

1. 让 4 个陈旧指标的最新统计期从 2025-07 推进到 **2026-09**，且不破坏 1986~2025-07 的历史。
2. 新写入行具备正确的 `period_date` / `publish_date` / `publish_date_quality`，可继续支持 P8-1 的 PIT 查询。
3. 采集器可重复运行（幂等），重复采集不产生同统计期重复行。
4. 不引入新依赖、不改表结构、不改查询/分析接口。

### 1.2 非目标

- **不处理 `GDP_YEARLY`**：旧源单季同比与新源累计同比口径不一致（调研 §4.2），保持现状，单独立项。
- 不重写 2008~2025-07 的历史值（调研 §4.1：该区间差异属首次公布值 vs 后续修订值）。
- 不实现定时调度、失败告警、freshness 监控（P10）。
- 不修改 `MACRO_TEMPORAL_RULES`、`RATE_TYPE_INDICATORS`、`MacroQuery`、`MacroFactorLink`。

---

## 2. 总体方案与数据流

### 2.1 方案选择

调研比较了 4 种方案（调研 §6），本设计采用 **A. 追加新期 + 双阶段采集**：

| 阶段 | 数据源 | 范围 | 职责 |
|------|--------|------|------|
| **history** | 现有金十 five-column 函数（`INDICATOR_MAP` 不变） | 全历史，实际止于 2025-07 | 保留各指标起点（CPI 1986/1996、PPI 1995、M2 1998）~2025-07 的首次公布值与 `exact` 发布日 |
| **freshness** | 东方财富/月度多列表（新增 `FRESHNESS_SOURCES`） | `trade_date >= 2025-08-01` | 追加 2025-08~2026-09，写 `estimated` 发布日 |

> 为什么不是「换源」：调研 §4.1 显示新源起点晚于旧源（CPI 2008、PPI 2006、M2 2008），整表替换会丢失更早历史；且重叠区存在修订差异。双阶段采集让新库与旧库都能得到「完整历史 + 新鲜尾部」。

### 2.2 数据流

```
factor-db collect --source akshare_macro [--indicator X]
        │
        ▼
AKShareMacroCollector.collect(params)
        ├─① history 阶段：super().collect(params)
        │     INDICATOR_MAP[X] → 金十函数 → 全历史（含 2025-09 空值占位，值行被丢弃）
        │
        └─② freshness 阶段（仅 4 指标）：_collect_freshness(X)
              FRESHNESS_SOURCES[X].func_name → 东方财富/月度表
              → 解析「YYYY年MM月份」为月初
              → 过滤 trade_date >= 2025-08-01
              → 显式 period_date=月末
              → publish_date=estimate_publish_date(X, period_date)
              → publish_date_quality='estimated'
        │
        ▼ pd.concat(history, freshness)
OSINTPipeline._store → MacroLoader.load(df, source_id='akshare_macro')
        │
        ▼
macro_data（PK=(trade_date, indicator_id, value_type)）
  - 旧行 trade_date = 发布日（如 2025-08-09）
  - 新行 trade_date = 月初 1 日（如 2025-09-01）
        │
        ▼
MacroQuery: trade_date 输出 = COALESCE(period_date, trade_date)
  - 2025-07 及以前 → 旧行 period 2025-07-31
  - 2025-08 及以后 → 新行 period 月末
  同统计期多行按 publish_date/loaded_at 取最新
```

### 2.3 关键边界

| 边界 | 处理 |
|------|------|
| 新源返回全历史（2008+） | `start_date=2025-08-01` 截断，避免写入重叠区修订值 |
| 新源首期 2025-08，旧源末期 2025-07 | 统计期无重叠，查询层无需跨源去重 |
| 旧源 2025-09 占位行（`今值` NaN） | 现有 `dropna(subset=['value'])` 已丢弃，history 阶段不受影响 |
| `GDP_YEARLY` | 无 `FRESHNESS_SOURCES` 条目，collect 只跑 history 阶段，行为完全不变 |
| freshness 阶段失败 | 只记 warning，返回 history 结果；不置 `_error_state`，不阻断其他指标 |
| 现有单测兼容 | `NormalizationAK` 等假模块无 freshness 函数 → `_resolve_function` 返回 None → freshness 空表 + warning，history 输出不变，既有用例零修改 |

---

## 3. 接口契约

### 3.1 新增配置类型

```python
@dataclass(frozen=True)
class MacroSourceSpec:
    """单个指标的 freshness 数据源规格"""
    func_name: str          # AKShare 函数名，如 'macro_china_cpi'
    value_column: str       # 目标列，如 '全国-环比增长'
    start_date: date        # 起始 trade_date（含），如 date(2025, 8, 1)


class AKShareMacroCollector(BaseCollector):
    FRESHNESS_SOURCES: Dict[str, MacroSourceSpec] = {
        'CPI_MONTHLY': MacroSourceSpec(
            'macro_china_cpi', '全国-环比增长', date(2025, 8, 1)),
        'CPI_YEARLY': MacroSourceSpec(
            'macro_china_cpi', '全国-同比增长', date(2025, 8, 1)),
        'PPI_YEARLY': MacroSourceSpec(
            'macro_china_ppi', '当月同比增长', date(2025, 8, 1)),
        'M2_YEARLY': MacroSourceSpec(
            'macro_china_money_supply',
            '货币和准货币(M2)-同比增长', date(2025, 8, 1)),
    }
```

### 3.2 采集器方法契约

```python
def collect(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """覆写 BaseCollector.collect：history + freshness 合并结果

    - history 阶段：super().collect(params)，沿用现有 transform_query/extract/transform
    - freshness 阶段：indicator ∈ FRESHNESS_SOURCES 时追加 _collect_freshness(indicator)
    - 两阶段任一为空则只返回另一阶段；freshness 异常不外抛
    """

def _collect_freshness(self, indicator: str) -> pd.DataFrame:
    """执行 freshness 阶段；失败返回空表，不修改 _error_state

    内部构造 freshness 查询并复用现有管道：
        spec = FRESHNESS_SOURCES[indicator]
        func = self._resolve_function(indicator, spec.func_name)  # 先试显式函数名
        raw = self.extract_data({'indicator': indicator, 'func_name': func})
        return self.transform_data({'indicator': indicator, 'spec': spec}, raw)
    """

def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
    """query['spec'] 存在时走 freshness 分支：
       - value 列固定为 spec.value_column（不做启发式选择）
       - 仅保留 trade_date >= spec.start_date
       - 输出额外列 period_date / publish_date / publish_date_quality
       - 不输出 source_id 列（交由 MacroLoader._fill_source_id 注入）
       否则维持现有 history 行为。
    """
```

`transform_query` **不变**（仍返回 `{'indicator', 'func_name'}`，仅服务 history 阶段）；freshness 查询由 `_collect_freshness` 内部构造，不经 `transform_query`。函数解析复用现有 [`_resolve_function`](file:///f:/Coding/Factor_DB/osint/collectors/akshare_collector.py#L88-L102)：`FUNCTION_CANDIDATES` 暂不为 freshness 增加候选（新源别名未实测，不臆造；后续发现别名时按实测补充）。

> 实施规划补充（E1）：`_resolve_function` 当前先遍历 `FUNCTION_CANDIDATES`，而 `M2_YEARLY` 已有旧源候选（`macro_china_m2_yearly`），且 `extract_data` 内部会二次解析。若按字面调用，M2 freshness 会命中旧源函数、目标列不存在而返回空表。实施时把解析顺序改为「显式 `primary` 优先，再回退候选」；现有 history 的 `primary` 均为候选首项，行为不变。

### 3.3 不变契约

| 接口 | 保持不变 |
|------|----------|
| `INDICATOR_MAP` | 12 项映射不变；4 个指标仍以旧源做 history |
| `VALUE_COLUMNS` | 不变；freshness 阶段直接使用 `spec.value_column`，避免两套列映射 |
| `MacroLoader.load(data_path, indicator_id=None, *, source_id=None)` | 不改；已支持显式时间列 |
| `MacroQuery` / `MacroFactorLink` | 不改 |
| CLI `collect --source/--indicator` | 不改；`--indicator` 时也自动跑两个阶段 |
| 表结构 / 主键 / 索引 | 不改 |

---

## 4. 关键决策及依据

| # | 决策 | 依据 |
|---|------|------|
| D1 | 采用「追加新期」，不做整表替换 | 调研 §4.1：最近 3 期新旧源逐期一致；历史差异属修订值；新源起点晚于旧源（CPI 2008、PPI 2006、M2 2008），替换会丢更早历史 |
| D2 | 保留 history 阶段 + 新增 freshness 阶段 | 调研 §6 方案 A；新库也能得到完整历史，旧库只补尾部 |
| D3 | freshness 起始 `trade_date >= 2025-08-01` | 旧源末期 period 2025-07（发布日 2025-08-09）；下一期即 2025-08（调研 §5） |
| D4 | `period_date` / `publish_date` / `quality` 由采集器显式写入 | 调研 §7.2/§7.3：day-1 行若交给 `resolve_publish_info` 会被标 `unknown`；显式列避免耦合 legacy 分支 |
| D5 | 发布日复用 `estimate_publish_date`：CPI/PPI 次月 9 日、M2 次月 13 日 | [macro_temporal.py L144-L152](file:///f:/Coding/Factor_DB/core/macro_temporal.py#L144-L152)；与 P8-1 estimated 口径一致 |
| D6 | 取值列显式映射，禁用启发式 | 调研 §3：CPI `全国-当月` 与 PPI `当月` 是价格指数；必须取同比/环比列，启发式会选错 |
| D7 | `GDP_YEARLY` 不进入 freshness | 调研 §4.2：单季同比 vs 累计同比，最大差 5.3pp，换源会改变序列定义 |
| D8 | freshness 失败隔离，不置 `_error_state` | 代码事实：[`BaseCollector.collect`](file:///f:/Coding/Factor_DB/osint/base.py#L166-L189) 捕获异常即置 `_error_state` 并使后续 collect 直接短路；freshness 需在覆写层自行捕获，保住 history 可用性与 12 指标采集 |
| D9 | `source_id` 统一为 `akshare_macro`：`transform_data` 移除硬编码 [`'source_id': 'akshare'`](file:///f:/Coding/Factor_DB/osint/collectors/akshare_collector.py#L232) 列，交由 `MacroLoader._fill_source_id` 注入 pipeline 传入的 registry id | 当前列值优先级高于 loader 参数，硬编码会让新采集行与 P8-1 迁移后的 `akshare_macro` 不一致（代码核对）；消除同一指标两种 source_id |
| D10 | 不新增 ADR | 仅采集器数据源与配置变化；schema/主键/查询/分析模型不变，未达架构级 |

---

## 5. 涉及文件与变更范围

| 文件 | 变更 |
|------|------|
| `osint/collectors/akshare_collector.py` | 新增 `MacroSourceSpec`、`FRESHNESS_SOURCES`；`collect` 覆写；`_collect_freshness`；`transform_data` freshness 分支；移除硬编码 `source_id` |
| `tests/test_collect.py` | 新增独立 `FreshnessAK` 假模块（不修改现有 `NormalizationAK`，避免影响既有单指标用例）；新增 freshness 用例 |
| `tests/test_macro_freshness.py`（新增） | 采集→Loader→Query 端到端：截断、显式时间列、幂等、PIT |
| `docs/implementation/macro_freshness_implementation.md`（待写） | 实施规划 |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第二十二节验收标准 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` / `CODE_WIKI.md` | 实施验收后同步（测试数、采集器说明） |

**不改**：`core/schema.py`、`core/macro_temporal.py`、`loaders/macro_loader.py`、`query/macro_query.py`、`analytics/macro_factor_link.py`、`osint/pipeline.py`、CLI。

---

## 6. 测试计划（设计级）

| # | 场景 | 断言 |
|---|------|------|
| 1 | 配置完整性 | `FRESHNESS_SOURCES` 恰含 4 指标；列名与起点正确；不含 `GDP_YEARLY` |
| 2 | freshness 截断 | 新源返回 2008+，输出仅 2025-08-01 之后 |
| 3 | 显式时间列 | `period_date`=月末；`publish_date`=次月 9/13 日；`quality='estimated'` |
| 4 | 列映射 | CPI 取环比/同比、PPI 取同比、M2 取同比；不取指数列 |
| 5 | 双阶段合并 | history 行 + freshness 行都在；2025-07 与 2025-08 两期连续 |
| 6 | GDP 不受影响 | 只跑 history，输出无新增期、无 `period_date` 显式列 |
| 7 | freshness 失败隔离 | 新源函数缺失/抛异常 → 返回 history，`_error_state=False` |
| 8 | 幂等 | 同一帧 load 两次，行数不变；`publish_date` 保留 |
| 9 | 历史不变 | 2025-07 及以前行数与值不变 |
| 10 | PIT | `as_of=2025-09-05` 排除 2025-08 期 CPI；`2025-09-15` 包含 |
| 11 | 回归 | 全量既有测试通过 |

---

## 7. 风险与权衡

| 风险 | 影响 | 权衡/缓解 |
|------|------|-----------|
| 拼接点后使用修订值 | 2025-08 起与旧口径可能有 0.1~0.2pp 差异 | 追加不改史；文档标注拼接点（调研 §8） |
| 发布日估算偏早 | CPI 9 日可能早于实际 1~2 天，PIT 轻微前视 | 保持 `estimated`；P10 可改为实际发布日采集 |
| 新源列名漂移 | 取列失败 → 空表 | 显式列常量 + 单测锚点；失败日志告警 |
| freshness 与 history 双调用 | 4 指标多 4 次网络请求 | 仅 4 指标；相对全量 12 指标可接受 |
| 新源历史被截断 | 新库历史依赖旧源 | 双阶段设计保证完整；若旧源未来下线，需迁移历史（另立任务） |
| `source_id` 统一 | 下次全量 collect 会把旧行 `akshare` 改为 `akshare_macro` | 与 P8-1 目标一致；单测覆盖 |
| `GDP_YEARLY` 继续陈旧 | 季度同比缺失 2025Q3+ | 调研 §4.2 明确不在本任务；单独立项 |
| `start_date` 硬编码 | 需人工维护 | 常量集中；若后续回补历史再调整 |

---

## 8. 验收锚点（供 checklist 使用）

| 项 | 期望 |
|----|------|
| 4 指标最新统计期 | 2026-09（`period_date`） |
| CPI_MONTHLY 2025-08 | 值 0.0，period 2025-08-31，publish 2025-09-09，estimated |
| CPI_YEARLY 2025-08 | 值 -0.4 |
| PPI_YEARLY 2025-08 | 值 -2.9 |
| M2_YEARLY 2025-08 | 值 8.8 |
| 历史 | 2025-07 及以前行数与值不变 |
| `GDP_YEARLY` | 仍止于 2025-06-30，不新增 |
| PIT | `as_of=2025-09-05` 不含 2025-08 CPI；`2025-09-15` 含 |
| 幂等 | 连续两次 collect/load 行数稳定 |
| 回归 | 全量 397 + 新增用例全部通过；`ruff` 零告警 |

---

## 9. 评审记录（幻觉排除，2026-11-05）

按 [DEVELOPMENT_PROCESS.md](../spec/DEVELOPMENT_PROCESS.md) 设计阶段 Review 门槛（决策回溯调研、接口签名与代码库一致、不声称未验证能力、数字来自实测），独立重读相关源码核对本文全部事实性声明。

### 9.1 核对通过项

| 类别 | 复核项 | 结果 |
|------|--------|------|
| 接口签名 | `collect` / `transform_data` / `transform_query` 与 `BaseCollector`（base.py）签名一致 | ✅ |
| 接口签名 | `_resolve_function` 候选回退存在且行为如述（[akshare_collector.py L88-L102](file:///f:/Coding/Factor_DB/osint/collectors/akshare_collector.py#L88-L102)） | ✅ |
| 接口签名 | `MacroLoader.load(data_path, indicator_id=None, *, source_id=None)`（[macro_loader.py L47-L52](file:///f:/Coding/Factor_DB/loaders/macro_loader.py#L47-L52)）；`_fill_source_id` 列值优先于参数（L259） | ✅ |
| 管道路由 | `OSINTPipeline._store` macro 分支 `loader.load(df, source_id=source_id)`，registry id 可达 loader | ✅ |
| 时间规则 | `estimate_publish_date`：CPI/PPI 次月 9 日、M2 次月 13 日（[macro_temporal.py L144-L152](file:///f:/Coding/Factor_DB/core/macro_temporal.py#L144-L152)） | ✅ |
| CLI | `collect --indicator` 参数存在，单指标运行路径可达双阶段 | ✅ |
| 决策回溯 | D1-D10 均能回溯到调研章节或源码行号 | ✅ |
| 验收数字 | §8 锚点值（0.0 / -0.4 / -2.9 / 8.8、最新期 2026-09、PIT 边界）全部来自调研实测（调研 §11 已独立复测复现） | ✅ |
| 单测兼容 | `NormalizationAK` 无 `macro_china_cpi`/`macro_china_ppi` → `_resolve_function` 返回 None → freshness 空表 + warning，既有用例零修改成立 | ✅ |

### 9.2 修正项（已改入正文）

| # | 问题 | 修正 |
|---|------|------|
| R1 | §3.2 原稿以 `phase` 内部查询键区分两阶段，属过度设计且迫使 `transform_query` 感知 phase | 删除 `phase` 键；freshness 查询由 `_collect_freshness` 内部构造，`transform_query` 保持不变 |
| R2 | freshness 函数解析未指明复用现有机制，且候选别名未实测 | 明确复用 `_resolve_function`；`FUNCTION_CANDIDATES` 暂不为 freshness 增加候选（不臆造未实测别名，后续按实测补充） |
| R3 | D8 缺代码依据（为何不能依赖基类异常处理） | 补充事实：`BaseCollector.collect`（[base.py L166-L189](file:///f:/Coding/Factor_DB/osint/base.py#L166-L189)）捕获异常即置 `_error_state` 并短路后续 collect，freshness 须在覆写层自行捕获 |
| R4 | D9 只写「统一 source_id」未给实现方式；且 `transform_data` 尾部硬编码 `'source_id': 'akshare'` 列值优先级高于 loader 注入 | 明确实现：移除该硬编码列（[akshare_collector.py L232](file:///f:/Coding/Factor_DB/osint/collectors/akshare_collector.py#L232)），交由 `MacroLoader._fill_source_id` 注入 pipeline 传入的 registry id |
| R5 | §2.3 缺「现有单测兼容」边界说明 | 补充边界行：假模块无 freshness 函数 → 空表 + warning，既有用例零修改 |
| R6 | §5 文件清单缺实施验收后的文档同步 | 补充 `DEVLOG.md` / `ARCHITECTURE.md` / `README.md` / `README_zh.md` / `CODE_WIKI.md` 同步行 |
| R7 | （实施规划阶段补充）`_resolve_function` 先遍历 `FUNCTION_CANDIDATES`，`M2_YEARLY` 旧源候选会导致 freshness 解析错误；且 `extract_data` 会二次解析 | 解析顺序改为「显式 `primary` 优先，再回退候选」；history 行为不变（现有 `primary` 均为候选首项），新增单测覆盖 |

### 9.3 结论

评审通过（含 §9.2 所列 7 处修正）。设计可进入实施规划阶段。
