"""五列宏观指标新鲜度端到端测试（P8-1b TDD）

覆盖：采集（history + freshness）→ MacroLoader → MacroQuery 全链路：
    - 4 指标最新统计期推进到 2026-09，source_id 统一
    - 历史行（<= 2025-07-31）不变
    - 重复加载幂等且保留 publish_date
    - PIT 边界（2025-09-05 排除 2025-08 期，2025-09-15 包含）
    - GDP_YEARLY 不进入 freshness
    - freshness 失败时 history 仍可用
"""

import os
import shutil
import tempfile
import unittest
from datetime import date

import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from Factor_DB.loaders.base import LoaderConfig
from Factor_DB.loaders.macro_loader import MacroLoader
from Factor_DB.osint.collectors.akshare_collector import AKShareMacroCollector
from Factor_DB.query.macro_query import MacroQuery

FRESHNESS_INDICATORS = ['CPI_MONTHLY', 'CPI_YEARLY', 'PPI_YEARLY', 'M2_YEARLY']


class HistoryAK:
    """金十 five-column（history 阶段）"""

    @staticmethod
    def macro_china_cpi_monthly():
        return pd.DataFrame([
            {'商品': 'CPI', '日期': '2025-08-09', '今值': 0.4, '预测值': 0.5, '前值': 0.1},
            {'商品': 'CPI', '日期': '2025-09-10', '今值': None, '预测值': 0.2, '前值': 0.4},
        ])

    @staticmethod
    def macro_china_cpi_yearly():
        return pd.DataFrame([
            {'商品': 'CPI', '日期': '2025-08-09', '今值': 0.0, '预测值': 0.1, '前值': -0.1},
        ])

    @staticmethod
    def macro_china_ppi_yearly():
        return pd.DataFrame([
            {'商品': 'PPI', '日期': '2025-08-09', '今值': -3.6, '预测值': -3.4, '前值': -3.6},
        ])

    @staticmethod
    def macro_china_m2_yearly():
        return pd.DataFrame([
            {'商品': 'M2', '日期': '2025-08-13', '今值': 8.8, '预测值': 8.9, '前值': 8.3},
        ])

    @staticmethod
    def macro_china_gdp_yearly():
        return pd.DataFrame([
            {'商品': 'GDP', '日期': '2025-07-15', '今值': 5.2, '预测值': 5.1, '前值': 5.4},
        ])


class DualAK(HistoryAK):
    """东财 / 月度表（freshness 阶段）"""

    @staticmethod
    def macro_china_cpi():
        return pd.DataFrame([
            {'月份': '2025年07月份', '全国-当月': 100.4,
             '全国-同比增长': 0.0, '全国-环比增长': 0.4},
            {'月份': '2025年08月份', '全国-当月': 99.6,
             '全国-同比增长': -0.4, '全国-环比增长': 0.0},
            {'月份': '2025年09月份', '全国-当月': 99.7,
             '全国-同比增长': -0.3, '全国-环比增长': 0.1},
            {'月份': '2026年09月份', '全国-当月': 100.2,
             '全国-同比增长': 0.9, '全国-环比增长': 0.2},
        ])

    @staticmethod
    def macro_china_ppi():
        return pd.DataFrame([
            {'月份': '2025年07月份', '当月': 96.4, '当月同比增长': -3.6},
            {'月份': '2025年08月份', '当月': 97.1, '当月同比增长': -2.9},
            {'月份': '2026年09月份', '当月': 105.0, '当月同比增长': 5.0},
        ])

    @staticmethod
    def macro_china_money_supply():
        return pd.DataFrame([
            {'月份': '2025年07月份',
             '货币和准货币(M2)-数量(亿元)': 3299400.0,
             '货币和准货币(M2)-同比增长': 8.8},
            {'月份': '2025年08月份',
             '货币和准货币(M2)-数量(亿元)': 3319100.0,
             '货币和准货币(M2)-同比增长': 8.8},
            {'月份': '2026年09月份',
             '货币和准货币(M2)-数量(亿元)': 3596289.70,
             '货币和准货币(M2)-同比增长': 7.2},
        ])


class BrokenAK(DualAK):
    """freshness 抛异常（history 正常）"""

    @staticmethod
    def macro_china_cpi():
        raise RuntimeError('freshness boom')


class _FreshnessDBTest(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'freshness.duckdb')
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()
        DuckDBConnection._instances.clear()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _collect(self, indicators, ak):
        collector = AKShareMacroCollector()
        collector._ak = ak
        collector._error_state = False
        frames = [
            collector.collect({'indicator': indicator})
            for indicator in indicators
        ]
        frames = [df for df in frames if not df.empty]
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def _load(self, df):
        loader = MacroLoader(LoaderConfig(db_path=self.db_path))
        try:
            return loader.load(df, source_id='akshare_macro')
        finally:
            loader.conn.close()
            DuckDBConnection._instances.clear()

    def _fetch(self, sql):
        conn = DuckDBConnection(self.db_path, read_only=True)
        try:
            with conn as c:
                return c.execute(sql).fetchdf()
        finally:
            conn.close()
            DuckDBConnection._instances.clear()

    def _query(self, indicators, as_of=None):
        query = MacroQuery(self.db_path)
        try:
            return query.get_macro_data(indicators, as_of=as_of)
        finally:
            query.conn.close()
            DuckDBConnection._instances.clear()


class TestMacroFreshnessEndToEnd(_FreshnessDBTest):

    def test_end_to_end_latest_period_and_source_id(self):
        df = self._collect(FRESHNESS_INDICATORS, DualAK())
        self.assertEqual(len(df), self._load(df))
        result = self._fetch(
            "SELECT indicator_id, MAX(period_date) AS latest, "
            "MAX(source_id) AS source_id FROM macro_data GROUP BY indicator_id"
        )
        self.assertEqual(set(FRESHNESS_INDICATORS), set(result['indicator_id']))
        self.assertTrue((result['latest'].astype(str) == '2026-09-30').all())
        self.assertTrue((result['source_id'] == 'akshare_macro').all())

    def test_history_rows_preserved(self):
        history = self._collect(['CPI_MONTHLY'], HistoryAK())
        self._load(history)
        snapshot_sql = (
            "SELECT trade_date, period_date, publish_date, value "
            "FROM macro_data WHERE indicator_id = 'CPI_MONTHLY' "
            "AND period_date <= DATE '2025-07-31' ORDER BY trade_date"
        )
        before = self._fetch(snapshot_sql)

        dual = self._collect(['CPI_MONTHLY'], DualAK())
        self._load(dual)
        after = self._fetch(snapshot_sql)
        pd.testing.assert_frame_equal(before, after)

    def test_reload_idempotent_and_publish_preserved(self):
        df = self._collect(['CPI_MONTHLY'], DualAK())
        self._load(df)
        first = self._fetch("SELECT COUNT(*) AS n FROM macro_data")['n'].iloc[0]
        self._load(df)
        second = self._fetch("SELECT COUNT(*) AS n FROM macro_data")['n'].iloc[0]
        self.assertEqual(int(first), int(second))

        row = self._fetch(
            "SELECT period_date, publish_date, publish_date_quality "
            "FROM macro_data WHERE indicator_id = 'CPI_MONTHLY' "
            "AND period_date = DATE '2025-08-31'"
        ).iloc[0]
        self.assertEqual('2025-09-09', str(row['publish_date'])[:10])
        self.assertEqual('estimated', row['publish_date_quality'])

    def test_pit_boundary(self):
        self._load(self._collect(['CPI_MONTHLY'], DualAK()))

        early = self._query(['CPI_MONTHLY'], as_of=date(2025, 9, 5))
        self.assertEqual('2025-07-31', str(early['trade_date'].max())[:10])

        late = self._query(['CPI_MONTHLY'], as_of=date(2025, 9, 15))
        self.assertEqual('2025-08-31', str(late['trade_date'].max())[:10])

    def test_gdp_yearly_not_written(self):
        df = self._collect(FRESHNESS_INDICATORS + ['GDP_YEARLY'], DualAK())
        self._load(df)
        row = self._fetch(
            "SELECT MAX(period_date) AS latest, COUNT(*) AS n "
            "FROM macro_data WHERE indicator_id = 'GDP_YEARLY'"
        ).iloc[0]
        self.assertEqual('2025-06-30', str(row['latest'])[:10])
        self.assertEqual(1, int(row['n']))

    def test_freshness_failure_keeps_history_usable(self):
        df = self._collect(['CPI_MONTHLY'], BrokenAK())
        self.assertEqual(len(df), self._load(df))
        data = self._query(['CPI_MONTHLY'])
        self.assertEqual('2025-07-31', str(data['trade_date'].max())[:10])
        row = data[data['trade_date'].astype(str).str.startswith('2025-07-31')]
        self.assertAlmostEqual(0.4, float(row['value'].iloc[0]), places=6)


if __name__ == '__main__':
    unittest.main()
