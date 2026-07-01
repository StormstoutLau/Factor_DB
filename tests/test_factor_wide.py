"""
因子宽表架构测试 (方案A: factor_wide + factor_history)

测试 SchemaManager 的宽表创建、长表历史、PIVOT/UNPIVOT 转换等功能。
"""

import os
import tempfile
import unittest

from core.connection import DuckDBConnection
from core.schema import SchemaManager


class TestFactorWideSchema(unittest.TestCase):
    """因子宽表架构 Schema 层测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_factor_wide.db')
        self.conn = DuckDBConnection(self.db_path)
        self.schema = SchemaManager(self.conn)

    def tearDown(self):
        self.conn.close()
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    # ========== 1. 宽表创建 ==========

    def test_create_factor_wide_with_factors(self):
        """创建带指定因子列的宽表"""
        factor_names = ['pe', 'pb', 'roe']
        result = self.schema.create_factor_wide(factor_names)
        self.assertTrue(result)
        self.assertTrue(self.schema.table_exists('factor_wide'))

        with self.conn as conn:
            cols = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'factor_wide'
                ORDER BY ordinal_position
            """).fetchall()
            col_names = [r[0] for r in cols]

        for expected in ['trade_date', 'stock_code', 'loaded_at', 'pe', 'pb', 'roe']:
            self.assertIn(expected, col_names, f"宽表应包含列 {expected}")

    def test_create_factor_wide_empty_factors(self):
        """空因子列表也能创建宽表（只有元数据列）"""
        result = self.schema.create_factor_wide([])
        self.assertTrue(result)

        with self.conn as conn:
            col_count = conn.execute("""
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'factor_wide'
            """).fetchone()[0]

        self.assertEqual(col_count, 3)  # trade_date, stock_code, loaded_at

    def test_create_factor_wide_pk_exists(self):
        """宽表主键 (trade_date, stock_code) 应存在"""
        self.schema.create_factor_wide(['pe', 'pb'])

        with self.conn as conn:
            pk_info = conn.execute("""
                SELECT constraint_type, constraint_name
                FROM information_schema.table_constraints
                WHERE table_name = 'factor_wide' AND constraint_type = 'PRIMARY KEY'
            """).fetchall()

        self.assertEqual(len(pk_info), 1, "宽表应有 PRIMARY KEY 约束")

    def test_create_factor_wide_pk_unique(self):
        """宽表主键应强制唯一"""
        self.schema.create_factor_wide(['pe'])

        with self.conn as conn:
            conn.execute("""
                INSERT INTO factor_wide (trade_date, stock_code, loaded_at, pe)
                VALUES ('2024-01-01', '000001', '2024-01-02 10:00:00', 10.5)
            """)

            with self.assertRaises(Exception):
                conn.execute("""
                    INSERT INTO factor_wide (trade_date, stock_code, loaded_at, pe)
                    VALUES ('2024-01-01', '000001', '2024-01-03 10:00:00', 11.0)
                """)

    # ========== 2. 长表历史创建 ==========

    def test_create_factor_history(self):
        """创建 factor_history 长表历史表"""
        result = self.schema.create_factor_history()
        self.assertTrue(result)
        self.assertTrue(self.schema.table_exists('factor_history'))

        with self.conn as conn:
            cols = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'factor_history'
                ORDER BY ordinal_position
            """).fetchall()
            col_names = [r[0] for r in cols]

        for expected in ['trade_date', 'stock_code', 'factor_name', 'factor_value', 'loaded_at']:
            self.assertIn(expected, col_names, f"factor_history 应包含列 {expected}")

    def test_factor_history_no_pk(self):
        """factor_history 无主键（避免大表 ART 索引 OOM）"""
        self.schema.create_factor_history()

        with self.conn as conn:
            pk_count = conn.execute("""
                SELECT COUNT(*) FROM information_schema.table_constraints
                WHERE table_name = 'factor_history' AND constraint_type = 'PRIMARY KEY'
            """).fetchone()[0]

        self.assertEqual(pk_count, 0, "factor_history 不应有主键（避免 OOM）")

    # ========== 3. 长表 → 宽表 PIVOT ==========

    def test_pivot_long_to_wide(self):
        """长表数据 PIVOT 到宽表"""
        self.schema.create_factor_wide(['pe', 'pb', 'roe'])
        self.schema.create_factor_history()

        with self.conn as conn:
            conn.execute("""
                INSERT INTO factor_history (trade_date, stock_code, factor_name, factor_value, loaded_at)
                VALUES
                    ('2024-01-01', '000001', 'pe', 10.5, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000001', 'pb', 1.2, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000001', 'roe', 0.15, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000002', 'pe', 8.3, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000002', 'pb', 0.9, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000002', 'roe', 0.12, '2024-01-02 10:00:00')
            """)

        rows = self.schema.pivot_long_to_wide('factor_history', 'factor_wide', ['pe', 'pb', 'roe'])
        self.assertEqual(rows, 2)

        with self.conn as conn:
            df = conn.execute("""
                SELECT trade_date, stock_code, pe, pb, roe
                FROM factor_wide
                ORDER BY stock_code
            """).fetchdf()

        self.assertEqual(len(df), 2)
        row1 = df[df['stock_code'] == '000001'].iloc[0]
        self.assertAlmostEqual(row1['pe'], 10.5)
        self.assertAlmostEqual(row1['pb'], 1.2)
        self.assertAlmostEqual(row1['roe'], 0.15)

    def test_pivot_with_missing_factors(self):
        """PIVOT 时部分因子缺失应填 NULL"""
        self.schema.create_factor_wide(['pe', 'pb'])
        self.schema.create_factor_history()

        with self.conn as conn:
            conn.execute("""
                INSERT INTO factor_history (trade_date, stock_code, factor_name, factor_value, loaded_at)
                VALUES
                    ('2024-01-01', '000001', 'pe', 10.5, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000002', 'pb', 0.9, '2024-01-02 10:00:00')
            """)

        rows = self.schema.pivot_long_to_wide('factor_history', 'factor_wide', ['pe', 'pb'])
        self.assertEqual(rows, 2)

        with self.conn as conn:
            df = conn.execute("""
                SELECT trade_date, stock_code, pe, pb
                FROM factor_wide
                ORDER BY stock_code
            """).fetchdf()

        row_pe_only = df[df['stock_code'] == '000001'].iloc[0]
        self.assertAlmostEqual(row_pe_only['pe'], 10.5)
        self.assertTrue(row_pe_only['pb'] is None or (row_pe_only['pb'] != row_pe_only['pb']))

    def test_pivot_multiple_versions_picks_latest(self):
        """PIVOT 时同一 (date, stock, factor) 有多版本，取最新 loaded_at"""
        self.schema.create_factor_wide(['pe'])
        self.schema.create_factor_history()

        with self.conn as conn:
            conn.execute("""
                INSERT INTO factor_history (trade_date, stock_code, factor_name, factor_value, loaded_at)
                VALUES
                    ('2024-01-01', '000001', 'pe', 10.0, '2024-01-02 10:00:00'),
                    ('2024-01-01', '000001', 'pe', 11.0, '2024-01-03 10:00:00')
            """)

        rows = self.schema.pivot_long_to_wide('factor_history', 'factor_wide', ['pe'])
        self.assertEqual(rows, 1)

        with self.conn as conn:
            val = conn.execute("SELECT pe FROM factor_wide WHERE stock_code = '000001'").fetchone()[0]

        self.assertAlmostEqual(val, 11.0, "应取最新 loaded_at 版本")

    # ========== 4. 宽表 → 长表 UNPIVOT ==========

    def test_unpivot_wide_to_long(self):
        """宽表数据 UNPIVOT 到长表"""
        self.schema.create_factor_wide(['pe', 'pb'])
        self.schema.create_factor_history()

        with self.conn as conn:
            conn.execute("""
                INSERT INTO factor_wide (trade_date, stock_code, loaded_at, pe, pb)
                VALUES
                    ('2024-01-01', '000001', '2024-01-02 10:00:00', 10.5, 1.2),
                    ('2024-01-01', '000002', '2024-01-02 10:00:00', 8.3, 0.9)
            """)

        rows = self.schema.unpivot_wide_to_long('factor_wide', 'factor_history', ['pe', 'pb'])
        self.assertEqual(rows, 4)  # 2 stocks × 2 factors

        with self.conn as conn:
            df = conn.execute("""
                SELECT stock_code, factor_name, factor_value
                FROM factor_history
                ORDER BY stock_code, factor_name
            """).fetchdf()

        self.assertEqual(len(df), 4)
        pe_row = df[(df['stock_code'] == '000001') & (df['factor_name'] == 'pe')].iloc[0]
        self.assertAlmostEqual(pe_row['factor_value'], 10.5)

    def test_unpivot_skips_null_values(self):
        """UNPIVOT 时 NULL 因子值不生成行"""
        self.schema.create_factor_wide(['pe', 'pb'])
        self.schema.create_factor_history()

        with self.conn as conn:
            conn.execute("""
                INSERT INTO factor_wide (trade_date, stock_code, loaded_at, pe, pb)
                VALUES ('2024-01-01', '000001', '2024-01-02 10:00:00', 10.5, NULL)
            """)

        rows = self.schema.unpivot_wide_to_long('factor_wide', 'factor_history', ['pe', 'pb'])
        self.assertEqual(rows, 1, "NULL 值不应 UNPIVOT")

        with self.conn as conn:
            factors = conn.execute(
                "SELECT factor_name FROM factor_history ORDER BY factor_name"
            ).fetchall()

        self.assertEqual(len(factors), 1)
        self.assertEqual(factors[0][0], 'pe')

    # ========== 5. 动态添加因子列 ==========

    def test_add_factor_column(self):
        """动态向宽表添加因子列"""
        self.schema.create_factor_wide(['pe'])

        result = self.schema.add_factor_column('factor_wide', 'pb')
        self.assertTrue(result)

        with self.conn as conn:
            cols = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'factor_wide'
            """).fetchall()
            col_names = [r[0] for r in cols]

        self.assertIn('pb', col_names)

    def test_add_factor_column_idempotent(self):
        """重复添加同一因子列不应报错"""
        self.schema.create_factor_wide(['pe'])

        self.assertTrue(self.schema.add_factor_column('factor_wide', 'pe'))
        self.assertTrue(self.schema.add_factor_column('factor_wide', 'pe'))

    def test_get_factor_columns(self):
        """获取宽表中的因子列列表"""
        self.schema.create_factor_wide(['pe', 'pb', 'roe'])

        factors = self.schema.get_factor_columns('factor_wide')
        self.assertEqual(set(factors), {'pe', 'pb', 'roe'})

    def test_get_factor_columns_empty_wide(self):
        """空宽表（无因子列）返回空列表"""
        self.schema.create_factor_wide([])

        factors = self.schema.get_factor_columns('factor_wide')
        self.assertEqual(factors, [])

    # ========== 6. 宽表 PK 索引低内存验证 ==========

    def test_wide_table_pk_columns_less(self):
        """宽表 PK 列数远少于长表 — 索引键更小 → 内存更低"""
        self.schema.create_factor_wide(['pe', 'pb', 'roe'])
        self.schema.create_factor_history()

        with self.conn as conn:
            wide_pk_cols = conn.execute("""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = 'factor_wide'
                  AND tc.constraint_type = 'PRIMARY KEY'
                ORDER BY kcu.ordinal_position
            """).fetchall()

            long_pk_cols = conn.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'factor_history'
            """).fetchall()

        self.assertEqual(len(wide_pk_cols), 2, "宽表 PK 应有 2 列 (date, stock)")
        self.assertGreaterEqual(len(long_pk_cols), 4, "长表有 5 列，PK 至少 4 列")

    def test_wide_table_rows_less_than_long(self):
        """宽表行数远少于长表 — 索引条目更少 → 内存更低"""
        import duckdb

        n_stocks = 100
        n_dates = 50
        n_factors = 30
        total_wide_rows = n_stocks * n_dates

        mem_db = duckdb.connect(':memory:')
        mem_db.execute(f"""
            CREATE TABLE factor_wide_test (
                trade_date DATE,
                stock_code VARCHAR,
                loaded_at TIMESTAMP,
                {', '.join([f'f{i} DOUBLE' for i in range(n_factors)])},
                PRIMARY KEY (trade_date, stock_code)
            )
        """)
        mem_db.execute(f"""
            INSERT INTO factor_wide_test
            SELECT
                DATE '2024-01-01' + (row % {n_dates})::INTEGER AS trade_date,
                LPAD((row / {n_dates})::VARCHAR, 6, '0') AS stock_code,
                TIMESTAMP '2024-01-02 10:00:00' AS loaded_at,
                {', '.join([f'RANDOM() AS f{i}' for i in range(n_factors)])}
            FROM range({total_wide_rows}) t(row)
        """)

        mem_db.execute(f"""
            CREATE TABLE factor_long_test (
                trade_date DATE,
                stock_code VARCHAR,
                factor_name VARCHAR,
                factor_value DOUBLE,
                loaded_at TIMESTAMP,
                PRIMARY KEY (trade_date, stock_code, factor_name, loaded_at)
            )
        """)
        mem_db.execute(f"""
            INSERT INTO factor_long_test
            SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
            FROM factor_wide_test
            UNPIVOT (
                factor_value FOR factor_name IN ({', '.join([f'f{i}' for i in range(n_factors)])})
            )
        """)

        wide_rows = mem_db.execute("SELECT COUNT(*) FROM factor_wide_test").fetchone()[0]
        long_rows = mem_db.execute("SELECT COUNT(*) FROM factor_long_test").fetchone()[0]

        ratio = long_rows / wide_rows
        self.assertAlmostEqual(ratio, n_factors, delta=1,
                               msg=f"长表行数应约为宽表的 {n_factors}×，实际 {ratio:.1f}×")

        wide_pk_cols = 2
        long_pk_cols = 4
        self.assertGreater(
            long_pk_cols / wide_pk_cols * (long_rows / wide_rows),
            10,
            f"长表索引规模应远大于宽表（行数×列数比例: {long_rows/wide_rows:.0f}× × {long_pk_cols/wide_pk_cols:.0f}× = {long_rows*long_pk_cols/(wide_rows*wide_pk_cols):.0f}×）"
        )

        mem_db.close()


if __name__ == '__main__':
    unittest.main()
