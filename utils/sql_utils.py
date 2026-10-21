"""
SQL 构造安全工具

职责：
    - 值参数绑定（DuckDB 命名参数 $name）
    - 标识符白名单校验与双引号转义
    - 不能绑定位置的字符串字面量转义

依据：
    - DuckDB Python DBAPI「Named Parameters」
    - DuckDB「Keywords and Identifiers」：双引号转义用重复双引号
    - DuckDB「Literal Types」：单引号转义用 ''
    - OWASP SQL Injection Prevention Cheat Sheet（标识符需 allow-list）
"""

from __future__ import annotations

import re
from typing import Any, Sequence

# 标识符白名单：字母/下划线开头，后续字母、数字、下划线
_IDENTIFIER_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def bind(params: dict[str, Any], name: str, value: Any) -> str:
    """绑定命名参数并返回占位符

    Args:
        params: 参数字典（会被原地修改）
        name: 参数名（不含 $）
        value: 参数值

    Returns:
        '$name'

    Raises:
        ValueError: 同名参数已绑定不同值
    """
    if name in params and params[name] != value:
        raise ValueError(f"参数 {name} 已绑定不同值，禁止重复覆盖")
    params[name] = value
    return f'${name}'


def bind_in(params: dict[str, Any], prefix: str, values: Sequence[Any]) -> str:
    """绑定一组值，返回逗号分隔的占位符列表

    Args:
        params: 参数字典（会被原地修改）
        prefix: 参数名前缀
        values: 值序列（不可为空）

    Returns:
        '$prefix_0, $prefix_1, ...'

    Raises:
        ValueError: values 为空
    """
    if not values:
        raise ValueError(f"bind_in({prefix}) 需要至少一个值")
    placeholders = [bind(params, f'{prefix}_{i}', v) for i, v in enumerate(values)]
    return ', '.join(placeholders)


def quote_identifier(name: str) -> str:
    """双引号引用标识符，内嵌双引号重复转义

    Args:
        name: 列名/表名（允许任意字符）

    Returns:
        形如 '"factor_name"'；含双引号时形如 '"a""b"'
    """
    escaped = str(name).replace('"', '""')
    return f'"{escaped}"'


def validate_identifier(name: str, label: str = 'identifier') -> str:
    """校验标识符是否匹配白名单

    Args:
        name: 待校验标识符
        label: 错误信息中的说明

    Returns:
        原值

    Raises:
        ValueError: 不匹配 ^[A-Za-z_][A-Za-z0-9_]*$
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"非法{label}: {name!r}（仅允许字母/数字/下划线，且不以数字开头）")
    return name


def escape_literal(value: Any) -> str:
    """返回单引号包裹的 SQL 字符串字面量

    仅用于不能使用参数绑定的位置（如 read_parquet 文件路径）。
    常规值应使用 bind()。

    Args:
        value: 任意值（转为字符串）

    Returns:
        形如 "'normal'"；含单引号时形如 "'a''b'"
    """
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"
