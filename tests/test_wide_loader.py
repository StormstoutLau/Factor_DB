"""
因子宽表加载器测试 (方案A: factor_wide + factor_history)

测试 DailyLoader 的宽表写入、增量更新、双写历史等功能。
"""

import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from loaders.base import LoaderConfig
from loaders.daily_loader import DailyLoader


class TestWideFactorLoader(unittest.TestCase):
    """宽表因子加载器测试类"""

    @staticmethod
    def _make_factor_df(n_dates=5, n_stocks=4, seed=42):
        """构造测试用因子 DataFrame (index=日期, columns=股票)"""
        np.random.seed(seed)
        dates = pd.date_range('2024-01-01', periods=n_dates, freq='B')
        stocks = [f'{code:06d}' for code in range(1, n_stocks + 1)]
        data = np.abs(np.random.randn(n_dates, n_stocks) * 3 + 10)
        return pd.DataFrame(data, index=dates, columns=stocks)

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_wide_loader.db')

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.create_factor_wide([])
        schema.create_factor_history()
        conn.close()

        self.config = LoaderConfig(
            db_path=self.db_path,
            show_progress=False,
            skip_existing=False
        )
        self.loader = DailyLoader(self.config)

    def tearDown(self):
        self.loader.conn.close()
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass
        os.rmdir(self.temp_dir)

    # ========== 1. 单因子宽表加载 ==========

    def test_load_single_factor_to_wide(self):
        """加载单因子到宽表"""
        df = self._make_factor_df()
        rows = self.loader.load_factor_to_wide(df, 'pe')

        self.assertEqual(rows, 20)  # 5 dates × 4 stocks

        with self.loader.conn as conn:
            wide_rows = conn.execute("SELECT COUNT(*) FROM factor_wide").fetchone()[0]
            hist_rows = conn.execute("SELECT COUNT(*) FROM factor_history").fetchone()[0]

        self.assertEqual(wide_rows, 20, "宽表应有 20 行")
        self.assertEqual(hist_rows, 20, "历史表应有 20 行")

        with self.loader.conn as conn:
            pe_val = conn.execute("""
                SELECT pe FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]

        self.assertIsNotNone(pe_val)
        self.assertGreater(pe_val, 0)

    def test_load_factor_auto_adds_column(self):
        """加载新因子自动添加列"""
        df = self._make_factor_df()

        self.loader.load_factor_to_wide(df, 'pe')
        factors_before = SchemaManager(self.loader.conn).get_factor_columns('factor_wide')
        self.assertIn('pe', factors_before)

        self.loader.load_factor_to_wide(df, 'pb')
        factors_after = SchemaManager(self.loader.conn).get_factor_columns('factor_wide')
        self.assertIn('pb', factors_after)
        self.assertIn('pe', factors_after)

    def test_load_factor_normalizes_stock_code(self):
        """加载时股票代码应标准化（去掉 .SZ/.SH 后缀）"""
        df = self._make_factor_df()
        df.columns = [f'{c}.SZ' for c in df.columns]

        self.loader.load_factor_to_wide(df, 'pe')

        with self.loader.conn as conn:
            codes = conn.execute(
                "SELECT DISTINCT stock_code FROM factor_wide ORDER BY stock_code"
            ).fetchall()

        for code_row in codes:
            self.assertFalse(
                code_row[0].endswith('.SZ') or code_row[0].endswith('.SH'),
                f"股票代码不应有后缀: {code_row[0]}"
            )
            self.assertEqual(len(code_row[0]), 6, f"应为 6 位数字: {code_row[0]}")

    # ========== 2. 增量更新 (Upsert) ==========

    def test_upsert_updates_existing(self):
        """同一 (date, stock) 再次加载应更新值和 loaded_at"""
        df1 = self._make_factor_df(seed=42)
        self.loader.load_factor_to_wide(df1, 'pe')

        with self.loader.conn as conn:
            old_val = conn.execute("""
                SELECT pe FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]
            old_loaded = conn.execute("""
                SELECT loaded_at FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]

        df2 = df1 * 2  # 所有值翻倍
        self.loader.load_factor_to_wide(df2, 'pe')

        with self.loader.conn as conn:
            new_val = conn.execute("""
                SELECT pe FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]
            new_loaded = conn.execute("""
                SELECT loaded_at FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]

        self.assertAlmostEqual(new_val, old_val * 2, "值应更新为 2 倍")
        self.assertGreaterEqual(new_loaded, old_loaded, "loaded_at 应更新")

    def test_upsert_preserves_other_factors(self):
        """更新一个因子不应影响其他因子列"""
        df_pe = self._make_factor_df(seed=42)
        df_pb = self._make_factor_df(seed=99)

        self.loader.load_factor_to_wide(df_pe, 'pe')
        self.loader.load_factor_to_wide(df_pb, 'pb')

        with self.loader.conn as conn:
            old_pb = conn.execute("""
                SELECT pb FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]

        df_pe2 = df_pe * 1.5
        self.loader.load_factor_to_wide(df_pe2, 'pe')

        with self.loader.conn as conn:
            new_pe = conn.execute("""
                SELECT pe FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]
            new_pb = conn.execute("""
                SELECT pb FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]

        self.assertAlmostEqual(new_pe, df_pe.iloc[0, 0] * 1.5, "pe 应更新")
        self.assertAlmostEqual(new_pb, old_pb, "pb 不应受影响")

    # ========== 3. 双写历史表 ==========

    def test_history_preserves_all_versions(self):
        """factor_history 应保留所有版本（PIT 审计）"""
        df1 = self._make_factor_df(seed=42)
        self.loader.load_factor_to_wide(df1, 'pe')

        df2 = df1 * 2
        self.loader.load_factor_to_wide(df2, 'pe')

        with self.loader.conn as conn:
            wide_rows = conn.execute("SELECT COUNT(*) FROM factor_wide").fetchone()[0]
            hist_rows = conn.execute("""
                SELECT COUNT(*) FROM factor_history WHERE factor_name = 'pe'
            """).fetchone()[0]
            versions = conn.execute("""
                SELECT COUNT(DISTINCT loaded_at) FROM factor_history WHERE factor_name = 'pe'
            """).fetchone()[0]

        self.assertEqual(wide_rows, 20, "宽表仍只有最新版本（20 行）")
        self.assertEqual(hist_rows, 40, "历史表应有 2 个版本（40 行）")
        self.assertEqual(versions, 2, "应有 2 个不同的 loaded_at 版本")

    def test_history_can_reconstruct_pit(self):
        """从 factor_history 可重建某个时间点的 PIT 视图"""
        df1 = self._make_factor_df(seed=42)
        self.loader.load_factor_to_wide(df1, 'pe')

        with self.loader.conn as conn:
            v1_time = conn.execute("SELECT MAX(loaded_at) FROM factor_history").fetchone()[0]

        df2 = df1 * 3
        self.loader.load_factor_to_wide(df2, 'pe')

        with self.loader.conn as conn:
            pit_val = conn.execute(f"""
                SELECT factor_value FROM factor_history
                WHERE factor_name = 'pe'
                  AND trade_date = '2024-01-01'
                  AND stock_code = '000001'
                  AND loaded_at <= '{v1_time}'
                ORDER BY loaded_at DESC
                LIMIT 1
            """).fetchone()[0]

        self.assertAlmostEqual(pit_val, df1.iloc[0, 0], "PIT 查询应返回 v1 的值")

    # ========== 4. 空值处理 ==========

    def test_skip_nan_values(self):
        """NaN 值不应写入历史表（保持历史表精简），宽表中 NaN 行不参与 upsert"""
        df_full = self._make_factor_df()
        self.loader.load_factor_to_wide(df_full, 'pe')

        df_nan = df_full.copy()
        df_nan.iloc[0, 0] = np.nan
        df_nan.iloc[2, 1] = np.nan
        self.loader.load_factor_to_wide(df_nan, 'pe')

        with self.loader.conn as conn:
            hist_count = conn.execute("""
                SELECT COUNT(*) FROM factor_history WHERE factor_name = 'pe'
            """).fetchone()[0]

        self.assertEqual(hist_count, 38, "第一次 20 行 + 第二次 18 行（跳过 2 个 NaN）")

        with self.loader.conn as conn:
            wide_pe = conn.execute("""
                SELECT pe FROM factor_wide
                WHERE trade_date = '2024-01-01' AND stock_code = '000001'
            """).fetchone()[0]

        self.assertAlmostEqual(wide_pe, df_full.iloc[0, 0], "宽表中 NaN 行不更新，保持原值")

    # ========== 5. 行数验证 ==========

    def test_load_factor_returns_correct_rows(self):
        """load_factor_to_wide 返回写入宽表的行数"""
        df = self._make_factor_df(n_dates=3, n_stocks=5)
        rows = self.loader.load_factor_to_wide(df, 'pe')
        self.assertEqual(rows, 15)  # 3 × 5


if __name__ == '__main__':
    unittest.main()
