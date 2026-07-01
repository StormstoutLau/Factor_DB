"""
查询缓存模块

提供 SQL 查询结果缓存，减少重复查询开销。
"""

import hashlib
import time
from typing import Optional

import pandas as pd


class QueryCache:
    """SQL 查询结果缓存

    基于 MD5 哈希的查询结果缓存，支持 TTL 过期和 LRU 淘汰。

    Attributes:
        max_size: 最大缓存条目数
        ttl: 缓存过期时间（秒）
    """

    def __init__(self, max_size: int = 1000, ttl: int = 300):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, sql: str) -> Optional[pd.DataFrame]:
        """从缓存获取查询结果

        Args:
            sql: 查询 SQL

        Returns:
            缓存的 DataFrame 副本，未命中返回 None
        """
        key = hashlib.md5(sql.encode()).hexdigest()
        if key in self._cache:
            ts, df = self._cache[key]
            if time.time() - ts < self.ttl:
                self._hits += 1
                return df.copy()
            else:
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
        if len(self._cache) >= self.max_size:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]
        self._cache[key] = (time.time(), df.copy())

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