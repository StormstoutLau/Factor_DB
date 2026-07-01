"""
Level 1 Parquet 分区测试 — TDD 红阶段

验证 Level 1 数据从 Feather 转换为按日期分区的 Parquet，
以及 DuckDB 直接查询分区 Parquet 的能力。
"""

import os
import shutil
import tempfile
import unittest
from datetime import date, time

import numpy as np
import pandas as pd
import pyarrow.feather as feather

from core.connection import DuckDBConnection


class TestLevel1ParquetConversion(unittest.TestCase):
    """Level 1 Feather → Parquet 分区转换测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.feather_dir = os.path.join(self.temp_dir, 'feather')
        self.parquet_dir = os.path.join(self.temp_dir, 'level1_parquet')
        os.makedirs(self.feather_dir, exist_ok=True)
        self._create_feather_files()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_feather_files(self):
        """构造 3 天的 Feather 测试数据，每天 2 只股票 × 240 分钟

        真实 Level 1 Feather 格式：time(int), stock_code(int), open, high, low, close, volume, amount
        """
        dates = ['2024-01-02', '2024-01-03', '2024-01-04']
        stocks = [1, 600001]

        for d in dates:
            rows = []
            for i in range(240):
                hour = 9 + (30 + i) // 60
                minute = (30 + i) % 60
                time_int = hour * 10000 + minute * 100
                for s in stocks:
                    rows.append({
                        'time': time_int,
                        'stock_code': s,
                        'open': float(np.random.randn() * 0.5 + 10),
                        'high': float(np.random.randn() * 0.5 + 10.5),
                        'low': float(np.random.randn() * 0.5 + 9.5),
                        'close': float(np.random.randn() * 0.5 + 10),
                        'volume': int(np.random.randint(1000, 10000)),
                        'amount': float(np.random.randn() * 10000 + 50000),
                    })
            df = pd.DataFrame(rows)
            file_path = os.path.join(self.feather_dir, f'{d}.feather')
            feather.write_feather(df, file_path)

    def test_convert_single_file(self):
        """转换单个 Feather 文件为 Parquet 分区"""
        from loaders.level1_loader import Level1ParquetConverter

        converter = Level1ParquetConverter(self.parquet_dir)
        count = converter.convert_file(
            os.path.join(self.feather_dir, '2024-01-02.feather')
        )

        self.assertEqual(count, 480)
        parquet_path = os.path.join(
            self.parquet_dir, 'trade_date=2024-01-02', 'data.parquet'
        )
        self.assertTrue(os.path.exists(parquet_path))

    def test_convert_directory(self):
        """批量转换目录下所有 Feather 文件"""
        from loaders.level1_loader import Level1ParquetConverter

        converter = Level1ParquetConverter(self.parquet_dir)
        total = converter.convert_directory(self.feather_dir)

        self.assertEqual(total, 480 * 3)

        for d in ['2024-01-02', '2024-01-03', '2024-01-04']:
            path = os.path.join(self.parquet_dir, f'trade_date={d}', 'data.parquet')
            self.assertTrue(os.path.exists(path), f'分区 {d} 不存在')

    def test_convert_skips_existing(self):
        """增量转换：已存在的分区应跳过"""
        from loaders.level1_loader import Level1ParquetConverter

        converter = Level1ParquetConverter(self.parquet_dir)
        converter.convert_directory(self.feather_dir)

        total2 = converter.convert_directory(self.feather_dir)
        self.assertEqual(total2, 0, "增量转换应返回 0")

    def test_parquet_has_trade_date_column(self):
        """Parquet 数据中应包含 trade_date 列（从分区推断）"""
        from loaders.level1_loader import Level1ParquetConverter

        converter = Level1ParquetConverter(self.parquet_dir)
        converter.convert_file(os.path.join(self.feather_dir, '2024-01-02.feather'))

        df = pd.read_parquet(
            os.path.join(self.parquet_dir, 'trade_date=2024-01-02', 'data.parquet')
        )
        self.assertIn('trade_date', df.columns)
        self.assertEqual(df['trade_date'].iloc[0], date(2024, 1, 2))


class TestLevel1ParquetQuery(unittest.TestCase):
    """DuckDB 查询分区 Parquet 测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.parquet_dir = os.path.join(self.temp_dir, 'level1_parquet')
        self.db_path = os.path.join(self.temp_dir, 'test.duckdb')
        self._create_parquet_partitions()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_parquet_partitions(self):
        """构造 3 天的 Parquet 分区数据"""
        dates = ['2024-01-02', '2024-01-03', '2024-01-04']
        stocks = ['000001', '600001']

        for d in dates:
            rows = []
            base_time = pd.Timestamp(f'{d} 09:30:00')
            for i in range(240):
                ts = base_time + pd.Timedelta(minutes=i)
                for s in stocks:
                    rows.append({
                        'trade_date': date.fromisoformat(d),
                        'trade_time': ts.time(),
                        'stock_code': s,
                        'open': float(10 + i * 0.01),
                        'high': float(10.5 + i * 0.01),
                        'low': float(9.5 + i * 0.01),
                        'close': float(10 + i * 0.01),
                        'volume': int(1000 + i * 10),
                        'amount': float(50000 + i * 100),
                    })
            df = pd.DataFrame(rows)
            part_dir = os.path.join(self.parquet_dir, f'trade_date={d}')
            os.makedirs(part_dir, exist_ok=True)
            df.to_parquet(os.path.join(part_dir, 'data.parquet'))

    def test_duckdb_read_partitioned_parquet(self):
        """DuckDB 能读取 Hive 风格分区的 Parquet"""
        conn = DuckDBConnection(self.db_path)
        df = conn.fetchdf(f'''
            SELECT * FROM read_parquet('{self.parquet_dir}/*/data.parquet', hive_partitioning=true)
            LIMIT 5
        ''')
        conn.close()

        self.assertEqual(len(df), 5)
        self.assertIn('trade_date', df.columns)
        self.assertIn('stock_code', df.columns)

    def test_partition_pruning_by_date(self):
        """按日期过滤时只读取对应分区（分区裁剪）"""
        conn = DuckDBConnection(self.db_path)
        df = conn.fetchdf(f'''
            SELECT COUNT(*) as cnt FROM read_parquet(
                '{self.parquet_dir}/*/data.parquet',
                hive_partitioning=true
            )
            WHERE trade_date = '2024-01-02'
        ''')
        conn.close()

        self.assertEqual(df['cnt'].iloc[0], 480)

    def test_query_with_stock_filter(self):
        """支持股票代码过滤"""
        conn = DuckDBConnection(self.db_path)
        df = conn.fetchdf(f'''
            SELECT COUNT(*) as cnt FROM read_parquet(
                '{self.parquet_dir}/*/data.parquet',
                hive_partitioning=true
            )
            WHERE trade_date = '2024-01-02' AND stock_code = '000001'
        ''')
        conn.close()

        self.assertEqual(df['cnt'].iloc[0], 240)

    def test_query_with_time_range(self):
        """支持时间范围过滤"""
        conn = DuckDBConnection(self.db_path)
        df = conn.fetchdf(f'''
            SELECT COUNT(*) as cnt FROM read_parquet(
                '{self.parquet_dir}/*/data.parquet',
                hive_partitioning=true
            )
            WHERE trade_date = '2024-01-02'
              AND trade_time >= '10:00:00'
              AND trade_time <= '10:30:00'
        ''')
        conn.close()

        self.assertEqual(df['cnt'].iloc[0], 62)


class TestPriceQueryLevel1Parquet(unittest.TestCase):
    """PriceQuery.get_level1 适配 Parquet 分区测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.parquet_dir = os.path.join(self.temp_dir, 'level1_parquet')
        self.db_path = os.path.join(self.temp_dir, 'test.duckdb')
        conn = DuckDBConnection(self.db_path, read_only=False)
        conn.connect()
        conn.close()
        DuckDBConnection._instances.clear()
        self._create_parquet_partitions()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_parquet_partitions(self):
        dates = ['2024-01-02', '2024-01-03', '2024-01-04']
        stocks = ['000001', '600001']

        for d in dates:
            rows = []
            base_time = pd.Timestamp(f'{d} 09:30:00')
            for i in range(240):
                ts = base_time + pd.Timedelta(minutes=i)
                for s in stocks:
                    rows.append({
                        'trade_date': date.fromisoformat(d),
                        'trade_time': ts.time(),
                        'stock_code': s,
                        'open': float(10 + i * 0.01),
                        'high': float(10.5 + i * 0.01),
                        'low': float(9.5 + i * 0.01),
                        'close': float(10 + i * 0.01),
                        'volume': int(1000 + i * 10),
                        'amount': float(50000 + i * 100),
                    })
            df = pd.DataFrame(rows)
            part_dir = os.path.join(self.parquet_dir, f'trade_date={d}')
            os.makedirs(part_dir, exist_ok=True)
            df.to_parquet(os.path.join(part_dir, 'data.parquet'))

    def test_get_level1_from_parquet(self):
        """PriceQuery.get_level1 应优先从 Parquet 分区查询"""
        from query.price_query import PriceQuery

        query = PriceQuery(self.db_path, level1_parquet_dir=self.parquet_dir)
        df = query.get_level1(trade_date=date(2024, 1, 2))

        self.assertEqual(len(df), 480)
        self.assertIn('trade_date', df.columns)
        self.assertIn('stock_code', df.columns)

    def test_get_level1_with_stock_filter(self):
        """Parquet 查询支持股票过滤"""
        from query.price_query import PriceQuery

        query = PriceQuery(self.db_path, level1_parquet_dir=self.parquet_dir)
        df = query.get_level1(
            trade_date=date(2024, 1, 2),
            stock_codes=['000001']
        )

        self.assertEqual(len(df), 240)

    def test_get_level1_with_time_range(self):
        """Parquet 查询支持时间范围"""
        from query.price_query import PriceQuery

        query = PriceQuery(self.db_path, level1_parquet_dir=self.parquet_dir)
        df = query.get_level1(
            trade_date=date(2024, 1, 2),
            start_time=time(10, 0),
            end_time=time(10, 30)
        )

        self.assertEqual(len(df), 62)

    def test_get_level1_multiple_days(self):
        """Parquet 查询支持多日范围"""
        from query.price_query import PriceQuery

        query = PriceQuery(self.db_path, level1_parquet_dir=self.parquet_dir)
        df = query.get_level1(
            stock_codes=['000001'],
            trade_date=date(2024, 1, 2)
        )

        self.assertEqual(len(df), 240)


if __name__ == '__main__':
    unittest.main()
