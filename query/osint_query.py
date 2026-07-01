"""
OSINT 数据查询器

提供跨数据源的统一查询接口。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from core.connection import DuckDBConnection
from .base import BaseQuery

logger = logging.getLogger(__name__)


class OSINTQuery(BaseQuery):
    """OSINT 数据查询器

    Example:
        query = OSINTQuery('factor_db.duckdb')

        # 查询所有宏观数据
        df = query.get_macro_data(['GDP', 'CPI'], date(2020, 1, 1), date(2026, 1, 1))

        # 按数据源查询
        df = query.get_data_by_source('worldbank')

        # 跨数据源关联查询
        df = query.get_cross_source_data(['GDP', 'CPI'], stock_code='000001.SZ')
    """

    def get_data_by_source(self, source_id: str,
                           start_date: Optional[date] = None,
                           end_date: Optional[date] = None) -> pd.DataFrame:
        """按数据源查询数据

        Args:
            source_id: 数据源 ID
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame
        """
        conditions = ["source_id = ?"]
        params = [source_id]

        if start_date:
            conditions.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("trade_date <= ?")
            params.append(end_date)

        where_clause = " AND ".join(conditions)

        try:
            with self.conn as conn:
                df = conn.execute(f"""
                    SELECT *, 'macro_data' AS _source_table FROM macro_data
                    WHERE {where_clause}
                    UNION ALL
                    SELECT *, 'alternative_data' AS _source_table FROM alternative_data
                    WHERE {where_clause}
                    ORDER BY trade_date DESC
                """, params).fetchdf()
            return df
        except Exception as e:
            logger.error(f"按数据源查询失败: {e}")
            return pd.DataFrame()

    def get_latest_macro(self, indicator_ids: Optional[List[str]] = None,
                         limit: int = 100) -> pd.DataFrame:
        """获取最新宏观数据

        Args:
            indicator_ids: 指标 ID 列表
            limit: 返回记录数

        Returns:
            DataFrame
        """
        conditions = []
        params = []

        if indicator_ids:
            placeholders = ",".join(["?"] * len(indicator_ids))
            conditions.append(f"indicator_id IN ({placeholders})")
            params.extend(indicator_ids)

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        try:
            with self.conn as conn:
                df = conn.execute(f"""
                    SELECT m.*, mi.category, mi.frequency, mi.unit
                    FROM macro_data m
                    LEFT JOIN macro_indicators mi ON m.indicator_id = mi.indicator_id
                    WHERE {where_clause}
                    ORDER BY m.trade_date DESC
                    LIMIT ?
                """, params + [limit]).fetchdf()
            return df
        except Exception as e:
            logger.error(f"获取最新宏观数据失败: {e}")
            return pd.DataFrame()

    def get_cross_source_data(self, indicator_ids: List[str],
                              stock_code: Optional[str] = None) -> pd.DataFrame:
        """跨数据源关联查询

        将宏观数据与股票因子数据关联。

        Args:
            indicator_ids: 宏观指标 ID 列表
            stock_code: 股票代码（可选）

        Returns:
            DataFrame
        """
        try:
            with self.conn as conn:
                macro_df = conn.execute("""
                    SELECT trade_date, indicator_id, value
                    FROM macro_data
                    WHERE indicator_id IN ({})
                    AND value_type = 'raw'
                    ORDER BY trade_date
                """.format(",".join(["?"] * len(indicator_ids))), indicator_ids).fetchdf()

                if stock_code and not macro_df.empty:
                    factor_df = conn.execute("""
                        SELECT trade_date, factor_name, factor_value
                        FROM factor_data
                        WHERE stock_code = ?
                        ORDER BY trade_date
                    """, [stock_code]).fetchdf()

                    if not factor_df.empty:
                        macro_pivot = macro_df.pivot_table(
                            index='trade_date', columns='indicator_id', values='value'
                        ).reset_index()
                        factor_pivot = factor_df.pivot_table(
                            index='trade_date', columns='factor_name', values='factor_value'
                        ).reset_index()

                        return macro_pivot.merge(factor_pivot, on='trade_date', how='outer')

                return macro_df
        except Exception as e:
            logger.error(f"跨数据源关联查询失败: {e}")
            return pd.DataFrame()

    def get_source_summary(self) -> pd.DataFrame:
        """获取数据源摘要统计"""
        try:
            with self.conn as conn:
                return conn.execute("""
                    SELECT COALESCE(source_id, 'unknown') as source_id,
                           COUNT(*) as record_count,
                           MIN(trade_date) as earliest_date,
                           MAX(trade_date) as latest_date,
                           COUNT(DISTINCT indicator_id) as indicator_count
                    FROM macro_data
                    GROUP BY COALESCE(source_id, 'unknown')
                    ORDER BY record_count DESC
                """).fetchdf()
        except Exception as e:
            logger.error(f"获取数据源摘要失败: {e}")
            return pd.DataFrame()

    def get_data_freshness(self) -> pd.DataFrame:
        """获取数据新鲜度（各指标最后更新时间）"""
        try:
            with self.conn as conn:
                return conn.execute("""
                    SELECT indicator_id,
                           indicator_name,
                           MAX(trade_date) as latest_date,
                           MAX(update_time) as latest_update,
                           DATEDIFF('day', MAX(trade_date), CURRENT_DATE) as days_behind
                    FROM macro_data
                    GROUP BY indicator_id, indicator_name
                    ORDER BY days_behind DESC
                """).fetchdf()
        except Exception as e:
            logger.error(f"获取数据新鲜度失败: {e}")
            return pd.DataFrame()
