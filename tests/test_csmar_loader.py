"""
CSMAR 加载器测试

覆盖：
- 尾号分片合并（1,000,000 行切分）
- 周/月/年日期解析
- 市场级维度 → 伪实体编码
- 个股级维度 → 因子名后缀
- history_only 写入行为（不膨胀宽表）
- 明细类表跳过
"""

import os
import shutil
import tempfile
import unittest

import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from Factor_DB.loaders.base import LoaderConfig
from Factor_DB.loaders.csmar_loader import CSMARLoader


class TestCSMARLoader(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'csmar.db')

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.create_factor_history()
        schema.create_table('factor_info')
        conn.close()

        self.config = LoaderConfig(
            db_path=self.db_path,
            show_progress=False,
            temp_directory=os.path.join(self.temp_dir, 'tmp'),
        )
        self.loader = CSMARLoader(self.config, history_only=True)

    def tearDown(self):
        self.loader.conn.close()
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _rows(self, sql: str):
        with self.loader.conn as conn:
            return conn.execute(sql).fetchall()

    # ---------- 命名 / 日期 ----------

    def test_base_factor_name_merges_chunks(self):
        self.assertEqual(
            CSMARLoader._base_factor_name('HF_BSImbalance12'), 'HF_BSImbalance'
        )
        self.assertEqual(
            CSMARLoader._base_factor_name('LIQ_AMIHUD_D3'), 'LIQ_AMIHUD_D'
        )
        self.assertEqual(
            CSMARLoader._base_factor_name('STK_MKT_THRFACMONTH'),
            'STK_MKT_THRFACMONTH',
        )

    def test_parse_week_month_year_dates(self):
        self.loader._date_kind = 'week'
        weeks = self.loader._parse_dates(pd.Series(['1990-53', '1991-01']))
        self.assertEqual(str(weeks.iloc[0].date()), '1990-12-31')
        self.assertEqual(str(weeks.iloc[1].date()), '1991-01-01')

        self.loader._date_kind = 'month'
        months = self.loader._parse_dates(pd.Series(['2000-01', '2000-12']))
        self.assertEqual(str(months.iloc[0].date()), '2000-01-01')

        self.loader._date_kind = 'year'
        years = self.loader._parse_dates(pd.Series(['1999', '2025']))
        self.assertEqual(str(years.iloc[0].date()), '1999-01-01')

    # ---------- 市场级 ----------

    def test_market_level_dimensions_go_to_entity(self):
        df = pd.DataFrame({
            'TradingMonth': ['2020-01', '2020-01', '2020-02', '2020-02'],
            'MarkettypeID': ['P9706', 'P9709', 'P9706', 'P9709'],
            'MktRF': [1.0, 2.0, 3.0, 4.0],
            'SMB': [5.0, 6.0, 7.0, 8.0],
        })
        self.loader._import_frame(df, 'FF3', 'test')

        names = self._rows('SELECT DISTINCT factor_name FROM factor_history')
        self.assertEqual(
            sorted(n[0] for n in names), ['FF3_MktRF', 'FF3_SMB']
        )
        entities = self._rows('SELECT DISTINCT stock_code FROM factor_history')
        self.assertEqual(
            sorted(e[0] for e in entities), ['MKT_MT_P9706', 'MKT_MT_P9709']
        )
        self.assertEqual(
            self._rows('SELECT COUNT(*) FROM factor_history')[0][0], 8
        )

    # ---------- 个股级 ----------

    def test_stock_level_dimensions_go_to_factor_name(self):
        df = pd.DataFrame({
            'Stkcd': [1, 1, 1, 1],
            'Trdwnt': ['2020-01', '2020-01', '2020-02', '2020-02'],
            'FormationPeriod': [1, 3, 1, 3],
            'Ret': [0.1, 0.2, 0.3, 0.4],
        })
        self.loader._import_frame(df, 'RANK', 'test')

        names = self._rows('SELECT DISTINCT factor_name FROM factor_history')
        self.assertEqual(
            sorted(n[0] for n in names), ['RANK_F_1', 'RANK_F_3']
        )
        self.assertEqual(
            self._rows('SELECT DISTINCT stock_code FROM factor_history'),
            [('000001',)],
        )
        self.assertEqual(
            self._rows('SELECT COUNT(*) FROM factor_history')[0][0], 4
        )

    def test_constant_attribute_not_used_as_dimension(self):
        """个股属性列（如市场类型）不应拆分因子名"""
        df = pd.DataFrame({
            'Stkcd': [1, 1, 2, 2],
            'Trddt': ['2020-01-02', '2020-01-03', '2020-01-02', '2020-01-03'],
            'MarketType': [1, 1, 2, 2],
            'ILLIQ': [0.1, 0.2, 0.3, 0.4],
        })
        self.loader._import_frame(df, 'AMIHUD', 'test')
        names = self._rows('SELECT DISTINCT factor_name FROM factor_history')
        self.assertEqual([n[0] for n in names], ['AMIHUD'])

    def test_vpin_n_used_as_dimension(self):
        """HF_VPIN 的 N（篮子数）必须作为维度，否则去重会丢一半行"""
        df = pd.DataFrame({
            'Stkcd': [600000] * 4,
            'Trddt': ['2020-01-02', '2020-01-02', '2020-01-03', '2020-01-03'],
            'Volume': [1e6, 1e6, 2e6, 2e6],
            'N': [50, 8, 50, 8],
            'VPIN': [0.3, 0.1, 0.4, 0.2],
        })
        count = self.loader._import_frame(df, 'HF_VPIN', 'test')

        self.assertEqual(count, 8)  # 2 日期 × 2 篮子数 × (Volume, VPIN)
        names = sorted(r[0] for r in self._rows(
            'SELECT DISTINCT factor_name FROM factor_history'))
        self.assertEqual(names, [
            'HF_VPIN_VPIN_N_50', 'HF_VPIN_VPIN_N_8',
            'HF_VPIN_Volume_N_50', 'HF_VPIN_Volume_N_8',
        ])

    def test_vpin_n_not_dimension_for_other_tables(self):
        """其他表的同名列 N 不应当作维度"""
        df = pd.DataFrame({
            'Stkcd': [600000, 600001],
            'Trddt': ['2020-01-02', '2020-01-02'],
            'N': [50, 8],
            'VPIN': [0.3, 0.1],
        })
        self.loader._import_frame(df, 'OTHER_VPIN', 'test')
        names = sorted(r[0] for r in self._rows(
            'SELECT DISTINCT factor_name FROM factor_history'))
        self.assertEqual(names, ['OTHER_VPIN_N', 'OTHER_VPIN_VPIN'])

    def test_history_rows_have_loaded_at_without_default(self):
        """生产 factor_history 由迁移重建、无 DEFAULT，写入必须显式给 loaded_at"""
        with self.loader.conn as conn:
            conn.execute('DROP TABLE factor_history')
            conn.execute('''
                CREATE TABLE factor_history (
                    trade_date DATE, stock_code VARCHAR, factor_name VARCHAR,
                    factor_value DOUBLE, loaded_at TIMESTAMPTZ
                )
            ''')
        df = pd.DataFrame({
            'Stkcd': [1, 2],
            'Trddt': ['2020-01-02', '2020-01-02'],
            'PE': [10.0, 20.0],
        })
        self.loader._import_frame(df, 'PE', 'test')

        rows, with_ts = self._rows(
            'SELECT COUNT(*), COUNT(loaded_at) FROM factor_history')[0]
        self.assertEqual(rows, 2)
        self.assertEqual(with_ts, 2)

    # ---------- 指数 / 行业级 ----------

    def test_index_level_entity(self):
        df = pd.DataFrame({
            'Indcd': [300, 399001],
            'Trddt': ['2020-01-02', '2020-01-02'],
            'RV': [0.1, 0.2],
        })
        self.loader._import_frame(df, 'HF_IndexRealized', 'test')
        entities = self._rows('SELECT DISTINCT stock_code FROM factor_history')
        self.assertEqual(sorted(e[0] for e in entities),
                         ['IDX000300', 'IDX399001'])

    def test_industry_level_entity(self):
        df = pd.DataFrame({
            'Nnindcd': ['A01', 'B02'],
            'Trddt': ['2020-01-02', '2020-01-02'],
            'CSSD1': [0.1, 0.2],
        })
        self.loader._import_frame(df, 'BF_IHEAD', 'test')
        entities = self._rows('SELECT DISTINCT stock_code FROM factor_history')
        self.assertEqual(sorted(e[0] for e in entities), ['INDA01', 'INDB02'])

    # ---------- history_only ----------

    def test_history_only_does_not_touch_wide(self):
        schema = SchemaManager(self.loader.conn)
        self.assertFalse(schema.table_exists('factor_wide'))

        df = pd.DataFrame({
            'Stkcd': [1, 2],
            'Trddt': ['2020-01-02', '2020-01-02'],
            'PE': [10.0, 20.0],
        })
        self.loader._import_frame(df, 'PE', 'test')

        self.assertFalse(SchemaManager(self.loader.conn).table_exists('factor_wide'))
        self.assertEqual(
            self._rows('SELECT COUNT(*) FROM factor_history')[0][0], 2
        )
        info = self._rows(
            "SELECT factor_name, category FROM factor_info WHERE factor_type='csmar'"
        )
        self.assertEqual(info, [('PE', 'test')])

    def test_skip_detail_tables(self):
        """股东/高管明细表应跳过且记录导入日志"""
        from pathlib import Path

        fake = Path(self.temp_dir) / 'BF_HOLDER.xlsx'
        count = self.loader.load_xlsx(fake, source_key='fake/BF_HOLDER.xlsx')
        self.assertEqual(count, 0)
        self.assertEqual(
            self._rows('SELECT COUNT(*) FROM factor_history')[0][0], 0
        )
        logs = self._rows('SELECT stem, rows FROM csmar_import_log')
        self.assertEqual(logs, [('BF_HOLDER', 0)])

    def test_dgtw_assignment_groups_become_values(self):
        """DGTW 股票分配表的 4 个分组标识应作为因子值导入"""
        df = pd.DataFrame({
            'Symbol': [1, 2],
            'TradingYear': ['2020', '2020'],
            'MarketValue': [1, 3],
            'BooktoMarket': [2, 4],
            'Momentum': [1, 5],
            'IsNotBSE': [1, 1],
        })
        self.loader._import_frame(df, 'STK_MKT_DGTWASSINGMENTS', 'test')
        names = sorted(
            n[0] for n in self._rows('SELECT DISTINCT factor_name FROM factor_history')
        )
        self.assertEqual(names, [
            'STK_MKT_DGTWASSINGMENTS_BooktoMarket',
            'STK_MKT_DGTWASSINGMENTS_IsNotBSE',
            'STK_MKT_DGTWASSINGMENTS_MarketValue',
            'STK_MKT_DGTWASSINGMENTS_Momentum',
        ])
        self.assertEqual(
            self._rows('SELECT COUNT(*) FROM factor_history')[0][0], 8
        )


if __name__ == '__main__':
    unittest.main()
