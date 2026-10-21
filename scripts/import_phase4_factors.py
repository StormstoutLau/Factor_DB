r"""
Import 124 NEW factors from Ashare_data (默认 E:\Ashare_data，可用 ASHARE_DATA_PATH 覆盖) to factor_data.
Skips 2 duplicates (ebit, current_ratio) already in DB.

Sources:
  - raw_fundamentals/      → 119 new factors (120 total, 1 dup: ebit)
  - construction_factors/  → 3 new factors (4 total, 1 dup: current_ratio)
  - price_volume_factors/  → 2 new factors
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.connection import DuckDBConnection
from loaders.base import LoaderConfig
from loaders.daily_loader import DailyLoader

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger('import_phase4')

DB_PATH = 'factor_db.duckdb'
ASHARE = Path(os.getenv('ASHARE_DATA_PATH', r'E:\Ashare_data'))

SKIP = {'ebit', 'current_ratio'}  # duplicates already in DB


def collect_files(base_dir: Path) -> list[tuple[str, Path]]:
    files = []
    for root, dirs, fnames in os.walk(str(base_dir)):
        for f in fnames:
            if f.endswith('.pkl'):
                factor_name = f.replace('.pkl', '')
                if factor_name in SKIP:
                    logger.debug(f"Skip duplicate: {factor_name}")
                    continue
                files.append((factor_name, Path(root) / f))
    return files


def main():
    parser = argparse.ArgumentParser(description='Phase 4: Import new factors from Ashare_data')
    parser.add_argument('--db-path', default=DB_PATH, help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='List files only, no import')
    args = parser.parse_args()

    # Collect all new factor files
    sources = {
        'raw_fundamentals': ASHARE / 'raw_fundamentals',
        'construction_factors': ASHARE / 'construction_factors',
        'price_volume_factors': ASHARE / 'price_volume_factors',
    }

    all_files: list[tuple[str, str, Path]] = []
    for source_name, source_dir in sources.items():
        files = collect_files(source_dir)
        for factor_name, file_path in files:
            all_files.append((source_name, factor_name, file_path))

    all_files.sort(key=lambda x: x[1])
    print(f"New factors to import: {len(all_files)}")
    for source, name, path in all_files:
        print(f"  [{source:25s}] {name:35s} → {path}")

    if args.dry_run:
        print("\nDry run - no data imported.")
        return

    # Initialize loader
    config = LoaderConfig(db_path=args.db_path, show_progress=True)
    loader = DailyLoader(config)

    start_time = time.time()
    total_count = 0
    success = 0
    failed = []

    for i, (source, factor_name, file_path) in enumerate(all_files):
        elapsed = time.time() - start_time
        print(f"\n[{i+1}/{len(all_files)}] [{source}] {factor_name}  (elapsed: {elapsed:.0f}s)")

        try:
            count = loader.load(file_path, field_name=factor_name)
            if count > 0:
                total_count += count
                success += 1
                print(f"  OK: {count:,} rows")
            else:
                print(f"  WARN: 0 rows (all NaN?)")
        except Exception as e:
            print(f"  FAIL: {e}")
            failed.append(factor_name)

    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"Import complete in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  Success: {success}/{len(all_files)} factors")
    print(f"  Total rows: {total_count:,}")
    if failed:
        print(f"  Failed: {failed}")

    loader.close()


if __name__ == '__main__':
    main()
