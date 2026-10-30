"""生产库端到端验收脚本（只读）

在真实生产库上执行 S1–S5 检查，产出 JSON + Markdown 报告。
设计见 docs/design/prod_e2e_validation_design.md。

用法：
    py -3 scripts/validate_production.py
    py -3 scripts/validate_production.py --start 2024-01-01 --end 2024-03-31
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

import duckdb

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.query.factor_query import FactorQuery
from Factor_DB.query.price_query import PriceQuery
from Factor_DB.utils.sql_utils import bind, quote_identifier

DEFAULT_DB = Path(__file__).resolve().parent.parent / 'factor_db.duckdb'
DEFAULT_START = date(2024, 1, 1)
DEFAULT_END = date(2024, 3, 31)
SLOW_QUERY_S = 30.0
MIN_NON_NULL_RATIO = 0.50

# 生产库基线（2026-10-26 核对值），偏差 >1% 仅告警
BASELINE_ROWS = {
    'factor_wide': 9_307_121,
    'factor_history': 5_203_062_078,
    'daily_prices': 43_226_352,
}
_MEMORY_LIMIT_RE = re.compile(r'^\d+(\.\d+)?(GB|MB|GIB|MIB|%)$', re.IGNORECASE)


# ========== 工具 ==========


def _run_check(check_id: str, name: str, fn: Callable[[], tuple[dict, list[str], str]]) -> dict:
    """执行单个检查，异常统一记为 fail"""
    t0 = time.perf_counter()
    try:
        metrics, warnings, detail = fn()
        status = 'warn' if warnings else 'pass'
        if warnings:
            detail = f"{detail}；告警：{'；'.join(warnings)}".strip('；')
    except Exception as e:  # noqa: BLE001 - 报告必须完整
        status = 'fail'
        metrics = {}
        detail = f'{type(e).__name__}: {e}'
    elapsed = time.perf_counter() - t0
    if status != 'fail' and elapsed > SLOW_QUERY_S:
        status = 'warn'
        detail = f'{detail}；耗时 {elapsed:.1f}s > {SLOW_QUERY_S}s'
    return {
        'id': check_id,
        'name': name,
        'status': status,
        'elapsed_s': round(elapsed, 3),
        'detail': detail,
        'metrics': metrics,
    }


def _table_types(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = 'main'"
    ).fetchall()
    return {name: typ for name, typ in rows}


def _wide_columns(conn) -> list[str]:
    cols = conn.execute('DESCRIBE factor_wide').fetchdf()
    keys = {'trade_date', 'stock_code', 'loaded_at'}
    return [c for c in cols['column_name'].tolist() if c not in keys]


def _pick_wide_factors(conn, start: date, end: date, limit: int = 8) -> list[str]:
    """在窗口内按非空覆盖从宽表中挑因子"""
    picked: list[str] = []
    for col in _wide_columns(conn)[:40]:
        params: dict[str, Any] = {}
        s = bind(params, 'start_date', start)
        e = bind(params, 'end_date', end)
        cnt = conn.execute(
            f'SELECT COUNT(*) FROM factor_wide '
            f'WHERE trade_date >= {s} AND trade_date <= {e} '
            f'AND {quote_identifier(col)} IS NOT NULL',
            params,
        ).fetchone()[0]
        if cnt > 0:
            picked.append(col)
            if len(picked) >= limit:
                break
    if not picked:
        raise RuntimeError(f'窗口 {start}~{end} 内无任何非空宽表因子')
    return picked


def _pick_history_factor(conn, wide_cols: list[str], start: date, end: date) -> Optional[str]:
    """从 factor_info 中挑一个仅存 factor_history 的因子"""
    names = conn.execute('SELECT factor_name FROM factor_info').fetchdf()['factor_name'].tolist()
    candidates = [n for n in names if n not in wide_cols][:3]
    for name in candidates:
        params: dict[str, Any] = {}
        fn = bind(params, 'factor_name', name)
        s = bind(params, 'start_date', start)
        e = bind(params, 'end_date', end)
        cnt = conn.execute(
            f'SELECT COUNT(*) FROM factor_history WHERE factor_name = {fn} '
            f'AND trade_date >= {s} AND trade_date <= {e}',
            params,
        ).fetchone()[0]
        if cnt > 0:
            return name
    return None


def _pick_stock_codes(conn, end: date, limit: int = 8) -> list[str]:
    params: dict[str, Any] = {}
    e = bind(params, 'end_date', end)
    codes = conn.execute(
        f'''SELECT DISTINCT stock_code FROM daily_prices
            WHERE trade_date = COALESCE(
                (SELECT MAX(trade_date) FROM daily_prices WHERE trade_date <= {e}),
                (SELECT MIN(trade_date) FROM daily_prices)
            )
            ORDER BY stock_code LIMIT {int(limit)}''',
        params,
    ).fetchdf()['stock_code'].tolist()
    if not codes:
        raise RuntimeError('daily_prices 中无可用股票代码')
    return codes


# ========== 主流程 ==========


def run_validation(
    db_path: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
    memory_limit: str = '8GB',
) -> dict:
    """执行 S1–S5 检查并返回报告 dict（不写文件、不退出进程）"""
    if not _MEMORY_LIMIT_RE.match(memory_limit):
        raise ValueError(f'非法 memory_limit: {memory_limit}')

    start = start or DEFAULT_START
    end = end or DEFAULT_END
    if start > end:
        raise ValueError('start 必须早于或等于 end')

    db_path = str(db_path)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f'数据库不存在: {db_path}')

    generated_at = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    conn = DuckDBConnection(db_path, read_only=True)
    conn.connect()
    conn.execute(f"SET memory_limit = '{memory_limit}'")

    fq = FactorQuery(db_path)
    pq = PriceQuery(db_path)
    ctx: dict[str, Any] = {}

    try:
        def _wide_factors() -> list[str]:
            if 'wide_factors' not in ctx:
                ctx['wide_factors'] = _pick_wide_factors(conn, start, end)
            return ctx['wide_factors']

        # ---- S1 结构与行数 ----
        def s1():
            types = _table_types(conn)
            problems = []
            if types.get('factor_data') != 'VIEW':
                problems.append(f"factor_data 应为 VIEW，实际 {types.get('factor_data')}")
            for t in ('factor_wide', 'factor_history', 'daily_prices'):
                if types.get(t) != 'BASE TABLE':
                    problems.append(f'{t} 应为 BASE TABLE，实际 {types.get(t)}')
            if problems:
                raise AssertionError('；'.join(problems))

            counts = {t: conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                      for t in BASELINE_ROWS}
            warnings = []
            for t, baseline in BASELINE_ROWS.items():
                actual = counts[t]
                if actual == 0 or abs(actual - baseline) / baseline > 0.01:
                    warnings.append(f'{t} 行数 {actual:,} 偏离基线 {baseline:,}')
            return counts, warnings, f'{len(types)} 个对象，行数 {counts}'

        # ---- S2 宽表矩阵 ----
        def s2():
            factors = _wide_factors()
            df = fq.get_factor_matrix(factors, start_date=start, end_date=end)
            if df.empty:
                raise AssertionError(f'宽表矩阵为空: {factors}')
            ratio = float(df[factors].notna().to_numpy().mean())
            warnings = []
            if ratio < MIN_NON_NULL_RATIO:
                warnings.append(f'非空率 {ratio:.1%} < {MIN_NON_NULL_RATIO:.0%}')
            metrics = {'factors': factors, 'rows': len(df), 'non_null_ratio': round(ratio, 4)}
            return metrics, warnings, f'{len(factors)} 因子 × {len(df)} 行，非空率 {ratio:.1%}'

        # ---- S3 history + PIT ----
        def s3():
            wide_cols = _wide_columns(conn)
            hist_factor = _pick_history_factor(conn, wide_cols, start, end)
            if hist_factor is None:
                raise RuntimeError('factor_info 中未找到窗口内有数据的 history-only 因子')

            params: dict[str, Any] = {}
            fn = bind(params, 'factor_name', hist_factor)
            s = bind(params, 'start_date', start)
            e = bind(params, 'end_date', end)
            hist_rows = conn.execute(
                f'SELECT COUNT(*) FROM factor_history WHERE factor_name = {fn} '
                f'AND trade_date >= {s} AND trade_date <= {e}', params
            ).fetchone()[0]

            wide_factor = _wide_factors()[0]
            latest = fq.get_factor(wide_factor, start_date=start, end_date=end)
            pit = fq.get_factor(
                wide_factor, start_date=start, end_date=end, as_of=date(2099, 12, 31)
            )
            if latest.empty:
                raise AssertionError(f'宽表因子 {wide_factor} 无数据')
            if len(latest) != len(pit):
                raise AssertionError(
                    f'PIT 行数不一致: latest={len(latest)} vs as_of={len(pit)}'
                )
            metrics = {
                'history_factor': hist_factor, 'history_rows': int(hist_rows),
                'wide_factor': wide_factor, 'pit_rows': len(pit),
            }
            return metrics, [], f'{hist_factor} {hist_rows} 行；PIT {len(pit)} 行一致'

        # ---- S4 价格 ----
        def s4():
            codes = _pick_stock_codes(conn, end)
            daily = pq.get_daily(codes, start_date=start, end_date=end)
            matrix = pq.get_price_matrix(
                field='close', stock_codes=codes[:2], start_date=start, end_date=end
            )
            if daily.empty or matrix.empty:
                raise AssertionError(f'价格查询为空: daily={len(daily)} matrix={matrix.shape}')
            metrics = {'stocks': codes, 'daily_rows': len(daily), 'matrix_shape': list(matrix.shape)}
            return metrics, [], f'get_daily {len(daily)} 行；get_price_matrix {matrix.shape}'

        # ---- S5 IC ----
        def s5():
            dmin, dmax = conn.execute(
                'SELECT MIN(trade_date), MAX(trade_date) FROM daily_prices'
            ).fetchone()
            if dmin is None:
                raise RuntimeError('daily_prices 为空，无法计算 IC')
            ic_start = max(start, dmin)
            ic_end = min(end, dmax)
            if ic_start > ic_end:
                return (
                    {'skipped': True},
                    [f'窗口 {start}~{end} 与 daily_prices 覆盖 {dmin}~{dmax} 无交集'],
                    'IC 窗口无交集，已跳过',
                )

            factor = _wide_factors()[0]
            df = fq.get_ic_series(factor, 1, ic_start, ic_end)
            if df.empty:
                raise AssertionError(f'{factor} 在 {ic_start}~{ic_end} 无可计算 IC 期')
            ic = df['ic']
            if ic.isna().any():
                raise AssertionError('IC 序列存在 NaN')
            if float(ic.abs().max()) > 1.0 + 1e-9:
                raise AssertionError(f'IC 超出 [-1, 1]: max|ic|={float(ic.abs().max()):.4f}')
            warnings = []
            if len(df) < 5:
                warnings.append(f'仅 {len(df)} 期（低频因子正常，仅供参考）')
            metrics = {
                'factor': factor, 'periods': len(df),
                'ic_mean': round(float(ic.mean()), 6),
                'ic_abs_max': round(float(ic.abs().max()), 6),
                'window': [str(ic_start), str(ic_end)],
            }
            return metrics, warnings, f'{factor} {len(df)} 期，IC 均值 {float(ic.mean()):.6f}'

        checks = [
            _run_check('S1', '结构与行数', s1),
            _run_check('S2', '宽表矩阵', s2),
            _run_check('S3', 'history + PIT', s3),
            _run_check('S4', '价格查询', s4),
            _run_check('S5', 'IC 序列', s5),
        ]
    finally:
        conn.close()
        DuckDBConnection._instances.clear()

    real_path = os.path.realpath(db_path)
    report = {
        'meta': {
            'db_path': db_path,
            'real_path': real_path,
            'db_size_gb': round(os.path.getsize(real_path) / (1024 ** 3), 2),
            'generated_at': generated_at,
            'python': sys.version.split()[0],
            'duckdb': duckdb.__version__,
            'window': {'start': str(start), 'end': str(end)},
            'memory_limit': memory_limit,
        },
        'checks': checks,
        'summary': {
            'pass': sum(c['status'] == 'pass' for c in checks),
            'warn': sum(c['status'] == 'warn' for c in checks),
            'fail': sum(c['status'] == 'fail' for c in checks),
            'total_elapsed_s': round(sum(c['elapsed_s'] for c in checks), 3),
        },
    }
    return report


def conclusion(report: dict) -> str:
    """报告结论行"""
    if report['summary']['fail']:
        return 'E2E_VALIDATION=FAILED'
    if report['summary']['warn']:
        return 'E2E_VALIDATION=DEGRADED'
    return 'E2E_VALIDATION=OK'


def render_markdown(report: dict) -> str:
    """报告 dict → Markdown"""
    meta = report['meta']
    lines = [
        '# 生产库端到端验收报告',
        '',
        f"> 生成时间：{meta['generated_at']} · 窗口：{meta['window']['start']} ~ {meta['window']['end']}",
        f"> 数据库：`{meta['real_path']}`（{meta['db_size_gb']} GB）",
        f"> 环境：Python {meta['python']} / DuckDB {meta['duckdb']} / memory_limit={meta['memory_limit']}",
        '',
        '## 摘要',
        '',
        '| 通过 | 告警 | 失败 | 总耗时 |',
        '|------|------|------|--------|',
        f"| {report['summary']['pass']} | {report['summary']['warn']} | "
        f"{report['summary']['fail']} | {report['summary']['total_elapsed_s']} s |",
        '',
        '## 检查明细',
        '',
        '| 步骤 | 名称 | 状态 | 耗时(s) | 指标 | 说明 |',
        '|------|------|------|---------|------|------|',
    ]
    for c in report['checks']:
        metrics = json.dumps(c['metrics'], ensure_ascii=False, default=str)
        lines.append(
            f"| {c['id']} | {c['name']} | {c['status']} | {c['elapsed_s']} | "
            f"`{metrics}` | {c['detail']} |"
        )
    lines += ['', '## 结论', '', f'`{conclusion(report)}`', '']
    return '\n'.join(lines)


def _write_reports(report: dict, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f'prod_e2e_validation_{report["meta"]["generated_at"][:10]}'
    json_path = report_dir / f'{stem}.json'
    md_path = report_dir / f'{stem}.md'
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                         encoding='utf-8')
    md_path.write_text(render_markdown(report), encoding='utf-8')
    return json_path, md_path


def _parse_date(value: str) -> date:
    return datetime.strptime(value, '%Y-%m-%d').date()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='生产库端到端验收（只读）')
    parser.add_argument('--db', default=str(DEFAULT_DB), help='数据库路径')
    parser.add_argument('--start', help='开始日期 YYYY-MM-DD（默认 2024-01-01）')
    parser.add_argument('--end', help='结束日期 YYYY-MM-DD（默认 2024-03-31）')
    parser.add_argument('--memory-limit', default='8GB', help='DuckDB memory_limit')
    parser.add_argument('--report-dir', default=str(Path(__file__).resolve().parent.parent / 'docs' / 'reports'))
    parser.add_argument('--no-report', action='store_true', help='只打印摘要，不写报告文件')
    args = parser.parse_args(argv)

    try:
        report = run_validation(
            args.db,
            start=_parse_date(args.start) if args.start else None,
            end=_parse_date(args.end) if args.end else None,
            memory_limit=args.memory_limit,
        )
    except (FileNotFoundError, ValueError, duckdb.Error) as e:
        print(f'环境错误：{e}', file=sys.stderr)
        return 2

    for c in report['checks']:
        print(f"[{c['status'].upper():4}] {c['id']} {c['name']} {c['elapsed_s']}s — {c['detail']}")
    print(conclusion(report))

    if not args.no_report:
        json_path, md_path = _write_reports(report, Path(args.report_dir))
        print(f'报告：{json_path}\n      {md_path}')

    return 0 if report['summary']['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
