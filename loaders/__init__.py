"""
数据加载模块

提供各类数据的加载功能。
"""

from .base import BaseLoader, LoaderConfig
from .daily_loader import DailyLoader

# Level1Loader requires pyarrow - lazy import
try:
    from .level1_loader import Level1Loader
except ImportError:
    Level1Loader = None  # type: ignore

from .macro_loader import MacroLoader
from .news_loader import NewsLoader
from .alternative_loader import AlternativeLoader
from .csmar_loader import CSMARLoader

__all__ = [
    'BaseLoader', 'LoaderConfig',
    'Level1Loader', 'DailyLoader',
    'MacroLoader', 'NewsLoader', 'AlternativeLoader',
    'CSMARLoader'
]
