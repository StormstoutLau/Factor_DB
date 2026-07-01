"""
查询缓存测试
"""

import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from query.cache import QueryCache
from query.price_query import PriceQuery


class TestQueryCache(unittest.TestCase):
    """QueryCache 单元测试"""

    def setUp(self):
        self.cache = QueryCache(max_size=10, ttl=60)

    def test_put_and_get(self):
        df = pd.DataFrame({'a': [1, 2, 3]})
        self.cache.put('SELECT * FROM test', df)
        result = self.cache.get('SELECT * FROM test')
        self.assertIsNotNone(result)
        self.assertTrue(result.equals(df))

    def test_cache_miss(self):
        result = self.cache.get('SELECT nonexistent')
        self.assertIsNone(result)

    def test_cache_stats(self):
        self.cache.put('sql1', pd.DataFrame({'x': [1]}))
        self.cache.get('sql1')  # hit
        self.cache.get('sql2')  # miss
        stats = self.cache.stats()
        self.assertEqual(stats['hits'], 1)
        self.assertEqual(stats['misses'], 1)
        self.assertEqual(stats['size'], 1)

    def test_cache_clear(self):
        self.cache.put('sql1', pd.DataFrame({'x': [1]}))
        self.assertEqual(len(self.cache), 1)
        self.cache.clear()
        self.assertEqual(len(self.cache), 0)
        self.assertEqual(self.cache.stats()['hits'], 0)
        self.assertEqual(self.cache.stats()['misses'], 0)

    def test_cache_ttl_expiry(self):
        cache = QueryCache(max_size=10, ttl=0)  # 立即过期
        cache.put('sql1', pd.DataFrame({'x': [1]}))
        result = cache.get('sql1')
        self.assertIsNone(result)

    def test_cache_returns_copy(self):
        df = pd.DataFrame({'a': [1, 2, 3]})
        self.cache.put('sql1', df)
        result = self.cache.get('sql1')
        result.iloc[0, 0] = 999
        # 原始不应被修改
        original = self.cache.get('sql1')
        self.assertEqual(original.iloc[0, 0], 1)

    def test_cache_lru_eviction(self):
        cache = QueryCache(max_size=2, ttl=999)
        cache.put('sql1', pd.DataFrame({'x': [1]}))
        cache.put('sql2', pd.DataFrame({'x': [2]}))
        cache.put('sql3', pd.DataFrame({'x': [3]}))
        # sql1 应被淘汰（最旧）
        self.assertIsNone(cache.get('sql1'))
        self.assertIsNotNone(cache.get('sql2'))
        self.assertIsNotNone(cache.get('sql3'))
        self.assertEqual(len(cache), 2)


class TestPriceQueryCache(unittest.TestCase):
    """PriceQuery 缓存集成测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_cache.db')
        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.create_table('daily_prices')
        with conn as c:
            c.execute('''
                INSERT INTO daily_prices (trade_date, stock_code, close,
                                         loaded_at)
                VALUES
                ('2024-01-01', '000001.SZ', 10.5, '2024-01-02 08:00:00'),
                ('2024-01-02', '000001.SZ', 11.0, '2024-01-03 08:00:00')
            ''')
        conn.close()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_cache_disabled_by_default(self):
        pq = PriceQuery(self.db_path)
        self.assertIsNone(pq.cache)
        stats = pq.cache_stats()
        self.assertIsNone(stats)

    def test_cache_enabled(self):
        pq = PriceQuery(self.db_path, cache_size=50)
        self.assertIsNotNone(pq.cache)

        # 第一次查询 - miss
        df1 = pq.get_daily(stock_codes=['000001.SZ'])
        stats = pq.cache_stats()
        self.assertEqual(stats['misses'], 1)

        # 第二次查询 - hit
        df2 = pq.get_daily(stock_codes=['000001.SZ'])
        stats = pq.cache_stats()
        self.assertEqual(stats['hits'], 1)
        self.assertTrue(df1.equals(df2))

    def test_cache_clear(self):
        pq = PriceQuery(self.db_path, cache_size=50)
        pq.get_daily(stock_codes=['000001.SZ'])
        self.assertGreater(pq.cache_stats()['size'], 0)

        pq.cache_clear()
        self.assertEqual(pq.cache_stats()['size'], 0)


if __name__ == '__main__':
    unittest.main()