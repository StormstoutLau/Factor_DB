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
from Factor_DB.utils.sql_utils import escape_literal

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

