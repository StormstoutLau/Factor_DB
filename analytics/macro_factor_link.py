"""
宏观数据与股票因子关联分析模块

实现功能：
    - 宏观指标与因子相关性分析
    - 宏观因子构建
    - 宏观环境识别
"""

from __future__ import annotations

import logging
import math
from datetime import date
from typing import Optional, List

import pandas as pd

from Factor_DB.query.macro_query import MacroQuery
from Factor_DB.query.factor_query import FactorQuery
from Factor_DB.query.alternative_query import AlternativeQuery

logger = logging.getLogger(__name__)

SENSITIVITY_COLUMNS = [
    'industry_code', 'industry_name', 'level', 'beta', 'correlation',
    't_stat', 'r_squared', 'n_obs', 'start_date', 'end_date',
]

# 率型指标：值可为 0 或负（如 CPI 环比、各类同比、利率），
# 对率再取 pct_change 会产生零基数 Inf 与负基数符号反转，默认改用 diff。
RATE_TYPE_INDICATORS = frozenset({
    'CPI_MONTHLY', 'CPI_YEARLY', 'PPI_YEARLY',
    'M2_YEARLY', 'GDP_YEARLY', 'INTEREST_RATE',
})


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
        self.alternative_query = AlternativeQuery(db_path)

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
                              end_date: Optional[date] = None,
                              as_of: Optional[date] = None,
                              level: Optional[str] = 'first',
                              transform: Optional[str] = None,
                              min_obs: int = 12) -> pd.DataFrame:
        """获取行业对宏观指标的敏感度

        行业日收益按月复利为月收益，宏观指标按月取最后观测后做变换，
        再逐行业做单因子 OLS：

            r_i,t = alpha + beta * delta_m,t + eps

        返回的 ``beta`` 即行业对宏观指标月度变化的线性暴露。

        Args:
            macro_indicator: 宏观指标ID
            sectors: 行业代码列表（None 表示全部）
            start_date: 开始日期
            end_date: 结束日期
            as_of: PIT 截止日期（只使用该日期前已发布的宏观数据）
            level: 行业级别：first/second/third；None 表示不限制
            transform: 宏观变换：pct_change/diff/level；None 表示按指标类型
                自动选择（率型指标用 diff，水平型指标用 pct_change）
            min_obs: 行业最少有效月度观测数（小于 3 抛 ValueError）

        Returns:
            行业敏感度 DataFrame，固定列见 ``SENSITIVITY_COLUMNS``；
            无数据或宏观无变化时返回同列空表。

        Notes:
            ``macro_data.trade_date`` 为统计期（ADR-0012），与行业月收益按
            统计期对齐；``as_of`` 通过 ``publish_date`` 过滤，可避免使用
            当时尚未发布的数据。
        """
        if level not in {None, 'first', 'second', 'third'}:
            raise ValueError(f"未知的行业级别: {level}")
        if transform is None:
            transform = (
                'diff' if macro_indicator in RATE_TYPE_INDICATORS
                else 'pct_change'
            )
        if transform not in {'pct_change', 'diff', 'level'}:
            raise ValueError(f"未知的宏观变换: {transform}")
        if min_obs < 3:
            raise ValueError(f"min_obs 必须 >= 3: {min_obs}")

        empty = pd.DataFrame(columns=SENSITIVITY_COLUMNS)

        # 1) 宏观月度序列与变换
        macro_df = self.macro_query.get_macro_data(
            [macro_indicator], start_date, end_date, as_of=as_of
        )
        if macro_df.empty:
            logger.warning(f"宏观数据为空: {macro_indicator}")
            return empty

        macro_df = macro_df.dropna(subset=['value']).copy()
        macro_df['period'] = pd.to_datetime(macro_df['trade_date']).dt.to_period('M')
        macro_monthly = (
            macro_df.groupby('period')['value']
            .last()
            .sort_index()
        )

        if transform == 'pct_change':
            macro_monthly = macro_monthly.pct_change(fill_method=None)
        elif transform == 'diff':
            macro_monthly = macro_monthly.diff()

        macro_monthly = macro_monthly.replace(
            [float('inf'), float('-inf')], float('nan')
        ).dropna()
        if macro_monthly.empty:
            logger.warning(f"宏观月度变化为空: {macro_indicator}")
            return empty

        macro_series = pd.DataFrame({
            'period': macro_monthly.index,
            'macro_change': macro_monthly.to_numpy(dtype=float),
        })

        # 2) 行业日收益 → 月度复利收益
        alt_df = self.alternative_query.get_data(
            data_type='industry_return',
            data_subtype=level,
            start_date=start_date,
            end_date=end_date,
        )
        if alt_df.empty:
            logger.warning("行业收益数据为空，请先执行 collect --source sw_industry")
            return empty
        if sectors is not None:
            alt_df = alt_df[alt_df['entity_id'].isin(sectors)]
        if alt_df.empty:
            logger.warning(f"指定行业无收益数据: {sectors}")
            return empty

        alt_df = alt_df.dropna(subset=['value']).copy()
        alt_df['period'] = pd.to_datetime(alt_df['trade_date']).dt.to_period('M')
        alt_df['gross'] = 1.0 + alt_df['value'].astype(float)

        monthly = (
            alt_df.groupby(
                ['entity_id', 'entity_name', 'data_subtype', 'period'],
                dropna=False,
            )['gross']
            .prod()
            .reset_index()
        )
        monthly['industry_return'] = monthly['gross'] - 1.0

        merged = monthly.merge(macro_series, on='period', how='inner')
        if merged.empty:
            logger.warning(f"宏观与行业收益无可对齐月份: {macro_indicator}")
            return empty

        # 3) 逐行业 OLS
        rows = []
        for (code, name, subtype), group in merged.groupby(
            ['entity_id', 'entity_name', 'data_subtype'],
            dropna=False,
            sort=True,
        ):
            group = group.sort_values('period')
            n_obs = len(group)
            if n_obs < min_obs:
                logger.debug(f"行业样本不足，跳过: {code} ({n_obs} < {min_obs})")
                continue

            x = group['macro_change'].to_numpy(dtype=float)
            y = group['industry_return'].to_numpy(dtype=float)
            x_mean = float(x.mean())
            y_mean = float(y.mean())
            sxx = float(((x - x_mean) ** 2).sum())
            if sxx <= 0:
                logger.debug(f"宏观变化无方差，跳过: {code}")
                continue

            sxy = float(((x - x_mean) * (y - y_mean)).sum())
            syy = float(((y - y_mean) ** 2).sum())
            beta = sxy / sxx
            alpha = y_mean - beta * x_mean
            sse = max(0.0, float(((y - alpha - beta * x) ** 2).sum()))

            se = math.sqrt(sse / (n_obs - 2) / sxx) if n_obs > 2 else 0.0
            t_stat = beta / se if se > 0 else 0.0
            r_squared = 1.0 - sse / syy if syy > 0 else 0.0
            correlation = sxy / math.sqrt(sxx * syy) if syy > 0 else 0.0

            rows.append({
                'industry_code': code,
                'industry_name': name,
                'level': subtype,
                'beta': beta,
                'correlation': correlation,
                't_stat': t_stat,
                'r_squared': r_squared,
                'n_obs': n_obs,
                'start_date': group['period'].min().start_time.date(),
                'end_date': group['period'].max().end_time.date(),
            })

        if not rows:
            logger.warning(f"无行业满足 min_obs={min_obs}: {macro_indicator}")
            return empty

        result = pd.DataFrame(rows, columns=SENSITIVITY_COLUMNS)
        result['_abs_beta'] = result['beta'].abs()
        result = (
            result.sort_values(
                ['_abs_beta', 'industry_code'], ascending=[False, True]
            )
            .drop(columns='_abs_beta')
            .reset_index(drop=True)
        )
        logger.info(
            f"行业敏感度完成: {macro_indicator} / {len(result)} 个行业 "
            f"({transform}, level={level})"
        )
        return result
