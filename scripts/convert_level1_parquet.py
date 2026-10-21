r"""
Level 1 Feather → Parquet 分区转换脚本

将 Level 1 Data 目录下所有 Feather 文件转换为 Hive 分区 Parquet。
默认源目录 E:\Level 1 Data（可用 LEVEL1_DATA_PATH 覆盖），
默认输出到项目内 data/level1_parquet/（可用 LEVEL1_PARQUET_DIR 覆盖）。
"""

import os
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FEATHER_DIR = Path(os.getenv('LEVEL1_DATA_PATH', r'E:\Level 1 Data'))
OUTPUT_DIR = Path(os.getenv(
    'LEVEL1_PARQUET_DIR', str(PROJECT_ROOT / 'data' / 'level1_parquet')
))

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