"""
RSS 新闻收集器

从财经新闻 RSS 源采集舆情数据。
依赖: pip install feedparser
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, CollectorConfig, OSINTEventType

logger = logging.getLogger(__name__)


class NewsRSSCollector(BaseCollector):
    """RSS 新闻舆情收集器"""

    meta = {
        'name': 'news_rss',
        'summary': 'RSS 财经新闻舆情采集',
        'categories': ['news', 'sentiment'],
        'required_keys': [],
    }

    DEFAULT_FEEDS = {
        'reuters_business': 'https://feeds.reuters.com/reuters/businessNews',
        'reuters_markets': 'https://feeds.reuters.com/reuters/marketsNews',
        'cnbc_top': 'https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147',
    }

    def setup(self):
        try:
            import feedparser
            self._feedparser = feedparser
        except ImportError:
            logger.error("feedparser 未安装，请运行: pip install feedparser")
            self._error_state = True
        self.feeds = self.DEFAULT_FEEDS.copy()

    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        feed_url = params.get('feed_url')
        feed_name = params.get('feed_name', 'custom')
        if feed_url:
            return {'feed_url': feed_url, 'feed_name': feed_name}

        feed_key = params.get('feed_key', 'reuters_business')
        if feed_key in self.feeds:
            return {'feed_url': self.feeds[feed_key], 'feed_name': feed_key}

        return {'feed_url': list(self.feeds.values())[0], 'feed_name': list(self.feeds.keys())[0]}

    def extract_data(self, query: Dict[str, Any]) -> Any:
        if self._error_state:
            return None
        feed = self._feedparser.parse(query['feed_url'])
        return feed

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        if raw_data is None or not hasattr(raw_data, 'entries') or not raw_data.entries:
            return pd.DataFrame()

        rows = []
        for entry in raw_data.entries:
            title = getattr(entry, 'title', '') or ''
            link = getattr(entry, 'link', '') or ''
            summary = getattr(entry, 'summary', '') or ''

            news_id = hashlib.md5(
                (title + link).encode()
            ).hexdigest()[:16]

            publish_date_parsed = getattr(entry, 'published_parsed', None)
            if publish_date_parsed:
                pub_date = date(*publish_date_parsed[:3])
            else:
                pub_date = date.today()

            rows.append({
                'news_id': f"rss_{news_id}",
                'publish_date': pub_date,
                'title': title,
                'content': summary,
                'source_id': query['feed_name'],
                'related_stocks': [],
                'related_industries': [],
                'sentiment_score': None,
                'sentiment_label': 'neutral',
            })

        return pd.DataFrame(rows)

    def produced_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.NEWS_SENTIMENT]

    def watched_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.MACRO_ECONOMIC]

    def handle_event(self, event) -> None:
        """当宏观事件发生时，触发相关新闻采集"""
        if event.event_type == OSINTEventType.MACRO_ECONOMIC:
            logger.info(f"宏观事件触发新闻采集: {event.source_module}")

    def collect_feed(self, feed_key: str = 'reuters_business') -> pd.DataFrame:
        """便捷方法：采集指定 RSS 源"""
        return self.collect({'feed_key': feed_key})

    def collect_all_feeds(self) -> pd.DataFrame:
        """便捷方法：采集所有 RSS 源"""
        all_dfs = []
        for feed_key in self.feeds:
            try:
                df = self.collect_feed(feed_key)
                if not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                logger.warning(f"RSS 源 {feed_key} 采集失败: {e}")
        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()
