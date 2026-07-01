"""
另类数据查询模块

提供另类数据的查询、统计和分析接口
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List

import pandas as pd

from .base import BaseQuery, QueryFilter

logger = logging.getLogger(__name__)


class AlternativeQuery(BaseQuery):
    """另类数据查询器

    Example:
        query = AlternativeQuery('market.db')

        # 获取产业链数据
        df = query.get_data(data_type='chain', entity_id='steel')

        # 获取卫星数据
        df = query.get_satellite_data('port_throughput')

        # 获取互联网数据
        df = query.get_e_commerce_data('search_index')
    """

    def get_data(self, data_type: str,
                data_subtype: Optional[str] = None,
                entity_type: Optional[str] = None,
                entity_id: Optional[str] = None,
                start_date: Optional[date] = None,
                end_date: Optional[date] = None) -> pd.DataFrame:
        """获取另类数据

        Args:
            data_type: 数据类型
            data_subtype: 数据子类型
            entity_type: 实体类型
            entity_id: 实体ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            另类数据 DataFrame
        """
        conditions = [f"data_type = '{data_type}'"]

        if data_subtype:
            conditions.append(f"data_subtype = '{data_subtype}'")
        if entity_type:
            conditions.append(f"entity_type = '{entity_type}'")
        if entity_id:
            conditions.append(f"entity_id = '{entity_id}'")
        if start_date:
            conditions.append(f"trade_date >= '{start_date}'")
        if end_date:
            conditions.append(f"trade_date <= '{end_date}'")

        where_clause = ' AND '.join(conditions)

        sql = f'''
            SELECT *
            FROM alternative_data
            WHERE {where_clause}
            ORDER BY trade_date
        '''

        return self._execute_query(sql)

    def get_data_types(self) -> pd.DataFrame:
        """获取所有数据类型

        Returns:
            数据类型 DataFrame
        """
        sql = "SELECT * FROM alternative_types ORDER BY data_type"
        return self._execute_query(sql)

    def get_entities(self, data_type: str,
                    data_subtype: Optional[str] = None) -> pd.DataFrame:
        """获取实体列表

        Args:
            data_type: 数据类型
            data_subtype: 数据子类型

        Returns:
            实体列表 DataFrame
        """
        conditions = [f"data_type = '{data_type}'"]
        if data_subtype:
            conditions.append(f"data_subtype = '{data_subtype}'")

        where_clause = ' AND '.join(conditions)

        sql = f'''
            SELECT DISTINCT entity_type, entity_id, entity_name
            FROM alternative_data
            WHERE {where_clause}
            ORDER BY entity_type, entity_id
        '''

        return self._execute_query(sql)

    def get_time_series(self, data_type: str,
                       data_subtype: str,
                       entity_id: str,
                       start_date: Optional[date] = None,
                       end_date: Optional[date] = None) -> pd.DataFrame:
        """获取时间序列数据

        Args:
            data_type: 数据类型
            data_subtype: 数据子类型
            entity_id: 实体ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            时间序列 DataFrame
        """
        return self.get_data(data_type, data_subtype, entity_id=entity_id,
                           start_date=start_date, end_date=end_date)

    def get_latest_data(self, data_type: str,
                       data_subtype: Optional[str] = None) -> pd.DataFrame:
        """获取最新数据

        Args:
            data_type: 数据类型
            data_subtype: 数据子类型

        Returns:
            最新数据 DataFrame
        """
        conditions = [f"data_type = '{data_type}'"]
        if data_subtype:
            conditions.append(f"data_subtype = '{data_subtype}'")

        where_clause = ' AND '.join(conditions)

        sql = f'''
            SELECT *
            FROM alternative_data
            WHERE {where_clause}
              AND trade_date = (
                  SELECT MAX(trade_date) FROM alternative_data
                  WHERE {where_clause}
              )
            ORDER BY entity_id
        '''

        return self._execute_query(sql)

    def get_data_stats(self, data_type: str,
                      data_subtype: Optional[str] = None,
                      entity_id: Optional[str] = None) -> pd.DataFrame:
        """获取数据统计信息

        Args:
            data_type: 数据类型
            data_subtype: 数据子类型
            entity_id: 实体ID

        Returns:
            统计信息 DataFrame
        """
        conditions = [f"data_type = '{data_type}'"]
        if data_subtype:
            conditions.append(f"data_subtype = '{data_subtype}'")
        if entity_id:
            conditions.append(f"entity_id = '{entity_id}'")

        where_clause = ' AND '.join(conditions)

        sql = f'''
            SELECT
                entity_id,
                COUNT(*) as count,
                MIN(trade_date) as start_date,
                MAX(trade_date) as end_date,
                AVG(value) as mean,
                STDDEV(value) as std,
                MIN(value) as min,
                MAX(value) as max
            FROM alternative_data
            WHERE {where_clause}
            GROUP BY entity_id
            ORDER BY entity_id
        '''

        return self._execute_query(sql)

    def get_correlation_with_price(self, data_type: str,
                                  data_subtype: str,
                                  entity_id: str,
                                  stock_code: str,
                                  price_field: str = 'close',
                                  lag: int = 0) -> float:
        """计算另类数据与股价的相关性

        Args:
            data_type: 数据类型
            data_subtype: 数据子类型
            entity_id: 实体ID
            stock_code: 股票代码
            price_field: 价格字段
            lag: 滞后天数

        Returns:
            相关系数
        """
        sql = f'''
            WITH alt AS (
                SELECT trade_date, value
                FROM alternative_data
                WHERE data_type = '{data_type}'
                  AND data_subtype = '{data_subtype}'
                  AND entity_id = '{entity_id}'
            ),
            price AS (
                SELECT trade_date, {price_field}
                FROM daily_prices
                WHERE stock_code = '{stock_code}'
            )
            SELECT CORR(a.value, p.{price_field}) as correlation
            FROM alt a
            JOIN price p ON a.trade_date = p.trade_date - {lag}
        '''

        df = self._execute_query(sql)
        if df.empty:
            return 0.0
        return float(df['correlation'].iloc[0])

    def get_satellite_data(self, data_subtype: str,
                          entity_id: Optional[str] = None,
                          start_date: Optional[date] = None,
                          end_date: Optional[date] = None) -> pd.DataFrame:
        """获取卫星数据

        Args:
            data_subtype: 数据子类型
            entity_id: 实体ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            卫星数据 DataFrame
        """
        return self.get_data('satellite', data_subtype, entity_id=entity_id,
                           start_date=start_date, end_date=end_date)

    def get_chain_data(self, data_subtype: str,
                      entity_id: Optional[str] = None,
                      start_date: Optional[date] = None,
                      end_date: Optional[date] = None) -> pd.DataFrame:
        """获取产业链数据

        Args:
            data_subtype: 数据子类型
            entity_id: 实体ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            产业链数据 DataFrame
        """
        return self.get_data('chain', data_subtype, entity_id=entity_id,
                           start_date=start_date, end_date=end_date)

    def get_e_commerce_data(self, data_subtype: str,
                           entity_id: Optional[str] = None,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> pd.DataFrame:
        """获取电商/互联网数据

        Args:
            data_subtype: 数据子类型
            entity_id: 实体ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            电商数据 DataFrame
        """
        return self.get_data('e_commerce', data_subtype, entity_id=entity_id,
                           start_date=start_date, end_date=end_date)
