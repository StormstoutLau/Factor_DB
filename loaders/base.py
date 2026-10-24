"""
数据加载器基类

定义所有加载器的统一接口。
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.utils.sql_utils import escape_literal, quote_identifier, validate_identifier

logger = logging.getLogger(__name__)

_MEMORY_LIMIT_RE = re.compile(r'\d+(?:\.\d+)?\s*(?:KB|MB|GB|TB|%)', re.IGNORECASE)


@dataclass
class LoaderConfig:
    """加载器配置

    内存/线程/临时目录支持环境变量覆盖：
        FACTOR_DB_MEMORY_LIMIT（默认 20GB）
        FACTOR_DB_THREADS（默认 4）
        FACTOR_DB_TEMP_DIR（默认 <db_path>.tmp，与 DuckDB 官方默认一致）
    """
    db_path: str = 'factor_db.duckdb'
    batch_size: int = 10000
    skip_existing: bool = True
    validate_data: bool = True
    show_progress: bool = True
    memory_limit: Optional[str] = None
    threads: Optional[int] = None
    temp_directory: Optional[str] = None
    preserve_insertion_order: bool = False

    def __post_init__(self):
        if self.memory_limit is None:
            self.memory_limit = os.getenv('FACTOR_DB_MEMORY_LIMIT', '20GB')
        if self.threads is None:
            self.threads = int(os.getenv('FACTOR_DB_THREADS', '4'))
        if self.temp_directory is None:
            self.temp_directory = os.getenv('FACTOR_DB_TEMP_DIR', f'{self.db_path}.tmp')

        if not _MEMORY_LIMIT_RE.fullmatch(str(self.memory_limit)):
            raise ValueError(f"非法 memory_limit: {self.memory_limit!r}（示例: 20GB / 512MB / 50%）")
        self.threads = int(self.threads)
        if self.threads <= 0:
            raise ValueError(f"threads 必须为正整数: {self.threads}")
        if not str(self.temp_directory).strip():
            raise ValueError("temp_directory 不能为空")


class BaseLoader(ABC):
    """数据加载器基类

    所有具体加载器必须继承此类并实现抽象方法。

    Example:
        class MyLoader(BaseLoader):
            def load(self, data_path: Path) -> int:
                # 实现加载逻辑
                pass

            def validate(self, data: any) -> bool:
                # 实现验证逻辑
                pass
    """

    def __init__(self, config: Optional[LoaderConfig] = None):
        """初始化加载器

        Args:
            config: 加载器配置
        """
        self.config = config or LoaderConfig()
        self.conn = DuckDBConnection(
            self.config.db_path, read_only=False, close_on_exit=False
        )
        self._loaded_count = 0

    def _apply_settings(self, conn) -> None:
        """应用内存/线程/临时目录配置（集中管理，避免散落硬编码）"""
        cfg = self.config
        conn.execute(f"SET memory_limit={escape_literal(cfg.memory_limit)}")
        conn.execute(f"SET threads={int(cfg.threads)}")
        conn.execute(f"SET temp_directory={escape_literal(cfg.temp_directory)}")
        if not cfg.preserve_insertion_order:
            conn.execute("SET preserve_insertion_order=false")

    @abstractmethod
    def load(self, data_path: Path) -> int:
        """加载数据

        Args:
            data_path: 数据文件或目录路径

        Returns:
            加载的记录数
        """
        pass

    # 因子长格式必需列
    FACTOR_LONG_COLUMNS = ('trade_date', 'stock_code', 'factor_value')

    def _ensure_factor_tables(self, conn, factor_name: str) -> str:
        """确保 factor_wide / factor_history 存在且包含该因子列

        Returns:
            双引号引用后的因子列名
        """
        from Factor_DB.core.schema import SchemaManager

        schema = SchemaManager(self.conn)
        if not schema.table_exists('factor_wide'):
            schema.create_factor_wide([factor_name])
        elif factor_name not in schema.get_factor_columns('factor_wide'):
            schema.add_factor_column('factor_wide', factor_name)

        if not schema.table_exists('factor_history'):
            schema.create_factor_history()

        return quote_identifier(factor_name)

    def _write_factor_from_temp(self, conn, temp_table: str, factor_name: str) -> int:
        """从长格式临时表双写 factor_history + factor_wide

        迁移前（factor_data 为实体表）额外写入 factor_data 保持旧读者一致；
        迁移后（factor_data 为视图）自动跳过。

        Args:
            conn: 底层 DuckDB 连接
            temp_table: 已注册的长表名（列：trade_date/stock_code/factor_value）
            factor_name: 因子名

        Returns:
            factor_wide 总行数

        Raises:
            ValueError: 因子名非法
        """
        from Factor_DB.core.schema import SchemaManager

        validate_identifier(temp_table, '临时表名')
        factor_col = self._ensure_factor_tables(conn, factor_name)
        legacy_write = SchemaManager(self.conn).object_type('factor_data') == 'BASE TABLE'
        temp = quote_identifier(temp_table)

        conn.execute('BEGIN TRANSACTION')
        try:
            conn.execute(f'''
                INSERT INTO factor_history
                    (trade_date, stock_code, factor_name, factor_value, loaded_at)
                SELECT
                    CAST(trade_date AS DATE),
                    CAST(stock_code AS VARCHAR),
                    $factor_name,
                    CAST(factor_value AS DOUBLE),
                    CURRENT_TIMESTAMP
                FROM {temp}
                WHERE factor_value IS NOT NULL
            ''', {'factor_name': factor_name})

            conn.execute(f'''
                INSERT INTO factor_wide (trade_date, stock_code, {factor_col}, loaded_at)
                SELECT
                    CAST(trade_date AS DATE),
                    CAST(stock_code AS VARCHAR),
                    CAST(factor_value AS DOUBLE),
                    CURRENT_TIMESTAMP
                FROM {temp}
                WHERE factor_value IS NOT NULL
                ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                    {factor_col} = EXCLUDED.{factor_col},
                    loaded_at = EXCLUDED.loaded_at
            ''')

            if legacy_write:
                conn.execute(f'''
                    INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                    SELECT
                        CAST(trade_date AS DATE),
                        CAST(stock_code AS VARCHAR),
                        $factor_name,
                        CAST(factor_value AS DOUBLE)
                    FROM {temp}
                    WHERE factor_value IS NOT NULL
                ''', {'factor_name': factor_name})

            wide_count = conn.execute('SELECT COUNT(*) FROM factor_wide').fetchone()[0]
            conn.execute('COMMIT')
        except Exception:
            try:
                conn.execute('ROLLBACK')
            except Exception:
                pass
            raise

        return wide_count

    def write_factor_long(self, df, factor_name: str) -> int:
        """长格式因子数据双写入口

        Args:
            df: 需包含 trade_date / stock_code / factor_value 三列
            factor_name: 因子名

        Returns:
            factor_wide 总行数

        Raises:
            ValueError: 空数据 / 缺列 / 非法因子名
        """
        if df is None or df.empty:
            raise ValueError("因子数据为空")

        missing = [c for c in self.FACTOR_LONG_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"因子长表缺少必要列: {missing}")

        validate_identifier(factor_name, '因子名')

        data = df[list(self.FACTOR_LONG_COLUMNS)].dropna(subset=['trade_date', 'stock_code'])
        if data.empty:
            raise ValueError("因子数据为空")
        # 宽表主键为 (trade_date, stock_code)，同键保留最后一条
        data = data.drop_duplicates(subset=['trade_date', 'stock_code'], keep='last')

        with self.conn as conn:
            self._apply_settings(conn)
            conn.register('_factor_write_tmp', data)
            try:
                return self._write_factor_from_temp(conn, '_factor_write_tmp', factor_name)
            finally:
                conn.unregister('_factor_write_tmp')

    @abstractmethod
    def validate(self, data: any) -> bool:
        """验证数据格式

        Args:
            data: 待验证的数据

        Returns:
            是否验证通过
        """
        pass

    def get_loaded_count(self) -> int:
        """获取已加载记录数"""
        return self._loaded_count

    def reset_counter(self) -> None:
        """重置计数器"""
        self._loaded_count = 0

    def _execute_batch(self, query: str, data: list) -> int:
        """批量执行插入

        Args:
            query: INSERT 语句
            data: 数据列表

        Returns:
            插入记录数
        """
        try:
            with self.conn as conn:
                # 使用 executemany 批量插入
                conn.executemany(query, data)
            return len(data)
        except Exception as e:
            logger.error(f"批量插入失败: {e}")
            return 0

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        if exc_type is not None:
            logger.error(f"加载器异常: {exc_val}")
        self.conn.close()

