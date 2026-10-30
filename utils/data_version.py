"""
数据版本计数器

用于查询缓存失效：任何写操作递增版本号，版本不符的缓存条目视为失效。
版本号仅存在于当前进程内，不做跨进程共享。
"""

from __future__ import annotations

import threading

_version = 0
_lock = threading.Lock()


def current_version() -> int:
    """获取当前数据版本"""
    with _lock:
        return _version


def bump_version() -> int:
    """递增数据版本并返回新值"""
    global _version
    with _lock:
        _version += 1
        return _version
