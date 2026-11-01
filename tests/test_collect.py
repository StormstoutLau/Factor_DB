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


if __name__ == '__main__':
    unittest.main()
