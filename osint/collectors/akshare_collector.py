"""
AKShare 宏观数据收集器

封装 AKShare 库的中国宏观经济数据接口。
依赖: pip install akshare
"""

from __future__ import annotations

import logging
import re
from calendar import monthrange
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, OSINTEventType

logger = logging.getLogger(__name__)


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
        """按候选顺序解析可用的 AKShare 函数名"""
        ak = getattr(self, '_ak', None)
        if ak is None:
            logger.error("AKShare 未初始化，无法解析函数: %s", primary)
            return None

        for name in self.FUNCTION_CANDIDATES.get(indicator, (primary,)):
            if hasattr(ak, name):
                if name != primary:
                    logger.warning(f"指标 {indicator} 回退到候选函数: {name}")
                return name

        logger.error(f"指标 {indicator} 无可用 AKShare 函数: {primary}")
        return None

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

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        """标准化为 macro_data 列

        日期语义：五列发布报告（今值/预测值/前值）的日期为发布日；
        中文月份/季度字符串为统计期（月初/季末）。
        """
        if raw_data is None or (isinstance(raw_data, pd.DataFrame) and raw_data.empty):
            return pd.DataFrame()

        df = raw_data.copy()

        date_col = None
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower
                   for k in ['日期', 'date', '时间', 'time', '年份', '月份', '季度']):
                date_col = col
                break

        if date_col is None:
            date_col = df.columns[0]

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
            'source_id': 'akshare',
        })
        result = result.dropna(subset=['value'])
        return result

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
