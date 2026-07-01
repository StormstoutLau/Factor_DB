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
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path


def cmd_init(args):
    """初始化数据库"""
    from core.connection import DuckDBConnection
    from core.schema import SchemaManager

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
    from core.connection import DuckDBConnection

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
    from loaders.base import LoaderConfig
    from loaders.daily_loader import DailyLoader

    config = LoaderConfig(db_path=args.db, show_progress=True)
    data_path = Path(args.path)

    if args.type == 'daily':
        with DailyLoader(config) as loader:
            total = loader.load(data_path)
            print(f"日K数据加载完成: {total} 行")
    elif args.type == 'level1':
        try:
            from loaders.level1_loader import Level1Loader
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
    from core.connection import DuckDBConnection

    conn = DuckDBConnection(args.db, read_only=True)
    with conn as c:
        df = c.fetchdf(args.sql)
        print(df.to_string(index=False))
    conn.close()


def cmd_list_factors(args):
    """列出所有因子"""
    from core.connection import DuckDBConnection

    conn = DuckDBConnection(args.db, read_only=True)
    with conn as c:
        df = c.fetchdf(
            "SELECT DISTINCT factor_name FROM factor_data ORDER BY factor_name"
        )
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
    from core.connection import DuckDBConnection
    from core.schema import SchemaManager

    conn = DuckDBConnection(args.db)
    schema = SchemaManager(conn)

    for table in ['daily_prices', 'factor_data']:
        deleted = schema.compact_table(table, args.before)
        if deleted > 0:
            print(f"  {table}: 旧版本数据已清理")

    conn.close()
    print(f"清理完成 (cutoff: {args.before})")


def main():
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
    p_cache = subparsers.add_parser('cache-clear', help='清空缓存')

    # compact
    p_compact = subparsers.add_parser('compact', help='清理旧版本数据')
    p_compact.add_argument('--db', default='factor_db.duckdb', help='数据库路径')
    p_compact.add_argument(
        '--before', required=True,
        help='截止日期 (YYYY-MM-DD)，保留此日期之前每个主键的最新版本'
    )

    args = parser.parse_args()

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
    }

    handlers[args.command](args)


if __name__ == '__main__':
    main()