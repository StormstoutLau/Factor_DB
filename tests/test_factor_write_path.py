"""
因子写路径统一测试

验证迁移前（factor_data 为表）与迁移后（factor_data 为视图）：
- 所有因子写入统一落到 factor_history + factor_wide
- 迁移前额外写 factor_data 保持旧读者一致
- OHLCV 仍写 daily_prices
- 迁移补齐部分宽表缺失列
- 兼容视图能自动看到新增因子列
"""

import os
import shutil
import tempfile
import unittest

import numpy as np
import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from Factor_DB.loaders.base import LoaderConfig
from Factor_DB.loaders.csmar_loader import CSMARLoader
from Factor_DB.loaders.daily_loader import DailyLoader


class TestFactorWritePath(unittest.TestCase):
    """因子写路径（双写 + 迁移前后一致性）"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_write_path.db')

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.create_table('factor_data')
        schema.create_table('daily_prices')
        conn.close()

        self.config = LoaderConfig(
            db_path=self.db_path,
            show_progress=False,
            skip_existing=False,
        )
        self.loader = DailyLoader(self.config)

    def tearDown(self):
        self.loader.conn.close()
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @staticmethod
    def _long_df(dates=('2024-01-02', '2024-01-03'), stocks=('000001', '000002'),
                 base=10.0):
        rows = []
        for i, d in enumerate(dates):
            for j, s in enumerate(stocks):
                rows.append({
                    'trade_date': pd.Timestamp(d).date(),
                    'stock_code': s,
                    'factor_value': base + i + j,
                })
        return pd.DataFrame(rows)

    def _count(self, sql: str) -> int:
        with self.loader.conn as conn:
            return conn.execute(sql).fetchone()[0]

    def _column_values(self, column: str):
        with self.loader.conn as conn:
            return conn.execute(
                f'SELECT {column} FROM factor_wide ORDER BY trade_date, stock_code'
            ).fetchdf()[column].tolist()

    # ========== 1. 迁移前：三表一致 ==========

    def test_write_factor_long_creates_tables(self):
        """迁移前写入应同时落到 factor_data / factor_history / factor_wide"""
        df = self._long_df()
        rows = self.loader.write_factor_long(df, 'pe')

        self.assertEqual(rows, 4)
        self.assertEqual(self._count("SELECT COUNT(*) FROM factor_data"), 4)
        self.assertEqual(self._count("SELECT COUNT(*) FROM factor_history"), 4)
        self.assertEqual(self._count("SELECT COUNT(*) FROM factor_wide"), 4)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_data WHERE factor_name = 'pe'"), 4
        )

        values = self._column_values('pe')
        self.assertEqual(values, [10.0, 11.0, 11.0, 12.0])

    def test_write_factor_long_rejects_missing_columns(self):
        """缺少 factor_value 列应抛 ValueError"""
        df = pd.DataFrame({
            'trade_date': [pd.Timestamp('2024-01-02').date()],
            'stock_code': ['000001'],
        })
        with self.assertRaises(ValueError):
            self.loader.write_factor_long(df, 'pe')

    def test_write_factor_long_rejects_empty(self):
        """空表应抛 ValueError"""
        df = pd.DataFrame(columns=['trade_date', 'stock_code', 'factor_value'])
        with self.assertRaises(ValueError):
            self.loader.write_factor_long(df, 'pe')

    def test_write_factor_long_rejects_bad_factor_name(self):
        """非法因子名应抛 ValueError"""
        df = self._long_df()
        with self.assertRaises(ValueError):
            self.loader.write_factor_long(df, 'bad name; DROP TABLE factor_wide')

    # ========== 2. 迁移后：写路径仍可用 ==========

    def test_write_factor_long_after_migration(self):
        """迁移后 factor_data 为视图，写入应成功且视图可读到新因子"""
        self.loader.write_factor_long(self._long_df(), 'pe')
        SchemaManager(self.loader.conn).migrate_factor_data_to_wide(batch_size=5)

        schema = SchemaManager(self.loader.conn)
        self.assertEqual(schema.object_type('factor_data'), 'VIEW')

        new_df = self._long_df(dates=('2024-01-04',), stocks=('000003',), base=20.0)
        rows = self.loader.write_factor_long(new_df, 'pb')

        self.assertEqual(rows, self._count('SELECT COUNT(*) FROM factor_wide'))
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_history WHERE factor_name = 'pb'"), 1
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_data WHERE factor_name = 'pb'"), 1
        )
        values = self._column_values('pb')
        self.assertEqual(values[-1], 20.0)
        self.assertTrue(np.isnan(values[:-1]).all())

    def test_factor_data_view_sees_new_columns_after_migration(self):
        """迁移后新增因子列应自动出现在 factor_data 视图"""
        self.loader.write_factor_long(self._long_df(), 'pe')
        SchemaManager(self.loader.conn).migrate_factor_data_to_wide(batch_size=5)

        self.loader.write_factor_long(
            self._long_df(dates=('2024-01-05',), stocks=('000009',), base=30.0),
            'new_factor',
        )

        self.assertEqual(
            self._count(
                "SELECT COUNT(*) FROM factor_data WHERE factor_name = 'new_factor'"
            ),
            1,
            "动态视图应包含迁移后新增的因子列",
        )

    def test_load_wide_factor_after_migration(self):
        """DailyLoader.load() 因子文件在迁移后仍可写入"""
        self.loader.write_factor_long(self._long_df(), 'pe')
        SchemaManager(self.loader.conn).migrate_factor_data_to_wide(batch_size=5)

        df = pd.DataFrame(
            [[1.5, 2.5], [1.6, 2.6]],
            index=pd.to_datetime(['2024-02-01', '2024-02-02']),
            columns=['000001', '000002'],
        )
        path = os.path.join(self.temp_dir, 'mom.pkl')
        df.to_pickle(path)

        from pathlib import Path
        count = self.loader.load(Path(path), field_name='mom')

        self.assertEqual(count, 4)
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_history WHERE factor_name = 'mom'"), 4
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_data WHERE factor_name = 'mom'"), 4
        )

    # ========== 3. OHLCV 行为不变 ==========

    def test_load_ohlcv_writes_daily_prices(self):
        """OHLCV 仍写 daily_prices，不进入因子表"""
        df = pd.DataFrame(
            [[10.0, 20.0], [11.0, 21.0]],
            index=pd.to_datetime(['2024-03-01', '2024-03-02']),
            columns=['000001', '000002'],
        )
        path = os.path.join(self.temp_dir, 'stock_close.pkl')
        df.to_pickle(path)

        from pathlib import Path
        count = self.loader.load(Path(path), field_name='close')

        self.assertEqual(count, 4)
        self.assertEqual(self._count('SELECT COUNT(*) FROM daily_prices'), 4)
        self.assertEqual(
            self._count(
                'SELECT COUNT(*) FROM daily_prices WHERE close IS NOT NULL'
            ),
            4,
        )
        self.assertEqual(self._count('SELECT COUNT(*) FROM factor_data'), 0)

    # ========== 4. CSMAR 路径 ==========

    def test_csmar_market_factor_after_migration(self):
        """CSMAR 市场因子在迁移后仍可写入"""
        self.loader.write_factor_long(self._long_df(), 'pe')
        SchemaManager(self.loader.conn).migrate_factor_data_to_wide(batch_size=5)

        csmar = CSMARLoader(self.config)
        try:
            df = pd.DataFrame({
                'trade_date': [pd.Timestamp('2024-01-10').date()],
                'MKT': [0.012],
            })
            count = csmar._import_market_factor(df, 'ff')
            self.assertEqual(count, 1)
            # 单值列时沿用原有命名约定：因子名不加列后缀
            self.assertEqual(
                self._count("SELECT COUNT(*) FROM factor_data WHERE factor_name = 'ff'"),
                1,
            )
            self.assertEqual(
                self._count("SELECT COUNT(*) FROM factor_history WHERE factor_name = 'ff'"),
                1,
            )
        finally:
            csmar.conn.close()

    # ========== 5. 迁移补列 ==========

    def test_migration_adds_missing_wide_columns(self):
        """已存在的部分宽表应被迁移补齐缺失因子列"""
        with self.loader.conn as conn:
            conn.execute("""
                INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                VALUES
                    (DATE '2024-01-02', '000001', 'pe', 10.0),
                    (DATE '2024-01-02', '000001', 'pb', 1.5)
            """)
        SchemaManager(self.loader.conn).create_factor_wide(['pe'])

        SchemaManager(self.loader.conn).migrate_factor_data_to_wide(batch_size=5)

        schema = SchemaManager(self.loader.conn)
        self.assertIn('pe', schema.get_factor_columns('factor_wide'))
        self.assertIn('pb', schema.get_factor_columns('factor_wide'))
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_data WHERE factor_name = 'pb'"), 1
        )
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM factor_history WHERE factor_name = 'pb'"), 1
        )


if __name__ == '__main__':
    unittest.main()
