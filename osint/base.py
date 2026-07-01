"""
OSINT 数据收集器基类

融合设计模式：
    - SpiderFoot: 事件驱动、模块注册/发现
    - OpenBB: Fetcher TET 管道 (Transform → Extract → Transform)
    - AKShare: 请求重试、反爬策略
    - Recon-ng: Key 管理、数据库集成
"""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

logger = logging.getLogger(__name__)


class OSINTEventType(str, Enum):
    """OSINT 事件类型"""
    MACRO_ECONOMIC = "MACRO_ECONOMIC"
    GOVERNMENT_POLICY = "GOVERNMENT_POLICY"
    NEWS_SENTIMENT = "NEWS_SENTIMENT"
    TRADE_DATA = "TRADE_DATA"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    ALTERNATIVE_DATA = "ALTERNATIVE_DATA"
    ERROR = "ERROR"


class OSINTEvent:
    """OSINT 事件对象（参考 SpiderFoot 事件架构）"""

    def __init__(
        self,
        event_type: OSINTEventType,
        data: Any,
        source_module: str,
        source_event: Optional['OSINTEvent'] = None,
        confidence: int = 100,
        metadata: Optional[Dict] = None,
    ):
        self.event_type = event_type
        self.data = data
        self.source_module = source_module
        self.source_event = source_event
        self.confidence = confidence
        self.metadata = metadata or {}
        self.timestamp = time.time()

    def __repr__(self):
        return f"OSINTEvent({self.event_type}, source={self.source_module})"


@dataclass
class CollectorConfig:
    """收集器配置"""
    source_id: str = ''
    source_name: str = ''
    db_path: str = 'factor_db.duckdb'
    rate_limit: float = 1.0
    max_retries: int = 3
    timeout: int = 30
    base_delay: float = 1.0
    user_agent: str = 'Factor_DB-OSINT/1.0'
    proxy: Optional[str] = None
    api_keys: Dict[str, str] = field(default_factory=dict)
    require_keys: List[str] = field(default_factory=list)


class BaseCollector(ABC):
    """OSINT 收集器基类

    融合 SpiderFoot 的模块架构和 OpenBB 的 Fetcher TET 管道：
        - setup(): 初始化（参考 SpiderFoot）
        - transform_query(): 转换查询参数（参考 OpenBB Fetcher）
        - extract_data(): 提取原始数据（参考 OpenBB Fetcher）
        - transform_data(): 转换数据格式（参考 OpenBB Fetcher）
        - handle_event(): 事件处理（参考 SpiderFoot）
        - watched_events()/produced_events(): 事件声明（参考 SpiderFoot）

    Example:
        class WorldBankCollector(BaseCollector):
            def setup(self):
                self.base_url = "https://api.worldbank.org/v2"

            def transform_query(self, params):
                return {"format": "json", "per_page": 100, **params}

            def extract_data(self, query):
                return self.request_get(f"{self.base_url}/country/CN/indicator/NY.GDP.MKTP.CD", params=query)

            def transform_data(self, query, raw_data):
                return pd.DataFrame([...])

            def produced_events(self):
                return [OSINTEventType.MACRO_ECONOMIC]
    """

    meta = {
        'name': '',
        'summary': '',
        'categories': [],
        'required_keys': [],
    }

    def __init__(self, config: Optional[CollectorConfig] = None):
        self.config = config or CollectorConfig()
        self._listener_modules: List['BaseCollector'] = []
        self._collected_count = 0
        self._error_state = False
        self._stop = False
        self._setup_done = False
        self.opts: Dict[str, Any] = {}

        self._check_required_keys()
        self.setup()
        self._setup_done = True

    def _check_required_keys(self):
        for key in self.config.require_keys:
            if not self.config.api_keys.get(key):
                logger.warning(f"'{key}' 未设置，模块 {self.__class__.__name__} 可能无法正常运行")

    @abstractmethod
    def setup(self):
        """初始化收集器（参考 SpiderFoot setup）"""
        pass

    @abstractmethod
    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """转换查询参数（参考 OpenBB Fetcher.transform_query）"""
        pass

    @abstractmethod
    def extract_data(self, query: Dict[str, Any]) -> Any:
        """提取原始数据（参考 OpenBB Fetcher.extract_data）"""
        pass

    @abstractmethod
    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        """转换数据格式（参考 OpenBB Fetcher.transform_data）"""
        pass

    def collect(self, params: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        """执行完整的 TET 管道：Transform → Extract → Transform

        Args:
            params: 查询参数

        Returns:
            标准化后的 DataFrame
        """
        if self._error_state or self._stop:
            logger.warning(f"收集器 {self.__class__.__name__} 处于错误/停止状态")
            return pd.DataFrame()

        try:
            query = self.transform_query(params or {})
            raw_data = self.extract_data(query)
            result = self.transform_data(query, raw_data)
            self._collected_count += len(result)

            event = OSINTEvent(
                event_type=self.produced_events()[0] if self.produced_events() else OSINTEventType.ALTERNATIVE_DATA,
                data=result,
                source_module=self.__class__.__name__,
            )
            self.notify_listeners(event)

            return result
        except Exception as e:
            logger.error(f"收集器 {self.__class__.__name__} 执行失败: {e}")
            self._error_state = True
            error_event = OSINTEvent(
                event_type=OSINTEventType.ERROR,
                data=str(e),
                source_module=self.__class__.__name__,
            )
            self.notify_listeners(error_event)
            return pd.DataFrame()

    def watched_events(self) -> List[OSINTEventType]:
        """关注的事件类型（参考 SpiderFoot watchedEvents）"""
        return []

    def produced_events(self) -> List[OSINTEventType]:
        """产出的事件类型（参考 SpiderFoot producedEvents）"""
        return []

    def handle_event(self, event: OSINTEvent) -> None:
        """处理事件（参考 SpiderFoot handleEvent）"""
        pass

    def register_listener(self, listener: 'BaseCollector') -> None:
        """注册监听模块（参考 SpiderFoot registerListener）"""
        self._listener_modules.append(listener)

    def notify_listeners(self, event: OSINTEvent) -> None:
        """通知监听模块（参考 SpiderFoot notifyListeners）"""
        for listener in self._listener_modules:
            if event.event_type in listener.watched_events() or not listener.watched_events():
                try:
                    listener.handle_event(event)
                except Exception as e:
                    logger.error(f"监听模块 {listener.__class__.__name__} 处理事件失败: {e}")

    def check_for_stop(self) -> bool:
        """检查是否应停止（参考 SpiderFoot checkForStop）"""
        return self._stop or self._error_state

    def reset_error(self) -> None:
        """重置错误状态，允许恢复收集"""
        self._error_state = False

    def stop(self) -> None:
        """停止收集器"""
        self._stop = True

    def get_collected_count(self) -> int:
        """获取已收集记录数"""
        return self._collected_count

    def reset_counter(self) -> None:
        """重置计数器"""
        self._collected_count = 0

    def _request(
        self,
        method: str,
        url: str,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        **kwargs,
    ) -> requests.Response:
        """带重试的 HTTP 请求（提取公共逻辑）

        Args:
            method: HTTP 方法 ('GET' 或 'POST')
            url: 请求 URL
            timeout: 超时时间
            max_retries: 最大重试次数
            **kwargs: 传递给 session.get/post 的参数

        Returns:
            Response 对象
        """
        _timeout = timeout or self.config.timeout
        _max_retries = max_retries or self.config.max_retries
        last_exception = None

        for attempt in range(_max_retries):
            try:
                with requests.Session() as session:
                    adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1)
                    session.mount("http://", adapter)
                    session.mount("https://", adapter)
                    session.headers.update({"User-Agent": self.config.user_agent})
                    if self.config.proxy:
                        session.proxies.update({"http": self.config.proxy, "https": self.config.proxy})

                    if method.upper() == 'GET':
                        response = session.get(url, timeout=_timeout, **kwargs)
                    else:
                        response = session.post(url, timeout=_timeout, **kwargs)
                    response.raise_for_status()

                    self._rate_limit()
                    return response

            except (requests.RequestException, ValueError) as e:
                last_exception = e
                if attempt < _max_retries - 1:
                    delay = self.config.base_delay * (2 ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(f"{method} 请求失败 (尝试 {attempt + 1}/{_max_retries}): {e}")
                    time.sleep(delay)

        raise last_exception

    def request_get(
        self,
        url: str,
        params: Optional[Dict] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> requests.Response:
        """带重试的 HTTP GET 请求（参考 AKShare request_with_retry）"""
        return self._request('GET', url, timeout=timeout, max_retries=max_retries, params=params)

    def request_post(
        self,
        url: str,
        data: Optional[Dict] = None,
        json: Optional[Dict] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
    ) -> requests.Response:
        """带重试的 HTTP POST 请求"""
        return self._request('POST', url, timeout=timeout, max_retries=max_retries, data=data, json=json)

    def _rate_limit(self):
        """速率限制"""
        if self.config.rate_limit > 0:
            time.sleep(self.config.rate_limit + random.uniform(0, 0.5))

    def asdict(self) -> Dict[str, Any]:
        """返回模块信息（参考 SpiderFoot asdict）"""
        return {
            'name': self.meta.get('name', self.__class__.__name__),
            'summary': self.meta.get('summary', ''),
            'categories': self.meta.get('categories', []),
            'required_keys': self.meta.get('required_keys', []),
            'produces': [e.value for e in self.produced_events()],
            'consumes': [e.value for e in self.watched_events()],
            'collected_count': self._collected_count,
            'error_state': self._error_state,
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.error(f"收集器异常: {exc_val}")
