"""
因子计算引擎

提供量价因子、技术因子、截面因子等计算功能。
所有计算使用 pandas 向量化操作，对每只股票独立计算。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class FactorCalculator:
    """因子计算引擎

    所有方法均为静态方法，可直接调用无需实例化。
    输入 DataFrame 应包含 stock_code 列用于分组计算。
    """

    # ==================================================================
    # 量价因子
    # ==================================================================

    @staticmethod
    def momentum(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
        """动量因子

        计算指定周期的收益率。

        Args:
            df: 包含 stock_code, close 的 DataFrame
            periods: 周期列表，默认 [5, 10, 20]

        Returns:
            添加了 momentum_{N} 列的 DataFrame
        """
        if periods is None:
            periods = [5, 10, 20]

        result = df.copy()
        for p in periods:
            result[f'momentum_{p}'] = result.groupby('stock_code')['close'].transform(
                lambda x: x.pct_change(p)
            )
        return result

    @staticmethod
    def volatility(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
        """波动率因子

        计算指定窗口的收益率标准差。

        Args:
            df: 包含 stock_code, close 的 DataFrame
            window: 窗口大小

        Returns:
            添加了 volatility_{window} 列的 DataFrame
        """
        result = df.copy()
        result[f'volatility_{window}'] = result.groupby('stock_code')['close'].transform(
            lambda x: x.pct_change().rolling(window).std()
        )
        return result

    @staticmethod
    def turnover_adj(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
        """换手率调整因子

        计算换手率的移动平均。

        Args:
            df: 包含 stock_code, turnover 的 DataFrame
            window: 窗口大小

        Returns:
            添加了 turnover_adj_{window} 列的 DataFrame
        """
        result = df.copy()
        col = f'turnover_adj_{window}'
        if 'turnover' in df.columns:
            result[col] = result.groupby('stock_code')['turnover'].transform(
                lambda x: x.rolling(window).mean()
            )
        else:
            result[col] = np.nan
        return result

    @staticmethod
    def volume_price_trend(df: pd.DataFrame) -> pd.DataFrame:
        """量价趋势因子 (VPT)

        VPT = 累积 (成交量变化 × 价格变化率)

        Args:
            df: 包含 stock_code, close, volume 的 DataFrame

        Returns:
            添加了 vpt 列的 DataFrame
        """
        result = df.copy()

        def _calc_vpt(group):
            group = group.sort_values('trade_date')
            price_change = group['close'].pct_change().fillna(0)
            vol_change = group['volume'].diff().fillna(0)
            return (vol_change * price_change).cumsum()

        result['vpt'] = result.groupby('stock_code', group_keys=False).apply(
            _calc_vpt, include_groups=False
        )
        return result

    # ==================================================================
    # 技术因子
    # ==================================================================

    @staticmethod
    def bollinger_position(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
        """布林带位置

        计算价格在布林带中的相对位置: (close - lower) / (upper - lower)

        Args:
            df: 包含 stock_code, close 的 DataFrame
            window: 窗口大小

        Returns:
            添加了 bollinger_position 列的 DataFrame
        """
        result = df.copy()

        def _calc_bollinger(group):
            group = group.sort_values('trade_date')
            ma = group['close'].rolling(window).mean()
            std = group['close'].rolling(window).std()
            upper = ma + 2 * std
            lower = ma - 2 * std
            band_width = upper - lower
            band_width = band_width.replace(0, np.nan)
            return (group['close'] - lower) / band_width

        result['bollinger_position'] = result.groupby(
            'stock_code', group_keys=False
        ).apply(_calc_bollinger, include_groups=False)
        return result

    @staticmethod
    def atr(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
        """平均真实波幅 (ATR)

        Args:
            df: 包含 stock_code, high, low, close 的 DataFrame
            window: 窗口大小

        Returns:
            添加了 atr_{window} 列的 DataFrame
        """
        result = df.copy()

        def _calc_atr(group):
            group = group.sort_values('trade_date')
            high = group['high']
            low = group['low']
            prev_close = group['close'].shift(1)
            tr = pd.concat([
                high - low,
                (high - prev_close).abs(),
                (low - prev_close).abs()
            ], axis=1).max(axis=1)
            return tr.rolling(window).mean()

        result[f'atr_{window}'] = result.groupby(
            'stock_code', group_keys=False
        ).apply(_calc_atr, include_groups=False)
        return result

    # ==================================================================
    # 截面因子
    # ==================================================================

    @staticmethod
    def industry_neutralize(
        df: pd.DataFrame,
        factor_col: str,
        industry_col: str
    ) -> pd.DataFrame:
        """行业中性化

        将因子值减去其行业均值，消除行业效应。

        Args:
            df: 包含因子列和行业列的 DataFrame
            factor_col: 因子列名
            industry_col: 行业列名

        Returns:
            添加了 {factor_col}_neutralized 列的 DataFrame
        """
        result = df.copy()
        industry_mean = result.groupby(industry_col)[factor_col].transform('mean')
        result[f'{factor_col}_neutralized'] = result[factor_col] - industry_mean
        return result

    @staticmethod
    def market_cap_neutralize(
        df: pd.DataFrame,
        factor_col: str,
        cap_col: str
    ) -> pd.DataFrame:
        """市值中性化

        使用市值对因子值进行截面回归中性化。

        Args:
            df: 包含因子列和市值列的 DataFrame
            factor_col: 因子列名
            cap_col: 市值列名

        Returns:
            添加了 {factor_col}_neutralized 列的 DataFrame
        """
        result = df.copy()
        # 对市值取对数以减少偏态
        log_cap = np.log(result[cap_col].replace(0, np.nan))
        # 简单线性回归：factor = a + b*log_cap
        valid = result[factor_col].notna() & log_cap.notna()
        if valid.sum() > 2:
            coeff = np.polyfit(log_cap[valid], result.loc[valid, factor_col], 1)
            predicted = coeff[0] * log_cap + coeff[1]
            result[f'{factor_col}_neutralized'] = result[factor_col] - predicted
        else:
            result[f'{factor_col}_neutralized'] = result[factor_col]
        return result

    # ==================================================================
    # 批量计算
    # ==================================================================

    @staticmethod
    def calculate_all(df: pd.DataFrame) -> pd.DataFrame:
        """批量计算所有常用因子

        Args:
            df: 包含 stock_code, close, high, low, volume 的 DataFrame

        Returns:
            添加了所有因子列的 DataFrame
        """
        result = df.copy()
        result = FactorCalculator.momentum(result, periods=[5, 20])
        result = FactorCalculator.volatility(result, window=20)
        result = FactorCalculator.bollinger_position(result, window=20)
        result = FactorCalculator.volume_price_trend(result)
        result = FactorCalculator.atr(result, window=14)
        return result