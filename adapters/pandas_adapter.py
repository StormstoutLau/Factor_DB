"""
pandas 适配器模块

提供查询结果与 pandas DataFrame 之间的转换。
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class PandasAdapter:
    """pandas 数据适配器

    将数据库查询结果转换为各种 pandas 格式。

    Example:
        adapter = PandasAdapter()

        # 转为宽格式矩阵
        matrix = adapter.to_wide_format(df, index='trade_date', columns='stock_code', values='close')

        # 转为长格式
        long_df = adapter.to_long_format(matrix, value_name='close')

        # 添加技术指标
        df_with_ma = adapter.add_technical_indicators(df, indicators=['MA5', 'MA20', 'RSI'])
    """

    @staticmethod
    def to_wide_format(
        df: pd.DataFrame,
        index: str = 'trade_date',
        columns: str = 'stock_code',
        values: str = 'close'
    ) -> pd.DataFrame:
        """转为宽格式矩阵

        Args:
            df: 长格式 DataFrame
            index: 索引列
            columns: 列名
            values: 值列

        Returns:
            宽格式 DataFrame
        """
        if df.empty:
            return pd.DataFrame()

        matrix = df.pivot(index=index, columns=columns, values=values)
        return matrix

    @staticmethod
    def to_long_format(
        df: pd.DataFrame,
        value_name: str = 'value',
        id_vars: Optional[list[str]] = None
    ) -> pd.DataFrame:
        """转为长格式

        Args:
            df: 宽格式 DataFrame
            value_name: 值列名
            id_vars: 标识列

        Returns:
            长格式 DataFrame
        """
        if df.empty:
            return pd.DataFrame()

        if id_vars is None:
            id_vars = [df.index.name or 'index']
            df = df.reset_index()

        long_df = df.melt(id_vars=id_vars, var_name='variable', value_name=value_name)
        return long_df

    @staticmethod
    def to_multi_index(df: pd.DataFrame) -> pd.DataFrame:
        """设置多级索引 (date, code)

        Args:
            df: 包含 trade_date 和 stock_code 的 DataFrame

        Returns:
            多级索引 DataFrame
        """
        if 'trade_date' in df.columns and 'stock_code' in df.columns:
            df = df.set_index(['trade_date', 'stock_code'])
        return df

    @staticmethod
    def fill_missing_dates(
        df: pd.DataFrame,
        date_col: str = 'trade_date',
        method: str = 'ffill'
    ) -> pd.DataFrame:
        """填充缺失日期

        Args:
            df: 数据 DataFrame
            date_col: 日期列
            method: 填充方法

        Returns:
            填充后的 DataFrame
        """
        if df.empty:
            return df

        # 获取完整日期范围
        min_date = df[date_col].min()
        max_date = df[date_col].max()
        all_dates = pd.date_range(min_date, max_date, freq='D')

        # 对每个股票填充
        filled_dfs = []
        for code in df['stock_code'].unique():
            code_df = df[df['stock_code'] == code].copy()
            code_df = code_df.set_index(date_col)
            code_df = code_df.reindex(all_dates)
            code_df['stock_code'] = code
            code_df = code_df.fillna(method=method)
            filled_dfs.append(code_df.reset_index().rename(columns={'index': date_col}))

        return pd.concat(filled_dfs, ignore_index=True)

    @staticmethod
    def add_technical_indicators(
        df: pd.DataFrame,
        indicators: list[str],
        stock_col: str = 'stock_code',
        date_col: str = 'trade_date',
        close_col: str = 'close'
    ) -> pd.DataFrame:
        """添加技术指标

        Args:
            df: 价格数据 DataFrame
            indicators: 指标列表 ['MA5', 'MA20', 'RSI', 'MACD', 'BBANDS']
            stock_col: 股票代码列
            date_col: 日期列
            close_col: 收盘价列

        Returns:
            添加指标后的 DataFrame
        """
        df = df.copy()
        df = df.sort_values([stock_col, date_col])

        for indicator in indicators:
            if indicator.startswith('MA'):
                window = int(indicator[2:])
                df[f'MA{window}'] = df.groupby(stock_col)[close_col].transform(
                    lambda x: x.rolling(window=window, min_periods=1).mean()
                )

            elif indicator == 'RSI':
                delta = df.groupby(stock_col)[close_col].diff()
                gain = delta.where(delta > 0, 0)
                loss = -delta.where(delta < 0, 0)

                avg_gain = gain.groupby(df[stock_col]).rolling(window=14, min_periods=1).mean().values
                avg_loss = loss.groupby(df[stock_col]).rolling(window=14, min_periods=1).mean().values

                rs = avg_gain / (avg_loss + 1e-10)
                df['RSI'] = 100 - (100 / (1 + rs))

            elif indicator == 'MACD':
                ema12 = df.groupby(stock_col)[close_col].transform(
                    lambda x: x.ewm(span=12, min_periods=1).mean()
                )
                ema26 = df.groupby(stock_col)[close_col].transform(
                    lambda x: x.ewm(span=26, min_periods=1).mean()
                )
                df['MACD'] = ema12 - ema26
                df['MACD_Signal'] = df.groupby(stock_col)['MACD'].transform(
                    lambda x: x.ewm(span=9, min_periods=1).mean()
                )

            elif indicator == 'BBANDS':
                df['BB_Middle'] = df.groupby(stock_col)[close_col].transform(
                    lambda x: x.rolling(window=20, min_periods=1).mean()
                )
                df['BB_Std'] = df.groupby(stock_col)[close_col].transform(
                    lambda x: x.rolling(window=20, min_periods=1).std()
                )
                df['BB_Upper'] = df['BB_Middle'] + 2 * df['BB_Std']
                df['BB_Lower'] = df['BB_Middle'] - 2 * df['BB_Std']

        return df

    @staticmethod
    def resample_to_panel(
        df: pd.DataFrame,
        freq: str = 'W',
        agg_func: str = 'last',
        date_col: str = 'trade_date',
        stock_col: str = 'stock_code'
    ) -> pd.DataFrame:
        """重采样为面板数据

        Args:
            df: 原始数据
            freq: 重采样频率 ('W'周, 'M'月, 'Q'季)
            agg_func: 聚合函数
            date_col: 日期列
            stock_col: 股票代码列

        Returns:
            重采样后的 DataFrame
        """
        df = df.copy()
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.set_index(date_col)

        # 按股票分组重采样
        resampled = df.groupby(stock_col).resample(freq).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': agg_func,
            'volume': 'sum',
            'amount': 'sum'
        })

        resampled = resampled.reset_index()
        return resampled
