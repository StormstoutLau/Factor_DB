"""
Phase 3: 数据迁移测试

验证 factor_data → factor_wide + factor_history 迁移逻辑，
factor_data 替换为视图保持向后兼容。
"""

import unittest
import tempfile
import shutil
import os
from unittest import mock

import pandas as pd
import numpy as np

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager, MigrationError


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

    def test_migrate_single_factor_arg_max_latest_version(self):
        """多版本因子迁移时，宽表应取 loaded_at 最新的 factor_value（arg_max 语义）"""
        # 构造带真实 loaded_at 差异的数据
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        with conn._conn as c:
            c.execute("DELETE FROM factor_data")
            # 同一 (date, stock, factor) 有 3 个版本，loaded_at 递增，factor_value 递增
            c.execute("""
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value, loaded_at) VALUES
                (DATE '2024-01-02', '000001.SZ', 'pe_ratio', 10.0, TIMESTAMP '2024-01-03 10:00:00'),
                (DATE '2024-01-02', '000001.SZ', 'pe_ratio', 20.0, TIMESTAMP '2024-01-03 11:00:00'),
                (DATE '2024-01-02', '000001.SZ', 'pe_ratio', 30.0, TIMESTAMP '2024-01-03 12:00:00')
            """)
        conn.close()

        # 迁移
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        # 直接迁移单因子，避免全量迁移重建表
        schema.create_factor_wide(['pe_ratio'])
        schema.create_factor_history()
        schema._migrate_single_factor('pe_ratio')
        conn.close()

        # 验证宽表取最新版本 (loaded_at=12:00, factor_value=30.0)
        DuckDBConnection._instances.clear()
        conn2 = DuckDBConnection(self.db_path, read_only=True)
        conn2.connect()
        val = conn2.execute(
            "SELECT \"pe_ratio\" FROM factor_wide WHERE trade_date = DATE '2024-01-02' AND stock_code = '000001.SZ'"
        ).fetchone()[0]
        conn2.close()

        self.assertEqual(val, 30.0, f"宽表应取 loaded_at 最新版本 (30.0)，实际 {val}")


class TestMigrationSafety(unittest.TestCase):
    """迁移数据安全（P0）：失败中止 / 备份 / 对账 / 幂等"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self._setup_source_db()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_source_db(self):
        """构造源库：3 因子 × 2 股票 × 3 天 × 2 版本 = 36 行"""
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
            dates = pd.date_range('2024-01-02', periods=3).date
            stocks = ['000001.SZ', '600001.SH']
            factors = ['pe_ratio', 'pb_ratio', 'market_cap']
            rows = []
            for i, d in enumerate(dates):
                for s in stocks:
                    for f in factors:
                        rows.append({'trade_date': d, 'stock_code': s, 'factor_name': f,
                                     'factor_value': float(i + 1), 'loaded_at': None})
                        rows.append({'trade_date': d, 'stock_code': s, 'factor_name': f,
                                     'factor_value': float(i + 0.5), 'loaded_at': None})
            df = pd.DataFrame(rows)
            c.execute("INSERT INTO factor_data SELECT * FROM df")
        conn.close()

    def _open_readonly(self):
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=True)
        conn.connect()
        tables = {
            r[0]: r[1]
            for r in conn.execute(
                "SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        return conn, tables

    def test_migration_aborts_on_factor_failure_keeps_source(self):
        """任一因子迁移失败必须中止，源表保持 TABLE 且行数不变"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)

        original = SchemaManager._migrate_single_factor

        def flaky(self_, factor_name):
            if factor_name == 'pb_ratio':
                raise MigrationError('simulated failure')
            return original(self_, factor_name)

        with mock.patch.object(SchemaManager, '_migrate_single_factor', flaky):
            with self.assertRaises(MigrationError):
                schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        conn2, tables = self._open_readonly()
        self.assertEqual(tables.get('factor_data'), 'BASE TABLE')
        count = conn2.execute('SELECT COUNT(*) FROM factor_data').fetchone()[0]
        self.assertEqual(count, 36, f"源表行数应保持 36，实际 {count}")
        self.assertFalse(
            [t for t in tables if t.startswith('factor_data_backup_')],
            "失败中止时不应产生备份表（未执行替换）"
        )
        conn2.close()

    def test_migration_creates_backup_table_on_success(self):
        """成功迁移应 RENAME 出备份表，而不是 DROP 原表"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        conn2, tables = self._open_readonly()
        backups = [t for t in tables if t.startswith('factor_data_backup_')]
        self.assertEqual(len(backups), 1, f"应有且仅有 1 个备份表，实际 {backups}")
        self.assertEqual(tables.get('factor_data'), 'VIEW')
        backup_count = conn2.execute(f'SELECT COUNT(*) FROM "{backups[0]}"').fetchone()[0]
        self.assertEqual(backup_count, 36, "备份表应保留源表全部 36 行")
        conn2.close()

    def test_migration_partial_history_aborts(self):
        """历史表已有不完整数据时必须中止（人工介入），不得覆盖源表"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        with conn._conn as c:
            c.execute('''
                CREATE TABLE factor_history (
                    trade_date DATE, stock_code VARCHAR, factor_name VARCHAR,
                    factor_value DOUBLE, loaded_at TIMESTAMP
                )
            ''')
            c.execute('''
                INSERT INTO factor_history VALUES
                (DATE '2024-01-02', '000001.SZ', 'pe_ratio', 1.0, TIMESTAMP '2024-01-02 10:00:00')
            ''')
        schema = SchemaManager(conn)
        with self.assertRaises(MigrationError):
            schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        conn2, tables = self._open_readonly()
        self.assertEqual(tables.get('factor_data'), 'BASE TABLE')
        count = conn2.execute('SELECT COUNT(*) FROM factor_data').fetchone()[0]
        self.assertEqual(count, 36)
        conn2.close()

    def test_migration_rerun_is_idempotent(self):
        """迁移成功后重跑应为空操作：不重复写历史、不产生第二个备份"""
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        schema = SchemaManager(conn)
        schema.migrate_factor_data_to_wide(batch_size=3)
        second = schema.migrate_factor_data_to_wide(batch_size=3)
        conn.close()

        self.assertEqual(second, {}, "重跑应直接短路返回空统计")
        conn2, tables = self._open_readonly()
        hist = conn2.execute('SELECT COUNT(*) FROM factor_history').fetchone()[0]
        self.assertEqual(hist, 36, f"历史表不应重复追加，实际 {hist}")
        backups = [t for t in tables if t.startswith('factor_data_backup_')]
        self.assertEqual(len(backups), 1)
        conn2.close()


if __name__ == '__main__':
    unittest.main()
