"""
日 K 数据加载器测试

测试 DailyLoader 的数据加载、格式转换、验证等功能。
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from loaders.base import LoaderConfig
from loaders.daily_loader import DailyLoader


class TestDailyLoader(unittest.TestCase):
    """DailyLoader 测试类"""

    @staticmethod
    def _make_price_df(rows=3, cols=3, seed=42):
        """构造测试用宽格式日K DataFrame"""
        np.random.seed(seed)
        dates = pd.date_range('2024-01-01', periods=rows, freq='B')
        stocks = [f'{code:06d}.SZ' for code in range(1, cols + 1)]
        data = np.abs(np.random.randn(rows, cols) * 10 + 50) + 1
        return pd.DataFrame(data, index=dates, columns=stocks)

    @staticmethod
    def _make_factor_df(rows=3, cols=3, seed=99):
        """构造因子 DataFrame"""
        np.random.seed(seed)
        dates = pd.date_range('2024-01-01', periods=rows, freq='B')
        stocks = [f'{code:06d}.SZ' for code in range(1, cols + 1)]
        data = np.abs(np.random.randn(rows, cols) * 3 + 10)
        return pd.DataFrame(data, index=dates, columns=stocks)

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_daily_loader.db')
        self.data_dir = Path(self.temp_dir) / 'test_data'
        self.data_dir.mkdir()

        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()

        self.config = LoaderConfig(
            db_path=self.db_path,
            show_progress=False,
            skip_existing=False
        )

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ========== T1: 文件格式兼容性 ==========

    def test_load_pkl(self):
        df = self._make_price_df()
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_load_csv(self):
        df = self._make_price_df()
        file_path = self.data_dir / 'close.csv'
        df.to_csv(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_load_parquet(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")

        df = self._make_price_df()
        file_path = self.data_dir / 'close.parquet'
        df.to_parquet(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_load_feather(self):
        try:
            import pyarrow  # noqa: F401
        except ImportError:
            self.skipTest("pyarrow not installed")

        df = self._make_price_df()
        df = df.reset_index().rename(columns={'index': 'trade_date'})
        file_path = self.data_dir / 'close.feather'
        df.to_feather(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

    def test_unsupported_format(self):
        file_path = self.data_dir / 'data.txt'
        file_path.write_text('not a data file')

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertEqual(count, 0)

    # ========== T2: 价格字段导入 ==========

    def test_import_close(self):
        df = self._make_price_df(rows=2, cols=2, seed=1)
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            loader.load(file_path)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT trade_date, stock_code, close
                FROM daily_prices
                WHERE (trade_date, stock_code, loaded_at) IN (
                    SELECT trade_date, stock_code, MAX(loaded_at)
                    FROM daily_prices GROUP BY trade_date, stock_code
                )
                ORDER BY trade_date, stock_code
            """).fetchdf()

        self.assertEqual(len(result), 4)
        conn.close()

    def test_import_multiple_price_fields(self):
        fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
        for field in fields:
            df = self._make_price_df(rows=2, cols=2, seed=hash(field) % 100)
            file_path = self.data_dir / f'{field}.pkl'
            df.to_pickle(file_path)

            with DailyLoader(self.config) as loader:
                count = loader.load(file_path)
            self.assertGreater(count, 0)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT open, high, low, close, volume, amount
                FROM daily_prices
                WHERE (trade_date, stock_code, loaded_at) IN (
                    SELECT trade_date, stock_code, MAX(loaded_at)
                    FROM daily_prices GROUP BY trade_date, stock_code
                )
                LIMIT 1
            """).fetchdf()

        for col in fields:
            self.assertIn(col, result.columns)
        conn.close()

    # ========== T3: 因子导入 ==========

    def test_import_factor(self):
        df = self._make_factor_df(rows=2, cols=2)
        file_path = self.data_dir / 'PE.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertGreater(count, 0)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute("""
                SELECT trade_date, stock_code, factor_name, factor_value
                FROM factor_data
                WHERE (trade_date, stock_code, factor_name, loaded_at) IN (
                    SELECT trade_date, stock_code, factor_name, MAX(loaded_at)
                    FROM factor_data GROUP BY trade_date, stock_code, factor_name
                )
                AND factor_name = 'PE'
                ORDER BY trade_date, stock_code
            """).fetchdf()

        self.assertEqual(len(result), 4)
        self.assertTrue((result['factor_name'] == 'PE').all())
        conn.close()

    # ========== T4: 目录加载 ==========

    def test_load_directory(self):
        for field in ['close', 'open', 'PE', 'PB']:
            df = self._make_price_df(rows=2, cols=2, seed=hash(field) % 100)
            df.to_pickle(self.data_dir / f'{field}.pkl')

        with DailyLoader(self.config) as loader:
            total = loader.load(self.data_dir)

        self.assertGreater(total, 0)

    def test_load_empty_directory(self):
        empty_dir = self.data_dir / 'empty'
        empty_dir.mkdir()

        with DailyLoader(self.config) as loader:
            count = loader.load(empty_dir)

        self.assertEqual(count, 0)

    # ========== T5: 数据验证 ==========

    def test_validate_empty_df(self):
        loader = DailyLoader(self.config)
        df = pd.DataFrame()
        self.assertFalse(loader.validate(df))

    def test_validate_no_numeric(self):
        loader = DailyLoader(self.config)
        df = pd.DataFrame({'A': ['a', 'b', 'c'], 'B': ['x', 'y', 'z']})
        self.assertFalse(loader.validate(df))

    def test_validate_normal(self):
        loader = DailyLoader(self.config)
        df = self._make_price_df()
        self.assertTrue(loader.validate(df))

    def test_validate_high_null_ratio(self):
        loader = DailyLoader(self.config)
        df = self._make_price_df(rows=10, cols=10)
        df.iloc[:, :] = np.nan
        df.iloc[0, 0] = 1.0
        self.assertTrue(loader.validate(df))

    # ========== T6: 股票信息 ==========

    def test_load_stock_info(self):
        info_df = pd.DataFrame({
            'code': ['000001', '000002'],
            'name': ['平安银行', '万科A'],
            'list_date': ['1991-04-03', '1991-01-29'],
            'industry': ['银行', '房地产'],
            'market': ['SZ', 'SZ']
        })

        file_path = self.data_dir / 'stock_info.pkl'
        info_df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load_stock_info(file_path)

        self.assertEqual(count, 2)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(
                "SELECT stock_code, stock_name FROM stock_info ORDER BY stock_code"
            ).fetchdf()

        self.assertEqual(result['stock_code'].tolist(), ['000001', '000002'])
        self.assertEqual(result['stock_name'].tolist(), ['平安银行', '万科A'])
        conn.close()

    # ========== T7: 边界与异常 ==========

    def test_nonexistent_path(self):
        with DailyLoader(self.config) as loader:
            count = loader.load(Path('/nonexistent/path'))
        self.assertEqual(count, 0)

    def test_corrupted_file(self):
        file_path = self.data_dir / 'corrupted.pkl'
        file_path.write_bytes(b'this is not a valid pickle file')

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertEqual(count, 0)

    # ========== T8: 宽到长格式转换 ==========

    def test_wide_to_long_row_count(self):
        df = self._make_price_df(rows=3, cols=3)
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertEqual(count, 9)

    def test_wide_to_long_stock_codes(self):
        df = self._make_price_df(rows=2, cols=2)
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            loader.load(file_path)

        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            codes = c.execute("""
                SELECT DISTINCT stock_code FROM daily_prices
                WHERE (trade_date, stock_code, loaded_at) IN (
                    SELECT trade_date, stock_code, MAX(loaded_at)
                    FROM daily_prices GROUP BY trade_date, stock_code
                )
                ORDER BY stock_code
            """).fetchdf()

        self.assertEqual(codes['stock_code'].tolist(), ['000001', '000002'])
        conn.close()

    def test_wide_to_long_nan_handling(self):
        df = self._make_price_df(rows=2, cols=2)
        df.iloc[0, 1] = np.nan
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            count = loader.load(file_path)

        self.assertEqual(count, 3)

    # ========== P0-1: memory_limit 配置 ==========

    def test_memory_limit_set_on_init(self):
        """测试 DailyLoader 初始化时设置 memory_limit"""
        loader = DailyLoader(self.config)
        # 直接通过底层连接验证（不通过 with，避免 __exit__ 关闭连接）
        conn = loader.conn.connect()
        try:
            ml = conn.execute(
                "SELECT current_setting('memory_limit')"
            ).fetchone()[0]
            # memory_limit 应为合理值（≤80% 物理内存，不超 20GB）
            self.assertIsNotNone(ml)
        finally:
            pass  # 不关闭连接，让 loader 管理生命周期
        loader.conn.close()

    def test_memory_limit_applied_in_import(self):
        """测试导入时 memory_limit 在 with 块内生效"""
        df = self._make_price_df(rows=2, cols=2)
        file_path = self.data_dir / 'close.pkl'
        df.to_pickle(file_path)

        with DailyLoader(self.config) as loader:
            # 在 with 块内，连接应已配置 memory_limit
            conn = loader.conn.connect()
            ml = conn.execute(
                "SELECT current_setting('memory_limit')"
            ).fetchone()[0]
            self.assertIsNotNone(ml)
            self.assertNotEqual(ml, '')  # 不应为空

            count = loader.load(file_path)
            self.assertGreater(count, 0)

    # ========== P1: temp_directory 配置 ==========

    def test_temp_directory_set(self):
        """测试 DailyLoader 设置 temp_directory"""
        loader = DailyLoader(self.config)
        conn = loader.conn.connect()
        try:
            td = conn.execute(
                "SELECT current_setting('temp_directory')"
            ).fetchone()[0]
            self.assertIsNotNone(td)
            # temp_directory 应指向有效路径或为空
        finally:
            pass
        loader.conn.close()


if __name__ == '__main__':
    unittest.main()