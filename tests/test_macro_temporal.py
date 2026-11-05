"""宏观数据时间语义与 PIT 测试（P8-1 TDD）

覆盖：
    - core/macro_temporal.py 规则（8）
    - SchemaManager.migrate_macro_data_temporal（5）
    - MacroLoader 时间列与 source/name（5）
    - MacroQuery period_date/as_of（6）
"""

import os
import shutil
import tempfile
import unittest
from datetime import date, datetime

import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.macro_temporal import (
    MACRO_TEMPORAL_RULES,
    estimate_publish_date,
    is_legacy_release_series,
    resolve_period_date,
    resolve_publish_info,
)
from Factor_DB.core.schema import SchemaManager
from Factor_DB.loaders.base import LoaderConfig
from Factor_DB.loaders.macro_loader import MacroLoader
from Factor_DB.query.macro_query import MacroQuery

OLD_COLUMNS = [
    'trade_date', 'indicator_id', 'indicator_name', 'value',
    'value_type', 'data_quality', 'source_id',
]
TEMPORAL_COLUMNS = [
    'trade_date', 'period_date', 'publish_date', 'publish_date_quality',
    'loaded_at', 'indicator_id', 'indicator_name', 'value', 'value_type',
    'data_quality', 'source_id',
]


class TestMacroTemporalRules(unittest.TestCase):
    """规则模块纯函数测试"""

    def test_rules_cover_all_indicators(self):
        """12 个 AKShare 指标均有规则"""
        expected = {
            'GDP_YEARLY', 'GDP_QUARTERLY', 'CPI_YEARLY', 'CPI_MONTHLY',
            'PPI_YEARLY', 'PPI_MONTHLY', 'PMI_MANUFACTURING', 'M2_YEARLY',
            'M2_MONTHLY', 'INTEREST_RATE', 'FX_RESERVES', 'IMPORT_EXPORT',
        }
        self.assertEqual(expected, set(MACRO_TEMPORAL_RULES))

    def test_release_monthly_period_and_publish(self):
        """发布日型月度指标：period=前一月末，publish=原始日期"""
        raw = date(2025, 8, 9)
        self.assertEqual(date(2025, 7, 31), resolve_period_date('CPI_MONTHLY', raw))
        publish, quality = resolve_publish_info('CPI_MONTHLY', raw)
        self.assertEqual(raw, publish)
        self.assertEqual('exact', quality)

    def test_release_quarterly_period(self):
        """发布日型季度指标：period=上一季季末"""
        raw = date(2024, 4, 16)
        self.assertEqual(date(2024, 3, 31), resolve_period_date('GDP_YEARLY', raw))

    def test_period_start_month_end(self):
        """统计期起点指标统一到月末"""
        self.assertEqual(
            date(2026, 10, 31),
            resolve_period_date('PMI_MANUFACTURING', date(2026, 10, 1)),
        )
        self.assertEqual(
            date(2026, 9, 30),
            resolve_period_date('PPI_MONTHLY', date(2026, 9, 1)),
        )

    def test_period_end_preserved(self):
        """统计期末指标保持季末"""
        raw = date(2026, 9, 30)
        self.assertEqual(raw, resolve_period_date('GDP_QUARTERLY', raw))
        publish, quality = resolve_publish_info('GDP_QUARTERLY', raw)
        self.assertEqual(date(2026, 10, 17), publish)
        self.assertEqual('estimated', quality)

    def test_legacy_day_one_unknown(self):
        """发布日型早期月首记录：period=月末、publish=None、quality=unknown"""
        raw = date(2010, 5, 1)
        self.assertEqual(date(2010, 5, 31), resolve_period_date('CPI_MONTHLY', raw))
        publish, quality = resolve_publish_info('CPI_MONTHLY', raw)
        self.assertIsNone(publish)
        self.assertEqual('unknown', quality)
        self.assertTrue(is_legacy_release_series('CPI_MONTHLY', raw))

    def test_unknown_indicator_raw_date(self):
        """未知指标不推导、不平移"""
        raw = date(2024, 1, 1)
        self.assertEqual(raw, resolve_period_date('UNKNOWN', raw))
        publish, quality = resolve_publish_info('UNKNOWN', raw)
        self.assertIsNone(publish)
        self.assertEqual('unknown', quality)

    def test_estimate_publish_date_rules(self):
        """CPI 次月 9 日；PMI 2 月次月 4 日；GDP 季末+17 天"""
        self.assertEqual(
            date(2025, 8, 9), estimate_publish_date('CPI_MONTHLY', date(2025, 7, 31))
        )
        self.assertEqual(
            date(2026, 3, 4),
            estimate_publish_date('PMI_MANUFACTURING', date(2026, 2, 28)),
        )
        self.assertEqual(
            date(2026, 10, 17),
            estimate_publish_date('GDP_QUARTERLY', date(2026, 9, 30)),
        )


class _TemporalDBTest(unittest.TestCase):
    """临时库基类"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'temporal.duckdb')
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()
        DuckDBConnection._instances.clear()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _insert_macro(self, rows, columns=OLD_COLUMNS):
        conn = DuckDBConnection(self.db_path)
        try:
            with conn as c:
                df = pd.DataFrame(rows, columns=columns)  # noqa: F841
                c.execute(
                    f'INSERT INTO macro_data ({", ".join(columns)}) '
                    f'SELECT {", ".join(columns)} FROM df'
                )
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

    def _fetch(self, sql):
        conn = DuckDBConnection(self.db_path, read_only=True)
        try:
            with conn as c:
                return c.execute(sql).fetchdf()
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

    def _migrate(self):
        conn = DuckDBConnection(self.db_path)
        try:
            return SchemaManager(conn).migrate_macro_data_temporal()
        finally:
            conn.close()
            DuckDBConnection._instances.clear()


class TestMacroTemporalMigration(_TemporalDBTest):
    """Schema 迁移测试"""

    def test_fresh_schema_has_temporal_columns_and_indexes(self):
        conn = DuckDBConnection(self.db_path, read_only=True)
        try:
            with conn as c:
                columns = [
                    row[1] for row in
                    c.execute("PRAGMA table_info('macro_data')").fetchall()
                ]
            schema = SchemaManager(conn)
            self.assertIn('period_date', columns)
            self.assertIn('publish_date', columns)
            self.assertIn('publish_date_quality', columns)
            self.assertIn('loaded_at', columns)
            self.assertTrue(schema.index_exists('idx_macro_period_date'))
            self.assertTrue(schema.index_exists('idx_macro_publish_date'))
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

    def test_migrate_backfills_release_series(self):
        self._insert_macro([
            (date(2025, 8, 9), 'CPI_MONTHLY', 'CPI_MONTHLY', 0.4, 'raw', 100, None),
            (date(2010, 5, 1), 'CPI_MONTHLY', 'CPI_MONTHLY', 0.2, 'raw', 100, None),
        ])
        stats = self._migrate()
        self.assertEqual(2, stats['rows_total'])

        df = self._fetch(
            "SELECT trade_date, period_date, publish_date, publish_date_quality "
            "FROM macro_data ORDER BY trade_date"
        )
        recent = df[df['trade_date'].astype(str).str.startswith('2025-08-09')].iloc[0]
        self.assertEqual('2025-07-31', str(recent['period_date'])[:10])
        self.assertEqual('2025-08-09', str(recent['publish_date'])[:10])
        self.assertEqual('exact', recent['publish_date_quality'])

        legacy = df[df['trade_date'].astype(str).str.startswith('2010-05-01')].iloc[0]
        self.assertEqual('2010-05-31', str(legacy['period_date'])[:10])
        self.assertTrue(pd.isna(legacy['publish_date']))
        self.assertEqual('unknown', legacy['publish_date_quality'])

    def test_migrate_backfills_period_start_and_quarter(self):
        self._insert_macro([
            (date(2026, 10, 1), 'PMI_MANUFACTURING', 'PMI_MANUFACTURING',
             49.3, 'raw', 100, None),
            (date(2026, 9, 30), 'GDP_QUARTERLY', 'GDP_QUARTERLY',
             1071071.2, 'raw', 100, None),
        ])
        self._migrate()
        df = self._fetch(
            "SELECT indicator_id, period_date, publish_date, publish_date_quality "
            "FROM macro_data ORDER BY indicator_id"
        )
        pmi = df[df['indicator_id'] == 'PMI_MANUFACTURING'].iloc[0]
        self.assertEqual('2026-10-31', str(pmi['period_date'])[:10])
        self.assertEqual('2026-10-31', str(pmi['publish_date'])[:10])
        self.assertEqual('estimated', pmi['publish_date_quality'])

        gdp = df[df['indicator_id'] == 'GDP_QUARTERLY'].iloc[0]
        self.assertEqual('2026-09-30', str(gdp['period_date'])[:10])
        self.assertEqual('2026-10-17', str(gdp['publish_date'])[:10])
        self.assertEqual('estimated', gdp['publish_date_quality'])

    def test_migrate_idempotent_and_stats(self):
        self._insert_macro([
            (date(2025, 8, 9), 'CPI_MONTHLY', 'CPI_MONTHLY', 0.4, 'raw', 100, None),
        ])
        first = self._migrate()
        second = self._migrate()
        for key in (
            'rows_total', 'period_date_filled', 'publish_date_filled',
            'publish_date_unknown', 'duplicate_period_rows',
        ):
            self.assertIn(key, first)
        self.assertEqual(first['rows_total'], second['rows_total'])
        df = self._fetch("SELECT COUNT(*) AS n FROM macro_data")
        self.assertEqual(1, int(df['n'].iloc[0]))

    def test_migrate_source_name_and_m2_zero_anomaly(self):
        conn = DuckDBConnection(self.db_path)
        try:
            with conn as c:
                c.execute(
                    "INSERT INTO macro_indicators (indicator_id, indicator_name) "
                    "VALUES ('M2_YEARLY', 'M2同比')"
                )
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

        self._insert_macro([
            (date(2020, 2, 17), 'M2_YEARLY', 'M2_YEARLY', 0.0, 'raw', 100, None),
            (date(2020, 2, 20), 'M2_YEARLY', 'M2_YEARLY', 8.4, 'raw', 100, None),
        ])
        self._migrate()
        df = self._fetch(
            "SELECT trade_date, indicator_name, source_id, data_quality "
            "FROM macro_data ORDER BY trade_date"
        )
        self.assertTrue((df['source_id'] == 'akshare_macro').all())
        self.assertTrue((df['indicator_name'] == 'M2同比').all())
        zero = df[df['trade_date'].astype(str).str.startswith('2020-02-17')].iloc[0]
        normal = df[df['trade_date'].astype(str).str.startswith('2020-02-20')].iloc[0]
        self.assertEqual(50, int(zero['data_quality']))
        self.assertEqual(100, int(normal['data_quality']))


class TestMacroLoaderTemporal(_TemporalDBTest):
    """MacroLoader 时间列测试"""

    def _load(self, df, **kwargs):
        loader = MacroLoader(LoaderConfig(db_path=self.db_path))
        try:
            return loader.load(df, **kwargs)
        finally:
            loader.conn.close()
            DuckDBConnection._instances.clear()

    def test_loader_derives_period_and_publish(self):
        df = pd.DataFrame({
            'trade_date': [date(2025, 8, 9)],
            'indicator_id': ['CPI_MONTHLY'],
            'value': [0.4],
            'value_type': ['raw'],
        })
        self.assertEqual(1, self._load(df))
        row = self._fetch(
            "SELECT period_date, publish_date, publish_date_quality FROM macro_data"
        ).iloc[0]
        self.assertEqual('2025-07-31', str(row['period_date'])[:10])
        self.assertEqual('2025-08-09', str(row['publish_date'])[:10])
        self.assertEqual('exact', row['publish_date_quality'])

    def test_loader_preserves_publish_date_on_reload(self):
        first = pd.DataFrame({
            'trade_date': [date(2025, 8, 9)],
            'indicator_id': ['CPI_MONTHLY'],
            'value': [0.4],
            'value_type': ['raw'],
            'period_date': [date(2025, 7, 31)],
            'publish_date': [date(2025, 8, 9)],
            'publish_date_quality': ['exact'],
        })
        self._load(first)
        second = pd.DataFrame({
            'trade_date': [date(2025, 8, 9)],
            'indicator_id': ['CPI_MONTHLY'],
            'value': [0.5],
            'value_type': ['raw'],
            'period_date': [date(2025, 7, 31)],
        })
        self._load(second)
        row = self._fetch(
            "SELECT value, publish_date, publish_date_quality FROM macro_data"
        ).iloc[0]
        self.assertEqual(0.5, float(row['value']))
        self.assertEqual('2025-08-09', str(row['publish_date'])[:10])
        self.assertEqual('exact', row['publish_date_quality'])

    def test_loader_records_source_and_name(self):
        conn = DuckDBConnection(self.db_path)
        try:
            with conn as c:
                c.execute(
                    "INSERT INTO macro_indicators (indicator_id, indicator_name) "
                    "VALUES ('CPI_MONTHLY', 'CPI环比')"
                )
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

        df = pd.DataFrame({
            'trade_date': [date(2025, 8, 9)],
            'indicator_id': ['CPI_MONTHLY'],
            'value': [0.4],
            'value_type': ['raw'],
        })
        self._load(df, source_id='akshare_macro')
        row = self._fetch(
            "SELECT indicator_name, source_id FROM macro_data"
        ).iloc[0]
        self.assertEqual('CPI环比', row['indicator_name'])
        self.assertEqual('akshare_macro', row['source_id'])

    def test_loader_unknown_indicator_fallback(self):
        df = pd.DataFrame({
            'trade_date': [date(2024, 1, 1)],
            'indicator_id': ['UNKNOWN'],
            'value': [1.0],
            'value_type': ['raw'],
        })
        self._load(df)
        row = self._fetch(
            "SELECT period_date, publish_date, publish_date_quality FROM macro_data"
        ).iloc[0]
        self.assertEqual('2024-01-01', str(row['period_date'])[:10])
        self.assertTrue(pd.isna(row['publish_date']))
        self.assertEqual('unknown', row['publish_date_quality'])

    def test_loader_explicit_temporal_columns_override(self):
        df = pd.DataFrame({
            'trade_date': [date(2025, 8, 9)],
            'indicator_id': ['CPI_MONTHLY'],
            'value': [0.4],
            'value_type': ['raw'],
            'period_date': [date(2025, 6, 30)],
            'publish_date': [date(2025, 7, 1)],
            'publish_date_quality': ['exact'],
        })
        self._load(df)
        row = self._fetch(
            "SELECT period_date, publish_date, publish_date_quality FROM macro_data"
        ).iloc[0]
        self.assertEqual('2025-06-30', str(row['period_date'])[:10])
        self.assertEqual('2025-07-01', str(row['publish_date'])[:10])
        self.assertEqual('exact', row['publish_date_quality'])


class TestMacroQueryPIT(_TemporalDBTest):
    """MacroQuery period_date/as_of 测试"""

    def _seed_cpi(self):
        self._insert_macro([
            (date(2025, 8, 9), date(2025, 7, 31), date(2025, 8, 9), 'exact',
             datetime(2026, 11, 4, 10, 0), 'CPI_MONTHLY', 'CPI_MONTHLY',
             0.4, 'raw', 100, 'akshare_macro'),
        ], columns=TEMPORAL_COLUMNS)

    def test_output_has_period_alias_and_publish(self):
        self._seed_cpi()
        df = MacroQuery(self.db_path).get_macro_data(['CPI_MONTHLY'])
        self.assertEqual(1, len(df))
        for col in ('period_date', 'publish_date', 'publish_date_quality'):
            self.assertIn(col, df.columns)
        self.assertEqual('2025-07-31', str(df['trade_date'].iloc[0])[:10])
        self.assertEqual('2025-08-09', str(df['publish_date'].iloc[0])[:10])

    def test_as_of_bounds_release(self):
        self._seed_cpi()
        query = MacroQuery(self.db_path)
        self.assertTrue(
            query.get_macro_data(['CPI_MONTHLY'], as_of=date(2025, 8, 5)).empty
        )
        self.assertEqual(
            1,
            len(query.get_macro_data(['CPI_MONTHLY'], as_of=date(2025, 8, 15))),
        )

    def test_null_publish_uses_loaded_at(self):
        self._insert_macro([
            (date(2025, 8, 9), date(2025, 7, 31), None, 'unknown',
             datetime(2026, 11, 4, 10, 0), 'CPI_MONTHLY', 'CPI_MONTHLY',
             0.4, 'raw', 100, 'akshare_macro'),
        ], columns=TEMPORAL_COLUMNS)
        query = MacroQuery(self.db_path)
        self.assertTrue(
            query.get_macro_data(['CPI_MONTHLY'], as_of=date(2026, 11, 1)).empty
        )
        self.assertEqual(
            1,
            len(query.get_macro_data(['CPI_MONTHLY'], as_of=date(2026, 11, 5))),
        )

    def test_duplicate_period_selects_latest(self):
        self._insert_macro([
            (date(2020, 2, 17), date(2020, 1, 31), date(2020, 2, 17), 'exact',
             datetime(2026, 11, 4), 'M2_YEARLY', 'M2_YEARLY',
             0.0, 'raw', 50, 'akshare_macro'),
            (date(2020, 2, 20), date(2020, 1, 31), date(2020, 2, 20), 'exact',
             datetime(2026, 11, 4), 'M2_YEARLY', 'M2_YEARLY',
             8.4, 'raw', 100, 'akshare_macro'),
        ], columns=TEMPORAL_COLUMNS)
        query = MacroQuery(self.db_path)
        latest = query.get_macro_data(['M2_YEARLY'])
        self.assertEqual(1, len(latest))
        self.assertEqual(8.4, float(latest['value'].iloc[0]))

        as_of_early = query.get_macro_data(['M2_YEARLY'], as_of=date(2020, 2, 18))
        self.assertEqual(1, len(as_of_early))
        self.assertEqual(0.0, float(as_of_early['value'].iloc[0]))

    def test_legacy_rows_fallback(self):
        self._insert_macro([
            (date(2024, 1, 1), 'CPI', 'CPI', 2.1, 'raw', 100, None),
            (date(2024, 2, 1), 'CPI', 'CPI', 2.3, 'raw', 100, None),
        ])
        df = MacroQuery(self.db_path).get_macro_data(['CPI'])
        self.assertEqual(2, len(df))
        self.assertEqual('2024-01-01', str(df['trade_date'].iloc[0])[:10])
        self.assertTrue(pd.isna(df['period_date'].iloc[0]))

    def test_matrix_as_of_and_other_queries(self):
        self._insert_macro([
            (date(2025, 7, 9), date(2025, 6, 30), date(2025, 7, 9), 'exact',
             datetime(2026, 11, 4), 'CPI_MONTHLY', 'CPI_MONTHLY',
             2.0, 'raw', 100, 'akshare_macro'),
            (date(2025, 8, 9), date(2025, 7, 31), date(2025, 8, 9), 'exact',
             datetime(2026, 11, 4), 'CPI_MONTHLY', 'CPI_MONTHLY',
             2.1, 'raw', 100, 'akshare_macro'),
        ], columns=TEMPORAL_COLUMNS)
        query = MacroQuery(self.db_path)

        matrix = query.get_macro_matrix(['CPI_MONTHLY'], as_of=date(2025, 8, 1))
        self.assertEqual(1, len(matrix))
        self.assertEqual('2025-06-30', str(matrix.index[0])[:10])

        self.assertEqual(2, query.get_indicator_stats('CPI_MONTHLY')['count'])
        latest = query.get_latest_values(['CPI_MONTHLY'])
        self.assertEqual(2.1, float(latest['value'].iloc[0]))
        self.assertEqual('2025-07-31', str(latest['latest_date'].iloc[0])[:10])

        ranged = query.get_macro_by_date_range(
            date(2025, 7, 1), date(2025, 7, 31)
        )
        self.assertEqual(1, len(ranged))
        self.assertEqual('2025-07-31', str(ranged['trade_date'].iloc[0])[:10])

        yoy = query.calculate_yoy('CPI_MONTHLY', periods=1)
        self.assertEqual(1, len(yoy))
        self.assertAlmostEqual(5.0, float(yoy['yoy'].iloc[0]))


if __name__ == '__main__':
    unittest.main()
