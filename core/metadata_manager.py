"""
元数据统一管理模块

职责：
    - 统一管理所有数据类型的元数据
    - 数据字典查询和维护
    - 数据源信息管理
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional, List, Dict, Any

import pandas as pd

from .connection import DuckDBConnection

logger = logging.getLogger(__name__)


class MetadataManager:
    """元数据管理器

    统一管理所有数据类型的元数据信息。

    Example:
        manager = MetadataManager('market.db')

        # 注册数据分类
        manager.add_category('macro', '宏观经济', 'GDP/CPI/PMI等宏观指标')

        # 注册数据源
        manager.add_data_source('tushare_macro', 'Tushare宏观', 'Tushare', 'monthly')

        # 注册数据字段
        manager.register_field('gdp_yoy', 'macro', 'GDP同比', 'numeric', '国内生产总值同比增长率', '%')

        # 查询数据字典
        df = manager.get_data_dictionary('macro')
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        """初始化元数据管理器

        Args:
            db_path: 数据库路径
        """
        self.db_path = db_path
        self.conn = DuckDBConnection(db_path, read_only=False)

    def add_category(self, category_id: str, category_name: str,
                    description: str = '', priority: int = 0) -> bool:
        """添加数据分类

        Args:
            category_id: 分类ID
            category_name: 分类名称
            description: 分类描述
            priority: 优先级

        Returns:
            是否添加成功
        """
        try:
            with self.conn as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO data_categories (category_id, category_name, description, priority)
                    VALUES (?, ?, ?, ?)
                """, [category_id, category_name, description, priority])
            logger.info(f"数据分类添加成功: {category_id}")
            return True
        except Exception as e:
            logger.error(f"数据分类添加失败: {e}")
            return False

    def get_categories(self) -> pd.DataFrame:
        """获取所有数据分类

        Returns:
            数据分类 DataFrame
        """
        try:
            with self.conn as conn:
                return conn.execute("SELECT * FROM data_categories ORDER BY priority").fetchdf()
        except Exception as e:
            logger.error(f"获取数据分类失败: {e}")
            return pd.DataFrame()

    def add_data_source(self, source_id: str, source_name: str,
                       provider: str = '', update_frequency: str = '',
                       contact_info: str = '') -> bool:
        """添加数据源

        Args:
            source_id: 数据源ID
            source_name: 数据源名称
            provider: 数据提供方
            update_frequency: 更新频率
            contact_info: 联系方式

        Returns:
            是否添加成功
        """
        try:
            with self.conn as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO data_sources (source_id, source_name, provider, update_frequency, contact_info)
                    VALUES (?, ?, ?, ?, ?)
                """, [source_id, source_name, provider, update_frequency, contact_info])
            logger.info(f"数据源添加成功: {source_id}")
            return True
        except Exception as e:
            logger.error(f"数据源添加失败: {e}")
            return False

    def get_data_sources(self, is_active: bool = True) -> pd.DataFrame:
        """获取数据源列表

        Args:
            is_active: 是否只返回活跃数据源

        Returns:
            数据源 DataFrame
        """
        try:
            with self.conn as conn:
                if is_active:
                    return conn.execute(
                        "SELECT * FROM data_sources WHERE is_active = TRUE ORDER BY source_id"
                    ).fetchdf()
                else:
                    return conn.execute("SELECT * FROM data_sources ORDER BY source_id").fetchdf()
        except Exception as e:
            logger.error(f"获取数据源失败: {e}")
            return pd.DataFrame()

    def register_field(self, field_id: str, category_id: str,
                      field_name: str, field_type: str,
                      description: str = '', unit: str = '',
                      source_id: str = '') -> bool:
        """注册数据字段

        Args:
            field_id: 字段ID
            category_id: 所属分类ID
            field_name: 字段名称
            field_type: 字段类型：numeric/string/date
            description: 字段描述
            unit: 单位
            source_id: 数据源ID

        Returns:
            是否注册成功
        """
        try:
            with self.conn as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO data_dictionary
                    (field_id, category_id, field_name, field_type, description, unit, source_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, [field_id, category_id, field_name, field_type, description, unit, source_id])
            logger.info(f"字段注册成功: {field_id}")
            return True
        except Exception as e:
            logger.error(f"字段注册失败: {e}")
            return False

    def get_data_dictionary(self, category_id: Optional[str] = None) -> pd.DataFrame:
        """获取数据字典

        Args:
            category_id: 分类ID过滤

        Returns:
            数据字典 DataFrame
        """
        try:
            with self.conn as conn:
                if category_id:
                    return conn.execute(
                        "SELECT * FROM data_dictionary WHERE category_id = ? ORDER BY field_id",
                        [category_id]
                    ).fetchdf()
                else:
                    return conn.execute("SELECT * FROM data_dictionary ORDER BY category_id, field_id").fetchdf()
        except Exception as e:
            logger.error(f"获取数据字典失败: {e}")
            return pd.DataFrame()

    def init_default_metadata(self) -> bool:
        """初始化默认元数据

        注册默认的数据分类、数据源和数据字段。

        Returns:
            是否初始化成功
        """
        try:
            # 注册数据分类
            categories = [
                ('price', '价格数据', '股票价格和成交量数据', 1),
                ('factor', '因子数据', '量化因子数据', 2),
                ('macro', '宏观经济', 'GDP/CPI/PMI等宏观指标', 3),
                ('sentiment', '舆情数据', '新闻和研报情感数据', 4),
                ('alternative', '另类数据', '产业链/卫星/互联网数据', 5),
            ]
            for cat in categories:
                self.add_category(*cat)

            # 注册数据源
            sources = [
                ('tushare', 'Tushare', 'Tushare Pro', 'daily', 'https://tushare.pro'),
                ('akshare', 'AKShare', 'AKShare', 'daily', 'https://akshare.akfamily.xyz'),
                ('eastmoney', '东方财富', '东方财富', 'realtime', 'https://eastmoney.com'),
                ('sina', '新浪财经', '新浪财经', 'realtime', 'https://finance.sina.com.cn'),
            ]
            for src in sources:
                self.add_data_source(*src)

            logger.info("默认元数据初始化完成")
            return True
        except Exception as e:
            logger.error(f"默认元数据初始化失败: {e}")
            return False
