"""
数据验证模块

提供数据格式、类型、范围的验证功能。
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """验证错误"""
    pass


class DataValidator:
    """数据验证器

    提供统一的数据验证接口。

    Example:
        validator = DataValidator()

        # 验证 DataFrame
        validator.validate_dataframe(df, required_cols=['trade_date', 'stock_code', 'close'])

        # 验证价格数据
        validator.validate_price_data(df)

        # 验证因子数据
        validator.validate_factor_data(df)
    """

    def __init__(self, max_null_ratio: float = 0.5):
        """初始化验证器

        Args:
            max_null_ratio: 最大允许空值比例
        """
        self.max_null_ratio = max_null_ratio

    def validate_dataframe(
        self,
        df: pd.DataFrame,
        required_cols: list[str],
        name: str = 'DataFrame'
    ) -> bool:
        """验证 DataFrame 基本结构

        Args:
            df: 待验证的 DataFrame
            required_cols: 必需列
            name: 数据名称

        Returns:
            是否验证通过

        Raises:
            ValidationError: 验证失败时抛出
        """
        if df is None:
            raise ValidationError(f"{name} 为 None")

        if df.empty:
            raise ValidationError(f"{name} 为空")

        # 检查必需列
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            raise ValidationError(f"{name} 缺少必需列: {missing}")

        return True

    def validate_price_data(self, df: pd.DataFrame) -> bool:
        """验证价格数据

        Args:
            df: 价格数据 DataFrame

        Returns:
            是否验证通过

        Raises:
            ValidationError: 验证失败时抛出
        """
        self.validate_dataframe(df, ['trade_date', 'stock_code', 'close'], '价格数据')

        # 检查 OHLC 逻辑
        if all(col in df.columns for col in ['open', 'high', 'low', 'close']):
            invalid = df[
                (df['high'] < df['low']) |
                (df['high'] < df['open']) |
                (df['high'] < df['close']) |
                (df['low'] > df['open']) |
                (df['low'] > df['close'])
            ]
            if len(invalid) > 0:
                logger.warning(f"发现 {len(invalid)} 条 OHLC 逻辑异常数据")

        # 检查价格非负
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if col in df.columns:
                negative = df[df[col] < 0]
                if len(negative) > 0:
                    raise ValidationError(f"发现 {len(negative)} 条负价格数据")

        # 检查空值比例
        null_ratio = df.isnull().sum() / len(df)
        high_null = null_ratio[null_ratio > self.max_null_ratio]
        if not high_null.empty:
            logger.warning(f"列空值比例过高: {high_null.to_dict()}")

        return True

    def validate_factor_data(self, df: pd.DataFrame) -> bool:
        """验证因子数据

        Args:
            df: 因子数据 DataFrame

        Returns:
            是否验证通过

        Raises:
            ValidationError: 验证失败时抛出
        """
        self.validate_dataframe(df, ['trade_date', 'stock_code', 'factor_name', 'factor_value'], '因子数据')

        # 检查因子名称非空
        empty_names = df[df['factor_name'].isnull() | (df['factor_name'] == '')]
        if len(empty_names) > 0:
            raise ValidationError(f"发现 {len(empty_names)} 条空因子名称")

        # 检查因子值为数值
        if not pd.api.types.is_numeric_dtype(df['factor_value']):
            raise ValidationError("factor_value 应为数值类型")

        # 检查无穷值
        inf_count = df['factor_value'].isin([float('inf'), float('-inf')]).sum()
        if inf_count > 0:
            logger.warning(f"发现 {inf_count} 个无穷值")

        return True

    def validate_level1_data(self, df: pd.DataFrame) -> bool:
        """验证 Level 1 数据

        Args:
            df: Level 1 数据 DataFrame

        Returns:
            是否验证通过

        Raises:
            ValidationError: 验证失败时抛出
        """
        self.validate_dataframe(df, ['trade_date', 'trade_time', 'stock_code'], 'Level 1 数据')

        # 验证时间格式
        if 'trade_time' in df.columns:
            invalid_time = df[~df['trade_time'].astype(str).str.match(r'^\d{2}:\d{2}:\d{2}$')]
            if len(invalid_time) > 0:
                logger.warning(f"发现 {len(invalid_time)} 条时间格式异常数据")

        # 验证交易时间范围 (A股: 9:30-11:30, 13:00-15:00)
        # 这里只做基本验证，详细验证可在业务层处理

        return True

    def validate_stock_codes(self, codes: list[str]) -> bool:
        """验证股票代码格式

        Args:
            codes: 股票代码列表

        Returns:
            是否验证通过
        """
        for code in codes:
            if not isinstance(code, str):
                raise ValidationError(f"股票代码应为字符串: {code}")

            # A股格式: 000001.SZ 或 600000.SH
            parts = code.split('.')
            if len(parts) != 2:
                logger.warning(f"股票代码格式可能不正确: {code}")
                continue

            num, exchange = parts
            if exchange not in ('SH', 'SZ', 'BJ'):
                logger.warning(f"未知交易所: {exchange}")

        return True

    def validate_date_range(
        self,
        start_date: date,
        end_date: date,
        max_range_days: Optional[int] = None
    ) -> bool:
        """验证日期范围

        Args:
            start_date: 开始日期
            end_date: 结束日期
            max_range_days: 最大范围天数

        Returns:
            是否验证通过
        """
        if start_date > end_date:
            raise ValidationError(f"开始日期 {start_date} 晚于结束日期 {end_date}")

        if max_range_days:
            days = (end_date - start_date).days
            if days > max_range_days:
                raise ValidationError(f"日期范围 {days} 天超过最大限制 {max_range_days} 天")

        return True
