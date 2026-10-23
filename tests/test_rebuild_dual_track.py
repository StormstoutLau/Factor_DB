"""两阶段双轨重建测试（rebuild_factor_data_dual_track）"""

import os
import shutil
import tempfile
import unittest

import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager


class TestRebuildDualTrack(unittest.TestCase):
    """验证 history 重建 + 分块 pivot + 视图替换"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self._setup_source_db()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _setup_source_db(self):
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        with conn._conn as c:
            c.execute('''
                CREATE TABLE factor_data (
                    trade_date DATE,
                    stock_code VARCHAR,
                    factor_name VARCHAR,
                    factor_value DOUBLE,
                    loaded_at TIMESTAMP
                )
            ''')
            dates = pd.date_range('2024-01-02', periods=3).date
            stocks = ['000001', '600001']
            rows = []
            for d in dates:
                for s in stocks:
                    for f in ('factor_a', 'factor_b', 'factor_c'):
                        # factor_b 在 2024-01-02 / 600001 缺失（稀疏因子）
                        if f == 'factor_b' and str(d) == '2024-01-02' and s == '600001':
                            continue
                        rows.append({
                            'trade_date': d, 'stock_code': s, 'factor_name': f,
                            'factor_value': 1.0, 'loaded_at': pd.Timestamp('2024-02-01'),
                        })
            # factor_a 多一个更新版本，应取 loaded_at 最新值
            rows.append({
                'trade_date': dates[0], 'stock_code': '000001', 'factor_name': 'factor_a',
                'factor_value': 2.0, 'loaded_at': pd.Timestamp('2024-03-01'),
            })
            df = pd.DataFrame(rows)
            c.execute('INSERT INTO factor_data SELECT * FROM df')
        conn.close()

    def _rebuild(self, chunk_size=1, sample_size=3):
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        stats = SchemaManager(conn).rebuild_factor_data_dual_track(
            chunk_size=chunk_size, sample_size=sample_size
        )
        conn.close()
        DuckDBConnection._instances.clear()
        return stats

    def _query(self, sql, params=None):
        conn = DuckDBConnection(self.db_path, read_only=True)
        conn.connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

    def test_rebuild_creates_view_and_counts(self):
        stats = self._rebuild()
        self.assertEqual(stats, {'factors': 3, 'history_rows': 18, 'wide_rows': 6})

        objects = dict(self._query("""
            SELECT table_name, table_type FROM information_schema.tables
            WHERE table_name IN ('factor_data', 'factor_wide', 'factor_history')
               OR table_name LIKE 'factor_data_backup_%'
        """))
        self.assertEqual(objects['factor_data'], 'VIEW')
        self.assertEqual(objects['factor_wide'], 'BASE TABLE')
        self.assertEqual(objects['factor_history'], 'BASE TABLE')
        self.assertTrue(
            any(k.startswith('factor_data_backup_') for k in objects),
            f'缺少备份表: {list(objects)}',
        )

    def test_latest_version_and_sparse_values(self):
        self._rebuild()
        latest = self._query(
            'SELECT factor_a FROM factor_wide '
            'WHERE trade_date = DATE \'2024-01-02\' AND stock_code = \'000001\''
        )
        self.assertEqual(latest[0][0], 2.0)

        sparse = self._query(
            'SELECT factor_b FROM factor_wide '
            'WHERE trade_date = DATE \'2024-01-02\' AND stock_code = \'600001\''
        )
        self.assertIsNone(sparse[0][0])

        n_c = self._query('SELECT COUNT(factor_c) FROM factor_wide')
        self.assertEqual(n_c[0][0], 6)

    def test_index_dependency_is_removed(self):
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        with conn._conn as c:
            c.execute('CREATE INDEX idx_factor_data_name ON factor_data(factor_name)')
        conn.close()

        stats = self._rebuild()
        self.assertEqual(stats['wide_rows'], 6)

        indexes = self._query(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name = 'factor_data'"
        )
        self.assertEqual(indexes, [])

    def test_second_run_is_idempotent(self):
        self._rebuild()
        stats = self._rebuild()
        self.assertEqual(stats, {'factors': 0, 'history_rows': 0, 'wide_rows': 0})
        objects = dict(self._query("""
            SELECT table_name, table_type FROM information_schema.tables
            WHERE table_name = 'factor_data'
        """))
        self.assertEqual(objects['factor_data'], 'VIEW')

    def test_invalid_chunk_size_leaves_source_untouched(self):
        DuckDBConnection._instances.clear()
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        with self.assertRaises(ValueError):
            SchemaManager(conn).rebuild_factor_data_dual_track(chunk_size=0)
        conn.close()
        DuckDBConnection._instances.clear()

        objects = dict(self._query("""
            SELECT table_name, table_type FROM information_schema.tables
            WHERE table_name = 'factor_data'
        """))
        self.assertEqual(objects['factor_data'], 'BASE TABLE')


if __name__ == '__main__':
    unittest.main()
