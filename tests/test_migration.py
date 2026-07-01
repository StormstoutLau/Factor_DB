"""
Phase 3: 数据迁移测试

验证 factor_data → factor_wide + factor_history 迁移逻辑，
factor_data 替换为视图保持向后兼容。
"""

import unittest
import tempfile
import shutil
import os

import pandas as pd
import numpy as np

from core.connection import DuckDBConnection
from core.schema import SchemaManager


class TestFactorDataMigration(unittest.TestCase):
    """因子数据迁移测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self._setup_source_db()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_source_db(self):
        """构造模拟源数据库（含历史版本数据）"""
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()

        with conn._conn as c:
            c.execute('''
                CREATE TABLE factor_data (
                    trade_date DATE,
                    stock_code VARCHAR,
                    factor_name VARCHAR,
                    factor_value DOUBLE,
                    loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # 构造 3 个因子，2 只股票，3 天数据
            dates = pd.date_range('2024-01-02', periods=3).date
            stocks = ['000001.SZ', '600001.SH']
            factors = ['pe_ratio', 'pb_ratio', 'market_cap']

            rows = []
            for i, d in enumerate(dates):
                for s in stocks:
                    for f in factors:
                        # 每个 (date, stock, factor) 有 2 个版本（第 1 天、第 2 天各加载一次）
                        rows.append({'trade_date': d, 'stock_code': s, 'factor_name': f,
                                     'factor_value': float(i + 1), 'loaded_at': None})
                        rows.append({'trade_date': d, 'stock_code': s, 'factor_name': f,
                                     'factor_value': float(i + 0.5), 'loaded_at': None})

            df = pd.DataFrame(rows)
            c.execute("INSERT INTO factor_data SELECT * FROM df")

        conn.close()

    def test_migration_creates_tables(self):
        """迁移应创建 factor_wide 和 factor_history 表，factor_data 替换为视图"""
        DuckDBConnection._instances.clear()

        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        stats = schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()
        # SHOW TABLES 会包含视图，需要过滤 table_type = 'BASE TABLE'
        tables = [r[0] for r in conn2.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
        ).fetchall()]
        views = [r[0] for r in conn2.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_type = 'VIEW'"
        ).fetchall()]
        conn2.close()

        self.assertIn('factor_wide', tables)
        self.assertIn('factor_history', tables)
        self.assertNotIn('factor_data', tables)
        self.assertIn('factor_data', views)

    def test_migration_wide_table_latest_version(self):
        """宽表只保留最新版本（每个 date×stock×factor 一行）"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()

        wide_count = conn2.execute('SELECT COUNT(*) FROM factor_wide').fetchone()[0]
        self.assertEqual(wide_count, 6, f"宽表应有 6 行（3天×2股票），实际 {wide_count}")

        factors = conn2.execute('''
            SELECT COUNT(*) FROM information_schema.columns
            WHERE table_name = 'factor_wide'
            AND column_name NOT IN ('trade_date', 'stock_code', 'loaded_at')
        ''').fetchone()[0]
        self.assertEqual(factors, 3)

        conn2.close()

    def test_migration_history_table_all_versions(self):
        """历史表保留所有版本"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()

        hist_count = conn2.execute('SELECT COUNT(*) FROM factor_history').fetchone()[0]
        self.assertEqual(hist_count, 36, f"历史表应有 36 行，实际 {hist_count}")

        conn2.close()

    def test_factor_data_view_compatible(self):
        """factor_data 视图应与原表兼容（UNPIVOT wide → long）"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()

        # 视图 = 宽表 UNPIVOT = 3因子 × 6(date×stock) = 18 行
        view_count = conn2.execute('SELECT COUNT(*) FROM factor_data').fetchone()[0]
        self.assertEqual(view_count, 18, f"视图应有 18 行，实际 {view_count}")

        pe_count = conn2.execute(
            "SELECT COUNT(*) FROM factor_data WHERE factor_name = 'pe_ratio'"
        ).fetchone()[0]
        self.assertEqual(pe_count, 6)

        rows = conn2.execute(
            "SELECT * FROM factor_data WHERE factor_name = 'pe_ratio' LIMIT 1"
        ).fetchall()
        self.assertTrue(len(rows) > 0)

        conn2.close()

    def test_wide_table_primary_key(self):
        """宽表应有 (trade_date, stock_code) 主键"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()

        pk_cols = conn2.execute('''
            SELECT column_name FROM information_schema.key_column_usage
            WHERE table_name = 'factor_wide'
            AND constraint_name LIKE '%pkey%'
        ''').fetchall()

        conn2.close()
        pk_names = [r[0] for r in pk_cols]
        self.assertIn('trade_date', pk_names)
        self.assertIn('stock_code', pk_names)

    def test_upsert_same_factor_no_duplicate(self):
        """重复迁移同一因子，宽表不应出现重复"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        schema._migrate_single_factor('pe_ratio')

        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()
        wide_count = conn2.execute('SELECT COUNT(*) FROM factor_wide').fetchone()[0]
        conn2.close()

        self.assertEqual(wide_count, 6)


if __name__ == '__main__':
    unittest.main()
