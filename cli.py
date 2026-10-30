"""
Factor_DB 命令行工具

提供数据库初始化、数据加载、查询等命令行操作。

Usage:
    factor-db init --db market.db
    factor-db stats --db market.db
    factor-db load daily --db market.db --path ./data/
    factor-db query --db market.db --sql "SELECT COUNT(*) FROM daily_prices"
    factor-db list-factors --db market.db
    factor-db cache-clear --db market.db
    factor-db compact --db market.db --before 2024-01-01
    factor-db ic --db market.db --factor PE --days 5
    factor-db ic-decay --db market.db --factor PE --days 1,2,3,5,10,20
    factor-db validate --db market.db --start 2024-01-01 --end 2024-03-31
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path


def cmd_init(args):
    """初始化数据库"""
    from Factor_DB.core.connection import DuckDBConnection
    from Factor_DB.core.schema import SchemaManager

    conn = DuckDBConnection(args.db)
    schema = SchemaManager(conn)
    if schema.init_database():
        print(f"数据库初始化成功: {args.db}")
    else:
        print("数据库初始化失败", file=sys.stderr)
        sys.exit(1)
    conn.close()


def cmd_stats(args):
    """显示数据库概览"""
    from Factor_DB.core.connection import DuckDBConnection

    conn = DuckDBConnection(args.db, read_only=True)
    with conn as c:
        tables = c.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchdf()

        for _, row in tables.iterrows():
            t = row['table_name']
            count = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t}: {count} rows")

    conn.close()


def cmd_load(args):
    """加载数据"""
    from Factor_DB.loaders.base import LoaderConfig
    from Factor_DB.loaders.daily_loader import DailyLoader

    config = LoaderConfig(db_path=args.db, show_progress=True)
    data_path = Path(args.path)

    if args.type == 'daily':
        with DailyLoader(config) as loader:
            total = loader.load(data_path)
            print(f"日K数据加载完成: {total} 行")
    elif args.type == 'level1':
        try:
            from Factor_DB.loaders.level1_loader import Level1Loader
            with Level1Loader(config) as loader:
                total = loader.load(data_path)
                print(f"Level1数据加载完成: {total} 行")
        except ImportError:
            print("pyarrow 未安装，无法加载 Level1 数据", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"不支持的数据类型: {args.type}", file=sys.stderr)
        sys.exit(1)


def cmd_query(args):
    """执行 SQL 查询"""
    from Factor_DB.core.connection import DuckDBConnection

    conn = DuckDBConnection(args.db, read_only=True)
    with conn as c:
        df = c.execute(args.sql).fetchdf()
        print(df.to_string(index=False))
    conn.close()


def cmd_list_factors(args):
    """列出所有因子"""
    from Factor_DB.core.connection import DuckDBConnection

    conn = DuckDBConnection(args.db, read_only=True)
    with conn as c:
        df = c.execute(
            "SELECT DISTINCT factor_name FROM factor_data ORDER BY factor_name"
        ).fetchdf()
        if df.empty:
            print("无因子数据")
        else:
            for _, row in df.iterrows():
                print(f"  {row['factor_name']}")
    conn.close()


def cmd_cache_clear(args):
    """清空缓存（无操作，本地缓存不持久化）"""
    print("缓存已清空（本地缓存由 Python 进程管理，重启后自动清除）")


def cmd_compact(args):
    """清理数据库旧版本数据"""
    from Factor_DB.core.connection import DuckDBConnection
    from Factor_DB.core.schema import SchemaManager

    conn = DuckDBConnection(args.db)
    schema = SchemaManager(conn)

    for table in ['daily_prices', 'factor_data']:
        deleted = schema.compact_table(table, args.before)
        if deleted > 0:
            print(f"  {table}: 旧版本数据已清理")

    conn.close()
    print(f"清理完成 (cutoff: {args.before})")


def _parse_date(value):
    """解析 YYYY-MM-DD 日期参数"""
    return datetime.strptime(value, '%Y-%m-%d').date()


def cmd_ic_decay(args):
    """计算 IC 衰减曲线"""
    from Factor_DB.query.factor_query import FactorQuery

    periods = tuple(int(p) for p in args.days.split(',') if p.strip())
    query = FactorQuery(args.db)
    df = query.get_ic_decay(
        args.factor, periods, start_date=args.start, end_date=args.end
    )
    print(df.to_string(index=False))
    query.conn.close()


def cmd_ic(args):
    """计算单期 IC 时间序列"""
    from Factor_DB.query.factor_query import FactorQuery

    query = FactorQuery(args.db)
    df = query.get_ic_series(
        args.factor, args.days, start_date=args.start, end_date=args.end
    )
    if df.empty:
        print(f'无 IC 数据: {args.factor}')
    else:
        ic = df['ic']
        ic_std = float(ic.std(ddof=1)) if len(ic) > 1 else float('nan')
        ic_mean = float(ic.mean())
        ic_ir = ic_mean / ic_std if ic_std else float('nan')
        print(
            f'IC 序列: {args.factor}, forward_return_days={args.days}, {len(df)} 期'
        )
        print(
            f'ic_mean={ic_mean:.6f}  ic_std={ic_std:.6f}  '
            f'ic_ir={ic_ir:.6f}  max|ic|={float(ic.abs().max()):.6f}'
        )
        rows = df if args.limit <= 0 else df.head(args.limit)
        print(rows.to_string(index=False))
    query.conn.close()


def cmd_validate(args):
    """生产库端到端验收（只读），委托 scripts/validate_production.py"""
    from Factor_DB.scripts import validate_production

    argv: list[str] = []
    if args.db:
        argv += ['--db', args.db]
    if args.start:
        argv += ['--start', args.start]
    if args.end:
        argv += ['--end', args.end]
    if args.memory_limit:
        argv += ['--memory-limit', args.memory_limit]
    if args.report_dir:
        argv += ['--report-dir', args.report_dir]
    if args.no_report:
        argv.append('--no-report')
    return validate_production.main(argv)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Factor_DB - 量化金融数据管理系统',
        prog='factor-db'
    )
    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # init
    p_init = subparsers.add_parser('init', help='初始化数据库')
    p_init.add_argument('--db', default='factor_db.duckdb', help='数据库路径')

    # stats
    p_stats = subparsers.add_parser('stats', help='显示数据库概览')
    p_stats.add_argument('--db', default='factor_db.duckdb', help='数据库路径')

    # load
    p_load = subparsers.add_parser('load', help='加载数据')
    p_load.add_argument('type', choices=['daily', 'level1'], help='数据类型')
    p_load.add_argument('--db', default='factor_db.duckdb', help='数据库路径')
    p_load.add_argument('--path', required=True, help='数据路径')

    # query
    p_query = subparsers.add_parser('query', help='执行 SQL 查询')
    p_query.add_argument('--db', default='factor_db.duckdb', help='数据库路径')
    p_query.add_argument('--sql', required=True, help='SQL 查询语句')

    # list-factors
    p_list = subparsers.add_parser('list-factors', help='列出所有因子')
    p_list.add_argument('--db', default='factor_db.duckdb', help='数据库路径')

    # cache-clear
    subparsers.add_parser('cache-clear', help='清空缓存')

    # ic-decay
    p_icd = subparsers.add_parser('ic-decay', help='计算 IC 衰减曲线')
    p_icd.add_argument('--db', default='factor_db.duckdb', help='数据库路径')
    p_icd.add_argument('--factor', required=True, help='因子名称')
    p_icd.add_argument(
        '--days', default='1,2,3,5,10,20', help='前向收益天数，逗号分隔'
    )
    p_icd.add_argument('--start', type=_parse_date, help='开始日期 YYYY-MM-DD')
    p_icd.add_argument('--end', type=_parse_date, help='结束日期 YYYY-MM-DD')

    # ic
    p_ic = subparsers.add_parser('ic', help='计算单期 IC 时间序列')
    p_ic.add_argument('--db', default='factor_db.duckdb', help='数据库路径')
    p_ic.add_argument('--factor', required=True, help='因子名称')
    p_ic.add_argument('--days', type=int, default=1, help='前向收益天数')
    p_ic.add_argument('--start', type=_parse_date, help='开始日期 YYYY-MM-DD')
    p_ic.add_argument('--end', type=_parse_date, help='结束日期 YYYY-MM-DD')
    p_ic.add_argument('--limit', type=int, default=10, help='展示行数，<=0 表示全部')

    # compact
    p_compact = subparsers.add_parser('compact', help='清理旧版本数据')
    p_compact.add_argument('--db', default='factor_db.duckdb', help='数据库路径')
    p_compact.add_argument(
        '--before', required=True,
        help='截止日期 (YYYY-MM-DD)，保留此日期之前每个主键的最新版本'
    )

    # validate
    p_validate = subparsers.add_parser('validate', help='生产库端到端验收（只读）')
    p_validate.add_argument('--db', help='数据库路径（默认与脚本一致）')
    p_validate.add_argument('--start', help='开始日期 YYYY-MM-DD')
    p_validate.add_argument('--end', help='结束日期 YYYY-MM-DD')
    p_validate.add_argument('--memory-limit', help='DuckDB memory_limit，如 8GB')
    p_validate.add_argument('--report-dir', help='报告输出目录')
    p_validate.add_argument('--no-report', action='store_true', help='只打印摘要，不写报告')

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    handlers = {
        'init': cmd_init,
        'stats': cmd_stats,
        'load': cmd_load,
        'query': cmd_query,
        'list-factors': cmd_list_factors,
        'cache-clear': cmd_cache_clear,
        'compact': cmd_compact,
        'ic-decay': cmd_ic_decay,
        'ic': cmd_ic,
        'validate': cmd_validate,
    }

    code = handlers[args.command](args)
    return code if isinstance(code, int) else 0


if __name__ == '__main__':
    sys.exit(main())