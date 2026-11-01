"""
AKShare 宏观数据收集器

封装 AKShare 库的中国宏观经济数据接口。
依赖: pip install akshare
"""

from __future__ import annotations

import logging
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
        if raw_data is None or (isinstance(raw_data, pd.DataFrame) and raw_data.empty):
            return pd.DataFrame()

        df = raw_data.copy()

        date_col = None
        value_col = None
        for col in df.columns:
            col_lower = str(col).lower()
            if any(k in col_lower for k in ['日期', 'date', '时间', 'time', '年份']):
                date_col = col
            if any(k in col_lower for k in ['值', 'value', '数值', '同比', '增速']):
                value_col = col

        if date_col is None:
            date_col = df.columns[0]
        if value_col is None:
            value_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

        result = pd.DataFrame({
            'trade_date': pd.to_datetime(df[date_col]).dt.date,
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
