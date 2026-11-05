# P7 之后的任务分析与优先级（2026-11-04）

> 日期：2026-11-04
> 范围：盘点 P7 完成后的生产库现状，识别后续任务、依赖链与优先级；只做分析，不改代码
> 方法：生产库只读盘点（`factor_db.duckdb`）+ 采集器/默认 RSS 源探针 + 仓库文档核对
> 下游：[宏观数据时间语义与 PIT 对齐调研（P8-0/P8-1）](macro_temporal_semantics_pit_research.md)

---

## 1. 结论摘要

P7 已修复“率型指标再取 `pct_change`”导致的 Inf 与符号反转，但生产库仍有两个更底层的问题：

1. **宏观日期语义混合**：`macro_data.trade_date` 有的是发布日期，有的是统计期起点/季末；同一指标内部也可能前后不一致。
2. **舆情链路为空**：`news_sentiment` / `report_sentiment` 均为 0 行，默认 RSS 源部分失效，且即使采集也没有实体映射与情感分。

因此后续顺序应为：

```
P8-0 文档/语义纠偏
   → P8-1 宏观日期语义统一 + PIT + source_id
   → P8-1b 五列指标新鲜度
   → P8-3 敏感度产品化
   → P9-1 宏观扩展 + value_kind 元数据
   → P9-2 多源融合
P8-2 舆情可行性调研（可与 P8-1 并行）
```

---

## 2. 生产库实测基线（2026-11-04）

| 数据域 | 实测 | 证据 |
|--------|------|------|
| 宏观 | 12 指标 / 4,358 行；`source_id` 全为 NULL | `macro_data` 按 `source_id` 分组仅 `None` |
| 宏观元数据 | `macro_indicators` 12 行；`data_sources` 2 行；`data_categories` 0；`alternative_types` 0 | 元数据表计数 |
| 行业 | 31 个一级行业 / 144,057 行 / 2000-01-04~2026-11-02 | 仅 `industry_return/first` |
| 舆情 | `news_sentiment` 0 行；`report_sentiment` 0 行 | 表计数 |
| 政府/世界银行 | `alternative_data` 无 `government_policy`；`macro_data` 无 WorldBank 指标 | 采集器已注册但未入库 |
| 默认舆情源 | Reuters 两个源 DNS 失败；CNBC 返回 200 且为 RSS | 默认源探针；`feedparser 6.0.12` 已安装 |

---

## 3. 关键发现

### 3.1 宏观日期语义混合（事实）

- 五列发布日历（`CPI_MONTHLY` / `CPI_YEARLY` / `PPI_YEARLY` / `M2_YEARLY` / `GDP_YEARLY`）的日期是**发布日期**：CPI 最近样本为 `2025-08-09`、`2025-07-09` 等。
- 多列统计表（`PPI_MONTHLY` / `PMI_MANUFACTURING` / `M2_MONTHLY` / `FX_RESERVES` / `IMPORT_EXPORT`）的日期是**统计期起点**：全部为每月 1 日。
- `GDP_QUARTERLY` 的日期是**季末**：全部为 3-31 / 6-30 / 9-30 / 12-31。
- 同一指标内部还发生切换：`CPI_MONTHLY` 2011 年及以前基本为每月 1 日（累计 195/354 行，含少量 2012 月首），2012 年起改为发布日（8~18 日）。

完整审计表与发布日程证据见 [P8 调研](macro_temporal_semantics_pit_research.md)。

### 3.2 五列指标已陈旧约 15 个月（事实）

`CPI_MONTHLY` / `CPI_YEARLY` / `PPI_YEARLY` 止于 2025-08-09，`M2_YEARLY` 止于 2025-08-13，`GDP_YEARLY` 止于 2025-07-15；而 `PPI_MONTHLY` / `PMI_MANUFACTURING` / `M2_MONTHLY` 已到 2026-09/10。P7 默认 diff 的 `n_obs=186` 正是 CPI 止于 2025-08 的产物。

### 3.3 舆情链路“有代码、无数据、无实体、无情感分”（事实）

- [news_rss_collector.py](file:///f:/Coding/Factor_DB/osint/collectors/news_rss_collector.py#L32-L36) 默认 Reuters 源已不可用；CNBC 虽可用，但为英文，`related_stocks=[]`、`sentiment_score=None`。
- [sentiment_query.py](file:///f:/Coding/Factor_DB/query/sentiment_query.py#L38-L66) 依赖 `related_stocks`，当前必然返回空表。
- [sentiment_factor.py](file:///f:/Coding/Factor_DB/analytics/sentiment_factor.py#L148-L166) 的 IC 计算仍返回空表。
- [multi_source_analysis.py](file:///f:/Coding/Factor_DB/analytics/multi_source_analysis.py#L124-L189) 宏观查 `PMI`（实际指标为 `PMI_MANUFACTURING`）、因子硬编码 `PE`、另类数据查 `chain`（生产库不存在），实际全部回落到 0.5。

### 3.4 宏观溯源与载入信息缺失（事实）

- [schema.py](file:///f:/Coding/Factor_DB/core/schema.py#L160-L171) 的 `macro_data` 主键为 `(trade_date, indicator_id, value_type)`，没有 `publish_date` / `loaded_at`。
- [macro_loader.py](file:///f:/Coding/Factor_DB/loaders/macro_loader.py#L198-L208) 写入时固定 `indicator_name=indicator_id`、`source_id=NULL`，并使用 `INSERT OR REPLACE`。
- `M2_YEARLY` 存在 7 个“同月多行”，其中 2020-02-17 值为 0.0；`INTEREST_RATE` 是 1,539 行 / 157 个月的日频观测，不是标准月频序列。

---

## 4. 后续任务与优先级

| ID | 任务 | 价值 | 复杂度/风险 | 优先级 | 前置依赖 |
|----|------|------|-------------|--------|----------|
| P8-0 | 修正文档中的“`trade_date`=统计期”表述；建立 12 指标日期语义审计表 | 避免继续基于错误口径分析 | 小 / 低 | **P0** | — |
| P8-1 | 宏观日期语义统一 + PIT：`period_date` / `publish_date` / `loaded_at` / `source_id` / `as_of` | 解决月份错位、前视、溯源缺失；会影响 P7 beta 复核 | 大 / 中 | **P1** | P8-0 |
| P8-1b | 五列指标新鲜度：替代源或补齐 2025-08 之后 | CPI 等核心指标已陈旧约 15 个月 | 中 / 中 | **P1** | P8-1 调研 |
| P8-2 | 舆情可行性调研：源、实体映射、情感打分 | 当前端到端为 0，先判断是否值得投入 | 中 / 高 | **P1** | — |
| P8-3 | 敏感度产品化：CLI + 结果落库 | 目前只有库函数，下游无法直接消费；低风险快赢 | 小 / 低 | **P2** | P8-1a 对齐口径 |
| P9-1 | 宏观扩展：进口额拆分、社融/M1/财政/就业、WorldBank、`value_kind` 元数据 | 消除 P7 硬编码率型清单，扩展研究覆盖 | 中 / 低 | **P2** | P8-1 |
| P9-2 | 多源融合真实化：修正指标名、接入真实舆情/宏观/另类数据 | 当前全部 0.5 默认值，无实际分析价值 | 中 / 中 | **P3** | P8-2 + P9-1 |
| P10 | 采集调度与质量监控：定时采集、freshness、失败告警、`source_id` | 在线闭环需要可持续运行 | 中 / 中 | **P3** | P8-1d + P8-2 |
| P10b | 技术债：catalog 名、`LIQ_ROLL_Y`、重复 `LoaderConfig`、government 历史化 | 不影响主线，但持续制造困惑 | 小 / 低 | **P4** | — |

---

## 5. 依赖链

```
P8-0 文档/语义纠偏
   └─→ P8-1 宏观日期语义 + PIT + source_id
          ├─→ P8-1b 五列指标新鲜度
          ├─→ P8-3 敏感度产品化/多因子增强
          └─→ P9-1 宏观扩展 + value_kind 元数据
                    └─→ P9-2 多源融合 ←── P8-2 舆情闭环
P8-2 舆情调研（独立，可与 P8-1 并行）
   └─→ P8-2b 采集→实体映射→情感打分→查询
P10 调度/质量监控 ←── P8-1(PIT/source_id) + P8-2(稳定采集)
```

**并行建议**：P8-1（宏观）与 P8-2（舆情调研）互不阻塞；P8-3 的 CLI 部分可先做，但多因子/滚动 beta 必须等 P8-1a 的日期口径确定。

---

## 6. 建议批次

- **P8 第一批（正确性）**：P8-0 → P8-1 调研/设计/实施 → P8-1b
- **P8 第二批（覆盖）**：P8-2 调研；若可行，再做最小闭环
- **P8 第三批（产品化）**：P8-3 CLI/落库 → P9-1 宏观扩展
- **P9/P10（融合与运维）**：P9-2 → P10

---

## 7. 暂不建议做

1. **多因子/滚动 beta**：日期语义未统一前做，结论会返工。
2. **多源融合真实化**：舆情 0 行、另类数据仅行业收益，先做只能继续返回默认值。
3. **调度系统**：采集器未稳定前上调度，会把错误数据固化。
4. **换手/多空收益**：`factor_pipeline` 已实现，Factor_DB 重复实现会形成依赖环。

---

## 8. 证据与来源

- 生产库只读查询：`macro_data` / `alternative_data` / `news_sentiment` / `report_sentiment` / `macro_indicators` / `data_sources`。
- 默认 RSS 源探针：Reuters 两个源 `getaddrinfo failed`；CNBC HTTP 200 且包含 `<rss>`。
- 本地代码核对：[schema.py](file:///f:/Coding/Factor_DB/core/schema.py#L160-L171)、[macro_loader.py](file:///f:/Coding/Factor_DB/loaders/macro_loader.py#L198-L208)、[macro_query.py](file:///f:/Coding/Factor_DB/query/macro_query.py#L60-L66)、[macro_factor_link.py](file:///f:/Coding/Factor_DB/analytics/macro_factor_link.py#L253-L259)。
- 日期语义与发布日程证据见 [P8 调研](macro_temporal_semantics_pit_research.md) §2-§3。
