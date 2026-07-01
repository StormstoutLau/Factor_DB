"""
股票代码标准化工具

统一将各种格式的股票代码转换为6位纯数字字符串。
"""

from __future__ import annotations


def normalize_stock_code(code: str | int | float) -> str:
    """标准化股票代码为6位纯数字字符串

    支持输入格式：
        - 整数: 1, 600001, 300001
        - 字符串: '000001', '000001.SZ', '1', '600000.SH'

    Args:
        code: 原始股票代码

    Returns:
        6位纯数字字符串，如 '000001'

    Examples:
        >>> normalize_stock_code(1)
        '000001'
        >>> normalize_stock_code('000001.SZ')
        '000001'
        >>> normalize_stock_code(600000)
        '600000'
    """
    if isinstance(code, (int, float)):
        if isinstance(code, float) and code != code:  # NaN check
            return ''
        code = str(int(code))
    else:
        code = str(code).strip()

    # 去除市场后缀 (.SZ, .SH, .BJ 等)
    if '.' in code:
        code = code.split('.')[0]

    # 补齐前导零到6位
    return code.zfill(6)


def is_index_code(code: str) -> bool:
    """判断是否为指数代码（带后缀的）

    Args:
        code: 原始代码字符串

    Returns:
        是否为指数代码
    """
    code = str(code).strip()
    return '.' in code and any(
        code.upper().endswith(suffix)
        for suffix in ('.SH', '.SZ', '.BJ')
    )


def get_market(code: str) -> str:
    """根据6位代码判断所属市场

    Args:
        code: 6位股票代码

    Returns:
        市场代码: 'SH', 'SZ', 'BJ'
    """
    if not code or str(code).strip() == '':
        return ''
    code = normalize_stock_code(code)
    if len(code) != 6:
        return ''
    if not code.isdigit():
        return ''
    if code.startswith(('6', '68')):
        return 'SH'
    elif code.startswith(('8', '9')):
        return 'BJ'
    else:
        return 'SZ'