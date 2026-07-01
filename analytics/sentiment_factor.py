"""
舆情数据与因子关联分析模块

实现功能：
    - 舆情情感与因子相关性分析
    - 舆情因子构建
    - 舆情驱动的选股信号
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List

import pandas as pd
import numpy as np

from query.sentiment_query import SentimentQuery
from query.factor_query import FactorQuery

logger = logging.getLogger(__name__)


class SentimentFactor:
    """舆情因子分析

    Example:
        sf = SentimentFactor('market.db')

        # 构建舆情因子
        factor = sf.build_sentiment_factor(['000001.SZ', '600000.SH'])

        # 获取舆情选股信号
        signals = sf.get_sentiment_signals(date(2024, 6, 30))
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        """初始化

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.sentiment_query = SentimentQuery(db_path)
        self.factor_query = FactorQuery(db_path)

    def build_sentiment_factor(self, stock_codes: List[str],
                              start_date: Optional[date] = None,
                              end_date: Optional[date] = None,
                              window: int = 5) -> pd.DataFrame:
        """构建舆情因子

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            window: 移动平均窗口

        Returns:
            舆情因子 DataFrame
        """
        # 获取情感聚合数据
        agg_df = self.sentiment_query.get_sentiment_aggregation(
            stock_codes, start_date, end_date, freq='D'
        )

        if agg_df.empty:
            logger.warning("情感聚合数据为空")
            return pd.DataFrame()

        # 计算移动平均
        agg_df['sentiment_ma'] = agg_df['avg_sentiment'].rolling(window=window, min_periods=1).mean()

        # 计算情感变化率
        agg_df['sentiment_change'] = agg_df['sentiment_ma'].diff()

        logger.info(f"舆情因子构建完成: {len(agg_df)} 期")
        return agg_df

    def get_sentiment_signals(self, trade_date: date,
                             top_n: int = 50,
                             min_news_count: int = 5) -> pd.DataFrame:
        """获取舆情选股信号

        Args:
            trade_date: 交易日期
            top_n: 返回前N只股票
            min_news_count: 最少新闻数量

        Returns:
            选股信号 DataFrame
        """
        # 获取正面舆情股票
        positive_df = self.sentiment_query.get_sentiment_stocks(
            'positive', trade_date, limit=top_n * 2
        )

        if positive_df.empty:
            logger.warning(f"无正面舆情数据: {trade_date}")
            return pd.DataFrame()

        # 过滤新闻数量
        positive_df = positive_df[positive_df['news_count'] >= min_news_count]

        # 排序并取前N
        positive_df = positive_df.head(top_n)

        logger.info(f"舆情选股信号生成完成: {len(positive_df)} 只标的")
        return positive_df

    def analyze_sentiment_impact(self, stock_code: str,
                                event_date: date,
                                window: int = 10) -> dict:
        """分析舆情事件对股票的影响

        Args:
            stock_code: 股票代码
            event_date: 事件日期
            window: 分析窗口

        Returns:
            影响分析结果字典
        """
        # 获取事件前后的情感数据
        start_date = pd.Timestamp(event_date) - pd.Timedelta(days=window)
        end_date = pd.Timestamp(event_date) + pd.Timedelta(days=window)

        sentiment_df = self.sentiment_query.get_stock_sentiment(
            stock_code, start_date.date(), end_date.date()
        )

        if sentiment_df.empty:
            return {}

        # 计算事件前后情感变化
        before = sentiment_df[sentiment_df['publish_date'] < event_date]['sentiment_score'].mean()
        after = sentiment_df[sentiment_df['publish_date'] >= event_date]['sentiment_score'].mean()

        return {
            'stock_code': stock_code,
            'event_date': event_date,
            'sentiment_before': before,
            'sentiment_after': after,
            'sentiment_change': after - before,
            'news_count': len(sentiment_df)
        }

    def get_sentiment_factor_ic(self, factor_name: str = 'sentiment_score',
                               forward_return_days: int = 5,
                               start_date: Optional[date] = None,
                               end_date: Optional[date] = None) -> pd.DataFrame:
        """计算舆情因子的IC（信息系数）

        Args:
            factor_name: 因子名称
            forward_return_days: 前瞻收益率天数
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            IC序列 DataFrame
        """
        # 获取舆情数据
        # 这里简化实现，实际需要从 news_sentiment 表中提取每日情感得分
        logger.info("舆情因子IC计算需要完整的情感日度数据")
        return pd.DataFrame()
