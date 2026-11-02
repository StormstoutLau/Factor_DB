"""行业宏观敏感度测试（TDD 红阶段）

覆盖 MacroFactorLink.get_sector_sensitivity 的月度对齐、OLS beta、
级别/行业过滤、变换、样本下限与空表语义。
"""

import os
import shutil
import tempfile
import unittest
from datetime import date, timedelta

import numpy as np
import pandas as pd

from Factor_DB.analytics.macro_factor_link import MacroFactorLink
from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager

EXPECTED_COLUMNS = [
    'industry_code', 'industry_name', 'level', 'beta', 'correlation',
    't_stat', 'r_squared', 'n_obs', 'start_date', 'end_date',
]

MONTHS = pd.period_range('2023-01', periods=24, freq='M')

# 23 个月度变化（首月为基期，无变化）
CHANGES = [
    0.010, 0.020, -0.010, 0.030, 0.005, -0.020, 0.015, 0.010,
    -0.005, 0.020, 0.010, -0.015, 0.025, 0.005, -0.010, 0.020,
    0.015, -0.005, 0.010, 0.030, -0.020, 0.005, 0.012,
]

# 与 x 不完全共线的确定性噪声，保证 t 统计量有限
NOISE = [((i % 5) - 2) * 0.001 for i in range(len(CHANGES))]

MACRO_VALUES = [100.0]
for _c in CHANGES:
    MACRO_VALUES.append(MACRO_VALUES[-1] * (1 + _c))

RET_A = [0.0] + [2 * c + n for c, n in zip(CHANGES, NOISE)]
RET_B = [0.0] + [-0.5 * c + n for c, n in zip(CHANGES, NOISE)]
RET_C = [0.0] + [5 * c for c in CHANGES]
RET_D = [0.0] + [1.5 * c for c in CHANGES]
RET_SHORT = [3 * c for c in CHANGES[-6:]]

PMI_DIFFS = [
    0.2, -0.1, 0.3, -0.2, 0.1, 0.0, -0.1, 0.2, 0.1, -0.3, 0.2,
    0.1, -0.2, 0.3, 0.0, -0.1, 0.2, -0.2, 0.1, 0.3, -0.1, 0.1, 0.2,
]
PMI_VALUES = [50.0]
for _d in PMI_DIFFS:
    PMI_VALUES.append(PMI_VALUES[-1] + _d)


def _beta(x, y):
    """独立复算 OLS 斜率"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    return float(np.sum((x - x.mean()) * (y - y.mean())) / np.sum((x - x.mean()) ** 2))


class TestSectorSensitivity(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'sector.duckdb')
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).init_database()
        conn.close()
        DuckDBConnection._instances.clear()

        self._seed_macro('TEST_MACRO', MACRO_VALUES)
        self._seed_macro('CONST_MACRO', [100.0] * len(MONTHS))
        self._seed_macro('TEST_PMI', PMI_VALUES)
        self._seed_industry('801010', '行业A', 'first', RET_A)
        self._seed_industry('801020', '行业B', 'first', RET_B)
        self._seed_industry('801016', '二级行业', 'second', RET_C)

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ========== 数据准备 ==========

    def _seed_macro(self, indicator_id, values):
        rows = [
            (
                MONTHS[i].end_time.date(), indicator_id, indicator_id,
                float(values[i]), 'raw', 'test',
            )
            for i in range(len(values))
        ]
        conn = DuckDBConnection(self.db_path)
        with conn as c:
            df = pd.DataFrame(  # noqa: F841 - 供 DuckDB 的 FROM df 引用
                rows,
                columns=['trade_date', 'indicator_id', 'indicator_name',
                         'value', 'value_type', 'source_id'],
            )
            c.execute(
                'INSERT INTO macro_data (trade_date, indicator_id, indicator_name, '
                'value, value_type, source_id) SELECT * FROM df'
            )

    def _seed_industry(self, code, name, level, returns, dates=None):
        rows = []
        for i, ret in enumerate(returns):
            trade_date = dates[i] if dates else MONTHS[i].end_time.date()
            data_id = f'industry_return_{level}_{code}_{trade_date}'
            rows.append((
                data_id, trade_date, 'industry_return', level, 'industry',
                code, name, float(ret), 'test',
            ))
        conn = DuckDBConnection(self.db_path)
        with conn as c:
            df = pd.DataFrame(  # noqa: F841
                rows,
                columns=['data_id', 'trade_date', 'data_type', 'data_subtype',
                         'entity_type', 'entity_id', 'entity_name', 'value',
                         'source_id'],
            )
            c.execute(
                'INSERT INTO alternative_data (data_id, trade_date, data_type, '
                'data_subtype, entity_type, entity_id, entity_name, value, source_id) '
                'SELECT * FROM df'
            )

    def _seed_two_rows_per_month(self, code, name, monthly_returns):
        """每月两条日收益，复利结果等于给定的月收益"""
        rows = []
        for i, monthly in enumerate(monthly_returns):
            r1 = 0.01
            r2 = (1 + monthly) / (1 + r1) - 1
            for offset, ret in (
                (MONTHS[i].start_time.date() + timedelta(days=14), r1),
                (MONTHS[i].end_time.date(), r2),
            ):
                data_id = f'industry_return_first_{code}_{offset}_{ret}'
                rows.append((
                    data_id, offset, 'industry_return', 'first', 'industry',
                    code, name, float(ret), 'test',
                ))
        conn = DuckDBConnection(self.db_path)
        with conn as c:
            df = pd.DataFrame(  # noqa: F841
                rows,
                columns=['data_id', 'trade_date', 'data_type', 'data_subtype',
                         'entity_type', 'entity_id', 'entity_name', 'value',
                         'source_id'],
            )
            c.execute(
                'INSERT INTO alternative_data (data_id, trade_date, data_type, '
                'data_subtype, entity_type, entity_id, entity_name, value, source_id) '
                'SELECT * FROM df'
            )

    def _link(self):
        return MacroFactorLink(self.db_path)

    # ========== 用例 ==========

    def test_empty_when_macro_missing(self):
        """宏观指标不存在 → 带固定列的空表"""
        df = self._link().get_sector_sensitivity('NO_SUCH_INDICATOR')
        self.assertTrue(df.empty)
        self.assertEqual(EXPECTED_COLUMNS, list(df.columns))

    def test_beta_and_columns_for_first_level(self):
        """一级行业：beta 与手算一致、按 |beta| 降序、n_obs=23"""
        df = self._link().get_sector_sensitivity('TEST_MACRO')
        self.assertEqual(EXPECTED_COLUMNS, list(df.columns))
        self.assertEqual(['801010', '801020'], df['industry_code'].tolist())

        expected_a = _beta(CHANGES, RET_A[1:])
        expected_b = _beta(CHANGES, RET_B[1:])
        self.assertAlmostEqual(expected_a, float(df['beta'].iloc[0]), places=9)
        self.assertAlmostEqual(expected_b, float(df['beta'].iloc[1]), places=9)

        self.assertEqual(23, int(df['n_obs'].iloc[0]))
        self.assertEqual(23, int(df['n_obs'].iloc[1]))
        self.assertGreater(float(df['r_squared'].iloc[1]), 0.9)
        self.assertLessEqual(float(df['r_squared'].iloc[1]), 1.0)
        self.assertTrue(np.isfinite(df['t_stat'].iloc[0]))
        self.assertTrue(np.isfinite(df['correlation'].iloc[0]))

    def test_monthly_returns_are_compounded(self):
        """每月两条日收益按复利聚合，beta 与手算月收益一致"""
        self._seed_two_rows_per_month('801030', '行业D', RET_D)
        df = self._link().get_sector_sensitivity(
            'TEST_MACRO', sectors=['801030'], min_obs=6
        )
        self.assertEqual(1, len(df))
        expected = _beta(CHANGES, RET_D[1:])
        self.assertAlmostEqual(expected, float(df['beta'].iloc[0]), places=9)
        self.assertEqual(23, int(df['n_obs'].iloc[0]))

    def test_level_filter_and_invalid_level(self):
        """默认一级；second 只返回二级；None 返回全部；非法值抛 ValueError"""
        link = self._link()
        default = link.get_sector_sensitivity('TEST_MACRO')
        self.assertEqual({'801010', '801020'}, set(default['industry_code']))

        second = link.get_sector_sensitivity('TEST_MACRO', level='second')
        self.assertEqual(['801016'], second['industry_code'].tolist())

        everything = link.get_sector_sensitivity('TEST_MACRO', level=None)
        self.assertEqual(3, len(everything))

        with self.assertRaises(ValueError):
            link.get_sector_sensitivity('TEST_MACRO', level='third_level')

    def test_sectors_subset(self):
        """sectors 过滤只保留指定行业"""
        df = self._link().get_sector_sensitivity(
            'TEST_MACRO', sectors=['801020']
        )
        self.assertEqual(1, len(df))
        self.assertEqual('801020', df['industry_code'].iloc[0])

    def test_transform_diff_and_level(self):
        """diff/level 变换的 beta 与手算一致，且与 pct_change 不同"""
        link = self._link()
        diff_df = link.get_sector_sensitivity(
            'TEST_PMI', sectors=['801010'], transform='diff'
        )
        x_diff = np.diff(PMI_VALUES)
        self.assertAlmostEqual(
            _beta(x_diff, RET_A[1:]), float(diff_df['beta'].iloc[0]), places=9
        )

        pct_df = link.get_sector_sensitivity(
            'TEST_PMI', sectors=['801010'], transform='pct_change'
        )
        x_pct = pd.Series(PMI_VALUES).pct_change(fill_method=None).dropna().to_numpy()
        self.assertAlmostEqual(
            _beta(x_pct, RET_A[1:]), float(pct_df['beta'].iloc[0]), places=9
        )
        self.assertNotAlmostEqual(
            float(diff_df['beta'].iloc[0]), float(pct_df['beta'].iloc[0]), places=6
        )

        level_df = link.get_sector_sensitivity(
            'TEST_PMI', sectors=['801010'], transform='level'
        )
        self.assertAlmostEqual(
            _beta(PMI_VALUES, RET_A), float(level_df['beta'].iloc[0]), places=9
        )
        self.assertEqual(len(RET_A), int(level_df['n_obs'].iloc[0]))

        with self.assertRaises(ValueError):
            link.get_sector_sensitivity('TEST_PMI', transform='unknown')

    def test_min_obs_skips_short_history(self):
        """历史不足 min_obs 的行业被跳过，放宽后可返回"""
        short_dates = [m.end_time.date() for m in MONTHS[-6:]]
        self._seed_industry('801040', '短历史', 'first', RET_SHORT, short_dates)
        link = self._link()

        df = link.get_sector_sensitivity('TEST_MACRO')
        self.assertEqual({'801010', '801020'}, set(df['industry_code']))

        relaxed = link.get_sector_sensitivity('TEST_MACRO', min_obs=6)
        self.assertIn('801040', set(relaxed['industry_code']))

    def test_constant_macro_returns_empty(self):
        """宏观月度无变化（Sxx=0）→ 带列空表"""
        df = self._link().get_sector_sensitivity('CONST_MACRO')
        self.assertTrue(df.empty)
        self.assertEqual(EXPECTED_COLUMNS, list(df.columns))

    def test_min_obs_validation(self):
        """min_obs < 3 抛 ValueError"""
        with self.assertRaises(ValueError):
            self._link().get_sector_sensitivity('TEST_MACRO', min_obs=2)

    def test_date_range_and_output_dates(self):
        """日期过滤后 n_obs 与输出区间正确（首月无变化被丢弃）"""
        df = self._link().get_sector_sensitivity(
            'TEST_MACRO',
            sectors=['801010'],
            start_date=date(2023, 6, 1),
            end_date=date(2024, 12, 31),
        )
        self.assertEqual(1, len(df))
        self.assertEqual(18, int(df['n_obs'].iloc[0]))
        self.assertEqual(date(2023, 7, 1), df['start_date'].iloc[0])
        self.assertEqual(date(2024, 12, 31), df['end_date'].iloc[0])

    def test_industry_name_and_level_columns(self):
        """industry_name / level 与 alternative_data 一致"""
        df = self._link().get_sector_sensitivity('TEST_MACRO')
        row = df[df['industry_code'] == '801010'].iloc[0]
        self.assertEqual('行业A', row['industry_name'])
        self.assertEqual('first', row['level'])


if __name__ == '__main__':
    unittest.main()
