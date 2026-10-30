"""
连接管理模块测试

测试 DuckDBConnection 的单例模式、上下文管理器等功能。
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path

from Factor_DB.core.connection import DuckDBConnection


class TestDuckDBConnection(unittest.TestCase):
    """DuckDBConnection 测试类"""

    def setUp(self):
        """测试前准备"""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        # 预先创建数据库文件，确保只读连接可以打开
        import duckdb
        conn = duckdb.connect(self.db_path)
        conn.execute("CREATE TABLE dummy (id INTEGER)")
        conn.close()

    def tearDown(self):
        """测试后清理"""
        DuckDBConnection._instances.clear()
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        os.rmdir(self.temp_dir)

    def test_singleton_pattern(self):
        """测试单例模式"""
        conn1 = DuckDBConnection(self.db_path)
        conn2 = DuckDBConnection(self.db_path)

        self.assertIs(conn1, conn2, "相同路径应返回同一实例")

    def test_read_write_separation(self):
        """测试读写分离"""
        write_conn = DuckDBConnection(self.db_path, read_only=False)
        read_conn = DuckDBConnection(self.db_path, read_only=True)

        self.assertIsNot(write_conn, read_conn, "读写模式应返回不同实例")

    def test_context_manager(self):
        """测试上下文管理器"""
        with DuckDBConnection(self.db_path) as conn:
            result = conn.execute("SELECT 1").fetchone()
            self.assertEqual(result[0], 1)

    def test_execute_and_fetch(self):
        """测试执行和获取数据"""
        conn = DuckDBConnection(self.db_path)

        # 创建测试表
        conn.execute("CREATE TABLE test (id INTEGER, name VARCHAR)")
        conn.execute("INSERT INTO test VALUES (1, 'hello')")

        # 查询
        df = conn.fetchdf("SELECT * FROM test")
        self.assertEqual(len(df), 1)
        self.assertEqual(df['name'].iloc[0], 'hello')

    def test_thread_safety(self):
        """测试线程安全 - DuckDB 连接单线程下需要独立连接"""
        results = []
        lock = threading.Lock()

        def worker():
            # 每个线程使用独立的数据库连接
            import duckdb
            conn = duckdb.connect(self.db_path, read_only=True)
            try:
                result = conn.execute("SELECT 42").fetchone()
                with lock:
                    results.append(result[0])
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 5)
        self.assertTrue(all(r == 42 for r in results))

    # ========== P2: 连接配置持久化 ==========

    def test_config_persists_across_with_blocks(self):
        """测试 SET 配置在多次 with 块之间持久化"""
        conn = DuckDBConnection(self.db_path, read_only=False, close_on_exit=False)

        # 第一次 with: 设置配置
        with conn as c:
            c.execute("SET memory_limit='1GB'")
            ml = c.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            # DuckDB may format 1GB as '1.0 GiB' or '953.6 MiB'
            self.assertIsNotNone(ml)
            self.assertNotEqual(ml, '')

        # 第二次 with: 配置应持久化（不再重置为默认值）
        with conn as c:
            ml = c.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            self.assertIsNotNone(ml)
            self.assertNotEqual(ml, '')

        conn.close()

    def test_close_on_exit_can_update(self):
        """单例二次初始化时显式传入 close_on_exit 应生效"""
        conn = DuckDBConnection(self.db_path)  # 默认 True
        self.assertTrue(conn.close_on_exit)

        updated = DuckDBConnection(self.db_path, close_on_exit=False)
        self.assertIs(conn, updated)
        self.assertFalse(updated.close_on_exit)
        updated.close()

    def test_default_keeps_existing_close_on_exit(self):
        """不传 close_on_exit（None）时保持单例已有配置"""
        conn = DuckDBConnection(self.db_path, close_on_exit=False)
        DuckDBConnection(self.db_path)
        self.assertFalse(conn.close_on_exit)
        conn.close()

    def test_del_closes_only_detached_instance(self):
        """析构不关闭仍被注册表共享的连接；脱离注册表后释放句柄"""
        import gc

        conn = DuckDBConnection(self.db_path)
        conn.connect()
        alias = DuckDBConnection(self.db_path)

        del conn
        gc.collect()
        self.assertEqual(alias.connect().execute("SELECT 1").fetchone()[0], 1)

        # 模拟 teardown：注册表清空后析构应关闭连接，文件可删除
        DuckDBConnection._instances.clear()
        del alias
        gc.collect()
        os.remove(self.db_path)

    def test_multiple_configs_persist(self):
        """测试多个配置项在跨 with 块后持久化"""
        conn = DuckDBConnection(self.db_path, read_only=False, close_on_exit=False)

        with conn as c:
            c.execute("SET memory_limit='2GB'")
            c.execute("SET threads=2")

        with conn as c:
            ml = c.execute("SELECT current_setting('memory_limit')").fetchone()[0]
            th = c.execute("SELECT current_setting('threads')").fetchone()[0]
            self.assertIsNotNone(ml)
            self.assertNotEqual(ml, '')
            self.assertEqual(th, 2)

        conn.close()


if __name__ == '__main__':
    unittest.main()
