"""数据源接入（P3）测试（TDD 红阶段）

覆盖：
- CollectorRegistry.auto_discover 在包模式下能发现 4 个内置采集器
- AKShareMacroCollector 的指标函数候选回退与缺失降级
- CLI `collect` 的列表、参数校验与运行输出
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from Factor_DB.cli import main
from Factor_DB.core.schema import SchemaManager
from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.osint.collectors.akshare_collector import AKShareMacroCollector
from Factor_DB.osint.registry import CollectorRegistry


class FakeAK:
    """只暴露备选函数名的假 AKShare 模块"""

    @staticmethod
    def macro_china_money_supply():
        return pd.DataFrame({'日期': ['2024-01-01'], '货币供应量': [100.0]})


class EmptyAK:
    """不暴露任何宏观函数的假 AKShare 模块"""


class _FakeRegistry:

    def __init__(self, ids):
        self._ids = ids

    def get_registered_ids(self):
        return list(self._ids)


class _FakePipeline:
    """CLI 运行路径用的假管道（避免触网）"""

    def __init__(self, db_path):
        self.db_path = db_path
        self.registry = _FakeRegistry(['dummy'])
        self.metadata = SimpleNamespace(conn=SimpleNamespace(close=lambda: None))

    def auto_discover(self):
        return 1

    def run(self, source_ids, params=None, config=None):
        return {sid: 1 for sid in source_ids}


class TestCollectorDiscovery(unittest.TestCase):

    def test_auto_discover_finds_packaged_collectors(self):
        """包模式下自动发现应注册 4 个内置采集器"""
        registry = CollectorRegistry()
        registry.auto_discover()
        ids = set(registry.get_registered_ids())
        expected = {'akshare_macro', 'worldbank', 'news_rss', 'government'}
        self.assertTrue(expected.issubset(ids), f'实际发现: {ids}')


class TestAKShareResolution(unittest.TestCase):

    def test_resolves_alias_when_primary_missing(self):
        """主函数名缺失时应回退到候选名"""
        collector = AKShareMacroCollector()
        collector._ak = FakeAK()
        collector._error_state = False
        df = collector.collect({'indicator': 'M2_MONTHLY'})
        self.assertFalse(df.empty)
        self.assertIn('indicator_id', df.columns)

    def test_missing_all_candidates_returns_empty(self):
        """所有候选都缺失时返回空表且不抛异常"""
        collector = AKShareMacroCollector()
        collector._ak = EmptyAK()
        collector._error_state = False
        df = collector.collect({'indicator': 'M2_MONTHLY'})
        self.assertTrue(df.empty)


class TestCollectCLI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'collect.duckdb')
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()
        DuckDBConnection._instances.clear()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(argv)
        return out.getvalue(), code

    def test_list_prints_registered_collectors(self):
        output, code = self._run(['collect', '--list'])
        self.assertEqual(0, code)
        self.assertIn('akshare_macro', output)

    def test_missing_source_returns_2(self):
        output, code = self._run(['collect'])
        self.assertEqual(2, code)
        self.assertIn('--source', output)

    def test_unknown_source_returns_2(self):
        output, code = self._run(['collect', '--source', 'not_exists'])
        self.assertEqual(2, code)
        self.assertIn('未知采集器', output)

    def test_runs_registered_source(self):
        with mock.patch('Factor_DB.osint.pipeline.OSINTPipeline', _FakePipeline):
            output, code = self._run([
                'collect', '--source', 'dummy',
                '--db', self.db_path, '--indicator', 'GDP_YEARLY',
            ])
        self.assertEqual(0, code)
        self.assertIn('dummy', output)
        self.assertIn('1 条', output)


class NormalizationAK:
    """覆盖 6 种中文日期/多列取值的假 AKShare 模块"""

    @staticmethod
    def macro_china_cpi_monthly():
        return pd.DataFrame([{
            '商品': '中国CPI月率报告',
            '日期': '2026-09-10',
            '今值': 0.4,
            '预测值': 0.5,
            '前值': 0.1,
        }])

    @staticmethod
    def macro_china_pmi():
        return pd.DataFrame([{
            '月份': '2026年10月份',
            '制造业-指数': 49.3,
            '制造业-同比增长': 0.612,
            '非制造业-指数': 49.7,
            '非制造业-同比增长': -0.798,
        }])

    @staticmethod
    def macro_china_gdp():
        return pd.DataFrame([{
            '季度': '2026年第1-3季度',
            '国内生产总值-绝对值': 1071071.2,
            '国内生产总值-同比增长': 4.6,
        }])

    @staticmethod
    def macro_china_money_supply():
        return pd.DataFrame([{
            '月份': '2026年09月份',
            '货币和准货币(M2)-数量(亿元)': 3596289.70,
            '货币和准货币(M2)-同比增长': 7.2,
        }])

    @staticmethod
    def macro_china_fx_gold():
        return pd.DataFrame([{
            '月份': '2026年09月份',
            '黄金储备-数值': 3235.17,
            '国家外汇储备-数值': 34002.51,
        }])

    @staticmethod
    def macro_china_hgjck():
        return pd.DataFrame([{
            '月份': '2026年09月份',
            '当月出口额-金额': 447184300.0,
            '当月进口额-金额': 313652200.0,
        }])


class InvalidDateAK:
    """包含一条非法日期的假 AKShare 模块"""

    @staticmethod
    def macro_china_cpi_monthly():
        return pd.DataFrame([
            {'商品': 'CPI', '日期': '2026-09-10', '今值': 0.4, '预测值': 0.5, '前值': 0.1},
            {'商品': 'CPI', '日期': 'not-a-date', '今值': 9.9, '预测值': 1.0, '前值': 0.0},
        ])


class TestMacroNormalization(unittest.TestCase):
    """P6：中文日期解析与取值列选择"""

    def _collect(self, indicator, ak=None):
        collector = AKShareMacroCollector()
        collector._ak = ak or NormalizationAK()
        collector._error_state = False
        return collector.collect({'indicator': indicator})

    def test_parse_chinese_month(self):
        parse = AKShareMacroCollector._parse_trade_date
        self.assertEqual(date(2026, 9, 1), parse('2026年09月份'))
        self.assertEqual(date(2026, 9, 1), parse('2026年9月份'))
        self.assertEqual(date(2008, 1, 1), parse('2008年01月份'))

    def test_parse_chinese_quarter(self):
        parse = AKShareMacroCollector._parse_trade_date
        self.assertEqual(date(2026, 3, 31), parse('2026年第1季度'))
        self.assertEqual(date(2026, 9, 30), parse('2026年第1-3季度'))
        self.assertEqual(date(2025, 12, 31), parse('2025年第4季度'))

    def test_parse_standard_and_invalid(self):
        parse = AKShareMacroCollector._parse_trade_date
        self.assertEqual(date(2026, 9, 10), parse('2026-09-10'))
        self.assertIsNone(parse('not-a-date'))

    def test_invalid_dates_are_dropped(self):
        df = self._collect('CPI_MONTHLY', ak=InvalidDateAK())
        self.assertEqual(1, len(df))
        self.assertEqual(date(2026, 9, 10), df['trade_date'].iloc[0])
        self.assertAlmostEqual(0.4, float(df['value'].iloc[0]), places=9)

    def test_cpi_prefers_current_value(self):
        df = self._collect('CPI_MONTHLY')
        self.assertEqual(1, len(df))
        self.assertAlmostEqual(0.4, float(df['value'].iloc[0]), places=9)
        self.assertEqual(date(2026, 9, 10), df['trade_date'].iloc[0])

    def test_pmi_uses_manufacturing_index(self):
        df = self._collect('PMI_MANUFACTURING')
        self.assertEqual(1, len(df))
        self.assertAlmostEqual(49.3, float(df['value'].iloc[0]), places=9)
        self.assertEqual(date(2026, 10, 1), df['trade_date'].iloc[0])

    def test_gdp_uses_absolute_value(self):
        df = self._collect('GDP_QUARTERLY')
        self.assertEqual(1, len(df))
        self.assertAlmostEqual(1071071.2, float(df['value'].iloc[0]), places=6)
        self.assertEqual(date(2026, 9, 30), df['trade_date'].iloc[0])

    def test_m2_uses_m2_quantity(self):
        df = self._collect('M2_MONTHLY')
        self.assertEqual(1, len(df))
        self.assertAlmostEqual(3596289.70, float(df['value'].iloc[0]), places=4)
        self.assertEqual(date(2026, 9, 1), df['trade_date'].iloc[0])

    def test_fx_uses_forex_reserves(self):
        df = self._collect('FX_RESERVES')
        self.assertEqual(1, len(df))
        self.assertAlmostEqual(34002.51, float(df['value'].iloc[0]), places=4)
        self.assertEqual(date(2026, 9, 1), df['trade_date'].iloc[0])

    def test_import_export_uses_export_amount(self):
        df = self._collect('IMPORT_EXPORT')
        self.assertEqual(1, len(df))
        self.assertAlmostEqual(447184300.0, float(df['value'].iloc[0]), places=2)
        self.assertEqual(date(2026, 9, 1), df['trade_date'].iloc[0])

    def test_unknown_indicator_falls_back_to_first_value_column(self):
        col = AKShareMacroCollector._pick_value_column(
            ['日期', '数值', '同比'], 'UNKNOWN'
        )
        self.assertEqual('数值', col)


if __name__ == '__main__':
    unittest.main()
