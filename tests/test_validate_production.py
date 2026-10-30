"""生产库端到端验收脚本测试（临时库 fixture）

验证 scripts/validate_production.py 的编排与判定逻辑，不触碰真实生产库。
"""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from loaders.base import LoaderConfig
from loaders.daily_loader import DailyLoader

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

import validate_production as vp  # noqa: E402


class TestValidateProduction(unittest.TestCase):
    """验收脚本的临时库测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'e2e.duckdb')
        self._seed()

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ========== fixture ==========

    def _seed(self):
        config = LoaderConfig(
            db_path=self.db_path, show_progress=False, skip_existing=False
        )
        loader = DailyLoader(config)
        dates = pd.date_range('2024-01-01', periods=5, freq='D')
        stocks = ['000001', '000002', '600001', '600002']
        df_pe = pd.DataFrame(
            np.random.RandomState(42).randn(5, 4) * 5 + 15,
            index=dates, columns=stocks
        )
        loader.load_factor_to_wide(df_pe, 'pe')
        loader.conn.close()
        DuckDBConnection._instances.clear()

        conn = DuckDBConnection(self.db_path)
        schema = SchemaManager(conn)
        schema.create_table('daily_prices')
        schema.create_table('factor_info')
        with conn as c:
            c.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close, loaded_at) VALUES "
                "('2024-01-01','000001',10.0,'2024-01-03 08:00:00'),"
                "('2024-01-01','000002',20.0,'2024-01-03 08:00:00'),"
                "('2024-01-01','600001',30.0,'2024-01-03 08:00:00'),"
                "('2024-01-01','600002',40.0,'2024-01-03 08:00:00'),"
                "('2024-01-02','000001',11.0,'2024-01-03 08:00:00'),"
                "('2024-01-02','000002',21.0,'2024-01-03 08:00:00'),"
                "('2024-01-02','600001',29.0,'2024-01-03 08:00:00'),"
                "('2024-01-02','600002',42.0,'2024-01-03 08:00:00')"
            )
            c.execute(
                "INSERT INTO factor_history "
                "(trade_date, stock_code, factor_name, factor_value, loaded_at) VALUES "
                "('2024-01-01','000001','hist_only',1.0,'2024-01-03 08:00:00'),"
                "('2024-01-02','000001','hist_only',2.0,'2024-01-03 08:00:00'),"
                "('2024-01-03','000001','hist_only',3.0,'2024-01-03 08:00:00')"
            )
            c.execute("INSERT INTO factor_info (factor_name, description) "
                      "VALUES ('hist_only', 'test history-only factor')")
            c.execute(
                "CREATE VIEW factor_data AS "
                "SELECT trade_date, stock_code, 'pe' AS factor_name, pe AS factor_value "
                "FROM factor_wide"
            )
        conn.close()
        DuckDBConnection._instances.clear()

    def _status(self, report, check_id):
        for check in report['checks']:
            if check['id'] == check_id:
                return check['status']
        raise AssertionError(f'报告缺少 {check_id}')

    # ========== 用例 ==========

    def test_run_validation_all_pass(self):
        """正常库：S1–S5 全部无硬性失败"""
        report = vp.run_validation(
            self.db_path, start=date(2024, 1, 1), end=date(2024, 1, 3)
        )
        self.assertEqual(report['summary']['fail'], 0)
        self.assertEqual({c['id'] for c in report['checks']},
                         {'S1', 'S2', 'S3', 'S4', 'S5'})

    def test_run_validation_missing_table_fails(self):
        """缺 daily_prices：S4 失败且报告仍完整"""
        conn = DuckDBConnection(self.db_path)
        with conn as c:
            c.execute('DROP TABLE daily_prices')
        conn.close()
        DuckDBConnection._instances.clear()

        report = vp.run_validation(
            self.db_path, start=date(2024, 1, 1), end=date(2024, 1, 3)
        )
        self.assertEqual(len(report['checks']), 5)
        self.assertEqual(self._status(report, 'S4'), 'fail')

    def test_render_markdown_contains_conclusion(self):
        """Markdown 报告包含结论行与各步骤"""
        report = vp.run_validation(
            self.db_path, start=date(2024, 1, 1), end=date(2024, 1, 3)
        )
        md = vp.render_markdown(report)
        self.assertIn('E2E_VALIDATION=', md)
        for sid in ('S1', 'S2', 'S3', 'S4', 'S5'):
            self.assertIn(sid, md)

    def test_ic_out_of_daily_range_warns(self):
        """窗口早于 daily_prices 覆盖范围：S5 降级为 warn 而非 fail"""
        report = vp.run_validation(
            self.db_path, start=date(2019, 1, 1), end=date(2019, 12, 31)
        )
        self.assertEqual(self._status(report, 'S5'), 'warn')


if __name__ == '__main__':
    unittest.main()
