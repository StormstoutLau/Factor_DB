"""
因子数据查询模块

提供因子数据的查询、统计和分析接口。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import pandas as pd

from .base import BaseQuery, QueryFilter

logger = logging.getLogger(__name__)


class FactorQuery(BaseQuery):
    """因子数据查询器

    支持因子数据的查询、截面获取和矩阵转换。

    宽表架构（方案A）:
    - 最新版本查询 (as_of=None) 优先走 factor_wide（性能最优）
    - PIT 查询 (as_of!=None) 走 factor_history（完整版本链）
    - 宽表不存在时自动回退 factor_data（向后兼容）

    Example:
        query = FactorQuery('market.db')

        # 查询单个因子
        df = query.get_factor(
            factor_name='PE',
            stock_codes=['000001.SZ'],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31)
        )

        # 获取因子截面
        cross_section = query.get_cross_section(
            factor_name='PE',
            trade_date=date(2024, 6, 30)
        )

        # 获取因子矩阵
        matrix = query.get_factor_matrix(
            factor_names=['PE', 'PB', 'ROE'],
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31)
        )
    """

    def __init__(self, db_path: str = 'factor_db.duckdb', cache_size: int = 0):
        super().__init__(db_path, cache_size)
        self._wide_table = None
        self._history_table = None
        self._wide_columns = None
        self._detect_tables()

    def _detect_tables(self):
        """检测可用的表（宽表/历史表/长表）"""
        try:
            tables = self.conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchdf()['table_name'].tolist()

            self._wide_table = 'factor_wide' if 'factor_wide' in tables else None
            self._history_table = 'factor_history' if 'factor_history' in tables else None

            if self._wide_table:
                cols = self.conn.execute(f"DESCRIBE {self._wide_table}").fetchdf()
                skip = {'trade_date', 'stock_code', 'loaded_at'}
                self._wide_columns = [c for c in cols['column_name'].tolist() if c not in skip]
            else:
                self._wide_columns = []

            logger.debug(f"检测到宽表: {self._wide_table}, 历史表: {self._history_table}, 因子列: {self._wide_columns}")
        except Exception as e:
            logger.warning(f"检测宽表架构失败: {e}")
            self._wide_table = None
            self._history_table = None
            self._wide_columns = []

    def _factor_in_wide(self, factor_name: str) -> bool:
        """判断因子是否在宽表中"""
        return self._wide_table is not None and factor_name in self._wide_columns

    def _all_factors_in_wide(self, factor_names: list[str]) -> bool:
        """判断所有因子是否都在宽表中"""
        if not self._wide_table:
            return False
        return all(f in self._wide_columns for f in factor_names)

    def get_factor(
        self,
        factor_name: str,
        stock_codes: Optional[list[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        as_of: Optional[date] = None
    ) -> pd.DataFrame:
        """查询因子数据

        Args:
            factor_name: 因子名称
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            as_of: PIT 截止时间

        Returns:
            因子数据 DataFrame (trade_date, stock_code, factor_name, factor_value)
        """
        filter = QueryFilter(
            start_date=start_date,
            end_date=end_date,
            stock_codes=stock_codes,
            as_of=as_of
        )

        date_filter = self._build_date_filter(filter)
        stock_filter = self._build_stock_filter(filter)

        if as_of is None and self._factor_in_wide(factor_name):
            sql = f'''
                SELECT
                    trade_date,
                    stock_code,
                    '{factor_name}' AS factor_name,
                    "{factor_name}" AS factor_value
                FROM factor_wide
                WHERE {date_filter}
                  AND {stock_filter}
                  AND "{factor_name}" IS NOT NULL
                ORDER BY trade_date, stock_code
            '''
            df = self._execute_query(sql)
            logger.debug(f"宽表因子查询完成: {factor_name}, {len(df)} 条记录")
            return df

        if as_of is None and self._wide_table and not self._factor_in_wide(factor_name):
            logger.debug(f"因子 {factor_name} 不在宽表中，返回空")
            return pd.DataFrame(columns=['trade_date', 'stock_code', 'factor_name', 'factor_value'])

        table = self._history_table if (as_of is not None and self._history_table) else 'factor_data'

        pit_filter = self._build_pit_subquery(
            table, ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name = '{factor_name}'",
            as_of=filter.as_of
        )

        sql = f'''
            SELECT trade_date, stock_code, factor_name, factor_value
            FROM {table}
            WHERE factor_name = '{factor_name}'
              AND {date_filter}
              AND {stock_filter}
              AND {pit_filter}
            ORDER BY trade_date, stock_code
        '''

        df = self._execute_query(sql)
        logger.debug(f"因子查询完成: {factor_name}, {len(df)} 条记录")
        return df

    def get_cross_section(
        self,
        factor_name: str,
        trade_date: date,
        as_of: Optional[date] = None
    ) -> pd.DataFrame:
        """获取因子截面数据

        获取某一天的因子值，用于截面分析。

        Args:
            factor_name: 因子名称
            trade_date: 交易日期
            as_of: PIT 截止时间

        Returns:
            截面数据 DataFrame (stock_code, factor_value)
        """
        if as_of is None and self._factor_in_wide(factor_name):
            sql = f'''
                SELECT stock_code, "{factor_name}" AS factor_value
                FROM factor_wide
                WHERE trade_date = '{trade_date}'
                  AND "{factor_name}" IS NOT NULL
                ORDER BY factor_value DESC
            '''
            df = self._execute_query(sql)
            logger.debug(f"宽表截面查询完成: {factor_name} @ {trade_date}, {len(df)} 条记录")
            return df

        table = self._history_table if (as_of is not None and self._history_table) else 'factor_data'

        pit_filter = self._build_pit_subquery(
            table, ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name = '{factor_name}' AND trade_date = '{trade_date}'",
            as_of=as_of
        )

        sql = f'''
            SELECT stock_code, factor_value
            FROM {table}
            WHERE factor_name = '{factor_name}'
              AND trade_date = '{trade_date}'
              AND {pit_filter}
            ORDER BY factor_value DESC
        '''

        df = self._execute_query(sql)
        logger.debug(f"截面查询完成: {factor_name} @ {trade_date}, {len(df)} 条记录")
        return df

    def get_factor_matrix(
        self,
        factor_names: list[str],
        stock_codes: Optional[list[str]] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        as_of: Optional[date] = None
    ) -> pd.DataFrame:
        """获取因子矩阵

        返回宽格式 DataFrame，每个因子一列。

        Args:
            factor_names: 因子名称列表
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            as_of: PIT 截止时间

        Returns:
            因子数据 DataFrame (trade_date, stock_code, factor_1, factor_2, ...)
        """
        filter = QueryFilter(
            start_date=start_date,
            end_date=end_date,
            stock_codes=stock_codes,
            as_of=as_of
        )

        date_filter = self._build_date_filter(filter)
        stock_filter = self._build_stock_filter(filter)

        if as_of is None and self._all_factors_in_wide(factor_names):
            cols = ', '.join([f'"{f}"' for f in factor_names])
            sql = f'''
                SELECT trade_date, stock_code, {cols}
                FROM factor_wide
                WHERE {date_filter}
                  AND {stock_filter}
                ORDER BY trade_date, stock_code
            '''
            df = self._execute_query(sql)
            logger.debug(f"宽表因子矩阵: {df.shape}")
            return df

        table = self._history_table if (as_of is not None and self._history_table) else 'factor_data'

        factor_list = ', '.join([f"'{f}'" for f in factor_names])
        pit_filter = self._build_pit_subquery(
            table, ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name IN ({factor_list})",
            as_of=filter.as_of
        )

        sql = f'''
            SELECT trade_date, stock_code, factor_name, factor_value
            FROM {table}
            WHERE factor_name IN ({factor_list})
              AND {date_filter}
              AND {stock_filter}
              AND {pit_filter}
            ORDER BY trade_date, stock_code, factor_name
        '''

        df = self._execute_query(sql)

        if df.empty:
            return pd.DataFrame()

        matrix = df.pivot_table(
            index=['trade_date', 'stock_code'],
            columns='factor_name',
            values='factor_value'
        ).reset_index()

        logger.debug(f"因子矩阵: {matrix.shape}")
        return matrix

    def get_factor_stats(
        self,
        factor_name: str,
        trade_date: Optional[date] = None
    ) -> dict:
        """获取因子统计信息

        Args:
            factor_name: 因子名称
            trade_date: 统计日期 (None 则统计全部)

        Returns:
            统计信息字典
        """
        date_condition = f"AND trade_date = '{trade_date}'" if trade_date else ""
        pit_filter = self._build_pit_subquery(
            'factor_data', ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name = '{factor_name}' {date_condition}"
        )

        sql = f'''
            SELECT
                COUNT(*) as count,
                AVG(factor_value) as mean,
                STDDEV(factor_value) as std,
                MIN(factor_value) as min,
                MAX(factor_value) as max,
                MEDIAN(factor_value) as median
            FROM factor_data
            WHERE factor_name = '{factor_name}'
              AND {pit_filter}
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

    def get_ic_series(
        self,
        factor_name: str,
        forward_return_days: int = 1,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> pd.DataFrame:
        """计算 IC (Information Coefficient) 时间序列

        衡量因子预测能力。

        Args:
            factor_name: 因子名称
            forward_return_days: 前瞻收益率天数
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            IC 序列 DataFrame (trade_date, ic)
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)
        factor_pit_filter = self._build_pit_subquery(
            'factor_data', ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name = '{factor_name}' AND {date_filter}"
        )

        sql = f'''
            WITH factor_rank AS (
                SELECT
                    trade_date,
                    stock_code,
                    factor_value,
                    RANK() OVER (PARTITION BY trade_date ORDER BY factor_value) as factor_rank
                FROM factor_data
                WHERE factor_name = '{factor_name}' AND {date_filter} AND {factor_pit_filter}
            ),
            return_rank AS (
                SELECT
                    a.trade_date,
                    a.stock_code,
                    RANK() OVER (PARTITION BY a.trade_date ORDER BY (b.close - a.close) / a.close) as return_rank
                FROM daily_prices a
                JOIN daily_prices b
                    ON a.stock_code = b.stock_code
                    AND b.trade_date = (SELECT MIN(trade_date) FROM daily_prices WHERE trade_date > a.trade_date)
                WHERE {date_filter}
                  AND (a.trade_date, a.stock_code, a.loaded_at) IN (
                      SELECT trade_date, stock_code, MAX(loaded_at)
                      FROM daily_prices
                      WHERE {date_filter}
                      GROUP BY trade_date, stock_code
                  )
                  AND (b.trade_date, b.stock_code, b.loaded_at) IN (
                      SELECT trade_date, stock_code, MAX(loaded_at)
                      FROM daily_prices
                      GROUP BY trade_date, stock_code
                  )
            )
            SELECT
                f.trade_date,
                CORR(f.factor_rank, r.return_rank) as ic
            FROM factor_rank f
            JOIN return_rank r
                ON f.trade_date = r.trade_date
                AND f.stock_code = r.stock_code
            GROUP BY f.trade_date
            ORDER BY f.trade_date
        '''

        df = self._execute_query(sql)
        logger.debug(f"IC 计算完成: {factor_name}, {len(df)} 期")
        return df

    def list_factors(self) -> list[str]:
        """列出所有可用因子

        Returns:
            因子名称列表
        """
        if self._wide_table and self._wide_columns:
            return sorted(self._wide_columns)

        table = self._history_table if self._history_table else 'factor_data'
        sql = f'''
            SELECT DISTINCT factor_name
            FROM {table}
            ORDER BY factor_name
        '''

        df = self._execute_query(sql)
        return df['factor_name'].tolist()

    def get_factor_coverage(
        self,
        factor_name: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> pd.DataFrame:
        """获取因子覆盖率

        Args:
            factor_name: 因子名称
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            覆盖率 DataFrame (trade_date, coverage_ratio)
        """
        filter = QueryFilter(start_date=start_date, end_date=end_date)
        date_filter = self._build_date_filter(filter)
        factor_pit_filter = self._build_pit_subquery(
            'factor_data', ['trade_date', 'stock_code', 'factor_name'],
            extra_conditions=f"factor_name = '{factor_name}' AND {date_filter}"
        )
        price_pit_filter = self._build_pit_subquery(
            'daily_prices', ['trade_date', 'stock_code']
        )

        sql = f'''
            WITH total_stocks AS (
                SELECT trade_date, COUNT(DISTINCT stock_code) as total
                FROM daily_prices
                WHERE {date_filter} AND {price_pit_filter}
                GROUP BY trade_date
            ),
            factor_stocks AS (
                SELECT trade_date, COUNT(DISTINCT stock_code) as cnt
                FROM factor_data
                WHERE factor_name = '{factor_name}' AND {date_filter} AND {factor_pit_filter}
                GROUP BY trade_date
            )
            SELECT
                t.trade_date,
                f.cnt * 1.0 / t.total as coverage_ratio
            FROM total_stocks t
            LEFT JOIN factor_stocks f ON t.trade_date = f.trade_date
            ORDER BY t.trade_date
        '''

        df = self._execute_query(sql)
        return df
