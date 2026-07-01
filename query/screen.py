"""
条件选股模块

提供基于 SQL 的条件选股功能。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

from .base import BaseQuery

logger = logging.getLogger(__name__)


class StockScreener(BaseQuery):
    """条件选股器

    支持多因子组合筛选。

    Example:
        screener = StockScreener('market.db')

        # 简单筛选
        result = screener.screen(
            trade_date=date(2024, 6, 30),
            conditions=[
                "PE > 0 AND PE < 20",
                "PB < 2",
                "market_cap > 1000000000"
            ],
            sort_by='ROE',
            ascending=False,
            limit=50
        )

        # 多因子打分
        result = screener.rank_by_factors(
            trade_date=date(2024, 6, 30),
            factors={
                'PE': -1,      # 负向因子
                'ROE': 1,      # 正向因子
                'PB': -1       # 负向因子
            },
            limit=100
        )
    """

    def _pit_factor_filter(self, trade_date: date, factor_list: str) -> str:
        """构建 PIT 因子过滤条件"""
        return self._build_pit_subquery(
            'factor_data', ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"trade_date = '{trade_date}' AND factor_name IN ({factor_list})"
        )

    def screen(
        self,
        trade_date: date,
        conditions: list[str],
        sort_by: Optional[str] = None,
        ascending: bool = True,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """条件选股

        Args:
            trade_date: 选股日期
            conditions: SQL WHERE 条件列表
            sort_by: 排序字段
            ascending: 是否升序
            limit: 返回数量限制

        Returns:
            选股结果 DataFrame
        """
        # 构建 WHERE 子句
        where_clause = ' AND '.join([f"({c})" for c in conditions])

        # PIT 过滤
        pit_filter = self._build_pit_subquery(
            'factor_data', ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"trade_date = '{trade_date}'"
        )

        # 构建 ORDER BY
        order_clause = ""
        if sort_by:
            direction = "ASC" if ascending else "DESC"
            order_clause = f"ORDER BY {sort_by} {direction}"

        # 构建 LIMIT
        limit_clause = f"LIMIT {limit}" if limit else ""

        sql = f'''
            SELECT stock_code, factor_name, factor_value
            FROM factor_data
            WHERE trade_date = '{trade_date}'
              AND ({where_clause})
              AND {pit_filter}
            {order_clause}
            {limit_clause}
        '''

        df = self._execute_query(sql)
        logger.info(f"选股完成: {len(df)} 只标的 @ {trade_date}")
        return df

    def rank_by_factors(
        self,
        trade_date: date,
        factors: dict[str, int],
        stock_codes: Optional[list[str]] = None,
        limit: Optional[int] = None
    ) -> pd.DataFrame:
        """多因子打分选股

        对每个因子进行排名，然后加权求和。

        Args:
            trade_date: 选股日期
            factors: 因子配置 {因子名: 方向(1正向/-1负向)}
            stock_codes: 股票池
            limit: 返回数量

        Returns:
            打分结果 DataFrame
        """
        factor_names = list(factors.keys())
        factor_list = ', '.join([f"'{f}'" for f in factor_names])

        # 股票过滤
        stock_filter = ""
        if stock_codes:
            codes = ', '.join([f"'{c}'" for c in stock_codes])
            stock_filter = f"AND stock_code IN ({codes})"

        # PIT 过滤
        pit_filter = self._pit_factor_filter(trade_date, factor_list)

        # 构建排名 SQL
        rank_parts = []
        for factor_name, direction in factors.items():
            order = "ASC" if direction == -1 else "DESC"
            rank_parts.append(f'''
                RANK() OVER (PARTITION BY factor_name ORDER BY factor_value {order}) as {factor_name}_rank
            ''')

        ranks_sql = ', '.join(rank_parts)

        # 构建总分 SQL
        score_parts = []
        for factor_name in factor_names:
            score_parts.append(f"{factor_name}_rank")
        score_sql = ' + '.join(score_parts)

        sql = f'''
            WITH ranked AS (
                SELECT
                    stock_code,
                    factor_name,
                    factor_value,
                    {ranks_sql}
                FROM factor_data
                WHERE trade_date = '{trade_date}'
                  AND factor_name IN ({factor_list})
                  {stock_filter}
                  AND {pit_filter}
            ),
            scored AS (
                SELECT
                    stock_code,
                    {score_sql} as total_score
                FROM ranked
                GROUP BY stock_code
            )
            SELECT stock_code, total_score
            FROM scored
            ORDER BY total_score DESC
            {f"LIMIT {limit}" if limit else ""}
        '''

        df = self._execute_query(sql)
        logger.info(f"多因子打分完成: {len(df)} 只标的")
        return df

    def get_industry_distribution(
        self,
        stock_codes: list[str],
        trade_date: Optional[date] = None
    ) -> pd.DataFrame:
        """获取行业分布

        Args:
            stock_codes: 股票代码列表
            trade_date: 日期 (预留)

        Returns:
            行业分布 DataFrame
        """
        codes = ', '.join([f"'{c}'" for c in stock_codes])

        sql = f'''
            SELECT
                industry,
                COUNT(*) as count,
                COUNT(*) * 100.0 / SUM(COUNT(*)) OVER () as percentage
            FROM stock_info
            WHERE stock_code IN ({codes})
            GROUP BY industry
            ORDER BY count DESC
        '''

        df = self._execute_query(sql)
        return df

    def get_quantile_stocks(
        self,
        factor_name: str,
        trade_date: date,
        quantile: float = 0.1,
        top: bool = True
    ) -> pd.DataFrame:
        """获取分位数股票

        Args:
            factor_name: 因子名称
            trade_date: 日期
            quantile: 分位数 (0-1)
            top: 是否取头部

        Returns:
            股票列表 DataFrame
        """
        order = "DESC" if top else "ASC"

        pit_filter = self._build_pit_subquery(
            'factor_data', ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name = '{factor_name}' AND trade_date = '{trade_date}'"
        )

        sql = f'''
            WITH stats AS (
                SELECT
                    PERCENTILE_CONT({quantile}) WITHIN GROUP (ORDER BY factor_value {order}) as threshold
                FROM factor_data
                WHERE factor_name = '{factor_name}' AND trade_date = '{trade_date}'
                  AND {pit_filter}
            )
            SELECT f.stock_code, f.factor_value
            FROM factor_data f, stats s
            WHERE f.factor_name = '{factor_name}'
              AND f.trade_date = '{trade_date}'
              AND {pit_filter}
              AND f.factor_value {'>=' if top else '<='} s.threshold
            ORDER BY f.factor_value {order}
        '''

        df = self._execute_query(sql)
        return df
