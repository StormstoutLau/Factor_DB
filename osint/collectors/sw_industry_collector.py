"""申万行业指数采集器

抓取申万一级/二级/三级行业指数日线，按收盘价计算日收益率，
经 OSINTPipeline 自动路由写入 alternative_data（data_type='industry_return'）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from ..base import BaseCollector, OSINTEventType

logger = logging.getLogger(__name__)


class SWIndustryCollector(BaseCollector):
    """申万行业指数采集器"""

    meta = {
        'name': 'sw_industry',
        'summary': '申万行业指数日线（一级/二级/三级）→ 日收益率',
        'categories': ['industry', 'alternative', 'china'],
        'required_keys': [],
    }

    INFO_FN = {
        'first': 'sw_index_first_info',
        'second': 'sw_index_second_info',
        'third': 'sw_index_third_info',
    }

    def setup(self):
        try:
            import akshare as ak
            self._ak = ak
        except ImportError:
            logger.error("akshare 未安装，请运行: pip install akshare")
            self._error_state = True

    def transform_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        level = params.get('level') or params.get('indicator') or 'first'
        if level not in self.INFO_FN:
            logger.warning(f"未知行业级别: {level}，回退 first")
            level = 'first'

        codes = params.get('codes')
        if isinstance(codes, str):
            codes = [c.strip() for c in codes.split(',') if c.strip()]

        return {
            'level': level,
            'codes': codes or None,
            'start_date': params.get('start_date'),
        }

    def extract_data(self, query: Dict[str, Any]) -> Optional[pd.DataFrame]:
        if self._error_state:
            return None

        ak = getattr(self, '_ak', None)
        if ak is None:
            logger.error("AKShare 未初始化，无法抓取申万行业指数")
            return None

        info_fn = getattr(ak, self.INFO_FN[query['level']], None)
        if info_fn is None:
            logger.error(f"AKShare 缺少分类函数: {self.INFO_FN[query['level']]}")
            return None

        try:
            info = info_fn()
        except Exception as e:
            logger.error(f"申万行业分类获取失败: {e}")
            return None

        if info is None or info.empty:
            logger.warning("申万行业分类返回空数据")
            return None

        selected = query.get('codes')
        frames: List[pd.DataFrame] = []
        for _, row in info.iterrows():
            # 实测 index_hist_sw 只接受 6 位代码，带 .SI 后缀会 KeyError
            code = str(row.get('行业代码', '')).split('.')[0].strip()
            name = str(row.get('行业名称', ''))
            if not code or (selected and code not in selected):
                continue

            try:
                hist = ak.index_hist_sw(symbol=code, period='day')
            except Exception as e:
                logger.warning(f"行业指数 {code} 抓取失败，跳过: {e}")
                continue

            if hist is None or hist.empty:
                logger.warning(f"行业指数 {code} 返回空数据，跳过")
                continue

            hist = hist.copy()
            hist['entity_id'] = code
            hist['entity_name'] = name
            frames.append(hist)
            time.sleep(self.config.rate_limit)

        if not frames:
            logger.error("全部行业指数抓取失败")
            return None

        return pd.concat(frames, ignore_index=True)

    def transform_data(self, query: Dict[str, Any], raw_data: Any) -> pd.DataFrame:
        if raw_data is None or (isinstance(raw_data, pd.DataFrame) and raw_data.empty):
            return pd.DataFrame()

        df = raw_data.copy()
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values(['entity_id', '日期'])

        # 每个指数的首行没有前收盘，pct_change 为 NaN，随后统一丢弃
        returns = df.groupby('entity_id')['收盘'].pct_change()

        result = pd.DataFrame({
            'trade_date': df['日期'].dt.date,
            'data_type': 'industry_return',
            'data_subtype': query['level'],
            'entity_type': 'industry',
            'entity_id': df['entity_id'],
            'entity_name': df['entity_name'],
            'value': returns,
            'data_quality': 100,
            'source_id': 'sw_industry',
        })
        result = result.dropna(subset=['value'])

        # start_date 在收益计算之后过滤，保证首日仍有前收盘可算
        start_date = query.get('start_date')
        if start_date:
            result = result[
                pd.to_datetime(result['trade_date']) >= pd.Timestamp(start_date)
            ]

        return result.reset_index(drop=True)

    def produced_events(self) -> List[OSINTEventType]:
        return [OSINTEventType.ALTERNATIVE_DATA]
