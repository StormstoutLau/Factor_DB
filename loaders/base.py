"""
数据加载器基类

定义所有加载器的统一接口。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.connection import DuckDBConnection

logger = logging.getLogger(__name__)


@dataclass
class LoaderConfig:
    """加载器配置"""
    db_path: str = 'factor_db.duckdb'
    batch_size: int = 10000
    skip_existing: bool = True
    validate_data: bool = True
    show_progress: bool = True


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

