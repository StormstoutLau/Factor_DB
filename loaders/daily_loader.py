"""
日 K 数据加载器

从 Pickle/CSV/Parquet 文件导入日 K 数据到 DuckDB。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

import pandas as pd
from tqdm import tqdm

from .base import BaseLoader, LoaderConfig
from utils.code_utils import normalize_stock_code

logger = logging.getLogger(__name__)


class DailyLoader(BaseLoader):
    """日 K 数据加载器

    支持从多种格式导入日 K 数据：
    - Pickle (.pkl)
    - CSV (.csv)
    - Parquet (.parquet)
    - Feather (.feather)

    Example:
        config = LoaderConfig(db_path='market.db')
        loader = DailyLoader(config)

        # 加载单个文件
        loader.load(Path('stock_close.pkl'))

        # 加载目录
        loader.load(Path('E:/Ashare_data/market_data'))
    """

    # 支持的文件格式
    SUPPORTED_FORMATS = {'.pkl', '.pickle', '.csv', '.parquet', '.feather'}

    def __init__(self, config: Optional[LoaderConfig] = None):
        super().__init__(config)

        # 性能优化配置
        with self.conn as conn:
            conn.execute("SET memory_limit='20GB'")
            conn.execute("SET threads=4")
            conn.execute("SET preserve_insertion_order=false")
            conn.execute("SET temp_directory='F:\\Coding\\Factor_DB\\factor_db.duckdb.tmp'")
            logger.info("DuckDB 性能配置已优化 (memory_limit=20GB, threads=4, temp_directory 已设置)")

    def load(self, data_path: Path, field_name: str = '') -> int:
        """加载日 K 数据

        Args:
            data_path: 数据文件或目录路径
            field_name: 目标字段名（空则使用文件名）

        Returns:
            加载的总记录数
        """
        if data_path.is_file():
            return self._load_single_file(data_path, field_name)
        elif data_path.is_dir():
            return self._load_directory(data_path)
        else:
            logger.error(f"路径不存在: {data_path}")
            return 0

    def load_dataframe(self, df: pd.DataFrame, field_name: str) -> int:
        """直接加载预处理后的 DataFrame 到 factor_data

        适用于需要预处理的数据（如类型转换、值映射等），
        绕过文件读取，直接使用 DuckDB UNPIVOT 导入。

        Args:
            df: 宽格式 DataFrame (dates × stocks)
            field_name: 因子名称

        Returns:
            导入记录数
        """
        logger.info(f"加载 DataFrame → factor_data.{field_name}")
        return self._import_price_data(df, field_name)

    def _load_single_file(self, file_path: Path, field_name: str = '') -> int:
        """加载单个数据文件

        Args:
            file_path: 数据文件路径
            field_name: 目标字段名（空则使用文件名 stem）

        Returns:
            加载记录数
        """
        suffix = file_path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            logger.warning(f"不支持的文件格式: {suffix}")
            return 0

        try:
            logger.info(f"加载文件: {file_path.name}")

            # 根据格式读取
            if suffix in ('.pkl', '.pickle'):
                df = pd.read_pickle(file_path)
            elif suffix == '.csv':
                df = pd.read_csv(file_path, index_col=0, parse_dates=True)
            elif suffix == '.parquet':
                df = pd.read_parquet(file_path)
            elif suffix == '.feather':
                df = pd.read_feather(file_path)

            # 验证数据
            if not self.validate(df):
                logger.warning(f"数据验证失败: {file_path.name}")
                return 0

            # 确定字段名：优先使用传入的 field_name，否则用文件名
            target_field = field_name if field_name else file_path.stem

            # 转换并导入
            count = self._import_price_data(df, target_field)

            self._loaded_count += count
            logger.info(f"文件导入完成: {file_path.name} ({count} 条)")

            return count

        except Exception as e:
            logger.error(f"加载文件失败 {file_path}: {e}")
            return 0

    def _load_directory(self, data_dir: Path) -> int:
        """加载目录下所有数据文件

        Args:
            data_dir: 数据目录

        Returns:
            加载总记录数
        """
        files = []
        for ext in self.SUPPORTED_FORMATS:
            files.extend(data_dir.glob(f'*{ext}'))

        if not files:
            logger.warning(f"目录下无支持的数据文件: {data_dir}")
            return 0

        logger.info(f"发现 {len(files)} 个数据文件")

        total_count = 0
        iterator = tqdm(files) if self.config.show_progress else files

        for file_path in iterator:
            count = self._load_single_file(file_path)
            total_count += count

            if self.config.show_progress and isinstance(iterator, tqdm):
                iterator.set_postfix({'total': total_count})

        logger.info(f"目录导入完成: {total_count} 条记录")
        return total_count

    def _import_price_data(self, df: pd.DataFrame, field_name: str) -> int:
        """导入价格数据 - 使用 DuckDB UNPIVOT 高性能转换

        将宽格式 (dates × stocks) 转为长格式导入。
        使用 DuckDB 原生 UNPIVOT 替代 pandas melt，显著提升性能。

        Args:
            df: 价格数据 DataFrame
            field_name: 字段名 (close/open/high/low/volume/amount 或因子名)

        Returns:
            导入记录数
        """
        try:
            # 确保 trade_date 是第一列
            date_col = df.columns[0]
            if date_col == 'trade_date':
                # 已经以 trade_date 为第一列
                pass
            elif isinstance(df.index, pd.DatetimeIndex):
                df = df.reset_index()
                df = df.rename(columns={df.columns[0]: 'trade_date'})
            else:
                df = df.reset_index()
                date_col = df.columns[0]
                if date_col != 'trade_date':
                    df = df.rename(columns={date_col: 'trade_date'})

            # 标准化股票代码（列名）
            code_cols = [c for c in df.columns if c != 'trade_date']
            rename_map = {c: normalize_stock_code(c) for c in code_cols}
            df = df.rename(columns=rename_map)

            # 使用 DuckDB UNPIVOT 进行宽→长转换（比 pandas melt 快 3-4x）
            with self.conn as conn:
                # 确保内存限制生效
                conn.execute("SET memory_limit='20GB'")
                conn.execute("SET threads=4")
                conn.execute("SET preserve_insertion_order=false")

                conn.register('_wide', df)

                # Step 1: UNPIVOT 到临时表
                conn.execute('''
                    CREATE TEMP TABLE _long AS
                    SELECT trade_date, stock_code, CAST(value AS DOUBLE) AS val
                    FROM (
                        UNPIVOT _wide
                        ON COLUMNS(* EXCLUDE trade_date)
                        INTO NAME stock_code VALUE value
                    )
                    WHERE value IS NOT NULL
                ''')

                # Step 2: 获取实际行数
                count = conn.execute('SELECT COUNT(*) FROM _long').fetchone()[0]

                # Step 3: 导入到目标表
                if field_name in ('close', 'open', 'high', 'low', 'volume', 'amount'):
                    conn.execute(f'''
                        INSERT INTO daily_prices (trade_date, stock_code, {field_name})
                        SELECT trade_date, stock_code, val AS {field_name}
                        FROM _long
                    ''')
                else:
                    conn.execute(f'''
                        INSERT INTO factor_data (trade_date, stock_code, factor_name, factor_value)
                        SELECT trade_date, stock_code, '{field_name}' AS factor_name, val AS factor_value
                        FROM _long
                    ''')

                conn.execute('DROP TABLE IF EXISTS _long')
                conn.unregister('_wide')

            return count

        except Exception as e:
            logger.error(f"导入价格数据失败: {e}")
            import traceback
            traceback.print_exc()
            return 0

    def _import_to_daily_table(self, df: pd.DataFrame, field_name: str) -> int:
        """导入到日 K 表

        Args:
            df: 长格式 DataFrame
            field_name: 字段名

        Returns:
            导入记录数
        """
        try:
            with self.conn as conn:
                # 注册临时表
                conn.register('temp_daily', df)

                # 使用 INSERT 追加写入（loaded_at 自动填充），支持 PIT 多版本
                conn.execute(f'''
                    INSERT INTO daily_prices (trade_date, stock_code, {field_name})
                    SELECT trade_date, stock_code, {field_name}
                    FROM temp_daily
                ''')

                conn.unregister('temp_daily')

            return len(df)

        except Exception as e:
            logger.error(f"导入日 K 表失败: {e}")
            return 0

    def _import_to_factor_table(self, df: pd.DataFrame, factor_name: str) -> int:
        """导入到因子表

        Args:
            df: 长格式 DataFrame
            factor_name: 因子名

        Returns:
            导入记录数
        """
        try:
            # 重命名列为标准格式
            df = df.rename(columns={factor_name: 'factor_value'})
            df['factor_name'] = factor_name

            with self.conn as conn:
                conn.register('temp_factor', df)

                conn.execute('''
                    INSERT INTO factor_data 
                    (trade_date, stock_code, factor_name, factor_value)
                    SELECT trade_date, stock_code, factor_name, factor_value
                    FROM temp_factor
                ''')

                conn.unregister('temp_factor')

            return len(df)

        except Exception as e:
            logger.error(f"导入因子表失败: {e}")
            return 0

    def validate(self, data: pd.DataFrame) -> bool:
        """验证日 K 数据格式

        Args:
            data: 待验证的 DataFrame

        Returns:
            是否验证通过
        """
        # 检查是否为空
        if data.empty:
            logger.error("数据为空")
            return False

        # 检查索引是否为日期
        if not isinstance(data.index, pd.DatetimeIndex):
            if not pd.api.types.is_datetime64_any_dtype(data.iloc[:, 0]):
                logger.warning("第一列应为日期类型")

        # 检查列名（股票代码）
        if len(data.columns) < 1:
            logger.error("缺少股票代码列")
            return False

        # 检查数值类型
        numeric_cols = data.select_dtypes(include=['number']).columns
        if len(numeric_cols) < 1:
            logger.error("缺少数值列")
            return False

        # 检查空值比例
        null_ratio = data.isnull().sum().sum() / (data.shape[0] * data.shape[1])
        if null_ratio > 0.8:
            logger.warning(f"空值比例过高: {null_ratio:.2%}")

        return True

    def load_stock_info(self, info_path: Path) -> int:
        """加载股票基本信息

        Args:
            info_path: 股票信息文件路径

        Returns:
            导入记录数
        """
        try:
            df = pd.read_pickle(info_path) if info_path.suffix == '.pkl' else pd.read_csv(info_path)

            # 标准化列名
            column_mapping = {
                'code': 'stock_code',
                'name': 'stock_name',
                'list_date': 'list_date',
                'industry': 'industry',
                'market': 'market'
            }

            df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

            with self.conn as conn:
                conn.register('temp_info', df)
                conn.execute('''
                    INSERT OR REPLACE INTO stock_info 
                    (stock_code, stock_name, list_date, industry, market)
                    SELECT stock_code, stock_name, list_date, industry, market
                    FROM temp_info
                ''')
                conn.unregister('temp_info')

            return len(df)

        except Exception as e:
            logger.error(f"加载股票信息失败: {e}")
            return 0

    # =============================
    # 宽表架构 (方案A: factor_wide + factor_history)
    # =============================

    def load_factor_to_wide(self, df: pd.DataFrame, factor_name: str) -> int:
        """加载单因子数据到宽表 (factor_wide)，同时双写到历史长表 (factor_history)

        宽表特性：
        - PK (trade_date, stock_code) → ART 索引仅 ~600MB（32GB 机器可行）
        - INSERT ON CONFLICT DO UPDATE 实现 upsert
        - 历史版本完整保留在 factor_history（PIT 审计）

        Args:
            df: 宽格式因子 DataFrame (index=日期, columns=股票代码)
            factor_name: 因子名称

        Returns:
            宽表中的总行数（所有因子，非仅本次）
        """
        logger.info(f"加载因子 {factor_name} → factor_wide + factor_history")

        try:
            from core.schema import SchemaManager
            schema = SchemaManager(self.conn)

            if not schema.table_exists('factor_wide'):
                schema.create_factor_wide([factor_name])
            elif factor_name not in schema.get_factor_columns('factor_wide'):
                schema.add_factor_column('factor_wide', factor_name)

            if not schema.table_exists('factor_history'):
                schema.create_factor_history()

            work_df = df.copy()

            if isinstance(work_df.index, pd.DatetimeIndex):
                work_df = work_df.reset_index()
                work_df = work_df.rename(columns={work_df.columns[0]: 'trade_date'})
            else:
                work_df = work_df.reset_index()
                date_col = work_df.columns[0]
                if date_col != 'trade_date':
                    work_df = work_df.rename(columns={date_col: 'trade_date'})

            code_cols = [c for c in work_df.columns if c != 'trade_date']
            rename_map = {c: normalize_stock_code(c) for c in code_cols}
            work_df = work_df.rename(columns=rename_map)

            with self.conn as conn:
                conn.execute("SET memory_limit='20GB'")
                conn.execute("SET threads=4")

                conn.execute('DROP TABLE IF EXISTS _long_tmp')
                conn.register('_wide_tmp', work_df)

                conn.execute('''
                    CREATE TEMP TABLE _long_tmp AS
                    SELECT
                        CAST(trade_date AS DATE) AS trade_date,
                        stock_code,
                        CAST(value AS DOUBLE) AS factor_value
                    FROM (
                        UNPIVOT _wide_tmp
                        ON COLUMNS(* EXCLUDE trade_date)
                        INTO NAME stock_code VALUE value
                    )
                    WHERE value IS NOT NULL
                ''')

                hist_count = conn.execute('SELECT COUNT(*) FROM _long_tmp').fetchone()[0]
                conn.execute(f'''
                    INSERT INTO factor_history (trade_date, stock_code, factor_name, factor_value)
                    SELECT trade_date, stock_code, '{factor_name}' AS factor_name, factor_value
                    FROM _long_tmp
                ''')
                logger.debug(f"factor_history 写入 {hist_count} 行")

                conn.execute(f'''
                    INSERT INTO factor_wide (trade_date, stock_code, "{factor_name}", loaded_at)
                    SELECT trade_date, stock_code, factor_value, CURRENT_TIMESTAMP AS loaded_at
                    FROM _long_tmp
                    ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                        "{factor_name}" = EXCLUDED."{factor_name}",
                        loaded_at = EXCLUDED.loaded_at
                ''')

                wide_count = conn.execute('SELECT COUNT(*) FROM factor_wide').fetchone()[0]

                conn.execute('DROP TABLE IF EXISTS _long_tmp')
                conn.unregister('_wide_tmp')

            logger.info(f"因子 {factor_name} 加载完成: wide={wide_count} 行, history={hist_count} 行")
            return wide_count

        except Exception as e:
            logger.error(f"加载因子 {factor_name} 到宽表失败: {e}")
            import traceback
            traceback.print_exc()
            return 0

