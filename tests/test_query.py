"""
查询接口测试

测试 PriceQuery、FactorQuery、StockScreener 的查询功能。
"""

import os
import tempfile
import unittest
from datetime import date

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from query.price_query import PriceQuery
from query.factor_query import FactorQuery
from query.screen import StockScreener


class TestPriceQuery(unittest.TestCase):
    """PriceQuery 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_query.db')

        # 初始化数据库
        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.init_database()

        # 插入测试数据
        with conn as c:
            c.execute("""
                INSERT INTO daily_prices (trade_date, stock_code, open, high, low, close, volume, amount)
                VALUES
                    ('2024-01-01', '000001.SZ', 10.0, 11.0, 9.5, 10.5, 10000, 105000),
                    ('2024-01-02', '000001.SZ', 10.5, 11.5, 10.0, 11.0, 12000, 132000),
                    ('2024-01-01', '600000.SH', 20.0, 21.0, 19.5, 20.5, 5000, 102500),
                    ('2024-01-02', '600000.SH', 20.5, 21.5, 20.0, 21.0, 6000, 126000)
            """)

        self.query = PriceQuery(self.db_path)

    def tearDown(self):
        """测试后清理"""
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_get_daily(self):
        """测试查询日 K 数据"""
        df = self.query.get_daily(
            stock_codes=['000001.SZ'],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2)
        )

        self.assertEqual(len(df), 2)
        self.assertIn('close', df.columns)

    def test_get_price_matrix(self):
        """测试获取价格矩阵"""
        matrix = self.query.get_price_matrix(
            field='close',
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2)
        )

        self.assertEqual(matrix.shape, (2, 2))  # 2天 × 2只股票
        self.assertIn('000001.SZ', matrix.columns)

    def test_get_date_range(self):
        """测试获取日期范围"""
        min_date, max_date = self.query.get_date_range()
        # DuckDB 返回 Timestamp，需要转为 date 比较
        from pandas import Timestamp
        if isinstance(min_date, Timestamp):
            min_date = min_date.date()
        if isinstance(max_date, Timestamp):
            max_date = max_date.date()
        self.assertEqual(min_date, date(2024, 1, 1))
        self.assertEqual(max_date, date(2024, 1, 2))


class TestFactorQuery(unittest.TestCase):
    """FactorQuery 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_factor.db')

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.init_database()

        # 插入测试因子数据
        with conn as c:
            c.execute("""
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                VALUES
                    ('2024-01-01', '000001.SZ', 'PE', 15.5),
                    ('2024-01-01', '600000.SH', 'PE', 12.3),
                    ('2024-01-02', '000001.SZ', 'PE', 16.0),
                    ('2024-01-02', '600000.SH', 'PE', 11.8),
                    ('2024-01-01', '000001.SZ', 'PB', 2.1),
                    ('2024-01-01', '600000.SH', 'PB', 1.5)
            """)

        self.query = FactorQuery(self.db_path)

    def tearDown(self):
        """测试后清理"""
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_get_factor(self):
        """测试查询因子数据"""
        df = self.query.get_factor('PE')
        self.assertEqual(len(df), 4)

    def test_get_cross_section(self):
        """测试获取截面数据"""
        df = self.query.get_cross_section('PE', date(2024, 1, 1))
        self.assertEqual(len(df), 2)

    def test_get_factor_matrix(self):
        """测试获取因子矩阵"""
        df = self.query.get_factor_matrix(['PE', 'PB'])
        self.assertIn('PE', df.columns)
        self.assertIn('PB', df.columns)

    def test_list_factors(self):
        """测试列出因子"""
        factors = self.query.list_factors()
        self.assertIn('PE', factors)
        self.assertIn('PB', factors)


class TestStockScreener(unittest.TestCase):
    """StockScreener 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_screen.db')

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.init_database()

        # 插入测试数据
        with conn as c:
            c.execute("""
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                VALUES
                    ('2024-01-01', '000001.SZ', 'PE', 15.5),
                    ('2024-01-01', '600000.SH', 'PE', 8.0),
                    ('2024-01-01', '000002.SZ', 'PE', 25.0)
            """)

        self.screener = StockScreener(self.db_path)

    def tearDown(self):
        """测试后清理"""
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_screen(self):
        """测试条件选股"""
        df = self.screener.screen(
            trade_date=date(2024, 1, 1),
            conditions=["factor_name = 'PE'", "factor_value < 20"]
        )

        self.assertGreater(len(df), 0)

    def test_get_quantile_stocks(self):
        """测试分位数选股"""
        df = self.screener.get_quantile_stocks(
            factor_name='PE',
            trade_date=date(2024, 1, 1),
            quantile=0.5,
            top=True
        )

        self.assertGreater(len(df), 0)


if __name__ == '__main__':
    unittest.main()
