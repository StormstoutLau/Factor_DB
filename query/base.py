"""
查询基类模块

定义所有查询类的统一接口和公共逻辑。
"""

from __future__ import annotations

import json
import logging
from abc import ABC
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.query.cache import QueryCache
from Factor_DB.utils.sql_utils import (
    bind,
    bind_in,
    quote_identifier,
    validate_identifier,
)

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

    def _build_date_filter(self, filter: QueryFilter, params: dict[str, Any]) -> str:
        """构建日期过滤条件（命名参数绑定）

        Args:
            filter: 查询过滤条件
            params: 参数字典（原地写入）

        Returns:
            SQL WHERE 子句（仅含 $ 占位符）
        """
        conditions = []

        if filter.start_date:
            conditions.append(f"trade_date >= {bind(params, 'start_date', filter.start_date)}")
        if filter.end_date:
            conditions.append(f"trade_date <= {bind(params, 'end_date', filter.end_date)}")

        if conditions:
            return ' AND '.join(conditions)
        return '1=1'

    def _build_stock_filter(self, filter: QueryFilter, params: dict[str, Any]) -> str:
        """构建股票代码过滤条件（命名参数绑定）

        Args:
            filter: 查询过滤条件
            params: 参数字典（原地写入）

        Returns:
            SQL WHERE 子句（仅含 $ 占位符）
        """
        if filter.stock_codes:
            placeholders = bind_in(params, 'stock_code', filter.stock_codes)
            return f"stock_code IN ({placeholders})"
        return '1=1'

    def _build_field_selector(self, filter: QueryFilter, default_fields: list[str]) -> str:
        """构建字段选择器（标识符白名单校验）

        Args:
            filter: 查询过滤条件
            default_fields: 默认字段列表

        Returns:
            SQL SELECT 子句
        """
        fields = filter.fields or default_fields
        return ', '.join(validate_identifier(f, '字段名') for f in fields)

    def _execute_query(self, sql: str, params: Optional[dict[str, Any]] = None) -> any:
        """执行查询并返回 DataFrame

        如果启用了缓存，优先从缓存获取结果。
        缓存 key 包含参数，避免相同模板不同参数串结果。

        Args:
            sql: SQL 语句
            params: 命名参数

        Returns:
            pandas DataFrame
        """
        cache_key = self._cache_key(sql, params)

        # 尝试从缓存获取
        if self.cache is not None:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        try:
            df = self.conn.fetchdf(sql, params)
        except Exception as e:
            logger.error(f"查询执行失败: {e}\nSQL: {sql}\n参数: {params}")
            raise

        # 存入缓存
        if self.cache is not None:
            self.cache.put(cache_key, df)

        return df

    @staticmethod
    def _cache_key(sql: str, params: Optional[dict[str, Any]]) -> str:
        """生成缓存 key（含参数，稳定序列化）"""
        if not params:
            return sql
        payload = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        return f"{sql}|{payload}"

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
                             as_of: Optional[date] = None,
                             non_null_columns: Optional[list[str]] = None,
                             params: Optional[dict[str, Any]] = None) -> str:
        """构建 PIT 子查询筛选条件

        返回 SQL 片段，用于 WHERE 子句中过滤每个主键的最新版本。

        Args:
            table: 表名（白名单校验）
            pk_columns: 主键列（不含 loaded_at，白名单校验）
            extra_conditions: 额外过滤条件（调用方负责参数化）
            as_of: PIT 截止时间，None 表示取最新版本；非 None 时写入 params
            non_null_columns: 必须非 NULL 的列（白名单校验）
            params: 命名参数字典；None 时内部新建（仅适用于不含 as_of 的场景）

        Returns:
            SQL 子查询字符串，用于 IN 条件
        """
        params = params if params is not None else {}

        validate_identifier(table, '表名')
        pk_list = ', '.join(validate_identifier(c, '主键列') for c in pk_columns)
        time_filter = f"AND loaded_at <= {bind(params, 'as_of', as_of)}" if as_of else ''

        null_filters = ''
        if non_null_columns:
            null_filters = ' AND ' + ' AND '.join(
                f'{validate_identifier(col, "过滤列")} IS NOT NULL'
                for col in non_null_columns
            )

        return f'''
            ({pk_list}, loaded_at) IN (
                SELECT {pk_list}, MAX(loaded_at)
                FROM {quote_identifier(table)}
                WHERE {extra_conditions} {time_filter} {null_filters}
                GROUP BY {pk_list}
            )
        '''
