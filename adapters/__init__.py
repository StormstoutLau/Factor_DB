"""
适配器模块

提供与外部系统的兼容层。
"""

from .pandas_adapter import PandasAdapter
from .engine_adapter import FactorTradingAdapter, DataLoaderV3Adapter

__all__ = ['PandasAdapter', 'FactorTradingAdapter', 'DataLoaderV3Adapter']
