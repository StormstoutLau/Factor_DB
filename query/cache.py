"""
查询缓存模块

提供 SQL 查询结果缓存，减少重复查询开销。

缓存策略：
    - LRU 淘汰：命中时刷新条目位置，容量超限淘汰最久未使用条目
    - TTL 过期：条目写入超过 ttl 秒后失效
    - 写后失效：数据版本号在写入时递增，版本不符的条目视为失效
"""

import hashlib
import time
from collections import OrderedDict
from typing import Optional

import pandas as pd

from Factor_DB.utils.data_version import current_version


class QueryCache:
    """SQL 查询结果缓存

    基于 MD5 哈希的查询结果缓存，支持 TTL 过期、LRU 淘汰与写后失效。

    Attributes:
        max_size: 最大缓存条目数
        ttl: 缓存过期时间（秒）
    """

    def __init__(self, max_size: int = 1000, ttl: int = 300):
        self.max_size = max_size
        self.ttl = ttl
        # key -> (写入时间, 写入时数据版本, DataFrame)
        self._cache: "OrderedDict[str, tuple[float, int, pd.DataFrame]]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, sql: str) -> Optional[pd.DataFrame]:
        """从缓存获取查询结果

        Args:
            sql: 查询 SQL

        Returns:
            缓存的 DataFrame 副本，未命中/过期/数据已变更返回 None
        """
        key = hashlib.md5(sql.encode()).hexdigest()
        entry = self._cache.get(key)
        if entry is not None:
            ts, version, df = entry
            if time.time() - ts < self.ttl and version == current_version():
                self._cache.move_to_end(key)  # 命中即最近使用
                self._hits += 1
                return df.copy()
            del self._cache[key]
        self._misses += 1
        return None

    def put(self, sql: str, df: pd.DataFrame) -> None:
        """将查询结果存入缓存

        Args:
            sql: 查询 SQL
            df: 查询结果
        """
        key = hashlib.md5(sql.encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
        elif len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)  # 淘汰最久未使用
        self._cache[key] = (time.time(), current_version(), df.copy())

    def clear(self) -> None:
        """清空缓存"""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    def stats(self) -> dict:
        """获取缓存统计

        Returns:
            包含缓存统计信息的字典
        """
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            'size': len(self._cache),
            'max_size': self.max_size,
            'hits': self._hits,
            'misses': self._misses,
            'hit_rate': round(hit_rate, 4),
            'ttl': self.ttl,
        }

    def __len__(self) -> int:
        return len(self._cache)
