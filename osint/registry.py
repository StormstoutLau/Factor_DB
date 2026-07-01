"""
OSINT 收集器注册中心

实现模块的自动发现、注册和管理。
参考 SpiderFoot 的模块发现机制和 Recon-ng 的模块加载机制。
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from pathlib import Path
from typing import Dict, List, Optional, Type

from .base import BaseCollector, CollectorConfig, OSINTEventType

logger = logging.getLogger(__name__)


class CollectorRegistry:
    """收集器注册中心

    Example:
        registry = CollectorRegistry()

        # 自动发现并注册所有收集器
        registry.auto_discover()

        # 查看已注册的收集器
        print(registry.list_collectors())

        # 获取特定类型的收集器
        macro_collectors = registry.get_by_event_type(OSINTEventType.MACRO_ECONOMIC)
    """

    def __init__(self):
        self._collectors: Dict[str, Type[BaseCollector]] = {}
        self._instances: Dict[str, BaseCollector] = {}

    def register(self, collector_class: Type[BaseCollector]) -> None:
        """注册收集器类

        Args:
            collector_class: 收集器类（非实例）
        """
        source_id = collector_class.meta.get('name', collector_class.__name__)
        if source_id in self._collectors:
            logger.warning(f"收集器已存在，覆盖: {source_id}")
        self._collectors[source_id] = collector_class
        logger.debug(f"注册收集器: {source_id}")

    def unregister(self, source_id: str) -> None:
        """注销收集器"""
        self._collectors.pop(source_id, None)
        self._instances.pop(source_id, None)

    def get(self, source_id: str, config: Optional[CollectorConfig] = None) -> Optional[BaseCollector]:
        """获取收集器实例（懒加载单例）

        Args:
            source_id: 收集器 ID
            config: 收集器配置

        Returns:
            收集器实例
        """
        if source_id not in self._collectors:
            logger.error(f"未注册的收集器: {source_id}")
            return None

        if source_id not in self._instances:
            collector_class = self._collectors[source_id]
            _config = config or CollectorConfig(source_id=source_id)
            self._instances[source_id] = collector_class(_config)

        return self._instances[source_id]

    def get_all(self, config: Optional[CollectorConfig] = None) -> Dict[str, BaseCollector]:
        """获取所有收集器实例"""
        return {sid: self.get(sid, config) for sid in self._collectors}

    def get_by_event_type(self, event_type: OSINTEventType) -> Dict[str, Type[BaseCollector]]:
        """按事件类型筛选收集器（不实例化类）"""
        result = {}
        for sid, cls in self._collectors.items():
            instance = self._instances.get(sid)
            if instance and event_type in instance.produced_events():
                result[sid] = cls
                continue
            if hasattr(cls, 'produced_events'):
                try:
                    temp = cls.__new__(cls)
                    if event_type in temp.produced_events():
                        result[sid] = cls
                except Exception:
                    pass
        return result

    def get_registered_ids(self) -> List[str]:
        """获取所有已注册的收集器 ID"""
        return list(self._collectors.keys())

    def list_collectors(self) -> List[Dict[str, any]]:
        """列出所有已注册的收集器信息"""
        return [
            {
                'source_id': sid,
                'name': cls.meta.get('name', cls.__name__),
                'summary': cls.meta.get('summary', ''),
                'categories': cls.meta.get('categories', []),
                'required_keys': cls.meta.get('required_keys', []),
            }
            for sid, cls in self._collectors.items()
        ]

    def auto_discover(self, package_path: Optional[str] = None) -> int:
        """自动发现并注册收集器

        扫描 collectors 子包中的所有模块，自动注册 BaseCollector 子类。

        Args:
            package_path: 收集器包路径，默认为 osint.collectors

        Returns:
            注册的收集器数量
        """
        if package_path is None:
            package_path = str(Path(__file__).parent / 'collectors')

        count = 0
        package_dir = Path(package_path)

        if not package_dir.exists():
            logger.warning(f"收集器目录不存在: {package_path}")
            return 0

        for module_info in pkgutil.iter_modules([str(package_dir)]):
            try:
                module = importlib.import_module(f'.collectors.{module_info.name}', package='osint')
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (isinstance(attr, type)
                            and issubclass(attr, BaseCollector)
                            and attr is not BaseCollector):
                        self.register(attr)
                        count += 1
            except Exception as e:
                logger.error(f"加载收集器模块失败 {module_info.name}: {e}")

        logger.info(f"自动发现完成: 注册 {count} 个收集器")
        return count

    def __len__(self):
        return len(self._collectors)

    def __contains__(self, source_id: str):
        return source_id in self._collectors
