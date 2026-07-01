"""
FactorQuery 宽表查询测试 — TDD 红阶段

验证 FactorQuery 在宽表架构下的查询行为：
- 最新版本查询优先走 factor_wide（性能最优）
- PIT 查询走 factor_history（兼容）
- 宽表不存在时自动回退 factor_data
"""

import os
import shutil
import tempfile
import unittest
from datetime import date

import numpy as np
import pandas as pd

from core.connection import DuckDBConnection
from loaders.daily_loader import DailyLoader
from loaders.base import LoaderConfig
from query.factor_query import FactorQuery


class TestWideFactorQuery(unittest.TestCase):
    """宽表架构下的 FactorQuery 测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_factor.duckdb')
        self.config = LoaderConfig(
            db_path=self.db_path,
            show_progress=False,
            skip_existing=False
        )
        self.loader = DailyLoader(self.config)
        self._seed_wide_data()

    def tearDown(self):
        self.loader.conn.close()
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed_wide_data(self):
        """构造测试数据：pe + pb 两个因子，5 日 × 4 股"""
        dates = pd.date_range('2024-01-01', periods=5, freq='D')
        stocks = ['000001', '000002', '600001', '600002']

        df_pe = pd.DataFrame(
            np.random.RandomState(42).randn(5, 4) * 5 + 15,
            index=dates, columns=stocks
        )
        df_pb = pd.DataFrame(
            np.random.RandomState(43).randn(5, 4) * 0.5 + 2.0,
            index=dates, columns=stocks
        )

        self.loader.load_factor_to_wide(df_pe, 'pe')
        self.loader.load_factor_to_wide(df_pb, 'pb')

        self.loader.conn.close()
        DuckDBConnection._instances.clear()

    # ========== 1. get_factor 宽表查询 ==========

    def test_get_factor_from_wide(self):
        """get_factor 应从宽表查询单因子（最新版本）"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor('pe')

        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 20)
        self.assertIn('trade_date', df.columns)
        self.assertIn('stock_code', df.columns)
        self.assertIn('factor_value', df.columns)

    def test_get_factor_with_date_range(self):
        """get_factor 支持日期范围过滤"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor('pe', start_date=date(2024, 1, 2), end_date=date(2024, 1, 4))

        self.assertEqual(len(df), 12)

    def test_get_factor_with_stock_filter(self):
        """get_factor 支持股票过滤"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor('pe', stock_codes=['000001', '600001'])

        self.assertEqual(len(df), 10)
        codes = set(df['stock_code'].unique())
        self.assertEqual(codes, {'000001', '600001'})

    # ========== 2. get_factor_matrix 宽表查询 ==========

    def test_get_factor_matrix_from_wide(self):
        """get_factor_matrix 应从宽表查询多因子"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor_matrix(['pe', 'pb'])

        self.assertIsInstance(df, pd.DataFrame)
        self.assertEqual(len(df), 20)
        self.assertIn('trade_date', df.columns)
        self.assertIn('stock_code', df.columns)
        self.assertIn('pe', df.columns)
        self.assertIn('pb', df.columns)

    def test_get_factor_matrix_pivot_format(self):
        """因子矩阵应为宽格式（每因子一列）"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor_matrix(['pe', 'pb'])

        row = df.iloc[0]
        self.assertTrue(pd.notna(row['pe']))
        self.assertTrue(pd.notna(row['pb']))

    # ========== 3. get_cross_section 截面查询 ==========

    def test_get_cross_section_from_wide(self):
        """get_cross_section 应从宽表查询截面数据"""
        query = FactorQuery(self.config.db_path)
        df = query.get_cross_section('pe', trade_date=date(2024, 1, 3))

        self.assertEqual(len(df), 4)
        self.assertIn('stock_code', df.columns)
        self.assertIn('factor_value', df.columns)

    # ========== 4. PIT 查询回退历史表 ==========

    def test_get_factor_with_pit_uses_history(self):
        """带 as_of 的 PIT 查询应走 factor_history"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor('pe', as_of=date(2099, 12, 31))

        self.assertEqual(len(df), 20)
        self.assertIn('factor_value', df.columns)

    def test_pit_reflects_history_versions(self):
        """PIT 查询应正确反映历史版本（加载两次后 as_of 取最新）"""
        from loaders.daily_loader import DailyLoader
        loader2 = DailyLoader(self.config)
        dates = pd.date_range('2024-01-01', periods=5, freq='D')
        stocks = ['000001', '000002', '600001', '600002']
        df_v2 = pd.DataFrame(
            np.ones((5, 4)) * 99.0,
            index=dates, columns=stocks
        )
        loader2.load_factor_to_wide(df_v2, 'pe')
        loader2.conn.close()
        DuckDBConnection._instances.clear()

        query = FactorQuery(self.config.db_path)
        df = query.get_factor('pe', as_of=date(2099, 12, 31))

        first_val = df[df['stock_code'] == '000001'].iloc[0]['factor_value']
        self.assertAlmostEqual(first_val, 99.0)

    # ========== 5. list_factors / 回退逻辑 ==========

    def test_list_factors_from_wide(self):
        """list_factors 应从宽表列名推断因子列表"""
        query = FactorQuery(self.config.db_path)
        factors = query.list_factors()

        self.assertIn('pe', factors)
        self.assertIn('pb', factors)

    def test_query_nonexistent_factor_returns_empty(self):
        """查询不存在的因子应返回空 DataFrame（而非报错）"""
        query = FactorQuery(self.config.db_path)
        df = query.get_factor('nonexistent_factor_xyz')

        self.assertEqual(len(df), 0)


if __name__ == '__main__':
    unittest.main()
