"""
舆情数据查询模块

提供新闻、研报情感查询、股票相关舆情分析
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List

import pandas as pd

from .base import BaseQuery, QueryFilter

logger = logging.getLogger(__name__)


class SentimentQuery(BaseQuery):
    """舆情数据查询器

    Example:
        query = SentimentQuery('market.db')

        # 获取股票舆情
        df = query.get_stock_sentiment('000001.SZ')

        # 获取情感聚合
        agg = query.get_sentiment_aggregation(['000001.SZ'], freq='W')

        # 获取正面舆情股票
        stocks = query.get_sentiment_stocks('positive', date(2024, 6, 30))
    """

    def get_stock_sentiment(self, stock_code: str,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> pd.DataFrame:
        """获取指定股票的舆情数据

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            舆情数据 DataFrame
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)

        # DuckDB array contains: use list_position or filter with ANY
        sql = f'''
            SELECT *
            FROM news_sentiment
            WHERE list_position(related_stocks, '{stock_code}') > 0
              AND {date_filter}
            ORDER BY publish_date DESC, publish_time DESC
        '''

        return self._execute_query(sql)

    def get_sentiment_aggregation(self, stock_codes: Optional[List[str]] = None,
                                 start_date: Optional[date] = None,
                                 end_date: Optional[date] = None,
                                 freq: str = 'D') -> pd.DataFrame:
        """获取情感数据聚合

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            freq: 聚合频率：D(日)/W(周)/M(月)

        Returns:
            聚合情感数据
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)

        # 股票过滤 - DuckDB 不支持 UNNEST in WHERE EXISTS with array
        # Use simpler approach: if stock_codes specified, filter after query
        # For now, query all and filter in Python if needed

        # 时间聚合
        if freq == 'W':
            date_trunc = "DATE_TRUNC('week', publish_date)"
        elif freq == 'M':
            date_trunc = "DATE_TRUNC('month', publish_date)"
        else:
            date_trunc = "publish_date"

        sql = f'''
            SELECT
                {date_trunc} as period,
                COUNT(*) as news_count,
                AVG(sentiment_score) as avg_sentiment,
                STDDEV(sentiment_score) as sentiment_std,
                SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END) as positive_count,
                SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END) as negative_count,
                SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END) as neutral_count
            FROM news_sentiment
            WHERE {date_filter}
            GROUP BY {date_trunc}
            ORDER BY period
        '''

        df = self._execute_query(sql)

        # 如果指定了股票代码，在 Python 中过滤
        if stock_codes and not df.empty:
            # 需要重新查询带股票过滤的数据
            # 由于 DuckDB 数组过滤限制，使用 ANY 语法
            codes = ', '.join([f"'{c}'" for c in stock_codes])
            sql = f'''
                SELECT
                    {date_trunc} as period,
                    COUNT(*) as news_count,
                    AVG(sentiment_score) as avg_sentiment,
                    STDDEV(sentiment_score) as sentiment_std,
                    SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END) as positive_count,
                    SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END) as negative_count,
                    SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END) as neutral_count
                FROM news_sentiment
                WHERE {date_filter}
                  AND EXISTS (
                      SELECT 1 FROM UNNEST(related_stocks) AS t(s)
                      WHERE t.s IN ({codes})
                  )
                GROUP BY {date_trunc}
                ORDER BY period
            '''
            df = self._execute_query(sql)

        return df

    def get_report_sentiment(self, stock_code: str,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> pd.DataFrame:
        """获取研报情感数据

        Args:
            stock_code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            研报情感数据 DataFrame
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)

        sql = f'''
            SELECT *
            FROM report_sentiment
            WHERE related_stock = '{stock_code}'
              AND {date_filter}
            ORDER BY publish_date DESC
        '''

        return self._execute_query(sql)

    def get_sentiment_stocks(self, sentiment_label: str = 'positive',
                           query_date: Optional[date] = None,
                           limit: int = 100) -> pd.DataFrame:
        """获取指定情感类型的股票列表

        Args:
            sentiment_label: 情感标签
            query_date: 查询日期（默认最新）
            limit: 返回数量限制

        Returns:
            股票列表 DataFrame
        """
        if query_date:
            date_filter = f"publish_date = '{query_date}'"
        else:
            date_filter = "publish_date = (SELECT MAX(publish_date) FROM news_sentiment)"

        # DuckDB: UNNEST not supported in SELECT directly
        # Use a two-step approach: first get matching news, then unnest in Python
        sql = f'''
            SELECT related_stocks, sentiment_score
            FROM news_sentiment
            WHERE {date_filter}
              AND sentiment_label = '{sentiment_label}'
        '''

        df = self._execute_query(sql)
        if df.empty:
            return pd.DataFrame()

        # Unnest in Python - handle DuckDB array format
        rows = []
        for _, row in df.iterrows():
            stocks = row['related_stocks']
            if isinstance(stocks, list):
                for s in stocks:
                    rows.append({'stock_code': s, 'sentiment_score': row['sentiment_score']})
            elif isinstance(stocks, str):
                # DuckDB may return arrays as strings like "['000001.SZ', '600000.SH']"
                import ast
                try:
                    stock_list = ast.literal_eval(stocks)
                    if isinstance(stock_list, list):
                        for s in stock_list:
                            rows.append({'stock_code': s, 'sentiment_score': row['sentiment_score']})
                except (ValueError, SyntaxError):
                    # Fallback: split by comma
                    for s in stocks.strip('[]{}').split(','):
                        s = s.strip().strip('"').strip("'")
                        if s:
                            rows.append({'stock_code': s, 'sentiment_score': row['sentiment_score']})

        if not rows:
            return pd.DataFrame()

        result = pd.DataFrame(rows)
        result = result.groupby('stock_code').agg({
            'sentiment_score': 'mean',
            'stock_code': 'count'
        }).rename(columns={'stock_code': 'news_count'}).reset_index()
        result = result.sort_values('sentiment_score', ascending=False).head(limit)

        return result

    def get_sentiment_trend(self, stock_code: str,
                           window: int = 5,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> pd.DataFrame:
        """获取情感趋势（移动平均）

        Args:
            stock_code: 股票代码
            window: 移动平均窗口
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            情感趋势 DataFrame
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)

        sql = f'''
            WITH daily_sentiment AS (
                SELECT
                    publish_date,
                    AVG(sentiment_score) as daily_avg
                FROM news_sentiment
                WHERE list_position(related_stocks, '{stock_code}') > 0
                  AND {date_filter}
                GROUP BY publish_date
            )
            SELECT
                publish_date,
                daily_avg,
                AVG(daily_avg) OVER (
                    ORDER BY publish_date
                    ROWS BETWEEN {window - 1} PRECEDING AND CURRENT ROW
                ) as ma_sentiment
            FROM daily_sentiment
            ORDER BY publish_date
        '''

        return self._execute_query(sql)

    def get_hot_news(self, query_date: Optional[date] = None,
                    limit: int = 20) -> pd.DataFrame:
        """获取热门新闻

        Args:
            query_date: 查询日期
            limit: 返回数量

        Returns:
            热门新闻 DataFrame
        """
        if query_date:
            date_filter = f"publish_date = '{query_date}'"
        else:
            date_filter = "publish_date = (SELECT MAX(publish_date) FROM news_sentiment)"

        sql = f'''
            SELECT *
            FROM news_sentiment
            WHERE {date_filter}
            ORDER BY (COALESCE(read_count, 0) + COALESCE(comment_count, 0) * 2) DESC
            LIMIT {limit}
        '''

        return self._execute_query(sql)
