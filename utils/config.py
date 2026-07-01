"""
配置管理模块

统一管理项目配置，支持配置文件和环境变量。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DatabaseConfig:
    """数据库配置"""
    db_path: str = 'factor_db.duckdb'
    read_only: bool = False
    max_memory: str = '4GB'
    threads: int = 4


@dataclass
class AppLoaderConfig:
    """应用层数据加载配置（聚合到主Config中）"""
    batch_size: int = 10000
    skip_existing: bool = True
    validate_data: bool = True
    show_progress: bool = True
    max_workers: int = 4


@dataclass
class QueryConfig:
    """查询配置"""
    cache_size: int = 1000
    cache_ttl: int = 300  # 秒
    max_rows: int = 1000000
    timeout: int = 30  # 秒


@dataclass
class Config:
    """项目主配置"""
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    loader: AppLoaderConfig = field(default_factory=AppLoaderConfig)
    query: QueryConfig = field(default_factory=QueryConfig)
    log_level: str = 'INFO'
    log_file: Optional[str] = None

    @classmethod
    def from_file(cls, config_path: str) -> 'Config':
        """从 JSON 文件加载配置

        Args:
            config_path: 配置文件路径

        Returns:
            配置对象
        """
        path = Path(config_path)
        if not path.exists():
            return cls()

        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return cls._from_dict(data)

    @classmethod
    def from_env(cls) -> 'Config':
        """从环境变量加载配置

        Returns:
            配置对象
        """
        config = cls()

        # 数据库配置
        if db_path := os.getenv('FACTOR_DB_PATH'):
            config.database.db_path = db_path
        if threads := os.getenv('FACTOR_DB_THREADS'):
            config.database.threads = int(threads)

        # 日志配置
        if log_level := os.getenv('FACTOR_LOG_LEVEL'):
            config.log_level = log_level

        return config

    @classmethod
    def _from_dict(cls, data: dict) -> 'Config':
        """从字典创建配置

        Args:
            data: 配置字典

        Returns:
            配置对象
        """
        config = cls()

        if 'database' in data:
            db_data = data['database']
            config.database = DatabaseConfig(
                db_path=db_data.get('db_path', config.database.db_path),
                read_only=db_data.get('read_only', config.database.read_only),
                max_memory=db_data.get('max_memory', config.database.max_memory),
                threads=db_data.get('threads', config.database.threads)
            )

        if 'loader' in data:
            loader_data = data['loader']
            config.loader = LoaderConfig(
                batch_size=loader_data.get('batch_size', config.loader.batch_size),
                skip_existing=loader_data.get('skip_existing', config.loader.skip_existing),
                validate_data=loader_data.get('validate_data', config.loader.validate_data),
                show_progress=loader_data.get('show_progress', config.loader.show_progress),
                max_workers=loader_data.get('max_workers', config.loader.max_workers)
            )

        if 'query' in data:
            query_data = data['query']
            config.query = QueryConfig(
                cache_size=query_data.get('cache_size', config.query.cache_size),
                cache_ttl=query_data.get('cache_ttl', config.query.cache_ttl),
                max_rows=query_data.get('max_rows', config.query.max_rows),
                timeout=query_data.get('timeout', config.query.timeout)
            )

        config.log_level = data.get('log_level', config.log_level)
        config.log_file = data.get('log_file', config.log_file)

        return config

    def to_file(self, config_path: str) -> None:
        """保存配置到文件

        Args:
            config_path: 配置文件路径
        """
        data = {
            'database': {
                'db_path': self.database.db_path,
                'read_only': self.database.read_only,
                'max_memory': self.database.max_memory,
                'threads': self.database.threads
            },
            'loader': {
                'batch_size': self.loader.batch_size,
                'skip_existing': self.loader.skip_existing,
                'validate_data': self.loader.validate_data,
                'show_progress': self.loader.show_progress,
                'max_workers': self.loader.max_workers
            },
            'query': {
                'cache_size': self.query.cache_size,
                'cache_ttl': self.query.cache_ttl,
                'max_rows': self.query.max_rows,
                'timeout': self.query.timeout
            },
            'log_level': self.log_level,
            'log_file': self.log_file
        }

        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
