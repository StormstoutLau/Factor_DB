"""
另类数据加载器

支持产业链、卫星、互联网等另类数据导入
"""

from __future__ import annotations

import logging
import json
from pathlib import Path
from typing import Union, Optional, Dict, Any

import pandas as pd

from .base import BaseLoader, LoaderConfig

logger = logging.getLogger(__name__)


class AlternativeLoader(BaseLoader):
    """另类数据加载器

    Example:
        loader = AlternativeLoader()

        # 加载产业链数据
        df = pd.DataFrame({
            'trade_date': ['2024-01-01'],
            'data_type': ['chain'],
            'data_subtype': ['steel_price'],
            'entity_type': ['industry'],
            'entity_id': ['steel'],
            'value': [3500]
        })
        count = loader.load(df)
    """

    def load(self, data_path: Union[Path, pd.DataFrame],
            data_type: Optional[str] = None) -> int:
        """加载另类数据

        Args:
            data_path: 文件路径或 DataFrame
            data_type: 数据类型（如果 DataFrame 中没有）

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
        df = self._standardize_columns(df, data_type)

        # 插入数据
        count = self._insert_data(df)
        self._loaded_count += count

        logger.info(f"另类数据加载完成: {count} 条记录")
        return count

    def _read_file(self, file_path: Path) -> Optional[pd.DataFrame]:
        """读取文件"""
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
            elif suffix == '.json':
                return pd.read_json(file_path)
            else:
                logger.error(f"不支持的文件格式: {suffix}")
                return None
        except Exception as e:
            logger.error(f"文件读取失败: {e}")
            return None

    def _standardize_columns(self, df: pd.DataFrame,
                            data_type: Optional[str] = None) -> pd.DataFrame:
        """标准化列名"""
        column_mapping = {
            'date': 'trade_date',
            '日期': 'trade_date',
            'type': 'data_type',
            '类型': 'data_type',
            'subtype': 'data_subtype',
            '子类型': 'data_subtype',
            'entity': 'entity_id',
            '实体': 'entity_id',
            'entity_name': 'entity_name',
            '实体名称': 'entity_name',
            'val': 'value',
            '数值': 'value',
        }
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

        # 确保必需列存在
        if 'trade_date' not in df.columns:
            raise ValueError("缺少必需的列: trade_date")
        if 'value' not in df.columns:
            raise ValueError("缺少必需的列: value")

        # 转换日期
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        # 如果提供了 data_type，添加到 DataFrame
        if data_type and 'data_type' not in df.columns:
            df['data_type'] = data_type

        # 添加默认值
        if 'entity_type' not in df.columns:
            df['entity_type'] = 'unknown'
        if 'data_quality' not in df.columns:
            df['data_quality'] = 100

        # 生成 data_id
        if 'data_id' not in df.columns:
            df['data_id'] = df.apply(
                lambda row: f"{row['data_type']}_{row.get('data_subtype', '')}_{row['entity_id']}_{row['trade_date']}",
                axis=1
            )

        # 处理 metadata JSON
        if 'metadata' in df.columns and df['metadata'].dtype == 'object':
            df['metadata'] = df['metadata'].apply(
                lambda x: json.dumps(x) if isinstance(x, dict) else x
            )

        return df

    def _insert_data(self, df: pd.DataFrame) -> int:
        """插入另类数据"""
        # 确保所有列都存在
        all_cols = ['data_id', 'trade_date', 'data_type', 'data_subtype',
                    'entity_type', 'entity_id', 'entity_name', 'value',
                    'value_text', 'value_array', 'data_quality', 'source_id', 'metadata']
        for col in all_cols:
            if col not in df.columns:
                df[col] = None

        # 选择需要的列
        df = df[all_cols]

        try:
            with self.conn as conn:
                conn.register('alt_temp', df)
                conn.execute("""
                    INSERT INTO alternative_data
                    (data_id, trade_date, data_type, data_subtype, entity_type, entity_id,
                     entity_name, value, value_text, value_array, data_quality, source_id, metadata, update_time)
                    SELECT data_id, trade_date, data_type, data_subtype, entity_type, entity_id,
                           entity_name, value, value_text, value_array, data_quality, source_id, metadata, CURRENT_TIMESTAMP
                    FROM alt_temp
                    ON CONFLICT DO NOTHING
                """)
                conn.unregister('alt_temp')
            return len(df)
        except Exception as e:
            logger.error(f"另类数据插入失败: {e}")
            return 0

    def validate(self, data: pd.DataFrame) -> bool:
        """验证另类数据格式"""
        if data is None or data.empty:
            logger.error("数据为空")
            return False

        # 检查必需列
        has_date = any(col in data.columns for col in ['trade_date', 'date', '日期'])
        has_value = any(col in data.columns for col in ['value', 'val', '数值'])

        if not has_date:
            logger.error("缺少日期列")
            return False
        if not has_value:
            logger.error("缺少数值列")
            return False

        return True

    def register_data_type(self, data_type: str, type_name: str,
                          description: str = '',
                          default_entity_type: str = 'unknown') -> bool:
        """注册数据类型

        Args:
            data_type: 数据类型ID
            type_name: 类型名称
            description: 类型描述
            default_entity_type: 默认实体类型

        Returns:
            是否注册成功
        """
        try:
            with self.conn as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO alternative_types
                    (data_type, type_name, description, default_entity_type)
                    VALUES (?, ?, ?, ?)
                """, [data_type, type_name, description, default_entity_type])
            logger.info(f"数据类型注册成功: {data_type}")
            return True
        except Exception as e:
            logger.error(f"数据类型注册失败: {e}")
            return False
