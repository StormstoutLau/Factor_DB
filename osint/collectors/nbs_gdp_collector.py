"""国家统计局 GDP 初步核算结果采集器（P8-1c）

从 NBS 发布列表发现「国内生产总值初步核算结果」新闻稿，解析「GDP同比增长速度」
表，追加 ``GDP_YEARLY`` 单季同比新期（2025Q3 起）。历史仍由 ``akshare_macro``
保留，本采集器只负责尾部新期。

设计见 ``docs/design/gdp_yearly_source_design.md``，实施见
``docs/implementation/gdp_yearly_source_implementation.md``。
"""

from __future__ import annotations

import logging
import re
from calendar import monthrange
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import pandas as pd

from Factor_DB.core.macro_temporal import estimate_publish_date

from ..base import BaseCollector, OSINTEventType

logger = logging.getLogger(__name__)


class NBSGDPCollector(BaseCollector):
    """国家统计局 GDP 初步核算结果采集器（单季同比）"""

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
    URL_DATE_RE = re.compile(r't(\d{8})_')
    YEAR_RE = re.compile(r'20\d{2}')

    YOY_CONTEXT = 'GDP同比增长速度'
    QOQ_CONTEXT = 'GDP环比增长速度'
    QUARTER_HEADERS = ('1季度', '2季度', '3季度', '4季度')
    EMPTY_TOKENS = {'', '--', '...', '…', '-', '—'}

    ALLOWED_HOST = 'www.stats.gov.cn'
    ALLOWED_PATH_PREFIX = '/sj/'
    CONTEXT_LIMIT = 900
    CONTEXT_NODES = 40

    def setup(self):
        self.listing_url = self.LISTING_URL

    # ------------------------------------------------------------------
    # 查询参数
    # ------------------------------------------------------------------

    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        start_period = params.get('start_period', self.START_PERIOD)
        if isinstance(start_period, str):
            try:
                start_period = date.fromisoformat(start_period)
            except ValueError:
                logger.warning(f"start_period 无法解析: {start_period!r}，回退默认")
                start_period = self.START_PERIOD
        elif not isinstance(start_period, date):
            logger.warning(f"start_period 类型非法: {start_period!r}，回退默认")
            start_period = self.START_PERIOD

        return {
            'start_period': start_period,
            'max_listing_pages': self._positive_int(
                params.get('max_listing_pages'), self.MAX_LISTING_PAGES,
                'max_listing_pages'),
            'max_detail_pages': self._positive_int(
                params.get('max_detail_pages'), self.MAX_DETAIL_PAGES,
                'max_detail_pages'),
        }

    @staticmethod
    def _positive_int(value: Any, default: int, name: str) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            logger.warning(f"{name} 非整数: {value!r}，回退 {default}")
            return default
        if parsed <= 0:
            logger.warning(f"{name} 必须为正整数: {value!r}，回退 {default}")
            return default
        return parsed

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------

    @classmethod
    def _is_allowed_url(cls, url: str) -> bool:
        """只允许 NBS 官方 host + /sj/ 路径，且无 query/fragment"""
        try:
            parts = urlsplit(url)
            hostname = parts.hostname
        except ValueError:
            return False
        return (
            parts.scheme in ('http', 'https')
            and hostname == cls.ALLOWED_HOST
            and parts.path.startswith(cls.ALLOWED_PATH_PREFIX)
            and not parts.query
            and not parts.fragment
        )

    @classmethod
    def _url_date(cls, url: str) -> Optional[date]:
        match = cls.URL_DATE_RE.search(url)
        if not match:
            return None
        token = match.group(1)
        try:
            return date(int(token[:4]), int(token[4:6]), int(token[6:8]))
        except ValueError:
            return None

    def _fetch_text(self, url: str) -> str:
        response = self.request_get(url)
        response.encoding = 'utf-8'
        return response.text

    def _extract_links(self, html: str, page_url: str) -> List[str]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        urls: List[str] = []
        for anchor in soup.find_all('a', href=True):
            title = anchor.get_text(' ', strip=True)
            if not self.TITLE_RE.search(title):
                continue
            url = urljoin(page_url, anchor['href'])
            if self._is_allowed_url(url):
                urls.append(url)
        return urls

    def _discover_detail_urls(self, max_listing_pages: int,
                              max_detail_pages: int) -> List[str]:
        urls: List[str] = []
        seen = set()
        for page_index in range(max_listing_pages):
            page_url = (
                self.LISTING_URL if page_index == 0
                else urljoin(self.LISTING_URL, f'index_{page_index + 1}.html')
            )
            try:
                html = self._fetch_text(page_url)
            except Exception as e:
                logger.warning(f"NBS 列表页抓取失败 {page_url}: {e}")
                continue

            for url in self._extract_links(html, page_url):
                if url in seen:
                    continue
                seen.add(url)
                urls.append(url)

            if len(urls) >= max_detail_pages:
                break

        urls.sort(key=lambda item: self._url_date(item) or date.min,
                  reverse=True)
        return urls[:max_detail_pages]

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def _parse_detail_page(self, html: str,
                           source_url: str) -> List[Dict[str, Any]]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, 'html.parser')
        records: List[Dict[str, Any]] = []
        for table in soup.find_all('table'):
            rows = table.find_all('tr')
            header_index = self._find_header_row(rows[:3])
            if header_index is None:
                continue
            if not self._is_yoy_table(table):
                continue
            records.extend(self._parse_rows(rows[header_index + 1:], source_url))
        return records

    @classmethod
    def _find_header_row(cls, rows: List[Any]) -> Optional[int]:
        for index, row in enumerate(rows):
            cells = [
                cell.get_text(strip=True)
                for cell in row.find_all(['th', 'td'])
            ]
            if (len(cells) >= 5
                    and cells[0] == '年份'
                    and tuple(cells[1:5]) == cls.QUARTER_HEADERS):
                return index
        return None

    @classmethod
    def _is_yoy_table(cls, table: Any) -> bool:
        texts = [
            str(item).strip()
            for item in table.find_all_previous(
                string=True, limit=cls.CONTEXT_NODES)
        ]
        context = ''.join(reversed(texts))[-cls.CONTEXT_LIMIT:]
        pos_yoy = context.rfind(cls.YOY_CONTEXT)
        pos_qoq = context.rfind(cls.QOQ_CONTEXT)
        return pos_yoy != -1 and pos_yoy > pos_qoq

    @classmethod
    def _parse_rows(cls, rows: List[Any],
                    source_url: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for row in rows:
            cells = [
                cell.get_text(strip=True)
                for cell in row.find_all(['th', 'td'])
            ]
            if len(cells) < 5 or not cls.YEAR_RE.fullmatch(cells[0]):
                continue

            year = int(cells[0])
            for quarter in range(1, 5):
                token = cells[quarter].replace('%', '').replace(',', '').strip()
                if token in cls.EMPTY_TOKENS:
                    continue
                try:
                    value = float(token)
                except ValueError:
                    logger.warning(
                        f"NBS GDP 单元格无法解析为数值: {token!r} "
                        f"({year}Q{quarter})"
                    )
                    continue
                records.append({
                    'period_date': cls._quarter_end(year, quarter),
                    'value': value,
                    'source_url': source_url,
                })
        return records

    @staticmethod
    def _quarter_end(year: int, quarter: int) -> date:
        month = quarter * 3
        return date(year, month, monthrange(year, month)[1])

    def extract_data(self, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        urls = self._discover_detail_urls(
            query['max_listing_pages'], query['max_detail_pages'])
        records: List[Dict[str, Any]] = []
        for url in urls:
            try:
                html = self._fetch_text(url)
                page_records = self._parse_detail_page(html, url)
                if not page_records:
                    logger.warning(f"NBS GDP 详情页未解析到季度值: {url}")
                records.extend(page_records)
            except Exception as e:
                logger.warning(f"NBS GDP 详情页处理失败 {url}: {e}")
        return records

    # ------------------------------------------------------------------
    # 标准化
    # ------------------------------------------------------------------

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        if not raw_data:
            logger.warning(
                f"NBS GDP 未发现 >= {query['start_period']} 的新期；"
                f"已解析到的最大统计期: None"
            )
            return pd.DataFrame()

        start_period = query['start_period']
        max_seen = max(item['period_date'] for item in raw_data)
        seen = set()
        rows: List[Dict[str, Any]] = []
        for item in raw_data:
            period = item['period_date']
            if period < start_period or period in seen:
                continue
            seen.add(period)
            rows.append({
                'trade_date': period,
                'period_date': period,
                'publish_date': estimate_publish_date('GDP_YEARLY', period),
                'publish_date_quality': 'estimated',
                'indicator_id': 'GDP_YEARLY',
                'indicator_name': 'GDP_YEARLY',
                'value': item['value'],
                'value_type': 'raw',
                'data_quality': 100,
            })

        if not rows:
            logger.warning(
                f"NBS GDP 未发现 >= {start_period} 的新期；"
                f"已解析到的最大统计期: {max_seen}"
            )
            return pd.DataFrame()

        return (
            pd.DataFrame(rows)
            .sort_values('period_date')
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # 失败隔离
    # ------------------------------------------------------------------

    def collect(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """失败后复位 ``_error_state``，保证下一轮可重试

        基类 ``collect`` 捕获异常会置错误态并短路后续调用；NBS 为外部页面源，
        失败不应拖累同进程其他采集器。返回基类结果（异常路径为空表）。
        """
        result = super().collect(params)
        if self._error_state:
            logger.warning('nbs_gdp 采集失败，已重置错误状态以便下次重试')
            self.reset_error()
        return result

    def produced_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.MACRO_ECONOMIC]
