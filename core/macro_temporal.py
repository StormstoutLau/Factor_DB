"""宏观数据时间语义规则

集中维护 AKShare 12 个宏观指标的统计期/发布日期映射，供迁移、Loader
与查询层共用，避免日期口径在多处漂移。

三个日期概念：
    - ``trade_date``：采集器给出的原始日期（可能是发布日、统计期起点或季末）
    - ``period_date``：统计期（月度=月末，季度=季末）
    - ``publish_date``：首次可得日（发布日期）

规则来源见 ``docs/research/macro_temporal_semantics_pit_research.md``。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class MacroTemporalRule:
    """单个宏观指标的日期语义规则"""

    semantics: str
    """release_date / period_start / period_end / observation"""

    frequency: str
    """monthly / quarterly / daily"""

    period_offset: str
    """previous_month / current_month_end / previous_quarter / current_quarter_end"""

    publish_rule: Optional[str]
    """nbs_cpi_ppi / nbs_pmi / pbc_m2 / safe_fx / customs_trade / nbs_gdp / lpr / none"""

    publish_date_quality: str
    """exact / estimated / unknown"""


MACRO_TEMPORAL_RULES: Dict[str, MacroTemporalRule] = {
    'CPI_MONTHLY': MacroTemporalRule(
        'release_date', 'monthly', 'previous_month', 'nbs_cpi_ppi', 'exact'
    ),
    'CPI_YEARLY': MacroTemporalRule(
        'release_date', 'monthly', 'previous_month', 'nbs_cpi_ppi', 'exact'
    ),
    'PPI_YEARLY': MacroTemporalRule(
        'release_date', 'monthly', 'previous_month', 'nbs_cpi_ppi', 'exact'
    ),
    'M2_YEARLY': MacroTemporalRule(
        'release_date', 'monthly', 'previous_month', 'pbc_m2', 'exact'
    ),
    'GDP_YEARLY': MacroTemporalRule(
        'release_date', 'quarterly', 'previous_quarter', 'nbs_gdp', 'exact'
    ),
    'PMI_MANUFACTURING': MacroTemporalRule(
        'period_start', 'monthly', 'current_month_end', 'nbs_pmi', 'estimated'
    ),
    'PPI_MONTHLY': MacroTemporalRule(
        'period_start', 'monthly', 'current_month_end', 'nbs_cpi_ppi', 'estimated'
    ),
    'M2_MONTHLY': MacroTemporalRule(
        'period_start', 'monthly', 'current_month_end', 'pbc_m2', 'estimated'
    ),
    'FX_RESERVES': MacroTemporalRule(
        'period_start', 'monthly', 'current_month_end', 'safe_fx', 'estimated'
    ),
    'IMPORT_EXPORT': MacroTemporalRule(
        'period_start', 'monthly', 'current_month_end', 'customs_trade', 'estimated'
    ),
    'GDP_QUARTERLY': MacroTemporalRule(
        'period_end', 'quarterly', 'current_quarter_end', 'nbs_gdp', 'estimated'
    ),
    'INTEREST_RATE': MacroTemporalRule(
        'observation', 'daily', 'current_month_end', 'lpr', 'exact'
    ),
}


def _month_end(value: date) -> date:
    """返回 value 所在自然月的最后一天"""
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def _previous_month_end(value: date) -> date:
    """返回 value 所在月的上一个月的最后一天"""
    first_day = date(value.year, value.month, 1)
    return first_day - timedelta(days=1)


def _previous_quarter_end(value: date) -> date:
    """返回 value 所在季度的上一个季度的最后一天"""
    quarter_first_month = ((value.month - 1) // 3) * 3 + 1
    return date(value.year, quarter_first_month, 1) - timedelta(days=1)


def _next_month_day(value: date, day: int) -> date:
    """返回 value 的下一个自然月的指定日"""
    if value.month == 12:
        year = value.year + 1
        month = 1
    else:
        year = value.year
        month = value.month + 1
    return date(year, month, day)


def resolve_period_date(indicator_id: str, raw_date: date,
                        publish_date: Optional[date] = None) -> date:
    """由原始采集日期推导统计期

    未知指标返回原始日期本身（不做月末平移，避免改变既有查询输出）。
    """
    rule = MACRO_TEMPORAL_RULES.get(indicator_id)
    if rule is None:
        return raw_date

    if rule.semantics == 'release_date':
        if raw_date.day == 1:
            # 历史上五列发布日历早期以月首记录统计期
            return _month_end(raw_date)
        if rule.frequency == 'quarterly':
            return _previous_quarter_end(raw_date)
        return _previous_month_end(raw_date)

    if rule.semantics == 'period_end':
        return raw_date

    # period_start / observation 统一到月末
    return _month_end(raw_date)


def estimate_publish_date(indicator_id: str, period_date: date) -> Optional[date]:
    """由统计期估算首次可得日

    仅用于统计期起点/期末的指标；发布日型指标应直接使用原始日期。
    """
    rule = MACRO_TEMPORAL_RULES.get(indicator_id)
    if rule is None or rule.publish_rule is None:
        return None

    if rule.publish_rule == 'nbs_cpi_ppi':
        return _next_month_day(period_date, 9)
    if rule.publish_rule == 'nbs_pmi':
        if period_date.month == 2:
            # 春节例外：2 月 PMI 于 3 月 4 日发布
            return _next_month_day(period_date, 4)
        return _month_end(period_date)
    if rule.publish_rule == 'pbc_m2':
        return _next_month_day(period_date, 13)
    if rule.publish_rule == 'safe_fx':
        return _next_month_day(period_date, 7)
    if rule.publish_rule == 'customs_trade':
        return _next_month_day(period_date, 10)
    if rule.publish_rule == 'nbs_gdp':
        return period_date + timedelta(days=17)
    return None


def is_legacy_release_series(indicator_id: str, raw_date: date) -> bool:
    """是否为发布日型指标的早期月首记录"""
    rule = MACRO_TEMPORAL_RULES.get(indicator_id)
    return bool(rule and rule.semantics == 'release_date' and raw_date.day == 1)


def resolve_publish_info(indicator_id: str, raw_date: date,
                         period_date: Optional[date] = None
                         ) -> Tuple[Optional[date], str]:
    """推导 (首次可得日, 质量标记)

    - 发布日型：原始日期即发布日期（exact）；早期月首记录为 unknown
    - 观测日型：原始日期即观测/发布日（exact）
    - 统计期起点/期末：按规则估算（estimated）
    - 未知指标：unknown
    """
    rule = MACRO_TEMPORAL_RULES.get(indicator_id)
    if rule is None:
        return None, 'unknown'

    if rule.semantics == 'release_date':
        if raw_date.day == 1:
            return None, 'unknown'
        return raw_date, 'exact'

    if rule.semantics == 'observation':
        return raw_date, 'exact'

    resolved_period = period_date or resolve_period_date(indicator_id, raw_date)
    return estimate_publish_date(indicator_id, resolved_period), 'estimated'


__all__ = [
    'MacroTemporalRule',
    'MACRO_TEMPORAL_RULES',
    'resolve_period_date',
    'estimate_publish_date',
    'is_legacy_release_series',
    'resolve_publish_info',
]
