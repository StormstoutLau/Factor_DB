"""
数据库表结构定义模块

职责：
    - 定义所有数据库表结构
    - 管理索引创建
    - 支持数据库版本迁移（预留）

未来扩展：
    如需支持宏观、舆情、另类数据，请使用 SCHEMA_EXTENSIONS 中的定义
    详见 docs/FUTURE_EXTENSION_PLAN.md 完整方案
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from Factor_DB.utils.sql_utils import (
    bind,
    bind_in,
    escape_literal,
    quote_identifier,
    validate_identifier,
)

from .connection import DuckDBConnection

logger = logging.getLogger(__name__)


class MigrationError(RuntimeError):
    """因子迁移失败（迁移中止，源表 factor_data 保持不变）"""


class SchemaManager:
    """数据库表结构管理器

    负责创建和维护数据库表结构、索引。

    Example:
        conn = DuckDBConnection('market.db')
        schema = SchemaManager(conn)
        schema.create_all_tables()
        schema.create_all_indexes()
    """

    # 表结构定义
    TABLES = {
        'level1_snapshots': '''
            CREATE TABLE IF NOT EXISTS level1_snapshots (
                trade_date DATE NOT NULL,
                trade_time TIME NOT NULL,
                stock_code VARCHAR NOT NULL,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                PRIMARY KEY (trade_date, trade_time, stock_code)
            )
        ''',
        'daily_prices': '''
            CREATE TABLE IF NOT EXISTS daily_prices (
                trade_date DATE NOT NULL,
                stock_code VARCHAR NOT NULL,
                loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                amount DOUBLE,
                adj_factor DOUBLE DEFAULT 1.0,
                turnover DOUBLE,
                PRIMARY KEY (trade_date, stock_code, loaded_at)
            )
        ''',
        'stock_info': '''
            CREATE TABLE IF NOT EXISTS stock_info (
                stock_code VARCHAR PRIMARY KEY,
                stock_name VARCHAR,
                list_date DATE,
                delist_date DATE,
                industry VARCHAR,
                industry_code VARCHAR,
                market VARCHAR,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'trade_calendar': '''
            CREATE TABLE IF NOT EXISTS trade_calendar (
                trade_date DATE PRIMARY KEY,
                is_trading_day BOOLEAN,
                week_day INTEGER,
                is_month_end BOOLEAN,
                is_quarter_end BOOLEAN,
                is_year_end BOOLEAN
            )
        ''',
        'factor_data': '''
            CREATE TABLE IF NOT EXISTS factor_data (
                trade_date DATE NOT NULL,
                stock_code VARCHAR NOT NULL,
                factor_name VARCHAR NOT NULL,
                factor_value DOUBLE,
                loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, stock_code, factor_name, loaded_at)
            )
        ''',
        'factor_info': '''
            CREATE TABLE IF NOT EXISTS factor_info (
                factor_name VARCHAR PRIMARY KEY,
                factor_type VARCHAR,
                description VARCHAR,
                category VARCHAR,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        # =====================
        # 扩展表：元数据管理
        # =====================
        'data_categories': '''
            CREATE TABLE IF NOT EXISTS data_categories (
                category_id VARCHAR PRIMARY KEY,
                category_name VARCHAR,
                description VARCHAR,
                priority INTEGER DEFAULT 0,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'data_sources': '''
            CREATE TABLE IF NOT EXISTS data_sources (
                source_id VARCHAR PRIMARY KEY,
                source_name VARCHAR,
                provider VARCHAR,
                update_frequency VARCHAR,
                contact_info VARCHAR,
                is_active BOOLEAN DEFAULT TRUE,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'data_dictionary': '''
            CREATE TABLE IF NOT EXISTS data_dictionary (
                field_id VARCHAR PRIMARY KEY,
                category_id VARCHAR,
                field_name VARCHAR,
                field_type VARCHAR,
                description VARCHAR,
                unit VARCHAR,
                source_id VARCHAR,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        # =====================
        # 扩展表：宏观经济数据
        # =====================
        'macro_data': '''
            CREATE TABLE IF NOT EXISTS macro_data (
                trade_date DATE NOT NULL,
                indicator_id VARCHAR NOT NULL,
                indicator_name VARCHAR,
                value DOUBLE,
                value_type VARCHAR DEFAULT 'raw',
                data_quality INTEGER DEFAULT 100,
                source_id VARCHAR,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (trade_date, indicator_id, value_type)
            )
        ''',
        'macro_indicators': '''
            CREATE TABLE IF NOT EXISTS macro_indicators (
                indicator_id VARCHAR PRIMARY KEY,
                indicator_name VARCHAR,
                category VARCHAR,
                frequency VARCHAR,
                unit VARCHAR,
                is_leading BOOLEAN DEFAULT FALSE,
                description VARCHAR,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        # =====================
        # 扩展表：舆情数据
        # =====================
        'news_sentiment': '''
            CREATE TABLE IF NOT EXISTS news_sentiment (
                news_id VARCHAR PRIMARY KEY,
                publish_date DATE NOT NULL,
                publish_time TIME,
                title VARCHAR,
                content TEXT,
                source_id VARCHAR,
                related_stocks VARCHAR[],
                related_industries VARCHAR[],
                sentiment_score DOUBLE,
                sentiment_label VARCHAR,
                keyword_count INTEGER,
                read_count INTEGER,
                comment_count INTEGER,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        'report_sentiment': '''
            CREATE TABLE IF NOT EXISTS report_sentiment (
                report_id VARCHAR PRIMARY KEY,
                publish_date DATE NOT NULL,
                title VARCHAR,
                analyst VARCHAR,
                brokerage VARCHAR,
                related_stock VARCHAR,
                rating_change VARCHAR,
                target_price DOUBLE,
                current_price DOUBLE,
                sentiment_score DOUBLE,
                content_summary TEXT,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''',
        # =====================
        # 扩展表：另类数据
        # =====================
        'alternative_data': '''
            CREATE TABLE IF NOT EXISTS alternative_data (
                data_id VARCHAR PRIMARY KEY,
                trade_date DATE NOT NULL,
                data_type VARCHAR NOT NULL,
                data_subtype VARCHAR,
                entity_type VARCHAR,
                entity_id VARCHAR,
                entity_name VARCHAR,
                value DOUBLE,
                value_text TEXT,
                value_array DOUBLE[],
                data_quality INTEGER DEFAULT 100,
                source_id VARCHAR,
                metadata JSON,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (trade_date, data_type, data_subtype, entity_type, entity_id)
            )
        ''',
        'alternative_types': '''
            CREATE TABLE IF NOT EXISTS alternative_types (
                data_type VARCHAR PRIMARY KEY,
                type_name VARCHAR,
                description VARCHAR,
                default_entity_type VARCHAR,
                is_time_series BOOLEAN DEFAULT TRUE,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        '''
    }

    # 索引定义
    INDEXES = {
        'idx_level1_date_code': 
            'CREATE INDEX IF NOT EXISTS idx_level1_date_code ON level1_snapshots(trade_date, stock_code)',
        'idx_level1_time': 
            'CREATE INDEX IF NOT EXISTS idx_level1_time ON level1_snapshots(trade_time)',
        'idx_level1_code_time': 
            'CREATE INDEX IF NOT EXISTS idx_level1_code_time ON level1_snapshots(stock_code, trade_time)',
        'idx_daily_date': 
            'CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(trade_date)',
        'idx_daily_code': 
            'CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_prices(stock_code)',
        'idx_daily_date_code': 
            'CREATE INDEX IF NOT EXISTS idx_daily_date_code ON daily_prices(trade_date, stock_code)',
        'idx_daily_pit': 
            'CREATE INDEX IF NOT EXISTS idx_daily_pit ON daily_prices(trade_date, stock_code, loaded_at DESC)',
        'idx_factor_name_date': 
            'CREATE INDEX IF NOT EXISTS idx_factor_name_date ON factor_data(factor_name, trade_date)',
        'idx_factor_code_date': 
            'CREATE INDEX IF NOT EXISTS idx_factor_code_date ON factor_data(stock_code, trade_date)',
        'idx_factor_pit': 
            'CREATE INDEX IF NOT EXISTS idx_factor_pit ON factor_data(trade_date, stock_code, factor_name, loaded_at DESC)',
        # 宏观数据索引
        'idx_macro_date': 
            'CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_data(trade_date)',
        'idx_macro_indicator': 
            'CREATE INDEX IF NOT EXISTS idx_macro_indicator ON macro_data(indicator_id)',
        'idx_macro_date_indicator': 
            'CREATE INDEX IF NOT EXISTS idx_macro_date_indicator ON macro_data(trade_date, indicator_id)',
        'idx_macro_indicator_category': 
            'CREATE INDEX IF NOT EXISTS idx_macro_indicator_category ON macro_indicators(category)',
        # 舆情数据索引
        'idx_news_date': 
            'CREATE INDEX IF NOT EXISTS idx_news_date ON news_sentiment(publish_date)',
        'idx_news_sentiment': 
            'CREATE INDEX IF NOT EXISTS idx_news_sentiment ON news_sentiment(sentiment_label, publish_date)',
        # Note: DuckDB does not support indexing on array columns like related_stocks
        'idx_report_date': 
            'CREATE INDEX IF NOT EXISTS idx_report_date ON report_sentiment(publish_date)',
        'idx_report_stock': 
            'CREATE INDEX IF NOT EXISTS idx_report_stock ON report_sentiment(related_stock)',
        # 另类数据索引
        'idx_alt_date': 
            'CREATE INDEX IF NOT EXISTS idx_alt_date ON alternative_data(trade_date)',
        'idx_alt_type': 
            'CREATE INDEX IF NOT EXISTS idx_alt_type ON alternative_data(data_type, data_subtype)',
        'idx_alt_entity': 
            'CREATE INDEX IF NOT EXISTS idx_alt_entity ON alternative_data(entity_type, entity_id)',
        'idx_alt_full': 
            'CREATE INDEX IF NOT EXISTS idx_alt_full ON alternative_data(trade_date, data_type, entity_type, entity_id)'
    }

    def __init__(self, connection: DuckDBConnection):
        """初始化 Schema 管理器

        Args:
            connection: DuckDB 连接实例
        """
        self.conn = connection

    def create_table(self, table_name: str) -> bool:
        """创建单个表

        Args:
            table_name: 表名

        Returns:
            是否创建成功
        """
        if table_name not in self.TABLES:
            logger.error(f"未知表名: {table_name}")
            return False

        try:
            with self.conn as conn:
                conn.execute(self.TABLES[table_name])
            logger.info(f"表创建成功: {table_name}")
            return True
        except Exception as e:
            logger.error(f"表创建失败 {table_name}: {e}")
            return False

    def create_all_tables(self) -> dict[str, bool]:
        """创建所有表

        Returns:
            各表创建结果的字典
        """
        results = {}
        for table_name in self.TABLES:
            results[table_name] = self.create_table(table_name)
        return results

    def create_index(self, index_name: str) -> bool:
        """创建单个索引

        Args:
            index_name: 索引名

        Returns:
            是否创建成功
        """
        if index_name not in self.INDEXES:
            logger.error(f"未知索引名: {index_name}")
            return False

        try:
            with self.conn as conn:
                conn.execute(self.INDEXES[index_name])
            logger.info(f"索引创建成功: {index_name}")
            return True
        except Exception as e:
            logger.error(f"索引创建失败 {index_name}: {e}")
            return False

    def drop_index(self, index_name: str) -> bool:
        """删除单个索引

        Args:
            index_name: 索引名

        Returns:
            是否删除成功
        """
        validate_identifier(index_name, '索引名')
        try:
            with self.conn as conn:
                conn.execute(f"DROP INDEX IF EXISTS {quote_identifier(index_name)}")
            logger.info(f"索引删除成功: {index_name}")
            return True
        except Exception as e:
            logger.error(f"索引删除失败 {index_name}: {e}")
            return False

    def index_exists(self, index_name: str) -> bool:
        """检查索引是否存在

        Args:
            index_name: 索引名

        Returns:
            是否存在
        """
        try:
            with self.conn as conn:
                result = conn.execute(
                    "SELECT COUNT(*) FROM duckdb_indexes() WHERE index_name = ?",
                    [index_name]
                ).fetchone()
                return result[0] > 0
        except Exception:
            return False

    # factor_data 二级索引列表（不含主键）
    FACTOR_INDEXES = [
        'idx_factor_name_date',
        'idx_factor_code_date',
        'idx_factor_pit',
    ]

    # factor_data 安全索引（低内存占用，不会 OOM）
    FACTOR_SAFE_INDEXES = [
        'idx_factor_name_date',
    ]

    def drop_factor_indexes(self) -> bool:
        """删除 factor_data 表的所有二级索引（保留主键）

        在大批量导入前删除索引可显著提升写入性能并降低内存消耗。

        Returns:
            是否全部删除成功
        """
        logger.info("删除 factor_data 二级索引...")
        all_ok = True
        for idx_name in self.FACTOR_INDEXES:
            if not self.drop_index(idx_name):
                all_ok = False
        return all_ok

    def recreate_factor_indexes(self, safe_only: bool = False) -> bool:
        """重建 factor_data 表的所有二级索引

        在大批量导入完成后重建索引以恢复查询性能。

        Args:
            safe_only: 如果为 True，仅创建低内存占用的安全索引
                       （跳过 idx_factor_code_date 和 idx_factor_pit，
                       这两个索引在 871M+ 行上需要 >25GB 内存，会 OOM）

        Returns:
            是否全部重建成功
        """
        indexes = self.FACTOR_SAFE_INDEXES if safe_only else self.FACTOR_INDEXES
        mode = "safe_only" if safe_only else "all"
        logger.info(f"重建 factor_data 二级索引 ({mode})...")
        all_ok = True
        for idx_name in indexes:
            if not self.create_index(idx_name):
                all_ok = False
        return all_ok

    def create_all_indexes(self, skip_oom: bool = False) -> dict[str, bool]:
        """创建所有索引

        Args:
            skip_oom: 如果为 True，跳过 idx_factor_code_date 和 idx_factor_pit
                      （这两个索引在 871M+ 行上需要 >25GB 内存，会 OOM）

        Returns:
            各索引创建结果的字典
        """
        OOM_INDEXES = {'idx_factor_code_date', 'idx_factor_pit'}
        results = {}
        for index_name in self.INDEXES:
            if skip_oom and index_name in OOM_INDEXES:
                continue
            results[index_name] = self.create_index(index_name)
        return results

    def drop_table(self, table_name: str) -> bool:
        """删除表

        Args:
            table_name: 表名

        Returns:
            是否删除成功
        """
        validate_identifier(table_name, '表名')
        try:
            with self.conn as conn:
                conn.execute(f"DROP TABLE IF EXISTS {quote_identifier(table_name)}")
            logger.info(f"表删除成功: {table_name}")
            return True
        except Exception as e:
            logger.error(f"表删除失败 {table_name}: {e}")
            return False

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在

        Args:
            table_name: 表名

        Returns:
            是否存在
        """
        try:
            with self.conn as conn:
                result = conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                    [table_name]
                ).fetchone()
                return result[0] > 0
        except Exception:
            return False

    def get_table_stats(self, table_name: str) -> Optional[dict]:
        """获取表统计信息

        Args:
            table_name: 表名

        Returns:
            统计信息字典
        """
        if not self.table_exists(table_name):
            return None

        validate_identifier(table_name, '表名')

        try:
            with self.conn as conn:
                # 行数
                row_count = conn.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(table_name)}"
                ).fetchone()[0]

                # 列信息
                columns = conn.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = ?",
                    [table_name],
                ).fetchdf()

                return {
                    'table_name': table_name,
                    'row_count': row_count,
                    'columns': columns.to_dict('records')
                }
        except Exception as e:
            logger.error(f"获取表统计失败 {table_name}: {e}")
            return None

    def init_database(self, safe_only: bool = False) -> bool:
        """初始化完整数据库

        创建所有表和索引。

        Args:
            safe_only: 如果为 True，跳过 idx_factor_code_date 和 idx_factor_pit
                       （这两个索引在 871M+ 行上需要 >25GB 内存，会 OOM）

        Returns:
            是否初始化成功
        """
        logger.info("开始初始化数据库...")

        # 创建表
        table_results = self.create_all_tables()
        if not all(table_results.values()):
            failed = [k for k, v in table_results.items() if not v]
            logger.error(f"部分表创建失败: {failed}")
            return False

        # 创建索引
        index_results = self.create_all_indexes(skip_oom=safe_only)
        if not all(index_results.values()):
            failed = [k for k, v in index_results.items() if not v]
            logger.warning(f"部分索引创建失败: {failed}")

        logger.info("数据库初始化完成")
        return True

    def compact_table(self, table_name: str, keep_before: str) -> int:
        """清理旧版本数据

        删除 loaded_at < keep_before 的非最新版本数据，
        保留每个主键在 cutoff 日期之前的最新版本。

        Args:
            table_name: 表名 (daily_prices / factor_data)
            keep_before: 截止日期字符串 'YYYY-MM-DD'

        Returns:
            删除的行数
        """
        if table_name not in ('daily_prices', 'factor_data'):
            logger.error(f"compact_table 不支持表: {table_name}")
            return 0

        pk_cols = {
            'daily_prices': ['trade_date', 'stock_code'],
            'factor_data': ['trade_date', 'stock_code', 'factor_name'],
        }

        pk_list = ', '.join(pk_cols[table_name])
        table_ident = quote_identifier(table_name)

        predicate = f'''
            ({pk_list}, loaded_at) NOT IN (
                SELECT {pk_list}, MAX(loaded_at)
                FROM {table_ident}
                WHERE loaded_at < $keep_before
                GROUP BY {pk_list}
            )
            AND loaded_at < $keep_before
        '''

        try:
            with self.conn as conn:
                # 先统计再删除：避免 DELETE ... RETURNING 在百万行级别物化结果集
                deleted = conn.execute(
                    f"SELECT COUNT(*) FROM {table_ident} WHERE {predicate}",
                    {'keep_before': keep_before},
                ).fetchone()[0]
                conn.execute(
                    f"DELETE FROM {table_ident} WHERE {predicate}",
                    {'keep_before': keep_before},
                )
            logger.info(
                f"表 {table_name} 历史数据清理完成 (cutoff: {keep_before}, 删除 {deleted} 行)"
            )
            return int(deleted)
        except Exception as e:
            logger.error(f"表 {table_name} 清理失败: {e}")
            return 0

    # =============================
    # 宽表架构 (方案A: factor_wide + factor_history)
    # =============================

    WIDE_META_COLUMNS = {'trade_date', 'stock_code', 'loaded_at'}

    def create_factor_wide(self, factor_names: list[str]) -> bool:
        """创建因子宽表 factor_wide

        宽表以 (trade_date, stock_code) 为主键，每个因子一列。
        相比长表(EAV)，行数减少 N 倍（N=因子数），
        PK ART 索引内存从 ~45GB 降至 ~600MB（32GB 机器可行）。

        Args:
            factor_names: 因子列名列表

        Returns:
            是否创建成功
        """
        parts = [
            'trade_date DATE NOT NULL',
            'stock_code VARCHAR NOT NULL',
            'loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP',
        ]
        for name in factor_names:
            # DDL 不支持参数绑定：先白名单校验，再双引号转义
            parts.append(f'{quote_identifier(validate_identifier(name, "因子名"))} DOUBLE')
        parts.append('PRIMARY KEY (trade_date, stock_code)')

        cols_sql = ',\n    '.join(parts)

        sql = f'''
            CREATE TABLE IF NOT EXISTS factor_wide (
                {cols_sql}
            )
        '''

        try:
            with self.conn as conn:
                conn.execute(sql)
            logger.info(f"宽表 factor_wide 创建成功 ({len(factor_names)} 个因子列)")
            return True
        except Exception as e:
            logger.error(f"宽表 factor_wide 创建失败: {e}")
            return False

    def create_factor_history(self) -> bool:
        """创建因子历史长表 factor_history

        结构与原 factor_data 相同，但无主键（避免大表 ART 索引 OOM）。
        用于 PIT 回测、版本对比、审计等场景。

        Returns:
            是否创建成功
        """
        sql = '''
            CREATE TABLE IF NOT EXISTS factor_history (
                trade_date DATE NOT NULL,
                stock_code VARCHAR NOT NULL,
                factor_name VARCHAR NOT NULL,
                factor_value DOUBLE,
                loaded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        '''

        try:
            with self.conn as conn:
                conn.execute(sql)
            logger.info("长表 factor_history 创建成功")
            return True
        except Exception as e:
            logger.error(f"长表 factor_history 创建失败: {e}")
            return False

    def add_factor_column(self, table_name: str, factor_name: str) -> bool:
        """向宽表动态添加因子列

        DuckDB 的 ALTER TABLE ADD COLUMN 是元数据操作，秒级完成，
        即使表已有大量数据也不会重写。

        Args:
            table_name: 宽表名 (通常是 factor_wide)
            factor_name: 因子名

        Returns:
            是否添加成功
        """
        validate_identifier(table_name, '表名')
        factor_col = quote_identifier(validate_identifier(factor_name, '因子名'))
        try:
            with self.conn as conn:
                conn.execute(
                    f'ALTER TABLE {quote_identifier(table_name)} '
                    f'ADD COLUMN IF NOT EXISTS {factor_col} DOUBLE'
                )
            logger.info(f"宽表 {table_name} 添加因子列: {factor_name}")
            return True
        except Exception as e:
            logger.error(f"宽表 {table_name} 添加因子列 {factor_name} 失败: {e}")
            return False

    def get_factor_columns(self, table_name: str) -> list[str]:
        """获取宽表中的因子列列表（排除元数据列）

        Args:
            table_name: 宽表名

        Returns:
            因子列名列表
        """
        validate_identifier(table_name, '表名')
        try:
            with self.conn as conn:
                rows = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [table_name],
                ).fetchall()
            return [r[0] for r in rows if r[0] not in self.WIDE_META_COLUMNS]
        except Exception as e:
            logger.error(f"获取宽表 {table_name} 因子列失败: {e}")
            return []

    def pivot_long_to_wide(
        self,
        long_table: str,
        wide_table: str,
        factor_names: list[str],
        use_latest: bool = True,
    ) -> int:
        """长表 PIVOT 到宽表

        将 EAV 长表中的因子值 pivot 为宽表的列。
        默认取每个 (trade_date, stock_code, factor_name) 的最新 loaded_at 版本。

        Args:
            long_table: 源长表名
            wide_table: 目标宽表名
            factor_names: 要 pivot 的因子列表
            use_latest: 是否只取最新版本（默认 True）

        Returns:
            写入宽表的行数
        """
        validate_identifier(long_table, '源表名')
        validate_identifier(wide_table, '目标表名')
        for f in factor_names:
            validate_identifier(f, '因子名')

        params: dict[str, Any] = {}
        # 因子名作为值绑定到 SQL（参数名与 IN 列表复用）
        case_exprs = ',\n        '.join(
            f'MAX(CASE WHEN factor_name = {bind(params, f"factor_name_{i}", f)} '
            f'THEN factor_value END) AS {quote_identifier(f)}'
            for i, f in enumerate(factor_names)
        )
        factor_in_list = bind_in(params, 'factor_name', factor_names)
        long_ident = quote_identifier(long_table)
        wide_ident = quote_identifier(wide_table)

        if use_latest:
            source_sql = f'''
                SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY trade_date, stock_code, factor_name
                            ORDER BY loaded_at DESC
                        ) AS rn
                    FROM {long_ident}
                    WHERE factor_name IN ({factor_in_list})
                )
                WHERE rn = 1
            '''
        else:
            source_sql = f'''
                SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
                FROM {long_ident}
                WHERE factor_name IN ({factor_in_list})
            '''

        sql = f'''
            INSERT INTO {wide_ident}
            SELECT
                trade_date,
                stock_code,
                MAX(loaded_at) AS loaded_at,
                {case_exprs}
            FROM (
                {source_sql}
            )
            GROUP BY trade_date, stock_code
        '''

        try:
            with self.conn as conn:
                conn.execute(sql, params)
                row_count = conn.execute(
                    f"SELECT COUNT(*) FROM {wide_ident}"
                ).fetchone()[0]
            logger.info(f"PIVOT {long_table} → {wide_table}: {row_count} 行, {len(factor_names)} 个因子")
            return row_count
        except Exception as e:
            logger.error(f"PIVOT 失败 {long_table} → {wide_table}: {e}")
            return 0

    def unpivot_wide_to_long(
        self,
        wide_table: str,
        long_table: str,
        factor_names: list[str],
        skip_nulls: bool = True,
    ) -> int:
        """宽表 UNPIVOT 到长表

        将宽表的因子列展开为 EAV 长表的行。
        默认跳过 NULL 值（减少存储空间）。

        Args:
            wide_table: 源宽表名
            long_table: 目标长表名
            factor_names: 要 unpivot 的因子列表
            skip_nulls: 是否跳过 NULL 值（默认 True）

        Returns:
            写入长表的行数
        """
        validate_identifier(wide_table, '源表名')
        validate_identifier(long_table, '目标表名')
        for f in factor_names:
            validate_identifier(f, '因子名')

        # UNPIVOT 列清单属于标识符位置，DDL/DML 均不支持参数绑定
        factor_list = ', '.join(quote_identifier(f) for f in factor_names)
        wide_ident = quote_identifier(wide_table)
        long_ident = quote_identifier(long_table)

        if skip_nulls:
            where_clause = "WHERE factor_value IS NOT NULL"
        else:
            where_clause = ""

        sql = f'''
            INSERT INTO {long_ident}
            SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
            FROM {wide_ident}
            UNPIVOT (
                factor_value FOR factor_name IN ({factor_list})
            )
            {where_clause}
        '''

        try:
            with self.conn as conn:
                before = conn.execute(
                    f"SELECT COUNT(*) FROM {long_ident}"
                ).fetchone()[0]
                conn.execute(sql)
                after = conn.execute(
                    f"SELECT COUNT(*) FROM {long_ident}"
                ).fetchone()[0]
                row_count = after - before
            logger.info(f"UNPIVOT {wide_table} → {long_table}: {row_count} 行, {len(factor_names)} 个因子")
            return row_count
        except Exception as e:
            logger.error(f"UNPIVOT 失败 {wide_table} → {long_table}: {e}")
            return 0

    def _configure_migration(self, conn) -> None:
        """设置迁移专用资源参数（内存安全）

        依据：docs/design/wide_migration_design.md §2.3
        - memory_limit=24GB（75% 物理内存，留 8GB 给 OS）
        - threads=4（减少并发内存碎片化）
        - preserve_insertion_order=false（官方缓解 OOM 建议）

        Args:
            conn: 底层 duckdb 连接（由 with self.conn as conn 提供）
        """
        try:
            conn.execute("SET memory_limit='24GB'")
            conn.execute("SET threads=4")
            conn.execute("SET preserve_insertion_order=false")
        except Exception as e:
            logger.error(f"迁移资源参数设置失败，中止迁移: {e}")
            raise MigrationError(f"迁移资源配置失败: {e}") from e

    def migrate_factor_data_to_wide(self, batch_size: int = 5) -> dict:
        """将 factor_data 长表迁移到双轨存储（factor_wide + factor_history）

        注意：本方法逐因子 upsert，在 241 列宽表 / 16.8 亿行规模下实测不可用
        （预计 130+ 小时）。大规模迁移请使用
        ``rebuild_factor_data_dual_track()``。

        安全语义（见 docs/design/migration_safety_design.md）：
        - 任一因子失败 → 中止，源表 factor_data 保持不变（不 RENAME、不 DROP）
        - 每因子宽表+历史表在同一事务内提交，并做行数对账
        - 全部成功后，原表 RENAME 为 factor_data_backup_<ts>，再创建兼容视图
        - 已迁移（factor_data 为视图）时直接短路，保证重跑幂等

        Args:
            batch_size: 每批处理的因子数量（默认 5）

        Returns:
            迁移统计 {factor_name: 行数}；已迁移时返回空 dict

        Raises:
            MigrationError: 任一因子迁移失败或视图替换失败（源表不会被删除）
        """
        stats: dict[str, int] = {}

        # Step 0: 幂等短路——factor_data 已是视图说明迁移已完成
        if self._is_view('factor_data'):
            logger.info("factor_data 已是视图，迁移已完成，跳过")
            return stats

        logger.info("开始因子数据迁移...")

        # Step 1: 获取所有因子列表
        with self.conn as conn:
            factors = conn.execute('''
                SELECT DISTINCT factor_name FROM factor_data
                ORDER BY factor_name
            ''').fetchall()
            all_factors = [f[0] for f in factors]
        logger.info(f"共 {len(all_factors)} 个因子待迁移")

        # Step 2: 创建宽表（预定义列，避免动态 ALTER）
        logger.info("创建 factor_wide 宽表...")
        self.create_factor_wide(all_factors)

        # 迁移前可能已通过双写创建了部分宽表：补齐缺失因子列
        existing_cols = set(self.get_factor_columns('factor_wide'))
        missing_cols = [f for f in all_factors if f not in existing_cols]
        if missing_cols:
            logger.info(f"补齐 factor_wide 缺失列: {len(missing_cols)} 个")
            for name in missing_cols:
                if not self.add_factor_column('factor_wide', name):
                    raise MigrationError(f"补齐 factor_wide 列失败: {name}")

        # Step 3: 创建历史表（如果不存在）
        if not self.table_exists('factor_history'):
            self.create_factor_history()
            logger.info("创建 factor_history 历史表完成")

        # Step 4: 分因子批量迁移（fail-fast：任一失败立即中止）
        failures: dict[str, str] = {}
        for i in range(0, len(all_factors), batch_size):
            batch = all_factors[i:i + batch_size]
            logger.info(f"迁移批次 {i // batch_size + 1}: {batch}")

            for factor in batch:
                try:
                    stats[factor] = self._migrate_single_factor(factor)
                except MigrationError as e:
                    failures[factor] = str(e)
                    logger.error(f"迁移中止：因子 {factor} 失败: {e}")
                    break
            if failures:
                break

        if failures:
            raise MigrationError(
                f"迁移中止：{len(failures)} 个因子失败，源表 factor_data 未改动。"
                f"失败明细: {failures}"
            )

        # Step 5: 仅在全部成功后替换为视图（RENAME 备份 + 单事务）
        self._replace_factor_data_with_view()

        wide_count = self._get_row_count('factor_wide')
        hist_count = self._get_row_count('factor_history')
        logger.info(f"迁移完成: factor_wide={wide_count:,}, factor_history={hist_count:,}")
        return stats

    def _migrate_single_factor(self, factor_name: str) -> int:
        """迁移单个因子到宽表和历史表（事务化 + 行数对账）

        安全语义：
        - 宽表 upsert 与历史表插入在同一事务内，任一失败整体回滚
        - 事务内校验：宽表非空值数 == 源 distinct 键数；历史行数 == 源非空行数
        - 历史表已有该因子完整数据时只补宽表，不重复写历史（重试幂等）
        - 失败抛 MigrationError（不再静默返回 0）

        Args:
            factor_name: 因子名

        Returns:
            宽表该因子的非空行数；全空因子返回 0

        Raises:
            MigrationError: 迁移失败或行数对账不一致
        """
        factor_col = quote_identifier(validate_identifier(factor_name, '因子名'))
        params = {'factor_name': factor_name}

        try:
            with self.conn as conn:
                self._configure_migration(conn)

                src_rows = conn.execute(
                    "SELECT COUNT(*) FROM factor_data "
                    "WHERE factor_name = $factor_name AND factor_value IS NOT NULL",
                    params,
                ).fetchone()[0]
                if src_rows == 0:
                    logger.info(f"  因子 {factor_name}: 无非空数据，跳过")
                    return 0

                src_keys = conn.execute('''
                    SELECT COUNT(*) FROM (
                        SELECT DISTINCT trade_date, stock_code
                        FROM factor_data
                        WHERE factor_name = $factor_name AND factor_value IS NOT NULL
                    ) t
                ''', params).fetchone()[0]

                hist_existing = conn.execute(
                    "SELECT COUNT(*) FROM factor_history WHERE factor_name = $factor_name",
                    params,
                ).fetchone()[0]

                # 宽表 Upsert：arg_max 取 loaded_at 最新版本（内存安全，见设计文档 §2.2）
                wide_sql = f'''
                    INSERT INTO factor_wide (trade_date, stock_code, loaded_at, {factor_col})
                    SELECT
                        trade_date,
                        stock_code,
                        COALESCE(max(loaded_at), CURRENT_TIMESTAMP) AS loaded_at,
                        arg_max(factor_value, COALESCE(loaded_at, CURRENT_TIMESTAMP)) AS factor_value
                    FROM factor_data
                    WHERE factor_name = $factor_name
                      AND factor_value IS NOT NULL
                    GROUP BY trade_date, stock_code
                    ON CONFLICT (trade_date, stock_code)
                    DO UPDATE SET
                        {factor_col} = EXCLUDED.{factor_col},
                        loaded_at = EXCLUDED.loaded_at
                '''

                # 重试/直接重入：历史已存在 → 只补宽表，不重复追加历史
                if hist_existing > 0:
                    if hist_existing < src_rows:
                        raise MigrationError(
                            f"因子 {factor_name} 历史表数据不完整"
                            f"（{hist_existing} < 源 {src_rows}），需人工介入"
                        )
                    conn.execute(wide_sql, params)
                    wide_count = conn.execute(
                        f"SELECT COUNT(*) FROM factor_wide WHERE {factor_col} IS NOT NULL"
                    ).fetchone()[0]
                    if wide_count < src_keys:
                        raise MigrationError(
                            f"因子 {factor_name} 宽表行数不足"
                            f"（{wide_count} < 源键数 {src_keys}）"
                        )
                    logger.info(
                        f"  因子 {factor_name}: 历史已存在（{hist_existing} 行），"
                        f"宽表校验 {wide_count}/{src_keys}"
                    )
                    return wide_count

                # 历史表：保留所有版本
                hist_sql = f'''
                    INSERT INTO factor_history (trade_date, stock_code, factor_name, factor_value, loaded_at)
                    SELECT
                        trade_date,
                        stock_code,
                        factor_name,
                        factor_value,
                        COALESCE(loaded_at, CURRENT_TIMESTAMP)
                    FROM factor_data
                    WHERE factor_name = $factor_name
                      AND factor_value IS NOT NULL
                '''

                # 单因子事务：wide + history 原子提交，失败整体回滚
                conn.execute("BEGIN TRANSACTION")
                try:
                    conn.execute(wide_sql, params)
                    wide_count = conn.execute(
                        f"SELECT COUNT(*) FROM factor_wide WHERE {factor_col} IS NOT NULL"
                    ).fetchone()[0]
                    if wide_count != src_keys:
                        raise MigrationError(
                            f"因子 {factor_name} 宽表对账失败"
                            f"（宽表 {wide_count} != 源键数 {src_keys}）"
                        )

                    conn.execute(hist_sql, params)
                    hist_after = conn.execute(
                        "SELECT COUNT(*) FROM factor_history WHERE factor_name = $factor_name",
                        params,
                    ).fetchone()[0]
                    if hist_after != src_rows:
                        raise MigrationError(
                            f"因子 {factor_name} 历史表对账失败"
                            f"（历史 {hist_after} != 源 {src_rows}）"
                        )

                    conn.execute("COMMIT")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    raise

            logger.info(f"  因子 {factor_name}: {wide_count:,} 键 / {src_rows:,} 行 → wide + history")
            return wide_count

        except MigrationError:
            raise
        except Exception as e:
            logger.error(f"迁移因子 {factor_name} 失败: {e}")
            raise MigrationError(f"迁移因子 {factor_name} 失败: {e}") from e

    def rebuild_factor_data_dual_track(
        self,
        chunk_size: int = 24,
        sample_size: int = 6,
        temp_directory: Optional[str] = None,
    ) -> dict[str, int]:
        """两阶段重建双轨存储（适用于超大 factor_data）

        与逐因子 upsert 的 ``migrate_factor_data_to_wide`` 相比：
        - Phase A：单次顺序扫描 factor_data → factor_history
        - Phase B：按因子分块 pivot（arg_max + CASE）→ 逐轮 FULL JOIN 合并
        - Phase C：创建 factor_wide + 主键、抽样对账、替换为视图

        安全语义：
        - factor_data 已是视图时幂等短路，返回空 dict
        - factor_data 为源表时，仅在全部成功后才 RENAME；失败源表不变
        - factor_wide / factor_history 为派生表，重建前会被删除

        Args:
            chunk_size: 每块因子数（建议 16–32）
            sample_size: 对账抽样因子数（0 表示跳过）
            temp_directory: DuckDB 溢写目录（建议放在 SSD 上）

        Returns:
            {'factors': 因子数, 'history_rows': 历史行数, 'wide_rows': 宽表行数}
        """
        empty = {'factors': 0, 'history_rows': 0, 'wide_rows': 0}

        if self._is_view('factor_data'):
            logger.info("factor_data 已是视图，无需重建")
            return empty

        if chunk_size < 1:
            raise ValueError("chunk_size 必须 >= 1")
        if sample_size < 0:
            raise ValueError("sample_size 不能为负")

        try:
            with self.conn as conn:
                self._configure_migration(conn)
                if temp_directory:
                    conn.execute(
                        f"SET temp_directory={escape_literal(temp_directory)}"
                    )

                if self._table_type(conn, 'factor_data') != 'BASE TABLE':
                    raise MigrationError("factor_data 不是源表，无法重建")

                # ---------- 清理派生表与上次残留 ----------
                leftovers = conn.execute("""
                    SELECT table_name FROM information_schema.tables
                    WHERE table_name LIKE '\\_rebuild\\_chunk\\_%' ESCAPE '\\'
                       OR table_name LIKE '\\_rebuild\\_merge\\_%' ESCAPE '\\'
                """).fetchall()
                for (name,) in leftovers:
                    conn.execute(f'DROP TABLE {quote_identifier(name)}')
                conn.execute('DROP TABLE IF EXISTS factor_wide')
                conn.execute('DROP TABLE IF EXISTS factor_history')

                # ---------- Phase A: 重建历史表 ----------
                conn.execute('SET preserve_insertion_order=true')
                logger.info("两阶段重建 Phase A: 扫描 factor_data → factor_history")
                src_rows = conn.execute(
                    'SELECT COUNT(*) FROM factor_data WHERE factor_value IS NOT NULL'
                ).fetchone()[0]
                factors = [
                    r[0] for r in conn.execute(
                        'SELECT DISTINCT factor_name FROM factor_data '
                        'WHERE factor_value IS NOT NULL ORDER BY factor_name'
                    ).fetchall()
                ]
                if not factors:
                    raise MigrationError("factor_data 无非空因子数据")

                conn.execute('''
                    CREATE TABLE factor_history AS
                    SELECT trade_date,
                           stock_code,
                           factor_name,
                           factor_value,
                           COALESCE(loaded_at, CURRENT_TIMESTAMP) AS loaded_at
                    FROM factor_data
                    WHERE factor_value IS NOT NULL
                ''')
                hist_rows = conn.execute(
                    'SELECT COUNT(*) FROM factor_history'
                ).fetchone()[0]
                if hist_rows != src_rows:
                    raise MigrationError(
                        f"历史表行数对账失败: {hist_rows} != {src_rows}"
                    )
                logger.info(
                    f"  factor_history: {hist_rows:,} 行 / {len(factors)} 因子"
                )

                # ---------- Phase B: 分块 pivot + 合并 ----------
                conn.execute('SET preserve_insertion_order=false')
                tables: list[str] = []
                cols_map: dict[str, list[str]] = {}

                for idx, start in enumerate(range(0, len(factors), chunk_size)):
                    chunk = factors[start:start + chunk_size]
                    name = f'_rebuild_chunk_{idx}'
                    value_cols = ',\n'.join(
                        f'arg_max(CASE WHEN factor_name = {escape_literal(f)} '
                        f'THEN factor_value END, '
                        f'COALESCE(loaded_at, CURRENT_TIMESTAMP)) '
                        f'AS {quote_identifier(f)}'
                        for f in chunk
                    )
                    in_list = ', '.join(escape_literal(f) for f in chunk)
                    conn.execute(f'''
                        CREATE TABLE {name} AS
                        SELECT trade_date, stock_code,
                               MAX(loaded_at) AS loaded_at,
                               {value_cols}
                        FROM factor_history
                        WHERE factor_name IN ({in_list})
                        GROUP BY trade_date, stock_code
                    ''')
                    tables.append(name)
                    cols_map[name] = chunk
                    logger.info(
                        f"  分块 {idx + 1}: {len(chunk)} 因子 / "
                        f"{conn.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]:,} 键"
                    )

                round_no = 0
                while len(tables) > 1:
                    next_tables: list[str] = []
                    next_cols: dict[str, list[str]] = {}
                    for i in range(0, len(tables), 2):
                        if i + 1 == len(tables):
                            next_tables.append(tables[i])
                            next_cols[tables[i]] = cols_map[tables[i]]
                            continue

                        a, b = tables[i], tables[i + 1]
                        name = f'_rebuild_merge_{round_no}_{i // 2}'
                        select_sql = ',\n'.join(
                            [f'a.{quote_identifier(c)} AS {quote_identifier(c)}'
                             for c in cols_map[a]]
                            + [f'b.{quote_identifier(c)} AS {quote_identifier(c)}'
                               for c in cols_map[b]]
                        )
                        conn.execute(f'''
                            CREATE TABLE {name} AS
                            SELECT trade_date, stock_code,
                                   GREATEST(a.loaded_at, b.loaded_at) AS loaded_at,
                                   {select_sql}
                            FROM {a} a
                            FULL JOIN {b} b USING (trade_date, stock_code)
                        ''')
                        next_tables.append(name)
                        next_cols[name] = cols_map[a] + cols_map[b]

                    for t in tables:
                        if t not in next_tables:
                            conn.execute(f'DROP TABLE {quote_identifier(t)}')
                    tables, cols_map = next_tables, next_cols
                    round_no += 1
                    logger.info(f"  合并轮次 {round_no}: 剩余 {len(tables)} 表")

                # ---------- Phase C: 宽表 + 主键 ----------
                final_table = tables[0]
                all_cols = cols_map[final_table]
                col_defs = ',\n'.join(
                    f'{quote_identifier(c)} DOUBLE' for c in all_cols
                )
                conn.execute(f'''
                    CREATE TABLE factor_wide (
                        trade_date DATE NOT NULL,
                        stock_code VARCHAR NOT NULL,
                        loaded_at TIMESTAMP NOT NULL,
                        {col_defs},
                        PRIMARY KEY (trade_date, stock_code)
                    )
                ''')
                select_cols = ', '.join(
                    quote_identifier(c) for c in all_cols
                )
                conn.execute(f'''
                    INSERT INTO factor_wide
                    SELECT trade_date, stock_code, loaded_at, {select_cols}
                    FROM {quote_identifier(final_table)}
                ''')
                conn.execute(f'DROP TABLE {quote_identifier(final_table)}')
                wide_rows = conn.execute(
                    'SELECT COUNT(*) FROM factor_wide'
                ).fetchone()[0]
                logger.info(f"  factor_wide: {wide_rows:,} 行 / {len(all_cols)} 列")

                # ---------- 抽样对账 ----------
                if sample_size and all_cols:
                    step = max(1, len(all_cols) // sample_size)
                    for f in all_cols[::step][:sample_size]:
                        # 历史表保留多版本，宽表按 (trade_date, stock_code) 去重
                        h = conn.execute(
                            'SELECT COUNT(*) FROM ('
                            '  SELECT DISTINCT trade_date, stock_code '
                            '  FROM factor_history WHERE factor_name = $f'
                            ') t', {'f': f}
                        ).fetchone()[0]
                        w = conn.execute(
                            f'SELECT COUNT({quote_identifier(f)}) FROM factor_wide'
                        ).fetchone()[0]
                        if h != w:
                            raise MigrationError(
                                f"因子 {f} 行数对账失败: history {h} != wide {w}"
                            )
                        logger.info(f"  对账 {f}: {h:,} 行 OK")

            # 视图替换使用独立连接（内部自行处理索引依赖）
            self._replace_factor_data_with_view()

            return {
                'factors': len(factors),
                'history_rows': hist_rows,
                'wide_rows': wide_rows,
            }

        except MigrationError:
            raise
        except Exception as e:
            logger.error(f"两阶段重建失败: {e}")
            raise MigrationError(f"两阶段重建失败: {e}") from e

    def _replace_factor_data_with_view(self) -> bool:
        """将 factor_data 表替换为视图（UNPIVOT factor_wide）

        安全语义（见 docs/design/migration_safety_design.md §4）：
        - RENAME 备份 + CREATE VIEW 在同一事务内，失败整体回滚
        - 原表绝不 DROP，保留为 factor_data_backup_<ts>
        - 已迁移（factor_data 已是视图）时直接返回 True

        Raises:
            MigrationError: 替换失败（已回滚，factor_data 仍为原表）
        """
        backup_name = f"factor_data_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        try:
            with self.conn as conn:
                self._configure_migration(conn)

                current_type = self._table_type(conn, 'factor_data')
                if current_type == 'VIEW':
                    logger.info("factor_data 已是视图，无需替换")
                    return True
                if current_type is None:
                    raise MigrationError("factor_data 表不存在，无法替换为视图")

                # 获取宽表因子列
                factor_cols = conn.execute('''
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'factor_wide'
                    AND column_name NOT IN ('trade_date', 'stock_code', 'loaded_at')
                    ORDER BY ordinal_position
                ''').fetchall()
                factor_list = [f[0] for f in factor_cols]

                if not factor_list:
                    raise MigrationError("factor_wide 无因子列，无法创建视图")

                # 动态视图：COLUMNS(*) 在每次查询时重新绑定，
                # 迁移后新增因子列会自动出现在 factor_data 视图中
                view_sql = '''
                    CREATE VIEW factor_data AS
                    SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
                    FROM factor_wide
                    UNPIVOT (
                        factor_value FOR factor_name IN (
                            COLUMNS(* EXCLUDE (trade_date, stock_code, loaded_at))
                        )
                    )
                    ORDER BY factor_name, trade_date, stock_code
                '''

                # 单事务：RENAME 备份 + 创建视图，任一失败整体回滚
                conn.execute("BEGIN TRANSACTION")
                try:
                    # 索引是 factor_data 的依赖对象，会阻止 RENAME
                    indexes = conn.execute(
                        "SELECT index_name FROM duckdb_indexes() "
                        "WHERE table_name = 'factor_data'"
                    ).fetchall()
                    for (index_name,) in indexes:
                        validate_identifier(index_name, '索引名')
                        conn.execute(f'DROP INDEX {quote_identifier(index_name)}')
                    if indexes:
                        logger.info(
                            f"已删除 factor_data 上的 {len(indexes)} 个索引"
                            "（避免 RENAME 依赖阻塞）"
                        )

                    conn.execute(
                        f'ALTER TABLE factor_data RENAME TO {quote_identifier(backup_name)}'
                    )
                    conn.execute(view_sql)
                    conn.execute("COMMIT")
                except Exception:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    # 兜底：若原名缺失而备份存在，改回原名
                    try:
                        if (
                            self._table_type(conn, 'factor_data') is None
                            and self._table_type(conn, backup_name) is not None
                        ):
                            conn.execute(
                                f'ALTER TABLE "{backup_name}" RENAME TO factor_data'
                            )
                            logger.warning("factor_data 视图替换失败，已恢复原表")
                    except Exception as restore_err:
                        logger.critical(
                            f"恢复 factor_data 原表失败，数据保留在 {backup_name}: {restore_err}"
                        )
                    raise

            logger.info(
                f"factor_data 视图创建完成（{len(factor_list)} 个因子），"
                f"原表备份为 {backup_name}"
            )
            return True

        except MigrationError:
            raise
        except Exception as e:
            logger.error(f"创建 factor_data 视图失败: {e}")
            raise MigrationError(f"factor_data 视图替换失败: {e}") from e

    def _table_type(self, conn, table_name: str) -> Optional[str]:
        """查询对象类型（TABLE / VIEW / None）

        Args:
            conn: 底层 duckdb 连接
            table_name: 对象名

        Returns:
            'BASE TABLE'、'VIEW' 或 None（不存在）
        """
        row = conn.execute(
            "SELECT table_type FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = ?",
            [table_name],
        ).fetchone()
        return row[0] if row else None

    def object_type(self, table_name: str) -> Optional[str]:
        """查询数据库对象类型

        Args:
            table_name: 对象名

        Returns:
            'BASE TABLE'、'VIEW' 或 None（不存在）
        """
        validate_identifier(table_name, '表名')
        try:
            with self.conn as conn:
                return self._table_type(conn, table_name)
        except Exception:
            return None

    def _is_view(self, table_name: str) -> bool:
        """判断对象是否为视图（不存在或异常时返回 False）"""
        try:
            with self.conn as conn:
                return self._table_type(conn, table_name) == 'VIEW'
        except Exception:
            return False

    def _get_row_count(self, table_name: str) -> int:
        """获取表行数"""
        validate_identifier(table_name, '表名')
        try:
            with self.conn as conn:
                return conn.execute(
                    f"SELECT COUNT(*) FROM {quote_identifier(table_name)}"
                ).fetchone()[0]
        except Exception:
            return 0
