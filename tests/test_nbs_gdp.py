"""NBS GDP 单季同比采集器测试（P8-1c TDD）

覆盖：
- 元信息与注册发现
- URL 白名单
- 列表页分页、重复锚点去重、URL 日期排序
- 表头非首行、同比/环比表分类、空 token 跳过
- 季度映射、起点截断、跨页去重
- 时间列语义、失败隔离与恢复
- Loader 集成、幂等、历史不变与 PIT
"""

import os
import shutil
import tempfile
import unittest
from datetime import date
from unittest import mock

import pandas as pd
import requests

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from Factor_DB.loaders.base import LoaderConfig
from Factor_DB.loaders.macro_loader import MacroLoader
from Factor_DB.osint.base import CollectorConfig, OSINTEventType
from Factor_DB.osint.collectors.nbs_gdp_collector import NBSGDPCollector
from Factor_DB.osint.registry import CollectorRegistry
from Factor_DB.query.macro_query import MacroQuery

LISTING_URL = 'https://www.stats.gov.cn/sj/zxfb/'
PAGE2_URL = 'https://www.stats.gov.cn/sj/zxfb/index_2.html'
PAGE3_URL = 'https://www.stats.gov.cn/sj/zxfb/index_3.html'
Q3_URL = 'https://www.stats.gov.cn/sj/zxfb/202610/t20261020_1965658.html'
Q2_URL = 'https://www.stats.gov.cn/sj/zxfb/202607/t20260716_1964142.html'

Q3_HREF = './202610/t20261020_1965658.html'
Q2_HREF = './202607/t20260716_1964142.html'
NON_GDP_HREF = './202610/t20261015_1111111.html'

EMPTY_LISTING = '<html><body></body></html>'


class _FakeResponse:

    def __init__(self, text):
        self.text = text
        self.encoding = 'utf-8'


def _listing_html(*links):
    anchors = ''.join(f'<a href="{href}">{title}</a>' for href, title in links)
    return f'<html><body>{anchors}</body></html>'


LISTING_PAGES = {
    LISTING_URL: _listing_html(
        (NON_GDP_HREF, '2026年9月份居民消费价格变动情况'),
        (Q3_HREF, '2026年三季度国内生产总值初步核算结果'),
        (Q3_HREF, '2026年三季度国内生产总值初步核算结果'),
        (Q3_HREF, '2026年三季度国内生产总值初步核算结果'),
    ),
    PAGE2_URL: _listing_html(
        (NON_GDP_HREF, '2026年9月份工业生产者出厂价格变动情况'),
    ),
    PAGE3_URL: _listing_html(
        (Q2_HREF, '2026年二季度和上半年国内生产总值初步核算结果'),
        (Q2_HREF, '2026年二季度和上半年国内生产总值初步核算结果'),
    ),
}


def _yoy_table(q3_2025='4.8', q3_2026='4.5'):
    rows = [
        ('2020', '--', '...', '—', '\u2002'),
        ('2021', '18.9', '8.1', '5.5', '4.5'),
        ('2022', '4.8', '0.8', '4.0', '3.0'),
        ('2023', '4.7', '6.5', '5.0', '5.3'),
        ('2024', '5.3', '4.7', '4.6', '5.4'),
        ('2025', '5.4', '5.2', q3_2025, '4.5'),
        ('2026', '5.0', '4.3', q3_2026, '\u2002'),
    ]
    body = ''.join(
        '<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>'
        for row in rows
    )
    return (
        '<table><tr><td>单位：%</td></tr>'
        '<tr><td>年份</td><td>1季度</td><td>2季度</td><td>3季度</td><td>4季度</td></tr>'
        f'{body}'
        '<tr><td>注：同比增长速度为与上年同期对比的增长速度。</td></tr></table>'
    )


def _qoq_table():
    rows = [
        ('2025', '1.2', '1.0', '1.1', '1.0'),
        ('2026', '1.3', '1.0', '1.1', '\u2002'),
    ]
    body = ''.join(
        '<tr>' + ''.join(f'<td>{cell}</td>' for cell in row) + '</tr>'
        for row in rows
    )
    return (
        '<table><tr><td>单位：%</td></tr>'
        '<tr><td>年份</td><td>1季度</td><td>2季度</td><td>3季度</td><td>4季度</td></tr>'
        f'{body}</table>'
    )


def _detail_html(q3_2025='4.8', q3_2026='4.5'):
    """真实页面结构：首行「单位：%」、表头第 2 行、桌面/移动双份同比表。"""
    yoy = _yoy_table(q3_2025, q3_2026)
    return f'''<html><head><meta name="PubDate" content="2026/10/20 09:30"></head><body>
<p>表1 2026年三季度GDP初步核算数据</p>
<table>
<tr><td>指标</td><td>绝对额（亿元）</td><td>比上年同期增长（%）</td></tr>
<tr><td>GDP</td><td>100</td><td>4.5</td></tr>
</table>
<p>表2GDP同比增长速度</p>
{yoy}
<p>（以下为移动版）表2GDP同比增长速度</p>
{yoy}
<p>注：同比增长速度为与上年同期对比的增长速度。</p>
<p>表3GDP环比增长速度</p>
{_qoq_table()}
</body></html>'''


def _make_collector():
    collector = NBSGDPCollector(CollectorConfig(source_id='nbs_gdp'))
    collector._error_state = False
    return collector


def _install_mock(collector, mapping):
    def _get(url, *args, **kwargs):
        return _FakeResponse(mapping.get(url, EMPTY_LISTING))

    collector.request_get = mock.Mock(side_effect=_get)
    return collector


def _detail_mapping(q3_2025='4.8', old_q3_2025='4.7'):
    mapping = dict(LISTING_PAGES)
    mapping[Q3_URL] = _detail_html(q3_2025=q3_2025)
    mapping[Q2_URL] = _detail_html(q3_2025=old_q3_2025, q3_2026='')
    return mapping


class TestNBSGDPCollector(unittest.TestCase):

    def test_meta_and_registry_discovery(self):
        self.assertEqual('nbs_gdp', NBSGDPCollector.meta['name'])
        self.assertEqual(
            [OSINTEventType.MACRO_ECONOMIC],
            NBSGDPCollector(CollectorConfig(source_id='nbs_gdp')).produced_events(),
        )
        registry = CollectorRegistry()
        registry.auto_discover()
        self.assertIn('nbs_gdp', registry.get_registered_ids())

    def test_url_allowlist(self):
        collector = _make_collector()
        self.assertTrue(collector._is_allowed_url(Q3_URL))
        self.assertTrue(collector._is_allowed_url(
            'http://www.stats.gov.cn/sj/zxfb/202610/t20261020_1965658.html'))
        self.assertFalse(collector._is_allowed_url(
            'https://evil.com/sj/zxfb/202610/t20261020_1965658.html'))
        self.assertFalse(collector._is_allowed_url(
            'https://www.stats.gov.cn/other/t20261020_1965658.html'))
        self.assertFalse(collector._is_allowed_url(Q3_URL + '?x=1'))
        self.assertFalse(collector._is_allowed_url(Q3_URL + '#frag'))
        self.assertFalse(collector._is_allowed_url('javascript:alert(1)'))

    def test_discovery_dedupes_and_sorts(self):
        collector = _install_mock(_make_collector(), dict(LISTING_PAGES))
        urls = collector._discover_detail_urls(6, 2)
        self.assertEqual([Q3_URL, Q2_URL], urls)

    def test_discovery_no_match_returns_empty(self):
        collector = _install_mock(_make_collector(), {
            LISTING_URL: _listing_html((NON_GDP_HREF, '2026年9月份居民消费价格变动情况')),
        })
        self.assertEqual([], collector.extract_data({
            'max_listing_pages': 6, 'max_detail_pages': 2,
        }))

    def test_parses_only_yoy_table(self):
        collector = _make_collector()
        records = collector._parse_detail_page(_detail_html(), Q3_URL)
        df = collector.transform_data({'start_period': date(2025, 9, 30)}, records)
        self.assertEqual(5, len(df))
        self.assertNotIn(1.2, set(df['value']))

    def test_header_row_not_first(self):
        collector = _make_collector()
        records = collector._parse_detail_page(_detail_html(), Q3_URL)
        values = {item['period_date']: item['value'] for item in records}
        self.assertEqual(4.8, values[date(2025, 9, 30)])

    def test_skips_empty_and_placeholder_cells(self):
        collector = _make_collector()
        records = collector._parse_detail_page(_detail_html(), Q3_URL)
        periods = {item['period_date'] for item in records}
        self.assertNotIn(date(2026, 12, 31), periods)
        self.assertFalse({p for p in periods if p.year == 2020})

    def test_quarter_end_mapping(self):
        collector = _make_collector()
        records = collector._parse_detail_page(_detail_html(), Q3_URL)
        values = {item['period_date']: item['value'] for item in records}
        self.assertEqual(5.4, values[date(2025, 3, 31)])
        self.assertEqual(5.2, values[date(2025, 6, 30)])
        self.assertEqual(4.8, values[date(2025, 9, 30)])
        self.assertEqual(4.5, values[date(2025, 12, 31)])

    def test_start_period_truncation(self):
        collector = _make_collector()
        records = collector._parse_detail_page(_detail_html(), Q3_URL)
        df = collector.transform_data({'start_period': date(2025, 9, 30)}, records)
        self.assertEqual(
            [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31),
             date(2026, 6, 30), date(2026, 9, 30)],
            list(df['period_date']),
        )

    def test_dedupe_across_pages_newest_wins(self):
        collector = _install_mock(_make_collector(), _detail_mapping())
        records = collector.extract_data({
            'max_listing_pages': 6, 'max_detail_pages': 2,
        })
        df = collector.transform_data({'start_period': date(2025, 9, 30)}, records)
        row = df[df['period_date'] == date(2025, 9, 30)]
        self.assertEqual(1, len(row))
        self.assertEqual(4.8, float(row['value'].iloc[0]))

    def test_temporal_columns_and_no_source_id(self):
        collector = _make_collector()
        records = collector._parse_detail_page(_detail_html(), Q3_URL)
        df = collector.transform_data({'start_period': date(2025, 9, 30)}, records)
        self.assertNotIn('source_id', df.columns)
        self.assertTrue((df['trade_date'] == df['period_date']).all())
        row = df[df['period_date'] == date(2025, 9, 30)].iloc[0]
        self.assertEqual('2025-10-17', str(row['publish_date'])[:10])
        self.assertEqual('estimated', row['publish_date_quality'])
        self.assertEqual('GDP_YEARLY', row['indicator_id'])
        self.assertEqual('raw', row['value_type'])

    def test_failure_isolation_and_recovery(self):
        collector = _make_collector()
        collector.request_get = mock.Mock(
            side_effect=requests.ConnectionError('boom'))
        self.assertTrue(collector.collect({}).empty)
        self.assertFalse(collector._error_state)

        collector = _install_mock(_make_collector(), _detail_mapping())
        with mock.patch.object(
                collector, 'transform_data', side_effect=RuntimeError('boom')):
            self.assertTrue(collector.collect({}).empty)
        self.assertFalse(collector._error_state)

        recovered = collector.collect({})
        self.assertEqual(5, len(recovered))


class _DBTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'nbs_gdp.duckdb')
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()
        DuckDBConnection._instances.clear()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _load(self, df, source_id):
        loader = MacroLoader(LoaderConfig(db_path=self.db_path))
        try:
            return loader.load(df, source_id=source_id)
        finally:
            loader.conn.close()
            DuckDBConnection._instances.clear()

    def _fetch(self, sql):
        conn = DuckDBConnection(self.db_path, read_only=True)
        try:
            with conn as c:
                return c.execute(sql).fetchdf()
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

    def _query(self, indicators, as_of=None):
        query = MacroQuery(self.db_path)
        try:
            return query.get_macro_data(indicators, as_of=as_of)
        finally:
            query.conn.close()
            DuckDBConnection._instances.clear()


class TestNBSGDPIntegration(_DBTest):

    def test_loader_integration_idempotent(self):
        collector = _install_mock(_make_collector(), _detail_mapping())
        df = collector.collect({})
        self.assertEqual(5, len(df))
        self.assertEqual(5, self._load(df, 'nbs_gdp'))

        result = self._fetch(
            "SELECT period_date, value, publish_date, publish_date_quality, source_id "
            "FROM macro_data WHERE indicator_id='GDP_YEARLY' ORDER BY period_date"
        )
        self.assertEqual(
            ['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30'],
            [str(v)[:10] for v in result['period_date']],
        )
        self.assertEqual([4.8, 4.5, 5.0, 4.3, 4.5],
                         [float(v) for v in result['value']])
        self.assertTrue((result['source_id'] == 'nbs_gdp').all())

        self._load(df, 'nbs_gdp')
        count = self._fetch(
            "SELECT COUNT(*) AS n FROM macro_data WHERE indicator_id='GDP_YEARLY'"
        )['n'].iloc[0]
        self.assertEqual(5, int(count))

    def test_history_invariant_and_pit(self):
        history = pd.DataFrame([
            {'trade_date': date(2025, 1, 17), 'period_date': date(2024, 12, 31),
             'publish_date': date(2025, 1, 17), 'publish_date_quality': 'exact',
             'indicator_id': 'GDP_YEARLY', 'indicator_name': 'GDP_YEARLY',
             'value': 5.4, 'value_type': 'raw', 'data_quality': 100},
            {'trade_date': date(2025, 4, 16), 'period_date': date(2025, 3, 31),
             'publish_date': date(2025, 4, 16), 'publish_date_quality': 'exact',
             'indicator_id': 'GDP_YEARLY', 'indicator_name': 'GDP_YEARLY',
             'value': 5.4, 'value_type': 'raw', 'data_quality': 100},
            {'trade_date': date(2025, 7, 15), 'period_date': date(2025, 6, 30),
             'publish_date': date(2025, 7, 15), 'publish_date_quality': 'exact',
             'indicator_id': 'GDP_YEARLY', 'indicator_name': 'GDP_YEARLY',
             'value': 5.2, 'value_type': 'raw', 'data_quality': 100},
        ])
        self._load(history, 'akshare_macro')
        snapshot_sql = (
            "SELECT trade_date, period_date, publish_date, publish_date_quality, value "
            "FROM macro_data WHERE indicator_id='GDP_YEARLY' "
            "AND period_date <= DATE '2025-06-30' ORDER BY period_date"
        )
        before = self._fetch(snapshot_sql)

        collector = _install_mock(_make_collector(), _detail_mapping())
        self._load(collector.collect({}), 'nbs_gdp')
        after = self._fetch(snapshot_sql)
        pd.testing.assert_frame_equal(before, after)

        total = self._fetch(
            "SELECT COUNT(*) AS n FROM macro_data WHERE indicator_id='GDP_YEARLY'"
        )['n'].iloc[0]
        self.assertEqual(8, int(total))

        early = self._query(['GDP_YEARLY'], as_of=date(2025, 10, 16))
        self.assertEqual('2025-06-30', str(early['trade_date'].max())[:10])
        late = self._query(['GDP_YEARLY'], as_of=date(2025, 10, 18))
        self.assertEqual('2025-09-30', str(late['trade_date'].max())[:10])
        q4_early = self._query(['GDP_YEARLY'], as_of=date(2026, 1, 16))
        self.assertEqual('2025-09-30', str(q4_early['trade_date'].max())[:10])
        q4_late = self._query(['GDP_YEARLY'], as_of=date(2026, 1, 18))
        self.assertEqual('2025-12-31', str(q4_late['trade_date'].max())[:10])


if __name__ == '__main__':
    unittest.main()
