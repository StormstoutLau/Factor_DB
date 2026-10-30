"""
DuckDB 连接管理模块

职责：
    - 管理数据库连接生命周期
    - 支持读写分离模式
    - 提供上下文管理器支持
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path
from typing import Optional

import duckdb

from Factor_DB.utils.data_version import bump_version

logger = logging.getLogger(__name__)

# 首关键字为写语句（用于缓存失效；SET/BEGIN/COMMIT 不算数据变更）
_WRITE_STATEMENT_RE = re.compile(
    r'^\s*(insert|update|delete|merge|create|drop|alter|truncate|copy|replace|rename)\b',
    re.IGNORECASE,
)


class DuckDBConnection:
    """DuckDB 连接管理器

    提供线程安全的单例连接管理，支持读写分离。

    Example:
        # 写入模式
        with DuckDBConnection('data.db', read_only=False) as conn:
            conn.execute("CREATE TABLE test (id INTEGER)")

        # 读取模式
        with DuckDBConnection('data.db', read_only=True) as conn:
            result = conn.execute("SELECT * FROM test").fetchdf()
    """

    _instances: dict[str, 'DuckDBConnection'] = {}
    _lock = threading.Lock()

    def __new__(cls, db_path: str, read_only: bool = False, **kwargs):
        """单例模式，相同路径返回同一实例"""
        key = f"{db_path}:{read_only}"
        if key not in cls._instances:
            with cls._lock:
                if key not in cls._instances:
                    instance = super().__new__(cls)
                    cls._instances[key] = instance
        return cls._instances[key]

    def __init__(self, db_path: str, read_only: bool = False,
                 close_on_exit: Optional[bool] = None):
        """初始化连接管理器

        Args:
            db_path: 数据库文件路径
            read_only: 是否只读模式
            close_on_exit: 上下文管理器退出时是否关闭连接；
                None 表示沿用单例已有配置（首次创建时为 True）
        """
        # 已存在的单例：仅显式传值才更新配置
        if hasattr(self, '_initialized'):
            if close_on_exit is not None and close_on_exit != self.close_on_exit:
                logger.info(
                    f"更新 close_on_exit: {self.close_on_exit} -> {close_on_exit} ({db_path})"
                )
                self.close_on_exit = close_on_exit
            return

        self.db_path = Path(db_path)
        self.read_only = read_only
        self.close_on_exit = True if close_on_exit is None else close_on_exit
        self._conn: Optional[duckdb.DuckDBPyConnection] = None
        self._initialized = True

        logger.info(f"DuckDBConnection 初始化: {db_path} (read_only={read_only}, close_on_exit={close_on_exit})")

    def connect(self) -> duckdb.DuckDBPyConnection:
        """建立或获取数据库连接

        Returns:
            DuckDB 连接对象

        Raises:
            duckdb.Error: 连接失败时抛出
        """
        if self._conn is None:
            try:
                # 确保目录存在
                self.db_path.parent.mkdir(parents=True, exist_ok=True)

                self._conn = duckdb.connect(str(self.db_path), self.read_only)
                logger.debug(f"DuckDB 连接已建立: {self.db_path}")
            except duckdb.Error as e:
                logger.error(f"DuckDB 连接失败: {e}")
                raise

        return self._conn

    def close(self) -> None:
        """关闭数据库连接并从单例缓存中移除"""
        if self._conn is not None:
            try:
                self._conn.close()
                logger.debug(f"DuckDB 连接已关闭: {self.db_path}")
            except duckdb.Error as e:
                logger.warning(f"关闭连接时出错: {e}")
            finally:
                self._conn = None

        # 从单例缓存中移除，允许后续重新创建不同配置的连接
        key = f"{self.db_path}:{self.read_only}"
        with self._lock:
            if key in self._instances:
                del self._instances[key]

    def execute(self, query: str, parameters: Optional[dict] = None):
        """执行 SQL 查询

        Args:
            query: SQL 语句
            parameters: 查询参数（防止 SQL 注入）

        Returns:
            查询结果
        """
        conn = self.connect()
        if parameters:
            result = conn.execute(query, parameters)
        else:
            result = conn.execute(query)

        if not self.read_only and _WRITE_STATEMENT_RE.match(query):
            bump_version()

        return result

    def fetchdf(self, query: str, parameters: Optional[dict] = None):
        """执行查询并返回 DataFrame

        Args:
            query: SQL 语句
            parameters: 查询参数

        Returns:
            pandas DataFrame
        """
        result = self.execute(query, parameters)
        return result.fetchdf()

    def __enter__(self) -> duckdb.DuckDBPyConnection:
        """上下文管理器入口"""
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器出口

        写连接的上下文可能包含数据变更（loader/SchemaManager 直接使用原生连接），
        退出时递增数据版本，使查询缓存立即失效。
        """
        if exc_type is not None:
            logger.error(f"上下文内发生异常: {exc_val}")
        elif not self.read_only:
            bump_version()
        if self.close_on_exit:
            self.close()

    def __del__(self):
        """析构时关闭连接（仅限已脱离单例注册表的实例）"""
        try:
            key = f"{self.db_path}:{self.read_only}"
            if DuckDBConnection._instances.get(key) is not self:
                self.close()
        except Exception:
            pass


class ConnectionPool:
    """连接池（预留扩展）

    未来支持多连接并发查询时使用。
    """

    def __init__(self, db_path: str, max_connections: int = 5):
        self.db_path = db_path
        self.max_connections = max_connections
        self._pool: list[DuckDBConnection] = []

    def acquire(self) -> DuckDBConnection:
        """获取连接"""
        if self._pool:
            return self._pool.pop()
        return DuckDBConnection(self.db_path, read_only=True)

    def release(self, conn: DuckDBConnection) -> None:
        """释放连接"""
        if len(self._pool) < self.max_connections:
            self._pool.append(conn)
        else:
            conn.close()

