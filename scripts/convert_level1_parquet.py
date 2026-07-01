"""
Level 1 Feather → Parquet 分区转换脚本

将 E:\Level 1 Data 下所有 Feather 文件转换为 Hive 分区 Parquet。
输出到 data/level1_parquet/ 目录。
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loaders.level1_loader import Level1ParquetConverter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

FEATHER_DIR = Path(r'E:\Level 1 Data')
OUTPUT_DIR = Path(r'F:\Coding\Factor_DB\data\level1_parquet')

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("Level 1 Feather → Parquet 分区转换")
    logger.info(f"源目录: {FEATHER_DIR}")
    logger.info(f"输出目录: {OUTPUT_DIR}")
    logger.info("=" * 60)

    converter = Level1ParquetConverter(OUTPUT_DIR)
    total = converter.convert_directory(FEATHER_DIR)

    logger.info("=" * 60)
    logger.info(f"转换完成: {total:,} 条记录")
    logger.info("=" * 60)