"""
SQL 安全与参数绑定测试（HIGH 1）

覆盖：
- 值参数绑定阻止引号注入
- 缓存 key 纳入参数
- 复用 SQL 片段（date_filter）参数只绑定一次
- 非法标识符抛 ValueError
- OSINT UNION ALL 参数复用
"""

import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from Factor_DB.query.factor_query import FactorQuery
from Factor_DB.query.osint_query import OSINTQuery
from Factor_DB.query.price_query import PriceQuery
from Factor_DB.query.screen import StockScreener
from Factor_DB.utils.sql_utils import (
    bind,
    bind_in,
    escape_literal,
    quote_identifier,
    validate_identifier,
)


class TestSqlUtils(unittest.TestCase):
    """sql_utils helper 单元测试"""

    def test_bind_returns_placeholder_and_writes_param(self):
        params = {}
        self.assertEqual(bind(params, 'start_date', date(2024, 1, 1)), '$start_date')
        self.assertEqual(params['start_date'], date(2024, 1, 1))

    def test_bind_same_name_same_value_is_idempotent(self):
        params = {}
        bind(params, 'x', 1)
        bind(params, 'x', 1)
        self.assertEqual(params['x'], 1)

    def test_bind_same_name_different_value_raises(self):
        params = {}
        bind(params, 'x', 1)
        with self.assertRaises(ValueError):
            bind(params, 'x', 2)

    def test_bind_in(self):
        params = {}
        sql = bind_in(params, 'code', ['000001', '600000'])
        self.assertEqual(sql, '$code_0, $code_1')
        self.assertEqual(params, {'code_0': '000001', 'code_1': '600000'})

    def test_bind_in_empty_raises(self):
        with self.assertRaises(ValueError):
            bind_in({}, 'code', [])

    def test_quote_identifier_escapes_double_quote(self):
        self.assertEqual(quote_identifier('pe_ratio'), '"pe_ratio"')
        self.assertEqual(quote_identifier('a"b'), '"a""b"')

    def test_validate_identifier_rejects_injection(self):
        self.assertEqual(validate_identifier('pe_ratio'), 'pe_ratio')
        for bad in ['a"b', 'x; DROP TABLE t', '1abc', '', 'a b']:
            with self.assertRaises(ValueError):
                validate_identifier(bad)

    def test_escape_literal_escapes_single_quote(self):
        self.assertEqual(escape_literal("a'b"), "'a''b'")
        self.assertEqual(escape_literal('normal'), "'normal'")


class TestQuerySqlSafety(unittest.TestCase):
    """查询层参数绑定行为测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_sql_safety.db')

        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        SchemaManager(conn).init_database()

        with conn as c:
            c.execute("""
                INSERT INTO daily_prices (trade_date, stock_code, open, high, low, close, volume, amount)
                VALUES
                    ('2024-01-02', '000001.SZ', 10.0, 11.0, 9.5, 10.5, 10000, 105000),
                    ('2024-01-03', '000001.SZ', 10.5, 11.5, 10.0, 11.0, 12000, 132000),
                    ('2024-01-02', '600000.SH', 20.0, 21.0, 19.5, 20.5, 5000, 102500),
                    ('2024-01-03', '600000.SH', 20.5, 21.5, 20.0, 21.0, 6000, 126000)
            """)
            c.execute("""
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                VALUES
                    ('2024-01-02', '000001.SZ', 'pe', 10.0),
                    ('2024-01-02', '600000.SH', 'pe', 20.0),
                    ('2024-01-02', '000001.SZ', 'pb', 1.0),
                    ('2024-01-02', '600000.SH', 'pb', 2.0),
                    ('2024-01-03', '000001.SZ', 'pe', 11.0),
                    ('2024-01-03', '600000.SH', 'pe', 21.0)
            """)
            c.execute("""
                INSERT INTO macro_data (trade_date, indicator_id, indicator_name, value, value_type, source_id)
                VALUES ('2024-01-02', 'GDP', 'GDP', 100.0, 'raw', 'test_src')
            """)
            c.execute("""
                INSERT INTO alternative_data (data_id, trade_date, data_type, data_subtype, entity_id, value, source_id)
                VALUES ('a1', '2024-01-02', 'chain', 'steel', 'S1', 5.0, 'test_src')
            """)
        conn.close()
        DuckDBConnection._instances.clear()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        for suffix in ['', '.wal', '.tmp']:
            path = self.db_path + suffix
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except PermissionError:
                    pass
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_injection_string_in_stock_code_returns_no_rows(self):
        """股票代码传注入串：应作为普通值比较，返回 0 行且不抛语法错"""
        q = PriceQuery(self.db_path)
        df = q.get_daily(
            stock_codes=["000001') OR ('1'='1"],
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
        )
        self.assertEqual(len(df), 0)

    def test_factor_name_with_quote_returns_no_rows(self):
        """因子名含单引号：不应触发 SQL 语法错误"""
        q = FactorQuery(self.db_path)
        df = q.get_factor(factor_name="pe'ratio", as_of=date(2024, 1, 2))
        self.assertEqual(len(df), 0)

    def test_cache_key_includes_params(self):
        """相同 SQL 模板、不同参数不得命中同一缓存条目"""
        q = FactorQuery(self.db_path, cache_size=10)
        df_pe = q.get_factor(factor_name='pe')
        df_pb = q.get_factor(factor_name='pb')

        self.assertEqual(len(df_pe), 4)
        self.assertEqual(len(df_pb), 2)
        stats = q.cache_stats()
        self.assertEqual(stats['misses'], 2, f"两次不同参数应均未命中，实际 {stats}")

    def test_reused_date_filter_in_ic_series(self):
        """date_filter 在多个 CTE 复用时，参数只需绑定一次"""
        q = FactorQuery(self.db_path)
        df = q.get_ic_series(
            factor_name='pe',
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 3),
        )
        self.assertFalse(df.empty)

    def test_invalid_field_identifier_raises(self):
        q = PriceQuery(self.db_path)
        with self.assertRaises(ValueError):
            q.get_daily(fields=['close; DROP TABLE daily_prices'])

    def test_invalid_sort_by_identifier_raises(self):
        q = StockScreener(self.db_path)
        with self.assertRaises(ValueError):
            q.screen(
                trade_date=date(2024, 1, 2),
                conditions=['1=1'],
                sort_by='x; DROP TABLE factor_data',
            )

    def test_osint_union_all_reuses_params(self):
        """UNION ALL 两个 WHERE 共用一组命名参数，且返回两表数据"""
        q = OSINTQuery(self.db_path)
        df = q.get_data_by_source('test_src')
        self.assertEqual(len(df), 2, f"应返回 macro_data + alternative_data 各 1 行，实际 {len(df)}")


if __name__ == '__main__':
    unittest.main()
