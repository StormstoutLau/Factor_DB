"""
多源数据融合分析模块

实现功能：
    - 跨数据类型的数据合并
    - 多源数据驱动的综合评分
    - 多维度选股信号
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List, Dict

import pandas as pd
import numpy as np

from query.price_query import PriceQuery
from query.factor_query import FactorQuery
from query.macro_query import MacroQuery
from query.sentiment_query import SentimentQuery
from query.alternative_query import AlternativeQuery

logger = logging.getLogger(__name__)


class MultiSourceAnalysis:
    """多源数据融合分析

    Example:
        msa = MultiSourceAnalysis('market.db')

        # 综合评分
        score = msa.combine_score(['000001.SZ'], date(2024, 6, 30))

        # 多维度选股
        stocks = msa.multi_dimension_screen(date(2024, 6, 30))
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        """初始化

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.price_query = PriceQuery(db_path)
        self.factor_query = FactorQuery(db_path)
        self.macro_query = MacroQuery(db_path)
        self.sentiment_query = SentimentQuery(db_path)
        self.alternative_query = AlternativeQuery(db_path)

    def combine_score(self, stock_codes: List[str],
                     trade_date: date,
                     weights: Optional[Dict[str, float]] = None) -> pd.DataFrame:
        """综合评分

        综合价格、因子、宏观、舆情、另类数据的评分。

        Args:
            stock_codes: 股票代码列表
            trade_date: 交易日期
            weights: 各维度权重
                默认: {'factor': 0.3, 'sentiment': 0.2, 'macro': 0.2, 'alternative': 0.15, 'price': 0.15}

        Returns:
            综合评分 DataFrame
        """
        if weights is None:
            weights = {
                'factor': 0.3,
                'sentiment': 0.2,
                'macro': 0.2,
                'alternative': 0.15,
                'price': 0.15
            }

        results = []

        for stock_code in stock_codes:
            score = 0.0

            # 因子评分
            if weights.get('factor', 0) > 0:
                factor_score = self._calculate_factor_score(stock_code, trade_date)
                score += weights['factor'] * factor_score

            # 舆情评分
            if weights.get('sentiment', 0) > 0:
                sentiment_score = self._calculate_sentiment_score(stock_code, trade_date)
                score += weights['sentiment'] * sentiment_score

            # 宏观评分
            if weights.get('macro', 0) > 0:
                macro_score = self._calculate_macro_score(trade_date)
                score += weights['macro'] * macro_score

            # 另类数据评分
            if weights.get('alternative', 0) > 0:
                alt_score = self._calculate_alternative_score(stock_code, trade_date)
                score += weights['alternative'] * alt_score

            # 价格评分
            if weights.get('price', 0) > 0:
                price_score = self._calculate_price_score(stock_code, trade_date)
                score += weights['price'] * price_score

            results.append({
                'stock_code': stock_code,
                'total_score': score,
                'factor_score': factor_score if weights.get('factor', 0) > 0 else None,
                'sentiment_score': sentiment_score if weights.get('sentiment', 0) > 0 else None,
                'macro_score': macro_score if weights.get('macro', 0) > 0 else None,
                'alternative_score': alt_score if weights.get('alternative', 0) > 0 else None,
                'price_score': price_score if weights.get('price', 0) > 0 else None,
            })

        df = pd.DataFrame(results)
        df = df.sort_values('total_score', ascending=False)

        logger.info(f"综合评分完成: {len(df)} 只标的")
        return df

    def _calculate_factor_score(self, stock_code: str, trade_date: date) -> float:
        """计算因子评分"""
        try:
            # 获取因子截面数据
            factor_df = self.factor_query.get_cross_section('PE', trade_date)
            if factor_df.empty:
                return 0.5

            # 计算分位数
            stock_factor = factor_df[factor_df['stock_code'] == stock_code]['factor_value']
            if stock_factor.empty:
                return 0.5

            # 假设低PE是好的（简化逻辑）
            rank = factor_df['factor_value'].rank(pct=True)
            stock_rank = rank[factor_df['stock_code'] == stock_code].iloc[0]
            return 1 - stock_rank  # 低PE得高分
        except Exception:
            return 0.5

    def _calculate_sentiment_score(self, stock_code: str, trade_date: date) -> float:
        """计算舆情评分"""
        try:
            sentiment_df = self.sentiment_query.get_stock_sentiment(stock_code, trade_date, trade_date)
            if sentiment_df.empty:
                return 0.5

            avg_sentiment = sentiment_df['sentiment_score'].mean()
            # 将 [-1, 1] 映射到 [0, 1]
            return (avg_sentiment + 1) / 2
        except Exception:
            return 0.5

    def _calculate_macro_score(self, trade_date: date) -> float:
        """计算宏观评分"""
        try:
            # 获取PMI数据
            pmi_df = self.macro_query.get_macro_data(['PMI'], trade_date, trade_date)
            if pmi_df.empty:
                return 0.5

            pmi = pmi_df['value'].iloc[0]
            # PMI > 50 为扩张，得分高
            if pmi >= 50:
                return min(1.0, 0.5 + (pmi - 50) / 20)
            else:
                return max(0.0, 0.5 - (50 - pmi) / 20)
        except Exception:
            return 0.5

    def _calculate_alternative_score(self, stock_code: str, trade_date: date) -> float:
        """计算另类数据评分"""
        try:
            # 获取产业链数据（简化）
            alt_df = self.alternative_query.get_data(
                'chain', entity_id=stock_code[:2],  # 简化：按行业前缀
                start_date=trade_date, end_date=trade_date
            )
            if alt_df.empty:
                return 0.5

            # 标准化得分
            value = alt_df['value'].iloc[0]
            return min(1.0, max(0.0, (value - 3000) / 1000))
        except Exception:
            return 0.5

    def _calculate_price_score(self, stock_code: str, trade_date: date) -> float:
        """计算价格评分"""
        try:
            # 获取近期价格
            price_df = self.price_query.get_daily(
                [stock_code],
                start_date=pd.Timestamp(trade_date) - pd.Timedelta(days=20),
                end_date=trade_date
            )
            if price_df.empty or len(price_df) < 5:
                return 0.5

            # 计算动量（20日收益率）
            returns = price_df['close'].iloc[-1] / price_df['close'].iloc[0] - 1
            # 将收益率映射到 [0, 1]
            return min(1.0, max(0.0, 0.5 + returns * 10))
        except Exception:
            return 0.5

    def multi_dimension_screen(self, trade_date: date,
                              stock_pool: Optional[List[str]] = None,
                              min_score: float = 0.6,
                              top_n: int = 50) -> pd.DataFrame:
        """多维度选股

        Args:
            trade_date: 交易日期
            stock_pool: 股票池
            min_score: 最低综合评分
            top_n: 返回前N只

        Returns:
            选股结果 DataFrame
        """
        if stock_pool is None:
            stock_pool = self.price_query.get_stock_list(trade_date)

        # 计算综合评分
        scores = self.combine_score(stock_pool, trade_date)

        # 过滤和排序
        scores = scores[scores['total_score'] >= min_score]
        scores = scores.head(top_n)

        logger.info(f"多维度选股完成: {len(scores)} 只标的")
        return scores

    def get_multi_source_panel(self, stock_codes: List[str],
                              trade_date: date) -> pd.DataFrame:
        """获取多源数据面板

        Args:
            stock_codes: 股票代码列表
            trade_date: 交易日期

        Returns:
            多源数据面板 DataFrame
        """
        results = []

        for stock_code in stock_codes:
            row = {'stock_code': stock_code, 'trade_date': trade_date}

            # 价格数据
            price_df = self.price_query.get_daily([stock_code], trade_date, trade_date)
            if not price_df.empty:
                row['close'] = price_df['close'].iloc[0]

            # 因子数据
            factor_df = self.factor_query.get_cross_section('PE', trade_date)
            if not factor_df.empty:
                stock_factor = factor_df[factor_df['stock_code'] == stock_code]
                if not stock_factor.empty:
                    row['PE'] = stock_factor['factor_value'].iloc[0]

            # 舆情数据
            sentiment_df = self.sentiment_query.get_stock_sentiment(stock_code, trade_date, trade_date)
            if not sentiment_df.empty:
                row['sentiment_score'] = sentiment_df['sentiment_score'].mean()
                row['sentiment_count'] = len(sentiment_df)

            results.append(row)

        return pd.DataFrame(results)
