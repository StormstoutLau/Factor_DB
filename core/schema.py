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
from typing import Optional

from .connection import DuckDBConnection

logger = logging.getLogger(__name__)


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
        try:
            with self.conn as conn:
                conn.execute(f"DROP INDEX IF EXISTS {index_name}")
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
        try:
            with self.conn as conn:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
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

        try:
            with self.conn as conn:
                # 行数
                row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

                # 列信息
                columns = conn.execute(
                    f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table_name}'"
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

        sql = f'''
            DELETE FROM {table_name}
            WHERE ({pk_list}, loaded_at) NOT IN (
                SELECT {pk_list}, MAX(loaded_at)
                FROM {table_name}
                WHERE loaded_at < '{keep_before}'
                GROUP BY {pk_list}
            )
            AND loaded_at < '{keep_before}'
        '''

        try:
            with self.conn as conn:
                conn.execute(sql)
            # 获取删除行数（DuckDB 不直接返回，用查询统计）
            # 简单返回成功标记
            logger.info(f"表 {table_name} 历史数据清理完成 (cutoff: {keep_before})")
            return 1
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
            parts.append(f'"{name}" DOUBLE')
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
        try:
            with self.conn as conn:
                conn.execute(f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS "{factor_name}" DOUBLE')
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
        try:
            with self.conn as conn:
                rows = conn.execute(f"""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    ORDER BY ordinal_position
                """).fetchall()
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
        case_exprs = ',\n        '.join(
            f'MAX(CASE WHEN factor_name = \'{f}\' THEN factor_value END) AS "{f}"'
            for f in factor_names
        )

        if use_latest:
            source_sql = f'''
                SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY trade_date, stock_code, factor_name
                            ORDER BY loaded_at DESC
                        ) AS rn
                    FROM {long_table}
                    WHERE factor_name IN ({', '.join(f"'{f}'" for f in factor_names)})
                )
                WHERE rn = 1
            '''
        else:
            source_sql = f'''
                SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
                FROM {long_table}
                WHERE factor_name IN ({', '.join(f"'{f}'" for f in factor_names)})
            '''

        sql = f'''
            INSERT INTO {wide_table}
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
                conn.execute(sql)
                row_count = conn.execute(f"SELECT COUNT(*) FROM {wide_table}").fetchone()[0]
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
        factor_list = ', '.join(f'"{f}"' for f in factor_names)

        if skip_nulls:
            where_clause = "WHERE factor_value IS NOT NULL"
        else:
            where_clause = ""

        sql = f'''
            INSERT INTO {long_table}
            SELECT trade_date, stock_code, factor_name, factor_value, loaded_at
            FROM {wide_table}
            UNPIVOT (
                factor_value FOR factor_name IN ({factor_list})
            )
            {where_clause}
        '''

        try:
            with self.conn as conn:
                before = conn.execute(f"SELECT COUNT(*) FROM {long_table}").fetchone()[0]
                conn.execute(sql)
                after = conn.execute(f"SELECT COUNT(*) FROM {long_table}").fetchone()[0]
                row_count = after - before
            logger.info(f"UNPIVOT {wide_table} → {long_table}: {row_count} 行, {len(factor_names)} 个因子")
            return row_count
        except Exception as e:
            logger.error(f"UNPIVOT 失败 {wide_table} → {long_table}: {e}")
            return 0

    def migrate_factor_data_to_wide(self, batch_size: int = 5) -> dict:
        """将 factor_data 长表迁移到双轨存储（factor_wide + factor_history）

        策略：
        1. 创建 factor_wide（宽表）和 factor_history（历史长表）
        2. 分因子批量迁移（避免内存溢出）
        3. 将 factor_data 替换为视图（UNPIVOT wide → long），保持向后兼容

        迁移完成后：
        - factor_wide: 最新版本因子数据，主键 (trade_date, stock_code)
        - factor_history: 所有历史版本，支持 PIT 查询
        - factor_data: 替换为视图，UNPIVOT factor_wide 的结果

        Args:
            batch_size: 每批处理的因子数量（默认 5）

        Returns:
            迁移统计 {factor_name: 行数}
        """
        logger.info("开始因子数据迁移...")
        stats = {}

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

        # Step 3: 创建历史表（如果不存在）
        if not self.table_exists('factor_history'):
            self.create_factor_history()
            logger.info("创建 factor_history 历史表完成")

        # Step 4: 分因子批量迁移
        for i in range(0, len(all_factors), batch_size):
            batch = all_factors[i:i + batch_size]
            logger.info(f"迁移批次 {i // batch_size + 1}: {batch}")

            for factor in batch:
                count = self._migrate_single_factor(factor)
                stats[factor] = count

        # Step 5: 将 factor_data 替换为视图
        logger.info("将 factor_data 替换为视图...")
        self._replace_factor_data_with_view()

        wide_count = self._get_row_count('factor_wide')
        hist_count = self._get_row_count('factor_history')
        logger.info(f"迁移完成: factor_wide={wide_count:,}, factor_history={hist_count:,}")
        return stats

    def _migrate_single_factor(self, factor_name: str) -> int:
        """迁移单个因子到宽表和历史表

        Args:
            factor_name: 因子名

        Returns:
            迁移的行数
        """
        try:
            with self.conn as conn:
                # 宽表 Upsert：每个 (trade_date, stock_code) 取最新版本
                # 使用 INSERT ... ON CONFLICT DO UPDATE 处理重复主键
                wide_sql = f'''
                    INSERT INTO factor_wide (trade_date, stock_code, loaded_at, "{factor_name}")
                    SELECT
                        trade_date,
                        stock_code,
                        COALESCE(loaded_at, CURRENT_TIMESTAMP) AS loaded_at,
                        factor_value
                    FROM (
                        SELECT *,
                               ROW_NUMBER() OVER (
                                   PARTITION BY trade_date, stock_code
                                   ORDER BY loaded_at DESC NULLS LAST
                               ) AS rn
                        FROM factor_data
                        WHERE factor_name = '{factor_name}'
                          AND factor_value IS NOT NULL
                    ) sub
                    WHERE rn = 1
                    ON CONFLICT (trade_date, stock_code)
                    DO UPDATE SET
                        "{factor_name}" = EXCLUDED."{factor_name}",
                        loaded_at = EXCLUDED.loaded_at
                '''
                conn.execute(wide_sql)
                wide_count = conn.execute(
                    f'SELECT COUNT(*) FROM factor_wide WHERE "{factor_name}" IS NOT NULL'
                ).fetchone()[0]

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
                    WHERE factor_name = '{factor_name}'
                      AND factor_value IS NOT NULL
                '''
                conn.execute(hist_sql)

            logger.info(f"  因子 {factor_name}: {wide_count:,} 行 → wide + history")
            return wide_count

        except Exception as e:
            logger.error(f"迁移因子 {factor_name} 失败: {e}")
            return 0

    def _replace_factor_data_with_view(self) -> bool:
        """将 factor_data 表替换为视图（UNPIVOT factor_wide）"""
        try:
            with self.conn as conn:
                # 获取宽表因子列
                factor_cols = conn.execute('''
                    SELECT column_name FROM information_schema.columns
                    WHERE table_name = 'factor_wide'
                    AND column_name NOT IN ('trade_date', 'stock_code', 'loaded_at')
                    ORDER BY ordinal_position
                ''').fetchall()
                factor_list = [f[0] for f in factor_cols]

                if not factor_list:
                    logger.warning("factor_wide 无因子列，视图创建失败")
                    return False

                # 必须先 DROP 原有 Table，再 CREATE View
                conn.execute("DROP TABLE IF EXISTS factor_data")
                conn.execute("DROP VIEW IF EXISTS factor_data")

                union_parts = []
                for f in factor_list:
                    union_parts.append(
                        f"SELECT trade_date, stock_code, '{f}' AS factor_name, \"{f}\" AS factor_value, loaded_at FROM factor_wide WHERE \"{f}\" IS NOT NULL"
                    )
                union_sql = '\n    UNION ALL\n    '.join(union_parts)
                view_sql = f'''
                    CREATE VIEW factor_data AS
                    {union_sql}
                    ORDER BY factor_name, trade_date, stock_code
                '''
                conn.execute(view_sql)

            logger.info(f"factor_data 视图创建完成（{len(factor_list)} 个因子）")
            return True

        except Exception as e:
            logger.error(f"创建 factor_data 视图失败: {e}")
            return False

    def _get_row_count(self, table_name: str) -> int:
        """获取表行数"""
        try:
            with self.conn as conn:
                return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        except Exception:
            return 0
