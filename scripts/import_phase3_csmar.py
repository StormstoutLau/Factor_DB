"""
Phase 3: CSMAR 因子数据全量导入脚本

将 CSMAR 因子数据目录（默认 E:\\因子研究）下的所有 zip/xlsx 导入 factor_history，
并登记 factor_info。CSMAR 因子不写入 factor_wide：
    - 因子列数量大（数百到上千列）
    - 时间跨度覆盖 1990-2026，超出宽表 (2009-2026) 的键范围
    - 宽表用于高频截面查询，CSMAR 因子按需从 history 查询（zonemap 按 factor_name 剪枝）

用法:
    python scripts/import_phase3_csmar.py
    python scripts/import_phase3_csmar.py --category "Fama-French因子"
    python scripts/import_phase3_csmar.py --verify
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Factor_DB.core.connection import DuckDBConnection
from Factor_DB.core.schema import SchemaManager
from Factor_DB.loaders.base import LoaderConfig
from Factor_DB.loaders.csmar_loader import CSMARLoader

logger = logging.getLogger(__name__)

CSMAR_ROOT = Path(os.getenv('CSMAR_DATA_PATH', r'E:\因子研究'))
DEFAULT_TEMP_DIR = os.getenv('FACTOR_DB_TEMP_DIR', r'D:\factor_db_tmp')


def init_database(db_path: str) -> None:
    """确保目标表存在（不触碰 factor_data 视图）"""
    logger.info("初始化数据库（factor_history / factor_info）...")
    conn = DuckDBConnection(db_path)
    schema = SchemaManager(conn)
    schema.create_factor_history()
    schema.create_table('factor_info')
    conn.close()


def import_csmar(db_path: str, category: str = '',
                 data_path: str = '') -> int:
    """导入 CSMAR 因子数据（仅写 factor_history）

    Args:
        db_path: 数据库路径
        category: 分类子目录（空表示全部）
        data_path: 数据根目录（空表示 CSMAR_ROOT）

    Returns:
        导入总记录数
    """
    root = Path(data_path) if data_path else CSMAR_ROOT
    target = root / category if category else root

    logger.info("=" * 60)
    logger.info("Phase 3: CSMAR 因子数据导入（history-only）")
    logger.info(f"数据目录: {target}")
    logger.info("=" * 60)

    if not target.exists():
        logger.error(f"数据目录不存在: {target}")
        return 0

    temp_dir = Path(DEFAULT_TEMP_DIR)
    temp_dir.mkdir(parents=True, exist_ok=True)

    config = LoaderConfig(
        db_path=db_path,
        show_progress=True,
        batch_size=20000,
        temp_directory=str(temp_dir),
    )
    loader = CSMARLoader(config, history_only=True)
    try:
        total = loader.load_directory(target)
    finally:
        loader.conn.close()

    logger.info("=" * 60)
    logger.info(f"Phase 3 导入完成: {total:,} 条")
    logger.info("=" * 60)
    return total


def verify_import(db_path: str, sample: int = 5) -> None:
    """验证导入结果（不做全表 COUNT，避免扫描 17 亿行）"""
    conn = DuckDBConnection(db_path, read_only=True)
    try:
        with conn as c:
            log = c.execute('''
                SELECT COUNT(*), COALESCE(SUM(rows), 0),
                       MIN(imported_at), MAX(imported_at)
                FROM csmar_import_log
            ''').fetchone()
            logger.info("=" * 60)
            logger.info("CSMAR 导入日志:")
            logger.info(f"  已处理文件: {log[0]:,}")
            logger.info(f"  累计写入行数: {log[1]:,}")
            logger.info(f"  时间范围: {log[2]} ~ {log[3]}")

            rows = c.execute('''
                SELECT category, COUNT(*) AS factor_count
                FROM factor_info
                WHERE factor_type = 'csmar'
                GROUP BY category
                ORDER BY factor_count DESC
            ''').fetchall()
            logger.info("  因子分类分布:")
            for cat, cnt in rows:
                logger.info(f"    {cat or '(未分类)':<40} {cnt:>8,}")

            total_factors = c.execute(
                "SELECT COUNT(*) FROM factor_info WHERE factor_type = 'csmar'"
            ).fetchone()[0]
            logger.info(f"  CSMAR 因子总数: {total_factors:,}")

            samples = c.execute(f'''
                SELECT factor_name FROM factor_info
                WHERE factor_type = 'csmar'
                ORDER BY random() LIMIT {int(sample)}
            ''').fetchall()
            logger.info("  抽样校验:")
            for (name,) in samples:
                info = c.execute('''
                    SELECT COUNT(*), MIN(trade_date), MAX(trade_date),
                           COUNT(DISTINCT stock_code)
                    FROM factor_history
                    WHERE factor_name = $name
                ''', {'name': name}).fetchone()
                logger.info(
                    f"    {name:<50} {info[0]:>12,} 行  "
                    f"{info[1]} ~ {info[2]}  {info[3]:,} 实体"
                )

    except Exception as e:
        logger.error(f"验证失败: {e}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='Phase 3: CSMAR 因子数据导入（history-only）'
    )
    parser.add_argument('--db-path', default='factor_db.duckdb', help='数据库路径')
    parser.add_argument('--category', default='', help='只导入指定分类目录')
    parser.add_argument('--data-path', default='', help='CSMAR 数据根目录')
    parser.add_argument('--verify', action='store_true', help='仅验证已有导入')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )

    if args.verify:
        verify_import(args.db_path)
        return

    init_database(args.db_path)
    import_csmar(args.db_path, args.category, args.data_path)
    verify_import(args.db_path)


if __name__ == '__main__':
    main()
