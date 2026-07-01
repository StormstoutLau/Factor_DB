"""
Phase 2: Level 1 Data 全量导入脚本

将 E:\Level 1 Data 下的所有 Feather 文件导入到 level1_snapshots 表。

用法:
    python scripts/import_phase2_level1.py
    python scripts/import_phase2_level1.py --db-path custom.duckdb
    python scripts/import_phase2_level1.py --start 2020-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from loaders.base import LoaderConfig, BaseLoader
from loaders.level1_loader import Level1Loader

logger = logging.getLogger(__name__)

LEVEL1_ROOT = Path(r'E:\Level 1 Data')


def init_database(db_path: str) -> bool:
    """初始化数据库（确保 level1_snapshots 表存在）"""
    logger.info("初始化数据库...")
    conn = DuckDBConnection(db_path)
    schema = SchemaManager(conn)
    result = schema.create_table('level1_snapshots')
    # 创建索引
    for idx in ['idx_level1_date_code', 'idx_level1_time', 'idx_level1_code_time']:
        schema.create_index(idx)
    conn.close()
    return result


def import_level1(db_path: str, start_date: str = None, end_date: str = None) -> int:
    """导入 Level 1 数据

    Args:
        db_path: 数据库路径
        start_date: 起始日期 (YYYY-MM-DD)，可选
        end_date: 结束日期 (YYYY-MM-DD)，可选

    Returns:
        导入总记录数
    """
    logger.info("=" * 50)
    logger.info("Phase 2: Level 1 Data 全量导入")
    logger.info(f"数据源: {LEVEL1_ROOT}")
    logger.info("=" * 50)

    if not LEVEL1_ROOT.exists():
        logger.error(f"Level 1 数据目录不存在: {LEVEL1_ROOT}")
        return 0

    # 收集文件列表
    all_files = sorted(LEVEL1_ROOT.glob('*.feather'))
    logger.info(f"发现 {len(all_files)} 个 Feather 文件")

    # 按日期范围过滤
    if start_date:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        all_files = [f for f in all_files if datetime.strptime(f.stem, '%Y-%m-%d') >= start_dt]
    if end_date:
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        all_files = [f for f in all_files if datetime.strptime(f.stem, '%Y-%m-%d') <= end_dt]

    if not all_files:
        logger.warning("没有匹配的文件")
        return 0

    logger.info(f"将导入 {len(all_files)} 个文件")
    logger.info(f"日期范围: {all_files[0].stem} ~ {all_files[-1].stem}")

    # 使用 Level1Loader 批量导入
    config = LoaderConfig(
        db_path=db_path,
        show_progress=True,
        skip_existing=True,
        batch_size=10000,
    )
    loader = Level1Loader(config)

    total_count = 0
    for file_path in all_files:
        count = loader._load_single_file(file_path)
        total_count += count

    loader.conn.close()
    logger.info(f"Level 1 数据导入完成: {total_count:,} 条总记录")
    return total_count


def verify_import(db_path: str):
    """验证导入结果"""
    conn = DuckDBConnection(db_path, read_only=True)
    try:
        with conn as c:
            # 统计信息
            result = c.execute('''
                SELECT 
                    COUNT(*) as total_rows,
                    COUNT(DISTINCT trade_date) as trading_days,
                    COUNT(DISTINCT stock_code) as stocks,
                    MIN(trade_date) as first_date,
                    MAX(trade_date) as last_date
                FROM level1_snapshots
            ''').fetchone()

            logger.info("=" * 50)
            logger.info("Level 1 数据验证:")
            logger.info(f"  总记录数: {result[0]:,}")
            logger.info(f"  交易日数: {result[1]}")
            logger.info(f"  股票数量: {result[2]}")
            logger.info(f"  日期范围: {result[3]} ~ {result[4]}")

            # 按年统计
            yearly = c.execute('''
                SELECT 
                    EXTRACT(YEAR FROM trade_date) as year,
                    COUNT(*) as records,
                    COUNT(DISTINCT trade_date) as days,
                    COUNT(DISTINCT stock_code) as stocks
                FROM level1_snapshots
                GROUP BY year
                ORDER BY year
            ''').fetchall()

            logger.info("  按年统计:")
            for row in yearly:
                logger.info(f"    {row[0]}: {row[1]:>12,} 条, {row[2]:>4} 天, {row[3]:>5} 只股票")

    except Exception as e:
        logger.error(f"验证失败: {e}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Phase 2: Level 1 Data 全量导入')
    parser.add_argument('--db-path', default='factor_db.duckdb', help='数据库路径')
    parser.add_argument('--start', help='起始日期 (YYYY-MM-DD)')
    parser.add_argument('--end', help='结束日期 (YYYY-MM-DD)')
    parser.add_argument('--verify', action='store_true', help='仅验证已有数据')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    if args.verify:
        verify_import(args.db_path)
        return

    # 初始化数据库
    init_database(args.db_path)

    # 导入数据
    total = import_level1(args.db_path, args.start, args.end)

    if total > 0:
        # 验证结果
        verify_import(args.db_path)


if __name__ == '__main__':
    main()