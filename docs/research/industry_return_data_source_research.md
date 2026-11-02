# 行业收益率数据源调研：`macro_factor_link` 行业敏感度 TODO

> 日期：2026-11-02
> 范围：为 `MacroFactorLink.get_sector_sensitivity()` 寻找可落地的行业收益率数据源（只做调研，不改代码）
> 方法：本地环境实测（AKShare 1.18.64、Python 3.12.10、仓库提交 `930b78f`）+ AKShare 官方文档（1.20.22）/ Tushare / Baostock 文档交叉核对

---

## 1. 问题定义

`analytics/macro_factor_link.py#L195-L217` 的 `get_sector_sensitivity()` 目前只做了宏观数据取数，随后：

```python
# TODO: 需要行业收益率数据，这里预留接口
logger.info("行业敏感度分析需要行业收益率数据")
return pd.DataFrame()
```

- 仓库内**无任何调用方**（全库 grep `get_sector_sensitivity` 仅命中定义与 `CODE_WIKI.md`），属于纯预留接口。
- 要完成该功能，最少需要：`行业标识 × 交易日 × 行业收益率`，且时间覆盖需与 `macro_data`（当前实测 CPI 起于 1996-03）可比。

### 数据需求拆解

| 维度 | 要求 | 说明 |
|------|------|------|
| 频率 | 日频（可聚合到月） | 宏观指标多为月频，日收益按月复利/求和即可对齐 |
| 历史长度 | ≥ 2010 年，越长越好 | 宏观指标可回溯到 1996，行业指数越长回归样本越稳 |
| 行业口径 | 一级行业（30 个左右）足够 | 敏感度分析的对象是"板块"，不需要三级细分 |
| 字段 | 收盘价或收益率 + 日期 | AKShare 多数接口只给收盘价，收益率需自算 `pct_change()` |
| 更新时效 | 收盘后当日/次日 | 非交易型用途，T+1 足够 |
| 成本 | 免费、无 token 最佳 | 项目 CI 不触网，实跑由使用者发起 |

---

## 2. 候选数据源

| # | 数据源（AKShare 函数） | 底层来源 | 历史起点 | 行业口径 / 数量 | 免费 | 本次结论 |
|---|------------------------|----------|----------|------------------|------|----------|
| 1 | `index_hist_sw(symbol, period)` | 申万宏源研究指数 | **1999-12-30** | 申万一级 31 / 二级 131 / 三级 335 | ✅ 无 token | **首选**（本机实测可用） |
| 2 | `sw_index_first_info()` / `_second_info()` / `_third_info()` | 乐咕乐股（申万分类） | — | 分类目录 + 估值 | ✅ | 与 1 配套取行业代码/名称 |
| 3 | `index_realtime_sw("一级行业")` | 申万宏源研究 | 实时快照 | 31 | ✅ | 可选，补最新一日 |
| 4 | `stock_board_industry_name_em()` + `stock_board_industry_hist_em()` | 东方财富行业板块 | 文档称按板块指数历史 | 东财行业板块（数量未实测） | ✅ | **本机不可达**（见 §3.2），不作主路径 |
| 5 | `stock_zh_index_daily("sh000928")` 等 | 新浪财经中证行业指数 | 2009-07-03（实测 000928） | 中证行业指数（代码未全量枚举） | ✅ | 可作交叉校验，口径与申万不同 |
| 6 | `stock_sector_spot("新浪行业")` | 新浪行业板块 | **仅实时** | 49 个板块 | ✅ | ❌ 无历史，不能用于回归 |
| 7 | Tushare `sw_daily` | 申万 2021 版行情 | 文档未注明 | 申万 | ❌ **需 5000 积分** | 有账号积分时可用，本次不选 |
| 8 | Baostock `query_history_k_data_plus` | Baostock | 1990-12-19 起（A 股/指数） | 一级/二级行业指数（口径按官方文档） | ✅ | 备选；本机未安装，未实测 |
| 9 | Wind / CSMAR / RESSET | 商业库 | 长 | 全 | ❌ 付费 | 不适用 |

---

## 3. 本地实测证据（AKShare 1.18.64）

### 3.1 申万指数（可用，首选）

| 探测项 | 结果 |
|--------|------|
| `sw_index_first_info()` | **31 行**，列 `行业代码/行业名称/成份个数/静态市盈率/TTM(滚动)市盈率/市净率/静态股息率`；代码形如 `801010.SI` |
| `sw_index_second_info()` | **131 行**，多一列 `上级行业` |
| `sw_index_third_info()` | **335 行** |
| `index_hist_sw("801010", "day")` | **6482 行**，`1999-12-30 ~ 2026-10-30`，列 `代码/日期/收盘/开盘/最高/最低/成交量/成交额`，耗时 ≈6 s |
| `index_hist_sw("801016", "day")`（二级） | 6482 行，同上字段，≈6.3 s |
| `index_hist_sw("850111", "day")`（三级） | 6481 行，同上字段 |
| `index_hist_sw("801010", "week")` | 1359 行，`1999-12-30 ~ 2026-10-30` |
| `index_hist_sw("801010.SI")` | ❌ `KeyError`：**必须剥离 `.SI` 后缀**，只传 6 位代码 |
| `index_realtime_sw("一级行业")` | 31 行，列 `指数代码/指数名称/昨收盘/今开盘/最新价/成交额/成交量/最高价/最低价`，≈3.5 s |
| `index_analysis_daily_sw("801010", ...)` | ❌ `KeyError: '发布日期'`（1.18.64 内部解析失效，不建议依赖） |

要点：
- 返回**只有 OHLC + 量额，没有涨跌幅列**；日收益率需 `收盘.pct_change()` 自算。
- 一级/二级/三级指数均从 1999-12-30 起，覆盖宏观指标历史绰绰有余。
- 单指数 ≈6 s，31 个一级行业全量 ≈3 分钟；需限流，避免并发。

### 3.2 东方财富行业板块（本机不可达）

| 探测项 | 结果 |
|--------|------|
| `stock_board_industry_name_em()` | ❌ `ConnectionError: RemoteDisconnected`（连续 2 次重试均失败） |
| `stock_board_industry_hist_em("小金属", ...)` | ❌ 同上 |
| `stock_zh_index_daily_em("sz399998")` | ❌ 同上 |
| 对照：`stock_zh_a_hist("000001", ...)`（同为东财源） | ❌ 同上 |

结论：**不是单个接口下线，而是当前网络对东财域名整体不可达**。因此东财行业板块即使文档存在，也不能作为本项目的主数据路径；若部署环境可访问，可作为备选（其行业分类与申万不同，且板块指数与申万指数不可混用）。

### 3.3 新浪 / 中证行业指数（可作交叉校验）

| 探测项 | 结果 |
|--------|------|
| `stock_zh_index_daily("sh000928")`（中证能源） | **4208 行**，`2009-07-03 ~ 2026-10-30`，列 `date/open/high/low/close/volume` |
| 000928 名称核实（独立复审） | `index_stock_info()` 732 行中命中 `000928 / 中证能源 / publish_date=2009-07-03`——指数发布日与新浪历史起点**完全一致**，名称与起始日期互证 |
| `stock_zh_index_daily("sh801010")` | ❌ `KeyError: 'date'`（新浪无申万指数） |
| `stock_sector_spot("新浪行业")` | 49 行，仅实时快照（`涨跌幅` 等），**无历史** |

AKShare 源码注释明确提示 `stock_zh_index_daily` "大量抓取容易封 IP"，只适合少量校验。

### 3.4 其他来源（文档核对，未本地实测）

- **Tushare `sw_daily`**：官方文档明确"单次最大 4000 行、**5000 积分可调取**、交易日 18:30 更新" → 有账号积分时可作为申万官方口径的替代，但引入 token 与积分门槛。
- **Baostock**：官方文档称免费提供指数日/周/月 K 线，涵盖"综合指数、规模指数、**一级行业指数、二级行业指数**、策略/成长/价值/主题指数"，历史自 1990-12-19；本机未安装 `baostock`，未实测其行业分类口径与代码表。

---

## 4. 结论与推荐

1. **首选：申万行业指数（`index_hist_sw`）**
   - 免费、无 token、1999 年起、一级/二级/三级全覆盖，且与"申万行业分类"这一国内最常用口径一致；
   - 与 `macro_factor_link` 的月频宏观指标可按月对齐（日收益 → 月收益）；
   - 唯一硬约束：symbol 必须用 6 位代码（剥离 `.SI`），收益率自行由收盘价计算。
2. **交叉校验：中证行业指数（新浪 `stock_zh_index_daily`）**
   - 实测 000928 自 2009 年起；行业分类与申万不同，适合抽样验证收益量级，不适合作为主数据。
3. **不作为主路径：东财行业板块**
   - 当前网络整体不可达；且东财板块指数与申万指数口径不同，混用会污染敏感度结论。
4. **不建议依赖：`index_analysis_daily_sw`**
   - 1.18.64 实测解析失败（`KeyError: '发布日期'`），用 `index_hist_sw` 自算收益更稳。
5. **备选：Tushare `sw_daily`（需 5000 积分）/ Baostock（免费，待实测）**
   - 若后续要求"申万官方口径"或需要第二个独立来源，再评估接入。

---

## 5. 收益率口径与工程注意事项

- **收益率定义**：`ret_t = close_t / close_{t-1} - 1`；行业指数为**价格指数**（不含分红），敏感度结论应表述为"价格收益敏感度"。
- **对齐**：宏观月频指标与行业月收益按自然月聚合（月末收盘 / 上月收盘 - 1，或日收益累乘），避免用未来数据。
- **分类版本**：申万行业分类存在 2014 版 / 2021 版差异，`index_hist_sw` 返回的是当前发布口径；跨十年回归需说明成分变更带来的口径漂移。
- **代码格式**：`sw_index_first_info()` 返回 `801010.SI`，`index_hist_sw` 只接受 `801010`。
- **限流**：31 个一级指数串行 ≈3 分钟；建议复用 `CollectorConfig.rate_limit`，避免并发触发上游限流。
- **存储建议（留给设计阶段决定，不在本次落地）**：
  - `alternative_data` 已具备 `trade_date / data_type / entity_type / entity_id / entity_name / value / source_id` 字段（`core/schema.py#L225-L240`），可承载 `data_type='industry_return'`、`entity_id='801010'` 的长表；
  - 或新增独立 `industry_returns` 表（`trade_date, industry_code, industry_name, close, return_1d`），查询更直观但需迁移。
- **网络与证书**：AKShare changelog 1.20.16 说明申万接口服务器缺中间证书，1.20.16 起随包附带中间证书；本机 1.18.64 实测可访问，但升级/降级时需注意 TLS 行为。

---

## 6. 风险与限制

| 风险 | 影响 | 缓解 |
|------|------|------|
| 申万站点不可用/改版 | 无法取数 | 保留中证（新浪）交叉源；接口封装在独立 collector，失败不影响其他采集器 |
| 只给价格指数，无分红 | 长期收益偏低 | 文档注明口径；如需全收益需另找全收益指数 |
| 行业分类版本漂移 | 长周期可比性下降 | 回归窗口分段；记录分类版本与抓取日期 |
| 单指数 6 s、31 个串行耗时 | 全量刷新慢 | 日增量只取最近一段（`index_hist_sw` 无日期参数，需全量拉取后截断） |
| 东财网络不可达 | 备选源不可用 | 主路径不依赖东财 |
| 免费源无 SLA | 数据中断 | `data_quality` / `source_id` 落库，便于审计 |

> 复审注：`inspect.signature(ak.index_hist_sw)` 实测为 `(symbol: str = '801030', period: str = 'day')`，确认**无日期参数**；`index_realtime_sw` 默认 `二级行业`、`index_analysis_daily_sw` 默认 `市场表征`，调用时须显式传参。

---

## 7. 参考来源

1. AKShare 官方文档（1.20.22）· 指数数据 · `index_hist_sw`（申万宏源研究-指数发布-指数详情-指数历史数据）：<https://akshare.akfamily.xyz/data/index/index.html#id82>
2. AKShare 官方 changelog（1.20.16 · 申万接口 TLS 中间证书说明）：<https://akshare.akfamily.xyz/changelog.html>
3. AKShare 源码 docstring（本机 1.18.64）：`index_hist_sw` 目标地址 `https://www.swsresearch.com/institute_sw/allIndex/releasedIndex/releasedetail?code=801001`；`stock_board_industry_hist_em` 目标地址 `https://quote.eastmoney.com/bk/90.BK1027.html`
4. Tushare 官方文档 · 申万行业日线行情 `sw_daily`（5000 积分门槛）：<https://tushare.pro/wctapi/documents/327.md>
5. Baostock 官方文档 · 指数数据（一级/二级行业指数 K 线，1990-12-19 起）：<http://baostock.com/baostock/index.php/指数数据>
6. 本地实跑记录（2026-11-02，AKShare 1.18.64）：申万一级/二级/三级列表与 `index_hist_sw` 日/周线、`index_realtime_sw`、东财 4 个接口失败复现、新浪中证 000928 历史 —— 见 §3 表格
