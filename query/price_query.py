"""
价格数据查询模块

提供股票价格数据的查询接口，支持日 K 和 Level 1 数据。
"""

from __future__ import annotations

import logging
from datetime import date, time
from pathlib import Path
from typing import Optional

import pandas as pd

from .base import BaseQuery, QueryFilter

logger = logging.getLogger(__name__)


class PriceQuery(BaseQuery):
    """价格数据查询器

    支持查询日 K 数据和 Level 1 分钟数据。

    Level 1 数据支持两种存储后端：
    - DuckDB 表 (level1_snapshots): 适合小数据量，索引加速
    - Parquet 分区文件: 适合大数据量，分区裁剪 + Zonemap 低内存

    Example:
        query = PriceQuery('market.db')

        # 查询日 K 数据
        df = query.get_daily(
            stock_codes=['000001.SZ', '600000.SH'],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            fields=['open', 'high', 'low', 'close', 'volume']
        )

        # 查询 Level 1 数据
        df = query.get_level1(
            stock_codes=['000001.SZ'],
            trade_date=date(2024, 1, 15),
            start_time=time(9, 30),
            end_time=time(15, 0)
        )

        # 获取价格矩阵 (用于回测)
        matrix = query.get_price_matrix(
            field='close',
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31)
        )
    """

    def __init__(self, db_path: str = 'factor_db.duckdb', cache_size: int = 0,
                 level1_parquet_dir: Optional[str] = None):
        """初始化价格查询器

        Args:
            db_path: 数据库文件路径
            cache_size: 缓存大小（0 表示不启用缓存）
            level1_parquet_dir: Level 1 Parquet 分区目录（如指定则优先使用）
        """
        super().__init__(db_path, cache_size)
        self.level1_parquet_dir = Path(level1_parquet_dir) if level1_parquet_dir else None
        if self.level1_parquet_dir and not self.level1_parquet_dir.exists():
            logger.warning(f"Level 1 Parquet 目录不存在: {self.level1_parquet_dir}")
            self.level1_parquet_dir = None

    def _use_parquet_level1(self) -> bool:
        """是否使用 Parquet 分区作为 Level 1 数据源"""
        return self.level1_parquet_dir is not None and self.level1_parquet_dir.exists()

    def get_daily(
        self,
        stock_codes: Optional[list[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        fields: Optional[list[str]] = None,
        as_of: Optional[date] = None
    ) -> pd.DataFrame:
        """查询日 K 数据

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            fields: 查询字段 (open/high/low/close/volume/amount/adj_factor)
            as_of: PIT 截止时间，仅返回此时间之前的数据版本

        Returns:
            日 K 数据 DataFrame
        """
        filter = QueryFilter(
            start_date=start_date,
            end_date=end_date,
            stock_codes=stock_codes,
            fields=fields,
            as_of=as_of
        )

        default_fields = ['trade_date', 'stock_code', 'open', 'high', 'low', 'close', 'volume', 'amount']
        select_fields = self._build_field_selector(filter, default_fields)

        date_filter = self._build_date_filter(filter)
        stock_filter = self._build_stock_filter(filter)
        pit_filter = self._build_pit_subquery(
            'daily_prices', ['trade_date', 'stock_code'],
            as_of=filter.as_of,
            non_null_columns=['close']
        )

        sql = f'''
            SELECT {select_fields}
            FROM daily_prices
            WHERE {date_filter} AND {stock_filter} AND {pit_filter}
            ORDER BY trade_date, stock_code
        '''

        df = self._execute_query(sql)
        logger.debug(f"日 K 查询完成: {len(df)} 条记录")
        return df

    def get_level1(
        self,
        stock_codes: Optional[list[str]] = None,
        trade_date: Optional[date] = None,
        start_time: Optional[time] = None,
        end_time: Optional[time] = None,
        fields: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """查询 Level 1 分钟数据

        优先从 Parquet 分区查询（如果配置了 level1_parquet_dir），
        否则回退到 DuckDB level1_snapshots 表。

        Args:
            stock_codes: 股票代码列表
            trade_date: 交易日期
            start_time: 开始时间
            end_time: 结束时间
            fields: 查询字段

        Returns:
            Level 1 数据 DataFrame
        """
        filter = QueryFilter(
            start_date=trade_date,
            end_date=trade_date,
            stock_codes=stock_codes,
            fields=fields
        )

        default_fields = ['trade_date', 'trade_time', 'stock_code', 'open', 'high', 'low', 'close', 'volume', 'amount']
        select_fields = self._build_field_selector(filter, default_fields)

        date_filter = self._build_date_filter(filter)
        stock_filter = self._build_stock_filter(filter)

        time_conditions = []
        if start_time:
            time_conditions.append(f"trade_time >= '{start_time}'")
        if end_time:
            time_conditions.append(f"trade_time <= '{end_time}'")
        time_filter = ' AND '.join(time_conditions) if time_conditions else '1=1'

        if self._use_parquet_level1():
            parquet_glob = str(self.level1_parquet_dir / '*/data.parquet')
            sql = f'''
                SELECT {select_fields}
                FROM read_parquet('{parquet_glob}', hive_partitioning=true)
                WHERE {date_filter} AND {stock_filter} AND {time_filter}
                ORDER BY trade_date, trade_time, stock_code
            '''
            df = self._execute_query(sql)
            logger.debug(f"Level 1 Parquet 查询完成: {len(df)} 条记录")
            return df

        sql = f'''
            SELECT {select_fields}
            FROM level1_snapshots
            WHERE {date_filter} AND {stock_filter} AND {time_filter}
            ORDER BY trade_date, trade_time, stock_code
        '''

        df = self._execute_query(sql)
        logger.debug(f"Level 1 查询完成: {len(df)} 条记录")
        return df

    def get_price_matrix(
        self,
        field: str = 'close',
        stock_codes: Optional[list[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        adjust: str = 'none',
        as_of: Optional[date] = None
    ) -> pd.DataFrame:
        """获取价格矩阵 (dates × stocks)

        用于回测引擎的向量化计算。

        Args:
            field: 价格字段 (open/high/low/close)
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            adjust: 复权方式 (none/forward/backward)
            as_of: PIT 截止时间

        Returns:
            价格矩阵 DataFrame，索引为日期，列为股票代码
        """
        # 验证字段
        valid_fields = {'open', 'high', 'low', 'close', 'volume', 'amount'}
        if field not in valid_fields:
            raise ValueError(f"无效字段: {field}，可选: {valid_fields}")

        # 构建过滤条件
        date_filter = self._build_date_filter(
            QueryFilter(start_date=start_date, end_date=end_date)
        )
        stock_filter = self._build_stock_filter(
            QueryFilter(stock_codes=stock_codes)
        )
        pit_filter = self._build_pit_subquery(
            'daily_prices', ['trade_date', 'stock_code'],
            as_of=as_of,
            non_null_columns=[field]
        )

        # 复权处理
        price_expr = self._get_adjusted_price_expr(field, adjust)

        sql = f'''
            SELECT trade_date, stock_code, {price_expr} as {field}
            FROM daily_prices
            WHERE {date_filter} AND {stock_filter} AND {pit_filter}
            ORDER BY trade_date, stock_code
        '''

        df = self._execute_query(sql)

        # 转为宽格式矩阵
        if df.empty:
            return pd.DataFrame()

        matrix = df.pivot(index='trade_date', columns='stock_code', values=field)
        logger.debug(f"价格矩阵: {matrix.shape}")
        return matrix

    def get_trade_dates(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> list[date]:
        """获取交易日列表

        Args:
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            交易日列表
        """
        date_filter = self._build_date_filter(
            QueryFilter(start_date=start_date, end_date=end_date)
        )

        sql = f'''
            SELECT trade_date
            FROM trade_calendar
            WHERE is_trading_day = TRUE AND {date_filter}
            ORDER BY trade_date
        '''

        df = self._execute_query(sql)
        return df['trade_date'].tolist()

    def get_stock_list(
        self,
        trade_date: Optional[date] = None,
        industry: Optional[str] = None
    ) -> list[str]:
        """获取股票列表

        Args:
            trade_date: 查询日期 (预留，用于过滤退市股票)
            industry: 行业过滤

        Returns:
            股票代码列表
        """
        conditions = ['1=1']

        if industry:
            conditions.append(f"industry = '{industry}'")

        where_clause = ' AND '.join(conditions)

        sql = f'''
            SELECT stock_code
            FROM stock_info
            WHERE {where_clause}
            ORDER BY stock_code
        '''

        df = self._execute_query(sql)
        return df['stock_code'].tolist()

    def get_date_range(self) -> tuple[Optional[date], Optional[date]]:
        """获取数据日期范围

        Returns:
            (最早日期, 最晚日期)
        """
        pit_filter = self._build_pit_subquery(
            'daily_prices', ['trade_date', 'stock_code']
        )

        sql = f'''
            SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date
            FROM daily_prices
            WHERE {pit_filter}
        '''

        df = self._execute_query(sql)
        if df.empty:
            return None, None

        return df['min_date'].iloc[0], df['max_date'].iloc[0]

    def _get_adjusted_price_expr(self, field: str, adjust: str) -> str:
        """获取复权价格表达式

        Args:
            field: 价格字段
            adjust: 复权方式

        Returns:
            SQL 表达式
        """
        if adjust == 'forward':
            # 前复权: price * adj_factor
            return f"{field} * adj_factor"
        elif adjust == 'backward':
            # 后复权: price / adj_factor (假设 adj_factor 是累积因子)
            return f"CASE WHEN adj_factor = 0 THEN {field} ELSE {field} / adj_factor END"
        else:
            return field
