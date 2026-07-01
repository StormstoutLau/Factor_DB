"""
舆情数据加载器

支持新闻、研报、社交媒体数据导入
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union, Optional

import pandas as pd

from .base import BaseLoader, LoaderConfig

logger = logging.getLogger(__name__)


class NewsLoader(BaseLoader):
    """舆情数据加载器

    Example:
        loader = NewsLoader()

        # 加载新闻数据
        news_df = pd.DataFrame({
            'news_id': ['news_001', 'news_002'],
            'publish_date': ['2024-01-01', '2024-01-02'],
            'title': ['标题1', '标题2'],
            'sentiment_score': [0.5, -0.3]
        })
        count = loader.load(news_df)
    """

    def load(self, data_path: Union[Path, pd.DataFrame],
            data_type: str = 'news') -> int:
        """加载舆情数据

        Args:
            data_path: 文件路径或 DataFrame
            data_type: 数据类型：news/report

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

        # 根据数据类型选择插入方法
        if data_type == 'news':
            count = self._insert_news(df)
        elif data_type == 'report':
            count = self._insert_reports(df)
        else:
            logger.error(f"未知数据类型: {data_type}")
            return 0

        self._loaded_count += count
        logger.info(f"舆情数据加载完成: {count} 条记录")
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
            else:
                logger.error(f"不支持的文件格式: {suffix}")
                return None
        except Exception as e:
            logger.error(f"文件读取失败: {e}")
            return None

    def _insert_news(self, df: pd.DataFrame) -> int:
        """插入新闻数据"""
        # 标准化列名
        column_mapping = {
            'id': 'news_id',
            'date': 'publish_date',
            'time': 'publish_time',
            'source': 'source_id',
            'stocks': 'related_stocks',
            'industries': 'related_industries',
            'score': 'sentiment_score',
            'label': 'sentiment_label',
        }
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

        # 确保必需列存在
        required = ['news_id', 'publish_date', 'title']
        for col in required:
            if col not in df.columns:
                logger.error(f"缺少必需列: {col}")
                return 0

        # 转换日期
        df['publish_date'] = pd.to_datetime(df['publish_date']).dt.date

        # 转换数组列
        if 'related_stocks' in df.columns and df['related_stocks'].dtype == 'object':
            df['related_stocks'] = df['related_stocks'].apply(
                lambda x: x if isinstance(x, list) else [s.strip() for s in str(x).split(',')] if pd.notna(x) else []
            )
        if 'related_industries' in df.columns and df['related_industries'].dtype == 'object':
            df['related_industries'] = df['related_industries'].apply(
                lambda x: x if isinstance(x, list) else [s.strip() for s in str(x).split(',')] if pd.notna(x) else []
            )

        # 确保所有列都存在
        all_cols = ['news_id', 'publish_date', 'publish_time', 'title', 'content',
                    'source_id', 'related_stocks', 'related_industries',
                    'sentiment_score', 'sentiment_label', 'keyword_count',
                    'read_count', 'comment_count']
        for col in all_cols:
            if col not in df.columns:
                df[col] = None

        try:
            with self.conn as conn:
                conn.register('news_temp', df)
                conn.execute("""
                    INSERT OR REPLACE INTO news_sentiment
                    (news_id, publish_date, publish_time, title, content,
                     source_id, related_stocks, related_industries,
                     sentiment_score, sentiment_label, keyword_count,
                     read_count, comment_count, update_time)
                    SELECT news_id, publish_date, publish_time, title, content,
                           source_id, related_stocks, related_industries,
                           sentiment_score, sentiment_label, keyword_count,
                           read_count, comment_count, CURRENT_TIMESTAMP
                    FROM news_temp
                """)
                conn.unregister('news_temp')
            return len(df)
        except Exception as e:
            logger.error(f"新闻数据插入失败: {e}")
            return 0

    def _insert_reports(self, df: pd.DataFrame) -> int:
        """插入研报数据"""
        # 标准化列名
        column_mapping = {
            'id': 'report_id',
            'date': 'publish_date',
            'stock': 'related_stock',
        }
        df = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})

        # 确保必需列存在
        required = ['report_id', 'publish_date', 'title']
        for col in required:
            if col not in df.columns:
                logger.error(f"缺少必需列: {col}")
                return 0

        # 转换日期
        df['publish_date'] = pd.to_datetime(df['publish_date']).dt.date

        # 确保所有列都存在
        all_cols = ['report_id', 'publish_date', 'title', 'analyst', 'brokerage',
                    'related_stock', 'rating_change', 'target_price', 'current_price',
                    'sentiment_score', 'content_summary']
        for col in all_cols:
            if col not in df.columns:
                df[col] = None

        try:
            with self.conn as conn:
                conn.register('report_temp', df)
                conn.execute("""
                    INSERT OR REPLACE INTO report_sentiment
                    (report_id, publish_date, title, analyst, brokerage,
                     related_stock, rating_change, target_price, current_price,
                     sentiment_score, content_summary, update_time)
                    SELECT report_id, publish_date, title, analyst, brokerage,
                           related_stock, rating_change, target_price, current_price,
                           sentiment_score, content_summary, CURRENT_TIMESTAMP
                    FROM report_temp
                """)
                conn.unregister('report_temp')
            return len(df)
        except Exception as e:
            logger.error(f"研报数据插入失败: {e}")
            return 0

    def validate(self, data: pd.DataFrame) -> bool:
        """验证舆情数据格式"""
        if data is None or data.empty:
            logger.error("数据为空")
            return False

        # 检查必需列（至少有一个ID和日期）
        has_id = any(col in data.columns for col in ['news_id', 'report_id', 'id'])
        has_date = any(col in data.columns for col in ['publish_date', 'date'])

        if not has_id:
            logger.error("缺少ID列")
            return False
        if not has_date:
            logger.error("缺少日期列")
            return False

        return True

    def load_news(self, news_df: pd.DataFrame) -> int:
        """加载新闻数据"""
        return self.load(news_df, data_type='news')

    def load_reports(self, reports_df: pd.DataFrame) -> int:
        """加载研报数据"""
        return self.load(reports_df, data_type='report')
