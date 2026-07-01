"""
PIT 查询 NULL 过滤测试

验证 _build_pit_subquery 的 non_null_columns 参数：
当 daily_prices 存在多个 loaded_at 版本且最新版本 close=NULL 时，
PIT 查询应正确排除空壳行，返回有实际数据的版本。
"""

import unittest
import tempfile
import shutil
import os
from datetime import date, datetime

import pandas as pd
import numpy as np

from core.connection import DuckDBConnection
from query.price_query import PriceQuery


class TestPITNullFiltering(unittest.TestCase):
    """PIT NULL 过滤测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self._setup_test_data()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_test_data(self):
        """创建模拟数据：最新版本 close=NULL 的场景"""
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()

        with conn._conn as c:
            # 创建 daily_prices 表
            c.execute('''
                CREATE TABLE daily_prices (
                    trade_date DATE,
                    stock_code VARCHAR,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume DOUBLE,
                    amount DOUBLE,
                    adj_factor DOUBLE,
                    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            c.execute('''
                CREATE TABLE trade_calendar (
                    trade_date DATE,
                    is_trading_day BOOLEAN
                )
            ''')
            c.execute('''
                CREATE TABLE stock_info (
                    stock_code VARCHAR,
                    stock_name VARCHAR
                )
            ''')

            # 模拟场景：同一 (trade_date, stock_code) 有 3 个版本
            # 版本 1: close=16.87 (最早，有数据)
            # 版本 2: close=NULL (中间，仅 adj_factor 更新)
            # 版本 3: close=NULL (最新，仅 adj_factor 更新)
            rows = []
            for d in pd.date_range('2024-01-02', periods=5).date:
                for s in ['000001', '000002']:
                    # 版本 1: 有实际数据
                    rows.append({
                        'trade_date': d, 'stock_code': s,
                        'open': 10.0, 'high': 11.0, 'low': 9.5, 'close': 10.5,
                        'volume': 1000000, 'amount': 10500000,
                        'adj_factor': 1.0,
                        'loaded_at': datetime(2024, 1, 10, 10, 0, 0)
                    })
                    # 版本 2: 仅 adj_factor 更新，close=NULL
                    rows.append({
                        'trade_date': d, 'stock_code': s,
                        'open': None, 'high': None, 'low': None, 'close': None,
                        'volume': None, 'amount': None,
                        'adj_factor': 1.1,
                        'loaded_at': datetime(2024, 1, 11, 10, 0, 0)
                    })
                    # 版本 3: 再次 adj_factor 更新，close=NULL
                    rows.append({
                        'trade_date': d, 'stock_code': s,
                        'open': None, 'high': None, 'low': None, 'close': None,
                        'volume': None, 'amount': None,
                        'adj_factor': 1.2,
                        'loaded_at': datetime(2024, 1, 12, 10, 0, 0)
                    })

            df = pd.DataFrame(rows)
            c.execute("INSERT INTO daily_prices SELECT * FROM df")

        conn.close()

    def test_price_matrix_returns_non_null_with_non_null_filter(self):
        """修复后：get_price_matrix 应返回非 NaN 数据"""
        DuckDBConnection._instances.clear()
        pq = PriceQuery(self.db_path)

        matrix = pq.get_price_matrix(
            field='close',
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 6)
        )

        # 应有 5 天 × 2 股票 = 10 个值
        self.assertEqual(matrix.shape, (5, 2))
        # 不应全是 NaN
        self.assertFalse(matrix.isna().all().all(),
                         "修复后价格矩阵不应全为 NaN")

        # 验证具体值（应该取到版本 1 的 close=10.5）
        first_row = matrix.iloc[0]
        self.assertEqual(first_row['000001'], 10.5)
        self.assertEqual(first_row['000002'], 10.5)

    def test_price_matrix_all_nan_without_fix(self):
        """验证：不加 non_null_columns 时，旧逻辑会返回 NaN"""
        DuckDBConnection._instances.clear()
        import duckdb
        conn = duckdb.connect(self.db_path, read_only=True)

        # 模拟旧版 PIT 逻辑（不加 close IS NOT NULL）
        sql = '''
            SELECT trade_date, stock_code, close
            FROM daily_prices
            WHERE (trade_date, stock_code, loaded_at) IN (
                SELECT trade_date, stock_code, MAX(loaded_at)
                FROM daily_prices
                WHERE 1=1
                GROUP BY trade_date, stock_code
            )
            ORDER BY trade_date, stock_code
        '''
        df = conn.execute(sql).fetchdf()
        conn.close()

        # 旧逻辑：所有 close 都是 NaN
        self.assertTrue(df['close'].isna().all(),
                        "旧逻辑（不加 non_null_columns）应全是 NaN，证明修复的必要性")

    def test_get_daily_returns_non_null_data(self):
        """修复后：get_daily 应返回非 NULL 的 close 数据"""
        DuckDBConnection._instances.clear()
        pq = PriceQuery(self.db_path)

        df = pq.get_daily(
            stock_codes=['000001'],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 6)
        )

        self.assertEqual(len(df), 5)
        self.assertFalse(df['close'].isna().any(),
                         "修复后 get_daily 的 close 不应有 NULL")

        # 验证 close 值
        self.assertEqual(df['close'].iloc[0], 10.5)

    def test_pit_subquery_sql_contains_non_null_filter(self):
        """验证生成的 SQL 包含 non_null 过滤条件"""
        from query.base import BaseQuery
        DuckDBConnection._instances.clear()

        bq = BaseQuery.__new__(BaseQuery)
        sql = bq._build_pit_subquery(
            'daily_prices', ['trade_date', 'stock_code'],
            non_null_columns=['close']
        )
        self.assertIn('close IS NOT NULL', sql)

    def test_pit_subquery_sql_no_non_null_filter_when_not_specified(self):
        """验证：不传 non_null_columns 时，SQL 不包含额外过滤"""
        from query.base import BaseQuery
        DuckDBConnection._instances.clear()

        bq = BaseQuery.__new__(BaseQuery)
        sql = bq._build_pit_subquery(
            'daily_prices', ['trade_date', 'stock_code']
        )
        self.assertNotIn('IS NOT NULL', sql)


if __name__ == '__main__':
    unittest.main()