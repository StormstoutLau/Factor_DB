"""分位数组合分层收益测试（TDD 红阶段）

验证 FactorQuery.get_quantile_portfolio 的组内等权收益、单调性与参数校验，
以及 CLI `quantile` 命令输出。
"""

import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd

from Factor_DB.cli import main
from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from loaders.base import LoaderConfig
from loaders.daily_loader import DailyLoader
from query.factor_query import FactorQuery

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestQuantilePortfolio(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'quantile.duckdb')
        self.start = date(2024, 1, 1)
        self.end = date(2024, 1, 6)
        self._seed()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed(self):
        """因子排名与收益排名完全一致：4 股、6 日、收益单调递增"""
        dates = pd.date_range(self.start, periods=6, freq='D')
        stocks = ['000001', '000002', '600001', '600002']

        factor = pd.DataFrame(
            [[1.0, 2.0, 3.0, 4.0]] * 6, index=dates, columns=stocks
        )
        loader = DailyLoader(LoaderConfig(
            db_path=self.db_path, show_progress=False, skip_existing=False
        ))
        loader.load_factor_to_wide(factor, 'pe')
        loader.conn.close()
        DuckDBConnection._instances.clear()

        closes = {
            '000001': [10.0 * 1.01 ** i for i in range(6)],
            '000002': [20.0 * 1.02 ** i for i in range(6)],
            '600001': [30.0 * 1.03 ** i for i in range(6)],
            '600002': [40.0 * 1.04 ** i for i in range(6)],
        }
        rows = [
            (dates[i].date(), code, values[i], pd.Timestamp('2024-01-07'))
            for code, values in closes.items()
            for i in range(6)
        ]

        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).create_table('daily_prices')
        with conn as c:
            df = pd.DataFrame(  # noqa: F841 - 供 DuckDB 的 FROM df 引用
                rows, columns=['trade_date', 'stock_code', 'close', 'loaded_at']
            )
            c.execute(
                'INSERT INTO daily_prices (trade_date, stock_code, close, loaded_at) '
                'SELECT * FROM df'
            )
        conn.close()
        DuckDBConnection._instances.clear()

    # ========== API ==========

    def test_quantile_portfolio_groups_and_returns(self):
        """2 组：组序 1/2 齐全，高因子组收益更高，期数 = 5"""
        query = FactorQuery(self.db_path)
        df = query.get_quantile_portfolio(
            'pe', n_groups=2, start_date=self.start, end_date=self.end
        )
        query.conn.close()

        self.assertEqual(
            ['trade_date', 'quantile', 'avg_return', 'n_stocks'],
            list(df.columns),
        )
        self.assertEqual({1, 2}, set(df['quantile'].unique()))
        self.assertEqual(5, df['trade_date'].nunique())
        self.assertEqual(2, int(df[df['quantile'] == 1]['n_stocks'].iloc[0]))

        group_mean = df.groupby('quantile')['avg_return'].mean()
        self.assertGreater(float(group_mean.loc[2]), float(group_mean.loc[1]))

    def test_quantile_monotonicity(self):
        """组均收益随组序严格递增 → 单调性为 1"""
        query = FactorQuery(self.db_path)
        df = query.get_quantile_portfolio(
            'pe', n_groups=4, start_date=self.start, end_date=self.end
        )
        query.conn.close()

        self.assertAlmostEqual(1.0, float(df.attrs['monotonicity']), places=6)

    def test_quantile_matches_manual_avg(self):
        """组内等权收益与 pandas 独立复算一致"""
        query = FactorQuery(self.db_path)
        df = query.get_quantile_portfolio(
            'pe', n_groups=2, start_date=self.start, end_date=self.end
        )
        query.conn.close()

        first_date = df['trade_date'].min()
        group2 = df[(df['trade_date'] == first_date) & (df['quantile'] == 2)]
        # 组 2 为因子值最高的两只：600001(3%) 与 600002(4%)
        expected = (1.03 + 1.04) / 2 - 1
        self.assertAlmostEqual(expected, float(group2['avg_return'].iloc[0]), places=9)

    def test_quantile_rejects_invalid_groups(self):
        """n_groups < 2 抛 ValueError"""
        query = FactorQuery(self.db_path)
        with self.assertRaises(ValueError):
            query.get_quantile_portfolio('pe', n_groups=1)
        query.conn.close()

    # ========== CLI ==========

    def test_cli_quantile_prints_table(self):
        """CLI quantile 输出组汇总与单调性"""
        out = StringIO()
        with redirect_stdout(out):
            main([
                'quantile', '--db', self.db_path, '--factor', 'pe',
                '--groups', '2', '--start', '2024-01-01', '--end', '2024-01-06',
            ])
        output = out.getvalue()
        self.assertIn('分层收益', output)
        self.assertIn('monotonicity=', output)
        self.assertIn('quantile', output)
