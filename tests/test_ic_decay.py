"""IC 衰减分析测试（TDD 红阶段）

验证 FactorQuery.get_ic_decay 在不同前向收益期数下的 IC 均值曲线，
以及 CLI `ic-decay` 命令输出。
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


class TestICDecay(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'ic_decay.duckdb')
        self.start = date(2024, 1, 1)
        self.end = date(2024, 1, 6)
        self._seed()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed(self):
        """构造因子排名与收益排名完全一致的数据（IC 恒为 1）"""
        dates = pd.date_range(self.start, periods=6, freq='D')
        stocks = ['000001', '000002', '600001', '600002']

        # 因子值在各股票上恒定，排名 A<B<C<D
        factor = pd.DataFrame(
            [[1.0, 2.0, 3.0, 4.0]] * 6, index=dates, columns=stocks
        )
        loader = DailyLoader(LoaderConfig(
            db_path=self.db_path, show_progress=False, skip_existing=False
        ))
        loader.load_factor_to_wide(factor, 'pe')
        loader.conn.close()
        DuckDBConnection._instances.clear()

        # 收益率按股票递增（A 1% < B 2% < C 3% < D 4%），排名与因子一致
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

    def test_ic_decay_returns_horizon_curve(self):
        """返回每个前向期数一行，且期数随 horizon 递减"""
        query = FactorQuery(self.db_path)
        df = query.get_ic_decay('pe', periods=(1, 2, 3), start_date=self.start, end_date=self.end)

        self.assertEqual(
            list(df.columns),
            ['forward_return_days', 'periods', 'ic_mean', 'ic_std', 'ic_ir'],
        )
        self.assertEqual(df['forward_return_days'].tolist(), [1, 2, 3])
        self.assertEqual(df['periods'].tolist(), [5, 4, 3])
        self.assertTrue((df['ic_mean'] > 0.999).all())

    def test_ic_decay_matches_ic_series(self):
        """horizon=1 的 IC 均值应与 get_ic_series 一致"""
        query = FactorQuery(self.db_path)
        decay = query.get_ic_decay('pe', periods=(1,), start_date=self.start, end_date=self.end)
        direct = query.get_ic_series('pe', 1, self.start, self.end)

        self.assertAlmostEqual(
            float(decay['ic_mean'].iloc[0]), float(direct['ic'].mean()), places=9
        )
        self.assertEqual(int(decay['periods'].iloc[0]), len(direct))

    def test_ic_decay_rejects_empty_periods(self):
        """periods 为空或全部非法时抛 ValueError"""
        query = FactorQuery(self.db_path)
        with self.assertRaises(ValueError):
            query.get_ic_decay('pe', periods=(), start_date=self.start, end_date=self.end)
        with self.assertRaises(ValueError):
            query.get_ic_decay('pe', periods=(0, -1), start_date=self.start, end_date=self.end)

    # ========== CLI ==========

    def test_cli_ic_decay_prints_table(self):
        """CLI ic-decay 输出包含列名与因子名"""
        out = StringIO()
        with redirect_stdout(out):
            main([
                'ic-decay', '--db', self.db_path, '--factor', 'pe',
                '--days', '1,2', '--start', '2024-01-01', '--end', '2024-01-06',
            ])
        output = out.getvalue()
        self.assertIn('forward_return_days', output)
        self.assertIn('ic_mean', output)


if __name__ == '__main__':
    unittest.main()
