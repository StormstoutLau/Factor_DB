"""
核心模块

提供数据库连接、表结构管理和元数据管理。
"""

from .connection import DuckDBConnection
from .schema import SchemaManager
from .metadata_manager import MetadataManager

__all__ = ['DuckDBConnection', 'SchemaManager', 'MetadataManager']
