"""
适配器模块测试

测试 PandasAdapter、FactorTradingAdapter、DataLoaderV3Adapter。
"""

import os
import tempfile
import unittest
from datetime import date

import numpy as np
import pandas as pd

from adapters.pandas_adapter import PandasAdapter
from adapters.engine_adapter import FactorTradingAdapter, DataLoaderV3Adapter
from core.connection import DuckDBConnection
from core.schema import SchemaManager


class TestPandasAdapter(unittest.TestCase):
    """PandasAdapter 测试类"""

    def setUp(self):
        """测试前准备"""
        self.adapter = PandasAdapter()
        self.sample_df = pd.DataFrame({
            'trade_date': ['2024-01-01', '2024-01-01', '2024-01-02', '2024-01-02'],
            'stock_code': ['000001.SZ', '600000.SH', '000001.SZ', '600000.SH'],
            'close': [10.5, 20.5, 11.0, 21.0]
        })

    def test_to_wide_format(self):
        """测试转为宽格式"""
        matrix = self.adapter.to_wide_format(self.sample_df)

        self.assertEqual(matrix.shape, (2, 2))
        self.assertIn('000001.SZ', matrix.columns)

    def test_to_long_format(self):
        """测试转为长格式"""
        matrix = self.adapter.to_wide_format(self.sample_df)
        long_df = self.adapter.to_long_format(matrix, value_name='close')

        self.assertGreater(len(long_df), 0)
        self.assertIn('close', long_df.columns)

    def test_to_multi_index(self):
        """测试多级索引"""
        df = self.adapter.to_multi_index(self.sample_df)
        self.assertIsInstance(df.index, pd.MultiIndex)

    def test_add_technical_indicators(self):
        """测试添加技术指标"""
        df = pd.DataFrame({
            'trade_date': pd.date_range('2024-01-01', periods=30),
            'stock_code': ['000001.SZ'] * 30,
            'close': np.random.randn(30).cumsum() + 10
        })

        result = self.adapter.add_technical_indicators(df, ['MA5', 'MA20'])
        self.assertIn('MA5', result.columns)
        self.assertIn('MA20', result.columns)


class TestFactorTradingAdapter(unittest.TestCase):
    """FactorTradingAdapter 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_adapter.db')

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.init_database()

        # 插入测试数据
        with conn as c:
            c.execute("""
                INSERT INTO daily_prices (trade_date, stock_code, open, high, low, close, volume, amount, adj_factor)
                VALUES
                    ('2024-01-01', '000001.SZ', 10.0, 11.0, 9.5, 10.5, 10000, 105000, 1.0),
                    ('2024-01-02', '000001.SZ', 10.5, 11.5, 10.0, 11.0, 12000, 132000, 1.0)
            """)

        self.adapter = FactorTradingAdapter(self.db_path)

    def tearDown(self):
        """测试后清理"""
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_get_adj_price(self):
        """测试获取复权价格"""
        df = self.adapter.get_adj_price(
            price_type='close',
            start_date='2024-01-01',
            end_date='2024-01-02'
        )

        self.assertIsInstance(df, pd.DataFrame)

    def test_get_trade_dates(self):
        """测试获取交易日"""
        dates = self.adapter.get_trade_dates('2024-01-01', '2024-01-02')
        self.assertIsInstance(dates, list)


class TestDataLoaderV3Adapter(unittest.TestCase):
    """DataLoaderV3Adapter 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_v3.db')

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

        self.adapter = DataLoaderV3Adapter(self.db_path)

    def tearDown(self):
        """测试后清理"""
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_to_data_loader_v3(self):
        """测试转为 DataLoaderV3 格式"""
        data = self.adapter.to_data_loader_v3(
            stock_codes=['000001.SZ', '600000.SH'],
            start_date='2024-01-01',
            end_date='2024-01-02',
            fields=['close']
        )

        self.assertIn('dates', data)
        self.assertIn('stocks', data)
        self.assertIn('close', data)
        self.assertIsInstance(data['close'], np.ndarray)

    def test_to_numpy_arrays(self):
        """测试转为 NumPy 数组"""
        df = pd.DataFrame({
            'trade_date': ['2024-01-01', '2024-01-01', '2024-01-02', '2024-01-02'],
            'stock_code': ['000001.SZ', '600000.SH', '000001.SZ', '600000.SH'],
            'close': [10.5, 20.5, 11.0, 21.0]
        })

        result = self.adapter.to_numpy_arrays(df)
        self.assertIn('dates', result)
        self.assertIn('values', result)
        self.assertIsInstance(result['values'], np.ndarray)


if __name__ == '__main__':
    unittest.main()
