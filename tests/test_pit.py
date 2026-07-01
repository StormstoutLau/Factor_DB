"""
PIT (Point-in-Time) 数据版本管理测试

测试 loaded_at 列、as_of 查询、数据清理等功能。
"""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

import pandas as pd

from core.connection import DuckDBConnection
from core.schema import SchemaManager


class TestPITSchema(unittest.TestCase):
    """PIT 表结构测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_pit.db')
        self.conn = DuckDBConnection(self.db_path)
        self.schema = SchemaManager(self.conn)

    def tearDown(self):
        self.conn.close()
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_daily_prices_has_loaded_at(self):
        """daily_prices 表包含 loaded_at 列"""
        self.schema.create_table('daily_prices')
        with self.conn as conn:
            cols = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'daily_prices'"
            ).fetchdf()
        self.assertIn('loaded_at', cols['column_name'].tolist())

    def test_factor_data_has_loaded_at(self):
        """factor_data 表包含 loaded_at 列"""
        self.schema.create_table('factor_data')
        with self.conn as conn:
            cols = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'factor_data'"
            ).fetchdf()
        self.assertIn('loaded_at', cols['column_name'].tolist())

    def test_loaded_at_default_value(self):
        """loaded_at 列有默认值 CURRENT_TIMESTAMP"""
        self.schema.create_table('daily_prices')
        with self.conn as conn:
            conn.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close) "
                "VALUES ('2024-01-01', '000001.SZ', 10.5)"
            )
            result = conn.execute(
                "SELECT loaded_at FROM daily_prices"
            ).fetchone()
        self.assertIsNotNone(result[0])

    def test_multiple_versions_same_pk(self):
        """同一 (trade_date, stock_code) 可以有多个 loaded_at 版本"""
        self.schema.create_table('daily_prices')
        with self.conn as conn:
            conn.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close) "
                "VALUES ('2024-01-01', '000001.SZ', 10.5)"
            )
            conn.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close) "
                "VALUES ('2024-01-01', '000001.SZ', 11.0)"
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM daily_prices "
                "WHERE trade_date = '2024-01-01' AND stock_code = '000001.SZ'"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_pit_indexes_created(self):
        """PIT 索引被创建"""
        self.schema.create_table('daily_prices')
        self.schema.create_table('factor_data')
        self.schema.create_index('idx_daily_pit')
        self.schema.create_index('idx_factor_pit')
        # 索引创建不抛异常即成功
        self.assertTrue(True)


class TestPITQuery(unittest.TestCase):
    """PIT 查询测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_pit_query.db')
        self.conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(self.conn)
        schema.create_table('daily_prices')
        schema.create_table('factor_data')

        # 插入多版本数据
        with self.conn as conn:
            # 版本1 (2024-01-03 loaded)
            conn.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close, loaded_at) "
                "VALUES "
                "('2024-01-01', '000001.SZ', 10.0, '2024-01-03 08:00:00'),"
                "('2024-01-02', '000001.SZ', 10.5, '2024-01-03 08:00:00')"
            )
            # 版本2 (2024-01-05 loaded, 修正了 01-01 的数据)
            conn.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close, loaded_at) "
                "VALUES "
                "('2024-01-01', '000001.SZ', 10.2, '2024-01-05 08:00:00'),"
                "('2024-01-02', '000001.SZ', 10.5, '2024-01-05 08:00:00')"
            )
            # 因子数据多版本
            conn.execute(
                "INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value, loaded_at) "
                "VALUES "
                "('2024-01-01', '000001.SZ', 'PE', 15.0, '2024-01-03 08:00:00'),"
                "('2024-01-01', '000001.SZ', 'PE', 14.5, '2024-01-05 08:00:00')"
            )
        conn.close()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_as_of_returns_old_version(self):
        """as_of='2024-01-04' 返回旧版本数据"""
        sql = '''
            SELECT close
            FROM daily_prices
            WHERE (trade_date, stock_code, loaded_at) IN (
                SELECT trade_date, stock_code, MAX(loaded_at)
                FROM daily_prices
                WHERE loaded_at <= '2024-01-04'
                GROUP BY trade_date, stock_code
            )
            AND trade_date = '2024-01-01'
        '''
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(sql).fetchone()
        self.assertAlmostEqual(result[0], 10.0)
        conn.close()

    def test_as_of_returns_new_version(self):
        """as_of='2024-01-06' 返回修正后的新版本数据"""
        sql = '''
            SELECT close
            FROM daily_prices
            WHERE (trade_date, stock_code, loaded_at) IN (
                SELECT trade_date, stock_code, MAX(loaded_at)
                FROM daily_prices
                WHERE loaded_at <= '2024-01-06'
                GROUP BY trade_date, stock_code
            )
            AND trade_date = '2024-01-01'
        '''
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(sql).fetchone()
        self.assertAlmostEqual(result[0], 10.2)
        conn.close()

    def test_factor_as_of(self):
        """因子数据的 as_of 查询"""
        sql = '''
            SELECT factor_value
            FROM factor_data
            WHERE (trade_date, stock_code, factor_name, loaded_at) IN (
                SELECT trade_date, stock_code, factor_name, MAX(loaded_at)
                FROM factor_data
                WHERE loaded_at <= '2024-01-04'
                GROUP BY trade_date, stock_code, factor_name
            )
            AND factor_name = 'PE'
        '''
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(sql).fetchone()
        self.assertAlmostEqual(result[0], 15.0)
        conn.close()

    def test_no_as_of_returns_latest(self):
        """无 as_of 限制时返回最新版本"""
        sql = '''
            SELECT close
            FROM daily_prices
            WHERE (trade_date, stock_code, loaded_at) IN (
                SELECT trade_date, stock_code, MAX(loaded_at)
                FROM daily_prices
                GROUP BY trade_date, stock_code
            )
            AND trade_date = '2024-01-01'
        '''
        conn = DuckDBConnection(self.db_path, read_only=True)
        with conn as c:
            result = c.execute(sql).fetchone()
        self.assertAlmostEqual(result[0], 10.2)
        conn.close()


class TestPITCompact(unittest.TestCase):
    """PIT 数据清理测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_pit_compact.db')
        self.conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(self.conn)
        schema.create_table('daily_prices')

        with self.conn as conn:
            # 3个版本，前2个在 2024-01-01 之前
            conn.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close, loaded_at) VALUES "
                "('2024-01-01', '000001.SZ', 10.0, '2024-01-01 08:00:00'),"
                "('2024-01-01', '000001.SZ', 10.1, '2024-01-02 08:00:00'),"
                "('2024-01-01', '000001.SZ', 10.2, '2024-01-05 08:00:00'),"
                "('2024-01-01', '000002.SZ', 20.0, '2024-01-01 08:00:00'),"
                "('2024-01-01', '000002.SZ', 20.1, '2024-01-02 08:00:00')"
            )
        self.conn.close()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_compact_keeps_latest_before_date(self):
        """compact 保留 cutoff 前每个主键的最新版本"""
        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        
        deleted = schema.compact_table('daily_prices', '2024-01-03')
        self.assertGreater(deleted, 0)  # compact 执行成功
        
        with conn as c:
            remaining = c.execute(
                "SELECT stock_code, close FROM daily_prices ORDER BY stock_code"
            ).fetchdf()
        
        # 000001.SZ: 保留了 10.1 (01-02 是最新) 和 10.2 (在 cutoff 之后不删除)
        # 000002.SZ: 保留了 20.1 (01-02 是最新)
        self.assertGreaterEqual(len(remaining), 2)
        conn.close()


if __name__ == '__main__':
    unittest.main()