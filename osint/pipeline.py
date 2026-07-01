"""
OSINT 数据管道

编排收集 → 处理 → 存储的完整流程。
参考 SpiderFoot 的扫描引擎和 Recon-ng 的工作流。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd

from core.connection import DuckDBConnection
from core.metadata_manager import MetadataManager
from .base import BaseCollector, CollectorConfig, OSINTEvent, OSINTEventType
from .registry import CollectorRegistry

logger = logging.getLogger(__name__)


class OSINTPipeline:
    """OSINT 数据管道

    Example:
        pipeline = OSINTPipeline('factor_db.duckdb')
        pipeline.auto_discover()

        # 运行指定收集器
        results = pipeline.run(['worldbank', 'akshare_macro'])

        # 运行所有收集器
        results = pipeline.run_all()
    """

    def __init__(self, db_path: str = 'factor_db.duckdb'):
        self.db_path = db_path
        self.registry = CollectorRegistry()
        self.metadata = MetadataManager(db_path)
        self._event_log: List[OSINTEvent] = []

    def register_collector(self, collector_class: type) -> None:
        """注册收集器"""
        self.registry.register(collector_class)

    def auto_discover(self) -> int:
        """自动发现收集器"""
        return self.registry.auto_discover()

    def run(
        self,
        source_ids: List[str],
        params: Optional[Dict[str, Dict[str, Any]]] = None,
        config: Optional[CollectorConfig] = None,
    ) -> Dict[str, int]:
        """运行指定收集器

        Args:
            source_ids: 收集器 ID 列表
            params: 各收集器的查询参数 {source_id: {key: value}}
            config: 收集器配置

        Returns:
            各收集器加载记录数
        """
        results = {}
        params = params or {}

        for source_id in source_ids:
            collector = self.registry.get(source_id, config)
            if collector is None:
                logger.warning(f"跳过未注册的收集器: {source_id}")
                results[source_id] = -1
                continue

            if collector.check_for_stop():
                logger.warning(f"收集器 {source_id} 处于停止状态，跳过")
                results[source_id] = -1
                continue

            try:
                logger.info(f"开始收集: {source_id}")
                collector_params = params.get(source_id, {})
                df = collector.collect(collector_params)

                if df is not None and not df.empty:
                    count = self._store(df, source_id)
                    results[source_id] = count
                    logger.info(f"收集完成: {source_id}, {count} 条记录")
                else:
                    results[source_id] = 0
                    logger.info(f"收集完成: {source_id}, 无数据")

                if collector._error_state:
                    self._event_log.append(OSINTEvent(
                        event_type=OSINTEventType.ERROR,
                        data='collector error state',
                        source_module=source_id,
                    ))
                else:
                    self._event_log.append(OSINTEvent(
                        event_type=collector.produced_events()[0] if collector.produced_events() else OSINTEventType.ALTERNATIVE_DATA,
                        data=df,
                        source_module=source_id,
                    ))

            except Exception as e:
                logger.error(f"收集器 {source_id} 运行失败: {e}")
                results[source_id] = -1
                self._event_log.append(OSINTEvent(
                    event_type=OSINTEventType.ERROR,
                    data=str(e),
                    source_module=source_id,
                ))

        return results

    def run_all(
        self,
        params: Optional[Dict[str, Dict[str, Any]]] = None,
        config: Optional[CollectorConfig] = None,
    ) -> Dict[str, int]:
        """运行所有已注册的收集器"""
        source_ids = self.registry.get_registered_ids()
        return self.run(source_ids, params, config)

    def run_by_event_type(
        self,
        event_type: OSINTEventType,
        params: Optional[Dict[str, Dict[str, Any]]] = None,
        config: Optional[CollectorConfig] = None,
    ) -> Dict[str, int]:
        """按事件类型运行收集器"""
        collectors = self.registry.get_by_event_type(event_type)
        source_ids = list(collectors.keys())
        return self.run(source_ids, params, config)

    def _store(self, df: pd.DataFrame, source_id: str) -> int:
        """将收集的数据存储到 DuckDB

        根据数据内容自动路由到对应的加载器：
            - 宏观数据 → macro_data 表
            - 舆情数据 → news_sentiment 表
            - 其他数据 → alternative_data 表
        """
        from loaders.macro_loader import MacroLoader
        from loaders.news_loader import NewsLoader
        from loaders.alternative_loader import AlternativeLoader

        data_type = self._detect_data_type(df, source_id)

        from loaders.base import LoaderConfig
        loader_config = LoaderConfig(db_path=self.db_path)

        try:
            if data_type == 'macro':
                loader = MacroLoader(loader_config)
                count = loader.load(df)
            elif data_type == 'news':
                loader = NewsLoader(loader_config)
                count = loader.load(df, data_type='news')
            elif data_type == 'report':
                loader = NewsLoader(loader_config)
                count = loader.load(df, data_type='report')
            else:
                loader = AlternativeLoader(loader_config)
                count = loader.load(df)

            # 更新元数据
            self._update_source_metadata(source_id, count)
            return count

        except Exception as e:
            logger.error(f"数据存储失败 {source_id}: {e}")
            return 0

    def _detect_data_type(self, df: pd.DataFrame, source_id: str) -> str:
        """检测数据类型

        根据列名特征判断数据应存储到哪个表。
        """
        cols = set(df.columns.str.lower())

        macro_keywords = {'indicator_id', 'indicator', '指标', 'value_type', 'trade_date'}
        news_keywords = {'news_id', 'title', 'sentiment_score', 'sentiment_label', 'publish_date'}
        report_keywords = {'report_id', 'analyst', 'brokerage', 'rating_change'}

        if cols & macro_keywords and 'stock_code' not in cols:
            return 'macro'
        if cols & report_keywords:
            return 'report'
        if cols & news_keywords:
            return 'news'
        return 'alternative'

    def _update_source_metadata(self, source_id: str, record_count: int) -> None:
        """更新数据源元数据"""
        try:
            self.metadata.add_data_source(
                source_id=f"osint_{source_id}",
                source_name=source_id,
                provider="Factor_DB OSINT",
                update_frequency="on_demand",
            )
        except Exception as e:
            logger.warning(f"元数据更新失败: {e}")

    def get_event_log(self) -> List[OSINTEvent]:
        """获取事件日志"""
        return self._event_log

    def get_status(self) -> Dict[str, Any]:
        """获取管道状态"""
        return {
            'registered_collectors': len(self.registry),
            'collector_list': self.registry.list_collectors(),
            'event_log_count': len(self._event_log),
        }
