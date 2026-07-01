"""
Level 1 数据加载器

从 Feather 文件导入 Level 1 分钟级行情数据到 DuckDB。
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow.feather as feather
from tqdm import tqdm

from .base import BaseLoader, LoaderConfig
from utils.code_utils import normalize_stock_code

logger = logging.getLogger(__name__)


class Level1Loader(BaseLoader):
    """Level 1 数据加载器

    支持从 Feather 文件导入分钟级行情数据。

    Example:
        config = LoaderConfig(db_path='market.db')
        loader = Level1Loader(config)
        count = loader.load(Path('E:/Level 1 Data'))
        print(f"加载完成: {count} 条记录")
    """

    def __init__(self, config: Optional[LoaderConfig] = None):
        super().__init__(config)

    def load(self, data_path: Path) -> int:
        """加载 Level 1 数据

        Args:
            data_path: Feather 文件或目录路径

        Returns:
            加载的总记录数
        """
        if data_path.is_file():
            return self._load_single_file(data_path)
        elif data_path.is_dir():
            return self._load_directory(data_path)
        else:
            logger.error(f"路径不存在: {data_path}")
            return 0

    def _load_single_file(self, file_path: Path) -> int:
        """加载单个 Feather 文件

        Args:
            file_path: Feather 文件路径

        Returns:
            加载记录数
        """
        try:
            # 从文件名提取日期
            date_str = file_path.stem  # "2014-01-02"
            trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            logger.info(f"加载文件: {file_path.name}")

            # 读取 Feather
            df = feather.read_feather(str(file_path))

            # 验证数据
            if not self.validate(df):
                logger.warning(f"数据验证失败: {file_path.name}")
                return 0

            # 转换数据格式
            df = self._transform_data(df, trade_date)

            # 导入 DuckDB
            count = self._import_to_duckdb(df)

            self._loaded_count += count
            logger.info(f"文件导入完成: {file_path.name} ({count} 条)")

            return count

        except Exception as e:
            logger.error(f"加载文件失败 {file_path}: {e}")
            return 0

    def _load_directory(self, data_dir: Path) -> int:
        """加载目录下所有 Feather 文件

        Args:
            data_dir: 数据目录

        Returns:
            加载总记录数
        """
        files = sorted(data_dir.glob('*.feather'))
        if not files:
            logger.warning(f"目录下无 Feather 文件: {data_dir}")
            return 0

        logger.info(f"发现 {len(files)} 个 Feather 文件")

        total_count = 0
        iterator = tqdm(files) if self.config.show_progress else files

        for file_path in iterator:
            count = self._load_single_file(file_path)
            total_count += count

            if self.config.show_progress and isinstance(iterator, tqdm):
                iterator.set_postfix({'total': total_count})

        logger.info(f"目录导入完成: {total_count} 条记录")
        return total_count

    def _transform_data(self, df: pd.DataFrame, trade_date) -> pd.DataFrame:
        """转换数据格式

        Args:
            df: 原始 DataFrame
            trade_date: 交易日期

        Returns:
            转换后的 DataFrame
        """
        # 转换时间格式 (93000 -> 09:30:00)
        df['trade_time'] = df['time'].apply(
            lambda x: time(x // 10000, (x // 100) % 100, x % 100)
        )

        # 转换股票代码 (1 -> 000001.SZ)
        df['stock_code'] = df['stock_code'].apply(self._format_stock_code)

        # 添加日期
        df['trade_date'] = trade_date

        # 选择并重命名列
        df = df[[
            'trade_date', 'trade_time', 'stock_code',
            'open', 'high', 'low', 'close', 'volume', 'amount'
        ]]

        return df

    def _format_stock_code(self, code: int) -> str:
        """格式化股票代码为6位纯数字

        Args:
            code: 数字代码 (1 → 000001, 600000 → 600000)

        Returns:
            6位纯数字字符串
        """
        return normalize_stock_code(code)

    def _import_to_duckdb(self, df: pd.DataFrame) -> int:
        """导入数据到 DuckDB

        Args:
            df: 转换后的 DataFrame

        Returns:
            导入记录数
        """
        try:
            with self.conn as conn:
                # 注册临时表
                conn.register('temp_level1', df)

                # 检查是否已存在
                if self.config.skip_existing:
                    trade_date = df['trade_date'].iloc[0]
                    result = conn.execute(
                        "SELECT COUNT(*) FROM level1_snapshots WHERE trade_date = ?",
                        [trade_date]
                    ).fetchone()
                    if result[0] > 0:
                        logger.debug(f"日期 {trade_date} 已存在，跳过")
                        conn.unregister('temp_level1')
                        return 0

                # 插入数据
                conn.execute('''
                    INSERT INTO level1_snapshots 
                    SELECT * FROM temp_level1
                ''')

                conn.unregister('temp_level1')

            return len(df)

        except Exception as e:
            logger.error(f"导入 DuckDB 失败: {e}")
            return 0

    def validate(self, data: pd.DataFrame) -> bool:
        """验证 Level 1 数据格式

        Args:
            data: 待验证的 DataFrame

        Returns:
            是否验证通过
        """
        required_columns = ['time', 'stock_code', 'open', 'high', 'low', 'close', 'volume', 'amount']

        # 检查必需列
        for col in required_columns:
            if col not in data.columns:
                logger.error(f"缺少必需列: {col}")
                return False

        # 检查数据类型
        if not pd.api.types.is_integer_dtype(data['time']):
            logger.warning("time 列应为整数类型")

        if not pd.api.types.is_numeric_dtype(data['close']):
            logger.error("close 列应为数值类型")
            return False

        # 检查空值比例
        null_ratio = data.isnull().sum() / len(data)
        if (null_ratio > 0.5).any():
            logger.warning("部分列空值比例过高")

        return True

    def get_date_range(self) -> tuple[Optional[datetime], Optional[datetime]]:
        """获取已加载数据的日期范围

        Returns:
            (最早日期, 最晚日期)
        """
        try:
            with self.conn as conn:
                result = conn.execute('''
                    SELECT MIN(trade_date), MAX(trade_date) 
                    FROM level1_snapshots
                ''').fetchone()
                return result[0], result[1]
        except Exception as e:
            logger.error(f"获取日期范围失败: {e}")
            return None, None


class Level1ParquetConverter:
    """Level 1 Feather → Parquet 分区转换器

    将按日存储的 Feather 文件转换为按 trade_date 分区的 Parquet 文件。
    数据不导入 DuckDB，DuckDB 通过 read_parquet() 直接查询分区文件，
    利用分区裁剪 + Zonemap 大幅降低内存占用。

    目录结构（Hive 风格分区）:
        output_dir/
            trade_date=2024-01-02/
                data.parquet
            trade_date=2024-01-03/
                data.parquet

    Example:
        converter = Level1ParquetConverter('/path/to/level1_parquet')
        total = converter.convert_directory('/path/to/feather_files')
        print(f"转换完成: {total} 条记录")
    """

    def __init__(self, output_dir: str | Path):
        """初始化转换器

        Args:
            output_dir: Parquet 分区输出根目录
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def convert_file(self, feather_path: str | Path) -> int:
        """转换单个 Feather 文件为 Parquet 分区

        Args:
            feather_path: Feather 文件路径

        Returns:
            转换的记录数
        """
        feather_path = Path(feather_path)
        try:
            date_str = feather_path.stem
            trade_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            part_dir = self.output_dir / f'trade_date={date_str}'
            parquet_path = part_dir / 'data.parquet'

            if parquet_path.exists():
                logger.debug(f"分区已存在，跳过: {date_str}")
                return 0

            df = feather.read_feather(str(feather_path))

            df = self._transform_data(df, trade_date)

            part_dir.mkdir(parents=True, exist_ok=True)
            df.to_parquet(parquet_path, compression='snappy')

            count = len(df)
            logger.info(f"转换完成: {feather_path.name} → {date_str} ({count} 条)")
            return count

        except Exception as e:
            logger.error(f"转换文件失败 {feather_path}: {e}")
            return 0

    def convert_directory(self, feather_dir: str | Path) -> int:
        """批量转换目录下所有 Feather 文件

        Args:
            feather_dir: Feather 文件目录

        Returns:
            转换的总记录数
        """
        feather_dir = Path(feather_dir)
        if not feather_dir.is_dir():
            logger.error(f"目录不存在: {feather_dir}")
            return 0

        files = sorted(feather_dir.glob('*.feather'))
        if not files:
            logger.warning(f"目录下无 Feather 文件: {feather_dir}")
            return 0

        logger.info(f"发现 {len(files)} 个 Feather 文件")

        total = 0
        for file_path in tqdm(files, desc='转换 Parquet'):
            count = self.convert_file(file_path)
            total += count

        logger.info(f"目录转换完成: {total} 条记录")
        return total

    def _transform_data(self, df: pd.DataFrame, trade_date) -> pd.DataFrame:
        """转换数据格式（复用 Level1Loader 的核心逻辑）

        Args:
            df: 原始 Feather DataFrame
            trade_date: 交易日期

        Returns:
            转换后的 DataFrame（含 trade_date 列）
        """
        if 'time' in df.columns and pd.api.types.is_integer_dtype(df['time']):
            df['trade_time'] = df['time'].apply(
                lambda x: time(x // 10000, (x // 100) % 100, x % 100)
            )
        elif 'trade_time' not in df.columns:
            raise ValueError("Feather 文件中缺少 time 或 trade_time 列")

        if 'stock_code' in df.columns:
            df['stock_code'] = df['stock_code'].apply(normalize_stock_code)

        df['trade_date'] = trade_date

        cols = [
            'trade_date', 'trade_time', 'stock_code',
            'open', 'high', 'low', 'close', 'volume', 'amount'
        ]
        return df[cols].copy()

