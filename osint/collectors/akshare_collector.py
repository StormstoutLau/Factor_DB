"""
AKShare 宏观数据收集器

封装 AKShare 库的中国宏观经济数据接口。
依赖: pip install akshare
"""

from __future__ import annotations

import logging
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from Factor_DB.core.macro_temporal import estimate_publish_date

from ..base import BaseCollector, OSINTEventType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MacroSourceSpec:
    """单个指标的 freshness 数据源规格"""

    func_name: str
    """AKShare 函数名，如 ``macro_china_cpi``"""

    value_column: str
    """目标取值列，如 ``全国-环比增长``"""

    start_date: date
    """起始 trade_date（含），如 ``date(2025, 8, 1)``"""


class AKShareMacroCollector(BaseCollector):
    """AKShare 中国宏观经济数据收集器"""

    meta = {
        'name': 'akshare_macro',
        'summary': 'AKShare 中国宏观经济数据（GDP/CPI/PPI/PMI/M2等）',
        'categories': ['macro', 'china'],
        'required_keys': [],
    }

    INDICATOR_MAP = {
        'GDP_YEARLY': 'macro_china_gdp_yearly',
        'GDP_QUARTERLY': 'macro_china_gdp',
        'CPI_YEARLY': 'macro_china_cpi_yearly',
        'CPI_MONTHLY': 'macro_china_cpi_monthly',
        'PPI_YEARLY': 'macro_china_ppi_yearly',
        'PPI_MONTHLY': 'macro_china_ppi',
        'PMI_MANUFACTURING': 'macro_china_pmi',
        'M2_YEARLY': 'macro_china_m2_yearly',
        'M2_MONTHLY': 'macro_china_money_supply',
        'INTEREST_RATE': 'macro_china_lpr',
        'FX_RESERVES': 'macro_china_fx_gold',
        'IMPORT_EXPORT': 'macro_china_hgjck',
    }

    # 多列统计表的主数据列（依据 AKShare 1.18.64 源码列名）
    VALUE_COLUMNS = {
        'GDP_QUARTERLY': '国内生产总值-绝对值',
        'PPI_MONTHLY': '当月',
        'PMI_MANUFACTURING': '制造业-指数',
        'M2_MONTHLY': '货币和准货币(M2)-数量(亿元)',
        'FX_RESERVES': '国家外汇储备-数值',
        'IMPORT_EXPORT': '当月出口额-金额',
    }

    _DAY_RE = re.compile(r'^(\d{4})年(\d{1,2})月(\d{1,2})日')
    _QUARTER_RE = re.compile(r'^(\d{4})年第(\d)(?:-(\d))?季度')
    _MONTH_RE = re.compile(r'^(\d{4})年(\d{1,2})月')

    # 主函数名 + 历史/备选名：AKShare 函数命名多次变更（本地 1.18.64 实测 12 个旧映射中 7 个失效）
    FUNCTION_CANDIDATES = {
        'GDP_QUARTERLY': ('macro_china_gdp', 'macro_china_gdp_quarterly'),
        'PPI_MONTHLY': ('macro_china_ppi', 'macro_china_ppi_monthly'),
        'M2_YEARLY': ('macro_china_m2_yearly', 'macro_china_money_supply_yearly'),
        'M2_MONTHLY': ('macro_china_money_supply', 'macro_china_money_supply_monthly'),
        'INTEREST_RATE': ('macro_china_lpr', 'macro_china_interest_rate'),
        'FX_RESERVES': ('macro_china_fx_gold', 'macro_china_fx_reserves_yearly', 'macro_china_fx_reserves'),
        'IMPORT_EXPORT': ('macro_china_hgjck', 'macro_china_import_export'),
    }

    # 金十 five-column 已停更的 4 个指标：追加新期时改用东财/月度多列表
    # 起点 2025-08 对应旧源最后一期 2025-07（发布日 2025-08-09）
    FRESHNESS_SOURCES: Dict[str, MacroSourceSpec] = {
        'CPI_MONTHLY': MacroSourceSpec(
            'macro_china_cpi', '全国-环比增长', date(2025, 8, 1)),
        'CPI_YEARLY': MacroSourceSpec(
            'macro_china_cpi', '全国-同比增长', date(2025, 8, 1)),
        'PPI_YEARLY': MacroSourceSpec(
            'macro_china_ppi', '当月同比增长', date(2025, 8, 1)),
        'M2_YEARLY': MacroSourceSpec(
            'macro_china_money_supply', '货币和准货币(M2)-同比增长', date(2025, 8, 1)),
    }

    def setup(self):
        try:
            import akshare as ak
            self._ak = ak
        except ImportError:
            logger.error("akshare 未安装，请运行: pip install akshare")
            self._error_state = True

    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        indicator = params.get('indicator', 'GDP_YEARLY')
        if indicator not in self.INDICATOR_MAP:
            logger.warning(f"未知指标: {indicator}，使用默认 GDP_YEARLY")
            indicator = 'GDP_YEARLY'
        return {'indicator': indicator, 'func_name': self.INDICATOR_MAP[indicator]}

    def _resolve_function(self, indicator: str, primary: str) -> Optional[str]:
        """解析可用的 AKShare 函数名（显式 primary 优先，再回退候选）

        先试 ``primary`` 是为了让 freshness 阶段的显式函数不被
        ``FUNCTION_CANDIDATES`` 中的旧源候选覆盖；history 阶段的
        primary 均为候选首项，行为不变。
        """
        ak = getattr(self, '_ak', None)
        if ak is None:
            logger.error("AKShare 未初始化，无法解析函数: %s", primary)
            return None

        candidates = (primary,) + tuple(
            name for name in self.FUNCTION_CANDIDATES.get(indicator, ())
            if name != primary
        )
        for name in candidates:
            if hasattr(ak, name):
                if name != primary:
                    logger.warning(f"指标 {indicator} 回退到候选函数: {name}")
                return name

        logger.error(f"指标 {indicator} 无可用 AKShare 函数: {primary}")
        return None

    def collect(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """执行采集：history（金十全历史）+ freshness（东财/月度表新期）

        freshness 仅对 ``FRESHNESS_SOURCES`` 中的 4 个指标执行；失败时
        只记 warning 并返回 history，不置 ``_error_state``。
        """
        history = super().collect(params)
        if self._error_state or self._stop:
            return history

        indicator = self.transform_query(params or {})['indicator']
        if indicator not in self.FRESHNESS_SOURCES:
            return history

        freshness = self._collect_freshness(indicator)
        if freshness.empty:
            return history

        self._collected_count += len(freshness)
        if history.empty:
            return freshness
        return pd.concat([history, freshness], ignore_index=True)

    def _collect_freshness(self, indicator: str) -> pd.DataFrame:
        """执行 freshness 阶段；失败返回空表，不修改 ``_error_state``"""
        spec = self.FRESHNESS_SOURCES[indicator]
        try:
            func = self._resolve_function(indicator, spec.func_name)
            if func is None:
                return pd.DataFrame()
            raw = self.extract_data({'indicator': indicator, 'func_name': func})
            return self.transform_data({'indicator': indicator, 'spec': spec}, raw)
        except Exception as e:
            logger.warning(f"宏观指标 {indicator} freshness 采集失败: {e}")
            return pd.DataFrame()

    @staticmethod
    def _parse_trade_date(value: Any) -> Optional[date]:
        """解析 AKShare 返回的日期，兼容中文月份/季度

        - ``YYYY年MM月份`` / ``YYYY年MM月`` → 当月 1 日
        - ``YYYY年第N季度`` / ``YYYY年第N-M季度`` → 最后一个季度的季末
        - ``YYYY年MM月DD日`` → 该日
        - 标准日期 → ``pd.to_datetime`` 兜底
        """
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value

        text = str(value).strip()
        if not text or text.lower() in {'nan', 'nat', 'none'}:
            return None

        match = AKShareMacroCollector._DAY_RE.match(text)
        if match:
            year, month, day = (int(match.group(i)) for i in (1, 2, 3))
            try:
                return date(year, month, day)
            except ValueError:
                return None

        match = AKShareMacroCollector._QUARTER_RE.match(text)
        if match:
            year = int(match.group(1))
            start_q = int(match.group(2))
            end_q = int(match.group(3) or match.group(2))
            if not 1 <= start_q <= end_q <= 4:
                return None
            month = end_q * 3
            return date(year, month, monthrange(year, month)[1])

        match = AKShareMacroCollector._MONTH_RE.match(text)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            if not 1 <= month <= 12:
                return None
            return date(year, month, 1)

        parsed = pd.to_datetime(text, errors='coerce')
        if pd.isna(parsed):
            return None
        return parsed.date()

    @classmethod
    def _pick_value_column(cls, columns: List[str],
                          indicator: str) -> Optional[str]:
        """按指标映射 → 今值 → 首个匹配列 → 第二列选择数据列"""
        cols = [str(c) for c in columns]
        if not cols:
            return None

        preferred = cls.VALUE_COLUMNS.get(indicator)
        if preferred and preferred in cols:
            return preferred
        if '今值' in cols:
            return '今值'

        for col in cols:
            if any(k in col.lower() for k in ['值', 'value', '数值', '同比', '增速']):
                return col

        return cols[1] if len(cols) > 1 else cols[0]

    def extract_data(self, query: Dict[str, Any]) -> Any:
        if self._error_state:
            return None
        func_name = self._resolve_function(
            query.get('indicator', ''), query['func_name']
        )
        if func_name is None:
            return None
        func = getattr(self._ak, func_name, None)
        if func is None:
            logger.error(f"AKShare 函数不存在: {func_name}")
            return None
        return func()

    @staticmethod
    def _detect_date_column(df: pd.DataFrame) -> Any:
        """探测日期列：优先列名含日期/时间/月份等关键词，否则取首列"""
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower
                   for k in ['日期', 'date', '时间', 'time', '年份', '月份', '季度']):
                return col
        return df.columns[0]

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        """标准化为 macro_data 列

        日期语义：五列发布报告（今值/预测值/前值）的日期为发布日；
        中文月份/季度字符串为统计期（月初/季末）。
        ``query`` 含 ``spec`` 时走 freshness 分支（追加新期）。
        """
        if raw_data is None or (isinstance(raw_data, pd.DataFrame) and raw_data.empty):
            return pd.DataFrame()

        spec = query.get('spec')
        if spec is not None:
            return self._transform_freshness(
                query['indicator'], spec, raw_data
            )

        df = raw_data.copy()

        date_col = self._detect_date_column(df)

        parsed_dates = df[date_col].map(self._parse_trade_date)
        invalid_count = int(parsed_dates.isna().sum())
        if invalid_count:
            logger.warning(f"宏观数据 {query['indicator']} 有 {invalid_count} 行日期无法解析，已丢弃")

        valid_mask = parsed_dates.notna()
        df = df[valid_mask].copy()
        parsed_dates = parsed_dates[valid_mask]
        if df.empty:
            logger.warning(f"宏观数据 {query['indicator']} 无有效日期行")
            return pd.DataFrame()

        value_col = self._pick_value_column(list(df.columns), query['indicator'])
        if value_col is None:
            logger.error(f"宏观数据 {query['indicator']} 无可用数据列")
            return pd.DataFrame()

        result = pd.DataFrame({
            'trade_date': parsed_dates.to_numpy(),
            'indicator_id': query['indicator'],
            'indicator_name': query['indicator'],
            'value': pd.to_numeric(df[value_col], errors='coerce'),
            'value_type': 'raw',
            'data_quality': 100,
        })
        result = result.dropna(subset=['value'])
        return result

    def _transform_freshness(self, indicator: str, spec: MacroSourceSpec,
                             raw_data: pd.DataFrame) -> pd.DataFrame:
        """标准化 freshness 行：截断起点 + 显式统计期/发布日

        不输出 ``source_id`` 列，由 ``MacroLoader._fill_source_id`` 注入
        pipeline 传入的数据源 id。
        """
        df = raw_data.copy()
        date_col = self._detect_date_column(df)
        parsed_dates = df[date_col].map(self._parse_trade_date)
        invalid_count = int(parsed_dates.isna().sum())
        if invalid_count:
            logger.warning(
                f"宏观数据 {indicator} freshness 有 {invalid_count} 行日期无法解析，已丢弃"
            )

        valid_mask = parsed_dates.notna()
        df = df[valid_mask]
        parsed_dates = parsed_dates[valid_mask]
        if df.empty:
            logger.warning(f"宏观数据 {indicator} freshness 无有效日期行")
            return pd.DataFrame()

        if spec.value_column not in df.columns:
            logger.error(
                f"宏观数据 {indicator} freshness 缺少取值列: {spec.value_column}"
            )
            return pd.DataFrame()

        result = pd.DataFrame({
            'trade_date': parsed_dates.to_numpy(),
            'indicator_id': indicator,
            'indicator_name': indicator,
            'value': pd.to_numeric(df[spec.value_column], errors='coerce').to_numpy(),
            'value_type': 'raw',
            'data_quality': 100,
        })
        result = result[result['trade_date'] >= spec.start_date]
        result = result.dropna(subset=['value']).copy()
        if result.empty:
            return result

        result['period_date'] = [
            date(value.year, value.month,
                 monthrange(value.year, value.month)[1])
            for value in result['trade_date']
        ]
        result['publish_date'] = [
            estimate_publish_date(indicator, value)
            for value in result['period_date']
        ]
        result['publish_date_quality'] = 'estimated'
        return result.reset_index(drop=True)

    def produced_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.MACRO_ECONOMIC]

    def collect_indicator(self, indicator: str) -> pd.DataFrame:
        """便捷方法：收集指定指标"""
        return self.collect({'indicator': indicator})

    def collect_all_indicators(self) -> pd.DataFrame:
        """便捷方法：收集所有指标"""
        all_dfs = []
        for indicator_name in self.INDICATOR_MAP:
            try:
                df = self.collect_indicator(indicator_name)
                if not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                logger.warning(f"指标 {indicator_name} 收集失败: {e}")
        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()
