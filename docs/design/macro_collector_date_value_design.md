# 宏观采集器日期与取值列修复设计（P6）

> 日期：2026-11-02
> 依据：[macro_collector_date_value_research.md](../research/macro_collector_date_value_research.md)
> 范围：修复 `AKShareMacroCollector` 的中文日期解析与取值列选择；不改表结构、CLI、其他采集器

---

## 1. 目标与非目标

### 目标

1. 支持实测出现的全部中文日期格式：`YYYY年MM月份`、`YYYY年第N季度`、`YYYY年第N-M季度`、`YYYY年MM月DD日`。
2. 五列发布报告取 `今值`，不再取 `前值`。
3. 多列统计表按指标映射取主数据列（GDP/PPI/PMI/M2/外汇/进出口）。
4. 无法解析的日期行丢弃并计数告警；主数据列缺失时回退，最终无可用列则返回空表。
5. 保持现有公共接口 `collect(params)` 不变。

### 非目标

- 不拆分 `IMPORT_EXPORT` 为进口/出口两个指标（当前只取出口金额）。
- 不把发布日回移为统计月（缺少发布滞后证据，保留发布日语义）。
- 不修改 `macro_data` schema、loader、CLI、其他 collector。
- 不改变 `INDICATOR_MAP` / `FUNCTION_CANDIDATES`。

---

## 2. 数据流

```
AKShareMacroCollector.transform_data(query, raw_df)
    ├─ 识别日期列：['日期','date','时间','time','年份','月份','季度']
    ├─ _parse_trade_date(每行) → date | None
    │     ├─ 完整中文日期 YYYY年MM月DD日
    │     ├─ 中文月份 YYYY年MM月(份) → 当月 1 日
    │     ├─ 中文季度 YYYY年第N[-M]季度 → 最后季度末
    │     └─ 标准日期 → pd.to_datetime 兜底
    ├─ 丢弃 None 行，记录 invalid_date_count
    ├─ _pick_value_column:
    │     1) VALUE_COLUMNS[indicator]（列存在时）
    │     2) '今值'
    │     3) 首个含 值/value/数值/同比/增速 的列
    │     4) 第二列（或第一列）
    └─ 标准列 DataFrame（trade_date/indicator_id/.../value/...）
```

---

## 3. 接口契约

### 3.1 新增类成员（`osint/collectors/akshare_collector.py`）

```python
VALUE_COLUMNS = {
    'GDP_QUARTERLY': '国内生产总值-绝对值',
    'PPI_MONTHLY': '当月',
    'PMI_MANUFACTURING': '制造业-指数',
    'M2_MONTHLY': '货币和准货币(M2)-数量(亿元)',
    'FX_RESERVES': '国家外汇储备-数值',
    'IMPORT_EXPORT': '当月出口额-金额',
}

@staticmethod
def _parse_trade_date(value: Any) -> Optional[date]: ...

@classmethod
def _pick_value_column(cls, columns: List[str], indicator: str) -> Optional[str]: ...
```

- `_parse_trade_date` 返回 `datetime.date` 或 `None`，不抛异常。
- `_pick_value_column` 在无任何列时返回 `None`。

### 3.2 `transform_data` 行为

| 场景 | 行为 |
|------|------|
| 日期可解析 | 使用解析结果作为 `trade_date` |
| 日期不可解析 | 丢弃该行；`logger.warning` 记录数量 |
| 映射列存在 | 使用映射列 |
| 映射列缺失 | 回退 `今值` → 首个匹配列 → 第二列 |
| 无可用数据列 | `logger.error`，返回空 DataFrame |
| `value` 转换失败 | `pd.to_numeric(errors='coerce')` 后丢弃 NaN（现有行为） |

### 3.3 日期语义

- 五列发布报告：`trade_date` = **发布日**（保留原日期）。
- 中文月份/季度：`trade_date` = 统计期起点/季末。
- 该差异写入 `transform_data` docstring。

---

## 4. 关键决策及依据

| # | 决策 | 依据 |
|---|------|------|
| D1 | 正则解析中文日期，不依赖 `pd.to_datetime` 推断 | 调研 §2.1：实测 6 个指标因中文格式失败；正则确定性强 |
| D2 | 月份映射为当月 1 日 | 统计期起点，便于按月聚合；与 P5 的自然月对齐一致 |
| D3 | 季度映射为季末，累计季度取最后季末 | `第1-3季度` 表示截至 Q3，取 9-30 最贴合语义 |
| D4 | 五列报告取 `今值` | 调研 §1.2：当前取 `前值`，实测 CPI/PPI/M2 均偏移一期 |
| D5 | 多列指标维护映射表 | 调研 §1.2/§2.2：旧逻辑取最后一个匹配列，PMI/M2/GDP 会取错维度 |
| D6 | 未知指标保留「首个匹配列」兜底 | 不因新增/未知指标直接失败；与旧逻辑相比只改“最后”为“首” |
| D7 | 保留发布日，不回移月份 | 缺少发布滞后证据；发布日与市场反应对齐；回移属推断 |
| D8 | 日期列关键词增加 `月份`/`季度` | 当前未覆盖，依赖第一列兜底；显式识别更稳健 |

---

## 5. 风险与权衡

| 风险 | 影响 | 应对 |
|------|------|------|
| AKShare 升级改列名 | 映射列缺失 | 回退 `今值`/首个匹配列，并记录 warning；测试覆盖映射存在与缺失 |
| `IMPORT_EXPORT` 仅取出口 | 进口数据缺失 | 文档注明；后续可拆指标 |
| 五列报告日期为发布日 | 与统计期错位 | 文档注明；P5 文档已说明非 PIT |
| 季度累计值映射季末 | 同一季度多条累计口径可能重复 | 当前数据每个季度一条累计值，日期唯一；后续如出现重复由主键覆盖 |
| 日期解析过度宽松 | 误解析非日期文本 | 仅匹配明确中文模式，其余走 pandas 兜底；无法解析则丢弃 |
| 未知指标兜底取错列 | 语义偏差 | 已知 12 个指标均有映射或 `今值`；未知指标只保证可运行 |
