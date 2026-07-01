"""
宏观数据与股票因子关联分析模块

实现功能：
    - 宏观指标与因子相关性分析
    - 宏观因子构建
    - 宏观环境识别
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List, Dict

import pandas as pd
import numpy as np

from query.macro_query import MacroQuery
from query.factor_query import FactorQuery

logger = logging.getLogger(__name__)


class MacroFactorLink:
    """宏观-因子关联分析

    Example:
        link = MacroFactorLink('market.db')

        # 计算相关性
        corr = link.calculate_correlation('CPI', 'PE')

        # 构建宏观因子
        macro_factor = link.build_macro_factor(['CPI', 'PPI', 'PMI'])

        # 识别宏观环境
        regime = link.identify_macro_regime(date(2024, 1, 1), date(2024, 12, 31))
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        """初始化

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.macro_query = MacroQuery(db_path)
        self.factor_query = FactorQuery(db_path)

    def calculate_correlation(self, macro_indicator: str,
                            factor_name: str,
                            start_date: Optional[date] = None,
                            end_date: Optional[date] = None,
                            method: str = 'pearson') -> float:
        """计算宏观指标与因子的相关性

        Args:
            macro_indicator: 宏观指标ID
            factor_name: 因子名称
            start_date: 开始日期
            end_date: 结束日期
            method: 相关性方法：pearson/spearman

        Returns:
            相关系数
        """
        # 获取宏观数据
        macro_df = self.macro_query.get_macro_data(
            [macro_indicator], start_date, end_date
        )
        if macro_df.empty:
            logger.warning(f"宏观数据为空: {macro_indicator}")
            return 0.0

        # 获取因子数据（取截面均值）
        factor_df = self.factor_query.get_factor(factor_name, start_date=start_date, end_date=end_date)
        if factor_df.empty:
            logger.warning(f"因子数据为空: {factor_name}")
            return 0.0

        # 计算因子截面均值
        factor_mean = factor_df.groupby('trade_date')['factor_value'].mean().reset_index()
        factor_mean.columns = ['trade_date', 'factor_mean']

        # 合并数据
        merged = pd.merge(macro_df, factor_mean, on='trade_date', how='inner')
        if len(merged) < 3:
            logger.warning("合并后数据不足，无法计算相关性")
            return 0.0

        # 计算相关性
        if method == 'pearson':
            corr = merged['value'].corr(merged['factor_mean'])
        elif method == 'spearman':
            corr = merged['value'].corr(merged['factor_mean'], method='spearman')
        else:
            raise ValueError(f"未知的相关性方法: {method}")

        logger.info(f"相关性计算完成: {macro_indicator} vs {factor_name} = {corr:.4f}")
        return corr

    def calculate_all_correlations(self, macro_indicators: List[str],
                                  factor_names: List[str],
                                  start_date: Optional[date] = None,
                                  end_date: Optional[date] = None) -> pd.DataFrame:
        """计算所有宏观指标与因子的相关性矩阵

        Args:
            macro_indicators: 宏观指标列表
            factor_names: 因子名称列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            相关性矩阵 DataFrame
        """
        results = []

        for macro in macro_indicators:
            for factor in factor_names:
                corr = self.calculate_correlation(macro, factor, start_date, end_date)
                results.append({
                    'macro_indicator': macro,
                    'factor_name': factor,
                    'correlation': corr
                })

        df = pd.DataFrame(results)
        matrix = df.pivot(index='macro_indicator', columns='factor_name', values='correlation')
        return matrix

    def build_macro_factor(self, macro_indicators: List[str],
                          lookback: int = 20,
                          method: str = 'equal_weight') -> pd.DataFrame:
        """构建宏观因子

        Args:
            macro_indicators: 宏观指标列表
            lookback: 回溯期
            method: 加权方法：equal_weight/pca

        Returns:
            宏观因子矩阵 DataFrame
        """
        # 获取宏观数据矩阵
        macro_matrix = self.macro_query.get_macro_matrix(macro_indicators)
        if macro_matrix.empty:
            logger.warning("宏观数据矩阵为空")
            return pd.DataFrame()

        # 标准化
        macro_matrix = macro_matrix.ffill()
        macro_std = (macro_matrix - macro_matrix.rolling(lookback).mean()) / macro_matrix.rolling(lookback).std()
        macro_std = macro_std.dropna()

        if method == 'equal_weight':
            # 等权合成
            macro_factor = macro_std.mean(axis=1)
        elif method == 'pca':
            # PCA 主成分（简化实现）
            from sklearn.decomposition import PCA
            pca = PCA(n_components=1)
            values = macro_std.values
            if len(values) > 0:
                macro_factor_values = pca.fit_transform(values)
                macro_factor = pd.Series(macro_factor_values.flatten(), index=macro_std.index)
            else:
                macro_factor = pd.Series()
        else:
            raise ValueError(f"未知的加权方法: {method}")

        result = pd.DataFrame({
            'trade_date': macro_factor.index,
            'macro_factor': macro_factor.values
        })

        logger.info(f"宏观因子构建完成: {len(result)} 期")
        return result

    def identify_macro_regime(self, start_date: date,
                             end_date: date,
                             n_regimes: int = 4) -> pd.DataFrame:
        """识别宏观环境（基于简单阈值分类）

        Args:
            start_date: 开始日期
            end_date: 结束日期
            n_regimes: 环境分类数量

        Returns:
            每个日期的宏观环境标签
        """
        return self.macro_query.identify_macro_regime(start_date, end_date, n_regimes)

    def get_sector_sensitivity(self, macro_indicator: str,
                              sectors: Optional[List[str]] = None,
                              start_date: Optional[date] = None,
                              end_date: Optional[date] = None) -> pd.DataFrame:
        """获取行业对宏观指标的敏感度

        Args:
            macro_indicator: 宏观指标ID
            sectors: 行业列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            行业敏感度 DataFrame
        """
        # 获取宏观数据
        macro_df = self.macro_query.get_macro_data([macro_indicator], start_date, end_date)
        if macro_df.empty:
            return pd.DataFrame()

        # TODO: 需要行业收益率数据，这里预留接口
        logger.info("行业敏感度分析需要行业收益率数据")
        return pd.DataFrame()
