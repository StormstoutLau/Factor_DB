"""
查询基类模块

定义所有查询类的统一接口和公共逻辑。
"""

from __future__ import annotations

import logging
from abc import ABC
from dataclasses import dataclass
from datetime import date
from typing import Optional

from core.connection import DuckDBConnection
from query.cache import QueryCache

logger = logging.getLogger(__name__)


@dataclass
class QueryFilter:
    """查询过滤条件"""
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    stock_codes: Optional[list[str]] = None
    fields: Optional[list[str]] = None
    as_of: Optional[date] = None  # PIT: 数据截止时间


class BaseQuery(ABC):
    """查询基类

    提供数据库连接、SQL 构建、查询执行等公共功能。

    Attributes:
        db_path: 数据库路径
        conn: DuckDB 连接（只读）
        cache: 查询缓存实例
    """

    def __init__(self, db_path: str = 'factor_db.duckdb', cache_size: int = 0):
        """初始化查询器

        Args:
            db_path: 数据库文件路径
            cache_size: 缓存大小（0 表示不启用缓存）
        """
        self.conn = DuckDBConnection(db_path, read_only=True)
        self.conn.connect()
        self.cache = QueryCache(max_size=cache_size) if cache_size > 0 else None

    def _build_date_filter(self, filter: QueryFilter) -> str:
        """构建日期过滤条件

        Args:
            filter: 查询过滤条件

        Returns:
            SQL WHERE 子句
        """
        conditions = []

        if filter.start_date:
            conditions.append(f"trade_date >= '{filter.start_date}'")
        if filter.end_date:
            conditions.append(f"trade_date <= '{filter.end_date}'")

        if conditions:
            return ' AND '.join(conditions)
        return '1=1'

    def _build_stock_filter(self, filter: QueryFilter) -> str:
        """构建股票代码过滤条件

        Args:
            filter: 查询过滤条件

        Returns:
            SQL WHERE 子句
        """
        if filter.stock_codes:
            codes = ', '.join([f"'{code}'" for code in filter.stock_codes])
            return f"stock_code IN ({codes})"
        return '1=1'

    def _build_field_selector(self, filter: QueryFilter, default_fields: list[str]) -> str:
        """构建字段选择器

        Args:
            filter: 查询过滤条件
            default_fields: 默认字段列表

        Returns:
            SQL SELECT 子句
        """
        fields = filter.fields or default_fields
        return ', '.join(fields)

    def _execute_query(self, sql: str) -> any:
        """执行查询并返回 DataFrame

        如果启用了缓存，优先从缓存获取结果。

        Args:
            sql: SQL 语句

        Returns:
            pandas DataFrame
        """
        # 尝试从缓存获取
        if self.cache is not None:
            cached = self.cache.get(sql)
            if cached is not None:
                return cached

        try:
            df = self.conn.fetchdf(sql)
        except Exception as e:
            logger.error(f"查询执行失败: {e}\nSQL: {sql}")
            raise

        # 存入缓存
        if self.cache is not None:
            self.cache.put(sql, df)

        return df

    def cache_clear(self) -> None:
        """清空查询缓存"""
        if self.cache is not None:
            self.cache.clear()

    def cache_stats(self) -> Optional[dict]:
        """获取缓存统计信息

        Returns:
            缓存统计字典，未启用缓存返回 None
        """
        if self.cache is not None:
            return self.cache.stats()
        return None

    def _build_pit_subquery(self, table: str, pk_columns: list[str],
                             extra_conditions: str = '1=1',
                             as_of: Optional[date] = None) -> str:
        """构建 PIT 子查询筛选条件

        返回 SQL 片段，用于 WHERE 子句中过滤每个主键的最新版本。

        Args:
            table: 表名
            pk_columns: 主键列（不含 loaded_at）
            extra_conditions: 额外过滤条件
            as_of: PIT 截止时间，None 表示取最新版本

        Returns:
            SQL 子查询字符串，用于 IN 条件
        """
        pk_list = ', '.join(pk_columns)
        time_filter = f"AND loaded_at <= '{as_of}'" if as_of else ''

        return f'''
            ({pk_list}, loaded_at) IN (
                SELECT {pk_list}, MAX(loaded_at)
                FROM {table}
                WHERE {extra_conditions} {time_filter}
                GROUP BY {pk_list}
            )
        '''
