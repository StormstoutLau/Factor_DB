"""CLI 冒烟测试

覆盖 argparse 分发与只读查询命令，避免入口长期无测试。
"""

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from Factor_DB.cli import main
from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager


class TestCli(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'cli.duckdb')

    def tearDown(self):
        DuckDBConnection._instances.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _seed(self):
        conn = DuckDBConnection(self.db_path)
        SchemaManager(conn).create_table('daily_prices')
        with conn as c:
            c.execute(
                "INSERT INTO daily_prices (trade_date, stock_code, close) "
                "VALUES ('2024-01-02', '000001', 10.0)"
            )
        conn.close()
        DuckDBConnection._instances.clear()

    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            main(argv)
        return out.getvalue()

    def test_no_command_prints_help(self):
        """无子命令时打印帮助"""
        output = self._run([])
        self.assertIn('usage', output)
        self.assertIn('factor-db', output)

    def test_stats_lists_table_counts(self):
        """stats 输出各表行数"""
        self._seed()
        output = self._run(['stats', '--db', self.db_path])
        self.assertIn('daily_prices: 1 rows', output)

    def test_query_prints_dataframe(self):
        """query 执行 SQL 并打印结果"""
        self._seed()
        output = self._run(['query', '--db', self.db_path, '--sql', 'SELECT 1 AS x'])
        self.assertIn('x', output)

    def test_list_factors_reads_view(self):
        """list-factors 从 factor_data 视图读取因子名"""
        self._seed()
        conn = DuckDBConnection(self.db_path)
        with conn as c:
            c.execute(
                "CREATE VIEW factor_data AS SELECT DATE '2024-01-02' AS trade_date, "
                "'000001' AS stock_code, 'pe' AS factor_name, 1.0 AS factor_value"
            )
        conn.close()
        DuckDBConnection._instances.clear()

        output = self._run(['list-factors', '--db', self.db_path])
        self.assertIn('pe', output)

    def test_cache_clear_prints_message(self):
        """cache-clear 输出提示"""
        output = self._run(['cache-clear'])
        self.assertIn('缓存', output)

    def test_validate_missing_db_returns_2(self):
        """validate 在数据库缺失时返回环境错误码 2"""
        err = io.StringIO()
        with redirect_stderr(err):
            code = main(['validate', '--db', os.path.join(self.temp_dir, 'missing.duckdb')])
        self.assertEqual(code, 2)
        self.assertIn('环境错误', err.getvalue())


if __name__ == '__main__':
    unittest.main()
