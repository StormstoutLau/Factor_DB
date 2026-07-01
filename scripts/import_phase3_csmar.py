"""
Phase 3: CSMAR 因子数据全量导入脚本

将 E:\因子研究 下的所有 CSMAR 因子数据导入到 factor_data 表。

用法:
    python scripts/import_phase3_csmar.py
    python scripts/import_phase3_csmar.py --db-path custom.duckdb
    python scripts/import_phase3_csmar.py --category "Fama-French因子"  # 只导入指定分类
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.connection import DuckDBConnection
from core.schema import SchemaManager
from loaders.base import LoaderConfig
from loaders.csmar_loader import CSMARLoader

logger = logging.getLogger(__name__)

CSMAR_ROOT = Path(r'E:\因子研究')


def init_database(db_path: str) -> bool:
    """初始化数据库"""
    logger.info("初始化数据库...")
    conn = DuckDBConnection(db_path)
    schema = SchemaManager(conn)
    # 确保 factor_data 和 factor_info 表存在
    schema.create_table('factor_data')
    schema.create_table('factor_info')
    for idx in ['idx_factor_name_date', 'idx_factor_code_date', 'idx_factor_pit']:
        schema.create_index(idx)
    conn.close()
    return True


def import_csmar_all(db_path: str, category_filter: str = '') -> int:
    """导入全部 CSMAR 因子数据

    Args:
        db_path: 数据库路径
        category_filter: 分类过滤（可选）

    Returns:
        导入总记录数
    """
    logger.info("=" * 60)
    logger.info("Phase 3: CSMAR 因子数据全量导入")
    logger.info(f"数据源: {CSMAR_ROOT}")
    logger.info("=" * 60)

    if not CSMAR_ROOT.exists():
        logger.error(f"CSMAR 数据目录不存在: {CSMAR_ROOT}")
        return 0

    config = LoaderConfig(
        db_path=db_path,
        show_progress=True,
        batch_size=20000,
    )

    loader = CSMARLoader(config)

    if category_filter:
        target_dir = CSMAR_ROOT / category_filter
        if target_dir.exists():
            total = loader.load_directory(target_dir)
        else:
            logger.error(f"分类目录不存在: {target_dir}")
            return 0
    else:
        total = loader.load_directory(CSMAR_ROOT)

    loader.conn.close()
    return total


def verify_import(db_path: str):
    """验证导入结果"""
    conn = DuckDBConnection(db_path, read_only=True)
    try:
        with conn as c:
            # 因子统计
            result = c.execute('''
                SELECT 
                    COUNT(*) as total_rows,
                    COUNT(DISTINCT factor_name) as factor_count,
                    COUNT(DISTINCT stock_code) as stock_count,
                    MIN(trade_date) as first_date,
                    MAX(trade_date) as last_date
                FROM factor_data
            ''').fetchone()

            logger.info("=" * 60)
            logger.info("CSMAR 因子数据验证:")
            logger.info(f"  总记录数: {result[0]:,}")
            logger.info(f"  因子数量: {result[1]}")
            logger.info(f"  股票数量: {result[2]}")
            logger.info(f"  日期范围: {result[3]} ~ {result[4]}")

            # 按因子名统计（前20）
            factors = c.execute('''
                SELECT factor_name, COUNT(*) as cnt
                FROM factor_data
                GROUP BY factor_name
                ORDER BY cnt DESC
                LIMIT 20
            ''').fetchall()

            logger.info("  前20个因子:")
            for row in factors:
                logger.info(f"    {row[0]:<40} {row[1]:>12,} 条")

    except Exception as e:
        logger.error(f"验证失败: {e}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Phase 3: CSMAR 因子数据全量导入')
    parser.add_argument('--db-path', default='factor_db.duckdb', help='数据库路径')
    parser.add_argument('--category', default='', help='只导入指定分类目录')
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
    total = import_csmar_all(args.db_path, args.category)

    if total > 0:
        logger.info("=" * 60)
        logger.info(f"Phase 3 导入完成: {total:,} 条总记录")
        verify_import(args.db_path)


if __name__ == '__main__':
    main()