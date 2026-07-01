"""
回测引擎适配器模块

提供与 Factor_Trading_v3.0 回测引擎的兼容接口。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.connection import DuckDBConnection
from query.price_query import PriceQuery
from query.factor_query import FactorQuery

logger = logging.getLogger(__name__)


class FactorTradingAdapter:
    """Factor_Trading_v3.0 回测引擎适配器

    提供与原有 DataManager 兼容的接口，支持无缝迁移。

    Example:
        adapter = FactorTradingAdapter('market.db')

        # 兼容原有接口
        close_df = adapter.get_adj_price('close', adjust='forward')
        trade_dates = adapter.get_trade_dates('2024-01-01', '2024-12-31')
        stock_list = adapter.get_stock_list()

        # 获取因子数据
        factor_df = adapter.get_factor_data('PE')
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        """初始化适配器

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.price_query = PriceQuery(db_path)
        self.factor_query = FactorQuery(db_path)

    def get_adj_price(
        self,
        price_type: str = 'close',
        adjust: str = 'forward',
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        stock_codes: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """获取复权价格 (兼容原有接口)

        Args:
            price_type: 价格类型 (open/high/low/close)
            adjust: 复权方式 (forward/backward/none)
            start_date: 开始日期
            end_date: 结束日期
            stock_codes: 股票代码列表

        Returns:
            价格矩阵 DataFrame
        """
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        return self.price_query.get_price_matrix(
            field=price_type,
            stock_codes=stock_codes,
            start_date=start,
            end_date=end,
            adjust=adjust
        )

    def get_trade_dates(self, start: str, end: str) -> list[str]:
        """获取交易日列表 (兼容原有接口)

        Args:
            start: 开始日期
            end: 结束日期

        Returns:
            交易日字符串列表
        """
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)

        dates = self.price_query.get_trade_dates(start_date, end_date)
        return [d.strftime('%Y-%m-%d') for d in dates]

    def get_stock_list(
        self,
        trade_date: Optional[str] = None,
        industry: Optional[str] = None
    ) -> list[str]:
        """获取股票列表 (兼容原有接口)

        Args:
            trade_date: 交易日期
            industry: 行业过滤

        Returns:
            股票代码列表
        """
        td = date.fromisoformat(trade_date) if trade_date else None
        return self.price_query.get_stock_list(td, industry)

    def get_factor_data(
        self,
        factor_name: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        stock_codes: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """获取因子数据 (兼容原有接口)

        Args:
            factor_name: 因子名称
            start_date: 开始日期
            end_date: 结束日期
            stock_codes: 股票代码列表

        Returns:
            因子数据 DataFrame
        """
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        return self.factor_query.get_factor(factor_name, stock_codes, start, end)

    def get_factor_matrix(
        self,
        factor_names: list[str],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """获取因子矩阵 (兼容原有接口)

        Args:
            factor_names: 因子名称列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            因子矩阵 DataFrame
        """
        start = date.fromisoformat(start_date) if start_date else None
        end = date.fromisoformat(end_date) if end_date else None

        return self.factor_query.get_factor_matrix(factor_names, start_date=start, end_date=end)

    def get_daily_data(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        fields: list[str]
    ) -> pd.DataFrame:
        """获取日 K 数据 (兼容原有接口)

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            fields: 查询字段

        Returns:
            日 K 数据 DataFrame
        """
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)

        return self.price_query.get_daily(stock_codes, start, end, fields)


class DataLoaderV3Adapter:
    """DataLoaderV3 适配器

    将 DuckDB 查询结果转换为 DataLoaderV3 所需的 NumPy 数组格式。

    Example:
        adapter = DataLoaderV3Adapter('market.db')

        # 获取 DataLoaderV3 格式的数据
        data = adapter.to_data_loader_v3(
            stock_codes=['000001.SZ', '600000.SH'],
            start_date='2024-01-01',
            end_date='2024-12-31',
            fields=['close', 'volume']
        )

        # data['dates'] -> np.ndarray[str]
        # data['stocks'] -> np.ndarray[str]
        # data['close'] -> np.ndarray[shape=(n_dates, n_stocks)]
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        """初始化适配器

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.price_query = PriceQuery(db_path)

    def to_data_loader_v3(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] = None,
        adjust: str = 'forward'
    ) -> dict:
        """转换为 DataLoaderV3 格式

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            fields: 数据字段
            adjust: 复权方式

        Returns:
            DataLoaderV3 格式字典
        """
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        fields = fields or ['close', 'open', 'high', 'low', 'volume']

        result = {
            'dates': None,
            'stocks': np.array(stock_codes),
        }

        # 获取每个字段的价格矩阵
        for field in fields:
            matrix = self.price_query.get_price_matrix(
                field=field,
                stock_codes=stock_codes,
                start_date=start,
                end_date=end,
                adjust=adjust
            )

            if matrix.empty:
                logger.warning(f"字段 {field} 无数据")
                continue

            # 确保股票顺序一致
            matrix = matrix.reindex(columns=stock_codes)

            # 保存日期
            if result['dates'] is None:
                result['dates'] = matrix.index.values.astype(str)

            # 保存数据
            result[field] = matrix.values

        return result

    def to_numpy_arrays(
        self,
        df: pd.DataFrame,
        index_col: str = 'trade_date',
        columns_col: str = 'stock_code',
        values_col: str = 'close'
    ) -> dict:
        """将 DataFrame 转为 NumPy 数组格式

        Args:
            df: 长格式 DataFrame
            index_col: 索引列
            columns_col: 列名
            values_col: 值列

        Returns:
            NumPy 格式字典
        """
        if df.empty:
            return {'dates': np.array([]), 'stocks': np.array([]), 'values': np.array([])}

        # 转为宽格式
        matrix = df.pivot(index=index_col, columns=columns_col, values=values_col)

        return {
            'dates': matrix.index.values.astype(str),
            'stocks': matrix.columns.values.astype(str),
            'values': matrix.values
        }

    def get_level1_panel(
        self,
        stock_codes: list[str],
        trade_date: str,
        fields: list[str] = None
    ) -> dict:
        """获取 Level 1 面板数据

        Args:
            stock_codes: 股票代码列表
            trade_date: 交易日期
            fields: 数据字段

        Returns:
            Level 1 面板数据字典
        """
        td = date.fromisoformat(trade_date)
        fields = fields or ['close', 'volume']

        result = {
            'trade_date': trade_date,
            'stock_codes': np.array(stock_codes),
        }

        for field in fields:
            df = self.price_query.get_level1(
                stock_codes=stock_codes,
                trade_date=td
            )

            if df.empty:
                continue

            # 转为宽格式 (time × stocks)
            if field in df.columns:
                matrix = df.pivot(index='trade_time', columns='stock_code', values=field)
                matrix = matrix.reindex(columns=stock_codes)
                result[field] = matrix.values
                result['times'] = matrix.index.values.astype(str)

        return result
