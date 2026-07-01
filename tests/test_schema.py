"""
数据库表结构测试

测试 SchemaManager 的表创建、索引创建等功能。
"""

import os
import tempfile
import unittest

from core.connection import DuckDBConnection
from core.schema import SchemaManager


class TestSchemaManager(unittest.TestCase):
    """SchemaManager 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_schema.db')
        self.conn = DuckDBConnection(self.db_path)
        self.schema = SchemaManager(self.conn)

    def tearDown(self):
        """测试后清理"""
        self.conn.close()
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_create_single_table(self):
        """测试创建单个表"""
        result = self.schema.create_table('daily_prices')
        self.assertTrue(result)
        self.assertTrue(self.schema.table_exists('daily_prices'))

    def test_create_all_tables(self):
        """测试创建所有表"""
        results = self.schema.create_all_tables()
        self.assertTrue(all(results.values()), f"部分表创建失败: {results}")

        # 验证每个表都存在
        for table_name in SchemaManager.TABLES:
            self.assertTrue(self.schema.table_exists(table_name))

    def test_create_index(self):
        """测试创建索引"""
        # 先创建表
        self.schema.create_table('daily_prices')

        # 创建索引
        result = self.schema.create_index('idx_daily_date')
        self.assertTrue(result)

    def test_table_exists(self):
        """测试表存在检查"""
        self.assertFalse(self.schema.table_exists('nonexistent'))

        self.schema.create_table('stock_info')
        self.assertTrue(self.schema.table_exists('stock_info'))

    def test_get_table_stats(self):
        """测试获取表统计信息"""
        self.schema.create_table('daily_prices')

        # 插入测试数据
        with self.conn as conn:
            conn.execute("""
                INSERT INTO daily_prices (trade_date, stock_code, close)
                VALUES ('2024-01-01', '000001.SZ', 10.5)
            """)

        stats = self.schema.get_table_stats('daily_prices')
        self.assertIsNotNone(stats)
        self.assertEqual(stats['row_count'], 1)
        self.assertIn('columns', stats)

    def test_init_database(self):
        """测试完整数据库初始化"""
        result = self.schema.init_database()
        self.assertTrue(result)

        # 验证所有表和索引
        for table_name in SchemaManager.TABLES:
            self.assertTrue(self.schema.table_exists(table_name))

    # ========== P0-2: 索引管理 ==========

    def test_drop_index(self):
        """测试删除单个索引"""
        self.schema.create_table('daily_prices')
        self.schema.create_index('idx_daily_date')

        # 验证索引存在
        self.assertTrue(self.schema.index_exists('idx_daily_date'))

        # 删除索引
        result = self.schema.drop_index('idx_daily_date')
        self.assertTrue(result)

        # 验证索引已删除
        self.assertFalse(self.schema.index_exists('idx_daily_date'))

    def test_drop_nonexistent_index(self):
        """测试删除不存在的索引"""
        result = self.schema.drop_index('nonexistent_index')
        self.assertTrue(result)  # DROP IF EXISTS 不报错

    def test_drop_and_recreate_factor_indexes(self):
        """测试批量删除和重建 factor_data 索引"""
        self.schema.create_table('factor_data')
        self.schema.create_all_indexes()

        # 验证 factor_data 索引存在
        factor_indexes = [
            'idx_factor_name_date',
            'idx_factor_code_date',
            'idx_factor_pit',
        ]
        for idx_name in factor_indexes:
            self.assertTrue(
                self.schema.index_exists(idx_name),
                f"索引 {idx_name} 应存在"
            )

        # 批量删除
        result = self.schema.drop_factor_indexes()
        self.assertTrue(result)

        # 验证已删除
        for idx_name in factor_indexes:
            self.assertFalse(
                self.schema.index_exists(idx_name),
                f"索引 {idx_name} 应已删除"
            )

        # 重建
        result = self.schema.recreate_factor_indexes()
        self.assertTrue(result)

        # 验证已重建
        for idx_name in factor_indexes:
            self.assertTrue(
                self.schema.index_exists(idx_name),
                f"索引 {idx_name} 应已重建"
            )

    def test_drop_factor_indexes_idempotent(self):
        """测试重复删除 factor_data 索引不报错"""
        self.schema.create_table('factor_data')
        self.schema.create_all_indexes()

        # 第一次删除
        self.assertTrue(self.schema.drop_factor_indexes())
        # 第二次删除（幂等）
        self.assertTrue(self.schema.drop_factor_indexes())

    # ========== P1: safe_only 模式 — 跳过 OOM 索引 ==========

    def test_recreate_factor_indexes_safe_only(self):
        """recreate_factor_indexes(safe_only=True) 只创建 safe 索引"""
        self.schema.create_table('factor_data')

        # safe_only=True 应只创建 idx_factor_name_date
        result = self.schema.recreate_factor_indexes(safe_only=True)
        self.assertTrue(result)

        self.assertTrue(
            self.schema.index_exists('idx_factor_name_date'),
            "safe_only 模式应创建 idx_factor_name_date"
        )
        self.assertFalse(
            self.schema.index_exists('idx_factor_code_date'),
            "safe_only 模式不应创建 idx_factor_code_date (OOM 风险)"
        )
        self.assertFalse(
            self.schema.index_exists('idx_factor_pit'),
            "safe_only 模式不应创建 idx_factor_pit (OOM 风险)"
        )

    def test_recreate_factor_indexes_default_creates_all(self):
        """recreate_factor_indexes() 默认行为不变 — 创建全部 3 个索引"""
        self.schema.create_table('factor_data')

        result = self.schema.recreate_factor_indexes()
        self.assertTrue(result)

        for idx_name in ['idx_factor_name_date', 'idx_factor_code_date', 'idx_factor_pit']:
            self.assertTrue(
                self.schema.index_exists(idx_name),
                f"默认模式应创建 {idx_name}"
            )

    def test_init_database_safe_only(self):
        """init_database(safe_only=True) 跳过 OOM 索引"""
        result = self.schema.init_database(safe_only=True)
        self.assertTrue(result)

        # 安全的 factor_data 索引应存在
        self.assertTrue(
            self.schema.index_exists('idx_factor_name_date'),
            "safe_only 模式应创建 idx_factor_name_date"
        )
        # OOM 风险索引不应存在
        self.assertFalse(
            self.schema.index_exists('idx_factor_code_date'),
            "safe_only 模式不应创建 idx_factor_code_date (OOM 风险)"
        )
        self.assertFalse(
            self.schema.index_exists('idx_factor_pit'),
            "safe_only 模式不应创建 idx_factor_pit (OOM 风险)"
        )

        # 非 factor_data 索引应正常创建
        self.assertTrue(
            self.schema.index_exists('idx_daily_date'),
            "safe_only 不应影响 daily_prices 索引"
        )

    def test_init_database_default_creates_all(self):
        """init_database() 默认行为不变 — 创建全部索引"""
        result = self.schema.init_database()
        self.assertTrue(result)

        for idx_name in ['idx_factor_name_date', 'idx_factor_code_date', 'idx_factor_pit']:
            self.assertTrue(
                self.schema.index_exists(idx_name),
                f"默认模式应创建 {idx_name}"
            )


if __name__ == '__main__':
    unittest.main()
