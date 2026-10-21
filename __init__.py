# -*- coding: utf-8 -*-
"""Factor_DB - 因子数据库模块

提供基于 DuckDB 的因子数据存储、查询和管理。
公共 API 由 core/ 子包提供，此处统一 re-export。
"""

from .core import DuckDBConnection, SchemaManager, MetadataManager

__version__ = "1.0.0"
__all__ = ['DuckDBConnection', 'SchemaManager', 'MetadataManager']
