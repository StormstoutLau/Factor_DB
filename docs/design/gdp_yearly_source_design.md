# GDP_YEARLY 单季同比源设计（P8-1c）

> 日期：2026-11-09
> 范围：新增国家统计局（NBS）GDP 初步核算结果采集器，为 `GDP_YEARLY` 追加 2025Q3~2026Q3；不改 schema、Loader、查询层与分析层
> 上游：[GDP_YEARLY 单季同比源调研](../research/gdp_yearly_source_research.md)（含 §11 幻觉排除记录）
> 下游：[实施规划](../implementation/gdp_yearly_source_implementation.md)（含 §9 评审）/ [验收 checklist](../spec/REVIEW_CHECKLIST.md) 第二十三节

---

## 1. 设计目标与非目标

### 1.1 目标

1. 让 `GDP_YEARLY` 的最新统计期从 **2025-06-30** 推进到 **2026-09-30**，且不改写 2010Q4~2025Q2 的 59 行历史。
2. 新行具备正确的 `period_date` / `publish_date` / `publish_date_quality`，可继续支持 P8-1 的 PIT 查询。
3. 采集可重复运行（幂等）：同一统计期重复采集走 upsert，不产生重复行。
4. 失败隔离：NBS 页面不可用/结构变化时返回空表 + warning，不影响 `akshare_macro` 等其他采集器。
5. 不引入新依赖、不改表结构、不改查询/分析接口、不改 CLI。

### 1.2 非目标

- 不替换 2010Q4~2025Q2 的历史值（旧源为首次公布值，NBS 表为后续修订值，调研 §4.1）。
- 不使用东财累计同比推导单季同比（调研 §3.5：最大误差 0.362pp，不作主源）。
- 不接入 NBS 国家数据 API（调研 §3.1：本环境 403 WAF）、FRED/IMF（本环境不可用）、付费源。
- 不实现定时调度、失败告警、freshness 监控（P10）。
- 不实现 GDP 环比（`GDP_QUARTERLY`）或更早历史回补。

---

## 2. 方案总览与数据流

### 2.1 方案选择

调研 §6 比较了 6 种方案，本设计采用 **A. NBS 官网追加**，并以**独立采集器**落地：

| 方案 | 结论 | 理由 |
|------|------|------|
| **A. NBS 官网新闻稿表 2 + 独立采集器** | ✅ 采用 | 官方口径、值精确、免费、仅用已声明依赖 bs4；与第三方源 provenance 分离 |
| B. 华尔街见闻日历 | ⚠️ 仅交叉验证 | 每季度需数十次请求；第三方；空日期抛 `KeyError`（调研 §3.3） |
| C. 东财累计同比推导 | ❌ | 非官方口径，最大误差 0.362pp |
| D. 继续用旧源 | ❌ | 旧源已停更约 16 个月 |
| E. NBS 国家数据 API | ❌ | 本环境 403 WAF |
| F. Tushare/Wind/Choice | ❌ | 无 token/授权；本任务不引入 |

### 2.2 数据流

```
factor-db collect --source nbs_gdp
        │
        ▼
NBSGDPCollector.collect(params)            ← 覆写：失败后重置 _error_state
        │
        ├─ transform_query: {'start_period': 2025-09-30,
        │                     'max_listing_pages': 6, 'max_detail_pages': 2}
        │
        ├─ extract_data:
        │    ① 发现：https://www.stats.gov.cn/sj/zxfb/ 第 1~6 页
        │         <a> 标题匹配 \d{4}年.*季度.*国内生产总值初步核算结果
        │         → urljoin → URL allowlist（host/path/无 query/fragment）
        │         → 取最新 max_detail_pages 个详情页（按 URL 日期降序）
        │    ② 解析：每个详情页
        │         <table> 逐表：表头网格校验（年份 + 1~4季度）
        │         + 上下文分类（GDP同比增长速度 vs GDP环比增长速度，rfind）
        │         → 逐行解析 20\d{2} 年份与 4 个季度值
        │         → 跳过 '' / '--' / '...' / '…' / 非数值
        │
        ├─ transform_data:
        │     去重（同统计期保留最新页面值）
        │     → 过滤 period_date >= 2025-09-30
        │     → trade_date = period_date = 季末
        │     → publish_date = estimate_publish_date('GDP_YEARLY', period)（+17 天）
        │     → publish_date_quality = 'estimated'
        │
        ▼
OSINTPipeline._store → _detect_data_type='macro' → MacroLoader.load(df, source_id='nbs_gdp')
        │
        ▼
macro_data（PK=(trade_date, indicator_id, value_type)）
  - 旧行 59 行：trade_date=发布日，source_id='akshare_macro'，保持不变
  - 新行 5 行：trade_date=季末，source_id='nbs_gdp'，publish_date 为 estimated
        │
        ▼
MacroQuery: 同期多行按 publish_date/loaded_at 取最新；旧新统计期无重叠
```

### 2.3 关键边界

| 边界 | 处理 |
|------|------|
| 旧源末期 2025Q2（period 2025-06-30） | 新源起点 `2025-09-30`，统计期无重叠，无需跨源去重 |
| 表 2 只覆盖 2021Q1 起 | 只用于追加 2025Q3+；历史仍由 `akshare_macro` 保留 |
| 详情页桌面/移动各一份表 2 | 两表结构与值相同；按统计期去重，保留先解析到的值 |
| 发布日只有估算值 | 统一 `estimate_publish_date` +17 天，`estimated`；误差 -3~+2 天（调研 §11） |
| 发现页无匹配 / 解析失败 | 返回空表 + warning；不写半成品行 |
| `collect` 内异常 | 覆写 `collect` 在 `super().collect` 返回后重置 `_error_state`，下一轮仍可重试 |
| `--indicator` 参数 | CLI 仅对 `akshare_macro` 传 indicator；`nbs_gdp` 使用默认参数即可 |
| 现有采集器发现测试 | `test_collect.py` 用 `issubset` 断言，新增 `nbs_gdp` 不影响 |

---

## 3. 社区优秀案例参考与取舍

调研阶段通过 GitHub 代码检索找到 3 个公开项目，均采用「NBS 新闻稿 + HTML 表解析」路线。本节记录其关键实现与本设计的采纳/拒绝理由，作为 D4/D5/D8 的旁证。

### 3.1 [MIDAS](https://github.com/RenatoVassallo/MIDAS/blob/33edd371b0234ef374cc3dd6c8ad03dc4b5e3789/src/MIDAS/data_nbs_china.py)（`src/MIDAS/data_nbs_china.py` @ `33edd37`）

| 实践 | 代码事实 | 本设计取舍 |
|------|----------|-----------|
| HTTP 重试 | `urllib3.Retry(total=4, connect=4, read=4, backoff_factor=1.0, status_forcelist=[429,500,502,503,504])` + 浏览器 UA/Accept/Referer | **不重复实现**：项目 [`BaseCollector._request`](file:///f:/Coding/Factor_DB/osint/base.py#L236-L286) 已有等价指数退避重试、UA、超时与限速；默认 UA 实测可访问 NBS（200） |
| 表分类 | 先正则提取 `<table>`，取表前 **900 字符 context**（[L1373](https://github.com/RenatoVassallo/MIDAS/blob/33edd371b0234ef374cc3dd6c8ad03dc4b5e3789/src/MIDAS/data_nbs_china.py#L1373)）；对主短语与同义说明句各取 `rfind` 后取最大位置，同比位置 > 环比位置判为同比表 | ✅ **采纳**（D5）：不依赖表序号，桌面/移动双表、页面增删表均不误判；本设计只用主短语 `GDP同比增长速度`/`GDP环比增长速度`，不含同义候选（有意简化，NBS 页面实测两短语均出现） |
| 表头校验 | `row[0] == '年份'` 且 `{'1季度','2季度','3季度','4季度'}.issubset(row[1:5])` | ✅ **采纳**（D5）：与 context 分类双重校验，排除表 1（分行业）与表 3（环比） |
| 空值处理 | 跳过 `''` / `--` / `...` / `…` | ✅ **采纳**（D9）：并补充 `-` / `—` 与非数值告警 |
| 发现分页 | 扫描 `index.html`、`index_N.html`，限制最大页数，连续无命中停止 | ✅ **部分采纳**（D4）：扫描 1~6 页；因历史不由本采集器负责，命中足够数量即停止，不做全量归档遍历 |
| URL 日期 | `_release_date_from_url` 用正则 `t(\d{8})_` 从详情页 URL 提取发布日期 | ✅ **采纳**（D4）：详情页按 URL 日期降序抓取，不依赖列表 DOM 顺序，保证「最新页面值优先」（D11） |
| 发布日 | `_default_gdp_release_date` = 季末次月 18 日 | ❌ **不采纳**：项目已有统一规则 `estimate_publish_date`（季末 +17 天），与 P8-1b 的 `estimated` 口径一致（D7） |
| 历史构建 | 扫全归档构建 1992+ 单季序列 | ❌ **不采纳**：本任务只追加 2025Q3+，历史由旧源保留（D2） |
| 附件解析 | 解析 NBS 页面附件 xlsx | ❌ **不采纳**：`openpyxl` 未在 `pyproject.toml` 声明；HTML 表与附件内容一致（调研 §3.2） |

### 3.2 [FenLynn/push](https://github.com/FenLynn/push/blob/14cc86b4d7794426ee4f08dcbf0662d1558516ca/sources/finance/indicators/gdp.py)（`sources/finance/indicators/gdp.py` @ `14cc86b`）

| 实践 | 代码事实 | 本设计取舍 |
|------|----------|-----------|
| 表头匹配 | `pd.read_html` 后用 `年份` / `1季度` / `4季度` 定位表头行 | ✅ **语义采纳**（D5）：用 bs4 遍历前 3 个 `<tr>` 找网格表头；**不采纳 `pd.read_html`**，因其依赖 lxml（未声明） |
| 相对链接 | `urljoin` 拼接详情页 URL | ✅ **采纳**（D4）：NBS 列表 href 为相对路径 |
| 失败策略 | 找不到表直接 `RuntimeError` | ❌ **不采纳**（D10）：采集器应返回空表 + warning，避免污染 `_error_state` 与 `run_all` |

### 3.3 [alphalake](https://github.com/yinhm/alphalake/blob/19c88d5677051dc256cd1d7a7db725683592f695/internal/source/macro/catalogue.go)（`internal/source/macro/catalogue.go` @ `19c88d5`）

| 实践 | 代码事实 | 本设计取舍 |
|------|----------|-----------|
| URL 白名单 | 仅允许 host `www.stats.gov.cn`、path 前缀 `/sj/`，拒绝 query/fragment；且强制 scheme https、拒绝 user 信息 | ✅ **采纳**（D8）：`urlsplit` 逐项校验，防止列表页外链/跳转；scheme 放宽至 `{http, https}`（与 alphalake 的差异：现有 `government_collector` 对 stats.gov.cn 使用 http URL，放宽以兼容同类入口） |
| 标题模式表 | 每个指标维护标题正则 | ✅ **部分采纳**：本任务只需一条 `\d{4}年.*季度.*国内生产总值初步核算结果`，不做通用 catalogue |

### 3.4 采纳汇总

| 实践 | 来源 | 对应决策 |
|------|------|----------|
| context 表分类 + 表头网格双重校验 | MIDAS / FenLynn | D5 |
| 空 token 跳过 + 非数值告警 | MIDAS | D9 |
| 分页扫描 + 命中上限 | MIDAS | D4 |
| `urljoin` + 官方 host/path allowlist | FenLynn / alphalake | D4/D8 |
| 复用基类重试与限速 | 项目既有 + MIDAS 等价实践 | D3 |
| 失败返回空表而非抛错 | 与 FenLynn 的 `RuntimeError` 策略不同 | D10 |

---

## 4. 接口契约

### 4.1 采集器类与元信息

```python
# osint/collectors/nbs_gdp_collector.py
class NBSGDPCollector(BaseCollector):
    meta = {
        'name': 'nbs_gdp',
        'summary': '国家统计局 GDP 初步核算结果（单季同比）',
        'categories': ['macro', 'china', 'official'],
        'required_keys': [],
    }

    LISTING_URL = 'https://www.stats.gov.cn/sj/zxfb/'
    START_PERIOD = date(2025, 9, 30)
    MAX_LISTING_PAGES = 6
    MAX_DETAIL_PAGES = 2

    TITLE_RE = re.compile(r'\d{4}年.*季度.*国内生产总值初步核算结果')
    YOY_CONTEXT = 'GDP同比增长速度'
    QOQ_CONTEXT = 'GDP环比增长速度'
    QUARTER_HEADERS = ('1季度', '2季度', '3季度', '4季度')
    EMPTY_TOKENS = {'', '--', '...', '…', '-', '—'}
    ALLOWED_HOST = 'www.stats.gov.cn'
    ALLOWED_PATH_PREFIX = '/sj/'
```

`BeautifulSoup` 在解析方法内惰性导入（与 [`government_collector.py` L69](file:///f:/Coding/Factor_DB/osint/collectors/government_collector.py#L69) 的既有写法一致），保持 `auto_discover` 导入模块时无额外副作用。

### 4.2 方法契约

```python
def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
    """返回 {'start_period': date, 'max_listing_pages': int, 'max_detail_pages': int}

    - start_period 可由调用方覆盖（str 'YYYY-MM-DD' 或 date），默认 START_PERIOD
    - 页数上限可由调用方覆盖，用于测试/运维收敛；非法值回退默认并 warning
    """

def extract_data(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
    """发现 + 抓取 + 解析，返回原始记录列表

    每条记录：{'period_date': date, 'value': float, 'source_url': str}
    - 网络/解析异常在单页粒度捕获，返回已成功记录；全部失败返回 []
    - 不修改 _error_state
    """

def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
    """标准化为 macro_data 列

    输出列：trade_date, period_date, publish_date, publish_date_quality,
            indicator_id, indicator_name, value, value_type, data_quality
    - 去重：同 period_date 保留先出现者（发现顺序为最新页面在前）
    - 过滤：period_date >= start_period
    - 不输出 source_id 列（由 MacroLoader._fill_source_id 注入 'nbs_gdp'）
    """

def collect(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """覆写：super().collect 返回后若 _error_state 为真，warning + reset_error()

    BaseCollector.collect 捕获异常会置 _error_state 并使后续 collect 短路；
    NBS 为外部页面源，失败应可重试，且不影响同进程其他采集器。
    返回值为基类结果（异常路径下为空表），失败不向外抛。
    """

def produced_events(self) -> List[OSINTEventType]:
    return [OSINTEventType.MACRO_ECONOMIC]
```

### 4.3 解析契约

| 步骤 | 规则 |
|------|------|
| 列表页 | `LISTING_URL` 为第 1 页；第 N 页为 `urljoin(LISTING_URL, f'index_{N}.html')`（N≥2） |
| 链接发现 | `<a href>` 文本去空白后 `TITLE_RE.search(title)`；`urljoin(page_url, href)` 后过白名单；按 URL 去重 |
| 详情页排序 | 用 `re.search(r't(\d{8})_', url)` 提取发布日期并降序排序；无日期链接保持发现顺序排在后面（MIDAS `_release_date_from_url` 实践） |
| URL 白名单 | scheme ∈ {http, https}；`hostname == 'www.stats.gov.cn'`；`path.startswith('/sj/')`；`query == '' and fragment == ''` |
| 详情页编码 | `response.encoding = 'utf-8'`（NBS 页面为 UTF-8） |
| 表格定位 | `soup.find_all('table')`；对每表遍历前 3 个 `<tr>`，首个满足「首格 `年份` + 第 2~5 格恰为 `1季度`…`4季度`」的视为表头行 |
| 表分类 | 取表前文文本（最近 40 个文本节点逆序拼接，截末 900 字符）；`pos_yoy = context.rfind('GDP同比增长速度')`，`pos_qoq = context.rfind('GDP环比增长速度')`；仅当 `pos_yoy != -1 and pos_yoy > pos_qoq` 时解析 |
| 数据行 | 表头之后的行；首格匹配 `20\d{2}`；单元格去空白、去 `%`、去千分位；`EMPTY_TOKENS` 跳过；`float()` 失败记 warning 并跳过该格 |
| 季度映射 | 1→03-31、2→06-30、3→09-30、4→12-31（含闰年由 `monthrange` 处理） |
| 去重 | 全局按 `period_date` 保留首次出现的值（最新详情页优先，即最新修订优先） |
| 截断 | `period_date >= start_period` |
| 无新期 | 结果为空时 warning 包含「已解析到的最大统计期」，便于运维判断是页面滞后还是解析失败 |

### 4.4 时间语义

| 列 | 值 |
|----|----|
| `trade_date` | 季末（与 `period_date` 相同） |
| `period_date` | 季末 |
| `publish_date` | `estimate_publish_date('GDP_YEARLY', period_date)` = 季末 + 17 天 |
| `publish_date_quality` | `'estimated'` |
| `indicator_id` / `indicator_name` | `'GDP_YEARLY'` |
| `value_type` | `'raw'` |
| `data_quality` | `100` |

> 为什么 `trade_date` 用季末而非发布日：PK 稳定、重复采集幂等（调研 §7.4）；`MacroQuery` 输出 `COALESCE(period_date, trade_date)`（[macro_query.py L48](file:///f:/Coding/Factor_DB/query/macro_query.py#L48)），两种写法查询结果一致。

### 4.5 不变契约

| 接口 | 保持不变 |
|------|----------|
| 表结构 / 主键 / 索引 | 不改 |
| `MacroLoader.load` / `_fill_temporal_columns` / `_fill_source_id` | 不改；显式时间列被保留，`source_id` 由 pipeline 传入 |
| `MacroQuery` / `MacroFactorLink` | 不改 |
| `AKShareMacroCollector` | 不改；`GDP_YEARLY` 的 history 阶段继续输出至 2025Q2 |
| CLI `collect --source/--list` | 不改；注册表自动发现新采集器，`--list` 动态输出 |
| `OSINTPipeline._detect_data_type` | 不改；新帧含 `indicator_id`/`value_type`/`trade_date` → 路由 macro |

---

## 5. 关键决策及依据

| # | 决策 | 依据 |
|---|------|------|
| D1 | 新增独立采集器 `nbs_gdp`，不塞进 `AKShareMacroCollector` | 调研 §7.1；官方源与第三方源 provenance 分离；`--source nbs_gdp` 可单独运行/重试；不影响 12 指标 history 管道。代价是 `--list` 多一项，文档同步即可 |
| D2 | 只追加 `period_date >= 2025-09-30`，不改写历史 | 调研 §4.1：2025Q2 拼接点 5.2 实测一致；2021~2023 差异属修订值 |
| D3 | 复用 `BaseCollector.request_get`，不自定义 Session/Retry | [`base.py`](file:///f:/Coding/Factor_DB/osint/base.py#L236-L296) 已有重试、退避、UA、超时、限速；默认 UA 实测 NBS listing/detail 均 200；MIDAS 的 Retry 实践与之等价 |
| D4 | 发现：扫 `sj/zxfb/` 1~6 页 + 标题正则 + URL 日期降序 + 取最新 2 个详情页 | 调研 §3.2：Q3 页在第 1 页、Q2 页在第 3 页；MIDAS 分页扫描（`index_{page}.html` + 连续无命中停止）与 `_release_date_from_url` 实践；按 URL 日期排序不依赖 DOM 顺序。较调研 §7.3「取最新一条」**有意扩展为 2 条**：单页解析失败时旧页仍可覆盖 2021Q1~2026Q2（仅缺最新一季），代价是多 1 次请求 |
| D5 | 表定位用「表头网格 + context 同比/环比分类」双重校验，不用表序号 | MIDAS `_looks_like_gdp_quarter_grid` + `_classify_gdp_table`；FenLynn 表头匹配；页面桌面/移动双表且表序可能变化 |
| D6 | `trade_date = period_date = 季末` | PK `(trade_date, indicator_id, value_type)` 稳定，重复采集幂等（调研 §7.4）；查询层 `COALESCE(period_date, trade_date)` 两种写法输出一致（[query/macro_query.py L48](file:///f:/Coding/Factor_DB/query/macro_query.py#L48)） |
| D7 | `publish_date = estimate_publish_date(...)`（+17 天），`quality='estimated'` | [`macro_temporal.py L157-L158`](file:///f:/Coding/Factor_DB/core/macro_temporal.py#L157-L158)；5 期实测误差 -3~+2 天；与 P8-1b 的 estimated 口径一致。表 2 含多个历史季度，无法为每行使用页面 PubDate |
| D8 | URL 白名单：host `www.stats.gov.cn` + path `/sj/` + 无 query/fragment | alphalake `ValidateURL` 实践；防止列表页外链与重定向到非官方域名 |
| D9 | 空 token 跳过、非数值告警跳过，不整体失败 | MIDAS `_parse_gdp_quarter_grid` 实践；单个脏单元格不应丢弃整张表 |
| D10 | 失败隔离：单页 try/except + `collect` 覆写重置 `_error_state` | [`BaseCollector.collect`](file:///f:/Coding/Factor_DB/osint/base.py#L166-L189) 异常即置错误态并短路后续 collect；NBS 为外部源，必须可重试且不拖累 `run_all`。重置后 pipeline 不再追加 ERROR 事件，失败可见性由 warning 日志承担 |
| D11 | 跨页/双表去重：同 `period_date` 保留首次出现值 | 详情页按新→旧抓取，最新发布页代表最新修订；桌面/移动双表由同一规则消除重复 |
| D12 | 不新增依赖：bs4 已声明，禁用 `pd.read_html` / xlsx 附件 | [`pyproject.toml`](file:///f:/Coding/Factor_DB/pyproject.toml#L12-L24) 含 `beautifulsoup4`，不含 `lxml`/`openpyxl`；不为一处解析扩大依赖面 |
| D13 | 不在本采集器内做东财推导 fallback | 调研 §3.5：误差最大 0.362pp；静默替换会改变序列精度。若未来需要 fallback，另立任务并显式标记质量 |
| D14 | 不新增 ADR | 仅新增采集器与数据源；schema/主键/查询/分析模型不变，未达架构级 |

---

## 6. 涉及文件与变更范围

| 文件 | 变更 |
|------|------|
| `osint/collectors/nbs_gdp_collector.py`（新增） | `NBSGDPCollector`：发现、抓取、表解析、时间语义、失败隔离 |
| `tests/test_nbs_gdp.py`（新增） | 静态 HTML/Soup 注入，不触网；覆盖 §7 用例 |
| `docs/implementation/gdp_yearly_source_implementation.md`（已编写，含 §9 评审记录） | 实施规划 |
| `docs/spec/REVIEW_CHECKLIST.md` | 新增第二十三节验收标准 |
| `docs/DEVLOG.md` / `docs/ARCHITECTURE.md` / `README.md` / `README_zh.md` / `CODE_WIKI.md` | 实施验收后同步（采集器列表、测试数、GDP 拼接点说明） |

**不改**：`core/schema.py`、`core/macro_temporal.py`、`loaders/macro_loader.py`、`query/macro_query.py`、`analytics/macro_factor_link.py`、`osint/base.py`、`osint/pipeline.py`、`osint/registry.py`、`cli.py`、`osint/collectors/akshare_collector.py`。

---

## 7. 测试计划（设计级）

全部用例使用内联静态 HTML 字符串或 mock `request_get`，不触网。

| # | 场景 | 断言 |
|---|------|------|
| 1 | 元信息与注册 | `meta['name'] == 'nbs_gdp'`；`produced_events() == [MACRO_ECONOMIC]`；`auto_discover()` 后含 `nbs_gdp` |
| 2 | URL 白名单 | 接受 `https://www.stats.gov.cn/sj/zxfb/202610/t20261020_xxx.html`；拒绝外域、`/other/` 路径、带 query、带 fragment、`javascript:` |
| 3 | 分页发现与排序 | 第 1 页含 1 条 GDP + 1 条非 GDP，第 2 页含 1 条更早 GDP；列表顺序故意倒置，`max_detail_pages=2` → 返回 2 个 URL 且按 `tYYYYMMDD` 日期降序（新页在前） |
| 4 | 无命中 | 列表页无匹配标题 → `extract_data` 返回 `[]`，`transform_data` 返回空表 |
| 5 | 表分类 | 同一页面含分行业表（表 1）、同比表（表 2）、环比表（表 3）→ 仅解析表 2 的 4 个季度网格 |
| 6 | 表头校验 | 表头为 `指标/2025年` 等非季度网格 → 跳过，不误解析 |
| 7 | 空值跳过 | `''` / `--` / `...` / `…` / `-` 不产生记录；同行其他季正常保留 |
| 8 | 季度映射 | 年份行 2025 的 4 个值 → `2025-03-31` / `2025-06-30` / `2025-09-30` / `2025-12-31` |
| 9 | 起点截断 | 输入 2021Q1~2026Q3 全表 → 仅输出 2025Q3 之后 5 行 |
| 10 | 去重 | 两个详情页对 2025Q3 给出不同值 → 新页值胜出，且仅 1 行 |
| 11 | 时间列 | `trade_date == period_date == 季末`；`publish_date = 季末 + 17 天`；`quality='estimated'`；无 `source_id` 列 |
| 12 | 失败隔离 | `request_get` 抛异常 → `collect` 返回空表、`_error_state is False`；再次调用会重新发起请求（可恢复） |
| 13 | Loader 集成与幂等 | 采集帧 → `MacroLoader`（临时 DuckDB）→ `MacroQuery`：5 行、值 4.8/4.5/5.0/4.3/4.5、`source_id='nbs_gdp'`；连续 load 两次行数不变 |
| 14 | 历史不变 + PIT | 预置旧源 59 行；采集后 `GDP_YEARLY` 共 64 行且前 59 行值不变；`as_of=2025-10-16` 不含 2025Q3，`2025-10-18` 含 |
| 15 | 回归 | 全量测试通过；`ruff check .` 零告警 |

预计新增 **14 个行为用例 + 1 项回归**；全量从 413 增至约 **427**。

---

## 8. 风险与权衡

| 风险 | 影响 | 权衡/缓解 |
|------|------|-----------|
| NBS 页面结构/URL 变更 | 解析失败、无新期 | 表头网格 + context 分类双重校验；多页发现；失败返回空表 + warning，不写库 |
| 分页文件名规则变化（`index_N.html`） | 只能扫到第 1 页 | 最新季度通常在第 1~3 页（调研 §3.2）；第 1 页即可覆盖最近一期，降级可用 |
| 发布日估算早于实际 1~2 天 | PIT 轻微前视（最多 +2 天） | 统一 `estimated`；文档标注；P10 可改为实际发布日采集 |
| 修订值 | 后续重新采集会更新 `value` | 追加不改史；`publish_date` 由 loader `COALESCE` 保留首估；文档标注拼接点 |
| 表 2 只覆盖 2021+ | 无法回补更早历史 | 本任务只追加 2025Q3+；历史由旧源保留 |
| NBS 频控/WAF | 请求失败 | 基类重试 + 限速；单次运行 ≤ 8 次请求（6 列表 + 2 详情） |
| 双表重复记录 | 行数翻倍 | 按 `period_date` 去重保留首次；单测覆盖 |
| 新采集器影响 `--list`/文档 | 文档与测试计数不同步 | 现有发现测试用 `issubset`；验收后同步 README/ARCHITECTURE/CODE_WIKI |
| `run_all` 触发新采集器 | 全量采集多 8 次请求 | 可接受；失败隔离保证不阻断其他源 |

---

## 9. 验收锚点（供 checklist 使用）

| 项 | 期望 |
|----|------|
| 采集输出 | `collect --source nbs_gdp` 返回 5 行 |
| 新增统计期与值 | 2025Q3=4.8、2025Q4=4.5、2026Q1=5.0、2026Q2=4.3、2026Q3=4.5 |
| 生产库行数 | `GDP_YEARLY` 59 → **64**；`macro_data` 4,414 → **4,419** |
| `period_date` | 2025-09-30、2025-12-31、2026-03-31、2026-06-30、2026-09-30 |
| `publish_date` | 2025-10-17、2026-01-17、2026-04-17、2026-07-17、2026-10-17（均为 estimated） |
| `source_id` | 新行 `nbs_gdp`；旧 59 行 `akshare_macro` 不变 |
| 历史 | 2010Q4~2025Q2 共 59 行值与日期不变 |
| PIT | `as_of=2025-10-16` 不含 2025Q3；`as_of=2025-10-18` 含；`as_of=2026-01-16` 不含 2025Q4，`2026-01-18` 含 |
| 幂等 | 连续两次采集/加载，`GDP_YEARLY` 仍 64 行 |
| 注册与 CLI | `collect --list` 出现 `nbs_gdp`；`--source nbs_gdp` 可运行 |
| 回归 | 全量 413 + 新增用例通过；`ruff` 零告警 |
| 失败演练 | 伪造不可达 URL/空页面 → 返回空表、warning、`_error_state=False`、库无变化 |

---

## 10. 评审记录（幻觉排除，2026-11-09）

按 [DEVELOPMENT_PROCESS.md](../spec/DEVELOPMENT_PROCESS.md) 阶段二设计 Review 门槛（每个决策有调研依据、接口签名与代码库一致、不声称未验证能力、数字来自实测），独立重读本地源码，并用 `gh api` 拉取三个社区案例在所引 commit 的原文逐行核对。

### 10.1 核对通过项

| 类别 | 复核项 | 结果 |
|------|--------|------|
| 接口签名 | `BaseCollector.collect`（[base.py L153-L189](file:///f:/Coding/Factor_DB/osint/base.py#L153-L189)）：入口 L162-164 检查 `_error_state/_stop` 短路；L180-189 捕获异常并 L182 置 `_error_state=True`、返回空表 | ✅ D10 引用 L166-L189 精确 |
| 接口签名 | `_request`/`request_get`（[base.py L236-L296](file:///f:/Coding/Factor_DB/osint/base.py#L236-L296)）：重试、指数退避、UA、代理、限速齐全 | ✅ D3 |
| 接口签名 | `estimate_publish_date` `nbs_gdp` 分支 = `period_date + timedelta(days=17)`（[macro_temporal.py L157-L158](file:///f:/Coding/Factor_DB/core/macro_temporal.py#L157-L158)） | ✅ D7 |
| 接口签名 | `MacroLoader`：`_fill_temporal_columns`（[L178](file:///f:/Coding/Factor_DB/loaders/macro_loader.py#L178)）仅对缺失/NaN 回填，显式列保留；`_fill_source_id`（[L259](file:///f:/Coding/Factor_DB/loaders/macro_loader.py#L259)）列值优先；`_insert_macro_data`（L344-L358）`ON CONFLICT DO UPDATE` 更新 `value`、`COALESCE` 保留 `publish_date` | ✅ §4.5/§8 修订值行为 |
| 查询语义 | `MacroQuery`：`_PERIOD_KEY = COALESCE(period_date, trade_date)`（[L48](file:///f:/Coding/Factor_DB/query/macro_query.py#L48)）；PIT `publish_date <= as_of`（L81-L83）；QUALIFY 按 `publish_date DESC NULLS LAST, loaded_at DESC` 取最新（L109-L114） | ✅ §2.2/§9 |
| 管道路由 | `_detect_data_type`（[pipeline.py L177-L198](file:///f:/Coding/Factor_DB/osint/pipeline.py#L177-L198)）alternative 关键词先判；新帧含 `indicator_id/value_type/trade_date` 且无 `stock_code` → macro；`run` L94-L99 在 `_error_state` 为真时追加 ERROR 事件 | ✅ D10 补注「重置后不再追加 ERROR 事件」成立 |
| 依赖 | [pyproject.toml](file:///f:/Coding/Factor_DB/pyproject.toml#L22) 含 `beautifulsoup4>=4.12.0`（L22）；无 `lxml`、`openpyxl` | ✅ D12 |
| 既有测试 | `test_collect.py` L67-L72 发现断言为 `expected.issubset(ids)`；`test_osint.py` L170-L181 `len(registry)` 为局部注册测试，不走 auto_discover | ✅ §2.3 边界 |
| CLI | `cmd_collect --list` 动态打印 `list_collectors()`；`--source` 校验已注册 ID | ✅ §4.5/§9 |
| 社区案例 | MIDAS @`33edd37`：`Retry(total=4, connect=4, read=4, backoff_factor=1.0, status_forcelist=[429,500,502,503,504], allowed_methods={GET,POST}, raise_on_status=False)`（L491-L499）；表前 900 字符 context（L1373）；`wanted.issubset(set(row[1:5]))`（L1385）；空 token `{"", "--", "...", "…"}`（L1422）；`_default_gdp_release_date` = 季末次月 18 日（L1453-L1456）；`t(\d{8})_` URL 日期（L1459-L1463）；分页 `index_{page}.html` + 连续 12 页无命中停止 + 页数上限常量（L783-L826） | ✅ §3.1 全部证实 |
| 社区案例 | FenLynn/push @`14cc86b`：`pd.read_html(report_url)`（L15）；表头行含 `年份`/`1季度`/`4季度`（L18-L22）；`urljoin`（L52）；找不到表/链接抛 `RuntimeError`（L39/L55） | ✅ §3.2 全部证实 |
| 社区案例 | alphalake @`19c88d5`：`ValidateURL` 强制 https、拒绝 `RawQuery`/`Fragment`/`User`（L44）；nbs 限 host `www.stats.gov.cn` + path `/sj/`（L47） | ✅ §3.3 证实；scheme 差异见 R4 |
| 验收数字 | publish_date 逐期核算：2025-09-30+17=2025-10-17、2025-12-31+17=2026-01-17、2026-03-31+17=2026-04-17、2026-06-30+17=2026-07-17、2026-09-30+17=2026-10-17 | ✅ 与 §9 一致 |
| 验收数字 | 5 期值 4.8/4.5/5.0/4.3/4.5、59 行历史、macro_data 4,414、PIT 边界 2025-10-16/18 与 2026-01-16/18，全部来自调研 §3.2/§5/§10/§11（§11 已独立复测） | ✅ |
| 决策回溯 | D1-D14 逐条可回溯调研章节（§7.1/§4.1/§3.2/§7.4/§3.5）或源码行号；D4 对调研 §7.3 的偏差已显式标注（R3） | ✅ |
| 实现可行性 | §4.3「表前文 context」可由 bs4 遍历 `string=True` 文本节点取表前最近 40 节点、逆序拼接截末 900 字符实现（与 MIDAS 的 HTML 前缀截取语义等价） | ✅ |

### 10.2 修正项（已改入正文）

| # | 问题 | 修正 |
|---|------|------|
| R1 | §3.1 将 MIDAS 表分类简化为「单一 `rfind` 比较」，实际 MIDAS（L1390-L1393）对主短语与同义说明句各取 `rfind` 后取最大位置 | 已改为准确描述；并注明本设计只用主短语 `GDP同比增长速度`/`GDP环比增长速度` 为**有意简化**（两短语在 NBS 页面实测均出现） |
| R2 | D4「旧页仍含 2021+ 全表」表述过强：旧详情页只覆盖 2021Q1 至其发布当季（如 Q2 页止于 2026Q2），不含最新一季 | 已改为「单页解析失败时旧页仍可覆盖 2021Q1~2026Q2（仅缺最新一季）」，降级语义为 5 期可得 4 期 |
| R3 | D4 取 2 个详情页与调研 §7.3「取最新一条」存在偏差但未标注 | 已注明为**有意扩展**及代价（多 1 次请求），符合「每个决策回溯调研」门槛 |
| R4 | §3.3 引用 alphalake「拒绝 query/fragment」但未声明其同时强制 https，而本设计白名单放宽为 `{http, https}` | 已在 §3.3 标注差异及理由（现有 `government_collector` 对 stats.gov.cn 使用 http URL） |

### 10.3 结论

评审通过（含 §10.2 所列 4 处修正）。设计可进入实施规划阶段（`docs/implementation/gdp_yearly_source_implementation.md`）。
