# GDP_YEARLY 单季同比源实施规划（P8-1c）

> 日期：2026-11-09
> 上游：[调研](../research/gdp_yearly_source_research.md) / [设计](../design/gdp_yearly_source_design.md)（含 §10 评审记录）
> 基线：HEAD `2242b83`（P8-1b 已完成）；全量 **413 passed**；`macro_data` **4,414** 行；`GDP_YEARLY` **59** 行（2010-12-31 ~ 2025-06-30）
> 本文档为编码前规划；生产采集与验收结果在 §7.5 回填。

---

## 0. 当前实际与外部锚点

### 0.1 仓库基线（2026-11-09 实测）

| 项 | 当前值 |
|----|--------|
| 分支状态 | P8-1c 四份文档（调研/设计/实施规划 + REVIEW_CHECKLIST §23）已就绪且未提交；代码与生产库未改 |
| 回归基线 | 413 passed；`ruff check .` 零告警 |
| 生产库 | `macro_data` 4,414 行；`GDP_YEARLY` 59 行，`MAX(period_date)=2025-06-30`，`MAX(publish_date)=2025-07-15` |
| 采集器注册 | 5 个（`akshare_macro` / `worldbank` / `news_rss` / `government` / `sw_industry`） |
| 依赖 | `beautifulsoup4>=4.12.0` 已声明（[pyproject.toml L22](file:///f:/Coding/Factor_DB/pyproject.toml#L22)）；无 `lxml`、`openpyxl` |

### 0.2 NBS 页面实测锚点（2026-11-09 现场探测）

**列表页**（`https://www.stats.gov.cn/sj/zxfb/`）：

| 页 | URL | 状态 | `<a>` 总数 | GDP 命中 | 说明 |
|----|-----|------|-----------|----------|------|
| 1 | `https://www.stats.gov.cn/sj/zxfb/` | 200 | 404 | 3 个锚点，**同一 URL 重复 3 次** | `2026年三季度国内生产总值初步核算结果` → `202610/t20261020_1965658.html` |
| 2 | `.../index_2.html` | 200 | 404 | 0 | 无命中 |
| 3 | `.../index_3.html` | 200 | 404 | 2 个锚点，同一 URL 重复 2 次 | `2026年二季度和上半年国内生产总值初步核算结果` → `202607/t20260716_1964142.html` |

> 结论：**必须按 URL 去重后再计入 `max_detail_pages`**，否则第 1 页的 3 个重复锚点会占满 2 个详情页预算，导致 Q2 页永远不被抓取。

**详情页**（Q3 2026）：`tables=6`（桌面/移动各 3 张）、`PubDate=2026/10/20 09:30`。6 张表结构：

| # | 表 | 首行 | 表头行 | 上下文分类 |
|---|----|------|--------|-----------|
| 0/3 | 表 1 分行业绝对额 | `['', '绝对额（亿元）', '比上年同期增长（%）']` | 无季度网格 | `pos_yoy=-1` → 跳过 |
| 1/4 | 表 2 同比 | `['单位：%']` | **第 2 行** `['年份','1季度','2季度','3季度','4季度']` | `pos_yoy=158` → 解析 |
| 2/5 | 表 3 环比 | `['单位：%']` | 第 2 行 同上 | `pos_qoq=53`、`pos_yoy=-1` → 跳过 |

> 结论：**表头不在第 1 行**，首行是「单位：%」；实现必须先在前 3 个 `<tr>` 中定位表头，再从其后的行取数。

**2026 年行原始单元格**（表 2）：

```
['2026', '5.0', '4.3', '4.5', '\u2002']   # 第 4 季度为 EN SPACE，strip 后为空串
```

> 结论：空值不是 `--`，而是 `\u2002`；`get_text(strip=True)` 后变 `''`，`EMPTY_TOKENS` 覆盖。

### 0.3 社区案例锚点（设计 §3 已评审）

| 项目 | commit | 文件 | 采纳点 | 关键行 |
|------|--------|------|--------|--------|
| [MIDAS](https://github.com/RenatoVassallo/MIDAS/blob/33edd371b0234ef374cc3dd6c8ad03dc4b5e3789/src/MIDAS/data_nbs_china.py) | `33edd37` | `src/MIDAS/data_nbs_china.py` | Retry/backoff、表前 900 字符 context、表头网格、空 token、URL 日期、分页 | L491-499、L1373、L1379-1388、L1390-1403、L1405-1425、L1453-1463、L783-826 |
| [FenLynn/push](https://github.com/FenLynn/push/blob/14cc86b4d7794426ee4f08dcbf0662d1558516ca/sources/finance/indicators/gdp.py) | `14cc86b` | `sources/finance/indicators/gdp.py` | 表头行匹配语义、`urljoin` | L15-L29、L52 |
| [alphalake](https://github.com/yinhm/alphalake/blob/19c88d5677051dc256cd1d7a7db725683592f695/internal/source/macro/catalogue.go) | `19c88d5` | `internal/source/macro/catalogue.go` | host/path allowlist、拒绝 query/fragment | L42-L56 |

---

## 1. 分步实施计划

| 步 | 内容 | 验证方式 |
|----|------|----------|
| S1 | 新建 `osint/collectors/nbs_gdp_collector.py`：模块头、`meta`、类常量、`setup`、`transform_query`、`_is_allowed_url` | 单测 1/2；`python -c "from ... import NBSGDPCollector"` |
| S2 | 实现发现与抓取：`_fetch_text`、`_url_date`、`_extract_links`、`_discover_detail_urls`（列表页 URL 构造内联于 `extract_data`，见 §4.1） | 单测 3/4；mock 列表页 3 页 |
| S3 | 实现解析：`_find_header_row`、`_is_yoy_table`（含表前 context 构造）、`_parse_detail_page`（含行解析），见 §4.2 | 单测 5/6/7/8 |
| S4 | 实现标准化与失败隔离：`transform_data`、`collect` 覆写 | 单测 9/10/11/12 |
| S5 | 新增 `tests/test_nbs_gdp.py` 端到端 2 例（Loader + PIT/历史） | 单测 13/14；定向 14 passed |
| S6 | 全量回归、注册表/CLI 验证、生产采集、文档同步与验收回填 | 全量 **427 passed**；`ruff` 零告警；§7.3 锚点 |

**TDD 顺序**：先写 `tests/test_nbs_gdp.py` 14 例 → 运行确认红（模块不存在，全部 import error / fail）→ S1-S4 → 定向绿 → S5 集成绿 → 全量回归 → S6 生产验收。

---

## 2. 工程细节逐项结论

| 类别 | 结论 |
|------|------|
| **版本** | Python `>=3.9`；仅用标准库 `re` / `datetime.date` / `calendar.monthrange` / `urllib.parse`，无新语法 |
| **依赖** | 不新增依赖；bs4 已声明；**不修改** `pyproject.toml` |
| **兼容** | 新增 `source_id='nbs_gdp'`，不覆盖旧行；表结构/主键/索引/查询/CLI 均不变；`--list` 多一项（文档同步） |
| **接口** | 仅新增采集器类；`transform_query` 接受可选 `start_period` / `max_listing_pages` / `max_detail_pages`；无调用方需改签名 |
| **签名** | `collect(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame`（与基类一致）；`extract_data(self, query) -> List[Dict[str, Any]]`；`transform_data(self, query, raw_data) -> pd.DataFrame`；无可变默认参数 |
| **低效操作** | 单次运行最多 8 次 HTTP（6 列表 + 2 详情）；解析 6 张表、23 行/表；入库复用 `MacroLoader` 单条 `INSERT ... SELECT`；无 `melt`、无逐行 INSERT、无全表扫描 |
| **内存** | 输出 5 行 × 9 列；页面 HTML 约 100 KB；远低于 32GB |
| **数据一致性** | `trade_date = period_date = 季末`，PK 稳定；重复采集走 `ON CONFLICT DO UPDATE`；`publish_date` 由 `COALESCE` 保留；旧新统计期无重叠 |
| **失败隔离** | 单页 `try/except` 只 warning；`collect` 覆写把 `_error_state` 复位；不写半成品行、不阻断其他采集器 |
| **计数** | 覆写只调用 `super().collect`，基类已 `_collected_count += len(result)`；**不得重复累加** |
| **事件** | 基类 `collect` 成功路径 `notify_listeners` 一次；失败路径基类已通知 ERROR 事件（无监听者时为空操作）；覆写不再追加任何事件，复位 `_error_state` 后 pipeline 的事件分支走正常路径 |
| **网络** | 列表页按需扫描，命中 2 个唯一详情 URL 即停；实测每轮 3 列表 + 2 详情 = 5 次请求；最坏 8 次；基类限速 1s/次 |
| **编码** | 详情页显式 `response.encoding = 'utf-8'`；NBS 页面为 UTF-8 |
| **日期边界** | `start_period=2025-09-30`（含）；2025-06-30 排除；`publish_date` 为季末 +17 天；`publish_date_quality='estimated'` |
| **重复锚点** | 列表页同一 URL 重复 2~3 次（实测），必须先按 URL 去重再计数 |
| **表头位置** | 首行为「单位：%」，表头在第 2 行；必须在前 3 个 `<tr>` 内搜索表头 |
| **空值形态** | 实测为 `\u2002`（EN SPACE），`get_text(strip=True)` 后为空串；`EMPTY_TOKENS` 覆盖 `''/--/.../…/-/—` |
| **URL 白名单** | scheme `{http, https}`、host `www.stats.gov.cn`、path 前缀 `/sj/`、无 query/fragment；`urlsplit` 异常视为非法 |
| **模块导出** | **不改** `osint/collectors/__init__.py`：`auto_discover` 用 `pkgutil` 扫描模块文件，不依赖包级导出；测试直接导入子模块 |
| **回归影响** | `test_collect.py` 发现断言为 `issubset`；`test_osint.py` 的 `len(registry)` 为局部注册测试；新增采集器不影响 |
| **CI 网络** | 全部单测 mock `request_get`，不触网；`pipeline` fixture 不调用 `auto_discover`，`run_all` 测试只注册 DummyCollector |

---

## 3. 接口 / 签名变更（Before / After）

| 位置 | Before | After |
|------|--------|-------|
| `osint/collectors/nbs_gdp_collector.py` | 不存在 | 新增 `NBSGDPCollector(BaseCollector)`，`meta['name']='nbs_gdp'` |
| `NBSGDPCollector.collect` | — | 覆写：`result = super().collect(params)`；若 `_error_state` 为真则 `self.reset_error()`；返回 `result` |
| `NBSGDPCollector.transform_query` | — | `{'start_period': date, 'max_listing_pages': int, 'max_detail_pages': int}` |
| `NBSGDPCollector.extract_data` | — | 返回 `[{'period_date': date, 'value': float, 'source_url': str}, ...]` |
| `NBSGDPCollector.transform_data` | — | 输出 `trade_date, period_date, publish_date, publish_date_quality, indicator_id, indicator_name, value, value_type, data_quality`（无 `source_id`） |
| `CollectorRegistry.auto_discover` / CLI `--list` | 5 个采集器 | 6 个（新增 `nbs_gdp`）；机制不变 |
| `MacroLoader` / `MacroQuery` / `macro_data` / `cli.py` | — | 均不变 |

---

## 4. 实现级解析算法

### 4.1 发现（`extract_data` 前半）

```
urls = []
seen = set()
for page_url in [LISTING_URL] + [urljoin(LISTING_URL, f'index_{n}.html') for n in 2..max_listing_pages]:
    html = _fetch_text(page_url)                    # 失败 → warning + continue
    for a in BeautifulSoup(html).find_all('a', href=True):
        title = a.get_text(' ', strip=True)
        if not TITLE_RE.search(title): continue
        url = urljoin(page_url, a['href'])
        if not _is_allowed_url(url): continue
        if url in seen: continue                     # 实测同一 URL 重复 2~3 次
        seen.add(url); urls.append(url)
    if len(urls) >= max_detail_pages: break
urls.sort(key=lambda u: _url_date(u) or date.min, reverse=True)   # tYYYYMMDD 降序；无日期兜底 date.min 排后（避免 None 与 date 比较抛 TypeError）
return urls[:max_detail_pages]
```

### 4.2 详情页解析（`_parse_detail_page`）

```
for table in soup.find_all('table'):                 # 实测 6 张（桌面/移动各 3）
    rows = table.find_all('tr')
    header_idx = _find_header_row(rows[:3])          # 首行是「单位：%」，表头在第 2 行
    if header_idx is None: continue
    if not _is_yoy_table(table): continue            # context.rfind 同比 > 环比
    for row in rows[header_idx + 1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
        if len(cells) < 5: continue                    # 注释行/合并行守卫，防 IndexError
        if not re.fullmatch(r'20\d{2}', cells[0]): continue
        for q in 0..3:
            token = cells[q + 1].replace('%', '').replace(',', '').strip()
            if token in EMPTY_TOKENS: continue        # \u2002 → strip 后 ''
            value = float(token)                      # 失败 → warning + skip
            records.append({'period_date': quarter_end(year, q + 1), 'value': value, 'source_url': url})
```

`_find_header_row`：

```python
for idx, row in enumerate(rows[:3]):
    cells = [c.get_text(strip=True) for c in row.find_all(['th', 'td'])]
    if len(cells) >= 5 and cells[0] == '年份' and tuple(cells[1:5]) == QUARTER_HEADERS:
        return idx
return None
```

`_is_yoy_table`：

```python
context = ''.join(reversed([str(s).strip() for s in table.find_all_previous(string=True, limit=40)]))[-900:]
pos_yoy = context.rfind('GDP同比增长速度')
pos_qoq = context.rfind('GDP环比增长速度')
return pos_yoy != -1 and pos_yoy > pos_qoq
```

> 实测表 2 的 `pos_yoy=158`；表 3 context 中「注：同比增长速度为与上年同期对比的增长速度」不含 `GDP同比增长速度`，故 `pos_yoy=-1`，不会误判。

### 4.3 标准化（`transform_data`）

```
seen = set(); rows = []
for record in raw_data:
    period = record['period_date']
    if period < query['start_period'] or period in seen: continue
    seen.add(period)
    rows.append({
        'trade_date': period, 'period_date': period,
        'publish_date': estimate_publish_date('GDP_YEARLY', period),
        'publish_date_quality': 'estimated',
        'indicator_id': 'GDP_YEARLY', 'indicator_name': 'GDP_YEARLY',
        'value': record['value'], 'value_type': 'raw', 'data_quality': 100,
    })
if not rows:
    max_seen = max((r['period_date'] for r in raw_data), default=None)
    logger.warning(f'NBS GDP 未发现 >= {query["start_period"]} 的新期；'
                   f'已解析到的最大统计期: {max_seen}')
    return pd.DataFrame()
return pd.DataFrame(rows).sort_values('period_date').reset_index(drop=True)
```

> 空结果 warning 必须携带 `已解析到的最大统计期`（设计 §4.3）：`max_seen == None` → 发现/解析全失败；`max_seen < start_period` → 页面滞后；`max_seen >= start_period` 但空 → 不可能（截断前已有值），用于运维判断。

### 4.4 失败隔离（`collect`）

```python
def collect(self, params=None):
    result = super().collect(params)
    if self._error_state:
        logger.warning('nbs_gdp 采集失败，已重置错误状态以便下次重试')
        self.reset_error()
    return result
```

基类 `collect`（[base.py L166-L189](file:///f:/Coding/Factor_DB/osint/base.py#L166-L189)）在异常时已置 `_error_state=True` 并返回空表；覆写仅负责复位，使下一轮可重试。注意 `reset_error` 后 pipeline 不再追加 ERROR 事件，失败可见性由 warning 日志承担（设计 D10）。

---

## 5. 涉及修改的文件清单

| 文件 | 改动 |
|------|------|
| `osint/collectors/nbs_gdp_collector.py`（新增） | `NBSGDPCollector` 全部实现（约 220 行） |
| `tests/test_nbs_gdp.py`（新增） | 14 例：发现/解析/时间语义/失败隔离/Loader/PIT |
| `docs/implementation/gdp_yearly_source_implementation.md` | 本文件（§7.5 回填） |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第二十三节验收标准 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` / `CODE_WIKI.md` | 验收后同步：采集器列表 + 测试数 413 → 427 + GDP 拼接点说明 |

**不改**：`core/schema.py`、`core/macro_temporal.py`、`loaders/macro_loader.py`、`query/macro_query.py`、`analytics/macro_factor_link.py`、`osint/base.py`、`osint/pipeline.py`、`osint/registry.py`、`osint/collectors/__init__.py`、`cli.py`、`osint/collectors/akshare_collector.py`、`pyproject.toml`。

---

## 6. 测试计划（TDD）

新增 **14** 例；目标全量 **413 + 14 = 427 passed**。

### 6.1 测试夹具

| 夹具 | 内容 |
|------|------|
| `LISTING_PAGES` | 按 URL 分发的 3 页列表（镜像真实分页）：第 1 页 = 1 条非 GDP + **同一 Q3 URL 重复 3 次**；第 2 页 = 无 GDP 命中；第 3 页 = 同一 Q2 URL 重复 2 次。`request_get` mock 按页 URL 返回对应 HTML |
| `DETAIL_HTML` | 4 张表：分行业（2 列）+ **同比表出现两次**（模拟桌面/移动双份，首行 `单位：%`、第 2 行季度表头、2021~2026 行、2026Q4 为 `\u2002`）+ 环比表（同表头）；表前有 `表2GDP同比增长速度` / `表3GDP环比增长速度` 段落 |
| `DETAIL_HTML_OLD` | 同结构，2025Q3 值故意设为 4.7（用于验证新页胜出） |
| `FakeResponse` | `text` + `encoding` 属性；`request_get` 用 `mock.patch.object` 按 URL 分发 |
| 临时库 | `tempfile.mkdtemp()` + `SchemaManager(conn).init_database()`；tearDown 清 `DuckDBConnection._instances` |

### 6.2 用例清单

| # | 用例 | 红灯预期 | 绿灯断言 |
|---|------|----------|----------|
| 1 | `test_meta_and_registry_discovery` | 模块不存在 | `name='nbs_gdp'`；`produced_events()==[MACRO_ECONOMIC]`；`auto_discover()` 后含 `nbs_gdp` |
| 2 | `test_url_allowlist` | 方法不存在 | 接受 `https://www.stats.gov.cn/sj/zxfb/...` 与 `http://...`；拒绝外域、`/other/`、`?x=1`、`#frag`、`javascript:` |
| 3 | `test_discovery_dedupes_and_sorts` | 不去重/不排序/漏扫后页 | 跨 3 页扫描；重复 3 次锚点只产生 1 个 URL；Q3 排在 Q2 前（URL 日期降序）；`max_detail_pages=2` 返回 2 个详情 URL |
| 4 | `test_discovery_no_match_returns_empty` | — | 无匹配标题 → `extract_data` 返回 `[]` |
| 5 | `test_parses_only_yoy_table` | 表序/表分类错误 | 仅表 2 的季度值；环比表值（如 1.2）不出现 |
| 6 | `test_header_row_not_first` | 误把首行当表头 | 表头在第 2 行时仍解析出 2025Q3=4.8 |
| 7 | `test_skips_empty_and_placeholder_cells` | `\u2002` 被当数值 | 2026Q4 无记录；`--`/`...`/`—` 均跳过；同行其他季保留 |
| 8 | `test_quarter_end_mapping` | 季度映射错 | 2025 行 4 值 → `2025-03-31/06-30/09-30/12-31` |
| 9 | `test_start_period_truncation` | 未截断 | 2021~2026 全表 → 仅 2025Q3 起 5 行 |
| 10 | `test_dedupe_across_pages_newest_wins` | 旧页覆盖新页 | 新页 2025Q3=4.8 胜出；仅 1 行 |
| 11 | `test_temporal_columns_and_no_source_id` | 缺列/多列 | `trade_date==period_date==季末`；publish=季末+17；`estimated`；无 `source_id` 列 |
| 12 | `test_failure_isolation_and_recovery` | 异常外抛/错误态残留 | A：全部请求失败 → 空表、`_error_state=False`；B：`transform_data` 抛错 → 空表且错误态复位，恢复后再次 collect 成功 |
| 13 | `test_loader_integration_idempotent` | 入库失败/重复 | 5 行、值 4.8/4.5/5.0/4.3/4.5、`source_id='nbs_gdp'`；连续 load 两次仍 5 行 |
| 14 | `test_history_invariant_and_pit` | 旧行被改/PIT 失效 | 预置 3 行旧源历史（含 2025Q2=5.2），入库后共 8 行且旧 3 行值不变；`as_of=2025-10-16` 不含 2025Q3，`2025-10-18` 含 |

> 单测用 3 行代表历史；生产验收用真实 59 行（§8）。

### 6.3 红 / 绿口径

- **红灯**：新建测试文件后运行 `py -3.12 -m pytest tests/test_nbs_gdp.py -q`，模块不存在 → 14 例全部 collection error/fail。
- **绿灯**：定向 `tests/test_nbs_gdp.py` **14 passed**；全量 **427 passed**；`py -3.12 -m ruff check .` 零告警。

---

## 7. 生产采集与验收（待回填）

### 7.1 基线记录（采集前）

```sql
SELECT COUNT(*) FROM macro_data;                                                   -- 预期 4,414
SELECT COUNT(*), MAX(period_date), MAX(publish_date)
FROM macro_data WHERE indicator_id = 'GDP_YEARLY';                                -- 预期 59 / 2025-06-30 / 2025-07-15
```

### 7.2 执行

```powershell
py -3.12 -m Factor_DB.cli collect --source nbs_gdp --db factor_db.duckdb
```

预期 CLI 输出：`nbs_gdp: 5 条`（处理行数，不等于新增行数）。

### 7.3 验收锚点

| 项 | 期望 |
|----|------|
| 新增统计期与值 | 2025Q3=4.8、2025Q4=4.5、2026Q1=5.0、2026Q2=4.3、2026Q3=4.5 |
| `GDP_YEARLY` 行数 | 59 → **64** |
| `macro_data` 行数 | 4,414 → **4,419** |
| `period_date` | 2025-09-30、2025-12-31、2026-03-31、2026-06-30、2026-09-30 |
| `publish_date` | 2025-10-17、2026-01-17、2026-04-17、2026-07-17、2026-10-17（均 `estimated`） |
| `source_id` | 新 5 行 `nbs_gdp`；旧 59 行 `akshare_macro` 不变 |
| 历史 | `period_date <= 2025-06-30` 的 59 行值与日期不变 |
| PIT | `as_of=2025-10-16` 最新期 2025-06-30；`2025-10-18` 最新期 2025-09-30；`2026-01-16` 最新期 2025-09-30；`2026-01-18` 最新期 2025-12-31 |
| 幂等 | 二次采集后 `GDP_YEARLY` 仍 64 行、`macro_data` 仍 4,419 行 |
| 注册与 CLI | `collect --list` 出现 `nbs_gdp`；`collect --source nbs_gdp` 可运行 |
| 回归 | 全量 **427 passed**；`ruff` 零告警 |

### 7.4 失败与回滚

| 场景 | 处理 |
|------|------|
| 列表/详情页 403、超时 | 基类重试 3 次；仍失败 → warning + 空表，库不变 |
| 页面结构变化（表头/context 不匹配） | 解析 0 行 → warning 含「未发现 >= 2025-09-30 的新期；已解析到的最大统计期: …」，库不变 |
| 值异常需回滚 | `DELETE FROM macro_data WHERE indicator_id='GDP_YEARLY' AND source_id='nbs_gdp' AND period_date >= DATE '2025-09-30';` 历史行不受影响 |

### 7.5 实测（2026-11-10）

| 项 | 实测 |
|----|------|
| 采集输出 | CLI `nbs_gdp: 5 条` |
| 行数变化 | `GDP_YEARLY` 59 → **64**；`macro_data` 4,414 → **4,419** |
| 5 期值与日期列 | 2025Q3=4.8@2025-09-30、2025Q4=4.5@2025-12-31、2026Q1=5.0@2026-03-31、2026Q2=4.3@2026-06-30、2026Q3=4.5@2026-09-30 |
| `publish_date` | 2025-10-17、2026-01-17、2026-04-17、2026-07-17、2026-10-17；均 `estimated` |
| `source_id` | 59 `akshare_macro` + 5 `nbs_gdp` |
| 历史不变 | 59 行 md5 `fef42d21daff0eb7bf64a93beb71787b` 采集前后一致 |
| 幂等 | 二次采集后仍 64 行 / 4,419 行 |
| PIT | `2025-10-16` → 2025-06-30；`2025-10-18` → 2025-09-30；`2026-01-16` → 2025-09-30；`2026-01-18` → 2025-12-31 |
| 回滚 | 临时库验证 DELETE 后仅剩 `akshare_macro` 1 行（2025-06-30=5.2） |
| 回归 | 定向 **14 passed**；全量 **427 passed**；`ruff` 零告警 |

---

## 8. 实施规划 Review 门槛自检

- [x] **依赖版本已核实**：bs4 已声明（`pyproject.toml` L22）；不新增依赖
- [x] **文件路径真实存在**：`osint/collectors/`、`tests/`、`docs/spec/REVIEW_CHECKLIST.md` 均已核对；新文件路径符合项目布局
- [x] **无低效操作**：无 `melt`、无逐行 INSERT、无全表扫描；最多 8 次 HTTP、5 行输出
- [x] **接口变更列出 Before/After**：见 §3
- [x] **32GB 内存约束**：数据量与页面体量可忽略
- [x] **工程隐患排除**：E1 重复锚点占满详情页预算（§0.2）、E2 表头不在首行（§0.2）、E3 `\u2002` 空值（§0.2）、E4 `collect` 重复计数（§2）、E5 `__init__.py` 无需导出（§2）
- [x] **失败隔离**：单页 try/except + `collect` 复位；网络失败库不变
- [x] **外部锚点充分**：社区案例行号（§0.3）+ NBS 现场探测（§0.2）+ 调研/设计交叉引用

---

## 9. 评审记录（幻觉排除，2026-11-09）

按 [DEVELOPMENT_PROCESS.md](../spec/DEVELOPMENT_PROCESS.md) 阶段三 Review 门槛（依赖版本核实、文件路径真实、无低效操作、Before/After、32GB 约束、工程隐患排除），独立复测基线与关键声明。

### 9.1 核对通过项（本轮独立复测）

| 类别 | 复核项 | 结果 |
|------|--------|------|
| 仓库基线 | git HEAD `2242b83`；全量 `py -3.12 -m pytest -q` → **413 passed**（203.70s） | ✅ 实测复现 |
| 生产库 | 只读 SQL：`macro_data` **4,414**；`GDP_YEARLY` **59** 行 / `MAX(period_date)=2025-06-30` / `MAX(publish_date)=2025-07-15`；59 行 `source_id` 全为 `akshare_macro` | ✅ 实测复现 |
| 注册表 | `auto_discover()` 返回 **5**，ID 与 §0.1 一致（`akshare_macro/government/news_rss/sw_industry/worldbank`） | ✅ 实测；同时证实 E5（pkgutil 扫描模块文件即可注册，不依赖 `__init__.py` 导出） |
| E1 重复锚点 | 现场探测：第 1 页同一 Q3 URL 重复 **3** 次、第 3 页重复 **2** 次 | ✅ 去重前不可直接计数 |
| E2 表头位置 | 现场探测：表 2 首行 `['单位：%']`，季度表头在第 2 行 | ✅ |
| E3 空值形态 | 现场探测：2026Q4 原始 `\u2002`，`get_text(strip=True)` 后 `''` | ✅ |
| E4 计数 | [base.py L170](file:///f:/Coding/Factor_DB/osint/base.py#L170) `_collected_count += len(result)` 在 try 内；覆写再累加会双计 | ✅ |
| 分类算法可行性 | 用 `find_all_previous(string=True, limit=40)` 逆序拼接取末 900 字符，在真实 Q3 页面实测：表 2 `pos_yoy=158/pos_qoq=-1` → yoy；表 3 `pos_qoq=53/pos_yoy=-1` → 跳过；表 1 无季度网格 → 跳过 | ✅ 真实页面验证 |
| 验收数字 | 59+5=64、4,414+5=4,419、publish 五期 +17 天（2025-10-17 等）、PIT 边界 10-16/18 与 01-16/18，与设计 §9 一致 | ✅ |
| CLI 输出 | `nbs_gdp: 5 条` 来源：`cmd_collect` 打印 `results[source_id]` = `MacroLoader._insert_macro_data` 返回 `len(df)=5` | ✅ |
| 回滚 SQL | DELETE 限定 `source_id='nbs_gdp' AND period_date >= 2025-09-30`；旧行 `source_id='akshare_macro'` 不受影响 | ✅ |
| 既有测试兼容 | `test_collect.py` 发现断言 `issubset`；`test_osint.py` `len(registry)` 为局部注册测试；L632-635 的 4 个导入走 `__init__.py` 现有导出，不受新增模块影响（本任务测试直接导入子模块，与 [test_macro_freshness.py L24](file:///f:/Coding/Factor_DB/tests/test_macro_freshness.py#L24) 风格一致） | ✅ |
| 低效/内存/依赖 | ≤8 次 HTTP、5 行输出；bs4 已声明；无 `melt`/逐行 INSERT/全表扫描 | ✅ |
| Python 3.9 兼容 | 仅标准库语法（`re`/`datetime`/`urllib.parse`/`calendar.monthrange`），无 walrus/match | ✅ |

### 9.2 修正项（已改入正文）

| # | 问题 | 修正 |
|---|------|------|
| F1 | §4.3 空结果 warning 未携带「已解析到的最大统计期」，与设计 §4.3「warning 包含已解析到的最大统计期」不符，运维无法区分页面滞后与解析失败 | 已补 `max_seen` 计算与三种语义（None=全失败 / <起点=页面滞后 / ≥起点=不可能） |
| F2 | §6.1 `DETAIL_HTML` 夹具仅 3 张表，未模拟真实页面的桌面/移动双份同比表，同页去重路径无测试覆盖 | 已改为 4 张表（同比表 ×2），同页去重随单测 5 隐式覆盖 |

### 9.3 结论

评审通过（含 §9.2 所列 2 处修正）。基线三项（413 passed / `macro_data` 4,414 / 注册 5 采集器）均独立实测复现。可进入 TDD 编码（S1：先写 `tests/test_nbs_gdp.py` 确认红灯）。

---

## 10. 四文档一致性对齐记录（阶段四，2026-11-09）

按 [DEVELOPMENT_PROCESS.md](../spec/DEVELOPMENT_PROCESS.md) 阶段四门槛，对调研 / 设计 / 实施规划 / [REVIEW_CHECKLIST §23](../spec/REVIEW_CHECKLIST.md) 做交叉核对。

### 10.1 核对通过项

| 维度 | 核对项 | 结果 |
|------|--------|------|
| 数字 | 59 → 64、4,414 → 4,419、5 期值 4.8/4.5/5.0/4.3/4.5、publish 五期（+17 天）、测试 413 → 427（+14）、发布日误差 -3~+2 天：四文档一致 | ✅ |
| 术语 | 采集器 `nbs_gdp` / `NBSGDPCollector`；新行 `source_id='nbs_gdp'`、旧行 `akshare_macro`；`start_period=2025-09-30`；`MAX_LISTING_PAGES=6` / `MAX_DETAIL_PAGES=2`；≤8 次 HTTP（实测 5 次）：一致 | ✅ |
| 常量 | `TITLE_RE` / `YOY_CONTEXT` / `QOQ_CONTEXT` / `QUARTER_HEADERS` / `EMPTY_TOKENS`（6 token）/ `ALLOWED_HOST` / `ALLOWED_PATH_PREFIX`：设计 §4.1 与实施 §4 引用一致 | ✅ |
| 接口 | `collect` 覆写（复位不抛出）、`transform_query` 三键、`extract_data` 记录三键、`transform_data` 9 列无 `source_id`：设计 §4.2 与实施 §3/§4 相同 | ✅ |
| 用例映射 | 设计 §7 的 14 例与实施 §6.2 的 #1-#14 一一对应（#15 回归 = 实施 S6） | ✅ |
| 算法 | URL 去重先于计数（E1）、表头前 3 行定位（E2）、`\u2002` 空值（E3）、context 900 字符 / 40 节点、URL 日期降序、同/跨页去重、失败隔离与 `_error_state` 复位：调研 §7 建议 → 设计 §4.3/D 决策 → 实施 §4 算法链条完整 | ✅ |
| 边界 | 不改 11 个既有文件清单：设计 §6 与实施 §5 完全一致（含 `osint/collectors/__init__.py`） | ✅ |
| checklist | §23 六组 47 项与上述内容逐条对应；验收命令与实施 §7.2 一致 | ✅ |

### 10.2 修正项（已改入正文）

| # | 文档 | 问题 | 修正 |
|---|------|------|------|
| C1 | 调研 / 设计头部 | 「下游：…（待编写）」已过时 | 改为指向设计/实施/checklist 的实际链接；设计 §6 实施行去掉「待写」 |
| C2 | 实施 §1 | S2/S3 列出的 `_listing_urls` / `_table_context` / `_parse_rows` 未在 §4 算法中出现，命名漂移 | 对齐为 §4 实际助手名（`_fetch_text`/`_url_date`/`_extract_links`/`_discover_detail_urls`/`_find_header_row`/`_is_yoy_table`/`_parse_detail_page`），并注明内联部分 |
| C3 | 实施 §6.1 | `LISTING_HTML` 单页夹具与设计 §7 #3「第 2 页含更早 GDP」的多页扫描语义不符，分页路径无覆盖 | 改为 `LISTING_PAGES` 三页结构（镜像真实分页：Q3×3 / 无命中 / Q2×2），用例 3 断言跨 3 页扫描 |
| C4 | 实施 §2 | 事件行「只在成功路径 notify_listeners 一次」不精确（失败路径基类也通知 ERROR 事件） | 改为区分成功/失败路径，覆写不追加任何事件 |
| C5 | 设计 D6 / §4.4 | `COALESCE` 事实误标注为「调研 §7.4」（实际出处是查询层代码） | 依据拆分为调研 §7.4（PK 稳定）+ [macro_query.py L48](file:///f:/Coding/Factor_DB/query/macro_query.py#L48)（COALESCE） |
| C6 | 实施 §0.1 | 「三份文档」未含已修改的 REVIEW_CHECKLIST §23 | 更新为四份文档 |
| C7 | 实施 §4.1 | `urls.sort(key=_url_date)` 在存在无日期 URL 时 None 与 date 比较抛 TypeError | 兜底 `key=lambda u: _url_date(u) or date.min` |
| C8 | 实施 §4.2 | 数据行未守卫 `len(cells) < 5`，注释行/合并行会 IndexError（真实页面 row8 注释行仅 1 格） | 加长度守卫 `continue` |

### 10.3 结论

一致性对齐通过（含 §10.2 所列 8 处修正，其中 C7/C8 为实现级隐患排除）。调研 → 设计 → 实施 → checklist 四文档数字、术语、接口、边界全部一致，可进入 TDD 编码。
