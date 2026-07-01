"""
宏观数据查询模块

提供宏观数据查询、时间序列分析、与股票因子关联查询
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List

import pandas as pd

from .base import BaseQuery, QueryFilter

logger = logging.getLogger(__name__)


class MacroQuery(BaseQuery):
    """宏观数据查询器

    Example:
        query = MacroQuery('market.db')

        # 获取单个指标数据
        df = query.get_macro_data(['CPI'], start_date=date(2024, 1, 1))

        # 获取指标矩阵
        matrix = query.get_macro_matrix(['CPI', 'PPI', 'PMI'])

        # 获取宏观环境标签
        regime = query.identify_macro_regime(date(2024, 1, 1), date(2024, 12, 31))
    """

    def get_macro_data(self, indicator_ids: List[str],
                      start_date: Optional[date] = None,
                      end_date: Optional[date] = None,
                      value_type: str = 'raw') -> pd.DataFrame:
        """获取宏观数据

        Args:
            indicator_ids: 指标ID列表
            start_date: 开始日期
            end_date: 结束日期
            value_type: 值类型：raw/yoy/change/seasonal

        Returns:
            宏观数据 DataFrame
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)

        indicator_list = ', '.join([f"'{i}'" for i in indicator_ids])

        sql = f'''
            SELECT trade_date, indicator_id, indicator_name, value, value_type
            FROM macro_data
            WHERE indicator_id IN ({indicator_list})
              AND value_type = '{value_type}'
              AND {date_filter}
            ORDER BY trade_date, indicator_id
        '''

        df = self._execute_query(sql)
        logger.debug(f"宏观数据查询完成: {len(df)} 条记录")
        return df

    def get_macro_matrix(self, indicator_ids: List[str],
                        start_date: Optional[date] = None,
                        end_date: Optional[date] = None,
                        value_type: str = 'raw') -> pd.DataFrame:
        """获取宏观数据矩阵（日期 × 指标）

        Args:
            indicator_ids: 指标ID列表
            start_date: 开始日期
            end_date: 结束日期
            value_type: 值类型

        Returns:
            宏观数据矩阵 DataFrame
        """
        df = self.get_macro_data(indicator_ids, start_date, end_date, value_type)

        if df.empty:
            return pd.DataFrame()

        # 转为宽格式
        matrix = df.pivot(index='trade_date', columns='indicator_id', values='value')
        return matrix

    def get_indicators(self, category: Optional[str] = None) -> pd.DataFrame:
        """获取指标列表

        Args:
            category: 指标类别过滤

        Returns:
            指标列表 DataFrame
        """
        if category:
            sql = f"SELECT * FROM macro_indicators WHERE category = '{category}' ORDER BY indicator_id"
        else:
            sql = "SELECT * FROM macro_indicators ORDER BY category, indicator_id"

        return self._execute_query(sql)

    def get_indicator_stats(self, indicator_id: str,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> dict:
        """获取指标统计信息

        Args:
            indicator_id: 指标ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            统计信息字典
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)

        sql = f'''
            SELECT
                COUNT(*) as count,
                AVG(value) as mean,
                STDDEV(value) as std,
                MIN(value) as min,
                MAX(value) as max,
                MEDIAN(value) as median
            FROM macro_data
            WHERE indicator_id = '{indicator_id}'
              AND {date_filter}
        '''

        df = self._execute_query(sql)
        if df.empty:
            return {}

        return {
            'count': int(df['count'].iloc[0]),
            'mean': float(df['mean'].iloc[0]),
            'std': float(df['std'].iloc[0]),
            'min': float(df['min'].iloc[0]),
            'max': float(df['max'].iloc[0]),
            'median': float(df['median'].iloc[0])
        }

    def get_latest_values(self, indicator_ids: Optional[List[str]] = None) -> pd.DataFrame:
        """获取最新指标值

        Args:
            indicator_ids: 指标ID列表（None则返回所有）

        Returns:
            最新值 DataFrame
        """
        if indicator_ids:
            indicator_list = ', '.join([f"'{i}'" for i in indicator_ids])
            sql = f'''
                SELECT indicator_id, indicator_name, value, trade_date as latest_date
                FROM macro_data
                WHERE indicator_id IN ({indicator_list})
                  AND trade_date = (
                      SELECT MAX(trade_date) FROM macro_data m2
                      WHERE m2.indicator_id = macro_data.indicator_id
                  )
            '''
        else:
            sql = '''
                SELECT indicator_id, indicator_name, value, trade_date as latest_date
                FROM macro_data
                WHERE trade_date = (
                    SELECT MAX(trade_date) FROM macro_data m2
                    WHERE m2.indicator_id = macro_data.indicator_id
                )
            '''

        return self._execute_query(sql)

    def get_macro_by_date_range(self, start_date: date,
                               end_date: date,
                               categories: Optional[List[str]] = None) -> pd.DataFrame:
        """获取日期范围内的所有宏观数据

        Args:
            start_date: 开始日期
            end_date: 结束日期
            categories: 指标类别过滤

        Returns:
            宏观数据 DataFrame
        """
        category_filter = ""
        if categories:
            cat_list = ', '.join([f"'{c}'" for c in categories])
            category_filter = f"AND mi.category IN ({cat_list})"

        sql = f'''
            SELECT md.trade_date, md.indicator_id, md.indicator_name, md.value, mi.category
            FROM macro_data md
            JOIN macro_indicators mi ON md.indicator_id = mi.indicator_id
            WHERE md.trade_date BETWEEN '{start_date}' AND '{end_date}'
              {category_filter}
            ORDER BY md.trade_date, md.indicator_id
        '''

        return self._execute_query(sql)

    def calculate_yoy(self, indicator_id: str,
                     periods: int = 12) -> pd.DataFrame:
        """计算同比数据

        Args:
            indicator_id: 指标ID
            periods: 同比周期数（月度数据默认12）

        Returns:
            同比数据 DataFrame
        """
        sql = f'''
            WITH ordered AS (
                SELECT trade_date, value,
                       LAG(value, {periods}) OVER (ORDER BY trade_date) as prev_value
                FROM macro_data
                WHERE indicator_id = '{indicator_id}'
                  AND value_type = 'raw'
                ORDER BY trade_date
            )
            SELECT trade_date, value,
                   (value - prev_value) / prev_value * 100 as yoy
            FROM ordered
            WHERE prev_value IS NOT NULL
        '''

        return self._execute_query(sql)

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
        # 获取关键宏观指标
        sql = f'''
            SELECT trade_date, indicator_id, value
            FROM macro_data
            WHERE indicator_id IN ('PMI', 'CPI_yoy', 'M2_yoy')
              AND trade_date BETWEEN '{start_date}' AND '{end_date}'
        '''

        df = self._execute_query(sql)
        if df.empty:
            return pd.DataFrame()

        # 转为宽格式
        wide = df.pivot(index='trade_date', columns='indicator_id', values='value')

        # 简单的环境分类逻辑
        def classify_regime(row):
            pmi = row.get('PMI', 50)
            cpi = row.get('CPI_yoy', 2)

            if pmi >= 50 and cpi >= 2:
                return '过热'
            elif pmi >= 50 and cpi < 2:
                return '复苏'
            elif pmi < 50 and cpi >= 2:
                return '滞胀'
            else:
                return '衰退'

        wide['regime'] = wide.apply(classify_regime, axis=1)

        return wide.reset_index()[['trade_date', 'regime']]
