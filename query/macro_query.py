"""
宏观数据查询模块

提供宏观数据查询、时间序列分析、与股票因子关联查询

时间语义（ADR-0012）：
    - **period_date**：统计期（月末/季末），查询输出的 trade_date 即统计期
    - **publish_date**：首次可得日；as_of 按该日期做 PIT 过滤
    - **publish_date_quality**：exact/estimated/unknown
    - 同一统计期存在多行（原始 trade_date 不同）时，取最新可得的一条
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Optional, List

import pandas as pd

from Factor_DB.utils.sql_utils import bind, bind_in

from .base import BaseQuery

logger = logging.getLogger(__name__)


class MacroQuery(BaseQuery):
    """宏观数据查询器

    Example:
        query = MacroQuery('market.db')

        # 获取单个指标数据（trade_date 为统计期）
        df = query.get_macro_data(['CPI'], start_date=date(2024, 1, 1))

        # PIT：只返回截至指定日期已发布的数据
        df = query.get_macro_data(['CPI'], as_of=date(2024, 6, 30))

        # 获取指标矩阵
        matrix = query.get_macro_matrix(['CPI', 'PPI', 'PMI'])

        # 获取宏观环境标签
        regime = query.identify_macro_regime(date(2024, 1, 1), date(2024, 12, 31))
    """

    # 统计期表达式：未迁移/未知指标的 period_date 为 NULL，回退到原始 trade_date
    _PERIOD_KEY = 'COALESCE(period_date, trade_date)'

    def _period_series_sql(self, params: dict[str, Any], *,
                           indicator_ids: Optional[List[str]] = None,
                           value_type: Optional[str] = None,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None,
                           as_of: Optional[date] = None) -> str:
        """构建按统计期去重后的宏观数据子查询（含 PIT 过滤）

        Args:
            params: 参数字典（原地写入）
            indicator_ids: 指标ID列表
            value_type: 值类型
            start_date: 统计期下界
            end_date: 统计期上界
            as_of: PIT 截止日期（按 publish_date，缺失时回退 loaded_at）

        Returns:
            可作为 FROM 子查询使用的 SQL
        """
        conditions = []
        if start_date is not None:
            conditions.append(
                f"{self._PERIOD_KEY} >= {bind(params, 'start_date', start_date)}"
            )
        if end_date is not None:
            conditions.append(
                f"{self._PERIOD_KEY} <= {bind(params, 'end_date', end_date)}"
            )
        if as_of is not None:
            as_of_param = bind(params, 'as_of', as_of)
            conditions.append(
                f"((publish_date IS NOT NULL AND publish_date <= {as_of_param}) "
                f"OR (publish_date IS NULL "
                f"AND CAST(loaded_at AS DATE) <= {as_of_param}))"
            )
        if indicator_ids:
            indicator_list = bind_in(params, 'indicator', indicator_ids)
            conditions.append(f"indicator_id IN ({indicator_list})")
        if value_type is not None:
            conditions.append(
                f"value_type = {bind(params, 'value_type', value_type)}"
            )

        where = ' AND '.join(conditions) if conditions else '1=1'

        return f'''
            SELECT
                {self._PERIOD_KEY} AS period_key,
                period_date,
                trade_date AS raw_trade_date,
                publish_date,
                publish_date_quality,
                loaded_at,
                indicator_id,
                indicator_name,
                value,
                value_type
            FROM macro_data
            WHERE {where}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY indicator_id, value_type, {self._PERIOD_KEY}
                ORDER BY publish_date DESC NULLS LAST,
                         loaded_at DESC,
                         trade_date DESC
            ) = 1
        '''

    def get_macro_data(self, indicator_ids: List[str],
                      start_date: Optional[date] = None,
                      end_date: Optional[date] = None,
                      value_type: str = 'raw',
                      as_of: Optional[date] = None) -> pd.DataFrame:
        """获取宏观数据

        Args:
            indicator_ids: 指标ID列表
            start_date: 统计期开始日期
            end_date: 统计期结束日期
            value_type: 值类型：raw/yoy/change/seasonal
            as_of: PIT 截止日期（仅返回该日期前已发布的数据）

        Returns:
            宏观数据 DataFrame（trade_date 为统计期，另含
            period_date/publish_date/publish_date_quality）
        """
        params: dict[str, Any] = {}
        series = self._period_series_sql(
            params,
            indicator_ids=indicator_ids,
            value_type=value_type,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        sql = f'''
            SELECT period_key AS trade_date,
                   period_date,
                   publish_date,
                   publish_date_quality,
                   indicator_id,
                   indicator_name,
                   value,
                   value_type
            FROM ({series}) m
            ORDER BY trade_date, indicator_id
        '''

        df = self._execute_query(sql, params)
        logger.debug(f"宏观数据查询完成: {len(df)} 条记录")
        return df

    def get_macro_matrix(self, indicator_ids: List[str],
                        start_date: Optional[date] = None,
                        end_date: Optional[date] = None,
                        value_type: str = 'raw',
                        as_of: Optional[date] = None) -> pd.DataFrame:
        """获取宏观数据矩阵（统计期 × 指标）

        Args:
            indicator_ids: 指标ID列表
            start_date: 统计期开始日期
            end_date: 统计期结束日期
            value_type: 值类型
            as_of: PIT 截止日期

        Returns:
            宏观数据矩阵 DataFrame
        """
        df = self.get_macro_data(
            indicator_ids, start_date, end_date, value_type, as_of
        )

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
        params: dict[str, Any] = {}
        if category:
            category_param = bind(params, 'category', category)
            sql = f"SELECT * FROM macro_indicators WHERE category = {category_param} ORDER BY indicator_id"
        else:
            sql = "SELECT * FROM macro_indicators ORDER BY category, indicator_id"

        return self._execute_query(sql, params)

    def get_indicator_stats(self, indicator_id: str,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None,
                           as_of: Optional[date] = None) -> dict:
        """获取指标统计信息（按统计期去重后计算）

        Args:
            indicator_id: 指标ID
            start_date: 统计期开始日期
            end_date: 统计期结束日期
            as_of: PIT 截止日期

        Returns:
            统计信息字典
        """
        params: dict[str, Any] = {}
        series = self._period_series_sql(
            params,
            indicator_ids=[indicator_id],
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        sql = f'''
            SELECT
                COUNT(*) as count,
                AVG(value) as mean,
                STDDEV(value) as std,
                MIN(value) as min,
                MAX(value) as max,
                MEDIAN(value) as median
            FROM ({series}) m
        '''

        df = self._execute_query(sql, params)
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

    def get_latest_values(self, indicator_ids: Optional[List[str]] = None,
                         as_of: Optional[date] = None) -> pd.DataFrame:
        """获取最新指标值（按统计期，而非原始采集日期）

        Args:
            indicator_ids: 指标ID列表（None则返回所有）
            as_of: PIT 截止日期

        Returns:
            最新值 DataFrame
        """
        params: dict[str, Any] = {}
        series = self._period_series_sql(
            params,
            indicator_ids=indicator_ids,
            as_of=as_of,
        )

        sql = f'''
            SELECT indicator_id, indicator_name, value,
                   period_key as latest_date
            FROM ({series}) m
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY indicator_id ORDER BY period_key DESC
            ) = 1
            ORDER BY indicator_id
        '''

        return self._execute_query(sql, params)

    def get_macro_by_date_range(self, start_date: date,
                               end_date: date,
                               categories: Optional[List[str]] = None,
                               as_of: Optional[date] = None) -> pd.DataFrame:
        """获取日期范围内的所有宏观数据（按统计期过滤）

        Args:
            start_date: 统计期开始日期
            end_date: 统计期结束日期
            categories: 指标类别过滤
            as_of: PIT 截止日期

        Returns:
            宏观数据 DataFrame
        """
        params: dict[str, Any] = {}
        series = self._period_series_sql(
            params,
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        category_filter = ""
        if categories:
            cat_list = bind_in(params, 'category', categories)
            category_filter = f"AND mi.category IN ({cat_list})"

        sql = f'''
            SELECT m.period_key as trade_date, m.indicator_id,
                   m.indicator_name, m.value, mi.category
            FROM ({series}) m
            LEFT JOIN macro_indicators mi ON m.indicator_id = mi.indicator_id
            WHERE 1=1 {category_filter}
            ORDER BY trade_date, m.indicator_id
        '''

        return self._execute_query(sql, params)

    def calculate_yoy(self, indicator_id: str,
                     periods: int = 12,
                     as_of: Optional[date] = None) -> pd.DataFrame:
        """计算同比数据（按统计期对齐）

        Args:
            indicator_id: 指标ID
            periods: 同比周期数（月度数据默认12）
            as_of: PIT 截止日期

        Returns:
            同比数据 DataFrame
        """
        params: dict[str, Any] = {}
        periods = int(periods)
        if periods <= 0:
            raise ValueError(f"periods 必须为正整数: {periods}")

        series = self._period_series_sql(
            params,
            indicator_ids=[indicator_id],
            value_type='raw',
            as_of=as_of,
        )

        sql = f'''
            WITH ordered AS (
                SELECT period_key as trade_date, value,
                       LAG(value, {periods}) OVER (ORDER BY period_key) as prev_value
                FROM ({series}) m
            )
            SELECT trade_date, value,
                   (value - prev_value) / prev_value * 100 as yoy
            FROM ordered
            WHERE prev_value IS NOT NULL
        '''

        return self._execute_query(sql, params)

    def identify_macro_regime(self, start_date: date,
                             end_date: date,
                             n_regimes: int = 4,
                             as_of: Optional[date] = None) -> pd.DataFrame:
        """识别宏观环境（基于简单阈值分类）

        Args:
            start_date: 统计期开始日期
            end_date: 统计期结束日期
            n_regimes: 环境分类数量
            as_of: PIT 截止日期

        Returns:
            每个日期（统计期）的宏观环境标签
        """
        params: dict[str, Any] = {}
        series = self._period_series_sql(
            params,
            indicator_ids=['PMI', 'CPI_yoy', 'M2_yoy'],
            start_date=start_date,
            end_date=end_date,
            as_of=as_of,
        )

        sql = f'''
            SELECT period_key as trade_date, indicator_id, value
            FROM ({series}) m
        '''

        df = self._execute_query(sql, params)
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
