"""
政府公告收集器

采集各国政府发布的政策公告、经济数据发布等。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, CollectorConfig, OSINTEventType

logger = logging.getLogger(__name__)


class GovernmentCollector(BaseCollector):
    """政府公告收集器"""

    meta = {
        'name': 'government',
        'summary': '各国政府政策公告采集',
        'categories': ['government', 'policy'],
        'required_keys': [],
    }

    SOURCES = {
        'pbc': {
            'name': '中国人民银行',
            'url': 'http://www.pbc.gov.cn/goutongjiaoliu/',
            'type': 'monetary_policy',
        },
        'ndrc': {
            'name': '国家发改委',
            'url': 'https://www.ndrc.gov.cn/xwdt/',
            'type': 'economic_policy',
        },
        'stats': {
            'name': '国家统计局',
            'url': 'http://www.stats.gov.cn/sj/zxfb/',
            'type': 'economic_data',
        },
    }

    def setup(self):
        self.sources = self.SOURCES.copy()

    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        source_key = params.get('source', 'pbc')
        if source_key not in self.sources:
            source_key = 'pbc'
        return {'source_key': source_key, **self.sources[source_key]}

    def extract_data(self, query: Dict[str, Any]) -> Any:
        try:
            response = self.request_get(query['url'])
            return response.text
        except Exception as e:
            logger.error(f"政府公告采集失败 {query['source_key']}: {e}")
            return None

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        if raw_data is None:
            return pd.DataFrame()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw_data, 'html.parser')

        rows = []
        items = soup.find_all(['a', 'li'], class_=True)
        for item in items[:20]:
            title = item.get_text(strip=True)
            link = item.get('href', '')
            if not title or len(title) < 5:
                continue

            data_id = hashlib.md5((title + link).encode()).hexdigest()[:16]
            rows.append({
                'data_id': f"gov_{data_id}",
                'trade_date': pd.Timestamp.now().date(),
                'data_type': 'government_policy',
                'data_subtype': query.get('type', 'policy'),
                'entity_type': 'country',
                'entity_id': 'CN',
                'entity_name': query.get('name', ''),
                'value': None,
                'value_text': title,
                'data_quality': 80,
                'source_id': query['source_key'],
                'metadata': {'url': link},
            })

        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def produced_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.GOVERNMENT_POLICY]

    def watched_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.MACRO_ECONOMIC]

    def collect_source(self, source_key: str = 'pbc') -> pd.DataFrame:
        """便捷方法：采集指定政府源"""
        return self.collect({'source': source_key})
