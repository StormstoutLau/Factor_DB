"""行业收益率入库（P4）测试（TDD 红阶段）

覆盖：
- `_detect_data_type` 的 alternative 前置路由与既有路由回归（含 government 修正）
- `SWIndustryCollector` 的列契约、收益率计算、首行丢弃、容错、级别/日期参数
- 采集器自动发现与 CLI `collect --source sw_industry`
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from Factor_DB.cli import main
from Factor_DB.osint.base import CollectorConfig
from Factor_DB.osint.pipeline import OSINTPipeline
from Factor_DB.osint.registry import CollectorRegistry

EXPECTED_COLUMNS = [
    'trade_date', 'data_type', 'data_subtype', 'entity_type', 'entity_id',
    'entity_name', 'value', 'data_quality', 'source_id',
]

INFO_ROWS = pd.DataFrame({
    '行业代码': ['801010.SI', '801030.SI'],
    '行业名称': ['农林牧渔', '基础化工'],
    '成份个数': [104, 413],
})


def _closes(symbol):
    return {
        '801010': [100.0, 101.0, 102.01],
        '801030': [200.0, 198.0, 200.0],
    }[symbol]


class FakeAK:
    """假 AKShare：申万分类 + 指数日线（不触网）"""

    def __init__(self, fail_symbols=()):
        self.fail_symbols = set(fail_symbols)

    def sw_index_first_info(self):
        return INFO_ROWS.copy()

    def sw_index_second_info(self):
        return INFO_ROWS.copy()

    def sw_index_third_info(self):
        return INFO_ROWS.copy()

    def index_hist_sw(self, symbol, period='day'):
        if symbol in self.fail_symbols:
            raise ValueError(f'faked failure: {symbol}')
        return pd.DataFrame({
            '代码': [symbol] * 3,
            '日期': ['2024-01-02', '2024-01-03', '2024-01-04'],
            '收盘': _closes(symbol),
            '开盘': _closes(symbol),
            '最高': _closes(symbol),
            '最低': _closes(symbol),
            '成交量': [1.0, 1.0, 1.0],
            '成交额': [1.0, 1.0, 1.0],
        })


def _make_collector(fail_symbols=()):
    from Factor_DB.osint.collectors.sw_industry_collector import SWIndustryCollector

    collector = SWIndustryCollector(
        CollectorConfig(source_id='sw_industry', rate_limit=0)
    )
    collector._ak = FakeAK(fail_symbols=fail_symbols)
    collector._error_state = False
    return collector


class TestRouting(unittest.TestCase):
    """`_detect_data_type` 路由"""

    def setUp(self):
        self.pipeline = OSINTPipeline.__new__(OSINTPipeline)

    def _route(self, columns):
        df = pd.DataFrame({col: [None] for col in columns})
        return self.pipeline._detect_data_type(df, 'test')

    def test_routing_entity_columns_to_alternative(self):
        """含 entity 列的行业收益数据应路由到 alternative"""
        route = self._route([
            'trade_date', 'data_type', 'data_subtype', 'entity_type',
            'entity_id', 'entity_name', 'value',
        ])
        self.assertEqual('alternative', route)

    def test_routing_regression(self):
        """3 条既有路由不变，government 由 macro 修正为 alternative"""
        self.assertEqual('macro', self._route(['trade_date', 'indicator_id', 'value']))
        self.assertEqual('macro', self._route(['trade_date', 'indicator', 'value']))
        self.assertEqual('news', self._route(['publish_date', 'title', 'sentiment_score']))
        self.assertEqual('report', self._route(['report_id', 'analyst', 'brokerage']))
        self.assertEqual('macro', self._route(['trade_date', 'value']))
        self.assertEqual('alternative', self._route([
            'data_id', 'trade_date', 'data_type', 'data_subtype', 'entity_type',
            'entity_id', 'entity_name', 'value', 'value_text', 'source_id',
        ]))


class TestSWIndustryTransform(unittest.TestCase):

    def test_transform_produces_standard_columns(self):
        """输出 9 个标准列，收益率等于手算 pct_change"""
        collector = _make_collector()
        df = collector.collect({'level': 'first'})

        self.assertFalse(df.empty)
        self.assertEqual(EXPECTED_COLUMNS, list(df.columns))
        self.assertEqual(4, len(df))
        self.assertEqual({'801010', '801030'}, set(df['entity_id']))
        self.assertEqual({'industry_return'}, set(df['data_type']))
        self.assertEqual({'first'}, set(df['data_subtype']))
        self.assertEqual({'industry'}, set(df['entity_type']))
        self.assertEqual({'sw_industry'}, set(df['source_id']))

        first = df[(df['entity_id'] == '801010') & (df['trade_date'].astype(str) == '2024-01-03')]
        self.assertEqual(1, len(first))
        self.assertAlmostEqual(0.01, float(first.iloc[0]['value']), places=6)

    def test_first_row_nan_dropped(self):
        """每个指数的首行（无前收盘）不入结果"""
        df = _make_collector().collect({'level': 'first'})
        self.assertFalse(df['value'].isna().any())
        for entity, group in df.groupby('entity_id'):
            self.assertEqual('2024-01-03', str(group['trade_date'].min()), entity)
        self.assertEqual(4, len(df))

    def test_partial_failure_tolerated(self):
        """单个指数失败不影响其他指数"""
        df = _make_collector(fail_symbols={'801030'}).collect({'level': 'first'})
        self.assertFalse(df.empty)
        self.assertEqual({'801010'}, set(df['entity_id']))
        self.assertEqual(2, len(df))

    def test_all_failure_returns_empty(self):
        """全部指数失败时返回空表且不抛异常"""
        df = _make_collector(fail_symbols={'801010', '801030'}).collect({'level': 'first'})
        self.assertTrue(df.empty)

    def test_level_default_and_alias(self):
        """level 默认 first，--indicator 可作别名，非法值回退 first"""
        collector = _make_collector()
        self.assertEqual('first', collector.transform_query({})['level'])
        self.assertEqual('second', collector.transform_query({'indicator': 'second'})['level'])
        self.assertEqual('third', collector.transform_query({'level': 'third'})['level'])
        self.assertEqual('first', collector.transform_query({'indicator': 'bogus'})['level'])

    def test_start_date_filter_after_returns(self):
        """start_date 在收益计算之后过滤，首日收益不丢"""
        collector = _make_collector()
        df = collector.collect({'level': 'first', 'start_date': '2024-01-03'})

        self.assertFalse(df.empty)
        self.assertEqual('2024-01-03', str(df['trade_date'].min()))
        self.assertEqual(4, len(df))
        head = df[(df['entity_id'] == '801010') & (df['trade_date'].astype(str) == '2024-01-03')]
        self.assertAlmostEqual(0.01, float(head.iloc[0]['value']), places=6)


class TestDiscovery(unittest.TestCase):

    def test_auto_discover_finds_sw_industry(self):
        """自动发现应注册 ≥5 个采集器且含 sw_industry"""
        registry = CollectorRegistry()
        registry.auto_discover()
        ids = set(registry.get_registered_ids())
        self.assertGreaterEqual(len(ids), 5)
        self.assertIn('sw_industry', ids)


class _FakeRegistry:

    def get_registered_ids(self):
        return ['sw_industry']


class _FakePipeline:

    def __init__(self, db_path):
        self.registry = _FakeRegistry()
        self.metadata = SimpleNamespace(conn=SimpleNamespace(close=lambda: None))

    def auto_discover(self):
        return 5

    def run(self, source_ids, params=None, config=None):
        return {sid: 1 for sid in source_ids}


class TestCollectCLI(unittest.TestCase):

    def test_cli_collect_sw_industry(self):
        """CLI collect 可运行 sw_industry 并输出条数"""
        db_path = os.path.join(tempfile.gettempdir(), 'sw_industry_cli.duckdb')
        out = io.StringIO()
        with mock.patch('Factor_DB.osint.pipeline.OSINTPipeline', _FakePipeline):
            with redirect_stdout(out):
                code = main(['collect', '--source', 'sw_industry', '--db', db_path])
        self.assertEqual(0, code)
        self.assertIn('sw_industry', out.getvalue())
        self.assertIn('1 条', out.getvalue())


if __name__ == '__main__':
    unittest.main()
