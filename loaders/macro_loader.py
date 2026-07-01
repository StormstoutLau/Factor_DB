"""
宏观经济数据加载器

支持多种数据源导入：
    - Excel/CSV 文件
    - DataFrame 直接导入
    - API 数据（预留）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union, Optional
from datetime import date

import pandas as pd

from .base import BaseLoader, LoaderConfig
from utils.validators import DataValidator

logger = logging.getLogger(__name__)


class MacroLoader(BaseLoader):
    """宏观数据加载器

    Example:
        loader = MacroLoader()

        # 从 DataFrame 加载
        df = pd.DataFrame({
            'trade_date': ['2024-01-01', '2024-02-01'],
            'indicator_id': ['CPI', 'CPI'],
            'value': [2.1, 2.3]
        })
        count = loader.load(df)

        # 从 CSV 加载
        count = loader.load(Path('macro_data.csv'))
    """

    def load(self, data_path: Union[Path, pd.DataFrame],
            indicator_id: Optional[str] = None) -> int:
        """加载宏观数据

        Args:
            data_path: 文件路径或 DataFrame
            indicator_id: 指标ID（如果是单个指标）

        Returns:
            加载的记录数
        """
        if isinstance(data_path, pd.DataFrame):
            df = data_path.copy()
        else:
            df = self._read_file(data_path)

        if df is None or df.empty:
            logger.warning("数据为空，跳过加载")
            return 0

        # 数据验证
        if self.config.validate_data:
            if not self.validate(df):
                logger.error("数据验证失败")
                return 0

        # 标准化列名
        df = self._standardize_columns(df, indicator_id)

        # 加载指标信息
        self._load_indicator_info(df)

        # 批量插入
        count = self._insert_macro_data(df)
        self._loaded_count += count

        logger.info(f"宏观数据加载完成: {count} 条记录")
        return count

    def _read_file(self, file_path: Path) -> Optional[pd.DataFrame]:
        """读取文件

        Args:
            file_path: 文件路径

        Returns:
            DataFrame 或 None
        """
        suffix = file_path.suffix.lower()

        try:
            if suffix == '.csv':
                return pd.read_csv(file_path)
            elif suffix in ('.xls', '.xlsx'):
                return pd.read_excel(file_path)
            elif suffix in ('.pkl', '.pickle'):
                return pd.read_pickle(file_path)
            elif suffix == '.parquet':
                return pd.read_parquet(file_path)
            else:
                logger.error(f"不支持的文件格式: {suffix}")
                return None
        except Exception as e:
            logger.error(f"文件读取失败: {e}")
            return None

    def _standardize_columns(self, df: pd.DataFrame,
                            indicator_id: Optional[str] = None) -> pd.DataFrame:
        """标准化列名

        Args:
            df: 原始 DataFrame
            indicator_id: 指标ID

        Returns:
            标准化后的 DataFrame
        """
        # 列名映射
        column_mapping = {
            'date': 'trade_date',
            '日期': 'trade_date',
            'indicator': 'indicator_id',
            '指标': 'indicator_id',
            'indicator_name': 'indicator_name',
            '指标名称': 'indicator_name',
            'value': 'value',
            '数值': 'value',
            'val': 'value',
        }

        # 重命名列
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

        # 确保必需的列存在
        if 'trade_date' not in df.columns:
            raise ValueError("缺少必需的列: trade_date")
        if 'value' not in df.columns:
            raise ValueError("缺少必需的列: value")

        # 如果提供了 indicator_id，添加到 DataFrame
        if indicator_id and 'indicator_id' not in df.columns:
            df['indicator_id'] = indicator_id

        # 转换日期格式
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        # 添加默认值
        if 'value_type' not in df.columns:
            df['value_type'] = 'raw'
        if 'data_quality' not in df.columns:
            df['data_quality'] = 100

        return df

    def _load_indicator_info(self, df: pd.DataFrame) -> None:
        """加载指标信息

        Args:
            df: 宏观数据 DataFrame
        """
        if 'indicator_id' not in df.columns:
            return

        # 提取唯一的指标信息
        indicators = df[['indicator_id']].drop_duplicates()
        if 'indicator_name' in df.columns:
            indicators['indicator_name'] = df.groupby('indicator_id')['indicator_name'].first().values

        # 插入指标信息
        try:
            with self.conn as conn:
                for _, row in indicators.iterrows():
                    conn.execute("""
                        INSERT OR IGNORE INTO macro_indicators (indicator_id, indicator_name)
                        VALUES (?, ?)
                    """, [row['indicator_id'], row.get('indicator_name', row['indicator_id'])])
        except Exception as e:
            logger.warning(f"指标信息加载失败: {e}")

    def _insert_macro_data(self, df: pd.DataFrame) -> int:
        """插入宏观数据

        Args:
            df: 宏观数据 DataFrame

        Returns:
            插入记录数
        """
        # 确保所有需要的列都存在
        required_cols = ['trade_date', 'indicator_id', 'value', 'value_type', 'data_quality']
        for col in required_cols:
            if col not in df.columns:
                if col == 'indicator_id':
                    raise ValueError("缺少 indicator_id 列")
                df[col] = None

        # 确保列顺序与表一致
        df = df[['trade_date', 'indicator_id', 'value', 'value_type', 'data_quality']]

        # 使用 COPY 批量导入
        try:
            with self.conn as conn:
                conn.register('macro_temp', df)
                conn.execute("""
                    INSERT OR REPLACE INTO macro_data (trade_date, indicator_id, indicator_name, value, value_type, data_quality, source_id, update_time)
                    SELECT trade_date, indicator_id, indicator_id as indicator_name, value, value_type, data_quality, NULL, CURRENT_TIMESTAMP
                    FROM macro_temp
                """)
                conn.unregister('macro_temp')
            return len(df)
        except Exception as e:
            logger.error(f"宏观数据插入失败: {e}")
            return 0

    def validate(self, data: pd.DataFrame) -> bool:
        """验证宏观数据格式

        Args:
            data: 待验证的数据

        Returns:
            是否验证通过
        """
        if data is None or data.empty:
            logger.error("数据为空")
            return False

        # 检查必需列
        required_cols = ['trade_date', 'value']
        if not any(col in data.columns for col in ['indicator_id', 'indicator', '指标']):
            if 'indicator_id' not in data.columns:
                logger.error("缺少指标标识列")
                return False

        for col in required_cols:
            if col not in data.columns and col not in ['date', '日期', '数值', 'val']:
                logger.error(f"缺少必需列: {col}")
                return False

        # 检查数值列
        if 'value' in data.columns:
            if not pd.api.types.is_numeric_dtype(data['value']):
                logger.error("value 列应为数值类型")
                return False

        return True

    def load_indicator_definitions(self, indicators_df: pd.DataFrame) -> int:
        """加载指标定义

        Args:
            indicators_df: 指标定义 DataFrame
                必需列: indicator_id, indicator_name
                可选列: category, frequency, unit, is_leading, description

        Returns:
            加载记录数
        """
        if indicators_df is None or indicators_df.empty:
            return 0

        try:
            with self.conn as conn:
                conn.register('indicators_temp', indicators_df)
                conn.execute("""
                    INSERT OR REPLACE INTO macro_indicators
                    SELECT indicator_id, indicator_name,
                           COALESCE(category, ''),
                           COALESCE(frequency, ''),
                           COALESCE(unit, ''),
                           COALESCE(is_leading, FALSE),
                           COALESCE(description, ''),
                           CURRENT_TIMESTAMP
                    FROM indicators_temp
                """)
                conn.unregister('indicators_temp')
            return len(indicators_df)
        except Exception as e:
            logger.error(f"指标定义加载失败: {e}")
            return 0
