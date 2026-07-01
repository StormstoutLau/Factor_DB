"""
World Bank 开放数据收集器

数据源: https://api.worldbank.org/v2/
免费、无需 API Key
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from datetime import date

from ..base import BaseCollector, CollectorConfig, OSINTEventType

logger = logging.getLogger(__name__)


class WorldBankCollector(BaseCollector):
    """World Bank 宏观经济数据收集器"""

    meta = {
        'name': 'worldbank',
        'summary': '世界银行开放数据 - 全球宏观经济指标',
        'categories': ['macro', 'international'],
        'required_keys': [],
    }

    BASE_URL = "https://api.worldbank.org/v2"

    COMMON_INDICATORS = {
        'NY.GDP.MKTP.CD': 'GDP',
        'NY.GDP.MKTP.KD.ZG': 'GDP_GROWTH',
        'FP.CPI.TOTL.ZG': 'CPI',
        'FR.INR.RINR': 'REAL_INTEREST_RATE',
        'SL.UEM.TOTL.ZS': 'UNEMPLOYMENT',
        'FM.LBL.BMNY.CN': 'M2',
        'NE.TRD.GNFS.ZS': 'TRADE_PERCENT_GDP',
        'GC.DOD.TOTL.GD.ZS': 'GOVT_DEBT_PERCENT_GDP',
        'SP.POP.TOTL': 'POPULATION',
        'PA.NUS.FRTF': 'EXCHANGE_RATE',
    }

    def setup(self):
        self.base_url = self.BASE_URL
        self.indicators = self.COMMON_INDICATORS.copy()

    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        query = {
            'format': 'json',
            'per_page': 100,
            'date': params.get('date_range', '2010:2026'),
        }
        if 'country' in params:
            query['country'] = params['country']
        else:
            query['country'] = 'all'
        if 'indicator' in params:
            query['indicator'] = params['indicator']
        return query

    def extract_data(self, query: Dict[str, Any]) -> Any:
        country = query.get('country', 'all')
        indicator = query.get('indicator', 'NY.GDP.MKTP.CD')
        url = f"{self.base_url}/country/{country}/indicator/{indicator}"
        return self.request_get(url, params=query)

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        response_json = raw_data.json()
        if not response_json or len(response_json) < 2:
            logger.warning("World Bank API 返回空数据")
            return pd.DataFrame()

        records = response_json[1]
        if not records:
            return pd.DataFrame()

        rows = []
        for record in records:
            if record.get('value') is not None:
                indicator_id = record['indicator']['id']
                rows.append({
                    'trade_date': record['date'],
                    'indicator_id': self.indicators.get(indicator_id, indicator_id),
                    'indicator_name': record['indicator']['value'],
                    'value': float(record['value']),
                    'value_type': 'raw',
                    'data_quality': 100,
                    'source_id': 'worldbank',
                })

        df = pd.DataFrame(rows)
        if not df.empty:
            df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        return df

    def produced_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.MACRO_ECONOMIC]

    def collect_indicator(self, indicator_code: str, country: str = 'all',
                         date_range: str = '2010:2026') -> pd.DataFrame:
        """便捷方法：收集指定指标"""
        return self.collect({
            'indicator': indicator_code,
            'country': country,
            'date_range': date_range,
        })

    def collect_multi_indicators(self, indicator_codes: List[str],
                                 country: str = 'all',
                                 date_range: str = '2010:2026') -> pd.DataFrame:
        """便捷方法：收集多个指标"""
        all_dfs = []
        for code in indicator_codes:
            df = self.collect_indicator(code, country, date_range)
            if not df.empty:
                all_dfs.append(df)
        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()
